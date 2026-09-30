"""THE OWNER'S AUTHORIZATION: WRITTEN BY THE OWNER, AUTHENTICATED, AUDITED,
REVOCABLE, AND VOID THE MOMENT THE SCOPE IT NAMED STOPS BEING THE SCOPE.

THE GAP THIS CLOSES. `bettor_funded_activation.authorize()` refuses a FUNDED
venue until `ingestion_state[OWNER_AUTH_KEY]` names this account, this venue
and the effective-limit digest now in force -- and nothing wrote that record.
The only way through the last funded refusal was a hand-typed row: no record
of who wrote it, which factors they presented, or what they were shown; no way
to revoke it; and nothing noticed when the binding or the approved limits moved
underneath it, so a signature given for one scope sat there looking like a
signature for the next one.

WHAT THIS MODULE DOES:

  * RECORD. `record_owner_authorization` writes the owner record only when the
    request repeats the account id, states in the owner's own words which
    account and venue it authorises, and names the CURRENT approved
    effective-limit digest, computed exactly as the consumer computes it. The
    account binding must name that exact account and venue. Both factors --
    the admin token and the owner-held resolution key -- must have been
    verified by the route, and the operator is whoever the key authenticates
    (configuration), never a name typed into the body.
  * REVOKE. `revoke_owner_authorization` marks the record revoked AND revokes
    the system authorization that was issued on it, so
    `EX.authorize_submission` -- which checks `revoked` -- refuses at once.
  * INVALIDATE. `invalidate_owner_authorization_if_scope_changed` is called by
    every writer of the scope (the binding, a limits proposal, a limits
    approval). If the active record's account/venue/digest no longer equals
    the binding plus the approved digest, it is marked invalidated and the
    system authorization issued on it is revoked.
  * AUDIT. Every attempt -- accepted, idempotent, refused, raised -- is a row
    in the append-only `bettor_funded_owner_authorization_audit` (migration
    146), following `bettor_funded_investigation`'s pattern: the audit id is
    reserved first, the effects are applied, the audit row is written last in
    the same transaction; a raise rolls back and is audited outside it.

WHAT IT DOES NOT DO. It makes no owner decision and cannot: it writes only
what the authenticated owner signs, for the scope that is already bound and
approved. It never sends, cancels or enables anything. Funded submission is
off in code whatever this record says.

NO CREDENTIAL PASSES THROUGH HERE. The route verifies the factors and hands
this module a description of WHICH were verified (AUTH_FIELDS); that
description is what the record and the audit carry.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from typing import Any

from . import bettor_funded_activation as FA

VERSION = "BETTOR_OWNER_AUTHORIZATION_V1"

AUDIT_TABLE = "bettor_funded_owner_authorization_audit"
OWNER_AUTH_KEY = FA.OWNER_AUTH_KEY
AUTHORIZATION_KEY = FA.AUTHORIZATION_KEY

#: ONE LOCK FOR EVERY READER-THEN-WRITER OF THE OWNER RECORD: the writer, the
#: revocation, the invalidation, and `authorize()` between reading the record
#: and issuing the system authorization on it. Without it a revocation landing
#: between that read and that write would leave a live system authorization
#: issued on a record the owner had just withdrawn.
LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext($1))"

#: WHICH factors the route verified -- never the factors themselves.
AUTH_FIELDS = ("admin_token_verified", "resolution_key_verified",
               "operator", "route")

# ── HOW LONG THE OWNER'S SIGNATURE LASTS ────────────────────────────
#
# THE BOUND, AND WHY. The system authorization it enables already expires
# after 24 h (`EX.AUTHORIZATION_TTL_S`) and is re-derived from this record
# each time, and every change of scope voids this record on its own. So the
# expiry here guards the one case nothing else does: a scope that has NOT
# changed, signed long enough ago that the owner may no longer have it in
# mind. Seven days by default covers one readiness window (`authorize`'s
# 168 h) without re-signing; thirty days is the ceiling, because an
# authorization for real capital that nobody has looked at in a month is not
# a current one. The owner may state a shorter lifetime; never an unbounded
# one.
DAY_S = 86400.0
DEFAULT_LIFETIME_DAYS = 7.0
MAX_LIFETIME_DAYS = 30.0

#: The statement must be at least this long -- the investigation route's bar.
STATEMENT_MIN_CHARS = 20

# ── actions and outcomes, as the audit table's CHECKs spell them ────
A_RECORD, A_REVOKE, A_INVALIDATE = "RECORD", "REVOKE", "INVALIDATE"
O_ACCEPTED = "ACCEPTED"
O_REFUSED = "REFUSED"
O_ALREADY_RECORDED = "ALREADY_RECORDED"
O_ALREADY_REVOKED = "ALREADY_REVOKED"

# ── record states ───────────────────────────────────────────────────
S_ABSENT = "ABSENT"
S_ACTIVE = "ACTIVE"
S_REVOKED = "REVOKED"
S_INVALIDATED = "INVALIDATED"
S_EXPIRED = "EXPIRED"
S_MALFORMED = "MALFORMED"

# ── refusals, each by name ──────────────────────────────────────────
R_SCHEMA = "THE_OWNER_AUTHORIZATION_AUDIT_IS_NOT_IN_THIS_DATABASE"
R_BOTH_FACTORS = "BOTH_FACTORS_ARE_REQUIRED"
R_NO_OPERATOR = "NO_OPERATOR_IS_AUTHENTICATED"
R_OPERATOR_NOT_AUTHENTICATED = \
    "THE_OPERATOR_IS_NOT_THE_IDENTITY_THE_RESOLUTION_KEY_AUTHENTICATES"
R_CONFIRM = "CONFIRM_MUST_REPEAT_THE_ACCOUNT_ID"
R_STATEMENT = "THE_OWNERS_STATEMENT_IS_TOO_SHORT_TO_SAY_WHAT_IT_AUTHORIZES"
R_STATEMENT_SCOPE = "THE_OWNERS_STATEMENT_MUST_NAME_THE_ACCOUNT_AND_THE_VENUE"
R_NOT_FUNDED = "AN_OWNER_AUTHORIZATION_IS_ONLY_FOR_A_FUNDED_CLASS_VENUE"
R_NO_BINDING = "NO_ACCOUNT_IS_BOUND_FOR_FUNDED_ACTIVATION"
R_BINDING_ACCOUNT = "THE_BOUND_ACCOUNT_IS_NOT_THE_ACCOUNT_BEING_AUTHORIZED"
R_BINDING_VENUE = "THE_BOUND_VENUE_IS_NOT_THE_VENUE_BEING_AUTHORIZED"
R_ACCOUNT_UNKNOWN = FA.R_ACCOUNT_UNKNOWN
R_LIMITS_MISSING = FA.R_LIMITS_MISSING
R_LIMITS_NOT_APPROVED = FA.R_LIMITS_NOT_APPROVED
R_DIGEST_MISSING = "THE_AUTHORIZATION_MUST_NAME_THE_EFFECTIVE_LIMIT_DIGEST"
R_DIGEST_NOT_CURRENT = \
    "THAT_DIGEST_IS_NOT_THE_CURRENTLY_APPROVED_EFFECTIVE_LIMITS"
R_LIFETIME = "THE_LIFETIME_MUST_BE_A_FINITE_POSITIVE_NUMBER_WITHIN_THE_BOUND"
R_CONFLICTING_OWNER_AUTHORIZATION = \
    "AN_ACTIVE_OWNER_AUTHORIZATION_COVERS_A_DIFFERENT_SCOPE_REVOKE_IT_FIRST"
R_RAISED = "THE_OWNER_AUTHORIZATION_WRITE_RAISED_AND_NOTHING_WAS_APPLIED"
R_REVOKE_CONFIRM = "CONFIRM_MUST_REPEAT_THE_AUTHORIZATION_ID"
R_REVOKE_REASON = "A_REVOCATION_STATES_WHY"
R_NO_SUCH_AUTHORIZATION = "NO_CURRENT_OWNER_AUTHORIZATION_CARRIES_THAT_ID"
R_REVOKE_RAISED = "THE_REVOCATION_RAISED_AND_NOTHING_WAS_APPLIED"
R_SCOPE_CHANGE_RAISED = "THE_SCOPE_CHANGE_RAISED_AND_NOTHING_WAS_APPLIED"

REFUSALS = (R_SCHEMA, R_BOTH_FACTORS, R_NO_OPERATOR,
            R_OPERATOR_NOT_AUTHENTICATED, R_CONFIRM, R_STATEMENT,
            R_STATEMENT_SCOPE, R_NOT_FUNDED, R_NO_BINDING, R_BINDING_ACCOUNT,
            R_BINDING_VENUE, R_ACCOUNT_UNKNOWN, R_LIMITS_MISSING,
            R_LIMITS_NOT_APPROVED, R_DIGEST_MISSING, R_DIGEST_NOT_CURRENT,
            R_LIFETIME, R_CONFLICTING_OWNER_AUTHORIZATION, R_RAISED,
            R_REVOKE_CONFIRM, R_REVOKE_REASON, R_NO_SUCH_AUTHORIZATION,
            R_REVOKE_RAISED, R_SCOPE_CHANGE_RAISED)

#: The non-secret fields of the owner record, as a reader is shown them.
#: Every field is non-secret by construction -- `authenticated_by` carries
#: booleans, the operator's NAME and the route -- and the list is explicit so
#: a field added later is not published by accident.
RECORD_FIELDS = ("authorization_id", "account_id", "venue", "venue_class",
                 "effective_digest", "scope_sha", "statement", "by", "at",
                 "expires_at", "lifetime_days", "revoked", "revoked_at",
                 "revoked_by", "revoked_reason", "invalidated",
                 "invalidated_at", "invalidated_reason", "authenticated_by",
                 "audit_id", "version")


# ═════════════════════════════════════════════════════════════════════
# 1 · PURE: THE SCOPE, THE RECORD'S STATE, THE CONSUMER'S CHECK
# ═════════════════════════════════════════════════════════════════════

def scope_sha(account_id: str, venue: str, effective_digest: str) -> str:
    """sha256(account_id | venue_lower | digest): ONE value binding all three.

    The effective digest alone binds neither account nor venue (an approval at
    or above every frozen rail produces the frozen digest for anyone), so the
    record carries this as well, and the consumer recomputes it.
    """
    raw = "%s|%s|%s" % (str(account_id or "").strip(),
                        str(venue or "").strip().lower(),
                        str(effective_digest or "").strip())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _finite_number(v) -> float | None:
    # `bool` is an int subclass: True would read as a 1970 timestamp.
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _truthy_flag(rec: dict, flag: str, at_field: str) -> bool:
    return bool(rec.get(flag)) or rec.get(at_field) not in (None, "")


def consumer_check(owner: dict | None, *, now: float) -> dict:
    """THE CHECKS `authorize()` ADDS AFTER ITS ACCOUNT/VENUE/LIMITS CHECKS.
    Pure. Returns {ok} or {ok: False, refusal, why}.

    ORDER, and why: a record someone TOOK AWAY is reported as revoked, one a
    scope change voided as invalidated -- those are facts about the record's
    history and need different actions (sign again vs. find out why it was
    withdrawn). Then a record that is not the writer's shape at all. Then
    expiry, which needs a readable `expires_at` to be asked.
    """
    rec = dict(owner or {})
    if _truthy_flag(rec, "revoked", "revoked_at"):
        return {"ok": False, "refusal": FA.R_OWNER_AUTH_REVOKED,
                "why": ("the owner's authorization %s was revoked by %s at "
                        "%s: %s" % (rec.get("authorization_id"),
                                    rec.get("revoked_by"),
                                    rec.get("revoked_at"),
                                    rec.get("revoked_reason")))}
    if _truthy_flag(rec, "invalidated", "invalidated_at"):
        return {"ok": False, "refusal": FA.R_OWNER_AUTH_INVALIDATED,
                "why": ("the owner's authorization %s was invalidated at %s "
                        "because its scope stopped being the scope in force "
                        "(%s). The owner signs again for the new scope"
                        % (rec.get("authorization_id"),
                           rec.get("invalidated_at"),
                           rec.get("invalidated_reason")))}
    missing = []
    if not str(rec.get("authorization_id") or "").strip():
        missing.append("authorization_id")
    auth = rec.get("authenticated_by")
    if not isinstance(auth, dict) or auth.get("admin_token_verified") \
            is not True or auth.get("resolution_key_verified") is not True \
            or not str(auth.get("operator") or "").strip():
        missing.append("authenticated_by")
    # A RECORD THE WRITER PRODUCED SAYS `revoked: false` AND
    # `invalidated: false` OUT LOUD. Their absence means it came from
    # somewhere else, and "not marked revoked" is not the same as "revocable
    # and not revoked".
    if rec.get("revoked") is not False:
        missing.append("revoked")
    if rec.get("invalidated") is not False:
        missing.append("invalidated")
    if str(rec.get("scope_sha") or "") != scope_sha(
            rec.get("account_id"), rec.get("venue"),
            rec.get("effective_digest")):
        missing.append("scope_sha")
    exp = _finite_number(rec.get("expires_at"))
    if exp is None:
        missing.append("expires_at")
    if missing:
        return {"ok": False, "refusal": FA.R_OWNER_AUTH_UNAUTHENTICATED,
                "malformed_fields": missing,
                "why": ("the owner record lacks or contradicts %s, so it "
                        "did not come through the authenticated writer "
                        "(POST /api/admin/funded-owner-authorization) and "
                        "is not read as the owner's authorization"
                        % ", ".join(missing))}
    if exp <= float(now):
        return {"ok": False, "refusal": FA.R_OWNER_AUTH_EXPIRED,
                "expires_at": exp,
                "why": ("the owner's authorization %s expired at %s. The "
                        "owner signs again" % (rec.get("authorization_id"),
                                               exp))}
    return {"ok": True}


def status_of(owner: dict | None, *, now: float) -> str:
    """ONE WORD for the record's state. Pure."""
    if not owner:
        return S_ABSENT
    got = consumer_check(owner, now=now)
    if got["ok"]:
        return S_ACTIVE
    return {FA.R_OWNER_AUTH_REVOKED: S_REVOKED,
            FA.R_OWNER_AUTH_INVALIDATED: S_INVALIDATED,
            FA.R_OWNER_AUTH_EXPIRED: S_EXPIRED}.get(got["refusal"],
                                                     S_MALFORMED)


