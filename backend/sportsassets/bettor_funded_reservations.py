"""THE RESERVATION-TO-SUBMISSION STATE MACHINE, addressed by operation identity.

WHAT THIS CLOSES. Migration 131 created `bettor_funded_leg_reservations` and
NOTHING IN THE APPLICATION TOUCHED IT. A schema with no caller is not a
capability: the table, its states, its live-per-leg bound and its operation
identity all existed while every acquisition still went straight to
`record_intent`, so none of it was ever reached. An independent review asked for
the machine to be finished, and this is the missing half.

── WHY A RESERVATION EXISTS AT ALL ───────────────────────────────────
An intent row is created and then an order is sent. Between those two moments the
capacity and the collateral are committed to an acquisition that has no row yet,
so two callers can each decide there is room. The reservation is the row that
exists BEFORE the intent, so "is there room" and "take the room" are one atomic
act at the database rather than a check followed by a hope.

── THE IDENTITY IS THE OPERATION, NOT THE ROW ────────────────────────
Every function here takes `operation_id`: the name of the ACQUISITION ATTEMPT.
That is deliberate and it is the whole idempotency story. A caller that retries
after a timeout, a restart that replays its work queue, a duplicate delivery of
the same instruction -- all of them arrive with the SAME operation_id and reach
the SAME row, whatever state it has got to. A `reservation_id` would have made
retry-safety depend on the caller remembering a value it did not choose.

And `operation_id` is UNIQUE ACROSS THE WHOLE TABLE, not merely among live rows.
A live-only rule stops two simultaneous holds; it does not stop the same
acquisition being replayed after the first became CONSUMED or RELEASED. A
legitimate top-up is a different economic action and carries a new identity, so
it is not blocked by this.

── THE TRANSITIONS, AND WHAT IS DELIBERATELY ABSENT ──────────────────
    HELD            -> COMMITTED | RELEASED
    COMMITTED       -> SEND_ATTEMPTED | RELEASED
    SEND_ATTEMPTED  -> CONSUMED | AMBIGUOUS
    AMBIGUOUS       -> CONSUMED | RELEASED

`SEND_ATTEMPTED -> RELEASED` IS NOT A TRANSITION. Once a request may have left,
nothing may declare that it did not; the only ways out are the venue naming the
order (CONSUMED) or admitting we do not know (AMBIGUOUS). AMBIGUOUS is resolved
only from the venue's own answer, which is what makes that release evidence
rather than a convenience. The database enforces this too, because the rule that
decides whether we believe an order exists must not be a convention held only in
Python.

── EXPOSURE IS COUNTED ONCE ──────────────────────────────────────────
A reservation contributes to committed capital ONLY while it is HELD. From
COMMITTED onward an intent row exists, and the intent is what the existing
headroom and one-position checks already count -- `order_is_outstanding` covers
INTENT_RECORDED, SEND_ATTEMPTED and UNRESOLVED. Counting the reservation as well
would double every acquisition's collateral at exactly the moment the system is
deciding whether it can afford the next one. `reserved_collateral_usd` therefore
sums HELD rows and nothing else, and says so in its own output.

NOTHING HERE SENDS ANYTHING. There is no venue client in this module and no
import that could reach one. It writes rows that describe what a caller is about
to do and what it later learned.
"""

from __future__ import annotations

from typing import Any

VERSION = "FUNDED_RESERVATIONS_V1"

#: Every state the column admits.
HELD = "HELD"
COMMITTED = "COMMITTED"
SEND_ATTEMPTED = "SEND_ATTEMPTED"
AMBIGUOUS = "AMBIGUOUS"
CONSUMED = "CONSUMED"
RELEASED = "RELEASED"

STATES = (HELD, COMMITTED, SEND_ATTEMPTED, AMBIGUOUS, CONSUMED, RELEASED)

#: LIVE MEANS "STILL CLAIMS THE LEG". An unacknowledged send is live: the order
#: may exist, so a second attempt on the same leg must be refused.
LIVE_STATES = (HELD, COMMITTED, SEND_ATTEMPTED, AMBIGUOUS)
TERMINAL_STATES = (CONSUMED, RELEASED)

