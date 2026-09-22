"""Liquidity-incentive reward in DOLLARS, as scenarios, with the floor.

THE CLAIM THIS CORRECTS. I wrote that the $1.00 minimum payout "makes
the M1-M3 scale ineligible by construction." That does not follow. The
minimum applies to the PAYOUT, and the payout is

    our qualifying score / total qualifying score  x  the period's pool

Nothing in that depends on our order being large in absolute terms. A
small order in a small, uncontested pool can clear a dollar; a large
order in a crowded one can fail to. Size enters only through our SHARE,
and share depends on the pool, the competition and the duration -- none
of which I had measured, and two of which are not published.

WHAT IS RETRIEVED (docs.polymarket.us/incentives/liquidity):

    pooled and scored, with a target size, a distance discount factor,
    snapshot weighting and a per-person cap
    periods   Early/pre-game (listing -> 6 h before), Day-of (6 h ->
              start), Live (start -> settlement), Daily (midnight to
              midnight ET) for events without a fixed start
    paid      within 5 business days of period end, credited within 2
    minimum   rewards under $1.00 are not paid
    excluded  cancelled or postponed games

WHAT IS NOT RETRIEVED: the pool size per period, the exact scoring
formula, and the per-person cap. So this does NOT forecast a reward. It
reports, for a grid of pool sizes and achievable shares, the dollars
per period and whether they clear the floor -- and the share we would
need at each pool size. The published terms can be checked against it
later; nothing here assumes them.

ANNUALISED FIGURES ARE DELIBERATELY SECONDARY. The decision is dollars
against committed capital-hours over the actual programme period.

Run:  python research/beta48/bettor_incentive_scenarios.py
"""
from __future__ import annotations

import json
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "acceptance", "incentive_scenarios.json")

MIN_PAYOUT = 1.00

# C4 over the 8-day capture, from bettor_policy_final (qfrac 0.00):
C4_EPISODES = 44
C4_CAPITAL_HOURS = 50505.7
C4_NET_USD = -10.23
C4_MARKETS = 12
CAPTURE_DAYS = 7.0

# M-scale, for the claim actually at issue.
M_CONTRACTS = 4
M_PRICE = 0.51
M_COMMITTED = M_CONTRACTS * M_PRICE          # $2.04
M_PERIOD_HOURS = 6.0                         # one pre-game period

POOLS = (50.0, 200.0, 1000.0, 5000.0, 25000.0)
SHARES = (0.001, 0.01, 0.05, 0.20)


