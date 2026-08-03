"""The AI trader's public track record — from the ACTUAL venue account.

The first version of the site read the engine's shadow mirror, which logs
intents (including paper ones). A track record must come from the account
that holds the money: every row here is a real position from the venue's
portfolio API, priced by its own trade activities, settled by its own
resolution activities. Nothing is inferred from our bookkeeping; our ledger
only ANNOTATES rows (edge, band) where it recognizes the market.

Windowed from `since` (default 2026-08-01, the account's first full day) on
ENTRY time — the first trade the venue reports for the market. A row
without a venue-reported entry inside the window is excluded rather than
guessed into it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from ..config import settings
from .pmus_account import _act_ts, _amt

_raw_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_RAW_TTL = 30.0
_lock = asyncio.Lock()

DEFAULT_SINCE = "2026-08-01"

# Slug grammar: {kind}-{league}-{teams...}-{date}-{qualifiers...}
#   atc   team contract (moneyline / segment h2h)   aec  event contract
#   asc   spread        tsc  total                  astatc  player prop
LEAGUE_SPORT: dict[str, tuple[str, str]] = {
    "mlb": ("Baseball", "⚾"), "nba": ("Basketball", "🏀"),
    "wnba": ("Basketball", "🏀"), "cbb": ("Basketball", "🏀"),
    "nfl": ("Football", "🏈"), "cfb": ("Football", "🏈"),
    "nhl": ("Hockey", "🏒"), "atp": ("Tennis", "🎾"), "wta": ("Tennis", "🎾"),
}
_SEGMENTS = {"f5", "h1", "h2", "p1", "q1"} | {f"i{i}" for i in range(1, 10)}


def classify_slug(slug: str) -> dict:
    """(sport, icon, category) from the venue's own naming. Pure; tested."""
    parts = (slug or "").split("-")
    kind = parts[0] if parts else ""
    league = parts[1] if len(parts) > 1 else ""
    sport, icon = LEAGUE_SPORT.get(league, ("Soccer", "⚽"))
    if league in ("lmx", "epl", "ucl", "ekst", "els", "lpa", "pdc", "scp",
                  "alsv", "bra", "arg", "mex"):
        sport, icon = "Soccer", "⚽"
    if kind == "astatc":
        cat = "Player Prop"
    elif kind == "asc":
        cat = "Spread"
    elif kind == "tsc":
        cat = "Total"
    elif any(p in _SEGMENTS for p in parts[2:]):
        cat = "Segment"
    else:
        cat = "Moneyline"
    return {"sport": sport, "icon": icon, "category": cat, "league": league}


