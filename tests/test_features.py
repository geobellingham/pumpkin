"""Tests for finding existing models, switching presets from the chat, goals and update checks."""
import http.server
import json
import os
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fixtures  # noqa: E402
from pumpkin import config, discover, models, updates  # noqa: E402
from pumpkin.core import Core  # noqa: E402


class FakeSettings(dict):
    def update(self, d):
        super().update(d)


class TestDiscover(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="pumpkin-disc-")
        self._home = config.HOME
        config.HOME = self.home
        self.s = FakeSettings(models_dirs=[f"{self.home}/Pumpkin/models"], download_dir=f"{self.home}/Pumpkin/models")
        # LM Studio keeps models under a hidden folder
        fixtures.make_gguf(f"{self.home}/.lmstudio/models/lmstudio-community/Qwen/qwen-Q4_K_M.gguf", size_gb=1)
        fixtures.make_gguf(f"{self.home}/.lmstudio/models/lmstudio-community/Qwen/mmproj-F16.gguf", mmproj=True)
        # a Hugging Face cache snapshot (files are links into blobs)
        blob = f"{self.home}/.cache/huggingface/hub/models--org--repo/blobs/abc"
        fixtures.make_gguf(blob, size_gb=1)
        snap = f"{self.home}/.cache/huggingface/hub/models--org--repo/snapshots/rev1"
        os.makedirs(snap)
        os.symlink(blob, f"{snap}/repo-Q8_0.gguf")
        # an Ollama store: manifest -> blob named by digest
        root = f"{self.home}/.ollama/models"
        fixtures.make_gguf(f"{root}/blobs/sha256-1234", size_gb=1)
        os.makedirs(f"{root}/manifests/registry.ollama.ai/library/llama3")
        with open(f"{root}/manifests/registry.ollama.ai/library/llama3/8b", "w") as f:
            json.dump({"layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:1234"},
                                  {"mediaType": "application/vnd.ollama.image.template", "digest": "sha256:9999"}]}, f)
        self._env = os.environ.pop("HF_HOME", None), os.environ.pop("OLLAMA_MODELS", None)

    def tearDown(self):
        config.HOME = self._home

    def test_finds_each_source_once(self):
        found = {d["source"]: d for d in discover.find(self.s)}
        self.assertEqual(found["LM Studio"]["count"], 1)            # the vision file is not a model
        self.assertEqual(found["Hugging Face cache"]["count"], 1)
        self.assertEqual(found["Ollama"]["count"], 1)

    def test_add_scans_in_place_and_links_ollama(self):
        ids = [d["id"] for d in discover.find(self.s)]
        added = discover.add(self.s, ids)
        self.assertIn("Ollama", added)
        link = f"{self.home}/Pumpkin/models/from-ollama/llama3-8b.gguf"
        self.assertTrue(os.path.islink(link))
        lib = models.Library(self.s)
        files = {m["file"] for m in lib.scan(force=True) if m["kind"] == "model"}
        self.assertEqual(files, {"qwen-Q4_K_M.gguf", "repo-Q8_0.gguf", "llama3-8b.gguf"})
        self.assertEqual(discover.find(self.s), [])                  # nothing left to offer

    def test_library_never_deletes_other_apps_files(self):
        discover.add(self.s, [d["id"] for d in discover.find(self.s)])
        lib = models.Library(self.s)
        link = f"{self.home}/Pumpkin/models/from-ollama/llama3-8b.gguf"
        lib.scan(force=True)
        with self.assertRaises(RuntimeError):
            lib.delete(link)
        self.assertTrue(os.path.exists(f"{self.home}/.ollama/models/blobs/sha256-1234"))


class Coll:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items

    def get(self, ref):
        return next((p for p in self.items if ref in (p["id"], p["name"])), None)


class StubServer:
    def __init__(self):
        self.state, self.preset, self.error, self.started = "off", None, None, []

    def start(self, p):
        self.started.append(p["id"])
        self.preset, self.state = dict(p), "ready"

    def wait_ready(self, timeout=0):
        return self.state == "ready"


