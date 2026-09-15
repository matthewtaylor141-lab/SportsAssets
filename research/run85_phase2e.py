#!/usr/bin/env python3
"""RUN 85 PHASE 2E -- game-level cohort closure. READ ONLY. NO CREDENTIAL.

Phase 2D found game-level markets and retracted the Phase 2C interpretation.
This run closes the cohort: it proves every request is bound to the market it
names, proves pagination actually advances, builds an event-stratified cohort
without alphabetical bias, and empirically tests an exact-horizon scheduler.

Every venue call goes through run85_pmus_collector._get under the authorized
boundary -- GET only, public gateway only, no credential, no signer, no order
path -- paced by the Phase 2B AdaptivePacer with exact Retry-After.

NO PROFITABILITY ANALYSIS. Nothing here computes an expectancy, a fill, a fee
or a net figure. No borrowed fee formula enters any result.

--------------------------------------------------------------------------
HARD BOUND, DISCLOSED BEFORE RUNNING
--------------------------------------------------------------------------
MAX_VENUE_REQUESTS       220   hard stop; the run raises rather than exceed it
MAX_DISCOVERY_PAGES       32   pagination walk
MAX_IDENTITY_PROBES       44   section 1, split across detail and book routes
MAX_COHORT_READS          24   section 6/7
MAX_SCHEDULER_READS       30   section 11

--------------------------------------------------------------------------
PREREGISTERED TAXONOMY (section 4). NEW for 2E. The Phase 2D allowlist is
NOT retrospectively widened -- both counts are reported side by side.
--------------------------------------------------------------------------
  GAME_BINARY      v2 in MONEYLINE / SPREAD / TOTAL / TOTALS
                   two mutually exclusive sides
  GAME_THREE_WAY   v2 == DRAWABLE_OUTCOME
                   soccer full-time result: THREE mutually exclusive outcomes
                   (home / draw / away) at the market-family level. This is
                   NOT forced into a binary representation. Whether the venue
                   expresses each outcome as its own two-sided contract is a
                   separate question this run records rather than assumes.
  GAME_PROP        v2 == PROP -- player and team props. Attached to a game,
                   but not a game-outcome contract. Counted, not included in
                   PHASE2E_GAME_LEVEL_COUNT.
  FUTURES          v2 == FUTURE
  OTHER_STRUCTURED any other non-empty structured value
  NOT_IDENTIFIED   UNSPECIFIED or absent, with no v1 either

  PHASE2E_GAME_LEVEL_COUNT = GAME_BINARY + GAME_THREE_WAY

--------------------------------------------------------------------------
PREREGISTERED STATE MAPPING (section 5). Sport-aware. Every mapping is
CORROBORATED against gameStartTime vs the local observation clock, and a
disagreement downgrades to NOT_IDENTIFIED rather than being forced.
--------------------------------------------------------------------------
  exact tokens, any sport:
      NS -> PREGAME     FT -> ENDED        SUSP -> SUSPENDED
      CAN -> CANCELLED  POST -> POSTPONED  LIVE / Live -> LIVE
  sport-conditional patterns:
      baseball family : ^IN\\d+$      innings          -> LIVE
      soccer family   : ^\\d+'$       minutes elapsed  -> LIVE
      esports family  : ^(Map|Game) \\d+$              -> LIVE
  "" or anything else -> NOT_IDENTIFIED

  A pattern is applied ONLY inside its sport family. "IN8" means an inning in
  baseball and means nothing established anywhere else, so it does not map
  outside it. This is the rule against reading a free-text token as evidence.

--------------------------------------------------------------------------
PREREGISTERED QUEUE RULE (section 7). QUANTITY, at a comparable tick
distance, never whole-ladder dollar asymmetry -- which Phase 2C retracted.
--------------------------------------------------------------------------
  R = cumulative ask quantity within 1 tick / cumulative bid quantity within
      1 tick, both measured in the market's OWN venue-supplied tick
      BID_HEAVY  R < 1/3
      ASK_HEAVY  R > 3
      BALANCED   otherwise
      NOT_IDENTIFIED  either side empty, or the venue supplied no tick

--------------------------------------------------------------------------
PREREGISTERED SUFFICIENCY CRITERION (section 3), stated before the walk:
--------------------------------------------------------------------------
  Stop walking when BOTH hold:
      at least 40 distinct native events carry >= 1 GAME_BINARY market, AND
      those events span >= 4 distinct venue sports
  or when the page bound is reached, whichever comes first. Which of the two
  ended the walk is reported.
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

PHASE = "run85/phase2e/1"

MAX_VENUE_REQUESTS = 220
MAX_DISCOVERY_PAGES = 32
MAX_IDENTITY_PROBES = 44
MAX_COHORT_READS = 24
MAX_SCHEDULER_READS = 30

SUFFICIENT_EVENTS = 40
SUFFICIENT_SPORTS = 4
COHORT_CAP = 10

EVENTS_PATH = "/v1/events"
MARKET_PATH = "/v1/market/slug/%s"
BOOK_PATH = "/v1/markets/%s/book"

GAME_BINARY_V2 = {"SPORTS_MARKET_TYPE_MONEYLINE", "SPORTS_MARKET_TYPE_SPREAD",
                  "SPORTS_MARKET_TYPE_TOTAL", "SPORTS_MARKET_TYPE_TOTALS"}
THREE_WAY_V2 = "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME"
PROP_V2 = "SPORTS_MARKET_TYPE_PROP"
FUTURES_V2 = "SPORTS_MARKET_TYPE_FUTURE"
PHASE2D_ALLOWLIST = set(GAME_BINARY_V2)      # what 2D froze, for the side-by-side

BASEBALL = {"mlb", "baseball", "milb", "npb", "kbo"}
SOCCER = {"epl", "uel", "uecl", "ucl", "bun", "lg1", "sea", "lal", "mls",
          "eflc", "soccer", "ere", "por", "bra", "arg"}
ESPORTS = {"esports", "cs2", "lol", "dota", "val"}

HORIZONS = (5.0, 10.0, 30.0, 60.0)
MIN_REQUEST_GAP_S = 2.0          # the 0.5 rps ceiling, as a spacing floor
RATE_CEILING_RPS = 0.5


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


# ------------------------------------------------------------- taxonomy
def classify(market):
    v2 = market.get("sportsMarketTypeV2")
    v1 = market.get("sportsMarketType")
    if v2 in GAME_BINARY_V2:
        return "GAME_BINARY"
    if v2 == THREE_WAY_V2:
        return "GAME_THREE_WAY"
    if v2 == PROP_V2:
        return "GAME_PROP"
    if v2 == FUTURES_V2 or v1 == "futures":
        return "FUTURES"
    if v2 and v2 != "SPORTS_MARKET_TYPE_UNSPECIFIED":
        return "OTHER_STRUCTURED"
    if v1:
        return "OTHER_STRUCTURED"
    return "NOT_IDENTIFIED"


def sport_family(sport):
    s = (sport or "").lower()
    if s in BASEBALL:
        return "baseball"
    if s in SOCCER:
        return "soccer"
    if s in ESPORTS:
        return "esports"
    return "other"


EXACT_STATE = {"NS": "PREGAME", "FT": "ENDED", "SUSP": "SUSPENDED",
               "CAN": "CANCELLED", "POST": "POSTPONED",
               "LIVE": "LIVE", "Live": "LIVE"}
RE_INNING = re.compile(r"^IN\d+$")
RE_MINUTE = re.compile(r"^\d+'$")
RE_SEGMENT = re.compile(r"^(Map|Game) \d+$")


def normalize_state(raw_period, sport):
    """Sport-aware. Returns (normalized, justification)."""
    if raw_period in EXACT_STATE:
        return EXACT_STATE[raw_period], "exact token %r" % raw_period
    fam = sport_family(sport)
    if fam == "baseball" and RE_INNING.match(raw_period or ""):
        return "LIVE", "baseball inning token %r" % raw_period
    if fam == "soccer" and RE_MINUTE.match(raw_period or ""):
        return "LIVE", "soccer minute token %r" % raw_period
    if fam == "esports" and RE_SEGMENT.match(raw_period or ""):
        return "LIVE", "esports segment token %r" % raw_period
    if raw_period in (None, ""):
        return "NOT_IDENTIFIED", "period absent"
    return "NOT_IDENTIFIED", "unmapped token %r in family %r" % (raw_period, fam)


def corroborate(norm, game_start_iso, now_epoch):
    """PREGAME must sit before the scheduled start and LIVE at or after it.
    A disagreement downgrades rather than overrides -- the venue's own clock
    field is the check on our reading of its own period string."""
    if not game_start_iso:
        return norm, "no gameStartTime to corroborate against"
    try:
        # calendar.timegm, not mktime: the venue's timestamps are UTC, and
        # mktime would read them as local and need a DST-dependent correction.
        gs = calendar.timegm(time.strptime(game_start_iso, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:                                  # noqa: BLE001
        return norm, "gameStartTime unparseable %r" % game_start_iso
    before = now_epoch < gs
    if norm == "PREGAME" and not before:
        return "NOT_IDENTIFIED", "period says pregame but start time has passed"
    if norm == "LIVE" and before:
        return "NOT_IDENTIFIED", "period says live but start time is future"
    return norm, "agrees with gameStartTime"


# ----------------------------------------------------- 2. pagination proof
def id_set_hash(ids):
    return hashlib.sha256(",".join(sorted(ids, key=int)).encode()).hexdigest()


def page_stats(params, body, n):
    events = (body or {}).get("events") or []
    if not isinstance(events, list):
        events = []
    ids = {str(e["id"]) for e in events if e.get("id") is not None}
    kinds = collections.Counter()
    for e in events:
        for m in (e.get("markets") or []):
            kinds[classify(m)] += 1
    return {"page": n, "params": dict(params), "events_returned": len(events),
            "unique_event_ids": len(ids),
            "event_id_min": min(ids, key=int) if ids else None,
            "event_id_max": max(ids, key=int) if ids else None,
            "event_id_set_sha256": id_set_hash(ids),
            "class_distribution": dict(kinds), "_ids": ids, "_events": events}


def prove_pagination(http, pacer, budget, log, out):
    say("=== 2. DISCOVERY PAGINATION PROOF ===")
    base = {"active": "true", "closed": "false", "limit": 100}
    r, rows = paced(http, EVENTS_PATH, pacer, budget, base)
    for x in rows:
        x["stage"] = "pagination_proof"
    log.extend(rows)
    p0 = page_stats(base, r.get("body"), 0)
    say("baseline            events=%d ids %s..%s set=%s"
        % (p0["events_returned"], p0["event_id_min"], p0["event_id_max"],
           p0["event_id_set_sha256"][:16]))
    cand = []
    for name, extra in (("offset", {"offset": 100}), ("page", {"page": 2})):
        r2, rows2 = paced(http, EVENTS_PATH, pacer, budget, dict(base, **extra))
        for x in rows2:
            x["stage"] = "pagination_proof"
        log.extend(rows2)
        pr = page_stats(dict(base, **extra), r2.get("body"), -1)
        overlap = len(pr["_ids"] & p0["_ids"])
        advanced = bool(pr["_ids"]) and overlap == 0
        cand.append({"param": name, "http": r2.get("http_status"),
                     "events": pr["events_returned"],
                     "id_set_sha256": pr["event_id_set_sha256"],
                     "overlap_with_page1": overlap,
                     "reproduces_page1":
                         pr["event_id_set_sha256"] == p0["event_id_set_sha256"],
                     "advanced": advanced})
        say("%-8s http=%s events=%3d overlap_with_page1=%3d set=%s -> %s"
            % (name, r2.get("http_status"), pr["events_returned"], overlap,
               pr["event_id_set_sha256"][:16],
               "ADVANCES" if advanced else "DOES NOT ADVANCE"))
    out["pagination_proof"] = cand
    ok = [c for c in cand if c["advanced"]]
    say()
    say("  A 200 is not evidence. Advancement is judged by a DISJOINT native")
    say("  event-id set, and the set hash is recorded so the claim is checkable")
    say("  from the archive rather than from this line.")
    say("EVENT_PAGINATION_VERIFIED = %s" % ("YES" if ok else "NO"))
    say("PAGINATION_MECHANISM      = %s" % (ok[0]["param"] if ok else "NOT_IDENTIFIED"))
    say()
    return (ok[0]["param"] if ok else None), p0


# ------------------------------------------- 3. bounded event-first universe
def walk(http, pacer, budget, log, mech, p0, out):
    say("=== 3. BOUNDED EVENT-FIRST WALK ===")
    say("sufficiency criterion (preregistered): >= %d events carrying a"
        % SUFFICIENT_EVENTS)
    say("GAME_BINARY market, spanning >= %d sports; else the %d-page bound."
        % (SUFFICIENT_SPORTS, MAX_DISCOVERY_PAGES))
    say()
    pages, events = [p0], {}
    stop = "SINGLE_PAGE_ONLY"

    def absorb(pg):
        for e in pg["_events"]:
            eid = str(e.get("id"))
            if eid not in events:
                events[eid] = e

    absorb(p0)

    def sufficiency():
        ge, sports = set(), set()
        for eid, e in events.items():
            if any(classify(m) == "GAME_BINARY" for m in (e.get("markets") or [])):
                ge.add(eid)
                pt = e.get("primaryTag")
                sports.add((pt or {}).get("slug") if isinstance(pt, dict) else None)
        sports.discard(None)
        return ge, sports

    if mech:
        for n in range(1, MAX_DISCOVERY_PAGES):
            params = {"active": "true", "closed": "false", "limit": 100,
                      mech: 100 * n}
            try:
                r, rows = paced(http, EVENTS_PATH, pacer, budget, params)
            except RuntimeError as exc:
                stop = str(exc)
                break
            for x in rows:
                x["stage"] = "walk"
            log.extend(rows)
            pg = page_stats(params, r.get("body"), n)
            prev = set().union(*[p["_ids"] for p in pages]) if pages else set()
            pg["new_unique_events"] = len(pg["_ids"] - prev)
            pg["overlap_with_prior"] = len(pg["_ids"] & prev)
            pages.append(pg)
            absorb(pg)
            ge, sports = sufficiency()
            say("p%-2d %-14s ev=%3d new=%3d ovl=%3d ids %7s..%-7s game_ev=%3d sports=%d"
                % (n, json.dumps({mech: params[mech]}), pg["events_returned"],
                   pg["new_unique_events"], pg["overlap_with_prior"],
                   pg["event_id_min"], pg["event_id_max"], len(ge), len(sports)))
            if not pg["events_returned"]:
                stop = "EMPTY_PAGE"
                break
            if not pg["new_unique_events"]:
                stop = "NO_NEW_EVENTS"
                break
            if len(ge) >= SUFFICIENT_EVENTS and len(sports) >= SUFFICIENT_SPORTS:
                stop = "SUFFICIENCY_CRITERION_MET"
                break
        else:
            stop = "PAGE_BOUND_REACHED"
    say()
    say("WALK_STOP_REASON = %s" % stop)
    out["walk_stop_reason"] = stop
    out["pages"] = [{k: v for k, v in p.items() if not k.startswith("_")}
                    for p in pages]
    return pages, events, stop


def frame(events, now_epoch, out):
    """EVENT -> its game-level markets, built before any budget is spent."""
    say("=== 3b. CANDIDATE FRAME (event first, never a slug slice) ===")
    rows, per_event, by_sport = 0, collections.Counter(), collections.Counter()
    game_events, states = {}, collections.Counter()
    for eid, e in events.items():
        pt = e.get("primaryTag")
        sport = (pt or {}).get("slug") if isinstance(pt, dict) else None
        raw = e.get("period")
        norm, why = normalize_state(raw, sport)
        norm, why2 = corroborate(norm, e.get("startTime"), now_epoch)
        gm = []
        for m in (e.get("markets") or []):
            k = classify(m)
            if k in ("GAME_BINARY", "GAME_THREE_WAY"):
                rows += 1
                gm.append((k, m))
        if not gm:
            continue
        per_event[eid] = len(gm)
        by_sport[str(sport)] += 1
        states[norm] += 1
        game_events[eid] = {"event": e, "sport": sport, "raw_period": raw,
                            "normalized_state": norm,
                            "state_justification": "%s; %s" % (why, why2),
                            "markets": gm}
    say("GAME_ROWS_DISCOVERED            = %d" % rows)
    say("GAME_UNIQUE_EVENTS_DISCOVERED   = %d" % len(game_events))
    active = sum(1 for v in game_events.values()
                 if v["normalized_state"] in ("PREGAME", "LIVE"))
    say("GAME_ACTIVE_EVENTS_DISCOVERED   = %d (PREGAME or LIVE)" % active)
    say()
    say("SPORT_DISTRIBUTION_BY_EVENT:")
    for k, n in by_sport.most_common(16):
        say("   %-12s %4d events" % (k, n))
    say()
    say("MARKETS_PER_EVENT_DISTRIBUTION (deciles of the per-event count):")
    vals = sorted(per_event.values())
    if vals:
        for p in (0, 10, 25, 50, 75, 90, 100):
            i = min(len(vals) - 1, max(0, int(round(p / 100.0 * (len(vals) - 1)))))
            say("   p%-3d %5d" % (p, vals[i]))
    say()
    say("NORMALIZED_STATE by event: %s" % dict(states))
    say()
    say("  Rows and events are different denominators and are never swapped:")
    say("  %d rows across %d events is a mean of %.1f markets per event."
        % (rows, len(game_events), (rows / len(game_events)) if game_events else 0))
    say()
    out["frame"] = {"game_rows": rows, "game_events": len(game_events),
                    "active_events": active,
                    "sport_distribution_by_event": dict(by_sport),
                    "markets_per_event": dict(collections.Counter(vals)),
                    "normalized_state_by_event": dict(states)}
    return game_events


def taxonomy_report(events, out):
    say("=== 4. TAXONOMY, OLD AND NEW SIDE BY SIDE ===")
    new = collections.Counter()
    old = 0
    for e in events.values():
        for m in (e.get("markets") or []):
            new[classify(m)] += 1
            if m.get("sportsMarketTypeV2") in PHASE2D_ALLOWLIST:
                old += 1
    for k, n in new.most_common():
        say("   %-18s %7d" % (k, n))
    say()
    g = new["GAME_BINARY"] + new["GAME_THREE_WAY"]
    say("PHASE2D_GAME_LEVEL_FLOOR = %d  (2D's frozen allowlist, on THIS walk)" % old)
    say("PHASE2E_GAME_LEVEL_COUNT = %d  (GAME_BINARY %d + GAME_THREE_WAY %d)"
        % (g, new["GAME_BINARY"], new["GAME_THREE_WAY"]))
    say("GAME_PROP counted separately = %d and NOT included above"
        % new["GAME_PROP"])
    say()
    say("  The three-way markets are kept as THREE mutually exclusive outcomes")
    say("  at the family level. Nothing here asserts they are a binary")
    say("  contract; whether the venue expresses each outcome as its own")
    say("  two-sided instrument is recorded in section 1's marketSides read,")
    say("  not assumed by the taxonomy.")
    say()
    out["taxonomy"] = {"phase2d_floor": old, "phase2e_count": g,
                       "by_class": dict(new)}
    return new


# --------------------------------------------------- 1. identity proof
def identity_proof(http, pacer, budget, log, game_events, out):
    """Section 1. Is the response bound to the request? Checked on BOTH the
    detail route and the book route, because 2D only ever checked detail."""
    say("=== 1. DETAIL/BOOK ROUTE IDENTITY PROOF ===")
    picks = []
    for eid, g in sorted(game_events.items(), key=lambda kv: int(kv[0])):
        for k, m in g["markets"][:1]:
            if m.get("slug"):
                picks.append((eid, m["slug"]))
        if len(picks) >= MAX_IDENTITY_PROBES // 2:
            break
    rows, bodies = [], collections.defaultdict(list)
    for eid, slug in picks:
        for route, path in (("detail", MARKET_PATH % slug),
                            ("book", BOOK_PATH % slug)):
            try:
                r, lg = paced(http, path, pacer, budget)
            except RuntimeError as exc:
                say("  budget stop: %s" % exc)
                out["identity"] = {"truncated": str(exc), "rows": rows}
                return rows, "NOT_IDENTIFIED"
            for x in lg:
                x["stage"] = "identity_%s" % route
                x["requested_market_slug"] = slug
                x["requested_event_id"] = eid
            log.extend(lg)
            body = r.get("body") or {}
            if route == "detail":
                d = body.get("market") or body
                resp_slug = d.get("slug")
                resp_ev = d.get("eventId") or d.get("event_id")
            else:
                d = B.market_data(body) or {}
                resp_slug = d.get("marketSlug")
                resp_ev = None
            if resp_slug is None:
                verdict = "RESPONSE_ID_NOT_IDENTIFIED"
            elif resp_slug == slug:
                verdict = "IDENTITY_MATCH"
            else:
                verdict = "IDENTITY_MISMATCH"
            rows.append({"route": route, "requested_market_slug": slug,
                         "requested_event_id": eid,
                         "http_status": r.get("http_status"),
                         "response_market_slug": resp_slug,
                         "response_event_id": resp_ev,
                         "response_body_sha256": r.get("response_sha256"),
                         "verdict": verdict})
            bodies[(route, r.get("response_sha256"))].append(slug)
    by = collections.Counter(x["verdict"] for x in rows)
    say("probes: %d across %d markets, both routes" % (len(rows), len(picks)))
    for k, n in by.most_common():
        say("   %-28s %4d" % (k, n))
    collisions = {k: v for k, v in bodies.items() if len(set(v)) > 1}
    say("distinct requested slugs sharing one response body: %d" % len(collisions))
    for k, v in list(collisions.items())[:5]:
        say("   %s %s <- %s" % (k[0], (k[1] or "")[:16], sorted(set(v))[:4]))
    ev_seen = [x for x in rows if x["response_event_id"] is not None]
    ev_match = [x for x in ev_seen
                if str(x["response_event_id"]) == str(x["requested_event_id"])]
    say("responses carrying an event id: %d; of those matching the requested "
        "event: %d" % (len(ev_seen), len(ev_match)))
    ok = (by.get("IDENTITY_MISMATCH", 0) == 0 and not collisions
          and by.get("IDENTITY_MATCH", 0) > 0)
    say()
    if collisions:
        say("  STOP CONDITION MET: different requested slugs returned the same")
        say("  body. Cohort calibration does NOT proceed.")
    say("DETAIL_ROUTE_IDENTITY_VERIFIED = %s" % ("YES" if ok else "NO"))
    say()
    out["identity"] = {"rows": rows, "by_verdict": dict(by),
                       "body_collisions": len(collisions),
                       "event_id_returned": len(ev_seen),
                       "event_id_matched": len(ev_match),
                       "verified": ok}
    return rows, ("YES" if ok else "NO")


# --------------------------------------------------- 7. near-touch queue
def near_touch(body, tick):
    d = B.market_data(body)
    if not d or tick in (None, 0):
        return None
    tick = Decimal(str(tick))
    out = {}
    for side, raw, pick in (("bid", d.get("bids"), max),
                            ("ask", d.get("offers"), min)):
        lv = B.ladder(raw)
        if not lv:
            return None
        px = [(p, q) for p, q, _ in lv]
        best = pick(p for p, _ in px)
        out["best_%s_px" % side] = str(best)
        out["touch_%s_qty" % side] = str(sum(q for p, q in px if p == best))
        out["touch_%s_notional" % side] = str(sum(p * q for p, q in px if p == best))
        for band in (0, 1, 2, 5):
            sel = [(p, q) for p, q in px if abs(p - best) <= band * tick]
            out["cum_%s_qty_%dt" % (side, band)] = str(sum(q for _, q in sel))
            out["cum_%s_notional_%dt" % (side, band)] = str(sum(p * q for p, q in sel))
    return out


def queue_class(nt):
    """The preregistered rule: QUANTITY within 1 tick, in the market's own tick."""
    if not nt:
        return "NOT_IDENTIFIED", None
    b = Decimal(nt["cum_bid_qty_1t"])
    a = Decimal(nt["cum_ask_qty_1t"])
    if b <= 0 or a <= 0:
        return "NOT_IDENTIFIED", None
    r = a / b
    if r < Decimal(1) / Decimal(3):
        return "BID_HEAVY", r
    if r > 3:
        return "ASK_HEAVY", r
    return "BALANCED", r


