"""Workspaces (a folder with its own instructions, memory, connectors), reading Office files, and the MCP client
Pumpkin Code uses for a workspace's connectors."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from pumpkin import coder, config, connectors, mcpclient, office, workspaces  # noqa: E402

FAKE_MCP = r'''
import json, sys
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    def send(r): print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
    if m["method"] == "initialize":
        print("a note on stdout that is not json", flush=True)
        send({"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "fake"}})
    elif m["method"] == "tools/list":
        send({"tools": [
            {"name": "look", "description": "Read something.", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
             "annotations": {"readOnlyHint": True}},
            {"name": "post", "description": "Post something.", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]})
    elif m["method"] == "tools/call":
        p = m["params"]
        if p["arguments"].get("die"):
            sys.exit(3)
        send({"content": [{"type": "text", "text": p["name"] + ":" + json.dumps(p["arguments"], sort_keys=True)}],
              "isError": bool(p["arguments"].get("fail"))})
    else:
        send({})
'''


def office_files(folder):
    """A small Word, Excel and PowerPoint file, as the zip of XML they are."""
    docx = os.path.join(folder, "plan.docx")
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml",
                   '<w:document><w:body>'
                   '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Quarterly plan</w:t></w:r></w:p>'
                   '<w:p><w:r><w:t xml:space="preserve">Revenue &amp; costs </w:t></w:r><w:r><w:t>grow.</w:t></w:r></w:p>'
                   '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Q1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>10</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
                   '</w:body></w:document>')
    xlsx = os.path.join(folder, "sales.xlsx")
    with zipfile.ZipFile(xlsx, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook><sheets><sheet name="Sales" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", "<sst><si><t>Item</t></si><si><t>Apples</t></si><si><t>Count</t></si></sst>")
        z.writestr("xl/worksheets/sheet1.xml",
                   '<worksheet><sheetData>'
                   '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>2</v></c></row>'
                   '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>42</v></c></row>'
                   '<row r="3"><c r="A3" t="inlineStr"><is><t>Total</t></is></c><c r="C3"><f>SUM(B2:B2)</f><v>42</v></c></row>'
                   '</sheetData></worksheet>')
    pptx = os.path.join(folder, "talk.pptx")
    with zipfile.ZipFile(pptx, "w") as z:
        z.writestr("ppt/slides/slide2.xml", "<p:sld><a:p><a:r><a:t>Second</a:t></a:r></a:p></p:sld>")
        z.writestr("ppt/slides/slide1.xml", "<p:sld><a:p><a:r><a:t>Title</a:t></a:r></a:p>"
                   "<a:p><a:r><a:t>Point </a:t></a:r><a:r><a:t>one</a:t></a:r></a:p></p:sld>")
    return docx, xlsx, pptx


class _Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pumpkin-ws-")
        p = mock.patch.object(config, "DATA_DIR", os.path.join(self.tmp, "data"))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.folder = os.path.join(self.tmp, "reports")
        os.makedirs(self.folder)


class TestOffice(_Home):
    def test_reads_word_excel_and_powerpoint(self):
        d, x, p = office_files(self.folder)
        text = office.read(d)
        self.assertIn("# Quarterly plan", text)
        self.assertIn("Revenue & costs grow.", text)
        self.assertIn("Q1\t10", text)
        sheet = office.read(x)
        self.assertIn("## Sheet: Sales", sheet)
        self.assertIn("Item\tCount", sheet)
        self.assertIn("Apples\t42", sheet)
        self.assertIn("Total\t\t42", sheet)
        slides = office.read(p)
        self.assertLess(slides.index("Title"), slides.index("Second"))      # slide 1 before slide 2
        self.assertIn("Point one", slides)

    def test_the_read_tool_uses_it_and_a_broken_file_says_so(self):
        office_files(self.folder)
        with open(os.path.join(self.folder, "bad.docx"), "wb") as f:
            f.write(b"not a zip")
        t = coder.Tools(self.folder, web=False)
        self.assertIn("    1\t# Quarterly plan", t.read("plan.docx"))
        self.assertIn("cannot be read", t.read("bad.docx"))


class TestStore(_Home):
    def test_create_change_and_delete(self):
        w = workspaces.create({"root": self.folder, "name": "Reports", "kind": "writing", "instructions": "Be brief."})
        self.assertEqual(w["kind"], "writing")
        self.assertEqual([x["name"] for x in workspaces.all_items()], ["Reports"])
        workspaces.update(w["id"], {"mode": "edits", "connectors": ["github"], "skills": ["a"]})
        got = workspaces.get(w["id"])
        self.assertEqual((got["mode"], got["connectors"], got["skills"], got["instructions"]),
                         ("edits", ["github"], ["a"], "Be brief."))
        workspaces.remember(w["id"], "The user writes in British English.")
        workspaces.delete(w["id"])
        self.assertEqual(workspaces.all_items(), [])
        self.assertEqual(workspaces.memory(w["id"]), "")
        with self.assertRaises(FileNotFoundError):
            workspaces.get(w["id"])

    def test_bad_input_says_what_is_wrong(self):
        for data, words in (({"root": os.path.join(self.tmp, "nope")}, "not a folder"),
                            ({"root": os.path.expanduser("~")}, "not the whole disk or your home"),
                            ({"root": self.folder, "kind": "poetry"}, "kind of work"),
                            ({"root": self.folder, "mode": "yolo"}, "Approval"),
                            ({"root": self.folder, "name": "  "}, "name")):
            with self.assertRaises(ValueError) as e:
                workspaces.create(data)
            self.assertIn(words, str(e.exception))
        w = workspaces.create({"root": self.folder})
        self.assertEqual(w["name"], "reports")                                   # the folder's name by default
        with self.assertRaises(ValueError):
            workspaces.create({"root": self.folder, "name": "reports"})

    def test_memory_keeps_a_size_that_fits_and_does_not_repeat(self):
        w = workspaces.create({"root": self.folder})
        workspaces.remember(w["id"], "Uses tabs.")
        workspaces.remember(w["id"], "uses tabs.")
        self.assertEqual(workspaces.memory(w["id"]).strip(), "Uses tabs.")
        with self.assertRaises(ValueError):
            workspaces.remember(w["id"], "   ")
        for i in range(200):
            workspaces.remember(w["id"], f"fact {i} " + "x" * 300)
        mem = workspaces.memory(w["id"])
        self.assertLessEqual(len(mem), workspaces.MAX_MEMORY + 1)
        self.assertIn("fact 199", mem)
        self.assertNotIn("fact 0 ", mem)                                         # the oldest went first

    def test_the_prompt_follows_the_kind_and_carries_instructions_and_memory(self):
        w = workspaces.create({"root": self.folder, "kind": "data", "instructions": "Figures are in euro."})
        workspaces.remember(w["id"], "Columns are tab separated.")
        m = types.SimpleNamespace(ctx=lambda: 8192, tool_limit=lambda: 20000, skills_for=lambda r, ws=None: [])
        s = coder.Session(m, self.folder, workspace=w["id"])
        text = coder.Manager.system_prompt(None, s)
        self.assertIn("spreadsheets and data files", text)
        self.assertIn("Never overwrite the original data", text)
        self.assertIn("Figures are in euro.", text)
        self.assertIn("Columns are tab separated.", text)
        self.assertIn("remember tool", text)
        plain = coder.Manager.system_prompt(None, coder.Session(m, self.folder))
        self.assertIn("a coding agent", plain)
        self.assertNotIn("remember tool", plain)

    def test_the_remember_tool_writes_to_the_workspace(self):
        w = workspaces.create({"root": self.folder})
        m = types.SimpleNamespace(ctx=lambda: 8192, tool_limit=lambda: 20000, skills_for=lambda r, ws=None: [])
        s = coder.Session(m, self.folder, workspace=w["id"])
        self.assertIn("remember", [d["function"]["name"] for d in s.tools.definitions()])
        out, ok, _ = s._do("remember", {"note": "Reports go to the finance team."})
        self.assertTrue(ok)
        self.assertIn("finance team", workspaces.memory(w["id"]))
        workspaces.delete(w["id"])
        with self.assertRaises(LookupError):
            s._do("remember", {"note": "x"})


class TestMcpClient(_Home):
    def entry(self):
        path = os.path.join(self.tmp, "fake_mcp.py")
        with open(path, "w") as f:
            f.write(FAKE_MCP)
        return {"command": sys.executable, "args": [path], "env": {}}

    def test_lists_and_calls_tools(self):
        c = mcpclient.Client(self.entry(), "Fake")
        try:
            tools = c.start(timeout=20)
            self.assertEqual([t["name"] for t in tools], ["look", "post"])
            text, err = c.call("post", {"text": "hi"})
            self.assertEqual((text, err), ('post:{"text": "hi"}', False))
            text, err = c.call("look", {"fail": True})
            self.assertTrue(err)
        finally:
            c.close()

    def test_a_server_that_stops_gives_words_not_a_hang(self):
        c = mcpclient.Client(self.entry(), "Fake")
        try:
            c.start(timeout=20)
            with self.assertRaises(mcpclient.McpError) as e:
                c.call("post", {"die": True}, timeout=10)
            self.assertIn("stopped", str(e.exception))
            self.assertFalse(c.alive)
        finally:
            c.close()

    def test_stop_cancels_a_waiting_call(self):
        c = mcpclient.Client({"command": sys.executable, "args": ["-c", "import time; time.sleep(60)"], "env": {}}, "Slow")
        c.proc = None
        with self.assertRaises(mcpclient.McpError):
            c.start(timeout=1)                          # never answers initialize
        self.assertFalse(c.alive)


class TestSessionConnectors(_Home):
    def setUp(self):
        super().setUp()
        entry = TestMcpClient.entry(self)
        self.w = workspaces.create({"root": self.folder, "connectors": ["fake"]})
        m = types.SimpleNamespace(ctx=lambda: 8192, tool_limit=lambda: 20000, skills_for=lambda r, ws=None: [],
                                  settings_get=lambda k, d: d, notify=lambda s, d: None)
        self.s = coder.Session(m, self.folder, workspace=self.w["id"])
        for p in (mock.patch.object(connectors, "load", return_value={"fake": {"enabled": True}}),
                  mock.patch.object(connectors, "available", return_value=True),
                  mock.patch.object(connectors, "entry", return_value=entry),
                  mock.patch.object(connectors, "spec", return_value={"id": "fake", "name": "Fake"})):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.s.close_connectors)

    def test_tools_are_offered_and_asked_about_unless_they_only_read(self):
        self.s._start_connectors()
        names = [d["function"]["name"] for d in self.s.tools.definitions()]
        self.assertIn("fake__look", names)
        self.assertIn("fake__post", names)
        # a tool that only reads runs without asking
        out, ok, _ = self.s._do("fake__look", {"q": "x"})
        self.assertTrue(ok)
        self.assertEqual(out, 'look:{"q": "x"}')
        # one that acts waits for the user's yes
        result = []
        t = threading.Thread(target=lambda: result.append(self.s._do("fake__post", {"text": "hello"})))
        t.start()
        end = time.time() + 10
        while not self.s.pending and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(self.s.pending["kind"], "tool")
        self.assertEqual(self.s.pending["tool"], "post")
        self.s.answer(self.s.pending["id"], "always")
        t.join(10)
        self.assertEqual(result[0][0], 'post:{"text": "hello"}')
        self.assertEqual(self.s.allowed_tools, ["fake__post"])
        # "always" is remembered for the session
        out, ok, _ = self.s._do("fake__post", {"text": "again"})
        self.assertTrue(ok)
        self.assertIsNone(self.s.pending)

    def test_saying_no_and_never_ask(self):
        self.s._start_connectors()
        t = threading.Thread(target=lambda: self.s._do("fake__post", {"text": "no"}))
        t.start()
        end = time.time() + 10
        while not self.s.pending and time.time() < end:
            time.sleep(0.05)
        self.s.answer(self.s.pending["id"], "no", "not now")
        t.join(10)
        self.assertEqual([e for e in self.s.events if e["kind"] == "answer"][0]["allow"], "no")
        self.s.mode = "auto"
        out, ok, _ = self.s._do("fake__post", {"text": "go"})
        self.assertTrue(ok)

    def test_a_connector_that_cannot_start_does_not_stop_the_session(self):
        with mock.patch.object(connectors, "available", return_value=False):
            self.s._start_connectors()
        self.assertEqual(self.s.tools.extra, [])
        self.assertTrue(any("could not start" in str(e.get("text")) for e in self.s.events))

    def test_a_connector_switched_off_is_left_alone(self):
        with mock.patch.object(connectors, "load", return_value={"fake": {"enabled": False}}):
            self.s._start_connectors()
        self.assertEqual(self.s.mcp, {})

    def test_skills_can_be_limited_by_the_workspace(self):
        found = [{"name": "Alpha", "x": 1}, {"name": "Beta Gamma", "x": 2}]
        core = types.SimpleNamespace(settings={"skills": True})
        mgr = types.SimpleNamespace(core=core)
        with mock.patch.object(coder.skills, "enabled", return_value=found):
            all_ = coder.Manager.skills_for(mgr, self.folder, {"skills": None})
            some = coder.Manager.skills_for(mgr, self.folder, {"skills": ["beta-gamma"]})
        self.assertEqual(len(all_), 2)
        self.assertEqual([x["name"] for x in some], ["Beta Gamma"])


if __name__ == "__main__":
    unittest.main()
