"""BETTOR_EV_SHADOW IS THE PRIMARY LANE, and these pin what that means.

Owner clarification 2026-09-19: "BETTOR EV ENGINE IS THE PRIMARY
PRODUCT. RN1_SHADOW is a secondary benchmark/research lane." /
"BETTOR_EV_SHADOW does NOT need P_BETTOR to begin accumulating
prospective evidence." / "Do not use RN1 to make the independent lane
look active."

The most load-bearing tests here are the independence ones. The
temptation this lane will face, repeatedly, is to borrow RN1's signal
to make itself look alive -- and the directive names that explicitly.
So the wall is checked three ways: the declared provenance, the writer's
refusal, and the table's own CHECK.
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import shadow as sh
from sportsassets import shadow_bettor as bettor
from sportsassets import shadow_lanes as lanes
from sportsassets import shadow_store as store
from sportsassets.api import command_shadow as CS

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
BETTOR_SRC = (BACKEND / "sportsassets" / "shadow_bettor.py").read_text()
WORKER_SRC = (BACKEND / "sportsassets" / "workers"
              / "shadow_bettor.py").read_text()
MIGRATION = (BACKEND / "migrations"
             / "071_bettor_opportunities.sql").read_text()
SHADOW_JS = (ROOT / "frontend" / "public" / "command"
             / "shadow.js").read_text()

NOW = datetime(2026, 9, 19, 18, 30, 0, tzinfo=timezone.utc)

MUTATING = (r"\bUPDATE\s+\w+\s+SET\b", r"\bDELETE\s+FROM\b",
            r"\bTRUNCATE\b", r"\bDROP\s+TABLE\b",
            r"\bON\s+CONFLICT[^;]*DO\s+UPDATE\b")


def _book(**over):
    fields = dict(captured_at=NOW, symbol="aec-nfl-x",
                  evidence_source="PMUS_BBO", bid=0.48, ask=0.52)
    fields.update(over)
    return store.market_state_record(**fields)


def _op(**over):
    fields = dict(symbol="aec-nfl-x", observed_at=NOW, outcome_leg="HOME",
                  evidence_source="PMUS_BBO", market_state=_book(),
                  cadence_s=300)
    fields.update(over)
    return bettor.opportunity_record(**fields)


# ── the independence wall ────────────────────────────────────────────


def test_the_bettor_lane_never_reads_rn1():
    """Checked in the source, because the wall has to survive somebody
    reaching for the nearest available signal on a quiet afternoon."""
    # Identifiers and table accesses, not English: this module's own
    # prose talks about which blockers prevent the most TRADES, and a
    # test that tripped on that would be switched off within a week.
    for forbidden in ("rn1_observations", "shadow_rn1", "RN1_ACTION",
                      "RN1_ACCOUNT_IDENTITY", "RN1_DERIVED_TARGET",
                      "rn1_price", "mirror_books"):
        assert forbidden not in BETTOR_SRC, forbidden
    for table in ("trades", "mirror_books", "rn1_observations",
                  "shadow_decisions"):
        assert not re.search(r"\bFROM\s+%s\b" % table, BETTOR_SRC,
                             re.IGNORECASE), table
        assert not re.search(r"\bJOIN\s+%s\b" % table, BETTOR_SRC,
                             re.IGNORECASE), table
    for forbidden in ("rn1_observations", "shadow_rn1", "mirror_books"):
        assert forbidden not in WORKER_SRC, forbidden


def test_an_opportunity_declares_only_independent_provenance():
    op = _op()
    assert op["rn1FeaturesUsed"] is False
    for provenance in op["featureLineage"].values():
        assert provenance in lanes.INDEPENDENT_PROVENANCES, provenance


def test_the_table_refuses_an_rn1_derived_opportunity():
    """Not a runtime refusal -- a database CHECK. A policy the schema
    permits is not a policy."""
    assert "bettor_opportunity_independent" in MIGRATION
    assert "CHECK (rn1_features_used IS FALSE)" in MIGRATION


def test_every_bettor_decision_descends_from_an_opportunity():
    assert "shadow_decisions_bettor_observed" in MIGRATION
    assert "bettor_opportunity_id IS NOT NULL" in MIGRATION


# ── collecting without P_BETTOR ──────────────────────────────────────


def test_bettor_collects_without_p_bettor_and_says_so():
    d = bettor.decide(_op(), _book())
    assert d["lane"] == lanes.BETTOR_EV_SHADOW
    assert d["proposedAction"] == sh.NO_TRADE
    assert d["pBettor"] is None
    assert d["pBettorStatus"] == lanes.NOT_ESTABLISHED
    assert d["informationEv"] is None


def test_the_first_blocker_is_always_the_true_one():
    """A dataset that hid INDEPENDENT_EV_NOT_ESTABLISHED behind a
    narrower complaint would misreport what is actually holding the
    engine."""
    codes = [b["code"] for b in bettor.decide(_op(), _book())["blockers"]]
    assert codes[0] == bettor.B_EV_NOT_ESTABLISHED


def test_a_no_trade_carries_its_reasons():
    d = bettor.decide(_op(), _book())
    assert d["reasonCodes"], "a NO_TRADE with no reason is unauditable"
    assert set(d["reasonCodes"]) <= set(bettor.BLOCKERS)


def test_there_is_no_path_that_produces_a_trade_today():
    """Not an oversight. There is no validated independent EV to act on,
    and a lane that traded anyway would manufacture the very claim the
    dataset exists to test."""
    for state in (_book(), _book(bid=0.01, ask=0.99), None):
        d = bettor.decide(_op(market_state=state), state)
        assert d["proposedAction"] == sh.NO_TRADE


def test_an_unreadable_book_is_a_named_blocker_not_a_skip():
    d = bettor.decide(_op(market_state=None), None)
    codes = [b["code"] for b in d["blockers"]]
    assert bettor.B_MARKET_STATE_UNREADABLE in codes


def test_depth_absent_from_a_bbo_is_reported_not_invented():
    op = _op()
    assert op["microstructure"]["depth"] == bettor.NOT_IDENTIFIED
    codes = [b["code"] for b in bettor.decide(op, _book())["blockers"]]
    assert bettor.B_INSUFFICIENT_DEPTH in codes


def test_a_wide_spread_is_named():
    wide = _book(bid=0.30, ask=0.70)
    codes = [b["code"]
             for b in bettor.decide(_op(market_state=wide), wide)["blockers"]]
    assert bettor.B_SPREAD_TOO_WIDE in codes


# ── the dataset's own integrity ──────────────────────────────────────


def test_the_selection_rule_travels_with_every_row():
    op = _op()
    assert op["universeVersion"] == bettor.UNIVERSE_VERSION
    assert op["universeSource"]
    assert op["selectionReason"]


def test_the_evidence_source_is_required():
    with pytest.raises(store.StoreRefusal) as exc:
        _op(evidence_source="")
    assert "EVIDENCE_SOURCE" in str(exc.value)


def test_one_row_per_market_per_cadence_bucket():
    """Without a bucket the dataset records how often the loop ran
    rather than what the market did."""
    a = _op(observed_at=NOW)
    b = _op(observed_at=NOW + timedelta(seconds=30))
    assert a["bettorOpportunityId"] == b["bettorOpportunityId"]
    c = _op(observed_at=NOW + timedelta(seconds=600))
    assert a["bettorOpportunityId"] != c["bettorOpportunityId"]


def test_a_different_market_is_a_different_row():
    assert _op()["bettorOpportunityId"] != \
        _op(symbol="aec-nfl-y")["bettorOpportunityId"]


def test_a_different_evidence_source_is_a_different_row():
    """Two sources looking at one market at one instant are two
    observations, not one. When institutional L2 arrives beside the
    retail book this is what keeps them separable."""
    assert _op()["bettorOpportunityId"] != \
        _op(evidence_source="INSTITUTIONAL_L2")["bettorOpportunityId"]


@pytest.mark.parametrize("pattern", MUTATING)
def test_the_bettor_writer_and_worker_only_insert(pattern):
    for name, src in (("shadow_bettor.py", BETTOR_SRC),
                      ("workers/shadow_bettor.py", WORKER_SRC)):
        assert not re.search(pattern, src, re.IGNORECASE), name


def test_the_bettor_worker_has_no_order_path():
    for forbidden in ("create_order", "place_order", "submit_order",
                      "cancel_order", "ORDER_INTENT", "live_executor"):
        assert forbidden not in WORKER_SRC, forbidden


# ── COMMAND leads with BETTOR ────────────────────────────────────────


def test_the_api_declares_bettor_primary():
    env = CS.environment()
    assert env["identity"] == "BETTOR EV ENGINE"
    assert env["primaryLane"] == lanes.BETTOR_EV_SHADOW
    assert env["benchmarkLane"] == lanes.RN1_SHADOW
    assert env["laneOrder"][0] == lanes.BETTOR_EV_SHADOW
    assert "PRIMARY" in env["laneRoles"][lanes.BETTOR_EV_SHADOW]
    assert "BENCHMARK" in env["laneRoles"][lanes.RN1_SHADOW]


def test_the_hybrid_lane_exists_only_as_not_yet_active():
    hybrid = CS.environment()["hybridLane"]
    assert hybrid["name"] == "RN1_PLUS_BETTOR"
    assert hybrid["state"] == "NOT_YET_ACTIVE"


def test_the_ui_takes_its_lane_order_from_the_server():
    """A front end that hard-coded the order would drift from the API
    the first time the hierarchy changed."""
    assert "environment.laneOrder" in SHADOW_JS
    assert "environment.primaryLane" in SHADOW_JS


def test_the_ui_leads_with_bettor_in_its_own_fallback():
    fallback = re.search(r"laneOrder\)\s*\n?\s*\|\|\s*\[([^\]]+)\]",
                         SHADOW_JS)
    assert fallback, "no fallback order found"
    assert fallback.group(1).index("BETTOR_EV_SHADOW") < \
        fallback.group(1).index("RN1_SHADOW")


def test_the_kpi_row_leads_with_bettor():
    kpi = SHADOW_JS[SHADOW_JS.index("const KPI = ["):]
    kpi = kpi[:kpi.index("];")]
    assert kpi.index("bettorEvDecisions") < kpi.index("rn1SignalsObserved")


def test_the_page_is_titled_for_the_product_not_the_benchmark():
    head = SHADOW_JS[SHADOW_JS.index("function shell()"):]
    head = head[:head.index("function paint")]
    assert "BETTOR EV Engine" in head
    assert "<h1>Shadow</h1>" not in head


def test_the_rn1_blocker_panel_is_labelled_as_the_benchmark():
    assert "Why BETTOR said no" in SHADOW_JS
    assert "Why the RN1 benchmark said no" in SHADOW_JS


def test_a_missing_bettor_store_does_not_503_the_whole_screen():
    """A staged rollout is not an outage, and must not be drawn as one."""
    assert "STORE_NOT_READY" in (
        BACKEND / "sportsassets" / "api" / "command_shadow.py").read_text()
    assert "_bettor_counts" in (
        BACKEND / "sportsassets" / "api" / "command_shadow.py").read_text()
