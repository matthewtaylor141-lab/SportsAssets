"""COMPARE FULL-SUITE FAILURE IDENTITIES, NOT COUNTS AND NOT FILENAMES.

The owner ruled both of the cheaper comparisons out, and correctly:

    "Compare full-suite failure identities against a matched baseline;
     matching counts or inspecting failing filenames is insufficient."

MATCHING COUNTS proves nothing: one failure fixed and one introduced is the
same count. INSPECTING FILENAMES is nearly as weak -- a file with four
pre-existing failures that now has five, or four DIFFERENT ones, reads as
unchanged. The only comparison that answers "did my change break anything"
is the SET of node ids.

Both inputs are `pytest -q -rf` output, whose short summary lists every
failure as `FAILED <nodeid> - <reason>`. The node id is the identity.
"""

import re
import sys

# A REAL NODE ID CONTAINS `::` AND NAMES A TEST FILE.
#
# THE DEFECT THIS FIXES, AND IT WAS MY OWN TOOLING. The pattern was
# `^(?:FAILED|ERROR)\s+(\S+)`, which is correct for the `-rf` summary and WRONG
# for captured log output. When I changed the gate from --tb=no to --tb=short so
# that a rare failure would be diagnosable, pytest began printing captured logs,
# and lines like
#
#     ERROR    sportsassets.execution_gate:execution_gate.py:248 execution gate
#     NOT bound -- every order this process attempts will be refused
#
# were counted as failing node ids. The gate on 34912d8 duly reported "3 NEW
# FAILING NODE ID(S)" whose identities were log lines, while its own summary said
# 367 failed -- exactly the baseline. The give-away was the inconsistency the
# comparator printed about itself: 367 failed, 370 distinct node ids.
#
# A tool that manufactures new failures is worse than no tool: it spends a
# reviewer's attention on nothing and it would eventually be used to dismiss a
# real one. So the token must look like a node id: a test path, and `::`.
PAT = re.compile(r"^(?:FAILED|ERROR)\s+((?:\S*/)?test[^\s]*::\S+)")


def ids(path):
    out = set()
    for line in open(path, encoding="utf-8", errors="replace"):
        m = PAT.match(line.strip())
        if m:
            out.add(m.group(1))
    return out


def tail(path):
    for line in reversed(open(path, encoding="utf-8",
                              errors="replace").readlines()):
        if "passed" in line or "failed" in line:
            return line.strip()
    return "<no summary line>"


base_path, head_path = sys.argv[1], sys.argv[2]
base, head = ids(base_path), ids(head_path)

print("BASELINE  %s" % base_path)
print("  summary: %s" % tail(base_path))
print("  distinct failing node ids: %d" % len(base))
print("HEAD      %s" % head_path)
print("  summary: %s" % tail(head_path))
print("  distinct failing node ids: %d" % len(head))
print()

new = sorted(head - base)
fixed = sorted(base - head)
print("NEW FAILURES (in HEAD, not in the baseline): %d" % len(new))
for n in new:
    print("  + %s" % n)
print()
print("NO LONGER FAILING (in the baseline, not in HEAD): %d" % len(fixed))
for n in fixed:
    print("  - %s" % n)
print()
print("COMMON (pre-existing, unchanged): %d" % len(base & head))
print()
if not new:
    print("VERDICT: NO NEW FAILURE IDENTITY. Every failure in HEAD is one the "
          "matched baseline already had.")
else:
    print("VERDICT: %d NEW FAILING NODE ID(S). Listed above; each one has to be "
          "explained or fixed before this is acceptance evidence." % len(new))
sys.exit(1 if new else 0)
