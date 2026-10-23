"""Pictures: the engine download choice, image model bundles, and the chat's image_generate with the text model
making room while drawing. Uses a fake sd-server and the fake llama-server."""
import base64
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

if "pumpkin.config" not in sys.modules:              # run on its own: keep everything in a temporary home
    _T = tempfile.mkdtemp(prefix="pumpkin-img-")
    os.environ.update(HOME=_T, USERPROFILE=_T, XDG_CONFIG_HOME=f"{_T}/.config", XDG_DATA_HOME=f"{_T}/.local/share",
                      XDG_STATE_HOME=f"{_T}/.local/state")

import fixtures  # noqa: E402
from pumpkin import config, images  # noqa: E402

GB = 2 ** 30


def tree(*files):
    return [{"type": "file", "path": p, "size": int(gb * GB)} for p, gb in files]


class TestEngine(unittest.TestCase):
    NAMES = ["sd-master-abc1234-bin-win-cpu-x64.zip", "sd-master-abc1234-bin-win-cuda12-x64.zip",
             "sd-master-abc1234-bin-win-vulkan-x64.zip", "sd-master-abc1234-bin-win-rocm-7.1-x64.zip",
             "cudart-sd-bin-win-cu12-x64.zip", "sd-master-abc1234-bin-Linux-Ubuntu-24.04-x86_64.zip",
             "sd-master-abc1234-bin-Linux-Ubuntu-24.04-x86_64-vulkan.zip",
             "sd-master-abc1234-bin-Linux-Ubuntu-24.04-x86_64-rocm-7.1.zip",
             "sd-master-abc1234-bin-Darwin-macOS-15.7-arm64.zip"]

    def match(self, backend, system):
        pat = images.asset_pattern(backend, system, "x64" if system != "darwin" else "arm64")
        return [n for n in self.NAMES if pat.match(n)]

    def test_each_computer_gets_one_build(self):
        self.assertEqual(self.match("cuda12", "win32"), ["sd-master-abc1234-bin-win-cuda12-x64.zip"])
        self.assertEqual(self.match("vulkan", "win32"), ["sd-master-abc1234-bin-win-vulkan-x64.zip"])
        self.assertEqual(self.match("cpu", "win32"), ["sd-master-abc1234-bin-win-cpu-x64.zip"])
        self.assertEqual(self.match("cpu", "linux"), ["sd-master-abc1234-bin-Linux-Ubuntu-24.04-x86_64.zip"])
        self.assertEqual(self.match("vulkan", "linux"), ["sd-master-abc1234-bin-Linux-Ubuntu-24.04-x86_64-vulkan.zip"])
        self.assertEqual(self.match("metal", "darwin"), ["sd-master-abc1234-bin-Darwin-macOS-15.7-arm64.zip"])

    def test_a_mac_build_says_which_macos_it_needs(self):
        self.assertEqual(images.mac_needs("sd-master-995-fd21ecf-bin-Darwin-macOS-26.0-arm64.zip"), 26)
        self.assertEqual(images.mac_needs("sd-master-abc-bin-win-cpu-x64.zip"), 0)

    def test_newest_release_with_the_build(self):
        rels = [{"tag_name": "master-3", "draft": True, "assets": [{"name": self.NAMES[1]}]},
                {"tag_name": "master-2", "assets": [{"name": self.NAMES[0]}]},
                {"tag_name": "master-1", "assets": [{"name": self.NAMES[1]}]}]
        rel, asset = images.pick_release(rels, "cuda12", "win32", "x64")
        self.assertEqual(rel["tag_name"], "master-1")
        self.assertEqual(asset["name"], self.NAMES[1])
        self.assertEqual(images.pick_release(rels, "vulkan", "win32", "x64"), (None, None))


