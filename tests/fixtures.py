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
    print("version: %s (abcdef1)\nbuilt with cc for x86_64-linux-gnu\nVulkan backend" % os.environ.get("FAKE_LLAMA_BUILD", "11796")); sys.exit(0)
if "--help" in args:
    print("-m, --model\n-c\n-fit, --fit\n-fitt\n-fa\n-ctk\n--metrics\n--agent\n--tools-runtime\n"
          "--mcp-servers-config\n--reasoning-budget\n--no-reasoning-preserve\n--webui-config-file\n"
          "--sleep-idle-seconds\n--mmproj\n--slot-save-path\n--spec-default"); sys.exit(0)
port = int(args[args.index("--port") + 1])
if os.environ.get("FAKE_LLAMA_FAIL_ABOVE") and "-c" in args and \
        int(args[args.index("-c") + 1]) > int(os.environ["FAKE_LLAMA_FAIL_ABOVE"]):
    print("ggml_vulkan: ErrorOutOfDeviceMemory\nfailed to allocate buffer"); sys.exit(1)
if os.environ.get("FAKE_MMPROJ_FAIL") and "--mmproj" in args:
    print("clip_model_loader: unknown projector type\nload_model: failed to load multimodal model, 'mmproj-F16.gguf'"); sys.exit(1)
if os.environ.get("FAKE_LLAMA_FAIL"):
    print("ggml_vulkan: ErrorOutOfDeviceMemory\nfailed to allocate buffer"); sys.exit(1)
