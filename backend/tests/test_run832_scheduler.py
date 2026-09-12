"""Run 83.2: the B+C scheduler, the plan/result model, and restart semantics.

THE CENTRAL TEST IN THIS FILE is test_a_pending_slot_holds_no_network_concurrency.
It is the arithmetic that killed V1, written down: V1's ceiling was
max_inflight / horizon = 8/60 = 0.133 events/s against an RN1-only arrival rate
of 0.187-0.255/s, so the architecture failed even after the population was
corrected. If someone reintroduces a per-event concurrency hold, that test names
the number it reintroduces.
"""
from __future__ import annotations

import ast
import pathlib
import time

from sportsassets.obs import cache as obs_cache
from sportsassets.obs import clock, scheduler
from sportsassets.obs.book import ObservationChannel
from sportsassets.obs.config import OFFSETS, window_for
from sportsassets.obs.scheduler import SampleStatus
from sportsassets.obs.slot import PREREGISTRATION_VERSION, slot_id

_OBS = pathlib.Path(__file__).resolve().parent.parent / "sportsassets" / "obs"
_CHANNEL = ObservationChannel.LOCAL_CACHE_BATCH_POLL_PATH


def _receipt(mono: float | None = None) -> clock.Instant:
    inst = clock.now()
    if mono is None:
        return inst
    return clock.Instant(monotonic=mono, wall=inst.wall)


def _plan(receipt=None, token="tok-1"):
    return scheduler.plan_for_event(
        obs_event_id="11111111-1111-1111-1111-111111111111",
        source_event_id="evt-a", receipt=receipt or _receipt(),
        channel=_CHANNEL, source_token_id=token)


# ------------------------------------------------------------- slot identity
def test_slot_ids_are_deterministic_and_distinct():
    a = slot_id(source_event_id="e", observation_channel=_CHANNEL,
                target_offset_ms=0)
    assert a == slot_id(source_event_id="e", observation_channel=_CHANNEL,
                        target_offset_ms=0)
    assert a != slot_id(source_event_id="e", observation_channel=_CHANNEL,
                        target_offset_ms=100)
    assert a != slot_id(source_event_id="e",
                        observation_channel="LEGACY_COMPARABLE_BOOK_PATH",
                        target_offset_ms=0)
    assert a != slot_id(source_event_id="e", observation_channel=_CHANNEL,
                        target_offset_ms=0,
                        preregistration_version="RUN83_PREREG_V1")


def test_the_preregistration_version_is_part_of_the_slot_identity():
    """Otherwise an amended cohort's slots collide with the old cohort's.

    migration 063's unique key is (source_event_id, channel, offset, version).
    Without the version in the derived id, re-observing the same event under V2
    would hash to V1's slot id and the insert would be refused -- making the
    amendment silently unenforceable.
    """
    assert PREREGISTRATION_VERSION == "RUN83_PREREG_V2"
    slots = _plan()
    assert all(s.preregistration_version == "RUN83_PREREG_V2" for s in slots)


def test_the_whole_plan_exists_before_any_sampling():
    """Ten immutable intentions per event, one per pre-registered offset."""
    slots = _plan()
    assert len(slots) == len(OFFSETS) == 10
    assert [s.target_offset_ms for s in slots] == [
        0, 100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000]
    # The windows are the frozen rule, not widened (owner decision 1).
    for s, (_, target_s) in zip(slots, OFFSETS):
        assert s.window_ms == int(round(window_for(target_s) * 1000))
    assert [s.window_ms for s in slots] == [50, 50, 50, 100, 200, 400, 1000,
                                            2000, 6000, 12000]


def test_targets_are_the_anchor_plus_the_offset_exactly():
    r = _receipt(mono=1000.0)
    slots = _plan(receipt=r)
    assert [s.target_monotonic for s in slots] == [
        1000.0, 1000.1, 1000.25, 1000.5, 1001.0, 1002.0, 1005.0, 1010.0,
        1030.0, 1060.0]
    assert all(s.receipt_monotonic == 1000.0 for s in slots)


