#!/usr/bin/env python3
"""RUN 82B -- TIMING IDENTIFIABILITY MAP.

    python3 research/gen/run82b.py

NO DATABASE. Sealed artifacts only.

THE QUESTION. What can historical retained data actually tell us about the
elapsed time between RN1's fill and BETTOR's first observable / actionable
state?

The answer is decided by CLOCK DOMAINS, not by arithmetic. Subtracting two
timestamps always yields a number; it yields an ELAPSED TIME only when both
timestamps came from the same clock, or from clocks whose offset was measured
and retained. Where that does not hold, this file refuses to print seconds as
though they meant seconds.

CLOCK PROVENANCE, TRACED FROM THE WRITE SITES IN THIS REPOSITORY -- read from
the code, not assumed, and cited so it can be re-checked:

  trades.ts
    chain  -- REMOTE. Polygon block timestamp via eth_getBlockByNumber,
              ingestion/chain.py:642. BUT THAT LINE HAS A SILENT FALLBACK:
              int(str(result.get("timestamp", hex(int(time.time())))), 16).
              If the RPC returns 200 with no timestamp (an unsynced or
              eventually-consistent node), THE LOCAL WALL CLOCK is substituted
              and stored as if it were block time. Nothing in the retained row
              distinguishes such a row from a real one.
    poll   -- REMOTE. raw["timestamp"] of the Data-API /trades row,
              ingestion/poller.py:107, defaulting to 0 when the field is
              absent (which would store 1970-01-01).
    s1     -- REMOTE, STRICT. Polygon block timestamp, hash-verified against
              the block hash and parentHash, with NO wall-clock fallback:
              ingestion/s1_emitter.py:1081, gate at :1049-1079.

  trades.detected_at
    all lanes -- LOCAL PYTHON WALL CLOCK, datetime.now(tz=utc),
              ingestion/pipeline.py:128. For the poll lane there are TWO
              writers -- the live poller (poller.py:457) and the missed-fill
              reconciler (reconciler.py:748) -- and the retained row does not
              say which one stamped it.

  copy_probes.probe_at
    all lanes -- LOCAL PYTHON WALL CLOCK, datetime.now(tz=utc),
              copy_probe.py:110, stamped in the same process that performed
              the ingest (the probe is a task spawned from pipeline.py:274).
              The column's SQL DEFAULT now() is a Postgres clock but is never
              reached, because the writer always binds the value explicitly.

  copy_probes.reaction_s
    all lanes -- CROSS-DOMAIN BY CONSTRUCTION, copy_probe.py:135:
              (probe_at - fill_dt), i.e. LOCAL WALL minus REMOTE CLOCK.
              It is not probe_at - detected_at, and no monotonic clock appears
              anywhere in the path.

CONSEQUENCE, AND IT IS THE WHOLE FINDING: every boundary that starts at the
source fill crosses a clock domain, in EVERY lane. The one interval that does
not is detected_at -> probe_at, which is local wall minus local wall in the
same process -- and that interval measures BETTOR's own internal handling only.
It says nothing about how long the fill took to reach us.

U2 IS ALSO A CENSORED SAMPLE ON THE VERY QUANTITY IN QUESTION. A probe row
exists only when (a) side == "BUY" (copy_probe.py:100), (b) the fill was fresher
than 600 s at ingest (pipeline.py:274), and (c) the computed detection latency
was <= MAX_REACTION_S = 120 s (copy_probe.py:38, :104). So U2 cannot contain a
slow detection even if one occurred, and no distribution drawn from it may be
read as the distribution of BETTOR's detection latency.

NAMING. Where an association is measured it is called
ASSOCIATION BETWEEN RETAINED ELAPSED TIME AND PRICE DETERIORATION.
It is never called a latency cost. No causal identification exists.
"""
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run81a_s import pct, stratum  # noqa: E402
from run81b_population_gate import SealedInputs, verify_sealed_hashes  # noqa: E402

LANES = ["A_CHAIN", "A_POLL", "A_S1", "A_BACKFILL_DIAGNOSTIC"]

