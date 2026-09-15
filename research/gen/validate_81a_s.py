#!/usr/bin/env python3
"""INDEPENDENT VALIDATION of RUN 81A-S's arithmetic, on the same sealed bytes.

    python3 research/gen/validate_81a_s.py [u2_events_v1.jsonl[.gz]]

WHY THIS EXISTS, AND WHY IT IS A SECOND IMPLEMENTATION RATHER THAN AN ASSERTION.
RUN 81A ORIGINAL is NOT the oracle for 81A-S: it measured a different population
(copy_probes retention deletes between draws), so a difference against it cannot
distinguish "the Python is wrong" from "the population moved". Those are separate
questions and the second one cannot answer the first.

So the implementation is validated against a SECOND INDEPENDENT COMPUTATION over
the SAME sealed population. Deliberately different where it can be, so that a
shared mistake is less likely to cancel out:

  * DECIMAL arithmetic here, IEEE double in 81A-S. A transcription error in a
    formula survives a change of numeric type; an arithmetic slip usually does
    not, and the two agreeing to a cent across ~200k rows is real evidence.
  * A PREFIX-SUM + BISECT walk here, an accumulate-until-filled loop in 81A-S.
    Same definition, different control flow, so an off-by-one in one is not
    reproduced by the other.
  * Independent re-derivation of the population predicates from the sealed
    fields, not a shared helper.

WHAT IT CHECKS, per scenario, on every FULLY DEPTH SUPPORTED row:

    TOP_OF_BOOK_MOVE_DOLLARS = (best_ask - p_h)      * q
    DEPTH_SLIPPAGE_DOLLARS   = (depth_vwap - best_ask) * q
    TOTAL_DRAG_DOLLARS       = (depth_vwap - p_h)    * q
    TOP_OF_BOOK + DEPTH      = TOTAL

The closure identity is algebraically trivial, which is exactly why it is worth
asserting per row: it cannot fail for a mathematical reason, so if it ever fails
the cause is a coding one, and that is the failure this file exists to catch.

It also emits an EVENT IDENTITY DIGEST -- sha256 over the sorted probe_ids --
so that any FUTURE run can prove it read the same event set rather than merely
the same totals. RUN 81A ORIGINAL retained no such digest, so it cannot be
compared against; that is stated rather than worked around.
"""
import gzip
import hashlib
import json
import sys
from decimal import Decimal, getcontext

getcontext().prec = 40

SCENARIOS = ("Q_A", "Q_B", "Q_C")
ONLINE = ("chain", "poll", "s1")
D0 = Decimal(0)


def cost_for(levels, q):
    """Prefix-sum + bisect walk. Returns None when the book cannot cover q.

    Deliberately a different control flow from 81A-S's accumulate-until-filled
    loop: same definition, different way of arriving at it.
    """
    if q <= 0:
        return None
    lv = sorted(levels, key=lambda t: t[0])
    cum_sh, cum_cost, running_sh, running_cost = [], [], D0, D0
    for px, sh in lv:
        running_sh += sh
        running_cost += px * sh
        cum_sh.append(running_sh)
        cum_cost.append(running_cost)
    if not cum_sh or cum_sh[-1] < q:
        return None
    lo, hi = 0, len(cum_sh) - 1
    while lo < hi:                      # first index whose cumulative >= q
        mid = (lo + hi) // 2
        if cum_sh[mid] >= q:
            hi = mid
        else:
            lo = mid + 1
    before_sh = cum_sh[lo - 1] if lo else D0
    before_cost = cum_cost[lo - 1] if lo else D0
    return before_cost + lv[lo][0] * (q - before_sh)


