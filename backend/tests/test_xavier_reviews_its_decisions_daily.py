"""XAVIER'S DAILY REVIEW (migration 149, `bettor_xavier_review`).

What these pin, against real ledger rows in a migrated database:

  * ONCE PER UTC DAY. A second call the same day, and a "restart" on a fresh
    connection, do not re-run it; a failed run writes no row and the next
    cycle retries.
  * THE OUTCOME COMES FROM THE FUNDED ECONOMICS LEDGER, in the forecast's own
    scope: cash after the decision instant minus the basis still held at it.
  * ONE WEIGHT PER FIXTURE. Three reviews of one position are one example.
  * ALTERNATIVES ARE LABELLED ESTIMATES: kind HYPOTHETICAL_ESTIMATE, a fill
    basis, could_have_filled UNPROVEN. A blocked alternative carries its
    blocker and no estimate.
  * A SETTLEMENT THE VENUE NOW CONTRADICTS invalidates the decision and keeps
    it out of every statistic.
  * THE MODEL SUMMARY NEVER PROMOTES; `withdraw_invalidated` only retires.
  * REVIEWS ARE APPEND-ONLY.

ALL LEDGER ROWS HERE ARE SYNTHETIC TEST EVIDENCE, dated in 2031 so that no
production-shaped row can fall in the review window; they are never a
production substitute.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os

import pytest

from sportsassets import bettor_xavier as X
from sportsassets import bettor_xavier_review as XR

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

ACCT = "acct-xrevt"
VENUE = "PMUS_TEST"
PFX = "xrevt-"
MODEL_KEY = "xrevt_key"
#: SYNTHETIC: a Monday noon in 2031, far from any real decision.
T = dt.datetime(2031, 3, 10, 12, 0, tzinfo=dt.timezone.utc).timestamp()
DAY = 86400.0
LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"


# ═════════════════════════════════════════════════════════════════════
# 0 · PURE: THE ARITHMETIC AND THE LABELS
# ═════════════════════════════════════════════════════════════════════

HELD = {"qty": 10.0, "basis_per_contract": 0.5, "payout_per_contract": 1.0,
        "payout_source": "BOOKED_SETTLEMENT"}


def test_a_hold_estimate_is_the_held_payout_minus_its_basis():
    got = XR.hypothetical_estimate(
        {"action": "HOLD", "qty": 10, "value_usd": 1.0}, held=HELD)
    assert got["kind"] == XR.KIND_HYPOTHETICAL
    assert got["estimate_usd"] == pytest.approx(5.0)
    assert got["fill_basis"] == XR.FILL_BASIS_NO_ORDER
    assert got["could_have_filled"] == "UNPROVEN"
    assert got["eventual_outcome_known"] is True


def test_an_exit_estimate_is_its_decision_time_proceeds_and_the_retained_rest():
    # sold 4 of 10: slice net of fees and basis 0.3, the retained 6 settle at 1
    got = XR.hypothetical_estimate(
        {"action": "DIRECT_EXIT", "qty": 4, "value_usd": 3.3,
         "slice_value_usd": 0.3, "fees_usd": 0.05}, held=HELD)
    assert got["estimate_usd"] == pytest.approx(0.3 + 6 * (1.0 - 0.5))
    assert got["fill_basis"] == XR.FILL_BASIS_DISPLAYED
    assert got["could_have_filled"] == "UNPROVEN"
    # proceeds stated gross: net of the stated fee and the sold basis
    got2 = XR.hypothetical_estimate(
        {"action": "REDUCE", "qty": 10, "value_usd": 0.4,
         "proceeds_usd": 5.6, "fees_usd": 0.1}, held=HELD)
    assert got2["estimate_usd"] == pytest.approx(5.6 - 0.1 - 5.0)
    # a retained remainder with an unknown settlement is not estimated
    unknown = dict(HELD, payout_per_contract=None)
    got3 = XR.hypothetical_estimate(
        {"action": "DIRECT_EXIT", "qty": 4, "value_usd": 1.0,
         "slice_value_usd": 0.3}, held=unknown)
    assert got3["estimate_usd"] is None
    assert got3["no_estimate_because"] == XR.NE_HELD_OUTCOME_UNKNOWN


def test_an_acquisition_estimate_needs_the_second_contracts_settlement():
    alt = {"action": "ACQUIRE_HEDGE", "value_usd": 0.2, "units": 10,
           "hedge_slug": "opp-plus-1-5", "hedge_order_intent": LONG,
           "cost_usd": 9.8, "fees_usd": 0.1,
           "position_includes_uncovered_inventory": True}
    none = XR.hypothetical_estimate(alt, held=HELD, hedge=None)
    assert none["estimate_usd"] is None
    assert none["no_estimate_because"] == XR.NE_HEDGE_OUTCOME_UNKNOWN
    got = XR.hypothetical_estimate(
        alt, held=HELD, hedge={"payout_per_contract": 0.0, "source": "t"})
    assert got["estimate_usd"] == pytest.approx(10 * 1.0 + 0 - 9.8 - 0.1)
    assert got["could_have_filled"] == "UNPROVEN"
    unnamed = XR.hypothetical_estimate(
        {k: v for k, v in alt.items() if k != "hedge_slug"}, held=HELD)
    assert unnamed["no_estimate_because"] == XR.NE_HEDGE_NOT_NAMED


def test_a_blocked_alternative_carries_its_blocker_and_no_estimate():
    got = XR.hypothetical_estimate(
        {"action": "REDUCE", "blocker": "NO_EXECUTABLE_DEPTH",
         "value_usd": None}, held=HELD)
    assert got["kind"] == XR.KIND_BLOCKED
    assert got["estimate_usd"] is None
    assert got["blocker"] == "NO_EXECUTABLE_DEPTH"
    assert "could_have_filled" not in got and "fill_basis" not in got
    nothing = XR.hypothetical_estimate({"action": "HOLD"}, held=HELD)
    assert nothing["no_estimate_because"] == XR.NE_NO_ECONOMICS


def test_sides_pay_and_win_by_the_long_price_and_a_push_is_not_binary():
    assert XR.side_payout(1.0, SHORT) == 0.0
    assert XR.side_payout(0.0, SHORT) == 1.0
    assert XR.side_won(0.0, SHORT) is True
    assert XR.side_won(1.0, SHORT) is False
    assert XR.side_won(0.5, LONG) is None


def test_fixture_weighting_and_insufficient_evidence():
    rows = ([{"fixture": "A", "predicted_usd": 0, "realised_usd": e,
              "error_usd": e} for e in (4.0, 3.0, 2.0)]
            + [{"fixture": "B", "predicted_usd": 0, "realised_usd": -4.0,
                "error_usd": -4.0}])
    st = XR.weighted_stats(rows)
    assert st["decisions"] == 4 and st["fixtures"] == 2
    assert st["mean_error_usd"] == pytest.approx(-0.5)
    assert st["decision_weighted_for_reference_only"][
        "mean_error_usd"] == pytest.approx(1.25)
    assert st["status"] == "INSUFFICIENT_EVIDENCE"
    rel = XR.reliability_check([("A", 0.6, True), ("A", 0.7, True),
                                ("A", 0.8, True), ("B", 0.4, False),
                                ("C", 0.5, None)])
    # the push on C never enters; A's three share one weight
    assert rel["decisions"] == 4 and rel["fixtures"] == 2
    assert rel["brier"] == pytest.approx(((0.29 / 3) + 0.16) / 2, abs=1e-6)
    assert "NOT a calibration verdict" in rel["what_this_is"]


def _facts(**kw):
    legs = [{"intent_id": "L", "leg_role": "PRIMARY", "order_intent": LONG,
             "open": kw.pop("open_", False),
             "closed_reason": "SETTLED_BY_THE_VENUE",
             "settlement": {"payout_price": 1.0}}]
    fills = [{"intent_id": "L", "leg_intent_id": "L", "direction": "ENTRY",
              "qty": 10.0, "cash_usd": 5.0, "at": 100.0}]
    econ = [
        {"event_id": "a", "intent_id": "L", "kind": "ENTRY_COST",
         "amount_usd": -5.0, "provisional": False, "effective_at": 100.0},
        {"event_id": "b", "intent_id": "L", "kind": "FEE",
         "amount_usd": -0.1, "provisional": kw.pop("prov", False),
         "effective_at": 100.0},
        # A LATE FEE ADJUSTMENT FOR THE PRE-DECISION FILL is attributed to
        # the fill's instant, so it stays sunk and out of the forward figure.
        {"event_id": "c", "intent_id": "L", "kind": "FEE_ADJUSTMENT",
         "amount_usd": -0.02, "provisional": False, "effective_at": 100.0},
        {"event_id": "d", "intent_id": "L", "kind": "SETTLEMENT",
         "amount_usd": 10.0, "provisional": False, "effective_at": 400.0}]
    return XR.position_facts(legs=legs, children=[], fills=fills,
                             economics=econ, decided_at=200.0, **kw)


def test_the_forward_realised_figure_is_cash_after_the_decision_minus_basis():
    f = _facts()
    assert f["status"] == XR.O_AUTHORITATIVE
    assert f["remaining_basis_at_decision_usd"] == pytest.approx(5.0)
    assert f["realised_forward_net_usd"] == pytest.approx(5.0)
    assert f["realised_whole_position_net_usd"] == pytest.approx(4.88)
    assert _facts(prov=True)["status"] == XR.O_NOT_FINAL
    assert _facts(open_=True)["status"] == XR.O_OPEN


def test_an_unanswered_disagreement_invalidates_and_a_correction_answers_it():
    rc = [{"recheck_id": 7, "intent_id": "L", "verdict": "DISAGREES",
           "booked_reading": "REPORTED_SETTLEMENT",
           "venue_reading": "EXPLICIT_VOID"}]
    bad = _facts(rechecks=rc, corrections=None)
    assert bad["status"] == XR.O_INVALIDATED
    assert bad["invalidation"]["reason"] == XR.INV_VENUE_DISAGREES
    ok = _facts(rechecks=rc, corrections=[{"correction_id": "c1",
                                           "intent_id": "L",
                                           "recheck_id": 7}])
    assert ok["status"] == XR.O_AUTHORITATIVE
    assert ok["settlement_corrections"][0]["correction_id"] == "c1"


def test_the_probability_checked_is_the_held_sides():
    """A short position scored against a long-side probability would score
    the complement, so an unsided key is used only for a LONG held side."""
    unsided = {"evidence": {"probability": 0.3}, "alternatives": []}
    assert XR.probability_used(unsided, held_side=SHORT) == (None, None)
    assert XR.probability_used(unsided, held_side=LONG)[0] == 0.3
    hold = {"evidence": {"probability": 0.3},
            "alternatives": [{"action": "HOLD", "qty": 10,
                              "cash_at_settlement_usd": 7.0}]}
    p, src = XR.probability_used(hold, held_side=SHORT)
    assert p == pytest.approx(0.7) and "cash_at_settlement" in src
    held = {"evidence": {"held_probability": 0.65}, "alternatives": []}
    assert XR.probability_used(held, held_side=SHORT)[0] == 0.65


def test_the_error_cause_is_only_what_the_record_states():
    facts = {"settlement_corrections": [], "later_actions": False,
             "fees_after_decision_usd": 0.1}
    exit_not_sent = {"chosen_action": "DIRECT_EXIT",
                     "chosen_plan_digest": "pd",
                     "execution_eligibility": X.E_SUBMISSION_DISABLED,
                     "alternatives": [{"action": "DIRECT_EXIT", "qty": 10,
                                       "plan_digest": "pd",
                                       "value_usd": 1.0}]}
    assert XR.attribute_cause(exit_not_sent, facts=facts, error=3.0)[
        "cause"] == XR.C_EXECUTION
    short = dict(exit_not_sent, execution_eligibility=X.E_DISPATCHED,
                 execution={"status": "TERMINAL:FILLED", "filled_qty": 4.0})
    got = XR.attribute_cause(short, facts=facts, error=1.0)
    assert got["cause"] == XR.C_EXECUTION
    assert got["evidence"]["filled_qty"] == 4.0
    hold = {"chosen_action": "HOLD", "evidence": {"probability": 0.6},
            "alternatives": [{"action": "HOLD", "value_usd": 1.0}]}
    assert XR.attribute_cause(hold, facts=facts, error=4.0)[
        "cause"] == XR.C_PROBABILITY
    assert XR.attribute_cause(hold, facts=dict(facts, later_actions=True),
                              error=4.0)["cause"] == XR.C_LATER_ACTIONS


# ═════════════════════════════════════════════════════════════════════
# 1 · THE DATABASE HARNESS
# ═════════════════════════════════════════════════════════════════════

@contextlib.asynccontextmanager
async def _conn():
    import asyncpg
    c = await asyncpg.connect(DSN)
    try:
        yield c
    finally:
        await c.close()


async def _purge(c):
    if not await XR.has_schema(c):
        pytest.skip("migration 149 is not in this database")
    async with c.transaction():
        # THE TABLES ARE APPEND-ONLY BY TRIGGER; the replica role lets the
        # test remove only its own synthetic rows.
        await c.execute("SET LOCAL session_replication_role = replica")
        if await X.has_event_schema(c):
            await c.execute("DELETE FROM bettor_xavier_execution_events "
                            " WHERE account_id=$1", ACCT)
        await c.execute("DELETE FROM bettor_xavier_decisions "
                        " WHERE account_id=$1", ACCT)
        await c.execute("DELETE FROM bettor_xavier_reviews "
                        " WHERE review_date >= '2031-01-01'")
        await c.execute("DELETE FROM bettor_funded_settlement_rechecks "
                        " WHERE intent_id LIKE $1", PFX + "%")
        for t in ("bettor_funded_economics", "bettor_funded_fills",
                  "bettor_funded_intents"):
            await c.execute("DELETE FROM %s WHERE intent_id LIKE $1" % t,
                            PFX + "%")
        await c.execute("DELETE FROM bettor_funded_portfolio_groups "
                        " WHERE group_id LIKE $1", PFX + "%")
        await c.execute("DELETE FROM bettor_funded_models "
                        " WHERE model_key LIKE $1", MODEL_KEY + "%")
        await c.execute("DELETE FROM ingestion_state WHERE key=$1",
                        XR.STATE_KEY)


@pytest.fixture()
async def db():
    if not DSN:
        pytest.skip("needs RN1X_TEST_DSN")
    async with _conn() as c:
        await _purge(c)
        yield c
        await _purge(c)


def _ts(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc)


async def _position(c, n, *, long_price=1.0, decided=T - 2 * DAY,
                    side=LONG, open_=False, provisional_fee=False):
    """ONE SYNTHETIC SINGLE-LEG POSITION: 10 contracts at 0.50 filled a day
    before `decided`, settled by the venue a day after it (unless open)."""
    gid, iid = PFX + "g%d" % n, PFX + "i%d" % n
    ev, slug = PFX + "ev%d" % n, PFX + "slug%d" % n
    fill_at, settle_at = decided - DAY, decided + DAY
    cash = 5.0
    await c.execute(
        "INSERT INTO bettor_funded_portfolio_groups (group_id, account_id, "
        " venue, event_key, structure, hedge_intent) VALUES "
        " ($1,$2,$3,$4,'SINGLE_LEG','NOT_APPLICABLE')", gid, ACCT, VENUE, ev)
    settlement = {} if open_ else {
        "payout_price": long_price, "terminal_reading": "REPORTED_SETTLEMENT",
        "at": settle_at}
    await c.execute(
        "INSERT INTO bettor_funded_intents (intent_id, account_id, venue, "
        " venue_class, us_market_slug, event_key, order_intent, limit_price, "
        " quantity, collateral_usd, effective_digest, state, kind, "
        " residual_qty, closed_at, closed_reason, settlement, "
        " portfolio_group_id, leg_role) VALUES ($1,$2,$3,'FUNDED',$4,$5,$6,"
        " 0.5,10,$7,'d','FILLED','ENTRY',$8,$9,$10,$11::jsonb,$12,'PRIMARY')",
        iid, ACCT, VENUE, slug, ev, side, cash, (10 if open_ else 0),
        None if open_ else _ts(settle_at),
        None if open_ else "SETTLED_BY_THE_VENUE", json.dumps(settlement),
        gid)
    fid = "fvf:vo-%s:1" % iid
    await c.execute(
        "INSERT INTO bettor_funded_fills (fill_id, intent_id, venue_order_id, "
        " venue_fill_id, at, qty, price, cash_usd, fee_usd, fee_basis) "
        " VALUES ($1,$2,$3,'1',$4,10,0.5,$5,0.1,'TEST_SYNTHETIC')",
        fid, iid, "vo-" + iid, _ts(fill_at), cash)
    rows = [("fev:%s:CASH" % fid, fill_at, "ENTRY_COST", -cash, False),
            ("fev:%s:FEE" % fid, fill_at, "FEE", -0.1, provisional_fee)]
    if not open_:
        pay = XR.side_payout(long_price, side) * 10
        rows.append(("fev:%s:SETTLEMENT:REPORTED_SETTLEMENT" % iid, settle_at,
                     "SETTLEMENT", pay, False))
    for eid, at, kind, amt, prov in rows:
        await c.execute(
            "INSERT INTO bettor_funded_economics (event_id, intent_id, at, "
            " kind, amount_usd, basis, provisional) VALUES "
            " ($1,$2,$3,$4,$5,'TEST_SYNTHETIC',$6)", eid, iid, _ts(at), kind,
            amt, prov)
    if not open_:
        await c.execute(
            "UPDATE bettor_funded_portfolio_groups SET closed_at=now(), "
            " closure='BOTH_LEGS_SETTLED' WHERE group_id=$1", gid)
    return {"group_id": gid, "intent_id": iid, "slug": slug, "fixture": ev}


async def _decide(c, pos, *, decided, p, chosen="HOLD", alts=None,
                  elig=None, digest=None):
    alts = alts if alts is not None else [
        {"action": "HOLD", "qty": 10, "value_usd": round(10 * p - 5.0, 6),
         "cash_at_settlement_usd": 10 * p},
        {"action": "DIRECT_EXIT", "qty": 10, "value_usd": 0.9,
         "slice_value_usd": 0.9, "fees_usd": 0.1,
         "plan_digest": "pd-%s-%d" % (pos["intent_id"], int(decided))},
        {"action": "REDUCE", "blocker": "NO_EXECUTABLE_DEPTH",
         "value_usd": None}]
    got = await X.record_decision(
        c, account_id=ACCT, venue=VENUE, intent_id=pos["intent_id"],
        decided_at=decided, responsibility_state=X.HELD,
        execution_eligibility=elig or (X.E_HOLD if chosen == "HOLD"
                                       else X.E_SUBMISSION_DISABLED),
        alternatives=alts, reasoning={"why": "synthetic test decision"},
        expected_economics={}, residual_exposure={"held_qty": 10,
                                                  "unpaired_qty": 10},
        evidence={"probability_source": "SYNTHETIC_TEST", "probability": p},
        obligations=[], chosen_action=chosen, chosen_plan_digest=digest,
        portfolio_group_id=pos["group_id"], us_market_slug=pos["slug"])
    assert got["ok"], got
    return got["xavier_decision_id"]


async def _stored(c, day="2031-03-10") -> dict:
    r = await c.fetchrow("SELECT * FROM bettor_xavier_reviews "
                         " WHERE review_date=$1::date", dt.date.fromisoformat(
                             day))
    assert r is not None
    out = dict(r)
    for k in ("window", "per_action", "alternatives", "calibration",
              "reliability", "errors", "models", "invalidated"):
        out[k] = json.loads(out[k]) if isinstance(out[k], str) else out[k]
    return out


# ═════════════════════════════════════════════════════════════════════
# 2 · ONCE PER DAY
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_review_runs_once_per_utc_day_and_a_restart_does_not(db):
    pos = await _position(db, 1)
    await _decide(db, pos, decided=T - 2 * DAY, p=0.6)
    first = await XR.daily_review(db, now=T)
    assert first["ok"] and first["ran"], first
    assert first["review_id"] == "xrev:2031-03-10"
    again = await XR.daily_review(db, now=T + 3600)
    assert again["ran"] is False
    assert again["refusal"] == XR.R_ALREADY_REVIEWED
    assert again["digest"]["last_review_date"] == "2031-03-10"
    # A RESTART is a new process with a new connection: the row is the guard.
    async with _conn() as fresh:
        after = await XR.daily_review(fresh, now=T + 7200)
    assert after["refusal"] == XR.R_ALREADY_REVIEWED
    assert after["digest"]["decisions_reviewed"] == 1
    assert after["state_key_agrees"] is True
    n = await db.fetchval("SELECT count(*) FROM bettor_xavier_reviews "
                          " WHERE review_date='2031-03-10'")
    assert n == 1
    nxt = await XR.daily_review(db, now=T + DAY)
    assert nxt["ran"] is True and nxt["review_id"] == "xrev:2031-03-11"
    assert first["digest"]["next_due_at"] == pytest.approx(
        dt.datetime(2031, 3, 11, tzinfo=dt.timezone.utc).timestamp())


@pg
async def test_a_failed_run_writes_no_row_and_the_next_cycle_retries(
        db, monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(XR, "_review", _boom)
    got = await XR.daily_review(db, now=T)
    assert got["ok"] is False and got["refusal"] == XR.R_RAISED
    assert await db.fetchval("SELECT count(*) FROM bettor_xavier_reviews "
                             " WHERE review_date='2031-03-10'") == 0
    st = json.loads(await db.fetchval(
        "SELECT value::text FROM ingestion_state WHERE key=$1",
        XR.STATE_KEY))
    assert "synthetic failure" in st["last_failure"]["error"]
    monkeypatch.undo()
    retry = await XR.daily_review(db, now=T + 900)
    assert retry["ran"] is True


# ═════════════════════════════════════════════════════════════════════
# 3 · OUTCOMES FROM THE LEDGER, STATISTICS PER FIXTURE
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_outcome_is_joined_from_the_economics_ledger(db):
    won = await _position(db, 1, long_price=1.0)
    await _decide(db, won, decided=T - 2 * DAY, p=0.6)
    prov = await _position(db, 3, long_price=1.0, provisional_fee=True)
    await _decide(db, prov, decided=T - 2 * DAY, p=0.6)
    held = await _position(db, 2, open_=True)       # the one open group, last
    await _decide(db, held, decided=T - 2 * DAY, p=0.6)
    got = await XR.daily_review(db, now=T)
    assert got["ran"], got
    r = await _stored(db)
    assert r["decisions_reviewed"] == 3
    assert r["decisions_with_outcomes"] == 1
    counts = r["per_action"]["outcome_status_counts"]
    assert counts[XR.O_AUTHORITATIVE] == 1
    assert counts[XR.O_OPEN] == 1
    assert counts[XR.O_NOT_FINAL] == 1
    top = r["errors"]["largest"][0]
    # predicted HOLD = 10 x 0.6 - 5 = 1.0; realised forward = 10 - 5 = 5.0;
    # the ledger's plain sum also carries the sunk 0.10 entry fee
    assert top["predicted_usd"] == pytest.approx(1.0)
    assert top["realised_usd"] == pytest.approx(5.0)
    assert top["realised_whole_position_net_usd"] == pytest.approx(4.9)
    assert top["error_usd"] == pytest.approx(4.0)
    assert top["cause"] == XR.C_PROBABILITY
    assert "remaining basis" in top["realised_basis"]


@pg
async def test_statistics_give_each_fixture_one_weight(db):
    a = await _position(db, 1, long_price=1.0)
    for i, p in enumerate((0.6, 0.7, 0.8)):
        await _decide(db, a, decided=T - 2 * DAY - i * 3600, p=p)
    b = await _position(db, 2, long_price=0.0)
    await _decide(db, b, decided=T - 2 * DAY, p=0.4)
    await XR.daily_review(db, now=T)
    r = await _stored(db)
    allx = r["per_action"]["all"]
    assert allx["decisions"] == 4 and allx["fixtures"] == 2
    # A: errors 4, 3, 2 -> one weight, mean 3; B: -4. Per fixture: -0.5
    assert allx["mean_error_usd"] == pytest.approx(-0.5)
    assert allx["decision_weighted_for_reference_only"][
        "mean_error_usd"] == pytest.approx(1.25)
    assert allx["status"] == "INSUFFICIENT_EVIDENCE"
    assert allx["weighting"] == "ONE_WEIGHT_PER_FIXTURE"
    rel = r["reliability"]
    assert rel["fixtures"] == 2 and rel["decisions"] == 4
    assert rel["brier"] == pytest.approx(((0.29 / 3) + 0.16) / 2, abs=1e-6)
    # the largest errors are one per fixture, not three from one position
    fixtures = [e["fixture"] for e in r["errors"]["largest"]]
    assert sorted(fixtures) == sorted({a["fixture"], b["fixture"]})


# ═════════════════════════════════════════════════════════════════════
# 4 · ALTERNATIVES: LABELLED, BLOCKED ONES WITHOUT AN ESTIMATE
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_alternatives_are_labelled_estimates_and_never_results(db):
    a = await _position(db, 1, long_price=1.0)
    xa = await _decide(db, a, decided=T - 2 * DAY, p=0.6)
    c = await _position(db, 2, long_price=0.0)
    alts = [{"action": "HOLD", "qty": 10, "value_usd": -1.0,
             "cash_at_settlement_usd": 4.0},
            {"action": "DIRECT_EXIT", "qty": 10, "value_usd": -0.6,
             "slice_value_usd": -0.6, "fees_usd": 0.1,
             "plan_digest": "pd-c"},
            {"action": "ACQUIRE_HEDGE", "value_usd": -0.8, "units": 10,
             "hedge_slug": PFX + "never-settled", "hedge_order_intent": LONG,
             "cost_usd": 9.9, "fees_usd": 0.1}]
    xc = await _decide(db, c, decided=T - 2 * DAY, p=0.4, alts=alts,
                       chosen="DIRECT_EXIT", digest="pd-c")
    await XR.daily_review(db, now=T)
    r = await _stored(db)
    rows = r["alternatives"]["rows"]
    mine = {(x["xavier_decision_id"], x["action"]): x for x in rows}
    ex = mine[(xa, "DIRECT_EXIT")]
    assert ex["kind"] == XR.KIND_HYPOTHETICAL
    assert ex["estimate_usd"] == pytest.approx(0.9)
    assert ex["fill_basis"] == XR.FILL_BASIS_DISPLAYED
    assert ex["could_have_filled"] == "UNPROVEN"
    assert ex["eventual_outcome_known"] is True
    assert "not a result" in ex["not_a_result"]
    red = mine[(xa, "REDUCE")]
    assert red["kind"] == XR.KIND_BLOCKED and red["estimate_usd"] is None
    assert red["blocker"] == "NO_EXECUTABLE_DEPTH"
    hold_c = mine[(xc, "HOLD")]
    assert hold_c["estimate_usd"] == pytest.approx(-5.0)
    assert hold_c["fill_basis"] == XR.FILL_BASIS_NO_ORDER
    acq = mine[(xc, "ACQUIRE_HEDGE")]
    assert acq["estimate_usd"] is None
    assert acq["no_estimate_because"] == XR.NE_HEDGE_OUTCOME_UNKNOWN
    # THE CHOSEN ACTION IS NEVER ITS OWN ALTERNATIVE
    assert (xa, "HOLD") not in mine and (xc, "DIRECT_EXIT") not in mine
    # every estimated row is labelled; no row claims a result
    for x in rows:
        if x["estimate_usd"] is not None:
            assert x["kind"] == XR.KIND_HYPOTHETICAL
            assert x["could_have_filled"] == "UNPROVEN"
        assert not any(k.startswith("would") for k in x)
    assert r["alternatives"]["no_verdict_on_which_action_was_right"] is True
    assert r["alternatives"]["by_action"]["DIRECT_EXIT"]["estimated"] == 1
    # C's exit was never sent: the realised path is the held position
    errs = {e["xavier_decision_id"]: e for e in r["errors"]["largest"]}
    assert errs[xc]["cause"] == XR.C_EXECUTION


# ═════════════════════════════════════════════════════════════════════
# 5 · A CONTRADICTED SETTLEMENT INVALIDATES
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_disagreeing_settlement_reread_invalidates_the_decision(db):
    a = await _position(db, 1, long_price=1.0)
    await _decide(db, a, decided=T - 2 * DAY, p=0.6)
    d = await _position(db, 2, long_price=1.0)
    xd = await _decide(db, d, decided=T - 2 * DAY, p=0.6)
    await db.execute(
        "INSERT INTO bettor_funded_settlement_rechecks (intent_id, read_at, "
        " booked_reading, booked_payout_price, venue_reading, "
        " venue_payout_price, verdict, why) VALUES ($1, $2, "
        " 'REPORTED_SETTLEMENT', 1, 'EXPLICIT_VOID', NULL, 'DISAGREES', "
        " 'synthetic test re-read')", d["intent_id"], _ts(T - DAY / 2))
    await XR.daily_review(db, now=T)
    r = await _stored(db)
    inv = [x for x in r["invalidated"] if x["xavier_decision_id"] == xd]
    assert inv and inv[0]["reason"] == XR.INV_VENUE_DISAGREES
    assert r["per_action"]["outcome_status_counts"][XR.O_INVALIDATED] == 1
    assert r["per_action"]["all"]["decisions"] == 1
    assert xd not in {e["xavier_decision_id"]
                      for e in r["errors"]["largest"]}
    assert xd not in {x["xavier_decision_id"]
                      for x in r["alternatives"]["rows"]}
    assert r["window"]["invalidated_total"] == 1
    # the next cycle's skip carries the same count into the heartbeat
    skip = await XR.daily_review(db, now=T + 60)
    assert skip["refusal"] == XR.R_ALREADY_REVIEWED
    assert skip["digest"]["invalidated"] == 1


# ═════════════════════════════════════════════════════════════════════
# 6 · THE MODEL SUMMARY NEVER PROMOTES
# ═════════════════════════════════════════════════════════════════════

async def _model(c, mid, *, key, state, evaluation=None, provenance=None):
    now = _ts(T - 10 * DAY)
    await c.execute(
        "INSERT INTO bettor_funded_models (model_id, model_key, "
        " model_version, state, kernel, estimator, features, params, "
        " fit_through, train_rows, evaluation, approved_at, approved_by, "
        " training_provenance, trained_through, outcomes_available_through) "
        " VALUES ($1,$2,$3,$4,'TEST','RIDGE_LOGISTIC',ARRAY['x'],"
        " '{}'::jsonb,$5,40,$6::jsonb,$7,$8,$9::jsonb,$10,$11)",
        mid, key, mid + "-v", state, now,
        None if evaluation is None else json.dumps(evaluation),
        now if state == "APPROVED" else None,
        "synthetic-approver" if state == "APPROVED" else None,
        None if provenance is None else json.dumps(provenance),
        now if provenance else None, now if provenance else None)


@pg
async def test_the_model_summary_never_promotes_and_only_withdraws(db):
    ev = {"evaluated_at": T - DAY, "contamination": {"verdict": "CLEAN"},
          "promotable_evidence": False,
          "PROSPECTIVE": {"n_events": 3, "log_loss": 0.61,
                          "report": {"baseline": {"log_loss": 0.69}}}}
    await _model(db, PFX + "cand", key=MODEL_KEY, state="CANDIDATE",
                 evaluation=ev)
    # AN APPROVED MODEL WHOSE RECORDS NO LONGER EXIST: its approval rests on
    # decisions that are not there, so the review's withdrawal retires it.
    await _model(db, PFX + "appr", key=MODEL_KEY + "_b", state="APPROVED",
                 evaluation=ev, provenance={
                     "kind": "RECORDS", "decision_ids": [PFX + "gone"],
                     "records_sha": "0" * 64, "weighting": "EVENT_BALANCED",
                     "n_events": 40})
    await XR.daily_review(db, now=T)
    r = await _stored(db)
    m = r["models"]
    assert m["promoted_anything"] is False
    assert "promote()" in m["promotion"]
    cand = m["keys"][MODEL_KEY]["models"][0]
    assert cand["state"] == "CANDIDATE"
    assert cand["prospective_events"] == 3
    assert cand["baseline_log_loss"] == pytest.approx(0.69)
    assert cand["contamination"] == "CLEAN"
    assert cand["evidence"]["reproduces"] is False
    assert m["keys"][MODEL_KEY]["withdraw"]["approved_model"] is None
    w = m["keys"][MODEL_KEY + "_b"]["withdraw"]
    assert w["withdrawn"] is True
    states = {x["model_id"]: x["state"] for x in await db.fetch(
        "SELECT model_id, state FROM bettor_funded_models "
        " WHERE model_key LIKE $1", MODEL_KEY + "%")}
    assert states == {PFX + "cand": "CANDIDATE", PFX + "appr": "RETIRED"}
    assert await db.fetchval(
        "SELECT count(*) FROM bettor_funded_models WHERE model_key LIKE $1 "
        " AND state='APPROVED'", MODEL_KEY + "%") == 0


# ═════════════════════════════════════════════════════════════════════
# 7 · APPEND-ONLY, AND THE CYCLE'S HOOK
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_reviews_are_append_only(db):
    import asyncpg

    await XR.daily_review(db, now=T)
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await db.execute("UPDATE bettor_xavier_reviews SET summary='x' "
                         " WHERE review_date='2031-03-10'")
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await db.execute("DELETE FROM bettor_xavier_reviews "
                         " WHERE review_date='2031-03-10'")


@pg
async def test_a_stopped_cycle_with_no_account_still_beats_the_review(
        db, monkeypatch):
    from sportsassets import bettor_funded_book as FB
    from sportsassets.workers import ext_pinnacle_loop as L

    seen = {}

    async def _review(conn, **kw):
        seen.update(kw)
        return {"ok": True, "ran": False, "refusal": XR.R_ALREADY_REVIEWED,
                "review_date": "2031-03-10",
                "digest": {"last_review_date": "2031-03-10",
                           "decisions_reviewed": 7,
                           "decisions_with_outcomes": 2, "invalidated": 1,
                           "next_due_at": T + DAY}}

    async def _stopped(c):
        return False, "the observation stop is engaged"

    async def _no_account(c, *, now):
        return None

    monkeypatch.setattr(XR, "daily_review", _review)
    monkeypatch.setattr(L, "_running", _stopped)
    monkeypatch.setattr(L, "_funded_service", _no_account)
    try:
        out = await L.cycle(db)
        assert out["state"] == "STOPPED"
        beat = json.loads(await db.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key=$1",
            FB.SCHEDULED_CYCLE_KEY))
        xr = beat["xavier_review"]
        assert xr["last_review_date"] == "2031-03-10"
        assert xr["decisions_reviewed"] == 7
        assert xr["invalidated"] == 1
        assert xr["next_due_at"] == pytest.approx(T + DAY)
        # EVERY ACCOUNT, not only a bound one: no filter was passed
        assert set(seen) == {"now"}
    finally:
        await db.execute("DELETE FROM ingestion_state WHERE key=$1",
                         FB.SCHEDULED_CYCLE_KEY)


async def test_the_hook_is_never_fatal(monkeypatch):
    from sportsassets.workers import ext_pinnacle_loop as L

    async def _boom(conn, **kw):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(XR, "daily_review", _boom)
    got = await L._xavier_daily_review(None, now=T)
    assert got["ok"] is False and got["refusal"] == XR.R_RAISED
    assert L._xavier_review_digest(None) is None
    d = L._xavier_review_digest(got)
    assert d["refusal"] == XR.R_RAISED and d["ran"] is False


async def test_without_the_table_the_review_refuses_by_name():
    class _NoTable:
        async def fetchval(self, *a, **k):
            return None

    got = await XR.daily_review(_NoTable(), now=T)
    assert got["ok"] is False and got["refusal"] == XR.R_SCHEMA
