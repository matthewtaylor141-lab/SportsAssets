"""THE PAPER VENUE GUARD: THE PAPER PATH READS MARKET DATA AND MUTATES NOTHING.

The paper session makes ZERO venue mutation calls. It is handed a
`PaperMarketDataClient`, which wraps a book-reading transport (by default
`ext_pinnacle_loop._read_book_blocking`, the collection cycle's own paced,
public book read) and exposes exactly one read, `read_book(slug)`. Every
mutation name -- place, submit, modify, replace, cancel, close, post,
create -- raises `PaperVenueMutationRefused` from `__getattr__`, BEFORE any
argument is used and before any network I/O, and the attempt is counted on
the client (and, by the session, in `paper_session_health.
mutation_attempts`, expected 0).

This is an in-process guard, like `bettor_read_only_venue` -- it stops a
paper call site from mutating by accident; the real boundary for a funded
order remains the submission switches (all False) and the credential. The
paper path additionally never imports an order-submitting function: its
"placement" is a row in `paper_orders`, simulated by
`bettor_paper_simulator`.

AND THE OTHER DIRECTION: `refuse_paper_record` is what the funded executor
(`bettor_funded_execution.submit_for_decision`) consults first, so a paper
id or a paper record can never enter the funded path.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import time
from typing import Any, Callable

VERSION = "PAPER_VENUE_GUARD_V1"

#: Attribute names that would mutate venue state. The control is the
#: ALLOWLIST (`READS`); this list only names the refusal precisely.
MUTATION_NAMES = (
    "place", "place_order", "submit", "submit_fok", "submit_order",
    "create_order", "post_order", "modify", "modify_order", "replace",
    "replace_order", "cancel", "cancel_order", "cancel_all", "close",
    "close_position")
READS = ("read_book",)

R_MUTATION_REFUSED = "PAPER_PATH_VENUE_MUTATION_REFUSED_BEFORE_TRANSMISSION"
#: The paper book read did not return inside the decision's own deadline.
R_BOOK_READ_DEADLINE = "PAPER_BOOK_READ_DEADLINE_EXCEEDED"
R_PAPER_TO_FUNDED = "A_PAPER_RECORD_NEVER_REACHES_THE_FUNDED_PATH"


class PaperVenueMutationRefused(RuntimeError):
    """Raised for any venue mutation attempted from the paper path, before
    any network I/O."""

    refusal = R_MUTATION_REFUSED


class PaperMarketDataClient:
    """READ-ONLY MARKET DATA FOR THE PAPER SESSION.

    `transport(slug) -> dict` is the only thing that touches the network; it
    is called by `read_book` and by nothing else. A mutation name raises
    PaperVenueMutationRefused and increments `mutation_attempts`; the
    transport is never reached."""

    __slots__ = ("_transport", "_calls", "_attempts", "_last_attempt",
                 "_on_attempt")

    def __init__(self, transport: Callable[[str], dict] | None = None, *,
                 on_attempt: Callable[[dict], Any] | None = None):
        if transport is None:
            transport = _default_transport
        object.__setattr__(self, "_transport", transport)
        object.__setattr__(self, "_calls", 0)
        object.__setattr__(self, "_attempts", 0)
        object.__setattr__(self, "_last_attempt", None)
        object.__setattr__(self, "_on_attempt", on_attempt)

    # ── the one read ────────────────────────────────────────────────
    async def read_book(self, slug: str, *, deadline_epoch_s=None,
                        timeout_s=None, not_before_epoch=None) -> dict:
        """One book read, off the event loop. Returns {marketData, error,
        observed_at}; never raises.

        WITH A DEADLINE (the in-cycle decision's), the read is BOUNDED by it:
        the venue request gate is told the deadline (it refuses by name rather
        than queue past it), and the wait is cut at `timeout_s`. Before this,
        the read carried NO deadline, so the gate could hold it for its 20 s
        undeadlined cap (plus the SDK's 30 s HTTP timeout) while the in-cycle
        decision around it was cancelled at 8 s -- and a cancelled decision
        recorded nothing at all.

        WITH `not_before_epoch` (R30A), the answer must be a book RECEIVED at
        or after that instant: a pending marketable entry needs a book
        observed at or after its eligible instant, and the 6 s shared-read
        cache otherwise answered its after-delay read with the decision's own
        pre-eligible receipt -- recorded outside the order's window, so the
        read was wasted (research-sql run 37233864395, E2: 22 of 60 filled
        entries had such a read). A transport that does not take the
        argument (a test stand-in) is called exactly as before."""
        object.__setattr__(self, "_calls", self._calls + 1)
        t0 = time.time()
        kw = {}
        if deadline_epoch_s is not None and _accepts(
                self._transport, "deadline_epoch_s"):
            kw["deadline_epoch_s"] = deadline_epoch_s
        if not_before_epoch is not None and _accepts(
                self._transport, "not_before_epoch"):
            kw["not_before_epoch"] = float(not_before_epoch)
        call = functools.partial(self._transport, slug, **kw)
        try:
            fut = asyncio.to_thread(call)
            got = (await asyncio.wait_for(fut, float(timeout_s))
                   if timeout_s is not None else await fut)
        except asyncio.TimeoutError:
            return {"marketData": None, "error": R_BOOK_READ_DEADLINE,
                    "observed_at": t0, "waited_s": round(time.time() - t0, 3),
                    "timeout_s": timeout_s}
        except Exception as exc:                               # noqa: BLE001
            return {"marketData": None, "error": type(exc).__name__,
                    "observed_at": t0}
        got = dict(got or {})
        got.setdefault("observed_at", time.time())
        return got

    @property
    def transport_calls(self) -> int:
        return self._calls

    @property
    def mutation_attempts(self) -> int:
        return self._attempts

    @property
    def last_mutation_attempt(self) -> dict | None:
        return self._last_attempt

    # ── the guard ───────────────────────────────────────────────────
    def __getattr__(self, name: str):
        if name in MUTATION_NAMES or name.startswith(
                ("place", "submit", "cancel", "modify", "replace", "close",
                 "post", "create")):
            rec = {"attempted": name, "at": time.time(),
                   "refusal": R_MUTATION_REFUSED,
                   "transport_calls_at_refusal": self._calls}
            object.__setattr__(self, "_attempts", self._attempts + 1)
            object.__setattr__(self, "_last_attempt", rec)
            cb = self._on_attempt
            if cb is not None:
                try:
                    cb(rec)
                except Exception:                              # noqa: BLE001
                    pass
            raise PaperVenueMutationRefused(
                "%r is a venue mutation; the paper session is read-only and "
                "refuses it before any network I/O (%s)"
                % (name, R_MUTATION_REFUSED))
        raise AttributeError("%r is not a paper market-data read; the "
                             "client exposes only %s" % (name, READS))

    def __setattr__(self, name, value):
        raise AttributeError("the paper market-data client is immutable")

    def __repr__(self):
        return "PaperMarketDataClient(reads=%s, mutation_attempts=%d)" % (
            READS, self._attempts)


#: A book read by this process (the collection cycle valuing the contract,
#: or an earlier paper read) at most this many seconds ago answers a paper
#: read without a second venue request. Below the decisions' own 10 s book
#: age limit, which they still apply to the shared read's receipt instant.
SHARED_BOOK_MAX_AGE_S = 6.0


def _default_transport(slug: str, *, deadline_epoch_s=None,
                       not_before_epoch=None) -> dict:
    """The collection cycle's own paced, public book read (`pmus.book_read`
    through `_read_book_blocking`), with the caller's deadline handed to the
    venue request gate -- or, when this process read the same book within
    SHARED_BOOK_MAX_AGE_S, that read, with its original receipt instant and
    `shared_read: True` (no venue request; nothing is made fresher than it
    is). Imported lazily so constructing the client reaches nothing.

    `not_before_epoch` (R30A): a shared read received BEFORE that instant
    does not answer -- the caller needs a book observed at or after it -- so
    the venue is read instead (paced and gated exactly as any other read)."""
    from .workers import ext_pinnacle_loop as L
    shared = L.recent_book(slug, max_age_s=SHARED_BOOK_MAX_AGE_S)
    if shared is not None and (
            not_before_epoch is None
            or float(shared.get("observed_at") or 0.0)
            >= float(not_before_epoch)):
        return shared
    return L._read_book_blocking(slug, deadline_epoch_s=deadline_epoch_s)


def _accepts(transport, name: str) -> bool:
    """Whether a transport takes the keyword `name` (the default takes
    `deadline_epoch_s` and `not_before_epoch`; a test's one-argument
    transport takes neither, and is called as before)."""
    try:
        return name in inspect.signature(transport).parameters
    except (TypeError, ValueError):
        return False


def _accepts_deadline(transport) -> bool:
    """Whether a transport takes `deadline_epoch_s` (the default does; a
    test's one-argument transport does not, and is called as before)."""
    return _accepts(transport, "deadline_epoch_s")


# ═════════════════════════════════════════════════════════════════════
# THE OTHER DIRECTION: NOTHING PAPER ENTERS THE FUNDED PATH
# ═════════════════════════════════════════════════════════════════════

PAPER_MARKERS = ("paper",)


def _is_paper(v) -> bool:
    return isinstance(v, str) and v.strip().lower().startswith(PAPER_MARKERS)


def refuse_paper_record(rec: Any, **ids) -> dict | None:
    """None when nothing here is paper; otherwise a refusal naming what was.

    Checked: every id the caller passes (account, operation, group, intent,
    decision), and on the record itself any id-like field, a paper
    `record_purpose`, a SIMULATED data label or a simulator event source."""
    found = []
    for k, v in ids.items():
        if _is_paper(v):
            found.append(k)
    r = rec if isinstance(rec, dict) else {}
    for k in ("order_id", "decision_id", "fill_id", "group_id", "session_id",
              "account_id", "intent_id", "valuation_row_id", "paper_id",
              "idempotency_key"):
        if _is_paper(r.get(k)):
            found.append("rec.%s" % k)
    if str(r.get("record_purpose") or "").upper().startswith("PAPER"):
        found.append("rec.record_purpose")
    if "SIMULATED" in str(r.get("data_label") or "").upper():
        found.append("rec.data_label")
    if str(r.get("event_source") or "").upper() == "SIMULATOR":
        found.append("rec.event_source")
    if r.get("paper") is True:
        found.append("rec.paper")
    if not found:
        return None
    return {"refusal": R_PAPER_TO_FUNDED, "paper_fields": found,
            "why": ("paper orders, fills, positions and authorizations live in "
                    "separate paper tables and are never an input to the "
                    "funded executor")}


def describe() -> dict:
    return {"version": VERSION, "reads": READS,
            "mutations_refused": MUTATION_NAMES,
            "refusal": R_MUTATION_REFUSED,
            "funded_refusal": R_PAPER_TO_FUNDED}
