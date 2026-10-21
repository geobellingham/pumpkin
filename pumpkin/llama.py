"""Finding, downloading and running llama-server."""
import glob
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.client import HTTPException

from . import config, hardware

RELEASES = "https://github.com/ggml-org/llama.cpp/releases"
LLAMA_DIR = os.path.join(config.DATA_DIR, "llama")
LOG_PATH = os.path.join(config.STATE_DIR, "llama-server.log")
USER_AGENT = "pumpkin"
KEY_PATH = os.path.join(config.STATE_DIR, "llama-key")


def api_key():
    """A private key llama-server requires, so only Pumpkin can use it (not other programs or web pages)."""
    try:
        with open(KEY_PATH) as f:
            k = f.read().strip()
        if len(k) >= 32:
            return k
    except OSError:
        pass
    os.makedirs(config.STATE_DIR, exist_ok=True)
    k = secrets.token_urlsafe(32)
    fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(k)
    return k


def auth_header():
    return {"Authorization": f"Bearer {api_key()}"}

# llama-server options Pumpkin knows how to use, checked against `llama-server --help`
KNOWN_FLAGS = ["--agent", "--tools-runtime", "-fit", "-fitt", "--reasoning-budget", "--no-reasoning-preserve",
               "--webui-config-file", "--mcp-servers-config", "--mmproj", "--metrics", "-fa", "-ctk",
               "--sleep-idle-seconds", "--n-cpu-moe", "--jinja"]


def candidates(settings):
    out = []
    if settings["llama_server"]:
        out.append(os.path.expanduser(settings["llama_server"]))
    out += sorted(glob.glob(os.path.join(LLAMA_DIR, "*", "llama-server")), key=os.path.getmtime, reverse=True)
    out += sorted(glob.glob(os.path.join(LLAMA_DIR, "*", "*", "llama-server")), key=os.path.getmtime, reverse=True)
    w = shutil.which("llama-server")
    if w:
        out.append(w)
    out += [os.path.join(config.HOME, "llama.cpp", "build", "bin", "llama-server"), "/usr/local/bin/llama-server",
            "/opt/llama.cpp/bin/llama-server", "/opt/homebrew/bin/llama-server"]          # the last one: Homebrew on a Mac
    seen, res = set(), []
    for c in out:
        if c and c not in seen and os.path.isfile(c) and os.access(c, os.X_OK):
            seen.add(c)
            res.append(c)
    return res


def find_server(settings):
    c = candidates(settings)
    return c[0] if c else None


def _env_for(binary):
    env = dict(os.environ)
    d = os.path.dirname(os.path.realpath(binary))
    var = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
    env[var] = d + (":" + env[var] if env.get(var) else "")
    return env


_probe_cache = {}


def probe(binary):
    """Version, backends and supported options of a llama-server binary."""
    if not binary:
        return None
    try:
        key = (binary, os.path.getmtime(binary))
    except OSError:
        return None
    if key in _probe_cache:
        return _probe_cache[key]
    info = {"path": binary, "version": None, "build": None, "commit": None, "flags": [], "backends": [], "ok": False,
            "error": None}
    try:
        r = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30, env=_env_for(binary))
        txt = (r.stdout or "") + (r.stderr or "")
        m = re.search(r"version:\s*(\d+)\s*\(([0-9a-f]+)\)", txt)
        if m:
            info["build"], info["commit"] = int(m.group(1)), m.group(2)
            info["version"] = f"b{m.group(1)}"
        for b in ("Vulkan", "CUDA", "ROCm", "HIP", "Metal", "SYCL", "CPU"):
            if re.search(rf"\b{b}\b", txt):
                info["backends"].append(b)
        if not info["backends"] and re.search(r"arm64-apple-darwin", txt):
            info["backends"].append("Metal")       # Apple Silicon builds use Metal without saying so
        h = subprocess.run([binary, "--help"], capture_output=True, text=True, timeout=30, env=_env_for(binary))
        htxt = (h.stdout or "") + (h.stderr or "")
        info["flags"] = [f for f in KNOWN_FLAGS if re.search(rf"(^|[\s,]){re.escape(f)}([\s,=]|$)", htxt, re.M)]
        info["ok"] = r.returncode == 0 or bool(info["version"])
        if not info["ok"]:
            info["error"] = txt.strip()[-400:]
    except (OSError, subprocess.SubprocessError) as e:
        info["error"] = str(e)
    _probe_cache[key] = info
    return info


