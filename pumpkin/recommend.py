"""Model recommendations for this computer, kept fresh from Hugging Face.

Two routes:
  gpu  - models that run entirely in video memory at a good quant (fastest)
  moe  - mixture-of-experts models too big for the GPU that still run well with part of them in RAM,
         because only a few billion parameters are active per token

Candidates come from the most downloaded and trending GGUF repos (refreshed daily). Each one is sized
from its parameter count, matched against this machine's memory, then ranked on how capable it is
for the hardware, popularity, freshness, tool-calling support and the reputation of the uploader.
The hand-picked catalog is merged in and marked as tested.
"""
import json
import math
import os
import re
import time
import urllib.parse

from . import config, models

CACHE = os.path.join(config.STATE_DIR, "hf-candidates.json")
MAX_AGE = 24 * 3600
GiB = 2 ** 30

# bits per weight of common quants (llama.cpp averages, rounded)
BPW = {"Q3_K_M": 3.91, "IQ4_XS": 4.25, "Q4_K_M": 4.85, "Q5_K_M": 5.69, "Q6_K": 6.56, "Q8_0": 8.5}
GPU_QUANTS = ["Q6_K", "Q5_K_M", "Q4_K_M"]          # tried best first
BIG_FALLBACK = ["IQ4_XS", "Q3_K_M"]                # only for models of 20B+, where Q3 still beats a smaller model
MOE_QUANTS = ["Q6_K", "Q5_K_M", "Q4_K_M", "IQ4_XS"]
QUANT_BONUS = {"Q6_K": 0.6, "Q5_K_M": 0.4, "Q4_K_M": 0.0, "IQ4_XS": -0.4, "Q3_K_M": -1.2}

TRUSTED = {"unsloth": 1.6, "bartowski": 1.4, "ggml-org": 1.6, "lmstudio-community": 1.2, "qwen": 1.5,
           "google": 1.5, "mistralai": 1.4, "microsoft": 1.2, "ibm-granite": 1.0, "nvidia": 1.0, "openai": 1.4,
           "meta-llama": 1.2, "deepseek-ai": 1.2, "zai-org": 1.0, "liquidai": 1.0, "mradermacher": 0.3}
NOT_CHAT = re.compile(r"embed|rerank|bert|whisper|tts|speech|vae|diffusion|flux|wan2|sdxl|stable-diffusion|"
                      r"qwen[-_]?image|clip|siglip|ocr-only|reward|classifier|draft|mtp", re.I)
NICHE = re.compile(r"uncensor|abliterat|roleplay|\brp\b|nsfw|erotic|merge|frankenstein|test|experimental|"
                   r"ternary|1\.58bit|bitnet", re.I)
MOE_ARCH = re.compile(r"moe|gpt-oss|gptoss|deepseek|mixtral|llama4|dbrx|jamba|granitehybrid|minimax|glm4moe|"
                      r"ernie4_5_moe|hunyuan|arctic", re.I)
NAME_MOE = re.compile(r"(?:^|[-_.])moe(?:[-_.]|$)|gpt-?oss|mixtral|llama-?4-(?:scout|maverick)", re.I)
ACTIVE = re.compile(r"[-_.]A(\d+(?:\.\d+)?)B\b", re.I)
# well-known model families that do well across chat, knowledge and tools; anything else can still win on popularity
FAMILY = [(re.compile(r"qwen", re.I), 1.5), (re.compile(r"gemma", re.I), 1.0), (re.compile(r"gpt-?oss", re.I), 1.0),
          (re.compile(r"glm", re.I), 0.8), (re.compile(r"deepseek", re.I), 0.8), (re.compile(r"mistral|devstral|magistral", re.I), 0.6),
          (re.compile(r"llama", re.I), 0.5), (re.compile(r"granite|phi-?\d", re.I), 0.3)]


# ---------------------------------------------------------------- fetching
def _query(params, token):
    q = [("filter", "gguf"), ("limit", str(params.pop("limit", 100)))]
    for k, v in params.items():
        q.append((k, v))
    for e in ("gguf", "downloads", "likes", "lastModified", "trendingScore", "gated", "pipeline_tag"):
        q.append(("expand[]", e))
    return models._hf_get("/api/models?" + urllib.parse.urlencode(q), token, timeout=45)