def build(positions: dict[str, dict], activities: list[dict],
          since_ts: float, max_stake: float | None = None,
          attributed: set[str] | None = None,
          copy_slugs: set[str] | None = None) -> dict:
    """Pure builder (unit-tested): venue payloads -> the track record.

    `max_stake` caps what the RECORD presents: positions whose cost exceeds
    it are excluded from every figure — and DISCLOSED, as a count and a net
    P&L, in the payload. The site's whole credibility claim is "read from
    the account, nothing edited by hand"; an exclusion the reader cannot
    see would make that claim a lie, so the exclusion always travels with
    the record it modifies. Rationale for having the cap at all: the
    strategy trades $1-$5 tickets, and anything far above that is an
    execution incident or a non-strategy trade, not the strategy.
    """
    # Entry facts come from the venue's TRADE activities: first trade time,
    # volume-weighted entry price, buy count.
    entries: dict[str, dict] = {}
    for act in activities or []:
        if act.get("type") != "ACTIVITY_TYPE_TRADE":
            continue
        t = act.get("trade") or {}
        slug = t.get("marketSlug")
        if not slug:
            continue
        ts = _act_ts(act) or _act_ts(t)
        qty, price = _amt(t.get("qty")), _amt(t.get("price"))
        e = entries.setdefault(slug, {"first_ts": ts, "qty": 0.0,
                                      "notional": 0.0, "fills": 0})
        if ts and (not e["first_ts"] or ts < e["first_ts"]):
            e["first_ts"] = ts
        if qty > 0 and 0 < price < 1:
            e["qty"] += qty
            e["notional"] += qty * price
            e["fills"] += 1

    # Resolutions carry the settlement FACTS, not just the timestamp: the
    # venue REMOVES resolved markets from the positions payload, so for a
    # settled trade the resolution activity is often the only record of its
    # cost and realized P&L. Missing this is how a record shows "$0 settled"
    # while the account has realized money — observed live 2026-08-02.
    resolutions: dict[str, dict] = {}
    for act in activities or []:
        if act.get("type") != "ACTIVITY_TYPE_POSITION_RESOLUTION":
            continue
        res = act.get("positionResolution") or {}
        slug = res.get("marketSlug")
        if not slug:
            continue
        after = res.get("afterPosition") or {}
        before = res.get("beforePosition") or {}
        resolutions[slug] = {
            "ts": _act_ts(act),
            "realized": _amt(after.get("realized")) or _amt(before.get("realized")),
            "cost": _amt(before.get("cost")),
            "title": (after.get("marketMetadata") or {}).get("title")
                     or (before.get("marketMetadata") or {}).get("title"),
        }

    rows = []
    undatable = 0
    over_limit = {"count": 0, "stake": 0.0, "net_pnl": 0.0, "open": 0}
    # Provenance (2026-08-02): a size cap alone let every non-engine fill
    # under $100 wear the AI's record — the arb-bug cohort did exactly
    # that. When the caller supplies the engine's own claimed slugs
    # (`attributed`, from its mirrored fills) the record requires POSITIVE
    # attribution; the copy sleeve's slugs (`copy_slugs`, from its audit
    # table) are its own cohort and excluded first. Both exclusions are
    # disclosed with the same honesty contract as the size cap.
    unattributed = {"count": 0, "stake": 0.0, "net_pnl": 0.0, "open": 0}
    copy_sleeve = {"count": 0, "stake": 0.0, "net_pnl": 0.0, "open": 0}

    def _excluded_bucket(slug: str, stake_now: float):
        if copy_slugs is not None and slug in copy_slugs:
            return copy_sleeve
        if attributed is not None and slug not in attributed:
            return unattributed
        if max_stake is not None and stake_now > max_stake:
            return over_limit
        return None

    seen: set[str] = set()
    for slug, p in (positions or {}).items():
        seen.add(slug)
        meta = p.get("marketMetadata") or {}
        qty = _amt(p.get("netPosition"))
        settled = bool(p.get("expired")) or qty <= 0
        e = entries.get(slug) or {}
        entry_ts = e.get("first_ts") or 0.0
        if not entry_ts:
            # The venue reported no datable trade for this position in the
            # activity pages we fetched. A row that cannot be windowed is
            # excluded and COUNTED, never guessed into a date.
            undatable += 1
            continue
        if entry_ts < since_ts:
            continue          # pre-window entry: excluded, not re-dated
        cost = _amt(p.get("cost"))
        value = _amt(p.get("cashValue"))
        realized = _amt(p.get("realized"))
        res = resolutions.get(slug)
        if res:
            # The resolution activity is the settlement record; the position
            # row can lag it (realized still 0 after the market resolves).
            settled = True
            realized = res["realized"] or realized
            cost = cost or res["cost"]
        stake_now = cost if cost > 0 else e.get("notional", 0.0)
        bucket = _excluded_bucket(slug, stake_now)
        if bucket is not None:
            bucket["count"] += 1
            bucket["stake"] += stake_now
            if settled:
                bucket["net_pnl"] += realized
            else:
                bucket["open"] += 1
            continue
        vwap = (e.get("notional", 0) / e["qty"]) if e.get("qty") else None
        rows.append({
            "market_slug": slug,
            "title": meta.get("title") or slug,
            "outcome": meta.get("outcome"),
            **classify_slug(slug),
            "entry_ts": entry_ts or None,
            "entry_date": (datetime.fromtimestamp(entry_ts, timezone.utc)
                           .strftime("%Y-%m-%d") if entry_ts else None),
            "entry_price": round(vwap, 4) if vwap else None,
            "fills": e.get("fills", 0),
            "qty": qty if not settled else e.get("qty", 0.0),
            "stake": round(cost if cost > 0 else e.get("notional", 0.0), 4),
            "value": round(value, 4),
            "settled": settled,
            "settled_ts": res["ts"] if res else None,
            "pnl": round(realized, 4) if settled else None,
            "unrealized": round(value - cost, 4) if not settled else None,
        })

    # Settled markets the positions payload no longer carries at all: build
    # their rows from the resolution + their own entry trades.
    for slug, res in resolutions.items():
        if slug in seen:
            continue
        e = entries.get(slug) or {}
        entry_ts = e.get("first_ts") or 0.0
        if not entry_ts:
            undatable += 1
            continue
        if entry_ts < since_ts:
            continue
        cost = res["cost"] or e.get("notional", 0.0)
        bucket = _excluded_bucket(slug, cost)
        if bucket is not None:
            bucket["count"] += 1
            bucket["stake"] += cost
            bucket["net_pnl"] += res["realized"]
            continue
        vwap = (e.get("notional", 0) / e["qty"]) if e.get("qty") else None
        rows.append({
            "market_slug": slug,
            "title": res.get("title") or slug,
            "outcome": None,
            **classify_slug(slug),
            "entry_ts": entry_ts,
            "entry_date": datetime.fromtimestamp(entry_ts, timezone.utc)
                          .strftime("%Y-%m-%d"),
            "entry_price": round(vwap, 4) if vwap else None,
            "fills": e.get("fills", 0),
            "qty": e.get("qty", 0.0),
            "stake": round(cost, 4),
            "value": 0.0,
            "settled": True,
            "settled_ts": res["ts"] or None,
            "pnl": round(res["realized"], 4),
            "unrealized": None,
        })
    rows.sort(key=lambda r: -(r["entry_ts"] or 0))

    settled_rows = [r for r in rows if r["settled"]]
    wins = [r for r in settled_rows if (r["pnl"] or 0) > 0]
    losses = [r for r in settled_rows if (r["pnl"] or 0) < 0]
    deployed = sum(r["stake"] for r in rows)
    settled_stake = sum(r["stake"] for r in settled_rows)
    net = sum(r["pnl"] or 0 for r in settled_rows)

    # Daily series. Deployment buckets on entry day (always known inside the
    # window); realized P&L buckets on the venue's resolution day where it
    # gave one, else the entry day, FLAGGED — never silently "today".
    daily: dict[str, dict] = {}
    for r in rows:
        if not r["entry_date"]:
            continue
        d = daily.setdefault(r["entry_date"], {
            "date": r["entry_date"], "deployed": 0.0, "trades": 0,
            "pnl": 0.0, "settled": 0, "wins": 0, "pnl_estimated": False})
        d["deployed"] += r["stake"]
        d["trades"] += 1
    for r in settled_rows:
        ts = r["settled_ts"] or r["entry_ts"]
        if not ts:
            continue
        day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        d = daily.setdefault(day, {
            "date": day, "deployed": 0.0, "trades": 0,
            "pnl": 0.0, "settled": 0, "wins": 0, "pnl_estimated": False})
        d["pnl"] += r["pnl"] or 0
        d["settled"] += 1
        if (r["pnl"] or 0) > 0:
            d["wins"] += 1
        if not r["settled_ts"]:
            d["pnl_estimated"] = True

    for d in daily.values():
        d["deployed"] = round(d["deployed"], 2)
        d["pnl"] = round(d["pnl"], 2)

    return {
        "since": datetime.fromtimestamp(since_ts, timezone.utc)
                 .strftime("%Y-%m-%d"),
        "generated_at": time.time(),
        "summary": {
            "trades": len(rows),
            "open": len(rows) - len(settled_rows),
            "settled": len(settled_rows),
            "wins": len(wins),
            # zero-realized settlements are pushes/voids, not losses
            "losses": len(losses),
            "deployed": round(deployed, 2),
            "open_value": round(sum(r["value"] for r in rows
                                    if not r["settled"]), 2),
            "net_pnl": round(net, 2),
            "settled_stake": round(settled_stake, 2),
            "roi": round(net / settled_stake, 4) if settled_stake else None,
            "win_rate": (round(len(wins) / len(settled_rows), 4)
                         if settled_rows else None),
        },
        "excluded_undatable": undatable,
        # Always present when a cap was applied, even at zero exclusions —
        # the reader can see the rule itself, not only its effects.
        "excluded_over_limit": (
            {"limit": max_stake,
             "count": over_limit["count"],
             "open": over_limit["open"],
             "stake": round(over_limit["stake"], 2),
             "net_pnl": round(over_limit["net_pnl"], 2)}
            if max_stake is not None else None),
        # Positions the engine's own mirror does not claim. When attribution
        # is active this is where non-engine activity (the arb-bug cohort,
        # anything unexplained) lands — visible, never blended in.
        "excluded_unattributed": (
            {"count": unattributed["count"],
             "open": unattributed["open"],
             "stake": round(unattributed["stake"], 2),
             "net_pnl": round(unattributed["net_pnl"], 2)}
            if attributed is not None else None),
        # The whale-copy sleeve's positions: its own strategy, its own
        # cohort, never counted as the engine's.
        "excluded_copy_sleeve": (
            {"count": copy_sleeve["count"],
             "open": copy_sleeve["open"],
             "stake": round(copy_sleeve["stake"], 2),
             "net_pnl": round(copy_sleeve["net_pnl"], 2)}
            if copy_slugs is not None else None),
        "daily": sorted(daily.values(), key=lambda d: d["date"]),
        "trades": rows,
    }


