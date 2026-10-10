"""ROLLBACK TARGET PER SERVICE, FROM ITS OWN DEPLOY HISTORY (rc6.3, contract
item 10: release safety).

Production evidence, signed pm-acceptance 38002788631 on release 16d23450
(RC6.2: api and workers on 16d23450, the market plane still on 732cc0c6;
the files are copied byte for byte into tests/fixtures/
pm_38002788631_rollback and checked against the packet's SHA256SUMS):

  * rollback.json named 3d5af039 (RC6.1) as the previous commit of all
    three services and, from that ONE shared target, wrote a ready-to-run
    `market-plane.yml ... deploy-commit 3d5af039` for the plane -- a
    service the release never touched;
  * the plane's OWN Render deploy list shows why that command is wrong:
    3d5af039 went live on the plane at 14:42:25Z, the plane hung on its
    Kalshi client and was put back on 732cc0c6 (live 15:59:44Z), which had
    already run there at 03:26Z -- 3d5af039 is the commit the plane was
    rolled back FROM;
  * scorecard_14's rollback_ready listed previous_by_service plane =
    3d5af039 beside that command (NOT_READY, only for the plane being off
    the release).

These tests hold, on the real files and on synthetic deploy lists:

  * a service NOT on the release has rollback action NONE (it stays on its
    own live commit), the reason is named, no command is written for it,
    and it never contributes its previous commit to the target;
  * a service ON the release goes back to ITS OWN previous live commit;
    a command is never written to a commit the service was not live on,
    nor to one it was rolled back from;
  * NOT_READY stays strict: the plane off the release still makes the
    rollback NOT_READY (no GREEN by relabelling it NONE), and the
    scorecard re-reads each service's own deploy list instead of trusting
    the record.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
FIX = BACKEND / "tests" / "fixtures" / "pm_38002788631_rollback"


def _tool(name):
    spec = importlib.util.spec_from_file_location(
        "rc63rb_" + name, BACKEND / "tools" / ("%s.py" % name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


RB = _tool("rollback_readiness")
SC = _tool("scorecard_14")
EP = _tool("evidence_packet")

API, WORKERS, PLANE = ("sportsassets-api", "sportsassets-workers",
                       "sportsassets-market-plane")
# the real packet's commits
REL = "16d234506a0683acc8577465b1beadf5423dc3f7"          # RC6.2 release
RC61 = "3d5af039d42026df16d9dad6ec0d4587b0141101"         # RC6.1
RC6 = "732cc0c696db614cbf63f83c3f843adec00301d6"          # RC6 (the plane)
# synthetic commits
R = "1" * 40
P_API, P_WORKERS, P_PLANE, OLD, X = ("a" * 40, "b" * 40, "c" * 40,
                                     "d" * 40, "e" * 40)


def _w(d, name, obj):
    (d / name).write_text(json.dumps(obj))


def _real_acc(tmp_path, *, drop=("rollback.json", "scorecard_14.json")):
    """The real packet's files in a fresh directory, without the record and
    the scorecard the old judge wrote (rebuilt here)."""
    acc = tmp_path / "acc"
    shutil.copytree(FIX, acc)
    for name in drop:
        (acc / name).unlink()
    return acc


def _build(acc, sha=REL):
    """The record as pm-acceptance builds it. The release tree's migrations
    for both sides: 16d23450 adds none to 3d5af039 (224 = 224, the packet's
    own migrations section), and this branch's tree is the release's."""
    mig = BACKEND / "migrations"
    rb = RB.build(acc, sha=sha, target_migrations=mig,
                  release_migrations=mig, target_is_ancestor=True)
    _w(acc, "rollback.json", rb)
    return rb


def _unit(acc, release_sha=REL):
    out = SC.score(str(acc), release_sha=release_sha)
    cat = next(c for c in out["categories"]
               if c["category"] == "Deployment infrastructure")
    return next(u for u in cat["units"] if u["unit"] == "rollback_ready")


# ── 1. the real packet ───────────────────────────────────────────────────

def test_the_fixture_files_are_the_signed_packets_own_bytes():
    sums = {}
    for line in (FIX / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(None, 1)
        sums[name.strip()] = digest
    files = sorted(p.name for p in FIX.iterdir() if p.name != "SHA256SUMS")
    assert len(files) == 13
    for name in files:
        assert hashlib.sha256((FIX / name).read_bytes()).hexdigest() == \
            sums[name], name


def test_the_observed_record_carried_the_plane_command():
    """What the old judge wrote: ONE target, a command for every service,
    the plane's included, although the plane was not on the release."""
    rb = json.loads((FIX / "rollback.json").read_text())
    assert rb["sha"] == REL and rb["target_sha"] == RC61
    assert rb["services"][PLANE]["live_commit"] == RC6
    assert "market-plane.yml" in rb["commands"][PLANE]
    assert "arg=%s" % RC61 in rb["commands"][PLANE]
    assert rb["status"] == "NOT_READY"
    assert rb["reasons"] == ["ROLLBACK_SERVICE_NOT_ON_THE_RELEASE:%s" % PLANE]
    sc = json.loads((FIX / "scorecard_14.json").read_text())
    unit = next(u for c in sc["categories"] for u in c["units"]
                if u["unit"] == "rollback_ready")
    assert unit["passed"] is False
    assert unit["detail"]["previous_by_service"][PLANE] == RC61
    assert "arg=%s" % RC61 in unit["detail"]["commands"][PLANE]


