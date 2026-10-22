"""Your documents, searchable by the model: the text of every file in ~/Pumpkin/files (and any folders you add),
split into short passages that know their file and page, in a full-text index on this computer.

The model finds passages with files_search and quotes them with their place ("report.pdf, p. 4"), so a small model
answers from your documents instead of guessing. Reading happens only here; nothing is uploaded."""
import os
import re
import sqlite3
import sys
import threading
import time
import zipfile
from xml.etree import ElementTree as ET

from . import config

VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
MAX_FILE_MB = 80
CHUNK = 1100                    # characters per passage
OVERLAP = 150
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".xml", ".yaml", ".yml", ".ini", ".rst",
            ".tex", ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".go", ".rs", ".sql", ".kql", ".sh", ".ps1"}
DOC_EXT = {".pdf", ".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods", ".html", ".htm", ".eml", ".epub"}
SCAN_EXT = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}      # pictures of text: listed, read by a vision model
SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".venv", "venv"}
_lock = threading.Lock()


# ---------------------------------------------------------------- where documents live
def folders(settings):
    """The searchable folders: the work folder, then any you added in Settings."""
    out = []
    for d in [settings["files_dir"]] + list(settings["doc_folders"] or []):
        d = os.path.realpath(os.path.expanduser(str(d)))
        if d not in out and os.path.isdir(d):
            out.append(d)
    return out


def display_name(path, roots):
    """How a file is named to the model and the person: relative to its folder, with / on every system."""
    for i, r in enumerate(roots):
        if path == r or path.startswith(r + os.sep):
            rel = os.path.relpath(path, r).replace(os.sep, "/")
            return rel if i == 0 else f"{os.path.basename(r)}/{rel}"
    return os.path.basename(path)


# ---------------------------------------------------------------- reading text out of files
def _pdf(path):
    if VENDOR not in sys.path:
        sys.path.append(VENDOR)                  # a pypdf you installed yourself comes first
    import logging
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    from pypdf import PdfReader
    r = PdfReader(path)
    if r.is_encrypted:
        try:
            r.decrypt("")
        except Exception:  # noqa: BLE001
            raise ValueError("The PDF is password protected.") from None
    out = []
    for i, page in enumerate(r.pages, 1):
        try:
            t = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - one broken page should not lose the rest
            t = ""
        out.append((f"p. {i}", t))
    return out


def _xml_text(data, para_tags, text_tag):
    """Paragraphs from an office XML part, any namespace."""
    root = ET.fromstring(data)
    paras = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] in para_tags:
            bits = []
            for t in el.iter():
                tag = t.tag.rsplit("}", 1)[-1]
                if tag == text_tag and t.text:
                    bits.append(t.text)
                elif tag in ("tab",):
                    bits.append("\t")
                elif tag in ("br", "line-break"):
                    bits.append("\n")
            s = "".join(bits).strip()
            if s:
                paras.append(s)
    return paras


def _parts(paras, label="part"):
    """Long documents without pages are cut into parts of a few thousand characters, at paragraph ends."""
    out, cur, n = [], [], 1
    size = 0
    for p in paras:
        cur.append(p)
        size += len(p)
        if size > 4000:
            out.append((f"{label} {n}", "\n".join(cur)))
            cur, size, n = [], 0, n + 1
    if cur or not out:
        out.append((f"{label} {n}" if out else "", "\n".join(cur)))
    return out


def _docx(path):
    with zipfile.ZipFile(path) as z:
        return _parts(_xml_text(z.read("word/document.xml"), {"p"}, "t"))