# --------------------------------------------------------------- the scheduler
def test_a_pending_slot_holds_no_network_concurrency():
    """DELIBERATELY FAILING PRE-FIX: V1's semaphore WAS the scheduler.

    V1 held one Semaphore(max_inflight()) slot per event for the event's whole
    60-second horizon. Capacity was therefore 8/60 = 0.133 events/s against a
    measured RN1-only arrival rate of 0.187/s (busiest day) to 0.255/s (the
    activation window) -- it failed by 1.4x to 1.9x EVEN AT THE CORRECT
    POPULATION. So the scheduler must not import or construct a Semaphore at
    all, and pending slots must cost only memory.
    """
    # Checked in the AST: the module docstring DESCRIBES the semaphore V1 held,
    # and a substring scan cannot tell that description from a reintroduction.
    tree = ast.parse((_OBS / "scheduler.py").read_text())
    used = {getattr(n, "attr", None) for n in ast.walk(tree)
            if isinstance(n, ast.Attribute)}
    used |= {getattr(n, "id", None) for n in ast.walk(tree)
             if isinstance(n, ast.Name)}
    assert "Semaphore" not in used, (
        "obs/scheduler.py constructs or references a Semaphore in code. A "
        "pending observation must never hold network concurrency: V1's ceiling "
        "was 8/60 = 0.133 events/s against an arrival rate of 0.187-0.255/s.")
    sched = scheduler.DueTimeScheduler()
    # 60 events' worth of slots -- five minutes of RN1 flow at the design rate --
    # all pending at once, which V1 could not have held.
    for i in range(60):
        sched.push_all(scheduler.plan_for_event(
            obs_event_id=f"{i:032x}", source_event_id=f"evt-{i}",
            receipt=_receipt(), channel=_CHANNEL, source_token_id="t"))
    assert sched.pending == 600


def test_slots_come_back_in_due_order_not_arrival_order():
    """A 0 ms slot admitted later must not queue behind an older 60 s slot."""
    sched = scheduler.DueTimeScheduler()
    old = _plan(receipt=_receipt(mono=time.monotonic()), token="old")
    sched.push(old[-1])                       # the 60 s slot, pushed first
    new = _plan(receipt=_receipt(mono=time.monotonic()), token="new")
    sched.push(new[0])                        # a 0 ms slot, pushed second
    ready = sched.pop_ready(time.monotonic() + 0.001)
    assert [s.target_offset_ms for s in ready] == [0]
    assert sched.pending == 1


def test_pop_ready_returns_nothing_before_the_target():
    sched = scheduler.DueTimeScheduler()
    sched.push(_plan(receipt=_receipt(mono=time.monotonic() + 100))[0])
    assert sched.pop_ready(time.monotonic()) == []
    assert sched.pending == 1


# ------------------------------------------------------- the decision table
def _sample(age_ms=10.0, validity=obs_cache.StateValidity.VALID, state=True,
            at_mono=None):
    inst = clock.now()
    if at_mono is not None:
        inst = clock.Instant(monotonic=at_mono, wall=inst.wall)
    st = None
    if state:
        st = obs_cache.TokenState(
            token_id="tok-1", received=clock.now(), bids=[(0.40, 100.0)],
            asks=[(0.42, 250.0)], venue_hash="h1")
    return obs_cache.Sample(token_id="tok-1", state=st, sampled_at=inst,
                            validity=validity, cache_age_ms=age_ms)


def test_a_sample_inside_its_window_is_captured_and_carries_its_age():
    slot = _plan(receipt=_receipt(mono=1000.0))[0]     # 0 ms, window 50 ms
    row = scheduler.resolve_local(slot, _sample(age_ms=12.5, at_mono=1000.02))
    assert row["status"] == SampleStatus.CAPTURED_LOCAL
    assert row["cache_age_ms"] == 12.5
    assert row["venue_book_hash"] == "h1"
    assert row["best_ask"] == 0.42
    assert row["continuity_status"] == obs_cache.Continuity.UNVERIFIED


def test_a_capture_can_never_lack_its_cache_age():
    """The schema CHECK says so too; this is the code-side half.

    A local sample without its age is indistinguishable from a fresh reading --
    the single most misleading row this table could hold.
    """
    slot = _plan(receipt=_receipt(mono=1000.0))[0]
    row = scheduler.resolve_local(slot, _sample(age_ms=None, at_mono=1000.02))
    assert row["status"] == SampleStatus.CACHE_INVALID, (
        "state with no measurable age was written as a CAPTURE. migration 063's "
        "rn1_obs_samples_capture_has_age_ck would reject the row; the resolver "
        "must refuse it with a reason instead of producing an insert error.")
    assert "without its staleness is not a reading" in row["miss_reason"]


def test_a_late_sample_is_a_miss_not_a_late_reading():
    """Owner decision 1's rule: a sample is a reading AT t, not after t."""
    slot = _plan(receipt=_receipt(mono=1000.0))[0]      # expires at 1000.05
    row = scheduler.resolve_local(slot, _sample(at_mono=1000.20))
    assert row["status"] == SampleStatus.MISSED_WINDOW
    assert row["sample_lateness_ms"] == 200.0
    # Lateness is measured against the TARGET, not against the window's edge:
    # sample_lateness_ms is the quantity the analysis uses, so the reason string
    # states the same number rather than a second, differently-derived one.
    assert "sampled 200.0ms late" in row["miss_reason"]
    assert "window 50ms" in row["miss_reason"]


