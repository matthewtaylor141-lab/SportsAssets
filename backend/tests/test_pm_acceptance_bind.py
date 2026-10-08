"""PM Evidence Pack component 3 bound: the harness (byte-identical package)
decides RED / YELLOW / GREEN from MACHINE evidence collected field by field
(pm_bind.acceptance). Missing evidence is never success; a status narrative
cannot be typed in; nothing here activates money.

Since the PM review of RC4 (2026-10-08) GREEN also needs the two receipts
the API cannot produce about itself: the Render runtime window (no_oom) and
the PRE / POST historical PAPER fingerprints (historical_paper_immutable).
Neither is ever defaulted: the old green fixture reached GREEN only through
a hard-coded True and the workers' process age."""
from __future__ import annotations

import copy
import json

from sportsassets.pm_bind import acceptance as PA

try:
    from tests import pm_acceptance_fixture as F
except ImportError:                                             # pragma: no cover
    import pm_acceptance_fixture as F  # type: ignore

NOW = 1_791_400_000.0
SHA = "a" * 40
BASE = "b" * 40


def green_red() -> dict:
    g = {"status": "GREEN"}
    return {
        "implementation_sha": SHA,
        "completion": {
            "small_live": "SHADOW",
            "runtime": {
                "shared_workers": {"commit_sha": SHA,
                                   "minutes_since_process_start": 240,
                                   "rss_highwater_fraction": 0.41,
                                   "universal_market_plane_started_here":
                                       False},
                "market_plane": {"state": "DEDICATED_RUNNING"}},
            "market_data": {"snapshot": "CURRENT", "fresh": 812,
                            "subscription_mode": "ALL"},
            "settlement": {"settlement_proven": True},
            "gate_evidence": {
                "software_reds_zero": {"evidence": {"software": 0}},
                "production_canary_clean": {"value": True},
                "xavier_complete": {"value": True}}},
        "controls": {
            "MIGRATION_INTEGRITY": g, "TRUTH_QUORUM": g,
            "CREDENTIAL_CLASSES": g, "PROFIT_BREAKERS": g,
            "VENUE_HEALTH": {"status": "GREEN", "evidence": {
                "isolated": True,
                "freshness": {
                    "held": {"numerator": 30, "denominator": 30,
                             "source": "held books"},
                    "priority": {"numerator": 240, "denominator": 246,
                                 "source": "priority books"}},
                "venues": {"KALSHI": {"green": True, "denominator": 64}}}},
            "DIGITAL_TWIN": {"status": "GREEN", "evidence": {
                "compared": 800, "matched": 790,
                "optimistic_false_fills": 2, "lookahead_violations": 0}},
            "CAPACITY": {"status": "GREEN", "evidence": {
                "proven_positive_capacity_usd": "82000"}}},
        "completion_readiness": {"governors": {"DIRECTIONAL": "ELIGIBLE"}}}


def green_board() -> dict:
    return {"mechanisms": {"DIRECTIONAL": {
        "forward_pnl_lcb_per_event": "0.42"}},
        "paper_ledger_realized_total_usd": "1234.50"}


def green_release() -> dict:
    return {"accepted_base_sha": BASE, "tested_sha": SHA, "release_sha": SHA,
            "descendant_of_base": True, "backend_tests_green": True,
            "capital_critical_green": True, "commit_guard_green": True,
            "engine_diagnostic_green": True}


def ev(red=None, board=None, rel="DEFAULT", runtime="DEFAULT",
       paper="DEFAULT"):
    return PA.evaluate(red=green_red() if red is None else red,
                       scoreboard=green_board() if board is None else board,
                       release=green_release() if rel == "DEFAULT" else rel,
                       runtime=(F.render_raw() if runtime == "DEFAULT"
                                else runtime),
                       paper=(F.paper_receipt() if paper == "DEFAULT"
                              else paper),
                       now=NOW)


def test_complete_machine_evidence_is_the_only_path_to_green():
    r = ev()
    assert r["pm_state"] == "GREEN", (r["critical_failures"],
                                      r["economic_or_evidence_gaps"])
    assert r["capital_status"] == "CAPITAL_CANDIDATE"
    assert r["fields_without_machine_evidence"] == []
    assert r["activates_money"] is False
    # every field carries its machine source and as_of
    assert set(r["provenance"]) == set(r["evidence_input"])
    assert all(p["source"] and p["as_of"] == NOW
               for p in r["provenance"].values())


