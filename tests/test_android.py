"""The Android app's Python side, tested anywhere with the phone's system files faked."""
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

# imported inside the tests: the main suite sets up its own folders before Pumpkin is first imported
hardware = models = None


def _load():
    global hardware, models
    from pumpkin import hardware as h, models as m
    hardware, models = h, m

GiB = 2 ** 30
PROPS = {"ro.product.model": "Pixel 8", "ro.soc.model": "Tensor G3"}
FREQ = {0: 1700000, 1: 1700000, 2: 1700000, 3: 1700000, 4: 2370000, 5: 2370000, 6: 2370000, 7: 2370000, 8: 2910000}


def fake_read(path, cast=str):
    if path.endswith("cpuinfo_max_freq"):
        n = int(path.split("/cpu")[2].split("/")[0])
        return cast(FREQ[n]) if n in FREQ else None
    return None


class TestPhone(unittest.TestCase):
    def setUp(self):
        _load()
        for p in (mock.patch.object(hardware, "ANDROID", True), mock.patch.object(hardware, "MAC", False),
                  mock.patch.object(hardware, "_getprop", PROPS.get), mock.patch.object(hardware, "_read", fake_read),
                  mock.patch.object(hardware.os, "cpu_count", lambda: 9),
                  mock.patch.object(hardware, "memory", lambda: {"ram_total": 8.0, "ram_used": 4.0, "ram_available": 4.0}),
                  mock.patch.dict(os.environ, {"PUMPKIN_FAKE_HARDWARE": ""})):
            p.start()
            self.addCleanup(p.stop)

    def test_phone_summary(self):
        s = hardware.summary()
        g = s["main_gpu"]
        self.assertEqual(g["name"], "Pixel 8 (Tensor G3)")
        self.assertTrue(g["phone"] and g["unified"])
        self.assertEqual(g["vram_total"], 3.9)              # about half of 8 GB is for the model
        self.assertEqual(s["backend"], "cpu")
        self.assertEqual(s["cpu"]["physical_cores"], 5)     # the fast cores only
        self.assertTrue(hardware.live()["unified"])

    def test_phone_fit(self):
        hw = {"vram_total": 3.9, "ram_total": 8.0, "unified": True, "phone": True}
        info = {"attn_layers": 28, "kv_heads": 8, "key_length": 128}
        small = models.fit(2.0, info, 4096, "q8_0", hw)
        self.assertEqual(small["level"], "gpu")
        self.assertEqual(models.fit_label(small), "Fits in your phone's memory")
        big = models.fit(5.0, info, 4096, "q8_0", hw)
        self.assertEqual(models.fit_label(big), "Too big for this phone")


class TestStart(unittest.TestCase):
    def test_start_in_a_fresh_python(self):
        """start() runs before anything else is imported, as in the app, and serves the UI with a sign-in link."""
        import subprocess
        d = tempfile.mkdtemp()
        native = os.path.join(d, "native")
        os.makedirs(native)
        code = ("import sys, urllib.request; sys.path.insert(0, %r); sys.getandroidapilevel = lambda: 34\n"
                "from pumpkin import android\n"
                "url = android.start(%r, %r, %r)\n"
                "print(url)\n"
                "op = urllib.request.build_opener(urllib.request.ProxyHandler({}))\n"
                "import json\n"
                "from pumpkin import config\n"
                "req = urllib.request.Request(url.split('/login')[0] + '/pumpkin/api/state', headers={'Authorization': 'Bearer ' + config.token()})\n"
                "st = json.load(op.open(req, timeout=10))\n"
                "print(st['platform'], config.Settings()['llama_server'])\n"
                "android.stop()\n") % (os.path.dirname(HERE), os.path.join(d, "files"), native, os.path.join(d, "cache"))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                             env={k: v for k, v in os.environ.items() if not k.startswith("XDG_")})
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        lines = out.stdout.strip().splitlines()
        self.assertIn("/login?key=", lines[0])
        self.assertEqual(lines[1], "android " + os.path.join(native, "libllama_server.so"))
        self.assertTrue(os.path.isdir(os.path.join(d, "files", "home", ".config", "pumpkin")))


if __name__ == "__main__":
    unittest.main()
