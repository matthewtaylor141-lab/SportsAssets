"""THE ACQUISITION PATH FROM RESERVATION TO SUBMISSION, and every way it breaks.

WHY THIS FILE EXISTS. Migration 131 shipped `bettor_funded_leg_reservations`
with six states, a live-per-leg bound and an operation identity -- and NOTHING IN
THE APPLICATION EVER TOUCHED IT. A grep for the table name across
`sportsassets/` returned no callers. So the states were never entered, the bound
was never contended, and the idempotency the `operation_id` index provides was
never exercised. An independent review asked for the machine to be finished and
for the identity to be carried through reservation, committed intent, send
attempt, acknowledgement, ambiguity and recovery. This is the proof of that path.

WHAT IS ASSERTED, and why each one is a way real money is lost:

  1 IDENTITY. A replay of the same operation reaches the SAME row in whatever
    state it has reached -- it does not acquire twice. A legitimate top-up is a
    different operation and proceeds. Neither is a special case of the other.
  2 TRANSITIONS. `SEND_ATTEMPTED -> RELEASED` does not exist, in Python OR in
    the database. Once a request may have left, nothing may declare that it did
    not; the routes out are the venue naming the order, or admitting we do not
    know. A machine that can jump from HELD to CONSUMED claims an order exists
    with no send ever made.
  3 IMMUTABILITY. Quantity, price, contract, role, group and the bound intent
    cannot be edited after commitment. Otherwise one operation identity stands
    for two economic actions, which is the thing it exists to prevent.
  4 EXPOSURE ONCE. A reservation counts as committed capital only while HELD.
    From COMMITTED on, the intent row exists and the existing headroom check
    counts it; counting both doubles every in-flight acquisition exactly when
    the system is deciding whether it can afford the next one.
  5 INTERRUPTION. Before send, after send, a lost acknowledgement, a restart, a
    duplicate delivery -- each is driven through and the end state asserted.

NOTHING HERE SENDS ANYTHING, and the module under test imports no venue client.
"""

from __future__ import annotations

import asyncio
import os

import pytest

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

FIXTURE = "nhl-tor-mtl-2026-12-05"
ACCT = "acct-131r"
PFX = "t131r-"
SLUG_A = "aec-tor-ml"
SLUG_B = "aec-mtl-ml"

CAPACITY_MUST_BE_FREE = os.environ.get(
    "RN1X_CAPACITY_SLOT_MUST_BE_FREE", "") not in ("", "0", "false", "FALSE")


async def _conn():
    import asyncpg
    c = await asyncpg.connect(DSN)
    await c.execute("SET lock_timeout = 4000")
    return c


async def _has_evidence(c) -> bool:
    return bool(await c.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        " WHERE table_schema='public' "
        "   AND table_name='bettor_funded_operation_evidence'"))


async def _has_131(c) -> bool:
    return bool(await c.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        " WHERE table_schema='public' "
        "   AND table_name='bettor_funded_leg_reservations'"))


async def _clean(c):
    if await _has_evidence(c):
        await c.execute("DELETE FROM bettor_funded_operation_evidence "
                        "WHERE operation_id LIKE $1", PFX + "%")
    if await _has_131(c):
        await c.execute("DELETE FROM bettor_funded_leg_reservations "
                        "WHERE operation_id LIKE $1 OR group_id LIKE $1",
                        PFX + "%")
    # ORDER MATTERS: `bettor_funded_economics` and `bettor_funded_discrepancies`
    # both reference the intent, so deleting intents first raises a foreign-key
    # violation from `finally` and reports a FAILURE for a test whose assertions
    # all passed.
    await c.execute("DELETE FROM bettor_funded_economics "
                    "WHERE intent_id LIKE $1", PFX + "%")
    await c.execute("DELETE FROM bettor_funded_discrepancies "
                    "WHERE intent_id LIKE $1", PFX + "%")
    await c.execute("DELETE FROM bettor_funded_fills WHERE intent_id LIKE $1",
                    PFX + "%")
    await c.execute("DELETE FROM bettor_funded_intents WHERE intent_id LIKE $1",
                    PFX + "%")
    if await _has_131(c):
        await c.execute("DELETE FROM bettor_funded_portfolio_groups "
                        "WHERE group_id LIKE $1", PFX + "%")


async def _ready(c):
    if not await _has_131(c):
        pytest.skip("migration 131 not applied to this database")
    await _clean(c)
    holder = await c.fetchval(
        "SELECT group_id FROM bettor_funded_portfolio_groups "
        " WHERE closed_at IS NULL AND group_id NOT LIKE $1 LIMIT 1", PFX + "%")
    if not holder:
        return
    msg = ("group %r holds the one open-group slot; this file needs it free and "
           "will not delete another owner's row" % holder)
    if CAPACITY_MUST_BE_FREE:
        pytest.fail(msg + ". RN1X_CAPACITY_SLOT_MUST_BE_FREE is set, so this is "
                          "an ENVIRONMENT FAILURE, not a pass")
    pytest.skip(msg)


async def _group(c, gid, *, structure="INDIRECT_MIDDLE"):
    await c.execute(
        "INSERT INTO bettor_funded_portfolio_groups "
        "(group_id, account_id, venue, event_key, structure) "
        "VALUES ($1,$2,'PMUS',$3,$4)", gid, ACCT, FIXTURE, structure)


async def _intent(c, iid, gid, role, slug, *, qty=10, state="INTENT_RECORDED",
                  residual=0.0):
    await c.execute(
        "INSERT INTO bettor_funded_intents (intent_id, account_id, venue,"
        " venue_class, us_market_slug, event_key, order_intent, limit_price,"
        " quantity, collateral_usd, effective_digest, state, kind,"
        " residual_qty, portfolio_group_id, leg_role) VALUES "
        "($1,$2,'PMUS','US',$3,$4,'ORDER_INTENT_BUY_LONG',0.5,$5,$6,$7,$8,"
        " 'ENTRY',$9,$10,$11)",
        iid, ACCT, slug, FIXTURE, int(qty), float(qty) * 0.5, iid + "-d", state,
        float(residual), gid, role)


