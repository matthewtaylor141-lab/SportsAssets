"""THE OTHER SIDE OF THE SAME VENUE CONTRACT IS VALUED (P0 incident,
inc-edge, 2026-10-04).

THE DEFECT, MEASURED ON PRODUCTION (research/incident_edge_inputs*.sql, runs
37233569900 / 37233755356 / 37234196320). The entry lane valued only the
provider's HOME team of every event, so no paper strategy could ever buy
the other side of a contract: on 17 completed-game V3 decisions (10
markets) in 7 days, the home side was BELOW_MIN_GROSS_EDGE while the other
side of the SAME contract on the SAME observed book cleared the unchanged
0.5 pp threshold and was positive net of the venue fee. Every home-side
input recomputed exactly (power de-vig 2797/2797, American odds 3627/3627,
consumed side 2876/2876, fees and net EV 46/46), so those home refusals are
genuine economics; the defect is the side that was never evaluated.

THE ROWS HERE ARE PRODUCTION-SHAPED. The Greece v Germany contract
`atc-unl-gre-ger-2026-10-04-gre` is the recorded production decision
papercg:91f66dd61a308f61f5b4c244 (2026-10-04, CG V3, BELOW_MIN_GROSS_EDGE):
its raw odds {Draw 2.96, Greece 6.28, Germany 1.78}, its stored power
probability 0.14208691970255594, its venue rules text (the recorded GRE_GER
wording) and its book exactly as `paper_book_observations` recorded it
(px {"value": "0.1600", "currency": "USD"}, qty "8386.8400", ...). The MLB
case is the recorded `aec-mlb-sd-mil-2026-10-04` shape (home team on the
venue's SHORT side, half-cent offers). The qualifying case moves only the
prices, to the recorded 18:00Z bucket of the same contract (NO at 0.76).

Each fix's production effect is stated in the commit; these tests reproduce
the defect (the complement row was never written, and could not be: the
uniqueness key swallowed it) and prove the repair through the real writer,
the real persist, the real paper pass and the real Xavier measure.
No real money, no venue order: the market-data client counts mutation
attempts and the proofs assert zero.
"""
from __future__ import annotations

import asyncio
import copy
import pathlib
import time

import pytest

from sportsassets import bettor_complement_valuation as CV
from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import bettor_valuation_purpose as vp
from sportsassets.agents import derek_policy as DP
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR
from sportsassets.workers import ext_pinnacle_loop as loop

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
CG = PB.CG_STRATEGY
LONG, SHORT = "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"

#: THE RECORDED VENUE WORDING of the Greece v Germany contract (the same
#: text tests/test_completed_game_paper_policy.GRE_GER pins).
GRE_GER = (
    "This market will settle to the winner at the end of 90 minutes plus "
    "stoppage time in the Greece vs Germany UEFA Nations League match "
    "scheduled for 2026-10-04 2:45PM ET. If the match is tied following 90 "
    "minutes plus stoppage time, the market will settle to Tie. If the match "
    "is delayed, postponed, or suspended and not rescheduled to a date within "
    "two weeks of the originally scheduled date, the market will settle to "
    "the last fair market price. Outcome sourced from UEFA.")
#: papercg:91f66dd61a308f61f5b4c244, as recorded.
GRE_ODDS = {"Draw": 2.96, "Greece": 6.28, "Germany": 1.78}
GRE_P_STORED = 0.14208691970255594


def _lv(px, q):
    return {"px": {"value": "%.4f" % px, "currency": "USD"},
            "qty": "%.4f" % q}


#: The recorded paper_book_observations levels of that decision (top 8).
GRE_OFFERS = [_lv(0.16, 8386.84), _lv(0.17, 2423.02), _lv(0.18, 400.0),
              _lv(0.19, 2.0), _lv(0.20, 1.0), _lv(0.21, 1027.0),
              _lv(0.22, 1779.0), _lv(0.23, 2122.0)]
GRE_BIDS = [_lv(0.15, 8112.23), _lv(0.14, 5932.14), _lv(0.13, 3936.01),
            _lv(0.12, 880.0), _lv(0.11, 150.0), _lv(0.10, 2500.0),
            _lv(0.09, 5000.0), _lv(0.08, 5082.0)]


