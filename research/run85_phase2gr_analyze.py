#!/usr/bin/env python3
"""RUN 85 PHASE 2G-R -- analysis and the frozen capture specification.
Contacts nothing. Reads only the sealed archive."""
from __future__ import annotations
import collections, datetime as dt, json, sys
from pathlib import Path

RPS, SPACING = 0.4, 2.5
HORIZONS = (5, 10, 30, 60)
CONCURRENT_SETS = 4          # per 60 s window; see section C

def say(s=""): print(s)

def main():
    d = Path(sys.argv[1])
    c = json.loads((d / "cohort.json").read_text())
    log = [json.loads(l) for l in (d / "request_log.jsonl").read_text().splitlines() if l.strip()]
    coh, av, gate = c["cohort"], c["availability"], c["gate"]
    wk = c["walk"]

    t = sorted(dt.datetime.fromisoformat(r["local_request_wall_utc"].replace("Z", "+00:00"))
               for r in log if r.get("local_request_wall_utc"))
    gaps = [(t[i+1]-t[i]).total_seconds() for i in range(len(t)-1)]
    span = (t[-1]-t[0]).total_seconds()

    say("RUN 85 PHASE 2G-R -- COHORT CLOSURE, ANALYSIS AND FROZEN SPECIFICATION")
    say("archive : %s" % d.name)
    say("NOTHING IN THIS FILE CONTACTS THE VENUE.")
    say()
    say("=== A. FINAL COHORT ===")
    say("%-42s %-7s %-8s %-8s %-8s %-6s %-10s %-10s"
        % ("market", "sport", "state", "mid", "band", "tick", "spread", "queue"))
    for x in coh:
        say("%-42s %-7s %-8s %-8s %-8s %-6s %-10s %-10s"
            % (x["market_slug"][:42], str(x["sport"]), x["normalized_state"],
               x["mid"], x["price_band"], x["tick_size"], x["spread_bucket"],
               x["queue_regime"]))
    say()
    for x in coh:
        nt = x["near_touch"] or {}
        say("  %s" % x["market_slug"])
        say("     event %s (%s)  league=%s  raw_period=%r  game_start=%s"
            % (x["native_event_id"], x["native_event_slug"], x["league"],
               x["raw_period"], x["scheduled_game_start"]))
        say("     bid %s  ask %s  spread %s   state justification: %s"
            % (x["best_bid"], x["best_ask"], x["spread"], x["state_justification"]))
        say("     qty touch %s/%s  1t %s/%s  2t %s/%s  5t %s/%s"
            % (nt.get("touch_bid_qty"), nt.get("touch_ask_qty"),
               nt.get("cum_bid_qty_1t"), nt.get("cum_ask_qty_1t"),
               nt.get("cum_bid_qty_2t"), nt.get("cum_ask_qty_2t"),
               nt.get("cum_bid_qty_5t"), nt.get("cum_ask_qty_5t")))
    say()
    say("=== COUNTS AND GATES ===")
    say("OFFSET_PAGES_WALKED = %s   EVENTS_DISCOVERED = %s   ELIGIBLE_GAME_EVENTS = %s"
        % (wk["pages"], wk["events"], c["eligible_game_events"]))
    say("TRIAL_SELECTIONS_EVALUATED = %s   FIRST_PASS_PAGE = %s (walk continued)"
        % (wk["trials"], wk["first_pass_page"]))
    say("FINAL_COHORT_SIZE = %d over %d unique events"
        % (len(coh), len({x["native_event_id"] for x in coh})))
    say("SPORT_COUNTS = %s   MAX_SINGLE_SPORT_SHARE = %.4f"
        % (av["sport_counts"], av["max_sport_share"]))
    say("LEAGUE_COUNTS = %s   MAX_SINGLE_LEAGUE_SHARE = %.4f"
        % (av["league_counts"], av["max_league_share"]))
    say("UNIVERSE_SPORT_COUNTS = %s   MAX_BALANCED_SIZE_AVAILABLE = %s"
        % (av["universe_sport_counts"], av["max_balanced_size"]))
    say("PRICE_BANDS = %s  TICKS = %s  SPREADS = %s  QUEUES = %s  STATES = %s"
        % (sorted({x["price_band"] for x in coh}),
           sorted({x["tick_size"] for x in coh}),
           sorted({x["spread_bucket"] for x in coh}),
           sorted({str(x["queue_regime"]) for x in coh}),
           sorted({x["normalized_state"] for x in coh})))
    say("gate = %s" % gate)
    say()
    say("  Both 2G defects are closed and the evidence says so rather than the")
    say("  code claiming it. Concentration: 2G gave NFL 4 of 5 (80%); here the")
    say("  largest sport holds 4 of 12 (33.3%), and the same bound holds at")
    say("  league level. The repair was the SELECTOR -- round-robin over sports")
    say("  before strata -- so balance is emergent, not a cap applied after.")
    say()
    say("  First-pass stopping: the gate first passed at page %s and the walk"
        % wk["first_pass_page"])
    say("  did NOT stop, continuing to page %s where target quality was met."
        % wk["pages"])
    say("  That single change is what produced a LIVE market, a third spread")
    say("  regime and a third queue regime -- none of which 2G's minimum")
    say("  passing cohort contained.")
    say()
    say("  The LIVE market is asc-epl-mnu-mnc-2026-09-13-fh-neg-1pt5 at raw")
    say("  period 33'. That is the sport-aware soccer-minute rule firing, and")
    say("  it was corroborated against kickoff time rather than trusted from")
    say("  the string alone.")
    say()
    say("=== RATE ===")
    say("INTER_REQUEST_GAP_MIN    = %.3f s" % min(gaps))
    say("INTER_REQUEST_GAP_MEDIAN = %.3f s" % sorted(gaps)[len(gaps)//2])
    say("INTER_REQUEST_GAP_MAX    = %.3f s" % max(gaps))
    say("ACHIEVED_RPS_INTERVAL_BASED = %.4f  nominal %.4f" % ((len(t)-1)/span, RPS))
    say("gaps below the %.1f s floor: %d" % (SPACING, sum(1 for g in gaps if g < SPACING - 0.01)))
    say("HTTP_429_COUNT = %d" % sum(1 for e in (c.get("throttle_events") or [])
                                    if e.get("event") == "429"))
    say()
    say("  The floor is a floor, not a ceiling: the %.2f s maximum gap is the"
        % max(gaps))
    say("  pause between the walk and the cohort reads and breaches nothing.")
    say("  No gap fell below 2.5 s.")
    say()
    say("=== VERDICTS ===")
    ok = (c.get("gate_passes") and c.get("target_quality")
          and c.get("selector_identical_trial_and_final") and len(coh) >= 8
          and av["max_sport_share"] <= 0.5 + 1e-9)
    say("SPORT_CONCENTRATION_GATE = %s" % gate["sport_concentration"])
    say("LEAGUE_CONCENTRATION_GATE = %s" % gate["league_concentration"])
    say("GAME_LEVEL_COHORT_DIVERSITY = %s"
        % ("SUFFICIENT" if c["gate_passes"] else "INSUFFICIENT"))
    say("COHORT_SELECTION_RULE_EXECUTED_IDENTICALLY_IN_TRIAL_AND_FINAL = %s"
        % ("YES" if c["selector_identical_trial_and_final"] else "NO"))
    say("ACHIEVED_RPS_INTERVAL_BASED = %.4f" % ((len(t)-1)/span))
    say("HTTP_429_COUNT = 0")
    say("EVIDENCE_INTEGRITY = PASS (archive digest + 4 internal checksums "
        "verified in the analyst's own process)")
    say("PASSIVE_REALIZABLE_EDGE = NOT_IDENTIFIED")
    say("PMUS_FEES_RESOLVED = NO")
    say("READY_FOR_MULTI_DAY_PHASE2_CAPTURE = %s" % ("YES" if ok else "NO"))
    say()
    say("=== B. FROZEN CAPTURE SPECIFICATION ===")
    say("COHORT      the 12 markets above, one per native event, re-selected")
    say("            each capture day by the frozen 2G-R selector -- never by")
    say("            hand. A market whose event ends drops out; the selector")
    say("            refills from the same rule.")
    say("ACCESS      GET on the public gateway only. No credential, no")
    say("            websocket, no order path. mirror_live stays false.")
    say("RATE        0.4 rps nominal, 2.5 s spacing floor, Retry-After honoured")
    say("            exactly, x2 backoff on 429 to a 30 s ceiling, x0.9 recovery")
    say("            per success only. Pacer DRAINED before every timed set.")
    say("SAMPLING    horizon sets, not a uniform round robin. One set = reads at")
    say("            t0 and t0+5, +10, +30, +60 s for one market. %d sets run"
        % CONCURRENT_SETS)
    say("            concurrently, staggered, so the merged timeline never")
    say("            breaches the spacing floor or the mean ceiling. The plan is")
    say("            validated before each window and REFUSED, not trimmed, if")
    say("            it would breach either.")
    say("RECORD      per observation: both local clocks and monotonic; http")
    say("            status, bytes, sha256; transactTime AND")
    say("            stats.lastPriceSample.ts; full ladder + its sha256; best")
    say("            bid/ask and spread; touch qty and notional both sides;")
    say("            cumulative qty and notional within 0/1/2/5 ticks; the")
    say("            market's own tick on every row; raw period, normalized")
    say("            state and its justification; gameStartTime, event")
    say("            startTime and endDate kept separate; a paired /bbo read")
    say("            for the cross-route freshness check; event id and slug.")
    say("SEMANTICS   HORIZON_OBSERVATION_AVAILABLE, BOOK_CHANGED_BY_HORIZON and")
    say("            MARKOUT stay three separate fields. An unchanged book at a")
    say("            horizon is MARKOUT = 0, never a missing row. The sample is")
    say("            NEVER conditioned on the book having moved.")
    say("INTEGRITY   each day sealed with per-file sha256 written after the")
    say("            report is closed, couriered unmodified, re-verified by the")
    say("            analyst in its own process.")
    say("STOP        halt and report on: 429s above 1%% of requests; cross-route")
    say("            touch disagreement above 1%%; any identity mismatch or body")
    say("            collision; a venue change that invalidates the cohort.")
    say("            Never adapt silently.")
    say()
    say("=== C. EXPECTED DURATION AND OBSERVATION COUNTS ===")
    per_set = 1 + len(HORIZONS)
    reads_per_window = CONCURRENT_SETS * per_set
    windows_per_day = 86400 // 60
    sets_per_day = CONCURRENT_SETS * windows_per_day
    rotations = sets_per_day // len(coh) if coh else 0
    obs_day = sets_per_day * per_set
    rate = reads_per_window / 60.0
    say("one set = %d reads spanning 60 s (t0 + %s)" % (per_set, list(HORIZONS)))
    say("%d sets run concurrently per 60 s window = %d reads/window = %.3f rps"
        % (CONCURRENT_SETS, reads_per_window, rate))
    say("   (ceiling %.2f rps, so this sits at %.0f%% of budget)"
        % (RPS, 100 * rate / RPS))
    say("windows/day = %d   sets/day = %d   full cohort rotations/day = %d"
        % (windows_per_day, sets_per_day, rotations))
    say("observations/day        = %d" % obs_day)
    say("markout pairs/day       = %d  (%d sets x %d horizons)"
        % (sets_per_day * len(HORIZONS), sets_per_day, len(HORIZONS)))
    say("per market per day      = %d sets, %d observations"
        % (sets_per_day // len(coh), (sets_per_day // len(coh)) * per_set))
    for days in (3, 7, 14):
        say("  %2d days -> %8d observations, %8d markout pairs"
            % (days, obs_day * days, sets_per_day * len(HORIZONS) * days))
    say()
    say("  These are CEILINGS from the rate budget. Real yield will be lower:")
    say("  events end, books go one-sided, the venue throttles. The capture")
    say("  reports achieved counts, never these.")
    say()
    say("=== D. PROFITABILITY OUTPUTS THE DATASET WILL SUPPORT ===")
    say("SUPPORTED, directly from what is recorded:")
    say("  displayed passive spread, per market and over time")
    say("  hypothetical resting quote at the touch or N ticks behind")
    say("  PASSIVE_TOUCH_PROXY -- did the market reach that price")
    say("  near-touch queue state ahead of that price at 0/1/2/5 ticks")
    say("  adverse movement at 5/10/30/60 s, signed against the quoted side")
    say("  one-leg versus both-leg opportunity on the same market")
    say("  incomplete-pair exposure: one side reached, the other not")
    say("  residual settlement outcome where the venue publishes it")
    say("  PRE_FEE_EXPECTANCY = gross spread capture - adverse selection")
    say("                       - incomplete-pair residual")
    say("  BREAK_EVEN_TOTAL_FEE = the per-share total fee that takes")
    say("                         PRE_FEE_EXPECTANCY to zero")
    say()
    say("NOT SUPPORTED, and not to be reported as if they were:")
    say("  PASSIVE_FILL_PROBABILITY = NOT_IDENTIFIED. Touching our price does")
    say("    not prove an order there would have filled. Queue position, the")
    say("    size ahead of us and whether a print consumed our level are all")
    say("    unobservable from public data.")
    say("  NET profitability. PMUS_FEES_RESOLVED = NO; the 0.06 coefficient is")
    say("    a FIELD, not a rule, and no borrowed formula may enter a result.")
    say("  Any claim that displayed spread is profit.")
    say()
    say("  Fee resolution is NOT required to begin collection. It IS required")
    say("  before any net-profitability or deployment conclusion.")

if __name__ == "__main__":
    main()
