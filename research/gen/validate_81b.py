#!/usr/bin/env python3
"""INDEPENDENT VALIDATION OF RUN 81B, over the same sealed bytes.

    python3 research/gen/validate_81b.py

WHY A SECOND IMPLEMENTATION EXISTS. In 81A-S this habit paid for itself: the
validator caught a defect the reference run could not see from inside itself
(SQL's count(DISTINCT x) ignores NULL and a Python set does not, which inflated
every condition count by one). A number that two independently written programs
produce from the same bytes is worth more than a number one program produces
twice.

WHAT IS DELIBERATELY DIFFERENT HERE:

  1. ARITHMETIC. Decimal at 50 digits over the retained text, not IEEE double.
     The reference run uses doubles on purpose, so 81A-S and 81B differ by
     population and not by numeric model; this file exists to say how much of
     the reference answer is a property of the numeric model. Differences are
     reported as agreement-to-a-stated-precision, never reconciled away.

  2. CONTROL FLOW OF THE DEPTH WALK. The reference sorts and accumulates; this
     one builds a prefix-sum over the sorted levels and bisects for the
     boundary level.

  3. THE DEPTH ALGEBRA. The reference forms depth_vwap = cost/q and then
     computes q*(vwap - p_h). This file NEVER FORMS A VWAP: it works from the
     walk cost directly, so
         TOTAL = cost - q*p_h
         DEPTH = cost - q*ask
         TOP   = q*ask - q*p_h
     If the reference's division-then-multiplication round trip lost anything,
     these two paths disagree and this file says so.

  4. THE POPULATION. Re-derived here from the raw sealed JSON rather than
     imported, so membership is checked and not assumed. Agreement is proved by
     an EVENT_IDENTITY_DIGEST over the sorted probe_ids -- matching totals are
     not evidence of matching rows.

NO DATABASE. Three committed files and nothing else.
"""
import bisect
import gzip
import hashlib
import json
from decimal import Decimal, getcontext

getcontext().prec = 50

EV = "research/snapshots/u2_events_v1.jsonl.gz"
ST = "research/snapshots/settlement_v1.jsonl"
W_GZ = "research/snapshots/u0_timing_witness_v1.jsonl.gz"

ZERO = Decimal(0)
ONE = Decimal(1)
PAYOUT_TOL = Decimal("1e-9")
SUM_TOL = Decimal("1e-6")


# --------------------------------------------------------- sealed settlement
def load_settlement_independently():
    """The structural verdicts, re-derived rather than imported."""
    out = {}
    with open(ST) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            toks = d.get("tokens") or []
            idx = [t.get("outcome_index") for t in toks]
            binary = (bool(d.get("token_metadata_present"))
                      and d.get("token_count") == 2
                      and sorted(i for i in idx if i is not None) == [0, 1]
                      and all(t.get("token_id") for t in toks))
            px = d.get("payouts")
            px_valid = (d.get("resolved_prices_type") == "array"
                        and isinstance(px, list) and px
                        and all(p is not None for p in px)
                        and d.get("payout_len") == d.get("token_count"))
            unique = False
            vals = None
            if px_valid:
                vals = [Decimal(str(p)) for p in px]
                px_valid = abs(sum(vals) - ONE) <= SUM_TOL
                unique = sum(1 for v in vals if abs(v - ONE) <= PAYOUT_TOL) == 1
            out[d["condition_id"]] = {
                "present": bool(d.get("market_row_present")),
                "tokmeta": bool(d.get("token_metadata_present")),
                "binary": binary,
                "resolved": bool(d.get("resolved")),
                "resolved_at": d.get("resolved_at"),
                "px_valid": bool(px_valid),
                "unique": unique,
                "payouts": vals,
                "tokidx": {t["token_id"]: t.get("outcome_index")
                           for t in toks if t.get("token_id")},
            }
    return out


