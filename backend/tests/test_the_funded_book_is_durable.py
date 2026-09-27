"""THE COUNTEREXAMPLES THE FUNDED PATH HAS TO SURVIVE.

Five properties, each with the defect it closes named, because each of these
was either wrong in the code I shipped or absent from it:

  1. TWO SWITCHES, BOTH GATING. The connector asked only for
     `authorization_consumed`, which is TRUE on the refusal
     `REAL_ORDER_SUBMISSION_IS_DISABLED_IN_CODE`. With the connector enabled
     and the execution module's constant False, it called the adapter anyway.

  2. THE COLLATERAL FORMULA MATCHES THE ADAPTER. `pmus.submit_fok` costs a
     BUY_SHORT at (1 - price) x qty. The connector used price x qty for every
     side, so above 0.50 it read LESS than the venue takes -- the direction
     that lets an order through the rails.

  3. ROUNDING CANNOT LOOSEN THE BOUND. `round(limit, 2)` rounds to nearest, so
     a sized 0.6351 became 0.64 and committed more per contract than the plan
     was admitted for.

  4. THE COMPLETE RAILS, MEASURED AGAINST PENDING EXPOSURE. Only
     MAX_MARKET_EXPOSURE was checked, and nothing that had not filled counted
     -- blind to exactly the window a second order does damage in.

  5. DURABILITY. A lost acknowledgement, a restart and a repeated delivery must
     not produce a blind resubmission or duplicate accounting, and an outcome
     the venue cannot establish must leave the exposure standing.
"""

from __future__ import annotations

import json
import time

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX

DSN = __import__("os").environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

ACCT = "acct-funded-durable-test"
VENUE = "PMUS"
SLUG = "aec-mlb-lad-sf-2026-09-27"
EVENT = "ev-lad-sf-2026-09-27"
LIMITS = {"capital_usd": 150, "per_order_usd": 10, "event_exposure_usd": 10,
          "max_exposure_usd": 30, "daily_loss_stop_usd": 25}


def _decision(**over):
    rec = {"admissible": True, "refusals": [], "us_market_slug": SLUG,
           "event_key": EVENT, "order_intent": FX.LONG,
           "execution_plan": {"execution": {"size": 15, "vwap": 0.62,
                                            "limit_price": 0.62}}}
    rec.update(over)
    return rec


# ── THE VENUE'S TRANSPORT, SUBSTITUTED AT `pmus._get_client` ────────

class _Orders:
    def __init__(self, sent, *, execs=None, order_id="venue-ord-1",
                 raise_on_create=None, preview_cost=None):
        self.sent = sent
        self._execs = execs
        self._oid = order_id
        self._raise = raise_on_create
        self._cost = preview_cost

    def preview(self, body):
        req = (body or {}).get("request") or {}
        self.sent.append(("preview", req))
        cost = self._cost
        if cost is None:
            px = float((req.get("price") or {}).get("value") or 0)
            qty = int(req.get("quantity") or 0)
            cost = ((1.0 - px) * qty
                    if req.get("intent") == "ORDER_INTENT_BUY_SHORT"
                    else px * qty)
        return {"order": {"price": req.get("price"),
                          "quantity": req.get("quantity"),
                          "cashOrderQty": {"value": "%.4f" % cost,
                                           "currency": "USD"}}}

    def create(self, params):
        self.sent.append(("create", dict(params)))
        if self._raise is not None:
            raise self._raise
        qty = int(params.get("quantity") or 0)
        px = float((params.get("price") or {}).get("value") or 0)
        execs = self._execs
        if execs is None:
            execs = [{"id": "vf-1", "type": "EXECUTION_TYPE_FILL",
                      "lastPx": {"value": "%.2f" % px, "currency": "USD"},
                      "lastShares": qty,
                      "order": {"state": "ORDER_STATE_FILLED"}}]
        return {"id": self._oid, "executions": execs}


class _Markets:
    def retrieve_by_slug(self, slug):
        return {"market": {"marketSides": [
            {"identifier": slug + "-a", "description": "A"},
            {"identifier": slug + "-b", "description": "B"}]}}