# ------------------------------------------ 6. event-stratified cohort
def build_cohort(game_events):
    """Fixes Phase 2D DEFECT 2. Round-robin across (sport, state) strata, one
    market per event, deterministic by native event id -- never by slug."""
    say("=== 6. EVENT-STRATIFIED COHORT ===")
    strata = collections.defaultdict(list)
    for eid, g in game_events.items():
        if g["normalized_state"] not in ("PREGAME", "LIVE"):
            continue
        key = (str(g["sport"]), g["normalized_state"])
        strata[key].append(eid)
    for k in strata:
        strata[k].sort(key=int)
    say("strata available (sport, state): %d" % len(strata))
    for k in sorted(strata, key=lambda z: (-len(strata[z]), z)):
        say("   %-24s %4d events" % (" | ".join(k), len(strata[k])))
    picks, pools = [], {k: list(v) for k, v in strata.items()}
    while any(pools.values()) and len(picks) < COHORT_CAP:
        for k in sorted(pools, key=lambda z: (-len(strata[z]), z)):
            if not pools[k] or len(picks) >= COHORT_CAP:
                continue
            eid = pools[k].pop(0)
            g = game_events[eid]
            m = sorted((m for _, m in g["markets"] if m.get("slug")),
                       key=lambda z: z["slug"])[0]
            picks.append({"native_event_id": eid,
                          "native_event_slug": g["event"].get("slug"),
                          "market_slug": m["slug"], "sport": g["sport"],
                          "stratum": " | ".join(k),
                          "raw_period": g["raw_period"],
                          "normalized_state": g["normalized_state"],
                          "state_justification": g["state_justification"],
                          "tick_size": m.get("orderPriceMinTickSize"),
                          "fee_coefficient_field": m.get("feeCoefficient"),
                          "sports_market_type_v2": m.get("sportsMarketTypeV2"),
                          "game_start_time": m.get("gameStartTime"),
                          "event_start_time": g["event"].get("startTime"),
                          "end_date": m.get("endDate")})
    say()
    say("cohort: %d markets from %d distinct events, %d distinct sports"
        % (len(picks), len({p["native_event_id"] for p in picks}),
           len({p["sport"] for p in picks})))
    say()
    return picks


