"""FUNDED ACTIVATION: the authorization path, complete, with capital off.

WHAT WAS WRONG WITH THE FIRST VERSION, AND IT IS A FAIR CRITICISM.
`activate()` refused unconditionally; three of its readiness checks were
hard-coded `met: False`; and the account and limit submissions were stored
as proposals that no enforcement path ever read. A panel like that cannot
be wrong, which is another way of saying it establishes nothing.

WHAT THIS DOES INSTEAD:

  * ACCOUNT SELECTION reads the canonical registry, `bettor_desk_accounts`,
    by ACCOUNT_ID. The paused account is refused on its `paused` column and
    its `accounting_status`, not on how its name is spelled -- renaming it
    changes nothing.
  * READINESS IS DERIVED FROM EVIDENCE. Each check names the rows it read
    and the verdict it drew. A check with no evidence is UNKNOWN and
    BLOCKS, exactly as an unevaluable risk rail does. None of them is a
    constant, and none of them is an unconditional pass.
  * LIMITS ARE CONSUMED BY THE ENFORCEMENT PATH.
    `bettor_entry_execution.effective_limits` takes the approved set and
    returns the element-wise MINIMUM against the frozen rails, so an
    approval can only ever TIGHTEN. A limit above a frozen rail is ignored
    and reported as ignored.
  * RESEARCH-WAIVED AND DEMONSTRATION ACTIVITY DO NOT QUALIFY. The
    "an autonomous entry was admitted" check excludes rows carrying the
    calibration waiver and excludes the demonstration and acceptance
    books, by experiment and by provenance.

AND CAPITAL STAYS OFF. The positive path can be reached and demonstrated,
but only for a venue whose class is TEST. For a FUNDED venue the answer is
`FUNDED_ACTIVATION_REQUIRES_THE_OWNERS_WRITTEN_AUTHORIZATION` even when
every other check is met, because enabling real submission is a code change
with the owner's authority behind it -- not a form on a page.
"""

from __future__ import annotations

import json
import time

VERSION = "BETTOR_FUNDED_ACTIVATION_V1"

# ── venues, by class ────────────────────────────────────────────────
#
# THE CLASS IS THE GATE, not the name. A venue this table does not know is
# UNKNOWN, and unknown refuses.
VENUE_TEST = "TEST"
VENUE_FUNDED = "FUNDED"
VENUE_CLASS = {
    "PMUS_TEST": VENUE_TEST,      # the venue's own sandbox
    "SANDBOX": VENUE_TEST,
    "PMUS": VENUE_FUNDED,
    "POLYMARKET": VENUE_FUNDED,
}

#: Where the approved account binding and limit set are recorded.
ACCOUNT_KEY = "bettor_funded_account_binding"
LIMITS_KEY = "bettor_funded_limits_approved"

#: Where an AUTHORIZATION is recorded. A SEPARATE key from the binding, on
#: purpose: the first version wrote the authorisation over ACCOUNT_KEY, which
#: destroyed the binding it had just been computed from -- so the desk then
#: showed "no account bound" immediately after authorising one, and a second
#: activation had nothing to read. The binding is the operator's intent; the
#: authorisation is the verdict about it. They are two records.
AUTHORIZATION_KEY = "bettor_funded_authorization"

#: The owner's authorisation record. Its ABSENCE is the last refusal on the
#: funded path, and this module can only read it.
OWNER_AUTH_KEY = "bettor_funded_owner_authorization"

#: Accounting states an account may be activated from. Anything else --
#: including UNKNOWN -- refuses.
ACCOUNTING_OK = ("CLEAN", "RECONCILED", "VERIFIED")