def _contract(slug, *, family="soccer", selection="Greece", intent=LONG,
              ladder="ASK"):
    """The contract dict exactly as ext_pinnacle_loop builds it for a
    venue-native soccer per-side contract (E6b: atc-, BUY_LONG, ASK,
    VENUE_NATIVE_US_SLUG)."""
    return {"venue": "PMUS", "condition_id": None, "us_market_slug": slug,
            "contract_identity_basis": "VENUE_NATIVE_US_SLUG",
            "identity_resolver": "bettor_venue_native_identity",
            "buy_intent": intent, "selection": selection,
            "payout_event": selection,
            "payout_event_basis": "RESOLVER_MATCHED_A_SIDE_FOR_THE_REQUESTED_"
                                  "OUTCOME",
            "probability_event": selection, "resolver_asked_for": selection,
            "matched_side_norm": "yes" if family == "soccer" else
            selection.lower(),
            "ladder_side": ladder, "sport_family": family, "market": "h2h",
            "period": "FULL_GAME", "period_basis": "VENUE_MARKET_TYPE",
            "line": None, "settlement_rule": None,
            "event_key": "evt-" + slug}


def _ev_quote(odds, *, observed_at, event_key):
    return {"book": devig.BOOK, "outcomes": dict(odds),
            "observed_at": observed_at, "received_at": observed_at + 0.4,
            "event_key": event_key, "period": "FULL_GAME",
            "period_basis": "THE_FEED_H2H_MARKET_IS_FULL_MATCH",
            "line": None, "settlement_rule": None}


def _displayed(offers, bids, intent):
    """`_displayed_not_for_orders` on the recorded payload (pure parse)."""
    return loop._displayed_not_for_orders(
        {"marketData": {"offers": offers, "bids": bids}}, intent=intent,
        slug="s", read_at=time.time(), currency={"verdict": "NOT_ESTABLISHED"})


def _home_record(slug, odds, offers, bids, *, now, family="soccer",
                 selection="Greece", intent=LONG, ladder="ASK",
                 rules=GRE_GER, books=3):
    """THE HOME CALIBRATION-ONLY RECORD as the cycle builds it: the same
    `ext.evaluate` call, purpose and evidence, settlement comparison and
    PinnAPI stamping (a the-odds-api row: stamping is a no-op)."""
    contract = _contract(slug, family=family, selection=selection,
                         intent=intent, ladder=ladder)
    evq = _ev_quote(odds, observed_at=now - 5.0,
                    event_key=contract["event_key"])
    shown = _displayed(offers, bids, intent)
    basis = {"refusal": loop.R_BOOK_CURRENCY_NOT_ESTABLISHED,
             "displayed": dict(shown, usable_for_orders=False),
             "book_currency": {"verdict": "NOT_ESTABLISHED"},
             "venue_read_why": "no mechanism"}
    extra = [loop.R_BOOK_CURRENCY_NOT_ESTABLISHED]
    rec = ext.evaluate(
        contract=contract, quote=evq,
        market_state=loop._displayed_market_state(basis),
        execution_plan=None,
        execution_estimate={"p_fill": None, "basis": "P_FILL_NOT_IDENTIFIED",
                            "crossing": True},
        size=None, risk={"permitted": False,
                         "reason": "NO_EXECUTION_PLAN_WAS_BUILT"},
        fee_fn=lambda qty, price: 0.0695 * qty * price * (1 - price),
        now=now, outcome_books=books, armed=True, payout_is_complement=False,
        extra_refusals=extra, record_purpose=ext.PURPOSE_CALIBRATION_ONLY,
        calibration_only_evidence={
            "venue_read_refusal": basis["refusal"],
            "venue_read_why": basis["venue_read_why"],
            "book_currency": basis["book_currency"],
            "displayed_quote": basis["displayed"],
            "decision_instant_epoch_s": now, "decision_lag_s": 0.5})
    rec["calibration_only_evidence"]["freshness"] = {"fresh": None}
    rec["calibration_only_evidence"]["rails"] = "NOT_EVALUATED"
    rec["settlement_comparison"] = {"compatibility": "UNKNOWN",
                                    "venue_rules_text": rules,
                                    "venue_rules_read": True}
    vq = {"ok": False, "refusal": loop.R_BOOK_CURRENCY_NOT_ESTABLISHED,
          "displayed_not_for_orders": shown,
          "displayed_not_for_orders_other_side": _displayed(
              offers, bids, CV.other_intent(intent))}
    return rec, contract, evq, vq, extra


def _complement(rec, contract, evq, vq, extra, *, now, books=3):
    return loop.complement_record(
        rec, contract=contract, ev_quote=evq,
        quote={"reference_input": None}, vq=vq,
        fee_fn=lambda qty, price: 0.0695 * qty * price * (1 - price),
        now=now, outcome_books=books, extra=extra,
        reference_check={"ok": True}, decision_lag_s=0.5)


# ═════════════════════════════════════════════════════════════════════
# 1 · THE RULE, PURE
# ═════════════════════════════════════════════════════════════════════

