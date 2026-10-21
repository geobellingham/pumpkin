"""Run Pumpkin at login as a systemd user service."""
import os
import shutil
import subprocess
import sys

from . import APP_ID, APP_NAME, config

UNIT = f"{APP_ID}.service"
UNIT_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(config.HOME, ".config"), "systemd", "user")
UNIT_PATH = os.path.join(UNIT_DIR, UNIT)


def available():
    return bool(shutil.which("systemctl"))


def installed():
    return os.path.exists(UNIT_PATH)


def linger():
    user = os.environ.get("USER") or ""
    return os.path.exists(f"/var/lib/systemd/linger/{user}") if user else False


def status():
    return {"available": available(), "installed": installed(), "linger": linger(),
            "active": installed() and _run(["systemctl", "--user", "is-active", UNIT])[1].strip() == "active",
            "linger_command": "sudo loginctl enable-linger $USER"}


def _run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)


def write_unit():
    os.makedirs(UNIT_DIR, exist_ok=True)
    parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(UNIT_PATH, "w") as f:
        f.write(f"""[Unit]
Description={APP_NAME}: local AI agents with llama.cpp
After=network-online.target

[Service]
Type=simple
Environment=PYTHONPATH={parent}
ExecStart={sys.executable} -m pumpkin serve
Restart=on-failure
RestartSec=3
KillMode=mixed
TimeoutStopSec=30

[Install]
WantedBy=default.target
""")
    _run(["systemctl", "--user", "daemon-reload"])


def enable(start_now=True):
    """Install and enable. When called from a running non-service Pumpkin, start_now=False: the
    service takes over at next login (starting it now would clash on the port)."""
    if not available():
        raise RuntimeError("systemd is not available on this system.")
    write_unit()
    code, out = _run(["systemctl", "--user", "enable"] + (["--now"] if start_now else []) + [UNIT])
    if code != 0:
        raise RuntimeError(out.strip()[-300:] or "systemctl failed")


def disable():
    _run(["systemctl", "--user", "disable", UNIT])
    if os.path.exists(UNIT_PATH):
        os.remove(UNIT_PATH)
    _run(["systemctl", "--user", "daemon-reload"])


def running_under_service():
    return bool(os.environ.get("INVOCATION_ID")) and installed()
