"""THE TWO RECEIPTS THE API CANNOT PRODUCE ABOUT ITSELF (PM review of RC4,
pm-acceptance run 37738089957 on 7fd4574e, 2026-10-08).

no_oom_minutes was the shared workers' process age: 76.7 min, a passing
value, while sportsassets-market-plane had been oomKilled twice (06:10:01Z
and 06:12:10Z, 2Gi) since it went live at 05:16:49Z.
historical_paper_immutable was typed True.

Both are now bound only from receipts (pm_bind.acceptance.runtime_window /
paper_history), and every gap in a receipt is UNKNOWN / UNPROVEN with a
named reason, never zero failures and never True:

  runtime  one OOM in an otherwise green 60-minute window -> RED; a
           truncated page chain -> UNKNOWN; another service's id -> not
           green; 59 minutes -> not green; a failed / malformed / unchained
           page, an unclassified event, another deploy, a foreign instance
           -> UNKNOWN
  PAPER    missing baseline, mismatched cutoff, unsettled watermark,
           unverified baseline, schema change, a baseline taken after the
           deploy -> UNPROVEN; a differing row set or a backdated insert ->
           CHANGED (False); moving projections reconciled, never history
"""
from __future__ import annotations

import copy
import json

import pytest

from sportsassets.pm_bind import acceptance as PA
from sportsassets.pm_evidence.pm_acceptance import harness as H

try:
    from tests import pm_acceptance_fixture as F
except ImportError:                                             # pragma: no cover
    import pm_acceptance_fixture as F  # type: ignore

PLANE = "sportsassets-market-plane"
WK = "sportsassets-workers"


def rw(raw, commit=F.SHA):
    return PA.runtime_window(raw, expected_commit=commit)


def no_oom_gate(raw) -> bool:
    e, _ = PA.independent({"worker_rss_fraction": 0.4}, runtime=raw,
                          paper=None, expected_commit=F.SHA, now=1.0)
    return H.derive_gates(e, PA.spec())["no_oom"]["pass"]


# ── runtime: the PM's four named cases ─────────────────────────────────────

def test_a_complete_clean_60_minute_window_is_the_only_green():
    r = rw(F.render_raw(minutes=60.0))
    assert r["status"] == PA.CLEAN, r["reasons"]
    assert r["no_oom_minutes"] == 60.0 and r["reasons"] == []
    for name, sid in F.SIDS.items():
        w = r["services"][name]
        assert w["service_id"] == sid and w["commit"] == F.SHA
        assert w["deploy_id"] == "dep-%s" % sid[4:]
        assert w["instances"] == [sid + "-c52xp"]
        assert w["pages"] == 1 and w["events_in_window"] == 2
    assert no_oom_gate(F.render_raw(minutes=60.0)) is True


def test_one_oom_injected_into_a_green_60_minute_window_is_red():
    raw = F.add_event(F.render_raw(minutes=60.0), PLANE,
                      F.oom(F.SIDS[PLANE], F.LIVE + 1800))
    r = rw(raw)
    assert r["status"] == PA.FAILED and r["no_oom_minutes"] == 0.0
    f = r["services"][PLANE]["failures"]
    assert len(f) == 1 and f[0]["oom_killed"] is True
    assert f[0]["type"] == "server_failed"
    assert no_oom_gate(raw) is False
    e, rep = PA.independent({}, runtime=raw, paper=None,
                            expected_commit=F.SHA, now=1.0)
    assert e["no_oom_minutes"] == 0.0
    assert H.evaluate(e, PA.spec())["pm_state"] == "RED"


def test_truncated_pages_are_unknown_never_zero_failures():
    raw = F.render_raw(minutes=60.0)
    sid = F.SIDS[WK]
    # a FULL first page, every event inside the window, and no next page
    raw["services"][WK]["event_pages"] = [[
        F.ev(sid, "server_available", F.LIVE + 10 + i, 100 + i)
        for i in range(100)]]
    r = rw(raw)
    assert r["status"] == PA.UNKNOWN and r["no_oom_minutes"] is None
    assert "%s:%s" % (WK, PA.R_RUNTIME_EVENTS_TRUNCATED) in r["reasons"]
    assert no_oom_gate(raw) is False
    _, rep = PA.independent({}, runtime=raw, paper=None,
                            expected_commit=F.SHA, now=1.0)
    assert "no_oom_minutes" in rep["unproven"]


