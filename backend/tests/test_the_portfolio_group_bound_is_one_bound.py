"""MIGRATION 131'S INVARIANTS, AS A REPEATABLE GATE.

Ad hoc SQL at a psql prompt is useful while developing and is not a release
gate: it is not re-run, it does not fail a build, and its sequencing is
whatever I typed. An independent review said so, and it was right -- the first
version of 131 shipped with nothing but such observations, and two of the
"failures" I then saw were my own invalid sequences rather than the schema.

WHAT THIS FILE PINS, all against a real PostgreSQL:

  1 ONE capacity bound shared by grouped and legacy positions.
  2 Closure is EARNED: inventory, outstanding orders and unresolved
    reservations each block it, and abandoning a hedge does not release
    capacity.
  3 Membership is bound: account, venue, fixture, role, distinct instruments,
    and a reservation's intent must be THIS leg's.
  4 Submission idempotency across CONSUMED and RELEASED, with a legitimate
    top-up still possible.
  5 Paired quantity is CURRENT inventory, not historical volume.

WHY EACH ASSERTION NAMES THE CONSTRAINT IT EXPECTS. A test that only checks
"this raised" passes when the wrong rule fires -- and during development the
wrong rule DID fire more than once (a leg refused for `one_open_leg_per_role`
when the case under test was a closed group). The expected constraint or
message is asserted, so a repair that moves the enforcement is visible.
"""

from __future__ import annotations

import os
import uuid

import pytest

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

FIXTURE = "nfl-chi-car-2026-09-13"
ACCT = "acct-131"


async def _conn():
    import asyncpg
    return await asyncpg.connect(DSN)


