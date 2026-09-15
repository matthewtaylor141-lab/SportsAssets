#!/usr/bin/env python3
"""RUN 85 TRACK B — primary economic analysis of ONE admitted Track A segment.

Reads the segment's COMMITTED RAW book states through `git show`, and
re-derives every set from those raw ladders rather than trusting the runner's
own derived file. The raw log is the evidence; sets.jsonl.gz is the runner's
opinion of it.

INTEGRITY FIRST. Nothing economic is computed until the segment passes the
operational gate. A segment that fails is reported and NOT admitted.

--------------------------------------------------------------------------
THE TWO HYPOTHETICAL RESTING ORDERS  (locked definitions)
--------------------------------------------------------------------------
For every valid t0 with bestBid b and bestAsk a:

    LONG_MAKER_PRICE  = b
    SHORT_MAKER_PRICE = 1 - a          (NOT 1 - b)
    PAIR_COST         = b + (1 - a) = 1 - (a - b)
    DISPLAYED_SPREAD_CAPTURE = a - b

The short maker order is a resting BUY of the short side at 1-a, which sits in
the long book as a resting SELL at a. So:

    LONG  touched  <=>  ask(t+h) <= b        crossed <=>  ask(t+h) <  b
    SHORT touched  <=>  bid(t+h) >= a        crossed <=>  bid(t+h) >  a

None of these is a FILL. They are touch states of a price we never placed.

--------------------------------------------------------------------------
MARKOUTS, AND THE IDENTITY THAT FOLLOWS
--------------------------------------------------------------------------
    LONG_MARKOUT  = mid(t+h) - b        we bought long at b
    SHORT_MARKOUT = a - mid(t+h)        we bought short at 1-a

Adding them:  LONG_MARKOUT + SHORT_MARKOUT = a - b = the spread, for ANY
mid(t+h). A completed pair therefore carries no directional risk at all — its
outcome is the spread whatever the market does.

The whole of adverse selection consequently lives in the INCOMPLETE pair: one
leg filled, the other not, leaving a naked position. That is not an assumption,
it falls out of the arithmetic, and it is why incomplete-pair cost is reported
separately and never averaged into completed pairs.

An unchanged valid book is a zero markout and is retained.

--------------------------------------------------------------------------
FILL MODELS — none of which is permitted to be the headline except by rank
--------------------------------------------------------------------------
  F0  crossed AND the resting level at our price is GONE at the horizon
      (the strongest queue-consumption evidence a public book affords)
  F1  crossed, strictly
  F2  touched AND the resting level at our price is gone or reduced
  F3  touched == filled.  AN UPPER BOUND. Never the primary result.

BOTH_LEGS_TOUCHED is never promoted directly to COMPLETED_PAIR: a completed
pair requires both legs to fill under the SAME model.

--------------------------------------------------------------------------
REBATE ROUNDING SCENARIOS
--------------------------------------------------------------------------
Actual fill decomposition is unobservable from a public book, so three
disclosed scenarios are reported rather than one invented number:

  R0  the whole hypothetical order fills as ONE fill
  R1  conservative fragmentation — four equal fills
  R2  extreme fragmentation — one contract per fill

The continuous unrounded rebate is never treated as cash received.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import statistics
import subprocess
import sys
from decimal import Decimal as D
from pathlib import Path

_f = importlib.util.spec_from_file_location(
    "tbfees", Path(__file__).with_name("run85_trackb_fees.py"))
FEE = importlib.util.module_from_spec(_f)
_f.loader.exec_module(FEE)

COHORT_SHA256 = "8cdedf26479a309bf7e22c292ebdd2f562673e2b8dccb2ec2793d9ad29187efe"
HORIZONS = ("5.0", "10.0", "30.0", "60.0")
SET_GAP_S = 90.0                 # legs inside a set are <=30 s apart; sets ~7 min
HYPOTHETICAL_SIZE = 100          # contracts, frozen and disclosed
FRAGMENTATION = {"R0": 1, "R1": 4, "R2": HYPOTHETICAL_SIZE}


def git_show(ref, path):
    return subprocess.run(["git", "show", "%s:%s" % (ref, path)],
                          check=True, capture_output=True).stdout


def jsonl_gz(raw):
    with gzip.open(io.BytesIO(raw), "rt") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def ladder(levels):
    out = []
    for x in levels or []:
        if not isinstance(x, dict):
            continue
        px = x.get("px")
        raw = px.get("value") if isinstance(px, dict) else px
        try:
            out.append((D(str(raw)), D(str(x.get("qty")))))
        except Exception:                                  # noqa: BLE001
            continue
    return out


def book_state(row):
    """(bid, ask, bid_ladder, ask_ladder, state, slug) from a raw /book row."""
    d = ((row.get("body") or {}).get("marketData")) or {}
    bids, asks = ladder(d.get("bids")), ladder(d.get("offers"))
    b = max((p for p, _ in bids), default=None)
    a = min((p for p, _ in asks), default=None)
    return b, a, bids, asks, d.get("state"), d.get("marketSlug")


def qty_at(levels, price):
    return sum(q for p, q in levels if p == price)


# ------------------------------------------------------------- integrity
def integrity(ref, seg):
    rep = {}
    files = sorted(subprocess.run(
        ["git", "ls-tree", "--name-only", "%s:%s" % (ref, seg)],
        check=True, capture_output=True).stdout.decode().split())
    blobs = {f: git_show(ref, "%s/%s" % (seg, f)) for f in files}
    rep["SEGMENT_PUSHED"] = "YES" if files else "NO"

    manifest = {}
    for line in blobs["checksums.sha256"].decode().strip().splitlines():
        want, name = line.split("  ", 1)
        manifest[name] = want
    bad = [n for n, w in manifest.items()
           if n not in blobs or hashlib.sha256(blobs[n]).hexdigest() != w]
    rep["INTERNAL_CHECKSUMS"] = "PASS" if not bad else "FAIL %s" % bad
    rep["SEGMENT_DIGEST"] = hashlib.sha256(blobs["checksums.sha256"]).hexdigest()

    meta = json.loads(blobs["runner_metadata.json"])
    summ = json.loads(blobs["segment_summary.json"])
    cohort_raw = git_show(ref, "research/run85_phase2_frozen_cohort.json")
    live = hashlib.sha256(cohort_raw).hexdigest()
    rep["COHORT_HASH_MATCH"] = (
        "YES" if live == COHORT_SHA256 == meta.get("cohort_sha256") else "NO")

    rows = jsonl_gz(blobs["request_log.jsonl.gz"])
    rep["REQUEST_COUNT"] = len(rows)
    rep["HTTP_200_COUNT"] = sum(1 for r in rows if r.get("http_status") == 200)
    rep["HTTP_429_COUNT"] = sum(1 for r in rows if r.get("http_status") == 429)
    idf = 0
    for r in rows:
        if r.get("http_status") != 200:
            continue
        got = (((r.get("body") or {}).get("marketData")) or {}).get("marketSlug")
        if got is not None and got != str(r.get("path")).split("/")[3]:
            idf += 1
    rep["IDENTITY_FAILURES"] = idf

    starts = sorted(r["local_request_monotonic_ns"] / 1e9 for r in rows)
    gaps = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
    rep["MIN_REQUEST_GAP"] = round(min(gaps), 4) if gaps else None
    rep["ACTUAL_CAPTURE_START"] = meta.get("segment_start_utc")
    rep["ACTUAL_CAPTURE_END"] = meta.get("segment_end_utc")
    rep["ELAPSED_S"] = summ.get("elapsed_s")
    rep["STOP_REASON"] = summ.get("stop_reason")
    rep["RETIRED"] = summ.get("retired") or {}

    ok = (rep["SEGMENT_PUSHED"] == "YES"
          and rep["INTERNAL_CHECKSUMS"] == "PASS"
          and rep["COHORT_HASH_MATCH"] == "YES"
          and rep["IDENTITY_FAILURES"] == 0
          and rep["REQUEST_COUNT"] > 0
          and (rep["MIN_REQUEST_GAP"] or 0) >= 2.5 - 1e-6)
    rep["ADMITTED_TO_PRIMARY"] = "YES" if ok else "NO"
    return rep, rows, meta, summ


# --------------------------------------------------- re-derive sets from raw
def build_sets(rows):
    """Group each market's /book rows into observation sets, from raw only."""
    by_market = {}
    for r in rows:
        if not str(r.get("path", "")).endswith("/book"):
            continue
        by_market.setdefault(str(r["path"]).split("/")[3], []).append(r)
    sets = []
    for slug, rs in by_market.items():
        rs.sort(key=lambda x: x["local_request_monotonic_ns"])
        group = []
        for r in rs:
            t = r["local_request_monotonic_ns"] / 1e9
            if group and t - (group[-1]["local_request_monotonic_ns"] / 1e9) > SET_GAP_S:
                sets.append((slug, group))
                group = []
            group.append(r)
        if group:
            sets.append((slug, group))
    return sets