# Pre-specified BEFORE any economics were computed, exactly as ordered.
BUCKETS = [("0-1s", 0.0, 1.0), ("1-2s", 1.0, 2.0), ("2-5s", 2.0, 5.0),
           ("5-10s", 5.0, 10.0), ("10-30s", 10.0, 30.0),
           ("30-60s", 30.0, 60.0), (">60s", 60.0, float("inf"))]

SEMANTICS = {
    "A_CHAIN": {
        "ts": "REMOTE Polygon block timestamp (chain.py:642) -- WITH A SILENT "
              "LOCAL WALL-CLOCK FALLBACK on an empty RPC result; provenance is "
              "NOT SINGLE-VALUED and the retained row cannot tell the cases "
              "apart",
        "det": "LOCAL Python wall clock (pipeline.py:128)",
        "probe": "LOCAL Python wall clock, same process (copy_probe.py:110)",
    },
    "A_POLL": {
        "ts": "REMOTE Data-API trade timestamp (poller.py:107), integer "
              "seconds, default 0 when absent",
        "det": "LOCAL Python wall clock (pipeline.py:128) -- TWO WRITERS, the "
               "live poller and the missed-fill reconciler, indistinguishable "
               "in the retained row",
        "probe": "LOCAL Python wall clock, same process (copy_probe.py:110)",
    },
    "A_S1": {
        "ts": "REMOTE Polygon block timestamp, HASH-VERIFIED, NO wall-clock "
              "fallback (s1_emitter.py:1081, gate :1049-1079) -- the strictest "
              "ts provenance in the tree",
        "det": "LOCAL Python wall clock (pipeline.py:128)",
        "probe": "LOCAL Python wall clock, same process (copy_probe.py:110)",
    },
    "A_BACKFILL_DIAGNOSTIC": {
        "ts": "REMOTE Data-API activity timestamp (same parser as poll)",
        "det": "LOCAL wall clock stamped ONCE per whale for the whole backfill "
               "(history.py:91) -- not a per-row reading; and migration "
               "003_backfill_source.sql retroactively relabels poll rows as "
               "backfill, so the lane is not even a single ingestion path",
        "probe": "LOCAL Python wall clock, same process (copy_probe.py:110)",
    },
}


def parse(ts):
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f%z")


def stats(vals):
    if not vals:
        return None
    return {"n": len(vals), "mean": sum(vals) / len(vals),
            "p01": pct(vals, 0.01), "p10": pct(vals, 0.10),
            "p25": pct(vals, 0.25), "p50": pct(vals, 0.50),
            "p75": pct(vals, 0.75), "p90": pct(vals, 0.90),
            "p99": pct(vals, 0.99),
            "min": min(vals), "max": max(vals),
            "neg": sum(1 for v in vals if v < 0)}


def row(label, s):
    if s is None:
        return f"  {label:<34}{'NOT TESTED -- zero witnesses':>40}"
    return (f"  {label:<34}{s['n']:>9,}{s['mean']:>11.3f}{s['p01']:>10.3f}"
            f"{s['p10']:>10.3f}{s['p50']:>10.3f}{s['p90']:>10.3f}"
            f"{s['p99']:>10.3f}{s['min']:>12.3f}{s['max']:>12.3f}"
            f"{100.0 * s['neg'] / s['n']:>9.2f}%")


