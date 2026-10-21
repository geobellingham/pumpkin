"""The one web server: the UI, the /pumpkin/api control API, and a pass-through to llama-server."""
import gzip
import hmac
import html
import http.client
import ipaddress
import json
import mimetypes
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import APP_NAME, __version__, agent, config, discover, doctor, hardware, llama, models, remote, updates

COOKIE = "pumpkin_key"
API = "/pumpkin/api/"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade"}
PKG_UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
DATA_UI = os.path.join(config.DATA_DIR, "ui")

LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{app}</title>
<style>
:root{{color-scheme:light dark;--bg:#fff;--fg:#111;--mute:#666;--line:#ddd}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0d0d0d;--fg:#eee;--mute:#999;--line:#333}}}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);
font:16px/1.5 system-ui,-apple-system,sans-serif;padding:24px;box-sizing:border-box}}
main{{max-width:24rem;width:100%}}h1{{font-size:1.4rem;margin:0 0 .5rem}}p{{color:var(--mute);margin:0 0 1.25rem}}
form{{display:flex;gap:.5rem}}input{{flex:1;min-width:0;font:inherit;padding:.6rem .8rem;border-radius:.6rem;
border:1px solid var(--line);background:transparent;color:inherit}}
button{{font:inherit;padding:.6rem 1rem;border-radius:.6rem;border:0;background:var(--fg);color:var(--bg);cursor:pointer}}
.err{{color:#d33;margin-top:.75rem}}</style></head><body><main><h1>{app}</h1>
<p>Open the link that {app} shows under Phone access, or paste your access key. On the computer, run <code>pumpkin link</code> to print it.</p>
<form action="/login" method="get"><input name="key" placeholder="Access key" autocomplete="off" aria-label="Access key">
<button>Open</button></form>{err}</main></body></html>"""


def _read_body(h):
    if (h.headers.get("Transfer-Encoding") or "").lower() == "chunked":
        out = b""
        while True:
            size = int(h.rfile.readline().strip().split(b";")[0] or b"0", 16)
            if size == 0:
                h.rfile.readline()
                break
            out += h.rfile.read(size)
            h.rfile.readline()
        return out
    n = int(h.headers.get("Content-Length") or 0)
    return h.rfile.read(n) if n else b""


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


class Web:
    def __init__(self, core):
        self.core = core
        self.token = config.token()
        self.httpd = None
        self.host = None
        self.gz_cache = {}
        self.stopped = threading.Event()

    # ------------------------------------------------------------ lifecycle
    def ui_dir(self):
        for d in (DATA_UI, PKG_UI):
            if os.path.isfile(os.path.join(d, "index.html")):
                return os.path.realpath(d)
        return None

    def serve(self, host=None):
        host = host or ("0.0.0.0" if self.core.settings["remote"] == "lan" else "127.0.0.1")
        web = self

        class Handler(BaseHTTPRequestHandler):
            server_version = f"{APP_NAME}/{__version__}"
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def end_headers(self):
                # no other site may put Pumpkin in a frame, and links never leak the address (it can hold the key)
                self.send_header("X-Frame-Options", "SAMEORIGIN")
                self.send_header("Content-Security-Policy", "frame-ancestors 'self'")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                super().end_headers()

            def do_GET(self):
                web.handle(self)

            do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_GET

        self.httpd = ThreadingHTTPServer((host, int(self.core.settings["port"])), Handler)
        self.httpd.daemon_threads = True
        self.host = host
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self.httpd

    def rebind(self, host):
        """Switch between this-computer-only and home-network listening without restarting."""
        if host == self.host:
            return
        old = self.httpd
        old.shutdown()
        old.server_close()
        self.serve(host)

    def shutdown(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.stopped.set()

    # ------------------------------------------------------------ helpers
    def send(self, h, code, body=b"", ctype="text/html; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode()
        h.send_response(code)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.end_headers()
        if h.command != "HEAD":
            h.wfile.write(body)

    def json(self, h, obj, code=200):
        self.send(h, code, json.dumps(obj, default=str), "application/json")

    def authed(self, h):
        for part in (h.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and hmac.compare_digest(v, self.token):
                return True
        auth = h.headers.get("Authorization") or ""
        if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].strip(), self.token):
            return True
        return False

    def ui_file(self, path):
        d = self.ui_dir()
        if not d or ".." in path or "\x00" in path:
            return None
        rel = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        full = os.path.realpath(os.path.join(d, rel))
        if not full.startswith(d + os.sep) or not os.path.isfile(full):
            return None
        return full

    def static(self, h, full):
        d = self.ui_dir()
        rel = os.path.relpath(full, d)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        if rel.startswith("_app/immutable/"):
            cache = "public, max-age=31536000, immutable"
        elif rel in ("index.html", "sw.js", "manifest.webmanifest", "version.json", "build.json"):
            cache = "no-cache"
        else:
            cache = "public, max-age=3600"
        with open(full, "rb") as f:
            data = f.read()
        headers = {"Cache-Control": cache}
        body = data
        compressible = ctype.startswith("text/") or any(x in ctype for x in ("javascript", "json", "svg"))
        if compressible and len(data) > 1024 and "gzip" in (h.headers.get("Accept-Encoding") or ""):
            key = (full, os.path.getmtime(full))
            body = self.gz_cache.get(key)
            if body is None:
                body = self.gz_cache[key] = gzip.compress(data, 6)
            headers["Content-Encoding"] = "gzip"
            headers["Vary"] = "Accept-Encoding"
        h.send_response(200)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        for k, v in headers.items():
            h.send_header(k, v)
        h.end_headers()
        if h.command != "HEAD":
            h.wfile.write(body)

    # ------------------------------------------------------------ routing
    @staticmethod
    def host_ok(value):
        """Guards against DNS rebinding: a web page on some domain that suddenly points at 127.0.0.1.
        Pumpkin is only ever opened by IP address, localhost, a plain machine name, .local or Tailscale."""
        host = (value or "").strip().lower()
        if not host:
            return True
        if host.startswith("["):
            host = host[1:host.find("]")] if "]" in host else host
        elif host.count(":") == 1:
            host = host.split(":")[0]
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            pass
        extra = [x.strip().lower() for x in os.environ.get("PUMPKIN_ALLOWED_HOSTS", "").split(",") if x.strip()]
        return (host == "localhost" or host.endswith(".localhost") or "." not in host or host.endswith(".local")
                or host.endswith(".ts.net") or host in extra)

    def handle(self, h):
        try:
            if not self.host_ok(h.headers.get("Host")):
                return self.send(h, 421, "<p>Pumpkin does not answer to this address. Open it with "
                                         "http://127.0.0.1 or the link from <code>pumpkin link</code>.</p>")
            u = urlparse(h.path)
            path, q = u.path, parse_qs(u.query)
            if path == "/login":
                key = (q.get("key") or [""])[0].strip()
                if not key or not hmac.compare_digest(key, self.token):
                    if key:
                        time.sleep(1)
                    err = '<p class="err">That key is not right.</p>' if key else ""
                    return self.send(h, 401 if key else 200, LOGIN_HTML.format(app=APP_NAME, err=err))
                secure = "; Secure" if (h.headers.get("X-Forwarded-Proto") or "").lower() == "https" else ""
                return self.send(h, 302, b"", headers={
                    "Location": "/", "Set-Cookie": f"{COOKIE}={self.token}; Path=/; HttpOnly; SameSite=Lax; "
                                                   f"Max-Age=31536000{secure}"})
            ui = self.ui_file(path)
            if ui and not ui.endswith("index.html"):
                return self.static(h, ui)               # open-source scripts and icons; data stays locked
            if not self.authed(h):
                if path.startswith(API) or "text/html" not in (h.headers.get("Accept") or ""):
                    return self.json(h, {"error": {"code": 401, "message": "Sign in to Pumpkin first."}}, 401)
                return self.send(h, 200, LOGIN_HTML.format(app=APP_NAME, err=""))
            if path.startswith(API):
                return self.api(h, path[len(API):].strip("/"), q)
            if ui:
                return self.static(h, ui)
            if path == "/" and not self.ui_dir():
                return self.send(h, 500, "<p>The Pumpkin web UI is missing. Reinstall Pumpkin.</p>")
            return self.proxy(h)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------ API
    def api(self, h, name, q):
        core = self.core
        method = h.command
        try:
            body = {}
            if method in ("POST", "PUT", "PATCH", "DELETE"):
                if h.headers.get("X-Pumpkin") != "1":
                    raise ApiError("Missing X-Pumpkin header.", 403)
                raw = _read_body(h)
                body = json.loads(raw.decode() or "{}") if raw else {}
            result = self.route(method, name, q, body)
            return self.json(h, result if result is not None else {"ok": True})
        except ApiError as e:
            return self.json(h, {"error": str(e)}, e.code)
        except (RuntimeError, ValueError, KeyError, OSError) as e:
            return self.json(h, {"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001 - always answer, never drop the connection
            return self.json(h, {"error": f"Something went wrong: {e}"}, 500)

    def route(self, method, name, q, body):  # noqa: C901 - one flat table is easier to follow
        core = self.core
        arg = lambda k, d=None: (q.get(k) or [d])[0]
        parts = name.split("/")
        head = parts[0]
        ident = parts[1] if len(parts) > 1 else None

        if head == "state" and method == "GET":
            return core.state()
        if head == "hardware":
            return core.hardware(fresh=arg("fresh") == "1")
        if head == "settings":
            if method == "GET":
                s = core.settings.public()
                s["token_hint"] = self.token[:4] + "…"
                return s
            changes = {k: v for k, v in body.items() if k in config.DEFAULT_SETTINGS}
            if "hf_token" in changes and changes["hf_token"] == "set":
                changes.pop("hf_token")
            core.settings.update(changes)
            return core.settings.public()

        # ---- llama-server
        if head == "llama":
            if method == "GET" and not ident:
                binary = llama.find_server(core.settings)
                return {"path": binary, "info": llama.probe(binary), "candidates": llama.candidates(core.settings),
                        "backend": core.hardware()["backend"], "platform": sys.platform,
                        "log": "".join(core.server.log_tail(80))}
            if ident == "download" and method == "POST":
                return core.download_llama(body.get("backend") or core.hardware()["backend"])
            if ident == "start" and method == "POST":
                p = core.presets.get(body.get("preset"))
                if not p:
                    raise ApiError("Unknown preset.", 404)
                core.server.start(p)
                return core.server.status()
            if ident == "stop" and method == "POST":
                core.server.stop()
                return core.server.status()
            if ident == "log":
                return {"log": "".join(core.server.log_tail(int(arg("n", "200"))))}
        if head == "jobs":
            job = core.jobs.get(ident or "")
            if not job:
                raise ApiError("Unknown job.", 404)
            if method == "DELETE":
                job["cancel"] = True
            return {k: v for k, v in job.items()}

        # ---- models
        if head == "models":
            if method == "GET" and not ident:
                ctx = int(arg("ctx")) if arg("ctx") else None
                lib = models.annotate(core.library.scan(force=arg("rescan") == "1"), core.fit_hw(), ctx,
                                      arg("kv", "q8_0"))
                return {"models": lib, "dirs": core.library.dirs(), "hardware": core.fit_hw()}
            if ident == "discover" and method == "GET":
                return {"found": discover.find(core.settings)}
            if ident == "discover" and method == "POST":
                added = discover.add(core.settings, body.get("ids") or [])
                core.library.scan(force=True)
                return {"added": added}
            if ident == "delete" and method == "POST":
                return {"removed": core.library.delete(body["path"])}
            if ident == "fit" and method == "GET":
                m = core.library.resolve(arg("path"))
                if not m:
                    raise ApiError("Unknown model.", 404)
                ctx = int(arg("ctx", "8192"))
                kv = arg("kv", "q8_0")
                f = models.fit(m["size_gb"], m.get("info") or {}, ctx, kv, core.fit_hw())
                f["text"] = models.FIT_TEXT[f["level"]]
                return f
        if head == "recommend" and method == "GET":
            return core.recommendations(refresh=arg("refresh") == "1", goal=arg("goal"))
        if head == "setup" and ident == "goal" and method == "POST":
            return core.apply_goal(body.get("goal"))
        if head == "catalog":
            # ids can contain a slash and a colon (hf:org/repo), sent URL-encoded
            ident = unquote(name[len("catalog/"):]) if "/" in name else None
            if method == "GET" and ident:
                return core.catalog_plan(ident)
            if method == "POST" and ident:
                return core.install(ident, start=body.get("start", True))
        if head == "hf":
            tok = core.settings["hf_token"] or None
            if ident == "search":
                return {"results": models.hf_search(arg("q", ""), tok),
                        "suggestions": models.suggested_searches(core.fit_hw())}
            if ident == "suggestions":
                return {"suggestions": models.suggested_searches(core.fit_hw())}
            if ident == "files":
                return {"files": models.hf_files(arg("repo", ""), core.fit_hw(), tok)}
        if head == "downloads":
            if method == "GET":
                return {"jobs": core.downloads.list()}
            if method == "POST" and not ident:
                return core.downloads.add(body["repo"], body["files"], body.get("folder"))
            if ident == "cancel" and method == "POST":
                return {"ok": core.downloads.cancel(body["id"])}
            if ident == "clear" and method == "POST":
                core.downloads.clear_finished()
                return {"ok": True}

        # ---- presets, personas, prompts, tasks
        coll = {"presets": core.presets, "personas": core.personas, "prompts": core.prompts,
                "tasks": core.tasks}.get(head)
        if coll is not None:
            if head == "presets" and ident == "from-model" and method == "POST":
                return core.preset_from_model(body["path"], body.get("name"))
            if head == "tasks" and ident == "run" and method == "POST":
                t = core.tasks.get(body["id"])
                if not t:
                    raise ApiError("Unknown task.", 404)
                core.runner.start({"name": t["name"], "prompt": t["prompt"], "preset": t.get("preset"),
                                   "stop_after": t.get("stop_after"), "task_id": t["id"]})
                return {"ok": True}
            if method == "GET":
                return {"items": coll.all()}
            if method == "POST" and not ident:
                return coll.add(body)
            if method in ("PUT", "PATCH") and ident:
                it = coll.update(ident, body)
                if it is None:
                    raise ApiError("Not found.", 404)
                if head == "presets" and core.server.preset and core.server.preset.get("id") == ident:
                    it = dict(it, restart_needed=True)
                return it
            if method == "DELETE" and ident:
                if head == "personas" and len(coll.all()) <= 1:
                    raise ApiError("Keep at least one persona.")
                return {"ok": coll.delete(ident)}
            if ident == "move" and method == "POST":
                return {"ok": coll.move(body["id"], int(body["index"]))}
        if head == "persona" and method == "POST":
            p = core.personas.get(body.get("id") or body.get("name"))
            if not p:
                raise ApiError("Unknown persona.", 404)
            core.settings.update({"persona": p["name"]})
            return {"persona": p["name"], "restart_needed": core.server.state == "ready"}

        # ---- agent runs
        if head == "agent":
            r = core.runner
            if ident == "log" or (method == "GET" and not ident):
                since = int(arg("since", "0"))
                return {"busy": r.busy, "job": (r.current or {}).get("name"),
                        "lines": [x for x in r.log if x["n"] > since]}
            if ident == "run" and method == "POST":
                core.runner.start({"name": body.get("name") or "Prompt", "prompt": body["prompt"],
                                   "preset": body.get("preset"), "stop_after": False})
                return {"ok": True}
            if ident == "cancel" and method == "POST":
                r.stop()
                return {"ok": True}

        # ---- reports and benchmarks
        if head == "reports":
            folder = None
            if arg("kind") == "bench":
                folder = os.path.join(config.HOME, "llm-bench-results")
            if method == "GET" and not ident:
                if arg("name"):
                    return {"name": arg("name"), "text": core.reports.read(arg("name"), folder)}
                return {"items": core.reports.list(folder), "dir": folder or core.reports.dir}
            if method == "DELETE" or ident == "delete":
                core.reports.delete(body.get("name") or arg("name"), folder)
                return {"ok": True}
        if head == "bench":
            if method == "POST" and ident == "speed":
                core.run_bench()
                return core.bench_state
            return {"history": agent.speed_history(), **core.bench_state}

        # ---- MCP servers
        if head == "mcp":
            p = os.path.join(config.CONFIG_DIR, "mcp.json")
            if method == "GET":
                try:
                    with open(p) as f:
                        text = f.read()
                except OSError:
                    text = json.dumps({"mcpServers": {}}, indent=2)
                return {"text": text}
            text = body.get("text", "")
            j = json.loads(text)
            if not isinstance(j.get("mcpServers", {}), dict):
                raise ApiError("mcpServers must be an object of named servers.")
            with open(p, "w") as f:
                f.write(text)
            return {"ok": True, "restart_needed": core.server.state == "ready"}

        # ---- health, remote access, app
        if head == "update":
            if method == "POST" and ident == "apply":
                updates.apply()
                return {"ok": True}
            return updates.status(force=arg("fresh") == "1")
        if head == "doctor":
            return {"checks": doctor.check(core)}
        if head == "remote":
            if method == "GET":
                return remote.status(core.settings, self.token)
            mode = body.get("mode", "off")
            if mode not in ("off", "tailscale", "lan"):
                raise ApiError("Unknown mode.")
            st = core.set_remote(mode)
            threading.Timer(0.5, self.rebind, args=("0.0.0.0" if mode == "lan" else "127.0.0.1",)).start()
            return st
        if head == "service":
            from . import service
            if method == "POST" and ident == "install":
                # this process keeps serving; the service takes over at the next login
                service.enable(start_now=service.running_under_service())
            elif method == "POST" and ident == "uninstall":
                service.disable()
            return service.status()
        if head == "token" and ident == "reset" and method == "POST":
            self.token = config.reset_token()
            return {"ok": True}
        if head == "quit" and method == "POST":
            threading.Timer(0.3, lambda: (core.shutdown(), self.shutdown())).start()
            return {"ok": True}
        raise ApiError(f"Unknown endpoint {method} {name}", 404)

    # ------------------------------------------------------------ llama-server pass-through
    CHAT_PATHS = ("/v1/chat/completions", "/chat/completions", "/v1/completions", "/completion", "/v1/messages")

    def _load_preset(self, ref):
        try:
            self.core.ensure_preset(ref)
        except RuntimeError:
            pass                                   # the status chip and Doctor show why it failed

    def proxy(self, h):
        port = int(self.core.settings["llama_port"])
        try:
            body = _read_body(h)
            path = urlparse(h.path).path
            if h.command == "GET" and path in ("/v1/models", "/models") and self.core.presets.all() \
                    and self.core.server.state != "external":
                data = self.core.model_list()
                if data:
                    return self.json(h, {"object": "list", "data": data, "models": data})
            if h.command == "POST" and path in ("/models/load", "/models/unload") and self.core.presets.all() \
                    and self.core.server.state != "external":
                try:
                    ref = (json.loads(body.decode() or "{}") or {}).get("model")
                except (ValueError, AttributeError):
                    ref = None
                preset = self.core.find_preset(ref)
                if preset is None:
                    return self.json(h, {"error": {"code": 404, "message": "That model is not one of your presets."}}, 404)
                if path == "/models/load":
                    threading.Thread(target=self._load_preset, args=(ref,), daemon=True).start()
                elif (self.core.server.preset or {}).get("id") == preset.get("id"):
                    self.core.server.stop()
                return self.json(h, {"success": True})
            if h.command == "POST" and body and path in self.CHAT_PATHS:
                try:
                    want = json.loads(body.decode() or "{}").get("model")
                except (ValueError, AttributeError):
                    want = None
                if want:
                    try:
                        self.core.ensure_preset(want)
                    except RuntimeError as e:
                        return self.json(h, {"error": {"code": 503, "message": str(e), "type": "unavailable_error"}}, 503)
            fwd = {}
            for k, v in h.headers.items():
                kl = k.lower()
                if kl in HOP or kl in ("host", "cookie", "content-length", "origin", "referer", "authorization"):
                    continue
                fwd[k] = v
            if body:
                fwd["Content-Length"] = str(len(body))
            fwd["Host"] = f"127.0.0.1:{port}"
            fwd.update(llama.auth_header())          # llama-server only answers Pumpkin
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3600)
            conn.request(h.command, h.path, body=body or None, headers=fwd)
            resp = conn.getresponse()
        except (ConnectionRefusedError, OSError):
            st = self.core.server.state
            msg = ("The model is still loading." if st == "loading" else
                   "No model is running. Start one from Pumpkin (tap the status chip, top right).")
            # the chat UI shows "Loading model" for 503, and an error with this message otherwise
            code = 503 if st == "loading" else 502
            return self.json(h, {"error": {"code": code, "message": msg, "type": "unavailable_error"}}, code)
        h.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() in HOP or k.lower() in ("server", "date", "content-length"):
                continue
            h.send_header(k, v)
        cl = resp.getheader("Content-Length")
        if cl and not resp.getheader("Transfer-Encoding"):
            h.send_header("Content-Length", cl)
        h.send_header("Connection", "close")
        h.end_headers()
        h.close_connection = True
        if h.command == "HEAD":
            conn.close()
            return
        try:
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                h.wfile.write(chunk)
                h.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, http.client.HTTPException):
            pass                                   # the browser left, or the model stopped mid-reply
        finally:
            conn.close()


def escape(s):
    return html.escape(str(s))
