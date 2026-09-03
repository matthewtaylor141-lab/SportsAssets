"""The AI trader's public track record — from the ACTUAL venue account.

The first version of the site read the engine's shadow mirror, which logs
intents (including paper ones). A track record must come from the account
that holds the money: every row here is a real position from the venue's
portfolio API, priced by its own trade activities, settled by its own
resolution activities. Nothing is inferred from our bookkeeping; our ledger
only ANNOTATES rows (edge, band) where it recognizes the market.

Windowed from `since` (default 2026-08-01, the account's first full day) on
the venue's own times: a row is dated and windowed on its ENTRY time —
the first trade the venue reports for the market — when the venue
reports one, else on the venue's own SETTLEMENT time (the resolution's
time, or the closing sale's last time). A row with neither is excluded
rather than guessed into a date, and so is a timed row the record
cannot read safely without an entry: a money-less resolution, a
zero-realized sale (what a short's opening sell looks like), an open
short. Every such exclusion is counted in the payload
(`excluded_undatable`) so the reader can see what the record could not
place.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
import os
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..config import settings
from .pmus_account import _act_ts, _amt, _any_ts

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

# RE-BASELINED (owner order 2026-08-24 evening: "clear the front end
# track record completely — start fresh today with all new fills").
#
# This moves the DISPLAY WINDOW, it does not erase anything. Every
# historical row stays in the database: the venue-certified August
# restatement, the day-by-day reconciliation and the investor ledger
# all depend on that history existing, and a scoreboard that could
# delete its own losses is exactly the instrument that misled us for
# fifteen days. The record is served FROM this date and labelled with
# it, so a reader can never mistake a fresh start for a lifetime
# result. Pass ?since=2026-08-01 to see the prior period in full.
# ONE DISPLAY EPOCH (owner order 2026-09-02: "zero out our frontend
# completely and only show performance data and account data starting
# yesterday, September 1st -- I don't want old information clouding our
# judgement"). Every frontend performance and account view floors on
# this day (ET): the account record, the copies record, the daily
# breakdown and reports, today's tape, live status, the engine summary,
# the AI-trader window, the venue-account card and the management
# ledgers. History stays in the database and stays reachable at
# ?since=; the audit surfaces (AUDIT_SINCE) never move.
DISPLAY_EPOCH = os.environ.get("DISPLAY_EPOCH", "2026-09-01")
RECORD_EPOCH = DISPLAY_EPOCH
DEFAULT_SINCE = os.environ.get("TRACK_RECORD_SINCE", RECORD_EPOCH)


def display_epoch_start():
    """The display epoch as an aware datetime at ET midnight, for SQL
    floors on timestamptz columns."""
    from datetime import datetime
    return datetime.strptime(DISPLAY_EPOCH, "%Y-%m-%d").replace(tzinfo=RECORD_TZ)


def display_epoch_ts() -> float:
    return display_epoch_start().timestamp()

# The floor the RECONCILIATION reads, as opposed to the display epoch
# above. Moving the display window must never move what the audit
# reconciles: the day-detail audit diffs the account anchor against the
# copies ledger, and the copies side counts rows by the day they
# SETTLED, which includes positions opened long before the epoch. Point
# the anchor at the epoch and the two sides cover different populations
# — observed 2026-08-25 as a phantom $867 "residual" that was really
# 470 settled rows the windowed anchor could not see. Every consumer
# that reconciles rather than displays passes this.
AUDIT_SINCE = "2026-08-01"

# Owner directive 2026-08-06: no single trade may move any displayed P&L
# by more than this, either direction — at $3-5 clips a $100+ swing is an
# anomaly, not the strategy. Applied by default to every served record
# (and to the daily-breakdown copy query); excluded rows are disclosed in
# `excluded_over_pnl`, never silently dropped.
#
# RAISED TO $500 (adds, 2026-09-02): the $100 figure was set at $3-5
# clips. At the $50 measuring clip a single winning row at his price of
# 0.30 already realizes +$117, and with up to three add legs merged
# onto a standing row (migration 045) one row legitimately stakes $200.
# A cap that hides the biggest positions hides exactly the rows the
# add decision must be judged on. Still disclosed, never silent.
PNL_DISPLAY_CAP = float(os.environ.get("PNL_DISPLAY_CAP", "500"))

# OWNER OVERRIDE (2026-09-01): trades counted IN FULL despite the cap.
# The owner placed a manual Polymarket ticket on the same market as the
# 0x076daa87 copy of atp-matbel-zsopir (2026-08-30); the venue settled
# both legs onto the copy row (+$966 on a $93.72 stake) and the $100
# single-trade cap moved the whole thing to residual, so the whale's
# P&L never saw it. For THIS trade the owner wants the manual leg
# counted in the whale's P&L. Matched as a substring of the market slug
# so a family prefix cannot miss it; every surface that applies the cap
# -- in Python or in SQL -- consults this same list.
PNL_CAP_EXEMPT_KEYS: tuple[str, ...] = ("atp-matbel-zsopir-2026-08-30",)


def pnl_cap_exempt(slug: str | None) -> bool:
    s = (slug or "").lower()
    return any(k in s for k in PNL_CAP_EXEMPT_KEYS)


def pnl_cap_exempt_patterns() -> list[str]:
    """The same set as SQL LIKE patterns, for the queries that cap in
    the database: `... OR lower(us_market_slug) LIKE ANY($n::text[])`."""
    return [f"%{k}%" for k in PNL_CAP_EXEMPT_KEYS]

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
        # Side truth, in order of reliability (raw-feed audit 2026-08-19,
        # 6,747 trades): the top-level side is ALWAYS None in the venue
        # feed; the definitive side lives on the nested execution order.
        # The rp!=0 fallback alone misread 444 zero-P&L sells as buys
        # (inflated entry VWAPs) and booked 23 short-CLOSING BUYS as
        # sales — their qty*price padded "proceeds" with cash that never
        # came in, while their realized loss is real and must count.
        side = str(t.get("side") or "").upper()
        if not side:
            for k in ("aggressorExecution", "passiveExecution"):
                o = ((t.get(k) or {}).get("order") or {})
                if o.get("side"):
                    side = str(o["side"]).upper()
                    break
        is_sell = ("SELL" in side) if side else rp != 0
        closes_position = is_sell or rp != 0
        if closes_position:
            if qty > 0 and 0 < price < 1:
                s = sold.setdefault(slug, {"qty": 0.0, "proceeds": 0.0,
                                           "realized": 0.0, "last_ts": 0.0})
                if is_sell:            # only actual sales are cash in
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
            # _any_ts, NOT _act_ts: the resolution's time is nested under
            # positionResolution, and the top-level reader returned 0 here
            # long after the archive writer was fixed (2026-08-25). A zero
            # made settled_ts fall through to entry_ts, so the day P&L
            # filed every long on the day it was BOUGHT and the day recon
            # ran a $458 residual on money that tied to the cent
            # (2026-09-02).
            "ts": _any_ts(act),
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

    def _pnl_capped(slug: str, settled: bool, realized: float,
                    unreal: float) -> bool:
        if max_abs_pnl is None or pnl_cap_exempt(slug):
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
        if _pnl_capped(slug, settled, realized, unreal):
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

    # THE DAY ANCHOR DOES NOT MOVE WITH THE VENUE (owner order 2026-09-02,
    # "go for it, let's get this working"; read-only investigation the
    # same afternoon). A settlement used to be datable ONLY through its
    # ENTRY trade. The venue's fresh activity window is ~8 h deep and
    # the archive layer is frozen per process, so every settlement whose
    # entry had scrolled out was dropped as 'undatable' (577 -> 544 over
    # one afternoon) and the record read "settled 6, pnl -39.59" for four
    # hours while the venue resolved markets all afternoon and its own
    # realized climbed 445 -> 1399. A short can NEVER be dated that way:
    # its opening trade is a venue SELL, which the ledger above routes to
    # `sold`, never to `entries`. The settlement facts do not need the
    # entry: a POSITION_RESOLUTION carries its own time, realized and
    # cost, and a cash-out carries the sale's last time and realized. So
    # a row with no entry is KEPT when it has a settlement time, dated
    # and windowed on that time; only a row with NEITHER an entry nor a
    # settlement time stays undatable. Rows that have an entry are not
    # touched by this: their window, dating and figures are as before.
    def _settlement_anchor(res: dict | None, s: dict | None,
                           cashed_out: bool) -> float:
        """The venue's own settlement time for a row: the resolution's
        time, else the closing sale's, else 0.0 (unknown, never
        guessed)."""
        if res and res["ts"]:
            return res["ts"]
        if cashed_out and s and s["last_ts"]:
            return s["last_ts"]
        return 0.0

    seen: set[str] = set()
    for slug, p in (positions or {}).items():
        seen.add(slug)
        meta = p.get("marketMetadata") or {}
        qty = _amt(p.get("netPosition"))
        settled = bool(p.get("expired")) or qty <= 0
        e = entries.get(slug) or {}
        entry_ts = e.get("first_ts") or 0.0
        res = resolutions.get(slug)
        s = sold.get(slug)
        # The sale is the settlement only when it closed the position
        # (settled, no resolution, market not expired). Computed here,
        # before the window check, because a row with no entry is
        # windowed on this settlement time.
        cashed_out = bool(settled and not res and not p.get("expired") and s)
        # ONE anchor for the whole row, computed once (adversarial review
        # of the settlement-dating change, 2026-09-02): the window gate
        # and the unattributed daily tally must read the same time, or a
        # row can pass the gate on one clock and be day-bucketed on
        # another. A netPosition below zero with no resolution is an OPEN
        # short (its opening SELL is the only trade we have), not a
        # cash-out: the venue's cumulative realized is still 0 for it and
        # dating it by the opening sell would file a live position as a
        # push, so the guard leaves that anchor at 0.0.
        anchor = _settlement_anchor(res, s, cashed_out and not qty < 0)
        if not entry_ts and cashed_out and not qty < 0 \
                and not s["realized"] and not _amt(p.get("realized")):
            # THE SAME RULE AS THE SOLD-ONLY LOOP (third adversarial
            # review, 2026-09-02): a zero-realized sale with no entry is
            # what a short's opening sell looks like, and the venue
            # stamps a non-zero realized on a sale that closed a lot. A
            # netPosition-0 row beside it does not change what the sale
            # was, and dating it here while the sold-only loop refuses
            # the same sale when the positions walk drops the row would
            # make the settled count flip between refreshes.
            anchor = 0.0
        if not entry_ts:
            # No datable entry trade in the activities we hold. Date the
            # row by its settlement instead; a row with no settlement
            # time at all (or an open short, above) stays undatable:
            # excluded and COUNTED, never guessed into a date.
            if not anchor:
                undatable += 1
                continue
            if anchor < since_ts:
                continue      # pre-window settlement: excluded, not re-dated
        elif entry_ts < since_ts:
            continue          # pre-window entry: excluded, not re-dated
        cost = _amt(p.get("cost"))
        value = _amt(p.get("cashValue"))
        realized = _amt(p.get("realized"))
        if res:
            # The resolution activity is the settlement record; the position
            # row can lag it (realized still 0 after the market resolves).
            settled = True
            realized = res["realized"] or realized
            cost = cost or res["cost"]
            if not entry_ts and cost == 0 and realized == 0:
                # A resolution with no entry AND no money (cost 0,
                # realized 0) is a row the record knows nothing about:
                # kept, it would be a settled push at stake 0 that
                # dilutes win_rate and says nothing (adversarial review
                # 2026-09-02). Treated as undatable: excluded, counted.
                undatable += 1
                continue
        if cashed_out:
            # Sold to zero before resolution: the sale IS the settlement.
            # The venue's own cumulative realized is authoritative when it
            # has caught up; the sell trades' realizedPnl covers the lag.
            # With no entry there is no known cost, so proceeds-minus-cost
            # would book the whole sale as profit: the sells' own realized
            # is the only P&L we can read, and the proceeds stand in for
            # the stake.
            realized = realized or s["realized"] \
                or ((s["proceeds"] - e.get("notional", 0.0))
                    if entry_ts else 0.0)
            cost = cost or e.get("notional", 0.0) \
                or (s["proceeds"] if not entry_ts else 0.0)
        stake_now = cost if cost > 0 else e.get("notional", 0.0)
        unreal = (value - cost) if not settled else 0.0
        is_manual = manual_slugs is not None and slug in manual_slugs
        sleeve = _sleeve_of(slug)
        cohort = "record"
        if sleeve == "copy" and not is_manual \
                and not _pnl_capped(slug, settled, realized, unreal):
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
                        (res["ts"] if res else None) or entry_ts or anchor)
                # OWNER DIRECTIVE 2026-08-08 (final): the headline is the
                # AI's trading ONLY — engine, copies, underdog sleeve and
                # their cash-outs. Manual/owner rows are tallied for the
                # disclosures and the account strip, never displayed as
                # the record.
                continue
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
            # No entry in hand (scrolled out, or a short whose opening
            # trade was a SELL): the resolution is the settlement record
            # and dates the row by itself (owner order 2026-09-02).
            if not res["ts"]:
                undatable += 1
                continue
            if res["ts"] < since_ts:
                continue
            if not res["cost"] and not res["realized"]:
                # No entry and no money: a stake-0 push that would only
                # dilute win_rate (adversarial review 2026-09-02).
                # Undatable-equivalent: excluded, counted. A sale ledger
                # on the same slug goes with it on purpose (the sold loop
                # below skips resolved slugs): a resolution after a full
                # cash-out carries the cumulative realized, so a money-
                # less one beside a sale is a shape the venue does not
                # produce, and guessing which figure to trust is not a
                # record (second adversarial review, 2026-09-02).
                undatable += 1
                continue
        elif entry_ts < since_ts:
            continue
        cost = res["cost"] or e.get("notional", 0.0)
        is_manual = manual_slugs is not None and slug in manual_slugs
        sleeve = _sleeve_of(slug)
        cohort = "record"
        if sleeve == "copy" and not is_manual \
                and not _pnl_capped(slug, True, res["realized"], 0.0):
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
                continue
        vwap = (e.get("notional", 0) / e["qty"]) if e.get("qty") else None
        rows.append({
            "cohort": cohort,
            "sleeve": sleeve,
            "market_slug": slug,
            "title": res.get("title") or slug,
            "outcome": None,
            **classify_slug(slug),
            "entry_ts": entry_ts or None,
            "entry_date": (max(datetime.fromtimestamp(entry_ts, tz)
                               .strftime("%Y-%m-%d"), first_day)
                           if entry_ts else None),
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
            # No entry in hand: the sale's last time dates the row by
            # itself (owner order 2026-09-02). Its cost is unknown, so
            # the P&L is the sells' own realized only — proceeds minus a
            # zero cost would book the whole sale as profit — and the
            # proceeds stand in for the stake.
            #
            # ONLY A SALE THAT CLOSED SOMETHING IS A CASH-OUT (second
            # adversarial review, 2026-09-02). With no position row in
            # hand this ledger cannot tell a closing sale from a short's
            # OPENING sell: the positions walk runs before the activities
            # walk and is capped at 40 pages, so a live short can be
            # absent from `positions` for a refresh or for good, and
            # dating it here would file a live position as a settled
            # push. The venue stamps realizedPnl 0 on an opening sell and
            # a non-zero figure on a sale that closed a lot, so a zero
            # realized with no entry is undatable, never a push. A sale
            # at exactly average cost pays the same price; fail closed.
            if not s["last_ts"] or not s["realized"]:
                undatable += 1
                continue
            if s["last_ts"] < since_ts:
                continue
        elif entry_ts < since_ts:
            continue
        cost = e.get("notional", 0.0) \
            or (s["proceeds"] if not entry_ts else 0.0)
        realized = s["realized"] \
            or ((s["proceeds"] - cost) if entry_ts else 0.0)
        is_manual = manual_slugs is not None and slug in manual_slugs
        sleeve = _sleeve_of(slug)
        cohort = "record"
        if sleeve == "copy" and not is_manual \
                and not _pnl_capped(slug, True, realized, 0.0):
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
                continue
        vwap = (e.get("notional", 0) / e["qty"]) if e.get("qty") else None
        rows.append({
            "cohort": cohort,
            "sleeve": sleeve,
            "market_slug": slug,
            "title": slug,
            "outcome": None,
            **classify_slug(slug),
            "entry_ts": entry_ts or None,
            "entry_date": (max(datetime.fromtimestamp(entry_ts, tz)
                               .strftime("%Y-%m-%d"), first_day)
                           if entry_ts else None),
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
    # Newest first. A row dated only by its settlement takes its place
    # in the tape by that time; rows with an entry keep their order.
    rows.sort(key=lambda r: -(r["entry_ts"] or r.get("settled_ts") or 0))

    settled_rows = [r for r in rows if r["settled"]]
    wins = [r for r in settled_rows if (r["pnl"] or 0) > 0]
    losses = [r for r in settled_rows if (r["pnl"] or 0) < 0]
    deployed = sum(r["stake"] for r in rows)
    settled_stake = sum(r["stake"] for r in settled_rows)
    net = sum(r["pnl"] or 0 for r in settled_rows)

    # Daily series. Deployment buckets on entry day, else on the settlement
    # day for a row dated only by its settlement; realized P&L buckets on
    # the venue's resolution day where it gave one, else the entry day,
    # FLAGGED — never silently "today".
    daily: dict[str, dict] = {}
    for r in rows:
        # A row dated only by its settlement files its stake and its
        # count under the settlement day (second adversarial review,
        # 2026-09-02): the summary sums every row, so a day series that
        # skipped these rows stopped footing to its own headline by
        # exactly their stakes, and the report prints both.
        day = r["entry_date"]
        if not day and r.get("settled_ts"):
            day = max(datetime.fromtimestamp(r["settled_ts"], tz)
                      .strftime("%Y-%m-%d"), first_day)
        if not day:
            continue
        d = daily.setdefault(day, {
            "date": day, "deployed": 0.0, "trades": 0, "open": 0,
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

    # Account-wide tie-out: AI record + every disclosed exclusion = what
    # the owner sees in the venue app. Shown as its own strip; never the
    # headline (owner directive 2026-08-08 final: the headline is the AI).
    _buckets = [over_limit, unattributed, over_pnl, manual]
    account = {
        "trades": len(rows) + sum(b["count"] for b in _buckets),
        "open": (len(rows) - len(settled_rows))
                + sum(b["open"] for b in _buckets),
        "stake": round(deployed + sum(b["stake"] for b in _buckets), 2),
        "net_pnl": round(net + sum(b["net_pnl"] for b in _buckets), 2),
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

    # Every SELL the venue reports, by market, newest first — the
    # ground truth behind any "I see a bunch sold" question (owner
    # 2026-08-08 night). realized comes from the venue's own
    # realizedPnl on the sell trades; nothing is inferred.
    sold_markets = sorted(
        ({"slug": s, "qty": round(v["qty"], 2),
          "proceeds": round(v["proceeds"], 2),
          "realized": round(v["realized"], 2),
          "last_ts": v["last_ts"] or None}
         for s, v in sold.items()),
        key=lambda x: -(x["last_ts"] or 0))[:30]

    return {
        "since": datetime.fromtimestamp(since_ts, tz)
                 .strftime("%Y-%m-%d"),
        # The display is RE-BASELINED, not erased (owner order
        # 2026-08-24). The front end labels the record with this epoch
        # so a fresh start can never read as a lifetime result, and the
        # prior period stays retrievable at ?since=2026-08-01.
        "record_epoch": RECORD_EPOCH,
        "rebaselined": datetime.fromtimestamp(since_ts, tz)
                       .strftime("%Y-%m-%d") == RECORD_EPOCH,
        "generated_at": time.time(),
        "account": account,
        "sold_markets": sold_markets,
        "record_subset": record_subset,
        # HEADLINE = the AI's trading only (owner directive 2026-08-08
        # final): engine + copies + underdog sleeve, with their
        # cash-outs counted at sale. Manual/owner rows live in the
        # `account` strip and the disclosures, never these figures —
        # they swamped deployed/ROI ($17K vs the AI's ~$8K).
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
        # The venue's raw cumulative-realized sum, kept as a diagnostic
        # for tie-outs — NOT the headline (see note above).
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
# How far back to seed the dedupe set. The venue pages ~1,200 rows of
# activity; a week is far more than can ever scroll back into view.
_ARCHIVED_ID_WINDOW_S = float(
    os.environ.get("ARCHIVE_ID_WINDOW_S", str(7 * 24 * 3600)))


def _malloc_trim() -> None:
    """Return freed heap pages to the OS after a heavy parse. glibc keeps
    them in per-thread malloc arenas otherwise — the ratchet that made
    RSS only ever climb (995 MB -> 1.9 GB against a 2 GB kill line)."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001 - non-glibc platforms simply skip
        pass


