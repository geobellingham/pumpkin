"""The model library: GGUF files on disk, whether they fit, Hugging Face search and downloads."""
import fnmatch
import glob
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config, gguf, hardware

GiB = 2 ** 30
HF = os.environ.get("PUMPKIN_HF_URL", "https://huggingface.co").rstrip("/")
SHARD = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$")
KV_BYTES = {"f16": 2.0, "bf16": 2.0, "q8_0": 34 / 32, "q5_1": 24 / 32, "q5_0": 22 / 32, "q4_1": 20 / 32,
            "q4_0": 18 / 32, "iq4_nl": 18 / 32}
QUANT_RE = re.compile(r"(UD-)?(I?Q\d(?:_[0-9A-Z]+)*|BF16|F16|F32|MXFP4(?:_MOE)?)", re.I)


def quant_from_name(name):
    base = SHARD.sub(".gguf", os.path.basename(name))
    hits = QUANT_RE.findall(base.replace(".gguf", ""))
    if not hits:
        return None
    ud, q = hits[-1]
    return (ud + q).upper().replace("UD-", "UD-")


# ---------------------------------------------------------------- memory estimates
def kv_cache_gb(info, ctx, kv_type="f16"):
    """Approximate KV cache size for a context length."""
    if not info or not info.get("attn_layers") or not info.get("kv_heads"):
        return None
    per = KV_BYTES.get((kv_type or "f16").lower(), 2.0)
    k = info.get("key_length") or 128
    v = info.get("value_length") or k
    tokens = ctx
    sw = info.get("sliding_window")
    if sw and info.get("arch", "").startswith(("gemma", "gpt-oss")):
        # roughly half the layers only keep the window
        tokens = (ctx + min(ctx, sw)) / 2
    return info["attn_layers"] * info["kv_heads"] * (k + v) * per * tokens / GiB


def fit(model_gb, info, ctx, kv_type, hw):
    """How well a model runs on this machine at this context.

    Returns level: 'gpu' (all in VRAM), 'split' (spills into RAM, slower), 'moe' (spills but it is a
    mixture-of-experts model, which llama.cpp handles well), 'tight', or 'no'."""
    vram = hw.get("vram_total") or 0
    ram = hw.get("ram_total") or 0
    kv = kv_cache_gb(info, ctx, kv_type)
    moe = bool(info and (info.get("experts") or 0) > 1)
    if kv is None:
        # no metadata yet (a file not downloaded): typical sizes per token at q8_0
        per_token = 0.00003 if moe else 0.00008
        kv = ctx * per_token * (2 if (kv_type or "f16") in ("f16", "bf16") else 1)
    overhead = 0.5 + 0.02 * model_gb                       # compute buffers and runtime
    need = model_gb + kv + overhead
    usable_vram = max(0.0, vram - 0.8)                     # desktop and driver
    usable_ram = max(0.0, ram * 0.75 - 4)                  # leave room for the system
    if vram and need <= usable_vram:
        level = "gpu"
    elif need <= usable_vram + usable_ram:
        level = "moe" if moe else "split"
        if need > usable_vram + usable_ram * 0.8:
            level = "tight"
    else:
        level = "no"
    return {"level": level, "need_gb": round(need, 1), "kv_gb": round(kv, 2), "vram_gb": round(vram, 1),
            "ram_gb": round(ram, 1), "moe": moe}


FIT_TEXT = {
    "gpu": "Fits in your GPU",
    "moe": "Fits with RAM offload (MoE, still fast)",
    "split": "Spills into RAM, noticeably slower",
    "tight": "Barely fits, may fail",
    "no": "Too big for this machine",
}


