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
#: The owner's record exists but does not cover this request. Three separate
#: names, because "you did not authorise this" and "you authorised something
#: else" are different facts and need different actions.
R_OWNER_AUTH_ACCOUNT = "THE_OWNERS_AUTHORIZATION_NAMES_A_DIFFERENT_ACCOUNT"
R_OWNER_AUTH_VENUE = "THE_OWNERS_AUTHORIZATION_NAMES_A_DIFFERENT_VENUE"
R_OWNER_AUTH_LIMITS = "THE_OWNERS_AUTHORIZATION_COVERS_DIFFERENT_LIMITS"

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

#: The registry READ, for a human who has to choose an account. Identity and
#: state only: no balance, no credential, nothing about money.
REGISTRY_READ_SQL = """
    SELECT account_id, desk_id, status, paused, pause_reason,
           accounting_status,
           extract(epoch FROM opened_at)::float8 AS opened_at
      FROM bettor_desk_accounts ORDER BY account_id
"""

#: Fields this surface must never carry, whatever the table grows.
REGISTRY_FORBIDDEN_FIELDS = ("opening_balance", "balance", "cash",
                             "buying_power", "account_value", "equity",
                             "provenance")


def summarise_registry(rows) -> dict:
    """WHICH ACCOUNTS COULD BE ARMED, BY THEIR OWN ROWS. Pure.

    It is pure so it can be tested without a connection pool, and so the
    eligibility rule lives beside the refusals it mirrors rather than inside
    a route handler.

    ELIGIBLE IS NOT APPROVED. This says only that a row does not itself
    refuse; naming the account and approving it are the owner's act.
    """
    clean = []
    for r in rows or ():
        row = {k: v for k, v in dict(r).items()
               if k not in REGISTRY_FORBIDDEN_FIELDS}
        clean.append(row)
    eligible = [r["account_id"] for r in clean
                if str(r.get("status") or "") == "ACTIVE"
                and not r.get("paused")
                and str(r.get("accounting_status") or "").upper()
                in ACCOUNTING_OK]
    return {
        "accounts": clean, "count": len(clean),
        "activation_eligible_by_their_rows": eligible,
        "eligible_means": ("ACTIVE, not paused, and an accounting status in "
                           "%s. It is NOT an approval: naming the account "
                           "and approving it are the owner's act"
                           % (list(ACCOUNTING_OK),)),
        "paused_accounts": [r["account_id"] for r in clean
                            if r.get("paused")],
        "the_guard_is_the_row": ("activation reads `paused` and "
                                 "`accounting_status` off these rows, so a "
                                 "rename or a relabel changes nothing"),
        "holds_no_balances": True,
    }


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
#
# THE FRESHNESS BASIS VOCABULARY, AS AN ALLOW-LIST AND A DENY-LIST.
#
# THE FALSE PASS THIS CLOSES. The first version asked whether the basis
# token contained "UNESTABLISHED", "NOT_RECORDED" or "UNKNOWN" and counted
# everything else as established. `VENUE_CLOCK_NOT_PROVIDED` -- the token the
# lane writes when the venue sent NO transact time at all -- contains none of
# those substrings, so the check PASSED on the one piece of evidence that
# most clearly means the age was never measured. A substring guess over an
# open vocabulary cannot be right; the tokens are enumerated instead.
#
# The tokens are written by `workers/ext_pinnacle_loop` (see
# `clock["basis"]` and `v_basis`), and a test asserts that this pair of
# tuples still matches the ones that module emits.
FRESHNESS_BASIS_ESTABLISHED = (
    "VENUE_TRANSACT_TIME",
    "VENUE_TRANSACT_TIME_REAGED_AT_THE_DECISION",
    # OUR OWN CLOCK, AND THE ONE WHOSE MEANING IS NOT IN DOUBT. The venue
    # answered after we asked, so the state we received cannot be older than
    # our request-to-response round trip -- whatever `transactTime` denotes.
    # It is a weaker measurement than the venue's stamp and it is HONEST:
    # an upper bound we own, established on every successful read. See the
    # supported-semantics note in `workers/ext_pinnacle_loop`.
    "OUR_REQUEST_RESPONSE_ROUND_TRIP",
)
FRESHNESS_BASIS_UNESTABLISHED = (
    "VENUE_CLOCK_NOT_PROVIDED",
    "VENUE_CLOCK_UNPARSEABLE",
    "NOT_RECORDED",
)


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
    # EACH READ IS JUDGED ON ITS OWN TOKEN **AND** ITS OWN EVIDENCE. A
    # supported established basis with no measured age is not evidence of a
    # measured age, so it does not count as established either.
    bases, ages = {}, 0
    established, unestablished, unrecognised = 0, 0, {}
    established_with_age = 0
    for row in ledger:
        clock = (row or {}).get("venue_clock") or {}
        b = str(clock.get("basis") or (row or {}).get("age_basis")
                or "NOT_RECORDED")
        bases[b] = bases.get(b, 0) + 1
        has_age = clock.get("age_at_read_s") is not None
        if has_age:
            ages += 1
        tok = b.strip().upper()
        if tok in FRESHNESS_BASIS_ESTABLISHED:
            established += 1
            if has_age:
                established_with_age += 1
        elif tok in FRESHNESS_BASIS_UNESTABLISHED:
            unestablished += 1
        else:
            unrecognised[b] = unrecognised.get(b, 0) + 1
    ev = {"cycle_at": cycle.get("at"), "cycle_label": cycle.get("cycle_label"),
          "book_reads_in_the_ledger": len(ledger),
          "reads_with_a_measured_age": ages, "bases": bases,
          "reads_with_a_supported_established_basis": established,
          "reads_established_AND_with_a_measured_age": established_with_age,
          "reads_with_a_supported_unestablished_basis": unestablished,
          "reads_with_an_unrecognised_basis": unrecognised,
          "supported_established_bases": list(FRESHNESS_BASIS_ESTABLISHED),
          "supported_unestablished_bases":
              list(FRESHNESS_BASIS_UNESTABLISHED)}
    if not ledger:
        return _check("venue_book_freshness_basis", None,
                      "the last cycle recorded no book read, so no basis "
                      "was observed", ev)
    if unrecognised:
        # AN UNRECOGNISED TOKEN IS NOT A PASS AND IT IS NOT A FAIL. It is
        # evidence this check does not know how to read, which is exactly
        # what UNKNOWN is for.
        return _check("venue_book_freshness_basis", None,
                      "the last cycle recorded %d book read(s) whose age "
                      "basis is not in this check's vocabulary (%s), so the "
                      "freshness evidence cannot be read"
                      % (sum(unrecognised.values()),
                         ", ".join(sorted(unrecognised))), ev)
    if established and not established_with_age:
        return _check("venue_book_freshness_basis", None,
                      "%d book read(s) name a supported established basis "
                      "and NONE carries a measured age, so the basis is "
                      "claimed and the evidence for it is missing"
                      % established, ev)
    if established_with_age:
        return _check("venue_book_freshness_basis", True,
                      "%d of %d book reads recorded a supported established "
                      "age basis (%s) together with a measured age"
                      % (established_with_age, len(ledger),
                         ", ".join(sorted(b for b in bases
                                          if b.strip().upper()
                                          in FRESHNESS_BASIS_ESTABLISHED))),
                      ev)
    return _check("venue_book_freshness_basis", False,
                  "every book read in the last cycle recorded an "
                  "UNESTABLISHED age basis (%s): what the venue's transact "
                  "time denotes is not established, so the freshness "
                  "refusal stands"
                  % ", ".join(sorted(bases)), ev)


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
    # THE ACTUAL ENUM, NOT A SUBSTRING GUESS.
    #
    # THE FALSE PASS THIS CLOSES. The first version counted a conflict by
    # looking for "CONFLICT" in the verdict string. The settlement module's
    # word for a conflict is INCOMPATIBLE, which contains no such substring,
    # so a window holding one COMPATIBLE and one INCOMPATIBLE row reported
    # "1 compatible and NONE conflicted" and PASSED -- on evidence that
    # included a fixture whose payouts provably differ. The three verdicts
    # are imported from the module that writes them.
    from . import bettor_settlement_terms as ST

    by = {str(r["verdict"]).upper(): int(r["n"]) for r in rows}
    known = {ST.COMPATIBLE, ST.INCOMPATIBLE, ST.UNKNOWN, "NOT_RECORDED"}
    compatible = by.get(ST.COMPATIBLE, 0)
    conflicting = by.get(ST.INCOMPATIBLE, 0)
    unknown = by.get(ST.UNKNOWN, 0) + by.get("NOT_RECORDED", 0)
    unrecognised = {v: n for v, n in by.items() if v not in known}
    ev = {"window_hours": hours, "by_verdict": by, "rows": sum(by.values()),
          "verdict_enum": sorted(known),
          "compatible": compatible, "conflicting": conflicting,
          "unknown": unknown, "unrecognised": unrecognised}
    if not by:
        return _check("settlement_compatibility", None,
                      "no candidate was evaluated in this window, so no "
                      "settlement comparison exists to read", ev)
    if unrecognised:
        # A VERDICT OUTSIDE THE ENUM IS EVIDENCE THIS CHECK CANNOT READ.
        return _check("settlement_compatibility", None,
                      "%d row(s) carry a settlement verdict outside the "
                      "enum (%s), so the comparison cannot be read"
                      % (sum(unrecognised.values()),
                         ", ".join(sorted(unrecognised))), ev)
    if conflicting:
        return _check("settlement_compatibility", False,
                      "%d of %d recent candidates are INCOMPATIBLE -- both "
                      "sides stated a rule and the payouts differ. One "
                      "conflict in the window refuses, whatever else is "
                      "compatible" % (conflicting, ev["rows"]), ev)
    if compatible:
        return _check("settlement_compatibility", True,
                      "%d recent candidate(s) had a stated COMPATIBLE "
                      "settlement rule, %d UNKNOWN, and none INCOMPATIBLE"
                      % (compatible, unknown), ev)
    return _check("settlement_compatibility", False,
                  "of %d recent candidates, none stated a COMPATIBLE rule "
                  "(%d UNKNOWN) -- the comparison is per fixture and UNKNOWN "
                  "refuses" % (ev["rows"], unknown), ev)


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


