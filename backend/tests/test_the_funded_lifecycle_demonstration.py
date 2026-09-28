"""THE CONTROLLED OPERATING DEMONSTRATION, THROUGH THE SCHEDULED LANE.

Owner directive, item 2: "Complete the scheduled lifecycle now …
Demonstrate partial exit, duplicate delivery, restart recovery, late
execution ingestion, remaining inventory and final accounting … Include
both a profitable exit and a loss-containment case. Label the
demonstration clearly and exclude it from strategy performance."

────────────────────────────────────────────────────────────────────
WHAT IS DEPLOYED CODE HERE AND WHAT IS A CHOSEN INPUT.

Every decision, every order and every ledger row below is made by the
functions the scheduled lane calls, entered at the top:

    ext_pinnacle_loop.cycle        the scheduled cycle
      -> _funded_service           the servicing pass
        -> bettor_funded_management.manage
          -> select_exit           the action AND the quantity
            -> bettor_mgmt_select.rank_with_hold
          -> submit_exit           the order, the wire price, the reserve
        -> bettor_funded_book.recover / ingest_fills / pnl / exposure

This module decides nothing, sizes nothing, prices nothing and books
nothing itself. What it supplies is INPUTS -- a probability row, a bid
ladder with depth, and the venue's answers -- and those inputs are
CHOSEN, not observed. No venue and no odds provider is read.

SO WHAT IT ESTABLISHES, EXACTLY: that the shipped software carries a
funded position from a held entry through recurring servicing, a
depth-limited partial exit, a redelivered execution, a restart, a late
execution arriving on recovery, a completing exit, and a loss-containing
exit that leaves inventory behind -- and that the ledger adds up at each
step.

IT ESTABLISHES NOTHING ABOUT OPPORTUNITY OR PROFITABILITY. Not that such
a contract existed, not that it was priced this way, not that it would
have filled. The +$3.16 in case A is arithmetic over prices I chose.

    "PRODUCTION CODE EXERCISED UNDER CONTROLLED INPUTS" IS NOT
    "PRODUCTION FUNDED BEHAVIOR VERIFIED". No funded order has been sent.

────────────────────────────────────────────────────────────────────
HOW THE EXCLUSION IS ENFORCED, AND WHERE.

The account id carries `DEMONSTRATION`, and `bettor_funded_book`
classifies every book it returns on that mark -- so
`counts_toward_strategy_performance` is False ON THE BOOK, at the reader
every consumer uses, rather than annotated in one panel. A test below
asserts it.

`FUNDED_EXIT_SUBMISSION_ENABLED` stays False in the shipped code. It is
monkeypatched True inside these tests, which is the established pattern
in `test_the_funded_lifecycle_is_complete.py`, and a test below asserts
the shipped constant is still False.
"""

import json
import os
import re
import time

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX  # noqa: F401
from sportsassets import bettor_funded_management as FM

# The harness is the funded lifecycle suite's own -- the same substituted
# transport, the same seeding, the same freshness statement. Reusing it is
# deliberate: a demonstration on a private harness would be demonstrating
# the harness.
from tests.test_the_funded_lifecycle_is_complete import (  # noqa: E402
    DSN, EVENT, LIMITS, PAYS_ON, SLUG, VENUE, _clean, _entry, _level,
    _live, _probability, _seed, _stopped, _transport, pg,
)

#: THE MARK THAT KEEPS THIS OUT OF PERFORMANCE. `bettor_funded_book`
#: classifies on it; it is not decoration.
ACCT = "acct-funded-DEMONSTRATION-lifecycle"

LABEL = "CONTROLLED DEMONSTRATION — SOFTWARE PROOF, NOT PERFORMANCE"

#: Where the trace lands. Written under the repository so it can be read
#: back, and named for what it is.
TRACE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "research", "evidence", "lifecycle")


#: FIELDS WHOSE VALUE IS A RUN, NOT A RESULT. See `_stable`.
_VOLATILE_TIME_KEYS = ("at", "recorded_at", "created_at", "updated_at",
                       "resolved_at", "closed_at", "sent_at",
                       "settlement_read_at", "outcome_at", "predicted_at",
                       "opened_at", "venue_executed_at")
#: Derived from elapsed WALL TIME during the test, so in this artifact it
#: measures how long pytest took and nothing about the position.
_VOLATILE_DERIVED_KEYS = ("capital_hours_usd_h",)
_GENERATED_ID = re.compile(r"\bfpi-[0-9a-f]{8,}\b")
#: A WALL CLOCK NESTED INSIDE AN EMBEDDED JSON STRING. The `settlement` column is
#: jsonb rendered as text, so its `"at"` is not a dict key this walk can see -- it
#: is characters inside a value. Normalising only the keys left exactly one line
#: churning per run, which is how this pattern got added: the first version of
#: this fix was measured, not assumed, and it was incomplete.
_EMBEDDED_EPOCH = re.compile(r'(\\?"(?:at|_at|[a-z_]*_at)\\?"\s*:\s*)'
                             r'\d{9,}\.\d+')

