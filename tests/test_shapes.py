"""3D models: trellis.cpp turns a picture into a .glb. Runs a real Pumpkin with fake llama-server, sd-server and
trellis-cli: from a picture, from words (drawn first), in the chat, for Claude, and when it goes wrong."""
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fixtures  # noqa: E402
from pumpkin import config, images, shapes  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class TestEngineChoice(unittest.TestCase):
    def test_the_vulkan_build_for_this_system(self):
        rels = [{"tag_name": "v0.9.0", "draft": True, "assets": [{"name": "trellis-vulkan-linux-x64.tar.gz"}]},
                {"tag_name": "v0.8.1", "assets": [{"name": "trellis-cuda-linux-x64.tar.gz"},
                                                  {"name": "trellis-vulkan-linux-x64.tar.gz"},
                                                  {"name": "trellis-vulkan-windows-x64.zip"}]},
                {"tag_name": "v0.8.0", "assets": [{"name": "trellis-vulkan-linux-x64.tar.gz"}]}]
        self.assertEqual([r["tag_name"] for r, _ in shapes.release_builds(rels, system="linux")], ["v0.8.1", "v0.8.0"])
        win = shapes.release_builds(rels, system="win32")
        self.assertEqual([(r["tag_name"], a["name"]) for r, a in win], [("v0.8.1", "trellis-vulkan-windows-x64.zip")])

    def test_quality_follows_the_graphics_card(self):
        self.assertEqual(shapes.default_quality({"main_gpu": {"vram_total": 16.0}}), "detailed")
        self.assertEqual(shapes.default_quality({"main_gpu": {"vram_total": 24.0}}), "best")
        self.assertEqual(shapes.default_res({"main_gpu": {"vram_total": 8.0}}), 512)
        self.assertEqual(shapes.default_res({"main_gpu": None, "ram_total": 64}), 512)
        self.assertEqual(shapes.default_res({"main_gpu": {"unified": True}, "ram_total": 32}), 1024)

    def test_pixal3d_when_it_is_in_and_the_quality_has_a_texture_model(self):
        self.assertEqual(shapes.choose_family("auto", True, 1024), "pixal3d")
        self.assertEqual(shapes.choose_family("auto", True, 512), "trellis")       # Pixal3D has no 512 texture
        self.assertEqual(shapes.choose_family("auto", False, 1536), "trellis")
        self.assertEqual(shapes.choose_family("trellis", True, 1536), "trellis")

    def test_what_each_quality_asks_the_engine_for(self):
        def cmd(res, family="trellis"):
            return shapes.Run("cli", "m", "in.png", "out.glb", res, family=family).cmd
        self.assertNotIn("--tex-res", cmd(512))
        self.assertEqual(cmd(1024)[-2:], ["--tex-res", "1024"])           # the full colour volume, not 512
        best = cmd(1536, "pixal3d")
        self.assertIn("--model", best)
        self.assertEqual(best[best.index("--atlas") + 1], "4096")

    def test_matte_takes_the_metal_off_and_nothing_else(self):
        import struct
        doc = json.dumps({"asset": {"version": "2.0"}, "materials": [{"pbrMetallicRoughness": {
            "baseColorTexture": {"index": 0}, "metallicFactor": 1.0}}]}).encode()
        doc += b" " * (-len(doc) % 4)
        binary = b"\x01\x02\x03\x04" * 5
        glb = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(doc) + 8 + len(binary)) + struct.pack("<I4s", len(doc), b"JSON") \
            + doc + struct.pack("<I4s", len(binary), b"BIN\0") + binary
        out = shapes.matte(glb)
        self.assertEqual(struct.unpack("<I", out[8:12])[0], len(out))
        jlen = struct.unpack("<I", out[12:16])[0]
        mat = json.loads(out[20:20 + jlen])["materials"][0]["pbrMetallicRoughness"]
        self.assertEqual((mat["metallicFactor"], mat["baseColorTexture"]), (0.0, {"index": 0}))
        self.assertTrue(out.endswith(struct.pack("<I4s", len(binary), b"BIN\0") + binary))
        self.assertEqual(shapes.matte(b"not a glb"), b"not a glb")

    def test_a_picture_made_for_3d(self):
        p = shapes.picture_prompt("  a carved pumpkin lantern. ")
        self.assertTrue(p.startswith("a single a carved pumpkin lantern,"))
        self.assertIn("plain light grey background", p)   # white cuts out badly from white objects


