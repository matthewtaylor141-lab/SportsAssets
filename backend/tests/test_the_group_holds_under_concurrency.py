"""TWO CONNECTIONS AT ONCE, which is the only way these rules can be tested.

WHY THIS FILE EXISTS. Migration 131's membership rules were written as trigger
bodies that read the group and then read its sibling legs. Read against a single
connection they look like enforcement. An independent review pointed out that
they are not, because `FOR SHARE` does not serialise -- and the race was then
REPRODUCED here before anything was changed:

    two asyncpg connections, one group, both legs on `aec-race-ml`
    insert A: ok      insert B: ok
    commit A: ok      commit B: ok
    LEGS COMMITTED: HEDGE aec-race-ml, PRIMARY aec-race-ml

A PRIMARY and a HEDGE on ONE CONTRACT, both committed. On this venue buying the
opposite side of a market reduces the same book rather than creating a second
settling holding, so that is same-contract netting -- explicitly not this
strategy -- recorded as an indirect pair. Each connection ran the same-contract
check and saw nothing, because the other's leg was uncommitted and therefore
invisible to it. Both SHARE locks were granted, so neither waited.

THE RULES UNDER TEST HERE, each with real overlap between two transactions:

  1 Two simultaneous legs on the same contract: one commits, one is refused.
  2 Two simultaneous legs in the same role: one commits, one is refused.
  3 A closure racing a hedge acquisition: the group does not end up closed with
    live exposure inside it.
  4 A closure racing a recovered fill on a leg.
  5 A binding cannot be invalidated afterwards -- not by moving the leg, and not
    by moving the GROUP out from under a leg that was validated against it.

EVERY WAIT IS BOUNDED. A serialising lock means one transaction BLOCKS, so a
test that simply awaits both would hang rather than fail -- which is how the
first version of the reproduction above ended up hanging once the lock was
strengthened. `lock_timeout` is set on each connection and every concurrent step
runs under `asyncio.wait_for`, so a lock that never resolves is a failure with a
message rather than a stuck suite.
"""

from __future__ import annotations

import asyncio
import os

import pytest

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

FIXTURE = "nba-bos-mia-2026-11-02"
OTHER_FIXTURE = "nba-lal-gsw-2026-11-02"
ACCT = "acct-131c"
PFX = "t131c-"
SLUG_A = "aec-bos-ml"
SLUG_B = "aec-mia-ml"

#: LONG ENOUGH FOR A REAL HANDOFF, SHORT ENOUGH TO FAIL A TEST. The contending
#: statement must actually block; it must not block the suite.
LOCK_TIMEOUT_MS = 4000
WAIT_S = 20.0


async def _conn():
    import asyncpg
    c = await asyncpg.connect(DSN)
    await c.execute("SET lock_timeout = %d" % LOCK_TIMEOUT_MS)
    return c


async def _has_131(c) -> bool:
    return bool(await c.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'public' "
        "  AND table_name = 'bettor_funded_portfolio_groups'"))


async def _clean(c):
    if await _has_131(c):
        await c.execute("DELETE FROM bettor_funded_leg_reservations "
                        "WHERE group_id LIKE $1", PFX + "%")
    await c.execute("DELETE FROM bettor_funded_fills WHERE intent_id LIKE $1",
                    PFX + "%")
    await c.execute("DELETE FROM bettor_funded_intents WHERE intent_id LIKE $1",
                    PFX + "%")
    if await _has_131(c):
        await c.execute("DELETE FROM bettor_funded_portfolio_groups "
                        "WHERE group_id LIKE $1", PFX + "%")


#: CI SETS THIS; see the same helper in the other 131 files.
CAPACITY_MUST_BE_FREE = os.environ.get(
    "RN1X_CAPACITY_SLOT_MUST_BE_FREE", "") not in ("", "0", "false", "FALSE")


