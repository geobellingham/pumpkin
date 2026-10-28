"""Keeping llama.cpp new: which release to download, the Update button, the automatic update, and a check that a
newer llama-server still speaks the API Pumpkin's chat was built for."""
import calendar
import glob
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from http.client import HTTPException

from . import config, llama

STATE_PATH = os.path.join(config.STATE_DIR, "llama-update.json")
CHECK_EVERY = 6 * 3600             # how often GitHub is asked for a newer llama.cpp
SETTLE = 24 * 3600                 # "latest release" skips builds younger than this: a broken one is usually fixed by then
STALE_DAYS = 7                     # the Overview mentions an update once the build in use is this much older


def _load():
    try:
        with open(STATE_PATH) as f:
            j = json.load(f)
        return j if isinstance(j, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(st):
    os.makedirs(config.STATE_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


def build_number(tag):
    m = re.fullmatch(r"b(\d+)", str(tag or ""))
    return int(m.group(1)) if m else 0


def owned(path):
    """Whether this llama-server is one Pumpkin downloaded (and so may replace). One you built or installed is yours."""
    if not path:
        return False
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(llama.LLAMA_DIR)]) == \
            os.path.realpath(llama.LLAMA_DIR)
    except ValueError:                                  # another drive on Windows
        return False


def downloaded():
    """Pumpkin's own llama.cpp downloads, newest build first: [{"tag", "backend", "dir", "path"}]."""
    out = []
    for d in glob.glob(os.path.join(llama.LLAMA_DIR, "b*-*")):
        m = re.fullmatch(r"(b\d+)-(\w+)", os.path.basename(d))
        if not m or not os.path.isdir(d):
            continue
        hits = glob.glob(os.path.join(d, "**", llama.EXE), recursive=True)
        if hits:
            out.append({"tag": m.group(1), "backend": m.group(2), "dir": d, "path": hits[0]})
    return sorted(out, key=lambda x: build_number(x["tag"]), reverse=True)


def backend_of(path, default):
    """The backend of a Pumpkin download (from its folder name, "b12345-vulkan"), so an update keeps it."""
    for d in downloaded():
        if os.path.realpath(d["path"]) == os.path.realpath(path or ""):
            return d["backend"]
    return default