def scope_differs(owner: dict | None, scope: dict) -> list:
    """Which of account/venue/digest the record names differently from the
    scope in force. Pure. An unapproved limit set has NO approved digest, so
    every record's digest differs from it."""
    rec = dict(owner or {})
    bad = []
    if str(rec.get("account_id") or "") != str(scope.get("account_id") or ""):
        bad.append("account_id")
    if str(rec.get("venue") or "").strip().upper() != \
            str(scope.get("venue") or "").strip().upper():
        bad.append("venue")
    if not scope.get("effective_digest") or \
            str(rec.get("effective_digest") or "").strip() != \
            str(scope.get("effective_digest")):
        bad.append("effective_digest")
    return bad


def _lifetime_days(raw) -> float | None:
    if raw is None or raw == "":
        return DEFAULT_LIFETIME_DAYS
    f = _finite_number(raw)
    if f is None or f <= 0 or f > MAX_LIFETIME_DAYS:
        return None
    return f


def _auth_row(auth: dict | None) -> dict:
    return {k: (auth or {}).get(k) for k in AUTH_FIELDS}


def _public(rec: dict | None) -> dict | None:
    if not rec:
        return None
    return {k: rec.get(k) for k in RECORD_FIELDS if k in rec}


# ═════════════════════════════════════════════════════════════════════
# 2 · READS
# ═════════════════════════════════════════════════════════════════════