async def _ready(c):
    if not await _has_131(c):
        pytest.skip("migration 131 not applied to this database")
    await _clean(c)
    holder = await c.fetchval(
        "SELECT group_id FROM bettor_funded_portfolio_groups "
        " WHERE closed_at IS NULL AND group_id NOT LIKE $1 LIMIT 1", PFX + "%")
    if not holder:
        return
    msg = ("group %r holds the one open-group slot; this file needs it free "
           "and will not delete another owner's row" % holder)
    if CAPACITY_MUST_BE_FREE:
        pytest.fail(msg + ". RN1X_CAPACITY_SLOT_MUST_BE_FREE is set, so this is "
                          "an ENVIRONMENT FAILURE, not a pass")
    pytest.skip(msg)


async def _group(c, gid, *, event=FIXTURE, structure="INDIRECT_MIDDLE"):
    await c.execute(
        "INSERT INTO bettor_funded_portfolio_groups "
        "(group_id, account_id, venue, event_key, structure) "
        "VALUES ($1,$2,'PMUS',$3,$4)", gid, ACCT, event, structure)


async def _insert_leg(c, iid, gid, role, slug, *, event=FIXTURE, qty=10,
                      residual=0.0, state="INTENT_RECORDED"):
    await c.execute(
        "INSERT INTO bettor_funded_intents (intent_id, account_id, venue,"
        " venue_class, us_market_slug, event_key, order_intent, limit_price,"
        " quantity, collateral_usd, effective_digest, state, kind,"
        " residual_qty, portfolio_group_id, leg_role) VALUES "
        "($1,$2,'PMUS','US',$3,$4,'ORDER_INTENT_BUY_LONG',0.5,$5,$6,$7,"
        " $8,'ENTRY',$9,$10,$11)",
        iid, ACCT, slug, event, int(qty), float(qty) * 0.5, iid + "-d", state,
        float(residual), gid, role)


def _name(exc) -> str:
    return "%s: %s" % (type(exc).__name__, " ".join(str(exc).split())[:200])


async def _race(first, second, *, label):
    """Run `first` to completion inside its transaction, start `second` while
    that transaction is still open, then commit `first` and let `second` finish.

    Returns (outcome_of_second_before_commit, outcome_after). Each await is
    bounded, so a lock that is never released fails the test.
    """
    raise NotImplementedError  # replaced by explicit sequences below


# ═════════════════════════════════════════════════════════════════════
# 1 · SIMULTANEOUS LEGS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_two_simultaneous_legs_on_the_same_contract_cannot_both_commit():
    """THE REPRODUCED RACE, now asserted the other way.

    Connection A inserts the PRIMARY and holds its transaction open. Connection
    B inserts a HEDGE on the SAME contract. Under `FOR SHARE` B was granted its
    lock immediately, saw no sibling, and committed -- two roles on one
    instrument. Under `FOR NO KEY UPDATE` B must WAIT for A, and once A commits
    B must be refused by name.
    """
    a = await _conn()
    b = await _conn()
    try:
        await _ready(a)
        await _group(a, PFX + "g1")
        ta = a.transaction()
        await ta.start()
        await _insert_leg(a, PFX + "pa", PFX + "g1", "PRIMARY", SLUG_A)

        tb = b.transaction()
        await tb.start()
        # B CONTENDS WHILE A IS STILL OPEN. It must not succeed here: either it
        # blocks (and then fails after A commits) or it times out on the lock.
        task = asyncio.ensure_future(
            _insert_leg(b, PFX + "hb", PFX + "g1", "HEDGE", SLUG_A))
        await asyncio.sleep(0.4)
        blocked = not task.done()

        await ta.commit()
        try:
            await asyncio.wait_for(task, timeout=WAIT_S)
            outcome = None
        except asyncio.TimeoutError:
            task.cancel()
            pytest.fail("B never resolved within %ss -- a serialising lock must "
                        "release when A commits" % WAIT_S)
        except Exception as exc:                            # noqa: BLE001
            outcome = exc
        if outcome is None:
            await tb.commit()
        else:
            await tb.rollback()

        legs = await a.fetch(
            "SELECT intent_id, leg_role, us_market_slug FROM "
            " bettor_funded_intents WHERE portfolio_group_id=$1 ORDER BY 1",
            PFX + "g1")
        slugs = {r["us_market_slug"] for r in legs}
        assert not (len(legs) == 2 and len(slugs) == 1), (
            "both legs committed on ONE contract -- that is same-contract "
            "netting recorded as an indirect pair: %r" % [dict(r) for r in legs])
        assert len(legs) == 1, [dict(r) for r in legs]
        assert outcome is not None, (
            "B's insert succeeded; the same-contract rule did not serialise")
        assert "with itself" in str(outcome) or "same contract" in str(outcome) \
            or "lock timeout" in str(outcome).lower(), _name(outcome)
        # RECORDED: whether B actually waited. Either answer is acceptable (a
        # lock timeout is also a refusal) but they are not the same thing, and a
        # future reader should not have to guess which happened.
        print("B blocked on the lock: %s; refusal: %s" % (blocked,
                                                          _name(outcome)))
    finally:
        await _clean(a)
        await a.close()
        await b.close()


