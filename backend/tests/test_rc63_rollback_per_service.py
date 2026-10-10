"""EACH SERVICE ROLLS BACK TO ITS OWN PREVIOUS LIVE COMMIT (rc6.3
rollback-fix).

Production evidence: the signed approved-judge pm-acceptance packet
38002788631 (release 16d23450, RC6.2). api and workers were live on
16d23450; the market plane was live on 732cc0c6 -- it had been moved back
there at ~15:59Z on 2026-10-09 after RC6.1 3d5af039's Kalshi client hung it.
The plane's own deploy list names 3d5af039 as the deploy before 732cc0c6, so
all three services "agreed" on 3d5af039, and rollback_readiness wrote ONE
command per service from that single release-wide target -- including

    gh workflow run market-plane.yml --ref claude/release-api
        -f action=deploy-commit -f arg=3d5af039... -f confirm=DO

and the scorecard's rollback_ready unit passed it on verbatim (with
previous_by_service = 3d5af039 for all three). Running it would have
redeployed the hang onto a service that was never on the release.

These tests hold, on the packet's own files (byte-identical, checked against
the packet's SHA256SUMS) and on synthetic histories:

  * a service not on the release gets action NONE (stays on its current
    commit, named ROLLBACK_SERVICE_NOT_ON_THE_RELEASE) and no command, in the
    record AND in what the judge passes on -- even from the old record that
    wrote one; the rollback stays NOT_READY (no GREEN by relabelling);
  * every service on the release goes back to ITS OWN previous live commit,
    also when that differs from the other services' (then there is no single
    target, named, and NOT_READY);
  * a commit the service was not live on before is never in a command, and a
    command never rides to another service;
  * an unreadable or unusable history is refused by name, with no command --
    also ONE deploy row of the wrong shape (a commit that is a string, a list
    or a number; a row, deploy or status that is not what Render writes)
    where it decides what is live or what ran before: never a crash (the
    judge's raise would drop all 14 scorecard categories from the packet,
    the tool's the rollback record), and never read past to an older
    commit. A bad row older than the previous live commit is not counted;
  * a deploy status is "never served" only when it is one Render documents
    as such (an allowlist, the same in the tool and the judge): any other
    status -- `Deactivated`, `succeeded`, `Live`, empty -- newer than the
    live deploy or between it and the previous live commit is refused by
    name (ROLLBACK_DEPLOY_STATUS_UNKNOWN), never skipped to an older
    commit; and a served (`deactivated`) deploy newer than the live one is
    refused by name (ROLLBACK_SERVED_DEPLOY_NEWER_THAN_THE_LIVE_DEPLOY),
    never ignored. Both in the tool and in the judge, on synthetic
    histories and on the signed packet's own files.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import pathlib
import re

import pytest

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

API, WORKERS, PLANE = ("sportsassets-api", "sportsassets-workers",
                       "sportsassets-market-plane")
SERVICES = (API, WORKERS, PLANE)

# ── the packet's facts ───────────────────────────────────────────────────
REL_16D = "16d234506a0683acc8577465b1beadf5423dc3f7"      # RC6.2 release
HANG_3D5 = "3d5af039d42026df16d9dad6ec0d4587b0141101"     # RC6.1, hung plane
PLANE_732 = "732cc0c696db614cbf63f83c3f843adec00301d6"    # plane's live
#: the packet's own SHA256SUMS lines for the files copied here unchanged
PACKET_SHA256 = {
    "rollback.json":
        "ad13bfdfc3b3ed1d73912eb01432d5391a2d6a8c7b3a50708bf775d43653f1f5",
    "render.json":
        "33de415786a8300ad62a2da3427282350d0e085b6b967bebd5fc39d1dc75625a",
    "gates_rollback_target.json":
        "c04228b1a2be2ea0c50ed195910519f974596104d9183fa9a51ac3294f89f2a8",
    "deploys_sportsassets-api.json":
        "45a0247afc399090d19a1be318be9e694e32b3f825da7daddadf9e8ea4ccd206",
    "deploys_sportsassets-workers.json":
        "6a9336861ef538ab1a440b39594bd095b201f6377ce4dfb807d5534ff0b9cb21",
    "deploys_sportsassets-market-plane.json":
        "105702b9bf02d4755697bc2b556830929e08e03499ed3619b61a637cb1fd6b55",
}


#: what a credential would look like in these files (they carry only
#: commit messages, deploy ids, commits and GitHub run links; the messages'
#: words "admin-token" / "secret-URL" are prose, not values)
_CREDENTIAL = re.compile(
    r"(rnd_[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_|"
    r"BEGIN [A-Z ]*PRIVATE KEY|://[^/\s\"]+:[^/\s\"]+@|"
    r"(?i:(api[_-]?key|token|secret|password)[\"']?\s*[:=]\s*[\"'][^\"']{8,}))")


def _packet_acc(tmp_path, *, keep_record=True):
    """The packet's own files in a fresh acc dir, each checked byte for
    byte against the signed packet's SHA256SUMS, and for credential-shaped
    values, first."""
    acc = tmp_path / "acc"
    acc.mkdir()
    for name, digest in PACKET_SHA256.items():
        data = (FIX / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, name
        assert not _CREDENTIAL.search(data.decode()), name
        if name == "rollback.json" and not keep_record:
            continue
        (acc / name).write_bytes(data)
    return acc


def _unit(acc, release_sha):
    out = SC.score(str(acc), release_sha=release_sha)
    cat = next(c for c in out["categories"]
               if c["category"] == "Deployment infrastructure")
    return next(u for u in cat["units"] if u["unit"] == "rollback_ready")


def _no_plane_command(cmds):
    assert cmds.get(PLANE) is None, cmds
    for c in cmds.values():
        assert "market-plane.yml" not in str(c or ""), cmds
        assert "sportsassets-market-plane" not in str(c or ""), cmds


# ── 1. the packet: the plane stays on 732cc0c6, no market-plane command ──

def test_the_packet_record_keeps_the_plane_on_732cc0c6_with_no_command(
        tmp_path):
    """rollback_readiness over the packet's own deploy lists: api and
    workers go back to THEIR previous live commit (3d5af039, which each ran
    14:42Z-21:07Z), the plane is NONE -- stay on 732cc0c6 -- and no command
    deploys anything onto the plane."""
    acc = _packet_acc(tmp_path, keep_record=False)
    rb = RB.build(acc, sha=REL_16D, target_is_ancestor=True)
    _no_plane_command(rb["commands"])          # the defect itself, first
    plan = rb["rollback_by_service"]
    assert plan[PLANE] == {"action": RB.NONE, "stay_on": PLANE_732,
                           "reason": RB.R_SERVICE_NOT_ON_RELEASE}
    for s in (API, WORKERS):
        assert plan[s] == {"action": RB.DEPLOY_PREVIOUS, "from": REL_16D,
                           "to": HANG_3D5}
        assert rb["commands"][s] == RB.COMMANDS[s] % HANG_3D5
    _no_plane_command(rb["commands"])
    # strict: the plane off the release keeps the rollback NOT_READY
    assert rb["status"] == RB.NOT_READY
    assert "%s:%s" % (RB.R_SERVICE_NOT_ON_RELEASE, PLANE) in rb["reasons"]
    # the target is what the services ON the release go back to
    assert rb["target_sha"] == HANG_3D5
    assert RB.main(["target", str(acc), "--sha", REL_16D]) == 0


def test_the_judge_passes_no_market_plane_command_from_the_signed_record(
        tmp_path):
    """The scorecard over the packet AS SIGNED (its rollback.json still
    carries the market-plane command for 3d5af039): the unit reports the
    plane NONE / stay on 732cc0c6, passes on no command for it and names
    the record's command; api and workers keep their own."""
    acc = _packet_acc(tmp_path)
    signed = json.loads((acc / "rollback.json").read_text())
    assert HANG_3D5 in signed["commands"][PLANE]     # the defect, as signed
    u = _unit(acc, REL_16D)
    d = u["detail"]
    _no_plane_command(d["commands"])           # the defect itself, first
    assert u["passed"] is False and d["status"] == "NOT_READY"
    assert d["rollback_by_service"][PLANE] == {
        "action": "NONE", "stay_on": PLANE_732,
        "reason": SC.R_ROLLBACK_SERVICE_NOT_ON_RELEASE}
    assert "%s:%s" % (SC.R_ROLLBACK_COMMAND_OFF_RELEASE, PLANE) in \
        d["reasons"]
    assert "%s:%s" % (SC.R_ROLLBACK_SERVICE_NOT_ON_RELEASE, PLANE) in \
        d["reasons"]
    for s in (API, WORKERS):
        assert d["rollback_by_service"][s]["to"] == HANG_3D5
        assert d["commands"][s] == signed["commands"][s]


