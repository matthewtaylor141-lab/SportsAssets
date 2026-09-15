"""THE SALE-LEG LEDGER (cash-out program, unit U1): one row per sale
leg, keyed by the archive's own key, folded from the lossless activity
archive.

MEASUREMENT ONLY. Nothing here is wired into a served endpoint, a
worker or a money path: this module reads `pmus_activity_archive` and
writes `copy_exit_legs` (migration 052), and no reader of any served
number reads either. Importing it changes nothing; running its sweep
changes no served figure. That is deliberate -- the unit that WRITES a
hand sale back onto one of our order rows comes later, and it cannot be
written at all until the facts below exist.

WHY THE FACTS DO NOT EXIST YET
------------------------------
When a copy-engine position is sold by hand in the venue app, the
repository records nothing: our row stays status='filled' for ever,
counted as live exposure with shares behind it we no longer own, and no
resolution ever arrives to grade it. Writing that row back needs the
venue's execution ORDER ID -- the field that can tie one sale to the
order that made it. The repository reads every sale twice and drops
that id both times:

    api/track_record.py `_fold_trade`  reduces a sale to ONE dict per
        market slug ({qty, proceeds, realized, last_ts}); two sales on
        one slug become indistinguishable and no order id survives.
    api/track_record.py `_slim`        lifts the nested execution
        order's SIDE to the top level and then discards the order
        object, id included, for the whole in-memory archive.

The TABLE they read from keeps the venue's full payload, append-only,
keyed by the venue's own activity id. So the id survives in Postgres
and nowhere else, and this module is that survival made queryable.

THE FOLD'S RULES (the whole correctness of this unit)
----------------------------------------------------
1. THE NESTED EXECUTION ORDER'S SIDE WINS over the realized-is-non-zero
   fallback. The venue's top-level `side` is always None in this feed
   (raw-feed audit 2026-08-19, 6,747 trades); the definitive side is on
   the nested order. The fallback alone misread 444 zero-P&L sells as
   buys and booked 23 short-CLOSING BUYS as sales.
2. A BREAK-EVEN SALE WITH NO NESTED ORDER IS `unknown`, NEVER A BUY.
   Nothing the venue sent can separate it from a fresh entry. It is
   stored named, contributing nothing to any total, so it can be
   COUNTED instead of silently booked either way. (Today the record
   folds exactly this row as an entry, inflating deployed capital.)
3. A REDEMPTION AND A MERGE ARE NOT SALES. Only ACTIVITY_TYPE_TRADE can
   produce a leg; every other shape is refused under its own name.
4. THE VENUE'S OWN `realizedPnl` IS CARRIED, NEVER RECOMPUTED. Not
   proceeds minus a basis we reconstruct, not a price read off a bid.
   `proceeds_usd` (qty*price) lives in its own column so the two can
   never be mistaken for one another.

WHAT THE KEY IS (read this before joining on it)
------------------------------------------------
`venue_activity_id` is the ARCHIVE ROW'S key, stored verbatim so the
two tables join: the venue's own activity id wherever the venue sent
one, and a content hash (sha256 of the sorted-key payload,
`track_record.py:1864-1867`) where it did not. The archive makes that
substitution, not this fold; what this fold never does is INVENT a key
-- an activity handed to it outside the archive path with no id of its
own is refused (`no_activity_id`). A reader taking one of these ids
back to the VENUE must check it against `pmus_activity_archive` first:
a 64-hex key is ours, not theirs.

THE GATE
--------
The per-slug sum of this ledger must reproduce `track_record`'s
`sold_markets` TO THE CENT:

    qty       sum(shares)       over side = 'sell'
    proceeds  sum(proceeds_usd) over side = 'sell'
    realized  sum(realized_usd) over side IN ('sell', 'buy_close')
    last_ts   max(ts)           over side <> 'unknown'

`sold_markets` is built from the venue's own tape and already counts
hand sales correctly. It is the yardstick: if this ledger disagrees
with it, THIS LEDGER IS WRONG. `gate_from_activities` proves that half
in pure Python -- against track_record's own `_fold_trade`, imported
rather than copied, so the two cannot drift -- and `gate_report` runs
the same comparison against a live database.

THE GATE'S SCOPE, STATED (it is not the whole ledger by default).
The SERVED `sold_markets` is truncated to the newest 30 markets
(`track_record.py:949`). Comparing against it therefore checks at most
30 slugs, and on that path `ledger_only` is empty by construction --
the read asks for those slugs and gets those slugs. That is a coverage
limit, not agreement, so `gate_report`:

  * REFUSES an empty or absent yardstick outright (`ok: False`,
    `reason: 'empty_yardstick'`). Comparing nothing and reporting
    success is the one failure mode a money-path gate may never have;
  * REFUSES to run at all on a sweep that did not complete a pass;
  * reports `outside_scope` -- how many ledger slugs the served list
    never named and how much money sits on them;
  * and gates the WHOLE ledger, all slugs, no [:30], when it is handed
    the archive-wide yardstick that `sweep(yardstick=True)` folds as it
    goes.

WHAT IS DELIBERATELY NOT STORED
-------------------------------
* A leg's `row_id` (which of OUR rows the sale closed). The fold knows
  what the venue did, not which of our rows it closed, and when two of
  our rows share one slug that question has no answer in this data.
  The column exists; the fold never writes it.
* Cash-in `shares` for a `buy_close` or an `unknown` leg (both pinned
  to 0 by migration 052's CHECKs). Neither is cash in, and a reader
  summing shares without filtering on side must not be able to subtract
  them from anything. The QUANTITY is not lost: `trade_qty` carries the
  venue's raw qty on every leg, which is what a writer closing a SHORT
  (closed by a BUY) has to read.
* A trade outside the fold's band (qty <= 0, or price outside (0,1)),
  and a trade carrying a non-finite amount. `_fold_trade` ignores the
  first, so the gate requires this ledger to ignore it too; the second
  cannot be stored in NUMERIC(24,6) at all. Both are counted under a
  named refusal reason, never stored -- so the ledger's `unknown` count
  is the undecidable population INSIDE the band, and the whole
  undecidable population is measured by `SIDE_CENSUS_SQL`, which counts
  every archived trade regardless of band.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Iterable, NamedTuple

# Same amount/timestamp readers the record's own fold uses. Imported,
# not re-implemented: a private copy of `_amt` that rounded differently
# would break the gate in a way no test of this module could see.
from ..api.pmus_account import _act_ts, _amt

log = logging.getLogger(__name__)

TRADE_TYPE = "ACTIVITY_TYPE_TRADE"
SOURCE_ARCHIVE = "pmus_activity_archive"

SIDE_SELL = "sell"
SIDE_BUY_CLOSE = "buy_close"
SIDE_UNKNOWN = "unknown"
#: The sides whose realized dollars are real. `unknown` is excluded
#: everywhere, by name, on purpose.
SIDES_COUNTED: tuple[str, ...] = (SIDE_SELL, SIDE_BUY_CLOSE)
#: The only side that took cash IN. A short-closing BUY realizes money
#: without receiving proceeds; conflating the two is the 2026-08-19
#: defect this ledger is built not to repeat.
SIDES_CASH_IN: tuple[str, ...] = (SIDE_SELL,)

SRC_TOP_LEVEL = "top_level"
SRC_AGGRESSOR = "nested_aggressor"
SRC_PASSIVE = "nested_passive"
SRC_REALIZED_FALLBACK = "realized_fallback"
SRC_NONE = "none"

# Activity shapes that are NOT sales, named so a refusal says which
# shape it refused instead of "other". A redemption and a merge move
# real money and are real exits -- they are simply not TRADES, they
# carry no execution order, and folding either as a sale would invent a
# price the venue never printed. Matched as substrings because the
# venue's enum spelling has moved between payload versions.
NON_SALE_SHAPES: tuple[tuple[str, str], ...] = (
    ("POSITION_RESOLUTION", "resolution"),
    ("REDEMPTION", "redemption"),
    ("REDEEM", "redemption"),
    ("MERGE", "merge"),
    ("SPLIT", "split"),
    ("CONVERSION", "conversion"),
    ("DEPOSIT", "deposit"),
    ("WITHDRAW", "withdrawal"),
)

# Bounds for every database read here (money-path rule: bounded LIMIT
# or a bounded cursor fetch, bounded timeout, fail closed). A chunk of
# 2,000 archive rows is ~1/12 of one day's TRADE+RESOLUTION intake at
# the rate venue_truth's crawl arithmetic states (~25k rows/day).
DEFAULT_CHUNK = 2000
#: 400 x 2,000 = 800,000 rows -- more than the whole archive (531,313
#: rows on 2026-09-03, growing ~24k/day), so one pass can finish.
DEFAULT_MAX_CHUNKS = 400
DEFAULT_TIMEOUT_S = 30.0
#: Whole-sweep wall-clock bound. A pass that runs out of budget stops
#: and says so; its written legs stand (the write is idempotent) and
#: the next run resumes the same pass.
DEFAULT_BUDGET_S = 900.0
#: Cursor key in `ingestion_state`. It records a PASS: where the last
#: pass stopped and whether that pass finished. It is NOT a
#: high-water mark of the archive -- see `sweep`.
CURSOR_KEY = "copy_exit_legs_cursor"


@dataclass(frozen=True)
class SaleLeg:
    """One sale leg, exactly as migration 052 stores it."""

    venue_activity_id: str
    ts: float | None
    market_slug: str
    shares: float
    trade_qty: float
    price: float | None
    proceeds_usd: float
    realized_usd: float
    order_id: str | None
    side: str
    side_src: str
    source: str = SOURCE_ARCHIVE
    row_id: int | None = None

    def as_params(self) -> tuple:
        """Insert parameters, in _INSERT_SQL's order."""
        return (self.venue_activity_id, self.ts, self.market_slug,
                self.shares, self.trade_qty, self.price, self.proceeds_usd,
                self.realized_usd, self.order_id, self.side,
                self.side_src, self.source)


