"""CAPITAL, CONCENTRATION AND SETTLEMENT SEMANTICS FOR ONE EXPERIMENT.

Owner directive 2026-09-20, after the first X1 cohort:

  S3  "Do not label the 300S markout simply 'P&L' without its status."
  S4  "Do not infer these from ENTRY_NOTIONAL_PLAYED. Four entries in
       the same market may reuse or overlap capital. Use the actual
       position-event time series."
  S5  "Do not represent four entries in one market as four independent
       samples."
  S7  "EXACT_SAME_CONTRACT proves instrument identity. It does NOT
       prove that we have correctly interpreted the proposition's
       settlement semantics."

WHAT THE LEDGER ACTUALLY OFFERS, and it decides the shape of all of
this. `bettor_experimental_positions` is append-only and carries ONE
event per position: `opened_at`. There is no `closed_at`, no exit row,
and the only write anywhere in the codebase is the INSERT -- so a
position's status can never leave 'OPEN', because an UPDATE would be
refused by the append-only trigger.

That is not a gap in this module; it is a finding about the lane, and
it is reported rather than papered over:

  * every position is still open, so CURRENT == PEAK and capital is
    strictly ADDITIVE -- four entries in one market overlap in time and
    each one ties up its own dollars;
  * CAPITAL_TURNS is therefore exactly 1.0, and that number means "no
    capital has ever been recycled", not "capital was turned over once";
  * REALIZED_PNL and SETTLED_PNL are NOT_APPLICABLE rather than 0.00,
    because nothing has exited and nothing has settled.

If an exit path is ever added, the same functions compute real turns
and real realized P&L from the same series without being edited.

NOTHING HERE IS INFERRED FROM ENTRY NOTIONAL. Capital deployed is the
integral of the open book over time, computed from `opened_at` and
whatever close event exists, which today is none.
"""

from __future__ import annotations

from datetime import timezone

# ── the four economic statuses, never collapsed into "P&L" ───────────

NOT_APPLICABLE = "NOT_APPLICABLE"
UNREALIZED_MARKOUT = "UNREALIZED_EXECUTABLE_MARKOUT"
REALIZED = "REALIZED"
SETTLED = "SETTLED"

# S7. Identity and settlement semantics are separate claims.
SETTLEMENT_OK = "IDENTIFIED"
SETTLEMENT_CONFLICT = "CONFLICTING_VENUE_PROSE"
SETTLEMENT_UNKNOWN = "NOT_IDENTIFIED"

POSITION_EVENTS_SQL = """
    SELECT p.position_id, p.experiment_id, p.market_id, p.side,
           p.opened_at, p.entry_qty, p.entry_vwap, p.entry_notional_usd,
           p.status, d.bettor_opportunity_id, d.institutional_instrument_id
      FROM bettor_experimental_positions p
      JOIN bettor_experimental_decisions d
        ON d.experimental_decision_id = p.experimental_decision_id
     ORDER BY p.opened_at
"""


async def position_events(pool) -> list:
    rows = await pool.fetch(POSITION_EVENTS_SQL)
    return [dict(r) for r in rows]


def _f(v):
    return None if v is None else float(v)


def _closed_at(p):
    """When this position stopped tying up capital.

    None while it is still open. There is no close event in the ledger
    today, so this is None for every row -- stated as a lookup rather
    than hardcoded so an exit path lights it up without an edit here.
    """
    return p.get("closed_at") or p.get("settled_at")


# ── S4: capital from the position time series ────────────────────────