def test_the_rebuilt_packet_record_scores_with_the_plane_staying(tmp_path):
    """The fixed tool's record over the same packet, judged: the plane is
    NONE with no command and no command was written for it; still NOT_READY
    for the plane being off the release (never GREEN by relabelling)."""
    acc = _packet_acc(tmp_path, keep_record=False)
    (acc / "rollback.json").write_text(json.dumps(
        RB.build(acc, sha=REL_16D, target_is_ancestor=True)))
    u = _unit(acc, REL_16D)
    d = u["detail"]
    _no_plane_command(d["commands"])           # the defect itself, first
    assert u["passed"] is False
    assert d["rollback_by_service"][PLANE]["action"] == "NONE"
    assert d["rollback_by_service"][PLANE]["stay_on"] == PLANE_732
    assert "%s:%s" % (SC.R_ROLLBACK_SERVICE_NOT_ON_RELEASE, PLANE) in \
        d["reasons"]
    assert not any(r.startswith(SC.R_ROLLBACK_COMMAND_OFF_RELEASE)
                   for r in d["reasons"])
    assert {d["commands"][s] for s in (API, WORKERS)} == {
        RB.COMMANDS[API] % HANG_3D5, RB.COMMANDS[WORKERS] % HANG_3D5}


# ── 2. synthetic histories ───────────────────────────────────────────────

REL = "1" * 40
PREV = "2" * 40
OTHER = "3" * 40
HANG = "4" * 40
OLD = "5" * 40


def _dep(i, status, commit):
    return {"deploy": {"id": "dep-%s" % i, "status": status,
                       "commit": {"id": commit}, "finishedAt": "t%s" % i}}


def _hist(live, *before):
    """A Render deploy list, newest first: the live deploy, a failed build
    and a redeploy of the live commit (neither a previous release), then
    `before` as (status, commit) newest first."""
    rows = [_dep(9, "live", live), _dep(8, "build_failed", OTHER),
            _dep(7, "deactivated", live)]
    rows += [_dep(6 - i, st, c) for i, (st, c) in enumerate(before)]
    return rows