async def _has_131(c) -> bool:
    return bool(await c.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name = 'bettor_funded_portfolio_groups'"))


async def _require_free_capacity(c):
    """Skip -- do not delete -- when something else holds the one open group.

    THE BOUND MAKES THESE TESTS EXCLUSIVE. There is exactly one open-group slot
    in the system, so a test cannot open its own while anything else holds it.
    That is the invariant working, not a defect, and it surfaced immediately:
    a leftover group from development failed all nineteen tests at once.

    The response is to SKIP with the holder named, never to delete it. A test
    that clears somebody else's open position to make room for itself is
    destroying the very state a funded system exists to protect -- and on a
    shared database that row could be real.
    """
    holder = await c.fetchval(
        "SELECT group_id FROM bettor_funded_portfolio_groups "
        "WHERE closed_at IS NULL AND group_id NOT LIKE 't131-%' LIMIT 1")
    if holder:
        pytest.skip("group %r holds the one open-group slot; these tests need "
                    "it free and will not delete another owner's row" % holder)


async def _clean(c):
    """Remove only what these tests create, by their own id prefix.

    NOT a TRUNCATE. This may run against a database holding other fixtures,
    and wiping the funded tables to make room for a test is how a test starts
    destroying evidence.
    """
    await c.execute("DELETE FROM bettor_funded_leg_reservations "
                    "WHERE group_id LIKE 't131-%'")
    await c.execute("DELETE FROM bettor_funded_fills WHERE intent_id IN "
                    "(SELECT intent_id FROM bettor_funded_intents "
                    " WHERE intent_id LIKE 't131-%')")
    await c.execute("DELETE FROM bettor_funded_intents "
                    "WHERE intent_id LIKE 't131-%'")
    await c.execute("DELETE FROM bettor_funded_portfolio_groups "
                    "WHERE group_id LIKE 't131-%'")


async def _group(c, gid, *, structure="INDIRECT_MIDDLE", event=FIXTURE):
    await c.execute(
        "INSERT INTO bettor_funded_portfolio_groups "
        "(group_id, account_id, venue, event_key, structure) "
        "VALUES ($1,$2,'PMUS',$3,$4)", gid, ACCT, event, structure)


async def _leg(c, iid, slug, gid, role, qty, *, acct=ACCT, event=FIXTURE,
               state="INTENT_RECORDED", kind="ENTRY", parent=None):
    # EVERY PARAMETER ITS OWN PLACEHOLDER. Reusing `$1` for two columns of
    # different inferred types makes asyncpg raise AmbiguousParameterError --
    # which it did, on sixteen tests at once.
    await c.execute(
        "INSERT INTO bettor_funded_intents "
        "(intent_id, account_id, venue, venue_class, us_market_slug, "
        " event_key, order_intent, limit_price, quantity, collateral_usd, "
        " effective_digest, state, kind, residual_qty, portfolio_group_id, "
        " leg_role, parent_intent_id) "
        "VALUES ($1,$2,'PMUS','US',$3,$4,'ORDER_INTENT_BUY_LONG',0.5,$5,"
        "        $6,$7,$8,$9,$10,$11,$12,$13)",
        iid, acct, slug, event, int(qty), float(qty) * 0.5, iid, state, kind,
        float(qty), gid, role, parent)


async def _fill(c, fid, iid, qty, direction):
    await c.execute(
        "INSERT INTO bettor_funded_fills "
        "(fill_id, intent_id, venue_order_id, venue_fill_id, qty, price, "
        " cash_usd, fee_usd, fee_basis, direction, at) "
        "VALUES ($1,$2,$3,$4,$5,0.5,$6,0,'TEST',$7, now())",
        fid, iid, "vo-" + iid, fid, float(qty), float(qty) * 0.5, direction)


async def _reserve(c, rid, gid, role, slug, qty, state, op, intent=None,
                   resolution=None):
    resolved = "now()" if state in ("CONSUMED", "RELEASED") else "NULL"
    await c.execute(
        "INSERT INTO bettor_funded_leg_reservations "
        "(reservation_id, group_id, leg_role, us_market_slug, quantity, "
        " collateral_usd, limit_price, state, operation_id, intent_id, "
        " resolved_at, resolution) "
        "VALUES ($1,$2,$3,$4,$5,$6,0.5,$7,$8,$9," + resolved + ",$10)",
        rid, gid, role, slug, float(qty), float(qty) * 0.5, state, op,
        intent, resolution)


def _why(exc) -> str:
    return "%s: %s" % (type(exc).__name__, exc)


# ═════════════════════════════════════════════════════════════════════
# 1 · ONE SHARED CAPACITY BOUND
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_second_open_group_is_refused():
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        with pytest.raises(Exception) as caught:
            await _group(c, "t131-b", event="other")
        assert "bettor_funded_one_open_group" in _why(caught.value), \
            _why(caught.value)
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_an_open_entry_cannot_exist_outside_a_group():
    """THE HOLE THE FIRST VERSION HAD, closed.

    It kept a separate bound for ungrouped entries, so one group (two legs)
    plus one ungrouped entry was three open entries across two economic
    positions -- while the file claimed the one-position limit was preserved.
    There is now no ungrouped path at all for an OPEN entry, which is what
    makes the group bound the only one.
    """
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        await _leg(c, "t131-p", "aec-chi-ml", "t131-a", "PRIMARY", 10)
        await _leg(c, "t131-h", "aec-car-p45", "t131-a", "HEDGE", 10)
        with pytest.raises(Exception) as caught:
            await _leg(c, "t131-solo", "aec-solo", None, None, 3,
                       event="ev-other")
        assert "open_entry_has_a_group" in _why(caught.value), _why(caught.value)
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_grouped_and_legacy_cannot_independently_claim_capacity():
    """CONCURRENT transactions, one capacity.

    Two sessions each try to take capacity -- one by opening a group, one by
    inserting an entry outside any group. Exactly one may succeed, and the
    ungrouped attempt must be refused on its own merits rather than racing.
    """
    import asyncpg

    a = await _conn()
    b = await asyncpg.connect(DSN)
    try:
        if not await _has_131(a):
            pytest.skip("migration 131 not applied to this database")
        await _clean(a)
        await _require_free_capacity(a)
        ta = a.transaction()
        await ta.start()
        await _group(a, "t131-a")
        # The other session cannot create an ungrouped open entry at all, so
        # there is no second route to capacity to race for.
        with pytest.raises(Exception) as caught:
            await _leg(b, "t131-solo", "aec-solo", None, None, 3,
                       event="ev-other")
        assert "open_entry_has_a_group" in _why(caught.value)
        await ta.commit()
        # And a second group from the other session is refused by the bound.
        with pytest.raises(Exception) as caught2:
            await _group(b, "t131-b", event="other")
        assert "one_open_group" in _why(caught2.value)
    finally:
        await _clean(a)
        await a.close()
        await b.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · CLOSURE IS EARNED
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_inventory_blocks_closure():
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        await _leg(c, "t131-p", "aec-chi-ml", "t131-a", "PRIMARY", 10,
                   state="FILLED")
        await _fill(c, "t131-f1", "t131-p", 10, "ENTRY")
        with pytest.raises(Exception) as caught:
            await c.execute(
                "UPDATE bettor_funded_portfolio_groups "
                "SET closed_at = now(), closure = 'ALL_LEGS_EXITED' "
                "WHERE group_id = 't131-a'")
        assert "still holds" in _why(caught.value), _why(caught.value)
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_no_closure_reason_permits_a_retained_holding():
    """The reason I invented that made this possible is GONE.

    The first version had HEDGE_ABANDONED_FIRST_LEG_RETAINED, which by name
    permitted closing while the first holding remained -- so "close group, open
    another" could accumulate live inventory without ever tripping the
    one-open-group index. The vocabulary no longer contains it, AND the
    exposure check would refuse it anyway.
    """
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        allowed = await c.fetchval(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'bettor_funded_group_closure_ck'")
        assert "HEDGE_ABANDONED" not in (allowed or ""), allowed
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        await _leg(c, "t131-p", "aec-chi-ml", "t131-a", "PRIMARY", 10,
                   state="FILLED")
        await _fill(c, "t131-f1", "t131-p", 10, "ENTRY")
        with pytest.raises(Exception) as caught:
            await c.execute(
                "UPDATE bettor_funded_portfolio_groups "
                "SET closed_at = now(), closure = 'BOTH_LEGS_SETTLED' "
                "WHERE group_id = 't131-a'")
        assert "still holds" in _why(caught.value)
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_abandoning_the_hedge_does_not_release_capacity():
    """The retained holding keeps the group open, so no new group may start."""
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        await _leg(c, "t131-p", "aec-chi-ml", "t131-a", "PRIMARY", 10,
                   state="FILLED")
        await _fill(c, "t131-f1", "t131-p", 10, "ENTRY")
        await c.execute("UPDATE bettor_funded_portfolio_groups "
                        "SET hedge_intent = 'ABANDONED' "
                        "WHERE group_id = 't131-a'")
        still_open = await c.fetchval(
            "SELECT closed_at IS NULL FROM bettor_funded_portfolio_groups "
            "WHERE group_id = 't131-a'")
        assert still_open is True
        with pytest.raises(Exception) as caught:
            await _group(c, "t131-b", event="other")
        assert "one_open_group" in _why(caught.value)
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_an_unresolved_reservation_blocks_closure():
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        # A leg in a TERMINAL state with no inventory, so neither inventory nor
        # an outstanding order is what blocks closure here -- the reservation is.
        # CANCELLED, not EXITED_IN_THE_MARKET: that is a CLOSURE value on the
        # funded intents table, not an order STATE, and the state check refused
        # it. The two vocabularies are separate and I conflated them.
        await _leg(c, "t131-p", "aec-chi-ml", "t131-a", "PRIMARY", 10,
                   state="CANCELLED")
        await c.execute("UPDATE bettor_funded_intents SET residual_qty = 0 "
                        "WHERE intent_id = 't131-p'")
        # HELD with no intent: a reservation taken before any order exists,
        # which is the normal first state and needs no intent to name.
        # (Pointing a HEDGE reservation at the PRIMARY leg's intent is refused
        # by the role binding -- as another test in this file asserts -- so it
        # cannot be used to set this case up.)
        await _reserve(c, "t131-r1", "t131-a", "HEDGE", "aec-car-p45", 10,
                       "HELD", "t131-op-1")
        with pytest.raises(Exception) as caught:
            await c.execute(
                "UPDATE bettor_funded_portfolio_groups "
                "SET closed_at = now(), closure = 'NEVER_HELD_ANY_INVENTORY' "
                "WHERE group_id = 't131-a'")
        assert "unresolved reservation" in _why(caught.value), _why(caught.value)
        # Resolve it and closure is then permitted.
        await c.execute(
            "UPDATE bettor_funded_leg_reservations SET state='RELEASED', "
            "resolved_at=now(), resolution='HEDGE_NOT_ACQUIRED' "
            "WHERE reservation_id='t131-r1'")
        await c.execute(
            "UPDATE bettor_funded_portfolio_groups "
            "SET closed_at = now(), closure = 'NEVER_HELD_ANY_INVENTORY' "
            "WHERE group_id = 't131-a'")
        assert await c.fetchval(
            "SELECT closed_at IS NOT NULL "
            "FROM bettor_funded_portfolio_groups WHERE group_id='t131-a'")
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_an_outstanding_order_blocks_closure():
    """An INTENT_RECORDED entry IS an outstanding order.

    This surfaced while developing: a group whose legs were merely recorded
    could not be closed, and that is correct rather than a defect -- an order
    the venue may still act on cannot be abandoned by closing its group.
    """
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        await _leg(c, "t131-p", "aec-chi-ml", "t131-a", "PRIMARY", 10,
                   state="INTENT_RECORDED")
        with pytest.raises(Exception) as caught:
            await c.execute(
                "UPDATE bettor_funded_portfolio_groups "
                "SET closed_at = now(), closure = 'NEVER_HELD_ANY_INVENTORY' "
                "WHERE group_id = 't131-a'")
        assert "outstanding or ambiguous order" in _why(caught.value)
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_closed_group_accepts_no_new_leg_or_reservation():
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        await c.execute(
            "UPDATE bettor_funded_portfolio_groups "
            "SET closed_at = now(), closure = 'NEVER_HELD_ANY_INVENTORY' "
            "WHERE group_id = 't131-a'")
        with pytest.raises(Exception) as caught:
            await _leg(c, "t131-late", "aec-new", "t131-a", "PRIMARY", 5)
        assert "is closed" in _why(caught.value), _why(caught.value)
        with pytest.raises(Exception) as caught2:
            await _reserve(c, "t131-r9", "t131-a", "HEDGE", "aec-car-p45", 1,
                           "HELD", "t131-op-9")
        assert "is closed" in _why(caught2.value), _why(caught2.value)
    finally:
        await _clean(c)
        await c.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · MEMBERSHIP AND IDENTITY
# ═════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.parametrize("field,value,expect", [
    ("account", "OTHER-ACCOUNT", "account"),
    ("event", "A-DIFFERENT-FIXTURE", "fixture"),
])
async def test_a_leg_must_match_its_group(field, value, expect):
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        kw = {"acct": value} if field == "account" else {"event": value}
        with pytest.raises(Exception) as caught:
            await _leg(c, "t131-bad", "aec-x", "t131-a", "PRIMARY", 1, **kw)
        assert expect in _why(caught.value), _why(caught.value)
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_the_two_legs_must_be_distinct_contracts():
    """Two roles on the same contract is netting, not an indirect pair.

    On this venue buying the opposite side of one market reduces the same book
    rather than creating a second independently settling holding, so a group
    whose legs name one contract is not the strategy at all.
    """
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        await _leg(c, "t131-p", "aec-chi-ml", "t131-a", "PRIMARY", 10)
        with pytest.raises(Exception) as caught:
            await _leg(c, "t131-h", "aec-chi-ml", "t131-a", "HEDGE", 10)
        assert "netting" in _why(caught.value), _why(caught.value)
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_reservation_cannot_name_another_legs_intent():
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        await _leg(c, "t131-p", "aec-chi-ml", "t131-a", "PRIMARY", 10)
        await _leg(c, "t131-h", "aec-car-p45", "t131-a", "HEDGE", 10)
        # A HEDGE reservation naming the PRIMARY leg's intent.
        with pytest.raises(Exception) as caught:
            await _reserve(c, "t131-r1", "t131-a", "HEDGE", "aec-car-p45", 10,
                           "CONSUMED", "t131-op-1", intent="t131-p",
                           resolution="ORDER_RECORDED")
        assert "belongs to group" in _why(caught.value), _why(caught.value)
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_reservation_cannot_name_a_nonexistent_intent():
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        with pytest.raises(Exception) as caught:
            await _reserve(c, "t131-r1", "t131-a", "HEDGE", "aec-car-p45", 10,
                           "CONSUMED", "t131-op-1", intent="t131-nope",
                           resolution="ORDER_RECORDED")
        msg = _why(caught.value)
        assert ("does not exist" in msg
                or "foreign key" in msg.lower()), msg
    finally:
        await _clean(c)
        await c.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · SUBMISSION IDEMPOTENCY
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_replaying_an_operation_is_refused_after_it_resolved():
    """WHAT HELD-UNIQUENESS COULD NOT DO.

    `UNIQUE(group, role) WHERE state='HELD'` stops two simultaneous
    reservations and nothing more: once the first became CONSUMED or RELEASED,
    the same acquisition could be replayed and submitted again. The operation
    identity is unique across the whole table, so a replay is refused whatever
    state the earlier attempt reached.
    """
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        await _leg(c, "t131-p", "aec-chi-ml", "t131-a", "PRIMARY", 10)
        await _leg(c, "t131-h", "aec-car-p45", "t131-a", "HEDGE", 10)
        await _reserve(c, "t131-r1", "t131-a", "HEDGE", "aec-car-p45", 10,
                       "CONSUMED", "t131-acq-A", intent="t131-h",
                       resolution="ORDER_RECORDED")
        with pytest.raises(Exception) as caught:
            await _reserve(c, "t131-r2", "t131-a", "HEDGE", "aec-car-p45", 10,
                           "HELD", "t131-acq-A")
        assert "operation_uniq" in _why(caught.value), _why(caught.value)
        # A LEGITIMATE TOP-UP carries a new identity and proceeds.
        await _reserve(c, "t131-r3", "t131-a", "HEDGE", "aec-car-p45", 5,
                       "HELD", "t131-acq-B")
        assert await c.fetchval(
            "SELECT count(*) FROM bettor_funded_leg_reservations "
            "WHERE group_id='t131-a'") == 2
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_an_unacknowledged_send_still_blocks_a_second_attempt():
    """AMBIGUOUS is live, not released.

    A send that was attempted and never acknowledged must keep consuming the
    leg: treating it as free is how a lost acknowledgement becomes two orders.
    """
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        await _leg(c, "t131-h", "aec-car-p45", "t131-a", "HEDGE", 10)
        await _reserve(c, "t131-r1", "t131-a", "HEDGE", "aec-car-p45", 10,
                       "AMBIGUOUS", "t131-acq-A", intent="t131-h")
        with pytest.raises(Exception) as caught:
            await _reserve(c, "t131-r2", "t131-a", "HEDGE", "aec-car-p45", 10,
                           "HELD", "t131-acq-B")
        assert "one_live_reservation_per_leg" in _why(caught.value), \
            _why(caught.value)
    finally:
        await _clean(c)
        await c.close()


