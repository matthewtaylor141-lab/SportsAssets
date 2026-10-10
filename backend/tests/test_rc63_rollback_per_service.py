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
  * an unreadable or unusable history is refused by name, with no command.
"""
from __future__ import annotations

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
])
def test_an_unusable_history_is_refused_by_name_with_no_command(
        tmp_path, hist, render, reason):
    """The plane's history unreadable, without exactly one live deploy,
    with no previous LIVE commit (a failed or cancelled deploy never
    served), or disagreeing with render.json: REFUSED by name and no
    command for it -- never another service's commit -- while api and
    workers keep their own; NOT_READY. The judge passes on nothing for it."""
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
