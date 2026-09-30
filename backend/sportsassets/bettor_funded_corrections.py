"""BOOKING A SETTLEMENT CORRECTION THE VENUE HAS MADE: ONE AUDITED DECISION,
ONE DELTA, NEVER A SECOND SETTLEMENT.

THE GAP (account map §3). A funded leg is closed on the venue's settlement as
read at the time (`bettor_funded_management.reconcile_settlement`) and
re-read for seven days afterwards (migration 141). A re-read that DISAGREES
refuses the account new exposure while it is the leg's newest established
one, and removes the leg's group from the pairing model's labels. Both are
right. But nothing could BOOK the correction: once the leg left the re-read
window the disagreement stood for ever, and the account was refused new
exposure permanently on a question nobody was able to close.

WHAT THIS DOES, AND IN WHAT ORDER (one transaction):

  1  reserve the audit id;
  2  check the request: the re-read named is the NEWEST ESTABLISHED one for
     that leg, its verdict is DISAGREES, it was compared with the reading
     that is booked NOW, `confirm` repeats the intent id, `seen_recheck_sha`
     is the sha of that re-read (the operator decided on THAT read, not a
     later one), the statement says what was checked, and the booked payout
     agrees with its own economics rows;
  3  compute the corrected payout by the close's own rules -- the side-aware
     payout of the residual at the venue's corrected price, or the remaining
     basis for a void -- and book the DELTA as one economics event of kind
     SETTLEMENT_CORRECTION with the deterministic id
     `fev:<intent>:SETTLEMENT_CORRECTION:<recheck_id>`;
  4  record the correction row (migration 145, append-only);
  5  make the corrected reading the BOOKED one on the intent, keeping every
     earlier reading in `settlement.history` (the column is not frozen by a
     trigger -- checked), so the next re-read compares against it and a
     LATER re-read that disagrees with the correction contests again;
  6  record a new outcome version for every decision on the leg's group (the
     learning ledger keeps versioned outcomes, migration 134);
  7  write the audit row LAST, naming what the effects actually were.

A refusal is audited too, and a raise rolls everything back and is audited
outside the transaction. The same request retried returns the existing
correction; a different request for an already-corrected re-read is refused.

WHAT IT NEVER DOES. It never rewrites the original SETTLEMENT economics row
(the record of what was booked when), never deletes a re-read, never touches
authorization, limits or the account row, and submits nothing to any venue.
AUTHENTICATION IS THE ROUTE'S: this module receives the already-verified
description of how the caller authenticated and records WHICH factors were
verified -- never the factors themselves. The operator is the identity the
resolution key authenticates, from configuration, never from the request.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from . import bettor_funded_book as FB

VERSION = "FUNDED_SETTLEMENT_CORRECTION_V1"
KIND = "SETTLEMENT_CORRECTION"
AUTHORITATIVE = ("REPORTED_SETTLEMENT", "EXPLICIT_VOID")
SETTLED_REASONS = ("SETTLED_BY_THE_VENUE", "VOIDED_BY_THE_VENUE")
MIN_STATEMENT = 20
#: Dollar amounts are booked to the cent by the close; this absorbs float
#: noise in comparing them, never a real difference.
USD_TOLERANCE = 1e-6

#: The authentication description the route must pass. Values say WHICH
#: factors were presented and verified -- never the factors.
AUTH_FIELDS = ("admin_token_verified", "resolution_key_verified",
               "operator", "route")

R_SCHEMA = "THE_SETTLEMENT_CORRECTION_SCHEMA_IS_NOT_IN_THIS_DATABASE"
R_BOTH_FACTORS = "BOTH_FACTORS_ARE_REQUIRED"
R_OPERATOR = "A_CORRECTION_NAMES_THE_AUTHENTICATED_OPERATOR"
R_OPERATOR_NOT_AUTHENTICATED = \
    "THE_OPERATOR_IS_NOT_THE_IDENTITY_THE_RESOLUTION_KEY_AUTHENTICATES"
R_CONFIRM = "CONFIRM_MUST_REPEAT_THE_INTENT_ID"
R_STATEMENT = "A_CORRECTION_STATES_WHAT_WAS_CHECKED"
R_RECHECK_ID = "A_CORRECTION_NAMES_THE_RE_READ_IT_ANSWERS"
R_SEEN_SHA = "THE_CORRECTION_DOES_NOT_CITE_THE_RE_READ_IT_WAS_MADE_ON"
R_NO_SUCH_INTENT = "NO_SUCH_FUNDED_ENTRY_LEG"
R_NOT_SETTLED = "THAT_LEG_WAS_NOT_CLOSED_ON_AN_AUTHORITATIVE_VENUE_SETTLEMENT"
R_NO_SUCH_RECHECK = "NO_SUCH_RE_READ_OF_THIS_LEG"
R_RECHECK_NOT_ESTABLISHED = "THAT_RE_READ_ESTABLISHED_NOTHING"
R_RECHECK_AGREES = "THAT_RE_READ_AGREES_WITH_WHAT_WAS_BOOKED"
R_NOT_NEWEST = "A_NEWER_ESTABLISHED_RE_READ_OF_THIS_LEG_EXISTS"
R_STALE_SHA = "THE_RE_READ_IS_NOT_THE_ONE_THE_OPERATOR_SAW"
R_NOT_AGAINST_BOOKED = \
    "THAT_RE_READ_WAS_COMPARED_WITH_A_READING_THAT_IS_NO_LONGER_BOOKED"
R_BOOK_INCONSISTENT = "THE_BOOKED_PAYOUT_AND_ITS_ECONOMICS_DISAGREE"
R_NOT_PRICEABLE = "THE_CORRECTED_PAYOUT_CANNOT_BE_COMPUTED"
R_CORRECTED_DIFFERENTLY = \
    "THAT_RE_READ_WAS_ALREADY_ANSWERED_BY_A_DIFFERENT_CORRECTION"
R_CHANGED_UNDER_US = "THE_LEG_CHANGED_WHILE_THE_CORRECTION_WAS_BEING_BOOKED"
R_RAISED = "THE_CORRECTION_RAISED_AND_NOTHING_WAS_APPLIED"

REFUSALS = (R_SCHEMA, R_BOTH_FACTORS, R_OPERATOR,
            R_OPERATOR_NOT_AUTHENTICATED, R_CONFIRM, R_STATEMENT,
            R_RECHECK_ID, R_SEEN_SHA, R_NO_SUCH_INTENT, R_NOT_SETTLED,
            R_NO_SUCH_RECHECK, R_RECHECK_NOT_ESTABLISHED, R_RECHECK_AGREES,
            R_NOT_NEWEST, R_STALE_SHA, R_NOT_AGAINST_BOOKED,
            R_BOOK_INCONSISTENT, R_NOT_PRICEABLE, R_CORRECTED_DIFFERENTLY,
            R_CHANGED_UNDER_US, R_RAISED)

ATTESTATION_TEXT = (
    "I have checked the venue's corrected settlement of this market against "
    "the re-read I cite, and I am booking the difference between the "
    "corrected payout and the payout that was booked. The original "
    "settlement stays on the record.")


def correction_id_for(intent_id: str, recheck_id: int) -> str:
    return "fsc:%s:%d" % (intent_id, int(recheck_id))


def event_id_for(intent_id: str, recheck_id: int) -> str:
    return "fev:%s:%s:%d" % (intent_id, KIND, int(recheck_id))


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _epoch(v) -> float | None:
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    return _num(v)


def recheck_sha(row) -> str:
    """THE IDENTITY OF ONE RE-READ AS AN OPERATOR SAW IT. Pure.

    Over every field the decision rests on, canonically formatted so the same
    row always hashes the same whether it came from asyncpg (Decimal,
    datetime) or from a listing's JSON (float, epoch)."""
    r = dict(row or {})

    def px(v):
        f = _num(v)
        return None if f is None else "%.9f" % f

    ts = _epoch(r.get("read_at"))
    canon = {"recheck_id": int(r["recheck_id"]),
             "intent_id": str(r.get("intent_id")),
             "read_at": None if ts is None else "%.6f" % ts,
             "booked_reading": r.get("booked_reading"),
             "booked_payout_price": px(r.get("booked_payout_price")),
             "venue_reading": r.get("venue_reading"),
             "venue_payout_price": px(r.get("venue_payout_price")),
             "verdict": r.get("verdict")}
    return hashlib.sha256(json.dumps(canon, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def settlement_payout(qty: float, long_price: float, order_intent: str) -> float:
    """THE CASH A SETTLED RESIDUAL IS PAID, side-aware. Pure.

    The close's own function (`FB.cash_for`) for every price above zero. At
    a long-side price of exactly ZERO `cash_for` answers 0 whatever the side
    -- its guard returns early for a non-positive price -- and a SHORT is
    then paid in full, not nothing. The side-aware rate the close relies on
    (`live_executor.cost_per_share`) is used there instead, so a correction
    TO zero on a short books the payout it is actually owed."""
    px = float(long_price)
    if not (0.0 <= px <= 1.0) or float(qty) <= 0:
        raise ValueError("a settlement price is in [0, 1] and a residual "
                         "is positive; got %r on %r" % (px, qty))
    if px > 0:
        return float(FB.cash_for(float(qty), px, order_intent))
    from .live_executor import cost_per_share
    return round(float(qty) * cost_per_share(0.0, order_intent), 2)


async def has_schema(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regclass('bettor_funded_settlement_corrections') IS NOT NULL"
        "   AND to_regclass('bettor_funded_correction_audit') IS NOT NULL"
        "   AND to_regclass('bettor_funded_settlement_rechecks') IS NOT NULL"))


async def _next_audit_id(conn) -> int:
    """Reserve the audit id BEFORE the effects, so every effect can name it
    and the row -- written last -- records what the effects actually were."""
    return int(await conn.fetchval(
        "SELECT nextval(pg_get_serial_sequence("
        "'bettor_funded_correction_audit', 'audit_id'))"))


async def _audit(conn, *, intent_id, recheck_id, outcome, refusal, operator,
                 statement, auth, seen, effect,
                 audit_id: int | None = None) -> int:
    if audit_id is None:
        audit_id = await _next_audit_id(conn)
    return int(await conn.fetchval(
        "INSERT INTO bettor_funded_correction_audit (audit_id, intent_id, "
        " recheck_id, outcome, refusal, operator, statement, "
        " authenticated_by, seen, effect) VALUES "
        " ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb) "
        "RETURNING audit_id",
        int(audit_id), str(intent_id), recheck_id, outcome, refusal,
        operator, statement,
        json.dumps({k: (auth or {}).get(k) for k in AUTH_FIELDS},
                   default=str),
        json.dumps(seen or {}, default=str),
        json.dumps(effect or {}, default=str)))


def _validate(*, intent_id, recheck_id, operator, statement,
              seen_recheck_sha, confirm, auth) -> str | None:
    if not ((auth or {}).get("admin_token_verified")
            and (auth or {}).get("resolution_key_verified")
            and str((auth or {}).get("operator") or "").strip()):
        # The route refuses before calling; this is the second statement.
        return R_BOTH_FACTORS
    if not str(operator or "").strip():
        return R_OPERATOR
    # THE OPERATOR IS WHO THE KEY AUTHENTICATES. A caller-supplied name that
    # differs from the configured identity is refused, not recorded.
    if str(operator).strip().casefold() != \
            str(auth.get("operator")).strip().casefold():
        return R_OPERATOR_NOT_AUTHENTICATED
    if str(confirm or "").strip() != str(intent_id or "").strip() \
            or not str(intent_id or "").strip():
        return R_CONFIRM
    if len(str(statement or "").strip()) < MIN_STATEMENT:
        return R_STATEMENT
    if recheck_id is None:
        return R_RECHECK_ID
    if not str(seen_recheck_sha or "").strip():
        return R_SEEN_SHA
    return None


def _as_int(v):
    try:
        if isinstance(v, bool):
            return None
        i = int(str(v).strip())
        return i if i > 0 else None
    except (TypeError, ValueError):
        return None


def _obj(v) -> dict:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return dict(v) if isinstance(v, dict) else {}


class _Rollback(Exception):
    def __init__(self, effect):
        super().__init__(effect.get("refusal"))
        self.effect = effect


async def book_settlement_correction(conn, *, intent_id: str, recheck_id,
                                     operator: str, statement: str,
                                     seen_recheck_sha: str, confirm: str,
                                     auth: dict | None = None,
                                     now: float | None = None) -> dict:
    """BOOK ONE SETTLEMENT CORRECTION, or refuse by name. Always audited;
    never raises: a failure half-way is rolled back AND audited."""
    trace: dict = {}
    try:
        return await _book(conn, intent_id=intent_id, recheck_id=recheck_id,
                           operator=operator, statement=statement,
                           seen_recheck_sha=seen_recheck_sha,
                           confirm=confirm, auth=auth or {}, now=now,
                           trace=trace)
    except Exception as exc:                                # noqa: BLE001
        # EVERY PATH IS AUDITED, including one that raised. The transaction
        # rolled back, so nothing was applied; the audit row says so.
        rb = exc if isinstance(exc, _Rollback) else _Rollback(
            {"refusal": R_RAISED,
             "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
        out = {"version": VERSION, "intent_id": str(intent_id), "ok": False,
               "refusal": rb.effect.get("refusal") or R_RAISED,
               "rolled_back": True, "detail": rb.effect,
               "submitted_anything": False}
        if not await has_schema(conn):
            return dict(out, audit_id=None, audited=False)
        aid = await _audit(
            conn, intent_id=intent_id, recheck_id=_as_int(recheck_id),
            outcome="REFUSED", refusal=out["refusal"],
            operator=str(operator or "").strip()[:200] or None,
            statement=str(statement or "").strip()[:2000] or None,
            auth=auth or {}, seen=trace.get("seen") or {},
            effect={"rolled_back": rb.effect})
        return dict(out, audit_id=aid, audited=True)


async def _book(conn, *, intent_id, recheck_id, operator, statement,
                seen_recheck_sha, confirm, auth, now, trace) -> dict:
    at = float(now if now is not None else time.time())
    iid = str(intent_id or "").strip()
    rid = _as_int(recheck_id)
    op = str(operator or "").strip()[:200] or None
    stmt = str(statement or "").strip()[:2000] or None
    seen_sha = str(seen_recheck_sha or "").strip()
    out: dict[str, Any] = {"version": VERSION, "intent_id": iid,
                           "recheck_id": rid, "at": at,
                           "submitted_anything": False}
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA, audited=False,
                    why="migrations 141 and 145 are needed")
    seen: dict[str, Any] = {"seen_recheck_sha": seen_sha or None}
    trace["seen"] = seen

    async def refuse(refusal, **detail):
        aid = await _audit(conn, intent_id=iid or str(intent_id),
                           recheck_id=rid, outcome="REFUSED",
                           refusal=refusal, operator=op, statement=stmt,
                           auth=auth, seen=seen, effect=detail)
        return dict(out, ok=False, refusal=refusal, audit_id=aid,
                    booked_anything=False, detail=detail)

    async with conn.transaction():
        bad = _validate(intent_id=iid, recheck_id=rid, operator=op,
                        statement=stmt, seen_recheck_sha=seen_sha,
                        confirm=confirm, auth=auth)
        if bad is not None:
            return await refuse(bad)
        leg = await conn.fetchrow(
            "SELECT intent_id, account_id, venue, us_market_slug, "
            "       order_intent, portfolio_group_id, closed_at, "
            "       closed_reason, residual_qty::float8 AS residual, "
            "       settlement "
            "  FROM bettor_funded_intents "
            " WHERE intent_id=$1 AND kind='ENTRY' FOR UPDATE", iid)
        if leg is None:
            return await refuse(R_NO_SUCH_INTENT)
        # ── ALREADY ANSWERED? The same request is idempotent; a different
        # one is refused -- and neither is reported as newly applied. ──
        prior = await conn.fetchrow(
            "SELECT * FROM bettor_funded_settlement_corrections "
            " WHERE recheck_id=$1", rid)
        if prior is not None:
            prior = dict(prior)
            same = (prior["intent_id"] == iid
                    and prior["seen_recheck_sha"] == seen_sha)
            if not same:
                return await refuse(
                    R_CORRECTED_DIFFERENTLY,
                    existing_correction_id=prior["correction_id"],
                    existing_intent_id=prior["intent_id"],
                    why=("that re-read is already answered by correction %s; "
                         "a re-read is answered once" %
                         prior["correction_id"]))
            aid = await _audit(
                conn, intent_id=iid, recheck_id=rid,
                outcome="ALREADY_CORRECTED", refusal=None, operator=op,
                statement=stmt, auth=auth, seen=seen,
                effect={"correction_id": prior["correction_id"],
                        "prior_audit_id": prior["audit_id"]})
            return dict(out, ok=True, already=True, audit_id=aid,
                        correction=_public(prior), booked_anything=False)
        booked = _obj(leg["settlement"])
        seen["booked_settlement"] = booked
        if leg["closed_at"] is None \
                or leg["closed_reason"] not in SETTLED_REASONS \
                or booked.get("terminal_reading") not in AUTHORITATIVE:
            return await refuse(R_NOT_SETTLED,
                                closed_reason=leg["closed_reason"],
                                booked_reading=booked.get("terminal_reading"))
        rc = await conn.fetchrow(
            "SELECT * FROM bettor_funded_settlement_rechecks "
            " WHERE recheck_id=$1 AND intent_id=$2", rid, iid)
        if rc is None:
            return await refuse(R_NO_SUCH_RECHECK)
        rc = dict(rc)
        seen["recheck"] = {k: rc.get(k) for k in (
            "recheck_id", "read_at", "booked_reading", "booked_payout_price",
            "venue_reading", "venue_payout_price", "verdict")}
        seen["recheck_sha"] = recheck_sha(rc)
        if rc["verdict"] == "NOT_ESTABLISHED":
            return await refuse(R_RECHECK_NOT_ESTABLISHED)
        if rc["verdict"] != "DISAGREES":
            return await refuse(R_RECHECK_AGREES, verdict=rc["verdict"])
        newest = await conn.fetchval(
            "SELECT recheck_id FROM bettor_funded_settlement_rechecks "
            " WHERE intent_id=$1 AND verdict <> 'NOT_ESTABLISHED' "
            " ORDER BY read_at DESC, recheck_id DESC LIMIT 1", iid)
        if int(newest) != rid:
            return await refuse(R_NOT_NEWEST, newest_recheck_id=int(newest))
        if seen_sha != seen["recheck_sha"]:
            return await refuse(R_STALE_SHA,
                                current_recheck_sha=seen["recheck_sha"])
        # ── THE RE-READ DISAGREED WITH WHAT IS BOOKED NOW ─────────────
        b_px, r_px = _num(booked.get("payout_price")), \
            _num(rc["booked_payout_price"])
        if rc["booked_reading"] != booked.get("terminal_reading") or (
                (b_px is None) != (r_px is None)) or (
                b_px is not None and abs(b_px - r_px) > 1e-9):
            return await refuse(
                R_NOT_AGAINST_BOOKED,
                booked_now={"terminal_reading": booked.get("terminal_reading"),
                            "payout_price": b_px},
                recheck_compared_with={"terminal_reading":
                                       rc["booked_reading"],
                                       "payout_price": r_px})
        to_reading = rc["venue_reading"]
        to_price = _num(rc["venue_payout_price"])
        if to_reading not in AUTHORITATIVE or (
                (to_reading == "REPORTED_SETTLEMENT") != (to_price is not None)):
            return await refuse(R_NOT_PRICEABLE, to_reading=to_reading,
                                to_price=to_price)
        # ── THE BOOK AGREES WITH ITSELF, OR NOTHING IS BOOKED ON IT ──
        econ = [dict(r) for r in await conn.fetch(
            "SELECT event_id, kind, amount_usd::float8 AS amount, "
            "       qty::float8 AS qty FROM bettor_funded_economics "
            " WHERE intent_id=$1 AND kind IN ('SETTLEMENT', $2)",
            iid, KIND)]
        settle = [e for e in econ if e["kind"] == "SETTLEMENT"]
        booked_usd = _num(booked.get("payout_usd"))
        econ_usd = round(sum(e["amount"] for e in econ), 6)
        seen["economics"] = econ
        if len(settle) != 1 or booked_usd is None \
                or abs(econ_usd - booked_usd) > USD_TOLERANCE \
                or not (_num(settle[0]["qty"]) or 0) > 0:
            return await refuse(
                R_BOOK_INCONSISTENT, settlement_events=len(settle),
                booked_payout_usd=booked_usd, economics_usd=econ_usd,
                why=("the booked payout must equal its SETTLEMENT event plus "
                     "every earlier correction, on exactly one SETTLEMENT "
                     "event that states its quantity; a book that disagrees "
                     "with itself is not corrected on top of"))
        qty = float(settle[0]["qty"])
        if abs(float(leg["residual"] or 0) - qty) > 1e-9:
            return await refuse(
                R_BOOK_INCONSISTENT, settled_qty=qty,
                residual_qty=float(leg["residual"] or 0),
                why="the settled quantity and the leg's residual disagree")
        # ── THE CORRECTED PAYOUT, BY THE CLOSE'S OWN RULES ───────────
        if to_reading == "EXPLICIT_VOID":
            rb = await FB.remaining_basis(conn, iid)
            new_usd = float(rb["remaining_basis_usd"])
            basis = ("the venue now declares a void, so the collateral on the "
                     "%s contracts held at settlement is returned: %s per "
                     "contract from the entry fills (the close's void rule, "
                     "FB.remaining_basis)" % (qty, rb["basis_per_contract"]))
            seen["remaining_basis"] = rb
        else:
            try:
                new_usd = settlement_payout(qty, to_price,
                                            str(leg["order_intent"]))
            except ValueError as exc:
                return await refuse(R_NOT_PRICEABLE, error=str(exc)[:200])
            basis = ("the venue's settlement endpoint now reports a long-side "
                     "price of %s; the payout of the %s contracts held is %s "
                     "(side-aware, %s)" % (to_price, qty, new_usd,
                                           leg["order_intent"]))
        delta = round(new_usd - booked_usd, 6)
        cid = correction_id_for(iid, rid)
        eid = event_id_for(iid, rid)
        # ── APPLY. The audit id first, so every effect can name it. ──
        aid = await _next_audit_id(conn)
        evidence = {"correction_id": cid, "recheck_id": rid,
                    "audit_id": aid, "from_reading": booked.get(
                        "terminal_reading"), "from_price": b_px,
                    "from_payout_usd": booked_usd, "to_reading": to_reading,
                    "to_price": to_price, "to_payout_usd": new_usd,
                    "operator": op}
        await FB.record_economic_event(
            conn, intent_id=iid, kind=KIND, amount_usd=delta, qty=qty, at=at,
            basis=("SETTLEMENT CORRECTION: %s. The difference from the booked "
                   "payout of %s is booked here; the original SETTLEMENT row "
                   "is not rewritten" % (basis, booked_usd)),
            evidence=evidence, event_id=eid)
        got = await conn.fetchval(
            "SELECT amount_usd::float8 FROM bettor_funded_economics "
            " WHERE event_id=$1 AND intent_id=$2 AND kind=$3", eid, iid, KIND)
        if got is None or abs(float(got) - delta) > USD_TOLERANCE:
            raise _Rollback({"refusal": R_CHANGED_UNDER_US,
                             "why": "the correction event is not the one "
                                    "this request computed",
                             "event_id": eid, "found_usd": got})
        await conn.execute(
            "INSERT INTO bettor_funded_settlement_corrections ("
            " correction_id, intent_id, recheck_id, from_reading, from_price,"
            " to_reading, to_price, delta_usd, economics_event_id, audit_id,"
            " operator, statement, seen_recheck_sha, created_at) VALUES "
            " ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,to_timestamp($14))",
            cid, iid, rid, booked.get("terminal_reading"), b_px, to_reading,
            to_price, delta, eid, aid, op, stmt, seen_sha, at)
        # ── THE CORRECTED READING BECOMES THE BOOKED ONE ─────────────
        #
        # `at` IS THE INSTANT THE CORRECTED READING WAS BOOKED, not the
        # original close: the labeller reads `settlement.at` as when the
        # label became known, and a label corrected today was not known when
        # the leg first closed. It also puts the leg back in the re-read
        # window, so the corrected reading is itself re-read.
        history = list(booked.get("history") or [])
        history.append({k: v for k, v in booked.items() if k != "history"})
        corrected = {"terminal_reading": to_reading, "at": at,
                     "payout_price": to_price, "payout_usd": new_usd,
                     "why": ("corrected from %s at %s on re-read %d (%s)"
                             % (booked.get("terminal_reading"), b_px, rid,
                                cid)),
                     "venue_read_at": _epoch(rc["read_at"]),
                     "corrected_by": {"correction_id": cid,
                                      "recheck_id": rid, "audit_id": aid,
                                      "operator": op,
                                      "economics_event_id": eid},
                     "history": history}
        status = await conn.execute(
            "UPDATE bettor_funded_intents SET settlement=$2::jsonb, "
            "  updated_at=now() WHERE intent_id=$1 "
            "   AND settlement->>'terminal_reading' IS NOT DISTINCT FROM $3",
            iid, json.dumps(corrected, default=str),
            booked.get("terminal_reading"))
        if not str(status).endswith(" 1"):
            raise _Rollback({"refusal": R_CHANGED_UNDER_US,
                             "why": "the booked settlement moved"})
        # ── A NEW OUTCOME VERSION FOR EVERY DECISION ON THE GROUP ─────
        outcomes = await _new_outcome_versions(
            conn, group_id=leg["portfolio_group_id"], correction_id=cid,
            delta=delta)
        effect = {"ok": True, "correction_id": cid, "economics_event_id": eid,
                  "delta_usd": delta, "from": {
                      "terminal_reading": booked.get("terminal_reading"),
                      "payout_price": b_px, "payout_usd": booked_usd},
                  "to": {"terminal_reading": to_reading,
                         "payout_price": to_price, "payout_usd": new_usd},
                  "outcome_versions": outcomes,
                  "original_settlement_row_rewritten": False}
        await _audit(conn, intent_id=iid, recheck_id=rid, outcome="ACCEPTED",
                     refusal=None, operator=op, statement=stmt, auth=auth,
                     seen=seen, effect=effect, audit_id=aid)
    row = await conn.fetchrow(
        "SELECT * FROM bettor_funded_settlement_corrections "
        " WHERE correction_id=$1", cid)
    return dict(out, ok=True, already=False, audit_id=aid,
                correction=_public(dict(row)), effect=effect,
                booked_anything=True, attestation=ATTESTATION_TEXT)


async def _new_outcome_versions(conn, *, group_id, correction_id,
                                delta) -> list:
    """Record the corrected realised net as a NEW outcome version for every
    decision on the group. A refusal (the group still open, no ledger here) is
    reported beside the correction, not hidden and not fatal: the correction
    is the accounting; the ledger is a reader of it."""
    if not group_id:
        return []
    from . import bettor_funded_learning as FL
    if not await conn.fetchval(
            "SELECT to_regclass('bettor_funded_decisions') IS NOT NULL"):
        return [{"ok": False, "refusal": FL.R_SCHEMA_UNAVAILABLE}]
    ids = [r["decision_id"] for r in await conn.fetch(
        "SELECT decision_id FROM bettor_funded_decisions WHERE group_id=$1 "
        " ORDER BY decided_at, decision_id", str(group_id))]
    out = []
    for did in ids:
        got = await FL.join_realised(
            conn, decision_id=did,
            correction_reason=("SETTLEMENT_CORRECTION %s: a settled leg's "
                               "booked payout moved by %s" % (correction_id,
                                                              delta)))
        out.append({"decision_id": did, "ok": bool(got.get("ok")),
                    "refusal": got.get("refusal"),
                    "outcome_version": got.get("outcome_version"),
                    "already": got.get("already")})
    return out


def _public(row: dict) -> dict:
    r = dict(row)
    for k in ("from_price", "to_price", "delta_usd"):
        r[k] = _num(r.get(k))
    return r


async def listing(conn, *, intent_id: str | None = None,
                  limit: int = 50) -> dict:
    """What an operator sees before deciding: every correction, every attempt,
    and every leg whose newest established re-read disagrees and is not yet
    answered -- with the sha a correction must cite."""
    if not await has_schema(conn):
        return {"ok": False, "refusal": R_SCHEMA}
    lim = max(1, min(int(limit), 500))
    corrections = [_public(dict(r)) for r in await conn.fetch(
        "SELECT * FROM bettor_funded_settlement_corrections "
        " WHERE ($1::text IS NULL OR intent_id=$1) "
        " ORDER BY created_at DESC, correction_id LIMIT $2", intent_id, lim)]
    attempts = [dict(r) for r in await conn.fetch(
        "SELECT audit_id, at, intent_id, recheck_id, outcome, refusal, "
        "       operator FROM bettor_funded_correction_audit "
        " WHERE ($1::text IS NULL OR intent_id=$1) "
        " ORDER BY audit_id DESC LIMIT $2", intent_id, lim)]
    awaiting = []
    for r in await conn.fetch(
            "SELECT newest.* FROM (SELECT DISTINCT ON (r.intent_id) r.* "
            "   FROM bettor_funded_settlement_rechecks r "
            "  WHERE r.verdict <> 'NOT_ESTABLISHED' "
            "    AND ($1::text IS NULL OR r.intent_id=$1) "
            "  ORDER BY r.intent_id, r.read_at DESC, r.recheck_id DESC) newest"
            " WHERE newest.verdict = 'DISAGREES' "
            "   AND NOT EXISTS (SELECT 1 FROM "
            "        bettor_funded_settlement_corrections c "
            "        WHERE c.recheck_id = newest.recheck_id) "
            " ORDER BY newest.read_at DESC LIMIT $2", intent_id, lim):
        d = dict(r)
        awaiting.append({
            "intent_id": d["intent_id"], "recheck_id": int(d["recheck_id"]),
            "read_at": _epoch(d["read_at"]),
            "booked_reading": d["booked_reading"],
            "booked_payout_price": _num(d["booked_payout_price"]),
            "venue_reading": d["venue_reading"],
            "venue_payout_price": _num(d["venue_payout_price"]),
            "recheck_sha": recheck_sha(d)})
    return {"ok": True, "version": VERSION, "corrections": corrections,
            "attempts": attempts, "awaiting_correction": awaiting,
            "attestation": ATTESTATION_TEXT,
            "never": ("rewrites the original SETTLEMENT row, books a second "
                      "SETTLEMENT, or submits anything")}
