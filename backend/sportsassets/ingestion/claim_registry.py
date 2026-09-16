"""Per-(tx, wallet) claim registry for the two chain ingest paths.

Path A2 (`chain._handle_v3`, receipt reconstruction) and S1
(`s1_emitter`, the shadow-decode emitter) can both produce a chain row
for the same fill. Key-identical overlap is harmless — the trades
table's dedupe collapses it — but key-DIVERGENT double emission is the
catastrophic case (two rows for one fill), so ownership is decided
deterministically: the receipt path claims first (synchronously, at
its `_v3_seen` set point), and the emitter reads the outcome before it
will emit.

SEMANTICS (pinned by the S1 design attack): this registry is an
ADVISORY in-process fast path only. The trades TABLE is the
authoritative cross-restart collision record — both paths also run a
`SELECT 1 FROM trades WHERE lower(tx_hash)=$1 AND whale_id=$2 AND
source='chain'` pre-probe immediately before their ingest, which is
what survives a process restart wiping this dict. A receipt-path claim
never overwrites an emitter claim, and vice versa; `finish` only lands
on entries the caller owns (or creates its own).

Single event loop; callers must not await between a claim check and
the claim itself (both call sites honour this).
"""

from __future__ import annotations

from collections import OrderedDict

_CAP = 16384

# (tx, wallet) -> {"owner": "receipt"|"emitter", "done": bool,
#                  "outcome": None|"ingested"|"refused"}
_claims: OrderedDict[tuple[str, str], dict] = OrderedDict()


def _key(tx: str, wallet: str) -> tuple[str, str]:
    return (str(tx).lower(), str(wallet).lower())


def claim(tx: str, wallet: str, owner: str) -> bool:
    """Claim (tx, wallet) for `owner`. Returns True if the claim is
    held by `owner` after the call. An existing claim by the OTHER
    owner is never overwritten."""
    k = _key(tx, wallet)
    cur = _claims.get(k)
    if cur is not None:
        if cur["owner"] == owner:
            _claims.move_to_end(k)
            return True
        if cur["done"] and cur["outcome"] == "refused":
            # a refused outcome releases ownership: the fill class
            # belongs to whichever path can still carry it
            _claims[k] = {"owner": owner, "done": False, "outcome": None}
            _claims.move_to_end(k)
            return True
        return False
    _claims[k] = {"owner": owner, "done": False, "outcome": None}
    while len(_claims) > _CAP:
        _claims.popitem(last=False)
    return True


def finish(tx: str, wallet: str, owner: str, outcome: str) -> None:
    """Mark the caller's own claim done with `outcome`
    ('ingested'|'refused'). A claim held by the other owner is left
    untouched; an absent claim is created-then-finished so late
    observers still see a definitive outcome."""
    k = _key(tx, wallet)
    cur = _claims.get(k)
    if cur is None:
        cur = {"owner": owner, "done": False, "outcome": None}
        _claims[k] = cur
        while len(_claims) > _CAP:
            _claims.popitem(last=False)
    if cur["owner"] != owner:
        return
    cur["done"] = True
    cur["outcome"] = outcome


def get(tx: str, wallet: str) -> dict | None:
    """The claim record, or None if no path has claimed the pair."""
    return _claims.get(_key(tx, wallet))


def _reset_for_tests() -> None:
    _claims.clear()