def _acc(tmp_path, hists, *, render=None):
    acc = tmp_path / "acc"
    acc.mkdir()
    for s, h in hists.items():
        if h is not None:
            (acc / ("deploys_%s.json" % s)).write_text(
                h if isinstance(h, str) else json.dumps(h))
    (acc / "render.json").write_text(json.dumps({
        s: {"live_commit": (render or {}).get(s, REL)} for s in SERVICES}))
    return acc


def _gates(acc, sha):
    (acc / "gates_rollback_target.json").write_text(json.dumps({"runs": {
        k: {"status": "completed", "conclusion": "success", "head_sha": sha}
        for k in RB.GATES}}))


def _mig(tmp_path):
    d = tmp_path / "mig"
    d.mkdir()
    (d / "001_init.sql").write_text("create table a();\n")
    return d


def _record(acc, tmp_path):
    rb = RB.build(acc, sha=REL, target_migrations=_mig(tmp_path),
                  release_migrations=tmp_path / "mig",
                  target_is_ancestor=True)
    (acc / "rollback.json").write_text(json.dumps(rb))
    return rb


def test_all_services_on_the_release_each_go_to_their_own_previous(
        tmp_path):
    """All three on the release, each history its own: each goes back to
    ITS previous live commit. The plane's history holds an older deploy it
    was moved back FROM (PREV -> HANG -> PREV -> REL): it goes to PREV,
    never HANG. With the target's gates, ancestry and schema proven this is
    READY, and the judge passes on exactly the three own commands."""
    acc = _acc(tmp_path, {
        API: _hist(REL, ("deactivated", PREV), ("deactivated", OLD)),
        WORKERS: _hist(REL, ("update_failed", HANG), ("deactivated", PREV)),
        PLANE: _hist(REL, ("deactivated", PREV), ("deactivated", HANG),
                     ("deactivated", PREV))})
    _gates(acc, PREV)
    rb = _record(acc, tmp_path)
    for s in SERVICES:
        assert rb["rollback_by_service"][s] == {
            "action": RB.DEPLOY_PREVIOUS, "from": REL, "to": PREV}, s
        assert rb["commands"][s] == RB.COMMANDS[s] % PREV
        assert HANG not in rb["commands"][s]
    assert rb["target_sha"] == PREV and rb["status"] == RB.READY, \
        rb["reasons"]
    u = _unit(acc, REL)
    assert u["passed"] is True, u["detail"]
    assert u["detail"]["commands"] == {s: RB.COMMANDS[s] % PREV
                                       for s in SERVICES}
    assert {p["action"] for p in u["detail"]["rollback_by_service"]
            .values()} == {"DEPLOY_PREVIOUS"}


def test_a_service_whose_previous_differs_goes_to_its_own(tmp_path):
    """api and workers ran PREV before the release, the plane ran OTHER:
    each command names its own service's previous live commit, never the
    others'; there is no single target (named) and it is NOT_READY."""
    acc = _acc(tmp_path, {
        API: _hist(REL, ("deactivated", PREV)),
        WORKERS: _hist(REL, ("deactivated", PREV)),
        PLANE: _hist(REL, ("deactivated", OTHER), ("deactivated", PREV))})
    _gates(acc, PREV)
    rb = _record(acc, tmp_path)
    assert rb["commands"][PLANE] == RB.COMMANDS[PLANE] % OTHER
    for s in (API, WORKERS):
        assert rb["commands"][s] == RB.COMMANDS[s] % PREV
        assert OTHER not in rb["commands"][s]
        assert rb["rollback_by_service"][s]["to"] == PREV
    assert PREV not in rb["commands"][PLANE]
    assert rb["rollback_by_service"][PLANE]["to"] == OTHER
    assert rb["target_sha"] is None and rb["status"] == RB.NOT_READY
    assert any(r.startswith(RB.R_TARGET_DIFFERS_BY_SERVICE)
               for r in rb["reasons"])
    u = _unit(acc, REL)
    assert u["passed"] is False
    assert u["detail"]["commands"][PLANE] == RB.COMMANDS[PLANE] % OTHER
    assert "%s:%s:%s" % (SC.R_ROLLBACK_TARGET_UNKNOWN, PLANE, OTHER) in \
        u["detail"]["reasons"]


def _row(i, status, commit):
    """A deploy row whose `commit` is written exactly as given (Render
    writes an object {"id": sha}; the shapes below are what the judge and
    the tool must refuse by name, never crash on)."""
    return {"deploy": {"id": "dep-%s" % i, "status": status,
                       "commit": commit, "finishedAt": "t%s" % i}}


def _put(rows, at, row, *, replace=False):
    rows = copy.deepcopy(rows)
    if replace:
        rows[at] = row
    else:
        rows.insert(at, row)
    return rows