class _Client:
    def __init__(self, sent, **kw):
        self.orders = _Orders(sent, **kw)
        self.markets = _Markets()


def _transport(monkeypatch, **kw):
    from sportsassets import pmus
    sent: list = []
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(sent, **kw))
    monkeypatch.setattr(pmus._gate, "authorize", lambda *a, **k: {"ok": True})
    return pmus, sent


async def _seed(conn, *, limits=None, expires_in=3600.0):
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    await conn.execute(
        "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
        " paused, accounting_status, opening_balance, note) VALUES "
        "($1,'desk-durable-test','ACTIVE',FALSE,'RECONCILED',0,'durable test')",
        ACCT)
    lim = dict(limits or LIMITS)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb", FA.LIMITS_KEY,
        json.dumps({"proposed": lim, "approved": True,
                    "approved_by": "OWNER"}))
    now = time.time()
    eff = EX.effective_limits(lim)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
        FA.AUTHORIZATION_KEY,
        json.dumps({"account_id": ACCT, "venue": VENUE,
                    "venue_class": FA.VENUE_FUNDED, "by": "test",
                    "at": now, "expires_at": now + expires_in,
                    "revoked": False,
                    "effective_limits": eff["effective"],
                    "effective_digest": eff["effective_digest"]}))


async def _clean(conn):
    # ECONOMICS FIRST: it carries a foreign key to the intents.
    await conn.execute("DELETE FROM bettor_funded_economics")
    await conn.execute("DELETE FROM bettor_funded_fills")
    await conn.execute("DELETE FROM bettor_funded_intents")
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    for k in (FA.AUTHORIZATION_KEY, FA.LIMITS_KEY):
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1", k)


# ── 1 · BOTH SWITCHES GATE ──────────────────────────────────────────

@pg
@pytest.mark.asyncio
async def test_the_connector_enabled_with_submission_off_calls_nothing(
        monkeypatch):
    """COUNTEREXAMPLE 1, and it is the defect I shipped.

    `authorization_consumed` is set TRUE immediately BEFORE
    `REAL_ORDER_SUBMISSION_ENABLED` is consulted -- it means "the record was
    read and matched", not "you may submit". Asking only for it walked past
    the execution module's own switch.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        pmus, sent = _transport(monkeypatch)
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
        assert EX.REAL_ORDER_SUBMISSION_ENABLED is False

        got = await FX.submit_for_decision(conn, _decision(),
                                           account_id=ACCT, venue=VENUE)
        assert got["ok"] is False
        assert got["refusal"] == FX.R_GATE_NOT_AFFIRMATIVE, got
        assert got["owner_side_satisfied"] is True
        assert got["execution_gate_affirmative"] is False
        assert got["gate_refusal"] == EX.R_SUBMISSION_DISABLED
        # ZERO ADAPTER CALLS
        assert sent == [], sent
        # AND NO INTENT WAS WRITTEN, because the refusal came first
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents") == 0
        assert got["intent_id"] is None
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_connectors_own_switch_gates_independently(monkeypatch):
    """BOTH SWITCHES, SEPARATELY. With the execution module's constant enabled
    in-test and the CONNECTOR's switch off, the connector is what refuses --
    so neither switch is carrying the other."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        pmus, sent = _transport(monkeypatch)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        assert FX.FUNDED_SUBMISSION_ENABLED is False
        got = await FX.submit_for_decision(conn, _decision(),
                                           account_id=ACCT, venue=VENUE)
        assert got["refusal"] == FX.R_FUNDED_DISABLED, got
        assert got["execution_gate_affirmative"] is True
        assert sent == []
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents") == 0
    finally:
        await _clean(conn)
        await conn.close()


# ── 2 · THE SHORT'S COLLATERAL, AND 3 · SAFE ROUNDING ───────────────