def _fetch_raw() -> dict:
    from polymarket_us import PolymarketUS

    cfg = settings()
    client = PolymarketUS(key_id=cfg.pmus_key_id, secret_key=cfg.pmus_secret_key)
    positions: dict[str, dict] = {}
    cursor = ""
    for _ in range(8):
        resp = client.portfolio.positions(
            {"limit": 100, **({"cursor": cursor} if cursor else {})}) or {}
        positions.update(resp.get("positions") or {})
        cursor = resp.get("nextCursor") or ""
        if resp.get("eof") or not cursor:
            break
    acts: list[dict] = []
    cursor = ""
    # First fetch after a deploy digs DEEP (Aug 1's trade activities had
    # scrolled out of the shallow window before the permanent archive
    # existed, which made the record's first day undatable and dropped it
    # from the site — observed 2026-08-03). Once archived, history is
    # permanent, so later refreshes go back to the cheap shallow page-in.
    global _deep_swept
    # Deep sweep is OPT-IN (DEEP_SWEEP=1): it was a one-time recovery tool
    # for Aug 1's scrolled-out history, but as a per-boot default it ran on
    # EVERY restart — and on a restart-looping service that meant the
    # heaviest possible work exactly when memory was scarcest (2026-08-03,
    # ~90s kill cycles). Set the env, let one boot archive the history
    # permanently, then unset it.
    import os as _os

    deep_ok = _os.getenv("DEEP_SWEEP", "0") == "1"
    pages = 80 if (deep_ok and not _deep_swept) else 12
    for _ in range(pages):
        resp = client.portfolio.activities(
            {"limit": 100, "sortOrder": "SORT_ORDER_DESCENDING",
             "types": ["ACTIVITY_TYPE_TRADE",
                       "ACTIVITY_TYPE_POSITION_RESOLUTION"],
             **({"cursor": cursor} if cursor else {})}) or {}
        acts.extend(resp.get("activities") or [])
        cursor = resp.get("nextCursor") or ""
        if resp.get("eof") or not cursor:
            break
    _deep_swept = True
    return {"positions": positions, "activities": acts}


