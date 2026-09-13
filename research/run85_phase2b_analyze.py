#!/usr/bin/env python3
"""RUN 85 PHASE 2B -- analysis, computed from the retrieved raw bytes.

Run in the analyst's own process on the couriered archive. Contacts nothing.

THE ECONOMIC CLAIM THIS FILE IS ALLOWED TO MAKE IS `DISPLAYED_PASSIVE_SPREAD`.
Not PASSIVE_EDGE. The spread is what the book displays; whether any of it is
capturable depends on fillability, adverse selection, residual exposure and the
actual applicable fees, none of which exist as evidence yet.

FEE LANGUAGE IS LOCKED. The venue's own per-market field feeCoefficient = 0.06
is verified. The FORM it is used with -- 0.06 x shares x p x (1-p) -- is
borrowed from a different venue's published schedule and is NOT verified for
PMUS. It may appear only as HYPOTHETICAL_BORROWED_FEE_FORM_SENSITIVITY and
never inside a base figure, a net expectancy, a break-even result or a
deployment verdict.

Usage: python3 run85_phase2b_analyze.py <extracted_capture_dir>
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "p2b", Path(__file__).with_name("run85_phase2b.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


def D(x):
    return Decimal(str(x))


def q(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))]


def pct(n, d):
    return 0.0 if not d else 100.0 * n / d


def dec(x):
    return None if x in (None, "") else D(x)


def main(capdir):
    cap = Path(capdir)
    rows = [json.loads(l) for l in (cap / "book_samples.jsonl").open()] \
        if (cap / "book_samples.jsonl").exists() else []
    req = [json.loads(l) for l in (cap / "request_log.jsonl").open()] \
        if (cap / "request_log.jsonl").exists() else []
    sel = json.loads((cap / "selection.json").read_text()) \
        if (cap / "selection.json").exists() else {}
    R = []

    def say(s=""):
        R.append(s)
        print(s)

    say("RUN 85 PHASE 2B -- ANALYSIS")
    say("=" * 70)
    say("evidence : %s" % cap.name)
    say("samples  : %d   request-log rows: %d" % (len(rows), len(req)))
    say()

    # ---- 1. COHORT SLOT ACCOUNTING --------------------------------------
    say("1. COHORT SLOT ACCOUNTING")
    say("   Labels CANNOT overlap: each slot consumes the chosen market's event,")
    say("   so every selected market carries exactly one label. A count of")
    say("   labels is never a count of markets.")
    say()
    cohort = sel.get("cohort") or []
    dropped = sel.get("dropped_by_cohort_cap") or []
    say("   %-46s %-12s %-14s %s" % ("market_slug", "event_id", "event_slug",
                                     "assigned_regime_labels"))
    for x in cohort + dropped:
        say("   %-46s %-12s %-14s %s%s"
            % (str(x.get("market_slug"))[:46], str(x.get("event_id")),
               str(x.get("event_slug"))[:14],
               x.get("assigned_regime_labels") or [x.get("regime_slot")],
               "  [DROPPED BY COHORT CAP]" if x in dropped else ""))
    labels = [x.get("regime_slot") for x in cohort]
    sampled_slugs = {r.get("market_slug") for r in rows if r.get("market_slug")}
    say()
    say("   UNIQUE_MARKETS_SELECTED       = %d" % len({x.get("market_slug")
                                                       for x in cohort}))
    say("   UNIQUE_EVENTS_SELECTED        = %d" % len({x.get("event_id")
                                                       for x in cohort}))
    say("   REGIME_LABELS_FILLED          = %s" % (", ".join(str(x) for x in labels)
                                                   or "none"))
    say("   REGIME_LABELS_DROPPED         = %s"
        % (", ".join(str(x.get("regime_slot")) for x in dropped) or "none"))
    overlap = (len(labels) != len(set(labels))
               or len({x.get("market_slug") for x in cohort}) != len(cohort))
    say("   REGIME_LABEL_OVERLAP_OCCURRED = %s" % ("YES" if overlap else "NO"))
    say("   markets actually sampled      = %d" % len(sampled_slugs))
    say()

    # ---- 3. TICK SIZE, PER MARKET ---------------------------------------
    say("3. TICK SIZE (per market, from the venue's own field)")
    say("   PHASE2A_TICK_SIZE_VERDICT = RETRACTED")
    say("   SOURCE = PMUS market-detail wire payload, orderPriceMinTickSize")
    ticks = {}
    for x in cohort + dropped:
        ticks[x.get("market_slug")] = x.get("order_price_min_tick_size")
    for k, v in ticks.items():
        say("     %-46s tick=%s" % (str(k)[:46], v))
    distinct_ticks = {str(v) for v in ticks.values() if v is not None}
    say("   distinct tick sizes observed : %s" % (sorted(distinct_ticks) or "none"))
    say("   No generalisation is made beyond the markets listed above.")
    say()

    # ---- 2/12. FEE FIELDS ------------------------------------------------
    say("2. FEE FIELDS (locked language)")
    coefs = {x.get("market_slug"): x.get("fee_coefficient")
             for x in cohort + dropped}
    for k, v in coefs.items():
        say("     %-46s feeCoefficient=%s" % (str(k)[:46], v))
    say("   PMUS_FEE_COEFFICIENT_FIELD          = %s"
        % (sorted({str(v) for v in coefs.values() if v is not None}) or "NOT_SEEN"))
    say("   PMUS_FEE_COEFFICIENT_FIELD_VERIFIED = YES (venue's own per-market field)")
    say("   PMUS_FEE_FORMULA                    = NOT_IDENTIFIED")
    say("   PMUS_MAKER_FEE                      = NOT_IDENTIFIED")
    say("   PMUS_TAKER_FEE                      = NOT_IDENTIFIED")
    say("   PMUS_MAKER_REBATE                   = NOT_IDENTIFIED")
    say("   PMUS_FEES_RESOLVED                  = NO")
    say("   The coefficient is the venue's. The p(1-p) form is not, and does not")
    say("   enter any figure below.")
    say()

    # ---- E. RATE ---------------------------------------------------------
    say("E. RATE AND RELIABILITY")
    allr = rows + req
    status = Counter(r.get("http_status") for r in allr)
    lat = [r["latency_ms"] for r in allr if r.get("latency_ms") is not None]
    ok = status.get(200, 0)
    n429 = status.get(429, 0)
    say("   requests attempted : %d" % len(allr))
    say("   HTTP_SUCCESS_RATE  : %.2f%% (%d)" % (pct(ok, len(allr)), ok))
    say("   HTTP_429_RATE      : %.2f%% (%d)" % (pct(n429, len(allr)), n429))
    say("   status distribution: %s" % dict(status))
    if lat:
        say("   latency ms p50=%.1f p90=%.1f p95=%.1f max=%.1f"
            % (q(lat, .5), q(lat, .9), q(lat, .95), max(lat)))
    tev = json.loads((cap / "throttle_events.json").read_text()) \
        if (cap / "throttle_events.json").exists() else []
    say("   throttle/backoff events recorded: %d" % len(tev))
    if tev:
        say("   retry-after values seen: %s"
            % dict(Counter(str(e.get("retry_after")) for e in tev)))
    say("   RPS_LIMIT_NOT_ESTABLISHED remains TRUE.")
    say()

    # ---- 6/9. CADENCE AND MARKOUT ---------------------------------------
    say("6/9. ACHIEVED CADENCE AND MARKOUT USABILITY")
    good = [r for r in rows if r.get("http_status") == 200 and r.get("view")]
    by_seg = defaultdict(lambda: defaultdict(list))
    for r in good:
        by_seg[r.get("segment")][r.get("market_slug")].append(r)
    seg_gaps = {}
    changes_rows = []
    for seg, per_mkt in by_seg.items():
        gaps = []
        for slug, rs in per_mkt.items():
            rs.sort(key=lambda x: x["local_response_monotonic_ns"])
            for a, b in zip(rs, rs[1:]):
                g = (b["local_response_monotonic_ns"]
                     - a["local_response_monotonic_ns"]) / 1e9
                gaps.append(g)
                va, vb = a["view"], b["view"]
                changes_rows.append({
                    "segment": seg, "market_slug": slug, "gap_s": round(g, 3),
                    "best_bid_before": va.get("best_bid"),
                    "best_bid_after": vb.get("best_bid"),
                    "best_ask_before": va.get("best_ask"),
                    "best_ask_after": vb.get("best_ask"),
                    "mid_before": va.get("mid"), "mid_after": vb.get("mid"),
                    "spread_before": va.get("spread"),
                    "spread_after": vb.get("spread"),
                    "bid_depth_before": va.get("bid_depth_usd"),
                    "bid_depth_after": vb.get("bid_depth_usd"),
                    "ask_depth_before": va.get("ask_depth_usd"),
                    "ask_depth_after": vb.get("ask_depth_usd"),
                    "changed": a.get("response_sha256") != b.get("response_sha256"),
                })
        seg_gaps[seg] = gaps
        if gaps:
            say("   segment %-6s markets=%d revisit p50=%.2fs p90=%.2fs "
                "p95=%.2fs max=%.2fs"
                % (seg, len(per_mkt), q(gaps, .5), q(gaps, .9), q(gaps, .95),
                   max(gaps)))
    say()
    ch = [c for c in changes_rows if c["changed"]]
    say("   consecutive same-market responses that DIFFER: %d of %d (%.1f%%)"
        % (len(ch), len(changes_rows), pct(len(ch), len(changes_rows))))
    say()
    fast_p50 = q(seg_gaps.get("FAST") or [], .5)
    coh_p50 = q(seg_gaps.get("COHORT") or [], .5)
    best_p50 = min([x for x in (fast_p50, coh_p50) if x is not None], default=None)
    say("   markout usability, judged against the ACHIEVED p50 revisit")
    say("   (the best of any segment; interpolation of missing intermediate")
    say("   states is not performed and no offset is claimed from one):")
    for off in (2, 5, 10, 30, 60):
        if best_p50 is None:
            v = "NOT_IDENTIFIED"
        elif off >= best_p50:
            v = "USABLE"
        else:
            v = "NOT_USABLE"
        say("     %-3ss : %s" % (off, v))
    say()

    # ---- 4. DETAIL vs BOOK FRESHNESS ------------------------------------
    say("4. MARKET-DETAIL QUOTE vs BOOK TOP (diagnostic only)")
    det = {}
    for r in req:
        if r.get("stage") == "complement_probe" and r.get("http_status") == 200:
            m = ((r.get("body") or {}).get("market")) or {}
            if m.get("slug"):
                det[m["slug"]] = (r, m)
    comps = []
    for slug, (drow, m) in det.items():
        bb, _ = P.amount(m.get("bestBidQuote"))
        ba, _ = P.amount(m.get("bestAskQuote"))
        if bb is None and ba is None:
            continue
        near = [r for r in good if r.get("market_slug") == slug]
        if not near:
            continue
        b = min(near, key=lambda r: abs(r["local_response_monotonic_ns"]
                                        - drow["local_response_monotonic_ns"]))
        sep = abs(b["local_response_monotonic_ns"]
                  - drow["local_response_monotonic_ns"]) / 1e9
        kbb, kba = dec(b["view"].get("best_bid")), dec(b["view"].get("best_ask"))
        comps.append({"market_slug": slug, "separation_s": round(sep, 3),
                      "detail_bid": str(bb), "book_bid": str(kbb),
                      "detail_ask": str(ba), "book_ask": str(kba),
                      "bid_match": bb == kbb, "ask_match": ba == kba})
    if comps:
        bm = sum(1 for c in comps if c["bid_match"])
        am = sum(1 for c in comps if c["ask_match"])
        both = sum(1 for c in comps if c["bid_match"] and c["ask_match"])
        seps = [c["separation_s"] for c in comps]
        say("   comparisons        : %d" % len(comps))
        say("   bid matches        : %d" % bm)
        say("   ask matches        : %d" % am)
        say("   both match         : %d" % both)
        say("   mismatches         : %d" % (len(comps) - both))
        say("   local separation s : p50=%.1f p90=%.1f min=%.1f max=%.1f"
            % (q(seps, .5), q(seps, .9), min(seps), max(seps)))
        for c in comps:
            say("     %-44s sep=%7.1fs bid %s vs %s (%s)  ask %s vs %s (%s)"
                % (c["market_slug"][:44], c["separation_s"],
                   c["detail_bid"], c["book_bid"], "OK" if c["bid_match"] else "X",
                   c["detail_ask"], c["book_ask"], "OK" if c["ask_match"] else "X"))
        say()
        say("   The separation above is what it is: the detail probe happens in")
        say("   discovery and the books are read afterwards, so this compares")
        say("   readings minutes apart. A mismatch at that separation is not")
        say("   evidence the summary quote is wrong -- it is evidence the two")
        say("   are not interchangeable without a freshness argument, which is")
        say("   exactly the claim this section refuses to make for free.")
    else:
        say("   no comparable pairs (detail quote or book missing)")
    say()

    # ---- 10/11. DISPLAYED SPREAD AND QUEUE ASYMMETRY --------------------
    say("10/11. DISPLAYED SPREAD AND QUEUE ASYMMETRY")
    say("    DISPLAYED_SPREAD_ONLY. No fill is simulated anywhere in this file.")
    say()
    per = defaultdict(list)
    for r in good:
        per[r.get("market_slug")].append(r)
    say("    %-40s %-8s %8s %8s %9s %12s %12s %8s"
        % ("market", "regime", "bid", "ask", "spread", "bid_depth$",
           "ask_depth$", "ratio"))
    for slug, rs in per.items():
        v = [r["view"] for r in rs]
        sp = [float(D(x["spread"])) for x in v if x.get("spread") is not None]
        bb = [float(D(x["best_bid"])) for x in v if x.get("best_bid") is not None]
        ba = [float(D(x["best_ask"])) for x in v if x.get("best_ask") is not None]
        bd = [float(D(x["bid_depth_usd"])) for x in v if x.get("bid_depth_usd")]
        ad = [float(D(x["ask_depth_usd"])) for x in v if x.get("ask_depth_usd")]
        reg = next((r.get("regime_slot") for r in rs if r.get("regime_slot")), "?")
        ratio = (statistics.median(ad) / statistics.median(bd)) \
            if bd and ad and statistics.median(bd) > 0 else None
        say("    %-40s %-8s %8s %8s %9s %12s %12s %8s"
            % (str(slug)[:40], str(reg)[:8],
               "%.4f" % statistics.median(bb) if bb else "-",
               "%.4f" % statistics.median(ba) if ba else "-",
               "%.4f" % statistics.median(sp) if sp else "-",
               "%.0f" % statistics.median(bd) if bd else "-",
               "%.0f" % statistics.median(ad) if ad else "-",
               "%.1fx" % ratio if ratio else "-"))
    say()
    say("    Depth at touch, and one tick behind where the venue's own tick is")
    say("    known for that market (no tick is manufactured):")
    for slug, rs in per.items():
        tick = dec(next((r.get("tick_size") for r in rs if r.get("tick_size")), None))
        v0 = rs[-1]["view"]
        bb, ba = dec(v0.get("best_bid")), dec(v0.get("best_ask"))
        say("    %-40s tick=%s bid_touch_qty=%s ask_touch_qty=%s"
            % (str(slug)[:40], tick if tick is not None else "NOT_IDENTIFIED",
               v0.get("bid_qty_at_touch"), v0.get("ask_qty_at_touch")))
        if tick is None:
            say("      one-tick-behind: NOT COMPUTED (tick not supplied for this market)")
        elif bb is not None:
            say("      one tick behind the bid would be %s" % (bb - tick))
    say()
    allsp = [float(D(r["view"]["spread"])) for r in good
             if r["view"].get("spread") is not None]
    if allsp:
        say("    DISPLAYED_PASSIVE_SPREAD across the cohort:")
        say("      n=%d  min=%.4f p25=%.4f median=%.4f p75=%.4f max=%.4f"
            % (len(allsp), min(allsp), q(allsp, .25),
               statistics.median(allsp), q(allsp, .75), max(allsp)))
        say("      crossed or locked (spread <= 0): %d"
            % sum(1 for s in allsp if s <= 0))
    say("    PASSIVE_REALIZABLE_EDGE = NOT_IDENTIFIED")
    say()

    if changes_rows:
        import csv
        with (cap / "run85_phase2b_bookchange.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(changes_rows[0].keys()))
            w.writeheader()
            w.writerows(changes_rows)
        say("wrote run85_phase2b_bookchange.csv (%d rows)" % len(changes_rows))
    (cap / "run85_phase2b_analysis.txt").write_text("\n".join(R) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
