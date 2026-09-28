"""DURABLE VENUE COOLDOWN: a real write, and a real read at startup.

── WHY THIS FILE EXISTS ────────────────────────────────────────────
The previous change produced a PORTABLE expiry -- epoch seconds rather
than `time.monotonic()` -- and stopped there. A repository search found no
production caller of `cooldown_state()` or `resume_cooldown()`, so nothing
was ever written and nothing was ever restored. A test that calls
`resume_cooldown()` by hand proves the function works; it proves nothing
about the deployed process.

That is the same shape as two defects already found here: `penalize()`
existed and nothing called it, and the servicing digest was computed and
dropped. A capability with no caller is not a capability.

── THE FAIL-OPEN IT CLOSES ─────────────────────────────────────────
The hold lives in module globals, so it dies with the process. A
crash-looping process therefore resumes at FULL RATE immediately after the
venue rate-limited the account -- the worst possible moment, and the one
most likely to follow a 429 storm.

── SCOPE, STATED RATHER THAN ASSUMED ───────────────────────────────
The row is keyed by LIMITER SCOPE, not globally, because a rate limit
belongs to whatever the venue counts against. We do not yet know whether
that is the credential, the account or the source IP -- so the scope is
recorded IN THE KEY and defaults to the venue plus credential id. When the
true scope is established, old rows simply stop matching rather than being
silently reinterpreted.

This module does no I/O of its own: the caller supplies the connection, so
it cannot open a second pool or block an event loop.
"""

import threading
import time

KEY_PREFIX = "venue_cooldown"

#: ── THE HANDOFF, BECAUSE THE OBSERVER OF A 429 HAS NO DATABASE ──────
#:
#: `pmus.book_read` is the only place that sees the rate-limit response, and
#: it is a synchronous market-data function called from a worker thread. It
#: has no connection and must not acquire one: opening a pool on a
#: market-data path, or blocking a thread on a write while a decision
#: deadline runs down, trades the thing we are protecting for the record of
#: having protected it.
#:
#: So the observation is QUEUED here -- one slot, last writer wins, because
#: `hold_until` extends only and the newest state already subsumes the older
#: -- and the loop drains it to the database on its own cycle, where it has
#: a connection and can await.
#:
#: A QUEUED SAVE IS NOT A SAVE, and `queue_save` says so in its return. The
#: distinction matters: if the process dies between the 429 and the drain,
#: the hold is lost, and that window is reported rather than papered over.
_pending_lock = threading.Lock()
_pending: dict | None = None
_drain_stats = {"queued": 0, "drained": 0, "dropped_superseded": 0,
                "save_failed": 0}


def queue_save(state: dict) -> dict:
    """Hand a fresh cooldown to whoever next has a connection.

    Returns what it did, and explicitly does NOT claim the hold is durable
    yet. Nothing here awaits, raises, or touches a socket.
    """
    global _pending
    if not isinstance(state, dict):
        return {"queued": False, "why": "no cooldown state to queue"}
    row = dict(state)
    row["written_at_epoch_s"] = time.time()
    with _pending_lock:
        superseded = _pending is not None
        _pending = row
        _drain_stats["queued"] += 1
        if superseded:
            _drain_stats["dropped_superseded"] += 1
    return {"queued": True,
            "is_durable_yet": False,
            "superseded_an_undrained_one": superseded,
            "why_not_written_here": (
                "the 429 is observed on a synchronous market-data path with "
                "no connection; writing from here would mean opening a pool "
                "on the read path or blocking a decision on a write"),
            "durable_after": "the loop's next drain_pending(conn)"}


def pending() -> dict | None:
    """The undrained cooldown, if any. For tests and for the heartbeat."""
    with _pending_lock:
        return dict(_pending) if _pending else None


def drain_stats() -> dict:
    with _pending_lock:
        return dict(_drain_stats)


def reset_pending() -> None:
    """Tests only."""
    global _pending
    with _pending_lock:
        _pending = None
        for k in _drain_stats:
            _drain_stats[k] = 0


async def drain_pending(conn, **kw) -> dict:
    """Write a queued cooldown, if there is one. Called by the loop.

    THE QUEUE IS CLEARED ONLY ON A SUCCESSFUL WRITE. A failed write leaves
    the observation pending so the next cycle tries again -- dropping it
    would turn a transient database error into a silently forgotten hold.
    """
    global _pending
    with _pending_lock:
        row = dict(_pending) if _pending else None
    if row is None:
        return {"drained": False, "nothing_pending": True}
    res = await save(conn, row, **kw)
    with _pending_lock:
        if res.get("saved"):
            # Only if it is still the same observation: a newer 429 during
            # the write must not be discarded by our success.
            if _pending is not None and (
                    _pending.get("written_at_epoch_s")
                    == row.get("written_at_epoch_s")):
                _pending = None
            _drain_stats["drained"] += 1
        else:
            _drain_stats["save_failed"] += 1
    return dict(res, drained=bool(res.get("saved")))

#: The scope a stored hold applies to. `CREDENTIAL` is the default because
#: an API key is the narrowest thing the venue could plausibly count, and
#: a hold applied too narrowly fails OPEN. It is written into the row so a
#: later correction is visible rather than silent.
SCOPE_CREDENTIAL = "VENUE_AND_CREDENTIAL"
SCOPE_ACCOUNT = "VENUE_AND_ACCOUNT"
SCOPE_UNKNOWN = "SCOPE_NOT_ESTABLISHED"


