"""Fake Hugging Face for UI tests: search, file trees and slow downloads of tiny GGUF files."""
import io
import json
import os
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures  # noqa: E402

TMP = tempfile.mkdtemp()
QUANTS = {"Q3_K_M": 0.43, "Q4_K_M": 0.55, "UD-Q4_K_XL": 0.58, "Q5_K_M": 0.65, "Q6_K": 0.78, "Q8_0": 1.0}
REPOS = {
    "unsloth/Qwen3.6-35B-A3B-GGUF": (36.0, "qwen3moe", 128),
    "unsloth/Qwen3.8-27B-GGUF": (28.0, "qwen3", 0),
    "ggml-org/gpt-oss-20b-GGUF": (12.5, "gpt-oss", 32),
    "unsloth/gemma-3-12b-it-GGUF": (12.5, "gemma3", 0),
    "unsloth/Qwen3-4B-Instruct-2507-GGUF": (4.2, "qwen3", 0),
    "unsloth/gemma-3-4b-it-GGUF": (4.3, "gemma3", 0),
    "bartowski/Mistral-Small-3.2-24B-GGUF": (24.0, "llama", 0),
    "unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF": (30.5, "qwen3moe", 128),
    "ornith-ai/Ornith-1.5-35B-A3B-GGUF": (35.5, "qwen35moe", 256),
    "ornith-ai/Ornith-1.5-9B-GGUF": (9.2, "qwen35", 0),
    "bartowski/Qwen_Qwen3.6-35B-A3B-GGUF": (35.1, "qwen3moe", 128),
    "JonathanColetti/Qwen3.8-27B-Uncensored-GGUF": (27.3, "qwen35", 0),
    "ggml-org/gpt-oss-120b-GGUF": (117.0, "gpt-oss", 128),
    "unsloth/GLM-4.5-Air-GGUF": (106.0, "glm4moe", 128),
    "TheBloke/Llama-2-13B-chat-GGUF": (13.0, "llama", 0),
}

RICH = [
    # id, params (B), arch, pipeline, downloads, trending, days old, tools in template
    ("unsloth/Qwen3.6-35B-A3B-GGUF", 35.1, "qwen3moe", "text-generation", 2100000, 180, 40, True),
    ("bartowski/Qwen_Qwen3.6-35B-A3B-GGUF", 35.1, "qwen3moe", "text-generation", 900000, 60, 38, True),
    ("ornith-ai/Ornith-1.5-35B-A3B-GGUF", 35.5, "qwen35moe", "text-generation", 3866315, 240, 20, True),
    ("ornith-ai/Ornith-1.5-9B-GGUF", 9.2, "qwen35", "text-generation", 5040182, 300, 20, True),
    ("unsloth/Qwen3.8-27B-GGUF", 27.3, "qwen35", "text-generation", 1500000, 90, 30, True),
    ("JonathanColetti/Qwen3.8-27B-Uncensored-GGUF", 27.3, "qwen35", "text-generation", 2542392, 120, 25, True),
    ("ggml-org/gpt-oss-20b-GGUF", 20.9, "gpt-oss", "text-generation", 1800000, 40, 300, True),
    ("ggml-org/gpt-oss-120b-GGUF", 117.0, "gpt-oss", "text-generation", 700000, 30, 300, True),
    ("unsloth/GLM-4.5-Air-GGUF", 106.0, "glm4moe", "text-generation", 400000, 20, 400, True),
    ("unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF", 30.5, "qwen3moe", "text-generation", 1200000, 15, 450, True),
    ("unsloth/Qwen3-4B-Instruct-2507-GGUF", 4.0, "qwen3", "text-generation", 1100000, 10, 450, True),
    ("bartowski/Mistral-Small-3.2-24B-GGUF", 24.0, "llama", "text-generation", 300000, 5, 480, True),
    ("TheBloke/Llama-2-13B-chat-GGUF", 13.0, "llama", "text-generation", 600000, 0, 1100, False),
    ("unsloth/gemma-3-12b-it-GGUF", 12.2, "gemma3", "image-text-to-text", 900000, 12, 500, False),
    ("unsloth/gemma-3-4b-it-GGUF", 3.9, "gemma3", "image-text-to-text", 800000, 8, 500, False),
    ("abenzerps/Qwen-Image-2.1-Uncensored-GGUF", 7.1, "qwen_image21", "text-to-image", 50000, 683, 5, False),
    ("nomic-ai/nomic-embed-text-v2-GGUF", 0.5, "nomic-bert", "feature-extraction", 900000, 3, 200, False),
]


