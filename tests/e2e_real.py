"""End to end on a real machine (CI runs it on macOS and Linux): Pumpkin downloads the real llama.cpp build,
downloads a tiny real model from Hugging Face, starts it and answers a chat through its own API.

    python tests/e2e_real.py            # needs the internet; uses a throwaway home folder
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="pumpkin-e2e-")
PORT, LLAMA_PORT = 18490, 18491
ENV = dict(os.environ, HOME=HOME, USERPROFILE=HOME, XDG_CONFIG_HOME=f"{HOME}/.config", XDG_DATA_HOME=f"{HOME}/.local/share",
           XDG_STATE_HOME=f"{HOME}/.local/state", PYTHONPATH=ROOT)
os.makedirs(f"{HOME}/.config/pumpkin")
with open(f"{HOME}/.config/pumpkin/settings.json", "w") as f:
    json.dump({"port": PORT, "llama_port": LLAMA_PORT, "keep_awake": False, "notifications": False,
               "check_updates": False}, f)


def _up():
    try:
        return bool(call("GET", "/pumpkin/api/state", timeout=5))
    except (OSError, urllib.error.URLError):
        return False


def step(msg):
    print(f"\n== {msg}", flush=True)


def call(method, path, body=None, timeout=60):
    tok = open(f"{HOME}/.config/pumpkin/token").read().strip()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {tok}", "X-Pumpkin": "1",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read()[:500]!r}")


def wait(cond, what, timeout=900):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(2)
    raise SystemExit(f"timed out waiting for {what}")


# E2E_PYTHON: run the server with another Python, e.g. the one inside the installed Windows package
server = subprocess.Popen([os.environ.get("E2E_PYTHON") or sys.executable, "-m", "pumpkin", "serve"], env=ENV, cwd=HOME,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    step("Pumpkin starts")
    wait(lambda: os.path.exists(f"{HOME}/.config/pumpkin/token"), "the token")

    def state():
        try:
            return call("GET", "/pumpkin/api/state")
        except (SystemExit, OSError):
            return None
    wait(state, "Pumpkin", 60)
    hw = call("GET", "/pumpkin/api/hardware")
    print(json.dumps({k: hw.get(k) for k in ("main_gpu", "ram_total", "backend", "cpu")}, indent=1))

    step("Pumpkin downloads llama.cpp and checks its checksum")
    backend = os.environ.get("E2E_BACKEND") or hw["backend"]
    job = call("POST", "/pumpkin/api/llama/download", {"backend": backend})
    job = wait(lambda: (j := call("GET", f"/pumpkin/api/jobs/{job['id']}"))["state"] != "running" and j, "llama.cpp", 900)
    if job["state"] != "done":
        raise SystemExit(f"llama.cpp download failed: {job['error']}")
    info = call("GET", "/pumpkin/api/llama")
    print(info["path"], info["info"]["version"], info["info"]["backends"], len(info["info"]["flags"]), "known options")
    for args in (["--version"], ["--help"]):
        t0 = time.time()
        r = subprocess.run([info["path"], *args], capture_output=True, text=True, timeout=120,
                           env=dict(ENV, DYLD_LIBRARY_PATH=os.path.dirname(info["path"]),
                                    LD_LIBRARY_PATH=os.path.dirname(info["path"])))
        out = (r.stdout or "") + (r.stderr or "")
        print(f"$ llama-server {args[0]}  (exit {r.returncode}, {time.time() - t0:.1f}s, {len(out)} chars)")
        print("\n".join(out.splitlines()[:12] if args[0] == "--version" else
                        [l for l in out.splitlines() if any(f in l for f in ("--agent", "-fit", "--metrics", "-ctk"))][:8]))
    assert info["info"]["ok"], info["info"]

    step("Pumpkin downloads a tiny real model from Hugging Face")
    dl = call("POST", "/pumpkin/api/downloads", {"repo": "ggml-org/models", "files": ["tinyllamas/stories260K.gguf"]})
    wait(lambda: next((j for j in call("GET", "/pumpkin/api/downloads")["jobs"] if j["id"] == dl["id"]
                       and j["state"] not in ("queued", "downloading")), None), "the model", 600)
    lib = call("GET", "/pumpkin/api/models?rescan=1")["models"]
    model = next(m for m in lib if m["file"].startswith("stories260K"))
    print(model["path"], model["size_gb"], "GB", model["fit"])

    step("Start it and chat through Pumpkin")
    preset = call("POST", "/pumpkin/api/presets/from-model", {"path": model["path"], "name": "Tiny"})
    patch = {"ctx": 256, "tools": False, "web": False}
    if os.environ.get("E2E_EXTRA"):
        patch["extra"] = os.environ["E2E_EXTRA"]
    call("PUT", f"/pumpkin/api/presets/{preset['id']}", patch)
    call("POST", "/pumpkin/api/llama/start", {"preset": preset["id"]})
    st = wait(lambda: (s := call("GET", "/pumpkin/api/state")["server"])["state"] in ("ready", "error") and s, "the model", 300)
    if st["state"] != "ready":
        print(call("GET", "/pumpkin/api/llama/log?n=80")["log"])
        raise SystemExit(f"model failed to start: {st['error']}")
    r = call("POST", "/v1/chat/completions", {"model": "Tiny", "max_tokens": 24,
                                              "messages": [{"role": "user", "content": "Once upon a time"}]}, 120)
    text = r["choices"][0]["message"]["content"]
    print("model says:", repr(text))
    assert text.strip(), "empty answer"
    print("speed:", r.get("timings", {}).get("predicted_per_second"), "tokens/s")
    log = open(f"{HOME}/.local/state/pumpkin/llama-server.log").read()
    print("\n".join(l for l in log.splitlines() if any(w in l.lower() for w in ("metal", "gpu", "offload", "vulkan")))[:2000])

    step("Devices: the model can hand tasks to another Pumpkin (here it pairs with itself)")
    call("POST", "/pumpkin/api/devices/share", {"on": True, "name": "E2E box"})
    time.sleep(1.5)                                            # it re-listens on all addresses
    code = call("GET", "/pumpkin/api/devices/share")["code"]
    dev = call("POST", "/pumpkin/api/devices", {"code": code})
    print("paired:", dev["name"], dev["urls"])
    # the device tools come with the preset's Tools switch and a fresh start
    call("PUT", f"/pumpkin/api/presets/{preset['id']}", {"tools": True, "sandbox": False, "web": False})
    call("POST", "/pumpkin/api/llama/start", {"preset": preset["id"]})
    st = wait(lambda: (s := call("GET", "/pumpkin/api/state")["server"])["state"] in ("ready", "error") and s,
              "the model with the devices tools", 300)
    if st["state"] != "ready":
        print(call("GET", "/pumpkin/api/llama/log?n=80")["log"])
        raise SystemExit(f"model failed to start with the devices tools: {st['error']}")
    names = [t.get("tool") for t in call("GET", "/tools")]
    print("llama-server tools:", names)
    assert "devices_list" in names and "devices_ask" in names, names
    assert call("GET", "/pumpkin/api/devices")["model_has_tools"] is True
    r = call("POST", "/tools", {"tool": "devices_list", "params": {}}, 60)
    print("devices_list:", json.dumps(r)[:300])
    assert "E2E box" in json.dumps(r) and "online" in json.dumps(r), r
    r = call("POST", "/tools", {"tool": "devices_ask", "params": {"device": "E2E box", "task": "Once upon a time"}},
             300)
    print("devices_ask:", json.dumps(r)[:400])
    assert "answer from E2E box" in json.dumps(r), r
    assert call("GET", "/pumpkin/api/devices/share")["recent"][0]["state"] == "done"
    call("POST", "/pumpkin/api/devices/share", {"on": False})
    time.sleep(1.5)                                            # it goes back to listening on this computer only
    wait(lambda: _up(), "Pumpkin to listen again", 30)
    call("DELETE", f"/pumpkin/api/devices/{dev['id']}")

    step("Your documents, saved files and memory: the model's own tools, run by the real llama-server")
    assert {"files_search", "files_read", "files_save", "memory_save", "memory_forget"} <= set(names), names
    files = call("GET", "/pumpkin/api/settings")["files_dir"]
    os.makedirs(files, exist_ok=True)
    with open(os.path.join(files, "e2e-manual.md"), "w", encoding="utf-8") as f:
        f.write("# Router\n\nTo reset the Pumpkin router, hold the blue button for twelve seconds.\n")
    r = call("POST", "/tools", {"tool": "files_search", "params": {"query": "reset router button"}}, 120)
    print("files_search:", json.dumps(r)[:400])
    assert "(e2e-manual.md" in json.dumps(r) and "twelve seconds" in json.dumps(r), r
    r = call("POST", "/tools", {"tool": "files_save", "params": {"name": "E2E summary", "content": "# Done\n\n- one"}}, 60)
    print("files_save:", json.dumps(r)[:300])
    saved = os.path.join(files, "outputs", "E2E summary.docx")
    assert os.path.exists(saved), r
    r = call("POST", "/tools", {"tool": "memory_save", "params": {"fact": "Runs Pumpkin in CI"}}, 60)
    assert any(m["text"] == "Runs Pumpkin in CI" for m in call("GET", "/pumpkin/api/memory")["items"]), r
    assert "Runs Pumpkin in CI" in call("GET", "/pumpkin/api/state")["persona_prompt"]
    call("POST", "/tools", {"tool": "memory_forget", "params": {"what": "runs pumpkin in ci"}}, 60)
    assert call("GET", "/pumpkin/api/memory")["items"] == []
    print("documents:", call("GET", "/pumpkin/api/docs"))
    for p in (saved, os.path.join(files, "e2e-manual.md")):
        os.remove(p)

    call("PUT", f"/pumpkin/api/presets/{preset['id']}", {"tools": False})

    import shutil
    ostype = subprocess.run(["docker", "info", "--format", "{{.OSType}}"], capture_output=True, text=True).stdout.strip() \
        if shutil.which("docker") else ""
    if ostype == "linux":                          # Windows runners have Docker with Windows containers only
        step("The AI's tools write into the work folder (Docker sandbox)")
        call("PUT", f"/pumpkin/api/presets/{preset['id']}", {"tools": True, "sandbox": True, "web": False})
        call("POST", "/pumpkin/api/llama/start", {"preset": preset["id"]})
        st = wait(lambda: (s := call("GET", "/pumpkin/api/state")["server"])["state"] in ("ready", "error") and s,
                  "the model with tools", 600)
        if st["state"] != "ready":
            print(call("GET", "/pumpkin/api/llama/log?n=60")["log"])
            raise SystemExit(f"model with tools failed to start: {st['error']}")
        log = open(f"{HOME}/.local/state/pumpkin/llama-server.log").read().splitlines()[0]
        assert "docker-container:pumpkin-workspace" in log, log
        r = call("POST", "/tools", {"tool": "write_file", "params": {"path": "notes/hello.md", "content": "made by the AI"}}, 120)
        print("write_file:", json.dumps(r)[:200])
        r = call("POST", "/tools", {"tool": "exec_shell_command", "params": {"command": "pwd && ls -la notes"}}, 120)
        print("exec_shell_command:", json.dumps(r)[:300])
        listing = call("GET", "/pumpkin/api/files?path=notes")
        print("Files page:", [i["name"] for i in listing["items"]])
        assert any(i["name"] == "hello.md" for i in listing["items"]), listing
        with open(f"{HOME}/Pumpkin/files/notes/hello.md") as f:
            assert f.read() == "made by the AI"
        st_ = os.stat(f"{HOME}/Pumpkin/files/notes/hello.md")
        print("owner after the Files page looked:", st_.st_uid, "me:", os.getuid())
        assert st_.st_uid == os.getuid()
    else:
        print("\n(no Docker here, skipping the work folder check)")

    step("Without Pumpkin's key the model server refuses")
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{LLAMA_PORT}/v1/models"), timeout=10)
        raise SystemExit("llama-server answered without the key")
    except urllib.error.HTTPError as e:
        assert e.code == 401, e.code

    step("Doctor")
    for c in call("GET", "/pumpkin/api/doctor")["checks"]:
        print(f"[{c['status']:4}] {c['area']}: {c['title']}  {c['detail']}")
    call("POST", "/pumpkin/api/llama/stop")
    print("\nAll good.")
finally:
    server.terminate()
    try:
        out, _ = server.communicate(timeout=20)
        print(out[-3000:])
    except subprocess.TimeoutExpired:
        server.kill()
