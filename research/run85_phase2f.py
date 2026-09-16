#!/usr/bin/env python3
"""RUN 85 PHASE 2F -- final pre-capture closure. READ ONLY. NO CREDENTIAL.

Closes only the remaining blockers to starting the multi-day game-level
observational dataset. No production, no orders, no credentials, no websocket.

--------------------------------------------------------------------------
MARKOUT SEMANTICS -- CORRECTED, AND THE CORRECTION IS THE POINT
--------------------------------------------------------------------------
Phase 2E said a markout "needs two distinct book states". That was WRONG and
is retracted. Conditioning the markout sample on the book having moved selects
on the outcome and biases every statistic computed from it.

Three concepts, kept apart everywhere below:

  A HORIZON_OBSERVATION_AVAILABLE  a trustworthy executable-state snapshot was
                                   observed at the target horizon
  B BOOK_CHANGED_BY_HORIZON        the book differs from t. A descriptive fact
                                   about the market, never a filter
  C MARKOUT                        the price change. If the book is unchanged
                                   and the snapshot is trustworthy, MARKOUT = 0
                                   -- that is DATA, not missingness

--------------------------------------------------------------------------
transactTime -- RESOLVED OFFLINE BEFORE THIS RUN, FROM SEALED EVIDENCE
--------------------------------------------------------------------------
Across 262 book reads in the sealed 2C, 2D and 2E archives, with zero
exceptions:

        marketData.transactTime == marketData.stats.lastPriceSample.ts

exactly, to the nanosecond. In the two 2C cases where transactTime ADVANCED
while the displayed ladder bytes were byte-identical, sharesTraded,
lastTradeSetTime and openInterest all stayed put -- so the field tracks
neither ladder mutations nor trades. And in 60 census markets there was never
a case of the ladder changing while transactTime stayed frozen.

  TRANSACTTIME_SEMANTICS = LAST_PRICE_SAMPLE_TIMESTAMP

It is the venue's periodic price MARK, on its own cadence. It is therefore
NOT a freshness signal: an old transactTime means no new price sample was
taken, not that the response is stale. Phase 2E's "serving a book stamped 22
minutes before the request" wrongly implied staleness and is corrected.

Freshness consequently needs a different instrument, which this run builds:
an INDEPENDENT route. /v1/markets/<slug>/bbo is served separately from
/v1/markets/<slug>/book. Read back to back, agreement on the touch is
cross-route corroboration; disagreement means one of them is behind.

--------------------------------------------------------------------------
RATE POLICY (section 6) -- AN OPERATING POINT, NOT A CLAIM
--------------------------------------------------------------------------
0.5 rps produced a 429 on two consecutive runs, so this run configures a more
conservative 0.4 rps nominal (2.5 s spacing). Retry-After is honoured exactly,
backoff widens on 429 and recovers only slowly on success. This says nothing
about where the venue's limit is; RPS_LIMIT_NOT_ESTABLISHED is carried
forward and no higher rate is tested.

--------------------------------------------------------------------------
HARD BOUND, DISCLOSED BEFORE RUNNING
--------------------------------------------------------------------------
MAX_VENUE_REQUESTS 200 · MAX_DISCOVERY_PAGES 32 · MAX_IDENTITY 20
MAX_FRESHNESS 40 · MAX_COHORT 14 · MAX_SCHEDULER 20

--------------------------------------------------------------------------
PREREGISTERED DIVERSITY STOP (section 3) -- replaces 2E's "40 events + 4
sports", which stopped one page before the richest part of the list.
--------------------------------------------------------------------------
Keep walking until the frame holds game-level events covering, among events
whose primaryTag is NON-NULL (a null tag is NOT a sport):
    >= 3 of {nfl, cfb, mlb, soccer-family, other-structured}
  AND >= 2 distinct tick sizes
  AND >= 2 distinct near-touch price bands
or the page bound. Unavailable categories are reported, never forced.
"""
from __future__ import annotations

import argparse
import calendar
import collections
import hashlib
import importlib.util
import json
import platform
import re
import statistics
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

_e = importlib.util.spec_from_file_location(
    "run85e", Path(__file__).with_name("run85_phase2e.py"))
E = importlib.util.module_from_spec(_e)
_e.loader.exec_module(E)

PHASE = "run85/phase2f/1"

NOMINAL_RPS = 0.4
SPACING_S = 1.0 / NOMINAL_RPS            # 2.5 s

MAX_VENUE_REQUESTS = 200
MAX_DISCOVERY_PAGES = 32
MAX_IDENTITY = 20
MAX_FRESHNESS = 40
MAX_COHORT = 14
MAX_SCHEDULER = 20

HORIZONS = (5.0, 10.0, 30.0, 60.0)
COHORT_CAP = 8

