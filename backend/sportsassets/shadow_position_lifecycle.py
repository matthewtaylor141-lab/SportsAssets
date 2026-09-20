"""A POSITION'S STATE IS FOLDED FROM ITS EVENTS, NEVER STORED.

Owner directive 2026-09-20 §1: "Implement position lifecycle through
append-only events... Do not invent a second economic ledger if the
current event ledger can represent this correctly."

WHY DERIVE RATHER THAN STORE. `bettor_experimental_positions.status` is
a stored conclusion on an append-only row, which is why it has been
frozen at 'OPEN' for every position ever created -- an UPDATE is
refused by the trigger. Storing state on an immutable row is what
produced the defect. So state is computed by folding the events in
order, every time it is read. A derived state cannot drift from its
evidence.

THE STATES, and each one means something a reader can check:

  OPEN                    opened, horizon not yet reached
  EXIT_MECHANISM_UNAVAILABLE
                          opened under infrastructure that could not
                          exit it. NOT a failure to exit -- there was
                          nothing to fail.
  EXIT_ELIGIBLE           the horizon has passed and an exit is owed
  EXIT_PENDING            an exit was decided, not yet executed
  PARTIALLY_EXITED        some quantity released, some still held
  CLOSED                  all quantity released through real exits
  SETTLED                 the venue resolved it

§5 IS ENFORCED HERE BY CONSTRUCTION: realized P&L is computed only from
EXIT_EXECUTION events that actually moved quantity against a named
book. A horizon markout can never become realized P&L through this
module, because the fold never reads a markout.
"""

from __future__ import annotations

from datetime import timezone

OPEN = "OPEN"
EXIT_MECHANISM_UNAVAILABLE = "EXIT_MECHANISM_UNAVAILABLE"
EXIT_ELIGIBLE = "EXIT_ELIGIBLE"
EXIT_PENDING = "EXIT_PENDING"
PARTIALLY_EXITED = "PARTIALLY_EXITED"
CLOSED = "CLOSED"
SETTLED = "SETTLED"

OPENED = "POSITION_OPENED"
UNAVAILABLE = "EXIT_MECHANISM_UNAVAILABLE_AT_ENTRY"
ELIGIBLE = "EXIT_BECAME_ELIGIBLE"
DECIDED = "EXIT_DECISION"
EXECUTED = "EXIT_EXECUTION"
CLOSED_EVENT = "POSITION_CLOSED"
SETTLED_EVENT = "SETTLED"

# Quantity below which a residual is not a residual. The venue's
# scaled-integer quantities are whole contracts, so anything under one
# contract is float dust from the vwap arithmetic, not a holding.
DUST_QTY = 1e-9

EVENTS_SQL = """
    SELECT e.position_event_id, e.position_id, e.experiment_id,
           e.market_id, e.event_type, e.event_at, e.sequence_no,
           e.qty_delta, e.notional_delta_usd, e.vwap,
           e.intended_qty, e.filled_qty, e.unfilled_qty,
           e.execution_status, e.book_sha, e.latency_status, e.why
      FROM bettor_experimental_position_events e
     ORDER BY e.position_id, e.event_at, e.sequence_no
"""


async def events(pool) -> list:
    return [dict(r) for r in await pool.fetch(EVENTS_SQL)]


def _f(v):
    return None if v is None else float(v)


