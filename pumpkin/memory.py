"""What Pumpkin remembers about you, kept in one private file on this computer.

The model saves lasting facts with the memory tools (only when you state them or ask it to remember), and every
chat starts with them in the system message. You can read, edit and delete each one on the Memory page, or switch
memory off. Nothing here is sent anywhere."""
import json
import os
import re
import threading
import time
import uuid

from . import config

MAX_ITEMS = 300
MAX_TEXT = 400                  # characters per memory
PROMPT_CHARS = 4000             # how much of it goes into each chat
_lock = threading.Lock()


def _path():
    return os.path.join(config.CONFIG_DIR, "memory.json")


def load():
    try:
        with open(_path(), encoding="utf-8") as f:
            items = json.load(f).get("items", [])
        return [i for i in items if isinstance(i, dict) and i.get("id") and i.get("text")]
    except (OSError, ValueError, AttributeError):
        return []


def _save(items):
    config.ensure_dirs()
    p = _path()
    tmp = p + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)        # it is about you: private to you
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, indent=1, ensure_ascii=False)
    os.replace(tmp, p)


def _clean(text):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        raise ValueError("The memory is empty.")
    if len(text) > MAX_TEXT:
        raise ValueError(f"Keep a memory under {MAX_TEXT} characters: one fact at a time.")
    return text


def _norm(text):
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


def add(text, source="chat"):
    """Saves one fact. The same fact twice is kept once (the newer wording wins)."""
    text = _clean(text)
    with _lock:
        items = load()
        same = next((i for i in items if _norm(i["text"]) == _norm(text)), None)
        if same:
            same.update(text=text, at=int(time.time()))
            _save(items)
            return same
        item = {"id": uuid.uuid4().hex[:10], "text": text, "at": int(time.time()), "source": source}
        items.append(item)
        del items[:-MAX_ITEMS]                                              # the oldest go first
        _save(items)
        return item


def update(mem_id, text):
    text = _clean(text)
    with _lock:
        items = load()
        for i in items:
            if i["id"] == mem_id:
                i.update(text=text, at=int(time.time()))
                _save(items)
                return i
    raise FileNotFoundError("That memory is gone.")


def remove(mem_id):
    with _lock:
        items = load()
        kept = [i for i in items if i["id"] != mem_id]
        if len(kept) == len(items):
            raise FileNotFoundError("That memory is gone.")
        _save(kept)


def forget(what):
    """Removes memories by id, or the ones whose words best match what the model was asked to forget."""
    what = str(what or "").strip()
    if not what:
        raise ValueError("Say what to forget.")
    with _lock:
        items = load()
        hit = [i for i in items if i["id"] == what]
        if not hit:
            want = set(_norm(what).split())
            scored = []
            for i in items:
                words = set(_norm(i["text"]).split())
                if want and words:
                    score = len(want & words) / len(want)
                    if score >= 0.6:
                        scored.append((score, i))
            scored.sort(key=lambda x: -x[0])
            best = scored[0][0] if scored else 0
            hit = [i for s, i in scored if s == best]
        if not hit:
            return []
        ids = {i["id"] for i in hit}
        _save([i for i in items if i["id"] not in ids])
        return hit


def clear():
    with _lock:
        _save([])


def prompt_block(limit=PROMPT_CHARS):
    """The memories as a block for the system message, newest kept when there are too many."""
    items = load()
    if not items:
        return ""
    lines, used = [], 0
    order = sorted(range(len(items)), key=lambda k: (items[k].get("at", 0), k), reverse=True)   # newest first
    for i in (items[k] for k in order):
        line = f"- {i['text']}"
        if used + len(line) > limit:
            break
        lines.append(line)
        used += len(line) + 1
    lines.reverse()
    return "About the user (use when relevant):\n" + "\n".join(lines)
