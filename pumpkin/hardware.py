"""What the machine has: GPUs, VRAM, RAM, CPU, and live usage."""
import glob
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time

GiB = 2 ** 30
VENDORS = {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}
MAC = sys.platform == "darwin"
ANDROID = hasattr(sys, "getandroidapilevel") or sys.platform == "android"
WINDOWS = sys.platform == "win32"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)      # Windows: helper programs never flash a console


# ---------------------------------------------------------------- Windows
_DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


def _win_vendor(text):
    t = (text or "").lower()
    if "nvidia" in t:
        return "NVIDIA"
    if "amd" in t or "radeon" in t or "advanced micro" in t:
        return "AMD"
    if "intel" in t:
        return "Intel"
    return None


def _win_gpus():
    """Display adapters from the registry. HardwareInformation.qwMemorySize is the real VRAM (the WMI field
    stops at 4 GB). NVIDIA cards are listed by nvidia-smi instead, which also reports what is in use."""
    import winreg
    found = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS)
    except OSError:
        return []
    i = 0
    while True:
        try:
            sub = winreg.EnumKey(root, i)
        except OSError:
            break
        i += 1
        if not sub.isdigit():
            continue
        try:
            k = winreg.OpenKey(root, sub)
        except OSError:
            continue

        def val(name):
            try:
                return winreg.QueryValueEx(k, name)[0]
            except OSError:
                return None
        name = val("DriverDesc") or ""
        vendor = _win_vendor(name) or _win_vendor(val("ProviderName"))
        if not vendor or "basic display" in name.lower() or "remote" in name.lower():
            continue
        mem = val("HardwareInformation.qwMemorySize")
        if mem is None:
            mem = val("HardwareInformation.MemorySize")
            if isinstance(mem, bytes):
                mem = int.from_bytes(mem[:8], "little")
        mem = int(mem or 0)
        integrated = vendor == "Intel" and mem < 2 * GiB or mem < 1 * GiB
        found.append({"vendor": vendor, "name": name, "vram_total": 0.0 if integrated else mem / GiB,
                      "vram_used": None, "busy": None, "integrated": integrated})
    return found


def _win_memory():
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return {"ram_total": 0.0, "ram_used": 0.0, "ram_available": 0.0}
    return {"ram_total": m.ullTotalPhys / GiB, "ram_used": (m.ullTotalPhys - m.ullAvailPhys) / GiB,
            "ram_available": m.ullAvailPhys / GiB}


def _win_physical_cores():
    """Physical cores from GetLogicalProcessorInformationEx (no PowerShell, no WMI)."""
    import ctypes
    k32 = ctypes.windll.kernel32
    size = ctypes.c_ulong(0)
    k32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(size))      # RelationProcessorCore = 0
    if not size.value:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if not k32.GetLogicalProcessorInformationEx(0, buf, ctypes.byref(size)):
        return None
    n, off = 0, 0
    while off < size.value:
        rel, length = int.from_bytes(buf.raw[off:off + 4], "little"), int.from_bytes(buf.raw[off + 4:off + 8], "little")
        if rel == 0:
            n += 1
        off += length or size.value
    return n or None


def _win_cpu():
    import winreg
    model = None
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        model = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except OSError:
        pass
    logical = os.cpu_count() or 1
    try:
        physical = _win_physical_cores() or max(1, logical // 2)
    except (OSError, AttributeError, ValueError):
        physical = max(1, logical // 2)
    try:
        import ctypes
        pf = ctypes.windll.kernel32.IsProcessorFeaturePresent
        avx2 = bool(pf(40))                              # PF_AVX2_INSTRUCTIONS_AVAILABLE
        avx512 = bool(pf(41))                            # PF_AVX512F_INSTRUCTIONS_AVAILABLE
    except (OSError, AttributeError):
        avx2 = avx512 = False
    return {"model": model or platform.processor() or "CPU", "physical_cores": physical, "logical_cores": logical,
            "avx2": avx2, "avx512": avx512}


class _WinVram:
    """VRAM in use on AMD and Intel GPUs comes from a Windows performance counter, which takes about a second
    to read, so a background thread refreshes it while someone is looking."""
    value, asked, _thread = None, 0.0, None

    @classmethod
    def get(cls):
        cls.asked = time.time()
        if cls._thread is None or not cls._thread.is_alive():
            cls._thread = threading.Thread(target=cls._loop, daemon=True)
            cls._thread.start()
        return cls.value

    @classmethod
    def _loop(cls):
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
               "((Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue)"
               ".CounterSamples | Measure-Object CookedValue -Sum).Sum"]
        while time.time() - cls.asked < 30:
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW).stdout
                cls.value = float(out.strip()) / GiB if out.strip() else None
            except (OSError, subprocess.SubprocessError, ValueError):
                cls.value = None
            time.sleep(3)


