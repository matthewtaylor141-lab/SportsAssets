#!/usr/bin/env python3
"""RUN 82A -- IS THE 81B DETERIORATION A SETTLEMENT-SELECTION ARTIFACT?

    python3 research/gen/run82a.py

NO DATABASE. Sealed artifacts only.

THE QUESTION. 81B measured price deterioration on a settlement-selected cohort.
Is that deterioration unusually severe BECAUSE the cohort was selected for being
resolved and analyzable?

WHY THE QUESTION IS ANSWERABLE AT ALL. The deterioration quantity

    q * (best_ask - p_h)

contains no settlement term. It needs no payout, no resolution and no outcome.
So it can be measured on events that could never enter 81B -- unresolved ones,
unlinked ones, quarantined ones -- and the selected cohort can be compared
against the population it was drawn from.

NO POST-HOC THRESHOLD IS APPLIED. This file reports the differences -- absolute
in percentage points, relative, and the full distribution of the per-share move
by bucket -- and characterises them. It does not invent a cutoff after seeing
the values and then classify against it. That practice was retired by owner
correction in 81B and is not reintroduced here.

BUCKETS ARE FIRST-FAIL AND MUTUALLY EXCLUSIVE, in the ladder's own order, so
every one of the 214,609 sealed events lands in exactly one. The order is
load-bearing: a bucket answers "where did this event FIRST fail", and reordering
the tests would move events between buckets without changing any event.

Missing depth is never treated as zero. Depth-supported denominators are printed
beside every depth figure.
"""
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run81a_s import pct, walk  # noqa: E402  -- one definition of each
from run81b_population_gate import SealedInputs, verify_sealed_hashes  # noqa: E402

U2_EVENT_CONTROL = 214609
U2_NOTIONAL_CONTROL = Decimal("48327293.76")

# First-fail buckets, in ladder order.
LADDER = ["UNLINKED_CONDITION", "NO_SETTLEMENT_METADATA",
          "TOKEN_METADATA_MISSING", "NOT_STRUCTURALLY_BINARY", "UNRESOLVED",
          "RESOLVED_PRICES_INVALID", "TIMING_QUARANTINE",
          "PAYOUT_MAPPING_AMBIGUOUS", "SETTLEMENT_ANALYZABLE_STRONG"]

# The owner's six requested buckets, as groupings of the ladder's first-fail
# classes. TOKEN_METADATA_MISSING and NOT_STRUCTURALLY_BINARY fit NONE of the
# six: the market row IS present (so not "no settlement metadata") and the
# ladder never reached the resolution test (so not "resolved but otherwise not
# analyzable"). They are reported under their own name rather than forced into
# a bucket whose label would then be false.
GROUPS = [
    ("SETTLEMENT_ANALYZABLE_STRONG", ["SETTLEMENT_ANALYZABLE_STRONG"]),
    ("TIMING_QUARANTINE", ["TIMING_QUARANTINE"]),
    ("RESOLVED_BUT_OTHERWISE_NOT_ANALYZABLE",
     ["RESOLVED_PRICES_INVALID", "PAYOUT_MAPPING_AMBIGUOUS"]),
    ("UNRESOLVED", ["UNRESOLVED"]),
    ("NO_SETTLEMENT_METADATA", ["NO_SETTLEMENT_METADATA"]),
    ("UNLINKED_CONDITION", ["UNLINKED_CONDITION"]),
    ("STRUCTURALLY_INELIGIBLE_PRE_RESOLUTION",
     ["TOKEN_METADATA_MISSING", "NOT_STRUCTURALLY_BINARY"]),
]


class Bucket:
    """Everything 82A reports for one bucket. No settlement term anywhere."""

    def __init__(self):
        self.events = 0
        self.conds = set()
        self.notional = Decimal("0")
        # deterioration-eligible rows: valid ask AND q definable
        self.elig = 0
        self.moves = []             # cents per share, one per eligible row
        self.pos = self.zero = self.neg = 0
        self.q = 0.0
        self.cost = 0.0             # Q_A source acquisition cost
        self.det = 0.0              # Q_A top-of-book deterioration, dollars
        # depth
        self.sup = 0
        self.sup_cost = 0.0
        self.sup_top = 0.0
        self.sup_dep = 0.0
        self.sup_total = 0.0

    def merge(self, o):
        self.events += o.events
        self.conds |= o.conds
        self.notional += o.notional
        self.elig += o.elig
        self.moves.extend(o.moves)
        self.pos += o.pos
        self.zero += o.zero
        self.neg += o.neg
        self.q += o.q
        self.cost += o.cost
        self.det += o.det
        self.sup += o.sup
        self.sup_cost += o.sup_cost
        self.sup_top += o.sup_top
        self.sup_dep += o.sup_dep
        self.sup_total += o.sup_total
        return self


def rate(num, den):
    return None if not den else num / den


def pctf(x, dp=3):
    return "      n/a" if x is None else f"{100.0 * x:>8.{dp}f}%"