# ── refusals, each by name ──────────────────────────────────────────
R_NO_ACCOUNT = "ACCOUNT_ID_NOT_SUPPLIED"
R_ACCOUNT_UNKNOWN = "ACCOUNT_ID_NOT_IN_THE_CANONICAL_REGISTRY"
R_ACCOUNT_NOT_ACTIVE = "ACCOUNT_IS_NOT_ACTIVE"
R_ACCOUNT_PAUSED = "ACCOUNT_IS_PAUSED"
R_ACCOUNTING_UNCERTAIN = "ACCOUNT_ACCOUNTING_IS_NOT_RESOLVED"
R_VENUE_UNKNOWN = "VENUE_CLASS_NOT_ESTABLISHED"
R_LIMITS_MISSING = "LIMIT_SET_INCOMPLETE"
R_LIMITS_NOT_APPROVED = "LIMIT_SET_NOT_APPROVED_BY_THE_OWNER"
R_READINESS_UNMET = "FUNDED_ACTIVATION_PREREQUISITES_NOT_MET"
R_OWNER_AUTH = "FUNDED_ACTIVATION_REQUIRES_THE_OWNERS_WRITTEN_AUTHORIZATION"

#: The owner's four limits, and the FROZEN RAIL each one tightens. A rail
#: absent from this map is untouched by any approval.
LIMIT_TO_RAIL = {
    "capital_usd": "MAX_CAPITAL_DEPLOYED",
    "per_order_usd": "MAX_MARKET_EXPOSURE",
    "max_exposure_usd": "MAX_CORRELATED_EXPOSURE",
    "daily_loss_stop_usd": "MAX_DRAWDOWN",
}
REQUIRED_LIMITS = tuple(LIMIT_TO_RAIL)


def _truthy(raw) -> bool:
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    try:
        return bool(json.loads(raw))
    except (TypeError, ValueError):
        return False


def _obj(raw):
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


async def _state(conn, key):
    return _obj(await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key = $1", key))


def venue_class(venue: str) -> str | None:
    return VENUE_CLASS.get(str(venue or "").strip().upper())


# ── 1 · ACCOUNT SELECTION, from the canonical registry ──────────────

ACCOUNT_SQL = """
    SELECT account_id, desk_id, status, paused, pause_reason,
           accounting_status, accounting_detail, opening_balance::float8
             AS opening_balance, provenance,
           extract(epoch FROM opened_at)::float8 AS opened_at
      FROM bettor_desk_accounts
     WHERE account_id = $1
"""

REGISTRY_SQL = """
    SELECT account_id, desk_id, status, paused, accounting_status
      FROM bettor_desk_accounts
     ORDER BY opened_at DESC NULLS LAST LIMIT 50
"""


async def account_selection(conn, account_id: str) -> dict:
    """Resolve a CANONICAL account id against the registry, or refuse.

    THE PAUSE IS READ OFF THE ROW. `bettor_desk_accounts.paused` and
    `accounting_status` are what stop the accounting-uncertain account, so
    renaming it, relabelling it on a screen or submitting it under another
    display name changes nothing at all.
    """
    ident = str(account_id or "").strip()
    out = {"requested_account_id": ident, "source": "bettor_desk_accounts",
           "matched": False}
    if not ident:
        return dict(out, ok=False, refusal=R_NO_ACCOUNT,
                    why="an activation needs the canonical account id")
    try:
        row = await conn.fetchrow(ACCOUNT_SQL, ident)
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, ok=False, refusal=R_ACCOUNT_UNKNOWN,
                    unreadable=type(exc).__name__,
                    why=("the account registry could not be read, so this "
                         "account's state is UNKNOWN and unknown refuses"))
    if row is None:
        return dict(out, ok=False, refusal=R_ACCOUNT_UNKNOWN,
                    why=("no account carries that canonical id. An account "
                         "that is not in the registry cannot be activated"))
    acct = dict(row)
    out.update(matched=True, account=acct)
    if str(acct.get("status") or "") != "ACTIVE":
        return dict(out, ok=False, refusal=R_ACCOUNT_NOT_ACTIVE,
                    why="status is %r" % acct.get("status"))
    if bool(acct.get("paused")):
        return dict(out, ok=False, refusal=R_ACCOUNT_PAUSED,
                    pause_reason=acct.get("pause_reason"),
                    why=("the account is paused on its own row. This is read "
                         "from the registry, not from a display name"))
    st = str(acct.get("accounting_status") or "UNKNOWN").upper()
    if st not in ACCOUNTING_OK:
        return dict(out, ok=False, refusal=R_ACCOUNTING_UNCERTAIN,
                    accounting_status=st,
                    accounting_detail=acct.get("accounting_detail"),
                    why=("an account whose accounting is %s cannot fund "
                         "trading. UNKNOWN is not clean" % st))
    return dict(out, ok=True, account_id=ident,
                accounting_status=st)


