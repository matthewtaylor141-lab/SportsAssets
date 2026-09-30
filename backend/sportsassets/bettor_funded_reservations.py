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
                  "limit_price", "collateral_usd", "intent_id", "order_intent")

#: The venue's two intents; a reservation may name one or neither (migration 136).
SIDES = ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT")
R_SIDE_NOT_A_VENUE_INTENT = "THE_RESERVATION_SIDE_IS_NOT_A_VENUE_INTENT"

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
               collateral_usd: float, order_intent: str | None = None) -> dict:
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
    # ── THE SIDE, WHEN THE CALLER KNOWS IT ──────────────────────────
    #
    # Migration 136. The instrument alone does not say which outcome token is
    # being claimed, and at a wire price of 0.50 a LONG and a SHORT on one slug
    # agree on every other field here. A side that is stated is stored, is part
    # of the fixed identity, and is compared by the database against the intent
    # this reservation is later committed to. One that is not stated is not
    # invented. Validated with the role, before the database is touched.
    if order_intent is not None and order_intent not in SIDES:
        return dict(out, ok=False, refusal=R_SIDE_NOT_A_VENUE_INTENT,
                    why=("%r is not one of the venue's two intents %r"
                         % (order_intent, SIDES)))
    try:
        if not await _has_schema(conn):
            return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE)
    except SchemaUnavailable as exc:
        return dict(out, ok=False, refusal=R_SCHEMA_UNAVAILABLE, why=str(exc))

    asserted = {"group_id": str(group_id), "leg_role": str(leg_role),
                "us_market_slug": str(us_market_slug),
                "quantity": float(quantity), "limit_price": float(limit_price),
                "collateral_usd": float(collateral_usd),
                "order_intent": order_intent}
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
                " collateral_usd, limit_price, state, operation_id,"
                " order_intent) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,'HELD',$8,$9)",
                rid, str(group_id), str(leg_role), str(us_market_slug),
                float(quantity), float(collateral_usd), float(limit_price),
                str(operation_id), order_intent)
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


#: ── EVIDENCE KINDS, matching migration 133's CHECK ──────────────────
EV_NAMED = "VENUE_NAMED_THE_ORDER"
EV_NO_SUCH_ORDER = "VENUE_HAS_NO_SUCH_ORDER"
EV_ESTABLISHED_NOTHING = "READ_ESTABLISHED_NOTHING"
#: A HUMAN'S STATEMENT, NOT THE VENUE'S (migration 139). An operator, through
#: the authenticated and audited resolution route, attests that no exposure-
#: bearing order exists for this operation -- after that route has itself read
#: the venue's resting orders and the account's own executions on the market
#: and found neither. It is its own kind so that it can never satisfy a check
#: written for `VENUE_HAS_NO_SUCH_ORDER`: `resolve_from_the_venue` does not
#: read it, and only `release_on_attestation` does.
EV_ATTESTED_NO_EXPOSURE = "OPERATOR_ATTESTED_NO_EXPOSURE"
#: AN OPERATOR NAMED THE ORDER (migration 139). The venue RETURNED the order --
#: its terms and creation time were checked by the resolution route -- but that
#: the order is THIS request's is the operator's statement from the venue's own
#: account records, not the venue's. So it is its own kind, read only by
#: `consume_on_operator_naming`, and the reservation's resolution says who
#: named it.
EV_OPERATOR_NAMED = "OPERATOR_NAMED_THE_ORDER"
#: THE VENUE REFUSED THE ORDER IN ITS OWN ANSWER (migration 150). The send
#: returned, carried no order id, and stated an EXPLICIT refusal status that the
#: book recorded as REJECTED on the intent. It is its own kind so that it can
#: never satisfy a check written for `VENUE_HAS_NO_SUCH_ORDER` (a terminal-order
#: search): it is read only by `release_on_explicit_refusal`, which also
#: requires the intent itself to be REJECTED with no venue order id.
EV_REFUSED_IN_ANSWER = "VENUE_REFUSED_THE_ORDER_IN_ITS_ANSWER"
EVIDENCE_KINDS = (EV_NAMED, EV_NO_SUCH_ORDER, EV_ESTABLISHED_NOTHING,
                  EV_ATTESTED_NO_EXPOSURE, EV_OPERATOR_NAMED,
                  EV_REFUSED_IN_ANSWER)
WHY_OPERATOR_ATTESTED_NO_EXPOSURE = "AN_OPERATOR_ATTESTED_NO_EXPOSURE_EXISTS"
WHY_OPERATOR_NAMED_THE_ORDER = "AN_OPERATOR_NAMED_THE_ORDER"
WHY_VENUE_REFUSED_IN_ITS_ANSWER = "THE_VENUE_REFUSED_THE_ORDER_IN_ITS_ANSWER"
R_INTENT_NOT_REJECTED = "THE_INTENT_WAS_NOT_RECORDED_AS_REFUSED_BY_THE_VENUE"

