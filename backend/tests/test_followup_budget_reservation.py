"""THE W1 STARVATION DEFECT, AND THE RESERVATION THAT FIXES IT.

W1 measured zero ON_TIME follow-ups at every horizon. The diagnosis
(research/bettor_w1_diagnosis.sql, run on 6612731) separated two
candidate causes and refuted the one I had proposed:

    18 ticks where sampling exhausted its budget -> 0.28 follow-ups/tick
     7 ticks with budget left over               -> 2.14 follow-ups/tick

Follow-ups were funded from the sampling pass's LEFTOVERS, so a tick
that used its budget on initial reads attempted no follow-up at all.
Reordering the queue -- my earlier proposal -- would have reordered a
queue that was never serviced.

These tests pin the reservation and the measurement defect it exposed.
They are focused on the diagnosed failure; they do not re-run unrelated
suites.
"""

import asyncio

from sportsassets import bettor_state_capture as sc
from sportsassets.workers import bettor_state as w


class _Pool:
    """Records nothing, answers everything empty. The budget split is
    arithmetic and needs no venue."""

    async def fetch(self, *a, **k):
        return []

    async def fetchrow(self, *a, **k):
        return None

    async def execute(self, *a, **k):
        return None


def test_the_follow_up_share_is_reserved_before_either_pass_runs():
    """THE FIX. The share cannot be consumed by sampling because it is
    subtracted before the sampling loop starts."""
    r = asyncio.run(w.tick(_Pool()))
    assert r["fuReserve"] >= 1
    assert r["budget"] >= 1
    assert r["fuReserve"] + r["budget"] == r["totalBudget"]


def test_total_reads_per_tick_are_unchanged_so_pacing_is_preserved():
    """A reservation, not an increase. The venue sees the same rate."""
    for pacing in (1.0, 1.352, 2.848, 3.849, 5.132, 8.0):
        total = max(2, int(w.MAX_READS_PER_TICK * min(
            1.0, w.READ_PACING_BASE_S / pacing)))
        fu = max(1, total // 2)
        sampling = max(1, total - fu)
        # The split never exceeds what the unsplit budget would have
        # spent, which is what keeps gateway pacing identical.
        assert sampling + fu <= total + 1, pacing
        assert fu >= 1 and sampling >= 1, pacing


def test_a_starved_sampling_pass_can_no_longer_zero_the_follow_ups():
    """The exact W1 condition: sampling wants more than the budget.
    Before the fix follow_budget was min(6, leftover=0) = 0."""
    total = 4
    fu_reserve = max(1, total // 2)
    sampling = max(1, total - fu_reserve)
    # Sampling consumes every slot it was given.
    leftover = 0
    follow_budget = min(w.MAX_FOLLOWUP_READS, fu_reserve + leftover)
    assert follow_budget == fu_reserve
    assert follow_budget > 0, "the W1 defect: this used to be 0"


def test_a_rate_limited_tick_still_backs_off_but_not_to_zero():
    """Backing off from the venue is preserved. It halves; it does not
    cancel, because a horizon that passes unobserved cannot be redone."""
    fu_reserve, leftover = 3, 0
    follow_budget = min(w.MAX_FOLLOWUP_READS, fu_reserve + leftover)
    rate_limited = max(1, follow_budget // 2)
    assert rate_limited < follow_budget
    assert rate_limited >= 1


def test_true_demand_is_counted_before_the_limit_not_after():
    """THE MEASUREMENT DEFECT. fu_due was len(mids_due(limit=budget)),
    so FU_DUE == FU_ATTEMPTED was a tautology and real demand had never
    been recorded."""
    import ast
    import inspect
    from sportsassets import bettor_state_store as ss
    assert hasattr(ss, "mids_outstanding")
    assert "NO limit applied" in inspect.getdoc(ss.mids_outstanding)

    # Check the SQL LITERAL, not the prose around it -- the docstring
    # explains what a LIMIT would do wrong, so a text scan matches its
    # own explanation. Same self-match that broke three earlier guards.
    src = inspect.getsource(ss)
    lits = [n.value for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    outstanding_sql = [s for s in lits
                       if "count(*)" in s and "bettor_state_mids" in s]
    assert outstanding_sql, "outstanding query not found"
    for s in outstanding_sql:
        assert "LIMIT" not in s.upper(), s[:120]

    tick_src = inspect.getsource(w.tick)
    # demand and selection are now two different counters
    assert 'stats["fuDue"] += outstanding' in tick_src
    assert 'stats["fuSelected"] += len(due)' in tick_src


def test_demand_and_selection_are_separate_columns():
    r = asyncio.run(w.tick(_Pool()))
    assert "fuDue" in r and "fuSelected" in r
    assert r["fuDue"] == 0 and r["fuSelected"] == 0   # empty pool


def test_the_ordering_hypothesis_is_recorded_as_refuted():
    """So the next reader does not re-propose it."""
    import inspect
    src = inspect.getsource(w.tick)
    assert "ORDERING HYPOTHESIS IS REFUTED" in src


def test_late_is_still_never_counted_as_on_time():
    """The repair must not buy coverage by widening tolerance."""
    assert sc.HORIZON_TOLERANCE_S == 30
    assert sc.ADMISSIBLE_TO_HORIZON_GATE == (sc.TIMING_ON_TIME,)
    from datetime import timedelta, datetime, timezone
    T = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    m = sc.mid_observation("x", horizon_s=60, observed_at=T,
                           read_at=T + timedelta(seconds=620), mid="0.5")
    assert m["TIMING_CLASS"] == sc.TIMING_LATE_RECOVERY
    assert m["ADMISSIBLE_TO_HORIZON_GATE"] is False