def test_a_wrong_service_id_is_never_green():
    other = F.SIDS["sportsassets-api"]
    raw = F.render_raw(minutes=75.0)
    raw["services"][WK]["event_pages"][0].insert(
        0, F.ev(other, "server_available", F.LIVE + 60, 77))
    r = rw(raw)
    assert r["status"] == PA.UNKNOWN
    assert "%s:%s:%s" % (WK, PA.R_RUNTIME_EVENT_WRONG_SERVICE,
                         other) in r["reasons"]
    assert no_oom_gate(raw) is False
    # an OOM carried under another service's id is not counted as ours,
    # and does not make the window clean either
    raw = F.render_raw(minutes=75.0)
    raw["services"][WK]["event_pages"][0].insert(
        0, F.oom(other, F.LIVE + 60))
    r = rw(raw)
    assert r["services"][WK]["failures"] == []
    assert r["status"] == PA.UNKNOWN
    # metrics of another service
    raw = F.render_raw(minutes=75.0)
    lab = raw["services"][WK]["memory"][0]["labels"]
    lab[1]["value"] = other
    assert rw(raw)["status"] == PA.UNKNOWN
    # the lookup bound to a different id than the events carry
    raw = F.render_raw(minutes=75.0)
    raw["services"][WK]["lookup"][0]["service"]["id"] = "srv-impostor0001"
    r = rw(raw)
    assert r["status"] == PA.UNKNOWN
    assert any(PA.R_RUNTIME_EVENT_WRONG_SERVICE in x for x in r["reasons"])


def test_59_minutes_is_not_green():
    r = rw(F.render_raw(minutes=59.0))
    assert r["status"] == PA.CLEAN and r["no_oom_minutes"] == 59.0
    assert no_oom_gate(F.render_raw(minutes=59.0)) is False
    assert no_oom_gate(F.render_raw(minutes=60.0)) is True


def test_the_shortest_service_window_is_the_observation():
    raw = F.render_raw(minutes=120.0)
    # the plane went live 70 minutes before collection, the others 120
    late = F.LIVE + 50 * 60
    s = raw["services"][PLANE]
    s["deploys"][0]["deploy"]["finishedAt"] = F.iso(late)
    s["deploys"][0]["deploy"]["createdAt"] = F.iso(late - 80)
    s["events_meta"]["start"] = F.iso(late, frac=False)
    r = rw(raw)
    assert r["status"] == PA.CLEAN and r["no_oom_minutes"] == 70.0


# ── runtime: every other way a read can be wrong ──────────────────────────

def test_the_rc4_market_plane_window_is_failed_where_rc4_said_76_minutes():
    """Replay of the real RC4 market-plane events (live 05:16:49Z, two
    oomKilled at 2Gi): the window is FAILED, no_oom_minutes 0, where the RC4
    binder reported the workers' process age, 76.7."""
    raw = F.render_raw(minutes=83.0, commit=F.RC4_PLANE_DEPLOY["commit"]["id"])
    s = raw["services"][PLANE]
    s["lookup"][0]["service"]["id"] = F.RC4_PLANE_SID
    s["deploys"] = [{"deploy": F.RC4_PLANE_DEPLOY, "cursor": "x"}]
    s["event_pages"] = [copy.deepcopy(F.RC4_PLANE_EVENTS)]
    s["memory"][0]["labels"] = [
        {"field": "instance", "value": F.RC4_PLANE_SID + "-z2qbz"},
        {"field": "service", "value": F.RC4_PLANE_SID}]
    r = rw(raw, commit=F.RC4_PLANE_DEPLOY["commit"]["id"])
    p = r["services"][PLANE]
    assert p["status"] == PA.FAILED, p["reasons"]
    assert [f["timestamp"] for f in p["failures"]] == [
        "2026-10-08T06:12:10.439986Z", "2026-10-08T06:10:01.514668Z"]
    assert all(f["oom_killed"] for f in p["failures"])
    assert r["no_oom_minutes"] == 0.0
    summ = PA.render_summary(raw, expected_commit=F.RC4_PLANE_DEPLOY[
        "commit"]["id"])
    assert summ[PLANE]["oom_events_since_live"] == 2


@pytest.mark.parametrize("typ,details", [
    ("server_failed", {"instanceID": "%s-x1", "reason": {
        "evicted": False,
        "unhealthy": "HTTP health check failed (timed out after 5 seconds)"}}),
    ("server_restarted", {"triggeredByUser": "usr-fixture"}),
    ("server_failed", {"instanceID": "%s-x1", "reason": {
        "evicted": False, "nonZeroExit": 1}})])