def analyse_set(slug, group):
    """One hypothetical postable pair and its per-leg horizon states."""
    if len(group) < 2:
        return None
    t0 = group[0]
    b, a, bids0, asks0, state0, got0 = book_state(t0)
    if b is None or a is None:
        return None                                   # not postable
    base = t0["local_request_monotonic_ns"] / 1e9
    rec = {
        "market": slug, "t0_wall_utc": t0.get("local_request_wall_utc"),
        "state": state0,
        "bid": b, "ask": a, "spread": a - b,
        "long_maker_price": b, "short_maker_price": D(1) - a,
        "pair_cost": b + (D(1) - a),
        "displayed_spread_capture": a - b,
        "near_touch_bid_qty": qty_at(bids0, b),
        "near_touch_ask_qty": qty_at(asks0, a),
        "hypothetical_size": HYPOTHETICAL_SIZE,
        "horizons": {},
    }
    for r in group[1:]:
        h = round((r["local_request_monotonic_ns"] / 1e9) - base)
        key = "%.1f" % h
        if key not in HORIZONS:
            continue
        hb, ha, bidsh, asksh, _, _ = book_state(r)
        if hb is None and ha is None:
            rec["horizons"][key] = {"available": False}
            continue
        mid = ((hb + ha) / 2) if (hb is not None and ha is not None) else None
        long_t = ha is not None and ha <= b
        long_c = ha is not None and ha < b
        short_t = hb is not None and hb >= a
        short_c = hb is not None and hb > a
        bid_level_now = qty_at(bidsh, b)
        ask_level_now = qty_at(asksh, a)
        rec["horizons"][key] = {
            "available": True,
            "bid": hb, "ask": ha, "mid": mid,
            "long_touch_status": ("CROSSED" if long_c else
                                  "TOUCHED" if long_t else "NOT_TOUCHED"),
            "short_touch_status": ("CROSSED" if short_c else
                                   "TOUCHED" if short_t else "NOT_TOUCHED"),
            "long_markout": (mid - b) if mid is not None else None,
            "short_markout": (a - mid) if mid is not None else None,
            "long_level_gone": bid_level_now == 0,
            "long_level_reduced": bid_level_now < rec["near_touch_bid_qty"],
            "short_level_gone": ask_level_now == 0,
            "short_level_reduced": ask_level_now < rec["near_touch_ask_qty"],
        }
    return rec