def best_ctx(model_gb, info, kv_type, hw, cap=None):
    """Largest common context size that still keeps the model on the GPU (or at least fitting)."""
    train = (info or {}).get("ctx_train") or 32768
    limit = min(train, cap or train)
    sizes = [s for s in (4096, 8192, 16384, 32768, 65536, 131072, 196608, 262144) if s <= limit] or [limit]
    best = sizes[0]
    for s in sizes:
        lv = fit(model_gb, info, s, kv_type, hw)["level"]
        if lv == "gpu" or (lv == "moe" and fit(model_gb, info, sizes[0], kv_type, hw)["level"] == "moe"):
            best = s
    return best


# ---------------------------------------------------------------- local files
class Library:
    def __init__(self, settings):
        self.settings = settings
        self.cache_path = os.path.join(config.STATE_DIR, "gguf-cache.json")
        try:
            with open(self.cache_path) as f:
                self.cache = json.load(f)
        except (OSError, ValueError):
            self.cache = {}
        self.lock = threading.Lock()
        self._models = None
        self._scanned = 0

    def dirs(self):
        out = []
        for d in self.settings["models_dirs"] or []:
            d = os.path.expanduser(d)
            if d not in out:
                out.append(d)
        dl = os.path.expanduser(self.settings["download_dir"])
        if dl not in out:
            out.append(dl)
        return out

    def _info(self, path, size, mtime):
        key = f"{path}|{size}|{int(mtime)}"
        if key in self.cache:
            return self.cache[key]
        try:
            info = gguf.summarize(gguf.read_metadata(path))
        except Exception as e:  # noqa: BLE001 - one bad file must not break the library
            info = {"error": str(e)[:200]}
        self.cache[key] = info
        return info

    def scan(self, force=False):
        with self.lock:
            if self._models is not None and not force and time.time() - self._scanned < 20:
                return self._models
            groups = {}
            for d in self.dirs():
                if not os.path.isdir(d):
                    continue
                for p in glob.glob(os.path.join(d, "**", "*.gguf"), recursive=True):
                    rel = os.path.relpath(p, d)
                    if any(seg.startswith(".") for seg in rel.split(os.sep)) or p.endswith(".part"):
                        continue
                    m = SHARD.search(p)
                    key = SHARD.sub(".gguf", p) if m else p
                    groups.setdefault(key, []).append(p)
            models = []
            for key, parts in groups.items():
                parts.sort()
                first = parts[0]
                try:
                    st = [os.stat(p) for p in parts]
                except OSError:
                    continue
                size = sum(s.st_size for s in st)
                m = SHARD.search(first)
                complete = not m or len(parts) == int(m.group(2))
                info = self._info(first, st[0].st_size, st[0].st_mtime)
                name = os.path.basename(key)
                models.append({
                    "id": first, "path": first, "file": name, "folder": os.path.dirname(first), "parts": len(parts),
                    "complete": complete, "size_gb": round(size / GiB, 2), "mtime": int(max(s.st_mtime for s in st)),
                    "quant": quant_from_name(name) or info.get("quant"), "info": info,
                    "kind": "mmproj" if info.get("mmproj") or "mmproj" in name.lower() else "model",
                })
            # pair vision projectors with models in the same folder
            for mdl in models:
                if mdl["kind"] != "model":
                    continue
                projs = [x for x in models if x["kind"] == "mmproj" and x["folder"] == mdl["folder"]]
                mdl["mmproj"] = sorted(projs, key=lambda x: ("f16" not in x["file"].lower(), x["file"]))[0]["path"] \
                    if projs else None
            models.sort(key=lambda x: (x["kind"] != "model", x["file"].lower()))
            self._models, self._scanned = models, time.time()
            try:
                os.makedirs(config.STATE_DIR, exist_ok=True)
                with open(self.cache_path, "w") as f:
                    json.dump(self.cache, f)
            except OSError:
                pass
            return models

    def resolve(self, ref):
        """A preset's model reference (full path, file name or glob pattern) -> library entry."""
        if not ref:
            return None
        models = self.scan()
        for m in models:
            if m["path"] == ref:
                return m
        if os.path.isabs(ref) and os.path.isfile(ref):
            st = os.stat(ref)
            return {"path": ref, "file": os.path.basename(ref), "size_gb": st.st_size / GiB,
                    "info": self._info(ref, st.st_size, st.st_mtime), "kind": "model"}
        base = os.path.basename(ref)
        for m in models:
            if m["file"] == base or os.path.basename(m["path"]) == base:
                return m
        for m in models:
            if fnmatch.fnmatch(m["file"], base) or fnmatch.fnmatch(os.path.basename(m["path"]), base):
                return m
        return None

    def delete(self, path):
        m = next((x for x in self.scan(force=True) if x["path"] == path), None)
        if not m:
            raise RuntimeError("That file is not in the library.")
        if not any(os.path.realpath(path).startswith(os.path.realpath(d) + os.sep) for d in self.dirs()):
            raise RuntimeError("Pumpkin only deletes files inside your model folders.")
        key = SHARD.sub(".gguf", path) if SHARD.search(path) else None
        parts = [p for p in glob.glob(os.path.join(m["folder"], "*.gguf"))
                 if (SHARD.sub(".gguf", p) == key if key else p == path)]
        for p in parts:
            os.remove(p)
        self.scan(force=True)
        return len(parts)