# ---------------------------------------------------------------- downloading a release
def latest_tag():
    """Newest release tag, read from the redirect of /releases/latest (no API token needed)."""
    req = urllib.request.Request(f"{RELEASES}/latest", headers={"User-Agent": USER_AGENT}, method="HEAD")
    with urllib.request.urlopen(req, timeout=20) as r:
        m = re.search(r"/tag/(b\d+)", r.geturl())
    if not m:
        raise RuntimeError("Could not find the latest llama.cpp release.")
    return m.group(1)


def asset_names(tag, backend, arch="x64", system=None):
    if (system or sys.platform) == "darwin":
        # the arm64 build uses Metal (Apple Silicon GPUs); Intel Macs get the x64 CPU build
        return [f"llama-{tag}-bin-macos-{arch}.tar.gz", f"llama-{tag}-bin-macos-{arch}.zip"]
    if backend == "cuda":
        return [f"llama-{tag}-bin-ubuntu-cuda-12.8-{arch}.tar.gz", f"llama-{tag}-bin-ubuntu-cuda-13.4-{arch}.tar.gz"]
    if backend == "vulkan":
        return [f"llama-{tag}-bin-ubuntu-vulkan-{arch}.tar.gz", f"llama-{tag}-bin-ubuntu-vulkan-{arch}.zip"]
    return [f"llama-{tag}-bin-ubuntu-{arch}.tar.gz", f"llama-{tag}-bin-ubuntu-{arch}.zip"]


GITHUB_API = os.environ.get("PUMPKIN_GITHUB_API", "https://api.github.com")


def release_digests(tag):
    """SHA-256 of every file in a llama.cpp release, as GitHub publishes them."""
    req = urllib.request.Request(f"{GITHUB_API}/repos/ggml-org/llama.cpp/releases/tags/{tag}",
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            assets = json.loads(r.read().decode()).get("assets") or []
    except (urllib.error.URLError, OSError, ValueError, HTTPException) as e:
        if os.environ.get("PUMPKIN_SKIP_VERIFY"):
            return {}
        raise RuntimeError(f"Could not check the llama.cpp download against GitHub ({e}). Try again in a minute.")
    return {a.get("name"): (a.get("digest") or "") for a in assets}


def verify(path, digest):
    """Refuses a download whose SHA-256 differs from the published one."""
    if not digest:
        return                                  # GitHub has no checksum for this file (older releases)
    algo, _, want = digest.partition(":")
    if algo != "sha256":
        return
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != want.lower():
        os.remove(path)
        raise RuntimeError(f"{os.path.basename(path).replace('.part', '')} did not match its published checksum, "
                           "so Pumpkin deleted it. Try the download again.")


def machine_arch():
    m = os.uname().machine
    return "arm64" if m in ("aarch64", "arm64") else "x64"


def _fetch(url, dest, progress=None, cancelled=lambda: False):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancelled():
                raise RuntimeError("Cancelled")
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)


def _extract(archive, dest):
    if archive.endswith(".zip") and shutil.which("unzip"):
        # unzip keeps the symlinks and execute bits that Python's zipfile drops (macOS builds have both)
        os.makedirs(dest, exist_ok=True)
        r = subprocess.run(["unzip", "-q", "-o", archive, "-d", dest], capture_output=True, text=True)
        if r.returncode == 0:
            return
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
        return
    with tarfile.open(archive) as t:
        try:
            t.extractall(dest, filter="data")
        except TypeError:                      # Python < 3.12
            t.extractall(dest)


