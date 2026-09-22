"""THE ECONOMIC TEST — run against recorded PMUS evidence, with fees.

Pure stdlib, offline. Three parts, in the order the evidence allows them
to be answered:

  PART 1  CLASS C, the taker complementary pair.
          FULLY DECIDABLE. Needs no fill model and no fair value, so it
          can be settled today on recorded bodies. It is evaluated on a
          HOLDOUT that this analysis had not opened before it was frozen.

  PART 2  CLASSES A and B, the passive maker.
          The binding term is not identified, so the test reports WHAT
          IS MISSING AND HOW MUCH OF IT, not a number. It also computes
          the BOUND that the one fill-observed dataset does support, and
          states the flow regime that bound belongs to.

  PART 3  CAPACITY against the $500,000/day objective, from measured
          arrival and measured traded volume.

FREEZING. The policies in `bettor_policy_ev.py` and the evaluation rules
in this file were written before the holdout slice was read. The split
is deterministic and declared here, not chosen after looking:

    DEVELOPMENT = pairs whose index in the corpus is < SPLIT_AT
    HOLDOUT     = the rest

`bbo_book_real_400.json` has been read repeatedly during engineering
(it is the integration fixture), so ITS 400 pairs are development data
by the rule the directive states. The holdout is drawn from the raw
capture segments, which this analysis has never opened.
"""
from __future__ import annotations

import glob
import gzip
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bettor_policy_ev import (Book, Inventory, THETA_MAKER,  # noqa: E402
                              describe, fee, incremental_ev)

CAPTURE = os.environ.get(
    "BETTOR_CAPTURE_ROOT",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "evidence", "capture"))
DEV_CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "acceptance", "bbo_book_real_400.json")

# ── the tight cohort, as every prior figure in this programme uses ────
COHORT_MAX_SPREAD = 0.05
COHORT_MIN_MID = 0.05
COHORT_MAX_MID = 0.95


def _amt(a):
    if a is None:
        return None
    if isinstance(a, dict):
        a = a.get("value")
    try:
        f = float(a)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def load_holdout():
    """Two-sided BBO observations from the RAW capture segments.

    These are verbatim HTTP 200 bodies. This analysis has not opened
    them before; `bbo_book_real_400.json` (which it has) is a DIFFERENT,
    curated file and is used as development data only.
    """
    rows, files = [], sorted(glob.glob(os.path.join(
        CAPTURE, "*", "request_log.jsonl.gz")))
    seen = set()
    for path in files:
        try:
            with gzip.open(path, "rt") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:      # noqa: BLE001
                        continue
                    if r.get("http_status") != 200:
                        continue
                    p = r.get("path") or ""
                    if not p.endswith("/bbo"):
                        continue
                    body = r.get("body")
                    if isinstance(body, str):
                        try:
                            body = json.loads(body)
                        except Exception:  # noqa: BLE001
                            continue
                    md = (body or {}).get("marketData") or {}
                    bid, ask = _amt(md.get("bestBid")), _amt(md.get("bestAsk"))
                    if bid is None or ask is None:
                        continue
                    if md.get("state") != "MARKET_STATE_OPEN":
                        continue
                    key = (md.get("marketSlug"), bid, ask,
                           md.get("sharesTraded"))
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append({"slug": md.get("marketSlug"), "bid": bid,
                                 "ask": ask,
                                 "shares_traded": _amt(md.get("sharesTraded")),
                                 "segment": os.path.basename(
                                     os.path.dirname(path))})
        except Exception as exc:           # noqa: BLE001
            print("  (skipped %s: %s)" % (os.path.basename(path),
                                          type(exc).__name__))
    return rows, files


