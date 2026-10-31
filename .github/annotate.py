"""Prints a test log as a GitHub annotation, which is easy to read from a run's summary page.

GitHub keeps only the first few thousand characters of an annotation, so the failures (unittest's FAIL and ERROR
blocks, or a faulthandler dump) come first and the end of the log after them."""
import re
import sys

level, title, path = sys.argv[1], sys.argv[2], sys.argv[3]
raw = open(path, "rb").read()
if raw.startswith((b"\xff\xfe", b"\xfe\xff")):          # Windows PowerShell 5 writes UTF-16
    text = raw.decode("utf-16", "replace")
else:
    text = raw.decode("utf-8-sig", "replace")
lines = [l.rstrip() for l in text.splitlines() if "ResourceWarning" not in l]

failures, keep = [], False
for l in lines:
    if re.match(r"^(FAIL|ERROR): ", l) or l.startswith(("Fatal Python error", "Timeout (")):
        keep = True
    elif re.match(r"^(Ran \d+ tests|={20,}$)", l) and keep and failures and not failures[-1].startswith("="):
        keep = l.startswith("=")
    if keep:
        failures.append(l)
summary = [l for l in lines[-6:] if l.startswith(("Ran ", "FAILED", "OK"))]
# a script that is not unittest (tests/e2e_real.py) prints "== step" headings: keep the last few, so a failure shows
# which step it was in, and the start of a traceback, which names the call that failed
steps = [l for l in lines if l.startswith("== ")][-4:]
tb = next((i for i, l in enumerate(lines) if l.startswith("Traceback")), None)
head = lines[tb:tb + 12] if tb is not None and not failures else []
body = failures[:60] + ([""] if failures else []) + summary
if not failures:
    body += (["Steps:"] + steps if steps else []) + (["", "Start of the traceback:"] + head if head else []) + ["", "End of the log:"] + lines[-12:]
msg = "\n".join(body)[:3800].replace("%", "%25").replace("\r", "").replace("\n", "%0A")
print(f"::{level} title={title}::{msg}")
