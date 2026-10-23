"""Recommendation ranking: two machines, two routes."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fake_hf  # noqa: E402
from pumpkin import models, recommend  # noqa: E402

ITEMS = fake_hf.rich("text-generation") + fake_hf.rich("image-text-to-text") + fake_hf.rich("text-to-image") \
    + fake_hf.rich("feature-extraction")
BIG = {"vram_total": 16.0, "ram_total": 30.0}        # 16 GB GPU, 32 GB RAM
SMALL = {"vram_total": 8.0, "ram_total": 16.0}
CPU = {"vram_total": 0.0, "ram_total": 16.0}


def names(rows):
    return [r["repo"] for r in rows]


class TestRecommend(unittest.TestCase):
    def test_routes_on_a_16gb_gpu(self):
        r = recommend.recommend(ITEMS, BIG, curated=models.catalog())
        gpu, moe = names(r["gpu"]), names(r["moe"])
        print("\n16GB GPU:", [(x["repo"], x["quant"]) for x in r["gpu"]])
        print("16GB MoE:", [(x["repo"], x["quant"], x["ram_share"]) for x in r["moe"]])
        # dense 27B fits the GPU at a low quant and is the smartest choice there
        self.assertIn("unsloth/Qwen3.8-27B-GGUF", gpu)
        # the 35B-A3B MoE does not fit in 16 GB at Q4+, so it is in the RAM route
        self.assertIn("unsloth/Qwen3.6-35B-A3B-GGUF", moe)
        self.assertNotIn("unsloth/Qwen3.6-35B-A3B-GGUF", gpu)
        # duplicates of the same model collapse to one entry
        self.assertNotIn("bartowski/Qwen_Qwen3.6-35B-A3B-GGUF", moe)
        # huge MoE models that cannot fit even with RAM are left out
        self.assertNotIn("ggml-org/gpt-oss-120b-GGUF", moe + gpu)
        self.assertNotIn("unsloth/GLM-4.5-Air-GGUF", moe + gpu)
        # image generators and embedding models are not chat models
        self.assertFalse(any("Image" in n or "embed" in n for n in gpu + moe))
        # dense models never land in the MoE route
        self.assertTrue(all(x["moe"] for x in r["moe"]))
        # tested picks are marked
        self.assertTrue(any(x.get("tested") for x in r["gpu"] + r["moe"]))
        # the uncensored re-upload ranks below the original
        if "JonathanColetti/Qwen3.8-27B-Uncensored-GGUF" in gpu:
            self.assertLess(gpu.index("unsloth/Qwen3.8-27B-GGUF"), gpu.index("JonathanColetti/Qwen3.8-27B-Uncensored-GGUF"))

    def test_smaller_machine(self):
        r = recommend.recommend(ITEMS, SMALL, curated=models.catalog())
        print("\n8GB GPU:", [(x["repo"], x["quant"]) for x in r["gpu"]])
        print("8GB MoE:", [(x["repo"], x["quant"], x["ram_share"]) for x in r["moe"]])
        self.assertNotIn("unsloth/Qwen3.8-27B-GGUF", names(r["gpu"]))
        self.assertIn("unsloth/Qwen3-4B-Instruct-2507-GGUF", names(r["gpu"]))
        for x in r["gpu"]:
            self.assertLessEqual(x["size_gb"], 8.0)

    def test_cpu_only(self):
        r = recommend.recommend(ITEMS, CPU, curated=models.catalog())
        self.assertEqual(r["gpu"], [])
        self.assertTrue(all(x["moe"] for x in r["moe"]))

    def test_no_graphics_card_still_gets_models(self):
        """A laptop with only RAM (or a built-in GPU that shares it) always gets something that runs, marked slow."""
        cat = models.catalog()
        for ram in (8.0, 16.0):
            r = recommend.recommend(recommend.curated_items(cat), {"vram_total": 0.0, "ram_total": ram}, curated=cat)
            self.assertTrue(r["slow"], ram)
            self.assertTrue(all(x["size_gb"] <= 9 for x in r["slow"]))
            self.assertEqual(r["slow"][0]["fit"]["text"], "Runs on the processor: works, but slowly")
            self.assertIn("runs on the processor", r["slow"][0]["blurb"])
        tiny = recommend.recommend(recommend.curated_items(cat), {"vram_total": 0.0, "ram_total": 4.0}, curated=cat)
        self.assertEqual(tiny["slow"][0]["name"], "Gemma 3 1B")                  # the last resort: smallest first
        self.assertIn("may fail", tiny["slow"][0]["fit"]["text"])
        # with a real GPU the slow route stays empty: the GPU and MoE lists are better
        gpu = recommend.recommend(recommend.curated_items(cat), SMALL, curated=cat)
        self.assertEqual(gpu["slow"], [])

    def test_processor_picks_q4(self):
        choices = [{"kind": "model", "name": f"m-{q}.gguf", "quant": q, "size_gb": s, "files": [f"m-{q}.gguf"]}
                   for q, s in (("Q3_K_M", 0.9), ("Q4_K_M", 1.1), ("Q6_K", 1.4), ("Q8_0", 1.8))]
        pick, f = models.pick_quant(choices, {"vram_total": 0.0, "ram_total": 16.0}, 8192)
        self.assertEqual((pick["quant"], f["level"]), ("Q4_K_M", "split"))
        pick, f = models.pick_quant(choices, {"vram_total": 0.0, "ram_total": 4.0}, 4096)
        self.assertEqual((pick["quant"], f["level"]), ("Q3_K_M", "tight"))

    def test_popular_and_strong_families_first(self):
        """Two models that both fit: the much-downloaded Qwen from unsloth beats a small edge model."""
        def item(rid, params, downloads):
            return {"id": rid, "downloads": downloads, "trendingScore": 5, "likes": 50, "pipeline_tag": "text-generation",
                    "gguf": {"total": params * 1e9, "architecture": "dense", "context_length": 32768,
                             "chat_template": "tools"}}
        items = [item("unsloth/Qwen3-4B-Instruct-2507-GGUF", 4.0, 400_000), item("LiquidAI/LFM2.5-1.2B-Instruct-GGUF", 1.2, 60_000),
                 item("someone/Qwen3-4B-random-GGUF", 4.0, 300)]
        for hw in (SMALL, {"vram_total": 0.0, "ram_total": 16.0}):
            r = recommend.recommend(items, hw)
            ranked = names(r["gpu"] or r["slow"])
            self.assertEqual(ranked[0], "unsloth/Qwen3-4B-Instruct-2507-GGUF", ranked)
            self.assertLess(ranked.index("LiquidAI/LFM2.5-1.2B-Instruct-GGUF"), ranked.index("someone/Qwen3-4B-random-GGUF"))

    def test_offline_falls_back_to_catalog(self):
        r = recommend.recommend(recommend.curated_items(models.catalog()), BIG, curated=models.catalog())
        self.assertTrue(r["gpu"] and r["moe"])
        self.assertTrue(all(x.get("tested") for x in r["gpu"] + r["moe"]))


if __name__ == "__main__":
    unittest.main()


class TestWindowsGpus(unittest.TestCase):
    """A laptop with Intel graphics and an NVIDIA card: the NVIDIA card must be the one Pumpkin uses."""

    def test_nvidia_with_na_fields_and_registry_fallback(self):
        from unittest import mock
        from pumpkin import hardware
        intel = {"vendor": "Intel", "name": "Intel(R) UHD Graphics", "vram_total": 0.0, "vram_used": None, "busy": None,
                 "integrated": True}
        reg_nv = {"vendor": "NVIDIA", "name": "NVIDIA GeForce RTX 4060 Laptop GPU", "vram_total": 8.0, "vram_used": None,
                  "busy": None, "integrated": False}
        smi = "NVIDIA GeForce RTX 4060 Laptop GPU, 8188, [N/A], [N/A]\n"
        run = mock.Mock(return_value=mock.Mock(stdout=smi))
        hardware._nv_cache["at"] = 0
        with mock.patch.object(hardware, "WINDOWS", True), mock.patch.object(hardware, "MAC", False), \
                mock.patch.object(hardware, "ANDROID", False), \
                mock.patch.object(hardware, "_win_gpus", lambda: [intel, dict(reg_nv)]), \
                mock.patch.object(hardware.shutil, "which", lambda b: "nvidia-smi" if b == "nvidia-smi" else None), \
                mock.patch.object(hardware.subprocess, "run", run):
            gl = hardware.gpus()
            self.assertEqual([g["vendor"] for g in gl], ["Intel", "NVIDIA"])
            main = hardware.main_gpu(gl)
            self.assertEqual(main["name"], "NVIDIA GeForce RTX 4060 Laptop GPU")
            self.assertAlmostEqual(main["vram_total"], 8.0, places=1)
            self.assertIsNone(main["vram_used"])
            # nvidia-smi answers nothing: the card still comes from the registry
            run.return_value = mock.Mock(stdout="")
            hardware._nv_cache["at"] = 0
            gl = hardware.gpus()
            self.assertEqual(hardware.main_gpu(gl)["vendor"], "NVIDIA")
        hardware._nv_cache["at"] = 0


class TestOldAndSmallGpus(unittest.TestCase):
    def test_old_nvidia_cards_use_vulkan(self):
        from unittest import mock
        from pumpkin import hardware
        with mock.patch.object(hardware, "MAC", False), mock.patch.object(hardware, "ANDROID", False), \
                mock.patch.object(hardware.shutil, "which", lambda b: "nvidia-smi"):
            for name, want in (("NVIDIA GeForce GT 710", "vulkan"), ("NVIDIA GeForce GTX 760", "vulkan"),
                               ("NVIDIA GeForce GTX 750 Ti", "cuda"), ("NVIDIA GeForce RTX 3060", "cuda")):
                gl = [{"vendor": "NVIDIA", "name": name, "vram_total": 2.0}]
                self.assertEqual(hardware.recommended_backend(gl), want, name)

    def test_a_2gb_card_still_gets_models_from_ram(self):
        cat = models.catalog()
        r = recommend.recommend(recommend.curated_items(cat), {"vram_total": 2.0, "ram_total": 8.0}, curated=cat)
        self.assertTrue(r["slow"])
        self.assertEqual(r["slow"][0]["name"], "Qwen3 4B Instruct")
