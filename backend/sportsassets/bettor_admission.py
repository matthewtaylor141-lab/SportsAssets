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

How long it STAYS true is a separate question, and one I answered wrong
before this harness existed: I called the zero state absorbing. Replayed
against the queue V1 actually inherited, the brake releases around tick
nineteen. V1 is a twenty-three-minute blackout, not a permanent latch.
That is still the wrong answer to a full queue -- which is why the
candidate carries an admission floor -- but it is a different fault from
the one I named, and the harness is what told me so.

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
# CORRECTION TO WHAT I FIRST WROTE HERE. I described V1's zero as
# absorbing -- "nothing the collector did could reopen the gate". The
# harness says that is wrong, and the correction matters because it
# changes what the defect actually is. Replaying V1 against the state
# it inherited:
#
#     7 ticks   (what production ran)   0 admitted, saturated 7/7
#     50 ticks  (left alone)           31 admitted, saturated 19/50
#
# The brake DOES release, after about nineteen ticks -- some twenty-three
# minutes -- once the inherited pipeline drains. V1 was a long transient,
# not a permanent latch, and production was rolled back before it could
# recover.
#
# The floor is still right, for the reason that survives the correction:
# twenty-three minutes of collecting nothing is not an acceptable
# response to a full queue, and it arrives silently every time the
# policy meets a backlog. A floor of one keeps the collector collecting
# while it catches up, and makes the zero state unreachable rather than
# merely temporary.
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
