"""pumpkin command line.

  pumpkin              start Pumpkin in the background (if needed) and open it
  pumpkin serve        run the server in the foreground (what the background service runs)
  pumpkin open         open the window (or your browser)
  pumpkin link         print the sign-in link for this computer and for your phone
  pumpkin doctor       check that everything needed is in place
  pumpkin service install|uninstall|status    run Pumpkin at login (systemd, launchd, or Windows sign-in)
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
        with config.local_open(base_url() + "/pumpkin/api/state", timeout=1.5) as r:
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
    if hasattr(signal, "SIGBREAK"):              # Windows: Ctrl+Break, and how the installer asks it to stop
        signal.signal(signal.SIGBREAK, stop)
    while not web.stopped.wait(1):               # a timeout keeps Ctrl+C working on Windows
        pass


def start_background():
    if running():
        return True
    if service_installed() and os.name != "nt":
        from . import service
        service.start()
    else:
        start_background_direct()
    for _ in range(60):
        if running():
            return True
        time.sleep(0.25)
    return False


def start_background_direct():
    """Start the server as a separate background process."""
    log = open(os.path.join(config.STATE_DIR, "pumpkin.log"), "a", encoding="utf-8")
    if os.name == "nt":
        # a hidden console that llama-server and other helpers share, so no black windows pop up
        subprocess.Popen([console_python(), "-X", "utf8", "-m", "pumpkin", "serve"], stdout=log,
                         stderr=subprocess.STDOUT, cwd=config.HOME, env=_pkg_env(),
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        subprocess.Popen([sys.executable, "-m", "pumpkin", "serve"], stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True, cwd=config.HOME, env=_pkg_env())


def console_python():
    """python.exe next to pythonw.exe (the Start menu shortcut runs pythonw, which has no console to share)."""
    exe = sys.executable
    d, name = os.path.split(exe)
    if name.lower() == "pythonw.exe" and os.path.exists(os.path.join(d, "python.exe")):
        return os.path.join(d, "python.exe")
    return exe


def close_window_browser(profile):
    """Edge can keep running in the background after its window is closed (startup boost, a crash, an update of
    Pumpkin underneath it). A new window then attaches to that old process and can stay empty. Pumpkin's window has
    its own profile, so only those Edge processes are closed here, never your normal browser."""
    script = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" | "
              "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($env:PUMPKIN_PROFILE) } | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                       timeout=20, env=dict(os.environ, PUMPKIN_PROFILE=profile),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


def clean_window_profile(profile):
    """Pumpkin's window keeps its own Edge profile. Older versions installed a service worker there, and Edge keeps
    using it (and its cached copy of the app) even after Pumpkin stops installing one: the window then stays empty
    until a hard reload. So the service worker folder is removed before every start, and the file caches after each
    Pumpkin update. Chats and settings (Local Storage, IndexedDB) are left alone."""
    default = os.path.join(profile, "Default")
    if not os.path.isdir(default):
        return
    marker = os.path.join(profile, "pumpkin-version")
    try:
        seen = open(marker).read().strip()
    except OSError:
        seen = ""
    gone = ["Service Worker"]
    if seen != __version__:
        gone += ["Cache", "Code Cache", "GPUCache"]
    for name in gone:
        path = os.path.join(default, name)
        for _ in range(10):                      # Edge may still be letting go of the files for a moment
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                break
            time.sleep(0.3)
    try:
        with open(marker, "w") as f:
            f.write(__version__)
    except OSError:
        pass


def windows_window(url):
    """Edge (on every Windows 10 and 11) or Chrome in app mode: a window of its own, no tabs or address bar.
    A separate profile keeps Pumpkin's sign-in apart from your browsing."""
    cands = [shutil.which("msedge"), shutil.which("chrome")]
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    exe = next((c for c in cands if c and os.path.exists(c)), None)
    if not exe:
        return False
    profile = os.path.join(config.DATA_DIR, "window")
    close_window_browser(profile)
    clean_window_profile(profile)
    subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
                      "--no-default-browser-check", "--window-size=1280,860"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


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
    if not getattr(args, "browser", False) and sys.platform == "darwin" and mac_window():
        # the Mac app starts Pumpkin and signs in by itself (pumpkin up, pumpkin url)
        subprocess.Popen(["open", mac_window()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    if os.name == "nt":
        if getattr(args, "browser", False) or not windows_window(login_url()):
            os.startfile(login_url())                      # noqa: S606 - the default browser
        return
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


def mac_window():
    """~/Applications/Pumpkin.app when it holds the native window (not just a launcher script)."""
    app = os.path.join(config.HOME, "Applications", "Pumpkin.app")
    exe = os.path.join(app, "Contents", "MacOS", "Pumpkin")
    try:
        with open(exe, "rb") as f:
            native = f.read(4) in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")   # a Mach-O program
    except OSError:
        return None
    return app if native else None


def cmd_url(args):
    """Prints this computer's sign-in link (the Mac window uses it)."""
    print(login_url())


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
        if sys.platform in ("darwin", "win32"):
            print(f"{APP_NAME} now starts when you log in.")
        else:
            print(f"{APP_NAME} now starts when you log in. To keep it running while you are logged out "
                  f"(for scheduled tasks), run: sudo loginctl enable-linger $USER")
    elif args.action == "uninstall":
        service.disable()
        print("Removed the login service.")
    else:
        print(json.dumps(service.status(), indent=2))


def cmd_code(args):
    from . import code_cli
    code_cli.run(args)


def urllib_post(name, body=None):
    req = urllib.request.Request(f"{base_url()}/pumpkin/api/{name}", data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json", "X-Pumpkin": "1",
                                          "Authorization": f"Bearer {config.token()}"}, method="POST")
    try:
        with config.local_open(req, timeout=10) as r:
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
    if os.name == "nt" and not sys.flags.utf8_mode:
        # Windows reads text files in the local code page; Pumpkin's files are UTF-8. Python started without
        # -X utf8 (a plain `python -m pumpkin`) runs again in UTF-8 mode, and so does everything it starts.
        os.environ["PYTHONUTF8"] = "1"
        sys.exit(subprocess.call([sys.executable, "-X", "utf8", "-m", "pumpkin"] + list(sys.argv[1:] if argv is None
                                                                                     else argv)))
    os.environ.setdefault("PYTHONUTF8", "1")      # helpers it starts (search, devices) too
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
    sub.add_parser("url", help="print this computer's sign-in link")
    c = sub.add_parser("code", help="Pumpkin Code: a coding agent in this folder, with your local model")
    c.add_argument("prompt", nargs="*", help="what to do (or type it once it starts)")
    c.add_argument("-d", "--dir", help="the project folder (default: this one)")
    c.add_argument("-c", "--continue", dest="cont", action="store_true", help="continue the last session here")
    c.add_argument("-m", "--model", help="the preset to use (part of its name)")
    c.add_argument("--mode", choices=["ask", "edits", "auto"], default="ask",
                   help="ask before changes (default), change files freely, or never ask")
    args = ap.parse_args(argv)
    config.ensure_dirs()
    {"serve": cmd_serve, "open": cmd_open, "link": cmd_link, "doctor": cmd_doctor, "service": cmd_service,
     "stop": cmd_stop, "up": cmd_up, "url": cmd_url, "code": cmd_code, None: cmd_open}[args.cmd](args)


if __name__ == "__main__":
    main()
