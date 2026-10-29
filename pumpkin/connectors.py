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
import re
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
                    "login": bool(s.get("login")), "private": bool(s.get("private")),
                    "login_hint": (s.get("login") or {}).get("hint", ""), "signed_in": mine.get("signed_in"),
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
    s = spec(cid)
    login_cancel(cid)
    with _lock:
        data = load()
        data.pop(cid, None)
        _save(data)
    if s.get("data_dir_env"):                               # the sign-in it kept goes too
        shutil.rmtree(os.path.join(config.CONFIG_DIR, "connector-data", cid), ignore_errors=True)
    _logins.pop(cid, None)


# ---------------------------------------------------------------- starting one
def entry(cid):
    """How llama-server starts it: {"command", "args", "env"}."""
    s = spec(cid)
    vals = values(cid)
    if missing(cid, vals):
        raise ValueError(f"{s['name']} needs {', '.join(missing(cid, vals))}.")
    args, env = list(s.get("args") or []), dict(s.get("env") or {})
    if s.get("data_dir_env"):
        env[s["data_dir_env"]] = data_dir(cid)            # its sign-in lives in Pumpkin's folder, private to you
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
        if f["kind"] == "words":
            # several words after one flag: only plain words, so nothing typed here can become another option
            words = [w for w in re.split(r"[\s,]+", str(v)) if re.fullmatch(r"[A-Za-z0-9_][\w.:-]*", w)]
            if words:
                args += [f["flag"]] + ([f["join"].join(words)] if f.get("join") else words)
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


def data_dir(cid):
    """A folder only you can open, for what a connector keeps about your sign-in."""
    d = os.path.join(config.CONFIG_DIR, "connector-data", cid)
    os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _norm(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def _tool_of(cid, name, mine):
    n = _norm(name)
    if n == _norm(cid) or n.startswith(_norm(cid) + "_"):
        return True
    for t in (mine.get("test") or {}).get("tools") or []:
        tn = _norm(t)
        if tn and (n == tn or n.endswith("_" + tn)):
            return True
    return False


def private_tool(name):
    """The connector's name when this tool belongs to one that is on and holds private data (Drive, Slack, mail...)."""
    for cid, mine in load().items():
        if not mine.get("enabled"):
            continue
        try:
            s = spec(cid)
        except LookupError:
            continue
        if s.get("private") and _tool_of(cid, name, mine):
            return s["name"]
    return None


def outbound_tool(name):
    """A tool that can send something to the internet: the web tools and connectors that browse."""
    n = _norm(name)
    if n.endswith(("web_search", "read_webpage")):
        return True
    for cid, mine in load().items():
        try:
            if mine.get("enabled") and spec(cid).get("outbound") and _tool_of(cid, name, mine):
                return True
        except LookupError:
            continue
    return False


# ---------------------------------------------------------------- signing in
# Some connectors sign in with a page in your own browser (Microsoft: a code you type; Google: its usual consent page).
# Pumpkin starts the connector's own sign-in, shows you the link, and waits. It never sees your password, and it only
# shows links to the company's own sites.
_logins = {}
LOGIN_SECONDS = 900
URL_RE = re.compile(r"https://[^\s<>\"')]+")
CODE_RE = re.compile(r"\bcode\s+([A-Z0-9]{6,12})\b")


def _host_ok(url, hosts):
    h = (re.match(r"https://([^/:?#]+)", url) or [None, ""])[1].lower()
    return any(h == x or h.endswith("." + x) for x in hosts)


def login_status(cid):
    st = _logins.get(cid)
    if not st:
        return {"state": "idle"}
    return {**{k: st[k] for k in ("state", "url", "code", "message", "error")}, "lines": st["lines"][-6:]}


def login_start(cid):
    s = spec(cid)
    lg = s.get("login")
    if not lg:
        raise ValueError(f"{s['name']} has no sign-in step.")
    if not available(cid):
        raise ValueError(f"{NEEDS.get(s['needs'], (s['needs'],))[0]} is not installed on this computer.")
    e = entry(cid)
    login_cancel(cid)
    st = {"state": "starting", "url": "", "code": "", "message": lg.get("hint", ""), "error": "", "lines": [],
          "proc": None, "cancel": False}
    _logins[cid] = st
    threading.Thread(target=_login_run, args=(cid, e, lg, st), daemon=True).start()
    return login_status(cid)


def login_cancel(cid):
    st = _logins.get(cid)
    if st and st["state"] in ("starting", "waiting"):
        st["cancel"] = True
        _kill(st.get("proc"))
        st["state"], st["error"] = "cancelled", "Stopped."
    return login_status(cid)


def _kill(p):
    if not p:
        return
    try:
        if WINDOWS:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True,
                           creationflags=hardware.NO_WINDOW)
        p.terminate()
        p.wait(5)
    except (OSError, subprocess.SubprocessError):
        try:
            p.kill()
        except OSError:
            pass