def fold(position_events) -> dict:
    """One position's current state, from its events in order."""
    rows = sorted(position_events or (),
                  key=lambda e: (e["event_at"], e["sequence_no"]))
    if not rows:
        return {"state": None, "why": "no events"}

    opened_qty = 0.0
    entry_notional = 0.0
    released_qty = 0.0
    exit_value = 0.0
    unavailable = False
    eligible_at = None
    decided_open = False
    closed_at = None
    settled_at = None
    opened_at = None
    last_exit_at = None

    for e in rows:
        kind = e["event_type"]
        if kind == OPENED:
            opened_qty += _f(e["qty_delta"]) or 0.0
            entry_notional += _f(e["notional_delta_usd"]) or 0.0
            opened_at = opened_at or e["event_at"]
        elif kind == UNAVAILABLE:
            unavailable = True
        elif kind == ELIGIBLE:
            eligible_at = eligible_at or e["event_at"]
        elif kind == DECIDED:
            decided_open = True
        elif kind == EXECUTED:
            # ONLY A REAL RELEASE COUNTS. An EXIT_EXECUTION recorded
            # NOT_IDENTIFIED moved nothing: §4 forbids interpolating an
            # exit, so it clears the pending decision and leaves the
            # quantity exactly where it was.
            decided_open = False
            if e["execution_status"] in ("FILLED", "PARTIAL"):
                released_qty += abs(_f(e["qty_delta"]) or 0.0)
                exit_value += abs(_f(e["notional_delta_usd"]) or 0.0)
                last_exit_at = e["event_at"]
        elif kind == CLOSED_EVENT:
            closed_at = e["event_at"]
        elif kind == SETTLED_EVENT:
            settled_at = e["event_at"]

    remaining = opened_qty - released_qty
    state = _state(remaining=remaining, released=released_qty,
                   settled_at=settled_at, closed_at=closed_at,
                   decided_open=decided_open, eligible_at=eligible_at,
                   unavailable=unavailable)

    return {
        "positionId": rows[0]["position_id"],
        "experimentId": rows[0]["experiment_id"],
        "marketId": rows[0]["market_id"],
        "state": state,
        "openedAt": opened_at,
        "openedQty": round(opened_qty, 9),
        "releasedQty": round(released_qty, 9),
        "remainingQty": round(max(remaining, 0.0), 9),
        "entryNotionalUsd": round(entry_notional, 6),
        # §5: realized ONLY from executions that moved quantity against
        # a named book. Never from a markout.
        "exitExecutedValueUsd": (round(exit_value, 6) if released_qty
                                 else None),
        "realizedShadowPnlUsd": (
            None if released_qty <= DUST_QTY else
            round(exit_value - entry_notional * (released_qty / opened_qty),
                  6) if opened_qty else None),
        "realizedBasis": (
            "exit executed value minus the pro-rata share of entry "
            "notional released; fees are not modelled in this lane and "
            "are therefore not netted, which is stated rather than "
            "assumed to be zero"),
        "capitalReleasedUsd": (
            0.0 if not opened_qty else
            round(entry_notional * (released_qty / opened_qty), 6)),
        "exitEligibleAt": eligible_at,
        "lastExitAt": last_exit_at,
        "closedAt": closed_at,
        "settledAt": settled_at,
        "exitMechanismUnavailableAtEntry": unavailable,
        "why": (
            "opened under infrastructure that could not execute the "
            "declared exit rule; this is the condition of entry, not a "
            "missed exit" if state == EXIT_MECHANISM_UNAVAILABLE
            else None),
    }


def _state(*, remaining, released, settled_at, closed_at, decided_open,
           eligible_at, unavailable):
    if settled_at is not None:
        return SETTLED
    if closed_at is not None or remaining <= DUST_QTY:
        return CLOSED
    if released > DUST_QTY:
        return PARTIALLY_EXITED
    if decided_open:
        return EXIT_PENDING
    # UNAVAILABLE OUTRANKS ELIGIBLE. A position whose horizon passed
    # while no exit could run is not "owed an exit the system declined
    # to take"; it is a position the system was never able to close.
    if unavailable:
        return EXIT_MECHANISM_UNAVAILABLE
    if eligible_at is not None:
        return EXIT_ELIGIBLE
    return OPEN


def by_position(all_events) -> dict:
    grouped = {}
    for e in all_events or ():
        grouped.setdefault(e["position_id"], []).append(e)
    return {pid: fold(rows) for pid, rows in grouped.items()}


# ── §13: the lifecycle census and §6's capital, with releases ────────