def test_the_complete_set_table_is_the_de_vigs_own():
    # 1 - p is exact only over the COMPLETE outcome set the de-vig enforces
    assert CV.COMPLETE_SET_OUTCOMES == devig.SUPPORTED
    assert CV.other_intent(LONG) == SHORT
    assert CV.other_intent(SHORT) == LONG
    assert CV.other_intent("ORDER_INTENT_SELL_LONG") is None
    assert CV.other_intent(None) is None


def test_the_recorded_home_side_recomputes_exactly():
    """THE INPUT VALIDATION, on the recorded row: the stored power de-vig
    of Greece and the home side's gross edge at its recorded best offer."""
    now = time.time()
    rec, *_ = _home_record("atc-unl-gre-ger-2026-10-04-gre", GRE_ODDS,
                           GRE_OFFERS, GRE_BIDS, now=now)
    assert rec["probability"] == pytest.approx(GRE_P_STORED, abs=1e-12)
    assert rec["record_purpose"] == vp.CALIBRATION_ONLY
    assert rec["payout_is_complement"] is False
    # recorded decision: gross edge -1.79130803 pp at the 0.16 offer
    assert (rec["probability"] - 0.16) * 100 == pytest.approx(
        -1.79130803, abs=1e-7)


def test_the_complement_is_one_minus_p_over_the_complete_set():
    now = time.time()
    rec, contract, evq, vq, extra = _home_record(
        "atc-unl-gre-ger-2026-10-04-gre", GRE_ODDS, GRE_OFFERS, GRE_BIDS,
        now=now)
    comp, why = _complement(rec, contract, evq, vq, extra, now=now)
    assert why is None
    names = sorted(GRE_ODDS)
    pr = dict(zip(names, devig.devig([GRE_ODDS[n] for n in names],
                                     "power")))
    # NOT(Greece) on a three-way book is the draw AND Germany, never Germany
    assert comp["probability"] == pytest.approx(
        1.0 - GRE_P_STORED, abs=1e-12)
    assert comp["probability"] == pytest.approx(pr["Draw"] + pr["Germany"],
                                                abs=1e-12)
    assert comp["probability"] != pytest.approx(pr["Germany"], abs=1e-3)
    assert comp["probability_of_selection"] == pytest.approx(GRE_P_STORED,
                                                             abs=1e-12)
    assert comp["payout_is_complement"] is True
    assert comp["payout_event"] == "NOT(Greece)"
    c = comp["contract"]
    assert c["buy_intent"] == SHORT and c["ladder_side"] == "BID"
    assert c["selection"] == "Greece"
    assert c["us_market_slug"] == contract["us_market_slug"]
    assert c["payout_event_basis"] == CV.BASIS
    # SEALED: never a funded candidate
    assert comp["record_purpose"] == vp.CALIBRATION_ONLY
    assert comp["admissible"] is False and comp["decision"] == "NO_TRADE"
    assert comp["executable_price"] is None
    assert comp["proposed_size"] is None
    assert comp["refusals"][0] == loop.R_BOOK_CURRENCY_NOT_ESTABLISHED
    assert vp.R_CALIBRATION_ONLY in comp["refusals"]
    ev = comp["calibration_only_evidence"]
    assert ev["usable_for_orders"] is False
    # the side the complement consumes: the bids, at 1 - 0.15
    assert ev["displayed_quote"]["acquisition_price"] == pytest.approx(0.85)
    assert ev["displayed_quote"]["side_consumed"] == "BID"
    assert ev["complement"]["basis"] == CV.BASIS
    # the contract's own settlement text travels with it
    assert comp["settlement_comparison"]["venue_rules_text"] == GRE_GER


def test_the_complement_passes_the_payout_outcome_match():
    """The paper strategies' own match accepts it as the event it pays on
    -- complement, NOT(selection) -- and the completed-game grading period
    is read off the same contract text."""
    now = time.time()
    rec, contract, evq, vq, extra = _home_record(
        "atc-unl-gre-ger-2026-10-04-gre", GRE_ODDS, GRE_OFFERS, GRE_BIDS,
        now=now)
    comp, _ = _complement(rec, contract, evq, vq, extra, now=now)
    row = _row_of(comp)
    cand = DP.candidate_from_row(row)
    m = PB.completed_game_match(cand, row)
    checks = {c["check"]: c for c in m["checks"]}
    assert checks["payout_outcome_match"]["passed"] is True, checks
    assert checks["ordinary_completion_grading_period"]["passed"] is True
    assert checks[DP.C_IDENTITY]["passed"] is True
    assert PD.holding_side_of(cand["side"]) == "SHORT"


