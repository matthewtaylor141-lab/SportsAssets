"""MERGE_ROI vs TOTAL_ROI per band — the survivorship check, per account.

THE QUESTION THIS ANSWERS, AND WHY IT IS THE DECIDING ONE.

The preregistered discriminator established, on verified evidence at one
common as-of, that the open-price band orders pair profitability inside
every one of six accounts (Spearman -0.94 to -1.00, low-positive /
high-negative, one sign crossing each), and that the two bands below
0.30 are positive in ALL SIX. That is a real and replicated fact about
the MERGE channel.

It is not yet a fact about money, and the same table says why. The
residual rate — the share of opens that never pair, even by settlement —
is 28% to 81% in the 0.00-0.10 band, the highest of any band in five of
the six accounts. Pairing succeeds least often exactly where the merge
edge looks best. A cheap leg that pairs at basis 0.94 books a certain
profit; the cheap leg that never pairs is a naked longshot that usually
settles at zero, and its loss lands in a channel the merge split cannot
see. So the merge-only view is biased in favour of the low bands by
construction, and the unanimous sub-0.30 result could be entirely that
bias.

This module puts the two side by side. `MERGE_ROI` is the pair channel.
`TOTAL_ROI` is every closed lot opened in that band — merge, sell and
settled — over the stake that opened there. Where they disagree in sign,
the merge reading was survivorship.

DECISION RULE, fixed here before the numbers are read, because the whole
point is to not choose a threshold after seeing the data:

  * a band is STRUCTURALLY_PROFITABLE only if TOTAL_ROI > 0 in EVERY
    account that reconciles — the same unanimity bar the merge result
    was held to. Not "most accounts", not "on average", not "the ones
    we like".
  * if no band clears that bar, the answer is that no band clears it.
    Engine A's band candidate is then dead and must NOT be revived by
    searching sub-bands, horizons or account subsets until something
    turns positive.

Read-only. No order, no capital, no production write.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "evidence" / "blobs_v3"

BANDS = ["0.00-0.10", "0.10-0.30", "0.30-0.50",
         "0.50-0.70", "0.70-0.90", "0.90-1.01"]
SECTION = "PNL_BY_OPEN_BAND_ALL_CHANNELS"


def main() -> int:
    recs = {}
    for p in sorted(EVIDENCE.glob("*_reconstruction.json")):
        w = p.name.replace("_reconstruction.json", "")
        d = json.loads(p.read_text())
        s = d.get(SECTION, {}).get("LIFETIME")
        if not isinstance(s, dict):
            print(f"{w}: {SECTION} absent — re-emit has not landed")
            continue
        recs[w] = s

    if not recs:
        print("NO THREE-CHANNEL EVIDENCE YET")
        return 1

    print("=" * 100)
    print("RECONCILIATION against the estimator's own closed-lot totals")
    print("=" * 100)
    clean = []
    for w, s in recs.items():
        ok = s.get("_RECONCILES")
        print(f"  {w:<22} {'RECONCILES' if ok else 'DOES NOT RECONCILE':<20}"
              f" stake residual ${s.get('_RESIDUAL_STAKE')}"
              f"  pnl residual ${s.get('_RESIDUAL_PNL')}")
        if ok:
            clean.append(w)
    print(f"\nclean: {len(clean)} of {len(recs)} — {', '.join(clean)}")
    if not clean:
        return 1

    print()
    print("=" * 100)
    print("MERGE_ROI (pair channel only)  vs  TOTAL_ROI (every lot opened "
          "in the band)")
    print("=" * 100)
    hdr = (f"{'account':<22}{'band':<12}{'MERGE_ROI':>11}{'TOTAL_ROI':>11}"
           f"{'MERGE_PNL':>15}{'SETTLED_PNL':>15}{'TOTAL_PNL':>15}"
           f"{'SIGN FLIP':>11}")
    print(hdr)
    print("-" * len(hdr))
    flips = 0
    for w in clean:
        s = recs[w]
        for b in BANDS:
            e = s.get(b)
            if not e:
                continue
            m, t = e.get("MERGE_ROI"), e.get("TOTAL_ROI")
            flip = (isinstance(m, float) and isinstance(t, float)
                    and (m > 0) != (t > 0))
            flips += bool(flip)
            print(f"{w:<22}{b:<12}"
                  f"{(f'{m*100:.2f}%' if isinstance(m, float) else 'n/a'):>11}"
                  f"{(f'{t*100:.2f}%' if isinstance(t, float) else 'n/a'):>11}"
                  f"{e.get('MERGE_PNL', 0):>15,.0f}"
                  f"{e.get('SETTLED_PNL', 0):>15,.0f}"
                  f"{e.get('TOTAL_PNL', 0):>15,.0f}"
                  f"{('YES' if flip else ''):>11}")
        print("-" * len(hdr))

    print(f"\nbands whose sign FLIPS between merge-only and total: {flips}")

    # band_of() returns NOT_IDENTIFIED for a price outside every band.
    # Those lots are inside the reconciliation but outside the table, so
    # their size has to be visible or the table silently understates the
    # book it claims to describe.
    print("\nOUT-OF-BAND lots (price outside every band; inside the "
          "reconciliation, outside the table above):")
    for w in clean:
        e = recs[w].get("NOT_IDENTIFIED")
        if not e:
            print(f"  {w:<22} none")
            continue
        tot = recs[w].get("_TOTAL_STAKE") or 0.0
        share = (e.get("TOTAL_STAKE", 0.0) / tot * 100) if tot else 0.0
        print(f"  {w:<22} stake ${e.get('TOTAL_STAKE', 0):,.0f} "
              f"({share:.2f}% of lot stake)  pnl ${e.get('TOTAL_PNL', 0):,.0f}")

    print()
    print("=" * 100)
    print("THE PREREGISTERED BAR: TOTAL_ROI > 0 in EVERY reconciling account")
    print("=" * 100)
    survivors = []
    for b in BANDS:
        vals = {w: recs[w].get(b, {}).get("TOTAL_ROI") for w in clean}
        have = {w: v for w, v in vals.items() if isinstance(v, float)}
        pos = sum(1 for v in have.values() if v > 0)
        unanimous = bool(have) and pos == len(have)
        if unanimous:
            survivors.append(b)
        worst = min(have.values()) if have else None
        print(f"  {b:<12} {pos}/{len(have)} positive"
              f"   worst {('%.2f%%' % (worst*100)) if worst is not None else 'n/a'}"
              f"{'   <-- SURVIVES' if unanimous else ''}")

    print()
    if survivors:
        print(f"STRUCTURALLY_PROFITABLE bands (unanimous on TOTAL): "
              f"{', '.join(survivors)}")
    else:
        print("STRUCTURALLY_PROFITABLE bands: NONE.")
        print("No band is positive on total economics in every reconciling")
        print("account. Per the rule fixed before these numbers were read,")
        print("the band candidate for Engine A is DEAD and must not be")
        print("revived by searching sub-bands, horizons or account subsets.")

    out = HERE / "evidence" / "band_total_economics.json"
    out.write_text(json.dumps(
        {"CLEAN_ACCOUNTS": clean,
         "SIGN_FLIPS_MERGE_VS_TOTAL": flips,
         "STRUCTURALLY_PROFITABLE_BANDS": survivors,
         "RULE": "TOTAL_ROI > 0 in EVERY reconciling account; fixed "
                 "before the numbers were read",
         "PER_ACCOUNT": {w: {b: recs[w].get(b) for b in BANDS}
                         for w in clean}},
        indent=1))
    print(f"\nfrozen to {out.relative_to(HERE.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