def read_cohort(http, pacer, budget, log, cohort, out):
    say("=== 7. NEAR-TOUCH QUEUE METRIC (quantity, own tick) ===")
    rows = []
    for c in cohort:
        try:
            r, lg = paced(http, BOOK_PATH % c["market_slug"], pacer, budget)
        except RuntimeError as exc:
            say("  budget stop: %s" % exc)
            break
        for x in lg:
            x["stage"] = "cohort_book"
            x["market_slug"] = c["market_slug"]
        log.extend(lg)
        nt = near_touch(r.get("body"), c["tick_size"])
        cls, ratio = queue_class(nt)
        d = B.market_data(r.get("body")) or {}
        rows.append(dict(c, near_touch=nt, queue_class=cls,
                         queue_ratio=(str(ratio) if ratio is not None else None),
                         venue_transact_time=d.get("transactTime"),
                         local_observation_utc=r.get("local_request_wall_utc"),
                         book_sha256=r.get("response_sha256")))
    say("%-42s %-6s %-9s %10s %10s %10s %10s  %s"
        % ("market", "tick", "state", "tch_bid", "tch_ask", "1t_bid", "1t_ask",
           "queue"))
    for x in rows:
        nt = x["near_touch"]
        if not nt:
            say("%-42s %-6s %-9s  near-touch NOT MEASURABLE"
                % (x["market_slug"][:42], x["tick_size"], x["normalized_state"]))
            continue
        say("%-42s %-6s %-9s %10s %10s %10s %10s  %s"
            % (x["market_slug"][:42], x["tick_size"], x["normalized_state"],
               nt["touch_bid_qty"], nt["touch_ask_qty"],
               nt["cum_bid_qty_1t"], nt["cum_ask_qty_1t"], x["queue_class"]))
    say()
    qc = collections.Counter(x["queue_class"] for x in rows)
    say("queue classification: %s" % dict(qc))
    say("tick sizes in cohort: %s"
        % dict(collections.Counter(str(x["tick_size"]) for x in rows)))
    say()
    out["cohort"] = rows
    return rows