# ---------------------------------------------------------------- Android (the Pumpkin app)
def _getprop(name):
    try:
        out = subprocess.run(["getprop", name], capture_output=True, text=True, timeout=3)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def phone_memory_limit(ram_bytes):
    """How much memory a model may use on a phone. Android and the apps you have open need the rest, and
    Android closes an app that takes too much. About half of RAM, a little less on small phones."""
    ram = ram_bytes / GiB
    return max(1.5, ram * 0.55 - 0.5)


def _android_gpus():
    tot = (memory().get("ram_total") or 0) * GiB
    model = _getprop("ro.product.model") or "Phone"
    soc = _getprop("ro.soc.model") or _getprop("ro.board.platform") or ""
    return [{"index": 0, "vendor": "Phone", "name": f"{model}{f' ({soc})' if soc else ''}", "integrated": False,
             "unified": True, "phone": True, "vram_total": round(phone_memory_limit(tot), 1), "vram_used": None,
             "busy": None}]


def _android_cpu():
    """The fast cores only: llama.cpp is slower when it also waits for the small, slow ones."""
    logical = os.cpu_count() or 1
    freqs = []
    for i in range(logical):
        f = _read(f"/sys/devices/system/cpu/cpu{i}/cpufreq/cpuinfo_max_freq", int)
        if f:
            freqs.append(f)
    fast = sum(1 for f in freqs if f >= 0.75 * max(freqs)) if freqs else max(1, logical // 2)
    soc = _getprop("ro.soc.model") or _getprop("ro.board.platform") or "ARM"
    return {"model": soc, "physical_cores": max(2, fast), "logical_cores": logical, "avx2": False, "avx512": False}


# ---------------------------------------------------------------- macOS
def _sysctl(name):
    try:
        out = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=3)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def apple_silicon():
    return MAC and (platform.machine() == "arm64" or _sysctl("hw.optional.arm64") == "1")


def gpu_memory_limit(ram_bytes, wired_limit_mb=None):
    """How much of a Mac's memory the GPU may use. macOS allows about two thirds of RAM up to 36 GB and
    three quarters above that, unless someone raised it with `sudo sysctl iogpu.wired_limit_mb=...`."""
    if wired_limit_mb:
        return wired_limit_mb / 1024
    ram = ram_bytes / GiB
    return ram * (0.75 if ram > 36 else 2 / 3)


def _mac_gpus():
    if not apple_silicon():
        return []                               # Intel Macs: llama.cpp runs on the CPU
    ram = _int(_sysctl("hw.memsize")) or 0
    chip = _sysctl("machdep.cpu.brand_string") or "Apple Silicon"
    return [{"index": 0, "vendor": "Apple", "name": f"{chip} GPU", "integrated": False, "unified": True,
             "vram_total": round(gpu_memory_limit(ram, _int(_sysctl("iogpu.wired_limit_mb"))), 1),
             "vram_used": None, "busy": None}]


def _mac_memory():
    tot = _int(_sysctl("hw.memsize")) or 0
    avail = None
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3).stdout
        page = int(re.search(r"page size of (\d+)", out).group(1))
        pages = {k.strip().lower(): int(v.strip().rstrip(".")) for k, v in re.findall(r"^([^:]+):\s+(\d+)\.?$", out, re.M)}
        free = sum(pages.get(k, 0) for k in ("pages free", "pages inactive", "pages speculative", "pages purgeable"))
        avail = free * page
    except (OSError, subprocess.SubprocessError, AttributeError, ValueError):
        pass
    if avail is None:
        avail = tot
    return {"ram_total": tot / GiB, "ram_used": max(0, tot - avail) / GiB, "ram_available": avail / GiB}