def fetch(token=None, force=False):
    """Candidate repos with their GGUF summaries, cached for a day."""
    try:
        with open(CACHE) as f:
            cached = json.load(f)
        if not force and time.time() - cached.get("at", 0) < MAX_AGE and cached.get("items"):
            return cached
    except (OSError, ValueError):
        cached = None
    items, seen = [], set()
    queries = [
        {"sort": "downloads", "pipeline_tag": "text-generation", "limit": 300},
        {"sort": "trendingScore", "pipeline_tag": "text-generation", "limit": 150},
        {"sort": "downloads", "pipeline_tag": "image-text-to-text", "limit": 80},
        {"sort": "trendingScore", "pipeline_tag": "image-text-to-text", "limit": 40},
    ]
    errors = []
    for q in queries:
        try:
            for it in _query(dict(q), token):
                rid = it.get("id") or it.get("modelId")
                if rid and rid not in seen:
                    seen.add(rid)
                    it.setdefault("pipeline_tag", q["pipeline_tag"])
                    items.append(it)
        except Exception as e:  # noqa: BLE001 - one failing query should not lose the others
            errors.append(str(e))
    if not items:
        if cached and cached.get("items"):
            cached["stale"] = True
            return cached
        raise RuntimeError("Could not reach Hugging Face: " + (errors[0] if errors else "no results"))
    out = {"at": time.time(), "items": items}
    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        with open(CACHE, "w") as f:
            json.dump(out, f)
    except OSError:
        pass
    return out


# ---------------------------------------------------------------- understanding a repo
def describe(it):
    """Turns one Hugging Face entry into what the ranking needs, or None if it is not a chat model."""
    rid = it.get("id") or it.get("modelId") or ""
    g = it.get("gguf") or {}
    params = g.get("total") or 0
    arch = (g.get("architecture") or "").lower()
    if "/" not in rid or not params or params < 0.5e9 or it.get("gated") not in (None, False, "false"):
        return None
    if not g.get("chat_template") or NOT_CHAT.search(rid) or NOT_CHAT.search(arch) or arch in ("clip", "t5"):
        return None
    org, name = rid.split("/", 1)
    tmpl = g.get("chat_template") or ""
    # the architecture says it best; by name only clear signs count ("A3B", "MoE", gpt-oss, Mixtral), because
    # "DeepSeek-R1-Distill-Qwen-14B" and friends are ordinary dense models with a famous name in front
    moe = bool(MOE_ARCH.search(arch) or ACTIVE.search(name) or NAME_MOE.search(name))
    m = ACTIVE.search(name)
    active = it.get("_active") or (float(m.group(1)) if m else (None if not moe else _guess_active(arch, params)))
    updated = it.get("lastModified")
    age_days = None
    if updated:
        try:
            age_days = (time.time() - time.mktime(time.strptime(updated[:19], "%Y-%m-%dT%H:%M:%S"))) / 86400
        except ValueError:
            pass
    return {
        "id": "hf:" + rid, "repo": rid, "org": org, "name": _pretty(name), "key": _family_key(name),
        "params_b": params / 1e9, "active_b": active, "moe": moe, "arch": arch,
        "ctx_train": g.get("context_length"), "tools": "tools" in tmpl or "tool_call" in tmpl,
        "thinking": "<think>" in tmpl or "enable_thinking" in tmpl or "reasoning" in tmpl,
        "vision": it.get("pipeline_tag") == "image-text-to-text",
        "downloads": it.get("downloads") or 0, "likes": it.get("likes") or 0,
        "trending": it.get("trendingScore") or 0, "age_days": age_days, "niche": bool(NICHE.search(name)),
    }


def _guess_active(arch, params):
    if "gpt-oss" in arch or "gptoss" in arch:
        return 3.6 if params < 40e9 else 5.1
    return None


def _pretty(name):
    n = re.sub(r"[-_.]?gguf$", "", name, flags=re.I)
    return n.replace("_", " ").replace("-", " ").strip()


