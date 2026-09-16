"""The forward snapshot schedule -- run 83G.

ONE RULE, AND EVERYTHING HERE SERVES IT:

    A SAMPLE IS A READING AT t, NOT THE FIRST READING AFTER t.

An offset whose window has passed unread is MISSED. It is recorded as missed,
with a reason, and never filled in from a later read. The alternative -- taking
whatever reading comes next and labelling it with the offset that was wanted --
produces a curve that looks complete and is false, and it is the specific defect
workers/price_path.py had to be rewritten to remove.

All scheduling is monotonic. The anchor is BETTOR's first receipt of the source
event, which is the one instant in this whole problem we can stamp in a single
clock domain (run 82: every source-anchored boundary crosses an unmeasured
offset, so the source fill cannot be the anchor).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import OFFSETS, window_for


class SnapshotStatus:
    CAPTURED = "CAPTURED"
    MISSED_WINDOW = "MISSED_WINDOW"
    VENUE_ERROR = "VENUE_ERROR"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    SKIPPED_PACING = "SKIPPED_PACING"


@dataclass
class Slot:
    """One pre-registered offset for one event."""

    label: str
    target_s: float
    anchor_monotonic: float
    status: str = SnapshotStatus.NOT_ATTEMPTED
    miss_reason: str | None = None

    @property
    def due_at(self) -> float:
        """The monotonic instant this reading is wanted at."""
        return self.anchor_monotonic + self.target_s

    @property
    def window_s(self) -> float:
        return window_for(self.target_s)

    @property
    def expires_at(self) -> float:
        return self.due_at + self.window_s

    def is_due(self, mono_now: float) -> bool:
        return self.due_at <= mono_now <= self.expires_at

    def is_expired(self, mono_now: float) -> bool:
        return mono_now > self.expires_at

    def wait_s(self, mono_now: float) -> float:
        return max(0.0, self.due_at - mono_now)


@dataclass
class Plan:
    """The full set of slots for one observed event."""

    anchor_monotonic: float
    slots: list[Slot] = field(default_factory=list)

    @classmethod
    def for_event(cls, anchor_monotonic: float) -> "Plan":
        return cls(
            anchor_monotonic=anchor_monotonic,
            slots=[Slot(label=lbl, target_s=t, anchor_monotonic=anchor_monotonic)
                   for lbl, t in OFFSETS],
        )

    @property
    def horizon_s(self) -> float:
        return max((s.target_s for s in self.slots), default=0.0)

    def next_pending(self, mono_now: float) -> Slot | None:
        """The earliest slot not yet resolved, expiring any that are past.

        Expiry happens HERE rather than at read time, so a slot cannot be
        resurrected by a late read that finds it still pending.
        """
        for slot in self.slots:
            if slot.status != SnapshotStatus.NOT_ATTEMPTED:
                continue
            if slot.is_expired(mono_now):
                slot.status = SnapshotStatus.MISSED_WINDOW
                slot.miss_reason = (
                    f"window closed unread: due at +{slot.target_s:g}s, "
                    f"window {slot.window_s:g}s, now "
                    f"+{mono_now - slot.anchor_monotonic:.3f}s"
                )
                continue
            return slot
        return None

    def expire_all(self, mono_now: float, reason: str) -> None:
        """Close out every unresolved slot -- used on shutdown or abandonment.

        Unresolved slots are recorded as missed rather than dropped. A dropped
        slot and a failed read are indistinguishable afterwards, and only one of
        them is honest.
        """
        for slot in self.slots:
            if slot.status == SnapshotStatus.NOT_ATTEMPTED:
                slot.status = SnapshotStatus.MISSED_WINDOW
                slot.miss_reason = reason

    def is_complete(self) -> bool:
        return all(s.status != SnapshotStatus.NOT_ATTEMPTED for s in self.slots)

    def census(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.slots:
            out[s.status] = out.get(s.status, 0) + 1
        return out


def walk_depth(levels, q):
    """Cost of taking q shares from an ascending ladder, or None if it cannot.

    Carried verbatim in behaviour from research/gen/run81a_s.py so the forward
    curve and the historical measurement are the same quantity. Returns None --
    never a partial cost dressed as a full one -- when the retained ladder
    cannot cover q. That row is DEPTH_EXHAUSTED and its full-q price is NOT
    IDENTIFIED: not extrapolated, not imputed, not a bound.
    """
    if q is None or q <= 0 or not levels:
        return None
    total = sum(sz for _px, sz in levels)
    if total < q:
        return None
    cost = 0.0
    taken = 0.0
    for px, sz in sorted(levels, key=lambda t: t[0]):
        if taken >= q:
            break
        use = min(sz, q - taken)
        cost += px * use
        taken += use
    return cost


def vwap(levels, q):
    """Executable VWAP for q, or None when depth is exhausted."""
    cost = walk_depth(levels, q)
    return None if cost is None else cost / q