# ── 2 · READINESS, DERIVED FROM EVIDENCE ────────────────────────────

def _check(name, met, detail, evidence=None, basis=None) -> dict:
    return {"check": name, "met": met, "detail": detail,
            "evidence": evidence or {},
            "basis": basis or "READ_AT_THIS_INSTANT"}


async def _freshness_check(conn) -> dict:
    """Is there an ESTABLISHED basis for the venue book's age?

    DERIVED, NOT DECLARED. The scheduled lane records, on every book read,
    the age it measured and the BASIS of that age. If the most recent cycle
    read books and every one of them recorded an unestablished basis, the
    check fails with those counts. If no cycle has read a book, there is no
    evidence and the check is UNKNOWN -- which blocks.
    """
    try:
        raw = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1",
            "ext_pinnacle_last_cycle")
    except Exception as exc:                                   # noqa: BLE001
        return _check("venue_book_freshness_basis", None,
                      "the cycle heartbeat could not be read: %s"
                      % type(exc).__name__)
    cycle = _obj(raw) or {}
    if not cycle:
        return _check("venue_book_freshness_basis", None,
                      "no scheduled cycle has been recorded, so there is no "
                      "book read to derive a freshness basis from")
    ledger = cycle.get("mapped_candidate_ledger") or []
    bases, ages = {}, 0
    for row in ledger:
        clock = (row or {}).get("venue_clock") or {}
        b = str(clock.get("basis") or (row or {}).get("age_basis")
                or "NOT_RECORDED")
        bases[b] = bases.get(b, 0) + 1
        if clock.get("age_at_read_s") is not None:
            ages += 1
    established = sum(n for b, n in bases.items()
                      if "UNESTABLISHED" not in b.upper()
                      and "NOT_RECORDED" not in b.upper()
                      and "UNKNOWN" not in b.upper())
    ev = {"cycle_at": cycle.get("at"), "cycle_label": cycle.get("cycle_label"),
          "book_reads_in_the_ledger": len(ledger),
          "reads_with_a_measured_age": ages, "bases": bases,
          "reads_with_an_established_basis": established}
    if not ledger:
        return _check("venue_book_freshness_basis", None,
                      "the last cycle recorded no book read, so no basis "
                      "was observed", ev)
    if established:
        return _check("venue_book_freshness_basis", True,
                      "%d of %d book reads recorded an established age "
                      "basis" % (established, len(ledger)), ev)
    return _check("venue_book_freshness_basis", False,
                  "every book read in the last cycle recorded an "
                  "UNESTABLISHED age basis: what the venue's transact time "
                  "denotes is not established, so the freshness refusal "
                  "stands", ev)


SETTLEMENT_SQL = """
    SELECT coalesce(settlement_comparison->>'verdict',
                    settlement_comparison->>'status', 'NOT_RECORDED')
             AS verdict,
           count(*) AS n
      FROM external_valuations
     WHERE experiment_id = $1
       AND decided_at >= now() - ($2 || ' hours')::interval
     GROUP BY 1 ORDER BY 2 DESC
"""