def test_no_release_receipt_is_red_never_assumed():
    r = ev(rel=None)
    assert r["pm_state"] == "RED"
    for k in ("release_lineage", "exact_sha", "backend_tests",
              "capital_critical", "commit_guard", "engine_diagnostic"):
        assert k in r["critical_failures"]
    assert "tested_sha" in r["fields_without_machine_evidence"]


def test_empty_evidence_is_red_with_every_field_missing_but_the_invariants():
    r = PA.evaluate(red={}, scoreboard={}, release=None, now=NOW)
    assert r["pm_state"] == "RED"
    # historical_paper_immutable is NOT an invariant: it was typed True
    assert set(r["evidence_input"]) == {"live_authority_shadow"}
    assert r["evidence_input"]["live_authority_shadow"] is False
    assert r["unproven"]["historical_paper_immutable"] == [
        PA.R_PAPER_RECEIPT_ABSENT]
    assert r["unproven"]["no_oom_minutes"] == [PA.R_RUNTIME_RECEIPT_ABSENT]
    for g in ("historical_paper_immutable", "no_oom"):
        assert g in r["critical_failures"]


def test_tested_release_deployed_must_be_one_sha():
    rel = green_release()
    rel["release_sha"] = "c" * 40
    assert "exact_sha" in ev(rel=rel)["critical_failures"]
    red = green_red()
    red["implementation_sha"] = "UNKNOWN"
    r = ev(red=red)
    assert "exact_sha" in r["critical_failures"]
    assert "deployed_sha" in r["fields_without_machine_evidence"]


def test_small_live_not_shadow_is_red():
    red = green_red()
    red["completion"]["small_live"] = "ACTIVE"
    assert "live_authority_shadow" in ev(red=red)["critical_failures"]


def test_market_plane_in_shared_workers_is_red():
    red = green_red()
    red["completion"]["runtime"]["shared_workers"][
        "universal_market_plane_started_here"] = True
    assert "shared_worker_ump_isolated" in ev(red=red)["critical_failures"]
    red = green_red()
    red["completion"]["runtime"]["market_plane"] = {"state": "ABSENT"}
    assert "shared_worker_ump_isolated" in ev(red=red)["critical_failures"]


def test_freshness_denominators_are_required_not_defaulted():
    red = green_red()
    red["controls"]["VENUE_HEALTH"]["evidence"]["freshness"]["held"] = {
        "numerator": 0, "denominator": 0}
    r = ev(red=red)
    assert "held_freshness" in r["critical_failures"]
    assert "held_required" in r["fields_without_machine_evidence"]


def test_negative_forward_edge_is_yellow_not_green():
    board = green_board()
    board["mechanisms"]["DIRECTIONAL"]["forward_pnl_lcb_per_event"] = "-0.1"
    r = ev(board=board)
    assert r["pm_state"] == "YELLOW"
    assert r["capital_status"] == "PAPER_SHADOW_ONLY"
    assert r["economic_or_evidence_gaps"] == ["positive_forward_edge"]


def test_a_cash_governor_is_never_capital_ready():
    red = green_red()
    red["completion_readiness"]["governors"]["SAME_VENUE_ARB"] = "CASH"
    r = ev(red=red)
    assert r["pm_state"] == "YELLOW"
    assert "profitability_governor_positive" in r["economic_or_evidence_gaps"]


def test_twin_without_comparisons_is_not_certified():
    red = green_red()
    red["controls"]["DIGITAL_TWIN"]["evidence"] = {"compared": 0}
    r = ev(red=red)
    assert "digital_twin_certified" in r["economic_or_evidence_gaps"]
    assert "twin_compared" in r["fields_without_machine_evidence"]


def test_kalshi_not_current_blocks_only_kalshi_gate():
    red = green_red()
    red["controls"]["VENUE_HEALTH"]["evidence"]["venues"]["KALSHI"] = {
        "green": False, "denominator": 0}
    r = ev(red=red)
    assert r["critical_failures"] == []
    assert r["economic_or_evidence_gaps"] == ["kalshi_market_data_live"]


def test_collect_never_mutates_its_inputs():
    red, board, rel = green_red(), green_board(), green_release()
    before = copy.deepcopy((red, board, rel))
    PA.evaluate(red=red, scoreboard=board, release=rel, now=NOW)
    assert (red, board, rel) == before


