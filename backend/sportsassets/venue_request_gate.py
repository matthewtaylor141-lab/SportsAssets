"""A REAL not-before gate, at the transport, counting every dispatch.

── WHAT THIS REPLACES, AND WHY THE LAST ATTEMPT DID NOT WORK ────────
An independent reproduction: with `Retry-After: 60` the code reported a
600-second hold, and a second logical read COMPLETED ~0.7 s later.

The reason is that `venue_pace` only multiplies the ordinary gap while a
penalty is active. Doubling 0.35 s to 0.7 s is precisely the 0.7 s that
was observed. It is a RATE REDUCTION, not a cooldown -- nothing in it
prevents a send. Calling it "the cooldown armed" was wrong, and it is the
same class of error as `penalize()` existing while nothing called it: a
control that reports instead of controlling.

── THE TWO THINGS, NOW SEPARATE ────────────────────────────────────
  NOT-BEFORE DEADLINE   an instant before which no applicable request may
                        be dispatched. A hard gate.
  REDUCED-RATE PERIOD   a longer inter-request gap afterwards. The old
                        behaviour, kept, but no longer mistaken for the
                        gate.

── WHY AT THE TRANSPORT ────────────────────────────────────────────
"Recheck immediately before transport dispatch" has exactly one honest
location: `handle_request`. Checking in the logical reader leaves the
SDK's internal retries unchecked -- which is how 1 paced call became 3
requests -- and leaves a window between the check and the send.

It also fixes the attempt count. Counting on the RESPONSE hook misses a
request that never produced one: three attempts ending in `ReadTimeout`
reported `attempts: null`. Here the attempt is counted BEFORE dispatch, so
a timeout is counted like anything else, and responses are counted
separately so the two can be compared.

── AND IT NEVER BLOCKS PAST THE CALLER'S DEADLINE ──────────────────
A background thread that sleeps out a 600-second hold and then sends,
after its caller has long timed out, is worse than a refusal: the request
arrives unattributed and unwanted. So a hold that would exceed the
supplied deadline REFUSES BY NAME instead of waiting.

THE PROTECTED WORKER IS NOT TOUCHED. This wraps a transport on a client
OUR lane constructs. Nothing here changes `bettor_live_loop`'s client, and
it introduces no retry of any order submission.
"""

import logging
import threading
import time
import uuid

log = logging.getLogger(__name__)

try:
    import httpx
except Exception:                                              # noqa: BLE001
    httpx = None

R_COOLDOWN_EXCEEDS_DEADLINE = "VENUE_COOLDOWN_EXCEEDS_THE_DECISION_DEADLINE"
R_DEADLINE_PASSED = "DECISION_DEADLINE_PASSED_BEFORE_DISPATCH"
R_HOLD_EXCEEDS_UNDEADLINED_CAP = "VENUE_COOLDOWN_EXCEEDS_THE_UNDEADLINED_WAIT_CAP"

#: The longest this gate will sleep for a caller that supplied NO deadline.
#:
#: WHY A CAP AND NOT AN HONEST "WAIT AS LONG AS THE HOLD SAYS". Not every
#: caller on this lane is a scheduled decision with a deadline -- the desk
#: sweep and the reconciliation reads are not -- and for those `dl` is None,
#: which took the sleep branch unconditionally. A 600-second `Retry-After`
#: would then have parked a request thread for ten minutes and dispatched it
#: afterwards, which is the exact failure the deadline check exists to
#: prevent, merely reached by the path that has no deadline to check.
#:
#: A CAP IS NOT A DEADLINE. It does not pretend to know when the caller
#: stopped caring; it only refuses to hold a thread open indefinitely on a
#: guess. A caller that genuinely can wait passes its own deadline and gets
#: the deadline rule instead.
MAX_UNDEADLINED_WAIT_S = 20.0

_LOCK = threading.Lock()
#: The hard gate: no applicable request dispatches before this epoch.
_not_before_epoch = 0.0
_not_before_reason = None
#: Per-logical-read accounting, keyed on a read id rather than a path.
#: A PATH KEY RACES: two callers reading the same slug share the counter,
#: and a shared reset makes each clobber the other. The id is minted per
#: logical read and the counters live and die with it.
_reads: dict = {}
#: Process totals, kept as SEPARATELY LABELLED telemetry -- useful, and
#: never confused with a per-read count.
_totals = {"dispatched": 0, "responses": 0, "rate_limited": 0,
           "refused_by_gate": 0, "waited_s": 0.0, "refused_write_locked": 0}

