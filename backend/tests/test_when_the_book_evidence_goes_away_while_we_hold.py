"""WHAT HAPPENS WHEN THE EVIDENCE DISAPPEARS AND THE INVENTORY DOES NOT.

Owner requirement (priority 3): "Complete the production book-evidence and
servicing requirements. Demonstrate what happens when evidence becomes
unavailable while inventory exists. Keep funded entry disabled until the
proposed pilot has a qualified management path."

THIS IS NOT A HYPOTHETICAL. It is the CURRENT PRODUCTION STATE, and worse
than "not yet wired". `ext_pinnacle_loop.book_currency_evidence` supplies
no mechanism, and the reason is a property of the FEED rather than of our
code: M1 was VERIFIED against the shipped market-data client and cannot
establish currency, because the payload carries no sequence number (so a
dropped message is undetectable) and nothing distinguishes a snapshot from
an increment (so a received message is not known to be a whole book).
Subscribing does not supply either. M2 needs the book endpoint to emit a
validator and that is not yet known.

So a funded pilot authorised today would hold inventory in exactly this
state, and the question "what does it do then" is the one that decides
whether the pilot has a qualified management path. These tests answer it
by DEMONSTRATION rather than by assertion.

THE FOUR PROPERTIES THAT MAKE IT SAFE RATHER THAN MERELY STUCK:

  1 NO ORDER IS SENT on an unevidenced book. The refusal is by name.
  2 THE INVENTORY IS NOT LOST. The residual is unchanged and the position
    is still held, with the blocker recorded against it.
  3 RECONCILIATION AND SETTLEMENT ARE NOT GATED. A fill that landed while
    the evidence was away still reaches the book -- otherwise an outage
    would mean the money moved and the book did not know.
  4 IT IS VISIBLE TO AN OPERATOR as something to act on, not a silence.

A LOOP THAT REFUSES SAFELY IS STILL NOT A COMPLETED TRADING SYSTEM, and
nothing here claims otherwise. This is containment working correctly. The
capability is UNFINISHED and the blocker is the venue's feed.
"""

import pytest

from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_management as FM

from tests.test_the_funded_lifecycle_is_complete import (  # noqa: E402
    ACCT, DSN, EVENT, PAYS_ON, SLUG, VENUE, _clean, _level, _live,
    _probability, _seed, _transport, pg,
)


async def _hold(conn, *, intent_id, qty=15, price=0.60):
    """An ordinary held position: filled, nothing exited."""
    from sportsassets import bettor_funded_execution as FX

    got = await FB.record_intent(
        conn, intent_id=intent_id, account_id=ACCT, venue=VENUE,
        venue_class=FA.VENUE_FUNDED, us_market_slug=SLUG, event_key=EVENT,
        order_intent=FX.LONG, limit_price=price, quantity=qty,
        collateral_usd=FX.collateral_for(price, qty, FX.LONG),
        effective_digest="d", payout_event=PAYS_ON, held_is_long=True)
    assert got.get("ok"), got
    await FB.record_acknowledgement(conn, intent_id,
                                    venue_order_id="vo-%s" % intent_id,
                                    status="open")
    await FB.ingest_fills(conn, intent_id, [
        {"qty": float(qty), "price": price,
         "venue_fill_id": "vf-e-%s" % intent_id}])
    return got


async def _fresh(conn, *, with_probability=True):
    """A clean database, WITH an eligible probability row by default.

    WHY THE PROBABILITY MUST BE THERE, and it is a finding rather than
    harness bookkeeping. `select_exit` refuses in a fixed order, and
    `R_NO_PROBABILITY` comes BEFORE `R_BOOK_NOT_FRESH`:

        NOTHING_HELD -> NO_PAYOUT_EVENT -> NO_BASIS -> BOOK_UNREADABLE
        -> NO_EXIT_SIDE -> NO_PROBABILITY -> BOOK_NOT_FRESH -> ...

    So a held position with no valuation row never reaches the
    book-evidence gate at all: it stops one step earlier. My first version
    of these tests omitted the row and asserted BOOK_NOT_FRESH, and the
    code correctly answered NO_ELIGIBLE_PROBABILITY_ROW_PRICES_THIS_CONTRACT
    -- my expectation was wrong about the ordering, not the code.

    That ordering matters operationally: in the 399-row production state
    there is no probability EITHER, so the first thing a funded position
    would report is the absent valuation, and the book-evidence blocker is
    the one behind it. Both have to be cleared, in that order.
    """
    await _clean(conn)
    await conn.execute("DELETE FROM bettor_funded_economics")
    await conn.execute("DELETE FROM bettor_funded_fills")
    await conn.execute("DELETE FROM bettor_funded_intents")
    await _seed(conn)
    if with_probability:
        await _probability(conn)