def test_a_short_is_costed_the_way_the_adapter_costs_it():
    """COUNTEREXAMPLE 2. Read the adapter's own formula and match it."""
    import inspect

    from sportsassets import pmus

    src = inspect.getsource(pmus.submit_fok)
    assert "(1.0 - limit_price) * quantity" in src

    # the connector agrees, on both sides
    assert FX.collateral_for(0.78, 100, FX.LONG) == pytest.approx(78.0)
    assert FX.collateral_for(0.78, 100, "ORDER_INTENT_BUY_SHORT") == \
        pytest.approx(22.0)
    # THE DIRECTION THAT MATTERS: above 0.50 the old formula read LESS than
    # the venue takes on a short, which lets an order through the rails.
    assert FX.collateral_for(0.30, 100, "ORDER_INTENT_BUY_SHORT") == \
        pytest.approx(70.0)
    assert 0.30 * 100 < 70.0


def test_rounding_never_loosens_the_approved_bound():
    """COUNTEREXAMPLE 3. `round(x, 2)` rounds to NEAREST."""
    assert round(0.6351, 2) == 0.64                # the old behaviour
    # a LONG floors: we cannot overpay
    assert FX.safe_cent(0.6351, FX.LONG) == 0.63
    assert FX.safe_cent(0.6399, FX.LONG) == 0.63
    # a SHORT ceils: a higher wire price commits LESS collateral
    assert FX.safe_cent(0.6301, "ORDER_INTENT_BUY_SHORT") == 0.64
    assert FX.safe_cent(0.6399, "ORDER_INTENT_BUY_SHORT") == 0.64
    # and the collateral of the SENT price never exceeds the collateral of
    # the price the plan was sized at, on either side
    for asked in (0.0101, 0.5, 0.6351, 0.7777, 0.9899):
        for intent in (FX.LONG, "ORDER_INTENT_BUY_SHORT"):
            sent = FX.safe_cent(asked, intent)
            assert sent is not None, (asked, intent)
            assert FX.collateral_for(sent, 100, intent) <= \
                FX.collateral_for(asked, 100, intent) + 1e-9, (asked, intent)
    # a price that cannot be represented without loosening is REFUSED
    assert FX.safe_cent(0.004, FX.LONG) is None
    assert FX.safe_cent(0.999, "ORDER_INTENT_BUY_SHORT") is None
    plan = FX.plan_from_decision(_decision(
        execution_plan={"execution": {"size": 10, "vwap": 0.004,
                                      "limit_price": 0.004}}))
    assert plan["refusal"] == FX.R_PRICE_UNREPRESENTABLE


