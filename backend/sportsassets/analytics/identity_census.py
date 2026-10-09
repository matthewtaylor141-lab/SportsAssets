"""THE POSITIONS IDENTITY-DEBT CENSUS: what the analytics persist dead-letters, counted.

WHY THIS EXISTS. `engine._persist_positions` drops every (whale, token)
position state that still has no condition_id after the token-catalog rescue
(the 2026-08-11/12 incident: one NULL froze the whole snapshot). It said so
with one WARNING per cycle -- "10902 row(s) still missing condition_id after
token-catalog rescue -- dead-lettered" every ~6 minutes on release 732cc0c6
(workers log 2026-10-09 04:20-05:20Z) -- a number with no owner, no age and
no answer to the only question that matters: does any ACTIVE position of OURS
have no market identity?

WHAT IT IS NOW. A counted, classified census of the dead-lettered states:

    OWNER    TRACKED_RESEARCH_WALLET  a roster wallet (whales); the `trades`
                                      ledger that rebuild_positions replays
                                      holds ONLY roster wallets
             OWN_BOOK                 a roster row whose address is one of
                                      our own configured wallets (pm_funder)

    CLASS    FLAT_ZERO_SIZE           no shares left (sold out)       HISTORICAL_DEBT
             RESOLVED                 resolved                         HISTORICAL_DEBT
             OPEN_NO_FILL_WITHIN_30D  shares left, no fill in 30 days HISTORICAL_DEBT
             OPEN_FILLED_WITHIN_30D   shares left, a fill in 30 days  ACTIVE

Beside it, OUR OWN BOOKS (ACTUAL and PAPER, read directly from their own
tables every cycle -- see OWN_BOOKS below): our positions are never in the
tracked wallets' replay, so the roster owner alone could never see them.

It is logged when it CHANGES (its signature: the counts by owner and class,
our own books' unknown counts and the refusal), not on every cycle; every
cycle carries it on the analytics heartbeat. An ACTIVE row of OUR OWN with
no identity -- a dead-lettered own-wallet state or a row of one of our
books -- is refused BY NAME (R_OWN_ACTIVE_IDENTITY_UNKNOWN): logged as an
error and carried as the analytics heartbeat's refusal, so it can never sit
inside a count. Our books unreadable is OWN_BOOKS_UNMEASURED, never clean.

WHAT IT DOES NOT DO. No rescue beyond the existing token catalog (no slug
guess, no fabricated condition), no deletion, no backfill: the dead-lettered
states stay out of `positions` exactly as before; this only names them.

THE 30-DAY LINE. A dead-lettered state has no market, so its market's state
cannot be read; the only fact the replay has is the age of its last fill. A
state with open shares and a fill inside 30 days is counted ACTIVE (it may be
live); older is HISTORICAL_DEBT. The line classifies a count -- it gates
nothing, sizes nothing and never removes a state from view. Production
(research-sql run 37927888187, 2026-10-09 12:06Z): all 10,902 dead-lettered
states are OPEN_NO_FILL_WITHIN_30D, six pinned research wallets, last fill
2026-09-05 15:25Z, 69,181 fills, chain-lane tokens the catalog never held.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from .positions import EPS

VERSION = "POSITIONS_IDENTITY_CENSUS_V1"
ACTIVE_WINDOW = timedelta(days=30)

OWNER_RESEARCH = "TRACKED_RESEARCH_WALLET"
OWNER_OWN = "OWN_BOOK"
HISTORICAL = "HISTORICAL_DEBT"
ACTIVE = "ACTIVE"
C_FLAT = "FLAT_ZERO_SIZE"
C_RESOLVED = "RESOLVED"
C_OPEN_STALE = "OPEN_NO_FILL_WITHIN_30D"
C_OPEN_RECENT = "OPEN_FILLED_WITHIN_30D"
KIND_OF = {C_FLAT: HISTORICAL, C_RESOLVED: HISTORICAL,
           C_OPEN_STALE: HISTORICAL, C_OPEN_RECENT: ACTIVE}

#: an ACTIVE dead-lettered position of OUR OWN: no market identity, so no
#: settlement, no mark and no exit can be bound to it
R_OWN_ACTIVE_IDENTITY_UNKNOWN = "OWN_ACTIVE_POSITION_HAS_NO_MARKET_IDENTITY"

ST_CLEAN = "NO_IDENTITY_DEBT"
ST_HISTORICAL = "HISTORICAL_DEBT_ONLY"
ST_ACTIVE_RESEARCH = "ACTIVE_RESEARCH_WALLET_DEBT"
ST_REFUSED = "REFUSED"
#: our own books could not be read this cycle: missing evidence is never a
#: clean census
ST_OWN_UNMEASURED = "OWN_BOOKS_UNMEASURED"

MAX_WHALES = 20
MAX_EXAMPLES = 5

# ── OUR OWN BOOKS ─────────────────────────────────────────────────────
# The dead-letter above is the TRACKED wallets' replay (`trades` holds only
# roster wallets), so an own-wallet owner appears there only when our funder
# address is on the roster. Our own books are separate tables and are read
# here directly, every cycle: a row of ours whose market identity is
# unknown is counted by book and activity, and an ACTIVE one is refused by
# name (R_OWN_ACTIVE_IDENTITY_UNKNOWN), never left inside a count.
#
#   book                        kind    rows counted       identity unknown
#   ACTUAL_LIVE_ORDERS          ACTUAL  open (filled /     no venue slug, no
#                                       exiting, shares)   condition, token not
#                                                          in the catalog
#   ACTUAL_SMALL_LIVE_FILLS     ACTUAL  every fill         blank venue slug
#   ACTUAL_FUNDED_ENTRIES       ACTUAL  every ENTRY        blank venue slug
#   ACTUAL_KALSHI_FILLS         ACTUAL  every fill         blank ticker
#   OWNER_REGISTERED_POSITIONS  ACTUAL  shares <> 0        blank venue slug
#   PAPER_AI_FOLLOWER           PAPER   open rows          no condition, token
#                                                          not in the catalog
#   PAPER_LEDGER_FILLS          PAPER   every fill         blank venue slug or
#                                                          a side not LONG/SHORT
#   PAPER_RN1X_POSITIONS        PAPER   every position     blank condition
#
# An ACTUAL row with no identity is ACTIVE at any age (money is never
# "historical" while it is held). A PAPER row is ACTIVE inside the 30-day
# window (placed / filled / decided) and HISTORICAL_DEBT outside it. The
# ACTIVE read is bounded by that window (indexed ranges, small tables) and
# runs every cycle; the HISTORICAL read (the legacy AI follower's 328,669
# open rows in production) runs at most once per OWN_HISTORICAL_EVERY_S and
# carries its own as-of. Production, these two statements verbatim
# (research/rc6_identity_own_books.sql, research-sql run 37933365836,
# 2026-10-09 12:56:41Z; both ran inside ~2 s): ACTIVE -- live_orders 51
# open, 0 unknown; paper_fills 1,219, rn1x 11, AI follower 0, small live,
# funded, Kalshi and registered 0 -- 0 unknown in every book; HISTORICAL --
# the AI follower 328,669 open rows, newest placed 2026-09-05 15:07Z, 68,449
# with no identity (OUR legacy PAPER debt, counted and visible), rn1x 2,400
# with 0 unknown.

BOOK_LIVE = "ACTUAL_LIVE_ORDERS"
BOOK_SMALL_LIVE = "ACTUAL_SMALL_LIVE_FILLS"
BOOK_FUNDED = "ACTUAL_FUNDED_ENTRIES"
BOOK_KALSHI = "ACTUAL_KALSHI_FILLS"
BOOK_REGISTERED = "OWNER_REGISTERED_POSITIONS"
BOOK_AI = "PAPER_AI_FOLLOWER"
BOOK_PAPER = "PAPER_LEDGER_FILLS"
BOOK_RN1X = "PAPER_RN1X_POSITIONS"
#: book -> (kind, what a counted row is)
OWN_BOOKS = {
    BOOK_LIVE: ("ACTUAL", "open order rows (filled / exiting, shares > 0)"),
    BOOK_SMALL_LIVE: ("ACTUAL", "fills"),
    BOOK_FUNDED: ("ACTUAL", "ENTRY intents"),
    BOOK_KALSHI: ("ACTUAL", "fills"),
    BOOK_REGISTERED: ("ACTUAL", "registered positions with shares"),
    BOOK_AI: ("PAPER", "open rows"),
    BOOK_PAPER: ("PAPER", "fills"),
    BOOK_RN1X: ("PAPER", "positions"),
}
#: books whose rows are not scanned, and why (visible, never silently out)
OWN_NOT_SCANNED = {
    "PAPER_ENGINE_FILLS_LEGACY": (
        "engine_fills.market_id / outcome_id are NOT NULL by schema; the "
        "legacy paper engine wrote its last row 2026-09-04 (research-sql run "
        "37927888187: 349,411 unsettled rows, every Polymarket token in the "
        "catalog)"),
}
OWN_HISTORICAL_EVERY_S = 3600.0
OWN_MEASURED = "MEASURED"
OWN_UNMEASURED = "UNMEASURED"
OWN_NOT_YET = "NOT_YET_READ"

#: $1 = the start of the 30-day window. Every ACTUAL row; PAPER rows inside
#: the window. One statement, read only.
OWN_ACTIVE_SQL = """
SELECT 'ACTUAL_LIVE_ORDERS' AS book, count(*) AS rows_n,
       count(*) FILTER (WHERE coalesce(btrim(lo.us_market_slug), '') = ''
                          AND coalesce(btrim(lo.condition_id), '') = ''
                          AND NOT EXISTS (SELECT 1 FROM market_tokens mt
                                           WHERE mt.token_id = lo.asset))
           AS unknown_n
  FROM live_orders lo
 WHERE lo.status IN ('filled', 'exiting') AND lo.filled_shares > 0
