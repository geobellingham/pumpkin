"""Connectors: ready-made MCP servers for apps and services (GitHub, Notion, a browser, Jira, a database...).

The catalog (connectors.json) says how to start each one and what it needs: a token, a folder, a switch. Your
answers are kept in connectors.json in Pumpkin's settings folder, on this computer only. A connector that is switched
on and filled in goes into the MCP file llama-server gets (core.mcp_config), for every preset or only the presets
that list it. The chat asks you before each tool call, as for every tool.

Servers start with npx (Node.js), uvx (uv) or docker, which download them on first use. "Test" starts one here, asks
it for its tools and stops it again, which also gets the download done before a model needs it."""
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

from . import config, hardware

CATALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "connectors.json")
WINDOWS = sys.platform == "win32"
NEEDS = {"npx": ("Node.js", "https://nodejs.org"), "uvx": ("uv", "https://docs.astral.sh/uv/"),
         "docker": ("Docker", "https://docs.docker.com/get-docker/")}

_catalog = None
_lock = threading.RLock()


def catalog():
    global _catalog
    if _catalog is None:
        with open(CATALOG, encoding="utf-8") as f:
            _catalog = json.load(f)
    return _catalog


def spec(cid):
    s = next((c for c in catalog() if c["id"] == cid), None)
    if not s:
        raise LookupError(f"There is no connector {cid}.")
    return s


# ---------------------------------------------------------------- what you set
def _path():
    return os.path.join(config.CONFIG_DIR, "connectors.json")


def load():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    os.makedirs(config.CONFIG_DIR, exist_ok=True)
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _path())
    try:
        os.chmod(_path(), 0o600)                            # it holds tokens
    except OSError:
        pass


def values(cid):
    """Your answers for one connector, with each field's default filled in."""
    s = spec(cid)
    mine = (load().get(cid) or {}).get("values") or {}
    out = {}
    for f in s["fields"]:
        v = mine.get(f["id"], f.get("default"))
        out[f["id"]] = v
    return out


def missing(cid, vals=None):
    """The labels of what still has to be filled in, or [] when it is ready."""
    s = spec(cid)
    vals = vals if vals is not None else values(cid)
    out = [f["label"] for f in s["fields"] if f.get("required") and not _filled(vals.get(f["id"]))]
    one = s.get("one_of")
    if one and not any(_filled(vals.get(k)) for k in one):
        labels = {f["id"]: f["label"] for f in s["fields"]}
        out.append(" or ".join(labels[k] for k in one))
    return out


def _filled(v):
    if isinstance(v, list):
        return any(str(x).strip() for x in v)
    return v is not None and str(v).strip() != ""


def available(cid):
    """The program it starts with is on this computer (Docker also has to be running)."""
    need = spec(cid)["needs"]
    if need == "docker":
        try:
            from . import workspace
            return workspace.docker_usable()
        except Exception:  # noqa: BLE001
            return bool(shutil.which("docker"))
    return bool(shutil.which(need))


def public():
    """The catalog with your state, for the Connectors page. Secrets come back as "set" or ""."""
    data = load()
    out = []
    for s in catalog():
        mine = data.get(s["id"]) or {}
        vals = values(s["id"])
        shown = {}
        for f in s["fields"]:
            v = vals.get(f["id"])
            shown[f["id"]] = ("set" if _filled(v) else "") if f["kind"] == "secret" else v
        need = NEEDS.get(s["needs"], (s["needs"], ""))
        out.append({**{k: s[k] for k in ("id", "name", "blurb", "needs", "docs", "fields")},
                    "needs_name": need[0], "needs_url": need[1], "values": shown,
                    "enabled": bool(mine.get("enabled")), "added": s["id"] in data,
                    "missing": missing(s["id"], vals), "available": available(s["id"]),
                    "test": mine.get("test")})
    return out


def save(cid, new_values=None, enabled=None):
    """Changes your answers (a secret sent as "set" keeps the one you had) and switches it on or off."""
    s = spec(cid)
    with _lock:
        data = load()
        mine = data.setdefault(cid, {"enabled": False, "values": {}})
        vals = mine.setdefault("values", {})
        kinds = {f["id"]: f["kind"] for f in s["fields"]}
        for k, v in (new_values or {}).items():
            if k not in kinds:
                continue
            if kinds[k] == "secret" and v == "set":
                continue
            if kinds[k] == "toggle":
                v = bool(v)
            elif kinds[k] == "paths":
                v = [str(x).strip() for x in (v if isinstance(v, list) else str(v or "").splitlines()) if str(x).strip()]
            else:
                v = str(v or "").strip()
            vals[k] = v
            mine.pop("test", None)                          # the old test result is about the old settings
        if enabled is not None:
            if enabled and missing(cid, {**values(cid), **vals}):
                raise ValueError("Fill in " + ", ".join(missing(cid, {**values(cid), **vals})) + " first.")
            mine["enabled"] = bool(enabled)
        _save(data)
    return next(c for c in public() if c["id"] == cid)


