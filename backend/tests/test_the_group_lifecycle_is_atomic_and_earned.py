"""THE FOUR PROOFS AN INDEPENDENT REVIEW ASKED FOR BY NAME, on a real database.

Each section exists because a specific claim I had made was checked and found
untrue. The claims are restated here next to the evidence that now settles them,
so a later reader can see which assertion retired which defect.

  1 ATOMICITY WAS CLAIMED AND ABSENT. The group INSERT and the intent INSERT
    were two autocommitted statements. A failure between them left an EMPTY
    GROUP holding the one capacity slot -- capacity consumed by nothing. Proved
    here by INJECTING a failure after the group is created, both for a
    standalone call and for a call inside a caller's transaction, and by
    checking that an expected database refusal is returned OUTSIDE a poisoned
    transaction.

  2 A GROUP-RELEASE REFUSAL MUST NOT ABORT VALID POSITION ACCOUNTING. Fully
    exiting ONE leg of a pair has to commit the execution, its fees, the
    residual and that leg's closure, leave the group OPEN because the other leg
    is still exposure, and leave the transaction usable.

  3 AUTOMATIC CLOSURE WROTE FALSE HISTORY. A settled group was labelled
    ALL_LEGS_EXITED. `reconcile_settlement()` is exercised HERE -- not
    simulated with hand SQL -- and the group's persisted closure is asserted
    with NO corrective UPDATE anywhere in this file. Mixed outcomes and the
    last-reservation-after-the-legs sequence are covered too.

  4 THE SCHEMA PROBE WAS NOT SAFELY ADAPTIVE. A process-wide boolean could
    latch "absent" from a transient catalogue error and then refuse every entry
    for the life of the process. An unavailable database and an absent
    migration must be distinguishable, and a half-applied migration must be
    neither.

WHY THERE IS NO CORRECTIVE SQL IN THIS FILE. The earlier settlement test wrote
the group's `closure` itself and then asserted the value it had just written,
which is why a trigger that labelled every closure ALL_LEGS_EXITED passed. A
test may set up inputs; it may not write the thing under test.
"""

from __future__ import annotations

import os
import uuid

import pytest

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

FIXTURE = "nfl-den-lv-2026-10-04"
ACCT = "acct-131g"
PFX = "t131g-"


async def _conn():
    import asyncpg
    return await asyncpg.connect(DSN)


