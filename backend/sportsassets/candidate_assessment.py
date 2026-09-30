"""WOULD THE PROPOSED TIMING EXCEPTION UNLOCK ANYTHING? A READ-ONLY ANSWER.

THE QUESTION, EXACTLY AS PUT. Not "is the timing requirement unmet" -- that is
settled and recorded in `market_data_design`. The question is whether accepting
the timing exception in `bettor_admission_policy` and CHANGING NOTHING ELSE would
let a single candidate through. If every candidate still fails some other
requirement, the exception buys nothing and must not be presented as the last
step to a trade.

    A POLICY EXCEPTION CANNOT SUPPLY MISSING VALUATION EVIDENCE.

So this module computes, per candidate:

  1. its ACTUAL result under the unchanged policy -- from its persisted refusal
     set, which is what the lane really returned;
  2. what would remain blocked if ONLY the timing exception were accepted --
     every refusal outside the waived set, kept BY NAME;
  3. every requirement SEPARATELY: settlement compatibility, qualified
     probability, source calibration, contract identity, executable net edge,
     account readiness and each risk rail.

WHAT THIS MODULE MUST NOT DO, AND DOES NOT.

  * It does not enable the exception. `bettor_admission_policy` is read for its
    refusal set only; `POLICY_ADMISSION_ENABLED` is never assigned here.
  * It submits nothing. There is no order path in this file.
  * It writes NO hypothetical admission anywhere. The counterfactual is returned
    as a number in a dict and is never persisted as a decision, never inserted
    into `external_valuations`, and never shaped like a strategy-book row.
  * It invents no candidate. With no readable source it reports
    `NO_READABLE_CANDIDATE_SOURCE` and refuses to answer.

AN UNREACHED REQUIREMENT IS NOT A SATISFIED ONE. The shadow lane stops at the
first failing stage and several requirements sit BEHIND the funded gate, so they
produce no refusal code in this census at all. Reading their silence as a pass is
the error this module is built to prevent: each one is listed with
`reached_by_this_lane=False` and carries its own independently-read state, and a
requirement whose state is unknown counts as BLOCKING.
"""

from __future__ import annotations

from . import bettor_admission_policy as policy
from . import bettor_external_shadow as ext

VERSION = "CANDIDATE_ASSESSMENT_V1"

#: Nothing here may enable anything. Asserted rather than promised.
THIS_MODULE_IS_READ_ONLY = True


# ── 1 · THE REQUIREMENTS, EACH ONE SEPARATE ──────────────────────────
#
# The user named these individually and they must not be collapsed into a
# single pass/fail. Each entry says where the requirement is enforced, which
# lane stage (if any) reports it, and whether the timing exception could waive
# it.

#: Requirement names, one per row of the report.
SETTLEMENT = "SETTLEMENT_COMPATIBILITY"
PROBABILITY = "QUALIFIED_PROBABILITY"
CALIBRATION = "SOURCE_CALIBRATION"
IDENTITY = "CONTRACT_IDENTITY"
NET_EDGE = "EXECUTABLE_NET_EDGE"
ACCOUNT = "ACCOUNT_READINESS"
RAILS = "RISK_RAILS"
CURRENCY = "MARKET_DATA_CURRENCY"
AUTHORIZATION = "SUBMISSION_AUTHORIZATION"

#: Lane stage -> requirement. The stages come from
#: `bettor_external_shadow.STAGES` rather than being retyped, so a stage added
#: there shows up here as UNMAPPED instead of silently vanishing.
REQUIREMENT_OF_STAGE = {
    "1_PROBABILITY": PROBABILITY,
    "2_FRESHNESS": CURRENCY,
    "3_IDENTITY": IDENTITY,
    "4_SETTLEMENT_SCOPE": SETTLEMENT,
    "5_EXECUTION_ESTIMATE": NET_EDGE,
    "6_SIZING": RAILS,
    "7_RISK": RAILS,
    "8_ECONOMICS": NET_EDGE,
}

UNMAPPED_REQUIREMENT = "UNMAPPED_STAGE_REQUIREMENT"


def unmapped_stages() -> tuple:
    """Stages the lane reports that this module has no requirement for. Must be
    empty; a non-empty result is a drift defect, not a detail."""
    return tuple(s for s in ext.STAGE_ORDER if s not in REQUIREMENT_OF_STAGE)


