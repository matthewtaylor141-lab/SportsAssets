#!/usr/bin/env python3
"""RUN 85 PHASE 2G-R -- cohort concentration repair. READ ONLY. NO CREDENTIAL.

Repairs exactly two defects in 2G and changes nothing else.

--------------------------------------------------------------------------
DEFECT 1: THE ANTI-DOMINATION RULE WAS EVALUATED AGAINST THE WRONG NUMBER
--------------------------------------------------------------------------
2G capped a sport at ceil(CAP/2) -- against the TARGET cohort size, not the
size the cohort actually reached. The cohort under-filled to 5 and NFL took
its full 4, so a rule meant to mean "at most half" delivered 80%.

The repair is not a better cap. It is a better SELECTOR. 2G round-robined over
(sport, state, band, spread) strata, which lets a sport with many strata win
many slots. 2G-R round-robins over SPORTS first, and only then over that
sport's strata. Balance stops being a constraint bolted on afterwards and
becomes a property of the order in which slots are handed out: with three
sports and eight slots the split is 3/3/2, and no sport can exceed half unless
the universe itself cannot supply an alternative.

The gate is then evaluated against the FINAL cohort:
    max single-sport share <= 0.50            for size >= 4
    same rule applied to league/family labels
If the eligible universe cannot produce a balanced cohort of at least 4 --
computed, not guessed, by max_balanced_size() -- the gate reports
WAIVED_BY_UNIVERSE_CONCENTRATION rather than passing silently.

--------------------------------------------------------------------------
DEFECT 2: STOPPING AT THE FIRST PASS RETURNS THE MINIMUM PASSING COHORT
--------------------------------------------------------------------------
2G stopped at page 6 of a 40-page bound, the instant select(frame) first
passed. By construction that is the worst cohort that clears the bar. 2G-R
keeps walking until the cohort is TARGET QUALITY -- size >= 8, concentration
passing, and the available regime dimensions covered -- or the hard bound is
reached. First pass is not a stop condition.

--------------------------------------------------------------------------
RATE REPORTING, corrected and locked
--------------------------------------------------------------------------
ACHIEVED_RPS = (N - 1) / (last_request_start - first_request_start).
The prior N/span charged the opening request no preceding interval and printed
0.4389 against a 0.40 nominal, which read as a breach it was not. Gap min,
median and max are reported alongside. The configured rate is unchanged at
0.4 rps nominal, 2.5 s spacing.

--------------------------------------------------------------------------
QUEUE REGIME: MEASURED, NOT WALKED ON -- unchanged from 2G and for the same
reason. Scoring queue inside the per-page trial loop costs a book read per
candidate per page, and selecting on it after measuring it would choose the
cohort by an observed outcome. It is measured on the final cohort and reported
as a result, including when it is thin.
"""
from __future__ import annotations
import argparse, collections, hashlib, importlib.util, json, platform, sys, time
from decimal import Decimal
from pathlib import Path
import httpx

def _load(n, f):
    s = importlib.util.spec_from_file_location(n, Path(__file__).with_name(f))
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

C = _load("run85c", "run85_pmus_collector.py")
B = _load("run85b", "run85_phase2b.py")
E = _load("run85e", "run85_phase2e.py")
F = _load("run85f", "run85_phase2f.py")
G = _load("run85g", "run85_phase2g.py")

PHASE = "run85/phase2gr/1"
NOMINAL_RPS, SPACING_S = 0.4, 2.5
MAX_VENUE_REQUESTS, MAX_OFFSET_PAGES = 140, 40
TARGET_SIZE, COHORT_CAP, MIN_GATE_SIZE = 8, 12, 4
MAX_SHARE = 0.50
EVENTS_PATH, BOOK_PATH = "/v1/events", "/v1/markets/%s/book"

def say(s=""): print(s)

def max_balanced_size(counts):
    """Largest cohort drawable from these per-sport counts with no sport above
    half. Computed, never assumed: if the biggest sport n1 exceeds half the
    total, everything else can only be matched one-for-one."""
    if not counts: return 0
    tot = sum(counts); n1 = max(counts)
    return tot if n1 * 2 <= tot else 2 * (tot - n1)