UNION ALL
SELECT 'ACTUAL_SMALL_LIVE_FILLS', count(*),
       count(*) FILTER (WHERE coalesce(btrim(f.us_market_slug), '') = '')
  FROM execmirror_fills f
UNION ALL
SELECT 'ACTUAL_FUNDED_ENTRIES', count(*),
       count(*) FILTER (WHERE coalesce(btrim(i.us_market_slug), '') = '')
  FROM bettor_funded_intents i WHERE i.kind = 'ENTRY'
UNION ALL
SELECT 'ACTUAL_KALSHI_FILLS', count(*),
       count(*) FILTER (WHERE coalesce(btrim(k.ticker), '') = '')
  FROM kalshi_live_fills k
UNION ALL
SELECT 'OWNER_REGISTERED_POSITIONS', count(*),
       count(*) FILTER (WHERE coalesce(btrim(r.us_market_slug), '') = '')
  FROM mirror_registered_positions r WHERE r.shares <> 0
UNION ALL
SELECT 'PAPER_AI_FOLLOWER', count(*),
       count(*) FILTER (WHERE coalesce(btrim(a.condition_id), '') = ''
                          AND NOT EXISTS (SELECT 1 FROM market_tokens mt
                                           WHERE mt.token_id = a.asset))
  FROM ai_trades a WHERE a.status = 'open' AND a.placed_at >= $1
