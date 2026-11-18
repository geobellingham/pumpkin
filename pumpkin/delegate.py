"""Claude and Pumpkin working together: Claude connects to Pumpkin as an MCP connector and hands the local model
the jobs it can do (summaries, extraction, sorting, translation, drafts), then carries on and collects the answers.

The tools are served at /mcp on Pumpkin's own address (Streamable HTTP, Pumpkin's key or the Claude key) and through
`pumpkin mcp` for apps that start their connectors as programs (Claude Desktop, Claude Code). Jobs run one at a time
on this computer's model, in the order they came in; the Claude card on the Devices page lists them."""
import json
import os
import re
import secrets
import threading
import time
import uuid

from . import __version__, config, docs, llama

MAX_PROMPT = 400_000            # characters in one prompt, files included
MAX_ITEMS = 500                 # items in one batch
MAX_FILE_BYTES = 60_000_000
KEEP_JOBS = 60
DEFAULT_WAIT = 45               # seconds a call waits for its answer before handing back the job's id
MAX_WAIT = 110

INSTRUCTIONS = """Pumpkin runs open-weight models on the user's own computer: free, private and always there, \
but slower and less capable than you. Use it to save your own effort and the user's usage on work that is bulk or \
mechanical, and keep the thinking for yourself.

Good to hand over: summarising or pulling facts out of long files, sorting or tagging many items, translating, \
rewriting or reformatting text, first drafts and boilerplate, simple code you will check, and quick lookups where \
a rough answer is fine. Keep for yourself: planning, hard reasoning, anything where a mistake is costly, and the \
final answer to the user.

How to work with it:
- Call status once to see the models and how fast they are.
- The model sees nothing of your conversation: give it a self-contained prompt and say exactly what to return. \
Pass json_schema when you need structured output.
- Pass files by path (they are read on the user's computer, so private files never leave it) instead of pasting them.
- Small job: ask, and the answer comes back. Big or many jobs: ask with wait 0, or batch for one instruction over \
many items, keep working on something else, then collect them with result.
- Check what comes back before you rely on it (spot-check facts, run the code). If it fails twice on the same job, \
do the job yourself."""

TOOLS = [
    {"name": "status",
     "description": "The local models on the user's computer: which is loaded, their size, context length, speed in "
                    "tokens per second, and how many jobs are waiting. Call it once before handing work over.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ask",
     "description": "Give the local model one job and get its answer. Returns the answer with the model, time and "
                    "speed, or, when it takes longer than `wait` seconds, a job id to collect with result.",
     "inputSchema": {"type": "object", "properties": {
         "prompt": {"type": "string", "description": "The whole job, self-contained: the model sees nothing else."},
         "system": {"type": "string", "description": "Optional instructions on how to answer."},
         "files": {"type": "array", "items": {"type": "string"},
                   "description": "Paths of files on the user's computer to read into the prompt (PDF, Word, "
                                  "PowerPoint, Excel, text, code). ~ is the user's home folder."},
         "json_schema": {"type": "object", "description": "A JSON Schema the answer must follow exactly."},
         "model": {"type": "string", "description": "A model name from status. Default: the loaded one."},
         "tools": {"type": "boolean", "description": "Let the local model use its own tools (web search, the "
                                                     "user's files) on the way. Slower. Default false."},
         "think": {"type": "boolean", "description": "Let a thinking model think first: better on hard jobs, "
                                                     "slower. Default false."},
         "max_tokens": {"type": "integer", "description": "Longest answer in tokens. Default 2048."},
         "temperature": {"type": "number", "description": "Default 0.3."},
         "wait": {"type": "integer", "description": f"Seconds to wait for the answer (0 to {MAX_WAIT}). 0 hands "
                                                    f"back a job id at once. Default {DEFAULT_WAIT}."}},
         "required": ["prompt"]}},
    {"name": "batch",
     "description": "The same instruction over many items (texts, file paths, rows), one answer per item, in order. "
                    "Runs in the background: you get a job id; collect with result.",
     "inputSchema": {"type": "object", "properties": {
         "instruction": {"type": "string", "description": "What to do with each item, and what to return."},
         "items": {"type": "array", "items": {"type": "string"}, "description": f"Up to {MAX_ITEMS} items."},
         "items_are_files": {"type": "boolean", "description": "Each item is a file path to read. Default false."},
         "json_schema": {"type": "object", "description": "A JSON Schema each answer must follow."},
         "model": {"type": "string"},
         "max_tokens": {"type": "integer", "description": "Longest answer per item. Default 1024."}},
         "required": ["instruction", "items"]}},
    {"name": "result",
     "description": "The state of a job, and its answer once done (a batch also reports how far it got).",
     "inputSchema": {"type": "object", "properties": {
         "job": {"type": "string"},
         "wait": {"type": "integer", "description": f"Seconds to wait for it to finish (0 to {MAX_WAIT}). "
                                                    f"Default {DEFAULT_WAIT}."}},
         "required": ["job"]}},
    {"name": "jobs", "description": "The recent jobs and their states.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "cancel", "description": "Stop a job that is waiting or running.",
     "inputSchema": {"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"]}},
    {"name": "load", "description": "Load one of the models from status, so the next jobs do not wait for it.",
     "inputSchema": {"type": "object", "properties": {"model": {"type": "string"}}, "required": ["model"]}},
]