def download_release(backend, tag=None, progress=None, cancelled=lambda: False):
    """Download a prebuilt llama-server for this backend and return its path."""
    tag = tag or latest_tag()
    arch = machine_arch()
    os.makedirs(LLAMA_DIR, exist_ok=True)
    target = os.path.join(LLAMA_DIR, f"{tag}-{backend}")
    digests = release_digests(tag)
    last_err = None
    for name in asset_names(tag, backend, arch):
        url = f"{RELEASES}/download/{tag}/{name}"
        tmp = os.path.join(LLAMA_DIR, name + ".part")
        try:
            _fetch(url, tmp, progress, cancelled)
        except urllib.error.HTTPError as e:
            last_err = f"{name}: HTTP {e.code}"
            continue
        verify(tmp, digests.get(name))
        shutil.rmtree(target, ignore_errors=True)
        _extract(tmp, target)
        os.remove(tmp)
        if backend == "cuda":
            label = name.split("-cuda-")[1].split("-")[0]
            cud = f"cudart-llama-bin-ubuntu-cuda-{label}-{arch}.tar.gz"
            try:
                rt = os.path.join(LLAMA_DIR, cud + ".part")
                cname = f"cudart-llama-{tag}-bin-ubuntu-cuda-{label}-{arch}.tar.gz"
                _fetch(f"{RELEASES}/download/{tag}/{cname}", rt, None, cancelled)
                verify(rt, digests.get(cname))
                _extract(rt, target)
                os.remove(rt)
            except (urllib.error.URLError, OSError):
                pass                           # the system CUDA runtime may be enough
        if sys.platform == "darwin":
            # downloaded by Pumpkin rather than a browser, so macOS should not quarantine it; clear it in case
            subprocess.run(["xattr", "-dr", "com.apple.quarantine", target], capture_output=True)
        hits = glob.glob(os.path.join(target, "**", "llama-server"), recursive=True)
        if not hits:
            raise RuntimeError(f"The download did not contain llama-server ({name}).")
        binary = hits[0]
        bindir = os.path.dirname(binary)
        # put any runtime libraries next to the binary so LD_LIBRARY_PATH finds them
        libs = glob.glob(os.path.join(target, "**", "*.so*"), recursive=True) + \
            glob.glob(os.path.join(target, "**", "*.dylib"), recursive=True)
        for lib in libs:
            dst = os.path.join(bindir, os.path.basename(lib))
            if not os.path.exists(dst):
                try:
                    shutil.copy2(lib, dst)
                except OSError:
                    pass
        os.chmod(binary, 0o755)
        return binary
    raise RuntimeError(f"No {backend} build of llama.cpp {tag} for {arch} was found ({last_err}).")