#: The insert's column list, as one name per parameter, in order. The
#: SQL below is built FROM this tuple and `as_params` is asserted
#: against it field by field, so a transposition of two money columns
#: cannot pass unseen (it did: the review's mutation 28 swapped
#: proceeds_usd and realized_usd and every test stayed green).
INSERT_COLUMNS: tuple[str, ...] = (
    "venue_activity_id", "ts", "market_slug", "shares", "trade_qty",
    "price", "proceeds_usd", "realized_usd", "order_id", "side",
    "side_src", "source")


class FoldResult(NamedTuple):
    """(leg, reason). `leg` is None whenever `reason` != 'ok'; the
    reason always names WHY, so a refusal is a census line and never a
    silent drop."""

    leg: SaleLeg | None
    reason: str


@dataclass
class LedgerFold:
    """What a fold of many activities produced."""

    legs: list[SaleLeg]
    refused: dict[str, int]
    duplicates: int = 0

    @property
    def unknown_legs(self) -> list[SaleLeg]:
        return [g for g in self.legs if g.side == SIDE_UNKNOWN]

    def counts(self) -> dict[str, int]:
        return {
            "legs": len(self.legs),
            "sell": sum(1 for g in self.legs if g.side == SIDE_SELL),
            "buy_close": sum(1 for g in self.legs
                             if g.side == SIDE_BUY_CLOSE),
            "unknown": sum(1 for g in self.legs if g.side == SIDE_UNKNOWN),
            "duplicates": self.duplicates,
        }