def fills(hz, model):
    """(long_fill, short_fill) under one disclosed model. Never called FILLED
    in the record -- these are model outputs, not observations."""
    if not hz.get("available"):
        return False, False
    lt, st = hz["long_touch_status"], hz["short_touch_status"]
    if model == "F3":
        return lt != "NOT_TOUCHED", st != "NOT_TOUCHED"
    if model == "F2":
        return (lt != "NOT_TOUCHED" and (hz["long_level_gone"] or hz["long_level_reduced"]),
                st != "NOT_TOUCHED" and (hz["short_level_gone"] or hz["short_level_reduced"]))
    if model == "F1":
        return lt == "CROSSED", st == "CROSSED"
    return (lt == "CROSSED" and hz["long_level_gone"],
            st == "CROSSED" and hz["short_level_gone"])


def rebates(price, size, scenario):
    n = FRAGMENTATION[scenario]
    per = D(size) / D(n)
    ex, _ = FEE.maker_rebate(size, price)
    _, rd_each = FEE.maker_rebate(per, price)
    return ex, rd_each * n


def pair_state(lf, sf):
    if lf and sf:
        return "COMPLETED_PAIR"
    if lf:
        return "LONG_ONLY_FILL"
    if sf:
        return "SHORT_ONLY_FILL"
    return "NO_FILL"


