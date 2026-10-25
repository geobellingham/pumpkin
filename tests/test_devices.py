"""Two Pumpkins on one machine: one shares its model, the other pairs with it and hands it tasks.
Run with: python3 -m unittest tests.test_devices"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import fixtures  # noqa: E402

_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


class _w:
    """json.dump target that closes itself."""
    def __init__(self, path):
        self.f = open(path, "w")

    def write(self, s):
        self.f.write(s)
        self.f.flush()

    def __del__(self):
        self.f.close()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Instance:
    def __init__(self, name, with_model):
        self.home = tempfile.mkdtemp(prefix=f"pumpkin-{name}-")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "PUMPKIN_"))}
        self.env.update(HOME=self.home, USERPROFILE=self.home, XDG_CONFIG_HOME=f"{self.home}/.config",
                        XDG_DATA_HOME=f"{self.home}/.local/share", XDG_STATE_HOME=f"{self.home}/.local/state",
                        PATH=f"{self.home}/bin" + os.pathsep + os.environ["PATH"], PYTHONPATH=ROOT, FAKE_LLAMA_LOAD="0.3",
                        PUMPKIN_FAKE_HARDWARE=f"{self.home}/hw.json")
        os.makedirs(f"{self.home}/bin")
        os.makedirs(f"{self.home}/.config/pumpkin")
        fixtures.make_fake_server(f"{self.home}/bin/llama-server")
        json.dump({"main_gpu": None, "gpus": [], "cpu": {"model": "Test CPU", "physical_cores": 4,
                   "logical_cores": 8}, "ram_total": 16, "ram_available": 12, "backend": "cpu", "disk": 100},
                  _w(f"{self.home}/hw.json"))
        self.port, self.llama_port = free_port(), free_port()
        json.dump({"port": self.port, "llama_port": self.llama_port, "keep_awake": False, "notifications": False,
                   "check_updates": False, "device_name": name, "setup_done": True},
                  _w(f"{self.home}/.config/pumpkin/settings.json"))
        if with_model:
            fixtures.make_gguf(f"{self.home}/Pumpkin/models/small/tiny-Q8_0.gguf", arch="llama", name="Tiny",
                               layers=16, heads=16, kv_heads=8, embd=1024, key_len=64, experts=0, file_type=7,
                               size_gb=0.6)
        self.log = open(f"{self.home}/pumpkin.log", "w")
        self.proc = subprocess.Popen([sys.executable, "-m", "pumpkin", "serve"], env=self.env, cwd=ROOT,
                                     stdout=self.log, stderr=subprocess.STDOUT)
        self.token = ""
        for _ in range(300):
            try:
                with open(f"{self.home}/.config/pumpkin/token") as f:
                    self.token = f.read().strip()
                # ready means a real answer with this key: a "sign in first" (key file still being written) is not
                if self.token and "version" in self.get("state"):
                    break
            except (OSError, urllib.error.URLError, ConnectionError, ValueError):
                pass
            time.sleep(0.1)

    def api(self, path, body=None, method=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/pumpkin/api/{path}",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     method=method or ("POST" if body is not None else "GET"),
                                     headers={"Authorization": "Bearer " + self.token, "X-Pumpkin": "1",
                                              "Content-Type": "application/json"})
        try:
            with _open(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return {"status": e.code, **json.loads(e.read() or b"{}")}

    get = api

    def stop(self):
        self.proc.terminate()
        self.log.close()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class TestDevices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = Instance("Helper", with_model=True)
        cls.boss = Instance("Boss", with_model=False)
        lib = cls.helper.api("models")["models"]
        cls.helper.api("presets/from-model", {"path": lib[0]["path"], "name": "Tiny helper"})

    @classmethod
    def tearDownClass(cls):
        cls.helper.stop()
        cls.boss.stop()

    def peer(self, inst, path, key, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{inst.port}/pumpkin/peer/{path}",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            with _open(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_1_sharing_off_by_default(self):
        st = self.helper.api("devices/share")
        self.assertFalse(st["share"])
        self.assertIsNone(st["code"])
        code, _ = self.peer(self.helper, "info", "x" * 32)
        self.assertEqual(code, 403)

    def test_2_pair_and_ask(self):
        st = self.helper.api("devices/share", {"on": True})
        self.assertTrue(st["share"])
        self.assertEqual(st["remote"], "lan")                      # sharing opened it to the network
        time.sleep(1.0)                                            # it re-listens on all addresses
        code = self.helper.api("devices/share")["code"]
        self.assertTrue(code.startswith("pumpkin-pair:"))
        # the device key is not the sign-in key, and the sign-in key does not open the peer endpoints
        self.assertNotIn(self.helper.token, code)
        self.assertEqual(self.peer(self.helper, "info", self.helper.token)[0], 401)

        bad = self.boss.api("devices", {"code": "hello"})
        self.assertIn("not a Pumpkin pairing code", bad["error"])

        dev = self.boss.api("devices", {"code": code})
        self.assertEqual(dev.get("name"), "Helper", dev)
        self.assertNotIn("key", dev)
        lst = self.boss.api("devices")["devices"]
        self.assertEqual(len(lst), 1)
        self.assertTrue(lst[0]["online"])
        self.assertIn("Tiny helper Q8_0", lst[0]["info"]["models"])

        # the boss asks: the helper starts its model and answers
        r = self.boss.api(f"devices/{dev['id']}/test", {})
        self.assertIn("Pumpkins are great", r.get("answer", ""), r)
        self.assertEqual(r["model"], "Tiny helper Q8_0")
        recent = self.helper.api("devices/share")["recent"]
        self.assertEqual(recent[0]["from"], "Boss")
        self.assertEqual(recent[0]["state"], "done")

        # the key gives no access to the rest of Pumpkin
        key = json.loads(__import__("base64").urlsafe_b64decode(code.split(":", 1)[1] + "==="))["k"]
        req = urllib.request.Request(f"http://127.0.0.1:{self.helper.port}/pumpkin/api/settings",
                                     headers={"Authorization": "Bearer " + key})
        with self.assertRaises(urllib.error.HTTPError) as e:
            _open(req, timeout=10)
        self.assertEqual(e.exception.code, 401)

    def test_2b_chat_with_the_other_devices_model(self):
        """The boss has no model of its own, yet its chat lists the helper's model and answers with it."""
        def raw(path, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{self.boss.port}{path}",
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Authorization": "Bearer " + self.boss.token,
                                                  "Content-Type": "application/json"})
            with _open(req, timeout=60) as r:
                return r.read().decode()

        self.boss.api("devices")                                    # refreshes the list the chat uses
        ids = [m["id"] for m in json.loads(raw("/v1/models"))["data"]]
        self.assertIn("Tiny helper Q8_0 (on Helper)", ids)
        out = raw("/v1/chat/completions", {"model": "Tiny helper Q8_0 (on Helper)", "stream": True,
                                           "messages": [{"role": "user", "content": "Hi there"}]})
        words = "".join(json.loads(l[6:])["choices"][0]["delta"]["content"] for l in out.splitlines()
                        if l.startswith("data: {"))
        self.assertEqual(words, "Hello from the fake model.")
        r = json.loads(raw("/v1/chat/completions", {"model": "Tiny helper Q8_0 (on Helper)",
                                                     "messages": [{"role": "user", "content": "Hi"}]}))
        self.assertIn("Pumpkins are great", r["choices"][0]["message"]["content"])
        recent = self.helper.api("devices/share")["recent"]
        self.assertEqual((recent[0]["from"], recent[0]["state"]), ("Boss", "done"))
        self.assertTrue(recent[0]["task"].startswith("Chat: Hi"))
        # loading a device's model from the chat is fine (it starts over there when asked)
        self.assertIn("success", raw("/models/load", {"model": "Tiny helper Q8_0 (on Helper)"}))

    def test_3_model_tools(self):
        """The MCP server the boss's model uses: list and ask."""
        env = dict(self.boss.env)
        p = subprocess.run([sys.executable, "-m", "pumpkin.mcp_devices"], env=env, cwd=ROOT, capture_output=True,
                           text=True, timeout=120, input="\n".join(json.dumps(m) for m in [
                               {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                               {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                               {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                "params": {"name": "list", "arguments": {}}},
                               {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                "params": {"name": "ask", "arguments": {"device": "help", "task": "Say hi"}}},
                               {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                "params": {"name": "ask", "arguments": {"device": "Laptop", "task": "Say hi"}}},
                           ]) + "\n")
        out = {j["id"]: j for j in map(json.loads, p.stdout.splitlines())}
        self.assertEqual({t["name"] for t in out[2]["result"]["tools"]}, {"list", "ask"})
        self.assertIn("Helper", out[3]["result"]["content"][0]["text"])
        self.assertIn("online", out[3]["result"]["content"][0]["text"])
        self.assertIn("Pumpkins are great", out[4]["result"]["content"][0]["text"])
        self.assertIn("answer from Helper", out[4]["result"]["content"][0]["text"])
        self.assertTrue(out[5]["result"]["isError"])
        self.assertIn("No device called Laptop", out[5]["result"]["content"][0]["text"])

    def test_4_boss_model_gets_the_tools(self):
        sys.path.insert(0, ROOT)
        from pumpkin import llama  # noqa: F401
        # the effective MCP file is written when the boss starts a model; check what it would contain
        code = ("import json,sys; from pumpkin.core import Core; c = Core(); p = c.mcp_config({'web': False});"
                "print(json.load(open(p))['mcpServers']['devices']['args'])")
        p = subprocess.run([sys.executable, "-c", code], env=self.boss.env, cwd=ROOT, capture_output=True,
                           text=True, timeout=60)
        self.assertIn("pumpkin.mcp_devices", p.stdout, p.stderr[-1500:])

    def test_5_switch_off_and_new_key(self):
        dev = self.boss.api("devices")["devices"][0]
        self.boss.api(f"devices/{dev['id']}", {"enabled": False}, method="PATCH")
        r = subprocess.run([sys.executable, "-c", "from pumpkin import devices; devices.ask('Helper', 'x')"],
                           env=self.boss.env, cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertIn("switched off", r.stderr)
        self.boss.api(f"devices/{dev['id']}", {"enabled": True}, method="PATCH")
        self.helper.api("devices/share", {"new_key": True})          # old pairings stop working
        lst = self.boss.api("devices")["devices"]
        self.assertFalse(lst[0]["online"])
        self.assertIn("key", lst[0]["problem"])
        st = self.helper.api("devices/share", {"on": False})
        self.assertEqual(st["remote"], "off")                       # sharing gave back what it opened
        self.assertIsNone(st["code"])
        self.boss.api(f"devices/{dev['id']}", method="DELETE")
        self.assertEqual(self.boss.api("devices")["devices"], [])


class TestNames(unittest.TestCase):
    def test_any_device_name_fits_in_the_header(self):
        sys.path.insert(0, ROOT)
        from pumpkin import devices
        for name in ("Лаптоп на Георги", "王的电脑", "Georgi’s laptop", "Georgi's laptop", "100% pumpkin"):
            v = devices.header_name(name)
            v.encode("latin-1")                                       # what http.client does with a header
            self.assertEqual(devices.name_from_header(v), name)
        self.assertEqual(devices.header_name("Georgi's laptop"), "Georgi's laptop")   # plain names stay readable


if __name__ == "__main__":
    unittest.main()
