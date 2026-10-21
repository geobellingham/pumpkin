"""One-time import from the earlier 'LLM Studio' script (~/.config/llm-studio)."""
import json
import os
import shutil

from . import config

OLD = os.path.join(config.HOME, ".config", "llm-studio")


def _load(name):
    try:
        with open(os.path.join(OLD, f"{name}.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def available():
    return os.path.isdir(OLD) and not os.path.exists(os.path.join(config.CONFIG_DIR, "presets.json"))


def run():
    """Copies presets, personas, tasks, prompts and settings. Returns a short list of what was imported."""
    done = []
    config.ensure_dirs()
    settings = config.load("settings", {})
    old_s = _load("settings") or {}
    for k in ("threads", "tool_chars", "max_steps", "sandbox_image", "persona"):
        if k in old_s:
            settings[k] = old_s[k]
    dirs = list(settings.get("models_dirs") or config.DEFAULT_SETTINGS["models_dirs"])
    if os.path.isdir("/data/models") and "/data/models" not in dirs:
        dirs.insert(0, "/data/models")
        settings["download_dir"] = "/data/models"
    settings["models_dirs"] = dirs
    old_reports = os.path.join(config.HOME, "llm-reports")
    if os.path.isdir(old_reports):
        settings["reports_dir"] = old_reports
    config.save("settings", settings)
    done.append("settings")

    presets = _load("presets")
    if presets:
        out = []
        for p in presets:
            extra = p.get("extra") or []
            out.append({"id": config.new_id(), "name": p.get("name", "Preset"), "desc": p.get("desc", ""),
                        "model": p.get("model", ""), "mmproj": p.get("mmproj", ""), "ctx": p.get("ctx", 32768),
                        "kv": p.get("kv", "q8_0"), "fitt": p.get("fitt", 1024),
                        "think_budget": p.get("think_budget"), "tools": bool(p.get("agent")), "sandbox": True,
                        "web": bool(p.get("agent")), "extra": " ".join(extra) if isinstance(extra, list) else extra})
        config.save("presets", out)
        done.append(f"{len(out)} presets")
    for name in ("personas", "prompts"):
        items = _load(name)
        if items:
            for it in items:
                it.setdefault("id", config.new_id())
            config.save(name, items)
            done.append(f"{len(items)} {name}")
    tasks = _load("tasks")
    if tasks:
        names = {p["name"]: p["id"] for p in (config.load("presets", []) or [])}
        for t in tasks:
            t.setdefault("id", config.new_id())
            t["preset"] = names.get(t.get("preset"), t.get("preset", ""))
        config.save("tasks", tasks)
        done.append(f"{len(tasks)} tasks")
    old_mcp = os.path.join(config.HOME, "mcp.json")
    if os.path.exists(old_mcp) and not os.path.exists(os.path.join(config.CONFIG_DIR, "mcp.json")):
        shutil.copy2(old_mcp, os.path.join(config.CONFIG_DIR, "mcp.json"))
        done.append("MCP servers")
    old_bench = os.path.join(config.HOME, "llm-bench.csv")
    new_bench = os.path.join(config.DATA_DIR, "speed.csv")
    if os.path.exists(old_bench) and not os.path.exists(new_bench):
        os.makedirs(config.DATA_DIR, exist_ok=True)
        shutil.copy2(old_bench, new_bench)
        done.append("speed history")
    return done
