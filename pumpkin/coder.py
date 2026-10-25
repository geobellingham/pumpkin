"""Pumpkin Code: a coding agent that works in a project folder with your local model.

It reads the code, changes files, runs commands and tests, and keeps going until the job is done, the way Claude Code
does. Changes to files and commands wait for the user's yes unless they chose otherwise, every change can be undone,
and long sessions are summarised so they fit the model's context. The Code page and `pumpkin code` in a terminal
both drive the same sessions through Pumpkin's API."""
import difflib
import fnmatch
import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

from . import config, hardware, llama, skills

WINDOWS = os.name == "nt"
MODES = ("ask", "edits", "auto")        # ask before changes / change files freely, ask before commands / never ask
READ_ONLY = {"read", "ls", "glob", "grep", "todo", "web_search", "read_webpage"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".mypy_cache", ".pytest_cache",
             ".ruff_cache", "dist", "build", ".next", ".nuxt", "target", ".idea", ".vscode", ".gradle", ".svelte-kit",
             "coverage", ".tox", ".cache", "Pods", "DerivedData"}
INSTRUCTION_FILES = ("AGENTS.md", "PUMPKIN.md", "CLAUDE.md", ".github/copilot-instructions.md")
# asked about even when the user said never to ask: these are hard to take back
DANGER = re.compile(r"\bsudo\b|\bsu\s+-|\brm\s+(-\w*\s+)*-\w*[rR]\w*\s+(/|~|\$HOME|\*)(\s|$)|\bgit\s+push\b|"
                    r"\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-\w*f|\bmkfs|\bdd\s+if=|\bshutdown\b|\breboot\b|"
                    r":\(\)\s*\{|\bformat\s+[a-z]:|Remove-Item\b.*-Recurse|\bdel\s+/[sq]|\bcurl\b[^|]*\|\s*(sudo\s+)?(ba|z)?sh\b")
_RM = re.compile(r"\brm\s+([^;&|\n]*)")
_RM_TARGET = re.compile(r"(/|~|\$HOME|\$\{HOME\})/?\*?|\*|/[\w.-]+/?")
# a command after one that was always allowed (npm test && rm -rf src) is asked about again
_CHAINED = re.compile(r"[;&|`<>\n]|\$\(")


