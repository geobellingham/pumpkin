"""Plain text out of Word, Excel and PowerPoint files, so a workspace of documents and spreadsheets can be read.

These files are zip archives of XML. The text is pulled out with patterns, not an XML parser, so a hostile file
cannot make Python expand entities, and nothing outside the standard library is needed. Pumpkin only reads them;
writing Office files is not supported."""
import html
import re
import zipfile

EXTENSIONS = (".docx", ".xlsx", ".pptx")
MEMBER_LIMIT = 30 * 2 ** 20           # no member is read past this many bytes: a zip bomb cannot fill memory
ROW_LIMIT = 2000
CELL_LIMIT = 300
SHEET_LIMIT = 50
SLIDE_LIMIT = 400
OUT_LIMIT = 4_000_000                 # characters of text out of one file: far more than a model can read anyway


def supported(path):
    return str(path).lower().endswith(EXTENSIONS)


def _member(z, name):
    try:
        with z.open(name) as f:
            return f.read(MEMBER_LIMIT).decode("utf-8", "replace")
    except KeyError:
        return ""


def _elements(xml, tag):
    """(attributes, inside) of each <tag ...>...</tag> or <tag .../>, in order. It only ever moves forward, so a
    file with tags that never close cannot make it start over and over (regular expressions with .*? would)."""
    opening = re.compile(rf"<{tag}\b([^>]*?)(/?)>")
    close = f"</{tag}>"
    i = 0
    while True:
        m = opening.search(xml, i)
        if not m:
            return
        if m.group(2):
            yield m.group(1), ""
            i = m.end()
            continue
        j = xml.find(close, m.end())
        if j < 0:                       # never closed: the rest is its inside, and that is the end
            yield m.group(1), xml[m.end():]
            return
        yield m.group(1), xml[m.end():j]
        i = j + len(close)


def _first(xml, tag):
    for _attrs, inner in _elements(xml, tag):
        return inner
    return None


def _text(xml):
    """The text of an XML fragment: tags dropped, entities decoded."""
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def _runs(xml, tag):
    """The text inside every <tag ...>...</tag>, joined."""
    return "".join(html.unescape(m) for m in re.findall(rf"<{tag}(?:\s[^>]*)?>([^<]*)</{tag}>", xml))


def docx(z):
    xml = _member(z, "word/document.xml")
    if not xml:
        raise ValueError("this Word file has no text in it")
    xml = re.sub(r"</w:p>(\s*)</w:tc>", r"<w:t>\t</w:t>\1", xml)       # the end of a table cell is a tab
    xml = re.sub(r"</w:tr>", "</w:p>", xml)                              # and the end of a row is a new line
    xml = re.sub(r"<w:tab\s*/>", "<w:t>\t</w:t>", xml)
    xml = re.sub(r"<w:br\s*/>", "<w:t>\n</w:t>", xml)
    out = []
    for para in re.split(r"</w:p>", xml):
        style = re.search(r'<w:pStyle w:val="([^"]+)"', para)
        t = _runs(para, "w:t")
        if not t.strip():
            out.append("")
            continue
        m = re.match(r"(?i)heading\s*(\d)", style.group(1)) if style else None
        out.append(("#" * int(m.group(1)) + " " if m else "") + t.rstrip("\t"))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()[:OUT_LIMIT]


def _col(ref):
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def xlsx(z):
    shared = []
    for _attrs, si in _elements(_member(z, "xl/sharedStrings.xml"), "si"):
        shared.append(_runs(si, "t"))
    book = _member(z, "xl/workbook.xml")
    rels = dict(re.findall(r'<Relationship[^>]*?Id="([^"]+)"[^>]*?Target="([^"]+)"', _member(z, "xl/_rels/workbook.xml.rels")))
    rels.update({k: v for v, k in re.findall(r'<Relationship[^>]*?Target="([^"]+)"[^>]*?Id="([^"]+)"',
                                              _member(z, "xl/_rels/workbook.xml.rels"))})
    sheets = []
    for m in re.finditer(r"<sheet\s[^>]*>", book):
        tag = m.group(0)
        name = re.search(r'name="([^"]*)"', tag)
        rid = re.search(r'r:id="([^"]*)"', tag)
        target = rels.get(rid.group(1)) if rid else None
        if target:
            target = target.lstrip("/")
            sheets.append((html.unescape(name.group(1)) if name else "Sheet", target if target.startswith("xl/") else "xl/" + target))
    if not sheets:
        sheets = [(n, n) for n in sorted(z.namelist()) if re.match(r"xl/worksheets/sheet\d+\.xml$", n)]
    if not sheets:
        raise ValueError("this Excel file has no sheets")
    out, size = [], 0
    for name, member in sheets[:SHEET_LIMIT]:
        xml = _member(z, member)
        out.append(f"## Sheet: {name}")
        n = 0
        for _rattrs, row in _elements(xml, "row"):
            if n >= ROW_LIMIT:
                out.append("... more rows")
                break
            n += 1
            cells = {}
            for attrs, inner in _elements(row, "c"):
                ref = re.search(r'r="([A-Z]{1,3})\d+"', attrs)
                if not ref:
                    continue
                kind = re.search(r'\bt="([^"]+)"', attrs)
                kind = kind.group(1) if kind else "n"
                v = _first(inner, "v")
                if kind == "s" and v is not None:
                    i = int(v) if v.isdigit() else -1
                    val = shared[i] if 0 <= i < len(shared) else ""
                elif kind == "inlineStr":
                    val = _runs(inner, "t")
                elif kind == "b" and v is not None:
                    val = "TRUE" if v == "1" else "FALSE"
                else:
                    val = html.unescape(v) if v is not None else ""
                f = _first(inner, "f")
                if f and val == "":
                    val = "=" + html.unescape(f)
                col = _col(ref.group(1))
                if col <= 300:                  # a cell far to the right would make a line of empty columns
                    cells[col] = val.replace("\t", " ").replace("\n", " ")[:CELL_LIMIT]
            if cells:
                line = "\t".join(cells.get(i, "") for i in range(1, max(cells) + 1)).rstrip("\t")
                out.append(line)
                size += len(line)
            if size > OUT_LIMIT:
                out.append("... the rest is cut")
                return "\n".join(out)
    if len(sheets) > SHEET_LIMIT:
        out.append(f"... {len(sheets) - SHEET_LIMIT} more sheets")
    return "\n".join(out)


def pptx(z):
    slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                    key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    if not slides:
        raise ValueError("this PowerPoint file has no slides")
    out = []
    for i, n in enumerate(slides[:SLIDE_LIMIT], 1):
        xml = _member(z, n)
        paras = [_runs(p, "a:t") for p in re.split(r"</a:p>", xml)]
        out.append(f"## Slide {i}\n" + "\n".join(p for p in paras if p.strip()))
    if len(slides) > SLIDE_LIMIT:
        out.append(f"... {len(slides) - SLIDE_LIMIT} more slides")
    return "\n\n".join(out)[:OUT_LIMIT]


def read(path):
    """The file's text. Raises ValueError with words for the model when it cannot be read."""
    low = str(path).lower()
    try:
        with zipfile.ZipFile(path) as z:
            if low.endswith(".docx"):
                return docx(z)
            if low.endswith(".xlsx"):
                return xlsx(z)
            if low.endswith(".pptx"):
                return pptx(z)
    except zipfile.BadZipFile as e:
        raise ValueError("it is not a valid Office file (or it is encrypted)") from e
    raise ValueError("this kind of file cannot be read")
