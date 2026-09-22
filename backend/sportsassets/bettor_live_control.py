"""The observation loop's own stop control, in the database.

WHY NOT THE ENVIRONMENT VARIABLE. `BETTOR_LIVE_LOOP=off` was set on
sportsassets-workers at 2026-09-21T22:38:58Z and acknowledged (HTTP
200, three characters stored). Render raised no deploy for it. An
explicit restart was then requested and DID happen --
`server_restarted` at 22:43:40.131647Z in the service's own event log
-- and at 22:47:29Z the loop was still running the discovery path
rather than logging that it was disabled. **The restarted process did
not read the new value.**

Stage 1 completed that finding rather than overturning it. A NEW DEPLOY
of the same variable WAS read: at 2026-09-22T00:31:22.805Z the new
process logged that `BETTOR_LIVE_LOOP=off` and returned before
constructing a client. So the variable is readable, but only a deploy
delivers it -- minutes, and a new process -- whereas this row takes
effect inside the process already running, within `CONTROL_EVERY_S`.
That is the reason the loop must not depend on the environment to stop:
not that the variable is inert, but that it is not PROMPT.

This control lives in `ingestion_state`, the same table the execution
gate's pause switch lives in, and it is read by the running process on
a timer. Changing it needs no deploy, no restart and no environment
change: one row, one UPDATE, and the loop stops within
`CONTROL_EVERY_S`.

IT FAILS CLOSED, and there are three separate ways to be closed:

  * UNREADABLE -- the query raised. A control we cannot read is not
    permission to keep observing.
  * ABSENT -- no row. Following `execution_gate.read_state`, absence is
    not permission. The row has to say, in so many words, that
    observation may run.
  * MALFORMED -- a row that is not a boolean and not `{"run": bool}`.
    A value nobody can parse is not a yes.

Only an explicit, parsable `true` runs. That also makes activation an
ordinary database write rather than a deployment, which is the point:
the loop ships registered and STOPPED, and someone turns it on.

THIS IS NOT A TRADING CONTROL. It governs one observation loop. It
does not read, write or influence `live_trading_paused`, `mirror_live`
or the execution gate, and it cannot authorize an order -- the loop it
stops has no order path to begin with.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

log = logging.getLogger(__name__)

CONTROL_VERSION = "BETTOR_LIVE_CONTROL_V1"

# The one row this module reads. Namespaced to the observation lane so
# it can never be confused with a trading switch.
CONTROL_KEY = "bettor_live_observation"

# How often the running loop re-reads it. Short enough that a stop is
# operationally quick, long enough that it is not a hot query.
CONTROL_EVERY_S = 30.0

# THE READ IS BOUNDED. Resolving the process pool can retry for a long
# time against a database that is down, and a control read that hangs
# is a control that has no answer -- which is indistinguishable, from
# the outside, from a loop that decided to keep going. The wait is
# capped and the timeout is one more way to be CLOSED.
CONTROL_READ_TIMEOUT_S = 10.0

# Why the loop is not running, enumerated so a report can count them.
W_RUN = "RUNNING"
W_STOPPED = "STOPPED_BY_CONTROL"
W_ABSENT = "CONTROL_ROW_ABSENT"
W_UNREADABLE = "CONTROL_UNREADABLE"
W_MALFORMED = "CONTROL_MALFORMED"

_CLOSED = (W_STOPPED, W_ABSENT, W_UNREADABLE, W_MALFORMED)


def _parse(raw) -> tuple[bool, str, str]:
    """(run, why, detail) from the stored value.

    Accepts a bare JSON boolean and the object form `{"run": bool}`.
    The object form exists so an operator can leave a note beside the
    switch -- `{"run": false, "by": "...", "at": "..."}` -- without the
    note changing what the switch means. Anything else is MALFORMED,
    which is closed.
    """
    val = raw
    if isinstance(val, (bytes, bytearray)):
        val = val.decode("utf-8", "replace")
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except (TypeError, ValueError):
            return False, W_MALFORMED, "value is not JSON"
    if isinstance(val, bool):
        return (val, W_RUN if val else W_STOPPED,
                "bare boolean %s" % ("true" if val else "false"))
    if isinstance(val, dict):
        if "run" not in val:
            return False, W_MALFORMED, "object has no 'run' key"
        r = val.get("run")
        if not isinstance(r, bool):
            return False, W_MALFORMED, (
                "'run' is %s, not a boolean"
                % ("null" if r is None else type(r).__name__))
        return (r, W_RUN if r else W_STOPPED,
                "object run=%s" % ("true" if r else "false"))
    return False, W_MALFORMED, (
        "value decodes to %s, and only true, false or {\"run\": bool} "
        "may release the control"
        % ("null" if val is None else type(val).__name__))


async def read_control(pool=None) -> dict:
    """The authoritative read. EVERY failure answers `run: False`.

    `pool` is passed in by the loop (and by the tests). When it is
    None the module resolves the process pool itself, so a caller that
    has none -- a script, a probe -- still gets a real answer rather
    than an exception.
    """
    out = {"control": CONTROL_VERSION, "key": CONTROL_KEY,
           "run": False, "why": W_UNREADABLE, "detail": None,
           "read_at": time.time(), "readable": False}
    async def _fetch():
        p = pool
        if p is None:
            from .db import get_pool
            p = await get_pool()
        return await p.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1", CONTROL_KEY)

    try:
        row = await asyncio.wait_for(_fetch(),
                                     timeout=CONTROL_READ_TIMEOUT_S)
    except asyncio.TimeoutError:
        out["detail"] = "read did not answer in %.0fs" % \
            CONTROL_READ_TIMEOUT_S
        return out
    except Exception as exc:                               # noqa: BLE001
        # NAMED, not swallowed. "The control is unreadable" and "the
        # control says stop" are different operational facts and the
        # report has to be able to tell them apart.
        out["detail"] = "read failed: %s" % type(exc).__name__
        return out

    out["readable"] = True
    if row is None:
        out.update(why=W_ABSENT,
                   detail="no row for %s; absence is not permission"
                          % CONTROL_KEY)
        return out
    run, why, detail = _parse(row)
    out.update(run=run, why=why, detail=detail)
    return out


def is_closed(state: dict) -> bool:
    """True when the loop must not observe. The inverse of one flag."""
    return not bool((state or {}).get("run")) or \
        (state or {}).get("why") in _CLOSED


def describe() -> dict:
    return {
        "control": CONTROL_VERSION,
        "table": "ingestion_state",
        "key": CONTROL_KEY,
        "checked": "at startup, then every %.0f s while running"
                   % CONTROL_EVERY_S,
        "fails_closed_on": [W_UNREADABLE, W_ABSENT, W_MALFORMED],
        "runs_only_on": "an explicit true, or {\"run\": true}",
        "changes_without_deploy": True,
        "governs": "the bettor observation loop only",
        "does_not_govern": ["live_trading_paused", "mirror_live",
                            "the execution gate", "any order path"],
        "why_not_env": ("BETTOR_LIVE_LOOP=off was set and the service "
                        "was restarted (server_restarted "
                        "2026-09-21T22:43:40.131647Z); the restarted "
                        "process still did not read it"),
    }


# ── THE PROBE ALLOWANCE, RESERVED BEFORE EVERY REQUEST ───────────────
#
# A BUDGET THAT RESETS ON RESTART IS NOT A BUDGET, and a budget charged
# AFTER the request is not a budget either. The first version got the
# first half right and the second half wrong, and the second half is
# where it failed. Measured on 2026-09-22 against real PostgreSQL:
#
#   * the charge sat downstream of the EMPTY_UNIVERSE early return, so
#     40 distinct markets were read and the row recorded nothing;
#   * a crash between dispatch and charge (four lifetimes, 160 BBO
#     attempts) left the row at consumed=0 and still open;
#   * the cap counted SUCCESSES, so 30 failed reads bought another
#     round -- 70 distinct markets against a ceiling of 40, in one
#     lifetime, with no crash at all;
#   * the listing had no durable accounting whatsoever.
#
# The order is therefore inverted. NOTHING IS DISPATCHED THAT WAS NOT
# RESERVED FIRST, and a reservation is never refunded:
#
#   * a listing attempt is reserved before each listing request;
#   * a distinct-market slot is reserved before a market's FIRST BBO
#     request, keyed by slug so a retry does not buy a second slot;
#   * a BBO-attempt slot is reserved before EVERY attempt, retries
#     included;
#   * the control and the absolute deadline are checked inside the same
#     transaction as the reservation, so neither can go stale between
#     the check and the dispatch it authorises.
#
# FAILED RESPONSES, REJECTED CANDIDATES AND EMPTY UNIVERSES STILL
# CONSUME. The allowance buys REQUESTS, not results. There is no refund
# path in this module, and a test asserts no counter is ever decremented.
#
# A CRASH AFTER RESERVING WASTES ALLOWANCE. That is deliberate and is
# the safe direction: the alternative -- confirming consumption after
# the response -- is exactly the bug above. Wasting is bounded by the
# caps; replenishing is not.
#
# WHY AN EXPLICIT TRANSACTION AND `FOR UPDATE`. A single statement is
# atomic but NOT sufficient. A data-modifying CTE evaluates its
# decision against the statement's snapshot; under READ COMMITTED two
# concurrent reservations both see `reserved = 39 < cap = 40`, the
# second blocks on the row lock, and on waking re-checks only its WHERE
# clause -- not the already-materialised decision -- and commits
# anyway. That over-grants. Locking the row first and deciding inside
# the transaction serialises reservations properly, which is what
# `test_concurrent_reservations_never_exceed_the_cap` and the real
# PostgreSQL reproduction both exercise.
BUDGET_KEY = "bettor_live_probe_state"

# Defaults. Whoever arms the probe writes the real numbers into the row,
# so the figures in an approval are the figures being enforced.
PROBE_MAX_DISTINCT = 40
PROBE_MAX_BBO_ATTEMPTS = 160
PROBE_MAX_LISTING_ATTEMPTS = 18
PROBE_DEADLINE_S = 1800.0

# The three counters, and the cap each is checked against. The names are
# the JSON keys, passed to SQL as data rather than interpolated.
R_DISTINCT = "distinct"
R_ATTEMPT = "bbo_attempt"
R_LISTING = "listing"

_COUNTER = {R_DISTINCT: "distinct_reserved",
            R_ATTEMPT: "bbo_attempts_reserved",
            R_LISTING: "listing_attempts_reserved"}
_CAP = {R_DISTINCT: "max_distinct",
        R_ATTEMPT: "max_bbo_attempts",
        R_LISTING: "max_listing_attempts"}
_DEFAULT_CAP = {R_DISTINCT: PROBE_MAX_DISTINCT,
                R_ATTEMPT: PROBE_MAX_BBO_ATTEMPTS,
                R_LISTING: PROBE_MAX_LISTING_ATTEMPTS}

B_OPEN = "OPEN"
B_EXHAUSTED = "BUDGET_EXHAUSTED"
B_EXPIRED = "DEADLINE_PASSED"
B_UNREADABLE = "BUDGET_UNREADABLE"

# A reservation either grants or it does not, and only the first two of
# these are a grant. Everything else means DO NOT DISPATCH.
V_GRANTED = "RESERVED"
V_ALREADY = "ALREADY_RESERVED"          # this probe already holds it
V_EXHAUSTED = "RESERVATION_EXHAUSTED"
V_EXPIRED = "RESERVATION_DEADLINE_PASSED"
V_STOPPED = "RESERVATION_STOPPED_BY_CONTROL"
V_ABSENT = "RESERVATION_NO_BUDGET_ROW"
V_MISMATCH = "RESERVATION_PROBE_MISMATCH"
V_UNREADABLE = "RESERVATION_UNREADABLE"

_GRANTS = (V_GRANTED, V_ALREADY)


def granted(res: dict) -> bool:
    """One place decides what counts as permission to dispatch.

    UNCERTAINTY IS NOT PERMISSION. A reservation whose write may or may
    not have landed -- a timeout, a dropped connection, a lost
    acknowledgement -- answers `V_UNREADABLE` and this returns False.
    The allowance may have been spent; the request is still not sent.
    """
    return bool(res) and res.get("why") in _GRANTS


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _parse_iso(raw) -> float | None:
    from datetime import datetime
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")) \
            .timestamp()
    except (TypeError, ValueError):
        return None


def _as_obj(row):
    """The row's value as a dict, or None. asyncpg hands back jsonb as
    str; a test double may hand back a dict already."""
    val = row
    if isinstance(val, (bytes, bytearray)):
        val = val.decode("utf-8", "replace")
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except (TypeError, ValueError):
            return None
    return val if isinstance(val, dict) else None


async def _resolve(pool):
    if pool is not None:
        return pool
    from .db import get_pool
    return await get_pool()


async def reserve(pool, kind: str, *, slug: str | None = None,
                  probe_id: str | None = None,
                  timeout_s: float = CONTROL_READ_TIMEOUT_S) -> dict:
    """Reserve ONE unit of allowance, durably, BEFORE its request.

    Returns a verdict dict; `granted()` decides whether the caller may
    dispatch. Nothing here ever decrements a counter.

    `kind` is `R_LISTING`, `R_DISTINCT` or `R_ATTEMPT`. For `R_DISTINCT`
    the `slug` is required and the reservation is IDEMPOTENT in it: a
    market this probe already holds a slot for answers `V_ALREADY`,
    which is a grant that consumes nothing, so the three retries of one
    market cost one distinct slot and three attempt slots.

    `probe_id` ties the reservation to one probe identity. A row armed
    for a different probe answers `V_MISMATCH` -- so a re-arm cannot
    inherit a previous probe's in-flight reservations, and a process
    that outlived its probe cannot spend the next one's allowance.

    THE CONTROL AND THE DEADLINE ARE CHECKED IN HERE, in the same
    transaction that takes the lock. Checking them in the caller would
    leave a window in which a stop or an expiry lands between the check
    and the request it authorised.
    """
    out = {"kind": kind, "slug": slug, "probe_id": probe_id,
           "why": V_UNREADABLE, "reserved": None, "cap": None,
           "remaining": 0, "detail": None, "uncertain": True}
    if kind not in _COUNTER:
        out.update(why=V_UNREADABLE, detail="unknown kind %r" % kind,
                   uncertain=False)
        return out
    if kind == R_DISTINCT and not slug:
        out.update(why=V_UNREADABLE, uncertain=False,
                   detail="a distinct-market slot needs its slug")
        return out

    counter, capkey = _COUNTER[kind], _CAP[kind]

    async def _txn():
        p = await _resolve(pool)
        async with p.acquire() as con:
            async with con.transaction():
                # THE LOCK COMES FIRST. Everything after it -- the
                # control, the deadline, the counter -- is read under it,
                # so two reservations cannot both pass the same check.
                raw = await con.fetchval(
                    "SELECT value FROM ingestion_state WHERE key=$1 "
                    "FOR UPDATE", BUDGET_KEY)
                if raw is None:
                    return dict(out, why=V_ABSENT, uncertain=False,
                                detail="no %s row; nothing is reserved "
                                       "against an unarmed probe"
                                       % BUDGET_KEY)
                bud = _as_obj(raw)
                if bud is None:
                    return dict(out, why=V_UNREADABLE, uncertain=False,
                                detail="budget value is not an object")

                pid = bud.get("probe_id")
                if probe_id is not None and str(pid or "") != str(probe_id):
                    return dict(out, why=V_MISMATCH, uncertain=False,
                                detail="row is probe %r, caller is %r"
                                       % (pid, probe_id))

                ctl_raw = await con.fetchval(
                    "SELECT value FROM ingestion_state WHERE key=$1",
                    CONTROL_KEY)
                if ctl_raw is None:
                    return dict(out, why=V_STOPPED, uncertain=False,
                                probe_id=pid,
                                detail="control row absent; absence is "
                                       "not permission")
                run, cwhy, cdetail = _parse(ctl_raw)
                if not run:
                    return dict(out, why=V_STOPPED, uncertain=False,
                                probe_id=pid,
                                detail="control says %s (%s)"
                                       % (cwhy, cdetail))

                deadline = _parse_iso(bud.get("deadline_at"))
                if deadline is None:
                    return dict(out, why=V_UNREADABLE, uncertain=False,
                                probe_id=pid,
                                detail="deadline_at absent or unparsable")
                if time.time() >= deadline:
                    return dict(out, why=V_EXPIRED, uncertain=False,
                                probe_id=pid,
                                detail="deadline %s passed"
                                       % bud.get("deadline_at"))

                try:
                    cap = int(bud.get(capkey, _DEFAULT_CAP[kind]))
                    used = int(bud.get(counter, 0) or 0)
                except (TypeError, ValueError):
                    return dict(out, why=V_UNREADABLE, uncertain=False,
                                probe_id=pid,
                                detail="%s/%s are not integers"
                                       % (counter, capkey))

                if kind == R_DISTINCT:
                    held = bud.get("slugs") or []
                    if not isinstance(held, list):
                        return dict(out, why=V_UNREADABLE, uncertain=False,
                                    probe_id=pid,
                                    detail="slugs is not a list")
                    if slug in held:
                        # ALREADY OURS. A grant that consumes nothing --
                        # this is what keeps a retry from buying a
                        # second distinct slot.
                        return dict(out, why=V_ALREADY, uncertain=False,
                                    probe_id=pid, reserved=used, cap=cap,
                                    remaining=max(0, cap - used),
                                    detail="probe already holds %s" % slug)

                if used >= cap:
                    return dict(out, why=V_EXHAUSTED, uncertain=False,
                                probe_id=pid, reserved=used, cap=cap,
                                remaining=0,
                                detail="%d of %d %s already reserved"
                                       % (used, cap, kind))

                # THE WRITE. Under the lock, so `used + 1` cannot race.
                if kind == R_DISTINCT:
                    await con.execute(
                        "UPDATE ingestion_state SET value = jsonb_set("
                        "  jsonb_set(value, ARRAY[$2::text],"
                        "            to_jsonb($3::int)),"
                        "  '{slugs}',"
                        "  COALESCE(value->'slugs','[]'::jsonb)"
                        "  || to_jsonb($4::text)),"
                        " updated_at = now() WHERE key=$1",
                        BUDGET_KEY, counter, used + 1, slug)
                else:
                    await con.execute(
                        "UPDATE ingestion_state SET value = jsonb_set("
                        "  value, ARRAY[$2::text], to_jsonb($3::int)),"
                        " updated_at = now() WHERE key=$1",
                        BUDGET_KEY, counter, used + 1)
                return dict(out, why=V_GRANTED, uncertain=False,
                            probe_id=pid, reserved=used + 1, cap=cap,
                            remaining=max(0, cap - (used + 1)),
                            detail="%d of %d %s reserved"
                                   % (used + 1, cap, kind))

    try:
        return await asyncio.wait_for(_txn(), timeout=timeout_s)
    except asyncio.TimeoutError:
        # UNCERTAIN, AND UNCERTAIN MEANS NO. The transaction may have
        # committed. We do not dispatch, and we do not try to give the
        # unit back -- wasting allowance is the safe direction.
        out.update(detail="reservation did not answer in %.0fs; it may "
                          "have committed, so the unit is treated as "
                          "SPENT and nothing is dispatched" % timeout_s)
        return out
    except Exception as exc:                               # noqa: BLE001
        out.update(detail="reservation failed: %s; treated as SPENT and "
                          "nothing is dispatched" % type(exc).__name__)
        return out


async def read_budget(pool=None, *, now: float | None = None) -> dict:
    """The probe's allowance, as the row has it. FAILS CLOSED.

    Reports all three counters so a refusal log says which bound was
    hit. `open` tracks the DISTINCT allowance and the deadline, because
    those are what decide whether a probe may begin; the other two caps
    are enforced by `reserve()` at the point of each request, which is
    the only place enforcement belongs.
    """
    now = time.time() if now is None else now
    out = {"key": BUDGET_KEY, "state": B_UNREADABLE, "open": False,
           "probe_id": None, "remaining": 0,
           "distinct_reserved": None, "max_distinct": None,
           "bbo_attempts_reserved": None, "max_bbo_attempts": None,
           "listing_attempts_reserved": None,
           "max_listing_attempts": None,
           "slugs_held": None,
           "started_at": None, "deadline_at": None,
           "seconds_left": 0.0, "detail": None}

    async def _fetch():
        p = await _resolve(pool)
        return await p.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1", BUDGET_KEY)

    try:
        row = await asyncio.wait_for(_fetch(),
                                     timeout=CONTROL_READ_TIMEOUT_S)
    except asyncio.TimeoutError:
        out["detail"] = "budget read did not answer in %.0fs" % \
            CONTROL_READ_TIMEOUT_S
        return out
    except Exception as exc:                               # noqa: BLE001
        out["detail"] = "budget read failed: %s" % type(exc).__name__
        return out

    if row is None:
        out["detail"] = ("no %s row; a probe without a declared "
                         "allowance does not run" % BUDGET_KEY)
        return out
    val = _as_obj(row)
    if val is None:
        out["detail"] = "budget value is not a JSON object"
        return out

    try:
        cap = int(val.get("max_distinct", PROBE_MAX_DISTINCT))
        used = int(val.get("distinct_reserved", 0) or 0)
        acap = int(val.get("max_bbo_attempts", PROBE_MAX_BBO_ATTEMPTS))
        aused = int(val.get("bbo_attempts_reserved", 0) or 0)
        lcap = int(val.get("max_listing_attempts",
                           PROBE_MAX_LISTING_ATTEMPTS))
        lused = int(val.get("listing_attempts_reserved", 0) or 0)
    except (TypeError, ValueError):
        out["detail"] = "a counter or cap is not an integer"
        return out
    deadline = _parse_iso(val.get("deadline_at"))
    if deadline is None:
        out["detail"] = "deadline_at is absent or unparsable"
        return out
    held = val.get("slugs")

    out.update(probe_id=val.get("probe_id"),
               distinct_reserved=used, max_distinct=cap,
               bbo_attempts_reserved=aused, max_bbo_attempts=acap,
               listing_attempts_reserved=lused,
               max_listing_attempts=lcap,
               slugs_held=len(held) if isinstance(held, list) else None,
               started_at=val.get("started_at"),
               deadline_at=val.get("deadline_at"),
               seconds_left=round(deadline - now, 1),
               remaining=max(0, cap - used))

    if not val.get("probe_id"):
        out.update(state=B_UNREADABLE, open=False, remaining=0,
                   detail="no probe_id; reservations cannot be tied to "
                          "a probe identity")
        return out
    if now >= deadline:
        out.update(state=B_EXPIRED, open=False, remaining=0,
                   detail="deadline %s passed" % val.get("deadline_at"))
    elif used >= cap:
        out.update(state=B_EXHAUSTED, open=False, remaining=0,
                   detail="%d of %d distinct-market slots reserved"
                          % (used, cap))
    else:
        out.update(state=B_OPEN, open=True,
                   detail="probe %s: distinct %d/%d, attempts %d/%d, "
                          "listing %d/%d, %.0fs left"
                          % (str(val.get("probe_id"))[:8], used, cap,
                             aused, acap, lused, lcap, deadline - now))
    return out


async def disarm(pool, why: str) -> dict:
    """Write the observation control to FALSE. Never to true.

    This is how a deadline survives the restart that follows it: the
    loop stops itself, and the row it leaves behind stops the next
    process too. Arming is always a human action through
    `render-ops sql obs-run`; nothing in this codebase writes `true`.
    """
    try:
        # RESOLVES None, like `read_control`, `read_budget` and
        # `reserve`. In production nothing injects a pool -- the
        # supervisor calls `main()` with no arguments -- and a disarm
        # that quietly did nothing there would mean the deadline's
        # automatic shutdown never fired on the one deployment it
        # exists for.
        p = await _resolve(pool)
        await p.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, "
            "'false'::jsonb) ON CONFLICT (key) DO UPDATE SET "
            "value = 'false'::jsonb", CONTROL_KEY)
    except Exception as exc:                               # noqa: BLE001
        log.error("bettor_live_control: could not disarm (%s); the "
                  "loop stops anyway and the next process will read "
                  "whatever is there", type(exc).__name__)
        return {"disarmed": False, "why": why,
                "error": type(exc).__name__}
    log.warning("bettor_live_control: DISARMED -- %s set to false (%s)",
                CONTROL_KEY, why)
    return {"disarmed": True, "why": why}


def describe_allowance() -> dict:
    """What the allowance is and what it buys. For the refusal log."""
    return {
        "key": BUDGET_KEY,
        "reserved_before_dispatch": True,
        "refund_paths": 0,
        "caps": {"distinct_markets": PROBE_MAX_DISTINCT,
                 "bbo_attempts_incl_retries": PROBE_MAX_BBO_ATTEMPTS,
                 "listing_attempts": PROBE_MAX_LISTING_ATTEMPTS},
        "deadline_s": PROBE_DEADLINE_S,
        "deadline_is": "absolute; a restart resumes toward it",
        "buys": "REQUESTS, not results -- failures, rejected candidates "
                "and empty universes all consume",
        "identity": "probe_id; a row armed for another probe refuses",
        "serialised_by": "SELECT ... FOR UPDATE inside the reservation "
                         "transaction",
        "uncertain_is": "refused -- the unit is treated as spent and "
                        "nothing is dispatched",
    }