R_NO_EVIDENCE = "NO_DURABLE_EVIDENCE_IS_BOUND_TO_THIS_OPERATION"
R_EVIDENCE_NOT_BOUND = "THE_EVIDENCE_IS_NOT_BOUND_TO_THIS_OPERATIONS_FACTS"
R_EVIDENCE_SEARCH_COULD_NOT_HAVE_FOUND_IT = \
    "THAT_SEARCH_COULD_NOT_HAVE_FOUND_THE_ORDER"
R_EVIDENCE_SCHEMA_UNAVAILABLE = "THE_EVIDENCE_TABLE_IS_NOT_IN_THIS_DATABASE"
R_ORDER_ID_REQUIRED = "A_CONSUMED_RESERVATION_MUST_NAME_THE_VENUES_ORDER"


async def _has_evidence_schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables "
            " WHERE table_schema='public' "
            "   AND table_name='bettor_funded_operation_evidence'"))
    except Exception as exc:                                # noqa: BLE001
        raise SchemaUnavailable(
            "the catalogue could not be read (%s)" % type(exc).__name__) from exc


async def record_venue_evidence(conn, *, evidence_id: str, operation_id: str,
                                account_id: str, venue: str,
                                us_market_slug: str, kind: str,
                                search_endpoint: str, read_at,
                                covered_terminal_orders: bool,
                                intent_id: str | None = None,
                                venue_order_id: str | None = None,
                                search_scope: dict | None = None,
                                window_from_epoch_s=None,
                                window_to_epoch_s=None,
                                results_returned: int = 0,
                                raw: dict | None = None) -> dict:
    """WRITE DOWN WHAT THE VENUE ANSWERED, bound to one operation.

    A PARAMETER IS NOT EVIDENCE. `resolve_from_the_venue` used to take a boolean
    and treat it as proof of absence; this is the row that replaces it. It records
    not only the answer but WHAT WAS ASKED -- endpoint, scope, window, whether
    terminal orders were covered, how many results came back -- so a later reader
    who was not there can tell whether the question could have produced the
    answer.
    """
    import json as _json
    out: dict[str, Any] = {"version": VERSION, "evidence_id": str(evidence_id),
                           "operation_id": str(operation_id), "kind": kind}
    if kind not in EVIDENCE_KINDS:
        return dict(out, ok=False, refusal="THAT_IS_NOT_AN_EVIDENCE_KIND",
                    recognised=list(EVIDENCE_KINDS))
    try:
        if not await _has_evidence_schema(conn):
            return dict(out, ok=False, refusal=R_EVIDENCE_SCHEMA_UNAVAILABLE)
    except SchemaUnavailable as exc:
        return dict(out, ok=False, refusal=R_EVIDENCE_SCHEMA_UNAVAILABLE,
                    why=str(exc))
    if kind in (EV_NAMED, EV_OPERATOR_NAMED) and not venue_order_id:
        return dict(out, ok=False, refusal=R_ORDER_ID_REQUIRED,
                    why=("a record that the venue named an order must carry the "
                         "id it named. Without one it names nothing"))
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO bettor_funded_operation_evidence "
                "(evidence_id, operation_id, account_id, venue, us_market_slug,"
                " intent_id, kind, venue_order_id, search_endpoint,"
                " search_scope, covered_terminal_orders, window_from_epoch_s,"
                " window_to_epoch_s, results_returned, raw, read_at) VALUES "
                "($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12,$13,$14,"
                " $15::jsonb,to_timestamp($16))",
                str(evidence_id), str(operation_id), str(account_id),
                str(venue), str(us_market_slug),
                None if intent_id is None else str(intent_id), kind,
                None if venue_order_id is None else str(venue_order_id),
                str(search_endpoint),
                _json.dumps(search_scope or {}, default=str),
                bool(covered_terminal_orders),
                None if window_from_epoch_s is None
                else float(window_from_epoch_s),
                None if window_to_epoch_s is None else float(window_to_epoch_s),
                int(results_returned), _json.dumps(raw or {}, default=str),
                float(read_at))
    except Exception as exc:                                # noqa: BLE001
        msg = " ".join(str(exc).split())
        if "bettor_funded_evidence_read_uniq" in msg \
                or "operation_evidence_pkey" in msg:
            # AN EVIDENCE ID IS CALLER-CHOSEN, so a replayed recovery pass reaches
            # the same row. But a collision is only a replay if the CONTENT
            # matches -- the same lesson as the reservation identity fix, which is
            # why it is applied here rather than swallowing the violation.
            have = dict(await conn.fetchrow(
                "SELECT * FROM bettor_funded_operation_evidence "
                " WHERE evidence_id = $1", str(evidence_id)) or {})
            expected = {
                "operation_id": str(operation_id), "account_id": str(account_id),
                "venue": str(venue), "us_market_slug": str(us_market_slug),
                "intent_id": None if intent_id is None else str(intent_id),
                "kind": kind, "venue_order_id": None if venue_order_id is None else str(venue_order_id),
                "search_endpoint": str(search_endpoint),
                "covered_terminal_orders": bool(covered_terminal_orders),
                "results_returned": int(results_returned),
                "window_from_epoch_s": window_from_epoch_s,
                "window_to_epoch_s": window_to_epoch_s}
            differs = [f for f, want in expected.items() if f not in have or have[f] != want]
            for field, value in (("search_scope", search_scope or {}), ("raw", raw or {})):
                stored = have.get(field)
                if isinstance(stored, str):
                    stored = _json.loads(stored)
                if stored != _json.loads(_json.dumps(value, default=str)):
                    differs.append(field)
            stored_time = have.get("read_at")
            if stored_time is None or abs(stored_time.timestamp() - float(read_at)) > 0.000001:
                differs.append("read_at")
            if differs:
                return dict(out, ok=False, refusal=R_EVIDENCE_NOT_BOUND,
                            conflicts=differs, stored=have,
                            why=("evidence id %r already records a DIFFERENT "
                                 "read. Evidence is never rewritten, and reusing "
                                 "its id for another answer would overwrite what "
                                 "the venue actually said" % evidence_id))
            return dict(out, ok=True, written=False, already=True,
                        why="this exact read is already recorded")
        if "absence_searched_terminal" in msg:
            return dict(out, ok=False,
                        refusal=R_EVIDENCE_SEARCH_COULD_NOT_HAVE_FOUND_IT,
                        why=("an absence claim from a search that did not cover "
                             "TERMINAL orders is a contradiction: an order that "
                             "filled immediately is not open, so it had already "
                             "left the set that was searched"))
        if "absence_found_nothing" in msg:
            return dict(out, ok=False, refusal=R_EVIDENCE_NOT_BOUND,
                        why=("an absence claim cannot be recorded from a search "
                             "that RETURNED results"))
        if "named_has_id" in msg:
            return dict(out, ok=False, refusal=R_ORDER_ID_REQUIRED,
                        why=msg[:200])
        raise
    return dict(out, ok=True, written=True, already=False)