def test_every_failure_and_restart_type_fails_the_window(typ, details):
    sid = F.SIDS[WK]
    d = json.loads(json.dumps(details).replace("%s", sid))
    raw = F.add_event(F.render_raw(), WK, F.ev(sid, typ, F.LIVE + 600, 50, d))
    r = rw(raw)
    assert r["status"] == PA.FAILED and r["no_oom_minutes"] == 0.0


def test_a_failure_before_live_is_not_this_releases():
    raw = F.add_event(F.render_raw(), WK,
                      F.oom(F.SIDS[WK], F.LIVE - 3600))
    raw["services"][WK]["event_pages"][0].sort(
        key=lambda x: x["event"]["timestamp"], reverse=True)
    assert rw(raw)["status"] == PA.CLEAN


def test_a_complete_multi_page_chain_is_clean_and_a_broken_one_is_not():
    raw = F.render_raw(minutes=200.0)
    sid = F.SIDS[WK]
    s = raw["services"][WK]
    first = [F.ev(sid, "server_available", F.LIVE + 9000 - i, 200 + i)
             for i in range(100)]
    s["event_pages"] = [first, s["event_pages"][0]]
    s["events_meta"]["requests"] = [
        {"cursor": "", "http": "200", "page": "events_%s_p000.json" % WK},
        {"cursor": first[-1]["cursor"], "http": "200",
         "page": "events_%s_p001.json" % WK}]
    r = rw(raw)
    assert r["status"] == PA.CLEAN, r["reasons"]
    assert r["services"][WK]["pages"] == 2
    bad = copy.deepcopy(raw)
    bad["services"][WK]["events_meta"]["requests"][1]["cursor"] = "elsewhere"
    r = rw(bad)
    assert r["status"] == PA.UNKNOWN
    assert "%s:%s:page1" % (WK, PA.R_RUNTIME_EVENTS_CURSOR_CHAIN) in \
        r["reasons"]
    # a failed second page: UNKNOWN, but an OOM on the first still counts
    bad = copy.deepcopy(raw)
    bad["services"][WK]["events_meta"]["requests"][1]["http"] = "500"
    assert rw(bad)["status"] == PA.UNKNOWN
    bad["services"][WK]["event_pages"][0].insert(
        0, F.oom(sid, F.LIVE + 9500))
    assert rw(bad)["status"] == PA.FAILED


@pytest.mark.parametrize("mutate,reason", [
    (lambda s: s.update(lookup=[]), PA.R_RUNTIME_SERVICE_ABSENT),
    (lambda s: s.update(lookup=s["lookup"] * 2),
     PA.R_RUNTIME_SERVICE_AMBIGUOUS),
    (lambda s: s.update(lookup={"error": "unauthorized"}),
     PA.R_RUNTIME_SERVICE_LOOKUP_UNREADABLE),
    (lambda s: s["lookup"][0]["service"].update(suspended="suspended"),
     PA.R_RUNTIME_SERVICE_SUSPENDED),
    (lambda s: s.update(deploys=None), PA.R_RUNTIME_DEPLOYS_UNREADABLE),
    (lambda s: s["deploys"][0]["deploy"].update(status="deactivated"),
     PA.R_RUNTIME_LIVE_DEPLOY_NOT_FOUND),
    (lambda s: s["deploys"][1]["deploy"].update(status="live"),
     PA.R_RUNTIME_LIVE_DEPLOY_AMBIGUOUS),
    (lambda s: s["deploys"][0]["deploy"]["commit"].update(id="d" * 40),
     PA.R_RUNTIME_DEPLOY_NOT_ON_RELEASE),
    (lambda s: s.update(events_meta=None), PA.R_RUNTIME_WINDOW_UNREADABLE),
    (lambda s: s["events_meta"].update(start=F.iso(F.LIVE + 600)),
     PA.R_RUNTIME_WINDOW_NOT_COVERED),
    (lambda s: s["events_meta"]["requests"][0].update(http="429"),
     PA.R_RUNTIME_EVENTS_READ_FAILED),
    (lambda s: s.update(event_pages=[{"error": "x"}]),
     PA.R_RUNTIME_EVENTS_MALFORMED),
    (lambda s: s["event_pages"][0][0]["event"].pop("serviceId"),
     PA.R_RUNTIME_EVENTS_MALFORMED),
    (lambda s: s["event_pages"][0].insert(0, F.ev(
        s["lookup"][0]["service"]["id"], "maintenance_started",
        F.LIVE + 60, 61)), PA.R_RUNTIME_UNCLASSIFIED_EVENT),
    (lambda s: s["event_pages"][0].insert(0, F.ev(
        s["lookup"][0]["service"]["id"], "deploy_started", F.LIVE + 60, 62,
        {"deployId": "dep-next"})), PA.R_RUNTIME_OTHER_DEPLOY_IN_WINDOW),
    (lambda s: s["event_pages"][0].insert(0, F.ev(
        s["lookup"][0]["service"]["id"], "server_available", F.LIVE + 60,
        63, {"instanceID": "srv-someoneelse-abcde"})),
     PA.R_RUNTIME_EVENT_WRONG_INSTANCE),
    (lambda s: s.update(memory=None), PA.R_RUNTIME_METRICS_UNREADABLE),
    (lambda s: s.update(memory_http="503"), PA.R_RUNTIME_METRICS_UNREADABLE),
    (lambda s: s.update(memory=[]), PA.R_RUNTIME_NO_INSTANCE_OBSERVED),
    (lambda s: s["memory"][0]["labels"][0].update(value="srv-other-abcde"),
     PA.R_RUNTIME_WRONG_INSTANCE),
])
def test_every_unreadable_or_foreign_read_is_unknown(mutate, reason):
    raw = F.render_raw()
    mutate(raw["services"][WK])
    r = rw(raw)
    assert r["status"] == PA.UNKNOWN, r
    assert r["no_oom_minutes"] is None
    assert any(x.startswith("%s:%s" % (WK, reason)) for x in r["reasons"]), \
        r["reasons"]
    assert no_oom_gate(raw) is False


