"""THE RISK CONSUMER COUNTS RESERVATIONS, AND COUNTS THEM ONCE.

WHAT WAS WRONG. `bettor_funded_reservations.reserved_collateral_usd` was a helper
with no caller. So a HELD reservation -- which claims a leg and its collateral in
the window BEFORE any intent row exists, which is precisely the window a second
decision does damage in -- was invisible to every rail. An independent review put
it plainly: it is not an enforced account-wide limit until the risk consumer calls
it.

`bettor_funded_execution.check_rails` now calls it, and this file proves the two
halves that matter:

  1 A HELD RESERVATION RAISES THE MEASURED EXPOSURE. Before, it raised nothing.
  2 COMMITTING IT TO AN INTENT DOES NOT DOUBLE THE NUMBER. From COMMITTED onward
    the acquisition has an intent row, and that intent is already inside
    `pending_and_in_flight_collateral_usd`. Counting both would inflate committed
    capital by the full size of every in-flight acquisition at the exact moment
    the system is deciding whether it can afford another.

AND A DEFECT THIS FILE FOUND while being written: `reserved_collateral_usd` had no
`ok` field, so `check_rails` read every successful call as UNREADABLE and put four
capital rails into `unmeasured`. Fail-closed was working; the signal was wrong. A
consumer cannot tell an outage from a helper with no contract, so the function now
has one.
"""

from __future__ import annotations

import os

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_reservations as RS

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

ACCT = "acct-res-once"
VENUE = "PMUS"
EVENT = "ev-res-once"
SLUG_A = "aec-res-a"
SLUG_B = "aec-res-b"
PFX = "tres1-"

LIMITS = {"MAX_MARKET_EXPOSURE": 500.0, "MAX_EVENT_EXPOSURE": 500.0,
          "MAX_CORRELATED_EXPOSURE": 500.0, "MAX_CAPITAL_DEPLOYED": 500.0,
          "MAX_RESIDUAL_INVENTORY": 5000.0, "MAX_DRAWDOWN": 500.0,
          "MAX_CAPITAL_HOURS": 500000.0}


async def _conn():
    import asyncpg
    return await asyncpg.connect(DSN)


async def _has(c, t) -> bool:
    return bool(await c.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        " WHERE table_schema='public' AND table_name=$1", t))


async def _clean(c):
    if await _has(c, "bettor_funded_operation_evidence"):
        await c.execute("DELETE FROM bettor_funded_operation_evidence "
                        "WHERE operation_id LIKE $1", PFX + "%")
    if await _has(c, "bettor_funded_leg_reservations"):
        await c.execute("DELETE FROM bettor_funded_leg_reservations "
                        "WHERE group_id LIKE $1", PFX + "%")
    await c.execute("DELETE FROM bettor_funded_economics "
                    "WHERE intent_id LIKE $1", PFX + "%")
    await c.execute("DELETE FROM bettor_funded_fills WHERE intent_id LIKE $1",
                    PFX + "%")
    await c.execute("DELETE FROM bettor_funded_intents WHERE intent_id LIKE $1",
                    PFX + "%")
    if await _has(c, "bettor_funded_portfolio_groups"):
        await c.execute("DELETE FROM bettor_funded_portfolio_groups "
                        "WHERE group_id LIKE $1", PFX + "%")


async def _ready(c):
    if not await _has(c, "bettor_funded_leg_reservations"):
        pytest.skip("migration 131 not applied to this database")
    await _clean(c)
    holder = await c.fetchval(
        "SELECT group_id FROM bettor_funded_portfolio_groups "
        " WHERE closed_at IS NULL AND group_id NOT LIKE $1 LIMIT 1", PFX + "%")
    if holder:
        msg = "group %r holds the one open-group slot" % holder
        if os.environ.get("RN1X_CAPACITY_SLOT_MUST_BE_FREE", "") not in (
                "", "0", "false", "FALSE"):
            pytest.fail(msg + "; RN1X_CAPACITY_SLOT_MUST_BE_FREE is set, so "
                              "this is an ENVIRONMENT FAILURE")
        pytest.skip(msg)


def _plan(*, collateral=0.0, qty=0):
    """A zero-sized plan, so the measured numbers are the BOOK's and the
    reservation's and not this order's. Isolating the term under test."""
    return {"collateral_usd": float(collateral), "quantity": int(qty),
            "event_key": EVENT, "us_market_slug": SLUG_A}


async def _rails(c):
    eff = EX.effective_limits(dict(LIMITS))
    return await FX.check_rails(c, _plan(), eff["effective"],
                                account_id=ACCT, venue=VENUE)


async def _group(c, gid):
    await c.execute(
        "INSERT INTO bettor_funded_portfolio_groups "
        "(group_id, account_id, venue, event_key, structure) "
        "VALUES ($1,$2,$3,$4,'INDIRECT_MIDDLE')", gid, ACCT, VENUE, EVENT)


