"""Run 83.2: channel-specific capacity gates -- owner decision 8.

WHAT WAS REJECTED, AND WHY THE TEST SAYS SO. Proposal P6 was one boot check:
refuse to start if `arrival_rate x 10 > configured_RPS`. It was rejected because
(a) the primary channel issues no request per slot, so that comparison is
between unrelated quantities, and (b) a mean cannot gate a burst -- RN1's
measured distribution is mean 1.584 fills per active second but p99 10 and
max 43.

So the gates are per channel and are evaluated at a burst quantile as well as
the mean, and test_the_arithmetic_that_killed_v1_is_written_down pins the numbers
themselves so a future change to the defaults cannot quietly reintroduce the
failure.
"""
from __future__ import annotations

from sportsassets.obs import capacity
from sportsassets.obs.config import OFFSETS, window_for

_LOCAL = "LOCAL_CACHE_BATCH_POLL_PATH"
_HTTP = "LEGACY_COMPARABLE_BOOK_PATH"


def _by(rep, channel, gate):
    return next(f for f in rep.findings if f.channel == channel and f.gate == gate)


def test_the_arithmetic_that_killed_v1_is_written_down():
    """V1 capacity = max_inflight / horizon = 8/60 = 0.133 events/s.

    Measured RN1-only arrival: 0.187/s on the busiest complete day, 0.255/s over
    the activation window. The architecture failed by 1.4x to 1.9x EVEN AT THE
    CORRECT POPULATION, which is the finding that made the scheduler rewrite
    necessary rather than optional.
    """
    horizon_s = max(t for _, t in OFFSETS)
    assert horizon_s == 60.0
    v1_capacity = 8 / horizon_s
    assert round(v1_capacity, 3) == 0.133
    assert capacity.MEASURED["rn1_events_per_s_peak_day"] > v1_capacity
    assert capacity.MEASURED["rn1_events_per_s_activation_window"] > v1_capacity
    assert round(capacity.MEASURED["rn1_events_per_s_activation_window"]
                 / v1_capacity, 1) == 1.9


def test_the_primary_channel_is_not_gated_on_an_http_pacing_ceiling():
    """P6's rejected test must not exist for the local channel.

    A local sample makes no request. Gating it on RPS would refuse a healthy
    configuration and, worse, would imply the primary curve depends on REST
    capacity -- which owner decision 3 forbids.
    """
    rep = capacity.assess()
    local_gates = {f.gate for f in rep.findings if f.channel == _LOCAL}
    assert "endpoint_rps" not in local_gates
    assert "request_concurrency" not in local_gates
    assert local_gates == {"hot_set", "refresh_rps", "sample_rate",
                           "cache_memory", "db_writes"}


def test_the_primary_channel_gates_are_the_resources_it_actually_uses():
    rep = capacity.assess()
    for gate in ("hot_set", "refresh_rps", "sample_rate", "cache_memory",
                 "db_writes"):
        assert _by(rep, _LOCAL, gate) is not None


def test_the_burst_quantile_not_the_mean_drives_the_hot_set_gate():
    """A gate satisfied by the mean is silent about the case that breaks."""
    at_mean = _by(capacity.assess(burst_per_s=1), _LOCAL, "hot_set")
    at_p99 = _by(capacity.assess(burst_per_s=10), _LOCAL, "hot_set")
    assert at_mean.detail != at_p99.detail, (
        "the hot-set gate reports the same thing at a 1/s and a 10/s burst; it "
        "is therefore not reading the burst at all")
    assert "10/s burst" in at_p99.detail


def test_the_secondary_channel_burst_gate_can_never_pass():
    """And that is the honest answer, not a bug in the gate.

    At RN1's p99 (10 fills in one second) the 0 ms slot alone needs 10 reads
    inside its 50 ms window -- 200 requests/s instantaneously -- and measured
    p50 request duration is 170 ms, so the 0 ms and 100 ms offsets are INSIDE
    the round trip. No RPS setting reaches that, which is precisely why this
    channel is secondary.
    """
    rep = capacity.assess()
    gate = _by(rep, _HTTP, "burst_feasibility")
    assert gate.ok is False
    assert "INSIDE the round trip" in gate.detail
    assert not rep.ok, "the overall report must not claim OK while this fails"
    assert gate in rep.failures()
    # The instantaneous figure is the one that matters and must be stated.
    shortest = min(window_for(t) for _, t in OFFSETS)
    assert shortest == 0.050
    assert f"{10 / shortest:.0f} req/s instantaneous" in gate.detail


def test_the_gates_report_rather_than_refusing_to_boot():
    """assess() returns a report; nothing in it exits or raises.

    A hard boot-refusal on an ESTIMATED arrival rate would be a new way to take
    the instrument down, and the estimate is the least certain input here.
    """
    rep = capacity.assess()
    assert isinstance(rep.lines(), list)
    assert all(line.startswith(("PASS ", "FAIL ")) for line in rep.lines())


def test_storage_is_derived_from_the_measured_row_size():
    """6,581 bytes/event = 82,493,440 bytes / 12,535 events, measured on V1."""
    assert capacity.MEASURED["bytes_per_observed_event"] == 6581
    peak = capacity.MEASURED["rn1_events_per_s_peak_day"]
    mb = capacity.storage_per_day(peak)
    assert 100 < mb < 112, f"peak-day storage came out at {mb:.1f} MB/day"
    # The design rate is above the approved 124 MB/day and must be reported so.
    assert capacity.storage_per_day() > 124


def test_the_design_rate_sits_above_every_measurement():
    for key in ("rn1_events_per_s_mean_10d", "rn1_events_per_s_peak_day",
                "rn1_events_per_s_activation_window"):
        assert capacity.DESIGN_EVENTS_PER_S > capacity.MEASURED[key], (
            f"the design rate is not above the measured {key}; RN1's daily "
            f"count rose 8,596 -> 16,187 in five days, so a design rate at or "
            f"below a measurement is already stale")
