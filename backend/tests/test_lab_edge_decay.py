"""LAB-A EDGE DECAY & LATENCY ECONOMICS -- the pure core (no database).

  * the lab package's reference/edge_decay.py is vendored BYTE FOR BYTE and
    its own reference tests hold against the vendored copy;
  * the qualified-opportunity rule is the strategy's own pre-trade
    qualification, its refusal set pinned to the decision code's constants;
  * the executable economics reproduce the paper path's rules (the
    simulator's level filter, the deployed fee schedule) -- pinned equal;
  * horizons use only recorded books inside their window (no carry-forward,
    no interpolation), a probability fresh by the lane's 30 s rule (never
    loosened), and the one point-in-time accessor (a row known after the
    instant is invisible; a missing stamp is never visible);
  * decay times keep their bounds and censoring; Karen / Allie / Eddie are
    UNAVAILABLE historically and consumed forward when stamped;
  * the fast-lane dependency graph is DERIVED from the component source.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from sportsassets.lab import edge_decay as ED
from sportsassets.lab import edge_decay_reads as R
from sportsassets.lab import fastlane as FL
from sportsassets.lab import pit as PIT
from sportsassets.lab import stats as ST
from sportsassets.lab.reference import edge_decay as REF

ROOT = pathlib.Path(__file__).resolve().parents[1]
#: sha256 of BETTOR_INDEPENDENT_PROFITABILITY_LAB/reference/edge_decay.py
#: (package sha256 548161b4...3fed1f) -- vendored unchanged
REFERENCE_SHA256 = ("babaab55b0d8dfec67e31fd483e904d141b78ea0158e5d51515321dd0"
                    "aaee875")
T0 = 1_791_000_000.0
LONG = "ORDER_INTENT_BUY_LONG"


def flat_fee(per):
    return lambda q, px, at=None: (round(float(q) * per, 6), "FLAT_TEST_FEE")


ZERO = flat_fee(0.0)


# ─────────────────────────── the vendored reference ────────────────────

def test_the_reference_is_vendored_byte_for_byte():
    body = (ROOT / "sportsassets" / "lab" / "reference" /
            "edge_decay.py").read_bytes()
    assert hashlib.sha256(body).hexdigest() == REFERENCE_SHA256


def test_the_reference_tests_hold_against_the_vendored_copy():
    # the package's tests/test_reference.py, verbatim assertions
    s = [(0, 0.04), (5, 0.035), (10, 0.02), (15, 0.01)]
    assert REF.edge_half_life(s) == 10
    assert REF.time_to_zero([(0, .02), (3, .01), (7, -.001)]) == 7
    assert abs(REF.pipeline_loss(120, 80) - 40) < 1e-9


def test_decay_uses_the_reference_and_keeps_bounds_and_censoring():
    d = ED.decay([(0, 0.04), (5, 0.035), (10, 0.02), (15, 0.01)])
    assert d["status"] == "MEASURED"
    assert d["half_life"] == {"status": "CROSSED", "upper_s": 10.0,
                              "lower_s": 5.0}
    assert d["t75"] == {"status": "CROSSED", "upper_s": 10.0, "lower_s": 5.0}
    assert d["time_to_zero"] == {"status": "RIGHT_CENSORED",
                                 "at_least_s": 15.0}
    assert ED.decay([(0, 0.02)])["status"] == "UNOBSERVED_AFTER_DETECTION"
    assert ED.decay([(0, -0.01), (5, 0.0)])["why"] == \
        "INITIAL_EDGE_NOT_POSITIVE"
    assert ED.decay([(5, 0.01)])["why"] == "NO_INITIAL_EDGE"


# ─────────────────────────── qualification ─────────────────────────────

def _d(**kw):
    base = {"decision_id": "papercg:x", "strategy": ED.CG_STRATEGY,
            "verdict": "REFUSE", "refusals": ["BELOW_MIN_GROSS_EDGE"],
            "book_obs_id": 1, "economics": {"best_level_edge_pp": 0.4}}
    base.update(kw)
    return base


def test_the_qualified_rows_are_exactly_the_strategy_qualification():
    assert ED.qualify(_d(verdict="ENTER", refusals=[]))["class"] == ED.ENTER
    assert ED.qualify(_d())["class"] == ED.NEAR_MISS
    assert ED.qualify(_d(refusals=["NET_EV_NOT_POSITIVE_AFTER_FEES"]))[
        "class"] == ED.NEAR_MISS
    # a non-positive gross edge is not a near miss
    q = ED.qualify(_d(economics={"best_level_edge_pp": -0.2}))
    assert q["class"] is None and "WITHOUT_POSITIVE" in q["why"]
    # any non-economic refusal disqualifies
    q = ED.qualify(_d(refusals=["BELOW_MIN_GROSS_EDGE",
                                "PROBABILITY_EVIDENCE_STALE"]))
    assert q["class"] is None and "PROBABILITY_EVIDENCE_STALE" in q["why"]
    # no book read: the strategy never priced an executable book
    assert ED.qualify(_d(book_obs_id=None))["why"] == \
        "NO_BOOK_READ_BY_THE_STRATEGY"
    assert ED.qualify(_d(strategy=ED.DEREK_V2))["why"] == \
        "NOT_MEASURABLE_BLENDED_FAIR_VALUE"
    assert ED.qualify(_d(strategy="PINNACLE_EXPLORATION_PAPER"))["why"] == \
        "NOT_AN_INVESTMENT_STRATEGY"


def test_the_refusal_and_sleeve_sets_are_the_decision_codes_own():
    from sportsassets import canonical_intent as CI
    from sportsassets.agents import derek_policy as DP
    from sportsassets.agents import paper_benchmark as PB
    assert ED.ECONOMIC_REFUSALS == {DP.R_BELOW, PB.R_FEES_CONSUME_EDGE,
                                    DP.R_NET, DP.R_NO_QTY}
    assert PB.R_EDGE == DP.R_BELOW and PB.R_NET == DP.R_NET
    assert set(ED.INVESTMENT_STRATEGIES) == {
        s for s, v in CI.STRATEGY_SLEEVE.items() if v == CI.INVESTMENT}
    assert ED.CG_STRATEGY == PB.CG_STRATEGY


def test_the_rule_constants_are_the_lanes_own_and_never_loosened():
    from sportsassets import bettor_paper_session as S
    from sportsassets.workers import ext_pinnacle_loop as LOOP
    assert ED.PROBABILITY_LIMIT_S == float(LOOP.PINNACLE_MAX_AGE_S) == 30.0
    cfg = S.default_config()
    assert ED.SIM_DELAY_S == cfg["simulator"]["decision_to_execution_delay_s"]
    assert ED.SIM_TTL_S == cfg["simulator"]["marketable_ttl_s"]
    # a recorded limit larger than the lane's rule is never used
    assert ED.limit_of({"pinnacle": {"limit_s": 45.0}}) == 30.0
    assert ED.limit_of({"pinnacle": {"limit_s": 20.0}}) == 20.0
    assert ED.limit_of({"pinnacle": {}}) == 30.0


# ─────────────────────────── the paper path's rules ────────────────────

def _md(bids=(), offers=()):
    return {"bids": [{"px": {"value": "%.3f" % p}, "qty": str(q)}
                     for p, q in bids],
            "offers": [{"px": {"value": "%.3f" % p}, "qty": str(q)}
                       for p, q in offers]}


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_levels_are_the_paper_simulators_levels(side):
    from sportsassets import bettor_paper_simulator as SIM
    md = _md(bids=[(0.48, 100), (0.475, 50), (0.47, 30)],
             offers=[(0.50, 20), (0.505, 9), (0.52, 40)])
    sim = SIM.levels_for(md, direction="BUY", holding_side=side)["levels"]
    lab = ED.to_levels(ED.side_of(md, side), side)
    assert [(x["price"], x["wire"], x["qty"]) for x in lab] == \
        [(x["price"], x["wire"], x["qty"]) for x in sim]
    # the extraction's [px, qty] pairs read the same
    pairs = [[e["px"]["value"], e["qty"]] for e in ED.side_of(md, side)]
    assert ED.to_levels(pairs, side) == lab


def test_the_fee_is_the_deployed_schedule_the_paper_ledger_charges():
    from sportsassets import bettor_paper_ledger as L
    for px in (0.07, 0.35, 0.5, 0.61, 0.96):
        for q in (1, 37, 1587, 10000):
            at = T0 + 3600
            assert ED.fee(None, q, px, at) == float(L._fee(None, q, px, at))


def test_executable_economics_on_one_book():
    lv = ED.to_levels([["0.50", "100"], ["0.52", "100"], ["0.60", "1000"]],
                      "LONG")
    e = ED.top_net_edge(lv, p=0.56, at=T0, fee_fn=flat_fee(0.01))
    assert e["edge"] == pytest.approx(0.05)
    o = ED.order_ev(lv, p=0.56, limit=0.52, qty=150, at=T0,
                    fee_fn=flat_fee(0.01))
    # 100 @ .50 + 50 @ .52 within the limit; fees 1.5
    assert o["filled_qty"] == 150 and o["complete"]
    assert o["ev_usd"] == pytest.approx(100 * 0.06 + 50 * 0.04 - 1.5)
    # nothing within the limit is a MEASURED zero, not a missing value
    z = ED.order_ev(lv, p=0.56, limit=0.49, qty=150, at=T0, fee_fn=ZERO)
    assert z["status"] == "MEASURED" and z["ev_usd"] == 0 and \
        z["filled_qty"] == 0
    # an empty consumed side is a measured zero edge
    em = ED.top_net_edge([], p=0.56, at=T0, fee_fn=ZERO)
    assert em["edge"] == 0.0 and em["empty_side"]
    assert ED.top_net_edge(lv, p=None, at=T0)["status"] == "UNAVAILABLE"


# ─────────────────────────── one opportunity ───────────────────────────

def _rec(*, books=(), vals=(), age=5.0, verdict="ENTER", qty=100,
         limit=0.52, b0_lag=0.4, limit_s=30.0):
    """A record shaped like the production extraction's line."""
    return {
        "d": {"decision_id": "papercg:t", "strategy": ED.CG_STRATEGY,
              "policy_version": "PINNACLE_COMPLETED_GAME_PAPER_V3",
              "verdict": verdict, "refusals": [] if verdict == "ENTER"
              else ["BELOW_MIN_GROSS_EDGE"],
              "decided_at": T0, "recorded_at": T0,
              "us_market_slug": "s", "holding_side": "LONG",
              "intent": LONG, "fixture": "fx", "valuation_id": 7,
              "p_pinnacle": 0.56, "proposed_qty": qty if verdict == "ENTER"
              else None, "limit_price": limit if verdict == "ENTER" else None,
              "book_obs_id": 1, "label": {"competition": "MLB",
                                          "market_type": "MONEYLINE"},
              "economics": {"best_level_edge_pp": 6.0,
                            "expected_net_profit_usd": 6.0 - 1.0},
              "pinnacle": {"at": T0 - age, "received_at": T0 - age + 0.5,
                           "valuation_decided_at": T0 - 0.05,
                           "limit_s": limit_s, "age_s": age}},
        "b0": {"obs_id": 1, "observed_at": T0 + b0_lag,
               "recorded_at": T0 - 0.01, "levels": [["0.50", "100"],
                                                    ["0.53", "50"]]},
        "v0": {"id": 7, "quote_context": "PRE_GAME"},
        "books": list(books), "vals": list(vals),
        "orders": ([{"order_type": "MARKETABLE", "time_in_force": "IOC",
                     "eligible_at": T0 + 2.0, "expires_at": T0 + 90.0}]
                   if verdict == "ENTER" else []), "fills": []}