async def _settlement_check(conn, experiment_id, hours) -> dict:
    """Has any recent candidate had a COMPATIBLE settlement rule stated?"""
    try:
        rows = [dict(r) for r in await conn.fetch(SETTLEMENT_SQL,
                                                 experiment_id, str(hours))]
    except Exception as exc:                                   # noqa: BLE001
        return _check("settlement_compatibility", None,
                      "the valuation rows could not be read: %s"
                      % type(exc).__name__)
    by = {str(r["verdict"]).upper(): int(r["n"]) for r in rows}
    ev = {"window_hours": hours, "by_verdict": by,
          "rows": sum(by.values())}
    if not by:
        return _check("settlement_compatibility", None,
                      "no candidate was evaluated in this window, so no "
                      "settlement comparison exists to read", ev)
    compatible = sum(n for v, n in by.items()
                     if "COMPATIBLE" in v and "IN" not in v.split("COMPAT")[0])
    conflicting = sum(n for v, n in by.items() if "CONFLICT" in v)
    unknown = sum(n for v, n in by.items()
                  if "UNKNOWN" in v or "NOT_RECORDED" in v)
    ev.update(compatible=compatible, conflicting=conflicting,
              unknown=unknown)
    if compatible and not conflicting:
        return _check("settlement_compatibility", True,
                      "%d recent candidate(s) had a stated compatible "
                      "settlement rule and none conflicted" % compatible, ev)
    return _check("settlement_compatibility", False,
                  "of %d recent candidates, %d compatible, %d conflicting, "
                  "%d unknown -- the comparison is per fixture and UNKNOWN "
                  "refuses" % (ev["rows"], compatible, conflicting, unknown),
                  ev)


SCOPE_SQL = """
    SELECT coalesce(period, 'NOT_RECORDED') AS period, count(*) AS n
      FROM external_valuations
     WHERE experiment_id = $1
       AND decided_at >= now() - ($2 || ' hours')::interval
     GROUP BY 1 ORDER BY 2 DESC
"""


async def _scope_check(conn, experiment_id, hours) -> dict:
    """Did the market scope RESOLVE for recent candidates?"""
    try:
        rows = [dict(r) for r in await conn.fetch(SCOPE_SQL, experiment_id,
                                                 str(hours))]
    except Exception as exc:                                   # noqa: BLE001
        return _check("market_scope_metadata", None,
                      "the valuation rows could not be read: %s"
                      % type(exc).__name__)
    by = {str(r["period"]): int(r["n"]) for r in rows}
    ev = {"window_hours": hours, "by_period": by, "rows": sum(by.values())}
    if not by:
        return _check("market_scope_metadata", None,
                      "no candidate was evaluated in this window, so no "
                      "scope was resolved either way", ev)
    resolved = sum(n for p, n in by.items()
                   if p.upper() in ("FULL_GAME", "FULL_TIME", "FULL_MATCH"))
    unresolved = ev["rows"] - resolved
    ev.update(resolved=resolved, unresolved=unresolved)
    if resolved:
        return _check("market_scope_metadata", True,
                      "%d recent candidate(s) had their market scope "
                      "established from the type's own scope token"
                      % resolved, ev)
    return _check("market_scope_metadata", False,
                  "no recent candidate's scope resolved to a full match; "
                  "%d row(s) carry an unresolved or segment scope"
                  % unresolved, ev)


ADMITTED_SQL = """
    SELECT count(*) FILTER (WHERE admissible)                    AS admitted,
           count(*) FILTER (WHERE admissible
                            AND risk_verdict::text LIKE '%research_waiver%')
                                                                 AS waived,
           count(*)                                              AS evaluated
      FROM external_valuations
     WHERE experiment_id = $1
       AND decided_at >= now() - ($2 || ' hours')::interval
"""

QUALIFYING_POSITIONS_SQL = """
    SELECT count(*) AS n
      FROM rn1x_positions p
     WHERE p.experiment_id = $1
       AND p.provenance = ANY($2::text[])
"""


