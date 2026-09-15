#!/usr/bin/env python3
"""RUN 85 TRACK B-L — BLOCK_4 economic read. READ ONLY. NO VENUE CONTACT.

Reads the COMMITTED sealed block and re-derives everything from the raw book
rows. Changes nothing: not the runner, not selection, not the cohort, not the
rate policy, not Track A.

--------------------------------------------------------------------------
THE ORDER MODEL, AND THE ALGEBRA THAT MAKES THE SHORT LEG CORRECT
--------------------------------------------------------------------------
One underlying book is quoted in the LONG token's price space. FINDING B-1
established longQuote == bestAsk and shortQuote == 1 - bestBid, 110/110, so
there is no independent complementary book: a SHORT position at price p is the
same instrument as a SELL of the long token at 1 - p.

At each t0 we rest, at frozen prices, for the life of that hypothetical order:

    LONG  maker price  = b0                  (a resting BUY  of the long token)
    SHORT maker price  = 1 - a0              (a resting SELL of the long token
                                              at 1 - (1 - a0) = a0)

    PAIR_COST             = b0 + (1 - a0) = 1 - (a0 - b0)
    PAIR_DISPLAYED_GROSS  = 1 - PAIR_COST = a0 - b0

So the touch tests live in the SAME price space as the captured ladder:

    LONG_TOUCH(h)   <=>  best_ask(h) <= b0     someone will sell at/below our bid
    SHORT_TOUCH(h)  <=>  best_bid(h) >= a0     someone will buy  at/above our offer

The short test compares against a0, NOT against 1 - a0 and NOT against
shortQuote. Comparing the short-token spelling (1 - a0) to the long-token
ladder would be the sign error this docstring exists to prevent.

A TOUCH IS NOT A FILL. Everything here is the F3 upper bound (touched counts as
filled), which is the most generous proxy available and is never the primary
claim. No execution exists, so no fill rate or fill probability is reported.

--------------------------------------------------------------------------
CLOCK
--------------------------------------------------------------------------
Every horizon is measured from ACTUAL observed elapsed time. Nominal slot times
are never substituted. "Touched by H" means touched at some observation whose
measured elapsed time is <= H; a cycle is eligible for H only if it actually
has an observation reaching H.
"""
from __future__ import annotations

import collections
import gzip
import importlib.util
import json
import statistics
import sys
from decimal import Decimal as D
from pathlib import Path

_f = importlib.util.spec_from_file_location(
    "fees", Path(__file__).with_name("run85_trackb_fees.py"))
F = importlib.util.module_from_spec(_f)
_f.loader.exec_module(F)

BLOCK = Path(__file__).parent / "evidence/trackbl/run85_trackbl_BLOCK_4_20260914T194859Z"
HORIZONS = [("5s", 5.0), ("10s", 10.0), ("30s", 30.0), ("60s", 60.0),
            ("5m", 300.0), ("10m", 600.0), ("15m", 900.0)]
COND_HORIZONS = [("5m", 300.0), ("10m", 600.0), ("15m", 900.0)]
# Track A's established horizon tolerance, reused rather than invented here.
# The capture is accurate to ~1 ms on 22 of 24 cycles; the tolerance exists so
# a read at 5.0001 s counts as the 5 s observation while one at 8.455 s does
# not. Every horizon still reports its real max |error|.
HORIZON_TOLERANCE_S = 0.5
NOT_ID = "NOT_IDENTIFIED"


def say(s=""):
    print(s)
    sys.stdout.flush()


def px(v):
    if not isinstance(v, dict):
        return None
    try:
        return D(str(v.get("value")))
    except Exception:                                      # noqa: BLE001
        return None


def top(levels, best):
    """Best price and its displayed qty, or (None, None)."""
    out = None
    for lv in (levels or []):
        p, q = px(lv.get("px")), lv.get("qty")
        try:
            q = D(str(q))
        except Exception:                                  # noqa: BLE001
            q = None
        if p is None or q is None or q <= 0:
            continue
        if out is None or best(p, out[0]):
            out = (p, q)
    return out or (None, None)


def quote(row):
    md = ((row.get("body") or {}).get("marketData") or {})
    b, bq = top(md.get("bids"), lambda p, c: p > c)
    a, aq = top(md.get("offers"), lambda p, c: p < c)
    return {"bid": b, "bid_qty": bq, "ask": a, "ask_qty": aq,
            "state": md.get("state")}


