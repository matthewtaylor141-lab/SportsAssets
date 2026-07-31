"""Regenerate the spread/total (derivative) band windows in config/bands.yaml.

WHY THESE ARE GENERATED SEPARATELY FROM MONEYLINE
-------------------------------------------------
The moneyline table comes from 5.34M resolved fills. Nothing of that size
exists for derivatives, so the windows were originally transcribed from one
account's statement (kch123): spread edge concentrated at 0.40-0.55, totals
0.40-0.50, "negative above 0.55". Encoded literally, that left the engine
able to trade 3 spread bands and 2 total bands out of 20.

The reading was too literal. A ROI-by-price table shows where an account BET,
not where edge EXISTS. "Negative above 0.55" is a statement about the buckets
they happened to fill, and the reference account's own moneyline measurement
says the expensive end is fine (0.75-0.80: +2.61c, 0.80-0.85: +2.21c,
0.85-0.90: +1.36c). There is no measured basis for treating high prices as
structurally bad.

The stronger argument is structural. Spreads and totals are two-sided markets
and our fair values come from de-vigging THE PAIR: p(Over 215.5) and
p(Under 215.5) are computed together and sum to one. If Over at 0.45 carries
edge, the identical calculation prices Under at 0.55 with the mirror edge —
same inputs, same confidence. A window that trades the cheap side and forbids
the expensive one isn't encoding "edge lives below 0.55"; it's encoding "we
only looked below 0.55". The one real asymmetry is payoff shape (85c risked
to win 15c), and that is a sizing question already answered by flat $1 fills
and the per-market cap, not an edge question.

WHAT THIS SCRIPT ENCODES
------------------------
  core       the kch123-measured zone, threshold unchanged at 0.025.
  mirror     the complementary side of every core line, same threshold —
             implied by the de-vig, not a new assumption.
  extension  0.10-0.90 at a STRICTER threshold (0.030) — no direct
             measurement, so it must clear a higher bar than the measured
             core and a higher bar than moneyline pays at any price.
  excluded   below 0.10 and above 0.90. Reason is precision, not opinion:
             the venue ticks in whole cents, so at a 3c price one tick is a
             33% error in the thing we are trying to measure to within 2c.

Everything here is SHADOW-TESTABLE and labelled as such. The grader scores
derivative fills by band; if the extension does not pay, it comes back out.
"""
import re

# Everything below is in whole CENTS, because that is the grid the venue
# actually quotes on and because mirroring (c -> 100 - c) has to land exactly.
# Working in 5c blocks left the mirror of the lowest tradeable cent one cent
# outside the window — a rounding artifact that reads as a policy asymmetry.
CORE_CENTS = {     # kch123-measured, direct evidence
    "spread": (40, 55),
    "total": (40, 50),
    # Player props have NO measured core — the calibration dataset carries no
    # prop history at all. Structurally a prop is the same object as a total
    # (a two-sided line de-vigged from an Over/Under pair), so it inherits
    # the total's shape, and every one of its bands is priced as UNMEASURED.
    "prop": (40, 50),
}
CORE_THRESHOLD = 0.025
EXT_THRESHOLD = 0.030      # unmeasured: must clear a higher bar than the core
EXT_LO_C, EXT_HI_C = 10, 90

# Props pay a surcharge over the equivalent total, for two reasons that are
# specific to props rather than to line bets in general:
#   - the books are thinner and move on single-player news; a late scratch
#     moves a strikeout line far more than it moves a game total
#   - the player-and-stat mapping is new, and mapping error surfaces as fake
#     edge, so the bar absorbs some of it until the nightly report has graded
#     a few hundred settlements
# It also trades a WIDER price range: "3+ home runs" is a genuine 5c bet,
# whereas a game line almost never sits there.
CATEGORY_SURCHARGE = {"prop": 0.010}
CATEGORY_SPAN_C = {"prop": (5, 95)}


