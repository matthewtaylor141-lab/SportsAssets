"""Position mirroring, phase P0: the SHADOW (owner order 2026-09-02,
"go for it, let's get this working").

MEASUREMENT ONLY. This worker never places, cancels or touches an
order. For each whale in MIRROR_WHALES (default "rn1") and each market
he traded inside MIRROR_LOOKBACK_H (newest first), every tick it:

  1. derives his position per outcome token from the fills we already
     ingest (BUY adds, SELL subtracts), and reads the exit worker's
     UNPINNED positions read for the same tokens so derived-vs-read
     drift is a number (stamped; a stale read is excluded, not trusted);
  2. maps the market to its Polymarket US slug and finds which of his
     two tokens is the venue's LONG side -- first from our own ledger
     rows on those tokens, else from the premap table (Postgres only,
     no venue call);
  3. reads what we hold there by our ledger (signed, long-token
     shares) and by the venue -- from ONE paced positions walk per tick,
     so a market we do not hold reads 0, not "unreadable" -- and the
     venue's quote for the long side (one BBO call per market, paced);
  4. computes the target = ratio x his_net, where ratio maps his
     median opening burst to the $50 measuring clip (analytics/mirror),
     capped at the mark, long-only in P0;
  5. writes one mirror_shadow row with the plan it WOULD execute (side,
     qty, resting price at his level, would-fill against the book) or
     the reason for none;
  6. records the EXIT LEG (A3/MIRROREXIT) beside that plan: whether he
     REDUCED or LEFT, when, at what price the complement traded, what
     our own leg was at that moment, what the exit rule would have done
     (rest at his equivalent, hold, or nothing) and the reason when it
     is nothing. The verdict on that plan is the row's own would_fill,
     judged on the SELL side over the same TTL as every other plan.

VENUE LOAD IS BOUNDED (review round one): the venue 429'd a board walk
above ~3 req/s and the money path's no-stack referee reads the same
positions endpoint, so every HTTP call here is paced at READ_PACING_S,
the positions walk happens once per tick, at most MAX_MARKETS_PER_TICK
markets are read, a failed walk abandons the tick before any BBO call,
a run of BBO misses abandons it too, and a write failure (table absent
until migration 046) stops the tick rather than spending venue budget
on rows that cannot land. Every abandon backs off BACKOFF_S.

Kill: MIRROR_SHADOW=off (env) or ingestion_state 'mirror_shadow' =
"off" (DB switch, no deploy).

THE CENSUS COLUMNS (to-a-tee Phase 0, owner order 2026-09-02 "I want
us to match everything ... mirror the whales to a tee"): every gate the
later phases read was unreadable from the row as written -- an unmapped
market carried only its two token ids, a mapped one carried no family,
no mapping class, no snapshot state, and his short side (55% of his
mapped markets) was refused before it was measured. So the row now
carries, INSIDE THE JSONB DETAIL and never as a new 046 column, what
those gates need: the unmapped market's slug/title/sport/family/why/
dollars; the mapped market's family, per-side flag, snapshot state,
ledger facts (is our position on the slug a legacy per-fill row, and
what mapping class the ledger row itself carries); a PARALLEL short
reading (target_short and its plan, computed with allow_short=True and
judged on the SELL side against the bid over the same TTL); and, when a
plan is touched, how long it waited and what sat at the best bid/ask.
The live-compared target (the value P1 names shadow_live_disagree on)
is byte-identical to before: the short reading is beside it, never in
it, because a shadow whose target went negative while the long-only
book flattened would refuse P2 on that counter by construction.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
from typing import Any

from ..analytics import mirror as mi
from ..analytics import mirror_live_rules as rules
from ..db import get_pool, heartbeat
from ..venue_pace import pace

log = logging.getLogger(__name__)

# EVERY KNOB BELOW IS DOWNWARD-ONLY, through the rules' own helper.
# These were raw env reads, and one of them is a money bound in
# disguise: SNAP_MAX_AGE_S is the freshness gate on HIS position --
# mirror_live reads it as fresh_read, which becomes the admission fact
# the drift rule's increase clause and the snapshot resolution key on --
# so a shell could open new books and grow live ones on an arbitrarily
# old reading of the whale, and loosen the very gate the rollout is
# steered by, without a deploy or a review. The dollar caps beside it
# have been downward-only since the rules were written; these now match.
# An operator can still make any of them TIGHTER (a shorter freshness
# window, fewer markets, a shorter judge TTL) without a deploy.
# an INTERVAL, not a cap: its aggressive direction is DOWN (more venue
# reads on a key shared with the live lane), so it lengthens only --
# which also keeps the operator's incident lever, slowing the shadow
# during a venue event without a deploy
POLL_S = rules.min_wait_env("MIRROR_SHADOW_POLL_S", 30.0)
LOOKBACK_H = rules.capped_env("MIRROR_LOOKBACK_H", 6.0, floor=0.25)
RATIO_DAYS = int(rules.capped_env("MIRROR_RATIO_DAYS", 30.0, floor=1.0))
RATIO_REFRESH_S = 3600.0
READ_PACING_S = 0.35
MAX_MARKETS_PER_TICK = int(rules.capped_env("MIRROR_MAX_MARKETS", 20.0, floor=0.0))
POSITIONS_PAGES_MAX = 5
MISS_STREAK_ABANDON = 3
BACKOFF_S = 60.0
# a raw positions read older than this is not a reading of his book now
# TWO-SIDED, and the floor is the interesting half. Raising it lets new
# books open on an arbitrarily old reading of him. LOWERING it is not
# the safe direction either: a book whose snapshot reads stale takes
# select_flatten's vanished path, the only path that accepts slippage,
# so a short window would delete the paired-flatten guard for every
# book. The floor is the SNAPSHOT WRITER'S own cadence (whale_exits
# INTERVAL_S, 120 s): under it, every read is stale by construction.
SNAP_MAX_AGE_S = rules.capped_env("MIRROR_SNAP_MAX_AGE_S", 300.0, floor=120.0)
# a plan is a resting order with this life: it fills if the book reaches
# its price inside it, and did not fill if it ages past it while the
# market is still read (the live lane's rest TTL, review of the first
# shadow hour)
JUDGE_TTL_S = rules.capped_env("MIRROR_JUDGE_TTL_S", 600.0, floor=30.0)  # the live rest TTL's own floor
# a market that mapped to no venue market is not re-read every tick: the
# per-tick cap goes to markets that can produce a plan (81% of RN1's
# markets read unmapped in the first hour and took every slot)
UNMAPPED_TTL_S = 900.0
# THE EXIT LEG'S OWN BOUNDS (A3). The census is a READ of rows this
# worker already wrote, so its cost is a query, not venue budget: it is
# bounded three ways -- a window, a row cap and a wall-clock timeout --
# and it runs at most once per EXIT_SUMMARY_S, never per market.
# EXIT_WINDOW_H is the gate's own 24 h; a shorter window is a tighter
# reading, so it is downward-only like every other bound here.
EXIT_WINDOW_H = rules.capped_env("MIRROR_EXIT_WINDOW_H", 24.0, floor=1.0)
# an INTERVAL, like POLL_S: its aggressive direction is DOWN (more
# database reads), so it lengthens only
EXIT_SUMMARY_S = rules.min_wait_env("MIRROR_EXIT_SUMMARY_S", 900.0)
# one row per (whale, market) comes back, so this is a cap on MARKETS,
# not on rows read. A CENSUS THAT HIT ITS CAP IS NOT A READING OF THE
# WINDOW -- the same standard `account_positions` holds the walk to --
# so `exit_census` asks for one row MORE than this and refuses the
# reading when that row comes back. The floor is deliberately far above
# the gate's own minimum n: at floor=30 a shell could pin the census to
# exactly the n the gate reads at, and every day busier than 30 markets
# would then read as refused. 500 is five times the mapped-market count
# the mirror programme records for a 24 h window (§0 of
# docs/mirror-to-a-tee-program.md, recorded there, not measured here).
EXIT_SUMMARY_MAX = int(rules.capped_env("MIRROR_EXIT_SUMMARY_MAX", 2000.0, floor=500.0))
# not a knob: a read that has not answered in this long is unreadable,
# and the tick must not wait on it
EXIT_CENSUS_TIMEOUT_S = 15.0
# §3b: "the line prints at n >= 30". The VALUE is an input to owner
# decision 18; this is the count under which there is no reading at all.
EXIT_MIN_N = 30
_unmapped_until: dict[tuple[str, str], float] = {}
_STATE_RATIO = "mirror_ratio"
_STATE_SWITCH = "mirror_shadow"
_SNAP_RAW_KEY = "whale_positions_raw:%s"
_backoff_until = 0.0
_ratio_cache: dict[str, Any] = {"at": 0.0, "by_whale": {}}
_exit_cache: dict[str, Any] = {"at": 0.0, "value": None}
_sleep = asyncio.sleep          # indirection so a test can count the pacing


def mirror_whales() -> list[str]:
    raw = os.environ.get("MIRROR_WHALES", "rn1")
    return sorted({w.strip().lower() for w in raw.split(",") if w.strip()})


def enabled() -> bool:
    return os.environ.get("MIRROR_SHADOW", "on").strip().lower() not in ("off", "0", "false", "no")


async def _db_switch_off(pool) -> bool:
    try:
        v = await pool.fetchval("SELECT value FROM ingestion_state WHERE key=$1",
                                _STATE_SWITCH)
    except Exception:  # noqa: BLE001 — no switch row is 'on'
        return False
    if v is None:
        return False
    s = v if isinstance(v, str) else json.dumps(v)
    return s.strip().strip('"').lower() in ("off", "0", "false", "no")


# ----------------------------------------------------------------- ratio

async def compute_ratio(pool, whale: str, days: int = RATIO_DAYS) -> dict:
    """His opening burst per market over `days`, and the ratio that maps
    the median burst to the measuring clip. Pure arithmetic lives in
    analytics/mirror; this is the query."""
    rows = await pool.fetch(
        """
        SELECT t.condition_id, t.asset, t.side, t.size::float8 AS size,
               t.price::float8 AS price, extract(epoch FROM t.ts)::float8 AS ts
          FROM trades t JOIN whales w ON w.id = t.whale_id
         WHERE lower(w.username) = $1
           AND t.ts >= now() - make_interval(days => $2)
           AND t.condition_id IS NOT NULL
         ORDER BY t.condition_id, t.ts
        """, whale, int(days))
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(str(r["condition_id"]), []).append(dict(r))
    bursts = [mi.opening_burst(fs) for fs in by.values()]
    out = mi.mirror_ratio(bursts)
    out["whale"] = whale
    out["days"] = int(days)
    out["markets"] = len(by)
    out["at"] = time.time()
    return out


async def refresh_ratios(pool, whales: list[str], force: bool = False) -> dict[str, dict]:
    if not force and time.time() - _ratio_cache["at"] < RATIO_REFRESH_S and _ratio_cache["by_whale"]:
        return _ratio_cache["by_whale"]
    by: dict[str, dict] = {}
    for w in whales:
        try:
            by[w] = await compute_ratio(pool, w)
        except Exception as exc:  # noqa: BLE001 — no ratio = no target, named
            log.warning("mirror_shadow: ratio for %s unreadable (%s)", w, type(exc).__name__)
            by[w] = {"whale": w, "ratio": None, "why": f"unreadable: {type(exc).__name__}"}
    _ratio_cache.update(at=time.time(), by_whale=by)
    try:
        await pool.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            _STATE_RATIO, json.dumps(by, default=str))
    except Exception:  # noqa: BLE001 — the cache is enough for a tick
        log.debug("mirror_shadow: ratio state write failed")
    return by


# ------------------------------------------------------------ his book

async def active_conditions(pool, whale: str, hours: float = LOOKBACK_H) -> list[str]:
    """His markets with activity in the window, NEWEST FIRST, so the
    per-tick cap always reads the market he just moved in."""
    rows = await pool.fetch(
        """
        SELECT t.condition_id, max(t.ts) AS last_ts
          FROM trades t JOIN whales w ON w.id = t.whale_id
         WHERE lower(w.username) = $1 AND t.condition_id IS NOT NULL
           AND t.ts >= now() - ($2::float8 * interval '1 hour')
         GROUP BY t.condition_id
         ORDER BY last_ts DESC
        """, whale, float(hours))
    return [str(r["condition_id"]) for r in rows]


async def his_fills(pool, whale: str, condition_id: str) -> list[dict]:
    """ALL his fills on the condition (his position is cumulative), with
    the trade context the mapper needs. The event title lives on the
    markets table, not on trades (review round two): without it the
    premap lookup builds its keys from one source instead of three and
    misses the markets the copy sleeve never traded -- the very ones
    the mirror exists to add."""
    rows = await pool.fetch(
        """
        SELECT t.id, t.asset, t.side, t.size::float8 AS size, t.price::float8 AS price,
               extract(epoch FROM t.ts)::float8 AS ts,
               COALESCE(t.market_title, m.title) AS market_title, t.event_slug,
               m.event_title, COALESCE(t.market_slug, m.slug) AS market_slug,
               t.outcome, t.outcome_index,
               COALESCE(NULLIF(m.sport, 'unclassified'), NULLIF(t.sport, 'unclassified'),
                        'unclassified') AS sport
          FROM trades t JOIN whales w ON w.id = t.whale_id
          LEFT JOIN markets m ON m.condition_id = t.condition_id
         WHERE lower(w.username) = $1 AND t.condition_id = $2
         ORDER BY t.ts, t.id
        """, whale, condition_id)
    return [dict(r) for r in rows]


async def snapshot_sizes(pool, whale: str) -> tuple[dict[str, float], float | None, bool]:
    """The exit worker's last UNPINNED positions read for the whale
    (token -> shares), its age in seconds, and whether the read was
    PARTIAL (a page walk that did not finish); ({}, None, True) when
    there is none. The pinned baseline it keeps beside it is for exit
    detection and deliberately holds deferred and sub-floor shrinks at
    their old size, so it is not read here."""
    try:
        raw = await pool.fetchval("SELECT value FROM ingestion_state WHERE key=$1",
                                  _SNAP_RAW_KEY % whale)
    except Exception:  # noqa: BLE001
        return {}, None, True
    if not raw:
        return {}, None, True
    try:
        d = raw if isinstance(raw, dict) else json.loads(raw)
        sizes = {str(k): float(v) for k, v in (d.get("sizes") or {}).items()}
        age = max(0.0, time.time() - float(d.get("at") or 0.0)) if d.get("at") else None
        return sizes, age, bool(d.get("partial", True))
    except (TypeError, ValueError, AttributeError):
        return {}, None, True


# -------------------------------------------------------------- mapping

def _choose_long(assets: list[str], cands: dict[str, tuple[str, str]],
                 pos: dict[str, float], source: str) -> dict | None:
    """Which of his two tokens is the venue's LONG side, from what each
    token resolved to (slug, intent). Two shapes exist (review round
    two): the aec tennis family resolves both tokens to ONE identifier
    with LONG/SHORT intents, so a short intent names the other token as
    the long; per-side-identifier markets resolve each token BUY_LONG on
    its OWN slug, so neither intent decides and his directional side --
    the token with the larger fills-derived position -- is the long,
    traded on its slug. Fill or row order never decides."""
    def other_of(a: str) -> str | None:
        return next((x for x in assets if x != a), None)

    longs = [a for a, (_, i) in cands.items() if i == "ORDER_INTENT_BUY_LONG"]
    shorts = [a for a, (_, i) in cands.items() if i == "ORDER_INTENT_BUY_SHORT"]
    if len(longs) == 2:
        if cands[longs[0]][0] == cands[longs[1]][0]:
            return None                 # both long on one slug: ambiguous, refuse
        a = max(longs, key=lambda x: (float(pos.get(x, 0.0)), x))
        return {"us_slug": cands[a][0], "long_asset": a, "other_asset": other_of(a),
                "source": source, "per_side": True}
    if longs:
        a = longs[0]
        return {"us_slug": cands[a][0], "long_asset": a, "other_asset": other_of(a),
                "source": source}
    if shorts:
        a = shorts[0]
        return {"us_slug": cands[a][0], "long_asset": other_of(a), "other_asset": a,
                "source": source}
    return None


async def map_market(pool, fills: list[dict]) -> dict | None:
    """{us_slug, long_asset, other_asset, source[, per_side]} for the
    condition, or None. Our own ledger first: the newest live_orders row
    on EACH of his tokens carries the slug and the intent we actually
    traded on. Else the premap table with the trade's own context
    (Postgres only). The long side is then chosen by shape, never by
    which row or fill came first (_choose_long)."""
    assets = sorted({str(f.get("asset") or "") for f in fills if f.get("asset")})
    if not assets:
        return None
    pos = mi.net_positions(fills)
    from ..live_executor import ORDER_INTENT_SQL

    cands: dict[str, tuple[str, str]] = {}
    try:
        rows = await pool.fetch(
            f"""
            SELECT asset, us_market_slug, {ORDER_INTENT_SQL} AS intent
              FROM live_orders
             WHERE asset = ANY($1::text[]) AND us_market_slug IS NOT NULL
               AND {ORDER_INTENT_SQL} IN ('ORDER_INTENT_BUY_LONG', 'ORDER_INTENT_BUY_SHORT')
             ORDER BY placed_at DESC LIMIT 20
            """, assets)
    except Exception:  # noqa: BLE001
        rows = []
    for r in rows:                      # newest row per token
        cands.setdefault(str(r["asset"]), (str(r["us_market_slug"]), str(r["intent"])))
    if cands:
        return _choose_long(assets, cands, pos, "ledger")
    # premap, per token, with the event title the markets table carries
    try:
        from . import premap as _premap
    except Exception:  # noqa: BLE001
        return None
    by_asset: dict[str, dict] = {}
    for f in fills:
        a = str(f.get("asset") or "")
        if a and a not in by_asset:
            by_asset[a] = f
    for a, f in by_asset.items():
        try:
            m = await _premap.resolve(pool, f.get("market_title"), f.get("event_title"),
                                      f.get("outcome"), f.get("market_slug"))
        except Exception:  # noqa: BLE001
            m = None
        if m and m.get("market_slug") and m.get("intent"):
            cands[a] = (str(m["market_slug"]), str(m["intent"]))
    if not cands:
        return None
    return _choose_long(assets, cands, pos, "premap")


# --------------------------------------------------------------- ours

async def ledger_net(pool, us_slug: str) -> int:
    """Our signed holding by the ledger, in long-token shares: filled or
    exiting rows, long minus short, whole shares. EVERY sleeve counts
    (review round one): the venue's net is account-wide, so a desk or
    underdog row left out here would read as a position the ledger
    cannot explain and freeze the market every tick."""
    from ..live_executor import ORDER_INTENT_SQL

    rows = await pool.fetch(
        f"""
        SELECT filled_shares::float8 AS sh, {ORDER_INTENT_SQL} AS intent
          FROM live_orders
         WHERE us_market_slug = $1 AND status IN ('filled', 'exiting')
        """, us_slug)
    net = 0.0
    for r in rows:
        sh = float(r["sh"] or 0.0)
        net += sh if str(r["intent"]) != "ORDER_INTENT_BUY_SHORT" else -sh
    return round(net, 4)               # fractional fills kept; the plan compares within a share


async def account_positions(pmus) -> dict[str, float] | None:
    """ONE paced walk of the venue account's positions per tick:
    {slug (lower): signed netPosition}. None when the walk failed -- a
    market absent from a successful walk is simply not held (0)."""
    def _walk() -> dict[str, float]:
        client = pmus._get_client()
        out: dict[str, float] = {}
        cursor = ""
        complete = False
        for _ in range(POSITIONS_PAGES_MAX):
            # every page is one venue read through the PROCESS-WIDE
            # measurement pacer (review round two): this worker and
            # price_path together never exceed one read per gap
            pace(READ_PACING_S)
            resp = client.portfolio.positions(
                {"limit": 100, **({"cursor": cursor} if cursor else {})}) or {}
            for slug, p in (resp.get("positions") or {}).items():
                key = slug.strip().lower() if isinstance(slug, str) else ""
                if not key or key in out:
                    # A ROW WE CANNOT NAME IS A ROW WE CANNOT PLACE AGAINST.
                    # Skipping it used to leave the walk claiming to be a
                    # COMPLETE reading of the account while a slug was
                    # missing from it; the caller then reads venue 0 for
                    # that market, the "the venue already holds this"
                    # admission clause passes, and a BUY goes out into a
                    # slug the account already holds. Unreadable row,
                    # unreadable walk -- the same rule the page cap keeps.
                    # A repeated key is the same defect by another route:
                    # last-write-wins can report 0 for a slug that is held.
                    raise RuntimeError("positions walk carries a row we cannot name uniquely")
                try:
                    net = float((p or {}).get("netPosition") or 0.0)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f"positions walk carries an unreadable netPosition for {key}") from exc
                if not math.isfinite(net):
                    # a NaN reaches int() downstream and wedges the book
                    raise RuntimeError(f"positions walk carries a non-finite netPosition for {key}")
                out[key] = net
            cursor = resp.get("nextCursor") or ""
            if resp.get("eof") or not cursor:
                complete = True
                break
        if not complete:
            # A WALK THAT HIT THE PAGE CAP IS NOT A READING OF THE ACCOUNT
            # (P1 design review): a slug on a page we never fetched would
            # read as "not held", which is a venue/ledger disagreement
            # invented by us -- or worse, a position the plan trades
            # against. Truncated is unreadable.
            raise RuntimeError(f"positions walk truncated at {POSITIONS_PAGES_MAX} pages")
        return out

    try:
        return await asyncio.to_thread(_walk)
    except Exception as exc:  # noqa: BLE001 — a failed walk is named, never guessed
        # the message names WHICH row or WHICH cap refused: three raise
        # sites all carry RuntimeError, and a walk that fails every tick
        # on one stuck row is otherwise indistinguishable from a 429
        log.warning("mirror_shadow: positions walk failed (%s: %s)",
                    type(exc).__name__, str(exc)[:200])
        return None


def _paced_bbo(pmus, slug: str) -> tuple[float | None, float | None]:
    """One BBO read behind the process-wide measurement pacer."""
    pace(READ_PACING_S)
    return pmus._bbo_quotes(pmus._get_client(), slug)


def _px(f: dict) -> float | None:
    try:
        p = float(f.get("price"))
    except (TypeError, ValueError):
        return None
    return p if 0.0 < p < 1.0 else None


def his_level(fills: list[dict], long_asset: str | None, other_asset: str | None,
              reducing: bool) -> float | None:
    """The price of HIS most recent move in the direction we are about
    to follow, in long-token terms. Increasing: his last BUY of the long
    token. Reducing: the most recent of his SELL of the long token (at
    its price) and his BUY of the other token (his pair completion, at
    one minus its price) -- by timestamp, so a sale after an old entry
    is not priced off the entry (review round one)."""
    best: tuple[float, float] | None = None
    for f in fills:
        a, side = str(f.get("asset") or ""), str(f.get("side") or "").upper()
        try:
            ts = float(f.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        p = _px(f)
        if p is None:
            continue
        lvl = None
        if not reducing:
            if long_asset and a == long_asset and side == "BUY":
                lvl = p
        else:
            if long_asset and a == long_asset and side == "SELL":
                lvl = p
            elif other_asset and a == other_asset and side == "BUY":
                lvl = round(1.0 - p, 4)
        if lvl is not None and (best is None or ts >= best[0]):
            best = (ts, lvl)
    return best[1] if best else None


# ------------------------------------------------- census helpers (Phase 0)

def notional_in_window(fills: list[dict], hours: float, now_ts: float | None = None) -> float:
    """Dollars of his BUYS on the market inside the last `hours`: the
    unmapped row's dollar weight, so the coverage gate can be read by
    his money and not by a count of $25 markets (coverage review: 86.8%
    of his stake sits in lots of $250 and over)."""
    now_ts = time.time() if now_ts is None else float(now_ts)
    lo = now_ts - float(hours) * 3600.0
    total = 0.0
    for f in fills:
        if str(f.get("side") or "").upper() != "BUY":
            continue
        try:
            ts = float(f.get("ts") or 0.0)
            n = float(f.get("size") or 0.0) * float(f.get("price") or 0.0)
        except (TypeError, ValueError):
            continue
        if ts >= lo and n > 0:
            total += n
    return round(total, 2)


def fills_since(fills: list[dict], age_s: float | None, now_ts: float | None = None) -> int | None:
    """How many of his fills landed AFTER the positions snapshot was
    taken. Fills-derived and snapshot-derived positions disagree for two
    different reasons -- an ingest miss and plain lag -- and only this
    count separates them (a book built at 1,098 sh/min lags thousands
    of shares inside a 300 s snapshot age). None without a snapshot."""
    if age_s is None:
        return None
    now_ts = time.time() if now_ts is None else float(now_ts)
    cut = now_ts - float(age_s)
    n = 0
    for f in fills:
        try:
            if float(f.get("ts") or 0.0) > cut:
                n += 1
        except (TypeError, ValueError):
            continue
    return n


def outcome_null_count(fills: list[dict]) -> int:
    """His fills on the market whose outcome is still NULL (chain rows
    are inserted with outcome NULL and enriched later; the premap side
    match refuses an empty outcome, so this is a named mapping miss and
    not a venue gap)."""
    return sum(1 for f in fills if not str(f.get("outcome") or "").strip())


def _first_context(fills: list[dict]) -> dict:
    """The market context the mapper reads: the first fill that carries
    a title, else the first fill."""
    ctx = next((f for f in fills if f.get("market_title")), fills[0] if fills else {})
    slug = str(ctx.get("market_slug") or "")
    sport = str(ctx.get("sport") or "").strip()
    if not sport or sport == "unclassified":
        try:
            from ..copy_sports import sport_of
            sport = sport_of(slug) or "unclassified"
        except Exception:  # noqa: BLE001
            sport = "unclassified"
    return {"his_slug": slug or None, "title": ctx.get("market_title"),
            "event_title": ctx.get("event_title"), "event_slug": ctx.get("event_slug"),
            "outcome": ctx.get("outcome"), "sport": sport}


def _family_of(slug: str | None) -> str:
    try:
        from ..copy_sports import market_type_of
        return market_type_of(slug or "")
    except Exception:  # noqa: BLE001
        return "unknown"


async def explain_unmapped(pool, ctx: dict) -> str:
    """WHY premap said no, as the resolver's own step name (the same
    read-only resolve_explain the copy lane's unmapped census uses), or
    a named failure -- never a guess."""
    try:
        from . import premap as _premap
    except Exception:  # noqa: BLE001
        return "explain_unavailable"
    try:
        ex = await _premap.resolve_explain(pool, ctx.get("title"), ctx.get("event_title"),
                                           ctx.get("outcome"), ctx.get("his_slug"))
    except Exception as exc:  # noqa: BLE001 — one market's why, named
        return f"explain_raised:{type(exc).__name__}"
    return str((ex or {}).get("step") or "unknown")


# THE LIVE ROWS ARE NEVER TRUNCATED (Phase 0 review of the instruments,
# major 1; owner order 2026-09-02 "mirror the whales to a tee"): the copy
# lane inserts one live_orders row per whale trade and the quarantine
# UPDATEs each to 'rejected', so on a slug where a per-fill row FILLED
# on one token and he then traded the other token twenty-odd times, the
# twenty newest rows are all rejected and a plain newest-20 read drops
# the filled row -- ledger_legacy read a confident False where P1's own
# referee (an EXISTS over filled/submitting/exiting, no LIMIT) reads
# True, and the plan was counted in the non-legacy rate it must not
# enter. So the filled/exiting rows are read whole, and the newest-20
# cap applies only to the rest (the rows the mapping class is read
# from). A slug's live rows are bounded by the lane's own caps, so the
# whole read is small; the newest-20 keeps the class read bounded.
_SQL_LEDGER_FACTS = """
SELECT status, lane, error, whale_username
  FROM live_orders
 WHERE us_market_slug = $1
   AND (status IN ('filled', 'exiting')
        OR id IN (SELECT id FROM live_orders
                   WHERE us_market_slug = $1
                     AND status IN ('filled', 'exiting', 'settled', 'cashed_out', 'merged',
                                    'submitting', 'open', 'rejected')
                   ORDER BY placed_at DESC LIMIT 20))
 ORDER BY placed_at DESC /* ledger-facts */
