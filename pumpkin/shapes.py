"""3D models: Pumpkin runs trellis.cpp (Microsoft's TRELLIS.2 in C++/ggml) to turn a picture into a textured 3D
model, a .glb file any 3D program, game engine or web page opens. With five more files the same engine runs Pixal3D,
a TRELLIS.2 fine-tune whose shape follows the picture much more closely.

- The engine (trellis-cli) is downloaded from trellis.cpp's GitHub releases, the Vulkan build (it runs on AMD, NVIDIA
  and Intel cards, and on the processor without one), checked against the checksums GitHub publishes.
- The model is ten GGUF files (about 16 GB) from Hugging Face, kept in ~/Pumpkin/image-models/trellis2.
- Each 3D model is one run of trellis-cli: it loads one stage at a time and frees it, so nothing stays in memory
  between models, and stopping a run frees everything at once. A text request is drawn first with the image model.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
from http.client import HTTPException

from . import config, hardware, images, llama

WINDOWS = os.name == "nt"
CLI = "trellis-cli.exe" if WINDOWS else "trellis-cli"
REPO = "pwilkin/trellis.cpp"
HF_REPO = "ilintar/trellis2-gguf"
FILES = ["birefnet.gguf", "dinov3.gguf", "ss_flow.gguf", "ss_dec.gguf", "shape_flow_512.gguf", "shape_flow_1024.gguf",
         "shape_dec.gguf", "tex_flow_512.gguf", "tex_flow_1024.gguf", "tex_dec.gguf"]
ENGINE_DIR = os.path.join(config.DATA_DIR, "trellis")
LOG_PATH = os.path.join(config.STATE_DIR, "trellis.log")
FOLDER = "trellis2"
PIXAL_REPO = "vegax87/Pixal3D"         # the Pixal3D-specific files, named as trellis.cpp expects them
PIXAL_FILES = ["pixal3d_ss_flow.gguf", "pixal3d_shape_flow_512.gguf", "pixal3d_shape_flow_1024.gguf",
               "pixal3d_tex_flow_1024.gguf", "pixal3d_naf.gguf"]
SIZE_GB = 16.5                         # the ten files, until Hugging Face says exactly
PIXAL_GB = 10.5
# each quality: geometry resolution, and what else goes to trellis-cli. The engine's own default bakes the colours of
# a 1024 model from a 512 volume, which is why they looked soft: detailed and best ask for the full 1024 volume.
QUALITY = {"fast": 512, "detailed": 1024, "best": 1536}
EXTRA = {"fast": [], "detailed": ["--tex-res", "1024"], "best": ["--tex-res", "1024", "--atlas", "4096"]}
NEEDS_GB = {512: 7.0, 1024: 13.0, 1536: 15.5}   # graphics memory a run takes at its peak, about
TIMEOUT = 3 * 3600


# ---------------------------------------------------------------- the engine
def supported():
    """trellis.cpp has builds for Linux and Windows on x64."""
    if hardware.ANDROID or hardware.MAC:
        return False
    return llama.machine_arch() == "x64"


def asset_name(backend="vulkan", system=None):
    system = system or sys.platform
    return f"trellis-{backend}-windows-x64.zip" if system == "win32" else f"trellis-{backend}-linux-x64.tar.gz"


def release_builds(releases, backend="vulkan", system=None):
    """Every release with a build for this computer, newest first: [(release, asset)]."""
    want = asset_name(backend, system)
    out = []
    for rel in releases:
        if rel.get("draft"):
            continue
        a = next((a for a in rel.get("assets") or [] if a.get("name") == want), None)
        if a:
            out.append((rel, a))
    return out


def _installed_meta():
    try:
        with open(os.path.join(ENGINE_DIR, "installed.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def find_engine(settings=None):
    """trellis-cli: the one set in Settings, or the one Pumpkin downloaded."""
    try:
        own = str(settings["trellis_cli"] or "").strip() if settings is not None else ""
    except KeyError:
        own = ""
    if own:
        own = os.path.expanduser(own)
        return own if os.path.isfile(own) else None
    p = _installed_meta().get("path")
    return p if p and os.path.isfile(p) else None


def engine_info(settings=None):
    meta = _installed_meta()
    path = find_engine(settings)
    return {"installed": bool(path), "path": path, "tag": meta.get("tag") if path == meta.get("path") else None,
            "backend": meta.get("backend")}


def _github(path):
    import urllib.request
    headers = {"User-Agent": llama.USER_AGENT, "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    req = urllib.request.Request(f"{llama.GITHUB_API}/repos/{REPO}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def download_engine(progress=None, cancelled=lambda: False, backend="vulkan"):
    """Downloads trellis-cli and returns its path. A build made on a newer Linux than this one is replaced by the
    release before it, like the image engine."""
    try:
        releases = _github("/releases?per_page=20")
    except (urllib.error.URLError, OSError, ValueError, HTTPException) as e:
        raise RuntimeError(f"Could not reach GitHub to find trellis.cpp ({e}). Try again in a minute.")
    builds = release_builds(releases, backend)
    if not builds:
        raise RuntimeError("No trellis.cpp build for this computer was found in its recent releases.")
    skipped = 0
    for rel, asset in builds[:6]:
        path = _install(rel, asset, backend, progress, cancelled)
        if not WINDOWS and images._needs_newer_linux(path):
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)
            skipped += 1
            continue
        with open(os.path.join(ENGINE_DIR, "installed.json"), "w", encoding="utf-8") as f:
            json.dump({"path": path, "backend": backend, "tag": rel.get("tag_name")}, f)
        return path
    raise RuntimeError("trellis.cpp's recent builds need a newer system library (glibc) than this computer has. "
                       "Update the system to make 3D models here." if skipped else "The 3D engine did not install.")


def _install(rel, asset, backend, progress, cancelled):
    tag = rel.get("tag_name") or "latest"
    os.makedirs(ENGINE_DIR, exist_ok=True)
    target = os.path.join(ENGINE_DIR, f"{config.slug(tag)}-{backend}")
    shutil.rmtree(target, ignore_errors=True)
    tmp = os.path.join(ENGINE_DIR, asset["name"] + ".part")
    llama._fetch(asset["browser_download_url"], tmp, progress, cancelled)
    llama.verify(tmp, asset.get("digest") or "")
    llama._extract(tmp, target, asset["name"])
    os.remove(tmp)
    path = None
    for dirpath, _, files in os.walk(target):
        if CLI in files:
            path = os.path.join(dirpath, CLI)
            break
    if not path:
        raise RuntimeError(f"The trellis.cpp download has no {CLI}.")
    if not WINDOWS:
        for dirpath, _, files in os.walk(target):
            for f in files:
                p = os.path.join(dirpath, f)
                os.chmod(p, os.stat(p).st_mode | 0o755)
    return path


# ---------------------------------------------------------------- the model files
def models_dir(settings):
    return os.path.join(images.models_dir(settings), FOLDER)


def installed(settings):
    """{ready, partial, missing, folder, size_gb, pixal3d: {ready, partial, missing}} for the model files."""
    folder = models_dir(settings)

    def there(f):
        p = os.path.join(folder, f)
        return os.path.isfile(p) and os.path.getsize(p) > 0
    have = [f for f in FILES if there(f)]
    pix = [f for f in PIXAL_FILES if there(f)]
    size = sum(os.path.getsize(os.path.join(folder, f)) for f in have + pix) / images.GiB
    return {"ready": len(have) == len(FILES), "partial": 0 < len(have) < len(FILES),
            "missing": [f for f in FILES if f not in have], "folder": folder, "size_gb": round(size, 1),
            "pixal3d": {"ready": len(have) == len(FILES) and len(pix) == len(PIXAL_FILES),
                        "partial": 0 < len(pix) < len(PIXAL_FILES), "missing": [f for f in PIXAL_FILES if f not in pix]}}


def total_size(token=None):
    """The ten files' size in GB, from Hugging Face; None offline."""
    try:
        tree = images.hf_tree(HF_REPO, token)
    except Exception:  # noqa: BLE001 - offline: no size yet
        return None
    sizes = {t.get("path"): images._size(t) for t in tree if isinstance(t, dict)}
    if not all(f in sizes for f in FILES):
        return None
    return round(sum(sizes[f] for f in FILES) / images.GiB, 1)