def test_detection_is_the_latest_of_the_decision_and_its_book():
    r = ED.evaluate(_rec(), fee_fn=flat_fee(0.01))
    assert r["t0"] == pytest.approx(T0 + 0.4)     # the book arrived later
    assert r["horizons"][0]["status"] == "MEASURED"
    assert r["initial"]["decided_order"]["ev_usd"] == pytest.approx(5.0)
    assert r["initial"]["reconciliation_usd"] == pytest.approx(0.0)


def test_horizons_use_only_books_inside_their_window_never_carried():
    t0 = T0 + 0.4
    books = [[2, t0 + 0.8, t0 + 0.8, [["0.51", "100"]]],     # (0, 1]
             [3, t0 + 4.0, t0 + 4.1, [["0.53", "100"]]]]     # (2, 5]
    r = ED.evaluate(_rec(books=books), fee_fn=ZERO)
    by = {h["h_s"]: h for h in r["horizons"]}
    assert by[1]["status"] == "MEASURED" and by[1]["book_obs_id"] == 2
    # +2 s: the +1 s book is NOT carried forward
    assert by[2]["status"] == "UNAVAILABLE"
    assert by[2]["why"] == "NO_RECORDED_BOOK_IN_WINDOW"
    assert by[5]["book_obs_id"] == 3
    assert by[5]["window_s"] == [2.0, 5]
    # the per-contract edge at +5 s: p .56 - .53
    assert by[5]["TOP_NET_EDGE"]["edge"] == pytest.approx(0.03)
    assert by[5]["retention"] == pytest.approx(0.03 / 0.06)