def league_of(g):
    ls = g.get("league") or []
    return ls[0] if ls else (g.get("sport_bucket") or "UNKNOWN")

def select_cohort(frame, cap=COHORT_CAP):
    """THE selector. Round-robin over SPORTS first, then that sport's strata.
    Deterministic: sports by (-pool size, name); strata by (-size, key);
    events by native id ascending. Never a slug sort. One market per event."""
    by_sport = collections.defaultdict(lambda: collections.defaultdict(list))
    for eid, g in frame.items():
        if not g["sport_bucket"]:
            continue
        c = g["candidates"][0]
        by_sport[g["sport_bucket"]][
            (g["normalized_state"], c["price_band"], c["spread_bucket"])].append(eid)
    for sp in by_sport:
        for k in by_sport[sp]:
            by_sport[sp][k].sort(key=int)
    sports = sorted(by_sport, key=lambda s: (-sum(len(v) for v in by_sport[s].values()), s))
    picks, used = [], set()
    progress = True
    while progress and len(picks) < cap:
        progress = False
        for sp in sports:
            if len(picks) >= cap: break
            strata = by_sport[sp]
            for k in sorted(strata, key=lambda z: (-len(strata[z]), tuple(map(str, z)))):
                taken = False
                while strata[k]:
                    eid = strata[k].pop(0)
                    if eid in used: continue
                    g = frame[eid]; c = g["candidates"][0]
                    picks.append({a: b for a, b in g.items() if a != "candidates"}
                                 | dict(c, stratum=" | ".join(map(str, (sp,) + k)),
                                        league_label=league_of(g)))
                    used.add(eid); taken = True; progress = True; break
                if taken: break
    return picks

def concentration(cohort, key):
    counts = collections.Counter(str(x[key]) for x in cohort)
    if not cohort: return counts, 0.0
    return counts, max(counts.values()) / len(cohort)

def score_cohort(cohort, frame):
    def fvals(fn):
        return {v for v in (fn(g) for g in frame.values() if g["sport_bucket"])
                if v is not None}
    f_sport = fvals(lambda g: g["sport_bucket"])
    f_band = fvals(lambda g: g["candidates"][0]["price_band"])
    f_tick = fvals(lambda g: g["candidates"][0]["tick_size"])
    f_spr = fvals(lambda g: g["candidates"][0]["spread_bucket"])
    f_live = any(g["normalized_state"] == "LIVE" for g in frame.values() if g["sport_bucket"])
    uni = collections.Counter(g["sport_bucket"] for g in frame.values() if g["sport_bucket"])
    mbs = max_balanced_size(list(uni.values()))

    sp_counts, sp_share = concentration(cohort, "sport_bucket")
    lg_counts, lg_share = concentration(cohort, "league_label")
    c_band = {c["price_band"] for c in cohort}
    c_tick = {c["tick_size"] for c in cohort}
    c_spr = {c["spread_bucket"] for c in cohort}
    c_live = any(c["normalized_state"] == "LIVE" for c in cohort)

    res = {}
    if len(cohort) < MIN_GATE_SIZE:
        res["sport_concentration"] = "FAIL"
    elif sp_share <= MAX_SHARE + 1e-9:
        res["sport_concentration"] = "PASS"
    elif mbs < MIN_GATE_SIZE:
        res["sport_concentration"] = "WAIVED_BY_UNIVERSE_CONCENTRATION"
    else:
        res["sport_concentration"] = "FAIL"
    if len(cohort) < MIN_GATE_SIZE:
        res["league_concentration"] = "FAIL"
    elif lg_share <= MAX_SHARE + 1e-9:
        res["league_concentration"] = "PASS"
    elif mbs < MIN_GATE_SIZE:
        res["league_concentration"] = "WAIVED_BY_UNIVERSE_CONCENTRATION"
    else:
        res["league_concentration"] = "FAIL"
    res["sport_count"] = ("PASS" if len({c["sport_bucket"] for c in cohort}) >= 2
                          else "WAIVED_BY_ABSENCE" if len(f_sport) < 2 else "FAIL")
    res["price_band"] = ("PASS" if len(c_band) >= 2
                         else "WAIVED_BY_ABSENCE" if len(f_band) < 2 else "FAIL")
    res["tick"] = ("PASS" if len(c_tick) >= 2
                   else "WAIVED_BY_ABSENCE" if len(f_tick) < 2 else "FAIL")
    res["spread"] = ("PASS" if len(c_spr) >= 2
                     else "WAIVED_BY_ABSENCE" if len(f_spr) < 2 else "FAIL")
    res["live_state"] = ("PASS" if c_live
                         else "WAIVED_BY_ABSENCE" if not f_live else "FAIL")
    res["size"] = "PASS" if len(cohort) >= MIN_GATE_SIZE else "FAIL"
    passes = all(v in ("PASS", "WAIVED_BY_ABSENCE", "WAIVED_BY_UNIVERSE_CONCENTRATION")
                 for v in res.values())
    target = passes and len(cohort) >= TARGET_SIZE
    avail = {"frame_sports": sorted(f_sport), "frame_bands": sorted(f_band),
             "frame_ticks": sorted(f_tick), "frame_spreads": sorted(f_spr),
             "frame_has_live": f_live, "universe_sport_counts": dict(uni),
             "max_balanced_size": mbs,
             "sport_counts": dict(sp_counts), "max_sport_share": sp_share,
             "league_counts": dict(lg_counts), "max_league_share": lg_share}
    return passes, target, res, avail

