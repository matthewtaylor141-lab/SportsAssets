"""Canonical dedupe key for trades.

Both ingestion paths (on-chain Path A and Data-API Path B) must derive the
IDENTICAL key for the same fill, so the DB unique constraint collapses them.

Key inputs per spec: (tx_hash, asset, side, size, price, timestamp).
Numbers are normalized to fixed 6-decimal strings so '84000', '84000.0' and
'84000.000000' hash identically regardless of source formatting.
"""

from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_UP, Decimal


def _num(value: float | int | str | Decimal) -> str:
    d = Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return format(d, "f")


def make_dedupe_key(
    tx_hash: str,
    asset: str,
    side: str,
    size: float | str | Decimal,
    price: float | str | Decimal,
    ts_epoch: int,
) -> str:
    canonical = "|".join(
        [
            tx_hash.lower().strip(),
            str(asset).strip(),
            side.upper().strip(),
            _num(size),
            _num(price),
            str(int(ts_epoch)),
        ]
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