def dangerous(command):
    """Hard to take back: DANGER, or rm with a recursive flag anywhere aimed at /, a top folder, ~ or $HOME
    (DANGER alone misses rm -r -f /, rm -rf ~/ and rm --recursive --force /home)."""
    command = str(command or "")
    if DANGER.search(command):
        return True
    for m in _RM.finditer(command):
        args = m.group(1).split()
        recursive = any(a == "--recursive" or re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*", a) for a in args)
        if recursive and any(_RM_TARGET.fullmatch(a.strip("'\"")) for a in args if not a.startswith("-")):
            return True
    return False


MAX_READ_LINES = 2000
MAX_EVENTS = 600


def _sessions_dir():
    return os.path.join(config.DATA_DIR, "code")


def find_shell():
    """The shell commands run in: bash where there is one (Git Bash on Windows), else PowerShell on Windows."""
    if not WINDOWS:
        return ["/bin/bash", "-c"] if os.path.exists("/bin/bash") else ["/bin/sh", "-c"], "bash"
    for c in (shutil.which("bash.exe"), r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if c and os.path.isfile(c) and "system32" not in c.lower():      # System32\bash.exe is WSL, another computer
            return [c, "-c"], "bash (Git Bash)"
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command"], "PowerShell"


def _is_text(data):
    return b"\x00" not in data[:8192]


def unified(path, old, new, limit=400):
    """A unified diff of one file, cut to limit lines."""
    lines = list(difflib.unified_diff((old or "").splitlines(), (new or "").splitlines(), "a/" + path, "b/" + path,
                                      lineterm="", n=2))
    if len(lines) > limit:
        lines = lines[:limit] + [f"... {len(lines) - limit} more lines"]
    return "\n".join(lines)


def _tool(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": list(required)}}}


class Tools:
    """What the model can do in the project folder. It cannot reach outside it."""

    def __init__(self, root, web=True):
        self.root = os.path.realpath(root)
        self.shell, self.shell_name = find_shell()
        self.web = web
        self.skills = lambda: []                 # the skills this session may use (set by the session)

    def definitions(self):
        s = {"type": "string"}
        n = {"type": "integer"}
        defs = [
            _tool("ls", "List a folder of the project (default: the top).", {"path": s}),
            _tool("glob", "Find files by name pattern, like **/*.py or src/**/test_*.ts. Newest first.",
                  {"pattern": s}, ["pattern"]),
            _tool("grep", "Search file contents with a regular expression. Answers file:line: text.",
                  {"pattern": s, "path": {"type": "string", "description": "folder or file (default: everything)"},
                   "glob": {"type": "string", "description": "only files like this, e.g. *.py"},
                   "ignore_case": {"type": "boolean"}}, ["pattern"]),
            _tool("read", "Read a file with line numbers. Use offset and limit for parts of long files.",
                  {"path": s, "offset": n, "limit": n}, ["path"]),
            _tool("edit", "Change a file: replace old (exact text, including spaces and indentation, unique in the "
                          "file) with new. Read the file first. Set all to replace every occurrence.",
                  {"path": s, "old": s, "new": s, "all": {"type": "boolean"}}, ["path", "old", "new"]),
            _tool("write", "Create a file, or replace a whole file, with content. Prefer edit for changes. Set append "
                           "to true to add content to the end of a file that exists.",
                  {"path": s, "content": s, "append": {"type": "boolean"}}, ["path", "content"]),
            _tool("bash", f"Run a command in the project folder with {self.shell_name} and get its output: build, "
                          f"test, git, install. Not for long-running servers. Timeout in seconds (default 120).",
                  {"command": s, "timeout": n}, ["command"]),
            _tool("todo", "Keep the list of steps for this job. Send the whole list each time with done marked.",
                  {"items": {"type": "array", "items": {"type": "object", "properties": {
                      "text": s, "done": {"type": "boolean"}}, "required": ["text"]}}}, ["items"]),
        ]
        if self.web:
            defs += [_tool("web_search", "Search the web, for documentation or an error message.", {"query": s},
                           ["query"]),
                     _tool("read_webpage", "Get a web page's text.", {"url": s, "start": n}, ["url"])]
        if self.skills():
            defs.append(_tool("skill", "Read one of your skills (instructions for a kind of job) by name, or a file it "
                                       "brings along with path.", {"name": s, "path": s}, ["name"]))
        return defs

    # ---- paths
    def path(self, p, must_exist=False):
        p = str(p or ".").strip()
        full = os.path.realpath(os.path.join(self.root, os.path.expanduser(p)))
        if os.path.commonpath([self.root, full]) != self.root:
            raise PermissionError(f"{p} is outside the project folder. Only files in {self.root} can be used.")
        if must_exist and not os.path.exists(full):
            raise FileNotFoundError(f"{p} does not exist. Use ls or glob to find the right path.")
        return full

    def rel(self, full):
        r = os.path.relpath(full, self.root)
        return r.replace(os.sep, "/") if r != "." else "."

    def _walk(self, top):
        for d, dirs, files in os.walk(top):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.endswith(".egg-info"))
            for f in files:
                yield os.path.join(d, f)

    # ---- the tools
    def ls(self, path="."):
        full = self.path(path, True)
        if os.path.isfile(full):
            return f"{self.rel(full)} is a file ({os.path.getsize(full)} bytes)."
        out = []
        for n in sorted(os.listdir(full), key=str.lower):
            fp = os.path.join(full, n)
            if os.path.isdir(fp):
                out.append(n + "/" + ("  (skipped in searches)" if n in SKIP_DIRS else ""))
            else:
                try:
                    out.append(f"{n}  {os.path.getsize(fp)} B")
                except OSError:
                    out.append(f"{n}  (broken link)")
        return "\n".join(out[:500]) + (f"\n... {len(out) - 500} more" if len(out) > 500 else "") if out else "(empty)"

    def glob(self, pattern):
        pattern = re.sub(r"^(\./)+", "", str(pattern or "").strip())     # ./src/*.py, but keep .github/*
        if not pattern:
            raise ValueError("Give a pattern, like **/*.py")
        rx = _glob_rx(pattern)
        hits = []
        for fp in self._walk(self.root):
            r = self.rel(fp)
            if rx.match(r) or ("/" not in pattern and fnmatch.fnmatch(os.path.basename(r), pattern)):
                try:
                    hits.append((os.path.getmtime(fp), r))
                except OSError:
                    pass
        hits.sort(reverse=True)
        if not hits:
            return "No files match."
        return "\n".join(r for _, r in hits[:200]) + (f"\n... {len(hits) - 200} more" if len(hits) > 200 else "")

    def grep(self, pattern, path=".", glob=None, ignore_case=False):
        full = self.path(path or ".", True)
        try:
            rx = re.compile(str(pattern), re.I if ignore_case else 0)
        except re.error as e:
            raise ValueError(f"That is not a valid regular expression: {e}")
        files = [full] if os.path.isfile(full) else self._walk(full)
        out, total = [], 0
        for fp in files:
            if glob and not fnmatch.fnmatch(os.path.basename(fp), glob) and not fnmatch.fnmatch(self.rel(fp), glob):
                continue
            try:
                if os.path.getsize(fp) > 4 * 2 ** 20:
                    continue
                with open(fp, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            if not _is_text(data):
                continue
            for i, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                if rx.search(line):
                    total += 1
                    if len(out) < 200:
                        out.append(f"{self.rel(fp)}:{i}: {line.strip()[:240]}")
        if not out:
            return "No matches."
        return "\n".join(out) + (f"\n... {total - len(out)} more matches" if total > len(out) else "")

    def read(self, path, offset=1, limit=None):
        full = self.path(path, True)
        if os.path.isdir(full):
            return self.ls(path)
        with open(full, "rb") as f:
            data = f.read(20 * 2 ** 20)
        if not _is_text(data):
            return f"{self.rel(full)} is a binary file ({os.path.getsize(full)} bytes)."
        lines = data.decode("utf-8", "replace").splitlines()
        try:
            start = max(1, int(offset or 1))
        except (TypeError, ValueError):
            start = 1
        try:
            count = min(MAX_READ_LINES, int(limit)) if limit else MAX_READ_LINES
        except (TypeError, ValueError):
            count = MAX_READ_LINES
        part = lines[start - 1:start - 1 + count]
        if not lines:
            return f"{self.rel(full)} is empty."
        body = "\n".join(f"{n:>5}\t{l[:2000]}" for n, l in enumerate(part, start))
        end = start - 1 + len(part)
        if end < len(lines):
            body += f"\n... lines {end + 1}-{len(lines)} not shown (read with offset={end + 1})"
        return body

    def file_text(self, full):
        if not os.path.exists(full):
            return None
        with open(full, "rb") as f:
            data = f.read()
        if not _is_text(data):
            raise ValueError(f"{self.rel(full)} is a binary file. It cannot be edited as text.")
        return data.decode("utf-8")

    def plan_edit(self, path, old, new, all_=False):
        """(full path, old text, new text) for an edit, or a ValueError that tells the model what went wrong."""
        full = self.path(path, True)
        text = self.file_text(full)
        old, new = str(old or ""), str(new if new is not None else "")
        if not old:
            raise ValueError("old is empty. To create a file use write; to add text, include the line it goes next to.")
        if old == new:
            raise ValueError("old and new are the same: nothing would change.")
        crlf = "\r\n" in text
        if crlf and "\r\n" not in old:
            old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
        count = text.count(old)
        if count == 0:
            raise ValueError("old was not found in " + self.rel(full) + ". It must match the file exactly." +
                             _closest(text, old))
        if count > 1 and not all_:
            raise ValueError(f"old appears {count} times in {self.rel(full)}. Add more surrounding lines so it is "
                             f"unique, or set all to replace every one.")
        return full, text, text.replace(old, new) if all_ else text.replace(old, new, 1)

    def plan_write(self, path, content, append=False):
        full = self.path(path)
        if os.path.isdir(full):
            raise ValueError(f"{path} is a folder.")
        old = self.file_text(full) if os.path.exists(full) else None
        text = str(content if content is not None else "")
        if append and old is not None:
            text = old + ("" if old.endswith("\n") or not old else "\n") + text
        return full, old, text

    def save(self, full, text):
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def bash(self, command, timeout=120, cancelled=lambda: False):
        try:
            timeout = max(1, min(int(timeout or 120), 1800))
        except (TypeError, ValueError):
            timeout = 120
        env = dict(os.environ)
        env.update(PAGER="cat", GIT_PAGER="cat", GIT_TERMINAL_PROMPT="0", CI="1", TERM="dumb", PYTHONUNBUFFERED="1",
                   NO_COLOR="1")
        kw = {"creationflags": hardware.NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS \
            else {"start_new_session": True}
        t0 = time.time()
        p = subprocess.Popen(self.shell + [str(command)], cwd=self.root, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, **kw)
        chunks = []

        def read():
            # piece by piece, so what came is kept even when a background child holds the pipe open
            try:
                while True:
                    b = p.stdout.read1(65536)
                    if not b:
                        break
                    chunks.append(b)
            except (OSError, ValueError):
                pass
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        why = None
        while p.poll() is None:
            if cancelled():
                why = "stopped by the user"
            elif time.time() - t0 > timeout:
                why = f"timed out after {timeout} s"
            if why:
                _kill_tree(p)
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    pass
                break
            time.sleep(0.1)
        reader.join(5)
        if not reader.is_alive():
            p.stdout.close()                     # else the reader still uses it; it closes with the process
        out = b"".join(list(chunks)).decode("utf-8", "replace").replace("\r\n", "\n")
        out = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out)
        if len(out) > 30000:
            out = out[:12000] + f"\n\n... {len(out) - 24000} characters cut ...\n\n" + out[-12000:]
        code = p.returncode
        tail = f"\n[{why}]" if why else f"\n[exit code {code}, {time.time() - t0:.1f} s]"
        return (out.rstrip() or "(no output)") + tail, (code == 0 and not why)


def _kill_tree(p):
    try:
        if WINDOWS:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True,
                           creationflags=hardware.NO_WINDOW)
        else:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def _glob_rx(pattern):
    """A glob with ** (any folders) as a regular expression over paths with /."""
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
            continue
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
            continue
        out += "[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c)
        i += 1
    return re.compile(out + "$")


def _closest(text, old):
    """A hint for a failed edit: the part of the file that looks most like what the model sent."""
    want = [l.strip() for l in old.strip().splitlines() if l.strip()]
    if not want:
        return ""
    lines = text.splitlines()
    best, at = 0.0, -1
    for i in range(len(lines)):
        if difflib.SequenceMatcher(None, lines[i].strip(), want[0]).quick_ratio() < 0.6:
            continue
        got = "\n".join(l.strip() for l in lines[i:i + len(want)])
        r = difflib.SequenceMatcher(None, got, "\n".join(want)).ratio()
        if r > best:
            best, at = r, i
    if at < 0 or best < 0.6:
        return " Read the file again and copy the text exactly."
    snippet = "\n".join(f"{n:>5}\t{l}" for n, l in enumerate(lines[at:at + len(want) + 1], at + 1))
    return f" The closest text is at line {at + 1} (read it and copy it exactly, spaces included):\n{snippet}"


# ---------------------------------------------------------------- a session
class Session:
    WATCH_EVERY = 10          # seconds between looks at the model server while waiting for it
    HUNG_AFTER = 30           # seconds with nothing received before the server's own word is asked

    def __init__(self, manager, root, mode="ask", preset=None, sid=None):
        self.m = manager
        self.id = sid or config.new_id()
        self.root = os.path.realpath(os.path.expanduser(root))
        self.mode = mode if mode in MODES else "ask"
        self.preset = preset
        self.title = ""
        self.messages = []
        self.events = []
        self.todo = []
        self.undo = []                       # [(path, text before or None, what)] newest last
        self.allowed = []                    # command prefixes the user said yes to for the whole session
        self.allow_edits = False
        self.state, self.error = "idle", None
        self.pending = None                  # the question waiting for the user
        self.live = None                     # the answer being written: {"text", "thinking"}
        self.cancel = False
        self.created = self.updated = time.time()
        self.tokens = 0
        self._sock = None                    # the connection to the model while it answers (Stop cuts it)
        self.phase = {"kind": "idle", "label": "", "since": time.time()}   # what the model is doing right now
        self.streamed = 0                    # pieces of the answer written in the current step
        self._answer = threading.Event()
        self._lock = threading.Lock()
        self._save_lock = threading.Lock()   # the session thread and the page both save
        self._send_lock = threading.Lock()   # two quick sends start one run, not two
        self.deleted = False
        self.tools = Tools(self.root, web=not hardware.ANDROID)
        self.tools.skills = lambda: self.m.skills_for(self.root)

    # ---- state for the UI
    def emit(self, kind, **data):
        with self._lock:
            n = (self.events[-1]["n"] + 1) if self.events else 1
            ev = {"n": n, "kind": kind, "t": time.time(), **data}
            self.events.append(ev)
            if len(self.events) > MAX_EVENTS:
                del self.events[:len(self.events) - MAX_EVENTS]
        self.updated = time.time()
        return ev

    def set_phase(self, kind, label=""):
        """What the model is doing right now: reading, thinking, writing, calling, tool, waiting, summarising."""
        if self.phase.get("kind") != kind or self.phase.get("label") != label:
            self.phase = {"kind": kind, "label": label, "since": time.time()}
        if kind in ("reading", "idle"):
            self.streamed = 0

    def summary(self):
        return {"id": self.id, "root": self.root, "title": self.title or os.path.basename(self.root), "mode": self.mode,
                "state": self.state, "updated": self.updated, "created": self.created, "preset": self.preset,
                "phase": self.phase if self.state in ("running", "waiting") else None}

    def view(self, after=0):
        return {**self.summary(), "events": [e for e in self.events if e["n"] > after], "live": self.live,
                "pending": self.pending, "todo": self.todo, "error": self.error, "undo": len(self.undo),
                "tokens": self.tokens, "ctx": self.m.ctx(), "shell": self.tools.shell_name,
                "streamed": self.streamed, "now": time.time(),
                "decoded": self.m.core.decode_progress() if self.state == "running" else None,
                "last": self.events[-1]["n"] if self.events else 0}

    # ---- saving
    def to_json(self):
        undo = [u for u in self.undo[-50:] if u[1] is None or len(u[1]) < 2 * 2 ** 20]
        return {"id": self.id, "root": self.root, "mode": self.mode, "preset": self.preset, "title": self.title,
                "messages": self.messages, "events": self.events[-MAX_EVENTS:], "todo": self.todo, "undo": undo,
                "allowed": self.allowed, "allow_edits": self.allow_edits, "created": self.created,
                "updated": self.updated, "tokens": self.tokens}

    def save(self):
        with self._save_lock:
            if self.deleted:
                return                           # deleted while it ran: its last save must not bring it back
            os.makedirs(_sessions_dir(), exist_ok=True)
            path = os.path.join(_sessions_dir(), self.id + ".json")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.to_json(), f)
            os.replace(tmp, path)

    @classmethod
    def load(cls, manager, data):
        s = cls(manager, data["root"], data.get("mode", "ask"), data.get("preset"), data["id"])
        for k in ("title", "messages", "events", "todo", "allowed", "allow_edits", "created", "updated", "tokens"):
            if k in data:
                setattr(s, k, data[k])
        s.undo = [tuple(u) for u in data.get("undo") or []]
        return s

    # ---- talking to it
    def send(self, text):
        text = str(text or "").strip()
        if not text:
            raise ValueError("Say what to do.")
        with self._send_lock:
            if self.state in ("running", "waiting"):
                raise RuntimeError("It is still working. Wait, or stop it first.")
            if not os.path.isdir(self.root):
                raise FileNotFoundError(f"The project folder {self.root} is not there any more.")
            if not self.title:
                self.title = re.sub(r"\s+", " ", text)[:70]
            if not self.messages:
                self.messages.append({"role": "system", "content": self.m.system_prompt(self)})
            self.messages.append({"role": "user", "content": text})
            self.emit("user", text=text)
            self.state, self.error, self.cancel = "running", None, False
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.cancel = True
        sock = getattr(self, "_sock", None)
        if sock is not None:
            # the model may be reading a long prompt and sending nothing yet: cut the connection so it stops now
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.pending:
            self.answer(self.pending["id"], "no", "Stopped by the user.")

    def answer(self, qid, allow, note=""):
        p = self.pending
        if not p or p["id"] != qid:
            raise ValueError("That question was already answered.")
        p["answer"], p["note"] = allow, str(note or "")[:2000]
        self._answer.set()

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError("The mode is ask, edits or auto.")
        self.mode = mode
        self.emit("info", text={"ask": "It asks before changing files or running commands.",
                                "edits": "It changes files without asking and asks before running commands.",
                                "auto": "It works without asking (it still asks before risky commands)."}[mode])
        self.save()

    def undo_last(self):
        if self.state in ("running", "waiting"):
            raise RuntimeError("Stop it before undoing.")
        if not self.undo:
            raise ValueError("Nothing to undo.")
        path, before, what = self.undo.pop()
        full = self.tools.path(path)
        if before is None:
            if os.path.isfile(full):
                os.remove(full)
        else:
            self.tools.save(full, before)
        self.emit("info", text=f"Undid: {what}")
        note = f"(The user undid your change: {what}. The file is back as it was.)"
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages[-1]["content"] += "\n" + note
        else:
            self.messages.append({"role": "user", "content": note})
            self.messages.append({"role": "assistant", "content": "Understood."})
        self.save()
        return path

    # ---- the loop
    def _run(self):
        try:
            steps = int(self.m.settings_get("code_max_steps", 80))
            cut = hang = 0
            for _ in range(steps):
                if self.cancel:
                    self.emit("info", text="Stopped.")
                    break
                self._compact_if_needed()
                try:
                    msg = self._complete(tools=True)
                    cut = hang = 0
                    if self.cancel:
                        self.emit("info", text="Stopped.")
                        break
                except _Hung:
                    hang += 1
                    if hang > 2:
                        raise RuntimeError("The model keeps going quiet in the middle of an answer. Stop it and start "
                                           "it again from Presets, or check the model's log on Settings.")
                    self.emit("info", text="The model stopped answering, so Pumpkin asked it again.")
                    continue
                except _CutOff as e:
                    cut += 1
                    if cut > 3:
                        raise RuntimeError("The model keeps running out of room while it writes. A bigger context for this "
                                           "model (Presets) gives it more room.") from e
                    self.emit("info", text="The model ran out of room in the middle of writing a file. It is trying "
                                           "again, writing the file in parts.")
                    self.messages.append({"role": "user", "content": "(Your last answer was cut off because it was "
                                          "too long, so nothing was written. Send the file in parts: the first part "
                                          "with write, then each next part with write and append set to true.)"})
                    continue
                calls = msg.get("tool_calls") or []
                entry = {"role": "assistant", "content": msg.get("content") or ""}
                if calls:
                    entry["tool_calls"] = calls
                self.messages.append(entry)
                if msg.get("content") or msg.get("thinking"):
                    self.emit("assistant", text=msg.get("content") or "", thinking=msg.get("thinking") or "")
                if not calls:
                    break
                for c in calls:
                    self.messages.append({"role": "tool", "tool_call_id": c.get("id") or "",
                                          "content": self._call(c)})
                    if self.cancel:
                        break
            else:
                self.emit("info", text=f"It took {steps} steps without finishing. Say \"go on\" to let it continue.")
            self.state = "idle"
        except Exception as e:  # noqa: BLE001 - the session must not die with the thread
            self.state, self.error = "error", (str(e) or e.__class__.__name__)[:800]
            self.emit("error", text=self.error)
            # an assistant message whose calls have no answers would break the next request
            while self.messages and self.messages[-1]["role"] == "assistant" and self.messages[-1].get("tool_calls"):
                self.messages.pop()
        finally:
            self.live, self.pending = None, None
            self.set_phase("idle")
            self.emit("done")
            try:
                self.save()
            except OSError:
                pass

    def _call(self, c):
        fn = c.get("function") or {}
        name = fn.get("name") or "?"
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            if not isinstance(args, dict):
                raise ValueError
        except ValueError:
            self.emit("tool", name=name, args={"raw": str(raw)[:2000]}, ok=False, result="The arguments were not valid JSON.")
            return "Error: the arguments were not valid JSON. Send one JSON object."
        ev = {"name": name, "args": _short_args(args)}
        self.set_phase("tool", _tool_label(name, args))
        try:
            text, ok, extra = self._do(name, args)
        except (ValueError, PermissionError, FileNotFoundError, IsADirectoryError, NotADirectoryError,
                UnicodeDecodeError, OSError, LookupError, http.client.HTTPException) as e:
            text, ok, extra = f"Error: {e}", False, {}
        limit = self.m.tool_limit()
        shown = text if len(text) <= 6000 else text[:6000] + f"\n... ({len(text) - 6000} more characters)"
        self.emit("tool", **ev, ok=ok, result=shown, **extra)
        if len(text) > limit:
            text = text[:limit // 2] + f"\n\n... {len(text) - limit} characters cut ...\n\n" + text[-limit // 2:]
        return text

    def _ask(self, kind, **data):
        """Waits for the user's yes or no. Returns (allowed, note)."""
        self._answer.clear()
        before = dict(self.phase)
        self.pending = {"id": config.new_id(), "kind": kind, **data}
        self.state = "waiting"
        self.set_phase("waiting", "Waiting for your answer")
        self.emit("ask", what=kind, **{k: v for k, v in self.pending.items() if k != "kind"})
        self.m.notify(self, data)
        while not self._answer.wait(1):
            if self.cancel:
                break
        p, self.pending = self.pending, None
        self.state = "running"
        self.set_phase(before.get("kind", "tool"), before.get("label", ""))
        ans = (p or {}).get("answer") or "no"
        self.emit("answer", id=(p or {}).get("id"), allow=ans, note=(p or {}).get("note", ""))
        if ans == "always":
            if kind == "command":
                self.allowed.append(_prefix(data.get("command", "")))
            else:
                self.allow_edits = True
        return ans in ("yes", "always"), (p or {}).get("note", "")

    def _needs_ask(self, kind, command=""):
        if kind == "command":
            if dangerous(command):
                return True
            if self.mode == "auto":
                return False
            c = command.strip()
            if _CHAINED.search(re.sub(r"\s[12]?>&[12]\b", " ", c)):
                return True                      # "always" covers one command, not what is chained after it
            return not any(c == a or c.startswith(a + " ") for a in self.allowed)
        return self.mode == "ask" and not self.allow_edits

    def _do(self, name, a):
        t = self.tools
        if name == "ls":
            return t.ls(a.get("path") or "."), True, {}
        if name == "glob":
            return t.glob(a.get("pattern")), True, {}
        if name == "grep":
            return t.grep(a.get("pattern", ""), a.get("path") or ".", a.get("glob"), bool(a.get("ignore_case"))), True, {}
        if name == "read":
            return t.read(a.get("path"), a.get("offset") or 1, a.get("limit")), True, {}
        if name == "todo":
            items = [{"text": str(i.get("text", ""))[:300], "done": bool(i.get("done"))}
                     for i in (a.get("items") or []) if isinstance(i, dict) and i.get("text")][:40]
            self.todo = items
            self.emit("todo", items=items)
            left = sum(1 for i in items if not i["done"])
            return f"Saved {len(items)} steps, {left} left.", True, {}
        if name in ("edit", "write"):
            if name == "edit":
                full, old, new = t.plan_edit(a.get("path"), a.get("old"), a.get("new"), bool(a.get("all")))
            else:
                full, old, new = t.plan_write(a.get("path"), a.get("content"), bool(a.get("append")))
            rel = t.rel(full)
            diff = unified(rel, old, new)
            what = (f"edit {rel}" if old is not None else f"create {rel}")
            if self._needs_ask("edit"):
                ok, note = self._ask("edit", path=rel, diff=diff, new_file=old is None)
                if not ok:
                    return "The user said no to this change." + (f" They said: {note}" if note else ""), False, \
                        {"diff": diff, "declined": True}
            t.save(full, new)
            self.undo.append((rel, old, what))
            del self.undo[:-200]
            adds = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
            dels = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
            return (f"{'Changed' if old is not None else 'Created'} {rel} (+{adds} -{dels} lines).", True,
                    {"diff": diff, "path": rel})
        if name == "bash":
            cmd = str(a.get("command") or "").strip()
            if not cmd:
                raise ValueError("Give a command.")
            if hardware.ANDROID:
                raise PermissionError("Commands cannot run on the phone. Use Pumpkin Code on a computer.")
            if self._needs_ask("command", cmd):
                ok, note = self._ask("command", command=cmd, danger=dangerous(cmd))
                if not ok:
                    return "The user said no to running this." + (f" They said: {note}" if note else ""), False, \
                        {"declined": True}
            self.emit("running", command=cmd)
            out, ok = t.bash(cmd, a.get("timeout") or 120, lambda: self.cancel)
            return out, ok, {}
        if name in ("web_search", "read_webpage"):
            from . import mcp_web
            return mcp_web.call(name, a), True, {}
        if name == "skill":
            want = str(a.get("name") or "").strip().lower()
            sk = next((x for x in t.skills() if x["name"].lower() == want or skills.slug(x["name"]) == skills.slug(want)),
                      None)
            if not sk:
                raise LookupError(f"There is no skill called {a.get('name')!r}. Skills: "
                                  f"{', '.join(x['name'] for x in t.skills()) or 'none'}.")
            return (skills.read_file(sk, a["path"]) if a.get("path") else skills.read_text(sk)), True, {}
        raise LookupError(f"There is no tool called {name}. Use ls, glob, grep, read, edit, write, bash or todo.")

    # ---- the model
    def _payload(self, tools):
        p = {"messages": self.messages, "stream": True, "stream_options": {"include_usage": True},
             "temperature": 0.3, "top_p": 0.95}
        limit = self.m.answer_limit()
        if limit:                                # no limit: the model writes as long as it needs, until the context is full
            p["max_tokens"] = limit
        if tools:
            p["tools"] = self.tools.definitions()
            p["parallel_tool_calls"] = True
        return p

    def _complete(self, tools=True, messages=None):
        """One answer from the model, streamed so the UI sees it being written. Returns {content, thinking,
        tool_calls}."""
        port = self.m.port(self.preset)
        payload = self._payload(tools)
        if messages is not None:
            payload["messages"] = messages
        body = json.dumps(payload).encode()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3600)
        conn.request("POST", "/v1/chat/completions", body=body,
                     headers={"Content-Type": "application/json", **llama.auth_header()})
        sock = conn.sock                          # the response takes the connection over: keep the socket to cut it
        self._sock = sock                         # Stop cuts it
        self.set_phase("summarising" if messages is not None else "reading",
                       "Summarising the earlier work" if messages is not None else "Reading the conversation")
        try:
            if self.cancel:
                raise ConnectionAbortedError("stopped")
            r = conn.getresponse()
        except (OSError, http.client.HTTPException):
            self._sock = None
            conn.close()
            if self.cancel:
                return {"content": "", "thinking": "", "tool_calls": []}
            raise
        if r.status != 200:
            raw = r.read().decode("utf-8", "replace")
            try:
                err = json.loads(raw).get("error")
                err = err.get("message") if isinstance(err, dict) else err
            except ValueError:
                err = raw[:400]
            if r.status == 400 and "context" in str(err).lower() and messages is None:
                self._sock = None
                conn.close()
                if not getattr(self, "_ctx_retry", False):
                    if self._compact(force=True):            # it got shorter: ask once more
                        self._ctx_retry = True
                        try:
                            return self._complete(tools)
                        finally:
                            self._ctx_retry = False
                raise RuntimeError("The conversation does not fit in the model's context even after summarising it. "
                                   "Pick a bigger context for this model in Presets, or start a new session.")
            if re.search(r"parse tool call|tool call arguments|invalid string|unexpected end of", str(err), re.I):
                raise _CutOff(str(err)[:300])
            raise RuntimeError(f"The model answered with an error: {str(err)[:600]}")
        text, thinking, calls = [], [], {}
        live = self.live = {"text": "", "thinking": ""}
        got = [time.time()]
        hung = [False]
        done = threading.Event()

        def watch():
            """Nothing arrives and llama-server says it is idle, twice in a row: the answer will never come (the
            request was dropped). Close the connection so the wait ends."""
            quiet = 0
            while not done.wait(self.WATCH_EVERY):
                if time.time() - got[0] < self.HUNG_AFTER:
                    quiet = 0
                    continue
                p = self.m.core.decode_progress()
                quiet = quiet + 1 if p is None else 0
                if quiet >= 2:
                    hung[0] = True
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except (OSError, AttributeError):
                        pass
                    return

        threading.Thread(target=watch, daemon=True).start()
        finish = None
        try:
            for line in r:
                got[0] = time.time()
                if self.cancel:
                    break
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    break
                try:
                    j = json.loads(data)
                except ValueError:
                    continue
                if j.get("error"):
                    e = j["error"]
                    e = e.get("message") if isinstance(e, dict) else e
                    if re.search(r"parse tool call|tool call arguments|invalid string", str(e), re.I):
                        raise _CutOff(str(e)[:300])
                    raise RuntimeError(f"The model answered with an error: {str(e)[:600]}")
                u, tm = j.get("usage") or {}, j.get("timings") or {}
                if u.get("prompt_tokens"):
                    self.tokens = int(u["prompt_tokens"]) + int(u.get("completion_tokens") or 0)
                elif tm.get("prompt_n") is not None:
                    self.tokens = int(tm.get("prompt_n") or 0) + int(tm.get("cache_n") or 0) + \
                        int(tm.get("predicted_n") or 0)
                for ch in j.get("choices") or []:
                    d = ch.get("delta") or {}
                    if d.get("content"):
                        text.append(d["content"])
                        live["text"] += d["content"]
                        self.streamed += 1
                        if messages is None:
                            self.set_phase("writing", "Writing the answer")
                    if d.get("reasoning_content"):
                        thinking.append(d["reasoning_content"])
                        live["thinking"] += d["reasoning_content"]
                        self.streamed += 1
                        if messages is None:
                            self.set_phase("thinking", "Thinking")
                    for tc in d.get("tool_calls") or []:
                        i = tc.get("index", len(calls))
                        cur = calls.setdefault(i, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            cur["id"] = tc["id"]
                        f = tc.get("function") or {}
                        if f.get("name"):
                            cur["function"]["name"] += f["name"]
                        if f.get("arguments"):
                            cur["function"]["arguments"] += f["arguments"]
                        live["tools"] = [c["function"]["name"] for c in calls.values()]
                        self.streamed += 1
                        if messages is None:
                            nm = next((c["function"]["name"] for c in calls.values() if c["function"]["name"]), "")
                            self.set_phase("calling", {"write": "Writing a file", "edit": "Editing a file",
                                                       "bash": "Writing a command"}.get(nm) or
                                           (f"Preparing {nm}" if nm else "Choosing what to do next"))
                    finish = ch.get("finish_reason") or finish
        except (OSError, http.client.HTTPException):
            if not hung[0] and not self.cancel:
                raise
        finally:
            self._sock = None
            done.set()
            conn.close()
            self.live = None
        if hung[0] and not self.cancel:
            raise _Hung("The model stopped answering.")
        out = [calls[k] for k in sorted(calls)]
        for n, c in enumerate(out):
            c["id"] = c["id"] or f"call_{int(time.time() * 1000)}_{n}"
        content = "".join(text)
        if finish == "length" and not out:
            content += "\n\n(The answer was cut off at the length limit.)"
        return {"content": content, "thinking": "".join(thinking), "tool_calls": out}

    # ---- keeping it in the context
    def _size(self):
        return len(json.dumps(self.messages, ensure_ascii=False)) // 3

    def _compact_if_needed(self):
        ctx = self.m.ctx()
        used = max(self.tokens, self._size())
        if ctx and used > ctx * 0.72:
            self._compact()

    def _compact(self, force=False):
        """Summarises the older part of the session so the rest fits in the model's context."""
        msgs = self.messages
        starts = [i for i, m in enumerate(msgs) if i > 1 and (m["role"] == "user" or (m["role"] == "assistant"))]
        if len(starts) < 3:
            if not force:
                return
            keep_from = len(msgs) - 1
        else:
            keep_from = starts[-2]
        while keep_from < len(msgs) and msgs[keep_from]["role"] == "tool":
            keep_from += 1
        old = msgs[1:keep_from]
        if not old:
            return False
        self.emit("info", text="The session is getting long for the model's context, so it is summarising the "
                               "earlier part.")
        transcript = []
        for m in old:
            c = m.get("content") or ""
            if m["role"] == "tool":
                c = c[:1500]
            calls = "; ".join(f'{t["function"]["name"]}({t["function"]["arguments"][:300]})'
                              for t in m.get("tool_calls") or [])
            transcript.append(f"[{m['role']}] {c[:4000]}" + (f" calls: {calls}" if calls else ""))
        req = [{"role": "system", "content": "You summarise a coding session so it can continue."},
               {"role": "user", "content": "Summarise this coding session for whoever continues it: what the user "
                "asked for (their words), what was found, which files were changed and how, commands that were run "
                "and their results, what is still left to do. Keep exact file names, function names and errors. "
                "Be brief.\n\n" + "\n".join(transcript)[-60000:]}]
        summary = self._complete(tools=False, messages=req)["content"].strip() or "(no summary)"
        first = next((m["content"] for m in old if m["role"] == "user"), "")
        self.messages = [msgs[0], {"role": "user", "content": f"Summary of the work so far:\n{summary}\n\n"
                                                             f"The first request was: {first[:3000]}"},
                         {"role": "assistant", "content": "Understood. I will continue from here."}] + msgs[keep_from:]
        self.tokens = self._size()
        self.emit("compact", text=summary)
        return True


class _CutOff(Exception):
    """The model's answer ended in the middle of a tool call (too long for one answer)."""


class _Hung(Exception):
    """The model server went quiet and idle while Pumpkin Code was waiting for its answer."""


def _tool_label(name, a):
    """A short line for what a tool call is doing, for the activity indicator."""
    arg = str(a.get("command") or a.get("path") or a.get("pattern") or a.get("query") or a.get("url") or "")
    arg = re.sub(r"\s+", " ", arg)[:60]
    verb = {"ls": "Listing", "glob": "Finding files", "grep": "Searching for", "read": "Reading", "edit": "Editing",
            "write": "Writing", "bash": "Running", "todo": "Updating the plan", "web_search": "Searching the web for",
            "read_webpage": "Reading"}.get(name, f"Using {name}")
    return f"{verb} {arg}".strip()


def _short_args(a):
    out = {}
    for k, v in a.items():
        if isinstance(v, str) and len(v) > 4000 and k in ("content", "old", "new"):
            v = v[:4000] + f"... ({len(v) - 4000} more characters)"
        out[k] = v
    return out


def _prefix(cmd):
    """What "always allow" covers: the program and its first word (npm test, git status, pytest)."""
    words = str(cmd).strip().split()
    if not words:
        return ""
    if len(words) > 1 and not words[1].startswith("-") and re.match(r"^[\w.:-]+$", words[1]):
        return " ".join(words[:2])
    return words[0]


# ---------------------------------------------------------------- all sessions
class Manager:
    def __init__(self, core):
        self.core = core
        self.sessions = {}
        self._loaded = False
        self.lock = threading.Lock()

    def settings_get(self, k, default):
        try:
            v = self.core.settings[k]
            return default if v in (None, "") else v
        except (KeyError, AttributeError, TypeError):
            return default

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        folder = _sessions_dir()
        if not os.path.isdir(folder):
            return
        for n in os.listdir(folder):
            if not n.endswith(".json"):
                continue
            try:
                with open(os.path.join(folder, n), encoding="utf-8") as f:
                    s = Session.load(self, json.load(f))
                self.sessions[s.id] = s
            except (OSError, ValueError, KeyError, TypeError):
                pass

    def list(self):
        with self.lock:
            self._load()
            return sorted((s.summary() for s in self.sessions.values()), key=lambda s: -s["updated"])

    def get(self, sid):
        with self.lock:
            self._load()
            s = self.sessions.get(sid)
        if not s:
            raise FileNotFoundError("That session is not there.")
        return s

    def create(self, root, mode="ask", preset=None):
        raw = str(root or "").strip()
        root = os.path.realpath(os.path.expanduser(raw)) if raw else ""   # "" would mean Pumpkin's own folder
        if not root or not os.path.isdir(root):
            raise ValueError(f"{root or 'That'} is not a folder on this computer.")
        if os.path.dirname(root) == root or root == os.path.realpath(os.path.expanduser("~")):
            raise ValueError("Pick a project folder, not the whole disk or your home folder.")
        s = Session(self, root, mode, preset)
        with self.lock:
            self._load()
            self.sessions[s.id] = s
        s.emit("info", text=f"Working in {root}.")
        s.save()
        recent = [r for r in (self.settings_get("code_recent", []) or []) if r != root]
        try:
            self.core.settings.update({"code_recent": [root] + recent[:9]})
        except Exception:  # noqa: BLE001
            pass
        return s

    def delete(self, sid):
        s = self.get(sid)
        s.deleted = True
        s.stop()
        with self.lock:
            self.sessions.pop(sid, None)
        try:
            os.remove(os.path.join(_sessions_dir(), sid + ".json"))
        except OSError:
            pass

    # ---- the model
    def port(self, preset):
        core = self.core
        if preset:
            core.ensure_preset(preset)
        elif core.server.state == "external":
            pass
        elif core.server.state not in ("ready", "loading"):
            p = core.default_preset()
            if not p:
                raise RuntimeError("No model is set up. Add one on the Models page first.")
            core.ensure_preset(p.get("id"))
        elif core.server.state == "loading" and not core.server.wait_ready(timeout=900):
            raise RuntimeError(core.server.error or "The model did not start.")
        with core.image_lock:                 # a picture or the vision helper may have paused the text model
            pass
        if core.server.state not in ("ready", "external"):
            if not core.server.wait_ready(timeout=900):
                raise RuntimeError(core.server.error or "The model is not running.")
        return int(core.settings["llama_port"])

    def ctx(self):
        pre = self.core.server.preset or {}
        try:
            return int(pre.get("ctx") or 0)
        except (TypeError, ValueError):
            return 0

    def skills_for(self, root):
        """The skills a session may use: yours and Pumpkin's, plus the project's own (.pumpkin/skills and so on)."""
        try:
            if not self.core.settings["skills"]:
                return []
            return skills.enabled(self.core.settings, project=root)
        except Exception:  # noqa: BLE001 - skills are extra; a broken one must not stop the session
            return []

    def answer_limit(self):
        """The longest single answer in tokens, or 0 for no limit (the default)."""
        try:
            return max(0, int(self.settings_get("code_max_tokens", 0)))
        except (TypeError, ValueError):
            return 0

    def tool_limit(self):
        ctx = self.ctx() or 16384
        return max(4000, min(30000, int(ctx * 3 * 0.15)))

    def notify(self, s, data):
        try:
            hook = getattr(self.core, "code_waiting", None)
            if hook:
                hook(s, data)
        except Exception:  # noqa: BLE001
            pass

    def system_prompt(self, s):
        t = s.tools
        osname = {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "Linux")
        parts = [
            f"You are Pumpkin Code, a coding agent. You work in the project folder {s.root} on {osname}, and you get "
            f"things done by calling tools. Commands run with {t.shell_name}.",
            "How to work:\n"
            "- Look before you act: find the right code with ls, glob and grep, and read files before you change "
            "them. Never guess what a file contains.\n"
            "- Change files with edit (exact old text, copied from read, then the new text). Use write only for new "
            "files or full rewrites.\n"
            "- Make the change the user asked for and nothing more. Keep the project's style.\n"
            "- After changing code, check it: run the tests, a build, or the program, and fix what fails.\n"
            "- For a job with several steps, keep a todo list with the todo tool and mark steps done as you go.\n"
            "- Paths are relative to the project folder. You cannot leave it.\n"
            "- If the request is unclear, or something would be hard to take back (deleting data, pushing, "
            "rewriting history), ask the user instead of doing it.\n"
            "- When you are done, say in a few sentences what you changed and how you checked it. Don't paste "
            "whole files back.",
        ]
        for name in INSTRUCTION_FILES:
            p = os.path.join(s.root, name)
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        parts.append(f"The project's instructions ({name}):\n" + f.read(12000))
                except OSError:
                    pass
                break
        sk = t.skills()
        if sk:
            parts.append("Skills (instructions for particular jobs; when the job matches one, read it with the skill "
                         "tool first and follow it):\n" + skills.listing(sk))
        try:
            parts.append("The project folder contains:\n" + t.ls("."))
        except OSError:
            pass
        git = _git_summary(s.root)
        if git:
            parts.append(git)
        return "\n\n".join(parts)


def _git_summary(root):
    if not os.path.isdir(os.path.join(root, ".git")) or not shutil.which("git"):
        return ""
    def run(*a):
        try:
            return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, timeout=10,
                                  creationflags=hardware.NO_WINDOW if WINDOWS else 0).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    branch = run("branch", "--show-current")
    status = run("status", "--short")
    lines = status.splitlines()
    log = run("log", "--oneline", "-5")
    out = f"It is a git repository, on branch {branch or '(detached)'}."
    out += ("\nUncommitted changes:\n" + "\n".join(lines[:30]) + (f"\n... {len(lines) - 30} more" if len(lines) > 30
                                                                 else "")) if lines else "\nNo uncommitted changes."
    if log:
        out += "\nRecent commits:\n" + log
    return out
