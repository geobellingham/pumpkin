"""A vision helper: a small vision model that Pumpkin starts only when it is needed and frees afterwards.

It does two jobs. It reads scanned pages and photos of paper into the document index, and it lets a text-only chat
model "see" a picture: the helper describes it (and copies any text in it) and the chat model gets that text."""
import base64
import os
import signal
import subprocess
import threading
import time

from . import config, hardware, llama

WINDOWS = os.name == "nt"
LOG_PATH = os.path.join(config.STATE_DIR, "vision-server.log")
PID_PATH = os.path.join(config.STATE_DIR, "vision-server.pid")
GiB = 2 ** 30

DESCRIBE = ("Describe this picture for someone who cannot see it: what it shows, concretely and briefly. If it contains "
            "text, copy that text exactly after a line that says 'Text in the picture:'.")
TRANSCRIBE = ("Transcribe all the text on this page exactly as written, in reading order. Keep tables as simple rows. "
              "Output only the text. If the page has no text, describe it in one sentence.")


def mime_of(data):
    """The picture type from its first bytes, or None for a format the vision model cannot take."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] in (b"GIF8",):
        return "image/gif"
    return None


def kill_leftover():
    """A vision server left behind by a crashed Pumpkin."""
    try:
        with open(PID_PATH) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return
    try:
        if llama._alive_llama(pid):
            if WINDOWS:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                               creationflags=hardware.NO_WINDOW)
            else:
                os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass
    try:
        os.remove(PID_PATH)
    except OSError:
        pass


class Helper:
    def __init__(self, settings, library):
        self.settings = settings
        self.library = library
        self.proc = None
        self.state = "off"
        self.error = None
        self.model_path = None
        self.last_used = 0.0
        self.busy = False
        self.bad = set()                     # models whose vision file would not load with them
        self.lock = threading.Lock()
        threading.Thread(target=self._idle_watch, daemon=True).start()

    # ---- which model
    def port(self):
        return int(self.settings["vision_port"])

    def model(self):
        """The library model the helper uses: the one chosen in Settings, else the smallest vision model there is."""
        models = self.library.scan()
        want = self.settings["vision_model"]
        if want:
            m = self.library.resolve(want)
            if m and m.get("mmproj") and m["path"] not in self.bad:
                return m
        found = [m for m in models if m["kind"] == "model" and m.get("mmproj") and m.get("complete", True)
                 and m["path"] not in self.bad]
        return min(found, key=lambda m: m["size_gb"]) if found else None

    def size_gb(self, m):
        total = m["size_gb"]
        mm = next((x for x in self.library.scan() if x["path"] == m.get("mmproj")), None)
        return total + (mm["size_gb"] if mm else 0.0)

    # ---- running
    def start(self, m, timeout=600):
        with self.lock:
            if self.state == "ready" and self.model_path == m["path"] and self.proc and self.proc.poll() is None:
                return
            self._stop_locked()
            binary = llama.find_server(self.settings)
            if not binary:
                raise RuntimeError("llama-server was not found. Open Settings to download it.")
            flags = (llama.probe(binary) or {}).get("flags") or []
            has = lambda f: f in flags or not flags
            threads = int(self.settings["threads"] or 0) or hardware.cpu()["physical_cores"]
            cmd = [binary, "-m", m["path"], "--mmproj", m["mmproj"], "-c", "8192", "-np", "1", "-t", str(threads),
                   "--host", "127.0.0.1", "--port", str(self.port())]
            cmd += ["-fit", "on"] if has("-fit") else ["-ngl", "99"]
            if has("-fa"):
                cmd += ["-fa", "on"]
            os.makedirs(config.STATE_DIR, exist_ok=True)
            log = open(LOG_PATH, "w")
            log.write("$ " + " ".join(cmd) + "\n\n")
            log.flush()
            env = llama._env_for(binary)
            env["LLAMA_API_KEY"] = llama.api_key()
            kw = {"creationflags": hardware.NO_WINDOW} if WINDOWS else {"start_new_session": True}
            try:
                self.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=config.STATE_DIR,
                                             **kw)
            except OSError as e:
                self.proc, self.state, self.error = None, "error", f"The vision helper could not be started: {e}"
                raise RuntimeError(self.error) from e
            finally:
                log.close()
            try:
                with open(PID_PATH, "w") as f:
                    f.write(str(self.proc.pid))
            except OSError:
                pass
            self.model_path, self.state, self.error = m["path"], "loading", None
            end = time.time() + timeout
            while time.time() < end:
                if self.proc.poll() is not None:
                    self.state, self.error = "error", self._explain()
                    self.proc = None
                    raise RuntimeError(self.error)
                if llama.health(self.port()):
                    self.state, self.last_used = "ready", time.time()
                    return
                time.sleep(1)
            self._stop_locked()
            self.state, self.error = "error", "The vision model took too long to load."
            raise RuntimeError(self.error)

    def stop(self):
        with self.lock:
            self._stop_locked()

    def _stop_locked(self):
        p = self.proc
        if p and p.poll() is None:
            if WINDOWS:
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True,
                               creationflags=hardware.NO_WINDOW)
            else:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
        self.proc = None
        if self.state != "error":
            self.state = "off"
        try:
            os.remove(PID_PATH)
        except OSError:
            pass

    def log_tail(self, n=40):
        try:
            with open(LOG_PATH, errors="replace") as f:
                return "".join(f.readlines()[-n:])
        except OSError:
            return ""

    def _explain(self):
        tail = self.log_tail(30)
        low = tail.lower()
        if "failed to load multimodal model" in low or "mtmd_init" in low:
            self.bad.add(self.model_path)
            return "The vision file next to this model does not work with it. Pumpkin will use another vision model."
        if "out of memory" in low or "failed to allocate" in low:
            return "The vision model ran out of memory. Pick a smaller vision model in Settings."
        lines = [x.strip() for x in tail.splitlines() if x.strip()]
        return "The vision model stopped: " + (lines[-1][:300] if lines else "no log")

    # ---- using it
    def describe(self, data, prompt=DESCRIBE, max_tokens=900):
        """Asks the running vision model about one picture (bytes) and returns its answer."""
        mime = mime_of(data)
        if not mime:
            raise ValueError("This picture format cannot be read (use PNG, JPEG or WebP).")
        url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        r = llama.http(self.port(), "/v1/chat/completions", {
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt},
                                                      {"type": "image_url", "image_url": {"url": url}}]}],
            "max_tokens": max_tokens, "temperature": 0.1, "chat_template_kwargs": {"enable_thinking": False}},
            timeout=900)
        if "choices" not in r:
            raise RuntimeError(f"The vision model answered with an error: {r.get('error', r)}")
        self.last_used = time.time()
        return ((r["choices"][0].get("message") or {}).get("content") or "").strip()

    def _idle_watch(self):
        while True:
            time.sleep(20)
            try:
                mins = float(self.settings["vision_keep_min"])
            except (TypeError, ValueError):
                mins = 5
            if mins > 0 and self.state == "ready" and not self.busy and time.time() - self.last_used > mins * 60:
                self.stop()

    def status(self):
        m = self.model()
        return {"state": self.state, "error": self.error, "model": (m or {}).get("file"),
                "available": bool(m), "busy": self.busy}