def _payload(raw: Any) -> dict | None:
    """A jsonb column comes back as a dict or as text depending on the
    codec in force. Unreadable is refused, never guessed."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            out = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return out if isinstance(out, dict) else None
    return None


def _nested_order(t: dict) -> tuple[dict, str]:
    """The nested execution order and which execution carried it.

    The two keys `pmus.trade_order` and `track_record._fold_trade` read,
    in that order -- but the PREFERENCE differs from `trade_order`'s on
    one shape and the difference is deliberate: this fold takes the
    first execution that names a SIDE (the side is what the
    classification turns on), and only when neither names one does it
    fall back to the first non-empty order, which is what
    `pmus.trade_order` returns unconditionally. So for a trade whose
    aggressor order carries an id and no side while the passive one
    carries both, `trade_order` returns the aggressor's id and this
    returns the passive's. `side_src` records which execution the side
    came from; the payload behind the leg's key settles anything else.

    Returns ({}, SRC_NONE) when the venue named neither -- the
    population rule 2 exists for.
    """
    for key, src in (("aggressorExecution", SRC_AGGRESSOR),
                     ("passiveExecution", SRC_PASSIVE)):
        o = ((t.get(key) or {}).get("order") or {})
        if isinstance(o, dict) and o.get("side"):
            return o, src
    # An execution with an order that names no side is still the order
    # this trade belongs to: its id is worth keeping even though the
    # side is not there to read.
    for key, src in (("aggressorExecution", SRC_AGGRESSOR),
                     ("passiveExecution", SRC_PASSIVE)):
        o = ((t.get(key) or {}).get("order") or {})
        if isinstance(o, dict) and o:
            return o, src
    return {}, SRC_NONE


def _order_id(o: dict) -> str | None:
    """The execution order's own id, the field `pmus.recent_trades`
    reads. None when the venue named none -- unknown, never a
    placeholder, and never derived from anything else."""
    v = o.get("id")
    if v is None or v == "":
        return None
    out = str(v).strip()
    return out[:200] or None


def _shape_reason(kind: str) -> str:
    for needle, name in NON_SALE_SHAPES:
        if needle in kind:
            return f"not_a_sale:{name}"
    return f"not_a_sale:{(kind or 'untyped').lower()[:60]}"


def fold_activity(act: Any, *, activity_id: str | None = None,
                  source: str = SOURCE_ARCHIVE) -> FoldResult:
    """One venue activity -> one sale leg, or a named refusal.

    `activity_id` is the archive ROW's key when folding from the table
    (that is the id the row is stored under, whether or not the payload
    repeats it, and whether the venue named it or the archive hashed
    it). Without one, the payload's own id is used, and an activity
    that carries no id at all is REFUSED: an append-only ledger keyed
    by the archive's key may never invent one of its own.

    The classification, in the order it is decided:

      side names SELL                -> 'sell'      (cash in)
      side names anything else, rp!=0-> 'buy_close' (a short closing:
                                        realized dollars, no proceeds)
      side names anything else, rp==0-> refused 'entry_buy'
      no side anywhere, rp != 0      -> 'sell', side_src
                                        'realized_fallback' (the
                                        record's own fallback, carried
                                        but LABELLED as inferred)
      no side anywhere, rp == 0      -> 'unknown'   (rule 2)

    which reproduces `track_record._fold_trade`'s classification
    exactly, plus the name for the case that fold cannot express.
    """
    a = _payload(act)
    if a is None:
        return FoldResult(None, "unreadable_payload")

    kind = str(a.get("type") or "")
    if kind != TRADE_TYPE:
        return FoldResult(None, _shape_reason(kind))

    t = a.get("trade")
    if not isinstance(t, dict) or not t:
        return FoldResult(None, "no_trade_object")

    aid = activity_id if activity_id not in (None, "") else a.get("id")
    aid = str(aid).strip() if aid not in (None, "") else ""
    if not aid:
        return FoldResult(None, "no_activity_id")

    slug = t.get("marketSlug")
    if not slug:
        # Exactly `_fold_trade`'s first refusal: no slug, nothing to
        # fold, the row stays raw.
        return FoldResult(None, "no_market_slug")

    # `_act_ts(act) or _act_ts(t)` -- the same expression the record's
    # fold dates a sale with, so this ledger's ts and sold_markets'
    # last_ts are the same clock. 0 means the venue dated nothing and
    # is stored as NULL: unknown is not now().
    ts = _act_ts(a) or _act_ts(t)
    qty, price = _amt(t.get("qty")), _amt(t.get("price"))
    realized = _amt(t.get("realizedPnl"))

    # NaN and Infinity are legal JSON-text scalars and `float()` takes
    # both, so they reach here. NaN is the dangerous one: `nan <= 0` is
    # False, so the band guard below lets it through, while
    # `_fold_trade`'s mirror-image `qty > 0` is also False, so the
    # record folds NOTHING -- the two disagree and the gate breaks on a
    # row carrying no measurable money at all. Infinity is worse: it
    # cannot be stored in NUMERIC(24,6), so one such row would abort
    # the whole page's insert for ever. Refused by name, at the top,
    # before any classification reads them.
    if not (math.isfinite(qty) and math.isfinite(price)
            and math.isfinite(realized)):
        return FoldResult(None, "non_finite_amount")

    side_txt = str(t.get("side") or "").upper()
    src = SRC_TOP_LEVEL if side_txt else SRC_NONE
    order, order_src = _nested_order(t)
    if not side_txt and order.get("side"):
        side_txt = str(order["side"]).upper()
        src = order_src
    oid = _order_id(order)

    if side_txt:
        if "SELL" in side_txt:
            side = SIDE_SELL
        elif realized != 0:
            # A BUY that realized money is a short being closed. Real
            # dollars, no cash in.
            side = SIDE_BUY_CLOSE
        else:
            return FoldResult(None, "entry_buy")
    elif realized != 0:
        # No side anywhere. A buy under average-cost accounting never
        # carries realized P&L, so this is a sale -- but the side was
        # INFERRED, and the row says so.
        side, src = SIDE_SELL, SRC_REALIZED_FALLBACK
    else:
        # Rule 2. Break-even, no side, no nested order: undecidable.
        side, src = SIDE_UNKNOWN, SRC_NONE

    # The record's fold counts a closing trade only inside this band,
    # so the ledger must refuse outside it or the gate cannot hold.
    # These two refusals are counted, and they are why the ledger's own
    # `unknown` count is the undecidable population INSIDE the band and
    # not the whole of it: SIDE_CENSUS_SQL measures the whole of it.
    if qty <= 0:
        return FoldResult(None, "no_qty")
    if not (0 < price < 1):
        return FoldResult(None, "outside_price_band")

    if side == SIDE_SELL:
        shares, proceeds = qty, qty * price
    else:
        # buy_close: realized dollars, but its qty*price is not cash in
        # and its qty is not a cash-in quantity (it IS the quantity the
        # leg retired, which `trade_qty` carries).
        # unknown: no money at all, in either direction.
        shares, proceeds = 0.0, 0.0

    return FoldResult(
        SaleLeg(venue_activity_id=aid,
                ts=float(ts) if ts else None,
                market_slug=str(slug),
                shares=shares,
                trade_qty=qty,
                price=price,
                proceeds_usd=proceeds,
                realized_usd=realized if side in SIDES_COUNTED else 0.0,
                order_id=oid,
                side=side,
                side_src=src,
                source=source),
        "ok")


def fold_activities(acts: Iterable[Any], *,
                    source: str = SOURCE_ARCHIVE) -> LedgerFold:
    """Fold many activities. First occurrence of an id wins (the table
    is append-only and ON CONFLICT DO NOTHING; the in-memory fold says
    the same thing so a dry run and a write agree)."""
    legs: list[SaleLeg] = []
    seen: set[str] = set()
    refused: dict[str, int] = {}
    dupes = 0
    for act in acts or []:
        leg, reason = fold_activity(act, source=source)
        if leg is None:
            refused[reason] = refused.get(reason, 0) + 1
            continue
        if leg.venue_activity_id in seen:
            dupes += 1
            continue
        seen.add(leg.venue_activity_id)
        legs.append(leg)
    return LedgerFold(legs=legs, refused=refused, duplicates=dupes)


def per_slug(legs: Iterable[SaleLeg]) -> dict[str, dict]:
    """Per-slug totals in `sold_markets`' own shape.

    The side filters are the whole point: an `unknown` leg contributes
    nothing, a `buy_close` contributes realized dollars but never
    proceeds or shares.
    """
    out: dict[str, dict] = {}
    for g in legs:
        s = out.setdefault(g.market_slug,
                           {"qty": 0.0, "proceeds": 0.0, "realized": 0.0,
                            "last_ts": 0.0, "legs": 0, "sell": 0,
                            "buy_close": 0, "unknown": 0})
        s["legs"] += 1
        s[g.side] += 1
        if g.side in SIDES_CASH_IN:
            s["qty"] += g.shares
            s["proceeds"] += g.proceeds_usd
        if g.side in SIDES_COUNTED:
            s["realized"] += g.realized_usd
            if g.ts:
                s["last_ts"] = max(s["last_ts"], g.ts)
    return out


# ── THE GATE ─────────────────────────────────────────────────────────


def sold_ledger(acts: Iterable[Any]) -> dict[str, dict]:
    """THE YARDSTICK: the `sold` dict `track_record.build` derives from
    the same activities, produced by track_record's OWN fold.

    Imported, never re-implemented. A local copy would let the yardstick
    drift from the thing it measures, which is the one failure this gate
    exists to make impossible. (The import is deferred: this module must
    stay importable without the record's dependency graph.)
    """
    from ..api.track_record import _fold_trade

    entries: dict[str, dict] = {}
    sold: dict[str, dict] = {}
    for act in acts or []:
        a = _payload(act)
        if a is None or a.get("type") != TRADE_TYPE:
            continue
        _fold_trade(a, entries, sold)
    return sold


def _cents(x: float) -> float:
    return round(float(x or 0.0), 2)


def compare(ledger: dict[str, dict], yardstick: dict[str, dict],
            *, slugs: Iterable[str] | None = None) -> dict:
    """Per-slug comparison of this ledger against `sold_markets`-shaped
    totals, TO THE CENT.

    `slugs` bounds the comparison to a named set. Read the bound for
    what it is: when the caller passes the yardstick's own keys (which
    `gate_report` does, because the served `sold_markets` is truncated
    to the newest 30 markets), `ledger_only` is EMPTY BY CONSTRUCTION --
    the set difference is taken against those same keys. It is only a
    real signal when the caller bounds the comparison to fewer slugs
    than the ledger holds. `gate_report` reports the ledger outside the
    served window separately, with its money, and never as agreement.
    """
    keys = ([str(s) for s in slugs] if slugs is not None
            else sorted(set(ledger) | set(yardstick)))
    mismatches: list[dict] = []
    checked = 0
    for slug in keys:
        mine = ledger.get(slug)
        theirs = yardstick.get(slug)
        if mine is None and theirs is None:
            continue
        checked += 1
        mine = mine or {}
        theirs = theirs or {}
        diffs = {}
        for field in ("qty", "proceeds", "realized"):
            a, b = _cents(mine.get(field)), _cents(theirs.get(field))
            if a != b:
                diffs[field] = {"ledger": a, "sold_markets": b,
                                "delta": round(a - b, 2)}
        a_ts = float(mine.get("last_ts") or 0.0)
        b_ts = float(theirs.get("last_ts") or 0.0)
        if a_ts != b_ts:
            diffs["last_ts"] = {"ledger": a_ts or None,
                                "sold_markets": b_ts or None}
        if diffs:
            # Presence is context on a real disagreement, never a
            # disagreement by itself: a slug whose only leg is
            # `unknown` is IN this ledger and correctly absent from
            # sold_markets, and it carries no money either way.
            if mine and not theirs:
                diffs["present"] = "ledger only"
            elif theirs and not mine:
                diffs["present"] = "sold_markets only"
            mismatches.append({"slug": slug, **diffs})
    only = sorted(set(ledger) - set(keys)) if slugs is not None else []
    return {"ok": not mismatches, "slugs_checked": checked,
            "mismatches": mismatches[:50],
            "mismatch_count": len(mismatches),
            "ledger_only": only[:50], "ledger_only_count": len(only)}


def gate_from_activities(acts: Iterable[Any]) -> dict:
    """THE GATE, run purely: fold these activities both ways and prove
    the per-slug totals agree to the cent.

    This is the half that can be proved without a database. The other
    half -- that the ledger built from the PRODUCTION archive agrees
    with the PRODUCTION sold_markets -- is `gate_report`, and it has to
    be run where the data is.

    One deliberate asymmetry: this ledger folds a repeated activity id
    ONCE (the table's key is that id and the write is ON CONFLICT DO
    NOTHING), while `_fold_trade` folds it twice. A tape carrying a
    duplicate id therefore mismatches here and is not a fold bug. It
    cannot occur in the archive, whose id is a primary key.

    It also cannot see rule 2 (the undecidable break-even). The
    yardstick folds that row into `entries`, so no comparison against
    `sold_markets` can detect it in either direction; its size is
    measured by SIDE_CENSUS_SQL and by nothing else.
    """
    fold = fold_activities(acts)
    result = compare(per_slug(fold.legs), sold_ledger(acts))
    result["fold"] = fold.counts()
    result["refused"] = dict(fold.refused)
    return result


# ── DATABASE (bounded reads, append-only writes, no served number) ────

#: The BACKFILL read: one ordered server-side cursor, streamed. Not
#: repeated LIMIT pages -- track_record.py:1702-1706 records what that
#: shape does on this exact table ("the planner answered 'WHERE id > $1
#: ORDER BY id LIMIT n' with a fresh scan of the whole table per chunk
#: (probe 2026-08-09 15:00Z: TimeoutError at chunk 6, 30s+ per chunk)
#: -- 73 chunks of that never finishes. A cursor scans once and
#: streams."). `id > $1` here is a WITHIN-PASS resume point, never a
#: high-water mark: see `sweep`.
_ARCHIVE_STREAM_SQL = """
    SELECT id, payload
      FROM pmus_activity_archive
     WHERE id > $1
       AND payload->>'type' = $2
     ORDER BY id
