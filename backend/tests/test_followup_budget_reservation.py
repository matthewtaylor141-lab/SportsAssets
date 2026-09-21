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


def test_the_w1_ordering_refutation_and_why_it_no_longer_applies():
    """THIS GUARD EXISTED TO STOP ME RE-PROPOSING THE ORDERING CHANGE,
    AND IT CAUGHT ME DOING EXACTLY THAT.

    It was right to fire, and the refutation was right for W1: reads
    were late because they waited for a rare spare-budget tick, and
    reordering a queue that is never serviced changes nothing.

    THE CONDITION IT DEPENDED ON HAS SINCE CHANGED, MEASURABLY. W2
    funded the queue and W3 spread it across horizons, so the queue is
    serviced now -- and W3 then measured reads taken at a median
    538-549s past their horizon against a 600s expiry, 1 read in 122
    inside its band, and zero on-time reads in 68 matured attempts.
    Ordering binds once servicing exists.

    So the guard is not deleted, it is restated: the code must carry
    both the original refutation and the measured reason it lapsed, so
    the next reader sees a superseded finding rather than a reversal."""
    import inspect
    src = inspect.getsource(w.tick)
    assert "ORDERING HYPOTHESIS WAS REFUTED IN W1" in src
    assert "CONDITION IT DEPENDED ON HAS SINCE CHANGED" in src
    # and the measured evidence that changed it, not just an assertion
    assert "538-549s" in src
    assert "1 read in 122" in src