def evidence_matches_operation(evidence, reservation):
    """Validate the recorded read against the operation, not its label alone."""
    import datetime
    import json
    import math

    e, r = dict(evidence), dict(reservation)
    bad = []
    for field, expected in (("operation_id", r.get("operation_id")),
                            ("account_id", e.get("group_account")),
                            ("venue", e.get("group_venue")),
                            ("us_market_slug", r.get("us_market_slug")),
                            ("intent_id", r.get("intent_id"))):
        if expected is None or str(e.get(field) or "") != str(expected):
            bad.append(field)
    if e.get("kind") == EV_NO_SUCH_ORDER:
        def epoch(value):
            if isinstance(value, datetime.datetime):
                value = value.timestamp()
            try:
                n = float(value)
                return n if math.isfinite(n) else None
            except (TypeError, ValueError, OverflowError):
                return None
        scope = e.get("search_scope") or {}
        if isinstance(scope, str):
            try:
                scope = json.loads(scope)
            except (TypeError, ValueError):
                scope = {}
        if not isinstance(scope, dict):
            scope = {}
        # A complete search must explicitly correlate this request and include
        # all states/pages. An empty OPEN page cannot satisfy these assertions.
        required = {"operation_id": r.get("operation_id"),
                    "intent_id": r.get("intent_id"),
                    "status": "ALL", "pagination_complete": True}
        for field, value in required.items():
            if value is None or scope.get(field) != value:
                bad.append("search_scope." + field)
        endpoint = str(e.get("search_endpoint") or "").lower()
        if not endpoint or "open" in endpoint or not e.get("covered_terminal_orders"):
            bad.append("terminal_coverage")
        if e.get("results_returned") != 0:
            bad.append("results_returned")
        sent = epoch(e.get("intent_sent_at"))
        start = epoch(e.get("window_from_epoch_s"))
        end = epoch(e.get("window_to_epoch_s"))
        read = epoch(e.get("read_at"))
        # Absence also needs a venue-established visibility watermark. Wall
        # clock time alone says nothing about eventual consistency of a search.
        visible = epoch(scope.get("complete_through_epoch_s"))
        if None in (sent, start, end, read, visible) or not (
                start <= sent <= end <= read and sent <= visible <= end):
            bad.append("search_window_and_visibility")
    return {"ok": not bad, "mismatched": bad}