def _mac_cpu():
    logical = _int(_sysctl("hw.logicalcpu")) or os.cpu_count() or 1
    physical = _int(_sysctl("hw.physicalcpu")) or logical
    perf = _int(_sysctl("hw.perflevel0.physicalcpu"))           # performance cores on Apple Silicon
    return {"model": _sysctl("machdep.cpu.brand_string") or "CPU", "physical_cores": perf or physical,
            "logical_cores": logical, "avx2": False, "avx512": False}


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


_nv_cache = {"at": 0.0, "value": []}


def _nvidia():
    """NVIDIA GPUs from nvidia-smi. Every open window and phone asks for live numbers every couple of seconds, so
    one reading is shared for a moment instead of starting nvidia-smi for each of them."""
    now = time.time()
    if now - _nv_cache["at"] < 1.5:
        return [dict(g) for g in _nv_cache["value"]]
    value = _nvidia_read()
    _nv_cache.update(at=time.time(), value=value)
    return [dict(g) for g in value]


def _nvidia_read():
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                             creationflags=NO_WINDOW).stdout
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
    """All GPUs with their VRAM in GiB. Integrated GPUs report vram_total 0 (they share RAM).
    On Apple Silicon the GPU shares memory with the CPU; vram_total is the part macOS lets the GPU use."""
    if MAC:
        return _mac_gpus()
    if ANDROID:
        return _android_gpus()
    if WINDOWS:
        found = [g for g in _win_gpus() if g["vendor"] != "NVIDIA" or not shutil.which("nvidia-smi")]
        nv = _nvidia()
        found += nv
        for i, g in enumerate(found):
            g["index"] = i
        return found
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
    if MAC:
        return _mac_memory()
    if WINDOWS:
        return _win_memory()
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
    if MAC:
        return _mac_cpu()
    if ANDROID:
        return _android_cpu()
    if WINDOWS:
        return _win_cpu()
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
    if MAC:
        return "metal" if apple_silicon() else "cpu"
    if ANDROID:
        return "cpu"
    g = main_gpu(gpu_list)
    if not g:
        return "cpu"
    if g["vendor"] == "NVIDIA" and shutil.which("nvidia-smi"):
        return "cuda"
    if g.get("integrated") and g["vendor"] == "Intel":
        return "vulkan"
    return "vulkan"


def vulkan_loader():
    if MAC or ANDROID:
        return False
    if WINDOWS:                         # GPU drivers install vulkan-1.dll into System32
        return os.path.exists(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "vulkan-1.dll"))
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
        return shutil.disk_usage(path).free / GiB
    except OSError:
        return None


_win_gpu_cache = []


def _win_gpus_cached():
    if not _win_gpu_cache:
        _win_gpu_cache.extend(_win_gpus())
    return _win_gpu_cache


def live():
    """Cheap numbers for the live readout (called every couple of seconds)."""
    fake = _fake()
    if fake:
        g = fake.get("main_gpu") or {}
        return {"ram_total": fake["ram_total"], "ram_used": fake.get("ram_used", 6.2), "ram_available": 20,
                "vram_total": g.get("vram_total"), "vram_used": fake.get("vram_used", 1.1), "gpu_busy": 3,
                "unified": bool(g.get("unified"))}
    out = memory()
    if MAC or ANDROID:
        g = ((_mac_gpus() if MAC else _android_gpus()) or [None])[0]
        if g:
            # one pool of memory: show how much of the GPU's share is in use (everything in use counts)
            out["vram_total"], out["vram_used"], out["unified"] = g["vram_total"], min(out["ram_used"], g["vram_total"]), True
        return out
    if WINDOWS:
        nv = _nvidia()
        if nv:
            out["vram_total"], out["vram_used"], out["gpu_busy"] = nv[0]["vram_total"], nv[0]["vram_used"], nv[0]["busy"]
        else:
            g = main_gpu(_win_gpus_cached())
            if g and g.get("vram_total"):
                out["vram_total"], out["vram_used"] = g["vram_total"], _WinVram.get()
        return out
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
