"""THE ACQUISITION BEING SUBMITTED IS ONE ACQUISITION, NOT TWO.

    "acquire_second_leg() creates a HELD reservation before calling
     submit_for_decision(). check_rails() then adds all HELD reservations AND
     plan['collateral_usd']. For the operation being submitted, those are the
     same acquisition. Existing exposure $10 plus a reserved $5 hedge must
     measure $15, not $20."

REPRODUCED FIRST, in `test_the_defect` below, against the arithmetic as it was.

── WHY THE DIRECTION OF THIS ERROR IS THE DANGEROUS ONE ─────────────

It OVERSTATES exposure, which sounds safe. It is not. A rail that refuses
affordable orders teaches its operator that the number is wrong, and the
response to a control that cries wolf is to raise the limit -- at which point
the rail no longer bounds the thing it was set to bound. A blind control that
looks conservative is still a blind control.

── THE FOUR POINTS THE BOUNDARY IS PROVED AT ────────────────────────

  BEFORE COMMITMENT   reservation HELD, no intent. The plan carries it; the
                      reservation sum leaves it out.
  AFTER COMMITMENT    reservation COMMITTED, intent exists. The intent carries
                      it; the reservation sum never counted past HELD. A
                      SUBMISSION for it is refused as a replay rather than
                      measured a third time.
  AFTER ACKNOWLEDGEMENT reservation CONSUMED, terminal, not live at all.
  AFTER RESTART       reservation AMBIGUOUS, still live and still claiming the
                      leg -- but past HELD, so the UNRESOLVED intent carries
                      the exposure and the reservation does not.

At every one of them the account's measured exposure for this acquisition is
$5, never $10.

── AND THE EXCLUSION IS EARNED ──────────────────────────────────────

It applies only when the plan and the reservation agree on the instrument, the
quantity, the price and the collateral. A plan for $50 may not hide behind a
reservation for $5, and a mismatch is a NAMED REFUSAL rather than an
arithmetic adjustment or an unmeasured rail.

    NO FUNDED ORDER IS SENT. NO VENUE IS CONTACTED.
"""

from __future__ import annotations

import json
import time

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_reservations as RSV

DSN = __import__("os").environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

ACCT = "acct-funded-DEMONSTRATION-countonce"
VENUE = "PMUS"
EVENT = "ev-countonce-2026-09-28"
SLUG_A = "aec-countonce-primary"
SLUG_B = "aec-countonce-hedge"
GROUP = "grp:fpi-countonce-primary"
PRIMARY = "fpi-countonce-primary"
OP = "op:countonce"

#: A $10 PRIMARY AND A $5 HEDGE, chosen so the arithmetic in the owner's
#: sentence is the arithmetic here: 10 + 5 = 15, and the defect gave 20.
PRIMARY_QTY, PRIMARY_PX = 20, 0.50       # $10.00 collateral
HEDGE_QTY, HEDGE_PX = 20, 0.25           # $5.00 collateral
LIMITS = {"capital_usd": 400, "per_order_usd": 60, "event_exposure_usd": 60,
          "max_exposure_usd": 200, "daily_loss_stop_usd": 40}
EMPTY_VENUE = {"held_usd": 0.0, "working_usd": 0.0, "unresolved_usd": 0.0}


# ════════════════════════════════════════════════════════════════════
# HARNESS
# ════════════════════════════════════════════════════════════════════

class _Orders:
    def __init__(self, sent, *, executions=None, raise_on_create=None):
        self.sent, self.creates = sent, 0
        self._exec, self._raise = executions, raise_on_create

    def preview(self, body):
        req = (body or {}).get("request") or {}
        self.sent.append(("preview", req))
        px = float((req.get("price") or {}).get("value") or 0)
        qty = int(req.get("quantity") or 0)
        return {"order": {"price": req.get("price"),
                          "quantity": req.get("quantity"),
                          "cashOrderQty": {"value": "%.4f" % (px * qty),
                                           "currency": "USD"}}}

    def create(self, params):
        self.sent.append(("create", dict(params)))
        self.creates += 1
        if self._raise is not None:
            raise self._raise
        return {"id": "venue-countonce-%d" % self.creates,
                "executions": list(self._exec or ())}

    def list(self, params=None):
        self.sent.append(("list", dict(params or {})))
        return {"orders": []}

    def retrieve(self, order_id):
        return None

    def cancel(self, order_id, body=None):
        return {}