class TestSwitching(unittest.TestCase):
    def core(self):
        c = Core.__new__(Core)
        c.presets = Coll([{"id": "a", "name": "Small model"}, {"id": "b", "name": "Big model"}])
        c.server = StubServer()
        return c

    def test_starts_the_preset_the_chat_asks_for(self):
        c = self.core()
        self.assertEqual(c.ensure_preset("big model")["id"], "b")
        self.assertEqual(c.server.started, ["b"])
        c.ensure_preset("Big model")                                  # already running: no restart
        self.assertEqual(c.server.started, ["b"])
        c.ensure_preset("a")
        self.assertEqual(c.server.started, ["b", "a"])

    def test_unknown_names_and_external_servers_are_left_alone(self):
        c = self.core()
        self.assertIsNone(c.ensure_preset("some-file.gguf"))
        c.server.state = "external"
        self.assertIsNone(c.ensure_preset("a"))
        self.assertEqual(c.server.started, [])

    def test_failed_start_is_reported(self):
        c = self.core()
        c.server.wait_ready = lambda timeout=0: False
        c.server.error = "out of memory"
        with self.assertRaises(RuntimeError) as e:
            c.ensure_preset("a")
        self.assertIn("out of memory", str(e.exception))

    def test_model_list_marks_the_running_one(self):
        c = self.core()
        c.ensure_preset("b")
        status = {m["id"]: m["status"]["value"] for m in c.model_list()}
        self.assertEqual(status, {"Small model": "unloaded", "Big model": "loaded"})


class TestGoals(unittest.TestCase):
    def test_rank_puts_matches_first_without_reordering_the_rest(self):
        ms = [{"name": "Gemma 3", "id": "g"}, {"name": "Qwen3 Coder 30B", "id": "q"}, {"name": "Llama 4", "id": "l"}]
        ranked = sorted(ms, key=lambda m: Core._goal_rank("coding", m))
        self.assertEqual([m["id"] for m in ranked], ["q", "g", "l"])
        tools = sorted([{"id": "x", "tools": False}, {"id": "y", "tools": True}], key=lambda m: Core._goal_rank("research", m))
        self.assertEqual(tools[0]["id"], "y")

    def test_goal_sets_persona(self):
        c = Core.__new__(Core)
        c.personas = Coll([{"id": "1", "name": "Assistant"}, {"id": "2", "name": "Coder"}])
        c.settings = FakeSettings(persona="Assistant")
        self.assertEqual(c.apply_goal("coding")["persona"], "Coder")
        with self.assertRaises(RuntimeError):
            c.apply_goal("nonsense")