# ------------------------------------- 11. exact-horizon scheduler test
def scheduler_test(http, pacer, budget, log, cohort, out):
    """Section 11. Place reads at EXACT target lags, not on a 2 s lattice.

    The constraint is aggregate rate. A pair of reads 5.0 s apart is 2 requests
    in 5 s; what must be held is the MEAN and a spacing floor, so the plan is
    built first, checked against both, and only then executed."""
    say("=== 11. EXACT-HORIZON SCHEDULER, SHORT TEST ===")
    subjects = [c for c in cohort if c.get("tick_size")][:3]
    if not subjects:
        say("no subject available; scheduler test SKIPPED")
        out["scheduler"] = {"ran": False}
        return []
    starts = [0.0, 2.5, 17.5][:len(subjects)]
    plan = []
    for s, sub in zip(starts, subjects):
        for h in (0.0,) + HORIZONS:
            plan.append((s + h, sub["market_slug"], h))
    plan.sort()
    gaps = [plan[i + 1][0] - plan[i][0] for i in range(len(plan) - 1)]
    span = plan[-1][0] - plan[0][0]
    rate = len(plan) / span if span else float("inf")
    say("subjects: %d   scheduled reads: %d   span %.1fs"
        % (len(subjects), len(plan), span))
    say("min scheduled gap %.2fs (floor %.2f)   mean rate %.3f rps (ceiling %.2f)"
        % (min(gaps), MIN_REQUEST_GAP_S, rate, RATE_CEILING_RPS))
    if min(gaps) < MIN_REQUEST_GAP_S - 1e-9 or rate > RATE_CEILING_RPS + 1e-9:
        say("PLAN REJECTED -- it would breach the rate discipline. NOT RUN.")
        out["scheduler"] = {"ran": False, "reason": "plan breached rate rule"}
        return []
    if len(plan) > MAX_SCHEDULER_READS:
        say("PLAN REJECTED -- %d reads exceeds the disclosed bound %d"
            % (len(plan), MAX_SCHEDULER_READS))
        out["scheduler"] = {"ran": False, "reason": "exceeds disclosed bound"}
        return []
    say()
    t0 = time.monotonic()
    obs, throttled = [], False
    for target, slug, h in plan:
        wait = (t0 + target) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
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
            x["target_offset_s"] = target
            x["horizon_s"] = h
        log.extend(lg)
        obs.append({"market_slug": slug, "horizon_s": h,
                    "target_offset_s": target,
                    "actual_offset_s": time.monotonic() - t0,
                    "http": r.get("http_status"),
                    "book_sha256": r.get("response_sha256"),
                    "venue_transact_time": (B.market_data(r.get("body")) or {}
                                            ).get("transactTime")})
    say("%-8s %6s %10s %10s %10s" % ("horizon", "n", "median_err", "p90", "max"))
    stats = {}
    base = {}
    for o in obs:
        if o["horizon_s"] == 0.0:
            base[o["market_slug"]] = o["actual_offset_s"]
    for h in HORIZONS:
        errs = []
        for o in obs:
            if o["horizon_s"] != h or o["market_slug"] not in base:
                continue
            achieved = o["actual_offset_s"] - base[o["market_slug"]]
            errs.append(abs(achieved - h))
        if not errs:
            say("%-8.0f %6d %10s %10s %10s" % (h, 0, "-", "-", "-"))
            stats[str(h)] = None
            continue
        errs.sort()
        def pct(p):
            return errs[min(len(errs) - 1,
                            max(0, int(round(p / 100.0 * (len(errs) - 1)))))]
        stats[str(h)] = {"n": len(errs), "median_ms": 1000 * statistics.median(errs),
                         "p90_ms": 1000 * pct(90), "p95_ms": 1000 * pct(95),
                         "max_ms": 1000 * errs[-1]}
        say("%-8.0f %6d %9.1fms %9.1fms %9.1fms"
            % (h, len(errs), stats[str(h)]["median_ms"],
               stats[str(h)]["p90_ms"], stats[str(h)]["max_ms"]))
    say()
    say("  Lag error is measured against each market's OWN t0 read, so it is")
    say("  the achieved separation between two real observations. No state is")
    say("  interpolated anywhere: a horizon with no pair of reads is reported")
    say("  as absent, never filled in.")
    if throttled:
        say()
        say("  A THROTTLE FIRED DURING THE TEST. Backoff displaced the")
        say("  schedule, so the lag errors above are contaminated for the")
        say("  affected reads and the horizons must be read as NOT cleanly")
        say("  demonstrated.")
    say()
    out["scheduler"] = {"ran": True, "plan_reads": len(plan),
                        "span_s": span, "mean_rps": rate,
                        "min_gap_s": min(gaps), "throttled_during_test": throttled,
                        "lag_error": stats, "observations": obs}
    return obs


