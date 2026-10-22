"""Your documents, files the model saves, and memory: reading, searching with citations, writing, remembering."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pumpkin import config, docs, memory, mcp_files, mcp_memory, outputs  # noqa: E402


def make_pdf(path, pages):
    """A small real PDF with one line of text per page (Helvetica), built by hand."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        content = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R "
                    f"/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    with open(path, "wb") as f:
        f.write(out)


def make_pptx(path, slides):
    with zipfile.ZipFile(path, "w") as z:
        for i, text in enumerate(slides, 1):
            z.writestr(f"ppt/slides/slide{i}.xml",
                       '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                       'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp>'
                       f'<p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')


def make_xlsx(path):
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} {r}><sheets><sheet name="Budget" sheetId="1" r:id="rId1"/>'
                                      '</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}><si><t>Rent</t></si><si><t>Groceries</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml",
                   f'<worksheet {ns}><sheetData><row><c t="s"><v>0</v></c><c><v>650</v></c></row>'
                   '<row><c t="s"><v>1</v></c><c><v>310</v></c></row></sheetData></worksheet>')


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="pumpkin-know-")
        self._dirs = config.CONFIG_DIR, config.DATA_DIR, config.STATE_DIR
        config.CONFIG_DIR = os.path.join(self.home, "config")
        config.DATA_DIR = os.path.join(self.home, "data")
        config.STATE_DIR = os.path.join(self.home, "state")
        self.files = os.path.join(self.home, "files")
        os.makedirs(self.files)
        self.s = {"files_dir": self.files, "doc_folders": [], "doc_index": True, "memory": True}
        docs._fts = None

    def tearDown(self):
        config.CONFIG_DIR, config.DATA_DIR, config.STATE_DIR = self._dirs