#: THE METHODS A PROCESS-LOCKED CLIENT MAY STILL SEND (cand21). Every order
#: mutation the SDK exposes -- create, cancel, modify, cancel_all,
#: close_position, and preview -- is a POST; every read is a GET. So the
#: transport refuses by METHOD, which also covers an SDK call added
#: tomorrow that no inventory has listed yet.
READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def check_write_lock(method: str, path: str = "") -> None:
    """Raise execution_gate.Denied for a non-read request in a process the
    execution gate has LOCKED (workers/all.py locks the workers process).
    A no-op in every process that is not locked."""
    from . import execution_gate as _eg
    why = _eg.process_lock()
    if why is None or str(method or "").upper() in READ_ONLY_METHODS:
        return
    with _LOCK:
        _totals["refused_write_locked"] += 1
    raise _eg.Denied("process_locked",
                     "%s %s refused at the transport: %s" % (method, path, why))


class VenueGateRefusal(Exception):
    """A dispatch refused by our own gate. Never a venue error.

    A DISTINCT TYPE ON PURPOSE. If this arrived as a generic exception the
    diagnostic would read it as a venue failure and the operator would go
    looking at the venue for a decision we made.
    """

    def __init__(self, refusal: str, detail: dict):
        super().__init__(refusal)
        self.refusal = refusal
        self.detail = detail


def begin_read(*, slug: str = None, deadline_epoch_s: float = None) -> str:
    """Open a logical read. Returns its id; counters are scoped to it."""
    rid = uuid.uuid4().hex[:16]
    with _LOCK:
        _reads[rid] = {"read_id": rid, "slug": slug,
                       "deadline_epoch_s": deadline_epoch_s,
                       "opened_at": time.time(),
                       "dispatched": 0, "responses": 0,
                       "rate_limited": 0, "waited_s": 0.0,
                       "statuses": [], "gate_refusals": []}
        # BOUNDED. A leaked read id must not grow this dict without limit;
        # the oldest are dropped, and `read_state` says so when one is gone.
        if len(_reads) > 64:
            for k in sorted(_reads, key=lambda k: _reads[k]["opened_at"])[:32]:
                _reads.pop(k, None)
    return rid


def end_read(read_id: str) -> dict:
    """Close a logical read and return its final counters."""
    with _LOCK:
        return dict(_reads.pop(read_id, {}) or {})


def read_state(read_id: str) -> dict | None:
    with _LOCK:
        st = _reads.get(read_id)
        return dict(st) if st else None


def attempts_for_read(read_id: str):
    """Dispatches for THIS read. None when the id is unknown.

    None IS NOT 0. 0 claims we know nothing was sent; None says the count
    is unavailable -- the distinction that stopped me concluding "the read
    never returned" from a null timestamp.
    """
    st = read_state(read_id)
    return None if st is None else st["dispatched"]


def totals() -> dict:
    """Process-wide telemetry, labelled as such."""
    with _LOCK:
        return dict(_totals, scope="PROCESS_SINCE_IMPORT",
                    is_not_a_per_read_count=True)


# ── THE GATE ────────────────────────────────────────────────────────

def hold_until(*, until_epoch_s: float, reason: str = None) -> dict:
    """Set the hard not-before instant. Extends, never shortens."""
    global _not_before_epoch, _not_before_reason
    try:
        until = float(until_epoch_s)
    except (TypeError, ValueError):
        return {"applied": False, "why": "not a number"}
    if until != until or until in (float("inf"), float("-inf")):
        return {"applied": False, "why": "NaN or infinite is not an instant"}
    with _LOCK:
        # EXTENDS ONLY. A later 429 with a shorter Retry-After must not
        # shorten a hold already in force -- that would let a burst of
        # responses talk us down to the smallest one.
        if until > _not_before_epoch:
            _not_before_epoch = until
            _not_before_reason = reason
        return {"applied": True, "not_before_epoch_s": _not_before_epoch,
                "reason": _not_before_reason,
                "seconds_from_now": max(0.0, _not_before_epoch - time.time())}


def gate_state(now: float = None) -> dict:
    wall = time.time() if now is None else float(now)
    with _LOCK:
        nb, why = _not_before_epoch, _not_before_reason
    return {"blocking": bool(nb > wall),
            "not_before_epoch_s": nb or None,
            "seconds_left": max(0.0, nb - wall) if nb else 0.0,
            "reason": why}