async def _usable_evidence(conn, *, operation_id, kind, reservation) -> dict:
    """The evidence for this operation that is actually BOUND to its facts.

    BOUND MEANS THE SAME ACCOUNT, THE SAME INSTRUMENT AND THE SAME INTENT. A read
    of a different market, or of another account, says nothing about this
    acquisition however true it is in itself. And for an ABSENCE claim it must
    also have covered terminal orders.
    """
    out: dict = {"kind": kind, "operation_id": str(operation_id)}
    try:
        if not await _has_evidence_schema(conn):
            return dict(out, ok=False, refusal=R_EVIDENCE_SCHEMA_UNAVAILABLE)
    except SchemaUnavailable as exc:
        return dict(out, ok=False, refusal=R_EVIDENCE_SCHEMA_UNAVAILABLE,
                    why=str(exc))
    rows = [dict(r) for r in await conn.fetch(
        "SELECT e.*, g.account_id AS group_account, g.venue AS group_venue, "
        " i.sent_at AS intent_sent_at FROM "
        "  bettor_funded_operation_evidence e "
        "  JOIN bettor_funded_leg_reservations r "
        "       ON r.operation_id = e.operation_id "
        "  JOIN bettor_funded_portfolio_groups g ON g.group_id = r.group_id "
        "  LEFT JOIN bettor_funded_intents i ON i.intent_id = r.intent_id "
        " WHERE e.operation_id = $1 AND e.kind = $2 "
        " ORDER BY e.read_at DESC", str(operation_id), kind)]
    if not rows:
        return dict(out, ok=False, refusal=R_NO_EVIDENCE,
                    why=("no %s evidence is recorded against this operation. A "
                         "caller's assertion is not a substitute" % kind))
    for r in rows:
        check = evidence_matches_operation(r, reservation)
        if check["ok"]:
            return dict(out, ok=True, refusal=None, evidence=r)
    out["evidence_checks"] = [evidence_matches_operation(r, reservation) for r in rows]
    return dict(out, ok=False,
                refusal=(R_EVIDENCE_SEARCH_COULD_NOT_HAVE_FOUND_IT
                         if kind == EV_NO_SUCH_ORDER else R_EVIDENCE_NOT_BOUND),
                candidates=len(rows),
                why=("%d %s row(s) exist for this operation but none is bound to "
                     "its account, instrument and intent%s"
                     % (len(rows), kind,
                        " AND searched terminal orders"
                        if kind == EV_NO_SUCH_ORDER else "")))


