"""Signed-in browsers and phones. Signing in with Pumpkin's key gives that browser its own session key, so one
lost phone can be signed out without touching the others. Only a hash of each session key is stored."""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from . import config

MAX_SESSIONS = 50
_lock = threading.Lock()
_cache = None                 # sessions in memory; last-used times are written at most every few minutes
_dirty_at = 0.0


def _path():
    return os.path.join(config.CONFIG_DIR, "sessions.json")


def _hash(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def _load():
    global _cache
    if _cache is None:
        try:
            with open(_path()) as f:
                _cache = [s for s in json.load(f).get("sessions", []) if isinstance(s, dict) and s.get("id")]
        except (OSError, ValueError, AttributeError):
            _cache = []
    return _cache


def _save():
    global _dirty_at
    config.ensure_dirs()
    tmp = _path() + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"sessions": _cache}, f, indent=2)
    os.replace(tmp, _path())
    _dirty_at = 0.0


def reload():
    """Forget the in-memory copy (tests, or another process changed the file)."""
    global _cache
    with _lock:
        _cache = None


def describe(ua):
    """"Chrome on Android" from a User-Agent."""
    ua = ua or ""
    if "; wv)" in ua or "PumpkinApp" in ua:
        browser = "Pumpkin app"
    elif "Edg/" in ua:
        browser = "Edge"
    elif "Firefox/" in ua or "FxiOS" in ua:
        browser = "Firefox"
    elif "Chrome/" in ua or "CriOS" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = "Browser"
    if "Android" in ua:
        system = "Android"
    elif "iPhone" in ua:
        system = "iPhone"
    elif "iPad" in ua:
        system = "iPad"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        system = "Mac"
    elif "Windows" in ua:
        system = "Windows"
    elif "CrOS" in ua:
        system = "ChromeOS"
    elif "Linux" in ua or "X11" in ua:
        system = "Linux"
    else:
        system = "unknown system"
    return f"{browser} on {system}"


def create(ua="", ip="", local=False):
    """A new session; returns the key for the cookie ("<id>.<secret>")."""
    sid, secret = secrets.token_hex(6), secrets.token_urlsafe(24)
    now = int(time.time())
    name = describe(ua)
    with _lock:
        items = _load()
        if local and name.startswith("Pumpkin app"):   # the app window signs in at every start: replace
            items[:] = [s for s in items if not (s.get("local") and s.get("name") == name)]
        items.append({"id": sid, "hash": _hash(secret), "name": name, "ip": ip, "local": bool(local),
                      "created": now, "last_used": now})
        items.sort(key=lambda s: s.get("last_used", 0), reverse=True)
        del items[MAX_SESSIONS:]
        _save()
    return f"{sid}.{secret}"


def check(key):
    """The session for a cookie key, or None."""
    global _dirty_at
    if not key or "." not in key:
        return None
    sid, _, secret = key.partition(".")
    with _lock:
        for s in _load():
            if s["id"] == sid and hmac.compare_digest(s.get("hash", ""), _hash(secret)):
                now = time.time()
                if now - s.get("last_used", 0) > 60:
                    s["last_used"] = int(now)
                    if not _dirty_at:
                        _dirty_at = now
                if _dirty_at and now - _dirty_at > 300:
                    _save()
                return s
    return None


def listing(current_id=None):
    with _lock:
        return [{k: v for k, v in s.items() if k != "hash"} | {"current": s["id"] == current_id} for s in _load()]


def remove(sid):
    with _lock:
        items = _load()
        n = len(items)
        items[:] = [s for s in items if s["id"] != sid]
        if len(items) != n:
            _save()
        return len(items) != n


def clear(keep=None):
    """Sign out every browser and phone (except `keep`, the one asking)."""
    with _lock:
        items = _load()
        items[:] = [s for s in items if s["id"] == keep]
        _save()