# ---------------------------------------------------------------- what GitHub has
def releases():
    """Recent llama.cpp releases: [{"tag", "at", "assets"}], newest first. llama.cpp publishes a release for
    every build, a few a day."""
    headers = {"User-Agent": llama.USER_AGENT, "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    req = urllib.request.Request(f"{llama.GITHUB_API}/repos/ggml-org/llama.cpp/releases?per_page=40", headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        items = json.loads(r.read().decode())
    out = []
    for it in items if isinstance(items, list) else []:
        tag = it.get("tag_name") or ""
        if not build_number(tag) or it.get("draft"):
            continue
        at = it.get("published_at") or it.get("created_at") or ""
        try:
            ts = calendar.timegm(time.strptime(at, "%Y-%m-%dT%H:%M:%SZ"))
        except (ValueError, OverflowError):
            ts = 0
        out.append({"tag": tag, "at": ts, "pre": bool(it.get("prerelease")),
                    "assets": [a.get("name") for a in it.get("assets") or []]})
    return sorted(out, key=lambda x: build_number(x["tag"]), reverse=True)


def pick(rels, backend, channel="release", bad=(), now=None, arch=None, system=None):
    """The release to install: the newest that has a file for this computer and has not failed here before.
    "release" waits a day after a build comes out; "newest" takes it the moment its files are up."""
    now = now or time.time()
    arch = arch or llama.machine_arch()
    limit = build_number(((bad or {}).get(f"too_new/{backend}") or {}).get("tag") if isinstance(bad, dict) else None)
    for strict in (True, False):
        for r in rels:
            if r["tag"] in bad or f"{r['tag']}-{backend}" in bad:
                continue
            if limit and build_number(r["tag"]) >= limit:
                continue                                # needs a newer system than this one (see mark_too_new)
            if channel != "newest" and (now - r["at"] < SETTLE or (strict and r.get("pre"))):
                continue
            if any(n in r["assets"] for n in llama.asset_names(r["tag"], backend, arch, system)):
                return r["tag"]
        if channel == "newest":
            break
    return None


def check(settings, backend, force=False, current=None, channel=None):
    """The newest release for this computer, asked from GitHub at most every few hours.
    Returns the tag, or None when GitHub could not be asked and nothing is known yet."""
    st = _load()
    channel = channel or settings["llama_channel"] or "release"
    key = f"{backend}/{channel}"
    known = (st.get("latest") or {}).get(key)
    if not force and known and time.time() - st.get("at", 0) < CHECK_EVERY:
        return known
    try:
        rels = releases()
    except (urllib.error.URLError, OSError, ValueError, HTTPException):
        return known
    st = _load()
    tag = pick(rels, backend, channel, {**(st.get("bad") or {}), **{f"too_new/{b}": v for b, v in too_new().items()}})
    st.setdefault("latest", {})[key] = tag
    st["at"] = time.time()
    st["dates"] = {r["tag"]: r["at"] for r in rels}       # llama.cpp ships ~20 builds a day: this is 2 days or so
    if current and current not in st["dates"] and current not in (st.get("published") or {}):
        # the build in use is older than the list: ask GitHub when it came out, once, for "a week behind"
        try:
            req = urllib.request.Request(f"{llama.GITHUB_API}/repos/ggml-org/llama.cpp/releases/tags/{current}",
                                         headers={"User-Agent": llama.USER_AGENT, "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                at = json.loads(r.read().decode()).get("published_at") or ""
            st.setdefault("published", {})[current] = calendar.timegm(time.strptime(at, "%Y-%m-%dT%H:%M:%SZ"))
        except (urllib.error.URLError, OSError, ValueError, HTTPException):
            pass
    _save(st)
    return tag


def cached(settings, backend, current):
    """No network: what the last look found, for the state the UI polls. stale: the build in use is more than a
    week older than the newest (llama.cpp ships a few builds a day, so only an old one is worth a mention)."""
    st = _load()
    latest = (st.get("latest") or {}).get(f"{backend}/{settings['llama_channel'] or 'release'}")
    avail = bool(latest) and bool(current) and build_number(latest) > build_number(current)
    dates = {**(st.get("published") or {}), **(st.get("dates") or {})}
    if not avail:
        return {"available": False, "latest": latest, "stale": False}
    cur_at, new_at = dates.get(current), dates.get(latest) or time.time()
    stale = bool(cur_at) and new_at - cur_at > STALE_DAYS * 86400
    return {"available": True, "latest": latest, "stale": stale}


def target_tag(settings, backend, channel=None):
    """What Setup and the Update button install: the newest release, or the one the chat UI was built from when
    GitHub cannot be asked. PUMPKIN_LLAMA_TAG pins one. channel="newest" for a model llama.cpp only just learned."""
    if os.environ.get("PUMPKIN_LLAMA_TAG"):
        return os.environ["PUMPKIN_LLAMA_TAG"]
    tag = check(settings, backend, force=True, channel=channel)
    return tag or llama.pinned_tag() or llama.latest_tag()


def mark_bad(tag, why):
    st = _load()
    st.setdefault("bad", {})[tag] = why[:300]
    for k in list((st.get("latest") or {})):
        if st["latest"][k] == tag:
            st["latest"].pop(k)                         # look again, skipping this one
    _save(st)


def mark_too_new(backend, tag, why, days=30, kind="system"):
    """A build that does not start on this computer because the system is older than it needs (glibc on Linux,
    the macOS version on a Mac): every build from this one on will need it too, so they are not downloaded again."""
    st = _load()
    cur = too_new().get(backend)                        # one that ran out of time does not hold the new one back
    if not cur or build_number(tag) < build_number(cur.get("tag")):
        st.setdefault("too_new", {})[backend] = {"tag": tag, "why": why[:300], "at": time.time(), "days": days,
                                                 "kind": kind}
    st["latest"] = {}                                   # look again, below that build
    _save(st)


def system_hint(err):
    """Plain words for a build that needs a newer system."""
    m = re.search(r"GLIBC_(\d+\.\d+)", err or "")
    if m:
        return f"it needs a newer Linux (glibc {m.group(1)} or later, like Ubuntu 24.04)"
    if re.search(r"macOS|LC_BUILD_VERSION|Symbol not found", err or ""):
        return "it needs a newer macOS than this one"
    return ""


def too_new():
    """Per backend, the first build too new for this system. Forgotten after a month (a week when only the GPU code
    failed), in case the system was updated."""
    return {b: v for b, v in (_load().get("too_new") or {}).items()
            if time.time() - v.get("at", 0) < v.get("days", 30) * 86400}


SYSTEM_TOO_OLD = r"GLIBC_\d|GLIBCXX_\d|built for macOS|was built for newer|Symbol not found|minimum.*macOS|LC_BUILD_VERSION"


def bad_tags():
    out = dict(_load().get("bad") or {})
    for backend, v in too_new().items():
        out[f"{v['tag']} and newer ({backend})"] = (f"their GPU code does not work on this computer: {v['why']}"
                                                    if v.get("kind") == "gpu" else
                                                    f"need a newer system than this one: {v['why']}")
    return out


def mark_ok(tag):
    st = _load()
    ok = st.setdefault("ok", [])
    if tag not in ok:
        ok.append(tag)
        st["ok"] = ok[-20:]
    _save(st)


def checked_at():
    return _load().get("at")


def checked_ok(tag):
    return tag in (_load().get("ok") or [])


# ---------------------------------------------------------------- does the new server fit the chat?
def check_api(port, flags=()):
    """Asks a freshly started llama-server the questions Pumpkin's chat asks. Returns (serious, minor): what broke
    the chat outright (it cannot show the model or read its answers) and what only breaks a part of it."""
    serious, minor = [], []
    props = llama.http(port, "/props", timeout=20)
    gen = props.get("default_generation_settings") if isinstance(props, dict) else None
    if not isinstance(props, dict) or props.get("error"):
        serious.append(f"/props did not answer ({(props or {}).get('error') if isinstance(props, dict) else props})")
    elif not isinstance(gen, dict) or not isinstance(gen.get("n_ctx"), int):
        serious.append("/props no longer says the context size where the chat looks for it")
    elif props.get("role") == "router":
        serious.append("the server says it is a router, which the chat handles differently")
    elif "modalities" in props and not isinstance(props["modalities"], dict):
        minor.append("/props reports what the model can see in a new way")
    try:
        problem = _stream_ok(port)
    except (OSError, ValueError, HTTPException) as e:
        problem = f"the chat request failed ({e})"
    if problem:
        serious.append(problem)
    slots = llama.http(port, "/slots", timeout=10)
    if not isinstance(slots, list) and not (isinstance(slots, dict) and slots.get("status") in (404, 501)):
        minor.append("/slots answers in a new way (the loading progress may not show)")
    if "--agent" in flags or "--mcp-servers-config" in flags:
        tools = llama.http(port, "/tools", timeout=10)
        if not isinstance(tools, list) and not (isinstance(tools, dict) and tools.get("status") in (404, 501)):
            minor.append("/tools answers in a new way (the model's tools may not show)")
    return serious, minor


def _stream_ok(port):
    """A one-word streamed answer, read the way the chat reads it. Returns what is wrong, or None."""
    import http.client
    body = json.dumps({"messages": [{"role": "user", "content": "Say hi."}], "stream": True, "max_tokens": 1,
                       "temperature": 0}).encode()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    try:
        conn.request("POST", "/v1/chat/completions", body=body,
                     headers={"Content-Type": "application/json", **llama.auth_header()})
        r = conn.getresponse()
        raw = r.read(1 << 20).decode("utf-8", "replace")
    finally:
        conn.close()
    if r.status != 200:
        return f"a chat request got HTTP {r.status}: {raw[:200]}"
    chunks, done = 0, False
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            done = True
            continue
        j = json.loads(data)
        ch = j.get("choices")
        if not isinstance(ch, list):
            return "streamed answers no longer have choices"
        if ch and not isinstance(ch[0].get("delta"), dict):
            return "streamed answers no longer carry a delta"
        chunks += 1
    if not chunks:
        return "the streamed answer was empty or in a new format"
    if not done:
        return "the streamed answer did not end the way the chat expects"
    return None


# ---------------------------------------------------------------- old builds
def prune(keep):
    """Removes Pumpkin's older llama.cpp downloads, keeping the ones in `keep` (the one in use and the one before,
    for going back). A folder still in use (Windows) is skipped and goes next time."""
    keep = {os.path.realpath(p) for p in keep if p}
    for d in downloaded():
        if os.path.realpath(d["path"]) in keep:
            continue
        shutil.rmtree(d["dir"], ignore_errors=True)
