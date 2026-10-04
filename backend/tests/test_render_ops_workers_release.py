"""cand21: render-ops can pin the workers and release them by commit id.

Before these two actions nothing in render-ops could set the workers'
branch or deploy them by commit: `deploy` takes the tracked branch's head,
`env-set` redeploys the service, and deploy-api-commit / api-branch-set are
literal to the web service. The workers sat on 47086de from a diverged
auto-deploy branch while the API ran the release by commit id.

The arms are EXECUTED here, in bash, against stubbed Render calls: what is
pinned is what they send and what they refuse, not how they are spelled.
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest

from tests.workflow_source import render_ops_path

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REAL_WF = os.path.join(_ROOT, ".github", "workflows", "render-ops.yml")
# The arms live in .github/render-ops/ops.sh (moved for size headroom); this
# is the effective source -- the wrapper with that script re-inlined.
WF = render_ops_path()
NOTES = os.path.join(_ROOT, ".github", "workflows", "RENDER_OPS_NOTES.md")
SHA = "17156263358f8e32c97240be2858ca1b8ef8508b"


def _arm(name):
    src = open(WF).read()
    m = re.search(r"\n( +)%s\)\n" % re.escape(name), src)
    assert m, name
    close = "\n" + m.group(1) + "  ;;\n"            # the arm's own ;; line
    end = src.index(close, m.end()) + len(close) - 1
    return src[m.start() + 1:end]


_STUBS = r'''
set +e
LOG=$(mktemp)
KEY=k; API=https://api.render.com/v1
svc_id() { case "$1" in sportsassets-workers) echo srv-wk ;; sportsassets-api) echo srv-api ;; esac; }
get() {
  echo "GET $1" >> "$LOG"
  case "$1" in
    /services/srv-wk) printf '{"type":"%s","autoDeploy":"%s","name":"sportsassets-workers","branch":"b"}' "$WK_TYPE" "$WK_AD" ;;
    /services/srv-api) echo '{"type":"web_service","autoDeploy":"no","name":"sportsassets-api","branch":"claude/release-api"}' ;;
    *) echo '[]' ;;
  esac
}
post() { echo "POST $1 $2" >> "$LOG"; echo '{"id":"dep-1","status":"created","commit":{"id":"x"}}' > /tmp/post.json; echo 201; }
curl() { echo "CURL $*" >> "$LOG"; echo '{}' > /tmp/post.json; echo 200; }
need_confirm() { if [ "$CONFIRM" != "DO" ]; then echo "refused: needs confirm=DO"; exit 1; fi; }
'''


def _run(arm_name, *, arg, confirm="DO", wk_type="background_worker", wk_ad="no"):
    script = _STUBS + ('ACTION=%s; ARG=%s; CONFIRM=%s; WK_TYPE=%s; WK_AD=%s\n'
                       'trap \'echo "=== LOG"; cat "$LOG"\' EXIT\n'
                       'case "$ACTION" in\n%s\nesac\n') % (
        arm_name, _q(arg), _q(confirm), wk_type, wk_ad, _arm(arm_name))
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    out, _, log = r.stdout.partition("=== LOG")
    return r.returncode, out, log


def _q(s):
    return "'" + s.replace("'", "'\\''") + "'"


@pytest.mark.parametrize("name", ["workers-branch-set", "workers-commit-deploy"])
def test_both_arms_are_choices_need_confirm_and_parse(name):
    src = open(WF).read()
    assert re.search(r"\n          - %s +# confirm=DO" % name, src), "not a dispatch choice"
    body = _arm(name)
    assert body.split("\n")[1].strip() == "need_confirm"
    r = subprocess.run(["bash", "-n"], input="case x in\n%s\nesac\n" % body,
                       text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    assert "$SERVICE" not in body, "the service must be a literal, never the input"
    assert os.path.getsize(REAL_WF) < 512_000


@pytest.mark.parametrize("name,arg", [("workers-branch-set", "claude/release-api"),
                                      ("workers-commit-deploy", SHA)])
def test_without_confirm_nothing_is_sent(name, arg):
    rc, out, log = _run(name, arg=arg, confirm="")
    assert rc != 0 and "refused" in out
    assert "CURL" not in log and "POST" not in log


def test_branch_set_patches_branch_and_autodeploy_together_on_the_worker_only():
    rc, out, log = _run("workers-branch-set", arg="claude/release-api")
    assert rc == 0, out
    patches = [l for l in log.splitlines() if l.startswith("CURL")]
    assert len(patches) == 1
    p = patches[0]
    assert "-X PATCH" in p and p.rstrip().endswith("/services/srv-wk")
    assert '{"branch":"claude/release-api","autoDeploy":"no"}' in p
    assert "srv-api" not in p


@pytest.mark.parametrize("bad", ["", "a b", "x;rm -rf /", "$(id)", 'q"uote'])
def test_branch_set_refuses_a_name_that_is_not_a_branch(bad):
    rc, out, log = _run("workers-branch-set", arg=bad)
    assert rc != 0 and "CURL" not in log


def test_branch_set_refuses_when_the_name_does_not_resolve_to_the_worker():
    rc, out, log = _run("workers-branch-set", arg="b", wk_type="web_service")
    assert rc != 0 and "CURL" not in log


def test_commit_deploy_posts_one_deploy_by_commit_id_to_the_worker():
    rc, out, log = _run("workers-commit-deploy", arg=SHA)
    assert rc == 0, out
    posts = [l for l in log.splitlines() if l.startswith("POST")]
    assert posts == ['POST /services/srv-wk/deploys '
                     '{"clearCache":"do_not_clear","commitId":"%s"}' % SHA]
    assert "/services/srv-api/deploys" in log, "the api's deploys are read back"


def test_commit_deploy_refuses_while_the_worker_still_auto_deploys():
    """Otherwise the next push to the tracked branch silently replaces the
    commit just deployed, and the exact-SHA lineage is a claim."""
    rc, out, log = _run("workers-commit-deploy", arg=SHA, wk_ad="yes")
    assert rc != 0 and "workers-branch-set first" in out
    assert "POST" not in log


@pytest.mark.parametrize("bad", ["", SHA[:7], SHA.upper(), SHA + "0", "main"])
def test_commit_deploy_refuses_anything_but_a_full_lowercase_sha(bad):
    rc, out, log = _run("workers-commit-deploy", arg=bad)
    assert rc != 0 and "POST" not in log


def test_commit_deploy_refuses_a_non_worker():
    rc, out, log = _run("workers-commit-deploy", arg=SHA, wk_type="web_service")
    assert rc != 0 and "POST" not in log


def test_the_moved_prose_and_the_new_arms_are_in_the_notes():
    notes = open(NOTES).read()
    for label in ("R82", "R83", "R84", "R93", "R94"):
        assert "\n## [%s]\n" % label in notes, label
        assert "[%s]" % label in open(WF).read(), label
    assert "A worker deploy has exactly three triggers" in notes
    assert "workers-commit-deploy (confirm=DO" in notes
