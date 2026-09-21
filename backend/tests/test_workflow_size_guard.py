"""The workflow guard must catch the failure that produced it.

2026-09-21: render-ops.yml -- the lever that operates Render, reads the
production database and holds the trading kill switch -- was disabled by
an eight-line comment. GitHub caps a workflow at 512,000 bytes; the file
stood at 511,808 and the comment took it to 512,388. Every dispatch
after that returned startup_failure, and two were sent before anyone
looked.

`yaml.safe_load` passed throughout, which is why "we validate the YAML"
was never a control. These tests pin the three things that are actually
checked, and the second one is the one that matters: the file at
511,808 bytes has to FAIL too. It was under the ceiling and it was still
one ordinary comment from taking the lever down.
"""

import os
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHECKER = os.path.join(REPO, "scripts", "check_workflows.py")


def _run(root):
    r = subprocess.run([sys.executable, CHECKER, root],
                       capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


def _tree(tmp_path, name, body):
    d = tmp_path / ".github" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body)
    return str(tmp_path)


GOOD = """
name: fine
on:
  workflow_dispatch:
jobs:
  go:
    runs-on: ubuntu-latest
    steps:
      - name: do a thing
        run: |
          set -e
          echo "hello ${{ github.sha }}"
"""


def test_a_healthy_workflow_passes(tmp_path):
    rc, out = _run(_tree(tmp_path, "fine.yml", GOOD))
    assert rc == 0, out


def test_expression_interpolation_is_not_a_syntax_error(tmp_path):
    """${{ }} is not shell. Left in place it makes every templated
    script look broken, and a check that cries wolf gets switched off."""
    rc, out = _run(_tree(tmp_path, "fine.yml", GOOD))
    assert rc == 0, out


# ── the historical failure, both halves ──────────────────────────────

def test_a_file_over_the_ceiling_fails(tmp_path):
    """THE DEFECT. 512,388 bytes: GitHub refuses to start it."""
    pad = "# " + "x" * 78 + "\n"
    body = GOOD + pad * (512_400 // len(pad))
    root = _tree(tmp_path, "big.yml", body)
    assert os.path.getsize(
        os.path.join(root, ".github/workflows/big.yml")) >= 512_000
    rc, out = _run(root)
    assert rc == 1
    assert "OVER the 512000-byte ceiling" in out


def test_a_file_just_under_the_ceiling_also_fails(tmp_path):
    """THE HALF THAT MATTERS. render-ops.yml sat at 511,808 bytes --
    under the limit, and passing every check there was. It was 192 bytes
    from death and nothing said so. Headroom is the control; the ceiling
    is just where the lever is already gone."""
    pad = "# " + "x" * 78 + "\n"
    body = GOOD + pad * (505_000 // len(pad))
    root = _tree(tmp_path, "tight.yml", body)
    size = os.path.getsize(os.path.join(root, ".github/workflows/tight.yml"))
    assert size < 512_000, "this fixture must be UNDER the hard ceiling"
    rc, out = _run(root)
    assert rc == 1, "a file with no headroom must not pass"
    assert "headroom" in out


def test_the_real_render_ops_has_real_headroom():
    """The repository's own tightest file, checked against the minimum
    rather than against the ceiling."""
    p = os.path.join(REPO, ".github", "workflows", "render-ops.yml")
    size = os.path.getsize(p)
    assert size < 512_000 - 32_768, (
        "render-ops.yml is back inside the danger band at %d bytes" % size)


# ── the two checks YAML parsing cannot make ──────────────────────────

def test_broken_shell_inside_valid_yaml_is_caught(tmp_path):
    """A run: block is a string to YAML. It parses whether or not the
    script is a script."""
    bad = GOOD.replace('echo "hello ${{ github.sha }}"',
                       'if [ -z "$X" ; then echo oops')
    rc, out = _run(_tree(tmp_path, "shell.yml", bad))
    assert rc == 1
    assert "syntax error" in out.lower() or "unexpected" in out.lower()


def test_a_job_with_no_steps_is_caught(tmp_path):
    rc, out = _run(_tree(tmp_path, "nosteps.yml", """
name: x
on:
  workflow_dispatch:
jobs:
  go:
    runs-on: ubuntu-latest
"""))
    assert rc == 1
    assert "no steps" in out


def test_a_workflow_with_no_triggers_is_caught(tmp_path):
    rc, out = _run(_tree(tmp_path, "notrig.yml", """
name: x
jobs:
  go:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""))
    assert rc == 1
    assert "trigger" in out


def test_on_is_not_reported_missing_because_yaml_says_true(tmp_path):
    """PyYAML resolves a bare `on:` key to the boolean True. A checker
    that looks only for the string "on" reports every workflow in the
    repo as missing its triggers, which is how a noisy check becomes an
    ignored one."""
    rc, out = _run(_tree(tmp_path, "fine.yml", GOOD))
    assert rc == 0
    assert "trigger" not in out


def test_the_whole_repository_passes():
    """The guard is only a control if it is green on what is committed."""
    rc, out = _run(REPO)
    assert rc == 0, out