async def _admitted_check(conn, experiment_id, hours,
                          qualifying_provenance) -> dict:
    """Has the lane admitted an entry ON ITS OWN GATES, unwaived?

    RESEARCH-WAIVED AND DEMONSTRATION ACTIVITY DO NOT QUALIFY. An entry
    admitted under the calibration waiver cleared fewer gates than a funded
    entry must; a demonstration entry cleared none, because its inputs were
    chosen. Both are excluded here -- the waiver by the row's own risk
    verdict, the demonstration by experiment and provenance.
    """
    try:
        row = dict(await conn.fetchrow(ADMITTED_SQL, experiment_id,
                                       str(hours)))
        held = int(await conn.fetchval(QUALIFYING_POSITIONS_SQL,
                                       experiment_id,
                                       list(qualifying_provenance)) or 0)
    except Exception as exc:                                   # noqa: BLE001
        return _check("an_autonomous_entry_was_admitted_unwaived", None,
                      "the lane's own rows could not be read: %s"
                      % type(exc).__name__)
    admitted = int(row.get("admitted") or 0)
    waived = int(row.get("waived") or 0)
    unwaived = admitted - waived
    ev = {"window_hours": hours, "evaluated": int(row.get("evaluated") or 0),
          "admitted": admitted, "of_those_research_waived": waived,
          "admitted_without_a_waiver": unwaived,
          "qualifying_positions_held": held,
          "qualifying_provenance": list(qualifying_provenance),
          "excluded": ["research-waived admissions",
                       "the controlled demonstration",
                       "the acceptance position"]}
    if unwaived > 0:
        return _check("an_autonomous_entry_was_admitted_unwaived", True,
                      "%d entr(ies) admitted on the lane's own gates with "
                      "no waiver" % unwaived, ev)
    return _check("an_autonomous_entry_was_admitted_unwaived", False,
                  "no entry has been admitted without the calibration "
                  "waiver in this window (%d evaluated, %d admitted, %d of "
                  "those waived)" % (ev["evaluated"], admitted, waived), ev)


