"""Prints the end of a log as a GitHub annotation, which is easy to read from a run's summary page."""
import sys

level, title, path = sys.argv[1], sys.argv[2], sys.argv[3]
raw = open(path, "rb").read()
if raw.startswith((b"\xff\xfe", b"\xfe\xff")):          # Windows PowerShell 5 writes UTF-16
    text = raw.decode("utf-16", "replace")
else:
    text = raw.decode("utf-8-sig", "replace")
lines = [l.rstrip() for l in text.splitlines() if "ResourceWarning" not in l and not l.startswith("  ")]
msg = "\n".join(lines[-80:]).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
print(f"::{level} title={title}::{msg}")
