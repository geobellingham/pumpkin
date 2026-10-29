"""After an update `pumpkin up` must end with the new version running, even when the old Pumpkin is slow to quit."""
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

if "pumpkin.config" not in sys.modules:
    _T = tempfile.mkdtemp(prefix="pumpkin-up-")
    os.environ.update(HOME=_T, USERPROFILE=_T, XDG_CONFIG_HOME=f"{_T}/.config", XDG_DATA_HOME=f"{_T}/.local/share",
                      XDG_STATE_HOME=f"{_T}/.local/state", APPDATA=f"{_T}/AppData/Roaming",
                      LOCALAPPDATA=f"{_T}/AppData/Local")

from pumpkin import __main__ as cli, __version__, updates  # noqa: E402


class TestStartNew(unittest.TestCase):
    def run_with(self, versions, **kw):
        seq = iter(versions)
        started, quits = [], []
        with mock.patch.object(cli, "running_version", side_effect=lambda: next(seq)), \
                mock.patch.object(cli, "start_background", side_effect=lambda: started.append(1) or True), \
                mock.patch.object(cli, "urllib_post", side_effect=lambda n, b=None: quits.append(n)), \
                mock.patch.object(cli.time, "sleep"):
            return cli.start_new(**kw), started, quits

    def test_the_old_one_still_answering_is_waited_for_then_the_new_one_starts(self):
        ok, started, quits = self.run_with(["0.9.0", "0.9.0", "0.9.0", None, __version__])
        self.assertTrue(ok)
        self.assertEqual(len(started), 1)                      # started once the port was free
        self.assertEqual(quits, ["quit"])                      # and it was asked to quit again while it lingered

    def test_nothing_running_starts_it(self):
        ok, started, _ = self.run_with([None, __version__])
        self.assertTrue(ok)
        self.assertEqual(len(started), 1)

    def test_the_right_version_already_running_is_left_alone(self):
        ok, started, quits = self.run_with([__version__])
        self.assertTrue((ok, started, quits) == (True, [], []))

    def test_it_gives_up_when_the_old_one_never_goes(self):
        with mock.patch.object(cli, "running_version", return_value="0.9.0"), \
                mock.patch.object(cli, "urllib_post"), mock.patch.object(cli.time, "sleep"), \
                mock.patch.object(cli.time, "time", side_effect=[0, 1, 2, 30, 200, 300, 400, 500]):
            self.assertFalse(cli.start_new(wait=150))


class TestUpdateLog(unittest.TestCase):
    def test_colour_codes_do_not_reach_the_page(self):
        d = tempfile.mkdtemp()
        with mock.patch.object(updates, "_log", return_value=os.path.join(d, "u.log")):
            with open(updates._log(), "w") as f:
                f.write("\x1b[1;33m🎃\x1b[0m Installing to /x/app\n")
            self.assertEqual(updates.log_tail(), "🎃 Installing to /x/app\n")


if __name__ == "__main__":
    unittest.main()