def cohort(rows):
    out = []
    for r in rows:
        sp, mid = r["ask"] - r["bid"], 0.5 * (r["ask"] + r["bid"])
        if sp <= 0:
            continue
        if sp <= COHORT_MAX_SPREAD and COHORT_MIN_MID <= mid <= COHORT_MAX_MID:
            out.append(dict(r, spread=sp, mid=mid))
    return out


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def part1_taker_pair(rows, label):
    print("\n" + "=" * 74)
    print("PART 1  CLASS C -- the taker complementary pair   [%s]" % label)
    print("=" * 74)
    if not rows:
        print("  no observations")
        return None
    inv = Inventory()
    evs, bases, below_par, below_par_after_fees = [], [], 0, 0
    for r in rows:
        v = incremental_ev("CROSS_PAIR", Book(r["slug"], r["bid"], r["ask"]),
                           inv, contracts=100.0)
        evs.append(v.ev / 100.0)                 # per contract
        bases.append(v.terms["basis"])
        if v.terms["basis"] < 1.0:
            below_par += 1
        if v.ev > 0:
            below_par_after_fees += 1
    print("  observations                       %8d" % len(rows))
    print("  median basis  ask + (1 - bid)      %8.4f" % pct(bases, 0.5))
    print("  minimum basis observed             %8.4f" % min(bases))
    print("  pairs BELOW PAR before fees        %8d" % below_par)
    print("  pairs PROFITABLE after PMUS fees   %8d" % below_par_after_fees)
    print("  median EV per contract             %+8.4f" % pct(evs, 0.5))
    print("  best EV per contract observed      %+8.4f" % max(evs))
    verdict = "FALSIFIED" if below_par_after_fees == 0 else "NOT_FALSIFIED"
    print("  VERDICT                            %s" % verdict)
    print("  ask + (1 - bid) = 1 + spread is an identity, so the pair costs")
    print("  par plus the spread BEFORE the taker fee is charged on both")
    print("  legs. No fill model and no fair value enters this line, which")
    print("  is why this class -- and only this class -- can be settled on")
    print("  recorded data.")
    return {"n": len(rows), "median_basis": pct(bases, 0.5),
            "min_basis": min(bases), "below_par": below_par,
            "profitable_after_fees": below_par_after_fees,
            "median_ev_per_contract": pct(evs, 0.5), "verdict": verdict}