UNION ALL
SELECT 'PAPER_LEDGER_FILLS', count(*),
       count(*) FILTER (WHERE coalesce(btrim(pf.us_market_slug), '') = ''
                           OR pf.holding_side NOT IN ('LONG', 'SHORT'))
  FROM paper_fills pf WHERE pf.filled_at >= $1
UNION ALL
SELECT 'PAPER_RN1X_POSITIONS', count(*),
       count(*) FILTER (WHERE coalesce(btrim(x.condition_id), '') = '')
  FROM rn1x_positions x WHERE x.decision_ts >= $1
"""
#: $1 = the start of the 30-day window: PAPER rows OUTSIDE it (historical)
OWN_HISTORICAL_SQL = """
SELECT 'PAPER_AI_FOLLOWER' AS book, count(*) AS rows_n,
       count(*) FILTER (WHERE coalesce(btrim(a.condition_id), '') = ''
                          AND NOT EXISTS (SELECT 1 FROM market_tokens mt
                                           WHERE mt.token_id = a.asset))
           AS unknown_n,
       max(a.placed_at) AS newest_at
  FROM ai_trades a WHERE a.status = 'open' AND a.placed_at < $1
UNION ALL
SELECT 'PAPER_LEDGER_FILLS', count(*),
       count(*) FILTER (WHERE coalesce(btrim(pf.us_market_slug), '') = ''
                           OR pf.holding_side NOT IN ('LONG', 'SHORT')),
       max(pf.filled_at)
  FROM paper_fills pf WHERE pf.filled_at < $1