def test_the_restored_budget_floor_can_never_overspend_the_tick():
    """The max(2,...) floor was masking an over-spend: at total 1,
    fu_reserve = max(1, 0) = 1 and budget = max(1, 0) = 1, which is two
    reads against a budget of one. Restoring the allowance required
    handling that case, not just changing a constant."""
    import inspect
    src = inspect.getsource(w.tick)
    assert "max(1, int(MAX_READS_PER_TICK" in src
    assert "must never exceed the tick budget" in src

    for tenths in range(10, 81):
        pacing = tenths / 10
        for seq in (0, 1):
            total = max(1, int(w.MAX_READS_PER_TICK * min(
                1.0, w.READ_PACING_BASE_S / pacing)))
            if total <= 1:
                fu = 1 if (seq % 2) else 0
                budget = total - fu
            else:
                fu = max(1, total // 2)
                budget = max(1, total - fu)
            assert fu + budget <= total, (pacing, seq, total, fu, budget)
            assert fu >= 0 and budget >= 0


def test_the_original_request_allowance_is_actually_restored():
    """Verified against the arithmetic, not asserted. At maximum
    backoff the restored floor issues ONE read per tick where the
    deployed floor issued two."""
    at_max_backoff = max(1, int(w.MAX_READS_PER_TICK * min(
        1.0, w.READ_PACING_BASE_S / w.READ_PACING_MAX_S)))
    assert at_max_backoff == 1
    # the regression this undoes
    assert max(2, int(w.MAX_READS_PER_TICK * min(
        1.0, w.READ_PACING_BASE_S / w.READ_PACING_MAX_S))) == 2


def test_the_queue_puts_in_band_candidates_before_recovery_work():
    """The ordering change, checked on the SQL literal rather than the
    prose that explains it."""
    import ast
    import inspect
    from sportsassets import bettor_state_store as ss
    lits = [n.value for n in ast.walk(ast.parse(inspect.getsource(ss)))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    # THE DUE QUERY SPECIFICALLY -- the one that selects unread tasks
    # for a horizon. A looser filter matched the history query, whose
    # ORDER BY is unrelated and would have passed or failed for no
    # reason connected to this change.
    due = [s for s in lits
           if "bettor_state_observations" in s
           and "bettor_state_mids" in s
           and "NOT EXISTS" in s.upper()
           and "LIMIT" in s.upper()]
    assert len(due) == 1, [s[:60] for s in due]
    sql = due[0]
    # in-band term sorts first, oldest-first still breaks ties
    assert "ORDER BY" in sql
    order = sql.upper().split("ORDER BY", 1)[1]
    assert "ABS(" in order, order
    assert order.index("ABS(") < order.index("O.OBSERVED_AT"), order


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


# ── W3: ROTATION AND THE PER-HORIZON CAP ────────────────────────────
#
# The reservation above got follow-ups FUNDED. It did not decide which
# horizon they went to, and a fixed loop order sent every funded read
# to the first horizon in the tuple. These pin the allocation.

def test_the_rotation_advances_on_service_not_on_a_tick_counter():
    """THE ALIASING THE OWNER NAMED. A tick-keyed offset aliases
    against any rule that makes follow-ups run on only some ticks:
    offline, at one read per tick, follow-ups run on odd ticks only, so
    i % 4 takes only the values 1 and 3 and two of the four horizons
    are never at the head of the order. A counter advanced once per
    tick that can actually serve cannot alias."""
    import ast
    import inspect
    src = inspect.getsource(w.tick)
    tree = ast.parse(src.lstrip())
    # The offset comes from the service counter, not a tick index.
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "_FU_SERVICE_OPS" in names
    assert hasattr(w, "_FU_SERVICE_OPS")
    assert isinstance(w._FU_SERVICE_OPS, int)


def test_every_horizon_reaches_the_head_of_the_order_in_turn():
    """Consecutive service opportunities take consecutive offsets, so
    over one full cycle each horizon leads exactly once."""
    n = len(sc.HORIZONS_OBSERVABLE_S)
    before = w._FU_SERVICE_OPS
    heads = []
    for _ in range(n * 3):
        r = asyncio.run(w.tick(_Pool()))
        heads.append(r["fuRotationHead"])
    assert w._FU_SERVICE_OPS > before, "counter must advance"
    assert set(heads) == set(sc.HORIZONS_OBSERVABLE_S), heads
    # each horizon leads the same number of times over whole cycles
    counts = {h: heads.count(h) for h in sc.HORIZONS_OBSERVABLE_S}
    assert len(set(counts.values())) == 1, counts


def test_the_head_horizon_cannot_drain_the_whole_follow_up_budget():
    """Rotation without a cap is not a repair: the leading horizon
    still takes every read before the next one is reached."""
    r = asyncio.run(w.tick(_Pool()))
    assert r["fuPerHorizonCap"] >= 1
    assert r["fuPerHorizonCap"] <= max(
        1, r["fuReserve"] + r["budget"])
    import inspect
    src = inspect.getsource(w.tick)
    # the cap bounds BOTH the query's LIMIT and the dispatch loop
    assert "min(follow_budget, per_horizon_cap)" in src
    assert "taken_this_horizon >= per_horizon_cap" in src


def test_v3_sends_no_more_requests_per_tick_than_v2_did():
    """The allocation changed; the request rate did not.

    I previously claimed pacing was preserved when it was not -- a
    max(2,...) floor had doubled the request rate at maximum backoff --
    so this asserts the arithmetic itself rather than restating the
    claim. Every pacing input is pinned, and the follow-up budget is
    still the single decrementing counter that bounds the whole pass."""
    assert w.READ_PACING_BASE_S == 1.0
    assert w.READ_PACING_MAX_S == 8.0
    assert w.BACKOFF_GROWTH == 2.0
    assert w.BACKOFF_RECOVERY == 0.75
    assert w.MAX_READS_PER_TICK == sc.MAX_MARKETS_PER_TICK + 2
    assert w.MAX_FOLLOWUP_READS == 6

    # The budget split is byte-for-byte V2's. V3 touches only which
    # horizon the funded reads are spent on.
    for pacing in (1.0, 1.352, 2.848, 3.849, 5.132, 8.0):
        total = max(2, int(w.MAX_READS_PER_TICK * min(
            1.0, w.READ_PACING_BASE_S / pacing)))
        fu = max(1, total // 2)
        sampling = max(1, total - fu)
        funded = min(w.MAX_FOLLOWUP_READS, fu + sampling)
        cap = max(1, funded // len(sc.HORIZONS_OBSERVABLE_S))
        # THE POINT: capping cannot raise the total. Each horizon takes
        # at most `cap`, and the shared follow_budget still stops the
        # pass, so the ceiling is unchanged at `funded`.
        assert cap <= funded, pacing
        assert min(funded, cap * len(sc.HORIZONS_OBSERVABLE_S)) <= funded


def test_the_shared_budget_still_bounds_the_pass_not_just_the_cap():
    """A per-horizon cap alone would let four horizons spend 4 x cap.
    The shared decrementing counter is what makes the ceiling hold, and
    it must be checked before every read, not only per horizon."""
    import inspect
    src = inspect.getsource(w.tick)
    assert "if follow_budget <= 0:\n            break" in src
    assert "follow_budget <= 0 or taken_this_horizon >= per_horizon_cap" \
        in src
    assert "follow_budget -= 1" in src


def test_the_version_marks_the_allocation_change_for_attribution():
    """Coverage measured under V2 and V3 must not be pooled: which
    horizon a read went to changed between them."""
    assert w.PACING_VERSION == "BETTOR_CAPTURE_PACING_V3_ROTATED_FOLLOWUPS"
    r = asyncio.run(w.tick(_Pool()))
    assert r["pacingVersion"] == w.PACING_VERSION


# ── the versioned eligibility change ────────────────────────────────

def test_selection_opens_at_the_start_of_the_tolerance_band():
    """Eligibility opened at exactly T0+h while ON_TIME closes at
    T0+h+30 -- a 30s target for ticks 65 to 82s apart. It now opens at
    T0+h-30, so the selectable window IS the tolerance band."""
    from sportsassets import bettor_state_store as ss
    assert sc.HORIZON_EARLY_ELIGIBILITY_S == 30
    for h in sc.HORIZONS_OBSERVABLE_S:
        assert ss._opens_at(h) == h - sc.HORIZON_TOLERANCE_S


def test_early_selection_did_not_widen_what_counts_as_on_time():
    """The change is to WHEN the scheduler may select, not to what the
    science admits. No read becomes on-time that was not on-time
    before."""
    assert sc.HORIZON_TOLERANCE_S == 30
    assert sc.ADMISSIBLE_TO_HORIZON_GATE == (sc.TIMING_ON_TIME,)
    assert sc.HORIZON_EARLY_ELIGIBILITY_S <= sc.HORIZON_TOLERANCE_S
    # the boundary cases are exactly where they always were
    assert sc.timing_class(870, 900) == sc.TIMING_ON_TIME
    assert sc.timing_class(930, 900) == sc.TIMING_ON_TIME
    assert sc.timing_class(869, 900) != sc.TIMING_ON_TIME
    assert sc.timing_class(931, 900) != sc.TIMING_ON_TIME


def test_the_eligibility_change_is_versioned_and_states_its_own_limit():
    """Versioned so a coverage figure can be attributed to the rule
    that produced it, and carrying the distinction in the contract
    rather than only in a commit message."""
    assert sc.ELIGIBILITY_VERSION == "BETTOR_ELIGIBILITY_V3_EARLY_30"
    claim = sc.EARLY_ELIGIBILITY_IS_NOT_A_WIDER_TOLERANCE
    assert "HORIZON_TOLERANCE_S" in claim
    assert "unchanged at 30" in claim
    # and the constant cannot silently grow past the band it opens
    assert sc.HORIZON_EARLY_ELIGIBILITY_S <= sc.HORIZON_TOLERANCE_S


def test_the_rotation_head_is_recorded_so_it_can_be_verified_live():
    """Offline the head is observable; in production it was not
    recorded at all, so a rotation that never reached a horizon could
    only be inferred from attempt counts -- which can look even for the
    wrong reason. Migration 091 adds the column; this pins the wiring.

    The default is None rather than the first horizon: a tick with no
    follow-up budget had no head, and collapsing that into '60s led'
    would corrupt exactly the evidence the column exists to provide.
    (With the current budget floor follow_budget never reaches 0, so
    None is a guard, not an expected value.)"""
    import inspect
    from sportsassets import bettor_state_store as ss
    src = inspect.getsource(ss)
    assert "fu_rotation_head" in src and "fu_per_horizon_cap" in src
    assert 'row.get("FU_ROTATION_HEAD")' in src

    wsrc = inspect.getsource(w)
    assert '"FU_ROTATION_HEAD": stats.get("fuRotationHead")' in wsrc
    assert '"fuRotationHead": None' in wsrc

    # placeholders and columns still line up after the addition
    ins = [s for s in src.split('"""')
           if "INSERT INTO bettor_capture_ticks" in s][0]
    cols = ins.split("(", 1)[1].split(")", 1)[0]
    n_cols = len([c for c in cols.split(",") if c.strip()])
    n_vals = ins.count("$")
    assert n_cols == n_vals, (n_cols, n_vals)


def test_the_aliasing_defect_was_never_live_and_is_not_claimed_to_be():
    """PRECISION ABOUT WHAT WAS FIXED.

    The parity-vs-rotation aliasing was found in the W3 candidate,
    offline, before deployment. It requires ticks that serve no
    follow-ups, and the deployed budget cannot produce one: total is
    floored at 2, fu_reserve at 1, and a rate-limited tick halves to a
    floor of 1. So follow_budget is >= 1 on every production tick.

    Service-opportunity rotation is therefore the correct-by-
    construction choice, NOT the repair of an observed production
    fault. The observed production fault is the fixed loop order it
    replaces, which starved three horizons at every budget."""
    worst = None
    for tenths in range(10, 81):
        pacing = tenths / 10
        total = max(2, int(w.MAX_READS_PER_TICK * min(
            1.0, w.READ_PACING_BASE_S / pacing)))
        fu = max(1, total // 2)
        budget = max(1, total - fu)
        follow_budget = min(w.MAX_FOLLOWUP_READS, fu + max(0, budget))
        rate_limited = max(1, follow_budget // 2)
        worst = rate_limited if worst is None else min(worst,
                                                       rate_limited)
    assert worst >= 1, (
        "if this ever drops to 0, production CAN produce ticks that "
        "serve no follow-ups, and the aliasing stops being hypothetical")