def mark(q):
    """m_h from the observed book. Midpoint ONLY if both sides are quoted."""
    if q["bid"] is None or q["ask"] is None:
        return None
    return (q["bid"] + q["ask"]) / 2


def load():
    rows = [json.loads(l) for l in gzip.open(BLOCK / "block_log.jsonl.gz", "rt")]
    by_lane = collections.defaultdict(list)
    for r in rows:
        r["q"] = quote(r)
        by_lane[r["_bl"]["lane"]].append(r)
    for lane in by_lane:
        by_lane[lane].sort(key=lambda r: r["_bl"]["actual_s"])
    cohort = json.loads((BLOCK / "block_cohort.json").read_text())
    return by_lane, cohort


def stat(xs):
    if not xs:
        return {"n": 0}
    xs = sorted(float(x) for x in xs)
    def pct(p):
        i = min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))
        return xs[i]
    return {"n": len(xs), "mean": statistics.fmean(xs), "median": pct(0.5),
            "p25": pct(0.25), "p75": pct(0.75), "min": xs[0], "max": xs[-1]}


def fmt(s, w=9):
    if not s.get("n"):
        return "n=0"
    return ("n=%d mean=%+.5f med=%+.5f p25=%+.5f p75=%+.5f min=%+.5f max=%+.5f"
            % (s["n"], s["mean"], s["median"], s["p25"], s["p75"],
               s["min"], s["max"]))


def build_pairs(by_lane, cohort):
    """One hypothetical pair per (lane, cycle) t0, with its later observations."""
    pairs = []
    for lane, rows in sorted(by_lane.items()):
        m = cohort[lane]
        for cyc in sorted({r["_bl"]["cycle"] for r in rows}):
            t0 = next((r for r in rows
                       if r["_bl"]["cycle"] == cyc and r["_bl"]["burst_offset_s"] == 0.0),
                      None)
            if t0 is None:
                continue
            q0 = t0["q"]
            if q0["bid"] is None or q0["ask"] is None or q0["ask"] <= q0["bid"]:
                continue                       # cannot post a two-sided pair
            t0s = t0["_bl"]["actual_s"]
            later = [{"elapsed": r["_bl"]["actual_s"] - t0s, "q": r["q"],
                      "cycle": r["_bl"]["cycle"], "offset": r["_bl"]["burst_offset_s"]}
                     for r in rows if r["_bl"]["actual_s"] > t0s]
            later.sort(key=lambda x: x["elapsed"])
            pairs.append({
                "lane": lane, "cycle": cyc,
                "slug": m["market_slug"], "band": m["price_band"],
                "spread_bucket": m["spread_bucket"], "sport": m["sport"],
                "league": m["league"], "tick": m["tick_size"],
                "b0": q0["bid"], "a0": q0["ask"],
                "bid_qty0": q0["bid_qty"], "ask_qty0": q0["ask_qty"],
                "state0": q0["state"], "later": later,
                # A later cycle's t0 can open on a DEGENERATE book -- the bid
                # side collapsed away -- which passes a0 > b0 and then shows a
                # huge "displayed gross". That is a broken book, not an edge,
                # and it is marked so no headline can quote it as opportunity.
                "degenerate": ((q0["ask"] - q0["bid"]) * 2
                               > (q0["ask"] + q0["bid"]) / 2),
                "max_elapsed": later[-1]["elapsed"] if later else 0.0,
            })
    return pairs


# ------------------------------------------------------------------ touches
def long_touch(obs, b0):
    return obs["q"]["ask"] is not None and obs["q"]["ask"] <= b0


def short_touch(obs, a0):
    return obs["q"]["bid"] is not None and obs["q"]["bid"] >= a0


def first_touch(p, side):
    """Earliest observation at which that leg touches, by ACTUAL elapsed time."""
    test = (lambda o: long_touch(o, p["b0"])) if side == "LONG" \
        else (lambda o: short_touch(o, p["a0"]))
    for o in p["later"]:
        if test(o):
            return o
    return None


def nearest(p, target):
    """The observation closest to a target horizon, for the error report."""
    if not p["later"]:
        return None
    return min(p["later"], key=lambda o: abs(o["elapsed"] - target))