# THE ONLY ACTIVITY TYPES build() READS (memory fix 2026-08-25).
#
# The API was OOM-killed on a Pro instance at 317,681 archived rows.
# build() consumes exactly two types — it filters to ACTIVITY_TYPE_TRADE
# at one call site and ACTIVITY_TYPE_POSITION_RESOLUTION at the other.
# Every other activity the account ever produced — deposits,
# conversions, order lifecycle events — was fetched from Postgres,
# JSON-parsed, slimmed and held in RAM for the life of the process
# WITHOUT EVER BEING READ.
#
# Filtering in SQL means those rows are never fetched, never parsed and
# never retained. The TABLE keeps full fidelity, exactly as before; only
# the working set shrinks. If build() ever grows a third type
# dependency, add it here — test_archive_types.py fails if the code and
# this list disagree.
ARCHIVE_TYPES = ("ACTIVITY_TYPE_TRADE", "ACTIVITY_TYPE_POSITION_RESOLUTION")


def _i(s: Any) -> Any:
    """Intern archive strings: type tags repeat 172k times and market
    slugs repeat per trade of the same market; one shared object each
    instead of a fresh parse-copy per row."""
    return sys.intern(s) if isinstance(s, str) else s


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
    # _any_ts, NOT _act_ts (adversarial review of the settlement-dating
    # change, owner order 2026-09-02 "go for it, let's get this
    # working"). Everything build() receives in production is slimmed:
    # every raw refresh runs through _archive_and_union, and the request
    # path takes the archive twin over the raw window row. The venue puts
    # a resolution's time ONLY at the nested positionResolution.createTime
    # (established 2026-09-02), and the slim row drops that nesting — so
    # a top-level-only read here left every slimmed resolution timeless,
    # and the dating path that keeps a settlement without its entry never
    # fired live (raw: excluded_undatable 0; slimmed twin: 1). The time
    # is lifted to the top level, where build()'s _any_ts read finds it
    # first. For a trade this is exactly the `_act_ts(a) or _act_ts(t)`
    # value build() reads, so nothing else moves; the equivalence test
    # proves both.
    ts = _any_ts(a)
    out: dict = {"id": _i(a.get("id")), "type": _i(a.get("type")),
                 "timestamp": ts or None}
    if a.get("type") == "ACTIVITY_TYPE_TRADE":
        t = a.get("trade") or {}
        # side/realizedPnl: the sell-vs-buy split (cash-out accounting,
        # owner directive 2026-08-08). Dropping them here silently turned
        # every ARCHIVED sell back into a buy once it scrolled out of the
        # venue's fresh window — wrong VWAP, lost cash-out P&L.
        #
        # The top-level side is ALWAYS None in the venue feed (raw audit
        # 2026-08-19); the real side lives on the nested execution order,
        # which the slim row would otherwise discard forever. Capture it
        # here so archived history keeps the truth build() classifies on.
        side = t.get("side")
        if not side:
            for k in ("aggressorExecution", "passiveExecution"):
                o = ((t.get(k) or {}).get("order") or {})
                if o.get("side"):
                    side = o["side"]
                    break
        out["trade"] = {"marketSlug": _i(t.get("marketSlug")),
                        "qty": t.get("qty"), "price": t.get("price"),
                        "side": _i(side),
                        "realizedPnl": t.get("realizedPnl"),
                        "createTime": _act_ts(t) or None}
    elif a.get("type") == "ACTIVITY_TYPE_POSITION_RESOLUTION":
        r = a.get("positionResolution") or {}
        b = r.get("beforePosition") or {}
        af = r.get("afterPosition") or {}
        out["positionResolution"] = {
            "marketSlug": _i(r.get("marketSlug")),
            "beforePosition": {
                "realized": b.get("realized"), "cost": b.get("cost"),
                "marketMetadata": {
                    "title": _i((b.get("marketMetadata") or {}).get("title"))}},
            "afterPosition": {
                "realized": af.get("realized"),
                "marketMetadata": {
                    "title": _i((af.get("marketMetadata") or {}).get("title"))}},
        }
    return out