#: the plane's history [live REL, build_failed OTHER, deactivated REL (a
#: redeploy), deactivated PREV] with ONE row it cannot read where that row
#: decides what is live or what ran before: each is refused by name
#: (ROLLBACK_DEPLOY_HISTORY_UNREADABLE) with no command -- never a crash,
#: and never skipped over to PREV (an older commit than the one the service
#: may last have run)
_GOOD = _hist(REL, ("deactivated", PREV))
_BAD_ROWS = [
    ("live_commit_is_a_string",
     _put(_GOOD, 0, _row(9, "live", REL), replace=True)),
    ("older_live_commit_is_a_list",
     _put(_GOOD, 3, _row(5, "deactivated", [HANG]))),
    ("older_live_commit_is_an_int",
     _put(_GOOD, 3, _row(5, "deactivated", 7))),
    ("redeploy_commit_is_a_string",
     _put(_GOOD, 2, _row(7, "deactivated", REL), replace=True)),
    ("older_row_is_not_an_object", _put(_GOOD, 3, HANG)),
    ("older_deploy_is_not_an_object", _put(_GOOD, 3, {"deploy": [HANG]})),
    ("older_status_is_not_a_string",
     _put(_GOOD, 3, _row(5, ["deactivated"], {"id": HANG}))),
    ("newer_row_is_not_readable", _put(_GOOD, 0, {"deploy": "live"})),
]


@pytest.mark.parametrize("hist,render,reason", [
    (None, None, RB.R_DEPLOYS_UNREADABLE),                        # absent
    ("{not json", None, RB.R_DEPLOYS_UNREADABLE),                 # garbage
    ({"deploys": []}, None, RB.R_DEPLOYS_UNREADABLE),             # not a list
    ([_dep(2, "live", REL), _dep(1, "live", PREV)], None,
     RB.R_NO_SINGLE_LIVE_DEPLOY + ":2"),
    ([_dep(2, "deactivated", REL), _dep(1, "deactivated", PREV)], None,
     RB.R_NO_SINGLE_LIVE_DEPLOY + ":0"),
    ([_dep(2, "live", REL), _dep(1, "build_failed", PREV),
      _dep(0, "canceled", OLD)], None, RB.R_NO_PREVIOUS_DEPLOY),
    (_hist(REL, ("deactivated", PREV)), {PLANE: OLD},
     RB.R_LIVE_DIFFERS_FROM_RENDER),
] + [pytest.param(h, None, RB.R_DEPLOYS_UNREADABLE, id=n)
     for n, h in _BAD_ROWS])
def test_an_unusable_history_is_refused_by_name_with_no_command(
        tmp_path, hist, render, reason):
    """The plane's history unreadable, without exactly one live deploy,
    with no previous LIVE commit (a failed or cancelled deploy never
    served), disagreeing with render.json, or with one row of the wrong
    shape where it decides what is live or what ran before (_BAD_ROWS):
    REFUSED by name and no command for it -- never another service's
    commit, never a crash -- while api and workers keep their own;
    NOT_READY. The judge passes on nothing for it."""
    acc = _acc(tmp_path, {API: _hist(REL, ("deactivated", PREV)),
                          WORKERS: _hist(REL, ("deactivated", PREV)),
                          PLANE: hist}, render=render)
    _gates(acc, PREV)
    rb = _record(acc, tmp_path)
    assert rb["commands"][PLANE] is None
    for s in (API, WORKERS):
        assert rb["commands"][s] == RB.COMMANDS[s] % PREV
    assert rb["rollback_by_service"][PLANE] == {"action": RB.REFUSED,
                                                "reason": reason}
    assert "%s:%s" % (reason, PLANE) in rb["reasons"]
    assert rb["status"] == RB.NOT_READY
    u = _unit(acc, REL)
    assert u["passed"] is False and u["detail"]["commands"][PLANE] is None
    assert u["detail"]["rollback_by_service"][PLANE]["action"] == "REFUSED"
    _no_plane_command(u["detail"]["commands"])


def _score(acc, release_sha):
    """score() over the whole packet: it must complete (all 14 categories,
    nothing raised), and its rollback_ready unit is returned."""
    out = SC.score(str(acc), release_sha=release_sha)
    assert len(out["categories"]) == 14
    cat = next(c for c in out["categories"]
               if c["category"] == "Deployment infrastructure")
    return next(u for u in cat["units"] if u["unit"] == "rollback_ready")


@pytest.mark.parametrize("hist", [h for _, h in _BAD_ROWS],
                         ids=[n for n, _ in _BAD_ROWS])
def test_the_judge_refuses_a_bad_deploy_row_by_name_with_the_record_present(
        tmp_path, hist):
    """The judge path on its own, not short-circuited by a missing record:
    rollback.json written while the plane's history read cleanly (so it
    carries a market-plane command for PREV), then the plane's deploy list
    as it now reads, with one row of the wrong shape. score() completes all
    14 categories (pm-acceptance records a scorecard that raises as absent:
    one bad field must not drop them all); rollback_ready is FAIL, not
    READ_UNAVAILABLE; the plane is REFUSED ROLLBACK_DEPLOY_HISTORY_UNREADABLE
    by name and the record's plane command is not passed on; api and
    workers keep their own. The tool, rebuilt over the same files, refuses
    it by name too, with no command."""
    acc = _acc(tmp_path, {s: _GOOD for s in SERVICES})
    _gates(acc, PREV)
    clean = _record(acc, tmp_path)
    assert clean["commands"][PLANE] == RB.COMMANDS[PLANE] % PREV
    (acc / ("deploys_%s.json" % PLANE)).write_text(json.dumps(hist))
    u = _score(acc, REL)
    d = u["detail"]
    assert u["class"] == "FAIL" and u["passed"] is False, u
    assert d["rollback_by_service"][PLANE] == {
        "action": "REFUSED", "reason": SC.R_ROLLBACK_HISTORY_UNREADABLE}
    assert "%s:%s" % (SC.R_ROLLBACK_HISTORY_UNREADABLE, PLANE) in \
        d["reasons"]
    _no_plane_command(d["commands"])
    for s in (API, WORKERS):
        assert d["commands"][s] == RB.COMMANDS[s] % PREV
    rb = RB.build(acc, sha=REL, target_is_ancestor=True)
    assert rb["rollback_by_service"][PLANE] == {
        "action": RB.REFUSED, "reason": RB.R_DEPLOYS_UNREADABLE}
    _no_plane_command(rb["commands"])
    assert rb["status"] == RB.NOT_READY