@pytest.mark.parametrize("raw", [None, {}, {"services": {}},
                                 {"collected_at": None, "services": {}}])
def test_an_absent_receipt_is_unknown(raw):
    r = rw(raw)
    assert r["status"] == PA.UNKNOWN and r["no_oom_minutes"] is None
    assert r["reasons"]


def test_a_missing_service_is_unknown_even_when_the_others_are_clean():
    raw = F.render_raw()
    del raw["services"][PLANE]
    r = rw(raw)
    assert r["status"] == PA.UNKNOWN
    assert "%s:%s" % (PLANE, PA.R_RUNTIME_SERVICE_NOT_READ) in r["reasons"]


def test_the_loader_reads_back_what_the_workflow_writes(tmp_path):
    raw = F.render_raw(minutes=61.0)
    F.write_runtime(tmp_path, raw)
    got = PA.load_runtime(tmp_path)
    assert rw(got)["status"] == PA.CLEAN
    assert rw(got)["no_oom_minutes"] == 61.0
    # a page file the meta names but the job never wrote
    (tmp_path / ("events_%s_p000.json" % WK)).unlink()
    assert rw(PA.load_runtime(tmp_path))["status"] == PA.UNKNOWN
    # nothing collected at all (no Render key)
    assert PA.load_runtime(tmp_path / "nothing") is None


def test_the_loader_never_follows_a_page_name_out_of_acc(tmp_path):
    raw = F.render_raw()
    F.write_runtime(tmp_path, raw)
    meta = json.loads((tmp_path / ("render_events_%s.json" % WK))
                      .read_text())
    meta["requests"][0]["page"] = "../../etc/passwd"
    (tmp_path / ("render_events_%s.json" % WK)).write_text(json.dumps(meta))
    got = PA.load_runtime(tmp_path)
    assert got["services"][WK]["event_pages"] == [None]
    assert rw(got)["status"] == PA.UNKNOWN


# ── PAPER history ──────────────────────────────────────────────────────────

def ph(receipt, deploy_started_at=F.LIVE - 84.0):
    return PA.paper_history(receipt.get("pre"), receipt.get("post"),
                            baseline=receipt.get("baseline"),
                            deploy_started_at=deploy_started_at)


def test_matching_pre_and_post_at_the_same_cutoff_is_proven():
    r = ph(F.paper_receipt())
    assert r["status"] == PA.PROVEN and r["immutable"] is True, r["reasons"]
    assert r["cutoff"] == F.CUTOFF
    assert all(t["match"] for t in r["tables"].values())
    assert set(r["tables"]) == set(PA.IMMUTABLE_TABLES)
    # the projections moved and are reconciled, not counted as history
    acc = r["projections"]["paper_accounts"]
    assert acc["status"] == "RECONCILED" and acc["accounts_added"] == 1
    assert r["projections"]["paper_orders"]["moved"] == ["digest"]
    assert r["projections"]["paper_orders"]["status"] == \
        "RECONCILED_AS_PROJECTION"