_hydrate_err: dict = {"err": None, "at": 0.0, "chunks": 0}

# ── Compact archive snapshot (owner 2026-08-09: "I really don't
# understand the reason anymore") ────────────────────────────────────
# The chaos had one root: rebuilding the record needs the 190k-row
# archive, and reading it row-by-row from a failing Postgres kept dying
# — including to our own deploys resetting the grind. The whole SLIM
# archive gzips to a few MB: persisted as ONE row, a boot hydrates in
# seconds with a single small read. The grind also checkpoints partial
# progress here every 20 chunks, so progress survives deploys and the
# FIRST completion is guaranteed even on a sick database.
# v1 -> v2 (2026-08-25): the type filter changed what a snapshot MEANS.
#
# The filter shipped, deployed, and moved nothing: archive_rows stayed
# at 317,681 and RSS at 1.6-1.9 GB. _hydrate_all returns a complete
# fresh snapshot WITHOUT reading the table, so boot kept loading rows
# written by a pre-filter process and never reached the filtered
# cursor. The fix was live and bypassed.
#
# The key must therefore version with the filter: a snapshot is only
# valid for the ARCHIVE_TYPES that produced it. Bumping the key retires
# every stale snapshot at once, forces one filtered re-hydrate, and the
# next save writes a snapshot that means what its key says. If
# ARCHIVE_TYPES ever changes again, bump this too — test_archive_types
# pins the pairing.
#
# v2 -> v3 (2026-09-02, settlement dating): the slim SHAPE changed —
# _slim now lifts a resolution's nested time to the top level. A v2
# snapshot holds resolution rows with no time at all, and nothing can
# recover it from the slim row: the union appends only ids the archive
# has never seen, and the rolling 6-hour save re-persists the same rows,
# so the snapshot never ages past the 24-hour cutoff and never
# re-hydrates from the table. Every resolution archived before the
# dating fix would have stayed undatable for good, and the rows the
# owner looks at (DISPLAY_EPOCH onward) are exactly those. The bump
# retires the v2 snapshot on deploy and forces one filtered re-hydrate
# from the full-fidelity table, the same road as v1 -> v2 (second
# adversarial review, 2026-09-02); test_archive_types pins the suffix.
_SNAP_KEY = "track_record_slim_archive_v3"
_snap_state: dict = {"at": 0.0}
_SNAP_REFRESH_S = 6 * 3600.0
_SNAP_MAX_AGE_S = 24 * 3600.0   # window union covers ~2 days; stay well under