def test_each_services_own_history_and_the_plane_was_rolled_back_from_rc61(
        tmp_path):
    """Each service's previous live commit comes from ITS OWN deploy list.
    The plane's names 3d5af039 too -- the commit it was rolled back FROM
    (732cc0c6 ran before it and after it)."""
    acc = _real_acc(tmp_path)
    for svc in (API, WORKERS):
        s = RB.service(acc, svc)
        assert s["live_commit"] == REL and s["previous_commit"] == RC61
        assert s["previous_rolled_back_from"] is False
        assert s["reasons"] == []
    p = RB.service(acc, PLANE)
    assert p["live_commit"] == RC6 and p["live_deploy_id"] == \
        "dep-db4gu7jbc2fs73boqcu0"
    assert p["previous_commit"] == RC61
    assert p["previous_deploy_id"] == "dep-db4fq1942hec73b5dedg"
    assert p["previous_rolled_back_from"] is True
    # the services' render_lookup files name the services these lists are
    for svc in (API, WORKERS, PLANE):
        look = json.loads((acc / ("render_lookup_%s.json" % svc)).read_text())
        assert [x["service"]["name"] for x in look] == [svc]


def test_the_real_packet_rebuilt_writes_no_command_for_the_plane(tmp_path):
    acc = _real_acc(tmp_path)
    rb = _build(acc)
    # strict: still NOT_READY, for exactly the reason it was before
    assert rb["status"] == RB.NOT_READY
    assert rb["reasons"] == ["ROLLBACK_SERVICE_NOT_ON_THE_RELEASE:%s" % PLANE]
    # the target comes from the services ON the release: their own 3d5af039
    assert rb["target_sha"] == RC61
    plane = rb["services"][PLANE]
    assert plane["on_release"] is False
    assert plane["action"] == RB.STAY == "NONE"
    assert plane["rollback_commit"] == RC6          # it stays where it is
    assert plane["action_reason"] == RB.R_SERVICE_NOT_ON_RELEASE
    assert rb["commands"][PLANE] is None
    assert not any("market-plane.yml" in (c or "")
                   for c in rb["commands"].values())
    for svc, action in ((API, "deploy-api-commit"),
                        (WORKERS, "workers-commit-deploy")):
        s = rb["services"][svc]
        assert s["on_release"] is True and s["action"] == RB.DEPLOY_PREVIOUS
        assert s["rollback_commit"] == s["previous_commit"] == RC61
        assert "action=%s" % action in rb["commands"][svc]
        assert "-f arg=%s " % RC61 in rb["commands"][svc]
    # every other field of the observed record is unchanged
    old = json.loads((FIX / "rollback.json").read_text())
    for k in ("sha", "target_sha", "target_is_ancestor_of_release",
              "target_gates", "procedure", "deploys_nothing", "version"):
        assert rb[k] == old[k], k
    assert rb["commands"][API] == old["commands"][API]
    assert rb["commands"][WORKERS] == old["commands"][WORKERS]


def test_the_scorecard_on_the_rebuilt_record_stays_not_ready(tmp_path):
    acc = _real_acc(tmp_path)
    _build(acc)
    u = _unit(acc)
    assert u["passed"] is False and u["class"] == "FAIL"
    d = u["detail"]
    assert d["status"] == "NOT_READY"
    assert d["reasons"] == ["ROLLBACK_SERVICE_NOT_ON_THE_RELEASE:%s" % PLANE]
    assert d["schema"] == "COMPATIBLE_IDENTICAL_SCHEMA"
    assert d["rollback_by_service"] == {
        API: {"action": "DEPLOY_PREVIOUS", "commit": RC61, "reason": None},
        WORKERS: {"action": "DEPLOY_PREVIOUS", "commit": RC61,
                  "reason": None},
        PLANE: {"action": "NONE", "commit": RC6,
                "reason": "ROLLBACK_SERVICE_NOT_ON_THE_RELEASE"}}
    assert d["commands"][PLANE] is None


