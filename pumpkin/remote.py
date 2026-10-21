"""Reaching Pumpkin from a phone: over Tailscale (anywhere) or the home network."""
import json
import os
import shutil
import socket
import subprocess

MAC_APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def ts():
    """The tailscale command: on the PATH, or inside the Mac app (which does not add itself to the PATH)."""
    w = shutil.which("tailscale")
    if w:
        return w
    return MAC_APP_CLI if os.path.exists(MAC_APP_CLI) else None


def _run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", str(e)


def tailscale_host():
    if not ts():
        return None
    code, out, _ = _run([ts(), "status", "--json"])
    if code != 0:
        return None
    try:
        return json.loads(out)["Self"]["DNSName"].rstrip(".") or None
    except (ValueError, KeyError, TypeError):
        return None


def tailscale_on(port):
    """Publishes Pumpkin at https://<machine>.<tailnet>.ts.net (tailnet only)."""
    if not ts():
        raise RuntimeError("Tailscale is not installed. Install it on this computer and your phone first.")
    code, out, err = _run([ts(), "serve", "--bg", "--https=443", f"localhost:{port}"])
    if code != 0:
        msg = err or out
        if "access denied" in msg.lower() or "operator" in msg.lower():
            raise RuntimeError("Tailscale needs permission once. Run: sudo tailscale set --operator=$USER")
        if "https" in msg.lower() and "enable" in msg.lower():
            raise RuntimeError("Turn on HTTPS certificates in the Tailscale admin page (DNS settings), then retry. "
                               + msg[:200])
        raise RuntimeError(msg[:400] or "tailscale serve failed")
    host = tailscale_host()
    if not host:
        raise RuntimeError("Tailscale is on, but this machine has no tailnet name yet. Turn on MagicDNS.")
    return f"https://{host}"


def tailscale_off():
    if ts():
        _run([ts(), "serve", "--https=443", "off"])


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))          # no packet is sent; this picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def status(settings, token):
    mode = settings["remote"]
    port = int(settings["port"])
    base = None
    if mode == "tailscale":
        host = tailscale_host()
        base = f"https://{host}" if host else None
    elif mode == "lan":
        ip = lan_ip()
        base = f"http://{ip}:{port}" if ip else None
    return {"mode": mode, "base": base, "link": f"{base}/login?key={token}" if base else None,
            "tailscale_installed": bool(ts()), "lan_ip": lan_ip()}
