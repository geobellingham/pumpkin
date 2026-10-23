"""The one web server: the UI, the /pumpkin/api control API, and a pass-through to llama-server."""
import gzip
import hmac
import html
import http.client
import ipaddress
import json
import mimetypes
import os
import secrets
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import (APP_NAME, __version__, agent, config, devices, discover, docs, doctor, hardware, llama, memory, models,
               remote, sessions, updates, workspace)

COOKIE = "pumpkin_key"
KILL_SW = """self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil((async () => {
  try { for (const k of await caches.keys()) await caches.delete(k); } catch (err) {}
  await self.registration.unregister();
})()));
"""
API = "/pumpkin/api/"
UPLOAD_LIMIT = 512 * 2 ** 20
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
        self.once = {}                      # single-use sign-in codes for the phone's QR: code -> expiry

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
                if not getattr(self, "own_csp", False):
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

    # files the browser may show itself; anything else (HTML, SVG, scripts) is only ever downloaded, because a
    # page the AI wrote must never run as Pumpkin
    INLINE = ("image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "audio/", "video/")

    def send_file(self, h, rel, inline=False):
        p = workspace.safe_path(self.core.settings, rel)
        if not os.path.isfile(p):
            raise FileNotFoundError("That file is not there any more.")
        name = os.path.basename(p)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        text = ctype.startswith("text/") or name.lower().endswith((".md", ".py", ".json", ".csv", ".log", ".yaml",
                                                                   ".yml", ".sh"))
        if inline and text and "html" not in ctype:
            ctype, disp = "text/plain; charset=utf-8", "inline"
        elif inline and ctype.startswith(self.INLINE):
            disp = "inline"
        else:
            disp = "attachment"
            if "html" in ctype or "svg" in ctype or "javascript" in ctype:
                ctype = "application/octet-stream"
        size = os.path.getsize(p)
        h.send_response(200)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(size))
        h.send_header("Content-Disposition", f"{disp}; filename*=UTF-8''{quote(name)}")
        h.own_csp = True
        if ctype != "application/pdf":                  # the browser's PDF viewer does not work in a sandbox
            h.send_header("Content-Security-Policy",
                          "sandbox; default-src 'none'; img-src 'self'; media-src 'self'; frame-ancestors 'self'")
        else:
            h.send_header("Content-Security-Policy", "frame-ancestors 'self'")
        h.send_header("Cache-Control", "no-store")
        h.end_headers()
        if h.command != "HEAD":
            with open(p, "rb") as f:
                shutil.copyfileobj(f, h.wfile, 1 << 20)

    def json(self, h, obj, code=200):
        self.send(h, code, json.dumps(obj, default=str), "application/json")

    def authed(self, h):
        """Signed in with this browser's own session (cookie), or with Pumpkin's key (the command line, the
        desktop window, and cookies from before sessions existed). Remembers which session asked."""
        h.session_id, h.old_cookie = None, False
        for part in (h.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k != COOKIE:
                continue
            s = sessions.check(v)
            if s:
                h.session_id = s["id"]
                return True
            if hmac.compare_digest(v, self.token):
                h.old_cookie = True
                return True
        auth = h.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            given = auth[7:].strip()
            if hmac.compare_digest(given, self.token):
                return True
            s = sessions.check(given)
            if s:
                h.session_id = s["id"]
                return True
        return False

    def new_session_cookie(self, h):
        ip = h.client_address[0] if h.client_address else ""
        local = ip in ("127.0.0.1", "::1")
        fwd = (h.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if local and fwd:              # Tailscale serve hands requests from the phone over on this computer
            ip, local = fwd, False
        key = sessions.create(h.headers.get("User-Agent") or "", ip, local=local)
        secure = "; Secure" if (h.headers.get("X-Forwarded-Proto") or "").lower() == "https" else ""
        return f"{COOKIE}={key}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000{secure}"

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
            if path == "/login" and q.get("once"):
                # the phone scanned a QR: a code that works once, for ten minutes; the real key never leaves
                code = (q.get("once") or [""])[0].strip()
                exp = self.once.pop(code, 0) if code else 0
                if exp < time.time():
                    time.sleep(1)
                    err = ('<p class="err">That code was already used or is too old. Make a new one on Pumpkin\'s '
                           'Phone page, or sign in with the key.</p>')
                    return self.send(h, 401, LOGIN_HTML.format(app=APP_NAME, err=err))
                return self.send(h, 302, b"", headers={"Location": "/", "Set-Cookie": self.new_session_cookie(h)})
            if path == "/login":
                key = (q.get("key") or [""])[0].strip()
                if not key or not hmac.compare_digest(key, self.token):
                    if key:
                        time.sleep(1)
                    err = '<p class="err">That key is not right.</p>' if key else ""
                    return self.send(h, 401 if key else 200, LOGIN_HTML.format(app=APP_NAME, err=err))
                # this browser gets its own session key; Pumpkin's key itself never goes into a cookie
                return self.send(h, 302, b"", headers={"Location": "/", "Set-Cookie": self.new_session_cookie(h)})
            if path.startswith("/pumpkin/peer/"):
                return self.peer(h, path[len("/pumpkin/peer/"):])
            if path in ("/sw.js", "/service-worker.js"):
                # an earlier version installed a service worker; this one removes itself and its caches, and from
                # then on every page comes straight from Pumpkin (no reload, so it can never loop)
                return self.send(h, 200, KILL_SW, "application/javascript; charset=utf-8",
                                 headers={"Service-Worker-Allowed": "/"})
            ui = self.ui_file(path)
            if ui and not ui.endswith("index.html"):
                return self.static(h, ui)               # open-source scripts and icons; data stays locked
            if not self.authed(h):
                if path.startswith(API) or "text/html" not in (h.headers.get("Accept") or ""):
                    return self.json(h, {"error": {"code": 401, "message": "Sign in to Pumpkin first."}}, 401)
                return self.send(h, 200, LOGIN_HTML.format(app=APP_NAME, err=""))
            if h.old_cookie and h.command == "GET" and "text/html" in (h.headers.get("Accept") or ""):
                # signed in before sessions existed: swap the cookie for a session of its own, same page
                return self.send(h, 302, b"", headers={"Location": h.path, "Set-Cookie": self.new_session_cookie(h)})
            if path.startswith(API):
                return self.api(h, path[len(API):].strip("/"), q)
            if ui:
                return self.static(h, ui)
            if path == "/" and not self.ui_dir():
                return self.send(h, 500, "<p>The Pumpkin web UI is missing. Reinstall Pumpkin.</p>")
            return self.proxy(h)
        except ConnectionError:                 # the browser left (Windows also says "aborted")
            pass

    # ------------------------------------------------------------ other Pumpkins asking this one
    def peer(self, h, name):
        """Only the device key opens these, and only while sharing is on. They can ask the model for an answer
        and read this device's name and models; nothing else."""
        core = self.core
        auth = h.headers.get("Authorization") or ""
        if not core.settings["share"]:
            return self.json(h, {"error": "Sharing is off on this device. Turn it on in Devices."}, 403)
        if not (auth.startswith("Bearer ") and devices.key_ok(auth[7:].strip())):
            time.sleep(1)
            return self.json(h, {"error": "Wrong device key. Pair this device again."}, 401)
        try:
            if name == "info" and h.command == "GET":
                return self.json(h, core.helper.info())
            if name == "chat" and h.command == "POST":
                # the other device's chat uses the model here: an OpenAI-style request, streamed back as it comes
                n = int(h.headers.get("Content-Length") or 0)
                if n > 30_000_000:
                    return self.json(h, {"error": "The conversation is too long."}, 413)
                body = json.loads(h.rfile.read(n).decode() or "{}") if n else {}
                msgs = body.get("messages")
                if not isinstance(msgs, list) or not msgs:
                    return self.json(h, {"error": "The chat has no messages."}, 400)
                last = next((m.get("content") for m in reversed(msgs) if isinstance(m, dict) and m.get("role") == "user"), "")
                entry = core.helper.begin(h.headers.get("X-Pumpkin-From") or "", f"Chat: {str(last)[:180]}")
                try:
                    body["model"] = core.helper.ready_model(body.get("model"))
                    self.to_llama(h, json.dumps(body).encode())
                    entry.update(state="done", model=body["model"])
                except Exception as e:
                    entry.update(state="error", error=str(e)[:200])
                    raise
                finally:
                    core.helper.end()
                return None
            if name == "ask" and h.command == "POST":
                n = int(h.headers.get("Content-Length") or 0)
                if n > 1_000_000:
                    return self.json(h, {"error": "The task is too long."}, 413)
                body = json.loads(h.rfile.read(n).decode() or "{}") if n else {}
                return self.json(h, core.helper.ask(body, who=h.headers.get("X-Pumpkin-From") or ""))
            return self.json(h, {"error": "Not found."}, 404)
        except devices.Busy as e:
            return self.json(h, {"error": str(e)}, 429)
        except ValueError as e:
            return self.json(h, {"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001 - always answer
            return self.json(h, {"error": str(e) or "Something went wrong."}, 500)

    # ------------------------------------------------------------ API
    def api(self, h, name, q):
        core = self.core
        method = h.command
        try:
            if name == "files/download" and method == "GET":
                return self.send_file(h, (q.get("path") or [""])[0], (q.get("inline") or [""])[0] == "1")
            if name == "files/upload" and method == "POST":
                if h.headers.get("X-Pumpkin") != "1":
                    raise ApiError("Missing X-Pumpkin header.", 403)
                n = int(h.headers.get("Content-Length") or 0)
                if n > UPLOAD_LIMIT:
                    raise ApiError(f"Files up to {UPLOAD_LIMIT // 2 ** 20} MB can be added here. Put bigger ones in "
                                   f"the folder directly.", 413)
                data = h.rfile.read(n) if n else b""
                rel = workspace.save_upload(core.settings, (q.get("dir") or [""])[0], (q.get("name") or [""])[0], data)
                if core.indexer:
                    core.indexer.poke()                  # searchable within seconds
                return self.json(h, {"path": rel})
            body = {}
            if method in ("POST", "PUT", "PATCH", "DELETE"):
                if h.headers.get("X-Pumpkin") != "1":
                    raise ApiError("Missing X-Pumpkin header.", 403)
                raw = _read_body(h)
                body = json.loads(raw.decode() or "{}") if raw else {}
            result = self.route(method, name, q, body, getattr(h, "session_id", None))
            return self.json(h, result if result is not None else {"ok": True})
        except ApiError as e:
            return self.json(h, {"error": str(e)}, e.code)
        except FileNotFoundError as e:
            return self.json(h, {"error": str(e) if str(e) and "No such" not in str(e) else "Not found."}, 404)
        except PermissionError as e:
            return self.json(h, {"error": str(e)}, 403)
        except (RuntimeError, ValueError, KeyError, OSError) as e:
            return self.json(h, {"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001 - always answer, never drop the connection
            return self.json(h, {"error": f"Something went wrong: {e}"}, 500)

    def route(self, method, name, q, body, session_id=None):  # noqa: C901 - one flat table is easier to follow
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
            if "doc_folders" in changes:
                dirs = []
                for d in changes["doc_folders"] or []:
                    d = os.path.expanduser(str(d).strip())
                    if not d:
                        continue
                    if not os.path.isdir(d):
                        raise ApiError(f"{d} is not a folder on this computer.")
                    dirs.append(d)
                changes["doc_folders"] = dirs
            core.settings.update(changes)
            if core.indexer and ({"doc_folders", "doc_index", "files_dir"} & set(changes)):
                core.indexer.poke()
            return core.settings.public()

        # ---- llama-server
        if head == "llama":
            if method == "GET" and not ident:
                binary = llama.find_server(core.settings)
                return {"path": binary, "info": llama.probe(binary), "candidates": llama.candidates(core.settings),
                        "backend": core.hardware()["backend"], "platform": "android" if hardware.ANDROID else sys.platform,
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

        # ---- pictures
        if head == "images":
            if method == "GET" and not ident:
                return core.images_state()
            if ident == "engine" and method == "POST":
                job = core.download_image_engine(body.get("backend"))
                return {"job": job["id"]}
            if ident == "install" and method == "POST":
                core.install_image(str(body.get("id") or ""))
                return core.images_state()
            if ident == "generate" and method == "POST":
                try:
                    r = core.make_image(body.get("prompt"), body.get("shape") or "square", body.get("seed"),
                                        swap_ok=body.get("swap", True) is not False)
                except (RuntimeError, ValueError) as e:
                    raise ApiError(str(e), 409)
                r.pop("jpeg", None)
                r["url"] = f"{API}files/download?path={quote(r['path'])}&inline=1"
                return r
            if ident == "stop" and method == "POST":
                core.image_server.stop()
                return core.images_state()
            if ident and method == "DELETE":
                core.delete_image(ident)
                return core.images_state()

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
                core.stop_if_using(path=body["path"])
                return {"removed": core.library.delete(body["path"])}
            if ident == "fit" and method == "GET":
                m = core.library.resolve(arg("path"))
                if not m:
                    raise ApiError("Unknown model.", 404)
                ctx = int(arg("ctx", "8192"))
                kv = arg("kv", "q8_0")
                f = models.fit(m["size_gb"], m.get("info") or {}, ctx, kv, core.fit_hw())
                f["text"] = models.fit_label(f)
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
                if head == "presets":
                    core.stop_if_using(preset_id=ident)
                    if core.settings["last_preset"] == ident:
                        core.settings.update({"last_preset": ""})
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

        # ---- the AI's work folder
        if head == "files":
            st = core.settings
            if method == "GET" and not ident:
                return workspace.listing(st, arg("path", ""))
            if ident == "recent":
                return {"items": workspace.recent(st)}
            if ident == "delete" and method == "POST":
                workspace.delete(st, body.get("path", ""))
                return {"ok": True}
            if ident == "reveal" and method == "POST":
                workspace.reveal(st, body.get("path", ""))
                return {"ok": True}

        # ---- health, remote access, app
        if head == "update":
            if method == "POST" and ident == "apply":
                updates.apply()
                return {"ok": True}
            if ident == "log":
                return {"log": updates.log_tail()}
            return updates.status(force=arg("fresh") == "1")
        if head == "doctor":
            return {"checks": doctor.check(core)}
        if head == "devices":
            if ident == "share":
                if method == "POST":
                    if "name" in body:
                        core.settings.update({"device_name": str(body["name"] or "").strip()[:60]})
                    if body.get("new_key"):
                        devices.new_peer_key()
                    if "on" in body:
                        listen = core.set_share(bool(body["on"]))
                        if listen:
                            threading.Timer(0.5, self.rebind, args=(listen,)).start()
                return core.share_status()
            if not ident:
                if method == "GET":
                    cur = core.server.preset if core.server.state == "ready" else None
                    return {"devices": core.refresh_peers(devices.statuses()), "model_has_tools": core.devices_loaded(),
                            "preset_tools": bool(cur.get("tools")) if cur else None,
                            "preset_id": cur.get("id") if cur else None}
                if method == "POST":
                    added = devices.add(body.get("code", ""))
                    threading.Thread(target=core.refresh_peers, daemon=True).start()   # its models show up in the chat
                    return added
            if ident and len(parts) > 2 and parts[2] == "test" and method == "POST":
                d = devices.find(ident)
                if not d:
                    raise ApiError("That device is not paired any more.", 404)
                return devices.ask(d["id"], "Reply with one short friendly sentence saying you are ready to help.",
                                   sender=devices.device_name(core.settings), timeout=900)
            if ident and method in ("PUT", "PATCH"):
                changed = devices.update(ident, body)
                threading.Thread(target=core.refresh_peers, daemon=True).start()
                return changed
            if ident and method == "DELETE":
                devices.remove(ident)
                threading.Thread(target=core.refresh_peers, daemon=True).start()
                return {"ok": True}
        if head == "remote":
            if method == "GET":
                return core.remote_status()
            if ident == "qr" and method == "POST":
                st = core.remote_status()
                if not st["base"]:
                    raise ApiError("Turn on phone access first: home network or Tailscale.")
                now = time.time()
                self.once = {c: e for c, e in self.once.items() if e > now}
                code = secrets.token_urlsafe(18)
                self.once[code] = now + 600
                return {"link": f"{st['base']}/login?once={code}", "expires": int(now + 600)}
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
        if head == "sessions":
            if method == "GET":
                return {"sessions": sessions.listing(session_id)}
            if ident == "clear" and method == "POST":
                sessions.clear(keep=session_id if body.get("keep_this", True) else None)
                return {"sessions": sessions.listing(session_id)}
            if ident and method == "DELETE":
                if not sessions.remove(ident):
                    raise ApiError("That sign-in is gone already.", 404)
                return {"sessions": sessions.listing(session_id)}
        # ---- what Pumpkin remembers about you
        if head == "memory":
            if method == "GET" and not ident:
                return {"enabled": bool(core.settings["memory"]), "items": memory.load()}
            if method == "POST" and ident == "clear":
                memory.clear()
                return {"items": []}
            if method == "POST" and not ident:
                return memory.add(body.get("text"), source="you")
            if method == "PUT" and ident:
                return memory.update(ident, body.get("text"))
            if method == "DELETE" and ident:
                memory.remove(ident)
                return {"ok": True}

        # ---- your documents, searchable by the model
        if head == "docs":
            if method == "GET" and not ident:
                return docs.status(core.settings)
            if method == "GET" and ident == "list":
                return {"items": docs.listing(core.settings, 500)}
            if method == "GET" and ident == "search":
                return {"hits": docs.search(core.settings, arg("q", ""), int(arg("n", "8")))}
            if method == "POST" and ident == "refresh":
                if core.indexer:
                    core.indexer.poke()
                return docs.status(core.settings)

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
            if h.command == "GET" and path in ("/v1/models", "/models") and self.core.server.state != "external" \
                    and (self.core.presets.all() or self.core.peer_models()):
                data = self.core.model_list()
                if data:
                    return self.json(h, {"object": "list", "data": data, "models": data})
            if h.command == "POST" and path in ("/models/load", "/models/unload") and self.core.server.state != "external" \
                    and (self.core.presets.all() or self.core.peer_models()):
                try:
                    ref = (json.loads(body.decode() or "{}") or {}).get("model")
                except (ValueError, AttributeError):
                    ref = None
                if self.core.find_peer_model(ref):
                    return self.json(h, {"success": True})      # it starts over there when the chat asks it
                preset = self.core.find_preset(ref)
                if preset is None:
                    return self.json(h, {"error": {"code": 404, "message": "That model is not one of your presets."}}, 404)
                if path == "/models/load":
                    threading.Thread(target=self._load_preset, args=(ref,), daemon=True).start()
                elif (self.core.server.preset or {}).get("id") == preset.get("id"):
                    self.core.server.stop()
                return self.json(h, {"success": True})
            if h.command == "POST" and body and path == "/tools" and self.core.server.state != "external":
                try:
                    call = json.loads(body.decode() or "{}")
                except (ValueError, UnicodeDecodeError):
                    call = {}
                if isinstance(call, dict) and call.get("tool") == "image_generate":
                    # Pumpkin draws the picture itself: it may stop llama-server to make room, which would cut off
                    # a call that went through it. The chat carries on once the text model is back.
                    params = call.get("params") if isinstance(call.get("params"), dict) else {}
                    return self.json(h, self.core.image_tool(params))
            if h.command == "POST" and body and path in self.CHAT_PATHS:
                try:
                    want = json.loads(body.decode() or "{}").get("model")
                except (ValueError, AttributeError):
                    want = None
                peer = self.core.find_peer_model(want) if want else None
                if peer:
                    return self.to_device(h, peer, body)
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
        self.relay(h, resp, conn)

    @staticmethod
    def relay(h, resp, conn=None):
        """Sends a response on to the browser as it arrives, so a streamed answer appears word by word."""
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
        try:
            if h.command == "HEAD":
                return
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                h.wfile.write(chunk)
                h.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError, http.client.HTTPException):
            pass                                   # the browser left, or the model stopped mid-reply
        finally:
            (conn or resp).close()

    def to_llama(self, h, body, path="/v1/chat/completions"):
        """A request straight to this device's llama-server, answered as it streams."""
        port = int(self.core.settings["llama_port"])
        fwd = {"Content-Type": "application/json", "Content-Length": str(len(body)), "Host": f"127.0.0.1:{port}"}
        fwd.update(llama.auth_header())
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3600)
        conn.request("POST", path, body=body, headers=fwd)
        self.relay(h, conn.getresponse(), conn)

    def to_device(self, h, peer, body):
        """The chat picked a model on a paired device: ask that device and stream its answer back here."""
        dev = devices.find(peer["device"])
        if not dev or not dev.get("enabled", True):
            return self.json(h, {"error": {"code": 404, "message": f"{peer['device_name']} is not paired any more."}}, 404)
        try:
            req = json.loads(body.decode() or "{}")
        except ValueError:
            return self.json(h, {"error": {"code": 400, "message": "The request is not valid JSON."}}, 400)
        req["model"] = peer["model"]
        dev["from"] = devices.device_name(self.core.settings)
        try:
            resp = devices.open_chat(dev, req)
        except (RuntimeError, ConnectionError) as e:
            return self.json(h, {"error": {"code": 502, "message": str(e), "type": "unavailable_error"}}, 502)
        self.relay(h, resp)


def escape(s):
    return html.escape(str(s))