#: THE MACHINE, mirrored from the database trigger that also enforces it. Kept
#: here so a caller can be refused by name before a statement is issued, and so
#: a test can assert the two agree rather than trusting that they do.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    HELD: (COMMITTED, RELEASED),
    COMMITTED: (SEND_ATTEMPTED, RELEASED),
    SEND_ATTEMPTED: (CONSUMED, AMBIGUOUS),
    AMBIGUOUS: (CONSUMED, RELEASED),
    CONSUMED: (),
    RELEASED: (),
}

#: ONLY HELD COUNTS TOWARD COMMITTED CAPITAL. See the module docstring.
STATES_THAT_COUNT_AS_COMMITTED_CAPITAL = (HELD,)

R_NO_SUCH_OPERATION = "NO_RESERVATION_FOR_THAT_OPERATION"
R_OPERATION_ALREADY_EXISTS = "THAT_OPERATION_ALREADY_HAS_A_RESERVATION"
R_LEG_ALREADY_RESERVED = "THAT_LEG_ALREADY_HAS_A_LIVE_RESERVATION"
R_ILLEGAL_TRANSITION = "THAT_IS_NOT_A_TRANSITION_THIS_MACHINE_HAS"
R_GROUP_IS_CLOSED = "THE_PORTFOLIO_GROUP_IS_CLOSED"
R_GROUP_DOES_NOT_EXIST = "THE_PORTFOLIO_GROUP_DOES_NOT_EXIST"
R_INTENT_IS_NOT_THIS_LEG = "THAT_INTENT_DOES_NOT_BELONG_TO_THIS_LEG"
R_ALREADY_COMMITTED_ELSEWHERE = "THIS_RESERVATION_NAMES_A_DIFFERENT_INTENT"
R_SCHEMA_UNAVAILABLE = "THE_RESERVATION_SCHEMA_IS_NOT_IN_THIS_DATABASE"
R_ROLE_NOT_STATED = "LEG_ROLE_WAS_NOT_STATED"
#: ── THE REFUSAL THAT WAS MISSING, AND THE BUG IT CLOSES ─────────────
#: An independent review reproduced this directly. `op-1` was COMMITTED to
#: `intent-A`; `commit_to_intent(op-1, intent-B)` returned ok=True/already=True --
#: because the STATE already equalled COMMITTED -- and then reported
#: `exposure_is_now_counted_on: intent-B` while the stored row still named
#: `intent-A`. `hold()` had the same hole: replaying `op-1` with a different role,
#: instrument, quantity, price and collateral was accepted as a retry and the
#: caller was told its terms had been taken.
#:
#: IDEMPOTENCY IS A PROMISE ABOUT AN IDENTICAL REQUEST. Reusing one identity for
#: different terms is a DIFFERENT economic action, and answering it with the first
#: action's row while echoing the second action's arguments is worse than either
#: refusing or accepting: the caller reads its own numbers back and believes they
#: were stored.
R_IDENTITY_REUSED_WITH_DIFFERENT_TERMS = \
    "THAT_OPERATION_IDENTITY_ALREADY_NAMES_DIFFERENT_TERMS"

#: What an `operation_id` FIXES. Any of these differing on a replay is a conflict,
#: not a retry. `intent_id` is included: from COMMITTED onward the identity names a
#: specific order.
IDENTITY_FIXES = ("group_id", "leg_role", "us_market_slug", "quantity",
                  "limit_price", "collateral_usd", "intent_id")

#: Why a reservation resolved. Stored in `resolution`, so a later reader can tell
#: an evidenced release from a guess.
WHY_NEVER_SENT = "THE_REQUEST_NEVER_LEFT_THIS_PROCESS"
WHY_VENUE_HAS_NO_SUCH_ORDER = "THE_VENUE_CONFIRMED_NO_SUCH_ORDER_EXISTS"
WHY_VENUE_NAMED_THE_ORDER = "THE_VENUE_NAMED_THE_ORDER"
WHY_CALLER_ABANDONED_BEFORE_COMMITTING = "ABANDONED_BEFORE_ANY_INTENT_EXISTED"

EVIDENCED_RELEASES = (WHY_NEVER_SENT, WHY_VENUE_HAS_NO_SUCH_ORDER,
                      WHY_CALLER_ABANDONED_BEFORE_COMMITTING)


class SchemaUnavailable(RuntimeError):
    """The reservation tables are not in this database, or cannot be read."""


