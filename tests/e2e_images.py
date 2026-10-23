"""End to end for pictures on a real machine (CI: Linux, Mac, Windows). Pumpkin downloads the real
stable-diffusion.cpp build and checks its checksum, finds the exact files of every image model on Hugging Face,
downloads the tiny one (SDXS) and draws a real picture.

    python tests/e2e_images.py          # needs the internet; uses a throwaway home folder
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="pumpkin-img-e2e-")
PORT = 18590
ENV = dict(os.environ, HOME=HOME, USERPROFILE=HOME, XDG_CONFIG_HOME=f"{HOME}/.config",
           XDG_DATA_HOME=f"{HOME}/.local/share", XDG_STATE_HOME=f"{HOME}/.local/state", PYTHONPATH=ROOT)
os.makedirs(f"{HOME}/.config/pumpkin")
with open(f"{HOME}/.config/pumpkin/settings.json", "w") as f:
    json.dump({"port": PORT, "llama_port": 18591, "image_port": 18592, "keep_awake": False, "notifications": False,
               "check_updates": False, "doc_index": False}, f)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def step(msg):
    print(f"\n== {msg}", flush=True)


def call(method, path, body=None, timeout=60):
    tok = open(f"{HOME}/.config/pumpkin/token").read().strip()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {tok}", "X-Pumpkin": "1",
                                          "Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read()[:800]!r}")


def wait(cond, what, timeout=900):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = cond()
        except (OSError, urllib.error.URLError):
            v = None
        if v:
            return v
        time.sleep(2)
    raise SystemExit(f"timed out waiting for {what}")


def sd_log():
    for p in (f"{HOME}/.local/state/pumpkin/sd-server.log", f"{HOME}/AppData/Local/pumpkin/state/sd-server.log"):
        if os.path.exists(p):
            with open(p, errors="replace") as f:
                return f.read()[-4000:]
    return "(no sd-server log)"


server = subprocess.Popen([os.environ.get("E2E_PYTHON") or sys.executable, "-m", "pumpkin", "serve"], env=ENV,
                          cwd=HOME, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    step("Pumpkin starts")
    wait(lambda: os.path.exists(f"{HOME}/.config/pumpkin/token"), "the token", 60)
    st = wait(lambda: call("GET", "/pumpkin/api/images"), "Pumpkin", 60)
    print("engine for this computer:", st["engine"]["recommended"])
    assert st["supported"], st

    step("Every image model's files are found on Hugging Face")
    st = wait(lambda: (s := call("GET", "/pumpkin/api/images")) and all(b["size_gb"] for b in s["bundles"]) and s,
              "the image models' sizes", 180)
    for b in st["bundles"]:
        print(f"- {b['name']}: about {b['size_gb']} GB, {b['fit']['text'] if b['fit'] else '?'}")

    step("Pumpkin downloads stable-diffusion.cpp and checks its checksum")
    backend = os.environ.get("E2E_IMAGE_BACKEND") or None
    job = call("POST", "/pumpkin/api/images/engine", {"backend": backend} if backend else {})
    job = wait(lambda: (j := call("GET", f"/pumpkin/api/jobs/{job['job']}"))["state"] != "running" and j,
               "the image engine", 900)
    if job["state"] != "done" and sys.platform == "darwin" and "needs macOS" in (job["error"] or ""):
        # stable-diffusion.cpp builds its Mac release on the newest macOS; an older Mac gets told so up front
        print("as expected on this older Mac:", job["error"])
        print("\nAll good (this Mac is too old for the image engine).")
        raise SystemExit(0)
    if job["state"] != "done":
        raise SystemExit(f"image engine download failed: {job['error']}")
    st = call("GET", "/pumpkin/api/images")
    print("engine:", st["engine"])
    r = subprocess.run([st["engine"]["path"], "--help"], capture_output=True, text=True, timeout=120)
    print(f"$ sd-server --help  (exit {r.returncode})")
    print("\n".join(((r.stdout or "") + (r.stderr or "")).splitlines()[:6]))

    step("Pumpkin downloads the tiny image model")
    call("POST", "/pumpkin/api/images/install", {"id": "sdxs"})
    st = wait(lambda: (s := call("GET", "/pumpkin/api/images")) and
              (b := next(x for x in s["bundles"] if x["id"] == "sdxs")) and not b["downloading"] and s,
              "the image model", 900)
    sdxs = next(x for x in st["bundles"] if x["id"] == "sdxs")
    if not sdxs["installed"]:
        raise SystemExit(f"SDXS did not install: {sdxs}")
    print("files:", sdxs["files"])
    assert st["ready"], st

    step("Draw a real picture")
    t0 = time.time()
    req_ok = True
    try:
        pic = call("POST", "/pumpkin/api/images/generate", {"prompt": "a cute orange pumpkin, studio photo"}, 1800)
    except SystemExit as e:
        req_ok = False
        print(e)
        pic = None
    print(sd_log())
    if not req_ok:
        raise SystemExit("drawing failed")
    print(json.dumps(pic, indent=1), f"{time.time() - t0:.1f} s")
    path = os.path.join(HOME, "Pumpkin", "files", pic["path"])
    with open(path, "rb") as f:
        data = f.read()
    assert data[:3] == b"\xff\xd8\xff", data[:16]
    assert len(data) > 5000, len(data)
    shutil.copy(path, os.path.join(ROOT, "e2e-picture.jpg"))
    print("picture:", len(data), "bytes,", pic["width"], "x", pic["height"])

    step("The image model frees its memory when asked")
    st = call("POST", "/pumpkin/api/images/stop")
    assert st["server"]["state"] == "off", st["server"]
    print("\nAll good.")
finally:
    server.terminate()
    try:
        out, _ = server.communicate(timeout=20)
        print(out[-3000:])
    except subprocess.TimeoutExpired:
        server.kill()