async def readiness(conn, *, experiment_id=None, hours: int = 168,
                    account_id: str | None = None) -> dict:
    """EVERY PREREQUISITE, EVALUATED. UNKNOWN blocks, and says so."""
    from . import bettor_entry_execution as EX
    from . import bettor_external_shadow as ext
    from . import bettor_entry_inventory as inv

    exp = experiment_id or ext.EXPERIMENT_ID
    qualifying = (inv.PROVENANCE,)
    checks = []

    # 1 · funded submission is off, read from the module that decides it
    checks.append(_check(
        "funded_submission_disabled",
        EX.REAL_ORDER_SUBMISSION_ENABLED is False,
        "bettor_entry_execution.REAL_ORDER_SUBMISSION_ENABLED is %r and the "
        "lane's writer CHECKs order_submitted FALSE"
        % (EX.REAL_ORDER_SUBMISSION_ENABLED,),
        {"real_order_submission_enabled": EX.REAL_ORDER_SUBMISSION_ENABLED,
         "real_capital_at_risk": EX.REAL_CAPITAL_AT_RISK},
        "MODULE_CONSTANT_OF_THE_LANE_THAT_WOULD_SUBMIT"))

    # 2 · the account, from the canonical registry
    if account_id:
        sel = await account_selection(conn, account_id)
        checks.append(_check(
            "account_selected_and_clean", bool(sel.get("ok")),
            sel.get("why") or ("account %s is ACTIVE, not paused and its "
                               "accounting is %s"
                               % (account_id, sel.get("accounting_status"))),
            {k: sel.get(k) for k in ("requested_account_id", "matched",
                                     "refusal", "accounting_status",
                                     "pause_reason")},
            "CANONICAL_REGISTRY_ROW_bettor_desk_accounts"))
    else:
        try:
            reg = [dict(r) for r in await conn.fetch(REGISTRY_SQL)]
        except Exception as exc:                               # noqa: BLE001
            reg = [{"unreadable": type(exc).__name__}]
        checks.append(_check(
            "account_selected_and_clean", False,
            "no account id has been bound. Activation needs the CANONICAL "
            "id of an account in the registry",
            {"registry_sample": reg[:10]},
            "CANONICAL_REGISTRY_ROW_bettor_desk_accounts"))

    # 3 · the limit set, and whether it is approved AND within the rails
    stored = await _state(conn, LIMITS_KEY) or {}
    approved = bool(stored.get("approved"))
    proposed = dict(stored.get("proposed") or {})
    eff = EX.effective_limits(proposed)
    checks.append(_check(
        "limits_recorded_and_complete",
        bool(proposed) and not [k for k in REQUIRED_LIMITS
                                if proposed.get(k) in (None, "")],
        ("recorded: %s" % (proposed or "nothing")),
        {"proposed": proposed, "required": list(REQUIRED_LIMITS)},
        "STORED_APPROVAL_ROW"))
    checks.append(_check(
        "limits_approved_by_the_owner", approved,
        ("approved by %s at %s" % (stored.get("approved_by"),
                                   stored.get("approved_at")))
        if approved else
        "a recorded limit set is the owner's stated intent; approval is a "
        "separate act and is not granted by this panel",
        {"approved": approved, "approved_by": stored.get("approved_by")},
        "STORED_APPROVAL_ROW"))
    checks.append(_check(
        "approved_limits_tighten_the_enforced_rails",
        bool(proposed) and eff["tightened"] != {} or None if not proposed
        else bool(eff["tightened"]) or eff["ignored_because_looser"] == {},
        "effective rails are the element-wise minimum of the frozen set and "
        "the approved set; an approval can only tighten",
        {"effective": eff["effective"], "tightened": eff["tightened"],
         "ignored_because_looser": eff["ignored_because_looser"],
         "frozen_digest": eff["frozen_digest"],
         "effective_digest": eff["effective_digest"]},
        "bettor_entry_execution.effective_limits"))

    # 4-6 · the three that used to be constants, now derived from rows
    checks.append(await _freshness_check(conn))
    checks.append(await _settlement_check(conn, exp, hours))
    checks.append(await _scope_check(conn, exp, hours))

    # 7 · an admitted, unwaived entry of this lane's own
    checks.append(await _admitted_check(conn, exp, hours, qualifying))

    unmet = [c for c in checks if c["met"] is not True]
    unknown = [c for c in checks if c["met"] is None]
    return {
        "version": VERSION, "at": time.time(),
        "experiment_id": exp, "window_hours": hours,
        "checks": checks, "unmet": unmet, "unmet_count": len(unmet),
        "unknown_count": len(unknown),
        "ready": not unmet,
        "unknown_blocks": ("a check with no evidence is UNKNOWN and blocks, "
                           "exactly as an unevaluable risk rail does"),
        "even_when_ready": (
            "a TEST venue can be authorised from here. A FUNDED venue needs "
            "the owner's written authorisation, which this service can only "
            "read -- and enabling real submission is a code change beyond "
            "it"),
    }


# ── 3 · THE AUTHORIZATION PATH ──────────────────────────────────────