# ---------------------------------------------------------------- the Claude key
def _key_path():
    return os.path.join(config.CONFIG_DIR, "claude_key")


def key(create=True):
    """The key Claude uses from another computer. It opens /mcp and nothing else; a new one replaces it."""
    try:
        with open(_key_path(), encoding="utf-8") as f:
            k = f.read().strip()
        if k:
            return k
    except OSError:
        pass
    return new_key() if create else ""


def new_key():
    config.ensure_dirs()
    k = "pk-claude-" + secrets.token_urlsafe(24)
    fd = os.open(_key_path() + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(k)
    os.replace(_key_path() + ".tmp", _key_path())
    return k


def key_ok(given):
    k = key(create=False)
    return bool(k) and secrets.compare_digest(str(given).encode(), k.encode())


# ---------------------------------------------------------------- reading what Claude sends
def read_file(path):
    """A file on this computer as text for the prompt."""
    p = os.path.realpath(os.path.expanduser(str(path or "").strip()))
    if not os.path.isfile(p):
        raise ValueError(f"There is no file at {path}.")
    if os.path.getsize(p) > MAX_FILE_BYTES:
        raise ValueError(f"{path} is too big (over {MAX_FILE_BYTES // 1_000_000} MB).")
    parts = docs.extract(p)
    return "\n".join(f"[{place}] {text}" if place and len(parts) > 1 else text for place, text in parts)


def _int(v, default, lo, hi):
    try:
        return max(lo, min(int(v), hi))
    except (TypeError, ValueError):
        return default


def _clean_answer(text):
    text = re.sub(r"<think>.*?</think>", "", str(text or ""), flags=re.S)
    return text.strip()


class Jobs:
    """The jobs Claude handed over, run one after another on this computer's model."""

    def __init__(self, core):
        self.core = core
        self.jobs = []                      # newest last
        self.cond = threading.Condition()
        self.last_speed = None
        self.worker = None

    # ---- the queue
    def add(self, kind, spec, who="Claude"):
        job = {"id": uuid.uuid4().hex[:8], "kind": kind, "spec": spec, "who": who, "state": "queued",
               "at": time.time(), "started": None, "finished": None, "model": None, "answer": None,
               "results": None, "done": 0, "total": len(spec.get("items") or []) or 1, "error": None,
               "tokens": 0, "cancel": False,
               "title": str(spec.get("prompt") or spec.get("instruction") or "")[:140]}
        with self.cond:
            self.jobs.append(job)
            del self.jobs[:-KEEP_JOBS]
            if not self.worker or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._work, daemon=True)
                self.worker.start()
            self.cond.notify_all()
        return job

    def find(self, jid):
        jid = str(jid or "").strip()
        job = next((j for j in self.jobs if j["id"] == jid), None)
        if not job:
            raise LookupError(f"There is no job {jid}. jobs lists the recent ones.")
        return job

    def wait(self, job, seconds):
        end = time.time() + seconds
        with self.cond:
            while job["state"] in ("queued", "running") and time.time() < end:
                self.cond.wait(min(1.0, max(0.05, end - time.time())))
        return job

    def cancel(self, jid):
        job = self.find(jid)
        if job["state"] in ("queued", "running"):
            job["cancel"] = True
            if job["state"] == "queued":
                self._finish(job, "cancelled")
        return job

    def _next(self):
        with self.cond:
            while True:
                job = next((j for j in self.jobs if j["state"] == "queued"), None)
                if job:
                    job["state"], job["started"] = "running", time.time()
                    return job
                if not self.cond.wait(timeout=120):
                    return None             # quiet for two minutes: the thread ends, the next job starts a new one

    def _work(self):
        while True:
            job = self._next()
            if not job:
                return
            try:
                if job["kind"] == "batch":
                    self._batch(job)
                else:
                    self._ask(job)
                self._finish(job, "cancelled" if job["cancel"] else "done")
            except Exception as e:  # noqa: BLE001 - one bad job must not stop the ones after it
                job["error"] = str(e) or e.__class__.__name__
                self._finish(job, "error")

    def _finish(self, job, state):
        with self.cond:
            job["state"], job["finished"] = state, time.time()
            self.cond.notify_all()

    # ---- running a job
    def _model(self, want):
        """Loads the model asked for, or makes sure one is loaded. Returns the preset."""
        core = self.core
        if core.server.state == "external":
            return {"name": "the model you started yourself"}
        name = core.helper.ready_model(want)
        return core.find_preset(name) or {"name": name}

    def _chat(self, messages, spec, preset):
        core = self.core
        prompt_chars = sum(len(m["content"]) for m in messages)
        ctx = int(preset.get("ctx") or 0)
        need = prompt_chars // 3 + int(spec.get("max_tokens") or 0)
        if ctx and need > ctx:
            raise ValueError(f"This is about {need} tokens with the answer, but {preset.get('name')} holds {ctx}. "
                             "Split it (batch helps), ask for a shorter answer, or load a model with a longer "
                             "context.")
        payload = {"messages": messages, "max_tokens": spec["max_tokens"], "stream": False,
                   "temperature": spec.get("temperature", 0.3),
                   "chat_template_kwargs": {"enable_thinking": bool(spec.get("think"))}}
        if isinstance(spec.get("json_schema"), dict):
            payload["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": "answer", "schema": spec["json_schema"]}}
        r = llama.http(core.server.port if core.server.state != "external" else int(core.settings["llama_port"]),
                       "/v1/chat/completions", payload, timeout=3600)
        if "choices" not in r:
            raise RuntimeError(f"The local model answered with an error: {r.get('error', r)}")
        choice = r["choices"][0]
        answer = _clean_answer((choice.get("message") or {}).get("content"))
        if not answer:
            why = ("ran out of room before answering (raise max_tokens, or turn think off)"
                   if choice.get("finish_reason") == "length" else "gave an empty answer")
            raise RuntimeError(f"The local model {why}.")
        tokens = int((r.get("usage") or {}).get("completion_tokens") or 0)
        speed = (r.get("timings") or {}).get("predicted_per_second")
        if speed:
            self.last_speed = round(float(speed), 1)
        if spec.get("json_schema") is not None:
            try:
                json.loads(answer)
            except ValueError:
                raise RuntimeError("The local model's answer is not valid JSON: " + answer[:300])
        return answer, tokens, choice.get("finish_reason")

    def _ask(self, job):
        spec = job["spec"]
        preset = self._model(spec.get("model"))
        job["model"] = preset.get("name")
        prompt = spec["prompt"]
        for path in spec.get("files") or []:
            prompt += f"\n\n--- {os.path.basename(str(path))} ---\n" + read_file(path)
        if len(prompt) > MAX_PROMPT:
            raise ValueError(f"The prompt with its files is over {MAX_PROMPT} characters. Use batch, or fewer files.")
        if spec.get("tools") and self.core.server.state == "ready":
            from . import agent
            system = spec.get("system") or "You do one job for another assistant. Use your tools when they help, " \
                                            "then answer with exactly what was asked."
            job["answer"] = _clean_answer(agent.run_agent(self.core.server.port, prompt, system, lambda t: None,
                                                          self.core.settings, lambda: job["cancel"]))
            return
        messages = ([{"role": "system", "content": spec["system"]}] if spec.get("system") else []) + \
            [{"role": "user", "content": prompt}]
        job["answer"], job["tokens"], job["finish"] = self._chat(messages, spec, preset)

    def _batch(self, job):
        spec = job["spec"]
        preset = self._model(spec.get("model"))
        job["model"] = preset.get("name")
        job["results"] = []
        for i, item in enumerate(spec["items"]):
            if job["cancel"]:
                return
            try:
                text = read_file(item) if spec.get("items_are_files") else item
                answer, tokens, _ = self._chat([{"role": "system", "content": spec["instruction"]},
                                                {"role": "user", "content": text}], spec, preset)
                job["results"].append({"item": i, "answer": answer})
                job["tokens"] += tokens
            except (ValueError, RuntimeError, OSError) as e:
                job["results"].append({"item": i, "error": str(e)})
            job["done"] = i + 1

    # ---- what Claude and the Devices page see
    def view(self, job, full=True):
        out = {"job": job["id"], "kind": job["kind"], "state": job["state"], "model": job["model"],
               "title": job["title"], "who": job["who"]}
        if job["started"]:
            out["seconds"] = round((job["finished"] or time.time()) - job["started"], 1)
        if job["state"] == "queued":
            out["waiting_behind"] = sum(1 for j in self.jobs if j["state"] in ("queued", "running")
                                        and j["at"] < job["at"])
        if job["kind"] == "batch":
            out["done"], out["total"] = job["done"], job["total"]
        if job["tokens"] and out.get("seconds"):
            out["tokens"] = job["tokens"]
            out["tokens_per_second"] = round(job["tokens"] / max(out["seconds"], 0.1), 1)
        if job["error"]:
            out["error"] = job["error"]
        if full and job["answer"] is not None:
            out["answer"] = job["answer"]
            if job.get("finish") == "length":
                out["note"] = "The answer was cut off at max_tokens."
        if full and job["results"] is not None:
            out["results"] = job["results"]
        if job["state"] in ("queued", "running") and full:
            out["next"] = f"Still {job['state']}. Do something else meanwhile, then call result with job {job['id']}."
        return out

    def recent(self):
        return [self.view(j, full=False) for j in reversed(self.jobs)]