async def record_the_venue_named_it(conn, *, operation_id: str,
                                    venue_order_id: str) -> dict:
    """THE VENUE NAMED AN ORDER, so the acquisition happened: CONSUMED.

    `venue_order_id` IS NO LONGER OPTIONAL. It used to default to None, so a
    reservation could reach CONSUMED claiming the venue had named an order while
    naming none -- and CONSUMED is the state that says a real order exists.

    AND DURABLE EVIDENCE IS REQUIRED, bound to this operation's account,
    instrument and intent, naming this very order id.
    """
    out: dict[str, Any] = {"version": VERSION,
                           "operation_id": str(operation_id), "to": CONSUMED}
    if not venue_order_id:
        return dict(out, ok=False, refusal=R_ORDER_ID_REQUIRED,
                    why=("CONSUMED asserts a real order exists at the venue. "
                         "That assertion must carry the venue's own id for it"))
    res = await _fetch(conn, operation_id)
    if res is None:
        return dict(out, ok=False, refusal=R_NO_SUCH_OPERATION)
    ev = await _usable_evidence(conn, operation_id=operation_id, kind=EV_NAMED,
                                reservation=res)
    if not ev.get("ok"):
        return dict(out, ok=False, refusal=ev["refusal"], why=ev.get("why"),
                    evidence_check=ev)
    if str(ev["evidence"]["venue_order_id"]) != str(venue_order_id):
        return dict(out, ok=False, refusal=R_EVIDENCE_NOT_BOUND,
                    why=("the recorded evidence names order %r, not %r"
                         % (ev["evidence"]["venue_order_id"], venue_order_id)))
    got = await _transition(
        conn, operation_id, to=CONSUMED, expect=(SEND_ATTEMPTED, AMBIGUOUS),
        note="%s:%s" % (WHY_VENUE_NAMED_THE_ORDER, venue_order_id))
    if got.get("ok"):
        got["evidence_id"] = ev["evidence"]["evidence_id"]
        got["venue_order_id"] = str(venue_order_id)
    return got


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
                                venue_order_id: str | None = None) -> dict:
    """RESOLVE AN AMBIGUOUS ACQUISITION FROM RECORDED EVIDENCE, never a boolean.

    ── WHAT THIS USED TO BE, AND WHY IT WAS WRONG ───────────────────
    It took `the_venue_has_the_order: bool`. A caller could clear an AMBIGUOUS
    send -- the state that exists precisely because nobody knows whether a live
    order is sitting at the venue -- by passing False. The parameter was named
    for what the caller was "asserting", which is not evidence; it is a comment.

    NOW THE DECISION COMES FROM WHAT WAS WRITTEN DOWN. `record_venue_evidence`
    stores the venue's answer with the endpoint, scope, window, whether terminal
    orders were covered and how many results came back. This function looks for
    evidence BOUND to this operation's account, instrument and intent:

      * a NAMED order, carrying its id      -> CONSUMED
      * searched-and-empty, TERMINAL covered -> RELEASED
      * anything less                        -> refused, and it stays AMBIGUOUS

    AN EMPTY OPEN-ORDERS LIST DOES NOT CLEAR AN AMBIGUOUS SEND. An order that
    filled immediately is not open: it had already left the set that was
    searched, so "nothing found" was never capable of meaning "nothing exists".
    """
    out: dict[str, Any] = {"version": VERSION,
                           "operation_id": str(operation_id)}
    res = await _fetch(conn, operation_id)
    if res is None:
        return dict(out, ok=False, refusal=R_NO_SUCH_OPERATION)
    named = await _usable_evidence(conn, operation_id=operation_id,
                                   kind=EV_NAMED, reservation=res)
    if named.get("ok"):
        return await record_the_venue_named_it(
            conn, operation_id=operation_id,
            venue_order_id=(venue_order_id
                            or named["evidence"]["venue_order_id"]))
    # AN EXPLICIT REFUSAL THE SEND RECORDED, reached again by recovery when
    # the release in the send's own transaction did not complete. It carries
    # its own checks (the intent must be REJECTED with no order id).
    refused = await _usable_evidence(conn, operation_id=operation_id,
                                     kind=EV_REFUSED_IN_ANSWER, reservation=res)
    if refused.get("ok") and res.get("state") == AMBIGUOUS:
        return await release_on_explicit_refusal(conn,
                                                 operation_id=operation_id)
    absent = await _usable_evidence(conn, operation_id=operation_id,
                                    kind=EV_NO_SUCH_ORDER, reservation=res)
    if not absent.get("ok"):
        return dict(out, ok=False, refusal=absent["refusal"],
                    why=absent.get("why"), exposure="PRESERVED",
                    named_check=named, absence_check=absent,
                    what_would_resolve_it=(
                        "either a VENUE_NAMED_THE_ORDER row carrying the order "
                        "id, or a VENUE_HAS_NO_SUCH_ORDER row from a search that "
                        "covered TERMINAL orders for this account and "
                        "instrument"))
    got = await _transition(
        conn, operation_id, to=RELEASED, expect=(AMBIGUOUS,),
        note=WHY_VENUE_HAS_NO_SUCH_ORDER)
    if got.get("ok"):
        got["evidence_id"] = absent["evidence"]["evidence_id"]
        got["searched"] = {k: absent["evidence"].get(k) for k in
                           ("search_endpoint", "covered_terminal_orders",
                            "results_returned", "window_from_epoch_s",
                            "window_to_epoch_s")}
    return got


async def consume_on_operator_naming(conn, *, operation_id: str,
                                    venue_order_id: str) -> dict:
    """CONSUME AN AMBIGUOUS RESERVATION ON AN AUDITED OPERATOR NAMING.

    Requires an `OPERATOR_NAMED_THE_ORDER` row bound to this operation's
    account, instrument and intent, naming this very order -- the database
    accepts one only with the order id, the attester and the audit id -- and
    moves AMBIGUOUS -> CONSUMED with a resolution that says an operator named
    it. `resolve_from_the_venue` never reads this kind.
    """
    out: dict[str, Any] = {"version": VERSION,
                           "operation_id": str(operation_id), "to": CONSUMED}
    if not venue_order_id:
        return dict(out, ok=False, refusal=R_ORDER_ID_REQUIRED)
    res = await _fetch(conn, operation_id)
    if res is None:
        return dict(out, ok=False, refusal=R_NO_SUCH_OPERATION)
    ev = await _usable_evidence(conn, operation_id=operation_id,
                                kind=EV_OPERATOR_NAMED, reservation=res)
    if not ev.get("ok"):
        return dict(out, ok=False, refusal=ev["refusal"], why=ev.get("why"),
                    exposure="PRESERVED", evidence_check=ev)
    if str(ev["evidence"]["venue_order_id"]) != str(venue_order_id):
        return dict(out, ok=False, refusal=R_EVIDENCE_NOT_BOUND,
                    why=("the recorded naming is of order %r, not %r"
                         % (ev["evidence"]["venue_order_id"], venue_order_id)))
    got = await _transition(
        conn, operation_id, to=CONSUMED, expect=(AMBIGUOUS,),
        note="%s:%s:%s" % (WHY_OPERATOR_NAMED_THE_ORDER, venue_order_id,
                           ev["evidence"]["evidence_id"]))
    if got.get("ok"):
        got["evidence_id"] = ev["evidence"]["evidence_id"]
        got["venue_order_id"] = str(venue_order_id)
        got["consumed_on"] = "AN_AUDITED_OPERATOR_NAMING"
    return got


