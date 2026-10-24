"""Backend tests. Run with: python3 -m unittest discover tests"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="pumpkin-test-")
os.environ.update(HOME=TMP, USERPROFILE=TMP, XDG_CONFIG_HOME=f"{TMP}/.config", XDG_DATA_HOME=f"{TMP}/.local/share",
                  XDG_STATE_HOME=f"{TMP}/.local/state", PATH=f"{TMP}/bin" + os.pathsep + os.environ["PATH"])
os.makedirs(f"{TMP}/bin")

import fixtures  # noqa: E402
from pumpkin import config, gguf, llama, models  # noqa: E402

fixtures.make_fake_server(f"{TMP}/bin/llama-server")
MODELS = f"{TMP}/Pumpkin/models"
fixtures.make_gguf(f"{MODELS}/qwen/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf", size_gb=20.5)
fixtures.make_gguf(f"{MODELS}/small/tiny-Q8_0.gguf", arch="llama", name="Tiny", layers=16, heads=16, kv_heads=8,
                   embd=1024, key_len=64, experts=0, file_type=7, size_gb=0.6)
fixtures.make_gguf(f"{MODELS}/qwen/mmproj-F16.gguf", mmproj=True)
fixtures.make_gguf(f"{MODELS}/big/Huge-Q4_K_M-00001-of-00002.gguf", arch="llama", experts=0, size_gb=40)
fixtures.make_gguf(f"{MODELS}/big/Huge-Q4_K_M-00002-of-00002.gguf", arch="llama", experts=0, size_gb=40)

HW = {"vram_total": 16.0, "ram_total": 30.0}


class TestGguf(unittest.TestCase):
    def test_reads_metadata_and_skips_vocab(self):
        meta = gguf.read_metadata(f"{MODELS}/qwen/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf")
        info = gguf.summarize(meta)
        self.assertEqual(info["arch"], "qwen3moe")
        self.assertEqual(info["layers"], 48)
        self.assertEqual(info["kv_heads"], 4)
        self.assertEqual(info["experts"], 128)
        self.assertEqual(info["quant"], "Q4_K_M")
        self.assertTrue(info["chat_template"])
        self.assertNotIn("tokenizer.ggml.tokens", {k: v for k, v in meta.items() if v})

    def test_mmproj(self):
        info = gguf.summarize(gguf.read_metadata(f"{MODELS}/qwen/mmproj-F16.gguf"))
        self.assertTrue(info["mmproj"])
        self.assertTrue(info["vision"])

    def test_not_gguf(self):
        p = f"{TMP}/x.gguf"
        open(p, "wb").write(b"nope")
        with self.assertRaises(ValueError):
            gguf.read_metadata(p)


class TestFit(unittest.TestCase):
    def test_levels(self):
        info = {"attn_layers": 48, "kv_heads": 4, "key_length": 128, "value_length": 128, "experts": 128}
        self.assertEqual(models.fit(20.5, info, 32768, "q8_0", HW)["level"], "moe")
        self.assertEqual(models.fit(9.0, {**info, "experts": 0}, 8192, "q8_0", HW)["level"], "gpu")
        self.assertEqual(models.fit(80, {**info, "experts": 0}, 8192, "q8_0", HW)["level"], "no")

    def test_kv_cache_size(self):
        # 48 layers * 4 heads * 256 * 2 bytes * 32768 tokens = 3 GiB
        info = {"attn_layers": 48, "kv_heads": 4, "key_length": 128, "value_length": 128}
        self.assertAlmostEqual(models.kv_cache_gb(info, 32768, "f16"), 3.0, places=2)

    def test_quant_names(self):
        self.assertEqual(models.quant_from_name("Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf"), "UD-Q4_K_XL")
        self.assertEqual(models.quant_from_name("model-Q8_0-00001-of-00003.gguf"), "Q8_0")
        self.assertEqual(models.quant_from_name("gpt-oss-20b-mxfp4.gguf"), "MXFP4")

    def test_pick_quant(self):
        choices = [{"kind": "model", "name": f"m-{q}.gguf", "quant": q, "size_gb": s, "files": [f"m-{q}.gguf"]}
                   for q, s in (("Q3_K_M", 6), ("Q4_K_M", 8), ("Q5_K_M", 9.5), ("Q6_K", 11), ("Q8_0", 14.5))]
        pick, f = models.pick_quant(choices, HW, 8192)
        self.assertEqual(pick["quant"], "Q6_K")             # best that fits in 16 GB with room for context
        pick, f = models.pick_quant(choices, {"vram_total": 8, "ram_total": 16}, 8192)
        self.assertEqual(pick["quant"], "Q3_K_M")


class TestLibrary(unittest.TestCase):
    def test_scan_groups_shards_and_pairs_mmproj(self):
        lib = models.Library(config.Settings())
        ms = lib.scan(force=True)
        files = {m["file"]: m for m in ms}
        self.assertIn("Huge-Q4_K_M.gguf", files)
        self.assertEqual(files["Huge-Q4_K_M.gguf"]["parts"], 2)
        self.assertAlmostEqual(files["Huge-Q4_K_M.gguf"]["size_gb"], 80, places=0)
        q = files["Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf"]
        self.assertTrue(q["mmproj"].endswith("mmproj-F16.gguf"))
        self.assertEqual(lib.resolve("*35B-A3B*")["file"], "Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf")


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pumpkin.core import Core
        from pumpkin.server import Web
        s = config.Settings()
        s.update({"port": 18190, "llama_port": 18191, "vision_port": 18193, "keep_awake": False, "notifications": False})
        cls.core = Core()
        # pretend to have a 16 GB GPU so fit checks are predictable on any machine
        cls.core.hw = {"main_gpu": {"vendor": "AMD", "name": "Test GPU", "vram_total": 16.0}, "ram_total": 30.0,
                       "backend": "vulkan", "gpus": [], "cpu": {"physical_cores": 8}}
        cls.core.hw_at = time.time() + 10 ** 6
        cls.web = Web(cls.core)
        cls.web.serve("127.0.0.1")
        cls.base = "http://127.0.0.1:18190"
        cls.tok = config.token()

    @classmethod
    def tearDownClass(cls):
        cls.core.shutdown()
        cls.web.shutdown()

    def call(self, method, path, body=None, auth=True):
        h = {"Content-Type": "application/json", "X-Pumpkin": "1"}
        if auth:
            h["Authorization"] = f"Bearer {self.tok}"
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait(self, cond, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.3)
        return False

    def test_1_auth(self):
        code, _ = self.call("GET", "/pumpkin/api/state", auth=False)
        self.assertEqual(code, 401)
        code, j = self.call("GET", "/pumpkin/api/state")
        self.assertEqual(code, 200)
        self.assertTrue(j["has_server"])
        self.assertTrue(j["has_models"])

    def test_2_llama_probe(self):
        code, j = self.call("GET", "/pumpkin/api/llama")
        self.assertEqual(j["info"]["version"], "b11796")
        self.assertIn("--agent", j["info"]["flags"])

    def test_3_preset_start_chat_and_task(self):
        code, lib = self.call("GET", "/pumpkin/api/models")
        tiny = next(m for m in lib["models"] if m["file"] == "tiny-Q8_0.gguf")
        self.assertEqual(tiny["fit"]["level"], "gpu")
        code, p = self.call("POST", "/pumpkin/api/presets/from-model", {"path": tiny["path"]})
        self.assertEqual(code, 200, p)
        self.assertTrue(p["tools"])
        code, st = self.call("POST", "/pumpkin/api/llama/start", {"preset": p["id"]})
        self.assertEqual(st["state"], "loading")
        self.assertTrue(self.wait(lambda: self.call("GET", "/pumpkin/api/state")[1]["server"]["state"] == "ready"))
        log = open(os.path.join(config.STATE_DIR, "llama-server.log")).read()
        self.assertIn("--mcp-servers-config", log)          # built-in web search attached
        mcp = json.load(open(os.path.join(config.STATE_DIR, "mcp-effective.json")))
        self.assertIn("web", mcp["mcpServers"])
        # pass-through to llama-server, streaming
        req = urllib.request.Request(self.base + "/v1/chat/completions",
                                     data=json.dumps({"stream": True, "messages": [{"role": "user", "content": "hi"}]}).encode(),
                                     headers={"Authorization": f"Bearer {self.tok}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode()
        self.assertIn("fake", body)
        self.assertIn("[DONE]", body)
        # an agent task: tool call then answer, saved as a report
        code, t = self.call("POST", "/pumpkin/api/tasks", {"name": "Pumpkin facts", "prompt": "Tell me about pumpkins",
                                                            "time": "08:00", "days": "daily", "preset": p["id"]})
        code, _ = self.call("POST", "/pumpkin/api/tasks/run", {"id": t["id"]})
        self.assertEqual(code, 200)
        self.assertTrue(self.wait(lambda: not self.call("GET", "/pumpkin/api/agent")[1]["busy"]))
        lines = [x["text"] for x in self.call("GET", "/pumpkin/api/agent")[1]["lines"]]
        self.assertTrue(any("web_search" in l for l in lines), lines)
        reports = self.call("GET", "/pumpkin/api/reports")[1]["items"]
        self.assertTrue(any("pumpkin-facts" in r["name"] for r in reports))
        txt = self.call("GET", "/pumpkin/api/reports?name=" + reports[0]["name"])[1]["text"]
        self.assertIn("Pumpkins are great", txt)
        # speed bench
        self.call("POST", "/pumpkin/api/bench/speed")
        self.assertTrue(self.wait(lambda: not self.call("GET", "/pumpkin/api/bench")[1]["running"]))
        self.assertEqual(self.call("GET", "/pumpkin/api/bench")[1]["history"][0]["gen"], "37.1")
        self.call("POST", "/pumpkin/api/llama/stop")
        self.assertEqual(self.call("GET", "/pumpkin/api/state")[1]["server"]["state"], "off")

    def test_4_failure_is_explained(self):
        os.environ["FAKE_LLAMA_FAIL"] = "1"
        try:
            p = self.core.presets.all()[0]
            self.call("POST", "/pumpkin/api/llama/start", {"preset": p["id"]})
            self.assertTrue(self.wait(lambda: self.call("GET", "/pumpkin/api/state")[1]["server"]["state"] == "error"))
            srv = self.call("GET", "/pumpkin/api/state")[1]["server"]
            self.assertIn("did not fit", srv["error"])
            # before giving up, Pumpkin tried by itself with a shorter context, down to 2,048 tokens
            self.assertIn("so Pumpkin started it with", srv["notice"] or "")
            ctx = int(p.get("ctx") or 8192)
            self.assertIn(f"-c {max(2048, ctx // 4)} ", open(os.path.join(config.STATE_DIR, "llama-server.log")).read())
            self.assertEqual(self.core.presets.get(p["id"])["ctx"], p.get("ctx"))   # nothing worked: preset unchanged
        finally:
            del os.environ["FAKE_LLAMA_FAIL"]

    def test_4b_a_smaller_context_is_tried_and_kept(self):
        p = self.core.presets.all()[0]
        self.core.presets.update(p["id"], {"ctx": 16384})
        os.environ["FAKE_LLAMA_FAIL_ABOVE"] = "4096"
        try:
            self.call("POST", "/pumpkin/api/llama/start", {"preset": p["id"]})
            self.assertTrue(self.wait(lambda: self.call("GET", "/pumpkin/api/state")[1]["server"]["state"] == "ready",
                                      30))
            srv = self.call("GET", "/pumpkin/api/state")[1]["server"]
            self.assertIn("with 4,096", srv["notice"])
            saved = self.core.presets.get(p["id"])
            self.assertEqual((saved["ctx"], saved["ctx_worked"]), (16384, 4096))   # your setting stays as you set it
            # next start goes straight to what worked, and says so
            self.core.server.stop()
            self.core.server.start(saved)
            self.assertTrue(self.wait(lambda: self.core.server.state == "ready", 30))
            self.assertIn("-c 4096 ", open(os.path.join(config.STATE_DIR, "llama-server.log")).read())
            self.assertIn("16,384 did not fit", self.core.server.notice)
            # picking another size tries that one
            self.core.server.stop()
            self.core.presets.update(p["id"], {"ctx": 2048})
            self.core.server.start(self.core.presets.get(p["id"]))
            self.assertTrue(self.wait(lambda: self.core.server.state == "ready", 30))
            with open(os.path.join(config.STATE_DIR, "llama-server.log")) as f:
                self.assertIn("-c 2048 ", f.read())
            self.assertIsNone(self.core.server.notice)
        finally:
            del os.environ["FAKE_LLAMA_FAIL_ABOVE"]
            self.core.presets.update(p["id"], {"ctx": p.get("ctx")})
            self.core.server.stop()

    def test_4b2_a_vision_file_that_does_not_fit_the_model_is_dropped(self):
        code, lib = self.call("GET", "/pumpkin/api/models")
        qwen = next(m for m in lib["models"] if m["file"].startswith("Qwen3.6"))
        self.assertTrue(qwen.get("mmproj"))
        code, p = self.call("POST", "/pumpkin/api/presets/from-model", {"path": qwen["path"]})
        self.assertTrue(p["mmproj"])
        os.environ["FAKE_MMPROJ_FAIL"] = "1"
        try:
            self.call("POST", "/pumpkin/api/llama/start", {"preset": p["id"]})
            ok = self.wait(lambda: self.call("GET", "/pumpkin/api/state")[1]["server"]["state"] == "ready", 30)
            st = self.call("GET", "/pumpkin/api/state")[1]["server"]
            self.assertTrue(ok, st)
            self.assertIn("without pictures", st.get("notice") or "")
            with open(os.path.join(config.STATE_DIR, "llama-server.log")) as f:
                self.assertNotIn("--mmproj", f.read())
            self.assertEqual(self.core.presets.get(p["id"])["mmproj"], "")   # next time it starts first try
        finally:
            os.environ.pop("FAKE_MMPROJ_FAIL", None)
            self.call("POST", "/pumpkin/api/llama/stop")
            self.core.presets.delete(p["id"])

    def test_4c_deleting_stops_the_model(self):
        """Deleting the running preset, or the model file under it, frees the memory at once."""
        p = self.core.presets.add({"name": "Throwaway", "model": self.core.presets.all()[0]["model"], "ctx": 2048})
        self.call("POST", "/pumpkin/api/llama/start", {"preset": p["id"]})
        self.assertTrue(self.wait(lambda: self.core.server.state == "ready", 30))
        pid = self.core.server.proc.pid
        code, _ = self.call("DELETE", f"/pumpkin/api/presets/{p['id']}")
        self.assertEqual(code, 200)
        self.assertEqual(self.core.server.state, "off")
        self.assertIsNone(self.core.server.proc)
        self.assertFalse(llama._alive_llama(pid) if os.name != "nt" else False)
        self.assertFalse(os.path.exists(llama.PID_PATH))
        # the model file of the running preset: stopped before the file goes
        import shutil
        src = self.core.presets.all()[0]["model"]
        copy = os.path.join(os.path.dirname(src), "copy-Q8_0.gguf")
        shutil.copy(src, copy)
        self.core.library.scan(force=True)
        q = self.core.presets.add({"name": "Copy", "model": copy, "ctx": 2048})
        self.core.server.start(q)
        self.assertTrue(self.wait(lambda: self.core.server.state == "ready", 30))
        code, j = self.call("POST", "/pumpkin/api/models/delete", {"path": copy})
        self.assertEqual((code, j["removed"]), (200, 1))
        self.assertEqual(self.core.server.state, "off")
        self.core.presets.delete(q["id"])

    @unittest.skipIf(os.name == "nt", "the fake llama-server is a Python script behind a .cmd file on Windows")
    def test_3b_the_conversation_survives_a_restart(self):
        pre = self.core.presets.all()[0]
        calls = os.path.join(os.path.dirname(os.path.abspath(pre["model"])), "slot-calls.txt")
        if os.path.exists(calls):
            os.remove(calls)
        ready = lambda: self.call("GET", "/pumpkin/api/state")[1]["server"]["state"] == "ready"
        if not ready():
            self.call("POST", "/pumpkin/api/llama/start", {"preset": pre["id"]})
            self.assertTrue(self.wait(ready))
        log = open(os.path.join(config.STATE_DIR, "llama-server.log")).read()
        self.assertIn("--slot-save-path", log)
        self.call("POST", "/pumpkin/api/llama/stop")                       # saves what the model has read
        saved = open(calls).read()
        self.assertIn("save ", saved)
        name = saved.split()[1]
        self.assertTrue(os.path.isfile(os.path.join(config.DATA_DIR, "contexts", name)))
        self.call("POST", "/pumpkin/api/llama/start", {"preset": pre["id"]})
        self.assertTrue(self.wait(ready))
        self.assertTrue(self.wait(lambda: "restore " + name in open(calls).read()), open(calls).read())
        # switched off in settings: nothing is saved
        self.core.settings.update({"keep_context": False})
        os.remove(calls)
        self.call("POST", "/pumpkin/api/llama/stop")
        self.assertFalse(os.path.exists(calls))
        self.core.settings.update({"keep_context": True})
        self.call("POST", "/pumpkin/api/llama/start", {"preset": pre["id"]})
        self.assertTrue(self.wait(ready))

    def test_3c_a_text_only_model_sees_pictures(self):
        from fixtures_img import png_bytes
        import base64 as b64
        tiny = next(p for p in self.core.presets.all() if "tiny" in p["model"])
        ready = lambda: self.call("GET", "/pumpkin/api/state")[1]["server"]["state"] == "ready"
        self.call("POST", "/pumpkin/api/llama/start", {"preset": tiny["id"]})
        self.assertTrue(self.wait(ready))
        code, v = self.call("GET", "/pumpkin/api/vision")
        self.assertTrue(v["available"], v)                     # the Qwen model has a vision projector
        self.assertFalse(v["text_sees"])
        code, props = self.call("GET", "/props")
        self.assertTrue(props["modalities"]["vision"])          # the chat lets you attach a picture
        url = "data:image/png;base64," + b64.b64encode(png_bytes()).decode()
        msg = {"role": "user", "content": [{"type": "text", "text": "What does it say?"},
                                           {"type": "image_url", "image_url": {"url": url}}]}
        req = urllib.request.Request(self.base + "/v1/chat/completions",
                                     data=json.dumps({"stream": True, "model": tiny["name"], "messages": [msg]}).encode(),
                                     headers={"Authorization": f"Bearer {self.tok}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            self.assertIn("[DONE]", r.read().decode())
        got = json.load(open(os.path.join(os.path.dirname(os.path.abspath(tiny["model"])), "chat-18191.json")))
        sent = json.dumps(got["messages"])
        self.assertNotIn("image_url", sent)                   # the text model only gets text
        self.assertIn("HARVEST 42", sent)
        self.assertTrue(ready())                              # it came back after making room
        self.core.vision.stop()

    @unittest.skipIf(os.name == "nt", "the fake llama-server is a Python script behind a .cmd file on Windows")
    def test_4d_a_leftover_server_is_stopped_at_start(self):
        import subprocess
        import sys
        proc = subprocess.Popen([sys.executable, os.path.join(TMP, "bin", "llama-server"), "--port", "18999"],
                                start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.makedirs(os.path.dirname(llama.PID_PATH), exist_ok=True)
        with open(llama.PID_PATH, "w") as f:
            f.write(str(proc.pid))
        self.assertTrue(llama.kill_leftover())
        proc.wait(10)
        self.assertIsNotNone(proc.returncode)
        self.assertFalse(llama.kill_leftover())                   # nothing left the second time

    def test_5_crud_and_doctor(self):
        code, per = self.call("POST", "/pumpkin/api/personas", {"name": "Pirate", "prompt": "Arr."})
        code, _ = self.call("POST", "/pumpkin/api/persona", {"id": per["id"]})
        self.assertEqual(self.core.persona_prompt(), "Arr.")
        code, j = self.call("PUT", "/pumpkin/api/mcp", {"text": "not json"})
        self.assertEqual(code, 400)
        code, j = self.call("GET", "/pumpkin/api/doctor")
        titles = {c["title"]: c["status"] for c in j["checks"]}
        self.assertEqual(titles["llama-server"], "ok")
        code, j = self.call("POST", "/pumpkin/api/models/delete", {"path": "/etc/passwd"})
        self.assertEqual(code, 400)

    def test_7_chat_switches_models(self):
        code, lib = self.call("GET", "/pumpkin/api/models")
        small = next(m for m in lib["models"] if m["file"] == "tiny-Q8_0.gguf")
        a = self.call("POST", "/pumpkin/api/presets/from-model", {"path": small["path"], "name": "Switch A"})[1]
        b = self.call("POST", "/pumpkin/api/presets/from-model", {"path": small["path"], "name": "Switch B"})[1]
        self.call("POST", "/pumpkin/api/llama/stop")

        def get(path, body=None):
            req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body else None,
                                         headers={"Authorization": f"Bearer {self.tok}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.status, json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")

        code, j = get("/v1/models")                      # works with no model running: the chat lists presets
        names = [m["id"] for m in j["data"]]
        self.assertIn(a["name"], names)
        self.assertIn(b["name"], names)
        code, j = get("/v1/chat/completions", {"model": a["name"], "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 200, j)                   # started Switch A, then answered
        # llama-server itself refuses anyone without Pumpkin's private key, and the key is not on its command line
        direct = urllib.request.Request("http://127.0.0.1:18191/v1/chat/completions",
                                        data=b'{"messages": [{"role": "user", "content": "hi"}]}',
                                        headers={"Content-Type": "text/plain"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(direct, timeout=10)
        self.assertEqual(e.exception.code, 401)
        from pumpkin import llama
        self.assertNotIn(llama.api_key(), open(os.path.join(config.STATE_DIR, "llama-server.log")).read())
        if os.name != "nt":                                   # Windows has no permission bits; the user profile is private
            self.assertEqual(oct(os.stat(llama.KEY_PATH).st_mode & 0o777), "0o600")
        self.assertEqual(self.call("GET", "/pumpkin/api/state")[1]["server"]["preset"]["id"], a["id"])
        code, j = get("/v1/chat/completions", {"model": b["name"], "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 200, j)
        self.assertEqual(self.call("GET", "/pumpkin/api/state")[1]["server"]["preset"]["id"], b["id"])
        status = {m["id"]: m["status"]["value"] for m in get("/v1/models")[1]["data"]}
        self.assertEqual(status[b["name"]], "loaded")
        self.assertEqual(status[a["name"]], "unloaded")
        # the chat's "Load model" button
        code, j = get("/models/load", {"model": a["name"]})
        self.assertEqual(code, 200, j)
        self.assertTrue(self.wait(lambda: (self.call("GET", "/pumpkin/api/state")[1]["server"]["preset"] or {}).get("id") == a["id"]
                                  and self.call("GET", "/pumpkin/api/state")[1]["server"]["state"] == "ready"))
        code, j = get("/models/unload", {"model": a["name"]})
        self.assertEqual(self.call("GET", "/pumpkin/api/state")[1]["server"]["state"], "off")
        code, j = get("/models/load", {"model": "not a preset"})
        self.assertEqual(code, 404)
        self.call("POST", "/pumpkin/api/llama/stop")

    def test_8_setup_goal_and_update_endpoints(self):
        code, j = self.call("POST", "/pumpkin/api/setup/goal", {"goal": "coding"})
        self.assertEqual(j["persona"], "Coder")
        code, j = self.call("POST", "/pumpkin/api/setup/goal", {"goal": "nope"})
        self.assertEqual(code, 400)
        code, j = self.call("GET", "/pumpkin/api/models/discover")
        self.assertEqual(code, 200)
        self.assertIn("found", j)
        self.assertIn("update", self.call("GET", "/pumpkin/api/state")[1])

    def test_9_files(self):
        import http.client
        base = os.path.join(TMP, "Pumpkin", "files")

        def raw(method, path, body=None, headers=None):
            c = http.client.HTTPConnection("127.0.0.1", 18190, timeout=10)
            c.request(method, path, body=body, headers={"Authorization": f"Bearer {self.tok}", **(headers or {})})
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()

        code, _, body = raw("POST", "/pumpkin/api/files/upload?dir=docs&name=plan.md", b"# Plan",
                            {"X-Pumpkin": "1", "Content-Type": "application/octet-stream"})
        self.assertEqual(code, 200, body)
        self.assertTrue(os.path.isfile(os.path.join(base, "docs", "plan.md")))
        code, _, body = raw("POST", "/pumpkin/api/files/upload?name=x.md", b"x")          # no X-Pumpkin header
        self.assertEqual(code, 403)
        code, j = self.call("GET", "/pumpkin/api/files?path=docs")
        self.assertEqual([i["name"] for i in j["items"]], ["plan.md"])
        code, h, body = raw("GET", "/pumpkin/api/files/download?path=/workspace/docs/plan.md&inline=1")
        self.assertEqual((code, body), (200, b"# Plan"))
        self.assertTrue(h["Content-Type"].startswith("text/plain"))
        with open(os.path.join(base, "evil.html"), "w") as f:
            f.write("<script>alert(1)</script>")
        code, h, _ = raw("GET", "/pumpkin/api/files/download?path=evil.html&inline=1")
        self.assertEqual(h["Content-Type"], "application/octet-stream")
        self.assertTrue(h["Content-Disposition"].startswith("attachment"))
        self.assertIn("sandbox", h["Content-Security-Policy"])
        code, _, _ = raw("GET", "/pumpkin/api/files/download?path=../../.config/pumpkin/token")
        self.assertEqual(code, 403)
        code, _, _ = raw("GET", "/pumpkin/api/files/download?path=docs/plan.md", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(code, 401)
        code, j = self.call("POST", "/pumpkin/api/files/delete", {"path": "docs"})
        self.assertFalse(os.path.exists(os.path.join(base, "docs")))
        code, j = self.call("POST", "/pumpkin/api/files/delete", {"path": ""})
        self.assertEqual(code, 403)

    def test_6_login_page(self):
        req = urllib.request.Request(self.base + "/", headers={"Accept": "text/html"})
        with urllib.request.urlopen(req) as r:
            self.assertIn(b"Access key", r.read())
            self.assertEqual(r.headers["X-Frame-Options"], "SAMEORIGIN")
            self.assertEqual(r.headers["Referrer-Policy"], "no-referrer")
            self.assertIn("frame-ancestors", r.headers["Content-Security-Policy"])

    def test_6a_old_service_worker_removes_itself(self):
        req = urllib.request.Request(f"{self.base}/sw.js")
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode()
            self.assertIn("javascript", r.headers["Content-Type"])
        self.assertIn("registration.unregister()", body)
        self.assertNotIn("navigate", body)                       # never reloads the page, so it cannot loop

    def test_6b_dns_rebinding_is_refused(self):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", 18190, timeout=5)
        c.request("GET", "/pumpkin/api/state", headers={"Host": "evil.example.com", "Authorization": f"Bearer {self.tok}"})
        self.assertEqual(c.getresponse().status, 421)
        c = http.client.HTTPConnection("127.0.0.1", 18190, timeout=5)
        c.request("GET", "/pumpkin/api/state", headers={"Host": "localhost:18190", "Authorization": f"Bearer {self.tok}"})
        self.assertEqual(c.getresponse().status, 200)


if __name__ == "__main__":
    unittest.main()