def test_a_book_known_after_the_horizon_is_invisible_at_it():
    """ANTI-LOOKAHEAD: a row OBSERVED inside the window but RECORDED after
    the horizon instant is not evidence at that horizon."""
    t0 = T0 + 0.4
    future = [[9, t0 + 3.0, t0 + 3600.0, [["0.50", "100"]]]]
    r = ED.evaluate(_rec(books=future), fee_fn=ZERO)
    by = {h["h_s"]: h for h in r["horizons"]}
    assert by[5]["status"] == "UNAVAILABLE"
    assert by[5]["why"] == "NO_RECORDED_BOOK_IN_WINDOW"
    assert all(h.get("book_obs_id") != 9 for h in r["horizons"])
    # and a decay sample is taken only once the row is known (at 3600 s
    # after t0 -- beyond the 600 s horizon, so never)
    assert r["samples"]["fresh"] == [(0.0, pytest.approx(0.06))]
    assert r["samples"]["venue_at_p0"] == [(0.0, pytest.approx(0.06))]
    # the same row recorded promptly IS a sample, at its known instant
    prompt = [[9, t0 + 3.0, t0 + 3.25, [["0.50", "100"]]]]
    r2 = ED.evaluate(_rec(books=prompt), fee_fn=ZERO)
    assert r2["samples"]["venue_at_p0"][1][0] == pytest.approx(3.25)