async def authorize(conn, *, account_id: str, venue: str, by: str,
                    experiment_id=None, hours: int = 168) -> dict:
    """RUN THE WHOLE AUTHORIZATION, and say exactly where it stopped.

    The positive path is reachable: a TEST-class venue, a clean canonical
    account, an owner-approved limit set inside the frozen rails and every
    readiness check met returns AUTHORIZED. It authorises the OPERATING
    path against a test venue and it does not enable capital: real
    submission stays off in code, and the venue-boundary gate authorises
    every submission independently of anything here.
    """
    from . import bettor_entry_execution as EX

    out = {"version": VERSION, "at": time.time(), "by": by,
           "requested": {"account_id": account_id, "venue": venue},
           "funded_submission": "DISABLED",
           "authorises_capital": False}
    klass = venue_class(venue)
    out["venue_class"] = klass
    if klass is None:
        return dict(out, ok=False, applied=None,
                    failed={"refusal": R_VENUE_UNKNOWN,
                            "known": sorted(VENUE_CLASS)},
                    refusal=R_VENUE_UNKNOWN)

    sel = await account_selection(conn, account_id)
    out["account_selection"] = sel
    if not sel.get("ok"):
        return dict(out, ok=False, applied=None,
                    failed={"refusal": sel["refusal"], "why": sel.get("why")},
                    refusal=sel["refusal"])

    stored = await _state(conn, LIMITS_KEY) or {}
    proposed = dict(stored.get("proposed") or {})
    missing = [k for k in REQUIRED_LIMITS if proposed.get(k) in (None, "")]
    if missing:
        return dict(out, ok=False, applied=None,
                    failed={"refusal": R_LIMITS_MISSING, "missing": missing},
                    refusal=R_LIMITS_MISSING)
    if not stored.get("approved"):
        return dict(out, ok=False, applied=None,
                    failed={"refusal": R_LIMITS_NOT_APPROVED,
                            "why": ("the limit set is recorded but not "
                                    "approved by the owner")},
                    refusal=R_LIMITS_NOT_APPROVED)
    eff = EX.effective_limits(proposed)
    out["effective_limits"] = eff

    ready = await readiness(conn, experiment_id=experiment_id, hours=hours,
                            account_id=account_id)
    out["readiness"] = {k: ready[k] for k in
                        ("ready", "unmet_count", "unknown_count")}
    out["unmet"] = [c["check"] for c in ready["unmet"]]
    if not ready["ready"]:
        return dict(out, ok=False, applied=None,
                    failed={"refusal": R_READINESS_UNMET,
                            "unmet": out["unmet"],
                            "detail": ready["unmet"]},
                    refusal=R_READINESS_UNMET)

    if klass == VENUE_FUNDED:
        owner = await _state(conn, OWNER_AUTH_KEY)
        out["owner_authorization_present"] = bool(owner)
        return dict(out, ok=False, applied=None,
                    failed={"refusal": R_OWNER_AUTH,
                            "why": ("every check is met, and a FUNDED venue "
                                    "still needs the owner's written "
                                    "authorisation. Enabling real "
                                    "submission is a code change with that "
                                    "authority behind it, not a form")},
                    refusal=R_OWNER_AUTH)

    # ── THE POSITIVE PATH, for a TEST venue ─────────────────────────
    record = {"account_id": sel["account_id"], "venue": venue,
              "venue_class": klass, "by": by, "at": time.time(),
              "effective_limits": eff["effective"],
              "effective_digest": eff["effective_digest"],
              "readiness_at_authorisation": [c["check"] for c in
                                             ready["checks"]],
              "funded_submission": "DISABLED",
              "authorises": ("the operating path against a TEST venue, "
                             "under the effective limits above"),
              "does_not_authorise": [
                  "real order submission (off in code)",
                  "any funded venue",
                  "raising any frozen rail"]}
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        AUTHORIZATION_KEY, json.dumps(record))
    return dict(out, ok=True, applied=record, failed=None,
                verdict="AUTHORIZED_FOR_A_TEST_VENUE",
                recorded_under=AUTHORIZATION_KEY,
                the_binding_is_untouched=ACCOUNT_KEY)


def describe() -> dict:
    return {
        "version": VERSION,
        "venue_classes": dict(VENUE_CLASS),
        "limit_to_rail": dict(LIMIT_TO_RAIL),
        "refusals": [R_NO_ACCOUNT, R_ACCOUNT_UNKNOWN, R_ACCOUNT_NOT_ACTIVE,
                     R_ACCOUNT_PAUSED, R_ACCOUNTING_UNCERTAIN,
                     R_VENUE_UNKNOWN, R_LIMITS_MISSING,
                     R_LIMITS_NOT_APPROVED, R_READINESS_UNMET, R_OWNER_AUTH],
        "account_identity": ("the CANONICAL account_id in "
                             "bettor_desk_accounts. The pause and the "
                             "accounting state are read off that row, not "
                             "from a display name"),
        "research_waived_does_not_qualify": True,
        "demonstration_does_not_qualify": True,
        "approvals_can_only_tighten": True,
        "authorises_capital": False,
    }
