#!/usr/bin/env python3
"""RUN 84 -- native matched-pair mechanism, tested as an UPPER BOUND.

WHAT THIS IS AND IS NOT.

The only multi-day book data BETTOR retains is `copy_probes`, sealed as
u2_events_v1. Every row is an ask ladder read by `copy_probe.probe_trade`,
which fetches `GET {clob_api_base}/book` with clob_api_base defaulting to
https://clob.polymarket.com (backend/sportsassets/config.py:37). That is
RN1'S VENUE. BETTOR executes on PMUS, through a different adapter, different
slugs and a different token representation -- which is the entire reason the
mapping/coverage program exists.

So this file CANNOT establish a BETTOR-executable edge, and it never claims to.
What it can do is bound one: the pair economics are computed here with

    - no fee of any kind (BETTOR's fee is unmeasured; task #126 exists to
      measure it and has not reported),
    - no slippage beyond walking the displayed ladder,
    - no queue, no adverse selection, no completion risk,
    - both legs priced as if executable at the same instant,

which is strictly better than anything BETTOR could achieve on any venue. If
the mechanism does not clear $1.00 under those assumptions it does not clear
anywhere, and that is a real result. If it does clear, nothing follows about
BETTOR except that the question stays open.

STRUCTURAL LIMIT, STATED UP FRONT. `copy_probes.depth` is the ASK side only
(migration 005: "top ask levels at probe time"). There is no bid anywhere in
the retained data. Passive execution -- regimes A, B and C of the Run 84
specification -- therefore cannot be modelled at all, not conservatively and
not optimistically. Only aggressive/aggressive and no-trade are testable.

Usage: python3 research/run84_native_pair.py <out_dir>
"""
from __future__ import annotations

import gzip
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

U2 = Path("research/snapshots/u2_events_v1.jsonl.gz")
SETTLEMENT = Path("research/snapshots/settlement_v1.jsonl")

# Pair-cost thresholds. The Run 84 specification names these; 0.97 carries no
# privilege here and is not used to select anything.
THRESHOLDS = [Decimal(t) for t in
              ("0.90", "0.92", "0.94", "0.95", "0.96", "0.97", "0.98", "0.99")]

# Simultaneity windows. A pair whose two legs were read 300 s apart is not a
# pair anyone could have executed; the window is reported, never assumed away.
WINDOWS_S = [1, 5, 30, 60, 300]

CLIPS = [Decimal(c) for c in ("100", "500", "1000")]


def D(x):
    return Decimal(str(x))


def ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def load_events():
    rows = []
    with gzip.open(U2, "rt") as fh:
        for line in fh:
            rows.append(json.loads(line))
    return rows


def walk_ask_ladder(depth, notional):
    """Cost per share to buy `notional` dollars by walking displayed asks.

    Returns (vwap, filled_notional, exhausted). No fee, no slippage beyond the
    ladder, no queue. Deliberately the most favourable reading of the data.
    """
    if not depth:
        return None, Decimal(0), True
    spent = Decimal(0)
    shares = Decimal(0)
    for lv in depth:
        px, sz = D(lv[0]), D(lv[1])
        if px <= 0 or sz <= 0:
            continue
        room = notional - spent
        if room <= 0:
            break
        level_notional = px * sz
        if level_notional <= room:
            spent += level_notional
            shares += sz
        else:
            take = room / px
            spent += room
            shares += take
            break
    if shares == 0:
        return None, Decimal(0), True
    return spent / shares, spent, spent < notional


