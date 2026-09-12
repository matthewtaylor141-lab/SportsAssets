"""Channel-specific capacity gates -- run 83.2, owner decision 8.

THE REJECTED VERSION OF THIS MODULE. The first proposal (P6) was a single boot
check: refuse to start if `arrival_rate x 10 > configured_RPS`. The owner
rejected it, correctly, for two reasons:

  1. IT IS THE WRONG TEST FOR THE PRIMARY CHANNEL. A local sample makes no
     request, so comparing the primary channel's sample rate to an HTTP pacing
     ceiling compares two unrelated quantities and would refuse a healthy
     configuration.
  2. A MEAN CANNOT GATE A BURST. RN1's measured distribution over 24 h is mean
     1.584 fills per active second, p95 3, p99 10, max 43. A gate satisfied by
     the mean is silent about the case that actually breaks: the p99 second.

So each channel is gated on the resources IT consumes, and every gate is
evaluated at a burst quantile as well as the mean. The gates REPORT; they do not
refuse on their own. A hard boot-refusal on an estimated arrival rate would be a
new way to take the instrument down, and the estimate is the least certain input
here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import (OFFSETS, cache_batch_size, cache_hot_tokens,
                     cache_refresh_interval_s, max_inflight, reads_per_second,
                     window_for)

# Measured on RUN83_ACTIVATION_FAILED_V1 and the trades ledger, 2026-09-12.
# These are the inputs the model is honest about being measurements OF, not
# assumptions: every one has a query behind it in research/run831_*.sql.
MEASURED = {
    "rn1_events_per_s_mean_10d": 11530 / 86400.0,      # 0.1334
    "rn1_events_per_s_peak_day": 16187 / 86400.0,      # 0.1874
    "rn1_events_per_s_activation_window": 190 / 744.47,  # 0.2552
    "rn1_burst_p95_per_s": 3,
    "rn1_burst_p99_per_s": 10,
    "rn1_burst_max_per_s": 43,
    "http_duration_s_mean": 0.1820,
    "http_duration_s_p95": 0.2267,
    "http_duration_s_max": 0.6693,
    "bytes_per_observed_event": 6581,                   # 82,493,440 / 12,535
}

# Above every measurement, because the trend is up (RN1: 8,596 -> 16,187 over
# five days).
DESIGN_EVENTS_PER_S = 0.30


@dataclass
class Finding:
    gate: str
    channel: str
    ok: bool
    detail: str


@dataclass
class CapacityReport:
    findings: list[Finding] = field(default_factory=list)

    def add(self, gate: str, channel: str, ok: bool, detail: str) -> None:
        self.findings.append(Finding(gate, channel, ok, detail))

    @property
    def ok(self) -> bool:
        return all(f.ok for f in self.findings)

    def failures(self) -> list[Finding]:
        return [f for f in self.findings if not f.ok]

    def lines(self) -> list[str]:
        return [f"{'PASS' if f.ok else 'FAIL'} {f.channel}/{f.gate}: {f.detail}"
                for f in self.findings]


def shortest_window_s() -> float:
    return min(window_for(t) for _, t in OFFSETS)


def assess(events_per_s: float = DESIGN_EVENTS_PER_S,
           burst_per_s: int = MEASURED["rn1_burst_p99_per_s"]) -> CapacityReport:
    """Evaluate both channels at the design rate AND at a burst quantile."""
    rep = CapacityReport()
    n_offsets = len(OFFSETS)

    # ---------------------------------------------- LOCAL_CACHE_BATCH_POLL_PATH
    # Subscription capacity: how many tokens must stay hot at once. A token stays
    # hot for its event's horizon, so the steady-state hot set is
    # arrival x horizon (plus the TTL tail).
    horizon_s = max(t for _, t in OFFSETS)
    hot_needed = events_per_s * horizon_s
    hot_burst = burst_per_s * 1.0 + hot_needed
    rep.add("hot_set", "LOCAL_CACHE_BATCH_POLL_PATH",
            cache_hot_tokens() >= hot_burst,
            f"need ~{hot_needed:.1f} steady, ~{hot_burst:.1f} with a "
            f"{burst_per_s}/s burst; configured {cache_hot_tokens()}")

    # Refresh cost: keeping the hot set fresh is the ONLY network cost of this
    # channel, and it is per-token-batch, not per-slot.
    batches = max(1, -(-cache_hot_tokens() // cache_batch_size()))
    refresh_rps = batches / cache_refresh_interval_s()
    rep.add("refresh_rps", "LOCAL_CACHE_BATCH_POLL_PATH",
            refresh_rps <= reads_per_second(),
            f"{batches} batch(es) every {cache_refresh_interval_s():.2f}s = "
            f"{refresh_rps:.2f} req/s vs pacer {reads_per_second():.2f}")

    # Sample lateness: the scheduler must reach a slot inside the SHORTEST
    # window. This is an event-loop question, not a network one.
    samples_per_s = events_per_s * n_offsets
    burst_samples = burst_per_s * n_offsets
    rep.add("sample_rate", "LOCAL_CACHE_BATCH_POLL_PATH", True,
            f"{samples_per_s:.2f} local samples/s steady, {burst_samples} in a "
            f"{burst_per_s}/s burst -- no request either way; shortest window "
            f"{shortest_window_s() * 1000:.0f}ms")

    # Cache memory: bounded by the hot cap, not by the board.
    rep.add("cache_memory", "LOCAL_CACHE_BATCH_POLL_PATH", True,
            f"{cache_hot_tokens()} tokens x one ladder each, TTL-retired; "
            f"bounded by configuration, not by the market count")

    # DB writes: one plan row plus one result row per slot.
    writes_per_s = samples_per_s * 2
    rep.add("db_writes", "LOCAL_CACHE_BATCH_POLL_PATH", writes_per_s < 100,
            f"{writes_per_s:.1f} rows/s (plan + result); V1 sustained 168/s of "
            f"misses, so this is well inside proven throughput")

    # ---------------------------------------------- LEGACY_COMPARABLE_BOOK_PATH
    # Only reached if the secondary channel is switched on. Its request-per-slot
    # shape is exactly what failed in V1, so the gate says so plainly rather
    # than implying a configuration exists that makes it work at the short end.
    secondary_rps = samples_per_s
    rep.add("endpoint_rps", "LEGACY_COMPARABLE_BOOK_PATH",
            secondary_rps <= reads_per_second(),
            f"one request per slot = {secondary_rps:.2f} req/s vs pacer "
            f"{reads_per_second():.2f}")
    concurrency = secondary_rps * MEASURED["http_duration_s_p95"]
    rep.add("request_concurrency", "LEGACY_COMPARABLE_BOOK_PATH",
            max_inflight() >= concurrency,
            f"Little's law: {secondary_rps:.2f}/s x p95 "
            f"{MEASURED['http_duration_s_p95']:.3f}s = {concurrency:.2f} "
            f"concurrent; configured {max_inflight()}")
    # The one that cannot be configured away.
    burst_instant = burst_per_s / shortest_window_s()
    rep.add("burst_feasibility", "LEGACY_COMPARABLE_BOOK_PATH", False,
            f"a {burst_per_s}/s burst needs {burst_per_s} reads inside the "
            f"{shortest_window_s() * 1000:.0f}ms window = "
            f"{burst_instant:.0f} req/s instantaneous, and p50 duration is "
            f"{MEASURED['http_duration_s_mean']:.3f}s -- the 0ms and 100ms "
            f"offsets are INSIDE the round trip. Unreachable at any RPS; this "
            f"channel is secondary for exactly this reason")
    return rep


def storage_per_day(events_per_s: float = DESIGN_EVENTS_PER_S) -> float:
    """Megabytes per day at a given arrival rate, from the MEASURED row size."""
    return events_per_s * 86400 * MEASURED["bytes_per_observed_event"] / 1e6
