"""The remaining declared policies, on tape-backed execution.

THE REGISTER OF VARIANTS TRIED. Kept whole, including the ones that
failed, because a comparison that quietly drops its losers is a search
result dressed as a finding.

    C0   BASE                quote every two-sided book >= 1 tick
    C2   RETIRED             2+ ticks, at the touch, hard-flatten.
                             Published at +0.0020/contract; negative at
                             EVERY queue fraction once fills come from
                             the venue's tape. Not retuned, not revived.
    C3   INVENTORY-AWARE     C2 plus: stop quoting the other side once a
                             leg fills, cap unmatched inventory at half
                             a clip, and complete the pair rather than
                             carry a naked leg.
    C4   INCENTIVE-AWARE LP  quote at the touch in a mid band, rest for
                             the full horizon rather than cancelling
                             early, and hold to settlement -- the shape
                             a liquidity-provision reward pays for.

NO PARAMETER SEARCH. Every one of these is a NAMED COMBINATION of knobs
that already existed in Policy. Nothing was swept, nothing was chosen
after seeing its result, and nothing new was added to make a number
positive.

THE INCENTIVE RATE IS NOT KNOWN AND IS NOT GUESSED. We have never
retrieved PMUS's liquidity-provision terms; `/incentives/overview`
appears in a documentation-capture list and nothing more. Assuming a
rate would manufacture the answer, so C4 is run at a reward of ZERO and
the output is instead the BREAK-EVEN RATE: the reward per committed
capital-hour that would be required to bring it to zero. That is a
number the venue's published terms can later be checked against, rather
than a number that depends on them.

CAPITAL-HOURS ARE THE DENOMINATOR, not contracts. A policy that earns
less per contract while tying up far less capital for far less time is
the better use of a fixed balance, and per-contract figures hide that.

Run:  python research/beta48/bettor_policy_final.py
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bettor_episodes as epi                                   # noqa: E402
import bettor_prints as prints_mod                              # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "acceptance", "policy_final.json")

CANDIDATES = [
    ("C0  base", epi.Policy(name="C0")),
    ("C2  retired", epi.Policy(name="C2", min_spread_ticks=2,
                               placement="AT_TOUCH", hard_flatten=True)),
    ("C3  inventory-aware", epi.Policy(
        name="C3", min_spread_ticks=2, placement="AT_TOUCH",
        hard_flatten=True, cancel_other_on_fill=True,
        max_unmatched_mult=0.5, recovery="COMPLETE_PAIR")),
    ("C4  incentive-aware LP", epi.Policy(
        name="C4", min_spread_ticks=1, placement="AT_TOUCH",
        min_mid=0.20, max_mid=0.80, quote_horizon_s=7200.0,
        recovery_wait_s=3600.0, recovery="HOLD", hard_flatten=False)),
]
QFRACS = (0.00, 0.25, 0.50, 1.00)


def _t(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def summarise(eps):
    net = sum(e.get("total_if_residual_realises", 0.0) for e in eps)
    contracts = sum(sum(abs(x) for x in e.get("fill_sizes") or ())
                    for e in eps)
    anyfill = sum(1 for e in eps if e.get("entry_legs_filled"))
    both = sum(1 for e in eps if len(e.get("entry_legs_filled") or ()) == 2)

    # CAPITAL-HOURS: collateral actually committed, integrated over the
    # episode's own life. Collateral is committed at entry, before any
    # fill, so an episode that never fills still consumes it.
    cap_h = 0.0
    for e in eps:
        a, b = _t(e.get("t0")), _t(e.get("t_end"))
        if a is None or b is None or b <= a:
            continue
        cap_h += e.get("collateral_incl_resting", 0.0) * (b - a) / 3600.0

    rebates = sum(e.get("rebates_received", 0.0) for e in eps)
    tape_iv = sum(e.get("tape_intervals", 0) for e in eps)
    snap_iv = sum(e.get("snapshot_intervals", 0) for e in eps)
    return {"episodes": len(eps), "any_fill": anyfill, "both_legs": both,
            "net_usd": round(net, 4), "contracts": round(contracts, 2),
            "per_contract": round(net / contracts, 6) if contracts else None,
            "capital_hours": round(cap_h, 2),
            "per_capital_hour": round(net / cap_h, 6) if cap_h else None,
            "rebates_received": round(rebates, 4),
            "tape_intervals": tape_iv, "snapshot_intervals": snap_iv}


def main():
    if not prints_mod.tape_dir():
        print("NO TAPE -- refusing to report a tape-backed result without "
              "the tape.")
        return 1

    print("=" * 78)
    print("DECLARED POLICIES ON TAPE-BACKED EXECUTION")
    print("simulated replay results -- NOT account performance")
    print("=" * 78)
    print()
    print("%-24s %5s %6s %7s %10s %11s %9s %11s" % (
        "candidate", "qfrac", "eps", "anyfil", "net$", "per_ctr",
        "cap_hrs", "per_cap_hr"))
    print("-" * 92)

    rows = []
    for name, pol in CANDIDATES:
        for q in QFRACS:
            p = epi.dataclasses.replace(pol, queue_ahead_fraction=q)
            s = summarise(epi.run_all(size=100.0, rebates_on=True,
                                      policy=p, use_tape=True))
            assert s["snapshot_intervals"] == 0, (
                "snapshot fallback leaked into a tape-backed run")
            rows.append({"candidate": name, "qfrac": q, **s})
            print("%-24s %5.2f %6d %7d %10.2f %11s %9.1f %11s" % (
                name, q, s["episodes"], s["any_fill"], s["net_usd"],
                ("%.6f" % s["per_contract"]) if s["per_contract"] is not None
                else "-", s["capital_hours"],
                ("%.6f" % s["per_capital_hour"])
                if s["per_capital_hour"] is not None else "-"))

    print()
    print("=" * 78)
    print("C4: THE BREAK-EVEN LIQUIDITY-PROVISION REWARD")
    print("=" * 78)
    print("The venue's incentive terms are NOT RETRIEVED. C4 therefore")
    print("runs at a reward of zero, and what is reported is the reward")
    print("per committed capital-hour that would bring it to zero.")
    print()
    print("%5s %12s %12s %16s" % (
        "qfrac", "net$", "cap_hrs", "break-even $/cap-hr"))
    be = []
    for r in rows:
        if not r["candidate"].startswith("C4"):
            continue
        ch = r["capital_hours"]
        need = (-r["net_usd"] / ch) if ch else None
        be.append({"qfrac": r["qfrac"], "break_even_per_capital_hour":
                   round(need, 6) if need is not None else None})
        print("%5.2f %12.2f %12.1f %16s" % (
            r["qfrac"], r["net_usd"], ch,
            ("%.6f" % need) if need is not None else "-"))
    print()
    print("Read this as: every committed dollar must earn AT LEAST this")
    print("much per hour in rewards before C4 stops losing money. It is")
    print("a hurdle to check the published terms against, not a forecast.")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump({"rows": rows, "c4_break_even": be,
                   "incentive_rate": "NOT_RETRIEVED -- run at zero"},
                  fh, indent=2, sort_keys=True)
    print("\nwritten: %s" % os.path.relpath(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
