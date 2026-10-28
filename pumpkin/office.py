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


def supported(path):
    return str(path).lower().endswith(EXTENSIONS)


def _member(z, name):
    try:
        with z.open(name) as f:
            return f.read(MEMBER_LIMIT).decode("utf-8", "replace")
    except KeyError:
        return ""


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
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _col(ref):
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def xlsx(z):
    shared = []
    for si in re.findall(r"<si>(.*?)</si>", _member(z, "xl/sharedStrings.xml"), re.S):
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
    out = []
    for name, member in sheets:
        xml = _member(z, member)
        out.append(f"## Sheet: {name}")
        rows = re.findall(r"<row\b[^>]*>(.*?)</row>", xml, re.S)
        for n, row in enumerate(rows):
            if n >= ROW_LIMIT:
                out.append(f"... {len(rows) - ROW_LIMIT} more rows")
                break
            cells = {}
            for c in re.finditer(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", row, re.S):
                attrs, inner = c.group(1), c.group(2) or ""
                ref = re.search(r'r="([A-Z]+\d+)"', attrs)
                if not ref:
                    continue
                kind = re.search(r'\bt="([^"]+)"', attrs)
                kind = kind.group(1) if kind else "n"
                v = re.search(r"<v>(.*?)</v>", inner, re.S)
                if kind == "s" and v:
                    i = int(v.group(1)) if v.group(1).isdigit() else -1
                    val = shared[i] if 0 <= i < len(shared) else ""
                elif kind == "inlineStr":
                    val = _runs(inner, "t")
                elif kind == "b" and v:
                    val = "TRUE" if v.group(1) == "1" else "FALSE"
                else:
                    val = html.unescape(v.group(1)) if v else ""
                f = re.search(r"<f[^>]*>(.*?)</f>", inner, re.S)
                if f and val == "":
                    val = "=" + html.unescape(f.group(1))
                cells[_col(ref.group(1))] = val.replace("\t", " ").replace("\n", " ")[:CELL_LIMIT]
            if cells:
                out.append("\t".join(cells.get(i, "") for i in range(1, max(cells) + 1)).rstrip("\t"))
    return "\n".join(out)


def pptx(z):
    slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                    key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    if not slides:
        raise ValueError("this PowerPoint file has no slides")
    out = []
    for i, n in enumerate(slides, 1):
        xml = _member(z, n)
        paras = [_runs(p, "a:t") for p in re.split(r"</a:p>", xml)]
        out.append(f"## Slide {i}\n" + "\n".join(p for p in paras if p.strip()))
    return "\n\n".join(out)


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