async def release_on_attestation(conn, *, operation_id: str) -> dict:
    """RELEASE AN AMBIGUOUS RESERVATION ON A RECORDED, AUDITED ATTESTATION.

    Not a venue answer and not presented as one. It requires an
    `OPERATOR_ATTESTED_NO_EXPOSURE` row bound to this operation's account,
    instrument and intent -- which the database accepts only with an attester
    and an audit id -- and moves AMBIGUOUS -> RELEASED with a resolution that
    names the attestation. SEND_ATTEMPTED is refused, as everywhere: it must
    first be marked AMBIGUOUS, which says the answer was lost.
    """
    out: dict[str, Any] = {"version": VERSION,
                           "operation_id": str(operation_id), "to": RELEASED}
    res = await _fetch(conn, operation_id)
    if res is None:
        return dict(out, ok=False, refusal=R_NO_SUCH_OPERATION)
    ev = await _usable_evidence(conn, operation_id=operation_id,
                                kind=EV_ATTESTED_NO_EXPOSURE, reservation=res)
    if not ev.get("ok"):
        return dict(out, ok=False, refusal=ev["refusal"], why=ev.get("why"),
                    exposure="PRESERVED", evidence_check=ev)
    got = await _transition(
        conn, operation_id, to=RELEASED, expect=(AMBIGUOUS,),
        note="%s:%s" % (WHY_OPERATOR_ATTESTED_NO_EXPOSURE,
                        ev["evidence"]["evidence_id"]))
    if got.get("ok"):
        got["evidence_id"] = ev["evidence"]["evidence_id"]
        got["released_on"] = "AN_AUDITED_OPERATOR_ATTESTATION"
    return got


async def release_on_explicit_refusal(conn, *, operation_id: str) -> dict:
    """RELEASE AN AMBIGUOUS CLAIM WHOSE SEND THE VENUE EXPLICITLY REFUSED.

    THE GAP THIS CLOSES (Xavier execution map Q4). A hedge refused in the
    response body -- no order id, an explicit refusal status -- left the intent
    REJECTED and the claim AMBIGUOUS, and nothing resolved it: the investigation
    reader skips non-lost-answer rows, `resolve` refuses R_NOT_A_LOST_ACK, and
    `resolve_from_the_venue` needs a named order or a terminal-order search.

    THREE THINGS MUST AGREE, and the release refuses on any one missing: a
    `VENUE_REFUSED_THE_ORDER_IN_ITS_ANSWER` row bound to this operation's
    account, instrument and intent; that intent recorded REJECTED by the book;
    and the intent carrying NO venue order id. An exception, a timeout or an
    unknown status never produces the evidence row, so those stay AMBIGUOUS
    and their exposure stays counted. AMBIGUOUS -> RELEASED only."""
    out: dict[str, Any] = {"version": VERSION,
                           "operation_id": str(operation_id), "to": RELEASED}
    res = await _fetch(conn, operation_id)
    if res is None:
        return dict(out, ok=False, refusal=R_NO_SUCH_OPERATION)
    ev = await _usable_evidence(conn, operation_id=operation_id,
                                kind=EV_REFUSED_IN_ANSWER, reservation=res)
    if not ev.get("ok"):
        return dict(out, ok=False, refusal=ev["refusal"], why=ev.get("why"),
                    exposure="PRESERVED", evidence_check=ev)
    intent = await conn.fetchrow(
        "SELECT state, venue_order_id FROM bettor_funded_intents "
        " WHERE intent_id=$1", res.get("intent_id"))
    if intent is None or intent["state"] != "REJECTED" \
            or intent["venue_order_id"] is not None:
        return dict(out, ok=False, refusal=R_INTENT_NOT_REJECTED,
                    exposure="PRESERVED",
                    intent_state=None if intent is None else intent["state"],
                    why=("the evidence says the venue refused the order, and "
                         "the intent does not agree (state %r, order id %r). "
                         "Two records disagreeing about whether an order "
                         "exists is not a resolution"
                         % (None if intent is None else intent["state"],
                            None if intent is None
                            else intent["venue_order_id"])))
    got = await _transition(
        conn, operation_id, to=RELEASED, expect=(AMBIGUOUS,),
        note="%s:%s" % (WHY_VENUE_REFUSED_IN_ITS_ANSWER,
                        ev["evidence"]["evidence_id"]))
    if got.get("ok"):
        got["evidence_id"] = ev["evidence"]["evidence_id"]
        got["released_on"] = "THE_VENUES_EXPLICIT_REFUSAL_IN_ITS_ANSWER"
    return got


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
        "  LEFT JOIN bettor_funded_intents i ON i.intent_id = r.intent_id "
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