def test_workers_on_another_commit_is_not_deployed():
    rel = green_release()
    rel["blockers"] = '["WORKERS_NOT_ON_RELEASE_SHA:088af82d7007"]'
    r = ev(rel=rel)
    assert "exact_sha" in r["critical_failures"]
    assert "workers" in r["provenance"]["deployed_sha"]["source"]


def test_release_endpoint_refuses_a_sha_the_api_is_not_serving():
    from sportsassets.api import command_red_team as CR
    body = dict(green_release(), deployed_sha=SHA)
    assert CR.validate_release(body, running_sha=SHA) == []
    assert "DEPLOYED_SHA_IS_NOT_THIS_API" in CR.validate_release(
        body, running_sha="d" * 40)
    assert CR.validate_release({}, running_sha=SHA)[0].startswith(
        "MISSING:")
    bad = dict(body, tested_sha="abc")
    assert "NOT_A_FULL_SHA:tested_sha" in CR.validate_release(
        bad, running_sha=SHA)


def test_red_team_reuses_only_a_fresh_ok_completion_read():
    from sportsassets.api import command_completion_readiness as CCR
    from sportsassets.api import command_red_team as CR
    CCR._CACHE.clear()
    assert CR.cached_completion(NOW) is None
    CCR._CACHE["main"] = (NOW - 10, {"status": "OK", "data": {"as_of": 1}})
    assert CR.cached_completion(NOW) == {"as_of": 1}
    CCR._CACHE["main"] = (NOW - CCR.CACHE_S - 1, {"status": "OK",
                                                  "data": {"as_of": 1}})
    assert CR.cached_completion(NOW) is None
    CCR._CACHE["main"] = (NOW - 10, {"status": "UNAVAILABLE", "data": None})
    assert CR.cached_completion(NOW) is None
    CCR._CACHE.clear()


# ── the two receipts (PM review of RC4) ─────────────────────────────────

def test_the_api_read_without_receipts_never_certifies_paper_or_runtime():
    """The API's own pm-acceptance read has neither receipt: both critical
    gates fail with named reasons, never a typed True or a process age."""
    r = ev(runtime=None, paper=None)
    assert r["pm_state"] == "RED"
    assert r["critical_failures"] == ["no_oom", "historical_paper_immutable"]
    assert "historical_paper_immutable" not in r["evidence_input"]
    assert "no_oom_minutes" not in r["evidence_input"]
    assert r["runtime_window_status"] == PA.UNKNOWN
    assert r["paper_history_status"] == PA.UNPROVEN


def test_the_workers_process_age_is_never_no_oom_minutes():
    """RC4: minutes_since_process_start 76.7 passed no_oom while the market
    plane had been oomKilled twice. A long-lived worker is not a window."""
    red = green_red()
    red["completion"]["runtime"]["shared_workers"][
        "minutes_since_process_start"] = 10_000
    r = ev(red=red, runtime=None)
    assert "no_oom_minutes" not in r["evidence_input"]
    assert "no_oom" in r["critical_failures"]


def test_green_receipts_bind_both_fields_with_their_sources():
    r = ev()
    assert r["evidence_input"]["no_oom_minutes"] == 75.0
    assert r["evidence_input"]["historical_paper_immutable"] is True
    assert "Render runtime receipt" in r["provenance"]["no_oom_minutes"][
        "source"]
    assert F.CUTOFF in r["provenance"]["historical_paper_immutable"][
        "source"]
    assert r["unproven"] == {}


def test_a_changed_paper_history_is_red_false_not_unproven():
    paper = F.paper_receipt()
    paper["post"]["tables"]["paper_ledger"]["digest"] = "0" * 32
    r = ev(paper=paper)
    assert r["evidence_input"]["historical_paper_immutable"] is False
    assert "historical_paper_immutable" in r["critical_failures"]
    assert r["paper_history_status"] == PA.CHANGED


def test_an_oom_in_the_window_is_red_through_the_binder():
    raw = F.add_event(F.render_raw(), "sportsassets-market-plane",
                      F.oom(F.SIDS["sportsassets-market-plane"],
                            F.LIVE + 1800))
    r = ev(runtime=raw)
    assert r["evidence_input"]["no_oom_minutes"] == 0.0
    assert r["pm_state"] == "RED" and "no_oom" in r["critical_failures"]


# ── a new receipt supersedes an earlier false one BY REFERENCE ───────────

D088 = "08828d04766017e520ffd4a34740162a2c792d36"
A91 = "a91be09f125f0ab0d1f29a0d0866b5cb6f5765fd"