def capital(positions, *, now) -> dict:
    """Capital deployed over time, from the open/close events only.

    The integral is computed by walking the event timeline rather than
    by summing entries: two positions open at once tie up both sets of
    dollars, and a position that closed before another opened does not.
    """
    positions = list(positions or ())
    if not positions:
        return {
            "CURRENT_CAPITAL_DEPLOYED_USD": 0.0,
            "PEAK_CAPITAL_DEPLOYED_USD": 0.0,
            "AVERAGE_CAPITAL_DEPLOYED_USD": 0.0,
            "CAPITAL_HOURS": 0.0,
            "CAPITAL_TURNS": None,
            "ENTRY_NOTIONAL_PLAYED_USD": 0.0,
            "OPEN_POSITIONS": 0,
            "CLOSED_POSITIONS": 0,
            "basis": "POSITION_EVENT_TIME_SERIES",
            "why": "no positions",
        }

    # THE EVENT TIMELINE. +notional when a position opens, -notional
    # when it closes. Nothing else moves capital.
    events = []
    for p in positions:
        amount = _f(p["entry_notional_usd"]) or 0.0
        events.append((_utc(p["opened_at"]), amount))
        closed = _closed_at(p)
        if closed is not None:
            events.append((_utc(closed), -amount))
    events.sort(key=lambda e: e[0])

    deployed = 0.0
    peak = 0.0
    capital_seconds = 0.0
    last_at = events[0][0]
    for at, delta in events:
        capital_seconds += deployed * (at - last_at).total_seconds()
        deployed += delta
        peak = max(peak, deployed)
        last_at = at
    # And the tail from the final event to now, which is where most of
    # an all-open book's capital-hours actually live.
    now = _utc(now)
    if now > last_at:
        capital_seconds += deployed * (now - last_at).total_seconds()

    entry_played = sum((_f(p["entry_notional_usd"]) or 0.0)
                       for p in positions)
    elapsed_s = (now - events[0][0]).total_seconds()
    closed_n = sum(1 for p in positions if _closed_at(p) is not None)

    return {
        "CURRENT_CAPITAL_DEPLOYED_USD": round(deployed, 6),
        "PEAK_CAPITAL_DEPLOYED_USD": round(peak, 6),
        # Time-weighted, NOT the mean of the entries.
        "AVERAGE_CAPITAL_DEPLOYED_USD": (
            0.0 if elapsed_s <= 0 else round(capital_seconds / elapsed_s, 6)),
        "CAPITAL_HOURS": round(capital_seconds / 3600.0, 6),
        # Entry notional over peak capital: how many times the same
        # dollars were re-used. 1.0 means they never were.
        "CAPITAL_TURNS": (None if peak <= 0
                          else round(entry_played / peak, 6)),
        "ENTRY_NOTIONAL_PLAYED_USD": round(entry_played, 6),
        "OPEN_POSITIONS": len(positions) - closed_n,
        "CLOSED_POSITIONS": closed_n,
        "FIRST_OPENED_AT": events[0][0],
        "ELAPSED_HOURS": round(elapsed_s / 3600.0, 6),
        "basis": "POSITION_EVENT_TIME_SERIES",
        "why": (
            "every position is still open, so capital is strictly "
            "additive and CURRENT == PEAK; CAPITAL_TURNS of 1.0 means "
            "no capital has been recycled, not that it turned over once"
            if closed_n == 0 else None),
    }


def _utc(at):
    if at is None:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


# ── S5: concentration ────────────────────────────────────────────────


def concentration(positions) -> dict:
    """Four entries in one market are not four independent samples."""
    positions = list(positions or ())
    if not positions:
        return {"POSITIONS": 0, "UNIQUE_MARKETS_TRADED": 0,
                "UNIQUE_EVENTS_TRADED": 0, "INDEPENDENT_EVENTS": 0,
                "PER_MARKET": [], "MAX_MARKET_CONCENTRATION_PCT": None,
                "MAX_EVENT_CONCENTRATION_PCT": None}

    total = sum((_f(p["entry_notional_usd"]) or 0.0) for p in positions)
    by_market = {}
    by_event = {}
    for p in positions:
        amount = _f(p["entry_notional_usd"]) or 0.0
        m = by_market.setdefault(p["market_id"], {"n": 0, "notional": 0.0})
        m["n"] += 1
        m["notional"] += amount
        ev = _event_of(p)
        e = by_event.setdefault(ev, {"n": 0, "notional": 0.0})
        e["n"] += 1
        e["notional"] += amount

    per_market = sorted(
        ({"market": k, "POSITIONS_PER_MARKET": v["n"],
          "NOTIONAL_PER_MARKET_USD": round(v["notional"], 6),
          "SHARE_PCT": (None if total <= 0
                        else round(100.0 * v["notional"] / total, 4))}
         for k, v in by_market.items()),
        key=lambda r: -r["NOTIONAL_PER_MARKET_USD"])

    return {
        "POSITIONS": len(positions),
        "UNIQUE_MARKETS_TRADED": len(by_market),
        "UNIQUE_EVENTS_TRADED": len(by_event),
        # THE NUMBER A STATISTICAL CLAIM MAY USE. Four positions in one
        # market are one market's worth of evidence, and the smaller
        # number is the honest denominator.
        "INDEPENDENT_EVENTS": len(by_event),
        "PER_MARKET": per_market,
        "MAX_MARKET_CONCENTRATION_PCT": (
            per_market[0]["SHARE_PCT"] if per_market else None),
        "MAX_EVENT_CONCENTRATION_PCT": (
            None if total <= 0 else round(
                100.0 * max(e["notional"] for e in by_event.values())
                / total, 4)),
        "why": (
            "positions in the same market share one outcome; they are "
            "repeated exposure to a single resolution, not independent "
            "samples, and a statistical claim must use "
            "INDEPENDENT_EVENTS as its denominator"),
    }


