"""Keeping llama.cpp up to date: picking a release, the API check, installing and going back."""
import os
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fixtures  # noqa: E402
from pumpkin import config, core, llama, llamaup  # noqa: E402

DAY = 86400
NOW = 1_800_000_000


def rel(n, age_days, backend="vulkan"):
    tag = f"b{n}"
    return {"tag": tag, "at": NOW - age_days * DAY, "assets": llama.asset_names(tag, backend, "x64", "linux")}


class TestPick(unittest.TestCase):
    def test_release_waits_a_day_newest_does_not(self):
        rels = [rel(12010, 0.2), rel(12005, 1.5), rel(12000, 3)]
        self.assertEqual(llamaup.pick(rels, "vulkan", "release", now=NOW, arch="x64", system="linux"), "b12005")
        self.assertEqual(llamaup.pick(rels, "vulkan", "newest", now=NOW, arch="x64", system="linux"), "b12010")

    def test_skips_builds_that_failed_here_and_ones_without_a_file(self):
        rels = [rel(12010, 2), rel(12005, 2, backend="cuda"), rel(12000, 3)]
        self.assertEqual(llamaup.pick(rels, "vulkan", "release", bad={"b12010": "x"}, now=NOW, arch="x64",
                                      system="linux"), "b12000")
        self.assertEqual(llamaup.pick(rels, "vulkan", "release", bad={"b12010-vulkan": "x"}, now=NOW, arch="x64",
                                      system="linux"), "b12000")
        self.assertEqual(llamaup.pick(rels, "cuda", "release", bad={"b12010-vulkan": "x"}, now=NOW, arch="x64",
                                      system="linux"), "b12005")

    def test_builds_too_new_for_this_system_are_not_tried_again(self):
        rels = [rel(12010, 2), rel(12005, 2), rel(12000, 3)]
        bad = {"too_new/vulkan": {"tag": "b12005"}}
        self.assertEqual(llamaup.pick(rels, "vulkan", "release", bad=bad, now=NOW, arch="x64", system="linux"), "b12000")
        cpu = [rel(12010, 2, "cpu"), rel(12000, 3, "cpu")]                 # the CPU build of the same release may run
        self.assertEqual(llamaup.pick(cpu, "cpu", "release", bad=bad, now=NOW, arch="x64", system="linux"), "b12010")

    def test_every_release_marked_prerelease_still_works(self):
        rels = [dict(rel(12010, 2), pre=True), dict(rel(12000, 3), pre=True)]     # how llama.cpp publishes today
        self.assertEqual(llamaup.pick(rels, "vulkan", "release", now=NOW, arch="x64", system="linux"), "b12010")

    def test_system_hint(self):
        err = "llama-server: /lib/aarch64-linux-gnu/libc.so.6: version `GLIBC_2.38' not found"
        self.assertRegex(err, llamaup.SYSTEM_TOO_OLD)
        self.assertIn("glibc 2.38", llamaup.system_hint(err))

    def test_build_numbers(self):
        self.assertEqual(llamaup.build_number("b11796"), 11796)
        self.assertEqual(llamaup.build_number("v1.2"), 0)


class _Dirs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for p in (mock.patch.object(llama, "LLAMA_DIR", os.path.join(self.tmp, "llama")),
                  mock.patch.object(llamaup, "STATE_PATH", os.path.join(self.tmp, "llama-update.json"))):
            p.start()
            self.addCleanup(p.stop)

    def fake_build(self, n, backend="vulkan", env=None):
        """A Pumpkin download of build n: the fake llama-server, answering --version with that number."""
        d = os.path.join(llama.LLAMA_DIR, f"b{n}-{backend}", "build", "bin")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "llama-server")
        with open(path, "w") as f:
            f.write(f"#!/bin/sh\nexport FAKE_LLAMA_BUILD={n}\n" + "".join(f"export {k}={v}\n" for k, v in (env or {}).items())
                    + f'exec "{sys.executable}" "{d}/fake.py" "$@"\n')
        os.chmod(path, 0o755)
        with open(os.path.join(d, "fake.py"), "w") as f:
            f.write(fixtures.FAKE_SERVER)
        return path