class TestWhereItRuns(unittest.TestCase):
    def test_a_mac_is_told_plainly(self):
        from unittest import mock
        from types import SimpleNamespace
        from pumpkin.core import Core
        fake = SimpleNamespace(settings={"images": True})
        with mock.patch.object(shapes, "supported", lambda: False):
            with self.assertRaises(RuntimeError) as e:
                Core.make_3d(fake, "a mug")
            self.assertIn("Linux and Windows only", str(e.exception))
            self.assertFalse(Core.shapes_ready(fake))


@unittest.skipUnless(shapes.supported(), "trellis.cpp has builds for Linux and Windows (x64) only")
class TestShapes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pumpkin.core import Core
        from pumpkin.server import Web
        cls.tmp = tempfile.mkdtemp(prefix="pumpkin-3d-")
        bin_dir = os.path.join(cls.tmp, "bin")
        os.makedirs(bin_dir)
        llama_bin = fixtures.make_fake_server(os.path.join(bin_dir, "llama-server"))
        sd_bin = fixtures.make_fake_sd(os.path.join(bin_dir, "sd-server"))
        cls.image_dir = os.path.join(cls.tmp, "image-models")
        cls.trellis = fixtures.make_fake_trellis(os.path.join(bin_dir, "trellis-cli"),
                                                 os.path.join(cls.image_dir, shapes.FOLDER))
        cls.calls = os.path.join(bin_dir, "trellis-calls.jsonl")
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
        os.environ["FAKE_TRELLIS_STEP"] = "0.05"
        s = config.Settings()
        s.update({"port": 18490, "llama_port": 18491, "image_port": 18492, "keep_awake": False,
                  "notifications": False, "llama_server": llama_bin, "sd_server": sd_bin, "trellis_cli": cls.trellis,
                  "image_dir": cls.image_dir, "image_swap": "auto", "image_model": "", "shape_quality": "auto",
                  "files_dir": cls.files, "models_dirs": [os.path.join(cls.tmp, "models")],
                  "download_dir": os.path.join(cls.tmp, "models"), "autostart_model": False, "images": True,
                  "doc_index": False})
        sdxs = images.bundle_info("sdxs")
        folder = os.path.join(cls.image_dir, "sdxs")
        images.write_manifest(folder, sdxs, [{"role": "model", "repo": "x/y", "path": "sdxs-Q8_0.gguf", "size": 3}])
        with open(os.path.join(folder, "sdxs-Q8_0.gguf"), "wb") as f:
            f.write(b"abc")
        cls.core = Core()
        cls.core.hw = {"main_gpu": {"vendor": "AMD", "name": "Test GPU", "vram_total": 16.0}, "ram_total": 32.0,
                       "backend": "vulkan", "gpus": [], "cpu": {"physical_cores": 4}}
        cls.core.hw_at = time.time() + 10 ** 6
        cls.web = Web(cls.core)
        cls.web.serve("127.0.0.1")
        cls.base = "http://127.0.0.1:18490"
        cls.tok = config.token()

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("FAKE_TRELLIS_STEP", None)
        cls.core.shutdown()
        cls.web.shutdown()
        if cls.old is not None:
            with open(cls.saved, "w", encoding="utf-8") as f:
                f.write(cls.old)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None, timeout=120, raw=False):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "X-Pumpkin": "1",
                                              "Authorization": f"Bearer {self.tok}"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout) as r:
                data = r.read()
                return r.status, (data if raw else json.loads(data or b"{}")), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), {}

    def last_call(self):
        with open(self.calls, encoding="utf-8") as f:
            return json.loads(f.read().splitlines()[-1])

    def test_1_ready_and_listed(self):
        st, s, _ = self.call("GET", "/pumpkin/api/shapes")
        self.assertEqual(st, 200)
        self.assertTrue(s["supported"] and s["ready"] and s["installed"], s)
        self.assertEqual(s["auto"], "detailed")                  # a 16 GB card
        self.assertFalse(s["pixal3d"]["installed"])
        self.assertEqual(set(s["fits"]), {"fast", "detailed", "best"})
        self.assertTrue(s["draws"])
        self.assertTrue(self.core.shapes_ready())

    def test_2_from_a_picture(self):
        st, r, _ = self.call("POST", "/pumpkin/api/shapes/make",
                             {"prompt": "my mug", "image": "data:image/png;base64," + base64.b64encode(PNG).decode(),
                              "quality": "fast", "seed": 7})
        self.assertEqual(st, 200, r)
        self.assertTrue(r["path"].startswith("3d/my-mug-") and r["path"].endswith(".glb"), r)
        self.assertEqual((r["quality"], r["res"], r["picture"]), ("fast", 512, None))
        args = self.last_call()
        self.assertEqual(args[args.index("--res") + 1], "512")
        self.assertEqual(args[args.index("-s") + 1], "7")
        self.assertTrue(args[args.index("-i") + 1].endswith(".png"))
        st, data, headers = self.call("GET", "/pumpkin/api/files/download?path=" + r["path"] + "&inline=1", raw=True)
        self.assertEqual(data[:4], b"glTF")
        self.assertEqual(headers.get("Content-Type"), "model/gltf-binary")
        self.assertFalse(os.listdir(os.path.join(config.STATE_DIR, "trellis-work")))   # nothing left behind

    def test_3_from_words_it_draws_first(self):
        st, r, _ = self.call("POST", "/pumpkin/api/shapes/make", {"prompt": "a carved pumpkin lantern"}, timeout=180)
        self.assertEqual(st, 200, r)
        self.assertTrue(r["picture"].startswith("images/a-single-a-carved-pumpkin-lantern"), r)
        self.assertEqual((r["res"], r["quality"]), (1024, "detailed"))        # auto on a 16 GB card
        self.assertTrue(os.path.isfile(os.path.join(self.files, r["picture"])))
        self.assertIn("trellis-work", self.last_call()[self.last_call().index("-i") + 1])   # the drawn picture went in

    def test_4_the_chat_gets_a_3d_model(self):
        r = self.core.shape_tool({"prompt": "a small robot", "quality": "fast"})
        text = r["plain_text_response"]
        self.assertRegex(text, r"saved as 3d/a-small-robot-\S+\.glb in their Files")   # what the chat's viewer reads
        bad = self.core.shape_tool({})
        self.assertIn("Say what the 3D model should be", bad["error"])

    def test_5_running_out_of_memory_says_what_to_do(self):
        os.environ["FAKE_TRELLIS_OOM"] = "1"
        try:
            st, r, _ = self.call("POST", "/pumpkin/api/shapes/make",
                                 {"prompt": "x", "image": base64.b64encode(PNG).decode()})
        finally:
            os.environ.pop("FAKE_TRELLIS_OOM")
        self.assertEqual(st, 409)
        self.assertIn("ran out of graphics memory", r["error"])
        self.assertIsNone(self.core.shape_progress)
        self.assertIsNone(self.core.shape_run)

    def test_6_stop_frees_it_at_once(self):
        os.environ["FAKE_TRELLIS_STEP"] = "3"
        out = {}
        t = threading.Thread(target=lambda: out.update(r=self.call("POST", "/pumpkin/api/shapes/make", {
            "prompt": "slow", "image": base64.b64encode(PNG).decode()})))
        t.start()
        try:
            for _ in range(100):
                st, s, _ = self.call("GET", "/pumpkin/api/shapes")
                if s.get("progress") and s["progress"].get("detail"):
                    break
                time.sleep(0.1)
            self.assertEqual(s["progress"]["stage"], "building")
            self.assertIn("[trellis]", s["progress"]["detail"])
            acts = self.core.activity()
            self.assertIn("Making the 3D model", [a["label"] for a in acts])
            t0 = time.time()
            self.call("POST", "/pumpkin/api/shapes/cancel", {})
            t.join(30)
        finally:
            os.environ["FAKE_TRELLIS_STEP"] = "0.05"
        self.assertLess(time.time() - t0, 15)
        self.assertEqual(out["r"][0], 409)
        self.assertIn("Cancelled", out["r"][1]["error"])

    def test_7_settings_and_nothing_to_make_it_from(self):
        st, s, _ = self.call("PUT", "/pumpkin/api/shapes/settings", {"quality": "fast"})
        self.assertEqual(s["quality"], "fast")
        self.assertEqual(self.core.shape_res(), 512)
        self.assertEqual(self.call("PUT", "/pumpkin/api/shapes/settings", {"quality": "huge"})[0], 400)
        self.assertEqual(self.call("PUT", "/pumpkin/api/shapes/settings", {"model": "hunyuan"})[0], 400)
        self.call("PUT", "/pumpkin/api/shapes/settings", {"quality": "auto"})
        self.assertEqual(self.call("POST", "/pumpkin/api/shapes/make", {})[0], 409)

    def test_8_missing_files_mean_not_ready(self):
        folder = os.path.join(self.image_dir, shapes.FOLDER)
        os.rename(os.path.join(folder, "tex_dec.gguf"), os.path.join(folder, "tex_dec.bak"))
        try:
            inst = shapes.installed(self.core.settings)
            self.assertEqual((inst["ready"], inst["partial"], inst["missing"]), (False, True, ["tex_dec.gguf"]))
            self.assertFalse(self.core.shapes_ready())
            st, r, _ = self.call("POST", "/pumpkin/api/shapes/make", {"prompt": "x", "image": base64.b64encode(PNG).decode()})
            self.assertIn("not installed", r["error"])
        finally:
            os.rename(os.path.join(folder, "tex_dec.bak"), os.path.join(folder, "tex_dec.gguf"))

    def glb_metal(self, path):
        import struct
        with open(os.path.join(self.files, path), "rb") as f:
            d = f.read()
        jlen = struct.unpack("<I", d[12:16])[0]
        return json.loads(d[20:20 + jlen])["materials"][0]["pbrMetallicRoughness"]["metallicFactor"]

    def test_a_pixal3d_best_and_matte(self):
        folder = os.path.join(self.image_dir, shapes.FOLDER)
        st, r, _ = self.call("POST", "/pumpkin/api/shapes/pixal3d", {})
        self.assertEqual(st, 200, r)                                # (the fake Hugging Face is not there: queued)
        fixtures.make_fake_trellis(os.path.join(self.tmp, "bin", "trellis-cli"), folder, pixal=True)
        try:
            st, s, _ = self.call("PUT", "/pumpkin/api/shapes/settings", {"quality": "best", "matte": True})
            self.assertTrue(s["pixal3d"]["installed"] and s["matte"], s)
            st, r, _ = self.call("POST", "/pumpkin/api/shapes/make", {"prompt": "jacket", "image": base64.b64encode(PNG).decode()})
            self.assertEqual(st, 200, r)
            self.assertEqual((r["model"], r["quality"], r["res"], r["fell_back"], r["matte"]),
                             ("Pixal3D", "best", 1536, False, True))
            args = self.last_call()
            self.assertEqual(args[args.index("--model") + 1], "pixal3d")
            self.assertEqual(args[args.index("--atlas") + 1], "4096")
            self.assertEqual(self.glb_metal(r["path"]), 0.0)          # the metal look is gone
            # Best did not fit: it goes again at Detailed, by itself
            os.environ["FAKE_TRELLIS_OOM"] = "1536"
            try:
                st, r, _ = self.call("POST", "/pumpkin/api/shapes/make",
                                     {"prompt": "jacket", "image": base64.b64encode(PNG).decode(), "matte": False})
            finally:
                os.environ.pop("FAKE_TRELLIS_OOM")
            self.assertEqual(st, 200, r)
            self.assertEqual((r["quality"], r["res"], r["fell_back"]), ("detailed", 1024, True))
            self.assertEqual(self.glb_metal(r["path"]), 1.0)          # as made
            # TRELLIS.2 when asked for, even with Pixal3D in
            st, r, _ = self.call("POST", "/pumpkin/api/shapes/make", {"prompt": "jacket", "model": "trellis",
                                                                      "image": base64.b64encode(PNG).decode()})
            self.assertNotIn("--model", self.last_call())
            self.assertEqual(r["model"], "TRELLIS.2")
        finally:
            self.call("PUT", "/pumpkin/api/shapes/settings", {"quality": "auto", "matte": False, "model": "auto"})
            for n in shapes.PIXAL_FILES:
                try:
                    os.remove(os.path.join(folder, n))
                except OSError:
                    pass
            for d in self.core.downloads.list():
                self.core.downloads.cancel(d["id"])

    def test_9_claude_can_ask_for_one(self):
        from pumpkin import delegate
        pic = os.path.join(self.tmp, "chair.png")
        with open(pic, "wb") as f:
            f.write(PNG)
        r = delegate.call(self.core, "make_3d", {"picture": pic, "quality": "fast"})
        done = self.core.delegate.view(self.core.delegate.wait(self.core.delegate.find(r["job"]), 60))
        self.assertEqual(done["state"], "done", done)
        self.assertIn(os.path.join(self.files, "3d", "chair-"), done["answer"])
        with self.assertRaises(ValueError):
            delegate.call(self.core, "make_3d", {})


if __name__ == "__main__":
    unittest.main()
