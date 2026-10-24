"""Pumpkin's own tool servers run inside Pumpkin, for the Android app.

On a computer llama-server starts each tool server as `python -m pumpkin.mcp_files` and so on. The Android app has
no Python program to start (its Python lives inside the app), so llama-server starts a tiny relay instead
(libpumpkin_mcp.so, android/mcp_bridge.c). The relay connects here and passes stdin and stdout through, and the
same tools answer from this process. Only this device can connect (127.0.0.1), and only with the secret that
Pumpkin hands the relay in its environment."""
import hmac
import os
import secrets
import socket
import threading

from . import mcp_base

KEY_ENV = "PUMPKIN_MCP_KEY"


def _servers():
    """name -> (server name, tools, call, errors). Imported late: each pulls in its own parts of Pumpkin."""
    from . import mcp_devices, mcp_files, mcp_memory, mcp_skills, mcp_web
    return {
        "skills": ("pumpkin-skills", mcp_skills.TOOLS, mcp_skills.call, mcp_base.ERRORS),
        "web": ("pumpkin-web", mcp_web.TOOLS, mcp_web.call, mcp_web.ERRORS),
        "files": ("pumpkin-files", mcp_files.TOOLS, mcp_files.call, mcp_base.ERRORS),
        "memory": ("pumpkin-memory", mcp_memory.TOOLS, mcp_memory.call, mcp_base.ERRORS),
        "devices": ("pumpkin-devices", mcp_devices.TOOLS, mcp_devices.call, Exception),
    }


class Host:
    """Listens on 127.0.0.1 for the relay. The first line a relay sends is "<secret> <server>"."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.key = secrets.token_urlsafe(24)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def usable(self):
        return bool(self.bridge) and os.path.isfile(self.bridge)

    def command(self, name, timeout_ms=None):
        """The entry for llama-server's MCP file that reaches tool server `name` here."""
        out = {"command": self.bridge, "args": [str(self.port), name], "env": {KEY_ENV: self.key}}
        if timeout_ms:
            out["timeout_ms"] = timeout_ms
        return out

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return                                    # closed
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            conn.settimeout(10)                           # the relay says who it is at once
            rfile = conn.makefile("r", encoding="utf-8", newline="\n")
            wfile = conn.makefile("w", encoding="utf-8", newline="\n")
            try:
                first = rfile.readline(512).split()
            except (OSError, UnicodeDecodeError):
                return
            if len(first) != 2 or not hmac.compare_digest(first[0].encode(), self.key.encode()):
                return
            server = _servers().get(first[1])
            if not server:
                return
            conn.settimeout(None)                         # a chat can stay quiet for a long time
            name, tools, call, errors = server
            try:
                mcp_base.serve(name, tools, call, errors, inp=rfile, out=wfile)
            except (OSError, UnicodeDecodeError, ValueError):
                pass                                      # the relay went away

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