def _login_run(cid, e, lg, st):
    env = dict(os.environ)
    env.update(e.get("env") or {})
    hosts = lg.get("hosts") or []
    try:
        if lg["how"] == "device":
            _login_device(e, env, lg, st, hosts)
        else:
            _login_tool(cid, e, env, lg, st, hosts)
    except Exception as ex:  # noqa: BLE001 - whatever failed is shown next to the button
        if st["state"] != "cancelled":
            st["state"], st["error"] = "error", str(ex)[:400]
    finally:
        _kill(st.get("proc"))
        if st["state"] in ("starting", "waiting"):
            st["state"], st["error"] = "error", "It stopped before you finished."
        if st["state"] == "done":
            login_done(cid)


def _login_device(e, env, lg, st, hosts):
    p = subprocess.Popen([e["command"]] + list(e.get("args") or []) + list(lg.get("args") or []), env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         errors="replace", creationflags=hardware.NO_WINDOW if WINDOWS else 0)
    st["proc"], st["state"] = p, "waiting"
    timer = threading.Timer(LOGIN_SECONDS, _kill, args=(p,))
    timer.daemon = True
    timer.start()
    for line in p.stdout:
        line = line.strip()
        if not line:
            continue
        st["lines"].append(line[:300])
        m = URL_RE.search(line)
        if m and not st["url"] and _host_ok(m.group(0).rstrip(".,"), hosts):
            st["url"] = m.group(0).rstrip(".,")
        c = CODE_RE.search(line)
        if c and not st["code"]:
            st["code"] = c.group(1)
    code = p.wait()
    if st["cancel"]:
        return
    if code == 0:
        st["state"] = "done"
    else:
        tail = " ".join(st["lines"][-3:])
        raise RuntimeError(f"Sign-in did not finish. {tail[-300:]}")