#: ── THE FIELDS A PLAN AND ITS RESERVATION MUST AGREE ON ─────────────
#:
#: A reservation the risk check is about to EXCLUDE from its sums, on the
#: grounds that the plan already counts it, must actually be the same
#: acquisition. These are the four facts that make it so. A mismatch is a
#: REFUSAL, not an arithmetic adjustment: the plan and the claim disagree about
#: what is being bought, and neither can be trusted to stand for the other.
PLAN_FIELDS = ("us_market_slug", "quantity", "limit_price", "collateral_usd")
R_PLAN_DOES_NOT_MATCH_RESERVATION = "THE_PLAN_AND_ITS_RESERVATION_DISAGREE"
R_NOT_HELD_SO_ALREADY_SUBMITTED = "THAT_OPERATION_HAS_ALREADY_LEFT_HELD"


def plan_matches(reservation: dict, plan: dict) -> dict:
    """DOES THIS RESERVATION NAME THE SAME ACQUISITION AS THIS PLAN.

    Used at the risk boundary, where the answer decides whether the plan's
    collateral and the reservation's collateral are ONE number or TWO. Getting
    that wrong in the permissive direction double-counts the acquisition and
    refuses an affordable order; getting it wrong in the other direction lets a
    plan for $50 hide behind a reservation for $5.
    """
    res, want = dict(reservation or {}), dict(plan or {})
    clashes = []
    for f in PLAN_FIELDS:
        a, b = res.get(f), want.get(f)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            same = abs(float(a) - float(b)) < 1e-9
        else:
            same = a is not None and b is not None and str(a) == str(b)
        if not same:
            clashes.append({"field": f, "reservation": a, "plan": b})
    # ── AND THE SIDE, WHICH THE FOUR FIELDS ABOVE CANNOT SEPARATE ────
    #
    # At a wire price of 0.50 a LONG and a SHORT on one slug agree on slug,
    # quantity, price and collateral, so the comparison above passed a plan for
    # one side against a reservation for the other and the rails counted them as
    # one acquisition. The execution plan names its side `intent`; the
    # reservation (migration 136) names it `order_intent`. A reservation that
    # stated a side must be met by a plan on that side -- including a plan that
    # states none, because an unstated side is not agreement.
    compared = list(PLAN_FIELDS)
    if res.get("order_intent") is not None:
        compared.append("order_intent")
        if str(res.get("order_intent")) != str(want.get("intent")):
            clashes.append({"field": "order_intent",
                            "reservation": res.get("order_intent"),
                            "plan": want.get("intent")})
    return {"ok": not clashes, "conflicts": clashes, "compared": compared}


