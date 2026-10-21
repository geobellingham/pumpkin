"""macOS pieces, tested anywhere with sysctl and vm_stat faked. CI also runs everything on a real Mac."""
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pumpkin import hardware, llama, models, service  # noqa: E402

GiB = 2 ** 30
SYSCTL = {"hw.memsize": str(36 * GiB), "hw.optional.arm64": "1", "machdep.cpu.brand_string": "Apple M3 Pro",
          "hw.logicalcpu": "12", "hw.physicalcpu": "12", "hw.perflevel0.physicalcpu": "6", "iogpu.wired_limit_mb": "0"}
VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               100000.
Pages active:                             900000.
Pages inactive:                           300000.
Pages speculative:                         50000.
Pages wired down:                         400000.
Pages purgeable:                           10000.
"""


def fake_run(cmd, *a, **k):
    if cmd[0] == "vm_stat":
        return subprocess.CompletedProcess(cmd, 0, VM_STAT, "")
    raise AssertionError(cmd)


class TestMacHardware(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(hardware, "MAC", True), mock.patch.object(hardware, "_sysctl", SYSCTL.get),
                   mock.patch.object(hardware.subprocess, "run", fake_run),
                   mock.patch.object(hardware.platform, "machine", lambda: "arm64"),
                   mock.patch.dict(os.environ, {"PUMPKIN_FAKE_HARDWARE": ""})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_apple_silicon(self):
        s = hardware.summary()
        g = s["main_gpu"]
        self.assertEqual(g["name"], "Apple M3 Pro GPU")
        self.assertTrue(g["unified"])
        self.assertEqual(g["vram_total"], 24.0)             # two thirds of 36 GB
        self.assertEqual(s["backend"], "metal")
        self.assertEqual(s["cpu"]["physical_cores"], 6)     # performance cores for llama.cpp threads
        self.assertAlmostEqual(s["ram_available"], (100000 + 300000 + 50000 + 10000) * 16384 / GiB, places=3)
        live = hardware.live()
        self.assertTrue(live["unified"])
        self.assertEqual(live["vram_total"], 24.0)

    def test_raised_gpu_limit_is_respected(self):
        with mock.patch.object(hardware, "_sysctl", {**SYSCTL, "iogpu.wired_limit_mb": "30720"}.get):
            self.assertEqual(hardware.gpus()[0]["vram_total"], 30.0)

    def test_intel_mac_runs_on_cpu(self):
        with mock.patch.object(hardware, "_sysctl", {**SYSCTL, "hw.optional.arm64": "0"}.get), \
                mock.patch.object(hardware.platform, "machine", lambda: "x86_64"):
            self.assertEqual(hardware.gpus(), [])
            self.assertEqual(hardware.recommended_backend(), "cpu")


class TestUnifiedFit(unittest.TestCase):
    INFO = {"attn_layers": 40, "kv_heads": 8, "key_length": 128}

    def test_no_spill_into_ram_on_a_mac(self):
        mac = {"vram_total": 24.0, "ram_total": 36.0, "unified": True}
        pc = {"vram_total": 16.0, "ram_total": 32.0}
        self.assertEqual(models.fit(14, self.INFO, 8192, "q8_0", mac)["level"], "gpu")
        # 30 GB does not fit a 36 GB Mac: the GPU's share is 24 GB and there is no separate RAM to spill into
        self.assertEqual(models.fit(30, self.INFO, 8192, "q8_0", mac)["level"], "no")
        self.assertIn(models.fit(20, self.INFO, 8192, "q8_0", pc)["level"], ("split", "tight"))


class TestMacLlama(unittest.TestCase):
    def test_release_names(self):
        self.assertEqual(llama.asset_names("b1", "metal", "arm64", "darwin")[0], "llama-b1-bin-macos-arm64.tar.gz")
        self.assertEqual(llama.asset_names("b1", "cpu", "x64", "darwin")[0], "llama-b1-bin-macos-x64.tar.gz")
        self.assertEqual(llama.asset_names("b1", "vulkan", "x64", "linux")[0], "llama-b1-bin-ubuntu-vulkan-x64.tar.gz")


class TestPinnedLlama(unittest.TestCase):
    def test_downloads_the_release_the_ui_is_built_from(self):
        want = open(os.path.join(os.path.dirname(HERE), "web", "LLAMA_TAG")).read().strip()
        self.assertEqual(llama.pinned_tag(), want)


class TestLaunchAgent(unittest.TestCase):
    def test_plist(self):
        d = tempfile.mkdtemp()
        with mock.patch.object(service, "PLIST_PATH", os.path.join(d, "app.pumpkin.plist")):
            service.write_plist()
            with open(service.PLIST_PATH, "rb") as f:
                p = plistlib.load(f)
        self.assertEqual(p["Label"], "app.pumpkin")
        self.assertEqual(p["ProgramArguments"][1:], ["-m", "pumpkin", "serve"])
        self.assertTrue(p["RunAtLoad"])
        self.assertIn("PYTHONPATH", p["EnvironmentVariables"])


if __name__ == "__main__":
    unittest.main()