def lifecycle(folded, *, experiment) -> dict:
    """§13's tiles for one experiment, and §6's capital with releases."""
    mine = [f for f in (folded or {}).values()
            if f.get("experimentId") == experiment]
    counts = {s: 0 for s in (OPEN, EXIT_MECHANISM_UNAVAILABLE,
                             EXIT_ELIGIBLE, EXIT_PENDING,
                             PARTIALLY_EXITED, CLOSED, SETTLED)}
    for f in mine:
        counts[f["state"]] = counts.get(f["state"], 0) + 1

    entry_played = sum(f["entryNotionalUsd"] for f in mine)
    released = sum(f["capitalReleasedUsd"] for f in mine)
    exit_value = sum(f["exitExecutedValueUsd"] or 0.0 for f in mine)
    realized = [f["realizedShadowPnlUsd"] for f in mine
                if f["realizedShadowPnlUsd"] is not None]

    return {
        "OPEN_POSITIONS": counts[OPEN],
        "EXIT_MECHANISM_UNAVAILABLE": counts[EXIT_MECHANISM_UNAVAILABLE],
        "EXIT_ELIGIBLE": counts[EXIT_ELIGIBLE],
        "EXIT_PENDING": counts[EXIT_PENDING],
        "PARTIALLY_EXITED": counts[PARTIALLY_EXITED],
        "CLOSED_POSITIONS": counts[CLOSED],
        "SETTLED_POSITIONS": counts[SETTLED],
        "ENTRY_NOTIONAL_PLAYED_USD": round(entry_played, 6),
        "CAPITAL_RELEASED_USD": round(released, 6),
        # §6: turnover is entry plus exit value, not entry counted
        # twice. With no exits it equals entry notional and says so.
        "GROSS_TRADING_TURNOVER_USD": round(entry_played + exit_value, 6),
        "REALIZED_PNL_USD": (round(sum(realized), 6) if realized else None),
        "REALIZED_PNL_STATUS": ("REALIZED" if realized
                                else "NOT_APPLICABLE"),
        "SETTLED_PNL_USD": None,
        "SETTLED_PNL_STATUS": "NOT_APPLICABLE",
        "why": (
            "no position has released any capital, so turnover equals "
            "entry notional and realized P&L is not applicable -- not "
            "zero" if released <= 0 else None),
    }


def capital_with_releases(folded, *, experiment, now) -> dict:
    """§6's capital, now aware that a close frees dollars.

    Identical arithmetic to the entry-only version while nothing has
    closed, and correct the moment something does -- which is the
    property that matters, since the all-open case is the one that
    misleads.
    """
    mine = [f for f in (folded or {}).values()
            if f.get("experimentId") == experiment]
    if not mine:
        return {"CURRENT_CAPITAL_DEPLOYED_USD": 0.0,
                "PEAK_CAPITAL_DEPLOYED_USD": 0.0,
                "AVERAGE_CAPITAL_DEPLOYED_USD": 0.0,
                "CAPITAL_HOURS": 0.0, "CAPITAL_TURNS": None,
                "CAPITAL_RELEASED_USD": 0.0}

    timeline = []
    for f in mine:
        timeline.append((_utc(f["openedAt"]), f["entryNotionalUsd"]))
        if f["capitalReleasedUsd"] and f["lastExitAt"] is not None:
            timeline.append((_utc(f["lastExitAt"]),
                             -f["capitalReleasedUsd"]))
    timeline.sort(key=lambda t: t[0])

    deployed = peak = 0.0
    capital_seconds = 0.0
    last_at = timeline[0][0]
    for at, delta in timeline:
        capital_seconds += deployed * (at - last_at).total_seconds()
        deployed += delta
        peak = max(peak, deployed)
        last_at = at
    now = _utc(now)
    if now > last_at:
        capital_seconds += deployed * (now - last_at).total_seconds()

    entry_played = sum(f["entryNotionalUsd"] for f in mine)
    released = sum(f["capitalReleasedUsd"] for f in mine)
    elapsed_s = (now - timeline[0][0]).total_seconds()

    return {
        "CURRENT_CAPITAL_DEPLOYED_USD": round(deployed, 6),
        "PEAK_CAPITAL_DEPLOYED_USD": round(peak, 6),
        "AVERAGE_CAPITAL_DEPLOYED_USD": (
            0.0 if elapsed_s <= 0 else round(capital_seconds / elapsed_s, 6)),
        "CAPITAL_HOURS": round(capital_seconds / 3600.0, 6),
        "CAPITAL_RELEASED_USD": round(released, 6),
        "ENTRY_NOTIONAL_PLAYED_USD": round(entry_played, 6),
        # §6: "Do not derive recycling from entry count."
        "CAPITAL_TURNS": (None if peak <= 0
                          else round(entry_played / peak, 6)),
        "CAPITAL_TURNS_BASIS": (
            "entry notional over PEAK capital actually required; 1.0 "
            "means the same dollars were never re-used"),
    }


def _utc(at):
    if at is None:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
