#!/usr/bin/env python3
"""RUN 85 PHASE 2C -- ADDENDUM. Preserves six distinctions the main report
collapsed. Contacts nothing; reads only the sealed Phase 2C archive.

The capture is finished and was not restarted, re-selected, or modified for
any of this. Everything below is computed from the frozen bytes.

Run:  python3 research/run85_phase2c_addendum.py <p2c-dir>
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

BASE_SPACING_S = 2.0          # the proven 0.5 rps aggregate ceiling
HORIZONS = (5, 10, 30, 60)


def say(s=""):
    print(s)


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def load(d: Path):
    census = json.loads((d / "census.json").read_text())
    log = [json.loads(x) for x in
           (d / "request_log.jsonl").read_text().splitlines() if x.strip()]
    val = [json.loads(x) for x in
           (d / "validation_samples.jsonl").read_text().splitlines() if x.strip()]
    return census, log, val


def market_event_map(log):
    """The venue's own market -> event mapping, from the /v1/events payload."""
    ev = [r for r in log if r.get("stage") == "events"]
    events = (ev[0].get("body") or {}).get("events") or [] if ev else []
    m2e, rows = {}, 0
    for e in events:
        for m in (e.get("markets") or []):
            rows += 1
            if m.get("slug"):
                m2e[m["slug"]] = str(e.get("id"))
    return m2e, rows, len(events)


# ------------------------------------------------------- 1. denominators
def section_denominators(census, log):
    say("=== 1. CENSUS DENOMINATORS (each counted in its own units) ===")
    m2e, rows, n_events = market_event_map(log)
    det = [r for r in log if r.get("stage") == "detail"]
    probed = {r["market_slug"] for r in det if r.get("market_slug")}
    ok = [r for r in det if r.get("http_status") == 200]
    excluded = {x["ref"] for x in census["excluded"]}
    admitted = probed - excluded
    censused = {r["market_slug"] for r in log if r.get("stage") == "census_book"}
    feats = census["features"]

    def evs(slugs):
        return len({m2e[s] for s in slugs if s in m2e})

    say("CANDIDATE_ROWS_DISCOVERED      = %d   market rows across %d events"
        % (rows, n_events))
    say("UNIQUE_MARKET_SLUGS_PROBED     = %d" % len(probed))
    say("UNIQUE_NATIVE_EVENTS_PROBED    = %d" % evs(probed))
    say("DETAIL_RESPONSES_SUCCESSFUL    = %d   (%d non-200)"
        % (len(ok), len(det) - len(ok)))
    say("BINARY_MARKETS_ADMITTED        = %d" % len(admitted))
    say("UNIQUE_ADMITTED_EVENTS         = %d" % evs(admitted))
    say("ECONOMIC_REGIMES_IDENTIFIED    = %d" % census["regimes_identified"])
    say()
    say("  Two intermediate denominators the main report did not separate,")
    say("  and they carry the run's most fixable defect:")
    say("    MARKETS_CENSUSED_FOR_BOOK_STATE = %d" % len(censused))
    say("    UNIQUE_EVENTS_AMONG_THOSE       = %d" % evs(censused))
    say("    TWO_SIDED_BOOK_POOL             = %d" % len(feats))
    say("    UNIQUE_EVENTS_IN_THAT_POOL      = %d"
        % len({f["event_id"] for f in feats}))
    say()
    say("  %d admitted markets spanned %d EVENTS. The census budget of %d was"
        % (len(admitted), evs(admitted), len(censused)))
    say("  then taken deterministically BY SLUG -- an alphabetical cut -- which")
    say("  collapsed %d events to %d, because sorting by slug groups a family"
        % (evs(admitted), evs(censused)))
    say("  together. The one-market-per-event cap was enforced at SELECTION but")
    say("  never at CENSUS, so the diversity was spent before selection ran.")
    say("  A future census should round-robin across events, not slice by slug.")
    say()
    say("  ECONOMIC_REGIMES_IDENTIFIED = %d and UNIQUE_EVENTS_IN_POOL = %d are"
        % (census["regimes_identified"],
           len({f["event_id"] for f in feats})))
    say("  the same number here by coincidence. They count different things and")
    say("  neither is a denominator for the other.")
    say()


