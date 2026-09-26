"""Every `run:` step in every workflow parses as shell. Third time lucky.

THE RECURRING DEFECT, three instances now, all the same apostrophe:

  1 `release's` inside `jq -r '...'` closed the shell quote, jq got a
    truncated program, and the text after it was handed to the shell. The
    step exited 3 and took the live-SHA readback with it.
  2 my own scanner for (1) flagged 78 closing quotes as defects, because
    it did not understand the `'"'"'` escape.
  3 `the venue'"'"'s` inside a DOUBLE-quoted `say "..."`. That escape is
    correct only when the surrounding context is single-quoted; inside
    double quotes an apostrophe needs no escaping at all, and writing the
    escape there left an unterminated single quote that swallowed the next
    twenty lines -- including a heredoc, whose body was then parsed as
    commands.

`bash -n` on the CONCATENATION of steps is not the check: steps are
independent scripts, and joining them produces both false alarms and false
clears. Each step is checked on its own, which is what found (3).

This is cheap, it is mechanical, and it is the only check that has ever
caught this class.
"""

import pathlib
import subprocess
import tempfile

import pytest
import yaml

WORKFLOWS = sorted(
    pathlib.Path(__file__).resolve().parents[2].joinpath(
        ".github/workflows").glob("*.yml"))


def _steps(path):
    doc = yaml.safe_load(path.read_text())
    for job_name, job in (doc.get("jobs") or {}).items():
        for i, step in enumerate(job.get("steps") or []):
            run = step.get("run")
            if isinstance(run, str) and run.strip():
                yield job_name, i, step.get("name") or "", run


def test_there_are_workflows_to_check():
    assert WORKFLOWS, "no workflow files found -- the check would pass vacuously"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_run_step_parses_as_shell(path):
    broken = []
    for job, idx, name, run in _steps(path):
        with tempfile.NamedTemporaryFile("w", suffix=".sh") as fh:
            fh.write(run)
            fh.flush()
            proc = subprocess.run(["bash", "-n", fh.name],
                                  capture_output=True, text=True)
        if proc.returncode:
            first = (proc.stderr.strip().splitlines() or [""])[0]
            broken.append("%s job=%s step=%d %r: %s"
                          % (path.name, job, idx, name[:40], first[:160]))
    assert not broken, "\n".join(broken)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_apostrophe_escape_inside_a_double_quoted_string(path):
    """The exact shape of defect (3), refused by pattern as well as by bash.

    `bash -n` catches it only when the unterminated quote happens to break
    something later in the same step. A step where it balances out by
    accident still means something other than what was written.
    """
    bad = []
    for job, idx, name, run in _steps(path):
        for n, line in enumerate(run.splitlines(), 1):
            # a say/echo of a double-quoted string that uses the
            # single-quote escape inside it
            st = line.strip()
            if not st.startswith(("say ", "echo ")):
                continue
            body = st.split(" ", 1)[1]
            if body.startswith('"') and "'\"'\"'" in body:
                bad.append("%s job=%s step=%d line=%d: %s"
                           % (path.name, job, idx, n, st[:110]))
    assert not bad, (
        "inside a double-quoted string an apostrophe needs no escaping; "
        "the escape there ends the string early:\n" + "\n".join(bad))