async def _named(c, op, intent_id, slug, vo, *, at=1790000100.0):
    """Record NAMED evidence for `op`, so `record_the_venue_named_it` can consume.

    THE TESTS HAD TO CHANGE, AND THAT IS THE POINT. Consuming a reservation now
    requires durable evidence bound to the operation's account, instrument and
    intent -- so a test that previously just asserted CONSUMED must now say where
    the venue's answer came from, exactly as the production path must.
    """
    from sportsassets import bettor_funded_reservations as RS
    got = await RS.record_venue_evidence(
        c, evidence_id="ev:%s" % op, operation_id=op, account_id=ACCT,
        venue="PMUS", us_market_slug=slug, intent_id=intent_id,
        kind=RS.EV_NAMED, venue_order_id=vo,
        search_endpoint="GET /v1/orders/%s" % vo, read_at=at,
        covered_terminal_orders=True, results_returned=1,
        raw={"id": vo, "status": "open"})
    assert got["ok"] is True, got
    return got


def _why(exc) -> str:
    return "%s: %s" % (type(exc).__name__, " ".join(str(exc).split())[:200])


# ═════════════════════════════════════════════════════════════════════
# 0 · THE MACHINE IS DECLARED IN ONE PLACE AND ENFORCED IN THE OTHER
# ═════════════════════════════════════════════════════════════════════

def test_the_declared_machine_has_no_release_after_a_send_was_attempted():
    """THE ONE TRANSITION WHOSE ABSENCE IS THE POINT.

    A release from SEND_ATTEMPTED would let the system decide, with no evidence,
    that a request which may already have reached the venue did not. That is how
    a real order becomes an untracked position.
    """
    from sportsassets import bettor_funded_reservations as RS
    assert RS.RELEASED not in RS.TRANSITIONS[RS.SEND_ATTEMPTED]
    assert set(RS.TRANSITIONS[RS.SEND_ATTEMPTED]) == {RS.CONSUMED, RS.AMBIGUOUS}
    # AND THE TERMINAL STATES ARE TERMINAL.
    assert RS.TRANSITIONS[RS.CONSUMED] == ()
    assert RS.TRANSITIONS[RS.RELEASED] == ()
    # AN UNACKNOWLEDGED SEND IS LIVE, so it still claims the leg.
    assert RS.SEND_ATTEMPTED in RS.LIVE_STATES
    assert RS.AMBIGUOUS in RS.LIVE_STATES
    # AND ONLY HELD COUNTS AS COMMITTED CAPITAL.
    assert RS.STATES_THAT_COUNT_AS_COMMITTED_CAPITAL == (RS.HELD,)


def test_the_module_cannot_reach_a_venue_client():
    """STRUCTURAL. This module writes rows about sending; it must not be able to
    send. Asserted on its source, because an import added later would otherwise
    pass unnoticed."""
    import pathlib
    from sportsassets import bettor_funded_reservations as RS
    src = pathlib.Path(RS.__file__).read_text()
    for forbidden in ("import httpx", "from . import pmus", "polymarket",
                      "submit_fok", "close_position"):
        assert forbidden not in src, (
            "%r appears in the reservation module, which must not be able to "
            "submit anything" % forbidden)