"""

#: A single bounded page, for a caller that wants one chunk and no
#: transaction (and for the tests). It carries the same ordering
#: caveat: a page is a page, not a claim about what the table holds.
_ARCHIVE_PAGE_SQL = """
    SELECT id, payload
      FROM pmus_activity_archive
     WHERE id > $1
       AND payload->>'type' = $2
     ORDER BY id
     LIMIT $3
"""

_INSERT_SQL = f"""
    INSERT INTO copy_exit_legs
        ({", ".join(INSERT_COLUMNS)})
    VALUES ($1, $2, $3, $4::float8::numeric, $5::float8::numeric,
            $6::float8::numeric, $7::float8::numeric, $8::float8::numeric,
            $9, $10, $11, $12)
    ON CONFLICT (venue_activity_id) DO NOTHING
"""

#: Per-slug ledger totals, in `sold_markets`' shape. The FILTERs are
#: the contract: they are what makes the gate's arithmetic the
#: yardstick's arithmetic. Built from SIDES_CASH_IN / SIDES_COUNTED so
#: the SQL and the fold cannot drift, and asserted clause by clause in
#: the tests (a FILTER the migration's CHECKs make redundant is still a
#: FILTER a later schema change can strand).
_CASH_IN_SQL = ", ".join(f"'{s}'" for s in SIDES_CASH_IN)
_COUNTED_SQL = ", ".join(f"'{s}'" for s in SIDES_COUNTED)

LEDGER_TOTALS_SQL = f"""
    SELECT market_slug,
           COALESCE(sum(shares) FILTER (WHERE side IN ({_CASH_IN_SQL})),
                    0)::float8 AS qty,
           COALESCE(sum(proceeds_usd)
                    FILTER (WHERE side IN ({_CASH_IN_SQL})),
                    0)::float8 AS proceeds,
           COALESCE(sum(realized_usd)
                    FILTER (WHERE side IN ({_COUNTED_SQL})),
                    0)::float8 AS realized,
           max(ts) FILTER (WHERE side <> '{SIDE_UNKNOWN}') AS last_ts,
           count(*) AS legs,
           count(*) FILTER (WHERE side = '{SIDE_UNKNOWN}') AS unknown_legs
      FROM copy_exit_legs
     WHERE ($1::text[] IS NULL OR market_slug = ANY($1::text[]))
       AND market_slug > $2
     GROUP BY 1
     ORDER BY 1
     LIMIT $3
