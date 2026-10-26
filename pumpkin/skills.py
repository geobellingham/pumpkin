"""Skills: folders of instructions for a kind of job, in the SKILL.md format Claude uses.

A skill is a folder with a SKILL.md: a short front matter with `name` and `description`, then the instructions. It can
hold more files (references, templates, scripts) next to it. The model only ever sees each skill's name and one-line
description; when a request fits one, it reads the instructions with the skills_read tool. That keeps the cost small on
a model with little context.

Skills come from two places: the ones shipped with Pumpkin (pumpkin/skills) and yours (the skills folder in Pumpkin's
settings folder). Yours win when both have the same name. Pumpkin Code also reads skills from the project folder
(.pumpkin/skills, .claude/skills or .agents/skills)."""
import io
import os
import re
import shutil
import urllib.request
import zipfile

from . import config

BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
PROJECT_DIRS = (".pumpkin/skills", ".claude/skills", ".agents/skills")
MAX_FILE = 1_000_000                      # one file inside a skill
MAX_SKILL = 5_000_000                     # a whole skill folder
MAX_LISTED = 40                           # skills listed for the model; more only costs context


def user_dir():
    return os.path.join(config.CONFIG_DIR, "skills")


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return s[:64] or "skill"


def parse(text):
    """SKILL.md text -> (front matter dict, body). The front matter is the simple `key: value` lines between two
    --- lines; quotes around a value are taken off, and a value can continue on indented lines."""
    text = str(text or "").replace("\r\n", "\n").lstrip("﻿")
    meta = {}
    m = re.match(r"---\n(.*?)\n---[ \t]*\n?", text, re.S)
    if not m:
        return meta, text.strip()
    key = None
    for line in m.group(1).split("\n"):
        kv = re.match(r"([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if kv and not line.startswith((" ", "\t")):
            key = kv.group(1).lower()
            val = kv.group(2).strip()
            meta[key] = "" if val in ("|", ">", "|-", ">-") else val
        elif key and line.strip():
            meta[key] = (meta[key] + " " + line.strip()).strip()
    for k, v in meta.items():
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            meta[k] = v[1:-1]
    return meta, text[m.end():].strip()


def read_dir(folder, source, default_name=None):
    """One skill folder -> dict, or None when there is no usable SKILL.md."""
    path = os.path.join(folder, "SKILL.md")
    try:
        if os.path.getsize(path) > MAX_FILE:
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            meta, body = parse(f.read())
    except OSError:
        return None
    name = (meta.get("name") or default_name or os.path.basename(folder)).strip()
    if not name:
        return None
    files = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in sorted(names):
            if n.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(root, n), folder).replace("\\", "/")
            if rel != "SKILL.md":
                files.append(rel)
    return {"name": name, "description": re.sub(r"\s+", " ", meta.get("description", "")).strip(),
            "body": body, "dir": folder, "files": files[:200], "source": source}


def _scan(root, source):
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for n in names:
        d = os.path.join(root, n)
        if os.path.isdir(d) and not n.startswith("."):
            s = read_dir(d, source)
            if s:
                out.append(s)
    return out


def all_skills(settings=None, project=None):
    """Every skill by name: Pumpkin's own, yours over them, then a project's over both. Each has "enabled"."""
    off = set((settings or {}).get("skills_off") or []) if settings is not None else set()
    found = {}
    for s in _scan(BUILTIN_DIR, "builtin") + _scan(user_dir(), "yours"):
        found[s["name"].lower()] = s
    if project:
        for rel in PROJECT_DIRS:
            for s in _scan(os.path.join(project, rel), "project"):
                found[s["name"].lower()] = s
    out = sorted(found.values(), key=lambda s: (s["source"] != "project", s["name"].lower()))
    for s in out:
        s["enabled"] = s["name"] not in off
    return out


def enabled(settings=None, project=None):
    return [s for s in all_skills(settings, project) if s["enabled"]]


def find(name, settings=None, project=None):
    want = str(name or "").strip().lower()
    for s in enabled(settings, project):
        if s["name"].lower() == want or slug(s["name"]) == slug(want):
            return s
    return None


def listing(skills, limit_chars=4000):
    """The lines the model sees: one per skill, name and description."""
    lines, used = [], 0
    for s in skills[:MAX_LISTED]:
        line = f"- {s['name']}: {s['description'][:240] or '(no description)'}"
        if used + len(line) > limit_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def read_text(s):
    """What skills_read returns: the instructions and the files the skill brings along."""
    out = f"# Skill: {s['name']}\n\n{s['body']}"
    if s["files"]:
        out += ("\n\nFiles in this skill (read one with skills_file, giving the skill name and the path):\n"
                + "\n".join(f"- {f}" for f in s["files"][:60]))
    return out


def read_file(s, rel):
    root = os.path.realpath(s["dir"])
    p = os.path.realpath(os.path.join(root, str(rel or "").lstrip("/\\")))
    try:
        inside = os.path.commonpath([root, p]) == root
    except ValueError:                           # another drive on Windows
        inside = False
    if not inside or not os.path.isfile(p):
        raise ValueError(f"The skill {s['name']} has no file {rel}.")
    if os.path.getsize(p) > MAX_FILE:
        raise ValueError(f"{rel} is too big to read.")
    with open(p, "rb") as f:
        data = f.read()
    if b"\x00" in data[:4096]:
        raise ValueError(f"{rel} is not a text file.")
    return data.decode("utf-8", "replace")