@pg
@pytest.mark.asyncio
async def test_a_short_at_a_longshot_price_is_admitted_on_the_right_number(
        monkeypatch):
    """THE SHORT THE OLD FORMULA WRONGLY REFUSED. 100 contracts of a 0.92
    favourite, shorted: the venue takes $8, and a $10 per-order rail covers
    it. The old `price x qty` read $92 and refused."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        pmus, sent = _transport(monkeypatch)
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        short = _decision(order_intent="ORDER_INTENT_BUY_SHORT",
                          execution_plan={"execution": {
                              "size": 100, "vwap": 0.92,
                              "limit_price": 0.92}})
        got = await FX.submit_for_decision(conn, short, account_id=ACCT,
                                           venue=VENUE)
        assert got["plan"]["collateral_usd"] == pytest.approx(8.0)
        assert got["submitted"] is True, got
        rail = {r["rail"]: r for r in got["rails"]["rails"]}
        assert rail["MAX_MARKET_EXPOSURE"]["measured"] == pytest.approx(8.0)
        assert rail["MAX_MARKET_EXPOSURE"]["verdict"] == "WITHIN"
        # the adapter received the short intent and costed it its own way
        create = [p for k, p in sent if k == "create"][0]
        assert create["intent"] == "ORDER_INTENT_BUY_SHORT"
    finally:
        await _clean(conn)
        await conn.close()


# ── 4 · THE COMPLETE RAILS, INCLUDING PENDING ───────────────────────

@pg
@pytest.mark.asyncio
async def test_every_effective_rail_is_checked_at_submission(monkeypatch):
    """COUNTEREXAMPLE 4a. Only MAX_MARKET_EXPOSURE was checked."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        pmus, sent = _transport(monkeypatch)
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
        got = await FX.submit_for_decision(conn, _decision(),
                                           account_id=ACCT, venue=VENUE)
        checked = {r["rail"] for r in got["rails"]["rails"]}
        assert checked == set(got["effective_limits"]["effective"]), checked
        assert got["rails"]["unmeasured"] == []
        assert got["rails"]["every_effective_rail_was_checked"] is True
        # every rail carries a number and a basis, not just a verdict
        for r in got["rails"]["rails"]:
            assert r["measured"] is not None, r
            assert r["basis"], r
        assert got["rails"]["counted_pending_and_in_flight"] is True
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_pending_exposure_counts_and_the_event_key_is_carried(
        monkeypatch):
    """COUNTEREXAMPLE 4b. A live intent that has not filled is collateral the
    venue may take at any moment. It counts at full size, per EVENT."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        # a live intent on the SAME event, nothing filled
        await FB.record_intent(
            conn, intent_id="fpi-pending", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug="aec-other-market",
            event_key=EVENT, order_intent=FX.LONG, limit_price=0.60,
            quantity=15, collateral_usd=9.0,
            effective_digest="d")
        exp = await FB.exposure(conn, account_id=ACCT, venue=VENUE)
        assert exp["pending_and_in_flight_collateral_usd"] == \
            pytest.approx(9.0)
        assert exp["filled_cash_usd"] == pytest.approx(0.0)
        assert exp["per_event_collateral_usd"][EVENT] == pytest.approx(9.0)

        pmus, sent = _transport(monkeypatch)
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
        got = await FX.submit_for_decision(conn, _decision(),
                                           account_id=ACCT, venue=VENUE)
        # $9 pending + $9.30 new = $18.30 against the $10 EVENT rail
        assert got["refusal"] == FX.R_OVER_RAIL, got
        over = {o["rail"] for o in got["over"]}
        assert "MAX_EVENT_EXPOSURE" in over, got["over"]
        assert sent == []
        # AND THE EVENT KEY IS REQUIRED, never inferred
        noev = _decision()
        noev.pop("event_key")
        assert FX.plan_from_decision(noev)["refusal"] == FX.R_NO_EVENT_KEY
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_two_concurrent_submissions_cannot_both_reach_the_venue(
        monkeypatch):
    """THE ONE-POSITION RULE, ENFORCED RATHER THAN PROPOSED.

    A SELECT-then-INSERT check cannot do this: both callers would read an
    empty book and both insert. The unique index over the live subset makes
    the second insert fail at the database, whatever the interleaving.
    """
    asyncpg = pytest.importorskip("asyncpg")
    import asyncio
    conn = await asyncpg.connect(DSN)
    other = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        pmus, sent = _transport(monkeypatch)
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)

        a, b = await asyncio.gather(
            FX.submit_for_decision(conn, _decision(), account_id=ACCT,
                                   venue=VENUE),
            FX.submit_for_decision(other, _decision(), account_id=ACCT,
                                   venue=VENUE),
            return_exceptions=True)
        results = [r for r in (a, b) if isinstance(r, dict)]
        assert len(results) == 2, (a, b)
        sent_ok = [r for r in results if r.get("submitted")]
        refused = [r for r in results
                   if r.get("refusal") == FB.R_ANOTHER_INTENT_IS_LIVE]
        assert len(sent_ok) == 1, [r.get("refusal") for r in results]
        assert len(refused) == 1, [r.get("refusal") for r in results]
        # EXACTLY ONE ORDER REACHED THE VENUE
        assert len([k for k, _ in sent if k == "create"]) == 1, sent
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents") == 1
    finally:
        await _clean(conn)
        await other.close()
        await conn.close()


# ── 5 · DURABILITY ─────────────────────────────────────────────────

@pg
@pytest.mark.asyncio
async def test_a_lost_acknowledgement_leaves_an_unresolved_intent_never_a_resend(
        monkeypatch):
    """COUNTEREXAMPLE 5a. The request left and raised. We do not know whether
    the venue holds an order, so the intent stays LIVE and UNRESOLVED -- the
    exposure is counted, the one-live guard keeps refusing, and nothing
    resends."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        pmus, sent = _transport(
            monkeypatch, raise_on_create=TimeoutError("read timed out"))
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)

        got = await FX.submit_for_decision(conn, _decision(),
                                           account_id=ACCT, venue=VENUE)
        assert got["refusal"] == FX.R_LOST_ACKNOWLEDGEMENT, got
        assert got["exposure"] == "PRESERVED"
        assert got["resubmitted_anything"] is False
        # the create was attempted exactly once
        assert len([k for k, _ in sent if k == "create"]) == 1
        row = await conn.fetchrow(
            "SELECT state, venue_order_id, unresolved_reason "
            "  FROM bettor_funded_intents")
        assert row["state"] == "UNRESOLVED"
        assert row["venue_order_id"] is None
        assert "unknown" in row["unresolved_reason"]
        # THE EXPOSURE IS COUNTED while unresolved
        exp = await FB.exposure(conn, account_id=ACCT, venue=VENUE)
        assert exp["pending_and_in_flight_collateral_usd"] > 0
        # AND A SECOND ATTEMPT IS REFUSED AND NOT SENT.
        #
        # It is the RAIL that refuses first, not the one-live guard, and that
        # is the pending-exposure fix doing its job: the unresolved intent's
        # $9.30 of collateral counts at full size, so $9.30 + $9.30 is $18.60
        # against a $10 event rail and the order never reaches the guard. The
        # guard is proved directly in the concurrency test above.
        again = await FX.submit_for_decision(conn, _decision(),
                                             account_id=ACCT, venue=VENUE)
        assert again["refusal"] == FX.R_OVER_RAIL, again
        rails = {r["rail"]: r for r in again["rails"]["rails"]}
        assert rails["MAX_EVENT_EXPOSURE"]["measured"] == pytest.approx(18.6)
        assert rails["MAX_CORRELATED_EXPOSURE"]["measured"] == \
            pytest.approx(18.6)
        assert len([k for k, _ in sent if k == "create"]) == 1

        # WITH RAILS THAT DO NOT BIND, the one-live guard is still what stops
        # it -- so the unresolved intent blocks new exposure by TWO
        # independent mechanisms and neither is carrying the other.
        await _seed(conn, limits=dict(LIMITS, per_order_usd=100,
                                      event_exposure_usd=100,
                                      max_exposure_usd=100,
                                      capital_usd=100))
        third = await FX.submit_for_decision(conn, _decision(),
                                             account_id=ACCT, venue=VENUE)
        assert third["refusal"] == FB.R_ANOTHER_INTENT_IS_LIVE, third
        assert len([k for k, _ in sent if k == "create"]) == 1
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_recovery_adopts_only_on_an_established_correlation():
    """COUNTEREXAMPLE 5b: RESTART. A fresh process finds the committed intent
    and reconciles it against the venue. `recover` has no send path at all.

    AND THE ADOPTION RULE CHANGED, because the old one was wrong. This test
    used to hand recovery a venue order carrying nothing but a market slug and
    assert that it was adopted. That is the defect: market coincidence is not
    ownership, and a manual or another strategy's order on the same market
    would have become ours. The same skeletal order is now REFUSED, and an
    order agreeing with every term of the request we sent is adopted.
    """
    asyncpg = pytest.importorskip("asyncpg")
    import inspect
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await FB.record_intent(
            conn, intent_id="fpi-lost", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG,
            event_key=EVENT, order_intent=FX.LONG, limit_price=0.62,
            quantity=15, collateral_usd=9.3, effective_digest="d")
        await FB.mark_send_attempted(conn, "fpi-lost")

        class _SlugOnly:
            """What the old rule adopted, and the new rule must not."""

            def open_orders(self):
                return [{"order_id": "venue-ord-9", "us_market_slug": SLUG}]

            def order_status(self, oid):
                return {"state": "open", "executions": []}

        got = await FB.recover(conn, _SlugOnly(), account_id=ACCT, venue=VENUE)
        assert got["ok"] is True
        assert got["resubmitted_anything"] is False
        assert got["reconciled"] == [], got
        assert got["unresolved"][0]["exposure"] == "PRESERVED"
        assert got["unresolved"][0]["correlation"]["refusal"] == \
            FB.R_NO_CORRELATED_ORDER
        assert await conn.fetchval(
            "SELECT venue_order_id FROM bettor_funded_intents "
            " WHERE intent_id='fpi-lost'") is None

        class _Venue:
            """Every term of the request we sent, present in the order."""

            def open_orders(self):
                return [{"order_id": "venue-ord-9", "us_market_slug": SLUG,
                         "intent": FX.LONG, "price": {"value": "0.62"},
                         "quantity": 15, "filled_shares": 0,
                         "state": "new"}]

            def order_status(self, oid):
                return {"state": "open", "executions": [],
                        "filled_shares": 0.0}

        got = await FB.recover(conn, _Venue(), account_id=ACCT, venue=VENUE)
        assert got["ok"] is True
        assert got["resubmitted_anything"] is False
        assert got["reconciled"][0]["case"] == \
            "ADOPTED_ON_AN_ESTABLISHED_CORRELATION"
        row = await conn.fetchrow(
            "SELECT state, venue_order_id FROM bettor_funded_intents")
        assert row["venue_order_id"] == "venue-ord-9"
        # ACKNOWLEDGED, and then read back in the same pass: adopting an id
        # without asking about the order would leave a row with no fills
        # against a position that may have filled during the downtime.
        assert row["state"] == "ACKNOWLEDGED"
        # STRUCTURAL: recovery cannot submit
        src = inspect.getsource(FB.recover)
        assert "submit_fok" not in src
        assert "submit" not in src.replace("resubmit", "")
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_outcome_the_venue_cannot_establish_preserves_the_exposure():
    """COUNTEREXAMPLE 5c. No open order on the market and no readable record:
    that is not 'nothing happened'. The intent stays UNRESOLVED and live."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await FB.record_intent(
            conn, intent_id="fpi-unknown", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG,
            event_key=EVENT, order_intent=FX.LONG, limit_price=0.62,
            quantity=15, collateral_usd=9.3, effective_digest="d")
        await FB.mark_send_attempted(conn, "fpi-unknown")

        class _Silent:
            def open_orders(self):
                return []

            def order_status(self, oid):
                return None

        got = await FB.recover(conn, _Silent(), account_id=ACCT, venue=VENUE)
        assert got["unresolved"], got
        assert got["unresolved"][0]["exposure"] == "PRESERVED"
        row = await conn.fetchrow(
            "SELECT state FROM bettor_funded_intents")
        assert row["state"] == "UNRESOLVED"
        assert row["state"] not in FB.TERMINAL_STATES
        exp = await FB.exposure(conn, account_id=ACCT, venue=VENUE)
        assert exp["pending_and_in_flight_collateral_usd"] == \
            pytest.approx(9.3)

        # AND AN UNREADABLE OPEN-ORDER LIST IS ALSO NOT "NO ORDER"
        class _Blind:
            def open_orders(self):
                raise RuntimeError("venue 503")

        got2 = await FB.recover(conn, _Blind(), account_id=ACCT, venue=VENUE)
        assert got2["unresolved"][0]["exposure"] == "PRESERVED"
        assert "could not be read" in got2["unresolved"][0]["reason"]
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_repeated_delivery_of_a_fill_cannot_double_the_accounting():
    """COUNTEREXAMPLE 5d. The same execution, ten times, is one row -- and an
    execution the venue does not name is NOT ingested and NOT called already
    held: the intent goes UNRESOLVED."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await FB.record_intent(
            conn, intent_id="fpi-fills", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG,
            event_key=EVENT, order_intent=FX.LONG, limit_price=0.62,
            quantity=15, collateral_usd=9.3, effective_digest="d")
        await FB.record_acknowledgement(conn, "fpi-fills",
                                        venue_order_id="vo-7", status="open")
        good = [{"qty": 15.0, "price": 0.62, "venue_fill_id": "vf-1"}]
        first = await FB.ingest_fills(conn, "fpi-fills", good)
        assert len(first["written"]) == 1
        cash1 = first["cash_usd_from_the_ledger"]
        fee1 = first["fees_usd_from_the_ledger"]
        for _ in range(9):
            again = await FB.ingest_fills(conn, "fpi-fills", good)
            assert again["written"] == []
            assert len(again["already_held"]) == 1
            assert again["cash_usd_from_the_ledger"] == pytest.approx(cash1)
            assert again["fees_usd_from_the_ledger"] == pytest.approx(fee1)
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_fills") == 1
        # the cash is the side-aware number, not qty x price by luck
        assert cash1 == pytest.approx(9.3)
        assert fee1 > 0

        # A FILLED ENTRY STILL HOLDS THE ONE-POSITION SLOT, and it should:
        # 15 contracts are owned. Opening the next intent therefore needs an
        # EVIDENCED closure first -- this test is about fill identity, not
        # about the position rule, so the position is closed explicitly and
        # the rule itself is proved in
        # tests/test_a_filled_order_is_not_a_closed_position.py.
        blocked = await FB.record_intent(
            conn, intent_id="fpi-blocked", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug="aec-second",
            event_key="ev-2", order_intent=FX.LONG, limit_price=0.50,
            quantity=4, collateral_usd=2.0, effective_digest="d")
        assert blocked["ok"] is False
        assert blocked["refusal"] == FB.R_ANOTHER_INTENT_IS_LIVE
        await FB.mark_position_closed(conn, "fpi-fills", "EXITED_IN_THE_MARKET")

        # TWO DISTINCT UNNAMED EXECUTIONS ARE BOTH UNRESOLVED
        await FB.record_intent(
            conn, intent_id="fpi-unnamed2", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug="aec-second",
            event_key="ev-2", order_intent=FX.LONG, limit_price=0.50,
            quantity=4, collateral_usd=2.0, effective_digest="d")
        await FB.record_acknowledgement(conn, "fpi-unnamed2",
                                        venue_order_id="vo-8", status="open")
        ing = await FB.ingest_fills(conn, "fpi-unnamed2", [
            {"qty": 2.0, "price": 0.50},
            {"qty": 2.0, "price": 0.50}])
        assert ing["unresolved_count"] == 2, ing
        assert ing["written"] == [] and ing["already_held"] == []
        st = await conn.fetchval(
            "SELECT state FROM bettor_funded_intents WHERE intent_id=$1",
            "fpi-unnamed2")
        assert st == "UNRESOLVED"
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_funded_pnl_is_never_assembled_from_a_shadow_row():
    """A SHADOW FILL CANNOT STAND IN FOR A FUNDED ONE.

    Structural, and the schema backs it: `rn1x_orders` carries
    `CHECK (is_modelled)`, so a funded order could not be written there if
    this code tried. Every read in the funded book is scoped to
    `bettor_funded_*`.
    """
    asyncpg = pytest.importorskip("asyncpg")
    import inspect
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        got = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        assert got["fills"] == 0
        assert got["cost_basis_usd"] == pytest.approx(0.0)
        # REALISED P&L IS A SUM OVER RECORDED EVENTS, not a constant with a
        # note attached. Over no closed position that sum is 0.0 -- and the
        # basis says what was summed, so the first settlement changes the
        # number instead of leaving a hardcoded zero in the field a loss stop
        # reads.
        assert got["realised_pnl_usd"] == pytest.approx(0.0)
        assert got["closed_positions"] == 0
        assert "realised equity curve" in got["realised_basis"]
        # AND UNREALISED IS NAMED UNMEASURED RATHER THAN ZEROED.
        assert got["unrealised_pnl_usd"] is None
        assert "UNMEASURED" in got["unrealised_basis"]

        await FB.record_intent(
            conn, intent_id="fpi-pnl", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG,
            event_key=EVENT, order_intent=FX.LONG, limit_price=0.62,
            quantity=10, collateral_usd=6.2, effective_digest="d")
        await FB.record_acknowledgement(conn, "fpi-pnl",
                                        venue_order_id="vo-9", status="open")
        await FB.ingest_fills(conn, "fpi-pnl", [
            {"qty": 10.0, "price": 0.62, "venue_fill_id": "x1"}])
        got = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        assert got["fills"] == 1
        assert got["cost_basis_usd"] == pytest.approx(6.2)
        assert got["cash_out_the_door_usd"] > got["cost_basis_usd"]

        # every funded read is scoped to the funded tables
        for fn in (FB.exposure, FB.pnl, FB.ingest_fills, FB.recover):
            src = inspect.getsource(fn)
            for shadow in ("rn1x_orders", "rn1x_fills", "rn1x_positions",
                           "shadow_executions", "live_orders"):
                assert shadow not in src, (fn.__name__, shadow)
        # and the modelled-only CHECK is really there
        got = await conn.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            " WHERE conname='rn1x_orders_is_modelled_check'")
        assert got and "is_modelled" in got
    finally:
        await _clean(conn)
        await conn.close()