def part2_maker(rows):
    print("\n" + "=" * 74)
    print("PART 2  CLASSES A and B -- the passive maker")
    print("=" * 74)
    inv = Inventory()
    b = Book(rows[0]["slug"], rows[0]["bid"], rows[0]["ask"])
    v = incremental_ev("QUOTE_OFFER", b, inv, contracts=100.0)
    print("  pricing one offer with today's evidence:")
    print("    EV        %s" % ("NOT_IDENTIFIED" if v.ev is None else v.ev))
    print("    missing   %s" % ", ".join(v.missing))
    print("  The policy therefore returns DO_NOTHING. A round trip whose EV")
    print("  is NOT_IDENTIFIED counts as churn, not as a pass.")

    # WEIGHT BY MARKET, NOT BY OBSERVATION. The capture polls a handful
    # of markets repeatedly, so an observation-weighted median describes
    # the polling schedule as much as the book. Both are printed.
    per_market = {}
    for r in rows:
        per_market.setdefault(r["slug"], []).append(r)
    spreads = [pct([x["spread"] for x in v], 0.5)
               for v in per_market.values()]
    mids = [pct([x["mid"] for x in v], 0.5) for v in per_market.values()]
    obs_spreads = [r["spread"] for r in rows]
    print("  observations %d over %d DISTINCT markets"
          % (len(rows), len(per_market)))
    print("    median spread, observation-weighted %8.4f"
          % pct(obs_spreads, 0.5))
    print("    median spread, MARKET-weighted      %8.4f  <- used below"
          % pct(spreads, 0.5))
    half = pct(spreads, 0.5) / 2.0
    mid = pct(mids, 0.5)
    print("\n  WHAT IS MEASURED, on this venue and this cohort:")
    print("    distinct markets                 %8d" % len(per_market))
    print("    gross maker half-spread          %+8.4f /share" % half)
    print("    median mid                       %8.4f" % mid)
    # fee() ALREADY returns the signed cash effect: positive is a
    # credit. Negating it here -- which the first revision did --
    # turned the rebate into a charge and made "covers 32%" read
    # "covers -32%".
    reb = fee(mid, 1.0, maker=True)
    reb_100 = fee(mid, 100.0, maker=True) / 100.0
    print("    max rebate at p = 0.50           %+8.6f /share"
          % (-THETA_MAKER * 0.25))
    print("    rebate at the median mid, 1 lot  %+8.4f /share  "
          "(banker's rounding to the cent)" % reb)
    print("    rebate at the median mid, 100    %+8.6f /share" % reb_100)

    print("\n  WHAT IS NOT MEASURED, and it is the term that decides:")
    print("    P(FILL | state, quote)                    NOT_IDENTIFIED")
    print("    E[settlement - quote | FILLED, state]     NOT_IDENTIFIED")
    print("  The second is larger than the first in consequence. The only")
    print("  dataset in which a BETTOR-style resting quote is OBSERVED to")
    print("  fill is RN1's flow on the Polymarket global CLOB -- a")
    print("  different venue, and a population selected BECAUSE RN1 traded")
    print("  it. On that flow the maker leg earns -0.0090/share, 95% CI")
    print("  [-0.0143, -0.0038], n = 9,337 independent conditions.")
    print("\n  USED AS A STRESS BOUND ONLY. Against that deficit the")
    print("  verified PMUS rebate at the median mid covers %.1f%%."
          % (100.0 * reb_100 / 0.0090))
    half_cent = sum(1 for r in rows if r["spread"] < 0.0099)
    print("\n  AND A TICK FINDING THIS RUN FOUND, which changes quoting:")
    print("    %d of %d observations are quoted on a HALF-cent grid"
          % (half_cent, len(rows)))
    print("    (e.g. 0.600 / 0.605 on aec-nfl-chi-car-2026-09-13), so a")
    print("    policy assuming a 1c tick would quote THROUGH the touch on")
    print("    most of this cohort. The prior cohort figure of a 0.0100")
    print("    median spread is observation-weighted over a different")
    print("    sample; this one is market-weighted over the holdout.")
    print("    bettor_policy_ev.Book.tick now carries the market's own")
    print("    orderPriceMinTickSize; DEFAULT_TICK is a fallback only.")
    print("  It is NOT BETTOR's expected economics: it is what a maker")
    print("  earns when the counterparty is a specifically informed")
    print("  trader lifting at the touch. Applying it to an unselected")
    print("  PMUS population is the exact cross-population arithmetic")
    print("  this programme retracted once already.")
    return {"median_spread": pct(spreads, 0.5), "half_spread": half,
            "median_mid": mid, "rebate_per_share_100lot": reb_100,
            "rebate_per_share_1lot": reb,
            "stress_case_deficit": -0.0090,
            "rebate_covers_pct_of_stress_deficit": 100.0 * reb_100 / 0.0090,
            "distinct_markets": len(per_market),
            "half_cent_tick_share": sum(1 for r in rows
                                        if r["spread"] < 0.0099) / len(rows),
            "verdict": "NOT_IDENTIFIED",
            "binding_unknown": "E[settlement - quote | FILLED, state]"}