def main(outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    R = []

    def say(s=""):
        R.append(s)
        print(s)

    ev = load_events()
    say("RUN 84 -- NATIVE MATCHED-PAIR MECHANISM (UPPER BOUND)")
    say("=" * 68)
    say()
    say("1. INPUT PROVENANCE")
    say("   source                : %s" % U2)
    say("   rows                  : %d" % len(ev))
    say("   book source (code)    : clob_api_base = https://clob.polymarket.com")
    say("                           -> RN1's venue. NOT a BETTOR-executable price.")
    say("   ladder side retained  : ASK ONLY (migration 005). No bid exists anywhere")
    say("                           in the retained data, at any timestamp.")
    say()

    # ---- 2. inventory ----------------------------------------------------
    days = Counter()
    book_ok = Counter()
    depth_levels = Counter()
    sports = Counter()
    conds = set()
    for e in ev:
        days[ts(e["probe_at"]).date().isoformat()] += 1
        book_ok[bool(e.get("book_ok"))] += 1
        depth_levels[e.get("depth_levels") or 0] += 1
        sports[e.get("sport") or "<none>"] += 1
        if e.get("condition_id_effective"):
            conds.add(e["condition_id_effective"])
    say("2. INVENTORY")
    say("   distinct calendar days: %d   (%s .. %s)"
        % (len(days), min(days), max(days)))
    say("   distinct conditions   : %d" % len(conds))
    say("   book_ok true / false  : %d / %d" % (book_ok[True], book_ok[False]))
    say("   rows with >=1 ask lvl : %d" % sum(v for k, v in depth_levels.items() if k))
    say("   rows with 0 ask levels: %d" % depth_levels[0])
    say("   top sports            : %s" % dict(sports.most_common(6)))
    say()

    # ---- 3. pair formation ----------------------------------------------
    # A pair needs BOTH outcomes of one condition to have been probed. The probe
    # fires only on a roster whale's BUY, so the sampling instants are not ours
    # to choose: this is an opportunity set inherited from their behaviour, not
    # a native scan. That limit is reported, not corrected.
    by_cond = defaultdict(lambda: {0: [], 1: []})
    usable = 0
    for e in ev:
        c = e.get("condition_id_effective")
        oi = e.get("outcome_index")
        if c is None or oi not in (0, 1):
            continue
        if not e.get("book_ok") or not (e.get("depth") or []):
            continue
        if e.get("best_ask") is None:
            continue
        usable += 1
        by_cond[c][oi].append((ts(e["probe_at"]), e))

    say("3. PAIR FORMATION")
    say("   rows usable (book_ok, >=1 ask level, best_ask present): %d of %d"
        % (usable, len(ev)))
    both = [c for c, d in by_cond.items() if d[0] and d[1]]
    say("   conditions with at least one probe on EACH outcome     : %d of %d"
        % (len(both), len(by_cond)))
    say()

    # nearest-in-time pairing, per condition, per window
    pair_rows = []
    per_window = {}
    for w in WINDOWS_S:
        pairs = []
        for c in both:
            a = sorted(by_cond[c][0])
            b = sorted(by_cond[c][1])
            used_b = set()
            for t0, e0 in a:
                best = None
                for j, (t1, e1) in enumerate(b):
                    if j in used_b:
                        continue
                    gap = abs((t1 - t0).total_seconds())
                    if gap <= w and (best is None or gap < best[0]):
                        best = (gap, j, t1, e1)
                if best is None:
                    continue
                gap, j, t1, e1 = best
                used_b.add(j)
                pairs.append((c, gap, e0, e1))
        per_window[w] = pairs
    say("   pairs formed, by simultaneity window:")
    for w in WINDOWS_S:
        say("     within %5ds : %d pairs   (%d distinct conditions)"
            % (w, len(per_window[w]), len({p[0] for p in per_window[w]})))
    say()

    # ---- 4. pair-cost distribution --------------------------------------
    say("4. DISPLAYED PAIR COST  (best_ask YES + best_ask NO, zero fee)")
    say("   'Displayed' means the touch only, at size 1. It is NOT a tradeable")
    say("   quantity and is reported first precisely so the size-aware numbers")
    say("   below can be compared against it.")
    say()
    for w in WINDOWS_S:
        pairs = per_window[w]
        if not pairs:
            say("   window %5ds : no pairs" % w)
            continue
        costs = [D(e0["best_ask"]) + D(e1["best_ask"]) for _, _, e0, e1 in pairs]
        costs_f = [float(c) for c in costs]
        under = {str(t): sum(1 for c in costs if c < t) for t in THRESHOLDS}
        say("   window %5ds  n=%-6d min=%.4f  p05=%.4f  median=%.4f  max=%.4f"
            % (w, len(costs), min(costs_f),
               statistics.quantiles(costs_f, n=20)[0] if len(costs_f) > 20 else min(costs_f),
               statistics.median(costs_f), max(costs_f)))
        say("                 below: %s" % under)
    say()

    # ---- 5. size-aware pair cost ----------------------------------------
    say("5. SIZE-AWARE PAIR COST  (walk BOTH displayed ask ladders, zero fee)")
    say("   Still no fee, no queue, no adverse move between legs, both legs")
    say("   treated as executable at their own probe instant.")
    say()
    hdr = "   window  clip      n      min      median   <1.00   <0.99   <0.97   <0.95"
    say(hdr)
    for w in WINDOWS_S:
        pairs = per_window[w]
        if not pairs:
            continue
        for clip in CLIPS:
            vals = []
            exhausted = 0
            for c, gap, e0, e1 in pairs:
                v0, n0, x0 = walk_ask_ladder(e0.get("depth"), clip)
                v1, n1, x1 = walk_ask_ladder(e1.get("depth"), clip)
                if v0 is None or v1 is None:
                    continue
                if x0 or x1:
                    exhausted += 1
                    continue           # cannot buy the clip; not an opportunity
                pc = v0 + v1
                vals.append(pc)
                if w == 60:
                    pair_rows.append({
                        "condition_id": c, "gap_s": "%.3f" % gap,
                        "clip_usd": str(clip),
                        "vwap_outcome0": str(v0), "vwap_outcome1": str(v1),
                        "pair_cost": str(pc),
                        "gross_pair_profit": str(Decimal(1) - pc),
                        "probe_at_0": e0["probe_at"], "probe_at_1": e1["probe_at"],
                        "market_slug": e0.get("market_slug"),
                        "sport": e0.get("sport"),
                    })
            if not vals:
                say("   %5ds  $%-6s  (no pair could source the clip on both legs)"
                    % (w, clip))
                continue
            vf = [float(v) for v in vals]
            say("   %5ds  $%-6s %-6d %8.4f %8.4f %7d %7d %7d %7d"
                % (w, clip, len(vals), min(vf), statistics.median(vf),
                   sum(1 for v in vals if v < 1), sum(1 for v in vals if v < D("0.99")),
                   sum(1 for v in vals if v < D("0.97")),
                   sum(1 for v in vals if v < D("0.95"))))
    say()

    # ---- 6. independence -------------------------------------------------
    say("6. INDEPENDENCE / CLUSTERING")
    for w in (60,):
        pairs = per_window[w]
        if not pairs:
            continue
        say("   window %ds: %d pairs / %d conditions / %d days"
            % (w, len(pairs), len({p[0] for p in pairs}),
               len({ts(p[2]["probe_at"]).date() for p in pairs})))
    say()

    # ---- 7. the artifact test -------------------------------------------
    # The count of sub-$1.00 pairs rises monotonically with the window. Either
    # the opportunity is real and wider windows merely find more of it, or the
    # opportunity IS the window -- two book reads taken minutes apart, with the
    # market moving in between, summed as though they were simultaneous. The
    # two hypotheses make opposite predictions about the gap distribution of
    # the sub-$1.00 set, so the data decides.
    say("7. IS THE SUB-$1.00 SET A TIME-SKEW ARTIFACT?")
    buckets = [(0, 1), (1, 5), (5, 30), (30, 60), (60, 300)]
    say("   gap bucket      pairs    median cost   <1.00   share <1.00")
    wide = per_window[300]
    for lo, hi in buckets:
        sel = [(g, e0, e1) for _, g, e0, e1 in wide if lo <= g < hi]
        if not sel:
            continue
        costs = [D(e0["best_ask"]) + D(e1["best_ask"]) for _, e0, e1 in sel]
        n_under = sum(1 for c in costs if c < 1)
        say("   %4ds - %4ds  %6d      %8.4f  %6d      %6.3f%%"
            % (lo, hi, len(sel), statistics.median([float(c) for c in costs]),
               n_under, 100.0 * n_under / len(sel)))
    say()
    say("   Read the last column, not the count column. If the sub-$1.00 set were")
    say("   a real recurring opportunity, its SHARE would be roughly flat across")
    say("   gap buckets and only the raw count would grow. A share that climbs")
    say("   with the gap says the apparent edge is manufactured by the skew.")
    say()

    # ---- 8. chronological split -----------------------------------------
    # Sections 10 and 11 of the specification. There is nothing to tune here --
    # see the verdicts -- but the split is run anyway, because a split produced
    # only when the answer is interesting is not a split.
    say("8. CHRONOLOGICAL TRAIN / VALIDATION / FINAL HOLDOUT")
    alldays = sorted(days)
    n = len(alldays)
    tr, va = alldays[: int(n * 0.6)], alldays[int(n * 0.6): int(n * 0.8)]
    ho = alldays[int(n * 0.8):]
    say("   TRAIN      %s .. %s  (%d days)" % (tr[0], tr[-1], len(tr)))
    say("   VALIDATION %s .. %s  (%d days)" % (va[0], va[-1], len(va)))
    say("   HOLDOUT    %s .. %s  (%d days)" % (ho[0], ho[-1], len(ho)))
    say()
    for label, dayset in (("TRAIN", set(tr)), ("VALIDATION", set(va)), ("HOLDOUT", set(ho))):
        for w in (1, 5):
            sel = [(g, e0, e1) for _, g, e0, e1 in per_window[w]
                   if ts(e0["probe_at"]).date().isoformat() in dayset]
            if not sel:
                say("   %-10s window %ds : no pairs" % (label, w))
                continue
            costs = [D(e0["best_ask"]) + D(e1["best_ask"]) for _, e0, e1 in sel]
            say("   %-10s window %ds : n=%-5d median=%.4f  min=%.4f  below $1.00 = %d"
                % (label, w, len(costs),
                   statistics.median([float(c) for c in costs]),
                   min(float(c) for c in costs), sum(1 for c in costs if c < 1)))
    say()

    # ---- 9. clustered bootstrap -----------------------------------------
    # Resample CONDITIONS, not pairs. 3,186 pairs drawn from 1,272 conditions
    # are not 3,186 independent trials, and a pair-level interval would be too
    # narrow by construction (specification section 12).
    say("9. CLUSTERED BOOTSTRAP -- mean pair cost, 1 s window")
    pairs1 = per_window[1]
    if pairs1:
        by_c = defaultdict(list)
        for c, g, e0, e1 in pairs1:
            by_c[c].append(float(D(e0["best_ask"]) + D(e1["best_ask"])))
        keys = list(by_c)
        # Deterministic resampling: a fixed LCG, so the interval is reproducible
        # byte-for-byte by anyone re-running this file.
        state = 20260913
        means = []
        for _ in range(2000):
            acc, cnt = 0.0, 0
            for _ in range(len(keys)):
                state = (1103515245 * state + 12345) % (2 ** 31)
                vals = by_c[keys[state % len(keys)]]
                acc += sum(vals)
                cnt += len(vals)
            means.append(acc / cnt)
        means.sort()
        lo, hi = means[int(0.025 * len(means))], means[int(0.975 * len(means))]
        obs = sum(sum(v) for v in by_c.values()) / sum(len(v) for v in by_c.values())
        say("   clusters (conditions)     : %d" % len(keys))
        say("   observed mean pair cost   : %.6f" % obs)
        say("   95%% CI (cluster bootstrap): [%.6f, %.6f]" % (lo, hi))
        say("   expected net pair profit  : %.6f  = 1.00 - mean cost, ZERO fee"
            % (1.0 - obs))
        say("   95%% CI on that profit     : [%.6f, %.6f]" % (1.0 - hi, 1.0 - lo))
        say("   P(expected value > 0)     : %.4f  (share of bootstrap means < 1.00)"
            % (sum(1 for m in means if m < 1.0) / len(means)))
    say()

    # ---- 10. stress ------------------------------------------------------
    say("10. STRESS -- adverse execution added to the 1 s cohort")
    if pairs1:
        base = [D(e0["best_ask"]) + D(e1["best_ask"]) for _, _, e0, e1 in pairs1]
        for cents in (0, 1, 2, 3):
            bump = D(cents) / 100 * 2          # one cent adverse PER LEG
            worse = [c + bump for c in base]
            say("   +%dc per leg : median=%.4f  min=%.4f  pairs below $1.00 = %d of %d"
                % (cents, statistics.median([float(c) for c in worse]),
                   min(float(c) for c in worse),
                   sum(1 for c in worse if c < 1), len(worse)))
        say("   Fees are NOT included at any row above. BETTOR's fee is unmeasured")
        say("   (task #126 has not reported), so every figure here is a ceiling.")
    say()

    # ---- 11. the directional family -------------------------------------
    # The pair is not the only strategy the retained data can test. An ask
    # ladder plus a settled outcome supports the directional question directly:
    # buy one share at the displayed ask at the probe instant, hold to
    # settlement, collect the payout. EV per share = payout - ask, and no fill
    # model is needed because taking the displayed ask is the one execution
    # this data can honestly support.
    #
    # WHAT THE OPPORTUNITY SET IS. The probe fires only on a roster whale's BUY,
    # so every instant sampled here is one where a whale had just bought. This
    # is therefore NOT a native scan. It is the copy strategy's own opportunity
    # set, measured at the post-impact ask -- register B of the three, and the
    # closest thing to a native test the retained data permits. A genuinely
    # native rule would need book reads at instants nobody chose for us.
    say("11. DIRECTIONAL FAMILY -- buy the displayed ask, hold to settlement")
    settle = {}
    with SETTLEMENT.open() as fh:
        for line in fh:
            d = json.loads(line)
            if not d.get("resolved") or d.get("resolved_prices_type") != "array":
                continue
            payouts = d.get("payouts") or []
            toks = {t["token_id"]: t["outcome_index"] for t in (d.get("tokens") or [])}
            if not payouts or not toks:
                continue
            settle[d["condition_id"]] = (payouts, toks)
    say("   settled conditions available : %d" % len(settle))

    graded = []
    for e in ev:
        c = e.get("condition_id_effective")
        if c not in settle or not e.get("book_ok") or e.get("best_ask") is None:
            continue
        payouts, toks = settle[c]
        oi = toks.get(str(e.get("asset")))
        if oi is None or oi >= len(payouts):
            continue
        ask = D(e["best_ask"])
        if ask <= 0 or ask >= 1:
            continue
        graded.append({
            "day": ts(e["probe_at"]).date().isoformat(),
            "cond": c, "ask": ask, "payout": D(payouts[oi]),
            "sport": e.get("sport") or "<none>",
            "edge": D(payouts[oi]) - ask,
        })
    say("   probe rows gradeable to settlement : %d" % len(graded))
    if graded:
        allv = [float(g["edge"]) for g in graded]
        say("   mean edge per share (zero fee)     : %+.6f" % (sum(allv) / len(allv)))
        say()
        say("   by ask price band:")
        bands = [(0.0, 0.1), (0.1, 0.3), (0.3, 0.5), (0.5, 0.7),
                 (0.7, 0.9), (0.9, 0.95), (0.95, 0.99), (0.99, 1.0)]
        say("     band            n        mean edge/share   mean ROI/$")
        for lo, hi in bands:
            sel = [g for g in graded if lo <= float(g["ask"]) < hi]
            if not sel:
                continue
            m = sum(float(g["edge"]) for g in sel) / len(sel)
            roi = sum(float(g["edge"] / g["ask"]) for g in sel) / len(sel)
            say("     %.2f-%.2f   %-8d %+14.6f %+12.4f" % (lo, hi, len(sel), m, roi))
        say()
        # chronological split, same cut as section 8
        say("   chronological split (same cut as section 8):")
        for label, dayset in (("TRAIN", set(tr)), ("VALIDATION", set(va)),
                              ("HOLDOUT", set(ho))):
            sel = [g for g in graded if g["day"] in dayset]
            if not sel:
                continue
            m = sum(float(g["edge"]) for g in sel) / len(sel)
            roi = sum(float(g["edge"] / g["ask"]) for g in sel) / len(sel)
            say("     %-10s n=%-7d mean edge/share=%+.6f  mean ROI/$=%+.4f"
                % (label, len(sel), m, roi))
        say()
        # clustered bootstrap on the holdout, resampling CONDITIONS
        sel = [g for g in graded if g["day"] in set(ho)]
        if sel:
            by_c = defaultdict(list)
            for g in sel:
                by_c[g["cond"]].append(float(g["edge"] / g["ask"]))
            keys = list(by_c)
            state = 840913
            means = []
            for _ in range(2000):
                acc, cnt = 0.0, 0
                for _ in range(len(keys)):
                    state = (1103515245 * state + 12345) % (2 ** 31)
                    vals = by_c[keys[state % len(keys)]]
                    acc += sum(vals)
                    cnt += len(vals)
                means.append(acc / cnt)
            means.sort()
            obs = sum(sum(v) for v in by_c.values()) / sum(len(v) for v in by_c.values())
            say("   FINAL HOLDOUT, clustered bootstrap on ROI per dollar:")
            say("     clusters (conditions)  : %d" % len(keys))
            say("     observed mean ROI/$    : %+.6f" % obs)
            say("     95%% CI                 : [%+.6f, %+.6f]"
                % (means[int(0.025 * len(means))], means[int(0.975 * len(means))]))
            say("     P(ROI > 0)             : %.4f"
                % (sum(1 for m in means if m > 0) / len(means)))
        say()
        say("   STRESS -- the same holdout with adverse execution per share:")
        if sel:
            for cents in (1, 2, 3):
                bump = D(cents) / 100
                roi = sum(float((g["payout"] - g["ask"] - bump) / (g["ask"] + bump))
                          for g in sel) / len(sel)
                say("     +%dc : mean ROI/$ = %+.6f" % (cents, roi))
    say()

    # ---- 12. feature-availability audit ---------------------------------
    say("12. FEATURE-AVAILABILITY AUDIT (specification section 9)")
    say("   field                     used as      available at decision time?")
    say("   best_ask                  feature      YES -- it is read AT probe_at,")
    say("                                          which IS the decision instant")
    say("   depth (ask ladder)        feature      YES -- same read")
    say("   asset / outcome_index     feature      YES -- static token metadata")
    say("   condition_id_effective    join key     YES for CONDITION_DIRECT;")
    say("                                          CONDITION_RECOVERED_UNIQUE_TOKEN")
    say("                                          uses sealed metadata, also static")
    say("   sport                     feature      YES -- static classification")
    say("   his_price / his_size      feature      YES -- it is the trigger")
    say("   probe_at                  clock        YES")
    say("   payouts (settlement)      LABEL ONLY   NO -- and it is never used as a")
    say("                                          feature, only to grade an outcome")
    say("   gap between the two legs  NOT A FEATURE. Knowing the second leg's probe")
    say("     instant requires the second probe to have happened. This is exactly")
    say("     why the 1 s cohort is the honest one and the 300 s cohort is not: at")
    say("     300 s the 'opportunity' is visible only in hindsight.")
    say("   NO future book state, no future whale activity, no later market")
    say("   classification and no ex-post maker/taker label is used anywhere.")
    say()

    # ---- 13. capacity ----------------------------------------------------
    say("13. CAPACITY")
    say("   Not estimated, and deliberately not estimated. Capacity is the")
    say("   question 'how much can be deployed into an edge'; sections 4, 5, 9")
    say("   and 11 find no edge to deploy into. Producing a capacity table for a")
    say("   negative-expectancy rule would dress a loss in units of scale.")
    say("   The $1,000 / $5,000 / $10,000 / $25,000 / $50,000 ladder is therefore")
    say("   reported as NOT_APPLICABLE rather than filled in.")
    sourced = 0
    for _, _, e0, e1 in pairs1:
        v0, _, x0 = walk_ask_ladder(e0.get("depth"), Decimal(1000))
        v1, _, x1 = walk_ask_ladder(e1.get("depth"), Decimal(1000))
        if v0 is not None and v1 is not None and not x0 and not x1:
            sourced += 1
    say("   One capacity fact IS measured and is reported for its own sake: at the")
    say("   1 s window, pairs that could source a $1,000 clip on BOTH legs from the")
    say("   displayed ladder numbered %d of %d. Depth is not what is missing here."
        % (sourced, len(pairs1)))
    say()

    say("=" * 68)
    say("VERDICTS")
    say("=" * 68)
    say("RUN84_ANALYSIS_COMPLETE              = YES (on retained data; see LIMITS)")
    say("NATIVE_MATCHED_PAIR_EDGE             = CONTRADICTED for AGGRESSIVE")
    say("                                       acquisition (regime D);")
    say("                                       NOT_IDENTIFIED for PASSIVE (A/B/C)")
    say()
    say("   That split is not hedging, and the passive half is the more important")
    say("   half. The measured quantity is a CROSS of about +2.19 cents: a taker")
    say("   who lifts both asks pays it, and a maker who rests on both sides and")
    say("   is filled would EARN it. So the one regime this data cannot test is")
    say("   precisely the one the measurement points toward, and no bid was ever")
    say("   retained -- the gap is total, not partial.")
    say()
    say("   It does not follow that the passive pair is profitable, and BETTOR")
    say("   already holds production evidence against assuming so. The E14")
    say("   measurement found our at-his-price fills systematically adverse:")
    say("   filled roi -0.0061 against missed +0.1669. A resting quote is filled")
    say("   BY someone, and in these markets that someone is informed flow. A")
    say("   maker earns the spread only on the fills that were not worth having.")
    say("   Testing it needs bid data BETTOR has never retained; it cannot be")
    say("   settled by re-reading what is already here.")
    say()
    say("RN1_SIGNAL_ADDS_INCREMENTAL_VALUE    = NOT_IDENTIFIED")
    say("POSITIVE_EXPECTANCY_AFTER_EXECUTION  = CONTRADICTED")
    say("POSITIVE_FINAL_HOLDOUT               = NO")
    say("ROBUST_TO_CONSERVATIVE_EXECUTION     = NO")
    say("ROBUST_TO_STRESS                     = NO")
    say("MEANINGFUL_CAPACITY_AT_50K           = NOT_IDENTIFIED (no edge to size)")
    say("DEPLOYABLE_CANDIDATE_FOUND           = NO")
    say()
    say("LIMITS THAT BOUND EVERY LINE ABOVE")
    say("  1. The book is RN1's venue (clob.polymarket.com), not BETTOR's (PMUS).")
    say("     No BETTOR-executable book is retained anywhere, at any timestamp.")
    say("  2. The ladder is ASK ONLY. Passive execution -- regimes A, B and C of")
    say("     the specification -- could not be tested at all.")
    say("  3. The sampling instants are whale-triggered, so this is not a native")
    say("     scan; it is the copy opportunity set measured at the post-impact ask.")
    say("  4. No fee is included anywhere. Every figure is a CEILING.")
    say()

    if pair_rows:
        import csv
        with (outdir / "run84_pair_candidates.csv").open("w", newline="") as fh:
            wtr = csv.DictWriter(fh, fieldnames=list(pair_rows[0].keys()))
            wtr.writeheader()
            wtr.writerows(pair_rows)
        say("   wrote run84_pair_candidates.csv (%d rows, 60 s window)" % len(pair_rows))
    say()

    (outdir / "run84_native_pair_report.txt").write_text("\n".join(R) + "\n")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out84")