time.sleep(float(os.environ.get("FAKE_LLAMA_LOAD", "1.0")))
TOOLS = [{"tool": "web_search", "definition": {"type": "function", "function": {"name": "web_search",
         "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}}]
if "--mcp-servers-config" in args:
    try:
        if "image" in json.load(open(args[args.index("--mcp-servers-config") + 1])).get("mcpServers", {}):
            TOOLS.append({"tool": "image_generate", "definition": {"type": "function", "function": {
                "name": "image_generate", "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}}}}}})
    except (OSError, ValueError):
        pass
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
        if p == "/slots": return s.j([{"id": 0, "is_processing": False, "next_token": [{"n_decoded": 0}]}])
        if p == "/tools": return s.j(TOOLS)
        if p == "/props" and os.environ.get("FAKE_LLAMA_NEW_API"): return s.j({"model": {"context": 8192}})
        if p == "/props": return s.j({"model_path": args[args.index("-m") + 1], "default_generation_settings": {"n_ctx": 8192, "params": {}}, "modalities": {}, "build_info": "b11796", "ui_settings": {"systemMessage": "STALE SERVER PERSONA"}})
        s.j({"error": {"code": 404, "message": "nf"}}, 404)
    def do_POST(s):
        n = int(s.headers.get("Content-Length") or 0); body = json.loads(s.rfile.read(n) or b"{}")
        if s.denied(): return
        p = s.path.split("?")[0]
        if p.startswith("/slots/"):
            act = s.path.split("action=")[-1]
            f = os.path.join(args[args.index("--slot-save-path") + 1], body.get("filename", "x")) if "--slot-save-path" in args else None
            if not f: return s.j({"error": {"code": 501, "message": "start with --slot-save-path"}}, 501)
            note = os.path.join(os.path.dirname(os.path.abspath(args[args.index("-m") + 1])), "slot-calls.txt")
            open(note, "a").write(act + " " + body.get("filename", "") + "\n")
            if act == "save":
                open(f, "wb").write(b"x" * 1000); return s.j({"n_saved": 5000, "filename": body["filename"]})
            if act == "restore":
                if not os.path.exists(f): return s.j({"error": {"code": 404, "message": "no file"}}, 404)
                return s.j({"n_restored": 5000, "filename": body["filename"]})
        if p == "/tools":
            return s.j({"plain_text_response": "1. Result about " + body["params"].get("query", "?")})
        if p == "/v1/chat/completions":
            msgs = body.get("messages", [])
            seen = os.path.join(os.path.dirname(os.path.abspath(args[args.index("-m") + 1])), "chat-%d.json" % port)
            json.dump(body, open(seen, "w"))
            names = [t.get("function", {}).get("name") for t in body.get("tools") or []]
            if names and "edit" in names and "bash" in names:          # Pumpkin Code
                if "TOOBIG" in json.dumps(msgs[:3]) and "cut off because" not in json.dumps(msgs):
                    return s.j({"error": {"code": 500, "message": "Failed to parse tool call arguments as JSON: parse error at line 1, column 24500: syntax error while parsing value - invalid string: missing closing quote"}}, 500)
                if "SLOWREAD" in json.dumps(msgs[:3]):
                    time.sleep(30)                                   # reading a long prompt: nothing sent yet
                if "HANGS" in json.dumps(msgs[:3]) and "HUNG-ONCE" not in os.environ.get("X", "") and not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(args[args.index("-m") + 1])), "hung-once")):
                    open(os.path.join(os.path.dirname(os.path.abspath(args[args.index("-m") + 1])), "hung-once"), "w").write("x")
                    s.send_response(200); s.send_header("Content-Type", "text/event-stream"); s.end_headers(); s.wfile.flush()
                    time.sleep(60); return
                done = sum(1 for m in msgs if m.get("role") == "tool")
                plan = [("todo", {"items": [{"text": "Find the greeting"}, {"text": "Change it"}]}),
                        ("read", {"path": "hello.py"}),
                        ("edit", {"path": "hello.py", "old": 'print("Hello")', "new": 'print("Hi")'}),
                        ("bash", {"command": sys.executable.replace("\\", "/") + " hello.py"})]
                s.send_response(200); s.send_header("Content-Type", "text/event-stream"); s.end_headers()
                send = lambda d, fin=None: s.wfile.write(("data: " + json.dumps({"choices": [{"delta": d, "finish_reason": fin}]}) + "\n\n").encode())
                if done < len(plan):
                    name, a = plan[done]
                    raw = json.dumps(a)
                    send({"reasoning_content": "Next I will use " + name})
                    send({"tool_calls": [{"index": 0, "id": "c%d" % done, "type": "function", "function": {"name": name, "arguments": raw[:5]}}]})
                    send({"tool_calls": [{"index": 0, "function": {"arguments": raw[5:]}}]})
                    send({}, "tool_calls")
                else:
                    for w in ["I changed", " the greeting", " to Hi and ran it."]:
                        send({"content": w})
                    send({}, "stop")
                s.wfile.write(("data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 40}}) + "\n\n").encode())
                s.wfile.write(b"data: [DONE]\n\n"); s.wfile.flush(); s.close_connection = True; return
            if body.get("stream") and "Summarise this coding session" in json.dumps(msgs):
                s.send_response(200); s.send_header("Content-Type", "text/event-stream"); s.end_headers()
                s.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": "SUMMARY: greeting changed"}}]}) + "\n\n").encode())
                s.wfile.write(b"data: [DONE]\n\n"); s.wfile.flush(); s.close_connection = True; return
            if "--mmproj" in args and "image_url" in json.dumps(msgs):
                text = "A photo of an orange pumpkin on a table.\nText in the picture: HARVEST 42"
                return s.j({"choices": [{"message": {"role": "assistant", "content": text}}]})
            names = [t.get("function", {}).get("name") for t in body.get("tools") or []]
            last = next((m for m in reversed(msgs) if m.get("role") == "user"), {})
            wants = "draw" in json.dumps(last.get("content", "")).lower()
            if body.get("stream") and "image_generate" in names and wants and msgs[-1].get("role") == "user":
                s.send_response(200); s.send_header("Content-Type", "text/event-stream"); s.end_headers()
                call = {"index": 0, "id": "img1", "type": "function", "function": {"name": "image_generate",
                        "arguments": json.dumps({"prompt": "an orange pumpkin on a wooden table, soft light"})}}
                for d in ({"role": "assistant", "content": None, "tool_calls": [call]},):
                    s.wfile.write(("data: " + json.dumps({"choices": [{"delta": d}]}) + "\n\n").encode())
                s.wfile.write(("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}) + "\n\n").encode())
                s.wfile.write(b"data: [DONE]\n\n"); s.wfile.flush(); s.close_connection = True; return
            if body.get("stream") and msgs and msgs[-1].get("role") == "tool":
                s.send_response(200); s.send_header("Content-Type", "text/event-stream"); s.end_headers()
                for w in ["Here", " is", " your", " pumpkin."]:
                    s.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": w}}]}) + "\n\n").encode()); s.wfile.flush()
                s.wfile.write(b"data: [DONE]\n\n"); s.wfile.flush(); s.close_connection = True; return
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
            t = {"predicted_per_second": 37.1, "prompt_per_second": 812.0, "prompt_n": 2000, "predicted_n": 40}
            if "--spec-default" in args:
                t.update(draft_n=100, draft_n_accepted=60)
                if "Repeat this text" in json.dumps(msgs):
                    t["predicted_per_second"] = 74.2          # guessing from the chat pays off when text repeats
            return s.j({"choices": [{"message": msg}], "timings": t})
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


FAKE_SD = r'''#!/usr/bin/env python3
"""Fake sd-server for tests: model status, async img_gen jobs. Writes what it was asked to sd-calls.jsonl."""
import base64, json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
args = sys.argv[1:]
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sd-calls.jsonl")
def note(o):
    with open(LOG, "a") as f:
        f.write(json.dumps(o) + "\n")
note({"start": args})
if os.environ.get("FAKE_SD_FAIL"):
    print("ggml_cuda: out of memory"); sys.exit(1)
port = int(args[args.index("--listen-port") + 1])
born = time.time()
JOBS = {}
def png(text, w=96, h=96):
    """A real picture (an orange disc on a dark background), with the prompt in a text chunk."""
    import struct, zlib
    rows = b""
    for y in range(h):
        rows += b"\x00" + b"".join(b"\xf0\x80\x20" if (x - w / 2) ** 2 + (y - h / 2) ** 2 < (w / 3) ** 2
                                  else b"\x22\x22\x2a" for x in range(w))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"prompt\x00" + text.encode()) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
class H(BaseHTTPRequestHandler):
    def log_message(s, *a): pass
    def j(s, o, code=200):
        b = json.dumps(o).encode(); s.send_response(code); s.send_header("Content-Type", "application/json")
        s.send_header("Content-Length", str(len(b))); s.end_headers(); s.wfile.write(b)
    def do_GET(s):
        if s.path == "/sdcpp/v1/models/status":
            return s.j({"state": "ready" if time.time() - born > 0.3 else "loading"})
        if s.path.startswith("/sdcpp/v1/jobs/"):
            jid = s.path.rsplit("/", 1)[-1]
            job = JOBS.get(jid)
            if not job: return s.j({"error": "nf"}, 404)
            if time.time() - job["at"] < 0.3: return s.j({"id": jid, "status": "generating"})
            if job.get("video"):
                clip = base64.b64encode(b"\x1aE\xdf\xa3fake webm " + job["prompt"].encode()).decode()
                return s.j({"id": jid, "status": "completed", "result": {"output_format": "webm", "mime_type": "video/webm",
                            "b64_json": clip, "fps": job["fps"], "frame_count": job["frames"]}})
            img = base64.b64encode(png(job["prompt"])).decode()
            return s.j({"id": jid, "status": "completed", "result": {"output_format": "jpeg",
                        "images": [{"index": 0, "b64_json": img}]}})
        s.j({"error": "nf"}, 404)
    def do_POST(s):
        n = int(s.headers.get("Content-Length") or 0); body = json.loads(s.rfile.read(n) or b"{}")
        if s.path == "/sdcpp/v1/img_gen":
            jid = "job_%d" % (len(JOBS) + 1)
            JOBS[jid] = {"at": time.time(), "prompt": body.get("prompt", "")}
            note({"img_gen": body})
            return s.j({"id": jid, "status": "queued"}, 202)
        if s.path == "/sdcpp/v1/vid_gen":
            jid = "job_%d" % (len(JOBS) + 1)
            JOBS[jid] = {"at": time.time(), "prompt": body.get("prompt", ""), "video": True,
                         "fps": body.get("fps"), "frames": body.get("video_frames")}
            note({"vid_gen": {k: v for k, v in body.items() if k != "init_image"}, "init": bool(body.get("init_image"))})
            return s.j({"id": jid, "status": "queued"}, 202)
        s.j({"error": "nf"}, 404)
ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def make_fake_sd(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(FAKE_SD)
    os.chmod(path, 0o755)
    if os.name == "nt":
        import sys
        with open(path + ".cmd", "w", encoding="utf-8") as f:
            f.write(f'@"{sys.executable}" "%~dp0{os.path.basename(path)}" %*\r\n')
        return path + ".cmd"
    return path
