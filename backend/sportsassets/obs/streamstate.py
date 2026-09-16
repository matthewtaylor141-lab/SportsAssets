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


# ------------------------------------------------------- retention, by TIME
#
# THE 256-MESSAGE CAP IS GONE, AND IT SHOULD NEVER HAVE BEEN A SCIENTIFIC
# DEPENDENCY. The stream's message rate is unknown -- that is one of the things
# passive observation is meant to measure -- so "the last 256 states" has no
# defensible temporal meaning. At 50 messages/second it is five seconds of
# history; at 500 it is half a second, and the genuine pre-receipt state for an
# event whose admission took 100 ms would be evicted by later churn before the
# 0 ms rule ever read it. The instrument would then answer from a post-receipt
# book and there would be nothing in the row to say so.
#
# TWO INDEPENDENT CHANGES REPLACE IT, AND THE SECOND IS THE REAL ONE:
#
#   1. retention is by TIME HORIZON with explicit headroom (below), so what is
#      kept is stated in seconds rather than in messages; and
#   2. A DUE OBSERVATION IS CAPTURED OUT OF THE BUFFER AT DUE TIME, into an
#      immutable SlotObservation. Once captured it does not depend on the buffer
#      at all, so later churn -- however fast -- cannot erase it.
#
# Because of (2), a buffer overflow is no longer a scientific loss. That is what
# makes it safe to bound memory at all: the guarantee comes from capture, not
# from retention.
RETENTION_HEADROOM_S: float = 15.0

# An absolute memory guard, not a scientific parameter. If it ever binds, the
# history says so (`overflow_events`) rather than dropping quietly -- see
# `append`. It is deliberately large enough that binding means something
# abnormal happened.
DEFAULT_OVERFLOW_CAP: int = 100_000


@dataclass
class TokenStateHistory:
    """The retained states for one token on one channel, ordered by arrival.

    A HISTORY, NOT A LATEST-VALUE CELL. The 0 ms rule needs the latest state at
    or before an anchor, and an anchor can be milliseconds in the past by the
    time the sampler runs -- so a cell holding only the newest frame answers the
    wrong question whenever a frame lands between receipt and sampling. That is
    not a rare race; at RN1's p99 of 10 fills in a second it is the normal case.

    RETAINED BY TIME, NOT BY COUNT. `retain_horizon_s` must cover the whole
    pre-registered ladder plus headroom, so that a slot still pending at 60 s can
    resolve against state that was already present at its anchor.
    """

    channel: str
    token_id: str
    retain_horizon_s: float = 75.0          # 60 s ladder + 15 s headroom
    overflow_cap: int = DEFAULT_OVERFLOW_CAP
    overflow_events: int = 0
    pruned: int = 0
    _states: list[StreamState] = field(default_factory=list)
    _keys: list[float] = field(default_factory=list)

    def append(self, state: StreamState) -> None:
        if state.token_id != self.token_id or state.channel != self.channel:
            raise ValueError("state does not belong to this history")
        # Frames are appended in arrival order; the monotonic clock cannot go
        # backwards inside one process, so the key list stays sorted.
        self._states.append(state)
        self._keys.append(state.receive.monotonic)
        self._prune(now_monotonic=state.receive.monotonic)

    def _prune(self, *, now_monotonic: float) -> None:
        """Drop everything older than the horizon. Time first, cap second."""
        cutoff = now_monotonic - self.retain_horizon_s
        keep_from = bisect.bisect_left(self._keys, cutoff)
        if keep_from:
            del self._states[:keep_from]
            del self._keys[:keep_from]
            self.pruned += keep_from

        if len(self._states) > self.overflow_cap:
            # INSIDE the horizon and still over the cap. This is a memory
            # guard firing, not a retention policy, so it is COUNTED and
            # visible. It is not a scientific loss: every slot already due has
            # been captured into its own immutable observation, and one not yet
            # due will be answered from a state at least as recent as the ones
            # being dropped.
            drop = len(self._states) - self.overflow_cap
            del self._states[:drop]
            del self._keys[:drop]
            self.overflow_events += 1
            self.pruned += drop

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


# =====================================================================
# CAPTURE -- where a scientific observation stops depending on the buffer
# =====================================================================
@dataclass(frozen=True)
class CapturedState:
    """A deep, immutable copy of one StreamState. Tuples, not lists.

    StreamState is frozen but its ladders are LISTS, so two references share one
    mutable object. That is fine for live state and wrong for evidence: an
    observation must be a fact about an instant, not a view onto something that
    can still change. Capture converts the ladders to tuples so the record is
    immutable all the way down.
    """

    channel: str
    token_id: str
    receive_monotonic: float
    receive_wall: object
    process_boot_id: str
    feed_session_id: str
    best_bid: float | None
    best_ask: float | None
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    depth_authority: str
    validity: str
    continuity: str
    venue_timestamp_raw: str | None
    venue_book_hash: str | None
    venue_sequence: str | None
    pending_unapplied_updates: int

    @classmethod
    def of(cls, s: StreamState) -> "CapturedState":
        return cls(
            channel=s.channel, token_id=s.token_id,
            receive_monotonic=s.receive.monotonic, receive_wall=s.receive.wall,
            process_boot_id=s.receive.process_boot_id,
            feed_session_id=s.feed_session_id,
            best_bid=s.best_bid, best_ask=s.best_ask,
            bids=tuple(s.bids), asks=tuple(s.asks),
            depth_authority=s.depth_authority, validity=s.validity,
            continuity=s.continuity,
            venue_timestamp_raw=s.venue_timestamp_raw,
            venue_book_hash=s.venue_book_hash, venue_sequence=s.venue_sequence,
            pending_unapplied_updates=s.pending_unapplied_updates,
        )