def _pptx(path):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
        names.sort(key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        return [(f"slide {i}", "\n".join(_xml_text(z.read(n), {"p"}, "t"))) for i, n in enumerate(names, 1)]


def _xlsx(path):
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root:
                shared.append("".join(t.text or "" for t in si.iter() if t.tag.endswith("}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        sheets = [s.get("name") for s in wb.iter() if s.tag.endswith("}sheet")]
        rels = {}
        try:
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
                rels[r.get("Id")] = r.get("Target")
        except KeyError:
            pass
        ids = [s.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
               for s in wb.iter() if s.tag.endswith("}sheet")]
        out = []
        for i, (name, rid) in enumerate(zip(sheets, ids), 1):
            target = rels.get(rid) or f"worksheets/sheet{i}.xml"
            part = "xl/" + target.lstrip("/").replace("xl/", "", 1)
            try:
                root = ET.fromstring(z.read(part))
            except KeyError:
                continue
            rows = []
            for row in root.iter():
                if not row.tag.endswith("}row"):
                    continue
                cells = []
                for c in row:
                    if not c.tag.endswith("}c"):
                        continue
                    v = next((x.text for x in c if x.tag.endswith("}v")), None)
                    if c.get("t") == "s" and v is not None:
                        try:
                            v = shared[int(v)]
                        except (ValueError, IndexError):
                            pass
                    elif c.get("t") == "inlineStr":
                        v = "".join(t.text or "" for t in c.iter() if t.tag.endswith("}t"))
                    cells.append(v or "")
                if any(cells):
                    rows.append("\t".join(cells))
            out.append((f"sheet {name}", "\n".join(rows)))
        return out


def _odf(path):
    with zipfile.ZipFile(path) as z:
        return _parts(_odf_plain(z.read("content.xml")))


def _odf_plain(data):
    root = ET.fromstring(data)
    out = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] in ("p", "h"):
            s = "".join(el.itertext()).strip()
            if s:
                out.append(s)
    return out


def _html_text(raw):
    from .mcp_web import _Text
    p = _Text()
    p.feed(raw)
    return p.text()


def _eml(path):
    import email
    from email import policy
    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=policy.default)
    head = "\n".join(f"{k}: {msg[k]}" for k in ("From", "To", "Cc", "Date", "Subject") if msg[k])
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        text = body.get_content()
        if body.get_content_type() == "text/html":
            text = _html_text(text)
    names = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
    if names:
        text += "\n\nAttachments: " + ", ".join(names)
    return _parts([head, text])


def _epub(path):
    with zipfile.ZipFile(path) as z:
        names = sorted(n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm")))
        out = []
        for i, n in enumerate(names, 1):
            t = _html_text(z.read(n).decode("utf-8", "replace"))
            if t.strip():
                out.append((f"chapter {len(out) + 1}", t))
        return out


def _read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def extract(path):
    """The text of a file as (place, text) pairs: pages, slides, sheets, or parts of a long document."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _pdf(path)
    if ext == ".docx":
        return _docx(path)
    if ext == ".pptx":
        return _pptx(path)
    if ext == ".xlsx":
        return _xlsx(path)
    if ext in (".odt", ".odp", ".ods"):
        return _odf(path)
    if ext in (".html", ".htm"):
        return _parts(_html_text(_read_text(path)).split("\n"))
    if ext == ".eml":
        return _eml(path)
    if ext == ".epub":
        return _epub(path)
    if ext in TEXT_EXT:
        return _parts(_read_text(path).split("\n"), "part")
    raise ValueError("Pumpkin cannot read this kind of file yet.")


def chunks(text):
    """Passages of about CHUNK characters, cut at paragraph or sentence ends, overlapping a little."""
    text = re.sub(r"[ \t\r\f\v]+", " ", text or "")
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + CHUNK)
        if end < len(text):
            cut = max(text.rfind("\n", start + CHUNK // 2, end), text.rfind(". ", start + CHUNK // 2, end))
            if cut > start:
                end = cut + 1
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        start = max(end - OVERLAP, start + 1)
    return out


# ---------------------------------------------------------------- the index
def db_path():
    return os.path.join(config.DATA_DIR, "documents.sqlite")


_fts = None


def connect():
    """The index database. Full-text search (FTS5) when this Python has it, plain tables otherwise."""
    global _fts
    config.ensure_dirs()
    c = sqlite3.connect(db_path(), timeout=30, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, parts INTEGER, "
              "chars INTEGER, empty INTEGER, error TEXT, at REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS pages (path TEXT, n INTEGER, place TEXT, text TEXT, "
              "PRIMARY KEY (path, n))")
    if _fts is None:
        try:
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(text, path UNINDEXED, "
                      "place UNINDEXED, n UNINDEXED, tokenize='unicode61 remove_diacritics 2')")
            _fts = True
        except sqlite3.OperationalError:
            _fts = False
    if not _fts:
        c.execute("CREATE TABLE IF NOT EXISTS passages (text TEXT, path TEXT, place TEXT, n INTEGER)")
    return c


def _wanted(roots):
    """Every file in the folders that Pumpkin can read or list, with its size and time."""
    out = {}
    for r in roots:
        for d, dirs, files in os.walk(r):
            dirs[:] = [x for x in dirs if not x.startswith(".") and x not in SKIP_DIRS]
            for f in files:
                if f.startswith(".") or f.startswith("~$"):
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext not in TEXT_EXT and ext not in DOC_EXT and ext not in SCAN_EXT:
                    continue
                p = os.path.join(d, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_size <= MAX_FILE_MB * 2 ** 20:
                    out[p] = (st.st_mtime, st.st_size)
    return out


_state = {"running": False, "done": 0, "todo": 0, "current": "", "at": 0.0}


def refresh(settings, max_seconds=None, progress=None):
    """Brings the index in step with the folders: new and changed files are read, removed ones dropped."""
    if not _lock.acquire(blocking=False):
        return {"busy": True}
    t0 = time.time()
    added = 0
    try:
        roots = folders(settings)
        c = connect()
        have = {row[0]: (row[1], row[2]) for row in c.execute("SELECT path, mtime, size FROM files")}
        want = _wanted(roots)
        gone = [p for p in have if p not in want]
        for p in gone:
            _drop(c, p)
        todo = [p for p, ms in want.items() if have.get(p) != ms]
        todo.sort(key=lambda p: -want[p][0])                   # newest first: what you just added is ready soonest
        _state.update(running=True, todo=len(todo), done=0)
        for p in todo:
            if max_seconds and time.time() - t0 > max_seconds:
                break
            _state["current"] = os.path.basename(p)
            _index_file(c, p, want[p])
            added += 1
            _state["done"] = added
            if progress:
                progress(added, len(todo))
        c.commit()
        c.close()
        return {"indexed": added, "removed": len(gone), "left": len(todo) - added}
    finally:
        _state.update(running=False, current="", at=time.time())
        _lock.release()


def _drop(c, path):
    c.execute("DELETE FROM files WHERE path=?", (path,))
    c.execute("DELETE FROM pages WHERE path=?", (path,))
    c.execute("DELETE FROM passages WHERE path=?", (path,))


def _index_file(c, path, ms):
    _drop(c, path)
    ext = os.path.splitext(path)[1].lower()
    error, parts = None, []
    if ext in SCAN_EXT:
        parts = []
    else:
        try:
            parts = extract(path)
        except Exception as e:  # noqa: BLE001 - one odd file must not stop the rest
            error = str(e)[:300] or e.__class__.__name__
    chars = 0
    empty = 0
    for n, (place, text) in enumerate(parts, 1):
        text = (text or "").strip()
        if not text:
            empty += 1
            continue
        chars += len(text)
        c.execute("INSERT INTO pages VALUES (?,?,?,?)", (path, n, place, text))
        for piece in chunks(text):
            c.execute("INSERT INTO passages (text, path, place, n) VALUES (?,?,?,?)", (piece, path, place, n))
    if ext in SCAN_EXT:
        empty = 1
    c.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?)",
              (path, ms[0], ms[1], len(parts), chars, empty, error, time.time()))
    c.commit()


STOP = set("a an and are as at be but by for from has have how i in is it its me my of on or our so that the "
           "their them there these they this to was we what when where which who why will with you your do does "
           "did can could should would about into than then also not no yes any all some".split())


def _terms(query):
    words = re.findall(r"\w+", (query or "").lower())
    keep = [w for w in words if len(w) > 1 and w not in STOP] or [w for w in words if w]
    seen = []
    for w in keep:
        if w not in seen:
            seen.append(w)
    return seen[:24]


def search(settings, query, n=8, file=None):
    """The passages that best match the words of the question, each with its file and place."""
    terms = _terms(query)
    if not terms:
        return []
    roots = folders(settings)
    c = connect()
    try:
        where, args = "", []
        if file:
            path = resolve(settings, file, c)
            where, args = " AND path = ?", [path]
        if _fts:
            # every word may match, a word's prefix too ("invoic" finds "invoices"); bm25 ranks passages that hold
            # more of the rarer words first
            q = " OR ".join(f'"{t}"*' for t in terms)
            rows = c.execute(f"SELECT text, path, place, n, bm25(passages) FROM passages WHERE passages MATCH ?{where} "
                             f"ORDER BY bm25(passages) LIMIT ?", [q] + args + [n * 3]).fetchall()
        else:
            like = " OR ".join("lower(text) LIKE ?" for _ in terms)
            rows = c.execute(f"SELECT text, path, place, n, 0 FROM passages WHERE ({like}){where} LIMIT 2000",
                             [f"%{t}%" for t in terms] + args).fetchall()
            rows.sort(key=lambda r: -sum(r[0].lower().count(t) for t in terms))
    finally:
        c.close()
    out, per_file = [], {}
    for text, path, place, num, score in rows:
        if per_file.get(path, 0) >= 3:                     # spread the answer over several documents when it can
            continue
        per_file[path] = per_file.get(path, 0) + 1
        out.append({"file": display_name(path, roots), "place": place, "text": text, "page": num})
        if len(out) >= n:
            break
    return out


def resolve(settings, name, c=None):
    """A file name the model used, back to the indexed file. Only indexed files in your folders can be read."""
    roots = folders(settings)
    own = c is None
    c = c or connect()
    try:
        paths = [r[0] for r in c.execute("SELECT path FROM files")]
    finally:
        if own:
            c.close()
    name = (name or "").strip().replace("\\", "/").lstrip("/")
    for prefix in ("workspace/",):
        if name.startswith(prefix):
            name = name[len(prefix):]
    exact = [p for p in paths if display_name(p, roots) == name]
    if exact:
        return exact[0]
    low = name.lower()
    near = [p for p in paths if display_name(p, roots).lower().endswith(low) or os.path.basename(p).lower() == low]
    if len(near) == 1:
        return near[0]
    if len(near) > 1:
        raise LookupError("Several files match: " + ", ".join(display_name(p, roots) for p in near[:8]))
    raise FileNotFoundError(f"No document called {name}. Use files_list to see the names.")


def read(settings, name, place=None, start=0, limit=12000):
    """The text of one page (or slide, sheet, part) of a document, or the whole thing from the start."""
    c = connect()
    try:
        path = resolve(settings, name, c)
        rows = c.execute("SELECT n, place, text FROM pages WHERE path=? ORDER BY n", (path,)).fetchall()
        meta = c.execute("SELECT error, empty, parts FROM files WHERE path=?", (path,)).fetchone()
    finally:
        c.close()
    disp = display_name(path, folders(settings))
    if not rows:
        if meta and meta[0]:
            return f"{disp} could not be read: {meta[0]}"
        return f"{disp} has no text Pumpkin can read (it may be a scan or a picture)."
    if place:
        want = str(place).lower().replace("page", "p.").replace("p ", "p. ").strip()
        if want.isdigit():
            want = f"p. {want}" if rows[0][1].startswith("p.") else want
        pick = [r for r in rows if r[1].lower() == want or str(r[0]) == want]
        if not pick:
            places = ", ".join(r[1] for r in rows[:40] if r[1])
            return f"{disp} has no {place}. It has: {places}"
        rows = pick
    text = "\n\n".join(f"[{disp}, {pl}]\n{t}" if pl else f"[{disp}]\n{t}" for _, pl, t in rows)
    start = max(0, int(start or 0))
    chunk = text[start:start + limit]
    more = len(text) - start - len(chunk)
    tail = f"\n\n[{more} more characters. Call files_read with start={start + len(chunk)} to continue.]" if more > 0 else ""
    return chunk + tail


def listing(settings, limit=200):
    roots = folders(settings)
    c = connect()
    try:
        rows = c.execute("SELECT path, parts, chars, empty, error, mtime FROM files ORDER BY mtime DESC").fetchall()
    finally:
        c.close()
    out = []
    for path, parts, chars, empty, error, mtime in rows[:limit]:
        out.append({"file": display_name(path, roots), "parts": parts, "chars": chars, "unreadable": bool(error),
                    "scanned": bool(empty) and not chars, "error": error, "mtime": int(mtime or 0)})
    return out


def status(settings):
    c = connect()
    try:
        files, chars = c.execute("SELECT count(*), coalesce(sum(chars),0) FROM files").fetchone()
        readable = c.execute("SELECT count(*) FROM files WHERE chars > 0").fetchone()[0]
        scans = c.execute("SELECT count(*) FROM files WHERE chars = 0 AND error IS NULL").fetchone()[0]
        errors = c.execute("SELECT count(*) FROM files WHERE error IS NOT NULL").fetchone()[0]
    finally:
        c.close()
    return {"files": files, "readable": readable, "scans": scans, "errors": errors, "chars": chars,
            "folders": folders(settings), "indexing": _state["running"], "done": _state["done"],
            "todo": _state["todo"], "current": _state["current"], "at": _state["at"], "fts": bool(_fts)}


# ---------------------------------------------------------------- keeping it fresh in the background
class Indexer:
    """Keeps the index in step: shortly after Pumpkin starts, after uploads, and every few minutes."""

    def __init__(self, settings):
        self.settings = settings
        self._wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def poke(self):
        self._wake.set()

    def _loop(self):
        time.sleep(5)
        while True:
            try:
                if self.settings["doc_index"]:
                    refresh(self.settings)
            except Exception:  # noqa: BLE001 - try again next round
                pass
            self._wake.wait(300)
            self._wake.clear()


def format_hits(hits):
    if not hits:
        return ("Nothing in the user's documents matches. Try other words (names, numbers, key terms), or say that "
                "the documents do not cover it.")
    out = []
    for i, h in enumerate(hits, 1):
        where = f"{h['file']}, {h['place']}" if h["place"] else h["file"]
        out.append(f"[{i}] ({where})\n{h['text']}")
    return ("Passages from the user's documents. Cite the ones you use as (file, place), for example "
            f"({hits[0]['file']}{', ' + hits[0]['place'] if hits[0]['place'] else ''}).\n\n" + "\n\n".join(out))
