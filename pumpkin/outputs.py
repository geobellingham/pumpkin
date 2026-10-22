"""Files the model makes for you: a report, a letter, a table, saved as Markdown, text, CSV, a web page or a Word
document in ~/Pumpkin/files, where the Files page shows them. No Docker needed, and nothing outside that folder can
be written."""
import datetime as dt
import html
import os
import re
import zipfile

from . import workspace

FORMATS = ("md", "txt", "csv", "html", "docx")


def save(settings, name, content, fmt=None, folder="outputs"):
    """Writes the file and returns its path inside the work folder. Never overwrites: a second report.docx becomes
    report (2).docx."""
    content = str(content or "")
    if not content.strip():
        raise ValueError("There is nothing to save.")
    name = os.path.basename(str(name or "").replace("\\", "/")).strip() or f"Pumpkin {dt.datetime.now():%Y-%m-%d %H%M}"
    stem, ext = os.path.splitext(name)
    known = ext.lstrip(".").lower() in FORMATS + ("markdown",)
    fmt = (fmt or (ext.lstrip(".") if known else "") or "md").lower().lstrip(".")
    if fmt == "markdown":
        fmt = "md"
    if fmt not in FORMATS:
        raise ValueError(f"Pumpkin can save {', '.join(FORMATS)} files.")
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", stem if known else name)
    stem = re.sub(r"\s+", " ", stem).strip(" .") or "Pumpkin"
    if fmt == "docx":
        data = docx_bytes(content, title=stem)
    elif fmt == "html":
        data = html_page(content, stem).encode("utf-8")
    else:
        data = content.encode("utf-8")
    return workspace.save_upload(settings, folder, f"{stem[:120]}.{fmt}", data)


# ---------------------------------------------------------------- a little Markdown
def blocks(md):
    """Markdown as blocks: ("h", level, text), ("p", text), ("ul"/"ol", [items]), ("code", text), ("table", rows)."""
    lines = md.replace("\r\n", "\n").split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s.startswith("```"):
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            out.append(("code", "\n".join(body)))
            i += 1
            continue
        m = re.match(r"(#{1,6})\s+(.*)", s)
        if m:
            out.append(("h", len(m.group(1)), m.group(2).strip().rstrip("#").strip()))
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?[\s:|-]+\|?$", lines[i + 1].strip()) \
                and "-" in lines[i + 1]:
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not re.match(r"^\|?[\s:|-]+\|?$", lines[i].strip()):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append(("table", rows))
            continue
        if re.match(r"[-*+]\s+", s) or re.match(r"\d+[.)]\s+", s):
            kind = "ol" if re.match(r"\d+[.)]\s+", s) else "ul"
            items = []
            while i < len(lines) and (re.match(r"\s*([-*+]|\d+[.)])\s+", lines[i])):
                items.append(re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", lines[i]).strip())
                i += 1
            out.append((kind, items))
            continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            out.append(("hr",))
            i += 1
            continue
        para = [s]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"\s*(#{1,6}\s|```|[-*+]\s|\d+[.)]\s|\|)", lines[i]):
            para.append(lines[i].strip())
            i += 1
        out.append(("p", " ".join(para)))
    return out


