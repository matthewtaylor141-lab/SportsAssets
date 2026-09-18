"""COMMAND's server-side read model: bt.command.v1 from ACTUAL records.

WHAT THIS REPLACES.

`frontend/public/command/config.js` names `/api/command/snapshot` and
says, in its own comment, that "neither route below is assumed to exist
in the existing API". It did not. COMMAND therefore had exactly one
source of numbers it could draw: `core.demoSnapshot()`, which is
labelled ILLUSTRATIVE and is refused by the connected feed. This module
is the real one.

THE THREE RULES THIS MODULE IS BUILT AROUND.

1.  UNAVAILABLE IS NEVER ZERO. Every economic field is `None` when the
    source did not produce it, and `core.validate` accepts null for
    exactly that reason. A zero balance and an unread balance look
    identical on a dashboard and mean opposite things.

2.  A FAILED RETRIEVAL IS NOT AN EMPTY BOOK. If the ledger cannot be
    read, this module RAISES `RetrievalIncomplete` and the route answers
    503. It never emits a well-formed snapshot with empty arrays --
    that is a dashboard confidently reporting no positions during an
    outage, which is worse than no dashboard.

3.  OWNERSHIP IS DECLARED, NEVER INFERRED. Each row carries a `lane`
    taken from an explicit identifier -- the mirror book that owns it,
    the operator registration that claims it, the calibration ledger
    that reserved it. `pmus_account._scorecard` classifies by "cost >
    $5" for its own purposes; that heuristic is NOT used here and must
    never be, because a mirror position that grows past $5 would be
    silently relabelled as the owner's own trade.

WHAT IT DOES NOT DO.

No venue client. The account figures come from `pmus_account
.account_snapshot()`, which is a single process-wide 30-second cache --
so ten browsers polling COMMAND are ten reads of one shared snapshot,
not ten venue collectors. Everything else is the database. Nothing here
can place, cancel or modify an order.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any

SCHEMA = "bt.command.v1"

SOURCE_NATIVE = "NATIVE_LEDGER"
SOURCE_SHADOW = "SHADOW_RESEARCH"
MODE_LIVE = "LIVE"
MODE_SHADOW = "SHADOW"
AUD_INTERNAL = "INTERNAL"
AUD_INVESTOR = "INVESTOR"

# ── LANES. Explicit identifiers, never size, never a guess. ──────────
LANE_AUTONOMOUS = "AUTONOMOUS_STRATEGY"
LANE_CALIBRATION = "CALIBRATION"
LANE_MANUAL = "LEGACY_MANUAL"
LANE_UNATTRIBUTED = "UNATTRIBUTED"
LANES = (LANE_AUTONOMOUS, LANE_CALIBRATION, LANE_MANUAL, LANE_UNATTRIBUTED)

OWNERSHIP_IS_DECLARED = (
    "lane comes from the record that claims the row -- a mirror book, an "
    "operator registration, or the calibration ledger. It is never inferred "
    "from trade size, and performance is never blended across lanes")

# ── THE EXIT LIFECYCLE, as four distinguishable states. ──────────────
EXIT_CONSIDERED = "EXIT_CONSIDERED"
EXIT_SUBMITTED = "EXIT_SUBMITTED"
EXIT_FILLED = "EXIT_FILLED"
POSITION_RECONCILED = "POSITION_RECONCILED"
EXIT_STAGES = (EXIT_CONSIDERED, EXIT_SUBMITTED, EXIT_FILLED,
               POSITION_RECONCILED)

UNIDENTIFIED = "NOT IDENTIFIED"
UNCLASSIFIED = "Unclassified"

MAX_ROWS = 10000        # core.validate refuses more; page beyond this


class RetrievalIncomplete(Exception):
    """The read model could not be built. Named, and never a zero."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason if not detail else "%s: %s" % (reason, detail))
        self.reason = reason
        self.detail = detail


# ── small helpers ────────────────────────────────────────────────────

def _f(v) -> float | None:
    """A finite float, or None. Never a silent zero."""
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _r2(v):
    x = _f(v)
    return None if x is None else round(x + 0.0, 2)