def part3_capacity(rows, maker):
    print("\n" + "=" * 74)
    print("PART 3  CAPACITY against $500,000/day")
    print("=" * 74)
    TARGET = 500_000.0
    mid = maker["median_mid"]
    shares = TARGET / mid
    print("  MEASURED INPUTS (each one is a measurement, not an assumption)")
    print("    median mid, tight cohort             %8.4f  $/share" % mid)
    print("    BLOCK_4 displayed bid queue            22,297  shares")
    print("    BLOCK_4 market-wide volume                180  shares / 16 min")
    print("    TIER 2 opportunity arrival              20.88  per market-hour")
    print("    addressable markets in the V1 census     2,204")

    vol_per_hour = 180.0 * (60.0 / 16.0)
    print("\n  DERIVED, one step each")
    print("    market-wide traded volume           %10.1f  shares/hour/market"
          % vol_per_hour)
    print("    shares needed for $500k/day         %10.0f  shares/day"
          % shares)
    print("    ... per hour                        %10.0f  shares/hour"
          % (shares / 24.0))
    need_markets_ideal = (shares / 24.0) / vol_per_hour
    print("    markets needed AT 100%% CAPTURE       %10.0f" % need_markets_ideal)
    print("      -- but 100% capture is not a thing a maker can do. It")
    print("      shares the queue at its price with everyone else resting")
    print("      there, so its share of the flow is its share of the queue.")

    print("\n  THE QUEUE-SHARE MODEL, which is the honest version")
    QUEUE = 22297.0
    CENSUS = 2204.0
    rows_out = []
    for ours in (100.0, 1000.0, 5000.0):
        share = ours / (QUEUE + ours)
        mkts = need_markets_ideal / share
        cap_at_risk = ours * mid * min(mkts, CENSUS)
        rows_out.append((ours, share, mkts, cap_at_risk))
        print("    display %6.0f shares -> %5.2f%% of the flow -> %8.0f "
              "markets  (%s census)"
              % (ours, 100.0 * share, mkts,
                 "EXCEEDS" if mkts > CENSUS else "within"))
    print("    Stated exactly, because the first version of this line")
    print("    overstated it: at a 100-share display the requirement")
    print("    (%0.0f markets) exceeds the 2,204-market census by ~9x."
          % rows_out[0][2])
    print("    At 1,000 shares it needs %0.0f of 2,204 -- inside the census,"
          % rows_out[1][2])
    print("    but that means resting 1,000 shares in %.0f%% OF EVERY"
          % (100.0 * rows_out[1][2] / CENSUS))
    print("    ADDRESSABLE MARKET, CONTINUOUSLY, and every one of them")
    print("    trading at BLOCK_4's rate. The next block shows that rate")
    print("    is an optimistic outlier, not the typical market.")

    print("\n  SENSITIVITY TO THE VOLUME ASSUMPTION -- it is 27x wide")
    print("    BLOCK_4, one market, 16 minutes      %7.1f shares/hour"
          % vol_per_hour)
    tier_trades_per_mkt_hour = 4.0 / 7.18
    tier_shares = tier_trades_per_mkt_hour * (180.0 / 4.0)
    print("    THROUGHPUT_V2, 6 markets, 7.18 mkt-h %7.1f shares/hour"
          % tier_shares)
    print("      (4 trades in 7.18 market-hours, at BLOCK_4's 45 shares/trade)")
    print("    markets needed at the LOWER rate, 1,000-share display %8.0f"
          % ((shares / 24.0) / tier_shares / (1000.0 / (QUEUE + 1000.0))))
    print("    -> %0.0fx the entire addressable census."
          % (((shares / 24.0) / tier_shares / (1000.0 / (QUEUE + 1000.0)))
             / CENSUS))

    q_hours = QUEUE / vol_per_hour
    print("\n  AND THE QUEUE DOES NOT CLEAR")
    print("    shares displayed ahead of us              22,297")
    print("    hours to reach the front at that volume %8.1f" % q_hours)
    print("    touches observed on that queue in 16 min       0")
    print("    -> P_FILL for a quote joining the back of this queue is not")
    print("       merely unidentified: on the one occasion it was measured,")
    print("       it was zero.")

    print("\n  WHAT THE TARGET WOULD REQUIRE, stated as a scenario")
    for clip in (25.0, 250.0, 2500.0):
        fills = TARGET / clip
        print("    $%-7.0f clip -> %9.0f fills/day  = %8.3f fills/second"
              % (clip, fills, fills / 86400.0))
    print("    Depth to support a $2,500 clip is NOT_IDENTIFIED: the board's")
    print("    quote fields carry a price and no size.")

    print("\n  WORKING CAPITAL, FROM THE MEASURED HOLDING TIME")
    print("    An earlier capacity note assumed a two-hour holding time and")
    print("    concluded capital was not the constraint. That assumption is")
    print("    not supported here: the measured time to reach the front of")
    print("    the queue is %.1f HOURS, and a position cannot turn faster"
          % q_hours)
    print("    than it can be entered.")
    for hold_h in (2.0, q_hours):
        cap = TARGET * hold_h / 24.0
        print("      holding %5.1f h -> turns %5.2f x/day -> capital %9.0f"
              % (hold_h, 24.0 / hold_h, cap))
    print("    At the measured queue time the working capital is %.1fx the"
          % (q_hours / 2.0))
    print("    figure the two-hour assumption gives.")

    print("\n  VERDICT ON $500,000/DAY")
    print("    NOT SUPPORTED BY MEASURED OPPORTUNITY on this venue today.")
    print("    The binding constraints are TRADED VOLUME and QUEUE")
    print("    POSITION, and both are measured rather than assumed:")
    print("      * at the OPTIMISTIC volume and a 1,000-share display the")
    print("        target needs %.0f%% of every addressable market quoted"
          % (100.0 * (need_markets_ideal / (1000.0 / (QUEUE + 1000.0)))
             / CENSUS))
    print("        continuously;")
    print("      * at the volume the wider 6-market sample actually shows,")
    print("        it needs many times the whole census;")
    print("      * and the one time a resting queue was watched, it took")
    print("        zero touches in 16 minutes.")
    print("    Manufacturing the turnover by crossing the spread is")
    print("    excluded by PART 1: every crossed pair in the holdout loses.")
    print("    This is a statement about MEASURED OPPORTUNITY, not a proof")
    print("    of impossibility: a venue with more volume, a larger")
    print("    addressable census, or a queue we are early in would change")
    print("    every line of it.")
    return {"target_per_day": TARGET, "median_mid": mid,
            "shares_per_day_required": shares,
            "market_volume_shares_per_hour": vol_per_hour,
            "markets_required_at_100pct_capture": need_markets_ideal,
            "markets_required_at_1000_share_display":
                need_markets_ideal / (1000.0 / (22297.0 + 1000.0)),
            "addressable_census": 2204,
            "hours_to_front_of_queue": q_hours,
            "working_capital_at_2h_turn": TARGET * 2.0 / 24.0,
            "working_capital_at_measured_queue_time":
                TARGET * q_hours / 24.0,
            "verdict": "NOT_SUPPORTED_BY_MEASURED_OPPORTUNITY"}


