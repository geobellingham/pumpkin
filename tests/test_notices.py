"""The Overview's warning when the model is swapped out or reads itself from disk, and the restart after pairing."""
import os
import sys
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pumpkin import agent, core, hardware, llama  # noqa: E402


class Proc:
    pid = 4242

    def poll(self):
        return None


def fake_core():
    c = types.SimpleNamespace(_disk=None, _disk_hits=0)
    c.memory_note = lambda *a: core.Core.memory_note(c, *a)
    return c


SRV = {"state": "ready", "preset": {"name": "Qwen3-Next"}}


class TestMemoryNote(unittest.TestCase):
    def test_swap(self):
        c = fake_core()
        with mock.patch.object(hardware, "proc_disk", return_value={"swap": 3.2, "read": 0}):
            n = c.memory_note(Proc(), SRV, {})
        self.assertEqual(n["kind"], "swap")
        self.assertIn("3.2 GB of Qwen3-Next", n["text"])

    def test_reading_from_disk_while_writing(self):
        c = fake_core()
        reads = iter(range(0, 10 ** 11, 400 * 2 ** 20))           # 400 MB more every look
        clock = iter(range(1000, 2000, 2))
        notes = []
        with mock.patch.object(hardware, "proc_disk", side_effect=lambda pid: {"swap": 0, "read": next(reads)}), \
                mock.patch.object(core, "time", types.SimpleNamespace(time=lambda: next(clock))):
            for i in range(5):
                notes.append(c.memory_note(Proc(), SRV, {"n_decode_total": 100 + i * 20}))
        self.assertIsNone(notes[2])                                 # one burst is not enough
        self.assertEqual(notes[4]["kind"], "disk")
        self.assertIn("200 MB/s", notes[4]["text"])

    def test_reading_a_saved_conversation_is_not_a_problem(self):
        c = fake_core()
        reads = iter(range(0, 10 ** 11, 400 * 2 ** 20))
        clock = iter(range(1000, 2000, 2))
        with mock.patch.object(hardware, "proc_disk", side_effect=lambda pid: {"swap": 0, "read": next(reads)}), \
                mock.patch.object(core, "time", types.SimpleNamespace(time=lambda: next(clock))):
            notes = [c.memory_note(Proc(), SRV, {"n_decode_total": 100}) for _ in range(6)]   # nothing written
        self.assertEqual(notes, [None] * 6)

    def test_mac_and_windows_say_nothing(self):
        with mock.patch.object(hardware, "proc_disk", return_value=None):
            self.assertIsNone(fake_core().memory_note(Proc(), SRV, {}))


class TestSpecVerdict(unittest.TestCase):
    def test_words(self):
        self.assertEqual(agent.spec_verdict({"new": 40, "edit": 80}, {"new": 40, "edit": 40})[0], "faster")
        self.assertEqual(agent.spec_verdict({"new": 30, "edit": 39}, {"new": 40, "edit": 40})[0], "slower")
        self.assertEqual(agent.spec_verdict({"new": 40, "edit": 42}, {"new": 40, "edit": 40})[0], "same")


class TestRestartAfterPairing(unittest.TestCase):
    def test_restarts_once_idle(self):
        started = []
        server = types.SimpleNamespace(state="ready", preset={"id": "p", "name": "Qwen", "tools": True}, notice=None)
        server.start = lambda p: started.append(p)
        server._changed = lambda: None
        idle = iter([False, False, True])
        c = types.SimpleNamespace(server=server, devices_loaded=lambda: False, model_idle=lambda: next(idle))
        with mock.patch.object(core, "time", types.SimpleNamespace(time=time.time, sleep=lambda s: None)):
            core.Core.after_pairing(c, "MacBook")
            for _ in range(100):
                if started:
                    break
                time.sleep(0.01)
        self.assertEqual(started, [server.preset])
        self.assertIn("ask MacBook", server.notice)

    def test_leaves_a_model_without_tools_alone(self):
        server = types.SimpleNamespace(state="ready", preset={"id": "p", "tools": False})
        server.start = mock.Mock()
        c = types.SimpleNamespace(server=server, devices_loaded=lambda: False, model_idle=lambda: True)
        core.Core.after_pairing(c, "Phone")
        time.sleep(0.1)
        server.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
