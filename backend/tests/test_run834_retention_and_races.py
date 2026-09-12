"""RUN 83.4 -- retention by time, capture at due time, and the 0 ms races.

THE DEFECT THIS FILE EXISTS BECAUSE OF. Run 83.3 retained "the last 256 states"
per token. The stream's message rate is unknown -- measuring it is one of the
things passive observation is for -- so 256 has no temporal meaning at all. At a
rate high enough, the genuine pre-receipt state is evicted by later churn before
the 0 ms rule ever reads it, and the instrument then answers from a POST-receipt
book with nothing in the row to say so. That is the worst class of defect this
project has: a wrong number that looks exactly like a right one.

TWO INDEPENDENT REPAIRS, AND THE SECOND IS THE ONE THAT MATTERS:

    1. retention is by TIME HORIZON (60 s ladder + 15 s headroom), so what is
       kept is stated in seconds; and
    2. a due observation is CAPTURED OUT OF THE BUFFER AT DUE TIME into an
       immutable SlotObservation, after which no amount of churn can touch it.

Because of (2), buffer overflow stops being a scientific loss. The tests below
prove that by pushing 100,000 updates through a channel AFTER capture and
checking every observation is byte-identical.

Times here are SYNTHETIC clock.Instants rather than real sleeps. The capture and
selection functions are pure functions of the instants handed to them, so
constructing the instants tests exactly the arithmetic under test -- and lets a
one-microsecond race be expressed exactly instead of hoped for.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from sportsassets.obs import clock, pmus_stream, streamstate
from sportsassets.obs.config import OFFSETS
from sportsassets.obs.streamstate import SelectionOutcome, StreamChannel

MICROSECOND = 1e-6


def _at(base: clock.Instant, offset_s: float) -> clock.Instant:
    return clock.Instant(monotonic=base.monotonic + offset_s,
                         wall=base.wall + timedelta(seconds=offset_s),
                         process_boot_id=base.process_boot_id,
                         process_identity=base.process_identity)


def _frame(slug="m", bid=0.40, ask=0.42, transact="1700000000000"):
    return {"marketData": {"marketSlug": slug,
                           "bids": [{"px": str(bid), "qty": "500"}],
                           "offers": [{"px": str(ask), "qty": "400"}],
                           "transactTime": transact}}


# =====================================================================
# THE 256 DEPENDENCY IS GONE
# =====================================================================
def test_retention_is_stated_in_seconds_and_covers_the_whole_ladder():
    horizon = pmus_stream.RETAIN_HORIZON_S
    longest = max(t for _, t in OFFSETS)
    assert horizon == longest + streamstate.RETENTION_HEADROOM_S
    assert horizon > longest, (
        "the retention horizon does not outlast the 60 s slot, so the last "
        "offset could be asked to resolve against state already pruned")
    assert streamstate.RETENTION_HEADROOM_S >= 15.0


def test_no_module_retains_state_by_an_arbitrary_message_count():
    """The literal 256 must not come back as a retention parameter."""
    import ast
    import pathlib

    obs = pathlib.Path(__file__).resolve().parents[1] / "sportsassets" / "obs"
    for path in sorted(obs.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.arg) and node.arg == "retain":
                raise AssertionError(
                    f"{path.name} still takes a count-based `retain` parameter")
    hist = streamstate.TokenStateHistory(
        channel=StreamChannel.PMUS_FAST_STREAM_PATH, token_id="m")
    assert hasattr(hist, "retain_horizon_s")
    assert not hasattr(hist, "retain")


def test_more_than_256_updates_in_one_second_lose_no_pre_receipt_state():
    """THE REGRESSION TEST FOR THE OLD CAP, stated as the old cap's failure.

    500 frames land in the second following the anchor. Under count-based
    retention of 256 the pre-anchor state is evicted and the 0 ms rule has
    nothing at or before the anchor to select. Under a time horizon it is
    milliseconds old and plainly present.
    """
    base = clock.now()
    s = pmus_stream.PmusStreamState(feed_session_id="stress")

    s.on_frame(_frame(bid=0.11), receive=_at(base, -0.001))   # the genuine one
    anchor = base
    for i in range(500):
        s.on_frame(_frame(bid=0.90), receive=_at(base, 0.001 + i * 0.002))

    hist = s.history("m")
    assert len(hist) > 256, "the stress did not exceed the old cap"

    sel = streamstate.select_state_at(hist, anchor, stale_tolerance_s=5.0)
    assert sel.selected
    assert sel.state.best_bid == 0.11, (
        "the pre-receipt state was lost to churn; this is exactly the defect "
        "the 256-message cap produced")
    assert sel.state_age_at_receipt_ms == pytest.approx(1.0, abs=1e-6)


def test_every_scheduled_observation_survives_100000_later_updates():
    """The capture contract: once due, an observation is out of harm's way.

    All ten slots are captured at their due instants, then the channel is
    hammered with 100,000 further frames -- two orders of magnitude past any
    buffer bound. Every observation must be byte-identical afterwards.
    """
    base = clock.now()
    s = pmus_stream.PmusStreamState(feed_session_id="stress")
    s.on_frame(_frame(bid=0.11), receive=_at(base, -0.001))
    anchor = base

    # 0 ms is captured AT STAMP TIME, before any of the churn below -- which is
    # what the real flow does and why admission delay cannot reach it.
    observations = {"0ms": streamstate.capture_zero_ms(
        s.history("m"), observation_slot_id="slot-0ms",
        observation_channel=pmus_stream.CHANNEL, anchor=anchor,
        stale_tolerance_s=5.0)}

    for label, target_s in OFFSETS:
        if target_s == 0.0:
            continue
        # frames keep arriving between slots
        for i in range(30):
            s.on_frame(_frame(bid=0.5 + i / 1000),
                       receive=_at(base, target_s * 0.99 + i * 1e-5))
        due = _at(base, target_s)
        observations[label] = streamstate.capture_for_slot(
            s.history("m"), observation_slot_id=f"slot-{label}",
            observation_channel=pmus_stream.CHANNEL,
            target_offset_ms=int(round(target_s * 1000)),
            anchor=anchor, captured_at=due, stale_tolerance_s=90.0)

    assert len(observations) == 10
    assert all(o.captured for o in observations.values()), {
        k: v.status for k, v in observations.items()}

    before = dict(observations)
    for i in range(100_000):
        s.on_frame(_frame(bid=0.99), receive=_at(base, 61.0 + i * 1e-4))

    assert observations == before, "a captured observation was disturbed"
    assert observations["0ms"].state.best_bid == 0.11
    # And the buffer did move on, so the test really did exercise churn.
    assert s.frames > 100_000


def test_the_overflow_guard_counts_rather_than_dropping_quietly():
    """A memory guard that fires silently is indistinguishable from working."""
    base = clock.now()
    hist = streamstate.TokenStateHistory(
        channel=StreamChannel.PMUS_FAST_STREAM_PATH, token_id="m",
        retain_horizon_s=75.0, overflow_cap=100)
    for i in range(500):
        hist.append(streamstate.StreamState(
            channel=hist.channel, token_id="m",
            receive=_at(base, i * 1e-4), feed_session_id="s",
            best_bid=float(i)))
    assert len(hist) == 100
    assert hist.overflow_events > 0, "the cap bound without recording that it did"
    assert hist.pruned >= 400


def test_pruning_is_by_age_not_by_count():
    base = clock.now()
    hist = streamstate.TokenStateHistory(
        channel=StreamChannel.PMUS_FAST_STREAM_PATH, token_id="m",
        retain_horizon_s=10.0)
    for i in range(40):
        hist.append(streamstate.StreamState(
            channel=hist.channel, token_id="m", receive=_at(base, i),
            feed_session_id="s", best_bid=float(i)))
    # 40 states one second apart, 10 s horizon -> the old ones are gone by TIME.
    assert len(hist) == 11, len(hist)
    assert hist.latest.best_bid == 39.0
    assert hist.pruned == 29


def test_a_captured_observation_is_immutable_all_the_way_down():
    """StreamState is frozen but its ladders are lists. Capture freezes those."""
    base = clock.now()
    s = pmus_stream.PmusStreamState(feed_session_id="x")
    live = s.on_frame(_frame(), receive=base)
    obs = streamstate.capture_zero_ms(
        s.history("m"), observation_slot_id="z",
        observation_channel=pmus_stream.CHANNEL, anchor=base,
        stale_tolerance_s=5.0)

    assert isinstance(obs.state.bids, tuple)
    assert isinstance(obs.state.asks, tuple)
    live.bids.append((0.99, 1.0))          # mutate the LIVE state's ladder
    assert obs.state.bids == ((0.40, 500.0),), (
        "the observation shares a mutable ladder with live state")
    with pytest.raises(Exception):
        obs.state.bids = ()                # frozen dataclass


# =====================================================================
# THE 0 MS CONTRACT, UNDER RACES
# =====================================================================
def test_race_update_one_microsecond_before_receipt_is_the_zero_ms_state():
    base = clock.now()
    s = pmus_stream.PmusStreamState(feed_session_id="r")
    s.on_frame(_frame(bid=0.33), receive=_at(base, -MICROSECOND))
    sel = streamstate.select_state_at(s.history("m"), base, stale_tolerance_s=5.0)
    assert sel.selected and sel.state.best_bid == 0.33
    assert sel.state_age_at_receipt_ms == pytest.approx(0.001, abs=1e-9)


def test_race_exactly_equal_monotonic_is_admissible_because_the_rule_says_le():
    """`<=`, not `<`. A state that arrived AT the anchor was in memory at it."""
    base = clock.now()
    s = pmus_stream.PmusStreamState(feed_session_id="r")
    s.on_frame(_frame(bid=0.44), receive=base)
    sel = streamstate.select_state_at(s.history("m"), base, stale_tolerance_s=5.0)
    assert sel.selected, "a state at exactly the anchor was refused"
    assert sel.state.best_bid == 0.44
    assert sel.state_age_at_receipt_ms == 0.0


def test_race_update_one_microsecond_after_receipt_is_not_the_zero_ms_state():
    base = clock.now()
    s = pmus_stream.PmusStreamState(feed_session_id="r")
    s.on_frame(_frame(bid=0.33), receive=_at(base, -0.010))
    s.on_frame(_frame(bid=0.99), receive=_at(base, +MICROSECOND))
    sel = streamstate.select_state_at(s.history("m"), base, stale_tolerance_s=5.0)
    assert sel.selected
    assert sel.state.best_bid == 0.33, (
        "a state that arrived AFTER receipt was served as the 0 ms state")


def test_race_only_post_receipt_state_is_named_not_silently_substituted():
    base = clock.now()
    s = pmus_stream.PmusStreamState(feed_session_id="r")
    s.on_frame(_frame(bid=0.99), receive=_at(base, +MICROSECOND))
    sel = streamstate.select_state_at(s.history("m"), base, stale_tolerance_s=5.0)
    assert sel.outcome == SelectionOutcome.NO_VALID_PRE_RECEIPT_STATE
    assert sel.state is None and sel.state_age_at_receipt_ms is None


def test_race_admission_delay_cannot_replace_the_genuine_zero_ms_state():
    """THE was_insert RACE, which is the one that actually bites in production.

    The anchor is stamped, then the canonical INSERT ... RETURNING (xmax = 0)
    round-trips to Postgres. Between those two moments an unbounded number of
    frames arrive -- at RN1's measured p99 of 10 fills/second, many.

    The 0 ms observation is CAPTURED AT STAMP TIME, before admission runs, so
    the answer cannot depend on how long the database took. This test makes the
    delay pathological (2,000 frames and a buffer small enough to overflow) and
    requires the captured value to be unchanged.
    """
    base = clock.now()
    s = pmus_stream.PmusStreamState(feed_session_id="adm")
    s.on_frame(_frame(bid=0.21), receive=_at(base, -0.0005))

    anchor = base
    # --- stamp: capture the 0 ms observation immediately, no I/O -------------
    zero = streamstate.capture_zero_ms(
        s.history("m"), observation_slot_id="slot-0ms",
        observation_channel=pmus_stream.CHANNEL, anchor=anchor,
        stale_tolerance_s=5.0)
    assert zero.captured and zero.state.best_bid == 0.21

    # --- the database is slow, and the market is not ------------------------
    for i in range(2_000):
        s.on_frame(_frame(bid=0.88), receive=_at(base, 0.0001 + i * 1e-5))

    # --- admission returns; the observation is the one taken at stamp time ---
    assert zero.state.best_bid == 0.21
    assert zero.state_age_at_receipt_ms == pytest.approx(0.5, abs=1e-6)

    # Selecting again NOW would still be right here, but the point is that the
    # result does not depend on that: the observation was already frozen.
    again = streamstate.select_state_at(s.history("m"), anchor,
                                        stale_tolerance_s=5.0)
    assert again.state.best_bid == 0.21


def test_venue_timestamps_are_never_operands_in_the_zero_ms_decision():
    """Built so a selector reading venue time gets the WRONG answer."""
    base = clock.now()
    s = pmus_stream.PmusStreamState(feed_session_id="prov")
    s.on_frame(_frame(bid=0.10, transact="9999999999999"),
               receive=_at(base, -0.010))      # later venue time, earlier arrival
    s.on_frame(_frame(bid=0.90, transact="1000000000000"),
               receive=_at(base, +0.010))      # earlier venue time, later arrival

    sel = streamstate.select_state_at(s.history("m"), base, stale_tolerance_s=5.0)
    assert sel.state.best_bid == 0.10
    assert sel.state.venue_timestamp_raw == "9999999999999"
    assert isinstance(sel.state.venue_timestamp_raw, str)


def test_the_capture_records_age_against_the_anchor_not_the_due_instant():
    """A 60 s slot's age is measured from receipt, not from when it was taken."""
    base = clock.now()
    s = pmus_stream.PmusStreamState(feed_session_id="age")
    s.on_frame(_frame(bid=0.5), receive=_at(base, 30.0))
    obs = streamstate.capture_for_slot(
        s.history("m"), observation_slot_id="slot-60s",
        observation_channel=pmus_stream.CHANNEL, target_offset_ms=60_000,
        anchor=base, captured_at=_at(base, 60.0), stale_tolerance_s=90.0)
    assert obs.captured
    # state arrived 30 s AFTER the anchor, so the age is negative -- and that is
    # the honest number for a later offset: the state is newer than receipt.
    assert obs.state_age_at_receipt_ms == pytest.approx(-30_000.0, abs=1e-3)
    assert obs.anchor_monotonic == base.monotonic
    assert obs.captured_at_monotonic == pytest.approx(base.monotonic + 60.0)