class TestUpdates(unittest.TestCase):
    def test_refuses_odd_tags(self):
        old = updates.CACHE
        updates.CACHE = tempfile.mktemp()
        try:
            with open(updates.CACHE, "w") as f:
                json.dump({"at": time.time(), "tag": "v1.0; rm -rf ~"}, f)
            with self.assertRaises(RuntimeError):
                updates.apply()
        finally:
            os.remove(updates.CACHE)
            updates.CACHE = old

    def test_download_checksum(self):
        from pumpkin import llama
        p = tempfile.mktemp()
        with open(p, "wb") as f:
            f.write(b"hello")
        llama.verify(p, "sha256:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")   # passes
        with self.assertRaises(RuntimeError):
            llama.verify(p, "sha256:" + "0" * 64)
        self.assertFalse(os.path.exists(p))                    # a bad download is deleted

    def test_version_compare(self):
        self.assertGreater(updates._ver("v0.1.10"), updates._ver("0.1.9"))
        self.assertGreater(updates._ver("v1.0.0"), updates._ver("0.9.9"))

    def test_reports_a_newer_release_and_survives_no_network(self):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                b = json.dumps({"tag_name": "v99.0.0", "html_url": "https://example.com/r", "body": "Notes"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        cache = tempfile.mktemp()
        old = (updates.API, updates.CACHE)
        try:
            updates.API, updates.CACHE = f"http://127.0.0.1:{srv.server_port}/", cache
            st = updates.status(force=True)
            self.assertTrue(st["available"])
            self.assertEqual(st["latest"], "99.0.0")
            self.assertTrue(updates.cached_status()["available"])
            srv.shutdown()
            updates.API = "http://127.0.0.1:1/"
            st = updates.status(force=True)                           # offline: keeps the last answer
            self.assertTrue(st["available"])
            self.assertTrue(st["failed"])
        finally:
            updates.API, updates.CACHE = old
            if os.path.exists(cache):
                os.remove(cache)

    def test_apply_runs_the_installer(self):
        marker = tempfile.mktemp()
        os.environ["PUMPKIN_INSTALL_CMD"] = (f"New-Item -ItemType File -Path '{marker}' | Out-Null" if os.name == "nt"
                                             else f"touch {marker}")
        old_home, old_state, old_cache = config.HOME, config.STATE_DIR, updates.CACHE
        config.STATE_DIR = tempfile.mkdtemp()
        config.HOME = tempfile.mkdtemp()                      # keeps the follow-up restart away from the real install
        updates.CACHE = os.path.join(config.STATE_DIR, "update-check.json")
        try:
            with open(updates.CACHE, "w") as f:
                json.dump({"at": time.time(), "tag": "v9.9.9"}, f)
            updates.apply()
            for _ in range(300):                              # PowerShell takes a few seconds to start
                if os.path.exists(marker):
                    break
                time.sleep(0.1)
            self.assertTrue(os.path.exists(marker))
        finally:
            del os.environ["PUMPKIN_INSTALL_CMD"]
            config.HOME, config.STATE_DIR, updates.CACHE = old_home, old_state, old_cache
            if os.path.exists(marker):
                os.remove(marker)


class TestSpeculativeDecoding(unittest.TestCase):
    def cmd(self, preset, flags, mmproj=False):
        from unittest import mock
        from pumpkin import llama
        lib = mock.Mock()
        lib.resolve.side_effect = lambda p: {"path": p, "info": {}} if p else None
        sm = llama.ServerManager.__new__(llama.ServerManager)
        sm.settings = {"threads": 4, "sandbox_image": "ubuntu:24.04", "llama_port": 8080, "files_dir": tempfile.mkdtemp()}
        sm.library = lib
        sm.mcp_config = lambda p: None
        p = {"model": "/m/x.gguf", **preset}
        if mmproj:
            p["mmproj"] = "/m/mmproj.gguf"
        return sm.build_cmd(p, "/bin/llama-server", {"flags": flags})

    def test_on_by_default_when_llama_server_has_it(self):
        self.assertIn("--spec-default", self.cmd({}, ["--spec-default", "-fa"]))
        self.assertNotIn("--spec-default", self.cmd({"spec": False}, ["--spec-default"]))
        self.assertNotIn("--spec-default", self.cmd({}, ["--spec-default"], mmproj=True))     # image models
        self.assertNotIn("--spec-default", self.cmd({}, ["-fa"]))                            # older llama-server


class TestWindowProfile(unittest.TestCase):
    def test_service_worker_always_and_caches_after_an_update(self):
        from pumpkin import __main__ as cli
        prof = tempfile.mkdtemp()
        d = os.path.join(prof, "Default")
        for name in ("Service Worker", "Cache", "Code Cache", "GPUCache", "IndexedDB", "Local Storage"):
            os.makedirs(os.path.join(d, name, "x"))
        cli.clean_window_profile(prof)
        left = sorted(os.listdir(d))
        self.assertEqual(left, ["IndexedDB", "Local Storage"])            # chats and settings stay
        for name in ("Service Worker", "Cache"):
            os.makedirs(os.path.join(d, name))
        cli.clean_window_profile(prof)                                   # same version: only the service worker
        self.assertEqual(sorted(os.listdir(d)), ["Cache", "IndexedDB", "Local Storage"])
        cli.clean_window_profile(os.path.join(prof, "missing"))          # no profile yet: nothing to do


class TestOldGpuIsLeftOut(unittest.TestCase):
    def test_device_list(self):
        from unittest import mock
        from pumpkin import llama
        lib = mock.Mock()
        lib.resolve.side_effect = lambda p: {"path": p, "info": {}} if p else None
        sm = llama.ServerManager.__new__(llama.ServerManager)
        sm.settings = {"threads": 4, "sandbox_image": "ubuntu:24.04", "llama_port": 8080, "files_dir": tempfile.mkdtemp()}
        sm.library = lib
        sm.mcp_config = lambda p: None
        listing = ("Available devices:\n  Vulkan0: NVIDIA GeForce GT 710 (2048 MiB, 1900 MiB free)\n"
                   "  Vulkan1: Intel(R) UHD Graphics 630 (8000 MiB, 7000 MiB free)\n")
        for out, want in ((listing, ["--device", "Vulkan1"]),
                          ("  Vulkan0: NVIDIA GeForce GT 710 (2048 MiB, 1900 MiB free)\n", ["--device", "none"]),
                          ("  CUDA0: NVIDIA GeForce RTX 3060 (12288 MiB, 11000 MiB free)\n", None)):
            llama._devices.clear()
            with mock.patch.object(llama.subprocess, "run", return_value=mock.Mock(stdout=out)):
                cmd = sm.build_cmd({"model": "/m/x.gguf"}, "/bin/llama-server", {"flags": ["--device"]})
            if want:
                i = cmd.index("--device")
                self.assertEqual(cmd[i:i + 2], want)
            else:
                self.assertNotIn("--device", cmd)
        llama._devices.clear()


class TestToolsNeedTheSandbox(unittest.TestCase):
    def cmd(self, docker):
        from unittest import mock
        from pumpkin import llama
        lib = mock.Mock()
        lib.resolve.return_value = {"path": "/m/x.gguf", "info": {}}
        sm = llama.ServerManager.__new__(llama.ServerManager)
        sm.settings = {"threads": 4, "sandbox_image": "ubuntu:24.04", "llama_port": 8080, "files_dir": tempfile.mkdtemp()}
        sm.library = lib
        sm.mcp_config = lambda p: None
        info = {"flags": ["--agent", "--tools-runtime", "--mcp-servers-config"]}
        with mock.patch.object(llama.shutil, "which", lambda b: "/usr/bin/docker" if docker and b == "docker" else None), \
                mock.patch.object(llama.workspace, "ensure", lambda s: "pumpkin-workspace"), \
                mock.patch.object(llama.workspace, "docker_usable", lambda: docker):
            return sm.build_cmd({"model": "x", "tools": True, "sandbox": True}, "/bin/llama-server", info)

    def test_no_docker_means_no_command_tools(self):
        self.assertNotIn("--agent", self.cmd(docker=False))

    def test_with_docker_the_tools_run_in_the_sandbox(self):
        c = self.cmd(docker=True)
        self.assertIn("--agent", c)
        self.assertEqual(c[c.index("--tools-runtime") + 1], "docker-container:pumpkin-workspace")


class TestProxyEnvironment(unittest.TestCase):
    def test_local_requests_ignore_a_proxy(self):
        """With HTTP_PROXY set, Pumpkin must still reach itself and llama-server on 127.0.0.1."""
        from pumpkin import llama

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = {k: os.environ.get(k) for k in ("HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy")}
        try:
            for k in old:
                os.environ.pop(k, None)
            os.environ["HTTP_PROXY"] = os.environ["http_proxy"] = "http://127.0.0.1:9"     # nothing listens there
            self.assertTrue(llama.health(srv.server_port))
            self.assertEqual(llama.http(srv.server_port, "/props"), {})
        finally:
            for k, v in old.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
            srv.shutdown()


class TestModelStopsMidReply(unittest.TestCase):
    def test_metrics_and_health_survive_a_cut_off_reply(self):
        import socket
        from pumpkin import llama
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)
        port = srv.getsockname()[1]

        def serve():
            for _ in range(3):
                c, _ = srv.accept()
                c.recv(4096)
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 74\r\n\r\n")   # then the model dies
                c.close()

        threading.Thread(target=serve, daemon=True).start()
        self.assertEqual(llama.metrics(port), {})
        llama.health(port)                                        # must not raise
        self.assertIn("error", llama.http(port, "/props"))
        srv.close()


@unittest.skipUnless(os.environ.get("PUMPKIN_ONLINE"), "needs the internet (runs in CI)")
class TestOnline(unittest.TestCase):
    def test_pinned_llama_release_publishes_checksums(self):
        from pumpkin import llama
        tag = open(os.path.join(os.path.dirname(HERE), "web", "LLAMA_TAG")).read().strip()
        digests = llama.release_digests(tag)
        for backend, arch, system in (("vulkan", "x64", "linux"), ("cpu", "x64", "linux"), ("metal", "arm64", "darwin"),
                                      ("cpu", "x64", "darwin")):
            names = [n for n in llama.asset_names(tag, backend, arch, system) if n in digests]
            self.assertTrue(names, f"no {backend} build for {system} {arch} in {tag}")
            self.assertTrue(digests[names[0]].startswith("sha256:"), names[0])


class TestMappedRam(unittest.TestCase):
    def test_reads_a_process_and_survives_a_missing_one(self):
        from pumpkin import hardware
        self.assertGreaterEqual(hardware.mapped_ram(os.getpid()), 0.0)
        self.assertEqual(hardware.mapped_ram(999999999), 0.0)
        self.assertEqual(hardware.mapped_ram(None), 0.0)


if __name__ == "__main__":
    unittest.main()


class TestSetupChecklistAndLongChats(unittest.TestCase):
    """The setup checklist (steps can be skipped) and the chat that outgrows the model's memory."""

    def make(self, **settings):
        c = Core.__new__(Core)
        base = dict(config.DEFAULT_SETTINGS)
        base.update(settings)

        class S(dict):
            def update(self, d):
                dict.update(self, d)

        c.settings = S(base)
        return c

    def test_skipping_a_step_is_remembered_and_can_be_undone(self):
        c = self.make()
        c.presets = type("P", (), {"all": lambda s: []})()
        c.tasks = type("T", (), {"all": lambda s: []})()
        c.library = type("L", (), {"scan": lambda s: []})()
        c.vision = type("V", (), {"model": lambda s: None})()
        c.server = type("Sv", (), {"preset": None})()
        c.image_default = lambda kind="draw": None
        steps = {s["id"]: s for s in c.checklist()["steps"]}
        self.assertFalse(steps["video"]["skipped"])
        self.assertFalse(steps["model"]["optional"])                 # the basics cannot be skipped
        steps = {s["id"]: s for s in c.skip_step("video")["steps"]}
        self.assertTrue(steps["video"]["skipped"])
        steps = {s["id"]: s for s in c.skip_step("video", False)["steps"]}
        self.assertFalse(steps["video"]["skipped"])
        c.skip_step("model")                                          # a basic step ignores it
        self.assertFalse({s["id"]: s for s in c.checklist()["steps"]}["model"]["skipped"])

    def test_a_chat_that_fits_is_left_alone(self):
        c = self.make()
        c.server = type("Sv", (), {"preset": {"ctx": 8000}})()
        body = json.dumps({"messages": [{"role": "user", "content": "hello"}]}).encode()
        self.assertEqual(c.prepare_chat(body), body)
        self.assertGreater(c.chat_tokens, 0)

    def test_a_chat_that_does_not_fit_gets_a_summary_of_its_older_part(self):
        c = self.make(llama_port=1)
        c.server = type("Sv", (), {"preset": {"ctx": 2000}})()
        asked = []

        from pumpkin import llama

        def fake(port, path, payload=None, **kw):
            asked.append(payload)
            return {"choices": [{"message": {"content": "They discussed cats."}}]}

        real, llama.http = llama.http, fake
        try:
            msgs = [{"role": "system", "content": "Be kind."}]
            for i in range(6):
                msgs += [{"role": "user", "content": f"question {i} " + "cats " * 300},
                         {"role": "assistant", "content": f"answer {i} " + "dogs " * 300}]
            msgs.append({"role": "user", "content": "and the last one?"})
            out = json.loads(c.prepare_chat(json.dumps({"messages": msgs}).encode()))["messages"]
            self.assertEqual(out[0]["content"], "Be kind.")                       # the system message stays first
            self.assertIn("They discussed cats.", out[1]["content"])
            self.assertEqual(out[-1]["content"], "and the last one?")             # the newest turn is kept whole
            self.assertLess(len(out), len(msgs))
            self.assertIn("summarised", c.chat_note[1])
            # the same chat a message later reuses the summary: the model is not asked again
            n = len(asked)
            msgs += [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "thanks"}]
            c.prepare_chat(json.dumps({"messages": msgs}).encode())
            self.assertEqual(len(asked), n)
            # switched off, it is left alone
            c.settings["compact_chat"] = False
            raw = json.dumps({"messages": msgs}).encode()
            self.assertEqual(c.prepare_chat(raw), raw)
        finally:
            llama.http = real


    def test_the_token_count_comes_from_the_server_and_shows_when_it_stops_moving(self):
        from pumpkin import llama
        c = self.make(llama_port=1)
        c.server = type("Sv", (), {"state": "ready"})()
        slots = [{"is_processing": True, "next_token": [{"n_decoded": 120}]}]
        real, llama.http = llama.http, lambda *a, **k: slots
        real_time = time.time
        now = [1000.0]
        time.time = lambda: now[0]
        try:
            self.assertEqual(c.decode_progress(), {"tokens": 120, "stalled": 0})
            now[0] += 100                                   # a minute and a half later nothing was written
            self.assertEqual(c.decode_progress(), {"tokens": 120, "stalled": 100})
            slots[0]["next_token"][0]["n_decoded"] = 150    # it moves again
            now[0] += 5
            self.assertEqual(c.decode_progress(), {"tokens": 150, "stalled": 0})
            slots[0]["is_processing"] = False
            now[0] += 5
            self.assertIsNone(c.decode_progress())
        finally:
            llama.http, time.time = real, real_time