def main():
    print("=" * 76)
    print("LIQUIDITY INCENTIVE, IN DOLLARS PER EVENT-PERIOD")
    print("pool size and our share are BOTH UNRETRIEVED; this is a grid,")
    print("not a forecast.")
    print("=" * 76)
    print()
    print("%10s | %s" % ("pool $", "".join("%12s" % ("share %.1f%%" % (s * 100))
                                           for s in SHARES)))
    print("-" * 76)
    rows = []
    for pool in POOLS:
        cells = []
        for s in SHARES:
            r = pool * s
            cells.append("%10.2f%s" % (r, "*" if r < MIN_PAYOUT else " "))
            rows.append({"pool": pool, "share": s, "reward": round(r, 4),
                         "below_minimum": r < MIN_PAYOUT})
        print("%10.0f | %s" % (pool, "".join(cells)))
    print()
    print("* below the $1.00 minimum, so NOT PAID AT ALL")

    print()
    print("=" * 76)
    print("THE SHARE REQUIRED TO CLEAR THE FLOOR")
    print("=" * 76)
    print("%10s %18s" % ("pool $", "share needed for $1"))
    need = []
    for pool in POOLS:
        s = MIN_PAYOUT / pool
        need.append({"pool": pool, "share_needed": round(s, 6)})
        print("%10.0f %17.2f%%" % (pool, 100 * s))
    print()
    print("THIS IS WHERE MY CLAIM WAS WRONG. In a $50 pool a 2% share")
    print("clears the dollar; in a $25,000 pool 0.004% does. Nothing in")
    print("that is a statement about our order being four contracts.")

    print()
    print("=" * 76)
    print("WHAT THE M-SCALE WOULD ACTUALLY NEED")
    print("=" * 76)
    m_cap_h = M_COMMITTED * M_PERIOD_HOURS
    print("  committed            $%.2f  (%d contracts at %.2f)"
          % (M_COMMITTED, M_CONTRACTS, M_PRICE))
    print("  one pre-game period   %.1f h  ->  %.2f capital-hours"
          % (M_PERIOD_HOURS, m_cap_h))
    print("  reward needed to be paid at all   $%.2f" % MIN_PAYOUT)
    print("  that is %.1f%% of committed capital IN ONE PERIOD,"
          % (100 * MIN_PAYOUT / M_COMMITTED))
    print("  or $%.4f per committed capital-hour." % (MIN_PAYOUT / m_cap_h))
    print()
    print("  So the M-scale is not CATEGORICALLY ineligible -- it is")
    print("  eligible only in a pool small enough, or a share large")
    print("  enough, that one period returns half the committed stake.")
    print("  That is an implausible regime, not an impossible one, and")
    print("  it is a statement about the RATIO, not about four contracts.")

    print()
    print("=" * 76)
    print("C4 AT REPLAY SCALE -- dollars first, annualised last")
    print("=" * 76)
    per_day_cap_h = C4_CAPITAL_HOURS / CAPTURE_DAYS
    print("  episodes %d over %d markets, %.0f capital-hours in %.0f days"
          % (C4_EPISODES, C4_MARKETS, C4_CAPITAL_HOURS, CAPTURE_DAYS))
    print("  replay trading result             $%+.2f" % C4_NET_USD)
    print("  reward needed to reach zero       $%.2f over the window"
          % (-C4_NET_USD))
    print("  ... which is $%.6f per committed capital-hour"
          % (-C4_NET_USD / C4_CAPITAL_HOURS))
    print("  ... or $%.2f per DAY at %.0f capital-hours a day"
          % (-C4_NET_USD / CAPTURE_DAYS, per_day_cap_h))
    print()
    print("  IN DOLLARS THAT IS A SMALL NUMBER: $%.2f a day."
          % (-C4_NET_USD / CAPTURE_DAYS))
    print("  The annualised percentage quoted earlier (165-855 per cent)")
    print("  is the")
    print("  same fact divided by a very small committed base, and it")
    print("  made a $1.46-a-day shortfall sound like a structural")
    print("  impossibility. Dollars and capital-hours are the decision")
    print("  variables; the percentage is secondary and was leading.")
    print()
    print("  WHETHER $%.2f/day IS REACHABLE IS STILL UNKNOWN, because it"
          % (-C4_NET_USD / CAPTURE_DAYS))
    print("  depends on the pool, our share and the number of qualifying")
    print("  event-periods -- none of which is retrieved.")

    res = {"minimum_payout": MIN_PAYOUT, "grid": rows,
           "share_needed": need,
           "m_scale": {"committed_usd": round(M_COMMITTED, 4),
                       "period_hours": M_PERIOD_HOURS,
                       "capital_hours": round(m_cap_h, 4),
                       "pct_of_committed_needed":
                           round(100 * MIN_PAYOUT / M_COMMITTED, 2),
                       "verdict": ("NOT categorically ineligible; eligible "
                                   "only where one period returns ~half the "
                                   "committed stake")},
           "c4": {"net_usd": C4_NET_USD,
                  "capital_hours": C4_CAPITAL_HOURS,
                  "reward_needed_total": round(-C4_NET_USD, 2),
                  "reward_needed_per_day": round(-C4_NET_USD / CAPTURE_DAYS, 2),
                  "reward_needed_per_capital_hour":
                      round(-C4_NET_USD / C4_CAPITAL_HOURS, 6)},
           "unretrieved": ["pool size per period", "scoring formula",
                           "per-person cap", "competing liquidity"]}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=2, sort_keys=True)
    print("\nwritten: %s" % os.path.relpath(OUT))


if __name__ == "__main__":
    main()
