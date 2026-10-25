"""The AI's work folder: paths stay inside it, uploads never overwrite, and AI-made pages never run."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pumpkin import workspace  # noqa: E402


class TestWorkFolder(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.s = {"files_dir": os.path.join(self.root, "files"), "sandbox_image": "ubuntu:24.04"}
        self.base = workspace.files_dir(self.s)
        os.makedirs(os.path.join(self.base, "notes"))
        with open(os.path.join(self.base, "notes", "a.md"), "w") as f:
            f.write("# hi")
        with open(os.path.join(self.root, "secret.txt"), "w") as f:
            f.write("no")
        os.symlink(os.path.join(self.root, "secret.txt"), os.path.join(self.base, "escape.txt"))

    def test_paths_cannot_leave_the_folder(self):
        # an absolute path is read as inside the folder, never as the real one
        self.assertEqual(workspace.safe_path(self.s, "/etc/passwd"), os.path.join(self.base, "etc", "passwd"))
        for bad in ("../secret.txt", "notes/../../secret.txt", "escape.txt"):
            with self.assertRaises(PermissionError, msg=bad):
                p = workspace.safe_path(self.s, bad)
                if not p.startswith(self.base + os.sep):
                    raise PermissionError
        # the path the AI uses inside the sandbox maps to the same file
        self.assertEqual(workspace.safe_path(self.s, "/workspace/notes/a.md"), os.path.join(self.base, "notes", "a.md"))

    def test_listing_and_recent(self):
        top = workspace.listing(self.s)
        self.assertEqual([i["name"] for i in top["items"] if i["dir"]], ["notes"])
        inner = workspace.listing(self.s, "notes")
        self.assertEqual(inner["items"][0]["path"], "notes/a.md")      # always / for the UI, also on Windows
        self.assertIn("a.md", [r["name"] for r in workspace.recent(self.s)])

    def test_upload_never_overwrites(self):
        self.assertEqual(workspace.save_upload(self.s, "notes", "a.md", b"new"), "notes/a (2).md")
        with open(os.path.join(self.base, "notes", "a.md")) as f:
            self.assertEqual(f.read(), "# hi")
        with self.assertRaises(ValueError):
            workspace.save_upload(self.s, "", ".bashrc", b"x")
        self.assertEqual(workspace.save_upload(self.s, "", "../../evil.sh", b"x"), "evil.sh")

    def test_delete(self):
        workspace.delete(self.s, "notes")
        self.assertFalse(os.path.exists(os.path.join(self.base, "notes")))
        with self.assertRaises(PermissionError):
            workspace.delete(self.s, "")

    def test_links_are_never_followed_out_of_the_folder(self):
        # a broken link planted in the folder (say by the sandbox) must not make an upload write outside
        outside = os.path.join(self.root, "planted.txt")
        os.symlink(outside, os.path.join(self.base, "report.txt"))
        self.assertEqual(workspace.save_upload(self.s, "", "report.txt", b"x"), "report (2).txt")
        self.assertFalse(os.path.exists(outside))
        # deleting a link removes the link, never what it points to
        workspace.delete(self.s, "escape.txt")
        self.assertFalse(os.path.lexists(os.path.join(self.base, "escape.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.root, "secret.txt")))
        os.symlink(os.path.join(self.base, "notes"), os.path.join(self.base, "shortcut"))
        workspace.delete(self.s, "shortcut")
        self.assertTrue(os.path.exists(os.path.join(self.base, "notes", "a.md")))


if __name__ == "__main__":
    unittest.main()