def main(path):
    opener = gzip.open if path.endswith(".gz") else open
    tot = {sc: {"n": 0, "tob": D0, "dep": D0, "all": D0,
                "viol": 0, "worst": D0} for sc in SCENARIOS}
    online_tot = {sc: {"n": 0, "tob": D0, "dep": D0, "all": D0}
                  for sc in SCENARIOS}
    n_rows = 0
    n_online = 0
    notional = D0
    ids = []

    with opener(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            n_rows += 1
            ids.append(int(r["probe_id"]))
            notional += Decimal(r["notional"])
            if r["source"] in ONLINE:
                n_online += 1

            ask = r["best_ask"]
            if ask is None:
                continue
            ask = Decimal(ask)
            ph = Decimal(r["price"])
            size = Decimal(r["size"])
            if not (D0 < ask <= 1):
                continue
            if not (D0 < ph < 1 and size > D0):
                continue
            levels = [(Decimal(p), Decimal(s)) for p, s in (r["depth"] or [])
                      if p is not None and s is not None]
            qs = (("Q_A", Decimal("0.10") * size), ("Q_B", size),
                  ("Q_C", Decimal(1000) / ph))
            for sc, q in qs:
                c = cost_for(levels, q)
                if c is None:
                    continue
                vwap = c / q
                tob = (ask - ph) * q
                dep = (vwap - ask) * q
                allv = (vwap - ph) * q
                t = tot[sc]
                t["n"] += 1
                t["tob"] += tob
                t["dep"] += dep
                t["all"] += allv
                gap = abs(tob + dep - allv)
                if gap > Decimal("0.005"):
                    t["viol"] += 1
                if gap > t["worst"]:
                    t["worst"] = gap
                if r["source"] in ONLINE:
                    o = online_tot[sc]
                    o["n"] += 1
                    o["tob"] += tob
                    o["dep"] += dep
                    o["all"] += allv

    digest = hashlib.sha256(
        "\n".join(str(i) for i in sorted(ids)).encode()).hexdigest()

    print("== INDEPENDENT VALIDATION of 81A-S arithmetic (Decimal, bisect walk) ==")
    print(f"rows read                {n_rows:,}")
    print(f"on an online lane        {n_online:,}")
    print(f"source notional          {notional:,.2f}")
    print(f"EVENT_IDENTITY_DIGEST    {digest}")
    print("  (sha256 over the sorted probe_ids; RUN 81A ORIGINAL retained no")
    print("   such digest, so event identity against it CANNOT be proven)")
    print()
    print(f"{'scenario':10}{'supported':>12}{'ToB $':>18}{'depth $':>18}"
          f"{'total $':>18}{'viol':>7}{'worst gap':>14}  closure")
    for sc in SCENARIOS:
        t = tot[sc]
        agg = abs(t["tob"] + t["dep"] - t["all"])
        if t["n"] == 0:
            verd = "NOT TESTED -- zero witnesses"
        elif t["viol"] == 0 and agg <= Decimal("0.005"):
            verd = "CLOSES"
        else:
            verd = "FAILS"
        print(f"{sc:10}{t['n']:>12,}{t['tob']:>18,.2f}{t['dep']:>18,.2f}"
              f"{t['all']:>18,.2f}{t['viol']:>7}{t['worst']:>14.8f}  {verd}")
    print()
    print("A_ONLINE_COMBINED only:")
    for sc in SCENARIOS:
        o = online_tot[sc]
        print(f"  {sc:8}{o['n']:>12,}{o['tob']:>18,.2f}{o['dep']:>18,.2f}"
              f"{o['all']:>18,.2f}")

    out = {"rows": n_rows, "online": n_online, "notional": str(notional),
           "digest": digest,
           "scenarios": {sc: {"n": tot[sc]["n"], "tob": str(tot[sc]["tob"]),
                              "dep": str(tot[sc]["dep"]), "all": str(tot[sc]["all"]),
                              "viol": tot[sc]["viol"]} for sc in SCENARIOS},
           "online": {sc: {"n": online_tot[sc]["n"],
                           "tob": str(online_tot[sc]["tob"]),
                           "dep": str(online_tot[sc]["dep"]),
                           "all": str(online_tot[sc]["all"])}
                      for sc in SCENARIOS}}
    with open("/tmp/validate_81a_s.json", "w") as fh:
        json.dump(out, fh, indent=1)
    print("\nwritten: /tmp/validate_81a_s.json")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1
         else "research/snapshots/u2_events_v1.jsonl.gz")
