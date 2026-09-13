#!/usr/bin/env python3
"""RUN 85 PHASE 2A -- sections E to H, computed from the retrieved raw bytes.

Run separately from the capture, in the analyst's own process, on the archive
the courier committed. Nothing here contacts anything.

THE TWO PAIR CONSTRUCTIONS ARE NEVER POOLED, and one of them is an identity
that should be stated before it is measured rather than presented as a
discovery.

For a binary contract quoted in LONG price terms, buying the short side is
selling the long side. So for ONE market with best bid b and best offer a:

    aggressive pair cost = a + (1 - b) = 1 + spread     always >= 1
    maker     pair cost  = b + (1 - a) = 1 - spread     always <= 1

The within-market maker edge IS the spread, by algebra, not by measurement.
That is the same +2.19c cross Run 84 measured on the global venue, seen from
the other side of the trade. Nothing about whether it can be CAPTURED follows:
capture needs fills, and fills against informed flow are adversely selected.
So this file reports the spread distribution as PASSIVE_BOOK_EDGE and leaves
PASSIVE_REALIZABLE_EDGE at NOT_IDENTIFIED, which is where Phase 2A puts it.

The across-market construction -- resting a bid on each of two outcome markets
of one event -- is NOT an identity. best_bid_A + best_bid_B can sit anywhere,
and only there can the displayed books say something that algebra does not.
It is reported separately and only for events whose structure was verified.

Usage: python3 run85_phase2a_analyze.py <extracted_capture_dir>
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path


def D(x):
    return Decimal(str(x))


def pct(n, d):
    return 0.0 if not d else 100.0 * n / d


def quant(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def best_bid(body):
    lv = body.get("bids") or []
    px = [D(x["px"]) for x in lv if x.get("px") is not None]
    return max(px) if px else None


def best_offer(body):
    lv = body.get("offers") or []
    px = [D(x["px"]) for x in lv if x.get("px") is not None]
    return min(px) if px else None


def depth_usd(levels):
    t = Decimal(0)
    for x in levels or []:
        try:
            t += D(x["px"]) * D(x["qty"])
        except Exception:                             # noqa: BLE001
            pass
    return t


def main(capdir):
    cap = Path(capdir)
    rows = [json.loads(l) for l in (cap / "book_samples.jsonl").open()]
    reqlog = []
    p = cap / "request_log.jsonl"
    if p.exists():
        reqlog = [json.loads(l) for l in p.open()]
    sel = json.loads((cap / "selection.json").read_text())
    R = []

    def say(s=""):
        R.append(s)
        print(s)

    say("RUN 85 PHASE 2A -- SECTIONS E-H")
    say("=" * 68)
    say("evidence dir : %s" % cap.name)
    say("sample rows  : %d   request-log rows: %d" % (len(rows), len(reqlog)))
    say()

    # ---- E. rate / reliability ------------------------------------------
    say("E. RATE AND RELIABILITY")
    allrows = rows + reqlog
    status = Counter(r.get("http_status") for r in allrows)
    errs = Counter(r.get("error") for r in allrows if r.get("error"))
    lat = [r["latency_ms"] for r in allrows if r.get("latency_ms") is not None]
    ok = sum(1 for r in allrows if r.get("http_status") == 200)
    say("   requests attempted : %d" % len(allrows))
    say("   successes (200)    : %d  (%.2f%%)" % (ok, pct(ok, len(allrows))))
    say("   status distribution: %s" % dict(status))
    say("   errors             : %s" % (dict(errs) or "none"))
    say("   429 count          : %d" % status.get(429, 0))
    say("   5xx count          : %d" % sum(v for k, v in status.items()
                                           if isinstance(k, int) and 500 <= k < 600))
    say("   parse failures     : %d" % errs.get("NON_JSON_BODY", 0))
    say("   timeouts           : %d" % sum(v for k, v in errs.items()
                                           if "Timeout" in str(k)))
    if lat:
        say("   latency ms p50=%.1f p90=%.1f p95=%.1f max=%.1f"
            % (quant(lat, .5), quant(lat, .9), quant(lat, .95), max(lat)))
    hdrs = Counter()
    for r in allrows:
        for k in (r.get("response_headers") or {}):
            hdrs[k] += 1
    say("   response headers seen: %s" % dict(hdrs))
    rl = [r.get("response_headers") for r in allrows
          if any("ratelimit" in k or "rate-limit" in k
                 for k in (r.get("response_headers") or {}))]
    if rl:
        say("   RATE LIMIT HEADERS PRESENT -- example: %s" % json.dumps(rl[0]))
    else:
        say("   no rate-limit headers observed.")
    say("   RPS_LIMIT_NOT_ESTABLISHED remains TRUE. The absence of throttling")
    say("   at our self-imposed 2.0/s ceiling is not a documented venue limit,")
    say("   and the rate is not raised on the strength of it.")
    say()

    # ---- F. book quality -------------------------------------------------
    say("F. BOOK QUALITY")
    good = [r for r in rows if r.get("http_status") == 200
            and isinstance(r.get("body"), dict)]
    say("   book responses parsed: %d of %d sample rows" % (len(good), len(rows)))
    if not good:
        say("   NO PARSED BOOKS -- sections F/G/H cannot be computed.")
        (cap / "run85_phase2a_analysis.txt").write_text("\n".join(R) + "\n")
        return 1

    with_bids = sum(1 for r in good if (r["body"].get("bids") or []))
    with_offs = sum(1 for r in good if (r["body"].get("offers") or []))
    with_both = sum(1 for r in good if (r["body"].get("bids") or [])
                    and (r["body"].get("offers") or []))
    say("   %% with bids        : %.2f%% (%d)" % (pct(with_bids, len(good)), with_bids))
    say("   %% with offers      : %.2f%% (%d)" % (pct(with_offs, len(good)), with_offs))
    say("   %% with BOTH        : %.2f%% (%d)" % (pct(with_both, len(good)), with_both))
    say("   market states      : %s" % dict(Counter(r["body"].get("state")
                                                    for r in good)))
    say("   complement classes : %s" % dict(Counter(r.get("complement_class")
                                                    for r in good)))

    spreads, bidD, askD = [], [], []
    for r in good:
        b, a = best_bid(r["body"]), best_offer(r["body"])
        if b is not None and a is not None:
            spreads.append(float(a - b))
        bidD.append(float(depth_usd(r["body"].get("bids"))))
        askD.append(float(depth_usd(r["body"].get("offers"))))
    if spreads:
        say("   spread  n=%d  min=%.4f p25=%.4f median=%.4f p75=%.4f max=%.4f"
            % (len(spreads), min(spreads), quant(spreads, .25),
               statistics.median(spreads), quant(spreads, .75), max(spreads)))
        say("   spread <=0 (crossed or locked): %d"
            % sum(1 for s in spreads if s <= 0))
    say("   bid depth $ median=%.2f p90=%.2f" % (statistics.median(bidD),
                                                 quant(bidD, .9)))
    say("   ask depth $ median=%.2f p90=%.2f" % (statistics.median(askD),
                                                 quant(askD, .9)))
    tt = Counter(bool(r["body"].get("transactTime")) for r in good)
    say("   transactTime present: %s" % dict(tt))
    say()

    # ---- book-change frequency and achieved cadence ---------------------
    say("H. TEMPORAL RESOLUTION")
    by_mkt = defaultdict(list)
    for r in good:
        by_mkt[r["market_slug"]].append(r)
    gaps, changes, samples = [], 0, 0
    for slug, rs in by_mkt.items():
        rs.sort(key=lambda x: x["local_response_monotonic_ns"])
        for a, b in zip(rs, rs[1:]):
            gaps.append((b["local_response_monotonic_ns"]
                         - a["local_response_monotonic_ns"]) / 1e9)
            samples += 1
            if a.get("response_sha256") != b.get("response_sha256"):
                changes += 1
    if gaps:
        say("   achieved per-market re-read gap: median=%.2fs p10=%.2fs p90=%.2fs"
            % (statistics.median(gaps), quant(gaps, .1), quant(gaps, .9)))
    say("   consecutive same-market responses that DIFFER: %d of %d (%.1f%%)"
        % (changes, samples, pct(changes, samples)))
    say()
    med = statistics.median(gaps) if gaps else None
    say("   markout offset reachability, judged against the ACHIEVED gap:")
    for off in (0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60):
        if med is None:
            verdict = "NOT_IDENTIFIED"
        elif off < med:
            verdict = "UNOBSERVABLE_WITH_THIS_CAPTURE"
        else:
            verdict = "OBSERVABLE"
        say("     %-6ss : %s" % (off, verdict))
    say()
    say("   This is a property of the CADENCE, not of REST. A per-market re-read")
    say("   gap of ~%.1f s means every offset below it is unreachable -- including"
        % (med if med is not None else float("nan")))
    say("   1 s and 2 s, not merely the sub-second ones. Phase 2 can buy shorter")
    say("   offsets by sampling fewer markets more often, at the same 2.0/s")
    say("   ceiling; that is a calibration decision this run exists to inform.")
    say()

    # ---- G. first passive economic screen -------------------------------
    say("G. FIRST PASSIVE ECONOMIC SCREEN (displayed books only)")
    say()
    say("   G1. WITHIN-MARKET pair (long + short of ONE binary contract).")
    say("       maker pair cost = 1 - spread, aggressive = 1 + spread. This is")
    say("       algebra, not a measurement: buying the short side IS selling the")
    say("       long side. The measured quantity is therefore the spread itself.")
    verified = [r for r in good
                if r.get("complement_class") == "BINARY_COMPLEMENT_VERIFIED"]
    vsp = []
    for r in verified:
        b, a = best_bid(r["body"]), best_offer(r["body"])
        if b is not None and a is not None:
            vsp.append(float(a - b))
    if vsp:
        say("       verified-binary observations : %d" % len(vsp))
        say("       maker pair cost   median=%.4f  (1 - spread)"
            % (1 - statistics.median(vsp)))
        say("       aggressive cost   median=%.4f  (1 + spread)"
            % (1 + statistics.median(vsp)))
        say("       spread            median=%.4f  p25=%.4f p75=%.4f"
            % (statistics.median(vsp), quant(vsp, .25), quant(vsp, .75)))
        say("       PASSIVE_BOOK_EDGE (gross, per paired $1) = %.4f"
            % statistics.median(vsp))
    else:
        say("       no verified binary complements with a two-sided book.")
    say()
    say("   G2. ACROSS-MARKET pair (two outcome markets of one event).")
    say("       NOT an identity. Reported only for events whose structure was")
    say("       verified, and never pooled with G1.")
    ev = defaultdict(list)
    for r in good:
        if r.get("event_slug"):
            ev[r["event_slug"]].append(r)
    across = 0
    costs = []
    for evslug, rs in ev.items():
        slugs = {x["market_slug"] for x in rs}
        if len(slugs) != 2:
            continue
        by_slug = defaultdict(list)
        for x in rs:
            by_slug[x["market_slug"]].append(x)
        a_s, b_s = sorted(slugs)
        for xa in by_slug[a_s]:
            near = min(by_slug[b_s],
                       key=lambda y: abs(y["local_response_monotonic_ns"]
                                         - xa["local_response_monotonic_ns"]),
                       default=None)
            if near is None:
                continue
            gap_s = abs(near["local_response_monotonic_ns"]
                        - xa["local_response_monotonic_ns"]) / 1e9
            ba, bb = best_bid(xa["body"]), best_bid(near["body"])
            if ba is None or bb is None:
                continue
            across += 1
            costs.append((float(ba + bb), gap_s))
    if costs:
        vals = [c for c, _ in costs]
        say("       across-market observations : %d" % across)
        say("       maker pair quote (bid_A + bid_B) median=%.4f min=%.4f max=%.4f"
            % (statistics.median(vals), min(vals), max(vals)))
        say("       leg gap seconds median=%.2f" % statistics.median(
            [g for _, g in costs]))
        say("       NOTE: a maker pair quote below 1.00 here is only meaningful")
        say("       if the two markets are genuinely exhaustive. Run 84's lesson")
        say("       applies to the leg gap as well.")
    else:
        say("       no two-market events in this sample.")
    say()
    say("   PASSIVE_REALIZABLE_EDGE = NOT_IDENTIFIED")
    say("   No resting order is declared filled anywhere above. Fillability and")
    say("   adverse selection are Phases 4 and 5 and have no evidence yet.")
    say()

    (cap / "run85_phase2a_analysis.txt").write_text("\n".join(R) + "\n")
    print("wrote %s" % (cap / "run85_phase2a_analysis.txt"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