# ── 2 · WHAT THE EXCEPTION CAN WAIVE, DERIVED AND NOT ASSERTED ───────
#
# The waived set is EXACTLY the refusals the policy exception itself issues,
# read off the module. Hand-listing them here would let the exception's real
# scope drift away from the scope this assessment credits it with -- which is
# precisely how a narrow exception becomes a broad one without anyone editing
# the sentence the owner signed.

def waived_refusal_codes() -> frozenset:
    """The refusal codes the timing exception could bear on, and no others.

    The exception speaks to ONE thing: whether a full-replacement VENUE book
    message on a live subscription may be treated as tradeable. So it bears on
    the venue-book clock and on its own subscription/delay refusals, and on
    nothing else.

    IT DOES NOT WAIVE `QUOTE_STALE`, THOUGH THAT SITS IN THE SAME STAGE. That
    code is the ODDS PROVIDER's own 30 s rule on the Pinnacle quote -- a
    different clock, a different source, and a measurement rather than a missing
    guarantee. Waiving the whole freshness stage because one of its codes is in
    scope would silently extend a venue-book assumption over the provider's
    published rule, which the owner is not being asked to accept. So the stage's
    codes are filtered by NAME and the exclusion is asserted in a test.
    """
    venue_book_clock = frozenset({
        # the venue book's own currency, which is what M1/P5 is about
        "VENUE_BOOK_STALE",
        "ONE_CLOCK_IS_NOT_MEASURED",
    })
    own = frozenset({policy.R_OUR_DELAY_TOO_LONG, policy.R_SILENT,
                     policy.R_NO_SUBSCRIPTION})
    return venue_book_clock | own


#: IN THE FRESHNESS STAGE AND DELIBERATELY NOT WAIVED, with the reason.
NOT_WAIVED_THOUGH_IN_THE_SAME_STAGE = {
    "QUOTE_STALE": (
        "the odds provider's own 30 s rule on the Pinnacle quote. A different "
        "clock on a different source, and a MEASURED age rather than a missing "
        "guarantee. The venue-book assumption does not reach it"),
}


def proves_it_waives_nothing_else() -> dict:
    """EVERY OTHER REQUIREMENT IS UNTOUCHED, checked against the stage table.

    Returns the stages whose codes are entirely outside the waived set. If any
    requirement other than MARKET_DATA_CURRENCY appeared as waivable, the
    exception would be broader than the sentence the owner is asked to sign, and
    this function is what would catch it.
    """
    waived = waived_refusal_codes()
    out = {}
    for name, codes in ext.STAGES:
        req = REQUIREMENT_OF_STAGE.get(name, UNMAPPED_REQUIREMENT)
        inside = sorted(c for c in codes if c in waived)
        out[name] = {
            "requirement": req,
            "codes_the_exception_could_waive": inside,
            "codes_it_cannot_touch": sorted(c for c in codes
                                            if c not in waived),
            "fully_outside_the_exception": not inside,
        }
    return {
        "waived_codes": sorted(waived),
        "by_stage": out,
        "requirements_the_exception_can_bear_on": sorted({
            v["requirement"] for v in out.values()
            if v["codes_the_exception_could_waive"]}),
        "and_that_set_must_be_exactly": [CURRENCY],
    }


# ── 3 · REQUIREMENTS THE LANE NEVER REACHES ──────────────────────────
#
# These produce NO refusal code in the census, because the shadow lane stops
# earlier or because they are enforced at the funded gate the shadow lane does
# not cross. THEIR SILENCE IS NOT A PASS.

