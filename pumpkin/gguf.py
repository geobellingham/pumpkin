"""Reads the metadata at the start of a .gguf file (no dependencies, stops before the tokenizer)."""
import struct

TYPES = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
SIZES = {k: struct.calcsize(v) for k, v in TYPES.items()}

FILE_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S",
              12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
              19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL",
              26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16",
              36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4"}


class _Reader:
    def __init__(self, f):
        self.f = f

    def read(self, n):
        b = self.f.read(n)
        if len(b) != n:
            raise EOFError("unexpected end of file")
        return b

    def u32(self):
        return struct.unpack("<I", self.read(4))[0]

    def u64(self):
        return struct.unpack("<Q", self.read(8))[0]

    def string(self, keep=True):
        n = self.u64()
        if n > 1 << 26:
            raise ValueError("string too long")
        if not keep:
            self.f.seek(n, 1)
            return None
        return self.read(n).decode("utf-8", "replace")

    def value(self, t, keep=True):
        if t in TYPES:
            b = self.read(SIZES[t])
            return struct.unpack(TYPES[t], b)[0]
        if t == 8:
            return self.string(keep)
        if t == 9:
            it = self.u32()
            n = self.u64()
            if it in SIZES and not keep:
                self.f.seek(SIZES[it] * n, 1)
                return None
            if not keep or n > 4096:
                for _ in range(n):
                    self.value(it, keep=False)
                return None if not keep else f"<array of {n}>"
            return [self.value(it) for _ in range(n)]
        raise ValueError(f"unknown GGUF value type {t}")


def read_metadata(path, max_keys=4000):
    """Returns a dict of the metadata keys, skipping big arrays (tokenizer vocab)."""
    meta = {}
    with open(path, "rb") as fh:
        r = _Reader(fh)
        if r.read(4) != b"GGUF":
            raise ValueError("not a GGUF file")
        version = r.u32()
        if version < 2:
            raise ValueError(f"GGUF version {version} is too old")
        meta["__tensor_count"] = r.u64()
        n_kv = r.u64()
        for _ in range(min(n_kv, max_keys)):
            key = r.string()
            t = r.u32()
            big = key.startswith("tokenizer.ggml.") or key.startswith("tokenizer.chat_template")
            if key == "tokenizer.chat_template":
                meta["__has_chat_template"] = True
                r.value(t, keep=False)
                continue
            meta[key] = r.value(t, keep=not big)
    return meta


def summarize(meta):
    """The parts of the metadata Pumpkin uses."""
    arch = meta.get("general.architecture") or ""
    g = lambda k, d=None: meta.get(f"{arch}.{k}", d)
    n_layer = g("block_count") or 0
    n_head = g("attention.head_count") or 0
    if isinstance(n_head, list):
        n_head = max(n_head) if n_head else 0
    n_kv = g("attention.head_count_kv", n_head) or n_head
    if isinstance(n_kv, list):
        # per-layer list: layers with 0 KV heads are not attention layers
        attn_layers = sum(1 for x in n_kv if x)
        n_kv = max(n_kv) if n_kv else 0
    else:
        attn_layers = n_layer
    embd = g("embedding_length") or 0
    key_len = g("attention.key_length") or (embd // n_head if n_head else 0)
    val_len = g("attention.value_length") or key_len
    interval = g("full_attention_interval")
    if interval and n_layer:
        attn_layers = max(1, n_layer // int(interval))
    sliding = g("attention.sliding_window")
    ftype = meta.get("general.file_type")
    is_mmproj = (meta.get("general.type") == "mmproj" or arch == "clip"
                 or any(k.startswith("clip.") for k in meta))
    return {
        "arch": arch, "name": meta.get("general.name") or meta.get("general.basename"),
        "size_label": meta.get("general.size_label"), "quant": FILE_TYPES.get(ftype) if ftype is not None else None,
        "ctx_train": g("context_length"), "layers": n_layer, "attn_layers": attn_layers, "kv_heads": n_kv,
        "key_length": key_len, "value_length": val_len, "experts": g("expert_count"),
        "experts_used": g("expert_used_count"), "sliding_window": sliding,
        "chat_template": bool(meta.get("__has_chat_template")), "mmproj": is_mmproj,
        "vision": bool(meta.get("clip.has_vision_encoder")) if is_mmproj else None,
        "audio": bool(meta.get("clip.has_audio_encoder")) if is_mmproj else None,
    }