def strong_quarantine(S):
    latest = {}
    with gzip.open(W_GZ, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            c = r.get("condition_id")
            if c and (c not in latest or r["ts"] > latest[c]):
                latest[c] = r["ts"]
    return {c for c, d in S.items()
            if d["resolved_at"] is not None
            and latest.get(c, "") > d["resolved_at"]}


# ------------------------------------------------------------- the depth walk
def walk_cost(levels, q):
    """Cost of taking q shares -- prefix sums plus a bisect, not an accumulate.

    levels is already price-ascending. Returns None when the retained book
    cannot cover q; a partial cost is never returned dressed as a full one.
    """
    if not levels or q <= ZERO:
        return None
    cum = []
    run = ZERO
    for _, sh in levels:
        run += sh
        cum.append(run)
    if run < q:
        return None
    i = bisect.bisect_left(cum, q)
    cost = ZERO
    for j in range(i):
        cost += levels[j][0] * levels[j][1]
    prev = cum[i - 1] if i else ZERO
    cost += levels[i][0] * (q - prev)
    return cost


class Agg:
    __slots__ = ("n", "q", "cost_src", "cost_obs", "pnl_src", "pnl_obs",
                 "top", "dep", "total", "max_decomp")

    def __init__(self):
        self.n = 0
        self.q = self.cost_src = self.cost_obs = ZERO
        self.pnl_src = self.pnl_obs = ZERO
        self.top = self.dep = self.total = ZERO
        self.max_decomp = ZERO


def main():
    S = load_settlement_independently()
    quar = strong_quarantine(S)

    scen = ("Q_A", "Q_B", "Q_C")
    topagg = {s: Agg() for s in scen}
    depagg = {s: Agg() for s in scen}
    exhausted = {s: 0 for s in scen}
    probe_ids = []
    n_pop = 0
    conds = set()
    notional = ZERO
    wins = 0

    with gzip.open(EV, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            c = r.get("condition_id_effective")
            if not c:
                continue
            d = S.get(c)
            if d is None or not d["present"] or not d["tokmeta"] \
               or not d["binary"] or not d["resolved"] or not d["px_valid"] \
               or c in quar:
                continue
            oi = d["tokidx"].get(r.get("asset"))
            if oi is None or not isinstance(oi, int) \
               or not (0 <= oi < len(d["payouts"])) or not d["unique"]:
                continue

            n_pop += 1
            conds.add(c)
            notional += Decimal(r["notional"])
            probe_ids.append(int(r["probe_id"]))
            pay = d["payouts"][oi]
            if abs(pay - ONE) <= PAYOUT_TOL:
                wins += 1

            ask = r["best_ask"]
            if ask is None:
                continue
            ask = Decimal(ask)
            ph = Decimal(r["price"])
            size = Decimal(r["size"])
            if not (ZERO < ask <= ONE) or not (ZERO < ph < ONE) or size <= ZERO:
                continue

            levels = sorted(((Decimal(p), Decimal(s))
                             for p, s in (r["depth"] or [])
                             if p is not None and s is not None),
                            key=lambda t: t[0])

            for sc, q in (("Q_A", Decimal("0.10") * size),
                          ("Q_B", size),
                          ("Q_C", Decimal(1000) / ph)):
                if q <= ZERO:
                    continue
                a = topagg[sc]
                a.n += 1
                a.q += q
                a.cost_src += q * ph
                a.cost_obs += q * ask
                a.pnl_src += q * (pay - ph)
                a.pnl_obs += q * (pay - ask)
                a.top += q * ask - q * ph

                cost = walk_cost(levels, q)
                if cost is None:
                    exhausted[sc] += 1
                    continue
                b = depagg[sc]
                b.n += 1
                b.q += q
                b.cost_src += q * ph
                b.cost_obs += cost
                b.pnl_src += q * (pay - ph)
                b.pnl_obs += q * pay - cost
                t = q * ask - q * ph
                dd = cost - q * ask
                tot = cost - q * ph
                b.top += t
                b.dep += dd
                b.total += tot
                b.max_decomp = max(b.max_decomp, abs((t + dd) - tot))

    digest = hashlib.sha256(
        ",".join(str(p) for p in sorted(probe_ids)).encode("ascii")).hexdigest()

    print("== INDEPENDENT VALIDATION OF RUN 81B ==")
    print("Decimal(50) arithmetic; prefix-sum + bisect depth walk; no vwap")
    print("formed anywhere; population re-derived, not imported. No database.\n")

    print("== POPULATION, RE-DERIVED INDEPENDENTLY ==")
    print(f"  events                {n_pop:>14,}")
    print(f"  conditions            {len(conds):>14,}")
    print(f"  source notional       {notional.quantize(Decimal('0.01')):>14,}")
    print(f"  S = 1 events          {wins:>14,}")
    print(f"  S = 0 events          {n_pop - wins:>14,}")
    print(f"  EVENT_IDENTITY_DIGEST {digest}")
    print("  Compare that digest with the reference run's. Matching aggregate")
    print("  totals are NOT evidence that two programs read the same rows.\n")

    print("== DEPTH DECOMPOSITION, EXACT IN THIS NUMERIC MODEL ==")
    print("   TOP + DEPTH == TOTAL, where TOTAL is formed as (cost - q*p_h)")
    print("   without ever dividing to a vwap and multiplying back.")
    for sc in scen:
        b = depagg[sc]
        if b.n == 0:
            v = "NOT TESTED"
        elif b.max_decomp == 0:
            v = "EXACT -- residual is identically zero"
        else:
            # Q_C's q is 1000/p_h, which is not exact in any finite base-10
            # representation, so its residual is Decimal rounding at 50 digits
            # and nothing else. Named, not hidden behind a threshold.
            v = (f"PASS -- max residual {b.max_decomp} "
                 f"(q = 1000/p_h is inexact at 50 digits)")
        print(f"  {sc:6}{b.n:>10,} witnesses   {v}")
    print()

    print("== THE FIGURES THIS FILE PRODUCES INDEPENDENTLY ==")
    for sc in scen:
        a, b = topagg[sc], depagg[sc]
        print(f"-- {sc} --")
        print(f"  top-of-book events                      {a.n:>18,}")
        print(f"  scenario source acquisition cost        {a.cost_src:>18,.2f}")
        print(f"  SOURCE_COUNTERFACTUAL_PNL               {a.pnl_src:>18,.2f}")
        print(f"  FIRST_OBSERVED_TOP_COUNTERFACTUAL_PNL   {a.pnl_obs:>18,.2f}")
        print(f"  TOP_OF_BOOK_PNL_DETERIORATION           {a.top:>18,.2f}")
        print(f"  depth-supported events                  {b.n:>18,}")
        print(f"  DEPTH_EXHAUSTED events                  "
              f"{exhausted[sc]:>18,}")
        print(f"  supported source acquisition cost       {b.cost_src:>18,.2f}")
        print(f"  SOURCE_DEPTH_SUBSET_COUNTERFACTUAL_PNL  {b.pnl_src:>18,.2f}")
        print(f"  FIRST_OBSERVED_DEPTH_COUNTERFACTUAL_PNL {b.pnl_obs:>18,.2f}")
        print(f"  TOP_COMPONENT                           {b.top:>18,.2f}")
        print(f"  DEPTH_COMPONENT                         {b.dep:>18,.2f}")
        print(f"  TOTAL_OBSERVED_PNL_DETERIORATION        {b.total:>18,.2f}")
        if b.total:
            print(f"  TOP_COMPONENT / TOTAL                   "
                  f"{b.top / b.total:>18.4f}")
            print(f"  DEPTH_COMPONENT / TOTAL                 "
                  f"{b.dep / b.total:>18.4f}")
        print()

    print("HOW TO READ A DIFFERENCE AGAINST THE REFERENCE RUN. Both programs")
    print("read the same sealed bytes, so the populations must be identical --")
    print("the digest proves that, or it does not. What may legitimately")
    print("differ is the last places of a sum, because one program works in")
    print("IEEE double and this one in 50-digit Decimal. A difference in the")
    print("cents is a numeric-model difference and is reported as such. A")
    print("difference in the dollars is a defect in one of the two programs")
    print("and is not to be explained away.")


if __name__ == "__main__":
    main()
