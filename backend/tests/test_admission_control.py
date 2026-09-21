"""CAPACITY-AWARE ADMISSION: the collector stops creating work it
cannot service.

THE MEASUREMENT THAT FORCED THIS. W3, run 180, 60-minute flows:

    intake                         1.43 observations/min
    follow-up tasks made eligible  5.85 tasks/min   (intake x 4)
    attempts completed             2.03 tasks/min
    tasks expiring unread          4.02 tasks/min

Sixty-five percent of the follow-up work this collector created for
itself was discarded unread. Four scheduling repairs -- reservation,
rotation, per-horizon cap, on-time-first ordering -- all decide WHICH
tasks get served. None of them can change HOW MANY, and the shortfall
is a rate.

These tests pin the throttle and, just as importantly, pin the
ACCOUNTING around it: a declined market is deferred, not lost, and must
never be counted in the same column as a market the budget dropped.
"""

import asyncio

from sportsassets import bettor_state_capture as sc
from sportsassets.workers import bettor_state as w


class _Pool:
    """Empty pool: no premap rows, no backlog."""

    def __init__(self, backlog=0):
        self._backlog = backlog

    async def fetch(self, *a, **k):
        return []

    async def fetchrow(self, sql, *a, **k):
        # mids_outstanding asks for a count; everything else gets None.
        if "count(*)" in str(sql):
            return {"n": self._backlog}
        return None

    async def execute(self, *a, **k):
        return None


def test_the_gate_measures_and_does_not_enforce():
    """V2, AND THE REASON IS A PRODUCTION MEASUREMENT.

    V1 ran for seven ticks (2026-09-21 12:07:34Z-12:14:31Z) and its gate
    closed on all seven: admit_cap 0.00 every tick, obs_written 0,
    ON_TIME 3/27 = 11.1% against V4's 246/796 = 30.9%. Intake stopped
    and timing got worse.

    Note which tests passed while that happened: every one of them. They
    checked that the rule behaved as designed, and the rule as designed
    was wrong. So this file now pins the production consequence, not
    just the intended arithmetic.

    The cap no longer restricts the budget. backlogTasks and
    admissionSaturated keep recording the oversubscription, which is
    real -- 15 to 26 tasks due against 5 reads a tick."""
    r = asyncio.run(w.tick(_Pool()))
    assert r["admitCap"] == r["budget"]
    assert r["admissionEnforced"] is False
    assert r["admissionVersion"] == w.ADMISSION_VERSION


def test_the_threshold_that_latched_shut_is_recorded():
    """WHY IT COULD NEVER REOPEN, in one line of arithmetic.

    `saturated = backlog > max(1, fu_reserve)` compares a LEVEL against
    a FLOW: tasks due right now against reads available in ONE tick.
    fu_reserve is 5. Every admitted observation owes a read at 60, 300,
    900 and 3600 seconds, so any steady state carries more than five due
    at once -- the gate shuts on the first tick, and with intake at zero
    the only thing left to drain it is an hour of prior commitment.

    Same level-versus-flow confusion named in the W3 write-up, then
    written into the repair for it."""
    deep = asyncio.run(w.tick(_Pool(backlog=10_000)))
    assert deep["admissionSaturated"] is True
    # measured, and NOT acted on
    assert deep["admitCap"] == deep["budget"]
    assert deep["budget"] > 0, "a saturated queue must not stop intake"


def test_one_observation_creates_one_task_per_horizon():
    """The arithmetic the throttle rests on. If this ever stops being
    true the divisor above is wrong."""
    assert len(sc.HORIZONS_OBSERVABLE_S) == 4
    assert sc.HORIZONS_OBSERVABLE_S == (60, 300, 900, 3600)


def test_the_backlog_is_still_counted_across_every_horizon():
    """The measurement survives the rollback -- it is the thing that
    showed the system is genuinely oversubscribed, and dropping it would
    lose the only evidence for the fix that still has to be built."""
    deep = asyncio.run(w.tick(_Pool(backlog=10_000)))
    assert deep["backlogTasks"] == 10_000 * len(sc.HORIZONS_OBSERVABLE_S)

    shallow = asyncio.run(w.tick(_Pool(backlog=0)))
    assert shallow["admissionSaturated"] is False
    assert shallow["backlogTasks"] == 0


def test_declined_work_is_never_counted_as_lost_work():
    """THE ACCOUNTING DEFECT I INTRODUCED AND CAUGHT IN REPLAY.

    The first version of this change applied the cap by shrinking
    `budget`. The sampling loop then reported every declined market as
    `read_budget_exhausted` / obs_skipped_budget -- the LOSS counter --
    so the throttle would have hidden inside the very figure I have
    been reporting as coverage failure since W1.

    A declined market stays in the rotation and comes round again. A
    budget-dropped market does not. Two causes, two counters."""
    import inspect
    src = inspect.getsource(w.tick)
    # the cap is its own limit, not a smaller budget
    assert "admitted_budget = min(budget, admit_cap)" in src
    assert 'stats["obsSkippedAdmission"] = remaining' in src
    # and the two statuses are distinguishable
    assert '"admission_capped"' in src
    assert '"read_budget_exhausted"' in src


def test_the_throttle_is_visible_in_telemetry_or_it_does_not_exist():
    """Without these columns a capped tick is indistinguishable from a
    tick that found no work, and 'we admitted less' is
    indistinguishable from 'we collected less'."""
    import ast
    import inspect
    from sportsassets import bettor_state_store as ss
    src = inspect.getsource(ss)
    lits = [n.value for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    ins = [s for s in lits if "INSERT INTO bettor_capture_ticks" in s]
    assert ins, "tick insert not found"
    sql = ins[0]
    for col in ("obs_skipped_admission", "backlog_tasks", "admit_cap",
                "admission_saturated", "admission_version"):
        assert col in sql, col
    # placeholders and columns still line up
    cols = sql.split("(", 1)[1].split(")", 1)[0]
    assert len([c for c in cols.split(",") if c.strip()]) == sql.count("$")


def test_the_sampling_change_is_versioned_so_rows_stay_separable():
    """WHICH markets enter the sample is exactly what the frozen rule
    governs. Rows admitted under the throttle must be distinguishable
    from every row admitted before it, or the broad research cohort and
    the narrower throttled sample get pooled."""
    assert w.ADMISSION_VERSION == "BETTOR_ADMISSION_V2_MEASURE_ONLY"
    r = asyncio.run(w.tick(_Pool()))
    assert r["admissionVersion"] == w.ADMISSION_VERSION


def test_admission_does_not_touch_the_scientific_tolerances():
    """The throttle changes how much work is created. It must not
    change what counts as a valid observation of it."""
    assert sc.HORIZON_TOLERANCE_S == 30
    assert sc.ADMISSIBLE_TO_HORIZON_GATE == (sc.TIMING_ON_TIME,)
    assert sc.HORIZON_DUE_WINDOW_S == 600
    assert sc.HORIZON_EARLY_ELIGIBILITY_S == 30


def test_requests_still_never_exceed_the_tick_budget():
    """Admission may only ever REDUCE what is issued."""
    for backlog in (0, 1, 5, 50, 10_000):
        r = asyncio.run(w.tick(_Pool(backlog=backlog)))
        assert r["fuReserve"] + r["budget"] <= r["totalBudget"]
        assert r["admitCap"] <= r["budget"] or r["admitCap"] == 0