class TestDocuments(Base):
    def fill(self):
        make_pdf(os.path.join(self.files, "lease.pdf"),
                 ["Lease agreement between Anna and Boris.", "The monthly rent is 650 euro, due on the 5th.",
                  "Notice period: two months in writing."])
        make_pptx(os.path.join(self.files, "talk.pptx"), ["Welcome", "Solar panels pay back in seven years"])
        make_xlsx(os.path.join(self.files, "budget.xlsx"))
        os.makedirs(os.path.join(self.files, "notes"))
        with open(os.path.join(self.files, "notes", "trip.md"), "w", encoding="utf-8") as f:
            f.write("# Trip\n\nThe ferry to Thassos leaves Keramoti every hour in summer.\n")
        with open(os.path.join(self.files, "letter.docx"), "wb") as f:
            f.write(outputs.docx_bytes("# Complaint\n\nMy heating has been broken since **January**.\n\n- item one\n"))
        with open(os.path.join(self.files, "photo.jpg"), "wb") as f:
            f.write(b"\xff\xd8\xff")
        with open(os.path.join(self.files, "broken.pdf"), "wb") as f:
            f.write(b"%PDF-1.4 not really")

    def test_reads_every_format_and_cites_the_page(self):
        self.fill()
        r = docs.refresh(self.s)
        self.assertEqual(r["indexed"], 7)
        hits = docs.search(self.s, "how much is the rent?")
        self.assertEqual({(h["file"], h["place"]) for h in hits}, {("lease.pdf", "p. 2"), ("budget.xlsx", "sheet Budget")})
        self.assertIn("650 euro", next(h["text"] for h in hits if h["file"] == "lease.pdf"))
        self.assertEqual(docs.search(self.s, "solar payback")[0]["place"], "slide 2")
        self.assertEqual(docs.search(self.s, "groceries")[0]["place"], "sheet Budget")
        self.assertEqual(docs.search(self.s, "ferry Thassos")[0]["file"], "notes/trip.md")
        self.assertEqual(docs.search(self.s, "heating broken")[0]["file"], "letter.docx")
        # prefixes match too: "notic" finds "Notice"
        self.assertEqual(docs.search(self.s, "notic period")[0]["place"], "p. 3")
        st = docs.status(self.s)
        self.assertEqual((st["files"], st["readable"], st["scans"], st["errors"]), (7, 5, 1, 1))

    def test_read_a_page_and_names_are_checked(self):
        self.fill()
        docs.refresh(self.s)
        self.assertIn("Notice period", docs.read(self.s, "lease.pdf", "p. 3"))
        self.assertIn("Notice period", docs.read(self.s, "lease.pdf", "3"))
        self.assertIn("[lease.pdf, p. 1]", docs.read(self.s, "lease.pdf"))
        self.assertIn("ferry", docs.read(self.s, "trip.md"))                     # the short name is enough
        with self.assertRaises(FileNotFoundError):
            docs.read(self.s, "../../config/token")
        self.assertIn("could not be read", docs.read(self.s, "broken.pdf"))

    def test_changes_are_picked_up(self):
        self.fill()
        docs.refresh(self.s)
        os.remove(os.path.join(self.files, "talk.pptx"))
        with open(os.path.join(self.files, "notes", "trip.md"), "w", encoding="utf-8") as f:
            f.write("The ferry now leaves from Kavala.\n")
        r = docs.refresh(self.s)
        self.assertEqual((r["indexed"], r["removed"]), (1, 1))
        self.assertEqual(docs.search(self.s, "solar"), [])
        self.assertIn("Kavala", docs.search(self.s, "ferry")[0]["text"])

    def test_extra_folders(self):
        extra = os.path.join(self.home, "Paperwork")
        os.makedirs(extra)
        with open(os.path.join(extra, "insurance.txt"), "w") as f:
            f.write("Policy number 4471, renewal in March.")
        self.s["doc_folders"] = [extra, os.path.join(self.home, "missing")]
        docs.refresh(self.s)
        self.assertEqual(docs.search(self.s, "policy renewal")[0]["file"], "Paperwork/insurance.txt")

    def test_long_text_is_cut_into_passages(self):
        text = " ".join(f"Sentence number {i} talks about topic {i % 7}." for i in range(400))
        parts = docs.chunks(text)
        self.assertGreater(len(parts), 5)
        self.assertTrue(all(len(p) <= docs.CHUNK + 5 for p in parts))
        self.assertIn("Sentence number 399", parts[-1])

    def test_mcp_tools(self):
        self.fill()
        out = mcp_files.call("search", {"query": "rent amount"}, self.s)
        self.assertIn("(lease.pdf, p. 2)", out)
        self.assertIn("Cite", out)
        self.assertIn("lease.pdf", mcp_files.call("list", {}, self.s))
        self.assertIn("Nothing in the user's documents", mcp_files.call("search", {"query": "volcano"}, self.s))