def remove(cid):
    with _lock:
        data = load()
        data.pop(cid, None)
        _save(data)


# ---------------------------------------------------------------- starting one
def entry(cid):
    """How llama-server starts it: {"command", "args", "env"}."""
    s = spec(cid)
    vals = values(cid)
    if missing(cid, vals):
        raise ValueError(f"{s['name']} needs {', '.join(missing(cid, vals))}.")
    args, env = list(s.get("args") or []), {}
    for f in s["fields"]:
        v = vals.get(f["id"])
        if f["kind"] == "toggle":
            args += f.get("on_args" if v else "off_args") or []
            continue
        if not _filled(v):
            continue
        if f["kind"] == "paths":
            args += [os.path.expanduser(str(x)) for x in v]
            continue
        v = os.path.expanduser(str(v)) if f["kind"] == "path" else str(v)
        if f.get("env"):
            env[f["env"]] = v
        if f.get("args"):
            args += [a.replace("{}", v) for a in f["args"]]
    args += s.get("args_after") or []
    cmd = s["command"]
    found = shutil.which(cmd)
    if WINDOWS and found and found.lower().endswith((".cmd", ".bat")):
        # Windows starts .cmd programs (npx is one) only through cmd.exe
        return {"command": "cmd", "args": ["/c", cmd] + args, "env": env}
    return {"command": found or cmd, "args": args, "env": env}


def servers_for(preset):
    """name -> entry for the connectors this preset uses: those it lists, or every one that is on."""
    want = (preset or {}).get("connectors")
    out = {}
    data = load()
    for cid, mine in data.items():
        if not mine.get("enabled"):
            continue
        if isinstance(want, list) and cid not in want:
            continue
        try:
            if not available(cid):
                continue
            e = entry(cid)
        except (LookupError, ValueError):
            continue
        e["timeout_ms"] = 300_000
        out[cid] = e
    return out


def test(cid, timeout=240):
    """Starts it, asks for its tools and stops it. Returns {"ok", "tools", "error"} and remembers it."""
    try:
        if not available(cid):
            need = NEEDS.get(spec(cid)["needs"], ("it",))[0]
            raise RuntimeError(f"{need} is not installed on this computer (or not running).")
        tools = handshake(entry(cid), timeout=timeout)
        result = {"ok": True, "tools": tools, "error": None, "at": time.time()}
    except (RuntimeError, ValueError, OSError, LookupError) as e:
        result = {"ok": False, "tools": [], "error": str(e)[:600], "at": time.time()}
    with _lock:
        data = load()
        if cid in data:
            data[cid]["test"] = result
            _save(data)
    return result


def handshake(e, timeout=240):
    """Talks MCP over stdin and stdout: initialize, then tools/list. Returns the tool names."""
    env = dict(os.environ)
    env.update(e.get("env") or {})
    try:
        p = subprocess.Popen([e["command"]] + list(e.get("args") or []), stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                             creationflags=hardware.NO_WINDOW if WINDOWS else 0)
    except OSError as ex:
        raise RuntimeError(f"It did not start: {ex}") from ex
    lines, err = queue.Queue(), []

    def pump_out():
        for line in p.stdout:
            lines.put(line)
        lines.put(None)

    def pump_err():
        for line in p.stderr:
            err.append(line.decode("utf-8", "replace"))
            del err[:-40]

    threading.Thread(target=pump_out, daemon=True).start()
    threading.Thread(target=pump_err, daemon=True).start()
    end = time.time() + timeout

    def send(msg):
        p.stdin.write((json.dumps(msg) + "\n").encode())
        p.stdin.flush()

    def wait(mid):
        while True:
            left = end - time.time()
            if left <= 0:
                raise RuntimeError(f"No answer within {timeout} seconds. The first start downloads it, which can be "
                                   f"slow: try again.")
            try:
                line = lines.get(timeout=min(left, 1))
            except queue.Empty:
                continue
            if line is None:
                tail = "".join(err[-8:]).strip()
                raise RuntimeError("It stopped." + (f" It said: {tail[-500:]}" if tail else ""))
            try:
                msg = json.loads(line)
            except ValueError:
                continue                                    # some servers print notes on stdout
            if isinstance(msg, dict) and msg.get("id") == mid:
                if msg.get("error"):
                    raise RuntimeError(f"It answered with an error: {msg['error'].get('message', msg['error'])}")
                return msg.get("result") or {}

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "pumpkin", "version": "1"}}})
        wait(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        r = wait(2)
        return [t.get("name") for t in r.get("tools") or [] if isinstance(t, dict)]
    except (BrokenPipeError, OSError) as ex:
        tail = "".join(err[-8:]).strip()
        raise RuntimeError("It stopped." + (f" It said: {tail[-500:]}" if tail else f" ({ex})")) from ex
    finally:
        try:
            p.stdin.close()
        except OSError:
            pass
        try:
            p.terminate()
            p.wait(5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                p.kill()
                p.wait(5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        for pipe in (p.stdout, p.stderr):
            try:
                pipe.close()
            except OSError:
                pass