def _family_key(name):
    n = name.lower()
    n = re.sub(r"^[a-z0-9]+_(?=[a-z])", "", n)          # bartowski style "Qwen_Qwen3.6-..." -> "qwen3.6-..."
    n = re.sub(r"[-_.]?gguf$", "", n)
    n = re.sub(r"[-_.](i1|imatrix|q\d.*|ud.*)$", "", n)
    n = re.sub(r"[-_.]", "", n)
    return n


# ---------------------------------------------------------------- fitting and ranking
def size_gb(params_b, quant):
    return params_b * 1e9 * BPW[quant] / 8 / GiB


def plan_route(d, hw):
    """Which route a model belongs to on this machine, and at which quant. None if it does not fit."""
    hint_dense = {"experts": 0}
    hint_moe = {"experts": 8}
    # a MoE model is better at Q5 with some of it in RAM than squeezed to Q3 on the GPU
    gpu_list = GPU_QUANTS + (BIG_FALLBACK if d["params_b"] >= 20 and not d["moe"] else [])
    for q in gpu_list:
        s = size_gb(d["params_b"], q)
        f = models.fit(s, hint_moe if d["moe"] else hint_dense, 16384, "q8_0", hw)
        if f["level"] == "gpu":
            return {"route": "gpu", "quant": q, "size_gb": round(s, 1), "fit": f}
    if not d["moe"]:
        return slow_route(d, hw)
    for q in MOE_QUANTS:
        s = size_gb(d["params_b"], q)
        f = models.fit(s, hint_moe, 32768, "q8_0", hw)
        if f["level"] == "moe":
            vram = max(0.1, (hw.get("vram_total") or 0) - 0.8)
            in_ram = max(0.0, min(1.0, (f["need_gb"] - vram) / f["need_gb"]))
            return {"route": "moe", "quant": q, "size_gb": round(s, 1), "fit": f, "ram_share": round(in_ram, 2)}
    return slow_route(d, hw)


SLOW_QUANTS = ["Q4_K_M", "IQ4_XS", "Q3_K_M"]
WEAK_GPU_GB = 4          # below this a graphics card holds only the tiniest models, so the RAM still matters


def slow_route(d, hw, tight=False):
    """No graphics card for AI (or a built-in one that shares the RAM): a model that fits in RAM still works on the
    processor, just slowly. Only offered there, and only up to a size that answers at a bearable pace."""
    if hw.get("unified") or (hw.get("vram_total") or 0) >= WEAK_GPU_GB:
        return None
    for q in SLOW_QUANTS:
        s = size_gb(d["params_b"], q)
        if s > 9:                                      # past this the processor manages about a word a second
            continue
        f = models.fit(s, {"experts": 8} if d["moe"] else {"experts": 0}, 4096 if tight else 8192, "q8_0", hw)
        if tight and f["level"] == "no" and f["need_gb"] <= (hw.get("ram_total") or 0) * 0.6:
            f["level"] = "tight"                       # last resort on a small computer: close other programs first
        if f["level"] in (("split", "moe", "tight") if tight else ("split", "moe")):
            return {"route": "slow", "quant": q, "size_gb": round(s, 1), "fit": f, "ram_share": 1.0}
    return None


def score(d, plan):
    s = 3.0 * math.log2(max(1.0, d["params_b"]))                 # bigger models answer better
    if plan["route"] == "slow":
        # on the processor every gigabyte costs speed: small models first, but not so small they are useless
        active = d["active_b"] or d["params_b"]
        s = 3.0 * math.log2(max(1.0, d["params_b"])) - max(0.0, plan["size_gb"] - 2.5) * 1.6 - max(0.0, active - 4) * 0.8
    elif plan["route"] == "moe":
        s = 3.0 * math.log2(max(1.0, d["params_b"])) * 0.85
        if d["active_b"]:
            s -= max(0.0, d["active_b"] - 6) * 0.35              # many active params are slow from RAM
        s -= plan.get("ram_share", 0) * 2.0
    s += QUANT_BONUS.get(plan["quant"], 0)
    # what most people download, from the uploaders most people trust, comes first
    s += 2.0 * math.log10(d["downloads"] + 10) + 0.8 * math.log10(d["trending"] + 1)
    s += 0.5 * math.log10(d["likes"] + 1)
    if d["age_days"] is not None:
        s += 2.0 if d["age_days"] < 90 else 1.0 if d["age_days"] < 240 else -1.0 if d["age_days"] > 540 else 0
    s += 2.0 if d["tools"] else 0
    s += TRUSTED.get(d["org"].lower(), -2.0)
    s += next((b for pat, b in FAMILY if pat.search(d["repo"])), 0.0)
    s -= 2.5 if d["niche"] else 0
    return round(s, 2)


