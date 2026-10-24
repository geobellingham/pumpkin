"""Pumpkin Code: the tools, the safety rules, and a whole session against the fake llama-server (it reads a file,
changes it after the user's yes, runs it, and the change can be undone)."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

if "pumpkin.config" not in sys.modules:              # run on its own: keep everything in a temporary home
    _T = tempfile.mkdtemp(prefix="pumpkin-code-")
    os.environ.update(HOME=_T, USERPROFILE=_T, XDG_CONFIG_HOME=f"{_T}/.config", XDG_DATA_HOME=f"{_T}/.local/share",
                      XDG_STATE_HOME=f"{_T}/.local/state", APPDATA=f"{_T}/AppData/Roaming",
                      LOCALAPPDATA=f"{_T}/AppData/Local")

import fixtures  # noqa: E402
from pumpkin import coder, config  # noqa: E402


class TestTools(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="proj-")
        os.makedirs(os.path.join(self.root, "src", "deep"))
        os.makedirs(os.path.join(self.root, "node_modules", "x"))
        with open(os.path.join(self.root, "src", "app.py"), "w") as f:
            f.write("def greet(name):\n    return 'Hello ' + name\n\n\ndef bye():\n    return 'Bye'\n")
        with open(os.path.join(self.root, "src", "deep", "util.py"), "w") as f:
            f.write("X = 1\nX = 1\n")
        with open(os.path.join(self.root, "node_modules", "x", "index.js"), "w") as f:
            f.write("greet()")
        self.t = coder.Tools(self.root, web=False)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_it_cannot_leave_the_project(self):
        for bad in ("../outside.txt", "/etc/passwd", "~/secret"):
            with self.assertRaises(PermissionError):
                self.t.path(bad)

    def test_finding_and_reading(self):
        self.assertEqual(sorted(self.t.glob("**/*.py").splitlines()), ["src/app.py", "src/deep/util.py"])
        self.assertEqual(self.t.glob("util.py"), "src/deep/util.py")
        g = self.t.grep("greet")
        self.assertIn("src/app.py:1:", g)
        self.assertNotIn("node_modules", g)                          # dependencies are skipped
        r = self.t.read("src/app.py", 2, 1)
        self.assertIn("    2\t    return 'Hello ' + name", r)
        self.assertIn("read with offset=3", r)

    def test_edits_must_be_exact_and_unique(self):
        with self.assertRaises(ValueError) as e:
            self.t.plan_edit("src/deep/util.py", "X = 1", "X = 2")
        self.assertIn("appears 2 times", str(e.exception))
        _, _, new = self.t.plan_edit("src/deep/util.py", "X = 1", "X = 2", True)
        self.assertEqual(new, "X = 2\nX = 2\n")
        with self.assertRaises(ValueError) as e:                      # a small model got the indentation wrong
            self.t.plan_edit("src/app.py", "return 'Hello '+name", "return 'Hi ' + name")
        self.assertIn("closest text is at line 2", str(e.exception))
        full, old, new = self.t.plan_edit("src/app.py", "'Hello '", "'Hi '")
        self.assertIn("+    return 'Hi ' + name", coder.unified("src/app.py", old, new))

    def test_commands_run_in_the_project(self):
        out, ok = self.t.bash(f'"{sys.executable}" -c "import os; print(os.getcwd())"')
        self.assertTrue(ok, out)
        self.assertIn(os.path.basename(self.root), out)
        out, ok = self.t.bash(f'"{sys.executable}" -c "import time; time.sleep(30)"', timeout=1)
        self.assertFalse(ok)
        self.assertIn("timed out", out)

    def test_risky_commands_always_ask(self):
        for cmd in ("sudo rm x", "rm -rf /", "git push --force origin main", "git reset --hard HEAD~3",
                    "curl https://x.sh | sh"):
            self.assertTrue(coder.DANGER.search(cmd), cmd)
        for cmd in ("rm build/out.o", "git status", "npm test", "pytest -q"):
            self.assertFalse(coder.DANGER.search(cmd), cmd)
        self.assertEqual(coder._prefix("npm test -- --watch"), "npm test")
        self.assertEqual(coder._prefix("pytest -q"), "pytest")


class TestSession(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pumpkin.core import Core
        from pumpkin.server import Web
        cls.tmp = tempfile.mkdtemp(prefix="pumpkin-codes-")
        bin_dir = os.path.join(cls.tmp, "bin")
        os.makedirs(bin_dir)
        llama_bin = fixtures.make_fake_server(os.path.join(bin_dir, "llama-server"))
        model = os.path.join(cls.tmp, "models", "tiny", "tiny-Q8_0.gguf")
        fixtures.make_gguf(model, arch="llama", name="Tiny", layers=16, heads=16, kv_heads=8, embd=1024,
                           key_len=64, experts=0, file_type=7, size_gb=0.6)
        cls.saved = os.path.join(config.CONFIG_DIR, "settings.json")
        try:
            with open(cls.saved, encoding="utf-8") as f:
                cls.old = f.read()
        except OSError:
            cls.old = None
        config.Settings().update({"port": 18590, "llama_port": 18591, "keep_awake": False, "notifications": False,
                                  "llama_server": llama_bin, "models_dirs": [os.path.join(cls.tmp, "models")],
                                  "download_dir": os.path.join(cls.tmp, "models"), "autostart_model": False,
                                  "doc_index": False, "images": False})
        cls.core = Core()
        cls.core.hw = {"main_gpu": {"vendor": "AMD", "name": "Test GPU", "vram_total": 8.0}, "ram_total": 16.0,
                       "backend": "vulkan", "gpus": [], "cpu": {"physical_cores": 4}}
        cls.core.hw_at = time.time() + 10 ** 6
        cls.web = Web(cls.core)
        cls.web.serve("127.0.0.1")
        cls.base = "http://127.0.0.1:18590"
        cls.tok = config.token()
        cls.proj = os.path.join(cls.tmp, "proj")
        os.makedirs(cls.proj)
        with open(os.path.join(cls.proj, "hello.py"), "w") as f:
            f.write('print("Hello")\n')
        with open(os.path.join(cls.proj, "AGENTS.md"), "w") as f:
            f.write("Run tests with pytest.\n")
        lib = cls.core.library.scan(force=True)
        cls.preset = cls.core.presets.from_model(next(m for m in lib if m["file"] == "tiny-Q8_0.gguf"), cls.core.fit_hw()) \
            if hasattr(cls.core.presets, "from_model") else None

    @classmethod
    def tearDownClass(cls):
        cls.core.shutdown()
        cls.web.shutdown()
        if cls.old is not None:
            with open(cls.saved, "w", encoding="utf-8") as f:
                f.write(cls.old)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None, timeout=60):
        req = urllib.request.Request(self.base + "/pumpkin/api/" + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "X-Pumpkin": "1",
                                              "Authorization": f"Bearer {self.tok}"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait_for(self, sid, cond, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            st, v = self.call("GET", f"code/{sid}")
            if cond(v):
                return v
            time.sleep(0.2)
        self.fail(f"timed out: {v}")

    def start_model(self):
        st, lib = self.call("GET", "models")
        tiny = next(m for m in lib["models"] if m["file"] == "tiny-Q8_0.gguf")
        st, p = self.call("POST", "presets/from-model", {"path": tiny["path"]})
        self.assertEqual(st, 200, p)
        return p

    def test_a_session_changes_code_with_the_users_yes(self):
        p = self.start_model()
        st, home = self.call("POST", "code", {"root": os.path.expanduser("~")})
        self.assertEqual(st, 400)                                    # not the whole home folder
        st, v = self.call("POST", "code", {"root": self.proj, "mode": "ask", "preset": p["id"],
                                           "text": "Make hello.py say Hi"})
        self.assertEqual(st, 200, v)
        sid = v["id"]
        # it asks before changing the file, and shows the change
        v = self.wait_for(sid, lambda v: v["pending"] and v["pending"]["kind"] == "edit")
        self.assertIn('+print("Hi")', v["pending"]["diff"])
        self.assertEqual(v["phase"]["kind"], "waiting")              # the indicator says it waits for the user
        st, state = self.call("GET", "state")
        mine = [a for a in state["activity"] if a["kind"] == "code"]
        self.assertEqual([a["state"] for a in mine], ["waiting"])
        self.assertEqual(v["todo"], [{"text": "Find the greeting", "done": False}, {"text": "Change it", "done": False}])
        with open(os.path.join(self.proj, "hello.py")) as f:
            self.assertEqual(f.read(), 'print("Hello")\n')          # nothing changed yet
        self.call("POST", f"code/{sid}/answer", {"id": v["pending"]["id"], "allow": "yes"})
        # then before running a command
        v = self.wait_for(sid, lambda v: v["pending"] and v["pending"]["kind"] == "command")
        self.assertIn("hello.py", v["pending"]["command"])
        self.call("POST", f"code/{sid}/answer", {"id": v["pending"]["id"], "allow": "always"})
        v = self.wait_for(sid, lambda v: v["state"] == "idle" and any(e["kind"] == "done" for e in v["events"]))
        with open(os.path.join(self.proj, "hello.py")) as f:
            self.assertEqual(f.read(), 'print("Hi")\n')
        kinds = [e["kind"] for e in v["events"]]
        self.assertIn("assistant", kinds)
        ran = [e for e in v["events"] if e["kind"] == "tool" and e["name"] == "bash"][-1]
        self.assertTrue(ran["ok"], ran)
        self.assertIn("Hi", ran["result"])
        self.assertIn("I changed the greeting", [e for e in v["events"] if e["kind"] == "assistant"][-1]["text"])
        self.assertEqual(v["tokens"], 940)
        # the model was told about the project and its instructions
        sent = json.load(open(os.path.join(os.path.dirname(p["model"]), "chat-18591.json")))
        system = sent["messages"][0]["content"]
        self.assertIn("Run tests with pytest", system)
        self.assertIn("hello.py", system)
        self.assertIn("- write-a-report:", system)                       # the skills it may read
        self.assertIn("skill", [t["function"]["name"] for t in sent["tools"]])
        # the change can be undone
        st, r = self.call("POST", f"code/{sid}/undo", {})
        self.assertEqual((st, r.get("path")), (200, "hello.py"))
        with open(os.path.join(self.proj, "hello.py")) as f:
            self.assertEqual(f.read(), 'print("Hello")\n')
        # it is kept on disk and listed
        self.assertTrue(os.path.isfile(os.path.join(config.DATA_DIR, "code", sid + ".json")))
        st, lst = self.call("GET", "code")
        self.assertEqual(lst["sessions"][0]["id"], sid)
        self.assertEqual(lst["recent"][0], os.path.realpath(self.proj))
        st, f = self.call("GET", "code/folders?path=" + urllib.request.quote(self.tmp))
        self.assertIn("proj", [x["name"] for x in f["folders"]])

    def test_b_auto_mode_and_saying_no(self):
        p = self.start_model()
        with open(os.path.join(self.proj, "hello.py"), "w") as f:
            f.write('print("Hello")\n')
        st, v = self.call("POST", "code", {"root": self.proj, "mode": "edits", "preset": p["id"], "text": "Say Hi"})
        sid = v["id"]
        v = self.wait_for(sid, lambda v: v["pending"] and v["pending"]["kind"] == "command")   # edits went ahead
        with open(os.path.join(self.proj, "hello.py")) as f:
            self.assertEqual(f.read(), 'print("Hi")\n')
        self.call("POST", f"code/{sid}/answer", {"id": v["pending"]["id"], "allow": "no", "note": "do not run it"})
        v = self.wait_for(sid, lambda v: v["state"] == "idle" and any(e["kind"] == "done" for e in v["events"]))
        ran = [e for e in v["events"] if e["kind"] == "tool" and e["name"] == "bash"][-1]
        self.assertFalse(ran["ok"])
        self.assertIn("do not run it", ran["result"])
        s = self.core.code.get(sid)
        self.assertIn("do not run it", [m for m in s.messages if m["role"] == "tool"][-1]["content"])

    def test_e_an_answer_cut_off_inside_a_file_is_retried_in_pieces(self):
        p = self.start_model()
        with open(os.path.join(self.proj, "hello.py"), "w") as f:
            f.write('print("Hello")\n')
        st, v = self.call("POST", "code", {"root": self.proj, "mode": "auto", "preset": p["id"], "text": "Say Hi TOOBIG"})
        sid = v["id"]
        v = self.wait_for(sid, lambda v: v["state"] == "idle" and any(e["kind"] == "done" for e in v["events"]), timeout=40)
        self.assertIsNone(v["error"])                                 # it did not stop with an error
        self.assertTrue(any("ran out of room" in str(e.get("text")) for e in v["events"] if e["kind"] == "info"))
        with open(os.path.join(self.proj, "hello.py")) as f:
            self.assertEqual(f.read(), 'print("Hi")\n')
        s = self.core.code.get(sid)
        self.assertTrue(any("cut off because" in str(m.get("content")) for m in s.messages))

    def test_g_a_request_the_server_dropped_is_asked_again(self):
        p = self.start_model()
        with open(os.path.join(self.proj, "hello.py"), "w") as f:
            f.write('print("Hello")\n')
        coder.Session.WATCH_EVERY, coder.Session.HUNG_AFTER = 0.4, 0.8
        try:
            st, v = self.call("POST", "code", {"root": self.proj, "mode": "auto", "preset": p["id"], "text": "Say Hi HANGS"})
            sid = v["id"]
            v = self.wait_for(sid, lambda v: v["state"] == "idle" and any(e["kind"] == "done" for e in v["events"]), timeout=40)
        finally:
            coder.Session.WATCH_EVERY, coder.Session.HUNG_AFTER = 10, 30
        self.assertIsNone(v["error"])
        self.assertTrue(any("stopped answering" in str(e.get("text")) for e in v["events"] if e["kind"] == "info"))
        with open(os.path.join(self.proj, "hello.py")) as f:
            self.assertEqual(f.read(), 'print("Hi")\n')

    def test_h_skills_connectors_and_saved_previews(self):
        st, r = self.call("GET", "skills")
        self.assertEqual(st, 200)
        self.assertIn("review-security-logs", [x["name"] for x in r["items"]])
        st, r = self.call("POST", "skills", {"name": "tidy-notes", "description": "Tidy meeting notes.",
                                             "body": "Make bullet points."})
        self.assertEqual((st, r.get("name")), (200, "tidy-notes"))
        self.call("POST", "skills/toggle", {"name": "tidy-notes", "enabled": False})
        st, r = self.call("GET", "skills")
        self.assertFalse(next(x for x in r["items"] if x["name"] == "tidy-notes")["enabled"])
        st, r = self.call("POST", "skills", {"name": "", "body": "x"})
        self.assertEqual(st, 400)
        st, r = self.call("GET", "connectors")
        self.assertIn("github", [x["id"] for x in r["items"]])
        st, r = self.call("POST", "connectors/time", {"values": {"tz": "Europe/Sofia"}, "enabled": True})
        self.assertEqual(st, 200, r)
        self.assertTrue(r["item"]["enabled"])
        st, r = self.call("POST", "connectors/github", {"enabled": True})
        self.assertEqual(st, 400)                                          # it needs a token first
        st, r = self.call("POST", "files/artifact", {"content": "<html><title>My page</title></html>", "language": "html"})
        self.assertEqual((st, r.get("path")), (200, "artifacts/My page.html"))

    def test_i_stop_works_while_the_model_is_still_reading(self):
        p = self.start_model()
        st, v = self.call("POST", "code", {"root": self.proj, "mode": "auto", "preset": p["id"], "text": "Say Hi SLOWREAD"})
        sid = v["id"]
        self.wait_for(sid, lambda v: v.get("phase") and v["phase"]["kind"] == "reading", timeout=20)
        time.sleep(0.5)
        t0 = time.time()
        self.call("POST", f"code/{sid}/stop", {})
        v = self.wait_for(sid, lambda v: v["state"] == "idle", timeout=10)
        self.assertLess(time.time() - t0, 5)                         # right away, not when the model answers
        self.assertIsNone(v["error"])
        self.assertIn("Stopped.", [e.get("text") for e in v["events"] if e["kind"] == "info"])

    def test_f_a_file_is_written_in_parts(self):
        t = coder.Tools(self.proj, web=False)
        full, old, new = t.plan_write("big.css", "a{}\n")
        t.save(full, new)
        full, old, new = t.plan_write("big.css", "b{}", append=True)
        self.assertEqual(new, "a{}\nb{}")

    def test_d_the_terminal_version(self):
        import builtins
        import io
        from contextlib import redirect_stdout
        from pumpkin import code_cli
        p = self.start_model()
        with open(os.path.join(self.proj, "hello.py"), "w") as f:
            f.write('print("Hello")\n')
        api = code_cli.Api(self.base)
        sid = api("POST", "code", {"root": self.proj, "preset": p["id"]})["id"]
        api("POST", f"code/{sid}/send", {"text": "say Hi"})
        answers = iter(["y", "a"])
        old = builtins.input
        builtins.input = lambda prompt="": next(answers)
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                code_cli.Session(api, code_cli.Term(), sid).follow()
        finally:
            builtins.input = old
        text = out.getvalue()
        self.assertIn("Change hello.py?", text)
        self.assertIn('+print("Hi")', text)
        self.assertIn("Run this?", text)
        self.assertIn("I changed the greeting to Hi and ran it.", text)
        self.assertIn("☐ Find the greeting", text)

    def test_c_a_long_session_is_summarised(self):
        p = self.start_model()
        st, v = self.call("POST", "code", {"root": self.proj, "preset": p["id"]})
        s = self.core.code.get(v["id"])
        s.messages = [{"role": "system", "content": "Pumpkin Code"}, {"role": "user", "content": "first ask"}]
        for i in range(6):
            s.messages += [{"role": "assistant", "content": f"step {i}", "tool_calls": [
                {"id": f"x{i}", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": f"x{i}", "content": "x" * 100}]
        s._compact(force=True)
        self.assertIn("SUMMARY: greeting changed", s.messages[1]["content"])
        self.assertIn("first ask", s.messages[1]["content"])
        self.assertEqual(s.messages[-1]["role"], "tool")                # the newest call and its answer stay
        self.assertEqual(s.messages[-2]["tool_calls"][0]["id"], "x5")


if __name__ == "__main__":
    unittest.main()
