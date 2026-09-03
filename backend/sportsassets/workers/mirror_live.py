"""Position mirroring, phase P1: the LIVE reconciler (owner order
2026-09-02, "go for it, let's get this working"; the P1 panel synthesis,
section 2, with the addendum's amendments).

The shadow (workers/mirror_shadow) reads each mirrored whale's book and
logs the one order it WOULD place. This worker places it -- long-only,
as a maker, under every existing breaker -- and keeps one BOOK per
(whale, market) in mirror_books, with its orders in mirror_orders and
its POSITION as one standing live_orders row (lane 'mirror', status
'filled' from open) so every consumer reads the book as the copy
position it is. Nothing here restates a rule: the arithmetic is
analytics/mirror, the decisions are analytics/mirror_live_rules, the
ledger writes are live_executor's mirror primitives, the venue reads
are the shadow's paced helpers. This module READS, CALLS THEM, and
WRITES, in the order the spec's tick names:

  0  MODE      env PMUS_MIRROR off -> SAFE (cancel-only; the loop keeps
               running so a deploy that drops the flag cannot orphan a
               rest), 'exits' -> reductions and flattens only, 'on' ->
               full; the DB switch 'mirror_live' must read exactly True
               for increases (false / absent / unreadable / malformed
               is exits-only); the trading tables must exist
  G  GUARDS    the executor's global guards in maybe_execute's order
               (any trip: a cancel-only tick), then the INCREASE-ONLY
               guards (the loss breaker, the sleeve's room, the mirror's
               own day cap and loss stop): reductions and flattens go on
  R  READS     once per tick: ratios, ONE paced positions walk, the
               account's open orders, the edge gate, the protected ids
  O  ORDERS    every non-terminal mirror order first: a lost placement
               is adopted by fingerprint or booked from the trade log by
               ORDER, an open order is read and its delta booked, a
               terminal one is written, a stale or unwanted one is
               cancelled and read until terminal
  B  BOOKS     existing books, then new candidates (newest first, under
               the per-tick cap): his position from the exit worker's
               FRESH COMPLETE snapshot (addendum section 1: fills are
               the trigger and the price, never the position), step M
               (a closed or closing market: cancel, never increase; a
               market that could not be read: cancel and HOLD, named)
               BEFORE the plan, the plan from the book's FIXED ratio,
               the freeze on venue/ledger disagreement, the act
  X  ACT       keep, cancel/replace, place a post-only GTC rest, the
               bounded take, the two flattens (paired-out rests and is
               never marketed; vanished rests, then mirror_exit's
               sole/co-held rules), the episode close
  E  CENSUS    every refusal named, every counter present, one
               heartbeat 'mirror_live', the reaper-isolation instrument

Fail closed on every read: a fact that could not be read is the named
refusal, never a guess; an unreadable ledger, venue or switch is a
tick that at most CANCELS; a trip MID-TICK (wrong sign, overfill) makes
the rest of the tick cancel-only. The reviews that shaped this file are
the P1 panel synthesis (critics C14-C16), the addendum's second-critic
amendments (sections 7-11), the step-5/6a/rules reviews it carries and
the step-9 worker review (owner order 2026-09-02, "go for it, let's get
this working": thirteen findings, each pinned in the worker tests).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .. import copy_sports, edge_gate
from .. import live_executor as le
from ..analytics import mirror as mi
from ..analytics import mirror_live_rules as rules
from ..analytics.decompose import payout_of
# The side and intent strings and the flat tolerance are values; every
# CAP, WAIT and dataclass is read through the rules module AT CALL TIME
# (rules.MIRROR_..., rules.BookState): the rules module is reloaded by
# its own tests under lowered environments, and a class or cap bound
# here at import would be the stale one (an AdmissionFacts the reloaded
# admission() no longer recognises reads `facts_unreadable`).
from ..analytics.mirror_live_rules import BUY, FLAT_TOL_SHARES, ORDER_INTENT, SELL
from ..analytics.roster_rules import MEASURE_CLIP_USD
from ..config import settings
from ..db import get_pool, heartbeat
from ..venue_pace import pace
from . import mirror_shadow as ms
from . import whale_exits

log = logging.getLogger(__name__)

# The loop's own timers. Every cap and wait is imported above from the
# rules module (never restated); these two are the reconciler's clock.
POLL_S = 30.0
WAKE_MIN_GAP_S = 5.0
SERVICE = "mirror_live"
# A 'placing' row with no order id older than this is a placement whose
# response was lost with the process (step O); younger, the placement
# may still be the one in flight under this very tick's lock.
PLACING_ORPHAN_S = 60.0
# The cancel/read discipline of the rest lane (_rest_cycle): two cancel
# attempts, then up to three reads a short gap apart until terminal.
CANCEL_ATTEMPTS = 2
CANCEL_READS = 3
CANCEL_READ_GAP_S = 0.3
# The book cross-check tolerance on settlement (spec 1e).
SETTLE_DISAGREE_USD = 0.05
# The shadow-vs-live instrument compares readings this close in time.
SHADOW_AGREE_WINDOW_S = 60.0

_STATE_LIVE = "mirror_live"
_STATE_WHALES = "mirror_live_whales"
_STATE_DEMOTED = "mirror_live_demoted"
_STATE_LOSS_STOP = "mirror_loss_stop"
_STATE_FLATTEN = "mirror_flatten"
_STATE_SIDE_ECHO = "side_echo_last"
MODE_SAFE, MODE_EXITS, MODE_ON = "safe", "exits", "on"
_OFF_VALUES = frozenset({"off", "0", "false", "no"})

# Every census name the tick can emit (spec section 5, plus the names
# the rules module and the addendum introduced). The heartbeat carries
# every one at 0 so a reader can tell "never happened" from "not
# counted"; the prefixed families (mapping:<why>, edge_gate:<why>,
# cell_gate_<clause>, place_refused:<status>) are counted under their
# family name here and by full name in the bounded _mirror_stop dict.
CENSUS_KEYS: tuple[str, ...] = (
    "mode_env_off", "mode_db_off", "mode_db_unreadable", "whales_unreadable",
    "tables_absent", "no_venue", "probe_disabled", "halted", "paused",
    "overspend_halt", "loss_breaker", "loss_breaker_unreadable", "no_budget_room",
    "mirror_day_cap", "mirror_loss_stop", "positions_unreadable",
    "open_orders_unreadable", "protected_ids_unreadable", "tick_abandoned",
    "no_ratio", "no_mark", "no_quote", "unmapped", "family", "per_side_unsupported",
    "market_closed", "market_unreadable", "game_too_far_out", "mapping", "edge_gate", "cell_gate",
    "clip_zero", "legacy_row", "slug_recent_copy", "underdog_coholds",
    "venue_already_holds", "kalshi_claimed", "side_band", "snapshot_stale", "drift",
    "max_books", "first_fill_gate", "asset_claimed", "book_exists",
    "short_side_refused", "on_target", "under_one_share", "dead_band", "hysteresis",
    "no_price", "venue_ledger_disagree", "wrong_sign_trip", "order_state_unknown",
    "placement_lost", "lost_ambiguous", "order_lost", "cancel_pending",
    "replace_capped", "ops_capped", "over_room", "open_order_pending", "rest_placed",
    "take_placed", "post_only_rejected", "post_only_ignored", "place_refused",
    "filled_rest", "filled_take", "partial_fill", "cancelled_unfilled", "expired",
    "resting_above_level", "reduce_unfilled", "flatten_rested", "flatten_vanished",
    "vanish_unconfirmed", "no_bid_for_flatten", "overfill", "closed_cashed_out",
    "closed_cancelled", "book_settle_disagree", "shadow_live_disagree",
    "reaper_touched_mirror", "demoted", "mirror_flatten", "row_not_live",
    "write_failed", "rate_limited", "book_error",
)
_FAMILIES = (("mapping:", "mapping"), ("edge_gate:", "edge_gate"),
             ("cell_gate_", "cell_gate"), ("place_refused:", "place_refused"))

# ------------------------------------------------------------ wake + census

# The hand-off's wake (spec 3.1): maybe_execute's gate calls notify() for
# a mirrored whale's fill, in this same process, and the loop reads the
# woken markets first. A courtesy, never a condition -- the loop polls.
_WAKE = asyncio.Event()
_WOKEN: set[str] = set()
_WOKEN_MAX = 200
_TICK_LOCK = asyncio.Lock()
_BOOK_LOCKS: dict[int, asyncio.Lock] = {}
_backoff_until = 0.0
_last_tick_at = 0.0
_unmapped_until: dict[tuple[str, str], float] = {}
# The venue IGNORED the post-only flag once (executions on a post-only
# create): the flag is off for the rest of the process and the maker
# thesis is measured by price selection alone (spec X.L).
_POST_ONLY_OK = True
# The _copy_stop shape (live_executor:386-398), bounded the same way and
# for the same reason: the key space includes the whale.
_MIRROR_CENSUS: dict[str, int] = {}
_MIRROR_CENSUS_MAX = 400
_RECENT: deque = deque(maxlen=40)
_current_stats: dict | None = None
_sleep = asyncio.sleep          # indirection so a test can skip the cancel-read gap


def notify(condition_id: str | None = None) -> None:
    """Wake the loop for one market. Tolerant of None and of a blank
    id (a fill with no condition still wakes the poll); never raises,
    because the caller is the money decision on a mirrored whale's fill
    and a lost wake is a late tick, never a copy."""
    try:
        cid = str(condition_id or "").strip()
        if cid and len(_WOKEN) < _WOKEN_MAX:
            _WOKEN.add(cid)
        _WAKE.set()
    except Exception:  # noqa: BLE001 — a wake must not become the caller's exception
        log.debug("mirror_live: wake for %r dropped", condition_id, exc_info=True)


def _family(reason: str) -> str:
    for prefix, fam in _FAMILIES:
        if reason.startswith(prefix):
            return fam
    return reason


def _mirror_stop(reason: str, whale: str | None = None) -> None:
    """Count one named refusal or event: the bounded per-process dict
    (the _copy_stop shape) and the running tick's census. Returns None
    so `return _mirror_stop(...)` reads like the executor's."""
    w = (whale or "?").lower()[:40]
    key = f"{reason}|{w}"
    if key not in _MIRROR_CENSUS and len(_MIRROR_CENSUS) >= _MIRROR_CENSUS_MAX:
        key = f"{reason}|(overflow)"
    _MIRROR_CENSUS[key] = _MIRROR_CENSUS.get(key, 0) + 1
    if _current_stats is not None:
        c = _current_stats["census"]
        fam = _family(reason)
        c[fam] = c.get(fam, 0) + 1
    return None


def mirror_census_snapshot() -> dict:
    return dict(sorted(_MIRROR_CENSUS.items(), key=lambda kv: -kv[1]))


def _recent(book_id: int | None, what: str, **detail) -> None:
    _RECENT.append({"at": round(time.time(), 1), "book": book_id, "what": what,
                    **{k: v for k, v in detail.items() if v is not None}})


def _lock_for(book_id: int) -> asyncio.Lock:
    lk = _BOOK_LOCKS.get(book_id)
    if lk is None:
        if len(_BOOK_LOCKS) > 200:      # closed books' locks are garbage
            _BOOK_LOCKS.clear()
        lk = _BOOK_LOCKS[book_id] = asyncio.Lock()
    return lk


# ----------------------------------------------------------------- readings

def _num(v: Any) -> float | None:
    return rules._num(v)


def _rowcount(status) -> int:
    return ms._rowcount(status)


def _jsonish(v: Any) -> Any:
    if isinstance(v, (dict, list)) or v is None:
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _post_only_enabled() -> bool:
    return (_POST_ONLY_OK and os.environ.get("PMUS_MIRROR_POST_ONLY", "on")
            .strip().lower() not in _OFF_VALUES)


def _gtd_enabled() -> bool:
    return os.environ.get("PMUS_MIRROR_GTD", "off").strip().lower() in ("on", "1", "true", "yes")