def test_mlb_home_on_the_short_side_complements_to_the_long_side():
    """aec- money line: the home team is the venue's SHORT side (all 594
    MLB valuations in the window); its complement is the LONG side of the
    same contract, at the best ask, paying on NOT(home) = the away team."""
    now = time.time()
    odds = {"San Diego Padres": 2.30, "Milwaukee Brewers": 1.68}
    offers = [_lv(0.45, 234275.74), _lv(0.455, 123922.9), _lv(0.46, 123064.71)]
    bids = [_lv(0.445, 491283.44), _lv(0.44, 165857.92), _lv(0.435, 1000.0)]
    rec, contract, evq, vq, extra = _home_record(
        "aec-mlb-sd-mil-2026-10-04", odds, offers, bids, now=now,
        family="baseball", selection="Milwaukee Brewers", intent=SHORT,
        ladder="BID", rules=PL.RECORDED_PHI_ATL_VENUE_PROSE)
    comp, why = _complement(rec, contract, evq, vq, extra, now=now)
    assert why is None
    names = sorted(odds)
    pr = dict(zip(names, devig.devig([odds[n] for n in names], "power")))
    assert comp["probability"] == pytest.approx(pr["San Diego Padres"],
                                                abs=1e-12)
    assert comp["contract"]["buy_intent"] == LONG
    assert comp["contract"]["ladder_side"] == "ASK"
    assert comp["payout_event"] == "NOT(Milwaukee Brewers)"
    # displayed LONG side: the best offer at px
    assert comp["calibration_only_evidence"]["displayed_quote"][
        "acquisition_price"] == pytest.approx(0.45)


@pytest.mark.parametrize("mutate,why", [
    (lambda r, c, v: r.update(record_purpose=vp.ENTRY_DECISION),
     CV.N_HOME_NOT_CALIBRATION_ONLY),
    (lambda r, c, v: r.update(probability=None), CV.N_NO_PROBABILITY),
    (lambda r, c, v: r.update(payout_is_complement=True),
     CV.N_ALREADY_COMPLEMENT),
    (lambda r, c, v: c.update(buy_intent="ORDER_INTENT_SELL_LONG"),
     CV.N_INTENT),
    (lambda r, c, v: r["valuation"].update(outcomes_priced=2),
     CV.N_OUTCOME_SET),
    (lambda r, c, v: c.update(sport_family="football"), CV.N_OUTCOME_SET),
    (lambda r, c, v: v.update(displayed_not_for_orders_other_side={
        "ok": False}), CV.N_OTHER_SIDE_EMPTY),
])
def test_no_complement_is_written_unless_it_is_exact(mutate, why):
    """A complement is recorded ONLY beside a calibration-only home record
    with a probability over a complete outcome set and a readable other
    side -- every other case is a NAMED reason, counted by the cycle."""
    now = time.time()
    rec, contract, evq, vq, extra = _home_record(
        "atc-unl-gre-ger-2026-10-04-gre", GRE_ODDS, GRE_OFFERS, GRE_BIDS,
        now=now)
    mutate(rec, contract, vq)
    comp, got = _complement(rec, contract, evq, vq, extra, now=now)
    assert comp is None and got == why
    assert why in CV.NOT_WRITTEN


def test_a_failed_pinnapi_check_removes_the_complements_probability_too():
    """`pinnapi_primary.stamp_record` runs on the complement exactly as on
    the home record: an invalid decision-instant check leaves no actionable
    probability on either side."""
    now = time.time()
    rec, contract, evq, vq, extra = _home_record(
        "atc-unl-gre-ger-2026-10-04-gre", GRE_ODDS, GRE_OFFERS, GRE_BIDS,
        now=now)
    comp, _ = loop.complement_record(
        rec, contract=contract, ev_quote=evq,
        quote={"reference_input": {"provider": "pinnapi.com/raw-websocket"}},
        vq=vq, fee_fn=lambda qty, price: 0.0, now=now, outcome_books=3,
        extra=extra,
        reference_check={"ok": False, "reason": "PINNAPI_PRIMARY_INPUT_"
                                               "CHANGED"},
        decision_lag_s=0.5)
    assert comp["probability"] is None
    assert comp["refusals"][0] == "PINNAPI_PRIMARY_INPUT_CHANGED"
    assert comp["admissible"] is False


def test_the_refused_read_carries_the_other_side_under_no_order_key():
    """venue_quote's refused read carries BOTH displayed sides of the one
    payload, each flagged unusable for orders; neither under an order key."""
    import inspect
    src = inspect.getsource(loop.venue_quote)
    assert '"displayed_not_for_orders_other_side"' in src
    shown = _displayed(GRE_OFFERS, GRE_BIDS, SHORT)
    assert shown["usable_for_orders"] is False
    assert shown["acquisition_price"] == pytest.approx(0.85)
    assert "ask" not in shown and "acquisition_ladder" not in shown


