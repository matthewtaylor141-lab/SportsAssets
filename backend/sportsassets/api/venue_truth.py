"""Venue-truth P&L — the site's record rebuilt from the venues' own
ledgers (task #74, owner order 2026-08-17 night: "frontend shows exact
and current and true profitability numbers").

Two sources, zero internal bookkeeping:

* Polymarket US — the raw activities feed (every fill + every position
  resolution, verbatim).  Realized per market is the venue's own
  cumulative ``afterPosition.realized`` on the resolution row (it
  already includes earlier sells); unresolved markets contribute the
  sum of their sell-trade ``realizedPnl`` only when flat, and are open
  at cost otherwise.  This is the methodology the corrected 2026-08-17
  weekly report reconciled to the account balance.

* Kalshi — the raw fills + settlements export (``kalshi_export_raw``
  in the engine heartbeat).  The settlement row's cost components are
  NOT our cash for maker/mixed-side positions (proven 2026-08-17), so
  realized is rebuilt signed from cash flows: settlement revenue plus
  sell proceeds minus buy outlay minus exact venue fees, fills deduped
  by trade_id.

Everything here is a pure function of venue payloads so it can be
unit-tested without credentials; assembly/caching lives with the
endpoint.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from zoneinfo import ZoneInfo

RECORD_TZ = ZoneInfo("America/New_York")


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _et_day(ts: float) -> str:
    """UTC epoch -> ET calendar day (the site's reporting day)."""
    return datetime.fromtimestamp(ts, RECORD_TZ).strftime("%Y-%m-%d")


def _iso_ts(txt: str) -> float:
    """Venue ISO-8601 string -> epoch seconds (0.0 when unparseable)."""
    if not txt:
        return 0.0
    try:
        return datetime.fromisoformat(
            str(txt).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Kalshi: signed cash model over raw fills + settlements
# ---------------------------------------------------------------------------

def _fill_price_usd(f: dict) -> float:
    """Price of the side actually traded, dollar dialect first.

    A 'no' fill priced off yes_price would book 1-p instead of p —
    that sign error is exactly the class of defect this module exists
    to remove, so the side chooses the field explicitly.
    """
    side = str(f.get("side") or "yes").lower()
    if side == "no":
        if f.get("no_price_dollars") is not None:
            return _f(f["no_price_dollars"])
        return _f(f.get("no_price")) / 100.0
    if f.get("yes_price_dollars") is not None:
        return _f(f["yes_price_dollars"])
    return _f(f.get("yes_price")) / 100.0


def _fill_qty(f: dict) -> float:
    return _f(f.get("count_fp") or f.get("count"))


def _fee_usd(f: dict) -> float:
    if f.get("fee_dollars") is not None:
        return _f(f["fee_dollars"])
    return _f(f.get("fee")) / 100.0


def kalshi_positions(raw: dict) -> list[dict]:
    """Per-ticker cash truth from a raw export.

    Returns rows: ticker, cost (buy outlay), proceeds (sell credits),
    fees, revenue (settlement payout), realized (settled only),
    settled, settled_at, result.
    """
    per: dict[str, dict] = {}

    def _bucket(ticker: str) -> dict:
        return per.setdefault(ticker, {
            "ticker": ticker, "cost": 0.0, "proceeds": 0.0, "fees": 0.0,
            "revenue": 0.0, "bought": 0.0, "sold": 0.0,
            "settled": False, "settled_at": 0.0, "result": None,
        })

    seen_trades: set[str] = set()
    for f in (raw or {}).get("fills") or []:
        tid = str(f.get("trade_id") or "")
        # trade_id is the dedupe truth; a row without one (schema drift)
        # is kept — dropping it would silently understate cash.
        if tid:
            if tid in seen_trades:
                continue
            seen_trades.add(tid)
        ticker = str(f.get("ticker") or "")
        if not ticker:
            continue
        b = _bucket(ticker)
        qty, px = _fill_qty(f), _fill_price_usd(f)
        b["fees"] += _fee_usd(f)
        if str(f.get("action") or "buy").lower() == "sell":
            b["proceeds"] += qty * px
            b["sold"] += qty
        else:
            b["cost"] += qty * px
            b["bought"] += qty

    for s in (raw or {}).get("settlements") or []:
        ticker = str(s.get("ticker") or "")
        if not ticker:
            continue
        b = _bucket(ticker)
        if s.get("revenue_dollars") is not None:
            b["revenue"] += _f(s["revenue_dollars"])
        else:
            b["revenue"] += _f(s.get("revenue")) / 100.0
        b["settled"] = True
        b["settled_at"] = max(b["settled_at"],
                              _iso_ts(str(s.get("settled_time") or "")))
        b["result"] = s.get("market_result") or b["result"]

    rows = []
    for b in per.values():
        settled = b["settled"]
        # A settlement can arrive for a ticker whose fills predate the
        # export window; its buy cost is then unknown and the realized
        # number would be pure revenue. Flag rather than fabricate.
        window_complete = not (settled and b["bought"] == 0.0)
        realized = (round(b["revenue"] + b["proceeds"]
                          - b["cost"] - b["fees"], 2)
                    if settled else 0.0)
        rows.append({
            "ticker": b["ticker"],
            "cost": round(b["cost"], 2),
            "proceeds": round(b["proceeds"], 2),
            "fees": round(b["fees"], 4),
            "revenue": round(b["revenue"], 2),
            "settled": settled,
            "settled_at": b["settled_at"] or None,
            "result": b["result"],
            "realized": realized,
            "open": (not settled) and b["bought"] > b["sold"] + 1e-9,
            "window_complete": window_complete,
        })
    return rows


# ---------------------------------------------------------------------------
# Polymarket US: afterPosition.realized over raw activities
# ---------------------------------------------------------------------------

def pm_positions(activities: list[dict]) -> list[dict]:
    """Per-market cash truth from verbatim venue activity rows.

    Same aggregation the tennis-week report uses, market-universal:
    realized on resolved markets is the venue's own cumulative number
    from the resolution row; unresolved markets realize only their
    sell-trade P&L and stay open while shares remain.
    """
    per: dict[str, dict] = {}

    def _bucket(slug: str) -> dict | None:
        if not slug:
            return None
        return per.setdefault(slug, {
            "market_slug": slug, "title": None, "cost": 0.0,
            "sell_rp": 0.0, "res_realized": None, "resolved": False,
            "bought": 0.0, "sold": 0.0, "settled_at": 0.0,
        })

    def _amt(a: Any) -> float:
        if isinstance(a, dict):
            a = a.get("value")
        return _f(a)

    from .pmus_account import _any_ts  # timestamp wherever the venue put it

    for act in activities or []:
        kind = act.get("type")
        if kind == "ACTIVITY_TYPE_TRADE":
            t = act.get("trade") or {}
            b = _bucket(str(t.get("marketSlug") or ""))
            if b is None:
                continue
            meta = t.get("marketMetadata") or {}
            b["title"] = b["title"] or meta.get("title")
            qty, px = _amt(t.get("qty")), _amt(t.get("price"))
            rp = _amt(t.get("realizedPnl"))
            side = str(t.get("side") or "").upper()
            # side=None was the venue's observed dialect on some rows
            # (2026-08-17); a nonzero realizedPnl marks a sell there.
            if "SELL" in side or rp != 0:
                b["sell_rp"] += rp
                b["sold"] += qty
            else:
                b["cost"] += qty * px
                b["bought"] += qty
        elif kind == "ACTIVITY_TYPE_POSITION_RESOLUTION":
            res = act.get("positionResolution") or {}
            b = _bucket(str(res.get("marketSlug") or ""))
            if b is None:
                continue
            after = res.get("afterPosition") or {}
            before = res.get("beforePosition") or {}
            b["resolved"] = True
            b["res_realized"] = (_amt(after.get("realized"))
                                 or _amt(before.get("realized")))
            b["cost"] = b["cost"] or _amt(before.get("cost"))
            b["settled_at"] = max(b["settled_at"], _any_ts(act))
            meta = after.get("marketMetadata") or {}
            b["title"] = b["title"] or meta.get("title")

    import re as _re

    rows = []
    for b in per.values():
        resolved = b["resolved"]
        realized = (b["res_realized"] if resolved
                    else round(b["sell_rp"], 2))
        is_open = (not resolved) and b["bought"] > b["sold"] + 1e-9
        # The venue's raw resolution rows carry no usable timestamp
        # (observed 2026-08-19: every PM settlement bucketed 'undated').
        # The slug embeds the event date (aec-...-2026-08-19), which is
        # the reporting day the tennis-week report already keys on — a
        # stable fallback, labeled distinctly so it is never mistaken
        # for a venue-stamped settlement time.
        m = _re.search(r"(\d{4}-\d{2}-\d{2})", b["market_slug"])
        rows.append({
            "market_slug": b["market_slug"],
            "title": b["title"] or b["market_slug"],
            "cost": round(b["cost"], 2),
            "realized": round(realized or 0.0, 2),
            "settled": resolved,
            "settled_at": b["settled_at"] or None,
            "day_hint": m.group(1) if m else None,
            "open": is_open,
        })
    return rows


# ---------------------------------------------------------------------------
# Combined payload
# ---------------------------------------------------------------------------

def _venue_summary(rows: list[dict]) -> dict:
    settled = [r for r in rows if r["settled"]]
    open_rows = [r for r in rows if r.get("open")]
    return {
        "markets": len(rows),
        "settled": len(settled),
        "wins": sum(1 for r in settled if r["realized"] > 0),
        "losses": sum(1 for r in settled if r["realized"] <= 0),
        "realized": round(sum(r["realized"] for r in settled), 2),
        "settled_cost": round(sum(r["cost"] for r in settled), 2),
        "open": len(open_rows),
        "open_cost": round(sum(r["cost"] for r in open_rows), 2),
    }


def daily_series(rows: list[dict]) -> list[dict]:
    """Settled cash per ET day, newest first; undated rows surfaced as
    their own bucket (a data problem to show, never to guess into a
    day)."""
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        if not r["settled"]:
            continue
        ts = r.get("settled_at") or 0.0
        day = (_et_day(ts) if ts
               else r.get("day_hint") or "undated")
        buckets.setdefault(day, []).append(r)
    ordered = sorted((d for d in buckets if d != "undated"), reverse=True)
    if "undated" in buckets:
        ordered.append("undated")
    out = []
    for day in ordered:
        rs = buckets[day]
        out.append({
            "day": day,
            "settled": len(rs),
            "wins": sum(1 for r in rs if r["realized"] > 0),
            "losses": sum(1 for r in rs if r["realized"] <= 0),
            "cost": round(sum(r["cost"] for r in rs), 2),
            "realized": round(sum(r["realized"] for r in rs), 2),
        })
    return out


def build_payload(pm_rows: list[dict], kx_rows: list[dict],
                  since_day: str, *, pm_error: str | None = None,
                  kx_error: str | None = None) -> dict:
    """The venue-truth record the site serves: per-venue summaries, a
    combined scorecard, and a per-ET-day series across both venues."""
    kx_incomplete = [r for r in kx_rows
                     if not r.get("window_complete", True)]
    # Settlements whose buys predate the Kalshi export window would
    # report pure revenue as profit; they are excluded from every
    # aggregate and named in the payload so the gap is visible.
    kx_counted = [r for r in kx_rows if r.get("window_complete", True)]
    tagged = ([{**r, "venue": "polymarket-us"} for r in pm_rows]
              + [{**r, "venue": "kalshi"} for r in kx_counted])
    # Days before the window opened belong to frozen history, not this
    # rolling rebuild — a settlement backfilled by the venue with an
    # old stamp must not re-open a closed reporting day.
    def _settle_day(r: dict) -> str | None:
        if r.get("settled_at"):
            return _et_day(r["settled_at"])
        return r.get("day_hint")

    in_window = [r for r in tagged
                 if not r["settled"]
                 or (_settle_day(r) or since_day) >= since_day]
    settled = [r for r in in_window if r["settled"]]
    payload = {
        "methodology": "venue-truth",
        "since": since_day,
        "polymarket_us": ({"error": pm_error} if pm_error
                          else _venue_summary(
                              [r for r in in_window
                               if r["venue"] == "polymarket-us"])),
        "kalshi": ({"error": kx_error} if kx_error
                   else _venue_summary(
                       [r for r in in_window if r["venue"] == "kalshi"])),
        "total": {
            "settled": len(settled),
            "wins": sum(1 for r in settled if r["realized"] > 0),
            "losses": sum(1 for r in settled if r["realized"] <= 0),
            "realized": round(sum(r["realized"] for r in settled), 2),
            "settled_cost": round(sum(r["cost"] for r in settled), 2),
        },
        "daily": daily_series(in_window),
        "partial": bool(pm_error or kx_error),
    }
    if kx_incomplete:
        payload["kalshi_window_incomplete"] = {
            "n": len(kx_incomplete),
            "tickers": [r["ticker"] for r in kx_incomplete][:10],
        }
    return payload


# ---------------------------------------------------------------------------
# Assembly: live sources -> cached snapshot
# ---------------------------------------------------------------------------
#
# The PM activities crawl pages ~80 venue calls and can take minutes; a
# homepage request must never wait on it. Serve-stale semantics: the
# first request kicks off a background build and says so; afterwards
# requests get the last built payload immediately while a refresh runs
# at most once per TTL.

import asyncio
import json as _json
import time as _time

_TTL_S = 600.0
_snap: dict[str, Any] = {"ts": 0.0, "payload": None, "building": False}
_snap_lock = asyncio.Lock()

# Rolling window: Kalshi's raw export carries 15 days, so the rebuild
# window is 13 days (one day of slack each side) and never earlier than
# the account's first live-order day.
_WINDOW_DAYS = 13
_FLOOR_DAY = "2026-08-01"


def _since_day() -> str:
    rolling = _et_day(_time.time() - _WINDOW_DAYS * 86_400)
    return max(rolling, _FLOOR_DAY)


async def _kalshi_raw_from_heartbeat() -> tuple[dict | None, str | None]:
    """Latest kalshi_export_raw the engine shipped, plus why-not."""
    from ..db import get_pool

    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT detail, beat_at FROM service_heartbeats "
        "WHERE service='edge_engine'")
    if row is None:
        return None, "engine never reported"
    detail = row["detail"]
    if isinstance(detail, str):
        detail = _json.loads(detail)
    raw = (detail or {}).get("kalshi_export_raw")
    if not raw:
        return None, "no kalshi_export_raw in engine heartbeat"
    if raw.get("error"):
        return None, f"engine export error: {raw['error']}"
    beat = row["beat_at"]
    age = _time.time() - beat.timestamp() if hasattr(beat, "timestamp") else 0
    if age > 3 * 3600:
        # The export refreshes on the settle cadence (minutes); hours of
        # silence means the numbers are a snapshot of some earlier world
        # and must be labeled, not blended in as current.
        return raw, f"engine heartbeat stale ({int(age / 60)} min)"
    return raw, None


def persistable_day_rows(rows: list[dict], venue: str,
                         since_day: str) -> list[tuple]:
    """Upsert tuples (day, venue, settled, wins, losses, cost, realized)
    for one venue's rows — in-window ET days only. Undated settlements
    are a data problem to display, never rows to freeze into history."""
    per_day = daily_series([r for r in rows
                            if r.get("window_complete", True)])
    out = []
    for d in per_day:
        if d["day"] == "undated" or d["day"] < since_day:
            continue
        out.append((d["day"], venue, d["settled"], d["wins"],
                    d["losses"], d["cost"], d["realized"]))
    return out


async def _persist_days(pm_rows: list[dict], kx_rows: list[dict],
                        since: str, pm_ok: bool, kx_ok: bool) -> None:
    """Upsert the live window's per-day aggregates. A venue that failed
    to fetch must not overwrite its rows with zeros — only venues that
    actually produced data write."""
    from ..db import get_pool

    tuples = []
    if pm_ok:
        tuples += persistable_day_rows(pm_rows, "polymarket-us", since)
    if kx_ok:
        tuples += persistable_day_rows(kx_rows, "kalshi", since)
    if not tuples:
        return
    pool = await get_pool()
    await pool.executemany(
        """
        INSERT INTO venue_truth_days
            (day, venue, settled, wins, losses, cost, realized, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, now())
        ON CONFLICT (day, venue) DO UPDATE SET
            settled = EXCLUDED.settled, wins = EXCLUDED.wins,
            losses = EXCLUDED.losses, cost = EXCLUDED.cost,
            realized = EXCLUDED.realized, updated_at = now()
        """, tuples)


async def _frozen_days(before_day: str) -> list[dict]:
    """History the window has rolled past — read-only from the ledger."""
    from ..db import get_pool

    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT day, venue, settled, wins, losses, cost, realized "
        "FROM venue_truth_days WHERE day < $1 ORDER BY day DESC",
        before_day)
    return [dict(r) for r in rows]