def describe() -> dict:
    """The machine as data, for the operator view and for the tests that assert
    the database agrees with it."""
    return {
        "version": VERSION,
        "states": list(STATES),
        "live_states": list(LIVE_STATES),
        "terminal_states": list(TERMINAL_STATES),
        "transitions": {k: list(v) for k, v in TRANSITIONS.items()},
        "states_that_count_as_committed_capital": list(
            STATES_THAT_COUNT_AS_COMMITTED_CAPITAL),
        "why_send_attempted_cannot_be_released": (
            "once a request may have left, nothing may declare that it did "
            "not. The routes out are CONSUMED (the venue named the order) and "
            "AMBIGUOUS (we do not know), and AMBIGUOUS is released only on the "
            "venue's own answer"),
        "identity": "operation_id, unique across the whole table, forever",
        "this_module_sends_nothing": True,
    }


async def _has_schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables "
            " WHERE table_schema='public' "
            "   AND table_name='bettor_funded_leg_reservations'"))
    except Exception as exc:                                # noqa: BLE001
        raise SchemaUnavailable(
            "the catalogue could not be read (%s), so whether the reservation "
            "tables exist is UNKNOWN" % type(exc).__name__) from exc


def _row(r) -> dict | None:
    if r is None:
        return None
    out = dict(r)
    for k in ("quantity", "collateral_usd", "limit_price"):
        if out.get(k) is not None:
            out[k] = float(out[k])
    return out


async def _fetch(conn, operation_id: str) -> dict | None:
    return _row(await conn.fetchrow(
        "SELECT * FROM bettor_funded_leg_reservations WHERE operation_id=$1",
        str(operation_id)))


def _conflicts(stored: dict, asserted: dict) -> list[dict]:
    """Which of the caller's IMMUTABLE arguments disagree with the stored row.

    A field the caller did not assert (None) is not compared -- a step that says
    nothing about the quantity is not claiming one. Numbers are compared as
    numbers, because `10` and `Decimal('10.0')` are the same reservation and a
    string comparison would call them a conflict.

    THIS IS CALLED BEFORE EVERY REPLAY-SUCCESS RETURN, including the raced path,
    which is the whole point: a replay is only a replay if the request is the same
    request.
    """
    out: list[dict] = []
    for field in IDENTITY_FIXES:
        want = asserted.get(field)
        if want is None:
            continue
        have = stored.get(field)
        if isinstance(want, (int, float)) and isinstance(have, (int, float)):
            same = abs(float(have) - float(want)) < 1e-9
        else:
            same = have is not None and str(have) == str(want)
        if not same:
            out.append({"field": field, "stored": have, "requested": want})
    return out


def _refuse_conflict(out: dict, stored: dict, clashes: list[dict]) -> dict:
    return dict(out, ok=False, already=True,
                refusal=R_IDENTITY_REUSED_WITH_DIFFERENT_TERMS,
                conflicts=clashes, reservation=stored,
                why=("this operation identity already names different terms, so "
                     "the request is not a retry of it. An identical retry gets "
                     "the existing row; a different action needs its own "
                     "operation_id"))


async def get(conn, operation_id: str) -> dict:
    """READ ONE ACQUISITION ATTEMPT BY ITS IDENTITY. Used by recovery, which
    needs to know where a restarted operation had got to."""
    try:
        if not await _has_schema(conn):
            return {"ok": False, "refusal": R_SCHEMA_UNAVAILABLE}
    except SchemaUnavailable as exc:
        return {"ok": False, "refusal": R_SCHEMA_UNAVAILABLE, "why": str(exc)}
    row = await _fetch(conn, operation_id)
    if row is None:
        return {"ok": False, "refusal": R_NO_SUCH_OPERATION,
                "operation_id": str(operation_id)}
    return {"ok": True, "refusal": None, "reservation": row,
            "is_live": row["state"] in LIVE_STATES,
            "counts_as_committed_capital":
                row["state"] in STATES_THAT_COUNT_AS_COMMITTED_CAPITAL}