@pytest.mark.parametrize("at,commit", [
    (0, REL_16D), (1, ["x"]), (1, 7)],
    ids=["live_commit_is_a_string", "previous_commit_is_a_list",
         "previous_commit_is_an_int"])
def test_a_bad_row_in_the_signed_packet_is_refused_by_name_not_crashed_on(
        tmp_path, at, commit):
    """The packet AS SIGNED (rollback.json present, with its api command for
    3d5af039) with ONE field of api's deploy list of the wrong shape: [0] is
    api's live deploy (16d23450), [1] the deploy it ran before (3d5af039),
    [2] the one before that (732cc0c6). The judge completes all 14
    categories and refuses api by name with no command (the signed record's
    api command is not passed on), still keeps the plane on 732cc0c6 with
    no command, and workers keeps its own. The tool, rebuilt over the same
    files, refuses api by name -- in particular it never reads past the
    unreadable [1] to name 732cc0c6 as api's previous."""
    acc = _packet_acc(tmp_path)
    p = acc / ("deploys_%s.json" % API)
    rows = json.loads(p.read_text())
    assert [rows[i]["deploy"]["commit"]["id"] for i in range(3)] == [
        REL_16D, HANG_3D5, PLANE_732]
    rows[at]["deploy"]["commit"] = commit
    p.write_text(json.dumps(rows))
    signed = json.loads((acc / "rollback.json").read_text())
    assert signed["commands"][API] == RB.COMMANDS[API] % HANG_3D5
    u = _score(acc, REL_16D)
    d = u["detail"]
    assert u["class"] == "FAIL" and u["passed"] is False, u
    assert d["rollback_by_service"][API] == {
        "action": "REFUSED", "reason": SC.R_ROLLBACK_HISTORY_UNREADABLE}
    assert "%s:%s" % (SC.R_ROLLBACK_HISTORY_UNREADABLE, API) in d["reasons"]
    assert d["commands"][API] is None
    _no_plane_command(d["commands"])
    assert d["rollback_by_service"][PLANE] == {
        "action": "NONE", "stay_on": PLANE_732,
        "reason": SC.R_ROLLBACK_SERVICE_NOT_ON_RELEASE}
    assert d["commands"][WORKERS] == RB.COMMANDS[WORKERS] % HANG_3D5
    rb = RB.build(acc, sha=REL_16D, target_is_ancestor=True)
    assert rb["rollback_by_service"][API] == {
        "action": RB.REFUSED, "reason": RB.R_DEPLOYS_UNREADABLE}
    assert rb["commands"][API] is None
    assert not any(PLANE_732 in str(c or "") for c in rb["commands"].values())
    _no_plane_command(rb["commands"])
    assert rb["rollback_by_service"][PLANE]["action"] == RB.NONE
    assert rb["commands"][WORKERS] == RB.COMMANDS[WORKERS] % HANG_3D5
    assert rb["status"] == RB.NOT_READY


def test_a_bad_row_that_cannot_change_what_ran_before_is_not_counted(
        tmp_path):
    """Rows of the wrong shape that cannot change what ran before: a failed
    build whose commit is a string (it never served) between the live
    deploy and PREV, and -- older than PREV -- a deploy whose commit is a
    list and a row that is not an object. Each service still goes back to
    PREV; the bad rows are never counted as previously live (only PREV and
    the readable OLD are); READY, and the judge passes the three own
    commands."""
    hist = _put(_GOOD, 3, _row(5, "build_failed", OTHER))
    hist += [_row(1, "deactivated", [HANG]), HANG,
             _dep(0, "deactivated", OLD)]
    acc = _acc(tmp_path, {s: hist for s in SERVICES})
    _gates(acc, PREV)
    rb = _record(acc, tmp_path)
    for s in SERVICES:
        assert rb["services"][s]["previously_live_commits"] == [PREV, OLD]
        assert rb["rollback_by_service"][s] == {
            "action": RB.DEPLOY_PREVIOUS, "from": REL, "to": PREV}
    assert rb["status"] == RB.READY, rb["reasons"]
    u = _score(acc, REL)
    assert u["passed"] is True, u["detail"]
    assert u["detail"]["commands"] == {s: RB.COMMANDS[s] % PREV
                                       for s in SERVICES}


