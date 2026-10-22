"""Importing settings from the earlier LLM Studio script (runs in its own HOME)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = r'''
import json, os
from pumpkin import config, migrate
assert migrate.available()
done = migrate.run()
print(json.dumps({"done": done, "presets": config.load("presets", []), "tasks": config.load("tasks", []),
                  "settings": config.load("settings", {})}))
'''


class TestMigrate(unittest.TestCase):
    def test_import_from_llm_studio(self):
        home = tempfile.mkdtemp()
        old = os.path.join(home, ".config", "llm-studio")
        os.makedirs(old)
        presets = [{"name": "⚡ Fast Agent", "desc": "MoE", "model": "*35B-A3B*.gguf", "mmproj": "", "ctx": 131072,
                    "fitt": 1024, "kv": "q8_0", "think_budget": 2048, "agent": True, "extra": ["--temp", "0.6"]}]
        tasks = [{"name": "Morning", "time": "08:00", "days": "daily", "preset": "⚡ Fast Agent", "enabled": True,
                  "prompt": "news"}]
        json.dump(presets, open(os.path.join(old, "presets.json"), "w"))
        json.dump(tasks, open(os.path.join(old, "tasks.json"), "w"))
        json.dump([{"name": "Tutor", "prompt": "Teach"}], open(os.path.join(old, "personas.json"), "w"))
        json.dump({"threads": 6, "persona": "Tutor", "sandbox_image": "ubuntu:24.04"},
                  open(os.path.join(old, "settings.json"), "w"))
        open(os.path.join(home, "mcp.json"), "w").write('{"mcpServers": {}}')
        os.makedirs(os.path.join(home, "llm-reports"))
        env = dict(os.environ, HOME=home, USERPROFILE=home, PYTHONPATH=ROOT, XDG_CONFIG_HOME=os.path.join(home, ".config"),
                   XDG_DATA_HOME=os.path.join(home, ".local/share"), XDG_STATE_HOME=os.path.join(home, ".local/state"))
        out = subprocess.run([sys.executable, "-c", SCRIPT], env=env, capture_output=True, text=True, check=True)
        r = json.loads(out.stdout)
        p = r["presets"][0]
        self.assertTrue(p["tools"])
        self.assertEqual(p["extra"], "--temp 0.6")
        self.assertEqual(r["tasks"][0]["preset"], p["id"])          # task now points at the preset id
        self.assertEqual(r["settings"]["persona"], "Tutor")
        self.assertTrue(r["settings"]["reports_dir"].endswith("llm-reports"))
        self.assertIn("MCP servers", r["done"])


if __name__ == "__main__":
    unittest.main()