class Budget:
    def __init__(s, c): s.cap, s.spent = c, 0
    def take(s, n=1):
        if s.spent + n > s.cap: raise RuntimeError("BOUND_REACHED spent=%d" % s.spent)
        s.spent += n

def paced(http, path, pacer, budget, params=None):
    budget.take(); return B.get_paced(http, path, pacer, params)

def walk(http, pacer, budget, log, out):
    say("=== WALK: continue past first pass until TARGET QUALITY ===")
    events, trials, pages = {}, 0, 0
    stop, cohort, gate, avail = "NOT_STARTED", [], {}, {}
    first_pass_page = None
    for k in range(MAX_OFFSET_PAGES):
        params = {"active": "true", "closed": "false", "limit": 100}
        if k: params["offset"] = 100 * k
        try:
            r, rows = paced(http, EVENTS_PATH, pacer, budget, params)
        except RuntimeError as exc:
            stop = str(exc); break
        for x in rows: x["stage"] = "walk"
        log.extend(rows); pages += 1
        got = (r.get("body") or {}).get("events") or []
        for e in got: events.setdefault(str(e.get("id")), e)
        frame = G.eligible(events, time.time())
        cohort = select_cohort(frame)
        trials += 1
        ok, target, gate, avail = score_cohort(cohort, frame)
        if ok and first_pass_page is None:
            first_pass_page = k
        say("p%-2d off=%-5s ev=%4d elig=%3d cohort=%2d sports=%s share=%.2f -> %s%s"
            % (k, params.get("offset", 0), len(events), len(frame), len(cohort),
               sorted({c["sport_bucket"] for c in cohort}),
               avail["max_sport_share"], "PASS" if ok else "no",
               " TARGET" if target else ""))
        if target:
            stop = "TARGET_QUALITY_COHORT_FOUND"; break
        if not got:
            stop = "EMPTY_PAGE"; break
    else:
        stop = "PAGE_BOUND_REACHED"
    say()
    say("WALK_STOP_REASON = %s" % stop)
    if first_pass_page is not None:
        say("first page where the gate passed = %s; the walk did NOT stop there"
            % first_pass_page)
    out["walk"] = {"stop": stop, "pages": pages, "events": len(events),
                   "trials": trials, "first_pass_page": first_pass_page}
    return events, cohort, gate, avail, stop

