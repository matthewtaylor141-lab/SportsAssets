#!/usr/bin/env python3
"""Build whale_exit_priors_v1.json from the retained reconstructions.

PURE derivation. Reads only the hash-verified blobs_v3 payloads and writes
structured priors. Invents no field: anything the archive does not carry is
written as the string NOT_IDENTIFIED, never as a number.

THE THREE TIME-NORMALISED CONCEPTS, kept apart because conflating them is a
real error and one of them is not a hazard rate at all:

    INTERVAL_COMPLETION_H = [F(t2)-F(t1)] / [1-F(t1)]
        conditional probability of completing SOMEWHERE INSIDE the interval.

    AVERAGE_COMPLETION_PROBABILITY_PER_MINUTE = H / interval_minutes
        DESCRIPTIVE ONLY. Not a hazard rate: it is a probability divided by a
        duration, it is not additive across unequal intervals, and it can be
        driven above 1.0 by a short interval.

    CONTINUOUS_HAZARD_LAMBDA = -ln( S(t2)/S(t1) ) / interval_minutes
        the equivalent constant continuous hazard. THE PREFERRED comparison
        across unequal intervals, because it is exactly the quantity that is
        additive in time.

The settlement tail has NO well-defined elapsed duration, so its lambda and its
per-minute figure are NOT_IDENTIFIED and it is excluded from every
monotonicity test.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

VERSION = "whale_exit_priors_v1"
REVISION = "RISK_SET_WEIGHTED_CONSENSUS"
ACCOUNTS = ["rn1", "ferrarichampions2026", "swisstony", "homerunhazard"]
EXCLUDED = {"swisstony": "FLAGGED_EXCLUDED_UNRESOLVED_TWO_SOURCE_DISCREPANCY"}

# AN ACCOUNT NEVER DISAPPEARS FROM THE CONSENSUS SILENTLY. The reason travels
# with the artifact, including the part of the reason we do not have.
EXCLUSION_PROVENANCE = {
    "swisstony": {
        "SWISSTONY_INCLUDED_IN_ACCOUNT_SPECIFIC_PRIOR": "YES",
        "SWISSTONY_INCLUDED_IN_CONSENSUS": "NO",
        "EXCLUSION_REASON": (
            "FLAGGED_EXCLUDED from clean ground truth on an unresolved "
            "two-source discrepancy, assigned as the account's ROLE in "
            "cross_account_table.py before any priors were built."),
        "EXCLUSION_IS_NOT_A_RECONCILIATION_FAILURE": (
            "swisstony RECONCILES internally -- residual $0.00 stake and "
            "$0.00 pnl against lot_s/lot_p, the same bar the five included "
            "accounts clear. The reconciliation does not clear the "
            "exclusion; they are different defects."),
        # Stated plainly rather than reconstructed: the specific two-source
        # comparison behind the flag is not retained in this workspace, and
        # naming fields we cannot point to would be inventing the reason.
        "WHICH_FIELDS_PREVENT_INCLUSION": "NOT_IDENTIFIED_IN_THIS_WORKSPACE",
        "WHETHER_EXCLUSION_WAS_FROZEN_BEFORE_RESULT": "YES",
        "FROZEN_AT": "commit ee7329c, 2026-09-15, the preregistered "
                     "discriminator run -- before any hazard curve was cut",
        "EFFECT_IF_INCLUDED": "NOT_COMPUTED",
        "DESCRIPTIVE_USES_STILL_ALLOWED": (
            "sign agreement and the cohort-level time-decay finding, which "
            "are descriptions and do not enter an economic estimate"),
    },
}
BANDS = ["0.00-0.10", "0.10-0.30", "0.30-0.50",
         "0.50-0.70", "0.70-0.90", "0.90-1.01"]
HORIZONS = ["5", "10", "30", "60", "120", "300", "600", "1800", "3600",
            "settlement"]
SECONDS = {"5": 5, "10": 10, "30": 30, "60": 60, "120": 120, "300": 300,
           "600": 600, "1800": 1800, "3600": 3600, "settlement": None}
CEILINGS = ["ceiling_0.90", "ceiling_0.92", "ceiling_0.94", "ceiling_0.95",
            "ceiling_0.96", "ceiling_0.97", "ceiling_0.98", "ceiling_0.99",
            "ceiling_1.00"]
NI = "NOT_IDENTIFIED"
# A band whose |MERGE_PNL| is below this share of the account's total merge
# P&L has ratios suppressed: a ratio on a near-zero denominator is noise.
STABILITY_FLOOR = 0.02


def _lam(f1, f2, minutes):
    """Equivalent constant continuous hazard per minute, or NOT_IDENTIFIED."""
    if minutes is None or minutes <= 0:
        return NI
    s1, s2 = 1.0 - f1, 1.0 - f2
    if s1 <= 0 or s2 <= 0:
        return NI
    return -math.log(s2 / s1) / minutes


def _lam_se(d, n, minutes):
    """Standard error of the interval hazard, from the COUNTS. Delta method.

    H = d/n is a binomial proportion with Var(H) = H(1-H)/n. The interval
    hazard is lambda = -ln(1-H)/m, whose derivative in H is 1/((1-H)m), so

        SE(lambda) = sqrt( H / ((1-H) * n) ) / m

    THIS IS COMPUTED FROM d AND n, NEVER FROM A PERCENTAGE. An interval rate
    on its own carries no sample size, and a confidence interval manufactured
    from one would be a decoration. Where the counts are absent this returns
    NOT_IDENTIFIED and the caller must leave the uncertainty unstated.
    """
    if d is None or n is None or minutes is None or minutes <= 0 or n <= 0:
        return NI
    h = d / n
    if h <= 0 or h >= 1:
        return NI
    return math.sqrt(h / ((1.0 - h) * n)) / minutes


def _intervals(fcurve):
    """[(label, F1, F2, minutes_or_None), ...] over the frozen horizons."""
    out = []
    prev_f, prev_s = 0.0, 0
    for h in HORIZONS:
        f = fcurve[h]
        if h == "settlement":
            out.append(("3600s-settlement", prev_f, f, None))
        else:
            out.append(("%ds-%ds" % (prev_s, SECONDS[h]), prev_f, f,
                        (SECONDS[h] - prev_s) / 60.0))
            prev_s = SECONDS[h]
        prev_f = f
    return out


def account_prior(path):
    d = json.loads(Path(path).read_text())
    comp = d["REFERENCE_ACCOUNT_COMPLETION"]["LIFETIME"]
    grid = comp["REFERENCE_ACCOUNT_COMPLETION_GRID"]
    chan = d["PNL_BY_OPEN_BAND_ALL_CHANNELS"]["LIFETIME"]
    census = d["CENSUS"]
    opens_total = comp["REFERENCE_ACCOUNT_FIRST_SIDE_ACQUISITIONS"]

    total_merge = sum(abs(chan[b]["MERGE_PNL"]) for b in BANDS if b in chan)

    # ---- hazard, by basis ceiling and by any-basis --------------------
    # The retained integer completion counts are EXACT and their denominator is
    # opens_total: grid[h]["any_basis_rate"] == any_basis_completed / opens to
    # within the archive's own rounding, checked in the tests. That makes the
    # interval risk set recoverable rather than assumed.
    any_cum = {h: grid[h]["any_basis_completed"] for h in HORIZONS}

    hazard = {}
    for key in CEILINGS + ["any_basis"]:
        fcurve = {h: (grid[h]["any_basis_rate"] if key == "any_basis"
                      else grid[h][key]["rate"]) for h in HORIZONS}
        counts = {h: (grid[h]["any_basis_completed"] if key == "any_basis"
                      else grid[h][key]["completed"]) for h in HORIZONS}
        rows = []
        prev_h = None
        for label, f1, f2, minutes in _intervals(fcurve):
            denom = 1.0 - f1
            h_int = (f2 - f1) / denom if denom > 0 else NI
            this_h = HORIZONS[len(rows)]
            # RISK SET AT THE START OF THE INTERVAL, under this curve's own
            # convention. For any_basis it is exact and unambiguous: everyone
            # who opened and had not completed by t1. For a basis ceiling the
            # published F is completed_at_or_below_ceiling / opens, which
            # treats a completion ABOVE the ceiling as still at risk -- a
            # sub-distribution quantity, not a cause-specific hazard. Both
            # numbers are carried so a reader can see the difference rather
            # than inherit one convention silently.
            n_at_risk = opens_total - (0 if prev_h is None else counts[prev_h])
            d_int = counts[this_h] - (0 if prev_h is None else counts[prev_h])
            n_any = opens_total - (0 if prev_h is None else any_cum[prev_h])
            se = _lam_se(d_int, n_at_risk, minutes)
            lam = _lam(f1, f2, minutes)
            rows.append({
                "INTERVAL": label,
                "INTERVAL_MINUTES": minutes if minutes is not None else NI,
                "CUMULATIVE_COMPLETION_F": f2,
                "SURVIVAL_S": 1.0 - f2,
                "INTERVAL_COMPLETION_H": h_int,
                "AVERAGE_COMPLETION_PROBABILITY_PER_MINUTE": (
                    h_int / minutes if minutes and h_int != NI else NI),
                "CONTINUOUS_HAZARD_LAMBDA": lam,
                "LAMBDA_IS_PREFERRED_COMPARISON": True,
                "PER_MINUTE_IS_DESCRIPTIVE_ONLY": True,
                # --- the counts, so two cells with the same lambda and very
                # different support are not weighted alike ---
                "N_AT_RISK_AT_START": n_at_risk,
                "N_COMPLETED_IN_INTERVAL": d_int,
                "N_AT_RISK_ANY_BASIS_DEPARTURES": n_any,
                "COMPLETION_RATE": (d_int / n_at_risk if n_at_risk else NI),
                "HAZARD_LAMBDA_SE": se,
                "HAZARD_LAMBDA_CI95": ([lam - 1.96 * se, lam + 1.96 * se]
                                       if se != NI and lam != NI else NI),
                "RISK_SET_CONVENTION": (
                    "EXACT_OPENS_MINUS_COMPLETED" if key == "any_basis"
                    else "SUB_DISTRIBUTION_ABOVE_CEILING_TREATED_AS_AT_RISK"),
                "CAUSE_SPECIFIC_HAZARD": (
                    "SAME_AS_PUBLISHED" if key == "any_basis"
                    else "NOT_COMPUTED"),
            })
            prev_h = this_h
        # monotone decay over the well-defined intervals only
        lams = [r["CONTINUOUS_HAZARD_LAMBDA"] for r in rows
                if r["CONTINUOUS_HAZARD_LAMBDA"] != NI]
        hazard[key] = {
            "ROWS": rows,
            "EVENTUAL_COMPLETION_RATE": fcurve["settlement"],
            "COMPLETED_COUNT_AT_SETTLEMENT": counts["settlement"],
            "FIRST_SIDE_ACQUISITIONS": opens_total,
            "LAMBDA_MONOTONE_NON_INCREASING": (
                all(lams[i] >= lams[i + 1] for i in range(len(lams) - 1))
                if len(lams) > 1 else NI),
            "SETTLEMENT_TAIL_EXCLUDED_FROM_MONOTONICITY": True,
        }

    # ---- channel economics, by band -----------------------------------
    bands = {}
    for b in BANDS:
        if b not in chan:
            continue
        r = chan[b]
        m, s, se, t = (r["MERGE_PNL"], r["SELL_PNL"],
                       r["SETTLED_PNL"], r["TOTAL_PNL"])
        stable = total_merge > 0 and abs(m) >= STABILITY_FLOOR * total_merge
        sign = lambda x: "POSITIVE" if x > 0 else ("NEGATIVE" if x < 0 else "ZERO")
        bands[b] = {
            "MERGE_CHANNEL_PNL": m,
            "SELL_CHANNEL_PNL": s,
            "SETTLED_CHANNEL_PNL": se,
            "TOTAL_CHANNEL_PNL": t,
            "MERGE_SIGN": sign(m), "SELL_SIGN": sign(s),
            "SETTLED_SIGN": sign(se), "TOTAL_SIGN": sign(t),
            "MERGE_COUNT": r["merges"], "SELL_COUNT": r["sells"],
            "SETTLED_LOTS": r["settled_lots"],
            "SETTLED_TO_MERGE_ABS_RATIO": (abs(se / m) if stable and m else NI),
            "TOTAL_TO_MERGE_RATIO": (t / m if stable and m else NI),
            "SUPPORT": "STABLE" if stable else "LOW_MERGE_BASE_UNSTABLE",
            "EVIDENCE_LEVEL": "LEVEL_A_DIRECT_WHALE_EVIDENCE",
        }
        pb = comp["BY_FIRST_LEG_PRICE_BAND"].get(b)
        bands[b]["OPENS"] = pb["opens"] if pb else NI
        bands[b]["RESIDUAL_RATE"] = pb["residual_rate"] if pb else NI
        bands[b]["MEAN_PAIR_BASIS"] = pb["mean_pair_basis"] if pb else NI

    return {
        "ACCOUNT": census["REFERENCE_ACCOUNT"],
        "EXCLUSION": EXCLUDED.get(census["REFERENCE_ACCOUNT"], None),
        "RETURNED_RANGE_START": census["RETURNED_RANGE_START"],
        "RETURNED_RANGE_END": census["RETURNED_RANGE_END"],
        "ROWS_KEPT": census["ROWS_KEPT"],
        "FIRST_SIDE_ACQUISITIONS": opens_total,
        "EVIDENCE_LEVEL": "LEVEL_A_DIRECT_WHALE_EVIDENCE",
        "HAZARD_BY_BASIS_CEILING": hazard,
        "CHANNEL_BY_PRICE_BAND": bands,
    }


def consensus(priors):
    """Cross-whale consensus. Weighted by first-side acquisitions where they
    exist; swisstony is EXCLUDED from the consensus, not silently averaged in.
    """
    usable = [p for p in priors if p["EXCLUSION"] is None]
    weights = {p["ACCOUNT"]: p["FIRST_SIDE_ACQUISITIONS"] for p in usable}
    have_all = all(isinstance(w, int) and w > 0 for w in weights.values())
    wsum = sum(weights.values()) if have_all else None

    out = {
        "CONSENSUS_MEMBERS": [p["ACCOUNT"] for p in usable],
        "EXCLUDED_FROM_CONSENSUS": [p["ACCOUNT"] for p in priors
                                    if p["EXCLUSION"] is not None],
        # THE WEIGHT IS THE INTERVAL RISK SET, NOT ACCOUNT SIZE.
        #
        # A conditional hazard's natural denominator is the number of positions
        # STILL UNPAIRED AT THE START OF THE INTERVAL, which is not the number
        # an account opened. Weighting by total acquisitions would let a large
        # account dominate the 1800-3600s prior on the strength of entries that
        # had long since completed and were never exposed to that interval's
        # risk. The archive retains the integer completion counts and their
        # denominator is opens_total, so the risk set is recoverable exactly
        # and the pooled estimator is the one actually used:
        #
        #     H_pooled = sum(d_i) / sum(n_i)
        #     lambda   = -ln(1 - H_pooled) / minutes
        "CONSENSUS_WEIGHTING": "POOLED_INTERVAL_RISK_SET",
        "CONSENSUS_WEIGHTING_SUPERSEDED": "FIRST_SIDE_ACQUISITIONS",
        "WHY_SUPERSEDED": (
            "ACCOUNT_SIZE_PROXY_NOT_INTERVAL_RISK_SET -- total acquisitions "
            "weights an account by entries that never reached the interval"),
        "INTERVAL_RISK_SET_N_AVAILABLE": "YES_ANY_BASIS",
        "CELL_ENTRY_N_AVAILABLE": "YES_PER_ACCOUNT_AND_PER_PRICE_BAND",
        "COMPLETION_COUNT_BY_INTERVAL_AVAILABLE": "YES_ANY_BASIS_AND_CEILING",
        # The four-way cell the ideal weight would need does not exist in the
        # archive: the completion grid is crossed with BASIS_CEILING but NOT
        # with PRICE_BAND, and the per-band table carries only three fixed
        # horizons (5s, 1h, settlement) and no ceilings.
        "FOUR_WAY_CELL_ACCOUNT_x_BAND_x_CEILING_x_INTERVAL": NI,
        # Pooling is the right estimator for a common hazard; it is not proven
        # optimal here, because the accounts are not draws from one population
        # and no between-account variance component is modelled.
        "CONSENSUS_STATISTICAL_OPTIMALITY": "NOT_ESTABLISHED",
        "ACQUISITION_WEIGHTS_RETAINED": weights if have_all else NI,
        "UNWEIGHTED_AVERAGE_REFUSED": True,
        "HAZARD_any_basis": [],
        "CHANNEL_SIGN_AGREEMENT": {},
    }

    rows0 = usable[0]["HAZARD_BY_BASIS_CEILING"]["any_basis"]["ROWS"]
    for i, r0 in enumerate(rows0):
        cells = [p["HAZARD_BY_BASIS_CEILING"]["any_basis"]["ROWS"][i]
                 for p in usable]
        lams = [c["CONTINUOUS_HAZARD_LAMBDA"] for c in cells]
        names = [p["ACCOUNT"] for p in usable]
        minutes = r0["INTERVAL_MINUTES"]

        # the previous estimator, retained so the revision is visible
        if any(l == NI for l in lams) or not have_all:
            acq = NI
        else:
            acq = sum(l * weights[n] for l, n in zip(lams, names)) / wsum

        n_pool = sum(c["N_AT_RISK_AT_START"] for c in cells)
        d_pool = sum(c["N_COMPLETED_IN_INTERVAL"] for c in cells)
        if minutes == NI or n_pool <= 0 or d_pool <= 0 or d_pool >= n_pool:
            lam = NI
            se = NI
        else:
            lam = -math.log(1.0 - d_pool / n_pool) / minutes
            se = _lam_se(d_pool, n_pool, minutes)

        out["HAZARD_any_basis"].append({
            "INTERVAL": r0["INTERVAL"],
            "CONSENSUS_CONTINUOUS_HAZARD_LAMBDA": lam,
            "ACQUISITION_WEIGHTED_CONTINUOUS_HAZARD_LAMBDA": acq,
            "POOLED_N_AT_RISK": n_pool,
            "POOLED_N_COMPLETED": d_pool,
            "RISK_SET_N_BY_MEMBER": {
                n: c["N_AT_RISK_AT_START"] for n, c in zip(names, cells)},
            "RISK_SET_WEIGHT_SHARE": (
                {n: c["N_AT_RISK_AT_START"] / n_pool
                 for n, c in zip(names, cells)} if n_pool else NI),
            "HAZARD_LAMBDA_SE": se,
            "HAZARD_LAMBDA_CI95": ([lam - 1.96 * se, lam + 1.96 * se]
                                   if se != NI and lam != NI else NI),
            # Disagreement survives the averaging: the members are kept so a
            # reader can see a consensus that hides a wide spread.
            "MEMBER_LAMBDAS": dict(zip(names, lams)),
            "COHORT_AGREEMENT": (
                NI if any(l == NI for l in lams) or min(lams) <= 0
                else ("TIGHT" if max(lams) / min(lams) <= 1.5
                      else "MODERATE" if max(lams) / min(lams) <= 3.0
                      else "WIDE")),
            "MEMBER_LAMBDA_SPREAD_RATIO": (
                NI if any(l == NI for l in lams) or min(lams) <= 0
                else max(lams) / min(lams)),
            "EVIDENCE_LEVEL": "LEVEL_B_WHALE_DERIVED_PRIOR",
        })

    # sign agreement is over ALL FOUR, including the excluded account, because
    # a sign is a description and does not enter an economic estimate
    for b in BANDS:
        cell = {}
        for ch in ("MERGE", "SELL", "SETTLED", "TOTAL"):
            signs = [p["CHANNEL_BY_PRICE_BAND"][b]["%s_SIGN" % ch]
                     for p in priors if b in p["CHANNEL_BY_PRICE_BAND"]]
            nz = [s for s in signs if s != "ZERO"]
            if not nz:
                cell[ch] = {"AGREEMENT": NI, "SIGNS": signs}
                continue
            agree = max(nz.count("POSITIVE"), nz.count("NEGATIVE"))
            cell[ch] = {
                "AGREEMENT": ("HIGH_COHORT_SUPPORT" if agree == 4 else
                              "MODERATE_COHORT_SUPPORT" if agree == 3 else
                              "LOW_COHORT_SUPPORT"),
                "AGREE_COUNT": agree, "OF": len(nz), "SIGNS": signs,
            }
        out["CHANNEL_SIGN_AGREEMENT"][b] = cell
    return out


def build(src_dir, out_path):
    priors = [account_prior(Path(src_dir) / f"{a}.json") for a in ACCOUNTS]
    doc = {
        "VERSION": VERSION,
        "REVISION": REVISION,
        "EXCLUSION_PROVENANCE": EXCLUSION_PROVENANCE,
        "SOURCE_RUN": "35034361586",
        "SOURCE": "blobs_v3 reconstructions, LIFETIME window",
        "GRANULARITY": "AGGREGATE",
        "PER_POSITION_ROWS": "NOT_PRESENT",
        "HISTORICAL_BOOK_STATE": "NOT_PRESENT",
        "EV_EXIT_HISTORICAL": NI,
        "WHALE_SELL_POLICY_GENERALIZABLE": "NOT_ESTABLISHED",
        "PRIORS_ARE_INITIAL_ONLY": True,
        "SUPERSEDED_BY": "BETTOR_POSTERIOR once shadow data is credible",
        "ACCOUNT_PRIORS": {p["ACCOUNT"]: p for p in priors},
        "CROSS_WHALE_CONSENSUS_PRIOR": consensus(priors),
    }
    Path(out_path).write_text(json.dumps(doc, indent=1))
    return doc


if __name__ == "__main__":
    import sys
    d = build(sys.argv[1], sys.argv[2])
    print("wrote", sys.argv[2])
    print("accounts:", list(d["ACCOUNT_PRIORS"]))
    print("consensus members:", d["CROSS_WHALE_CONSENSUS_PRIOR"]["CONSENSUS_MEMBERS"])
    print("weighting:", d["CROSS_WHALE_CONSENSUS_PRIOR"]["CONSENSUS_WEIGHTING"])