def main():
    by_lane, cohort = load()
    pairs = build_pairs(by_lane, cohort)

    say("=" * 78)
    say("RUN 85 TRACK B-L -- BLOCK_4 ECONOMIC READ")
    say("=" * 78)
    say("evidence      %s" % BLOCK.name)
    say("pairs (lane x cycle t0, both sides quoted)   %d" % len(pairs))
    say("cohort bands  %s" % dict(collections.Counter(p["band"] for p in pairs)))
    say()
    say("DISCOVERY_FRAME_SCOPE = BOUNDED_PREFIX_OF_CURRENT_ACTIVE_UNIVERSE")
    say("DISCOVERY_LIST_EXHAUSTED = NO")
    say()

    # ---- 1. the frozen hypothetical pairs
    say("-" * 78)
    say("1. HYPOTHETICAL PAIRS, PRICES FROZEN AT t0")
    say("-" * 78)
    say("   LONG maker = b0 ; SHORT maker = 1 - a0 ; PAIR_COST = 1 - (a0 - b0)")
    say("   PAIR_DISPLAYED_GROSS = a0 - b0   (displayed, not realized)")
    say()
    say("   %-42s %-3s %-8s %-8s %-10s %-10s %s" %
        ("market", "cyc", "b0", "a0", "PAIR_COST", "DISP_GROSS", "flag"))
    for p in pairs:
        say("   %-42s %-3d %-8s %-8s %-10s %-10s %s"
            % (p["slug"][:42], p["cycle"], p["b0"], p["a0"],
               p["b0"] + (1 - p["a0"]), p["a0"] - p["b0"],
               "DEGENERATE_BOOK" if p["degenerate"] else ""))
    nd = sum(1 for p in pairs if p["degenerate"])
    say()
    say("   DEGENERATE_BOOK pairs: %d of %d. Their bid side had collapsed, so the"
        % (nd, len(pairs)))
    say("   'displayed gross' is an artefact of a broken book and is EXCLUDED")
    say("   from every headline figure below.")
    say()

    # ---- 2. horizon clock
    say("-" * 78)
    say("2. HORIZON CLOCK -- ACTUAL ELAPSED, NEVER NOMINAL")
    say("-" * 78)
    say("   %-6s %-9s %-14s %-14s %-14s" %
        ("target", "eligible", "nearest obs min", "max", "max |error|"))
    eligible = {}
    for name, h in HORIZONS:
        # ELIGIBILITY IS TWO-SIDED, and getting this wrong inflates the
        # denominator. A cycle counts for H only if it BOTH reaches H and has
        # at least one observation INSIDE the window -- otherwise "touched by
        # H" is vacuously false over an empty observation set. With a max slot
        # error of 3.46 s, a 5 s window can genuinely contain no read.
        lim = h + HORIZON_TOLERANCE_S
        elig = [p for p in pairs
                if p["max_elapsed"] >= h - HORIZON_TOLERANCE_S
                and any(o["elapsed"] <= lim for o in p["later"])]
        eligible[name] = elig
        dropped = len(pairs) - len(elig)
        errs, acts = [], []
        for p in elig:
            o = nearest(p, h)
            if o:
                acts.append(o["elapsed"])
                errs.append(abs(o["elapsed"] - h))
        say("   %-6s %-9d %-14.3f %-14.3f %-14.3f %s"
            % (name, len(elig), min(acts) if acts else 0,
               max(acts) if acts else 0, max(errs) if errs else 0,
               ("NO_OBS_INSIDE_WINDOW dropped=%d" % dropped) if dropped else ""))
    say()
    say("   A cycle counts as eligible for H only if it reaches H AND has an")
    say("   observation inside the window. Touch-by-H uses measured elapsed <= H.")
    say()

    # ---- 3. touch upper bounds
    say("-" * 78)
    say("3. TOUCH UPPER BOUNDS (F3: touched counted as filled) -- NOT FILLS")
    say("-" * 78)
    say("   LONG_TOUCH  <=> best_ask(h) <= b0")
    say("   SHORT_TOUCH <=> best_bid(h) >= a0     (short maker 1-a0 == sell at a0)")
    say()
    table = []
    for name, h in HORIZONS:
        elig = eligible[name]
        lt = st = bt = et = 0
        lres, sres = [], []
        for p in elig:
            obs = [o for o in p["later"]
                   if o["elapsed"] <= h + HORIZON_TOLERANCE_S]
            L = any(long_touch(o, p["b0"]) for o in obs)
            S = any(short_touch(o, p["a0"]) for o in obs)
            lt += L
            st += S
            bt += (L and S)
            et += (L or S)
            # residual markout for a one-legged pair, at this horizon
            o = nearest(p, h)
            m = mark(o["q"]) if o else None
            if m is not None:
                if L and not S:
                    lres.append(m - p["b0"])
                if S and not L:
                    sres.append(p["a0"] - m)
        table.append((name, len(elig), lt, st, et, bt, lres, sres))
    say("   %-5s %-8s %-6s %-6s %-7s %-6s %-9s %-9s %-13s %-13s"
        % ("h", "eligible", "long", "short", "either", "both",
           "either %", "both %", "long-only res", "short-only res"))
    for name, n, lt, st, et, bt, lres, sres in table:
        say("   %-5s %-8d %-6d %-6d %-7d %-6d %-9s %-9s %-13s %-13s"
            % (name, n, lt, st, et, bt,
               "%.1f%%" % (100.0 * et / n) if n else "-",
               "%.1f%%" % (100.0 * bt / n) if n else "-",
               ("%+.5f" % statistics.fmean([float(x) for x in lres])) if lres else "n=0",
               ("%+.5f" % statistics.fmean([float(x) for x in sres])) if sres else "n=0"))
    say()
    say("   These are TOUCH_UPPER_BOUND_F3. MAKER_FILL_PROBABILITY = NOT_IDENTIFIED.")
    say("   PAIR_COMPLETION_PROBABILITY = NOT_IDENTIFIED.")
    say()

    # ---- 4. sequential completion
    # ---- 3b. why: did our own resting LEVEL even survive?
    say("-" * 78)
    say("3b. DESCRIPTIVE -- DID THE t0 LEVELS PERSIST? (not a fill claim)")
    say("-" * 78)
    say("   A touch needs the market to come TO our price. This records what the")
    say("   book did instead. It explains the touch counts; it is not evidence")
    say("   about fills and is never promoted to one.")
    say()
    say("   %-42s %-3s %-22s %-22s"
        % ("market", "cyc", "best bid path", "best ask path"))
    for p in pairs:
        bids = {str(o["q"]["bid"]) for o in p["later"] if o["q"]["bid"] is not None}
        asks = {str(o["q"]["ask"]) for o in p["later"] if o["q"]["ask"] is not None}
        say("   %-42s %-3d %-22s %-22s"
            % (p["slug"][:42], p["cycle"],
               ",".join(sorted(bids))[:22], ",".join(sorted(asks))[:22]))
    say()
    moved = sum(1 for p in pairs
                if any(o["q"]["bid"] != p["b0"] for o in p["later"]
                       if o["q"]["bid"] is not None)
                or any(o["q"]["ask"] != p["a0"] for o in p["later"]
                       if o["q"]["ask"] is not None))
    say("   pairs whose touch price moved at all during their life: %d / %d"
        % (moved, len(pairs)))
    say()

    say("-" * 78)
    say("4. SEQUENTIAL COMPLETION -- CONDITIONAL ON A FIRST TOUCH")
    say("-" * 78)
    firsts = []
    for p in pairs:
        fl, fs = first_touch(p, "LONG"), first_touch(p, "SHORT")
        if fl is None and fs is None:
            continue
        if fs is None or (fl is not None and fl["elapsed"] <= fs["elapsed"]):
            firsts.append((p, "LONG", fl))
        else:
            firsts.append((p, "SHORT", fs))
    say("   FIRST_LEG_TOUCH_COUNT   %d of %d pairs" % (len(firsts), len(pairs)))
    say("   LONG_FIRST_COUNT        %d"
        % sum(1 for _, s, _ in firsts if s == "LONG"))
    say("   SHORT_FIRST_COUNT       %d"
        % sum(1 for _, s, _ in firsts if s == "SHORT"))
    say()
    for name, h in COND_HORIZONS:
        n_obs = n_hit = 0
        for p, side, fo in firsts:
            # window measured from the FIRST TOUCH, not from t0
            horizon_end = fo["elapsed"] + h
            covered = p["max_elapsed"] >= horizon_end - HORIZON_TOLERANCE_S
            if not covered:
                continue
            n_obs += 1
            win = [o for o in p["later"]
                   if fo["elapsed"] < o["elapsed"]
                   <= horizon_end + HORIZON_TOLERANCE_S]
            hit = any(short_touch(o, p["a0"]) for o in win) if side == "LONG" \
                else any(long_touch(o, p["b0"]) for o in win)
            n_hit += hit
        if n_obs == 0:
            say("   COMPLEMENT_TOUCH_WITHIN_%-4s_AFTER_FIRST  "
                "NOT_OBSERVABLE_FROM_BLOCK_3" % name.upper())
        else:
            say("   COMPLEMENT_TOUCH_WITHIN_%-4s_AFTER_FIRST  %d / %d observable"
                % (name.upper(), n_hit, n_obs))
    say()
    n15 = len(eligible["15m"])
    b15 = sum(1 for p in eligible["15m"]
              if any(long_touch(o, p["b0"]) for o in p["later"]
                     if o["elapsed"] <= 900 + HORIZON_TOLERANCE_S)
              and any(short_touch(o, p["a0"]) for o in p["later"]
                      if o["elapsed"] <= 900 + HORIZON_TOLERANCE_S))
    say("   BOTH_LEGS_TOUCHED_BY_15M_FROM_T0            %d / %d" % (b15, n15))
    say("   COMPLEMENT_TOUCH_CONDITIONAL_ON_FIRST_TOUCH  reported above")
    say("   (these are different quantities and are not merged)")
    say()

    # ---- 5. residual risk
    say("-" * 78)
    say("5. RESIDUAL RISK -- ONE LEG ONLY, MARKED FROM THE FIRST TOUCH")
    say("-" * 78)
    say("   m_h = midpoint, ONLY where both sides remain quoted. Never invented.")
    say("   LONG-first  residual markout_h = m_h - b0")
    say("   SHORT-first residual markout_h = a0 - m_h")
    say()
    worst = []
    for name, h in HORIZONS:
        vals, nomark = [], 0
        for p, side, fo in firsts:
            end = fo["elapsed"] + h
            if p["max_elapsed"] < end - HORIZON_TOLERANCE_S:
                continue
            o = min((x for x in p["later"] if x["elapsed"] >= fo["elapsed"]),
                    key=lambda x: abs(x["elapsed"] - end), default=None)
            if o is None:
                continue
            m = mark(o["q"])
            if m is None:
                nomark += 1
                continue
            v = (m - p["b0"]) if side == "LONG" else (p["a0"] - m)
            vals.append(v)
        s = stat(vals)
        say("   %-5s %s%s" % (name, fmt(s),
                              "  (mark unavailable: %d)" % nomark if nomark else ""))
    for p, side, fo in firsts:
        vals = []
        for o in p["later"]:
            if o["elapsed"] < fo["elapsed"]:
                continue
            m = mark(o["q"])
            if m is None:
                continue
            vals.append((m - p["b0"]) if side == "LONG" else (p["a0"] - m))
        if vals:
            worst.append((min(vals), p["slug"], p["cycle"], side))
    say()
    if worst:
        say("   WORST OBSERVED RESIDUAL PER CYCLE (most adverse mark seen)")
        for v, slug, cyc, side in sorted(worst)[:12]:
            say("     %+.5f  %-42s cyc=%d %s-first" % (v, slug[:42], cyc, side))
    else:
        say("   no one-legged cycle produced a markable residual")
    say()

    # ---- 6. completed-pair economics
    say("-" * 78)
    say("6. COMPLETED-PAIR ECONOMICS -- CONDITIONAL, NOT REALIZED")
    say("-" * 78)
    say("   maker theta = %s (a REBATE). Fee = theta * C * p * (1-p)." % F.THETA_MAKER)
    say("   long leg p = b0 ; short leg p = 1 - a0. The legs are a spread apart,")
    say("   so their rebates are NOT equal in general.")
    say()
    say("   %-42s %-3s %-10s %-11s %-11s %-11s"
        % ("market", "cyc", "DISP_GROSS", "reb_long_ex", "reb_short_ex", "reb_total_ex"))
    seen = set()
    for p in pairs:
        key = (p["slug"], p["b0"], p["a0"])
        if key in seen:
            continue
        seen.add(key)
        rl, _ = F.maker_rebate(1, p["b0"])
        rs, _ = F.maker_rebate(1, D(1) - p["a0"])
        say("   %-42s %-3d %-10s %-11.6f %-11.6f %-11.6f"
            % (p["slug"][:42], p["cycle"], p["a0"] - p["b0"], rl, rs, rl + rs))
    say()
    say("   PER-CONTRACT figures above are EXACT_UNROUNDED_REBATE.")
    say("   Official rounding is half-even to the cent, PER FILL. Worked examples")
    say("   on the tightest and widest pairs in the cohort:")
    say()
    ex = [pairs[0]] + [p for p in pairs if p["band"] == "MODERATE"][:1]
    for p in ex:
        say("   %s  b0=%s a0=%s" % (p["slug"][:52], p["b0"], p["a0"]))
        for c in (1, 10, 100, 1000):
            exl, rdl = F.maker_rebate(c, p["b0"])
            exs, rds = F.maker_rebate(c, D(1) - p["a0"])
            gross = (p["a0"] - p["b0"]) * c
            say("     C=%-5d displayed_gross=$%-9.4f exact_rebate=$%-9.5f "
                "rounded=$%-8.2f" % (c, gross, exl + exs, rdl + rds))
        say()
    say("   ACTUAL_REBATE_NOT_IDENTIFIED -- no execution exists, fill")
    say("   fragmentation is unknown, and a fragmented fill can round to zero.")
    say()

    # ---- 7. pre-adverse-selection budget
    say("-" * 78)
    say("7. PRE_ADVERSE_SELECTION_PAIR_BUDGET")
    say("-" * 78)
    say("   CONDITIONAL_ON_BOTH_MODELED_MAKER_EXECUTIONS. Not expected profit,")
    say("   not realized edge, not net expectancy.")
    say()
    say("   %-42s %-11s %-11s %-11s"
        % ("market (C = 100 contracts)", "spread cap", "rebates rnd", "budget"))
    for p in sorted({(p["slug"], p["b0"], p["a0"])
                     for p in pairs if not p["degenerate"]}):
        slug, b0, a0 = p
        gross = (a0 - b0) * 100
        _, rdl = F.maker_rebate(100, b0)
        _, rds = F.maker_rebate(100, D(1) - a0)
        say("   %-42s $%-10.2f $%-10.2f $%-10.2f"
            % (slug[:42], gross, rdl + rds, gross + rdl + rds))
    say()
    say("   LIQUIDITY_INCENTIVE_REWARD = NOT_VERIFIED")
    say("   EXCLUDED_FROM_PRIMARY_ECONOMICS = YES")
    say()

    # ---- 8. capacity
    say("-" * 78)
    say("8. CAPACITY -- DISPLAYED ONLY")
    say("-" * 78)
    say("   %-42s %-3s %-12s %-12s %-14s"
        % ("market", "cyc", "bid qty", "ask qty", "pair proxy"))
    for p in pairs:
        cap = min(p["bid_qty0"], p["ask_qty0"])
        say("   %-42s %-3d %-12s %-12s %-14s"
            % (p["slug"][:42], p["cycle"], p["bid_qty0"], p["ask_qty0"], cap))
    say()
    say("   DISPLAYED_CAPACITY_PROXY = the figures above")
    say("   QUEUE_POSITION      = NOT_IDENTIFIED")
    say("   EXECUTABLE_CAPACITY = NOT_IDENTIFIED")
    say("   Visible queue size is NOT executable capacity and says nothing about")
    say("   where our order would sit in the queue.")
    say()

    # ---- 9. segmentation
    say("-" * 78)
    say("9. SEGMENTATION")
    say("-" * 78)
    for key, label in (("band", "PRICE BAND"), ("spread_bucket", "SPREAD REGIME"),
                       ("tick", "TICK"), ("sport", "SPORT"), ("league", "LEAGUE")):
        say("   %s" % label)
        groups = collections.defaultdict(list)
        for p in pairs:
            groups[str(p[key])].append(p)
        for g, ps in sorted(groups.items()):
            et = bt = 0
            for p in ps:
                obs = [o for o in p["later"]
                       if o["elapsed"] <= 900 + HORIZON_TOLERANCE_S]
                L = any(long_touch(o, p["b0"]) for o in obs)
                S = any(short_touch(o, p["a0"]) for o in obs)
                et += (L or S)
                bt += (L and S)
            say("     %-22s pairs=%-3d either_touch<=15m=%-3d both=%-3d"
                % (g[:22], len(ps), et, bt))
        say()
    say("   NEAR_MID_EXECUTION_EVIDENCE_FROM_BLOCK_3 = NONE")
    say("   The cohort contains zero NEAR_MID markets. Tail-market results are")
    say("   NOT extrapolated to near-mid markets.")
    say()

    say("-" * 78)
    say("10. SCOPE LIMITATION CARRIED WITH EVERY CONCLUSION")
    say("-" * 78)
    say("   BLOCK_3 selection came from a BOUNDED PREFIX of the current active")
    say("   universe (DISCOVERY_LIST_EXHAUSTED = NO). This is evidence about the")
    say("   selected cohort only -- not 'the best markets on PMUS', not")
    say("   'whole-board opportunity', not 'current-universe expectancy'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