# ═════════════════════════════════════════════════════════════════════
# 2 · THE ROW, THE KEY AND THE ROLLBACK (migration 251)
# ═════════════════════════════════════════════════════════════════════

def _row_of(rec: dict) -> dict:
    """The row `persist` would write, as DP.candidate_from_row reads it."""
    c = rec["contract"]
    v = rec.get("valuation") or {}
    return {"id": 1, "experiment_id": ext.EXPERIMENT_ID, "venue": "PMUS",
            "condition_id": c.get("condition_id"),
            "us_market_slug": c.get("us_market_slug"),
            "event_key": c.get("event_key"), "buy_intent": c.get("buy_intent"),
            "payout_event": rec.get("payout_event"),
            "payout_is_complement": rec.get("payout_is_complement"),
            "contract_selection": c.get("selection"),
            "probability_event": c.get("probability_event"),
            "sport_family": c.get("sport_family"), "market": c.get("market"),
            "line": None, "period": c.get("period"),
            "probability": rec.get("probability"),
            "provider": "the-odds-api.com/v4",
            "observed_at": rec.get("observed_at"),
            "received_at": rec.get("received_at"),
            "overround": v.get("overround"), "devig_method": "power",
            "version": devig.VERSION, "outcome_books": rec.get(
                "outcome_books"),
            "refusals": rec.get("refusals"),
            "record_purpose": rec.get("record_purpose"),
            "admissible": rec.get("admissible"),
            "settlement_comparison": rec.get("settlement_comparison"),
            "decided_at": time.time()}


async def _clean(conn, slug):
    """The valuation rows these proofs wrote, removed (the paper records
    are append-only and stay with their scratch accounts, exactly as
    `paper_live_fixture.purge_everything` leaves them)."""
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "DELETE FROM external_valuations WHERE us_market_slug=$1", slug)


@pg
def test_the_old_key_swallowed_the_complement_and_251_keeps_it():
    """THE DEFECT'S SECOND HALF, reproduced: under migration 106's key the
    complement (same selection, same observation, other side) is an "already
    recorded" duplicate and `persist` writes nothing. Under 251 both rows
    exist, one per side."""
    async def go():
        conn = await H.connect()
        slug = "atc-unl-gre-ger-2026-10-04-gre-k%d" % int(time.time())
        try:
            now = time.time()
            rec, contract, evq, vq, extra = _home_record(
                slug, GRE_ODDS, GRE_OFFERS, GRE_BIDS, now=now)
            comp, _ = _complement(rec, contract, evq, vq, extra, now=now)
            tr = conn.transaction()
            await tr.start()
            try:
                old = (MIG / "rollback" /
                       "251_external_valuations_one_per_observation_per_"
                       "side.down.sql").read_text()
                # (rolled back) the rows 106's key cannot hold -- other
                # proofs' complements and no-instant rows -- and rows
                # labelled with their clock (which the rollback refuses to
                # erase) are cleared so the rollback's own guard lets it
                # apply here
                await conn.execute(
                    "DELETE FROM external_valuations WHERE "
                    "payout_is_complement OR observed_at IS NULL "
                    "OR observed_at_basis IS NOT NULL")
                await conn.execute(old)
                # THE KEY is what this half reproduces: the writer under test
                # also writes 251's label column, put back bare (no CHECK)
                # inside this rolled-back transaction
                await conn.execute("ALTER TABLE external_valuations ADD "
                                   "COLUMN observed_at_basis text")
                assert await ext.persist(conn, copy.deepcopy(rec))
                assert await ext.persist(conn, copy.deepcopy(comp)) is None
            finally:
                await tr.rollback()
            hid = await ext.persist(conn, copy.deepcopy(rec))
            cid = await ext.persist(conn, copy.deepcopy(comp))
            assert hid and cid and hid != cid
            rows = await conn.fetch(
                "SELECT id, buy_intent, payout_event, payout_is_complement, "
                "probability, record_purpose, admissible, contract_selection "
                "FROM external_valuations WHERE us_market_slug=$1 "
                "ORDER BY id", slug)
            assert [r["payout_is_complement"] for r in rows] == [False, True]
            assert [r["buy_intent"] for r in rows] == [LONG, SHORT]
            assert rows[1]["payout_event"] == "NOT(Greece)"
            assert rows[1]["contract_selection"] == "Greece"
            assert rows[0]["probability"] + rows[1]["probability"] == \
                pytest.approx(1.0, abs=1e-12)
            assert {r["record_purpose"] for r in rows} == {vp.CALIBRATION_ONLY}
            assert not any(r["admissible"] for r in rows)
            # a re-read of the same observation is still one row per side
            assert await ext.persist(conn, copy.deepcopy(comp)) is None
            assert await ext.persist(conn, copy.deepcopy(rec)) is None
            # the database refuses to make the complement tradable
            import asyncpg
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute(
                    "UPDATE external_valuations SET admissible=true, "
                    "decision='BUY' WHERE id=$1", cid)
        finally:
            await _clean(conn, slug)
            await conn.close()

    asyncio.run(go())


