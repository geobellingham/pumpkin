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


if __name__ == "__main__":
    unittest.main()