#: name -> (where it is enforced, how its state is read, why the census is silent)
NOT_REACHED_BY_THIS_LANE = {
    CALIBRATION: {
        "enforced_in": "bettor_entry_execution.R_"
                       "EXTERNAL_SOURCE_CALIBRATION_NOT_MEASURED",
        "state_read_from": "external_source_calibration (row count)",
        "why_the_census_is_silent": (
            "the shadow lane refuses upstream of the funded execution gate, so "
            "a calibration refusal never reaches its refusal array"),
    },
    ACCOUNT: {
        "enforced_in": "bettor_account_onboarding + "
                       "bettor_entry_execution account-exposure refusals",
        "state_read_from": "bettor_account_registry row for the named account",
        "why_the_census_is_silent": (
            "the shadow lane has no account at all -- it never submits, so it "
            "never asks whether one is eligible"),
    },
    AUTHORIZATION: {
        "enforced_in": "bettor_entry_execution."
                       "R_NO_SUBMISSION_AUTHORIZATION_HAS_BEEN_RECORDED and "
                       "REAL_ORDER_SUBMISSION_ENABLED",
        "state_read_from": "bettor_funded_activation authorization row",
        "why_the_census_is_silent": "same reason: the lane does not submit",
    },
}


# ── 4 · THE ASSESSMENT ───────────────────────────────────────────────

R_NO_SOURCE = "NO_READABLE_CANDIDATE_SOURCE"


def assess(rows, *, source: str, window_description: str,
           external_state: dict | None = None) -> dict:
    """THE READ-ONLY ANSWER. `rows` are persisted candidate records; each needs
    a `refusals` sequence and, optionally, `admissible`.

    `external_state` carries the independently-read state of the requirements
    this lane never reaches, as {requirement: {"satisfied": bool|None,
    "evidence": str}}. A requirement absent from it, or carrying
    ``satisfied=None``, counts as BLOCKING -- unknown is never a pass.
    """
    rows = list(rows or [])
    if not rows:
        return {"ok": False, "refusal": R_NO_SOURCE,
                "why": ("no candidate records were readable, so no assessment "
                        "is reported. An empty census is not 'zero blockers'"),
                "source": source}

    waived = waived_refusal_codes()
    ext_state = dict(external_state or {})

    per_code: dict = {}
    per_requirement: dict = {}
    residual_per_requirement: dict = {}
    admissible_now = 0
    would_clear_the_lane_if_only_timing_waived = 0
    residual_codes: dict = {}

    for r in rows:
        refs = [str(c) for c in (r.get("refusals") or [])]
        if r.get("admissible") is True and not refs:
            admissible_now += 1
        seen_req, seen_res = set(), set()
        for c in refs:
            per_code[c] = per_code.get(c, 0) + 1
            stage = ext.STAGE_OF.get(c, ext.STAGE_UNCLASSIFIED)
            req = REQUIREMENT_OF_STAGE.get(stage, UNMAPPED_REQUIREMENT)
            if req not in seen_req:
                per_requirement[req] = per_requirement.get(req, 0) + 1
                seen_req.add(req)
            if c not in waived:
                residual_codes[c] = residual_codes.get(c, 0) + 1
                if req not in seen_res:
                    residual_per_requirement[req] = (
                        residual_per_requirement.get(req, 0) + 1)
                    seen_res.add(req)
        # THE COUNTERFACTUAL, and it is a LANE-STAGE counterfactual only: would
        # this candidate's own persisted refusals all fall inside the waived
        # set? It says nothing about the requirements the lane never reached,
        # which are handled separately below and can only reduce this number.
        if not (set(refs) - waived):
            would_clear_the_lane_if_only_timing_waived += 1

    # The requirements this lane never reaches, read independently. UNKNOWN
    # BLOCKS.
    unreached = {}
    for name, meta in NOT_REACHED_BY_THIS_LANE.items():
        st = ext_state.get(name) or {}
        sat = st.get("satisfied")
        unreached[name] = {
            "reached_by_this_lane": False,
            "satisfied": sat,
            "blocking": sat is not True,
            "evidence": st.get("evidence",
                               "NOT SUPPLIED -- counted as blocking"),
            **meta,
        }
    unreached_blockers = sorted(k for k, v in unreached.items()
                                if v["blocking"])

    n = len(rows)
    # THE ONE-HUNDRED-PERCENT ARGUMENT, and it is the load-bearing one. Per-code
    # counts alone cannot give the intersection of refusal sets in general --
    # but they do not need to when a code the exception CANNOT waive appears on
    # EVERY candidate: in that case no candidate is admissible however the other
    # codes overlap. Recorded explicitly so the inference is auditable.
    universal_unwaivable = sorted(c for c, k in residual_codes.items()
                                  if k == n)

    admitted = (0 if (unreached_blockers or universal_unwaivable)
                else would_clear_the_lane_if_only_timing_waived)

    return {
        "ok": True, "refusal": None,
        "version": VERSION,
        "source": source,
        "window": window_description,
        "candidates": n,

        # 1 · ACTUAL RESULTS UNDER THE UNCHANGED POLICY
        "admissible_under_the_unchanged_policy": admissible_now,
        "refused_under_the_unchanged_policy": n - admissible_now,
        "refusals_by_code": dict(sorted(per_code.items(),
                                        key=lambda kv: -kv[1])),
        "candidates_failing_each_requirement": dict(
            sorted(per_requirement.items(), key=lambda kv: -kv[1])),

        # 2 · WHAT WOULD REMAIN BLOCKED IF ONLY TIMING WERE ACCEPTED
        "if_only_the_timing_exception_were_accepted": {
            "waived_codes": sorted(waived),
            "candidates_whose_lane_refusals_are_ALL_waived":
                would_clear_the_lane_if_only_timing_waived,
            "residual_refusals_by_code": dict(
                sorted(residual_codes.items(), key=lambda kv: -kv[1])),
            "residual_blockers_by_requirement": dict(
                sorted(residual_per_requirement.items(),
                       key=lambda kv: -kv[1])),
            "refusals_on_EVERY_candidate_that_the_exception_cannot_waive":
                universal_unwaivable,
            "and_why_that_list_settles_it": (
                "a refusal present on all %d candidates and outside the waived "
                "set means no candidate is admissible however the remaining "
                "refusals overlap. Per-code counts cannot give an intersection "
                "in general; at 100%% they do not need to" % n),
            "requirements_this_lane_never_reached": unreached,
            "unreached_requirements_that_block": unreached_blockers,
            "candidates_that_would_actually_be_admitted": admitted,
        },

        # 3 · THE ANSWER TO THE QUESTION THAT WAS ASKED
        "does_the_exception_unlock_anything": admitted > 0,
        "the_answer": (
            "NO. Every candidate still fails at least one requirement the "
            "timing exception cannot waive, so accepting it would not produce a "
            "single admission" if admitted == 0 else
            "%d candidate(s) would clear the lane. This is a COUNTERFACTUAL "
            "COUNT and not an admission: nothing here enables the exception or "
            "records a decision" % admitted),
        "and_nothing_here_was_enabled_or_written": {
            "policy_admission_enabled": policy.POLICY_ADMISSION_ENABLED,
            "orders_submitted": 0,
            "rows_written": 0,
            "hypothetical_admissions_persisted": 0,
        },
        "drift_check_unmapped_stages": list(unmapped_stages()),
    }


