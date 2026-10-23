"""Pumpkins helping each other: one Pumpkin (usually the strongest computer) hands text tasks to the others.

Sharing side: "Share this device" gives out a pairing code: the addresses this Pumpkin answers on, plus a device
key. The key only opens /pumpkin/peer/info and /pumpkin/peer/ask: the other Pumpkin can ask the model here for an
answer and nothing else, so no settings, files, tools or chats.

Asking side: paired devices live in devices.json (private to this user). The model reaches them through the
built-in `devices` MCP server (pumpkin.mcp_devices): devices_list and devices_ask."""
import base64
import hmac
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.client import HTTPException

from . import __version__, config, hardware

CODE_PREFIX = "pumpkin-pair:"
MAX_TASK = 100_000                     # characters in one request
_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open   # devices are on your own network
_lock = threading.Lock()


# ------------------------------------------------------------------ this device
def peer_key():
    """The key other Pumpkins use to ask this one. Separate from the sign-in key, and only good for asking."""
    config.ensure_dirs()
    p = os.path.join(config.CONFIG_DIR, "peer-key")
    try:
        k = open(p).read().strip()
        if len(k) >= 24:
            return k
    except OSError:
        pass
    k = secrets.token_urlsafe(24)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(k)
    return k


def new_peer_key():
    try:
        os.remove(os.path.join(config.CONFIG_DIR, "peer-key"))
    except OSError:
        pass
    return peer_key()


def device_name(settings):
    if settings["device_name"]:
        return settings["device_name"]
    if hardware.ANDROID:
        try:
            from java import jclass                                     # Chaquopy
            b = jclass("android.os.Build")
            return f"{b.MANUFACTURER.capitalize()} {b.MODEL}".strip()
        except Exception:  # noqa: BLE001
            return "Phone"
    return socket.gethostname().split(".")[0] or "Computer"


def addresses(settings):
    """Where other devices can reach this Pumpkin, best first."""
    from . import remote
    port = int(settings["port"])
    out = []
    if settings["remote"] == "tailscale":
        host = remote.tailscale_host()
        if host:
            out.append(f"https://{host}")
    if settings["remote"] in ("lan", "tailscale") or hardware.ANDROID:
        for ip in _local_ips():
            out.append(f"http://{ip}:{port}")
    return out


def _local_ips():
    """Tailscale (100.64.0.0/10) first, then home network addresses."""
    ips = set()
    from . import remote
    ip = remote.lan_ip()
    if ip:
        ips.add(ip)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:                                    # the Tailscale address, when Tailscale is running here
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("100.100.100.100", 1))   # no packet is sent
        ts_ip = s.getsockname()[0]
        s.close()
        if ts_ip.startswith("100."):
            ips.add(ts_ip)
    except OSError:
        pass
    ips = {i for i in ips if not i.startswith("127.")}
    return sorted(ips, key=lambda i: (not _is_tailscale(i), i))


def _is_tailscale(ip):
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
        return a == 100 and 64 <= b <= 127
    except ValueError:
        return False


def pairing_code(settings):
    urls = addresses(settings)
    if not urls:
        return None
    raw = json.dumps({"n": device_name(settings), "u": urls, "k": peer_key()}, separators=(",", ":")).encode()
    return CODE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_code(code):
    code = (code or "").strip()
    if not code.startswith(CODE_PREFIX):
        raise ValueError("That is not a Pumpkin pairing code. Copy it from Devices > Share this device on the other "
                         "device.")
    body = code[len(CODE_PREFIX):]
    try:
        j = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        urls = [u for u in j["u"] if isinstance(u, str) and u.startswith(("http://", "https://"))]
        key = str(j["k"])
    except (ValueError, KeyError, TypeError):
        raise ValueError("That pairing code is damaged. Copy it again.") from None
    if not urls or len(key) < 24:
        raise ValueError("That pairing code is incomplete. Copy it again.")
    return {"name": str(j.get("n") or "Device")[:60], "urls": urls[:6], "key": key}


def key_ok(given):
    return bool(given) and hmac.compare_digest(given, peer_key())


# ------------------------------------------------------------------ requests from other devices
class Busy(Exception):
    """Too many requests from other devices right now (HTTP 429)."""


