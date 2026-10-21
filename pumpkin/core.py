"""Everything Pumpkin does, in one object the HTTP API calls into."""
import json
import re
import os
import sys
import threading
import time

from . import __version__, agent, config, hardware, llama, migrate, models, recommend, remote


class Core:
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
        self.server = llama.ServerManager(self.settings, self.library)
        self.server.persona_prompt = self.persona_prompt
        self.server.mcp_config = self.mcp_config
        self.reports = agent.Reports(self.settings)
        self.runner = agent.Runner(self)
        self.jobs = {}                       # background jobs like downloading llama-server
        self.bench_state = {"running": False, "error": None, "last": None}
        self.hw = None
        self.hw_at = 0
        if self.settings["autostart_model"]:
            p = self.presets.get(self.settings["last_preset"])
            if p:
                threading.Thread(target=self._safe_start, args=(p,), daemon=True).start()

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
        return {"vram_total": g.get("vram_total") or 0, "ram_total": h.get("ram_total") or 0}

    def persona_prompt(self):
        p = self.personas.get(self.settings["persona"])
        return (p or {}).get("prompt", "")

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
        if preset.get("web", True):
            pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env = {"PYTHONPATH": pkg_parent}
            for k in ("PUMPKIN_SEARXNG", "BRAVE_API_KEY"):
                if os.environ.get(k):
                    env[k] = os.environ[k]
            servers.setdefault("web", {"command": sys.executable, "args": ["-m", "pumpkin.mcp_web"], "env": env})
        if not servers:
            return None
        path = os.path.join(config.STATE_DIR, "mcp-effective.json")
        with open(path, "w") as f:
            json.dump({"mcpServers": servers}, f, indent=2)
        return path

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

    def recommendations(self, refresh=False):
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
                "text": models.FIT_TEXT[f["level"]] if f else "Too big for this machine"}
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
    def set_remote(self, mode):
        port = int(self.settings["port"])
        if mode == "tailscale":
            remote.tailscale_on(port)
        elif self.settings["remote"] == "tailscale":
            remote.tailscale_off()
        self.settings.update({"remote": mode})
        return remote.status(self.settings, config.token())

    def shutdown(self):
        try:
            self.server.stop()
        except Exception:  # noqa: BLE001
            pass
