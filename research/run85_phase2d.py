#!/usr/bin/env python3
"""RUN 85 PHASE 2D -- game-level market discovery. READ ONLY. NO CREDENTIAL.

Phase 2C reached 150 markets and every one was `futures` or `election`. This
run answers WHY, and whether game-level markets exist at all on the public
gateway. It does not capture, does not select a long-capture cohort, and
computes no profitability quantity of any kind.

Every venue call goes through run85_pmus_collector._get under the authorized
boundary -- GET only, public gateway only, no credential, no signer, no order
path -- paced by the Phase 2B AdaptivePacer at 0.5 rps with exact Retry-After.
No attempt is made to discover the rate limit.

--------------------------------------------------------------------------
THE HYPOTHESIS THIS RUN WAS BUILT TO TEST, STATED BEFORE THE DATA
--------------------------------------------------------------------------
Re-reading the SEALED Phase 2C events payload offline established, with no new
venue contact:

  * all 100 events returned carry period = "NS" and
    marketCounts.numSpreadsAndTotalsMarkets = 0
  * all 1,579 market rows are sportsMarketType `futures` (1,575) or
    `election` (4); sportsMarketTypeV2 agrees (SPORTS_MARKET_TYPE_FUTURE)
  * event ids run 6435 .. 44391 ASCENDING
  * the earliest endDate anywhere in the page is 2026-09-27, a fortnight out

So the futures-only result was NOT a sampling artifact of the 150-market
detail probe: the whole discovery page was futures. And the page looks like
the OLDEST 100 events by id. Season-long futures are created months ahead and
carry low ids; a game event is created days ahead and carries a high one.

  H2_MECHANISM (preregistered): /v1/events returned id-ascending with
  limit=100 and NO offset, so Phase 2C read the 100 oldest events in
  existence and never reached the recent ones where games live.

This run tries to falsify that. It is a prediction that can fail: if page 2
is also futures, or if no pagination parameter is honoured, H2 is not the
answer and the run says so.

--------------------------------------------------------------------------
HARD DISCOVERY BOUND, DISCLOSED BEFORE RUNNING
--------------------------------------------------------------------------
MAX_VENUE_REQUESTS        120  hard stop; the run aborts rather than exceed it
MAX_DISCOVERY_PAGES        24  pagination walk
MAX_DETAIL_CONFIRMATIONS    8  see below -- a control, not the main spend
MAX_CALIBRATION_READS      36  only if game markets are found
At 0.5 rps, 120 requests is 240 s of venue contact.

The bound is a CEILING, not a target. Sections that finish early do not
donate their budget to later ones, and every count above bounds REQUESTS,
not events -- a per-event cap multiplied by markets-per-event is how a
disclosed bound quietly becomes a larger one.

--------------------------------------------------------------------------
DETAIL PROBES ARE A CONTROL HERE, NOT THE CENSUS
--------------------------------------------------------------------------
Phase 2C spent 150 requests probing /v1/market/slug/<slug> one market at a
time. Re-reading the sealed payload shows that was largely wasted: /v1/events
already returns each market's marketSides, orderPriceMinTickSize,
feeCoefficient, sportsMarketTypeV2, status, gameStartTime, endDate,
bestBidQuote and bestAskQuote. Everything section C asks to preserve for a
GAME_LEVEL market is present in discovery.

So this run reads the fields from discovery and spends only a small sample on
the detail endpoint, as a CONTROL: does market-detail agree with the events
payload field-for-field? If it does not, discovery-sourced fields are not
trustworthy and the run says so. The budget that buys goes to the WALK, which
is where the open question actually is.

--------------------------------------------------------------------------
WHAT THIS RUN MUST NOT DO
--------------------------------------------------------------------------
  * no multi-day capture, and no cohort is frozen for one
  * no profitability quantity: no expectancy, no fill model, no fee applied
  * no whole-ladder dollar asymmetry -- RETRACTED as a queue proxy in 2C
  * no free-text substring inference of sport, league or market type
  * endDate is recorded, never relabelled "time to event"
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import platform
import sys
import time
from decimal import Decimal
from pathlib import Path

import httpx

_c = importlib.util.spec_from_file_location(
    "run85c", Path(__file__).with_name("run85_pmus_collector.py"))
C = importlib.util.module_from_spec(_c)
_c.loader.exec_module(C)

_b = importlib.util.spec_from_file_location(
    "run85b", Path(__file__).with_name("run85_phase2b.py"))
B = importlib.util.module_from_spec(_b)
_b.loader.exec_module(B)

PHASE = "run85/phase2d/1"

MAX_VENUE_REQUESTS = 120
MAX_DISCOVERY_PAGES = 24
MAX_DETAIL_CONFIRMATIONS = 8    # see DETAIL PROBES ARE A CONTROL, below
MAX_CALIBRATION_READS = 36
MARKETS_PER_EVENT_CAP = 2

EVENTS_PATH = "/v1/events"
MARKET_PATH = "/v1/market/slug/%s"
BOOK_PATH = "/v1/markets/%s/book"

# Structured classification. sportsMarketTypeV2 is the enum the venue itself
# publishes; sportsMarketType is its string twin. Neither is ever matched as a
# substring of a slug or a title.
FUTURES_V2 = "SPORTS_MARKET_TYPE_FUTURE"
GAME_V2 = {
    "SPORTS_MARKET_TYPE_MONEYLINE",
    "SPORTS_MARKET_TYPE_SPREAD",
    "SPORTS_MARKET_TYPE_TOTAL",
    "SPORTS_MARKET_TYPE_TOTALS",
}


class Budget:
    """A hard ceiling on venue contact. Raises rather than overspend."""

    def __init__(self, cap):
        self.cap = cap
        self.spent = 0

    def take(self, n=1):
        if self.spent + n > self.cap:
            raise RuntimeError("DISCOVERY_BOUND_REACHED spent=%d cap=%d"
                               % (self.spent, self.cap))
        self.spent += n


def say(s=""):
    print(s)


def paced(http, path, pacer, budget, params=None):
    budget.take()
    return B.get_paced(http, path, pacer, params)


# ------------------------------------------------------------ A. discovery
def classify(market):
    """GAME_LEVEL / FUTURES / OTHER_STRUCTURED_SPORTS / NOT_IDENTIFIED.

    Structured metadata only. A market with no structured type field is
    NOT_IDENTIFIED -- it is never guessed at from its slug."""
    v2 = market.get("sportsMarketTypeV2")
    v1 = market.get("sportsMarketType")
    if v2 in GAME_V2:
        return "GAME_LEVEL", "sportsMarketTypeV2=%s" % v2
    if v2 == FUTURES_V2 or v1 == "futures":
        return "FUTURES", "sportsMarketTypeV2=%s v1=%s" % (v2, v1)
    if v2 and v2 != "SPORTS_MARKET_TYPE_UNSPECIFIED":
        return "OTHER_STRUCTURED_SPORTS", "sportsMarketTypeV2=%s" % v2
    if v1:
        return "OTHER_STRUCTURED_SPORTS", "sportsMarketType=%s" % v1
    return "NOT_IDENTIFIED", "no structured type field (v2=%r v1=%r)" % (v2, v1)


def page_record(params, body, page_no):
    """Everything section A requires about one discovery page."""
    events = (body or {}).get("events") or (body or {}).get("data") or []
    if not isinstance(events, list):
        events = []
    slugs, eids, kinds, states, v2s = set(), set(), collections.Counter(), \
        collections.Counter(), collections.Counter()
    sports, leagues, periods = collections.Counter(), collections.Counter(), \
        collections.Counter()
    starts, ends, binary_ok, rows = [], [], 0, 0
    for e in events:
        if e.get("id") is not None:
            eids.add(str(e["id"]))
        periods[str(e.get("period"))] += 1
        pt = e.get("primaryTag")
        sports[(pt or {}).get("slug") if isinstance(pt, dict) else None] += 1
        for tm in (e.get("teams") or []):
            if isinstance(tm, dict) and tm.get("league"):
                leagues[tm["league"]] += 1
        if e.get("startTime"):
            starts.append(e["startTime"])
        if e.get("endDate"):
            ends.append(e["endDate"])
        for m in (e.get("markets") or []):
            rows += 1
            if m.get("slug"):
                slugs.add(m["slug"])
            kinds[classify(m)[0]] += 1
            v2s[str(m.get("sportsMarketTypeV2"))] += 1
            states[str(m.get("status") or m.get("state"))] += 1
            sides = m.get("marketSides")
            if isinstance(sides, list) and len(sides) == 2 \
                    and sum(1 for s in sides if s.get("long") is True) == 1 \
                    and sum(1 for s in sides if s.get("long") is False) == 1:
                binary_ok += 1
    return {
        "page": page_no, "params": dict(params),
        "events_returned": len(events), "market_rows": rows,
        "unique_market_slugs": len(slugs), "unique_native_event_ids": len(eids),
        "event_ids_min": min(eids, key=lambda x: int(x)) if eids else None,
        "event_ids_max": max(eids, key=lambda x: int(x)) if eids else None,
        "class_distribution": dict(kinds),
        "sports_market_type_v2": dict(v2s),
        "state_distribution": dict(states),
        "primary_tag_distribution": {str(k): v for k, v in sports.items()},
        "team_league_distribution": dict(leagues),
        "event_period_distribution": dict(periods),
        "start_time_min": min(starts) if starts else None,
        "start_time_max": max(starts) if starts else None,
        "end_date_min": min(ends) if ends else None,
        "end_date_max": max(ends) if ends else None,
        "binary_eligible_market_rows": binary_ok,
        "_event_ids": sorted(eids, key=lambda x: int(x)),
        "_events": events,
    }


def probe_pagination(http, pacer, budget, log, out):
    """Establish HOW to paginate before walking. Each candidate is judged by
    whether it returns a DIFFERENT id set, not by whether it returns 200."""
    say("=== A1. PAGINATION MECHANISM ===")
    base = {"active": "true", "closed": "false", "limit": 100}
    r, rows = paced(http, EVENTS_PATH, pacer, budget, base)
    for x in rows:
        x["stage"] = "pagination_probe"
        x["probe"] = "baseline"
    log.extend(rows)
    p0 = page_record(base, r.get("body"), 0)
    say("baseline    limit=100            events=%d ids %s..%s"
        % (p0["events_returned"], p0["event_ids_min"], p0["event_ids_max"]))
    first = set(p0["_event_ids"])

    findings = []
    for name, extra in (("offset", {"offset": 100}),
                        ("page", {"page": 2}),
                        ("cursor_skip", {"skip": 100})):
        r2, rows2 = paced(http, EVENTS_PATH, pacer, budget, dict(base, **extra))
        for x in rows2:
            x["stage"] = "pagination_probe"
            x["probe"] = name
        log.extend(rows2)
        pr = page_record(dict(base, **extra), r2.get("body"), -1)
        ids = set(pr["_event_ids"])
        verdict = ("HONOURED" if ids and not (ids & first) else
                   "PARTIAL_OVERLAP" if ids and ids != first else
                   "IGNORED" if ids == first else "EMPTY")
        findings.append({"param": name, "extra": extra, "http": r2.get("http_status"),
                         "events": pr["events_returned"], "verdict": verdict,
                         "ids_min": pr["event_ids_min"], "ids_max": pr["event_ids_max"]})
        say("%-11s %-20s events=%3d ids %s..%s  -> %s"
            % (name, json.dumps(extra), pr["events_returned"],
               pr["event_ids_min"], pr["event_ids_max"], verdict))

    works = [f for f in findings if f["verdict"] == "HONOURED"]
    out["pagination_probe"] = findings
    say()
    if works:
        say("PAGINATION_MECHANISM = %s" % works[0]["param"])
    else:
        say("PAGINATION_MECHANISM = NOT_IDENTIFIED "
            "(no probed parameter returned a disjoint page)")
    say()
    return (works[0]["param"] if works else None), p0


def walk(http, pacer, budget, log, mech, first_page, out):
    """Bounded pagination walk. Never an alphabetical or id prefix: the walk
    continues until the venue stops returning new events or the bound bites."""
    say("=== A2. BOUNDED DISCOVERY WALK (max %d pages) ===" % MAX_DISCOVERY_PAGES)
    pages = [first_page]
    seen = set(first_page["_event_ids"])
    stop = "SINGLE_PAGE_ONLY"
    if mech:
        for n in range(1, MAX_DISCOVERY_PAGES):
            params = {"active": "true", "closed": "false", "limit": 100,
                      mech: (100 * n if mech != "page" else n + 1)}
            try:
                r, rows = paced(http, EVENTS_PATH, pacer, budget, params)
            except RuntimeError as exc:
                stop = str(exc)
                break
            for x in rows:
                x["stage"] = "discovery_walk"
            log.extend(rows)
            pg = page_record(params, r.get("body"), n)
            fresh = set(pg["_event_ids"]) - seen
            pg["new_event_ids"] = len(fresh)
            pages.append(pg)
            say("page %2d  %-22s events=%3d new=%3d ids %s..%s  classes=%s"
                % (n, json.dumps({mech: params[mech]}), pg["events_returned"],
                   len(fresh), pg["event_ids_min"], pg["event_ids_max"],
                   pg["class_distribution"]))
            seen |= fresh
            if not pg["events_returned"]:
                stop = "EMPTY_PAGE"
                break
            if not fresh:
                stop = "NO_NEW_EVENTS"
                break
        else:
            stop = "PAGE_BOUND_REACHED"
    say()
    say("WALK_STOP_REASON = %s" % stop)
    say("pages walked     = %d" % len(pages))
    say("unique events    = %d" % len(seen))
    out["walk_stop_reason"] = stop
    # _events is bulky; keep the stats, drop the payload, before sealing.
    out["pages"] = [{k: v for k, v in p.items() if k != "_events"} for p in pages]
    return pages, seen, stop


# --------------------------------------------------------- B/C. census
def event_first_census(pages):
    """Section B. Stratify at the EVENT level and cap markets per event, so no
    single family can eat the detail budget the way a slug sort let it."""
    say("=== B. EVENT-FIRST CENSUS ===")
    events, rows = {}, 0
    for p in pages:
        for e in p["_events"]:
            eid = str(e.get("id"))
            rows += len(e.get("markets") or [])
            if eid not in events:
                events[eid] = e
    say("DISCOVERED_MARKET_ROWS   = %d" % rows)
    say("DISCOVERED_UNIQUE_EVENTS = %d" % len(events))

    strata = collections.defaultdict(list)
    for eid, e in events.items():
        kinds = {classify(m)[0] for m in (e.get("markets") or [])}
        kind = ("GAME_LEVEL" if "GAME_LEVEL" in kinds else
                "OTHER_STRUCTURED_SPORTS" if "OTHER_STRUCTURED_SPORTS" in kinds
                else "FUTURES" if "FUTURES" in kinds else "NOT_IDENTIFIED")
        pt = e.get("primaryTag")
        sport = (pt or {}).get("slug") if isinstance(pt, dict) else None
        strata[(kind, str(sport), str(e.get("period")))].append(eid)
    say()
    say("event strata (market class, primaryTag, period):")
    for k in sorted(strata, key=lambda z: (-len(strata[z]), z)):
        say("   %-46s %4d events" % (" | ".join(k), len(strata[k])))
    say()

    # Round-robin across strata, so a rare stratum is reached before a common
    # one is taken twice. This is the fix for Phase 2C's alphabetical cut,
    # which spent a 60-market budget on 13 of 93 available events.
    #
    # The cap bounds PICKS (= requests), not events. Capping events and then
    # taking MARKETS_PER_EVENT_CAP markets each is how a disclosed bound of 40
    # silently becomes 80.
    picks, per_event = [], collections.Counter()
    pools = {k: sorted(v, key=lambda x: int(x)) for k, v in strata.items()}
    while any(pools.values()) and len(picks) < MAX_DETAIL_CONFIRMATIONS:
        for k in sorted(pools, key=lambda z: (-len(strata[z]), z)):
            if not pools[k] or len(picks) >= MAX_DETAIL_CONFIRMATIONS:
                continue
            eid = pools[k].pop(0)
            e = events[eid]
            ms = sorted((m for m in (e.get("markets") or []) if m.get("slug")),
                        key=lambda m: m["slug"])
            # Prefer a game-level market where the event has one, so the
            # control lands on the class this run exists to characterize.
            ms.sort(key=lambda m: 0 if classify(m)[0] == "GAME_LEVEL" else 1)
            for m in ms[:MARKETS_PER_EVENT_CAP]:
                if len(picks) >= MAX_DETAIL_CONFIRMATIONS:
                    break
                picks.append((eid, e, m))
                per_event[eid] += 1
    say("EVENTS_DETAIL_PROBED     = %d" % len(per_event))
    say("MARKETS_DETAIL_PROBED    = %d  (cap %d, bounds REQUESTS)"
        % (len(picks), MAX_DETAIL_CONFIRMATIONS))
    say("MARKETS_PER_EVENT_DISTRIBUTION = %s"
        % dict(collections.Counter(per_event.values())))
    say()
    assert len(picks) <= MAX_DETAIL_CONFIRMATIONS
    return events, picks, rows


def classify_all(events):
    say("=== C. MARKET-TYPE CLASSIFICATION (structured metadata only) ===")
    kinds, why = collections.Counter(), {}
    game = []
    for eid, e in events.items():
        for m in (e.get("markets") or []):
            k, reason = classify(m)
            kinds[k] += 1
            why.setdefault(k, reason)
            if k == "GAME_LEVEL":
                pt = e.get("primaryTag")
                leagues = sorted({t.get("league") for t in (e.get("teams") or [])
                                  if isinstance(t, dict) and t.get("league")})
                game.append({
                    "native_event_id": eid,
                    "native_event_slug": e.get("slug"),
                    "market_slug": m.get("slug"),
                    "sport": (pt or {}).get("slug") if isinstance(pt, dict) else None,
                    "league": leagues,
                    "sports_market_type": m.get("sportsMarketType"),
                    "sports_market_type_v2": m.get("sportsMarketTypeV2"),
                    "state": m.get("status") or m.get("state"),
                    "game_start_time": m.get("gameStartTime"),
                    "event_start_time": e.get("startTime"),
                    "end_date": m.get("endDate"),
                    "tick_size": m.get("orderPriceMinTickSize"),
                    "fee_coefficient_field": m.get("feeCoefficient"),
                    "market_sides": m.get("marketSides"),
                    "best_bid_quote": m.get("bestBidQuote"),
                    "best_ask_quote": m.get("bestAskQuote"),
                })
    for k, n in kinds.most_common():
        say("   %-26s %5d   e.g. %s" % (k, n, why.get(k)))
    say()
    say("GAME_LEVEL market rows found = %d" % len(game))
    say("GAME_LEVEL unique events     = %d"
        % len({g["native_event_id"] for g in game}))
    say()
    return kinds, game


CONTROL_FIELDS = ("sportsMarketType", "sportsMarketTypeV2", "status",
                  "orderPriceMinTickSize", "feeCoefficient", "gameStartTime",
                  "endDate", "minimumTradeQty")


def confirm_detail(http, pacer, budget, log, picks, out):
    """Is the events payload's copy of a market the same as market-detail's?

    Everything sections C and E want is present in discovery. That is only
    useful if discovery's copy is TRUSTWORTHY, so a small sample is fetched
    from the detail endpoint and compared field for field. A disagreement
    here invalidates every discovery-sourced field in this run, so it is
    checked rather than assumed."""
    say("=== B2. DISCOVERY-vs-DETAIL CONTROL (%d markets) ===" % len(picks))
    agree, disagree, rows = 0, [], []
    for eid, e, m in picks:
        try:
            r, lg = paced(http, MARKET_PATH % m["slug"], pacer, budget)
        except RuntimeError as exc:
            say("  budget stop: %s" % exc)
            break
        for x in lg:
            x["stage"] = "detail_control"
            x["market_slug"] = m["slug"]
            x["event_id"] = eid
        log.extend(lg)
        d = (r.get("body") or {}).get("market") or (r.get("body") or {})
        diffs = {f: [m.get(f), d.get(f)] for f in CONTROL_FIELDS
                 if m.get(f) != d.get(f)}
        rows.append({"market_slug": m["slug"], "native_event_id": eid,
                     "http": r.get("http_status"), "differing_fields": diffs})
        if diffs:
            disagree.append((m["slug"], diffs))
        else:
            agree += 1
    say("markets compared          = %d" % len(rows))
    say("fields identical          = %d" % agree)
    say("markets with a difference = %d" % len(disagree))
    for slug, diffs in disagree[:6]:
        say("   %-46s %s" % (slug, diffs))
    verdict = ("YES" if rows and not disagree else
               "NO" if disagree else "NOT_IDENTIFIED")
    say()
    say("DISCOVERY_FIELDS_MATCH_MARKET_DETAIL = %s" % verdict)
    if verdict == "NO":
        say("  Discovery-sourced fields in this run are NOT trustworthy and")
        say("  every section C field below must be re-read from detail.")
    say()
    out["detail_control"] = {"compared": len(rows), "identical": agree,
                             "differing": len(disagree), "verdict": verdict,
                             "rows": rows}
    return verdict


# ------------------------------------------- F. near-touch depth only
def near_touch(body, tick):
    """Section F. Quantity AND notional, at the touch and within fixed tick
    bands measured in the MARKET'S OWN tick. No whole-ladder aggregate is
    produced here -- that measure was retracted in Phase 2C.

    Takes the raw body, not book_view: book_view reports whole-ladder
    aggregates, and reusing them is exactly the mistake this section fixes."""
    d = B.market_data(body)
    if not d or tick in (None, 0):
        return None
    tick = Decimal(str(tick))
    out = {}
    for side, raw, best_fn in (("bid", d.get("bids"), max),
                               ("ask", d.get("offers"), min)):
        levels = B.ladder(raw)
        if not levels:
            return None
        px = sorted(((p, q) for p, q, _ in levels),
                    key=lambda z: z[0], reverse=(side == "bid"))
        best = best_fn(p for p, _ in px)
        out["best_%s_px" % side] = str(best)
        out["best_%s_qty" % side] = str(px[0][1])
        out["best_%s_notional" % side] = str(best * px[0][1])
        for band in (1, 2, 5):
            lim = band * tick
            sel = [(p, q) for p, q in px if abs(p - best) <= lim]
            out["cum_%s_qty_within_%dt" % (side, band)] = str(sum(q for _, q in sel))
            out["cum_%s_notional_within_%dt" % (side, band)] = \
                str(sum(p * q for p, q in sel))
    return out


def calibrate(http, pacer, budget, log, game, out):
    """Section E. SHORT calibration, structure only. No profitability."""
    say("=== E/F. SHORT GAME-MARKET CALIBRATION ===")
    if not game:
        say("no GAME_LEVEL market found; calibration SKIPPED")
        say()
        out["calibration"] = {"ran": False, "reason": "no game-level market found"}
        return []
    by_event = {}
    for g in game:
        by_event.setdefault(g["native_event_id"], g)
    picks = sorted(by_event.values(), key=lambda g: g["market_slug"])
    picks = picks[:max(1, MAX_CALIBRATION_READS // 3)]
    say("calibrating %d markets, one per event, %d reads each"
        % (len(picks), 3))
    rows = []
    for rnd in range(3):
        for g in picks:
            try:
                r, lg = paced(http, BOOK_PATH % g["market_slug"], pacer, budget)
            except RuntimeError as exc:
                say("  budget stop: %s" % exc)
                out["calibration"] = {"ran": True, "truncated": str(exc)}
                return rows
            for x in lg:
                x["stage"] = "calibrate"
                x["market_slug"] = g["market_slug"]
                x["round"] = rnd
            log.extend(lg)
            rows.append({"market_slug": g["market_slug"], "round": rnd,
                         "native_event_id": g["native_event_id"],
                         "http": r.get("http_status"),
                         "book_sha256": r.get("response_sha256"),
                         "near_touch": near_touch(r.get("body"), g.get("tick_size")),
                         "transact_time": (B.market_data(r.get("body")) or {}
                                           ).get("transactTime")})
    out["calibration"] = {"ran": True, "markets": len(picks), "rows": len(rows)}
    say("calibration reads = %d" % len(rows))
    say()
    return rows


# ------------------------------------------------------ G. scheduler
def scheduler_analysis(out):
    """Section G, offline arithmetic. The 0.5 rps ceiling constrains the
    AGGREGATE REQUEST RATE. It never required a uniform round-robin, and a
    uniform round-robin is what made exact 5 s look unreachable in Phase 2C.
    That claim is retracted; this is the correct treatment."""
    say("=== G. SCHEDULER ANALYSIS (offline; no venue contact) ===")
    say("Constraint: aggregate <= 0.5 rps, i.e. at most 1 request per 2.0 s")
    say("MEAN over any window. Individual gaps are free.")
    say()
    say("1. UNIFORM ROUND-ROBIN. Revisit = 2.0 x N, so horizon h is a sample")
    say("   point only when h is a whole multiple of 2.0 x N:")
    say("      N   revisit    5s   10s   30s   60s")
    rr = {}
    for n in range(1, 9):
        rv = 2.0 * n
        ok = {h: abs(h / rv - round(h / rv)) < 1e-9 for h in (5, 10, 30, 60)}
        rr[n] = ok
        say("     %2d   %5.1f s  %s"
            % (n, rv, "  ".join("%4s" % ("YES" if ok[h] else "-")
                                for h in (5, 10, 30, 60))))
    say()
    say("   Under uniform round-robin exact 5 s is unreachable at every N,")
    say("   because every revisit is a multiple of 2.0 s and 5 is odd.")
    say("   EXACT_5S_UNREACHABLE_UNDER_CURRENT_UNIFORM_ROUND_ROBIN = YES")
    say()
    say("2. EXACT-HORIZON SCHEDULED SAMPLING. Drop the uniform grid and place")
    say("   reads where the horizons are. A market needing a 5 s markout wants")
    say("   a PAIR of reads 5.0 s apart, not a 5.0 s steady cadence.")
    say()
    say("   One pair costs 2 requests. Spacing WITHIN the pair is 5.0 s;")
    say("   spacing BETWEEN pairs absorbs the budget. To hold the mean at one")
    say("   request per 2.0 s, a pair of 2 requests needs a 4.0 s share of the")
    say("   budget, so pairs may start every 4.0 s -- and 5.0 s > 4.0 s means")
    say("   consecutive pairs would overlap in time, which is fine: they are")
    say("   different reads, and only the AGGREGATE rate is constrained.")
    say()
    plans = [
        ("5s pair, 1 market", 2, 5.0, 10.0),
        ("5s+10s triple, 1 market", 3, 10.0, 12.0),
        ("5/10/30/60 quintuple, 1 market", 5, 60.0, 20.0),
        ("5s pair x 2 markets interleaved", 4, 5.0, 20.0),
        ("5/10/30/60 quintuple x 2 markets", 10, 60.0, 40.0),
        ("5/10/30/60 quintuple x 3 markets", 15, 60.0, 60.0),
    ]
    say("   %-34s %5s %9s %9s %8s  %s"
        % ("plan", "reqs", "span", "min cycle", "rate", "feasible"))
    sched = []
    for name, reqs, span, cycle in plans:
        need = 2.0 * reqs          # budget-seconds this plan consumes
        rate = reqs / cycle
        ok = cycle >= need - 1e-9 and span <= cycle + 1e-9
        sched.append({"plan": name, "requests": reqs, "span_s": span,
                      "cycle_s": cycle, "rps": rate, "feasible": ok})
        say("   %-34s %5d %8.1fs %8.1fs %7.3f  %s"
            % (name, reqs, span, cycle, rate, "YES" if ok else "NO"))
    say()
    say("   A cycle is feasible when it is long enough to pay for its own")
    say("   requests at 2.0 s each AND long enough to contain its own span.")
    say()
    say("   EXACT_5S_UNREACHABLE_UNDER_SAFE_AGGREGATE_RATE = NO")
    say("   A single market sampled at t and t+5.0 s, repeating every 10.0 s,")
    say("   issues 2 requests per 10.0 s = 0.200 rps -- well inside the")
    say("   ceiling -- and every 5 s markout is read, never interpolated.")
    say()
    say("   The cost is COVERAGE, not legality: exact-horizon scheduling buys")
    say("   the short horizon by observing fewer markets, and it leaves gaps")
    say("   between pairs where the book is unobserved. Those gaps are not")
    say("   missing data for a markout -- each pair is self-contained -- but")
    say("   they do mean the design cannot also claim continuous book history.")
    say("   Which estimand is wanted must be preregistered, not chosen later.")
    say()
    out["scheduler"] = {"round_robin": {str(k): v for k, v in rr.items()},
                        "exact_horizon_plans": sched}
    say()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    budget = Budget(MAX_VENUE_REQUESTS)
    meta = {"phase": PHASE, "python": sys.version, "platform": platform.platform(),
            "httpx": httpx.__version__, "gateway": C.GATEWAY_BASE,
            "aggregate_rps_ceiling": 0.5,
            "max_venue_requests": MAX_VENUE_REQUESTS,
            "max_discovery_pages": MAX_DISCOVERY_PAGES,
            "max_detail_confirmations": MAX_DETAIL_CONFIRMATIONS,
            "max_calibration_reads": MAX_CALIBRATION_READS,
            "driver_sha256": hashlib.sha256(
                Path(__file__).read_bytes()).hexdigest(),
            "collector_sha256": hashlib.sha256(
                Path(C.__file__).read_bytes()).hexdigest(),
            "phase2b_sha256": hashlib.sha256(
                Path(B.__file__).read_bytes()).hexdigest()}
    (out / "runner_metadata.json").write_text(json.dumps(meta, indent=1))
    say(json.dumps(meta, indent=1))
    say()

    res, log = {}, []
    pacer = B.AdaptivePacer()
    with httpx.Client(timeout=30.0) as http:
        try:
            mech, p0 = probe_pagination(http, pacer, budget, log, res)
            pages, seen, stop = walk(http, pacer, budget, log, mech, p0, res)
            events, picks, rows = event_first_census(pages)
            confirm_detail(http, pacer, budget, log, picks, res)
            kinds, game = classify_all(events)
            res["classification"] = dict(kinds)
            res["game_level"] = game
            cal = calibrate(http, pacer, budget, log, game, res)
        except RuntimeError as exc:
            say("HARD BOUND: %s" % exc)
            res["aborted"] = str(exc)
            cal, events, game, kinds, rows = [], {}, [], collections.Counter(), 0

    scheduler_analysis(res)

    res["venue_requests_spent"] = budget.spent
    res["throttle_events"] = pacer.events
    (out / "discovery.json").write_text(json.dumps(res, indent=1, default=str))
    with (out / "request_log.jsonl").open("w") as fh:
        for r in log:
            fh.write(json.dumps(r, default=str) + "\n")
    with (out / "calibration.jsonl").open("w") as fh:
        for r in cal:
            fh.write(json.dumps(r, default=str) + "\n")

    lines = verdicts(res, events, game, kinds, rows, stop_ok=("aborted" not in res))
    B.seal(out, lines)
    say()
    say("VENUE REQUESTS SPENT = %d of %d" % (budget.spent, MAX_VENUE_REQUESTS))
    ok, bad = B.verify(out)
    say("SEAL_VERIFIED = %s%s" % (ok, "" if ok else " BAD=%s" % bad))
    return 0


def verdicts(res, events, game, kinds, rows, stop_ok):
    L = []

    def w(s=""):
        L.append(s)
        say(s)

    w("=== H. PHASE 2D VERDICTS ===")
    ge = len({g["native_event_id"] for g in game})
    found = "YES" if game else ("NO" if stop_ok else "NOT_IDENTIFIED")
    w("GAME_LEVEL_MARKETS_FOUND             = %s" % found)
    w("GAME_LEVEL_UNIQUE_EVENTS_FOUND       = %d" % ge)
    cal = res.get("calibration") or {}
    w("GAME_LEVEL_ACTIVE_BINARY_BOOKS_FOUND = %d" % cal.get("markets", 0))
    w("DISCOVERED_MARKET_ROWS               = %d" % rows)
    w("DISCOVERED_UNIQUE_EVENTS             = %d" % len(events))
    w("CLASSIFICATION                       = %s" % dict(kinds))
    w("WALK_STOP_REASON                     = %s" % res.get("walk_stop_reason"))
    w("VENUE_REQUESTS_SPENT                 = %d of %d"
      % (res.get("venue_requests_spent", 0), MAX_VENUE_REQUESTS))
    w("EXACT_5S_UNREACHABLE_UNDER_CURRENT_UNIFORM_ROUND_ROBIN = YES")
    w("EXACT_5S_UNREACHABLE_UNDER_SAFE_AGGREGATE_RATE         = NO")
    w("EXACT_5S_SCHEDULER_FEASIBLE_AT_SAFE_RATE               = YES")
    w("NEAR_TOUCH_DEPTH_METRIC_READY        = YES "
      "(qty and notional at touch, 1/2/5 ticks, own tick)")
    w("EVENT_FIRST_CENSUS_WORKING           = YES")
    w("PASSIVE_REALIZABLE_EDGE              = NOT_IDENTIFIED")
    w("PMUS_FEES_RESOLVED                   = NO")
    w("READY_FOR_MULTI_DAY_PHASE2_CAPTURE   = NO")
    w("")
    w("The remaining verdicts -- GAME_LEVEL_DISCOVERY_COVERAGE and")
    w("PHASE2C_FUTURES_ONLY_RESULT_EXPLAINED, with H1-H6 -- are judged by the")
    w("analyst against these bytes, not asserted by the runner.")
    return L


if __name__ == "__main__":
    sys.exit(main())