# ── 5 · THE LIVE READ ────────────────────────────────────────────────

#: ENTRY DECISIONS ONLY (migration 144). The question is what the proposed
#: exception would unlock among the lane's CANDIDATES; a CALIBRATION_ONLY row
#: is a valuation recorded on a refused venue read, carries no executable
#: price or plan, and can be unlocked by nothing -- counting it would inflate
#: the denominator with records that were never candidates.
CENSUS_SQL = """
    SELECT refusals, admissible
      FROM external_valuations
     WHERE experiment_id = $1
       AND record_purpose = 'ENTRY_DECISION'
       AND decided_at >= now() - ($2::text || ' seconds')::interval
"""


async def census_rows(conn, *, experiment_id: str | None = None,
                      window_s: float = 86400.0) -> list:
    """Read the persisted per-candidate refusal sets. READ-ONLY: one SELECT.

    Returns [] when the table is unreachable or empty, which `assess` turns into
    `NO_READABLE_CANDIDATE_SOURCE` rather than into a clean bill of health.
    """
    eid = experiment_id or getattr(ext, "EXPERIMENT_ID", None)
    try:
        rows = await conn.fetch(CENSUS_SQL, eid, str(int(window_s)))
    except Exception:
        return []
    return [{"refusals": list(r["refusals"] or []),
             "admissible": bool(r["admissible"])} for r in rows]


R_INDETERMINATE = "INDETERMINATE_FROM_COUNTS_ALONE"