@pg
async def test_the_database_enforces_the_same_machine_the_module_declares():
    """THE TWO MUST AGREE. The module's map is what refuses a caller by name;
    the database trigger is what refuses everything else, including hand SQL.
    Every state pair is attempted directly against the database, and the
    outcomes are compared to the declared map -- so a drift between them is a
    failure rather than a latent disagreement."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g0")
        await _intent(c, PFX + "i0", PFX + "g0", "PRIMARY", SLUG_A)
        disagreements = []
        for frm in RS.STATES:
            for to in RS.STATES:
                if frm == to:
                    continue
                await c.execute(
                    "DELETE FROM bettor_funded_leg_reservations "
                    " WHERE operation_id LIKE $1", PFX + "%")
                await c.execute(
                    "INSERT INTO bettor_funded_leg_reservations "
                    "(reservation_id, group_id, leg_role, us_market_slug,"
                    " quantity, collateral_usd, limit_price, state,"
                    " operation_id, intent_id, resolved_at, resolution) "
                    "VALUES ($1,$2,'PRIMARY',$3,10,5.0,0.5,$4,$5,$6,"
                    "        CASE WHEN $4 IN ('CONSUMED','RELEASED') "
                    "             THEN now() ELSE NULL END,"
                    "        CASE WHEN $4 IN ('CONSUMED','RELEASED') "
                    "             THEN 'seed' ELSE NULL END)",
                    PFX + "r0", PFX + "g0", SLUG_A, frm, PFX + "op0",
                    None if frm in ("HELD", "RELEASED") else PFX + "i0")
                db_allowed = True
                try:
                    # THE INTENT IS SUPPLIED WHERE THE TARGET STATE REQUIRES
                    # ONE. `bettor_funded_reservation_committed_has_intent_ck`
                    # demands it from COMMITTED onward, and an UPDATE that left
                    # it NULL was refused -- which this test first read as the
                    # database disagreeing about HELD -> COMMITTED. The database
                    # was right and the probe was incomplete: a reservation
                    # cannot be committed to nothing.
                    await c.execute(
                        "UPDATE bettor_funded_leg_reservations "
                        "   SET state=$2,"
                        "       intent_id = COALESCE(intent_id, CASE WHEN $2 IN"
                        "           ('HELD','RELEASED') THEN NULL ELSE $3 END),"
                        "       resolved_at = CASE WHEN $2 IN ('CONSUMED',"
                        "           'RELEASED') THEN now() ELSE NULL END,"
                        "       resolution = CASE WHEN $2 IN ('CONSUMED',"
                        "           'RELEASED') THEN 'x' ELSE NULL END"
                        " WHERE operation_id=$1",
                        PFX + "op0", to, PFX + "i0")
                except Exception:                           # noqa: BLE001
                    db_allowed = False
                declared = to in RS.TRANSITIONS[frm]
                if db_allowed != declared:
                    disagreements.append((frm, to, "db=%s declared=%s"
                                          % (db_allowed, declared)))
        assert not disagreements, (
            "the database and the declared machine disagree on: %r"
            % disagreements)
    finally:
        await _clean(c)
        await c.close()


# ═════════════════════════════════════════════════════════════════════
# 1 · IDENTITY: REPLAY, DUPLICATE DELIVERY, AND A LEGITIMATE TOP-UP
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_replaying_the_same_operation_does_not_acquire_twice():
    """THE RETRY CASE. A caller whose first call timed out calls again with the
    same operation identity. It must reach the SAME row -- not a second
    reservation, and not an error it has to special-case."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g1")
        first = await RS.hold(c, operation_id=PFX + "op1", group_id=PFX + "g1",
                              leg_role="PRIMARY", us_market_slug=SLUG_A,
                              quantity=10, limit_price=0.5,
                              collateral_usd=5.0)
        assert first["ok"] is True and first["already"] is False, first
        again = await RS.hold(c, operation_id=PFX + "op1", group_id=PFX + "g1",
                              leg_role="PRIMARY", us_market_slug=SLUG_A,
                              quantity=10, limit_price=0.5,
                              collateral_usd=5.0)
        assert again["ok"] is True and again["already"] is True, again
        assert again["reservation"]["reservation_id"] == \
            first["reservation"]["reservation_id"]
        assert await c.fetchval(
            "SELECT count(*) FROM bettor_funded_leg_reservations "
            " WHERE group_id=$1", PFX + "g1") == 1
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_replay_after_the_first_attempt_resolved_is_still_the_same_one():
    """THE CASE THE LIVE-ONLY BOUND CANNOT CATCH. A HELD-uniqueness rule stops
    two simultaneous reservations. It does nothing about the same acquisition
    being replayed after the first became CONSUMED -- which is precisely the
    replay a restarted worker performs. The operation identity is unique across
    the whole table, forever, so the replay is recognised."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g2")
        await _intent(c, PFX + "i2", PFX + "g2", "PRIMARY", SLUG_A)
        op = PFX + "op2"
        await RS.hold(c, operation_id=op, group_id=PFX + "g2",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        await RS.commit_to_intent(c, operation_id=op, intent_id=PFX + "i2")
        await RS.mark_send_attempted(c, operation_id=op)
        await _named(c, op, PFX + "i2", SLUG_A, "vo-2")
        await RS.record_the_venue_named_it(c, operation_id=op,
                                          venue_order_id="vo-2")
        assert (await RS.get(c, op))["reservation"]["state"] == RS.CONSUMED

        replay = await RS.hold(c, operation_id=op, group_id=PFX + "g2",
                               leg_role="PRIMARY", us_market_slug=SLUG_A,
                               quantity=10, limit_price=0.5,
                               collateral_usd=5.0)
        assert replay["ok"] is True and replay["already"] is True, replay
        assert replay["reservation"]["state"] == RS.CONSUMED
        assert await c.fetchval(
            "SELECT count(*) FROM bettor_funded_leg_reservations "
            " WHERE group_id=$1", PFX + "g2") == 1, (
            "the replay created a SECOND reservation for a completed "
            "acquisition")
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_an_identical_retry_of_every_step_returns_the_existing_result():
    """THE HALF THAT MUST KEEP WORKING. A conflict check that also refused
    identical retries would break the retry-safety the identity exists for, so
    both halves are pinned: same request -> same row, ok=True, already=True."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "gid1")
        await _intent(c, PFX + "iid1", PFX + "gid1", "PRIMARY", SLUG_A)
        op = PFX + "opid1"
        terms = dict(operation_id=op, group_id=PFX + "gid1", leg_role="PRIMARY",
                     us_market_slug=SLUG_A, quantity=10, limit_price=0.5,
                     collateral_usd=5.0)
        first = await RS.hold(c, **terms)
        assert first["ok"] is True and first["already"] is False, first
        # BYTE-FOR-BYTE THE SAME REQUEST, TWICE MORE.
        for _ in range(2):
            again = await RS.hold(c, **terms)
            assert again["ok"] is True, again
            assert again["already"] is True, again
            assert again["reservation"]["reservation_id"] == \
                first["reservation"]["reservation_id"]
        # AND THE SAME FOR THE BOUND STEP.
        a = await RS.commit_to_intent(c, operation_id=op,
                                      intent_id=PFX + "iid1")
        b = await RS.commit_to_intent(c, operation_id=op,
                                      intent_id=PFX + "iid1")
        assert a["ok"] is True and b["ok"] is True, (a, b)
        assert b["already"] is True
        assert b["exposure_is_now_counted_on"] == PFX + "iid1"
        assert await c.fetchval(
            "SELECT count(*) FROM bettor_funded_leg_reservations "
            " WHERE group_id=$1", PFX + "gid1") == 1
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_committing_one_identity_to_a_second_intent_is_refused():
    """THE COUNTEREXAMPLE AN INDEPENDENT REVIEW REPRODUCED.

    `op-1` is COMMITTED to intent-A. `commit_to_intent(op-1, intent-B)` returned
    ok=True/already=True -- because the STATE already equalled COMMITTED -- and
    then reported `exposure_is_now_counted_on: intent-B` while the stored row
    still named intent-A. The caller read its own argument back and believed the
    binding had moved.

    MATCHING THE TARGET STATE IS NOT BEING THE SAME REQUEST. The refusal names
    the field, what is stored and what was asked for, so a caller can tell a
    retry from a mistake.
    """
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "gid2")
        await _intent(c, PFX + "iA", PFX + "gid2", "PRIMARY", SLUG_A)
        await _intent(c, PFX + "iB", PFX + "gid2", "HEDGE", SLUG_B)
        op = PFX + "opid2"
        await RS.hold(c, operation_id=op, group_id=PFX + "gid2",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        await RS.commit_to_intent(c, operation_id=op, intent_id=PFX + "iA")

        bad = await RS.commit_to_intent(c, operation_id=op, intent_id=PFX + "iB")
        assert bad["ok"] is False, bad
        assert bad["refusal"] == RS.R_IDENTITY_REUSED_WITH_DIFFERENT_TERMS, bad
        assert bad.get("exposure_is_now_counted_on") is None, (
            "a refused call must not report an exposure binding at all")
        clash = {k["field"]: k for k in bad["conflicts"]}
        assert clash["intent_id"]["stored"] == PFX + "iA"
        assert clash["intent_id"]["requested"] == PFX + "iB"
        # AND THE DATABASE STILL NAMES THE FIRST INTENT.
        assert await c.fetchval(
            "SELECT intent_id FROM bettor_funded_leg_reservations "
            " WHERE operation_id=$1", op) == PFX + "iA"
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_holding_one_identity_on_different_terms_is_refused_field_by_field():
    """THE SAME HOLE IN `hold()`. Replaying an identity with a different role,
    instrument, quantity, price and collateral was accepted as a retry and the
    caller was handed the FIRST action's row -- while believing its own terms had
    been taken. Every fixed field is exercised separately, because a check that
    compared only one of them would pass a test that changed them all."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "gid3")
        op = PFX + "opid3"
        base = dict(operation_id=op, group_id=PFX + "gid3", leg_role="PRIMARY",
                    us_market_slug=SLUG_A, quantity=10, limit_price=0.5,
                    collateral_usd=5.0)
        assert (await RS.hold(c, **base))["ok"] is True
        for field, value in (("leg_role", "HEDGE"),
                             ("us_market_slug", SLUG_B),
                             ("quantity", 999),
                             ("limit_price", 0.9),
                             ("collateral_usd", 900.0)):
            bad = await RS.hold(c, **dict(base, **{field: value}))
            assert bad["ok"] is False, (field, bad)
            assert bad["refusal"] == \
                RS.R_IDENTITY_REUSED_WITH_DIFFERENT_TERMS, (field, bad)
            assert [k["field"] for k in bad["conflicts"]] == [field], (
                "the refusal must name exactly the field that differs: %r"
                % bad["conflicts"])
        # NOTHING WAS MUTATED BY ANY OF THOSE REFUSALS.
        row = dict(await c.fetchrow(
            "SELECT leg_role, us_market_slug, quantity::float8 q, "
            "       limit_price::float8 p, collateral_usd::float8 col "
            "  FROM bettor_funded_leg_reservations WHERE operation_id=$1", op))
        assert row == {"leg_role": "PRIMARY", "us_market_slug": SLUG_A,
                       "q": 10.0, "p": 0.5, "col": 5.0}
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_group_change_under_one_identity_is_refused_too():
    """A DIFFERENT GROUP IS A DIFFERENT POSITION. Reusing the identity across
    groups would attach one acquisition attempt to two portfolios."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "gid4")
        op = PFX + "opid4"
        assert (await RS.hold(c, operation_id=op, group_id=PFX + "gid4",
                              leg_role="PRIMARY", us_market_slug=SLUG_A,
                              quantity=10, limit_price=0.5,
                              collateral_usd=5.0))["ok"] is True
        bad = await RS.hold(c, operation_id=op, group_id=PFX + "gid-other",
                            leg_role="PRIMARY", us_market_slug=SLUG_A,
                            quantity=10, limit_price=0.5, collateral_usd=5.0)
        assert bad["ok"] is False
        assert bad["refusal"] == RS.R_IDENTITY_REUSED_WITH_DIFFERENT_TERMS, bad
        assert [k["field"] for k in bad["conflicts"]] == ["group_id"]
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_legitimate_top_up_is_a_new_operation_and_is_not_blocked():
    """IDEMPOTENCY MUST NOT BECOME A CEILING. Deciding to add to a position is a
    different economic action, so it carries a new identity and must proceed once
    the earlier reservation has resolved. A rule that blocked it would make the
    first acquisition on a leg the only one ever possible."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g3")
        await _intent(c, PFX + "i3", PFX + "g3", "PRIMARY", SLUG_A)
        await RS.hold(c, operation_id=PFX + "op3a", group_id=PFX + "g3",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        await RS.commit_to_intent(c, operation_id=PFX + "op3a",
                                  intent_id=PFX + "i3")
        await RS.mark_send_attempted(c, operation_id=PFX + "op3a")
        await _named(c, PFX + "op3a", PFX + "i3", SLUG_A, "vo-3a")
        await RS.record_the_venue_named_it(c, operation_id=PFX + "op3a",
                                          venue_order_id="vo-3a")

        top_up = await RS.hold(c, operation_id=PFX + "op3b",
                               group_id=PFX + "g3", leg_role="PRIMARY",
                               us_market_slug=SLUG_A, quantity=4,
                               limit_price=0.55, collateral_usd=2.2)
        assert top_up["ok"] is True and top_up["already"] is False, top_up
        assert top_up["reservation"]["quantity"] == pytest.approx(4.0)
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_second_live_operation_on_the_same_leg_is_refused_by_name():
    """CONTENTION IS NOT A REPLAY, and the two are reported differently. A
    different operation arriving while one is still live is refused with the
    holder named, so a caller can see what is in its way."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g4")
        await RS.hold(c, operation_id=PFX + "op4a", group_id=PFX + "g4",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        second = await RS.hold(c, operation_id=PFX + "op4b",
                               group_id=PFX + "g4", leg_role="PRIMARY",
                               us_market_slug=SLUG_B, quantity=10,
                               limit_price=0.5, collateral_usd=5.0)
        assert second["ok"] is False
        assert second["refusal"] == RS.R_LEG_ALREADY_RESERVED, second
        assert second["held_by"]["operation_id"] == PFX + "op4a"
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_two_connections_racing_the_same_leg_produce_one_reservation():
    """AND UNDER REAL CONCURRENCY. The refusal above is a read-then-insert away
    from being decorative; the unique index is what makes it true when two
    workers decide at the same instant."""
    from sportsassets import bettor_funded_reservations as RS
    a = await _conn()
    b = await _conn()
    try:
        await _ready(a)
        await _group(a, PFX + "g5")
        results = await asyncio.gather(
            RS.hold(a, operation_id=PFX + "op5a", group_id=PFX + "g5",
                    leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                    limit_price=0.5, collateral_usd=5.0),
            RS.hold(b, operation_id=PFX + "op5b", group_id=PFX + "g5",
                    leg_role="PRIMARY", us_market_slug=SLUG_B, quantity=10,
                    limit_price=0.5, collateral_usd=5.0),
            return_exceptions=True)
        oks = [r for r in results
               if isinstance(r, dict) and r.get("ok") and not r.get("already")]
        assert len(oks) == 1, results
        assert await a.fetchval(
            "SELECT count(*) FROM bettor_funded_leg_reservations "
            " WHERE group_id=$1 AND state = ANY($2::text[])",
            PFX + "g5", list(RS.LIVE_STATES)) == 1
    finally:
        await _clean(a)
        await a.close()
        await b.close()


# ═════════════════════════════════════════════════════════════════════
# 2 · IMMUTABILITY AFTER COMMITMENT
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_committed_reservation_cannot_be_re_pointed_at_another_intent():
    """ONE OPERATION IDENTITY, ONE ORDER. Re-pointing it would let a single
    recorded acquisition stand for two real ones, and the ledger would name the
    wrong order as the thing that was placed."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g6")
        await _intent(c, PFX + "i6a", PFX + "g6", "PRIMARY", SLUG_A)
        await RS.hold(c, operation_id=PFX + "op6", group_id=PFX + "g6",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        await RS.commit_to_intent(c, operation_id=PFX + "op6",
                                  intent_id=PFX + "i6a")
        with pytest.raises(Exception) as caught:
            await c.execute(
                "UPDATE bettor_funded_leg_reservations SET intent_id=$2 "
                " WHERE operation_id=$1", PFX + "op6", PFX + "i6b")
        assert "already committed to intent" in str(caught.value) \
            or "does not exist" in str(caught.value), _why(caught.value)
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_the_reserved_quantity_price_and_contract_are_fixed():
    """A DIFFERENT SIZE IS A DIFFERENT ACQUISITION. Editing the quantity on an
    existing row would let one operation identity cover both, which is exactly
    what the identity exists to prevent -- and the collateral already counted
    against capital would no longer match what was reserved."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g7")
        await RS.hold(c, operation_id=PFX + "op7", group_id=PFX + "g7",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        for col, val in (("quantity", 25), ("limit_price", 0.9),
                         ("collateral_usd", 99), ("us_market_slug", SLUG_B),
                         ("leg_role", "HEDGE"), ("operation_id", PFX + "other")):
            with pytest.raises(Exception) as caught:
                await c.execute(
                    "UPDATE bettor_funded_leg_reservations SET %s=$2 "
                    " WHERE operation_id=$1" % col, PFX + "op7", val)
            assert "identity is fixed" in str(caught.value), (
                "%s was editable: %s" % (col, _why(caught.value)))
        row = await c.fetchrow(
            "SELECT quantity::float8 q, limit_price::float8 p, us_market_slug s "
            "  FROM bettor_funded_leg_reservations WHERE operation_id=$1",
            PFX + "op7")
        assert (row["q"], row["p"], row["s"]) == (10.0, 0.5, SLUG_A)
    finally:
        await _clean(c)
        await c.close()


# ═════════════════════════════════════════════════════════════════════
# 3 · EXPOSURE IS COUNTED ONCE
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_committed_capital_counts_the_reservation_or_the_intent_never_both():
    """THE DOUBLE-COUNT THAT WOULD SPEND MONEY TWICE -- or refuse to spend it at
    all.

    While HELD there is no intent row, so the reservation is the only record of
    the commitment and it must be counted. The moment it is COMMITTED an intent
    exists in a state `bettor_funded_order_is_outstanding` covers, and the
    existing headroom check counts that. Counting both inflates committed capital
    by the full size of every in-flight acquisition, at the exact moment the
    system is deciding whether it can afford another.
    """
    from sportsassets import bettor_funded_book as FB
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g8")
        await RS.hold(c, operation_id=PFX + "op8", group_id=PFX + "g8",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        held = await RS.reserved_collateral_usd(c, account_id=ACCT)
        assert held["reserved_usd"] == pytest.approx(5.0), held
        assert held["live_but_not_counted"] == []

        await _intent(c, PFX + "i8", PFX + "g8", "PRIMARY", SLUG_A)
        got = await RS.commit_to_intent(c, operation_id=PFX + "op8",
                                        intent_id=PFX + "i8")
        assert got["ok"] is True, got
        assert got["counts_as_committed_capital"] is False

        after = await RS.reserved_collateral_usd(c, account_id=ACCT)
        assert after["reserved_usd"] == pytest.approx(0.0), (
            "the reservation is still counted after an intent row exists; the "
            "same collateral is now counted twice: %r" % after)
        assert len(after["live_but_not_counted"]) == 1
        assert after["live_but_not_counted"][0][
            "counted_on_the_intent_instead"] == PFX + "i8"
        # AND THE INTENT IS WHAT THE EXISTING CHECK NOW SEES.
        assert PFX + "i8" in [r["intent_id"]
                              for r in await FB.open_entry_positions(c)]
        # THE RESERVATION IS STILL LIVE, though -- it still claims the leg.
        assert [r["operation_id"] for r in await RS.live(c, account_id=ACCT)] \
            == [PFX + "op8"]
    finally:
        await _clean(c)
        await c.close()


# ═════════════════════════════════════════════════════════════════════
# 4 · INTERRUPTION, LOST ANSWERS, RESTART AND RECOVERY
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_an_interruption_before_the_send_releases_the_leg():
    """NOTHING LEFT THIS PROCESS, so the leg goes back and the release says so.
    This is the only kind of release that is a statement about us rather than a
    claim about the venue."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "g9")
        await _intent(c, PFX + "i9", PFX + "g9", "PRIMARY", SLUG_A)
        await RS.hold(c, operation_id=PFX + "op9", group_id=PFX + "g9",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        await RS.commit_to_intent(c, operation_id=PFX + "op9",
                                  intent_id=PFX + "i9")
        got = await RS.release(c, operation_id=PFX + "op9",
                               why=RS.WHY_NEVER_SENT)
        assert got["ok"] is True, got
        assert got["reservation"]["state"] == RS.RELEASED
        assert got["reservation"]["resolution"] == RS.WHY_NEVER_SENT
        assert got["reservation"]["resolved_at"] is not None
        # THE LEG IS FREE AGAIN for a new operation.
        again = await RS.hold(c, operation_id=PFX + "op9b",
                              group_id=PFX + "g9", leg_role="PRIMARY",
                              us_market_slug=SLUG_A, quantity=10,
                              limit_price=0.5, collateral_usd=5.0)
        assert again["ok"] is True and again["already"] is False, again
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_an_interruption_after_the_send_cannot_be_released():
    """THE REFUSAL THAT PROTECTS A REAL ORDER.

    After the request may have left, a caller asking to release must be refused
    -- and refused by name, so the correct route is discoverable. The leg stays
    claimed, because the order may exist.
    """
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "ga")
        await _intent(c, PFX + "ia", PFX + "ga", "PRIMARY", SLUG_A,
                      state="SEND_ATTEMPTED")
        op = PFX + "opa"
        await RS.hold(c, operation_id=op, group_id=PFX + "ga",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        await RS.commit_to_intent(c, operation_id=op, intent_id=PFX + "ia")
        await RS.mark_send_attempted(c, operation_id=op)

        refused = await RS.release(c, operation_id=op, why="I assume it failed")
        assert refused["ok"] is False
        assert refused["refusal"] == RS.R_ILLEGAL_TRANSITION, refused
        assert set(refused["legal_from_here"]) == {RS.CONSUMED, RS.AMBIGUOUS}
        assert (await RS.get(c, op))["reservation"]["state"] == \
            RS.SEND_ATTEMPTED
        # AND THE LEG IS STILL CLAIMED, so no second attempt is admitted.
        blocked = await RS.hold(c, operation_id=PFX + "opa2",
                                group_id=PFX + "ga", leg_role="PRIMARY",
                                us_market_slug=SLUG_A, quantity=10,
                                limit_price=0.5, collateral_usd=5.0)
        assert blocked["ok"] is False
        assert blocked["refusal"] == RS.R_LEG_ALREADY_RESERVED
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_lost_acknowledgement_becomes_ambiguous_and_stays_exposure():
    """WE DO NOT KNOW, AND THAT IS A STATE. AMBIGUOUS is not terminal and not
    released: it keeps claiming the leg and keeps the group open, because an
    order that may exist is exposure."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "gb", structure="SINGLE_LEG")
        await _intent(c, PFX + "ib", PFX + "gb", "PRIMARY", SLUG_A,
                      state="UNRESOLVED")
        op = PFX + "opb"
        await RS.hold(c, operation_id=op, group_id=PFX + "gb",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        await RS.commit_to_intent(c, operation_id=op, intent_id=PFX + "ib")
        await RS.mark_send_attempted(c, operation_id=op)
        amb = await RS.mark_ambiguous(
            c, operation_id=op, why="the answer never arrived")
        assert amb["ok"] is True, amb
        assert amb["exposure"] == "PRESERVED"
        assert amb["reservation"]["resolved_at"] is None
        assert amb["reservation"]["state"] in RS.LIVE_STATES

        # AND THE GROUP CANNOT BE RELEASED while it is unresolved.
        assert await c.fetchval(
            "SELECT bettor_funded_group_release($1)", PFX + "gb") is None
        blocker = await c.fetchval("SELECT bettor_funded_group_blocker($1)",
                                   PFX + "gb")
        assert "reservation" in blocker or "outstanding" in blocker, blocker
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_recovery_resolves_an_ambiguity_only_from_recorded_evidence():
    """BOTH ANSWERS, AND NEITHER IS A BOOLEAN.

    `resolve_from_the_venue` used to take `the_venue_has_the_order: bool`, so a
    caller could clear an AMBIGUOUS send -- the state that exists precisely
    because nobody knows whether a live order sits at the venue -- by passing
    False. A parameter named for what the caller is "asserting" is a comment, not
    evidence.
    """
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        if not await _has_evidence(c):
            pytest.skip("migration 133 not applied to this database")
        # (a) THE VENUE NAMES THE ORDER.
        await _group(c, PFX + "gc")
        await _intent(c, PFX + "ic", PFX + "gc", "PRIMARY", SLUG_A)
        op = PFX + "opc"
        await RS.hold(c, operation_id=op, group_id=PFX + "gc",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        await RS.commit_to_intent(c, operation_id=op, intent_id=PFX + "ic")
        await RS.mark_send_attempted(c, operation_id=op)
        await RS.mark_ambiguous(c, operation_id=op, why="no answer")

        # WITH NO EVIDENCE AT ALL, IT STAYS AMBIGUOUS.
        nothing = await RS.resolve_from_the_venue(c, operation_id=op)
        assert nothing["ok"] is False
        assert nothing["refusal"] == RS.R_NO_EVIDENCE, nothing
        assert nothing["exposure"] == "PRESERVED"
        assert (await RS.get(c, op))["reservation"]["state"] == RS.AMBIGUOUS

        wrote = await RS.record_venue_evidence(
            c, evidence_id=PFX + "evc", operation_id=op, account_id=ACCT,
            venue="PMUS", us_market_slug=SLUG_A, intent_id=PFX + "ic",
            kind=RS.EV_NAMED, venue_order_id="vo-c",
            search_endpoint="GET /v1/orders/vo-c", read_at=1790000000.0,
            covered_terminal_orders=True, results_returned=1,
            raw={"status": "filled"})
        assert wrote["ok"] is True, wrote
        yes = await RS.resolve_from_the_venue(c, operation_id=op)
        assert yes["ok"] is True, yes
        assert yes["reservation"]["state"] == RS.CONSUMED
        assert yes["evidence_id"] == PFX + "evc"
        assert RS.WHY_VENUE_NAMED_THE_ORDER in yes["reservation"]["resolution"]
        await _clean(c)

        # (b) AN EMPTY *OPEN ORDERS* SEARCH DOES NOT CLEAR IT.
        await _group(c, PFX + "gd")
        await _intent(c, PFX + "id", PFX + "gd", "PRIMARY", SLUG_A)
        op2 = PFX + "opd"
        await RS.hold(c, operation_id=op2, group_id=PFX + "gd",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        await RS.commit_to_intent(c, operation_id=op2, intent_id=PFX + "id")
        await RS.mark_send_attempted(c, operation_id=op2)
        await RS.mark_ambiguous(c, operation_id=op2, why="no answer")

        # THE DATABASE ITSELF REFUSES TO STORE THAT AS AN ABSENCE CLAIM.
        weak = await RS.record_venue_evidence(
            c, evidence_id=PFX + "evd-open", operation_id=op2, account_id=ACCT,
            venue="PMUS", us_market_slug=SLUG_A, intent_id=PFX + "id",
            kind=RS.EV_NO_SUCH_ORDER,
            search_endpoint="GET /v1/orders?status=open", read_at=1790000001.0,
            covered_terminal_orders=False, results_returned=0)
        assert weak["ok"] is False
        assert weak["refusal"] == \
            RS.R_EVIDENCE_SEARCH_COULD_NOT_HAVE_FOUND_IT, weak
        assert "filled immediately is not open" in weak["why"]

        # RECORDED AS "ESTABLISHED NOTHING", it is kept and still does not resolve.
        kept = await RS.record_venue_evidence(
            c, evidence_id=PFX + "evd-nil", operation_id=op2, account_id=ACCT,
            venue="PMUS", us_market_slug=SLUG_A, intent_id=PFX + "id",
            kind=RS.EV_ESTABLISHED_NOTHING,
            search_endpoint="GET /v1/orders?status=open", read_at=1790000002.0,
            covered_terminal_orders=False, results_returned=0)
        assert kept["ok"] is True, kept
        still = await RS.resolve_from_the_venue(c, operation_id=op2)
        assert still["ok"] is False, still
        assert still["exposure"] == "PRESERVED"
        assert "TERMINAL orders" in still["what_would_resolve_it"]
        assert (await RS.get(c, op2))["reservation"]["state"] == RS.AMBIGUOUS

        # (c) A SEARCH THAT COVERED TERMINAL ORDERS AND FOUND NOTHING DOES.
        # Synthetic venue evidence covers this recorded send and declares the
        # point through which the history is complete. A wall clock alone is
        # not a visibility guarantee.
        await c.execute("UPDATE bettor_funded_intents SET sent_at=to_timestamp($2) "
                        "WHERE intent_id=$1", PFX + "id", 1790000000.0)
        good = await RS.record_venue_evidence(
            c, evidence_id=PFX + "evd-all", operation_id=op2, account_id=ACCT,
            venue="PMUS", us_market_slug=SLUG_A, intent_id=PFX + "id",
            kind=RS.EV_NO_SUCH_ORDER,
            search_endpoint="GET /v1/orders?status=all",
            search_scope={"account": ACCT, "market": SLUG_A,
                          "operation_id": op2, "intent_id": PFX + "id",
                          "status": "ALL", "pagination_complete": True,
                          "complete_through_epoch_s": 1790000003.0,
                          "includes": ["open", "filled", "cancelled",
                                       "rejected"]},
            read_at=1790000003.0, covered_terminal_orders=True,
            window_from_epoch_s=1789999000.0, window_to_epoch_s=1790000003.0,
            results_returned=0)
        assert good["ok"] is True, good
        no = await RS.resolve_from_the_venue(c, operation_id=op2)
        assert no["ok"] is True, no
        assert no["reservation"]["state"] == RS.RELEASED
        assert no["reservation"]["resolution"] == RS.WHY_VENUE_HAS_NO_SUCH_ORDER
        assert no["searched"]["covered_terminal_orders"] is True
        assert no["evidence_id"] == PFX + "evd-all"
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_consumed_reservation_must_name_the_venues_order():
    """CONSUMED ASSERTS A REAL ORDER EXISTS. `venue_order_id` used to default to
    None, so that assertion could be written naming nothing."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        if not await _has_evidence(c):
            pytest.skip("migration 133 not applied to this database")
        await _group(c, PFX + "ge2")
        await _intent(c, PFX + "ie2", PFX + "ge2", "PRIMARY", SLUG_A)
        op = PFX + "ope2"
        await RS.hold(c, operation_id=op, group_id=PFX + "ge2",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        await RS.commit_to_intent(c, operation_id=op, intent_id=PFX + "ie2")
        await RS.mark_send_attempted(c, operation_id=op)

        blank = await RS.record_the_venue_named_it(c, operation_id=op,
                                                  venue_order_id="")
        assert blank["ok"] is False
        assert blank["refusal"] == RS.R_ORDER_ID_REQUIRED, blank

        # AND WITH AN ID BUT NO RECORDED EVIDENCE, still refused.
        unevidenced = await RS.record_the_venue_named_it(
            c, operation_id=op, venue_order_id="vo-e2")
        assert unevidenced["ok"] is False
        assert unevidenced["refusal"] == RS.R_NO_EVIDENCE, unevidenced
        assert (await RS.get(c, op))["reservation"]["state"] == \
            RS.SEND_ATTEMPTED

        # EVIDENCE FOR A DIFFERENT ORDER ID DOES NOT AUTHORISE THIS ONE.
        await RS.record_venue_evidence(
            c, evidence_id=PFX + "eve2", operation_id=op, account_id=ACCT,
            venue="PMUS", us_market_slug=SLUG_A, intent_id=PFX + "ie2",
            kind=RS.EV_NAMED, venue_order_id="vo-SOMEONE-ELSE",
            search_endpoint="GET /v1/orders", read_at=1790000010.0,
            covered_terminal_orders=True, results_returned=1)
        mismatched = await RS.record_the_venue_named_it(
            c, operation_id=op, venue_order_id="vo-e2")
        assert mismatched["ok"] is False
        assert mismatched["refusal"] == RS.R_EVIDENCE_NOT_BOUND, mismatched
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_evidence_for_another_instrument_does_not_resolve_this_operation():
    """BOUND MEANS THE SAME INSTRUMENT. A read of a different market says nothing
    about this acquisition, however true it is in itself."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        if not await _has_evidence(c):
            pytest.skip("migration 133 not applied to this database")
        await _group(c, PFX + "gf2")
        await _intent(c, PFX + "if2", PFX + "gf2", "PRIMARY", SLUG_A)
        op = PFX + "opf2"
        await RS.hold(c, operation_id=op, group_id=PFX + "gf2",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        await RS.commit_to_intent(c, operation_id=op, intent_id=PFX + "if2")
        await RS.mark_send_attempted(c, operation_id=op)
        await RS.mark_ambiguous(c, operation_id=op, why="no answer")
        await RS.record_venue_evidence(
            c, evidence_id=PFX + "evf2", operation_id=op, account_id=ACCT,
            venue="PMUS", us_market_slug=SLUG_B,      # <- the OTHER market
            intent_id=PFX + "if2", kind=RS.EV_NO_SUCH_ORDER,
            search_endpoint="GET /v1/orders?status=all", read_at=1790000020.0,
            covered_terminal_orders=True, results_returned=0)
        got = await RS.resolve_from_the_venue(c, operation_id=op)
        assert got["ok"] is False, got
        assert got["exposure"] == "PRESERVED"
        assert (await RS.get(c, op))["reservation"]["state"] == RS.AMBIGUOUS
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_restart_finds_its_unresolved_work_by_operation_identity():
    """WHAT A RESTARTED PROCESS ACTUALLY DOES. It has no memory of what it was
    doing, so it lists the live reservations -- each one an acquisition whose
    outcome it does not know -- and asks the venue about each. `live()` is that
    list, and `get()` by operation identity is how it picks up a specific one."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "ge")
        await _intent(c, PFX + "ie", PFX + "ge", "PRIMARY", SLUG_A,
                      state="SEND_ATTEMPTED")
        op = PFX + "ope"
        await RS.hold(c, operation_id=op, group_id=PFX + "ge",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        await RS.commit_to_intent(c, operation_id=op, intent_id=PFX + "ie")
        await RS.mark_send_attempted(c, operation_id=op)

        # ── THE RESTART: a brand-new connection, no in-process state ──
        fresh = await _conn()
        try:
            outstanding = await RS.live(fresh, account_id=ACCT)
            assert [r["operation_id"] for r in outstanding] == [op], outstanding
            assert outstanding[0]["state"] == RS.SEND_ATTEMPTED
            assert outstanding[0]["intent_id"] == PFX + "ie"
            picked = await RS.get(fresh, op)
            assert picked["ok"] is True
            assert picked["is_live"] is True
            assert picked["counts_as_committed_capital"] is False
            # AND IT MUST NOT RESEND: the only steps available are the venue's
            # answer or an admission of ignorance.
            assert set(RS.TRANSITIONS[outstanding[0]["state"]]) == \
                {RS.CONSUMED, RS.AMBIGUOUS}
        finally:
            await fresh.close()
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_a_duplicate_delivery_of_each_step_is_harmless():
    """THE SAME INSTRUCTION TWICE, at every step. A duplicated message must not
    advance the machine twice or fail the caller; it reports `already` and leaves
    the row where it is."""
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "gf")
        await _intent(c, PFX + "if", PFX + "gf", "PRIMARY", SLUG_A)
        op = PFX + "opf"
        steps = [
            ("hold", lambda: RS.hold(
                c, operation_id=op, group_id=PFX + "gf", leg_role="PRIMARY",
                us_market_slug=SLUG_A, quantity=10, limit_price=0.5,
                collateral_usd=5.0)),
            ("commit", lambda: RS.commit_to_intent(
                c, operation_id=op, intent_id=PFX + "if")),
            ("send", lambda: RS.mark_send_attempted(c, operation_id=op)),
            ("evidence", lambda: _named(c, op, PFX + "if", SLUG_A, "vo-f")),
            ("consume", lambda: RS.record_the_venue_named_it(
                c, operation_id=op, venue_order_id="vo-f")),
        ]
        for name, step in steps:
            first = await step()
            assert first["ok"] is True, (name, first)
            dup = await step()
            assert dup["ok"] is True, (name, dup)
            assert dup.get("already") is True, (
                "a duplicate %r was not recognised as a replay: %r"
                % (name, dup))
        assert await c.fetchval(
            "SELECT count(*) FROM bettor_funded_leg_reservations "
            " WHERE group_id=$1", PFX + "gf") == 1
        final = await RS.get(c, op)
        assert final["reservation"]["state"] == RS.CONSUMED
        assert final["is_live"] is False
    finally:
        await _clean(c)
        await c.close()


@pg
async def test_the_whole_path_carries_one_identity_end_to_end():
    """THE JOIN THE REVIEW ASKED FOR, asserted as one chain: the operation
    identity reaches the reservation, the reservation names the committed intent,
    the intent carries the venue's order id, and the group's paired quantity
    reflects what was actually acquired."""
    from sportsassets import bettor_funded_book as FB
    from sportsassets import bettor_funded_reservations as RS
    c = await _conn()
    try:
        await _ready(c)
        await _group(c, PFX + "gg")
        op = PFX + "opg"
        await RS.hold(c, operation_id=op, group_id=PFX + "gg",
                      leg_role="PRIMARY", us_market_slug=SLUG_A, quantity=10,
                      limit_price=0.5, collateral_usd=5.0)
        await _intent(c, PFX + "ig", PFX + "gg", "PRIMARY", SLUG_A)
        await RS.commit_to_intent(c, operation_id=op, intent_id=PFX + "ig")
        await RS.mark_send_attempted(c, operation_id=op)
        await FB.record_acknowledgement(c, PFX + "ig", venue_order_id="vo-g",
                                       status="open")
        await _named(c, op, PFX + "ig", SLUG_A, "vo-g")
        await RS.record_the_venue_named_it(c, operation_id=op,
                                          venue_order_id="vo-g")
        await FB.ingest_fills(
            c, PFX + "ig",
            [{"qty": 10.0, "price": 0.5, "venue_fill_id": "vf-g"}])

        chain = await c.fetchrow(
            "SELECT r.operation_id, r.state AS res_state, r.intent_id, "
            "       i.state AS intent_state, i.venue_order_id, "
            "       i.residual_qty::float8 AS residual, "
            "       i.portfolio_group_id "
            "  FROM bettor_funded_leg_reservations r "
            "  JOIN bettor_funded_intents i ON i.intent_id = r.intent_id "
            " WHERE r.operation_id = $1", op)
        assert chain["operation_id"] == op
        assert chain["res_state"] == RS.CONSUMED
        assert chain["intent_id"] == PFX + "ig"
        assert chain["intent_state"] == "FILLED"
        assert chain["venue_order_id"] == "vo-g"
        assert chain["residual"] == pytest.approx(10.0)
        assert chain["portfolio_group_id"] == PFX + "gg"
        # THE GROUP STILL HOLDS IT, and the reservation no longer counts capital.
        assert await c.fetchval(
            "SELECT bettor_funded_group_leg_inventory($1,'PRIMARY')::float8",
            PFX + "gg") == pytest.approx(10.0)
        assert (await RS.reserved_collateral_usd(
            c, account_id=ACCT))["reserved_usd"] == pytest.approx(0.0)
    finally:
        await _clean(c)
        await c.close()
