"""Where things live, and the JSON files that hold settings, presets, personas, tasks and prompts."""
import copy
import json
import os
import re
import secrets
import threading
import urllib.request
import uuid

from . import APP_ID

HOME = os.path.expanduser("~")
if os.name == "nt" and not os.environ.get("XDG_CONFIG_HOME"):
    # Windows: settings in %APPDATA% (they roam with the account), downloads and logs in %LOCALAPPDATA%
    _local = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
    CONFIG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming"), APP_ID)
    DATA_DIR = os.path.join(_local, APP_ID)
    STATE_DIR = os.path.join(_local, APP_ID, "state")
else:
    CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), APP_ID)
    DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share"), APP_ID)
    STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local", "state"), APP_ID)
USER_DIR = os.path.join(HOME, "Pumpkin")          # things people open themselves: models, reports

DEFAULT_SETTINGS = {
    "port": 8090,                      # Pumpkin (UI + API)
    "llama_port": 8080,                # llama-server, only ever on 127.0.0.1
    "llama_server": "",                # explicit path; empty = find or use the downloaded one
    "models_dirs": [os.path.join(USER_DIR, "models")],
    "download_dir": os.path.join(USER_DIR, "models"),
    "reports_dir": os.path.join(USER_DIR, "reports"),
    "files_dir": os.path.join(USER_DIR, "files"),       # the AI's work folder, /workspace in the sandbox
    "threads": 0,                      # 0 = physical cores
    "persona": "Assistant",
    "sandbox_image": "ubuntu:24.04",
    "tool_chars": 8000,
    "max_steps": 14,
    "hf_token": "",
    "remote": "off",                   # off | tailscale | lan
    "last_preset": "",
    "autostart_model": False,          # start the last preset when Pumpkin starts
    "notifications": True,
    "ntfy_topic": "",                  # phone push notifications through ntfy.sh (or your own ntfy server)
    "ntfy_server": "https://ntfy.sh",
    "keep_awake": True,                # stop the computer from sleeping while a model is loaded
    "setup_done": False,
    "check_updates": True,             # ask GitHub twice a day if a new Pumpkin is out
    "share": False,                    # other Pumpkins may ask this one's model (Devices page)
    "share_opened_lan": False,         # sharing turned on home-network listening, so turning it off undoes that
    "device_name": "",                 # how other devices see this one; empty = the computer's name
    "share_parallel": 2,               # requests from other devices at once; more get "busy, try later"
    "share_per_hour": 60,              # requests from other devices per hour
    "memory": True,                    # remember facts about you across chats (Memory page), kept on this device
    "doc_index": True,                 # keep your documents searchable for the model (Files page)
    "doc_folders": [],                 # more folders to make searchable, besides the work folder
    "vision_model": "",                # the vision model that reads scans and describes pictures; empty = the smallest one you have
    "vision_port": 8093,
    "vision_relay": True,              # a text-only chat model gets a description of the pictures you attach
    "vision_keep_min": 5,              # minutes the vision model stays loaded after its last job (0 = never frees)
    "keep_context": True,              # save the conversation a model has read when it stops and load it at the next start
    "images": True,                    # the chat's model may draw pictures (Images page) once an image model is in
    "image_dir": os.path.join(USER_DIR, "image-models"),
    "video_model": "",                 # the video model the chat makes clips with; empty = the first one installed
    "edit_model": "",                  # the picture-changing model the chat uses; empty = the first one installed
    "image_model": "",                 # the image model the chat draws with; empty = the first one installed
    "image_port": 8092,                # sd-server, only ever on 127.0.0.1
    "image_swap": "auto",              # auto: stop the text model while drawing when both do not fit | always | never
    "image_keep_min": 10,              # free the image model's memory after this many idle minutes (0 = keep)
    "sd_server": "",                   # explicit sd-server path; empty = the downloaded one
}

DEFAULT_PERSONAS = [
    {"name": "Assistant", "prompt": "You are a helpful, direct assistant. Use your tools when they give a better "
     "answer: search the web for current information and read the pages before relying on them. Give source "
     "URLs for anything you looked up."},
    {"name": "Researcher", "prompt": "You are a careful researcher. For every question: search, open at least 3 "
     "different sources, compare them, and note where they disagree. Never state a fact you did not see in a page "
     "you read. End with a source list."},
    {"name": "Coder", "prompt": "You are a senior software engineer. Write correct, simple code. When you have a "
     "sandbox, run the code to check it before answering, and show the commands you ran."},
    {"name": "Concise", "prompt": "Answer in as few words as possible. No preamble, no filler. Use tools only when "
     "needed."},
]