def test_supersedes_must_name_receipt_ids():
    from sportsassets.api import command_red_team as CR
    assert CR.validate_supersedes(None) == ([], [])
    assert CR.validate_supersedes(["rel:08828d047660:2", "rel:08828d047660:1",
                                   "rel:08828d047660:2"]) == (
        ["rel:08828d047660:1", "rel:08828d047660:2"], [])
    for bad in ("rel:08828d047660:1", ["08828d04"], ["rel:XYZ:1"],
                [1], ["rel:08828d047660:1"] * 21 + ["rel:08828d047661:1"]):
        assert CR.validate_supersedes(bad)[1] == [
            "NOT_A_RECEIPT_ID:supersedes"], bad


async def test_a_new_receipt_supersedes_the_false_one_and_never_touches_it(
        monkeypatch):
    import os

    import asyncpg
    import pytest
    from fastapi import HTTPException

    from sportsassets.api import command_red_team as CR
    dsn = os.environ.get("RN1X_TEST_DSN", "")
    if not dsn:
        pytest.skip("needs RN1X_TEST_DSN")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)

    async def _p():
        return pool
    monkeypatch.setattr(CR, "_pool", _p)
    count = ("SELECT (SELECT count(*) FROM red_team_release_receipts) + "
             " (SELECT count(*) FROM red_team_control_receipts)")
    try:
        # the earlier FALSE receipt: 08828d04 deployed while
        # claude/release-api was at a91be09f -> green=false
        monkeypatch.setenv("RENDER_GIT_COMMIT", D088)
        old = await CR.release_receipt(dict(
            green_release(), tested_sha=D088, release_sha=A91,
            deployed_sha=D088, workers_deployed_sha=D088))
        assert old["release_gate"]["green"] is False
        async with pool.acquire() as conn:
            before = dict(await conn.fetchrow(
                "SELECT * FROM red_team_release_receipts WHERE "
                " receipt_id = $1", old["receipt_id"]))
            n0 = await conn.fetchval(count)
        monkeypatch.setenv("RENDER_GIT_COMMIT", SHA)
        body = dict(green_release(), deployed_sha=SHA,
                    workers_deployed_sha=SHA,
                    migration_fingerprint_match=True)
        # an unknown or malformed reference appends NOTHING
        for sup, why in ((["rel:ffffffffffff:1"],
                          "SUPERSEDED_RECEIPT_NOT_FOUND:rel:ffffffffffff:1"),
                         ("rel:08828d047660:1",
                          "NOT_A_RECEIPT_ID:supersedes")):
            with pytest.raises(HTTPException) as e:
                await CR.release_receipt(dict(body, supersedes=sup))
            assert e.value.status_code == 422
            assert why in e.value.detail["refused"]
        async with pool.acquire() as conn:
            assert await conn.fetchval(count) == n0
        new = await CR.release_receipt(dict(body,
                                            supersedes=[old["receipt_id"]]))
        assert new["release_gate"]["green"] is True
        assert new["supersedes"] == [old["receipt_id"]]
        async with pool.acquire() as conn:
            after = dict(await conn.fetchrow(
                "SELECT * FROM red_team_release_receipts WHERE "
                " receipt_id = $1", old["receipt_id"]))
            assert after == before                  # untouched, still there
            ref = await conn.fetchrow(
                "SELECT control, status, evidence FROM "
                " red_team_control_receipts WHERE receipt_id = $1",
                "sup:%s" % new["receipt_id"])
            assert ref["control"] == "RELEASE_RECEIPT_SUPERSEDES"
            assert json.loads(ref["evidence"]) == {
                "receipt_id": new["receipt_id"],
                "supersedes": [old["receipt_id"]], "deployed_sha": SHA,
                "by_reference_only": True,
                "earlier_receipts_unchanged": True}
            # and neither the false receipt nor the reference can be erased
            for q, k in (("DELETE FROM red_team_release_receipts WHERE "
                          " receipt_id = $1", old["receipt_id"]),
                         ("UPDATE red_team_release_receipts SET blockers = "
                          " '[]'::jsonb WHERE receipt_id = $1",
                          old["receipt_id"]),
                         ("DELETE FROM red_team_control_receipts WHERE "
                          " receipt_id = $1", "sup:%s" % new["receipt_id"])):
                with pytest.raises(asyncpg.PostgresError):
                    await conn.execute(q, k)
    finally:
        await pool.close()