def test_the_new_scorecard_names_the_observed_records_plane_command(
        tmp_path):
    """The old judge's own record, re-scored: the plane command is a named
    reason of its own, beside the plane being off the release."""
    acc = _real_acc(tmp_path, drop=("scorecard_14.json",))
    u = _unit(acc)
    assert u["passed"] is False
    assert u["detail"]["reasons"] == [
        "ROLLBACK_SERVICE_NOT_ON_THE_RELEASE:%s" % PLANE,
        "ROLLBACK_COMMAND_FOR_A_SERVICE_NOT_ON_THE_RELEASE:%s" % PLANE]


def test_the_target_step_reads_the_release_from_lineage(tmp_path):
    """pm-acceptance runs `target acc` (no --sha) after the lineage step:
    the release is lineage.json's tested sha, so the plane (off the
    release) does not take part and the target is 3d5af039."""
    acc = _real_acc(tmp_path)
    assert json.loads((acc / "lineage.json").read_text())["sha"] == REL
    for argv in (["target", str(acc)], ["target", str(acc), "--sha", REL]):
        r = subprocess.run([sys.executable, "-I",
                            str(BACKEND / "tools" / "rollback_readiness.py")]
                           + argv, capture_output=True, text=True,
                           timeout=60)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == RC61
    # the release unknown: every service counts, and the plane's previous
    # commit was rolled back from -- no target, named
    (acc / "lineage.json").unlink()
    tgt, svcs, reasons = RB.target(acc)
    assert tgt is None
    assert svcs[PLANE]["action"] == RB.NO_TARGET
    assert "%s:%s:%s" % (RB.R_PREVIOUS_ROLLED_BACK, PLANE, RC61[:12]) in \
        reasons


def test_the_packet_carries_the_per_service_plan(tmp_path):
    acc = _real_acc(tmp_path)
    _build(acc)
    p = EP.build(acc, now=1.0)
    assert p["rollback"]["commands"]["value"][PLANE] is None
    svcs = p["rollback"]["services"]["value"]
    assert svcs[PLANE]["action"] == "NONE"
    assert svcs[PLANE]["rollback_commit"] == RC6
    assert svcs[API]["action"] == svcs[WORKERS]["action"] == "DEPLOY_PREVIOUS"
    assert p["rollback"]["status"]["value"] == "NOT_READY"


# ── 2. synthetic deploy lists ────────────────────────────────────────────

def _dep(i, commit, status="deactivated", trigger="api"):
    return {"deploy": {"id": "dep-%s" % i, "status": status,
                       "trigger": trigger, "commit": {"id": commit},
                       "finishedAt": "t%s" % i}}


def _history(live, *older, live_trigger="api"):
    """Newest first: the live deploy, then each older commit deactivated
    (with a failed build in between, which is never a previous release)."""
    rows = [_dep(0, live, "live", live_trigger),
            _dep("f", OLD, "build_failed")]
    rows += [_dep(n + 1, c) for n, c in enumerate(older)]
    return rows


def _gates(sha):
    return {"runs": {k: {"id": 10 + i, "status": "completed",
                         "conclusion": "success", "head_sha": sha}
                     for i, k in enumerate(SC.GATES)}}


def _synthetic(tmp_path, histories, *, target_gates=None):
    acc = tmp_path / "acc"
    acc.mkdir(parents=True)
    _w(acc, "lineage.json", {"sha": R})
    _w(acc, "render.json", {s: {"live_commit": h[0]["deploy"]["commit"]["id"]}
                            for s, h in histories.items()})
    for s, h in histories.items():
        _w(acc, "deploys_%s.json" % s, h)
    if target_gates:
        _w(acc, "gates_rollback_target.json", _gates(target_gates))
    mig = tmp_path / "mig"
    mig.mkdir()
    (mig / "001_init.sql").write_text("select 1;\n")
    rb = RB.build(acc, sha=R, target_migrations=mig, release_migrations=mig,
                  target_is_ancestor=True)
    _w(acc, "rollback.json", rb)
    return acc, rb


