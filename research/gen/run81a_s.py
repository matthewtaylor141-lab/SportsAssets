#!/usr/bin/env python3
"""RUN 81A-S -- FIRST_RETAINED_OBSERVATION_DRAG, replicated on U2_SNAPSHOT_V1.

PRICE DETERIORATION AT THE FIRST RETAINED EXACT BOOK OBSERVATION.

    python3 research/gen/run81a_s.py [research/snapshots/u2_events_v1.jsonl[.gz]]

NO DATABASE CONNECTION. That is the point, and it is stronger than a sealed-SQL
guard: psql can load a local file only through \\copy (barred by the runner) or a
temp table (barred by the read-only transaction), so a sealed SQL analysis would
have had to reach back to the live tables -- the exact thing sealing forbids.
This script opens one committed file and nothing else. There is no live table in
scope to fall back to, so the seal is a property of the program rather than a
rule someone has to keep.

WHAT IT IS NOT. RUN 81A-S is NOT a re-run of RUN 81A ORIGINAL and its numbers are
NOT expected to match. 81A ORIGINAL measured the live U2 population that existed
between 02:04 and 02:10Z on 2026-09-12; that event set was never materialised and
copy_probes retention has deleted rows oldest-first since, so it is NOT
RECONSTRUCTIBLE. 81A-S measures U2_SNAPSHOT_V1, observed at
U2_SNAPSHOT_V1_DRAWN_AT. Differences are expected and are reported, never
reconciled away. 81A-S is the PERMANENT REPRODUCIBLE BASELINE; 81A ORIGINAL is a
historical measurement that stands on its own terms.

Every rule below is carried verbatim from research/rn1_run81a_drag.sql:

  * p_h is the trade's own price; the probe's his_price is a cross-check only.
  * A row enters the top-of-book population if best_ask is present, > 0 and <= 1.
    THE DEPTH REQUIREMENT NEVER TRIMS THAT POPULATION -- that separation is what
    keeps "the market had already moved" distinguishable from "our proposed size
    would have walked the book".
  * q is in SHARES: Q_A = 0.10 * size, Q_B = size, Q_C = 1000 / p_h.
  * The walk is price-ascending with the stored array index as tie-break, so no
    result depends on the stored order.
  * MISSING DEPTH DOES NOT BOUND ANYTHING. A row whose retained depth cannot
    cover q is DEPTH_EXHAUSTED and is NOT_IDENTIFIABLE_FROM_RETAINED_DEPTH: no
    extrapolated last price, no imputed VWAP, never folded into an aggregate as
    if known. Supported-only aggregates print their retention beside them.
  * Negative drag stays negative. Nothing is floored anywhere.
  * Arithmetic is IEEE double, as Postgres float8 was, so the two runs differ by
    population and not by numeric model.
"""
import gzip
import json
import sys
from collections import defaultdict

# RUN 81A ORIGINAL's three controls, for the delta. Historical, not a target.
ORIG_EVENTS, ORIG_CONDITIONS, ORIG_NOTIONAL = 214651, 17755, 48327388.19

ONLINE = ("chain", "poll", "s1")
SCENARIOS = ("Q_A", "Q_B", "Q_C")


def pct(vals, p):
    """percentile_cont: linear interpolation on the sorted values, as Postgres."""
    if not vals:
        return None
    v = sorted(vals)
    if len(v) == 1:
        return v[0]
    i = p * (len(v) - 1)
    lo = int(i)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (i - lo)


def stratum(source):
    return {"chain": "A_CHAIN", "poll": "A_POLL", "s1": "A_S1",
            "backfill": "A_BACKFILL_DIAGNOSTIC"}.get(source,
                                                     "HALT_UNKNOWN_LANE_" + str(source))