def runs(text):
    """Inline **bold**, *italic*, `code` and [links](url) as (text, bold, italic, code) runs."""
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 (\2)", text)
    out = []
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`|(?<![\w*])\*[^*\s][^*]*\*(?![\w*])|(?<!\w)_[^_\s][^_]*_(?!\w))", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.append((part[2:-2], True, False, False))
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append((part[1:-1], False, False, True))
        elif len(part) > 2 and part[0] == part[-1] and part[0] in "*_":
            out.append((part[1:-1], False, True, False))
        else:
            out.append((part, False, False, False))
    return out


# ---------------------------------------------------------------- as a web page
def _inline_html(text):
    out = []
    for t, b, it, code in runs(text):
        e = html.escape(t)
        if code:
            e = f"<code>{e}</code>"
        if b:
            e = f"<strong>{e}</strong>"
        if it:
            e = f"<em>{e}</em>"
        out.append(e)
    return re.sub(r"(https?://[^\s<)]+)", r'<a href="\1">\1</a>', "".join(out))


def html_page(md, title):
    body = []
    for b in blocks(md):
        if b[0] == "h":
            body.append(f"<h{b[1]}>{_inline_html(b[2])}</h{b[1]}>")
        elif b[0] == "p":
            body.append(f"<p>{_inline_html(b[1])}</p>")
        elif b[0] in ("ul", "ol"):
            body.append(f"<{b[0]}>" + "".join(f"<li>{_inline_html(x)}</li>" for x in b[1]) + f"</{b[0]}>")
        elif b[0] == "code":
            body.append(f"<pre><code>{html.escape(b[1])}</code></pre>")
        elif b[0] == "hr":
            body.append("<hr>")
        elif b[0] == "table":
            rows = b[1]
            head = "".join(f"<th>{_inline_html(c)}</th>" for c in rows[0]) if rows else ""
            rest = "".join("<tr>" + "".join(f"<td>{_inline_html(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
            body.append(f"<table><thead><tr>{head}</tr></thead><tbody>{rest}</tbody></table>")
    style = ("body{font:16px/1.6 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem;color:#1f1f1f}"
             "table{border-collapse:collapse;margin:1rem 0}th,td{border:1px solid #ccc;padding:.35rem .6rem;"
             "text-align:left}pre{background:#f4f4f4;padding:.8rem;overflow:auto}code{font-size:.92em}"
             "@media(prefers-color-scheme:dark){body{background:#161616;color:#e8e8e8}pre{background:#222}"
             "th,td{border-color:#444}a{color:#8ab4f8}}")
    return (f"<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,"
            f"initial-scale=1\"><title>{html.escape(title)}</title><style>{style}</style></head><body>"
            + "\n".join(body) + "</body></html>")


# ---------------------------------------------------------------- as a Word document
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _x(s):
    return html.escape(s, quote=False)


def _wruns(text, size=None):
    out = []
    for t, b, it, code in runs(text):
        props = ""
        if b:
            props += "<w:b/>"
        if it:
            props += "<w:i/>"
        if code:
            props += '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>'
        if size:
            props += f'<w:sz w:val="{size}"/>'
        rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
        out.append(f'<w:r>{rpr}<w:t xml:space="preserve">{_x(t)}</w:t></w:r>')
    return "".join(out)


def _para(inner, style=None, indent=None):
    ppr = ""
    if style or indent:
        ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + \
              (f'<w:ind w:left="{indent}" w:hanging="300"/>' if indent else "") + "</w:pPr>"
    return f"<w:p>{ppr}{inner}</w:p>"


def docx_bytes(md, title="Document"):
    import io
    body = []
    for b in blocks(md):
        if b[0] == "h":
            body.append(_para(_wruns(b[2]), f"Heading{min(b[1], 3)}"))
        elif b[0] == "p":
            body.append(_para(_wruns(b[1])))
        elif b[0] in ("ul", "ol"):
            for n, item in enumerate(b[1], 1):
                mark = f"{n}.\t" if b[0] == "ol" else "•\t"
                body.append(_para(f'<w:r><w:t xml:space="preserve">{mark}</w:t></w:r>' + _wruns(item), indent=600))
        elif b[0] == "code":
            for line in b[1].split("\n") or [""]:
                body.append(_para('<w:r><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/><w:sz w:val="19"/>'
                                  f'</w:rPr><w:t xml:space="preserve">{_x(line)}</w:t></w:r>', "Code"))
        elif b[0] == "hr":
            body.append(_para(""))
        elif b[0] == "table" and b[1]:
            cols = max(len(r) for r in b[1])
            rows = []
            for ri, r in enumerate(b[1]):
                cells = []
                for c in r + [""] * (cols - len(r)):
                    inner = _wruns(f"**{c}**" if ri == 0 and c else c)
                    cells.append(f'<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>{_para(inner)}</w:tc>')
                rows.append("<w:tr>" + "".join(cells) + "</w:tr>")
            borders = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
                              for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
            body.append(f'<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>{borders}</w:tblBorders>'
                        f'</w:tblPr>' + "".join(rows) + "</w:tbl>")
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W}><w:body>' + "".join(body) +
           '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1300" w:right="1300" w:bottom="1300" '
           'w:left="1300" w:header="700" w:footer="700" w:gutter="0"/></w:sectPr></w:body></w:document>')

    def style(sid, name, size, bold=False, space=120, font=None):
        rpr = (f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}"/>' if font else "") + ("<w:b/>" if bold else "") + \
              f'<w:sz w:val="{size}"/>'
        return (f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/><w:basedOn w:val="Normal"/>'
                f'<w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="{space * 2}" w:after="{space}"/></w:pPr>'
                f'<w:rPr>{rpr}</w:rPr></w:style>')
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {W}>'
              '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
              '<w:sz w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="140" w:line="276" '
              'w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
              + style("Heading1", "heading 1", 34, True, 160) + style("Heading2", "heading 2", 28, True, 140)
              + style("Heading3", "heading 3", 24, True, 120) + style("Code", "Code", 19, False, 0, "Consolas")
              + "</w:styles>")
    ctypes = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/'
              'package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-'
              'package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override '
              'PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
              'wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/'
              'vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/docProps/core.xml" '
              'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.'
            'org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
            'Target="docProps/core.xml"/></Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.'
                'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
                'openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.'
            'openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{_x(title)}</dc:title><dc:creator>Pumpkin</dc:creator>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created></cp:coreProperties>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ctypes)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("docProps/core.xml", core)
    return buf.getvalue()
