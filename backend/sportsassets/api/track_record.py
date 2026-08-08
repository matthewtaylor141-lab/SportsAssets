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
from zoneinfo import ZoneInfo

from ..config import settings
from .pmus_account import _act_ts, _amt

_raw_cache: dict[str, Any] = {"ts": 0.0, "data": None}
# The record is settlement-paced: a 3-minute snapshot is indistinguishable
# from live, and this TTL cuts the API's venue call volume ~6x — the
# engine's settlement and order calls share the venue's rate budget, and
# those are the calls that make money (audit 2026-08-04).
_RAW_TTL = 180.0
_lock = asyncio.Lock()
# Refresh health, surfaced in the payload: a snapshot that quietly stopped
# refreshing must be VISIBLE as stale, not indistinguishable from live.
_refresh_health: dict[str, Any] = {"error": None, "error_at": 0.0,
                                   "streak": 0}

DEFAULT_SINCE = "2026-08-01"

# Owner directive 2026-08-06: no single trade may move any displayed P&L
# by more than this, either direction — at $3-5 clips a $100+ swing is an
# anomaly, not the strategy. Applied by default to every served record
# (and to the daily-breakdown copy query); excluded rows are disclosed in
# `excluded_over_pnl`, never silently dropped.
PNL_DISPLAY_CAP = 100.0

