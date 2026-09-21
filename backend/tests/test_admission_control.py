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


def test_admission_is_derived_from_capacity_not_a_magic_number():
    """Sustainable intake is the follow-up reserve divided by the
    number of horizons each admitted observation obliges us to read.
    Both terms are measured on the same tick, in the same unit."""
    r = asyncio.run(w.tick(_Pool()))
    horizons = len(sc.HORIZONS_OBSERVABLE_S)
    assert r["admitCap"] == r["fuReserve"] // horizons
    assert r["admissionVersion"] == w.ADMISSION_VERSION


def test_one_observation_creates_one_task_per_horizon():
    """The arithmetic the throttle rests on. If this ever stops being
    true the divisor above is wrong."""
    assert len(sc.HORIZONS_OBSERVABLE_S) == 4
    assert sc.HORIZONS_OBSERVABLE_S == (60, 300, 900, 3600)


def test_a_deep_backlog_stops_intake_entirely():
    """Capacity already owed to outstanding work is not available for
    new work. Admitting into a saturated queue does not collect more
    data -- it converts reads that would have completed on time into
    reads that expire."""
    deep = asyncio.run(w.tick(_Pool(backlog=10_000)))
    assert deep["admissionSaturated"] is True
    assert deep["admitCap"] == 0
    assert deep["backlogTasks"] == 10_000 * len(sc.HORIZONS_OBSERVABLE_S)

    shallow = asyncio.run(w.tick(_Pool(backlog=0)))
    assert shallow["admissionSaturated"] is False
    assert shallow["admitCap"] >= 0


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
    assert w.ADMISSION_VERSION == "BETTOR_ADMISSION_V1_CAPACITY_AWARE"
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