#: WHAT "WAIVED" ACTUALLY MEANS, ON THE ROW.
#:
#: THE DEFECT THIS CLOSES, of the same family as the other two. The first
#: version counted a waived admission with
#: `risk_verdict::text LIKE '%research_waiver%'`. The lane writes the waiver
#: RECORD on every verdict it persists -- authorised or not, waived or not --
#: so that predicate matched EVERY row, every admission counted as waived,
#: and the check could never be satisfied by anything. It failed closed, so
#: it never granted anything it should not have; it was simply unsatisfiable,
#: and it reported "0 admitted without a waiver" on rows where nothing had
#: been waived at all. The test is now the waiver's own CONTENT: a non-empty
#: `waived` array is a consumed waiver, and nothing else is.
WAIVER_CONSUMED_SQL = """
    coalesce(jsonb_array_length(
        CASE WHEN jsonb_typeof(risk_verdict -> 'research_waiver'
                                            -> 'waived') = 'array'
             THEN risk_verdict -> 'research_waiver' -> 'waived'
             ELSE '[]'::jsonb END), 0) > 0
"""

ADMITTED_SQL = """
    SELECT count(*) FILTER (WHERE admissible)                    AS admitted,
           count(*) FILTER (WHERE admissible AND (%s))           AS waived,
           count(*)                                              AS evaluated
      FROM external_valuations
     WHERE experiment_id = $1
       AND decided_at >= now() - ($2 || ' hours')::interval
""" % (WAIVER_CONSUMED_SQL,)