@pg
def test_the_rollback_refuses_while_complement_rows_exist():
    async def go():
        conn = await H.connect()
        slug = "atc-unl-gre-ger-2026-10-04-gre-r%d" % int(time.time())
        down = (MIG / "rollback" / "251_external_valuations_one_per_"
                "observation_per_side.down.sql").read_text()
        up = (MIG / "251_external_valuations_one_per_observation_per_"
              "side.sql").read_text()
        try:
            now = time.time()
            rec, contract, evq, vq, extra = _home_record(
                slug, GRE_ODDS, GRE_OFFERS, GRE_BIDS, now=now)
            comp, _ = _complement(rec, contract, evq, vq, extra, now=now)
            await ext.persist(conn, copy.deepcopy(rec))
            await ext.persist(conn, copy.deepcopy(comp))
            import asyncpg
            tr = conn.transaction()
            await tr.start()
            try:
                with pytest.raises(asyncpg.PostgresError,
                                   match="complement-side"):
                    await conn.execute(down)
            finally:
                await tr.rollback()
            # with none present it applies, and 251 re-applies (rolled back)
            tr = conn.transaction()
            await tr.start()
            try:
                await conn.execute(
                    "DELETE FROM external_valuations WHERE "
                    "payout_is_complement OR observed_at IS NULL "
                    "OR observed_at_basis IS NOT NULL")
                await conn.execute(down)
                d = await conn.fetchval(
                    "SELECT indexdef FROM pg_indexes WHERE indexname="
                    "'external_valuations_one_per_observation'")
                assert "buy_intent" not in d
                await conn.execute(up)
                d = await conn.fetchval(
                    "SELECT indexdef FROM pg_indexes WHERE indexname="
                    "'external_valuations_one_per_observation'")
                assert "buy_intent" in d
            finally:
                await tr.rollback()
        finally:
            await _clean(conn, slug)
            await conn.close()

    asyncio.run(go())


# ═════════════════════════════════════════════════════════════════════
# 3 · THROUGH THE REAL PAPER PASS AND XAVIER'S MEASURE
# ═════════════════════════════════════════════════════════════════════

async def _nosleep(_):
    return None


@pytest.fixture
def cg_on(monkeypatch, new_strategies_off):
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv(PL.S.ENV_FLAG, "on")
    PL.set_policy_control(PB.CG_POLICY["control_key"], True)
    PL.set_policy_control(PB.CONTROL_KEY, False)
    PB._CONTEXT_CACHE.clear()
    PD._CONTEXT_CACHE.clear()
    yield
    PB._CONTEXT_CACHE.clear()


async def _decide_both(conn, *, slug, odds, offers, bids, label):
    """The home record and its complement written by the REAL writer
    (`ext.persist`), then ONE real paper pass over a production-shaped
    book (the recorded px/qty shape, currency included)."""
    now = time.time() + 5.0
    await PL.purge_everything(conn)
    await PL.purge_research_models(conn)
    acct = await PL.new_account(conn, label, now=now)
    rec, contract, evq, vq, extra = _home_record(
        slug, odds, offers, bids, now=now - 2.0)
    comp, why = _complement(rec, contract, evq, vq, extra, now=now - 2.0)
    assert why is None
    hid = await ext.persist(conn, copy.deepcopy(rec))
    cid = await ext.persist(conn, copy.deepcopy(comp))
    assert hid and cid
    t = PL.Transport(now)
    t.books[slug] = {"offers": offers, "bids": bids}
    client = PL.client(t)
    t.t = max(t.t, now)
    out = await PR.paper_pass(conn, now=now, account_id=acct["account_id"],
                              market_data=client, config=acct["config"],
                              force=True, fee_fn=None, sleep=_nosleep)
    assert out["ran"] and not out["errors"], out["errors"]
    assert client.mutation_attempts == 0
    dec = {}
    for name, vid in (("home", hid), ("complement", cid)):
        dec[name] = await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
            " valuation_id=$2 AND strategy=$3", acct["session_id"], vid, CG)
        assert dec[name] is not None, name
    return acct, dec, hid, cid, now


