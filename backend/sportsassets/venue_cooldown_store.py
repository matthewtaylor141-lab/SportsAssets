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

KEY_PREFIX = "venue_cooldown"

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
    row = {
        "not_before_epoch_s": state.get("not_before_epoch_s")
                              or state.get("expires_at_epoch_s"),
        "reason": state.get("reason"),
        "retry_after_s": state.get("retry_after_s"),
        "scope": scope,
        "venue": venue,
        "written_at_epoch_s": state.get("written_at_epoch_s"),
        "expiry_is_epoch_not_monotonic": (
            "monotonic is comparable only within one process and one boot; "
            "this value is wall-clock and safe to read back elsewhere"),
    }
    if row["not_before_epoch_s"] is None:
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

    exp = row.get("not_before_epoch_s")
    gate = grt.hold_until(until_epoch_s=exp,
                          reason=row.get("reason") or "RESUMED_AFTER_RESTART")
    rate = vp.resume_cooldown(exp, reason=row.get("reason"))
    return {"resumed": bool(gate.get("applied") and rate.get("resumed")),
            "key": k, "stored": True, "row": row,
            "gate": gate, "reduced_rate": rate,
            "both_controls_restored": (
                "the not-before gate and the reduced-rate period are "
                "separate controls and both are re-armed; restoring one "
                "would leave the process either free to send or merely "
                "slowed")}


__all__ = ["save", "load_and_resume", "key_for", "KEY_PREFIX",
           "SCOPE_CREDENTIAL", "SCOPE_ACCOUNT", "SCOPE_UNKNOWN"]