@pg
async def test_two_simultaneous_legs_in_the_same_role_cannot_both_commit():
    """THE ROLE BOUND UNDER CONCURRENCY. `bettor_funded_one_open_leg_per_role`
    is a unique index, so this one the database serialises on its own -- but
    only because the index exists and both rows are OPEN. Asserted rather than
    assumed, because the same-contract sibling check next to it did not."""
    a = await _conn()
    b = await _conn()
    try:
        await _ready(a)
        await _group(a, PFX + "g2")
        ta = a.transaction()
        await ta.start()
        await _insert_leg(a, PFX + "pa2", PFX + "g2", "PRIMARY", SLUG_A,
                          residual=10.0, state="FILLED")

        tb = b.transaction()
        await tb.start()
        task = asyncio.ensure_future(
            _insert_leg(b, PFX + "pb2", PFX + "g2", "PRIMARY", SLUG_B,
                        residual=10.0, state="FILLED"))
        await asyncio.sleep(0.4)
        await ta.commit()
        outcome = None
        try:
            await asyncio.wait_for(task, timeout=WAIT_S)
        except asyncio.TimeoutError:
            task.cancel()
            pytest.fail("B never resolved within %ss" % WAIT_S)
        except Exception as exc:                            # noqa: BLE001
            outcome = exc
        if outcome is None:
            await tb.commit()
        else:
            await tb.rollback()
        assert outcome is not None, "two open PRIMARY legs both committed"
        assert await a.fetchval(
            "SELECT count(*) FROM bettor_funded_intents "
            " WHERE portfolio_group_id=$1 AND leg_role='PRIMARY'",
            PFX + "g2") == 1
    finally:
        await _clean(a)
        await a.close()
        await b.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · CLOSURE RACING EXPOSURE
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_closure_racing_a_hedge_acquisition_never_strands_exposure():
    """THE WORST OUTCOME IS A CLOSED GROUP WITH LIVE LEGS IN IT -- capacity
    released while contracts are owned, and an accounting label that says the
    position is finished.

    A closes the group (its only leg holds nothing). B simultaneously attaches a
    hedge. Whichever order they resolve in, the end state must be coherent: a
    CLOSED group holds no open leg, and an OPEN group is free to.
    """
    a = await _conn()
    b = await _conn()
    try:
        await _ready(a)
        await _group(a, PFX + "g3")
        await _insert_leg(a, PFX + "pa3", PFX + "g3", "PRIMARY", SLUG_A,
                          state="ABANDONED")
        await a.execute(
            "UPDATE bettor_funded_intents SET closed_at=now(), "
            "  closed_reason='NEVER_HELD_ANY_INVENTORY' WHERE intent_id=$1",
            PFX + "pa3")

        ta = a.transaction()
        await ta.start()
        closed_label = await a.fetchval(
            "SELECT bettor_funded_group_release($1)", PFX + "g3")

        tb = b.transaction()
        await tb.start()
        task = asyncio.ensure_future(
            _insert_leg(b, PFX + "hb3", PFX + "g3", "HEDGE", SLUG_B,
                        residual=10.0, state="FILLED"))
        await asyncio.sleep(0.4)
        await ta.commit()
        hedge_err = None
        try:
            await asyncio.wait_for(task, timeout=WAIT_S)
        except asyncio.TimeoutError:
            task.cancel()
            pytest.fail("the hedge insert never resolved within %ss" % WAIT_S)
        except Exception as exc:                            # noqa: BLE001
            hedge_err = exc
        if hedge_err is None:
            await tb.commit()
        else:
            await tb.rollback()

        g = await a.fetchrow(
            "SELECT closed_at, closure FROM bettor_funded_portfolio_groups "
            " WHERE group_id=$1", PFX + "g3")
        open_legs = await a.fetchval(
            "SELECT count(*) FROM bettor_funded_intents "
            " WHERE portfolio_group_id=$1 AND kind='ENTRY' "
            "   AND bettor_funded_position_is_open(state, residual_qty, "
            "                                      closed_at)", PFX + "g3")
        print("release said %r; hedge %s; group closed=%s; open legs=%s"
              % (closed_label, "refused" if hedge_err else "committed",
                 g["closed_at"] is not None, open_legs))
        if g["closed_at"] is not None:
            assert open_legs == 0, (
                "the group is CLOSED as %r while %d open leg(s) remain in it: "
                "capacity was released with exposure inside"
                % (g["closure"], open_legs))
            assert hedge_err is not None, (
                "the hedge committed into a group that then closed")
            assert "closed" in str(hedge_err), _name(hedge_err)
        else:
            # The acquisition won; that is fine, provided the group stayed open
            # for it.
            assert hedge_err is None, _name(hedge_err)
    finally:
        await _clean(a)
        await a.close()
        await b.close()