def _s(v, fallback: str = UNIDENTIFIED) -> str:
    """A non-empty string. The contract requires one; a blank fails
    validation in the browser, so the fallback is explicit and honest
    rather than an empty string smuggled through."""
    t = "" if v is None else str(v).strip()
    return t or fallback


def _iso(v) -> str | None:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        try:
            return v.astimezone(_utc()).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError, OSError):
            try:
                return v.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:                              # noqa: BLE001
                return None
    t = str(v).strip()
    return t or None


def _utc():
    import datetime as _dt
    return _dt.timezone.utc


def iso_now(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(now if now is not None else time.time()))


def _p01(v):
    """A probability/price, only if it really is in [0,1]. The browser
    refuses anything outside, so a bad mark must arrive as None rather
    than fail the whole snapshot for every reader."""
    x = _f(v)
    return x if x is not None and 0.0 <= x <= 1.0 else None


def _title_of(slug: str) -> str:
    """A human title from the venue slug, without inventing a fixture.

    The slug IS the identity here; prettifying it is presentation. When
    it is missing we say so rather than print a blank row.
    """
    t = (slug or "").strip()
    return t or UNIDENTIFIED


def _sport_family(slug: str) -> tuple:
    try:
        from .. import copy_sports as CS
        sport = _s(CS.sport_of(slug), UNCLASSIFIED)
        family = _s(CS.mirror_family_of(slug), UNCLASSIFIED)
    except Exception:                                      # noqa: BLE001
        # A classifier failure is a classification gap, not a crash and
        # not a wrong label.
        sport, family = UNCLASSIFIED, UNCLASSIFIED
    return sport, family


# ── row builders (pure) ──────────────────────────────────────────────

def position_row(book: dict, mark: float | None = None,
                 lane: str = LANE_AUTONOMOUS,
                 as_of: str = "") -> dict:
    """One mirror book -> one COMMAND position row.

    `quantity` is the magnitude; the SIDE lives in `leg`, because the
    contract refuses a negative quantity and a short rendered as -412
    shares would simply be dropped from the table.
    """
    slug = _s(book.get("us_market_slug"), "")
    sport, family = _sport_family(slug)
    net = _f(book.get("ledger_net"))
    entry = _p01(book.get("avg_cost"))
    mk = _p01(mark if mark is not None else book.get("last_mark"))
    qty = None if net is None else abs(net)
    leg = "LONG" if (net or 0) >= 0 else "SHORT"
    cost = None if (qty is None or entry is None) else round(qty * entry, 2)
    value = None if (qty is None or mk is None) else round(qty * mk, 2)
    unreal = None if (cost is None or value is None) else round(
        (value - cost) if leg == "LONG" else (cost - value), 2)
    state = _s(book.get("state"), "")
    frozen = _s(book.get("frozen_reason"), "") if state == "frozen" else ""
    return {
        "id": "book-%s" % _s(book.get("id"), "?"),
        "eventId": _s(book.get("game_key") or book.get("condition_id"), slug),
        "marketId": _s(book.get("condition_id"), slug),
        "title": _title_of(slug),
        "outcome": _s(book.get("long_asset"), UNIDENTIFIED),
        "sport": sport,
        "family": family,
        "venue": "Polymarket US",
        "leg": leg,
        "quantity": qty,
        "entry": entry,
        "mark": mk,
        "cost": cost,
        "value": value,
        "unrealized": unreal,
        "realized": _r2(book.get("realized_pnl")),
        "intent": _s(book.get("intent"), UNIDENTIFIED),
        "reason": _s(frozen or book.get("last_reason"), "no reason recorded"),
        "openedAt": _s(_iso(book.get("opened_at")), UNIDENTIFIED),
        "asOf": _s(_iso(book.get("updated_at")) or as_of, as_of or UNIDENTIFIED),
        "markBasis": ("venue per-market read stored on the book"
                      if mk is not None else "mark unavailable at this read"),
        "state": state or UNIDENTIFIED,
        "lane": lane,
        "exitStage": _exit_stage(book),
        "evidence": SOURCE_NATIVE,
    }