UNION ALL
SELECT 'PAPER_RN1X_POSITIONS', count(*),
       count(*) FILTER (WHERE coalesce(btrim(x.condition_id), '') = ''),
       max(x.decision_ts)
  FROM rn1x_positions x WHERE x.decision_ts < $1
"""

#: the HISTORICAL read's memo (at most once per OWN_HISTORICAL_EVERY_S)
_OWN_HIST: dict = {"at": None, "rows": None, "as_of": None, "error": None}


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def classify(state, now: datetime) -> str:
    """One dead-lettered PositionState's class. Pure."""
    pos = state.position
    if pos.resolved:
        return C_RESOLVED
    if pos.shares <= EPS:
        return C_FLAT
    last = _aware(state.last_ts)
    if last is not None and last >= _aware(now) - ACTIVE_WINDOW:
        return C_OPEN_RECENT
    return C_OPEN_STALE


def _iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return _aware(v).isoformat()
    return str(v)


def own_books_census(active_rows: list | None, *,
                     hist_rows: list | None = None,
                     hist_as_of: str | None = None,
                     hist_error: str | None = None,
                     error: str | None = None) -> dict:
    """OUR OWN BOOKS' identity census from OWN_ACTIVE_SQL's rows (None: the
    read failed -- UNMEASURED, never clean) and the memoised
    OWN_HISTORICAL_SQL rows (None: not read yet, or failed). Pure."""
    books = {b: {"kind": kind, "unit": unit, ACTIVE: None, HISTORICAL: None}
             for b, (kind, unit) in OWN_BOOKS.items()}
    for r in active_rows or ():
        b = books.get(r["book"])
        if b is not None:
            b[ACTIVE] = {"rows": int(r["rows_n"] or 0),
                         "unknown_identity": int(r["unknown_n"] or 0)}
    for r in hist_rows or ():
        b = books.get(r["book"])
        if b is not None and b["kind"] == "PAPER":
            b[HISTORICAL] = {"rows": int(r["rows_n"] or 0),
                             "unknown_identity": int(r["unknown_n"] or 0),
                             "newest_at": _iso(r.get("newest_at"))}
    unread = sorted(b for b, v in books.items() if v[ACTIVE] is None)
    measured = active_rows is not None and not unread
    hist_status = (OWN_MEASURED if hist_rows is not None
                   else OWN_UNMEASURED if hist_error else OWN_NOT_YET)
    active_unknown = sum(v[ACTIVE]["unknown_identity"]
                         for v in books.values() if v[ACTIVE])
    return {
        "status": OWN_MEASURED if measured else OWN_UNMEASURED,
        "error": error, "books_unread": unread,
        "active_rows": sum(v[ACTIVE]["rows"] for v in books.values()
                           if v[ACTIVE]),
        "active_unknown": active_unknown if measured else None,
        "active_unknown_by_book": {b: v[ACTIVE]["unknown_identity"]
                                   for b, v in books.items()
                                   if v[ACTIVE] and
                                   v[ACTIVE]["unknown_identity"]},
        "historical_status": hist_status, "historical_as_of": hist_as_of,
        "historical_error": hist_error,
        "historical_rows": (sum(v[HISTORICAL]["rows"] for v in books.values()
                                if v[HISTORICAL])
                            if hist_status == OWN_MEASURED else None),
        "historical_unknown": (sum(v[HISTORICAL]["unknown_identity"]
                                   for v in books.values() if v[HISTORICAL])
                               if hist_status == OWN_MEASURED else None),
        "books": books,
        "not_scanned": dict(OWN_NOT_SCANNED),
        "rule": ("an ACTUAL row with no market identity is ACTIVE at any age; "
                 "a PAPER row is ACTIVE when placed / filled / decided within "
                 "%d days, else HISTORICAL_DEBT; an ACTIVE row of ours with "
                 "no identity is refused by name" % ACTIVE_WINDOW.days)}