def med(xs):
    return statistics.median(xs) if xs else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--segment", required=True)
    a = ap.parse_args(argv)

    rep, rows, meta, summ = integrity(a.ref, a.segment)
    print("=== OPERATIONAL INTEGRITY ===")
    for k, v in rep.items():
        print("%-28s %s" % (k, v))
    print()
    if rep["ADMITTED_TO_PRIMARY"] != "YES":
        print("SEGMENT NOT ADMITTED TO PRIMARY ANALYSIS. No economics computed.")
        return 1

    sets = [s for s in (analyse_set(slug, g) for slug, g in build_sets(rows)) if s]
    print("=== PRIMARY SAMPLE ===")
    print("POSTABLE_PAIRS               %d" % len(sets))
    print("PRIMARY_OBSERVATIONS         %d book reads"
          % sum(1 for r in rows if str(r.get("path", "")).endswith("/book")))
    print()

    print("=== TOUCH REGISTERS (never fills) ===")
    print("%-10s %-10s %-10s %-10s %-10s" % ("horizon", "avail", "long_t", "short_t", "both_t"))
    for h in HORIZONS:
        av = [s for s in sets if s["horizons"].get(h, {}).get("available")]
        lt = [s for s in av if s["horizons"][h]["long_touch_status"] != "NOT_TOUCHED"]
        st = [s for s in av if s["horizons"][h]["short_touch_status"] != "NOT_TOUCHED"]
        bt = [s for s in av if s in lt and s in st]
        print("%-10s %-10d %-10d %-10d %-10d" % (h, len(av), len(lt), len(st), len(bt)))
    print()

    print("=== FILL MODELS -> PAIR STATES (per horizon) ===")
    for model in ("F0", "F1", "F2", "F3"):
        line = []
        for h in HORIZONS:
            states = {"COMPLETED_PAIR": 0, "LONG_ONLY_FILL": 0,
                      "SHORT_ONLY_FILL": 0, "NO_FILL": 0}
            for s in sets:
                hz = s["horizons"].get(h, {})
                states[pair_state(*fills(hz, model))] += 1
            line.append("h=%s pair=%d L=%d S=%d none=%d"
                        % (h, states["COMPLETED_PAIR"], states["LONG_ONLY_FILL"],
                           states["SHORT_ONLY_FILL"], states["NO_FILL"]))
        tag = " (UPPER BOUND)" if model == "F3" else ""
        print("%-4s%s" % (model, tag))
        for x in line:
            print("      %s" % x)
    print()

    print("=== BUDGET (spread + both rounded rebates), completed-pair proxies ===")
    print("scenario assumptions: R0 one fill  R1 four fills  R2 %d fills"
          % HYPOTHETICAL_SIZE)
    for model in ("F0", "F1", "F2", "F3"):
        for h in ("60.0",):
            comp = [s for s in sets
                    if pair_state(*fills(s["horizons"].get(h, {}), model))
                    == "COMPLETED_PAIR"]
            if not comp:
                print("%-4s h=%-5s COMPLETED_PAIR_PROXY=0  -> no budget to report"
                      % (model, h))
                continue
            spreads = [s["spread"] * HYPOTHETICAL_SIZE for s in comp]
            row = []
            for sc in ("R0", "R1", "R2"):
                tot = []
                for s in comp:
                    _, rl = rebates(s["long_maker_price"], HYPOTHETICAL_SIZE, sc)
                    _, rs = rebates(s["short_maker_price"], HYPOTHETICAL_SIZE, sc)
                    tot.append(rl + rs)
                row.append("%s reb=%s budget=%s" % (
                    sc, med(tot), med([x + y for x, y in zip(spreads, tot)])))
            print("%-4s h=%-5s n=%-4d med_spread=%s  %s"
                  % (model, h, len(comp), med(spreads), "  ".join(row)))
    print()

    print("=== INCOMPLETE PAIRS — residual exposure, reported separately ===")
    for model in ("F0", "F1", "F2", "F3"):
        for h in HORIZONS:
            res = []
            for s in sets:
                hz = s["horizons"].get(h, {})
                st = pair_state(*fills(hz, model))
                if st in ("LONG_ONLY_FILL", "SHORT_ONLY_FILL"):
                    mk = (hz["long_markout"] if st == "LONG_ONLY_FILL"
                          else hz["short_markout"])
                    res.append((st, mk))
            if res:
                mks = [float(m) for _, m in res if m is not None]
                print("%-4s h=%-5s residual_legs=%-4d median_residual_markout=%s"
                      % (model, h, len(res),
                         ("%.6f" % med(mks)) if mks else "NOT_IDENTIFIED"))
    print()

    print("=== CARRIED FORWARD ===")
    print("LIQUIDITY_INCENTIVE_REWARD   EXCLUDED — cohort eligibility NOT_VERIFIED")
    print("PAIR_COMPLETION_PROBABILITY  NOT_IDENTIFIED")
    print("MAKER_FILL_PROBABILITY       NOT_IDENTIFIED")
    print("NET_EXPECTANCY               NOT REPORTED")
    print("mirror_live = false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