def main():
    print("=" * 78)
    print("RUN 82B -- TIMING IDENTIFIABILITY MAP")
    print("=" * 78)
    print("Sealed bytes only. No database. No seconds are printed as elapsed")
    print("time across an unresolved clock boundary. mirror_live=false.\n")

    hashes = verify_sealed_hashes()
    print("== SEALED HASH IDENTITY ==")
    for name, _e, _g, n, ok in hashes:
        print(f"  {name:<24}{n:>10,} lines  "
              f"{'MATCH' if ok else '*** MISMATCH ***'}")
    if not all(ok for *_, ok in hashes):
        print("\n== HALT -- sealed hash failure ==")
        return 1
    print()

    sealed = SealedInputs()
    lane_n = defaultdict(int)
    lane_no = defaultdict(lambda: Decimal("0"))
    d_ts = defaultdict(list)      # detected_at - ts   CROSS-DOMAIN
    d_probe = defaultdict(list)   # probe_at - detected_at  SAME DOMAIN
    d_full = defaultdict(list)    # probe_at - ts      CROSS-DOMAIN
    react_resid = []              # stored reaction_s vs recomputed probe_at-ts
    rows_by_lane = defaultdict(list)

    for r, _bucket, _S in sealed.events():
        lane = stratum(r["source"])
        lane_n[lane] += 1
        lane_no[lane] += Decimal(r["notional"])
        ts = parse(r["ts"])
        det = parse(r["detected_at"])
        pr = parse(r["probe_at"])
        a = (det - ts).total_seconds()
        b = (pr - det).total_seconds()
        c = (pr - ts).total_seconds()
        d_ts[lane].append(a)
        d_probe[lane].append(b)
        d_full[lane].append(c)
        if r.get("reaction_s") is not None:
            react_resid.append(abs(float(r["reaction_s"]) - c))
        rows_by_lane[lane].append((b, r))

    order = [l for l in LANES if l in lane_n]
    total_n = sum(lane_n.values())

    # ------------------------------------------------- the identifiability map
    print("=" * 78)
    print("LANE-BY-LANE IDENTIFIABILITY")
    print("=" * 78)
    for lane in order:
        sem = SEMANTICS[lane]
        print(f"-- {lane} --")
        print(f"  U2 event count            {lane_n[lane]:>12,}")
        print(f"  % of U2                   "
              f"{100.0 * lane_n[lane] / total_n:>11.3f}%")
        print(f"  source notional           "
              f"{lane_no[lane].quantize(Decimal('0.01')):>12,}")
        print(f"  source ts semantics       {sem['ts']}")
        print(f"  detection ts semantics    {sem['det']}")
        print(f"  probe ts semantics        {sem['probe']}")
        print("  source -> detection elapsed valid?   NO -- REMOTE clock minus "
              "LOCAL clock,")
        print("      two domains with no retained synchronisation evidence. The "
              "difference")
        print("      is (true elapsed + unmeasured offset) and the two terms are "
              "not separable.")
        print("  detection -> probe elapsed valid?    YES -- local wall minus "
              "local wall in")
        print("      the SAME process. Caveat: wall, not monotonic, so an NTP "
              "step lands in it.")
        print("      Measures BETTOR-INTERNAL handling only; says nothing about "
              "reaching us.")
        print("  source -> probe elapsed valid?       NO -- same domain crossing "
              "as above.")
        print("      This is the quantity stored as reaction_s.")
        print("  PHYSICAL ACTIONABLE LATENCY IDENTIFIABLE?  NO")
        if lane == "A_CHAIN":
            print("    Reason: the domain crossing, PLUS a second and independent")
            print("    defect -- chain.py:642 silently substitutes the local wall")
            print("    clock for a missing block timestamp, so some rows' ts is")
            print("    not a venue clock at all and no retained field says which.")
        elif lane == "A_POLL":
            print("    Reason: the domain crossing, PLUS detected_at having two")
            print("    possible writers (live poller, missed-fill reconciler) that")
            print("    the row does not distinguish -- a reconciler row's")
            print("    detected_at is a catch-up time, arbitrarily far from ts.")
        elif lane == "A_S1":
            print("    Reason: THE DOMAIN CROSSING ALONE. s1's ts is the strictest")
            print("    in the tree -- hash-verified block time, no wall fallback --")
            print("    and it still fails, because the failure is the boundary")
            print("    between the chain's clock and ours, not the quality of the")
            print("    parse. A better parser cannot fix an unmeasured offset.")
        else:
            print("    Reason: the domain crossing, PLUS detected_at stamped once")
            print("    per whale for an entire backfill rather than per row, PLUS")
            print("    migration 003 relabelling poll rows into this lane.")
        print()

    # ------------------------------------------------------ the distributions
    print("=" * 78)
    print("OBSERVED DIFFERENCES -- REPORTED AS CLOCK DIAGNOSTICS, NOT ELAPSED TIME")
    print("=" * 78)
    print("Seconds below are the arithmetic difference of two stored timestamps.")
    print("For the two CROSS-DOMAIN rows that number is (elapsed + offset) and is")
    print("NOT a latency. It is shown because its SHAPE -- especially negative")
    print("values, which elapsed time cannot take -- measures the disagreement.\n")
    hdr = (f"  {'interval / lane':<34}{'n':>9}{'mean':>11}{'p01':>10}"
           f"{'p10':>10}{'p50':>10}{'p90':>10}{'p99':>10}{'min':>12}"
           f"{'max':>12}{'% < 0':>10}")

    print("-- CROSS-DOMAIN: detected_at - ts  (NOT an elapsed time) --")
    print(hdr)
    for lane in order:
        print(row(lane, stats(d_ts[lane])))
    print("  A negative value means the stored source clock is AHEAD of ours.")
    print("  Elapsed time cannot be negative; clock disagreement can.\n")

    print("-- SAME DOMAIN: probe_at - detected_at  (a valid elapsed time) --")
    print(hdr)
    for lane in order:
        print(row(lane, stats(d_probe[lane])))
    print("  BETTOR-internal handling from ingest to book read. This is the ONLY")
    print("  interval in the retained data that is an elapsed time.\n")

    print("-- CROSS-DOMAIN: probe_at - ts  (the stored reaction_s) --")
    print(hdr)
    for lane in order:
        print(row(lane, stats(d_full[lane])))
    if react_resid:
        print(f"  stored reaction_s vs recomputed probe_at - ts: "
              f"max |difference| = {max(react_resid):.6f} s over "
              f"{len(react_resid):,} rows")
        print("  (a self-consistency check of the sealed columns, not a")
        print("   validation of the quantity's meaning)")
    print()

    print("== CENSORING OF U2, WHICH BOUNDS EVERY DISTRIBUTION ABOVE ==")
    print("  A copy_probes row exists only when all three hold:")
    print("    side == 'BUY'                          copy_probe.py:100")
    print("    fill fresher than 600 s at ingest      pipeline.py:274")
    print("    computed detection latency <= 120 s    copy_probe.py:38, :104")
    print("  So U2 CANNOT CONTAIN a slow detection even if one occurred. These")
    print("  distributions are right-truncated by construction and are not the")
    print("  distribution of BETTOR's detection latency.\n")

    # ------------------------------------------------------- the S1 diagnostic
    print("=" * 78)
    print("S1 DIAGNOSTIC")
    print("=" * 78)
    print("S1_PHYSICAL_LATENCY_ANALYSIS = NOT IDENTIFIED")
    print()
    print("The pre-specified S1 latency-bucket table is NOT RUN, under the rule")
    print("given for it: if any clock boundary is not actually valid, do not run")
    print("the table. Two of the three S1 boundaries are invalid --")
    print("source->detection and source->probe both cross from the Polygon block")
    print("clock to our wall clock with no retained synchronisation evidence.")
    print()
    print("S1's ts is the STRICTEST in the tree: a hash-verified block timestamp")
    print("with no wall-clock fallback. It still fails. That is the point -- the")
    print("obstacle is the domain boundary, not parse quality, so no amount of")
    print("care on the s1 side can recover physical latency from retained data.")
    print()
    s1n = lane_n.get("A_S1", 0)
    print(f"S1 is also small: {s1n:,} U2 events "
          f"({100.0 * s1n / total_n:.3f}% of U2).\n")

    # -------------------------------- association on the ONE valid interval
    print("=" * 78)
    print("ASSOCIATION BETWEEN RETAINED ELAPSED TIME AND PRICE DETERIORATION")
    print("=" * 78)
    print("ON THE ONE VALID INTERVAL ONLY: probe_at - detected_at, BETTOR's own")
    print("internal handling. This is NOT the table that was pre-specified --")
    print("that one needed source-fill boundaries and they are not identifiable.")
    print("It is offered because it is the only elapsed time the retained data")
    print("actually contains, and because it is informative about OUR half.")
    print()
    print("IT IS NOT A LATENCY COST. Even a monotone relationship here would not")
    print("establish causation: price may already have moved because RN1's fill")
    print("itself carries information, because market state changed at the same")
    print("time, because of venue basis, or because slow handling and busy")
    print("markets co-occur. No causal identification exists.")
    print()
    print("Buckets were fixed before any economics were computed.\n")
    print(f"  {'bucket':<10}{'n':>9}{'source notional':>17}{'mean s':>10}"
          f"{'med s':>10}{'mean c/sh':>11}{'med c/sh':>10}{'% pos':>9}"
          f"{'Q_A deteri $':>15}{'det/cost':>10}")
    for name, lo, hi in BUCKETS:
        n = 0
        no = Decimal("0")
        el = []
        moves = []
        pos = 0
        cost = det = 0.0
        for lane in order:
            for b, r in rows_by_lane[lane]:
                if not (lo <= b < hi):
                    continue
                n += 1
                no += Decimal(r["notional"])
                el.append(b)
                ask = r["best_ask"]
                ask = float(ask) if ask is not None else None
                ph = float(r["price"])
                size = float(r["size"])
                if ask is None or not (0 < ask <= 1) \
                   or not (0 < ph < 1) or size <= 0:
                    continue
                mv = (ask - ph) * 100.0
                moves.append(mv)
                if mv > 0:
                    pos += 1
                q = 0.10 * size
                cost += q * ph
                det += q * (ask - ph)
        if n == 0:
            print(f"  {name:<10}{0:>9}{'  NOT TESTED -- zero witnesses':>40}")
            continue
        mv_mean = f"{sum(moves) / len(moves):>11.4f}" if moves else f"{'n/a':>11}"
        mv_med = f"{pct(moves, 0.5):>10.4f}" if moves else f"{'n/a':>10}"
        pos_s = f"{100.0 * pos / len(moves):>8.3f}%" if moves else f"{'n/a':>9}"
        dc = f"{100.0 * det / cost:>9.3f}%" if cost else f"{'n/a':>10}"
        print(f"  {name:<10}{n:>9,}{no.quantize(Decimal('0.01')):>17,}"
              f"{sum(el) / len(el):>10.3f}{pct(el, 0.5):>10.3f}"
              f"{mv_mean}{mv_med}{pos_s}{det:>15,.2f}{dc}")
    print()
    print("  Read the n column before the rate column. A bucket holding a")
    print("  handful of events states nothing about the population, and the")
    print("  censoring above means the slow buckets are not a random sample of")
    print("  slow handling -- they are the slow handling that still beat 120 s.")
    print("  The relationship across buckets is NOT monotone, which is itself")
    print("  worth stating: it is not the shape a clean latency effect makes.")
    print()
    propositions(d_probe, order)
    decision_table()
    instrumentation()
    return 0