class TestBundles(unittest.TestCase):
    Z = next(b for b in images.catalog() if b["id"] == "z-image-turbo")
    TREES = {
        "leejet/Z-Image-Turbo-GGUF": tree(("z_image_turbo-Q8_0.gguf", 6.7), ("z_image_turbo-Q6_K.gguf", 5.3),
                                          ("z_image_turbo-Q4_K.gguf", 3.9), ("z_image_turbo-Q3_K.gguf", 3.1),
                                          ("README.md", 0)),
        "unsloth/Qwen3-4B-Instruct-2507-GGUF": tree(("Qwen3-4B-Instruct-2507-Q4_K_M.gguf", 2.5),
                                                    ("Qwen3-4B-Instruct-2507-Q8_0.gguf", 4.3)),
        "Comfy-Org/z_image_turbo": tree(("split_files/vae/ae.safetensors", 0.33),
                                        ("split_files/text_encoders/qwen_3_4b.safetensors", 8.0)),
    }

    def pick(self, budget):
        return {f["role"]: f["path"] for f in images.resolve(self.Z, self.TREES, budget)}

    def test_quality_follows_the_graphics_card(self):
        self.assertEqual(self.pick(15)["diffusion_model"], "z_image_turbo-Q8_0.gguf")
        self.assertEqual(self.pick(8.5)["diffusion_model"], "z_image_turbo-Q6_K.gguf")
        self.assertEqual(self.pick(7)["diffusion_model"], "z_image_turbo-Q4_K.gguf")
        self.assertEqual(self.pick(0)["diffusion_model"], "z_image_turbo-Q4_K.gguf")    # no GPU: the usual one
        p = self.pick(15)
        self.assertEqual(p["llm"], "Qwen3-4B-Instruct-2507-Q4_K_M.gguf")
        self.assertEqual(p["vae"], "split_files/vae/ae.safetensors")

    def test_a_missing_file_says_which(self):
        trees = dict(self.TREES, **{"Comfy-Org/z_image_turbo": tree(("other.bin", 1))})
        with self.assertRaisesRegex(RuntimeError, "vae"):
            images.resolve(self.Z, trees, 10)

    def test_installed_only_when_every_file_is_complete(self):
        d = tempfile.mkdtemp()
        s = {"image_dir": d}
        files = images.resolve(self.Z, self.TREES, 7)
        folder = os.path.join(d, "z-image-turbo")
        images.write_manifest(folder, self.Z, [dict(f, size=4) for f in files])
        self.assertFalse(images.installed(s)["z-image-turbo"]["ready"])
        for f in files:
            with open(os.path.join(folder, os.path.basename(f["path"])), "wb") as fh:
                fh.write(b"abcd")
        m = images.installed(s)["z-image-turbo"]
        self.assertTrue(m["ready"])
        self.assertEqual({f["role"] for f in m["files"]}, {"diffusion_model", "llm", "vae"})

    def test_sizes_and_labels(self):
        self.assertEqual(images.size_for(1, 1, 1024), (1024, 1024))
        w, h = images.size_for(16, 9, 1024)
        self.assertTrue(w > h and w % 64 == 0 and h % 64 == 0)
        hw = {"main_gpu": {"vram_total": 12}, "ram_total": 32}
        self.assertEqual(images.fit(6.7, hw, "cuda12")["level"], "gpu")
        self.assertEqual(images.fit(6.7, {"main_gpu": {"vram_total": 4}, "ram_total": 32}, "vulkan")["level"],
                         "partial")
        self.assertEqual(images.fit(6.7, {"main_gpu": {"vram_total": 2, "too_old": True}, "ram_total": 16}, "cpu")
                         ["level"], "slow")
        self.assertEqual(images.fit(12, {"main_gpu": None, "ram_total": 8}, "cpu")["level"], "no")

    def test_the_text_library_leaves_image_models_alone(self):
        from pumpkin import models
        d = tempfile.mkdtemp()
        fixtures.make_gguf(os.path.join(d, "llm", "Qwen3-4B-Q4_K_M.gguf"), arch="qwen3", experts=0)
        fixtures.make_gguf(os.path.join(d, "pics", "z_image_turbo-Q4_K.gguf"), arch="qwen3", experts=0)
        with open(os.path.join(d, "pics", images.MANIFEST), "w") as f:
            f.write("{}")
        lib = models.Library({"models_dirs": [d], "download_dir": d})
        self.assertEqual([m["file"] for m in lib.scan(force=True)], ["Qwen3-4B-Q4_K_M.gguf"])