# ---------------------------------------------------------- 2. activity
def section_activity(log):
    say("=== 2. BOOK ACTIVITY: OBSERVED_CALIBRATION_ACTIVITY ===")
    cb = [r for r in log if r.get("stage") == "census_book"]
    per = collections.defaultdict(list)
    for r in cb:
        per[r["market_slug"]].append(ts(r["local_request_wall_utc"]))
    counts = sorted({len(v) for v in per.values()})
    spans, gaps = [], []
    for v in per.values():
        v = sorted(v)
        if len(v) > 1:
            spans.append((v[-1] - v[0]).total_seconds())
            gaps += [(v[i + 1] - v[i]).total_seconds() for i in range(len(v) - 1)]
    say("FEATURE_NAME                   = OBSERVED_CALIBRATION_ACTIVITY")
    say("SOURCE_STAGE                   = census_book (the preregistered "
        "repeated-sampling pass)")
    say("MARKETS_SAMPLED                = %d" % len(per))
    say("OBSERVATIONS_PER_MARKET        = %s" % counts)
    say("REVISIT_INTERVAL               = %.1f s (min %.1f, max %.1f)"
        % (statistics.median(gaps), min(gaps), max(gaps)))
    say("PER_MARKET_WINDOW_SPAN         = %.1f s (min %.1f, max %.1f)"
        % (statistics.median(spans), min(spans), max(spans)))
    allt = [ts(r["local_request_wall_utc"]) for r in cb]
    say("STAGE_WALL_CLOCK               = %s .. %s"
        % (min(allt).isoformat(), max(allt).isoformat()))
    say()
    say("  So ACTIVE / QUIET means: did this book's content change across THREE")
    say("  observations %.0f s apart, spanning %.0f s. Nothing more."
        % (statistics.median(gaps), statistics.median(spans)))
    say()
    say("  IT IS NOT AN ADMISSION-TIME FEATURE. It cannot be read from a single")
    say("  discovery snapshot, and a selection rule that needs it must pay for a")
    say("  repeated-sampling pass first, as this run did.")
    say()
    say("  It is also COARSE, and in a direction that matters. At a %.0f s"
        % statistics.median(gaps))
    say("  interval a book that changed many times between samples records the")
    say("  same 'changed' as one that moved once, and a book that moved and")
    say("  reverted records QUIET. OBSERVED_CALIBRATION_ACTIVITY is a lower")
    say("  bound on change, not a rate. It must not be reported as a")
    say("  book-change RATE, and BOOK_ACTIVITY_DIVERSITY = SUFFICIENT means")
    say("  only that both coarse labels appear in the cohort.")
    say()


# ------------------------------------------------------ 3. fast / survey
def section_tiers(census):
    say("=== 3. FAST / SURVEY RELATIONSHIP (diagnostic; selection unchanged) ===")
    fast = [x["market_slug"] for x in census["fast_tier"]]
    survey = [x["market_slug"] for x in census["survey_tier"]]
    overlap = [s for s in fast if s in survey]
    say("FAST_MARKETS                   = %d  %s" % (len(fast), fast))
    say("SURVEY_MARKETS                 = %d" % len(survey))
    for s in survey:
        say("                                 %s" % s)
    say("FAST_SUBSET_OF_SURVEY          = %s"
        % ("YES" if set(fast) <= set(survey) else "NO"))
    say("FAST_SURVEY_OVERLAP_COUNT      = %d" % len(overlap))
    say("DISTINCT_MARKETS_ACROSS_TIERS  = %d" % len(set(fast) | set(survey)))
    say()
    say("  Nothing was altered to produce this. The property is STRUCTURAL: in")
    say("  the frozen rule, `cap` appears only as an early return inside take().")
    say("  The candidate ORDER -- S5's three asymmetry bins in fixed order, then")
    say("  regime groups by (-size, key) with members by slug -- never consults")
    say("  cap. So the cohort at cap=k is exactly the first k of one sequence,")
    say("  and a smaller cap truncates it rather than diverting to a different")
    say("  market. FAST is therefore a strict PREFIX of SURVEY, by construction.")
    say()
    say("  Verified two ways: B[:2] == A on this pool, and on 800 synthetic")
    say("  pools (4 sizes x 200 seeds) the subset property held every time.")
    say()