def _event_of(p) -> str:
    """The resolving event a market belongs to.

    Taken from the market symbol's own grammar: the venue's symbols are
    <family>-<league>-<teams>-<date>[-<segment>]-<side>-<line>, so the
    first five dash-separated parts name the contest. Deterministic and
    venue-native -- no fuzzy matching, in keeping with the identity
    rules this lane already follows.
    """
    parts = (p.get("market_id") or "").split("-")
    return "-".join(parts[:5]) if len(parts) >= 5 else (
        p.get("market_id") or "UNKNOWN")


# ── S3: what the markout is, and what it is not ──────────────────────


def economics(*, entry_notional_usd, eligible_markouts, realized_usd=None,
              settled_usd=None) -> dict:
    """The four figures S3 requires, each carrying its own status.

    `eligible_markouts` is {horizon: executable_usd} and has ALREADY
    passed both gates. Nothing here re-decides eligibility; it only
    refuses to call the result "P&L".
    """
    entry = float(entry_notional_usd or 0.0)
    out = {
        "ENTRY_NOTIONAL_PLAYED_USD": round(entry, 6),
        # NEVER "P&L". This is where the position would have been
        # marked, not money that has come back.
        "CURRENT_EXECUTABLE_MARKOUT": {},
        "REALIZED_PNL_USD": (None if realized_usd is None
                             else round(float(realized_usd), 6)),
        "REALIZED_PNL_STATUS": (NOT_APPLICABLE if realized_usd is None
                                else REALIZED),
        "SETTLED_PNL_USD": (None if settled_usd is None
                            else round(float(settled_usd), 6)),
        "SETTLED_PNL_STATUS": (NOT_APPLICABLE if settled_usd is None
                               else SETTLED),
        "why": (
            "an executable markout is where the position could have "
            "been exited at that instant on the observed book. It is "
            "not realized P&L and not settlement: no position has "
            "exited and none has settled."),
    }
    for horizon, usd in sorted((eligible_markouts or {}).items()):
        out["CURRENT_EXECUTABLE_MARKOUT"][horizon] = {
            "EXECUTABLE_MARKOUT_USD": (None if usd is None
                                       else round(float(usd), 6)),
            "RETURN_ON_ENTRY_NOTIONAL": (
                None if usd is None or entry <= 0 else round(
                    float(usd) / entry, 6)),
            "STATUS": UNREALIZED_MARKOUT,
        }
    return out


# ── S7: settlement semantics, separate from identity ─────────────────

SETTLEMENT_SQL = """
    SELECT b.market_id, b.outcome_leg, b.identity_status,
           b.settlement_prose_conflict
      FROM bettor_identity_bindings b
     WHERE b.market_id = ANY($1::text[])
       AND b.settlement_prose_conflict IS NOT NULL
"""


async def settlement_semantics(pool, markets) -> dict:
    """Does the venue's own prose agree with itself on these markets?

    EXACT_SAME_CONTRACT establishes WHICH instrument is held. It says
    nothing about whether we have read the proposition's settlement
    rule correctly, and on at least one market the venue publishes two
    rule texts that describe opposite propositions. Kept apart, and
    attached to the cohort rather than to the identity verdict.
    """
    markets = sorted(set(markets or ()))
    if not markets:
        return {"SETTLEMENT_SEMANTICS_STATUS": SETTLEMENT_UNKNOWN,
                "conflicts": [], "markets": 0}
    rows = await pool.fetch(SETTLEMENT_SQL, markets)
    conflicts = [{"market": r["market_id"], "leg": r["outcome_leg"],
                  "identityStatus": r["identity_status"],
                  "conflict": r["settlement_prose_conflict"]}
                 for r in rows]
    return {
        "SETTLEMENT_SEMANTICS_STATUS": (SETTLEMENT_CONFLICT if conflicts
                                        else SETTLEMENT_OK),
        "conflicts": conflicts,
        "markets": len(markets),
        "identityIsNotSettlement": (
            "EXACT_SAME_CONTRACT proves instrument identity. It does "
            "not prove the settlement proposition has been read "
            "correctly."),
        "settlementPnlUsable": not conflicts,
        "why": (
            "the venue publishes two rule texts describing opposite "
            "propositions on this market, so settlement P&L is not "
            "usable here until the authoritative interpretation is "
            "identified. Entry and markout evidence stand: they are "
            "price evidence and do not depend on the settlement rule."
            if conflicts else None),
    }
