"""The AI's work folder: ~/Pumpkin/files on this computer, /workspace inside the sandbox.

One long-lived Docker container has only that folder mounted. llama-server's tools attach to it
(--tools-runtime docker-container:pumpkin-workspace), so everything the AI writes lands in the folder
and survives model restarts, while the rest of the computer stays out of reach."""
import mimetypes
import os
import shutil
import subprocess
import sys

from . import config

NAME = "pumpkin-workspace"
MOUNT = "/workspace"


def files_dir(settings):
    d = os.path.expanduser(settings["files_dir"])
    os.makedirs(d, exist_ok=True)
    return os.path.realpath(d)


def _docker(*args, timeout=60):
    try:
        r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", str(e)


_usable = {"at": 0.0, "ok": False}


def docker_usable():
    """Docker is installed, its daemon answers this user, and it runs Linux containers (Docker Desktop on Windows
    can be switched to Windows containers). Installed but stopped, or the user not in the docker group, counts as
    not usable. Asked at most once a minute."""
    if not shutil.which("docker"):
        return False
    import time
    if time.time() - _usable["at"] > 60:
        code, out, _ = _docker("info", "--format", "{{.OSType}}", timeout=15)
        _usable.update(at=time.time(), ok=code == 0 and out.strip() == "linux")
    return _usable["ok"]


def ensure(settings):
    """Starts the sandbox with the work folder mounted, if Docker is there. Returns its name, or None."""
    if not docker_usable():
        return None
    folder = files_dir(settings)
    image = settings["sandbox_image"]
    code, out, _ = _docker("inspect", NAME, "--format",
                           "{{.State.Running}}|{{.Config.Image}}|{{index .Config.Labels \"pumpkin.mount\"}}")
    if code == 0:
        running, img, mount = (out.split("|") + ["", "", ""])[:3]
        if img == image and mount == folder:
            if running != "true":
                _docker("start", NAME)
            return NAME
        _docker("rm", "-f", NAME)                      # the folder or image changed in Settings
    code, out, err = _docker("run", "-d", "--name", NAME, "--init", "--label", f"pumpkin.mount={folder}",
                             "-v", f"{folder}:{MOUNT}", "-w", MOUNT, image, "sleep", "infinity", timeout=600)
    return NAME if code == 0 else None


def stop():
    if shutil.which("docker"):
        _docker("rm", "-f", NAME, timeout=30)


def fix_owner(settings):
    """On Linux the sandbox runs as root, so give what it made back to you (a Mac does this by itself)."""
    if sys.platform == "darwin" or not shutil.which("docker") or not hasattr(os, "getuid"):
        return
    _docker("exec", NAME, "chown", "-R", f"{os.getuid()}:{os.getgid()}", MOUNT, timeout=60)


# ---------------------------------------------------------------- the Files page
def safe_path(settings, rel):
    """A path inside the work folder. Anything that would leave it (.., links) is refused."""
    base = files_dir(settings)
    rel = (rel or "").replace("\\", "/").strip("/")
    if rel.startswith(MOUNT.strip("/") + "/") or rel == MOUNT.strip("/"):
        rel = rel[len(MOUNT.strip("/")):].strip("/")   # the AI says /workspace/x.md; that is x.md here
    full = os.path.realpath(os.path.join(base, rel))
    if full != base and not full.startswith(base + os.sep):
        raise PermissionError("That is outside the work folder.")
    return full


def listing(settings, rel=""):
    fix_owner(settings)
    base = files_dir(settings)
    d = safe_path(settings, rel)
    if not os.path.isdir(d):
        raise FileNotFoundError("That folder does not exist.")
    items = []
    for name in os.listdir(d):
        if name.startswith("."):
            continue
        p = os.path.join(d, name)
        try:
            st = os.stat(p)
        except OSError:
            continue
        isdir = os.path.isdir(p)
        items.append({"name": name, "path": _rel(p, base), "dir": isdir,
                      "size": 0 if isdir else st.st_size, "mtime": int(st.st_mtime),
                      "type": "" if isdir else (mimetypes.guess_type(name)[0] or "")})
    items.sort(key=lambda x: (not x["dir"], -x["mtime"]))
    return {"path": _rel(d, base) if d != base else "", "items": items, "folder": base,
            "display": _short(base), "sandbox": MOUNT}


def recent(settings, limit=6):
    """Newest files anywhere in the folder (for the Overview)."""
    base = files_dir(settings)
    out = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for f in files:
            if f.startswith("."):
                continue
            p = os.path.join(root, f)
            try:
                out.append((os.stat(p).st_mtime, p))
            except OSError:
                pass
    out.sort(reverse=True)
    return [{"name": os.path.basename(p), "path": _rel(p, base), "mtime": int(t)} for t, p in out[:limit]]


WIN_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


def _rel(p, base):
    """Paths the UI sees always use /, also on Windows."""
    return os.path.relpath(p, base).replace(os.sep, "/")


def save_upload(settings, rel_dir, name, data):
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    if not name or name.startswith("."):
        raise ValueError("Pick a file with a normal name.")
    if os.name == "nt" and (any(c in name for c in '<>:"|?*') or name.split(".")[0].lower() in WIN_RESERVED
                            or name.endswith((".", " "))):
        # a colon would write into a hidden stream of another file; CON, NUL and friends are devices
        raise ValueError("Windows does not allow that file name. Rename the file and try again.")
    d = safe_path(settings, rel_dir)
    os.makedirs(d, exist_ok=True)
    target = os.path.join(d, name)
    stem, ext = os.path.splitext(name)
    n = 2
    while True:
        try:
            # never open what is there, also not a link pointing out of the folder (even a broken one). "x" covers
            # that on Linux and macOS; Windows follows a broken link even with "x", so any name that exists as a
            # link or file is skipped first
            if os.path.lexists(target):
                raise FileExistsError(target)
            f = open(target, "xb")
            break
        except FileExistsError:                         # never overwrite: report.md -> report (2).md
            target = os.path.join(d, f"{stem} ({n}){ext}")
            n += 1
    with f:
        f.write(data)
    return _rel(target, files_dir(settings))


def delete(settings, rel):
    fix_owner(settings)
    rel = (rel or "").replace("\\", "/").strip("/")
    name = os.path.basename(rel)
    if not rel or name in (".", ".."):
        raise PermissionError("The work folder itself stays.")
    # the folder it is in must be inside; the item itself may be a link, which goes without what it points to
    p = os.path.join(safe_path(settings, os.path.dirname(rel)), name)
    if os.path.islink(p):
        os.remove(p)
        return
    p = safe_path(settings, rel)
    if p == files_dir(settings):
        raise PermissionError("The work folder itself stays.")
    if os.path.isdir(p):
        shutil.rmtree(p)
    else:
        os.remove(p)


def reveal(settings, rel=""):
    """Opens the folder in Files or Finder (on this computer)."""
    p = safe_path(settings, rel)
    if os.name == "nt":
        os.startfile(p if os.path.isdir(p) else os.path.dirname(p))       # noqa: S606 - File Explorer
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    if not shutil.which(opener):
        raise RuntimeError("No file manager to open.")
    subprocess.Popen([opener, p if os.path.isdir(p) else os.path.dirname(p)], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def _short(path):
    home = config.HOME.rstrip(os.sep)
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path