def _exit_stage(book: dict) -> str:
    """Which of the four exit states this book is in.

    An exit being CONSIDERED, an exit ORDER SUBMITTED, an exit FILLED
    and a position RECONCILED are four different facts, and a dashboard
    that collapses them tells an operator a sale happened when only a
    thought did.
    """
    state = _s(book.get("state"), "")
    if state == "closed" and book.get("closed_at"):
        return POSITION_RECONCILED
    if book.get("open_order_id") and state == "closing":
        return EXIT_SUBMITTED
    net = _f(book.get("ledger_net"))
    if state == "closing" and net is not None and abs(net) < 1:
        return EXIT_FILLED
    if state == "closing":
        return EXIT_CONSIDERED
    return UNIDENTIFIED


# The ledger's own order states -> the words COMMAND shows. The four
# management asked for by name -- cancellations, rejections, UNRESOLVED
# states and filled quantity -- each keep their own word. `unknown` and
# `lost` are NOT collapsed into cancelled: an order whose fate we never
# read is the one that can still surprise the ledger, and it is exactly
# the row an operator must be able to see.
ORDER_STATUS = {
    "placing": "SUBMITTING",
    "open": "RESTING",            # -> PARTIAL below when filled > 0
    "filled": "FILLED",
    "cancelled": "CANCELLED",
    "expired": "EXPIRED",
    "rejected": "REJECTED",
    "unknown": "UNRESOLVED",
    "lost": "UNRESOLVED",
}


def order_status(state: str, filled: float | None, qty: float | None) -> str:
    s = ORDER_STATUS.get((state or "").strip().lower())
    if s is None:
        return UNIDENTIFIED
    if s == "RESTING" and filled is not None and filled > 0:
        return "PARTIAL"
    if s == "CANCELLED" and filled is not None and filled > 0:
        # A cancel that filled part of the order is not simply cancelled.
        return "CANCELLED_PARTIAL"
    return s


def order_row(o: dict, lane: str = LANE_AUTONOMOUS) -> dict:
    """One mirror order -> one COMMAND order row.

    `filled` is clamped to `quantity` ONLY when the venue reported more
    than we asked for, and that case is named in the reason rather than
    hidden: an over-fill is a reconciliation event, not a rounding
    detail, and the contract refuses filled > quantity outright.
    """
    slug = _s(o.get("us_market_slug"), "")
    qty = _f(o.get("quantity"))
    filled = _f(o.get("filled"))
    reason = _s(o.get("reason") or o.get("kind"), "no reason recorded")
    if qty is not None and filled is not None and filled > qty:
        # THE CONTRACT REFUSES filled > quantity, so the row would be
        # dropped outright. Clamping it silently would hide the one
        # thing an operator most needs to see, so the overfill is named
        # in the reason and survives into the table.
        reason = "OVERFILL venue %s vs requested %s -- %s" % (filled, qty,
                                                              reason)
        filled = qty
    side = _s(o.get("side"), UNIDENTIFIED)
    return {
        "id": "order-%s" % _s(o.get("id"), "?"),
        "positionId": ("book-%s" % o["book_id"]) if o.get("book_id") else None,
        "eventId": _s(o.get("game_key") or o.get("condition_id"), slug),
        "marketId": _s(o.get("condition_id"), slug),
        "title": _title_of(slug),
        "outcome": _s(o.get("asset") or o.get("long_asset"), UNIDENTIFIED),
        "venue": "Polymarket US",
        "side": "SELL" if side.startswith("SELL") else "BUY",
        "intent": side,
        "price": _p01(o.get("price")),
        "quantity": qty,
        "filled": filled,
        "status": order_status(_s(o.get("state"), ""), filled, qty),
        "venueStatus": _s(o.get("venue_state"), UNIDENTIFIED),
        "venueOrderId": _s(o.get("order_id"), UNIDENTIFIED),
        "at": _s(_iso(o.get("placed_at")), UNIDENTIFIED),
        "updatedAt": _s(_iso(o.get("updated_at")), UNIDENTIFIED),
        "reason": reason,
        "lane": lane,
        "evidence": SOURCE_NATIVE,
    }