async def hold(conn, *, operation_id: str, group_id: str, leg_role: str,
               us_market_slug: str, quantity: float, limit_price: float,
               collateral_usd: float) -> dict:
    """TAKE THE LEG, BEFORE ANY INTENT EXISTS.

    IDEMPOTENT ON AN IDENTICAL OPERATION. A replay of the SAME request reaches
    the row the first call made and is reported as such, in whatever state it has
    since reached -- not a new hold and not an error. That is what lets a caller
    retry after a timeout without duplicating an acquisition or having to remember
    whether its first attempt landed.

    A CONFLICTING REUSE OF THE IDENTITY IS REFUSED. It used to be accepted: a
    replay of `op-1` naming a different role, instrument, quantity, price and
    collateral came back ok=True/already=True, and the caller was handed the FIRST
    action's row while believing its own terms had been taken. The immutable
    arguments are now compared against the stored row before any replay succeeds.

    THREE REFUSALS, THREE DIFFERENT THINGS: this identity already names different
    terms; this LEG is already claimed by another live operation; this group is
    closed or absent. Conflating the first two would make contention look like a
    retry.
    """
    out: dict[str, Any] = {"version": VERSION, "operation_id": str(operation_id),
                           "group_id": str(group_id), "leg_role": leg_role}
    if leg_role not in ("PRIMARY", "HEDGE"):
        return dict(out, ok=False, refusal=R_ROLE_NOT_STATED,
                    why="leg role %r is not PRIMARY or HEDGE" % (leg_role,))
    try:
        if not await _has_schema(conn):
            return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE)
    except SchemaUnavailable as exc:
        return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE, why=str(exc))

    asserted = {"group_id": str(group_id), "leg_role": str(leg_role),
                "us_market_slug": str(us_market_slug),
                "quantity": float(quantity), "limit_price": float(limit_price),
                "collateral_usd": float(collateral_usd)}
    existing = await _fetch(conn, operation_id)
    if existing is not None:
        clashes = _conflicts(existing, asserted)
        if clashes:
            return _refuse_conflict(out, existing, clashes)
        return dict(out, ok=True, refusal=None, already=True,
                    reservation=existing,
                    why=("this operation already has a reservation with exactly "
                         "these terms; a replay reaches the same row rather than "
                         "taking the leg twice"))
    rid = "res:%s" % str(operation_id)
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO bettor_funded_leg_reservations "
                "(reservation_id, group_id, leg_role, us_market_slug, quantity,"
                " collateral_usd, limit_price, state, operation_id) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,'HELD',$8)",
                rid, str(group_id), str(leg_role), str(us_market_slug),
                float(quantity), float(collateral_usd), float(limit_price),
                str(operation_id))
    except Exception as exc:                                # noqa: BLE001
        # THE SAVEPOINT HAS ROLLED BACK BY HERE, so the queries below are safe.
        # The same ordering the entry path needed, for the same reason: catching
        # a PostgresError does not undo an aborted transaction.
        msg = " ".join(str(exc).split())
        if "bettor_funded_reservation_operation_uniq" in msg:
            # THE RACED INSERT IS STILL A REPLAY, AND STILL HAS TO MATCH. Another
            # caller won the unique index; whether that is OUR operation retried
            # or a different action reusing the identity is decided by the same
            # comparison as the pre-check above, not by which insert lost.
            raced = await _fetch(conn, operation_id)
            clashes = _conflicts(raced or {}, asserted)
            if clashes:
                return _refuse_conflict(out, raced, clashes)
            return dict(out, ok=True, refusal=None, already=True,
                        reservation=raced,
                        why="another caller inserted this same operation first")
        if "bettor_funded_one_live_reservation_per_leg" in msg:
            holder = _row(await conn.fetchrow(
                "SELECT * FROM bettor_funded_leg_reservations "
                " WHERE group_id=$1 AND leg_role=$2 AND state = ANY($3::text[])",
                str(group_id), str(leg_role), list(LIVE_STATES)))
            return dict(out, ok=False, refusal=R_LEG_ALREADY_RESERVED,
                        held_by=holder,
                        why=("this leg already has a live reservation. An "
                             "unacknowledged send is live, so a second attempt "
                             "is refused until the first is resolved"))
        if "is closed" in msg:
            return dict(out, ok=False, refusal=R_GROUP_IS_CLOSED, why=msg[:300])
        if "does not exist" in msg or "violates foreign key" in msg:
            return dict(out, ok=False, refusal=R_GROUP_DOES_NOT_EXIST,
                        why=msg[:300])
        raise
    return dict(out, ok=True, refusal=None, already=False,
                reservation=await _fetch(conn, operation_id),
                counts_as_committed_capital=True,
                why=("the leg is claimed. This is the only state in which the "
                     "reservation itself counts toward committed capital; from "
                     "COMMITTED onward the intent row is what is counted"))