class _Client:
    def __init__(self, sent, **kw):
        self.orders = _Orders(sent, **kw)


def _transport(monkeypatch, **kw):
    from sportsassets import pmus
    sent: list = []
    client = _Client(sent, **kw)
    monkeypatch.setattr(pmus, "_get_client", lambda: client)
    monkeypatch.setattr(pmus._gate, "authorize", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
    monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
    return pmus, sent, client


def _fill(qty, px, *, vid="vf-1", state="ORDER_STATE_FILLED"):
    return {"id": vid, "type": "EXECUTION_TYPE_FILL",
            "lastPx": {"value": "%.2f" % px, "currency": "USD"},
            "lastShares": qty, "order": {"state": state}}


async def _seed(conn):
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    await conn.execute(
        "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
        " paused, accounting_status, opening_balance, note) VALUES "
        "($1,'desk-countonce','ACTIVE',FALSE,'RECONCILED',0,'count once')",
        ACCT)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb", FA.LIMITS_KEY,
        json.dumps({"proposed": dict(LIMITS), "approved": True,
                    "approved_by": "OWNER"}))
    now = time.time()
    eff = EX.effective_limits(dict(LIMITS))
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
        FA.AUTHORIZATION_KEY,
        json.dumps({"account_id": ACCT, "venue": VENUE,
                    "venue_class": FA.VENUE_FUNDED, "by": "countonce",
                    "at": now, "expires_at": now + 3600.0, "revoked": False,
                    "effective_limits": eff["effective"],
                    "effective_digest": eff["effective_digest"]}))
    return eff


async def _clean(conn):
    await conn.execute(
        "DELETE FROM bettor_funded_operation_evidence WHERE account_id=$1", ACCT)
    for t in ("bettor_funded_economics", "bettor_funded_discrepancies",
              "bettor_funded_fills"):
        await conn.execute(
            "DELETE FROM %s WHERE intent_id IN (SELECT intent_id FROM "
            "bettor_funded_intents WHERE account_id=$1)" % t, ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_leg_reservations WHERE group_id IN "
        "(SELECT group_id FROM bettor_funded_portfolio_groups "
        "  WHERE account_id=$1)", ACCT)
    await conn.execute("DELETE FROM bettor_funded_intents WHERE account_id=$1",
                       ACCT)
    await conn.execute(
        "DELETE FROM bettor_funded_portfolio_groups WHERE account_id=$1", ACCT)
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    for k in (FA.AUTHORIZATION_KEY, FA.LIMITS_KEY, FA.ACCOUNT_KEY):
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1", k)


async def _primary(conn):
    """A $10 FILLED PRIMARY LEG, through the book's own writer."""
    coll = FX.collateral_for(PRIMARY_PX, PRIMARY_QTY, FX.LONG)
    assert coll == pytest.approx(10.0), coll
    got = await FB.record_intent(
        conn, intent_id=PRIMARY, account_id=ACCT, venue=VENUE,
        venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG_A, event_key=EVENT,
        order_intent=FX.LONG, limit_price=PRIMARY_PX, quantity=PRIMARY_QTY,
        collateral_usd=coll, effective_digest="d", payout_event="SIDE_A",
        held_is_long=True, portfolio_group_id=None, leg_role="PRIMARY",
        group_structure="INDIRECT_MIDDLE")
    assert got.get("ok"), got
    assert got["portfolio_group_id"] == GROUP
    await FB.record_acknowledgement(conn, PRIMARY, venue_order_id="vo-primary",
                                    status="open")
    await FB.ingest_fills(conn, PRIMARY,
                          [{"qty": float(PRIMARY_QTY), "price": PRIMARY_PX,
                            "venue_fill_id": "vf-primary"}])
    return GROUP


def _plan():
    return {"us_market_slug": SLUG_B, "event_key": EVENT, "intent": FX.LONG,
            "limit_price": HEDGE_PX, "quantity": HEDGE_QTY,
            "collateral_usd": FX.collateral_for(HEDGE_PX, HEDGE_QTY, FX.LONG),
            "sell": False, "tif": FX.TIF, "post_only": False,
            "payout_event": "SIDE_B", "held_is_long": True,
            "sized_from": {"size": HEDGE_QTY, "vwap": HEDGE_PX}}