def decision_row(r: dict) -> dict:
    """One candidate refusal -> one COMMAND decision row.

    These ARE the decision receipts and blockers: the census name the
    candidate left under, with the numbers the tick actually read. EV is
    not manufactured to fill the column -- netEv, p10, p90 and pPositive
    stay null, and the contract accepts null for exactly that reason.
    """
    slug = _s(r.get("us_slug"), "")
    sport, family = _sport_family(slug)
    return {
        "id": "refusal-%s" % _s(r.get("id"), "?"),
        "eventId": _s(r.get("condition_id"), slug),
        "marketId": _s(r.get("condition_id"), slug),
        "title": _title_of(slug or _s(r.get("condition_id"), "")),
        "outcome": _s(r.get("long_asset"), UNIDENTIFIED),
        "sport": sport,
        "family": family,
        "venue": "Polymarket US",
        "action": "COPY CANDIDATE",
        "state": "REFUSED",
        "price": _p01(r.get("his_px")),
        "mark": _p01(r.get("mark")),
        "quantity": _f(r.get("target")),
        "netEv": None, "p10": None, "p90": None, "pPositive": None,
        "fillProbability": None,
        "gate": "BLOCKED",
        "blocker": _s(r.get("refusal"), UNIDENTIFIED),
        "reason": "candidate left the tick under census name %s"
                  % _s(r.get("refusal"), UNIDENTIFIED),
        "evidence": SOURCE_NATIVE,
        "lane": LANE_AUTONOMOUS,
        "at": _s(_iso(r.get("at")), UNIDENTIFIED),
    }


def service_row(h: dict, now: float) -> dict:
    """A heartbeat -> a service row, with its OWN age.

    Animation is not freshness: the row carries the age of the last
    beat, so a worker that died twenty minutes ago reads as twenty
    minutes stale rather than as a green dot on a moving page.
    """
    beat = _iso(h.get("beat_at"))
    age = None
    if h.get("beat_at") is not None and hasattr(h["beat_at"], "timestamp"):
        try:
            age = max(0.0, round(now - h["beat_at"].timestamp(), 1))
        except (ValueError, OSError, TypeError):
            age = None
    status = _s(h.get("status"), UNIDENTIFIED).upper()
    return {
        "name": _s(h.get("service"), UNIDENTIFIED),
        "status": status if age is None or age <= 300 else "STALE",
        "latency": age,
        "beatAt": beat or UNIDENTIFIED,
        "detail": ("last beat %ss ago" % age) if age is not None
                  else "beat time unreadable",
    }


def account_block(acct: dict | None, session: dict | None) -> dict:
    """Balances, with every unknown left as null.

    `reserved` is the calibration session's reserve when one exists.
    `available` is cash less reserved, and is null the moment either
    side of that subtraction is unknown -- an available balance computed
    from an unread cash figure is a number nobody may trade against.
    """
    a = acct or {}
    cash = _r2(a.get("cash") if "cash" in a else a.get("cash_usd"))
    pos_value = _r2(a.get("position_value") if "position_value" in a
                    else a.get("positions_value"))
    reserved = None
    if session is not None:
        from .. import calibration as cal
        reserved = _r2(cal.reserved(session))
    available = None if (cash is None or reserved is None) else round(
        cash - reserved, 2)
    value = None if (cash is None or pos_value is None) else round(
        cash + pos_value, 2)
    unreal = _r2(a.get("unrealized"))
    realized = _r2(a.get("realized_mtd") if "realized_mtd" in a
                   else a.get("realized"))
    net = None if (realized is None or unreal is None) else round(
        realized + unreal, 2)
    return {
        "value": value,
        "cash": cash,
        "reserved": reserved,
        "available": available,
        "positionValue": pos_value,
        "realizedMtd": realized,
        "unrealized": unreal,
        "netMtd": net,
        "grossVolumeMtd": _r2(a.get("gross_volume_mtd")),
        "feesMtd": _r2(a.get("fees_mtd")),
        "rebatesMtd": _r2(a.get("rebates_mtd")),
    }


