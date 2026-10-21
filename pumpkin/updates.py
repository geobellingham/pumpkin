"""Checks GitHub for a newer Pumpkin release and runs the installer to update."""
import json
import os
import re
import subprocess
import time
import urllib.request

from . import __version__, config

REPO = os.environ.get("PUMPKIN_REPO", "geobellingham/pumpkin")
API = os.environ.get("PUMPKIN_UPDATE_URL", f"https://api.github.com/repos/{REPO}/releases/latest")
CACHE = os.path.join(config.STATE_DIR, "update-check.json")
EVERY = 12 * 3600


def _ver(tag):
    return tuple(int(x) for x in re.findall(r"\d+", tag or "")[:3]) or (0,)


def _cached():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def check(force=False):
    """Asks GitHub at most twice a day. Network trouble keeps the last answer."""
    c = _cached()
    if not force and time.time() - c.get("at", 0) < EVERY:
        return c
    try:
        req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": f"pumpkin/{__version__}"})
        with urllib.request.urlopen(req, timeout=6) as r:
            j = json.loads(r.read().decode())
        c = {"at": time.time(), "tag": j.get("tag_name") or "", "url": j.get("html_url") or "",
             "notes": (j.get("body") or "")[:600]}
        os.makedirs(config.STATE_DIR, exist_ok=True)
        with open(CACHE, "w") as f:
            json.dump(c, f)
    except (OSError, ValueError):
        c["failed"] = True
    return c


def status(force=False):
    c = check(force)
    tag = c.get("tag") or ""
    return {"current": __version__, "latest": tag.lstrip("v"), "available": bool(tag) and _ver(tag) > _ver(__version__),
            "url": c.get("url") or f"https://github.com/{REPO}/releases", "notes": c.get("notes") or "",
            "checked": c.get("at"), "failed": bool(c.get("failed"))}


def cached_status():
    """No network: what the last check found. Used for the state the UI polls."""
    c = _cached()
    tag = c.get("tag") or ""
    return {"available": bool(tag) and _ver(tag) > _ver(__version__), "latest": tag.lstrip("v")}


def apply():
    """Runs the installer of the new release in the background. It checks the download's checksum, stops this
    server, installs, and starts the new one."""
    tag = check().get("tag") or ""
    if not re.fullmatch(r"v?\d+(\.\d+){1,3}", tag):
        raise RuntimeError("No new release to update to.")
    script = os.environ.get("PUMPKIN_INSTALL_CMD") or (
        f"curl -fsSL https://raw.githubusercontent.com/{REPO}/{tag}/install.sh | PUMPKIN_TAG={tag} bash -s -- --no-open")
    bin_pumpkin = os.path.join(config.HOME, ".local", "bin", "pumpkin")
    cmd = f"sleep 1; {script}"
    if not os.environ.get("PUMPKIN_INSTALL_CMD"):          # a custom command (tests) is left to do its own thing
        cmd += f" && {bin_pumpkin} up"                     # the login service if there is one, else in the background
    log = open(os.path.join(config.STATE_DIR, "update.log"), "w")
    subprocess.Popen(["bash", "-c", cmd], stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                     cwd=config.HOME)
    log.close()