def key_for(*, venue: str = "PMUS", scope: str = SCOPE_CREDENTIAL,
            credential_id: str = None) -> str:
    """The durable row's key. The scope is part of it, deliberately."""
    tail = credential_id or "default"
    return "%s:%s:%s:%s" % (KEY_PREFIX, str(venue).upper(), scope, tail)


async def save(conn, state: dict, *, venue: str = "PMUS",
               scope: str = SCOPE_CREDENTIAL,
               credential_id: str = None) -> dict:
    """Persist a cooldown so a restart cannot forget it.

    NEVER RAISES INTO THE CALLER. Failing to record a cooldown must not
    take down the read path that observed the 429 -- but it IS reported, so
    "we stored it" is never assumed.
    """
    import json

    k = key_for(venue=venue, scope=scope, credential_id=credential_id)
    # ── TWO INSTANTS, STORED SEPARATELY, BECAUSE THEY ARE TWO CONTROLS ──
    #
    # This row used to hold one number under one name, which is the same
    # conflation that let a doubled 0.35 s gap be reported as a 600-second
    # cooldown. They are not the same quantity and they do not expire
    # together:
    #
    #   not_before_epoch_s     the HARD prohibition. The venue's own
    #                          `Retry-After` when it named one, else our
    #                          short floor. Usually seconds.
    #   reduced_rate_until_epoch_s
    #                          the longer period during which the ordinary
    #                          gap is multiplied. Usually ten minutes.
    #
    # A restart that restored only the first would resume at full rate after
    # a few seconds; one that restored only the second would be free to send
    # immediately and merely slowly.
    nb = state.get("not_before_epoch_s")
    if nb is None:
        nb = ((state.get("not_before") or {}).get("not_before_epoch_s")
              if isinstance(state.get("not_before"), dict) else None)
    rr = (state.get("reduced_rate_until_epoch_s")
          or state.get("expires_at_epoch_s"))
    row = {
        "not_before_epoch_s": nb,
        "reduced_rate_until_epoch_s": rr,
        "reason": state.get("reason"),
        "retry_after_s": state.get("retry_after_s"),
        "scope": scope,
        "venue": venue,
        "written_at_epoch_s": state.get("written_at_epoch_s"),
        "what_these_two_are": (
            "not_before_epoch_s is a prohibition; "
            "reduced_rate_until_epoch_s is a slower rate. Separate "
            "instants, separately restored"),
        "expiry_is_epoch_not_monotonic": (
            "monotonic is comparable only within one process and one boot; "
            "this value is wall-clock and safe to read back elsewhere"),
    }
    if row["not_before_epoch_s"] is None and row[
            "reduced_rate_until_epoch_s"] is None:
        return {"saved": False, "why": "no expiry instant to store", "key": k}
    try:
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            k, json.dumps(row))
        return {"saved": True, "key": k, "row": row}
    except Exception as exc:                                   # noqa: BLE001
        return {"saved": False, "key": k,
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:160])}


async def load_and_resume(conn, *, venue: str = "PMUS",
                          scope: str = SCOPE_CREDENTIAL,
                          credential_id: str = None) -> dict:
    """Read a stored hold at startup and re-arm BOTH controls.

    Re-arms the not-before gate AND the reduced-rate period, because they
    are two different controls and restoring only one would leave the
    process either sending freely or merely slowed.
    """
    import json

    k = key_for(venue=venue, scope=scope, credential_id=credential_id)
    try:
        raw = await conn.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key = $1", k)
    except Exception as exc:                                   # noqa: BLE001
        # A READ THAT FAILED IS NOT "NO COOLDOWN". Reported as unknown, so
        # a caller can decide rather than infer permission from an error.
        return {"resumed": False, "key": k, "read_failed": True,
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    if not raw:
        return {"resumed": False, "key": k, "stored": False,
                "why": "no stored cooldown for this scope"}
    try:
        row = json.loads(raw)
    except Exception:                                          # noqa: BLE001
        return {"resumed": False, "key": k, "stored": True,
                "why": "the stored cooldown is not readable JSON"}

    from . import venue_pace as vp
    from . import venue_request_gate as grt

    # EACH CONTROL FROM ITS OWN FIELD. A row written before the two were
    # separated carries only `not_before_epoch_s`; that value is then used
    # for both, and `row_predates_the_split` says so rather than letting the
    # reader assume the reduced-rate period was genuinely that short.
    nb = row.get("not_before_epoch_s")
    rr = row.get("reduced_rate_until_epoch_s")
    predates = rr is None
    if predates:
        rr = nb
    gate = grt.hold_until(until_epoch_s=nb,
                          reason=row.get("reason") or "RESUMED_AFTER_RESTART")
    rate = vp.resume_cooldown(rr, reason=row.get("reason"))
    # RESUMED MEANS SOMETHING WAS RE-ARMED, not that both were. An expired
    # stored instant legitimately re-arms nothing, and that is reported as
    # expired rather than as a failure.
    return {"resumed": bool(gate.get("applied") or rate.get("resumed")),
            "key": k, "stored": True, "row": row,
            "gate": gate, "reduced_rate": rate,
            "row_predates_the_split": predates,
            "both_controls_restored": bool(gate.get("applied")
                                           and rate.get("resumed")),
            "why_both": (
                "the not-before gate and the reduced-rate period are "
                "separate controls and both are re-armed; restoring one "
                "would leave the process either free to send or merely "
                "slowed")}


__all__ = ["save", "load_and_resume", "key_for", "KEY_PREFIX",
           "SCOPE_CREDENTIAL", "SCOPE_ACCOUNT", "SCOPE_UNKNOWN",
           "queue_save", "drain_pending", "pending", "drain_stats",
           "reset_pending"]