async def _transition(conn, operation_id: str, *, to: str, expect: tuple,
                      intent_id: str | None = None,
                      note: str | None = None) -> dict:
    """ONE GUARDED TRANSITION, atomic, refused by name.

    THE EXPECTED CURRENT STATES ARE IN THE UPDATE'S OWN `WHERE` CLAUSE, not only
    in the read above it. A read-then-write would let two callers racing the same
    step both pass the read; here the second matches no row, and the report then
    says where the row actually got to -- which is what a replay needs to hear.

    AND EVERY REPLAY-SUCCESS RETURN VALIDATES THE CALLER'S IMMUTABLE ARGUMENTS
    FIRST. This is the bug an independent review reproduced: `op-1` already
    COMMITTED to `intent-A`, and `commit_to_intent(op-1, intent-B)` returned
    ok=True/already=True purely because the STATE matched, so the caller was told
    a different intent had been bound than the one stored. Matching the target
    state is not the same as being the same request.
    """
    out: dict[str, Any] = {"version": VERSION, "operation_id": str(operation_id),
                           "to": to}
    asserted = {"intent_id": None if intent_id is None else str(intent_id)}
    try:
        if not await _has_schema(conn):
            return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE)
    except SchemaUnavailable as exc:
        return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE, why=str(exc))
    row = await _fetch(conn, operation_id)
    if row is None:
        return dict(out, ok=False, refusal=R_NO_SUCH_OPERATION)
    out["from_state"] = row["state"]
    if row["state"] == to:
        # ALREADY THERE. A replay of the same step is not an error -- PROVIDED it
        # is the same step. A different `intent_id` makes it a different action.
        clashes = _conflicts(row, asserted)
        if clashes:
            return _refuse_conflict(out, row, clashes)
        return dict(out, ok=True, refusal=None, already=True, reservation=row)
    if to not in TRANSITIONS.get(row["state"], ()):
        return dict(out, ok=False, refusal=R_ILLEGAL_TRANSITION,
                    reservation=row,
                    legal_from_here=list(TRANSITIONS.get(row["state"], ())),
                    why=("%s -> %s is not a transition this machine has"
                         % (row["state"], to)))

    # ── THE PLACEHOLDERS ARE BUILT, NOT COUNTED BY HAND ──────────────
    # An earlier version computed `$N` from the length of an argument tuple.
    # That is the shape of bug that silently points a parameter at the wrong
    # column, and asyncpg's own `AmbiguousParameterError` has already cost this
    # work sixteen tests once. The list and the SQL are extended together.
    params: list = [str(operation_id), to]
    sets = ["state = $2"]
    if intent_id is not None:
        params.append(str(intent_id))
        sets.append("intent_id = $%d" % len(params))
    if to in TERMINAL_STATES:
        params.append(note or to)
        sets.append("resolved_at = now()")
        sets.append("resolution = $%d" % len(params))
    params.append(list(expect))
    sql = ("UPDATE bettor_funded_leg_reservations SET " + ", ".join(sets)
           + " WHERE operation_id = $1 AND state = ANY($%d::text[])"
           % len(params))
    try:
        async with conn.transaction():
            status = await conn.execute(sql, *params)
    except Exception as exc:                                # noqa: BLE001
        msg = " ".join(str(exc).split())
        if "cannot go from" in msg:
            return dict(out, ok=False, refusal=R_ILLEGAL_TRANSITION,
                        why=msg[:300], reservation=await _fetch(
                            conn, operation_id))
        if "identity is fixed" in msg or "already committed to intent" in msg:
            return dict(out, ok=False, refusal=R_ALREADY_COMMITTED_ELSEWHERE,
                        why=msg[:300])
        if "belongs to group" in msg or "reserved" in msg and "names intent" in msg:
            return dict(out, ok=False, refusal=R_INTENT_IS_NOT_THIS_LEG,
                        why=msg[:300])
        if "is closed" in msg:
            return dict(out, ok=False, refusal=R_GROUP_IS_CLOSED, why=msg[:300])
        raise
    after = await _fetch(conn, operation_id)
    if str(status).endswith(" 0"):
        # THE ROW MOVED UNDER US, which is a real answer rather than a failure:
        # another caller performed this step first, or a different one.
        #
        # THE RACED PATH IS A REPLAY-SUCCESS PATH TOO, so it validates the
        # immutable arguments before reporting ok. Without this, losing the race
        # to a caller that bound a DIFFERENT intent would be reported as this
        # caller's own step having succeeded.
        clashes = _conflicts(after or {}, asserted)
        if clashes:
            return _refuse_conflict(out, after, clashes)
        return dict(out, ok=(after or {}).get("state") == to,
                    refusal=None if (after or {}).get("state") == to
                    else R_ILLEGAL_TRANSITION,
                    reservation=after, raced=True,
                    why=("no row matched the expected state; it is now %r"
                         % (after or {}).get("state")))
    return dict(out, ok=True, refusal=None, already=False, reservation=after)