async def reserved_collateral_usd(conn, *,
                                  account_id: str | None = None,
                                  excluding_operation_id: str | None = None,
                                  plan: dict | None = None) -> dict:
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
    # ── AN EXPLICIT ok/refusal, BECAUSE A CONSUMER MUST FAIL CLOSED ──
    # This function had NO `ok` field, so `bettor_funded_execution.check_rails`
    # read every successful call as unreadable and put four capital rails into
    # `unmeasured`. Fail-closed is right, but only when the signal is real; a
    # consumer cannot distinguish an outage from a helper with no contract.
    try:
        if not await _has_schema(conn):
            return {"version": VERSION, "ok": False,
                    "refusal": R_SCHEMA_UNAVAILABLE,
                    "reserved_usd": 0.0, "reserved_by_market_usd": {},
                    "reserved_by_event_usd": {},
                    "why": ("this database has no reservation tables, so there "
                            "are no reservations to count. That is a complete "
                            "answer, not an outage")}
    except SchemaUnavailable as exc:
        return {"version": VERSION, "ok": False,
                "refusal": "THE_RESERVATION_TABLES_COULD_NOT_BE_READ",
                "reserved_usd": None, "why": str(exc)}
    sql = ("SELECT r.*, g.account_id, g.event_key "
           "  FROM bettor_funded_leg_reservations r "
           "  JOIN bettor_funded_portfolio_groups g ON g.group_id = r.group_id "
        "  LEFT JOIN bettor_funded_intents i ON i.intent_id = r.intent_id "
           " WHERE r.state = ANY($1::text[])")
    args: list = [list(LIVE_STATES)]
    if account_id is not None:
        args.append(str(account_id))
        sql += " AND g.account_id = $%d" % len(args)
    rows = [_row(r) for r in await conn.fetch(sql, *args)]
    # ── THE OPERATION BEING SUBMITTED, COUNTED BY THE PLAN AND NOT HERE ──
    #
    # THE DEFECT THIS CLOSES, and it was reproduced. `acquire_second_leg` takes a
    # HELD reservation and then calls `submit_for_decision`, whose `check_rails`
    # adds EVERY HELD reservation *and* `plan["collateral_usd"]`. For the
    # operation being submitted those are the same acquisition, so a $10 existing
    # exposure plus a $5 hedge measured $20 instead of $15 -- and the rails
    # refused affordable orders, which is a blind control in the direction that
    # looks safe and is not: an operator would raise the limit to clear it.
    #
    # THE EXCLUSION IS EARNED, NOT ASSERTED. It applies only when the reservation
    # is still HELD (past HELD it has an intent row and is not summed here at
    # all) and only when the plan and the reservation agree on the instrument,
    # the quantity, the price and the collateral. A disagreement REFUSES: if the
    # plan is for $50 and the reservation claims $5, neither may stand for the
    # other.
    excluded = None
    if excluding_operation_id is not None:
        mine = [r for r in rows
                if str(r["operation_id"]) == str(excluding_operation_id)]
        if not mine:
            return {"version": VERSION, "ok": False,
                    "refusal": R_NO_SUCH_OPERATION,
                    "operation_id": str(excluding_operation_id),
                    "reserved_usd": None,
                    "why": ("the risk check was told to count this operation "
                            "through the plan instead of through its "
                            "reservation, and there is no live reservation for "
                            "it. Proceeding would count it NEITHER way")}
        res = mine[0]
        if res["state"] != HELD:
            return {"version": VERSION, "ok": False,
                    "refusal": R_NOT_HELD_SO_ALREADY_SUBMITTED,
                    "operation_id": str(excluding_operation_id),
                    "state": res["state"], "intent_id": res.get("intent_id"),
                    "reserved_usd": None,
                    "why": ("this operation is at %s, so an intent already "
                            "carries its exposure and a plan for it would be "
                            "the same acquisition a third time. A submission "
                            "for it is a replay, not a new order" % res["state"])}
        chk = plan_matches(res, plan or {})
        if not chk["ok"]:
            return {"version": VERSION, "ok": False,
                    "refusal": R_PLAN_DOES_NOT_MATCH_RESERVATION,
                    "operation_id": str(excluding_operation_id),
                    "conflicts": chk["conflicts"], "reserved_usd": None,
                    "why": ("the plan and the reservation disagree on %s. The "
                            "exclusion below rests on them being ONE "
                            "acquisition, so a mismatch is refused rather than "
                            "netted" % ", ".join(
                                c["field"] for c in chk["conflicts"]))}
        excluded = {"operation_id": res["operation_id"],
                    "collateral_usd": res["collateral_usd"],
                    "us_market_slug": res["us_market_slug"],
                    "event_key": res.get("event_key"),
                    "state": res["state"],
                    "counted_by": "THE_PLANS_OWN_COLLATERAL",
                    "plan_check": chk}
        rows = [r for r in rows
                if str(r["operation_id"]) != str(excluding_operation_id)]
    counted = [r for r in rows
               if r["state"] in STATES_THAT_COUNT_AS_COMMITTED_CAPITAL]
    not_counted = [r for r in rows if r not in counted]
    # ── PER MARKET AND PER EVENT, for the rails that are scoped that way ──
    # The account-wide total is not enough for `MAX_MARKET_EXPOSURE` or
    # `MAX_EVENT_EXPOSURE`: a reservation on one instrument must not raise the
    # measured exposure of another. `event_key` comes from the reservation's
    # GROUP, which is the only place it is recorded.
    by_market: dict[str, float] = {}
    by_event: dict[str, float] = {}
    for r in counted:
        mk = str(r["us_market_slug"])
        by_market[mk] = round(by_market.get(mk, 0.0) + r["collateral_usd"], 6)
        ev = r.get("event_key")
        if ev:
            by_event[str(ev)] = round(by_event.get(str(ev), 0.0)
                                      + r["collateral_usd"], 6)
    return {
        "ok": True,
        "refusal": None,
        "excluded_because_the_plan_counts_it": excluded,
        "every_other_reservation_is_retained": (
            "the exclusion is one operation. Every other live claim on the "
            "account is still summed, which is the whole point of the reading"),
        "reserved_by_market_usd": by_market,
        "reserved_by_event_usd": by_event,
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