def _login_tool(cid, e, env, lg, st, hosts):
    """Starts the connector's server and asks it for its sign-in link (Google), then waits for the sign-in to land."""
    vals = values(cid)
    email = str(vals.get("email") or "").strip()
    if not email:
        raise ValueError("Fill in your account first.")
    p = subprocess.Popen([e["command"]] + list(e.get("args") or []), env=env, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace",
                         creationflags=hardware.NO_WINDOW if WINDOWS else 0)
    st["proc"] = p
    out = queue.Queue()

    def pump():
        for line in p.stdout:
            out.put(line)
        out.put(None)

    threading.Thread(target=pump, daemon=True).start()

    def send(msg):
        p.stdin.write(json.dumps(msg) + "\n")
        p.stdin.flush()

    def wait(mid, seconds=120):
        end = time.time() + seconds
        while time.time() < end:
            if st["cancel"]:
                raise RuntimeError("Stopped.")
            try:
                line = out.get(timeout=1)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError("The connector stopped.")
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict) and m.get("id") == mid:
                if m.get("error"):
                    raise RuntimeError(str(m["error"].get("message", m["error"]))[:300])
                return m.get("result") or {}
        raise RuntimeError("No answer. The first start downloads it, which can take a minute or two: try again.")

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pumpkin", "version": "1"}}})
    wait(1, 240)
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    service = (re.findall(r"[a-z]+", str(vals.get("services") or "drive")) or ["drive"])[0]
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": lg["tool"], "arguments": {"user_google_email": email, "service_name": service}}})
    r = wait(2)
    text = " ".join(c.get("text", "") for c in r.get("content") or [] if isinstance(c, dict))
    m = re.search(r"https://\S+", text)
    if not m:
        if re.search(r"already|authenticated|valid credentials", text, re.I):
            st["state"] = "done"
            return
        raise RuntimeError(text.strip()[:300] or "It did not give a sign-in link.")
    url = m.group(0).rstrip(".,)")
    if not _host_ok(url, hosts):
        raise RuntimeError("It gave a link that is not Google's, so Pumpkin did not show it.")
    st["url"], st["state"] = url, "waiting"
    cred = os.path.join(data_dir(cid), email + ".json")
    end = time.time() + LOGIN_SECONDS
    while time.time() < end:
        if st["cancel"]:
            return
        if os.path.isfile(cred):
            st["state"] = "done"
            return
        if p.poll() is not None:
            raise RuntimeError("The connector stopped before you finished.")
        time.sleep(1)
    raise RuntimeError("Nobody signed in within 15 minutes.")


def login_done(cid):
    """Remembers that the sign-in finished (the Connectors page shows it)."""
    with _lock:
        data = load()
        if cid in data:
            data[cid]["signed_in"] = time.time()
            _save(data)


def signout(cid):
    """Forgets the sign-in: the connector's saved credentials go."""
    s = spec(cid)
    lg = s.get("login") or {}
    login_cancel(cid)
    if lg.get("how") == "tool":
        shutil.rmtree(data_dir(cid), ignore_errors=True)
    elif lg.get("how") == "device" and available(cid):
        try:
            e = entry(cid)
            env = dict(os.environ, **(e.get("env") or {}))
            subprocess.run([e["command"]] + list(e.get("args") or []) + ["--logout"], env=env, timeout=120,
                           capture_output=True, stdin=subprocess.DEVNULL,
                           creationflags=hardware.NO_WINDOW if WINDOWS else 0)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    with _lock:
        data = load()
        if cid in data:
            data[cid].pop("signed_in", None)
            _save(data)
    _logins.pop(cid, None)


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
    err_reader = threading.Thread(target=pump_err, daemon=True)
    err_reader.start()
    end = time.time() + timeout

    def last_words():
        """What it wrote on stderr before it stopped. Its stdout can end before the stderr reader has caught
        up, so wait for the process and that reader first."""
        try:
            p.wait(2)
        except subprocess.TimeoutExpired:
            pass
        err_reader.join(2)
        return "".join(err[-8:]).strip()

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
                tail = last_words()
                raise RuntimeError("It stopped." + (f" It said: {tail[-500:]}" if tail else ""))
            try:
                msg = json.loads(line)
            except ValueError:
                continue                                    # some servers print notes on stdout
            if isinstance(msg, dict) and msg.get("id") == mid:
                if msg.get("error"):
                    e = msg["error"]
                    raise RuntimeError(f"It answered with an error: {e.get('message', e) if isinstance(e, dict) else e}")
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
        tail = last_words()
        raise RuntimeError("It stopped." + (f" It said: {tail[-500:]}" if tail else f" ({ex})")) from ex
    finally:
        try:
            p.stdin.close()
        except OSError:
            pass
        try:
            if WINDOWS:                                     # cmd /c npx ...: end node too, not only cmd.exe
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True,
                               creationflags=hardware.NO_WINDOW)
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