def _iso(ts: float) -> str:
    return (datetime.fromtimestamp(ts, tz=timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"))


async def _state(pool, key: str) -> tuple[Any, str | None]:
    """(value, error): one ingestion_state key by the roster_auto
    _read_state idiom; an exception is the named error and the value
    None, so every caller decides its own fail-closed reading."""
    try:
        raw = await pool.fetchval("SELECT value FROM ingestion_state WHERE key=$1", key)
    except Exception as exc:  # noqa: BLE001 — unreadable is named, never guessed
        return None, type(exc).__name__
    if raw is None:
        return None, None
    if isinstance(raw, str):
        try:
            return json.loads(raw), None
        except ValueError:
            return None, "malformed"
    return raw, None


async def _write_state(pool, key: str, value: Any) -> None:
    await pool.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb /* ml-state-write */",
        key, json.dumps(value, default=str))


def _paced(fn, *args):
    """One venue READ behind the process-wide measurement pacer, run in
    a worker thread by the caller (the shadow's _paced_bbo shape)."""
    pace(ms.READ_PACING_S)
    return fn(*args)


# ------------------------------------------------------------------- SQL

_SQL_TABLE_GUARD = "SELECT 1 FROM mirror_books LIMIT 0 /* ml-table-guard */"
_SQL_ORDERS_OPEN = """
SELECT o.id, o.book_id, o.whale, o.us_market_slug, o.kind, o.side, o.tif, o.post_only,
       o.his_level::float8 AS his_level, o.price::float8 AS price, o.wire::float8 AS wire,
       o.qty, o.order_id, o.state, o.venue_state, o.filled::float8 AS filled,
       o.booked_filled::float8 AS booked_filled, o.avg_px::float8 AS avg_px,
       o.taker_at_placement, o.pre_ids, o.reason,
       extract(epoch FROM o.placed_at)::float8 AS placed_ts
  FROM mirror_orders o
 WHERE o.state IN ('placing', 'open', 'unknown')
 ORDER BY o.placed_at, o.id /* ml-orders-open */
"""
_SQL_BOOK_COLS = """
SELECT b.id, b.whale, b.condition_id, b.us_market_slug, b.game_key, b.long_asset,
       b.other_asset, b.intent, b.map_source, b.ratio::float8 AS ratio,
       b.anchor_usd::float8 AS anchor_usd, b.standing_row_id, b.episode, b.flat_reopens,
       b.state, b.frozen_reason, extract(epoch FROM b.frozen_at)::float8 AS frozen_ts,
       b.frozen_ticks, b.target, b.ledger_net, b.venue_net::float8 AS venue_net,
       b.open_order_id, extract(epoch FROM b.take_armed_at)::float8 AS take_armed_ts,
       b.last_reason, b.last_plan, b.gross_buy_usd::float8 AS gross_buy_usd,
       b.gross_sell_usd::float8 AS gross_sell_usd,
       b.peak_exposure_usd::float8 AS peak_exposure_usd, b.avg_cost::float8 AS avg_cost,
       b.realized_pnl::float8 AS realized_pnl, b.settled_pnl::float8 AS settled_pnl,
       extract(epoch FROM b.opened_at)::float8 AS opened_ts,
       extract(epoch FROM b.updated_at)::float8 AS updated_ts
  FROM mirror_books b
"""
_SQL_BOOKS_OPEN = _SQL_BOOK_COLS + " WHERE b.state <> 'closed' ORDER BY b.updated_at, b.id /* ml-books-open */"
_SQL_BOOK_READ = _SQL_BOOK_COLS + " WHERE b.id = $1 /* ml-book-read */"
_SQL_BOOKS_COUNT = """
SELECT count(*) FILTER (WHERE state <> 'closed') AS live,
       count(*) FILTER (WHERE opened_at > now() - interval '24 hours') AS today
  FROM mirror_books /* ml-books-count */
"""
_SQL_MIRROR_DAY = """
SELECT COALESCE(sum(cash_usd), 0)::float8 FROM mirror_orders
 WHERE side = 'BUY_LONG' AND placed_at > now() - interval '24 hours' /* ml-mirror-day */
"""
_SQL_LOSS_SUM = """
SELECT COALESCE((SELECT sum(realized_pnl) FROM mirror_books
                  WHERE updated_at > now() - interval '24 hours'), 0)::float8
     + COALESCE((SELECT sum(settled_pnl) FROM mirror_books
                  WHERE state = 'closed' AND settled_pnl IS NOT NULL
                    AND closed_at > now() - interval '24 hours'), 0)::float8
       AS lost,
       (SELECT count(*) FROM mirror_books
         WHERE updated_at > now() - interval '24 hours') AS books /* ml-loss-sum */
"""
_SQL_REPLACES = """
SELECT count(*) FROM mirror_orders
 WHERE book_id = $1 AND reason = 'replace' AND done_at > now() - interval '1 hour' /* ml-replaces */
"""
# The flatten rest that starts the slippage clock is the NEWEST one of
# the CURRENT vanish: still standing, or placed at or after the tick
# the book entered the vanish (the plan's vanish_since). A min() over
# every flatten rest the book ever placed let a rest cancelled two
# hours ago -- he came back, the book went on -- send a fresh vanish
# straight to close_position with no rest first (step-9 review).
_SQL_FLATTEN_REST_SINCE = """
SELECT max(extract(epoch FROM placed_at))::float8 FROM mirror_orders
 WHERE book_id = $1 AND kind = 'flatten_vanished' AND tif IN ('GTC', 'GTD')
   AND (state IN ('placing', 'open', 'unknown')
        OR placed_at >= to_timestamp($2)) /* ml-flatten-since */
"""
_SQL_ORDER_INSERT = """
INSERT INTO mirror_orders (book_id, whale, us_market_slug, kind, side, tif, post_only,
                           good_till, his_level, price, wire, qty, state, pre_ids,
                           target_at_place, ledger_at_place, bid_at_place, ask_at_place,
                           reason)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'placing', $13::jsonb,
        $14, $15, $16, $17, $18)
RETURNING id /* ml-order-insert */
"""
_SQL_ORDER_PERSIST_ID = """
UPDATE mirror_orders SET order_id = $2, state = 'open', venue_state = $3,
       receipt = $4::jsonb, updated_at = now()
 WHERE id = $1 /* ml-order-persist */
"""
_SQL_ORDER_ADOPT = """
UPDATE mirror_orders SET order_id = $2, state = 'open', reason = $3, updated_at = now()
 WHERE id = $1 AND order_id IS NULL /* ml-order-adopt */
"""
_SQL_ORDER_STATE = """
UPDATE mirror_orders SET state = $2, venue_state = $3, reason = $4, maker = $5,
       done_at = CASE WHEN $2 IN ('filled', 'cancelled', 'expired', 'rejected', 'lost')
                      THEN now() ELSE done_at END,
       order_id = COALESCE($6, order_id), updated_at = now()
 WHERE id = $1 /* ml-order-state */
"""
_SQL_ORDER_CURSOR = """
UPDATE mirror_orders SET booked_filled = booked_filled + $2, filled = $3, avg_px = $4,
       updated_at = now()
 WHERE id = $1 AND booked_filled = $5 /* ml-order-cursor */
"""
_SQL_ORDER_CASH = """
UPDATE mirror_orders SET cash_usd = cash_usd + $2, realized = realized + $3,
       taker_at_placement = taker_at_placement OR $4, updated_at = now()
 WHERE id = $1 /* ml-order-cash */
"""
_SQL_ORDER_REASON = "UPDATE mirror_orders SET reason = $2, updated_at = now() WHERE id = $1 /* ml-order-reason */"
_SQL_ADDS_SEQ = """
SELECT count(*) FROM live_orders lo,
       jsonb_array_elements(COALESCE(lo.raw->'adds', '[]'::jsonb)) a
 WHERE lo.id = $1 AND a->>'order_id' = $2 /* ml-adds-seq */
"""
_SQL_STANDING_READ = """
SELECT status, lane, filled_shares::float8 AS filled_shares, fill_price::float8 AS fill_price,
       pnl::float8 AS pnl, raw FROM live_orders WHERE id = $1 /* ml-standing-read */
"""
_SQL_STANDING_NAME = """
UPDATE live_orders SET raw = jsonb_set(COALESCE(raw, '{}'::jsonb), '{mirror,named}', 'true'::jsonb)
 WHERE id = $1 AND lane = 'mirror' AND status = 'filled' /* ml-standing-name */
"""
_SQL_LEDGER_IDS = """
SELECT order_id FROM live_orders WHERE us_market_slug = $1 AND order_id IS NOT NULL /* ml-ledger-ids */
"""
_SQL_MANUAL_SHARES = """
SELECT COALESCE(sum(filled_shares), 0)::float8 FROM live_orders
 WHERE us_market_slug = $1 AND COALESCE(whale_username, '') = 'manual'
   AND status IN ('filled', 'exiting') /* ml-manual-shares */
"""
# A LIVE per-fill row is a claim whatever its age (spec A: it keeps its
# own exit path until it is cashed_out or settled); only the NAMED
# error rows age out of the 48 h window. The window once wrapped both
# clauses, so a three-day-old live position on the same game or the
# other outcome no longer refused admission (step-9 review).
_SQL_LEGACY_ROW = """
SELECT EXISTS (
  SELECT 1 FROM live_orders
   WHERE COALESCE(lane, '') <> 'mirror'
     AND (asset = $1 OR us_market_slug = $2 OR ($3::text IS NOT NULL AND us_market_slug LIKE $4))
     AND (status IN ('filled', 'submitting', 'exiting')
          OR (status = 'error' AND (error LIKE 'venue holds a POSITION%'
                                    OR error LIKE 'ORPHAN FILL RECORDED%'
                                    OR error LIKE 'venue has no record of order%')
              AND placed_at > now() - interval '48 hours'))) /* ml-legacy-row */
"""
_SQL_SLUG_RECENT = """
SELECT EXISTS (
  SELECT 1 FROM live_orders
   WHERE us_market_slug = $1 AND COALESCE(lane, '') <> 'mirror'
     AND status NOT IN ('rejected', 'unfilled')
     AND placed_at > now() - interval '60 minutes') /* ml-slug-recent */
"""
_SQL_UNDERDOG = """
SELECT EXISTS (
  SELECT 1 FROM live_orders
   WHERE COALESCE(whale_username, '') = 'underdog' AND asset = ANY($1::text[])
     AND status IN ('filled', 'submitting', 'exiting')) /* ml-underdog */
"""
_SQL_KALSHI = "SELECT 1 FROM kalshi_claims WHERE asset = $1 LIMIT 1 /* ml-kalshi */"
_SQL_MARKET = "SELECT closed, resolved, resolved_prices FROM markets WHERE condition_id = $1 /* ml-market */"
_SQL_TOKEN_INDEX = "SELECT outcome_index FROM market_tokens WHERE token_id = $1 /* ml-token-index */"
_SQL_SIBLING_TOKEN = """
SELECT token_id FROM market_tokens WHERE condition_id = $1 AND token_id <> $2
 ORDER BY outcome_index LIMIT 1 /* ml-sibling-token */
"""
_SQL_WHALE_ADDRESS = "SELECT address FROM whales WHERE lower(username) = $1 LIMIT 1 /* ml-whale-address */"
_SQL_SHADOW_LATEST = """
SELECT target, ratio::float8 AS ratio, his_net::float8 AS his_net,
       extract(epoch FROM at)::float8 AS at_ts
  FROM mirror_shadow WHERE whale = $1 AND condition_id = $2
 ORDER BY at DESC LIMIT 1 /* ml-shadow-latest */
"""
_SQL_REAPER_TOUCHED = """
SELECT count(*) FROM live_orders lo JOIN mirror_orders mo ON mo.order_id = lo.order_id
 WHERE COALESCE(lo.lane, '') <> 'mirror' /* ml-reaper-touched */
"""
_SQL_BOOK_PLAN = """
UPDATE mirror_books SET target = $2, target_raw = $3, his_net = $4, his_long = $5,
       his_other = $6, snap_long = $7, snap_other = $8, drift = $9, his_level = $10,
       venue_net = $11, last_reason = $12, last_plan = $13::jsonb, updated_at = now()
 WHERE id = $1 /* ml-book-plan */
"""
# THE FIRST REASON STICKS: a book frozen 'overfill' whose venue then
# disagrees with its emptied ledger is still the overfill, not the
# disagreement; the later name rides in last_reason and the census.
_SQL_BOOK_FREEZE = """
UPDATE mirror_books SET frozen_reason = CASE WHEN state = 'frozen' THEN frozen_reason ELSE $2 END,
       state = 'frozen', frozen_at = COALESCE(frozen_at, now()),
       frozen_ticks = frozen_ticks + 1, last_reason = $2, updated_at = now()
 WHERE id = $1 AND state IN ('live', 'frozen') /* ml-book-freeze */
"""
_SQL_BOOK_THAW = """
UPDATE mirror_books SET state = 'live', frozen_reason = NULL, frozen_at = NULL,
       updated_at = now()
 WHERE id = $1 AND state = 'frozen' /* ml-book-thaw */
"""
_SQL_BOOK_STATE = """
UPDATE mirror_books SET state = $2, last_reason = $3,
       closed_at = CASE WHEN $2 = 'closed' THEN now() ELSE closed_at END, updated_at = now()
 WHERE id = $1 /* ml-book-state */
"""
_SQL_BOOK_OPEN_ORDER = "UPDATE mirror_books SET open_order_id = $2, updated_at = now() WHERE id = $1 /* ml-book-open-order */"
_SQL_BOOK_ARM = """
UPDATE mirror_books SET take_armed_at = CASE WHEN $2 THEN now() ELSE NULL END, updated_at = now()
 WHERE id = $1 /* ml-book-arm */
"""
_SQL_BOOK_LEDGER_BUY = """
UPDATE mirror_books SET ledger_net = $2, avg_cost = $3, gross_buy_usd = $4,
       peak_exposure_usd = $5, updated_at = now()
 WHERE id = $1 /* ml-book-ledger-buy */
"""
_SQL_BOOK_LEDGER_SELL = """
UPDATE mirror_books SET ledger_net = $2, gross_sell_usd = $3, realized_pnl = $4,
       updated_at = now()
 WHERE id = $1 /* ml-book-ledger-sell */
"""
_SQL_BOOK_SETTLED = """
UPDATE mirror_books SET settled_pnl = $2, own_book_pnl = $3, settle_disagree = $4,
       state = 'closed', last_reason = $5, closed_at = now(), updated_at = now()
 WHERE id = $1 /* ml-book-settled */
"""
_SQL_BOOK_REOPENS = "UPDATE mirror_books SET flat_reopens = $2 WHERE id = $1 /* ml-book-reopens */"


# --------------------------------------------------------------- the tick

@dataclass
class _Tick:
    pool: Any
    pmus: Any
    http: Any
    now: float
    stats: dict
    mode: str = MODE_SAFE
    mode_db_refusal: str = "mode_db_off"
    cancel_all: str | None = None          # every open order is cancelled under this name
    increase_block: str | None = None      # a global increase-only refusal
    allow: set = field(default_factory=set)
    narrow: set | None = None
    narrow_unreadable: bool = False
    demoted: set = field(default_factory=set)
    demoted_unreadable: bool = False
    flatten_all: bool = False
    positions: dict | None = None
    open: list | None = None
    open_error: str | None = None
    open_tried: bool = False
    protected: set | None = None
    protected_tried: bool = False
    ratios: dict = field(default_factory=dict)
    day_room: float | None = None
    total_room: float | None = None
    mirror_day: float | None = None
    ops: int = 0
    reads: int = 0
    misses: int = 0
    abandoned: bool = False
    snaps: dict = field(default_factory=dict)
    open_by_book: dict = field(default_factory=dict)   # book_id -> (order row, status)
    nonterminal: set = field(default_factory=set)      # book ids with a non-terminal order
    books_seen: set = field(default_factory=set)       # (whale, condition_id)


def _new_stats() -> dict:
    return {"status": "ok", "mode": MODE_SAFE, "whales": [], "books_live": 0,
            "books_frozen": 0, "orders_open": 0, "placed_rest": 0, "placed_take": 0,
            "filled_rest": 0, "filled_take": 0, "partial_fills": 0, "requotes": 0,
            "cancelled": 0, "flattened": 0, "closed_books": 0, "frozen_reasons": {},
            "census": {k: 0 for k in CENSUS_KEYS}, "recent": [], "abandoned": False,
            "skipped_backoff": False, "ops": 0, "reads": 0, "woken": [],
            "reaper_touched_mirror": 0, "post_only": _POST_ONLY_OK}


def _increases_refusal(t: _Tick, whale: str) -> str | None:
    """Why this whale may not INCREASE this tick, or None. Allowlist,
    DB narrowing and demotion gate increases ONLY (addendum section 7):
    a whale removed from every list still has his books reduced and
    flattened by the steps below."""
    w = (whale or "").lower()
    if t.cancel_all:
        # a cancel-only tick -- a global guard, or a trip MID-TICK
        # (wrong sign, overfill) -- refuses every increase by the
        # trip's own name; the DB switch it wrote false is read on
        # the next tick (step-9 review: a candidate opened a book and
        # rested a BUY in the very tick that tripped live off)
        return t.cancel_all
    if t.mode == MODE_SAFE:
        return "mode_env_off"
    if t.mode == MODE_EXITS:
        return t.mode_db_refusal
    if t.increase_block:
        return t.increase_block
    if w not in t.allow:
        return "mode_env_off"
    if t.narrow_unreadable or t.demoted_unreadable:
        return "whales_unreadable"
    if t.narrow is not None and w not in t.narrow:
        return "mode_db_off"
    if w in t.demoted:
        return "demoted"
    return None


async def _read_mode(t: _Tick) -> None:
    """Step 0: the mode ladder. Every DB read fails closed to exits-only
    or to 'nobody increases'; the environment alone decides SAFE."""
    env = os.environ.get("PMUS_MIRROR", "off").strip().lower()
    if env not in (MODE_EXITS, MODE_ON):
        t.mode = MODE_SAFE
        t.stats["mode"] = MODE_SAFE
        _mirror_stop("mode_env_off")
        return
    t.allow = set(le.mirror_allowlist())
    t.stats["whales"] = sorted(t.allow)
    live, err = await _state(t.pool, _STATE_LIVE)
    if env == MODE_EXITS:
        t.mode, t.mode_db_refusal = MODE_EXITS, "mode_env_off"
        _mirror_stop("mode_env_off")
    elif err is not None:
        t.mode, t.mode_db_refusal = MODE_EXITS, "mode_db_unreadable"
        _mirror_stop("mode_db_unreadable")
    elif live is not True:
        t.mode, t.mode_db_refusal = MODE_EXITS, "mode_db_off"
        _mirror_stop("mode_db_off")
    else:
        t.mode = MODE_ON
    t.stats["mode"] = t.mode
    if t.mode != MODE_ON:
        return
    narrow, err = await _state(t.pool, _STATE_WHALES)
    if err is not None:
        t.narrow_unreadable = True
        _mirror_stop("whales_unreadable")
    elif narrow is not None:
        if isinstance(narrow, list):
            t.narrow = {str(w).strip().lower() for w in narrow}
        else:
            t.narrow_unreadable = True          # malformed narrows to nobody
            _mirror_stop("whales_unreadable")
    demoted, err = await _state(t.pool, _STATE_DEMOTED)
    if err is not None:
        t.demoted_unreadable = True
        _mirror_stop("whales_unreadable")
    elif isinstance(demoted, list):
        t.demoted = {str(w).strip().lower() for w in demoted}
    elif demoted is not None:
        t.demoted_unreadable = True
        _mirror_stop("whales_unreadable")


async def _global_guards(t: _Tick) -> None:
    """Step G, in maybe_execute's order. A tripped global guard makes
    the tick cancel-only (t.cancel_all); a tripped increase-only guard
    stops increases and lets reductions and flattens run."""
    if not settings().copy_probe_enabled:
        t.cancel_all = "probe_disabled"
    elif le.copy_halted():
        t.cancel_all = "halted"
    elif await le._is_paused(t.pool):
        t.cancel_all = "paused"
    elif await le.overspend_halt(t.pool):
        t.cancel_all = "overspend_halt"
    if t.cancel_all:
        _mirror_stop(t.cancel_all)
        return
    # THE ADMIN FLATTEN IS A REDUCTION LEVER, read in every mode that
    # can sell: it once sat below the mode early-return, so with the DB
    # switch false, absent or unreadable -- exactly the state every
    # trip leaves behind -- or PMUS_MIRROR=exits, the lever was inert
    # (step-9 review). SAFE never reaches here and still only cancels.
    flat, err = await _state(t.pool, _STATE_FLATTEN)
    if err is None and flat is True:
        t.flatten_all = True
        _mirror_stop("mirror_flatten")
    if t.mode != MODE_ON:
        return
    lb = await le._loss_breaker_tripped(t.pool)
    if lb is None:
        t.increase_block = "loss_breaker_unreadable"
    elif lb:
        t.increase_block = "loss_breaker"
    if t.increase_block is None:
        try:
            # the rest lane's in-flight reservations come off the room
            # first (addendum section 7: concurrent placements across
            # lanes must not exceed the day cap by one clip each)
            async with le._REST_LOCK:
                day, total = await le._copy_day_room(t.pool, settings())
                day -= float(le._REST_RESERVED_USD or 0.0)
            # the sleeve's day and lifetime knobs default to no ceiling
            # (an infinite room); room_scale reads a number, so an
            # unbounded room is written as the largest bound there is
            t.day_room = float(day) if math.isfinite(day) else 1e12
            t.total_room = float(total) if math.isfinite(total) else 1e12
        except Exception as exc:  # noqa: BLE001 — an unreadable ledger is no room
            log.warning("mirror_live: sleeve room unreadable (%s)", type(exc).__name__)
            t.increase_block = "no_budget_room"
        else:
            if not (t.day_room > 0 and t.total_room > 1):
                t.increase_block = "no_budget_room"
    if t.increase_block is None:
        try:
            spent = float(await t.pool.fetchval(_SQL_MIRROR_DAY) or 0.0)
            t.mirror_day = float(rules.MIRROR_DAY_USD) - spent
        except Exception as exc:  # noqa: BLE001
            log.warning("mirror_live: mirror day spend unreadable (%s)", type(exc).__name__)
            t.increase_block = "mirror_day_cap"
        else:
            if t.mirror_day <= 0:
                t.increase_block = "mirror_day_cap"
    if t.increase_block is None:
        stop, err = await _state(t.pool, _STATE_LOSS_STOP)
        if err is not None or stop is not None:
            t.increase_block = "mirror_loss_stop"
        else:
            try:
                row = await t.pool.fetchrow(_SQL_LOSS_SUM)
                lost = float((row or {})["lost"] or 0.0) if row else 0.0
                books = int((row or {})["books"] or 0) if row else 0
            except Exception as exc:  # noqa: BLE001 — a stop that cannot be read is a stop
                log.warning("mirror_live: loss stop unreadable (%s)", type(exc).__name__)
                t.increase_block = "mirror_loss_stop"
            else:
                if lost <= -float(rules.MIRROR_LOSS_STOP_USD):
                    try:
                        await _write_state(t.pool, _STATE_LOSS_STOP,
                                           {"at": _iso(t.now), "sum": round(lost, 4),
                                            "books": books,
                                            "limit": float(rules.MIRROR_LOSS_STOP_USD)})
                    except Exception:  # noqa: BLE001 — the tick still refuses
                        log.warning("mirror_live: loss stop receipt not written", exc_info=True)
                    t.increase_block = "mirror_loss_stop"
    if t.increase_block:
        _mirror_stop(t.increase_block)


async def _read_open(t: _Tick) -> list | None:
    """The account's open orders, ONE call per tick, read on first
    need. None when the venue could not be listed ('open_orders_unreadable'):
    a lane that cannot see the book does not place on it."""
    if t.open_tried:
        return t.open
    t.open_tried = True
    try:
        t.open = list(await asyncio.to_thread(_paced, t.pmus.open_orders) or [])
    except Exception as exc:  # noqa: BLE001
        t.open_error = type(exc).__name__
        _mirror_stop("open_orders_unreadable")
        log.warning("mirror_live: open orders unreadable (%s)", t.open_error)
        t.open = None
    return t.open


async def _read_protected(t: _Tick) -> set | None:
    if t.protected_tried:
        return t.protected
    t.protected_tried = True
    t.protected = await le._protected_order_ids(t.pool)
    if t.protected is None:
        _mirror_stop("protected_ids_unreadable")
    return t.protected


async def _snapshot(t: _Tick, whale: str) -> tuple[dict, float | None, bool]:
    if whale not in t.snaps:
        t.snaps[whale] = await ms.snapshot_sizes(t.pool, whale)
    return t.snaps[whale]


async def _bbo(t: _Tick, slug: str) -> tuple[float | None, float | None]:
    try:
        bid, ask = await asyncio.to_thread(ms._paced_bbo, t.pmus, slug)
    except Exception as exc:  # noqa: BLE001 — an unreadable book is no quote
        log.warning("mirror_live: BBO for %s unreadable (%s)", slug, type(exc).__name__)
        bid = ask = None
    t.reads += 1
    if bid is None and ask is None:
        t.misses += 1
        _mirror_stop("no_quote")
        if t.misses >= ms.MISS_STREAK_ABANDON:
            _abandon(t, "no_quote")
    else:
        t.misses = 0
    return bid, ask


def _mark_of(bid, ask) -> float | None:
    b, a = _num(bid), _num(ask)
    if b is not None and a is not None and 0.0 < b < 1.0 and 0.0 < a < 1.0:
        return round((b + a) / 2.0, 4)
    if a is not None and 0.0 < a < 1.0:
        return a
    return None


def _abandon(t: _Tick, why: str) -> None:
    global _backoff_until
    if not t.abandoned:
        _backoff_until = t.now + ms.BACKOFF_S
        t.abandoned = True
        t.stats.update(abandoned=True, status="degraded", abandon_reason=why)
        _mirror_stop("tick_abandoned")
        log.warning("mirror_live: tick abandoned (%s), backing off %ss", why, ms.BACKOFF_S)


async def _market(t: _Tick, cid: str) -> dict | None:
    """{closed, resolved, resolved_prices} or None when unreadable."""
    try:
        row = await t.pool.fetchrow(_SQL_MARKET, cid)
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    return {"closed": row["closed"], "resolved": row["resolved"],
            "resolved_prices": row["resolved_prices"]}


# ------------------------------------------------------------ the ledger

class _Rebook(Exception):
    """Someone advanced the cursor first: roll back and re-read."""


class _RowNotLive(Exception):
    pass


class _Refused(Exception):
    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


def _book_state(book: dict) -> rules.BookState:
    return rules.BookState(ledger_net=float(book.get("ledger_net") or 0.0),
                     avg_cost=book.get("avg_cost"),
                     gross_buy_usd=float(book.get("gross_buy_usd") or 0.0),
                     gross_sell_usd=float(book.get("gross_sell_usd") or 0.0),
                     peak_exposure_usd=float(book.get("peak_exposure_usd") or 0.0),
                     realized_pnl=float(book.get("realized_pnl") or 0.0))


async def _book_fill(t: _Tick, o: dict, book: dict, inc: float, px: float | None,
                     maker: bool, taker_at_placement: bool = False) -> str:
    """Book one fill delta: ONE transaction -- the mirror_orders cursor
    (WHERE booked_filled = expected; 0 rows means someone booked it:
    roll back, re-read), the standing-row statement, the book's ledger
    columns. A crash between the venue read and this write re-books
    exactly once on the next tick. Returns 'booked' | 'rebooked' |
    'duplicate' | 'row_not_live' | 'overfill' | 'refused:<why>'; a
    write failure PROPAGATES with the transaction rolled back (addendum
    section 8)."""
    expected = float(o.get("booked_filled") or 0.0)
    new_filled = expected + inc
    side = o["side"]
    sid = book["standing_row_id"]
    booking = None
    overfill = False
    try:
        async with t.pool.acquire() as conn:
            async with conn.transaction():
                tag = await conn.execute(_SQL_ORDER_CURSOR, o["id"], inc, new_filled, px,
                                         expected)
                if _rowcount(tag) == 0:
                    raise _Rebook()
                if side == BUY:
                    usd = float(le.fill_cash(inc, px, ORDER_INTENT))
                    seq = int(await conn.fetchval(_SQL_ADDS_SEQ, sid, str(o["order_id"])) or 0)
                    row = await le._book_mirror_buy(
                        conn, sid, str(o["order_id"]), seq, inc, px, usd,
                        inc * float(o.get("wire") or 0.0), o.get("his_level"), maker)
                    if row is None:
                        st = await conn.fetchrow(_SQL_STANDING_READ, sid)
                        if st is not None and st["status"] == "filled" and st["lane"] == "mirror":
                            # already on raw.adds: the cursor advance is
                            # kept, nothing else moves (addendum section 9)
                            await conn.execute(_SQL_ORDER_CASH, o["id"], 0.0, 0.0,
                                               bool(taker_at_placement))
                            return "duplicate"
                        raise _RowNotLive()
                    booking = rules.book_buy(_book_state(book), inc, px, usd)
                    if booking.refusal:
                        raise _Refused(booking.refusal)
                    ns = booking.state
                    await conn.execute(_SQL_BOOK_LEDGER_BUY, book["id"], int(round(ns.ledger_net)),
                                       ns.avg_cost, ns.gross_buy_usd, ns.peak_exposure_usd)
                    await conn.execute(_SQL_ORDER_CASH, o["id"], booking.usd, 0.0,
                                       bool(taker_at_placement))
                else:
                    res = await le._book_mirror_sell(conn, sid, inc, px,
                                                     float(book.get("ledger_net") or 0.0))
                    if res.get("refusal") == "row_not_live":
                        raise _RowNotLive()
                    overfill = bool(res.get("overfill"))
                    if res.get("refusal") in ("bad_fill", "no_entry_price"):
                        raise _Refused(str(res["refusal"]))
                    booking = rules.book_sell(_book_state(book), inc, px)
                    overfill = overfill or booking.overfill
                    if booking.refusal:
                        raise _Refused(booking.refusal)
                    ns = booking.state
                    await conn.execute(_SQL_BOOK_LEDGER_SELL, book["id"], int(round(ns.ledger_net)),
                                       ns.gross_sell_usd, ns.realized_pnl)
                    await conn.execute(_SQL_ORDER_CASH, o["id"], booking.usd,
                                       booking.realized or 0.0, bool(taker_at_placement))
    except _Rebook:
        return "rebooked"
    except _RowNotLive:
        await _freeze(t, book, "row_not_live")
        return "row_not_live"
    except _Refused as exc:
        log.error("mirror_live: fill on order %s refused by the ledger (%s); nothing booked",
                  o["id"], exc.why)
        return f"refused:{exc.why}"
    # committed: carry the new figures through the rest of the tick
    o["booked_filled"] = new_filled
    o["filled"] = new_filled
    o["avg_px"] = px
    if booking is not None:
        ns = booking.state
        book.update(ledger_net=int(round(ns.ledger_net)), avg_cost=ns.avg_cost,
                    gross_buy_usd=ns.gross_buy_usd, gross_sell_usd=ns.gross_sell_usd,
                    peak_exposure_usd=ns.peak_exposure_usd, realized_pnl=ns.realized_pnl)
    if 0.0 < new_filled < float(o["qty"]) - FLAT_TOL_SHARES:
        _mirror_stop("partial_fill", o["whale"])
        t.stats["partial_fills"] += 1
    _recent(book["id"], "fill", side=side, shares=round(inc, 4), px=px, maker=maker)
    if overfill:
        # a SELL past what the ledger held is a SHORT on a signed-net
        # venue: freeze (which names it), and trip the DB switch off
        # with the receipt
        await _freeze(t, book, "overfill")
        await _trip_live_off(t, "overfill", {"book": book["id"], "order": o["id"],
                                             "sold": inc, "ledger": book.get("ledger_net")})
        return "overfill"
    return "booked"


async def _trip_live_off(t: _Tick, why: str, receipt: dict) -> None:
    """The side-echo circuit's shape (live_executor:5551-5563): the DB
    switch goes false with a receipt naming the evidence, and this tick
    cancels everything. An admin turns it back on. t.cancel_all is read
    by _increases_refusal (no increase, no new book), by _place and the
    slippage flatten (nothing placed), by _act's keep path (a resting
    order is cancelled) and by _tick, which stops the book walk and
    cancels what step O kept before the trip (step-9 review)."""
    t.cancel_all = t.cancel_all or why
    try:
        await _write_state(t.pool, _STATE_LIVE, False)
        await _write_state(t.pool, f"{_STATE_LIVE}_trip",
                           {"at": _iso(t.now), "why": why, **receipt})
    except Exception:  # noqa: BLE001 — the tick is already cancel-only
        log.error("mirror_live: could not write the %s trip receipt", why, exc_info=True)
    log.error("MIRROR LIVE TRIPPED OFF: %s %s", why, receipt)


async def _freeze(t: _Tick, book: dict, reason: str, detail: dict | None = None) -> None:
    if book.get("state") in ("closed", "closing"):
        _mirror_stop(reason, book.get("whale"))
        return
    await t.pool.execute(_SQL_BOOK_FREEZE, book["id"], reason)
    if book.get("state") != "frozen" or book.get("frozen_reason") != reason:
        _mirror_stop(reason, book.get("whale"))
    if book.get("state") != "frozen":
        book["frozen_reason"] = reason
    book["state"] = "frozen"
    book["last_reason"] = reason
    book["frozen_ts"] = book.get("frozen_ts") or t.now
    book["frozen_ticks"] = int(book.get("frozen_ticks") or 0) + 1
    fr = t.stats["frozen_reasons"]
    fr[reason] = fr.get(reason, 0) + 1
    _recent(book["id"], "frozen", reason=reason, **(detail or {}))


async def _thaw(t: _Tick, book: dict) -> None:
    if book.get("state") != "frozen":
        return
    await t.pool.execute(_SQL_BOOK_THAW, book["id"])
    book.update(state="live", frozen_reason=None, frozen_ts=None)
    _recent(book["id"], "thawed")


async def _disarm_take(t: _Tick, book: dict) -> None:
    """Clear the take armed by a post-only 400. The arm says "the book
    was crossing when the rest was refused"; a rest that then PLACED,
    or an order that finished, contradicts or consumes that evidence,
    and a stale arm let a five-second-old rest be taken the instant
    the market touched it, past the rest-first wait (step-9 review;
    critic C15's never-IOC-first)."""
    if book.get("take_armed_ts") is None:
        return
    await t.pool.execute(_SQL_BOOK_ARM, book["id"], False)
    book["take_armed_ts"] = None


# ---------------------------------------------------------- step O: orders

def _order_side_of(o: dict) -> str:
    return "SELL" if o.get("side") == SELL else "BUY"


def _on_book_matches(o: dict, venue: dict, wire: float, qty: int) -> bool:
    """The rest lane's _bid_matches generalised to a side: OUR side, our
    cent, our whole quantity."""
    if str(venue.get("side") or "").upper() != _order_side_of(o) or not venue.get("order_id"):
        return False
    try:
        px = float(venue.get("price") or 0.0)
        q = float(venue.get("quantity") or 0.0)
    except (TypeError, ValueError):
        return False
    return abs(q - float(int(qty))) <= 1e-6 and abs(px - float(wire)) <= 1e-6


async def _ledger_ids(t: _Tick, slug: str) -> set | None:
    try:
        rows = await t.pool.fetch(_SQL_LEDGER_IDS, slug)
    except Exception:  # noqa: BLE001
        return None
    return {str(r["order_id"]) for r in rows if r["order_id"]}


async def _find_lost_placement(t: _Tick, o: dict, book: dict, orders: list,
                               window: tuple[float, float]) -> tuple[str, Any]:
    """The lost-response search of step O: the book's open orders by
    fingerprint with pre_ids, the protected set and every ledger id on
    the slug excluded (spec R: the mirror's search can adopt neither a
    copy id nor a pre-placement id). ('found', order) | ('ambiguous',
    n) | ('none', None) | ('unreadable', why)."""
    protected = await _read_protected(t)
    if protected is None:
        return "unreadable", "protected_ids_unreadable"
    ledger = await _ledger_ids(t, o["us_market_slug"])
    if ledger is None:
        return "unreadable", "ledger_ids_unreadable"
    pre = {str(x) for x in (_jsonish(o.get("pre_ids")) or [])}
    exclude = pre | protected | ledger
    lo, hi = window
    cands = []
    for v in orders:
        if str(v.get("us_market_slug") or "").lower() != str(o["us_market_slug"]).lower():
            continue
        if str(v.get("order_id")) in exclude:
            continue
        if not _on_book_matches(o, v, float(o["wire"]), int(o["qty"])):
            continue
        ts = le._order_created_ts(v.get("created_at"))
        if ts is None or not (lo <= ts <= hi):
            continue
        cands.append(v)
    if len(cands) == 1:
        return "found", cands[0]
    if len(cands) > 1:
        return "ambiguous", len(cands)
    return "none", None


async def _trade_log_fills(t: _Tick, o: dict, since: float, window: tuple[float, float],
                           by_order: bool = True) -> list | None:
    """Fills of a lost placement from the venue's trade log, BY ORDER
    and exact size only (le._lost_fill_is_ours's rule): the order the
    venue names must carry our quantity, our wire and our side, sit in
    the window, and be unknown to every ledger row. None when the log
    could not be read (raises, truncated) -- the row is left. With
    `by_order` False (a sole-holder CLOSE, which has no quantity and no
    wire of its own) the size match is skipped and the caller holds
    the fills to the venue's POSITION delta instead."""
    try:
        fills = await asyncio.to_thread(_paced, t.pmus.recent_trades, o["us_market_slug"], since)
    except Exception as exc:  # noqa: BLE001 — unreadable is not "no fills"
        log.warning("mirror_live: trade log for %s unreadable (%s)", o["us_market_slug"],
                    type(exc).__name__)
        return None
    protected = await _read_protected(t)
    ledger = await _ledger_ids(t, o["us_market_slug"])
    if protected is None or ledger is None:
        return None
    known = protected | ledger
    lo, hi = window
    ours = []
    for f in fills or []:
        try:
            ts = float(f.get("ts") or 0.0)
            if by_order:
                oq, op = f.get("order_qty"), f.get("order_price")
                if oq is None or op is None:
                    continue
                if (abs(float(oq) - float(int(o["qty"]))) > 1e-6
                        or abs(float(op) - float(o["wire"])) > 1e-6):
                    continue
        except (TypeError, ValueError):
            continue
        if not (lo <= ts <= hi):
            continue
        side = str(f.get("side") or "").upper()
        if _order_side_of(o) not in side:
            continue
        oid = f.get("order_id")
        if not oid or str(oid) in known:
            continue
        ours.append(f)
    return ours


async def _reconcile_placing(t: _Tick, o: dict, book: dict) -> None:
    """A 'placing' row with no order id: the process died between
    orders.create and the persist. Adopt by fingerprint, else book from
    the trade log by ORDER, else freeze 'placement_lost'; past
    _LOST_FILL_WINDOW_S the order is 'lost' and the book thaws only
    when venue == ledger (step P)."""
    t.nonterminal.add(book["id"])
    age = t.now - float(o.get("placed_ts") or t.now)
    if age < PLACING_ORPHAN_S:
        _mirror_stop("open_order_pending", o["whale"])
        return
    if o.get("tif") == "CLOSE":
        await _reconcile_lost_close(t, o, book)
        return
    orders = await _read_open(t)
    if orders is None:
        return                       # named already; the row is left
    placed = float(o.get("placed_ts") or t.now)
    window = (placed - le._ORPHAN_SKEW_S, placed + le._ORPHAN_MATCH_S)
    verdict, what = await _find_lost_placement(t, o, book, orders, window)
    if verdict == "unreadable":
        log.warning("mirror_live: lost placement on row %s not searched (%s); left for the "
                    "next tick", o["id"], what)
        return
    if verdict == "found":
        oid = str(what.get("order_id"))
        await t.pool.execute(_SQL_ORDER_ADOPT, o["id"], oid, "adopted by fingerprint")
        o["order_id"], o["state"] = oid, "open"
        _recent(book["id"], "adopted", order=oid)
        log.warning("mirror_live: order row %s adopted venue order %s by fingerprint", o["id"], oid)
        await _reconcile_open(t, o, book)
        return
    if verdict == "ambiguous":
        await _freeze(t, book, "lost_ambiguous", {"candidates": what})
        if age >= le._LOST_FILL_WINDOW_S:
            await _mark_lost(t, o, book)
        return
    fills = await _trade_log_fills(t, o, placed - 30.0, (placed - le._ORPHAN_SKEW_S,
                                                          placed + le._LOST_FILL_WINDOW_S))
    if fills is None:
        return
    if fills:
        oid = str(fills[0].get("order_id"))
        await t.pool.execute(_SQL_ORDER_ADOPT, o["id"], oid, "adopted from the trade log")
        o["order_id"], o["state"] = oid, "open"
        total = 0.0
        notional = 0.0
        for f in fills:
            if str(f.get("order_id")) != oid:
                continue
            q, px = _num(f.get("qty")), _num(f.get("price"))
            if q and px:
                total += q
                notional += q * px
        px = round(notional / total, 6) if total > 0 else float(o["wire"])
        st = {"state": "filled" if total >= float(o["qty"]) - FLAT_TOL_SHARES else "cancelled",
              "filled_shares": total, "avg_px": px}
        await _book_delta(t, o, book, st, maker=True)
        await _finish_order(t, o, book, st, "booked from the trade log")
        return
    await _freeze(t, book, "placement_lost")
    if age >= le._LOST_FILL_WINDOW_S:
        await _mark_lost(t, o, book)


async def _reconcile_lost_close(t: _Tick, o: dict, book: dict) -> None:
    """A sole-holder CLOSE row (tif 'CLOSE') whose response was lost.
    close_position carries no wire and no quantity of its own, so the
    fingerprint search and the trade log's by-ORDER match can find
    nothing for it (step-9 review: the search read float(None) and the
    row sat 'placing' for good). The venue's POSITION is the evidence
    instead: the book was the slug's sole holder when the close was
    sent, so the shares that left the account since are what the close
    sold, and the venue's own trade log names their price -- one order
    id unknown to every ledger row, its fills held to the position
    delta. Nothing sold: the row is 'lost' by name past the window and
    the book thaws when venue == ledger, so the flatten runs again. An
    unreadable position or log leaves the row for the next tick; a
    delta the log cannot account for, or more than one seller on a
    sole-held slug, stays frozen by name for a human, never booked at
    a guessed price."""
    t.nonterminal.add(book["id"])
    placed = float(o.get("placed_ts") or t.now)
    age = t.now - placed
    slug = o["us_market_slug"]
    try:
        held, _avg = await le._pm_held(slug)
    except Exception as exc:  # noqa: BLE001 — unreadable: the row is left
        log.warning("mirror_live: venue position for %s unreadable (%s); lost close on row %s "
                    "left for the next tick", slug, type(exc).__name__, o["id"])
        return
    qty = int(o["qty"])
    sold = qty - int(held)
    if sold < 1:
        await _freeze(t, book, "placement_lost", {"close": "nothing_sold", "held": int(held)})
        if age >= le._LOST_FILL_WINDOW_S:
            await _mark_lost(t, o, book)
        return
    fills = await _trade_log_fills(t, o, placed - 30.0,
                                   (placed - le._ORPHAN_SKEW_S, placed + le._LOST_FILL_WINDOW_S),
                                   by_order=False)
    if fills is None:
        return
    by_id: dict[str, list] = {}
    for f in fills:
        by_id.setdefault(str(f.get("order_id")), []).append(f)
    total = notional = 0.0
    if len(by_id) == 1:
        for f in next(iter(by_id.values())):
            q, px = _num(f.get("qty")), _num(f.get("price"))
            if q and px and 0.0 < px < 1.0:
                total += q
                notional += q * px
    if len(by_id) != 1 or total < 1.0:
        await _freeze(t, book, "placement_lost",
                      {"close": "unattributed", "sold": sold, "sellers": len(by_id)})
        return
    oid = next(iter(by_id))
    booked = min(total, float(sold))
    px = round(notional / total, 6)
    await t.pool.execute(_SQL_ORDER_ADOPT, o["id"], oid, "adopted from the trade log by position")
    o["order_id"], o["state"] = oid, "open"
    _recent(book["id"], "adopted", order=oid, sold=sold)
    st = {"state": "filled" if booked >= qty - FLAT_TOL_SHARES else "cancelled",
          "filled_shares": booked, "avg_px": px}
    await _book_delta(t, o, book, st, maker=False, taker_at_placement=True)
    await _finish_order(t, o, book, st, "booked from the position and the trade log")


async def _mark_lost(t: _Tick, o: dict, book: dict) -> None:
    await t.pool.execute(_SQL_ORDER_STATE, o["id"], "lost", None, "order_lost", None, None)
    await t.pool.execute(_SQL_BOOK_OPEN_ORDER, book["id"], None)
    o["state"] = "lost"
    t.nonterminal.discard(book["id"])
    _mirror_stop("order_lost", o["whale"])
    _recent(book["id"], "order_lost", order_row=o["id"])


async def _order_status(t: _Tick, oid: str) -> dict | None:
    try:
        return await asyncio.to_thread(_paced, t.pmus.order_status, oid)
    except Exception as exc:  # noqa: BLE001 — unknown is a state, not a None
        log.warning("mirror_live: order %s unreadable (%s)", oid, type(exc).__name__)
        return None


async def _book_delta(t: _Tick, o: dict, book: dict, st: dict, maker: bool,
                      taker_at_placement: bool = False) -> str | None:
    filled = _num(st.get("filled_shares"))
    if filled is None:
        return None
    inc = filled - float(o.get("booked_filled") or 0.0)
    if inc < FLAT_TOL_SHARES:
        return None
    px = _num(st.get("avg_px"))
    if px is None or not (0.0 < px < 1.0):
        px = _num(o.get("wire"))     # a rest fills at its own cent; the venue named no better
    if px is None or not (0.0 < px < 1.0):
        # a CLOSE row has no cent of its own (its wire is 0.0): a fill
        # the venue did not price is refused by name and left unbooked
        # for the next read, never booked at a guess and never a
        # TypeError (step-9 review)
        _mirror_stop("no_price", o["whale"])
        log.error("mirror_live: fill on order %s carries no price and the row has no cent; "
                  "nothing booked", o["id"])
        await _freeze(t, book, "no_price")
        return "no_price"
    try:
        out = await _book_fill(t, o, book, inc, px, maker, taker_at_placement)
    except Exception as exc:  # noqa: BLE001 — a write failure is named; the cursor did not move
        # A LEDGER THAT CANNOT BE WRITTEN IS A BOOK THAT CANNOT BE
        # REASONED ABOUT: frozen under the failure's name (nothing new
        # on it, its rest cancelled); the order is never finalized while
        # a fill stands unbooked (_finish_order), so the next tick books
        # it exactly once off the cursor
        _mirror_stop("write_failed", o["whale"])
        log.error("mirror_live: booking on order %s failed (%s); re-booked next tick",
                  o["id"], type(exc).__name__, exc_info=True)
        await _freeze(t, book, "write_failed")
        return "write_failed"
    if out == "rebooked":
        _recent(book["id"], "rebooked", order_row=o["id"])
    return out


def _terminal_state(o: dict, st: dict) -> str:
    filled = float(_num(st.get("filled_shares")) or o.get("booked_filled") or 0.0)
    if filled >= float(o["qty"]) - FLAT_TOL_SHARES:
        return "filled"
    vs = str(st.get("state") or "").lower()
    if vs == "expired":
        return "expired"
    if vs == "rejected":
        return "rejected"
    return "cancelled"


async def _finish_order(t: _Tick, o: dict, book: dict, st: dict, reason: str | None) -> str:
    venue_filled = _num(st.get("filled_shares"))
    if venue_filled is not None and venue_filled > float(o.get("booked_filled") or 0.0) + FLAT_TOL_SHARES:
        # the venue filled more than the ledger booked (the booking
        # failed): a terminal state would drop those shares for good,
        # so the row stays 'unknown' and is re-read until they book
        await t.pool.execute(_SQL_ORDER_STATE, o["id"], "unknown", str(st.get("state") or ""),
                             "unbooked_fill", None, o.get("order_id"))
        o["state"] = "unknown"
        t.open_by_book.pop(book["id"], None)
        t.nonterminal.add(book["id"])
        await _freeze(t, book, "write_failed")
        return "unknown"
    state = _terminal_state(o, st)
    maker = (o.get("tif") in ("GTC", "GTD") and not o.get("taker_at_placement")
             and o.get("kind") != "take")
    await t.pool.execute(_SQL_ORDER_STATE, o["id"], state, str(st.get("state") or ""),
                         reason, bool(maker), o.get("order_id"))
    await t.pool.execute(_SQL_BOOK_OPEN_ORDER, book["id"], None)
    o["state"] = state
    book["open_order_id"] = None
    t.open_by_book.pop(book["id"], None)
    t.nonterminal.discard(book["id"])
    await _disarm_take(t, book)
    filled = float(o.get("booked_filled") or 0.0)
    w = o["whale"]
    if state == "filled":
        if maker:
            _mirror_stop("filled_rest", w)
            t.stats["filled_rest"] += 1
        else:
            _mirror_stop("filled_take", w)
            t.stats["filled_take"] += 1
        if o.get("kind") in ("flatten_paired", "flatten_vanished"):
            t.stats["flattened"] += 1
    elif state == "expired":
        _mirror_stop("expired", w)
    elif filled <= FLAT_TOL_SHARES and state == "cancelled":
        _mirror_stop("cancelled_unfilled", w)
        if o.get("kind") in ("reduce", "flatten_paired"):
            _mirror_stop("reduce_unfilled", w)
    if state in ("cancelled", "expired"):
        t.stats["cancelled"] += 1
    _recent(book["id"], "order_" + state, order_row=o["id"], filled=filled)
    return state


async def _reconcile_open(t: _Tick, o: dict, book: dict, cancel_reason: str | None = None) -> str:
    """An order with an id: read it, book the delta, write a terminal
    state, or cancel it when the tick or the book says so."""
    oid = str(o["order_id"])
    st = await _order_status(t, oid)
    if not st:
        await t.pool.execute(_SQL_ORDER_STATE, o["id"], "unknown", None, "order_state_unknown",
                             None, oid)
        o["state"] = "unknown"
        t.nonterminal.add(book["id"])
        await _freeze(t, book, "order_state_unknown")
        return "unknown"
    await _book_delta(t, o, book, st, maker=(o.get("kind") != "take"
                                             and not o.get("taker_at_placement")))
    if le._rest_terminal(st):
        return await _finish_order(t, o, book, st, o.get("reason"))
    if cancel_reason is None:
        if t.cancel_all:
            cancel_reason = t.cancel_all
        elif o["side"] == BUY and _increases_refusal(t, o["whale"]):
            cancel_reason = _increases_refusal(t, o["whale"])
        elif book.get("state") in ("frozen", "closing", "closed"):
            # a CLOSED book's order is a rest nobody plans for: its
            # fill would land on a retired row (step-9 review)
            cancel_reason = book.get("frozen_reason") or book.get("state")
        elif t.now - float(o.get("placed_ts") or t.now) >= float(rules.MIRROR_REST_TTL_S):
            cancel_reason = "ttl"
    if cancel_reason:
        return await _cancel_and_settle(t, o, book, cancel_reason)
    t.open_by_book[book["id"]] = (o, st)
    t.nonterminal.add(book["id"])
    if o["state"] != "open":
        await t.pool.execute(_SQL_ORDER_STATE, o["id"], "open", str(st.get("state") or ""),
                             o.get("reason"), None, oid)
        o["state"] = "open"
    return "open"


async def _cancel_and_settle(t: _Tick, o: dict, book: dict, reason: str) -> str:
    """Step C: cancel twice, read until terminal (bounded), book the
    delta, write the terminal state. Non-terminal after the reads is
    'unknown', the book frozen 'cancel_pending', nothing new on it."""
    if t.ops >= rules.MIRROR_MAX_ORDER_OPS_PER_TICK:
        # the order still RESTS: it stays in open_by_book so no caller
        # reads "nothing open" off a cancel that never went out (the
        # settled path closed a book over its resting order; step-9
        # review); the next tick's budget cancels it
        _mirror_stop("ops_capped", o["whale"])
        return "ops_capped"
    t.ops += 1
    oid = str(o["order_id"])
    slug = o["us_market_slug"]
    cancel_ok = False
    for _attempt in range(CANCEL_ATTEMPTS):
        try:
            c = await asyncio.to_thread(t.pmus.cancel_order, oid, slug)
            cancel_ok = bool((c or {}).get("ok"))
        except Exception:  # noqa: BLE001
            cancel_ok = False
        if cancel_ok:
            break
    st = None
    for _i in range(CANCEL_READS):
        st = await _order_status(t, oid)
        if le._rest_terminal(st):
            break
        await _sleep(CANCEL_READ_GAP_S)
    if st:
        await _book_delta(t, o, book, st, maker=(o.get("kind") != "take"
                                                 and not o.get("taker_at_placement")))
    if not le._rest_terminal(st):
        await t.pool.execute(_SQL_ORDER_STATE, o["id"], "unknown", str((st or {}).get("state") or ""),
                             reason, None, oid)
        o["state"] = "unknown"
        t.open_by_book.pop(book["id"], None)
        t.nonterminal.add(book["id"])
        await _freeze(t, book, "cancel_pending", {"cancel_ok": cancel_ok})
        return "unknown"
    _recent(book["id"], "cancel", reason=reason, cancel_ok=cancel_ok)
    if reason in ("ttl", "replace"):
        t.stats["requotes"] += 1
    return await _finish_order(t, o, book, st, reason)


async def _reconcile_orders(t: _Tick, count: bool = True) -> None:
    """Step O. Every non-terminal mirror order, oldest first, under
    its book's lock, BEFORE any book is planned. Run a second time by
    _tick after a mid-tick trip (`count` False keeps the first pass's
    figure): every order the first pass KEPT is cancelled under the
    trip's name, since t.cancel_all is now set."""
    rows = [dict(r) for r in await t.pool.fetch(_SQL_ORDERS_OPEN)]
    if count:
        t.stats["orders_open"] = len(rows)
    for o in rows:
        try:
            book = await t.pool.fetchrow(_SQL_BOOK_READ, o["book_id"])
        except Exception as exc:  # noqa: BLE001
            log.warning("mirror_live: book %s unreadable (%s)", o["book_id"], type(exc).__name__)
            continue
        if not book:
            continue
        book = dict(book)
        async with _lock_for(book["id"]):
            try:
                if not o.get("order_id"):
                    await _reconcile_placing(t, o, book)
                else:
                    await _reconcile_open(t, o, book)
            except Exception as exc:  # noqa: BLE001 — one order, not the tick
                _mirror_stop("book_error", o.get("whale"))
                log.exception("mirror_live: order %s failed (%s)", o["id"], type(exc).__name__)
            if o.get("state") in ("placing", "open", "unknown"):
                t.nonterminal.add(book["id"])


# ------------------------------------------------------ step B: the books

@dataclass
class _Reading:
    """Everything step B read for one (whale, market) before deciding."""
    whale: str
    cid: str
    slug: str
    la: str
    oa: str | None
    fills: list
    his_long: float
    his_other: float
    snap: dict
    snap_age: float | None
    snap_partial: bool
    fresh_read: bool          # a snapshot young enough, complete or not
    fresh: bool               # young AND complete: the position source
    snap_long: float | None
    snap_other: float | None
    bid: float | None
    ask: float | None
    mark: float | None
    venue: float
    manual: float
    market: dict | None
    market_live: bool | None      # None: the markets row could not be read (never "closed")


async def _read_market(t: _Tick, whale: str, cid: str, slug: str, la: str, oa: str | None,
                       fills: list, read_quote: bool = True,
                       market: dict | None = None) -> _Reading:
    """`market` is the caller's own step-M reading when it has one
    (_tick_book reads the row once, BEFORE the plan); read here only
    for a candidate. A second read that failed would otherwise turn a
    live market into a "closed" one for the close rule (step-9 review:
    one unreadable read is the named refusal, never a fact)."""
    pos = mi.net_positions(fills)
    his_long = float(pos.get(la, 0.0)) if la else 0.0
    his_other = float(pos.get(oa, 0.0)) if oa else 0.0
    snap, age, partial = await _snapshot(t, whale)
    fresh_read = age is not None and age <= ms.SNAP_MAX_AGE_S
    fresh = fresh_read and not partial

    def _snap_of(asset):
        if not asset or not fresh_read:
            return None
        if asset in snap:
            return float(snap[asset])
        return None if partial else 0.0

    bid = ask = None
    if read_quote:
        bid, ask = await _bbo(t, slug)
    venue = float((t.positions or {}).get(slug.lower(), 0.0)) if t.positions is not None else 0.0
    try:
        manual = float(await t.pool.fetchval(_SQL_MANUAL_SHARES, slug) or 0.0)
    except Exception:  # noqa: BLE001 — the desk's shares unreadable: explained nothing
        manual = 0.0
    mk = market if market is not None else await _market(t, cid)
    market_live = None if mk is None else bool(mk["closed"] is False and mk["resolved"] is False)
    return _Reading(whale, cid, slug, la, oa, fills, his_long, his_other, snap, age,
                    bool(partial), fresh_read, fresh, _snap_of(la), _snap_of(oa), bid, ask,
                    _mark_of(bid, ask), venue, manual, mk, market_live)


def _his_level(fills: list, long_asset: str | None, other_asset: str | None,
               reducing: bool) -> float | None:
    """His level for the WIRE, unrounded. The shadow's his_level picks
    the same fill -- his most recent move in the direction we follow,
    by timestamp (mirror_shadow.his_level) -- but hands back the
    other-token equivalent as round(1 - p, 4), a logging figure. The
    wire must come from the exact figure (addendum section 10:
    rules.sell_price ceils the UNROUNDED max(his equivalent, ask); his
    other-token BUY at 0.47996 is 0.52004 to him, and the 4-place 0.52
    rested a cent UNDER him, the one case the rule forbids; step-9
    review). The selection is restated from the shadow only because
    its figure is rounded at source; the worker tests pin the two
    agree to four places on every fixture."""
    best: tuple[float, float] | None = None
    for f in fills:
        a, side = str(f.get("asset") or ""), str(f.get("side") or "").upper()
        try:
            ts = float(f.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        p = ms._px(f)
        if p is None:
            continue
        lvl = None
        if not reducing:
            if long_asset and a == long_asset and side == "BUY":
                lvl = p
        elif long_asset and a == long_asset and side == "SELL":
            lvl = p
        elif other_asset and a == other_asset and side == "BUY":
            lvl = 1.0 - p
        if lvl is not None and (best is None or ts >= best[0]):
            best = (ts, lvl)
    return best[1] if best else None


def _net_for(r: _Reading, drift: rules.DriftRule) -> tuple[float, float | None]:
    """(net used for the target, snapshot net). The POSITION is the
    exit worker's fresh complete snapshot (addendum section 1); on a
    fresh disagreement the smaller reading sizes the reduction; with
    no fresh read the derived reading carries reductions only."""
    derived = mi.his_net(r.his_long, r.his_other)
    snap_net = None
    if r.fresh and r.snap_long is not None and r.snap_other is not None:
        snap_net = mi.his_net(r.snap_long, r.snap_other)
    if snap_net is None:
        return derived, None
    if drift.increase_ok:
        return snap_net, snap_net
    if drift.reduce_from == "smaller":
        return min(derived, snap_net), snap_net
    return derived, snap_net


async def _confirm_gone(t: _Tick, whale: str, asset: str) -> bool:
    """The mirror's OWN vanish confirmation (addendum section 8): the
    exit worker's `ours` clause excludes a book-held asset from its
    partial-walk branch, so the worker asks the data API itself. False
    on everything unreadable."""
    try:
        address = await t.pool.fetchval(_SQL_WHALE_ADDRESS, whale.lower())
    except Exception:  # noqa: BLE001
        return False
    if not address or t.http is None:
        return False
    try:
        return bool(await whale_exits._confirm_gone(t.http, t.pool, str(address), asset))
    except Exception:  # noqa: BLE001 — unknown is not gone
        return False


async def _shadow_check(t: _Tick, book: dict, target: int, net_used: float) -> None:
    """spec 1e: a shadow reading of the same whale and market within
    60 s that computed a different target FROM THE SAME INPUTS -- the
    same net and the book's ratio -- is an arithmetic divergence and is
    named; readings from different inputs (the shadow's live ratio and
    fills-derived net against the book's fixed ratio and snapshot net,
    addendum sections 1 and 7) are not compared."""
    try:
        row = await t.pool.fetchrow(_SQL_SHADOW_LATEST, book["whale"], book["condition_id"])
    except Exception:  # noqa: BLE001 — the shadow's table is its own
        return
    if not row:
        return
    at, sr, sn = _num(row["at_ts"]), _num(row["ratio"]), _num(row["his_net"])
    if at is None or abs(t.now - at) > SHADOW_AGREE_WINDOW_S:
        return
    if sr is None or sn is None or abs(sr - float(book.get("ratio") or 0.0)) > 1e-9:
        return
    if abs(sn - net_used) > 1e-6:
        return
    if row["target"] is not None and int(row["target"]) != int(target):
        _mirror_stop("shadow_live_disagree", book["whale"])
        _recent(book["id"], "shadow_live_disagree", shadow=int(row["target"]), live=int(target))


async def _write_plan(t: _Tick, book: dict, r: _Reading | None, target, target_raw,
                      drift_v, his_level, reason: str, plan: dict) -> None:
    net = None
    if r is not None:
        net = mi.his_net(r.his_long, r.his_other)
    book["last_reason"] = reason
    await t.pool.execute(
        _SQL_BOOK_PLAN, book["id"], target, target_raw, net,
        r.his_long if r else None, r.his_other if r else None,
        r.snap_long if r else None, r.snap_other if r else None, drift_v, his_level,
        r.venue if r else None, reason, json.dumps(plan, default=str))


async def _cancel_open_for(t: _Tick, book: dict, reason: str) -> None:
    ent = t.open_by_book.get(book["id"])
    if ent is None:
        return
    o, _st = ent
    await _cancel_and_settle(t, o, book, reason)


async def _close_settled(t: _Tick, book: dict, standing: dict, status: str) -> None:
    """Step M's tail: the standing row left 'filled' -- settled by
    _settle_pmus_from_venue, or cashed_out / cancelled by the episode
    close -- so the book is closed and, on 'settled', its own figure is
    cross-checked against the venue's (spec 1e, book_settle_disagree)."""
    settled_pnl = own = None
    disagree = None
    if status == "settled":
        settled_pnl = _num(standing.get("pnl"))
        payout = None
        mk = await _market(t, book["condition_id"])
        try:
            idx = await t.pool.fetchval(_SQL_TOKEN_INDEX, book["long_asset"])
        except Exception:  # noqa: BLE001
            idx = None
        if mk is not None:
            payout = payout_of(mk.get("resolved_prices"), idx)
        ac = _num(book.get("avg_cost"))
        shares = float(book.get("ledger_net") or 0.0)
        if payout is not None and (ac is not None or shares <= FLAT_TOL_SHARES):
            own = round(float(book.get("realized_pnl") or 0.0)
                        + shares * (payout - (ac or 0.0)), 4)
        if settled_pnl is not None and own is not None:
            disagree = abs(settled_pnl - own) > SETTLE_DISAGREE_USD
            if disagree:
                _mirror_stop("book_settle_disagree", book["whale"])
    await t.pool.execute(_SQL_BOOK_SETTLED, book["id"], settled_pnl, own, disagree,
                         f"closed: standing row {status}")
    book["state"] = "closed"
    t.stats["closed_books"] += 1
    _recent(book["id"], "closed", row=status, settled=settled_pnl, own=own)


async def _maybe_close_episode(t: _Tick, book: dict, market_live: bool | None,
                               vanished: bool, target: int | None, plan: dict) -> str:
    """Step E: the episode close by the rules' verdict, only with no
    non-terminal order on the book."""
    if (book["id"] in t.open_by_book or book["id"] in t.nonterminal
            or book.get("state") == "closed"):
        return "orders_open"
    flat_for = None
    ledger = float(book.get("ledger_net") or 0.0)
    if abs(ledger) < FLAT_TOL_SHARES and target == 0:
        since = _num((_jsonish(book.get("last_plan")) or {}).get("flat_since"))
        since = t.now if since is None else since
        plan["flat_since"] = since
        flat_for = t.now - since
    why = rules.episode_close_reason(_book_state(book), None if market_live is None else not market_live,
                                     vanished, flat_for, 0)
    if why not in ("cashed_out", "cancelled"):
        return why
    verdict = await le._close_mirror_episode(t.pool, book["standing_row_id"],
                                             float(book.get("gross_buy_usd") or 0.0))
    if verdict is None:
        return "close_refused"
    await t.pool.execute(_SQL_BOOK_STATE, book["id"], "closed", f"closed_{verdict}")
    book["state"] = "closed"
    t.stats["closed_books"] += 1
    _mirror_stop(f"closed_{verdict}", book["whale"])
    _recent(book["id"], "closed", how=verdict)
    return verdict


async def _tick_book(t: _Tick, book: dict) -> None:
    """One existing book: read, step M, plan, act, close."""
    w, cid, slug = book["whale"], book["condition_id"], book["us_market_slug"]
    la, oa = book["long_asset"], book.get("other_asset")
    t.books_seen.add((w, cid))
    if book.get("state") == "frozen":
        t.stats["books_frozen"] += 1
        if book.get("frozen_ts") and t.now - float(book["frozen_ts"]) > float(rules.MIRROR_FROZEN_ALERT_S):
            t.stats["status"] = "degraded"
    else:
        t.stats["books_live"] += 1
    # the standing row first: a row the settlement or the close has
    # retired ends the book before any venue read
    standing = await t.pool.fetchrow(_SQL_STANDING_READ, book["standing_row_id"])
    standing = dict(standing) if standing else None
    if standing is not None and standing.get("status") in ("settled", "cashed_out", "cancelled"):
        await _cancel_open_for(t, book, "market_closed")
        # closed only once nothing of the book is non-terminal: a
        # cancel the ops budget refused, or an order 'unknown', still
        # rests, and its fill would land on the retired row (step-9
        # review); the next tick finishes the cancel, then closes
        if book["id"] not in t.open_by_book and book["id"] not in t.nonterminal:
            await _close_settled(t, book, standing, str(standing["status"]))
        return
    if standing is None or standing.get("status") != "filled" or standing.get("lane") != "mirror":
        await _cancel_open_for(t, book, "row_not_live")
        await _freeze(t, book, "row_not_live")
        return
    fills = await ms.his_fills(t.pool, w, cid)
    # STEP M BEFORE ANY PLAN (addendum section 10): a closed or
    # resolved market, or a closing book, cancels and never increases.
    # 'closing' is entered on a POSITIVE reading only (closed True or
    # resolved True): a markets row that could not be read, is absent
    # or is malformed is `market_unreadable` -- what rests is cancelled
    # and the book is HELD with no plan until a readable tick, because
    # one unreadable read once made a held book 'closing' for good,
    # with no reduction or flatten ever again (step-9 review)
    mk = await _market(t, cid)
    market_live = None if mk is None else bool(mk["closed"] is False and mk["resolved"] is False)
    closed_read = mk is not None and (mk["closed"] is True or mk["resolved"] is True)
    if closed_read or book.get("state") == "closing":
        if book.get("state") != "closing":
            await t.pool.execute(_SQL_BOOK_STATE, book["id"], "closing", "market_closed")
            book["state"] = "closing"
            _mirror_stop("market_closed", w)
        await _cancel_open_for(t, book, "market_closed")
        plan = {"kind": "closing", "market_live": market_live}
        why = await _maybe_close_episode(t, book, market_live, False,
                                         0 if abs(float(book.get("ledger_net") or 0)) < FLAT_TOL_SHARES else None,
                                         plan)
        if book.get("state") != "closed":
            await _write_plan(t, book, None, book.get("target"), None, None, book.get("his_level"),
                              f"closing: {why}", plan)
        return
    if market_live is not True:
        _mirror_stop("market_unreadable", w)
        await _cancel_open_for(t, book, "market_unreadable")
        await _write_plan(t, book, None, book.get("target"), None, None, book.get("his_level"),
                          "market_unreadable", {"kind": "no_plan", "market_unreadable": True,
                                                "at": t.now})
        return
    r = await _read_market(t, w, cid, slug, la, oa, fills, market=mk)
    if t.abandoned:
        return
    drift = rules.drift_rule(r.his_long, r.snap_long, r.fresh_read, r.snap_partial,
                             last_fresh_agreed=True)
    net, snap_net = _net_for(r, drift)
    ledger = int(book.get("ledger_net") or 0)
    venue_int = int(r.venue)
    # the plan's numbers, written whatever happens below
    plan: dict[str, Any] = {"bid": r.bid, "ask": r.ask, "mark": r.mark, "venue": r.venue,
                            "manual": r.manual, "ledger": ledger, "net": net,
                            "snap_net": snap_net, "fresh": r.fresh, "drift": drift.drift,
                            "at": t.now}
    prior_plan = _jsonish(book.get("last_plan")) or {}
    # the flat clock carries only while the book IS flat: a re-bought
    # book that flattens again starts a new MIRROR_FLAT_CLOSE_S wait,
    # never closes cashed_out at once off the clock of an earlier flat
    # spell (step-9 review); dropped again below once the target reads
    # above zero
    if prior_plan.get("flat_since") is not None and abs(ledger) < FLAT_TOL_SHARES:
        plan["flat_since"] = prior_plan["flat_since"]
    # THE TARGET, from the book's FIXED ratio (addendum section 7)
    if t.flatten_all:
        tg = {"target": 0, "raw": 0.0, "refusal": None}
    else:
        tg = rules.mirror_target(book.get("ratio"), net, r.mark, MEASURE_CLIP_USD,
                                 cap_usd=rules.MIRROR_NET_CAP_USD)
    target = tg["target"]
    if target is not None and target > 0:
        plan.pop("flat_since", None)
    if tg.get("refusal") == "short_side_refused":
        _mirror_stop("short_side_refused", w)
    elif tg.get("refusal"):
        # NO PLAN: never "target zero, flatten". What rests is cancelled
        # by the plan's own name; the book is held.
        _mirror_stop(tg["refusal"], w)
        await _cancel_open_for(t, book, tg["refusal"])
        await _write_plan(t, book, r, None, None, drift.drift, book.get("his_level"),
                          tg["refusal"], {**plan, "kind": "no_plan"})
        return
    plan.update(target=target, target_raw=tg["raw"])
    await _shadow_check(t, book, target, net)
    # THE FREEZE: venue vs ledger + the desk's explained shares
    explained = ledger + r.manual
    if abs(venue_int - explained) > mi.VENUE_LEDGER_TOL_SHARES:
        detail = {"venue": venue_int, "ledger": ledger, "manual": r.manual}
        if r.venue < 0 and ledger > 0:
            await _trip_live_off(t, "wrong_sign_trip", {"book": book["id"], **detail})
            await _cancel_open_for(t, book, "wrong_sign_trip")
            await _freeze(t, book, "wrong_sign_trip", detail)
        else:
            await _cancel_open_for(t, book, "venue_ledger_disagree")
            await _freeze(t, book, "venue_ledger_disagree", detail)
            if int(book.get("frozen_ticks") or 0) > rules.MIRROR_FROZEN_NAME_TICKS:
                try:
                    await t.pool.execute(_SQL_STANDING_NAME, book["standing_row_id"])
                except Exception:  # noqa: BLE001 — the name is for a human, best-effort
                    log.warning("mirror_live: could not name the standing row of book %s",
                                book["id"], exc_info=True)
        await _write_plan(t, book, r, target, tg["raw"], drift.drift, book.get("his_level"),
                          book["frozen_reason"], {**plan, "kind": "frozen", **detail})
        return
    if book.get("state") == "frozen":
        if book["id"] in t.open_by_book:
            await _cancel_open_for(t, book, book.get("frozen_reason") or "frozen")
        if book["id"] not in t.open_by_book and book["id"] not in t.nonterminal:
            # venue == ledger and nothing non-terminal: the book thaws
            await _thaw(t, book)
        if book.get("state") == "frozen":
            await _write_plan(t, book, r, target, tg["raw"], drift.drift, book.get("his_level"),
                              book["frozen_reason"], {**plan, "kind": "frozen"})
            return
    # INCREASES: mode, allowlist, the drift rule, the starred re-checks
    inc_refusal = _increases_refusal(t, w)
    if inc_refusal is None and not drift.increase_ok:
        inc_refusal = drift.refusal or "snapshot_stale"
    if inc_refusal is None and target > ledger:
        inc_refusal = await _increase_recheck(t, book, r)
    reducing = target <= ledger
    his_px = _his_level(fills, la, oa, reducing)
    p = mi.plan(target, float(ledger), float(venue_int - r.manual), mi.Book(r.bid, r.ask),
                his_px, r.mark)
    kind = None
    confirm_gone = None
    cancel_reason = None
    vanished = False
    if p.side == BUY:
        kind = "increase"
        if inc_refusal:
            # the refusal is the name a resting order is cancelled
            # under (drift, snapshot_stale, the re-check's clause),
            # never the unlisted "no_plan" (step-9 review)
            _mirror_stop(inc_refusal, w)
            cancel_reason = inc_refusal
            p = None
    elif p.side == SELL:
        if target > 0:
            kind = "reduce"
        elif t.flatten_all:
            kind = "flatten_vanished"
        else:
            if r.his_long <= 0 and r.his_other <= 0:
                confirm_gone = await _confirm_gone(t, w, la)
            kind = rules.select_flatten(target, r.his_long, r.his_other, r.fresh_read,
                                        r.snap_long, r.snap_other, r.market_live, confirm_gone,
                                        r.snap_partial)
            if kind == "vanish_unconfirmed":
                _mirror_stop("vanish_unconfirmed", w)
                kind = "flatten_paired"
    else:
        _mirror_stop(rules.plan_reason_key(p.reason), w)
        if (target == 0 and abs(ledger) < FLAT_TOL_SHARES
                and r.his_long <= 0 and r.his_other <= 0):
            # flat at target 0 with fills reading him gone: the vanish
            # is confirmed by the same rule the SELL tick used, so the
            # episode closes on it (spec 1c) instead of waiting out the
            # flat hour (step-9 review); unconfirmed is not vanished
            confirm_gone = await _confirm_gone(t, w, la)
            vanished = rules.select_flatten(target, r.his_long, r.his_other, r.fresh_read,
                                            r.snap_long, r.snap_other, r.market_live,
                                            confirm_gone, r.snap_partial) == "flatten_vanished"
    if kind == "flatten_vanished":
        # the vanish's own clock (the flatten rest reference reads it):
        # carried while the book stays in the vanish, reset the tick it
        # re-enters (step-9 review)
        since = _num(prior_plan.get("vanish_since")) if prior_plan.get("kind") == "flatten_vanished" else None
        plan["vanish_since"] = t.now if since is None else since
    plan.update(kind=kind, side=(p.side if p else None), qty=(p.qty if p else 0),
                price=(p.price if p else None), his_level=his_px,
                reason=(p.reason if p else inc_refusal))
    reason = p.reason if p else (inc_refusal or "no plan")
    try:
        if kind == "flatten_vanished" and p is not None:
            vanished = True
            reason = await _flatten_vanished(t, book, r, p, his_px, plan)
        else:
            reason = await _act(t, book, r, p, kind, his_px, plan, cancel_reason) or reason
    finally:
        why = await _maybe_close_episode(t, book, r.market_live, vanished, target, plan)
        plan["close"] = why
        await _write_plan(t, book, r, target, tg["raw"], drift.drift, his_px, reason, plan)


async def _increase_recheck(t: _Tick, book: dict, r: _Reading) -> str | None:
    """The starred admission clauses on every INCREASE (spec A): clip,
    mapping, edge, cell -- read now, never remembered from open."""
    ok, why = await le._mapping_admitted(t.pool, r.whale, book.get("map_source"), r.slug)
    edge_ok, edge_why = edge_gate.verdict(r.whale)
    his_slug = next((f.get("market_slug") for f in r.fills if f.get("market_slug")), None)
    clause = copy_sports.copy_verdict(r.whale, str(his_slug or ""), price=book.get("his_level"))
    facts = rules.AdmissionFacts(increases_ok=True, per_fill_usd=le.per_fill_usd(r.whale, r.slug),
                           mapping_ok=bool(ok), mapping_why=why, edge_ok=bool(edge_ok),
                           edge_why=edge_why, cell_ok=clause is None, cell_clause=clause)
    return rules.admission(facts, increase=True)


async def _act(t: _Tick, book: dict, r: _Reading, p: mi.Plan | None, kind: str | None,
               his_px: float | None, plan: dict, cancel_reason: str | None = None) -> str | None:
    """Step X for a live book: keep / cancel-replace the resting
    order, place the rest, or fire the bounded take. `cancel_reason`
    is the increase refusal a BUY plan was refused under; a tick that
    tripped mid-way (t.cancel_all) cancels the resting order under the
    trip's name and places nothing."""
    w = r.whale
    wire = _wire_for(p, his_px, r)
    ent = t.open_by_book.get(book["id"])
    if ent is not None:
        o, st = ent
        leaves = _num(st.get("leaves"))
        if leaves is None:
            leaves = float(o["qty"]) - float(o.get("booked_filled") or 0.0)
        oo = rules.OpenOrder(o["side"], _num(o.get("wire")), int(o["qty"]), leaves,
                       _num(o.get("placed_ts")))
        decision = rules.keep_or_replace(oo, p, t.now, cancel_reason=(t.cancel_all or cancel_reason),
                                         wire=wire)
        if decision == "keep":
            _mirror_stop("open_order_pending", w)
            plan["open_order"] = o["id"]
            # THE BOUNDED TAKE off a rest that has stood the wait. The
            # rest's OWN age is the wait: the arm a post-only 400 set
            # is for the no-rest case below, and every placed rest or
            # finished order clears it (_disarm_take)
            if (p is not None and rules.take_allowed(t.now - float(o["placed_ts"]), None, t.now,
                                                     r.bid, r.ask, wire, p.side)):
                await _cancel_and_settle(t, o, book, "take")
                if book["id"] not in t.open_by_book and o["state"] in ("filled", "cancelled", "expired"):
                    left = int(min(p.qty, max(0.0, float(o["qty"]) - float(o.get("booked_filled") or 0.0))))
                    if left >= 1:
                        return await _place(t, book, r, "take", p.side, wire, left, his_px, p,
                                            plan, tif="IOC")
                return "take"
            if (p is not None and t.now - float(o["placed_ts"]) >= float(rules.MIRROR_TAKE_AFTER_S)
                    and not rules.at_or_through(p.side, r.bid, r.ask, wire)):
                # the wait elapsed and the market never came to him:
                # held under target, never chased (critic C15)
                _mirror_stop("resting_above_level", w)
            return "open_order_pending"
        if decision == "replace":
            try:
                n = int(await t.pool.fetchval(_SQL_REPLACES, book["id"]) or 0)
            except Exception:  # noqa: BLE001 — an unreadable count is the cap
                n = rules.MIRROR_MAX_REPLACES_PER_HOUR
            if n >= rules.MIRROR_MAX_REPLACES_PER_HOUR:
                _mirror_stop("replace_capped", w)
                return "replace_capped"
            await _cancel_and_settle(t, o, book, "replace")
            if book["id"] in t.open_by_book or o["state"] not in ("filled", "cancelled", "expired"):
                return "cancel_pending"
            # re-plan against the ledger the cancel's booking left
            ledger = int(book.get("ledger_net") or 0)
            target = int(plan.get("target") or 0)
            p = mi.plan(target, float(ledger), float(int(r.venue) - r.manual),
                        mi.Book(r.bid, r.ask), his_px, r.mark)
            if p.side is None:
                _mirror_stop(rules.plan_reason_key(p.reason), w)
                return p.reason
            wire = _wire_for(p, his_px, r)
            if p.side == BUY and (kind != "increase" or _increases_refusal(t, w)):
                return "no plan after replace"
        else:
            # a named cancel: the plan is no order, or has no price, or
            # the tick tripped
            await _cancel_and_settle(t, o, book, decision)
            if p is None or p.side is None or t.cancel_all:
                return decision
            if decision == "no_price":
                _mirror_stop("no_price", w)
                return decision
    if p is None or p.side is None:
        return None
    if book["id"] in t.nonterminal:
        # a row of this book is still non-terminal (a lost placement,
        # an unknown cancel): the partial unique index would refuse the
        # INSERT below, and the name is the same
        _mirror_stop("open_order_pending", w)
        return "open_order_pending"
    if wire is None:
        _mirror_stop("no_price", w)
        return "no_price"
    # the take armed by a post-only rejection, with no rest standing
    if (book.get("take_armed_ts") and book["id"] not in t.open_by_book
            and rules.take_allowed(None, book.get("take_armed_ts"), t.now, r.bid, r.ask, wire, p.side)):
        qty = p.qty if p.side == SELL else _room_qty(t, p.qty, wire)
        qty = min(qty, int(book.get("ledger_net") or 0)) if p.side == SELL else qty
        if qty >= 1:
            return await _place(t, book, r, "take", p.side, wire, qty, his_px, p, plan, tif="IOC")
    if p.side == BUY:
        qty = _room_qty(t, p.qty, wire)
        if qty < 1:
            _mirror_stop("over_room", w)
            return "over_room"
        return await _place(t, book, r, "increase", BUY, wire, qty, his_px, p, plan)
    qty = min(int(p.qty), int(book.get("ledger_net") or 0))
    if qty < 1:
        _mirror_stop("under_one_share", w)
        return "under_one_share"
    return await _place(t, book, r, kind or "reduce", SELL, wire, qty, his_px, p, plan)


def _wire_for(p: mi.Plan | None, his_px: float | None, r: _Reading) -> float | None:
    """The cent on the wire from the UNROUNDED facts (addendum section
    10: never plan.price). A BUY joins HIS level: with no level there is
    nothing to join and buy_price says so. A SELL is a reduction of
    what we hold: his equivalent is one of the plan's two candidates
    and the ask alone is the plan's price when he gave none (mi.plan's
    cands) -- the admin flatten and a snapshot-driven reduction have no
    fill of his to price off, and the ask is a read fact, never a guess
    under it."""
    if p is None or p.side is None:
        return None
    if p.side == BUY:
        return rules.buy_price(his_px, r.bid)
    return rules.sell_price(his_px if his_px is not None else r.ask, r.ask)


def _room_qty(t: _Tick, qty: int, wire: float | None) -> int:
    return rules.room_scale(int(qty), wire, le.LIVE_MAX_CLIP_USD, t.day_room, t.total_room,
                            t.mirror_day)


async def _guarded(t: _Tick, row_id: int, fn, *args, **kwargs):
    """The _ioc_guarded shape: the venue call runs shielded so our own
    cancellation cannot lose its result; on cancellation the order id
    is written onto the mirror_orders row when the call completes."""
    fut = asyncio.ensure_future(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await asyncio.shield(fut)
    except asyncio.CancelledError:
        asyncio.ensure_future(_record_orphan(t.pool, row_id, fut))
        raise


async def _record_orphan(pool, row_id: int, fut) -> None:
    try:
        result = await asyncio.wait_for(fut, timeout=120.0)
    except BaseException as exc:  # noqa: BLE001 — includes cancellation
        log.error("mirror_live: venue call on order row %s cancelled mid-call; result lost (%s)",
                  row_id, type(exc).__name__)
        return
    oid = result.get("order_id") if isinstance(result, dict) else None
    if not oid:
        return
    try:
        await pool.execute(_SQL_ORDER_PERSIST_ID, row_id, str(oid),
                           str(result.get("status") or ""), json.dumps(result.get("raw") or {},
                                                                       default=str))
    except Exception:  # noqa: BLE001
        log.error("mirror_live: orphan order %s on row %s could not be persisted", oid, row_id,
                  exc_info=True)


async def _place(t: _Tick, book: dict, r: _Reading, kind: str, side: str, wire: float,
                 qty: int, his_px: float | None, p: mi.Plan | None, plan: dict,
                 tif: str = "GTC") -> str:
    """Step L (and T when tif is IOC): INSERT the 'placing' row with
    the pre-placement snapshot BEFORE the venue call, place, persist
    the id IMMEDIATELY, book what executed on create."""
    global _POST_ONLY_OK
    w, slug = r.whale, r.slug
    if t.cancel_all:
        # a tick that tripped mid-way (an overfill booked by the cancel
        # this placement follows, a wrong sign on another book) places
        # nothing more: the one choke point (step-9 review)
        return t.cancel_all
    if t.ops >= rules.MIRROR_MAX_ORDER_OPS_PER_TICK:
        _mirror_stop("ops_capped", w)
        return "ops_capped"
    orders = await _read_open(t)
    if orders is None:
        return "open_orders_unreadable"
    pre_ids = sorted({str(o.get("order_id")) for o in orders
                      if o.get("order_id") and str(o.get("us_market_slug") or "").lower() == slug.lower()})
    is_take = tif == "IOC"
    post_only = bool(_post_only_enabled()) and not is_take
    good_till = _iso(t.now + float(rules.MIRROR_REST_TTL_S)) if (_gtd_enabled() and not is_take) else None
    tif_rec = "IOC" if is_take else ("GTD" if good_till else "GTC")
    venue_tif = ("TIME_IN_FORCE_IMMEDIATE_OR_CANCEL" if is_take
                 else "TIME_IN_FORCE_GOOD_TILL_CANCEL")
    try:
        row_id = await t.pool.fetchval(
            _SQL_ORDER_INSERT, book["id"], w, slug, kind, side, tif_rec, post_only, good_till,
            his_px, (p.price if p is not None else wire), wire, int(qty), json.dumps(pre_ids),
            int(_num(plan.get("target")) or 0), int(book.get("ledger_net") or 0), r.bid, r.ask,
            kind)
    except Exception as exc:  # noqa: BLE001 — the unique index: one open order per book
        if le._names_constraint(exc, "mirror_orders_one_open_per_book"):
            _mirror_stop("open_order_pending", w)
            return "open_order_pending"
        raise
    t.ops += 1
    o = {"id": int(row_id), "book_id": book["id"], "whale": w, "us_market_slug": slug,
         "kind": kind, "side": side, "tif": tif_rec, "post_only": post_only,
         "his_level": his_px, "price": (p.price if p is not None else wire), "wire": wire,
         "qty": int(qty), "order_id": None, "state": "placing", "filled": 0.0,
         "booked_filled": 0.0, "avg_px": None, "taker_at_placement": False,
         "pre_ids": pre_ids, "placed_ts": t.now, "reason": kind}
    sell = side == SELL
    est = float(qty) * float(wire) if side == BUY else 0.0
    if est:
        le._REST_RESERVED_USD = float(le._REST_RESERVED_USD or 0.0) + est
    try:
        try:
            resp = await _guarded(t, o["id"], t.pmus.submit_fok, slug, wire, int(qty), sell,
                                  venue_tif, ORDER_INTENT, post_only, good_till)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the RESPONSE is lost, not the order
            return await _lost_response(t, o, book, r, exc)
    finally:
        if est:
            le._REST_RESERVED_USD = max(0.0, float(le._REST_RESERVED_USD or 0.0) - est)
    resp = resp if isinstance(resp, dict) else {}
    status = str(resp.get("status") or "")
    oid = resp.get("order_id")
    raw = resp.get("raw") or {}
    if status == "post_only_rejected":
        code = (raw or {}).get("status_code")
        await t.pool.execute(_SQL_ORDER_STATE, o["id"], "rejected", status, "post_only_rejected",
                             None, None)
        await t.pool.execute(_SQL_ORDER_REASON, o["id"], f"post_only_rejected:{code}")
        _mirror_stop("post_only_rejected", w)
        if rules.take_arms(code):
            await t.pool.execute(_SQL_BOOK_ARM, book["id"], True)
            book["take_armed_ts"] = t.now
        if code == 429 or "429" in str((raw or {}).get("error") or ""):
            _mirror_stop("rate_limited", w)
            _abandon(t, "rate_limited")
        _recent(book["id"], "post_only_rejected", code=code)
        return "post_only_rejected"
    if not oid:
        await t.pool.execute(_SQL_ORDER_STATE, o["id"], "rejected", status,
                             f"place_refused:{status or 'no_id'}", None, None)
        _mirror_stop(f"place_refused:{status or 'no_id'}", w)
        if "429" in json.dumps(raw, default=str):
            _mirror_stop("rate_limited", w)
            _abandon(t, "rate_limited")
        return f"place_refused:{status}"
    # PERSIST THE ID BEFORE ANYTHING ELSE: the un-orphanable line
    await t.pool.execute(_SQL_ORDER_PERSIST_ID, o["id"], str(oid), status,
                         json.dumps(raw, default=str))
    o.update(order_id=str(oid), state="open")
    await t.pool.execute(_SQL_BOOK_OPEN_ORDER, book["id"], o["id"])
    book["open_order_id"] = o["id"]
    t.nonterminal.add(book["id"])
    if is_take:
        _mirror_stop("take_placed", w)
        t.stats["placed_take"] += 1
    else:
        _mirror_stop("flatten_rested" if kind in ("flatten_paired", "flatten_vanished")
                     else "rest_placed", w)
        t.stats["placed_rest"] += 1
    # the take's arm is consumed by the IOC and contradicted by a rest
    # the venue accepted (the book was not crossing after all)
    await _disarm_take(t, book)
    if side == BUY:
        # this tick's own placements come off this tick's room readings,
        # so two books in one tick cannot each spend the last clip
        for attr in ("day_room", "total_room", "mirror_day"):
            v = getattr(t, attr)
            if v is not None:
                setattr(t, attr, v - est)
    _recent(book["id"], "placed", kind=kind, side=side, wire=wire, qty=int(qty), tif=tif_rec)
    filled = float(_num(resp.get("filled_shares")) or 0.0)
    if filled > 0:
        st = {"state": status, "filled_shares": filled, "avg_px": resp.get("fill_price")}
        await _book_delta(t, o, book, st, maker=False, taker_at_placement=True)
        o["taker_at_placement"] = True
        if post_only:
            _mirror_stop("post_only_ignored", w)
            _POST_ONLY_OK = False
            t.stats["status"] = "degraded"
            t.stats["post_only"] = False
            log.error("mirror_live: the venue IGNORED post-only on order %s (%s filled at "
                      "create); the flag is off for this process", oid, filled)
    if is_take or filled >= float(qty) - FLAT_TOL_SHARES or le._rest_terminal(
            {"state": status} if status else None):
        st = {"state": status or ("filled" if filled >= qty else "cancelled"),
              "filled_shares": filled, "avg_px": resp.get("fill_price")}
        await _finish_order(t, o, book, st, kind)
        return "take" if is_take else "filled_at_create"
    t.open_by_book[book["id"]] = (o, {"state": status, "filled_shares": filled,
                                      "leaves": float(qty) - filled})
    return "rest_placed"


async def _lost_response(t: _Tick, o: dict, book: dict, r: _Reading, exc: BaseException) -> str:
    """The placement raised: search the book by fingerprint with pre_ids,
    the protected set and every ledger id excluded; found -> adopt; not
    found -> the row stays 'placing' with no id and the book is frozen
    'placement_lost' for step O to revisit."""
    log.warning("mirror_live: placement on %s raised %s; searching the book", r.slug,
                type(exc).__name__)
    if o.get("tif") == "CLOSE":
        # a close carries no cent and no quantity to search by: the
        # next tick reads the venue's position (_reconcile_lost_close)
        await t.pool.execute(_SQL_BOOK_OPEN_ORDER, book["id"], o["id"])
        t.nonterminal.add(book["id"])
        await _freeze(t, book, "placement_lost", {"raised": type(exc).__name__, "close": True})
        return "placement_lost"
    try:
        # a venue READ, behind the pacer like every other (step-9 review)
        orders = list(await asyncio.to_thread(_paced, t.pmus.open_orders, [r.slug]) or [])
    except Exception:  # noqa: BLE001 — unreadable is not "not found"
        orders = None
    if orders is not None:
        verdict, what = await _find_lost_placement(
            t, o, book, orders, (t.now - le._ORPHAN_SKEW_S, t.now + le._ORPHAN_MATCH_S))
        if verdict == "found":
            oid = str(what.get("order_id"))
            await t.pool.execute(_SQL_ORDER_ADOPT, o["id"], oid, "adopted after a lost response")
            o.update(order_id=oid, state="open")
            await t.pool.execute(_SQL_BOOK_OPEN_ORDER, book["id"], o["id"])
            book["open_order_id"] = o["id"]
            t.nonterminal.add(book["id"])
            _recent(book["id"], "adopted", order=oid)
            t.open_by_book[book["id"]] = (o, {"state": what.get("state"),
                                              "filled_shares": what.get("filled_shares"),
                                              "leaves": what.get("leaves")})
            _mirror_stop("rest_placed", r.whale)
            t.stats["placed_rest"] += 1
            await _disarm_take(t, book)
            return "rest_placed"
        if verdict == "ambiguous":
            await _freeze(t, book, "lost_ambiguous", {"candidates": what})
            return "lost_ambiguous"
    await t.pool.execute(_SQL_BOOK_OPEN_ORDER, book["id"], o["id"])
    t.nonterminal.add(book["id"])
    await _freeze(t, book, "placement_lost", {"raised": type(exc).__name__})
    return "placement_lost"


async def _flatten_vanished(t: _Tick, book: dict, r: _Reading, p: mi.Plan, his_px, plan: dict) -> str:
    """Step F: he has LEFT the market (or the admin forced it). Rest
    the SELL at his equivalent for rules.MIRROR_FLATTEN_REST_S first; then
    mirror_exit's rules verbatim: sole holder -> close_position with
    EXIT_SLIPPAGE_BIPS; co-held -> one IOC at sell_limit_price(bid),
    refused by name when the bid is unreadable."""
    w = r.whale
    _mirror_stop("flatten_vanished", w)
    plan["flatten"] = "vanished"
    ledger = int(book.get("ledger_net") or 0)
    if ledger < 1:
        return "flatten_vanished: flat"
    # the reference rest is one of THIS vanish (the plan's vanish_since,
    # set by _tick_book): with none, the vanish begins now and rests
    vanish_since = _num(plan.get("vanish_since"))
    vanish_since = t.now if vanish_since is None else vanish_since
    try:
        since = _num(await t.pool.fetchval(_SQL_FLATTEN_REST_SINCE, book["id"], vanish_since))
    except Exception:  # noqa: BLE001 — unreadable: rest again, never slip
        since = t.now
    if since is None or t.now - since < float(rules.MIRROR_FLATTEN_REST_S):
        return await _act(t, book, r, p, "flatten_vanished", his_px, plan) or "flatten_rested"
    # the rest stood its wait: cancel it, then the slippage path
    await _cancel_open_for(t, book, "flatten_vanished")
    if book["id"] in t.open_by_book:
        return "cancel_pending"
    if t.cancel_all:
        return t.cancel_all          # the cancel's booking tripped the tick: nothing more
    if t.ops >= rules.MIRROR_MAX_ORDER_OPS_PER_TICK:
        _mirror_stop("ops_capped", w)
        return "ops_capped"
    ledger = int(book.get("ledger_net") or 0)
    if ledger < 1:
        return "flatten_vanished: flat"
    try:
        held, _avg = await le._pm_held(r.slug)
    except Exception as exc:  # noqa: BLE001 — cannot size: refuse, retry next tick
        _mirror_stop("no_bid_for_flatten", w)
        log.warning("mirror_live: venue position for %s unreadable (%s)", r.slug, type(exc).__name__)
        return "no_bid_for_flatten"
    sole = ledger >= int(held)
    if not sole:
        # a venue READ, behind the pacer like every other (step-9 review)
        bid = await asyncio.to_thread(_paced, t.pmus.slug_bid, r.slug, True)
        if bid is None or not (0.0 < float(bid) < 1.0):
            _mirror_stop("no_bid_for_flatten", w)
            return "no_bid_for_flatten"
        limit = le.sell_limit_price(float(bid))
    orders = await _read_open(t)
    pre_ids = sorted({str(o.get("order_id")) for o in (orders or [])
                      if o.get("order_id") and str(o.get("us_market_slug") or "").lower() == r.slug.lower()})
    tif_rec = "CLOSE" if sole else "IOC"
    if book["id"] in t.nonterminal:
        _mirror_stop("open_order_pending", w)
        return "open_order_pending"
    try:
        row_id = await t.pool.fetchval(
            _SQL_ORDER_INSERT, book["id"], w, r.slug, "flatten_vanished", SELL, tif_rec, False,
            None, his_px, (None if sole else limit) or 0.0, (None if sole else limit) or 0.0,
            ledger, json.dumps(pre_ids), 0, ledger, r.bid, r.ask, "flatten_vanished")
    except Exception as exc:  # noqa: BLE001 — the unique index: one open order per book
        if le._names_constraint(exc, "mirror_orders_one_open_per_book"):
            _mirror_stop("open_order_pending", w)
            return "open_order_pending"
        raise
    t.ops += 1
    # the in-memory row carries what the INSERT wrote: a CLOSE has no
    # wire and the column holds 0.0, never None (step-9 review: a None
    # here was a TypeError in the lost-placement search)
    o = {"id": int(row_id), "book_id": book["id"], "whale": w, "us_market_slug": r.slug,
         "kind": "flatten_vanished", "side": SELL, "tif": tif_rec, "post_only": False,
         "his_level": his_px, "price": 0.0 if sole else limit, "wire": 0.0 if sole else limit,
         "qty": ledger, "order_id": None, "state": "placing", "filled": 0.0, "booked_filled": 0.0,
         "avg_px": None, "taker_at_placement": True, "pre_ids": pre_ids, "placed_ts": t.now,
         "reason": "flatten_vanished"}
    try:
        if sole:
            resp = await _guarded(t, o["id"], t.pmus.close_position, r.slug,
                                  slippage_bips=le.EXIT_SLIPPAGE_BIPS)
        else:
            resp = await _guarded(t, o["id"], t.pmus.submit_fok, r.slug, limit, ledger, True,
                                  "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL", ORDER_INTENT)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        return await _lost_response(t, o, book, r, exc)
    resp = resp if isinstance(resp, dict) else {}
    oid = resp.get("order_id")
    status = str(resp.get("status") or "")
    filled = float(_num(resp.get("filled_shares")) or 0.0)
    if oid:
        await t.pool.execute(_SQL_ORDER_PERSIST_ID, o["id"], str(oid), status,
                             json.dumps(resp.get("raw") or {}, default=str))
        o.update(order_id=str(oid), state="open")
    if filled > 0:
        # booked = min(filled, ledger) is the primitive's own ceiling
        await _book_delta(t, o, book, {"state": status, "filled_shares": filled,
                                       "avg_px": resp.get("fill_price")}, maker=False,
                          taker_at_placement=True)
    st = {"state": status or ("filled" if filled >= ledger else "cancelled"),
          "filled_shares": filled, "avg_px": resp.get("fill_price")}
    if not oid:
        if sole and status == "close_failed":
            # the adapter turns an exception INSIDE the close call into
            # this status (pmus.close_position); a request that timed
            # out may have executed at the venue, so it is the lost
            # response of the CLOSE row, never a refusal to retry
            return await _lost_response(t, o, book, r, RuntimeError(
                str(((resp.get("raw") or {}).get("error")) or "close_failed")[:80]))
        await t.pool.execute(_SQL_ORDER_STATE, o["id"], "rejected", status,
                             f"place_refused:{status or 'no_id'}", None, None)
        _mirror_stop(f"place_refused:{status or 'no_id'}", w)
        return f"place_refused:{status}"
    await _finish_order(t, o, book, st, "flatten_vanished")
    _recent(book["id"], "flattened", how="close_position" if sole else "ioc", filled=filled)
    return "flatten_vanished"


# ----------------------------------------------------- step A: admission

async def _tick_candidate(t: _Tick, whale: str, cid: str) -> None:
    """A market with no book: read everything admission wants, refuse
    by the first name, else open the book and plan it this tick."""
    w = whale
    fills = await ms.his_fills(t.pool, w, cid)
    m = await ms.map_market(t.pool, fills)
    if not m:
        _mirror_stop("unmapped", w)
        _unmapped_until[(w, cid)] = t.now + ms.UNMAPPED_TTL_S
        return
    slug, la, oa = m["us_slug"], m["long_asset"], m.get("other_asset")
    if not la or (w, cid) in t.books_seen:
        return
    if not oa:
        # his fills never touched the other outcome: the market names
        # it, and the book must know both tokens (the underdog referee
        # reads either, mirror_exit's _mirror_owns_asset reads either)
        try:
            oa = await t.pool.fetchval(_SQL_SIBLING_TOKEN, cid, la)
        except Exception:  # noqa: BLE001 — unknown sibling: the referees read the long token
            oa = None
        oa = str(oa) if oa else None
    r = await _read_market(t, w, cid, slug, la, oa, fills)
    if t.abandoned:
        return
    drift = rules.drift_rule(r.his_long, r.snap_long, r.fresh_read, r.snap_partial,
                             last_fresh_agreed=True)
    net, _snap_net = _net_for(r, drift)
    ratio = (t.ratios.get(w) or {}).get("ratio")
    anchor = (t.ratios.get(w) or {}).get("anchor_usd")
    clip = le.per_fill_usd(w, slug)
    tg = rules.mirror_target(ratio, net, r.mark, clip, cap_usd=rules.MIRROR_NET_CAP_USD)
    if tg.get("refusal"):
        _mirror_stop(tg["refusal"], w)
        return
    target = int(tg["target"])
    if target <= 0:
        return                       # nothing to hold: no book
    his_px = _his_level(fills, la, oa, reducing=False)
    if his_px is None or not (0.0 < his_px < 1.0):
        _mirror_stop("no_price", w)
        return
    mk = r.market
    ok, why = await le._mapping_admitted(t.pool, w, m.get("source"), slug)
    edge_ok, edge_why = edge_gate.verdict(w)
    his_slug = next((f.get("market_slug") for f in fills if f.get("market_slug")), None)
    clause = copy_sports.copy_verdict(w, str(his_slug or ""), price=his_px)
    game_key = le._us_game_key(slug)
    legacy = slug_recent = underdog = kalshi = None
    try:
        legacy = bool(await t.pool.fetchval(_SQL_LEGACY_ROW, la, slug, game_key,
                                            f"%{game_key}%" if game_key else "%"))
        slug_recent = bool(await t.pool.fetchval(_SQL_SLUG_RECENT, slug))
        underdog = bool(await t.pool.fetchval(_SQL_UNDERDOG, [a for a in (la, oa) if a]))
    except Exception as exc:  # noqa: BLE001 — unreadable referees refuse by name
        log.warning("mirror_live: admission referees for %s unreadable (%s)", slug, type(exc).__name__)
    try:
        kalshi = bool(await t.pool.fetchval(_SQL_KALSHI, la))
    except Exception:  # noqa: BLE001 — unreadable: claimed
        kalshi = None
    # the executor's own band (live_executor reads the same env with
    # the same default at its side check), parsed the one way a string
    # from the environment is parsed on this path
    band = rules._env_float("LIVE_SIDE_PRICE_BAND")
    band = 0.15 if band is None else band
    side_band_hit = None
    if r.ask is not None:
        side_band_hit = abs(float(r.ask) - float(his_px)) > band
    books_live = opened_today = None
    try:
        row = await t.pool.fetchrow(_SQL_BOOKS_COUNT)
        if row:
            books_live, opened_today = int(row["live"] or 0), int(row["today"] or 0)
    except Exception:  # noqa: BLE001
        pass
    se, err = await _state(t.pool, _STATE_SIDE_ECHO)
    first_fill_ok = None
    if err is None and isinstance(se, dict):
        try:
            first_fill_ok = int(se.get("ok", 0)) >= 1
        except (TypeError, ValueError):
            first_fill_ok = None
    elif err is None and se is None:
        first_fill_ok = False
    facts = rules.AdmissionFacts(
        increases_ok=_increases_refusal(t, w) is None,
        increases_refusal=_increases_refusal(t, w) or "mode_env_off",
        per_fill_usd=clip, family=copy_sports.market_type_of(slug),
        per_side=bool(m.get("per_side", False)),
        market_closed=(None if mk is None else mk["closed"]),
        market_resolved=(None if mk is None else mk["resolved"]),
        game_too_far_out=le._game_too_far_out(slug),
        mapping_ok=bool(ok), mapping_why=why, edge_ok=bool(edge_ok), edge_why=edge_why,
        cell_ok=clause is None, cell_clause=clause, legacy_row=legacy,
        slug_recent_copy=slug_recent, underdog_coholds=underdog,
        venue_net=(None if t.positions is None else r.venue), kalshi_claimed=kalshi,
        side_band_hit=side_band_hit, snap_fresh=r.fresh, drift=drift.drift,
        books_live=books_live, opened_today=opened_today, first_fill_ok=first_fill_ok)
    refusal = rules.admission(facts)
    if refusal:
        _mirror_stop(refusal, w)
        return
    opened = await le._open_mirror_book(t.pool, w, cid, slug, la, oa, tg["ratio_eff"], anchor,
                                        his_px, target, m.get("source"), game_key)
    if not opened.get("ok"):
        _mirror_stop(str(opened.get("refusal") or "open_failed"), w)
        return
    book = await t.pool.fetchrow(_SQL_BOOK_READ, opened["book_id"])
    if not book:
        return
    book = dict(book)
    if int(book.get("episode") or 1) > 1:
        await t.pool.execute(_SQL_BOOK_REOPENS, book["id"], int(book["episode"]) - 1)
    _recent(book["id"], "opened", whale=w, slug=slug, target=target, ratio=tg["ratio_eff"])
    log.info("mirror_live: book %s opened for %s on %s (target %s @ %s)", book["id"], w, slug,
             target, his_px)
    async with _lock_for(book["id"]):
        await _tick_book(t, book)


# -------------------------------------------------------------- tick_once

def _woken_first(rows: list, woken: list) -> list:
    if not woken:
        return rows
    first = [r for r in rows if str(r.get("condition_id")) in woken]
    rest = [r for r in rows if str(r.get("condition_id")) not in woken]
    return first + rest


async def tick_once(pool, pmus, http, now_ts: float | None = None) -> dict:
    """One reconciler pass. Returns the census the heartbeat carries;
    every counter is present whatever the tick did."""
    global _current_stats, _last_tick_at
    now = time.time() if now_ts is None else float(now_ts)
    stats = _new_stats()
    if _TICK_LOCK.locked():
        stats.update(status="overlap", skipped_overlap=True)
        return stats
    async with _TICK_LOCK:
        _current_stats = stats
        t = _Tick(pool=pool, pmus=pmus, http=http, now=now, stats=stats)
        woken = sorted(_WOKEN)
        _WOKEN.clear()
        _WAKE.clear()
        stats["woken"] = woken
        try:
            await _tick(t, woken)
        finally:
            _last_tick_at = now
            stats["ops"], stats["reads"] = t.ops, t.reads
            stats["recent"] = list(_RECENT)[-20:]
            stats["post_only"] = _POST_ONLY_OK
            _current_stats = None
    return stats


async def _tick(t: _Tick, woken: list) -> None:
    stats = t.stats
    if t.now < _backoff_until:
        stats["skipped_backoff"] = True
        return
    # the trading tables: workers never run migrations
    try:
        await t.pool.fetch(_SQL_TABLE_GUARD)
    except Exception as exc:  # noqa: BLE001 — absent or unreadable: refuse, never crash
        _mirror_stop("tables_absent")
        stats.update(status="degraded", tables_absent=type(exc).__name__)
        log.warning("mirror_live: mirror tables unreadable (%s); refusing", type(exc).__name__)
        return
    await _read_mode(t)
    if t.mode != MODE_SAFE and le.active_venue() != "polymarket-us":
        _mirror_stop("no_venue")
        t.mode = MODE_SAFE
        stats["mode"] = MODE_SAFE
        t.cancel_all = "no_venue"
    if t.mode == MODE_SAFE:
        t.cancel_all = t.cancel_all or "mode_env_off"
        await _reconcile_orders(t)
        await _instruments(t)
        return
    await _global_guards(t)
    if t.cancel_all:
        await _reconcile_orders(t)
        await _instruments(t)
        return
    # R: the reads, once. Ratios for the allowlist only: an open book
    # carries its own fixed ratio and never re-reads one
    t.ratios = await ms.refresh_ratios(t.pool, sorted(t.allow)) if t.allow else {}
    t.positions = await ms.account_positions(t.pmus)
    if t.positions is None:
        _mirror_stop("positions_unreadable")
        _abandon(t, "positions_unreadable")
        return
    stats["venue_positions"] = len(t.positions)
    if await _read_open(t) is None:
        _abandon(t, "open_orders_unreadable")
        return
    await edge_gate.refresh(t.pool)
    if await _read_protected(t) is None:
        _abandon(t, "protected_ids_unreadable")
        return
    # O: the orders first
    await _reconcile_orders(t)
    if t.cancel_all:
        # a trip while booking (an overfill): the tick is cancel-only
        # from here -- what step O kept before the trip is cancelled,
        # no book is planned, no candidate opens (spec 1c "tick
        # cancel-only"; step-9 review)
        await _reconcile_orders(t, count=False)
        await _instruments(t)
        return
    # B: the books, woken markets first
    books = [dict(b) for b in await t.pool.fetch(_SQL_BOOKS_OPEN)]
    for book in _woken_first(books, woken):
        if t.abandoned or t.cancel_all:
            break
        async with _lock_for(book["id"]):
            try:
                await _tick_book(t, book)
            except Exception as exc:  # noqa: BLE001 — one book, not the tick
                _mirror_stop("book_error", book.get("whale"))
                log.exception("mirror_live: book %s failed (%s)", book["id"], type(exc).__name__)
    if t.cancel_all:
        # a wrong-sign trip on a book: "cancel everything" (spec P) --
        # every order the earlier books kept, and no candidate
        await _reconcile_orders(t, count=False)
        await _instruments(t)
        return
    # then new candidates, newest first, for whales that may increase
    for w in sorted(t.allow):
        if t.abandoned:
            break
        refusal = _increases_refusal(t, w)
        if refusal:
            _mirror_stop(refusal, w)         # a whale who may not open a book, by name
            continue
        try:
            conds = await ms.active_conditions(t.pool, w)
        except Exception as exc:  # noqa: BLE001
            log.warning("mirror_live: active markets for %s unreadable (%s)", w, type(exc).__name__)
            continue
        conds = [c for c in conds if c in woken] + [c for c in conds if c not in woken]
        for cid in conds:
            if t.abandoned:
                break
            if t.reads >= ms.MAX_MARKETS_PER_TICK:
                stats["capped_tick"] = True
                break
            if (w, cid) in t.books_seen or _unmapped_until.get((w, cid), 0.0) > t.now:
                continue
            try:
                await _tick_candidate(t, w, cid)
            except Exception as exc:  # noqa: BLE001
                _mirror_stop("book_error", w)
                log.exception("mirror_live: candidate %s/%s failed (%s)", w, cid, type(exc).__name__)
    await _instruments(t)


async def _instruments(t: _Tick) -> None:
    """The reaper-isolation instrument (spec 5): a mirror order id on
    a copy row -- cancelled, adopted or booked by a reaper -- must read
    0. Unreadable is reported as such, never as 0."""
    try:
        n = int(await t.pool.fetchval(_SQL_REAPER_TOUCHED) or 0)
    except Exception as exc:  # noqa: BLE001
        t.stats["reaper_touched_mirror"] = None
        t.stats["reaper_touched_error"] = type(exc).__name__
        return
    t.stats["reaper_touched_mirror"] = n
    if n > 0:
        _mirror_stop("reaper_touched_mirror")
        t.stats["status"] = "degraded"


# ------------------------------------------------------------------- main

async def main() -> None:
    """The loop. PMUS_MIRROR=off is a running cancel-only loop, never an
    idle one: a deploy that drops the flag must still cancel what the
    previous process left resting."""
    import httpx

    from .. import pmus

    pool = await get_pool()
    cfg = settings()
    log.info("mirror_live up: PMUS_MIRROR=%s allowlist=%s poll=%ss",
             os.environ.get("PMUS_MIRROR", "off"), sorted(le.mirror_allowlist()), POLL_S)
    async with httpx.AsyncClient(base_url=cfg.data_api_base, timeout=25.0) as http:
        while True:
            try:
                stats = await tick_once(pool, pmus, http)
                try:
                    await heartbeat(SERVICE, str(stats.get("status") or "ok"), stats)
                except Exception:  # noqa: BLE001
                    log.debug("mirror_live: heartbeat failed")
                if stats.get("ops") or stats.get("abandoned"):
                    log.info("mirror_live: %s", {k: v for k, v in stats.items()
                                                 if k not in ("census", "recent")})
            except Exception:  # noqa: BLE001 — the reconciler never dies
                log.exception("mirror_live pass failed")
            try:
                await asyncio.wait_for(_WAKE.wait(), timeout=POLL_S)
            except asyncio.TimeoutError:
                pass
            gap = time.time() - _last_tick_at
            if gap < WAKE_MIN_GAP_S:
                await asyncio.sleep(WAKE_MIN_GAP_S - gap)


__all__ = ["POLL_S", "WAKE_MIN_GAP_S", "SERVICE", "CENSUS_KEYS", "notify", "tick_once",
           "mirror_census_snapshot", "main"]
