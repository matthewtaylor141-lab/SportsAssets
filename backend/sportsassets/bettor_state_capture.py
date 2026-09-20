"""UNSELECTED PROSPECTIVE PMUS STATE CAPTURE. Frozen before row 1.

Owner directive 2026-09-20, "APPROVED -- START THE READ-ONLY
PROSPECTIVE PMUS UNSELECTED CAPTURE":

    §3 "This is READ ONLY. No orders. No capital. No shadow fill
       fabrication. No mandate activation. Freeze the sampling rule
       BEFORE row 1. ... Selection must be independent of future
       economics."

    §4 "Do not change these after seeing outcomes without creating a
       new version."

WHAT THIS DATASET IS, AND THE ONE THING IT IS NOT
-------------------------------------------------

It measures object A of the owner's §2 taxonomy:

    A. UNCONDITIONAL STATE ECONOMICS
       E[SETTLEMENT - QUOTE | MARKET STATE]

It does NOT measure:

    B. P(OUR RESTING ORDER FILLS | STATE, QUOTE)
    C. E[SETTLEMENT - QUOTE | OUR RESTING ORDER FILLED, STATE]
    D. MAKER EV

and the owner's correction of 2026-09-20 is the reason the distinction
is carried in code rather than in a comment:

    "Adverse selection for a maker is conditional on execution. An
    unselected book + settlement dataset observes STATE, QUOTE and
    FUTURE VALUE, but does NOT observe WHETHER OUR HYPOTHETICAL
    RESTING ORDER WOULD HAVE FILLED. Therefore it solves
    STATE-SELECTION bias but does NOT solve FILL-SELECTION bias."

So the quantity this dataset yields is named

    UNCONDITIONAL_QUOTE_TO_SETTLEMENT_VALUE

and the name `UNCONDITIONAL_MAKER_ADVERSE_SELECTION` is REFUSED by
this module -- `forbidden_name()` raises on it -- because a name is
the only part of a statistic that survives into a headline.

WHY SELECTION HAPPENS BEFORE THE BOOK IS READ
----------------------------------------------

The market to sample is chosen from a stable hash of its venue
identifier, on a rotation fixed by the clock. Nothing about the book,
the spread, the depth, the recent move, or the eventual outcome enters
that choice, because none of it has been read yet when the choice is
made.

The consequence that matters most: A MARKET WHOSE BOOK CANNOT BE READ
STILL WRITES A ROW. Dropping unreadable books would condition the
dataset on readability, readability correlates with liquidity, and
liquidity correlates with exactly the economics we are trying to
measure. An unreadable book at a known instant is evidence, and it is
stored with a named reason.

The existing BETTOR lane orders its universe by `updated_at DESC`,
which is a selection on recent venue activity. That rule is fine for
what it does and is NOT reused here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from . import bettor_book_snapshot as booksnap
from . import bettor_sport_mapping as sportmap

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ── §2. THE FOUR OBJECTS, AND THE SUBSTITUTIONS THAT ARE REFUSED ─────

OBJECT_A = "UNCONDITIONAL_STATE_ECONOMICS"
OBJECT_B = "FILL_PROBABILITY"
OBJECT_C = "FILL_CONDITIONAL_ADVERSE_SELECTION"
OBJECT_D = "MAKER_EV"

OBJECTS = {
    OBJECT_A: {
        "estimand": "E[SETTLEMENT - QUOTE | MARKET STATE]",
        "status": "MEASURABLE_PROSPECTIVELY_WITHOUT_P_FILL",
        "measuredBy": "THIS_DATASET",
    },
    OBJECT_B: {
        "estimand": "P(OUR RESTING ORDER FILLS | MARKET STATE, QUOTE)",
        "status": NOT_IDENTIFIED,
        "measuredBy": "NOTHING_AVAILABLE",
    },
    OBJECT_C: {
        "estimand": ("E[SETTLEMENT - QUOTE | OUR RESTING ORDER FILLED, "
                     "STATE]"),
        "status": NOT_IDENTIFIED,
        "measuredBy": "NOTHING_AVAILABLE",
        "why": ("conditional on execution. This dataset never observes "
                "an execution of ours, so the conditioning event never "
                "occurs in it"),
    },
    OBJECT_D: {
        "estimand": ("combination of P_FILL, VALUE_IF_FILLED, "
                     "VALUE_IF_NO_FILL, FEES, REBATES, INVENTORY "
                     "CONSEQUENCES"),
        "status": NOT_IDENTIFIED,
        "measuredBy": "NOTHING_AVAILABLE",
    },
}

# The name this dataset's quantity may carry.
MEASURED_QUANTITY = "UNCONDITIONAL_QUOTE_TO_SETTLEMENT_VALUE"
MEASURED_QUANTITY_ALIAS = "UNCONDITIONAL_STATE_FUTURE_VALUE"

# The names it may NOT carry, and why each one is wrong. These are
# enforced, not documented: see forbidden_name().
FORBIDDEN_NAMES = {
    "UNCONDITIONAL_MAKER_ADVERSE_SELECTION": (
        "adverse selection is conditional on execution. This dataset "
        "never observes whether a hypothetical resting order would "
        "have filled, so it solves STATE-selection bias and leaves "
        "FILL-selection bias untouched"),
    "MAKER_ADVERSE_SELECTION": (
        "same defect. Object A may never be substituted for object C"),
    "ADVERSE_SELECTION": (
        "ambiguous between objects A and C, and the ambiguity is "
        "exactly the error being guarded against"),
    "MAKER_EV": (
        "object D needs P_FILL, VALUE_IF_NO_FILL, fees, rebates and "
        "inventory consequences, none of which are in this dataset"),
    "BASE_CASE_EV": (
        "retracted 2026-09-20. It combined populations from different "
        "venues, windows and flow-selection regimes"),
}

NEVER_SUBSTITUTE = (
    "Never substitute A for C. Never substitute A for D.")


class ForbiddenName(ValueError):
    """Raised when a result is about to be given a name that asserts
    more identification than the data supports."""


def forbidden_name(name: str) -> None:
    """Raise if `name` claims an estimand this dataset cannot support.

    Called at every point a quantity derived from this dataset is
    labelled. A statistic's name is what survives into a summary, a
    slide and a decision; the arithmetic is not what gets misread.
    """
    why = FORBIDDEN_NAMES.get(str(name).strip().upper())
    if why:
        raise ForbiddenName(
            "%s may not be used for a quantity measured from %s: %s. "
            "Use %s." % (name, UNIVERSE_VERSION, why, MEASURED_QUANTITY))


# ── THE POST-DEPLOYMENT MEASUREMENT WINDOW, DECLARED IN ADVANCE ─────
#
# Owner 2026-09-20: "Declare the window before inspecting its results."
#
# Written and committed BEFORE any query of it was run. Its start is
# tied to an event -- the deploy that puts the tick telemetry live --
# rather than to a clock time chosen after seeing data, so the window
# cannot have been picked to flatter a result.
#
# WHY THE 10-MINUTE WARM-UP. The adaptive pacing carries state across
# ticks: a worker that restarts begins at READ_PACING_BASE_S and has to
# re-discover the venue's tolerance. Measuring from the instant of
# deploy would measure the re-discovery, not the steady state. Ten
# minutes is two full cadence buckets.
#
# WHY IT ENDS. An open-ended window can be stopped when the numbers
# look right. This one has a declared length.
MEASUREMENT_WINDOW = {
    "id": "POST_TELEMETRY_DEPLOY_W1",
    "declaredAt": "2026-09-20T21:15Z",
    "declaredBeforeAnyQueryOfIt": True,
    "startRule": ("the completion of the deploy that first carries "
                  "bettor_capture_ticks and timing_class, PLUS a "
                  "10-minute warm-up for the adaptive pacer to leave "
                  "its cold start"),
    "lengthMinutes": 30,
    "reportedSeparately": ["initial reads", "follow-up reads",
                           "429 rate", "throughput"],
    "missingVsPending": (
        "an observation is FINALLY_MISSING only once its recovery "
        "deadline (horizon + HORIZON_DUE_WINDOW_S) has elapsed with no "
        "read. Before that it is PENDING and must not be counted as a "
        "loss -- a horizon that has not had time to be read yet is not "
        "a failure to read it"),
    "throughputIsNotCoverage": (
        "rows per minute extrapolated to a full pass does NOT establish "
        "that every eligible market is visited. Markets that enter and "
        "resolve between two visits are never seen, and no throughput "
        "figure detects that"),
}


# ── W2, DECLARED BEFORE THE REPAIR IS DEPLOYED ──────────────────────
#
# Written and committed BEFORE the reservation fix went live, so its
# acceptance criteria cannot have been fitted to its results. W1 is
# untouched and keeps running out to its own deadlines.
#
# W1's operational result was FAILED: zero ON_TIME reads at every
# horizon. W2 asks whether reserving the follow-up budget changes that,
# and at what cost to initial coverage -- because the reservation is a
# REALLOCATION of a fixed budget, not an increase, so any gain in
# follow-ups is paid for in initial reads.
# The repair's deployment, recorded so outcomes collected before and
# after it stay distinguishable without inference from row timestamps.
REPAIR_DEPLOYMENTS = (
    {"change": "FOLLOWUP_BUDGET_RESERVATION",
     "commit": "7101382",
     "deployStarted": "2026-09-20T22:07:22Z",
     "deployEnded": "2026-09-20T22:08:17Z",
     "pacingVersion": "BETTOR_CAPTURE_PACING_V2_ADAPTIVE",
     "universeVersion": "BETTOR_UNSELECTED_STATE_V2",
     "what": ("tick budget split before either pass; follow-up share "
              "can no longer be consumed by sampling. Total reads per "
              "tick unchanged, so gateway pacing is identical"),
     "alsoFixed": ("fu_due now counts true outstanding demand via "
                   "mids_outstanding rather than the post-limit batch; "
                   "fu_selected records what the budget admitted"),
     "cohortsBefore": "W1 and everything earlier, on 1dc4854 and prior",
     "cohortsAfter": "W2 and everything later"},
)

MEASUREMENT_WINDOW_W2 = {
    "id": "POST_RESERVATION_REPAIR_W2",
    "declaredAt": "2026-09-20T22:10Z",
    "startsAt": "2026-09-20T22:18:17Z",
    "endsAt": "2026-09-20T22:48:17Z",
    "boundsFixedBy": ("deploy 7101382 ended 22:08:17Z + 10 min warm-up; "
                      "the rule, not a choice made after seeing data"),
    "declaredBeforeRepairDeployed": True,
    "startRule": ("completion of the deploy carrying the follow-up "
                  "budget reservation, PLUS a 10-minute warm-up, "
                  "identical to W1's rule"),
    "lengthMinutes": 30,
    "cohort": ("observations whose observed_at falls inside the "
               "window, then followed to each horizon's own recovery "
               "deadline. Identical construction to W1"),

    # ── DENOMINATORS, the same four buckets W1 reports ──────────────
    "reportedBuckets": ("NOT_YET_DUE", "DUE_ON_TIME",
                        "DUE_LATE_RECOVERY", "DUE_PENDING",
                        "DUE_FINALLY_MISSING"),
    "everyCohortObservationAccountedAtEveryHorizon": True,

    # ── OPERATIONAL ACCEPTANCE CRITERIA, fixed in advance ───────────
    #
    # Stated as thresholds, not as "improvement", because any non-zero
    # number improves on W1's zero and that is too weak a bar to be
    # worth measuring against.
    "PASS_IF": {
        "ON_TIME_300S": (
            ">= 50% of the 300s-due cohort is ON_TIME. The 300s window "
            "is [270,330]s -- 60s wide against a 60s tick -- so a tick "
            "should nearly always fall inside it when budget exists. "
            "This is the horizon the repair must fix"),
        "ON_TIME_60S": (
            "> 0% of the 60s-due cohort is ON_TIME. The 60s window is "
            "[30,90]s and a 60s tick can miss it entirely by phase, so "
            "full coverage is not achievable by budget alone and is "
            "NOT claimed as a criterion"),
        "INITIAL_COVERAGE_COST": (
            "obs_attempted / obs_scheduled reported and compared with "
            "W1's 44.6%. A fall is EXPECTED and is the price of the "
            "reallocation; it is reported, not hidden"),
    },
    "FAIL_IF": (
        "ON_TIME remains 0 at every horizon, which would mean the "
        "bottleneck is not budget allocation and the diagnosis was "
        "wrong"),

    # ── WHAT W2 CANNOT ESTABLISH ────────────────────────────────────
    "capacityRemainsInsufficient": (
        "CORRECTED. The earlier figure counted 3 follow-ups per "
        "arrival, which is the number that come DUE inside a 30-minute "
        "window, not the number an arrival eventually needs. Steady "
        "state is 4 -- the 3600s horizon is not exempt, it is merely "
        "not yet due when a short window closes. In actual tick units "
        "(p50 spacing 72s, not the nominal 60s): arrivals 2.64/tick, "
        "so WITHIN-WINDOW follow-up demand is 7.9/tick and STEADY "
        "STATE is 10.6/tick, against 8 sampling wanted and a capacity "
        "of 1-7 reads/tick. Short-window demand understates the "
        "standing requirement by one horizon's worth. "
        "The system is CAPACITY-BOUND, not merely mis-scheduled. A "
        "reservation can only decide WHICH reads are lost, never "
        "eliminate the loss. Closing the gap needs fewer horizons, a "
        "lower sampling rate, or more venue headroom -- each a "
        "separate decision, none taken here"),
    "notAClaimOfCompleteness": (
        "the collection system is NOT complete and W2 cannot make it "
        "so. W2 measures one specific repair"),

    # ── A DEFECT IN THIS DECLARATION, RECORDED RATHER THAN QUIETLY ──
    # ── DROPPED WHEN THE RESULT CAME IN ─────────────────────────────
    #
    # PASS_IF.ON_TIME_300S justifies its 50% bar with "the 300s window
    # is [270,330]s -- 60s wide against a 60s tick". That window did
    # not exist in the code W2 ran. Under the V2 eligibility rule the
    # scheduler could not SELECT an observation until T0+300, while
    # ON_TIME closes at T0+330, so the selectable window was [300,330]
    # -- 30s wide, against ticks 65 to 82s apart. The criterion assumed
    # the eligibility change that ships in V3.
    #
    # The W2 result is therefore reported against the criterion AS
    # DECLARED, and this miscalibration is reported beside it. It
    # explains a shortfall; it does not excuse one, and W2's numbers
    # are not restated against a bar invented afterwards.
    "declarationDefect_ON_TIME_300S": (
        "the 50% bar was justified by a [270,330]s selectable window "
        "that the V2 eligibility rule did not provide -- it was "
        "[300,330]s. The criterion described V3's sampling rule before "
        "V3 existed. Reported, not retrofitted"),
}


# ── W3: THE DEPLOYABLE CHANGE AND ITS MEASUREMENT PLAN ──────────────
#
# Declared BEFORE the deploy and before any W3 row exists.
MEASUREMENT_WINDOW_W3 = {
    "id": "POST_ROTATION_AND_EARLY_ELIGIBILITY_W3",
    "declaredAt": "2026-09-20T22:40Z",
    "declaredBeforeDeploy": True,
    "startRule": ("completion of the deploy carrying PACING_VERSION "
                  "BETTOR_CAPTURE_PACING_V3_ROTATED_FOLLOWUPS, plus a "
                  "10-minute warm-up. Identical to W1's and W2's rule; "
                  "the exact timestamps are filled from the deploy "
                  "record, not chosen after seeing rows"),
    "lengthMinutes": 30,
    # Filled 22:58Z from the deploy record, before any W3 row existed.
    "startsAt": "2026-09-20T23:03:17Z",
    "endsAt": "2026-09-20T23:33:17Z",
    "boundsFixedBy": ("workers deploy 0868e3e live 22:53:17Z + 10 min "
                      "warm-up; the same rule W1 and W2 used, applied "
                      "before the window opened"),
    "preflight": (
        "research/bettor_migration_091_check.sql, run 178 at 22:57:03Z: "
        "fu_rotation_head, fu_per_horizon_cap and fu_selected all "
        "present; newest tick 81s old; 10 ticks in the last 10 min; "
        "rotation heads already observed as 300, 60, 900 (and NULL on "
        "the V2 ticks the 10-minute window still spans, which write no "
        "such column). The rotation is cycling in production"),
    "cohort": ("observations whose observed_at falls inside the "
               "window, followed to each horizon's own recovery "
               "deadline. Identical construction to W1 and W2"),

    # ── WHAT CHANGED, AND WHY IT IS ONE CHANGE AND NOT TWO ──────────
    #
    # TWO MECHANISMS SHIP TOGETHER, DELIBERATELY, AND THE BUNDLING IS
    # A COST I AM CHOOSING TO PAY RATHER THAN HIDE.
    #
    #   ALLOCATION  the follow-up horizon order rotates on service
    #               opportunities and each horizon is capped at its
    #               share of the tick's follow-up budget
    #   ELIGIBILITY an observation becomes selectable at T0+h-30
    #               instead of T0+h, so the selectable window is the
    #               whole tolerance band
    #
    # Offline the allocation change ALONE lifts every horizon out of
    # starvation and still returns ZERO on-time reads at 300s and 900s:
    # every one of those attempts lands late, because a 30s selectable
    # window cannot be hit reliably by ticks 65-82s apart. Shipping
    # allocation alone would therefore have spent a window to measure a
    # result that was already predictable and uninformative.
    #
    # THEY REMAIN ATTRIBUTABLE because they move different recorded
    # quantities: allocation moves FU_ATTEMPTED per horizon (coverage),
    # eligibility moves TIMING_CLASS (on-time share). A result showing
    # coverage without on-time isolates the allocation change; a result
    # showing both isolates the pair.
    "changes": {
        "ALLOCATION": "PACING_VERSION BETTOR_CAPTURE_PACING_V3_ROTATED_FOLLOWUPS",
        "ELIGIBILITY": "ELIGIBILITY_VERSION BETTOR_ELIGIBILITY_V3_EARLY_30",
    },
    "attributionRule": (
        "FU_ATTEMPTED per horizon attributes to the allocation change; "
        "TIMING_CLASS attributes to the eligibility change. They are "
        "separate columns and are never pooled"),

    # ── ACCEPTANCE CRITERIA, FIXED IN ADVANCE ───────────────────────
    "PASS_IF": {
        "COVERAGE_EVERY_HORIZON": (
            "FU_ATTEMPTED > 0 at all four horizons among observations "
            "whose horizon came due inside the window. This is the "
            "allocation criterion and it is the one the rotation must "
            "satisfy. Zero at any horizon that had standing eligible "
            "demand falsifies the rotation repair"),
        "ON_TIME_300S": (
            ">= 40% of the 300s-due cohort is ON_TIME. The selectable "
            "window is now [270,330]s -- 60s wide against ticks of "
            "65-82s -- so a tick should usually fall inside it when "
            "budget exists. The bar is 40 and not 50 because a tick "
            "gap can exceed the band's width"),
        "ON_TIME_900S": (
            "> 0% of the 900s-due cohort is ON_TIME. Stated weakly on "
            "purpose: 900s on-time coverage depends on where ticks "
            "fall relative to T0+900, and at some spacings no tick can "
            "land inside the band at all"),
    },
    "FAIL_IF": (
        "any horizon with standing eligible demand receives zero "
        "attempts, which would mean the rotation does not reach it"),

    # ── WHAT W3 CANNOT ESTABLISH ────────────────────────────────────
    "noPromiseOfFullCoverage": (
        "W3 does NOT promise on-time coverage at every horizon. "
        "Measured demand exceeds measured capacity -- steady-state "
        "follow-up demand is 10.6 reads/tick against a capacity of "
        "1-7 -- so reads will still be lost. The rotation changes "
        "WHICH are lost; it cannot change THAT some are. Under "
        "overload, selective loss becomes spread loss, and that is the "
        "whole of the claim"),
    "toleranceUnchanged": (
        "HORIZON_TOLERANCE_S remains 30 and ADMISSIBLE_TO_HORIZON_GATE "
        "remains ON_TIME only. No criterion here is met by widening "
        "either"),
    "simulationIsNotMeasurement": (
        "the offline decomposition above comes from "
        "bettor_schedule_sim, which exercises the allocation rule and "
        "observes no venue. Reproducing W1's qualitative shape is not "
        "reproducing W1's production behaviour, and no simulated "
        "number may be reported as a W3 result"),
}


# ── §4. THE FROZEN UNIVERSE RULE ─────────────────────────────────────

UNIVERSE_VERSION = "BETTOR_UNSELECTED_STATE_V2"

# ── WHY THERE IS A V2, AND WHAT V1's ROWS ARE ────────────────────────
#
# V1 ran from 19:23:35Z to 19:31Z on 2026-09-20 and wrote a small number
# of rows. It was replaced because its ROTATION WAS NOT A ROTATION.
#
# V1 sized the rotation on an assumed ~9,700 eligible legs: 277 slices,
# cap 40, so ~35 per slice and a full pass covering the universe. The
# measured figure is 47,078 eligible legs -- 170 per slice. The cap
# therefore bound on EVERY pass, and because the within-slice ordering
# is a stable hash, the same 40 markets were drawn from each slice
# forever: a fixed panel of ~11,000 legs with ~36,000 never sampled at
# all. Every V1 row carries slice_truncated = true, which is how it was
# caught in the first two buckets.
#
# THE SECOND DEFECT. V1 read one book PER LEG. The venue's book endpoint
# takes a market slug and knows nothing about sides, so the yes and no
# legs of one market issued two identical requests for one payload --
# double the venue load for no extra information. V2 samples MARKETS and
# reads each one once. That halves the requirement from 47,078 to 23,539
# and removes the hazard of two rows sharing a payload.
#
# THE THIRD DEFECT. V1 sampled its whole slice in a burst at the top of
# each 300s bucket: 40 requests at 0.4s spacing is ~16s at 2.5 req/s on
# top of every other loop in the process. Ten of V1's first fourteen
# reads came back 429. V2 spreads the same bucket's reads across the
# five 60s ticks inside it, which is the same markets at a fifth of the
# instantaneous rate.
#
# V1's ROWS ARE KEPT. They are honest observations of the states they
# saw, they carry their own universe_version and rule_sha, and deleting
# them would be a retrospective edit of collected data. They are simply
# not a sample of the universe, and any analysis must filter to V2.
#
# NO OUTCOME WAS SEEN BEFORE THIS CHANGE. At the moment V2 was written
# every maturation count was 0 and no settlement existed; the status
# query carries no settlement-minus-quote term by construction. The
# version bump is required by §4 regardless -- the rule changed, so the
# version changes -- but it is worth recording that the change was
# forced by a coverage defect visible in the first two buckets and not
# by anything about what the data said.
SUPERSEDED = {
    "BETTOR_UNSELECTED_STATE_V1": {
        "ranFrom": "2026-09-20T19:23:35Z",
        "supersededAt": "2026-09-20T19:31Z",
        "why": ("ROTATION_DID_NOT_COVER_THE_UNIVERSE: sized for ~9,700 "
                "eligible legs, measured 47,078. The per-cycle cap bound "
                "on every pass and the stable ordering drew the same "
                "markets forever"),
        "alsoFixed": ("ONE_BOOK_READ_PER_LEG_NOT_PER_MARKET and "
                      "WHOLE_SLICE_READ_AS_ONE_BURST"),
        "rowsRetained": True,
        "rowsAreASampleOfTheUniverse": False,
        "outcomesSeenBeforeTheChange": False,
    },
}

# Rotation: SLICES buckets of markets, one bucket per cadence tick.
# A full pass over the universe takes SLICES * SAMPLING_CADENCE_S. All
# three numbers are part of the frozen rule because each changes which
# markets are observed when.
#
# NOT COMMENSURATE WITH A DAY. A period that divides or equals 24h would
# sample every market at the SAME TIME OF DAY forever -- a market drawn
# at 03:00 UTC would never be seen pregame, and time of day tracks
# kickoff times, kickoff times track liquidity, and liquidity tracks the
# economics being measured. That is a selection reached by arithmetic
# rather than by intent, and exactly the kind this frame exists to
# avoid. 787 is prime and the period is 236,100s = 65.58h, which is not
# a whole number of days, so sampling times precess through the clock.
#
# SIZED ON THE MEASURED UNIVERSE, NOT AN ASSUMED ONE. 23,539 eligible
# MARKETS (47,078 legs, two per market, one book between them) over 787
# slices is ~30 per slice against a cap of 40 -- headroom for venue
# growth before the cap binds again. THE CAP MUST NOT BIND ROUTINELY:
# when it does, the stable within-slice ordering means the same markets
# are drawn every pass and the rest are never sampled at all. That is a
# fixed panel wearing a rotation's clothes, and it is what V1 was.
ROTATION_SLICES = 787
SAMPLING_CADENCE_S = 300
MAX_MARKETS_PER_CYCLE = 40

# The bucket's reads are spread across the ticks inside it rather than
# fired as one burst. Same markets, same bucket, a fifth of the
# instantaneous rate -- V1 put 40 requests through a shared gateway in
# ~16s and ten of its first fourteen came back 429.
TICKS_PER_BUCKET = 5
MAX_MARKETS_PER_TICK = -(-MAX_MARKETS_PER_CYCLE // TICKS_PER_BUCKET)

FULL_ROTATION_S = ROTATION_SLICES * SAMPLING_CADENCE_S
SECONDS_PER_DAY = 86_400

# The measurement that sized the above. Recorded so a later reader can
# see what the rule was sized against and re-check it rather than
# inheriting the assumption that sank V1.
MEASURED_UNIVERSE = {
    "measuredAt": "2026-09-20T19:26Z",
    "ELIGIBLE_LEGS": 47_078,
    "ELIGIBLE_MARKETS": 23_539,
    "ELIGIBLE_EVENTS": 1_493,
    "source": "research/bettor_unselected_sizing.sql",
    "note": ("two legs per market share one book, so the rotation is "
             "sized on MARKETS. Events are what the pre-registration's "
             "gate counts, because rows within an event are not "
             "independent"),
}

# Follow-up mid horizons. Declared HERE, before row 1, so the set of
# horizons cannot be chosen later on the basis of which one looked
# interesting.
HORIZONS_OBSERVABLE_S = (60, 300, 900, 3600)
HORIZONS_NOT_OBSERVABLE_S = (5, 15, 30)
HORIZON_NOT_OBSERVABLE_REASON = (
    "NOT_OBSERVABLE_AT_THIS_CADENCE: the capture loop ticks at 60s and "
    "the venue is paced deliberately, so no read exists between T0 and "
    "T0+60s. A horizon with no read behind it would be an interpolation "
    "presented as an observation")

# Tolerance around a horizon. A read that lands outside it is recorded
# with its ACTUAL lag and never relabelled as the nominal horizon.
#
# WIDENED 2026-09-20 20:45Z after measurement. At 30s, only 7 of 205
# observations past their 60s horizon ever got a 60s mid (3.4%) and 14
# of 194 got a 300s one (7.2%): the due-window closed while the tick was
# busy or rate-limited, and the observation was then skipped forever.
# A wider window does not make a late read pretend to be an on-time one
# -- ACTUAL_LAG_S and WITHIN_TOLERANCE still carry the truth, and
# WITHIN_TOLERANCE is now judged against the TIGHT figure so the
# analysis can still select on-time reads only.
HORIZON_TOLERANCE_S = 30
HORIZON_DUE_WINDOW_S = 600

# ── WHEN AN OBSERVATION BECOMES SELECTABLE. A SAMPLING CHANGE. ──────
#
# Eligibility opened at exactly T0 + horizon while the ON_TIME band
# closes at T0 + horizon + 30, so the scheduler could only ever pick an
# observation up inside a 30-SECOND window -- and ticks are 65 to 82
# seconds apart. The first eligible tick therefore landed past the band
# more often than not, which is why W1 and W2 returned late recoveries
# at horizons that were being served.
#
# Opening eligibility 30s EARLY makes the selectable window
# [T0+h-30, T0+h+30]: the full tolerance band, and nothing wider.
#
# THIS IS A VERSIONED SAMPLING CHANGE, NOT A CHANGE TO THE SCIENCE.
# HORIZON_TOLERANCE_S is untouched. A read at lag 870 for the 900s
# horizon was ALWAYS on time by the unchanged rule |lag - h| <= 30; the
# only thing that changed is that the scheduler is now permitted to
# issue it. No read becomes on-time that was not on-time before, and
# timing_class() is not consulted differently.
HORIZON_EARLY_ELIGIBILITY_S = 30
ELIGIBILITY_VERSION = "BETTOR_ELIGIBILITY_V3_EARLY_30"

# ── AND THE MEASUREMENT THAT JUSTIFIES IT, NOT AN ARGUMENT ──────────
#
# research/bettor_ontime_opportunity.sql, run 176 on ecb0928, against
# the W1 cohort. For each observation and horizon: did a tick actually
# occur inside the selectable window?
#
#   horizon  band elapsed  had a tick in band  ...with follow-up budget
#      60          66            66 (100%)              31
#     300          66             2 (3.0%)               1
#     900          66             9 (13.6%)              8
#    3600          18             0 (0%)                 0
#
# I HAD ESTIMATED 30/72 = 42% BY A PHASE ARGUMENT. Measured, 300s was
# 3%. The estimate was wrong because it assumed T0 is uniformly
# distributed against tick boundaries, and it is not: THE SAME TICK
# LOOP THAT WRITES AN OBSERVATION RUNS THE FOLLOW-UP PASS, so T0 sits a
# measured 3.7s median after its own tick and every later tick is
# ~71.4s further on. The achievable lags are therefore QUANTISED to
# k*71.4 - 3.7, and a horizon is reachable only if some integer k lands
# in its window:
#
#     h=60    k in [0.89, 1.31]   -> k=1     reachable
#     h=300   k in [4.25, 4.67]   -> none    UNREACHABLE
#     h=900   k in [12.66, 13.08] -> k=13    reachable
#     h=3600  k in [50.47, 50.89] -> none    UNREACHABLE
#
# which reproduces the measured 100% / 3% / 13.6% / 0% exactly. The 3%
# and the handful at 900s are tick-spacing JITTER, not phase luck.
#
# WIDENING THE SELECTABLE WINDOW TO THE FULL TOLERANCE BAND changes
# h=300 to k in [3.83, 4.67], which contains k=4. That is the whole
# mechanism of the eligibility change, and it is why the change is
# necessary rather than merely tidy.
#
# IT DOES NOT FIX 3600s: k in [50.05, 50.89] still contains no integer
# at the median spacing. That horizon depends on jitter (measured
# spacing ranges 21.6s to 82.5s) and no allocation rule can supply it.
# THE STRUCTURAL FIX WOULD BE TO DECOUPLE T0 FROM TICK BOUNDARIES so
# lags stop being quantised. That is a separate change to the sampling
# rule, it is NOT taken here, and it must not be made silently.
MEASURED_ONTIME_OPPORTUNITY = {
    "source": "research/bettor_ontime_opportunity.sql",
    "run": "research-sql 176 on ecb0928, 2026-09-20T22:39:05Z",
    "cohort": "W1, 66 observations",
    "bandElapsed": {60: 66, 300: 66, 900: 66, 3600: 18},
    "hadATickInBand": {60: 66, 300: 2, 900: 9, 3600: 0},
    "andHadFollowUpBudget": {60: 31, 300: 1, 900: 8, 3600: 0},
    "tickSpacingP50S": 71.4,
    "tickSpacingMinS": 21.6,
    "tickSpacingMaxS": 82.5,
    "obsOffsetFromItsTickP50S": 3.7,
    "aTickInBandIsNotAReadInBand": (
        "HAD_A_TICK_IN_BAND counts scheduling CHANCES. Whether a "
        "request also completed inside the band is a separate and "
        "smaller number, and the follow-up budget column separates a "
        "third loss again. At 60s, 66 observations had a tick in band, "
        "31 had one with budget, and W1 recorded zero on-time reads"),
    "phaseApproximationWithdrawn": (
        "30/72 = 42% assumed T0 independent of tick boundaries. "
        "Measured 3% at 300s. The estimate is withdrawn; the quantised "
        "model above reproduces all four measured figures"),
}

EARLY_ELIGIBILITY_IS_NOT_A_WIDER_TOLERANCE = (
    "HORIZON_EARLY_ELIGIBILITY_S moves when the SCHEDULER may select an "
    "observation. HORIZON_TOLERANCE_S decides what counts as ON_TIME "
    "and is unchanged at 30. Selecting early can only produce reads "
    "that the original tolerance already admitted; it cannot reclassify "
    "a late read, and no analysis gate is relaxed by it")

# Selection may open early; it may NEVER open earlier than the
# tolerance band itself, which would admit reads the science rejects.
assert HORIZON_EARLY_ELIGIBILITY_S <= HORIZON_TOLERANCE_S

# ── LATE IS NOT ON TIME, AND IS NEVER COUNTED AS IT ─────────────────
#
# The wider due window buys RECOVERY, not validity. A read that lands
# 400s after T0 is evidence about t+400s; it is not a 60-second
# outcome, and nothing may present it as one.
#
# Three classes, and only the first is admissible to a horizon's gate.
TIMING_ON_TIME = "ON_TIME"
TIMING_LATE_RECOVERY = "LATE_RECOVERY"
TIMING_NOT_OBSERVABLE = "NOT_OBSERVABLE"
TIMING_CLASSES = (TIMING_ON_TIME, TIMING_LATE_RECOVERY,
                  TIMING_NOT_OBSERVABLE)

ADMISSIBLE_TO_HORIZON_GATE = (TIMING_ON_TIME,)

LATE_IS_NOT_ON_TIME = (
    "a read inside the recovery window but outside the original "
    "tolerance is LATE_RECOVERY. Its true elapsed time is preserved in "
    "ACTUAL_LAG_S, it is kept because a late observation is still an "
    "observation of the market at that later instant, and it is NEVER "
    "counted toward the horizon it was scheduled for. Only ON_TIME "
    "reads are admissible to a horizon's gate")


def timing_class(horizon_s: int, lag_s) -> str:
    """Which class a read falls in. The ONLY place the rule lives."""
    if horizon_s in HORIZONS_NOT_OBSERVABLE_S:
        return TIMING_NOT_OBSERVABLE
    try:
        lag = float(lag_s)
    except (TypeError, ValueError):
        return TIMING_NOT_OBSERVABLE
    return (TIMING_ON_TIME
            if abs(lag - horizon_s) <= HORIZON_TOLERANCE_S
            else TIMING_LATE_RECOVERY)

FROZEN_RULE = {
    "UNIVERSE_VERSION": UNIVERSE_VERSION,

    "ELIGIBILITY_RULE": (
        "a venue market is eligible iff (a) it carries a resolvable "
        "venue-native identity -- market_slug, event_slug and side_norm "
        "all present -- and (b) its premap row was refreshed within "
        "PREMAP_FRESH_S. Nothing about price, spread, depth, volume, "
        "volatility or expected profitability enters eligibility"),

    "MARKET_TYPES_INCLUDED": (
        "every market type bettor_sport_mapping resolves to a SPORT, "
        "plus every market type it leaves unresolved. An unresolved "
        "sport is a gap in the mapping, not a reason to drop the row"),

    "MARKET_TYPES_EXCLUDED": (
        "the declared non-sport categories in "
        "bettor_sport_mapping.NON_SPORT_CATEGORIES (today: election_). "
        "This is a category exclusion fixed before collection, not an "
        "economic one"),

    "SAMPLING_CADENCE": (
        "one observation bucket per market per %ds. A market is read at "
        "most once per bucket; a second read in the same bucket is "
        "discarded by primary key, so the row count measures the market "
        "and not how often the loop ran" % SAMPLING_CADENCE_S),

    "MAX_MARKETS": (
        "%d MARKETS per cycle -- markets, not legs: the venue's book "
        "endpoint takes a slug and knows nothing about sides, so the "
        "yes and no legs of one market share one read. Delivered as %d "
        "per 60s tick rather than one burst. When a slice holds more "
        "than the cap the overflow is recorded as SLICE_TRUNCATED with "
        "its count. THE CAP MUST NOT BIND ROUTINELY: a binding cap plus "
        "a stable ordering is a fixed panel, not a rotation"
        % (MAX_MARKETS_PER_CYCLE, MAX_MARKETS_PER_TICK)),

    "MARKET_SELECTION_METHOD": (
        "deterministic. slice(identifier) = "
        "int(sha256(UNIVERSE_VERSION|identifier)[:8], 16) %% %d. A "
        "market's slice depends only on its venue identifier and the "
        "frozen version string -- not on its book, its activity, its "
        "history or its outcome, none of which have been read at the "
        "moment the choice is made" % ROTATION_SLICES),

    "ROTATION_METHOD": (
        "cycle = floor(epoch_seconds / %d) %% %d; that cycle's slice is "
        "the bucket's membership, read across the %d ticks inside the "
        "bucket rather than in one burst. Every eligible market is "
        "visited once per full rotation of %ds (%.2fh) regardless of "
        "how active it is. The period is deliberately NOT commensurate "
        "with 24h (%d is prime), so sampling times precess through the "
        "clock instead of fixing each market at one hour of the day -- "
        "which would confound the market with time of day, and time of "
        "day with kickoff times and liquidity. Ordering within a slice "
        "is by a second stable hash, never by updated_at, which would "
        "select on recent venue activity; the start of that ordering "
        "advances one place per rotation so a tick abandoned to rate "
        "limiting does not drop the same tail every pass"
        % (SAMPLING_CADENCE_S, ROTATION_SLICES, TICKS_PER_BUCKET,
           FULL_ROTATION_S, FULL_ROTATION_S / 3600.0, ROTATION_SLICES)),

    "TIME_TO_EVENT_REQUIREMENTS": (
        "NONE. Time to event is RECORDED on every row and filters "
        "nothing. Imposing a window would select states by how close "
        "they are to resolution, which is a selection on the very "
        "dynamics being measured"),

    "IDENTITY_REQUIREMENTS": (
        "venue-native deterministic fields only: market_slug, "
        "event_slug, side_norm, and the venue's own sports_type and "
        "team_league carried raw beside their mapped values. No fuzzy "
        "title matching, no approximate team-name matching, no price "
        "matching"),

    "BOOK_READABILITY_REQUIREMENTS": (
        "NONE FOR INCLUSION. A market selected by the rotation writes a "
        "row whether or not its book parses; an unreadable book is "
        "stored with a named reason. Requiring a readable book would "
        "condition the dataset on liquidity"),

    "RETENTION_POLICY": (
        "append-only, never updated in place, never deleted by the "
        "capture path. Outcomes land in a separate table keyed to the "
        "observation, so no future value can overwrite the state that "
        "was recorded before it"),

    "HORIZONS_OBSERVABLE_S": list(HORIZONS_OBSERVABLE_S),
    "HORIZONS_NOT_OBSERVABLE_S": list(HORIZONS_NOT_OBSERVABLE_S),
    "HORIZON_NOT_OBSERVABLE_REASON": HORIZON_NOT_OBSERVABLE_REASON,
}

PREMAP_FRESH_S = 7200

# ── what selection may NEVER depend on (§3, verbatim) ────────────────

SELECTION_MUST_NOT_DEPEND_ON = (
    "RN1_TRADED_IT",
    "FERRARI_TRADED_IT",
    "BETTOR_LIKES_IT",
    "SPREAD_LOOKS_PROFITABLE",
    "LATER_OUTCOME_WAS_INTERESTING",
    "MAKER_ECONOMICS_LOOK_ATTRACTIVE",
)

SELECTION_INDEPENDENCE = (
    "selection is a pure function of (venue identifier, clock). It is "
    "computed before any book is read, so it cannot depend on the book; "
    "and it does not read any outcome table, so it cannot depend on "
    "what happened next")


def rule_sha() -> str:
    """A sha over the frozen rule. Stored on every row.

    A rule that changed after collection began, without a version
    bump, shows up as a second sha against the same UNIVERSE_VERSION --
    which is the condition §4 forbids, made detectable rather than
    trusted.
    """
    blob = json.dumps(FROZEN_RULE, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


RULE_SHA = rule_sha()


# ── the rotation itself ──────────────────────────────────────────────

def slice_of(identifier: str) -> int:
    """Which rotation slice a market belongs to. Stable forever."""
    raw = "%s|%s" % (UNIVERSE_VERSION, identifier)
    return int(hashlib.sha256(raw.encode()).hexdigest()[:8], 16) \
        % ROTATION_SLICES


def _order_key(identifier: str) -> str:
    """Within-slice ordering. Stable, and unrelated to activity."""
    raw = "%s|order|%s" % (UNIVERSE_VERSION, identifier)
    return hashlib.sha256(raw.encode()).hexdigest()


def cycle_of(at: datetime) -> int:
    return int(at.timestamp() // SAMPLING_CADENCE_S) % ROTATION_SLICES


def bucket_of(at: datetime) -> datetime:
    """The observation bucket. One row per market per bucket."""
    epoch = int(at.timestamp() // SAMPLING_CADENCE_S) * SAMPLING_CADENCE_S
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def tick_index(at: datetime) -> int:
    """Which tick within the current bucket this is, 0..TICKS-1."""
    into = int(at.timestamp()) % SAMPLING_CADENCE_S
    per = SAMPLING_CADENCE_S // TICKS_PER_BUCKET
    return min(TICKS_PER_BUCKET - 1, into // per)


def select(candidates: list, *, at: datetime,
           max_markets: int = MAX_MARKETS_PER_CYCLE,
           tick: int | None = None) -> dict:
    """The sampling rule, applied. NOTHING HERE READS A BOOK.

    `candidates` are eligible markets from the premap, already deduped
    to one entry per market. The return carries the chosen rows AND the
    truncation state, because a silent cap is a selection nobody can
    see afterwards -- V1's cap bound on every pass and every row said so.

    With `tick`, only that tick's share of the bucket's markets is
    returned. The bucket's membership does not change; only which of
    its markets this particular 60s tick reads.
    """
    cycle = cycle_of(at)
    in_slice = [c for c in candidates
                if slice_of(c["identifier"]) == cycle]
    in_slice.sort(key=lambda c: _order_key(c["identifier"]))

    # THE ROTATING OFFSET. A read that dies to rate limiting abandons
    # the rest of its tick, so a FIXED starting point would drop the
    # same tail of the ordering on every pass -- a small permanent
    # exclusion, uncorrelated with economics but an exclusion all the
    # same. Advancing the start by the rotation count spreads which
    # markets lose out. It depends only on the clock.
    if in_slice:
        rot = int(at.timestamp() // FULL_ROTATION_S) % len(in_slice)
        in_slice = in_slice[rot:] + in_slice[:rot]

    bucket_share = in_slice[:max_markets]
    truncated_by = max(0, len(in_slice) - max_markets)
    if tick is None:
        chosen = bucket_share
    else:
        per = -(-max_markets // TICKS_PER_BUCKET)
        chosen = bucket_share[tick * per:(tick + 1) * per]

    return {
        "UNIVERSE_VERSION": UNIVERSE_VERSION,
        "RULE_SHA": RULE_SHA,
        "CYCLE": cycle,
        "TICK": tick,
        "ROTATION_SLICES": ROTATION_SLICES,
        "CANDIDATES_ELIGIBLE": len(candidates),
        "CANDIDATES_IN_SLICE": len(in_slice),
        "SELECTED": chosen,
        "BUCKET_SHARE": len(bucket_share),
        "SLICE_TRUNCATED": truncated_by > 0,
        "SLICE_TRUNCATED_BY": truncated_by,
        "selectionIndependence": SELECTION_INDEPENDENCE,
    }


# ── §5. THE COMPLETE MARKET STATE ────────────────────────────────────

STATE_VERSION = "BETTOR_STATE_OBSERVATION_V1"

# Reasons a field is absent. A field is absent for a NAMED reason or it
# is present; it is never zero, and never quietly missing.
R_NO_BOOK = "BOOK_UNREADABLE"
# A book that PARSED but has no two-sided market has no mid, and that
# is a fact about the market, not about our read. Production rows
# marked READABLE were carrying BOOK_UNREADABLE in their reasons
# because the mid-derived features reused R_NO_BOOK for "no mid".
# Two different absences must not share one reason code: one says the
# venue refused us, the other says nobody is quoting both sides.
R_NO_MID = "NO_TWO_SIDED_MARKET_SO_NO_MID"
R_NO_LADDER = "NO_LADDER_PUBLISHED"
R_NO_HISTORY = "NO_PRIOR_OBSERVATION_OF_THIS_MARKET_IN_THIS_DATASET"
R_NO_GAME_START = "VENUE_PUBLISHED_NO_GAME_START"
R_SIDE_ABSENT = "VENUE_PUBLISHED_ONE_SIDE_ONLY"
R_SIBLING_NOT_READ = "COMPLEMENT_INSTRUMENT_NOT_READ_THIS_CYCLE"

MISSING_REASONS = (R_NO_BOOK, R_NO_MID, R_NO_LADDER, R_NO_HISTORY,
                   R_NO_GAME_START, R_SIDE_ABSENT, R_SIBLING_NOT_READ)

NEVER_MANUFACTURED = (
    "a value that was not observed is NOT_IDENTIFIED with a reason from "
    "MISSING_REASONS. It is never 0, never the other side's value, and "
    "never carried forward from a previous read")

# ── WHAT THE FRAME IS, STATED NARROWLY ──────────────────────────────
#
# AN EARLIER VERSION OF THIS MODULE SAID "the sampling frame is
# unbiased". THAT WAS TOO STRONG and is withdrawn.
#
# What is supported: the SCHEDULE is selection-independent. Which
# markets a cycle picks is a pure function of the venue identifier and
# the clock, computed before any book is read.
#
# What is NOT supported: that the ROWS ON DISK are an unbiased sample of
# the universe. Three things sit between the schedule and the rows, and
# each one drops markets:
#
#   1. RATE LIMITING. Measured 20:31Z on V2: 66 of 187 attempts refused
#      (35.3%), 94 readable (50.3%), 27 otherwise unreadable (14.4%).
#      A refused attempt still writes a row, so it is visible.
#   2. BUDGET SHRINK. A refused tick halves its own read budget, so the
#      tail of that tick's share is never attempted. Those markets
#      write NO ROW AT ALL and are invisible on disk -- they can only
#      be inferred from the gap between what the rule would have picked
#      and what landed.
#   3. ABANDONMENT. A tick that hits MISS_ABANDON stops early, same
#      effect.
#
# So the honest statement is: THE SCHEDULE IS SELECTION-INDEPENDENT;
# THE REALISED SAMPLE IS THE SCHEDULE MINUS LOSSES THAT ARE NOT YET
# CHARACTERISED. The rotating within-slice cursor spreads (3) across
# passes so the same tail is not always dropped, but "spread out" is
# not "absent", and none of this is established until coverage is
# measured against the schedule rather than against itself.
FRAME_CLAIM = (
    "THE SCHEDULE is selection-independent: which markets a cycle picks "
    "is a pure function of venue identifier and clock, computed before "
    "any book is read. THE REALISED SAMPLE is that schedule minus reads "
    "lost to rate limiting, budget shrink and tick abandonment. Losses "
    "of the first kind write a row and are visible; losses of the "
    "second and third kinds write nothing and are invisible on disk. "
    "The realised sample is NOT yet established to be an unbiased "
    "sample of the universe, and must not be described as one")

# ── GATEWAY CONSUMERS, SEPARATED BY HOST AND BY QUOTA ──────────────
#
# TWO EARLIER ACCOUNTS OF THIS WERE WRONG, IN THE SAME WAY.
#
# First I wrote that refusals come from "the live mirror, busiest when
# games are live". mirror_live = false made that suspect, and the logs
# refuted it: the copy lane runs its full evaluation and refuses only at
# the submit step ("LIVE refused: homerunhazard not funded"), so the
# pause gates SUBMISSION, not reads.
#
# Then I listed the busy hosts from the log and called them all gateway
# consumers. THAT WAS THE SAME ERROR AGAIN -- pooling things that share
# a log file but not a quota. Activity on another domain does not
# consume the PMUS gateway's budget.
#
# SEPARATED, from the worker log of 2026-09-20T20:32Z:
#
#   gateway.polymarket.us      THE ONLY HOST OBSERVED RETURNING 429.
#                              /v1/markets/{slug}/book  <- this capture
#                              /v1/markets/{slug}/bbo   <- mirror_shadow,
#                                shadow_bettor, shadow_rn1, price_path,
#                                shadow_experimental, institutional_md
#                              Both refused in the same second for the
#                              same market, so the limit is shared
#                              across path families on this host.
#
#   api.polymarket.us          PMUS, same domain, different path family
#                              (/portfolio/positions, /orders/open,
#                              /portfolio/activities, /order/{id}).
#                              ALL 200 in the observed window. WHETHER
#                              IT SHARES THE GATEWAY QUOTA IS NOT
#                              ESTABLISHED by these logs and must not be
#                              assumed either way.
#
#   data-api.polymarket.com    POLYMARKET GLOBAL. A different venue.
#   clob.polymarket.com        Consumes NO PMUS quota. The dense
#   gamma-api.polymarket.com   /trades polling across watched wallets
#                              lives here, not on the PMUS gateway.
#
#   polygon-mainnet.g.alchemy  Chain RPC. Unrelated to either venue.
#
# WHAT SURVIVES. The observed PMUS-gateway consumers are the book and
# bbo readers -- seven loops including this one. What drives their
# aggregate rate, and whether that rate tracks the sporting day, is NOT
# established. The measurement (bettor_unselected_readability.sql) is
# the only evidence, and it runs OPPOSITE to the live-hours story:
# LIVE 32.8% of 122 refused, PREGAME 43.8% of 73.
GATEWAY_CONSUMERS = {
    "gateway.polymarket.us": {
        "quota": "THE OBSERVED 429 SOURCE",
        "consumers": ["bettor_state (book)", "mirror_shadow (bbo)",
                      "shadow_bettor (bbo)", "shadow_rn1 (bbo)",
                      "price_path (bbo)", "shadow_experimental (bbo)",
                      "institutional_md"],
    },
    "api.polymarket.us": {
        "quota": "SHARED_WITH_GATEWAY_NOT_ESTABLISHED",
        "consumers": ["copy lane portfolio/orders reconciliation"],
        "observed": "all 200 in the 20:32Z window",
    },
    "data-api.polymarket.com": {
        "quota": "DIFFERENT_VENUE_NO_PMUS_QUOTA",
        "consumers": ["watched-wallet trade polling"],
    },
    "clob.polymarket.com": {"quota": "DIFFERENT_VENUE_NO_PMUS_QUOTA"},
    "gamma-api.polymarket.com": {"quota": "DIFFERENT_VENUE_NO_PMUS_QUOTA"},
    "polygon-mainnet.g.alchemy.com": {"quota": "CHAIN_RPC_UNRELATED"},
}

MIRROR_PAUSE_DOES_NOT_STOP_READS = (
    "mirror_live = false gates ORDER SUBMISSION, not market-data reads. "
    "The copy lane continues to read books, classify exits and compute "
    "conviction, refusing only at the submit step. Its read traffic is "
    "unchanged by the pause")

READABILITY_IS_NOT_MISSING_AT_RANDOM = (
    "a refused read writes a row, so refusals are VISIBLE -- but the "
    "READABLE SUBSET is still not established to be missing at random. "
    "Refusals come from gateway.polymarket.us, whose observed "
    "consumers are the seven book/bbo readers -- NOT from the other "
    "hosts in the same log, which belong to a different venue or to "
    "the chain and consume no PMUS quota. What drives the aggregate "
    "rate is not established, and the measurement runs OPPOSITE to the "
    "live-hours hypothesis: LIVE 32.8% of 122 refused against PREGAME "
    "43.8% of 73. SIMILAR REFUSAL "
    "RATES ACROSS OBSERVED CATEGORIES WOULD NOT PROVE RANDOMNESS -- "
    "they would fail to detect a difference on the categories looked "
    "at, which is weaker. The limitation stands on any readable-only "
    "estimate regardless of what the rates show")

LIVE = "LIVE"
PREGAME = "PREGAME"
LIVE_STATUS_UNKNOWN = "NOT_IDENTIFIED"


def _d(v):
    if v is None or v == NOT_IDENTIFIED:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _s(v):
    return NOT_IDENTIFIED if v is None else str(v)


def _time_to_event(observed_at, game_start):
    if game_start is None:
        return NOT_IDENTIFIED, LIVE_STATUS_UNKNOWN, R_NO_GAME_START
    if isinstance(game_start, str):
        try:
            game_start = datetime.fromisoformat(
                game_start.replace("Z", "+00:00"))
        except ValueError:
            return NOT_IDENTIFIED, LIVE_STATUS_UNKNOWN, R_NO_GAME_START
    if game_start.tzinfo is None:
        game_start = game_start.replace(tzinfo=timezone.utc)
    secs = (game_start - observed_at).total_seconds()
    return str(int(secs)), (PREGAME if secs > 0 else LIVE), None


def _imbalance(snap):
    """Displayed book imbalance, or NOT_IDENTIFIED with a reason.

    (bid - ask) / (bid + ask) over the captured ladders. This is a
    property of DISPLAYED depth and inherits every caveat displayed
    depth carries -- it is not a queue, and it is not what a resting
    order would meet.
    """
    dd = (snap or {}).get("DISPLAYED_DEPTH_AT_T0") or {}
    b, a = _d(dd.get("bid")), _d(dd.get("ask"))
    if b is None or a is None:
        return NOT_IDENTIFIED, R_NO_LADDER
    total = b + a
    if total == 0:
        return NOT_IDENTIFIED, R_NO_LADDER
    return str((b - a) / total), None


def _move(mid, history):
    """Recent price move from THIS dataset's own prior observations.

    Returns NOT_IDENTIFIED when there is no history. A market seen for
    the first time has no recent move; reporting 0 would say it did not
    move, which is a different and false statement.
    """
    m = _d(mid)
    if m is None:
        return NOT_IDENTIFIED, NOT_IDENTIFIED, R_NO_MID
    prior = [(_d(h.get("mid")), h.get("observedAt")) for h in (history or [])]
    prior = [(v, t) for v, t in prior if v is not None]
    if not prior:
        return NOT_IDENTIFIED, NOT_IDENTIFIED, R_NO_HISTORY
    last = prior[0][0]
    move = m - last
    vals = [v for v, _ in prior] + [m]
    mean = sum(vals) / len(vals)
    if len(vals) < 3:
        return str(move), NOT_IDENTIFIED, R_NO_HISTORY
    var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
    return str(move), str(var.sqrt() if hasattr(var, "sqrt")
                          else Decimal(str(float(var) ** 0.5))), None


def observation_id(symbol, leg, bucket) -> str:
    raw = "|".join([UNIVERSE_VERSION, str(symbol), str(leg),
                    bucket.isoformat()])
    return "bsv_" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def state_record(subject: dict, *, observed_at: datetime,
                 book: dict | None = None, received_at=None,
                 request_at=None, feed=None, read_error=None,
                 venue_state=None, history: list | None = None,
                 complement: dict | None = None,
                 selection: dict | None = None) -> dict:
    """One row of §5. Every declared field appears, absences named.

    `book` is the venue's marketData object verbatim, or None. A None
    book does NOT skip the row -- see the module docstring.
    """
    bucket = bucket_of(observed_at)
    snap = booksnap.snapshot(book, symbol=subject.get("symbol"),
                             captured_at=observed_at,
                             receipt_at=received_at,
                             request_at=request_at, feed=feed)
    readable = snap.get("PARSE_STATUS") == booksnap.PARSE_OK
    missing = []
    if not readable:
        missing.append(R_NO_BOOK)

    bid, ask = _d(snap.get("BEST_BID")), _d(snap.get("BEST_ASK"))
    if readable and (bid is None or ask is None):
        missing.append(R_SIDE_ABSENT)
    spread = ask - bid if bid is not None and ask is not None else None
    mid = (ask + bid) / 2 if spread is not None else None

    tte, live, tte_why = _time_to_event(observed_at,
                                        subject.get("gameStart"))
    if tte_why:
        missing.append(tte_why)
    imb, imb_why = _imbalance(snap if readable else None)
    if imb_why and imb_why not in missing:
        missing.append(imb_why)
    move, vol, move_why = _move(mid, history)
    if move_why and move_why not in missing:
        missing.append(move_why)

    # THE COMPLEMENT LEG. The venue publishes one long contract per
    # slug; the NO leg's executable book belongs to sibling
    # instruments and is NOT this read. It is carried only when it was
    # separately read this cycle, and is otherwise absent by name --
    # never derived as 1 - YES, which would assert a no-arbitrage
    # identity the taker-pair measurement has already refuted (0 of
    # 3,732 observed pairs traded at or below par).
    if complement is None:
        no_bid = no_ask = no_depth = NOT_IDENTIFIED
        if R_SIBLING_NOT_READ not in missing:
            missing.append(R_SIBLING_NOT_READ)
    else:
        no_bid = _s(complement.get("bid"))
        no_ask = _s(complement.get("ask"))
        no_depth = complement.get("depth", NOT_IDENTIFIED)

    sel = selection or {}
    return {
        "stateVersion": STATE_VERSION,
        "OBSERVATION_ID": observation_id(subject.get("symbol"),
                                         subject.get("outcomeLeg"), bucket),
        "OBSERVED_AT": observed_at,
        "OBSERVATION_BUCKET": bucket,

        # ── identity, venue-native only ──────────────────────────────
        "EVENT_ID": subject.get("eventId") or NOT_IDENTIFIED,
        "MARKET_ID": subject.get("marketId") or NOT_IDENTIFIED,
        # The venue's LEG instrument, not the rotation key. The rotation
        # hashes the market slug (one book per market), so `identifier`
        # is that slug; the instrument this row's leg refers to travels
        # separately and is not overwritten by it.
        "INSTRUMENT_ID": (subject.get("legIdentifier")
                          or subject.get("identifier") or NOT_IDENTIFIED),
        "CONDITION_ID": subject.get("conditionId") or NOT_IDENTIFIED,
        "OUTCOME_LEG": subject.get("outcomeLeg") or NOT_IDENTIFIED,
        "SPORT": subject.get("sport") or NOT_IDENTIFIED,
        "LEAGUE": subject.get("league") or NOT_IDENTIFIED,
        "MARKET_TYPE": subject.get("kind") or NOT_IDENTIFIED,
        "SPORT_SOURCE_RAW": subject.get("sportSourceRaw"),
        "LEAGUE_SOURCE_RAW": subject.get("leagueSourceRaw"),
        "IDENTITY_STATUS": ("VENUE_NATIVE_RESOLVED"
                            if subject.get("marketId")
                            and subject.get("eventId")
                            else "VENUE_NATIVE_INCOMPLETE"),

        # ── timing ───────────────────────────────────────────────────
        "TIME_TO_EVENT_S": tte,
        "LIVE_STATUS": live,
        "BOOK_SOURCE_TIMESTAMP": snap.get("TRANSACT_TIME", NOT_IDENTIFIED),
        "BOOK_RECEIVED_TIMESTAMP": received_at or observed_at,
        "BOOK_AGE_S": _book_age(snap, received_at or observed_at),

        # ── the book ─────────────────────────────────────────────────
        "YES_BID": _s(bid),
        "YES_ASK": _s(ask),
        "YES_DEPTH": (snap.get("DISPLAYED_DEPTH_AT_T0") if readable
                      else NOT_IDENTIFIED),
        "NO_BID": no_bid,
        "NO_ASK": no_ask,
        "NO_DEPTH": no_depth,
        "SPREAD": _s(spread),
        "MID": _s(mid),
        "MULTI_LEVEL_DEPTH": ({"bid": snap.get("BID_LADDER"),
                               "ask": snap.get("ASK_LADDER"),
                               "levels": booksnap.LADDER_LEVELS}
                              if readable else NOT_IDENTIFIED),
        "BOOK_IMBALANCE": imb,
        "RECENT_PRICE_MOVE": move,
        "REALISED_VOLATILITY": vol,
        "STATS_SHARES_TRADED": snap.get("STATS_SHARES_TRADED",
                                        NOT_IDENTIFIED),

        # ── venue and readability ────────────────────────────────────
        "VENUE_STATE": (venue_state or snap.get("VENUE_STATE")
                        or NOT_IDENTIFIED),
        "BOOK_READABILITY_STATUS": (
            "READABLE" if readable
            else "UNREADABLE:%s" % (read_error
                                    or snap.get("PARSE_STATUS")
                                    or NOT_IDENTIFIED)),
        "MISSING_FIELD_REASONS": missing + list(
            snap.get("MISSING_FIELD_REASONS") or []),
        "missingReasonsDeclared": list(MISSING_REASONS),
        "neverManufactured": NEVER_MANUFACTURED,
        "readabilityIsNotMissingAtRandom":
            READABILITY_IS_NOT_MISSING_AT_RANDOM,

        # ── provenance and the frozen rule ───────────────────────────
        "UNIVERSE_VERSION": UNIVERSE_VERSION,
        "RULE_SHA": RULE_SHA,
        "SELECTION_CYCLE": sel.get("CYCLE", NOT_IDENTIFIED),
        "SLICE_TRUNCATED": sel.get("SLICE_TRUNCATED", False),
        "SLICE_TRUNCATED_BY": sel.get("SLICE_TRUNCATED_BY", 0),
        "SELECTION_METHOD": FROZEN_RULE["MARKET_SELECTION_METHOD"],
        "RAW_SOURCE": snap.get("RAW_SOURCE"),

        # ── what this row is NOT ─────────────────────────────────────
        "FILL_STATUS": "NO_ORDER_EXISTS",
        "measures": OBJECT_A,
        "doesNotMeasure": [OBJECT_B, OBJECT_C, OBJECT_D],
        "neverSubstitute": NEVER_SUBSTITUTE,
    }


def _book_age(snap, received_at):
    ts = snap.get("TRANSACT_TIME")
    if not ts or ts == NOT_IDENTIFIED:
        return NOT_IDENTIFIED
    try:
        src = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return NOT_IDENTIFIED
    if src.tzinfo is None:
        src = src.replace(tzinfo=timezone.utc)
    return str(round((received_at - src).total_seconds(), 3))


# ── §6. FUTURE OUTCOMES, APPENDED SEPARATELY ─────────────────────────

OUTCOME_VERSION = "BETTOR_STATE_OUTCOME_V1"

A_FUTURE_READ_IS_NOT_A_FILL = (
    "a later mid, a later trade, a later settlement -- none of them is "
    "our fill. This dataset contains no order, so it contains no fill, "
    "and no column here may be read as one")

SETTLEMENT_RESOLVED = "RESOLVED"
SETTLEMENT_PENDING = "PENDING"
SETTLEMENT_SEMANTICS_UNVERIFIED = "SEMANTICS_NOT_VERIFIED"


def horizon_due(observed_at: datetime, horizon_s: int) -> datetime:
    return observed_at + timedelta(seconds=horizon_s)


def mid_observation(observation_id_: str, *, horizon_s: int,
                    observed_at: datetime, read_at: datetime,
                    mid) -> dict:
    """One appended future mid. THE ACTUAL LAG IS THE RECORD.

    A read that lands 74s after T0 is stored as a 60s-horizon row whose
    ACTUAL_LAG_S is 74. It is never relabelled as 60, and never
    interpolated toward it.
    """
    if horizon_s in HORIZONS_NOT_OBSERVABLE_S:
        return {"OBSERVATION_ID": observation_id_,
                "HORIZON_S": horizon_s,
                "MID": NOT_IDENTIFIED,
                "STATUS": HORIZON_NOT_OBSERVABLE_REASON,
                "TIMING_CLASS": TIMING_NOT_OBSERVABLE,
                "ADMISSIBLE_TO_HORIZON_GATE": False,
                "isNotAFill": A_FUTURE_READ_IS_NOT_A_FILL}
    lag = (read_at - observed_at).total_seconds()
    # WITHIN_TOLERANCE stays judged against the TIGHT tolerance even
    # though the due window is wider. Widening the window buys recovery;
    # it must not quietly widen what counts as on time.
    cls = timing_class(horizon_s, lag)
    return {
        "outcomeVersion": OUTCOME_VERSION,
        "OBSERVATION_ID": observation_id_,
        "HORIZON_S": horizon_s,
        "READ_AT": read_at,
        "ACTUAL_LAG_S": str(round(lag, 3)),
        "WITHIN_TOLERANCE": cls == TIMING_ON_TIME,
        "TIMING_CLASS": cls,
        "ADMISSIBLE_TO_HORIZON_GATE": cls in ADMISSIBLE_TO_HORIZON_GATE,
        "MID": _s(_d(mid)),
        "STATUS": "OBSERVED" if _d(mid) is not None else NOT_IDENTIFIED,
        "isNotAFill": A_FUTURE_READ_IS_NOT_A_FILL,
        "lateIsNotOnTime": LATE_IS_NOT_ON_TIME,
    }


def settlement_record(observation_id_: str, *, outcome=None,
                      settled_at=None,
                      semantics_status=SETTLEMENT_SEMANTICS_UNVERIFIED
                      ) -> dict:
    """The settlement leg of §6.

    SETTLEMENT_SEMANTICS_STATUS is its own column because "did this
    slug settle at 1 for the side we quoted" is a venue convention, not
    an arithmetic fact, and a dataset that assumes it would carry a
    sign error nobody could see.
    """
    return {
        "outcomeVersion": OUTCOME_VERSION,
        "OBSERVATION_ID": observation_id_,
        "SETTLEMENT_OUTCOME": _s(outcome),
        "SETTLEMENT_TIMESTAMP": settled_at or NOT_IDENTIFIED,
        "SETTLEMENT_STATUS": (SETTLEMENT_RESOLVED if outcome is not None
                              else SETTLEMENT_PENDING),
        "SETTLEMENT_SEMANTICS_STATUS": semantics_status,
        "isNotAFill": A_FUTURE_READ_IS_NOT_A_FILL,
    }


# ── eligibility, applied to a premap row ─────────────────────────────

def eligible(row: dict) -> tuple[bool, str | None]:
    """§4's ELIGIBILITY_RULE, as code. READS NO BOOK AND NO OUTCOME."""
    if not row.get("market_slug"):
        return False, "NO_VENUE_MARKET_SLUG"
    if not row.get("event_slug"):
        return False, "NO_VENUE_EVENT_SLUG"
    if not row.get("side_norm"):
        return False, "NO_VENUE_SIDE"
    kind = str(row.get("kind") or "")
    for prefix in sportmap.NON_SPORT_CATEGORIES:
        if kind.startswith(prefix):
            return False, "EXCLUDED_CATEGORY_%s" % prefix.rstrip("_").upper()
    return True, None


def describe() -> dict:
    """What this capture is, for COMMAND and for the record."""
    return {
        "universeVersion": UNIVERSE_VERSION,
        "ruleSha": RULE_SHA,
        "frozenRule": FROZEN_RULE,
        "readOnly": True,
        "orderPathExists": False,
        "capitalAtRisk": 0,
        "mirrorLive": False,
        "measures": OBJECT_A,
        "measuredQuantity": MEASURED_QUANTITY,
        "objects": OBJECTS,
        "forbiddenNames": dict(FORBIDDEN_NAMES),
        "neverSubstitute": NEVER_SUBSTITUTE,
        "selectionMustNotDependOn": list(SELECTION_MUST_NOT_DEPEND_ON),
        "selectionIndependence": SELECTION_INDEPENDENCE,
        "unreadableBooksAreKept": (
            "a selected market writes a row even when its book does not "
            "parse. Dropping it would condition the dataset on "
            "readability, and readability tracks liquidity"),
    }
