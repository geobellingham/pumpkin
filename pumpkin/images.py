"""Pictures: Pumpkin runs stable-diffusion.cpp's sd-server next to llama-server, and the chat's model can draw.

- The engine (sd-server) is downloaded from stable-diffusion.cpp's GitHub releases for this computer's GPU and
  checked against the checksums GitHub publishes, like llama-server.
- Image models come as bundles (the drawing model, its text encoder and its VAE) from image_catalog.json. Each
  bundle has its own folder under ~/Pumpkin/image-models with a small manifest.
- When the model calls image_generate, Pumpkin answers the call itself (server.py). If the text model and the
  image model do not fit in memory together, the text model is stopped while drawing and started again before the
  answer goes back to the chat, which then simply carries on.
"""
import base64
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException

from . import config, hardware, llama, models

WINDOWS = os.name == "nt"
EXE = "sd-server.exe" if WINDOWS else "sd-server"
REPO = "leejet/stable-diffusion.cpp"
RELEASES = f"https://github.com/{REPO}/releases"
SD_DIR = os.path.join(config.DATA_DIR, "sd")
LOG_PATH = os.path.join(config.STATE_DIR, "sd-server.log")
PID_PATH = os.path.join(config.STATE_DIR, "sd-server.pid")
MANIFEST = "pumpkin-image.json"
CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "image_catalog.json")
GiB = 2 ** 30
SIZES = {"square": (1, 1), "portrait": (3, 4), "landscape": (4, 3), "wide": (16, 9), "tall": (9, 16)}
FLAGS = {"diffusion_model": "--diffusion-model", "model": "--model", "llm": "--llm", "vae": "--vae",
         "llm_vision": "--llm_vision", "t5xxl": "--t5xxl", "clip_l": "--clip_l", "clip_g": "--clip_g"}


