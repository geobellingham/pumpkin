"""Everything Pumpkin does, in one object the HTTP API calls into."""
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time

from . import (__version__, agent, coder, config, connectors, devices, docs, hardware, images, llama, mcp_local, memory, migrate, models,
               recommend, remote, skills, updates, vision, workspace)

# which setting holds the chosen model for each kind of image model
IMAGE_KEYS = {"draw": "image_model", "edit": "edit_model", "video": "video_model"}


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
    mcp_host = None
    image_server = None
    last_picture = None                  # (when first seen, bytes) of the newest picture in a chat request
    mcp_devices = False                  # the running text model was started with devices_ask
    mcp_image = False                    # the running text model was started with image_generate
    chat_tokens = 0                      # about how much of the model's memory the newest chat request uses
    chat_at = 0.0
    chat_note = None                     # (when, text) the chat's older part was summarised
    chat_summary = None                  # {"n", "hash", "text"} the summary the chat is using
    _decode = None                       # (tokens written, when it last grew, when read) for decode_progress

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
            images.kill_leftover()
            vision.kill_leftover()
        self.server = llama.ServerManager(self.settings, self.library)
        self.server.persona_prompt = self.persona_prompt
        self.server.mcp_config = self.mcp_config
        self.server.on_recover = self._keep_working_ctx
        self.reports = agent.Reports(self.settings)
        self.runner = agent.Runner(self)
        self.helper = devices.Helper(self)
        self.indexer = docs.Indexer(self.settings)
        self.image_server = images.ImageServer(self.settings)
        self.vision = vision.Helper(self.settings, self.library)
        self.code = coder.Manager(self)
        self.vision_seen = {}                 # picture hash -> what the vision model said (a chat re-sends its pictures)
        self.image_lock = threading.Lock()
        self.pictures_seen = {}
        self.image_sizes = {}                # bundle id -> estimated GB before it is downloaded
        if hardware.ANDROID:
            # the app has no Python program for llama-server to start: its tools answer from here (mcp_local)
            self.mcp_host = mcp_local.Host(os.environ.get("PUMPKIN_MCP_BRIDGE"))
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
        if pid and self.presets.get(pid) and preset.get("_no_mmproj"):
            self.presets.update(pid, {"mmproj": ""})
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
        tools = bool(pre.get("tools")) and self.tools_here()
        ctx = int(pre.get("ctx") or 8192)
        # every word here is sent with every message; on a 4-8k model it has to stay small
        if tools:
            text += ("\n\nTools: files_search for the user's own files (cite file, page), files_save to save documents "
                     "you write")
            text += (", memory_save for lasting facts they tell you about themselves." if self.settings["memory"]
                     else ".")
            if self.images_ready():
                text += " image_generate draws a picture when the user asks for one."
            if self.edit_ready():
                text += " image_edit changes the picture the user attached or you drew last."
            if self.video_ready():
                text += " image_video makes a short video clip (slow: minutes)."
            hint = self.devices_hint()
            if hint:
                text += " " + hint
            if self.skills_on():
                block = skills.listing(skills.enabled(self.settings),
                                       limit_chars=700 if ctx <= 8192 else 1800 if ctx <= 32768 else 4000)
                if block:
                    text += ("\n\nSkills: instructions for particular jobs. When a request matches one, call "
                             "skills_read with its name first and follow it.\n" + block)
        if pre.get("tools") and pre.get("sandbox", True) and workspace.docker_usable():
            text += " Your sandbox's /workspace is the user's Files folder."
        if self.settings["memory"]:
            budget = 600 if ctx <= 4096 else 1300 if ctx <= 8192 else 2600 if ctx <= 16384 else memory.PROMPT_CHARS
            block = memory.prompt_block(limit=budget)
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
        """Tells the model which other devices it can hand work to, when there are any online."""
        online = [d for d in self.peers if d.get("online") and d.get("enabled", True)]
        if not online or not self.mcp_devices:        # a model started before pairing has no devices_ask: say nothing
            return ""
        names = "; ".join(f"{d.get('name')} ({', '.join(((d.get('info') or {}).get('models') or [])[:3]) or 'a model'})"
                          for d in online)
        return (f"devices_ask hands a self-contained task to a model on another device: a stronger one for hard questions, "
                f"a small fast one for simple jobs: {names}.")

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
        try:
            sees = bool(self.settings["vision_relay"]) and bool(self.vision.model())
        except Exception:  # noqa: BLE001 - no vision helper (yet)
            sees = False
        for p in self.presets.all():
            name = p.get("name") or p.get("id")
            data.append({"id": name, "object": "model", "created": 0, "owned_by": "pumpkin",
                         "architecture": {"input_modalities": ["text", "image"] if p.get("mmproj") or sees
                                          else ["text"], "output_modalities": ["text"]},
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
        self.mcp_image = False
        self.mcp_devices = False
        user = os.path.join(config.CONFIG_DIR, "mcp.json")
        try:
            with open(user) as f:
                servers.update((json.load(f) or {}).get("mcpServers") or {})
        except (OSError, ValueError, AttributeError):
            pass
        if self.mcp_host:                                       # Android: the tools answer from this process
            self._app_tools(servers, preset)
        elif not hardware.ANDROID:
            self._computer_tools(servers, preset)
            try:
                for name, e in connectors.servers_for(preset).items():
                    servers.setdefault(name, e)          # your connectors (Connectors page)
            except Exception:  # noqa: BLE001 - a broken connector file must not stop the model
                pass
        if not servers:
            return None
        path = os.path.join(config.STATE_DIR, "mcp-effective.json")
        # it holds your connector tokens, so only you can read it (like connectors.json)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w") as f:
            json.dump({"mcpServers": servers}, f, indent=2)
        return path

    def skills_on(self):
        """Skills are switched on and at least one is turned on."""
        try:
            return bool(self.settings["skills"]) and bool(skills.enabled(self.settings))
        except Exception:  # noqa: BLE001 - a broken skill folder must not stop a model from starting
            return False

    def tools_here(self):
        """Pumpkin's own tools work here: always on a computer, on Android when the app has its tool relay."""
        return not hardware.ANDROID or bool(self.mcp_host and self.mcp_host.usable())

    def _app_tools(self, servers, preset):
        """Android: llama-server starts the app's relay, which passes each call to this process (mcp_local)."""
        if not self.mcp_host.usable():
            return                                              # an app built without the relay
        h = self.mcp_host
        if preset.get("web", True):
            servers.setdefault("web", h.command("web"))
        servers.setdefault("files", h.command("files", 300_000))
        if self.settings["memory"]:
            servers.setdefault("memory", h.command("memory"))
        if self.skills_on():
            servers.setdefault("skills", h.command("skills"))
        if any(d.get("enabled", True) for d in devices.load()):
            servers.setdefault("devices", h.command("devices", 1_800_000))

    def _computer_tools(self, servers, preset):
        """Pumpkin's tool servers as Python programs that llama-server starts."""
        if preset.get("web", True):
            pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env = {"PYTHONPATH": pkg_parent, "PYTHONUTF8": "1"}
            for k in ("PUMPKIN_SEARXNG", "BRAVE_API_KEY"):
                if os.environ.get(k):
                    env[k] = os.environ[k]
            servers.setdefault("web", {"command": _python(), "args": ["-m", "pumpkin.mcp_web"], "env": env})
        # your documents and files the model saves for you, and what it remembers about you
        servers.setdefault("files", self._builtin("pumpkin.mcp_files", 300_000))
        if self.settings["memory"]:
            servers.setdefault("memory", self._builtin("pumpkin.mcp_memory"))
        if self.skills_on():
            servers.setdefault("skills", self._builtin("pumpkin.mcp_skills"))
        if any(d.get("enabled", True) for d in devices.load()):
            # a phone can take minutes, so the call may wait up to 30 minutes
            servers.setdefault("devices", self._builtin("pumpkin.mcp_devices", 1_800_000))
        if self.images_ready() or self.edit_ready() or self.video_ready():
            # the chat's calls are answered by Pumpkin itself (server.py); this one serves the listing and agents
            # a clip can take up to two hours (images.generate_video), so the call may wait that long
            servers.setdefault("image", self._builtin("pumpkin.mcp_images", 7_200_000))
        self.mcp_image = "image" in servers
        self.mcp_devices = "devices" in servers

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
            for k in ("gpu", "moe", "slow"):
                recs[k] = sorted(recs.get(k, []), key=lambda m: self._goal_rank(goal, m))   # stable: keeps the ranking
        self._recs = {d["id"]: d for r in recs.values() for d in r}
        return {"gpu": recs["gpu"], "moe": recs["moe"], "slow": recs.get("slow", []), "hardware": hw,
                "source": source, "updated": at, "stale": stale, "error": error}

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
        proc = getattr(self.server, "proc", None)
        mapped = hardware.mapped_ram(proc.pid) if proc and proc.poll() is None else 0.0
        if mapped > 0.1 and live.get("ram_total"):
            # the model's file kept in RAM. The system counts it as cache, not as used, and so does every system
            # monitor, so "ram_used" stays what they show and the cached part is reported next to it
            live["model_ram"] = round(min(mapped, live["ram_total"]), 1)
        srv = self.server.status()
        met = llama.metrics(self.server.port) if srv["state"] in ("ready", "external") else {}
        r = self.runner
        return {
            "version": __version__, "now": time.time(), "server": srv, "live": live,
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
            "activity": self.activity(met, srv),
            "metrics_more": {k: met.get(k) for k in ("requests_processing", "requests_deferred",
                                                      "n_decode_total", "prompt_tokens_total")},
        }

    # ---- what is happening right now
    def decode_progress(self):
        """How many tokens the model has written for the answer in progress, read from llama-server itself. It moves
        while a long tool call is being written, even when the text reaches the screen in one piece at the end.
        Returns None when the server says it is idle (a "unknown": True answer when it does not say), else
        {"tokens", "stalled"}: stalled is the seconds the count has
        not grown since the model began writing (0 while it still reads the prompt)."""
        if self.server.state not in ("ready", "external"):
            self._decode = None
            return None
        now = time.time()
        last = self._decode
        if last and now - last[2] < 0.8:                 # polled twice at once: one question to the server
            return last[3]
        r = llama.http(int(self.settings["llama_port"]), "/slots", timeout=1)
        if not isinstance(r, list):
            return {"tokens": 0, "stalled": 0, "unknown": True}      # this server does not say what it is doing
        n, busy = 0, False
        for sl in r if isinstance(r, list) else []:
            if isinstance(sl, dict) and sl.get("is_processing"):
                busy = True
                nt = sl.get("next_token")
                nt = nt[0] if isinstance(nt, list) and nt else nt
                if isinstance(nt, dict):
                    try:
                        n += int(nt.get("n_decoded") or 0)
                    except (TypeError, ValueError):
                        pass
        if not busy:
            self._decode = None
            return None
        moved = last[1] if last and last[0] == n else now
        out = {"tokens": n, "stalled": round(now - moved) if n > 0 else 0}
        self._decode = (n, moved, now, out)
        return out

    def activity(self, met=None, srv=None):
        """Everything Pumpkin is busy with, one entry each, for the Overview and Pumpkin Code. An entry:
        id, kind, title, state (working | loading | paused | idle), label, detail, progress (0..1 or None), since."""
        now = time.time()
        out = []
        srv = srv or self.server.status()
        met = met or {}
        pre = srv.get("preset") or self.server.preset or {}
        picture = self.image_server.progress if self.image_server else None
        if srv["state"] == "loading":
            out.append({"id": "text", "kind": "model", "title": pre.get("name") or "Model", "state": "loading",
                        "label": "Loading into memory", "since": self.server.started_at or now})
        elif srv["state"] in ("ready", "external"):
            busy = bool(met.get("requests_processing"))
            ctx = int(pre.get("ctx") or 0)
            used = int(self.chat_tokens) if self.chat_tokens and now - self.chat_at < 6 * 3600 else 0
            item = {"id": "text", "kind": "model", "title": pre.get("name") or "Model",
                    "state": "working" if busy else "idle", "label": "Answering" if busy else "Ready"}
            prog = self.decode_progress() if busy else None
            if busy and prog and prog["tokens"]:
                item["detail"] = f"{prog['tokens']} tokens so far" + (
                    f", {met['predicted_tokens_seconds']:.1f} a second" if met.get("predicted_tokens_seconds") else "")
                if prog["stalled"] >= 90:
                    item["label"] = "Answering, but no new tokens for " + (
                        f"{prog['stalled'] // 60} min" if prog["stalled"] >= 120 else f"{prog['stalled']} s")
            elif busy and met.get("predicted_tokens_seconds"):
                item["detail"] = f"{met['predicted_tokens_seconds']:.1f} tokens a second"
            if ctx and used:
                item["context"] = {"used": min(used, ctx), "total": ctx}
            if self.chat_note and now - self.chat_note[0] < 90:
                item["note"] = self.chat_note[1]
            out.append(item)
        elif picture and picture.get("swapped"):
            out.append({"id": "text", "kind": "model", "title": pre.get("name") or "Model", "state": "paused",
                        "label": "Paused so the picture has room"})
        v = self.vision.status()
        if v["state"] in ("loading", "ready") or v["busy"]:
            out.append({"id": "vision", "kind": "vision", "title": v.get("model") or "Vision model",
                        "state": "working" if v["busy"] else ("loading" if v["state"] == "loading" else "idle"),
                        "label": "Looking at a picture" if v["busy"] else
                                 ("Loading into memory" if v["state"] == "loading" else "Loaded, frees itself when idle")})
        i = self.image_server.status() if self.image_server else {}
        if i.get("busy") or i.get("state") in ("loading", "ready"):
            stage = (picture or {}).get("stage")
            out.append({"id": "image", "kind": "image", "title": i.get("model") or "Image model",
                        "state": "working" if i.get("busy") and stage != "loading" else
                                 ("loading" if i.get("busy") or i.get("state") == "loading" else "idle"),
                        "label": {"loading": "Loading into memory", "drawing": "Drawing", "video": "Making the video"}
                                 .get(stage, "Working") if i.get("busy") else "Loaded, frees itself when idle",
                        "since": (picture or {}).get("since")})
        for d in self.downloads.list():
            if d["state"] in ("queued", "downloading"):
                out.append({"id": "dl-" + d["id"], "kind": "download", "title": d["repo"].split("/")[-1],
                            "state": "working", "label": "Downloading",
                            "progress": (d["done"] / d["total"]) if d.get("total") else None,
                            "detail": f"{d['speed'] / 1e6:.1f} MB a second" if d.get("speed") else None})
        for j in list(self.jobs.values()):
            if j["state"] == "running" and j["kind"] in ("install", "scans", "llama"):
                out.append({"id": "job-" + j["id"], "kind": "job", "title": j.get("model") or j["kind"].capitalize(),
                            "state": "working", "label": j.get("phase") or "Working",
                            "progress": (j["done"] / j["total"]) if j.get("total") else None})
        r = self.runner
        if r.busy:
            out.append({"id": "agent", "kind": "agent", "title": (r.current or {}).get("name") or "Task",
                        "state": "working", "label": "Running a scheduled task"})
        try:
            for s in [x.summary() for x in list(self.code.sessions.values())]:
                if s.get("phase"):
                    out.append({"id": "code-" + s["id"], "kind": "code", "title": s["title"], "state":
                                "waiting" if s["state"] == "waiting" else "working", "label": s["phase"]["label"],
                                "since": s["phase"]["since"]})
        except Exception:  # noqa: BLE001 - the list is for show
            pass
        return out

    def checklist(self):
        """The things that make Pumpkin better once they are set up, each done, skipped or still to do."""
        s = self.settings
        skipped = set(s["setup_skipped"] or [])
        phone = hardware.ANDROID
        draw = None if phone else (bool(images.find_engine(s)) and self.image_default("draw") is not None)
        sees = self.text_model_sees() or bool(self.vision.model()) or any(
            m.get("kind") == "model" and m.get("mmproj") for m in self.library.scan())
        steps = [
            ("engine", "Get the engine", "llama-server runs the models. Pumpkin downloads the right one for this computer.",
             "#/pumpkin/settings", bool(llama.find_server(s)), False),
            ("model", "Install a model", "Pick one that fits this computer. Pumpkin shows what runs well.",
             "#/pumpkin/models", any(m["kind"] == "model" for m in self.library.scan()), False),
            ("preset", "Start a model", "A preset is a model with its settings. Pumpkin makes one when a model is installed.",
             "#/pumpkin/presets", bool(self.presets.all()), False),
            ("vision", "Let the chat see pictures",
             "Attach a picture or a scanned page and the model reads it. A small vision model is enough, even when your "
             "main model cannot see.", "#/pumpkin/models", bool(sees), True),
            ("pictures", "Draw pictures",
             "Install the image engine and a model, then ask the chat to draw. Desktop only.",
             "#/pumpkin/images", bool(draw), True) if not phone else None,
            ("edit", "Change pictures you attach", "Qwen Image Edit changes a picture from a sentence.",
             "#/pumpkin/images", bool(self.image_default("edit")) and bool(images.find_engine(s)), True)
            if not phone else None,
            ("video", "Make short videos", "Wan 2.2 turns a sentence, or a picture, into a clip.",
             "#/pumpkin/images", bool(self.image_default("video")) and bool(images.find_engine(s)), True)
            if not phone else None,
            ("documents", "Search your documents",
             "Point Pumpkin at folders and the model finds answers in your own files, scans included.",
             "#/pumpkin/files", bool(s["doc_folders"]), True),
            ("connectors", "Connect your apps", "GitHub, Notion, Jira, a browser, a database: the model gets their "
             "tools. Desktop only.", "#/pumpkin/connectors",
             any(v.get("enabled") for v in connectors.load().values()), True) if not phone else None,
            ("code", "Try Pumpkin Code", "A coding agent that reads and edits a project folder. Desktop only.",
             "#/pumpkin/code", bool(s["code_recent"]), True) if not phone else None,
            ("tasks", "Schedule a task", "A morning briefing, or anything you want done at a set time.",
             "#/pumpkin/tasks", any(t.get("enabled") for t in self.tasks.all()), True),
            ("phone", "Use Pumpkin from your phone", "Chat with your models from anywhere, over your own network.",
             "#/pumpkin/phone", s["remote"] != "off", True) if not phone else None,
            ("notify", "Get a notification when a task finishes", "Pumpkin sends it to your phone through ntfy.",
             "#/pumpkin/settings", bool(s["ntfy_topic"]), True),
        ]
        out = []
        for st in steps:
            if not st:
                continue
            sid, title, text, href, done, optional = st
            out.append({"id": sid, "title": title, "text": text, "href": href, "done": bool(done),
                        "optional": optional, "skipped": optional and sid in skipped and not done})
        return {"steps": out, "hidden": bool(s["checklist_hidden"])}

    def skip_step(self, step_id, skipped=True):
        keep = [x for x in (self.settings["setup_skipped"] or []) if x != step_id]
        if skipped:
            keep.append(step_id)
        self.settings.update({"setup_skipped": keep})
        return self.checklist()

    # ---- long chats
    @staticmethod
    def _plain(m):
        """One chat message as text, pictures and tool calls as short notes."""
        c = m.get("content")
        if isinstance(c, list):
            c = " ".join(p.get("text", "") if p.get("type") == "text" else "[a picture]" for p in c if isinstance(p, dict))
        c = str(c or "")
        calls = "; ".join(f'{t.get("function", {}).get("name")}({str(t.get("function", {}).get("arguments"))[:200]})'
                          for t in m.get("tool_calls") or [] if isinstance(t, dict))
        return f"[{m.get('role')}] {c}" + (f" calls: {calls}" if calls else "")

    @classmethod
    def _chat_size(cls, req):
        """About how many tokens a chat request takes: three characters a token, a picture about 700."""
        n = 0
        for m in req.get("messages") or []:
            if isinstance(m, dict):
                n += len(cls._plain(m)) // 3 + 8 + 700 * sum(
                    1 for p in (m.get("content") if isinstance(m.get("content"), list) else [])
                    if isinstance(p, dict) and p.get("type") == "image_url")
        if req.get("tools"):
            n += len(json.dumps(req["tools"])) // 3
        return n

    @classmethod
    def _chat_hash(cls, msgs):
        return hashlib.sha1("\n".join(cls._plain(m) for m in msgs).encode("utf-8", "replace")).hexdigest()

    def prepare_chat(self, body):
        """A chat request is about to go to the model. Notes how full the model's memory is and, when the chat no
        longer fits, swaps its older part for a summary (the chat on screen keeps every message). Returns the body."""
        try:
            req = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return body
        msgs = req.get("messages") if isinstance(req, dict) else None
        if not isinstance(msgs, list) or not msgs or not all(isinstance(m, dict) for m in msgs):
            return body
        ctx = int((self.server.preset or {}).get("ctx") or 0)
        used = self._chat_size(req)
        self.chat_tokens, self.chat_at = used, time.time()
        if not ctx or not self.settings["compact_chat"] or used <= ctx * 0.8 or req.get("prompt"):
            return body
        lead = 0
        while lead < len(msgs) and msgs[lead].get("role") in ("system", "developer"):
            lead += 1
        rest = msgs[lead:]
        cache = self.chat_summary
        base, carried = None, ""
        if cache and len(rest) >= cache["n"] and self._chat_hash(rest[:cache["n"]]) == cache["hash"]:
            base, carried = cache["n"], cache["text"]
            small = dict(req, messages=msgs[:lead] + self._summary_pair(carried) + rest[base:])
            if self._chat_size(small) <= ctx * 0.8:
                return self._compacted(small, len(rest[:base]))
        users = [i for i, m in enumerate(rest) if m.get("role") == "user"]
        cut = users[-2] if len(users) >= 2 else (users[-1] if users else len(rest))
        if cut <= (base or 0):
            return body                                  # nothing older is left to summarise
        old = rest[(base or 0):cut]
        text = ("\n".join(self._plain(m)[:3000] for m in old))
        text = text[-int(ctx * 2.2):]
        ask = [{"role": "system", "content": "You summarise a conversation so it can continue."},
               {"role": "user", "content": "Summarise this conversation for whoever continues it: what the user wants, "
                "what was decided or found, names, numbers and anything still open. Keep their exact wording for "
                "instructions. Be brief.\n\n" + (f"Summary so far:\n{carried}\n\nThen:\n" if carried else "") + text}]
        r = llama.http(int(self.settings["llama_port"]), "/v1/chat/completions",
                       {"messages": ask, "max_tokens": 1200, "temperature": 0.2, "stream": False}, timeout=900)
        summary = ""
        if isinstance(r, dict) and not r.get("error"):
            summary = (((r.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not summary:
            return body                                  # the model answers with its own error
        self.chat_summary = {"n": cut, "hash": self._chat_hash(rest[:cut]), "text": summary}
        small = dict(req, messages=msgs[:lead] + self._summary_pair(summary) + rest[cut:])
        return self._compacted(small, cut)

    @staticmethod
    def _summary_pair(text):
        return [{"role": "user", "content": f"Summary of our conversation so far:\n{text}"},
                {"role": "assistant", "content": "Understood. I will continue from there."}]

    def _compacted(self, req, count):
        self.chat_tokens = self._chat_size(req)
        self.chat_note = (time.time(), f"The first {count} messages are summarised so the chat fits the model's memory. "
                                       f"The chat on screen still shows all of them.")
        return json.dumps(req).encode()

    # ---- pictures
    def image_default(self, kind="draw"):
        """The installed image model the chat draws (kind "draw") or edits pictures (kind "edit") with."""
        inst = {k: v for k, v in images.installed(self.settings).items()
                if v["ready"] and (v.get("kind") or "draw") == kind}
        want = self.settings[IMAGE_KEYS.get(kind, "video_model")]
        if want in inst:
            return inst[want]
        for b in images.catalog():                   # the catalog's order: the recommended one first
            if b["id"] in inst:
                return inst[b["id"]]
        return next(iter(inst.values()), None)

    def edit_ready(self):
        """The chat's model can change pictures: pictures are on, the engine and an editing model are in."""
        if hardware.ANDROID or not self.settings["images"] or not self.image_server:
            return False
        return bool(images.find_engine(self.settings)) and self.image_default("edit") is not None

    def video_ready(self):
        """The chat's model can make video clips: pictures are on, the engine and a video model are in."""
        if hardware.ANDROID or not self.settings["images"] or not self.image_server:
            return False
        return bool(images.find_engine(self.settings)) and self.image_default("video") is not None

    def note_picture(self, body):
        """A chat request went by: remember the newest picture in it, so image_edit knows what to change."""
        i = body.rfind(b"data:image/")
        if i < 0:
            return
        m = re.match(rb"data:image/(?:png|jpe?g|webp);base64,([A-Za-z0-9+/=]+)", body[i:])
        if not m:
            return
        try:
            data = base64.b64decode(m.group(1))
        except ValueError:
            return
        key = hashlib.sha1(data).hexdigest()
        if key not in self.pictures_seen:
            self.pictures_seen[key] = time.time()
            for k in list(self.pictures_seen)[:-20]:
                self.pictures_seen.pop(k, None)
        self.last_picture = (self.pictures_seen[key], data)

    def picture_to_edit(self, source=None):
        """Bytes of the picture to change: a file in the user's Files when named, otherwise the newest picture of the
        chat (one the user attached, or one drawn since: the newer of the two)."""
        root = os.path.realpath(os.path.expanduser(self.settings["files_dir"]))
        if source:
            p = os.path.realpath(os.path.join(root, str(source).lstrip("/\\")))
            try:
                inside = os.path.commonpath([root, p]) == root
            except ValueError:                   # another drive on Windows
                inside = False
            if not inside or not os.path.isfile(p) \
                    or not p.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                raise ValueError("That picture is not in the user's Files.")
            with open(p, "rb") as f:
                return f.read()
        newest = None
        folder = os.path.join(root, "images")
        if os.path.isdir(folder):
            for n in os.listdir(folder):
                fp = os.path.join(folder, n)
                if n.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) and os.path.isfile(fp):
                    t = os.path.getmtime(fp)
                    if newest is None or t > newest[0]:
                        newest = (t, fp)
        seen = self.last_picture
        if seen and (newest is None or seen[0] >= newest[0]):
            return seen[1]
        if newest:
            with open(newest[1], "rb") as f:
                return f.read()
        raise ValueError("There is no picture to change. Ask the user to attach one.")

    def images_ready(self):
        """The chat's model can draw: pictures are on, the engine and an image model are in."""
        if hardware.ANDROID or not self.settings["images"] or not self.image_server:
            return False
        return bool(images.find_engine(self.settings)) and self.image_default() is not None

    def images_state(self):
        hw = self.hardware()
        recommended = images.engine_backend(hw.get("gpus"))
        engine = images.engine_info(self.settings)
        backend = engine.get("backend") or recommended          # the labels follow the engine that is in
        inst = images.installed(self.settings)
        dls = [j for j in self.downloads.list() if j.get("kind") == "image"]
        defaults = {k: self.image_default(k) for k in ("draw", "edit", "video")}
        out = []
        for b in images.catalog():
            m = inst.get(b["id"])
            folder = os.path.join(images.models_dir(self.settings), b["id"])
            jobs = [j for j in dls if os.path.normpath(j["dest"]) == os.path.normpath(folder)]
            active = [j for j in jobs if j["state"] in ("queued", "downloading")]
            failed = next((j["error"] for j in jobs if j["state"] == "error"), None)
            size = (m or {}).get("size_gb") or self.image_sizes.get(b["id"])
            out.append({
                "id": b["id"], "name": b["name"], "desc": b.get("desc", ""), "recommended": bool(b.get("recommended")),
                "installed": bool(m and m["ready"]), "partial": bool(m and not m["ready"]),
                "downloading": bool(active), "error": None if active else failed,
                "done": sum(j["done"] for j in jobs), "total": sum(j["total"] for j in jobs),
                "size_gb": size, "fit": images.fit(size, hw, backend) if size else None,
                "kind": images.kind(b),
                "default": bool(defaults[images.kind(b)] and defaults[images.kind(b)]["id"] == b["id"]),
                "files": [f["file"] for f in (m or {}).get("files") or []],
            })
        engine["recommended"] = recommended
        job = next((j for j in self.jobs.values() if j["kind"] == "image-engine" and j["state"] == "running"), None)
        engine["job"] = job["id"] if job else None
        if not self.image_sizes and not hardware.ANDROID:
            threading.Thread(target=self._estimate_images, daemon=True).start()
        return {"supported": not hardware.ANDROID and recommended is not None, "enabled": bool(self.settings["images"]),
                "engine": engine, "bundles": out, "server": self.image_server.status() if self.image_server else None,
                "ready": self.images_ready(), "edit_ready": self.edit_ready(), "video_ready": self.video_ready(), "swap": self.settings["image_swap"],
                "keep_min": self.settings["image_keep_min"], "folder": images.models_dir(self.settings),
                "text": self._text_draw_state()}

    def _text_draw_state(self):
        """Whether the running text model can call image_generate, and if not why."""
        if self.server.state not in ("loading", "ready"):
            return {"running": False}
        pre = self.server.preset or {}
        return {"running": True, "preset": pre.get("id"), "name": pre.get("name"), "tools": bool(pre.get("tools")),
                "draws": bool(pre.get("tools")) and self.mcp_image}

    def _resolve_image(self, b):
        token = self.settings["hf_token"] or None
        trees = {spec["repo"]: images.hf_tree(spec["repo"], token) for spec in b["files"]}
        return images.resolve(b, trees, images.budget(self.hardware()))

    def _estimate_images(self):
        for b in images.catalog():
            if b["id"] in self.image_sizes:
                continue
            try:
                files = self._resolve_image(b)
                self.image_sizes[b["id"]] = round(sum(f["size"] for f in files) / images.GiB, 2)
            except Exception:  # noqa: BLE001 - offline: no size yet
                self.image_sizes.setdefault(b["id"], None)

    def download_image_engine(self, backend=None):
        backend = backend or images.engine_backend(self.hardware().get("gpus"))
        if not backend:
            raise RuntimeError("Pictures are not available on this device.")
        running = next((j for j in self.jobs.values() if j["kind"] == "image-engine" and j["state"] == "running"), None)
        if running:
            return running

        def fn(progress, cancelled, job):
            return images.download_engine(backend, progress=progress, cancelled=cancelled)
        return self.start_job("image-engine", fn)

    def install_image(self, bundle_id):
        """Picks the files that suit this computer and downloads them into the model's own folder."""
        b = images.bundle_info(bundle_id)
        if not b:
            raise ValueError("That image model is not in Pumpkin's list.")
        files = self._resolve_image(b)
        folder = os.path.join(images.models_dir(self.settings), b["id"])
        m = images.write_manifest(folder, b, files)
        self.image_sizes[b["id"]] = round(sum(f["size"] for f in files) / images.GiB, 2)
        by_repo = {}
        for f in files:
            by_repo.setdefault(f["repo"], []).append(f["path"])
        for repo, paths in by_repo.items():
            self.downloads.add(repo, paths, dest=folder, kind="image")
        key = IMAGE_KEYS.get(images.kind(b), "video_model")
        if not self.settings[key]:
            self.settings.update({key: b["id"]})
        return m

    def delete_image(self, bundle_id):
        m = images.installed(self.settings).get(bundle_id)
        if not m:
            raise FileNotFoundError("That image model is not installed.")
        if (self.image_server.bundle or {}).get("id") == bundle_id:
            self.image_server.stop()            # the memory is freed at once, and Windows cannot delete open files
        for j in self.downloads.list():
            if j.get("kind") == "image" and os.path.normpath(j["dest"]) == os.path.normpath(m["folder"]):
                self.downloads.cancel(j["id"])
        root = os.path.realpath(images.models_dir(self.settings))
        if os.path.dirname(os.path.realpath(m["folder"])) != root:
            raise PermissionError("That folder is not one of Pumpkin's image models.")
        shutil.rmtree(m["folder"], ignore_errors=True)
        for key in IMAGE_KEYS.values():
            if self.settings[key] == bundle_id:
                self.settings.update({key: ""})

    def _text_model_gb(self):
        pre = self.server.preset or {}
        total = 0
        for p in (pre.get("model"), pre.get("mmproj")):
            try:
                total += os.path.getsize(p) if p else 0
            except OSError:
                pass
        return total / images.GiB

    def image_needs_swap(self, manifest):
        return self.needs_swap(manifest.get("size_gb") or 0)

    def needs_swap(self, size_gb):
        """Whether the text model must make room for another model of this size. auto: only when both do not fit."""
        mode = self.settings["image_swap"]
        if self.server.state not in ("loading", "ready") or mode == "never":
            return False
        if mode == "always":
            return True
        hw = self.hardware()
        g = hw.get("main_gpu") or {}
        vram = 0 if g.get("too_old") else (g.get("vram_total") or 0)
        ram = hw.get("ram_total") or 0
        text = self._text_model_gb() * 1.15 + 0.5            # its context (KV cache) needs room too
        pic = size_gb * 1.1 + 1.0                              # and the other model's working memory
        if vram >= 2 and not g.get("unified"):
            live = hardware.live()                           # what is really free now, whatever the estimate says
            if live.get("vram_total") and live.get("vram_used") is not None and not live.get("unified") \
                    and live["vram_total"] - live["vram_used"] < pic:
                return True
            return text + pic > vram
        return text + pic > ram * 0.8

    def _engine_run(self, m, swap_ok, work, stage="drawing"):
        """Loads image model m in sd-server and returns (work(server), whether the text model paused). If the two
        do not fit together the text model pauses meanwhile and comes back by itself; if the card turns out to be
        full anyway the job is tried once more with the text model out of the way."""
        with self.image_lock:
            was = None
            if swap_ok and self.image_needs_swap(m):
                was = dict(self.server.preset)
                self.server.stop()
            srv = self.image_server
            srv.busy = True
            try:
                g = self.hardware().get("main_gpu") or {}
                cap = images.budget(self.hardware()) if not g.get("unified") and not g.get("too_old") else 0
                for attempt in (1, 2):
                    srv.progress = {"stage": "loading" if srv.state != "ready" else stage, "since": time.time(),
                                    "swapped": bool(was)}
                    srv.start(m, max_vram=cap)
                    srv.progress.update(stage=stage)
                    try:
                        return work(srv), bool(was)
                    except RuntimeError as e:
                        if attempt == 2 or not srv.out_of_memory():
                            raise
                        # the card is full: start over with the text model out of the way (if it may be)
                        srv.stop()
                        if was or not swap_ok or self.settings["image_swap"] == "never" \
                                or self.server.state not in ("loading", "ready"):
                            raise RuntimeError("There is not enough free memory on the graphics card for this "
                                               "model. Stop the text model, or set Images > the text model "
                                               "pauses to Always.") from e
                        was = dict(self.server.preset)
                        self.server.stop()
            finally:
                srv.busy, srv.progress = False, None
                if was:
                    srv.stop()                                  # its memory goes back to the text model
                    try:
                        self.server.start(was)
                        self.server.wait_ready(timeout=900)
                    except Exception:  # noqa: BLE001 - the result is still worth returning
                        pass

    def make_image(self, prompt, shape="square", seed=None, swap_ok=True, edit=False, source=None):
        """Draws one picture with the default image model (or, with edit, changes a picture with the editing model)
        and saves it into the user's Files (images/). Returns {path, jpeg, seconds, model, swapped}."""
        prompt = re.sub(r"\s+", " ", str(prompt or "")).strip()
        if not prompt:
            raise ValueError("Say what the picture should show." if not edit else "Say what to change in the picture.")
        if len(prompt) > 4000:
            prompt = prompt[:4000]
        if not self.settings["images"]:
            raise RuntimeError("Pictures are switched off in Pumpkin (Images page).")
        m = self.image_default("edit" if edit else "draw")
        if not m:
            raise RuntimeError("No picture-changing model is installed. The user can add Qwen Image Edit on Pumpkin's "
                               "Images page." if edit else
                               "No image model is installed. The user can add one on Pumpkin's Images page.")
        if not images.find_engine(self.settings):
            raise RuntimeError("The image engine is not installed. The user can add it on Pumpkin's Images page.")
        d = m.get("defaults") or {}
        ref = self.picture_to_edit(source) if edit else None
        ratio = (images.dims(ref) if ref else None) or images.SIZES.get(shape or "square", (1, 1))
        w, h = images.size_for(*ratio, base=int(d.get("width") or 1024))
        t0 = time.time()
        data, was = self._engine_run(m, swap_ok, lambda srv: srv.generate(prompt, w, h, -1 if seed is None else seed,
                                                                          ref=ref))
        folder = os.path.join(os.path.expanduser(self.settings["files_dir"]), "images")
        os.makedirs(folder, exist_ok=True)
        name = ("edit-" if edit else "") + (config.slug(prompt[:48]) or "picture") + time.strftime("-%Y%m%d-%H%M%S") + ".jpg"
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)
        if self.indexer:
            self.indexer.poke()
        return {"path": f"images/{name}", "jpeg": data, "seconds": round(time.time() - t0, 1), "model": m["name"],
                "swapped": bool(was), "width": w, "height": h}

    def make_video(self, prompt, shape="landscape", seconds=None, seed=None, swap_ok=True, from_picture=False,
                   source=None, progress=None):
        """Makes a short clip with the video model and saves it into the user's Files (videos/). With from_picture
        the clip starts from the picture the user attached or the newest one drawn (or source, a file in Files)."""
        prompt = re.sub(r"\s+", " ", str(prompt or "")).strip()[:4000]
        if not prompt:
            raise ValueError("Say what the video should show.")
        if not self.settings["images"]:
            raise RuntimeError("Pictures and video are switched off in Pumpkin (Images page).")
        m = self.image_default("video")
        if not m:
            raise RuntimeError("No video model is installed. The user can add Wan 2.2 on Pumpkin's Images page.")
        if not images.find_engine(self.settings):
            raise RuntimeError("The image engine is not installed. The user can add it on Pumpkin's Images page.")
        d = m.get("defaults") or {}
        init = self.picture_to_edit(source) if (from_picture or source) else None
        ratio = (images.dims(init) if init else None) or images.SIZES.get(shape or "landscape", (16, 9))
        w, h = images.size_for(*ratio, base=int(d.get("width") or 640))
        fps = int(d.get("fps") or 24)
        try:
            secs = float(seconds) if seconds not in (None, "") else float(d.get("seconds") or 2)
        except (TypeError, ValueError):
            secs = float(d.get("seconds") or 2)
        secs = max(1.0, min(secs, float(d.get("max_seconds") or 5)))
        frames = int(secs * fps) // 4 * 4 + 1               # the engine uses 4n+1 frames
        t0 = time.time()
        (data, ext), was = self._engine_run(m, swap_ok, lambda srv: srv.generate_video(
            prompt, w, h, frames, fps, -1 if seed is None else seed, d.get("negative") or "", init,
            progress=progress), stage="filming")
        folder = os.path.join(os.path.expanduser(self.settings["files_dir"]), "videos")
        os.makedirs(folder, exist_ok=True)
        name = (config.slug(prompt[:48]) or "video") + time.strftime("-%Y%m%d-%H%M%S") + "." + ext
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)
        return {"path": f"videos/{name}", "seconds": round(time.time() - t0, 1), "model": m["name"],
                "swapped": bool(was), "width": w, "height": h, "length": round(frames / fps, 1), "frames": frames}

    def video_tool(self, params):
        """The chat called image_video. The chat shows the clip from the file; the model only gets a short note."""
        try:
            r = self.make_video(params.get("prompt"), params.get("shape") or "landscape", params.get("seconds"),
                                params.get("seed"), from_picture=bool(params.get("from_picture")),
                                source=params.get("source"))
        except (RuntimeError, ValueError, OSError) as e:
            return {"error": str(e)}
        return {"plain_text_response": (
            f"The video is shown to the user and saved as {r['path']} in their Files ({r['model']}, "
            f"{r['length']} s of video, made in {r['seconds']} s). Do not describe it or paste a link unless asked.")}

    def image_tool(self, params, edit=False):
        """The chat called image_generate (or image_edit). The picture goes back as an image line the chat shows (and
        gives a vision model); the text tells the model it is done."""
        try:
            r = self.make_image(params.get("prompt"), params.get("shape") or "square", params.get("seed"),
                                edit=edit, source=params.get("source") if edit else None)
        except (RuntimeError, ValueError, OSError) as e:
            return {"error": str(e)}
        b64 = base64.b64encode(r["jpeg"]).decode()
        return {"plain_text_response": (
            f"The picture is shown to the user and saved as {r['path']} in their Files ({r['model']}, "
            f"{r['seconds']} s). Do not describe it or paste a link unless asked.\n"
            f"data:image/jpeg;base64,{b64}")}

    def code_waiting(self, session, data):
        """Pumpkin Code waits for a yes: tell the user, who may have stepped away."""
        if not self.settings["notifications"]:
            return
        what = f"Change {data.get('path')}?" if data.get("path") else f"Run {str(data.get('command', ''))[:80]}?"
        agent.notify(self.settings, f"Pumpkin Code is waiting: {session.title[:40]}", what)

    # ---- the vision helper
    def vision_run(self, fn, swap_ok=True):
        """Runs fn(helper) with the vision model loaded. If the text model and the vision model do not fit
        together, the text model pauses meanwhile and comes back by itself (like it does for pictures)."""
        m = self.vision.model()
        if not m:
            raise RuntimeError("No vision model is installed. Download one with vision on the Models page "
                               "(Gemma 3 4B is a good small one).")
        if not llama.find_server(self.settings):
            raise RuntimeError("llama-server was not found. Open Settings to download it.")
        with self.image_lock:
            was = None
            if swap_ok and self.needs_swap(self.vision.size_gb(m)):
                was = dict(self.server.preset)
                self.server.stop()
            self.vision.busy = True
            try:
                for _ in range(3):
                    try:
                        self.vision.start(m)
                        break
                    except RuntimeError:
                        if m["path"] not in self.vision.bad:
                            raise
                        m = self.vision.model()              # that one's vision file is broken: try the next
                        if not m:
                            raise
                else:
                    raise RuntimeError(self.vision.error or "None of the vision models here would start.")
                return fn(self.vision)
            finally:
                self.vision.busy = False
                if was:
                    self.vision.stop()
                    try:
                        self.server.start(was)
                        self.server.wait_ready(timeout=900)
                    except Exception:  # noqa: BLE001 - the answer is still worth returning
                        pass

    def text_model_sees(self):
        """The running chat model takes pictures itself (it has a vision projector)."""
        pre = self.server.preset or {}
        return bool(pre.get("mmproj"))

    def relay_sees(self):
        """Pictures attached to a text-only chat model are read by the vision helper instead."""
        try:
            return bool(self.settings["vision_relay"]) and not self.text_model_sees() and bool(self.vision.model())
        except Exception:  # noqa: BLE001
            return False

    def relay_images(self, body):
        """A chat request carries pictures for a model that cannot see: swap each for the vision model's
        description. Returns the new request body (bytes)."""
        try:
            req = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return body
        changed = False
        for msg in req.get("messages") or []:
            parts = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(parts, list):
                continue
            for i, part in enumerate(parts):
                if not isinstance(part, dict) or part.get("type") != "image_url":
                    continue
                url = (part.get("image_url") or {}).get("url") or ""
                m = re.match(r"data:image/[a-z+]+;base64,(.+)", url, re.S)
                if not m:
                    continue
                try:
                    data = base64.b64decode(m.group(1))
                except ValueError:
                    continue
                key = hashlib.sha1(data).hexdigest()
                seen = self.vision_seen.get(key)
                if seen is None:
                    try:
                        seen = self.vision_run(lambda h, d=data: h.describe(d)) or \
                            "(the vision model saw nothing to describe)"
                        self.vision_seen[key] = seen          # a chat sends its pictures again with every message
                        for k in list(self.vision_seen)[:-50]:
                            self.vision_seen.pop(k, None)
                    except (RuntimeError, ValueError, OSError) as e:
                        seen = f"(it could not be read: {e})"
                parts[i] = {"type": "text", "text": f"[A picture the user attached. A vision model describes it: "
                                                     f"{seen}]"}
                changed = True
        return json.dumps(req).encode() if changed else body

    def vision_state(self):
        st = self.vision.status()
        st.update(relay=bool(self.settings["vision_relay"]), text_sees=self.text_model_sees(),
                  scans=docs.status(self.settings).get("scans", 0),
                  reading=next((j["id"] for j in self.jobs.values() if j["kind"] == "scans" and j["state"] == "running"),
                               None))
        return st

    def read_scans(self):
        """Reads every scanned page and photo of paper in the document index with the vision model."""
        def fn(progress, cancelled, job):
            def reader(path, ext):
                return docs.scan_parts(path, ext, lambda data: self.vision_run(
                    lambda h: h.describe(data, vision.TRANSCRIBE, 1500)))
            return docs.read_scans(self.settings, reader, progress, cancelled)
        if any(j["kind"] == "scans" and j["state"] == "running" for j in self.jobs.values()):
            raise RuntimeError("Scans are already being read.")
        if not self.vision.model():
            raise RuntimeError("No vision model is installed. Download one with vision on the Models page "
                               "(Gemma 3 4B is a good small one).")
        return self.start_job("scans", fn)

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
        if self.mcp_host:
            self.mcp_host.close()
        if self.image_server:
            self.image_server.stop()
        try:
            self.vision.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            workspace.fix_owner(self.settings)
            workspace.stop()
        except Exception:  # noqa: BLE001
            pass