def blurb(d, plan):
    parts = []
    if d["moe"]:
        act = f", {d['active_b']:g}B active" if d["active_b"] else ""
        parts.append(f"{d['params_b']:.0f}B mixture-of-experts{act}")
    else:
        parts.append(f"{d['params_b']:.0f}B" if d["params_b"] >= 1.5 else f"{d['params_b']:.1f}B")
    if d["tools"]:
        parts.append("tool calling")
    if d["thinking"]:
        parts.append("thinks before answering")
    if d["vision"]:
        parts.append("understands images")
    if plan["route"] == "moe":
        parts.append(f"about {round(plan['ram_share'] * 100)}% in RAM")
    elif plan["route"] == "slow":
        parts.append("runs on the processor")
    text = ", ".join(parts)
    return text[:1].upper() + text[1:]


def recommend(items, hw, local_files=(), curated=(), per_route=8):
    """Ranks candidates into the two routes."""
    best = {}
    for it in items:
        d = describe(it)
        if not d:
            continue
        plan = plan_route(d, hw)
        if not plan:
            continue
        if d["age_days"] is not None and d["age_days"] > 720 and d["repo"].lower() not in {
                c["repo"].lower() for c in curated}:
            continue                                     # two years is ancient for local models
        d.update(plan)
        d["score"] = score(d, plan)
        k = (plan["route"], d["key"])
        if k not in best or d["score"] > best[k]["score"]:
            best[k] = d
    picks = {c["repo"].lower(): c for c in curated}
    if not any(r == "slow" or r == "gpu" for r, _ in best) and (hw.get("vram_total") or 0) < WEAK_GPU_GB \
            and not hw.get("unified"):
        # nothing runs comfortably: offer the smallest models that run at all, even if barely
        for it in items:
            d = describe(it)
            if d:
                plan = slow_route(d, hw, tight=True)
                if plan:
                    d.update(plan)
                    d["score"] = -d["params_b"]
                    best[("slow", d["key"])] = d
    out = {"gpu": [], "moe": [], "slow": []}
    for d in best.values():
        c = picks.get(d["repo"].lower())
        if c:
            d["tested"] = True
            d["name"] = c["name"]
            d["score"] += 3.0
        d["blurb"] = blurb(d, d)
        d["fit"]["text"] = models.fit_label(d["fit"])
        d["installed"] = any(d["key"] and d["key"] in f for f in local_files)
        d["ctx"] = 32768 if d["route"] == "moe" else 8192 if d["route"] == "slow" else 16384
        out[d["route"]].append(d)
    for r in out:
        out[r].sort(key=lambda x: -x["score"])
        # keep variety: at most two picks from one family name stem (e.g. many Qwen3 sizes)
        seen, keep = {}, []
        for d in out[r]:
            stem = re.sub(r"\d.*$", "", d["key"])[:8] or d["key"]
            if seen.get(stem, 0) >= 2:
                continue
            seen[stem] = seen.get(stem, 0) + 1
            keep.append(d)
        out[r] = keep[:per_route]
    return out


def curated_items(catalog):
    """The hand-picked catalog in the same shape as Hugging Face results (used as a fallback too)."""
    out = []
    for c in catalog:
        if not c.get("params_b"):
            continue
        arch = "moe" if c.get("moe") else "dense"
        out.append({"id": c["repo"], "downloads": 0, "trendingScore": 0, "likes": 0,
                    "pipeline_tag": "image-text-to-text" if c.get("vision") else "text-generation",
                    "gguf": {"total": c["params_b"] * 1e9, "architecture": arch, "context_length": c.get("ctx"),
                             "chat_template": "tools" if c.get("tools") else "chat"},
                    "_active": c.get("active_b")})
    return out
