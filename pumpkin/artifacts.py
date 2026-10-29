"""Artifacts: things the model builds for you in a side panel (a page, a document, a diagram, a piece of code) and
that you keep editing together. Each one keeps its earlier versions, so a change by the model or by you can be undone.

The model makes and changes them with the artifact tools (mcp_artifacts); you edit them on the Artifacts page or in
the panel next to the chat. They live in Pumpkin's settings folder on this computer, and nothing here is sent anywhere.

A page the model wrote is untrusted code: it is only ever shown inside a sandboxed frame with no network (see
SANDBOX_CSP), so it can draw and run scripts but cannot reach Pumpkin, your files or the internet."""
import json
import os
import re
import threading
import time
import uuid

from . import config

KINDS = ("html", "md", "svg", "code", "text")
MAX_ITEMS = 200
MAX_VERSIONS = 40
MAX_CHARS = 400_000                 # per version
MAX_TITLE = 100
_lock = threading.RLock()

# What a page may do in the frame: run its own scripts and styles and show pictures that are inside it, and nothing
# else (no requests, no forms, no frames, no links out). The frame is also sandboxed without allow-same-origin.
SANDBOX_CSP = ("default-src 'none'; img-src data: blob:; media-src data: blob:; font-src data:; "
               "style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'")


def _dir():
    return os.path.join(config.CONFIG_DIR, "artifacts")


def _path(aid):
    if not re.fullmatch(r"[0-9a-f]{8}", str(aid or "")):
        raise LookupError("There is no such artifact.")
    return os.path.join(_dir(), aid + ".json")


def _read(aid):
    try:
        with open(_path(aid), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise LookupError("There is no such artifact.") from None
    if not isinstance(data, dict) or not isinstance(data.get("versions"), list) or not data["versions"]:
        raise LookupError("There is no such artifact.")
    return data


def _write(data):
    os.makedirs(_dir(), exist_ok=True)
    p = _path(data["id"])
    tmp = f"{p}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, p)


def _title(t):
    t = re.sub(r"\s+", " ", str(t or "")).strip()[:MAX_TITLE]
    return t or "Untitled"


def _content(c):
    c = str(c if c is not None else "")
    if not c.strip():
        raise ValueError("The content is empty.")
    if len(c) > MAX_CHARS:
        raise ValueError(f"That is too long for an artifact ({len(c):,} characters, at most {MAX_CHARS:,}).")
    return c


def guess_kind(content, kind=None):
    k = str(kind or "").lower().strip()
    k = {"markdown": "md", "page": "html", "webpage": "html", "document": "md", "diagram": "svg", "txt": "text",
         "plain": "text"}.get(k, k)
    if k in KINDS:
        return k
    head = str(content or "").lstrip()[:200].lower()
    if head.startswith("<svg"):
        return "svg"
    if head.startswith("<!doctype html") or head.startswith("<html"):
        return "html"
    return "md"


def _summary(d):
    last = d["versions"][-1]
    return {"id": d["id"], "title": d["title"], "kind": d["kind"], "versions": len(d["versions"]),
            "version": last["n"], "by": last.get("by", "model"), "updated": d["updated"], "created": d["created"],
            "size": len(last["content"])}


def create(title, content, kind=None, by="model"):
    content = _content(content)
    with _lock:
        if len(all_()) >= MAX_ITEMS:
            raise ValueError(f"There are {MAX_ITEMS} artifacts already. Delete some you do not need.")
        now = time.time()
        d = {"id": uuid.uuid4().hex[:8], "title": _title(title), "kind": guess_kind(content, kind), "created": now,
             "updated": now, "versions": [{"n": 1, "at": now, "by": by, "note": "First version", "content": content}]}
        _write(d)
    return _summary(d)


def all_():
    out = []
    try:
        names = os.listdir(_dir())
    except OSError:
        return out
    for n in names:
        if n.endswith(".json"):
            try:
                out.append(_summary(_read(n[:-5])))
            except LookupError:
                continue
    return sorted(out, key=lambda x: -x["updated"])


def find(ref):
    """An artifact by its id, or by its title when the model wrote that instead."""
    ref = str(ref or "").strip()
    if re.fullmatch(r"[0-9a-f]{8}", ref):
        return ref
    low = ref.lower()
    items = all_()
    hit = [i for i in items if i["title"].lower() == low] or [i for i in items if low and low in i["title"].lower()]
    if len(hit) == 1:
        return hit[0]["id"]
    if len(hit) > 1:
        raise ValueError("More than one artifact has that name. Use its id: " + ", ".join(i["id"] for i in hit[:5]))
    raise LookupError(f"There is no artifact called {ref}. Use artifacts_list to see them.")


def get(aid, version=None):
    d = _read(aid)
    vs = d["versions"]
    v = vs[-1] if version in (None, "") else next((x for x in vs if x["n"] == int(version)), None)
    if v is None:
        raise LookupError("There is no such version.")
    doc = document(v["content"], d["kind"]) if d["kind"] in ("html", "svg") else None
    return {**_summary(d), "content": v["content"], "doc": doc, "shown": v["n"], "note": v.get("note", ""),
            "history": [{"n": x["n"], "at": x["at"], "by": x.get("by", "model"), "note": x.get("note", ""),
                         "size": len(x["content"])} for x in vs]}


def update(aid, content=None, title=None, by="model", note=""):
    """A new version (the old ones stay). Returns the summary."""
    with _lock:
        d = _read(aid)
        if title is not None:
            d["title"] = _title(title)
        if content is not None:
            content = _content(content)
            if content != d["versions"][-1]["content"]:
                d["versions"].append({"n": d["versions"][-1]["n"] + 1, "at": time.time(), "by": by,
                                      "note": str(note or "")[:120], "content": content})
                del d["versions"][1:-(MAX_VERSIONS - 1)]        # the first version and the newest ones
        d["updated"] = time.time()
        _write(d)
    return _summary(d)


def edit(aid, find_text, replace_text, by="model", note=""):
    """Changes one exact piece of the text, the cheap way to fix a detail in a long page."""
    with _lock:
        d = _read(aid)
        cur = d["versions"][-1]["content"]
        n = cur.count(find_text) if find_text else 0
        if n == 0:
            raise ValueError("That text is not in the artifact. Copy it exactly from artifacts_read, or use "
                             "artifacts_update to send the whole new version.")
        if n > 1:
            raise ValueError(f"That text appears {n} times. Include more of the text around it so it is unique.")
        return update(aid, cur.replace(find_text, str(replace_text or ""), 1), by=by, note=note or "Edited")


def restore(aid, version, by="you"):
    with _lock:
        d = _read(aid)
        v = next((x for x in d["versions"] if x["n"] == int(version)), None)
        if v is None:
            raise LookupError("There is no such version.")
        return update(aid, v["content"], by=by, note=f"Went back to version {v['n']}")


def delete(aid):
    with _lock:
        try:
            os.remove(_path(aid))
        except FileNotFoundError:
            raise LookupError("There is no such artifact.") from None
    return True


def document(content, kind):
    """What the sandboxed frame shows for an html or svg artifact: the page with a policy that forbids the network."""
    csp = f'<meta http-equiv="Content-Security-Policy" content="{SANDBOX_CSP}">'
    if kind == "svg":
        return (f'<!doctype html><meta charset="utf-8">{csp}<style>html,body{{margin:0;height:100%}}'
                f'body{{display:flex;align-items:center;justify-content:center}}svg{{max-width:100%;max-height:100%}}'
                f'</style>{content}')
    return f'<!doctype html><meta charset="utf-8">{csp}' + content