#: ONE MARKET, ONE COHERENT CHAIN.
#:
#: WHY THIS EXISTS. The four market checks each read their own rows, so
#: "ready" could be assembled from UNRELATED evidence: a freshness basis
#: established on market A, a compatible settlement rule stated for market B,
#: a resolved scope on market C and an admission on market D. Nothing in that
#: is a tradeable opportunity. This selects rows where every link is on THE
#: SAME ROW of `external_valuations` -- which is where the lane records all
#: four, including the venue clock basis and its measured age inside
#: `risk_verdict -> 'freshness_evidence'`.
ELIGIBLE_MARKET_SQL = """
    SELECT us_market_slug, condition_id, sport_family, market, period,
           estimated_edge_per_contract AS edge,
           extract(epoch FROM decided_at)::float8 AS decided_at,
           settlement_comparison->>'verdict'            AS settlement_verdict,
           risk_verdict->'freshness_evidence'->>'venue_age_basis'
                                                        AS venue_age_basis,
           (risk_verdict->'freshness_evidence'->>'venue_age_at_read_s')
                                                        AS venue_age_at_read_s,
           (risk_verdict->'freshness_evidence'->>'venue_age_s') AS venue_age_s
      FROM external_valuations
     WHERE experiment_id = $1
       AND decided_at >= now() - ($2 || ' hours')::interval
       AND admissible IS TRUE
       AND decision = 'BUY'
       AND NOT (%s)
       AND settlement_comparison->>'verdict' = $3
       AND upper(coalesce(period, '')) = ANY($4::text[])
       AND risk_verdict->'freshness_evidence'->>'venue_age_basis'
             = ANY($5::text[])
       AND risk_verdict->'freshness_evidence'->>'venue_age_at_read_s'
             IS NOT NULL
     ORDER BY decided_at DESC
     LIMIT 5
""" % (WAIVER_CONSUMED_SQL,)

