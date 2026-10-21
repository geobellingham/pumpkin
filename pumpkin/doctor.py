"""Health checks: what works, what is missing, and how to fix it."""
import os
import shutil
import socket
import subprocess
import sys

from . import hardware, llama, remote

MAC = sys.platform == "darwin"
ANDROID = hardware.ANDROID


def _run(cmd, timeout=8):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)


def check(core):
    s = core.settings
    out = []

    def add(area, title, status, detail="", fix="", command=""):
        out.append({"area": area, "title": title, "status": status, "detail": detail, "fix": fix,
                    "command": command})

    # ---- llama-server
    binary = llama.find_server(s)
    if not binary:
        add("Model server", "llama-server", "fail", "Not found.",
            "Download it under Settings > Model server, or set the path to your own build.")
    else:
        info = llama.probe(binary) or {}
        if not info.get("ok"):
            add("Model server", "llama-server", "fail", f"{binary} does not run: {info.get('error')}",
                "If it is a downloaded build, the GPU runtime may be missing (see below). Or download it again.")
        else:
            backends = ", ".join(info.get("backends") or []) or "unknown"
            add("Model server", "llama-server", "ok", f"{info.get('version') or 'unknown version'} ({backends})",
                command=binary)
            if "--agent" not in (info.get("flags") or []):
                add("Model server", "Agent tools", "warn", "This llama-server has no --agent option, so models "
                    "cannot use the sandbox or MCP tools.", "Update llama-server under Settings.")

    # ---- GPU
    hw = hardware.summary()
    g = hw.get("main_gpu")
    if not g:
        if MAC:
            add("Hardware", "GPU", "warn", "This Mac has an Intel processor, so models run on the CPU, which is slow. "
                "Small models (up to about 8B) still work.")
        else:
            add("Hardware", "GPU", "warn", "No GPU found. Models will run on the CPU, which is slow.",
                "If you have a GPU, install its driver.")
    else:
        if g.get("unified"):
            detail = (f"{g['name']}, shares {hw['ram_total']:.0f} GB of memory and can use about "
                      f"{g['vram_total']:.0f} GB of it")
        else:
            detail = f"{g['name']}, {g['vram_total']:.0f} GB VRAM" if g.get("vram_total") else g["name"]
        add("Hardware", "GPU", "ok", detail)
        if g["vendor"] in ("AMD", "Intel") or (g["vendor"] == "NVIDIA" and not shutil.which("nvidia-smi")):
            if not hw.get("vulkan"):
                add("Hardware", "Vulkan", "fail", "The Vulkan loader is missing, so the Vulkan build cannot use "
                    "the GPU.", "Install the Vulkan drivers.",
                    "sudo apt install libvulkan1 mesa-vulkan-drivers")
            else:
                add("Hardware", "Vulkan", "ok", "Vulkan loader installed")
    add("Hardware", "Memory", "ok", f"{hw['ram_total']:.0f} GB RAM, {hw['cpu']['physical_cores']} CPU cores")

    # ---- models
    models = [m for m in core.library.scan() if m["kind"] == "model"]
    if not models:
        add("Models", "Model files", "warn", "No models yet.", "Download one under Models.")
    else:
        add("Models", "Model files", "ok", f"{len(models)} model{'s' if len(models) != 1 else ''} in "
            f"{len(core.library.dirs())} folder{'s' if len(core.library.dirs()) != 1 else ''}")
    free = hardware.disk_free(os.path.expanduser(s["download_dir"]))
    if free is not None:
        add("Models", "Disk space", "ok" if free > 30 else "warn", f"{free:.0f} GB free in the download folder",
            "" if free > 30 else "Models are 5–40 GB each. Free some space or pick another download folder.")

    # ---- sandbox
    if ANDROID:
        add("Agent tools", "Commands and files", "ok", "Phones have no sandbox for running commands, so the AI "
            "chats and runs your tasks without them.")
    elif not shutil.which("docker"):
        if MAC:
            add("Agent tools", "Docker sandbox", "warn", "Docker is not installed. Tools would run directly on this "
                "computer, so Pumpkin keeps them off until Docker is available.",
                "Install Docker Desktop (or OrbStack) and open it once.", "brew install --cask docker")
        else:
            add("Agent tools", "Docker sandbox", "warn", "Docker is not installed. Tools would run directly on this "
                "computer, so Pumpkin keeps them off until Docker is available.",
                "Install Docker, then log out and back in.", "sudo apt install docker.io && sudo usermod -aG docker $USER")
    else:
        code, txt = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
        if code == 0:
            img = s["sandbox_image"]
            c2, _ = _run(["docker", "image", "inspect", img])
            add("Agent tools", "Docker sandbox", "ok", f"Docker {txt.strip()}; sandbox image {img}"
                + ("" if c2 == 0 else " will download on first use"))
        elif MAC:
            add("Agent tools", "Docker sandbox", "fail", "Docker is installed but not running.",
                "Open Docker Desktop and wait until it says it is running.", "open -a Docker")
        elif "permission denied" in txt.lower():
            in_group = False
            try:
                import grp                         # not on every platform (Android has none)
                in_group = os.environ.get("USER", "") in grp.getgrnam("docker").gr_mem
            except (ImportError, KeyError):
                pass
            if in_group:
                add("Agent tools", "Docker sandbox", "fail", "You were added to the docker group, but this "
                    "session started before that.", "Log out and back in (or restart), then restart Pumpkin.")
            else:
                add("Agent tools", "Docker sandbox", "fail", "Your user cannot use Docker.",
                    "Add yourself to the docker group, then log out and back in.",
                    "sudo usermod -aG docker $USER")
        else:
            add("Agent tools", "Docker sandbox", "fail", "Docker is installed but not running.",
                "Start the Docker service.", "sudo systemctl enable --now docker")
    from . import workspace
    if not ANDROID:
        add("Agent tools", "Work folder", "ok", "Files the AI makes are saved in "
            f"{workspace._short(workspace.files_dir(s))}"
            + (" (/workspace in the sandbox)" if shutil.which("docker") else " once Docker is installed"))
    if not ANDROID:
        add("Agent tools", "Web search", "ok" if s.get("web_search", True) is not False else "warn",
            "Built-in search (DuckDuckGo) and page reader, no API key needed")

    # ---- remote access
    mode = s["remote"]
    if mode == "tailscale":
        if not remote.ts():
            add("Phone access", "Tailscale", "fail", "Tailscale is not installed.",
                "Install it and sign in on this computer and your phone.",
                "" if MAC else "curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up")
        else:
            code, txt = _run([remote.ts(), "status", "--json"])
            if code != 0:
                add("Phone access", "Tailscale", "fail", "Tailscale is not running or not signed in.",
                    "Open the Tailscale app and sign in." if MAC else "Sign in.", "" if MAC else "sudo tailscale up")
            else:
                add("Phone access", "Tailscale", "ok", "Signed in")
    elif mode == "lan":
        add("Phone access", "Home network", "ok", "Reachable from devices on the same network")

    # ---- ports
    for name, port in (("Model server port", int(s["llama_port"])),):
        if core.server.state in ("off", "error"):
            with socket.socket() as sock:
                busy = sock.connect_ex(("127.0.0.1", port)) == 0
            if busy:
                add("System", name, "warn", f"Something else is using port {port}.",
                    "Stop the other program or change the port in Settings.")
    if MAC or ANDROID:
        add("System", "Notifications", "ok", ("Mac" if MAC else "Phone") + " notifications for finished tasks")
        return out
    add("System", "Notifications", "ok" if shutil.which("notify-send") else "warn",
        "Desktop notifications for finished tasks" if shutil.which("notify-send") else "notify-send is missing.",
        "" if shutil.which("notify-send") else "Install it for task notifications.",
        "" if shutil.which("notify-send") else "sudo apt install libnotify-bin")
    return out
