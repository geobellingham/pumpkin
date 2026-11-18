"""Claude and Pumpkin together: Claude connects over MCP (/mcp, or `pumpkin mcp` for Claude Desktop and Claude Code)
and hands jobs to the local model. Runs a real Pumpkin with the fake llama-server."""
import json
import os
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from test_devices import ROOT, Instance, _open  # noqa: E402


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestClaudeConnector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Instance("Claude", with_model=True)
        lib = cls.p.api("models")["models"]
        cls.p.api("presets/from-model", {"path": lib[0]["path"], "name": "Tiny"})
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.p.stop()

    def rpc(self, method, params=None, key=None, raw=None):
        TestClaudeConnector.n += 1
        body = raw if raw is not None else json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method,
                                                       "params": params or {}}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.p.port}/mcp", data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + (key if key is not None else self.p.token)})
        try:
            with _open(req, timeout=120) as r:
                data = r.read()
                return r.status, json.loads(data) if data else None
        except urllib.error.HTTPError as e:
            return e.code, None

    def tool(self, name, **args):
        st, r = self.rpc("tools/call", {"name": name, "arguments": args})
        self.assertEqual(st, 200)
        text = r["result"]["content"][0]["text"]
        if r["result"]["isError"]:
            return {"error": text}
        return json.loads(text)

    def test_1_claude_meets_pumpkin(self):
        st, r = self.rpc("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("bulk or mechanical", r["result"]["instructions"])     # when to hand work over
        names = {t["name"] for t in self.rpc("tools/list")[1]["result"]["tools"]}
        self.assertEqual(names, {"status", "ask", "batch", "result", "jobs", "cancel", "load"})
        st, _ = self.rpc("", raw=b'{"jsonrpc": "2.0", "method": "notifications/initialized"}')
        self.assertEqual(st, 202)
        s = self.tool("status")
        self.assertEqual(len(s["models"]), 1, s)                             # the preset Pumpkin made for the model
        self.assertGreater(s["models"][0]["context_tokens"], 0)

    def test_2_only_the_right_keys_open_it(self):
        self.assertEqual(self.rpc("tools/list", key="wrong")[0], 401)
        self.assertEqual(self.rpc("tools/list", key="")[0], 401)
        info = self.p.api("claude")
        self.assertIn("pumpkin mcp", info["claude_code"].replace('"', ""))
        self.assertIn('"mcpServers"', info["desktop_config"])
        # the Claude key works from another computer, and a new one shuts the old one out
        from_file = _read(os.path.join(self.p.home, ".config", "pumpkin", "claude_key")).strip() \
            if os.path.exists(os.path.join(self.p.home, ".config", "pumpkin", "claude_key")) else None
        self.p.api("claude/key", {})
        k = _read(os.path.join(self.p.home, ".config", "pumpkin", "claude_key")).strip()
        self.assertNotEqual(k, from_file)
        self.assertEqual(self.rpc("tools/list", key=k)[0], 200)
        self.p.api("claude/key", {})
        self.assertEqual(self.rpc("tools/list", key=k)[0], 401)
        # a browser page cannot get in with the sign-in cookie alone
        req = urllib.request.Request(f"http://127.0.0.1:{self.p.port}/mcp", data=b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as e:
            _open(req, timeout=10)
        self.assertEqual(e.exception.code, 401)

    def test_3_ask_and_get_the_answer(self):
        r = self.tool("ask", prompt="Say hi", system="ECHO", wait=60)
        self.assertEqual(r["state"], "done", r)
        self.assertEqual(r["answer"], "ECHO Say hi")
        self.assertEqual(r["model"], self.tool("status")["models"][0]["name"])   # loaded on demand
        self.assertEqual(self.tool("status")["speed_tokens_per_second"], 41.5)

    def test_4_files_are_read_here_and_answers_can_be_structured(self):
        path = os.path.join(self.p.home, "notes.txt")
        with open(path, "w") as f:
            f.write("one two three four")
        r = self.tool("ask", prompt="Count the words", files=[path],
                      json_schema={"type": "object", "properties": {"words": {"type": "integer"}}}, wait=60)
        self.assertEqual(r["state"], "done", r)
        self.assertIn("words", json.loads(r["answer"]))
        seen = json.loads(_read(os.path.join(self.p.home, "Pumpkin", "models", "small", f"chat-{self.p.llama_port}.json")))
        self.assertIn("one two three four", seen["messages"][-1]["content"])
        self.assertEqual(seen["response_format"]["type"], "json_schema")
        self.assertIn("error", self.tool("ask", prompt="x", files=["/no/such/file.txt"]))

    def test_4b_a_model_that_thinks_anyway_gets_room_to_answer(self):
        r = self.tool("ask", prompt="REASONS please", system="ECHO", max_tokens=300, wait=60)
        self.assertEqual(r["answer"], "ECHO full answer", r)                   # asked again with more room
        seen = json.loads(_read(os.path.join(self.p.home, "Pumpkin", "models", "small", f"chat-{self.p.llama_port}.json")))
        self.assertEqual(seen["chat_template_kwargs"], {"enable_thinking": False, "reasoning_effort": "low"})
        self.assertEqual(seen["max_tokens"], 300 + 2048)

    def test_5_hand_over_keep_working_collect_later(self):
        r = self.tool("ask", prompt="SLOWJOB please", system="ECHO", wait=0)
        self.assertIn(r["state"], ("queued", "running"))
        self.assertIn("result", r["next"])
        b = self.tool("batch", instruction="ECHO each", items=["apple", "pear", "plum"])
        done = self.tool("result", job=r["job"], wait=60)
        self.assertEqual(done["answer"], "ECHO SLOWJOB please")
        out = self.tool("result", job=b["job"], wait=60)
        self.assertEqual(out["state"], "done", out)
        self.assertEqual([x["answer"] for x in out["results"]], ["ECHO apple", "ECHO pear", "ECHO plum"])
        self.assertEqual((out["done"], out["total"]), (3, 3))
        listed = self.tool("jobs")["jobs"]
        self.assertEqual(listed[0]["job"], b["job"])                           # newest first, without the answers
        self.assertNotIn("answer", listed[0])
        self.assertIn(b["job"], [j["job"] for j in self.p.api("claude")["jobs"]])

    def test_6_cancel_and_mistakes(self):
        slow = self.tool("ask", prompt="SLOWJOB one", system="ECHO", wait=0)
        queued = self.tool("ask", prompt="SLOWJOB two", system="ECHO", wait=0)
        self.assertEqual(self.tool("cancel", job=queued["job"])["state"], "cancelled")
        self.assertEqual(self.tool("result", job=slow["job"], wait=60)["state"], "done")
        self.assertIn("error", self.tool("result", job="nope"))
        self.assertIn("error", self.tool("ask", prompt=" "))
        self.assertIn("error", self.tool("batch", instruction="x", items=[]))
        self.assertIn("error", self.tool("load", model="no such model"))
        big = self.tool("ask", prompt="x " * 60000, system="ECHO", wait=30)           # more than the model holds
        self.assertEqual(big["state"], "error")
        self.assertIn("Split it", big["error"])

    def test_7_claude_desktop_and_claude_code_start_it_as_a_program(self):
        env = dict(self.p.env)
        proc = subprocess.Popen([sys.executable, "-m", "pumpkin", "mcp"], env=env, cwd=ROOT, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "ask", "arguments": {"prompt": "From the pipe", "system": "ECHO"}}}]
            for m in msgs:
                proc.stdin.write(json.dumps(m) + "\n")
            proc.stdin.flush()
            got = {}
            end = time.time() + 60
            while len(got) < 2 and time.time() < end:
                line = proc.stdout.readline()
                if line:
                    r = json.loads(line)
                    got[r["id"]] = r
            self.assertEqual(got[1]["result"]["serverInfo"]["name"], "pumpkin")
            self.assertIn("ECHO From the pipe", got[2]["result"]["content"][0]["text"])
        finally:
            proc.stdin.close()
            proc.terminate()
            proc.wait(10)
            proc.stdout.close()
            proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
