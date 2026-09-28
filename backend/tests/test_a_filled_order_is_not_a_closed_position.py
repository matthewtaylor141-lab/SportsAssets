"""THE FOUR DEFECTS THE FUNDED LANE HAD AFTER THE AUTHORIZATION REPAIRS.

Each section names the defect, and each defect was real in the code I shipped:

  1. A FILLED ORDER WAS BEING TREATED AS A CLOSED POSITION. FILLED is excluded
     from the outstanding-order set -- correctly, a filled order is finished --
     and everything read that one predicate as "where the exposure is". So the
     instant an entry filled, the one-position index released its slot, the
     holding vanished from per-event and per-market collateral, and correlated
     exposure counted only outstanding orders. At the point of MAXIMUM
     exposure. The index enforced one outstanding ORDER while claiming to
     enforce one open POSITION.

  2. RECOVERY COULD ADOPT SOMEBODY ELSE'S ORDER, AND MISSED THE REAL ADAPTER'S
     EXECUTIONS. `recover()` took `candidates[0]` off a match on market slug
     alone, so a manual order or another strategy's order on that market became
     ours. Separately it read `st.get("fills")`, a key `pmus.order_status` has
     never returned -- it returns `executions` -- so the production adapter
     could report a fill and recovery would ingest none of it.

  3. FUNDED MANAGEMENT AND P&L WERE NOT COMPLETE. `pnl()` hardcoded
     `realised_pnl_usd = 0.0` and `check_rails()` hardcoded MAX_DRAWDOWN to the
     same constant. "Nothing has settled yet" describes a day; it cannot
     implement a loss stop, because the constant still reads zero after the
     first settlement.

  4. ESTIMATED FEES WERE BOOKED AS ACTUAL FEES. `ingest_fills()` always called
     `expected_fee()` and stored that as `fee_usd`, which everything downstream
     read as what the venue charged.

WHERE THIS CUTS. At `pmus._get_client`, the transport seam, for everything that
goes through the adapter -- so `pmus.open_orders`, `pmus.order_status` and
`pmus.submit_fok` are the PRODUCTION functions with their own normalisation,
their own key names and their own gate. No order leaves this process: the
transport is a list, and the shipped switches are asserted off by their own
tests.
"""

from __future__ import annotations

import json
import time

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_management as FM

#: THE VENUE'S OWN POSITION READ, WHICH `submit_for_decision` NOW REQUIRES.
#:
#: Account-wide exposure is a submission precondition and the venue is the
#: authority on what the account holds; without it the path refuses
#: ACCOUNT_WIDE_EXPOSURE_COULD_NOT_BE_MEASURED, which is the CORRECT behaviour
#: and is asserted on its own in `test_account_exposure_reaches_enforcement.py`.
#: These tests are about rails against a FILLED order, so they supply an empty
#: account and reach their own assertions.
EMPTY_VENUE = {"held_usd": 0.0, "working_usd": 0.0, "unresolved_usd": 0.0}


DSN = __import__("os").environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

ACCT = "acct-funded-lifecycle-test"
VENUE = "PMUS"
SLUG = "aec-nfl-kc-buf-2026-09-27"
EVENT = "ev-kc-buf-2026-09-27"
#: Roomy enough that a second entry is refused by the POSITION RULE rather
#: than incidentally by a rail -- the two must be told apart.
LIMITS = {"capital_usd": 400, "per_order_usd": 60, "event_exposure_usd": 60,
          "max_exposure_usd": 200, "daily_loss_stop_usd": 40}


def _decision(**over):
    rec = {"admissible": True, "refusals": [], "us_market_slug": SLUG,
           "event_key": EVENT, "order_intent": FX.LONG,
           "execution_plan": {"execution": {"size": 10, "vwap": 0.62,
                                            "limit_price": 0.62}}}
    rec.update(over)
    return rec


# ── THE VENUE'S TRANSPORT, AND ONLY THAT ────────────────────────────

class _Orders:
    """The four venue calls the production adapter makes, in the venue's own
    shapes -- camelCase, money objects, `ORDER_STATE_*` -- because
    `_norm_order`, `_execution_record` and `_order_cost` are the code under
    test and a double that answered in OUR shape would skip them."""

    def __init__(self, sent, *, execs=None, order_id="venue-ord-1",
                 resting=(), retrieve=None, raise_on_create=None,
                 cancel_ok=True):
        self.sent = sent
        self._execs = execs
        self._oid = order_id
        self._resting = list(resting)
        self._retrieve = retrieve or {}
        self._raise = raise_on_create
        self._cancel_ok = cancel_ok

    def preview(self, body):
        req = (body or {}).get("request") or {}
        self.sent.append(("preview", req))
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

    def list(self, params=None):
        self.sent.append(("list", dict(params or {})))
        return {"orders": list(self._resting)}

    def retrieve(self, order_id):
        self.sent.append(("retrieve", order_id))
        return self._retrieve.get(str(order_id))

    def cancel(self, order_id, body=None):
        self.sent.append(("cancel", order_id))
        if not self._cancel_ok:
            raise RuntimeError("the venue refused the cancel")
        return {}


class _Markets:
    """The venue's market endpoints, including the BOOK feed the exit selector
    reads. The ladder is published in the venue's own shape -- `bids` /
    `offers` with `{"px": {"value": ...}, "qty": ...}` levels -- because
    `bettor_book_snapshot` parses that and a double answering in our shape
    would skip the parser."""

    def __init__(self, bids=None, offers=None, raise_on_book=False):
        self._bids = bids
        self._offers = offers
        self._raise = raise_on_book

    def retrieve_by_slug(self, slug):
        return {"market": {"marketSides": [
            {"identifier": slug + "-a", "description": "A"},
            {"identifier": slug + "-b", "description": "B"}]}}

    def book(self, slug):
        if self._raise:
            raise RuntimeError("the venue's book feed is unreachable")
        if self._bids is None and self._offers is None:
            return {}          # NO_MARKET_DATA_IN_PAYLOAD
        return {"marketData": {
            "bids": list(self._bids or []),
            "offers": list(self._offers or [])}}


def _level(px, qty):
    return {"px": {"value": "%.2f" % px, "currency": "USD"}, "qty": str(qty)}


class _Client:
    def __init__(self, sent, *, bids=None, offers=None,
                 raise_on_book=False, **kw):
        self.orders = _Orders(sent, **kw)
        self.markets = _Markets(bids=bids, offers=offers,
                                raise_on_book=raise_on_book)


def _transport(monkeypatch, **kw):
    from sportsassets import pmus
    sent: list = []
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(sent, **kw))
    monkeypatch.setattr(pmus._gate, "authorize", lambda *a, **k: {"ok": True})
    return pmus, sent


def _venue_order(*, oid, slug=SLUG, intent=FX.LONG, price="0.62", qty=10,
                 created="2026-09-27T12:00:00Z", filled=0):
    """One resting order in the VENUE's own listing shape, so `_norm_order`
    does the translating."""
    return {"id": oid, "marketSlug": slug, "intent": intent,
            "price": {"value": price, "currency": "USD"},
            "quantity": qty, "cumQuantity": filled,
            "leavesQuantity": qty - filled, "state": "ORDER_STATE_NEW",
            "createTime": created, "tif": "TIME_IN_FORCE_FILL_OR_KILL"}