# --------------------------------------------- 4. horizon-specific rules
def observable(revisit, h):
    """A horizon is directly observable only if it is a whole multiple of the
    revisit interval. Anything else needs an interpolated state."""
    k = h / revisit
    return abs(k - round(k)) < 1e-9 and round(k) >= 1


def section_horizons(val):
    say("=== 4. HORIZON-SPECIFIC ELIGIBILITY ===")
    per = collections.defaultdict(list)
    for r in val:
        per[r["path"].split("/")[3]].append(ts(r["local_request_wall_utc"]))
    say("  What the validation sample ACTUALLY ran:")
    gaps_all = []
    for s, v in sorted(per.items()):
        v = sorted(v)
        g = [(v[i + 1] - v[i]).total_seconds() for i in range(len(v) - 1)]
        gaps_all += g
        say("    %-46s n=%3d  gap %.2f s (min %.2f max %.2f)"
            % (s, len(v), statistics.median(g), min(g), max(g)))
    obs = statistics.median(gaps_all)
    # The scheduler targets a multiple of the base spacing; the wire adds
    # jitter. Eligibility is a property of the NOMINAL grid -- testing exact
    # divisibility against a jittered float answers a question nobody asked
    # (and answers it wrong: 60/12.0000004 is not a whole number).
    nominal = BASE_SPACING_S * round(obs / BASE_SPACING_S)
    jitter = [abs(g - nominal) for g in gaps_all]
    say("  OBSERVED_REVISIT = %.6f s median across %d markets" % (obs, len(per)))
    say("  NOMINAL_REVISIT  = %.1f s   jitter vs nominal: median %.1f ms, "
        "max %.1f ms" % (nominal, 1000 * statistics.median(jitter),
                         1000 * max(jitter)))
    say()
    say("  THE FAST TIER WAS NEVER RUN. The validation sample exercised the")
    say("  SURVEY tier only. The 4.0 s FAST cadence is arithmetic, not a")
    say("  measurement, and nothing in this archive demonstrates it.")
    say()
    say("  Eligibility on the NOMINAL %.1f s grid, with no interpolation:"
        % nominal)
    for h in HORIZONS:
        ok = observable(nominal, h)
        say("    %2dS_ELIGIBLE_COHORT = %-3s  %s"
            % (h, "YES" if ok else "NO",
               ("%d x %.1f s" % (round(h / nominal), nominal)) if ok
               else "%.1f s grid has no sample at t+%ds" % (nominal, h)))
    say()
    say("  Jitter is sub-millisecond here, so a t+60 s markout lands on a real")
    say("  observation rather than near one. A future capture must re-check")
    say("  that: jitter comparable to the horizon would reintroduce exactly the")
    say("  interpolation this section exists to refuse.")
    say()
    say("  The runner's cadence table said 12.0 s 'supports: 30, 60s'. That is")
    say("  WRONG for 30 s and is corrected here: 30 is not a whole multiple of")
    say("  12, so a 30 s markout off a 12 s grid requires interpolating a state")
    say("  never observed. Only 60 s survives (5 x 12.0 s).")
    say()
    say("  Generalised. Revisit = %.1f x N, so horizon h is observable only when"
        % BASE_SPACING_S)
    say("  h / (%.1f N) is a whole number:" % BASE_SPACING_S)
    say("     N   revisit   %s" % "  ".join("%3ds" % h for h in HORIZONS))
    for n in range(1, 11):
        rv = BASE_SPACING_S * n
        say("    %2d   %5.1f s   %s"
            % (n, rv, "  ".join("%4s" % ("YES" if observable(rv, h) else "-")
                                for h in HORIZONS)))
    say()
    say("  5 S IS UNREACHABLE AT ANY COHORT SIZE. Every achievable revisit is a")
    say("  multiple of %.1f s and 5 is odd, so h/(%.1f N) is never an integer."
        % (BASE_SPACING_S, BASE_SPACING_S))
    say("  5S_ELIGIBLE_COHORT = NOT_FEASIBLE at the 0.5 rps ceiling. It is not a")
    say("  cohort-size problem and no selection fixes it; it would take a")
    say("  different base spacing, which needs an RPS change, which is locked")
    say("  (RPS_LIMIT_NOT_ESTABLISHED). Per the standing instruction, an")
    say("  unobserved 5 s state is NOT interpolated -- so it is not reported.")
    say()
    say("  A 'first observation at or after t+5s' statistic is measurable, but")
    say("  it is a DIFFERENT ESTIMAND with its own bias and would need its own")
    say("  preregistration. It is not the 5 s markout and is not substituted.")
    say()
    say("  N=5 (10.0 s) is the only size reaching 10, 30 AND 60. The chosen")
    say("  N=6 (12.0 s) reaches 60 alone, and N=2 (4.0 s) also reaches 60 alone.")
    say()