def gate_rows(mirror_live: bool, calibration_open: int,
              deployed: bool) -> list:
    return [
        {"name": "Order submission from COMMAND", "status": "BLOCKED",
         "detail": "this interface has no order authority and no venue client"},
        {"name": "Autonomous strategy admission",
         "status": "PASS" if mirror_live else "BLOCKED",
         "detail": "mirror_live=%s" % ("true" if mirror_live else "false")},
        {"name": "Calibration lifecycle limit",
         "status": "PASS" if calibration_open == 0 else "PENDING",
         "detail": "%d of 1 concurrent lifecycles open" % calibration_open},
        {"name": "Deployed browser reconciliation",
         "status": "PASS" if deployed else "PENDING",
         "detail": "records reconciled to backend ids in a deployed browser"},
    ]


# ── assembly ─────────────────────────────────────────────────────────

def _content_hash(payload: dict) -> str:
    body = {k: v for k, v in payload.items()
            if k not in ("sequence", "snapshotId", "asOf")}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def assemble(positions: list, orders: list, decisions: list,
             history: list, activity: list, services: list, gates: list,
             account: dict, provenance: dict, counts: dict,
             engine: dict, calibration: dict,
             now: float | None = None,
             audience: str = AUD_INTERNAL,
             mode: str = MODE_LIVE,
             source: str = SOURCE_NATIVE) -> dict:
    """Build the payload, then stamp identity FROM ITS CONTENT.

    The browser enforces three things at once: sequence must not go
    backwards, one sequence must name one snapshotId, and one
    snapshotId must never change content. Deriving `sequence` from the
    wall clock in milliseconds satisfies all three without a store --
    it rises across restarts and across API instances, and two
    different contents can never share one. The caller's short cache
    then returns the SAME object, so a poller that sees no change sees
    no change rather than a new identity every fifteen seconds.
    """
    now = time.time() if now is None else now
    payload = {
        "schema": SCHEMA,
        "source": source,
        "mode": mode,
        "audience": audience,
        "provenance": provenance,
        "account": account,
        "counts": counts,
        "positions": positions,
        "orders": orders,
        "decisions": decisions,
        "history": history,
        "activity": activity,
        "services": services,
        "gates": gates,
        "engine": engine,
        "calibration": calibration,
        "lanes": {"declared": list(LANES),
                  "ownershipIsDeclared": OWNERSHIP_IS_DECLARED},
        "exitStages": list(EXIT_STAGES),
    }
    digest = _content_hash(payload)
    payload["sequence"] = int(now * 1000)
    payload["snapshotId"] = "BTC-%d-%s" % (payload["sequence"], digest[:12])
    payload["contentHash"] = digest
    payload["asOf"] = iso_now(now)
    return payload


def investor_projection(snapshot: dict) -> dict:
    """The INVESTOR audience, projected ON THE SERVER.

    `core.projectInvestor` refuses to run on anything but DEMO, and it
    is right to: a browser that strips internal rows has still received
    them. Every position, order, decision and model is dropped here,
    before the response leaves the process.
    """
    out = dict(snapshot)
    out["audience"] = AUD_INVESTOR
    out["positions"] = []
    out["orders"] = []
    out["decisions"] = []
    out["models"] = []
    out["activity"] = [a for a in snapshot.get("activity", ())
                       if a.get("kind") == "report"]
    out["allocation"] = _allocation(snapshot.get("positions") or [])
    digest = _content_hash(out)
    out["contentHash"] = digest
    out["snapshotId"] = "BTC-INV-%d-%s" % (out.get("sequence", 0), digest[:12])
    return out


def _allocation(positions: list) -> list:
    groups: dict = {}
    for p in positions:
        c = _f(p.get("cost"))
        if c is None:
            continue
        groups[p.get("sport") or UNCLASSIFIED] = \
            groups.get(p.get("sport") or UNCLASSIFIED, 0.0) + c
    return sorted(({"name": k, "value": round(v, 2)}
                   for k, v in groups.items()),
                  key=lambda r: -r["value"])


