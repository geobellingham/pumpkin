"""Find model files other apps already downloaded, so nobody downloads the same 20 GB twice."""
import glob
import json
import os

from . import config

GiB = 1024 ** 3


def _home(*p):
    return os.path.join(config.HOME, *p)


def _folders():
    """(id, label, folder) for the places other apps keep .gguf files."""
    hf = os.environ.get("HF_HUB_CACHE") or os.path.join(os.environ.get("HF_HOME") or _home(".cache", "huggingface"), "hub")
    return [
        ("lmstudio", "LM Studio", _home(".lmstudio", "models")),
        ("lmstudio-old", "LM Studio", _home(".cache", "lm-studio", "models")),
        ("huggingface", "Hugging Face cache", hf),
        ("llamacpp", "llama.cpp cache", os.environ.get("LLAMA_CACHE") or _home(".cache", "llama.cpp")),
        ("jan", "Jan", _home("jan", "data", "models")),
        ("jan-old", "Jan", _home(".local", "share", "Jan", "data", "models")),
        ("gpt4all", "GPT4All", _home(".local", "share", "nomic.ai", "GPT4All")),
        ("downloads", "Your Downloads folder", _home("Downloads")),
    ]


def _short(path):
    home = config.HOME.rstrip(os.sep)
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def _count(folder):
    n, size = 0, 0
    for p in glob.glob(os.path.join(folder, "**", "*.gguf"), recursive=True):
        rel = os.path.relpath(p, folder)
        if any(seg.startswith(".") for seg in rel.split(os.sep)) or "mmproj" in os.path.basename(p).lower():
            continue
        try:
            size += os.stat(p).st_size
        except OSError:
            continue
        n += 1
    return n, size


def ollama_dirs():
    out = []
    if os.environ.get("OLLAMA_MODELS"):
        out.append(os.environ["OLLAMA_MODELS"])
    out += [_home(".ollama", "models"), "/usr/share/ollama/.ollama/models", "/var/lib/ollama/.ollama/models"]
    return [d for d in out if os.path.isdir(os.path.join(d, "manifests"))]


def ollama_models(root):
    """[(name, blob path, size)] from an Ollama store. Ollama's blobs are plain GGUF files."""
    found = []
    for mf in glob.glob(os.path.join(root, "manifests", "**", "*"), recursive=True):
        if not os.path.isfile(mf):
            continue
        rel = os.path.relpath(mf, os.path.join(root, "manifests")).split(os.sep)
        try:
            with open(mf) as f:
                layers = (json.load(f) or {}).get("layers") or []
        except (OSError, ValueError):
            continue
        for layer in layers:
            if layer.get("mediaType") == "application/vnd.ollama.image.model":
                blob = os.path.join(root, "blobs", (layer.get("digest") or "").replace(":", "-"))
                if os.access(blob, os.R_OK):
                    tail = rel[-2:] if len(rel) >= 2 else rel
                    found.append((f"{tail[0]}-{tail[1]}" if len(tail) == 2 else tail[0], blob, os.stat(blob).st_size))
    return found


def link_dir(settings):
    return os.path.join(os.path.expanduser(settings["download_dir"]), "from-ollama")


def find(settings):
    """What is on this computer that Pumpkin does not know about yet."""
    known = [os.path.realpath(os.path.expanduser(d)) for d in settings["models_dirs"] or []]
    known.append(os.path.realpath(os.path.expanduser(settings["download_dir"])))
    out = []
    for ident, label, folder in _folders():
        if not os.path.isdir(folder):
            continue
        real = os.path.realpath(folder)
        if any(real == k or real.startswith(k + os.sep) for k in known):
            continue
        n, size = _count(folder)
        if n:
            out.append({"id": ident, "source": label, "path": folder, "display": _short(folder), "count": n,
                        "size_gb": round(size / GiB, 1)})
    for root in ollama_dirs():
        have = set(os.listdir(link_dir(settings))) if os.path.isdir(link_dir(settings)) else set()
        new = [m for m in ollama_models(root) if f"{m[0]}.gguf" not in have]
        if new:
            out.append({"id": "ollama:" + root, "source": "Ollama", "path": root, "display": _short(root),
                        "count": len(new), "size_gb": round(sum(m[2] for m in new) / GiB, 1)})
    return out


def add(settings, ids):
    """Add the chosen sources. Folders are scanned in place; Ollama models are linked, never copied."""
    found = {d["id"]: d for d in find(settings)}
    added, failed = [], []
    dirs = list(settings["models_dirs"] or [])
    for ident in ids:
        d = found.get(ident)
        if not d:
            continue
        if ident.startswith("ollama:"):
            target = link_dir(settings)
            os.makedirs(target, exist_ok=True)
            for name, blob, _ in ollama_models(d["path"]):
                link = os.path.join(target, f"{name}.gguf")
                if not os.path.lexists(link):
                    try:
                        os.symlink(blob, link)
                    except OSError:
                        # Windows allows symlinks only in Developer Mode; a hard link also takes no extra space
                        try:
                            os.link(blob, link)
                        except OSError:          # a hard link cannot cross drives: skip this one, keep going
                            failed.append(name)
                            continue
        elif d["path"] not in dirs:
            dirs.append(d["path"])
        added.append(d["source"])
    settings.update({"models_dirs": dirs})
    if failed:
        raise RuntimeError(f"Could not link {', '.join(failed[:5])} from Ollama: the download folder is on another "
                           f"drive than Ollama's models. Turn on Developer Mode in Windows (it allows links), or "
                           f"pick a download folder on the same drive in Settings.")
    return added