def test_a_render_summary_of_the_wrong_shape_is_refused_by_name(tmp_path):
    """render.json naming the plane as a bare string instead of
    {"live_commit": ...}: the tool does not crash; the plane's live deploy
    cannot be checked against Render's summary, so it is REFUSED by name
    with no command, in the record and in what the judge passes on."""
    acc = _acc(tmp_path, {s: _GOOD for s in SERVICES})
    ren = json.loads((acc / "render.json").read_text())
    ren[PLANE] = REL
    (acc / "render.json").write_text(json.dumps(ren))
    _gates(acc, PREV)
    rb = _record(acc, tmp_path)
    assert rb["rollback_by_service"][PLANE] == {
        "action": RB.REFUSED, "reason": RB.R_LIVE_DIFFERS_FROM_RENDER}
    _no_plane_command(rb["commands"])
    assert rb["status"] == RB.NOT_READY
    u = _score(acc, REL)
    assert u["passed"] is False
    assert u["detail"]["rollback_by_service"][PLANE] == {
        "action": "REFUSED", "reason": SC.R_ROLLBACK_LIVE_NOT_RENDERS}
    _no_plane_command(u["detail"]["commands"])


def test_the_judge_never_passes_a_command_for_a_commit_never_live_there(
        tmp_path):
    """A record that writes, for a service on the release, a command naming
    a commit its own deploy list never shows live (OTHER only ever failed to
    build), or its own commit in ANOTHER service's command form: the judge
    passes on neither, each named."""
    acc = _acc(tmp_path, {s: _hist(REL, ("deactivated", PREV))
                          for s in SERVICES})
    _gates(acc, PREV)
    rb = _record(acc, tmp_path)
    rb["commands"][API] = RB.COMMANDS[API] % OTHER
    rb["commands"][WORKERS] = RB.COMMANDS[PLANE] % PREV
    (acc / "rollback.json").write_text(json.dumps(rb))
    d = _unit(acc, REL)["detail"]
    assert d["commands"][API] is None and d["commands"][WORKERS] is None
    assert d["commands"][PLANE] == RB.COMMANDS[PLANE] % PREV
    assert "%s:%s:%s" % (SC.R_ROLLBACK_NOT_OWN_PREVIOUS, API, OTHER) in \
        d["reasons"]
    assert "%s:%s" % (SC.R_ROLLBACK_NOT_ITS_SERVICE, WORKERS) in \
        d["reasons"]
    assert SC.R_ROLLBACK_COMMANDS_INCOMPLETE in d["reasons"]


def test_the_tool_never_writes_a_command_for_a_commit_never_live_there():
    """command() re-checks the service's own previously-live commits, so a
    plan naming any other commit (or its live one) writes nothing."""
    rec = {"live_commit": REL, "previously_live_commits": [PREV]}
    ok = {"action": RB.DEPLOY_PREVIOUS, "from": REL, "to": PREV}
    assert RB.command(API, ok, rec) == RB.COMMANDS[API] % PREV
    for bad in (OTHER, REL, None, "x"):
        assert RB.command(API, dict(ok, to=bad), rec) is None
    assert RB.command(API, {"action": RB.NONE, "stay_on": REL}, rec) is None
    # the judge's command forms are the tool's, text for text
    assert SC.ROLLBACK_COMMAND_FORMS == RB.COMMANDS


# ── 3. deploy statuses: an allowlist, and Render's newest-first order ────

#: the reason names, written out (the names are the contract; and a module
#: that fails to import would hide which case failed)
STATUS_UNKNOWN = "ROLLBACK_DEPLOY_STATUS_UNKNOWN"
SERVED_NEWER = "ROLLBACK_SERVED_DEPLOY_NEWER_THAN_THE_LIVE_DEPLOY"
#: Render's deploy statuses that never served (API `deploy.status`)
RENDER_NEVER_SERVED = frozenset((
    "build_failed", "update_failed", "canceled", "pre_deploy_failed",
    "created", "queued", "build_in_progress", "update_in_progress",
    "pre_deploy_in_progress"))


def _rows(*rows):
    """A Render deploy list, newest first, from (status, commit) pairs."""
    return [_dep(len(rows) - i, st, c) for i, (st, c) in enumerate(rows)]


#: the plane's history with ONE row where it decides what is live or what
#: ran before: a status Render does not document, or a served deploy newer
#: than the live one. At 3fa0ede0 every one of these gave READY and a
#: plane command for PREV (an unknown status was skipped as "never served";
#: a served row newer than the live one was ignored): each must be refused
#: by name with no command
_STATUS_ROWS = [
    # an unknown status between the live deploy and the previous live
    # commit (review: [live REL, 'Deactivated'/'succeeded' HANG,
    # deactivated PREV] -> READY + PREV)
    ("Deactivated_between_live_and_previous",
     _rows(("live", REL), ("Deactivated", HANG), ("deactivated", PREV)),
     STATUS_UNKNOWN),
    ("succeeded_between_live_and_previous",
     _rows(("live", REL), ("succeeded", HANG), ("deactivated", PREV)),
     STATUS_UNKNOWN),
    ("unknown_after_a_redeploy_of_the_live_commit",
     _rows(("live", REL), ("build_failed", OTHER), ("deactivated", REL),
           ("DEACTIVATED", HANG), ("deactivated", PREV)), STATUS_UNKNOWN),
    ("empty_status_between_live_and_previous",
     _rows(("live", REL), ("", HANG), ("deactivated", PREV)),
     STATUS_UNKNOWN),
    # an unknown, or live-like, status newer than the live deploy (it may
    # be what is live)
    ("unknown_status_newer_than_live",
     _rows(("succeeded", HANG), ("live", REL), ("deactivated", PREV)),
     STATUS_UNKNOWN),
    ("Live_newer_than_live",
     _rows(("Live", HANG), ("live", REL), ("deactivated", PREV)),
     STATUS_UNKNOWN),
    # a served deploy newer than the live one (review: [deactivated HANG,
    # live REL, deactivated PREV] -> READY + PREV)
    ("deactivated_newer_than_live",
     _rows(("deactivated", HANG), ("live", REL), ("deactivated", PREV)),
     SERVED_NEWER),
    ("deactivated_newer_than_live_behind_a_failed_build",
     _rows(("build_failed", OTHER), ("deactivated", HANG), ("live", REL),
           ("deactivated", PREV)), SERVED_NEWER),
    ("redeploy_of_the_live_commit_newer_than_live",
     _rows(("deactivated", REL), ("live", REL), ("deactivated", PREV)),
     SERVED_NEWER),
]
_STATUS_PARAMS = [pytest.param(h, r, id=n) for n, h, r in _STATUS_ROWS]