"""


async def ledger_rows(pool, us_slug: str) -> list[dict] | None:
    """Every live_orders row on the venue slug that could explain what
    the ledger holds or where its mapping came from: ALL of its
    filled/exiting rows and the newest twenty of every status (see
    _SQL_LEDGER_FACTS for why the live rows sit outside the cap); None
    when the read failed (unreadable is named, never 'no rows')."""
    try:
        rows = await pool.fetch(_SQL_LEDGER_FACTS, us_slug)
    except Exception:  # noqa: BLE001
        return None
    return [dict(r) for r in rows]


# the mapping class a refused row records in its error text ('(src=fuzzy,
# slug=...)'); compiled once with the module (Phase 0 review, minor 5)
_SRC_RE = re.compile(r"\(src=([a-z_]+),")


def ledger_facts(rows: list[dict] | None) -> dict:
    """Two readings off the slug's ledger rows. `legacy`: a NON-mirror
    row is live on the slug (filled/exiting), so any plan the shadow
    makes there is against a per-fill position P1 refuses by name
    (legacy_row) -- those plans must not count toward the would-fill
    rate P1 is gated on. `map_class`: the mapping class the copy lane's
    own row carries, which is the only class a ledger-sourced mirror map
    can claim; a refused row names it in its error text ('(src=fuzzy,
    slug=...)'), a traded row never recorded it. Fail closed: None rows
    read as unreadable on both counts."""
    if rows is None:
        return {"legacy": None, "map_class": "unreadable"}
    legacy = False
    traded_lane = None
    src = None
    for r in rows:
        status = str(r.get("status") or "")
        lane = str(r.get("lane") or "") or "-"
        if status in ("filled", "exiting") and lane != "mirror":
            legacy = True
        if status in ("filled", "exiting", "settled", "cashed_out", "merged") and traded_lane is None:
            traded_lane = lane
        if src is None:
            m = _SRC_RE.search(str(r.get("error") or ""))
            if m:
                src = m.group(1)
    if src:
        cls = f"refused:{src}"
    elif traded_lane is not None:
        cls = f"traded:{traded_lane}"
    elif rows:
        cls = "unrecorded"
    else:
        cls = "no_rows"
    return {"legacy": legacy, "map_class": cls}


def _book_depth(client, slug: str) -> dict | None:
    """The best level of each side of the venue book -- price and
    resting size -- from one `markets.book` read; None when unreadable.
    The BBO feed the plan is judged on carries prices only, so the size
    that sat ahead of a resting order at the touch (the queue, the one
    residual the shadow's touch rate cannot see) is read here, once per
    touch, never per tick."""
    try:
        raw = client.markets.book(slug) or {}
    except Exception:  # noqa: BLE001
        return None
    body = raw if isinstance(raw, dict) else {}
    for key in ("marketData", "book"):
        if isinstance(body.get(key), dict):
            body = body[key]

    def _levels(items) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for lvl in items or []:
            try:
                px = lvl.get("px")
                p = float(px.get("value") if isinstance(px, dict) else px)
                q = float(lvl.get("qty") or 0.0)
            except (TypeError, ValueError, AttributeError):
                continue
            if 0.0 < p < 1.0 and q > 0:
                out.append((p, q))
        return out

    bids = _levels(body.get("bids"))
    asks = _levels(body.get("offers") or body.get("asks"))
    if not bids and not asks:
        return None
    out: dict[str, Any] = {}
    if bids:
        p, q = max(bids)
        out.update(bid=p, bid_qty=q)
    if asks:
        p, q = min(asks)
        out.update(ask=p, ask_qty=q)
    return out


def _paced_depth(pmus, slug: str) -> dict | None:
    """One paced book-depth read (the same process-wide gate every venue
    read here goes through)."""
    pace(READ_PACING_S)
    try:
        client = pmus._get_client()
    except Exception:  # noqa: BLE001
        return None
    return _book_depth(client, slug)


def short_reading(ratio: float, net: float, mark: float, ledger: float,
                  venue: float | None, book: "mi.Book", fills: list[dict],
                  long_asset: str | None, other_asset: str | None) -> dict:
    """THE PARALLEL SHORT READING. The same arithmetic as the live-
    compared plan with allow_short=True: a negative net becomes a
    negative target capped at the short leg's mark, and the plan from
    the ledger toward it is a SELL of the long token at his equivalent
    (one minus the price he paid for the other token) or better -- the
    shape the executor's BUY_SHORT wire already sends (373 sign-verified
    fills, 0 mismatch). Written beside the long-only target, never in
    its place, so P1's shadow_live_disagree keeps comparing like with
    like while P2's gate (would_fill_short over 30 markets) is measured."""
    tgt = mi.target_shares(ratio, net, mark, allow_short=True)
    reducing = int(tgt["target"]) <= int(ledger)
    his_px = his_level(fills, long_asset, other_asset, reducing)
    p = mi.plan(int(tgt["target"]), float(ledger), venue, book, his_px, mark)
    return {"target_short": int(tgt["target"]), "target_raw_short": tgt["raw"],
            "capped_short": bool(tgt["capped"]), "his_px_short": his_px,
            "would_side_short": p.side, "would_qty_short": int(p.qty),
            "would_px_short": p.price, "would_fill_short": None,
            "reason_short": p.reason,
            "marketable_now_short": (bool(p.would_fill) if p.side and p.price is not None
                                     else None)}