@pytest.mark.parametrize("table,field", [
    (t, f) for t, fs in PA.IMMUTABLE_TABLES.items() for f in fs])
def test_any_differing_immutable_field_is_changed(table, field):
    rc = F.paper_receipt()
    v = rc["post"]["tables"][table][field]
    rc["post"]["tables"][table][field] = (
        v + 1 if isinstance(v, int) else "f" * 32 if field == "digest"
        else v + "1")
    r = ph(rc)
    assert r["status"] == PA.CHANGED and r["immutable"] is False
    assert "%s:%s:%s" % (PA.R_PAPER_HISTORY_CHANGED, table, field) in \
        r["reasons"]


@pytest.mark.parametrize("table", PA.BACKDATABLE)
def test_a_backdated_insert_below_the_cutoff_is_changed(table):
    """A fill / decision stamped below the cutoff by the application but
    recorded after the PRE capture is history added after the fact."""
    rc = F.paper_receipt()
    rc["post"]["tables"][table]["late_recorded_rows"] = 1
    r = ph(rc)
    assert r["status"] == PA.CHANGED
    assert "%s:%s" % (PA.R_PAPER_BACKDATED_ROWS, table) in r["reasons"]


def test_missing_baseline_or_post_is_unproven_never_true():
    rc = F.paper_receipt()
    for pre, post, why in ((None, rc["post"], PA.R_PAPER_BASELINE_ABSENT),
                           (rc["pre"], None, PA.R_PAPER_POST_ABSENT)):
        r = PA.paper_history(pre, post, baseline=rc["baseline"],
                             deploy_started_at=F.LIVE)
        assert r["status"] == PA.UNPROVEN and r["immutable"] is None
        assert why in r["reasons"]
    e, rep = PA.independent({"historical_paper_immutable": True},
                            runtime=None, paper=None,
                            expected_commit=F.SHA, now=1.0)
    # the API's typed True is never carried
    assert "historical_paper_immutable" not in e
    assert rep["replaced_from_api"] == {"historical_paper_immutable": True}
    assert rep["unproven"]["historical_paper_immutable"] == [
        PA.R_PAPER_RECEIPT_ABSENT]


def test_a_mismatched_cutoff_is_unproven():
    rc = F.paper_receipt()
    rc["post"] = F.fingerprint(captured_at=F.LIVE + 4500,
                               cutoff="2026-10-08T04:00:00Z")
    r = ph(rc)
    assert r["status"] == PA.UNPROVEN
    assert any(x.startswith(PA.R_PAPER_CUTOFF_MISMATCH) for x in r["reasons"])


@pytest.mark.parametrize("mutate,reason", [
    (lambda rc: rc["pre"]["watermark"].update(
        writers_open_since_before_cutoff=1), PA.R_PAPER_BASELINE_NOT_SETTLED),
    (lambda rc: rc["pre"]["watermark"].update(cutoff_lag_s=599.0),
     PA.R_PAPER_BASELINE_NOT_SETTLED),
    (lambda rc: rc["post"]["watermark"].update(
        writers_open_since_before_cutoff=2), PA.R_PAPER_POST_NOT_SETTLED),
    (lambda rc: rc["post"].update(database="other_db"),
     PA.R_PAPER_DIFFERENT_DATABASE),
    (lambda rc: rc["post"]["settings"].update(TimeZone="America/New_York"),
     PA.R_PAPER_SESSION_SETTINGS_DIFFER),
    (lambda rc: rc["post"].update(captured_at=rc["pre"]["captured_at"]),
     PA.R_PAPER_POST_NOT_AFTER_BASELINE),
    (lambda rc: rc.update(baseline=None), PA.R_PAPER_BASELINE_UNVERIFIED),
    (lambda rc: rc["baseline"].update(attestation_verified=False),
     PA.R_PAPER_BASELINE_UNVERIFIED),
    (lambda rc: rc["baseline"].update(capture_sha256_in_packet="0" * 64),
     PA.R_PAPER_BASELINE_UNVERIFIED),
    (lambda rc: rc["baseline"].update(
        workflow_path=".github/workflows/research-sql.yml"),
     PA.R_PAPER_BASELINE_UNVERIFIED),
    (lambda rc: rc["post"]["tables"].pop("paper_settlements"),
     PA.R_PAPER_TABLE_NOT_IN_RECEIPT),
    (lambda rc: rc["pre"]["tables"]["paper_fills"].pop("late_recorded_rows"),
     PA.R_PAPER_MALFORMED),
    (lambda rc: rc["post"]["tables"]["paper_ledger"].update(digest=None),
     PA.R_PAPER_MALFORMED),
    (lambda rc: rc["post"].update(version="V0"), PA.R_PAPER_MALFORMED),
    (lambda rc: rc["pre"].update(watermark=None), PA.R_PAPER_MALFORMED),
])
def test_partial_conflicting_or_unverified_evidence_is_unproven(mutate,
                                                                reason):
    rc = F.paper_receipt()
    mutate(rc)
    r = ph(rc)
    assert r["status"] == PA.UNPROVEN and r["immutable"] is None, r
    assert any(x.startswith(reason) for x in r["reasons"]), r["reasons"]