def _vram(hw):
    g = (hw or {}).get("main_gpu") or {}
    if g.get("unified"):
        return (hw or {}).get("ram_total", 0) * 0.6
    return 0 if g.get("too_old") else (g.get("vram_total") or 0)


def default_quality(hw):
    """Detailed needs about a 16 GB card and Best more; smaller cards, and computers without one, get Fast."""
    vram = _vram(hw)
    return "best" if vram >= 22 else "detailed" if vram >= 15 else "fast"


def default_res(hw):
    return QUALITY[default_quality(hw)]


def choose_family(want, pixal_ready, res):
    """Pixal3D when it is in (its shape follows the picture), except at 512, where it has no texture model."""
    want = str(want or "auto").lower()
    if want == "trellis" or not pixal_ready or res < 1024:
        return "trellis"
    return "pixal3d"


def matte(glb):
    """The .glb with every material's metalness set to 0. TRELLIS often makes fabric and plastic partly metallic,
    which shows as dark, shiny clothes; the colours and the shape stay as they were."""
    import struct
    if glb[:4] != b"glTF" or len(glb) < 20:
        return glb
    jlen, jtype = struct.unpack("<I4s", glb[12:20])
    if jtype != b"JSON":
        return glb
    doc = json.loads(glb[20:20 + jlen].decode("utf-8"))
    for m in doc.get("materials") or []:
        m.setdefault("pbrMetallicRoughness", {})["metallicFactor"] = 0.0
    body = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    body += b" " * (-len(body) % 4)
    rest = glb[20 + jlen:]
    return struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(body) + len(rest)) + struct.pack("<I4s", len(body), b"JSON") \
        + body + rest