# ------------------------------------------------ the exit leg (A3)
#
# THE HALF OF THE METHODOLOGY WITH NO EVIDENCE BEHIND IT. The shadow
# judges what it would have done on ENTRIES; it recorded nothing about
# EXITS, and the exit is the half that decides a market maker's day.
# The owner's reading of the whale -- "he never sells" -- is right about
# the wire and wrong about the position: he exits by BUYING THE
# COMPLEMENT, so on a netting venue his exit arrives as a BUY and only
# his NET says he left. A rule keyed on his SELLs would see almost
# nothing -- the programme's decision 18 records how few sells exist
# in his whole history (docs/mirror-to-a-tee-program.md; a reading
# taken there, not re-measured here) -- while a rule keyed on his
# NET sees every exit he has ever made.
#
# Nothing here arms anything and nothing here re-plans. The exit leg is
# a LABEL on the plan the row already carries plus the evidence behind
# it, so the live-compared columns stay byte-identical and the verdict
# is the row's own `would_fill` -- judged on the SELL side, against the
# book we actually read, over the same JUDGE_TTL_S the BUY judge uses.
# A separate exit judge would have been a second arithmetic that could
# disagree with the first; there is one plan and one verdict.
#
# Read this before quoting the rate it produces: a maker who is filled
# on his exit was filled because the market came to him, which on the
# losing half of the distribution is the market coming through him. The
# fill rate here says how OFTEN the exit rests would have been touched.
# It says nothing about what those fills were worth, and the programme
# is explicit that the VALUE is owner decision 18, not a threshold this
# unit sets.