# ── THE SCHEDULER CALLS IT, AND IT STILL SENDS NOTHING ──────────────

def test_the_scheduled_lane_now_calls_the_connector():
    """THE GAP THAT WAS 'NOT_WIRED'. The worker had no order path at all."""
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as L

    assert callable(L._funded_attempt)
    src = inspect.getsource(L._funded_attempt)
    assert "submit_for_decision" in src
    # it is called from the cycle, on the admitted record
    cyc = inspect.getsource(L.cycle)
    assert "_funded_attempt(" in cyc
    assert 'rec["event_key"]' in cyc
    # an unconfigured lane adds nothing rather than reporting a refusal
    assert "NOT CONFIGURED" in src
    # a raising connector does not take the cycle down
    assert "FUNDED_CONNECTOR_RAISED" in src


@pg
@pytest.mark.asyncio
async def test_the_scheduler_hook_sends_nothing_with_the_switch_off(
        monkeypatch):
    """CALLED FROM THE SCHEDULE, SHIPPED SETTINGS: no adapter call at all."""
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets.workers import ext_pinnacle_loop as L
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb",
            FA.ACCOUNT_KEY,
            json.dumps({"account_id": ACCT, "venue": VENUE}))
        pmus, sent = _transport(monkeypatch)
        assert FX.FUNDED_SUBMISSION_ENABLED is False
        assert EX.REAL_ORDER_SUBMISSION_ENABLED is False
        got = await L._funded_attempt(conn, _decision(), now=time.time())
        assert got is not None
        # IN THE SHIPPED BUILD BOTH SWITCHES ARE OFF, and the execution gate is
        # asked FIRST -- so its refusal is the one reported. Which of the two
        # answers is a detail; that no adapter call happens is the property.
        assert got["refusal"] == FX.R_GATE_NOT_AFFIRMATIVE, got
        assert got["gate_refusal"] == EX.R_SUBMISSION_DISABLED
        assert sent == []
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents") == 0
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           FA.ACCOUNT_KEY)
        await _clean(conn)
        await conn.close()


def test_the_live_state_definition_agrees_with_the_database():
    """TWO DEFINITIONS OF 'LIVE' WOULD BE A SILENT HOLE: the headroom check
    and the concurrency guard must count the same states."""
    import pathlib
    import re

    sql = pathlib.Path(FB.__file__).resolve().parents[1] / "migrations" / \
        "125_funded_pilot_intents_and_fills.sql"
    body = sql.read_text()
    fn = body[body.index("bettor_funded_intent_is_live"):]
    named = set(re.findall(r"'([A-Z_]+)'", fn[:fn.index("$$ LANGUAGE")]))
    assert named == set(FB.LIVE_STATES), (named, FB.LIVE_STATES)
