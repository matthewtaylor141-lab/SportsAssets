"""THE CANONICAL LIVE AUTHORIZATION (R30A convergence, audit P0 #2). Pure:
imports only the standard library.

What a venue primitive accepts as permission to ORIGINATE NEW EXPOSURE (a
BUY): a LiveAuthorization that the canonical SMALL LIVE adapter issued
(live_parity.issue_live_authorization -- the only constructor) for one
canonical decision intent, outside SHADOW. This release has no LIVE mode
(SMALL_LIVE_MODE is SHADOW here and in live_parity, and migration 225 CHECKs
small_live_control.mode = 'SHADOW'), so nothing can be authorized.

WHY A MODULE OF ITS OWN (R30A review). The boundary sits INSIDE the venue
adapters every lane calls -- pmus.submit_fok and live_executor's CLOB
_submit_fok -- and those adapters are reachable from the workers' loops.
Importing live_parity there would make execmirror, the funded stack and the
execution intent reachable from the workers too (tests/test_workers_hold_no_
venue_write.py pins that they are not). So the adapters read only this.

HOW A TOKEN TRAVELS. Not as a new argument: pmus.submit_fok's signature is a
drop-in contract shared with pmx (tests/test_e35_pmx_adapter.py), and the
lanes reach it through asyncio.to_thread / partial / wrapper callables. The
canonical caller PRESENTS the token for the duration of its one call
(`with presenting(token): adapter.submit_fok(...)`), in a ContextVar --
copied into asyncio.to_thread's worker thread, invisible to any other task.
A lane that presents nothing presents None, and None authorizes nothing.
"""
from __future__ import annotations

import contextlib
import contextvars

SHADOW = "SHADOW"
#: THE ONLY SMALL LIVE MODE THIS CODE KNOWS (live_parity.SMALL_LIVE_MODE and
#: migration 225 say the same; a test pins all three equal)
SMALL_LIVE_MODE = SHADOW
#: the canonical SMALL LIVE adapter (live_parity.LIVE_ADAPTER_VERSION)
ISSUER = "SMALL_LIVE_ADAPTER_V2"


class LiveAuthorization:
    """An authorization to originate one live order from one canonical
    intent. Only live_parity.issue_live_authorization constructs one, and
    only outside SHADOW -- which this release has no way to be."""
    __slots__ = ("issued_by", "intent_id", "content_sha", "issued_at")

    def __init__(self, *, intent_id: str, content_sha: str, issued_at: float):
        self.issued_by = ISSUER
        self.intent_id = intent_id
        self.content_sha = content_sha
        self.issued_at = issued_at


def authorized(token, *, mode: str | None = None) -> bool:
    """True only for a LiveAuthorization the canonical adapter issued, and
    only outside SHADOW. Always False in this release."""
    m = SMALL_LIVE_MODE if mode is None else mode
    return (token is not None and m != SHADOW
            and isinstance(token, LiveAuthorization)
            and getattr(token, "issued_by", None) == ISSUER)


_PRESENTED: contextvars.ContextVar = contextvars.ContextVar(
    "canonical_live_authorization", default=None)


def presented():
    """The token the current call presented, or None."""
    return _PRESENTED.get()


@contextlib.contextmanager
def presenting(token):
    """Present `token` to the venue adapter for the duration of one call."""
    reset = _PRESENTED.set(token)
    try:
        yield
    finally:
        _PRESENTED.reset(reset)
