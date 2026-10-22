"""Test helpers: tiny GGUF files and a fake llama-server."""
import os
import struct


def _s(x):
    b = x.encode()
    return struct.pack("<Q", len(b)) + b


def make_gguf(path, arch="qwen3moe", size_gb=0.0, name="Test Model", layers=48, heads=32, kv_heads=4,
              embd=2048, key_len=128, ctx=262144, experts=128, file_type=15, mmproj=False, vocab=2000):
    kv = []

    def add(key, t, val):
        kv.append(_s(key) + struct.pack("<I", t) + val)
    if mmproj:
        add("general.architecture", 8, _s("clip"))
        add("general.type", 8, _s("mmproj"))
        add("clip.has_vision_encoder", 7, struct.pack("<?", True))
    else:
        add("general.architecture", 8, _s(arch))
        add("general.name", 8, _s(name))
        add("general.file_type", 4, struct.pack("<I", file_type))
        add(f"{arch}.block_count", 4, struct.pack("<I", layers))
        add(f"{arch}.context_length", 4, struct.pack("<I", ctx))
        add(f"{arch}.embedding_length", 4, struct.pack("<I", embd))
        add(f"{arch}.attention.head_count", 4, struct.pack("<I", heads))
        add(f"{arch}.attention.head_count_kv", 4, struct.pack("<I", kv_heads))
        add(f"{arch}.attention.key_length", 4, struct.pack("<I", key_len))
        if experts:
            add(f"{arch}.expert_count", 4, struct.pack("<I", experts))
            add(f"{arch}.expert_used_count", 4, struct.pack("<I", 8))
        # a big string array like a tokenizer vocab, which the reader must skip
        toks = b"".join(_s(f"tok{i}") for i in range(vocab))
        add("tokenizer.ggml.tokens", 9, struct.pack("<I", 8) + struct.pack("<Q", vocab) + toks)
        add("tokenizer.chat_template", 8, _s("{{ messages }}"))
    data = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", len(kv)) + b"".join(kv)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    if size_gb:
        if os.name == "nt":
            # NTFS only makes a file sparse when told to; otherwise a 40 GB fake model is 40 GB of written zeros
            import subprocess
            subprocess.run(["fsutil", "sparse", "setflag", path], check=True, capture_output=True)
        size = int(size_gb * 2 ** 30)
        if os.name == "nt":
            # os.truncate zero-fills on Windows (minutes for 20 GB); writing one byte at the end of a sparse file is instant
            with open(path, "r+b") as f:
                f.seek(size - 1)
                f.write(b"\0")
        else:
            os.truncate(path, size)                    # sparse: no real disk use
    return path


FAKE_SERVER = r'''#!/usr/bin/env python3
"""Fake llama-server for tests: --version, --help, and a tiny OpenAI-style API with tools."""
import json, sys, time, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
args = sys.argv[1:]
if "--version" in args:
    print("version: 11796 (abcdef1)\nbuilt with cc for x86_64-linux-gnu\nVulkan backend"); sys.exit(0)
if "--help" in args:
    print("-m, --model\n-c\n-fit, --fit\n-fitt\n-fa\n-ctk\n--metrics\n--agent\n--tools-runtime\n"
          "--mcp-servers-config\n--reasoning-budget\n--no-reasoning-preserve\n--webui-config-file\n"
          "--sleep-idle-seconds\n--mmproj"); sys.exit(0)
port = int(args[args.index("--port") + 1])
if os.environ.get("FAKE_LLAMA_FAIL"):
    print("ggml_vulkan: ErrorOutOfDeviceMemory\nfailed to allocate buffer"); sys.exit(1)
time.sleep(float(os.environ.get("FAKE_LLAMA_LOAD", "1.0")))
TOOLS = [{"tool": "web_search", "definition": {"type": "function", "function": {"name": "web_search",
         "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}}]
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(s, *a): pass
    def j(s, o, code=200):
        b = json.dumps(o).encode(); s.send_response(code); s.send_header("Content-Type", "application/json")
        s.send_header("Content-Length", str(len(b))); s.end_headers(); s.wfile.write(b)
    def denied(s):
        key = os.environ.get("LLAMA_API_KEY")
        if key and s.headers.get("Authorization") != "Bearer " + key:
            s.j({"error": {"code": 401, "message": "Invalid API Key"}}, 401); return True
        return False
    def do_GET(s):
        p = s.path.split("?")[0]
        if p == "/health": return s.j({"status": "ok"})
        if s.denied(): return
        if p == "/metrics":
            b = b"llamacpp:predicted_tokens_seconds 37.5\nllamacpp:prompt_tokens_seconds 900\n"
            s.send_response(200); s.send_header("Content-Length", str(len(b))); s.end_headers(); s.wfile.write(b); return
        if p == "/tools": return s.j(TOOLS)
        if p == "/props": return s.j({"model_path": args[args.index("-m") + 1], "default_generation_settings": {"n_ctx": 8192, "params": {}}, "modalities": {}, "build_info": "b11796", "ui_settings": {"systemMessage": "STALE SERVER PERSONA"}})
        s.j({"error": {"code": 404, "message": "nf"}}, 404)
    def do_POST(s):
        n = int(s.headers.get("Content-Length") or 0); body = json.loads(s.rfile.read(n) or b"{}")
        if s.denied(): return
        p = s.path.split("?")[0]
        if p == "/tools":
            return s.j({"plain_text_response": "1. Result about " + body["params"].get("query", "?")})
        if p == "/v1/chat/completions":
            msgs = body.get("messages", [])
            if body.get("stream"):
                s.send_response(200); s.send_header("Content-Type", "text/event-stream"); s.end_headers()
                for w in ["Hello", " from", " the", " fake", " model."]:
                    s.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": w}}]}) + "\n\n").encode()); s.wfile.flush(); time.sleep(0.05)
                s.wfile.write(b"data: [DONE]\n\n"); s.wfile.flush(); s.close_connection = True; return
            if body.get("tools") and not any(m.get("role") == "tool" for m in msgs):
                msg = {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                       "function": {"name": "web_search", "arguments": json.dumps({"query": "pumpkins"})}}]}
            else:
                msg = {"role": "assistant", "content": "## Answer\n\nPumpkins are great."}
            return s.j({"choices": [{"message": msg}], "timings": {"predicted_per_second": 37.1, "prompt_per_second": 812.0, "prompt_n": 2000}})
        s.j({"error": "nf"}, 404)
ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def make_fake_server(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(FAKE_SERVER)
    os.chmod(path, 0o755)
    if os.name == "nt":
        # Windows runs programs by extension: a .cmd next to the script makes `llama-server` a command
        import sys
        with open(path + ".cmd", "w", encoding="utf-8") as f:
            f.write(f'@"{sys.executable}" "%~dp0{os.path.basename(path)}" %*\r\n')
        return path + ".cmd"
    return path