async def _build() -> dict:
    since = _since_day()
    kx_raw, kx_err = await _kalshi_raw_from_heartbeat()
    kx_rows = kalshi_positions(kx_raw) if kx_raw else []

    from .pmus_account import _week_activities
    pm_rows: list[dict] = []
    pm_err: str | None = None
    try:
        acts = await _week_activities(since)
        pm_rows = pm_positions(acts)
    except Exception as exc:  # noqa: BLE001 — name it, never 500
        pm_err = f"{type(exc).__name__}: {str(exc)[:200]}"

    payload = build_payload(pm_rows, kx_rows, since,
                            pm_error=pm_err,
                            kx_error=None if kx_rows else kx_err)
    if kx_rows and kx_err:
        payload["kalshi_note"] = kx_err   # stale-but-served, labeled

    # Accrete: freeze this window's days into the ledger, then splice
    # rolled-past history in front of the live window so the record
    # never shortens as the sources roll forward.
    try:
        await _persist_days(pm_rows, kx_rows, since,
                            pm_ok=pm_err is None, kx_ok=bool(kx_rows))
        frozen = await _frozen_days(since)
    except Exception as exc:  # noqa: BLE001 — a DB flake must not
        # take down the live rebuild; the payload just loses history.
        payload["history_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        frozen = []
    if frozen:
        by_day: dict[str, dict] = {}
        for r in frozen:
            d = by_day.setdefault(r["day"], {
                "day": r["day"], "settled": 0, "wins": 0, "losses": 0,
                "cost": 0.0, "realized": 0.0})
            for k in ("settled", "wins", "losses"):
                d[k] += r[k]
            d["cost"] = round(d["cost"] + r["cost"], 2)
            d["realized"] = round(d["realized"] + r["realized"], 2)
        payload["frozen_days"] = sorted(by_day.values(),
                                        key=lambda d: d["day"], reverse=True)
        t = payload["total"]
        payload["all_time"] = {
            "since": min(by_day),
            "settled": t["settled"] + sum(d["settled"]
                                          for d in by_day.values()),
            "wins": t["wins"] + sum(d["wins"] for d in by_day.values()),
            "losses": t["losses"] + sum(d["losses"]
                                        for d in by_day.values()),
            "realized": round(t["realized"]
                              + sum(d["realized"]
                                    for d in by_day.values()), 2),
            "settled_cost": round(t["settled_cost"]
                                  + sum(d["cost"]
                                        for d in by_day.values()), 2),
        }
    payload["as_of"] = _time.time()
    return payload


async def _refresh() -> None:
    try:
        payload = await _build()
        _snap.update(ts=_time.time(), payload=payload)
    finally:
        _snap["building"] = False


async def snapshot() -> dict:
    """The endpoint surface: last built payload immediately; refresh in
    the background when past TTL; honest 'building' on cold start."""
    async with _snap_lock:
        now = _time.time()
        if (_snap["payload"] is None or now - _snap["ts"] > _TTL_S) \
                and not _snap["building"]:
            _snap["building"] = True
            asyncio.get_running_loop().create_task(_refresh())
        if _snap["payload"] is None:
            return {"methodology": "venue-truth", "building": True,
                    "since": _since_day()}
        out = dict(_snap["payload"])
        out["age_s"] = round(now - _snap["ts"], 1)
        return out
