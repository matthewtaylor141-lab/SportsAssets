"""Maker economics: what a passive quote is worth, term by term.

A LARGE SPREAD IS NOT AN EDGE, and the decision engine's refusal to act
on one was right but incomplete. "P_FILL is NOT_IDENTIFIED" is a true
statement that does no work: it names one missing input and leaves the
other three unexamined. A maker decision has four terms and only one of
them is the spread.

    E[maker] = p_fill x ( half_spread
                        - adverse_selection
                        - inventory_carry(duration)
                        - exit_cost )
             + (1 - p_fill) x quote_cost

WHY EACH TERM IS THERE, and why leaving any of them out flatters the
answer:

  half_spread          what the quote is paid IF it fills and nothing
                       moves. This is the only term a naive model has.
                       Measured on PMUS: 0.0050 per share.

  adverse_selection    THE REASON THE FILL HAPPENED. A resting quote is
                       hit precisely when someone wants the other side,
                       so fills are not a random sample of the book --
                       they are conditioned on the taker's information.
                       E[value | filled] is strictly worse than
                       E[value], and the difference is not small on a
                       venue where the taker chooses the moment. A model
                       that omits it earns the half-spread on paper and
                       loses it in the markout.

  inventory_carry      a filled quote is a POSITION, held until it is
                       exited or settles. Capital is occupied for that
                       duration and the position carries risk the whole
                       time. Duration is not free even when the price
                       does not move.

  exit_cost            the position has to come back. On this venue,
                       crossing the spread to exit costs the full
                       spread -- so a round trip that earns half a
                       spread and pays a full one is negative before
                       anything else happens. THIS IS THE TERM MOST
                       OFTEN FORGOTTEN, and it is the one that decides
                       whether Class A/B can work at all.

  quote_cost           what an unfilled quote costs. Not zero: it
                       occupies risk budget, it can be picked off on a
                       fast move, and cancel/replace consumes API
                       allowance. Small, but not nothing.

WHAT IS MEASURED AND WHAT IS NOT, from the sprint verdict:

    Class A  passive same-venue complementary maker pair
             INSUFFICIENT_EVIDENCE, gross term 0.0050/share half-spread
    Class B  maker first leg + controlled completion
             INSUFFICIENT_EVIDENCE, gross term 0.0050/share half-spread

The GROSS term is measured. p_fill, adverse selection, duration and exit
cost are not. So this module computes the decomposition and returns
NOT_IDENTIFIED for the total, while making it possible to ask "what
would p_fill have to be for this to clear zero?" -- which is a
falsifiable question and a far more useful one than the spread alone.

SHADOW EVALUATION WITHOUT LIVE AUTHORITY. `evaluate()` accepts a
candidate parameter set and returns what it implies. A candidate carries
`authority="SHADOW"` and nothing in this module can change that. A
candidate's number is never returned as the engine's belief -- it is
returned as that candidate's claim, labelled with the candidate's own
validation status, so candidates can be compared in shadow and promoted
only by a separate, frozen, pre-registered decision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

NOT_IDENTIFIED = "NOT_IDENTIFIED"
IDENTIFIED = "IDENTIFIED"
SHADOW = "SHADOW"
LIVE = "LIVE"

# Measured on PMUS and recorded in the sprint verdict. The one term that
# is not a guess.
MEASURED_HALF_SPREAD_PER_SHARE = 0.0050


@dataclass(frozen=True)
class MakerParams:
    """One candidate parameter set. SHADOW authority, always.

    Every field defaults to None, meaning NOT_IDENTIFIED. A None does
    not become a zero anywhere in this module -- it propagates, and the
    total comes back NOT_IDENTIFIED with the missing terms named.
    """
    name: str = "unnamed"
    p_fill: float | None = None
    adverse_selection_per_share: float | None = None
    carry_per_share_per_hour: float | None = None
    expected_duration_hours: float | None = None
    exit_cost_per_share: float | None = None
    quote_cost_per_share: float | None = None
    validation_status: str = NOT_IDENTIFIED
    authority: str = SHADOW              # never LIVE from this module

    def missing(self) -> list[str]:
        out = []
        for f in ("p_fill", "adverse_selection_per_share",
                  "carry_per_share_per_hour", "expected_duration_hours",
                  "exit_cost_per_share", "quote_cost_per_share"):
            if getattr(self, f) is None:
                out.append(f)
        return out


@dataclass
class MakerEvaluation:
    status: str
    per_share_net: float | None = None
    terms: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    why: str = ""
    authority: str = SHADOW
    candidate: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def half_spread(bid: float | None, ask: float | None) -> float | None:
    """Half the quoted spread, or None. Never negative.

    A crossed or locked book (ask <= bid) has no half-spread to earn and
    returns None rather than a negative number that would read as a cost.
    """
    if bid is None or ask is None:
        return None
    if not (math.isfinite(bid) and math.isfinite(ask)):
        return None
    if ask <= bid:
        return None
    return (ask - bid) / 2.0


def round_trip_is_negative_before_anything_else(bid: float | None,
                                                ask: float | None) -> bool:
    """Does crossing out cost more than resting in earns?

    The structural question, answerable from the book alone with no
    probability at all: a maker earns at most half the spread and a
    taker exit pays the whole spread. If the position must be exited by
    crossing rather than by settling, the round trip starts at minus
    half a spread and every other term makes it worse.
    """
    hs = half_spread(bid, ask)
    if hs is None:
        return True
    return (2.0 * hs) > hs               # always true: exit > entry earn


def evaluate(params: MakerParams, *, bid: float | None = None,
             ask: float | None = None,
             size_contracts: float = 1.0,
             hold_to_settlement: bool = False) -> MakerEvaluation:
    """The decomposition, with every missing term named.

    `hold_to_settlement=True` removes the exit cost -- a position held to
    settlement is not crossed out, it resolves. That is the single
    change that can make Class A/B viable, and it is why the flag is
    explicit rather than assumed either way.
    """
    hs = half_spread(bid, ask)
    terms: dict = {"half_spread_per_share": hs,
                   "half_spread_source": ("BOOK" if hs is not None
                                          else "UNAVAILABLE")}
    missing = list(params.missing())
    if hs is None:
        missing.append("half_spread (book is crossed, locked or absent)")

    if hold_to_settlement:
        terms["exit_cost_per_share"] = 0.0
        terms["exit_note"] = ("held to settlement, so no crossing cost; "
                              "this removes the exit term and ONLY that one")
        if "exit_cost_per_share" in missing:
            missing.remove("exit_cost_per_share")
    else:
        terms["exit_cost_per_share"] = params.exit_cost_per_share
        terms["exit_note"] = ("exited by crossing, which costs the FULL "
                              "spread against a half-spread earned")

    terms["adverse_selection_per_share"] = params.adverse_selection_per_share
    terms["p_fill"] = params.p_fill
    carry = None
    if (params.carry_per_share_per_hour is not None
            and params.expected_duration_hours is not None):
        carry = (params.carry_per_share_per_hour
                 * params.expected_duration_hours)
    terms["inventory_carry_per_share"] = carry
    terms["quote_cost_per_share"] = params.quote_cost_per_share

    if missing:
        return MakerEvaluation(
            status=NOT_IDENTIFIED, per_share_net=None, terms=terms,
            missing=sorted(set(missing)),
            why=("%d of the terms are unmeasured, so the total is not a "
                 "number. A large spread is not an edge: the fill is "
                 "conditioned on the taker's information and the exit "
                 "costs more than the entry earns"
                 % len(set(missing))),
            authority=params.authority, candidate=params.name)

    exit_c = terms["exit_cost_per_share"]
    filled = (hs - params.adverse_selection_per_share - carry - exit_c)
    net = (params.p_fill * filled
           + (1.0 - params.p_fill) * -abs(params.quote_cost_per_share))
    terms["value_if_filled_per_share"] = filled
    return MakerEvaluation(
        status=IDENTIFIED, per_share_net=net * size_contracts, terms=terms,
        missing=[],
        why=("p_fill %.4f x (%.6f filled) + %.4f x (-%.6f quote cost)"
             % (params.p_fill, filled, 1 - params.p_fill,
                abs(params.quote_cost_per_share))),
        authority=params.authority, candidate=params.name)


def breakeven_p_fill(params: MakerParams, *, bid: float | None,
                     ask: float | None,
                     hold_to_settlement: bool = False) -> dict:
    """What would p_fill have to be for this quote to clear zero?

    THE USEFUL QUESTION. p_fill is unmeasured, but the OTHER terms can
    be supplied and the threshold solved for. A breakeven above 1.0
    means no fill probability rescues it and the structure is dead
    without measuring anything further -- which is a real, falsifiable
    finding obtainable today.
    """
    probe = MakerParams(
        name=params.name, p_fill=0.5,
        adverse_selection_per_share=params.adverse_selection_per_share,
        carry_per_share_per_hour=params.carry_per_share_per_hour,
        expected_duration_hours=params.expected_duration_hours,
        exit_cost_per_share=params.exit_cost_per_share,
        quote_cost_per_share=params.quote_cost_per_share,
        validation_status=params.validation_status,
        authority=params.authority)
    ev = evaluate(probe, bid=bid, ask=ask,
                  hold_to_settlement=hold_to_settlement)
    if ev.status != IDENTIFIED:
        return {"status": NOT_IDENTIFIED, "missing": ev.missing,
                "why": "the other terms are not all supplied"}

    filled = ev.terms["value_if_filled_per_share"]
    quote_cost = abs(params.quote_cost_per_share)
    denom = filled + quote_cost
    if denom <= 0:
        return {"status": IDENTIFIED, "breakeven_p_fill": None,
                "verdict": "NO_FILL_PROBABILITY_RESCUES_THIS",
                "value_if_filled_per_share": filled,
                "why": ("a fill is worth %.6f per share, which is not "
                        "positive. Filling more often makes it worse, so "
                        "the structure fails without measuring p_fill at "
                        "all" % filled)}
    p = quote_cost / denom
    return {"status": IDENTIFIED, "breakeven_p_fill": p,
            "verdict": ("REQUIRES_P_FILL_ABOVE_ONE" if p > 1.0
                        else "FEASIBLE_IF_P_FILL_EXCEEDS_BREAKEVEN"),
            "value_if_filled_per_share": filled,
            "why": ("needs p_fill > %.4f for the expected value to clear "
                    "zero" % p)}


def describe() -> dict:
    return {
        "model": "E[maker] = p_fill x (half_spread - adverse_selection "
                 "- carry(duration) - exit_cost) + (1-p_fill) x quote_cost",
        "measured_terms": {"half_spread_per_share":
                           MEASURED_HALF_SPREAD_PER_SHARE},
        "unmeasured_terms": ["p_fill", "adverse_selection_per_share",
                             "carry_per_share_per_hour",
                             "expected_duration_hours",
                             "exit_cost_per_share", "quote_cost_per_share"],
        "structural_fact": ("a maker earns at most HALF a spread and a "
                            "taker exit pays a WHOLE one, so a crossed "
                            "round trip starts negative"),
        "candidate_authority": SHADOW,
        "promotion": ("a candidate is promoted only by a separate frozen "
                      "pre-registered decision; nothing in this module can "
                      "grant live authority"),
    }
