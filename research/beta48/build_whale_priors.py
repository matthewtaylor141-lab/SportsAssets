#!/usr/bin/env python3
"""Derive WHALE_REFERENCE_PRIORS_V1.json from the sealed whale artefacts.

Contacts nothing. Reads `evidence/whale_audit/whale_exit_priors_v1.json` and
emits the reference priors BETTOR's bridge consumes. Every number is COPIED or
ARITHMETICALLY DERIVED from that file -- nothing here is authored.

THE FOUR-ACCOUNT STRUCTURE THIS EXTRACTS, which is the empirical core of the
whole bridge:

  MERGE_CHANNEL_PNL is POSITIVE in every band below 0.50 and NEGATIVE in every
  band above 0.70, IN ALL FOUR ACCOUNTS. Sign agreement across four
  independently reconstructed accounts is a mechanism-level regularity in a way
  that any single account's magnitude is not.

  MEAN_PAIR_BASIS rises monotonically with the band in all four, and CROSSES
  1.00 in the top bands for ferrari (1.00555, 1.01177), swisstony (1.00932) and
  homerunhazard (1.00584, 1.01022). A completed pair at basis > 1.00 cost more
  than the $1 it redeems: a loss locked in at completion, before fees.
  RN1 never crosses -- its maximum is 0.99825.

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
BANDS = ("0.00-0.10", "0.10-0.30", "0.30-0.50", "0.50-0.70", "0.70-0.90",
         "0.90-1.01")


def load(path=None):
    return json.loads(Path(path or SOURCE).read_text())


def channel_cells(src):
    """ACCOUNT x PRICE_BAND -- the one cross-section the artefacts support."""
    out = []
    for acct, r in sorted(src["ACCOUNT_PRIORS"].items()):
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
                "PRICE_BAND": band,
                "OPENS": opens if opens is not None else NOT_IDENTIFIED,
                "EFFECTIVE_N_LEVEL": "POSITION",
                "EFFECTIVE_N": opens if opens is not None else NOT_IDENTIFIED,
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
                "PAIR_BASIS_ABOVE_PAR": (
                    "YES" if isinstance(c.get("MEAN_PAIR_BASIS"), (int, float))
                    and c["MEAN_PAIR_BASIS"] > 1.0 else "NO"),
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
        basis_above = sum(1 for r in rows if r["PAIR_BASIS_ABOVE_PAR"] == "YES")
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


def hazard_cells(src, ceiling="ceiling_1.00"):
    """ACCOUNT x TIME_UNPAIRED_INTERVAL, with the CI the artefact carries."""
    out = []
    for acct, r in sorted(src["ACCOUNT_PRIORS"].items()):
        h = (r.get("HAZARD_BY_BASIS_CEILING") or {}).get(ceiling) or {}
        for row in h.get("ROWS", []):
            out.append({
                "ACCOUNT": acct,
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
            "EFFECTIVE_N_LEVEL_AVAILABLE": "POSITION",
            "EFFECTIVE_N": pos if pos else NOT_IDENTIFIED,
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
