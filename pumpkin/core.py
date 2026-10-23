"""Everything Pumpkin does, in one object the HTTP API calls into."""
import json
import re
import os
import shutil
import sys
import threading
import time

from . import (__version__, agent, config, devices, docs, hardware, llama, memory, migrate, models, recommend, remote,
               updates, workspace)


def _python():
    """The Python for tool servers. On Windows that is python.exe even when Pumpkin itself runs windowless with
    pythonw.exe, so the servers' pipes work; llama-server starts them without a window either way."""
    exe = sys.executable
    if os.name == "nt" and os.path.basename(exe).lower() == "pythonw.exe":
        alt = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(alt):
            return alt
    return exe


class Core:
    peers = ()                               # paired devices as they answered lately (filled in the background)
    peers_at = 0
    indexer = None

    def __init__(self):
        config.ensure_dirs()
        self.migrated = []
        if migrate.available():
            try:
                self.migrated = migrate.run()
            except Exception as e:  # noqa: BLE001 - a failed import must not stop startup
                self.migrated = [f"import failed: {e}"]
        self.settings = config.Settings()
        self.presets = config.Collection("presets", [])
        self.personas = config.Collection("personas", config.DEFAULT_PERSONAS)
        self.prompts = config.Collection("prompts", config.DEFAULT_PROMPTS, key="title")
        self.tasks = config.Collection("tasks", config.DEFAULT_TASKS)
        self.library = models.Library(self.settings)
        self.downloads = models.Downloads(self.settings, self.library)
        if not hardware.ANDROID:
            llama.kill_leftover()            # a model server left over from before still holds memory and the port
        self.server = llama.ServerManager(self.settings, self.library)
        self.server.persona_prompt = self.persona_prompt
        self.server.mcp_config = self.mcp_config
        self.server.on_recover = self._keep_working_ctx
        self.reports = agent.Reports(self.settings)
        self.runner = agent.Runner(self)
        self.helper = devices.Helper(self)
        self.indexer = None if hardware.ANDROID else docs.Indexer(self.settings)
        self.peers = []                      # paired devices as they answered lately: online, models
        self.peers_at = 0
        threading.Thread(target=self._watch_peers, daemon=True).start()
        self.jobs = {}                       # background jobs like downloading llama-server
        self.bench_state = {"running": False, "error": None, "last": None}
        self.hw = None
        self.hw_at = 0
        threading.Thread(target=self._check_update, daemon=True).start()
        if self.settings["autostart_model"]:
            p = self.presets.get(self.settings["last_preset"])
            if p:
                threading.Thread(target=self._safe_start, args=(p,), daemon=True).start()

    def stop_if_using(self, preset_id=None, path=None):
        """Deleting the running preset or its model file stops the model first: the memory is freed at once, and
        Windows cannot delete a file that is open."""
        cur = self.server.preset or {}
        if self.server.state not in ("loading", "ready", "error") or not cur:
            return False
        hit = preset_id and cur.get("id") == preset_id
        if path and not hit:
            real = os.path.realpath(path)
            stem = re.sub(r"-\d{5}-of-\d{5}\.gguf$", "", real)          # any part of a split model
            for ref in (cur.get("model"), cur.get("mmproj")):
                m = self.library.resolve(ref) if ref else None
                p = os.path.realpath(m["path"] if m else ref or "")
                if p and (p == real or re.sub(r"-\d{5}-of-\d{5}\.gguf$", "", p) == stem):
                    hit = True
        if hit:
            self.server.stop()
        return bool(hit)

    def _keep_working_ctx(self, preset):
        """A model loaded only with a shorter context: note that next to the preset, so next time it starts first try.
        The context you chose stays as it is; choosing another one makes Pumpkin try that instead."""
        pid = preset.get("id")
        if pid and self.presets.get(pid) and preset.get("_asked"):
            self.presets.update(pid, {"ctx_worked": int(preset["ctx"]), "ctx_asked": int(preset["_asked"])})

    def _check_update(self):
        time.sleep(20)
        try:
            if self.settings["check_updates"]:
                updates.check()
        except Exception:  # noqa: BLE001 - never bother anyone because GitHub is unreachable
            pass

    def _safe_start(self, p):
        try:
            self.server.start(p)
        except Exception:  # noqa: BLE001
            pass

    # ---- helpers
    def hardware(self, fresh=False):
        if fresh or not self.hw or time.time() - self.hw_at > 300:
            self.hw = hardware.summary()
            self.hw_at = time.time()
        return self.hw

    def fit_hw(self):
        h = self.hardware()
        g = h.get("main_gpu") or {}
        out = {"vram_total": g.get("vram_total") or 0, "ram_total": h.get("ram_total") or 0}
        if g.get("unified"):
            out["unified"] = True
        if g.get("phone"):
            out["phone"] = True
        return out

    def persona_prompt(self):
        """The chat's system message: the persona, what the tools are for, and what Pumpkin remembers about you."""
        p = self.personas.get(self.settings["persona"])
        text = (p or {}).get("prompt", "")
        pre = (self.server.preset or {}) if self.server.state in ("loading", "ready") else {}
        tools = bool(pre.get("tools")) and not hardware.ANDROID
        if tools:
            text += ("\n\nThe user's own documents are searchable with files_search. When a question may be about "
                     "their files (contracts, reports, letters, notes), search them first, answer from the passages "
                     "and cite each fact as (file, page). When they ask you to write a document, report, letter or "
                     "table, save it with files_save (Word unless they want another format) and tell them the name.")
            if self.settings["memory"]:
                text += (" When they tell you a lasting fact about themselves or ask you to remember something, save "
                         "it with memory_save; when they ask you to forget it, use memory_forget.")
            hint = self.devices_hint()
            if hint:
                text += "\n\n" + hint
        if pre.get("tools") and pre.get("sandbox", True) and workspace.docker_usable():
            text += ("\n\nYour sandbox's /workspace is the same folder as the user's Files. Save files you make "
                     "there.")
        if self.settings["memory"]:
            block = memory.prompt_block()
            if block:
                text += "\n\n" + block
        return text.strip()

    # ---- paired devices: their models in the chat, and when the model should ask them
    def refresh_peers(self, statuses=None):
        if statuses is None:
            statuses = devices.statuses(timeout=4) if any(d.get("enabled", True) for d in devices.load()) else []
        self.peers = statuses
        self.peers_at = time.time()
        return statuses

    def _watch_peers(self):
        time.sleep(3)
        while True:
            try:
                self.refresh_peers()
            except Exception:  # noqa: BLE001 - try again next round
                pass
            time.sleep(60)

    def peer_models(self):
        """Models on paired devices that answer right now, as the chat names them: "Qwen3 27B (on Gaming PC)"."""
        out = []
        for d in self.peers:
            if not d.get("online") or not d.get("enabled", True):
                continue
            info = d.get("info") or {}
            for m in info.get("models") or []:
                out.append({"id": f"{m} (on {d.get('name')})", "device": d["id"], "device_name": d.get("name"),
                            "model": m, "loaded": info.get("model") == m and info.get("state") == "ready"})
        return out

    def find_peer_model(self, ref):
        ref = (ref or "").strip().lower()
        if not ref or "(on " not in ref:
            return None
        return next((m for m in self.peer_models() if m["id"].lower() == ref), None)

    def devices_hint(self):
        """Tells the model which stronger devices it can hand work to, when there are any online."""
        online = [d for d in self.peers if d.get("online") and d.get("enabled", True)]
        if not online:
            return ""
        lines = []
        for d in online:
            models = ", ".join((d.get("info") or {}).get("models") or []) or "its usual model"
            lines.append(f"- {d.get('name')}: {models}")
        return ("Paired devices you can hand work to with devices_ask:\n" + "\n".join(lines) + "\nAsk one when a "
                "question needs more knowledge or careful reasoning than you are sure of, or a long text needs "
                "summarising, and its model is bigger than yours. Say which device answered.")

    # ---- switching models from the chat
    _switch_lock = threading.Lock()

    def find_preset(self, ref):
        ref = (ref or "").strip().lower()
        if not ref:
            return None
        for p in self.presets.all():
            if ref in ((p.get("id") or "").lower(), (p.get("name") or "").lower()):
                return p
        return None

    def ensure_preset(self, ref):
        """The chat asked for a model by preset name: start that preset if it is not the one running."""
        p = self.find_preset(ref)
        if not p or self.server.state == "external":
            return None
        with self._switch_lock:
            cur = self.server.preset or {}
            if not (self.server.state in ("ready", "loading") and cur.get("id") == p.get("id")):
                self.server.start(p)
            if not self.server.wait_ready(timeout=900):
                raise RuntimeError(self.server.error or f"{p.get('name')} did not start. Open Pumpkin to see why.")
        return p

    def model_list(self):
        """/v1/models for the chat: every preset, with the running one marked."""
        cur = (self.server.preset or {}) if self.server.state in ("ready", "loading") else {}
        data = []
        for p in self.presets.all():
            name = p.get("name") or p.get("id")
            data.append({"id": name, "object": "model", "created": 0, "owned_by": "pumpkin",
                         "status": {"value": "loaded" if cur.get("id") == p.get("id") and self.server.state == "ready"
                                    else "loading" if cur.get("id") == p.get("id") else "unloaded"}})
        for m in self.peer_models():                 # the models of paired devices, answered over there
            data.append({"id": m["id"], "object": "model", "created": 0, "owned_by": "device",
                         "status": {"value": "loaded" if m["loaded"] else "unloaded"}})
        return data

    def default_preset(self):
        last = self.presets.get(self.settings["last_preset"])
        if last:
            return last
        items = self.presets.all()
        return items[0] if items else None

    def mcp_config(self, preset):
        """The MCP file handed to llama-server: your servers plus the built-in web search."""
        servers = {}
        user = os.path.join(config.CONFIG_DIR, "mcp.json")
        try:
            with open(user) as f:
                servers.update((json.load(f) or {}).get("mcpServers") or {})
        except (OSError, ValueError, AttributeError):
            pass
        if preset.get("web", True) and not hardware.ANDROID:     # the app has no separate Python to run it
            pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env = {"PYTHONPATH": pkg_parent}
            for k in ("PUMPKIN_SEARXNG", "BRAVE_API_KEY"):
                if os.environ.get(k):
                    env[k] = os.environ[k]
            servers.setdefault("web", {"command": _python(), "args": ["-m", "pumpkin.mcp_web"], "env": env})
        if not hardware.ANDROID:
            # your documents and files the model saves for you, and what it remembers about you
            servers.setdefault("files", self._builtin("pumpkin.mcp_files", 300_000))
            if self.settings["memory"]:
                servers.setdefault("memory", self._builtin("pumpkin.mcp_memory"))
        if any(d.get("enabled", True) for d in devices.load()) and not hardware.ANDROID:
            # a phone can take minutes, so the call may wait up to 30 minutes
            servers.setdefault("devices", self._builtin("pumpkin.mcp_devices", 1_800_000))
        if not servers:
            return None
        path = os.path.join(config.STATE_DIR, "mcp-effective.json")
        with open(path, "w") as f:
            json.dump({"mcpServers": servers}, f, indent=2)
        return path

    @staticmethod
    def _builtin(module, timeout_ms=None):
        """One of Pumpkin's own tool servers, run with this Python and these settings folders."""
        pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = {"PYTHONPATH": pkg_parent, "PYTHONUTF8": "1"}
        for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                  "XDG_STATE_HOME"):
            if os.environ.get(k):
                env[k] = os.environ[k]
        out = {"command": _python(), "args": ["-m", module], "env": env}
        if timeout_ms:
            out["timeout_ms"] = timeout_ms
        return out

    # ---- presets
    def preset_from_model(self, path, name=None):
        m = self.library.resolve(path)
        if not m:
            raise RuntimeError("That model is not in the library.")
        info = m.get("info") or {}
        kv = "q8_0"
        ctx = models.best_ctx(m["size_gb"], info, kv, self.fit_hw(), cap=131072)
        label = name or (info.get("name") or m["file"].replace(".gguf", ""))
        q = m.get("quant")
        if q and q.lower() not in label.lower():
            label = f"{label} {q}"
        llama_info = llama.probe(llama.find_server(self.settings)) or {}
        tools_ok = "--agent" in (llama_info.get("flags") or []) or not llama_info.get("flags")
        p = {"name": label, "desc": "", "model": m["path"], "mmproj": m.get("mmproj") or "", "ctx": ctx, "kv": kv,
             "fitt": 1024, "think_budget": None, "tools": tools_ok, "sandbox": True, "web": tools_ok, "extra": ""}
        return self.presets.add(p)

    # ---- recommendations and one-click install
    _plans = {}
    _recs = None

    GOALS = {"chat": "Assistant", "coding": "Coder", "research": "Researcher", "agent": "Assistant"}

    def apply_goal(self, goal):
        """The setup wizard's question: what will this be used for? It picks the persona."""
        if goal not in self.GOALS:
            raise RuntimeError("Unknown goal.")
        name = self.GOALS[goal]
        if self.personas.get(name):
            self.settings.update({"persona": name})
        return {"persona": self.settings["persona"]}

    @staticmethod
    def _goal_rank(goal, m):
        text = f"{m.get('name', '')} {m.get('id', '')}".lower()
        if goal == "coding":
            return 0 if re.search(r"coder|code|devstral", text) else 1
        if goal in ("research", "agent"):
            return 0 if m.get("tools") else 1
        return 0

    def recommendations(self, refresh=False, goal=None):
        """Two lists for this machine: models that fit the GPU, and MoE models that are fine in RAM."""
        hw = self.fit_hw()
        curated = models.catalog()
        source, at, stale, error = "huggingface", None, False, None
        try:
            data = recommend.fetch(self.settings["hf_token"] or None, force=refresh)
            items, at, stale = data["items"], data.get("at"), bool(data.get("stale"))
        except Exception as e:  # noqa: BLE001 - fall back to the hand-picked list
            items, source, error = [], "offline", str(e)
        have = {it.get("id", "").lower() for it in items}
        items = items + [c for c in recommend.curated_items(curated) if c["id"].lower() not in have]
        local = [re.sub(r"[-_.]", "", os.path.basename(m["path"]).lower()) for m in self.library.scan()]
        recs = recommend.recommend(items, hw, local, curated)
        if goal:
            for k in ("gpu", "moe"):
                recs[k] = sorted(recs[k], key=lambda m: self._goal_rank(goal, m))   # stable: keeps the ranking
        self._recs = {d["id"]: d for r in recs.values() for d in r}
        return {"gpu": recs["gpu"], "moe": recs["moe"], "hardware": hw, "source": source, "updated": at,
                "stale": stale, "error": error}

    def _spec(self, ident):
        if self._recs is None:
            self.recommendations()
        d = (self._recs or {}).get(ident)
        if d:
            return {"id": d["id"], "name": d["name"], "repo": d["repo"], "moe": d["route"] == "moe",
                    "vision": d.get("vision"), "ctx": d["ctx"], "blurb": d.get("blurb", "")}
        c = next((x for x in models.catalog() if x["id"] == ident), None)
        if c:
            return c
        raise RuntimeError("Unknown model. Refresh the recommendations and try again.")

    def catalog_plan(self, ident):
        """The exact files to download for a recommendation, from the repo's real file list."""
        c = self._spec(ident)
        cached = self._plans.get(ident)
        if cached and time.time() - cached["at"] < 3600:
            return cached
        hw = self.fit_hw()
        choices = models.hf_files(c["repo"], hw, self.settings["hf_token"] or None)
        pick, f = models.pick_quant(choices, hw, int(c.get("ctx") or 16384), bool(c.get("moe")))
        mm = models.pick_mmproj(choices) if c.get("vision") else None
        plan = {"id": ident, "at": time.time(), "file": pick, "fit": f, "mmproj": mm,
                "size_gb": round((pick or {}).get("size_gb", 0) + (mm or {}).get("size_gb", 0), 1),
                "text": models.fit_label(f) if f else "Too big for this machine"}
        self._plans[ident] = plan
        return plan

    def install(self, ident, start=True):
        """Download the best quant for this machine, make a preset, and start it."""
        c = self._spec(ident)

        def fn(progress, cancelled, job):
            job["phase"] = "Choosing the right size for your computer"
            plan = self.catalog_plan(ident)
            if not plan.get("file"):
                raise RuntimeError(f"No file in {c['repo']} fits this computer.")
            files = list(plan["file"]["files"]) + (list(plan["mmproj"]["files"]) if plan.get("mmproj") else [])
            dl = self.downloads.add(c["repo"], files, config.slug(c["name"]))
            job["phase"] = f"Downloading {plan['file']['quant'] or ''} ({plan['size_gb']} GB)".replace("  ", " ")
            while dl["state"] in ("queued", "downloading"):
                if cancelled():
                    self.downloads.cancel(dl["id"])
                    raise RuntimeError("Cancelled")
                progress(dl["done"], dl["total"])
                time.sleep(0.5)
            if dl["state"] != "done":
                raise RuntimeError(dl.get("error") or "The download did not finish.")
            job["phase"] = "Creating a preset"
            self.library.scan(force=True)
            path = os.path.join(dl["dest"], os.path.basename(plan["file"]["files"][0]))
            existing = next((p for p in self.presets.all() if p.get("model") == path), None)
            preset = existing or self.preset_from_model(path, c["name"])
            if c.get("ctx") and not existing:
                m = self.library.resolve(path)
                best = models.best_ctx(m["size_gb"], m.get("info") or {}, "q8_0", self.fit_hw(),
                                       cap=int(c["ctx"]) if not c.get("moe") else 65536)
                preset = self.presets.update(preset["id"], {"ctx": best, "desc": c.get("blurb", "")})
            if start:
                job["phase"] = "Starting the model"
                self.server.start(preset)
            job["phase"] = "Ready"
            return {"preset": preset["id"]}

        self.settings.update({"setup_done": True})
        return self.start_job("install", fn, model=c["name"], phase="Starting")

    # ---- state for the UI
    def state(self):
        live = hardware.live()
        srv = self.server.status()
        met = llama.metrics(self.server.port) if srv["state"] in ("ready", "external") else {}
        r = self.runner
        return {
            "version": __version__, "server": srv, "live": live,
            "metrics": {k: met.get(k) for k in ("predicted_tokens_seconds", "prompt_tokens_seconds",
                                                 "tokens_predicted_total", "n_tokens_max")},
            "agent": {"busy": r.busy, "job": (r.current or {}).get("name"),
                      "log_seq": r.log_seq},
            "downloads": sum(1 for j in self.downloads.list() if j["state"] in ("queued", "downloading")),
            "installs": [{k: j.get(k) for k in ("id", "model", "phase", "state", "done", "total", "error")}
                         for j in self.jobs.values() if j["kind"] == "install"
                         and (j["state"] == "running" or time.time() - j.get("ended", time.time()) < 60)],
            "persona": self.settings["persona"],
            "persona_prompt": self.persona_prompt(),
            "setup_done": bool(self.settings["setup_done"]),
            "has_server": bool(llama.find_server(self.settings)),
            "has_models": any(m["kind"] == "model" for m in self.library.scan()),
            "migrated": self.migrated,
            "update": updates.cached_status(),
            "platform": "android" if hardware.ANDROID else sys.platform,
            "remote": self.settings["remote"],
        }

    # ---- background jobs (llama-server download)
    def start_job(self, kind, fn, **extra):
        job = {"id": config.new_id(), "kind": kind, "state": "running", "done": 0, "total": 0, "error": None,
               "result": None, "cancel": False, **extra}
        self.jobs[job["id"]] = job

        def progress(done, total):
            job["done"], job["total"] = done, total

        def run():
            try:
                job["result"] = fn(progress, lambda: job["cancel"], job)
                job["state"] = "done"
            except Exception as e:  # noqa: BLE001
                job["state"], job["error"] = "error", models._friendly(e)
            job["ended"] = time.time()
        threading.Thread(target=run, daemon=True).start()
        return job

    def download_llama(self, backend):
        def fn(progress, cancelled, job):
            path = llama.download_release(backend, progress=progress, cancelled=cancelled)
            self.settings.update({"llama_server": path})
            return path
        return self.start_job("llama", fn)

    def run_bench(self):
        if self.bench_state["running"]:
            raise RuntimeError("A benchmark is already running.")
        if self.server.state not in ("ready", "external"):
            raise RuntimeError("Start a model first.")
        label = (self.server.preset or {}).get("name") or "external server"

        def run():
            self.bench_state.update(running=True, error=None)
            try:
                self.bench_state["last"] = agent.speed_bench(self.server.port, label,
                                                             hardware.live().get("vram_used"))
            except Exception as e:  # noqa: BLE001
                self.bench_state["error"] = str(e)
            finally:
                self.bench_state["running"] = False
        threading.Thread(target=run, daemon=True).start()

    # ---- remote access
    _ts_on = False

    def set_remote(self, mode):
        """Tailscale can be picked before it is ready: the Phone page then shows the steps left, and Pumpkin
        publishes itself on the tailnet as soon as they are done."""
        if mode != "tailscale" and self.settings["remote"] == "tailscale":
            remote.tailscale_off()
        self._ts_on = False
        self.settings.update({"remote": mode})
        return self.remote_status()

    def remote_status(self):
        st = remote.status(self.settings, config.token())
        st["error"] = None
        if st["mode"] == "tailscale":
            t = st.get("tailscale") or {}
            if not (t.get("signed_in") and t.get("magic_dns") and t.get("https")):
                st["base"] = st["link"] = None
            elif not self._ts_on:
                try:
                    remote.tailscale_on(int(self.settings["port"]))
                    self._ts_on = True
                except RuntimeError as e:
                    st["base"] = st["link"] = None
                    st["error"] = str(e)
        return st

    def set_share(self, on):
        """Sharing needs Pumpkin to listen beyond this computer. If it only listened here, open it to the home
        network (and Tailscale's network, which reaches it the same way), and undo that when sharing stops."""
        s = self.settings
        changes = {"share": bool(on)}
        listen = None
        if on and s["remote"] == "off":
            self.set_remote("lan")
            changes["share_opened_lan"], listen = True, "0.0.0.0"
        elif not on and s["share_opened_lan"]:
            if s["remote"] == "lan":
                self.set_remote("off")
                listen = "127.0.0.1"
            changes["share_opened_lan"] = False
        s.update(changes)
        return listen

    def devices_loaded(self):
        """Whether the running model already has the devices tools (they are added when a model starts)."""
        if self.server.state != "ready":
            return None
        tools = llama.http(self.server.port, "/tools", timeout=5)
        if not isinstance(tools, list):
            return False
        return any(str(t.get("tool", "")).startswith("devices_") for t in tools if isinstance(t, dict))

    def share_status(self):
        s = self.settings
        return {"share": bool(s["share"]), "name": devices.device_name(s), "custom_name": s["device_name"],
                "code": devices.pairing_code(s) if s["share"] else None, "addresses": devices.addresses(s),
                "remote": s["remote"], "recent": self.helper.recent, "android": hardware.ANDROID}

    def shutdown(self):
        try:
            self.server.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            workspace.fix_owner(self.settings)
            workspace.stop()
        except Exception:  # noqa: BLE001
            pass
