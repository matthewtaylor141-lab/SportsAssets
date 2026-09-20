"""THE DECISIONS WRITTEN BEFORE THE EV MACHINERY COULD LOAD.

Owner directive, §5:

    "Use PRE_PACKAGING_EV_MACHINERY_UNAVAILABLE for the immutable V3
    decisions written before the EV machinery was successfully
    available inside the running production worker. Do not rewrite
    those rows. Do not call them contaminated."

WHY "CONTAMINATED" WOULD HAVE BEEN THE WRONG WORD, and the distinction
matters more than the label. A contaminated row is one whose content
is wrong -- a number produced by reasoning that has since been
retracted. These rows contain no such number. Every one of them says:

    proposed_action   NO_TRADE
    action_ev_status  EV_MACHINERY_UNAVAILABLE
    table             []

which was TRUE when it was written and is true now. The engine could
not be loaded, so nothing was priced, and the row says exactly that.
That is honest evidence of a real operating condition, not damaged
evidence of a market.

THE FAIL-CLOSED PATH IS WHY. The bridge refuses to improvise when it
cannot load its engines, so the uncorrected first bridge -- the one
carrying a sign constraint on FILL_SELECTION_EFFECT and a REFUTED
verdict on crossing, both later retracted -- never produced a stored
number at all. A probe of the ledger confirms it: across every row of
this cohort, zero carry BREAK_EVEN_P_FILL_LOWER_BOUND and zero carry
REFUTED.

WHAT THEY MUST BE EXCLUDED FROM, AND WHY ONLY THAT. They are excluded
from POST-PACKAGING EV-COMPONENT COVERAGE, because a coverage
statistic asks "of the decisions that could have priced actions, how
many did" -- and these could not, so including them would understate
coverage by counting an outage as a modelling failure. They are
excluded from nothing else. They remain part of the complete V3 record.

THE BOUNDARY IS A VERSION, NOT A TIMESTAMP. V3 was frozen against the
first bridge's code sha, so when the corrections moved that sha the
production guard blocked writing under V3 entirely -- which means V3
can never acquire a post-packaging row. The cohort is closed by
construction rather than by a cutoff anyone has to remember or defend.

    V3_POST_PACKAGING_EV_DECISIONS = 0, permanently, and not because
    nothing happened after some chosen instant -- because the version
    itself cannot be written to again.
"""

from __future__ import annotations

COHORT = "PRE_PACKAGING_EV_MACHINERY_UNAVAILABLE"

COHORT_VERSION = "BETTOR_EV_SHADOW_V3"
SUCCESSOR_VERSION = "BETTOR_EV_SHADOW_V4"

MACHINERY_UNAVAILABLE = "EV_MACHINERY_UNAVAILABLE"

# The instant V3 was frozen, by the deploy that carried the first
# bridge. Recorded as an observed fact, not used as a cutoff: the
# cohort is defined by VERSION and STATUS, never by this timestamp.
V3_FROZEN_AT = "2026-09-20T15:07:07.838004Z"
V3_FROZEN_CODE_SHA = "8247f618e2a0777e"

NOT_CONTAMINATED = (
    "these rows are not contaminated. A contaminated row would carry a "
    "number produced by reasoning since retracted; these carry no "
    "number at all. Each says NO_TRADE because EV_MACHINERY_UNAVAILABLE, "
    "which was true when written and is true now")

WHY_THE_RETRACTED_REASONING_NEVER_REACHED_THEM = (
    "the bridge fails closed when it cannot load its engines, so the "
    "uncorrected first bridge never priced anything. Verified in the "
    "ledger rather than assumed: zero rows carry "
    "BREAK_EVEN_P_FILL_LOWER_BOUND and zero carry REFUTED")

EXCLUDED_FROM = "POST_PACKAGING_EV_COMPONENT_COVERAGE"

WHY_EXCLUDED = (
    "a coverage statistic asks how many decisions that COULD price "
    "actions did so. These could not, because the engine was not "
    "loadable, so counting them would report an outage as a modelling "
    "failure and understate coverage")

EXCLUDED_FROM_NOTHING_ELSE = (
    "they remain part of the complete V3 record and of every count of "
    "what the lane observed and decided. The exclusion is one "
    "statistic wide")

CLOSED_BY_CONSTRUCTION = (
    "V3 was frozen against the first bridge's code sha, so once the "
    "corrections moved that sha the production guard blocked all "
    "writing under V3. The cohort cannot grow and cannot acquire a "
    "post-packaging row -- it is closed by the version, not by a "
    "timestamp anyone has to defend")

IMMUTABLE = (
    "shadow_decisions carries an append-only trigger. These rows are "
    "not rewritten, reclassified in place or deleted; the "
    "classification lives here and in reporting, never on the row")


def classify(policy_version, action_ev_status) -> dict:
    """Which cohort a decision belongs to, for reporting only."""
    if policy_version != COHORT_VERSION:
        return {"cohort": None, "inCohort": False,
                "why": "not a %s decision" % COHORT_VERSION}
    if action_ev_status == MACHINERY_UNAVAILABLE:
        return {
            "cohort": COHORT, "inCohort": True,
            "excludedFrom": EXCLUDED_FROM,
            "whyExcluded": WHY_EXCLUDED,
            "excludedFromNothingElse": EXCLUDED_FROM_NOTHING_ELSE,
            "notContaminated": NOT_CONTAMINATED,
            "immutable": IMMUTABLE,
        }
    # A V3 row that PRICED something would be post-packaging. The guard
    # makes this unreachable; it is handled anyway rather than assumed
    # impossible, because an assumption that never runs is not a check.
    return {"cohort": "V3_POST_PACKAGING", "inCohort": False,
            "why": ("a V3 decision carrying a priced action table. The "
                    "policy guard should make this unreachable, so its "
                    "appearance is a finding rather than a category")}


def report(pre_packaging_count=None, post_packaging_count=None) -> dict:
    """The two figures §5 asks to be reported separately."""
    return {
        "cohort": COHORT,
        "cohortVersion": COHORT_VERSION,
        "successorVersion": SUCCESSOR_VERSION,
        "V3_PRE_PACKAGING_EV_MACHINERY_UNAVAILABLE_DECISIONS":
            pre_packaging_count,
        "V3_POST_PACKAGING_EV_DECISIONS": post_packaging_count,
        "v3FrozenAt": V3_FROZEN_AT,
        "v3FrozenCodeSha": V3_FROZEN_CODE_SHA,
        "closedByConstruction": CLOSED_BY_CONSTRUCTION,
        "notContaminated": NOT_CONTAMINATED,
        "whyTheRetractedReasoningNeverReachedThem":
            WHY_THE_RETRACTED_REASONING_NEVER_REACHED_THEM,
        "excludedFrom": EXCLUDED_FROM,
        "excludedFromNothingElse": EXCLUDED_FROM_NOTHING_ELSE,
        "immutable": IMMUTABLE,
    }


def describe() -> dict:
    return report()