def clear_hold() -> None:
    """Tests and an explicit operator reset only."""
    global _not_before_epoch, _not_before_reason
    with _LOCK:
        _not_before_epoch = 0.0
        _not_before_reason = None


def check_before_dispatch(*, read_id: str = None, deadline_epoch_s=None,
                          now: float = None, sleep=None) -> dict:
    """Enforce the not-before instant. Refuse rather than outlive a deadline.

    Returns what it did. Raises `VenueGateRefusal` when the wait cannot be
    honoured inside the caller's deadline -- because the alternative is a
    thread that sends after everyone has stopped listening.
    """
    wall = time.time() if now is None else float(now)
    st = read_state(read_id) if read_id else None
    dl = deadline_epoch_s
    if dl is None and st is not None:
        dl = st.get("deadline_epoch_s")

    if dl is not None and wall >= float(dl):
        detail = {"refusal": R_DEADLINE_PASSED, "now_epoch_s": wall,
                  "deadline_epoch_s": float(dl),
                  "why": ("the decision deadline passed before this request "
                          "was dispatched, so sending it could only produce "
                          "evidence for a decision already abandoned")}
        _note_gate_refusal(read_id, detail)
        raise VenueGateRefusal(R_DEADLINE_PASSED, detail)

    g = gate_state(now=wall)
    if not g["blocking"]:
        return {"waited_s": 0.0, "gated": False}

    wait = g["seconds_left"]
    if dl is None and wait > MAX_UNDEADLINED_WAIT_S:
        detail = {"refusal": R_HOLD_EXCEEDS_UNDEADLINED_CAP,
                  "seconds_left": round(wait, 3),
                  "not_before_epoch_s": g["not_before_epoch_s"],
                  "cap_s": MAX_UNDEADLINED_WAIT_S,
                  "reason": g["reason"],
                  "why": ("this caller supplied no deadline, so there is no "
                          "instant at which waiting becomes pointless -- and "
                          "a thread parked for the whole hold would dispatch "
                          "long after anything was listening. Refused at the "
                          "cap instead. A caller that can genuinely wait "
                          "should pass a deadline and be judged against it")}
        _note_gate_refusal(read_id, detail)
        raise VenueGateRefusal(R_HOLD_EXCEEDS_UNDEADLINED_CAP, detail)
    if dl is not None and (wall + wait) > float(dl):
        detail = {"refusal": R_COOLDOWN_EXCEEDS_DEADLINE,
                  "seconds_left": round(wait, 3),
                  "not_before_epoch_s": g["not_before_epoch_s"],
                  "deadline_epoch_s": float(dl),
                  "reason": g["reason"],
                  "why": ("the venue cooldown outlasts this decision's "
                          "deadline. Refused rather than waited, so no "
                          "thread sends a request after its caller has "
                          "given up on the answer")}
        _note_gate_refusal(read_id, detail)
        raise VenueGateRefusal(R_COOLDOWN_EXCEEDS_DEADLINE, detail)

    (sleep or time.sleep)(wait)
    with _LOCK:
        _totals["waited_s"] += wait
        if read_id and read_id in _reads:
            _reads[read_id]["waited_s"] += wait
    return {"waited_s": wait, "gated": True, "reason": g["reason"]}


def _note_gate_refusal(read_id, detail) -> None:
    with _LOCK:
        _totals["refused_by_gate"] += 1
        if read_id and read_id in _reads:
            _reads[read_id]["gate_refusals"].append(dict(detail))


def note_dispatch(read_id: str = None) -> None:
    """Count an attempt BEFORE it is sent.

    Before, not after, so a request that never produces a response -- a
    `ReadTimeout` -- is still counted. Counting on the response hook
    reported `attempts: null` for three timed-out attempts.
    """
    with _LOCK:
        _totals["dispatched"] += 1
        if read_id and read_id in _reads:
            _reads[read_id]["dispatched"] += 1


def note_response(read_id: str = None, status: int = None) -> None:
    with _LOCK:
        _totals["responses"] += 1
        if status == 429:
            _totals["rate_limited"] += 1
        if read_id and read_id in _reads:
            r = _reads[read_id]
            r["responses"] += 1
            if status is not None and len(r["statuses"]) < 16:
                r["statuses"].append(int(status))
            if status == 429:
                r["rate_limited"] += 1


