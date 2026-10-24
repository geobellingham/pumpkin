"""`pumpkin code`: Pumpkin Code in a terminal, in the folder you run it from.

It talks to the Pumpkin running on this computer (starting it if needed), so the session also shows on the Code page
and can be picked up there, on the phone or here again with `pumpkin code -c`."""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import APP_NAME, config

HELP = """Type what you want done, in your own words. While it works, Ctrl+C stops it.
  /mode ask      ask before changing files and before commands (the start)
  /mode edits    change files without asking, ask before commands
  /mode auto     never ask (risky commands still ask)
  /undo          take back the last file change
  /todo          show the plan
  /model NAME    use another preset (/model to list them)
  /new           start a new session in this folder
  /quit          leave (the session is kept; continue with pumpkin code -c)"""


class Term:
    def __init__(self):
        self.color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
        if os.name == "nt" and self.color:
            os.system("")                         # turns on ANSI colors in the Windows console

    def c(self, code, text):
        return f"\x1b[{code}m{text}\x1b[0m" if self.color else text

    def dim(self, t):
        return self.c("2", t)

    def bold(self, t):
        return self.c("1", t)

    def orange(self, t):
        return self.c("38;5;208", t)

    def red(self, t):
        return self.c("31", t)

    def green(self, t):
        return self.c("32", t)


class Api:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(self, method, path, body=None, timeout=30):
        req = urllib.request.Request(f"{self.base}/pumpkin/api/{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "X-Pumpkin": "1",
                                              "Authorization": f"Bearer {config.token()}"})
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode() or "{}").get("error")
            except ValueError:
                msg = None
            raise RuntimeError(msg or f"Pumpkin answered {e.code}")


def summarize(ev):
    """One short line for a tool call."""
    name, a = ev.get("name"), ev.get("args") or {}
    arg = a.get("path") or a.get("pattern") or a.get("command") or a.get("query") or a.get("url") or ""
    return f"{name}({str(arg)[:100]})" if arg else str(name)


class Session:
    def __init__(self, api, term, sid):
        self.api, self.t, self.id = api, term, sid
        self.after = 0
        self.streamed = ""             # what of the answer being written was printed already
        self.thinking_shown = False
        self.asked_diff = None

    def show(self, ev):
        t, k = self.t, ev["kind"]
        if k == "assistant":
            text = ev.get("text") or ""
            if self.streamed and text.startswith(self.streamed):
                sys.stdout.write(text[len(self.streamed):] + "\n")
            elif text:
                if self.streamed:
                    sys.stdout.write("\n")
                print(t.orange("● ") + text)
            elif self.streamed:
                sys.stdout.write("\n")
            self.streamed, self.thinking_shown = "", False
        elif k == "tool":
            if ev.get("name") == "todo" and ev.get("ok"):
                return                                   # the list itself was just shown
            mark = t.green("●") if ev.get("ok") else t.red("●")
            print(f"{mark} {t.bold(summarize(ev))}")
            if ev.get("diff") and ev.get("diff") == self.asked_diff:
                self.asked_diff = None                   # shown with the question already
            elif ev.get("diff") and not ev.get("declined"):
                print(self.diff(ev["diff"]))
            else:
                lines = (ev.get("result") or "").splitlines()
                if ev.get("name") in ("bash",) or not ev.get("ok"):
                    body = lines[-12:] if len(lines) > 12 else lines
                    if len(lines) > 12:
                        print(t.dim(f"  ⎿ ... {len(lines) - 12} lines above"))
                    for l in body:
                        print(t.dim("  ⎿ " + l[:200]))
                elif ev.get("name") != "todo":
                    print(t.dim(f"  ⎿ {len(lines)} lines" if len(lines) != 1 else "  ⎿ " + lines[0][:160]))
        elif k == "todo":
            self.todo(ev.get("items") or [])
        elif k == "info":
            print(t.dim(ev.get("text", "")))
        elif k == "compact":
            print(t.dim("(summarised the earlier part of the session to fit the model's context)"))
        elif k == "error":
            print(t.red("Error: " + ev.get("text", "")))

    def diff(self, d):
        out = []
        for l in d.splitlines()[2:60]:
            if l.startswith("+"):
                out.append("  " + self.t.green(l))
            elif l.startswith("-"):
                out.append("  " + self.t.red(l))
            elif l.startswith("@@"):
                out.append("  " + self.t.dim(l))
            else:
                out.append("  " + l)
        n = len(d.splitlines())
        if n > 62:
            out.append(self.t.dim(f"  ... {n - 62} more lines"))
        return "\n".join(out)

    def todo(self, items):
        for i in items:
            print(("  ☒ " + self.t.dim(i["text"])) if i.get("done") else ("  ☐ " + i["text"]))

    def ask(self, p):
        t = self.t
        if p["kind"] == "edit":
            self.asked_diff = p.get("diff")
            print(t.bold(("Create " if p.get("new_file") else "Change ") + p.get("path", "") + "?"))
            print(self.diff(p.get("diff") or ""))
        else:
            warn = t.red(" (this one is hard to take back)") if p.get("danger") else ""
            print(t.bold("Run this?") + warn + "\n  " + t.orange(p.get("command", "")))
        opts = "[y]es / [a]lways" + (" for this command" if p["kind"] == "command" else " for file changes") + \
            " / [n]o, or say what to do instead: "
        try:
            ans = input(opts).strip()
        except EOFError:
            ans = "n"
        low = ans.lower()
        allow = "yes" if low in ("y", "yes", "") else "always" if low in ("a", "always") else "no"
        note = "" if allow != "no" or low in ("n", "no") else ans
        self.api("POST", f"code/{self.id}/answer", {"id": p["id"], "allow": allow, "note": note})

    def follow(self):
        """Prints what happens until the session is idle again."""
        asked = None
        while True:
            try:
                v = self.api("GET", f"code/{self.id}?after={self.after}")
            except (RuntimeError, OSError) as e:
                print(self.t.red(f"Lost Pumpkin: {e}"))
                time.sleep(2)
                continue
            for ev in v["events"]:
                self.after = max(self.after, ev["n"])
                self.show(ev)
            live = v.get("live")
            if live:
                th = live.get("thinking") or ""
                if th and not self.thinking_shown and not live.get("text"):
                    print(self.t.dim("✻ thinking..."))
                    self.thinking_shown = True
                text = live.get("text") or ""
                if text.startswith(self.streamed) and len(text) > len(self.streamed):
                    if not self.streamed:
                        sys.stdout.write(self.t.orange("● "))
                    sys.stdout.write(text[len(self.streamed):])
                    sys.stdout.flush()
                    self.streamed = text
            p = v.get("pending")
            if p and p["id"] != asked:
                asked = p["id"]
                self.ask(p)
                continue
            if v["state"] in ("idle", "error") and not live and not p:
                return v
            time.sleep(0.15)