# a reduction older than the shadow's own window is not a current exit:
# the market can re-enter the window on a NEW trade while carrying an
# ancient reduction, and that must not read as "he is leaving now".
# Derived, not a seventh knob: it tightens with MIRROR_LOOKBACK_H.
def _exit_max_age_s() -> float:
    return float(LOOKBACK_H) * 3600.0


def reduction_event(fills: list[dict], long_asset: str | None, other_asset: str | None,
                    max_age_s: float | None = None, now_ts: float | None = None) -> dict | None:
    """HIS most recent move that REDUCED his net on this market, or None.

    His net is long minus other (mi.his_net), so exactly two shapes
    reduce it: a SELL of the long token, and a BUY of the complement --
    his pair completion, the shape he actually uses. The event is the
    LAST fill that changed his net at all: if that fill INCREASED it he
    is adding, not leaving, and a reduction behind it is history, not a
    current exit. `left` is a net that reached zero (or crossed it);
    anything else is a trim.

    Prices are read the same way the rest of the worker reads them
    (`_px`, which refuses a price outside (0,1)); an unreadable price
    still records the event, with `px_equiv` None, so a reduction we
    cannot price is visible instead of absent.

    ONE LIMITATION, STATED BECAUSE `left` IS SERVED ON THE PROBE LINE:
    each leg is floored at zero at EVERY fill here, while
    `mi.net_positions` floors only the final value. The two agree unless
    a SELL exceeds the BUYs we ingested -- exactly the ingest-miss the
    floor exists for -- and in that case a trim reads as net_after 0 and
    the event is recorded `left` rather than `reduced`. The row's own
    `his_net` carries the same limitation, and he exits by buying the
    complement, which never drives a leg negative, so the exposure is
    small; it is a mislabel of a kind, never a phantom exit.
    """
    now_ts = time.time() if now_ts is None else float(now_ts)
    max_age_s = _exit_max_age_s() if max_age_s is None else float(max_age_s)

    def _ts(f: dict) -> float:
        try:
            return float(f.get("ts") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _size(f: dict) -> float:
        try:
            return float(f.get("size") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    # replayed in his own order, with the same floor-at-zero rule
    # mi.net_positions applies (a SELL beyond what we saw him buy is a
    # fill we missed, not a short)
    raw: dict[str, float] = {}

    def _net() -> float:
        return round(max(0.0, raw.get(long_asset or "", 0.0))
                     - max(0.0, raw.get(other_asset or "", 0.0)), 6)

    last: dict | None = None
    if not long_asset:
        # we do not know which token is the long leg, so "he reduced the
        # long side" is not a statement this row can make
        return None
    # his own order, and OURS as the tiebreak: the query returns the
    # fills ORDER BY ts, id, and two fills on one timestamp must not be
    # reordered by a lexicographic id ("10" before "9")
    for _i, f in sorted(enumerate(fills), key=lambda p: (_ts(p[1]), p[0])):
        asset = str(f.get("asset") or "")
        size = _size(f)
        if not asset or size <= 0:
            continue
        before = _net()
        raw[asset] = raw.get(asset, 0.0) + (size if str(f.get("side") or "").upper() == "BUY"
                                            else -size)
        after = _net()
        if abs(after - before) < 1e-9:
            continue                      # nothing about his net changed
        if after > before:
            last = None                   # he is adding: no exit is in force
            continue
        if before <= 0:
            # he held no long side to leave: this is him OPENING a short
            # of the long leg, which is the short reading's question, not
            # the exit leg's
            continue
        px = _px(f)
        if asset == other_asset and str(f.get("side") or "").upper() == "BUY":
            move, equiv, comp = "bought_complement", (None if px is None else round(1.0 - px, 4)), px
        elif asset == long_asset and str(f.get("side") or "").upper() == "SELL":
            move, equiv, comp = "sold_long", px, None
        else:                             # a token that is neither leg cannot move this net
            continue
        last = {"move": move, "asset": asset, "at": _ts(f), "size": round(size, 4),
                "px_equiv": equiv, "complement_px": comp,
                "net_before": before, "net_after": after,
                "left": after <= 1e-9}
    if last is None:
        return None
    age = round(max(0.0, now_ts - float(last["at"])), 1)
    if max_age_s > 0 and age > max_age_s:
        return None                       # a reduction older than the window we read
    last["age_s"] = age
    last["kind"] = "left" if last["left"] else "reduced"
    return last


# THE PLAN REASONS THAT ARE A DECISION not to reduce (we hold what we
# have) as opposed to a reading that could not decide at all. `mi.plan`
# returns side=None for exactly six reasons: these four, plus its two
# FAIL-CLOSED REFUSALS -- "venue unreadable" and "frozen: venue and
# ledger disagree" -- which are not decisions and must never be filed
# as one. A test pins that this list plus those two is the whole set,
# so a seventh reason added upstream cannot silently become a `hold`:
# anything not named here falls through to `none`, the unjudged class.
_EXIT_HOLD_REASONS = ("on target", "under one share", "under the dollar dead band",
                      "inside hysteresis")


def exit_leg(ev: dict | None, ledger: float, venue: float | None,
             target: int | None = None, p: "mi.Plan | None" = None,
             his_px: float | None = None, no_plan_reason: str | None = None) -> dict:
    """The exit-leg block for one row, or {} when he is not reducing.

    What it records, and nothing beyond it: that he reduced or left,
    when, at what price the COMPLEMENT traded (the leg he actually
    bought), what our own leg was at that moment -- by our ledger and by
    the venue, the two readings the plan is fail-closed on -- what the
    exit rule would have done, and the reason when it would have done
    nothing.

      rest  the row's plan is a SELL of the long leg at his equivalent
            or better; its verdict is the row's own would_fill
      hold  the rule DECIDED not to reduce: on target, under a share,
            inside the dead band or the hysteresis, or the plan is a
            BUY_LONG -- our target is still above what we hold and the
            entry leg, not the exit leg, has the market
      none  the rule could not decide: no ratio, no mark, an unreadable
            venue, a frozen slug, no side of the book to rest at

    `none` is the unjudged class: it is never a fill and never a miss.

    THE BUCKET IS DECIDED BY THE REASON, NEVER BY ARITHMETIC. An earlier
    version filed any side-None plan as `hold` when `target >= ledger`,
    which is a comparison, not a decision: it swept `frozen` and `venue
    unreadable` -- both fail-closed REFUSALS -- into `hold` whenever our
    target sat at or above our ledger, which is the mirror's own day-one
    state (it holds nothing). Frozen is the most common reason class in
    the shadow window the mirror programme records, so that clause put
    the rows carrying the LEAST information into the bucket the line
    serves as "the rule chose not to reduce", and into the count the
    gate keys on. It is removed, not guarded.
    """
    if not ev:
        return {}
    out: dict[str, Any] = {
        "exit_kind": ev["kind"], "exit_move": ev["move"], "exit_at": ev["at"],
        "exit_age_s": ev.get("age_s"), "exit_size": ev["size"],
        "exit_his_px": ev["px_equiv"], "exit_complement_px": ev["complement_px"],
        "exit_his_net_before": ev["net_before"], "exit_his_net_after": ev["net_after"],
        # our own leg at that moment, both readings
        "exit_ledger": round(float(ledger), 4), "exit_venue": venue,
        "exit_target": None if target is None else int(target),
    }
    if p is None:
        out.update(exit_plan="none", exit_reason=no_plan_reason or "no plan")
        return out
    if p.side == "SELL_LONG" and p.price is not None:
        out.update(exit_plan="rest", exit_side=p.side, exit_qty=int(p.qty),
                   exit_px=p.price, exit_reason=p.reason,
                   # did we rest at HIS level, or did the book's own side
                   # hold the price above it? (the plan takes the max)
                   exit_at_his_level=(his_px is not None and p.price == his_px),
                   # the immediate read, beside the verdict and never in
                   # it: the bid is already at or through our price
                   exit_marketable_now=bool(p.would_fill))
        return out
    if p.side is None and str(p.reason or "") in _EXIT_HOLD_REASONS:
        out.update(exit_plan="hold", exit_reason=p.reason)
        return out
    if p.side == "BUY_LONG":
        # he reduced and our target is still ABOVE what we hold: the
        # exit rule does nothing here, whatever the entry rule does.
        #
        # THE PLAN'S OWN REASON RIDES ALONG. This clause writes the
        # bucket's own text over `p.reason`, and one of the reasons it
        # overwrites is `no price to rest at` -- a book we could not
        # read on the side the entry leg wanted. The BUCKET is right
        # either way (the exit rule would not reduce here whatever the
        # book says), but `hold` is served on the line as "the rule
        # chose not to reduce", and without this key the row no longer
        # records that there was no price. It costs one key.
        out.update(exit_plan="hold",
                   exit_reason="target still above the ledger; the entry leg has it",
                   exit_plan_reason=p.reason)
        return out
    out.update(exit_plan="none", exit_reason=p.reason or no_plan_reason or "no plan")
    return out


def _pct(vals: list[float], q: float) -> float | None:
    """Nearest-rank percentile — the same rule the drift p90 and the
    report's `_p` use, restated here so the worker does not import the
    report to read one number."""
    xs = sorted(float(v) for v in vals if v is not None)
    if not xs:
        return None
    n = len(xs)
    return round(xs[min(n - 1, max(0, -(-int(q * 100) * n // 100) - 1))], 1)


# ONE ROW PER MARKET COMES BACK, not one per reading: the gate is
# clustered by MARKET (§3b), and a market read two hundred times is one
# market's worth of evidence. The DISTINCT ON prefers the reading that
# was actually judged, then any reading that produced a rest, then the
# newest -- so a market whose newest tick says "hold" does not erase the
# exit plan it rested an hour ago. The window, the cap and the caller's
# timeout are the three bounds; there is no ORDER BY over the whole
# table and no scan outside the window's index.
_SQL_EXIT_CENSUS = """
SELECT DISTINCT ON (whale, condition_id)
       whale, condition_id, would_fill, would_qty, would_px,
       detail->>'exit_kind'   AS exit_kind,
       detail->>'exit_plan'   AS exit_plan,
       detail->>'exit_reason' AS exit_reason,
       detail->>'family'      AS family,
       (detail->>'touched_s')::float8 AS touched_s
  FROM mirror_shadow
 WHERE at >= now() - ($1::float8 * interval '1 hour')
   AND detail ? 'exit_kind'
 ORDER BY whale, condition_id,
          (detail->>'exit_plan' = 'rest' AND would_fill IS NOT NULL) DESC,
          (detail->>'exit_plan' = 'rest') DESC,
          at DESC
 LIMIT $2 /* exit-leg-census */
"""


def summarize_exit_rows(rows: list[dict], window_h: float = EXIT_WINDOW_H,
                        limit: int | None = None) -> dict:
    """The MIRROREXIT reading, per whale, from one row per market.

    Every market lands in exactly one bucket and none is counted twice:
    n = resolved + unjudged + hold, and resolved = fills + misses. A
    market we could not judge -- a rest nobody ever read back, or a
    reading whose rule could not decide -- is `unjudged`, never a fill
    and never a miss (A3's unreadable contract).

    WHAT THE DENOMINATOR IS KEYED ON, both halves, because the line has
    to say both: `n` counts the markets where HE IS REDUCING and which
    WE COULD MAP to a US slug. A market we could not map has no book to
    judge a rest against and carries no exit block at all, so it is
    absent from this reading rather than counted unjudged; and a SELL
    our own ratio raised without a reduction of his -- our target moving
    down, or the market cap binding -- carries no exit block either. The
    probe line prints this count as `mapped_markets_he_reduced` for that
    reason: M14's denominator is "planned reductions", of which this is
    a strict subset, and decision 18 should read it as the subset it is.

    `lo` is the cluster-robust 95% lower bound the gates are read at
    (proof.roi_with_ci through mirror_report.rate_with_ci); the rows are
    already one per market, so the cluster count equals the resolved
    count and the interval cannot be narrowed by re-reading a market.

    THE GATE'S n IS THE PROPORTION'S OWN DENOMINATOR, NOT `n`. §3b reads
    this gate as `proportion / market / >= 30`, so the 30 is a count of
    JUDGED markets -- `clusters` -- and `ready` keys on that. Keying it
    on `n` (which counts holds and unjudged rows too) let 28 holds and
    two filled rests print `ready=true` with a 95% lower bound of 1.00
    over two markets: `rate_with_ci` -> `proof.roi_with_ci` refuses only
    below two clusters, and two identical observations give a zero
    standard error and an interval clamped onto the point estimate.
    Below the floor the three COHORT ESTIMATES -- the rate, its lower
    bound and the dollar share -- are not computed at all; the counts
    they would be computed from stay on the line, because a count is a
    fact and a rate below its minimum n authorises nothing (§3b).

    A TRUNCATED CENSUS IS NOT A READING. `limit` is the caller's market
    cap; the caller reads one row beyond it, and if that row exists this
    returns the refusal (no whales, no families) instead of an
    alphabetically-selected prefix of (whale, condition_id) served as
    the window's reading.
    """
    from ..analytics.mirror_report import rate_with_ci

    if limit is not None and len(rows) > int(limit):
        return {"whales": {}, "families": {}, "markets": None,
                "window_h": float(window_h), "truncated": True, "limit": int(limit)}

    def _blank() -> dict[str, Any]:
        return {"n": 0, "reduced": 0, "left": 0, "rest": 0, "hold": 0, "no_plan": 0,
                "resolved": 0, "fills": 0, "misses": 0, "unresolved": 0,
                "resolved_usd": 0.0, "unfilled_usd": 0.0}

    by: dict[str, dict[str, Any]] = {}
    rest_rows: dict[str, list[dict]] = {}
    touches: dict[str, list[float]] = {}
    fam: dict[str, dict[str, Any]] = {}
    for r in rows:
        w = str(r.get("whale") or "-")
        b = by.setdefault(w, _blank())
        b["n"] += 1
        b["left" if str(r.get("exit_kind")) == "left" else "reduced"] += 1
        plan = str(r.get("exit_plan") or "")
        if plan != "rest":
            b["hold" if plan == "hold" else "no_plan"] += 1
            continue
        b["rest"] += 1
        wf = r.get("would_fill")
        if wf is None:
            b["unresolved"] += 1
            continue
        b["resolved"] += 1
        rest_rows.setdefault(w, []).append({"would_fill": bool(wf),
                                            "condition_id": r.get("condition_id")})
        try:
            usd = abs(float(r.get("would_qty") or 0.0)) * float(r.get("would_px") or 0.0)
        except (TypeError, ValueError):
            usd = 0.0
        b["resolved_usd"] += usd
        fk = f"{w}/{str(r.get('family') or '-')}"
        fb = fam.setdefault(fk, {"n": 0, "fills": 0, "touch": [], "whale": w})
        fb["n"] += 1
        if wf:
            b["fills"] += 1
            fb["fills"] += 1
            t = r.get("touched_s")
            if t is not None:
                touches.setdefault(w, []).append(float(t))
                fb["touch"].append(float(t))
        else:
            b["misses"] += 1
            b["unfilled_usd"] += usd
    for w, b in by.items():
        b["resolved_usd"] = round(b["resolved_usd"], 2)
        b["unfilled_usd"] = round(b["unfilled_usd"], 2)
        b["unjudged"] = b["unresolved"] + b["no_plan"]
        ci = rate_with_ci(rest_rows.get(w, []))
        # one row per market comes back, so the cluster count IS the
        # judged-market count; the gate's minimum n is read against it
        b["clusters"] = ci["clusters"]
        b["ready"] = bool(b["clusters"] >= EXIT_MIN_N)
        b["n_min"] = EXIT_MIN_N
        # §3b: the line prints at >= 30 JUDGED markets and the VALUE
        # feeds decision 18. Below that there is no reading to quote.
        b["rate"] = (round(b["fills"] / b["resolved"], 4)
                     if b["ready"] and b["resolved"] else None)
        b["lo"] = (ci["ci95"][0] if b["ready"] and ci.get("ci95") else None)
        b["unfilled_usd_share"] = (round(b["unfilled_usd"] / b["resolved_usd"], 4)
                                   if b["ready"] and b["resolved_usd"] > 0 else None)
        # THE PERCENTILES TAKE THE SAME FLOOR AS THE RATE. They are
        # descriptive and carry no confidence claim, which is why an
        # earlier version let them print at any n with `touch_n` beside
        # them -- but §3b lists `.time_to_touch_p50/p90` in the SAME gate
        # row as `sell_fill_lo` (`proportion / market / >= 30`), and they
        # were printing numbers on a line whose sibling fields read
        # `below_min_n`. A line that is a gate should obey one floor in
        # every field, so a reader cannot take a two-market median off
        # the row decision 18 reads. The count they were taken over
        # stays, because a count is a fact.
        b["touch_p50"] = _pct(touches.get(w, []), 0.5) if b["ready"] else None
        b["touch_p90"] = _pct(touches.get(w, []), 0.9) if b["ready"] else None
        b["touch_n"] = len(touches.get(w, []))
    fams = {}
    # bounded: the twelve families carrying the most judged markets.
    # §3b asks this split for time-to-touch p50/p90, NOT for a rate: a
    # per-family rate had no minimum n of its own and would have printed
    # 1.00 off one judged market beside a line that is a gate. The
    # counts stay; the rate is removed rather than floored.
    #
    # The split carries its WHALE'S readiness, and its percentiles take
    # the same floor: flooring them on the gate line while the split
    # beside it printed them at any n would have left the split as the
    # way to read the number the gate refused.
    for k, v in sorted(fam.items(), key=lambda kv: (-kv[1]["n"], kv[0]))[:12]:
        rdy = bool((by.get(v["whale"]) or {}).get("ready"))
        fams[k] = {"n": v["n"], "fills": v["fills"], "touch_n": len(v["touch"]),
                   "ready": rdy,
                   "touch_p50": _pct(v["touch"], 0.5) if rdy else None,
                   "touch_p90": _pct(v["touch"], 0.9) if rdy else None}
    return {"whales": by, "families": fams, "markets": len(rows),
            "window_h": float(window_h), "truncated": False}


async def exit_census(pool, window_h: float | None = None, limit: int | None = None) -> dict:
    """One bounded read of the rows this worker already wrote, summarized
    per whale. Raises on an unreadable table; the caller contains it.

    It asks the table for ONE MARKET MORE than the cap: a census that
    hit its cap is a lexicographic prefix of (whale, condition_id) --
    whichever whale sorts first would take every slot and the second
    would read n=0 -- and that is not a reading of the window. The extra
    row is how the reading knows, and `summarize_exit_rows` refuses it.
    """
    w = float(EXIT_WINDOW_H if window_h is None else window_h)
    lim = int(EXIT_SUMMARY_MAX if limit is None else limit)
    rows = await pool.fetch(_SQL_EXIT_CENSUS, w, lim + 1)
    return summarize_exit_rows([dict(r) for r in rows], w, limit=lim)


async def refresh_exit_census(pool, now_ts: float | None = None, force: bool = False) -> dict:
    """The census, at most once per EXIT_SUMMARY_S, and CONTAINED: this
    is a measurement read, so a failure of it may not cost a tick, may
    not abandon a market and may not spend the venue budget. It fails
    closed -- the block carries an error and NO numbers, never a stale
    reading dressed as a fresh one -- and the failure takes the interval
    with it, so a table that raises every call is read no more often
    than one that answers."""
    now_ts = time.time() if now_ts is None else float(now_ts)
    if (not force and _exit_cache.get("value") is not None
            and now_ts - float(_exit_cache.get("at") or 0.0) < EXIT_SUMMARY_S):
        return _exit_cache["value"]
    try:
        value = await asyncio.wait_for(exit_census(pool), EXIT_CENSUS_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — table absent until 046, or slow
        value = {"error": type(exc).__name__}
        log.warning("mirror_shadow: exit-leg census unreadable (%s)", type(exc).__name__)
    _exit_cache.update(at=now_ts, value=value)
    return value


def attach_exit_census(stats: dict, now_ts: float) -> None:
    """The reading the probe line reads, on every census this worker
    beats -- including the ones that abandoned, where it is the CACHED
    value and its age says so. A tick that has never read it says so
    too: silence is the one output an instrument must not produce, and
    a tick that skipped the refresh says SUPPRESSED rather than absent,
    because "we never read it" and "we did not read it this tick" are
    different facts about the instrument.

    THE WINDOW TRAVELS WITH THE NUMBERS. `MIRROR_EXIT_WINDOW_H` is
    shell-settable down to 1 h, so a line that does not name its window
    can serve a 1 h cohort in the shape of the 24 h reading §3b and §4
    S4 define. It is published beside them and printed on the line.

    The block is copied per whale on the way out: the cache is a module
    global that outlives the tick, and an aliased sub-dict would let
    anything downstream edit the reading the next tick serves.
    """
    value = _exit_cache.get("value")
    if value is None:
        skipped = bool(stats.get("abandoned") or stats.get("skipped_backoff")
                       or stats.get("switched_off"))
        stats["exit_leg"] = {"state": "suppressed" if skipped else "unread"}
        return
    stats["exit_census_age_s"] = round(max(0.0, now_ts - float(_exit_cache.get("at") or 0.0)), 1)
    if "error" in value:
        stats["exit_leg"] = {"error": value["error"]}
        return
    stats["exit_window_h"] = value.get("window_h")
    if value.get("truncated"):
        # a census that hit its market cap is not a reading of the
        # window: no numbers at all, never a truncated cohort dressed
        # as the whole one
        stats["exit_leg"] = {"state": "truncated"}
        stats["exit_markets"] = None
        return
    stats["exit_leg"] = {k: dict(v) for k, v in (value.get("whales") or {}).items()}
    stats["exit_family"] = {k: dict(v) for k, v in (value.get("families") or {}).items()}
    stats["exit_markets"] = value.get("markets", 0)


# ---------------------------------------------------------------- tick

async def shadow_market(pool, pmus, whale: str, condition_id: str,
                        ratio: float | None, snap: dict[str, float],
                        positions: dict[str, float] | None,
                        snap_age_s: float | None = None,
                        allow_short: bool = False,
                        snap_partial: bool = False) -> dict:
    """One (whale, market) reading. `positions` is this tick's account
    walk (None = the walk failed: venue unreadable, plan frozen). `snap`
    is the exit worker's raw positions read; a token ABSENT from a fresh
    and complete read is a position he no longer holds (0), which is the
    exact case drift exists to catch -- fills say he holds, the venue
    says he merged out (review round two). Absent from a PARTIAL read is
    unknown (None). Returns the row that was written."""
    fills = await his_fills(pool, whale, condition_id)
    pos = mi.net_positions(fills)
    row: dict[str, Any] = {"whale": whale, "condition_id": condition_id,
                           "ratio": ratio, "detail": {}}
    m = await map_market(pool, fills)
    if not m:
        assets = sorted(pos)
        row.update(long_asset=assets[0] if assets else None,
                   other_asset=assets[1] if len(assets) > 1 else None,
                   his_long=None, his_other=None, his_net=None,
                   reason="unmapped: no US market for his tokens")
        # WHAT THE UNMAPPED MARKET IS (Phase 0): the row used to carry two
        # token ids and nothing else, so 81% of his markets were one
        # number with no family, no dollars and no reason. Each is now
        # named -- slug, title, sport, family, the resolver's own step,
        # his dollars in the window, and the NULL-outcome fills that
        # make a premap miss ours rather than the venue's.
        ctx = _first_context(fills)
        row["detail"].update(
            his_slug=ctx["his_slug"], title=ctx["title"], event_title=ctx["event_title"],
            event_slug=ctx["event_slug"], sport=ctx["sport"],
            family=_family_of(ctx["his_slug"]),
            explain=await explain_unmapped(pool, ctx),
            notional_6h=notional_in_window(fills, LOOKBACK_H),
            gross_sh=round(sum(pos.values()), 4),
            outcome_null=outcome_null_count(fills))
        return row
    la, oa, slug = m["long_asset"], m["other_asset"], m["us_slug"]
    his_long = float(pos.get(la, 0.0)) if la else 0.0
    his_other = float(pos.get(oa, 0.0)) if oa else 0.0
    net = mi.his_net(his_long, his_other)
    fresh_snap = snap_age_s is not None and snap_age_s <= SNAP_MAX_AGE_S
    # THE MAPPED MARKET'S CENSUS (Phase 0): family (the P1 family gate
    # reads it), per-side as a bool on every row, the snapshot state in
    # one word, fills that landed after the snapshot, and the ledger's
    # own facts -- whether the position on the slug is a legacy per-fill
    # row and what mapping class that row carries, because a
    # ledger-sourced map is refused at P1 admission under the quarantine
    # and the share P1 can actually admit was not a number anywhere.
    lf = ledger_facts(await ledger_rows(pool, slug))
    row["detail"].update(
        family=_family_of(slug), per_side=bool(m.get("per_side")),
        map_class=(lf["map_class"] if m["source"] == "ledger" else m["source"]),
        ledger_legacy=lf["legacy"],
        snap_state=("none" if snap_age_s is None else
                    "stale" if not fresh_snap else
                    "fresh_partial" if snap_partial else "fresh_complete"),
        fills_since_snap=fills_since(fills, snap_age_s),
        his_paired_sh=round(min(his_long, his_other), 4),
        his_sport=_first_context(fills)["sport"])

    def _snap_of(asset: str | None) -> float | None:
        if not asset or not fresh_snap:
            return None
        if asset in snap:
            return float(snap[asset])
        return None if snap_partial else 0.0

    row.update(us_market_slug=slug, long_asset=la, other_asset=oa,
               his_long=his_long, his_other=his_other, his_net=net,
               snap_long=_snap_of(la), snap_other=_snap_of(oa))
    row["detail"]["map"] = m["source"]
    if m.get("per_side"):
        row["detail"]["per_side"] = True     # his larger side is the long, on its own slug
    if fresh_snap and snap_partial:
        row["detail"]["snap_partial"] = True
    if snap_age_s is not None:
        row["detail"]["snap_age_s"] = round(float(snap_age_s), 1)
        if not fresh_snap:
            row["detail"]["snap_stale"] = True
    # venue quote for the long side (one paced call) and what we hold
    bid = ask = None
    try:
        bid, ask = await asyncio.to_thread(_paced_bbo, pmus, slug)
    except Exception as exc:  # noqa: BLE001 — unreadable book, named below
        row["detail"]["bbo_error"] = type(exc).__name__
    mark = None
    if bid is not None and ask is not None and 0.0 < bid < 1.0 and 0.0 < ask < 1.0:
        mark = round((float(bid) + float(ask)) / 2.0, 4)
    elif ask is not None and 0.0 < ask < 1.0:
        mark = float(ask)
    if mark is not None:
        # his GROSS dollars at the mark -- long leg at the mark, other leg
        # at one minus it -- the denominator the coverage gate is read
        # against (a net mirror can never hold the paired part)
        row["detail"]["his_gross_usd"] = round(his_long * mark + his_other * (1.0 - mark), 2)
    venue = None if positions is None else float(positions.get(slug.lower(), 0.0))
    try:
        ledger = await ledger_net(pool, slug)
    except Exception:  # noqa: BLE001
        ledger = 0
    # HIS EXIT, READ FROM HIS OWN FILLS (A3): the same list the target is
    # derived from, so it costs no read of any kind. It rides the early
    # returns too -- an exit we could not plan against is the reading the
    # gate needs most, and dropping it would leave the census counting
    # only the exits that went well.
    ev = reduction_event(fills, la, oa)
    # NO SCALE OR NO MARK IS NO PLAN (review round one): a missing ratio
    # or an unreadable book must not read as "target zero, flatten" or
    # as an uncapped target. Nothing is planned; the row says why.
    if ratio is None or ratio <= 0:
        row.update(target=0, target_raw=0.0, capped=False, ledger_net=int(ledger),
                   venue_net=venue, bid=bid, ask=ask, mark=mark, his_last_px=None,
                   would_side=None, would_qty=0, would_px=None, would_fill=None,
                   reason="no ratio: fewer than the minimum markets with an opening burst")
        row["detail"].update(exit_leg(ev, ledger, venue, no_plan_reason=row["reason"]))
        return row
    if mark is None:
        row.update(target=0, target_raw=0.0, capped=False, ledger_net=int(ledger),
                   venue_net=venue, bid=bid, ask=ask, mark=None, his_last_px=None,
                   would_side=None, would_qty=0, would_px=None, would_fill=None,
                   reason="no mark: book unreadable")
        row["detail"].update(exit_leg(ev, ledger, venue, no_plan_reason=row["reason"]))
        return row
    tgt = mi.target_shares(ratio, net, mark, allow_short=allow_short)
    reducing = int(tgt["target"]) <= int(ledger)
    his_px = his_level(fills, la, oa, reducing)
    p = mi.plan(int(tgt["target"]), float(ledger), venue,
                mi.Book(bid=bid, ask=ask), his_px, mark)
    row.update(target=int(tgt["target"]), target_raw=tgt["raw"], capped=bool(tgt["capped"]),
               ledger_net=int(ledger), venue_net=venue, bid=bid, ask=ask, mark=mark,
               his_last_px=his_px, would_side=p.side, would_qty=int(p.qty),
               would_px=p.price,
               # resolved against the NEXT reading of this market (see
               # _write); the immediate read is kept beside it
               would_fill=None,
               reason=(tgt["why"] + "; " if tgt.get("why") else "") + p.reason)
    row["detail"].update(p.detail)
    if p.side and p.price is not None:
        row["detail"]["marketable_now"] = bool(p.would_fill)
    # the exit leg names the plan the row already carries; it never
    # re-plans, so there is one arithmetic and one verdict
    row["detail"].update(exit_leg(ev, ledger, venue, int(tgt["target"]), p, his_px))
    # the parallel short reading, beside the target and never in it
    row["detail"].update(short_reading(ratio, net, mark, ledger, venue,
                                       mi.Book(bid=bid, ask=ask), fills, la, oa))
    return row


def _rowcount(status) -> int:
    """asyncpg returns the command tag ('UPDATE 3'); a fake pool may
    return None."""
    try:
        return int(str(status).split()[-1])
    except (TypeError, ValueError, IndexError, AttributeError):
        return 0


async def _resolve_previous(pool, row: dict, census: dict | None = None,
                            pmus=None) -> tuple[int, int]:
    """WOULD IT HAVE FILLED? Every plan this market still has open is
    judged against THIS tick's book. A plan is a resting order with a
    life of JUDGE_TTL_S (the live lane's rest TTL): it FILLED if, at any
    reading inside that life, the opposite side of the book REACHED our
    price (buy: the ask came down to it; sell: the bid came up to it);
    it did NOT fill if it aged past its life while the market was still
    being read without that happening. "The book moved past our level"
    is never a fill: market makers reprice by cancel-and-replace, so a
    level that vanished was as likely pulled as taken, and counting it
    would flatter the rate that gates P1. A plan on a market we stopped
    reading stays NULL -- unobserved is not unfilled. (Review round one
    judged one plan against one next reading, thirty seconds apart, and
    read 15% on the first hour of the shadow; a resting order lives
    minutes, not one tick, so this is the question P1 actually asks.)
    Returns (resolved, filled) counts for the census. Best-effort.

    THE TOUCH IS RECORDED (Phase 0): a judged plan also learns how long
    it waited (touched_s, from its own `at`) and the touching side's
    price, inside its JSONB detail -- a 046 column would change the
    table the report select is pinned to. The PARALLEL SHORT reading in
    the detail is judged by the same rule on its own side: its SELL of
    the long token fills when the bid comes UP to its price, its BUY
    when the ask comes down, and it expires past the same TTL. Those
    counts go to the short census keys, never into the long-only rate
    P1 is gated on. When something was touched this tick and a venue
    handle is given, ONE paced book read records the size resting at the
    best bid and ask (the queue the touch rate cannot see) on the rows
    just touched; an unreadable book records null, never a guess."""
    bid, ask = row.get("bid"), row.get("ask")
    whale, cid = row.get("whale"), row.get("condition_id")
    resolved = filled = 0
    resolved_s = filled_s = 0
    touch_ctx = ("COALESCE(detail, '{}'::jsonb) || jsonb_build_object("
                 "'touched_s', round(extract(epoch FROM (now() - at)))::int, "
                 "'touch_px', $3::float8)")
    try:
        if ask is not None and 0.0 < float(ask) < 1.0:
            n = _rowcount(await pool.execute(
                "UPDATE mirror_shadow SET would_fill = true, detail = " + touch_ctx + " "
                "WHERE whale = $1 AND condition_id = $2 AND would_fill IS NULL "
                "AND would_side = 'BUY_LONG' AND would_px IS NOT NULL AND would_px >= $3 "
                "AND at >= now() - ($4::float8 * interval '1 second') /* judge-buy */",
                whale, cid, float(ask), float(JUDGE_TTL_S)))
            resolved += n
            filled += n
        if bid is not None and 0.0 < float(bid) < 1.0:
            n = _rowcount(await pool.execute(
                "UPDATE mirror_shadow SET would_fill = true, detail = " + touch_ctx + " "
                "WHERE whale = $1 AND condition_id = $2 AND would_fill IS NULL "
                "AND would_side = 'SELL_LONG' AND would_px IS NOT NULL AND would_px <= $3 "
                "AND at >= now() - ($4::float8 * interval '1 second') /* judge-sell */",
                whale, cid, float(bid), float(JUDGE_TTL_S)))
            resolved += n
            filled += n
        if bid is not None or ask is not None:
            # still being read, and the plan outlived a resting order
            resolved += _rowcount(await pool.execute(
                "UPDATE mirror_shadow SET would_fill = false, detail = COALESCE(detail, '{}'::jsonb) "
                "|| jsonb_build_object('expired_s', round(extract(epoch FROM (now() - at)))::int) "
                "WHERE whale = $1 AND condition_id = $2 AND would_fill IS NULL "
                "AND would_side IS NOT NULL AND would_px IS NOT NULL "
                "AND at < now() - ($3::float8 * interval '1 second') /* judge-expire */",
                whale, cid, float(JUDGE_TTL_S)))
        # the parallel short reading, judged on ITS side of the book
        short_ctx = ("COALESCE(detail, '{}'::jsonb) || jsonb_build_object("
                     "'would_fill_short', true, "
                     "'touched_s_short', round(extract(epoch FROM (now() - at)))::int, "
                     "'touch_px_short', $3::float8)")
        if bid is not None and 0.0 < float(bid) < 1.0:
            n = _rowcount(await pool.execute(
                "UPDATE mirror_shadow SET detail = " + short_ctx + " "
                "WHERE whale = $1 AND condition_id = $2 "
                "AND detail->>'would_fill_short' IS NULL "
                "AND detail->>'would_side_short' = 'SELL_LONG' "
                "AND (detail->>'would_px_short')::float8 <= $3 "
                "AND at >= now() - ($4::float8 * interval '1 second') /* judge-short-sell */",
                whale, cid, float(bid), float(JUDGE_TTL_S)))
            resolved_s += n
            filled_s += n
        if ask is not None and 0.0 < float(ask) < 1.0:
            n = _rowcount(await pool.execute(
                "UPDATE mirror_shadow SET detail = " + short_ctx + " "
                "WHERE whale = $1 AND condition_id = $2 "
                "AND detail->>'would_fill_short' IS NULL "
                "AND detail->>'would_side_short' = 'BUY_LONG' "
                "AND (detail->>'would_px_short')::float8 >= $3 "
                "AND at >= now() - ($4::float8 * interval '1 second') /* judge-short-buy */",
                whale, cid, float(ask), float(JUDGE_TTL_S)))
            resolved_s += n
            filled_s += n
        if bid is not None or ask is not None:
            resolved_s += _rowcount(await pool.execute(
                "UPDATE mirror_shadow SET detail = COALESCE(detail, '{}'::jsonb) "
                "|| jsonb_build_object('would_fill_short', false, "
                "'expired_s_short', round(extract(epoch FROM (now() - at)))::int) "
                "WHERE whale = $1 AND condition_id = $2 "
                "AND detail->>'would_fill_short' IS NULL "
                "AND detail->>'would_side_short' IS NOT NULL "
                "AND detail->>'would_px_short' IS NOT NULL "
                "AND at < now() - ($3::float8 * interval '1 second') /* judge-short-expire */",
                whale, cid, float(JUDGE_TTL_S)))
        if filled + filled_s > 0:
            depth = None
            if pmus is not None and row.get("us_market_slug"):
                try:
                    depth = await asyncio.to_thread(_paced_depth, pmus, str(row["us_market_slug"]))
                except Exception:  # noqa: BLE001 — unreadable depth is null
                    depth = None
                if census is not None:
                    census["touch_depth_reads"] = census.get("touch_depth_reads", 0) + 1
            await pool.execute(
                "UPDATE mirror_shadow SET detail = COALESCE(detail, '{}'::jsonb) "
                "|| jsonb_build_object('touch_depth', $3::jsonb) "
                "WHERE whale = $1 AND condition_id = $2 "
                "AND (detail ? 'touched_s' OR detail ? 'touched_s_short') "
                "AND NOT (detail ? 'touch_depth') /* judge-depth */",
                whale, cid, json.dumps(depth))
    except Exception:  # noqa: BLE001 — table absent until 046
        pass
    if census is not None:
        census["resolved_short"] = census.get("resolved_short", 0) + resolved_s
        census["resolved_filled_short"] = census.get("resolved_filled_short", 0) + filled_s
    return resolved, filled


async def _write(pool, row: dict, census: dict | None = None, pmus=None) -> tuple[int, int]:
    """Judge this market's open plans against the row's book, then land
    the row. Returns (resolved, filled) from _resolve_previous; the short
    reading's counts land in `census` when given."""
    verdict = await _resolve_previous(pool, row, census, pmus)
    await pool.execute(
        """
        INSERT INTO mirror_shadow (whale, condition_id, us_market_slug, long_asset,
            other_asset, his_long, his_other, his_net, snap_long, snap_other, ratio,
            target, target_raw, capped, ledger_net, venue_net, bid, ask, mark,
            his_last_px, would_side, would_qty, would_px, would_fill, reason, detail)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,
                $20,$21,$22,$23,$24,$25,$26::jsonb)
        """,
        row.get("whale"), row.get("condition_id"), row.get("us_market_slug"),
        row.get("long_asset"), row.get("other_asset"), row.get("his_long"),
        row.get("his_other"), row.get("his_net"), row.get("snap_long"),
        row.get("snap_other"), row.get("ratio"), row.get("target"), row.get("target_raw"),
        row.get("capped"), row.get("ledger_net"), row.get("venue_net"), row.get("bid"),
        row.get("ask"), row.get("mark"), row.get("his_last_px"), row.get("would_side"),
        row.get("would_qty"), row.get("would_px"), row.get("would_fill"), row.get("reason"),
        json.dumps(row.get("detail") or {}, default=str))
    return verdict


async def tick_once(pool, pmus, now_ts: float | None = None) -> dict:
    """One pass over the newest MAX_MARKETS_PER_TICK markets of every
    mirrored whale. Returns the census the heartbeat carries; its
    `status` is what the heartbeat reports."""
    global _backoff_until
    now_ts = time.time() if now_ts is None else now_ts
    # would_orders: plans this tick; marketable_now: of those, the book
    # was already at or through the resting price; resolved /
    # resolved_filled: previous plans judged against this tick's book
    # (review round two: a fill counted at write time is structurally 0)
    stats: dict[str, Any] = {"status": "ok", "whales": 0, "markets": 0, "rows": 0,
                             "unmapped": 0, "would_orders": 0, "marketable_now": 0,
                             "resolved": 0, "resolved_filled": 0,
                             # the parallel short reading's census (Phase 0)
                             "would_orders_short": 0, "resolved_short": 0,
                             "resolved_filled_short": 0, "touch_depth_reads": 0,
                             # the exit leg (A3): rows on which he reduced or
                             # left, and what the exit rule would have done.
                             # exit_unjudged is a row whose rule could not
                             # decide -- never a fill, never a miss.
                             "exit_rows": 0, "exit_rest": 0, "exit_hold": 0,
                             "exit_unjudged": 0, "exit_left": 0,
                             "frozen": 0, "skipped_markets": 0, "skipped_unmapped": 0,
                             "stale_snapshots": 0, "skipped_backoff": False, "ratio": {}}
    if now_ts < _backoff_until:
        stats["skipped_backoff"] = True
        attach_exit_census(stats, now_ts)
        return stats
    if await _db_switch_off(pool):
        stats["switched_off"] = True
        attach_exit_census(stats, now_ts)
        return stats
    whales = mirror_whales()
    ratios = await refresh_ratios(pool, whales)
    # ONE positions walk for the whole tick, before any market read; a
    # failed walk is the venue saying no -- back off before adding load
    positions = await account_positions(pmus)
    if positions is None:
        _backoff_until = now_ts + BACKOFF_S
        stats.update(positions_unreadable=True, abandoned=True, status="degraded")
        log.warning("mirror_shadow: account positions unreadable — abandoning the "
                    "tick, backing off %ss", BACKOFF_S)
        attach_exit_census(stats, now_ts)
        return stats
    stats["venue_positions"] = len(positions)
    reads = 0
    misses = 0
    for w in whales:
        stats["whales"] += 1
        r = (ratios.get(w) or {})
        stats["ratio"][w] = r.get("ratio")
        snap, snap_age, snap_partial = await snapshot_sizes(pool, w)
        if snap_age is not None and snap_age > SNAP_MAX_AGE_S:
            stats["stale_snapshots"] += 1
        try:
            conds = await active_conditions(pool, w)
        except Exception as exc:  # noqa: BLE001
            log.warning("mirror_shadow: active markets for %s unreadable (%s)", w, type(exc).__name__)
            continue
        for i, cid in enumerate(conds):
            if reads >= MAX_MARKETS_PER_TICK:
                stats["skipped_markets"] += len(conds) - i
                stats["capped_tick"] = True
                break
            if _unmapped_until.get((w, cid), 0.0) > now_ts:
                stats["skipped_unmapped"] += 1
                continue
            reads += 1
            stats["markets"] += 1
            try:
                row = await shadow_market(pool, pmus, w, cid, r.get("ratio"), snap,
                                          positions, snap_age, snap_partial=snap_partial)
            except Exception as exc:  # noqa: BLE001 — one market, not the tick
                log.warning("mirror_shadow: %s/%s failed (%s)", w, cid, type(exc).__name__)
                continue
            if str(row.get("reason") or "").startswith("unmapped"):
                stats["unmapped"] += 1
                _unmapped_until[(w, cid)] = now_ts + UNMAPPED_TTL_S
            if row.get("would_side"):
                stats["would_orders"] += 1
                if (row.get("detail") or {}).get("marketable_now"):
                    stats["marketable_now"] += 1
            if (row.get("detail") or {}).get("would_side_short"):
                stats["would_orders_short"] += 1
            ex = str((row.get("detail") or {}).get("exit_plan") or "")
            if ex:
                stats["exit_rows"] += 1
                if (row.get("detail") or {}).get("exit_kind") == "left":
                    stats["exit_left"] += 1
                stats["exit_rest" if ex == "rest" else
                      "exit_hold" if ex == "hold" else "exit_unjudged"] += 1
            if "frozen" in str(row.get("reason") or ""):
                stats["frozen"] += 1
            if row.get("us_market_slug") and row.get("bid") is None and row.get("ask") is None:
                misses += 1
                if misses >= MISS_STREAK_ABANDON:
                    _backoff_until = now_ts + BACKOFF_S
                    stats.update(abandoned=True, status="degraded")
                    log.warning("mirror_shadow: %d consecutive venue misses — "
                                "abandoning the tick, backing off %ss", misses, BACKOFF_S)
                    break
            else:
                misses = 0
            try:
                n_res, n_fill = await _write(pool, row, stats, pmus)
                stats["rows"] += 1
                stats["resolved"] += n_res
                stats["resolved_filled"] += n_fill
            except Exception as exc:  # noqa: BLE001 — table absent until 046
                # A ROW THAT CANNOT LAND STOPS THE TICK: no venue budget
                # is spent on readings nobody can read back.
                _backoff_until = now_ts + BACKOFF_S
                stats.update(write_failed=type(exc).__name__, abandoned=True,
                             status="degraded")
                log.warning("mirror_shadow: write failed (%s) — abandoning the tick, "
                            "backing off %ss", type(exc).__name__, BACKOFF_S)
                break
        if stats.get("abandoned"):
            break
    # the 24 h reading, at most once per EXIT_SUMMARY_S and never on a
    # tick that has already abandoned: an abandoned tick has told us the
    # venue or the table is unwell, and a census read is not the answer.
    #
    # THIS GUARD IS LOAD-BEARING FOR TWO OF THE THREE ABANDONS. The
    # unreadable-walk abandon returns above it and never reaches this
    # line; the BBO miss streak and the write failure `break` out of the
    # loop and arrive here. The write failure is the one that most wants
    # it: an INSERT INTO mirror_shadow has just raised, and without the
    # guard the next thing this tick would do is a SELECT against that
    # same table. The test that pins it drives BOTH fall-through paths,
    # because an assertion on the walk path alone passes with the guard
    # deleted -- which is what an earlier round of that test did.
    if not stats.get("abandoned"):
        await refresh_exit_census(pool, now_ts)
    attach_exit_census(stats, now_ts)
    return stats


async def main() -> None:
    from .. import pmus

    if not enabled():
        log.info("mirror_shadow: off by env (MIRROR_SHADOW)")
        while True:
            await asyncio.sleep(300)
    pool = await get_pool()
    log.info("mirror_shadow up: whales=%s poll=%ss lookback=%sh (NO ORDERS)",
             mirror_whales(), POLL_S, LOOKBACK_H)
    while True:
        try:
            stats = await tick_once(pool, pmus)
            try:
                await heartbeat("mirror_shadow", str(stats.get("status") or "ok"), stats)
            except Exception:  # noqa: BLE001
                log.debug("mirror_shadow: heartbeat failed")
            if stats.get("rows") or stats.get("abandoned"):
                log.info("mirror_shadow: %s", stats)
        except Exception:  # noqa: BLE001 — a measurement worker never dies
            log.exception("mirror_shadow pass failed")
        await asyncio.sleep(POLL_S)
