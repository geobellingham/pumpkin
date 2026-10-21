"""pumpkin command line.

  pumpkin              start Pumpkin in the background (if needed) and open it
  pumpkin serve        run the server in the foreground (what the background service runs)
  pumpkin open         open the window (or your browser)
  pumpkin link         print the sign-in link for this computer and for your phone
  pumpkin doctor       check that everything needed is in place
  pumpkin service install|uninstall|status    run Pumpkin at login (systemd on Linux, launchd on a Mac)
  pumpkin stop         stop the background server and the model
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import APP_ID, APP_NAME, __version__, config



def base_url():
    port = config.Settings()["port"]
    return f"http://127.0.0.1:{port}"


def running():
    try:
        with urllib.request.urlopen(base_url() + "/pumpkin/api/state", timeout=1.5) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        return e.code == 401                    # it is up, we just did not sign in
    except (urllib.error.URLError, OSError):
        return False


def login_url():
    return f"{base_url()}/login?key={config.token()}"


def cmd_serve(args):
    from .core import Core
    from .server import Web
    core = Core()
    web = Web(core)
    try:
        web.serve(args.host)
    except OSError as e:
        print(f"{APP_NAME} could not listen on port {core.settings['port']}: {e}", file=sys.stderr)
        print("Is it already running? Try: pumpkin open", file=sys.stderr)
        sys.exit(1)
    print(f"{APP_NAME} {__version__} is running at {base_url()}", flush=True)
    if core.migrated:
        print("Imported from LLM Studio: " + ", ".join(core.migrated), flush=True)

    def stop(*_):
        core.shutdown()
        web.shutdown()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    web.stopped.wait()


def start_background():
    if running():
        return True
    if service_installed():
        from . import service
        service.start()
    else:
        log = open(os.path.join(config.STATE_DIR, "pumpkin.log"), "a")
        subprocess.Popen([sys.executable, "-m", "pumpkin", "serve"], stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True, cwd=config.HOME, env=_pkg_env())
    for _ in range(60):
        if running():
            return True
        time.sleep(0.25)
    return False


def _pkg_env():
    env = dict(os.environ)
    parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = parent + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def cmd_open(args):
    config.ensure_dirs()
    if not start_background():
        print(f"{APP_NAME} did not start. See {os.path.join(config.STATE_DIR, 'pumpkin.log')}", file=sys.stderr)
        sys.exit(1)
    if not getattr(args, "browser", False):
        try:
            from . import desktop
            if desktop.available():
                desktop.run(login_url())
                return
        except Exception as e:  # noqa: BLE001 - fall back to the browser
            print(f"Window unavailable ({e}); opening your browser.", file=sys.stderr)
    opener = shutil.which("open" if sys.platform == "darwin" else "xdg-open")
    if opener:
        subprocess.Popen([opener, login_url()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        print(f"Open this in your browser: {login_url()}")


def cmd_up(args):
    """Start in the background without opening anything (used after an update)."""
    config.ensure_dirs()
    sys.exit(0 if start_background() else 1)


def cmd_link(args):
    from . import remote
    s = config.Settings()
    print(f"This computer:  {login_url()}")
    st = remote.status(s, config.token())
    if st["link"]:
        print(f"Your phone:     {st['link']}")
    else:
        print("Your phone:     turn on phone access in Pumpkin first (the Phone page).")
    print("Keep these links private: they sign you in.")


def cmd_doctor(args):
    from .core import Core
    from . import doctor
    core = Core()
    marks = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}
    worst = 0
    for c in doctor.check(core):
        worst = max(worst, {"ok": 0, "warn": 1, "fail": 2}[c["status"]])
        print(f"[{marks[c['status']]}] {c['area']}: {c['title']}  {c['detail']}")
        if c["status"] != "ok" and c["fix"]:
            print(f"        fix: {c['fix']}")
        if c["status"] != "ok" and c["command"]:
            print(f"        run: {c['command']}")
    sys.exit(1 if worst == 2 else 0)


def service_installed():
    from . import service
    return service.installed()


def cmd_service(args):
    from . import service
    if args.action == "install":
        was_running = running() and not service.installed()
        if was_running:
            urllib_post("quit")
            time.sleep(1.5)
        service.enable(start_now=True)
        if sys.platform == "darwin":
            print(f"{APP_NAME} now starts when you log in.")
        else:
            print(f"{APP_NAME} now starts when you log in. To keep it running while you are logged out "
                  f"(for scheduled tasks), run: sudo loginctl enable-linger $USER")
    elif args.action == "uninstall":
        service.disable()
        print("Removed the login service.")
    else:
        print(json.dumps(service.status(), indent=2))


def urllib_post(name, body=None):
    req = urllib.request.Request(f"{base_url()}/pumpkin/api/{name}", data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json", "X-Pumpkin": "1",
                                          "Authorization": f"Bearer {config.token()}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode() or "{}")
    except (urllib.error.URLError, OSError):
        return None


def cmd_stop(args):
    if service_installed():
        from . import service
        service.stop()
    elif running():
        urllib_post("quit")
    print(f"{APP_NAME} stopped.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog=APP_ID, description=f"{APP_NAME} {__version__}: run local AI agents "
                                 "with llama.cpp.")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="run the server in the foreground")
    s.add_argument("--host", help="address to listen on (default 127.0.0.1, or 0.0.0.0 in home-network mode)")
    o = sub.add_parser("open", help="open the window or browser")
    o.add_argument("--browser", action="store_true", help="use your browser instead of the app window")
    sub.add_parser("link", help="print the sign-in links")
    sub.add_parser("doctor", help="check the setup")
    sv = sub.add_parser("service", help="run at login (systemd user service)")
    sv.add_argument("action", choices=["install", "uninstall", "status"])
    sub.add_parser("stop", help="stop Pumpkin and the model")
    sub.add_parser("up", help="start Pumpkin in the background without opening it")
    args = ap.parse_args(argv)
    config.ensure_dirs()
    {"serve": cmd_serve, "open": cmd_open, "link": cmd_link, "doctor": cmd_doctor, "service": cmd_service,
     "stop": cmd_stop, "up": cmd_up, None: cmd_open}[args.cmd](args)


if __name__ == "__main__":
    main()