def run(args):
    from . import __main__ as m
    term = Term()
    if not m.running():
        print(term.dim(f"Starting {APP_NAME}..."))
        if not m.start_background():
            print(term.red(f"{APP_NAME} did not start. Run `pumpkin doctor` to see why."))
            sys.exit(1)
    api = Api(m.base_url())
    root = os.path.realpath(args.dir or os.getcwd())
    try:
        info = api("GET", "code")
    except RuntimeError as e:
        print(term.red(str(e)))
        sys.exit(1)
    preset = None
    if args.model:
        hit = [p for p in info["presets"] if args.model.lower() in (p["name"] or "").lower()]
        if not hit:
            print(term.red(f"No preset matches {args.model}. Presets: " + ", ".join(p["name"] for p in info["presets"])))
            sys.exit(1)
        preset = hit[0]["id"]
    sid = None
    if args.cont:
        mine = [s for s in info["sessions"] if os.path.realpath(s["root"]) == root]
        if mine:
            sid = mine[0]["id"]
    try:
        if not sid:
            sid = api("POST", "code", {"root": root, "mode": args.mode, "preset": preset})["id"]
    except RuntimeError as e:
        print(term.red(str(e)))
        sys.exit(1)
    s = Session(api, term, sid)
    model = info.get("running") or "the default preset"
    print(term.orange("🎃 Pumpkin Code") + term.dim(f"  {root}  ·  {model}  ·  /help for commands"))
    if args.cont:
        v = api("GET", f"code/{sid}")
        for ev in v["events"][-12:]:
            s.show(ev)
        s.after = v["last"]
    first = " ".join(args.prompt).strip() if args.prompt else ""
    while True:
        if first:
            line, first = first, ""
            print(term.bold("> ") + line)
        else:
            try:
                line = input(term.bold("> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
        if not line:
            continue
        if line.startswith("/"):
            cmd, _, rest = line[1:].partition(" ")
            rest = rest.strip()
            try:
                if cmd in ("quit", "exit", "q"):
                    return
                if cmd == "help":
                    print(HELP)
                elif cmd == "mode":
                    print(term.dim(f"Mode: {api('POST', f'code/{sid}/mode', {'mode': rest})['mode']}"))
                elif cmd == "undo":
                    print(term.dim(f"Put {api('POST', f'code/{sid}/undo', {})['path']} back as it was."))
                elif cmd == "todo":
                    s.todo(api("GET", f"code/{sid}?after=999999999")["todo"] or [])
                elif cmd == "new":
                    sid = api("POST", "code", {"root": root, "mode": args.mode, "preset": preset})["id"]
                    s = Session(api, term, sid)
                    print(term.dim("New session."))
                elif cmd == "model":
                    presets = api("GET", "code")["presets"]
                    hit = [p for p in presets if rest and rest.lower() in (p["name"] or "").lower()]
                    if not hit:
                        print("Presets: " + ", ".join(p["name"] for p in presets))
                    else:
                        api("POST", f"code/{sid}/model", {"preset": hit[0]["id"]})
                        print(term.dim(f"Using {hit[0]['name']}."))
                else:
                    print(f"Unknown command /{cmd}. /help lists them.")
            except RuntimeError as e:
                print(term.red(str(e)))
            continue
        try:
            api("POST", f"code/{sid}/send", {"text": line, "after": s.after})
        except RuntimeError as e:
            print(term.red(str(e)))
            continue
        stopper = threading.Event()
        while True:
            try:
                s.follow()
                break
            except KeyboardInterrupt:
                if stopper.is_set():
                    raise
                stopper.set()
                print(term.dim("\nStopping..."))
                try:
                    api("POST", f"code/{sid}/stop", {})
                except RuntimeError:
                    pass
