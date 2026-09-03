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
     the reason for none.

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
import os
import re
import time
from typing import Any

from ..analytics import mirror as mi
from ..db import get_pool, heartbeat
from ..venue_pace import pace

log = logging.getLogger(__name__)

POLL_S = float(os.environ.get("MIRROR_SHADOW_POLL_S", "30"))
LOOKBACK_H = float(os.environ.get("MIRROR_LOOKBACK_H", "6"))
RATIO_DAYS = int(os.environ.get("MIRROR_RATIO_DAYS", "30"))
RATIO_REFRESH_S = 3600.0
READ_PACING_S = 0.35
MAX_MARKETS_PER_TICK = int(os.environ.get("MIRROR_MAX_MARKETS", "20"))
POSITIONS_PAGES_MAX = 5
MISS_STREAK_ABANDON = 3
BACKOFF_S = 60.0
# a raw positions read older than this is not a reading of his book now
SNAP_MAX_AGE_S = float(os.environ.get("MIRROR_SNAP_MAX_AGE_S", "300"))
# a plan is a resting order with this life: it fills if the book reaches
# its price inside it, and did not fill if it ages past it while the
# market is still read (the live lane's rest TTL, review of the first
# shadow hour)
JUDGE_TTL_S = float(os.environ.get("MIRROR_JUDGE_TTL_S", "600"))
# a market that mapped to no venue market is not re-read every tick: the
# per-tick cap goes to markets that can produce a plan (81% of RN1's
# markets read unmapped in the first hour and took every slot)
UNMAPPED_TTL_S = 900.0
_unmapped_until: dict[tuple[str, str], float] = {}
_STATE_RATIO = "mirror_ratio"
_STATE_SWITCH = "mirror_shadow"
_SNAP_RAW_KEY = "whale_positions_raw:%s"
_backoff_until = 0.0
_ratio_cache: dict[str, Any] = {"at": 0.0, "by_whale": {}}
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
                try:
                    out[str(slug).lower()] = float((p or {}).get("netPosition") or 0.0)
                except (TypeError, ValueError):
                    continue
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
        log.warning("mirror_shadow: positions walk failed (%s)", type(exc).__name__)
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
    # NO SCALE OR NO MARK IS NO PLAN (review round one): a missing ratio
    # or an unreadable book must not read as "target zero, flatten" or
    # as an uncapped target. Nothing is planned; the row says why.
    if ratio is None or ratio <= 0:
        row.update(target=0, target_raw=0.0, capped=False, ledger_net=int(ledger),
                   venue_net=venue, bid=bid, ask=ask, mark=mark, his_last_px=None,
                   would_side=None, would_qty=0, would_px=None, would_fill=None,
                   reason="no ratio: fewer than the minimum markets with an opening burst")
        return row
    if mark is None:
        row.update(target=0, target_raw=0.0, capped=False, ledger_net=int(ledger),
                   venue_net=venue, bid=bid, ask=ask, mark=None, his_last_px=None,
                   would_side=None, would_qty=0, would_px=None, would_fill=None,
                   reason="no mark: book unreadable")
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
                             "frozen": 0, "skipped_markets": 0, "skipped_unmapped": 0,
                             "stale_snapshots": 0, "skipped_backoff": False, "ratio": {}}
    if now_ts < _backoff_until:
        stats["skipped_backoff"] = True
        return stats
    if await _db_switch_off(pool):
        stats["switched_off"] = True
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