def windows_for(category: str) -> list[tuple[int, float, str]]:
    """(cent, threshold, basis) for every tradeable price, cheapest first."""
    core_lo, core_hi = CORE_CENTS[category]
    core = set(range(core_lo, core_hi + 1))
    # The de-vig prices both sides of a line together, so the mirror of the
    # measured window carries exactly the same evidence as the window itself.
    mirror = {100 - c for c in core}
    bump = CATEGORY_SURCHARGE.get(category, 0.0)
    lo_c, hi_c = CATEGORY_SPAN_C.get(category, (EXT_LO_C, EXT_HI_C))
    out = []
    for c in range(lo_c, hi_c + 1):
        if bump:
            # A category with a surcharge has no measured core of its own —
            # it is borrowing another category's SHAPE, not its evidence, so
            # every band is labelled unmeasured however the shape arose.
            out.append((c, (CORE_THRESHOLD if c in core or c in mirror
                            else EXT_THRESHOLD) + bump, "unmeasured"))
        elif c in core:
            out.append((c, CORE_THRESHOLD, "measured"))
        elif c in mirror:
            out.append((c, CORE_THRESHOLD, "mirror"))
        else:
            out.append((c, EXT_THRESHOLD, "extension"))
    return out


def _merge(rows):
    """Collapse runs of equal-threshold cents into [lo, hi) ranges. The upper
    bound is one cent PAST the last included price — Policy.band_threshold
    matches on lo <= p < hi, so an inclusive top cent has to be written that
    way even when the number looks unrounded."""
    merged: list[list] = []
    for c, th, basis in rows:
        if merged and merged[-1][1] == c and merged[-1][2] == th:
            merged[-1][1] = c + 1
        else:
            merged.append([c, c + 1, th, basis])
    return [(lo / 100, hi / 100, th, basis) for lo, hi, th, basis in merged]


def render() -> str:
    out = [
        "# Per-category band policy — GENERATED by scripts/gen_category_bands.py.",
        "# Spread/total markets are two-sided: our fair value de-vigs the PAIR,",
        "# so an edge on one side is the same measurement as the mirror edge on",
        "# the other. The kch123-measured core (spread 0.40-0.55, total",
        "# 0.40-0.50) and its mirror keep the measured 2.5c threshold; the rest",
        "# of 0.10-0.90 is an unmeasured EXTENSION and must clear 3.0c — a",
        "# higher bar than the measured core, and higher than moneyline asks at",
        "# any price. Outside 0.10-0.90 we do not trade derivatives at all: the",
        "# venue ticks in whole cents, and at those prices one tick swamps the",
        "# edge we are trying to measure.",
        "# SHADOW-TESTABLE: the grader scores these bands; if the extension does",
        "# not pay, it comes back out.",
        "# Ranges are [lo, hi): an upper bound of 0.61 means 60c is the last",
        "# tradeable price, which is what makes the window mirror-symmetric.",
        "categories:",
    ]
    for category in ("spread", "total", "prop"):
        rows = _merge(windows_for(category))
        out.append(f"  {category}:")
        out.append("    tradeable:")
        for lo, hi, th, basis in rows:
            out.append(f'      "{lo:.2f}-{hi:.2f}": {{min_edge_threshold: {th:.3f}}}'
                       f"   # {basis}")
        out.append("    dead_zones: []")
    return "\n".join(out) + "\n"


def main() -> None:
    path = "config/bands.yaml"
    existing = open(path).read()
    # The moneyline half of the file is owned by gen_bands.py — keep it byte
    # for byte and replace only from the per-category marker onward.
    marker = re.search(r"^# Per-category band policy", existing, re.M)
    tail = re.search(r"^# Implausibility guard", existing, re.M)
    if not marker or not tail:
        raise SystemExit("bands.yaml is missing its section markers")
    open(path, "w").write(
        existing[:marker.start()] + render() + "\n" + existing[tail.start():])

    for category in ("spread", "total", "prop"):
        rows = _merge(windows_for(category))
        span = sum(hi - lo for lo, hi, _, _ in rows)
        lo_c, hi_c = CORE_CENTS[category]
        print(f"{category}: {len(rows)} ranges, {span:.2f} of price space "
              f"(was {(hi_c - lo_c) / 100:.2f})")


if __name__ == "__main__":
    main()
