"""Agent runs: the tool loop, scheduled tasks, reports and the speed benchmark."""
import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import threading
import time

from . import APP_NAME, config, llama

TASK_SUFFIX = ("\n\nYou are running an autonomous task. Use your tools to do the work. Read pages when you need "
               "details, do not repeat identical calls, and finish with a clear Markdown answer that includes "
               "source URLs.")
DAYS = {"daily": range(7), "weekdays": range(5), "weekends": (5, 6)}
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def due_days(spec):
    spec = (spec or "daily").lower().strip()
    if spec in DAYS:
        return set(DAYS[spec])
    out = set()
    for part in re.split(r"[,\s]+", spec):
        if part[:3] in DAY_NAMES:
            out.add(DAY_NAMES.index(part[:3]))
    return out or set(range(7))


def run_agent(port, prompt, system, say, settings, cancelled=lambda: False):
    """Chat with tool calls until the model answers without one."""
    max_steps = int(settings["max_steps"] or 14)
    limit = int(settings["tool_chars"] or 8000)
    tools = llama.http(port, "/tools")
    if isinstance(tools, dict) and tools.get("error"):
        if tools.get("status") == 404:
            tools = []
        else:
            raise RuntimeError(f"Could not list tools: {tools['error']}")
    defs = [t["definition"] for t in tools if isinstance(t, dict) and t.get("definition")]
    if defs:
        say(f"{len(defs)} tools: " + ", ".join(t.get("tool", "?") for t in tools))
    else:
        say("This preset has no tools, so the model answers from what it knows.")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    seen = set()
    for step in range(1, max_steps + 1):
        if cancelled():
            return "(cancelled)"
        say(f"Step {step}: thinking")
        payload = {"messages": messages, "temperature": 0.6, "max_tokens": 4096}
        if defs:
            payload["tools"] = defs
        r = llama.http(port, "/v1/chat/completions", payload, timeout=3600)
        if "choices" not in r:
            raise RuntimeError(f"The model server answered with an error: {r.get('error', r)}")
        msg = r["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        entry = {"role": "assistant", "content": msg.get("content") or ""}
        if calls:
            entry["tool_calls"] = calls
        messages.append(entry)
        if not calls:
            return msg.get("content") or ""
        for c in calls:
            name = c["function"]["name"]
            raw = c["function"].get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                args = None
            key = (name, json.dumps(args, sort_keys=True) if args is not None else raw)
            say(f"  {name} {str(raw)[:160]}")
            if args is None:
                text = "Error: tool arguments were not valid JSON."
            elif key in seen:
                text = "You already ran this exact call. Use the earlier result instead of repeating it."
            else:
                seen.add(key)
                out = llama.http(port, "/tools", {"tool": name, "params": args}, timeout=600)
                text = out.get("plain_text_response") or out.get("error") or json.dumps(out)
                if not isinstance(text, str):
                    text = json.dumps(text)
            if len(text) > limit:
                text = text[:limit] + f"\n...[cut, {len(text) - limit} more characters]"
            say(f"    got {len(text)} characters")
            messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": text})
    say("Step limit reached, asking for the final answer")
    messages.append({"role": "user", "content": "Step limit reached. Give your final answer now using what you have."})
    r = llama.http(port, "/v1/chat/completions", {"messages": messages, "temperature": 0.6, "max_tokens": 4096},
                   timeout=3600)
    if "choices" not in r:
        raise RuntimeError(f"The model server answered with an error: {r.get('error', r)}")
    return r["choices"][0]["message"].get("content") or ""


def notify(settings, title, body=""):
    """Desktop notification, plus a phone push through ntfy when a topic is set."""
    if settings["notifications"] and shutil.which("notify-send"):
        try:
            subprocess.Popen(["notify-send", "-a", APP_NAME, title, body], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError:
            pass
    topic = (settings["ntfy_topic"] or "").strip()
    if topic:
        threading.Thread(target=_ntfy, args=(settings["ntfy_server"] or "https://ntfy.sh", topic, title, body),
                         daemon=True).start()


def _ntfy(server, topic, title, body):
    import urllib.request
    req = urllib.request.Request(f"{server.rstrip('/')}/{topic}", data=(body or title).encode(),
                                 headers={"Title": title.encode("ascii", "replace").decode(), "Tags": "jack_o_lantern"},
                                 method="POST")
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except OSError:
        pass


class Runner:
    """Runs one agent job at a time (a task or a prompt) and keeps its live log."""

    def __init__(self, core):
        self.core = core
        self.busy = False
        self.cancel = False
        self.current = None
        self.log = []
        self.log_seq = 0
        self.lock = threading.Lock()
        threading.Thread(target=self._scheduler, daemon=True).start()

    def say(self, text):
        with self.lock:
            for line in str(text).splitlines() or [""]:
                self.log_seq += 1
                self.log.append({"n": self.log_seq, "t": time.time(), "text": line})
            del self.log[:-500]

    def start(self, job):
        """job: {name, prompt, preset (id or name), stop_after, task_id}"""
        with self.lock:
            if self.busy:
                raise RuntimeError("An agent run is already in progress.")
            self.busy, self.cancel, self.current = True, False, job
            self.log = []
        threading.Thread(target=self._run, args=(job,), daemon=True).start()

    def stop(self):
        self.cancel = True

    def _run(self, job):
        core = self.core
        srv = core.server
        started = False
        try:
            self.say(f"{job['name']}")
            want = core.presets.get(job.get("preset")) if job.get("preset") else None
            running = srv.preset if srv.state == "ready" else None
            if srv.state not in ("ready", "external") or (want and running and running.get("id") != want.get("id")):
                p = want or core.default_preset()
                if not p:
                    raise RuntimeError("There is no preset to start. Create one under Models.")
                self.say(f"Starting {p['name']}. Loading a model takes a minute.")
                srv.start(p)
                started = True
                if not srv.wait_ready(900, lambda: self.cancel):
                    if self.cancel:
                        return
                    raise RuntimeError(srv.error or "The model did not become ready in 15 minutes.")
            system = core.persona_prompt() + TASK_SUFFIX
            result = run_agent(srv.port, job["prompt"], system, self.say, core.settings, lambda: self.cancel)
            if self.cancel:
                self.say("Cancelled.")
                return
            path = core.reports.save(job["name"], job["prompt"], result)
            self.say(f"Finished. Report saved: {os.path.basename(path)}")
            notify(core.settings, "Task finished", job["name"])
            if job.get("task_id"):
                core.tasks.update(job["task_id"], {"last_run": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                                   "last_report": os.path.basename(path), "last_error": ""})
        except Exception as e:  # noqa: BLE001
            self.say(f"Error: {e}")
            notify(core.settings, "Task failed", f"{job['name']}: {e}")
            if job.get("task_id"):
                core.tasks.update(job["task_id"], {"last_run": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                                                   "last_error": str(e)[:300]})
        finally:
            self.busy, self.current = False, None
            if started and job.get("stop_after"):
                srv.stop()

    def _scheduler(self):
        while True:
            time.sleep(20)
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                pass

    def _tick(self):
        now = dt.datetime.now()
        today = now.strftime("%Y-%m-%d")
        for t in self.core.tasks.all():
            if not t.get("enabled") or t.get("last_sched") == today or self.busy:
                continue
            if now.weekday() not in due_days(t.get("days")):
                continue
            try:
                hh, mm = map(int, str(t.get("time", "")).split(":"))
            except ValueError:
                continue
            due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if due <= now < due + dt.timedelta(minutes=30):
                self.core.tasks.update(t["id"], {"last_sched": today})
                self.start({"name": t["name"], "prompt": t["prompt"], "preset": t.get("preset"),
                            "stop_after": t.get("stop_after"), "task_id": t["id"]})
                return


class Reports:
    def __init__(self, settings):
        self.settings = settings

    @property
    def dir(self):
        return os.path.expanduser(self.settings["reports_dir"])

    def save(self, name, prompt, result):
        os.makedirs(self.dir, exist_ok=True)
        now = dt.datetime.now()
        path = os.path.join(self.dir, f"{now:%Y-%m-%d_%H%M}_{config.slug(name)}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {name}\n\n_{now:%Y-%m-%d %H:%M}_\n\n## Prompt\n\n{prompt}\n\n## Result\n\n{result}\n")
        return path

    def list(self, folder=None):
        folder = folder or self.dir
        out = []
        try:
            for n in os.listdir(folder):
                p = os.path.join(folder, n)
                if n.endswith(".md") and os.path.isfile(p):
                    out.append({"name": n, "mtime": int(os.path.getmtime(p)), "size": os.path.getsize(p)})
        except OSError:
            pass
        return sorted(out, key=lambda x: -x["mtime"])[:200]

    def read(self, name, folder=None):
        folder = folder or self.dir
        if name != os.path.basename(name) or not name.endswith(".md"):
            raise ValueError("bad report name")
        with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as f:
            return f.read()

    def delete(self, name, folder=None):
        folder = folder or self.dir
        if name != os.path.basename(name) or not name.endswith(".md"):
            raise ValueError("bad report name")
        os.remove(os.path.join(folder, name))


# ---------------------------------------------------------------- speed benchmark
FILLER = "The quick brown fox jumps over the lazy dog while the cat watches from the old stone wall. " * 220
SPEED_CSV = os.path.join(config.DATA_DIR, "speed.csv")


def speed_bench(port, label, vram_used=None):
    common = {"cache_prompt": False, "chat_template_kwargs": {"enable_thinking": False}}
    r1 = llama.http(port, "/v1/chat/completions", {
        "messages": [{"role": "user", "content": "Write a 150-word story about a lighthouse."}],
        "max_tokens": 200, "temperature": 0.7, **common}, timeout=900)
    r2 = llama.http(port, "/v1/chat/completions", {
        "messages": [{"role": "user", "content": "Here is some text:\n" + FILLER + "\nReply with just OK."}],
        "max_tokens": 8, "temperature": 0, **common}, timeout=1800)
    for r in (r1, r2):
        if "timings" not in r:
            raise RuntimeError(f"No timings in the reply: {str(r.get('error', r))[:200]}")
    row = {"time": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "model": label,
           "gen": round(r1["timings"]["predicted_per_second"], 1),
           "prompt": round(r2["timings"]["prompt_per_second"], 0),
           "vram": round(vram_used, 1) if vram_used is not None else ""}
    os.makedirs(os.path.dirname(SPEED_CSV), exist_ok=True)
    with open(SPEED_CSV, "a", newline="") as f:
        csv.writer(f).writerow([row["time"], row["model"], row["gen"], row["prompt"], row["vram"]])
    return row


def speed_history():
    try:
        with open(SPEED_CSV, newline="") as f:
            rows = [r for r in csv.reader(f) if len(r) >= 4]
    except OSError:
        return []
    return [{"time": r[0], "model": r[1], "gen": r[2], "prompt": r[3], "vram": r[4] if len(r) > 4 else ""}
            for r in rows][-100:][::-1]