EVENTS_PATH = "/v1/events"
MARKET_PATH = "/v1/market/slug/%s"
BOOK_PATH = "/v1/markets/%s/book"
BBO_PATH = "/v1/markets/%s/bbo"

SOCCER_TAGS = E.SOCCER
SPORT_BUCKETS = ("nfl", "cfb", "mlb", "soccer", "other")


class Budget:
    def __init__(self, cap):
        self.cap, self.spent = cap, 0

    def take(self, n=1):
        if self.spent + n > self.cap:
            raise RuntimeError("BOUND_REACHED spent=%d cap=%d" % (self.spent, self.cap))
        self.spent += n


def say(s=""):
    print(s)


def paced(http, path, pacer, budget, params=None):
    budget.take()
    return B.get_paced(http, path, pacer, params)


def drain(pacer):
    """Section 5. Clear the pacer's outstanding spacing debt WITHOUT consuming
    a request slot, so a timed plan's t0 is not displaced.

    pacer.wait() sleeps until _last + spacing and then restamps _last. If t0 is
    taken straight after a previous section's request, the first scheduled read
    blocks for most of a spacing interval and the whole plan starts late -- the
    Phase 2E startup debt. Sleeping the slack here and leaving _last alone means
    the next wait() finds no slack and returns at once."""
    if pacer._last is None:
        return 0.0
    slack = (pacer._last + pacer.spacing) - time.monotonic()
    if slack > 0:
        time.sleep(slack)
        return slack
    return 0.0


def sport_bucket(tag):
    t = (tag or "").lower()
    if not t:
        return None                      # a null tag is NOT a sport
    if t == "nfl":
        return "nfl"
    if t == "cfb":
        return "cfb"
    if t == "mlb":
        return "mlb"
    if t in SOCCER_TAGS:
        return "soccer"
    return "other"


def ladder_hash(body):
    d = B.market_data(body) or {}
    return hashlib.sha256(json.dumps([d.get("bids"), d.get("offers")],
                                     sort_keys=True).encode()).hexdigest()


def touch_of(body):
    d = B.market_data(body) or {}
    bids, asks = B.ladder(d.get("bids")), B.ladder(d.get("offers"))
    bb = max((p for p, _, _ in bids), default=None)
    ba = min((p for p, _, _ in asks), default=None)
    return bb, ba


def price_band(mid):
    if mid is None:
        return None
    m = float(mid)
    return "P_LOW" if m < 0.2 else "P_MID" if m < 0.8 else "P_HIGH"


# --------------------------------------------------- 3. discovery + frame
def prove_pagination(http, pacer, budget, log, out):
    say("=== 2/3a. PAGINATION (offset only; page is CONTRADICTED) ===")
    base = {"active": "true", "closed": "false", "limit": 100}
    r, rows = paced(http, EVENTS_PATH, pacer, budget, base)
    for x in rows:
        x["stage"] = "pagination"
    log.extend(rows)
    p0 = E.page_stats(base, r.get("body"), 0)
    r2, rows2 = paced(http, EVENTS_PATH, pacer, budget, dict(base, offset=100))
    for x in rows2:
        x["stage"] = "pagination"
    log.extend(rows2)
    p1 = E.page_stats(dict(base, offset=100), r2.get("body"), 1)
    overlap = len(p0["_ids"] & p1["_ids"])
    adv = bool(p1["_ids"]) and overlap == 0
    say("page0 ids %s..%s set=%s"
        % (p0["event_id_min"], p0["event_id_max"], p0["event_id_set_sha256"][:16]))
    say("offset=100 ids %s..%s set=%s overlap=%d -> %s"
        % (p1["event_id_min"], p1["event_id_max"],
           p1["event_id_set_sha256"][:16], overlap,
           "ADVANCES" if adv else "DOES NOT ADVANCE"))
    out["pagination"] = {"advanced": adv, "overlap": overlap,
                         "page0_set": p0["event_id_set_sha256"],
                         "page1_set": p1["event_id_set_sha256"]}
    say("EVENT_PAGINATION_VERIFIED = %s" % ("YES" if adv else "NO"))
    say()
    return adv, [p0, p1]