class Helper:
    """Answers other Pumpkins' requests with the model on this device."""

    def __init__(self, core):
        self.core = core
        self.recent = []                    # last requests, shown on the Devices page
        self.busy = 0
        self._times = []                    # when recent requests came in, for the hourly limit
        self._lock = threading.Lock()

    def info(self):
        core = self.core
        cur = core.server.preset if core.server.state in ("ready", "loading") else None
        live = hardware.live() if hasattr(hardware, "live") else {}
        return {"name": device_name(core.settings), "version": __version__,
                "platform": "android" if hardware.ANDROID else __import__("sys").platform,
                "model": (cur or {}).get("name"), "state": core.server.state,
                "models": [p.get("name") for p in core.presets.all() if p.get("name")],
                "busy": self.busy, "ram_total": live.get("ram_total") if isinstance(live, dict) else None}

    def begin(self, who, what):
        """Takes one of the slots other devices may use, or refuses with Busy. Returns the entry for the list."""
        core = self.core
        entry = {"at": time.time(), "from": str(who)[:60], "task": str(what)[:200], "state": "running"}
        with self._lock:
            # a paired device must not be able to tie up this model: a few at once, and so many per hour
            now = time.time()
            self._times = [t for t in self._times if now - t < 3600]
            if self.busy >= max(1, int(core.settings["share_parallel"] or 2)):
                raise Busy(f"{device_name(core.settings)} is busy with other requests. Try again in a minute.")
            if len(self._times) >= max(1, int(core.settings["share_per_hour"] or 60)):
                raise Busy(f"{device_name(core.settings)} has reached its limit of requests per hour.")
            self._times.append(now)
            self.recent = ([entry] + self.recent)[:20]
            self.busy += 1
        return entry

    def end(self):
        with self._lock:
            self.busy -= 1

    def ready_model(self, want):
        """Starts the model the other device asked for (or this device's usual one) and waits for it."""
        core = self.core
        want = str(want or "").strip()
        if want:
            if not core.ensure_preset(want):
                raise ValueError(f"This device has no model called {want}.")
        elif core.server.state not in ("ready", "loading"):
            p = core.default_preset()
            if not p:
                raise RuntimeError("This device has no model installed.")
            core.ensure_preset(p.get("name") or p.get("id"))
        if not core.server.wait_ready(timeout=900):
            raise RuntimeError(core.server.error or "The model here did not start.")
        return (core.server.preset or {}).get("name")

    def ask(self, body, who=""):
        from . import llama
        core = self.core
        task = str(body.get("task") or "").strip()
        if not task:
            raise ValueError("The task is empty.")
        if len(task) > MAX_TASK:
            raise ValueError(f"The task is too long (over {MAX_TASK} characters).")
        system = str(body.get("system") or "").strip()[:20_000]
        max_tokens = max(16, min(int(body.get("max_tokens") or 2048), 8192))
        entry = self.begin(who, task)
        t0 = time.time()
        try:
            name = self.ready_model(body.get("model"))
            msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": task}]
            r = llama.http(core.server.port, "/v1/chat/completions",
                           {"messages": msgs, "max_tokens": max_tokens, "temperature": 0.6,
                            # a small thinking model can spend every token thinking and answer nothing
                            "chat_template_kwargs": {"enable_thinking": False}}, timeout=1800)
            if "choices" not in r:
                raise RuntimeError(f"The model here answered with an error: {r.get('error', r)}")
            choice = r["choices"][0]
            answer = (choice.get("message") or {}).get("content") or ""
            if not answer.strip():
                why = ("ran out of room while thinking (finish: length)" if choice.get("finish_reason") == "length"
                       else "gave an empty answer")
                raise RuntimeError(f"The model on this device {why}. Try again with a simpler task or another model.")
            usage = r.get("usage") or {}
            entry.update(state="done", seconds=round(time.time() - t0, 1), model=name)
            return {"answer": answer, "model": name, "device": device_name(core.settings),
                    "seconds": round(time.time() - t0, 1), "tokens": usage.get("completion_tokens")}
        except Exception as e:
            entry.update(state="error", error=str(e)[:200])
            raise
        finally:
            self.end()


# ------------------------------------------------------------------ devices this Pumpkin can ask
def _store_path():
    return os.path.join(config.CONFIG_DIR, "devices.json")


def load():
    try:
        with open(_store_path()) as f:
            j = json.load(f)
        return [d for d in j.get("devices", []) if isinstance(d, dict) and d.get("id")]
    except (OSError, ValueError, AttributeError):
        return []


def save(devs):
    config.ensure_dirs()
    p = _store_path()
    tmp = p + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)     # holds the device keys
    with os.fdopen(fd, "w") as f:
        json.dump({"devices": devs}, f, indent=2)
    os.replace(tmp, p)


def public(d):
    return {k: v for k, v in d.items() if k != "key"}


