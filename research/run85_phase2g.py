#!/usr/bin/env python3
"""RUN 85 PHASE 2G -- cohort closure only. READ ONLY. NO CREDENTIAL.

The single open blocker is GAME_LEVEL_COHORT_DIVERSITY. Everything else from
Phase 2F stays locked and is not revisited.

--------------------------------------------------------------------------
THE FIX, WHICH IS A ONE-LINE CHANGE OF SUBJECT
--------------------------------------------------------------------------
Phase 2E stopped walking when the frame held enough EVENTS AND SPORTS.
Phase 2F stopped walking when the FRAME's own composition was diverse.
Both were the wrong subject: the deliverable is the SELECTED COHORT.

    STOP_CONDITION = select(frame).diversity_passes     <- 2G
    not              frame.diversity_passes             <- 2F

At every offset increment this run rebuilds the frame, runs THE SELECTOR, and
scores THE RESULTING COHORT. There is exactly one selector function,
`select_cohort`, and one scorer, `score_cohort`. The trial call inside the walk
and the final call after it are THE SAME FUNCTIONS ON THE SAME FEATURES -- no
second approximation of the rule exists anywhere in this file.

--------------------------------------------------------------------------
PREREGISTERED SELECTION RULE, frozen before the data
--------------------------------------------------------------------------
ELIGIBLE  game-level (GAME_BINARY or GAME_THREE_WAY by the 2E taxonomy),
          normalized state PREGAME or LIVE, venue tick present, and both
          discovery touch quotes present so price and spread are observable
          without spending a request.

FEATURES  all from the discovery payload, so a trial selection costs nothing:
            sport       primaryTag; NULL IS NOT A SPORT and is never a bucket
            price_band  mid < 0.20 P_LOW, < 0.80 P_MID, else P_HIGH
            tick        venue orderPriceMinTickSize, verbatim
            spread_tk   round(spread / tick): 1, 2-3, 4+
            state       PREGAME / LIVE

ORDER     never by slug. Candidates are ordered by native event id ascending;
          strata are visited by a fixed key order. Fully deterministic.

S1  group eligible events by stratum (sport, state, price_band, spread_tk)
S2  visit strata round-robin, largest first then by key, taking the lowest
    native event id not yet used
S3  at most ONE market per native event
S4  NO SPORT MAY EXCEED ceil(cap/2) OF THE COHORT -- the anti-domination rule
    that 2F lacked, which is how 7 of 8 came back NFL
S5  stop at the cap

--------------------------------------------------------------------------
FROZEN COHORT DIVERSITY GATE
--------------------------------------------------------------------------
Each dimension is REQUIRED only if the frame actually offers more than one
value of it; otherwise it is WAIVED_BY_ABSENCE and reported as such, never
silently passed.

    sport       >= 2 distinct non-null sports
    price_band  >= 2 distinct bands
    tick        >= 2 distinct ticks        (waived if the frame has only one)
    spread_tk   >= 2 distinct regimes      (waived if the frame has only one)
    state       LIVE included if the frame has any; never forced

QUEUE REGIME IS MEASURED, NOT GATED, AND THE REASON IS SAID OUT LOUD.
Near-touch queue needs a book read per candidate, so it cannot be scored
inside a per-page trial loop without spending a request on every candidate on
every page. It is therefore measured on the FINAL cohort and reported. It is
not a selection feature and not a stop criterion. Selecting on it after
measuring it would be choosing the cohort by an observed outcome, which is the
bias this whole phase exists to avoid.

--------------------------------------------------------------------------
BOUND AND RATE
--------------------------------------------------------------------------
MAX_VENUE_REQUESTS 120 · MAX_OFFSET_PAGES 40 · nominal 0.4 rps, 2.5 s spacing
Retry-After honoured exactly, adaptive backoff, no rate testing.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import math
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
_e = importlib.util.spec_from_file_location(
    "run85e", Path(__file__).with_name("run85_phase2e.py"))
E = importlib.util.module_from_spec(_e)
_e.loader.exec_module(E)
_f = importlib.util.spec_from_file_location(
    "run85f", Path(__file__).with_name("run85_phase2f.py"))
F = importlib.util.module_from_spec(_f)
_f.loader.exec_module(F)

PHASE = "run85/phase2g/1"
NOMINAL_RPS = 0.4
SPACING_S = 1.0 / NOMINAL_RPS
MAX_VENUE_REQUESTS = 120
MAX_OFFSET_PAGES = 40
COHORT_CAP = 8
EVENTS_PATH = "/v1/events"
BOOK_PATH = "/v1/markets/%s/book"


def say(s=""):
    print(s)


def amt(x):
    if isinstance(x, dict) and x.get("value") is not None:
        try:
            return Decimal(str(x["value"]))
        except Exception:                              # noqa: BLE001
            return None
    return None


def spread_bucket(spread, tick):
    if spread is None or not tick:
        return None
    n = int((spread / Decimal(str(tick))).to_integral_value(rounding="ROUND_HALF_UP"))
    return "S_1T" if n <= 1 else "S_2_3T" if n <= 3 else "S_4T_PLUS"


# ---------------------------------------------------------- the frame
def eligible(events, now_epoch):
    """EVENT -> its eligible game-level markets, with every selection feature
    read from the discovery payload so a trial selection costs no request."""
    out = {}
    for eid, e in events.items():
        pt = e.get("primaryTag")
        tag = (pt or {}).get("slug") if isinstance(pt, dict) else None
        sport = F.sport_bucket(tag)          # None for a null tag: NOT a sport
        raw = e.get("period")
        norm, w1 = E.normalize_state(raw, tag)
        norm, w2 = E.corroborate(norm, e.get("startTime"), now_epoch)
        if norm not in ("PREGAME", "LIVE"):
            continue
        cands = []
        for m in (e.get("markets") or []):
            if E.classify(m) not in ("GAME_BINARY", "GAME_THREE_WAY"):
                continue
            tick = m.get("orderPriceMinTickSize")
            bb, ba = amt(m.get("bestBidQuote")), amt(m.get("bestAskQuote"))
            if not tick or bb is None or ba is None:
                continue
            mid = (bb + ba) / 2
            cands.append({
                "market_slug": m.get("slug"),
                "sports_market_type_v2": m.get("sportsMarketTypeV2"),
                "tick_size": str(tick), "best_bid": str(bb), "best_ask": str(ba),
                "mid": str(mid), "spread": str(ba - bb),
                "spread_bucket": spread_bucket(ba - bb, tick),
                "price_band": F.price_band(mid),
                "fee_coefficient_field": m.get("feeCoefficient"),
                "scheduled_game_start": m.get("gameStartTime"),
                "end_date": m.get("endDate")})
        if not cands:
            continue
        cands.sort(key=lambda z: z["market_slug"] or "")
        out[str(eid)] = {
            "native_event_id": str(eid),
            "native_event_slug": e.get("slug"),
            "sport": tag, "sport_bucket": sport,
            "league": sorted({t.get("league") for t in (e.get("teams") or [])
                              if isinstance(t, dict) and t.get("league")}),
            "raw_period": raw, "normalized_state": norm,
            "state_justification": "%s; %s" % (w1, w2),
            "event_start_time": e.get("startTime"),
            "candidates": cands}
    return out


# ------------------------------------- THE selector (one implementation)
def select_cohort(frame, cap=COHORT_CAP):
    """The ONE selection rule. Called identically by the trial loop inside the
    walk and by the final selection after it. Deterministic: native event id
    ascending within a fixed stratum order, never a slug sort."""
    strata = collections.defaultdict(list)
    for eid, g in frame.items():
        if not g["sport_bucket"]:
            continue                          # a null tag is not a sport
        c = g["candidates"][0]
        strata[(g["sport_bucket"], g["normalized_state"],
                c["price_band"], c["spread_bucket"])].append(eid)
    for k in strata:
        strata[k].sort(key=int)
    per_sport_cap = math.ceil(cap / 2)         # S4 anti-domination
    picks, used_ev, per_sport = [], set(), collections.Counter()
    pools = {k: list(v) for k, v in strata.items()}
    progress = True
    while progress and len(picks) < cap:
        progress = False
        for k in sorted(pools, key=lambda z: (-len(strata[z]),
                                              tuple(str(x) for x in z))):
            if len(picks) >= cap:
                break
            sport = k[0]
            if per_sport[sport] >= per_sport_cap:
                continue
            while pools[k]:
                eid = pools[k].pop(0)
                if eid in used_ev:
                    continue
                g = frame[eid]
                c = g["candidates"][0]
                picks.append({k2: v2 for k2, v2 in g.items()
                              if k2 != "candidates"} | dict(c, stratum=" | ".join(
                                  str(x) for x in k)))
                used_ev.add(eid)
                per_sport[sport] += 1
                progress = True
                break
    return picks


def score_cohort(cohort, frame):
    """The frozen gate. A dimension the frame cannot vary is WAIVED_BY_ABSENCE
    and named, never silently passed."""
    def frame_vals(fn):
        v = set()
        for g in frame.values():
            if not g["sport_bucket"]:
                continue
            v.add(fn(g))
        return {x for x in v if x is not None}

    f_sport = frame_vals(lambda g: g["sport_bucket"])
    f_band = frame_vals(lambda g: g["candidates"][0]["price_band"])
    f_tick = frame_vals(lambda g: g["candidates"][0]["tick_size"])
    f_spr = frame_vals(lambda g: g["candidates"][0]["spread_bucket"])
    f_live = any(g["normalized_state"] == "LIVE" for g in frame.values()
                 if g["sport_bucket"])

    c_sport = {c["sport_bucket"] for c in cohort}
    c_band = {c["price_band"] for c in cohort}
    c_tick = {c["tick_size"] for c in cohort}
    c_spr = {c["spread_bucket"] for c in cohort}
    c_live = any(c["normalized_state"] == "LIVE" for c in cohort)

    res = {}
    res["sport"] = ("PASS" if len(c_sport) >= 2 else
                    "WAIVED_BY_ABSENCE" if len(f_sport) < 2 else "FAIL")
    res["price_band"] = ("PASS" if len(c_band) >= 2 else
                         "WAIVED_BY_ABSENCE" if len(f_band) < 2 else "FAIL")
    res["tick"] = ("PASS" if len(c_tick) >= 2 else
                   "WAIVED_BY_ABSENCE" if len(f_tick) < 2 else "FAIL")
    res["spread"] = ("PASS" if len(c_spr) >= 2 else
                     "WAIVED_BY_ABSENCE" if len(f_spr) < 2 else "FAIL")
    res["live_state"] = ("PASS" if c_live else
                         "WAIVED_BY_ABSENCE" if not f_live else "FAIL")
    passes = all(v in ("PASS", "WAIVED_BY_ABSENCE") for v in res.values())
    # A cohort of one market trivially "spans" nothing; require real size.
    if len(cohort) < 4:
        passes = False
        res["size"] = "FAIL"
    else:
        res["size"] = "PASS"
    return passes, res, {"frame_sports": sorted(f_sport),
                         "frame_bands": sorted(f_band),
                         "frame_ticks": sorted(f_tick),
                         "frame_spreads": sorted(f_spr),
                         "frame_has_live": f_live}


class Budget:
    def __init__(self, cap):
        self.cap, self.spent = cap, 0

    def take(self, n=1):
        if self.spent + n > self.cap:
            raise RuntimeError("BOUND_REACHED spent=%d cap=%d" % (self.spent, self.cap))
        self.spent += n


def paced(http, path, pacer, budget, params=None):
    budget.take()
    return B.get_paced(http, path, pacer, params)


def walk(http, pacer, budget, log, out):
    say("=== 1. WALK WITH SELECT-THEN-SCORE STOP CONDITION ===")
    say("stop when select(frame) passes the gate -- NOT when the frame does.")
    say()
    events, pages, trials = {}, 0, 0
    stop = "NOT_STARTED"
    cohort, gate, avail = [], {}, {}
    for k in range(MAX_OFFSET_PAGES):
        params = {"active": "true", "closed": "false", "limit": 100}
        if k:
            params["offset"] = 100 * k
        try:
            r, rows = paced(http, EVENTS_PATH, pacer, budget, params)
        except RuntimeError as exc:
            stop = str(exc)
            break
        for x in rows:
            x["stage"] = "walk"
        log.extend(rows)
        pages += 1
        body = r.get("body") or {}
        got = body.get("events") or []
        for e in got:
            events.setdefault(str(e.get("id")), e)
        frame = eligible(events, time.time())
        cohort = select_cohort(frame)                  # <- THE selector
        trials += 1
        ok, gate, avail = score_cohort(cohort, frame)  # <- THE scorer
        say("p%-2d off=%-5s ev=%4d eligible=%3d cohort=%d sports=%s bands=%s "
            "ticks=%s -> %s"
            % (k, params.get("offset", 0), len(events), len(frame), len(cohort),
               sorted({c["sport_bucket"] for c in cohort}),
               sorted({c["price_band"] for c in cohort}),
               sorted({c["tick_size"] for c in cohort}),
               "PASS" if ok else "no"))
        if ok:
            stop = "SELECTED_COHORT_PASSES_GATE"
            break
        if not got:
            stop = "EMPTY_PAGE"
            break
    else:
        stop = "PAGE_BOUND_REACHED"
    say()
    say("WALK_STOP_REASON = %s" % stop)
    out["walk"] = {"stop": stop, "pages": pages, "events": len(events),
                   "trials": trials}
    return events, cohort, gate, avail, pages, trials, stop


def measure(http, pacer, budget, log, cohort, out):
    """Queue regime is MEASURED here, after selection, and is not a selection
    feature. Choosing the cohort by a measured queue would be selecting on an
    observed outcome."""
    say("=== 6. FINAL COHORT TABLE (queue measured, not selected on) ===")
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
        nt = E.near_touch(r.get("body"), c["tick_size"])
        q, ratio = E.queue_class(nt)
        d = B.market_data(r.get("body")) or {}
        rows.append(dict(c, near_touch=nt, queue_regime=q,
                         queue_ratio=str(ratio) if ratio is not None else None,
                         venue_transact_time=d.get("transactTime"),
                         local_observation_utc=r.get("local_request_wall_utc"),
                         book_sha256=r.get("response_sha256")))
    for x in rows:
        nt = x["near_touch"] or {}
        say("  %s" % x["market_slug"])
        say("     event %s (%s)  sport=%s league=%s"
            % (x["native_event_id"], x["native_event_slug"], x["sport"],
               x["league"]))
        say("     raw_period=%r state=%s  game_start=%s"
            % (x["raw_period"], x["normalized_state"], x["scheduled_game_start"]))
        say("     bid %s  ask %s  mid %s  tick %s  spread %s (%s)"
            % (x["best_bid"], x["best_ask"], x["mid"], x["tick_size"],
               x["spread"], x["spread_bucket"]))
        say("     touch qty  bid %-12s ask %s"
            % (nt.get("touch_bid_qty"), nt.get("touch_ask_qty")))
        say("     1t qty     bid %-12s ask %s"
            % (nt.get("cum_bid_qty_1t"), nt.get("cum_ask_qty_1t")))
        say("     2t qty     bid %-12s ask %s"
            % (nt.get("cum_bid_qty_2t"), nt.get("cum_ask_qty_2t")))
        say("     5t qty     bid %-12s ask %s"
            % (nt.get("cum_bid_qty_5t"), nt.get("cum_ask_qty_5t")))
        say("     band=%s  queue=%s" % (x["price_band"], x["queue_regime"]))
    say()
    out["cohort"] = rows
    return rows


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
            "max_offset_pages": MAX_OFFSET_PAGES, "cohort_cap": COHORT_CAP,
            "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (out / "runner_metadata.json").write_text(json.dumps(meta, indent=1))
    say(json.dumps(meta, indent=1))
    say()
    res, log = {}, []
    pacer = B.AdaptivePacer(base=SPACING_S)
    rows, gate, avail, pages, trials, stop = [], {}, {}, 0, 0, "NOT_RUN"
    t0 = time.monotonic()
    with httpx.Client(timeout=30.0) as http:
        try:
            events, cohort, gate, avail, pages, trials, stop = walk(
                http, pacer, budget, log, res)
            # FINAL SELECTION: the same function, on the same features.
            frame = eligible(events, time.time())
            final = select_cohort(frame)
            identical = ([c["market_slug"] for c in final]
                         == [c["market_slug"] for c in cohort])
            say("final selection re-run: identical to the last trial = %s"
                % identical)
            say()
            res["selector_identical_trial_and_final"] = bool(identical)
            res["eligible_game_events"] = len(frame)
            rows = measure(http, pacer, budget, log, final, res)
            ok, gate, avail = score_cohort(final, frame)
            res["gate"] = gate
            res["frame_availability"] = avail
            res["gate_passes"] = ok
        except RuntimeError as exc:
            say("HARD BOUND: %s" % exc)
            res["aborted"] = str(exc)
    el = time.monotonic() - t0
    res.update({"venue_requests_spent": budget.spent, "elapsed_s": el,
                "achieved_rps": budget.spent / el if el else None,
                "throttle_events": pacer.events})
    (out / "cohort.json").write_text(json.dumps(res, indent=1, default=str))
    with (out / "request_log.jsonl").open("w") as fh:
        for r in log:
            fh.write(json.dumps(r, default=str) + "\n")
    B.seal(out, verdicts(res, rows, gate, avail, pages, trials, stop))
    ok, bad = B.verify(out)
    say()
    say("SEAL_VERIFIED = %s%s" % (ok, "" if ok else " BAD=%s" % bad))
    return 0


def verdicts(res, rows, gate, avail, pages, trials, stop):
    L = []

    def w(s=""):
        L.append(s)
        say(s)

    thr = res.get("throttle_events") or []
    n429 = sum(1 for e in thr if e.get("event") == "429")
    w("=== 7. REQUIRED COUNTS ===")
    w("OFFSET_PAGES_WALKED        = %d" % pages)
    w("EVENTS_DISCOVERED          = %d" % (res.get("walk") or {}).get("events", 0))
    w("ELIGIBLE_GAME_EVENTS       = %d" % res.get("eligible_game_events", 0))
    w("TRIAL_SELECTIONS_EVALUATED = %d" % trials)
    w("FINAL_COHORT_SIZE          = %d" % len(rows))
    w("FINAL_COHORT_UNIQUE_EVENTS = %d" % len({r["native_event_id"] for r in rows}))
    w("SPORTS_IN_FINAL_COHORT             = %s"
      % sorted({str(r["sport"]) for r in rows}))
    w("PRICE_BANDS_IN_FINAL_COHORT        = %s"
      % sorted({str(r["price_band"]) for r in rows}))
    w("TICK_VALUES_IN_FINAL_COHORT        = %s"
      % sorted({str(r["tick_size"]) for r in rows}))
    w("SPREAD_REGIMES_IN_FINAL_COHORT     = %s"
      % sorted({str(r["spread_bucket"]) for r in rows}))
    w("QUEUE_REGIMES_IN_FINAL_COHORT      = %s"
      % sorted({str(r["queue_regime"]) for r in rows}))
    w("NORMALIZED_STATES_IN_FINAL_COHORT  = %s"
      % sorted({str(r["normalized_state"]) for r in rows}))
    w("")
    w("gate: %s" % gate)
    w("frame availability: %s" % avail)
    w("WALK_STOP_REASON = %s" % stop)
    w("")
    w("=== 8. VERDICTS ===")
    w("GAME_LEVEL_COHORT_DIVERSITY = %s"
      % ("SUFFICIENT" if res.get("gate_passes") else "INSUFFICIENT"))
    w("COHORT_SELECTION_RULE_EXECUTED_IDENTICALLY_IN_TRIAL_AND_FINAL = %s"
      % ("YES" if res.get("selector_identical_trial_and_final") else "NO"))
    w("HTTP_429_COUNT = %d" % n429)
    for e in thr:
        w("   %s" % e)
    w("NOMINAL_RPS = %.2f   ACHIEVED_RPS = %.4f over %.1f s"
      % (NOMINAL_RPS, res.get("achieved_rps") or 0, res.get("elapsed_s") or 0))
    w("VENUE_REQUESTS_SPENT = %s of %d"
      % (res.get("venue_requests_spent"), MAX_VENUE_REQUESTS))
    w("PASSIVE_REALIZABLE_EDGE = NOT_IDENTIFIED")
    w("PMUS_FEES_RESOLVED      = NO")
    w("")
    w("EVIDENCE_INTEGRITY and READY_FOR_MULTI_DAY_PHASE2_CAPTURE are judged by")
    w("the analyst against these bytes, not asserted by the runner.")
    return L


if __name__ == "__main__":
    sys.exit(main())