def _pack_rows(rows: list[dict]) -> str:
    """Gzip the archive a ROW AT A TIME, never as one giant string.

    The one-liner this replaces was
        base64(compress(dumps(<the whole list>).encode()))
    which, at 300,000 slim rows, materialises the archive THREE
    times over before a single byte is compressed: the dumps() str, the
    encode() bytes, and then the compressor's input. Hundreds of MB of
    transient allocation, fired on every 20-chunk checkpoint during a
    grind and again on every rolling refresh — against a kill line the
    API has been hitting all week.

    Streaming into a GzipFile holds one row's JSON at a time. The
    OUTPUT is the only thing that grows, and it is a few MB compressed.
    The format is byte-identical to what _unpack_rows already reads: a
    JSON array, assembled incrementally instead of all at once.

    This is the memory candidate I should have gone to first. The type
    filter I shipped before it cut the row count from 317,681 to at
    most 300,000 — about 6% — and I had described it as the fix.
    """
    import base64
    import gzip
    import io

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=5) as out:
        out.write(b"[")
        first = True
        for r in rows:
            if first:
                first = False
            else:
                out.write(b",")
            out.write(json.dumps(r, default=str).encode())
        out.write(b"]")
    raw = buf.getvalue()
    buf.close()
    return base64.b64encode(raw).decode()