class TestOutputs(Base):
    def test_word_document_is_readable(self):
        md = ("# Lease summary\n\nRent is **650 euro**, see *page 2*.\n\n## Dates\n\n1. Start: May\n2. End: April\n\n"
              "| Item | Euro |\n|---|---|\n| Rent | 650 |\n| Deposit | 1300 |\n\n```\ncode line\n```\n")
        rel = outputs.save(self.s, "Lease summary", md, "docx")
        self.assertEqual(rel, "outputs/Lease summary.docx")
        path = os.path.join(self.files, "outputs", "Lease summary.docx")
        text = "\n".join(t for _, t in docs.extract(path))
        for want in ("Lease summary", "Rent is 650 euro, see page 2.", "Start: May", "Deposit", "1300", "code line"):
            self.assertIn(want, text)
        with zipfile.ZipFile(path) as z:
            self.assertIn("Heading1", z.read("word/document.xml").decode())
        # never overwrites
        self.assertEqual(outputs.save(self.s, "Lease summary", "again", "docx"), "outputs/Lease summary (2).docx")

    def test_other_formats_and_names(self):
        self.assertEqual(outputs.save(self.s, "plan.md", "# Plan"), "outputs/plan.md")
        self.assertEqual(outputs.save(self.s, "Report v1.2", "x", "txt"), "outputs/Report v1.2.txt")
        page = os.path.join(self.files, outputs.save(self.s, "Page", "# Hi <there>\n\n- a [link](https://x.org)", "html"))
        with open(page, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("&lt;there&gt;", body)
        self.assertIn('<a href="https://x.org">', body)
        self.assertEqual(outputs.save(self.s, "../../evil", "x", "md"), "outputs/evil.md")
        with self.assertRaises(ValueError):
            outputs.save(self.s, "x", "data", "exe")
        with self.assertRaises(ValueError):
            outputs.save(self.s, "x", "  ", "md")
        self.assertIn("outputs/Notes.docx", mcp_files.call("save", {"name": "Notes", "content": "hello"}, self.s))


class TestMemory(Base):
    def test_save_dedupe_forget(self):
        memory.add("Prefers short answers")
        memory.add("prefers short answers.")                        # the same fact once
        memory.add("Has a cat called Luna")
        self.assertEqual(len(memory.load()), 2)
        block = memory.prompt_block()
        self.assertIn("- Has a cat called Luna", block)
        gone = memory.forget("the cat Luna")
        self.assertEqual([g["text"] for g in gone], ["Has a cat called Luna"])
        self.assertEqual(memory.forget("something never said"), [])
        with self.assertRaises(ValueError):
            memory.add("x" * 500)
        if os.name != "nt":
            self.assertEqual(oct(os.stat(memory._path()).st_mode & 0o777), "0o600")

    def test_prompt_keeps_the_newest_when_full(self):
        for i in range(60):
            memory.add(f"Fact number {i} " + "word " * 15)
        block = memory.prompt_block(limit=800)
        self.assertIn("Fact number 59", block)
        self.assertNotIn("Fact number 0 ", block)
        self.assertLess(len(block), 1100)

    def test_mcp_tools(self):
        self.assertIn("Saved to memory", mcp_memory.call("save", {"fact": "Lives in Plovdiv"}))
        self.assertIn("Forgot: Lives in Plovdiv", mcp_memory.call("forget", {"what": "lives in plovdiv"}))


class TestServersOverStdio(Base):
    """The tool servers as llama-server runs them: a separate Python, JSON-RPC over pipes."""

    def rpc(self, module, calls):
        env = dict(os.environ, PYTHONPATH=ROOT, HOME=self.home, USERPROFILE=self.home,
                   XDG_CONFIG_HOME=os.path.join(self.home, ".config"), XDG_DATA_HOME=os.path.join(self.home, ".data"),
                   XDG_STATE_HOME=os.path.join(self.home, ".state"))
        lines = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
                 {"jsonrpc": "2.0", "method": "notifications/initialized"},
                 {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]
        for i, (name, args) in enumerate(calls, 2):
            lines.append({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}})
        out = subprocess.run([sys.executable, "-m", module], input="\n".join(json.dumps(x) for x in lines) + "\n",
                             capture_output=True, text=True, env=env, timeout=60)
        replies = [json.loads(x) for x in out.stdout.splitlines() if x.strip()]
        self.assertEqual(len(replies), 2 + len(calls), out.stderr)
        return replies

    def test_files_server(self):
        files = os.path.join(self.home, "Pumpkin", "files")
        os.makedirs(files)
        with open(os.path.join(files, "manual.txt"), "w") as f:
            f.write("To reset the router, hold the button for ten seconds.")
        r = self.rpc("pumpkin.mcp_files", [("search", {"query": "reset router"}), ("save", {"name": "A", "content": "b"})])
        self.assertEqual([t["name"] for t in r[1]["result"]["tools"]], ["search", "read", "list", "save"])
        self.assertIn("(manual.txt", r[2]["result"]["content"][0]["text"])
        self.assertTrue(os.path.exists(os.path.join(files, "outputs", "A.docx")))

    def test_memory_server(self):
        r = self.rpc("pumpkin.mcp_memory", [("save", {"fact": "Likes tea"}), ("nope", {})])
        self.assertFalse(r[2]["result"]["isError"])
        self.assertTrue(r[3]["result"]["isError"])
        cfg = os.path.join(self.home, ".config", "pumpkin", "memory.json")
        with open(cfg) as f:
            self.assertEqual(json.load(f)["items"][0]["text"], "Likes tea")


if __name__ == "__main__":
    unittest.main()