class TestSkills(unittest.TestCase):
    """Skills in the SKILL.md format: reading them, yours over Pumpkin's, switching off, importing."""

    def setUp(self):
        from pumpkin import skills
        self.skills = skills
        self.dir = tempfile.mkdtemp(prefix="pumpkin-skills-")
        self._cfg = config.CONFIG_DIR
        config.CONFIG_DIR = self.dir

    def tearDown(self):
        config.CONFIG_DIR = self._cfg

    def test_the_front_matter_is_read(self):
        meta, body = self.skills.parse('---\nname: "my-skill"\ndescription: >\n  Use it when\n  things happen.\n---\n\nDo this.\n')
        self.assertEqual(meta["name"], "my-skill")
        self.assertEqual(meta["description"], "Use it when things happen.")
        self.assertEqual(body, "Do this.")
        meta, body = self.skills.parse("No front matter here.")
        self.assertEqual(meta, {})
        self.assertEqual(body, "No front matter here.")

    def test_pumpkin_ships_skills_and_yours_win(self):
        names = [s["name"] for s in self.skills.all_skills({})]
        self.assertIn("write-a-report", names)
        self.assertIn("review-security-logs", names)
        self.skills.save("write-a-report", "My own way of writing reports.", "Write it my way.")
        mine = self.skills.find("write-a-report", {})
        self.assertEqual(mine["source"], "yours")
        self.assertIn("Write it my way.", self.skills.read_text(mine))
        # switched off, it is not offered
        self.assertIsNone(self.skills.find("write-a-report", {"skills_off": ["write-a-report"]}))
        self.assertTrue(self.skills.delete("write-a-report"))
        self.assertEqual(self.skills.find("write-a-report", {})["source"], "builtin")

    def test_a_skills_files_stay_inside_it(self):
        d = os.path.join(self.dir, "src", "templ")
        os.makedirs(os.path.join(d, "refs"))
        with open(os.path.join(d, "SKILL.md"), "w") as f:
            f.write("---\nname: templ\ndescription: Uses a template.\n---\nRead refs/t.md.\n")
        with open(os.path.join(d, "refs", "t.md"), "w") as f:
            f.write("TEMPLATE")
        self.assertEqual(self.skills.import_folder(os.path.join(self.dir, "src")), ["templ"])
        s = self.skills.find("templ", {})
        self.assertEqual(s["files"], ["refs/t.md"])
        self.assertEqual(self.skills.read_file(s, "refs/t.md"), "TEMPLATE")
        with self.assertRaises(ValueError):
            self.skills.read_file(s, "../../../../etc/passwd")
        self.assertIn("refs/t.md", self.skills.read_text(s))

    def test_import_from_github(self):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("repo-main/README.md", "hi")
            z.writestr("repo-main/skills/pdf-tables/SKILL.md", "Find the tables in a PDF.\n")
            z.writestr("repo-main/skills/other/SKILL.md", "---\nname: other\ndescription: x\n---\nother\n")
        asked = []

        def fetch(url):
            asked.append(url)
            return buf.getvalue()

        names = self.skills.import_github("https://github.com/owner/repo/tree/main/skills/pdf-tables", fetch=fetch)
        self.assertEqual(asked, ["https://codeload.github.com/owner/repo/zip/main"])
        self.assertEqual(names, ["pdf-tables"])                      # no name in it: the folder's name
        self.assertIn("Find the tables", self.skills.read_text(self.skills.find("pdf-tables", {})))
        self.assertEqual(sorted(self.skills.import_github("https://github.com/owner/repo", fetch=fetch)),
                         ["other", "pdf-tables"])
        with self.assertRaises(ValueError):
            self.skills.import_github("https://example.com/not-github")

    def test_the_tool_server_reads_them(self):
        from pumpkin import mcp_skills
        self.assertIn("# Skill: write-a-report", mcp_skills.call("read", {"name": "Write-A-Report"}))
        with self.assertRaises(LookupError):
            mcp_skills.call("read", {"name": "nope"})
        self.assertIn("- write-a-report:", self.skills.listing(self.skills.enabled({})))


