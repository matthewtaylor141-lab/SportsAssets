#!/usr/bin/env python3
"""TRACK P2 -- the FROZEN protocol for the forward native fair-value study.

OFFLINE AND PURE. This module contacts nothing. It defines the experiment,
hashes it, and implements the analysis that will run UNCHANGED at every
checkpoint. The capture runner and the analysis both import their rules
from here so there is exactly one place the experiment is defined.

WHAT IS BEING TESTED
Are contemporaneous TWO-SIDED PMUS prices systematically miscalibrated
against eventual settlement? That is REGISTER 1 -- predictive information.
It is not a taker backtest and not a maker claim, and this file cannot
produce either: it never reads an executable price into the primary
statistic.

WHY SEQUENTIAL, AND WHY IT IS NOT PEEKING
Waiting for 2,500 settled markets to learn that a 6pp deviation exists
would waste weeks. So the protocol looks at N = 250 / 500 / 1,000 / 2,500
settled markets -- but pays for those looks in advance with
HAYBITTLE-PETO boundaries: interim looks must clear |z| >= 3.0
(p < 0.0027) and only the final look uses 1.98. Overall two-sided alpha
stays about 0.05 while each interim look costs almost none of it.
Haybittle-Peto rather than a Lan-DeMets spending function because it is
exact, needs no multivariate-normal integration, and cannot be quietly
mis-implemented in a way that flatters a result.

WHY THERE IS ALSO A FUTILITY RULE
A tiny-but-real deviation is not worth another month of capture. The
protocol therefore stops EARLY FOR FUTILITY when the confidence interval
already excludes the effect size that would matter.

WHAT "MATTERS" MEANS, DERIVED ONCE, IN ADVANCE
MEANINGFUL_EFFECT = 0.02 (2 percentage points), from the cost of actually
crossing on PMUS:
    verified taker fee at p=0.5   0.06 * 0.5 * 0.5   = 0.0150
    half of a one-tick spread     0.01 / 2           = 0.0050
                                                     -------
                                                       0.0200
A calibration deviation smaller than that cannot become a taker trade even
if it is perfectly real. Stating it here, before any data, is what stops it
being chosen later to fit whatever the data shows. It is a REGISTER 1 ->
REGISTER 2 bridge for SIZING ONLY and is never itself a tradability claim.

CLUSTERING -- THE THING MOST LIKELY TO OVERSTATE SIGNIFICANCE
One game emits many markets: moneyline, several spreads, several totals,
props. Their outcomes are strongly dependent. N is counted in settled
MARKETS as instructed, but every uncertainty interval is bootstrapped over
EVENTS, so a 12-market NFL game contributes one draw and not twelve.
Both counts are always reported.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from typing import Any

PROTOCOL_VERSION = "TRACKP2-1"

# ---------------------------------------------------------------- population
POPULATION_RULE = (
    "Every eligible sports market on the CURRENT PMUS board, enumerated "
    "with active=true&closed=false and walked to a REAL terminal boundary "
    "(a page returning zero events). No RN1 filter, no activity, "
    "liquidity, spread, price, sport-performance filter, and no manual "
    "shortlist. Reaching a configured page ceiling is NOT a terminal "
    "boundary and makes the capture PREFIX_BOUNDED.")

EXCLUSION_RULES = (
    "NOT_A_SPORTS_MARKET: event category is not sports",
    "NO_GAME_START_TIME: gameStartTime absent -> TIME_TO_EVENT_STATUS = "
    "NOT_IDENTIFIED; endDate is NEVER substituted",
    "GAME_START_IN_THE_PAST_AT_DISCOVERY: cannot take a T-60 snapshot",
    "MARKET_NOT_OPEN_AT_SNAPSHOT: status != MARKET_STATUS_OPEN",
    "MIDPOINT_NOT_IDENTIFIED: one or both sides of the book absent",
    "SETTLEMENT_NOT_RESOLVED: still PENDING at analysis time",
    "SETTLEMENT_VOID_OR_CANCELED: no binary outcome exists",
    "OUTCOME_NOT_ONE_HOT: outcomePrices is not a clean 1/0 vector",
)

# ------------------------------------------------------------------ capture
SNAPSHOT_OFFSETS_S = {"T_MINUS_60": 3600, "T_MINUS_10": 600}
SNAPSHOT_TOLERANCE_S = 180.0      # a snapshot outside this is recorded with
                                  # its actual offset and excluded from the
                                  # arm it missed, never silently retimed
PRIMARY_ARM = "T_MINUS_60"        # declared now; T_MINUS_10 is secondary

REQUIRED_SNAPSHOT_FIELDS = (
    "marketSlug", "event_id", "event_ticker", "side_ids", "side_definitions",
    "bids", "offers", "best_bid", "best_ask", "spread",
    "stats.lastTradePx", "stats.sharesTraded", "stats.openInterest",
    "status", "gameStartTime", "endDate", "sportsMarketTypeV2", "tags",
    "transactTime", "LOCAL_RECEIPT_WALL", "LOCAL_RECEIPT_MONOTONIC", "RAW",
)

# ------------------------------------------------------------- fair reference
MIDPOINT_DEFINITION = (
    "MIDPOINT = (BEST_BID + BEST_ASK) / 2, from the same book observation. "
    "If either side is absent, MIDPOINT = NOT_IDENTIFIED and the "
    "observation leaves the primary study. The midpoint is a FAIR-MARKET "
    "REFERENCE and is never described as executable.")

OUTCOME_DEFINITION = (
    "OUTCOME = 1 if this market side's outcomePrices entry is exactly 1 "
    "and every other entry is exactly 0; 0 if this side's entry is exactly "
    "0 and exactly one other entry is 1. Anything else is not a binary "
    "outcome. An outcome is NEVER inferred from a price.")

VOID_HANDLING = (
    "VOID and CANCELED settlements are recorded verbatim, counted, and "
    "excluded from the primary statistic because no binary outcome exists. "
    "They are never dropped silently and never treated as 0.")

# ------------------------------------------------------------- the statistic
PRIMARY_STATISTIC = (
    "MEAN_CALIBRATION_RESIDUAL = mean(OUTCOME - MIDPOINT) over eligible "
    "settled market-sides in the PRIMARY_ARM. H0: the mean is 0.")

MEANINGFUL_EFFECT = 0.02
CHECKPOINTS = (250, 500, 1000, 2500)
INTERIM_Z = 3.0            # Haybittle-Peto
FINAL_Z = 1.98
BOOTSTRAP = 4000
BOOTSTRAP_SEED = 20260915

PRICE_BANDS = (("LOW", 0.02, 0.35), ("MID", 0.35, 0.65),
               ("HIGH", 0.65, 0.98))
SEGMENTATION_RULES = (
    "PRIMARY: all eligible sports markets, PRIMARY_ARM only. "
    "PREDECLARED SECONDARY, reported but never gating: (1) T_MINUS_60 vs "
    "T_MINUS_10, (2) the three broad price bands above, (3) sport. "
    "No league, market-type, finer price band, time window or model "
    "variant is examined at any checkpoint. A subgroup noticed after "
    "seeing results is HYPOTHESIS_GENERATING and is labelled as such.")

# --------------------------------------------------- final confirmation split
FINAL_CONFIRMATION_FRACTION = 0.20
FINAL_CONFIRMATION_SALT = "TRACKP2-1/final-confirmation/v1"
FINAL_CONFIRMATION_RULE = (
    "Assignment is a deterministic hash of the market slug with a fixed "
    "salt, computed AT CAPTURE TIME, before any outcome exists. The lowest "
    "20% of the hash space is P2_FINAL_CONFIRMATION and is never inspected "
    "during checkpoints. Hash-based rather than by capture order so that a "
    "capture interrupted mid-slate cannot bias which markets are reserved.")


def assignment(market_slug: str) -> str:
    """EXPLORATORY or P2_FINAL_CONFIRMATION, from identity alone."""
    h = hashlib.sha256((FINAL_CONFIRMATION_SALT + "|" + market_slug)
                       .encode()).digest()
    frac = int.from_bytes(h[:8], "big") / float(1 << 64)
    return ("P2_FINAL_CONFIRMATION" if frac < FINAL_CONFIRMATION_FRACTION
            else "EXPLORATORY")


def midpoint(best_bid: Any, best_ask: Any):
    """None means NOT_IDENTIFIED. A one-sided book has no midpoint, and
    inventing one from the single side present is exactly how an
    execution price sneaks into a fair-value study."""
    try:
        b, a = float(best_bid), float(best_ask)
    except (TypeError, ValueError):
        return None
    if not (0.0 < b <= 1.0 and 0.0 < a <= 1.0) or a < b:
        return None
    return (b + a) / 2.0


def outcome_of(side_index: int, outcome_prices) -> Any:
    """1, 0, or None. Never inferred from a price."""
    if isinstance(outcome_prices, str):
        try:
            outcome_prices = json.loads(outcome_prices)
        except (ValueError, TypeError):
            return None
    try:
        vals = [float(x) for x in (outcome_prices or [])]
    except (TypeError, ValueError):
        return None
    if not vals or side_index >= len(vals):
        return None
    ones = [i for i, v in enumerate(vals) if v == 1.0]
    zeros = [i for i, v in enumerate(vals) if v == 0.0]
    if len(ones) != 1 or len(ones) + len(zeros) != len(vals):
        return None
    return 1 if ones[0] == side_index else 0


# ------------------------------------------------------------------ analysis
def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def cluster_bootstrap(rows, stat, n=BOOTSTRAP, seed=BOOTSTRAP_SEED):
    """Resample EVENTS with replacement. One NFL game emitting twelve
    markets contributes one draw, not twelve -- otherwise the interval is
    narrower than the evidence supports and every gate reads too
    optimistic."""
    if not rows:
        return (None, None)
    by = defaultdict(list)
    for r in rows:
        by[r.get("event_id") or r["market_slug"]].append(r)
    keys = list(by)
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        flat = []
        for _ in range(len(keys)):
            flat.extend(by[keys[rng.randrange(len(keys))]])
        if flat:
            out.append(stat(flat))
    out.sort()
    if not out:
        return (None, None)
    return (out[int(0.025 * len(out))], out[int(0.975 * len(out))])


def mean_residual(rows) -> float:
    return sum(r["residual"] for r in rows) / len(rows)


def calibration_table(rows):
    out = []
    for name, lo, hi in PRICE_BANDS:
        b = [r for r in rows if lo <= r["midpoint"] < hi]
        if not b:
            continue
        out.append({
            "BAND": name, "RANGE": [lo, hi], "N_MARKETS": len(b),
            "N_EVENTS": len({r.get("event_id") or r["market_slug"]
                             for r in b}),
            "MEAN_MIDPOINT": sum(r["midpoint"] for r in b) / len(b),
            "OBSERVED_SETTLEMENT_RATE": sum(r["outcome"] for r in b) / len(b),
            "MEAN_CALIBRATION_RESIDUAL": mean_residual(b),
        })
    return out


def analyse(rows, checkpoint_index: int, label: str) -> dict:
    """The frozen primary analysis. Identical code at every checkpoint.

    `rows` are eligible settled market-sides for ONE arm, each with
    midpoint, outcome, residual, market_slug, event_id, sport.
    """
    n = len(rows)
    res: dict[str, Any] = {
        "LABEL": label,
        "PROTOCOL_VERSION": PROTOCOL_VERSION,
        "CHECKPOINT_INDEX": checkpoint_index,
        "N_MARKETS": n,
        "N_EVENTS": len({r.get("event_id") or r["market_slug"]
                         for r in rows}),
    }
    if n == 0:
        res["GATE"] = "P2-B"
        res["GATE_REASON"] = "NO_ELIGIBLE_SETTLED_MARKETS_YET"
        return res

    mean = mean_residual(rows)
    lo, hi = cluster_bootstrap(rows, mean_residual)
    res.update({
        "MEAN_MIDPOINT": sum(r["midpoint"] for r in rows) / n,
        "OBSERVED_SETTLEMENT_RATE": sum(r["outcome"] for r in rows) / n,
        "MEAN_CALIBRATION_RESIDUAL": mean,
        "CI95_CLUSTERED_BY_EVENT": [lo, hi],
        "BRIER": sum((r["midpoint"] - r["outcome"]) ** 2 for r in rows) / n,
        "LOG_LOSS": sum(
            -(r["outcome"] * math.log(min(max(r["midpoint"], 1e-9), 1 - 1e-9))
              + (1 - r["outcome"])
              * math.log(1 - min(max(r["midpoint"], 1e-9), 1 - 1e-9)))
            for r in rows) / n,
        "CALIBRATION_CURVE": calibration_table(rows),
    })

    # z from the clustered interval, not from a naive per-market SE
    se = ((hi - lo) / (2 * 1.96)) if (lo is not None and hi is not None
                                      and hi > lo) else None
    z = (mean / se) if se else None
    res["CLUSTERED_SE"] = se
    res["Z"] = z
    is_final = checkpoint_index >= len(CHECKPOINTS) - 1
    bound = FINAL_Z if is_final else INTERIM_Z
    res["BOUNDARY_Z"] = bound
    res["BOUNDARY_RULE"] = ("HAYBITTLE_PETO_FINAL" if is_final
                            else "HAYBITTLE_PETO_INTERIM")
    res["MEANINGFUL_EFFECT"] = MEANINGFUL_EFFECT

    # FUTILITY: the interval already excludes anything that could matter,
    # in BOTH directions. Checked first, because "we can stop, the effect
    # cannot be big enough" is a real answer and cheaper than more capture.
    if lo is not None and hi is not None and \
            abs(lo) < MEANINGFUL_EFFECT and abs(hi) < MEANINGFUL_EFFECT:
        res["GATE"] = "P2-C"
        res["GATE_REASON"] = (
            "CI [%0.4f, %0.4f] excludes |effect| >= %0.3f, the deviation "
            "required to clear PMUS taker cost" % (lo, hi, MEANINGFUL_EFFECT))
        return res

    if z is not None and abs(z) >= bound and abs(mean) >= MEANINGFUL_EFFECT:
        res["GATE"] = "P2-A"
        res["GATE_REASON"] = (
            "|z| %.2f >= %.2f and |mean residual| %.4f >= %0.3f"
            % (abs(z), bound, abs(mean), MEANINGFUL_EFFECT))
        return res

    res["GATE"] = "P2-B"
    res["GATE_REASON"] = (
        "point estimate %+.4f, CI [%s, %s], |z| %s vs boundary %.2f -- "
        "continue to the next checkpoint"
        % (mean, "%.4f" % lo if lo is not None else "-",
           "%.4f" % hi if hi is not None else "-",
           "%.2f" % abs(z) if z is not None else "-", bound))
    return res


# ------------------------------------------------------------------- freeze
def spec() -> dict:
    return {
        "PROTOCOL_VERSION": PROTOCOL_VERSION,
        "OBJECTIVE": "Are contemporaneous two-sided PMUS prices "
                     "systematically miscalibrated vs settlement? "
                     "REGISTER 1 only.",
        "POPULATION_RULE": POPULATION_RULE,
        "EXCLUSION_RULES": list(EXCLUSION_RULES),
        "SNAPSHOT_OFFSETS_S": SNAPSHOT_OFFSETS_S,
        "SNAPSHOT_TOLERANCE_S": SNAPSHOT_TOLERANCE_S,
        "PRIMARY_ARM": PRIMARY_ARM,
        "REQUIRED_SNAPSHOT_FIELDS": list(REQUIRED_SNAPSHOT_FIELDS),
        "MIDPOINT_DEFINITION": MIDPOINT_DEFINITION,
        "OUTCOME_DEFINITION": OUTCOME_DEFINITION,
        "VOID_HANDLING": VOID_HANDLING,
        "PRIMARY_STATISTIC": PRIMARY_STATISTIC,
        "MEANINGFUL_EFFECT": MEANINGFUL_EFFECT,
        "MEANINGFUL_EFFECT_DERIVATION":
            "PMUS verified taker fee at p=0.5 (0.06*0.25=0.0150) + half a "
            "one-tick spread (0.0050) = 0.0200. Sizing bridge only; not a "
            "tradability claim.",
        "CHECKPOINTS": list(CHECKPOINTS),
        "CHECKPOINT_N_UNIT": "independent settled MARKETS",
        "UNCERTAINTY_CLUSTER_UNIT": "EVENT",
        "SEQUENTIAL_BOUNDARY": {
            "RULE": "HAYBITTLE_PETO",
            "INTERIM_Z": INTERIM_Z, "FINAL_Z": FINAL_Z,
            "WHY": "exact, needs no MVN integration, and cannot be "
                   "mis-implemented in a way that flatters a result",
        },
        "FUTILITY_RULE": "stop P2-C when the clustered CI excludes "
                         "|effect| >= MEANINGFUL_EFFECT in both directions",
        "SEGMENTATION_RULES": SEGMENTATION_RULES,
        "PRICE_BANDS": [list(b) for b in PRICE_BANDS],
        "FINAL_CONFIRMATION_RULE": FINAL_CONFIRMATION_RULE,
        "FINAL_CONFIRMATION_FRACTION": FINAL_CONFIRMATION_FRACTION,
        "FINAL_CONFIRMATION_SALT": FINAL_CONFIRMATION_SALT,
        "BOOTSTRAP": BOOTSTRAP, "BOOTSTRAP_SEED": BOOTSTRAP_SEED,
        "REGISTERS": {
            "1": "PREDICTIVE / CALIBRATION EDGE -- what P2 measures",
            "2": "EXECUTABLE TAKER EDGE -- not measured by P2",
            "3": "MAKER EXECUTION EDGE -- not measured by P2; BLOCK_4 is "
                 "binding evidence that displayed maker edge need not "
                 "become executable edge",
        },
        "AUTHORIZATION": "READ-ONLY GET to gateway.polymarket.us. No "
                         "orders, no capital, no production writes, "
                         "mirror_live=false.",
    }


def spec_blob() -> str:
    return json.dumps(spec(), indent=1, sort_keys=True, default=str)


def spec_hash() -> str:
    return hashlib.sha256(spec_blob().encode()).hexdigest()


if __name__ == "__main__":
    print(spec_blob())
    print("\nPROTOCOL_SHA256 %s" % spec_hash())