async def _intent(c, iid, gid, role, slug, *, collateral=5.0):
    await c.execute(
        "INSERT INTO bettor_funded_intents (intent_id, account_id, venue,"
        " venue_class, us_market_slug, event_key, order_intent, limit_price,"
        " quantity, collateral_usd, effective_digest, state, kind,"
        " residual_qty, portfolio_group_id, leg_role) VALUES "
        "($1,$2,$3,$4,$5,$6,'ORDER_INTENT_BUY_LONG',0.5,10,$7,$8,"
        " 'INTENT_RECORDED','ENTRY',0,$9,$10)",
        iid, ACCT, VENUE, FA.VENUE_FUNDED, slug, EVENT, float(collateral),
        iid + "-d", gid, role)


# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_held_reservation_raises_the_measured_exposure():
    """BEFORE THIS, IT RAISED NOTHING. A reservation in HELD has no intent, so
    `exposure()` cannot see it -- and that is the window it exists to cover."""
    c = await _conn()
    try:
        await _ready(c)
        before = await _rails(c)
        assert before["unmeasured"] == [], before["unmeasured"]
        base = {r["rail"]: r["measured"] for r in before["rails"]}
        assert before["reservation_reading"]["held_reserved_usd"] == \
            pytest.approx(0.0)

        await _group(c, PFX + "g1")
        got = await RS.hold(c, operation_id=PFX + "op1", group_id=PFX + "g1",
                            leg_role="PRIMARY", us_market_slug=SLUG_A,
                            quantity=10, limit_price=0.5, collateral_usd=5.0)
        assert got["ok"] is True, got

        after = await _rails(c)
        assert after["unmeasured"] == [], after["unmeasured"]
        now = {r["rail"]: r["measured"] for r in after["rails"]}
        for rail in ("MAX_CORRELATED_EXPOSURE", "MAX_CAPITAL_DEPLOYED",
                     "MAX_MARKET_EXPOSURE", "MAX_EVENT_EXPOSURE"):
            assert now[rail] == pytest.approx(base[rail] + 5.0), (
                "%s did not move by the reserved collateral" % rail)
        rr = after["reservation_reading"]
        assert rr["held_reserved_usd"] == pytest.approx(5.0)
        assert rr["by_market_usd"][SLUG_A] == pytest.approx(5.0)
        assert rr["by_event_usd"][EVENT] == pytest.approx(5.0)
        assert rr["counted_states"] == ["HELD"]
        assert after["counted_held_reservations"] is True
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_reservation_on_another_market_does_not_raise_this_markets_rail():
    """PER-MARKET MEANS PER MARKET. An account-wide total added to a per-market
    rail would make one instrument's reservation block another's order."""
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g2")
        await RS.hold(c, operation_id=PFX + "op2", group_id=PFX + "g2",
                      leg_role="HEDGE", us_market_slug=SLUG_B, quantity=10,
                      limit_price=0.5, collateral_usd=7.0)
        r = await _rails(c)
        by = {x["rail"]: x["measured"] for x in r["rails"]}
        # The plan is on SLUG_A; the reservation is on SLUG_B.
        assert by["MAX_MARKET_EXPOSURE"] == pytest.approx(0.0)
        # But it IS the same event and the same account.
        assert by["MAX_EVENT_EXPOSURE"] == pytest.approx(7.0)
        assert by["MAX_CORRELATED_EXPOSURE"] == pytest.approx(7.0)
        assert r["reservation_reading"]["by_market_usd"] == {SLUG_B: 7.0}
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_committing_the_reservation_does_not_double_the_exposure():
    """THE CENTRAL CLAIM, MEASURED ACROSS THE BOUNDARY.

    HELD: the reservation is the only record, so it is counted.
    COMMITTED: an intent row exists and `pending_and_in_flight_collateral_usd`
    counts it, so the reservation stops. The measured total must be the SAME
    number, not twice it.
    """
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g3")
        await RS.hold(c, operation_id=PFX + "op3", group_id=PFX + "g3",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        held = await _rails(c)
        held_total = {r["rail"]: r["measured"] for r in held["rails"]}[
            "MAX_CORRELATED_EXPOSURE"]
        assert held_total == pytest.approx(5.0)
        assert held["reservation_reading"]["held_reserved_usd"] == \
            pytest.approx(5.0)

        # NOW THE INTENT EXISTS AND THE RESERVATION IS BOUND TO IT.
        await _intent(c, PFX + "i3", PFX + "g3", "PRIMARY", SLUG_A,
                      collateral=5.0)
        bound = await RS.commit_to_intent(c, operation_id=PFX + "op3",
                                          intent_id=PFX + "i3")
        assert bound["ok"] is True, bound
        assert bound["counts_as_committed_capital"] is False

        after = await _rails(c)
        totals = {r["rail"]: r["measured"] for r in after["rails"]}
        assert totals["MAX_CORRELATED_EXPOSURE"] == pytest.approx(5.0), (
            "the same $5 is now counted twice: %s" % totals)
        assert totals["MAX_CAPITAL_DEPLOYED"] == pytest.approx(5.0)
        rr = after["reservation_reading"]
        assert rr["held_reserved_usd"] == pytest.approx(0.0)
        # AND THE READING NAMES THE INTENT THAT CARRIES IT NOW, so the claim
        # "counted once" can be checked rather than taken.
        not_counted = {x["operation_id"]: x for x in rr["live_but_not_counted"]}
        assert not_counted[PFX + "op3"]["state"] == RS.COMMITTED
        assert not_counted[PFX + "op3"][
            "counted_on_the_intent_instead"] == PFX + "i3"
        assert "double every in-flight acquisition" in rr["why_only_held"]
        # THE INTENT IS WHAT THE BOOK NOW SEES.
        assert PFX + "i3" in [p["intent_id"]
                              for p in await FB.open_entry_positions(c)]
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_the_whole_state_machine_never_double_counts():
    """EVERY TRANSITION, not only the first boundary. The total attributable to
    this acquisition must be $5 at every step and $0 once it is terminal."""
    c = await _conn()
    try:
        await _ready(c)
        if not await _has(c, "bettor_funded_operation_evidence"):
            pytest.skip("migration 133 not applied to this database")
        await _group(c, PFX + "g4")
        op = PFX + "op4"
        await RS.hold(c, operation_id=op, group_id=PFX + "g4",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        seen = {}

        async def _snap(label):
            r = await _rails(c)
            assert r["unmeasured"] == [], (label, r["unmeasured"])
            seen[label] = {
                "correlated": {x["rail"]: x["measured"]
                               for x in r["rails"]}["MAX_CORRELATED_EXPOSURE"],
                "held_reserved": r["reservation_reading"]["held_reserved_usd"],
            }

        await _snap("HELD")
        await _intent(c, PFX + "i4", PFX + "g4", "PRIMARY", SLUG_A,
                      collateral=5.0)
        await RS.commit_to_intent(c, operation_id=op, intent_id=PFX + "i4")
        await _snap("COMMITTED")
        await RS.mark_send_attempted(c, operation_id=op)
        await _snap("SEND_ATTEMPTED")
        await RS.mark_ambiguous(c, operation_id=op, why="no answer arrived")
        await _snap("AMBIGUOUS")
        await RS.record_venue_evidence(
            c, evidence_id=PFX + "ev4", operation_id=op, account_id=ACCT,
            venue=VENUE, us_market_slug=SLUG_A, intent_id=PFX + "i4",
            kind=RS.EV_NAMED, venue_order_id="vo-4",
            search_endpoint="GET /v1/orders/vo-4", read_at=1790000200.0,
            covered_terminal_orders=True, results_returned=1)
        await RS.record_the_venue_named_it(c, operation_id=op,
                                          venue_order_id="vo-4")
        await _snap("CONSUMED")

        for label in ("HELD", "COMMITTED", "SEND_ATTEMPTED", "AMBIGUOUS",
                      "CONSUMED"):
            assert seen[label]["correlated"] == pytest.approx(5.0), (
                "exposure changed at %s: %r" % (label, seen))
        # ONLY HELD EVER COUNTS THE RESERVATION ITSELF.
        assert seen["HELD"]["held_reserved"] == pytest.approx(5.0)
        for label in ("COMMITTED", "SEND_ATTEMPTED", "AMBIGUOUS", "CONSUMED"):
            assert seen[label]["held_reserved"] == pytest.approx(0.0), label
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_an_unreadable_reservation_table_blocks_rather_than_counting_zero():
    """FAIL-CLOSED, AND FOR THE RIGHT REASON. A risk check that treats an
    unreadable claim as zero is how capital gets committed twice. ABSENT is not
    UNREADABLE -- a database without migration 131 has no reservations to count,
    which is a complete answer."""
    c = await _conn()
    try:
        await _ready(c)

        class _Broken:
            """Everything works except the reservation read."""

            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                return getattr(self._real, name)

        async def _boom(*_a, **_k):
            raise RuntimeError("the reservation table could not be read")

        import sportsassets.bettor_funded_reservations as RSV
        real = RSV.reserved_collateral_usd
        try:
            RSV.reserved_collateral_usd = _boom
            r = await _rails(c)
        finally:
            RSV.reserved_collateral_usd = real
        assert set(r["unmeasured"]) >= {"MAX_CORRELATED_EXPOSURE",
                                        "MAX_CAPITAL_DEPLOYED",
                                        "MAX_MARKET_EXPOSURE",
                                        "MAX_EVENT_EXPOSURE"}, r["unmeasured"]
        assert r["every_effective_rail_was_checked"] is False
        assert r["reservation_reading"]["schema"] == "UNREADABLE"
        # AND THE RAILS THAT BLOCKED SAY WHY rather than reporting a number.
        by = {x["rail"]: x for x in r["rails"]}
        assert by["MAX_CORRELATED_EXPOSURE"]["measured"] is None
        assert by["MAX_CORRELATED_EXPOSURE"]["verdict"] == "NOT_MEASURED"
    finally:
        await _clean(c)
        await c.close()
