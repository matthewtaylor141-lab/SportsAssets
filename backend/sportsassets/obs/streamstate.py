"""What a stream channel holds, and how a scheduled instant picks a state from it.

ONE RULE GOVERNS THIS WHOLE MODULE, AND IT IS A CLOCK RULE.

    The 0 ms state is the latest state whose LOCAL RECEIVE MONOTONIC timestamp
    is at or before the source event's receipt monotonic:

        latest_state_receive_monotonic <= source_receipt_monotonic
        state_age_at_receipt_ms = source_receipt_monotonic
                                  - latest_state_receive_monotonic

Both readings come from THIS process's monotonic clock, so the subtraction is a
real interval. A venue timestamp -- PMUS `transactTime`, CLOB's millisecond
`timestamp` -- belongs to the VENUE's clock domain, and this module will not use
one to decide whether a state was in BETTOR's memory at receipt. Those timestamps
are carried as provenance and nothing subtracts them from a local reading;
`venue_timestamp_raw` is a string for exactly that reason, because a string is
awkward to do arithmetic on by accident.

WHY THIS MATTERS MORE THAN IT LOOKS. The question the instrument asks is "what
could BETTOR have acted on at the instant it learned of the fill?" That is a
question about OUR memory, not about the venue's wall clock. A frame the venue
stamped before the fill but which reached us afterwards was NOT available to act
on, and selecting it would silently answer a different, easier question.

DEPTH AUTHORITY IS SEPARATE FROM STATE VALIDITY. A state can be perfectly valid
-- fresh, continuous, in-domain -- and still not support a depth-weighted price,
because whether the channel's arrays are a full replacement is unestablished.
Those are two different columns and two different verdicts.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from . import clock


class StreamChannel:
    """The four channels, named. Never inferred from a URL or a payload shape."""

    # PRIMARY EXECUTION CHANNEL (owner decision, Run 83.2B). Authenticated
    # market socket at wss://api.polymarket.us/v1/ws/markets.
    PMUS_FAST_STREAM_PATH = "PMUS_FAST_STREAM_PATH"
    # SECONDARY SOURCE / COMPARABILITY CHANNEL. Unauthenticated CLOB market
    # socket. Never conflated with the above, and never substituted for it.
    CLOB_FAST_STREAM_PATH = "CLOB_FAST_STREAM_PATH"
    # Secondary diagnostic HTTP read.
    LEGACY_COMPARABLE_BOOK_PATH = "LEGACY_COMPARABLE_BOOK_PATH"
    # Diagnostic only. Never an economic input.
    LOCAL_CACHE_BATCH_POLL_PATH = "LOCAL_CACHE_BATCH_POLL_PATH"

    STREAM_CHANNELS = (PMUS_FAST_STREAM_PATH, CLOB_FAST_STREAM_PATH)


class DepthAuthority:
    """May this state's ladder be used for a depth-weighted price?

    FULL_REPLACEMENT_CONFIRMED is NOT set by reading an SDK type. A dataclass
    with a `bids` list establishes that a list arrives, not that the list is the
    whole book. It is set only by passive observation, and until that observation
    exists every PMUS state is TOP_OF_BOOK_ONLY.
    """

    FULL_REPLACEMENT_CONFIRMED = "FULL_REPLACEMENT_CONFIRMED"
    TOP_OF_BOOK_ONLY = "TOP_OF_BOOK_ONLY"
    # A CLOB state carrying price_change frames whose semantics are unresolved.
    DEPTH_PENDING_SEMANTICS = "DEPTH_PENDING_SEMANTICS"


class StateValidity:
    VALID = "VALID"
    STALE_BEYOND_TOLERANCE = "STALE_BEYOND_TOLERANCE"
    INVALIDATED_BY_DISCONNECT = "INVALIDATED_BY_DISCONNECT"
    NEVER_BOOTSTRAPPED = "NEVER_BOOTSTRAPPED"


class Continuity:
    UNVERIFIED = "STREAM_CONTINUITY_UNVERIFIED"
    VERIFIED = "STREAM_CONTINUITY_VERIFIED"


class SelectionOutcome:
    SELECTED = "SELECTED"
    # The channel has state for this token, but every frame of it arrived AFTER
    # the receipt anchor. There was nothing in memory to act on.
    NO_VALID_PRE_RECEIPT_STATE = "NO_VALID_PRE_RECEIPT_STATE"
    # No frame for this token has ever arrived.
    CACHE_MISS = "CACHE_MISS"
    # A state exists and precedes the anchor, but is not admissible.
    CACHE_INVALID = "CACHE_INVALID"


class DepthUnavailable(ValueError):
    """Raised when a depth-weighted price is asked of a non-authoritative ladder."""


@dataclass(frozen=True)
class StreamState:
    """One channel's view of one token at one local instant.

    `receive` is the ONLY field that may be compared with a receipt anchor. It is
    a clock.Instant, which carries its own process_boot_id, so a comparison
    across a restart raises instead of returning a number.
    """

    channel: str
    token_id: str
    receive: clock.Instant
    feed_session_id: str
    best_bid: float | None = None
    best_ask: float | None = None
    bids: list[tuple[float, float]] = field(default_factory=list)
    asks: list[tuple[float, float]] = field(default_factory=list)
    depth_authority: str = DepthAuthority.TOP_OF_BOOK_ONLY
    validity: str = StateValidity.VALID
    continuity: str = Continuity.UNVERIFIED
    # ---- provenance only. Never compared with a local clock. ----------------
    venue_timestamp_raw: str | None = None
    venue_book_hash: str | None = None
    venue_sequence: str | None = None
    # Frames received that could not be applied because their semantics are
    # unresolved. Non-zero means this state may be behind the venue by a known
    # number of messages -- which is a far more useful admission than a silently
    # applied guess.
    pending_unapplied_updates: int = 0

    @property
    def depth_levels(self) -> int:
        return max(len(self.bids), len(self.asks))

    def vwap(self, q: float, side: str = "ask") -> tuple[float | None, bool]:
        """Depth-weighted price for size `q`, or a refusal.

        RAISES unless the ladder is an established full replacement. The
        alternative -- returning a number computed over whatever levels happened
        to arrive -- would produce a VWAP that looks like every other VWAP in the
        table while meaning something else entirely. Q_A is the PRIMARY size
        measure, so a quietly wrong Q_A would be a quietly wrong headline result.
        """
        if self.depth_authority != DepthAuthority.FULL_REPLACEMENT_CONFIRMED:
            raise DepthUnavailable(
                f"{self.channel} depth_authority={self.depth_authority}: the "
                "ladder is not an established full replacement, so a "
                "depth-weighted price over it is not defined. Record "
                "top-of-book and leave vwap NULL."
            )
        levels = sorted(self.asks) if side == "ask" else sorted(
            self.bids, reverse=True)
        taken = 0.0
        cost = 0.0
        for price, size in levels:
            if taken >= q:
                break
            use = min(size, q - taken)
            cost += use * price
            taken += use
        if taken <= 0:
            return None, True
        # Exhausted means the retained ladder did not hold q. Never extrapolated.
        return cost / taken, taken < q


@dataclass
class TokenStateHistory:
    """The retained states for one token on one channel, ordered by arrival.

    A HISTORY, NOT A LATEST-VALUE CELL. The 0 ms rule needs the latest state at
    or before an anchor, and an anchor can be milliseconds in the past by the
    time the sampler runs -- so a cell holding only the newest frame answers the
    wrong question whenever a frame lands between receipt and sampling. That is
    not a rare race; at RN1's p99 of 10 fills in a second it is the normal case.
    """

    channel: str
    token_id: str
    retain: int = 256
    _states: list[StreamState] = field(default_factory=list)
    _keys: list[float] = field(default_factory=list)

    def append(self, state: StreamState) -> None:
        if state.token_id != self.token_id or state.channel != self.channel:
            raise ValueError("state does not belong to this history")
        # Frames are appended in arrival order; the monotonic clock cannot go
        # backwards inside one process, so the key list stays sorted.
        self._states.append(state)
        self._keys.append(state.receive.monotonic)
        if len(self._states) > self.retain:
            drop = len(self._states) - self.retain
            del self._states[:drop]
            del self._keys[:drop]

    @property
    def latest(self) -> StreamState | None:
        return self._states[-1] if self._states else None

    def __len__(self) -> int:
        return len(self._states)

    def at_or_before(self, anchor: clock.Instant) -> StreamState | None:
        """The latest state received at or before `anchor`. Local clock only."""
        if not self._states:
            return None
        if self._states[-1].receive.process_boot_id != anchor.process_boot_id:
            # Deliberately raises rather than returning None: a boot-id mismatch
            # is a bookkeeping defect in the caller, not an absent observation,
            # and the two must not be recorded as the same thing.
            raise clock.ClockDomainError(
                "refusing to select stream state across process instances; the "
                "monotonic origin moved, so 'at or before' has no meaning"
            )
        idx = bisect.bisect_right(self._keys, anchor.monotonic)
        if idx == 0:
            return None
        return self._states[idx - 1]


@dataclass(frozen=True)
class Selection:
    """The answer to 'what state was in memory at the anchor?', with its reason."""

    outcome: str
    state: StreamState | None
    state_age_at_receipt_ms: float | None
    reason: str

    @property
    def selected(self) -> bool:
        return self.outcome == SelectionOutcome.SELECTED


def select_state_at(history: TokenStateHistory | None,
                    anchor: clock.Instant,
                    *, stale_tolerance_s: float) -> Selection:
    """THE 0 MS RULE, and the only place it is implemented.

    `anchor` is the source event's FIRST_BETTOR_RECEIPT_MONOTONIC instant --
    receipt time, never classification time and never admission time.
    """
    if history is None or len(history) == 0:
        return Selection(SelectionOutcome.CACHE_MISS, None, None,
                         "no frame for this token has ever arrived")

    state = history.at_or_before(anchor)
    if state is None:
        return Selection(
            SelectionOutcome.NO_VALID_PRE_RECEIPT_STATE, None, None,
            "every retained frame for this token arrived after the receipt "
            "anchor; there was no state in memory to act on")

    # Same process, same monotonic origin -- elapsed_between enforces that.
    age_s = clock.elapsed_between(state.receive, anchor)
    age_ms = age_s * 1000.0

    if state.validity != StateValidity.VALID:
        return Selection(SelectionOutcome.CACHE_INVALID, state, age_ms,
                         f"state_validity={state.validity}")
    if age_s > stale_tolerance_s:
        return Selection(
            SelectionOutcome.CACHE_INVALID, state, age_ms,
            f"state_validity={StateValidity.STALE_BEYOND_TOLERANCE} "
            f"({age_ms:.1f} ms > {stale_tolerance_s * 1000:.0f} ms)")

    return Selection(SelectionOutcome.SELECTED, state, age_ms, "selected")