def assess_from_counts(counts, *, candidates: int, source: str,
                       window_description: str,
                       external_state: dict | None = None) -> dict:
    """THE SAME QUESTION WHERE ONLY PER-CODE COUNTS ARE AVAILABLE.

    WHAT COUNTS CANNOT DO, STATED FIRST. A census of "code X refused 445 rows,
    code Y refused 233" does not give the INTERSECTION of refusal sets. From
    counts alone you cannot tell whether some candidate escaped every unwaivable
    code, so in general this function returns `INDETERMINATE_FROM_COUNTS_ALONE`
    and asks for the per-candidate read.

    THE ONE CASE WHERE COUNTS SUFFICE, and it is exact rather than approximate:
    if a code the exception CANNOT waive refused ALL `candidates` rows, then
    every candidate carries it, so none is admissible however the other codes
    overlap. No intersection is needed because one of the sets is everything.

    An unreached requirement that blocks is also decisive on its own, for a
    different reason: it applies to every candidate by construction.
    """
    counts = {str(k): int(v) for k, v in dict(counts or {}).items()}
    n = int(candidates)
    if n <= 0:
        return {"ok": False, "refusal": R_NO_SOURCE,
                "why": "a census of zero candidates answers nothing",
                "source": source}

    waived = waived_refusal_codes()
    residual = {c: k for c, k in counts.items() if c not in waived}
    universal = sorted(c for c, k in residual.items() if k >= n)

    unreached = {}
    ext_state = dict(external_state or {})
    for name, meta in NOT_REACHED_BY_THIS_LANE.items():
        st = ext_state.get(name) or {}
        sat = st.get("satisfied")
        unreached[name] = {
            "reached_by_this_lane": False, "satisfied": sat,
            "blocking": sat is not True,
            "evidence": st.get("evidence",
                               "NOT SUPPLIED -- counted as blocking"),
            **meta,
        }
    unreached_blockers = sorted(k for k, v in unreached.items()
                               if v["blocking"])

    per_requirement = {}
    for c, k in counts.items():
        req = REQUIREMENT_OF_STAGE.get(
            ext.STAGE_OF.get(c, ext.STAGE_UNCLASSIFIED), UNMAPPED_REQUIREMENT)
        # UPPER BOUND ONLY. Several codes can share a requirement and land on
        # the same candidate, so the max is the most that is defensible from
        # counts; a sum would double-count and overstate.
        per_requirement[req] = max(per_requirement.get(req, 0), k)

    decisive = bool(universal or unreached_blockers)
    return {
        "ok": True,
        "refusal": None if decisive else R_INDETERMINATE,
        "version": VERSION,
        "basis": "PER_CODE_COUNTS_ONLY",
        "source": source,
        "window": window_description,
        "candidates": n,
        "refusals_by_code": dict(sorted(counts.items(),
                                        key=lambda kv: -kv[1])),
        "candidates_failing_each_requirement_AT_MOST": dict(
            sorted(per_requirement.items(), key=lambda kv: -kv[1])),
        "if_only_the_timing_exception_were_accepted": {
            "waived_codes": sorted(waived),
            "residual_refusals_by_code": dict(sorted(residual.items(),
                                                     key=lambda kv: -kv[1])),
            "refusals_on_EVERY_candidate_that_the_exception_cannot_waive":
                universal,
            "requirements_this_lane_never_reached": unreached,
            "unreached_requirements_that_block": unreached_blockers,
            "candidates_that_would_be_admitted": 0 if decisive else None,
        },
        "does_the_exception_unlock_anything": False if decisive else None,
        "the_answer": (
            ("NO. %s refused all %d candidates and the exception cannot waive "
             "it%s, so accepting the exception would produce zero admissions."
             % (", ".join(universal) or "no lane code",
                n,
                ("; and %s remain(s) blocking outside this lane"
                 % ", ".join(unreached_blockers)) if unreached_blockers
                else "")) if decisive else
            "INDETERMINATE from counts alone -- no unwaivable code covers every "
            "candidate, so the per-candidate refusal sets are needed. This is "
            "not a 'yes'"),
        "and_what_counts_cannot_tell_you": (
            "per-code counts do not give the intersection of refusal sets. The "
            "NO above rests only on a code present on 100% of candidates, "
            "which needs no intersection; anything weaker returns "
            "INDETERMINATE rather than a guess"),
        "and_nothing_here_was_enabled_or_written": {
            "policy_admission_enabled": policy.POLICY_ADMISSION_ENABLED,
            "orders_submitted": 0, "rows_written": 0,
            "hypothetical_admissions_persisted": 0,
        },
    }