def _decision_record(*, qty=HEDGE_QTY, px=HEDGE_PX, slug=SLUG_B):
    return {"admissible": True, "refusals": [], "us_market_slug": slug,
            "event_key": EVENT, "order_intent": FX.LONG,
            "payout_event": "SIDE_B",
            "execution_plan": {"execution": {"size": qty, "vwap": px,
                                            "limit_price": px}}}


async def _hold(conn, *, qty=HEDGE_QTY, px=HEDGE_PX, slug=SLUG_B,
                collateral=None, op=OP):
    coll = (FX.collateral_for(px, qty, FX.LONG) if collateral is None
            else collateral)
    got = await RSV.hold(conn, operation_id=op, group_id=GROUP,
                         leg_role="HEDGE", us_market_slug=slug, quantity=qty,
                         limit_price=px, collateral_usd=coll)
    assert got.get("ok"), got
    return coll


async def _committed_capital(conn) -> dict:
    """THE ACCOUNT'S COMMITTED CAPITAL FOR WHAT IT ALREADY HOLDS.

    NOT a rail reading. Every rail in `check_rails` includes
    `plan['collateral_usd']` -- correctly, because a rail bounds "this order on
    top of the book" -- so a rail is the wrong instrument for "how much does the
    book carry right now", which is what this file's four-point claim is about.

    It is `pending_and_in_flight_collateral_usd` plus HELD reservations, and
    NOTHING ELSE. Adding `filled_cash_usd` on top double-counts a filled
    position -- measured: a $10 filled-and-held primary leg reported $10 of cash
    AND $10 of at-risk collateral, so the naive sum read $20.35 for one $10 leg.
    `pending_and_in_flight_collateral_usd` is already the COMPLETE at-risk
    figure: the whole clip while an order is outstanding, and the residual
    pro rata once it is terminal and the contracts are still held.

    It is stable across all four states: what changes is WHICH term the hedge's
    $5 sits in, and it must sit in exactly one.
    """
    exp = await FB.exposure(conn, account_id=ACCT, venue=VENUE)
    res = await RSV.reserved_collateral_usd(conn, account_id=ACCT)
    assert res["ok"], res
    return {
        "total": round(float(exp["pending_and_in_flight_collateral_usd"])
                       + float(res["reserved_usd"]), 6),
        "pending_intents": float(exp["pending_and_in_flight_collateral_usd"]),
        "held_reservations": float(res["reserved_usd"]),
        "cash_and_fees_reported_separately": round(
            float(exp["filled_cash_usd"]) + float(exp["filled_fees_usd"]), 6),
    }


async def _correlated(conn, *, operation_id=None, plan=None):
    """MAX_CORRELATED_EXPOSURE, which is the rail the owner's numbers name."""
    eff = EX.effective_limits(dict(LIMITS))
    rails = await FX.check_rails(conn, plan or _plan(), eff["effective"],
                                account_id=ACCT, venue=VENUE,
                                operation_id=operation_id)
    if not rails.get("ok", True):
        return rails
    by = {r["rail"]: r for r in rails["rails"]}
    return dict(rails, correlated=by.get("MAX_CORRELATED_EXPOSURE"))