@pg
async def test_a_closure_racing_a_recovered_fill_does_not_release_the_slot():
    """RECOVERY IS WHERE THIS BITES IN PRODUCTION. A cycle concludes the leg
    holds nothing and asks for a release; at the same moment recovery learns
    from the venue that the order DID fill and writes the residual. If the
    closure wins, the slot is released while contracts are owned.

    The release must not see a stale zero: the fill is written first in B's
    still-open transaction, and A's release attempt must either wait and then
    decline, or -- if it went first -- leave a group that the fill then finds
    closed and is refused by.
    """
    a = await _conn()
    b = await _conn()
    try:
        await _ready(a)
        await _group(a, PFX + "g4", structure="SINGLE_LEG")
        await _insert_leg(a, PFX + "pa4", PFX + "g4", "PRIMARY", SLUG_A,
                          state="UNRESOLVED")

        # B: RECOVERY WRITES THE FILL, and holds its transaction open.
        tb = b.transaction()
        await tb.start()
        await b.execute(
            "UPDATE bettor_funded_intents SET state='FILLED', "
            "  residual_qty=10, resolved_at=now() WHERE intent_id=$1",
            PFX + "pa4")

        # A: THE CLOSURE ATTEMPT, concurrent with that.
        ta = a.transaction()
        await ta.start()
        task = asyncio.ensure_future(
            a.fetchval("SELECT bettor_funded_group_release($1)", PFX + "g4"))
        await asyncio.sleep(0.4)
        await tb.commit()
        try:
            label = await asyncio.wait_for(task, timeout=WAIT_S)
        except asyncio.TimeoutError:
            task.cancel()
            pytest.fail("the release never resolved within %ss" % WAIT_S)
        await ta.commit()

        held = await a.fetchval(
            "SELECT bettor_funded_group_leg_inventory($1,'PRIMARY')::float8",
            PFX + "g4")
        closed = await a.fetchval(
            "SELECT closed_at IS NOT NULL FROM bettor_funded_portfolio_groups "
            " WHERE group_id=$1", PFX + "g4")
        print("release returned %r; inventory now %s; group closed %s"
              % (label, held, closed))
        assert held == pytest.approx(10.0)
        assert closed is False, (
            "the group closed while 10 recovered contracts are held -- the "
            "capacity slot was released against live exposure")
        assert label is None, (
            "the release claimed a closure (%r) on a leg that holds "
            "inventory" % label)
    finally:
        await _clean(a)
        await a.close()
        await b.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · A BINDING CANNOT BE INVALIDATED AFTERWARDS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_leg_cannot_be_moved_onto_the_other_legs_contract():
    """THE CHECK MUST HOLD ON UPDATE, not only on INSERT. A pair assembled
    correctly and then edited into one contract is the same economic error,
    arrived at more slowly."""
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g5")
        await _insert_leg(c, PFX + "pa5", PFX + "g5", "PRIMARY", SLUG_A,
                          residual=10.0, state="FILLED")
        await _insert_leg(c, PFX + "hb5", PFX + "g5", "HEDGE", SLUG_B,
                          residual=10.0, state="FILLED")
        with pytest.raises(Exception) as caught:
            await c.execute(
                "UPDATE bettor_funded_intents SET us_market_slug=$2 "
                " WHERE intent_id=$1", PFX + "hb5", SLUG_A)
        assert "with itself" in str(caught.value), _name(caught.value)
        assert await c.fetchval(
            "SELECT us_market_slug FROM bettor_funded_intents "
            " WHERE intent_id=$1", PFX + "hb5") == SLUG_B
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_the_group_cannot_be_moved_out_from_under_its_legs():
    """THE GAP THE LEG TRIGGER CANNOT SEE.

    `bettor_funded_leg_matches_group` re-validates a LEG when the leg changes.
    Nothing watched the GROUP. So the group's `event_key` could be set to a
    different fixture after both legs had been validated against the old one,
    leaving two bindings quietly false -- and the combined economics and
    `bettor_funded_group_matched_volume` read those fields. A group's account,
    venue and fixture are its identity and are now fixed.
    """
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g6")
        await _insert_leg(c, PFX + "pa6", PFX + "g6", "PRIMARY", SLUG_A,
                          residual=10.0, state="FILLED")
        for col, val in (("event_key", OTHER_FIXTURE),
                         ("account_id", "acct-somebody-else"),
                         ("venue", "OTHERVENUE")):
            with pytest.raises(Exception) as caught:
                await c.execute(
                    "UPDATE bettor_funded_portfolio_groups SET %s=$2 "
                    " WHERE group_id=$1" % col, PFX + "g6", val)
            assert "identity is fixed" in str(caught.value), _name(caught.value)
        row = await c.fetchrow(
            "SELECT account_id, venue, event_key FROM "
            " bettor_funded_portfolio_groups WHERE group_id=$1", PFX + "g6")
        assert row["event_key"] == FIXTURE
        assert row["account_id"] == ACCT
        assert row["venue"] == "PMUS"
        # AND THE STRUCTURE IS STILL REFINABLE: a single leg that starts seeking
        # a hedge is an ordinary transition, not an identity change.
        await c.execute(
            "UPDATE bettor_funded_portfolio_groups "
            "   SET structure='INDIRECT_MIDDLE', hedge_intent='SOUGHT' "
            " WHERE group_id=$1", PFX + "g6")
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_closed_group_is_not_reopened():
    """ITS CLOSURE IS RECORDED HISTORY AND ITS CAPACITY WAS RELEASED. Clearing
    `closed_at` would hand back a slot that has already been accounted for and
    make the stored closure reason a lie."""
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g7", structure="SINGLE_LEG")
        await _insert_leg(c, PFX + "pa7", PFX + "g7", "PRIMARY", SLUG_A,
                         state="ABANDONED")
        await c.execute(
            "UPDATE bettor_funded_intents SET closed_at=now(), "
            "  closed_reason='NEVER_HELD_ANY_INVENTORY' WHERE intent_id=$1",
            PFX + "pa7")
        label = await c.fetchval("SELECT bettor_funded_group_release($1)",
                                 PFX + "g7")
        assert label == "NEVER_HELD_ANY_INVENTORY", label
        with pytest.raises(Exception) as caught:
            await c.execute(
                "UPDATE bettor_funded_portfolio_groups SET closed_at=NULL, "
                "  closure=NULL WHERE group_id=$1", PFX + "g7")
        assert "not re-opened" in str(caught.value), _name(caught.value)
        assert await c.fetchval(
            "SELECT closed_at IS NOT NULL FROM "
            " bettor_funded_portfolio_groups WHERE group_id=$1",
            PFX + "g7") is True
    finally:
        await _clean(c)
        await c.close()