def propositions(d_probe, order):
    allv = [v for lane in order for v in d_probe[lane]]
    med = pct(allv, 0.5) if allv else None
    print("=" * 78)
    print("RUN 82 CONCLUSIONS -- LOCKED")
    print("=" * 78)
    print("Each proposition is judged on its own evidence. None is inferred")
    print("from another; that inference is the error this section exists to")
    print("prevent.\n")

    print("A. By first retained observation, economics are materially worse")
    print("   than RN1 source-fill economics.")
    print("   SUPPORTED.")
    print("   81B, Q_A: +$45,437.46 at his fill prices against -$42,856.87 at")
    print("   the first retained ask on the same events -- a change of sign.\n")

    print("B. Settlement selection manufactures the observed price")
    print("   deterioration.")
    print("   CONTRADICTED by the broadly similar deterioration distributions")
    print("   across FULL U2, SETTLEMENT_ANALYZABLE_STRONG and UNRESOLVED.")
    print("   82A: 3.6658% / 3.5707% / 3.6697% of Q_A source cost, with the")
    print("   per-share distributions identical from p10 to p90.")
    print("   DESCRIPTIVE. This does not establish that all forms of selection")
    print("   bias are absent -- only that this one does not manufacture it.\n")

    print("C. BETTOR's measured post-detection internal processing is the")
    print("   primary cause.")
    print("   NOT IDENTIFIED as a causal proposition.")
    print(f"   Observed detection -> probe dispatch is about {med:.3f} s.")
    print("   THE LOCKED NARROW CONCLUSION, AND NOTHING WIDER:")
    print("     BETTOR's measured post-detection probe-dispatch interval is")
    print("     already approximately milliseconds, so that measured segment")
    print("     is not evidence of a multi-second internal-processing")
    print("     bottleneck.")
    print("   That segment is detected_at -> probe dispatch ONLY. It is NOT an")
    print("   end-to-end latency conclusion and must never be converted into")
    print("   one.\n")

    print("D. A true source-fill -> action system operating within 1-2 seconds")
    print("   would recover the economics.")
    print("   NOT IDENTIFIED.")
    print("   source fill -> BETTOR detection crosses unresolved clock domains,")
    print("   so what the economics would have been 1-2 seconds after the TRUE")
    print("   source fill cannot be inferred from retained data.\n")

    print("E. The economics are already gone before BETTOR could possibly")
    print("   observe RN1.")
    print("   NOT IDENTIFIED.")
    print("   This needs BETTOR's first observation dated against the VENUE's")
    print("   clock -- the crossing the retained data does not contain.\n")

    print("F. At first retained observation the deterioration is primarily")
    print("   top-of-book rather than depth at Q_A.")
    print("   SUPPORTED descriptively on the measured population.")
    print("   76.695% top-of-book on the analyzable cohort, 77.228% on FULL U2.")
    print("   The depth share rises with size -- 43.46% at Q_B, 57.20% at the")
    print("   Q_C stress -- so this is a statement about Q_A, not about size in")
    print("   general.\n")