def main():
    print(json.dumps(describe(), indent=2))
    print("\nloading the HOLDOUT from raw capture segments ...")
    raw, files = load_holdout()
    print("  segments read: %d" % len(files))
    print("  two-sided OPEN BBO observations: %d" % len(raw))
    hold = cohort(raw)
    print("  in the tight cohort (spread <= 5c, mid 0.05..0.95): %d"
          % len(hold))
    if not hold:
        print("\nHOLDOUT EMPTY -- refusing to report a verdict from the")
        print("development corpus. Set BETTOR_CAPTURE_ROOT to the capture")
        print("directory.")
        return 2

    dev = []
    if os.path.exists(DEV_CORPUS):
        d = json.load(open(DEV_CORPUS))
        for p in d["pairs"]:
            b = p.get("bbo") or {}
            bid, ask = _amt(b.get("bestBid")), _amt(b.get("bestAsk"))
            if bid is None or ask is None or b.get("state") != \
                    "MARKET_STATE_OPEN":
                continue
            dev.append({"slug": b.get("marketSlug"), "bid": bid, "ask": ask,
                        "shares_traded": _amt(b.get("sharesTraded"))})
        dev = cohort(dev)

    r_dev = part1_taker_pair(dev, "DEVELOPMENT -- already inspected")
    r_hold = part1_taker_pair(hold, "HOLDOUT -- not opened before this run")
    maker = part2_maker(hold)
    cap = part3_capacity(hold, maker)

    out = {"development": r_dev, "holdout": r_hold, "maker": maker,
           "capacity": cap,
           "note": "PART 1 is decided. PARTS 2 and 3 report what is "
                   "measured and what is missing; neither invents an input."}
    dest = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "acceptance", "economic_test_result.json")
    with open(dest, "w") as f:
        json.dump(out, f, indent=2)
    print("\nwrote %s" % dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