def _venue_retrieval(*, oid, executions=None, filled=0, qty=10,
                     state="ORDER_STATE_NEW", price="0.62", commission=None):
    """The venue's `orders.retrieve` answer, whose `executions` list is the key
    `pmus.order_status` reads and recovery used to miss."""
    out = {"order": {"id": oid, "marketSlug": SLUG, "intent": FX.LONG,
                     "price": {"value": price, "currency": "USD"},
                     "quantity": qty, "cumQuantity": filled,
                     "leavesQuantity": qty - filled, "state": state}}
    if executions is not None:
        out["executions"] = executions
    if commission is not None:
        out["order"]["commissionNotionalTotalCollected"] = {
            "value": "%.4f" % commission, "currency": "USD"}
    return out


async def _seed(conn, *, limits=None, expires_in=3600.0):
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       ACCT)
    await conn.execute(
        "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
        " paused, accounting_status, opening_balance, note) VALUES "
        "($1,'desk-lifecycle-test','ACTIVE',FALSE,'RECONCILED',0,'lifecycle')",
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


PAYS_ON = "KANSAS_CITY_CHIEFS"


async def _entry(conn, *, intent_id, qty=10, price=0.62, slug=SLUG,
                 event=EVENT, intent=FX.LONG, vo="vo-1", fill_qty=None,
                 fill_id="vf-1", commission=None, payout_event=PAYS_ON):
    """AN ENTRY THAT FILLED, through the book's own writers.

    `payout_event` is persisted because `ev_hold` refuses without it and it is
    NOT derivable from the order intent. Passing None models a row written
    before the column existed, which the selector must report rather than
    guess around.
    """
    coll = FX.collateral_for(price, qty, intent)
    got = await FB.record_intent(
        conn, intent_id=intent_id, account_id=ACCT, venue=VENUE,
        venue_class=FA.VENUE_FUNDED, us_market_slug=slug, event_key=event,
        order_intent=intent, limit_price=price, quantity=qty,
        collateral_usd=coll, effective_digest="d",
        payout_event=payout_event, held_is_long=(intent == FX.LONG))
    if not got.get("ok"):
        return got
    await FB.record_acknowledgement(conn, intent_id, venue_order_id=vo,
                                    status="open")
    f = {"qty": float(qty if fill_qty is None else fill_qty),
         "price": price, "venue_fill_id": fill_id}
    if commission is not None:
        f["commission_usd"] = commission
    ing = await FB.ingest_fills(conn, intent_id, [f])
    return dict(got, fills=ing)


# ════════════════════════════════════════════════════════════════════
# 1 · A FILLED ORDER IS NOT A CLOSED POSITION
# ════════════════════════════════════════════════════════════════════

def test_the_two_questions_have_two_predicates():
    """STRUCTURAL. Order terminality and inventory closure are separate, and
    FILLED is terminal for the order while the position it created is open."""
    assert "FILLED" not in FB.OUTSTANDING_ORDER_STATES
    assert "FILLED" in FB.TERMINAL_ORDER_STATES
    d = FB.describe()
    sep = d["order_terminality_is_not_inventory_closure"]
    assert sep["outstanding"] == "bettor_funded_order_is_outstanding(state)"
    assert sep["still_held"] == \
        "bettor_funded_holds_inventory(residual, closed)"
    assert set(d["closure_reasons"]) == {
        "EXITED_IN_THE_MARKET", "SETTLED_BY_THE_VENUE",
        "VOIDED_BY_THE_VENUE", "NEVER_HELD_ANY_INVENTORY"}


@pg
@pytest.mark.asyncio
async def test_the_database_itself_keeps_the_slot_after_a_fill():
    """THE COUNTEREXAMPLE, AT THE DATABASE. A FILLED entry holding contracts
    blocks a second entry; an EXIT is never blocked; and only an evidenced
    closure releases the slot."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        got = await _entry(conn, intent_id="fpi-a")
        assert got["ok"] is True
        row = await conn.fetchrow(
            "SELECT state, residual_qty::float8 AS r, "
            "       bettor_funded_order_is_outstanding(state) AS out, "
            "       bettor_funded_holds_inventory(residual_qty, closed_at) "
            "           AS held, "
            "       bettor_funded_position_is_open(state, residual_qty, "
            "           closed_at) AS open "
            "  FROM bettor_funded_intents WHERE intent_id='fpi-a'")
        assert row["state"] == "FILLED"       # the ORDER is finished
        assert row["out"] is False            # ... and not outstanding
        assert row["r"] == pytest.approx(10.0)
        assert row["held"] is True            # the CONTRACTS are owned
        assert row["open"] is True            # so the POSITION is open

        # A SECOND ENTRY IS REFUSED, and by the position rule.
        second = await _entry(conn, intent_id="fpi-b", vo="vo-2",
                              fill_id="vf-2")
        assert second["ok"] is False
        assert second["refusal"] == FB.R_ANOTHER_INTENT_IS_LIVE
        assert second["open_positions"][0]["intent_id"] == "fpi-a"
        assert second["open_positions"][0]["state"] == "FILLED"

        # AN EXIT IS NOT REFUSED. Refusing one because a position is open is
        # the failure that strands inventory.
        await conn.execute(
            "INSERT INTO bettor_funded_intents (intent_id, account_id, venue,"
            " venue_class, us_market_slug, event_key, order_intent,"
            " limit_price, quantity, collateral_usd, effective_digest, state,"
            " provenance, kind, parent_intent_id) VALUES "
            "($1,$2,$3,$4,$5,$6,$7,0.70,10,0,'d','INTENT_RECORDED',$8,"
            " 'EXIT','fpi-a')",
            "fpi-x", ACCT, VENUE, FA.VENUE_FUNDED, SLUG, EVENT, FX.LONG,
            FB.PROVENANCE)
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents "
            " WHERE kind='EXIT'") == 1

        # AND ONLY AN EVIDENCED CLOSURE RELEASES THE SLOT.
        with pytest.raises(ValueError):
            await FB.mark_position_closed(conn, "fpi-a", "I_ASSUMED_IT_WAS_GONE")
        closed = await FB.mark_position_closed(
            conn, "fpi-a", "EXITED_IN_THE_MARKET")

        # ── AN EVIDENCED CLOSURE IS NOT ENOUGH ON ITS OWN ───────────
        #
        # WHY THIS HALF WAS ADDED RATHER THAN THE ASSERTION BELOW RELAXED.
        # This test used to close `fpi-a` and immediately expect the slot back.
        # It could, because the one-position index counts only ENTRY rows, and
        # the EXIT row inserted above is kind='EXIT'. But the test left that
        # EXIT at INTENT_RECORDED -- an order the system still believes it may
        # send. Migration 131 refuses to release the group while any order on a
        # leg OR ITS CHILDREN is outstanding, and that refusal is right: a
        # released slot plus a live exit order on the same contract is how a
        # new entry and an unsent exit end up pointed at each other.
        #
        # So the property the assertion protects -- an evidenced closure
        # releases the slot -- is UNCHANGED and still asserted below. What is
        # added is its precondition: the position's own orders must have
        # resolved first. Both halves are pinned so neither can be lost.
        if closed.get("group_release", {}).get("group_id"):
            assert closed["group_release"]["released"] is False
            assert "outstanding or ambiguous order" in \
                closed["group_release"]["why"]
            blocked = await _entry(conn, intent_id="fpi-blocked", vo="vo-9",
                                   fill_id="vf-9")
            assert blocked["ok"] is False
            # AND THE REFUSAL DID NOT ABORT THE TRANSACTION. A decline is an
            # answer, not a database error.
            assert await conn.fetchval("SELECT 42") == 42
            await FB.abandon_before_send(conn, "fpi-x",
                                         "THE_EXIT_ORDER_NEVER_LEFT")

        third = await _entry(conn, intent_id="fpi-c", vo="vo-3",
                             fill_id="vf-3")
        assert third["ok"] is True
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_completed_purchase_still_counts_against_every_rail(
        monkeypatch):
    """A COMPLETED PURCHASE, THEN ANOTHER ON THE SAME EVENT. The purchased
    inventory is what the second order is checked against -- per event, per
    market and correlated -- and it is refused with no venue call."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        pmus, sent = _transport(monkeypatch)
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)

        first = await FX.submit_for_decision(conn, _decision(),
                                            account_id=ACCT, venue=VENUE, venue_positions=EMPTY_VENUE)
        assert first["submitted"] is True, first
        assert first["state"] == "FILLED"
        assert first["order"]["filled_qty_from_the_ledger"] == \
            pytest.approx(10.0)

        # THE HOLDING IS IN EXPOSURE, and in the per-event and per-market
        # collateral -- which is exactly what vanished before.
        exp = await FB.exposure(conn, account_id=ACCT, venue=VENUE)
        assert exp["residual_holdings"], exp
        assert exp["outstanding_orders"] == []
        assert exp["per_event_collateral_usd"][EVENT] == pytest.approx(6.2)
        assert exp["per_market_collateral_usd"][SLUG] == pytest.approx(6.2)
        assert exp["collateral_of_residual_holdings_usd"] == \
            pytest.approx(6.2)
        assert exp["contracts_held"] == pytest.approx(10.0)

        # AND THE RAILS MEASURE IT.
        eff = EX.effective_limits(dict(LIMITS))
        plan = FX.plan_from_decision(_decision())
        rails = await FX.check_rails(conn, plan, eff["effective"],
                                    account_id=ACCT, venue=VENUE)
        by = {r["rail"]: r for r in rails["rails"]}
        assert by["MAX_EVENT_EXPOSURE"]["measured"] == pytest.approx(12.4)
        assert by["MAX_MARKET_EXPOSURE"]["measured"] == pytest.approx(12.4)
        assert by["MAX_CORRELATED_EXPOSURE"]["measured"] == \
            pytest.approx(12.4)
        assert by["MAX_RESIDUAL_INVENTORY"]["measured"] == \
            pytest.approx(20.0)
        assert rails["unmeasured"] == []

        # THE SECOND SUBMISSION IS REFUSED AND NOTHING IS SENT.
        before = len([k for k, _ in sent if k == "create"])
        second = await FX.submit_for_decision(conn, _decision(),
                                             account_id=ACCT, venue=VENUE, venue_positions=EMPTY_VENUE)
        assert second["ok"] is False
        assert second["refusal"] in (FX.R_OVER_RAIL,
                                     FB.R_ANOTHER_INTENT_IS_LIVE), second
        assert len([k for k, _ in sent if k == "create"]) == before
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_fill_between_the_rail_check_and_the_insert_is_still_refused():
    """THE CONCURRENCY CASE. The second caller reads the rails while the first
    order is still resting -- so its measurement says there is room -- and the
    first order FILLS before the second caller's intent insert lands.

    A SELECT-then-INSERT check cannot survive this, which is why the guarantee
    is a unique index over the OPEN-POSITION predicate rather than over the
    outstanding-order one: had the index kept its old definition, the fill
    would have released the slot in the window and the second insert would have
    succeeded against 10 contracts the lane already owned.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    other = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        # the first order is resting, not filled: the rails have room
        await FB.record_intent(
            conn, intent_id="fpi-resting", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
            order_intent=FX.LONG, limit_price=0.62, quantity=10,
            collateral_usd=6.2, effective_digest="d")
        await FB.record_acknowledgement(conn, "fpi-resting",
                                        venue_order_id="vo-r", status="open")
        eff = EX.effective_limits(dict(LIMITS))
        plan = FX.plan_from_decision(_decision())

        # THE SECOND CALLER'S RAIL CHECK, taken now.
        rails = await FX.check_rails(other, plan, eff["effective"],
                                    account_id=ACCT, venue=VENUE)
        assert rails["over"] == [], rails          # there IS room
        assert rails["exposure"]["outstanding_orders"], rails

        # ... AND THE FIRST ORDER FILLS IN THE WINDOW.
        await FB.ingest_fills(conn, "fpi-resting", [
            {"qty": 10.0, "price": 0.62, "venue_fill_id": "vf-r"}])
        assert await conn.fetchval(
            "SELECT state FROM bettor_funded_intents "
            " WHERE intent_id='fpi-resting'") == "FILLED"

        # THE SECOND CALLER'S INSERT NOW LANDS, and the database refuses it.
        got = await FB.record_intent(
            other, intent_id="fpi-race", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
            order_intent=FX.LONG, limit_price=0.62, quantity=10,
            collateral_usd=6.2, effective_digest="d")
        assert got["ok"] is False
        assert got["refusal"] == FB.R_ANOTHER_INTENT_IS_LIVE
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents "
            " WHERE kind='ENTRY'") == 1
    finally:
        await _clean(conn)
        await conn.close()
        await other.close()


# ════════════════════════════════════════════════════════════════════
# 2 · RECOVERY: OWNERSHIP, NOT COINCIDENCE -- AND `executions`
# ════════════════════════════════════════════════════════════════════

def test_market_coincidence_is_not_ownership():
    """THE RULE, PURE. Every counterexample the old `candidates[0]` accepted."""
    mine = {"us_market_slug": SLUG, "order_intent": FX.LONG,
            "limit_price": 0.62, "quantity": 10}

    def norm(o):
        from sportsassets import pmus
        return pmus._norm_order(o)

    # (a) SAME MARKET, NOTHING ELSE: refused.
    got = FB.correlate_venue_order(
        mine, [norm({"id": "someone-else", "marketSlug": SLUG})])
    assert got["adopt"] is None
    assert got["refusal"] == FB.R_NO_CORRELATED_ORDER

    # (b) SAME MARKET AND SIDE, A DIFFERENT CLIP: refused.
    got = FB.correlate_venue_order(
        mine, [norm(_venue_order(oid="manual", qty=25))])
    assert got["adopt"] is None
    assert got["rejected"][0]["disagrees_on"] == ["quantity"]

    # (c) SAME EVERYTHING BUT THE PRICE: refused.
    got = FB.correlate_venue_order(
        mine, [norm(_venue_order(oid="other-strategy", price="0.61"))])
    assert got["adopt"] is None
    assert got["rejected"][0]["disagrees_on"] == ["limit_price"]

    # (d) THE OTHER SIDE OF OUR OWN MARKET: refused. Every two-sided market
    #     here shares one identifier between its sides, so this is the case
    #     that would have bought the wrong team's exposure into our book.
    got = FB.correlate_venue_order(
        mine, [norm(_venue_order(oid="short-side",
                                 intent="ORDER_INTENT_BUY_SHORT"))])
    assert got["adopt"] is None
    assert got["rejected"][0]["disagrees_on"] == ["order_intent"]

    # (e) TWO ORDERS THAT BOTH MATCH: neither is established as ours.
    got = FB.correlate_venue_order(
        mine, [norm(_venue_order(oid="a")), norm(_venue_order(oid="b"))])
    assert got["adopt"] is None
    assert got["refusal"] == FB.R_AMBIGUOUS_CORRELATION
    assert sorted(got["matched"]) == ["a", "b"]

    # (f) A MATCH ANOTHER INTENT ALREADY OWNS is not a candidate at all.
    got = FB.correlate_venue_order(
        mine, [norm(_venue_order(oid="already-ours"))],
        claimed=["already-ours"])
    assert got["adopt"] is None
    assert got["already_claimed_by_another_intent"] == ["already-ours"]

    # (g) A MATCH THAT PREDATES OUR REQUEST cannot be ours.
    got = FB.correlate_venue_order(
        mine, [norm(_venue_order(oid="earlier",
                                 created="2026-09-27T11:00:00Z"))],
        sent_at="2026-09-27T12:00:00Z")
    assert got["adopt"] is None
    assert got["refusal"] == FB.R_CANDIDATE_PREDATES_US

    # (h) AND THE CASE THAT USED TO BE ADOPTED AND MUST NOT BE: ONE order,
    #     agreeing on every term, created after our send. It is still not
    #     ours. One manual order at the same price and size satisfies all
    #     four terms, and this venue accepts no client order identity, so the
    #     difference cannot be established. THIS is the counterexample the
    #     four-term rule failed.
    got = FB.correlate_venue_order(
        mine, [norm(_venue_order(oid="looks-like-ours",
                                 created="2026-09-27T12:00:05Z")),
               norm(_venue_order(oid="unrelated", qty=3))],
        sent_at="2026-09-27T12:00:00Z")
    assert got["adopt"] is None
    assert got["refusal"] == FB.R_NO_DURABLE_IDENTITY
    assert got["term_match_only"] == "looks-like-ours"
    assert got["created_after_our_send"] is True
    assert got["would_need"]["venue_accepts_it"] is False

    # (i) AND THE VENUE REALLY DOES NOT ACCEPT ONE. Asserted against the
    #     INSTALLED SDK rather than described, because this is the fact the
    #     whole rule rests on.
    import polymarket_us.types.orders as _o
    import inspect as _i

    params = _i.getsource(_o.CreateOrderParams)
    for name in ("clientOrderId", "clOrdId", "clOrdID", "clientId",
                 "externalId", "idempotency"):
        assert name not in params, name
    assert FB.CLIENT_ORDER_IDENTITY_SUPPORTED is False


def test_the_clock_check_reads_the_timestamp_the_database_hands_it():
    """A SILENT DEGRADATION THIS PINS. `sent_at` comes out of asyncpg as a
    `datetime`, not an ISO string, and `_epoch_of` returning None for it would
    not fail anything -- the correlation would just quietly stop checking
    whether a candidate predates our request and fall back to the four terms
    alone. So every shape the timestamp can arrive in is asserted."""
    import datetime as _dt

    when = _dt.datetime(2026, 9, 27, 12, 0, 0, tzinfo=_dt.timezone.utc)
    assert FB._epoch_of(when) == when.timestamp()
    assert FB._epoch_of("2026-09-27T12:00:00Z") == when.timestamp()
    assert FB._epoch_of(when.timestamp()) == when.timestamp()
    # A NAIVE TIMESTAMP IS READ AS UTC, which is what the column stores.
    assert FB._epoch_of(_dt.datetime(2026, 9, 27, 12, 0, 0)) == \
        when.timestamp()
    # AND WHAT CANNOT BE READ IS None, which the rule reports rather than
    # treating as agreement.
    assert FB._epoch_of(None) is None
    assert FB._epoch_of("not a time") is None
    got = FB.correlate_venue_order(
        {"us_market_slug": SLUG, "order_intent": FX.LONG,
         "limit_price": 0.62, "quantity": 10},
        [__import__("sportsassets.pmus", fromlist=["x"])._norm_order(
            _venue_order(oid="term-match", created="nonsense"))],
        sent_at=when)
    # AN UNREADABLE TIMESTAMP USED TO FALL BACK TO "THE FOUR TERMS ALONE",
    # which was the adoption path -- so the weakest evidence produced the
    # strongest action. It now establishes nothing and nothing is adopted.
    assert got["adopt"] is None
    assert got["refusal"] == FB.R_NO_DURABLE_IDENTITY
    assert got["created_after_our_send"] is None
    assert "not even the weak time discriminator" in got["time_check"]


def test_one_executions_reader_handles_both_adapter_shapes():
    """THE KEY RECOVERY READ. `pmus.order_status` returns `executions` with
    `last_px`/`last_shares`; `submit_fok` returns the venue's raw response with
    `lastPx`/`lastShares`. Recovery read `fills`, which is neither."""
    submit_shape = {"raw": {"response": {"executions": [
        {"id": "e1", "type": "EXECUTION_TYPE_FILL",
         "lastPx": {"value": "0.62"}, "lastShares": 4}]}}}
    status_shape = {"executions": [
        {"id": "e2", "type": "EXECUTION_TYPE_FILL", "last_px": 0.62,
         "last_shares": 6.0, "commission_usd": 0.11}]}
    a = FB.executions_of(submit_shape)["executions"]
    b = FB.executions_of(status_shape)["executions"]
    assert a[0] == {"qty": 4.0, "price": 0.62, "venue_fill_id": "e1",
                    "raw": submit_shape["raw"]["response"]["executions"][0]}
    assert b[0]["qty"] == 6.0 and b[0]["venue_fill_id"] == "e2"
    # THE VENUE'S OWN COMMISSION RIDES THROUGH, so the fee can be reconciled.
    assert b[0]["commission_usd"] == 0.11
    # A NON-FILL EXECUTION IS SKIPPED AND SAID TO BE SKIPPED.
    read = FB.executions_of({"executions": [
        {"id": "e3", "type": "EXECUTION_TYPE_REJECTED"}]})
    assert read["executions"] == []
    assert read["skipped"][0]["type"] == "EXECUTION_TYPE_REJECTED"
    # AND THE CONNECTOR USES THE SAME READER.
    assert FX._executions_of(submit_shape) == a


@pg
@pytest.mark.asyncio
async def test_recovery_through_the_real_adapter_refuses_an_unrelated_order(
        monkeypatch):
    """THROUGH `pmus.open_orders`, with the transport substituted. An unrelated
    order on our market is reported as an orphan and never adopted."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        pmus, sent = _transport(monkeypatch, resting=[
            _venue_order(oid="manual-desk-order", qty=25)])
        await FB.record_intent(
            conn, intent_id="fpi-lost", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
            order_intent=FX.LONG, limit_price=0.62, quantity=10,
            collateral_usd=6.2, effective_digest="d")
        await FB.mark_send_attempted(conn, "fpi-lost")

        got = await FB.recover(conn, pmus, account_id=ACCT, venue=VENUE)
        assert got["reconciled"] == [], got
        u = got["unresolved"][0]
        assert u["exposure"] == "PRESERVED"
        assert u["correlation"]["refusal"] == FB.R_NO_CORRELATED_ORDER
        assert u["correlation"]["rejected"][0]["disagrees_on"] == ["quantity"]
        # THE ORDER IS REPORTED, AND REPORTED AS NOT OURS.
        assert got["orphans"][0]["venue_order_id"] == "manual-desk-order"
        assert await conn.fetchval(
            "SELECT venue_order_id FROM bettor_funded_intents") is None
        # nothing was sent
        assert [k for k, _ in sent if k in ("create", "cancel")] == []
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_recovery_through_the_real_adapter_refuses_two_candidates(
        monkeypatch):
    """AMBIGUITY STAYS UNRESOLVED. Two orders matching our request are
    indistinguishable, so neither is adopted."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        pmus, sent = _transport(monkeypatch, resting=[
            _venue_order(oid="cand-a"), _venue_order(oid="cand-b")])
        await FB.record_intent(
            conn, intent_id="fpi-two", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
            order_intent=FX.LONG, limit_price=0.62, quantity=10,
            collateral_usd=6.2, effective_digest="d")
        await FB.mark_send_attempted(conn, "fpi-two")

        got = await FB.recover(conn, pmus, account_id=ACCT, venue=VENUE)
        assert got["reconciled"] == []
        assert got["unresolved"][0]["correlation"]["refusal"] == \
            FB.R_AMBIGUOUS_CORRELATION
        assert await conn.fetchval(
            "SELECT venue_order_id FROM bettor_funded_intents") is None
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_fill_during_downtime_is_read_from_the_adapters_executions(
        monkeypatch):
    """THE OTHER HALF OF DEFECT 2. The order was acknowledged, this process
    died, and it filled. `pmus.order_status` reports that under `executions`;
    recovery read `fills` and ingested nothing, so the money had moved and the
    book did not know."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        execs = [{"id": "downtime-exec-1", "type": "EXECUTION_TYPE_FILL",
                  "lastPx": {"value": "0.62"}, "lastShares": 10,
                  "order": {"state": "ORDER_STATE_FILLED"},
                  "commissionNotionalTotalCollected": {"value": "0.0900"}}]
        pmus, sent = _transport(
            monkeypatch,
            resting=[],
            retrieve={"vo-down": _venue_retrieval(
                oid="vo-down", executions=execs, filled=10,
                state="ORDER_STATE_FILLED")})
        await FB.record_intent(
            conn, intent_id="fpi-down", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
            order_intent=FX.LONG, limit_price=0.62, quantity=10,
            collateral_usd=6.2, effective_digest="d")
        await FB.record_acknowledgement(conn, "fpi-down",
                                        venue_order_id="vo-down",
                                        status="open")

        got = await FB.recover(conn, pmus, account_id=ACCT, venue=VENUE)
        rec = got["reconciled"][0]
        assert rec["case"] == "READ_FROM_THE_VENUE"
        assert rec["executions_read"] == 1
        assert rec["fills_written"] == 1
        assert rec["filled_qty_from_the_ledger"] == pytest.approx(10.0)
        assert rec["state_now"] == "FILLED"
        assert rec["residual_qty"] == pytest.approx(10.0)
        # THE EXECUTION'S OWN IDENTITY IS WHAT MAKES IT IDEMPOTENT.
        fill = await conn.fetchrow("SELECT * FROM bettor_funded_fills")
        assert fill["venue_fill_id"] == "downtime-exec-1"
        again = await FB.recover(conn, pmus, account_id=ACCT, venue=VENUE)
        assert again["live_intents"] == 0     # the order is finished
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_fills") == 1
        # AND THE HOLDING IS IN EXPOSURE.
        exp = await FB.exposure(conn, account_id=ACCT, venue=VENUE)
        assert exp["contracts_held"] == pytest.approx(10.0)
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_filled_shares_with_no_executions_list_is_a_discrepancy(
        monkeypatch):
    """"THE VENUE SENT NO LIST" IS NOT "THE VENUE SENT AN EMPTY ONE". An order
    the venue says filled, with no executions to place, is quantity we cannot
    account for -- so the intent stays UNRESOLVED rather than reading as a
    clean unfilled order."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        pmus, sent = _transport(monkeypatch, retrieve={
            "vo-quiet": _venue_retrieval(oid="vo-quiet", filled=10,
                                         state="ORDER_STATE_FILLED")})
        await FB.record_intent(
            conn, intent_id="fpi-quiet", account_id=ACCT, venue=VENUE,
            venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
            order_intent=FX.LONG, limit_price=0.62, quantity=10,
            collateral_usd=6.2, effective_digest="d")
        await FB.record_acknowledgement(conn, "fpi-quiet",
                                        venue_order_id="vo-quiet",
                                        status="open")
        got = await FB.recover(conn, pmus, account_id=ACCT, venue=VENUE)
        u = got["unresolved"][0]
        assert u["case"] == "FILLED_SHARES_WITH_NO_EXECUTIONS"
        assert u["exposure"] == "PRESERVED"
        assert "unplaceable" in u["reason"]
    finally:
        await _clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 3 · MANAGEMENT, REALISED P&L AND THE LOSS STOP
# ════════════════════════════════════════════════════════════════════

def test_the_drawdown_is_computed_not_hardcoded():
    """PURE. The realised equity curve and its worst peak-to-trough."""
    got = FB.drawdown_from([])
    assert got["realised_pnl_usd"] == 0.0
    assert got["max_drawdown_usd"] == 0.0
    assert got["closed_positions"] == 0
    got = FB.drawdown_from([
        {"intent_id": "p1", "at": 1.0, "net": 4.0},
        {"intent_id": "p2", "at": 2.0, "net": -9.0},
        {"intent_id": "p3", "at": 3.0, "net": 2.0}])
    assert got["realised_pnl_usd"] == pytest.approx(-3.0)
    assert got["peak_realised_usd"] == pytest.approx(4.0)
    assert got["max_drawdown_usd"] == pytest.approx(9.0)
    assert got["worst_at_intent"] == "p2"
    # ORDER IS CLOSURE ORDER, not insertion order.
    same = FB.drawdown_from([
        {"intent_id": "p3", "at": 3.0, "net": 2.0},
        {"intent_id": "p1", "at": 1.0, "net": 4.0},
        {"intent_id": "p2", "at": 2.0, "net": -9.0}])
    assert same["max_drawdown_usd"] == pytest.approx(9.0)


def test_the_servicing_lane_can_only_reduce_exposure():
    """STRUCTURAL, ON THE PARSED MODULE. The servicing lane has exactly two
    order-capable calls and neither can open a position: the adapter is only
    ever called with `sell` true, and the only other call is a cancel. And
    `manage`, the thing a schedule runs unattended, calls neither."""
    import ast
    import inspect
    import pathlib

    tree = ast.parse(pathlib.Path(FM.__file__).read_text())
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", getattr(node.func, "id", None))
        if name in ("submit_fok", "cancel_order", "submit_for_decision",
                    "record_intent", "close_position"):
            calls.append((name, node))
    names = sorted(n for n, _ in calls)
    assert names == ["cancel_order", "submit_fok"], names
    sub = [n for name, n in calls if name == "submit_fok"]
    assert len(sub) == 1
    # THE FOURTH POSITIONAL ARGUMENT IS `sell`, AND IT IS THE LITERAL True.
    # `pmus.submit_fok` derives SELL_SHORT/SELL_LONG from it; a False or a
    # variable here would be a buy leaving the servicing lane.
    fourth = sub[0].args[3]
    assert isinstance(fourth, ast.Constant) and fourth.value is True
    # `manage` DOES call `submit_exit` now -- that is the whole correction, and
    # asserting it did not was pinning the gap. What must stay true is that it
    # cannot OPEN a position: no entry submission, and no ENTRY intent written.
    mg = inspect.getsource(FM.manage)
    assert "submit_exit" in mg
    for banned in ("submit_for_decision", "record_intent", "submit_fok"):
        assert banned not in mg, banned


def test_the_servicing_switch_is_separate_from_the_entry_switch():
    """STOPPING NEW EXPOSURE MUST NOT STRAND INVENTORY."""
    assert FX.FUNDED_SUBMISSION_ENABLED is False
    assert FM.FUNDED_EXIT_SUBMISSION_ENABLED is False
    assert FM.describe()["entry_switch"] != FM.describe()["servicing_switch"]
    reads = [d for d in FM.disablements()
             if d["value"] == "DELIBERATELY AVAILABLE"]
    assert reads and reads[0]["stops"].startswith("nothing")
    # AND THE EXIT ROUNDS THE OTHER WAY FROM THE ENTRY, per side.
    assert FX.safe_cent(0.6399, FX.LONG) == 0.63          # never overpay
    assert FM.safe_exit_cent(0.6301, FX.LONG) == 0.64     # never accept less
    assert FX.safe_cent(0.6301, "ORDER_INTENT_BUY_SHORT") == 0.64
    assert FM.safe_exit_cent(0.6399, "ORDER_INTENT_BUY_SHORT") == 0.63


@pg
@pytest.mark.asyncio
async def test_entry_partial_exit_restart_final_close_and_the_stop(
        monkeypatch):
    """THE WHOLE LIFECYCLE, and the loss it realises trips the configured stop.

    Entry 10 @ 0.62 -> partial exit 4 @ 0.30 -> RESTART (fresh connection,
    state read from the database alone) -> final exit 6 @ 0.30 -> the position
    closes, realised P&L is a sum over recorded economic events, and the
    drawdown it produces exceeds MAX_DRAWDOWN so the next entry is refused on
    that rail. Servicing stays available throughout.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        pmus, sent = _transport(monkeypatch)
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", True)
        monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", True)
        entry = await FX.submit_for_decision(conn, _decision(),
                                            account_id=ACCT, venue=VENUE, venue_positions=EMPTY_VENUE)
        assert entry["submitted"] is True, entry
        iid = entry["intent_id"]

        # ── THE PARTIAL EXIT ────────────────────────────────────────
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        pmus2, sent2 = _transport(monkeypatch, order_id="vo-exit-1", execs=[
            {"id": "vx-1", "type": "EXECUTION_TYPE_FILL",
             "lastPx": {"value": "0.30"}, "lastShares": 4,
             "order": {"state": "ORDER_STATE_FILLED"}}])
        part = await FM.submit_exit(conn, intent_id=iid, limit_price=0.30,
                                   quantity=4, adapter=pmus2, venue=VENUE,
                                   # THE INPUTS' OWN DEADLINE. A submission
                                   # without one is refused before the venue is
                                   # reached, so this test says when its prices
                                   # expire exactly as the scheduled path does.
                                   inputs_expire_at=time.time() + 30.0)
        assert part["submitted"] is True, part
        assert part["position_after"]["residual_qty"] == pytest.approx(6.0)
        assert part["position_after"]["closed_at"] is None
        # the adapter was asked to SELL, and told which side we hold
        create = [p for k, p in sent2 if k == "create"][-1]
        assert create["intent"] == "ORDER_INTENT_SELL_LONG"
        assert create["quantity"] == 4

        # ── THE RESTART ─────────────────────────────────────────────
        await conn.close()
        conn = await asyncpg.connect(DSN)
        held = await FM.open_positions(conn, account_id=ACCT, venue=VENUE)
        assert len(held) == 1
        assert held[0]["intent_id"] == iid
        assert held[0]["residual"] == pytest.approx(6.0)
        assert held[0]["holding"] is True
        assert held[0]["outstanding"] is False   # the ENTRY order is finished

        # ── THE FINAL CLOSE ─────────────────────────────────────────
        pmus3, sent3 = _transport(monkeypatch, order_id="vo-exit-2", execs=[
            {"id": "vx-2", "type": "EXECUTION_TYPE_FILL",
             "lastPx": {"value": "0.30"}, "lastShares": 6,
             "order": {"state": "ORDER_STATE_FILLED"}}])
        final = await FM.submit_exit(conn, intent_id=iid, limit_price=0.30,
                                    adapter=pmus3, venue=VENUE,
                                    inputs_expire_at=time.time() + 30.0)
        assert final["submitted"] is True, final
        assert final["position_after"]["residual_qty"] == pytest.approx(0.0)
        assert final["position_after"]["closed_reason"] == \
            "EXITED_IN_THE_MARKET"

        # ── REALISED P&L, SUMMED OVER RECORDED EVENTS ───────────────
        got = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        assert got["closed_positions"] == 1
        # 3.00 in - 6.20 out - the fees. A loss, and a measured one.
        assert got["realised_pnl_usd"] < -3.0
        assert got["max_drawdown_usd"] == pytest.approx(
            -got["realised_pnl_usd"])
        assert got["exit_proceeds_usd"] == pytest.approx(3.0)
        assert got["cost_basis_usd"] == pytest.approx(6.2)
        assert "realised equity curve" in got["realised_basis"]
        kinds = {r["kind"] for r in got["economics_by_kind"]}
        assert {"ENTRY_COST", "EXIT_PROCEEDS", "FEE"} <= kinds

        # ── AND THE STOP TRIPS ──────────────────────────────────────
        tight = dict(LIMITS, daily_loss_stop_usd=1.0)
        eff = EX.effective_limits(tight)
        rails = await FX.check_rails(conn, FX.plan_from_decision(_decision()),
                                    eff["effective"], account_id=ACCT,
                                    venue=VENUE)
        dd = [r for r in rails["rails"] if r["rail"] == "MAX_DRAWDOWN"][0]
        assert dd["verdict"] == "EXCEEDED", dd
        assert dd["measured"] > dd["limit"]
        assert "closed funded position" in dd["basis"]

        # SERVICING IS STILL AVAILABLE WITH ENTRIES OFF.
        monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", False)
        blocked = await FX.submit_for_decision(conn, _decision(),
                                              account_id=ACCT, venue=VENUE, venue_positions=EMPTY_VENUE)
        assert blocked["ok"] is False
        mg = await FM.manage(conn, account_id=ACCT, venue=VENUE,
                             adapter=pmus3, probe=lambda c, s: {
                                 "terminal_reading": "PENDING"})
        assert mg["ok"] is True
        assert mg["opened_anything"] is False
        assert mg["loss_stop"]["measured_usd"] > 0
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_settlement_closes_only_on_the_venues_own_answer():
    """AN INFERENCE IS NOT A SETTLEMENT. Only REPORTED and VOID close a funded
    position; a converged price inference and a pending market do not."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, intent_id="fpi-settle")

        for reading in ("CONVERGED_PRICE_INFERENCE", "PENDING", "UNREADABLE",
                        "UNMATCHED"):
            got = await FM.reconcile_settlement(
                conn, intent_id="fpi-settle",
                probe=lambda c, s, r=reading: {
                    "terminal_reading": r, "why": "test",
                    "authoritative_payout_present": r == "REPORTED_SETTLEMENT",
                    "reader_verdict": {"settlement_price": 1.0}})
            assert got["closed"] is False, reading
            assert got["refusal"] == FM.R_SETTLEMENT_NOT_AUTHORITATIVE
        assert await conn.fetchval(
            "SELECT closed_at FROM bettor_funded_intents "
            " WHERE intent_id='fpi-settle'") is None

        # AND A REPORTED SETTLEMENT DOES CLOSE IT, at the venue's price.
        got = await FM.reconcile_settlement(
            conn, intent_id="fpi-settle",
            probe=lambda c, s: {"terminal_reading": "REPORTED_SETTLEMENT",
                                "authoritative_payout_present": True,
                                "why": "the venue reported a price",
                                "reader_verdict": {"settlement_price": 1.0}})
        assert got["closed"] is True
        assert got["settlement_usd"] == pytest.approx(10.0)   # 10 @ 1.00
        row = await conn.fetchrow(
            "SELECT closed_reason, settlement FROM bettor_funded_intents "
            " WHERE intent_id='fpi-settle'")
        assert row["closed_reason"] == "SETTLED_BY_THE_VENUE"
        assert json.loads(row["settlement"])["payout_price"] == 1.0
        # A WIN, AND THE REALISED NUMBER SAYS SO.
        pnl = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        assert pnl["realised_pnl_usd"] > 3.0
        assert pnl["max_drawdown_usd"] == pytest.approx(0.0)
        # IDEMPOTENT: the same reading twice does not book the payout twice.
        await FM.reconcile_settlement(
            conn, intent_id="fpi-settle",
            probe=lambda c, s: {"terminal_reading": "REPORTED_SETTLEMENT",
                                "authoritative_payout_present": True,
                                "reader_verdict": {"settlement_price": 1.0}})
        again = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        assert again["realised_pnl_usd"] == \
            pytest.approx(pnl["realised_pnl_usd"])
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_exit_is_never_refused_by_the_one_position_rule():
    """THE FAILURE THAT STRANDS INVENTORY. An exit reduces exposure, so it is
    `kind='EXIT'` and the one-open-position index does not apply to it -- and
    it is not checked against the entry rails either."""
    asyncpg = pytest.importorskip("asyncpg")
    import inspect
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        await _entry(conn, intent_id="fpi-stuck")
        # the position rule refuses another ENTRY ...
        blocked = await _entry(conn, intent_id="fpi-more", vo="vo-2",
                               fill_id="vf-2")
        assert blocked["refusal"] == FB.R_ANOTHER_INTENT_IS_LIVE
        # ... and the exit path passes the same authorization boundary an
        # entry does -- an exit is still a real order -- and then stops at its
        # OWN switch, which is what "servicing outlives entry" means.
        # ... and the exit reaches its OWN switch with the entry-side
        # authorization gate NOT consulted as a blocker. REAL_ORDER_SUBMISSION
        # is off here and it does not matter: that constant governs adding
        # exposure. The servicing gate is ownership.
        got = await FM.submit_exit(conn, intent_id="fpi-stuck",
                                  limit_price=0.70)
        assert got["refusal"] == FM.R_EXIT_DISABLED, got
        assert got["servicing_gate"]["ok"] is True
        assert got["servicing_gate"]["owns_the_row"] is True
        assert got["submission_authority_for_the_record"][
            "does_not_gate_this_exit"] is True
        assert got["submission_authority_for_the_record"][
            "affirmative"] is False
        assert got["would_send"]["args"][3] is True        # sell
        assert got["plan"]["quantity"] == 10
        # AND NO EXIT PRICE IS EVER INVENTED.
        none = await FM.submit_exit(conn, intent_id="fpi-stuck")
        assert none["refusal"] == FM.R_NO_EXIT_PRICE
        # AN EXIT LARGER THAN THE INVENTORY IS REFUSED.
        over = await FM.submit_exit(conn, intent_id="fpi-stuck",
                                   limit_price=0.70, quantity=11)
        assert over["refusal"] == FM.R_OVER_RESIDUAL
        # STRUCTURAL: the exit path does not consult the entry rails.
        src = inspect.getsource(FM.submit_exit)
        assert "check_rails" not in src
    finally:
        await _clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 4 · EXPECTED FEES ARE NOT OBSERVED FEES
# ════════════════════════════════════════════════════════════════════

def test_expected_and_observed_fees_are_kept_apart():
    """PURE. A missing venue charge is PROVISIONAL, not exact."""
    at = "2026-09-27"
    absent = FB.reconcile_fee(10, 0.62, None, at=at)
    assert absent["fee_state"] == FB.FEE_PROVISIONAL
    assert absent["observed_fee_usd"] is None
    assert absent["booked_fee_usd"] == absent["expected_fee_usd"] > 0
    assert "EXPECTATION" in absent["reconciliation"]["why"]

    agrees = FB.reconcile_fee(10, 0.62, absent["expected_fee_usd"], at=at)
    assert agrees["fee_state"] == FB.FEE_RECONCILED
    assert agrees["booked_fee_usd"] == agrees["observed_fee_usd"]

    # THE VENUE CHARGED SOMETHING ELSE. The booked number is the venue's --
    # that is what the account paid -- and the difference is carried.
    other = FB.reconcile_fee(10, 0.62, absent["expected_fee_usd"] + 0.25,
                             at=at)
    assert other["fee_state"] == FB.FEE_DISAGREES
    assert other["booked_fee_usd"] == pytest.approx(other["observed_fee_usd"])
    assert other["booked_fee_usd"] != other["expected_fee_usd"]


@pg
@pytest.mark.asyncio
async def test_a_venue_charge_that_differs_is_exposed_in_cash_and_pnl():
    """THE COUNTEREXAMPLE FOR DEFECT 4. A fill the venue charged more for than
    the deployed schedule predicted: the cash, the P&L and the reconciliation
    all show the difference instead of absorbing it."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        expected, _ = FB.fee_for(10, 0.62, at=time.time())
        charged = round(expected + 0.37, 4)
        await _entry(conn, intent_id="fpi-fee", commission=charged)

        fill = await conn.fetchrow("SELECT * FROM bettor_funded_fills")
        assert fill["fee_state"] == FB.FEE_DISAGREES
        assert float(fill["expected_fee_usd"]) == pytest.approx(expected)
        assert float(fill["observed_fee_usd"]) == pytest.approx(charged)
        # THE BOOKED FEE IS WHAT THE ACCOUNT PAID.
        assert float(fill["fee_usd"]) == pytest.approx(charged)

        got = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        assert got["fees_usd"] == pytest.approx(charged)
        assert got["expected_fees_usd"] == pytest.approx(expected)
        assert got["fee_variance_usd"] == pytest.approx(0.37, abs=1e-6)
        assert got["fills_where_the_venue_charged_something_else"] == 1
        assert got["fee_discrepancies"][0]["fee_state"] == FB.FEE_DISAGREES
        # CASH OUT THE DOOR USES THE CHARGE, not the estimate.
        assert got["cash_out_the_door_usd"] == pytest.approx(6.2 + charged)

        # AND A FILL WITH NO STATED CHARGE STAYS PROVISIONAL, with the
        # incompleteness carried into the realised total once it closes.
        await FB.mark_position_closed(conn, "fpi-fee", "EXITED_IN_THE_MARKET")
        await _entry(conn, intent_id="fpi-prov", vo="vo-p", fill_id="vf-p")
        prov = await conn.fetchrow(
            "SELECT fee_state FROM bettor_funded_fills "
            " WHERE intent_id='fpi-prov'")
        assert prov["fee_state"] == FB.FEE_PROVISIONAL
        await FB.mark_position_closed(conn, "fpi-prov", "EXITED_IN_THE_MARKET")
        real = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        assert real["realised_is_provisional"] is True
        assert "PROVISIONAL" in real["provisional_note"]
    finally:
        await _clean(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# 5 · THE COMMAND CENTRE SHOWS THE BOOK AND THE DISCREPANCIES
# ════════════════════════════════════════════════════════════════════

def test_the_scheduled_lane_services_the_book_every_cycle():
    """RECURRING MANAGEMENT. Entry is opportunistic -- no admitted candidate,
    no attempt. Servicing is not: it runs on every cycle, because the drawdown
    the loss stop reads goes stale otherwise and a position that settled during
    a pause never leaves the book."""
    import inspect

    from sportsassets.workers import ext_pinnacle_loop as L

    assert callable(L._funded_service)
    src = inspect.getsource(L.cycle)
    assert "_funded_service" in src
    # IT IS NOT NESTED INSIDE THE ADMITTED-ENTRY BRANCH. The attempt hook is;
    # this one sits at cycle level, so a cycle that admits nothing still
    # services what the lane holds.
    hook = inspect.getsource(L._funded_service)
    assert "manage" in hook
    assert "submit_fok" not in hook
    assert "submit_for_decision" not in hook


@pg
@pytest.mark.asyncio
async def test_one_servicing_pass_reconciles_settles_and_measures(monkeypatch):
    """THE RECURRING PASS, END TO END, with every submission switch off.

    It reconciles the order against the venue, asks about settlement for what
    is still held, re-measures exposure and realised P&L, and reports what
    needs a decision -- opening nothing.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        pmus, sent = _transport(monkeypatch, resting=[], retrieve={})
        await _entry(conn, intent_id="fpi-svc")

        got = await FM.manage(conn, account_id=ACCT, venue=VENUE,
                              adapter=pmus,
                              probe=lambda c, s: {"terminal_reading": "PENDING",
                                                  "why": "the venue lists it"})
        assert got["ok"] is True
        assert got["opened_anything"] is False
        assert got["exposure"]["contracts_held"] == pytest.approx(10.0)
        assert got["pnl"]["realised_pnl_usd"] == pytest.approx(0.0)
        # THE STOP IS MEASURED, AND ITS THRESHOLD IS THE OWNER'S NUMBER.
        assert got["loss_stop"]["rail"] == "MAX_DRAWDOWN"
        assert got["loss_stop"]["limit_usd"] == pytest.approx(40.0)
        assert got["loss_stop"]["tripped"] is False
        # THE MISSING INPUT IS NAMED SPECIFICALLY, not as a generic "a
        # decision is needed". Here there is no probability row for this slug,
        # so the selector stops at the first input it cannot get.
        # This fixture's client publishes no book, so the selector stops at
        # the first input it cannot get and NAMES it. An unreadable book is
        # not a reason to sell and not a reason to say nothing.
        needs = [n["what"] for n in got["needs_a_decision"]]
        assert FM.R_BOOK_UNREADABLE in needs, needs
        assert got["selection"] and got["selection"][0]["ok"] is False
        assert got["exits"] == []
        assert [k for k, _ in sent if k in ("create", "cancel")] == []

        # AND THE VENUE SETTLING IT CLOSES IT ON THE NEXT PASS.
        got = await FM.manage(
            conn, account_id=ACCT, venue=VENUE, adapter=pmus,
            probe=lambda c, s: {"terminal_reading": "REPORTED_SETTLEMENT",
                                "authoritative_payout_present": True,
                                "reader_verdict": {"settlement_price": 0.0}})
        assert got["settlement"][0]["closed"] is True
        assert got["exposure"]["contracts_held"] == pytest.approx(0.0)
        assert got["exposure"]["open_positions"] == []
        # A TOTAL LOSS, MEASURED: the 10 contracts paid nothing back.
        assert got["pnl"]["realised_pnl_usd"] < -6.0
        assert got["loss_stop"]["measured_usd"] > 6.0
        # THE INVENTORY DECISION IS GONE; THE FEE ONE IS NOT, and should not
        # be: this venue stated no commission, so the fee inside that realised
        # loss is still the schedule's estimate.
        needs = [n["what"] for n in got["needs_a_decision"]]
        assert FM.R_BOOK_UNREADABLE not in needs
        assert needs == ["A_FEE_THAT_IS_NOT_RECONCILED"]
        assert got["pnl"]["realised_is_provisional"] is True
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_command_centre_shows_the_funded_book_and_what_is_unresolved():
    """A PANEL OF TOTALS HIDES EVERY DISCREPANCY. Each one is a row."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed(conn)
        expected, _ = FB.fee_for(10, 0.62, at=time.time())
        await _entry(conn, intent_id="fpi-cc", commission=expected + 0.5)
        await FB.mark_unresolved(conn, "fpi-cc", "the venue went quiet")

        got = await FB.command_center(conn)
        assert got["section"] == "Funded book"
        assert "NEVER SUMMED" in got["label"]
        assert got["book_count"] == 1
        assert got["books"][0]["account_id"] == ACCT
        assert got["books"][0]["exposure"]["contracts_held"] == \
            pytest.approx(10.0)
        kinds = {d["kind"] for d in got["unresolved_discrepancies"]}
        assert "AN_UNRESOLVED_INTENT" in kinds
        assert "A_FEE_THE_VENUE_CHARGED_DIFFERENTLY" in kinds
        assert "RESIDUAL_INVENTORY_STILL_HELD" in kinds
        assert got["unresolved_discrepancy_count"] == \
            len(got["unresolved_discrepancies"])
        assert "UNMEASURED" in got["realised_pnl_is_measured_not_assumed"]
    finally:
        await _clean(conn)
        await conn.close()