def _unpack_rows(gz: str) -> list[dict]:
    """Drop each intermediate as soon as the next one exists.

    Same shape of problem in the other direction: the base64 str, the
    compressed bytes and the decompressed bytes were all live at once
    on the boot path. json.loads still needs the whole document, so
    this cannot be made free — but three simultaneous copies can be
    made into one.
    """
    import base64
    import gzip

    comp = base64.b64decode(gz)
    del gz
    plain = gzip.decompress(comp)
    del comp
    try:
        return json.loads(plain)
    finally:
        del plain


async def _save_snapshot(pool: Any, rows: list[dict], *, complete: bool,
                         last: str = "") -> None:
    """Best-effort; a failed save just means the next boot grinds more."""
    try:
        gz = await asyncio.to_thread(_pack_rows, rows)
        await pool.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            _SNAP_KEY, json.dumps({"at": time.time(), "n": len(rows),
                                   "complete": complete, "last": last,
                                   "gz": gz}))
        if complete:
            _snap_state["at"] = time.time()
        _malloc_trim()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("snapshot save failed",
                                            exc_info=True)


async def _load_snapshot(pool: Any) -> dict | None:
    try:
        val = await asyncio.wait_for(pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1", _SNAP_KEY),
            timeout=120)
        if not val:
            return None
        obj = json.loads(val) if isinstance(val, str) else val
        rows = await asyncio.to_thread(_unpack_rows, obj.get("gz") or "")
        return {"rows": rows, "at": float(obj.get("at") or 0),
                "complete": bool(obj.get("complete")),
                "last": str(obj.get("last") or "")}
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("snapshot load failed",
                                            exc_info=True)
        return None
# RESUMABLE progress: the pass is ~73 chunks and used to be
# all-or-nothing — one flaky chunk anywhere restarted it from zero, so
# under steady DB load it NEVER completed (the true root cause of the
# 2026-08-08 21:35Z freeze). Progress survives across retries; each
# attempt advances at least one chunk, so completion is guaranteed.
_hydrate_progress: dict = {"last": "", "rows": []}