def walk(http, pacer, budget, log, seed_pages, out):
    say("=== 3b. DIVERSITY-DRIVEN WALK ===")
    pages = list(seed_pages)
    events = {}
    for p in pages:
        for e in p["_events"]:
            events.setdefault(str(e.get("id")), e)

    def frame_state():
        buckets, ticks, bands = set(), set(), set()
        n = 0
        for eid, e in events.items():
            pt = e.get("primaryTag")
            tag = (pt or {}).get("slug") if isinstance(pt, dict) else None
            gm = [m for m in (e.get("markets") or [])
                  if E.classify(m) in ("GAME_BINARY", "GAME_THREE_WAY")]
            if not gm:
                continue
            n += 1
            b = sport_bucket(tag)
            if b:
                buckets.add(b)
            for m in gm:
                if m.get("orderPriceMinTickSize"):
                    ticks.add(str(m["orderPriceMinTickSize"]))
                bq, aq = m.get("bestBidQuote"), m.get("bestAskQuote")
                if bq and aq:
                    try:
                        mid = (Decimal(str(bq["value"])) + Decimal(str(aq["value"]))) / 2
                        bands.add(price_band(mid))
                    except Exception:                  # noqa: BLE001
                        pass
        return n, buckets, ticks, bands

    stop = "SEED_ONLY"
    for k in range(2, MAX_DISCOVERY_PAGES):
        n, buckets, ticks, bands = frame_state()
        if len(buckets) >= 3 and len(ticks) >= 2 and len(bands) >= 2:
            stop = "DIVERSITY_CRITERION_MET"
            break
        params = {"active": "true", "closed": "false", "limit": 100,
                  "offset": 100 * k}
        try:
            r, rows = paced(http, EVENTS_PATH, pacer, budget, params)
        except RuntimeError as exc:
            stop = str(exc)
            break
        for x in rows:
            x["stage"] = "walk"
        log.extend(rows)
        pg = E.page_stats(params, r.get("body"), k)
        pages.append(pg)
        before = len(events)
        for e in pg["_events"]:
            events.setdefault(str(e.get("id")), e)
        say("p%-2d offset=%-5d ev=%3d new=%3d ids %7s..%-7s game_ev=%d "
            "sports=%s ticks=%d bands=%d"
            % (k, params["offset"], pg["events_returned"], len(events) - before,
               pg["event_id_min"], pg["event_id_max"], n,
               sorted(buckets), len(ticks), len(bands)))
        if not pg["events_returned"]:
            stop = "EMPTY_PAGE"
            break
    else:
        stop = "PAGE_BOUND_REACHED"
    n, buckets, ticks, bands = frame_state()
    say()
    say("WALK_STOP_REASON = %s   pages=%d" % (stop, len(pages)))
    say("game events=%d  sport buckets=%s  ticks=%s  price bands=%s"
        % (n, sorted(buckets), sorted(ticks), sorted(x for x in bands if x)))
    for b in SPORT_BUCKETS:
        if b not in buckets:
            say("   UNAVAILABLE at this instant: sport bucket %r" % b)
    out["walk"] = {"stop": stop, "pages": len(pages), "game_events": n,
                   "sport_buckets": sorted(buckets), "ticks": sorted(ticks),
                   "price_bands": sorted(x for x in bands if x)}
    say()
    return events, stop


def build_frame(events, now_epoch, out):
    say("=== 3c. EVENT-LEVEL FRAME ===")
    frame, states, buckets = {}, collections.Counter(), collections.Counter()
    for eid, e in events.items():
        pt = e.get("primaryTag")
        tag = (pt or {}).get("slug") if isinstance(pt, dict) else None
        gm = [m for m in (e.get("markets") or [])
              if E.classify(m) in ("GAME_BINARY", "GAME_THREE_WAY")]
        if not gm:
            continue
        raw = e.get("period")
        norm, w1 = E.normalize_state(raw, tag)
        norm, w2 = E.corroborate(norm, e.get("startTime"), now_epoch)
        states[norm] += 1
        b = sport_bucket(tag)
        buckets[str(b)] += 1
        frame[eid] = {"event": e, "primary_tag": tag, "sport_bucket": b,
                      "raw_period": raw, "normalized_state": norm,
                      "state_justification": "%s; %s" % (w1, w2), "markets": gm}
    say("game events in frame = %d" % len(frame))
    say("sport buckets (None = venue supplies no primaryTag, NOT a sport): %s"
        % dict(buckets))
    say("normalized state    : %s" % dict(states))
    live = states.get("LIVE", 0)
    if not live:
        say("LIVE_COHORT_AVAILABLE = NO_AT_OBSERVATION_TIME")
    else:
        say("LIVE_COHORT_AVAILABLE = YES (%d live events)" % live)
    say()
    out["frame"] = {"game_events": len(frame),
                    "sport_buckets": {k: v for k, v in buckets.items()},
                    "states": dict(states), "live_events": live}
    return frame