_deep_swept = False


_archive_ready = False
_archive_cache: dict[str, Any] = {"ts": 0.0, "data": None}


_archived_ids: set[str] = set()


async def _archive_and_union(acts: list[dict]) -> list[dict]:
    """Persist every venue activity ever seen; return the full archive.

    The venue's activity feed is a sliding window (we page ~1,200 rows,
    newest first). As trading accelerates, older TRADE and RESOLUTION
    activities scroll out of it — which made settled rows fall off the
    public record (181 -> 92 in one evening) and stripped entry timestamps
    from rows that were then excluded as undatable. A track record whose
    evidence DECAYS is not a track record. So every activity is archived
    on first sight, keyed by the venue's own id, and the record is built
    from the archive: it can only ever grow.

    CALLED ONCE PER RAW REFRESH, NEVER PER PAGE REQUEST. The first
    version ran on every request, unguarded: ~8,000 rows serialized and
    upserted per page view, concurrently for concurrent viewers — which
    strangled the event loop and OOM-flapped the API for an hour on
    2026-08-03 while every other suspect had already been eliminated.
    Serialization runs in a worker thread; already-archived ids are
    skipped via an in-process set seeded from the table.
    """
    global _archive_ready
    from ..db import get_pool

    pool = await get_pool()
    if not _archive_ready:
        await pool.execute(
            """CREATE TABLE IF NOT EXISTS pmus_activity_archive (
                   id text PRIMARY KEY,
                   ts double precision,
                   payload jsonb NOT NULL)""")
        known = await pool.fetch("SELECT id FROM pmus_activity_archive")
        _archived_ids.update(r["id"] for r in known)
        _archive_ready = True

    def _serialize() -> list[tuple]:
        out = []
        for a in acts or []:
            aid = str(a.get("id") or "")
            if not aid:
                aid = hashlib.sha256(json.dumps(a, sort_keys=True, default=str)
                                     .encode()).hexdigest()
            if aid in _archived_ids:
                continue
            out.append((aid, float(_act_ts(a) or 0.0),
                        json.dumps(a, default=str)))
        return out

    rows = await asyncio.to_thread(_serialize)
    if rows:
        await pool.executemany(
            "INSERT INTO pmus_activity_archive (id, ts, payload) "
            "VALUES ($1, $2, $3::jsonb) ON CONFLICT (id) DO NOTHING", rows)
        _archived_ids.update(r[0] for r in rows)
    stored = await pool.fetch("SELECT payload FROM pmus_activity_archive")

    def _parse() -> list[dict]:
        out: list[dict] = []
        for r in stored:
            p = r["payload"]
            out.append(json.loads(p) if isinstance(p, str) else p)
        return out

    parsed = await asyncio.to_thread(_parse)
    _archive_cache["data"], _archive_cache["ts"] = parsed, time.time()
    return parsed