async def has_schema(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regclass($1) IS NOT NULL", AUDIT_TABLE))


async def _read(conn, key):
    return FA._obj(await FA._state(conn, key))


async def _write(conn, key, value) -> None:
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        key, json.dumps(value, default=str))


async def current_scope(conn) -> dict:
    """WHAT AN OWNER WOULD BE SIGNING, read from the records in force.

    The digest is computed EXACTLY as `authorize()` computes it --
    `EX.effective_limits(normalise_limit_keys(stored["proposed"]))` -- and is
    reported only when the limit set is approved: an unapproved proposal has
    no approved digest to sign.
    """
    from . import bettor_entry_execution as EX

    binding = await _read(conn, FA.ACCOUNT_KEY) or {}
    stored = await _read(conn, FA.LIMITS_KEY) or {}
    proposed = FA.normalise_limit_keys(dict(stored.get("proposed") or {}))
    missing = [k for k in FA.REQUIRED_LIMITS if proposed.get(k) in (None, "")]
    approved = bool(stored.get("approved"))
    eff = EX.effective_limits(proposed) if proposed and not missing else None
    digest = eff["effective_digest"] if (eff and approved) else None
    acct = str(binding.get("account_id") or "").strip() or None
    venue = str(binding.get("venue") or "").strip() or None
    return {
        "account_id": acct, "venue": venue,
        "venue_class": FA.venue_class(venue) if venue else None,
        "bound": bool(acct and venue),
        "limits_recorded": bool(proposed) and not missing,
        "limits_missing": missing,
        "limits_approved": approved,
        "approved_by": stored.get("approved_by") if approved else None,
        "approved_at": stored.get("approved_at") if approved else None,
        "approved_limits": proposed if approved else None,
        "effective_limits": eff["effective"] if (eff and approved) else None,
        "effective_digest": digest,
        "scope_sha": (scope_sha(acct, venue, digest)
                      if (acct and venue and digest) else None),
    }