def test_a_late_admission_is_named_separately_from_a_late_sample():
    """Two different failures; V1 recorded both as MISSED_WINDOW.

    One says the instrument could not start in time, the other that it could not
    arrive in time. V1's 72-second admission lag produced misses
    indistinguishable from an oversleeping scheduler.
    """
    slot = _plan(receipt=_receipt(mono=1000.0))[0]
    row = scheduler.resolve_local(slot, _sample(at_mono=1000.02),
                                  admission_monotonic=1000.40)
    assert row["status"] == SampleStatus.MISSED_ADMISSION_LATE
    assert "admitted at +400.0ms" in row["miss_reason"]


def test_no_local_state_is_a_finding_not_a_dropped_row():
    slot = _plan(receipt=_receipt(mono=1000.0))[0]
    row = scheduler.resolve_local(
        slot, _sample(state=False, age_ms=None,
                      validity=obs_cache.StateValidity.NEVER, at_mono=1000.01))
    assert row["status"] == SampleStatus.CACHE_MISS
    assert row["cache_age_ms"] is None


def test_stale_state_is_recorded_invalid_with_its_age_not_served_as_a_reading():
    slot = _plan(receipt=_receipt(mono=1000.0))[0]
    row = scheduler.resolve_local(
        slot, _sample(age_ms=9000.0, validity=obs_cache.StateValidity.STALE,
                      at_mono=1000.01))
    assert row["status"] == SampleStatus.CACHE_INVALID
    assert row["cache_age_ms"] == 9000.0
    assert "STALE_BEYOND_TOLERANCE" in row["miss_reason"]


def test_invalidated_state_after_a_failure_is_not_a_reading():
    slot = _plan(receipt=_receipt(mono=1000.0))[0]
    row = scheduler.resolve_local(
        slot, _sample(validity=obs_cache.StateValidity.INVALIDATED,
                      at_mono=1000.01))
    assert row["status"] == SampleStatus.CACHE_INVALID


def test_a_local_sample_never_carries_an_http_request():
    """The schema forbids it; so must the resolver's output."""
    slot = _plan(receipt=_receipt(mono=1000.0))[0]
    row = scheduler.resolve_local(slot, _sample(at_mono=1000.01))
    assert row.get("request_start_wall") is None
    assert row.get("response_wall") is None


# --------------------------------------------------------- restart semantics
def test_a_lost_slot_is_resolved_against_its_original_plan():
    """Owner decision 7: no new process reconstructs a dead monotonic deadline."""
    slot = _plan(receipt=_receipt(mono=1000.0))[4]
    row = scheduler.resolve_restarted(slot, boot_id="new-boot")
    assert row["status"] == SampleStatus.PROCESS_RESTARTED
    assert row["observation_slot_id"] == slot.observation_slot_id
    assert row["target_monotonic"] == 1001.0       # the ORIGINAL deadline
    assert row["process_boot_id"] == "new-boot"    # but THIS process
    assert row["actual_sample_monotonic"] is None
    assert row["sample_lateness_ms"] is None, (
        "a lateness was computed across a process boundary. There is no common "
        "clock in which that number means anything -- which is exactly what "
        "clock.elapsed_between raises to prevent.")


def test_the_restart_row_does_not_invent_a_reading():
    slot = _plan(receipt=_receipt(mono=1000.0))[0]
    row = scheduler.resolve_restarted(slot, boot_id="b")
    for field in ("best_bid", "best_ask", "cache_age_ms", "venue_book_hash"):
        assert row.get(field) is None


def test_the_collector_records_restart_losses_rather_than_discarding_them():
    src = (_OBS / "collector.py").read_text()
    tree = ast.parse(src)
    run = next(n for n in ast.walk(tree)
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "run")
    finallies = [n for n in ast.walk(run) if isinstance(n, ast.Try) and n.finalbody]
    assert finallies, "run() has no finally block to account for lost slots"
    body = "\n".join(ast.dump(s) for f in finallies for s in f.finalbody)
    assert "resolve_restarted" in body and "drain_all" in body, (
        "run()'s shutdown path does not resolve pending slots as "
        "PROCESS_RESTARTED_BEFORE_CAPTURE; they would vanish instead of dating "
        "their own loss")