"""

#: THE COVERAGE READ. What the gate did NOT compare, with its money on
#: it: every ledger slug the served `sold_markets` never named. On the
#: production path that list is the newest 30 markets, and the rows the
#: next unit exists to retire are stranded copies weeks old -- exactly
#: the slugs outside it. Reporting this as a number with dollars beside
#: it is the difference between a bounded gate and a gate that looks
#: green because it looked at nothing.
OUTSIDE_SCOPE_SQL = f"""
    SELECT count(DISTINCT market_slug) AS slugs,
           count(*) AS legs,
           COALESCE(sum(shares) FILTER (WHERE side IN ({_CASH_IN_SQL})),
                    0)::float8 AS qty,
           COALESCE(sum(proceeds_usd)
                    FILTER (WHERE side IN ({_CASH_IN_SQL})),
                    0)::float8 AS proceeds,
           COALESCE(sum(realized_usd)
                    FILTER (WHERE side IN ({_COUNTED_SQL})),
                    0)::float8 AS realized
      FROM copy_exit_legs
     WHERE NOT (market_slug = ANY($1::text[]))
     LIMIT 1
"""

#: THE POPULATION RULE 2 EXISTS FOR, measured in production and nowhere
#: else: how many archived trades name no side anywhere and carry
#: realized P&L of exactly zero. Every one of those is folded as an
#: ENTRY by the record today, so a NULL side bucket with a non-zero
#: break_even count IS the money being mis-booked. Read-only, one
#: grouped scan, bounded output.
#:
#: The realized read is the plan's own form with one guard added: the
#: value is cast only when it LOOKS numeric. A bare `::float8` on a
#: non-numeric string aborts the whole grouped scan ("invalid input
#: syntax for type double precision") and takes the only measurement of
#: the undecidable population with it. Unreadable and absent both land
#: in break_even, which OVER-states the ambiguous population rather
#: than hiding any of it -- the direction a census of "what we cannot
#: decide" has to fail in.
SIDE_CENSUS_SQL = """
    SELECT COALESCE(payload->'trade'->'aggressorExecution'->'order'->>'side',
                    payload->'trade'->'passiveExecution'->'order'->>'side',
                    payload->'trade'->>'side') AS side,
           count(*) FILTER (
               WHERE COALESCE(
                   CASE WHEN payload->'trade'->'realizedPnl'->>'value' ~
                             '^-?[0-9]+(\\.[0-9]+)?([eE][-+]?[0-9]+)?$'
                        THEN (payload->'trade'->'realizedPnl'->>'value')
                             ::float8
                   END, 0) = 0) AS break_even,
           count(*) AS trades
      FROM pmus_activity_archive
     WHERE payload->>'type' = 'ACTIVITY_TYPE_TRADE'
     GROUP BY 1
     ORDER BY 3 DESC
     LIMIT 50
