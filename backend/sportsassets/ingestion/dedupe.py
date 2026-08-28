"""Canonical dedupe key for trades.

Both ingestion paths (on-chain Path A and Data-API Path B) must derive the
IDENTICAL key for the same fill, so the DB unique constraint collapses them.

Key inputs per spec: (tx_hash, asset, side, size, price, timestamp).
Numbers are normalized to fixed 6-decimal strings so '84000', '84000.0' and
'84000.000000' hash identically regardless of source formatting.
"""

from __future__ import annotations

import hashlib
import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def _num(value: float | int | str | Decimal) -> str:
    d = Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return format(d, "f")


def key_fields_valid(ev) -> bool:
    """True when every dedupe-key field survives THE KEY'S OWN
    normalization as a real value — the single validity gate both
    Path-B carriers (poller + reconciler) apply before ingest.

    Fleet round 13 gated the raw fields; round 14 (major x3) proved
    raw checks diverge from the key: a whitespace tx/asset is truthy
    but strips to the '' sentinel; size 1e-7 is > 0 but quantizes to
    0.000000; NaN passes every ordered comparison (nan <= 0 is
    False) and quantizes to a quiet-NaN key while Postgres numeric
    accepts it; Infinity raises InvalidOperation inside the key and
    killed a whole wallet's poll cycle from one hostile row. The
    gate therefore checks the NORMALIZED values (strip/upper/
    quantize), requires finite numbers, floors ts at the 1e9
    sentinel, and NEVER raises — a row this refuses is unusable for
    ingest and for coverage testimony alike.
    """
    try:
        if not (ev.tx_hash or "").strip():
            return False
        if not str(ev.asset or "").strip():
            return False
        if str(ev.side or "").upper().strip() not in ("BUY", "SELL"):
            return False
        for v, cap in ((ev.size, 1e18), (ev.price, 1e4)):
            if not isinstance(v, (int, float)) or not math.isfinite(v):
                return False
            if Decimal(str(v)).quantize(
                    Decimal("0.000001"), rounding=ROUND_HALF_UP) <= 0:
                return False
            # storability bounds mirror the trades columns (round 15:
            # a micro-scaled price of 420000 passed the >0 gate and
            # overflowed NUMERIC(10,6) inside the INSERT, killing the
            # wallet's whole batch one call later)
            if v >= cap:
                return False
        ts = ev.ts_epoch
        if isinstance(ts, float) and not math.isfinite(ts):
            return False
        # the upper bound refuses millisecond-scaled epochs (round 15:
        # ts=1.756e12 passed the 1e9 floor and datetime.fromtimestamp
        # raised 'year out of range' inside ingest)
        if not isinstance(ts, (int, float)) or ts <= 1e9 or ts >= 4e10:
            return False
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        return False
    return True


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