async def read_own_books(pool, *, now: datetime, clock=None,
                         every_s: float = OWN_HISTORICAL_EVERY_S) -> dict:
    """Read our own books (OWN_ACTIVE_SQL every call; OWN_HISTORICAL_SQL at
    most once per `every_s`, its rows and as-of memoised). Read only; a
    failed read is UNMEASURED, never a clean census."""
    clock = clock or time.monotonic
    start = _aware(now) - ACTIVE_WINDOW
    try:
        active = [dict(r) for r in await pool.fetch(OWN_ACTIVE_SQL, start)]
    except Exception as exc:  # noqa: BLE001 — measurement, never the cycle
        return own_books_census(None, error=type(exc).__name__)
    h = _OWN_HIST
    t = clock()
    if h["at"] is None or t - h["at"] >= every_s:
        try:
            rows = [dict(r) for r in await pool.fetch(OWN_HISTORICAL_SQL,
                                                       start)]
            h.update(at=t, rows=rows, as_of=_aware(now).isoformat(),
                     error=None)
        except Exception as exc:  # noqa: BLE001 — retried after every_s
            h.update(at=t, rows=None, as_of=None, error=type(exc).__name__)
    return own_books_census(active, hist_rows=h["rows"],
                            hist_as_of=h["as_of"], hist_error=h["error"])


def census(missing: list, *, now: datetime,
           own_whale_ids: set | frozenset = frozenset(),
           own_wallets_configured: int = 0,
           own_books: dict | None = None) -> dict:
    """The census of the dead-lettered states `missing` (states with no
    condition_id after the rescue), with OUR OWN BOOKS' census beside it
    (`own_books`, read_own_books; the analytics persist always passes it).
    Pure."""
    now = _aware(now)
    by_owner: dict = {}
    by_whale: dict = {}
    examples: list = []
    own_active = 0
    for st in missing:
        owner = OWNER_OWN if st.whale_id in own_whale_ids else OWNER_RESEARCH
        cls = classify(st, now)
        kind = KIND_OF[cls]
        o = by_owner.setdefault(owner, {HISTORICAL: 0, ACTIVE: 0,
                                        "by_class": {}})
        o[kind] += 1
        o["by_class"][cls] = o["by_class"].get(cls, 0) + 1
        w = by_whale.setdefault(st.whale_id, {
            "whale_id": st.whale_id, "owner": owner, "states": 0,
            HISTORICAL: 0, ACTIVE: 0, "last_fill_at": None})
        w["states"] += 1
        w[kind] += 1
        last = _aware(st.last_ts)
        if last is not None and (w["last_fill_at"] is None
                                 or last > w["last_fill_at"]):
            w["last_fill_at"] = last
        if kind == ACTIVE:
            if owner == OWNER_OWN:
                own_active += 1
            if len(examples) < MAX_EXAMPLES:
                examples.append({"whale_id": st.whale_id, "owner": owner,
                                 "token_id": str(st.token_id),
                                 "shares": round(st.position.shares, 6),
                                 "last_fill_at": None if last is None
                                 else last.isoformat()})
    whales = sorted(by_whale.values(), key=lambda w: (-w["states"],
                                                      w["whale_id"]))
    for w in whales:
        w["last_fill_at"] = (None if w["last_fill_at"] is None
                             else w["last_fill_at"].isoformat())
    active = sum(o[ACTIVE] for o in by_owner.values())
    ob = own_books or {}
    books_active = int(ob.get("active_unknown") or 0)
    books_unmeasured = own_books is not None and ob.get("status") != OWN_MEASURED
    own_hist = int(ob.get("historical_unknown") or 0)
    roster_active = own_active
    own_active += books_active
    refusal = R_OWN_ACTIVE_IDENTITY_UNKNOWN if own_active else None
    status = (ST_REFUSED if refusal
              else ST_OWN_UNMEASURED if books_unmeasured
              else ST_ACTIVE_RESEARCH if active
              else ST_HISTORICAL if (missing or own_hist) else ST_CLEAN)
    return {
        "version": VERSION, "as_of": now.isoformat(),
        "dead_lettered": len(missing),
        "historical_debt": sum(o[HISTORICAL] for o in by_owner.values()),
        "active": active, "own_active": own_active,
        "own_active_by_source": {"roster_wallet": roster_active,
                                 "own_books": books_active},
        "status": status, "refusal": refusal,
        "own_books": own_books,
        "by_owner": by_owner,
        "by_whale": whales[:MAX_WHALES],
        "whales_truncated": len(whales) > MAX_WHALES,
        "active_examples": examples,
        "own_wallets_configured": int(own_wallets_configured),
        "active_window_days": ACTIVE_WINDOW.days,
        "rule": ("a dead-lettered state (no condition_id after the token-"
                 "catalog rescue) is ACTIVE when it holds shares and filled "
                 "within %d days, else HISTORICAL_DEBT; nothing is rescued, "
                 "deleted or backfilled here" % ACTIVE_WINDOW.days)}