def measure(http, pacer, budget, log, cohort, out):
    say()
    say("=== FINAL COHORT (queue measured after selection, never selected on) ===")
    rows = []
    for c in cohort:
        try:
            r, lg = paced(http, BOOK_PATH % c["market_slug"], pacer, budget)
        except RuntimeError as exc:
            say("  budget stop: %s" % exc); break
        for x in lg: x["stage"] = "cohort_book"; x["market_slug"] = c["market_slug"]
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
        say("     event %s (%s)  sport=%s league=%s  state=%s (raw %r)  start=%s"
            % (x["native_event_id"], x["native_event_slug"], x["sport"],
               x["league"], x["normalized_state"], x["raw_period"],
               x["scheduled_game_start"]))
        say("     bid %s ask %s mid %s tick %s spread %s (%s) band %s"
            % (x["best_bid"], x["best_ask"], x["mid"], x["tick_size"],
               x["spread"], x["spread_bucket"], x["price_band"]))
        say("     touch %s/%s   1t %s/%s   2t %s/%s   5t %s/%s   queue %s"
            % (nt.get("touch_bid_qty"), nt.get("touch_ask_qty"),
               nt.get("cum_bid_qty_1t"), nt.get("cum_ask_qty_1t"),
               nt.get("cum_bid_qty_2t"), nt.get("cum_ask_qty_2t"),
               nt.get("cum_bid_qty_5t"), nt.get("cum_ask_qty_5t"), x["queue_regime"]))
    out["cohort"] = rows
    return rows

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True)
    a = ap.parse_args(argv); out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    budget = Budget(MAX_VENUE_REQUESTS)
    meta = {"phase": PHASE, "python": sys.version, "platform": platform.platform(),
            "httpx": httpx.__version__, "gateway": C.GATEWAY_BASE,
            "nominal_rps": NOMINAL_RPS, "spacing_s": SPACING_S,
            "target_size": TARGET_SIZE, "cohort_cap": COHORT_CAP,
            "max_single_sport_share": MAX_SHARE,
            "max_venue_requests": MAX_VENUE_REQUESTS,
            "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (out / "runner_metadata.json").write_text(json.dumps(meta, indent=1))
    say(json.dumps(meta, indent=1)); say()
    res, log, rows = {}, [], []
    pacer = B.AdaptivePacer(base=SPACING_S)
    gate, avail, stop = {}, {}, "NOT_RUN"
    with httpx.Client(timeout=30.0) as http:
        try:
            events, cohort, gate, avail, stop = walk(http, pacer, budget, log, res)
            frame = G.eligible(events, time.time())
            final = select_cohort(frame)
            ident = ([c["market_slug"] for c in final] == [c["market_slug"] for c in cohort])
            say("final selection identical to last trial = %s" % ident)
            res["selector_identical_trial_and_final"] = bool(ident)
            res["eligible_game_events"] = len(frame)
            rows = measure(http, pacer, budget, log, final, res)
            ok, target, gate, avail = score_cohort(final, frame)
            res.update({"gate": gate, "availability": avail,
                        "gate_passes": ok, "target_quality": target})
        except RuntimeError as exc:
            say("HARD BOUND: %s" % exc); res["aborted"] = str(exc)
    res.update({"venue_requests_spent": budget.spent, "throttle_events": pacer.events})
    (out / "cohort.json").write_text(json.dumps(res, indent=1, default=str))
    with (out / "request_log.jsonl").open("w") as fh:
        for r in log: fh.write(json.dumps(r, default=str) + "\n")
    B.seal(out, verdicts(res, rows, gate, avail, log))
    okv, bad = B.verify(out)
    say(); say("SEAL_VERIFIED = %s%s" % (okv, "" if okv else " BAD=%s" % bad))
    return 0

def verdicts(res, rows, gate, avail, log):
    L = []
    def w(s=""): L.append(s); say(s)
    import datetime as dt
    ts = sorted(dt.datetime.fromisoformat(r["local_request_wall_utc"].replace("Z", "+00:00"))
                for r in log if r.get("local_request_wall_utc"))
    gaps = [(ts[i+1]-ts[i]).total_seconds() for i in range(len(ts)-1)]
    span = (ts[-1]-ts[0]).total_seconds() if len(ts) > 1 else 0
    thr = res.get("throttle_events") or []
    wk = res.get("walk") or {}
    w("=== 7. REQUIRED OUTPUT ===")
    w("OFFSET_PAGES_WALKED        = %s" % wk.get("pages"))
    w("EVENTS_DISCOVERED          = %s" % wk.get("events"))
    w("ELIGIBLE_GAME_EVENTS       = %s" % res.get("eligible_game_events"))
    w("TRIAL_SELECTIONS_EVALUATED = %s" % wk.get("trials"))
    w("FIRST_PASS_PAGE            = %s (walk did NOT stop there)"
      % wk.get("first_pass_page"))
    w("FINAL_COHORT_SIZE          = %d" % len(rows))
    w("FINAL_COHORT_UNIQUE_EVENTS = %d" % len({r["native_event_id"] for r in rows}))
    w("SPORT_COUNTS               = %s" % avail.get("sport_counts"))
    w("MAX_SINGLE_SPORT_SHARE     = %.4f" % (avail.get("max_sport_share") or 0))
    w("LEAGUE_COUNTS              = %s" % avail.get("league_counts"))
    w("MAX_SINGLE_LEAGUE_SHARE    = %.4f" % (avail.get("max_league_share") or 0))
    w("UNIVERSE_SPORT_COUNTS      = %s" % avail.get("universe_sport_counts"))
    w("MAX_BALANCED_SIZE_AVAILABLE= %s" % avail.get("max_balanced_size"))
    w("PRICE_BANDS       = %s" % sorted({r["price_band"] for r in rows}))
    w("TICK_VALUES       = %s" % sorted({r["tick_size"] for r in rows}))
    w("QUEUE_REGIMES     = %s" % sorted({str(r["queue_regime"]) for r in rows}))
    w("SPREAD_REGIMES    = %s" % sorted({str(r["spread_bucket"]) for r in rows}))
    w("NORMALIZED_STATES = %s" % sorted({r["normalized_state"] for r in rows}))
    w("")
    w("SPORT_CONCENTRATION_GATE  = %s" % gate.get("sport_concentration"))
    w("LEAGUE_CONCENTRATION_GATE = %s" % gate.get("league_concentration"))
    w("full gate: %s" % gate)
    w("")
    w("INTER_REQUEST_GAP_MIN    = %.3f s" % (min(gaps) if gaps else 0))
    w("INTER_REQUEST_GAP_MEDIAN = %.3f s" % (sorted(gaps)[len(gaps)//2] if gaps else 0))
    w("INTER_REQUEST_GAP_MAX    = %.3f s" % (max(gaps) if gaps else 0))
    w("ACHIEVED_RPS_INTERVAL_BASED = %.4f  (N-1)/(last-first), nominal %.4f"
      % (((len(ts)-1)/span) if span else 0, NOMINAL_RPS))
    w("HTTP_429_COUNT = %d" % sum(1 for e in thr if e.get("event") == "429"))
    for e in thr: w("   %s" % e)
    w("VENUE_REQUESTS_SPENT = %s of %d" % (res.get("venue_requests_spent"),
                                           MAX_VENUE_REQUESTS))
    w("")
    w("=== VERDICTS ===")
    w("GAME_LEVEL_COHORT_DIVERSITY = %s"
      % ("SUFFICIENT" if res.get("gate_passes") else "INSUFFICIENT"))
    w("TARGET_QUALITY_REACHED = %s" % ("YES" if res.get("target_quality") else "NO"))
    w("COHORT_SELECTION_RULE_EXECUTED_IDENTICALLY_IN_TRIAL_AND_FINAL = %s"
      % ("YES" if res.get("selector_identical_trial_and_final") else "NO"))
    w("PASSIVE_REALIZABLE_EDGE = NOT_IDENTIFIED")
    w("PMUS_FEES_RESOLVED = NO")
    w("")
    w("EVIDENCE_INTEGRITY and READY_FOR_MULTI_DAY_PHASE2_CAPTURE are judged by")
    w("the analyst against these bytes, not asserted by the runner.")
    return L

if __name__ == "__main__":
    sys.exit(main())