def annotate(models, hw, ctx=None, kv_type="q8_0"):
    """Adds a fit estimate to each model entry."""
    out = []
    for m in models:
        m = dict(m)
        if m["kind"] == "model":
            info = m.get("info") or {}
            c = ctx or best_ctx(m["size_gb"], info, kv_type, hw)
            m["fit"] = fit(m["size_gb"], info, c, kv_type, hw)
            m["fit"]["ctx"] = c
            m["fit"]["text"] = FIT_TEXT[m["fit"]["level"]]
        out.append(m)
    return out


# ---------------------------------------------------------------- Hugging Face
def _hf_get(path, token=None, timeout=30):
    headers = {"User-Agent": "pumpkin"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(HF + path, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def hf_search(query, token=None, limit=30):
    q = urllib.parse.urlencode({"search": query, "filter": "gguf", "sort": "downloads", "direction": "-1",
                                "limit": limit})
    items = _hf_get(f"/api/models?{q}", token)
    return [{"repo": it.get("id") or it.get("modelId"), "downloads": it.get("downloads", 0),
             "likes": it.get("likes", 0), "updated": it.get("lastModified"), "gated": it.get("gated", False)}
            for it in items if it.get("id") or it.get("modelId")]


def looks_moe(name):
    """Mixture-of-experts models usually say so in the name (35B-A3B, MoE, gpt-oss)."""
    return bool(re.search(r"[-_.]A\d+(\.\d+)?B\b|MoE|gpt-oss|Mixtral", name, re.I))


def hf_files(repo, hw, token=None, kv_type="q8_0"):
    """GGUF files of a repo grouped into downloadable choices, each with a fit estimate."""
    tree = _hf_get(f"/api/models/{urllib.parse.quote(repo, safe='/')}/tree/main?recursive=1", token)
    files = [t for t in tree if t.get("type") == "file" and t.get("path", "").endswith(".gguf")]
    groups = {}
    for f in files:
        p = f["path"]
        key = SHARD.sub(".gguf", p) if SHARD.search(p) else p
        groups.setdefault(key, []).append(f)
    choices = []
    for key, parts in groups.items():
        size = sum((f.get("lfs") or {}).get("size") or f.get("size") or 0 for f in parts) / GiB
        name = os.path.basename(key)
        kind = "mmproj" if "mmproj" in name.lower() else "model"
        c = {"name": name, "files": sorted(f["path"] for f in parts), "size_gb": round(size, 2), "kind": kind,
             "quant": quant_from_name(name)}
        if kind == "model":
            # without the file we cannot read the metadata; estimate the context from size only
            fe = fit(size, {"experts": 8} if looks_moe(repo) else None, 8192, kv_type, hw)
            fe["text"] = FIT_TEXT[fe["level"]]
            c["fit"] = fe
        choices.append(c)
    choices.sort(key=lambda c: (c["kind"] != "model", c["size_gb"]))
    return choices


class Downloads:
    """Resumable downloads from Hugging Face, one at a time, in the background."""

    def __init__(self, settings, library):
        self.settings, self.library = settings, library
        self.jobs = []
        self.lock = threading.Lock()
        self.wake = threading.Event()
        threading.Thread(target=self._worker, daemon=True).start()

    def add(self, repo, files, folder=None):
        dest = os.path.join(os.path.expanduser(self.settings["download_dir"]),
                            folder or config.slug(repo.split("/")[-1]))
        with self.lock:
            job = {"id": config.new_id(), "repo": repo, "files": list(files), "dest": dest, "state": "queued",
                   "done": 0, "total": 0, "speed": 0, "error": None, "current": None, "added": time.time()}
            self.jobs.append(job)
        self.wake.set()
        return job

    def cancel(self, ident):
        with self.lock:
            for j in self.jobs:
                if j["id"] == ident and j["state"] in ("queued", "downloading"):
                    j["state"] = "cancelled"
                    return True
        return False

    def clear_finished(self):
        with self.lock:
            self.jobs = [j for j in self.jobs if j["state"] in ("queued", "downloading")]

    def list(self):
        with self.lock:
            return [dict(j) for j in self.jobs]

    def _worker(self):
        while True:
            self.wake.wait(5)
            self.wake.clear()
            while True:
                with self.lock:
                    job = next((j for j in self.jobs if j["state"] == "queued"), None)
                    if job:
                        job["state"] = "downloading"
                if not job:
                    break
                try:
                    self._run(job)
                    if job["state"] == "downloading":
                        job["state"] = "done"
                    self.library.scan(force=True)
                except Exception as e:  # noqa: BLE001
                    if job["state"] != "cancelled":
                        job["state"], job["error"] = "error", _friendly(e)

    def _run(self, job):
        token = self.settings["hf_token"] or None
        os.makedirs(job["dest"], exist_ok=True)
        sizes = {}
        for f in job["files"]:
            sizes[f] = self._size(job["repo"], f, token)
        job["total"] = sum(sizes.values())
        done_before = 0
        for f in job["files"]:
            if job["state"] == "cancelled":
                return
            job["current"] = os.path.basename(f)
            target = os.path.join(job["dest"], os.path.basename(f))
            if os.path.exists(target) and os.path.getsize(target) == sizes[f]:
                done_before += sizes[f]
                job["done"] = done_before
                continue
            self._file(job, f, target, sizes[f], token, done_before)
            done_before += sizes[f]

    def _size(self, repo, f, token):
        url = f"{HF}/{repo}/resolve/main/{urllib.parse.quote(f)}"
        headers = {"User-Agent": "pumpkin"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            return int(r.headers.get("Content-Length") or r.headers.get("X-Linked-Size") or 0)

    def _file(self, job, f, target, size, token, done_before):
        part = target + ".part"
        have = os.path.getsize(part) if os.path.exists(part) else 0
        url = f"{HF}/{job['repo']}/resolve/main/{urllib.parse.quote(f)}"
        headers = {"User-Agent": "pumpkin"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if have:
            headers["Range"] = f"bytes={have}-"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            if have and r.status != 206:
                have = 0                                       # server ignored the range: start over
            mode = "ab" if have else "wb"
            t0, last_bytes, last_t = time.time(), have, time.time()
            with open(part, mode) as out:
                got = have
                while True:
                    if job["state"] == "cancelled":
                        return
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    job["done"] = done_before + got
                    now = time.time()
                    if now - last_t >= 1:
                        job["speed"] = (got - last_bytes) / (now - last_t)
                        last_bytes, last_t = got, now
        if size and os.path.getsize(part) < size:
            raise RuntimeError("The download stopped early. Retry to resume it.")
        os.replace(part, target)


def _friendly(e):
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403):
            return "Hugging Face refused the download. This model may need you to accept its license on the " \
                   "website and add a Hugging Face token in Settings."
        if e.code == 404:
            return "That file is no longer on Hugging Face."
        return f"Hugging Face answered HTTP {e.code}."
    if isinstance(e, urllib.error.URLError):
        return f"Could not reach Hugging Face ({e.reason})."
    if isinstance(e, OSError) and e.errno == 28:
        return "The disk is full."
    return str(e)


def suggested_searches(hw):
    """Search terms that make sense for this machine's memory (no hardcoded model list to go stale)."""
    vram = hw.get("vram_total") or 0
    ram = hw.get("ram_total") or 0
    total = vram + ram * 0.6
    out = []
    if vram >= 20 or total >= 40:
        out += ["Qwen3.6-35B-A3B GGUF", "27B GGUF", "32B GGUF"]
    if vram >= 10:
        out += ["14B GGUF", "Qwen3.8-27B GGUF"]
    out += ["8B GGUF", "4B GGUF"]
    seen = []
    for s in out:
        if s not in seen:
            seen.append(s)
    return seen[:5]


# ---------------------------------------------------------------- one-click catalog
CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json")
QUALITY = ["IQ2_XXS", "IQ2_XS", "IQ2_S", "IQ2_M", "Q2_K", "Q2_K_XL", "IQ3_XXS", "IQ3_XS", "Q3_K_S", "IQ3_S",
           "IQ3_M", "Q3_K_M", "Q3_K_L", "Q3_K_XL", "IQ4_XS", "IQ4_NL", "Q4_0", "Q4_K_S", "Q4_1", "Q4_K_M", "MXFP4",
           "Q4_K_XL", "Q5_K_S", "Q5_K_M", "Q5_K_XL", "Q6_K", "Q6_K_XL", "Q8_0", "Q8_K_XL", "BF16", "F16"]


def catalog():
    try:
        with open(CATALOG_PATH) as f:
            return json.load(f).get("models", [])
    except (OSError, ValueError):
        return []


def quant_rank(q):
    q = (q or "").upper().replace("UD-", "")
    for i, name in enumerate(QUALITY):
        if q == name:
            return i
    for i, name in enumerate(QUALITY):
        if q.startswith(name):
            return i
    return -1


def pick_quant(choices, hw, ctx, moe=False, kv="q8_0"):
    """The best-quality quant that runs well here. Above Q6 the quality gain is not worth the memory."""
    best_q = quant_rank("Q6_K")
    models = [c for c in choices if c["kind"] == "model" and 0 <= quant_rank(c.get("quant")) <= best_q + 1]
    if not models:
        models = [c for c in choices if c["kind"] == "model"]
    if not models:
        return None, None
    scored = []
    hint = {"experts": 8} if moe else None
    for c in models:
        f = fit(c["size_gb"], hint, ctx, kv, hw)
        scored.append((c, f))
    for want in (("gpu",), ("moe",) if moe else (), ("split", "tight")):
        ok = [(c, f) for c, f in scored if f["level"] in want]
        if not ok:
            continue
        if want == ("gpu",) or want == ("moe",):
            # highest quality that fits; prefer unsloth dynamic (UD) builds on ties
            ok.sort(key=lambda cf: (quant_rank(cf[0]["quant"]), "UD-" in (cf[0]["quant"] or "")))
            pick = ok[-1]
            # do not go below Q3 when a larger option would at least run
            return pick
        ok.sort(key=lambda cf: cf[0]["size_gb"])
        return ok[0]                                   # smallest that runs at all
    return None, None


def pick_mmproj(choices):
    projs = [c for c in choices if c["kind"] == "mmproj"]
    if not projs:
        return None
    projs.sort(key=lambda c: (0 if "F16" in c["name"].upper() and "BF16" not in c["name"].upper() else 1,
                              c["size_gb"]))
    return projs[0]
