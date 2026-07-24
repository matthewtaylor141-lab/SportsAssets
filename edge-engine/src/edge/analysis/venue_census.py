"""Retrospective venue census — how big is the pond, really?

Polymarket US publishes no historical order books or price series, so a
true "what would we have traded" backtest is impossible from public data.
What IS knowable retrospectively: which markets existed, when, in what
league, of what type, and with how much liquidity/volume.

This module measures that universe over the trailing N days and combines
it with the live funnel's measured conversion rates to produce an honest
trades-per-day estimate:

    markets/day x mapped% x tradeable% x threshold-clear% = fills/day

Every factor is measured, not assumed; the ones we can't measure yet are
reported as ranges with their source labelled.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


def _category_of(outcome_key: str, slug: str) -> str:
    from edge.fairvalue.lines import parse_outcome_line, slug_point

    parsed = parse_outcome_line(outcome_key)
    if parsed.kind == "total":
        return "total"
    if parsed.kind == "spread" or slug_point(slug) is not None:
        return "spread"
    return "moneyline"


def census(client, days: int = 30, page_limit: int = 100,
           max_pages_per_day: int = 20) -> dict:
    """Walk the venue's closed sports events day by day.

    client: a polymarket_us.PolymarketUS (public gateway is enough).
    Returns per-day and aggregate counts of markets by category/league,
    plus liquidity/volume distribution.
    """
    now = datetime.now(timezone.utc)
    per_day: dict[str, dict] = {}
    by_league: dict[str, int] = defaultdict(int)
    by_category: dict[str, int] = defaultdict(int)
    liquid_markets = 0
    total_markets = 0
    total_events = 0
    volume_sum = 0.0

    for d in range(days):
        day_start = (now - timedelta(days=d + 1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        key = day_start.strftime("%Y-%m-%d")
        day = {"events": 0, "markets": 0, "by_category": defaultdict(int),
               "liquid_markets": 0, "volume": 0.0}
        offset = 0
        for _ in range(max_pages_per_day):
            try:
                resp = client.events.list({
                    "limit": page_limit, "offset": offset, "categories": ["sports"],
                    "startTimeMin": day_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "startTimeMax": day_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }) or {}
            except Exception as exc:  # noqa: BLE001 — partial census still useful
                log.warning("census page failed (%s offset %s): %s", key, offset, exc)
                break
            events = resp.get("events") or []
            if not events:
                break
            for ev in events:
                day["events"] += 1
                league = ((ev.get("series") or {}).get("slug")
                          or (ev.get("tags") or [{}])[0].get("slug") or "?")
                for m in ev.get("markets") or []:
                    slug = m.get("slug") or ""
                    outcome = m.get("outcome") or m.get("title") or ""
                    cat = _category_of(outcome, slug)
                    day["markets"] += 1
                    day["by_category"][cat] += 1
                    by_category[cat] += 1
                    by_league[league] += 1
                    vol = float(m.get("volume") or 0)
                    liq = float(m.get("liquidity") or 0)
                    day["volume"] += vol
                    volume_sum += vol
                    total_markets += 1
                    if liq >= 100:  # a book deep enough to matter at our sizes
                        day["liquid_markets"] += 1
                        liquid_markets += 1
            if len(events) < page_limit:
                break
            offset += page_limit
        day["by_category"] = dict(day["by_category"])
        total_events += day["events"]
        per_day[key] = day

    days_with_data = sum(1 for v in per_day.values() if v["markets"] > 0) or 1
    return {
        "days_requested": days,
        "days_with_data": days_with_data,
        "totals": {"events": total_events, "markets": total_markets,
                   "liquid_markets": liquid_markets,
                   "volume": round(volume_sum, 2)},
        "per_day_avg": {
            "events": round(total_events / days_with_data, 1),
            "markets": round(total_markets / days_with_data, 1),
            "liquid_markets": round(liquid_markets / days_with_data, 1),
        },
        "by_category": dict(by_category),
        "by_league": dict(sorted(by_league.items(), key=lambda kv: -kv[1])[:25]),
        "per_day": per_day,
    }


def estimate_fills_per_day(census_result: dict, tradeable_rate: float,
                           clear_rate: float) -> dict:
    """Funnel arithmetic with every factor labelled by its source.

    tradeable_rate: measured share of venue markets we map at >=0.95.
    clear_rate: measured share of PRICED outcomes that clear thresholds.
    """
    liquid = census_result["per_day_avg"]["liquid_markets"]
    priced = liquid * tradeable_rate
    fills = priced * clear_rate
    return {
        "liquid_markets_per_day": liquid,
        "tradeable_rate": round(tradeable_rate, 4),
        "clear_rate": round(clear_rate, 4),
        "estimated_priced_per_day": round(priced, 1),
        "estimated_fills_per_day": round(fills, 1),
        "note": "liquidity>=100 filter; rates are measured from live funnel "
                "telemetry, not assumed",
    }