@pytest.mark.parametrize("hist,reason", _STATUS_PARAMS)
def test_an_unknown_status_or_a_served_deploy_newer_than_live_is_refused(
        tmp_path, hist, reason):
    """The tool over the plane's history with one such row: the plane is
    REFUSED by name with no command (never PREV, an older commit than the
    one it may last have run), api and workers keep their own, NOT_READY;
    the judge, over the tool's own record, passes on nothing for the plane
    and names the same reason (FAIL, never READY)."""
    acc = _acc(tmp_path, {API: _hist(REL, ("deactivated", PREV)),
                          WORKERS: _hist(REL, ("deactivated", PREV)),
                          PLANE: hist})
    _gates(acc, PREV)
    rb = _record(acc, tmp_path)
    _no_plane_command(rb["commands"])          # the defect itself, first
    assert rb["rollback_by_service"][PLANE] == {"action": "REFUSED",
                                                "reason": reason}
    assert "%s:%s" % (reason, PLANE) in rb["reasons"]
    assert rb["services"][PLANE]["previous_commit"] is None
    assert rb["services"][PLANE]["previously_live_commits"] == []
    for s in (API, WORKERS):
        assert rb["commands"][s] == RB.COMMANDS[s] % PREV
    assert rb["status"] == RB.NOT_READY
    u = _score(acc, REL)
    d = u["detail"]
    assert u["class"] == "FAIL" and u["passed"] is False, u
    assert d["status"] == "NOT_READY"
    assert d["rollback_by_service"][PLANE] == {"action": "REFUSED",
                                               "reason": reason}
    assert "%s:%s" % (reason, PLANE) in d["reasons"]
    _no_plane_command(d["commands"])
    for s in (API, WORKERS):
        assert d["commands"][s] == RB.COMMANDS[s] % PREV


@pytest.mark.parametrize("hist,reason", _STATUS_PARAMS)
def test_the_judge_refuses_an_unknown_status_with_the_record_present(
        tmp_path, hist, reason):
    """The judge on its own: rollback.json written while the plane's
    history read cleanly (it carries a market-plane command for PREV), then
    the plane's deploy list as it now reads. score() completes all 14
    categories; the plane is REFUSED by name, the record's plane command is
    not passed on, rollback_ready is FAIL; api and workers keep their own."""
    acc = _acc(tmp_path, {s: _GOOD for s in SERVICES})
    _gates(acc, PREV)
    clean = _record(acc, tmp_path)
    assert clean["commands"][PLANE] == RB.COMMANDS[PLANE] % PREV
    assert clean["status"] == RB.READY, clean["reasons"]
    (acc / ("deploys_%s.json" % PLANE)).write_text(json.dumps(hist))
    u = _score(acc, REL)
    d = u["detail"]
    _no_plane_command(d["commands"])           # the defect itself, first
    assert u["class"] == "FAIL" and u["passed"] is False, u
    assert d["rollback_by_service"][PLANE] == {"action": "REFUSED",
                                               "reason": reason}
    assert "%s:%s" % (reason, PLANE) in d["reasons"]
    for s in (API, WORKERS):
        assert d["commands"][s] == RB.COMMANDS[s] % PREV


@pytest.mark.parametrize("status", sorted(RENDER_NEVER_SERVED))
def test_a_status_render_documents_as_never_served_is_skipped(tmp_path,
                                                              status):
    """Each status Render documents as never served, newer than the live
    deploy (a deploy still building or that failed after it) and between
    the live deploy and PREV (around a redeploy of the live commit): it
    changes nothing -- each service goes back to PREV, counted as the only
    previously-live commit, READY, and the judge passes the three own
    commands. The allowlist refuses only what it does not know."""
    hist = _rows((status, OTHER), ("live", REL), (status, HANG),
                 ("deactivated", REL), (status, OLD), ("deactivated", PREV))
    acc = _acc(tmp_path, {s: hist for s in SERVICES})
    _gates(acc, PREV)
    rb = _record(acc, tmp_path)
    for s in SERVICES:
        assert rb["rollback_by_service"][s] == {
            "action": RB.DEPLOY_PREVIOUS, "from": REL, "to": PREV}, s
        assert rb["services"][s]["previously_live_commits"] == [PREV]
    assert rb["status"] == RB.READY, rb["reasons"]
    u = _score(acc, REL)
    assert u["passed"] is True, u["detail"]
    assert u["detail"]["commands"] == {s: RB.COMMANDS[s] % PREV
                                       for s in SERVICES}