async def commit_to_intent(conn, *, operation_id: str, intent_id: str) -> dict:
    """BIND THE RESERVATION TO THE INTENT THAT WILL BE SENT.

    From here on the reservation names a specific order, so the identity cannot
    be re-pointed: the database refuses a second, different `intent_id`. That is
    the rule that stops one acquisition attempt from standing in for two.

    AND FROM HERE THE RESERVATION STOPS COUNTING AS COMMITTED CAPITAL, because
    the intent row now exists and the existing headroom check counts it. Both
    counting would double the collateral of every acquisition at the moment the
    system is deciding whether it can afford the next.
    """
    got = await _transition(conn, operation_id, to=COMMITTED, expect=(HELD,),
                            intent_id=str(intent_id))
    if got.get("ok") and got.get("reservation"):
        got["counts_as_committed_capital"] = False
        # ── READ BACK FROM THE ROW, NEVER ECHO THE ARGUMENT ──────────
        # This line used to be `str(intent_id)`, which is how the reproduced bug
        # became visible: a replay reported `exposure_is_now_counted_on:
        # intent-B` while the stored reservation named `intent-A`. The conflict
        # check above now refuses that case outright, and reporting the STORED
        # value means even a future hole cannot make this field disagree with the
        # database.
        got["exposure_is_now_counted_on"] = got["reservation"]["intent_id"]
    return got


async def mark_send_attempted(conn, *, operation_id: str) -> dict:
    """THE REQUEST IS ABOUT TO LEAVE. After this, a lost answer means the order
    MAY exist, and this reservation can no longer be released as never-sent."""
    return await _transition(conn, operation_id, to=SEND_ATTEMPTED,
                             expect=(COMMITTED,))


async def record_the_venue_named_it(conn, *, operation_id: str,
                                    venue_order_id: str | None = None) -> dict:
    """THE VENUE NAMED AN ORDER, so the acquisition happened and the reservation
    is CONSUMED. Reachable from SEND_ATTEMPTED and from AMBIGUOUS -- the second
    is recovery learning, from the venue itself, that the order it could not see
    does exist."""
    return await _transition(
        conn, operation_id, to=CONSUMED, expect=(SEND_ATTEMPTED, AMBIGUOUS),
        note=("%s:%s" % (WHY_VENUE_NAMED_THE_ORDER, venue_order_id)
              if venue_order_id else WHY_VENUE_NAMED_THE_ORDER))


async def mark_ambiguous(conn, *, operation_id: str, why: str) -> dict:
    """WE DO NOT KNOW WHETHER THE ORDER EXISTS.

    Deliberately NOT terminal and deliberately still LIVE: it keeps claiming the
    leg, so no second attempt is admitted, and it keeps the group open. An
    unknown is exposure until the venue says otherwise.
    """
    got = await _transition(conn, operation_id, to=AMBIGUOUS,
                            expect=(SEND_ATTEMPTED,))
    if got.get("ok"):
        got["exposure"] = "PRESERVED"
        got["stated_reason"] = str(why)[:500]
        got["resolve_by"] = "asking the venue; never by resending"
    return got