def decision_table():
    rows = [
        ("1. Is first-observation deterioration real?",
         "81B: +$45,437.46 -> -$42,856.87 at Q_A on 112,543 events; per-event "
         "closure 0 violations; two independent implementations agree to the "
         "cent with matching identity digests",
         "SUPPORTED",
         "Already identified. Nothing further needed."),
        ("2. Is it robust to settlement selection?",
         "82A: Q_A deterioration rate 3.5707% on the analyzable cohort vs "
         "3.6658% on FULL U2 and 3.6697% on UNRESOLVED -- events that can never "
         "enter 81B; p10-p90 of the per-share move identical across buckets",
         "SUPPORTED -- BROADLY SIMILAR, the selected cohort is if anything "
         "MILDER than the population it came from",
         "Already identified."),
        ("3. Is it mostly top-of-book or depth?",
         "82A/81B: at Q_A top-of-book is 76.7% of total deterioration on the "
         "analyzable cohort and 77.2% on FULL U2; the depth share rises with "
         "size -- 43.5% at Q_B, 57.2% at the Q_C stress",
         "IDENTIFIED: predominantly top-of-book at copy size; depth becomes "
         "co-dominant only at sizes far above it",
         "Already identified for retained depth. Depth beyond what was stored "
         "remains NOT IDENTIFIED."),
        ("4. Can we identify elapsed physical latency historically?",
         "82B: every source-anchored boundary crosses a clock domain in every "
         "lane; 92.04% of chain detected_at - ts values are NEGATIVE, which "
         "elapsed time cannot be; poll is sweep-shaped with a visible 120 s "
         "censor; s1 is strict and positive but still cross-domain",
         "NO -- NOT IDENTIFIED",
         "Paired timestamps in ONE domain, plus recorded clock-sync quality. "
         "See the instrumentation spec."),
        ("5. Is BETTOR's measured post-detection processing the primary cause?",
         "82B: detection -> probe dispatch has a median of about 7 ms. The "
         "locked narrow reading is that this measured segment is not evidence "
         "of a multi-second internal bottleneck -- NOT that internal handling "
         "is exonerated end to end, since the segment before detection is the "
         "unmeasurable one",
         "NOT IDENTIFIED as a causal proposition",
         "The same instrumentation as (4), plus a market-data snapshot stamped "
         "in our own domain at a known instant."),
        ("6. Would a system acting 1-2 s after the TRUE source fill recover it?",
         "82B: source fill -> BETTOR detection crosses unresolved clock "
         "domains, so the economics 1-2 s after the true fill are not "
         "reconstructible. The ~7 ms figure speaks only to the post-detection "
         "segment and does not transfer to this question",
         "NOT IDENTIFIED",
         "A measured source-fill-to-receipt time, which needs a venue-side "
         "timestamp in a domain we can compare against -- run 83."),
        ("7. Can we say reactive copying is fundamentally too late?",
         "82B: dating our first observation against the venue clock is exactly "
         "the crossing the data does not contain",
         "NOT IDENTIFIED",
         "A forward-looking A/B: same signal, two arrival paths, with "
         "one-domain timestamps on both -- plus fills, not just book reads."),
        ("8. What new data would resolve 4-7?",
         "n/a -- this row is the answer to the others",
         "See the instrumentation spec below",
         "n/a"),
    ]
    print("=" * 78)
    print("DECISION TABLE")
    print("=" * 78)
    for q, ev, v, w in rows:
        print(f"QUESTION   {q}")
        print(f"EVIDENCE   {ev}")
        print(f"VERDICT    {v}")
        print(f"IDENTIFY   {w}")
        print("-" * 78)
    print()


