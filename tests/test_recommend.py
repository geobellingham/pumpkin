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

    def test_offline_falls_back_to_catalog(self):
        r = recommend.recommend(recommend.curated_items(models.catalog()), BIG, curated=models.catalog())
        self.assertTrue(r["gpu"] and r["moe"])
        self.assertTrue(all(x.get("tested") for x in r["gpu"] + r["moe"]))


if __name__ == "__main__":
    unittest.main()