@pg
async def test_the_recorded_row_complement_is_now_decided_and_refused_by_fees(
        cg_on):
    """THE RECORDED PRODUCTION ROW. Before: one decision, the home side,
    BELOW_MIN_GROSS_EDGE at -1.79 pp; the NO side was never looked at. Now
    the NO side is decided too: 1 - p = 0.85791 against 1 - 0.15 = 0.85
    clears the 0.5 pp gross threshold (0.79 pp) and the deployed fee
    (0.0695 x 0.85 x 0.15 = 0.89 pp) consumes it -- GENUINE ECONOMICS,
    recorded by name, no threshold touched."""
    conn = await H.connect()
    slug = "atc-unl-gre-ger-2026-10-04-gre"
    try:
        acct, dec, hid, cid, now = await _decide_both(
            conn, slug=slug, odds=GRE_ODDS, offers=GRE_OFFERS,
            bids=GRE_BIDS, label="cmpfee")
        h, c = dec["home"], dec["complement"]
        assert h["refusal"] == PB.R_EDGE
        assert H.j(h["policy_decision"])["gross_edge_pp"] == pytest.approx(
            -1.79130803, abs=1e-7)
        assert h["holding_side"] == "LONG"
        assert c["holding_side"] == "SHORT"
        assert c["p_pinnacle"] == pytest.approx(1.0 - GRE_P_STORED, abs=1e-12)
        assert c["refusal"] == PB.R_FEES_CONSUME_EDGE, c["refusals"]
        pdx = H.j(c["policy_decision"])
        assert pdx["threshold_edge_pp"] == 0.5        # unchanged
        assert pdx["gross_edge_pp"] == pytest.approx(
            (1.0 - GRE_P_STORED - 0.85) * 100, abs=1e-7)
        assert H.j(c["economics"])["levels"][0]["wire"] == pytest.approx(0.15)
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_orders WHERE session_id=$1",
            acct["session_id"]) == 0
    finally:
        await PL.purge_everything(conn)
        await _clean(conn, slug)
        await conn.close()


@pg
async def test_a_qualifying_other_side_enters_and_xavier_measures_it(cg_on):
    """THE SAME CONTRACT AT THE RECORDED 18:00Z BUCKET'S PRICES (NO at
    0.76): the home side stays BELOW_MIN_GROSS_EDGE, the NO side clears the
    unchanged 0.5 pp gross threshold AND the deployed fee, and ENTERS -- a
    SHORT paper order on the same contract. Xavier then measures the held
    side on 1 - p from the complement rows (never p(home))."""
    conn = await H.connect()
    slug = "atc-unl-gre-ger-2026-10-04-gre"
    odds = {"Draw": 3.40, "Greece": 4.54, "Germany": 1.95}
    offers = [_lv(0.25, 6000.0), _lv(0.26, 2400.0), _lv(0.27, 900.0)]
    bids = [_lv(0.24, 5200.0), _lv(0.23, 3100.0), _lv(0.22, 800.0)]
    try:
        acct, dec, hid, cid, now = await _decide_both(
            conn, slug=slug, odds=odds, offers=offers, bids=bids,
            label="cmpent")
        h, c = dec["home"], dec["complement"]
        p_home = float(h["p_pinnacle"])
        assert h["refusal"] == PB.R_EDGE
        assert c["verdict"] == "ENTER", c["refusals"]
        assert c["holding_side"] == "SHORT" and c["intent"] == SHORT
        assert float(c["p_pinnacle"]) == pytest.approx(1.0 - p_home,
                                                       abs=1e-12)
        pdx = H.j(c["policy_decision"])
        assert pdx["threshold_edge_pp"] == 0.5
        assert pdx["gross_edge_pp"] == pytest.approx(
            (1.0 - p_home - 0.76) * 100, abs=1e-7)
        econ = H.j(c["economics"])
        assert econ["acquisition"]["net_ev_positive"] is True
        assert econ["acquisition"]["fees_usd"] > 0
        o = await conn.fetchrow(
            "SELECT * FROM paper_orders WHERE decision_id=$1",
            c["decision_id"])
        assert o is not None and o["holding_side"] == "SHORT"
        # the limit is the deepest level still clearing gross AND net of its
        # fee: 0.77 (1.84 pp gross, 0.61 pp net); 0.78 clears gross only.
        # The wire is the YES price the short sells at: 1 - limit.
        assert float(o["limit_price"]) == pytest.approx(0.77)
        assert float(o["wire_price"]) == pytest.approx(0.23)

        # XAVIER: the held side's probability is read from the complement
        # rows (slug, held intent, NOT(Greece), complement), never p(home)
        ctx = {"now": now + 1.0, "clock": lambda: now + 1.0,
               "config": acct["config"]}
        m = await PB.xavier_measure(
            conn, ctx, pos={"group_id": o["group_id"],
                            "us_market_slug": slug, "holding_side": "SHORT"},
            strategy=CG)
        assert m["p"] == pytest.approx(1.0 - p_home, abs=1e-12)
        assert m["valuation_id"] == cid
    finally:
        await PL.purge_everything(conn)
        await _clean(conn, slug)
        await conn.close()