# ---------------------------------------------------------------- the tools
def status(core):
    lib = core.library
    cur = (core.server.preset or {}) if core.server.state in ("ready", "loading") else {}
    models = []
    for p in core.presets.all():
        m = lib.resolve(p.get("model")) if p.get("model") else None
        size = round(os.path.getsize(m["path"]) / 1e9, 1) if m and os.path.isfile(m.get("path", "")) else None
        models.append({"name": p.get("name"), "size_gb": size, "context_tokens": int(p.get("ctx") or 8192),
                       "sees_pictures": bool(p.get("mmproj")), "own_tools": bool(p.get("tools")),
                       "loaded": cur.get("id") == p.get("id") and core.server.state == "ready"})
    jobs = core.delegate
    return {"pumpkin": __version__, "state": core.server.state, "loaded": cur.get("name"),
            "models": models, "speed_tokens_per_second": jobs.last_speed,
            "jobs_waiting": sum(1 for j in jobs.jobs if j["state"] in ("queued", "running")),
            "note": None if models else "No models are set up in Pumpkin yet. Ask the user to add one on its Models "
                                        "page."}


def call(core, name, args, who="Claude"):
    jobs = core.delegate
    if name == "status":
        return status(core)
    if name == "ask":
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("The prompt is empty.")
        files = args.get("files") or []
        if not isinstance(files, list) or len(files) > 40:
            raise ValueError("files must be a list of up to 40 paths.")
        for f in files:
            if not os.path.isfile(os.path.expanduser(str(f))):
                raise ValueError(f"There is no file at {f}.")
        spec = {"prompt": prompt, "system": str(args.get("system") or "").strip()[:20_000], "files": files,
                "json_schema": args.get("json_schema") if isinstance(args.get("json_schema"), dict) else None,
                "model": str(args.get("model") or "").strip(), "tools": bool(args.get("tools")),
                "think": bool(args.get("think")), "max_tokens": _int(args.get("max_tokens"), 2048, 16, 32768),
                "temperature": max(0.0, min(float(args.get("temperature", 0.3) or 0), 2.0))}
        job = jobs.add("ask", spec, who)
        return jobs.view(jobs.wait(job, _int(args.get("wait"), DEFAULT_WAIT, 0, MAX_WAIT)))
    if name == "batch":
        items = args.get("items")
        if not isinstance(items, list) or not items or not all(isinstance(i, str) for i in items):
            raise ValueError("items must be a list of strings.")
        if len(items) > MAX_ITEMS:
            raise ValueError(f"Up to {MAX_ITEMS} items in one batch: split it.")
        instruction = str(args.get("instruction") or "").strip()
        if not instruction:
            raise ValueError("The instruction is empty.")
        spec = {"instruction": instruction, "items": items, "items_are_files": bool(args.get("items_are_files")),
                "json_schema": args.get("json_schema") if isinstance(args.get("json_schema"), dict) else None,
                "model": str(args.get("model") or "").strip(), "max_tokens": _int(args.get("max_tokens"), 1024, 16, 16384),
                "temperature": 0.2}
        job = jobs.add("batch", spec, who)
        return jobs.view(jobs.wait(job, 2))
    if name == "result":
        job = jobs.find(args.get("job"))
        return jobs.view(jobs.wait(job, _int(args.get("wait"), DEFAULT_WAIT, 0, MAX_WAIT)))
    if name == "jobs":
        return {"jobs": jobs.recent()}
    if name == "cancel":
        return jobs.view(jobs.cancel(args.get("job")))
    if name == "load":
        p = core.find_preset(args.get("model"))
        if not p:
            raise ValueError(f"There is no model called {args.get('model')}. status lists them.")
        threading.Thread(target=lambda: core.ensure_preset(p.get("name")), daemon=True).start()
        return {"loading": p.get("name"), "note": "Jobs you hand over now wait until it is ready."}
    raise LookupError(f"Unknown tool {name}")