#: The scope tokens that mean a whole fixture.
FULL_SCOPE = ("FULL_GAME", "FULL_TIME", "FULL_MATCH")


async def _eligible_market_check(conn, experiment_id, hours) -> dict:
    """IS THERE ONE MARKET WHOSE WHOLE CHAIN HOLDS AT ONCE?

    Every link read off the SAME row: admitted on the lane's own gates with
    no waiver consumed, a COMPATIBLE settlement verdict, a scope that
    resolved to the whole fixture, and a supported established venue clock
    basis WITH the age it measured. If one exists it is NAMED -- slug,
    condition id and each link's value -- because "ready" has to point at
    something.
    """
    from . import bettor_settlement_terms as ST

    ev = {"window_hours": hours,
          "every_link_on_one_row": True,
          "links": ["admissible", "no waiver consumed",
                    "settlement verdict COMPATIBLE",
                    "period in %s" % (list(FULL_SCOPE),),
                    "a supported established venue clock basis",
                    "a measured venue age at the read"],
          "supported_established_bases":
              list(FRESHNESS_BASIS_ESTABLISHED)}
    try:
        rows = [dict(r) for r in await conn.fetch(
            ELIGIBLE_MARKET_SQL, experiment_id, str(hours), ST.COMPATIBLE,
            list(FULL_SCOPE), list(FRESHNESS_BASIS_ESTABLISHED))]
    except Exception as exc:                                   # noqa: BLE001
        return _check("an_eligible_market_with_one_coherent_chain", None,
                      "the lane's own rows could not be read: %s"
                      % type(exc).__name__, ev)
    ev["eligible_markets"] = len(rows)
    if not rows:
        ev["why_none"] = ("no single row carries all of the links above. A "
                          "check that passes on one market and another that "
                          "passes on a different market do not together "
                          "make an eligible market")
        return _check("an_eligible_market_with_one_coherent_chain", False,
                      "no market in this window carries every link of the "
                      "chain on its own row", ev)
    ev["markets"] = rows
    top = rows[0]
    return _check(
        "an_eligible_market_with_one_coherent_chain", True,
        "%d market(s) carry the whole chain; the most recent is %s "
        "(condition %s): settlement %s, scope %s, clock basis %s at age %s s"
        % (len(rows), top.get("us_market_slug"),
           str(top.get("condition_id"))[:18], top.get("settlement_verdict"),
           top.get("period"), top.get("venue_age_basis"),
           top.get("venue_age_at_read_s")), ev)

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

    # 4-6 · the three that used to be constants, now derived from rows.
    #       EACH ONE READS ITS OWN ROWS, so each is a DIAGNOSTIC: it says
    #       whether the lane has ever seen that kind of evidence. None of
    #       them says the pieces belong to the same market.
    checks.append(await _freshness_check(conn))
    checks.append(await _settlement_check(conn, exp, hours))
    checks.append(await _scope_check(conn, exp, hours))

    # 7 · an admitted, unwaived entry of this lane's own
    checks.append(await _admitted_check(conn, exp, hours, qualifying))

    # 8 · AND THE ONE THAT MAKES THE OTHERS MEAN SOMETHING: a single market
    #     carrying every link at once. Without it, "ready" could be assembled
    #     from four unrelated rows, which is not an opportunity.
    checks.append(await _eligible_market_check(conn, exp, hours))

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
        "what_ready_means": (
            "every check met, INCLUDING one market whose whole evidence "
            "chain -- admission with no waiver consumed, a COMPATIBLE "
            "settlement verdict, a whole-fixture scope, and a supported "
            "established venue clock basis with the age it measured -- sits "
            "on ONE row. The four per-kind checks are diagnostics; they do "
            "not establish that the evidence belongs to the same market, "
            "and qualification is never assembled from unrelated rows"),
        "the_eligible_market_check_is":
            "an_eligible_market_with_one_coherent_chain",
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

    # THE READINESS IS COMPUTED FIRST, AND REPORTED ON EVERY PATH.
    #
    # WHY. The first version returned at the earliest failed gate, so a
    # request with nothing bound came back naming only that gate -- and the
    # production readback, which asks this endpoint what still blocks funded
    # activation, printed an EMPTY prerequisite list. The question "what is
    # still missing" must be answered whichever gate stops the request, so
    # the checks are evaluated up front and travel with every refusal.
    ready = await readiness(conn, experiment_id=experiment_id, hours=hours,
                            account_id=account_id)
    out["readiness"] = {k: ready[k] for k in
                        ("ready", "unmet_count", "unknown_count")}
    out["readiness_checks"] = ready["checks"]
    out["unmet"] = [c["check"] for c in ready["unmet"]]

    def _stop(refusal, **extra):
        failed = {"refusal": refusal, "unmet": out["unmet"]}
        failed.update(extra)
        return dict(out, ok=False, applied=None, failed=failed,
                    refusal=refusal)

    # THE ACCOUNT COMES FIRST, because with nothing bound the useful
    # answer is "no account", not "no venue class".
    sel = await account_selection(conn, account_id)
    out["account_selection"] = sel
    if not sel.get("ok"):
        return _stop(sel["refusal"], why=sel.get("why"))

    klass = venue_class(venue)
    out["venue_class"] = klass
    if klass is None:
        return _stop(R_VENUE_UNKNOWN, known=sorted(VENUE_CLASS))

    stored = await _state(conn, LIMITS_KEY) or {}
    proposed = dict(stored.get("proposed") or {})
    missing = [k for k in REQUIRED_LIMITS if proposed.get(k) in (None, "")]
    if missing:
        return _stop(R_LIMITS_MISSING, missing=missing)
    if not stored.get("approved"):
        return _stop(R_LIMITS_NOT_APPROVED,
                     why=("the limit set is recorded but not approved by "
                          "the owner"))
    eff = EX.effective_limits(proposed)
    out["effective_limits"] = eff

    if not ready["ready"]:
        return _stop(R_READINESS_UNMET, detail=ready["unmet"])

    if klass == VENUE_FUNDED:
        # THE FUNDED BRANCH VALIDATES THE RECORD; IT NO LONGER REFUSES BLIND.
        #
        # WHAT WAS WRONG. The branch returned R_OWNER_AUTH whether or not the
        # owner's authorisation existed, so the record could be present and
        # correct and the refusal would not change and would not say why --
        # "requires the owner's written authorisation" while holding it.
        #
        # WHAT IT DOES NOW. It reads the record and checks that it names THIS
        # account, THIS venue and the effective limits now in force. An
        # absent record refuses as before; a record that does not match
        # refuses BY ITS OWN NAME; and a record that matches is accepted --
        # after which the thing that stops a submission is the code
        # constant, which this module cannot move.
        owner = _obj(await _state(conn, OWNER_AUTH_KEY)) or {}
        out["owner_authorization_present"] = bool(owner)
        if not owner:
            return _stop(R_OWNER_AUTH,
                         why=("no owner authorisation record exists. A "
                              "FUNDED venue needs the owner's written "
                              "authorisation, and enabling real submission "
                              "is a code change with that authority behind "
                              "it, not a form"))
        out["owner_authorization"] = {k: owner.get(k) for k in
                                      ("account_id", "venue", "by", "at",
                                       "effective_digest", "statement")}
        if str(owner.get("account_id") or "") != sel["account_id"]:
            return _stop(R_OWNER_AUTH_ACCOUNT,
                         why=("the owner's authorisation names account %r; "
                              "this request is for %r"
                              % (owner.get("account_id"), sel["account_id"])))
        if str(owner.get("venue") or "").upper() != str(venue).upper():
            return _stop(R_OWNER_AUTH_VENUE,
                         why=("the owner's authorisation names venue %r; "
                              "this request is for %r"
                              % (owner.get("venue"), venue)))
        od = str(owner.get("effective_digest") or "")
        if od and od != eff["effective_digest"]:
            return _stop(R_OWNER_AUTH_LIMITS,
                         why=("the owner authorised the limit set digesting "
                              "to %s; the effective set now digests to %s"
                              % (od[:12], eff["effective_digest"][:12])))
        out["owner_authorization_validated"] = True

    # ── THE AUTHORISATION IS RECORDED, THEN IMMEDIATELY CONSUMED ────
    test_only = klass == VENUE_TEST
    granted_at = time.time()
    record = {"account_id": sel["account_id"], "venue": venue,
              "venue_class": klass, "by": by, "at": granted_at,
              # AN AUTHORIZATION WITH NO END is a standing permission
              # nobody remembers granting. The execution gate enforces this.
              "expires_at": granted_at + EX.AUTHORIZATION_TTL_S,
              "ttl_s": EX.AUTHORIZATION_TTL_S,
              "revoked": False,
              "effective_limits": eff["effective"],
              "effective_digest": eff["effective_digest"],
              "readiness_at_authorisation": [c["check"] for c in
                                             ready["checks"]],
              "eligible_market_at_authorisation": next(
                  (c["evidence"].get("markets") for c in ready["checks"]
                   if c["check"] == "an_eligible_market_with_one_coherent_"
                                    "chain" and c["met"] is True), None),
              "funded_submission": "DISABLED",
              "owner_authorization_validated": bool(
                  out.get("owner_authorization_validated")),
              "authorises": ("the operating path against a %s-class venue, "
                             "under the effective limits above" % klass),
              "does_not_authorise": [
                  "real order submission (off in code)",
                  "raising any frozen rail"]}
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        AUTHORIZATION_KEY, json.dumps(record))

    # RECORDING IT IS NOT THE POINT. The execution side is asked, with this
    # record, whether a submission may go out -- so the answer in this
    # response is the EXECUTOR'S, not the panel's. It consumes the record
    # and then refuses on the code constant, which is the whole design.
    consumed = EX.authorize_submission(
        account_id=sel["account_id"], venue=venue, authorization=record,
        approved_limits=proposed)
    out["execution_boundary"] = consumed
    out["authorization_is_consumed_by"] = ("bettor_entry_execution."
                                           "authorize_submission")
    return dict(out, ok=True, applied=record, failed=None,
                verdict=("AUTHORIZED_FOR_A_TEST_VENUE" if test_only
                         else "AUTHORIZED_FOR_A_FUNDED_VENUE_"
                              "SUBMISSION_STILL_DISABLED_IN_CODE"),
                recorded_under=AUTHORIZATION_KEY,
                the_binding_is_untouched=ACCOUNT_KEY,
                submission_would_be=consumed.get("refusal"))


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
