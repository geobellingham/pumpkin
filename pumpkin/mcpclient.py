"""A small MCP client: starts a connector (a program that speaks MCP over stdin and stdout), lists its tools and
calls them. Pumpkin Code sessions use it for the connectors a workspace has switched on."""
import json
import os
import queue
import subprocess
import threading
import time

from . import hardware

WINDOWS = os.name == "nt"
LINE_LIMIT = 16 * 2 ** 20                # one message from a connector; longer ones are dropped, not kept in memory


class McpError(RuntimeError):
    pass


class Client:
    def __init__(self, entry, name="connector"):
        self.entry = entry
        self.name = name
        self.proc = None
        self.tools = []
        self._next = 0
        self._waiting = {}                       # request id -> queue for the answer
        self._lock = threading.Lock()
        self._write = threading.Lock()
        self._err = []
        self.alive = False

    # ---- starting and stopping
    def start(self, timeout=240):
        env = dict(os.environ)
        env.update(self.entry.get("env") or {})
        try:
            self.proc = subprocess.Popen([self.entry["command"]] + list(self.entry.get("args") or []),
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         env=env, creationflags=hardware.NO_WINDOW if WINDOWS else 0)
        except OSError as e:
            raise McpError(f"{self.name} did not start: {e}") from e
        self.alive = True
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()
        try:
            self._request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "pumpkin", "version": "1"}}, timeout)
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            tools, cursor = [], None
            for _ in range(20):                  # a server may list its tools in pages
                r = self._request("tools/list", {"cursor": cursor} if cursor else {}, timeout)
                tools += [t for t in r.get("tools") or [] if isinstance(t, dict) and t.get("name")]
                cursor = r.get("nextCursor")
                if not cursor:
                    break
        except Exception:
            self.close()
            raise
        self.tools = tools
        return tools

    def close(self):
        self.alive = False
        p, self.proc = self.proc, None
        if p is None:
            return
        for q in list(self._waiting.values()):
            q.put(None)
        try:
            p.stdin.close()
        except OSError:
            pass
        try:
            if WINDOWS:                          # cmd /c npx ...: end node too, not only cmd.exe
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

    # ---- talking
    def _read_out(self):
        p = self.proc
        try:
            while True:
                line = p.stdout.readline(LINE_LIMIT)
                if not line:
                    break
                if len(line) >= LINE_LIMIT and not line.endswith(b"\n"):
                    while line and not line.endswith(b"\n"):    # too long to be an answer worth keeping: skip it
                        line = p.stdout.readline(LINE_LIMIT)
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue                     # some servers print notes on stdout
                if not isinstance(msg, dict):
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    q = self._waiting.get(msg["id"])
                    if q is not None:
                        q.put(msg)
                elif "id" in msg and msg.get("method") == "ping":
                    self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                elif "id" in msg and msg.get("method"):
                    self._send({"jsonrpc": "2.0", "id": msg["id"],
                                "error": {"code": -32601, "message": "Pumpkin does not support this request."}})
        except (OSError, ValueError):
            pass
        finally:
            self.alive = False
            for q in list(self._waiting.values()):
                q.put(None)

    def _read_err(self):
        try:
            for line in iter(lambda: self.proc.stderr.readline(4096), b""):
                self._err.append(line.decode("utf-8", "replace"))
                del self._err[:-40]
        except (OSError, ValueError):
            pass

    def last_words(self):
        return "".join(self._err[-8:]).strip()[-500:]

    def _send(self, msg):
        with self._write:
            try:
                self.proc.stdin.write((json.dumps(msg) + "\n").encode())
                self.proc.stdin.flush()
            except (OSError, AttributeError, ValueError) as e:
                raise McpError(f"{self.name} stopped." + (f" It said: {self.last_words()}" if self.last_words() else "")) from e

    def _request(self, method, params, timeout=120, cancelled=lambda: False):
        with self._lock:
            self._next += 1
            mid = self._next
        q = queue.Queue()
        self._waiting[mid] = q
        try:
            self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
            end = time.time() + timeout
            while True:
                if cancelled():
                    self._send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                "params": {"requestId": mid, "reason": "stopped by the user"}})
                    raise McpError("Stopped.")
                left = end - time.time()
                if left <= 0:
                    raise McpError(f"{self.name} did not answer within {timeout} seconds.")
                try:
                    msg = q.get(timeout=min(left, 0.5))
                except queue.Empty:
                    continue
                if msg is None:
                    raise McpError(f"{self.name} stopped." + (f" It said: {self.last_words()}" if self.last_words() else ""))
                if msg.get("error"):
                    e = msg["error"]
                    raise McpError(str(e.get("message", e)) if isinstance(e, dict) else str(e))
                return msg.get("result") or {}
        finally:
            self._waiting.pop(mid, None)

    def call(self, tool, args, timeout=120, cancelled=lambda: False):
        """Calls one tool. Returns (text, is_error)."""
        r = self._request("tools/call", {"name": tool, "arguments": args or {}}, timeout, cancelled)
        out = []
        for c in r.get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text":
                out.append(str(c.get("text") or ""))
            elif c.get("type") == "image":
                out.append("[an image: Pumpkin Code cannot show pictures from connectors]")
            elif c.get("type") == "resource":
                res = c.get("resource") or {}
                out.append(str(res.get("text") or res.get("uri") or "[a resource]"))
        if not out and r.get("structuredContent") is not None:
            out.append(json.dumps(r["structuredContent"], ensure_ascii=False))
        return "\n".join(out) or "(no output)", bool(r.get("isError"))