def main():
    print("=" * 78)
    print("RUN 82A -- SELECTION ROBUSTNESS OF THE FIRST-OBSERVATION DETERIORATION")
    print("=" * 78)
    print("Sealed bytes only. No database. No settlement term in any quantity")
    print("below -- q*(best_ask - p_h) needs no payout, so it is measurable on")
    print("events that could never enter 81B. mirror_live=false.\n")

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
    B = defaultdict(Bucket)

    for r, bucket, _S in sealed.events():
        b = B[bucket]
        b.events += 1
        c = r.get("condition_id_effective")
        if c:
            b.conds.add(c)
        b.notional += Decimal(r["notional"])

        ask = r["best_ask"]
        ask = float(ask) if ask is not None else None
        ph = float(r["price"])
        size = float(r["size"])
        if ask is None or not (0 < ask <= 1) or not (0 < ph < 1) or size <= 0:
            continue
        b.elig += 1
        move = (ask - ph) * 100.0        # cents per share
        b.moves.append(move)
        if move > 0:
            b.pos += 1
        elif move == 0:
            b.zero += 1
        else:
            b.neg += 1

        q = 0.10 * size                  # Q_A, the primary scenario
        b.q += q
        b.cost += q * ph
        b.det += q * (ask - ph)

        levels = [(float(p), float(s)) for p, s in (r["depth"] or [])
                  if p is not None and s is not None]
        _total_sh, cost = walk(levels, q)
        if cost is None:
            continue                     # DEPTH_EXHAUSTED -- never counted as 0
        vwap = cost / q
        b.sup += 1
        b.sup_cost += q * ph
        b.sup_top += q * (ask - ph)
        b.sup_dep += q * (vwap - ask)
        b.sup_total += q * (vwap - ph)

    # ---------------------------------------------------------- CLOSURE
    tot_ev = sum(b.events for b in B.values())
    tot_no = sum((b.notional for b in B.values()), Decimal("0"))
    print("== CLOSURE TO U2_SNAPSHOT_V1 ==")
    print(f"  sum of bucket events   {tot_ev:>14,}   control "
          f"{U2_EVENT_CONTROL:>14,}   "
          f"{'MATCH' if tot_ev == U2_EVENT_CONTROL else '*** MISMATCH ***'}")
    print(f"  sum of bucket notional {tot_no.quantize(Decimal('0.01')):>14,}"
          f"   control {U2_NOTIONAL_CONTROL:>14,}   "
          f"{'MATCH' if tot_no.quantize(Decimal('0.01')) == U2_NOTIONAL_CONTROL else '*** MISMATCH ***'}")
    print(f"  exact notional sum at full retained precision: {tot_no}")
    if tot_ev != U2_EVENT_CONTROL \
       or tot_no.quantize(Decimal("0.01")) != U2_NOTIONAL_CONTROL:
        print("\n== HALT -- bucket closure failure ==")
        return 1
    print("  Buckets are first-fail and mutually exclusive, so every sealed")
    print("  event lands in exactly one and the two sums close by construction")
    print("  only if the classifier is total. It is.\n")

    # ------------------------------------------------- the grouped report
    full = Bucket()
    for b in B.values():
        full.merge(b)
    rows = [("FULL_U2_SNAPSHOT_V1", full)]
    for name, members in GROUPS:
        g = Bucket()
        for m in members:
            if m in B:
                g.merge(B[m])
        rows.append((name, g))

    print("== BUCKET CENSUS AND TOP-OF-BOOK DETERIORATION AT Q_A ==")
    print("   Q_A = 0.10 * RN1 source fill shares. Cents/share statistics and")
    print("   the percentage splits are over DETERIORATION-ELIGIBLE rows (valid")
    print("   retained ask and a definable q), whose count is printed; the")
    print("   event and notional columns are over the whole bucket.\n")
    hdr = (f"{'bucket':<40}{'events':>9}{'conds':>8}{'source notional':>17}"
           f"{'elig':>9}{'Q_A cost':>15}{'mean c/sh':>11}{'med c/sh':>10}"
           f"{'%pos':>9}{'%zero':>9}{'%neg':>9}{'Q_A deteri $':>15}"
           f"{'det/cost':>10}")
    print(hdr)
    for name, b in rows:
        cond = f"{len(b.conds):,}" if b.conds else "n/a"
        head = (f"{name:<40}{b.events:>9,}{cond:>8}"
                f"{b.notional.quantize(Decimal('0.01')):>17,}"
                f"{b.elig:>9,}{b.cost:>15,.2f}")
        if not b.moves:
            print(head + f"{'NOT TESTED -- zero eligible rows':>55}")
            continue
        mean = sum(b.moves) / len(b.moves)
        med = pct(b.moves, 0.5)
        print(head + f"{mean:>11.4f}{med:>10.4f}"
              f"{pctf(rate(b.pos, b.elig)):>9}{pctf(rate(b.zero, b.elig)):>9}"
              f"{pctf(rate(b.neg, b.elig)):>9}{b.det:>15,.2f}"
              f"{pctf(rate(b.det, b.cost)):>10}")
    print()
    print("  UNLINKED_CONDITION has no condition by definition, so its")
    print("  condition count prints n/a rather than 0 -- the two mean")
    print("  different things and only one of them is true.")
    zero_named = [n for n, b in rows[1:] if b.events == 0]
    print(f"  buckets that are empty, printed explicitly as zero: "
          f"{zero_named if zero_named else 'none'}\n")

    # ------------------------------------------------ distribution detail
    print("== FULL DISTRIBUTION OF THE PER-SHARE MOVE, BY BUCKET ==")
    print("   cents per share, over deterioration-eligible rows. This is what")
    print("   the comparison below rests on -- not a single summary number.")
    print(f"{'bucket':<40}{'n':>9}{'p10':>9}{'p25':>9}{'p50':>9}"
          f"{'p75':>9}{'p90':>9}{'p99':>9}")
    for name, b in rows:
        if not b.moves:
            print(f"{name:<40}{0:>9}{'NOT TESTED -- zero witnesses':>50}")
            continue
        v = sorted(b.moves)
        print(f"{name:<40}{len(v):>9,}"
              + "".join(f"{pct(v, p):>9.3f}"
                        for p in (0.10, 0.25, 0.50, 0.75, 0.90, 0.99)))
    print()

    # ------------------------------------------------- the key comparison
    keyrows = [("FULL_U2_SNAPSHOT_V1", full),
               ("SETTLEMENT_ANALYZABLE_STRONG", B["SETTLEMENT_ANALYZABLE_STRONG"]),
               ("UNRESOLVED", B["UNRESOLVED"])]
    print("=" * 78)
    print("KEY COMPARISON -- RECOMPUTED FROM SEALED BYTES, NOT MATCHED TO PRIORS")
    print("=" * 78)
    print(f"{'':<32}{'Q_A det / Q_A cost':>22}{'% events ask > p_h':>22}")
    for name, b in keyrows:
        print(f"{name:<32}{pctf(rate(b.det, b.cost), 4):>22}"
              f"{pctf(rate(b.pos, b.elig), 4):>22}")
    print()

    base = full
    print("DIFFERENCES AGAINST FULL U2 -- absolute in percentage points, and")
    print("relative. No cutoff is applied to either.")
    print(f"{'':<32}{'det rate abs pp':>18}{'det rate rel':>15}"
          f"{'%pos abs pp':>14}{'%pos rel':>12}")
    br = rate(base.det, base.cost)
    bp = rate(base.pos, base.elig)
    for name, b in keyrows[1:]:
        r_ = rate(b.det, b.cost)
        p_ = rate(b.pos, b.elig)
        if r_ is None or br is None or p_ is None or bp is None:
            print(f"{name:<32}{'NOT TESTED -- zero witnesses':>18}")
            continue
        d_abs = (r_ - br) * 100.0
        d_rel = None if not br else (r_ - br) / br
        p_abs = (p_ - bp) * 100.0
        p_rel = None if not bp else (p_ - bp) / bp
        print(f"{name:<32}{d_abs:>+18.4f}{pctf(d_rel, 3):>15}"
              f"{p_abs:>+14.4f}{pctf(p_rel, 3):>12}")
    print()

    # --------------------------------------------------------- depth check
    print("=" * 78)
    print("82A DEPTH CHECK -- Q_A DEPTH_SUPPORTED SUBSET, BY BUCKET")
    print("=" * 78)
    print("   Denominators are the SUPPORTED rows only and are printed. A row")
    print("   whose retained book cannot cover q is DEPTH_EXHAUSTED and is NOT")
    print("   counted as zero deterioration -- it is not counted at all.")
    print(f"{'bucket':<40}{'elig':>9}{'supported':>11}{'sup rate':>10}"
           f"{'sup Q_A cost':>16}{'top comp':>14}{'depth comp':>14}"
           f"{'total':>14}{'total/cost':>11}")
    for name, b in rows:
        if b.sup == 0:
            print(f"{name:<40}{b.elig:>9,}{0:>11,}"
                  f"{'NOT TESTED -- zero supported rows':>60}")
            continue
        print(f"{name:<40}{b.elig:>9,}{b.sup:>11,}"
              f"{pctf(rate(b.sup, b.elig)):>10}{b.sup_cost:>16,.2f}"
              f"{b.sup_top:>14,.2f}{b.sup_dep:>14,.2f}{b.sup_total:>14,.2f}"
              f"{pctf(rate(b.sup_total, b.sup_cost)):>11}")
    print()
    print(f"{'bucket':<40}{'top share of total':>20}{'depth share':>14}")
    for name, b in rows:
        if b.sup == 0 or b.sup_total == 0:
            print(f"{name:<40}{'NOT TESTED':>20}")
            continue
        print(f"{name:<40}{pctf(rate(b.sup_top, b.sup_total), 3):>20}"
              f"{pctf(rate(b.sup_dep, b.sup_total), 3):>14}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
