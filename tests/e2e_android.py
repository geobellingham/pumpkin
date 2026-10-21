"""End to end in an Android emulator (CI): install the APK, open Pumpkin, download a tiny real model from
Hugging Face on the "phone", start it and chat. Uses adb; needs the debug APK (for run-as).

    python tests/e2e_android.py path/to/app-debug.apk
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

APK = sys.argv[1]
PKG = "app.pumpkin"
LOCAL = 18490
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def adb(*args, check=True, text=True):
    r = subprocess.run(["adb", *args], capture_output=True, text=text, timeout=300)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: {r.stdout} {r.stderr}")
    return r.stdout


def step(msg):
    print(f"\n== {msg}", flush=True)


def wait(cond, what, timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = cond()
        except Exception:  # noqa: BLE001 - not there yet
            v = None
        if v:
            return v
        time.sleep(2)
    print(adb("logcat", "-d", "-t", "300", check=False)[-6000:])
    raise SystemExit(f"timed out waiting for {what}")


step("Install and open the app")
adb("install", "-r", "-g", APK)                      # -g grants the notification permission
adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")
home = "files/home/.config/pumpkin"
settings = wait(lambda: json.loads(adb("shell", "run-as", PKG, "cat", f"{home}/settings.json")), "Pumpkin's settings", 300)
token = wait(lambda: adb("shell", "run-as", PKG, "cat", f"{home}/token").strip(), "the access key", 300)
port = settings["port"]
adb("forward", f"tcp:{LOCAL}", f"tcp:{port}")
print("Pumpkin is on port", port, "on the phone")


def call(method, path, body=None, timeout=60):
    req = urllib.request.Request(f"http://127.0.0.1:{LOCAL}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "X-Pumpkin": "1",
                                          "Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read()[:500]!r}")


st = wait(lambda: call("GET", "/pumpkin/api/state"), "Pumpkin to answer", 300)
print("platform:", st.get("platform"), "version:", st.get("version"))
assert st.get("platform") == "android", st.get("platform")
hw = call("GET", "/pumpkin/api/hardware")
print(json.dumps({"main_gpu": hw.get("main_gpu"), "ram_total": hw.get("ram_total"), "cpu": hw.get("cpu")}, indent=1))
info = call("GET", "/pumpkin/api/llama")
print("llama-server:", info["info"].get("version"), info["info"].get("ok"), info["path"])
assert info["info"].get("ok"), info["info"]

step("Download a tiny real model on the phone")
dl = call("POST", "/pumpkin/api/downloads", {"repo": "ggml-org/models", "files": ["tinyllamas/stories260K.gguf"]})
wait(lambda: next((j for j in call("GET", "/pumpkin/api/downloads")["jobs"] if j["id"] == dl["id"]
                   and j["state"] not in ("queued", "downloading")), None), "the model", 600)
lib = call("GET", "/pumpkin/api/models?rescan=1")["models"]
model = next(m for m in lib if m["file"].startswith("stories260K"))
print(model["file"], model["fit"])

step("Start it and chat")
preset = call("POST", "/pumpkin/api/presets/from-model", {"path": model["path"], "name": "Tiny"})
call("PUT", f"/pumpkin/api/presets/{preset['id']}", {"ctx": 256, "tools": False, "web": False})
call("POST", "/pumpkin/api/llama/start", {"preset": preset["id"]})
srv = wait(lambda: (s := call("GET", "/pumpkin/api/state")["server"])["state"] in ("ready", "error") and s, "the model", 300)
if srv["state"] != "ready":
    print(call("GET", "/pumpkin/api/llama/log?n=80")["log"])
    raise SystemExit(f"the model did not start: {srv['error']}")
r = call("POST", "/v1/chat/completions", {"model": "Tiny", "max_tokens": 24,
                                          "messages": [{"role": "user", "content": "Once upon a time"}]}, 180)
text = r["choices"][0]["message"]["content"]
print("the phone's model says:", repr(text))
assert text.strip()
print("speed:", r.get("timings", {}).get("predicted_per_second"), "tokens/s")

step("Doctor")
for c in call("GET", "/pumpkin/api/doctor")["checks"]:
    print(f"[{c['status']:4}] {c['area']}: {c['title']}  {c['detail']}")

step("The window")
time.sleep(3)
png = subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True, timeout=60).stdout
open("android-screen.png", "wb").write(png)
print("screenshot", len(png), "bytes")
print("\nAll good.")