# ---------------------------------------------------------------- talking to a running server
def http(port, path, payload=None, timeout=60, method=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"Content-Type": "application/json", **auth_header()},
                                 method=method or ("POST" if payload is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(body)
        except ValueError:
            j = {}
        err = j.get("error")
        if isinstance(err, dict):
            err = err.get("message")
        return {"error": err or f"HTTP {e.code}: {body[:300]}", "status": e.code}
    except (urllib.error.URLError, OSError, HTTPException) as e:
        return {"error": f"llama-server is not reachable ({e})", "status": 0}


def health(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.8) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        return False if e.code == 503 else False
    except (urllib.error.URLError, OSError, HTTPException):
        return False


def metrics(port):
    out = {}
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/metrics", headers=auth_header())
        with urllib.request.urlopen(req, timeout=1) as r:
            for line in r.read().decode().splitlines():
                m = re.match(r"^llamacpp:(\w+)\s+([0-9.eE+-]+)", line)
                if m:
                    out[m.group(1)] = float(m.group(2))
    except (urllib.error.URLError, OSError, ValueError, HTTPException):
        pass                                   # the model may be stopping or switching right now
    return out


# ---------------------------------------------------------------- running it
class ServerManager:
    """Starts and stops llama-server for a preset and reports what it is doing."""

    def __init__(self, settings, library):
        self.settings = settings
        self.library = library
        self.proc = None
        self.preset = None             # dict of the preset that is running or loading
        self.state = "off"             # off | loading | ready | error | external
        self.error = None
        self.started_at = None
        self.lock = threading.RLock()
        self.on_change = []
        threading.Thread(target=self._watch, daemon=True).start()

    @property
    def port(self):
        return int(self.settings["llama_port"])

    # ---- command line
    def build_cmd(self, preset, binary, info):
        model = self.library.resolve(preset.get("model"))
        if not model:
            raise RuntimeError(f"The model file for “{preset.get('name')}” was not found. "
                               "Pick a model in the preset, or download it under Models.")
        flags = set(info.get("flags") or [])
        has = lambda f: f in flags or not info.get("flags")
        s = self.settings
        threads = int(s["threads"] or 0) or hardware.cpu()["physical_cores"]
        cmd = [binary, "-m", model["path"]]
        mm = self.library.resolve(preset.get("mmproj")) if preset.get("mmproj") else None
        if mm:
            cmd += ["--mmproj", mm["path"]]
        ctx = int(preset.get("ctx") or 8192)
        cmd += ["-c", str(ctx), "-np", "1", "-t", str(threads)]
        if has("-fit") and preset.get("fit", True):
            cmd += ["-fit", "on"]
            if has("-fitt"):
                cmd += ["-fitt", str(int(preset.get("fitt") or 1024))]
        elif preset.get("gpu_layers") is not None:
            cmd += ["-ngl", str(preset["gpu_layers"])]
        if has("-fa"):
            cmd += ["-fa", "on"]
        if has("--metrics"):
            cmd += ["--metrics"]
        kv = preset.get("kv") or ""
        if kv and kv != "f16" and has("-ctk"):
            cmd += ["-ctk", kv, "-ctv", kv]
        tb = preset.get("think_budget")
        if tb not in (None, "", -1) and has("--reasoning-budget"):
            if has("--no-reasoning-preserve"):
                cmd += ["--no-reasoning-preserve"]
            cmd += ["--reasoning-budget", str(int(tb))]
        if preset.get("tools") and has("--agent"):
            cmd += ["--agent"]
            if has("--tools-runtime") and preset.get("sandbox", True) and shutil.which("docker"):
                cmd += ["--tools-runtime", f"docker:{s['sandbox_image']}"]
            mcp = self.mcp_config(preset)
            if mcp and has("--mcp-servers-config") and _mcp_has_servers(mcp):
                cmd += ["--mcp-servers-config", mcp]
        # the persona reaches the chat through Pumpkin's UI (it keeps the browser's system message in step);
        # handing it to llama-server as a UI default as well made the two fight over it
        idle = int(preset.get("idle_minutes") or 0)
        if idle > 0 and has("--sleep-idle-seconds"):
            cmd += ["--sleep-idle-seconds", str(idle * 60)]
        extra = preset.get("extra") or ""
        if isinstance(extra, str):
            import shlex
            extra = shlex.split(extra)
        cmd += list(extra)
        cmd += ["--host", "127.0.0.1", "--port", str(self.port)]
        return cmd

    # replaced by the app
    persona_prompt = lambda self: ""
    mcp_config = lambda self, preset: None

    # ---- lifecycle
    def start(self, preset):
        with self.lock:
            binary = find_server(self.settings)
            if not binary:
                raise RuntimeError("llama-server was not found. Open Settings to download it.")
            info = probe(binary) or {}
            cmd = self.build_cmd(preset, binary, info)
            self._stop_locked()
            os.makedirs(config.STATE_DIR, exist_ok=True)
            log = open(LOG_PATH, "w")
            log.write("$ " + " ".join(cmd) + "\n\n")
            log.flush()
            env = _env_for(binary)
            env["LLAMA_API_KEY"] = api_key()          # through the environment, so it never shows up in `ps`
            self.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                         env=env, cwd=config.STATE_DIR)
            log.close()
            self.preset, self.state, self.error, self.started_at = dict(preset), "loading", None, time.time()
            self._inhibit(True)
            self.settings.update({"last_preset": preset.get("id") or preset.get("name")})
        self._changed()

    def stop(self):
        with self.lock:
            self._stop_locked()
            self.preset, self.state, self.error = None, "off", None
        self._changed()

    def _inhibit(self, on):
        """While a model is loaded, keep the computer from going to sleep (the screen can still lock).
        Linux asks systemd; a Mac uses caffeinate (closing a laptop's lid still sleeps it)."""
        cur = getattr(self, "_inhibitor", None)
        if sys.platform == "darwin":
            cmd = ["caffeinate", "-i"] if shutil.which("caffeinate") else None
        elif shutil.which("systemd-inhibit"):
            cmd = ["systemd-inhibit", "--what=sleep:idle", "--who=Pumpkin", "--why=A model is loaded",
                   "--mode=block", "sleep", "infinity"]
        else:
            cmd = None
        if on and cur is None and self.settings["keep_awake"] and cmd:
            try:
                self._inhibitor = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                   start_new_session=True)
            except OSError:
                self._inhibitor = None
        elif not on and cur is not None:
            try:
                os.killpg(os.getpgid(cur.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            self._inhibitor = None

    def _stop_locked(self):
        self._inhibit(False)
        p = self.proc
        if p and p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            for _ in range(40):
                if p.poll() is not None:
                    break
                time.sleep(0.25)
            if p.poll() is None:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                p.wait(timeout=5)
        self.proc = None

    def wait_ready(self, timeout=900, cancelled=lambda: False):
        end = time.time() + timeout
        while time.time() < end and not cancelled():
            if self.state == "ready" or (self.state == "external"):
                return True
            if self.state in ("error", "off"):
                return False
            time.sleep(1)
        return False

    def log_tail(self, n=60):
        try:
            with open(LOG_PATH, errors="replace") as f:
                return f.readlines()[-n:]
        except OSError:
            return []

    def _explain_failure(self):
        tail = "".join(self.log_tail(80))
        hints = [
            (r"out of memory|ErrorOutOfDeviceMemory|failed to allocate|cudaMalloc failed",
             "The model did not fit in memory. Use a smaller quant, a shorter context, or a smaller KV cache type."),
            (r"failed to load model|error loading model", "llama-server could not load the model file. It may be "
             "incomplete (re-download it) or need a newer llama-server."),
            (r"unknown model architecture", "This llama-server is too old for this model. Update it in Settings."),
            (r"invalid argument|unknown argument|error: unrecognized", "llama-server did not accept an option. Check "
             "the extra arguments in the preset, or update llama-server."),
            (r"address already in use|bind.*failed", "Port %d is in use by another program." % self.port),
            (r"docker.*permission denied|Got permission denied while trying to connect to the Docker daemon",
             "The sandbox needs Docker access. Open Doctor for the fix."),
        ]
        for pat, msg in hints:
            if re.search(pat, tail, re.I):
                return msg
        last = [l.strip() for l in tail.splitlines() if l.strip()][-3:]
        return "llama-server stopped. " + (" ".join(last)[-300:] if last else "See the log for details.")

    def _watch(self):
        while True:
            try:
                changed = False
                with self.lock:
                    p = self.proc
                    if p is not None and p.poll() is not None:
                        self.proc = None
                        if self.state in ("loading", "ready"):
                            self.state, self.error = "error", self._explain_failure()
                            changed = True
                        self._inhibit(False)
                    ok = health(self.port)
                    if self.proc is not None:
                        if ok and self.state == "loading":
                            self.state, changed = "ready", True
                    elif ok and self.state in ("off", "error"):
                        self.state, self.preset, changed = "external", None, True
                    elif not ok and self.state == "external":
                        self.state, changed = "off", True
                if changed:
                    self._changed()
            except Exception:  # noqa: BLE001 - the watcher must never die
                pass
            time.sleep(1.0)

    def _changed(self):
        for cb in list(self.on_change):
            try:
                cb(self)
            except Exception:  # noqa: BLE001
                pass

    def status(self):
        return {"state": self.state, "error": self.error, "preset": self.preset,
                "uptime": time.time() - self.started_at if self.started_at and self.state == "ready" else None,
                "port": self.port}


def _mcp_has_servers(path):
    try:
        with open(path) as f:
            j = json.load(f)
        servers = j.get("mcpServers") or j.get("servers") or {}
        return bool(servers)
    except (OSError, ValueError, AttributeError):
        return False