def test_an_unknown_status_older_than_the_previous_live_commit_is_not_counted(
        tmp_path):
    """An unknown status OLDER than the previous live commit cannot change
    what ran before: not refused, and never counted as previously live
    (only the `deactivated` PREV and OLD are); READY, the judge passes the
    three own commands."""
    hist = _rows(("live", REL), ("deactivated", PREV), ("Deactivated", HANG),
                 ("succeeded", OTHER), ("deactivated", OLD))
    acc = _acc(tmp_path, {s: hist for s in SERVICES})
    _gates(acc, PREV)
    rb = _record(acc, tmp_path)
    for s in SERVICES:
        assert rb["services"][s]["previously_live_commits"] == [PREV, OLD]
        assert rb["rollback_by_service"][s]["to"] == PREV
    assert rb["status"] == RB.READY, rb["reasons"]
    u = _score(acc, REL)
    assert u["passed"] is True, u["detail"]
    assert u["detail"]["commands"] == {s: RB.COMMANDS[s] % PREV
                                       for s in SERVICES}


def test_the_never_served_allowlist_is_renders_in_the_tool_and_the_judge():
    """One allowlist, Render's documented never-served statuses, text for
    text in the tool and the judge (each stays one standard-library file);
    the two reason names are the same in both. The signed packet's own
    deploy lists carry only statuses Render documents, so the allowlist
    never refuses production's history."""
    assert RB.NEVER_SERVED == SC.RENDER_NEVER_SERVED == RENDER_NEVER_SERVED
    assert RB.LIVE == "live" and RB.DEACTIVATED == "deactivated"
    assert not ({RB.LIVE, RB.DEACTIVATED} & RB.NEVER_SERVED)
    assert RB.R_DEPLOY_STATUS_UNKNOWN == \
        SC.R_ROLLBACK_DEPLOY_STATUS_UNKNOWN == STATUS_UNKNOWN
    assert RB.R_SERVED_NEWER_THAN_LIVE == \
        SC.R_ROLLBACK_SERVED_NEWER_THAN_LIVE == SERVED_NEWER
    for s in SERVICES:
        rows = json.loads((FIX / ("deploys_%s.json" % s)).read_text())
        assert {r["deploy"]["status"] for r in rows} <= \
            RB.NEVER_SERVED | {RB.LIVE, RB.DEACTIVATED}, s


def _status_edit(rows, edit):
    rows = copy.deepcopy(rows)
    if edit.startswith("status="):
        rows[1]["deploy"]["status"] = edit.split("=", 1)[1]
    else:                       # a served deploy newer than the live one
        newer = copy.deepcopy(rows[1])
        newer["deploy"]["id"] = "dep-newer-than-live"
        rows.insert(0, newer)
    return rows


@pytest.mark.parametrize("svc,edit,reason", [
    (API, "status=Deactivated", STATUS_UNKNOWN),
    (API, "status=succeeded", STATUS_UNKNOWN),
    (API, "served_newer_than_live", SERVED_NEWER),
    (PLANE, "served_newer_than_live", SERVED_NEWER)],
    ids=["api_previous_status_Deactivated", "api_previous_status_succeeded",
         "api_served_deploy_newer_than_live",
         "plane_served_deploy_newer_than_live"])
def test_the_signed_packet_with_an_unknown_status_is_refused_by_name(
        tmp_path, svc, edit, reason):
    """The packet AS SIGNED with ONE row of one service's own deploy list
    changed. api: [0] live 16d23450, [1] 3d5af039 (what it ran before),
    [2] 732cc0c6. With [1]'s status one Render does not document, the tool
    at 3fa0ede0 skipped it as never served and wrote an api command for
    732cc0c6 (the plane's commit, older than what api last ran); with a
    served 3d5af039 row newer than the live deploy it ignored the row. Now
    api is REFUSED by name with no command, in the record and in what the
    judge passes on, and no command names 732cc0c6. The plane (live on
    732cc0c6, [1] 3d5af039) with a served row newer than its live deploy
    is REFUSED by name with no command (never a command for 3d5af039).
    Whatever else: the plane gets no command, workers keeps its own
    3d5af039, NOT_READY."""
    acc = _packet_acc(tmp_path)
    p = acc / ("deploys_%s.json" % svc)
    rows = json.loads(p.read_text())
    assert [rows[i]["deploy"]["status"] for i in range(2)] == [
        "live", "deactivated"]
    p.write_text(json.dumps(_status_edit(rows, edit)))
    for rec in (_score(acc, REL_16D)["detail"],
                RB.build(acc, sha=REL_16D, target_is_ancestor=True)):
        cmds = rec["commands"]
        _no_plane_command(cmds)                # the defect class, first
        assert not any(PLANE_732 in str(c or "") for c in cmds.values())
        assert rec["status"] == "NOT_READY"
        assert rec["rollback_by_service"][svc] == {"action": "REFUSED",
                                                   "reason": reason}
        assert "%s:%s" % (reason, svc) in rec["reasons"]
        assert cmds[svc] is None
        assert cmds[WORKERS] == RB.COMMANDS[WORKERS] % HANG_3D5
        if svc == API:
            assert rec["rollback_by_service"][PLANE] == {
                "action": "NONE", "stay_on": PLANE_732,
                "reason": RB.R_SERVICE_NOT_ON_RELEASE}
        else:
            assert cmds[API] == RB.COMMANDS[API] % HANG_3D5