# ═════════════════════════════════════════════════════════════════════
# 0 · THE BASELINE, so the demonstration can distinguish two states
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_with_the_mechanism_supplied_the_exit_is_at_least_considered(monkeypatch):
    """Evidence present: selection reaches the economics rather than refusing
    on freshness. Without this, every later test would pass for the wrong
    reason -- a harness that cannot select is not evidence that the ABSENCE
    of evidence is what blocked it."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        await _hold(conn, intent_id="ev-base")
        _transport(monkeypatch, order_id="vo-x", bids=[_level(0.55, 20)])
        p = (await FM.open_positions(conn, account_id=ACCT, venue=VENUE))[0]
        pick = await FM.select_exit(conn, p, **_live_kwargs())
        # It may refuse for an ECONOMIC or a valuation reason -- that is
        # fine and is a different lane's problem. What it must NOT do is
        # refuse on book freshness, because the mechanism was supplied.
        assert pick.get("refusal") != FM.R_BOOK_NOT_FRESH, pick
    finally:
        await _clean(conn)
        await conn.close()


def _live_kwargs():
    ev = _live()
    return {"subscription": ev, "revalidation": None}


@pg
@pytest.mark.asyncio
async def test_the_absent_valuation_is_reported_before_the_absent_book_evidence(monkeypatch):
    """TWO BLOCKERS, IN A FIXED ORDER, and the order is the operational fact.

    With no probability row the position stops at R_NO_PROBABILITY and never
    reaches the book-evidence gate. That is what a funded position would
    report today, because the 399-row production state has no valuation
    either. Both blockers have to be cleared, and this one first.

    I got this wrong writing these tests: I omitted the row and asserted
    the book refusal. The code answered the earlier one, correctly.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn, with_probability=False)
        await _hold(conn, intent_id="ev-order")
        _pmus, sent, _c = _transport(monkeypatch, order_id="vo-x",
                                     bids=[_level(0.55, 20)])
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        p = (await FM.open_positions(conn, account_id=ACCT, venue=VENUE))[0]
        pick = await FM.select_exit(conn, p)
        assert pick.get("ok") is False
        assert pick["refusal"] == FM.R_NO_PROBABILITY, pick
        # AND STILL NOTHING IS SENT. A different refusal is not a weaker one.
        assert not [s for s in sent if s[0] == "create"], sent
        # Supplying the probability advances it to the NEXT blocker, which is
        # the book evidence -- proving the two are ordered, not alternatives.
        await _probability(conn)
        pick2 = await FM.select_exit(conn, p)
        assert pick2["refusal"] == FM.R_BOOK_NOT_FRESH, pick2
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 1 · NO ORDER IS SENT, AND THE REFUSAL IS BY NAME
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_no_exit_is_sent_when_the_book_evidence_is_unavailable(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        await _hold(conn, intent_id="ev-1")
        # A perfectly good bid is on the book. The refusal must come from
        # the ABSENT EVIDENCE, not from absent liquidity -- otherwise the
        # demonstration proves nothing about evidence at all.
        _pmus, sent, _c = _transport(monkeypatch, order_id="vo-x",
                                     bids=[_level(0.55, 20)])
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)

        p = (await FM.open_positions(conn, account_id=ACCT, venue=VENUE))[0]
        pick = await FM.select_exit(conn, p)          # no mechanism supplied
        assert pick.get("ok") is False
        assert pick["refusal"] == FM.R_BOOK_NOT_FRESH, pick
        assert pick.get("book_refusal"), "the freshness verdict must name itself"
        # AND NOTHING WAS SENT. Even with the submission switch forced on.
        assert not [s for s in sent if s[0] == "create"], sent
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_refusal_says_probability_freshness_is_not_book_freshness(monkeypatch):
    """The two clocks are separate and the refusal must say so.

    A fresh Pinnacle price is not evidence that the venue's book is current,
    and an earlier revision of this lane treated one as the other.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        await _hold(conn, intent_id="ev-2")
        _transport(monkeypatch, order_id="vo-x", bids=[_level(0.55, 20)])
        p = (await FM.open_positions(conn, account_id=ACCT, venue=VENUE))[0]
        pick = await FM.select_exit(conn, p)
        assert pick["refusal"] == FM.R_BOOK_NOT_FRESH
        assert "probability freshness does not establish book freshness" in (
            pick.get("note") or "")
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · THE INVENTORY IS NOT LOST, AND THE BLOCKER IS RECORDED
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_residual_survives_a_full_management_pass_with_no_evidence(monkeypatch):
    """A whole `manage` cycle over held inventory with the evidence gone.

    THIS IS THE DEMONSTRATION THE BRIEF ASKS FOR. The pass must complete,
    send nothing, leave the inventory exactly as it was, and name what is
    missing per position.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        await _hold(conn, intent_id="ev-3", qty=15, price=0.60)
        _pmus, sent, _c = _transport(monkeypatch, order_id="vo-x",
                                     bids=[_level(0.55, 20)])
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)

        got = await FM.manage(conn, account_id=ACCT, venue=VENUE)
        # THE PASS COMPLETED. A refusal is not an exception.
        assert got.get("open_positions"), got
        # NOTHING WAS SENT.
        assert not [s for s in sent if s[0] == "create"], sent
        assert got.get("exits") == [], got.get("exits")
        # THE POSITION IS NAMED, WITH ITS BLOCKER AND ITS RESIDUAL.
        owed = [d for d in got["needs_a_decision"]
                if d["intent_id"] == "ev-3"]
        assert owed, got["needs_a_decision"]
        assert owed[0]["what"] == FM.R_BOOK_NOT_FRESH
        assert owed[0]["residual_qty"] == pytest.approx(15.0)
        assert owed[0]["us_market_slug"] == SLUG
        # AND THE INVENTORY IS UNTOUCHED IN THE LEDGER.
        row = await conn.fetchrow(
            "SELECT residual_qty::float8 AS r, closed_at "
            "  FROM bettor_funded_intents WHERE intent_id='ev-3'")
        assert row["r"] == pytest.approx(15.0)
        assert row["closed_at"] is None
        exp = await FB.exposure(conn, account_id=ACCT, venue=VENUE)
        assert exp["contracts_held"] == pytest.approx(15.0)
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_repeated_passes_do_not_accumulate_or_drift(monkeypatch):
    """An outage lasts many cycles. Three passes must be the same as one.

    The failure this guards against is a refusal that nonetheless mutates
    something each time -- a duplicated discrepancy row, a decremented
    residual, a growing decision list treated as progress.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        await _hold(conn, intent_id="ev-4")
        _transport(monkeypatch, order_id="vo-x", bids=[_level(0.55, 20)])
        seen = []
        for _ in range(3):
            got = await FM.manage(conn, account_id=ACCT, venue=VENUE)
            owed = [d for d in got["needs_a_decision"]
                    if d["intent_id"] == "ev-4"]
            # TWO THINGS ARE GENUINELY OWED HERE and that is not drift: the
            # book evidence blocks the exit, and the entry fill's fee is
            # PROVISIONAL because the venue stated no commission on it. My
            # first version asserted exactly one entry and was wrong about
            # what the position owes, not about accumulation. What must not
            # change is the SET -- an outage that grew the list each pass
            # would look like progress.
            codes = sorted({d["what"] for d in owed})
            assert FM.R_BOOK_NOT_FRESH in codes, owed
            seen.append((len(owed), tuple(codes)))
            ex = [d for d in owed if d["what"] == FM.R_BOOK_NOT_FRESH]
            assert ex[0]["residual_qty"] == pytest.approx(15.0)
        assert len(set(seen)) == 1, (
            "three passes must owe exactly the same things: %r" % (seen,))
        row = await conn.fetchrow(
            "SELECT residual_qty::float8 AS r FROM bettor_funded_intents "
            " WHERE intent_id='ev-4'")
        assert row["r"] == pytest.approx(15.0)
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · RECONCILIATION IS NOT GATED ON THE EVIDENCE
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_fill_that_landed_during_the_outage_still_reaches_the_book(monkeypatch):
    """THE CASE THAT MATTERS MOST, and the one a naive gate would break.

    If book evidence gated the whole pass, an exit that filled at the venue
    while our evidence was unavailable would be invisible: the money moved
    and the book did not know. Reconciliation and settlement reads are
    deliberately NOT gated, and this proves it on a real fill.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        await _hold(conn, intent_id="ev-5", qty=15, price=0.60)
        # An EXIT child that the venue filled while we were not looking.
        _transport(monkeypatch, order_id="vo-x-ev-5",
                   bids=[_level(0.55, 9)],
                   exec_by_call=[[{"id": "vx-out", "type": "EXECUTION_TYPE_FILL",
                                  "lastPx": {"value": "0.55"},
                                  "lastShares": 9,
                                  "order": {"state": "ORDER_STATE_FILLED"}}]])
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        import time as _t
        ex = await FM.submit_exit(conn, intent_id="ev-5", limit_price=0.55,
                                  quantity=9, venue=VENUE,
                                  inputs_expire_at=_t.time() + 600.0)
        assert ex.get("submitted") is True, ex
        after = await conn.fetchrow(
            "SELECT residual_qty::float8 AS r FROM bettor_funded_intents "
            " WHERE intent_id='ev-5'")
        assert after["r"] == pytest.approx(6.0), (
            "the fill must have reduced the inventory")

        # NOW the evidence is gone, and a management pass runs anyway.
        _transport(monkeypatch, order_id="vo-x2", bids=[_level(0.55, 20)])
        got = await FM.manage(conn, account_id=ACCT, venue=VENUE)
        # Reconciliation and the economics repair both RAN.
        assert "economics_repair" in got, got.keys()
        assert got.get("economics_repair_error") is None
        # The partial result is still computable without any book evidence:
        # it is made of our own fills, not of the venue's current book.
        sold = await FB.realised_on_sold(conn, "ev-5")
        assert sold["ok"] is True
        assert sold["sold_qty"] == pytest.approx(9.0)
        assert sold["residual_qty"] == pytest.approx(6.0)
        # And the remaining 6 are still refused an exit, by name.
        owed = [d for d in got["needs_a_decision"]
                if d["intent_id"] == "ev-5"]
        assert owed and owed[0]["what"] == FM.R_BOOK_NOT_FRESH, got
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · AND IT IS VISIBLE TO AN OPERATOR
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_operator_view_shows_held_inventory_it_cannot_act_on(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _fresh(conn)
        await _hold(conn, intent_id="ev-6")
        _transport(monkeypatch, order_id="vo-x", bids=[_level(0.55, 20)])
        await FM.manage(conn, account_id=ACCT, venue=VENUE)
        cc = await FB.command_center(conn)
        held = [d for d in cc["unresolved_discrepancies"]
                if d["kind"] == "RESIDUAL_INVENTORY_STILL_HELD"
                and d["intent_id"] == "ev-6"]
        assert held, "inventory we cannot act on must not be a silence"
        assert held[0]["residual_qty"] == pytest.approx(15.0)
        # The partial breakdown is present and honest: nothing sold yet.
        p = held[0]["partial_exit_result"]
        assert p["sold_qty"] == pytest.approx(0.0)
        assert p["realised_on_sold_usd"] == pytest.approx(0.0)
        assert p["remaining_basis_usd"] == pytest.approx(9.0, abs=1e-6)
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# 5 · WHY THIS IS THE VENUE'S FEED AND NOT OUR WIRING
# ═════════════════════════════════════════════════════════════════════

def test_the_seam_supplies_no_mechanism_and_says_why_verifiably():
    """M1 is UNAVAILABLE BY MEASUREMENT, which is not the same as unwired.

    The distinction decides who owns the blocker. "We have not subscribed
    yet" is engineering. "The feed carries no sequence number and does not
    distinguish a snapshot from an increment" is the venue, and
    subscribing cannot supply either.
    """
    from sportsassets.workers import ext_pinnacle_loop as L

    ev = L.book_currency_evidence(SLUG)
    assert ev["subscription"] is None
    assert ev["revalidation"] is None
    why = ev["why_none"] or ""
    assert "sequence number" in why
    assert "snapshot from an increment" in why
    assert "properties of the FEED" in why
    # And it names what would change the verdict, so it is a blocker with
    # an exit rather than a permanent excuse.
    assert ev["what_would_change_it"]
    assert ("ETag" in ev["what_would_change_it"]
            or "Last-Modified" in ev["what_would_change_it"])


def test_funded_entry_is_still_disabled():
    """The brief's condition: entry stays off until management is qualified.

    Management is NOT qualified -- an exit cannot be evidenced in this
    production state -- so this must remain false.
    """
    assert FM.FUNDED_EXIT_SUBMISSION_ENABLED is False
    dis = FM.disablements()
    assert dis, "the disablements must be enumerable, not implicit"