# ------------------------------------------------------- 1. identity
def identity(http, pacer, budget, log, frame, out):
    say("=== 1. MARKET IDENTITY RE-VERIFICATION ===")
    picks = []
    for eid, g in sorted(frame.items(), key=lambda kv: int(kv[0])):
        m = sorted((m for m in g["markets"] if m.get("slug")),
                   key=lambda z: z["slug"])[0]
        picks.append((eid, m["slug"]))
        if len(picks) >= MAX_IDENTITY // 2:
            break
    rows, bodies = [], collections.defaultdict(set)
    for eid, slug in picks:
        for route, path in (("detail", MARKET_PATH % slug),
                            ("book", BOOK_PATH % slug)):
            try:
                r, lg = paced(http, path, pacer, budget)
            except RuntimeError as exc:
                say("  budget stop: %s" % exc)
                break
            for x in lg:
                x["stage"] = "identity_%s" % route
                x["requested_market_slug"] = slug
                x["requested_event_id"] = eid
            log.extend(lg)
            body = r.get("body") or {}
            if route == "detail":
                d = body.get("market") or body
                resp, rev = d.get("slug"), d.get("eventId")
            else:
                d = B.market_data(body) or {}
                resp, rev = d.get("marketSlug"), None
            v = ("RESPONSE_ID_NOT_IDENTIFIED" if resp is None else
                 "IDENTITY_MATCH" if resp == slug else "IDENTITY_MISMATCH")
            rows.append({"route": route, "requested_market_slug": slug,
                         "requested_event_id": eid,
                         "http_status": r.get("http_status"),
                         "response_market_slug": resp,
                         "response_event_id": rev,
                         "response_body_sha256": r.get("response_sha256"),
                         "verdict": v})
            bodies[(route, r.get("response_sha256"))].add(slug)
    by = collections.Counter(x["verdict"] for x in rows)
    coll = {k: v for k, v in bodies.items() if len(v) > 1}
    ev_seen = sum(1 for x in rows if x["response_event_id"] is not None)
    say("probes %d  %s  body collisions %d  responses carrying an event id %d"
        % (len(rows), dict(by), len(coll), ev_seen))
    ok = by.get("IDENTITY_MISMATCH", 0) == 0 and not coll and by.get("IDENTITY_MATCH", 0)
    say("DETAIL_ROUTE_IDENTITY_VERIFIED = %s (market slug)" % ("YES" if ok else "NO"))
    say("EVENT_ID_ROUTE_IDENTITY        = NOT_IDENTIFIED (venue returns none)")
    say()
    out["identity"] = {"by_verdict": dict(by), "collisions": len(coll),
                       "event_id_returned": ev_seen, "verified": bool(ok),
                       "rows": rows}
    return bool(ok)


