"""The FROZEN cross-account table: ACCOUNT x OPEN_PRICE_BUCKET.

Section A and B of the standing instruction. This module VERIFIES first
and tabulates second, and an account that fails verification is printed
in the verification block but **excluded from the table**, because it
cannot enter strategy selection as clean evidence.

TWO DENOMINATORS LIVE IN EVERY ROW AND THEY ARE NOT THE SAME NUMBER.
This is the single easiest way to misread this table, so it is enforced
in the column names rather than left to a footnote:

  * `OPENS` and every `COMPLETION_*` / `*_PAIR_BASIS` / `RESIDUAL_RATE`
    column comes from `completion_grid`, whose unit is a MARKET going
    from flat to held. Its question is "having opened, did the account
    ever get the other side?"
  * `MERGES`, `MATCHED_PNL` and `MATCHED_ROI` come from
    `merge_pnl_by_open_band`, whose unit is a LEG going flat while the
    other leg may still be held. Its question is "what did the pair
    channel actually earn, booked to the band the HELD leg opened in?"

`MERGES / OPENS` is therefore NOT a completion rate and must never be
computed. The completion rate is `COMPLETION_RATE_SETTLEMENT`.

Only `MERGE_PNL_BY_OPEN_BAND` reconciles to canonical
`realized_merge_pnl`. The `first_complement_*` fields in the completion
grid reproduce ~26% of it on RN1 and are not read here at all.

LIFETIME only. Regime windows are a function of when the run happened —
`LAST_7D` means "the seven days before this run" — so they are not a
fixed quantity and two runs' regime numbers are not a replication of
each other. Any regime claim must name its as-of; this table does not
make one.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
EVIDENCE = HERE / "evidence" / "blobs_v3"
CENT = 0.01

BANDS = ["0.00-0.10", "0.10-0.30", "0.30-0.50",
         "0.50-0.70", "0.70-0.90", "0.90-1.01"]

# TWO reconciliations, and they are not the same test.
#
#  * AUTHORITATIVE: the payload's own `_RESIDUAL_VS_ESTIMATOR`, computed
#    inside the run on UNROUNDED floats, against canonical
#    `realized_merge_pnl`. This is the identity the attribution has to
#    satisfy, and the standing requirement is that it be exact.
#  * INDEPENDENT: this module re-sums the PUBLISHED per-band
#    `MATCHED_PNL` values. Those are rounded to the cent before
#    publication, so six of them can differ from the unrounded total by
#    up to 6 x $0.005. The tolerance is therefore DERIVED from the
#    publication format, not chosen to make accounts pass:
#
#        REPUBLICATION_TOL = len(BANDS) * 0.005 = $0.03
#
# A first version of this file used a flat $0.01 on the re-sum alone and
# reported EXACT_BUCKET_PNL_ATTRIBUTION = NOT_IDENTIFIED for
# homerunhazard, kch123 and w2c33 on residuals of exactly one cent. That
# was the check being wrong, not the accounts: all four payloads carry
# `_RESIDUAL_VS_ESTIMATOR = 0.0`. Both tests must now pass, and both are
# reported, so a real attribution failure cannot hide behind the looser
# one.
REPUBLICATION_TOL = len(BANDS) * 0.005

# Frozen before the data, per the standing instruction: RN1 discovered
# the band relationship and therefore cannot also confirm it.
ROLE = {
    "rn1": "DISCOVERY",
    "ferrarichampions2026": "VALIDATION",
    "homerunhazard": "CONTROL",
    "kch123": "CONTROL",
    "w2c33": "CONTROL",
    "swisstony": "FLAGGED_EXCLUDED",
}


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def verify(wallet: str, p: dict) -> dict:
    """The seven fields that decide whether an account may be used."""
    lt = p["MERGE_PNL_BY_OPEN_BAND"]["LIFETIME"]
    census = p["CENSUS"]
    canonical = lt.get("_REALIZED_MERGE_PNL")
    # AUTHORITATIVE: computed in-run on unrounded floats.
    in_run = lt.get("_RESIDUAL_VS_ESTIMATOR")
    in_run_ok = in_run is not None and abs(in_run) < CENT
    # INDEPENDENT: re-sum of the published, already-rounded values.
    summed = round(sum(lt[b]["MATCHED_PNL"] for b in BANDS if b in lt), 2)
    resum = None if canonical is None else round(summed - canonical, 2)
    resum_ok = resum is not None and abs(resum) <= REPUBLICATION_TOL + 1e-9

    days = census.get("DAYS_SINCE_LAST_FILL_AT_AS_OF")
    # A NEGATIVE days-since-last-fill means the pull contains fills LATER
    # than the common as-of. That is not an error and it does not touch
    # LIFETIME, which ignores the anchor by construction — but it does
    # mean LIFETIME is "the whole pull", not "everything up to the
    # as-of", and it must be said rather than left for a reader to
    # assume.
    post = isinstance(days, (int, float)) and days < 0

    return {
        "WALLET": wallet,
        "ROLE": ROLE.get(wallet, "UNCLASSIFIED"),
        "EXACT_BUCKET_PNL_ATTRIBUTION": "IDENTIFIED"
                                        if (in_run_ok and resum_ok)
                                        else "NOT_IDENTIFIED",
        "RECONCILIATION_RESIDUAL": in_run,
        "RECONCILIATION_RESIDUAL_REPUBLISHED": resum,
        "REPUBLICATION_TOL": round(REPUBLICATION_TOL, 4),
        "CANONICAL_REALIZED_MERGE_PNL": canonical,
        "SUM_BUCKET_MATCHED_PNL": summed,
        "AS_OF_UTC": census.get("AS_OF_UTC"),
        "DAYS_SINCE_LAST_FILL": days,
        "FILLS_EXIST_AFTER_AS_OF": post,
        "DATA_COVERAGE_LIMITATIONS": census.get("COVERAGE_LIMITATIONS"),
        "ROWS_KEPT": census.get("ROWS_KEPT"),
        "RETURNED_RANGE_START": census.get("RETURNED_RANGE_START"),
        "RETURNED_RANGE_END": census.get("RETURNED_RANGE_END"),
    }


def rows(wallet: str, p: dict) -> list:
    """One row per (account, open-price bucket). LIFETIME only."""
    comp = p["REFERENCE_ACCOUNT_COMPLETION"]["LIFETIME"]["BY_FIRST_LEG_PRICE_BAND"]
    merge = p["MERGE_PNL_BY_OPEN_BAND"]["LIFETIME"]
    out = []
    for b in BANDS:
        c = comp.get(b) or {}
        m = merge.get(b) or {}
        out.append({
            "ACCOUNT": wallet,
            "ROLE": ROLE.get(wallet, "UNCLASSIFIED"),
            "OPEN_PRICE_BUCKET": b,
            # completion_grid unit: a MARKET going flat -> held
            "OPENS": c.get("opens"),
            "COMPLETION_5S": c.get("completion_rate_5s"),
            "COMPLETION_1H": c.get("completion_rate_1h"),
            "COMPLETION_SETTLEMENT": c.get("completion_rate_settlement"),
            "RESIDUAL_RATE": c.get("residual_rate"),
            "MEAN_COMPLETED_PAIR_BASIS": c.get("mean_pair_basis"),
            "MEDIAN_COMPLETED_PAIR_BASIS": c.get("median_pair_basis"),
            # merge_pnl_by_open_band unit: a LEG going flat
            "MERGES": m.get("merges"),
            "MATCHED_STAKE": m.get("MATCHED_STAKE"),
            "EXACT_MATCHED_PNL": m.get("MATCHED_PNL"),
            "MATCHED_ROI": m.get("MATCHED_ROI"),
        })
    return out


def main() -> int:
    blobs = sorted(EVIDENCE.glob("*_reconstruction.json"))
    if not blobs:
        print("NO VERIFIED EVIDENCE in", EVIDENCE)
        return 1

    checks, table, clean = [], [], []
    for path in blobs:
        wallet = path.name.replace("_reconstruction.json", "")
        p = load(path)
        v = verify(wallet, p)
        checks.append(v)
        if v["EXACT_BUCKET_PNL_ATTRIBUTION"] == "IDENTIFIED":
            clean.append(wallet)
            table.extend(rows(wallet, p))

    print("=" * 78)
    print("A. VERIFY DATA FIRST — per account")
    print("=" * 78)
    for v in checks:
        print(f"\n{v['WALLET']}  [{v['ROLE']}]")
        for k in ("EXACT_BUCKET_PNL_ATTRIBUTION", "RECONCILIATION_RESIDUAL",
                  "RECONCILIATION_RESIDUAL_REPUBLISHED", "REPUBLICATION_TOL",
                  "CANONICAL_REALIZED_MERGE_PNL", "SUM_BUCKET_MATCHED_PNL",
                  "AS_OF_UTC", "DAYS_SINCE_LAST_FILL",
                  "FILLS_EXIST_AFTER_AS_OF"):
            print(f"  {k:<38} {v[k]}")
        print(f"  {'ROWS_KEPT':<38} {v['ROWS_KEPT']:,}")
        print(f"  {'RETURNED_RANGE':<38} "
              f"{v['RETURNED_RANGE_START']} .. {v['RETURNED_RANGE_END']}")
        print(f"  {'DATA_COVERAGE_LIMITATIONS':<38} "
              f"{len(v['DATA_COVERAGE_LIMITATIONS'] or [])} standing caveats "
              f"(identical across accounts; see census)")

    failed = [v["WALLET"] for v in checks
              if v["EXACT_BUCKET_PNL_ATTRIBUTION"] != "IDENTIFIED"]
    print(f"\nACCOUNTS RECONCILING EXACTLY: {len(clean)} — {', '.join(clean)}")
    if failed:
        print(f"EXCLUDED (cannot enter strategy selection as clean "
              f"evidence): {', '.join(failed)}")

    print()
    print("=" * 78)
    print("B. THE FROZEN CROSS-ACCOUNT TABLE — LIFETIME")
    print("=" * 78)
    print("OPENS/COMPLETION_*/RESIDUAL_RATE/*_PAIR_BASIS: unit = a MARKET")
    print("  going flat -> held (completion_grid).")
    print("MERGES/MATCHED_*: unit = a LEG going flat, booked to the HELD")
    print("  leg's OPENING band (merge_pnl_by_open_band).")
    print("MERGES / OPENS IS NOT A COMPLETION RATE. Do not compute it.")
    print()
    hdr = (f"{'ACCOUNT':<22}{'BUCKET':<12}{'OPENS':>7}{'C_5S':>8}"
           f"{'C_1H':>8}{'C_SETL':>8}{'RESID':>8}{'BASIS_MEAN':>11}"
           f"{'BASIS_MED':>10}{'MERGES':>9}{'MATCHED_PNL':>15}{'ROI':>10}")
    print(hdr)
    print("-" * len(hdr))
    last = None
    for r in table:
        if last and last != r["ACCOUNT"]:
            print("-" * len(hdr))
        last = r["ACCOUNT"]

        def f(x, spec):
            return format(x, spec) if isinstance(x, (int, float)) else "n/a"

        print(f"{r['ACCOUNT']:<22}{r['OPEN_PRICE_BUCKET']:<12}"
              f"{f(r['OPENS'], '>7,')}"
              f"{f(r['COMPLETION_5S'], '>8.3f')}"
              f"{f(r['COMPLETION_1H'], '>8.3f')}"
              f"{f(r['COMPLETION_SETTLEMENT'], '>8.3f')}"
              f"{f(r['RESIDUAL_RATE'], '>8.3f')}"
              f"{f(r['MEAN_COMPLETED_PAIR_BASIS'], '>11.4f')}"
              f"{f(r['MEDIAN_COMPLETED_PAIR_BASIS'], '>10.4f')}"
              f"{f(r['MERGES'], '>9,')}"
              f"{f(r['EXACT_MATCHED_PNL'], '>15,.2f')}"
              f"{f(r['MATCHED_ROI'], '>10.4f')}")

    out = HERE / "evidence" / "cross_account_table.json"
    out.write_text(json.dumps(
        {"VERIFICATION": checks, "TABLE": table,
         "CLEAN_ACCOUNTS": clean, "EXCLUDED_ACCOUNTS": failed,
         "BASIS": "LIFETIME",
         "ROLE_ASSIGNMENT_FROZEN_BEFORE_DATA": ROLE},
        indent=1))
    print(f"\nfrozen to {out.relative_to(HERE.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
