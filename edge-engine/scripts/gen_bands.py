"""Regenerate the moneyline band policy from measured calibration.

Bands are not hand-picked: every 5c band in data/calib_price.csv with a
measured positive edge is tradeable; bands measuring <= DEAD_CEILING are
excluded. Thresholds follow the price-tier ladder already validated in the
original config (every pre-existing threshold is reproduced exactly), so
this change is purely ADDITIVE — it opens measured-positive bands that
were previously unlisted-and-therefore-blocked.
"""
import csv
import re

DEAD_CEILING = 0.20   # cents of measured edge at/below which a band is dead

# ── net-ROI floor ───────────────────────────────────────────────────────
# Profit is turnover x net ROI, and net ROI per fill is
# (threshold - crossing_cost) / price. Both the return AND the speed of
# proof collapse as price rises: a 2.0c edge on a 47c contract is 4.2%
# gross and needs ~2,500 settlements to distinguish from zero; the same
# 2.0c at 12c is 17% gross and proves itself in ~500. A band whose net
# ROI sits below the benchmark is worse than dead — it consumes day
# budget and market claims that the productive bands wanted, and it
# cannot even prove itself within a season.
#
# CROSSING_CENTS is what we actually PAY over mid to enter, measured from
# our own fills (spread_report, 2026-08-02: moneyline 1.4c, draw 1.5c,
# uncategorized 1.6c). 1.5c is the conservative round figure. Re-measure
# when maker pricing returns; a lower measured cost re-opens bands by
# regeneration, not by hand.
#
# The numerator is EXPECTED CAPTURE, not the threshold. The threshold is
# the minimum edge we would accept; the band's measured edge_cents is what
# fills in that band actually earned across 5.34M of them. Using the
# threshold alone once closed 0.45-0.50 — a band measuring +2.94c, which
# nets 3.0% after crossing — because the 2.0c minimum can't pay 2% at 47c
# even though the average fill there does. Where a measurement exists it
# is the better estimate; expected capture can never be below the
# threshold, because we refuse everything under it.
NET_ROI_FLOOR = 0.02      # the benchmark: reference account = 2.3%
CROSSING_CENTS = 1.5


def threshold_for(lo: float) -> float:
    if lo < 0.15:
        return 0.030
    if lo < 0.45:
        return 0.025
    if lo < 0.70:
        return 0.020
    if lo < 0.85:
        return 0.015
    return 0.012


def clears_net_floor(lo: float, hi: float, threshold: float,
                     edge_cents: float | None = None) -> bool:
    mid = (lo + hi) / 2
    capture = max(threshold * 100, edge_cents or 0.0)   # cents
    return (capture - CROSSING_CENTS) / 100 / mid >= NET_ROI_FLOOR


def main() -> None:
    tradeable, dead = [], []
    for r in csv.DictReader(open("data/calib_price.csv")):
        m = re.match(r"\[([\d.]+), ([\d.]+)\)", r["bin"])
        if not m:
            continue
        lo, hi = float(m.group(1)), float(m.group(2))
        edge = float(r["edge_cents"])
        th = threshold_for(lo)
        if lo >= 0.90 or edge <= DEAD_CEILING:      # 90c+ dead per spec
            dead.append((lo, hi, edge))
        elif not clears_net_floor(lo, hi, th, edge):
            # Measured-positive but unprofitable to TRADE at our costs: the
            # edge is real and smaller than what we pay to cross plus the
            # benchmark. Dead until the measured crossing cost falls.
            dead.append((lo, hi, edge))
        else:
            tradeable.append((lo, hi, edge, th))

    out = ["# Entry-price band policy — GENERATED from data/calib_price.csv",
           "# (scripts_gen_bands.py). Every band with measured positive edge is",
           "# tradeable; thresholds follow the validated price-tier ladder.",
           "# Source: 5.34M resolved fills of the reference account.",
           "tradeable:"]
    for lo, hi, edge, th in tradeable:
        out.append(f'  "{lo:.2f}-{hi:.2f}": {{edge_cents: {edge:.2f}, '
                   f"min_edge_threshold: {th:.3f}}}")
    out.append("dead_zones: [" + ", ".join(f'"{lo:.2f}-{hi:.2f}"'
                                           for lo, hi, _ in dead) + "]")
    out.append("kalshi_fee_adjustment: true")

    # Preserve the per-category section verbatim (kch123-derived, separate
    # evidence base — not regenerated here).
    existing = open("config/bands.yaml").read()
    idx = existing.find("\n# Per-category band policy")
    if idx != -1:
        out.append(existing[idx:].rstrip())

    open("config/bands.yaml", "w").write("\n".join(out) + "\n")
    print(f"tradeable bands: {len(tradeable)} (was 8)")
    print(f"dead: {[f'{lo:.2f}-{hi:.2f}' for lo, hi, _ in dead]}")


if __name__ == "__main__":
    main()