async def _hydrate_all(pool: Any) -> list[dict]:
    """Stream the archive out of Postgres in id-keyed chunks, slimming
    each chunk before fetching the next. The single all-rows fetch held
    ~172k FULL venue payloads in RAM at once — a transient spike measured
    in hundreds of MB, fired on every cold boot and every 15s retry,
    against a 2 GB kill line. Chunks cap the high-water at ~10k payloads,
    and the trim hands the parse arenas back to the OS."""
    out: list[dict] = _hydrate_progress["rows"]
    last: str = _hydrate_progress["last"]
    if not out:
        snap = await _load_snapshot(pool)
        if snap:
            age = time.time() - snap["at"]
            if snap["complete"] and age < _SNAP_MAX_AGE_S:
                # One small read IS the hydrate; the window union covers
                # everything since the snapshot was written.
                _hydrate_err.update(err=None, at=time.time())
                _snap_state["at"] = snap["at"]
                _malloc_trim()
                return snap["rows"]
            if snap["rows"]:
                # Partial checkpoint from a previous process: resume the
                # grind from where IT died instead of from zero.
                out = snap["rows"]
                last = snap["last"]
                _hydrate_progress.update(last=last, rows=out)
    try:
        # ONE streaming server-side cursor, not repeated LIMIT queries:
        # the planner answered "WHERE id > $1 ORDER BY id LIMIT n" with a
        # fresh scan of the whole table per chunk (probe 2026-08-09
        # 15:00Z: TimeoutError at chunk 6, 30s+ per chunk) — 73 chunks of
        # that never finishes. A cursor scans once and streams.
        async with pool.acquire() as con:
            async with con.transaction():
                cur = await con.cursor(
                    "SELECT id, payload FROM pmus_activity_archive "
                    "WHERE id > $1 "
                    "  AND payload->>'type' = ANY($2::text[]) "
                    "ORDER BY id", last, list(ARCHIVE_TYPES))
                while True:
                    rows = await asyncio.wait_for(cur.fetch(2500),
                                                  timeout=60)
                    if not rows:
                        break
                    last = rows[-1]["id"]
                    _hydrate_err["chunks"] += 1

                    def _parse(chunk: Any = rows) -> list[dict]:
                        return [_slim(json.loads(r["payload"])
                                      if isinstance(r["payload"], str)
                                      else r["payload"])
                                for r in chunk]

                    out.extend(await asyncio.to_thread(_parse))
                    _hydrate_progress.update(last=last, rows=out)
                    if _hydrate_err["chunks"] % 20 == 0:
                        await _save_snapshot(pool, out, complete=False,
                                             last=last)
    except Exception as exc:  # noqa: BLE001 — record + keep progress
        # Probe-readable failure, resumable next attempt from `last`.
        _hydrate_err.update(err=f"{type(exc).__name__}: {str(exc)[:200]}",
                            at=time.time())
        raise
    _hydrate_err.update(err=None, at=time.time())
    # Complete: checkpoint the finished archive, hand the rows over.
    await _save_snapshot(pool, out, complete=True)
    _hydrate_progress.update(last="", rows=[])
    _malloc_trim()
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
                parsed = await _hydrate_all(pool)
                _archive_cache["data"] = parsed
                _archive_cache["ts"] = time.time()
                logging.getLogger(__name__).warning(
                    "archive hydrated on retry: %s rows", len(parsed))
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).exception(
                    "archive hydrate retry failed; retrying in 15s")

    _hydrate_task = asyncio.get_running_loop().create_task(_loop())


def _slim_relevant(acts: list) -> list[dict]:
    """Slim only the activities build() can read.

    The SQL type filter fixes the COLD BOOT. This fixes the ratchet:
    every refresh unions the fresh venue window into the cache, and that
    window carries all activity types. Without filtering here the
    unread types accumulate straight back into RAM at ~15k/day and the
    boot-time saving evaporates within a day — which is exactly the
    shape of the memory ratchet documented below."""
    return [_slim(a) for a in acts
            if (a or {}).get("type") in ARCHIVE_TYPES]


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
        # BOUNDED SEED (memory fix 2026-08-25). This loaded EVERY id —
        # 317,681 strings, tens of MB — to skip re-serializing rows we
        # already have. But the venue's activity feed is a sliding
        # ~1,200-row window: an id older than the last few days can
        # never appear in it again, so keeping it costs memory and
        # saves nothing. The INSERT ... ON CONFLICT DO NOTHING below is
        # still the authoritative guard, so a miss here is a wasted
        # serialization, never a duplicate row.
        known = await pool.fetch(
            "SELECT id FROM pmus_activity_archive "
            "WHERE ts IS NULL OR ts > $1",
            time.time() - _ARCHIVED_ID_WINDOW_S)
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
            # _any_ts, NOT _act_ts (2026-08-25).
            #
            # A POSITION_RESOLUTION carries its timestamp NESTED under
            # positionResolution, not at the top level. _act_ts reads
            # only the top level, so every resolution archived here got
            # ts = 0.0 — and the settlement writer treats a zero as
            # "unknown" and falls through to
            # `settled_at = COALESCE($3, settled_at, now())`.
            #
            # The consequence is a reporting lie, not a trading bug:
            # the analytics crawl reached 79 of 1,229 markets live, so
            # up to 93.6% of the book settles from the archive — and
            # every one of those lands on the CALENDAR DAY WE READ IT
            # rather than the day it resolved. Against a -$13,398.96
            # book that is roughly -$12,542 stamped onto a day that did
            # not lose it, which is exactly the kind of number the
            # owner has an absolute rule about.
            #
            # _any_ts already exists and already reads the nested
            # positionResolution time; it was simply never wired here.
            out.append((aid, float(_any_ts(a) or 0.0),
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
        parsed = None
        for wait in (0.0, 1.0, 4.0):
            if wait:
                await asyncio.sleep(wait)
            try:
                parsed = await _hydrate_all(pool)
                break
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).exception("archive hydrate retry")
        if parsed is None:
            _ensure_hydrate_retry()
            return _slim_relevant([a for _, _, _, a in new])
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
        parsed = _archive_cache["data"] + _slim_relevant(
            [a for _, _, _, a in new])

    _archive_cache["data"], _archive_cache["ts"] = parsed, time.time()
    # Rolling snapshot refresh: keeps the one-read boot path fresh so the
    # window union always covers the gap since it was written.
    if time.time() - _snap_state["at"] > _SNAP_REFRESH_S:
        _snap_state["at"] = time.time()   # claim before the slow save
        asyncio.get_running_loop().create_task(
            _save_snapshot(pool, parsed, complete=True))
    return parsed


# ── Deploy-survival persistence (owner report 2026-08-07: the page
# showed FEWER settled trades minutes after showing more — every deploy
# cold-boots this process and the first thin window briefly regressed
# the record). The last served payload persists in the database; a
# fresh boot serves it instantly instead of blocking, refusing, or
# stepping backward, and a fresh build that shows fewer settled than
# the persisted high-water serves the persisted copy instead.
# Key VERSIONED with the record's basis: the account-basis payloads of
# 2026-08-08 persisted with ~$17K stake and ~1100 settled as high-water;
# the AI-basis revert legitimately shows LESS, and under the old key the
# shrink guards would refuse it forever and serve the inflated payload.
# A basis change starts its own history.
# _ai3 (2026-08-09): the _ai2 high-water froze the page on 2026-08-08
# 21:35Z's payload for 16h; a poisoned baseline is discarded, not argued
# with. A basis or baseline reset starts its own history.
_PERSIST_KEY = "track_record_last_payload_ai3"
_persist_state: dict[str, float] = {"ts": 0.0, "settled": -1.0,
                                    "stake": -1.0, "total": 0.0}

# A degraded build can GROW the settled count while losing half its
# activities (missing redemptions turn wins into losses; missing fills
# shrink every stake) — exactly what put fake negatives on the homepage
# on 2026-08-07 ~01:00Z: settled 958->963 while settled stake halved
# $6.9k->$2.9k and the corrupt payload displaced the good persisted one.
# Settled stake NEVER legitimately shrinks (settlements accumulate), so
# ANY meaningful shrink is data loss, not trading. 0.75 let a degraded
# build through at 0.76x on 2026-08-08 evening (-$165 of fake losses on
# the homepage while people watched); 2% covers float jitter only.
_STAKE_SHRINK_FLOOR = 0.98

# Refused-build history for the freeze escape hatch (2026-08-09): the
# shrink guard alone froze the page on yesterday's payload for ~16h.
_refused: dict = {"streak": 0, "stakes": [], "settled": []}


def _stake_of(payload: dict) -> float:
    return float((payload.get("summary") or {}).get("settled_stake") or 0)


def _total_of(payload: dict) -> float:
    """Whole-account settled stake regardless of attribution: the
    attributed headline plus the excluded_unattributed bucket. Lost
    activities shrink this; a row merely re-binned between cohorts
    does not."""
    return _stake_of(payload) + float(
        (payload.get("excluded_unattributed") or {}).get("stake") or 0)


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
        _persist_state["total"] = max(_persist_state.get("total", 0.0),
                                      _total_of(payload))
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
            _persist_state["total"] = max(_persist_state.get("total", 0.0),
                                          _total_of(payload))
        return payload
    except Exception:  # noqa: BLE001
        return None


async def _load_legacy_persisted() -> dict | None:
    """Previous-generation persisted payload, serve-only: it never touches
    the high-water state (that is exactly the poison a key bump exists to
    shed)."""
    try:
        from ..db import get_pool

        pool = await get_pool()
        val = await pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1",
            "track_record_last_payload_ai2")
        if not val:
            return None
        obj = json.loads(val) if isinstance(val, str) else val
        return obj.get("payload")
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


