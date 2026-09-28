"""The audit's loss -> recovery -> closure counterexample, on PostgreSQL.

Independent audit (28 Sep 2026), finding A06. `reproduce_drawdown.py` drove
`bettor_funded_book.realised()` against its database-row seam and got:

    position state                 realised   reported max_dd   REQUIRED
    first partial exit loses $20     -$20          $20            $20
    later partial exit recovers $20   $0         ** $0 **      ** $20 **
    position closes flat              $0         ** $0 **      ** $20 **

The reader replaced history with a net result at the most recent exit or
closure. The audit confirmed the per-fill increment redesign in 18fb67b
fixes it and instructed: keep the redesign, and pin the case against a
MIGRATED database rather than re-deriving it.

── WHY THIS IS THE SHARPEST VERSION OF THE TEST ─────────────────────
`test_a_partial_loss_actually_stops_new_exposure` proves ONE partial exit
is booked. That is necessary and much weaker than this: a reader that
simply reports the latest net would PASS the one-exit case, because with a
single exit the latest net IS the history. Only recovery makes them differ.

The audit says so directly: "Do not declare the loss-stop work complete
because the simple one-partial-exit test passes."

The required property is that a drawdown reached at T1 survives:
  * a later exit that recovers the money, and
  * the position closing flat afterwards.

`max_drawdown_usd` is a maximum OVER THE PAST. If recovery erased it, the
loss stop would un-trip itself by recovering -- and, worse, a position
could be closed to clear a breach.
"""

import pytest

from sportsassets import bettor_funded_book as FB

from tests.test_the_funded_lifecycle_is_complete import (  # noqa: E402
    ACCT, DSN, VENUE, _clean, pg,
)
from tests.test_a_partial_loss_actually_stops_new_exposure import (  # noqa: E402
    TIGHT_STOP, _seed_tight,
)
from tests.test_partial_exit_pnl_is_separable import _entry, _sell  # noqa: E402


#: The audit's shape: buy 40 @ 0.50, sell 20 at a loss, sell 20 at a gain
#: that exactly recovers it, then close. Chosen so the cumulative curve
#: returns to zero -- which is what makes the historical trough the only
#: thing distinguishing a correct reader from a "latest net" reader.
#:
#: Fee-free (`fee=0.0`), because the point here is the CHRONOLOGY and a fee
#: curve would obscure whether recovery is exact.
QTY = 40
ENTRY_PX = 0.50
LOSS_PX = 0.25      # sell 20 at 0.25 on a 0.50 basis -> -$5.00
GAIN_PX = 0.75      # sell 20 at 0.75 on a 0.50 basis -> +$5.00


