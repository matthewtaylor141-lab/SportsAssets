#!/usr/bin/env python3
"""Derive WHALE_REFERENCE_PRIORS_V1.json from the sealed whale artefacts.

Contacts nothing. Reads `evidence/whale_audit/whale_exit_priors_v1.json` and
emits the reference priors BETTOR's bridge consumes. Every number is COPIED or
ARITHMETICALLY DERIVED from that file -- nothing here is authored.

THE STRUCTURE THIS EXTRACTS, which is the empirical core of the whole bridge:

  MERGE_CHANNEL_PNL is POSITIVE in every band below 0.50 and NEGATIVE in every
  band above 0.70, in the THREE PRIMARY accounts and also in the swisstony
  sensitivity run. Sign agreement across independently reconstructed accounts
  is a mechanism-level regularity in a way that any single account's magnitude
  is not.

  THE CANONICAL PRIOR IS THREE ACCOUNTS: rn1, ferrarichampions2026,
  homerunhazard. swisstony is a GHOST / SENSITIVITY prior and is emitted
  separately under WHALE_PRIOR_4_ACCOUNT_SENSITIVITY. It was FLAGGED_EXCLUDED
  before any prior was built, on a two-source SIGN disagreement about its pair
  channel -- the very quantity the consensus is built on -- and nothing in this
  workspace resolves it. SWISSTONY_PRIMARY_PRIOR_ELIGIBILITY = NOT_ESTABLISHED.

  MEAN_PAIR_BASIS rises monotonically with the band, and CROSSES 1.00 in the
  top bands for ferrari (1.00555, 1.01177), homerunhazard (1.00584, 1.01022)
  and, in the sensitivity run, swisstony (1.00932). A completed pair at basis
  > 1.00 cost more in GROSS trade prices than the $1 it redeems. That is a
  structural fact about the prices paid; it is NOT an established net loss,
  because rebates, maker rewards and other incentives are
  NOT_SEPARATELY_RETAINED. RN1's maximum is 0.99825, which is a descriptive
  structural distinction in the retained aggregate data and not a verdict on
  who traded better.

EVERY CELL HERE IS CONDITIONED ON WHALE-SELECTED ENTRIES. The sample is
positions the whales chose to open. Declined markets and refused prices are
absent, so a band row describes RETAINED WHALE-ENTERED POSITIONS in that band
and never an arbitrary entry at that price. SELECTION_CONDITION travels with
every row.

The mechanism reading -- and it is a reading, recorded as such -- is that the
band is the price of the FIRST leg acquired. Acquire the cheap leg first and
the pair completes at a good basis; acquire the expensive leg first and it
completes at or above par. That is one hypothesis consistent with the numbers,
not a demonstrated causal claim, and `MECHANISM_READING_STATUS` says so.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "evidence" / "whale_audit" / "whale_exit_priors_v1.json"
OUT = HERE / "WHALE_REFERENCE_PRIORS_V1.json"

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_ESTABLISHED = "NOT_ESTABLISHED"
BANDS = ("0.00-0.10", "0.10-0.30", "0.30-0.50", "0.50-0.70", "0.70-0.90",
         "0.90-1.01")

# Correction 1. The canonical prior and the ghost, kept apart at every level.
PRIMARY_WHALE_PRIOR_ACCOUNTS = ("ferrarichampions2026", "homerunhazard", "rn1")
SENSITIVITY_ONLY_ACCOUNTS = ("swisstony",)

# Correction 2. Travels with every cell this file emits.
SELECTION_CONDITION = "OBSERVED_WHALE_ENTERED_POSITIONS_ONLY"


def load(path=None):
    return json.loads(Path(path or SOURCE).read_text())


def channel_cells(src, accounts=None):
    """ACCOUNT x PRICE_BAND -- one measured marginal, never a cross product."""
    keep = set(accounts or PRIMARY_WHALE_PRIOR_ACCOUNTS)
    out = []
    for acct, r in sorted(src["ACCOUNT_PRIORS"].items()):
        if acct not in keep:
            continue
        cb = r.get("CHANNEL_BY_PRICE_BAND") or {}
        for band in BANDS:
            c = cb.get(band)
            if not c:
                continue
            merge = c.get("MERGE_CHANNEL_PNL")
            settled = c.get("SETTLED_CHANNEL_PNL")
            opens = c.get("OPENS")
            out.append({
                "ACCOUNT": acct,
                "ACCOUNT_ROLE": ("SENSITIVITY_ONLY"
                                 if acct in SENSITIVITY_ONLY_ACCOUNTS
                                 else "PRIMARY"),
                "PRICE_BAND": band,
                "OPENS": opens if opens is not None else NOT_IDENTIFIED,
                # Correction 3. A position count bounds the independent count
                # from above; it does not measure it, and it is not named
                # EFFECTIVE_N anywhere.
                "POSITION_LEVEL_N": opens if opens is not None
                else NOT_IDENTIFIED,
                "INDEPENDENT_EFFECTIVE_N": NOT_IDENTIFIED,
                "INDEPENDENT_N_UPPER_BOUND": opens if opens is not None
                else NOT_IDENTIFIED,
                "INTERVAL_WIDTH_STATUS":
                    "LOWER_BOUND_ON_TRUE_CLUSTER_ROBUST_WIDTH",
                # Correction 2. The sample is whale-chosen entries.
                "SELECTION_CONDITION": SELECTION_CONDITION,
                # COMPLETION economics and RESIDUAL economics, never summed
                # into one headline. Ferrari exists to stop that.
                "MERGE_CHANNEL_PNL": merge,
                "SETTLED_CHANNEL_PNL": settled,
                "SELL_CHANNEL_PNL": c.get("SELL_CHANNEL_PNL"),
                "TOTAL_CHANNEL_PNL": c.get("TOTAL_CHANNEL_PNL"),
                "MERGE_SIGN": c.get("MERGE_SIGN"),
                "SETTLED_SIGN": c.get("SETTLED_SIGN"),
                "RESIDUAL_RATE": c.get("RESIDUAL_RATE"),
                "MEAN_PAIR_BASIS": c.get("MEAN_PAIR_BASIS"),
                # Correction 8. Above par is a GROSS fact about prices paid.
                # Incentives are NOT_SEPARATELY_RETAINED, so the net outcome
                # is not established by this field.
                "GROSS_PAIR_BASIS_ABOVE_PAR": (
                    "YES" if isinstance(c.get("MEAN_PAIR_BASIS"), (int, float))
                    and c["MEAN_PAIR_BASIS"] > 1.0 else "NO"),
                "PAIR_BASIS_ABOVE_PAR_ESTABLISHES":
                    "GROSS_STRUCTURAL_FACT_BEFORE_INCENTIVES",
                "PAIR_BASIS_ABOVE_PAR_DOES_NOT_ESTABLISH":
                    "ESTABLISHED_FINAL_NET_LOSS",
                "NET_OF_INCENTIVES_PAIR_OUTCOME": NOT_IDENTIFIED,
                "SUPPORT": c.get("SUPPORT", NOT_IDENTIFIED),
                "EVIDENCE_LEVEL": c.get("EVIDENCE_LEVEL", NOT_IDENTIFIED),
                "MERGE_COUNT": c.get("MERGE_COUNT"),
                "SETTLED_LOTS": c.get("SETTLED_LOTS"),
            })
    return out


def band_consensus(cells):
    """Where do the accounts AGREE on a sign? Sign agreement is the finding."""
    out = []
    for band in BANDS:
        rows = [c for c in cells if c["PRICE_BAND"] == band]
        if not rows:
            continue
        merge_pos = sum(1 for r in rows if (r["MERGE_CHANNEL_PNL"] or 0) > 0)
        basis_above = sum(1 for r in rows
                          if r["GROSS_PAIR_BASIS_ABOVE_PAR"] == "YES")
        bases = [r["MEAN_PAIR_BASIS"] for r in rows
                 if isinstance(r["MEAN_PAIR_BASIS"], (int, float))]
        resid = [r["RESIDUAL_RATE"] for r in rows
                 if isinstance(r["RESIDUAL_RATE"], (int, float))]
        out.append({
            "PRICE_BAND": band,
            "ACCOUNTS": len(rows),
            "MERGE_POSITIVE_ACCOUNTS": merge_pos,
            "MERGE_SIGN_CONSENSUS": ("POSITIVE" if merge_pos == len(rows)
                                     else "NEGATIVE" if merge_pos == 0
                                     else "DISAGREE"),
            "ACCOUNTS_WITH_PAIR_BASIS_ABOVE_PAR": basis_above,
            "MEAN_PAIR_BASIS_MIN": min(bases) if bases else NOT_IDENTIFIED,
            "MEAN_PAIR_BASIS_MAX": max(bases) if bases else NOT_IDENTIFIED,
            "RESIDUAL_RATE_MIN": min(resid) if resid else NOT_IDENTIFIED,
            "RESIDUAL_RATE_MAX": max(resid) if resid else NOT_IDENTIFIED,
            # Consensus on a SIGN is not consensus on a MAGNITUDE, and only the
            # sign is claimed here.
            "CONSENSUS_IS_ON": "SIGN_ONLY_NOT_MAGNITUDE",
        })
    return out


def hazard_cells(src, ceiling="ceiling_1.00", accounts=None):
    """ACCOUNT x TIME_UNPAIRED_INTERVAL, with the CI the artefact carries.

    This is a COMPLETION hazard: the probability the whale's other leg was
    acquired. It is NOT a BETTOR fill probability and may not seed one --
    `whale_bridge.assert_p_fill_source` refuses that by name.
    """
    keep = set(accounts or PRIMARY_WHALE_PRIOR_ACCOUNTS)
    out = []
    for acct, r in sorted(src["ACCOUNT_PRIORS"].items()):
        if acct not in keep:
            continue
        h = (r.get("HAZARD_BY_BASIS_CEILING") or {}).get(ceiling) or {}
        for row in h.get("ROWS", []):
            out.append({
                "ACCOUNT": acct,
                "QUANTITY": "WHALE_COMPLETION_HAZARD",
                "IS_NOT": "BETTOR_P_FILL",
                "MAY_SEED_BETTOR_P_FILL": False,
                "SELECTION_CONDITION": SELECTION_CONDITION,
                "BASIS_CEILING": ceiling,
                "INTERVAL": row.get("INTERVAL"),
                "CONTINUOUS_HAZARD_LAMBDA": row.get("CONTINUOUS_HAZARD_LAMBDA"),
                "HAZARD_LAMBDA_SE": row.get("HAZARD_LAMBDA_SE"),
                "HAZARD_LAMBDA_CI95": row.get("HAZARD_LAMBDA_CI95"),
                "N_AT_RISK_AT_START": row.get("N_AT_RISK_AT_START"),
                "N_COMPLETED_IN_INTERVAL": row.get("N_COMPLETED_IN_INTERVAL"),
                "CUMULATIVE_COMPLETION_F": row.get("CUMULATIVE_COMPLETION_F"),
                "RISK_SET_CONVENTION": row.get("RISK_SET_CONVENTION"),
                "CAUSE_SPECIFIC_HAZARD": row.get("CAUSE_SPECIFIC_HAZARD",
                                                 "NOT_COMPUTED"),
            })
    return out


def account_scope(src):
    out = []
    for acct, r in sorted(src["ACCOUNT_PRIORS"].items()):
        fills = r.get("ROWS_KEPT")
        pos = r.get("FIRST_SIDE_ACQUISITIONS")
        out.append({
            "ACCOUNT": acct,
            "ACCOUNT_ROLE": ("SENSITIVITY_ONLY"
                             if acct in SENSITIVITY_ONLY_ACCOUNTS
                             else "PRIMARY"),
            "RAW_SOURCE": src.get("SOURCE", NOT_IDENTIFIED),
            "SOURCE_RUN": src.get("SOURCE_RUN", NOT_IDENTIFIED),
            "DATE_RANGE_START": r.get("RETURNED_RANGE_START"),
            "DATE_RANGE_END": r.get("RETURNED_RANGE_END"),
            "N_FILLS": fills,
            "N_POSITIONS": pos,
            "FILLS_PER_POSITION": (round(fills / pos, 2)
                                   if fills and pos else NOT_IDENTIFIED),
            # The levels the artefacts simply do not carry.
            "N_MARKETS": NOT_IDENTIFIED,
            "N_EVENTS": NOT_IDENTIFIED,
            "SPORTS": NOT_IDENTIFIED,
            "LEAGUES": NOT_IDENTIFIED,
            "MARKET_TYPES": NOT_IDENTIFIED,
            "EVIDENCE_LEVEL": r.get("EVIDENCE_LEVEL", NOT_IDENTIFIED),
            "EXCLUSION": r.get("EXCLUSION"),
            "CLUSTER_LEVEL_AVAILABLE": "POSITION",
            "POSITION_LEVEL_N": pos if pos else NOT_IDENTIFIED,
            "INDEPENDENT_EFFECTIVE_N": NOT_IDENTIFIED,
            "INDEPENDENT_N_UPPER_BOUND": pos if pos else NOT_IDENTIFIED,
            "SELECTION_CONDITION": SELECTION_CONDITION,
        })
    return out


FIELD_AVAILABILITY = {
    "FILL_TIMESTAMP": "PRESENT_IN_UPSTREAM_RECONSTRUCTION",
    "FILL_PRICE": "PRESENT_IN_UPSTREAM_RECONSTRUCTION",
    "SIZE": "PRESENT_IN_UPSTREAM_RECONSTRUCTION",
    "ECONOMIC_SIDE": "PRESENT_IN_UPSTREAM_RECONSTRUCTION",
    "MARKET_IDENTITY": "NOT_PRESENT_IN_RETAINED_PRIORS",
    "EVENT_IDENTITY": "NOT_PRESENT_IN_RETAINED_PRIORS",
    "SPORT": "NOT_PRESENT_IN_RETAINED_PRIORS",
    "LEAGUE": "NOT_PRESENT_IN_RETAINED_PRIORS",
    "MARKET_TYPE": "NOT_PRESENT_IN_RETAINED_PRIORS",
    "GAME_START": "NOT_PRESENT_IN_RETAINED_PRIORS",
    "PREGAME_LIVE": "NOT_PRESENT_IN_RETAINED_PRIORS",
    "SETTLEMENT_RESULT": "AGGREGATED_INTO_SETTLED_CHANNEL",
    "MERGE_COMPLETION": "AGGREGATED_INTO_MERGE_CHANNEL",
    "REDEMPTION": "AGGREGATED",
    "FEE_REBATE": "NOT_SEPARATELY_RETAINED",
    "ORDER_PLACEMENT": "NOT_PRESENT",
    "CANCELLED_ORDERS": "NOT_PRESENT",
    "QUOTE_PRICE": "NOT_PRESENT",
    "QUEUE_STATE": "NOT_PRESENT",
    "BOOK_STATE": "NOT_PRESENT",
}


def build(src=None):
    src = src or load()
    cells = channel_cells(src)
    ghost = channel_cells(src, accounts=SENSITIVITY_ONLY_ACCOUNTS)
    four = cells + ghost
    ex = (src.get("EXCLUSION_PROVENANCE") or {}).get("swisstony") or {}
    return {
        "VERSION": "WHALE_REFERENCE_PRIORS_V1",
        "DERIVED_FROM": str(SOURCE.name),
        "SOURCE_VERSION": src.get("VERSION"),
        "SOURCE_RUN": src.get("SOURCE_RUN"),
        "SOURCE_GRANULARITY": src.get("GRANULARITY"),
        "NOTHING_IN_THIS_FILE_IS_AUTHORED": True,
        "VENUE_CONTACT": 0, "ORDERS": 0, "CAPITAL": 0, "mirror_live": False,

        "PRIORS_ARE_INITIAL_ONLY": src.get("PRIORS_ARE_INITIAL_ONLY"),
        "SUPERSEDED_BY": src.get("SUPERSEDED_BY"),

        # ---- CORRECTION 1: the canonical prior and the ghost ------------
        "PRIMARY_WHALE_PRIOR_ACCOUNTS": list(PRIMARY_WHALE_PRIOR_ACCOUNTS),
        "WHALE_PRIOR_3_ACCOUNT": "CANONICAL",
        "WHALE_PRIOR_4_ACCOUNT_SENSITIVITY":
            "SENSITIVITY_ONLY_NEVER_CANONICAL",
        "SWISSTONY_STATUS": "GHOST_PRIOR_SENSITIVITY_ONLY",
        "SWISSTONY_PRIMARY_PRIOR_ELIGIBILITY": NOT_ESTABLISHED,
        "SWISSTONY_EXCLUSION_PROVENANCE": ex,
        "SWISSTONY_DISPUTED_FIELD_RECONSTRUCTED":
            "MERGE_PAIR_CHANNEL_PNL_SIGN_ACCOUNT_LIFETIME",
        "SWISSTONY_DISPUTED_FIELD_SOURCE":
            "BETA48_DATA_GATE.md BIGGEST_DISCREPANCY item 1 -- external "
            "report -$3,390,000 (-0.7%) against this reconstruction's "
            "+$260,123 (+0.104%): a SIGN FLIP on the pair channel, which is "
            "the quantity the merge-sign consensus is built on",
        "SWISSTONY_DISPUTE_RESOLVED": NOT_ESTABLISHED,
        "RECONCILIATION_RESIDUAL_ZERO_DOES_NOT_CLEAR_THE_EXCLUSION": True,

        # ---- CORRECTION 2: the sample is whale-chosen entries ------------
        "SELECTION_CONDITION": SELECTION_CONDITION,
        "SELECTION_CONDITION_MEANS": (
            "the sample is positions the whales chose to open; declined "
            "markets, refused prices and unentered opportunities are absent, "
            "so no cell describes an arbitrary entry at that price"),
        "COUNTERFACTUAL_ENTRY_OUTCOME": NOT_IDENTIFIED,
        "WHALE_EVIDENCE_ALONE_CAN_CREATE_A_TRADE": False,

        # ---- CORRECTION 3: what the counts are, and are not --------------
        "INDEPENDENT_EFFECTIVE_N": NOT_IDENTIFIED,
        "POSITION_LEVEL_INTERVAL_WIDTH":
            "LOWER_BOUND_ON_TRUE_CLUSTER_ROBUST_INTERVAL_WIDTH",
        "POSITION_LEVEL_WEIGHTING_STATUS": "HEURISTIC",
        "POSITION_LEVEL_WEIGHTING_IS_ANTI_CONFIDENCE_CAPPED": True,

        # ---- CORRECTION 4: the joint is not built ------------------------
        "PRICE_TIME_JOINT_PRIOR": NOT_IDENTIFIED,
        "JOINT_PRIOR_CONSTRUCTION_FORBIDDEN": [
            "MULTIPLICATION_OF_MARGINALS", "INTERPOLATION_ACROSS_BANDS",
            "CROSS_PRODUCT_TABLE_CONSTRUCTION", "ADDITIVE_DECOMPOSITION"],

        # ---- CORRECTION 5: completion is not execution -------------------
        "WHALE_COMPLETION_AS_P_FILL": "FORBIDDEN",
        "BETTOR_P_FILL_SOURCE": "BETTOR_NATIVE_ADMITTED_FILLS_REQUIRED",

        # ---- CORRECTION 8: above par is gross ----------------------------
        "PAIR_BASIS_ABOVE_PAR_IS": "GROSS_STRUCTURAL_FACT_BEFORE_INCENTIVES",
        "PAIR_BASIS_ABOVE_PAR_IS_NOT": "ESTABLISHED_FINAL_NET_LOSS",
        "NET_OF_INCENTIVES_PAIR_OUTCOME": NOT_IDENTIFIED,
        "INCENTIVES_IN_WHALE_ARTEFACTS": "NOT_SEPARATELY_RETAINED",

        # What the source itself refuses to claim, carried forward verbatim so
        # a consumer cannot lose it.
        "SOURCE_REFUSALS": {
            "PER_POSITION_ROWS": src.get("PER_POSITION_ROWS"),
            "HISTORICAL_BOOK_STATE": src.get("HISTORICAL_BOOK_STATE"),
            "EV_EXIT_HISTORICAL": src.get("EV_EXIT_HISTORICAL"),
            "WHALE_SELL_POLICY_GENERALIZABLE":
                src.get("WHALE_SELL_POLICY_GENERALIZABLE"),
            "TRUE_CAUSE_SPECIFIC_COMPLETION_HAZARD":
                src.get("TRUE_CAUSE_SPECIFIC_COMPLETION_HAZARD"),
            "COMPETING_RISK_MODEL": src.get("COMPETING_RISK_MODEL"),
            "JOINT_TIME_BASIS_GRANULARITY":
                src.get("JOINT_TIME_BASIS_GRANULARITY"),
            "BASIS_CEILING_LAMBDA_IS": src.get("BASIS_CEILING_LAMBDA_IS"),
        },

        "ACCOUNT_SCOPE": account_scope(src),
        "FIELD_AVAILABILITY": FIELD_AVAILABILITY,

        "CELL_SPACE_AVAILABLE": ["ACCOUNT x PRICE_BAND",
                                 "ACCOUNT x TIME_UNPAIRED_INTERVAL"],
        "CELL_SPACE_AVAILABLE_ARE_MARGINALS_NOT_A_CROSS_PRODUCT": True,
        "BLOBS_V3_RICH_CELL_STATUS": "NOT_AVAILABLE_AGGREGATE_ONLY",
        "BLOBS_V3_PROBE": "BLOBS_V3_PROBE_V1.json",
        "BLOBS_V3_ADDS_ONLY": ["ACCOUNT x FILL_SIZE_BUCKET",
                               "ACCOUNT x ISO_WEEK"],
        "CELL_SPACE_NOT_AVAILABLE": [
            "SPORT", "LEAGUE", "MARKET_TYPE", "PREGAME_LIVE",
            "TIME_TO_EVENT", "SIZE_BAND", "MARKET_AGE", "TIME_OF_DAY",
            "SEQUENCE_POSITION", "THE CROSS PRODUCT OF BAND x INTERVAL",
        ],
        "WHY_NOT_AVAILABLE": (
            "the retained priors are AGGREGATE; per-position rows and market "
            "or event identity are NOT_PRESENT, so a richer cell cannot be "
            "computed and is not interpolated"),

        "CHANNEL_BY_ACCOUNT_AND_BAND": cells,
        "BAND_CONSENSUS": band_consensus(cells),
        "COMPLETION_HAZARD_BY_ACCOUNT_AND_INTERVAL": hazard_cells(src),

        # The 4-account run is kept, labelled, and never promoted. It exists
        # so a reader can see what swisstony would have done to the consensus
        # without the consensus depending on it.
        "SENSITIVITY_RUN": {
            "PRIOR": "WHALE_PRIOR_4_ACCOUNT_SENSITIVITY",
            "STATUS": "SENSITIVITY_ONLY_NEVER_CANONICAL",
            "ACCOUNTS": list(PRIMARY_WHALE_PRIOR_ACCOUNTS)
            + list(SENSITIVITY_ONLY_ACCOUNTS),
            "CHANNEL_BY_ACCOUNT_AND_BAND": ghost,
            "BAND_CONSENSUS": band_consensus(four),
            "COMPLETION_HAZARD_BY_ACCOUNT_AND_INTERVAL": hazard_cells(
                src, accounts=SENSITIVITY_ONLY_ACCOUNTS),
        },

        "MECHANISM_READING": (
            "the price band is read as the price of the FIRST leg acquired: "
            "acquiring the cheap leg first completes at a good basis, "
            "acquiring the expensive leg first completes at or above par"),
        "MECHANISM_READING_STATUS": "HYPOTHESIS_CONSISTENT_WITH_THE_SIGNS",
        "MECHANISM_READING_IS_NOT": "A_DEMONSTRATED_CAUSAL_CLAIM",

        "COMPLETION_AND_RESIDUAL_ARE_NEVER_SUMMED": True,
        "WHY": ("ferrari's 0.10-0.30 band is MERGE +3,844,029 against SETTLED "
                "-4,435,219 for a TOTAL of -560,629: the completion mechanism "
                "worked and the residual inventory ate it. One headline "
                "number would have hidden that"),
    }


def main():                                                   # pragma: no cover
    doc = build()
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n")
    cons = doc["BAND_CONSENSUS"]
    print("wrote %s" % OUT.name)
    print("%-12s %-10s %-8s %-10s" % ("BAND", "MERGE", "ABOVE_PAR", "RESID"))
    for c in cons:
        print("%-12s %-10s %-8s %.3f-%.3f"
              % (c["PRICE_BAND"], c["MERGE_SIGN_CONSENSUS"],
                 c["ACCOUNTS_WITH_PAIR_BASIS_ABOVE_PAR"],
                 c["RESIDUAL_RATE_MIN"], c["RESIDUAL_RATE_MAX"]))
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())
