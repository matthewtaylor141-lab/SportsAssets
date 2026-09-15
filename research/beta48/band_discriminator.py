#!/usr/bin/env python3
"""BETA48 — does the pair channel's SIGN track a decision-time fact?

READ ONLY, OFFLINE. Reads the sealed reconstructions and prints one
table. Contacts nothing. Changes no estimator. Promotes nothing.

THE QUESTION. channel_decomposition.py showed the pair/merge channel
splits 3 positive / 3 negative across the six reference accounts, and
BETA48_DATA_GATE recorded that a 3/3 split cannot distinguish
STRUCTURAL_DISCRIMINATOR from ACCOUNT_SELECTION_PROXY, MARKET_MIX_PROXY
or REGIME_PROXY. This file asks the cheapest version of the structural
question, using only what is already sealed: does the sign track the
COMPLETED PAIR BASIS?

WHY THE BASIS IS THE RIGHT THING TO LOOK AT. A complementary pair pays
exactly $1. If the two legs together cost MORE than $1, the pair is a
loss the moment it is completed, whatever the outcome. So a basis above
1.00 is not a bad result -- it is a bad decision, visible at the instant
of completion, and it is exactly what an Engine A/C copier would be
copying.

THE ANSWER IS NO, AND THAT IS THE RESULT. The basis does NOT separate
the two classes:

    pair-POSITIVE  bands above parity:  ferrari 2, rn1 0, swisstony 1
    pair-NEGATIVE  bands above parity:  HRH 2, kch123 3, w2c33 3

ferrariChampions2026 is pair-POSITIVE with two bands above parity --
the same count as homerunhazard, which is pair-NEGATIVE. The
open-weighted basis overlaps too: swisstony (+) sits at 0.9901, WORSE
than homerunhazard (-) at 0.9829. Any threshold that puts ferrari on
the positive side puts HRH there as well.

I record this because I first read the table by eye and called it a
clean split -- "every positive account stays below 1.02, every negative
one goes above 1.00". Both halves of that are false on the numbers, and
the check in main() is what caught it. The separation test is computed,
not asserted, precisely so a hopeful reading cannot survive.

THE CONFOUND, which would have mattered had the split held.
mean_pair_basis is computed over COMPLETED pairs only, and the
never-completed share ranges from 23.5% to 69.3% across these accounts.
The basis is therefore conditioned on a subset each account selects
differently: one that abandons its worst first legs looks better here
than one that completes them. w2c33 abandons 69.3% of its opens.

So: PAIR_BASIS_DISCRIMINATOR = NOT_SUPPORTED. The completed pair basis
is not the structural fact that explains the 3/3 sign split, and it must
not be used as one in either direction. The question BETA48_DATA_GATE
named stands unanswered and still needs per-band MATCHED_PNL, which is
not in the sealed grids and needs one more read-only extraction.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLOBS = HERE / "evidence" / "blobs"
OUT = HERE / "evidence"

BANDS = ("0.00-0.10", "0.10-0.30", "0.30-0.50",
         "0.50-0.70", "0.70-0.90", "0.90-1.01")


def decompose_merge(r):
    return r.get("realized_merge_pnl") or 0.0


def main() -> int:
    rows, out = [], {}
    for p in sorted(BLOBS.glob("*_reconstruction.json")):
        name = p.name.split("_recon")[0]
        d = json.loads(p.read_text())
        life = d["REFERENCE_ACCOUNT_REPLAY"]["LIFETIME"]
        comp = d["REFERENCE_ACCOUNT_COMPLETION"]["LIFETIME"]
        bb = comp["BY_FIRST_LEG_PRICE_BAND"]
        merge = decompose_merge(life)

        present = [b for b in BANDS if bb.get(b)
                   and bb[b].get("mean_pair_basis") is not None]
        opens = sum(bb[b]["opens"] for b in present)
        wtd = (sum(bb[b]["mean_pair_basis"] * bb[b]["opens"] for b in present)
               / opens) if opens else None
        worst = max(bb[b]["mean_pair_basis"] for b in present)
        over = [b for b in present if bb[b]["mean_pair_basis"] > 1.0]
        never = (comp["REFERENCE_ACCOUNT_NEVER_COMPLETED"]
                 / comp["REFERENCE_ACCOUNT_FIRST_SIDE_ACQUISITIONS"])
        rows.append({"account": name, "merge_pnl": round(merge, 2),
                     "merge_sign": "+" if merge > 0 else "-",
                     "worst_band_basis": round(worst, 5),
                     "open_weighted_basis": round(wtd, 5) if wtd else None,
                     "bands_above_parity": len(over),
                     "bands_above_parity_names": over,
                     "never_completed_share": round(never, 4),
                     "by_band": {b: bb[b] for b in present}})
        out[name] = rows[-1]

    rows.sort(key=lambda r: -r["merge_pnl"])
    print("=" * 96)
    print("BETA48 - pair-channel sign vs completed pair basis: NOT_SUPPORTED")
    print("=" * 96)
    print("%-22s %16s %4s %11s %11s %8s %9s"
          % ("account", "MERGE_PNL", "sgn", "WORST_BAND",
             "OPEN_WTD", "BANDS>1", "NEVER%"))
    print("-" * 96)
    for r in rows:
        print("%-22s %16s %4s %11.4f %11.4f %8d %8.1f%%"
              % (r["account"], f'{r["merge_pnl"]:,.0f}', r["merge_sign"],
                 r["worst_band_basis"], r["open_weighted_basis"],
                 r["bands_above_parity"], 100 * r["never_completed_share"]))

    pos = [r for r in rows if r["merge_pnl"] > 0]
    neg = [r for r in rows if r["merge_pnl"] <= 0]
    sep = (max(r["bands_above_parity"] for r in pos)
           < min(r["bands_above_parity"] for r in neg))
    print()
    print("pair-POSITIVE bands-above-parity: %s"
          % [r["bands_above_parity"] for r in pos])
    print("pair-NEGATIVE bands-above-parity: %s"
          % [r["bands_above_parity"] for r in neg])
    print("clean separation on bands-above-parity: %s" % sep)
    print()
    print("READ THIS BEFORE USING IT. mean_pair_basis is measured on COMPLETED")
    print("pairs only and the never-completed share runs %.1f%%..%.1f%% across"
          % (100 * min(r["never_completed_share"] for r in rows),
             100 * max(r["never_completed_share"] for r in rows)))
    print("these accounts, so the basis is conditioned on a subset each")
    print("account selects differently.")
    print()
    print("RESULT: PAIR_BASIS_DISCRIMINATOR = NOT_SUPPORTED.")
    print("The classes overlap on both statistics, so the completed pair basis")
    print("does not explain the 3/3 sign split and must not be used as though")
    print("it did. The 3/3 split stays unexplained and still needs per-band")
    print("MATCHED_PNL, which is not in the sealed grids.")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "band_discriminator.json").write_text(
        json.dumps({"rows": out,
                    "PAIR_BASIS_DISCRIMINATOR": "NOT_SUPPORTED",
                    "clean_separation_bands_above_parity": sep},
                   indent=1, default=str))
    print("\nsealed -> research/beta48/evidence/band_discriminator.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
