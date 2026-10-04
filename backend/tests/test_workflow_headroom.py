"""WORKFLOW HEADROOM: THE CAUSE FIXED, AND THE LEVER UNCHANGED.

GitHub refuses to start a workflow whose file exceeds 500 KB (measured here
as 512,000 bytes: 511,808 dispatched, 512,388 returned startup_failure on
2026-09-21). On candidate 27 render-ops.yml -- the lever that deploys the API
and the workers, reads production and holds the kill switch -- stood at
510,399 bytes: 1,601 bytes of headroom, one comment from dead. command-verify
was at 433,890.

THE FIX IS STRUCTURAL, NOT A HIGHER NUMBER. render-ops's one 500-kilobyte
`run:` body moved, byte for byte, to .github/render-ops/ops.sh; the entry-run
step bodies of command-verify moved to .github/command-verify/*.sh. Each
workflow fetches its scripts from the SAME commit (actions/checkout, no
persisted credential) and runs them with the shell GitHub used for the inline
body. This file proves:

  §1 every workflow is under a 400,000-byte ceiling (real files, not the
     reconstruction) -- 112,000 bytes of margin under GitHub's limit;
  §2 render-ops still offers every input, every action choice, the same
     env (inputs reach the script only through env, never interpolated into
     it) and the same job/step shape as the parsed original;
  §3 every confirm=DO action still opens with `need_confirm`, the guard
     itself is unchanged, and no need_confirm call was lost;
  §4 the wrapper runs exactly its versioned script, with the original shell
     flags, after removing the checkout -- and command-verify's entry-run
     steps keep their per-step `if:` guards and default shell;
  §5 the move was lossless: the workflow as it stood before the move equals,
     once parsed, the wrapper with its scripts re-inlined (checked from git
     history at the commit that introduced the scripts, so it stays true
     after later intentional edits to the scripts).
"""
from __future__ import annotations

import json
import os
import re
import subprocess

import pytest
import yaml

from tests import workflow_source as ws

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "workflow_contract_8e62749.json")
ORIGINAL = json.load(open(FIXTURE, encoding="utf-8"))
CEILING = 400_000
GITHUB_LIMIT = 512_000