# All human-facing DAY bucketing happens in the owner's timezone. Bucketing
# in UTC put every settlement after 8pm Eastern on the NEXT day's calendar
# box (Wednesday wore Tuesday night's -$16 two days running, owner report
# 2026-08-05): a "day" on the Performance page means an Eastern day.
RECORD_TZ = ZoneInfo("America/New_York")

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
          copy_slugs: set[str] | None = None,
          max_abs_pnl: float | None = None,
          manual_slugs: set[str] | None = None,
          tz: ZoneInfo = RECORD_TZ) -> dict:
    """Pure builder (unit-tested): venue payloads -> the track record.

    `max_stake` caps what the RECORD presents: positions whose cost exceeds
    it are excluded from every figure — and DISCLOSED, as a count and a net
    P&L, in the payload. The site's whole credibility claim is "read from
    the account, nothing edited by hand"; an exclusion the reader cannot
    see would make that claim a lie, so the exclusion always travels with
    the record it modifies. Rationale for having the cap at all: the
    strategy trades $1-$5 tickets, and anything far above that is an
    execution incident or a non-strategy trade, not the strategy.

    `max_abs_pnl` (owner directive 2026-08-06): a single trade moving the
    P&L by more than this — either direction, realized or open
    mark-to-market — is excluded from every displayed figure. At $3-5
    clips a $100+ single-trade swing is an anomaly (mis-attribution, a
    venue data glitch, a runaway fill), not the strategy; letting one
    such row through would swamp the record it sits in. Highest
    precedence of the exclusions (a $100+ swing is out no matter which
    cohort it belongs to, copy sleeve included) and disclosed like the
    others, in `excluded_over_pnl`.
    """
    # The record's first calendar day is the UTC date the window opens
    # (2026-08-01), even though that instant is 8pm ET July 31: the
    # account's opening session settled Jul 31 evening ET, and it belongs
    # to day 1 of the record, not to a phantom July box the month view
    # never shows (owner report 2026-08-05: "+$45 missing from Aug 1").
    # Only dates BEFORE this fold forward; every later day stays Eastern.
    first_day = datetime.fromtimestamp(since_ts, timezone.utc) \
        .strftime("%Y-%m-%d")

    # Entry facts come from the venue's TRADE activities: first trade time,
    # volume-weighted entry price, buy count. SELLS are their own ledger
    # (owner directive 2026-08-08: cashed-out trades must count toward
    # P&L): a sell must not inflate the entry VWAP, and its realizedPnl
    # is the cash-out's settlement record. A trade is a sell when the
    # venue says so or — enum-spelling-proof — when it carries realized
    # P&L, which a buy under average-cost accounting never does.
    entries: dict[str, dict] = {}
    sold: dict[str, dict] = {}
    for act in activities or []:
        if act.get("type") != "ACTIVITY_TYPE_TRADE":
            continue
        t = act.get("trade") or {}
        slug = t.get("marketSlug")
        if not slug:
            continue
        ts = _act_ts(act) or _act_ts(t)
        qty, price = _amt(t.get("qty")), _amt(t.get("price"))
        rp = _amt(t.get("realizedPnl"))
        is_sell = "SELL" in str(t.get("side") or "").upper() or rp != 0
        if is_sell:
            if qty > 0 and 0 < price < 1:
                s = sold.setdefault(slug, {"qty": 0.0, "proceeds": 0.0,
                                           "realized": 0.0, "last_ts": 0.0})
                s["qty"] += qty
                s["proceeds"] += qty * price
                s["realized"] += rp
                if ts:
                    s["last_ts"] = max(s["last_ts"], ts)
            continue
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

    # VENUE-BASIS totals (owner directive 2026-08-08, second report: the
    # headline must match the venue app's own number). The venue stamps
    # cumulative realized P&L on every position row — no windowing, no
    # entry-dating. Summing that (plus resolutions/sells for rows the
    # payload no longer carries) reproduces the venue's own arithmetic;
    # the dated-row machinery below still builds the ledger, calendar
    # and cohorts, but the HEADLINE is this.
    venue_totals = {"net_pnl": 0.0, "settled": 0, "wins": 0, "losses": 0,
                    "settled_stake": 0.0}
    _vseen: set[str] = set()
    for _slug, _p in (positions or {}).items():
        _vseen.add(_slug)
        _r = _amt(_p.get("realized"))
        _res = resolutions.get(_slug)
        if _res and not _r:
            _r = _res["realized"]
        if not _r and _slug in sold \
                and _amt(_p.get("netPosition")) <= 0:
            # Sold to zero, venue's cumulative realized lagging: the sell
            # ledger is the sale's own settlement record.
            _s = sold[_slug]
            _r = _s["realized"] or (_s["proceeds"]
                                    - (entries.get(_slug) or {})
                                    .get("notional", 0.0))
        venue_totals["net_pnl"] += _r
        if bool(_p.get("expired")) or _amt(_p.get("netPosition")) <= 0 \
                or _res:
            venue_totals["settled"] += 1
            venue_totals["settled_stake"] += (
                _amt(_p.get("cost")) or (_res["cost"] if _res else 0.0))
            if _r > 0:
                venue_totals["wins"] += 1
            elif _r < 0:
                venue_totals["losses"] += 1
    for _slug, _res in resolutions.items():
        if _slug in _vseen:
            continue
        venue_totals["net_pnl"] += _res["realized"]
        venue_totals["settled"] += 1
        venue_totals["settled_stake"] += _res["cost"]
        if _res["realized"] > 0:
            venue_totals["wins"] += 1
        elif _res["realized"] < 0:
            venue_totals["losses"] += 1
    for _slug, _s in sold.items():
        if _slug in _vseen or _slug in resolutions:
            continue
        _e = entries.get(_slug) or {}
        _r = _s["realized"] or (_s["proceeds"] - _e.get("notional", 0.0))
        venue_totals["net_pnl"] += _r
        venue_totals["settled"] += 1
        venue_totals["settled_stake"] += _e.get("notional", 0.0)
        if _r > 0:
            venue_totals["wins"] += 1
        elif _r < 0:
            venue_totals["losses"] += 1
    for _k in ("net_pnl", "settled_stake"):
        venue_totals[_k] = round(venue_totals[_k], 2)

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
    over_pnl = {"count": 0, "stake": 0.0, "net_pnl": 0.0, "open": 0}
    # Admin-directed trades (owner directive 2026-08-07): REAL account
    # money, but a human's picks — never the AI's record. Excluded from
    # every displayed figure, disclosed like the other exclusions, and
    # checked FIRST so a manual row can't leak into any other cohort.
    manual = {"count": 0, "stake": 0.0, "net_pnl": 0.0, "open": 0}
    # Daily slice of the unattributed cohort (owner directive 2026-08-06:
    # unattributed results count toward the software category in the daily
    # breakdown report — an aggregate-only bucket can't be day-bucketed).
    unattributed_daily: dict[str, dict] = {}

    def _tally_unattributed_day(settled: bool, realized: float,
                                ts: float | None):
        if not settled or not ts:
            return
        day = max(datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d"),
                  first_day)
        d = unattributed_daily.setdefault(
            day, {"date": day, "pnl": 0.0, "settled": 0, "wins": 0})
        d["pnl"] = round(d["pnl"] + realized, 4)
        d["settled"] += 1
        if realized > 0:
            d["wins"] += 1

    def _pnl_capped(settled: bool, realized: float, unreal: float) -> bool:
        if max_abs_pnl is None:
            return False
        return abs(realized if settled else unreal) > max_abs_pnl

    def _excluded_bucket(slug: str, stake_now: float, settled: bool,
                         realized: float, unreal: float):
        # Manual-desk rows first: a human's ticket is manual money no
        # matter its size or attribution state.
        if manual_slugs is not None and slug in manual_slugs:
            return manual
        # The P&L cap outranks the remaining splits: a $100+ single-trade
        # swing is out of the record no matter whose row it is.
        if _pnl_capped(settled, realized, unreal):
            return over_pnl
        if attributed is not None and slug not in attributed:
            return unattributed
        if max_stake is not None and stake_now > max_stake:
            return over_limit
        return None

    def _sleeve_of(slug: str) -> str:
        """Copy-sleeve rows are PART of the record (owner decision
        2026-08-04: the copy sleeve is the AI's trading too — presenting
        engine-only understated real volume to the point of reading as
        wrong). They stay tagged, and their cohort stats still travel
        separately so per-sleeve grading survives the merge."""
        return ("copy" if copy_slugs is not None and slug in copy_slugs
                else "engine")

    def _tally_copy(stake_now: float, settled: bool, realized: float):
        copy_sleeve["count"] += 1
        copy_sleeve["stake"] += stake_now
        if settled:
            copy_sleeve["net_pnl"] += realized
        else:
            copy_sleeve["open"] += 1

    def _cohort_name(bucket: dict) -> str:
        return ("manual" if bucket is manual
                else "over_pnl" if bucket is over_pnl
                else "unattributed" if bucket is unattributed
                else "over_limit")

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
        s = sold.get(slug)
        cashed_out = bool(settled and not res and not p.get("expired") and s)
        if cashed_out:
            # Sold to zero before resolution: the sale IS the settlement.
            # The venue's own cumulative realized is authoritative when it
            # has caught up; the sell trades' realizedPnl covers the lag.
            realized = realized or s["realized"] \
                or (s["proceeds"] - e.get("notional", 0.0))
            cost = cost or e.get("notional", 0.0)
        stake_now = cost if cost > 0 else e.get("notional", 0.0)
        unreal = (value - cost) if not settled else 0.0
        is_manual = manual_slugs is not None and slug in manual_slugs
        sleeve = _sleeve_of(slug)
        cohort = "record"
        if sleeve == "copy" and not is_manual \
                and not _pnl_capped(settled, realized, unreal):
            _tally_copy(stake_now, settled, realized)
        else:
            bucket = (manual if is_manual
                      else over_pnl if sleeve == "copy"
                      else _excluded_bucket(slug, stake_now, settled,
                                            realized, unreal))
            if bucket is not None:
                bucket["count"] += 1
                bucket["stake"] += stake_now
                if settled:
                    bucket["net_pnl"] += realized
                else:
                    bucket["open"] += 1
                if bucket is unattributed:
                    _tally_unattributed_day(
                        settled, realized,
                        (res["ts"] if res else None) or entry_ts)
                # OWNER DIRECTIVE 2026-08-08: the whole account is THE
                # P&L metric — cohort rows stay tallied for the reports
                # but are no longer dropped from the displayed record.
                cohort = _cohort_name(bucket)
        vwap = (e.get("notional", 0) / e["qty"]) if e.get("qty") else None
        rows.append({
            "cohort": cohort,
            "sleeve": sleeve,
            "market_slug": slug,
            "title": meta.get("title") or slug,
            "outcome": meta.get("outcome"),
            **classify_slug(slug),
            "entry_ts": entry_ts or None,
            "entry_date": (max(datetime.fromtimestamp(entry_ts, tz)
                               .strftime("%Y-%m-%d"), first_day)
                           if entry_ts else None),
            "entry_price": round(vwap, 4) if vwap else None,
            "fills": e.get("fills", 0),
            "qty": qty if not settled else e.get("qty", 0.0),
            "stake": round(cost if cost > 0 else e.get("notional", 0.0), 4),
            "value": round(value, 4),
            "settled": settled,
            "settled_ts": (res["ts"] if res
                           else (s or {}).get("last_ts") if cashed_out
                           else None),
            "cashed_out": cashed_out,
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
        is_manual = manual_slugs is not None and slug in manual_slugs
        sleeve = _sleeve_of(slug)
        cohort = "record"
        if sleeve == "copy" and not is_manual \
                and not _pnl_capped(True, res["realized"], 0.0):
            _tally_copy(cost, True, res["realized"])
        else:
            bucket = (manual if is_manual
                      else over_pnl if sleeve == "copy"
                      else _excluded_bucket(slug, cost, True,
                                            res["realized"], 0.0))
            if bucket is not None:
                bucket["count"] += 1
                bucket["stake"] += cost
                bucket["net_pnl"] += res["realized"]
                if bucket is unattributed:
                    _tally_unattributed_day(True, res["realized"],
                                            res["ts"] or entry_ts)
                cohort = _cohort_name(bucket)
        vwap = (e.get("notional", 0) / e["qty"]) if e.get("qty") else None
        rows.append({
            "cohort": cohort,
            "sleeve": sleeve,
            "market_slug": slug,
            "title": res.get("title") or slug,
            "outcome": None,
            **classify_slug(slug),
            "entry_ts": entry_ts,
            "entry_date": max(datetime.fromtimestamp(entry_ts, tz)
                              .strftime("%Y-%m-%d"), first_day),
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
    # Cashed-out markets the positions payload no longer carries and no
    # resolution ever will (the market is still open — WE closed): build
    # their settled rows from the entry trades + the sell ledger. Without
    # this a full cash-out on an unresolved market simply vanished from
    # the record (owner report 2026-08-08).
    for slug, s in sold.items():
        if slug in seen or slug in resolutions:
            continue
        e = entries.get(slug) or {}
        entry_ts = e.get("first_ts") or 0.0
        if not entry_ts:
            undatable += 1
            continue
        if entry_ts < since_ts:
            continue
        cost = e.get("notional", 0.0)
        realized = s["realized"] or (s["proceeds"] - cost)
        is_manual = manual_slugs is not None and slug in manual_slugs
        sleeve = _sleeve_of(slug)
        cohort = "record"
        if sleeve == "copy" and not is_manual \
                and not _pnl_capped(True, realized, 0.0):
            _tally_copy(cost, True, realized)
        else:
            bucket = (manual if is_manual
                      else over_pnl if sleeve == "copy"
                      else _excluded_bucket(slug, cost, True, realized, 0.0))
            if bucket is not None:
                bucket["count"] += 1
                bucket["stake"] += cost
                bucket["net_pnl"] += realized
                if bucket is unattributed:
                    _tally_unattributed_day(True, realized,
                                            s["last_ts"] or entry_ts)
                cohort = _cohort_name(bucket)
        vwap = (e.get("notional", 0) / e["qty"]) if e.get("qty") else None
        rows.append({
            "cohort": cohort,
            "sleeve": sleeve,
            "market_slug": slug,
            "title": slug,
            "outcome": None,
            **classify_slug(slug),
            "entry_ts": entry_ts,
            "entry_date": max(datetime.fromtimestamp(entry_ts, tz)
                              .strftime("%Y-%m-%d"), first_day),
            "entry_price": round(vwap, 4) if vwap else None,
            "fills": e.get("fills", 0),
            "qty": e.get("qty", 0.0),
            "stake": round(cost, 4),
            "value": 0.0,
            "settled": True,
            "settled_ts": s["last_ts"] or None,
            "cashed_out": True,
            "pnl": round(realized, 4),
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
            "date": r["entry_date"], "deployed": 0.0, "trades": 0, "open": 0,
            "pnl": 0.0, "settled": 0, "wins": 0, "pnl_estimated": False})
        d["deployed"] += r["stake"]
        d["trades"] += 1
        # Open count lets the UI tell "live day, money still on the table"
        # apart from "settled at zero" — a day of open positions is not a
        # $0 loss, and painting it red taught the owner to distrust the page.
        if not r["settled"]:
            d["open"] += 1
    for r in settled_rows:
        ts = r["settled_ts"] or r["entry_ts"]
        if not ts:
            continue
        day = max(datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d"),
                  first_day)
        d = daily.setdefault(day, {
            "date": day, "deployed": 0.0, "trades": 0, "open": 0,
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

    # OWNER DIRECTIVE 2026-08-08: the whole account IS the record — every
    # cohort's rows are inside `rows` now, so the account tie-out is the
    # summary itself, not summary-plus-buckets (adding the buckets back
    # would double-count every excluded-cohort row).
    account = {
        "trades": len(rows),
        "open": len(rows) - len(settled_rows),
        "stake": round(deployed, 2),
        "net_pnl": round(net, 2),
    }
    # The AI-only subset survives as a DISCLOSURE — the strategy stays
    # gradable on its own even though the headline is the account.
    _rec = [r for r in rows if r.get("cohort") == "record"]
    _rec_settled = [r for r in _rec if r["settled"]]
    record_subset = {
        "trades": len(_rec),
        "settled": len(_rec_settled),
        "wins": sum(1 for r in _rec_settled if (r["pnl"] or 0) > 0),
        "losses": sum(1 for r in _rec_settled if (r["pnl"] or 0) < 0),
        "net_pnl": round(sum(r["pnl"] or 0 for r in _rec_settled), 2),
        "settled_stake": round(sum(r["stake"] for r in _rec_settled), 2),
    }

    return {
        "since": datetime.fromtimestamp(since_ts, tz)
                 .strftime("%Y-%m-%d"),
        "generated_at": time.time(),
        "account": account,
        "record_subset": record_subset,
        # HEADLINE = the venue's own arithmetic (owner directive
        # 2026-08-08): cumulative realized as the venue stamps it, no
        # windowing, no entry-dating — the number that matches the
        # venue app. Counts/deployed still come from the dated rows
        # (they feed the ledger and calendar); win_rate and ROI ride
        # the venue basis so "profit per dollar" is account-true.
        "summary": {
            "trades": len(rows),
            "open": len(rows) - len(settled_rows),
            "settled": venue_totals["settled"],
            "wins": venue_totals["wins"],
            # zero-realized settlements are pushes/voids, not losses
            "losses": venue_totals["losses"],
            "deployed": round(deployed, 2),
            "open_value": round(sum(r["value"] for r in rows
                                    if not r["settled"]), 2),
            "net_pnl": venue_totals["net_pnl"],
            "settled_stake": venue_totals["settled_stake"],
            "roi": (round(venue_totals["net_pnl"]
                          / venue_totals["settled_stake"], 4)
                    if venue_totals["settled_stake"] else None),
            "win_rate": (round(venue_totals["wins"]
                               / venue_totals["settled"], 4)
                         if venue_totals["settled"] else None),
        },
        "venue_totals": venue_totals,
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
        # Single trades swinging the P&L past the cap in either direction
        # (owner directive 2026-08-06) — excluded from every figure above,
        # copy sleeve included, and disclosed here.
        "excluded_over_pnl": (
            {"limit": max_abs_pnl,
             "count": over_pnl["count"],
             "open": over_pnl["open"],
             "stake": round(over_pnl["stake"], 2),
             "net_pnl": round(over_pnl["net_pnl"], 2)}
            if max_abs_pnl is not None else None),
        # Admin-directed tickets: real account money, a human's picks —
        # disclosed, never blended into the AI's figures.
        "manual_desk": (
            {"count": manual["count"],
             "open": manual["open"],
             "stake": round(manual["stake"], 2),
             "net_pnl": round(manual["net_pnl"], 2)}
            if manual_slugs is not None else None),
        # Positions the engine's own mirror does not claim. When attribution
        # is active this is where non-engine activity (the arb-bug cohort,
        # anything unexplained) lands — visible, never blended in.
        "excluded_unattributed": (
            {"count": unattributed["count"],
             "open": unattributed["open"],
             "stake": round(unattributed["stake"], 2),
             "net_pnl": round(unattributed["net_pnl"], 2),
             "daily": sorted(unattributed_daily.values(),
                             key=lambda d: d["date"])}
            if attributed is not None else None),
        # The whale-copy sleeve's cohort stats. Its rows are INSIDE the
        # record (it is the AI's trading too); this block keeps the sleeve
        # gradable on its own.
        "copy_sleeve": (
            {"count": copy_sleeve["count"],
             "open": copy_sleeve["open"],
             "stake": round(copy_sleeve["stake"], 2),
             "net_pnl": round(copy_sleeve["net_pnl"], 2)}
            if copy_slugs is not None else None),
        "daily": sorted(daily.values(), key=lambda d: d["date"]),
        "trades": rows,
    }


def _paged(call, params: dict, max_pages: int,
           pace: float = 0.15) -> tuple[list[dict], bool]:
    """Cursor-page a venue endpoint to eof (or the page cap).

    Returns (pages, complete). Each page call retries twice on transient
    failure — one rate-limited call out of forty must not throw away the
    whole refresh (that is how the site went stale while looking healthy).
    Pacing keeps the burst under the venue's rate limit in the first place.
    """
    pages: list[dict] = []
    cursor = ""
    for _ in range(max_pages):
        p = dict(params)
        if cursor:
            p["cursor"] = cursor
        resp: dict | None = None
        for attempt in range(3):
            try:
                resp = call(p) or {}
                break
            except Exception:  # noqa: BLE001 — retry transient venue errors
                if attempt == 2:
                    raise
                time.sleep(1.0 + 2.0 * attempt)
        pages.append(resp or {})
        cursor = (resp or {}).get("nextCursor") or ""
        if (resp or {}).get("eof") or not cursor:
            return pages, True
        if pace:
            time.sleep(pace)
    return pages, False


def _fetch_raw() -> dict:
    from polymarket_us import PolymarketUS

    cfg = settings()
    client = PolymarketUS(key_id=cfg.pmus_key_id, secret_key=cfg.pmus_secret_key)
    # Positions page to EOF (cap 40 = 4,000). The old cap of 8 pages was
    # sized for the engine sleeve alone; once the copy sleeve ran hundreds
    # of clips a day the account blew through 800 rows and every position
    # past page 8 silently vanished — the owner's "we have more pending
    # trades than the site shows" (2026-08-04). Completeness is now
    # measured and travels with the payload instead of being assumed.
    pos_pages, pos_complete = _paged(
        client.portfolio.positions, {"limit": 100}, max_pages=40)
    positions: dict[str, dict] = {}
    for resp in pos_pages:
        positions.update(resp.get("positions") or {})
    acts: list[dict] = []
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
    act_pages, _ = _paged(
        client.portfolio.activities,
        {"limit": 100, "sortOrder": "SORT_ORDER_DESCENDING",
         "types": ["ACTIVITY_TYPE_TRADE",
                   "ACTIVITY_TYPE_POSITION_RESOLUTION"]},
        max_pages=pages)
    for resp in act_pages:
        acts.extend(resp.get("activities") or [])
    _deep_swept = True
    return {"positions": positions, "activities": acts,
            "positions_pages": len(pos_pages),
            "positions_complete": pos_complete}


_deep_swept = False


_archive_ready = False
_archive_cache: dict[str, Any] = {"ts": 0.0, "data": None}


_archived_ids: set[str] = set()

def _slim(a: dict) -> dict:
    """Reduce an archived activity to exactly the fields build() reads.

    The in-memory archive is every activity the account ever produced —
    44k and growing ~2k/day — and holding the venue's full payloads put a
    healthy process at 1.9 GB of a 2 GB instance (measured 2026-08-03
    19:57Z: flat, but 93% of the kill line). The TABLE keeps full
    fidelity; RAM keeps the record's working set. If build() grows a new
    field dependency, add it here — the equivalence test will catch a
    miss.
    """
    ts = _act_ts(a)
    out: dict = {"id": a.get("id"), "type": a.get("type"),
                 "timestamp": ts or None}
    if a.get("type") == "ACTIVITY_TYPE_TRADE":
        t = a.get("trade") or {}
        out["trade"] = {"marketSlug": t.get("marketSlug"),
                        "qty": t.get("qty"), "price": t.get("price"),
                        "createTime": _act_ts(t) or None}
    elif a.get("type") == "ACTIVITY_TYPE_POSITION_RESOLUTION":
        r = a.get("positionResolution") or {}
        b = r.get("beforePosition") or {}
        af = r.get("afterPosition") or {}
        out["positionResolution"] = {
            "marketSlug": r.get("marketSlug"),
            "beforePosition": {
                "realized": b.get("realized"), "cost": b.get("cost"),
                "marketMetadata": {
                    "title": (b.get("marketMetadata") or {}).get("title")}},
            "afterPosition": {
                "realized": af.get("realized"),
                "marketMetadata": {
                    "title": (af.get("marketMetadata") or {}).get("title")}},
        }
    return out


_hydrate_task: asyncio.Task | None = None


def _ensure_hydrate_retry() -> None:
    """Keep trying to load the archive until it loads.

    A boot-time hydrate failure used to be permanent for the process —
    the site served the venue's sliding window until the next deploy got
    lucky. History coming back must not depend on luck."""
    global _hydrate_task
    if _hydrate_task is not None and not _hydrate_task.done():
        return

    async def _loop() -> None:
        from ..db import get_pool

        while _archive_cache["data"] is None:
            await asyncio.sleep(15)
            try:
                pool = await get_pool()
                stored = await pool.fetch(
                    "SELECT payload FROM pmus_activity_archive")

                def _parse() -> list[dict]:
                    return [_slim(json.loads(r["payload"])
                                  if isinstance(r["payload"], str)
                                  else r["payload"])
                            for r in stored]

                parsed = await asyncio.to_thread(_parse)
                _archive_cache["data"] = parsed
                _archive_cache["ts"] = time.time()
                logging.getLogger(__name__).warning(
                    "archive hydrated on retry: %s rows", len(parsed))
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).exception(
                    "archive hydrate retry failed; retrying in 15s")

    _hydrate_task = asyncio.get_running_loop().create_task(_loop())


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
                        json.dumps(a, default=str), a))
        return out

    new = await asyncio.to_thread(_serialize)
    if new:
        await pool.executemany(
            "INSERT INTO pmus_activity_archive (id, ts, payload) "
            "VALUES ($1, $2, $3::jsonb) ON CONFLICT (id) DO NOTHING",
            [(aid, ts, js) for aid, ts, js, _ in new])
        _archived_ids.update(r[0] for r in new)

    if _archive_cache["data"] is None:
        # Cold boot: hydrate the in-memory archive from the table, once.
        # This read races the workers' heaviest moments (analytics cycles,
        # backfill flood) on a shared Postgres. A failed attempt must NOT
        # give up — twice today a boot-time failure silently demoted the
        # site to the venue's ~2-day window (Aug 1 vanished, every total
        # shrank; owner reports 2026-08-03 evening, twice). On failure a
        # background task retries every 15s until the history is back, and
        # the request path unions archive+window so the page keeps serving
        # the freshest data meanwhile.
        stored = None
        for wait in (0.0, 1.0, 4.0):
            if wait:
                await asyncio.sleep(wait)
            try:
                stored = await pool.fetch(
                    "SELECT payload FROM pmus_activity_archive")
                break
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).exception("archive hydrate retry")
        if stored is None:
            _ensure_hydrate_retry()
            return [_slim(a) for _, _, _, a in new]

        def _parse() -> list[dict]:
            out: list[dict] = []
            for r in stored:
                p = r["payload"]
                out.append(_slim(json.loads(p) if isinstance(p, str) else p))
            return out

        parsed = await asyncio.to_thread(_parse)
    else:
        # Warm: the cache IS the archive (first-seen versions, exactly what
        # the table holds under ON CONFLICT DO NOTHING), so append only this
        # refresh's genuinely-new activities. Re-reading and re-parsing the
        # WHOLE table here on every refresh was the API's memory ratchet:
        # each multi-MB parse landed in a fresh worker thread whose malloc
        # arena kept the freed pages, so RSS only ever went up (995 MB ->
        # 1,336 MB baseline over one morning, spikes to the 2 GB kill line
        # — observed 2026-08-03, three OOM restarts on the Standard
        # instance). A new list, not in-place append: readers hold
        # references to the old one mid-request.
        parsed = _archive_cache["data"] + [_slim(a) for _, _, _, a in new]

    _archive_cache["data"], _archive_cache["ts"] = parsed, time.time()
    return parsed


# ── Deploy-survival persistence (owner report 2026-08-07: the page
# showed FEWER settled trades minutes after showing more — every deploy
# cold-boots this process and the first thin window briefly regressed
# the record). The last served payload persists in the database; a
# fresh boot serves it instantly instead of blocking, refusing, or
# stepping backward, and a fresh build that shows fewer settled than
# the persisted high-water serves the persisted copy instead.
_PERSIST_KEY = "track_record_last_payload"
_persist_state: dict[str, float] = {"ts": 0.0, "settled": -1.0, "stake": -1.0}

# A degraded build can GROW the settled count while losing half its
# activities (missing redemptions turn wins into losses; missing fills
# shrink every stake) — exactly what put fake negatives on the homepage
# on 2026-08-07 ~01:00Z: settled 958->963 while settled stake halved
# $6.9k->$2.9k and the corrupt payload displaced the good persisted one.
# Settled stake NEVER legitimately shrinks (settlements accumulate), so
# a fresh build materially below the stake high-water is evidence of
# data loss, not trading.
_STAKE_SHRINK_FLOOR = 0.75


def _stake_of(payload: dict) -> float:
    return float((payload.get("summary") or {}).get("settled_stake") or 0)


def _looks_degraded(payload: dict) -> bool:
    return (_persist_state["stake"] > 0
            and _stake_of(payload) < _persist_state["stake"]
            * _STAKE_SHRINK_FLOOR)


async def _persist_payload(payload: dict) -> None:
    settled = float((payload.get("summary") or {}).get("settled") or 0)
    now = time.time()
    if settled < _persist_state["settled"] \
            or _looks_degraded(payload) \
            or now - _persist_state["ts"] < 60:
        return
    try:
        from ..db import get_pool

        pool = await get_pool()
        await pool.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            _PERSIST_KEY, json.dumps({"at": now, "payload": payload},
                                     default=str))
        _persist_state["ts"] = now
        _persist_state["settled"] = settled
        _persist_state["stake"] = max(_persist_state["stake"],
                                      _stake_of(payload))
    except Exception:  # noqa: BLE001 — persistence is an upgrade, not a gate
        logging.getLogger(__name__).debug("payload persist failed",
                                          exc_info=True)


async def _load_persisted() -> dict | None:
    try:
        from ..db import get_pool

        pool = await get_pool()
        val = await pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1", _PERSIST_KEY)
        if not val:
            return None
        obj = json.loads(val) if isinstance(val, str) else val
        payload = obj.get("payload")
        if payload:
            _persist_state["settled"] = max(
                _persist_state["settled"],
                float((payload.get("summary") or {}).get("settled") or 0))
            _persist_state["stake"] = max(_persist_state["stake"],
                                          _stake_of(payload))
        return payload
    except Exception:  # noqa: BLE001
        return None


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
    # The window boundary is UTC midnight, deliberately NOT the ET midnight
    # the calendar buckets on. Parsing `since` at ET midnight (04:00Z)
    # silently dropped the account's first ~4 hours of trades (00:00-04:00Z
    # Aug 1) and shrank the Aug 1 record — owner report 2026-08-05. A UTC
    # boundary keeps every trade in the record; the cost is that a
    # 00:00-04:00Z entry (evening ET of the day before) displays in the
    # PREVIOUS ET day's calendar box, which is truthful.
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
        # Deploy survival: serve the persisted payload INSTANTLY and let
        # the cold fetch warm in the background — a reboot must never
        # block a page, refuse, or step the record backward.
        persisted = await _load_persisted()
        if persisted is not None:
            if not _lock.locked():
                async def _cold() -> None:
                    async with _lock:
                        if _raw_cache["data"] is not None:
                            return
                        try:
                            _raw_cache["data"] = await asyncio.wait_for(
                                asyncio.to_thread(_fetch_raw), timeout=150)
                            _raw_cache["ts"] = time.time()
                            await _archive_and_union(
                                _raw_cache["data"]["activities"])
                        except Exception:  # noqa: BLE001
                            logging.getLogger(__name__).exception(
                                "background cold fetch failed")

                asyncio.get_running_loop().create_task(_cold())
            return {"configured": True, "restored_across_deploy": True,
                    **persisted}
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
                    # Hard ceiling: a hung venue call used to hold this lock
                    # forever, freezing the snapshot permanently with no
                    # visible symptom. 150s covers the full 52-page fetch.
                    _raw_cache["data"] = await asyncio.wait_for(
                        asyncio.to_thread(_fetch_raw), timeout=150)
                    _raw_cache["ts"] = time.time()
                    _refresh_health.update(error=None, streak=0)
                    # Archive travels WITH the refresh — once per new
                    # snapshot, never per page request.
                    await _archive_and_union(_raw_cache["data"]["activities"])
                except Exception as exc:  # noqa: BLE001 — stale beats broken
                    _refresh_health.update(
                        error=f"{type(exc).__name__}: {str(exc)[:200]}",
                        error_at=time.time(),
                        streak=_refresh_health["streak"] + 1)
                    logging.getLogger(__name__).exception(
                        "track-record background refresh failed; serving stale")

        asyncio.get_running_loop().create_task(_bg_refresh())
    raw = _raw_cache["data"]
    # The request path reads ONLY in-process caches — the archive is
    # synced by the refresh paths above, never by a page view.
    # UNION, never either/or: picking one source meant a failed archive
    # read served the venue's ~2-day window alone and history vanished
    # from the record. The merge means the worst a failure can do is trim
    # history until the retry task restores it — the fresh window always
    # serves, and nothing that ever settled can be pushed off the page by
    # an infrastructure hiccup.
    archive = _archive_cache["data"] or []
    window = raw["activities"] or []
    # A freshly-booted process whose archive has not hydrated yet KNOWS how
    # much history it is missing (_archived_ids was seeded from the table).
    # Serving the bare window in that state shrinks the record to a few
    # hours and presents it as the truth — the owner caught exactly that
    # twice on 2026-08-04 (449 settled -> 215, +$73 -> +$18) during deploy
    # reboots. Refusing is strictly better: the frontend treats an error
    # payload as "keep showing the last good numbers".
    known_history = len(_archived_ids)
    if not archive and known_history > max(int(len(window) * 1.5), 2000):
        # Hydration window: the persisted payload is strictly better
        # than a refusal — real numbers from minutes ago vs an error.
        persisted = await _load_persisted()
        if persisted is not None:
            return {"configured": True, "restored_across_deploy": True,
                    **persisted}
        return {"configured": True,
                "error": (f"history hydrating ({known_history} archived "
                          "activities not yet loaded); refusing to serve a "
                          "shrunken record — retry shortly")}
    if archive:
        seen_ids = {str(a.get("id") or "") for a in archive}
        acts = archive + [a for a in window
                          if str(a.get("id") or "") not in seen_ids]
    else:
        acts = window
    # Self-describing provenance: which activity source built this payload.
    # When the archive is unavailable the record silently loses everything
    # older than the venue's sliding window — that state must be visible in
    # one curl, not deduced from shrunken totals.
    source = {
        "activities_source": "archive+window" if archive else "venue_window",
        "archive_rows": len(archive),
        "window_rows": len(window),
    }
    attributed = copy_slugs = manual_slugs = None
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
            "WHERE us_market_slug IS NOT NULL "
            "AND COALESCE(whale_username, '') NOT IN "
            "('manual', 'underdog')")
        copy_slugs = {r["us_market_slug"] for r in cp}
        # The underdog sleeve is the AI's own trading (not a whale copy,
        # not the owner): its slugs count as ATTRIBUTED so the rows wear
        # the record cohort rather than falling to 'unattributed'.
        ud = await pool.fetch(
            "SELECT DISTINCT us_market_slug FROM live_orders "
            "WHERE us_market_slug IS NOT NULL "
            "AND whale_username = 'underdog'")
        if attributed is not None:
            attributed |= {r["us_market_slug"] for r in ud}
        # Admin-directed tickets (owner 2026-08-07): their own disclosed
        # cohort, never the AI's record.
        mn = await pool.fetch(
            "SELECT DISTINCT us_market_slug FROM live_orders "
            "WHERE us_market_slug IS NOT NULL "
            "AND whale_username = 'manual'")
        manual_slugs = {r["us_market_slug"] for r in mn}
    except Exception:  # noqa: BLE001 — provenance is an upgrade, not a gate
        attributed = copy_slugs = manual_slugs = None
    snapshot = {
        "raw_ts": _raw_cache["ts"],
        "age_s": round(time.time() - _raw_cache["ts"], 1),
        "positions_pages": raw.get("positions_pages"),
        "positions_complete": bool(raw.get("positions_complete", True)),
        "refresh_error": _refresh_health["error"],
        "refresh_error_streak": _refresh_health["streak"],
    }
    payload = {"configured": True, **source, "snapshot": snapshot,
               **build(raw["positions"], acts, since_ts,
                       max_stake=max_stake, attributed=attributed,
                       copy_slugs=copy_slugs, max_abs_pnl=PNL_DISPLAY_CAP,
                       manual_slugs=manual_slugs)}
    # Monotonic guards: a fresh build thinner than the persisted
    # high-water (post-boot window still catching up) must not show the
    # owner fewer settled trades than he already saw — and a build whose
    # settled STAKE shrank materially is built from lost activities
    # (fake losses), not from trading, however many rows it counts.
    fresh_settled = float((payload.get("summary") or {}).get("settled") or 0)
    degraded = _looks_degraded(payload)
    if fresh_settled < _persist_state["settled"] or degraded:
        persisted = await _load_persisted()
        if persisted is not None:
            out = {"configured": True, "restored_across_deploy": True,
                   **persisted}
            if degraded:
                out["degraded_build"] = {
                    "fresh_settled_stake": round(_stake_of(payload), 2),
                    "high_water_stake": round(_persist_state["stake"], 2),
                    "note": "fresh build lost activities; serving last "
                            "good payload",
                }
            return out
    await _persist_payload(payload)
    return payload
