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
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
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
# a previous plan older than this is not judged against this tick's book
JUDGE_MAX_AGE_S = 3.0 * POLL_S
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
               t.outcome, t.outcome_index
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
    return int(net) if net >= 0 else -int(-net)


async def account_positions(pmus) -> dict[str, float] | None:
    """ONE paced walk of the venue account's positions per tick:
    {slug (lower): signed netPosition}. None when the walk failed -- a
    market absent from a successful walk is simply not held (0)."""
    def _walk() -> dict[str, float]:
        client = pmus._get_client()
        out: dict[str, float] = {}
        cursor = ""
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
                break
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
        return row
    la, oa, slug = m["long_asset"], m["other_asset"], m["us_slug"]
    his_long = float(pos.get(la, 0.0)) if la else 0.0
    his_other = float(pos.get(oa, 0.0)) if oa else 0.0
    net = mi.his_net(his_long, his_other)
    fresh_snap = snap_age_s is not None and snap_age_s <= SNAP_MAX_AGE_S

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
    venue = None if positions is None else float(positions.get(slug.lower(), 0.0))
    try:
        ledger = await ledger_net(pool, slug)
    except Exception:  # noqa: BLE001
        ledger = 0
    venue_int = None if venue is None else (int(venue) if venue >= 0 else -int(-venue))
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
    p = mi.plan(int(tgt["target"]), int(ledger), venue_int,
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
    return row


async def _resolve_previous(pool, row: dict) -> None:
    """WOULD IT HAVE FILLED? The order the previous tick would have
    rested is judged against THIS tick's book (review round one): a buy
    resting at px filled only if the ask has come down to px; a sell at
    px only if the bid has come up to px. The opposite side has to REACH
    our price. "The book moved past our level" is not counted: market
    makers reprice by cancel-and-replace, so a level that vanished was
    as likely pulled as taken, and counting it would flatter the rate
    that gates P1. Written onto the previous row; a market with no later
    reading of the side we need stays unresolved (NULL), never counted
    either way. ONLY A RECENT PLAN IS JUDGED (review round two): a plan
    older than JUDGE_MAX_AGE_S -- a market that fell out of the per-tick
    cap or the lookback and came back hours later, after the game moved
    -- is one thirty-second observation, not a day's; it stays NULL.
    Returns the verdict (True / False) or None when nothing was judged.
    Best-effort."""
    bid, ask = row.get("bid"), row.get("ask")
    if bid is None and ask is None:
        return None
    try:
        prev = await pool.fetchrow(
            "SELECT id, would_side, would_px FROM mirror_shadow "
            "WHERE whale = $1 AND condition_id = $2 AND would_side IS NOT NULL "
            "AND would_px IS NOT NULL AND would_fill IS NULL "
            "AND at >= now() - ($3::float8 * interval '1 second') "
            "ORDER BY at DESC LIMIT 1 /* prev-plan */",
            row.get("whale"), row.get("condition_id"), float(JUDGE_MAX_AGE_S))
    except Exception:  # noqa: BLE001 — table absent until 046
        return None
    if prev is None:
        return None
    px = float(prev["would_px"])
    if str(prev["would_side"]) == "BUY_LONG":
        if ask is None:
            return None                 # the side that has to reach us is unread
        filled = float(ask) <= px
    else:
        if bid is None:
            return None
        filled = float(bid) >= px
    try:
        await pool.execute("UPDATE mirror_shadow SET would_fill = $2 WHERE id = $1",
                           prev["id"], bool(filled))
    except Exception:  # noqa: BLE001
        return None
    return bool(filled)


async def _write(pool, row: dict) -> bool | None:
    """Judge the previous plan for this market, then land the row.
    Returns the previous plan's verdict (see _resolve_previous)."""
    verdict = await _resolve_previous(pool, row)
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
                             "frozen": 0, "skipped_markets": 0, "stale_snapshots": 0,
                             "skipped_backoff": False, "ratio": {}}
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
            if row.get("would_side"):
                stats["would_orders"] += 1
                if (row.get("detail") or {}).get("marketable_now"):
                    stats["marketable_now"] += 1
            if str(row.get("reason") or "").startswith("frozen"):
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
                verdict = await _write(pool, row)
                stats["rows"] += 1
                if verdict is not None:
                    stats["resolved"] += 1
                    if verdict:
                        stats["resolved_filled"] += 1
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