async def _has_131(c) -> bool:
    return bool(await c.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name = 'bettor_funded_portfolio_groups'"))


async def _clean(c):
    """Only this file's rows, by prefix. Never a TRUNCATE, and guarded so it is
    safe in `finally` after a skip on a database without 131."""
    has = await _has_131(c)
    if has:
        await c.execute("DELETE FROM bettor_funded_leg_reservations "
                        "WHERE group_id LIKE $1 OR group_id LIKE $2",
                        PFX + "%", "grp:" + PFX + "%")
    await c.execute(
        "DELETE FROM bettor_funded_economics WHERE intent_id LIKE $1",
        PFX + "%")
    await c.execute(
        "DELETE FROM bettor_funded_discrepancies WHERE intent_id LIKE $1",
        PFX + "%")
    await c.execute(
        "DELETE FROM bettor_funded_fills WHERE intent_id LIKE $1", PFX + "%")
    await c.execute(
        "DELETE FROM bettor_funded_intents WHERE intent_id LIKE $1", PFX + "%")
    if has:
        await c.execute("DELETE FROM bettor_funded_portfolio_groups "
                        "WHERE group_id LIKE $1 OR group_id LIKE $2",
                        PFX + "%", "grp:" + PFX + "%")


#: CI SETS THIS. See `_free_capacity_or_skip`.
CAPACITY_MUST_BE_FREE = os.environ.get(
    "RN1X_CAPACITY_SLOT_MUST_BE_FREE", "") not in ("", "0", "false", "FALSE")


async def _free_capacity_or_skip(c):
    """SKIP, NEVER DELETE, when something else holds the one open-group slot --
    unless this is the gate, where the same condition is a FAILURE.

    TWO DIFFERENT SITUATIONS, AND THEY NEED DIFFERENT ANSWERS.

      * ON A SHARED OR DEVELOPMENT DATABASE a skip is right. That row could be
        a real position, and a test that clears another owner's open group to
        make room for itself destroys the exact state a funded system exists to
        protect.
      * IN THE GATE it is an ENVIRONMENT FAILURE and must be rejected. The gate
        builds a freshly migrated, isolated database, so nothing can legitimately
        hold the slot. A capacity-slot skip there means the acceptance database
        was dirty or another test leaked a group -- and a silent skip turns the
        capital-critical assertions in this file into a green tick that proved
        nothing. An independent review named this specifically.

    So the gate exports `RN1X_CAPACITY_SLOT_MUST_BE_FREE=1` and the same
    condition fails instead of skipping. The rule lives here rather than in a
    post-hoc log grep, so it cannot be forgotten when a job is edited.
    """
    holder = await c.fetchval(
        "SELECT group_id FROM bettor_funded_portfolio_groups "
        " WHERE closed_at IS NULL AND group_id NOT LIKE $1 "
        "   AND group_id NOT LIKE $2 LIMIT 1", PFX + "%", "grp:" + PFX + "%")
    if not holder:
        return
    msg = ("group %r holds the one open-group slot; this file needs it free and "
           "will not delete another owner's row" % holder)
    if CAPACITY_MUST_BE_FREE:
        pytest.fail(
            msg + ". RN1X_CAPACITY_SLOT_MUST_BE_FREE is set, so this is an "
            "ENVIRONMENT FAILURE, not a pass: the acceptance database must be "
            "freshly migrated and isolated, and these capital-critical "
            "assertions must actually run")
    pytest.skip(msg)


async def _ready(c):
    if not await _has_131(c):
        pytest.skip("migration 131 not applied to this database")
    await _clean(c)
    await _free_capacity_or_skip(c)


async def _open_groups(c) -> list[str]:
    return [r["group_id"] for r in await c.fetch(
        "SELECT group_id FROM bettor_funded_portfolio_groups "
        " WHERE closed_at IS NULL ORDER BY opened_at")]


async def _closure(c, gid) -> str | None:
    return await c.fetchval(
        "SELECT closure FROM bettor_funded_portfolio_groups "
        " WHERE group_id = $1", gid)


async def _entry(c, iid, slug, *, gid=None, role=None, structure=None):
    """The REAL entry path. Nothing here writes the intents table directly,
    because the defect in (1) lived in `record_intent` and a fixture INSERT
    would have stepped straight over it."""
    from sportsassets import bettor_funded_book as FB
    return await FB.record_intent(
        c, intent_id=iid, account_id=ACCT, venue="PMUS", venue_class="US",
        us_market_slug=slug, event_key=FIXTURE,
        order_intent="ORDER_INTENT_BUY_LONG", limit_price=0.5, quantity=10,
        collateral_usd=5.0, effective_digest=iid + "-d",
        portfolio_group_id=gid, leg_role=role, group_structure=structure)


async def _fill(c, iid, qty, direction, *, fid=None, price=0.5):
    """Go through the real path: acknowledge, then `ingest_fills`.

    `ingest_fills` is what maintains `residual_qty` and what the group release
    is now called from, so a fixture that inserted fill rows directly would
    test none of the behaviour these proofs are about.
    """
    from sportsassets import bettor_funded_book as FB
    fid = fid or ("%s-%s-%s" % (iid, direction, uuid.uuid4().hex[:6]))
    await FB.record_acknowledgement(c, iid, venue_order_id="vo-" + iid,
                                   status="open")
    return await FB.ingest_fills(
        c, iid,
        [{"qty": float(qty), "price": float(price), "venue_fill_id": fid}],
        direction=direction)


def _probe_reporting(price):
    """A settlement probe that reports an authoritative price. A stub because
    the venue is not reachable from a test -- but `reconcile_settlement` itself
    is NOT stubbed, which is the point of section 3."""
    def probe(_client, _slug):
        return {"terminal_reading": "REPORTED_SETTLEMENT",
                "authoritative_payout_present": True,
                "reader_verdict": {"settlement_price": float(price)},
                "why": "test probe reporting an authoritative settlement"}
    return probe


# ═════════════════════════════════════════════════════════════════════
# 1 · ATOMICITY, INJECTED -- NOT ASSERTED FROM THE DOCSTRING
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_failure_after_the_group_is_created_leaves_no_empty_group():
    """INJECT the failure between the two writes and count the groups.

    THE INJECTION. `order_intent` is passed through to the insert, and the
    table's CHECK admits only the two BUY forms. So the group INSERT succeeds
    and the intent INSERT is refused by the database -- exactly the window the
    old code left open. Before the repair this left `grp:<iid>` behind with no
    leg in it, holding the one capacity slot against nothing.
    """
    c = await _conn()
    try:
        await _ready(c)
        from sportsassets import bettor_funded_book as FB
        before = await _open_groups(c)
        with pytest.raises(Exception) as caught:
            await FB.record_intent(
                c, intent_id=PFX + "atom1", account_id=ACCT, venue="PMUS",
                venue_class="US", us_market_slug="aec-den-ml",
                event_key=FIXTURE,
                order_intent="ORDER_INTENT_SELL_SOMETHING_INVALID",
                limit_price=0.5, quantity=10, collateral_usd=5.0,
                effective_digest=PFX + "atom1-d")
        assert "order_intent" in str(caught.value).lower() or \
            "check constraint" in str(caught.value).lower(), caught.value
        after = await _open_groups(c)
        assert after == before, (
            "a failed entry left a group behind: %r" % (
                set(after) - set(before)))
        assert await c.fetchval(
            "SELECT count(*) FROM bettor_funded_portfolio_groups "
            " WHERE group_id = $1", "grp:" + PFX + "atom1") == 0
        # AND THE CONNECTION IS STILL USABLE.
        assert await c.fetchval("SELECT 42") == 42
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_the_same_failure_inside_a_callers_transaction_rolls_back_only_ours():
    """THE NESTED CASE, which is the one that matters in production: fill
    ingestion and settlement both call into the book inside a transaction they
    own. `conn.transaction()` nests as a SAVEPOINT, so our two writes roll back
    and the caller's earlier work survives."""
    c = await _conn()
    try:
        await _ready(c)
        from sportsassets import bettor_funded_book as FB
        before = await _open_groups(c)
        tx = c.transaction()
        await tx.start()
        try:
            # THE CALLER'S OWN EARLIER WORK, which must survive our rollback.
            marker = await c.fetchval("SELECT 7")
            assert marker == 7
            with pytest.raises(Exception):
                await FB.record_intent(
                    c, intent_id=PFX + "atom2", account_id=ACCT, venue="PMUS",
                    venue_class="US", us_market_slug="aec-den-ml",
                    event_key=FIXTURE, order_intent="NOT_AN_ORDER_INTENT",
                    limit_price=0.5, quantity=10, collateral_usd=5.0,
                    effective_digest=PFX + "atom2-d")
            # THE OUTER TRANSACTION IS STILL ALIVE. Before the repair the
            # savepoint did not exist, so the failure aborted the whole
            # transaction and this query raised InFailedSQLTransactionError.
            assert await c.fetchval("SELECT 8") == 8
            assert await _open_groups(c) == before
        finally:
            await tx.rollback()
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_an_expected_refusal_is_returned_outside_a_poisoned_transaction():
    """A COMPETING ENTRY GETS A NAMED REFUSAL, and the caller's transaction is
    still usable afterwards.

    THE MEASURED DEFECT. Inside a caller's transaction the second entry
    returned NO refusal at all: `open_entry_positions()` in the refusal handler
    itself raised `InFailedSQLTransactionError`, because catching a
    PostgresError does not undo an aborted transaction. The `except` is now
    outside the savepoint block, so the rollback has already happened when the
    handler runs its queries.
    """
    c = await _conn()
    try:
        await _ready(c)
        first = await _entry(c, PFX + "live1", "aec-den-ml")
        assert first["ok"] is True, first
        tx = c.transaction()
        await tx.start()
        try:
            second = await _entry(c, PFX + "live2", "aec-lv-ml")
            assert second["ok"] is False
            assert second["refusal"] is not None, second
            # AND THE TRANSACTION STILL WORKS.
            assert await c.fetchval("SELECT 42") == 42
        finally:
            await tx.rollback()
    finally:
        await _clean(c)
        await c.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · A DECLINED RELEASE DOES NOT COST VALID ACCOUNTING
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_fully_exiting_one_leg_keeps_the_group_and_commits_everything():
    """EXIT ONE LEG COMPLETELY while the other is still held.

    WHAT MUST ALL BE TRUE AT ONCE: the exit execution is written, its fee is
    booked, the parent's residual is zero, that leg is closed with a reason,
    the GROUP IS STILL OPEN because the hedge is exposure, and the transaction
    accepts further SQL. The old code attempted a closing UPDATE the trigger
    refuses, and the resulting raise aborted the transaction carrying all of
    the above while returning a tidy dict that said otherwise.
    """
    c = await _conn()
    try:
        await _ready(c)
        from sportsassets import bettor_funded_book as FB
        prim = await _entry(c, PFX + "p1", "aec-den-ml",
                            structure="INDIRECT_MIDDLE")
        assert prim["ok"] is True, prim
        gid = prim["portfolio_group_id"]
        await _fill(c, PFX + "p1", 10, "ENTRY")
        hedge = await _entry(c, PFX + "h1", "aec-lv-ml", gid=gid, role="HEDGE")
        assert hedge["ok"] is True, hedge
        await _fill(c, PFX + "h1", 10, "ENTRY")

        # AN EXIT CHILD ON THE PRIMARY, FILLED IN FULL.
        await c.execute(
            "INSERT INTO bettor_funded_intents (intent_id, account_id, venue,"
            " venue_class, us_market_slug, event_key, order_intent,"
            " limit_price, quantity, collateral_usd, effective_digest, state,"
            " provenance, kind, parent_intent_id) VALUES "
            "($1,$2,'PMUS','US',$3,$4,'ORDER_INTENT_BUY_LONG',0.6,10,0,$5,"
            " 'INTENT_RECORDED',$6,'EXIT',$7)",
            PFX + "x1", ACCT, "aec-den-ml", FIXTURE, PFX + "x1-d",
            FB.PROVENANCE, PFX + "p1")

        tx = c.transaction()
        await tx.start()
        try:
            ing = await _fill(c, PFX + "x1", 10, "EXIT", price=0.6)
            assert ing["ok"] is True, ing

            # THE EXECUTION IS THERE.
            assert await c.fetchval(
                "SELECT count(*) FROM bettor_funded_fills "
                " WHERE intent_id = $1 AND direction = 'EXIT'",
                PFX + "x1") == 1
            # ITS FEE IS BOOKED -- a number, not a NULL.
            fee = await c.fetchval(
                "SELECT fee_usd::float8 FROM bettor_funded_fills "
                " WHERE intent_id = $1", PFX + "x1")
            assert fee is not None
            # THE PARENT'S RESIDUAL IS ZERO AND THAT LEG IS CLOSED.
            leg = await c.fetchrow(
                "SELECT residual_qty::float8 AS r, closed_at, closed_reason "
                "  FROM bettor_funded_intents WHERE intent_id = $1",
                PFX + "p1")
            assert leg["r"] == pytest.approx(0.0)
            assert leg["closed_at"] is not None
            assert leg["closed_reason"] == "EXITED_IN_THE_MARKET"
            # THE GROUP IS STILL OPEN, because the hedge is still exposure.
            assert gid in await _open_groups(c), (
                "the group closed while the hedge leg still held contracts")
            assert await c.fetchval(
                "SELECT bettor_funded_group_leg_inventory($1,'HEDGE')::float8",
                gid) == pytest.approx(10.0)
            # AND THE TRANSACTION STILL ACCEPTS SQL.
            assert await c.fetchval("SELECT 99") == 99
            await tx.commit()
        except BaseException:
            await tx.rollback()
            raise

        # COMMITTED, not merely visible inside the transaction.
        assert await c.fetchval(
            "SELECT closed_reason FROM bettor_funded_intents "
            " WHERE intent_id = $1", PFX + "p1") == "EXITED_IN_THE_MARKET"
        assert gid in await _open_groups(c)
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_partial_exit_declines_the_release_and_says_why():
    """A PARTIAL EXIT LEAVES INVENTORY, so the release must decline -- and the
    reason must name the inventory rather than a generic failure."""
    c = await _conn()
    try:
        await _ready(c)
        from sportsassets import bettor_funded_book as FB
        prim = await _entry(c, PFX + "p2", "aec-den-ml")
        gid = prim["portfolio_group_id"]
        await _fill(c, PFX + "p2", 10, "ENTRY")
        await c.execute(
            "INSERT INTO bettor_funded_intents (intent_id, account_id, venue,"
            " venue_class, us_market_slug, event_key, order_intent,"
            " limit_price, quantity, collateral_usd, effective_digest, state,"
            " provenance, kind, parent_intent_id) VALUES "
            "($1,$2,'PMUS','US',$3,$4,'ORDER_INTENT_BUY_LONG',0.6,4,0,$5,"
            " 'INTENT_RECORDED',$6,'EXIT',$7)",
            PFX + "x2", ACCT, "aec-den-ml", FIXTURE, PFX + "x2-d",
            FB.PROVENANCE, PFX + "p2")
        await _fill(c, PFX + "x2", 4, "EXIT", price=0.6)

        assert await c.fetchval(
            "SELECT residual_qty::float8 FROM bettor_funded_intents "
            " WHERE intent_id = $1", PFX + "p2") == pytest.approx(6.0)
        assert gid in await _open_groups(c)
        why = await c.fetchval("SELECT bettor_funded_group_blocker($1)", gid)
        assert why is not None and "still holds" in why, why
        rel = await FB.try_release_group(c, PFX + "p2")
        assert rel["released"] is False
        assert "still holds" in rel["why"], rel
    finally:
        await _clean(c)
        await c.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · ONE CLOSURE AUTHORITY, AND THE HISTORY IT WRITES IS TRUE
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_reconcile_settlement_itself_persists_both_legs_settled():
    """RUN `reconcile_settlement()`, TWICE, AND READ THE GROUP BACK.

    NO CORRECTIVE SQL. The previous version of this proof wrote the group's
    `closure` by hand and then asserted the value it had written, which is how
    a trigger that labelled every closure ALL_LEGS_EXITED survived. Here the
    only writer is the production path.
    """
    c = await _conn()
    try:
        await _ready(c)
        from sportsassets import bettor_funded_management as FM
        prim = await _entry(c, PFX + "s1", "aec-den-ml",
                            structure="INDIRECT_MIDDLE")
        gid = prim["portfolio_group_id"]
        await _fill(c, PFX + "s1", 10, "ENTRY")
        hedge = await _entry(c, PFX + "s2", "aec-lv-ml", gid=gid, role="HEDGE")
        assert hedge["ok"] is True, hedge
        await _fill(c, PFX + "s2", 10, "ENTRY")

        first = await FM.reconcile_settlement(
            c, intent_id=PFX + "s1", probe=_probe_reporting(1.0))
        assert first["ok"] is True, first
        assert first["closed"] is True
        # ONE LEG SETTLED IS NOT THE GROUP SETTLED.
        assert gid in await _open_groups(c)
        assert await _closure(c, gid) is None

        second = await FM.reconcile_settlement(
            c, intent_id=PFX + "s2", probe=_probe_reporting(0.0))
        assert second["ok"] is True, second
        assert gid not in await _open_groups(c), (
            "both legs settled and the group is still open")
        assert await _closure(c, gid) == "BOTH_LEGS_SETTLED", (
            "the group's persisted closure must say the legs SETTLED. "
            "'ALL_LEGS_EXITED' here is the false history this asserts against")
        # AND THE RELEASE IT REPORTED AGREES WITH WHAT IS STORED.
        assert second["closure"]["group_release"]["closure"] == \
            "BOTH_LEGS_SETTLED"
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_an_exit_on_one_leg_and_a_settlement_on_the_other_is_mixed():
    """A MIXED OUTCOME IS LABELLED MIXED. Calling it ALL_LEGS_EXITED claims a
    sale that never happened; calling it BOTH_LEGS_SETTLED claims the venue
    resolved a leg we sold. The combined economics read this label."""
    c = await _conn()
    try:
        await _ready(c)
        from sportsassets import bettor_funded_book as FB
        from sportsassets import bettor_funded_management as FM
        prim = await _entry(c, PFX + "m1", "aec-den-ml",
                            structure="INDIRECT_MIDDLE")
        gid = prim["portfolio_group_id"]
        await _fill(c, PFX + "m1", 10, "ENTRY")
        hedge = await _entry(c, PFX + "m2", "aec-lv-ml", gid=gid, role="HEDGE")
        await _fill(c, PFX + "m2", 10, "ENTRY")

        # LEG ONE IS SOLD IN THE MARKET, through the fill path.
        await c.execute(
            "INSERT INTO bettor_funded_intents (intent_id, account_id, venue,"
            " venue_class, us_market_slug, event_key, order_intent,"
            " limit_price, quantity, collateral_usd, effective_digest, state,"
            " provenance, kind, parent_intent_id) VALUES "
            "($1,$2,'PMUS','US',$3,$4,'ORDER_INTENT_BUY_LONG',0.6,10,0,$5,"
            " 'INTENT_RECORDED',$6,'EXIT',$7)",
            PFX + "mx", ACCT, "aec-den-ml", FIXTURE, PFX + "mx-d",
            FB.PROVENANCE, PFX + "m1")
        await _fill(c, PFX + "mx", 10, "EXIT", price=0.6)
        assert await c.fetchval(
            "SELECT closed_reason FROM bettor_funded_intents "
            " WHERE intent_id=$1", PFX + "m1") == "EXITED_IN_THE_MARKET"
        assert gid in await _open_groups(c)

        # LEG TWO IS RESOLVED BY THE VENUE.
        got = await FM.reconcile_settlement(
            c, intent_id=PFX + "m2", probe=_probe_reporting(1.0))
        assert got["ok"] is True, got
        assert gid not in await _open_groups(c)
        assert await _closure(c, gid) == "MIXED_EXIT_AND_SETTLEMENT", (
            "one leg was SOLD and one was SETTLED; neither single label is "
            "true of this group")
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_the_last_reservation_going_terminal_after_the_legs_releases_it():
    """THE SEQUENCE NO INTENT EVENT NOTICES. A reservation can resolve AFTER
    every leg has closed. Nothing in the intents table changes then, so the
    group would sit open holding the capacity slot with no event left to
    release it. The reservation trigger is the path for exactly this, and it is
    the reason reservations keep a trigger while intents do not."""
    c = await _conn()
    try:
        await _ready(c)
        prim = await _entry(c, PFX + "r1", "aec-den-ml")
        gid = prim["portfolio_group_id"]
        # A RESERVATION STILL CLAIMING CAPACITY, bound to this leg.
        await c.execute(
            "INSERT INTO bettor_funded_leg_reservations "
            "(reservation_id, group_id, leg_role, us_market_slug, quantity, "
            " collateral_usd, limit_price, state, operation_id, intent_id) "
            "VALUES ($1,$2,'PRIMARY',$3,10,5.0,0.5,'AMBIGUOUS',$4,$5)",
            PFX + "res1", gid, "aec-den-ml", PFX + "op1", PFX + "r1")

        # THE LEG FINISHES FIRST -- order terminal, nothing held, closed with a
        # reason. `abandon_before_send` itself asks for the release, and it must
        # DECLINE here, because the reservation is still live.
        from sportsassets import bettor_funded_book as FB
        await FB.abandon_before_send(c, PFX + "r1", "NOTHING_LEFT_THIS_PROCESS")
        await FB.mark_position_closed(c, PFX + "r1", "NEVER_HELD_ANY_INVENTORY")
        assert gid in await _open_groups(c), (
            "the reservation still claims the capacity, so the group must stay "
            "open")
        # AND THE RESERVATION IS THE ONLY THING LEFT HOLDING IT. Asserting the
        # blocker by name matters: if some other row were the holder this test
        # would pass for the wrong reason and prove nothing about reservations.
        why = await c.fetchval("SELECT bettor_funded_group_blocker($1)", gid)
        assert "reservation" in (why or ""), why

        # NOW THE RESERVATION RESOLVES. No intent row changes.
        await c.execute(
            "UPDATE bettor_funded_leg_reservations "
            "   SET state='RELEASED', resolved_at=now(), "
            "       resolution='THE_VENUE_CONFIRMED_NOTHING_WAS_PLACED' "
            " WHERE reservation_id=$1", PFX + "res1")
        assert gid not in await _open_groups(c), (
            "the last reservation resolved and nothing released the group")
        assert await _closure(c, gid) == "NEVER_HELD_ANY_INVENTORY", (
            await _closure(c, gid))
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_closure_written_by_hand_is_still_refused_when_unearned():
    """THE BACKSTOP IS STILL ARMED. The release declines rather than raising,
    so a caller is never poisoned -- but a closure written by any OTHER route
    (hand SQL, a repair script, a future caller) must still be refused. The
    trigger and the release consult the SAME predicate, so they cannot drift.
    """
    c = await _conn()
    try:
        await _ready(c)
        prim = await _entry(c, PFX + "b1", "aec-den-ml")
        gid = prim["portfolio_group_id"]
        await _fill(c, PFX + "b1", 10, "ENTRY")
        with pytest.raises(Exception) as caught:
            await c.execute(
                "UPDATE bettor_funded_portfolio_groups "
                "   SET closed_at = now(), closure = 'ALL_LEGS_EXITED' "
                " WHERE group_id = $1", gid)
        assert "still holds" in str(caught.value), caught.value
        assert gid in await _open_groups(c)
    finally:
        await _clean(c)
        await c.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · AN UNAVAILABLE DATABASE IS NOT AN ABSENT MIGRATION
# ═════════════════════════════════════════════════════════════════════

class _Catalogue:
    """A connection stub for the probe's catalogue reads, and nothing else.

    It answers the two queries the probe makes -- one over
    `information_schema.tables`, one over `information_schema.columns` -- so a
    test can present an outage, a half-applied schema or a complete one without
    needing a second database.
    """

    def __init__(self, *, raise_with=None, tables=(), columns=()):
        self._raise_with = raise_with
        self._tables = set(tables)
        self._columns = {tuple(x) for x in columns}
        self.calls = 0

    async def fetchval(self, sql, *args):
        self.calls += 1
        if self._raise_with is not None:
            raise self._raise_with
        if "information_schema.tables" in sql:
            return int(args[0] in self._tables)
        if "information_schema.columns" in sql:
            return int((args[0], args[1]) in self._columns)
        raise AssertionError("the probe asked something unexpected: %s" % sql)


async def test_a_transient_catalogue_error_is_not_read_as_an_absent_migration():
    """AN OUTAGE MUST NOT LATCH AS A SCHEMA VERDICT.

    THE DEFECT. A process-wide boolean was set from the first probe. If that
    probe hit a transient catalogue error the process concluded "131 is absent"
    and every later entry took the ungrouped path -- writing rows the schema
    would refuse -- for the life of the process, with no retry and no signal.
    `SchemaUnavailable` is now raised, the cache is POSITIVE-ONLY, and the
    caller refuses by name rather than guessing a path.
    """
    from sportsassets import bettor_funded_book as FB
    FB._forget_schema_probe()
    try:
        stub = _Catalogue(raise_with=RuntimeError("the catalogue read failed"))
        with pytest.raises(FB.SchemaUnavailable) as caught:
            await FB._schema_has_groups(stub)
        assert "UNKNOWN" in str(caught.value), caught.value
        # AND NOTHING WAS CACHED, so the next call retries rather than
        # inheriting the outage.
        healthy = _Catalogue(
            tables={"bettor_funded_portfolio_groups"},
            columns={("bettor_funded_intents", "portfolio_group_id"),
                     ("bettor_funded_intents", "leg_role")})
        assert await FB._schema_has_groups(healthy) is True
        assert healthy.calls == 3
    finally:
        FB._forget_schema_probe()


async def test_a_half_applied_migration_is_unavailable_not_present():
    """THE TABLE WITHOUT ITS COLUMNS IS NOT 131.

    A migration interrupted partway can leave `bettor_funded_portfolio_groups`
    present while `portfolio_group_id` and `leg_role` are still missing from the
    intents table. Reading that as "131 is applied" sends every entry down the
    grouped path and each one fails on an absent column; reading it as absent
    writes open entries the CHECK will refuse. Neither is safe, so it is refused
    as UNAVAILABLE -- which is what it is.
    """
    from sportsassets import bettor_funded_book as FB
    FB._forget_schema_probe()
    try:
        stub = _Catalogue(tables={"bettor_funded_portfolio_groups"})
        with pytest.raises(FB.SchemaUnavailable) as caught:
            await FB._schema_has_groups(stub)
        assert "PARTIALLY" in str(caught.value), caught.value
    finally:
        FB._forget_schema_probe()


async def test_an_absent_migration_is_absent_and_is_not_cached():
    """A CLEAN 'NO' IS A 'NO', and a later 'yes' is picked up.

    The two answers have to be distinguishable from the outage above: this one
    returns False rather than raising, and because only a YES is cached, a
    process that started before 131 was applied sees it once it is there --
    without a restart.
    """
    from sportsassets import bettor_funded_book as FB
    FB._forget_schema_probe()
    try:
        assert await FB._schema_has_groups(_Catalogue()) is False
        full = _Catalogue(
            tables={"bettor_funded_portfolio_groups"},
            columns={("bettor_funded_intents", "portfolio_group_id"),
                     ("bettor_funded_intents", "leg_role")})
        assert await FB._schema_has_groups(full) is True
        # NOW IT IS CACHED, so a later transient outage cannot un-know it.
        assert await FB._schema_has_groups(
            _Catalogue(raise_with=RuntimeError("gone"))) is True
    finally:
        FB._forget_schema_probe()


@pg
async def test_the_probe_reports_this_database_and_only_caches_a_yes():
    """AGAINST THE REAL DATABASE, and the cache is positive-only.

    A negative answer is NOT remembered, so a process that started before the
    migration was applied picks it up rather than refusing entries until it is
    restarted.
    """
    c = await _conn()
    try:
        from sportsassets import bettor_funded_book as FB
        FB._forget_schema_probe()
        expected = await _has_131(c)
        assert await FB._schema_has_groups(c) is expected
        # A SECOND CALL AGREES.
        assert await FB._schema_has_groups(c) is expected
        if not expected:
            # NOTHING WAS LATCHED: forgetting changes nothing, because a "no"
            # was never stored in the first place.
            FB._forget_schema_probe()
            assert await FB._schema_has_groups(c) is False
    finally:
        await c.close()