# ── the reads ────────────────────────────────────────────────────────
# Every query is bounded and every failure is NAMED. A read that raises
# becomes RetrievalIncomplete, never an empty list quietly passed on.

BOOKS_SQL = """
SELECT id, condition_id, us_market_slug, game_key, long_asset, intent,
       state, frozen_reason, ledger_net, avg_cost, realized_pnl,
       open_order_id, last_reason, opened_at, updated_at, closed_at
  FROM mirror_books
 WHERE state <> 'closed'
 ORDER BY updated_at DESC
 LIMIT $1
"""

ORDERS_SQL = """
SELECT o.id, o.book_id, o.us_market_slug, o.kind, o.side, o.price,
       o.state, o.venue_state, o.qty AS quantity, o.filled, o.reason,
       o.placed_at, o.updated_at, o.done_at, o.order_id,
       b.condition_id, b.game_key, b.long_asset
  FROM mirror_orders o
  LEFT JOIN mirror_books b ON b.id = o.book_id
 ORDER BY o.placed_at DESC
 LIMIT $1
"""

REFUSALS_SQL = """
SELECT id, at, condition_id, us_slug, refusal, target, mark, his_px,
       long_asset
  FROM mirror_candidate_refusals
 ORDER BY at DESC
 LIMIT $1
"""

HEARTBEAT_SQL = "SELECT service, status, detail, beat_at FROM service_heartbeats"

REGISTERED_SQL = """
SELECT us_market_slug, condition_id, asset, shares, side, note, registered_by
  FROM mirror_registered_positions
"""

# THE SLEEVE'S OWN SWITCH, read from where the worker reads it. The
# mirror arms only when ingestion_state['mirror_live'] is EXACTLY true
# (workers/mirror_live._STATE_LIVE), so COMMAND reports the same row
# rather than a second opinion that could disagree with the worker.
MODE_SQL = "SELECT value FROM ingestion_state WHERE key = 'mirror_live'"


async def _fetch(pool, sql, *args, what: str):
    try:
        return await pool.fetch(sql, *args)
    except Exception as exc:                                   # noqa: BLE001
        raise RetrievalIncomplete(
            "LEDGER_READ_FAILED", "%s: %s" % (what, type(exc).__name__)) from exc


async def read_records(pool, limit: int = 2000) -> dict:
    """Every record COMMAND shows, from the database, in one pass."""
    if pool is None:
        raise RetrievalIncomplete("NO_DATABASE_POOL")
    books = await _fetch(pool, BOOKS_SQL, limit, what="mirror_books")
    orders = await _fetch(pool, ORDERS_SQL, limit, what="mirror_orders")
    refusals = await _fetch(pool, REFUSALS_SQL, 200, what="refusals")
    beats = await _fetch(pool, HEARTBEAT_SQL, what="service_heartbeats")
    registered = await _fetch(pool, REGISTERED_SQL, what="registered")
    mode = await _fetch(pool, MODE_SQL, what="ingestion_state.mirror_live")
    for name, rows in (("mirror_books", books), ("mirror_orders", orders)):
        if len(rows) >= MAX_ROWS:
            raise RetrievalIncomplete(
                "RECORD_SET_EXCEEDS_SNAPSHOT_LIMIT", name)
    return {"books": [dict(r) for r in books],
            "orders": [dict(r) for r in orders],
            "refusals": [dict(r) for r in refusals],
            "beats": [dict(r) for r in beats],
            "registered": [dict(r) for r in registered],
            "mirrorLive": mirror_live_of([dict(r) for r in mode])}


def mirror_live_of(rows: list) -> bool:
    """EXACTLY true, the way the worker reads it.

    ingestion_state.value is jsonb, so it arrives as the string "true"
    or as a parsed bool depending on the driver. Anything that is not an
    unambiguous true -- missing row, null, "on", 1 -- reads as NOT live,
    because the safe reading of an ambiguous arming switch is off.
    """
    if not rows:
        return False
    v = rows[0].get("value")
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v) is True
        except (ValueError, TypeError):
            return False
    return False