@pg
async def test_holding_the_other_side_is_recorded_and_gates_nothing(
        cg_on, monkeypatch):
    """BOTH SIDES ARE NOW VALUED, so a strategy holding one side of a binary
    contract can be handed the other. The first version of this branch
    REFUSED that entry (THIS_STRATEGY_HOLDS_THE_OTHER_SIDE_OF_THIS_CONTRACT,
    at the decision and under the account lock) -- a new paper risk-admission
    rule the owner did not ask for (review of 7bd084b; owner 2026-10-04: "do
    NOT change ... concentration / sizing / risk limits"). It only tightened,
    but a rule is the owner's to add. So the fact is RECORDED on the decision
    (`opposite_side_held`, with what it means) for the owner's decision, and
    the risk rules are exactly the base's: the later side is decided on its
    own economics and the account lock applies only its existing checks."""
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_paper_limits as LIMITS
    conn = await H.connect()
    slug = "atc-unl-gre-ger-2026-10-04-gre"
    odds = {"Draw": 3.40, "Greece": 4.54, "Germany": 1.95}
    offers = [_lv(0.25, 6000.0), _lv(0.26, 2400.0), _lv(0.27, 900.0)]
    bids = [_lv(0.24, 5200.0), _lv(0.23, 3100.0), _lv(0.22, 800.0)]
    real = LIMITS.uses_owner_policy
    scratch: dict = {}
    monkeypatch.setattr(LIMITS, "uses_owner_policy",
                        lambda a: a == scratch.get("id") or real(a))
    try:
        now = time.time() + 5.0
        await PL.purge_everything(conn)
        await PL.purge_research_models(conn)
        acct = await PL.new_account(conn, "cmpbox", now=now)
        scratch["id"] = acct["account_id"]
        # 1 · the complement (SHORT, NOT(Greece)) enters on the qualifying book
        rec, contract, evq, vq, extra = _home_record(
            slug, odds, offers, bids, now=now - 2.0)
        comp, _ = _complement(rec, contract, evq, vq, extra, now=now - 2.0)
        cid = await ext.persist(conn, copy.deepcopy(comp))
        t = PL.Transport(now)
        t.books[slug] = {"offers": offers, "bids": bids}
        client = PL.client(t)
        t.t = max(t.t, now)
        out = await PR.paper_pass(conn, now=now,
                                  account_id=acct["account_id"],
                                  market_data=client, config=acct["config"],
                                  force=True, fee_fn=None, sleep=_nosleep)
        assert out["ran"] and not out["errors"], out["errors"]
        c = await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
            " valuation_id=$2 AND strategy=$3", acct["session_id"], cid, CG)
        assert c["verdict"] == "ENTER" and c["holding_side"] == "SHORT"
        # 2 · a later valuation makes the HOME side (LONG, Greece) clear the
        # threshold on its own book: decided on its economics, the held
        # other side RECORDED beside it, no new refusal
        now2 = now + 30.0
        rec2, *_ = _home_record(slug, {"Draw": 3.10, "Greece": 2.60,
                                       "Germany": 2.90}, offers, bids,
                                now=now2 - 2.0)
        hid = await ext.persist(conn, copy.deepcopy(rec2))
        assert hid
        t.t = max(t.t, now2)
        out = await PR.paper_pass(conn, now=now2,
                                  account_id=acct["account_id"],
                                  market_data=client, config=acct["config"],
                                  force=True, fee_fn=None, sleep=_nosleep)
        assert out["ran"] and not out["errors"], out["errors"]
        h = await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE session_id=$1 AND "
            " valuation_id=$2 AND strategy=$3", acct["session_id"], hid, CG)
        assert h is not None and h["holding_side"] == "LONG"
        assert h["verdict"] == "ENTER", h["refusals"]
        pin = H.j(h["pinnacle"])
        assert pin["opposite_side_held"], pin.get("opposite_side_held")
        assert pin["opposite_side_held_is"] == L.OPPOSITE_SIDE_HELD_IS
        assert not hasattr(L, "R_OPPOSITE_SIDE_HELD")
        # 3 · and the account lock applies only its existing rules: the
        # same order is not refused for holding the other side
        o = H.order(acct, key="box-long", slug=slug, qty=10, limit=0.30,
                    holding_side="LONG", at=now2, fixture=None)
        o["strategy"] = CG
        got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=now2)
        assert "OTHER_SIDE" not in str(got.get("refusal") or ""), got
        assert client.mutation_attempts == 0
    finally:
        await PL.purge_everything(conn)
        await _clean(conn, slug)
        await conn.close()