class TestDrawingInChat(unittest.TestCase):
    """A running Pumpkin: the chat calls image_generate, the text model makes room and comes back."""

    @classmethod
    def setUpClass(cls):
        from pumpkin.core import Core
        from pumpkin.server import Web
        cls.tmp = tempfile.mkdtemp(prefix="pumpkin-draw-")
        bin_dir = os.path.join(cls.tmp, "bin")
        os.makedirs(bin_dir)
        llama_bin = fixtures.make_fake_server(os.path.join(bin_dir, "llama-server"))
        cls.sd_bin = fixtures.make_fake_sd(os.path.join(bin_dir, "sd-server"))
        cls.calls = os.path.join(bin_dir, "sd-calls.jsonl")
        cls.model = os.path.join(cls.tmp, "models", "tiny", "tiny-Q8_0.gguf")
        fixtures.make_gguf(cls.model, arch="llama", name="Tiny", layers=16, heads=16, kv_heads=8, embd=1024,
                           key_len=64, experts=0, file_type=7, size_gb=0.6)
        cls.saved = os.path.join(config.CONFIG_DIR, "settings.json")
        try:
            with open(cls.saved, encoding="utf-8") as f:
                cls.old = f.read()
        except OSError:
            cls.old = None
        cls.files = os.path.join(cls.tmp, "files")
        s = config.Settings()
        s.update({"port": 18390, "llama_port": 18391, "image_port": 18392, "keep_awake": False,
                  "notifications": False, "llama_server": llama_bin, "sd_server": cls.sd_bin,
                  "image_dir": os.path.join(cls.tmp, "image-models"), "image_swap": "always", "image_model": "",
                  "files_dir": cls.files, "models_dirs": [os.path.join(cls.tmp, "models")],
                  "download_dir": os.path.join(cls.tmp, "models"), "autostart_model": False, "images": True,
                  "doc_index": False})
        sdxs = images.bundle_info("sdxs")
        folder = os.path.join(cls.tmp, "image-models", "sdxs")
        images.write_manifest(folder, sdxs, [{"role": "model", "repo": "x/y", "path": "sdxs-Q8_0.gguf", "size": 3}])
        with open(os.path.join(folder, "sdxs-Q8_0.gguf"), "wb") as f:
            f.write(b"abc")
        cls.core = Core()
        cls.core.hw = {"main_gpu": {"vendor": "AMD", "name": "Test GPU", "vram_total": 8.0}, "ram_total": 16.0,
                       "backend": "vulkan", "gpus": [], "cpu": {"physical_cores": 4}}
        cls.core.hw_at = time.time() + 10 ** 6
        cls.web = Web(cls.core)
        cls.web.serve("127.0.0.1")
        cls.base = "http://127.0.0.1:18390"
        cls.tok = config.token()

    @classmethod
    def tearDownClass(cls):
        cls.core.shutdown()
        cls.web.shutdown()
        if cls.old is not None:
            with open(cls.saved, "w", encoding="utf-8") as f:
                f.write(cls.old)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None, timeout=60):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "X-Pumpkin": "1",
                                              "Authorization": f"Bearer {self.tok}"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def sd_calls(self):
        try:
            with open(self.calls) as f:
                return [json.loads(x) for x in f if x.strip()]
        except OSError:
            return []

    def test_chat_draws_and_the_text_model_comes_back(self):
        st, images_state = self.call("GET", "/pumpkin/api/images")
        self.assertEqual(st, 200, images_state)
        sdxs = next(b for b in images_state["bundles"] if b["id"] == "sdxs")
        self.assertTrue(sdxs["installed"] and sdxs["default"])
        self.assertTrue(images_state["ready"])
        st, preset = self.call("POST", "/pumpkin/api/presets/from-model", {"path": self.model, "name": "Tiny"})
        self.assertEqual(st, 200, preset)
        self.call("PUT", f"/pumpkin/api/presets/{preset['id']}", {"ctx": 2048, "tools": True, "web": False,
                                                                     "sandbox": False})
        self.call("POST", "/pumpkin/api/llama/start", {"preset": preset["id"]})
        self.assertTrue(self.core.server.wait_ready(60), self.core.server.error)
        with open(self.core.mcp_config(self.core.server.preset)) as f:
            self.assertIn("image", json.load(f)["mcpServers"])
        self.assertIn("image_generate", self.core.persona_prompt())
        pid_before = self.core.server.proc.pid

        st, r = self.call("POST", "/tools", {"tool": "image_generate",
                                             "params": {"prompt": "a pumpkin on a bicycle", "shape": "landscape"}}, 120)
        self.assertEqual(st, 200, r)
        lines = r["plain_text_response"].splitlines()
        self.assertTrue(lines[-1].startswith("data:image/jpeg;base64,"), r)
        self.assertIn(b"a pumpkin on a bicycle", base64.b64decode(lines[-1].split(",", 1)[1]))
        self.assertIn("images/", lines[0])
        saved = [f for f in os.listdir(os.path.join(self.files, "images")) if f.endswith(".jpg")]
        self.assertEqual(len(saved), 1)
        gen = [c["img_gen"] for c in self.sd_calls() if "img_gen" in c][-1]
        self.assertGreater(gen["width"], gen["height"])                       # landscape
        start = [c["start"] for c in self.sd_calls() if "start" in c][-1]
        self.assertIn("--model", start)
        self.assertEqual(start[start.index("--steps") + 1], "1")
        # it made room: the text model was stopped while drawing, and is back with the same preset
        self.assertEqual(self.core.server.state, "ready")
        self.assertEqual(self.core.server.preset["id"], preset["id"])
        self.assertNotEqual(self.core.server.proc.pid, pid_before)
        self.assertEqual(self.core.image_server.state, "off")                # and the picture's memory is free again

        # other tools still go to llama-server
        st, r = self.call("POST", "/tools", {"tool": "web_search", "params": {"query": "pumpkins"}})
        self.assertIn("pumpkins", r.get("plain_text_response", ""))

        # with room for both, the text model keeps running and the image model stays loaded for the next one
        self.core.settings.update({"image_swap": "auto"})
        pid = self.core.server.proc.pid
        st, r = self.call("POST", "/pumpkin/api/images/generate", {"prompt": "a small cat", "shape": "square"}, 120)
        self.assertEqual(st, 200, r)
        self.assertTrue(r["url"].startswith("/pumpkin/api/files/download?path=images/"))
        self.assertFalse(r["swapped"])
        self.assertEqual(self.core.server.proc.pid, pid)
        self.assertEqual(self.core.image_server.state, "ready")
        st, img = self.call("POST", "/pumpkin/api/images/stop")
        self.assertEqual(self.core.image_server.state, "off")

    def test_errors_reach_the_model(self):
        st, r = self.call("POST", "/tools", {"tool": "image_generate", "params": {"prompt": "  "}})
        self.assertEqual(st, 200)
        self.assertIn("Say what the picture should show", r.get("error", ""))


if __name__ == "__main__":
    unittest.main()