def instrumentation():
    print("=" * 78)
    print("MINIMUM FORWARD-LOOKING INSTRUMENTATION")
    print("=" * 78)
    print("Historical data cannot identify physical latency, so the only route")
    print("is to record it going forward. THIS IS A SPECIFICATION, NOT A CHANGE:")
    print("nothing here is built, and collecting it must not be a reason to")
    print("activate trading. That requires separate approval.\n")
    print("Per copied source event, persisted immutably, one row, never updated:")
    for i, f in enumerate([
        "source event / fill id",
        "source venue timestamp",
        "source timestamp provenance / clock domain  <- the field whose absence "
        "is the whole of this run's negative result",
        "BETTOR receipt timestamp from a MONOTONIC local clock",
        "normalization-complete timestamp",
        "decision timestamp",
        "market-data request / send timestamp",
        "market-data response timestamp",
        "exact BBO / depth snapshot timestamp",
        "order submit timestamp",
        "venue acknowledgement timestamp",
        "first fill timestamp",
        "complete fill timestamp",
        "cancel / replace timestamps",
        "venue execution ids",
        "superseding command id",
        "market / token mapping provenance",
        "cross-venue mapping id",
        "all prices, sizes and fee / rebate fields available",
    ], 1):
        print(f"  {i:>2}. {f}")
    print()
    print("Three rules that make the difference between this and what exists:")
    print()
    print("  WALL AND MONOTONIC ARE SEPARATE FIELDS. Every interval that is to")
    print("  be read as elapsed time is computed from monotonic readings taken")
    print("  in one process. Wall clock is retained for correlation only. The")
    print("  current tree has no monotonic clock in this path at all.")
    print()
    print("  CLOCK SYNCHRONISATION QUALITY IS RECORDED, NOT ASSUMED. Offset and")
    print("  dispersion against the reference at the moment of each event. An")
    print("  unmeasured offset is precisely what makes 92% of the chain lane's")
    print("  differences negative today, and no analysis can repair it after the")
    print("  fact.")
    print()
    print("  PROVENANCE TRAVELS WITH THE VALUE. A timestamp is stored beside the")
    print("  name of the clock that produced it. chain.py:642's silent fallback")
    print("  to the local wall clock is invisible today only because no field")
    print("  records which clock the value came from.")
    print()
    print("One more, which is not a field: the censoring must be recorded too.")
    print("Today a probe exists only for BUY fills under 120 s, so the slow tail")
    print("is not merely rare in the data -- it is absent by construction, and")
    print("nothing in the data says so. Whatever is dropped should be counted.")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