def trip_circuit_on_order_429(method, path="") -> bool:
    """A 429 ON AN ORDER REQUEST (any method that is not a read: the
    preview, the create, the close, a cancel) TRIPS THE SHARED CIRCUIT.

    THE GAP THIS CLOSES (R30A chaos review, reproduced: a preview, then a
    429 on the create, and `venue_pace.penalty_left()` 0.0 afterwards).
    Program section 21 requires a 429 to be raised by name AND to trip the
    circuit. Every funded / copy / desk order reaches the venue through
    pmus.submit_fok or pmus.close_position on the client this transport
    wraps, and both answered a 429 by name -- raised, or as the named
    refusal -- but neither tripped venue_pace: only the copy worker did, at
    its own layer (workers/mirror_live._rate_limited), and the funded lane's
    send boundary records a raised send as a lost acknowledgement without
    touching the circuit. So a funded acquisition or exit that met a 429 left
    every lane in the process on the ordinary gap, into a venue that had
    just said slow down. READS were already covered (pmus.paced_read arms
    the cooldown from a measured 429 via _arm_cooldown_from).

    It is done HERE, at the transport, because this is the one place every
    order request on the funded credential passes -- whatever function sent
    it -- and because it leaves the adapter functions themselves untouched
    (their sources are pinned). The decision is by the response's own status
    code, never a text. `venue_pace.penalize()` is the same shared circuit
    execmirror.Venue and the copy worker trip (idempotent across a burst, so
    a caller that also trips it changes nothing). Nothing is retried and no
    request is added; the SDK still raises the 429 by name to its caller.
    Never raises."""
    if str(method or "").upper() in READ_ONLY_METHODS:
        return False
    try:
        from . import venue_pace as _vp
        _vp.penalize()
        log.warning("venue 429 on %s %s: the shared venue circuit is tripped "
                    "(gap x%s for %ss)", str(method).upper(), path,
                    _vp.PENALTY_MULT, _vp.PENALTY_S)
        return True
    except Exception:                                          # noqa: BLE001
        return False


# ── THE TRANSPORT ───────────────────────────────────────────────────

_current_read = threading.local()


def bind_read(read_id: str | None) -> None:
    """Bind a logical read to THIS thread, so the transport can attribute."""
    _current_read.rid = read_id


def current_read() -> str | None:
    return getattr(_current_read, "rid", None)


if httpx is not None:

    class PacedTransport(httpx.BaseTransport):
        """Wraps a real transport: gate, count, pace, dispatch.

        EVERY actual HTTP request passes through here, including any the
        SDK retries internally, which is the property the logical-read
        wrapper could not have.
        """

        def __init__(self, inner, *, pace=None):
            self._inner = inner
            self._pace = pace

        def handle_request(self, request):
            rid = current_read()
            # 0 · THE PROCESS WRITE LOCK (cand21): in a locked process no
            #     request that is not a read leaves, whatever called it.
            check_write_lock(getattr(request, "method", ""),
                             getattr(getattr(request, "url", None), "path", ""))
            # 1 · THE HARD GATE, immediately before dispatch. This is the
            #     recheck: nothing happens between it and the send.
            check_before_dispatch(read_id=rid)
            # 2 · THE ORDINARY RATE GAP, after the gate and not instead
            #     of it. These are two different controls.
            if self._pace is not None:
                try:
                    self._pace()
                except Exception:                              # noqa: BLE001
                    pass
            # 3 · COUNTED BEFORE THE SEND.
            note_dispatch(rid)
            resp = self._inner.handle_request(request)
            note_response(rid, getattr(resp, "status_code", None))
            # 4 · AN ORDER REQUEST'S 429 TRIPS THE SHARED CIRCUIT
            if getattr(resp, "status_code", None) == 429:
                trip_circuit_on_order_429(
                    getattr(request, "method", ""),
                    getattr(getattr(request, "url", None), "path", ""))
            return resp

        def close(self):
            try:
                self._inner.close()
            except Exception:                                  # noqa: BLE001
                pass

else:                                                          # pragma: no cover
    PacedTransport = None


__all__ = ["PacedTransport", "begin_read", "end_read", "read_state",
           "attempts_for_read", "totals", "hold_until", "gate_state",
           "clear_hold", "check_before_dispatch", "check_write_lock",
           "READ_ONLY_METHODS", "note_dispatch",
           "note_response", "bind_read", "current_read",
           "VenueGateRefusal", "R_COOLDOWN_EXCEEDS_DEADLINE",
           "R_DEADLINE_PASSED", "R_HOLD_EXCEEDS_UNDEADLINED_CAP",
           "MAX_UNDEADLINED_WAIT_S"]