@dataclass(frozen=True)
class SlotObservation:
    """ONE IMMUTABLE RESULT FOR ONE SLOT. Written once, never revised.

    This is the object that makes the retention question a memory question
    rather than a scientific one. It is taken AT DUE TIME out of whatever valid
    state the channel then holds, and from that moment it is self-contained:
    every later frame, prune, overflow, disconnect or reconnect is irrelevant to
    it. A stress test that pushes tens of thousands of updates through the
    channel cannot disturb an observation already captured.
    """

    observation_slot_id: str
    observation_channel: str
    target_offset_ms: int
    status: str
    outcome: str
    reason: str
    captured_at_monotonic: float
    anchor_monotonic: float
    state_age_at_receipt_ms: float | None
    state: CapturedState | None

    @property
    def captured(self) -> bool:
        return self.status == "CAPTURED_STREAM"


def capture_for_slot(history: TokenStateHistory | None, *,
                     observation_slot_id: str, observation_channel: str,
                     target_offset_ms: int, anchor: clock.Instant,
                     captured_at: clock.Instant,
                     stale_tolerance_s: float) -> SlotObservation:
    """Resolve one slot against the channel's CURRENT state and freeze it.

    Two different questions, deliberately, depending on the offset:

      * 0 ms asks what was in memory AT THE ANCHOR, so it selects at-or-before
        the anchor and its age is measured from the anchor.
      * every later offset asks what the channel holds AT DUE TIME, so it takes
        the then-current valid state -- which is the state BETTOR would have
        been acting on at that instant.

    Both record `state_age_at_receipt_ms` against the ANCHOR, because that is
    the quantity the experiment is about, and both use the local monotonic clock
    for it. No venue timestamp is an operand anywhere in this function.
    """
    if target_offset_ms == 0:
        sel = select_state_at(history, anchor,
                              stale_tolerance_s=stale_tolerance_s)
    else:
        sel = select_state_at(history, captured_at,
                              stale_tolerance_s=stale_tolerance_s)

    if sel.selected and sel.state is not None:
        # Age is always measured from the anchor, never from the due instant.
        age_ms = clock.elapsed_between(sel.state.receive, anchor) * 1000.0
        return SlotObservation(
            observation_slot_id=observation_slot_id,
            observation_channel=observation_channel,
            target_offset_ms=target_offset_ms,
            status="CAPTURED_STREAM", outcome=sel.outcome, reason=sel.reason,
            captured_at_monotonic=captured_at.monotonic,
            anchor_monotonic=anchor.monotonic,
            state_age_at_receipt_ms=age_ms,
            state=CapturedState.of(sel.state),
        )

    # Not a capture. The slot still gets exactly one immutable result, so a
    # missing observation is a fact about a known slot rather than an absence.
    status = (SelectionOutcome.NO_VALID_PRE_RECEIPT_STATE
              if sel.outcome == SelectionOutcome.NO_VALID_PRE_RECEIPT_STATE
              else sel.outcome)
    return SlotObservation(
        observation_slot_id=observation_slot_id,
        observation_channel=observation_channel,
        target_offset_ms=target_offset_ms,
        status=status, outcome=sel.outcome, reason=sel.reason,
        captured_at_monotonic=captured_at.monotonic,
        anchor_monotonic=anchor.monotonic,
        state_age_at_receipt_ms=sel.state_age_at_receipt_ms,
        state=CapturedState.of(sel.state) if sel.state is not None else None,
    )


def capture_zero_ms(history: TokenStateHistory | None, *,
                    observation_slot_id: str, observation_channel: str,
                    anchor: clock.Instant,
                    stale_tolerance_s: float) -> SlotObservation:
    """Freeze the 0 ms observation AT STAMP TIME, before admission runs.

    THIS IS THE ANSWER TO THE ADMISSION-DELAY RACE. The canonical `was_insert`
    answer comes back from Postgres, and between stamping the anchor and getting
    it, an unbounded number of frames can arrive -- at RN1's p99 burst, many. If
    the 0 ms state were selected after admission, it would be selected out of a
    buffer that had moved on, and a long enough delay combined with a bounded
    buffer could leave nothing at or before the anchor at all.
    """
    return capture_for_slot(
        history, observation_slot_id=observation_slot_id,
        observation_channel=observation_channel, target_offset_ms=0,
        anchor=anchor, captured_at=anchor,
        stale_tolerance_s=stale_tolerance_s)