def rich(pipeline):
    out = []
    for rid, pb, arch, pipe, dl, tr, days, tools in RICH:
        if pipeline and pipe != pipeline:
            continue
        ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - days * 86400))
        tmpl = "{% for m in messages %}{{ m.content }}{% endfor %}" + (" {% if tools %}{{ tools }}{% endif %}" if tools else "")
        out.append({"id": rid, "downloads": dl, "trendingScore": tr, "likes": dl // 5000, "lastModified": ts,
                    "gated": False, "pipeline_tag": pipe,
                    "gguf": {"total": int(pb * 1e9), "architecture": arch, "context_length": 131072,
                             "chat_template": "" if pipe in ("text-to-image", "feature-extraction") else tmpl}})
    return out


def tree(repo):
    full, arch, experts = REPOS[repo]
    base = repo.split("/")[1].replace("-GGUF", "")
    out = [{"type": "file", "path": "README.md", "size": 100}]
    for q, frac in QUANTS.items():
        if repo.startswith("ggml-org/gpt-oss") and q != "Q8_0":
            continue
        out.append({"type": "file", "path": f"{base}-{q}.gguf", "lfs": {"size": int(full * frac * 2 ** 30)}})
    if "gemma" in repo:
        out.append({"type": "file", "path": "mmproj-F16.gguf", "lfs": {"size": 850 * 2 ** 20}})
    return out


def file_bytes(repo, name):
    p = os.path.join(TMP, repo.replace("/", "_"), name)
    if not os.path.exists(p):
        full, arch, experts = REPOS[repo]
        if "mmproj" in name:
            fixtures.make_gguf(p, mmproj=True)
        else:
            fixtures.make_gguf(p, arch=arch, name=repo.split("/")[1], experts=experts, vocab=50)
        with open(p, "ab") as f:
            f.write(os.urandom(3 * 2 ** 20))           # 3 MB so progress is visible
    return open(p, "rb").read()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def j(self, o, code=200):
        b = json.dumps(o).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def route(self, head=False):
        u = urlparse(self.path)
        p = unquote(u.path)
        if p == "/api/models" and "expand" in u.query:
            pipe = (parse_qs(u.query).get("pipeline_tag") or [""])[0]
            return self.j(rich(pipe))
        if p == "/api/models":
            q = (parse_qs(u.query).get("search") or [""])[0].lower().replace(" gguf", "")
            hits = [r for r in REPOS if all(w in r.lower() for w in q.split())] or list(REPOS)[:3]
            return self.j([{"id": r, "downloads": 120000 // (i + 1), "likes": 50} for i, r in enumerate(hits)])
        if p.startswith("/api/models/") and p.endswith("/tree/main"):
            repo = p[len("/api/models/"):-len("/tree/main")]
            return self.j(tree(repo)) if repo in REPOS else self.j({"error": "nf"}, 404)
        if "/resolve/main/" in p:
            repo, name = p.lstrip("/").split("/resolve/main/")
            if repo not in REPOS:
                return self.j({"error": "nf"}, 404)
            data = file_bytes(repo, name)
            start = 0
            rng = self.headers.get("Range")
            if rng:
                start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206 if rng else 200)
            self.send_header("Content-Length", str(len(data) - start))
            self.end_headers()
            if head:
                return
            buf = io.BytesIO(data[start:])
            while True:
                chunk = buf.read(256 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                time.sleep(0.15)
            return
        self.j({"error": "nf"}, 404)

    def do_GET(self):
        self.route()

    def do_HEAD(self):
        self.route(head=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