def test_the_probability_must_be_fresh_at_the_horizon():
    t0 = T0 + 0.4
    books = [[2, t0 + 40.0, t0 + 40.0, [["0.50", "100"]]]]   # (30, 60]
    r = ED.evaluate(_rec(books=books, age=5.0), fee_fn=ZERO)
    h60 = {h["h_s"]: h for h in r["horizons"]}[60]
    # the detection probability is 45.4 s old at t0+60: STALE, unavailable
    assert h60["status"] == "UNAVAILABLE"
    assert h60["why"] == "PROBABILITY_NOT_FRESH_AT_HORIZON:STALE"
    # the venue decomposition is computed and labelled not executable
    assert h60["VENUE_BOOK_AT_DETECTION_P"]["label"] == \
        "NOT_EXECUTABLE_RESEARCH_DECOMPOSITION"
    # a later valuation, known by then and fresh, makes it executable
    vals = [[8, t0 + 30.0, t0 + 30.5, t0 + 31.0, 0.58]]
    r2 = ED.evaluate(_rec(books=books, vals=vals, age=5.0), fee_fn=ZERO)
    h60 = {h["h_s"]: h for h in r2["horizons"]}[60]
    assert h60["status"] == "MEASURED"
    assert h60["probability"]["valuation_id"] == 8
    assert h60["TOP_NET_EDGE"]["edge"] == pytest.approx(0.08)
    # a valuation decided AFTER the instant is never used at it
    late = [[8, t0 + 30.0, t0 + 30.5, t0 + 61.0, 0.58]]
    r3 = ED.evaluate(_rec(books=books, vals=late, age=5.0), fee_fn=ZERO)
    assert {h["h_s"]: h for h in r3["horizons"]}[60]["status"] == \
        "UNAVAILABLE"


def test_execution_loss_is_measured_on_the_first_book_after_eligible():
    t0 = T0 + 0.4
    books = [[2, T0 + 3.0, T0 + 3.0, [["0.52", "100"]]]]
    r = ED.evaluate(_rec(books=books), fee_fn=ZERO)
    ex = r["execution"]
    assert ex["status"] == "MEASURED" and ex["execution_book_obs_id"] == 2
    # detection: 100 @ .50 -> 6.00; execution: 100 @ .52 -> 4.00
    assert ex["ev_at_detection_usd"] == pytest.approx(6.0)
    assert ex["ev_at_execution_usd"] == pytest.approx(4.0)
    assert ex["ev_lost_usd"] == pytest.approx(2.0)
    assert ex["book_acquisition_after_eligible_s"] == pytest.approx(1.0)
    # no book in the order window: UNAVAILABLE, counted, never a zero loss
    r2 = ED.evaluate(_rec(books=[]), fee_fn=ZERO)
    assert r2["execution"]["status"] == "UNAVAILABLE"
    assert r2["execution"]["why"] == "NO_RECORDED_BOOK_IN_THE_ORDER_WINDOW"
    assert "ev_lost_usd" not in r2["execution"]
    assert t0 > T0