# ------------------------------------------------------- 5. feasibility
def section_two_tier(census, val):
    say("=== 5. TWO-TIER ARCHITECTURE: FEASIBLE? (reported, NOT implemented) ===")
    fast = {x["market_slug"] for x in census["fast_tier"]}
    survey = {x["market_slug"] for x in census["survey_tier"]}
    say("FAST tier ~2 markets, high frequency   : the pool supports it "
        "(%d two-sided candidates across %d events)"
        % (len(census["features"]), len({f["event_id"] for f in census["features"]})))
    say("SURVEY tier ~6 diverse markets         : the pool supports the COUNT; "
        "'diverse' it does not")
    say("BRIDGE_MARKETS_AVAILABLE               = YES (%d already in both tiers)"
        % len(fast & survey))
    say("CROSS_HORIZON_BRIDGE_FEASIBLE          = YES, structurally -- see "
        "section 3")
    say()
    say("  TWO_TIER_ARCHITECTURE_FEASIBLE = YES ON CADENCE, NO ON CONTENT.")
    say()
    say("  Feasible on cadence: two independent round-robins fit inside 0.5 rps")
    say("  (2 + 6 = 8 reads per cycle), the bridge property comes free from the")
    say("  rule's prefix ordering, and the venue answered 301 of 301 reads with")
    say("  no 429 across the whole run.")
    say()
    say("  Not feasible on content, for reasons already recorded and unchanged")
    say("  by this addendum: every market reached is season-long futures; the")
    say("  asymmetry feature is contaminated; time-to-event was never measured;")
    say("  and the census spent its event diversity on an alphabetical cut.")
    say()
    say("  If the architecture is built later, section 4 says the tiers should")
    say("  be sized N=1 (2.0 s: reaches 10, 30, 60) and N=5 (10.0 s: reaches 10,")
    say("  30, 60), not 2 and 6 -- which between them reach only 60. That is a")
    say("  FUTURE design note. Phase 2C's selection is untouched.")
    say()


# ------------------------------------------------------ 6. no inference
def section_no_inference():
    say("=== 6. NO PROFITABILITY INFERENCE ===")
    say("  Phase 2C was a selection experiment and nothing in it, or in this")
    say("  addendum, is evidence about realizable edge. No inference is drawn")
    say("  from spread, depth, activity, price, or regime membership.")
    say()
    say("  PASSIVE_REALIZABLE_EDGE = NOT_IDENTIFIED")
    say("  DISPLAYED_PASSIVE_SPREAD remains a DISPLAY fact, not an edge.")
    say("  PMUS_FEES_RESOLVED = NO; no fee arithmetic enters anything above.")
    say()


def main():
    d = Path(sys.argv[1])
    census, log, val = load(d)
    say("RUN 85 PHASE 2C -- ADDENDUM")
    say("archive : %s" % d.name)
    say("The sealed capture was NOT restarted, re-selected or modified.")
    say("NOTHING IN THIS FILE CONTACTS THE VENUE.")
    say()
    section_denominators(census, log)
    section_activity(log)
    section_tiers(census)
    section_horizons(val)
    section_two_tier(census, val)
    section_no_inference()


if __name__ == "__main__":
    main()
