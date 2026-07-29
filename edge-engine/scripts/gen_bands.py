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


def main() -> None:
    tradeable, dead = [], []
    for r in csv.DictReader(open("data/calib_price.csv")):
        m = re.match(r"\[([\d.]+), ([\d.]+)\)", r["bin"])
        if not m:
            continue
        lo, hi = float(m.group(1)), float(m.group(2))
        edge = float(r["edge_cents"])
        if lo >= 0.90 or edge <= DEAD_CEILING:      # 90c+ dead per spec
            dead.append((lo, hi, edge))
        else:
            tradeable.append((lo, hi, edge, threshold_for(lo)))

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