async def accepted_row(conn, owner: dict) -> dict:
    """IS THERE AN ACCEPTED RECORD ROW FOR THIS EXACT RECORD? The consumer's
    proof that the record came through the authenticated writer. A read
    failure is an answer of NO, never a pass."""
    try:
        row = await conn.fetchrow(
            "SELECT audit_id, operator, "
            "       (effect->'record'->>'expires_at')::float8 AS expires_at "
            "  FROM %s "
            " WHERE action = 'RECORD' AND outcome = 'ACCEPTED' "
            "   AND authorization_id = $1 AND scope_sha = $2 "
            " ORDER BY audit_id DESC LIMIT 1" % AUDIT_TABLE,
            str(owner.get("authorization_id") or ""),
            str(owner.get("scope_sha") or ""))
    except Exception as exc:                                # noqa: BLE001
        return {"ok": False, "unreadable": type(exc).__name__,
                "why": ("the owner-authorization audit could not be read, so "
                        "nothing establishes that this record came through "
                        "the authenticated writer")}
    if row is None:
        return {"ok": False,
                "why": ("no ACCEPTED audit row carries authorization %s with "
                        "this scope, so the record did not come through the "
                        "authenticated writer" % owner.get("authorization_id"))}
    # THE EXPIRY IS THE ONE THE OWNER SIGNED. The audit row records it; a
    # record re-dated later than that in ingestion_state is not the record
    # the writer produced.
    exp = _finite_number(owner.get("expires_at"))
    if row["expires_at"] is None or exp is None \
            or exp > float(row["expires_at"]) + 1e-6:
        return {"ok": False, "audit_id": int(row["audit_id"]),
                "why": ("the record's expiry %s is not the expiry its "
                        "ACCEPTED audit row recorded (%s), so it was altered "
                        "after it was written" % (exp, row["expires_at"]))}
    return {"ok": True, "audit_id": int(row["audit_id"]),
            "operator": row["operator"]}