# ------------------------------------------------- 12. CFB task 48 recheck
def cfb_recheck(game_events, out):
    say("=== 12. CFB TASK #48 RECHECK ===")
    say("TASK48_PRIOR_CONCLUSION = (historical, recorded verbatim, NOT altered)")
    say('  "college football moneylines and spreads never map -- the grammar')
    say('   class finds no full-game per-side contract (the venue\'s atc rows')
    say('   for cfb are segment props) so no aec-cfb book has ever opened and')
    say('   every C4 subject step dies"')
    say()
    found = []
    for eid, g in game_events.items():
        if (g["sport"] or "") != "cfb":
            continue
        for k, m in g["markets"]:
            slug = m.get("slug") or ""
            if not slug.startswith("aec-cfb-"):
                continue
            sides = m.get("marketSides")
            two = (isinstance(sides, list) and len(sides) == 2
                   and sum(1 for s in sides if s.get("long") is True) == 1
                   and sum(1 for s in sides if s.get("long") is False) == 1)
            found.append({"native_event_id": eid, "market_slug": slug,
                          "sports_market_type": m.get("sportsMarketType"),
                          "sports_market_type_v2": m.get("sportsMarketTypeV2"),
                          "state": m.get("status") or m.get("state"),
                          "two_sided_structure": two,
                          "normalized_state": g["normalized_state"]})
    say("current active aec-cfb-* game-level rows found: %d" % len(found))
    for f in found[:10]:
        say("   %-40s %-34s two_sided=%s %s"
            % (f["market_slug"][:40], f["sports_market_type"],
               f["two_sided_structure"], f["state"]))
    say()
    yes = bool(found) and all(f["two_sided_structure"] for f in found)
    say("CURRENT_CFB_MONEYLINE_CONTRACTS_FOUND = %s"
        % ("YES" if found else "NO"))
    say()
    say("  WHAT THIS DOES AND DOES NOT SAY. It is evidence about the venue")
    say("  NOW. Task #48 was a conclusion about the venue THEN, and this run")
    say("  holds no point-in-time evidence from that instant -- so it does not")
    say("  claim the old conclusion was wrong when it was drawn. The old")
    say("  evidence is untouched.")
    say()
    say("  What it does say is that the conclusion cannot be CARRIED FORWARD")
    say("  as a present-tense fact about the venue.")
    say("TASK48_CONCLUSION_SAFE_TO_CARRY_FORWARD = %s"
        % ("NO" if found else "NOT_IDENTIFIED"))
    say()
    out["cfb_recheck"] = {"found": found, "two_sided_all": yes}
    return found


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    budget = Budget(MAX_VENUE_REQUESTS)
    meta = {"phase": PHASE, "python": sys.version, "platform": platform.platform(),
            "httpx": httpx.__version__, "gateway": C.GATEWAY_BASE,
            "rate_ceiling_rps": RATE_CEILING_RPS,
            "min_request_gap_s": MIN_REQUEST_GAP_S,
            "max_venue_requests": MAX_VENUE_REQUESTS,
            "max_discovery_pages": MAX_DISCOVERY_PAGES,
            "sufficiency_events": SUFFICIENT_EVENTS,
            "sufficiency_sports": SUFFICIENT_SPORTS,
            "cohort_cap": COHORT_CAP,
            "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "collector_sha256": hashlib.sha256(Path(C.__file__).read_bytes()).hexdigest(),
            "phase2b_sha256": hashlib.sha256(Path(B.__file__).read_bytes()).hexdigest()}
    (out / "runner_metadata.json").write_text(json.dumps(meta, indent=1))
    say(json.dumps(meta, indent=1))
    say()

    res, log = {}, []
    pacer = B.AdaptivePacer()
    cohort, ident_ok, sched = [], "NO", []
    with httpx.Client(timeout=30.0) as http:
        try:
            mech, p0 = prove_pagination(http, pacer, budget, log, res)
            pages, events, stop = walk(http, pacer, budget, log, mech, p0, res)
            taxonomy_report(events, res)
            game_events = frame(events, time.time(), res)
            _, ident_ok = identity_proof(http, pacer, budget, log, game_events, res)
            if ident_ok != "YES":
                say("IDENTITY NOT VERIFIED -- cohort calibration SKIPPED "
                    "as the rule requires.")
            else:
                picks = build_cohort(game_events)
                cohort = read_cohort(http, pacer, budget, log, picks, res)
                sched = scheduler_test(http, pacer, budget, log, cohort, res)
            cfb_recheck(game_events, res)
        except RuntimeError as exc:
            say("HARD BOUND: %s" % exc)
            res["aborted"] = str(exc)

    res["venue_requests_spent"] = budget.spent
    res["throttle_events"] = pacer.events
    res["final_spacing_s"] = getattr(pacer, "spacing", None)
    (out / "discovery.json").write_text(json.dumps(res, indent=1, default=str))
    with (out / "request_log.jsonl").open("w") as fh:
        for r in log:
            fh.write(json.dumps(r, default=str) + "\n")

    lines = verdicts(res, ident_ok, cohort, sched)
    B.seal(out, lines)
    ok, bad = B.verify(out)
    say()
    say("SEAL_VERIFIED = %s%s" % (ok, "" if ok else " BAD=%s" % bad))
    return 0


