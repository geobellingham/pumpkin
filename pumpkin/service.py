"""Run Pumpkin at login: a systemd user service on Linux, a launchd agent on a Mac."""
import os
import shutil
import subprocess
import sys

from . import APP_ID, APP_NAME, config

MAC = sys.platform == "darwin"
UNIT = f"{APP_ID}.service"
UNIT_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(config.HOME, ".config"), "systemd", "user")
UNIT_PATH = os.path.join(UNIT_DIR, UNIT)
LABEL = f"app.{APP_ID}"
PLIST_PATH = os.path.join(config.HOME, "Library", "LaunchAgents", f"{LABEL}.plist")


def _domain():
    return f"gui/{os.getuid()}"


def available():
    return bool(shutil.which("launchctl" if MAC else "systemctl"))


def installed():
    return os.path.exists(PLIST_PATH if MAC else UNIT_PATH)


def linger():
    if MAC:
        return True                              # a Mac runs login items while you are logged in; nothing to add
    user = os.environ.get("USER") or ""
    return os.path.exists(f"/var/lib/systemd/linger/{user}") if user else False


def active():
    if not installed():
        return False
    if MAC:
        code, out = _run(["launchctl", "print", f"{_domain()}/{LABEL}"])
        return code == 0 and "state = running" in out
    return _run(["systemctl", "--user", "is-active", UNIT])[1].strip() == "active"


def status():
    return {"available": available(), "installed": installed(), "linger": linger(), "active": active(),
            "linger_command": "" if MAC else "sudo loginctl enable-linger $USER"}


def _run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)


def _parent():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write_unit():
    os.makedirs(UNIT_DIR, exist_ok=True)
    with open(UNIT_PATH, "w") as f:
        f.write(f"""[Unit]
Description={APP_NAME}: local AI agents with llama.cpp
After=network-online.target

[Service]
Type=simple
Environment=PYTHONPATH={_parent()}
ExecStart={sys.executable} -m pumpkin serve
Restart=on-failure
RestartSec=3
KillMode=mixed
TimeoutStopSec=30

[Install]
WantedBy=default.target
""")
    _run(["systemctl", "--user", "daemon-reload"])


def write_plist():
    import plistlib
    os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
    log = os.path.join(config.STATE_DIR, "pumpkin.log")
    plist = {"Label": LABEL, "ProgramArguments": [sys.executable, "-m", "pumpkin", "serve"],
             "EnvironmentVariables": {"PYTHONPATH": _parent(),
                                      "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
             "WorkingDirectory": config.HOME, "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False},
             "StandardOutPath": log, "StandardErrorPath": log, "ProcessType": "Interactive"}
    with open(PLIST_PATH, "wb") as f:
        plistlib.dump(plist, f)


def enable(start_now=True):
    """Install and enable. When called from a running non-service Pumpkin, start_now=False: the
    service takes over at next login (starting it now would clash on the port)."""
    if not available():
        raise RuntimeError("launchd is not available." if MAC else "systemd is not available on this system.")
    if MAC:
        write_plist()
        if start_now:
            _run(["launchctl", "bootout", f"{_domain()}/{LABEL}"])
            code, out = _run(["launchctl", "bootstrap", _domain(), PLIST_PATH])
            if code != 0:
                raise RuntimeError(out.strip()[-300:] or "launchctl failed")
        return
    write_unit()
    code, out = _run(["systemctl", "--user", "enable"] + (["--now"] if start_now else []) + [UNIT])
    if code != 0:
        raise RuntimeError(out.strip()[-300:] or "systemctl failed")


def disable():
    if MAC:
        _run(["launchctl", "bootout", f"{_domain()}/{LABEL}"])
        if os.path.exists(PLIST_PATH):
            os.remove(PLIST_PATH)
        return
    _run(["systemctl", "--user", "disable", UNIT])
    if os.path.exists(UNIT_PATH):
        os.remove(UNIT_PATH)
    _run(["systemctl", "--user", "daemon-reload"])


def start():
    if MAC:
        code, _ = _run(["launchctl", "kickstart", f"{_domain()}/{LABEL}"])
        if code != 0:
            _run(["launchctl", "bootstrap", _domain(), PLIST_PATH])
    else:
        _run(["systemctl", "--user", "start", UNIT])


def stop():
    if MAC:
        _run(["launchctl", "bootout", f"{_domain()}/{LABEL}"])     # stays installed; loads again at next login
    else:
        _run(["systemctl", "--user", "stop", UNIT])


def running_under_service():
    if MAC:
        return os.environ.get("XPC_SERVICE_NAME") == LABEL
    return bool(os.environ.get("INVOCATION_ID")) and installed()
