"""PM Evidence Pack component 3 bound: the harness (byte-identical package)
decides RED / YELLOW / GREEN from MACHINE evidence collected field by field
(pm_bind.acceptance). Missing evidence is never success; a status narrative
cannot be typed in; nothing here activates money."""
from __future__ import annotations

import copy

from sportsassets.pm_bind import acceptance as PA

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
            "market_data": {"fresh": 812, "subscription_mode": "ALL"},
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


def ev(red=None, board=None, rel="DEFAULT"):
    return PA.evaluate(red=green_red() if red is None else red,
                       scoreboard=green_board() if board is None else board,
                       release=green_release() if rel == "DEFAULT" else rel,
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
    assert set(r["evidence_input"]) == {"historical_paper_immutable",
                                        "live_authority_shadow"}
    assert r["evidence_input"]["live_authority_shadow"] is False


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