# ----------------------------------------------------- 2. freshness probe
def freshness(http, pacer, budget, log, frame, out):
    """Section 2. transactTime semantics are already resolved offline; what is
    open is whether the PUBLIC BOOK is trustworthy enough for observational
    research. Tested with an independent route, not with transactTime age."""
    say("=== 2. FRESHNESS: BOOK vs INDEPENDENT BBO ROUTE ===")
    subjects = []
    for eid, g in sorted(frame.items(), key=lambda kv: int(kv[0])):
        m = sorted((m for m in g["markets"] if m.get("slug")),
                   key=lambda z: z["slug"])[0]
        subjects.append((eid, m["slug"], m.get("orderPriceMinTickSize")))
        if len(subjects) >= 4:
            break
    rows = []
    for rnd in range(MAX_FRESHNESS // (2 * max(1, len(subjects)))):
        for eid, slug, tick in subjects:
            try:
                rb, lb = paced(http, BOOK_PATH % slug, pacer, budget)
                rq, lq = paced(http, BBO_PATH % slug, pacer, budget)
            except RuntimeError as exc:
                say("  budget stop: %s" % exc)
                out["freshness"] = {"rows": rows, "truncated": str(exc)}
                return rows
            for x in lb:
                x["stage"] = "fresh_book"
                x["market_slug"] = slug
            for x in lq:
                x["stage"] = "fresh_bbo"
                x["market_slug"] = slug
            log.extend(lb)
            log.extend(lq)
            bd = B.market_data(rb.get("body")) or {}
            st = bd.get("stats") or {}
            lps = (st.get("lastPriceSample") or {}).get("ts")
            bb, ba = touch_of(rb.get("body"))
            q = (rq.get("body") or {})
            qq = q.get("marketBbo") or q.get("bbo") or q
            def amt(x):
                v, _ = B.amount(x)
                return v
            qbb, qba = amt(qq.get("bestBid")), amt(qq.get("bestAsk"))
            agree = (bb == qbb and ba == qba)
            rows.append({
                "round": rnd, "market_slug": slug, "native_event_id": eid,
                "book_http": rb.get("http_status"), "bbo_http": rq.get("http_status"),
                "local_request_utc": rb.get("local_request_wall_utc"),
                "local_response_utc": rb.get("local_response_wall_utc"),
                "transact_time": bd.get("transactTime"),
                "last_price_sample_ts": lps,
                "transact_equals_sample": bd.get("transactTime") == lps,
                "ladder_sha256": ladder_hash(rb.get("body")),
                "book_best_bid": str(bb) if bb is not None else None,
                "book_best_ask": str(ba) if ba is not None else None,
                "bbo_best_bid": str(qbb) if qbb is not None else None,
                "bbo_best_ask": str(qba) if qba is not None else None,
                "cross_route_touch_agrees": agree,
                "bbo_payload_keys": sorted(qq.keys())[:12],
                "shares_traded": st.get("sharesTraded"),
                "open_interest": st.get("openInterest"),
            })
    say("probes: %d (book+bbo pairs across %d markets)" % (len(rows), len(subjects)))
    ident = sum(1 for r in rows if r["transact_equals_sample"])
    say("transactTime == stats.lastPriceSample.ts : %d of %d" % (ident, len(rows)))
    agree = sum(1 for r in rows if r["cross_route_touch_agrees"])
    measurable = sum(1 for r in rows
                     if r["bbo_best_bid"] is not None or r["bbo_best_ask"] is not None)
    say("cross-route touch agreement             : %d of %d (%d had a readable bbo)"
        % (agree, len(rows), measurable))
    say()
    say("per-market sequences:")
    bym = collections.defaultdict(list)
    for r in rows:
        bym[r["market_slug"]].append(r)
    classes = collections.Counter()
    for m, v in bym.items():
        say("  %s" % m)
        prev = None
        for r in v:
            changed = prev is not None and r["ladder_sha256"] != prev
            if r["bbo_best_bid"] is None and r["bbo_best_ask"] is None:
                cls = "NOT_IDENTIFIED"
            elif not r["cross_route_touch_agrees"]:
                cls = "POTENTIALLY_STALE"
            elif changed:
                cls = "CHANGED_BOOK_VALID"
            else:
                cls = "UNCHANGED_BOOK_VALID"
            classes[cls] += 1
            say("     r%d book %s/%s  bbo %s/%s  ladder=%s tt=%s -> %s"
                % (r["round"], r["book_best_bid"], r["book_best_ask"],
                   r["bbo_best_bid"], r["bbo_best_ask"],
                   r["ladder_sha256"][:8],
                   (r["transact_time"] or "")[11:23], cls))
            prev = r["ladder_sha256"]
    say()
    say("observation classification: %s" % dict(classes))
    say()
    out["freshness"] = {"rows": rows, "classes": dict(classes),
                        "transact_equals_sample": ident,
                        "cross_route_agree": agree,
                        "bbo_measurable": measurable}
    return rows


# ----------------------------------------------------------- 7. cohort
def build_cohort(frame):
    say("=== 7. EVENT-STRATIFIED COHORT ===")
    strata = collections.defaultdict(list)
    for eid, g in frame.items():
        if g["normalized_state"] not in ("PREGAME", "LIVE"):
            continue
        strata[(str(g["sport_bucket"]), g["normalized_state"])].append(eid)
    for k in strata:
        strata[k].sort(key=int)
    say("strata: %s" % {" | ".join(k): len(v) for k, v in sorted(strata.items())})
    picks, pools = [], {k: list(v) for k, v in strata.items()}
    while any(pools.values()) and len(picks) < COHORT_CAP:
        for k in sorted(pools, key=lambda z: (-len(strata[z]), z)):
            if not pools[k] or len(picks) >= COHORT_CAP:
                continue
            eid = pools[k].pop(0)
            g = frame[eid]
            m = sorted((m for m in g["markets"] if m.get("slug")),
                       key=lambda z: z["slug"])[0]
            picks.append({"native_event_id": eid,
                          "native_event_slug": g["event"].get("slug"),
                          "market_slug": m["slug"],
                          "sport": g["primary_tag"],
                          "sport_bucket": g["sport_bucket"],
                          "league": sorted({t.get("league")
                                            for t in (g["event"].get("teams") or [])
                                            if isinstance(t, dict) and t.get("league")}),
                          "stratum": " | ".join(k),
                          "raw_period": g["raw_period"],
                          "normalized_state": g["normalized_state"],
                          "state_justification": g["state_justification"],
                          "scheduled_game_start": m.get("gameStartTime"),
                          "event_start_time": g["event"].get("startTime"),
                          "end_date": m.get("endDate"),
                          "tick_size": m.get("orderPriceMinTickSize"),
                          "fee_coefficient_field": m.get("feeCoefficient"),
                          "sports_market_type_v2": m.get("sportsMarketTypeV2")})
    say("cohort %d markets / %d events / %d sport buckets"
        % (len(picks), len({p["native_event_id"] for p in picks}),
           len({p["sport_bucket"] for p in picks})))
    say()
    return picks


def read_cohort(http, pacer, budget, log, cohort, out):
    say("=== 7b. COHORT NEAR-TOUCH STATE ===")
    rows = []
    for c in cohort[:MAX_COHORT]:
        try:
            r, lg = paced(http, BOOK_PATH % c["market_slug"], pacer, budget)
        except RuntimeError as exc:
            say("  budget stop: %s" % exc)
            break
        for x in lg:
            x["stage"] = "cohort"
            x["market_slug"] = c["market_slug"]
        log.extend(lg)
        nt = E.near_touch(r.get("body"), c["tick_size"])
        cls, ratio = E.queue_class(nt)
        bb, ba = touch_of(r.get("body"))
        mid = ((bb + ba) / 2) if (bb is not None and ba is not None) else None
        d = B.market_data(r.get("body")) or {}
        rows.append(dict(c, near_touch=nt, queue_class=cls,
                         queue_ratio=str(ratio) if ratio is not None else None,
                         best_bid=str(bb) if bb is not None else None,
                         best_ask=str(ba) if ba is not None else None,
                         mid=str(mid) if mid is not None else None,
                         price_band=price_band(mid),
                         spread=str(ba - bb) if (bb is not None and ba is not None)
                         else None,
                         venue_transact_time=d.get("transactTime"),
                         local_observation_utc=r.get("local_request_wall_utc"),
                         ladder_sha256=ladder_hash(r.get("body"))))
    say("%-42s %-6s %-8s %-7s %-7s %-8s %-10s"
        % ("market", "sport", "state", "mid", "tick", "band", "queue"))
    for x in rows:
        say("%-42s %-6s %-8s %-7s %-7s %-8s %-10s"
            % (x["market_slug"][:42], str(x["sport"]), x["normalized_state"],
               x["mid"], x["tick_size"], x["price_band"], x["queue_class"]))
    say()
    for x in rows:
        nt = x["near_touch"]
        if not nt:
            continue
        say("  %s" % x["market_slug"])
        say("     touch  bid %10s  ask %10s   spread %s"
            % (nt["touch_bid_qty"], nt["touch_ask_qty"], x["spread"]))
        say("     qty within 1t  %10s / %-10s   2t %10s / %-10s   5t %10s / %s"
            % (nt["cum_bid_qty_1t"], nt["cum_ask_qty_1t"],
               nt["cum_bid_qty_2t"], nt["cum_ask_qty_2t"],
               nt["cum_bid_qty_5t"], nt["cum_ask_qty_5t"]))
    say()
    out["cohort"] = rows
    return rows


# -------------------------------------------- 5. drained-pacer scheduler
def scheduler(http, pacer, budget, log, cohort, out):
    say("=== 5. DRAINED-PACER EXACT-HORIZON SCHEDULER ===")
    subjects = [c for c in cohort if c.get("tick_size")][:2]
    if not subjects:
        say("no subject; SKIPPED")
        out["scheduler"] = {"ran": False}
        return []
    starts = [0.0, 2.5][:len(subjects)]
    plan = sorted((s + h, sub["market_slug"], h)
                  for s, sub in zip(starts, subjects)
                  for h in (0.0,) + HORIZONS)
    gaps = [plan[i + 1][0] - plan[i][0] for i in range(len(plan) - 1)]
    span = plan[-1][0] - plan[0][0]
    rate = len(plan) / span if span else float("inf")
    say("plan %d reads, span %.1fs, min gap %.2fs, mean %.3f rps (nominal %.2f)"
        % (len(plan), span, min(gaps), rate, NOMINAL_RPS))
    if min(gaps) < SPACING_S - 1e-9 or rate > NOMINAL_RPS + 1e-9 \
            or len(plan) > MAX_SCHEDULER:
        say("PLAN REJECTED -- breaches spacing, rate or disclosed bound. NOT RUN.")
        out["scheduler"] = {"ran": False, "reason": "plan breached the rate rule"}
        return []

    drained = drain(pacer)
    probe = time.monotonic()
    pacer.wait()
    debt_after_drain = time.monotonic() - probe
    pacer._last = None                    # the probe wait consumed no request
    say("drain slept %.3fs; residual debt measured after drain = %.4fs"
        % (drained, debt_after_drain))
    ok_drain = debt_after_drain < 0.05
    say("DRAINED_PACER_VERIFIED = %s" % ("YES" if ok_drain else "NO"))
    say()

    t0 = time.monotonic()
    obs, throttled = [], False
    for target, slug, h in plan:
        w = (t0 + target) - time.monotonic()
        if w > 0:
            time.sleep(w)
        before = len(pacer.events)
        try:
            r, lg = paced(http, BOOK_PATH % slug, pacer, budget)
        except RuntimeError as exc:
            say("  budget stop: %s" % exc)
            break
        if len(pacer.events) > before:
            throttled = True
        for x in lg:
            x["stage"] = "scheduler"
            x["market_slug"] = slug
            x["horizon_s"] = h
        log.extend(lg)
        d = B.market_data(r.get("body")) or {}
        obs.append({"market_slug": slug, "horizon_s": h,
                    "target_offset_s": target,
                    "actual_offset_s": time.monotonic() - t0,
                    "http": r.get("http_status"),
                    "valid_snapshot": r.get("http_status") == 200 and bool(d),
                    "ladder_sha256": ladder_hash(r.get("body")),
                    "transact_time": d.get("transactTime")})

    base = {o["market_slug"]: o for o in obs if o["horizon_s"] == 0.0}
    say("%-8s %5s %11s %10s %10s %10s  %s"
        % ("horizon", "n", "median", "p90", "p95", "max", "valid/observed"))
    stats, changed = {}, {}
    for h in HORIZONS:
        errs, valid, nchg, tot = [], 0, 0, 0
        for o in obs:
            if o["horizon_s"] != h or o["market_slug"] not in base:
                continue
            b = base[o["market_slug"]]
            errs.append(abs((o["actual_offset_s"] - b["actual_offset_s"]) - h))
            tot += 1
            if o["valid_snapshot"]:
                valid += 1
            if o["ladder_sha256"] != b["ladder_sha256"]:
                nchg += 1
        if not errs:
            stats[str(h)] = None
            continue
        errs.sort()

        def pct(p):
            return errs[min(len(errs) - 1,
                            max(0, int(round(p / 100.0 * (len(errs) - 1)))))]
        stats[str(h)] = {"n": len(errs),
                         "median_ms": 1000 * statistics.median(errs),
                         "p90_ms": 1000 * pct(90), "p95_ms": 1000 * pct(95),
                         "max_ms": 1000 * errs[-1],
                         "valid_snapshots": valid, "observations": tot}
        changed[str(h)] = (nchg, tot)
        say("%-8.0f %5d %9.1fms %8.1fms %8.1fms %8.1fms  %d/%d"
            % (h, len(errs), stats[str(h)]["median_ms"], stats[str(h)]["p90_ms"],
               stats[str(h)]["p95_ms"], stats[str(h)]["max_ms"], valid, tot))
    say()
    say("BOOK_CHANGED_BY_HORIZON -- descriptive only, NEVER a filter:")
    for h in HORIZONS:
        c = changed.get(str(h))
        if c:
            say("   %2ds  book differed from t0 in %d of %d observations"
                % (int(h), c[0], c[1]))
    say()
    say("  A markout at a horizon whose book is unchanged is MARKOUT = 0, not")
    say("  a missing observation. The rate above is reported because it")
    say("  describes the market, and it is not used to select the sample.")
    if throttled:
        say()
        say("  A THROTTLE FIRED DURING THE TEST -- lag figures are contaminated.")
    say()
    out["scheduler"] = {"ran": True, "drained_slept_s": drained,
                        "residual_debt_s": debt_after_drain,
                        "drained_verified": ok_drain,
                        "plan_reads": len(plan), "span_s": span,
                        "mean_rps": rate, "throttled": throttled,
                        "lag_error": stats, "book_changed": changed,
                        "observations": obs}
    return obs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    budget = Budget(MAX_VENUE_REQUESTS)
    meta = {"phase": PHASE, "python": sys.version, "platform": platform.platform(),
            "httpx": httpx.__version__, "gateway": C.GATEWAY_BASE,
            "nominal_rps": NOMINAL_RPS, "spacing_s": SPACING_S,
            "max_venue_requests": MAX_VENUE_REQUESTS,
            "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "phase2e_sha256": hashlib.sha256(Path(E.__file__).read_bytes()).hexdigest(),
            "phase2b_sha256": hashlib.sha256(Path(B.__file__).read_bytes()).hexdigest()}
    (out / "runner_metadata.json").write_text(json.dumps(meta, indent=1))
    say(json.dumps(meta, indent=1))
    say()

    res, log = {}, []
    pacer = B.AdaptivePacer(base=SPACING_S)
    cohort, ident_ok, sched = [], False, []
    t_start = time.monotonic()
    with httpx.Client(timeout=30.0) as http:
        try:
            adv, seeds = prove_pagination(http, pacer, budget, log, res)
            events, stop = walk(http, pacer, budget, log, seeds, res)
            frame = build_frame(events, time.time(), res)
            ident_ok = identity(http, pacer, budget, log, frame, res)
            freshness(http, pacer, budget, log, frame, res)
            if ident_ok:
                picks = build_cohort(frame)
                cohort = read_cohort(http, pacer, budget, log, picks, res)
                sched = scheduler(http, pacer, budget, log, cohort, res)
            else:
                say("IDENTITY NOT VERIFIED -- cohort and scheduler SKIPPED.")
        except RuntimeError as exc:
            say("HARD BOUND: %s" % exc)
            res["aborted"] = str(exc)

    elapsed = time.monotonic() - t_start
    res["venue_requests_spent"] = budget.spent
    res["elapsed_s"] = elapsed
    res["achieved_rps"] = budget.spent / elapsed if elapsed else None
    res["throttle_events"] = pacer.events
    (out / "closure.json").write_text(json.dumps(res, indent=1, default=str))
    with (out / "request_log.jsonl").open("w") as fh:
        for r in log:
            fh.write(json.dumps(r, default=str) + "\n")

    B.seal(out, verdicts(res, ident_ok, cohort))
    ok, bad = B.verify(out)
    say()
    say("SEAL_VERIFIED = %s%s" % (ok, "" if ok else " BAD=%s" % bad))
    return 0


def verdicts(res, ident_ok, cohort):
    L = []

    def w(s=""):
        L.append(s)
        say(s)

    sc = res.get("scheduler") or {}
    fr = res.get("freshness") or {}
    thr = res.get("throttle_events") or []
    n429 = sum(1 for e in thr if e.get("event") == "429")
    five = (sc.get("lag_error") or {}).get("5.0")
    chg5 = (sc.get("book_changed") or {}).get("5.0")

    w("=== 11. PHASE 2F VERDICTS ===")
    w("DETAIL_ROUTE_IDENTITY_VERIFIED = %s (market slug)"
      % ("YES" if ident_ok else "NO"))
    w("EVENT_ID_ROUTE_IDENTITY        = NOT_IDENTIFIED")
    w("EVENT_PAGINATION_VERIFIED      = %s"
      % ("YES" if (res.get("pagination") or {}).get("advanced") else "NO"))
    w("TRANSACTTIME_SEMANTICS         = LAST_PRICE_SAMPLE_TIMESTAMP")
    w("   transactTime == stats.lastPriceSample.ts in %d of %d probes this run"
      % (fr.get("transact_equals_sample", 0), len(fr.get("rows") or [])))
    w("DRAINED_PACER_VERIFIED         = %s"
      % ("YES" if sc.get("drained_verified") else "NO"))
    if five:
        w("EXACT_5S_REQUEST_PLACEMENT_VERIFIED = %s"
          % ("YES" if five["max_ms"] < 1000 else "NO"))
        w("   5 s lag error median %.1f ms, p90 %.1f ms, p95 %.1f ms, max %.1f ms"
          % (five["median_ms"], five["p90_ms"], five["p95_ms"], five["max_ms"]))
        w("EXACT_5S_VALID_STATE_OBSERVATION_VERIFIED = %s (%d/%d valid snapshots)"
          % ("YES" if five["valid_snapshots"] == five["observations"] else "NO",
             five["valid_snapshots"], five["observations"]))
    else:
        w("EXACT_5S_REQUEST_PLACEMENT_VERIFIED       = NOT_IDENTIFIED")
        w("EXACT_5S_VALID_STATE_OBSERVATION_VERIFIED = NOT_IDENTIFIED")
    if chg5:
        w("BOOK_CHANGED_WITHIN_5S_RATE    = %d / %d  (descriptive, not a filter)"
          % (chg5[0], chg5[1]))
    w("COHORT_MARKETS                 = %d from %d events, %d sport buckets"
      % (len(cohort), len({c['native_event_id'] for c in cohort}),
         len({c['sport_bucket'] for c in cohort})))
    w("HTTP_429_COUNT                 = %d" % n429)
    for e in thr:
        w("   %s" % e)
    w("NOMINAL_RPS                    = %.2f" % NOMINAL_RPS)
    w("ACHIEVED_RPS                   = %.4f over %.1f s"
      % (res.get("achieved_rps") or 0, res.get("elapsed_s") or 0))
    w("RPS_LIMIT_NOT_ESTABLISHED      = CARRIED FORWARD")
    w("PASSIVE_REALIZABLE_EDGE        = NOT_IDENTIFIED")
    w("PMUS_FEES_RESOLVED             = NO")
    w("VENUE_REQUESTS_SPENT           = %s of %d"
      % (res.get("venue_requests_spent"), MAX_VENUE_REQUESTS))
    w("")
    w("GAME_LEVEL_COHORT_DIVERSITY, PUBLIC_BOOK_FRESHNESS_SUFFICIENT_FOR_")
    w("OBSERVATIONAL_RESEARCH and READY_FOR_MULTI_DAY_PHASE2_CAPTURE are judged")
    w("by the analyst against these bytes, not asserted by the runner.")
    return L


if __name__ == "__main__":
    sys.exit(main())