def test_a_near_miss_has_no_decided_order():
    r = ED.evaluate(_rec(verdict="REFUSE"), fee_fn=ZERO)
    assert r["qualification"]["class"] == ED.NEAR_MISS
    assert r["initial"]["decided_order"] is None
    assert "execution" not in r and "DECIDED_ORDER_EV" not in r["decay"]


# ─────────────────────────── the accessor ──────────────────────────────

def test_the_accessor_filters_masks_and_fails_closed():
    rows = [{"obs_id": 1, "observed_at": 10.0, "recorded_at": 10.0},
            {"obs_id": 2, "observed_at": 10.0, "recorded_at": 99.0},
            {"obs_id": 3, "observed_at": None, "recorded_at": 5.0}]
    vis = PIT.visible("paper_book_observations", rows, 20.0)
    assert [r["obs_id"] for r in vis] == [1]          # 2 later, 3 unstamped
    with pytest.raises(PIT.LookaheadViolation):
        PIT.post_check("paper_book_observations", rows[1:2], 20.0)
    with pytest.raises(PIT.LookaheadViolation):
        PIT.post_check("paper_book_observations", rows[2:3], 20.0)
    with pytest.raises(PIT.LookaheadViolation):
        PIT.visible("an_unregistered_table", rows, 20.0)
    o = PIT.visible("paper_orders", [{"decided_at": 1.0, "created_at": 1.0,
                                       "state": "FILLED", "filled_qty": 5,
                                       "terminal_at": 50.0}], 20.0)[0]
    assert o["state"] is None and o["filled_qty"] is None
    assert "state" in o["_masked_at_clock"]


def test_the_accessor_refuses_fragments_that_could_leak():
    async def no_conn():
        raise AssertionError("must refuse before any query")
    import asyncio
    for where in ("1=1; DROP TABLE x", "obs_id IN (SELECT 1)",
                  "state = 'FILLED'"):
        with pytest.raises(PIT.LookaheadViolation):
            asyncio.run(PIT.read(None, "paper_orders" if "state" in where
                                 else "paper_book_observations", clock=1.0,
                                 columns=["obs_id"] if "state" not in where
                                 else ["order_id"], where=where))


# ─────────────────────────── statistics ────────────────────────────────

def test_kaplan_meier_respects_censoring():
    units = [(10, True), (20, False), (30, True), (40, True)]
    # S: 0.75 after 10; 20 censored; 0.75 * 1/2 = 0.375 after 30
    assert ST.kaplan_meier(units) == [(10.0, 0.75), (30.0, 0.375), (40.0, 0.0)]
    assert ST.km_quantile(units, 0.5) == 30.0
    assert ST.km_quantile([(5, False), (6, False)], 0.5) is None


def test_the_bootstrap_resamples_clusters_deterministically():
    cl = {"a": [1.0, 1.0], "b": [3.0], "c": [5.0]}
    a = ST.cluster_bootstrap(cl, lambda u: ST.median(u), b=300)
    b = ST.cluster_bootstrap(cl, lambda u: ST.median(u), b=300)
    assert a == b and a["status"] == "MEASURED" and a["lo"] <= a["hi"]
    assert ST.cluster_bootstrap({"a": [1]}, ST.median)["status"] == \
        "UNAVAILABLE"
    assert ST.bonferroni_level(10) == pytest.approx(0.995)


# ─────────────────────────── the latency chain ─────────────────────────

def test_karen_allie_eddie_are_unavailable_historically():
    rec = _rec()
    rec["xi"] = {"timeline": {"decision_complete": {"utc_ns": int(
        (T0 + 0.11) * 1e9)}, "intent_created": {"utc_ns": int(
            (T0 + 0.115) * 1e9)}}}
    lc = ED.latency_chain(rec)
    assert lc["source"] == "HISTORICAL_RECORD"
    for st in ("karen_complete", "allie_complete", "eddie_complete"):
        assert lc["unavailable"][st] == ED.NOT_PRE_TRADE
    assert lc["spans"]["provider_observed_to_bettor_receipt"]["s"] == \
        pytest.approx(0.5)
    assert lc["spans"]["decision_to_adapter"]["s"] == pytest.approx(0.005,
                                                                    abs=1e-5)
    assert lc["spans"]["derek_complete_to_karen_complete"]["status"] == \
        "UNAVAILABLE"