def catalog():
    try:
        with open(CATALOG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def bundle_info(bundle_id):
    return next((b for b in catalog() if b["id"] == bundle_id), None)


# ---------------------------------------------------------------- the engine
def engine_backend(hw_gpus=None):
    """Which sd-server build suits this computer. stable-diffusion.cpp has CUDA builds for Windows only;
    Linux NVIDIA cards use its Vulkan build."""
    if hardware.MAC:
        return "metal" if hardware.apple_silicon() else None
    if hardware.ANDROID:
        return None
    b = hardware.recommended_backend(hw_gpus)
    if b == "cuda" and WINDOWS:
        return "cuda12"
    if b in ("cuda", "vulkan") and hardware.vulkan_loader():
        return "vulkan"
    return "cpu"


def asset_pattern(backend, system=None, arch=None):
    """The release file for a backend, as a regex on its name (the names carry the build's commit)."""
    system = system or sys.platform
    arch = arch or llama.machine_arch()
    if system == "win32":
        return re.compile(rf"^sd-.*-bin-win-{re.escape(backend)}-x64\.zip$")
    if system == "darwin":
        return re.compile(r"^sd-.*-bin-Darwin-.*-arm64\.zip$")
    cpu = "aarch64" if arch == "arm64" else "x86_64"
    if backend == "vulkan":
        return re.compile(rf"^sd-.*-bin-Linux-.*-{cpu}-vulkan\.zip$")
    return re.compile(rf"^sd-.*-bin-Linux-.*-{cpu}\.zip$")


def _github(path):
    headers = {"User-Agent": llama.USER_AGENT, "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    req = urllib.request.Request(f"{llama.GITHUB_API}/repos/{REPO}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def pick_release(releases, backend, system=None, arch=None):
    """The newest release that has a build for this backend: (release, asset), or (None, None)."""
    pat = asset_pattern(backend, system, arch)
    for rel in releases:
        if rel.get("draft"):
            continue
        for a in rel.get("assets") or []:
            if pat.match(a.get("name") or ""):
                return rel, a
    return None, None


def mac_version():
    import platform
    try:
        return int((platform.mac_ver()[0] or "0").split(".")[0])
    except ValueError:
        return 0


def mac_needs(asset_name):
    """The macOS a Mac build was made on (its name says, e.g. ...-Darwin-macOS-26.0-arm64.zip); older systems cannot
    load it."""
    m = re.search(r"macOS-(\d+)", asset_name or "")
    return int(m.group(1)) if m else 0


def find_engine(settings):
    """The sd-server to use: the one set in Settings, or the newest downloaded one."""
    own = (settings["sd_server"] or "").strip()
    if own:
        return os.path.expanduser(own) if os.path.isfile(os.path.expanduser(own)) else None
    best = None
    if os.path.isdir(SD_DIR):
        for d in os.listdir(SD_DIR):
            root = os.path.join(SD_DIR, d)
            for dirpath, _, files in os.walk(root):
                if EXE in files:
                    p = os.path.join(dirpath, EXE)
                    if not best or os.path.getmtime(p) > os.path.getmtime(best):
                        best = p
                    break
    return best


def engine_info(settings):
    path = find_engine(settings)
    info = {"path": path, "installed": bool(path), "backend": None, "tag": None}
    if path:
        try:
            with open(os.path.join(SD_DIR, "installed.json"), encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("path") == path:
                info.update(backend=meta.get("backend"), tag=meta.get("tag"))
        except (OSError, ValueError):
            pass
    return info


def download_engine(backend, progress=None, cancelled=lambda: False):
    """Downloads sd-server for this backend and returns its path. Falls back to the CPU build when a release has
    none for the GPU."""
    try:
        releases = _github("/releases?per_page=15")
    except (urllib.error.URLError, OSError, ValueError, HTTPException) as e:
        raise RuntimeError(f"Could not reach GitHub to find stable-diffusion.cpp ({e}). Try again in a minute.")
    rel, asset = pick_release(releases, backend)
    if not rel and backend != "cpu":
        backend = "cpu"
        rel, asset = pick_release(releases, backend)
    if not rel:
        raise RuntimeError("No stable-diffusion.cpp build for this computer was found in its recent releases.")
    need = mac_needs(asset["name"]) if sys.platform == "darwin" else 0
    if need and 0 < mac_version() < need:
        raise RuntimeError(f"stable-diffusion.cpp's Mac build needs macOS {need} or newer, and this Mac has macOS "
                           f"{mac_version()}. Update macOS to make pictures here, or let a paired PC draw.")
    tag = rel.get("tag_name") or "latest"
    os.makedirs(SD_DIR, exist_ok=True)
    target = os.path.join(SD_DIR, f"{config.slug(tag)}-{backend}")
    parts = [asset]
    if backend == "cuda12":
        rt = next((a for a in rel.get("assets") or [] if a.get("name") == "cudart-sd-bin-win-cu12-x64.zip"), None)
        if rt:
            parts.append(rt)
    shutil.rmtree(target, ignore_errors=True)
    for a in parts:
        tmp = os.path.join(SD_DIR, a["name"] + ".part")
        llama._fetch(a["browser_download_url"], tmp, progress, cancelled)
        llama.verify(tmp, a.get("digest") or "")
        llama._extract(tmp, target, a["name"])
        os.remove(tmp)
    path = None
    for dirpath, _, files in os.walk(target):
        if EXE in files:
            path = os.path.join(dirpath, EXE)
            break
    if not path:
        raise RuntimeError(f"The stable-diffusion.cpp download has no {EXE}.")
    if not WINDOWS:
        for dirpath, _, files in os.walk(target):
            for f in files:
                if f.startswith("sd-") or f.endswith((".so", ".dylib")) or ".so." in f:
                    p = os.path.join(dirpath, f)
                    os.chmod(p, os.stat(p).st_mode | 0o755)
    if backend == "cuda12":
        # the CUDA runtime zip unpacks into cudart/; the program looks for its DLLs next to itself
        for dirpath, _, files in os.walk(target):
            for f in files:
                if f.lower().endswith(".dll") and dirpath != os.path.dirname(path):
                    shutil.copy2(os.path.join(dirpath, f), os.path.join(os.path.dirname(path), f))
    with open(os.path.join(SD_DIR, "installed.json"), "w", encoding="utf-8") as f:
        json.dump({"path": path, "backend": backend, "tag": tag}, f)
    return path


# ---------------------------------------------------------------- image models
def models_dir(settings):
    return os.path.expanduser(settings["image_dir"])


def _quant_in(name, q):
    return re.search(rf"(^|[-_.]){re.escape(q)}([-_.]|$)", os.path.basename(name), re.I) is not None


def _size(t):
    return (t.get("lfs") or {}).get("size") or t.get("size") or 0


def resolve(bundle, trees, budget_gb):
    """Picks the exact files of a bundle from the repos' file lists. trees: repo -> [{path, size}] as the Hugging
    Face API lists them. The drawing model's quality (quant) is the best that fits budget_gb with the rest."""
    chosen = {}
    for spec in bundle["files"]:
        files = [t for t in trees.get(spec["repo"]) or [] if t.get("type", "file") == "file"
                 and re.search(spec["match"], t.get("path", ""))]
        if not files:
            raise RuntimeError(f"{spec['repo']} has no file for the {spec['role']} any more.")
        chosen[spec["role"]] = (spec, files)
    fixed = sum(_size(min(fs, key=_size)) for spec, fs in chosen.values() if not spec.get("quants"))
    out = []
    for role, (spec, files) in chosen.items():
        pick = None
        if spec.get("quants"):
            options = [(q, next((f for f in files if _quant_in(f["path"], q)), None)) for q in spec["quants"]]
            options = [(q, f) for q, f in options if f]
            for q, f in options:                                   # best first
                if (fixed + _size(f)) / GiB <= budget_gb:
                    pick = f
                    break
            if not pick:
                want = spec.get("fallback")
                pick = next((f for q, f in options if q == want), None) or (options[-1][1] if options else None)
        pick = pick or min(files, key=_size)
        out.append({"role": role, "repo": spec["repo"], "path": pick["path"], "size": _size(pick)})
    return out


def budget(hw):
    """How much an image model may take to run at full speed: the graphics card's memory (a Mac shares its RAM).
    Without a usable GPU the smaller choices are better: everything runs on the processor."""
    g = hw.get("main_gpu") or {}
    vram = g.get("vram_total") or 0
    if g.get("too_old"):
        vram = 0
    return max(0.0, vram - 1.0)


def fit(size_gb, hw, backend):
    """A label like the text models get."""
    g = hw.get("main_gpu") or {}
    vram = 0 if g.get("too_old") else (g.get("vram_total") or 0)
    ram = hw.get("ram_total") or 0
    if backend in (None, "cpu") or vram < 2:
        if size_gb > ram * 0.7:
            return {"level": "no", "text": "Too big for this computer's memory"}
        return {"level": "slow", "text": "Works, slowly: about a minute or more per picture without a graphics card"}
    if size_gb + 1 <= vram:
        return {"level": "gpu", "text": "Fits your graphics card"}
    if size_gb <= ram * 0.7:
        return {"level": "partial", "text": "Works: part of it runs from RAM, so it is slower"}
    return {"level": "no", "text": "Too big for this computer's memory"}


def read_manifest(folder):
    try:
        with open(os.path.join(folder, MANIFEST), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def installed(settings):
    """bundle id -> manifest with "ready" (every file is there in full)."""
    out = {}
    root = models_dir(settings)
    if not os.path.isdir(root):
        return out
    for d in sorted(os.listdir(root)):
        folder = os.path.join(root, d)
        m = read_manifest(folder)
        if not m or not m.get("id"):
            continue
        ok = True
        for f in m.get("files") or []:
            p = os.path.join(folder, f["file"])
            if not os.path.isfile(p) or (f.get("size") and os.path.getsize(p) != f["size"]):
                ok = False
        m["folder"], m["ready"] = folder, ok and bool(m.get("files"))
        m["size_gb"] = round(sum(f.get("size") or 0 for f in m.get("files") or []) / GiB, 2)
        out[m["id"]] = m
    return out


def write_manifest(folder, bundle, files):
    os.makedirs(folder, exist_ok=True)
    m = {"id": bundle["id"], "name": bundle["name"], "kind": kind(bundle), "defaults": bundle.get("defaults") or {},
         "files": [{"role": f["role"], "file": os.path.basename(f["path"]), "repo": f["repo"], "size": f["size"]}
                   for f in files]}
    with open(os.path.join(folder, MANIFEST), "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=1)
    return m


def dims(data):
    """Width and height of a PNG, JPEG or WebP picture, or None."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                mk = data[i + 1]
                if mk in (0xD8, 0x01) or 0xD0 <= mk <= 0xD7:
                    i += 2
                    continue
                n = struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= mk <= 0xCF and mk not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + n
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            if data[12:16] == b"VP8X":
                return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
            if data[12:16] == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if data[12:16] == b"VP8L":
                b = int.from_bytes(data[21:25], "little")
                return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
    except (struct.error, IndexError):
        pass
    return None


def kind(bundle):
    """"draw" (makes pictures from words) or "edit" (changes a picture)."""
    return (bundle or {}).get("kind") or "draw"


def size_for(w_ratio, h_ratio, base):
    """A width and height near base*base pixels in this shape, in steps of 64 (what the models expect)."""
    area = base * base
    w = (area * w_ratio / h_ratio) ** 0.5
    h = area / w
    return max(256, int(round(w / 64)) * 64), max(256, int(round(h / 64)) * 64)


# ---------------------------------------------------------------- the running engine
class ImageServer:
    """One sd-server with one image model loaded, on 127.0.0.1 only."""

    def __init__(self, settings):
        self.settings = settings
        self.proc = None
        self.bundle = None            # the manifest of the loaded model
        self.state, self.error = "off", None
        self.lock = threading.RLock()
        self.last_used = 0
        self.busy = False
        self.progress = None          # {"stage", "since"} while making a picture
        threading.Thread(target=self._idle_watch, daemon=True).start()

    def port(self):
        return int(self.settings["image_port"])

    def build_cmd(self, binary, manifest):
        cmd = [binary, "--listen-ip", "127.0.0.1", "--listen-port", str(self.port())]
        folder = manifest["folder"]
        for f in manifest["files"]:
            flag = FLAGS.get(f["role"])
            if flag:
                cmd += [flag, os.path.join(folder, f["file"])]
        d = manifest.get("defaults") or {}
        if d.get("steps"):
            cmd += ["--steps", str(d["steps"])]
        if d.get("cfg") is not None:
            cmd += ["--cfg-scale", str(d["cfg"])]
        if d.get("sampler"):
            cmd += ["--sampling-method", d["sampler"]]
        cmd += [str(a) for a in d.get("args") or []]
        threads = int(self.settings["threads"] or 0)
        if threads > 0:
            cmd += ["-t", str(threads)]
        return cmd

    def start(self, manifest, timeout=900):
        """Starts sd-server with this model and waits until it is loaded."""
        with self.lock:
            if self.state == "ready" and self.bundle and self.bundle["id"] == manifest["id"] and self._alive():
                return
            self.stop()
            binary = find_engine(self.settings)
            if not binary:
                raise RuntimeError("The image engine is not installed yet. Install it on the Images page.")
            os.makedirs(config.STATE_DIR, exist_ok=True)
            log = open(LOG_PATH, "w", encoding="utf-8", errors="replace")
            cmd = self.build_cmd(binary, manifest)
            log.write("$ " + " ".join(cmd) + "\n\n")
            log.flush()
            env = llama._env_for(binary)
            kw = {"creationflags": hardware.NO_WINDOW} if WINDOWS else {"start_new_session": True}
            self.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=config.STATE_DIR, **kw)
            log.close()
            try:
                with open(PID_PATH, "w") as f:
                    f.write(str(self.proc.pid))
            except OSError:
                pass
            self.bundle, self.state, self.error = manifest, "loading", None
            end = time.time() + timeout
            while time.time() < end:
                if self.proc.poll() is not None:
                    self.state, self.error = "error", self._explain()
                    self.proc = None
                    raise RuntimeError(self.error)
                try:
                    st = self.http("/sdcpp/v1/models/status", timeout=5)
                except (OSError, ValueError, HTTPException):
                    st = None
                if st and st.get("state") == "ready":
                    self.state, self.last_used = "ready", time.time()
                    return
                if st and st.get("state") == "error":
                    self.state, self.error = "error", st.get("error") or self._explain()
                    self.stop(keep_error=True)
                    raise RuntimeError(self.error)
                time.sleep(1)
            self.stop()
            self.state, self.error = "error", "The image model took too long to load."
            raise RuntimeError(self.error)

    def _alive(self):
        return bool(self.proc and self.proc.poll() is None)

    def stop(self, keep_error=False):
        with self.lock:
            p = self.proc
            if p and p.poll() is None:
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
            self.proc = None
            if not keep_error:
                self.state, self.error = "off", None
            self.bundle = None if not keep_error else self.bundle
            try:
                os.remove(PID_PATH)
            except OSError:
                pass

    def http(self, path, payload=None, timeout=60, method=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port()}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")

    def generate(self, prompt, width, height, seed=-1, negative="", timeout=1800, cancelled=lambda: False, ref=None):
        """Makes one picture and returns it as JPEG bytes. ref: picture bytes to change (an editing model)."""
        body = {"prompt": prompt, "negative_prompt": negative or "", "width": int(width), "height": int(height),
                "seed": int(seed if seed is not None else -1), "batch_count": 1, "output_format": "jpeg",
                "output_compression": 92, "embed_image_metadata": False}
        if ref:
            body["ref_images"] = [base64.b64encode(ref).decode()]
        try:
            job = self.http("/sdcpp/v1/img_gen", body, timeout=60)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"The image engine refused the request: {_err_text(e)}")
        jid = job.get("id")
        if not jid:
            raise RuntimeError(f"The image engine gave no job: {job}")
        end = time.time() + timeout
        while time.time() < end:
            if cancelled():
                try:
                    self.http(f"/sdcpp/v1/jobs/{urllib.parse.quote(jid)}/cancel", {}, timeout=10)
                except (OSError, ValueError, HTTPException):
                    pass
                raise RuntimeError("Cancelled.")
            if not self._alive():
                self.state, self.error = "error", self._explain()
                self.proc = None
                raise RuntimeError(self.error)
            st = self.http(f"/sdcpp/v1/jobs/{urllib.parse.quote(jid)}", timeout=30)
            s = st.get("status")
            if s == "completed":
                imgs = (st.get("result") or {}).get("images") or []
                if not imgs:
                    raise RuntimeError("The image engine finished without a picture.")
                self.last_used = time.time()
                return base64.b64decode(imgs[0]["b64_json"])
            if s in ("failed", "cancelled"):
                raise RuntimeError(((st.get("error") or {}).get("message")) or f"The picture {s}.")
            time.sleep(0.5)
        raise RuntimeError("Making the picture took too long.")

    def log_tail(self, n=40):
        try:
            with open(LOG_PATH, errors="replace") as f:
                return "".join(f.readlines()[-n:])
        except OSError:
            return ""

    def _explain(self):
        tail = self.log_tail(30)
        low = tail.lower()
        if "out of memory" in low or "failed to allocate" in low or "cudamalloc" in low:
            return "The image model ran out of memory. Pick a smaller one on the Images page."
        lines = [l.strip() for l in tail.splitlines() if l.strip()]
        return "The image engine stopped: " + (lines[-1][:300] if lines else "no log")

    def _idle_watch(self):
        """Frees the memory after the image model has not been used for a while (Settings: minutes; 0 = never)."""
        while True:
            time.sleep(20)
            try:
                mins = float(self.settings["image_keep_min"])
            except (TypeError, ValueError):
                mins = 10
            if mins > 0 and self.state == "ready" and not self.busy and time.time() - self.last_used > mins * 60:
                self.stop()

    def status(self):
        return {"state": self.state, "error": self.error, "model": (self.bundle or {}).get("name"),
                "id": (self.bundle or {}).get("id"), "busy": self.busy, "progress": self.progress}


def _err_text(e):
    try:
        body = json.loads(e.read().decode() or "{}")
        return (body.get("error") if isinstance(body.get("error"), str) else body.get("message")) or str(e)
    except (OSError, ValueError, AttributeError):
        return str(e)


def kill_leftover():
    """An sd-server left over from before (Pumpkin was killed) still holds memory and the port."""
    try:
        with open(PID_PATH) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return
    try:
        if WINDOWS:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                 text=True, creationflags=hardware.NO_WINDOW).stdout
            if "sd-server" in out.lower():
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                               creationflags=hardware.NO_WINDOW)
        else:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                if b"sd-server" in f.read():
                    os.kill(pid, 15)
    except (OSError, ProcessLookupError):
        pass
    try:
        os.remove(PID_PATH)
    except OSError:
        pass


def hf_tree(repo, token=None):
    return models._hf_get(f"/api/models/{urllib.parse.quote(repo, safe='/')}/tree/main?recursive=1", token)