async def warm_cache() -> None:
    """Run the first (deep) venue fetch at process boot, off the user path.

    Fail-soft: a failed warm just means the first visitor takes the slow
    path once."""
    try:
        await track_record()
        logging.getLogger(__name__).info("track-record cache warmed")
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("track-record warm failed")


async def track_record(since: str | None = None,
                       max_stake: float | None = None) -> dict:
    cfg = settings()
    if not (cfg.pmus_key_id and cfg.pmus_secret_key):
        return {"configured": False}
    try:
        since_ts = datetime.strptime(since or DEFAULT_SINCE, "%Y-%m-%d") \
            .replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        since_ts = datetime.strptime(DEFAULT_SINCE, "%Y-%m-%d") \
            .replace(tzinfo=timezone.utc).timestamp()
    # STALE-WHILE-REVALIDATE. The venue fetch is 20+ serial REST calls
    # (80+ on the post-deploy deep sweep) — 5-60 seconds. Holding the page
    # request for it made the site "take forever and honestly never load"
    # (owner, 2026-08-03). A snapshot that is 30-90 seconds old is
    # indistinguishable from live for a settlement-paced record, so: serve
    # the last good snapshot INSTANTLY, refresh in the background
    # (single-flight), and only ever block on the very first fetch of a
    # process — which warm_cache() runs at boot, before any user arrives.
    now = time.time()
    if _raw_cache["data"] is None:
        async with _lock:
            if _raw_cache["data"] is None:
                try:
                    _raw_cache["data"] = await asyncio.wait_for(
                        asyncio.to_thread(_fetch_raw), timeout=55)
                    _raw_cache["ts"] = time.time()
                    try:
                        await _archive_and_union(
                            _raw_cache["data"]["activities"])
                    except Exception:  # noqa: BLE001
                        logging.getLogger(__name__).exception(
                            "archive sync failed on cold fetch")
                except Exception as exc:  # noqa: BLE001 — surface, don't 500
                    return {"configured": True,
                            "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    elif now - _raw_cache["ts"] > _RAW_TTL and not _lock.locked():
        async def _bg_refresh() -> None:
            async with _lock:
                if time.time() - _raw_cache["ts"] <= _RAW_TTL:
                    return          # someone else already refreshed
                try:
                    _raw_cache["data"] = await asyncio.to_thread(_fetch_raw)
                    _raw_cache["ts"] = time.time()
                    # Archive travels WITH the refresh — once per new
                    # snapshot, never per page request.
                    await _archive_and_union(_raw_cache["data"]["activities"])
                except Exception:  # noqa: BLE001 — stale beats broken
                    logging.getLogger(__name__).exception(
                        "track-record background refresh failed; serving stale")

        asyncio.get_running_loop().create_task(_bg_refresh())
    raw = _raw_cache["data"]
    # The request path reads ONLY in-process caches — the archive is
    # synced by the refresh paths above, never by a page view.
    acts = _archive_cache["data"] or raw["activities"]
    attributed = copy_slugs = None
    try:
        from ..db import get_pool

        pool = await get_pool()
        eng = await pool.fetch(
            "SELECT DISTINCT outcome_id FROM engine_fills "
            "WHERE venue LIKE 'polymarket%'")
        # An EMPTY mirror means attribution is unavailable (mirror down or
        # never ran), not that the engine placed nothing — filtering on it
        # would zero the whole record. None disables the filter; the old
        # size-cap behavior stands until the mirror speaks.
        attributed = {r["outcome_id"] for r in eng} or None
        cp = await pool.fetch(
            "SELECT DISTINCT us_market_slug FROM live_orders "
            "WHERE us_market_slug IS NOT NULL")
        copy_slugs = {r["us_market_slug"] for r in cp}
    except Exception:  # noqa: BLE001 — provenance is an upgrade, not a gate
        attributed = copy_slugs = None
    return {"configured": True,
            **build(raw["positions"], acts, since_ts,
                    max_stake=max_stake, attributed=attributed,
                    copy_slugs=copy_slugs)}