# ═════════════════════════════════════════════════════════════════════
# 5 · PAIRED IS CURRENT INVENTORY, NOT HISTORICAL VOLUME
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_paired_quantity_follows_current_inventory():
    """THE DEFECT: selling a leg left `paired` reporting the bought quantity.

    The first version summed ENTRY fills only, so 10 on each leg followed by
    selling all 10 of one still reported 10 paired -- historical volume
    presented as current hedging.
    """
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        await _leg(c, "t131-p", "aec-chi-ml", "t131-a", "PRIMARY", 10,
                   state="FILLED")
        await _leg(c, "t131-h", "aec-car-p45", "t131-a", "HEDGE", 10,
                   state="FILLED")

        async def paired():
            return await c.fetchval(
                "SELECT bettor_funded_group_paired_qty('t131-a')")

        assert float(await paired()) == 0.0, "nothing filled yet"
        await _fill(c, "t131-f1", "t131-p", 10, "ENTRY")
        assert float(await paired()) == 0.0, "an unhedged leg pairs nothing"
        await _fill(c, "t131-f2", "t131-h", 4, "ENTRY")
        assert float(await paired()) == 4.0, "a partial hedge pairs what filled"
        await _fill(c, "t131-f3", "t131-h", 6, "ENTRY")
        assert float(await paired()) == 10.0

        # SELL 6 FROM ONE LEG through a child EXIT intent.
        await _leg(c, "t131-x1", "aec-chi-ml", None, None, 6,
                   state="FILLED", kind="EXIT", parent="t131-p")
        await _fill(c, "t131-f4", "t131-x1", 6, "EXIT")
        assert float(await paired()) == 4.0, "selling 6 leaves 4 paired"

        # FULLY EXIT THAT LEG.
        await _leg(c, "t131-x2", "aec-chi-ml", None, None, 4,
                   state="FILLED", kind="EXIT", parent="t131-p")
        await _fill(c, "t131-f5", "t131-x2", 4, "EXIT")
        assert float(await paired()) == 0.0, "a fully exited leg pairs nothing"

        # AND HISTORICAL VOLUME IS A DIFFERENT QUESTION, still 10.
        vol = await c.fetchval(
            "SELECT bettor_funded_group_matched_volume('t131-a')")
        assert float(vol) == 10.0, (
            "matched VOLUME is history and must not follow current inventory")
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_replayed_fill_does_not_double_the_quantity():
    """Fills are keyed on the venue's own id, so a replay cannot add twice."""
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        await _group(c, "t131-a")
        await _leg(c, "t131-p", "aec-chi-ml", "t131-a", "PRIMARY", 10,
                   state="FILLED")
        await _leg(c, "t131-h", "aec-car-p45", "t131-a", "HEDGE", 10,
                   state="FILLED")
        await _fill(c, "t131-f1", "t131-p", 10, "ENTRY")
        await _fill(c, "t131-f2", "t131-h", 10, "ENTRY")
        before = float(await c.fetchval(
            "SELECT bettor_funded_group_paired_qty('t131-a')"))
        with pytest.raises(Exception):
            await _fill(c, "t131-f1", "t131-p", 10, "ENTRY")
        after = float(await c.fetchval(
            "SELECT bettor_funded_group_paired_qty('t131-a')"))
        assert before == after == 10.0
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_an_unknown_group_is_null_not_a_known_empty_one():
    """A readiness report must distinguish "no such group" from "holds none".

    Returning 0 for a group that does not exist lets a typo or a stale id read
    as a real group with nothing paired, which in a readiness check is the
    difference between "safe" and "we are looking at the wrong thing".
    """
    c = await _conn()
    try:
        if not await _has_131(c):
            pytest.skip("migration 131 not applied to this database")
        await _clean(c)
        await _require_free_capacity(c)
        assert await c.fetchval(
            "SELECT bettor_funded_group_paired_qty('t131-does-not-exist')"
        ) is None
        await _group(c, "t131-a")
        got = await c.fetchval(
            "SELECT bettor_funded_group_paired_qty('t131-a')")
        assert got is not None and float(got) == 0.0
    finally:
        await _clean(c)
        await c.close()