@pg
@pytest.mark.asyncio
async def test_the_drawdown_survives_recovery_and_closure(monkeypatch):
    """Three phases. The trough must be present in all three."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        await _seed_tight(conn, limits=TIGHT_STOP)
        await _entry(conn, intent_id="audit-dd", qty=QTY, price=ENTRY_PX,
                     fee=0.0)

        # ── PHASE 1 · THE LOSS ──────────────────────────────────────
        await _sell(conn, monkeypatch, parent="audit-dd", qty=20,
                    price=LOSS_PX, fee=0.0)
        p1 = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        assert p1["realised_pnl_usd"] == pytest.approx(-5.0, abs=1e-6), p1
        assert p1["max_drawdown_usd"] == pytest.approx(5.0, abs=1e-6), p1
        trough = p1["max_drawdown_usd"]

        # ── PHASE 2 · THE RECOVERY. THIS IS THE AUDIT'S FAILING CASE ─
        #
        # Cumulative P&L returns to 0. A reader reporting the latest net
        # would say max_drawdown 0.00 here -- which is what the audited
        # code did, and it is the whole defect: the $5 dip HAPPENED.
        await _sell(conn, monkeypatch, parent="audit-dd", qty=20,
                    price=GAIN_PX, fee=0.0)
        p2 = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        assert p2["realised_pnl_usd"] == pytest.approx(0.0, abs=1e-6), p2
        assert p2["max_drawdown_usd"] == pytest.approx(trough, abs=1e-6), (
            "RECOVERY ERASED THE DRAWDOWN. The $5 trough at the first exit "
            "is a fact about the past and cannot be undone by a later "
            "gain. This is audit finding A06.", p2)
        # AND THE CURVE ITSELF SHOWS BOTH EXIT POINTS, in order.
        #
        # SELECTED BY COMPONENT, not by count. Selling all 40 of 40
        # contracts CLOSES the position, so a third increment appears --
        # the POSITION_TERMINAL remainder, at 0.0, because both exit fills
        # already booked the whole result. My first version asserted
        # `len == 2` and the extra zero-net row failed it, which is the
        # anti-double-count working rather than a defect.
        exits = [c for c in p2["increments"] if c["component"] == "EXIT_FILL"]
        assert len(exits) == 2, p2["increments"]
        assert exits[0]["at"] <= exits[1]["at"]
        assert exits[0]["net"] < 0 < exits[1]["net"]
        assert exits[0]["net"] == pytest.approx(-5.0, abs=1e-6)
        assert exits[1]["net"] == pytest.approx(+5.0, abs=1e-6)
        # THE TERMINAL REMAINDER IS ZERO: nothing is counted twice.
        term2 = [c for c in p2["increments"]
                 if c["component"] == "POSITION_TERMINAL"]
        assert all(t["net"] == pytest.approx(0.0, abs=1e-4) for t in term2), (
            term2)

        # ── PHASE 3 · CLOSURE. Flat, and the trough still stands ─────
        #
        # Selling the full 40 already closed the position, so this UPDATE is
        # idempotent rather than the thing that closes it. It is kept so the
        # phase is explicit and so the assertion below is about a CLOSED
        # position regardless of whether the lifecycle closed it for us.
        await conn.execute(
            "UPDATE bettor_funded_intents SET closed_at=now(), "
            "closed_reason='EXITED_IN_THE_MARKET', residual_qty=0 "
            " WHERE intent_id=$1", "audit-dd")
        p3 = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        assert p3["realised_pnl_usd"] == pytest.approx(0.0, abs=1e-4), p3
        assert p3["max_drawdown_usd"] == pytest.approx(trough, abs=1e-6), (
            "CLOSURE ERASED THE DRAWDOWN.", p3)
        # THE TERMINAL INCREMENT ADDS NOTHING: everything was already
        # booked by the two exit fills, so a non-zero value here would be
        # the double count.
        term = [c for c in p3["increments"]
                if c["component"] == "POSITION_TERMINAL"]
        assert len(term) == 1, p3["increments"]
        assert term[0]["net"] == pytest.approx(0.0, abs=1e-4), term
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_deployed_consumer_reads_the_surviving_trough(monkeypatch):
    """The audit requires proving the CONSUMER reads it, not just the reader.

    A correct `realised()` whose value never reaches `check_rails` is a
    corrected number nobody acts on. This drives the actual rail after the
    recovery, where the naive reader would report 0.00 and admit the trade.
    """
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_funded_execution as FX

    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        eff = await _seed_tight(conn, limits=TIGHT_STOP)   # $1 stop
        await _entry(conn, intent_id="audit-dd2", qty=QTY, price=ENTRY_PX,
                     fee=0.0)
        await _sell(conn, monkeypatch, parent="audit-dd2", qty=20,
                    price=LOSS_PX, fee=0.0)
        await _sell(conn, monkeypatch, parent="audit-dd2", qty=20,
                    price=GAIN_PX, fee=0.0)

        # NET P&L IS ZERO. A reader that reports the latest net would give
        # the rail 0.00 against a $1 stop and this order would CLEAR.
        pl = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        assert pl["realised_pnl_usd"] == pytest.approx(0.0, abs=1e-6)

        plan = {"collateral_usd": 1.0, "quantity": 2.0,
                "event_key": "ev-audit-unrelated",
                "us_market_slug": "aec-nba-mia-orl-2026-09-29",
                "limit_price": 0.50}
        got = await FX.check_rails(conn, plan, eff,
                                  account_id=ACCT, venue=VENUE)
        dd = [r for r in got["rails"] if r["rail"] == "MAX_DRAWDOWN"][0]

        # THE RAIL MUST SEE THE $5 TROUGH, NOT THE $0 NET.
        assert dd["verdict"] == "EXCEEDED", (
            "the consumer read the net, not the history", dd)
        assert dd["measured"] == pytest.approx(5.0, abs=1e-6), dd
        assert "MAX_DRAWDOWN" in {r["rail"] for r in got["over"]}
    finally:
        await _clean(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# A07 · AN INCOMPLETE LEDGER IS NOT A SMALLER DRAWDOWN
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_an_unbookable_result_makes_the_rail_unmeasured(monkeypatch):
    """Audit A07, and it was a defect in code I wrote this session.

    `realised()` reports `not_bookable` -- results it could not place on
    the curve. I added that list so an omission would be VISIBLE, then
    read `max_drawdown_usd` beside it without ever consulting it. The
    subtotal of a partial ledger was handed to the loss stop as though it
    were the whole history.

    THE DIRECTION IS THE DANGEROUS ONE: every omitted result HAPPENED, so
    excluding it can only understate the drawdown. The rail would pass on
    a book whose true peak-to-trough is larger than anything it can see.

    The containment reuses the existing fail-closed path: no measurement
    means the rail BLOCKS. `NOT_MEASURED`, never a smaller number.
    """
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_funded_execution as FX

    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        eff = await _seed_tight(conn, limits=TIGHT_STOP)
        await _entry(conn, intent_id="audit-a07", qty=QTY, price=ENTRY_PX,
                     fee=0.0)
        await _sell(conn, monkeypatch, parent="audit-a07", qty=20,
                    price=LOSS_PX, fee=0.0)

        # A COMPLETE LEDGER MEASURES AND EXCEEDS -- the positive control,
        # so "blocks" cannot be satisfied by a rail that always blocks.
        before = await FX.check_rails(
            conn, {"collateral_usd": 1.0, "quantity": 2.0,
                   "event_key": "ev-a07", "us_market_slug": "aec-a07",
                   "limit_price": 0.50},
            eff, account_id=ACCT, venue=VENUE)
        dd0 = [r for r in before["rails"] if r["rail"] == "MAX_DRAWDOWN"][0]
        assert dd0["verdict"] == "EXCEEDED", dd0
        assert dd0["measured"] == pytest.approx(5.0, abs=1e-6), dd0
        assert "MAX_DRAWDOWN" not in before["unmeasured"], before

        # NOW BREAK THE BASIS. Remove the ENTRY fill, so the exit fill has
        # no entry at or before it and therefore no point-in-time
        # per-contract basis. That is exactly the `not_bookable` condition.
        await conn.execute(
            "DELETE FROM bettor_funded_fills WHERE direction='ENTRY'")

        real = await FB.realised(conn, account_id=ACCT, venue=VENUE)
        assert real["not_bookable"], real
        assert any("no entry fill" in (r.get("why") or "")
                   for r in real["not_bookable"]), real["not_bookable"]

        after = await FX.check_rails(
            conn, {"collateral_usd": 1.0, "quantity": 2.0,
                   "event_key": "ev-a07", "us_market_slug": "aec-a07",
                   "limit_price": 0.50},
            eff, account_id=ACCT, venue=VENUE)
        dd1 = [r for r in after["rails"] if r["rail"] == "MAX_DRAWDOWN"][0]
        assert dd1["verdict"] == "NOT_MEASURED", dd1
        assert dd1["measured"] is None, dd1
        assert "MAX_DRAWDOWN" in after["unmeasured"], after
        assert after["every_effective_rail_was_checked"] is False, after
        # AND IT DID NOT QUIETLY BECOME A SMALLER NUMBER.
        assert dd1.get("measured") != 0.0, dd1
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_operator_reader_agrees_that_it_is_unmeasured(monkeypatch):
    """`loss_controls` must not report a number the rail refuses to trust.

    Two readers disagreeing about whether a measurement EXISTS is worse
    than either answer alone: the operator surface would show a drawdown
    while the gate blocked on not having one, and nobody could tell which
    to believe.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        eff = await _seed_tight(conn, limits=TIGHT_STOP)
        await _entry(conn, intent_id="audit-a07b", qty=QTY, price=ENTRY_PX,
                     fee=0.0)
        await _sell(conn, monkeypatch, parent="audit-a07b", qty=20,
                    price=LOSS_PX, fee=0.0)
        await conn.execute(
            "DELETE FROM bettor_funded_fills WHERE direction='ENTRY'")

        lc = await FB.loss_controls(conn, account_id=ACCT, venue=VENUE,
                                    approved_limits=eff)
        assert lc["realised_drawdown_usd"] == FB.NOT_IDENTIFIED, lc
        assert lc["realised_drawdown_is_unmeasured_because"], lc
        assert "PARTIAL history" in lc[
            "realised_drawdown_is_unmeasured_because"]
        assert lc["realised_not_bookable"], lc
        # AN UNMEASURED DRAWDOWN CANNOT TRIP -- AND CANNOT CLEAR.
        ctl = [c for c in lc["controls"] if c["control"] == "MAX_DRAWDOWN"][0]
        assert ctl["tripped"] is None, ctl
        assert ctl["tripped"] is not False, (
            "False would read as 'checked and within limit' on a book we "
            "could not fully read", ctl)
    finally:
        await _clean(conn)
        await conn.close()