def test_plane_not_on_release_stays_and_never_decides_the_target(tmp_path):
    """The plane's own previous commit differs from the API's: before, that
    made the target unknown (ROLLBACK_TARGET_DIFFERS_BY_SERVICE); the plane
    is not on the release, so it stays on its own commit and the target is
    what the services on the release ran."""
    acc, rb = _synthetic(tmp_path, {
        API: _history(R, P_API), WORKERS: _history(R, P_API),
        PLANE: _history(X, P_PLANE)}, target_gates=P_API)
    assert rb["target_sha"] == P_API
    assert rb["reasons"] == ["ROLLBACK_SERVICE_NOT_ON_THE_RELEASE:%s" % PLANE]
    assert rb["status"] == RB.NOT_READY
    assert rb["services"][PLANE]["action"] == RB.STAY
    assert rb["services"][PLANE]["rollback_commit"] == X
    assert rb["commands"][PLANE] is None
    assert P_PLANE not in json.dumps(rb["commands"])
    u = _unit(acc, R)
    assert u["passed"] is False
    assert u["detail"]["reasons"] == [
        "ROLLBACK_SERVICE_NOT_ON_THE_RELEASE:%s" % PLANE]
    assert u["detail"]["rollback_by_service"][PLANE] == {
        "action": "NONE", "commit": X,
        "reason": "ROLLBACK_SERVICE_NOT_ON_THE_RELEASE"}


def test_services_on_release_go_back_to_their_own_previous_commit(tmp_path):
    """Three services on the release, the same previous commit: READY, and
    each command is that service's own previous live commit."""
    acc, rb = _synthetic(tmp_path, {
        s: _history(R, P_API, OLD) for s in (API, WORKERS, PLANE)},
        target_gates=P_API)
    assert rb["status"] == RB.READY and rb["reasons"] == []
    for s in (API, WORKERS, PLANE):
        assert rb["services"][s]["action"] == RB.DEPLOY_PREVIOUS
        assert rb["services"][s]["rollback_commit"] == P_API
        assert "-f arg=%s " % P_API in rb["commands"][s]
    u = _unit(acc, R)
    assert u["passed"] is True, u["detail"]
    assert all(v == {"action": "DEPLOY_PREVIOUS", "commit": P_API,
                     "reason": None}
               for v in u["detail"]["rollback_by_service"].values())


def test_differing_own_previous_commits_never_borrow_anothers(tmp_path):
    """On the release, the API ran P_API before and the workers P_WORKERS:
    each command names its OWN service's previous commit, never the
    other's; one release target is unknown, so NOT_READY (the job proves
    gates and schema for one target only)."""
    acc, rb = _synthetic(tmp_path, {
        API: _history(R, P_API), WORKERS: _history(R, P_WORKERS),
        PLANE: _history(R, P_API)}, target_gates=P_API)
    assert rb["target_sha"] is None and rb["status"] == RB.NOT_READY
    assert any(r.startswith(RB.R_TARGET_DIFFERS_BY_SERVICE)
               for r in rb["reasons"])
    assert "arg=%s" % P_API in rb["commands"][API]
    assert "arg=%s" % P_WORKERS in rb["commands"][WORKERS]
    assert P_API not in rb["commands"][WORKERS]
    assert P_WORKERS not in rb["commands"][API]
    u = _unit(acc, R)
    assert u["passed"] is False
    assert any(r.startswith("ROLLBACK_TARGET_UNKNOWN:%s" % WORKERS)
               for r in u["detail"]["reasons"])


def test_a_commit_rolled_back_from_is_never_a_target_or_a_command(tmp_path):
    """The API on the release ran P_API, was put back on R, and R is live
    again: P_API is the commit it was rolled back FROM."""
    acc, rb = _synthetic(tmp_path, {
        API: _history(R, P_API, R, OLD), WORKERS: _history(R, P_API),
        PLANE: _history(R, P_API)}, target_gates=P_API)
    api = rb["services"][API]
    assert api["previous_commit"] == P_API
    assert api["previous_rolled_back_from"] is True
    assert api["action"] == RB.NO_TARGET and api["rollback_commit"] is None
    assert api["action_reason"] == RB.R_PREVIOUS_ROLLED_BACK
    assert rb["commands"][API] is None
    assert rb["target_sha"] is None and rb["status"] == RB.NOT_READY
    assert "%s:%s:%s" % (RB.R_PREVIOUS_ROLLED_BACK, API, P_API[:12]) in \
        rb["reasons"]
    u = _unit(acc, R)
    assert u["passed"] is False
    assert "%s:%s:%s" % (SC.R_ROLLBACK_PREVIOUS_ROLLED_BACK, API,
                         P_API[:12]) in u["detail"]["reasons"]
    assert u["detail"]["rollback_by_service"][API]["action"] == "NO_TARGET"