@unittest.skipIf(os.name == "nt", "the fake builds here are shell scripts")
class TestDownloads(_Dirs):
    def test_lists_and_prunes_only_pumpkins_own(self):
        a, b, c = self.fake_build(12000), self.fake_build(12100), self.fake_build(12200, "cuda")
        self.assertEqual([d["tag"] for d in llamaup.downloaded()], ["b12200", "b12100", "b12000"])
        self.assertTrue(llamaup.owned(a))
        self.assertFalse(llamaup.owned("/usr/local/bin/llama-server"))
        self.assertEqual(llamaup.backend_of(c, "vulkan"), "cuda")
        self.assertEqual(llamaup.backend_of("/elsewhere/llama-server", "vulkan"), "vulkan")
        llamaup.prune([c, b])
        self.assertFalse(os.path.exists(a))
        self.assertTrue(os.path.exists(b) and os.path.exists(c))

    def _core(self, **settings):
        s = {"llama_server": "", "llama_previous": "", "llama_updates": "notify", "llama_channel": "release"}
        s.update(settings)
        settings_obj = mock.MagicMock()
        settings_obj.__getitem__.side_effect = lambda k: s[k]
        settings_obj.update.side_effect = lambda ch: s.update(ch)
        c = types.SimpleNamespace(settings=settings_obj, data=s)
        c._install_llama = lambda *a, **k: core.Core._install_llama(c, *a, **k)
        return c

    def test_install_keeps_the_old_one_for_going_back(self):
        old = self.fake_build(12000)
        c = self._core(llama_server=old)
        new = self.fake_build(12100)                      # as if just downloaded
        with mock.patch.object(llama, "download_release", side_effect=AssertionError("already on disk")):
            path = c._install_llama("vulkan", "b12100")
        self.assertEqual(path, new)
        self.assertEqual(c.data["llama_server"], new)
        self.assertEqual(c.data["llama_previous"], old)

    def test_update_button_then_starts_the_preset(self):
        old = self.fake_build(12000)
        c = self._core(llama_server=old)
        c.jobs, c.hardware = {}, lambda: {"backend": "vulkan"}
        c.start_job = lambda *a, **k: core.Core.start_job(c, *a, **k)
        c.presets = types.SimpleNamespace(get=lambda i: {"id": i, "name": "LFM3"})
        started = []
        c.server = types.SimpleNamespace(start=lambda p: started.append(p))
        new = self.fake_build(12100)
        with mock.patch.object(llamaup, "target_tag", return_value="b12100") as tt, \
                mock.patch.object(llama, "find_server", side_effect=lambda st: st["llama_server"]):
            job = core.Core.download_llama(c, then_start="p1")
            for _ in range(100):
                if job["state"] != "running":
                    break
                time.sleep(0.05)
        self.assertEqual(job["state"], "done", job.get("error"))
        self.assertEqual(job["tag"], "b12100")
        self.assertEqual(c.data["llama_server"], new)
        self.assertEqual(started, [{"id": "p1", "name": "LFM3"}])
        self.assertEqual(tt.call_args.kwargs.get("channel"), "newest")       # a brand-new model needs the newest build

    def test_a_build_that_does_not_run_here_is_dropped(self):
        old = self.fake_build(12000)
        c = self._core(llama_server=old)

        def broken(backend, tag, **k):
            d = os.path.join(llama.LLAMA_DIR, f"{tag}-{backend}")
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, "llama-server")
            with open(p, "w") as f:
                f.write("#!/bin/sh\necho 'dyld: Symbol not found, built for macOS 15' >&2\nexit 1\n")
            os.chmod(p, 0o755)
            return p
        with mock.patch.object(llama, "download_release", side_effect=broken):
            with self.assertRaises(RuntimeError) as e:
                c._install_llama("vulkan", "b12300")
        self.assertIn("kept the one you had", str(e.exception))
        self.assertIn("newer macOS", str(e.exception))
        self.assertEqual(c.data["llama_server"], old)
        self.assertEqual(llamaup.too_new()["vulkan"]["tag"], "b12001")     # everything newer than the one that runs
        self.assertFalse(os.path.exists(os.path.join(llama.LLAMA_DIR, "b12300-vulkan")))


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipIf(os.name == "nt", "the fake builds here are shell scripts")
class TestApiCheck(_Dirs):
    def run_fake(self, env):
        port = _free_port()
        model = fixtures.make_gguf(os.path.join(self.tmp, "m.gguf"), arch="llama", experts=0)
        p = subprocess.Popen([sys.executable, "-c", fixtures.FAKE_SERVER, "-m", model, "--port", str(port)],
                             env={**os.environ, "LLAMA_API_KEY": llama.api_key(), "FAKE_LLAMA_LOAD": "0", **env},
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        for _ in range(50):
            if llama.health(port):
                break
            time.sleep(0.1)
        return port

    def test_the_api_pumpkins_chat_uses(self):
        port = self.run_fake({})
        self.assertEqual(llamaup.check_api(port, ["--agent"]), ([], []))

    def test_a_changed_props_is_serious(self):
        port = self.run_fake({"FAKE_LLAMA_NEW_API": "1"})
        serious, _ = llamaup.check_api(port)
        self.assertTrue(serious)
        self.assertIn("context size", serious[0])


@unittest.skipIf(os.name == "nt", "the fake builds here are shell scripts")
class TestAfterReady(_Dirs):
    """A new build that breaks the chat goes back to the one before and is skipped from then on."""

    def _core(self, binary, previous=""):
        s = {"llama_server": binary, "llama_previous": previous, "llama_updates": "notify", "llama_channel": "release"}
        settings = mock.MagicMock()
        settings.__getitem__.side_effect = lambda k: s[k]
        settings.update.side_effect = lambda ch: s.update(ch)
        server = types.SimpleNamespace(port=1, notice=None, started=[], changed=0)
        server.start = lambda p: server.started.append((p, s["llama_server"]))
        server._changed = lambda: None
        c = types.SimpleNamespace(settings=settings, server=server, data=s, hardware=lambda: {"backend": "vulkan"})
        c._install_llama = lambda *a, **k: core.Core._install_llama(c, *a, **k)
        return c

    def test_serious_problem_goes_back(self):
        old, new = self.fake_build(12000), self.fake_build(12100)
        c = self._core(new, old)
        with mock.patch.object(llamaup, "check_api", return_value=(["/props changed"], [])), \
                mock.patch.object(llama, "find_server", side_effect=lambda st: st["llama_server"]):
            core.Core._after_ready(c, {"id": "p1", "name": "Tiny"})
        self.assertEqual(c.data["llama_server"], old)
        self.assertEqual(c.server.started, [({"id": "p1", "name": "Tiny"}, old)])
        self.assertIn("went back to b12000", c.server.notice)
        self.assertIn("b12100", llamaup.bad_tags())

    def test_good_build_is_remembered_and_old_ones_cleared(self):
        oldest, old, new = self.fake_build(11900), self.fake_build(12000), self.fake_build(12100)
        c = self._core(new, old)
        with mock.patch.object(llamaup, "check_api", return_value=([], [])) as chk, \
                mock.patch.object(llama, "find_server", side_effect=lambda st: st["llama_server"]):
            core.Core._after_ready(c, {"id": "p1"})
            core.Core._after_ready(c, {"id": "p1"})
        self.assertEqual(chk.call_count, 1)                 # asked once per build
        self.assertTrue(llamaup.checked_ok("b12100"))
        self.assertFalse(os.path.exists(oldest))
        self.assertTrue(os.path.exists(old))                # kept for going back
        self.assertEqual(c.server.started, [])


class TestWeekBehind(_Dirs):
    def test_stale_only_when_a_week_older(self):
        s = {"llama_channel": "release"}
        now = time.time()
        llamaup._save({"latest": {"vulkan/release": "b12100"}, "dates": {"b12100": now - 3600, "b12090": now - 7200},
                       "published": {"b11796": now - 12 * DAY}})
        self.assertTrue(llamaup.cached(s, "vulkan", "b11796")["stale"])
        self.assertFalse(llamaup.cached(s, "vulkan", "b12090")["stale"])       # newer is out, but only hours newer
        self.assertFalse(llamaup.cached(s, "vulkan", "b12000")["stale"])       # date unknown: say nothing yet


class TestUnknownArchFix(unittest.TestCase):
    def test_offers_update_on_too_old_error(self):
        m = llama.ServerManager.__new__(llama.ServerManager)
        m.state, m.error, m.notice, m.preset, m.started_at = "error", "This llama-server is too old for this model", \
            None, None, None
        m.settings = {"llama_port": 8080}
        self.assertEqual(m.status()["fix"], "update_llama")
        m.error = "The model did not fit in memory."
        self.assertIsNone(m.status()["fix"])


if __name__ == "__main__":
    unittest.main()
