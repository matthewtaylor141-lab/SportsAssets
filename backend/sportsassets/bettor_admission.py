"""Admission policies, as pure functions, so they can be compared.

WHY THIS IS ITS OWN MODULE. The admission rule has now been wrong twice
in two different ways, and both times the evidence arrived only after it
was running in production against real markets. The rule is a handful of
arithmetic on numbers the tick already has; there is no reason for it to
be reachable only by deploying it.

Pulling it out makes three things possible that were not before:

  * the replay harness drives THE REAL FUNCTION, not a copy of it, which
    is the failure the harness was built for in the first place;
  * V4, V1, V2 and any candidate can be run against one workload and
    compared on equal terms;
  * V1's production failure becomes a regression fixture, so a future
    policy has to survive the scenario that broke the last one.

UNITS, AND WHY THEY ARE THE WHOLE STORY. V1 died of a dimension error:

    saturated = backlog > max(1, fu_reserve)

`backlog` counts TASKS. `fu_reserve` counts READS PER TICK. Those are a
level and a flow; comparing them directly asks "is there more than one
tick of work waiting", and since every observation owes a read at 60s,
300s, 900s and 3600s, a healthy steady state always holds more than one
tick of work. So it was true on the first tick and on every tick
production ran.

That brake is the SECOND defect. The first is plainer and was hiding
under it: `fu_reserve // horizons_per_obs` is integer division, and at
the budget production actually runs -- pacing 2.054, so four reads a
tick and a reserve of two -- it is 2 // 4 == 0. V1 admits nothing on
every tick whatever the backlog, and no state of the queue changes it.
See ADMIT_FLOOR_OBS below for the full arithmetic and for the two
earlier accounts of this that I got wrong.

Every quantity here therefore carries its unit in its name, and any
comparison is between two of the same kind. Times are compared with
times. That is not a style preference; it is the specific defect.

NOTHING HERE DECIDES WHETHER A POLICY SHIPS. The comparison is evidence;
shipping is a separate decision with its own authorization.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TickCapacity:
    """What one tick has to work with. Units in every field name."""

    total_budget_reads: int      # reads available this tick
    fu_reserve_reads: int        # of those, reserved for follow-ups
    sample_budget_reads: int     # of those, available for new samples
    backlog_tasks: int           # follow-up tasks due NOW, all horizons
    horizons_per_obs: int        # tasks each admitted observation owes
    tick_period_s: float         # seconds between ticks
    tolerance_s: float           # how late a read may be and still count

    # ── added after review found the fractional-intake defect ────────

    # FORWARD OBLIGATIONS, NOT JUST DUE ONES. backlog_tasks counts what
    # is due NOW. An observation admitted thirty seconds ago owes reads
    # at 60s, 300s, 900s and 3600s and none of them is due yet, so it
    # contributes nothing to backlog while contributing four reads of
    # future demand. Admitting against backlog alone therefore keeps
    # admitting right up until the obligations land together.
    #
    # None means the caller did not supply it. The account then says so
    # in `why` rather than silently substituting backlog_tasks, which
    # would understate demand and be the permissive choice.
    outstanding_tasks: int | None = None

    # The longest deadline any outstanding task carries. With it, total
    # outstanding work can be compared against the time available to
    # do it.
    longest_horizon_s: float = 3600.0

    # MEASURED, NOT ASSUMED. The fraction of attempted follow-up reads
    # that produce a usable observation. Failures and retries consume
    # reserve without discharging an obligation, so nominal reserve
    # overstates service by exactly this factor. Default 1.0 is the
    # optimistic value and production must pass the measured one; the
    # harness varies it deliberately.
    service_success_rate: float = 1.0

    # ── derived, and every one of these is a rate or a time ──────────

    @property
    def effective_reserve_reads(self) -> float:
        """Reserve that actually discharges obligations."""
        return self.fu_reserve_reads * max(0.0, min(1.0,
                                                    self.service_success_rate))

    @property
    def service_reads_per_s(self) -> float:
        return self.effective_reserve_reads / self.tick_period_s

    @property
    def sustainable_obs_per_s(self) -> float:
        """The stability condition, and the only number that matters
        long run: each observation creates `horizons_per_obs` tasks, so
        admitting faster than service/horizons grows the queue without
        bound and no ordering rule can rescue it."""
        return self.service_reads_per_s / max(1, self.horizons_per_obs)

    @property
    def sustainable_obs_per_tick(self) -> float:
        """USUALLY LESS THAN ONE, AND THAT IS THE POINT.

        At the recommended configuration -- 45s tick, reserve 2, four
        horizons -- this is (2/45)/4 * 45 = 0.5. Any rule that turns
        0.5 into an integer admission per tick admits twice what the
        reserve can serve. int() then max(1, ...) did exactly that.
        """
        return self.sustainable_obs_per_s * self.tick_period_s

    @property
    def drain_time_s(self) -> float:
        """How long the DUE queue takes to clear at the CURRENT service
        rate. A time, so it can be compared with a deadline."""
        if self.effective_reserve_reads <= 0:
            return float("inf")
        return (self.backlog_tasks
                / self.effective_reserve_reads) * self.tick_period_s

    @property
    def commitment_window_s(self) -> float:
        """By when every outstanding obligation must have been met."""
        return self.longest_horizon_s + self.tolerance_s

    @property
    def obligation_time_s(self) -> float | None:
        """How long ALL outstanding work would take at current service.

        None when outstanding_tasks was not supplied -- an absence the
        account reports rather than papering over.
        """
        if self.outstanding_tasks is None:
            return None
        if self.effective_reserve_reads <= 0:
            return float("inf")
        return (self.outstanding_tasks
                / self.effective_reserve_reads) * self.tick_period_s


@dataclass(frozen=True)
class Decision:
    admit_obs: int          # how many new observations this tick
    saturated: bool         # is the queue beyond its deadline
    why: str
    version: str


# ── the policies ─────────────────────────────────────────────────────

V4 = "BETTOR_ADMISSION_V0_NONE"
V1 = "BETTOR_ADMISSION_V1_CAPACITY_AWARE"
V2 = "BETTOR_ADMISSION_V2_MEASURE_ONLY"
V3 = "BETTOR_ADMISSION_V3_DRAIN_TIME"


def policy_v4(cap: TickCapacity) -> Decision:
    """No admission control. Whatever the sample budget allows.

    The measured baseline: 30.9% on time, and a queue that grows.
    """
    return Decision(cap.sample_budget_reads, False,
                    "no admission control", V4)


def policy_v1(cap: TickCapacity) -> Decision:
    """THE REGRESSION, KEPT ON PURPOSE.

    Reproduced exactly as it ran, dimension error and all, so any
    candidate can be run against the scenario that broke it. A policy
    that cannot be shown to survive this has not been tested.
    """
    sustainable = cap.fu_reserve_reads // max(1, cap.horizons_per_obs)
    saturated = cap.backlog_tasks > max(1, cap.fu_reserve_reads)
    admit = 0 if saturated else sustainable
    return Decision(min(cap.sample_budget_reads, admit), saturated,
                    "level compared against a per-tick flow", V1)


def policy_v2(cap: TickCapacity) -> Decision:
    """Measure, do not enforce. The current production baseline."""
    saturated = cap.backlog_tasks > max(1, cap.fu_reserve_reads)
    return Decision(cap.sample_budget_reads, saturated,
                    "measured only; allowance unchanged from V4", V2)


# How much of the tolerance the queue may consume before the brake
# engages. Below 1.0 so the brake acts while there is still time to act,
# rather than once the deadline has already passed.
DRAIN_HEADROOM = 0.75

# THE FLOOR. Admission may be reduced, never switched off.
#
# THIRD STATEMENT OF V1'S DEFECT, AND THIS ONE HAS THE ARITHMETIC.
#
# I first said the brake latched and could never reopen. Then the
# harness, run at an ASSUMED budget of 10 reads a tick, showed the brake
# releasing around tick 19, and I corrected myself to "a twenty-three
# minute blackout, not a latch". Both accounts were built on a budget
# production does not have.
#
# The measured pacing is 2.054, and the tick's budget is
# int(10 * 1/pacing) = 4, so fu_reserve is 2. V1's sustainable term is
#
#     fu_reserve // horizons_per_obs   ==   2 // 4   ==   0
#
# Integer division floors to zero whenever the reserve drops below the
# number of horizons, which under backoff is most of the time. At that
# budget V1 admits nothing on EVERY tick REGARDLESS OF BACKLOG -- the
# brake is not even reached, and there is no state of the queue that
# makes it admit again:
#
#     budget 10, fu 5   ->  admits 1  (brake can still zero it)
#     budget  6, fu 3   ->  admits 0  always
#     budget  4, fu 2   ->  admits 0  always     <- production
#     budget  2, fu 1   ->  admits 0  always
#
# So the effect I described first was right and the mechanism was not,
# and my correction was right only at a budget production rarely sees.
# The level-versus-flow brake is a real defect and it is the second one.
#
# THE FLOOR ANSWERS BOTH. max(1, ...) makes zero unreachable whatever
# the budget, the backlog or the divisor, so neither fault can stop the
# collector again.
ADMIT_FLOOR_OBS = 1


def policy_v3(cap: TickCapacity) -> Decision:
    """THE SECOND REGRESSION, KEPT ON PURPOSE -- like V1 above.

    NOT A CANDIDATE. It was proposed as one and it is wrong, found by
    independent review and reproduced: at the configuration the report
    itself recommended -- 45s tick, reserve 2, four horizons -- it
    admits ONE OBSERVATION EVERY TICK even with a backlog of 100,
    because

        max(ADMIT_FLOOR_OBS, int(0.5))  ==  max(1, 0)  ==  1

    which is 1.333 obs/min creating 5.333 tasks/min against 2.667
    reserved reads/min. Twice capacity, and twice the 0.667 obs/min the
    report claimed for it. See AdmissionAccount (V5) for the repair and
    for why a floor cannot express a rate below one per tick.

    Retained so the replacement can be run against the scenario that
    broke it, which is the same reason V1 is still here.

    Its two questions, which were the right two:

      1. STEADY STATE. Admit no faster than service/horizons, which is
         the rate at which the queue neither grows nor shrinks. V1 had
         this part right; it was the brake bolted on top that killed it.

      2. TRANSIENT. If the queue as it stands cannot be cleared inside
         the tolerance -- drain_time_s against tolerance_s, seconds
         against seconds -- admit less. Never none.

    NOTE WHAT THIS DOES NOT CLAIM. It does not make an infeasible
    workload feasible. If arrivals exceed sustainable intake the queue
    still grows; this bounds how fast, and keeps the collector
    collecting while it does. The feasibility question is answered by
    the capacity report, not by a policy.
    """
    sustainable = max(ADMIT_FLOOR_OBS,
                      int(cap.sustainable_obs_per_tick))
    deadline_s = cap.tolerance_s * DRAIN_HEADROOM
    saturated = cap.drain_time_s > deadline_s

    if saturated:
        admit = max(ADMIT_FLOOR_OBS, sustainable // 2)
        why = ("queue needs %.0fs to drain against a %.0fs deadline; "
               "admitting %d, never zero" % (cap.drain_time_s, deadline_s,
                                             admit))
    else:
        admit = sustainable
        why = ("queue drains in %.0fs, inside the %.0fs deadline"
               % (cap.drain_time_s, deadline_s))

    return Decision(max(0, min(cap.sample_budget_reads, admit)),
                    saturated, why, V3)


# ── V5: FRACTIONAL ADMISSION, WITH STATE ─────────────────────────────
#
# WHAT V3 GOT WRONG, found by independent review and reproduced:
#
#     max(ADMIT_FLOOR_OBS, int(cap.sustainable_obs_per_tick))
#
# At a 45-second tick, a reserve of two reads and four horizons,
# sustainable_obs_per_tick is 0.5. int(0.5) is 0, and the floor turns
# that 0 into 1 -- ON EVERY TICK, INCLUDING WITH A BACKLOG OF 100.
# That is 1.333 observations/minute creating 5.333 follow-up tasks per
# minute against 2.667 reserved reads per minute. TWICE CAPACITY.
#
# So the report's "0.667 observations/minute" was arithmetic I did on
# paper and never implemented. The code admitted double it.
#
# THE FLOOR WAS THE RIGHT ANSWER TO THE WRONG QUESTION. V1 latched at
# zero FOREVER because integer division floored and nothing could
# reopen it. The fix for that is not "always admit at least one" -- it
# is "never lose the fraction". Those differ exactly where it matters:
#
#     permanent latch    admits 0 on every tick, no state of the queue
#                        changes it, the collector stops. THE BUG.
#     fractional pacing  admits 0 on some ticks and 1 on others, and
#                        the long-run average is the sustainable rate.
#                        CORRECT, and at this capacity it means one
#                        observation every two ticks.
#
# A floor cannot express a rate below one per tick. An accumulator can,
# so the credits are kept and ADMIT_FLOOR_OBS is not applied.
#
# WHAT ELSE THE ACCOUNT HAS TO DO, beyond not overshooting:
#
#   * count FORWARD obligations, not just due ones. Four horizons means
#     an observation admitted now is four reads of demand that has not
#     arrived yet (see TickCapacity.outstanding_tasks);
#   * bound the credits, so a long saturated stretch cannot bank an
#     admission burst that lands the moment capacity returns;
#   * leave recovery headroom, so a backlog DRAINS rather than merely
#     stops growing. Arrivals exactly equal to nominal service is a
#     queue that never recovers from any excursion, and calling that
#     stable is what the review objected to.

# Intake targets this share of effective service. The remainder is what
# drains an existing backlog. At 1.0 the queue is marginally stable in
# theory and never recovers in practice, because any excursion is
# permanent.
TARGET_UTILISATION = 0.85

# How much of the commitment window outstanding work may already fill
# before admission stops entirely. Below 1.0 so the brake acts while
# there is still time to act.
COMMITMENT_HEADROOM = 0.80

# Credits carried across ticks, as a multiple of one tick's earnings.
# THIS IS THE BURST BOUND. Without it a policy that admits nothing for
# an hour banks an hour of credits and dumps them into a queue that has
# just started recovering.
MAX_CARRY_TICKS = 1.0

V5 = "BETTOR_ADMISSION_V5_FRACTIONAL"


class AdmissionAccount:
    """Stateful fractional admission. One instance per collector.

    Not a pure function, which is why it is a class and not another
    entry in POLICIES: the whole repair is that the fraction survives
    between ticks. A stateless call cannot admit 0.5 observations, and
    every rounding of 0.5 is either 0 (V1's latch) or 1 (V3's double).
    """

    def __init__(self):
        self.credits_obs = 0.0
        self.ticks = 0
        self.admitted_total = 0
        self.zero_ticks = 0

    def decide(self, cap: TickCapacity) -> Decision:
        self.ticks += 1

        earn = cap.sustainable_obs_per_tick * TARGET_UTILISATION
        notes = []

        # 1. FORWARD OBLIGATIONS. Can the work already committed be
        #    finished inside the window it was committed for?
        obligation_s = cap.obligation_time_s
        if obligation_s is None:
            notes.append("outstanding_tasks not supplied, so the forward "
                         "obligation check did NOT run")
            overcommitted = False
        else:
            limit_s = cap.commitment_window_s * COMMITMENT_HEADROOM
            overcommitted = obligation_s > limit_s
            if overcommitted:
                notes.append("outstanding work needs %.0fs against a %.0fs "
                             "commitment window" % (obligation_s, limit_s))

        # 2. THE DUE QUEUE. Seconds against seconds, as V1 failed to do.
        deadline_s = cap.tolerance_s * DRAIN_HEADROOM
        saturated = cap.drain_time_s > deadline_s
        if saturated:
            notes.append("due queue needs %.0fs to drain against a %.0fs "
                         "deadline" % (cap.drain_time_s, deadline_s))

        # 3. EARN. A tick with no effective reserve earns nothing: there
        #    is no service to pace against, so there is no rate to bank.
        if cap.effective_reserve_reads <= 0:
            earn = 0.0
            notes.append("no effective follow-up reserve this tick")
        self.credits_obs += earn

        # 4. SPEND -- or not. Either brake holds the admission back but
        #    does NOT burn the credits, so a recovering collector
        #    resumes at its proper rate rather than from zero.
        if overcommitted or saturated:
            admit = 0
            why = "admitting 0: " + "; ".join(notes)
        else:
            admit = int(self.credits_obs)
            admit = max(0, min(admit, cap.sample_budget_reads))
            self.credits_obs -= admit
            why = ("credits %.2f/tick, admitting %d"
                   % (earn, admit))
            if notes:
                why += " (" + "; ".join(notes) + ")"

        # 5. BOUND THE CARRY. At most one tick's earnings survive, so an
        #    outage cannot bank a burst. The bound is never below 1.0,
        #    or a sub-unit earn rate could never reach a whole
        #    observation and this would be V1's latch wearing a float.
        ceiling = max(1.0, earn * MAX_CARRY_TICKS)
        if self.credits_obs > ceiling:
            why += " (credits capped %.2f -> %.2f)" % (self.credits_obs,
                                                       ceiling)
            self.credits_obs = ceiling

        self.admitted_total += admit
        if admit == 0:
            self.zero_ticks += 1
        return Decision(admit, saturated or overcommitted, why, V5)

    @property
    def admitted_obs_per_tick(self) -> float:
        """The realised long-run rate. This is the number to compare
        against sustainable_obs_per_tick, not any single tick."""
        return self.admitted_total / self.ticks if self.ticks else 0.0


POLICIES = {V4: policy_v4, V1: policy_v1, V2: policy_v2, V3: policy_v3}


# THE FOUR WAYS OUT, AND WHAT EACH COSTS.
#
# An admission policy chooses what to drop. It cannot create read
# budget, and the workload is over budget, so one of these has to be
# chosen -- none of them is free and none of them is a scheduling fix.
#
# Each entry states the change, what it does to intake, and what it
# costs somewhere else. `evidence_cost` is the one that matters for a
# research collector: the frozen sampling rule governs WHICH markets
# enter the sample, so anything that admits less narrows the frame and
# has to be versioned.
OPTIONS = {
    "admit_less": {
        "change": "cap intake at the sustainable rate",
        "intake": "falls to service/horizons observations per minute",
        "timing": "on-time rises: the queue stops growing",
        "evidence_cost": ("the sampling frame narrows. Fewer markets "
                          "per unit time, so a fixed-length window "
                          "covers less of the universe. This is a "
                          "sampling change and must be versioned."),
        "resource_cost": "none; it uses less",
        "operational_cost": "none",
    },
    "read_more": {
        "change": "raise the follow-up reserve per tick",
        "intake": "unchanged",
        "timing": "on-time rises if the venue serves the extra reads",
        "evidence_cost": "none",
        "resource_cost": ("more requests on a gateway the money path "
                          "shares and that already returns 429s. The "
                          "pacing backoff exists because of those."),
        "operational_cost": ("contends with the mirror lane for gaps; "
                             "the collector is the lowest-priority "
                             "consumer by design"),
    },
    "fewer_horizons": {
        "change": "watch three horizons instead of four",
        "intake": "unchanged",
        "timing": "on-time rises: each observation owes fewer reads",
        "evidence_cost": ("a declared horizon disappears from the "
                          "design. 3600s is the one the EV work leans "
                          "on least per observation but it is also the "
                          "one that cannot be reconstructed later."),
        "resource_cost": "none",
        "operational_cost": "none",
    },
    "shorter_tick": {
        "change": "tick faster than the tolerance window is wide",
        "intake": "unchanged",
        "timing": ("addresses the part capacity cannot: with a gap "
                   "below 2x tolerance every due moment has a tick "
                   "inside its window"),
        "evidence_cost": "none",
        "resource_cost": "same reads, more often; same gateway pressure",
        "operational_cost": "more frequent wakeups, same 429 exposure",
    },
}


def feasibility(cap: TickCapacity, arrivals_obs_per_min: float) -> dict:
    """Can this workload be served at all? Answer before tuning.

    An admission policy chooses which work to drop when there is too
    much. It cannot create read budget. If demand exceeds what the
    budget can serve, the only real choices are: admit less, read more,
    or watch fewer horizons -- and this quantifies each.
    """
    sustainable_per_min = cap.sustainable_obs_per_s * 60.0
    ratio = (arrivals_obs_per_min / sustainable_per_min
             if sustainable_per_min > 0 else float("inf"))
    return {
        "arrivals_obs_per_min": round(arrivals_obs_per_min, 3),
        "sustainable_obs_per_min": round(sustainable_per_min, 3),
        "oversubscription_ratio": round(ratio, 2),
        "feasible": ratio <= 1.0,
        "tasks_created_per_min": round(
            arrivals_obs_per_min * cap.horizons_per_obs, 2),
        "tasks_served_per_min": round(cap.service_reads_per_s * 60.0, 2),
        "choices": {
            "admit_less": (
                "cut intake to %.2f observations/min, which is %.0f%% of "
                "the current rate" % (
                    sustainable_per_min,
                    100.0 * sustainable_per_min / arrivals_obs_per_min
                    if arrivals_obs_per_min else 0.0)),
            "read_more": (
                "raise the follow-up reserve from %d to %d reads per "
                "tick" % (cap.fu_reserve_reads,
                          int(round(cap.fu_reserve_reads * ratio)))),
            "watch_fewer_horizons": (
                "drop from %d horizons to %d" % (
                    cap.horizons_per_obs,
                    max(1, int(cap.horizons_per_obs / ratio))
                    if ratio > 0 else cap.horizons_per_obs)),
        },
    }
