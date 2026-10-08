"""PM Evidence Pack component 1 as a RELEASE GATE: every frozen golden case
runs through BETTOR's production mapping / claim / routing / arbitrage code
(pm_bind.golden), and a production behavior that differs from the frozen
expectation turns the gate red. Required CI (capital-critical list)."""
from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from sportsassets import canonical_claims as CC
from sportsassets.canonical_venue import mapping_contract as MC
from sportsassets.pm_bind import golden as G

DATA = pathlib.Path(G.__file__).resolve().parents[1] / "pm_evidence" / "data"
KALSHI_CASES = ("G10_KALSHI_TWO_WAY_YES_NO_ALIAS",
                "G11_KALSHI_TWO_WAY_OPPOSITE_YES",
                "G12_THREE_WAY_FALSE_NO_ALIAS", "G17_SAME_VENUE_ARB")


def test_every_frozen_case_passes_on_the_production_path():
    r = G.receipt()
    assert r["green"] is True
    assert (r["passed"], r["total"]) == (18, 18)
    assert (r["appended_passed"], r["appended_total"]) == (6, 6)
    assert {c["result"] for c in r["cases"]} == {"PASS"}
    assert {c["result"] for c in r["appended_source_records"]} == {"PASS"}
    assert r["path"].startswith("PRODUCTION")


def test_no_case_is_unbound_or_relabelled():
    r = G.receipt()
    by = {c["id"]: c for c in r["cases"]}
    for cid in KALSHI_CASES:
        assert by[cid]["source_kind"] == "FROZEN_INTEGRATION_CASE", cid
    frozen = json.loads((DATA / "golden_cases.json").read_text())
    assert [c["id"] for c in frozen["cases"]] == list(by)


def test_appended_records_never_edit_the_frozen_file():
    app = json.loads((DATA / "golden_cases_kalshi_rep_v1.json").read_text())
    frozen = json.loads((DATA / "golden_cases.json").read_text())
    assert not {c["id"] for c in app["cases"]} & {
        c["id"] for c in frozen["cases"]}
    assert {s["kind"] for s in app["source_manifest"]} == {
        "KALSHI_REP_CONFIRMATION"}
    pack = (pathlib.Path(__file__).resolve().parents[2] / "research" /
            "pm_evidence_acceptance" / "BETTOR_PM_EVIDENCE_ACCEPTANCE_PACK_V1")
    src = next(pack.rglob("golden_cases.json"))
    assert hashlib.sha256(src.read_bytes()).hexdigest() == hashlib.sha256(
        (DATA / "golden_cases.json").read_bytes()).hexdigest()


def test_a_router_that_picks_the_dearest_route_fails_the_gate(monkeypatch):
    real = CC.route_claim

    def dearest(*a, **k):
        out = real(*a, **k)
        if out.get("runner_up"):
            out = dict(out, best_single=out["runner_up"])
        return out
    monkeypatch.setattr(CC, "route_claim", dearest)
    r = G.receipt()
    assert r["green"] is False
    failed = {c["id"] for c in r["cases"] if not c["pass"]}
    assert "G14_BEST_PRICE_USES_OPPONENT_NO" in failed


def test_a_mapper_that_ignores_identity_fails_the_gate(monkeypatch):
    def lax(ident, cand, **_k):
        return MC.MappingDecision("ESTABLISHED", ())
    monkeypatch.setattr(MC, "map_fixture_exact", lax)
    r = G.receipt()
    failed = {c["id"] for c in r["cases"] if not c["pass"]}
    assert {"G02_NFL_WRONG_START", "G03_NFL_WRONG_TEAM"} <= failed
    assert r["green"] is False


def test_same_market_yes_no_treated_as_two_routes_fails_the_gate(
        monkeypatch):
    monkeypatch.setattr(CC, "same_market", lambda a, b: False)
    from sportsassets.agents import adriana_claims as AC
    if hasattr(AC, "same_market"):
        monkeypatch.setattr(AC, "same_market", lambda a, b: False)
    r = G.receipt()
    app = {c["id"]: c for c in r["appended_source_records"]}
    assert app["KR01_SAME_MARKET_YES_NO_NEVER_ARB"]["pass"] is False
    assert r["green"] is False


@pytest.mark.parametrize("cid", ["G13_UNKNOWN_SETTLEMENT_REFUSES",
                                 "G16_STALE_CHEAP_ROUTE_LOSES"])
def test_fail_closed_cases_stay_fail_closed(cid):
    by = {c["id"]: c for c in G.receipt()["cases"]}
    assert by[cid]["pass"] is True