"""


def _parse_rows(rows: Any) -> list[tuple[str, Any]]:
    """(archive key, payload dict|None) for a chunk of archive rows.

    Run off the event loop: the payloads are jsonb text under the
    codec this repository registers, and `json.loads` on 2,000 of them
    is the work track_record's own hydrate moved into a thread
    (`track_record.py:1719-1732`) after it strangled the loop.
    """
    return [(str(r["id"]), _payload(r["payload"])) for r in rows]


async def _read_cursor(pool: Any, *, timeout: float) -> dict:
    """The pass cursor: {'last_id', 'pass_complete'}.

    An absent or unreadable cursor reads as "no pass in progress",
    which starts a full pass -- the safe direction, since a full pass
    is what makes the ledger complete.
    """
    raw = await asyncio.wait_for(
        pool.fetchval("SELECT value FROM ingestion_state WHERE key = $1",
                      CURSOR_KEY), timeout)
    try:
        doc = raw if isinstance(raw, dict) else (json.loads(raw) if raw
                                                 else None)
    except (ValueError, TypeError):
        doc = None
    doc = doc if isinstance(doc, dict) else {}
    return {"last_id": str(doc.get("last_id") or ""),
            "pass_complete": bool(doc.get("pass_complete", True))}


async def _write_cursor(pool: Any, last_id: str, *, complete: bool,
                        timeout: float) -> None:
    await asyncio.wait_for(
        pool.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            CURSOR_KEY,
            json.dumps({"last_id": last_id, "pass_complete": complete,
                        "at": time.time()})), timeout)


async def _write_legs(pool: Any, legs: list[SaleLeg], *,
                      timeout: float) -> None:
    if not legs:
        return
    await asyncio.wait_for(
        pool.executemany(_INSERT_SQL, [g.as_params() for g in legs]),
        timeout)


async def ingest_page(pool: Any, *, after_id: str = "",
                      limit: int = DEFAULT_CHUNK,
                      timeout: float = DEFAULT_TIMEOUT_S) -> dict:
    """Fold ONE bounded page of archived TRADE activities and return
    what it did.

    Bounded on every axis (money-path rule): a LIMIT on the read, a
    timeout on the read and on the write, and only TRADE rows fetched
    (the type filter is in SQL, so a resolution's payload is never even
    parsed). The write is INSERT ... ON CONFLICT DO NOTHING: the table
    is append-only and a re-run of the same page is a no-op.

    `done` means THIS PAGE did not fill its limit. It is not a claim
    that the ledger is complete -- for that, run a `sweep` pass.
    """
    limit = max(1, min(int(limit), 20000))
    rows = await asyncio.wait_for(
        pool.fetch(_ARCHIVE_PAGE_SQL, str(after_id or ""), TRADE_TYPE, limit),
        timeout)
    legs: list[SaleLeg] = []
    refused: dict[str, int] = {}
    last_id = str(after_id or "")
    for rid, doc in await asyncio.to_thread(_parse_rows, rows):
        last_id = rid
        leg, reason = fold_activity(doc, activity_id=rid)
        if leg is None:
            refused[reason] = refused.get(reason, 0) + 1
            continue
        legs.append(leg)
    await _write_legs(pool, legs, timeout=timeout)
    return {"scanned": len(rows), "legs_written": len(legs),
            "refused": refused, "last_id": last_id,
            "done": len(rows) < limit,
            "sides": {s: sum(1 for g in legs if g.side == s)
                      for s in (SIDE_SELL, SIDE_BUY_CLOSE, SIDE_UNKNOWN)}}


async def sweep(pool: Any, *, chunk: int = DEFAULT_CHUNK,
                max_chunks: int = DEFAULT_MAX_CHUNKS,
                timeout: float = DEFAULT_TIMEOUT_S,
                budget_s: float = DEFAULT_BUDGET_S,
                resume: bool = True, yardstick: bool = True) -> dict:
    """ONE PASS over every archived TRADE row, streamed, resumable.

    WHY A PASS AND NOT A HIGH-WATER MARK. The obvious incremental
    sweep -- remember the largest archive id folded, resume above it --
    is lossless only if archive ids increase with arrival. They do not.
    `pmus_activity_archive.id` is the venue's own opaque activity id,
    and for an activity the venue did not name the archive synthesises
    `sha256(payload)` (`track_record.py:1864-1867`), which sorts
    anywhere. A sale arriving under a key below the mark would be
    skipped for ever while the sweep reported itself caught up -- the
    fact base silently incomplete, which is the one thing this unit may
    not be.

    So the cursor records a PASS, not a mark: a pass always begins at
    the start of the table, and the stored `last_id` exists only to
    finish an INTERRUPTED pass. When the previous pass completed, this
    one starts again from the beginning. A full re-scan is affordable
    precisely because the write is ON CONFLICT DO NOTHING: re-folding a
    row already stored costs a no-op insert.

    `caught_up` is therefore a claim about a pass -- every row this
    pass reached is folded -- and rows inserted BELOW the cursor while
    a pass runs are picked up by the next pass, never lost.

    Bounded: a server-side cursor inside one transaction (the shape
    track_record's hydrate was forced onto, after repeated LIMIT pages
    on this table timed out at 30s+ each), `chunk` rows per fetch, a
    per-fetch timeout, `max_chunks` fetches and a wall-clock budget. A
    fetch that times out or a budget that runs out STOPS the pass and
    reports it, with everything folded so far written and the resume
    point stored -- never a traceback and never a partial pass claiming
    to be complete.

    `yardstick=True` folds the same rows through `track_record`'s own
    `_fold_trade` as they stream, so the caller ends a completed pass
    holding the archive-wide `sold` ledger -- every slug, no [:30] --
    which is what lets `gate_report` gate the WHOLE ledger instead of
    the newest 30 markets.
    """
    doc = await _read_cursor(pool, timeout=timeout) if resume else {
        "last_id": "", "pass_complete": True}
    start = "" if doc["pass_complete"] else doc["last_id"]
    resumed = bool(start)

    fold_yard = None
    entries: dict[str, dict] = {}
    sold: dict[str, dict] = {}
    if yardstick:
        from ..api.track_record import _fold_trade as fold_yard  # noqa: N813

    totals: dict[str, Any] = {
        "scanned": 0, "legs_written": 0, "chunks": 0, "refused": {},
        "sides": {SIDE_SELL: 0, SIDE_BUY_CLOSE: 0, SIDE_UNKNOWN: 0},
        "yardstick_errors": 0}
    complete = False
    stopped = None
    last_id = start
    deadline = time.monotonic() + max(1.0, float(budget_s))

    async with pool.acquire() as con:
        async with con.transaction():
            cur = await con.cursor(_ARCHIVE_STREAM_SQL, start, TRADE_TYPE)
            for _ in range(max(1, int(max_chunks))):
                if time.monotonic() > deadline:
                    stopped = "budget_exhausted"
                    break
                try:
                    rows = await asyncio.wait_for(
                        cur.fetch(max(1, min(int(chunk), 20000))), timeout)
                except (asyncio.TimeoutError, TimeoutError):
                    # The recorded failure mode on this table. Stop,
                    # keep what is written, say so: re-running resumes.
                    stopped = "read_timeout"
                    break
                if not rows:
                    complete = True
                    break
                totals["chunks"] += 1
                totals["scanned"] += len(rows)
                legs: list[SaleLeg] = []
                for rid, payload in await asyncio.to_thread(_parse_rows, rows):
                    last_id = rid
                    leg, reason = fold_activity(payload, activity_id=rid)
                    if leg is None:
                        totals["refused"][reason] = (
                            totals["refused"].get(reason, 0) + 1)
                    else:
                        legs.append(leg)
                        totals["sides"][leg.side] += 1
                    if fold_yard is not None and isinstance(payload, dict):
                        try:
                            fold_yard(payload, entries, sold)
                        except Exception:  # noqa: BLE001
                            # The record's fold is less tolerant of a
                            # malformed payload than this one. Counted,
                            # never allowed to stop the pass -- and a
                            # non-zero count invalidates the whole-
                            # ledger comparison, so it is reported.
                            totals["yardstick_errors"] += 1
                # Written on the POOL, not on `con`: the read holds one
                # transaction open for the whole pass, and legs written
                # inside it would roll back with it. Separate
                # connection, separate commit, so an interrupted pass
                # keeps every leg it folded.
                await _write_legs(pool, legs, timeout=timeout)
                totals["legs_written"] += len(legs)
            else:
                stopped = "max_chunks"

    if resume:
        await _write_cursor(pool, last_id, complete=complete, timeout=timeout)

    totals["last_id"] = last_id
    totals["complete_pass"] = complete
    totals["caught_up"] = complete
    totals["stopped"] = stopped
    totals["coverage"] = ("full_pass" if complete and not resumed else
                          "resumed_pass" if complete else
                          f"incomplete:{stopped or 'unknown'}")
    if yardstick:
        totals["yardstick"] = sold if complete else None
        totals["yardstick_slugs"] = len(sold)
    log.info("sale-leg sweep: %s chunks, %s archived trades scanned, "
             "%s legs (sell %s / buy_close %s / unknown %s), coverage=%s",
             totals["chunks"], totals["scanned"], totals["legs_written"],
             totals["sides"][SIDE_SELL], totals["sides"][SIDE_BUY_CLOSE],
             totals["sides"][SIDE_UNKNOWN], totals["coverage"])
    return totals


async def db_per_slug(pool: Any, slugs: Iterable[str] | None = None, *,
                      limit: int = 500, after_slug: str = "",
                      timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, dict]:
    """Per-slug ledger totals from the database, bounded.

    A slug list longer than the bound is REFUSED, not truncated: a gate
    that silently checked 200 of 300 slugs and reported `ok` would be
    the exact failure this table exists to prevent. The unbounded form
    (`slugs=None`) has the same hazard from the other end -- it would
    stop at `limit` with no signal -- so callers that want the whole
    ledger use `db_all_slugs`, which pages and refuses to guess.
    """
    want = [str(s) for s in slugs] if slugs is not None else None
    if want is not None and len(want) > 200:
        raise ValueError(f"gate refuses {len(want)} slugs in one read "
                         f"(bound 200): page the comparison instead")
    rows = await asyncio.wait_for(
        pool.fetch(LEDGER_TOTALS_SQL, want, str(after_slug or ""),
                   max(1, min(int(limit), 5000))),
        timeout)
    return {r["market_slug"]: {"qty": float(r["qty"] or 0.0),
                               "proceeds": float(r["proceeds"] or 0.0),
                               "realized": float(r["realized"] or 0.0),
                               "last_ts": float(r["last_ts"] or 0.0),
                               "legs": int(r["legs"] or 0),
                               "unknown": int(r["unknown_legs"] or 0)}
            for r in rows}


async def db_all_slugs(pool: Any, *, page: int = 500, max_pages: int = 60,
                       timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, dict]:
    """THE WHOLE LEDGER, paged by slug, or an exception.

    Every page is bounded and every page carries its own cursor, so a
    ledger larger than one page is read completely rather than
    truncated. Running out of pages RAISES: a whole-ledger gate that
    silently saw part of the ledger is worse than no gate.
    """
    out: dict[str, dict] = {}
    after = ""
    for _ in range(max(1, int(max_pages))):
        got = await db_per_slug(pool, None, limit=page, after_slug=after,
                                timeout=timeout)
        if not got:
            return out
        out.update(got)
        after = max(got)
    raise ValueError(
        f"ledger has more than {max_pages * page} slugs: the whole-ledger "
        f"gate refuses to report on a truncated read")


async def db_outside_scope(pool: Any, slugs: Iterable[str], *,
                           timeout: float = DEFAULT_TIMEOUT_S) -> dict:
    """The money on the ledger slugs a bounded gate did NOT compare."""
    rows = await asyncio.wait_for(
        pool.fetch(OUTSIDE_SCOPE_SQL, [str(s) for s in slugs]), timeout)
    r = rows[0] if rows else {}
    return {"slugs": int(r.get("slugs") or 0),
            "legs": int(r.get("legs") or 0),
            "qty": round(float(r.get("qty") or 0.0), 2),
            "proceeds": round(float(r.get("proceeds") or 0.0), 2),
            "realized": round(float(r.get("realized") or 0.0), 2)}


#: Named in every failing gate report. A red gate is NOT proof the fold
#: is wrong, and the report says so before anyone acts on it.
NOT_THE_LEDGERS_FAULT: tuple[str, ...] = (
    "the sweep had not caught up when the gate ran (check "
    "sweep.complete_pass; gate_report refuses this outright when it is "
    "handed the sweep result)",
    "a trade arrived between the sweep and the served read -- the served "
    "record unions the venue's live window, this ledger reads only the "
    "archive (read sold_markets twice and require the two to agree)",
    "the served record came from a cache or a snapshot older than the "
    "rows just folded (track_record caches its payload)",
)


async def gate_report(pool: Any, sold_markets: Iterable[dict] | None, *,
                      sweep_result: dict | None = None,
                      archive_yardstick: dict[str, dict] | None = None,
                      timeout: float = DEFAULT_TIMEOUT_S) -> dict:
    """THE GATE against production: does this ledger reproduce the
    served `sold_markets`, to the cent?

    FAIL CLOSED, in this order, before any comparison is attempted:

      * a sweep result that did not complete a pass -> refused. Gating
        an incomplete ledger produces a red gate whose cause is the
        sweep, and the operator is then told the ledger is wrong;
      * an EMPTY or absent `sold_markets` -> refused, by name. `[]`
        would otherwise compare nothing and report ok, and `[]` is
        exactly what `/api/track-record` serves on a box with no venue
        credentials ({'configured': False}) or during a venue outage
        ({'configured': True, 'error': ...}).

    Then it reports its own coverage rather than implying it has none:
    `scope.outside` is the ledger the served list never named, with its
    money. Hand it `archive_yardstick` (from `sweep(yardstick=True)`)
    and it additionally gates the WHOLE ledger, all slugs, no [:30] --
    the only form in which `ledger_only` can be non-empty.
    """
    out: dict[str, Any] = {"before_blaming_the_ledger":
                           list(NOT_THE_LEDGERS_FAULT)}
    if sweep_result is not None and not sweep_result.get("complete_pass"):
        out.update(ok=False, reason="sweep_incomplete",
                   detail=f"the sweep reported coverage "
                          f"{sweep_result.get('coverage')!r}: the ledger is "
                          f"not known to be complete, so a disagreement "
                          f"would say nothing about the fold",
                   slugs_checked=0, mismatches=[], mismatch_count=0,
                   ledger_only=[], ledger_only_count=0)
        return out

    yard = {str(m.get("slug")): {"qty": _amt(m.get("qty")),
                                 "proceeds": _amt(m.get("proceeds")),
                                 "realized": _amt(m.get("realized")),
                                 "last_ts": float(m.get("last_ts") or 0.0)}
            for m in (sold_markets or []) if isinstance(m, dict)
            and m.get("slug")}
    if not yard:
        out.update(ok=False, reason="empty_yardstick",
                   detail="no served sold_markets to compare against: "
                          "nothing was checked, so nothing is proved "
                          "(a track-record payload carrying 'configured': "
                          "false or an 'error' key has no sold_markets)",
                   slugs_checked=0, mismatches=[], mismatch_count=0,
                   ledger_only=[], ledger_only_count=0)
        return out

    mine = await db_per_slug(pool, list(yard), timeout=timeout)
    out.update(compare(mine, yard, slugs=list(yard)))
    out["unknown_legs"] = sum(v["unknown"] for v in mine.values())
    out["scope"] = {
        "yardstick_slugs": len(yard),
        "note": "the served sold_markets is the newest 30 markets "
                "(track_record.py:949); slugs outside it are NOT compared "
                "on this path, so ledger_only is empty by construction "
                "here",
        "outside": await db_outside_scope(pool, list(yard), timeout=timeout),
    }
    if archive_yardstick is not None:
        # THE WHOLE LEDGER, against the archive-wide fold: every slug,
        # no truncation, and `ledger_only` finally means something.
        whole = await db_all_slugs(pool, timeout=timeout)
        full = compare(whole, archive_yardstick)
        full["ledger_slugs"] = len(whole)
        full["yardstick_slugs"] = len(archive_yardstick)
        out["full_ledger"] = full
        out["ok"] = bool(out["ok"]) and bool(full["ok"])
    return out