def handle(core, msg, who="Claude"):
    """One JSON-RPC message from Claude: the answer, or None for a notification."""
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Not a JSON-RPC message."}}
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or "2025-03-26",
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "pumpkin", "version": __version__},
                  "instructions": INSTRUCTIONS}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = msg.get("params") or {}
        args = p.get("arguments") or {}
        try:
            if not isinstance(args, dict):
                raise ValueError("The arguments must be one JSON object.")
            out = call(core, p.get("name"), args, who)
            result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}],
                      "isError": False}
        except (ValueError, LookupError, RuntimeError, OSError) as e:
            result = {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


# ---------------------------------------------------------------- how to connect (the Devices page card)
def connect_info(core, token):
    """The commands and settings that connect Claude to this Pumpkin."""
    import shutil
    import sys
    from . import remote
    exe = shutil.which("pumpkin") if os.name != "nt" else None     # a .cmd launcher cannot be started without a shell
    if exe:
        cmd, args, env = exe, ["mcp"], {}
    else:
        py = sys.executable
        if os.name == "nt" and os.path.basename(py).lower() == "pythonw.exe":
            py = os.path.join(os.path.dirname(py), "python.exe")        # Claude talks to it over stdin and stdout
        cmd, args = py, (["-X", "utf8"] if os.name == "nt" else []) + ["-m", "pumpkin", "mcp"]
        env = {"PYTHONPATH": os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}
    desktop = {"mcpServers": {"pumpkin": dict({"command": cmd, "args": args}, **({"env": env} if env else {}))}}
    quoted = " ".join(f'"{a}"' if " " in a else a for a in [cmd] + args)
    envs = " ".join(f"-e {k}={v}" for k, v in env.items())
    info = {"desktop_config": json.dumps(desktop, indent=2),
            "claude_code": f"claude mcp add pumpkin --scope user {envs + ' ' if envs else ''}-- {quoted}",
            "remote_url": None, "remote_claude_code": None, "jobs": core.delegate.recent(),
            "speed": core.delegate.last_speed}
    base = remote.status(core.settings, token).get("base")
    if base:
        k = key()
        info["remote_url"] = base + "/mcp"
        info["remote_claude_code"] = (f"claude mcp add pumpkin --scope user --transport http {base}/mcp "
                                      f"--header \"Authorization: Bearer {k}\"")
        info["key"] = k
    return info
