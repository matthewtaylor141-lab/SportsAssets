"""Run every admission policy against one workload and compare.

WHAT IT DRIVES. The real functions from bettor_admission -- the same
objects workers/bettor_state.py calls. Not a reimplementation. The
simulator that "passed" while production failed did so because it
modelled an ordering rule the system did not run, and the whole reason
this file imports the policy rather than describing it is to make that
class of mistake impossible here.

WHAT IT MODELS. A discrete tick loop with:

  * arrivals, as observations per minute, taken from production;
  * four follow-up tasks per observation, due at 60s, 300s, 900s and
    3600s after the observation, which is what the collector actually
    owes;
  * a per-tick read budget split between sampling and follow-ups
    exactly as the production tick splits it;
  * tolerance and a recovery window, applied with the deployed
    classification rules and not re-tuned per policy.

THE ACCOUNTING IS CLOSED, AND THAT IS THE POINT. Every task created ends
in exactly one terminal state:

    ON_TIME        served inside tolerance
    LATE_RECOVERY  served inside the recovery window
    EXPIRED        never served, deadline passed
    DEFERRED       still in the queue when the run ended
    NEVER_CREATED  the observation was declined at admission

created == on_time + late + expired + deferred, asserted every run. An
on-time RATE computed over a denominator that quietly lost its hard
cases is the specific way this measurement gets faked, so the
denominator is every task the workload was owed and the identity is
checked rather than assumed.

NEVER_CREATED is reported beside the rate and never inside it. A policy
that admits nothing scores 100% on-time against an empty denominator;
V1 did exactly that in production, at 11.1%, while writing zero
observations. Coverage and timing are two numbers and stay two numbers.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field

from . import bettor_admission as pol
from . import bettor_state_capture as sc


@dataclass
class Workload:
    """The measured shape of the job. Defaults are production figures."""

    arrivals_obs_per_min: float = 2.1
    tick_period_s: float = 71.4
    minutes: int = 240
    total_budget_reads: int = 10        # MAX_MARKETS_PER_TICK + 2
    horizons_s: tuple = tuple(sc.HORIZONS_OBSERVABLE_S)
    tolerance_s: float = float(sc.HORIZON_TOLERANCE_S)
    recovery_s: float = 600.0
    # Reads that fail at the venue and must be retried. Production saw
    # rate limiting; a policy that only works on a perfect venue is not
    # a policy.
    read_failure_rate: float = 0.0
    seed: int = 12345
    # The two bounds mids_outstanding uses. Defaults are the deployed
    # constants; they are fields so a capacity choice can be tested
    # without editing the collector.
    due_window_s: float = float(sc.HORIZON_DUE_WINDOW_S)
    early_eligibility_s: float = float(sc.HORIZON_EARLY_ELIGIBILITY_S)

    # THE STATE V1 WAS DEPLOYED INTO, AND WITHOUT IT THIS HARNESS SAYS
    # V1 IS FINE. Run from an empty queue V1 scores 91.5% and admits
    # steadily, because its brake -- backlog > fu_reserve -- never trips
    # when the backlog starts at zero and service keeps up.
    #
    # Production was not empty. V1 replaced V4, which had been running
    # for eleven hours and left 15 to 26 tasks due at any instant. V1's
    # first tick therefore saw a backlog above 5, latched, admitted
    # nothing, and had no path back open: with intake at zero the only
    # thing that could drain the queue was an hour of prior commitment.
    #
    # A harness that starts every policy from rest cannot see that, and
    # would have cleared V1 to deploy. The initial backlog IS the
    # regression scenario.
    initial_backlog_tasks: int = 0

    # THE REST OF THE INHERITED STATE, AND THE PART THAT ACTUALLY KILLED
    # V1. Seeding tasks that are ALREADY DUE is not enough: with intake
    # at zero those drain in a few ticks and the brake releases, which
    # is why this harness first reported V1 saturating 3 ticks out of
    # 201 while production saturated every one of its 7.
    #
    # What production handed V1 was eleven hours of V4 observations
    # whose LATER horizons had not yet come due. An observation admitted
    # 40 minutes ago still owes its 900s read (already open) and its
    # 3600s read (opening in 20 minutes). That pipeline keeps feeding
    # the backlog for a full hour after intake stops, so a policy whose
    # brake is "is the backlog large" can shut and stay shut through the
    # entire drain -- with nothing it can do to end it, because the
    # thing filling the queue is work it already owes.
    #
    # Modelled as observations admitted uniformly over the preceding
    # hour at the measured arrival rate.
    inflight_obs_minutes: float = 0.0


@dataclass
class Task:
    due_at_s: float
    horizon_s: int
    obs_id: int
    served_at_s: float | None = None
    attempts: int = 0


@dataclass
class Result:
    version: str
    created: int = 0
    never_created: int = 0
    on_time: int = 0
    late: int = 0
    expired: int = 0
    deferred: int = 0
    observations: int = 0
    ticks: int = 0
    reads_used: int = 0
    saturated_ticks: int = 0
    max_backlog: int = 0
    by_horizon: dict = field(default_factory=dict)

    def check(self) -> None:
        """created == on_time + late + expired + deferred. Every task
        created has exactly one end."""
        total = self.on_time + self.late + self.expired + self.deferred
        assert total == self.created, (
            "task accounting does not close: created=%d but "
            "on_time+late+expired+deferred=%d" % (self.created, total))

    @property
    def on_time_rate(self) -> float:
        """Over EVERY task created, including the ones never served.
        Not over attempts -- attempts is a denominator a struggling
        policy can shrink."""
        return (self.on_time / self.created) if self.created else 0.0

    def as_dict(self) -> dict:
        self.check()
        return {
            "version": self.version,
            "observations_admitted": self.observations,
            "observations_declined": self.never_created,
            "tasks_created": self.created,
            "on_time": self.on_time,
            "late_recovery": self.late,
            "expired": self.expired,
            "deferred_at_end": self.deferred,
            "on_time_rate_of_created": round(self.on_time_rate, 4),
            "ticks": self.ticks,
            "saturated_ticks": self.saturated_ticks,
            "max_backlog_tasks": self.max_backlog,
            "reads_used": self.reads_used,
            "by_horizon": self.by_horizon,
        }


class _Rand:
    """A tiny deterministic LCG. The same workload must reach every
    policy; a shared global RNG would give each run a different one and
    the comparison would not be a comparison."""

    def __init__(self, seed: int):
        self.s = seed & 0xFFFFFFFF

    def next(self) -> float:
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return self.s / 0x7FFFFFFF


def run_policy(version: str, w: Workload) -> Result:
    policy = pol.POLICIES[version]
    r = Result(version=version)
    rng = _Rand(w.seed)

    horizons = list(w.horizons_s)
    r.by_horizon = {h: {"created": 0, "on_time": 0, "late": 0,
                        "expired": 0, "deferred": 0} for h in horizons}

    ticks = int(w.minutes * 60 / w.tick_period_s)
    # Arrivals accumulate as a fractional rate so a rate below one per
    # tick is not silently rounded to zero -- which would model the very
    # starvation the policy is being tested for.
    arrivals_per_tick = w.arrivals_obs_per_min * w.tick_period_s / 60.0
    pending_arrivals = 0.0

    queue: list[Task] = []
    obs_id = 0

    # Seed the inherited queue: tasks already due when the policy took
    # over, spread across the horizons as V4 left them. These are
    # counted in `created` like any other task, so the accounting still
    # closes and the on-time rate is not computed over a denominator
    # that quietly excludes the hardest work in the run.
    if w.initial_backlog_tasks:
        for i in range(w.initial_backlog_tasks):
            h = horizons[i % len(horizons)]
            obs_id += 1
            queue.append(Task(due_at_s=-float(i % 30), horizon_s=h,
                              obs_id=obs_id))
            r.created += 1
            r.by_horizon[h]["created"] += 1

    # Observations already admitted before this policy took over, spread
    # uniformly across the preceding window. Their horizons fall where
    # they fall: some overdue, some open now, some opening later in the
    # run. This is the queue the policy inherits, not a number.
    if w.inflight_obs_minutes:
        n_inflight = int(w.arrivals_obs_per_min * w.inflight_obs_minutes)
        for i in range(n_inflight):
            age = (i + 1) * (w.inflight_obs_minutes * 60.0
                             / max(1, n_inflight))
            obs_id += 1
            for h in horizons:
                due = h - age
                # already past its recovery window: V4 lost it, and it
                # is not this policy's to be judged on
                if due + w.recovery_s < 0:
                    continue
                queue.append(Task(due_at_s=due, horizon_s=h,
                                  obs_id=obs_id))
                r.created += 1
                r.by_horizon[h]["created"] += 1

    for t in range(ticks):
        now = t * w.tick_period_s

        # THE BUDGET SPLIT, AS PRODUCTION SPLITS IT.
        total = w.total_budget_reads
        if total <= 1:
            fu_reserve = 1 if (t % 2) else 0
            sample_budget = total - fu_reserve
        else:
            fu_reserve = max(1, total // 2)
            sample_budget = max(1, total - fu_reserve)

        # BACKLOG, AS mids_outstanding COUNTS IT -- and getting this
        # wrong is how a harness clears a policy that then fails.
        #
        # _OUTSTANDING_SQL counts observations whose age lies between
        # (horizon - HORIZON_EARLY_ELIGIBILITY_S) and (horizon +
        # HORIZON_DUE_WINDOW_S) with no read yet recorded. That is a
        # 630-second window per horizon, not "due within tolerance".
        # Modelled the narrow way, the backlog a policy sees here is a
        # small fraction of the number production feeds it: V1 saturates
        # on 3 ticks out of 201 instead of every tick, and the harness
        # reports the regression as healthy.
        #
        # The number the policy is handed has to be the number the
        # deployed query returns.
        due = [x for x in queue
               if x.served_at_s is None
               and now >= x.due_at_s - w.early_eligibility_s
               and now <= x.due_at_s + w.due_window_s]
        backlog = len(due)
        r.max_backlog = max(r.max_backlog, backlog)

        cap = pol.TickCapacity(
            total_budget_reads=total,
            fu_reserve_reads=fu_reserve,
            sample_budget_reads=sample_budget,
            backlog_tasks=backlog,
            horizons_per_obs=len(horizons),
            tick_period_s=w.tick_period_s,
            tolerance_s=w.tolerance_s,
        )
        decision = policy(cap)
        if decision.saturated:
            r.saturated_ticks += 1

        # ── arrivals and admission ──────────────────────────────────
        pending_arrivals += arrivals_per_tick
        want = int(pending_arrivals)
        pending_arrivals -= want
        admit = min(want, decision.admit_obs)
        r.never_created += (want - admit) * len(horizons)

        for _ in range(admit):
            obs_id += 1
            r.observations += 1
            for h in horizons:
                queue.append(Task(due_at_s=now + h, horizon_s=h,
                                  obs_id=obs_id))
                r.created += 1
                r.by_horizon[h]["created"] += 1

        # ── service: in-band first, then oldest, as production does ──
        servable = [x for x in queue
                    if x.served_at_s is None
                    and x.due_at_s <= now + w.tolerance_s
                    and now <= x.due_at_s + w.recovery_s]
        servable.sort(key=lambda x: (
            abs(now - x.due_at_s) > w.tolerance_s, x.due_at_s))

        budget = fu_reserve
        for task in servable:
            if budget <= 0:
                break
            budget -= 1
            r.reads_used += 1
            task.attempts += 1
            if w.read_failure_rate and rng.next() < w.read_failure_rate:
                continue                      # retried on a later tick
            task.served_at_s = now
            lag = abs(now - task.due_at_s)
            if lag <= w.tolerance_s:
                r.on_time += 1
                r.by_horizon[task.horizon_s]["on_time"] += 1
            else:
                r.late += 1
                r.by_horizon[task.horizon_s]["late"] += 1

        # ── expiry ──────────────────────────────────────────────────
        still: list[Task] = []
        for x in queue:
            if x.served_at_s is not None:
                continue
            if now > x.due_at_s + w.recovery_s:
                r.expired += 1
                r.by_horizon[x.horizon_s]["expired"] += 1
            else:
                still.append(x)
        queue = still
        r.ticks += 1

    for x in queue:
        if x.served_at_s is None:
            r.deferred += 1
            r.by_horizon[x.horizon_s]["deferred"] += 1

    r.check()
    return r


def compare(w: Workload, versions=None) -> dict:
    versions = versions or [pol.V4, pol.V1, pol.V2, pol.V3]
    results = {v: run_policy(v, w).as_dict() for v in versions}

    cap = pol.TickCapacity(
        total_budget_reads=w.total_budget_reads,
        fu_reserve_reads=max(1, w.total_budget_reads // 2),
        sample_budget_reads=max(1, w.total_budget_reads
                                - max(1, w.total_budget_reads // 2)),
        backlog_tasks=0,
        horizons_per_obs=len(w.horizons_s),
        tick_period_s=w.tick_period_s,
        tolerance_s=w.tolerance_s,
    )
    return {
        "workload": {
            "arrivals_obs_per_min": w.arrivals_obs_per_min,
            "tick_period_s": w.tick_period_s,
            "minutes": w.minutes,
            "total_budget_reads_per_tick": w.total_budget_reads,
            "horizons_s": list(w.horizons_s),
            "tolerance_s": w.tolerance_s,
            "recovery_s": w.recovery_s,
            "read_failure_rate": w.read_failure_rate,
            "initial_backlog_tasks": w.initial_backlog_tasks,
            "inflight_obs_minutes": w.inflight_obs_minutes,
        },
        "feasibility": pol.feasibility(cap, w.arrivals_obs_per_min),
        "results": results,
    }


def _print(report: dict) -> None:
    w = report["workload"]
    f = report["feasibility"]
    print("WORKLOAD  %.2f obs/min, %d-min run, %d reads/tick, horizons %s"
          % (w["arrivals_obs_per_min"], w["minutes"],
             w["total_budget_reads_per_tick"], w["horizons_s"]))
    print("          tolerance %.0fs, recovery %.0fs, read failure %.0f%%,"
          " inherited backlog %d tasks"
          % (w["tolerance_s"], w["recovery_s"],
             100 * w["read_failure_rate"],
             w.get("initial_backlog_tasks", 0)))
    print()
    print("FEASIBILITY")
    print("  tasks created/min : %.2f" % f["tasks_created_per_min"])
    print("  tasks served/min  : %.2f" % f["tasks_served_per_min"])
    print("  sustainable intake: %.2f obs/min (now %.2f)"
          % (f["sustainable_obs_per_min"], f["arrivals_obs_per_min"]))
    print("  oversubscription  : %.2fx     FEASIBLE: %s"
          % (f["oversubscription_ratio"], f["feasible"]))
    if not f["feasible"]:
        print("  the workload cannot be served at this budget. choices:")
        for k, v in f["choices"].items():
            print("      %-22s %s" % (k, v))
    print()
    print("%-38s %6s %6s %7s %7s %7s %8s %7s %6s"
          % ("policy", "obs", "tasks", "on_time", "late", "expired",
             "deferred", "rate", "sat"))
    for v, res in report["results"].items():
        print("%-38s %6d %6d %7d %7d %7d %8d %6.1f%% %6s"
              % (v, res["observations_admitted"], res["tasks_created"],
                 res["on_time"], res["late_recovery"], res["expired"],
                 res["deferred_at_end"],
                 100 * res["on_time_rate_of_created"],
                 "%d/%d" % (res["saturated_ticks"], res["ticks"])))
    print()
    print("COVERAGE IS NOT TIMING. Observations declined at admission:")
    for v, res in report["results"].items():
        print("  %-38s %d declined, %d admitted"
              % (v, res["observations_declined"] // len(w["horizons_s"]),
                 res["observations_admitted"]))


def main(argv) -> int:
    w = Workload()
    for arg in argv[1:]:
        if "=" in arg:
            k, val = arg.split("=", 1)
            if hasattr(w, k):
                cur = getattr(w, k)
                setattr(w, k, type(cur)(val) if not isinstance(cur, tuple)
                        else tuple(int(x) for x in val.split(",")))
    report = compare(w)
    _print(report)
    print()
    print(json.dumps(report["feasibility"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
