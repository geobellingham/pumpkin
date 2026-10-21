"""What the machine has: GPUs, VRAM, RAM, CPU, and live usage."""
import glob
import os
import re
import shutil
import subprocess

GiB = 2 ** 30
VENDORS = {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}


def _read(path, cast=str):
    try:
        with open(path) as f:
            return cast(f.read().strip())
    except (OSError, ValueError):
        return None


def _pci_name(slot):
    """Marketing name from lspci when it is installed, e.g. 'Radeon RX 9070 XT'."""
    if not slot or not shutil.which("lspci"):
        return None
    try:
        out = subprocess.run(["lspci", "-s", slot], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    name = out.strip().split(": ", 1)[-1].strip() if out.strip() else ""
    name = re.sub(r"\s*\(rev [0-9a-f]+\)$", "", name)
    br = re.findall(r"\[([^\]]+)\]", name)
    if len(br) > 1 or (br and not name.startswith("[")):
        # 'Advanced Micro Devices, Inc. [AMD/ATI] Navi 48 [Radeon RX 9070/9070 XT]' -> the marketing name
        return br[-1]
    return name or None


def _nvidia():
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for i, line in enumerate(out.strip().splitlines()):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            gpus.append({"index": i, "vendor": "NVIDIA", "name": parts[0], "vram_total": float(parts[1]) / 1024,
                         "vram_used": float(parts[2]) / 1024, "busy": float(parts[3]), "source": "nvidia-smi"})
        except ValueError:
            continue
    return gpus


def gpus():
    """All GPUs with their VRAM in GiB. Integrated GPUs report vram_total 0 (they share RAM)."""
    found = []
    seen_nvidia = False
    for dev in sorted(glob.glob("/sys/class/drm/card[0-9]*/device")):
        card = dev.split("/")[-2]
        if "-" in card:
            continue
        vendor = VENDORS.get(_read(f"{dev}/vendor") or "")
        if not vendor:
            continue
        if vendor == "NVIDIA":
            seen_nvidia = True
            continue                     # nvidia-smi gives better numbers
        tot = _read(f"{dev}/mem_info_vram_total", int)
        used = _read(f"{dev}/mem_info_vram_used", int)
        busy = _read(f"{dev}/gpu_busy_percent", int)
        slot = os.path.basename(os.path.realpath(dev))
        integrated = vendor == "Intel" or (tot is not None and tot < 2 * GiB)
        found.append({"index": len(found), "vendor": vendor, "name": _pci_name(slot) or f"{vendor} GPU",
                      "vram_total": (tot or 0) / GiB if not integrated else 0.0,
                      "vram_used": (used or 0) / GiB if not integrated else 0.0,
                      "busy": busy, "integrated": integrated, "sysfs": dev})
    nv = _nvidia()
    if nv or seen_nvidia:
        found.extend(nv or [{"vendor": "NVIDIA", "name": "NVIDIA GPU (install the driver for nvidia-smi)",
                             "vram_total": 0.0, "vram_used": 0.0, "busy": None, "integrated": False}])
    for i, g in enumerate(found):
        g["index"] = i
    return found


def main_gpu(gpu_list=None):
    gl = gpu_list if gpu_list is not None else gpus()
    dedicated = [g for g in gl if not g.get("integrated")]
    pool = dedicated or gl
    return max(pool, key=lambda g: g.get("vram_total") or 0) if pool else None


def memory():
    mem = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                mem[k] = int(v.split()[0]) * 1024
    except (OSError, ValueError):
        return {"ram_total": 0.0, "ram_used": 0.0, "ram_available": 0.0}
    tot, avail = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
    return {"ram_total": tot / GiB, "ram_used": (tot - avail) / GiB, "ram_available": avail / GiB}


def cpu():
    model, flags, cores = None, set(), set()
    phys = None
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if ":" not in line:
                    continue
                k, v = (x.strip() for x in line.split(":", 1))
                if k == "model name" and not model:
                    model = v
                elif k == "flags" and not flags:
                    flags = set(v.split())
                elif k == "physical id":
                    phys = v
                elif k == "core id":
                    cores.add((phys, v))
    except OSError:
        pass
    logical = os.cpu_count() or 1
    physical = len(cores) or max(1, logical // 2)
    return {"model": model or "CPU", "physical_cores": physical, "logical_cores": logical,
            "avx2": "avx2" in flags, "avx512": any(f.startswith("avx512") for f in flags)}


def recommended_backend(gpu_list=None):
    g = main_gpu(gpu_list)
    if not g:
        return "cpu"
    if g["vendor"] == "NVIDIA" and shutil.which("nvidia-smi"):
        return "cuda"
    if g.get("integrated") and g["vendor"] == "Intel":
        return "vulkan"
    return "vulkan"


def vulkan_loader():
    for p in ("/usr/lib/x86_64-linux-gnu/libvulkan.so.1", "/usr/lib64/libvulkan.so.1", "/usr/lib/libvulkan.so.1",
              "/usr/lib/aarch64-linux-gnu/libvulkan.so.1"):
        if os.path.exists(p):
            return True
    try:
        out = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True, timeout=5).stdout
        return "libvulkan.so.1" in out
    except (OSError, subprocess.SubprocessError):
        return False


def _fake():
    """PUMPKIN_FAKE_HARDWARE=file.json pretends to have other hardware (for development and tests)."""
    p = os.environ.get("PUMPKIN_FAKE_HARDWARE")
    if not p:
        return None
    import json
    with open(p) as f:
        return json.load(f)


def summary():
    fake = _fake()
    if fake:
        return fake
    gl = gpus()
    g = main_gpu(gl)
    return {"gpus": gl, "main_gpu": g, "cpu": cpu(), **memory(), "backend": recommended_backend(gl),
            "vulkan": vulkan_loader(), "disk": disk_free()}


def disk_free(path=None):
    path = path or os.path.expanduser("~")
    while path and not os.path.exists(path):
        path = os.path.dirname(path)
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize / GiB
    except OSError:
        return None


def live():
    """Cheap numbers for the live readout (called every couple of seconds)."""
    fake = _fake()
    if fake:
        g = fake.get("main_gpu") or {}
        return {"ram_total": fake["ram_total"], "ram_used": fake.get("ram_used", 6.2), "ram_available": 20,
                "vram_total": g.get("vram_total"), "vram_used": fake.get("vram_used", 1.1), "gpu_busy": 3}
    out = memory()
    g = None
    for dev in sorted(glob.glob("/sys/class/drm/card[0-9]*/device")):
        tot = _read(f"{dev}/mem_info_vram_total", int)
        if tot and tot >= 2 * GiB and (g is None or tot > g[0]):
            g = (tot, dev)
    if g:
        out["vram_total"] = g[0] / GiB
        out["vram_used"] = (_read(f"{g[1]}/mem_info_vram_used", int) or 0) / GiB
        out["gpu_busy"] = _read(f"{g[1]}/gpu_busy_percent", int)
    else:
        nv = _nvidia()
        if nv:
            out["vram_total"], out["vram_used"], out["gpu_busy"] = nv[0]["vram_total"], nv[0]["vram_used"], nv[0]["busy"]
    return out