def test_a_render_rollback_trigger_marks_the_previous_as_rolled_back_from(
        tmp_path):
    _, rb = _synthetic(tmp_path, {
        API: _history(R, P_API, live_trigger="rollback"),
        WORKERS: _history(R, P_API), PLANE: _history(R, P_API)},
        target_gates=P_API)
    assert rb["services"][API]["previous_rolled_back_from"] is True
    assert rb["services"][API]["action"] == RB.NO_TARGET
    assert rb["commands"][API] is None and rb["status"] == RB.NOT_READY


def test_no_previous_live_deploy_is_no_target_and_no_command(tmp_path):
    _, rb = _synthetic(tmp_path, {
        API: [_dep(0, R, "live")], WORKERS: _history(R, P_API),
        PLANE: _history(R, P_API)}, target_gates=P_API)
    assert rb["services"][API]["action"] == RB.NO_TARGET
    assert rb["services"][API]["action_reason"] == RB.R_NO_PREVIOUS_DEPLOY
    assert rb["commands"][API] is None
    assert rb["target_sha"] is None and rb["status"] == RB.NOT_READY


# ── 3. the scorecard re-reads each service's own deploy list ─────────────

def _ready(tmp_path):
    return _synthetic(tmp_path, {
        s: _history(R, P_API, OLD) for s in (API, WORKERS, PLANE)},
        target_gates=P_API)


def test_a_command_to_a_commit_the_service_never_ran_is_named(tmp_path):
    acc, rb = _ready(tmp_path)
    rb["commands"][API] = rb["commands"][API].replace(P_API, X)
    rb["status"], rb["reasons"] = RB.READY, []
    _w(acc, "rollback.json", rb)
    u = _unit(acc, R)
    assert u["passed"] is False
    assert "%s:%s:%s" % (SC.R_ROLLBACK_COMMAND_NOT_OWN_PREVIOUS, API,
                         X[:12]) in u["detail"]["reasons"]


def test_a_record_whose_previous_commit_disagrees_with_the_list_is_caught(
        tmp_path):
    """The record (and its command) say P_API; the API's own deploy list
    says it ran OLD before: the command is not its own previous commit."""
    acc, _ = _ready(tmp_path)
    _w(acc, "deploys_%s.json" % API, _history(R, OLD))
    u = _unit(acc, R)
    assert u["passed"] is False
    assert "%s:%s:%s" % (SC.R_ROLLBACK_COMMAND_NOT_OWN_PREVIOUS, API,
                         P_API[:12]) in u["detail"]["reasons"]


def test_an_unreadable_deploy_list_is_never_ready(tmp_path):
    acc, _ = _ready(tmp_path)
    (acc / ("deploys_%s.json" % WORKERS)).unlink()
    u = _unit(acc, R)
    assert u["passed"] is False
    assert "%s:%s:DEPLOY_HISTORY_UNREADABLE" % (
        SC.R_ROLLBACK_COMMAND_NOT_OWN_PREVIOUS, WORKERS) in \
        u["detail"]["reasons"]


def test_a_command_for_a_service_off_the_release_is_named(tmp_path):
    acc, rb = _synthetic(tmp_path, {
        API: _history(R, P_API), WORKERS: _history(R, P_API),
        PLANE: _history(X, P_API)}, target_gates=P_API)
    assert rb["commands"][PLANE] is None
    rb["commands"][PLANE] = RB.COMMANDS[PLANE] % P_API      # forged
    _w(acc, "rollback.json", rb)
    u = _unit(acc, R)
    assert u["passed"] is False
    assert u["detail"]["reasons"] == [
        "ROLLBACK_SERVICE_NOT_ON_THE_RELEASE:%s" % PLANE,
        "ROLLBACK_COMMAND_FOR_A_SERVICE_NOT_ON_THE_RELEASE:%s" % PLANE]


def test_every_new_code_is_classified():
    from sportsassets import refusal_taxonomy as RT
    from sportsassets import refusal_taxonomy_table as TT
    for code in (RB.R_PREVIOUS_ROLLED_BACK, SC.R_ROLLBACK_PREVIOUS_ROLLED_BACK,
                 SC.R_ROLLBACK_COMMAND_OFF_RELEASE,
                 SC.R_ROLLBACK_COMMAND_NOT_OWN_PREVIOUS):
        assert RT.normalize(code) in TT.NOT_REFUSAL, code
