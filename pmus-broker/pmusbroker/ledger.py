"""The mint ledger: who asked, when, for what capability. Never what was minted.

FOUR FIELDS, AND THE ABSENCE OF A FIFTH. mint id, wall time, capability id,
consumer. There is no column for the signature, the timestamp header, the key id
or the secret, and `record()` takes a MintedHandshake but reads only its
metadata -- so a future edit that tries to log the material has to add a field
here, in a file whose whole purpose is that it has none.

IN MEMORY, BOUNDED, AND NOT A DATABASE. The broker has no database access at all
(owner requirement). A ring buffer is enough for the operational question this
answers -- "did the collector ask for this, or did something else?" -- and a
process with no DB credential is a process that cannot be used to reach one.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:                                    # pragma: no cover
    from .mint import MintedHandshake

MAX_RECORDS = 512


@dataclass(frozen=True)
class MintRecord:
    mint_id: str
    minted_at_wall_ms: int
    capability_id: str
    consumer: str


class MintLedger:
    def __init__(self, maxlen: int = MAX_RECORDS) -> None:
        self._lock = threading.Lock()
        self._records: deque[MintRecord] = deque(maxlen=maxlen)
        self.total = 0

    def record(self, minted: MintedHandshake) -> MintRecord:
        """Take the metadata off a handshake. `minted.headers` is not read."""
        rec = MintRecord(
            mint_id=minted.mint_id,
            minted_at_wall_ms=minted.minted_at_wall_ms,
            capability_id=minted.capability_id,
            consumer=minted.consumer,
        )
        with self._lock:
            self._records.append(rec)
            self.total += 1
        return rec

    def recent(self, limit: int = 50) -> list[MintRecord]:
        with self._lock:
            return list(self._records)[-limit:]

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"total": self.total, "retained": len(self._records)}