# ════════════════════════════════════════════════════════════════════
# 1 · THE DEFECT, REPRODUCED
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_defect_without_the_operation_identity_it_is_counted_twice():
    """THE ARITHMETIC IN THE OWNER'S SENTENCE, MEASURED BOTH WAYS.

    `operation_id=None` is the old call, and it is kept reachable on purpose --
    a submission that took no reservation is correct to sum every HELD claim and
    add its own plan. What was wrong was using that path for a submission whose
    own reservation is in the sum.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _primary(conn)
        await _hold(conn)

        # ── THE OLD ARITHMETIC: $10 live + $5 reserved + $5 plan = $20 ──
        old = await _correlated(conn, operation_id=None)
        assert old["correlated"]["measured"] == pytest.approx(20.0), (
            "this is the reproduction; if it is not 20 the defect is not here")

        # ── AND THE REPAIRED ONE: $10 live + $5 plan = $15 ──────────
        new = await _correlated(conn, operation_id=OP)
        assert new["correlated"]["measured"] == pytest.approx(15.0), new
        ex = new["the_submitted_operation_is_counted_once"]
        assert ex["operation_id"] == OP
        assert ex["collateral_usd"] == pytest.approx(5.0)
        assert ex["counted_by"] == "THE_PLANS_OWN_COLLATERAL"
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_every_other_reservation_is_still_counted():
    """THE EXCLUSION IS ONE OPERATION, NOT THE WHOLE READING. A second live claim
    on the account must still raise the measured exposure -- otherwise the repair
    would have replaced a double count with a blind spot."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _primary(conn)
        await _hold(conn)
        # A SECOND LIVE CLAIM ON THE ACCOUNT: $3 on another instrument.
        #
        # IT SITS IN THE SAME GROUP, and it has to: migration 131 permits ONE
        # OPEN GROUP per lane, so a second group cannot exist while this one
        # holds a position. The reading under test is ACCOUNT-WIDE, so what
        # matters is that a second live reservation exists at all -- it is
        # carried as the group's PRIMARY-role claim, which the one-live-per-role
        # index leaves free because the filled primary LEG holds no reservation.
        other = await RSV.hold(
            conn, operation_id="op:countonce-other", group_id=GROUP,
            leg_role="PRIMARY", us_market_slug="aec-countonce-other",
            quantity=12, limit_price=0.25, collateral_usd=3.0)
        assert other.get("ok"), other
        got = await _correlated(conn, operation_id=OP)
        # $10 live + $3 other reservation + $5 plan = $18.
        assert got["correlated"]["measured"] == pytest.approx(18.0), got
        assert "still summed" in got["reservation_reading"][
            "every_other_reservation_is_retained"]
        assert got["reservation_reading"][
            "excluded_because_the_plan_counts_it"]["operation_id"] == OP
        assert got["reservation_reading"]["held_reserved_usd"] == \
            pytest.approx(3.0), (
            "the other claim, and only the other claim")
    finally:
        await _clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 2 · THE EXCLUSION IS EARNED
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("field,changed", [
    ("us_market_slug", {"us_market_slug": "aec-countonce-somewhere-else"}),
    ("quantity", {"quantity": 40}),
    ("limit_price", {"limit_price": 0.40}),
    ("collateral_usd", {"collateral_usd": 50.0}),
])
async def test_a_plan_that_disagrees_with_its_reservation_is_refused(field,
                                                                    changed):
    """A PLAN FOR $50 MAY NOT HIDE BEHIND A RESERVATION FOR $5.

    The exclusion rests on the two naming ONE acquisition. Where they do not, the
    answer is a named refusal -- not an arithmetic adjustment, and not an
    unmeasured rail, which would send a reader to look at the rails instead of at
    the identity mismatch that actually stopped the order.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _primary(conn)
        await _hold(conn)
        got = await _correlated(conn, operation_id=OP,
                                plan=dict(_plan(), **changed))
        assert got["ok"] is False, got
        assert got["refusal"] == RSV.R_PLAN_DOES_NOT_MATCH_RESERVATION
        assert [c["field"] for c in got["conflicts"]] == [field] or \
            field in [c["field"] for c in got["conflicts"]], got["conflicts"]
        # AND IT IS NOT REPORTED AS AN UNMEASURED RAIL.
        assert got["unmeasured"] == []
        assert got["rails"] == []
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_operation_with_no_reservation_is_refused_not_ignored():
    """TOLD TO NET AGAINST A RESERVATION THAT DOES NOT EXIST, this must refuse.
    Proceeding would count the acquisition NEITHER way -- the plan would be
    excluded from nothing and no reservation would carry it."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _primary(conn)
        got = await _correlated(conn, operation_id="op:countonce-absent")
        assert got["ok"] is False
        assert got["refusal"] == RSV.R_NO_SUCH_OPERATION
        assert "NEITHER way" in got["why"]
    finally:
        await _clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 3 · THE FOUR POINTS OF THE BOUNDARY
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_counted_once_before_commitment_after_it_after_ack_and_after_restart(
        monkeypatch):
    """ONE ACQUISITION, FOUR STATES, AND $5 AT EVERY ONE OF THEM.

    The $10 primary leg never moves. What moves is WHICH ROW carries the hedge's
    $5 -- and at no point may both carry it.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    seen = {}
    try:
        await _clean(conn)
        await _seed(conn)
        await _primary(conn)
        base = await _committed_capital(conn)
        assert base["total"] == pytest.approx(10.0), base
        seen["JUST_THE_PRIMARY"] = base

        # ── 1 · BEFORE COMMITMENT: HELD, no intent ──────────────────
        await _hold(conn)
        assert (await RSV.get(conn, OP))["reservation"]["state"] == RSV.HELD
        seen["BEFORE_COMMITMENT"] = await _committed_capital(conn)
        # THE HEDGE'S $5 IS IN THE RESERVATION TERM AND NOWHERE ELSE: the
        # intent term still holds only the primary leg's $10.
        assert seen["BEFORE_COMMITMENT"]["held_reservations"] == \
            pytest.approx(5.0)
        assert seen["BEFORE_COMMITMENT"]["pending_intents"] == \
            pytest.approx(10.0)
        # AND THE RAIL FOR THIS SUBMISSION READS $15, NOT $20.
        rail = await _correlated(conn, operation_id=OP)
        assert rail["correlated"]["measured"] == pytest.approx(15.0), rail

        # ── 2 · AFTER COMMITMENT: the intent carries it ─────────────
        #
        # The submission is driven through the production path, whose send fails
        # after leaving -- so the intent stands and the reservation goes
        # AMBIGUOUS. That gives states 2, 3 and 4 from one run rather than from
        # hand-written rows, which is the point: these are the states the
        # deployed code actually produces.
        _, sent, client = _transport(
            monkeypatch, raise_on_create=TimeoutError("answer lost"))
        got = await FX.submit_for_decision(
            conn, _decision_record(), account_id=ACCT, venue=VENUE,
            venue_positions=EMPTY_VENUE, operation_id=OP,
            portfolio_group_id=GROUP, leg_role="HEDGE")
        assert got["submitted"] is True, got
        assert got["refusal"] == FX.R_LOST_ACKNOWLEDGEMENT
        hedge_intent = got["intent_id"]
        assert client.orders.creates == 1
        # ── 4 · AFTER RESTART: AMBIGUOUS, still live, still claimed ──
        assert (await RSV.get(conn, OP))["reservation"]["state"] == \
            RSV.AMBIGUOUS
        seen["AFTER_COMMITMENT_AND_RESTART"] = await _committed_capital(conn)
        # THE $5 HAS MOVED TO THE INTENT AND LEFT THE RESERVATION TERM.
        assert seen["AFTER_COMMITMENT_AND_RESTART"][
            "held_reservations"] == pytest.approx(0.0)
        assert seen["AFTER_COMMITMENT_AND_RESTART"][
            "pending_intents"] == pytest.approx(15.0)

        # AND A RESUBMISSION OF THE SAME OPERATION IS REFUSED AS A REPLAY,
        # not measured a third time.
        replay = await FX.submit_for_decision(
            conn, _decision_record(), account_id=ACCT, venue=VENUE,
            venue_positions=EMPTY_VENUE, operation_id=OP,
            portfolio_group_id=GROUP, leg_role="HEDGE")
        assert replay["ok"] is False
        assert replay["refusal"] == RSV.R_NOT_HELD_SO_ALREADY_SUBMITTED
        assert replay["nothing_was_written"] is True
        assert client.orders.creates == 1, "a replay must not reach the venue"

        # ── 3 · AFTER ACKNOWLEDGEMENT: CONSUMED, terminal ───────────
        ev = await RSV.record_venue_evidence(
            conn, evidence_id="ev:countonce", operation_id=OP,
            account_id=ACCT, venue=VENUE, us_market_slug=SLUG_B,
            intent_id=hedge_intent, kind=RSV.EV_NAMED,
            venue_order_id="venue-countonce-1",
            search_endpoint="orders.list",
            search_scope={"account_id": ACCT, "us_market_slug": SLUG_B},
            covered_terminal_orders=True, results_returned=1,
            read_at=time.time())
        assert ev["ok"] is True, ev
        res = await RSV.resolve_from_the_venue(conn, operation_id=OP)
        assert res["ok"] is True, res
        assert (await RSV.get(conn, OP))["reservation"]["state"] == \
            RSV.CONSUMED
        await FB.record_acknowledgement(conn, hedge_intent,
                                        venue_order_id="venue-countonce-1",
                                        status="filled")
        await FB.ingest_fills(conn, hedge_intent,
                              [{"qty": float(HEDGE_QTY), "price": HEDGE_PX,
                                "venue_fill_id": "vf-hedge"}])
        seen["AFTER_ACKNOWLEDGEMENT"] = await _committed_capital(conn)
        # BOTH LEGS FILLED AND STILL HELD, so their collateral is the residual
        # pro rata -- the whole of it, since nothing has been sold -- and NO
        # reservation is live.
        assert seen["AFTER_ACKNOWLEDGEMENT"]["held_reservations"] == \
            pytest.approx(0.0)
        assert seen["AFTER_ACKNOWLEDGEMENT"]["pending_intents"] == \
            pytest.approx(15.0)

        # ══ THE CLAIM, AT ALL FOUR POINTS ═══════════════════════════
        #
        # $10 primary + $5 hedge = $15, and the fees the venue charged on top.
        # WHAT MOVES IS WHICH TERM CARRIES THE HEDGE'S $5 -- reservation, then
        # intent, then cash -- and at no point do two of them carry it.
        for label in ("BEFORE_COMMITMENT", "AFTER_COMMITMENT_AND_RESTART",
                      "AFTER_ACKNOWLEDGEMENT"):
            assert seen[label]["total"] == pytest.approx(15.0), (label, seen)
        assert seen["JUST_THE_PRIMARY"]["total"] == pytest.approx(10.0), seen
        # AND THE HEDGE'S $5 IS NEVER IN TWO TERMS AT ONCE.
        # AND THE HEDGE'S $5 IS NEVER IN TWO TERMS AT ONCE. The check is on the
        # TOTAL, because the primary leg's $10 always sits in the intent term:
        # a total above $15 while both legs exist is the double count, whichever
        # pair of terms produced it.
        for label, m in seen.items():
            if label == "JUST_THE_PRIMARY":
                continue
            assert m["total"] == pytest.approx(15.0), (
                "%s measured %s; anything above $15 is the hedge counted twice"
                % (label, m))
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_reservation_sum_never_counts_a_state_past_held():
    """THE INVARIANT THE WHOLE REPAIR RESTS ON. Past HELD the acquisition has an
    intent row, which the exposure reading already counts -- so if the
    reservation sum ever included one, the exclusion would be papering over a
    double count rather than removing one."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _primary(conn)
        await _hold(conn)
        assert (await RSV.reserved_collateral_usd(
            conn, account_id=ACCT))["reserved_usd"] == pytest.approx(5.0)
        # A REAL HEDGE INTENT, AND THE MACHINE'S OWN TRANSITION. Pointing the
        # HEDGE reservation at the PRIMARY intent is refused by 131's trigger --
        # "names intent ... which belongs to group ... role PRIMARY" -- and that
        # refusal is the leg-matching guard working, so the test uses a real
        # hedge leg rather than working around it.
        hedge = await FB.record_intent(
            conn, intent_id="fpi-countonce-hedge", account_id=ACCT,
            venue=VENUE, venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG_B,
            event_key=EVENT, order_intent=FX.LONG, limit_price=HEDGE_PX,
            quantity=HEDGE_QTY,
            collateral_usd=FX.collateral_for(HEDGE_PX, HEDGE_QTY, FX.LONG),
            effective_digest="d", payout_event="SIDE_B", held_is_long=True,
            portfolio_group_id=GROUP, leg_role="HEDGE")
        assert hedge.get("ok"), hedge
        bound = await RSV.commit_to_intent(conn, operation_id=OP,
                                          intent_id="fpi-countonce-hedge")
        assert bound.get("ok"), bound
        got = await RSV.reserved_collateral_usd(conn, account_id=ACCT)
        assert got["reserved_usd"] == pytest.approx(0.0), got
        assert [x["operation_id"] for x in got["live_but_not_counted"]] == [OP]
        assert got["counted_states"] == [RSV.HELD]
    finally:
        await _clean(conn)
        await conn.close()