async def resolve_from_the_venue(conn, *, operation_id: str,
                                the_venue_has_the_order: bool,
                                why: str | None = None) -> dict:
    """RESOLVE AN AMBIGUOUS ACQUISITION ON THE VENUE'S OWN ANSWER.

    The boolean is named for what the CALLER IS ASSERTING, so a future caller
    that does not actually have the venue's answer has to notice that it is
    claiming to. There is no code path that resolves an ambiguity by resending.
    """
    if the_venue_has_the_order:
        return await record_the_venue_named_it(conn, operation_id=operation_id)
    return await _transition(
        conn, operation_id, to=RELEASED, expect=(AMBIGUOUS,),
        note=why or WHY_VENUE_HAS_NO_SUCH_ORDER)


async def release(conn, *, operation_id: str, why: str) -> dict:
    """GIVE THE LEG BACK, and only from a state where nothing may have left.

    HELD and COMMITTED are both pre-send, so releasing them is a statement about
    this process and nothing else. SEND_ATTEMPTED is not accepted here -- and
    that refusal is the point: the caller must go through AMBIGUOUS and then the
    venue. AMBIGUOUS is released through `resolve_from_the_venue`, whose
    signature makes the claim explicit.
    """
    return await _transition(conn, operation_id, to=RELEASED,
                             expect=(HELD, COMMITTED),
                             note=why or WHY_NEVER_SENT)


async def live(conn, *, group_id: str | None = None,
               account_id: str | None = None) -> list[dict]:
    """EVERY RESERVATION THAT STILL CLAIMS A LEG. This is what recovery iterates
    after a restart: each of these is an acquisition whose outcome this process
    does not yet know."""
    sql = ("SELECT r.* FROM bettor_funded_leg_reservations r "
           "  JOIN bettor_funded_portfolio_groups g ON g.group_id = r.group_id "
           " WHERE r.state = ANY($1::text[])")
    args: list = [list(LIVE_STATES)]
    if group_id is not None:
        args.append(str(group_id))
        sql += " AND r.group_id = $%d" % len(args)
    if account_id is not None:
        args.append(str(account_id))
        sql += " AND g.account_id = $%d" % len(args)
    return [_row(r) for r in await conn.fetch(sql + " ORDER BY r.created_at",
                                             *args)]


async def reserved_collateral_usd(conn, *,
                                  account_id: str | None = None) -> dict:
    """COMMITTED CAPITAL HELD BY RESERVATIONS, COUNTED ONCE.

    ONLY `HELD` IS SUMMED. From COMMITTED onward the acquisition has an intent
    row, and the intent is what the existing headroom and one-position checks
    count -- `bettor_funded_order_is_outstanding` covers INTENT_RECORDED,
    SEND_ATTEMPTED and UNRESOLVED. Summing both would double the collateral of
    every in-flight acquisition precisely when the system is deciding whether it
    can afford another, which is the direction that spends money it does not
    have.

    The output states the rule and lists the live rows that were NOT counted,
    with the intent that carries each one, so a reader can check the claim
    instead of taking it.
    """
    sql = ("SELECT r.*, g.account_id FROM bettor_funded_leg_reservations r "
           "  JOIN bettor_funded_portfolio_groups g ON g.group_id = r.group_id "
           " WHERE r.state = ANY($1::text[])")
    args: list = [list(LIVE_STATES)]
    if account_id is not None:
        args.append(str(account_id))
        sql += " AND g.account_id = $%d" % len(args)
    rows = [_row(r) for r in await conn.fetch(sql, *args)]
    counted = [r for r in rows
               if r["state"] in STATES_THAT_COUNT_AS_COMMITTED_CAPITAL]
    not_counted = [r for r in rows if r not in counted]
    return {
        "version": VERSION,
        "account_id": account_id,
        "reserved_usd": round(sum(r["collateral_usd"] for r in counted), 6),
        "counted_states": list(STATES_THAT_COUNT_AS_COMMITTED_CAPITAL),
        "counted": [{"operation_id": r["operation_id"],
                     "collateral_usd": r["collateral_usd"]} for r in counted],
        "live_but_not_counted": [
            {"operation_id": r["operation_id"], "state": r["state"],
             "collateral_usd": r["collateral_usd"],
             "counted_on_the_intent_instead": r["intent_id"]}
            for r in not_counted],
        "why": ("a reservation past HELD has an intent row, and the intent is "
                "what the headroom check already counts. Counting both would "
                "double every in-flight acquisition"),
    }