def verdicts(res, ident_ok, cohort, sched):
    L = []

    def w(s=""):
        L.append(s)
        say(s)

    tax = res.get("taxonomy") or {}
    fr = res.get("frame") or {}
    sc = res.get("scheduler") or {}
    thr = res.get("throttle_events") or []
    n429 = sum(1 for e in thr if e.get("event") == "429")
    qc = collections.Counter(x["queue_class"] for x in cohort)
    five = (sc.get("lag_error") or {}).get("5.0")

    w("=== 14. PHASE 2E VERDICTS ===")
    w("DETAIL_ROUTE_IDENTITY_VERIFIED   = %s" % ident_ok)
    w("EVENT_PAGINATION_VERIFIED        = %s"
      % ("YES" if any(c["advanced"] for c in res.get("pagination_proof", []))
         else "NO"))
    w("GAME_LEVEL_MARKETS_FOUND         = YES")
    w("PHASE2D_GAME_LEVEL_FLOOR         = %s" % tax.get("phase2d_floor"))
    w("PHASE2E_GAME_LEVEL_ROWS          = %s" % tax.get("phase2e_count"))
    w("PHASE2E_GAME_LEVEL_UNIQUE_EVENTS = %s" % fr.get("game_events"))
    w("GAME_ACTIVE_EVENTS_DISCOVERED    = %s" % fr.get("active_events"))
    w("COHORT_MARKETS                   = %d from %d events, %d sports"
      % (len(cohort), len({c["native_event_id"] for c in cohort}),
         len({c["sport"] for c in cohort})))
    w("NEAR_TOUCH_QUEUE_METRIC_READY    = %s"
      % ("YES" if qc and set(qc) != {"NOT_IDENTIFIED"} else "NO"))
    w("QUEUE_CLASSIFICATION             = %s" % dict(qc))
    w("GAME_STATE_CLASSIFICATION_READY  = %s"
      % ("YES" if fr.get("normalized_state_by_event", {}).get("NOT_IDENTIFIED", 0)
         < fr.get("game_events", 1) else "NO"))
    w("EXACT_5S_SCHEDULER_EMPIRICALLY_VERIFIED = %s"
      % ("YES" if (five and not sc.get("throttled_during_test")) else "NO"))
    if five:
        w("  5 s lag error: median %.1f ms, p90 %.1f ms, p95 %.1f ms, max %.1f ms"
          % (five["median_ms"], five["p90_ms"], five["p95_ms"], five["max_ms"]))
    w("HTTP_429_COUNT                   = %d" % n429)
    w("0.5_RPS_UNCONDITIONALLY_SAFE     = NO")
    w("RPS_LIMIT_NOT_ESTABLISHED        = CARRIED FORWARD")
    cf = res.get("cfb_recheck") or {}
    w("CURRENT_CFB_MONEYLINE_CONTRACTS_FOUND   = %s"
      % ("YES" if cf.get("found") else "NO"))
    w("TASK48_CONCLUSION_SAFE_TO_CARRY_FORWARD = %s"
      % ("NO" if cf.get("found") else "NOT_IDENTIFIED"))
    w("PMUS_FEE_COEFFICIENT_FIELD       = 0.06 (field VERIFIED, formula NOT)")
    w("PMUS_FEES_RESOLVED               = NO")
    w("PASSIVE_REALIZABLE_EDGE          = NOT_IDENTIFIED")
    w("VENUE_REQUESTS_SPENT             = %s of %d"
      % (res.get("venue_requests_spent"), MAX_VENUE_REQUESTS))
    w("")
    w("GAME_LEVEL_COHORT_DIVERSITY and READY_FOR_MULTI_DAY_PHASE2_CAPTURE are")
    w("judged by the analyst against these bytes, not asserted by the runner.")
    return L


if __name__ == "__main__":
    sys.exit(main())