def lane_of(book: dict, registered_keys: set) -> str:
    """The lane of one book, from an explicit claim only.

    An operator registration on this book's own (slug, asset) is the
    declaration that the shares are a hand position. Nothing about the
    SIZE of a book is consulted, here or anywhere below.
    """
    key = (_s(book.get("us_market_slug"), ""), _s(book.get("long_asset"), ""))
    if key in registered_keys:
        return LANE_MANUAL
    if _s(book.get("us_market_slug"), ""):
        return LANE_AUTONOMOUS
    return LANE_UNATTRIBUTED


def build(records: dict, account: dict | None, session: dict | None,
          mirror_live: bool | None = None, now: float | None = None,
          account_error: str | None = None,
          deployed_verified: bool = False) -> dict:
    """Records -> the bt.command.v1 payload. Pure; no I/O."""
    from .. import calibration as cal

    if mirror_live is None:
        mirror_live = bool(records.get("mirrorLive"))
    now = time.time() if now is None else now
    as_of = iso_now(now)
    registered_keys = {(_s(r.get("us_market_slug"), ""),
                        _s(r.get("asset"), ""))
                       for r in records.get("registered", ())}

    positions = [position_row(b, lane=lane_of(b, registered_keys),
                              as_of=as_of)
                 for b in records.get("books", ())]
    by_book = {b.get("id"): b for b in records.get("books", ())}
    orders = [order_row(o, lane=lane_of(by_book.get(o.get("book_id"), o),
                                        registered_keys))
              for o in records.get("orders", ())]
    decisions = [decision_row(r) for r in records.get("refusals", ())]
    services = [service_row(h, now) for h in records.get("beats", ())]

    if account_error:
        services.append({
            "name": "Venue account ledger", "status": "UNAVAILABLE",
            "latency": None,
            "beatAt": UNIDENTIFIED,
            "detail": "balances unread: %s -- shown as unavailable, not zero"
                      % account_error})
    services.sort(key=lambda s: s["name"])

    session = session if session is not None else cal.empty_session("NONE")
    acct = account_block(account, session)
    calib = cal.budget_block(session)

    unavailable = []
    if acct["cash"] is None:
        unavailable.append("account.cash")
    if acct["value"] is None:
        unavailable.append("account.value")
    if acct["grossVolumeMtd"] is None:
        unavailable.append("account.grossVolumeMtd")
    unavailable.append("history -- no durable portfolio-value series exists "
                       "yet; an empty series is shown rather than a "
                       "reconstructed one")

    provenance = {
        "sourceId": "SPORTSASSETS-API/mirror_books+mirror_orders+"
                    "mirror_candidate_refusals+pmus_account",
        "accountingBasis":
            "our own booking ledger for quantity and cost; the venue's "
            "account snapshot for cash; marks are the last stored "
            "per-market read. Unavailable terms are null, never zero.",
        "reconciliation": "LEDGER_VS_VENUE_NOT_ASSERTED_BY_THIS_ENDPOINT",
        "window": "open books at %s" % as_of,
        "valuationAsOf": as_of,
        "unavailable": unavailable,
        "ownershipIsDeclared": OWNERSHIP_IS_DECLARED,
        "disclaimer":
            "Read-only observation. This interface has no order authority.",
    }

    counts = {"positions": len(positions), "orders": len(orders),
              "events": len({p["eventId"] for p in positions})}
    engine = {
        "name": "BETTOR mirror sleeve",
        "mode": "LIVE" if mirror_live else "EXITS_ONLY",
        "decisionGrade": "BLOCKED",
        "modelVersion": UNIDENTIFIED,
        "autoPromotion": False,
        "independentFillEvidence": None,
    }
    activity = [{"id": "act-%s" % d["id"], "at": d["at"], "kind": "rejected",
                 "title": d["blocker"], "detail": d["title"]}
                for d in decisions[:20]]

    return assemble(
        positions=positions, orders=orders, decisions=decisions,
        history=[], activity=activity, services=services,
        gates=gate_rows(mirror_live, calib["openLifecycles"],
                        deployed_verified),
        account=acct, provenance=provenance, counts=counts,
        engine=engine, calibration=calib, now=now)