# ---------------------------------------------------------------- yours: write, import, delete
def _safe_dest(name):
    os.makedirs(user_dir(), exist_ok=True)
    return os.path.join(user_dir(), slug(name))


def front(name, description, body):
    desc = re.sub(r"\s+", " ", str(description or "")).strip()
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n{str(body or '').strip()}\n"


def save(name, description, body, old_name=None):
    """Creates or changes one of your skills. Returns it."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not name:
        raise ValueError("Give the skill a name.")
    if len(name) > 64:
        raise ValueError("Keep the name under 64 characters.")
    if not str(body or "").strip():
        raise ValueError("Write the instructions.")
    dest = _safe_dest(name)
    if old_name and slug(old_name) != slug(name):
        old = os.path.join(user_dir(), slug(old_name))
        if os.path.isdir(old) and not os.path.exists(dest):
            os.rename(old, dest)
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(front(name, description, body))
    return read_dir(dest, "yours")


def delete(name):
    for s in _scan(user_dir(), "yours"):
        if s["name"].lower() == str(name or "").strip().lower():
            shutil.rmtree(s["dir"], ignore_errors=True)
            return True
    return False


def _size(folder):
    total = 0
    for root, _dirs, names in os.walk(folder):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total


def import_folder(path, default_name=None):
    """Copies skills from a folder on this computer: the folder itself when it has a SKILL.md, otherwise every
    folder inside it that has one. Returns the names imported."""
    path = os.path.realpath(os.path.expanduser(str(path or "").strip()))
    if not os.path.isdir(path):
        raise ValueError(f"{path} is not a folder on this computer.")
    found = [path] if os.path.isfile(os.path.join(path, "SKILL.md")) else [
        os.path.dirname(os.path.join(r, "SKILL.md")) for r, _d, n in os.walk(path) if "SKILL.md" in n][:50]
    if not found:
        raise ValueError("There is no SKILL.md in that folder.")
    names = []
    for src in found:
        s = read_dir(src, "yours", default_name if src == path else None)
        if not s:
            continue
        if _size(src) > MAX_SKILL:
            raise ValueError(f"The skill {s['name']} is bigger than 5 MB.")
        dest = _safe_dest(s["name"])
        if os.path.realpath(dest) == os.path.realpath(src):
            continue
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".*", "__pycache__", "node_modules"))
        with open(os.path.join(dest, "SKILL.md"), encoding="utf-8", errors="replace") as f:
            meta, body = parse(f.read())
        if not meta.get("name"):                  # keep the name it was imported under
            with open(os.path.join(dest, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(front(s["name"], s["description"], body))
        names.append(s["name"])
    return names


def _github(url):
    """https://github.com/owner/repo[/tree/ref/sub/path] -> (owner, repo, ref, sub path)."""
    m = re.match(r"https?://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/(?:tree|blob)/([^/]+)(?:/(.*))?)?/?$",
                 str(url or "").strip())
    if not m:
        raise ValueError("Give a GitHub link like https://github.com/owner/repo/tree/main/skills/my-skill.")
    sub = (m.group(4) or "").strip("/")
    if sub.endswith("SKILL.md"):
        sub = sub[:-len("SKILL.md")].strip("/")
    return m.group(1), m.group(2), m.group(3), sub


def _download(url, limit=50_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": "Pumpkin"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("That repository is too big to import from (over 50 MB).")
    return data


def import_github(url, fetch=None):
    """Imports the skills in a GitHub folder (or a whole repository). Returns the names imported."""
    owner, repo, ref, sub = _github(url)
    fetch = fetch or _download
    data, err = None, None
    for r in ([ref] if ref else ["HEAD", "main", "master"]):
        try:
            data = fetch(f"https://codeload.github.com/{owner}/{repo}/zip/{r}")
            break
        except (OSError, ValueError) as e:
            err = e
    if data is None:
        raise ValueError(f"Could not download {owner}/{repo}: {err}")
    import tempfile
    tmp = tempfile.mkdtemp(prefix="pumpkin-skill-")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            top = z.namelist()[0].split("/")[0] if z.namelist() else ""
            want = f"{top}/{sub}/" if sub else f"{top}/"
            for info in z.infolist():
                if not info.filename.startswith(want) or info.is_dir() or info.file_size > MAX_FILE:
                    continue
                rel = info.filename[len(want):]
                # a backslash or drive letter would leave the folder on Windows, so such names are skipped
                if ".." in rel.split("/") or "\\" in rel or ":" in rel or rel.startswith("/"):
                    continue
                target = os.path.join(tmp, rel)
                if os.path.commonpath([os.path.realpath(tmp), os.path.realpath(target)]) != os.path.realpath(tmp):
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as src, open(target, "wb") as dst:
                    dst.write(src.read())
        return import_folder(tmp, default_name=(sub.split("/")[-1] if sub else repo))
    except zipfile.BadZipFile as e:
        raise ValueError("GitHub sent something that is not a zip file.") from e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