def _own_signature(ob: dict | None) -> tuple:
    """Our own books' part of a change: the read status and the UNKNOWN
    counts by book and scope -- never the row totals, which move with every
    order (they would log every cycle)."""
    if ob is None:
        return ()
    return (ob.get("status"), ob.get("historical_status"),
            tuple(sorted(
                (b, scope, (v.get(scope) or {}).get("unknown_identity"))
                for b, v in (ob.get("books") or {}).items()
                for scope in (ACTIVE, HISTORICAL))))


def signature(c: dict) -> tuple:
    """What a change is: the counts by owner and class, the refusal and our
    own books' unknown counts -- never the clock (as_of) or the examples."""
    return (c.get("dead_lettered"), c.get("refusal"),
            tuple(sorted((owner, cls, n)
                         for owner, o in (c.get("by_owner") or {}).items()
                         for cls, n in (o.get("by_class") or {}).items())),
            _own_signature(c.get("own_books")))


def _own_line(ob: dict | None) -> str:
    if ob is None:
        return "own books not read"
    if ob.get("status") != OWN_MEASURED:
        return "own books %s (%s; unread %s)" % (
            ob.get("status"), ob.get("error") or "-",
            ",".join(ob.get("books_unread") or ()) or "-")
    hist = ("HISTORICAL_DEBT %s unknown of %s rows as of %s [%s]" % (
        ob.get("historical_unknown"), ob.get("historical_rows"),
        ob.get("historical_as_of"), ", ".join(
            "%s %d" % (b, v[HISTORICAL]["unknown_identity"])
            for b, v in sorted((ob.get("books") or {}).items())
            if v.get(HISTORICAL) and v[HISTORICAL]["unknown_identity"])
        or "none")
        if ob.get("historical_status") == OWN_MEASURED
        else "HISTORICAL_DEBT %s" % ob.get("historical_status"))
    return "own books: ACTIVE %d unknown of %d rows [%s]; %s" % (
        ob.get("active_unknown") or 0, ob.get("active_rows") or 0,
        ", ".join("%s %d" % kv for kv in sorted(
            (ob.get("active_unknown_by_book") or {}).items())) or "none",
        hist)


def line(c: dict) -> str:
    """The one-line statement of a census, for the log."""
    parts = []
    for owner, o in sorted((c.get("by_owner") or {}).items()):
        cls = ", ".join("%s %d" % (k, v)
                        for k, v in sorted(o["by_class"].items()))
        parts.append("%s: %s %d / %s %d [%s]" % (
            owner, HISTORICAL, o[HISTORICAL], ACTIVE, o[ACTIVE], cls))
    return ("positions identity-debt census: %d dead-lettered (no condition_"
            "id after the token-catalog rescue) -- %s; %s; status %s; "
            "refusal %s"
            % (c.get("dead_lettered") or 0,
               "; ".join(parts) or "none", _own_line(c.get("own_books")),
               c.get("status"), c.get("refusal") or "none"))


_LAST: dict = {"signature": None}


def log_on_change(c: dict, log) -> bool:
    """Log the census when its signature changed since the last one logged
    in this process (the first census of a process always logs). A refusal
    logs at ERROR, debt at WARNING, a clean census at INFO. Returns whether
    it logged."""
    sig = signature(c)
    if sig == _LAST["signature"]:
        log.debug("%s (unchanged)", line(c))
        return False
    _LAST["signature"] = sig
    if c.get("refusal"):
        log.error("%s; examples %s", line(c), c.get("active_examples"))
    elif c.get("status") != ST_CLEAN:
        # debt (the tracked wallets' or our own legacy books'), or our own
        # books unreadable: a warning, never quiet
        log.warning(line(c))
    else:
        log.info(line(c))
    return True


def summary(c: dict | None) -> dict | None:
    """The census as the analytics heartbeat carries it (bounded)."""
    if not c:
        return None
    return {k: c.get(k) for k in (
        "version", "as_of", "dead_lettered", "historical_debt", "active",
        "own_active", "own_active_by_source", "status", "refusal",
        "own_books", "by_owner", "own_wallets_configured",
        "active_window_days")} | {
        "by_whale": (c.get("by_whale") or [])[:MAX_WHALES]}