_NORMALISED_TIME = "<NORMALISED: a wall clock, not a result>"
_NORMALISED_DERIVED = "<NORMALISED: derived from test elapsed time>"
_NORMALISED_ID = "fpi-<GENERATED-PER-RUN>"


def _stable(value):
    """Replace the fields that change on every run, and nothing else.

    ── WHY THIS EXISTS ──────────────────────────────────────────────
    These traces are tracked in the repository so they can be read back, and
    they were NON-DETERMINISTIC: a uuid-generated exit `intent_id`, wall-clock
    timestamps, and `capital_hours_usd_h` computed from elapsed time during the
    test. So every run rewrote them, and the rewrite rode along in whatever
    commit happened next -- which is exactly what happened here: a `git add -A`
    swept a regenerated trace into a commit about something else, unreviewed and
    unmentioned. Inspecting that diff then showed 175 changed lines, and
    separating the real content from the noise took a measurement rather than a
    glance.

    A DIFF THAT ALWAYS CHANGES CANNOT BE INSPECTED, and inspecting the release
    diff is a standing requirement here. So the volatile fields are normalised
    to named placeholders -- the KEY is kept, so a reader still sees that the
    trace carried a timestamp -- and everything that is actually evidence is left
    exact: prices, quantities, cash, fees, residuals, drawdown, refusals and
    reasons all still differ byte for byte if the behaviour changes.

    It is normalisation, not redaction: nothing true is removed, and the file
    says what was replaced and why.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in _VOLATILE_TIME_KEYS and not isinstance(v, (dict, list)):
                out[k] = _NORMALISED_TIME if v is not None else None
            elif k in _VOLATILE_DERIVED_KEYS and not isinstance(v, (dict, list)):
                out[k] = _NORMALISED_DERIVED if v is not None else None
            else:
                out[k] = _stable(v)
        return out
    if isinstance(value, list):
        return [_stable(v) for v in value]
    if isinstance(value, str):
        return _EMBEDDED_EPOCH.sub(r"\g<1>0",
                                   _GENERATED_ID.sub(_NORMALISED_ID, value))
    return value


def _write_trace(name: str, payload: dict) -> str:
    os.makedirs(TRACE_DIR, exist_ok=True)
    path = os.path.join(TRACE_DIR, name)
    body = {
        "normalisation": {
            "what": ("fields whose value is a RUN rather than a RESULT are "
                     "replaced by a named placeholder, so this tracked file "
                     "does not change on every execution and its diff can be "
                     "inspected"),
            "time_keys": list(_VOLATILE_TIME_KEYS),
            "derived_keys": list(_VOLATILE_DERIVED_KEYS),
            "generated_ids": "fpi-<hex> becomes %r" % _NORMALISED_ID,
            "what_is_NOT_normalised": (
                "every economic quantity and every refusal: prices, "
                "quantities, cash, fees, residuals, drawdown, states and "
                "reasons are exact, so a behaviour change still shows up"),
        },
    }
    body.update(_stable(payload))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=1, default=str, sort_keys=False)
    return path




async def _seed_for(conn, account_id, *, limits=None):
    """The lifecycle suite's seeding, with the account named.

    `_seed` hard-codes its own account, so this repeats its three writes
    against ours rather than mutating the shared helper -- the funded
    suite's own fixtures must keep working unchanged.
    """
    await conn.execute("DELETE FROM bettor_desk_accounts WHERE account_id=$1",
                       account_id)
    await conn.execute(
        "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
        " paused, accounting_status, opening_balance, note) VALUES "
        "($1,'desk-demonstration','ACTIVE',false,'RECONCILED',0,"
        " 'controlled demonstration, not performance')", account_id)
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
        json.dumps({"account_id": account_id, "venue": VENUE,
                    "venue_class": FA.VENUE_FUNDED, "by": "demonstration",
                    "at": now, "expires_at": now + 3600.0, "revoked": False,
                    "effective_limits": eff["effective"],
                    "effective_digest": eff["effective_digest"]}))
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb", FA.ACCOUNT_KEY,
        json.dumps({"account_id": account_id, "venue": VENUE,
                    "approved": True}))


async def _entry_for(conn, account_id, *, intent_id, qty, price):
    """One held funded ENTRY under the named account, fully filled."""
    from sportsassets import bettor_funded_execution as _FX

    coll = _FX.collateral_for(price, qty, _FX.LONG)
    got = await FB.record_intent(
        conn, intent_id=intent_id, account_id=account_id, venue=VENUE,
        venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
        order_intent=_FX.LONG, limit_price=price, quantity=qty,
        collateral_usd=coll, effective_digest="demo",
        payout_event=PAYS_ON, held_is_long=True)
    assert got.get("ok"), got
    await FB.record_acknowledgement(conn, intent_id,
                                    venue_order_id="vo-%s" % intent_id,
                                    status="open")
    await FB.ingest_fills(conn, intent_id, [
        {"qty": float(qty), "price": price,
         "venue_fill_id": "vf-entry-%s" % intent_id}])
    return got


async def _ledger(conn, intent_id):
    """The position row and every fill on it, as the database holds them."""
    pos = await conn.fetchrow(
        "SELECT intent_id, state, kind, quantity::float8 AS qty, "
        "       residual_qty::float8 AS residual, closed_reason, "
        "       closed_at IS NOT NULL AS closed "
        "  FROM bettor_funded_intents WHERE intent_id=$1", intent_id)
    exits = await conn.fetch(
        "SELECT intent_id, state, quantity::float8 AS qty, "
        "       residual_qty::float8 AS residual, unresolved_reason "
        "  FROM bettor_funded_intents "
        " WHERE parent_intent_id=$1 ORDER BY created_at", intent_id)
    fills = await conn.fetch(
        "SELECT f.fill_id, f.intent_id, f.direction, f.qty::float8 AS qty, "
        "       f.price::float8 AS price, f.fee_state "
        "  FROM bettor_funded_fills f "
        "  LEFT JOIN bettor_funded_intents i ON i.intent_id=f.intent_id "
        " WHERE f.intent_id=$1 OR i.parent_intent_id=$1 "
        " ORDER BY f.at, f.fill_id", intent_id)
    return {
        "position": dict(pos) if pos is not None else None,
        "exit_intents": [dict(r) for r in exits],
        "fills": [dict(r) for r in fills],
        "available_to_exit": float(await conn.fetchval(
            "SELECT bettor_funded_available_to_exit($1)::float8", intent_id)),
    }


def _seams(monkeypatch, L):
    """The two seams every scheduled-lane test must state explicitly."""
    monkeypatch.delenv("EDGE_ODDS_API_KEY", raising=False)
    # The entry lane is not this demonstration's subject; the stop keeps it
    # from attempting one while servicing still runs.
    monkeypatch.setattr(L, "_running", lambda c: _stopped())
    # `book_currency_evidence` returns NO mechanism in production, so the
    # scheduled path refuses on unestablished book currency -- correctly,
    # and that refusal is a named blocker the operating view reports. This
    # demonstration's subject is the lifecycle DOWNSTREAM of admission, so
    # it says which published contract admitted the book instead of
    # pretending none is needed.
    monkeypatch.setattr(
        L, "book_currency_evidence",
        lambda slug=None: {"subscription": _live(), "revalidation": None})
    monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)


# ═════════════════════════════════════════════════════════════════════
# 0 · THE SHIPPED SWITCH IS STILL OFF, AND THE LABEL IS ENFORCED
# ═════════════════════════════════════════════════════════════════════

def test_the_shipped_exit_switch_is_still_disabled():
    """The demonstration patches it in-test; the code does not ship on."""
    import importlib

    fresh = importlib.reload(
        importlib.import_module("sportsassets.bettor_funded_management"))
    assert fresh.FUNDED_EXIT_SUBMISSION_ENABLED is False


def test_the_demonstration_account_is_classified_out_of_performance():
    assert FB.is_demonstration_account(ACCT) is True
    cls = FB.classify_book(ACCT)
    assert cls["book_class"] == FB.BOOK_CLASS_DEMONSTRATION
    assert cls["counts_toward_strategy_performance"] is False


def test_a_real_funded_account_is_not_classified_false():
    """False would claim a finding. A funded book reads None."""
    cls = FB.classify_book("acct-funded-pilot-1")
    assert cls["book_class"] == FB.BOOK_CLASS_FUNDED
    assert cls["counts_toward_strategy_performance"] is None


# ═════════════════════════════════════════════════════════════════════
# 1 · CASE A -- A PROFITABLE EXIT, IN FOUR PARTS
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_case_a_partial_exit_duplicate_delivery_restart_and_late_fill(
        monkeypatch):
    """LONG 20 @ 0.55. Every step's assertion is a database readback.

    1. depth caps the exit below the holding      -> PARTIAL EXIT
    2. the venue fills less than the exit asked   -> PARTIAL FILL
    3. the same execution is delivered again      -> NO SECOND DEDUCTION
    4. restart, then recovery reads the remainder -> LATE INGESTION
    5. a second cycle sells the rest              -> CLOSED, ACCOUNTED
    """
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets.workers import ext_pinnacle_loop as L

    trace: list = []
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await conn.execute("DELETE FROM bettor_funded_economics")
        await conn.execute("DELETE FROM bettor_funded_fills")
        await conn.execute("DELETE FROM bettor_funded_intents")
        await conn.execute(
            "DELETE FROM bettor_desk_accounts WHERE account_id=$1", ACCT)
        await _seed_for(conn, ACCT)
        await _entry_for(conn, ACCT, intent_id="dem-win", qty=20, price=0.55)
        # Holding is worth 0.50/contract. The bid pays 0.72 -- but only for
        # 8 contracts, so the SIZING rule, not this test, caps the exit.
        await _probability(conn, p=0.50)
        _seams(monkeypatch, L)

        before = await _ledger(conn, "dem-win")
        assert before["position"]["residual"] == pytest.approx(20.0)
        trace.append({"step": 0, "what": "ENTRY HELD", "ledger": before})

        # ── CYCLE 1: a depth-limited exit, partially filled ─────────
        pmus, sent, _c = _transport(
            monkeypatch, order_id="vo-xa", bids=[_level(0.72, 8)],
            # THE VENUE FILLS 5 OF THE 8 ASKED. `leavesQuantity` says 3
            # are still working, so this is a partial fill on a live
            # order -- not a cancellation.
            exec_by_call=[[{"id": "vx-a1", "type": "EXECUTION_TYPE_FILL",
                            "lastPx": {"value": "0.72"}, "lastShares": 5,
                            "order": {"state": "ORDER_STATE_PARTIALLY_FILLED",
                                      "cumQuantity": 5,
                                      "leavesQuantity": 3}}]],
            retrieve={"vo-xa-1": {
                "order": {"id": "vo-xa-1", "marketSlug": SLUG,
                          "intent": "ORDER_INTENT_SELL_LONG",
                          "price": {"value": "0.72"}, "quantity": 8,
                          "cumQuantity": 8, "leavesQuantity": 0,
                          "state": "ORDER_STATE_FILLED"},
                # BOTH executions come back on the recovery read. The
                # first is already booked, so recovery must write only
                # the second -- that is the late-ingestion case AND an
                # idempotency case at once.
                "executions": [
                    {"id": "vx-a1", "type": "EXECUTION_TYPE_FILL",
                     "lastPx": {"value": "0.72"}, "lastShares": 5},
                    {"id": "vx-a2", "type": "EXECUTION_TYPE_FILL",
                     "lastPx": {"value": "0.72"}, "lastShares": 3,
                     "commissionNotionalTotalCollected": {
                         "value": "0.0480"}}]}})

        one = await L.cycle(conn)
        svc = one["funded_servicing"]
        assert svc is not None and svc["ok"] is True, svc
        pick = svc["selection"][0]
        assert pick["ok"] is True, pick
        assert pick["selected"] in ("DIRECT_EXIT", "REDUCE")
        assert pick["limit_price"] == pytest.approx(0.72)
        # THE SIZING RULE CAPPED IT, not this test.
        assert pick["selected_qty"] == 8, (
            "the exit should be capped by the 8 contracts of depth, not by "
            "the 20 held; got %s" % pick["selected_qty"])
        # THE SEND MOVED TO THE RANKING, AND IT STILL HAPPENS. `manage` now
        # selects and DEFERS; the exit joins the one ranking that also holds
        # the indirect hedge and is dispatched from there. The assertion
        # follows the dispatch rather than being dropped -- and this case is a
        # LOSS being contained, which must still execute: positive absolute
        # P&L is not a prerequisite for a correct forward decision.
        assert svc["exits"] == [], "manage submitted before the ranking ran"
        assert svc["defer_dispatch"] is True
        _pcx = (svc.get("pair_cycle") or {}).get("exits") or []
        assert _pcx and _pcx[0]["submitted"] is True, svc.get("pair_cycle")
        assert _pcx[0]["quantity"] == pick["selected_qty"]
        assert _pcx[0]["limit_price"] == pick["limit_price"]
        create = [p for k, p in sent if k == "create"][-1]
        assert create["intent"] == "ORDER_INTENT_SELL_LONG"
        assert create["quantity"] == 8

        after_1 = await _ledger(conn, "dem-win")
        # 5 of 20 sold. 3 are still RESERVED by the live exit, so 12 are
        # available even though 15 are held -- the distinction that stops
        # an oversell.
        assert after_1["position"]["residual"] == pytest.approx(15.0)
        assert after_1["available_to_exit"] == pytest.approx(12.0)
        assert after_1["position"]["closed"] is False
        sell_fills = [f for f in after_1["fills"] if f["direction"] == "EXIT"]
        assert len(sell_fills) == 1
        assert sell_fills[0]["qty"] == pytest.approx(5.0)
        trace.append({"step": 1, "what": "PARTIAL EXIT, PARTIALLY FILLED",
                      "selected": pick["selected"],
                      "selected_qty": pick["selected_qty"],
                      "limit_price": pick["limit_price"],
                      "venue_create_payload": create,
                      "ledger": after_1})

        # ── DUPLICATE DELIVERY: the same execution, again ───────────
        xid = after_1["exit_intents"][0]["intent_id"]
        dup = await FB.ingest_fills(conn, xid, [
            {"qty": 5.0, "price": 0.72, "venue_fill_id": "vx-a1"}])
        after_dup = await _ledger(conn, "dem-win")
        assert after_dup["position"]["residual"] == pytest.approx(15.0), (
            "a redelivered execution deducted inventory a second time")
        assert len([f for f in after_dup["fills"]
                    if f["direction"] == "EXIT"]) == 1
        trace.append({"step": 2, "what": "DUPLICATE DELIVERY IGNORED",
                      "ingest_result": dup, "ledger": after_dup})

        # ── RESTART, THEN RECOVERY READS THE LATE EXECUTION ─────────
        #
        # THE BOOK IS EMPTIED FOR THIS CYCLE, DELIBERATELY. A servicing
        # pass RECOVERS AND THEN ACTS in the same cycle -- my first
        # version of this step left the 0.72 book installed and the pass
        # correctly sold again, which is right behaviour and made the
        # late-ingestion arithmetic unreadable. With no executable level
        # the pass recovers and refuses to sell, so the 3 late contracts
        # are the only movement in it.
        _transport(
            monkeypatch, order_id="vo-xa", bids=[],
            retrieve={"vo-xa-1": {
                "order": {"id": "vo-xa-1", "marketSlug": SLUG,
                          "intent": "ORDER_INTENT_SELL_LONG",
                          "price": {"value": "0.72"}, "quantity": 8,
                          "cumQuantity": 8, "leavesQuantity": 0,
                          "state": "ORDER_STATE_FILLED"},
                "executions": [
                    {"id": "vx-a1", "type": "EXECUTION_TYPE_FILL",
                     "lastPx": {"value": "0.72"}, "lastShares": 5},
                    {"id": "vx-a2", "type": "EXECUTION_TYPE_FILL",
                     "lastPx": {"value": "0.72"}, "lastShares": 3,
                     "commissionNotionalTotalCollected": {
                         "value": "0.0480"}}]}})
        await conn.close()
        conn = await asyncpg.connect(DSN)
        two = await L.cycle(conn)
        svc2 = two["funded_servicing"]
        assert svc2["recovered"]["ok"] is True, svc2["recovered"]
        rec = svc2["recovered"]["reconciled"][0]
        # It read BOTH executions and wrote only the one it did not have.
        assert rec["executions_read"] == 2, rec
        assert rec["fills_written"] == 1, rec
        after_2 = await _ledger(conn, "dem-win")
        assert after_2["position"]["residual"] == pytest.approx(12.0)
        assert after_2["available_to_exit"] == pytest.approx(12.0)
        # AND NOTHING ELSE WAS SOLD IN THAT PASS, for a stated reason.
        sel2 = (svc2.get("selection") or [{}])[0]
        assert sel2.get("ok") is not True
        assert sel2.get("refusal") == FM.R_NO_EXIT_SIDE, sel2
        assert len([f for f in after_2["fills"]
                    if f["direction"] == "EXIT"]) == 2
        trace.append({"step": 3,
                      "what": "RESTART, LATE EXECUTION INGESTED ON RECOVERY",
                      "reconciled": rec,
                      "same_pass_sold_nothing_because": sel2.get("refusal"),
                      "ledger": after_2})

        # ── CYCLE 3: sell the remaining 12 and close the position ───
        pmus2, sent2, _c2 = _transport(
            monkeypatch, order_id="vo-xb", bids=[_level(0.70, 40)])
        three = await L.cycle(conn)
        svc3 = three["funded_servicing"]
        assert svc3["ok"] is True, svc3
        pick3 = svc3["selection"][0]
        assert pick3["selected_qty"] == 12, pick3
        after_3 = await _ledger(conn, "dem-win")
        assert after_3["position"]["residual"] == pytest.approx(0.0)
        assert after_3["position"]["closed_reason"] == "EXITED_IN_THE_MARKET"
        trace.append({"step": 4, "what": "COMPLETING EXIT, POSITION CLOSED",
                      "selected_qty": pick3["selected_qty"],
                      "limit_price": pick3["limit_price"],
                      "ledger": after_3})

        # ── FINAL ACCOUNTING, FROM THE PRODUCTION READER ────────────
        pl = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        # bought 20 @ 0.55 = 11.00; sold 5+3 @ 0.72 = 5.76 and 12 @ 0.70
        # = 8.40, so 14.16 of proceeds against 11.00 of basis.
        assert pl["cost_basis_usd"] == pytest.approx(11.00, abs=1e-6)
        assert pl["exit_proceeds_usd"] == pytest.approx(14.16, abs=1e-6)
        # 14.16 - 11.00 = 3.16 GROSS, less 0.638 of fees = 2.522. My
        # first expectation here was ">3.0", which silently assumed
        # fee-free arithmetic. The identity below is the real check.
        assert pl["realised_pnl_usd"] == pytest.approx(2.522, abs=1e-3)
        assert pl["closed_positions"] == 1
        assert pl["fees_usd"] > 0, "the venue's commission was not booked"
        # PROCEEDS - BASIS - FEES, and nothing smoothed.
        assert pl["realised_pnl_usd"] == pytest.approx(
            pl["exit_proceeds_usd"] - pl["cost_basis_usd"] - pl["fees_usd"],
            abs=1e-6)
        trace.append({"step": 5, "what": "FINAL ACCOUNTING (PROFITABLE)",
                      "pnl": pl,
                      "exposure": await FB.exposure(conn, account_id=ACCT,
                                                    venue=VENUE)})

        path = _write_trace("CASE_A_PROFITABLE_EXIT.json", {
            "label": LABEL,
            "counts_toward_strategy_performance": False,
            "what_this_establishes": (
                "that the deployed scheduled lane carries a held position "
                "through a depth-limited partial exit, a partial fill, a "
                "redelivered execution, a restart, a late execution on "
                "recovery and a completing exit, and that the ledger adds "
                "up at each step"),
            "what_this_does_not_establish": (
                "anything about opportunity or profitability. The prices, "
                "the depth and the probability are CHOSEN. No funded order "
                "was sent; FUNDED_EXIT_SUBMISSION_ENABLED ships False and "
                "was patched inside this test"),
            "account_id": ACCT, "venue": VENUE, "position": "dem-win",
            "steps": trace})
        assert os.path.exists(path)
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · CASE B -- LOSS CONTAINMENT, LEAVING INVENTORY BEHIND
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_case_b_a_loss_is_contained_and_inventory_remains(monkeypatch):
    """LONG 15 @ 0.60 into a fallen book. Selling LOCKS A LOSS and wins.

    This is the case a lane that only takes profits never reaches: the bid
    is 0.41, well under the 0.60 basis, so the exit realises −0.19 a
    contract. It is still the best available action because holding is
    worth 0.30. The depth covers 9 of the 15, so 6 contracts REMAIN HELD
    and the position does not close.
    """
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets.workers import ext_pinnacle_loop as L

    trace: list = []
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DELETE FROM bettor_funded_economics")
        await conn.execute("DELETE FROM bettor_funded_fills")
        await conn.execute("DELETE FROM bettor_funded_intents")
        await conn.execute(
            "DELETE FROM bettor_desk_accounts WHERE account_id=$1", ACCT)
        await _seed_for(conn, ACCT)
        await _entry_for(conn, ACCT, intent_id="dem-loss", qty=15, price=0.60)
        await _probability(conn, p=0.30)
        _seams(monkeypatch, L)

        _pm, sent, _c = _transport(
            monkeypatch, order_id="vo-xl", bids=[_level(0.41, 9)])
        one = await L.cycle(conn)
        svc = one["funded_servicing"]
        assert svc["ok"] is True, svc
        pick = svc["selection"][0]
        assert pick["ok"] is True, pick
        assert pick["limit_price"] == pytest.approx(0.41)
        assert pick["selected_qty"] == 9, pick
        # THE CHOSEN ACTION KNOWS IT IS REALISING A LOSS AND IS CHOSEN
        # ANYWAY, because holding is worth less. That is containment, and
        # it must be VISIBLE in the servicing result -- the projection
        # used to drop the candidate table and report only the winner, so
        # nothing an operator could read said the exit locked a loss.
        rank = pick["ranking"]
        assert rank["selected_locks_a_loss"] is True, (
            "a 0.41 exit on a 0.60 basis must be reported as locking a "
            "loss; got %r" % rank.get("selected_candidate"))
        assert rank["selected_is_depth_limited"] is True
        assert rank["selected_candidate"]["action"] == rank["selected"]
        assert rank["candidates"], "the candidate table did not travel"
        # THE SEND MOVED TO THE RANKING, AND IT STILL HAPPENS. `manage` now
        # selects and DEFERS; the exit joins the one ranking that also holds
        # the indirect hedge and is dispatched from there. The assertion
        # follows the dispatch rather than being dropped -- and this case is a
        # LOSS being contained, which must still execute: positive absolute
        # P&L is not a prerequisite for a correct forward decision.
        assert svc["exits"] == [], "manage submitted before the ranking ran"
        assert svc["defer_dispatch"] is True
        _pcx = (svc.get("pair_cycle") or {}).get("exits") or []
        assert _pcx and _pcx[0]["submitted"] is True, svc.get("pair_cycle")
        assert _pcx[0]["quantity"] == pick["selected_qty"]
        assert _pcx[0]["limit_price"] == pick["limit_price"]

        after = await _ledger(conn, "dem-loss")
        # ── REMAINING INVENTORY. The position is NOT closed. ────────
        assert after["position"]["residual"] == pytest.approx(6.0)
        assert after["position"]["closed"] is False
        assert after["position"]["closed_reason"] is None
        trace.append({"step": 1, "what": "LOSS-CONTAINING PARTIAL EXIT",
                      "selected": pick["selected"],
                      "selected_qty": pick["selected_qty"],
                      "limit_price": pick["limit_price"],
                      "locks_a_loss": True,
                      "venue_create_payload":
                          [p for k, p in sent if k == "create"][-1],
                      "ledger": after})

        pl = await FB.pnl(conn, account_id=ACCT, venue=VENUE)
        # 9 sold at 0.41 = 3.69 against 9 * 0.60 = 5.40 of basis.
        assert pl["exit_proceeds_usd"] == pytest.approx(3.69, abs=1e-6)
        # ── REALISED IS -2.01, AND THE OLD 0.0 WAS THE BUG ──────────
        #
        # THIS BLOCK ASSERTED THE DEFECT, and its own comment recorded me
        # concluding otherwise: "I expected a negative number here and was
        # wrong about the code, not the other way round." The instinct was
        # right and the conclusion was wrong.
        #
        # `FB.realised` DID filter `closed_at IS NOT NULL`, so a position
        # that had sold 9 of 15 contracts below cost reported 0.00 -- and
        # every consumer of MAX_DRAWDOWN reads that figure, so the approved
        # loss stop could be breached without tripping. Calling the filter
        # "the convention" turned a disarmed control into a requirement, in
        # a CAPITAL-PATH test.
        #
        # An independent audit reproduced the same defect from the other
        # direction (a $20 drawdown erased by recovery and closure), and
        # `realised` now books each exit fill as an increment at its own
        # instant.
        #
        # BOTH FACTS COEXIST, which is what the old comment got right:
        # nothing has CLOSED (6 contracts still held), and a result HAS
        # been realised on the 9 that were sold. The error was inferring
        # the second from the first.
        assert pl["realised_pnl_usd"] == pytest.approx(-2.01, abs=1e-6)
        assert pl["max_drawdown_usd"] == pytest.approx(2.01, abs=1e-6)
        assert pl["closed_positions"] == 0
        assert pl["partially_realised_open_positions"] == 1
        assert pl["partially_realised_usd"] == pytest.approx(-2.01, abs=1e-6)
        # WHERE THE LOSS IS VISIBLE INSTEAD: the open side's net cash.
        # 9.00 of basis out, 3.69 of proceeds back, 0.40 of fees.
        assert pl["cost_basis_usd"] == pytest.approx(9.00, abs=1e-6)
        assert pl["open_position_net_cash_usd"] == pytest.approx(
            -5.71, abs=1e-6)
        assert pl["cash_out_the_door_usd"] == pytest.approx(5.71, abs=1e-6)
        # ── PROVISIONAL IS NOW True, AND THAT IS CORRECT ────────────
        #
        # It read False when the realised total was empty: with no booked
        # results there were no provisional events to count. Now that the
        # two exit fills ARE booked, their fee state is counted -- and this
        # demonstration supplies no venue-stated commission, so the fees
        # are the schedule's EXPECTED figures. PROVISIONAL is the honest
        # label for a result whose fees the venue has not confirmed, and
        # asserting False would now be asserting that an unconfirmed fee is
        # final.
        assert "realised_pnl_usd" in pl
        assert pl["realised_is_provisional"] is True
        assert pl["realised_provisional_note"], pl
        # ── AND THE SLICE-LEVEL RESULT, WHICH I FIRST DECLINED TO SPLIT ──
        #
        # I wrote here that splitting it "needs a cost-attribution convention
        # (FIFO or average) ... and this lane declares none". THAT WAS WRONG
        # on the facts: `remaining_basis` already declares average entry cost
        # per contract for a void refund, so the convention existed and I had
        # not looked. `realised_on_sold` uses that same one.
        #
        # I ALSO GAVE THE WRONG NUMBER. I said the sold slice was −$1.71.
        # That is 3.69 − 5.40 with NO fees. This clip pays 0.25 of entry fee
        # and 0.15 of exit fee, and 9/15 of the entry fee belongs to the sold
        # side, so the fees on the sold quantity are 0.30 and the result is
        # −$2.01. −$1.71 understated the loss by exactly those fees.
        sold = await FB.realised_on_sold(conn, "dem-loss")
        assert sold["sold_qty"] == pytest.approx(9.0)
        assert sold["exit_proceeds_usd"] == pytest.approx(3.69, abs=1e-6)
        assert sold["allocated_basis_usd"] == pytest.approx(5.40, abs=1e-6)
        assert sold["fees_on_sold_usd"] == pytest.approx(0.30, abs=1e-6)
        assert sold["realised_on_sold_usd"] == pytest.approx(-2.01, abs=1e-6)
        assert sold["remaining_basis_usd"] == pytest.approx(3.60, abs=1e-6)
        assert sold["attribution"] == "AVERAGE_ENTRY_COST_PER_CONTRACT"
        # NET CASH IS NOT THAT NUMBER, and the gap is arithmetic: the basis
        # still held (3.60) plus the residual's share of the entry fee (0.10).
        assert pl["open_position_net_cash_usd"] == pytest.approx(
            sold["realised_on_sold_usd"] - sold["remaining_basis_usd"]
            - sold["entry_fees_allocated_to_residual_usd"], abs=1e-6)
        trace.append({"step": 2.5,
                      "what": "THE PARTIAL RESULT, SEPARATED",
                      "realised_on_sold": sold,
                      "two_corrections": [
                          "I said the lane declared no cost-attribution "
                          "convention. `remaining_basis` already declared "
                          "average entry cost per contract.",
                          "I said the sold slice was −$1.71. That figure is "
                          "fee-free. With 0.30 of fees on the sold quantity "
                          "it is −$2.01."]})
        # The 6 still held are NOT marked. Unrealised is UNMEASURED, not 0.
        exp = await FB.exposure(conn, account_id=ACCT, venue=VENUE)
        assert exp["contracts_held"] == pytest.approx(6.0)
        trace.append({"step": 2, "what": "FINAL ACCOUNTING (LOSS CONTAINED)",
                      "pnl": pl, "exposure": exp})

        # ── THE OPERATOR VIEW: classified, and the residual is named ──
        cc = await FB.command_center(conn)
        book = [b for b in cc["books"] if b["account_id"] == ACCT][0]
        assert book["book_class"] == FB.BOOK_CLASS_DEMONSTRATION
        assert book["counts_toward_strategy_performance"] is False
        assert cc["demonstration_book_count"] >= 1
        assert book not in cc["funded_books"]
        stranded = [d for d in cc["unresolved_discrepancies"]
                    if d["kind"] == "RESIDUAL_INVENTORY_STILL_HELD"
                    and d["intent_id"] == "dem-loss"]
        assert stranded and stranded[0]["residual_qty"] == pytest.approx(6.0)
        trace.append({"step": 3, "what": "OPERATOR VIEW",
                      "book": book,
                      "demonstration_book_count":
                          cc["demonstration_book_count"],
                      "funded_book_count": cc["funded_book_count"],
                      "residual_discrepancy": stranded[0]})

        path = _write_trace("CASE_B_LOSS_CONTAINMENT.json", {
            "label": LABEL,
            "counts_toward_strategy_performance": False,
            "what_this_establishes": (
                "that the lane exits at a LOSS when holding is worth less, "
                "that the chosen action is marked as locking a loss rather "
                "than presented as a gain, that a depth-limited exit leaves "
                "the balance held rather than stranding or oversells it, "
                "and that the residual appears in the operator view as "
                "something to act on"),
            "what_this_does_not_establish": (
                "that a 0.41 bid existed for 9 contracts. It is a "
                "CHOSEN input. And note what the ledger does NOT say: the "
                "−$2.01 realised on the 9 sold is not booked as "
                "`realised_pnl_usd`, because the position is still open and "
                "that figure books on CLOSURE. Net cash on the open "
                "position is −$5.71, which is NOT the partial result: it "
                "exceeds it by the 3.60 of basis still held plus the 0.10 "
                "of entry fee sitting on the 6 residual contracts"),
            "two_figures_i_published_wrongly_and_have_corrected": [
                "−$1.71 as the sold slice. That is the FEE-FREE arithmetic "
                "(3.69 − 5.40). The sold quantity carries 0.30 of fees, so "
                "the result is −$2.01. The understatement was the fees.",
                "that this lane declares no cost-attribution convention. "
                "`remaining_basis` already declared average entry cost per "
                "contract for void refunds; `realised_on_sold` uses it."],
            "account_id": ACCT, "venue": VENUE, "position": "dem-loss",
            "steps": trace})
        assert os.path.exists(path)
    finally:
        await conn.execute("DELETE FROM bettor_funded_economics")
        await conn.execute("DELETE FROM bettor_funded_fills")
        await conn.execute("DELETE FROM bettor_funded_intents")
        await conn.execute(
            "DELETE FROM bettor_desk_accounts WHERE account_id=$1", ACCT)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · THE TRACE SAYS WHAT IT IS NOT
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", ["CASE_A_PROFITABLE_EXIT.json",
                                  "CASE_B_LOSS_CONTAINMENT.json"])
def test_each_written_trace_carries_the_exclusion_and_the_limits(name):
    path = os.path.join(TRACE_DIR, name)
    if not os.path.exists(path):
        pytest.skip("trace not written in this run (needs RN1X_TEST_DSN)")
    with open(path, "r", encoding="utf-8") as fh:
        t = json.load(fh)
    assert t["counts_toward_strategy_performance"] is False
    assert "NOT PERFORMANCE" in t["label"]
    assert "CHOSEN" in t["what_this_does_not_establish"]
    assert t["steps"], "a trace with no steps establishes nothing"
