#!/usr/bin/env python3
"""RUN 81B POPULATION GATE -- the HALT check, and nothing past it.

    python3 research/gen/run81b_population_gate.py

NO DATABASE. Opens exactly the three sealed artifacts and nothing else:
    research/snapshots/u2_events_v1.jsonl.gz
    research/snapshots/settlement_v1.jsonl
    research/snapshots/u0_timing_witness_v1.jsonl.gz

WHAT THIS FILE DOES:
  1. Re-derives SETTLEMENT_ANALYZABLE_STRONG from sealed bytes, applying the
     ladder predicate unchanged, and compares its three controls against the
     approved expectations. A mismatch is a HALT, not a note.
  2. Reports the side and source census of that population.
  3. Measures how much of U0 the sealed U2 population covers on exactly those
     conditions -- a ROW COUNT ONLY.

WHAT THIS FILE DOES NOT DO: any economics whatsoever. No settlement-realized
margin, no matched register, no directional remainder, no drag bridge, no P&L.
The 81B bridge specification has not been received in full and nothing that
depends on it is computed, sketched or approximated here.

WHY THE COVERAGE MEASUREMENT CANNOT BE VALUED. The U0 timing witness carries
trade_id, condition_id, ts and source -- deliberately no prices and no sizes,
because none of them enter the timing predicate it was sealed for. So the U0
rows outside the population can be COUNTED from sealed bytes and can never be
PRICED from them. Any per-condition acquisition pool built from these inputs is
a PARTIAL pool by construction, and this file says so rather than computing one.
"""
import gzip
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reachability_ladder import load_settlement, EV, W_GZ  # noqa: E402

# The approved controls. Source: research/REACHABILITY_LADDER.md section F.
EXPECTED_EVENTS = 112543
EXPECTED_CONDITIONS = 9336
EXPECTED_NOTIONAL_2DP = Decimal("24727133.10")


def main():
    S = load_settlement()

    # --- the STRONG quarantine, from the sealed U0 witness ----------------
    # A U0 row with a NULL condition witnesses nothing about any condition and
    # is skipped; it is still counted in the witness's own manifest.
    latest_u0 = {}
    u0_by_cond = defaultdict(int)
    n_witness = 0
    with gzip.open(W_GZ, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            n_witness += 1
            c = r.get("condition_id")
            if c:
                u0_by_cond[c] += 1
                if c not in latest_u0 or r["ts"] > latest_u0[c]:
                    latest_u0[c] = r["ts"]

    strong_q = {c for c, d in S.items()
                if d.get("resolved_at") is not None
                and latest_u0.get(c, "") > d["resolved_at"]}

    # --- the population: the ladder predicate, applied unchanged ----------
    pop_ev = 0
    pop_cond = set()
    # Decimal over the retained text, never float: the sealed rows carry six
    # decimal places and summing 112,543 of them in binary floating point would
    # make the control a function of addition order.
    pop_notional = Decimal("0")
    sides = defaultdict(int)
    sources = defaultdict(int)
    u2_by_cond = defaultdict(int)

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
            if d is None or not d.get("market_row_present"):
                continue
            if not d.get("token_metadata_present") or not d["_binary"]:
                continue
            if not d.get("resolved") or not d["_px_valid"]:
                continue
            if c in strong_q:
                continue
            oi = d["_tokidx"].get(r.get("asset"))
            if oi is None or not isinstance(oi, int) \
               or not (0 <= oi < len(d["payouts"])):
                continue
            if not d["_unique_winner"]:
                continue
            pop_ev += 1
            pop_cond.add(c)
            pop_notional += Decimal(r["notional"])
            sides[r.get("side")] += 1
            sources[r.get("source")] += 1
            u2_by_cond[c] += 1

    pop_2dp = pop_notional.quantize(Decimal("0.01"))
    checks = [
        ("events", pop_ev, EXPECTED_EVENTS),
        ("conditions", len(pop_cond), EXPECTED_CONDITIONS),
        ("source notional", pop_2dp, EXPECTED_NOTIONAL_2DP),
    ]
    halted = [nm for nm, got, exp in checks if got != exp]

    print("== RUN 81B POPULATION GATE ==")
    print("== SETTLEMENT_ANALYZABLE_STRONG, re-derived from sealed bytes ==")
    print("No database. No economics of any kind computed in this file.\n")

    print(f"U0 timing witness rows read {n_witness:,}; "
          f"conditions datable {len(latest_u0):,}")
    print(f"STRONG quarantine conditions {len(strong_q):,}\n")

    print("== CONTROLS ==")
    for nm, got, exp in checks:
        g = f"{got:,.2f}" if isinstance(got, Decimal) else f"{got:,}"
        e = f"{exp:,.2f}" if isinstance(exp, Decimal) else f"{exp:,}"
        flag = "MATCH" if got == exp else "*** MISMATCH ***"
        print(f"  {nm:<18}{g:>16}   expected {e:>16}   {flag}")
    print(f"  {'(exact notional at full retained precision)':<18} "
          f"{pop_notional}")
    print("  The approved control is the 2 dp rounding of that exact sum; the")
    print("  comparison is made at 2 dp for that reason and not by truncation.")
    print()

    if halted:
        print("== HALT ==")
        print("  Controls did not reproduce: " + ", ".join(halted))
        print("  81B does not proceed. Report the drift before any further use")
        print("  of this population.")
        return 1
    print("== ALL THREE CONTROLS REPRODUCE. NO HALT. ==\n")

    print("== SIDE AND SOURCE CENSUS OF THE POPULATION ==")
    for k in sorted(sides, key=str):
        print(f"  side    {k!s:<10}{sides[k]:>12,}")
    for k in sorted(sources, key=str):
        print(f"  source  {k!s:<10}{sources[k]:>12,}")
    print()

    # --- U2 coverage of U0, on exactly these conditions -------------------
    u0_ev = sum(u0_by_cond[c] for c in pop_cond)
    complete = sum(1 for c in pop_cond if u2_by_cond[c] == u0_by_cond[c])
    impossible = sum(1 for c in pop_cond if u2_by_cond[c] > u0_by_cond[c])

    print("== U2 COVERAGE OF U0 ON THE ANALYZABLE CONDITIONS ==")
    print("   ROW COUNTS ONLY. The U0 witness carries no prices and no sizes,")
    print("   so the uncovered rows can be counted here and can never be")
    print("   valued from the locked inputs.")
    print(f"  U0 events on these conditions         {u0_ev:>12,}")
    print(f"  U2 events on the same conditions      {pop_ev:>12,}")
    print(f"  U2 share of U0 events                 "
          f"{100.0 * pop_ev / u0_ev:>11.3f}%")
    print(f"  U0 events NOT in the population       {u0_ev - pop_ev:>12,}")
    print(f"  conditions with COMPLETE U2 coverage  {complete:>12,} of "
          f"{len(pop_cond):,} ({100.0 * complete / len(pop_cond):.2f}%)")
    print(f"  conditions where U2 > U0 (impossible) {impossible:>12,}")
    print()
    print("CONSEQUENCE, STATED AND NOT COMPUTED AROUND: a per-condition")
    print("acquisition pool built from these inputs is a PARTIAL pool. An")
    print("average cost derived from one is NOT RN1's average cost, and a")
    print("matched quantity derived from one is NOT RN1's matched quantity.")
    print("This file computes neither.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
