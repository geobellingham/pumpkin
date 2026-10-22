"""Pumpkin inside the Android app. The app (Kotlin, with Python through Chaquopy) calls start() once and
shows the returned address in its window. Everything else is the same Pumpkin as on a computer.

Import nothing from Pumpkin at the top: its folders are decided when pumpkin.config is first imported,
so the environment has to point at the app's storage before that."""
import os
import socket
import sys
import threading

_running = {}


def _free_port(preferred):
    """Another app on the phone may already use a port; any free one will do (it is only used inside the app)."""
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


def start(files_dir, native_dir, cache_dir=""):
    """Starts Pumpkin and returns the sign-in address for the app's window. Safe to call again.
    If it fails, the reason is also written to files/start-error.txt (the app shows it too)."""
    try:
        return _start(files_dir, native_dir, cache_dir)
    except BaseException:
        import traceback
        text = traceback.format_exc()
        try:
            with open(os.path.join(files_dir, "start-error.txt"), "w") as f:
                f.write(text)
        except OSError:
            pass
        print(text, file=sys.stderr, flush=True)
        raise


def pick_server(native_dir, cpuinfo="/proc/cpuinfo"):
    """The fast llama-server needs the ARM dot-product and half-float instructions (phones since about 2018).
    Older ones, like the Galaxy S8, crash on it, so they get the basic build when the app has one."""
    fast = os.path.join(native_dir, "libllama_server.so")
    basic = os.path.join(native_dir, "libllama_server_basic.so")
    try:
        with open(cpuinfo) as f:
            text = f.read()
    except OSError:
        return fast
    feats = set()
    for line in text.splitlines():
        if line.lower().startswith("features"):
            feats.update(line.split(":", 1)[-1].split())
    if feats and not ({"asimddp", "asimdhp"} <= feats) and os.path.exists(basic):
        return basic
    return fast


def _start(files_dir, native_dir, cache_dir=""):
    if "url" in _running:
        return _running["url"]
    home = os.path.join(files_dir, "home")
    os.makedirs(home, exist_ok=True)
    os.environ.update(HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"),
                      XDG_DATA_HOME=os.path.join(home, ".local", "share"),
                      XDG_STATE_HOME=os.path.join(home, ".local", "state"))
    if cache_dir:
        os.environ["TMPDIR"] = cache_dir
    try:
        import certifi                                    # HTTPS for Hugging Face and GitHub
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass

    from . import agent, config
    s = config.Settings()
    s.update({
        "llama_server": pick_server(native_dir),            # built into the app
        "port": _free_port(int(s["port"])), "llama_port": _free_port(int(s["llama_port"])),
        # the phone is only reachable from outside when it shares its model with your other Pumpkins
        "remote": "lan" if s["share"] else "off", "keep_awake": False, "autostart_model": False,
    })
    agent.NOTIFY_HOOK = _notify
    from .core import Core
    from .server import Web
    core = Core()
    web = Web(core)
    web.serve("0.0.0.0" if s["share"] else "127.0.0.1")
    _running.update(core=core, web=web, url=f"http://127.0.0.1:{s['port']}/login?key={config.token()}")
    return _running["url"]


def stop():
    core, web = _running.pop("core", None), _running.pop("web", None)
    _running.pop("url", None)
    if core:
        core.shutdown()
    if web:
        threading.Thread(target=web.shutdown, daemon=True).start()


def _notify(title, body):
    """Task notifications through the app (Android notifications)."""
    try:
        from java import jclass                           # Chaquopy's bridge to the Kotlin side
        jclass("app.pumpkin.PumpkinService").notifyTask(title, body or "")
    except Exception:  # noqa: BLE001 - notifications are a nicety
        pass