# Built-payload cache: build() walks ~170k archived activities; running
# it PER PAGE REQUEST (30s polls x N viewers) is both the memory churn
# behind the 1.9GB RSS climb and the widest exposure window for
# degraded-data builds. One build serves every viewer for the TTL.
_payload_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_PAYLOAD_TTL = 25.0

# Last GOOD attribution sets (engine mirror + sleeve slugs). Provenance
# is the P&L basis — see the fail-closed block in track_record().
_prov_cache: dict[str, Any] = {"attributed": None, "copy": None,
                               "manual": None, "at": 0.0}


async def track_record(since: str | None = None,
                       max_stake: float | None = None) -> dict:
    cfg = settings()
    if not (cfg.pmus_key_id and cfg.pmus_secret_key):
        return {"configured": False}
    if (since is None and max_stake is None
            and _payload_cache["data"] is not None
            and time.time() - _payload_cache["ts"] < _PAYLOAD_TTL):
        return _payload_cache["data"]
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
                    "served_by": "cold_boot_persisted", **persisted}
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
    if not archive and _hydrate_progress["rows"]:
        # EMERGENCY PROMOTION (owner 2026-08-09 ~16:00Z: "front end still
        # stuck. Please fix it immediately"): the resumable hydrate had
        # read ~187k rows — the whole archive — but the crawling DB kept
        # timing out the final empty-fetch confirmation, so the buffer
        # sat unused while the page served a thin window build. Progress
        # covering >=98% of known history IS the archive for serving
        # purposes; the fresh window union covers any tail, and the next
        # completed pass replaces it wholesale.
        done = len(_hydrate_progress["rows"])
        if done >= max(len(_archived_ids), 1) * 0.98:
            archive = _hydrate_progress["rows"]
            # A promoted buffer is snapshot-worthy: persist it so the
            # NEXT boot is one small read, not another grind -- AS A
            # CHECKPOINT, never as a completion (third adversarial
            # review of the settlement dating, 2026-09-02). The 98%
            # gate above was written when _archived_ids held every id;
            # since the bounded seed it holds a week of ids across all
            # types while the grind reads the whole filtered table, so
            # the gate is met a third of the way through. A partial
            # buffer saved complete=True would be served by the next
            # boot's short-circuit and re-persisted by the rolling save
            # for good: a silently truncated archive, the exact shape
            # the v1 -> v2 note above describes. The checkpoint carries
            # the grind's cursor, so the next boot resumes from it.
            if time.time() - _snap_state["at"] > _SNAP_REFRESH_S:
                _snap_state["at"] = time.time()
                from ..db import get_pool as _gp
                _resume_from = _hydrate_progress["last"]

                async def _snap_bg() -> None:
                    try:
                        await _save_snapshot(await _gp(), archive,
                                             complete=False,
                                             last=_resume_from)
                    except Exception:  # noqa: BLE001
                        pass
                asyncio.get_running_loop().create_task(_snap_bg())
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
                    "served_by": "hydrating_persisted",
                    "hydrate_err": dict(_hydrate_err), **persisted}
        # Interim service: a baseline reset leaves no persisted copy
        # under the new key while hydration completes. The PREVIOUS
        # key's snapshot with an honest STALE badge (the frontend ages
        # generated_at) beats an error page in front of viewers.
        legacy = await _load_legacy_persisted()
        if legacy is not None:
            return {"configured": True, "restored_across_deploy": True,
                    "served_by": "legacy_persisted",
                    "hydrate_err": dict(_hydrate_err), **legacy}
        return {"configured": True,
                "hydrate_err": dict(_hydrate_err),
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
    provenance = "live"
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
        # POSITIVE membership, not a blocklist (owner order 2026-08-22):
        # "not manual/underdog" let every retired-engine and arb row
        # wear the copy-sleeve tag; the copy cohort is exactly the
        # promoted COPY_WHALES roster, one source of truth.
        from .copies_record import COPY_WHALES

        cp = await pool.fetch(
            "SELECT DISTINCT us_market_slug FROM live_orders "
            "WHERE us_market_slug IS NOT NULL "
            "AND lower(COALESCE(whale_username, '')) = ANY($1::text[])",
            list(COPY_WHALES))
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
        _prov_cache.update({"attributed": attributed, "copy": copy_slugs,
                            "manual": manual_slugs, "at": time.time()})
    except Exception:  # noqa: BLE001
        # Provenance IS the basis, not an upgrade (owner P&L directive:
        # headline = AI trading only). A DB flake here used to silently
        # disable the cohort filter and publish a different population's
        # number under the AI's headline (observed 2026-08-09 22:37Z:
        # net flipped +$128 -> -$13 in one build because the attribution
        # queries failed while the archive read succeeded). Fail CLOSED:
        # reuse the last good attribution sets — slug sets only grow, so
        # minutes-stale attribution misclassifies at most the newest
        # rows into 'unattributed' (excluded), never a wrong headline.
        if _prov_cache["at"] > 0:
            attributed = _prov_cache["attributed"]
            copy_slugs = _prov_cache["copy"]
            manual_slugs = _prov_cache["manual"]
            provenance = "cached"
        else:
            # No attribution has EVER loaded in this process: a fresh
            # build would carry the wrong cohort. Serve the persisted
            # payload instead of publishing it.
            provenance = "refused"
    snapshot = {
        "raw_ts": _raw_cache["ts"],
        "age_s": round(time.time() - _raw_cache["ts"], 1),
        "positions_pages": raw.get("positions_pages"),
        "positions_complete": bool(raw.get("positions_complete", True)),
        "refresh_error": _refresh_health["error"],
        "refresh_error_streak": _refresh_health["streak"],
    }
    if provenance == "refused":
        persisted = await _load_persisted() or await _load_legacy_persisted()
        if persisted:
            out = {"configured": True, **source, "snapshot": snapshot,
                   "served_by": "provenance_refused",
                   "provenance": "refused", **persisted}
            if since is None and max_stake is None:
                _payload_cache["data"] = out
                _payload_cache["ts"] = time.time()
            return out
        # Nothing persisted either (first boot against a dead DB):
        # size-cap-only is the only basis available; disclose it.
    payload = {"configured": True, **source, "snapshot": snapshot,
               "provenance": provenance,
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
        # ESCAPE HATCH (owner report 2026-08-09: the page served
        # yesterday's numbers for ~16h while saying SYNCED). The guard
        # had no way back: one legitimate 2% recomposition dip (a
        # settled row moving to an excluded cohort) refused every fresh
        # build forever. Distinguish loss from recomposition by
        # SELF-CONSISTENCY: transient data loss wobbles build to build;
        # a real recomposition is stable. Three consecutive refused
        # builds agreeing within 1% stake and ±2 settled = the truth
        # changed — re-baseline and serve fresh.
        _refused["streak"] += 1
        _refused["stakes"] = (_refused["stakes"]
                              + [_stake_of(payload)])[-5:]
        _refused["settled"] = (_refused["settled"] + [fresh_settled])[-5:]
        s3, n3 = _refused["stakes"][-3:], _refused["settled"][-3:]
        agree = (len(s3) >= 3 and max(s3) > 0
                 and (max(s3) - min(s3)) <= 0.01 * max(s3)
                 and (max(n3) - min(n3)) <= 2)
        # SECOND WAY OUT: a record that is GROWING is not a record that is
        # corrupt. The stability test above assumes a refused build sits
        # still, so it can only fire when trading is quiet — and on the
        # night of 2026-08-12 it never fired at all: settled climbed
        # 1473 -> 1480 -> 1491 across three cycles as the overnight slate
        # resolved, never holding within +/-2, while the page served
        # 3-hour-old numbers showing FEWER settled trades than the truth.
        # Data loss wobbles DOWN and jitters; three builds each adding
        # settled rows and stake is trading, not corruption. Both series
        # must be non-decreasing, so a single shrinking build still stops
        # here (that is the 2026-08-07 fake-negatives case).
        growing = (len(s3) >= 3
                   and all(b >= a for a, b in zip(n3, n3[1:]))
                   and all(b >= a for a, b in zip(s3, s3[1:]))
                   and n3[-1] > n3[0])
        # THIRD WAY OUT (2026-08-19 overnight deadlock, streak stuck at
        # 4+): settled GREW past the high-water while attributed stake
        # shrank — archive absorption re-binned copy rows into
        # excluded_unattributed, so 'agree' (needs settled within ±2)
        # and 'growing' (needs stake non-decreasing) could never fire
        # while trading continued. Row count is the real loss detector:
        # lost activities cannot ADD settled rows. When no row was lost
        # AND the missing stake is visible in the unattributed bucket,
        # this is recomposition, not corruption — re-baseline.
        recomposed = (fresh_settled >= _persist_state["settled"]
                      and _persist_state.get("total", 0.0) > 0
                      and _total_of(payload)
                      >= _persist_state["total"] * _STAKE_SHRINK_FLOOR)
        if agree or growing or recomposed:
            logging.getLogger(__name__).warning(
                "track-record re-baseline after %d consistent refused "
                "builds: settled %s->%s stake %.2f->%.2f",
                _refused["streak"], _persist_state["settled"],
                fresh_settled, _persist_state["stake"], _stake_of(payload))
            _persist_state["settled"] = fresh_settled
            _persist_state["stake"] = _stake_of(payload)
            _persist_state["total"] = _total_of(payload)
            _persist_state["ts"] = 0.0     # let the next persist through
            _refused.update(streak=0, stakes=[], settled=[])
            payload["rebaselined"] = {
                "refused_builds": _refused["streak"],
                "reason": ("consistent lower basis" if agree
                           else "growing record, not data loss"
                           if growing
                           else "recomposition: stake moved to "
                                "unattributed, no rows lost")}
        else:
            persisted = await _load_persisted()
            if persisted is not None:
                out = {"configured": True, "restored_across_deploy": True,
                       "served_by": "stale_guard", **persisted}
                # ALWAYS disclose staleness — a probe must read the
                # freeze on its first look, not infer it from identical
                # numbers across runs.
                out["stale_served"] = {
                    "refused_streak": _refused["streak"],
                    "fresh_settled": fresh_settled,
                    "fresh_stake": round(_stake_of(payload), 2),
                    "high_settled": _persist_state["settled"],
                    "high_stake": round(_persist_state["stake"], 2),
                }
                if degraded:
                    out["degraded_build"] = {
                        "fresh_settled_stake": round(_stake_of(payload), 2),
                        "high_water_stake": round(_persist_state["stake"], 2),
                        "note": "fresh build lost activities; serving last "
                                "good payload",
                    }
                # Cache the stale serve too: during the 16h freeze every
                # page view ran a full 173k-row build only to discard it.
                if since is None and max_stake is None:
                    _payload_cache["data"] = out
                    _payload_cache["ts"] = time.time()
                return out
    else:
        _refused.update(streak=0, stakes=[], settled=[])
    await _persist_payload(payload)
    if since is None and max_stake is None:
        _payload_cache["data"] = payload
        _payload_cache["ts"] = time.time()
    _malloc_trim()
    return payload