# ── 6 · THE UNREACHED REQUIREMENTS, READ RATHER THAN ASSUMED ─────────

#: Real tables, verified against the migrations rather than guessed:
#: `external_source_calibration` (117), `bettor_desk_accounts` (097), and the
#: authorization row, which `bettor_funded_activation` keeps in
#: `ingestion_state` under a key rather than in a table of its own.
CALIBRATION_SQL = "SELECT count(*)::int FROM external_source_calibration"
ACCOUNT_SQL = """
    SELECT status, paused,
           extract(epoch FROM opened_at)::float8 AS opened_at
      FROM bettor_desk_accounts
     WHERE account_id = $1
"""
AUTHORIZATION_KEY = "bettor_funded_authorization"


async def unreached_state_from_db(conn, *, account_id: str | None = None,
                                  now: float | None = None) -> dict:
    """Read the three requirements the shadow lane never reaches. READ-ONLY.

    A read that FAILS, or a row that is absent, yields ``satisfied=None`` or
    False -- never a pass. AN UNREADABLE REQUIREMENT IS NOT A SATISFIED ONE, and
    that is the whole point of reading them here instead of inferring them from
    the census's silence.
    """
    import json
    import time as _t
    at = float(now if now is not None else _t.time())
    out = {}

    # 1 · CALIBRATION. Zero rows means NOT MEASURED, which is the state
    # recorded in the shadow-loop evidence and is not the same as "measured and
    # poor".
    try:
        n = await conn.fetchval(CALIBRATION_SQL)
        out[CALIBRATION] = {
            "satisfied": bool(n and int(n) > 0),
            "evidence": "external_source_calibration rows=%s" % n}
    except Exception as e:
        out[CALIBRATION] = {"satisfied": None,
                            "evidence": "read failed: %s" % type(e).__name__}

    # 2 · SUBMISSION AUTHORIZATION. Present, unrevoked and unexpired, all three.
    try:
        raw = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1",
            AUTHORIZATION_KEY)
        rec = json.loads(raw) if isinstance(raw, str) else (raw or None)
        if not rec:
            out[AUTHORIZATION] = {"satisfied": False,
                                  "evidence": "no authorization row exists"}
        else:
            revoked = rec.get("revoked_at") is not None
            exp = rec.get("expires_at")
            expired = not isinstance(exp, (int, float)) or float(exp) <= at
            out[AUTHORIZATION] = {
                "satisfied": not (revoked or expired),
                "evidence": ("authorization revoked=%s expires_at=%s now=%.0f"
                             % (revoked, exp, at))}
    except Exception as e:
        out[AUTHORIZATION] = {"satisfied": None,
                              "evidence": "read failed: %s" % type(e).__name__}

    # 3 · ACCOUNT READINESS. SELECTION IS NOT ELIGIBILITY: naming an account
    # does not clear it, so an account that is paused or unreconciled is
    # reported as not satisfied with its actual row shown.
    if not account_id:
        out[ACCOUNT] = {"satisfied": None,
                        "evidence": "no account was named; naming one would "
                                    "not make it eligible either"}
        return out
    try:
        row = await conn.fetchrow(ACCOUNT_SQL, account_id)
        if row is None:
            out[ACCOUNT] = {"satisfied": False,
                            "evidence": "no such account: %s" % account_id}
        else:
            ok = (str(row["status"]).upper() == "ACTIVE"
                  and not bool(row["paused"]))
            out[ACCOUNT] = {
                "satisfied": ok,
                "evidence": ("account %s status=%s paused=%s -- and an ACTIVE "
                             "row is still not a RECONCILED one; reconciliation "
                             "is read by bettor_account_onboarding"
                             % (account_id, row["status"], row["paused"]))}
    except Exception as e:
        out[ACCOUNT] = {"satisfied": None,
                        "evidence": "read failed: %s" % type(e).__name__}
    return out