class TestConnectors(unittest.TestCase):
    def setUp(self):
        from pumpkin import connectors
        self.c = connectors
        self.dir = tempfile.mkdtemp(prefix="pumpkin-conn-")
        self._cfg = config.CONFIG_DIR
        config.CONFIG_DIR = self.dir

    def tearDown(self):
        config.CONFIG_DIR = self._cfg

    def test_every_catalog_entry_is_complete(self):
        ids = set()
        for s in self.c.catalog():
            self.assertNotIn(s["id"], ids)
            ids.add(s["id"])
            for k in ("name", "blurb", "needs", "command", "docs", "fields"):
                self.assertIn(k, s, s["id"])
            self.assertIn(s["needs"], self.c.NEEDS)
            for f in s["fields"]:
                self.assertIn(f["kind"], ("secret", "text", "path", "paths", "toggle"), s["id"])

    def test_github_is_started_with_the_token_outside_its_arguments(self):
        with self.assertRaises(ValueError):
            self.c.save("github", {}, enabled=True)                  # no token yet
        self.c.save("github", {"token": "ghp_secret"}, enabled=True)
        e = self.c.entry("github")
        self.assertEqual(e["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"], "ghp_secret")
        self.assertNotIn("ghp_secret", " ".join(e["args"]))
        self.assertEqual(e["args"][-1], "ghcr.io/github/github-mcp-server")      # the image comes last
        self.assertIn("GITHUB_READ_ONLY=1", e["args"])                         # read only unless you say so
        self.assertIn("GITHUB_TOOLSETS=repos,issues,pull_requests,actions", e["args"])
        # the token is never sent back to the page, and "set" keeps it
        pub = next(x for x in self.c.public() if x["id"] == "github")
        self.assertEqual(pub["values"]["token"], "set")
        self.c.save("github", {"token": "set", "read_only": False})
        e = self.c.entry("github")
        self.assertEqual(e["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"], "ghp_secret")
        self.assertNotIn("GITHUB_READ_ONLY=1", e["args"])

    def test_postgres_is_read_only_unless_changes_are_allowed(self):
        self.c.save("postgres", {"uri": "postgresql://u:p@h/db"}, enabled=True)
        self.assertIn("--access-mode=restricted", self.c.entry("postgres")["args"])
        self.c.save("postgres", {"writes": True})
        self.assertIn("--access-mode=unrestricted", self.c.entry("postgres")["args"])

    def test_a_preset_can_pick_its_connectors(self):
        self.c.save("time", {}, enabled=True)
        self.c.save("folders", {"paths": "/tmp\n"}, enabled=True)
        avail = self.c.available
        self.c.available = lambda cid: True
        try:
            self.assertEqual(sorted(self.c.servers_for({})), ["folders", "time"])
            self.assertEqual(list(self.c.servers_for({"connectors": ["time"]})), ["time"])
            self.assertEqual(self.c.servers_for({"connectors": []}), {})
            self.c.save("time", enabled=False)
            self.assertEqual(list(self.c.servers_for({})), ["folders"])
        finally:
            self.c.available = avail

    def test_the_test_talks_to_a_real_tool_server(self):
        pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        e = {"command": sys.executable, "args": ["-m", "pumpkin.mcp_skills"],
             "env": {"PYTHONPATH": pkg, "PYTHONUTF8": "1"}}
        self.assertEqual(self.c.handshake(e, timeout=60), ["read", "file"])
        with self.assertRaises(RuntimeError) as err:
            self.c.handshake({"command": sys.executable, "args": ["-c", "import sys; sys.stderr.write('bad token'); sys.exit(1)"]}, timeout=30)
        self.assertIn("bad token", str(err.exception))


