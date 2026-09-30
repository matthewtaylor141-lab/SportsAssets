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
    async def read_book(self, slug: str) -> dict:
        """One book read, off the event loop. Returns {marketData, error,
        observed_at}; never raises."""
        object.__setattr__(self, "_calls", self._calls + 1)
        t0 = time.time()
        try:
            got = await asyncio.to_thread(self._transport, slug)
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


def _default_transport(slug: str) -> dict:
    """The collection cycle's own paced, public book read (`pmus.book_read`
    through `_read_book_blocking`). Imported lazily so constructing the
    client reaches nothing."""
    from .workers import ext_pinnacle_loop as L
    return L._read_book_blocking(slug)


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
