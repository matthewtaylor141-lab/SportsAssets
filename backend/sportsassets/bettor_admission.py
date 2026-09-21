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

    # ── derived, and every one of these is a rate or a time ──────────

    @property
    def service_reads_per_s(self) -> float:
        return self.fu_reserve_reads / self.tick_period_s

    @property
    def sustainable_obs_per_s(self) -> float:
        """The stability condition, and the only number that matters
        long run: each observation creates `horizons_per_obs` tasks, so
        admitting faster than service/horizons grows the queue without
        bound and no ordering rule can rescue it."""
        return self.service_reads_per_s / max(1, self.horizons_per_obs)

    @property
    def sustainable_obs_per_tick(self) -> float:
        return self.sustainable_obs_per_s * self.tick_period_s

    @property
    def drain_time_s(self) -> float:
        """How long the CURRENT queue takes to clear at the CURRENT
        service rate. A time, so it can be compared with a deadline."""
        if self.fu_reserve_reads <= 0:
            return float("inf")
        return (self.backlog_tasks / self.fu_reserve_reads) * self.tick_period_s


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
    """Candidate. Compares a time with a time, and cannot latch shut.

    Two questions, in order:

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