def walk(levels, q):
    """Cost of taking q shares, price-ascending, and the total retained size.

    Returns (depth_shares, cost) where cost is None when the retained book
    cannot cover q -- never a partial cost dressed up as a full one.
    """
    total = sum(sh for _, sh in levels)
    if total < q or q <= 0:
        return total, None
    cost, taken = 0.0, 0.0
    for px, sh in sorted(levels, key=lambda t: t[0]):
        if taken >= q:
            break
        use = min(sh, q - taken)
        cost += px * use
        taken += use
    return total, cost


def main(path):
    opener = gzip.open if path.endswith(".gz") else open
    rows = []
    with opener(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    n_events = len(rows)
    conditions = {r["condition_id"] for r in rows if r["condition_id"]}
    notional = sum(float(r["notional"]) for r in rows)

    print("== RUN 81A-S -- SNAPSHOT REPLICATION on U2_SNAPSHOT_V1 ==")
    print("== PRICE DETERIORATION AT THE FIRST RETAINED EXACT BOOK OBSERVATION ==")
    print("== No settlement. No latency. No database. mirror_live=false. ==\n")

    print("== 0. POPULATION vs RUN 81A ORIGINAL -- reported, never reconciled ==")
    print(f"{'':28} {'81A ORIGINAL':>16} {'U2_SNAPSHOT_V1':>16} {'delta':>14}")
    print(f"{'events':28} {ORIG_EVENTS:>16,} {n_events:>16,} "
          f"{n_events - ORIG_EVENTS:>+14,}")
    print(f"{'conditions':28} {ORIG_CONDITIONS:>16,} {len(conditions):>16,} "
          f"{len(conditions) - ORIG_CONDITIONS:>+14,}")
    print(f"{'source notional':28} {ORIG_NOTIONAL:>16,.2f} {notional:>16,.2f} "
          f"{notional - ORIG_NOTIONAL:>+14,.2f}")
    print("RUN 81A ORIGINAL measured a population that is NOT RECONSTRUCTIBLE.")
    print("These are two different populations, not two attempts at one.\n")

    # ---- derive per row -------------------------------------------------
    lanes = defaultdict(int)
    for r in rows:
        st = stratum(r["source"])
        lanes[st] += 1
        r["_stratum"] = st
        ask = r["best_ask"]
        r["_ask"] = float(ask) if ask is not None else None
        r["_ph"] = float(r["price"])
        r["_size"] = float(r["size"])
        r["_notional"] = float(r["notional"])
        r["_ask_valid"] = (r["_ask"] is not None and 0 < r["_ask"] <= 1)
        r["_q_ok"] = (0 < r["_ph"] < 1 and r["_size"] > 0)
        r["_levels"] = [(float(p), float(s)) for p, s in (r["depth"] or [])
                        if p is not None and s is not None]

    halt = [k for k in lanes if k.startswith("HALT_")]
    if halt:
        print(f"== HALT -- lane outside the eligibility matrix: {halt} ==\n")

    # ---- 1. selection ladder -------------------------------------------
    print("== 1. THE SELECTION LADDER, per lane ==")
    print(f"{'stratum':22}{'events':>9}{'valid ask':>11}{'q defin.':>10}"
          f"{'sup Q_A':>9}{'sup Q_B':>9}{'sup Q_C':>9}")
    ladder = defaultdict(lambda: defaultdict(int))
    for r in rows:
        st = r["_stratum"]
        ladder[st]["n"] += 1
        if r["_ask_valid"]:
            ladder[st]["ask"] += 1
            if r["_q_ok"]:
                ladder[st]["q"] += 1
                total = sum(sh for _, sh in r["_levels"])
                for sc, q in (("Q_A", 0.10 * r["_size"]), ("Q_B", r["_size"]),
                              ("Q_C", 1000.0 / r["_ph"])):
                    if total >= q:
                        ladder[st]["sup" + sc] += 1
    order = [s for s in ("A_CHAIN", "A_POLL", "A_S1", "A_BACKFILL_DIAGNOSTIC")
             if s in ladder]
    for st in order:
        d = ladder[st]
        print(f"{st:22}{d['n']:>9,}{d['ask']:>11,}{d['q']:>10,}"
              f"{d['supQ_A']:>9,}{d['supQ_B']:>9,}{d['supQ_C']:>9,}")
    comb = defaultdict(int)
    for st in order:
        if st != "A_BACKFILL_DIAGNOSTIC":
            for k, v in ladder[st].items():
                comb[k] += v
    print(f"{'A_ONLINE_COMBINED':22}{comb['n']:>9,}{comb['ask']:>11,}"
          f"{comb['q']:>10,}{comb['supQ_A']:>9,}{comb['supQ_B']:>9,}"
          f"{comb['supQ_C']:>9,}")
    print()

    # ---- 2. top of book, ALL rows with a valid ask ----------------------
    print("== 2. TOP_OF_BOOK_MOVE -- all rows with a valid ask, no depth gate ==")
    tob = defaultdict(list)
    tmeta = defaultdict(lambda: {"cond": set(), "notional": 0.0})
    for r in rows:
        if not r["_ask_valid"]:
            continue
        cps = (r["_ask"] - r["_ph"]) * 100.0
        for key in (r["_stratum"],) + (("A_ONLINE_COMBINED",)
                                       if r["source"] in ONLINE else ()):
            tob[key].append(cps)
            tmeta[key]["cond"].add(r["condition_id"])
            tmeta[key]["notional"] += r["_notional"]
    hdr = (f"{'stratum':22}{'events':>9}{'conds':>8}{'src notional':>16}"
           f"{'mean':>9}{'p50':>9}{'p10':>9}{'p25':>9}{'p75':>9}{'p90':>9}"
           f"{'%pos':>8}{'%zero':>8}{'%neg':>8}")
    print(hdr)
    for key in order + ["A_ONLINE_COMBINED"]:
        v = tob.get(key)
        if not v:
            continue
        n = len(v)
        print(f"{key:22}{n:>9,}{len(tmeta[key]['cond']):>8,}"
              f"{tmeta[key]['notional']:>16,.2f}"
              f"{sum(v)/n:>9.4f}{pct(v,.50):>9.4f}{pct(v,.10):>9.4f}"
              f"{pct(v,.25):>9.4f}{pct(v,.75):>9.4f}{pct(v,.90):>9.4f}"
              f"{100*sum(1 for x in v if x>0)/n:>8.3f}"
              f"{100*sum(1 for x in v if x==0)/n:>8.3f}"
              f"{100*sum(1 for x in v if x<0)/n:>8.3f}")
    print()

    # ---- 3-5. per scenario ----------------------------------------------
    exhausted = defaultdict(lambda: {"n": 0, "notional": 0.0, "req": 0.0,
                                     "obs": 0.0, "cov": []})
    sup = defaultdict(lambda: {"tot": [], "tob": [], "dep": [],
                               "tob_usd": 0.0, "dep_usd": 0.0, "tot_usd": 0.0,
                               "cond": set(), "notional": 0.0, "req": 0})
    closure = defaultdict(lambda: {"w": 0, "viol": 0, "worst": 0.0,
                                   "a": 0.0, "b": 0.0, "c": 0.0})
    for r in rows:
        if not (r["_ask_valid"] and r["_q_ok"]):
            continue
        for sc, q in (("Q_A", 0.10 * r["_size"]), ("Q_B", r["_size"]),
                      ("Q_C", 1000.0 / r["_ph"])):
            total, cost = walk(r["_levels"], q)
            st = r["_stratum"]
            keys = [(sc, st)] + ([(sc, "A_ONLINE_COMBINED")]
                                 if r["source"] in ONLINE else [])
            for k in keys:
                sup[k]["req"] += 1
            if cost is None:
                for k in keys:
                    e = exhausted[k]
                    e["n"] += 1
                    e["notional"] += r["_notional"]
                    e["req"] += q
                    e["obs"] += total
                    e["cov"].append(total / q if q else 0.0)
                continue
            vwap = cost / q
            tob_c = (r["_ask"] - r["_ph"]) * 100.0
            dep_c = (vwap - r["_ask"]) * 100.0
            tot_c = (vwap - r["_ph"]) * 100.0
            tob_u = (r["_ask"] - r["_ph"]) * q
            dep_u = (vwap - r["_ask"]) * q
            tot_u = (vwap - r["_ph"]) * q
            for k in keys:
                s = sup[k]
                s["tot"].append(tot_c)
                s["tob"].append(tob_c)
                s["dep"].append(dep_c)
                s["tob_usd"] += tob_u
                s["dep_usd"] += dep_u
                s["tot_usd"] += tot_u
                s["cond"].add(r["condition_id"])
                s["notional"] += r["_notional"]
            cl = closure[sc]
            cl["w"] += 1
            gap = abs(tob_u + dep_u - tot_u)
            cl["worst"] = max(cl["worst"], gap)
            if gap > 0.005:
                cl["viol"] += 1
            cl["a"] += tob_u
            cl["b"] += dep_u
            cl["c"] += tot_u

    print("== 3. DEPTH SUPPORT, per scenario and lane ==")
    print(f"{'scenario':10}{'stratum':22}{'requested':>11}{'supported':>11}"
          f"{'exhausted':>11}{'retention%':>12}")
    for sc in SCENARIOS:
        for st in order + ["A_ONLINE_COMBINED"]:
            k = (sc, st)
            if not sup[k]["req"]:
                continue
            req = sup[k]["req"]
            ex = exhausted[k]["n"]
            print(f"{sc:10}{st:22}{req:>11,}{req-ex:>11,}{ex:>11,}"
                  f"{100*(req-ex)/req:>12.3f}")
    print()

    print("== 4. DRAG ON THE DEPTH_SUPPORTED_SUBSET ==")
    print(f"{'scenario':9}{'stratum':22}{'events':>9}{'conds':>8}"
          f"{'mean tot':>10}{'p50':>9}{'p10':>9}{'p90':>9}"
          f"{'mean ToB':>10}{'mean dep':>10}"
          f"{'ToB $':>15}{'depth $':>15}{'total $':>15}"
          f"{'%pos':>8}{'%zero':>8}{'%neg':>8}")
    for sc in SCENARIOS:
        for st in order + ["A_ONLINE_COMBINED"]:
            k = (sc, st)
            s = sup[k]
            v = s["tot"]
            if not v:
                continue
            n = len(v)
            print(f"{sc:9}{st:22}{n:>9,}{len(s['cond']):>8,}"
                  f"{sum(v)/n:>10.4f}{pct(v,.50):>9.4f}{pct(v,.10):>9.4f}"
                  f"{pct(v,.90):>9.4f}"
                  f"{sum(s['tob'])/n:>10.4f}{sum(s['dep'])/n:>10.4f}"
                  f"{s['tob_usd']:>15,.2f}{s['dep_usd']:>15,.2f}"
                  f"{s['tot_usd']:>15,.2f}"
                  f"{100*sum(1 for x in v if x>0)/n:>8.3f}"
                  f"{100*sum(1 for x in v if x==0)/n:>8.3f}"
                  f"{100*sum(1 for x in v if x<0)/n:>8.3f}")
    print()

    print("== 5. CLOSURE ASSERTION -- per event and in aggregate ==")
    print(f"{'scenario':10}{'witnesses':>11}{'violations':>12}"
          f"{'worst gap':>14}{'agg gap':>14}  verdict")
    for sc in SCENARIOS:
        cl = closure[sc]
        agg = abs(cl["a"] + cl["b"] - cl["c"])
        if cl["w"] == 0:
            verdict = "NOT TESTED -- zero witnesses"
        elif cl["viol"] == 0 and agg <= 0.005:
            verdict = "CLOSES"
        else:
            verdict = "FAILS -- the decomposition does not close"
        print(f"{sc:10}{cl['w']:>11,}{cl['viol']:>12,}"
              f"{cl['worst']:>14.8f}{agg:>14.6f}  {verdict}")
    print()

    print("== 6. DEPTH_EXHAUSTED -- NOT_IDENTIFIABLE_FROM_RETAINED_DEPTH ==")
    print(f"{'scenario':10}{'stratum':22}{'events':>9}{'src notional':>16}"
          f"{'req shares':>18}{'obs shares':>18}{'coverage':>11}{'median row':>12}")
    for sc in SCENARIOS:
        for st in order + ["A_ONLINE_COMBINED"]:
            e = exhausted[(sc, st)]
            if not e["n"]:
                continue
            print(f"{sc:10}{st:22}{e['n']:>9,}{e['notional']:>16,.2f}"
                  f"{e['req']:>18,.2f}{e['obs']:>18,.2f}"
                  f"{(e['obs']/e['req'] if e['req'] else 0):>11.6f}"
                  f"{pct(e['cov'], .50):>12.6f}")
    print()

    # ---- 7. segments -----------------------------------------------------
    print("== 7. SEGMENTS -- decision-time-valid variables only, online lanes ==")

    def band_price(p):
        for hi, name in ((0.10, "p1 [0.00,0.10)"), (0.25, "p2 [0.10,0.25)"),
                         (0.50, "p3 [0.25,0.50)"), (0.75, "p4 [0.50,0.75)"),
                         (0.90, "p5 [0.75,0.90)")):
            if p < hi:
                return name
        return "p6 [0.90,1.00)"

    def band_size(n):
        for hi, name in ((10, "n1 <$10"), (100, "n2 $10-100"),
                         (1000, "n3 $100-1k"), (10000, "n4 $1k-10k")):
            if n < hi:
                return name
        return "n5 >=$10k"

    seg = defaultdict(lambda: {"tob": [], "qa": [], "n": 0, "notional": 0.0})
    for r in rows:
        if r["source"] not in ONLINE or not (r["_ask_valid"] and r["_q_ok"]):
            continue
        tob_c = (r["_ask"] - r["_ph"]) * 100.0
        q = 0.10 * r["_size"]
        _, cost = walk(r["_levels"], q)
        qa = (cost / q - r["_ph"]) * 100.0 if cost is not None else None
        for kind, val in (("sport", r["sport"] or "(null)"),
                          ("price_band", band_price(r["_ph"])),
                          ("size_band", band_size(r["_notional"]))):
            s = seg[(kind, val)]
            s["n"] += 1
            s["notional"] += r["_notional"]
            s["tob"].append(tob_c)
            if qa is not None:
                s["qa"].append(qa)
    print(f"{'kind':12}{'segment':18}{'events':>9}{'src notional':>16}"
          f"{'mean ToB':>10}{'p50 ToB':>10}{'Q_A sup':>9}{'Q_A ret%':>10}"
          f"{'Q_A mean':>10}{'Q_A p50':>10}")
    for (kind, val) in sorted(seg):
        s = seg[(kind, val)]
        if kind == "sport" and s["n"] < 200:
            continue
        qa = s["qa"]
        print(f"{kind:12}{val:18}{s['n']:>9,}{s['notional']:>16,.2f}"
              f"{sum(s['tob'])/s['n']:>10.4f}{pct(s['tob'],.50):>10.4f}"
              f"{len(qa):>9,}{100*len(qa)/s['n']:>10.2f}"
              f"{(sum(qa)/len(qa) if qa else 0):>10.4f}"
              f"{(pct(qa,.50) if qa else 0):>10.4f}")
    print()
    print("== RUN 81A-S ENDS. Sealed inputs only. No database. mirror_live=false. ==")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "research/snapshots/u2_events_v1.jsonl"
    main(src)