async def audit_tail(conn, limit: int = 20) -> list:
    rows = await conn.fetch(
        "SELECT audit_id, extract(epoch FROM at)::float8 AS at, action, "
        "       outcome, refusal, authorization_id, account_id, venue, "
        "       effective_digest, scope_sha, operator, statement, reason, "
        "       authenticated_by, effect "
        "  FROM %s ORDER BY audit_id DESC LIMIT $1" % AUDIT_TABLE,
        int(limit))
    out = []
    for r in rows:
        d = dict(r)
        for k in ("authenticated_by", "effect"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        out.append(d)
    return out


async def status(conn, *, now: float | None = None) -> dict:
    """THE READ BEHIND `GET /api/admin/funded-owner-authorization`.

    It shows the owner EXACTLY what they would bind to before they sign --
    account, venue, the approved limits and their effective digest, and the
    scope_sha of the three -- and, if a record exists, its non-secret fields
    and whether it is valid against the binding and limits in force now.
    """
    at = float(now if now is not None else time.time())
    scope = await current_scope(conn)
    rec = await _read(conn, OWNER_AUTH_KEY)
    st = status_of(rec, now=at)
    differs = scope_differs(rec, scope) if rec else []
    chk = consumer_check(rec, now=at) if rec else None
    schema = await has_schema(conn)
    proof = (await accepted_row(conn, rec)
             if (rec and schema and chk and chk.get("ok")) else None)
    why_not = []
    if not scope["bound"]:
        why_not.append(R_NO_BINDING)
    elif scope["venue_class"] != FA.VENUE_FUNDED:
        why_not.append(R_NOT_FUNDED)
    if not scope["limits_recorded"]:
        why_not.append(R_LIMITS_MISSING)
    elif not scope["limits_approved"]:
        why_not.append(R_LIMITS_NOT_APPROVED)
    if not schema:
        why_not.append(R_SCHEMA)
    if st == S_ACTIVE and not differs:
        why_not.append("AN_ACTIVE_AUTHORIZATION_ALREADY_COVERS_THIS_SCOPE")
    elif st == S_ACTIVE:
        why_not.append(R_CONFLICTING_OWNER_AUTHORIZATION)
    return {
        "version": VERSION, "at": at,
        "schema_present": schema,
        "to_sign": {
            "account_id": scope["account_id"],
            "venue": scope["venue"],
            "venue_class": scope["venue_class"],
            "effective_digest": scope["effective_digest"],
            "scope_sha": scope["scope_sha"],
            "approved_limits": scope["approved_limits"],
            "effective_limits": scope["effective_limits"],
            "limits_approved_by": scope["approved_by"],
            "limits_approved_at": scope["approved_at"],
            "confirm_must_be": scope["account_id"],
            "statement_must_name": [x for x in (scope["account_id"],
                                                scope["venue"]) if x],
            "statement_min_chars": STATEMENT_MIN_CHARS,
            "lifetime_days": {"default": DEFAULT_LIFETIME_DAYS,
                              "max": MAX_LIFETIME_DAYS,
                              "field": "lifetime_days (optional)"},
            "ready_to_sign": not why_not,
            "why_not": why_not,
            "route": "POST /api/admin/funded-owner-authorization",
            "factors": ("X-Admin-Token AND X-Resolution-Key; the operator "
                        "is the server's FUNDED_RESOLUTION_OPERATOR"),
        },
        "record": _public(rec),
        "record_status": st,
        "valid_against_current_scope": bool(
            rec and st == S_ACTIVE and not differs
            and (proof or {}).get("ok")),
        "differs_from_current_scope_on": differs,
        "consumer_check": chk,
        "accepted_audit_row": proof,
        "audit_tail": await audit_tail(conn) if schema else [],
        "authorises_capital": False,
        "funded_submission": "DISABLED",
    }


# ═════════════════════════════════════════════════════════════════════
# 3 · THE AUDIT
# ═════════════════════════════════════════════════════════════════════

async def _next_audit_id(conn) -> int:
    """Reserved BEFORE the effects, so the record can name the audit row that
    accepted it, and the row -- written last -- records the effects."""
    return int(await conn.fetchval(
        "SELECT nextval(pg_get_serial_sequence($1, 'audit_id'))",
        AUDIT_TABLE))


async def _audit(conn, *, action, outcome, refusal=None, authorization_id=None,
                 account_id=None, venue=None, effective_digest=None,
                 scope=None, operator=None, statement=None, reason=None,
                 auth=None, effect=None, audit_id: int | None = None) -> int:
    if audit_id is None:
        audit_id = await _next_audit_id(conn)
    return int(await conn.fetchval(
        "INSERT INTO %s (audit_id, action, outcome, refusal, "
        " authorization_id, account_id, venue, effective_digest, scope_sha, "
        " operator, statement, reason, authenticated_by, effect) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14::jsonb)"
        " RETURNING audit_id" % AUDIT_TABLE,
        int(audit_id), action, outcome, refusal,
        (str(authorization_id)[:200] if authorization_id else None),
        (str(account_id)[:200] if account_id else None),
        (str(venue)[:60] if venue else None),
        (str(effective_digest)[:128] if effective_digest else None),
        scope, (str(operator)[:200] if operator else None),
        (str(statement)[:2000] if statement else None),
        (str(reason)[:2000] if reason else None),
        json.dumps(_auth_row(auth), default=str),
        json.dumps(effect or {}, default=str)))


def _factors_ok(auth: dict | None) -> bool:
    a = auth or {}
    return (a.get("admin_token_verified") is True
            and a.get("resolution_key_verified") is True
            and bool(str(a.get("operator") or "").strip()))


async def _revoke_system_authorization(conn, *, authorization_id, at, by,
                                       reason, audit_id) -> dict:
    """REVOKE THE SYSTEM AUTHORIZATION ISSUED ON THIS OWNER RECORD, so the
    execution gate refuses at once rather than when its 24 h run out.

    Issued on it means it names this authorization_id. A FUNDED-class system
    authorization that names NO owner authorization predates the writer and
    cannot be shown to rest on some other record, so it is revoked too --
    this only ever removes authority.
    """
    sysrec = await _read(conn, AUTHORIZATION_KEY)
    if not sysrec:
        return {"revoked": False, "why": "no system authorization exists"}
    if sysrec.get("revoked") or sysrec.get("revoked_at"):
        return {"revoked": False, "already_revoked": True,
                "revoked_at": sysrec.get("revoked_at")}
    issued_on = sysrec.get("owner_authorization_id")
    funded = str(sysrec.get("venue_class") or "") == FA.VENUE_FUNDED \
        or FA.venue_class(sysrec.get("venue")) == FA.VENUE_FUNDED
    if issued_on != authorization_id and not (funded and not issued_on):
        return {"revoked": False, "issued_on": issued_on,
                "why": "the system authorization rests on another record"}
    sysrec.update(revoked=True, revoked_at=at, revoked_by=by,
                  revoked_reason=reason,
                  revoked_with_owner_authorization=authorization_id,
                  revoke_audit_id=audit_id)
    await _write(conn, AUTHORIZATION_KEY, sysrec)
    return {"revoked": True, "issued_on": issued_on,
            "account_id": sysrec.get("account_id"),
            "venue": sysrec.get("venue")}


# ═════════════════════════════════════════════════════════════════════
# 4 · RECORD
# ═════════════════════════════════════════════════════════════════════

def _validate_record_request(*, account_id, venue, statement, confirm,
                             operator, auth, lifetime_days) -> str | None:
    if not _factors_ok(auth):
        return R_BOTH_FACTORS
    if not str(operator or "").strip():
        return R_NO_OPERATOR
    # THE NAME ON THE RECORD IS THE ONE THE KEY AUTHENTICATES.
    if str(operator).strip().casefold() != \
            str(auth.get("operator") or "").strip().casefold():
        return R_OPERATOR_NOT_AUTHENTICATED
    if not account_id or str(confirm or "").strip() != account_id:
        return R_CONFIRM
    if len(statement) < STATEMENT_MIN_CHARS:
        return R_STATEMENT
    # THE OWNER STATES WHAT THEY AUTHORISE, in their own words: the account
    # and the venue must both appear in the statement, so a generic "I
    # approve" cannot be replayed onto another scope.
    low = statement.casefold()
    if account_id.casefold() not in low or not venue \
            or venue.casefold() not in low:
        return R_STATEMENT_SCOPE
    if FA.venue_class(venue) != FA.VENUE_FUNDED:
        return R_NOT_FUNDED
    if _lifetime_days(lifetime_days) is None:
        return R_LIFETIME
    return None


async def _record(conn, *, account_id, venue, effective_digest, statement,
                  confirm, operator, auth, lifetime_days, at, trace) -> dict:
    out: dict[str, Any] = {"version": VERSION, "action": A_RECORD, "at": at,
                           "requested": {"account_id": account_id,
                                         "venue": venue,
                                         "effective_digest": effective_digest},
                           "authorises_capital": False,
                           "funded_submission": "DISABLED"}
    refusal = _validate_record_request(
        account_id=account_id, venue=venue, statement=statement,
        confirm=confirm, operator=operator, auth=auth,
        lifetime_days=lifetime_days)
    sha = scope_sha(account_id, venue, effective_digest) \
        if (account_id and venue and effective_digest) else None
    audit_kw = dict(account_id=account_id, venue=venue,
                    effective_digest=effective_digest, scope=sha,
                    operator=str(operator or "").strip() or None,
                    statement=statement or None, auth=auth)
    detail: dict[str, Any] = {}
    trace["detail"] = detail
    async with conn.transaction():
        await conn.execute(LOCK_SQL, OWNER_AUTH_KEY)
        if refusal is None:
            scope = await current_scope(conn)
            detail["scope_in_force"] = {k: scope.get(k) for k in
                                        ("account_id", "venue",
                                         "effective_digest", "scope_sha",
                                         "limits_approved")}
            if not scope["bound"]:
                refusal = R_NO_BINDING
            elif scope["account_id"] != account_id:
                refusal = R_BINDING_ACCOUNT
            elif str(scope["venue"]).upper() != venue.upper():
                refusal = R_BINDING_VENUE
            elif not scope["limits_recorded"]:
                refusal = R_LIMITS_MISSING
            elif not scope["limits_approved"]:
                refusal = R_LIMITS_NOT_APPROVED
            elif not effective_digest:
                refusal = R_DIGEST_MISSING
            elif effective_digest != scope["effective_digest"]:
                refusal = R_DIGEST_NOT_CURRENT
            elif await conn.fetchval(FA.ACCOUNT_SQL, account_id) is None:
                refusal = R_ACCOUNT_UNKNOWN
        existing = await _read(conn, OWNER_AUTH_KEY)
        st = status_of(existing, now=at)
        if refusal is None and st == S_ACTIVE:
            same = (str(existing.get("account_id")) == account_id
                    and str(existing.get("venue") or "").upper()
                    == venue.upper()
                    and str(existing.get("effective_digest")) ==
                    effective_digest
                    and str(existing.get("statement") or "").strip()
                    == statement)
            if same:
                # THE SAME SIGNATURE RETRIED: the record stands as written
                # (its expiry included) and the retry is audited as such.
                aid = await _audit(
                    conn, action=A_RECORD, outcome=O_ALREADY_RECORDED,
                    authorization_id=existing.get("authorization_id"),
                    effect={"existing_audit_id": existing.get("audit_id")},
                    **audit_kw)
                return dict(out, ok=True, already=True, audit_id=aid,
                            record=_public(existing))
            refusal = R_CONFLICTING_OWNER_AUTHORIZATION
            detail["active_authorization"] = {
                k: existing.get(k) for k in ("authorization_id",
                                             "account_id", "venue",
                                             "effective_digest",
                                             "expires_at")}
        if refusal is not None:
            aid = await _audit(conn, action=A_RECORD, outcome=O_REFUSED,
                               refusal=refusal, effect=detail, **audit_kw)
            return dict(out, ok=False, refusal=refusal, audit_id=aid,
                        applied=None, detail=detail)
        # ── ACCEPTED: the audit id first, the record, the audit row last ──
        aid = await _next_audit_id(conn)
        days = _lifetime_days(lifetime_days)
        who = str(operator).strip()
        record = {
            "version": VERSION,
            "authorization_id": "foa-" + uuid.uuid4().hex,
            # the consumer's fields
            "account_id": account_id, "venue": venue,
            "venue_class": FA.VENUE_FUNDED,
            "effective_digest": effective_digest,
            "by": who, "at": at, "statement": statement,
            # what binds and bounds it
            "scope_sha": sha,
            "lifetime_days": days,
            "expires_at": at + days * DAY_S,
            "revoked": False, "invalidated": False,
            "authenticated_by": _auth_row(auth),
            "audit_id": aid,
            "authorises": ("the FUNDED activation step for this account at "
                           "this venue under exactly these effective limits"),
            "does_not_authorise": ["real order submission (off in code)",
                                   "raising any frozen rail",
                                   "any other account, venue or limit set"],
        }
        await _write(conn, OWNER_AUTH_KEY, record)
        superseded = ({"authorization_id": existing.get("authorization_id"),
                       "status": st} if existing else None)
        await _audit(conn, action=A_RECORD, outcome=O_ACCEPTED,
                     authorization_id=record["authorization_id"],
                     effect={"record": _public(record),
                             "superseded": superseded},
                     audit_id=aid, **audit_kw)
    return dict(out, ok=True, audit_id=aid, record=_public(record),
                superseded=superseded, applied={"recorded_under":
                                                OWNER_AUTH_KEY})


async def record_owner_authorization(conn, *, account_id, venue,
                                     effective_digest, statement, confirm,
                                     operator, auth: dict | None = None,
                                     lifetime_days=None,
                                     now: float | None = None) -> dict:
    """RECORD THE OWNER'S AUTHORIZATION, or refuse by name. Always audited.

    `auth` is the route's statement of how the caller authenticated
    (AUTH_FIELDS); both factors must be verified and `operator` must be the
    identity the resolution key authenticates. Never raises: a raise inside
    rolls back every effect and is audited outside the transaction.
    """
    at = float(now if now is not None else time.time())
    acct = str(account_id or "").strip()
    ven = str(venue or "").strip()
    digest = str(effective_digest or "").strip()
    stmt = str(statement or "").strip()[:2000]
    if not await has_schema(conn):
        return {"version": VERSION, "action": A_RECORD, "ok": False,
                "refusal": R_SCHEMA, "applied": None,
                "why": "migration 146 is not applied; nothing was written"}
    trace: dict = {}
    try:
        return await _record(conn, account_id=acct, venue=ven,
                             effective_digest=digest, statement=stmt,
                             confirm=confirm, operator=operator, auth=auth,
                             lifetime_days=lifetime_days, at=at, trace=trace)
    except Exception as exc:                                # noqa: BLE001
        err = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        aid = await _audit(
            conn, action=A_RECORD, outcome=O_REFUSED, refusal=R_RAISED,
            account_id=acct or None, venue=ven or None,
            effective_digest=digest or None,
            operator=str(operator or "").strip() or None,
            statement=stmt or None, auth=auth,
            effect={"rolled_back": True, "error": err,
                    "detail": trace.get("detail") or {}})
        return {"version": VERSION, "action": A_RECORD, "ok": False,
                "refusal": R_RAISED, "audit_id": aid, "applied": None,
                "rolled_back": True, "error": err}


# ═════════════════════════════════════════════════════════════════════
# 5 · REVOKE
# ═════════════════════════════════════════════════════════════════════

async def _revoke(conn, *, authorization_id, confirm, reason, operator, auth,
                  at) -> dict:
    out: dict[str, Any] = {"version": VERSION, "action": A_REVOKE, "at": at,
                           "requested": {"authorization_id":
                                         authorization_id}}
    refusal = None
    if not _factors_ok(auth):
        refusal = R_BOTH_FACTORS
    elif not str(operator or "").strip():
        refusal = R_NO_OPERATOR
    elif str(operator).strip().casefold() != \
            str(auth.get("operator") or "").strip().casefold():
        refusal = R_OPERATOR_NOT_AUTHENTICATED
    elif not authorization_id or \
            str(confirm or "").strip() != authorization_id:
        refusal = R_REVOKE_CONFIRM
    elif not reason:
        refusal = R_REVOKE_REASON
    who = str(operator or "").strip() or None
    async with conn.transaction():
        await conn.execute(LOCK_SQL, OWNER_AUTH_KEY)
        rec = await _read(conn, OWNER_AUTH_KEY)
        if refusal is None and (not rec or str(rec.get("authorization_id")
                                               or "") != authorization_id):
            refusal = R_NO_SUCH_AUTHORIZATION
        kw = dict(authorization_id=authorization_id or None,
                  account_id=(rec or {}).get("account_id"),
                  venue=(rec or {}).get("venue"),
                  effective_digest=(rec or {}).get("effective_digest"),
                  scope=(rec or {}).get("scope_sha"), operator=who,
                  reason=reason or None, auth=auth)
        if refusal is not None:
            aid = await _audit(conn, action=A_REVOKE, outcome=O_REFUSED,
                               refusal=refusal, **kw)
            return dict(out, ok=False, refusal=refusal, audit_id=aid,
                        applied=None)
        aid = await _next_audit_id(conn)
        # THE SYSTEM AUTHORIZATION GOES WITH IT, on every path -- including a
        # retry of a revocation already made, in case the first left it.
        sysrev = await _revoke_system_authorization(
            conn, authorization_id=authorization_id, at=at, by=who,
            reason=reason, audit_id=aid)
        if rec.get("revoked") or rec.get("revoked_at"):
            await _audit(conn, action=A_REVOKE, outcome=O_ALREADY_REVOKED,
                         effect={"revoked_at": rec.get("revoked_at"),
                                 "system_authorization": sysrev},
                         audit_id=aid, **kw)
            return dict(out, ok=True, already=True, audit_id=aid,
                        record=_public(rec), system_authorization=sysrev)
        prior = status_of(rec, now=at)
        rec.update(revoked=True, revoked_at=at, revoked_by=who,
                   revoked_reason=reason, revoke_audit_id=aid)
        await _write(conn, OWNER_AUTH_KEY, rec)
        await _audit(conn, action=A_REVOKE, outcome=O_ACCEPTED,
                     effect={"status_before": prior,
                             "system_authorization": sysrev},
                     audit_id=aid, **kw)
    return dict(out, ok=True, audit_id=aid, record=_public(rec),
                system_authorization=sysrev,
                applied={"owner_authorization_revoked": True,
                         "system_authorization_revoked":
                             sysrev.get("revoked")})


async def revoke_owner_authorization(conn, *, authorization_id, confirm,
                                     reason, operator,
                                     auth: dict | None = None,
                                     now: float | None = None) -> dict:
    """WITHDRAW THE OWNER'S AUTHORIZATION and the system authorization issued
    on it. Audited on every path; never raises."""
    at = float(now if now is not None else time.time())
    aid_s = str(authorization_id or "").strip()
    why = str(reason or "").strip()[:2000]
    if not await has_schema(conn):
        return {"version": VERSION, "action": A_REVOKE, "ok": False,
                "refusal": R_SCHEMA, "applied": None}
    try:
        return await _revoke(conn, authorization_id=aid_s, confirm=confirm,
                             reason=why, operator=operator, auth=auth, at=at)
    except Exception as exc:                                # noqa: BLE001
        err = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        aid = await _audit(conn, action=A_REVOKE, outcome=O_REFUSED,
                           refusal=R_REVOKE_RAISED,
                           authorization_id=aid_s or None,
                           operator=str(operator or "").strip() or None,
                           reason=why or None, auth=auth,
                           effect={"rolled_back": True, "error": err})
        return {"version": VERSION, "action": A_REVOKE, "ok": False,
                "refusal": R_REVOKE_RAISED, "audit_id": aid,
                "applied": None, "rolled_back": True, "error": err}


# ═════════════════════════════════════════════════════════════════════
# 6 · INVALIDATE WHEN THE SCOPE CHANGES
# ═════════════════════════════════════════════════════════════════════

#: The scope-change reasons, as the callers name them.
SCOPE_ACCOUNT_BINDING = "ACCOUNT_BINDING_CHANGED"
SCOPE_LIMITS_PROPOSED = "LIMITS_PROPOSED_APPROVAL_RESET"
SCOPE_LIMITS_APPROVED = "LIMITS_APPROVED"


async def invalidate_owner_authorization_if_scope_changed(
        conn, *, reason: str, by: str | None = None,
        now: float | None = None) -> dict:
    """VOID THE OWNER RECORD IF IT NO LONGER DESCRIBES THE SCOPE IN FORCE.

    Idempotent: a record already revoked or invalidated, or one whose
    account, venue and digest still equal the binding and the approved
    digest, is left exactly as it is and nothing is audited (nothing was
    attempted on it). An invalidation is audited, and revokes the system
    authorization issued on the record.

    RAISES on failure, deliberately: callers run it in the same transaction
    as the scope write (`apply_scope_change`), so a failure here means the
    scope did not change either -- never a moved scope with a signature that
    still looks current.
    """
    at = float(now if now is not None else time.time())
    async with conn.transaction():
        await conn.execute(LOCK_SQL, OWNER_AUTH_KEY)
        rec = await _read(conn, OWNER_AUTH_KEY)
        if not rec:
            return {"ok": True, "invalidated": False, "why": "no record"}
        if _truthy_flag(rec, "revoked", "revoked_at") or \
                _truthy_flag(rec, "invalidated", "invalidated_at"):
            return {"ok": True, "invalidated": False,
                    "authorization_id": rec.get("authorization_id"),
                    "why": "already revoked or invalidated"}
        scope = await current_scope(conn)
        differs = scope_differs(rec, scope)
        if not differs:
            return {"ok": True, "invalidated": False,
                    "authorization_id": rec.get("authorization_id"),
                    "why": "the scope in force is the scope signed"}
        aid = await _next_audit_id(conn)
        prior = status_of(rec, now=at)
        rec.update(invalidated=True, invalidated_at=at,
                   invalidated_reason=reason,
                   invalidated_differs_on=differs,
                   invalidate_audit_id=aid)
        sysrev = await _revoke_system_authorization(
            conn, authorization_id=rec.get("authorization_id"), at=at,
            by=by or "SCOPE_CHANGE", reason=reason, audit_id=aid)
        await _write(conn, OWNER_AUTH_KEY, rec)
        await _audit(
            conn, action=A_INVALIDATE, outcome=O_ACCEPTED,
            authorization_id=rec.get("authorization_id"),
            account_id=rec.get("account_id"), venue=rec.get("venue"),
            effective_digest=rec.get("effective_digest"),
            scope=rec.get("scope_sha"), operator=by, reason=reason,
            effect={"status_before": prior, "differs_on": differs,
                    "scope_now": {k: scope.get(k) for k in
                                  ("account_id", "venue", "effective_digest",
                                   "scope_sha")},
                    "system_authorization": sysrev},
            audit_id=aid)
    return {"ok": True, "invalidated": True, "audit_id": aid,
            "authorization_id": rec.get("authorization_id"),
            "differs_on": differs, "system_authorization": sysrev}


async def apply_scope_change(conn, *, reason: str, write, by=None) -> dict:
    """RUN A SCOPE WRITE AND THE INVALIDATION IT OWES IN ONE TRANSACTION.

    `write` is an async callable performing the binding/limits write. If it or
    the invalidation raises, both roll back -- the scope did not move -- and
    the failure is audited outside the transaction and returned as a named
    refusal. It is not swallowed: the caller reports `ok: False` with nothing
    applied. Without the audit table the failure cannot be recorded, so it is
    re-raised rather than hidden.
    """
    try:
        async with conn.transaction():
            applied = await write()
            inv = await invalidate_owner_authorization_if_scope_changed(
                conn, reason=reason, by=by)
        return {"ok": True, "applied": applied, "owner_authorization": inv}
    except Exception as exc:                                # noqa: BLE001
        if not await has_schema(conn):
            raise
        err = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        aid = await _audit(conn, action=A_INVALIDATE, outcome=O_REFUSED,
                           refusal=R_SCOPE_CHANGE_RAISED, operator=by,
                           reason=reason,
                           effect={"rolled_back": True, "error": err})
        return {"ok": False, "refusal": R_SCOPE_CHANGE_RAISED,
                "audit_id": aid, "error": err, "rolled_back": True}


def describe() -> dict:
    return {
        "version": VERSION,
        "record_key": OWNER_AUTH_KEY,
        "audit_table": AUDIT_TABLE,
        "binds": "account_id, venue and the approved effective-limit digest "
                 "(scope_sha = sha256(account_id|venue_lower|digest))",
        "lifetime_days": {"default": DEFAULT_LIFETIME_DAYS,
                          "max": MAX_LIFETIME_DAYS},
        "invalidated_by": [SCOPE_ACCOUNT_BINDING, SCOPE_LIMITS_PROPOSED,
                           SCOPE_LIMITS_APPROVED],
        "refusals": list(REFUSALS),
        "factors": list(AUTH_FIELDS),
        "authorises_capital": False,
    }
