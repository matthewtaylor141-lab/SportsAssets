#!/usr/bin/env python3
"""BETA48 — is FIRST_LEG_PRICE a cross-account discriminator, or a mix effect?

READ ONLY, OFFLINE. Reads the sealed reconstructions. Contacts nothing.
Changes no estimator. Promotes nothing on its own.

THE QUESTION, sharpened. channel_decomposition.py found the pair channel
splits 3 positive / 3 negative ACROSS accounts. BETA48_DATA_GATE could
not say whether that is structural or a proxy. There is a specific
hypothesis that would explain it without any account-level story:

    if MATCHED_ROI is positive in low open-price bands and negative in
    high ones WITHIN EVERY ACCOUNT, then the account-level sign is just
    each account's MIX across bands, and the discriminator is the band.

That is a within-account test, and it is the one that matters, because a
BETTOR copying pair construction chooses the band, not the account.

WHAT COUNTS AS PASSING. Three things, all computed, none eyeballed:

  1. SIGN CONSISTENCY -- in every account, the lowest bands are positive
     and the highest are negative, with one crossing point.
  2. MONOTONICITY -- Spearman rank correlation between band index and
     MATCHED_ROI is strongly negative in every account.
  3. MIX SUFFICIENCY -- reweighting every account to a COMMON band mix
     moves the account-level sign toward agreement. If the 3/3 split is
     a mix effect, a common mix should largely remove it.

If 1 and 2 hold across accounts including the pair-negative controls,
FIRST_LEG_PRICE is a structural discriminator and the account split is a
mix artefact. If they hold only in the pair-positive accounts, it is an
account-selection proxy.

LEAKAGE. The bucket is the price at which the held leg was OPENED, known
at entry. No settlement, no complement behaviour, no later price and no
future whale behaviour enters it. MATCHED_ROI is the outcome being
predicted, not a feature.

TWO DEFINITIONS OF "OPEN" ARE IN PLAY and they are NOT the same; the
table reports both rather than blending them:
  * completion_grid OPENS -- the whole MARKET was flat (both legs)
  * merge_pnl_by_open_band -- that LEG was flat, the other may be held
The second is the correct unit for attributing merges. The first is the
correct denominator for completion and residual rates. A row therefore
carries OPENS from one and MERGES from the other, and they need not match.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
# INPUT DIRECTORY ONLY. The default below is unchanged from the version
# committed BEFORE the data existed. `BETA48_BLOBS` redirects which
# verified evidence set is read and touches nothing else: no statistic,
# no threshold, no criterion, no band, no account list. It exists
# because the preregistered default points at run 35028887477, which ran
# before merge_pnl_by_open_band existed and therefore carries no exact
# split to test — the test would report NOT_IDENTIFIED for every account
# on a missing field rather than on the evidence.
BLOBS = Path(os.environ.get("BETA48_BLOBS") or (HERE / "evidence" / "blobs"))
OUT = HERE / "evidence"

BANDS = ("0.00-0.10", "0.10-0.30", "0.30-0.50",
         "0.50-0.70", "0.70-0.90", "0.90-1.01")
FOCUS = ("rn1", "ferrarichampions2026", "homerunhazard", "swisstony")
ALL = FOCUS + ("kch123", "w2c33")
NI = "NOT_IDENTIFIED"


def spearman(xs, ys):
    """Rank correlation, no scipy. Ties averaged."""
    n = len(xs)
    if n < 3:
        return None

    def rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return round(num / (dx * dy), 4) if dx and dy else None


def load(name):
    p = BLOBS / ("%s_reconstruction.json" % name)
    return json.loads(p.read_text()) if p.exists() else None


def rows_for(rec):
    """One row per band, joining the completion grid to the exact split."""
    comp = rec["REFERENCE_ACCOUNT_COMPLETION"]["LIFETIME"]
    grid = comp["BY_FIRST_LEG_PRICE_BAND"]
    ob = rec.get("MERGE_PNL_BY_OPEN_BAND", {}).get("LIFETIME")
    exact = isinstance(ob, dict) and ob.get("_RECONCILES") is True
    out = []
    for b in BANDS:
        g = grid.get(b) or {}
        e = (ob or {}).get(b) or {}
        out.append({
            "band": b,
            "OPENS": g.get("opens"),
            "COMPLETION_5S": g.get("completion_rate_5s", NI),
            "COMPLETION_1H": g.get("completion_rate_1h"),
            "COMPLETION_SETTLEMENT": g.get("completion_rate_settlement", NI),
            "MEAN_COMPLETED_PAIR_BASIS": g.get("mean_pair_basis"),
            "MEDIAN_PAIR_BASIS": g.get("median_pair_basis", NI),
            "RESIDUAL_RATE": g.get("residual_rate", NI),
            "MERGES": e.get("merges") if exact else NI,
            "EXACT_MATCHED_PNL": e.get("MATCHED_PNL") if exact else NI,
            "MATCHED_STAKE": e.get("MATCHED_STAKE") if exact else NI,
            "MATCHED_ROI": e.get("MATCHED_ROI") if exact else NI,
        })
    return out, exact, (ob or {}).get("_RESIDUAL_VS_ESTIMATOR")


def main() -> int:
    recs, tables, recon = {}, {}, {}
    for a in ALL:
        r = load(a)
        if r is None:
            continue
        recs[a] = r
        tables[a], ok, resid = rows_for(r)
        recon[a] = {"EXACT_BUCKET_PNL_ATTRIBUTION":
                    "ESTABLISHED" if ok else NI,
                    "residual_vs_estimator": resid}

    have_exact = [a for a in tables if recon[a]["EXACT_BUCKET_PNL_ATTRIBUTION"]
                  == "ESTABLISHED"]

    print("=" * 112)
    print("BETA48 CROSS-ACCOUNT FIRST-LEG TABLE  (LIFETIME, identical accounting)")
    print("=" * 112)
    print("EXACT_BUCKET_PNL_ATTRIBUTION per account:")
    for a in ALL:
        if a in recon:
            print("  %-22s %-12s residual %s" % (
                a, recon[a]["EXACT_BUCKET_PNL_ATTRIBUTION"],
                recon[a]["residual_vs_estimator"]))
        else:
            print("  %-22s %s (not returned)" % (a, NI))
    print()

    hdr = ("%-13s %9s %7s %7s %7s %8s %8s %7s %8s %14s %10s"
           % ("band", "OPENS", "C_5S", "C_1H", "C_SET", "MEANBAS",
              "MEDBAS", "RESID", "MERGES", "EXACT_PNL", "ROI"))
    for a in ALL:
        if a not in tables:
            continue
        print("--- %s ---" % a.upper())
        print(hdr)
        for r in tables[a]:
            def f(v, w, p=None):
                if v is None or v == NI:
                    return ("%-*s" % (w, NI))[:w]
                return ("%*.*f" % (w, p, v)) if p is not None else ("%*s" % (w, format(v, ",")))
            print("%-13s %9s %7s %7s %7s %8s %8s %7s %8s %14s %10s" % (
                r["band"],
                format(r["OPENS"], ",") if r["OPENS"] else "-",
                f(r["COMPLETION_5S"], 7, 4), f(r["COMPLETION_1H"], 7, 4),
                f(r["COMPLETION_SETTLEMENT"], 7, 4),
                f(r["MEAN_COMPLETED_PAIR_BASIS"], 8, 4),
                f(r["MEDIAN_PAIR_BASIS"], 8, 4),
                f(r["RESIDUAL_RATE"], 7, 4),
                format(r["MERGES"], ",") if isinstance(r["MERGES"], int) else NI[:8],
                format(r["EXACT_MATCHED_PNL"], ",.0f")
                if isinstance(r["EXACT_MATCHED_PNL"], (int, float)) else NI,
                ("%+.3f%%" % (100 * r["MATCHED_ROI"]))
                if isinstance(r["MATCHED_ROI"], float) else NI))
        print()

    # ---------------- the within-account discriminator test --------------
    print("=" * 112)
    print("WITHIN-ACCOUNT TEST — does the band separate profit from loss "
          "INSIDE each account?")
    print("=" * 112)
    verdicts = {}
    for a in have_exact:
        idx, roi = [], []
        for i, r in enumerate(tables[a]):
            if isinstance(r["MATCHED_ROI"], float) and r["MERGES"]:
                idx.append(i)
                roi.append(r["MATCHED_ROI"])
        rho = spearman(idx, roi)
        signs = ["+" if v > 0 else "-" for v in roi]
        crossings = sum(1 for i in range(1, len(signs))
                        if signs[i] != signs[i - 1])
        low = [v for i, v in zip(idx, roi) if i <= 1]
        high = [v for i, v in zip(idx, roi) if i >= 4]
        shape_ok = (bool(low) and bool(high)
                    and all(v > 0 for v in low) and all(v < 0 for v in high))
        verdicts[a] = {"spearman_band_vs_roi": rho,
                       "sign_pattern": "".join(signs),
                       "sign_crossings": crossings,
                       "low_bands_positive_high_bands_negative": shape_ok}
        print("%-22s rho=%-8s signs=%-8s crossings=%d  low+/high- = %s"
              % (a, rho, "".join(signs), crossings, shape_ok))

    if verdicts:
        all_mono = all(v["spearman_band_vs_roi"] is not None
                       and v["spearman_band_vs_roi"] <= -0.7
                       for v in verdicts.values())
        all_shape = all(v["low_bands_positive_high_bands_negative"]
                        for v in verdicts.values())
        print()
        print("monotone (rho <= -0.7) in EVERY account: %s" % all_mono)
        print("low-positive / high-negative in EVERY account: %s" % all_shape)

        # ---------- mix sufficiency: common band weights ----------------
        print()
        print("=" * 112)
        print("MIX SUFFICIENCY — reweight every account to ONE common band mix")
        print("=" * 112)
        tot_stake = {b: 0.0 for b in BANDS}
        for a in have_exact:
            for r in tables[a]:
                if isinstance(r["MATCHED_STAKE"], float):
                    tot_stake[r["band"]] += r["MATCHED_STAKE"]
        gross = sum(tot_stake.values())
        wts = {b: (tot_stake[b] / gross if gross else 0.0) for b in BANDS}
        print("common mix (stake share): %s"
              % {b: round(w, 4) for b, w in wts.items()})
        print()
        print("%-22s %14s %14s" % ("account", "ACTUAL_ROI", "COMMON_MIX_ROI"))
        mixed = {}
        for a in have_exact:
            num = den = 0.0
            for r in tables[a]:
                if isinstance(r["MATCHED_ROI"], float):
                    num += wts[r["band"]] * r["MATCHED_ROI"]
                    den += wts[r["band"]]
            cm = num / den if den else None
            act_p = sum(r["EXACT_MATCHED_PNL"] for r in tables[a]
                        if isinstance(r["EXACT_MATCHED_PNL"], float))
            act_s = sum(r["MATCHED_STAKE"] for r in tables[a]
                        if isinstance(r["MATCHED_STAKE"], float))
            act = act_p / act_s if act_s else None
            mixed[a] = {"actual_roi": round(act, 6) if act else None,
                        "common_mix_roi": round(cm, 6) if cm else None}
            print("%-22s %13s%% %13s%%"
                  % (a, "%+.3f" % (100 * act) if act is not None else NI,
                     "%+.3f" % (100 * cm) if cm is not None else NI))
        signs_act = {a: (mixed[a]["actual_roi"] or 0) > 0 for a in mixed}
        signs_mix = {a: (mixed[a]["common_mix_roi"] or 0) > 0 for a in mixed}
        print()
        print("actual signs:     %s" % signs_act)
        print("common-mix signs: %s" % signs_mix)
        agree = len(set(signs_mix.values())) == 1
        print("common mix makes every account agree in sign: %s" % agree)
    else:
        all_mono = all_shape = agree = False
        mixed, wts = {}, {}

    payload = {
        "TABLES": tables,
        "EXACT_ATTRIBUTION": recon,
        "WITHIN_ACCOUNT": verdicts,
        "MIX_SUFFICIENCY": {"weights": {b: round(w, 6) for b, w in wts.items()},
                            "per_account": mixed} if verdicts else NI,
        "CONTROLS_AVAILABLE": {
            "sport_or_question": "PARTIAL — BY_SPORT_OR_QUESTION top-25 only",
            "league": NI,
            "market_type": NI,
            "first_leg_size": "AVAILABLE — BY_FILL_SIZE_BUCKET (no P&L)",
            "first_leg_side": NI,
            "time_period": "AVAILABLE — BY_WEEK (no P&L)",
            "pregame_live_time_to_event": NI,
            "liquidity_book_state": NI,
        },
        "BETTOR_PASSIVE_FILL_PROBABILITY": NI,
        "BETTOR_EXPECTED_PAIR_PNL": NI,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "cross_account_discriminator.json").write_text(
        json.dumps(payload, indent=1, default=str))
    print("\nsealed -> research/beta48/evidence/cross_account_discriminator.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