class TestHardening(unittest.TestCase):
    def test_keys_with_any_characters_are_compared_safely(self):
        from pumpkin import server
        self.assertFalse(server._eq("é", "abc"))                       # used to raise TypeError (an HTTP 500)
        self.assertFalse(server._eq("\udce9", "abc"))
        self.assertTrue(server._eq("abc", "abc"))
        from pumpkin import devices
        with mock_peer_key("k123"):
            self.assertFalse(devices.key_ok("é"))
            self.assertTrue(devices.key_ok("k123"))

    def test_request_bodies_have_limits(self):
        import io
        from pumpkin import server

        class H:
            def __init__(self, headers, data):
                self.headers, self.rfile = headers, io.BytesIO(data)

        with self.assertRaises(server.BodyError):
            server._read_body(H({"Content-Length": "-1"}, b""))
        with self.assertRaises(server.BodyError):
            server._read_body(H({"Content-Length": "100"}, b"x" * 100), limit=10)
        with self.assertRaises(server.BodyError):
            server._read_body(H({"Transfer-Encoding": "chunked"}, b"8\r\n12345678\r\n8\r\n12345678\r\n0\r\n\r\n"), limit=10)
        self.assertEqual(server._read_body(H({"Transfer-Encoding": "chunked"}, b"3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n")),
                         b"abcde")

    def test_docker_must_answer_to_count(self):
        from unittest import mock
        from pumpkin import workspace
        workspace._usable.update(at=0.0, ok=False)
        with mock.patch.object(workspace.shutil, "which", lambda b: "/usr/bin/docker"), \
                mock.patch.object(workspace, "_docker", lambda *a, **k: (1, "", "Cannot connect to the Docker daemon")):
            self.assertFalse(workspace.docker_usable())                  # installed but stopped
        workspace._usable.update(at=0.0, ok=False)
        with mock.patch.object(workspace.shutil, "which", lambda b: "/usr/bin/docker"), \
                mock.patch.object(workspace, "_docker", lambda *a, **k: (0, "linux", "")):
            self.assertTrue(workspace.docker_usable())
        workspace._usable.update(at=0.0, ok=False)

    def test_a_sandbox_that_will_not_start_leaves_the_command_tools_off(self):
        from unittest import mock
        from pumpkin import llama
        lib = mock.Mock()
        lib.resolve.return_value = {"path": "/m/x.gguf", "info": {}}
        sm = llama.ServerManager.__new__(llama.ServerManager)
        sm.settings = {"threads": 4, "sandbox_image": "ubuntu:24.04", "llama_port": 8080, "files_dir": tempfile.mkdtemp()}
        sm.library = lib
        sm.mcp_config = lambda p: None
        info = {"flags": ["--agent", "--tools-runtime", "--mcp-servers-config"]}
        with mock.patch.object(llama.workspace, "ensure", lambda s: None), \
                mock.patch.object(llama.workspace, "docker_usable", lambda: True):
            cmd = sm.build_cmd({"model": "x", "tools": True, "sandbox": True}, "/bin/llama-server", info)
        self.assertNotIn("--tools-runtime", cmd)

    def test_another_drive_is_outside_the_project(self):
        from unittest import mock
        from pumpkin import coder
        t = coder.Tools(tempfile.mkdtemp(), web=False)

        def other_drive(paths):
            raise ValueError("Paths don't have the same drive")

        with mock.patch.object(coder.os.path, "commonpath", other_drive):
            with self.assertRaises(PermissionError):
                t.path("D:/x")

    def test_a_damaged_download_never_counts_as_finished(self):
        import hashlib
        from pumpkin import models
        d = tempfile.mkdtemp()
        part, target = os.path.join(d, "m.gguf.part"), os.path.join(d, "m.gguf")
        with open(part, "wb") as f:
            f.write(b"model bytes")
        job = {"state": "downloading"}
        with self.assertRaises(RuntimeError):
            models.Downloads._finish(models.Downloads.__new__(models.Downloads), job, "m.gguf", part, target, "0" * 64)
        self.assertFalse(os.path.exists(part) or os.path.exists(target))
        with open(part, "wb") as f:
            f.write(b"model bytes")
        models.Downloads._finish(models.Downloads.__new__(models.Downloads), job, "m.gguf", part, target, hashlib.sha256(b"model bytes").hexdigest())
        self.assertTrue(os.path.exists(target))


class mock_peer_key:
    def __init__(self, key):
        from unittest import mock
        from pumpkin import devices
        self.p = mock.patch.object(devices, "peer_key", lambda: key)

    def __enter__(self):
        self.p.start()

    def __exit__(self, *a):
        self.p.stop()