def test_a_schema_change_is_unproven_not_a_claimed_mutation():
    rc = F.paper_receipt()
    rc["post"]["columns"]["paper_fills"] += ",new_col:text"
    rc["post"]["tables"]["paper_fills"]["digest"] = "e" * 32
    r = ph(rc)
    assert r["status"] == PA.UNPROVEN and r["immutable"] is None
    assert "%s:paper_fills" % PA.R_PAPER_COLUMNS_CHANGED in r["reasons"]
    # ... but a real change in another table is still CHANGED
    rc["post"]["tables"]["paper_ledger"]["max_seq"] += 1
    assert ph(rc)["status"] == PA.CHANGED


def test_the_baseline_must_be_taken_before_the_deploy():
    rc = F.paper_receipt()
    assert ph(rc, deploy_started_at=F.LIVE - 3700.0)["reasons"] == [
        PA.R_PAPER_BASELINE_NOT_BEFORE_DEPLOY]
    assert PA.R_PAPER_DEPLOY_TIME_UNKNOWN in ph(rc, None)["reasons"]


def test_new_accounts_are_reconciled_but_a_vanished_one_is_not():
    rc = F.paper_receipt()
    r = ph(rc)
    assert r["projections"]["paper_accounts"]["status"] == "RECONCILED"
    rc["post"]["projections"]["paper_accounts"]["rows_all"] -= 2
    r = ph(rc)
    assert r["projections"]["paper_accounts"]["status"] == "NOT_RECONCILED"


def test_independent_binds_both_from_the_jobs_files(tmp_path):
    F.write_runtime(tmp_path, F.render_raw(minutes=75.0))
    F.write_paper(tmp_path, F.paper_receipt())
    api = {"no_oom_minutes": 76.7, "historical_paper_immutable": True,
           "worker_rss_fraction": 0.82}
    e, rep = PA.independent(api, runtime=PA.load_runtime(tmp_path),
                            paper=PA.load_paper(tmp_path),
                            expected_commit=F.SHA, now=5.0)
    assert e["no_oom_minutes"] == 75.0
    assert e["historical_paper_immutable"] is True
    assert e["worker_rss_fraction"] == 0.82
    assert rep["replaced_from_api"] == {"no_oom_minutes": 76.7,
                                        "historical_paper_immutable": True}
    assert rep["runtime_window"]["status"] == PA.CLEAN
    assert rep["paper_history"]["status"] == PA.PROVEN
    # the baseline downloaded but unreadable: named, not "receipt absent"
    (tmp_path / "paper_history_pre.json").write_text("{not json")
    _, rep = PA.independent(api, runtime=None, paper=PA.load_paper(tmp_path),
                            expected_commit=F.SHA, now=5.0)
    assert rep["unproven"]["historical_paper_immutable"][0].startswith(
        PA.R_PAPER_BASELINE_ABSENT)


def test_the_binder_is_importable_by_the_workflow_with_stdlib_only():
    """pm-acceptance runs `python3 -I` on a bare runner: acceptance.py and
    the harness may import only the standard library."""
    import ast
    import pathlib
    import sys
    root = pathlib.Path(PA.__file__).resolve().parents[1]
    for f in (root / "pm_bind" / "acceptance.py",
              root / "pm_evidence" / "pm_acceptance" / "harness.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                mods = [(node.module or "").split(".")[0]]
            else:
                continue
            for m in mods:
                assert m == "__future__" or m in sys.stdlib_module_names, \
                    (f.name, m)