# ---------------------------------------------------------------- one run
class Run:
    """One trellis-cli run: a picture in, a .glb out. Progress is the last line it printed."""

    def __init__(self, cli, models, picture, out, res=1024, seed=None, faces=None, threads=0, family="trellis",
                 quality=None):
        self.cmd = [cli, "-i", picture, "-o", out, "-m", models, "--res", str(int(res))]
        if family == "pixal3d":
            self.cmd += ["--model", "pixal3d"]
        self.cmd += EXTRA.get(quality or {512: "fast", 1024: "detailed", 1536: "best"}.get(int(res), "detailed"), [])
        if seed is not None and str(seed).strip() not in ("", "-1"):
            self.cmd += ["-s", str(int(seed))]
        if faces:
            self.cmd += ["--faces", str(max(1000, int(faces)))]
        if threads and int(threads) > 0:
            self.cmd += ["-t", str(int(threads))]
        self.out = out
        self.proc = None

    def run(self, cancelled=lambda: False, progress=None, timeout=TIMEOUT):
        os.makedirs(config.STATE_DIR, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8", errors="replace") as log:
            log.write("$ " + " ".join(self.cmd) + "\n\n")
            log.flush()
            kw = {"creationflags": hardware.NO_WINDOW} if WINDOWS else {"start_new_session": True}
            self.proc = subprocess.Popen(self.cmd, stdout=log, stderr=subprocess.STDOUT, env=llama._env_for(self.cmd[0]),
                                         cwd=config.STATE_DIR, **kw)
        end = time.time() + timeout
        try:
            while self.proc.poll() is None:
                if cancelled():
                    raise RuntimeError("Cancelled.")
                if time.time() > end:
                    raise RuntimeError("Making the 3D model took too long.")
                if progress:
                    progress(last_line())
                time.sleep(1)
        finally:
            if self.proc.poll() is None:
                self.stop()
        if cancelled():
            raise RuntimeError("Cancelled.")                # stopped from outside (the Stop button)
        if self.proc.returncode != 0 or not os.path.isfile(self.out) or not os.path.getsize(self.out):
            raise RuntimeError(explain())
        with open(self.out, "rb") as f:
            return f.read()

    def stop(self):
        p = self.proc
        if not p or p.poll() is not None:
            return
        if WINDOWS:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True,
                           creationflags=hardware.NO_WINDOW)
        else:
            p.terminate()
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=5)


def log_tail(n=40):
    try:
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return ""


def last_line():
    lines = [l.strip() for l in log_tail(6).splitlines() if l.strip() and not l.startswith("$ ")]
    return lines[-1][:160] if lines else ""


def out_of_memory():
    low = log_tail(80).lower()
    return any(s in low for s in ("out of memory", "failed to allocate", "cudamalloc", "vk::outofdevicememory",
                                  "erroroutofdevicememory", "exceeds device buffer size limit"))


def explain():
    if out_of_memory():
        return ("The 3D engine ran out of graphics memory. Try a lower quality, or stop the text model while it works "
                "(Images > the text model pauses: Always).")
    hits = [l.strip() for l in log_tail(60).splitlines()
            if re.search(r"error|fail|abort|assert|not found|cannot|unsupported", l, re.I)]
    if hits:
        return "The 3D engine stopped: " + " | ".join(h[:200] for h in hits[-2:])
    lines = [l.strip() for l in log_tail(10).splitlines() if l.strip()]
    return "The 3D engine stopped: " + (lines[-1][:300] if lines else "no log")


# what a picture made for 3D should look like: one whole object the engine can cut out cleanly
PICTURE_HINT = ("a single {what}, the whole object in view with space around it, centered, studio product shot, "
                "soft even light, plain light grey background, no shadows, no text")


def picture_prompt(what):
    what = re.sub(r"\s+", " ", str(what or "")).strip().rstrip(".")
    return PICTURE_HINT.format(what=what)