DEFAULT_PROMPTS = [
    {"title": "Daily briefing", "prompt": "Search today's top world news. Pick the 5 most important stories, open "
     "one article for each, and write a short briefing with the source URL under each item."},
    {"title": "Compare products", "prompt": "Compare the three most popular options for <thing> right now. Read at "
     "least two reviews of each and give me a table of price, strengths and weaknesses, with sources."},
    {"title": "Explain a topic", "prompt": "Explain <topic> to me as if I am smart but new to it. Use one concrete "
     "example and finish with three questions to check I understood."},
    {"title": "Fact check", "prompt": "Find a claim that is going viral this week. Search for sources supporting "
     "and debunking it, read them, and give me a verdict with the strongest evidence on each side."},
    {"title": "Sandbox check", "prompt": "Run `cat /etc/os-release`, `free -h` and `df -h /` and tell me what each "
     "result says about where your commands are running."},
]

DEFAULT_TASKS = [
    {"name": "Morning briefing", "time": "08:00", "days": "weekdays", "preset": "", "enabled": False,
     "stop_after": False, "prompt": DEFAULT_PROMPTS[0]["prompt"]},
]

_lock = threading.RLock()

# Pumpkin talking to itself and to its llama-server on 127.0.0.1 must never go through a proxy, even when
# HTTP_PROXY is set (urllib would send local requests to the proxy, and they would fail)
_local = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def local_open(req, timeout=10):
    return _local.open(req, timeout=timeout)


def ensure_dirs():
    for d in (CONFIG_DIR, DATA_DIR, STATE_DIR):
        os.makedirs(d, exist_ok=True)


def _path(name):
    return os.path.join(CONFIG_DIR, f"{name}.json")


def load(name, default):
    with _lock:
        p = _path(name)
        if not os.path.exists(p):
            return copy.deepcopy(default)
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            # keep a copy of the broken file instead of silently losing it
            try:
                os.replace(p, p + ".broken")
            except OSError:
                pass
            return copy.deepcopy(default)


def save(name, data):
    with _lock:
        ensure_dirs()
        p = _path(name)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, p)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "item"


def new_id():
    return uuid.uuid4().hex[:10]


class Settings:
    """settings.json with defaults filled in."""

    def __init__(self):
        self.data = copy.deepcopy(DEFAULT_SETTINGS)
        self.data.update(load("settings", {}))

    def __getitem__(self, k):
        return self.data.get(k, DEFAULT_SETTINGS.get(k))

    def get(self, k, default=None):
        return self.data.get(k, default)

    def update(self, changes):
        with _lock:
            for k, v in changes.items():
                if k in DEFAULT_SETTINGS:
                    self.data[k] = v
            save("settings", self.data)

    def public(self):
        d = dict(self.data)
        d["hf_token"] = "set" if d.get("hf_token") else ""
        return d


class Collection:
    """A list of dicts in one JSON file, each with a stable id."""

    def __init__(self, name, default, key="name"):
        self.name, self.key = name, key
        items = load(name, None)
        if items is None:
            items = copy.deepcopy(default)
        changed = False
        for it in items:
            if not it.get("id"):
                it["id"] = new_id()
                changed = True
        self.items = items
        if changed or not os.path.exists(_path(name)):
            self.save()

    def save(self):
        save(self.name, self.items)

    def all(self):
        return self.items

    def get(self, ident):
        for it in self.items:
            if it.get("id") == ident or it.get(self.key) == ident:
                return it
        return None

    def add(self, item):
        with _lock:
            item = dict(item)
            item["id"] = new_id()
            self.items.append(item)
            self.save()
            return item

    def update(self, ident, changes):
        with _lock:
            it = self.get(ident)
            if it is None:
                return None
            for k, v in changes.items():
                if k != "id":
                    it[k] = v
            self.save()
            return it

    def delete(self, ident):
        with _lock:
            it = self.get(ident)
            if it is None:
                return False
            self.items.remove(it)
            self.save()
            return True

    def move(self, ident, index):
        with _lock:
            it = self.get(ident)
            if it is None:
                return False
            self.items.remove(it)
            self.items.insert(max(0, min(index, len(self.items))), it)
            self.save()
            return True


def token():
    """The private key that the browser, the phone and the desktop window use."""
    ensure_dirs()
    p = os.path.join(CONFIG_DIR, "token")
    try:
        t = open(p).read().strip()
        if len(t) >= 24:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(24)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(t)
    return t


def reset_token():
    try:
        os.remove(os.path.join(CONFIG_DIR, "token"))
    except OSError:
        pass
    return token()