def _doc(name):
    with open(ws.workflow_path(name), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _on(doc):
    return doc.get("on", doc.get(True))


# ── §1 size ──────────────────────────────────────────────────────────

def test_every_workflow_is_under_the_safe_ceiling():
    sizes = {n: os.path.getsize(ws.workflow_path(n))
             for n in os.listdir(ws.WORKFLOWS) if n.endswith((".yml", ".yaml"))}
    assert len(sizes) > 40, "the scan did not find the workflows"
    over = {n: s for n, s in sizes.items() if s >= CEILING}
    assert not over, ("over the %d-byte ceiling (GitHub refuses at %d): %s. "
                      "Move a large run: body into a versioned script the "
                      "way render-ops does (tests/workflow_source.py)."
                      % (CEILING, GITHUB_LIMIT, over))


def test_the_two_files_that_were_near_the_limit_now_have_real_room():
    assert os.path.getsize(ws.workflow_path("render-ops.yml")) < 20_000
    assert os.path.getsize(ws.workflow_path("command-verify.yml")) < 350_000


# ── §2 the render-ops dispatch surface ───────────────────────────────

def test_render_ops_keeps_every_input_and_every_action_choice():
    was = ORIGINAL["workflows"]["render-ops.yml"]["contract"]
    now = ws.contract(_doc("render-ops.yml"))
    was_in = was["on"]["workflow_dispatch"]["inputs"]
    now_in = _on(_doc("render-ops.yml"))["workflow_dispatch"]["inputs"]
    assert set(was_in) <= set(now_in), set(was_in) - set(now_in)
    for k, spec in was_in.items():
        for field in ("required", "type", "default", "description"):
            assert now_in[k].get(field) == spec.get(field), (k, field)
    missing = set(was_in["action"]["options"]) - set(now_in["action"]["options"])
    assert not missing, "action modes removed: %s" % sorted(missing)
    for mode in ("deploy-api-commit", "workers-commit-deploy", "deploys",
                 "api-branch-set", "workers-branch-set", "sql", "env-set"):
        assert mode in now_in["action"]["options"], mode
    assert now["jobs"] == was["jobs"], "job/step shape changed"
    assert set(_on(_doc("render-ops.yml"))) == set(was["on"]), "triggers changed"


def test_inputs_reach_the_script_only_through_env():
    step = next(s for s in _doc("render-ops.yml")["jobs"]["ops"]["steps"]
                if s.get("name") == "Run")
    assert step["shell"] == "bash"
    assert step["env"] == {"ACTION": "${{ inputs.action }}",
                           "SERVICE": "${{ inputs.service }}",
                           "CONFIRM": "${{ inputs.confirm }}",
                           "KEY_SECRET": "${{ secrets.RENDER_API_KEY }}"}
    assert "${{" not in step["run"]
    body = open(os.path.join(ws.REPO, ".github", "render-ops", "ops.sh"),
                encoding="utf-8").read()
    assert "${{" not in body, "an expression inside the script is not evaluated"


# ── §3 the confirm=DO guards ─────────────────────────────────────────

def _ops():
    return open(os.path.join(ws.REPO, ".github", "render-ops", "ops.sh"),
                encoding="utf-8").read()


def _top_level_arm(body, action):
    for m in re.finditer(r"^  ([a-z0-9|-]+)\)\n", body, re.M):
        if action in m.group(1).split("|"):
            end = body.index("\n    ;;\n", m.end())
            return body[m.end():end]
    raise AssertionError("no top-level case arm for %s" % action)


@pytest.mark.parametrize(
    "action", ORIGINAL["workflows"]["render-ops.yml"]["confirm_do_actions"])
def test_every_confirm_do_action_opens_with_need_confirm(action):
    arm = _top_level_arm(_ops(), action)
    first = [ln.strip() for ln in arm.splitlines()
             if ln.strip() and not ln.strip().startswith("#")][0]
    assert first == "need_confirm", (action, first)


def test_the_guard_itself_and_its_call_count_are_unchanged():
    rec = ORIGINAL["workflows"]["render-ops.yml"]
    body = _ops()
    defs = [ln.strip() for ln in body.splitlines()
            if ln.strip().startswith("need_confirm()")]
    assert defs == rec["need_confirm_definition"]
    assert body.count("need_confirm") >= rec["need_confirm_calls"]
    assert len(rec["confirm_do_actions"]) == 11


# ── §4 the wrappers ──────────────────────────────────────────────────

def test_render_ops_runs_exactly_its_versioned_script_with_the_original_flags():
    steps = _doc("render-ops.yml")["jobs"]["ops"]["steps"]
    assert [s.get("name") for s in steps] == [
        "Fetch the versioned step scripts", "Run"]
    fetch, run = steps
    assert fetch["uses"] == "actions/checkout@v4"
    assert fetch["with"] == {"path": ".render-ops-src",
                             "sparse-checkout": ".github/render-ops",
                             "persist-credentials": False}
    assert run["run"].splitlines() == [
        "# versioned step script: .github/render-ops/ops.sh",
        'install -D -m 0644 .render-ops-src/.github/render-ops/ops.sh '
        '"$RUNNER_TEMP/wfsteps/ops.sh"',
        "rm -rf .render-ops-src",
        # `shell: bash` is GitHub's `bash --noprofile --norc -eo pipefail {0}`
        'exec bash --noprofile --norc -eo pipefail "$RUNNER_TEMP/wfsteps/ops.sh"']


def test_command_verify_entry_run_keeps_its_guards_and_default_shell():
    job = _doc("command-verify.yml")["jobs"]["entry-run"]
    assert job["if"] == "inputs.entry_run == 'DO'"
    steps = job["steps"]
    assert steps[0]["name"] == "Fetch the versioned step scripts"
    assert steps[0]["with"]["persist-credentials"] is False
    runs = [s for s in steps[1:] if "run" in s]
    assert len(runs) == 11
    for s in runs:
        assert s["if"] == ("${{ !cancelled() && github.event.inputs.entry_run "
                           "== 'DO' }}"), s["name"]
        assert "shell" not in s                    # GitHub default: bash -e
        lines = s["run"].splitlines()
        rel = lines[0].split("# versioned step script: ", 1)[1]
        assert lines[1:] == ["exec bash -e .command-verify-src/" + rel], lines
        assert rel.startswith(".github/command-verify/entry-run-")


def test_every_versioned_script_exists_and_parses():
    for name in ("render-ops.yml", "command-verify.yml"):
        paths = ws.script_paths(name)
        assert paths, name
        for rel in paths:
            p = os.path.join(ws.REPO, rel)
            assert os.path.isfile(p), rel
            r = subprocess.run(["bash", "-n", p], capture_output=True,
                               text=True)
            assert r.returncode == 0, (rel, r.stderr[:300])


def test_the_effective_source_reinlines_every_script():
    for name in ("render-ops.yml", "command-verify.yml"):
        mono = yaml.safe_load(ws.monolith_text(name))
        assert ws.contract(mono) == ws.contract(_doc(name))
        bodies = [s["run"] for j in mono["jobs"].values()
                  for s in j["steps"] if "run" in s]
        for rel in ws.script_paths(name):
            assert ws._read_repo(rel) in bodies, rel


# ── §5 the move was lossless ─────────────────────────────────────────

def _git(*args):
    return subprocess.run(["git", "-C", ws.REPO] + list(args),
                          capture_output=True, text=True)


def _introducing_commit():
    r = _git("log", "--diff-filter=A", "--format=%H", "--",
             ".github/render-ops/ops.sh")
    if r.returncode != 0:
        pytest.skip("git history is not readable here: %s" % r.stderr[:200])
    shas = r.stdout.split()
    return shas[-1] if shas else None


@pytest.mark.parametrize("name", ["render-ops.yml", "command-verify.yml"])
def test_the_move_was_lossless(name):
    """Parsed equality of the pre-move workflow and the post-move workflow
    with its scripts re-inlined, at the commit that introduced the scripts
    (or, before that commit exists, HEAD against the working tree)."""
    c = _introducing_commit()
    if c is None:                     # the move is not committed yet
        before = _git("show", "HEAD:.github/workflows/" + name)
        assert before.returncode == 0, before.stderr
        after = ws.monolith_text(name)
    else:
        before = _git("show", "%s^:.github/workflows/%s" % (c, name))
        assert before.returncode == 0, before.stderr
        wrap = _git("show", "%s:.github/workflows/%s" % (c, name))
        assert wrap.returncode == 0, wrap.stderr

        def read(rel):
            r = _git("show", "%s:%s" % (c, rel))
            assert r.returncode == 0, (rel, r.stderr)
            return r.stdout
        after = ws.inline_scripts(wrap.stdout, read)
    assert yaml.safe_load(after) == yaml.safe_load(before.stdout)
    if name == "render-ops.yml":
        assert after == before.stdout, "render-ops moved byte for byte"


def test_the_recorded_original_matches_the_fixture():
    rec = ORIGINAL["workflows"]["render-ops.yml"]
    assert rec["bytes"] == 510_399 and len(rec["sha256"]) == 64
    assert ORIGINAL["workflows"]["command-verify.yml"]["bytes"] == 433_890