def test_forward_stage_stamps_are_consumed_when_present():
    rec = _rec()
    rec["intent"] = {"latency_stages": {
        "pinnacle_observed_at": T0 - 5, "ingest_at": T0 - 4.5,
        "probability_qualified_at": T0 - 0.05, "derek_complete_at": T0 + 0.1,
        "karen_complete_at": T0 + 0.12, "allie_complete_at": T0 + 0.15,
        "eddie_complete_at": T0 + 0.2},
        "adapter_stages": {"intent_recorded_at": T0 + 0.21,
                           "paper_submit_at": T0 + 0.25}}
    lc = ED.latency_chain(rec)
    assert lc["source"] == "FORWARD_CANONICAL_INTENT"
    assert lc["spans"]["karen_complete_to_allie_complete"]["s"] == \
        pytest.approx(0.03)
    assert lc["spans"]["canonical_intent_complete_to_adapter_receipt"][
        "s"] == pytest.approx(0.04)
    # a forward intent that does not stamp a stage says so
    rec["intent"] = {"latency_stages": {"ingest_at": T0 - 4.5}}
    lc = ED.latency_chain(rec)
    assert lc["unavailable"]["karen_complete"] == ED.NOT_STAMPED


# ─────────────────────────── the fast lane ─────────────────────────────

def test_the_dependency_graph_is_derived_from_the_component_source():
    g = FL.derive_graph()
    assert g["status"] == "DERIVED"
    dep = g["depends_on"]
    assert set(dep) == {"event_start", "eddie", "opportunity_score",
                        "karen", "allie"}
    assert dep["karen"] == []                        # reads slug/strategy only
    assert "eddie" in dep["allie"] and "eddie" in dep["opportunity_score"]
    assert g["layers"][0] == ["event_start", "karen"]
    # derived, not hard-coded: a source where Karen reads Eddie's output
    src = '''
async def at_decision(conn, *, decision, book_row, now=None):
    eddie = await eddie_at_decision(conn, decision=decision)
    karen = await karen_at_decision(conn, slug=eddie.get("x"))
    allie = await allie_at_decision(conn, decision=decision)
'''
    g2 = FL.derive_graph(src)
    assert g2["depends_on"]["karen"] == ["eddie"]
    assert g2["depends_on"]["allie"] == []


def test_the_intent_waits_for_every_component_and_the_estimate_is_honest():
    ia = FL.intent_assembly()
    assert ia["intent_waits_for"] == ["allie", "derek", "eddie", "karen",
                                      "opportunity_score"]
    dep = FL.derive_graph()["depends_on"]
    lat = {"event_start": 0.01, "eddie": 0.05, "opportunity_score": 0.02,
           "karen": 0.01, "allie": 0.03}
    cp = FL.critical_path(dep, lat)
    assert cp["sequential_s"] == pytest.approx(0.12)
    assert cp["concurrent_s"] == pytest.approx(0.09)
    assert FL.critical_path(dep, dict(lat, karen=None))["status"] == \
        "UNAVAILABLE"
    u = FL.urgency_classes({}, sequential_s=0.12, concurrent_s=0.09)
    assert any("ALL required" in x or "ALL" in x for x in u["invariants"])


# ─────────────────────────── the extraction loader ─────────────────────

def test_the_extraction_log_parses_with_stamps_and_bom():
    rec = _rec()
    sha = "ab" * 32
    text = "\n".join([
        "2026-10-04T20:16:57.5Z == research/lab_a_edge_decay_extract.sql ==",
        "2026-10-04T20:16:57.5Z == sha256 %s ==" % sha,
        "2026-10-04T20:16:57.6Z == %s rows follow (one JSON object per line)"
        % R.EXTRACT_VERSION,
        "2026-10-04T20:16:57.6Z  lab_a_row",
        "2026-10-04T20:16:57.6Z --------",
        "2026-10-04T20:16:57.7Z  " + json.dumps(rec),
        "﻿2026-10-04T20:16:57.8Z  " + json.dumps(rec),
        "2026-10-04T20:16:57.9Z (2 rows)"])
    ex = R.parse_extract(text)
    assert ex["rows_declared"] == 2 and len(ex["records"]) == 2
    assert ex["sql_sha256"] == sha
    s = ED.summarize(ED.evaluate_all(ex["records"], fee_fn=ZERO))
    assert s["qualified"] == 2 and s["evaluated"] == 2
    pm = ED.pm_answers(s)
    assert pm["status"] == "RETROSPECTIVE_ONLY"
    assert pm["A_ev_lost_to_internal_latency"]["karen_allie_eddie_loss_usd"][
        "status"] == "UNAVAILABLE"