# =====================================================================
# THE SCIENTIFIC UNCERTAINTY STAYS LOCKED
# =====================================================================
def test_no_fixture_promotes_pmus_depth_authority():
    base = clock.now()
    s = pmus_stream.PmusStreamState(feed_session_id="u")
    s.on_frame(_frame(), receive=base)
    obs = streamstate.capture_zero_ms(
        s.history("m"), observation_slot_id="z",
        observation_channel=pmus_stream.CHANNEL, anchor=base,
        stale_tolerance_s=5.0)
    assert obs.state.depth_authority == "TOP_OF_BOOK_ONLY"
    assert obs.state.continuity == "STREAM_CONTINUITY_UNVERIFIED"
    assert pmus_stream.FULL_REPLACEMENT_STATUS == "PMUS_FULL_REPLACEMENT_UNCONFIRMED"
    # Top of book IS directly represented, so it is recorded.
    assert obs.state.best_bid == 0.40 and obs.state.best_ask == 0.42


def test_a_captured_pmus_state_still_refuses_to_price_a_size():
    base = clock.now()
    s = pmus_stream.PmusStreamState(feed_session_id="u")
    live = s.on_frame(_frame(), receive=base)
    with pytest.raises(streamstate.DepthUnavailable):
        live.vwap(q=10.0)
    # And the frozen copy carries the same verdict, so nothing downstream can
    # read authority off the capture that the live state refused.
    obs = streamstate.capture_zero_ms(
        s.history("m"), observation_slot_id="z",
        observation_channel=pmus_stream.CHANNEL, anchor=base,
        stale_tolerance_s=5.0)
    assert obs.state.depth_authority != "FULL_REPLACEMENT_CONFIRMED"