def call(dev, path, payload=None, timeout=10):
    """Try the device's addresses in order; remember the one that answered."""
    last = None
    urls = list(dev.get("urls") or [])
    if dev.get("url") in urls:
        urls.remove(dev["url"])
        urls.insert(0, dev["url"])
    for base in urls:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(base.rstrip("/") + "/pumpkin/peer/" + path, data=data, method=
                                     "POST" if data else "GET",
                                     headers={"Authorization": "Bearer " + dev["key"], "Content-Type":
                                              "application/json", "X-Pumpkin-From": dev.get("from", "")[:60]})
        try:
            with _open(req, timeout=timeout) as r:
                j = json.loads(r.read().decode("utf-8", "replace") or "{}")
                dev["url"] = base
                return j
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode("utf-8", "replace")).get("error")
            except ValueError:
                msg = None
            if e.code in (401, 403):
                raise PermissionError(msg or "The other device did not accept the key. Pair it again.") from None
            if e.code == 429:
                raise RuntimeError(msg or "The other device is busy. Try again later.") from None
            if e.code in (400, 404, 500, 503):
                raise RuntimeError(msg or f"The other device answered with an error ({e.code}).") from None
            last = f"HTTP {e.code}"
        except (urllib.error.URLError, OSError, HTTPException, ValueError) as e:
            last = str(getattr(e, "reason", e))
    raise ConnectionError(f"{dev.get('name', 'The device')} is not reachable ({last or 'no address'}). Is Pumpkin "
                          f"open there, with sharing on?")


def open_chat(dev, body, timeout=3600):
    """Starts a chat on a paired device's model and returns the open response, to be streamed back as it comes.
    Tried on each of the device's addresses until one answers."""
    data = json.dumps(body).encode()
    last = None
    urls = list(dev.get("urls") or [])
    if dev.get("url") in urls:
        urls.remove(dev["url"])
        urls.insert(0, dev["url"])
    for base in urls:
        req = urllib.request.Request(base.rstrip("/") + "/pumpkin/peer/chat", data=data, method="POST",
                                     headers={"Authorization": "Bearer " + dev["key"], "Content-Type": "application/json",
                                              "X-Pumpkin-From": dev.get("from", "")[:60]})
        try:
            r = _open(req, timeout=timeout)
            dev["url"] = base
            return r
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode("utf-8", "replace")).get("error")
            except ValueError:
                msg = None
            raise RuntimeError(msg or f"{dev.get('name', 'The device')} answered with an error ({e.code}).") from None
        except (urllib.error.URLError, OSError, HTTPException) as e:
            last = str(getattr(e, "reason", e))
    raise ConnectionError(f"{dev.get('name', 'The device')} is not reachable ({last or 'no address'}). Is Pumpkin "
                          f"open there, with sharing on?")


def add(code):
    p = parse_code(code)
    dev = {"id": uuid.uuid4().hex[:10], "name": p["name"], "urls": p["urls"], "key": p["key"], "enabled": True,
           "added": int(time.time())}
    info = call(dev, "info", timeout=8)                                  # proves the code works
    dev["name"] = info.get("name") or dev["name"]
    with _lock:
        devs = [d for d in load() if d.get("key") != dev["key"]]       # pairing again replaces the old entry
        devs.append(dev)
        save(devs)
    return public(dev) | {"info": info}


def update(dev_id, changes):
    with _lock:
        devs = load()
        for d in devs:
            if d["id"] == dev_id:
                if "enabled" in changes:
                    d["enabled"] = bool(changes["enabled"])
                if changes.get("name"):
                    d["name"] = str(changes["name"]).strip()[:60]
                if changes.get("note") is not None:
                    d["note"] = str(changes["note"]).strip()[:300]
                save(devs)
                return public(d)
    raise FileNotFoundError("That device is not paired any more.")


def remove(dev_id):
    with _lock:
        devs = load()
        save([d for d in devs if d["id"] != dev_id])


def find(name_or_id, devs=None):
    devs = load() if devs is None else devs
    want = (name_or_id or "").strip().lower()
    for d in devs:
        if d["id"] == want or d.get("name", "").lower() == want:
            return d
    for d in devs:                                                       # "mac" finds "Georgi's MacBook"
        if want and want in d.get("name", "").lower():
            return d
    return None


def statuses(timeout=3):
    """Every paired device with whether it answers right now (asked in parallel)."""
    devs = load()
    out = [None] * len(devs)

    def one(i, d):
        try:
            out[i] = public(d) | {"online": True, "info": call(d, "info", timeout=timeout)}
        except Exception as e:  # noqa: BLE001
            out[i] = public(d) | {"online": False, "problem": str(e)}
    threads = [threading.Thread(target=one, args=(i, d), daemon=True) for i, d in enumerate(devs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 2)
    return [o or public(d) | {"online": False} for o, d in zip(out, devs)]


def ask(name, task, model=None, system=None, sender="", timeout=1800):
    d = find(name)
    if not d:
        names = ", ".join(x["name"] for x in load() if x.get("enabled", True)) or "none"
        raise LookupError(f"No device called {name}. Paired devices: {names}.")
    if not d.get("enabled", True):
        raise PermissionError(f"{d['name']} is switched off in Devices.")
    d["from"] = sender
    body = {"task": task}
    if model:
        body["model"] = model
    if system:
        body["system"] = system
    return call(d, "ask", body, timeout=timeout)
