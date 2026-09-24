"""THE OPS WORKFLOW'S TWO FAILURE MODES, both of which I have hit.

1. A HARD 512,000-BYTE CEILING. Over it, the workflow returns
   `startup_failure` with no log and no annotation, which looks exactly
   like a quoting bug and is not one.

2. QUOTING. The `sql` cases are shell strings holding SQL, so an
   unbalanced quote breaks the ENTIRE case statement -- and the error
   surfaces against whichever case happens to FOLLOW, not the one that
   is wrong. That is how a `desk-recon` case missing its closing quote
   reported "syntax error near unexpected token `('" against
   `desk-live`, several hundred lines away.

   I first blamed a `$'` sequence in a regex anchor. That was wrong:
   inside a double-quoted string `$'` is an ordinary dollar followed by
   a quote, not ANSI-C quoting, and an existing working case
   (`loss-breaker`) contains one. The cause was only the missing quote,
   and the check below finds it without needing a theory about why.

Both are checked here rather than discovered by dispatching a run.
"""
from __future__ import annotations

import os
import re
import subprocess

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WF = os.path.join(_ROOT, ".github", "workflows", "render-ops.yml")

# Decimal, not KiB. Measured against a real startup_failure at 513,127.
CEILING = 512_000


def test_the_workflow_is_under_the_startup_ceiling():
    size = os.path.getsize(WF)
    assert size < CEILING, (
        "render-ops.yml is %d bytes, over the %d ceiling. Render returns "
        "startup_failure with NO log, which reads as a syntax error. "
        "Remove a spent one-shot case to pay for a new one."
        % (size, CEILING))


def _sql_cases(src):
    """Every `<name>) SQL="..."; TO=<n> ;;` case, as shell text."""
    out = []
    for m in re.finditer(r"\n\s*([a-z0-9-]+(?::[A-Z]+)?)\)\s*SQL=", src):
        start = m.start() + 1
        end = src.find("TO=", m.end())
        if end < 0:
            continue
        end = src.find(";;", end)
        if end < 0:
            continue
        out.append((m.group(1), src[start:end + 2]))
    return out


def test_every_sql_case_is_valid_shell():
    """THE CHECK THAT WOULD HAVE CAUGHT IT.

    A case whose quoting is wrong takes the whole `case` statement with
    it, so the runner blames the NEXT case. Each one is parsed on its
    own here, which puts the blame on the right line.
    """
    src = open(WF).read()
    cases = _sql_cases(src)
    assert len(cases) > 20, (
        "only %d sql cases found -- the extraction is wrong and the "
        "check would pass vacuously" % len(cases))
    broken = []
    for name, body in cases:
        script = "case x in\n%s\nesac\n" % body
        r = subprocess.run(["bash", "-n"], input=script, text=True,
                           capture_output=True)
        if r.returncode != 0:
            broken.append((name, r.stderr.strip()[:160]))
    assert not broken, broken


def test_the_shell_check_catches_a_missing_closing_quote():
    """CONTROL. Without this the check above could be passing because
    it parses nothing, not because the cases are sound."""
    broken = 'case x in\ndesk-x) SQL="SELECT count(*) FROM t;; TO=1 ;;\nesac\n'
    r = subprocess.run(["bash", "-n"], input=broken, text=True,
                       capture_output=True)
    assert r.returncode != 0, "a missing closing quote parsed cleanly"
    # and the sound form of the same case does parse
    ok = 'case x in\ndesk-x) SQL="SELECT count(*) FROM t;"; TO=1 ;;\nesac\n'
    r2 = subprocess.run(["bash", "-n"], input=ok, text=True,
                        capture_output=True)
    assert r2.returncode == 0, r2.stderr
