"""THE PAPER AGENTS' LEARNING RECORD: PROVENANCE, THE LINKED CHAIN, AUDREY'S
EVENT AUDITS, LESSONS IN EACH AGENT'S MEMORY, AND IMPROVEMENT PROPOSALS.

PAPER ONLY. Real-money execution stays disabled. Nothing here places,
cancels or modifies an order or reads a funded table. It RECORDS what the
paper book's own forward records show and reads it back for management
(migration 185); and ONE bounded paper-policy change -- the completed-game
policy's minimum gross edge -- can become the running paper agent's ACTIVE
parameter version, only through the explicit, audited `activate_proposal`
of an evaluated PASS, with atomic `rollback_policy_parameters` (186).

WHAT IT KEEPS, AND WHERE
  1 · DECISION PROVENANCE (`paper_decisions.provenance`, going forward).
      `decision_provenance` builds it in the same INSERT as the decision:
      versions (code, runtime, policy, model, simulator, Pinnacle source),
      the valuation inputs AS READ at the decision instant with their
      SHA-256, the session config SHA, prices, fees, expected figures, the
      alternatives considered and a plain explanation. `decision_record_
      audit` states, field by field, what a decision record retains; a row
      recorded before 185 reads NOT_RECORDED_BEFORE_MIGRATION_185 (it is
      never rewritten or reconstructed).
  2 · THE LINKED CHAIN (`fill_chain` / `group_chain`): Derek's decision ->
      the entry order -> its fill(s) -> each ledger cash debit -> Xavier's
      handoff -> his reviews and actions -> the exit or settlement -> the
      ledger result -> Audrey's audit findings, each with ids and
      timestamps. EVERY ABSENT LINK IS LISTED AS MISSING with its reason --
      a still-open position's settlement is MISSING and `pending`, a fill
      with no ledger entry is MISSING and a `defect`; nothing is omitted.
  3 · AUDREY AUDITS MEANINGFUL EVENTS AS THEY HAPPEN (`step_audit_events`,
      a step of the scheduled paper pass after her monitor and daily
      report): the first fill of every entry, every handoff, every
      management fill, every settlement (SETTLED_AT_VENUE_PRICE separately),
      every exceptional outcome (a void, a venue-price settlement, a
      correction) and every ledger inconsistency -- ONE finding per event
      (`paper_audrey_findings`, deterministic id, looked up per account), so
      repeated passes and restarts never audit an event twice. A WARNING or
      CRITICAL event opens a task through her existing improvement hook.
  4 · LESSONS, EACH AGENT'S PERSISTENT MEMORY (`paper_agent_lessons`),
      derived ONLY from forward records: Derek's refusal funnel, his settled
      entries against what he expected at decision, his simulated fills
      against the optimistic bound, exceptional settlements against the
      ordinary-completion assumption; Xavier's management outcomes; Audrey's
      chain and audit coverage. Each lesson names the records that produced
      it (selection, count, SHA-256 of every id, a sample of ids). An
      actionable lesson opens (once per series) an improvement task in
      `agent_tasks` (kind PAPER_LEARNING_TASK) -- never an IMPROVEMENT
      candidate, so the funded improvement workflow never picks it up.
  5 · IMPROVEMENT PROPOSALS (`paper_improvement_proposals`): a proposed
      change, a protocol whose TRAINING period ends strictly before its
      later EVALUATION period begins (and the evaluation period begins no
      earlier than the proposal -- forward outcomes only; the database
      enforces both), the evaluation when the period has ended and enough
      forward outcomes exist (otherwise INSUFFICIENT_FORWARD_DATA with the
      counts), and whether it is ACTIVE. Activation is NEVER automatic: it
      needs a PASS, a named human approver and the explicit control row
      PAPER_LEARNING_PROPOSAL_ACTIVATION (inserted disabled); the scope is
      PAPER_ONLY by CHECK. A retrospective result for an alternative that
      was never executed is labelled COUNTERFACTUAL_NOT_EXECUTABLE_PROOF and
      is never described as something the agent could have executed.
  6 · THE MANAGEMENT READ (`learning_summary`): per agent, what was learned,
      the proposed change, its evaluation, active or not. Every section is
      {"status": OK | EMPTY | UNAVAILABLE, "why", "data"}: a failed read is
      UNAVAILABLE with its reason, never a zero.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import hashlib
import json
import time
from typing import Any

from .. import bettor_paper_ledger as L

VERSION = "PAPER_LEARNING_V1"
RECORD_VERSION = "PAPER_DECISION_RECORD_V2"
DEREK, XAVIER, AUDREY = "DEREK", "XAVIER", "AUDREY"
AGENT_IDS = (DEREK, XAVIER, AUDREY)

#: COUNTERFACTUAL LABEL: a figure for an alternative that was never executed
#: (an order never placed, an exit never taken). It is what the recorded
#: book and the settled outcome imply -- NOT proof the order would have
#: filled, and never described as something the agent could have executed.
COUNTERFACTUAL = "COUNTERFACTUAL_NOT_EXECUTABLE_PROOF"
NOT_BEFORE_185 = "NOT_RECORDED_BEFORE_MIGRATION_185"
FORWARD_ONLY = "FORWARD_RECORDS_ONLY"

# ── LINK STATUSES ─────────────────────────────────────────────────────
PRESENT = "PRESENT"
MISSING = "MISSING"
INCONSISTENT = "INCONSISTENT"
NOT_APPLICABLE = "NOT_APPLICABLE"

# ── AUDREY'S EVENT KINDS (paper_audrey_findings.kind) ─────────────────
EV_FIRST_FILL = "PAPER_EVENT_FIRST_FILL"
EV_HANDOFF = "PAPER_EVENT_HANDOFF"
EV_MGMT_FILL = "PAPER_EVENT_MANAGEMENT_FILL"
EV_SETTLEMENT = "PAPER_EVENT_SETTLEMENT"
EV_VENUE_PRICE = "PAPER_EVENT_SETTLED_AT_VENUE_PRICE"
EV_EXCEPTIONAL = "PAPER_EVENT_EXCEPTIONAL_OUTCOME"
EV_LEDGER = "PAPER_EVENT_LEDGER_INCONSISTENCY"
EVENT_KINDS = (EV_FIRST_FILL, EV_HANDOFF, EV_MGMT_FILL, EV_SETTLEMENT,
               EV_VENUE_PRICE, EV_EXCEPTIONAL, EV_LEDGER)
EVENTS_PER_KIND_PER_PASS = 200
CASH_TOLERANCE_USD = 0.005

# ── LESSONS ───────────────────────────────────────────────────────────
TASK_KIND = "PAPER_LEARNING_TASK"
LEARNING_EVERY_S = 3600.0
LESSON_WINDOW_S = 30 * 86400.0
WATERMARK_KEY = "paper_learning_last_run:%s"
L_REFUSALS = "REFUSAL_FUNNEL"
L_SETTLED = "SETTLED_ENTRIES_VS_DECISION_EXPECTATION"
L_FILLS = "SIMULATED_FILLS_VS_OPTIMISTIC_BOUND"
L_EXCEPTIONAL = "EXCEPTIONAL_SETTLEMENTS_VS_ORDINARY_COMPLETION"
L_MANAGEMENT = "MANAGEMENT_OUTCOMES"
L_COVERAGE = "CHAIN_AND_AUDIT_COVERAGE"
MIN_FUNNEL_DECISIONS = 20
NEAR_MISS_PP = 1.0
NEAR_MISS_FOR_PROPOSAL = 5
STALE_EXIT_FOR_PROPOSAL = 3
CHAINS_PER_COVERAGE_LESSON = 200

# ── PROPOSALS ─────────────────────────────────────────────────────────
C_EDGE = "DEREK_ENTRY_EDGE_THRESHOLD"
#: THE ONE SUPPORTED, BOUNDED PAPER-POLICY CHANGE (migration 186): the
#: completed-game paper policy's minimum gross edge. The same whitelist and
#: bounds as paper_benchmark.CG_PARAMETER_BOUNDS and the migration's CHECK.
PARAM_POLICY = "PINNACLE_COMPLETED_GAME_PAPER"
PARAM_DEFAULTS = {"min_gross_edge_pp": 0.5}
#: THE 0.5 pp FLOOR IS AN OWNER MANDATE (2026-10-01, migration 188, replacing
#: the earlier 5.0 pp floor): the threshold may be TIGHTENED (to at most
#: 6.0 pp) by an evaluated, approved proposal and returned towards 0.5,
#: never set below it. A lower threshold needs a SEPARATE OWNER DECISION and
#: a new migration; no proposal, activation or rollback here can produce one.
PARAM_BOUNDS = {"min_gross_edge_pp": (0.5, 6.0)}
PARAM_GRID_PP = 0.5
PARAM_MAX_STEP_PP = 1.0
PARAM_FLOOR_WHY = (
    "0.5 pp: an OWNER MANDATE (2026-10-01) -- the shipped threshold is the "
    "floor; an approved proposal may only tighten it (ceiling 6.0 pp, "
    "0.5 pp grid, at most 1 pp per activation). Going below 0.5 pp needs a "
    "separate owner decision and a new migration. Context: at this edge the "
    "simulator's fee (up to ~1.75 pp per contract at mid prices) usually "
    "exceeds the gross edge, so the after-fee check binds. Further context "
    "decision: fees up to ~1.75 pp per contract at mid prices; EV "
    "conditional on ordinary completion with exceptional-settlement "
    "frequency unmeasured; venue book currency not established (P5); "
    "de-vigged reference error.")
C_STALE_EXIT = "XAVIER_EXIT_WHEN_MEASURE_STALE"
EVALUATOR = "EVALUATOR:PAPER_FORWARD_OUTCOMES"
ACTIVATION_CONTROL_KEY = "PAPER_LEARNING_PROPOSAL_ACTIVATION"
S_AWAITING = "AWAITING_FORWARD_DATA"
S_INSUFFICIENT = "INSUFFICIENT_FORWARD_DATA"
S_EVALUATED = "EVALUATED"
V_PASS, V_NO_GAIN, V_HARM = "PASS", "FAIL_NO_IMPROVEMENT", "FAIL_HARM"
PROPOSAL_EPSILON_S = 0.001
PROPOSAL_COOLDOWN_S = 7 * 86400.0
CHANGE_CLASSES = {
    C_EDGE: {"agent": DEREK, "evaluation_days": 7.0,
             "min_evaluation_outcomes": 10,
             "metric": ("mean per-contract result, after the per-contract "
                        "fee, of the evaluation-period decisions refused "
                        "ONLY for the edge threshold that the proposed "
                        "threshold would admit, at the decision-time best "
                        "displayed level, on their settled outcome"),
             "pass_rule": "metric > 0 on at least min_evaluation_outcomes"},
    C_STALE_EXIT: {"agent": XAVIER, "evaluation_days": 7.0,
                   "min_evaluation_outcomes": 5,
                   "metric": ("mean (full exit at the first evaluation-"
                              "period review whose measure was stale, at "
                              "that review's displayed exit walk) minus the "
                              "realized result, over positions closed by "
                              "the evaluation"),
                   "pass_rule": "metric > 0 on at least "
                                "min_evaluation_outcomes"}}

# ── REFUSALS ──────────────────────────────────────────────────────────
R_NO_FILL = "NO_SUCH_PAPER_FILL"
R_NO_GROUP = "NO_RECORDS_FOR_THIS_PAPER_GROUP"
R_OVERLAP = "TRAINING_AND_EVALUATION_PERIODS_OVERLAP"
R_EMPTY_PERIOD = "A_PERIOD_IS_EMPTY_OR_INVERTED"
R_TRAIN_AFTER_PROPOSAL = "THE_TRAINING_PERIOD_ENDS_AFTER_THE_PROPOSAL"
R_PEEK = "THE_EVALUATION_PERIOD_STARTS_BEFORE_THE_PROPOSAL"
R_UNKNOWN_CLASS = "NOT_A_REGISTERED_PAPER_CHANGE_CLASS"
R_WRONG_AGENT = "THE_CHANGE_CLASS_BELONGS_TO_ANOTHER_AGENT"
R_OPEN_EXISTS = "AN_OPEN_PROPOSAL_ALREADY_EXISTS_FOR_THIS_CHANGE"
R_NO_PROPOSAL = "NO_SUCH_PROPOSAL"
R_ACTIVATION_OFF = "THE_PAPER_PROPOSAL_ACTIVATION_CONTROL_IS_OFF"
R_NO_APPROVER = "AN_ACTIVATION_NAMES_ITS_HUMAN_APPROVER"
R_AGENT_APPROVER = "AN_AGENT_OR_EVALUATOR_CANNOT_ACTIVATE"
R_SELF_APPROVER = "THE_PROPOSER_CANNOT_ACTIVATE_ITS_OWN_PROPOSAL"
R_NOT_PASSED = "ONLY_AN_EVALUATED_PASS_CAN_BE_ACTIVATED"
R_PERIOD_OPEN = "EVALUATION_PERIOD_NOT_ENDED"
R_TOO_FEW = "TOO_FEW_FORWARD_OUTCOMES"
R_CHANGE_NOT_SUPPORTED = "ONLY_THE_COMPLETED_GAME_POLICY_EDGE_IS_SUPPORTED"
R_NOT_WHITELISTED = "THE_PARAMETER_IS_NOT_WHITELISTED"
R_OUT_OF_BOUNDS = "THE_PARAMETER_VALUE_IS_OUT_OF_BOUNDS_OR_OFF_GRID"
R_STEP_TOO_LARGE = "THE_CHANGE_EXCEEDS_THE_MAXIMUM_STEP_OR_IS_EMPTY"
R_ACTIVATION_CHECKS = "THE_ACTIVATION_CHECKS_DID_NOT_PASS"
R_ALREADY_ACTIVE = "THE_PROPOSAL_IS_ALREADY_ACTIVE"
R_NO_PARAMETER_HEAD = "NO_ACTIVE_PARAMETER_VERSION_ROW"
R_NOTHING_TO_ROLL_BACK = "THE_ACTIVE_VERSION_HAS_NO_PREDECESSOR"
R_ROLLBACK_NEEDS_REASON = "A_ROLLBACK_STATES_ITS_REASON"
R_NOT_AN_APPROVED_CONFIGURATION = "ROLLBACK_RESTORES_ONLY_AN_APPROVED_VERSION"

_JSON_COLS = frozenset((
    "label", "internal_model", "pinnacle", "book", "economics",
    "qualification_gaps", "policy_decision", "alternatives", "optimistic",
    "provenance", "queue_basis", "evidence", "detail", "selection",
    "exposure", "standing", "confirmed_protection", "incomplete_search",
    "exceptional", "measure", "action", "metrics", "proposed_change",
    "protocol", "last_attempt", "evaluation", "spec", "bids",
    "offers", "tick", "config"))


# ═════════════════════════════════════════════════════════════════════
# 0 · SMALL HELPERS
# ═════════════════════════════════════════════════════════════════════

def _h(*parts) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()
                          ).hexdigest()[:24]


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode()).hexdigest()


def _plain(v):
    if isinstance(v, _dt.datetime):
        return float(v.timestamp())
    if isinstance(v, _dt.date):
        return str(v)
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v


def _rec(r) -> dict | None:
    """A record as plain values: timestamps as epoch seconds, numerics as
    floats, jsonb parsed."""
    if r is None:
        return None
    out = {}
    for k, v in dict(r).items():
        if k in _JSON_COLS and isinstance(v, str):
            try:
                out[k] = json.loads(v)
            except ValueError:
                out[k] = v          # a text column of the same name
        else:
            out[k] = _plain(v)
    return out


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _section(data=None, *, why_empty: str | None = None,
             error: Exception | None = None, empty: bool | None = None
             ) -> dict:
    if error is not None:
        return {"status": "UNAVAILABLE",
                "why": "%s: %s" % (type(error).__name__, str(error)[:160]),
                "data": None}
    is_empty = (not data) if empty is None else empty
    if is_empty:
        return {"status": "EMPTY", "why": why_empty, "data": data}
    return {"status": "OK", "why": None, "data": data}


async def _safe(coro, *, why_empty: str, empty=None) -> dict:
    try:
        data = await coro
    except Exception as exc:                                    # noqa: BLE001
        return _section(error=exc)
    return _section(data, why_empty=why_empty,
                    empty=None if empty is None else empty(data))


async def has_schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT to_regclass('paper_agent_lessons') IS NOT NULL "
            "   AND to_regclass('paper_improvement_proposals') IS NOT NULL"))
    except Exception:                                           # noqa: BLE001
        return False


# ═════════════════════════════════════════════════════════════════════
# 1 · DECISION PROVENANCE (pure) AND THE DECISION RECORD AUDIT
# ═════════════════════════════════════════════════════════════════════

#: The valuation fields a paper decision is formed from, kept AS READ.
VALUATION_INPUT_KEYS = (
    "id", "experiment_id", "version", "source_class", "provider", "book",
    "devig_method", "venue", "condition_id", "us_market_slug",
    "contract_selection", "sport_family", "market", "period", "observed_at",
    "received_at", "probability", "decision", "admissible", "refusals",
    "payout_event", "payout_is_complement", "buy_intent", "ladder_side",
    "record_purpose", "decided_at", "event_key")


def valuation_snapshot(row: dict | None) -> dict:
    """THE INPUTS AS READ: the decision-relevant valuation fields, the
    settlement comparison's verdict, and the SHA-256 of the whole row (so a
    later change to the stored row is detectable)."""
    row = dict(row or {})
    snap = {k: _plain(row.get(k)) for k in VALUATION_INPUT_KEYS if k in row}
    if isinstance(snap.get("refusals"), (list, tuple)):
        snap["refusals"] = list(snap["refusals"])
    sc = L._j(row.get("settlement_comparison")) or {}
    snap["settlement_comparison"] = {
        "compatibility": sc.get("compatibility"),
        "venue_rules_text_recorded": bool(sc.get("venue_rules_text")),
        "sha256": _sha(sc) if sc else None}
    whole = {k: (L._j(v) if isinstance(v, str) and k in (
        "settlement_comparison", "raw_odds", "calibration_only_evidence")
        else _plain(v)) for k, v in row.items()}
    return {"valuation": snap, "inputs_sha256": _sha(whole),
            "inputs_sha256_basis": ("SHA-256 of the full external_valuations "
                                    "row as read at the decision instant "
                                    "(sorted JSON; timestamps as epoch "
                                    "seconds)")}


def explanation(*, strategy: str, policy_version: str, verdict: str,
                refusals: list, policy_decision: dict | None,
                qty=None, limit_price=None, pinnacle: dict | None = None,
                alternatives: dict | None = None) -> str:
    """ONE PLAIN ACCOUNT OF THE DECISION, from the stored fields only."""
    pd = dict(policy_decision or {})

    def n(v, fmt="%.4f"):
        return "n/a" if v is None else fmt % float(v)
    p = (pinnacle or {}).get("p")
    head = "%s under %s (%s)" % (verdict, policy_version, strategy)
    figures = ("p_pinnacle %s, p_internal %s, best-level gross edge %s pp, "
               "fees $%s, expected net $%s" % (
                   n(p), n(pd.get("p_internal")),
                   n(pd.get("gross_edge_pp"), "%.2f"),
                   n(pd.get("fees_usd"), "%.2f"),
                   n(pd.get("net_expected_profit_usd"), "%.2f")))
    if pd.get("economics_label"):
        figures += " (%s)" % pd["economics_label"]
    alts = []
    for k, v in (alternatives or {}).items():
        if not isinstance(v, dict):
            continue
        if k == "NO_TRADE":
            alts.append("NO_TRADE ($0 expected)")
        elif v.get("status"):
            alts.append("%s (%s)" % (k, v["status"]))
        else:
            alts.append("%s (gross edge %s pp at %s)" % (
                k, n(v.get("gross_edge_pp"), "%.4f"),
                n(v.get("best_price"), "%.2f")))
    if verdict == "ENTER":
        act = "buy %s contracts, limit %s" % (n(qty, "%.0f"),
                                              n(limit_price, "%.2f"))
    else:
        act = "refused %s%s" % (
            refusals[0] if refusals else "UNSPECIFIED",
            "" if len(refusals or []) < 2 else
            " (also: %s)" % ", ".join(refusals[1:6]))
        sh = pd.get("shortfall") or {}
        if sh.get("edge_shortfall_pp"):
            act += "; edge short by %s pp" % n(sh["edge_shortfall_pp"],
                                                "%.2f")
    return "%s: %s. %s. Alternatives considered: %s." % (
        head, act, figures, "; ".join(alts) or "none recorded")


def decision_provenance(*, strategy: str, code_version: str,
                        policy_version: str, row: dict | None,
                        session: dict | None, verdict: str, refusals: list,
                        policy_decision: dict | None = None,
                        internal_model: dict | None = None,
                        pinnacle: dict | None = None,
                        book: dict | None = None, limit_price=None,
                        qty=None, alternatives: dict | None = None,
                        optimistic: dict | None = None,
                        simulator_version: str | None = None,
                        decided_via: str | None = None,
                        at: float | None = None) -> dict:
    """WHAT THE DECISION RECORD RETAINS BEYOND ITS COLUMNS (pure)."""
    from . import paper_runtime as _PR
    pd = dict(policy_decision or {})
    im = dict(internal_model or {})
    levels = (book or {}).get("levels") or []
    model = ({"available": False, "reason": im.get("reason")
              or "NO_INTERNAL_MODEL_BY_DESIGN"}
             if im.get("available") is False else
             {"available": im.get("model_id") is not None,
              "model_id": im.get("model_id"),
              "model_version": im.get("model_version"),
              "approval_status": im.get("approval_status"),
              "label": im.get("label"),
              "provenance_verified": im.get("provenance_verified")})
    return {
        "record_version": RECORD_VERSION,
        "recorded_by": VERSION,
        "decided_at": at, "decided_via": decided_via,
        "versions": {"code": code_version, "runtime": _PR.VERSION,
                     "policy": policy_version, "strategy": strategy,
                     "model": model, "simulator": simulator_version,
                     "pinnacle_source": (pinnacle or {}).get(
                         "source_version"),
                     "parameters": pd.get("parameters")},
        "inputs": dict(valuation_snapshot(row),
                       pinnacle={k: (pinnacle or {}).get(k) for k in (
                           "p", "at", "age_s", "limit_s", "qualification",
                           "refusal", "method", "overround")},
                       book_obs_id=(book or {}).get("book_obs_id"),
                       book_observed_at=(book or {}).get("observed_at"),
                       session_id=(session or {}).get("session_id"),
                       session_config_sha=(session or {}).get(
                           "config_sha")),
        "decision": {"verdict": verdict,
                     "refusal": refusals[0] if refusals else None,
                     "refusals": list(refusals or [])},
        "alternatives_considered": sorted((alternatives or {}).keys()),
        "prices": {"limit_price": _f(limit_price),
                   "proposed_qty": _f(qty),
                   "best_level_price": (levels[0].get("price")
                                        if levels else None),
                   "levels_recorded": len(levels),
                   "optimistic_vwap": (optimistic or {}).get("vwap")},
        "fees": {"fees_usd": pd.get("fees_usd"),
                 "basis": ("the policy decision's fees on the walked "
                           "levels" if pd.get("fees_usd") is not None
                           else "NOT_COMPUTED: refused before the "
                                "economics")},
        "expected": {"net_expected_profit_usd":
                     pd.get("net_expected_profit_usd"),
                     "gross_edge_pp": pd.get("gross_edge_pp"),
                     "economics_label": pd.get("economics_label"),
                     "basis": "what was known at the decision instant"},
        "explanation": explanation(
            strategy=strategy, policy_version=policy_version,
            verdict=verdict, refusals=list(refusals or []),
            policy_decision=pd, qty=qty, limit_price=limit_price,
            pinnacle=pinnacle, alternatives=alternatives)}


def safe_provenance(**kw) -> dict:
    """`decision_provenance`, never raising: a failure to build it never
    stops the decision from being recorded (the failure is recorded)."""
    try:
        return decision_provenance(**kw)
    except Exception as exc:                                    # noqa: BLE001
        return {"record_version": RECORD_VERSION, "recorded_by": VERSION,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}


def decision_record_audit(d: dict | None) -> dict:
    """WHAT ONE DECISION RECORD RETAINS, field by field: PRESENT, MISSING,
    NOT_APPLICABLE (e.g. no book for a candidate refused before the read),
    or NOT_RECORDED_BEFORE_MIGRATION_185. Pure."""
    if not d:
        return {"complete": False, "fields": {}, "why": "NO_DECISION_RECORD"}
    prov = L._j(d.get("provenance"))
    enter = d.get("verdict") == "ENTER"
    pd = L._j(d.get("policy_decision")) or {}
    im = L._j(d.get("internal_model")) or {}
    fields = {}
    fields["inputs"] = (PRESENT if d.get("valuation_id") is not None
                        and L._j(d.get("pinnacle")) else MISSING)
    fields["inputs_snapshot"] = (
        NOT_BEFORE_185 if prov is None else
        PRESENT if (prov.get("inputs") or {}).get("inputs_sha256")
        else MISSING)
    fields["policy_version"] = PRESENT if d.get("policy_version") \
        else MISSING
    fields["model_version"] = (
        PRESENT if (im.get("available") is False or im.get("model_version")
                    or im.get("refusal") or im.get("model_id")) else MISSING)
    fields["decision"] = (PRESENT if d.get("verdict") in ("ENTER", "REFUSE")
                          and (enter == (d.get("refusal") is None))
                          else MISSING)
    fields["alternatives"] = PRESENT if L._j(d.get("alternatives")) \
        is not None else MISSING
    if enter:
        fields["prices"] = (PRESENT if d.get("limit_price") is not None
                            and d.get("proposed_qty") is not None
                            and L._j(d.get("book")) else MISSING)
        fields["fees"] = PRESENT if pd.get("fees_usd") is not None \
            else MISSING
    else:
        fields["prices"] = (PRESENT if L._j(d.get("book")) else
                            NOT_APPLICABLE if d.get("book_obs_id") is None
                            else MISSING)
        fields["fees"] = (PRESENT if pd.get("fees_usd") is not None
                          else NOT_APPLICABLE)
    fields["explanation"] = (
        NOT_BEFORE_185 if prov is None else
        PRESENT if prov.get("explanation") else MISSING)
    fields["versions"] = (
        NOT_BEFORE_185 if prov is None else
        PRESENT if (prov.get("versions") or {}).get("code") else MISSING)
    gaps = sorted(k for k, v in fields.items()
                  if v not in (PRESENT, NOT_APPLICABLE))
    return {"complete": not gaps, "fields": fields, "gaps": gaps,
            "provenance": ("RECORDED" if prov is not None
                           else NOT_BEFORE_185),
            "provenance_error": (prov or {}).get("error"),
            "explanation": (prov or {}).get("explanation"),
            "never_rewritten": ("a record made before migration 185 keeps "
                                "what it recorded; nothing is back-filled")}


# ═════════════════════════════════════════════════════════════════════
# 2 · THE LINKED CHAIN FOR ONE SIMULATED FILL
# ═════════════════════════════════════════════════════════════════════

def _link(name, status, *, ids=None, at=None, why=None, pending=False,
          detail=None) -> dict:
    return {"link": name, "status": status, "ids": list(ids or []),
            "at": at, "why": why, "pending": bool(pending),
            "defect": status in (MISSING, INCONSISTENT) and not pending,
            "detail": detail or {}}


def _expected_cash(fill: dict) -> float:
    g, fe = float(fill["gross_usd"]), float(fill["fee_usd"])
    return -(g + fe) if fill["direction"] == "BUY" else g - fe


async def fill_chain(conn, fill_id: str) -> dict:
    """THE COMPLETE LINKED CHAIN OF ONE SIMULATED FILL (read only)."""
    f = await conn.fetchrow("SELECT group_id FROM paper_fills "
                            " WHERE fill_id=$1", fill_id)
    if f is None:
        return {"found": False, "fill_id": fill_id, "refusal": R_NO_FILL}
    return await group_chain(conn, f["group_id"], fill_id=fill_id)


async def _findings_for(conn, account_id: str, subjects: list) -> list:
    return [_rec(r) for r in await conn.fetch(
        "SELECT finding_id, session_id, found_at, kind, severity, subject, "
        "       detail, improvement_task_id FROM paper_audrey_findings "
        " WHERE account_id=$1 AND subject = ANY($2::text[]) "
        " ORDER BY found_at, finding_id", account_id,
        [s for s in subjects if s])]


async def group_chain(conn, group_id: str, *, fill_id: str | None = None
                      ) -> dict:
    """THE CHAIN OF ONE PAPER GROUP (position), every link with ids and
    timestamps; an absent link is MISSING with its reason."""
    orders = [_rec(r) for r in await conn.fetch(
        "SELECT * FROM paper_orders WHERE group_id=$1 "
        " ORDER BY created_at, order_id", group_id)]
    fills = [_rec(r) for r in await conn.fetch(
        "SELECT * FROM paper_fills WHERE group_id=$1 "
        " ORDER BY filled_at, fill_id", group_id)]
    if not orders and not fills:
        return {"found": False, "group_id": group_id, "refusal": R_NO_GROUP}
    entry = next((o for o in orders if o["role"] == "ENTRY"), None)
    acct = (entry or orders[0] if orders else fills[0])["account_id"]
    order_ids = [o["order_id"] for o in orders]
    ledger = [_rec(r) for r in await conn.fetch(
        "SELECT * FROM paper_ledger WHERE account_id=$1 AND (group_id=$2 "
        "    OR order_id = ANY($3::text[])) ORDER BY seq", acct, group_id,
        order_ids)]
    decision = None
    if entry is not None and entry.get("decision_id"):
        decision = _rec(await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE decision_id=$1",
            entry["decision_id"]))
    handoff = _rec(await conn.fetchrow(
        "SELECT * FROM paper_handoffs WHERE group_id=$1", group_id))
    reviews = [_rec(r) for r in await conn.fetch(
        "SELECT review_id, reviewed_at, trigger, recommendation, refusal, "
        "       selection, measure, action, exceptional, strategy "
        "  FROM paper_xavier_reviews WHERE group_id=$1 "
        " ORDER BY reviewed_at, review_id", group_id)]
    settlements = [_rec(r) for r in await conn.fetch(
        "SELECT * FROM paper_settlements WHERE group_id=$1 "
        " ORDER BY settled_at, version", group_id)]
    pos = [p for p in await L.positions(conn, acct, include_closed=True)
           if p["group_id"] == group_id]
    by_fill = {e["fill_id"]: e for e in ledger
               if e.get("fill_id") and e["kind"] in ("FILL", "SALE")}
    entry_fills = [x for x in fills if entry is not None
                   and x["order_id"] == entry["order_id"]]
    mgmt_orders = [o for o in orders if o["role"] != "ENTRY"]
    mgmt_fills = [x for x in fills if x["order_id"] in
                  {o["order_id"] for o in mgmt_orders}]
    links = []

    # 1 · DEREK'S DECISION
    if entry is None:
        links.append(_link("DEREK_DECISION", MISSING,
                           why="NO_ENTRY_ORDER_IN_THIS_GROUP"))
    elif decision is None:
        links.append(_link(
            "DEREK_DECISION", MISSING,
            why=("THE_ENTRY_ORDER_NAMES_NO_DECISION"
                 if not entry.get("decision_id")
                 else "THE_NAMED_DECISION_RECORD_IS_NOT_FOUND"),
            ids=[entry.get("decision_id")] if entry.get("decision_id")
            else []))
    else:
        audit = decision_record_audit(decision)
        prov = decision.get("provenance") or {}
        links.append(_link(
            "DEREK_DECISION", PRESENT, ids=[decision["decision_id"]],
            at=decision["decided_at"], detail={
                "verdict": decision["verdict"],
                "strategy": decision.get("strategy"),
                "policy_version": decision["policy_version"],
                "valuation_id": decision.get("valuation_id"),
                "limit_price": decision.get("limit_price"),
                "proposed_qty": decision.get("proposed_qty"),
                "expected_net_usd": (decision.get("policy_decision") or {})
                .get("net_expected_profit_usd"),
                "record_audit": audit,
                "explanation": prov.get("explanation"),
                "inputs_sha256": (prov.get("inputs") or {}).get(
                    "inputs_sha256")}))
    # 2 · THE ENTRY ORDER
    if entry is None:
        links.append(_link("ENTRY_ORDER", MISSING,
                           why="NO_ENTRY_ORDER_IN_THIS_GROUP"))
    else:
        links.append(_link(
            "ENTRY_ORDER", PRESENT, ids=[entry["order_id"]],
            at=entry["decided_at"], detail={
                k: entry.get(k) for k in (
                    "state", "qty", "filled_qty", "limit_price",
                    "eligible_at", "expires_at", "terminal_at",
                    "terminal_reason", "strategy", "created_at")}))
    # 3 · THE ENTRY FILL(S)
    if not entry_fills:
        links.append(_link("ENTRY_FILLS", MISSING,
                           why="NO_SIMULATED_FILL_OF_THE_ENTRY_ORDER"))
    else:
        links.append(_link(
            "ENTRY_FILLS", PRESENT, ids=[x["fill_id"] for x in entry_fills],
            at=entry_fills[0]["filled_at"], detail={"fills": [
                {k: x.get(k) for k in ("fill_id", "filled_at", "qty",
                                       "price", "fee_usd", "gross_usd",
                                       "basis", "book_obs_id",
                                       "event_source")}
                for x in entry_fills]}))
    # 4 · THE LEDGER CASH DEBIT OF EACH ENTRY FILL
    if not entry_fills:
        links.append(_link("LEDGER_CASH_DEBIT", MISSING,
                           why="NO_ENTRY_FILL_TO_DEBIT"))
    else:
        absent, wrong, seqs = [], [], []
        for x in entry_fills:
            e = by_fill.get(x["fill_id"])
            if e is None or e["kind"] != "FILL":
                absent.append(x["fill_id"])
                continue
            seqs.append(e["seq"])
            if abs(float(e["cash_delta_usd"]) - _expected_cash(x)) > \
                    CASH_TOLERANCE_USD:
                wrong.append({"fill_id": x["fill_id"], "seq": e["seq"],
                              "ledger_cash_delta_usd": e["cash_delta_usd"],
                              "expected_usd": round(_expected_cash(x), 6)})
        st = MISSING if absent else INCONSISTENT if wrong else PRESENT
        debit = [e for e in ledger if e["seq"] in seqs]
        links.append(_link(
            "LEDGER_CASH_DEBIT", st, ids=seqs,
            at=debit[0]["committed_at"] if debit else None,
            why=(None if st == PRESENT else
                 "FILL_WITHOUT_ITS_LEDGER_ENTRY: %s" % absent if absent
                 else "LEDGER_DEBIT_DIFFERS_FROM_FILL_COST"),
            detail={"entries": [{k: e.get(k) for k in (
                "seq", "kind", "fill_id", "cash_delta_usd",
                "reserved_delta_usd", "cash_after_usd", "committed_at")}
                for e in debit], "mismatches": wrong}))
    # 5 · THE HANDOFF TO XAVIER
    if handoff is None:
        links.append(_link("XAVIER_HANDOFF", MISSING,
                           why=("A_FILLED_ENTRY_WITHOUT_A_HANDOFF"
                                if entry_fills else
                                "NO_FILL_SO_NO_HANDOFF_IS_DUE"),
                           pending=not entry_fills))
    else:
        links.append(_link(
            "XAVIER_HANDOFF", PRESENT, ids=[handoff["handoff_id"]],
            at=handoff["created_at"], detail={k: handoff.get(k) for k in (
                "first_fill_id", "first_fill_at", "owner", "confirmed_qty",
                "outstanding_qty", "strategy", "updated_at")}))
    # 6 · XAVIER'S REVIEWS
    if not reviews:
        links.append(_link("XAVIER_REVIEWS", MISSING,
                           why=("A_HANDED_OFF_GROUP_WITH_NO_REVIEW"
                                if handoff else "NOT_HANDED_OFF")))
    else:
        links.append(_link(
            "XAVIER_REVIEWS", PRESENT, ids=[r["review_id"] for r in reviews],
            at=reviews[0]["reviewed_at"], detail={"reviews": [
                {"review_id": r["review_id"], "at": r["reviewed_at"],
                 "trigger": r["trigger"],
                 "recommendation": r["recommendation"],
                 "action": (r.get("action") or {}).get("taken"),
                 "action_order_id": (r.get("action") or {}).get("order_id"),
                 "measure_source": (r.get("measure") or {}).get("source"),
                 "measure_stale": (r.get("measure") or {}).get("stale"),
                 "exceptional": r.get("exceptional")}
                for r in reviews]}))
    # 7 · XAVIER'S ACTIONS (management orders, their fills and cash)
    if not mgmt_orders:
        links.append(_link(
            "XAVIER_ACTIONS", NOT_APPLICABLE,
            why=("NO_MANAGEMENT_ORDER_WAS_PLACED: each review's recorded "
                 "action is listed under XAVIER_REVIEWS")))
    else:
        bad = [x["fill_id"] for x in mgmt_fills
               if x["fill_id"] not in by_fill or abs(
                   float(by_fill[x["fill_id"]]["cash_delta_usd"])
                   - _expected_cash(x)) > CASH_TOLERANCE_USD]
        links.append(_link(
            "XAVIER_ACTIONS", INCONSISTENT if bad else PRESENT,
            ids=[o["order_id"] for o in mgmt_orders],
            at=mgmt_orders[0]["decided_at"],
            why=("MANAGEMENT_FILL_WITHOUT_MATCHING_LEDGER_ENTRY: %s" % bad
                 if bad else None),
            detail={"orders": [{k: o.get(k) for k in (
                "order_id", "role", "direction", "state", "qty",
                "filled_qty", "limit_price", "decided_at", "terminal_at",
                "terminal_reason")} for o in mgmt_orders],
                "fills": [{"fill_id": x["fill_id"], "role": x["role"],
                           "filled_at": x["filled_at"], "qty": x["qty"],
                           "price": x["price"],
                           "ledger_seq": (by_fill.get(x["fill_id"]) or {})
                           .get("seq")} for x in mgmt_fills]}))
    # 8 · THE EXIT OR SETTLEMENT
    p = pos[0] if pos else None
    open_qty = float(p["open_qty"]) if p else None
    exited = bool(p) and open_qty <= 1e-9 and float(p["sold_qty"]) > 0 \
        and not settlements
    if settlements:
        links.append(_link(
            "EXIT_OR_SETTLEMENT", PRESENT,
            ids=[s["settlement_id"] for s in settlements],
            at=settlements[-1]["settled_at"], detail={"settlements": [
                {k: s.get(k) for k in (
                    "settlement_id", "version", "supersedes", "outcome",
                    "qty", "payout_per_contract", "payout_usd",
                    "evidence_source", "settled_at")}
                for s in settlements]}))
    elif exited:
        sells = [x for x in mgmt_fills if x["direction"] == "SELL"]
        links.append(_link(
            "EXIT_OR_SETTLEMENT", PRESENT, ids=[x["fill_id"] for x in sells],
            at=sells[-1]["filled_at"] if sells else None,
            detail={"closed_by": "SALES", "sold_qty": p["sold_qty"]}))
    else:
        links.append(_link(
            "EXIT_OR_SETTLEMENT", MISSING, pending=bool(p),
            why=("PENDING: the position is open (open qty %s); no exit or "
                 "settlement yet" % open_qty if p else
                 "NO_POSITION_FROM_THESE_FILLS")))
    # 9 · THE LEDGER RESULT
    group_cash = round(sum(float(e["cash_delta_usd"]) for e in ledger
                           if e["kind"] in ("FILL", "SALE", "SETTLEMENT",
                                            "CORRECTION")), 6)
    if settlements:
        want = {s["settlement_id"]: s for s in settlements}
        got = {(e.get("detail") or {}).get("settlement_id"): e
               for e in ledger if e["kind"] in ("SETTLEMENT", "CORRECTION")}
        absent = [sid for sid in want if sid not in got]
        wrong = []
        for sid, s in want.items():
            e = got.get(sid)
            if e is None:
                continue
            exp = (float(s["payout_usd"]) if int(s["version"]) == 1 else
                   float(s["payout_usd"]) - float(
                       next((w["payout_usd"] for w in settlements
                             if w["settlement_id"] == s.get("supersedes")),
                            0.0)))
            if abs(float(e["cash_delta_usd"]) - exp) > CASH_TOLERANCE_USD:
                wrong.append({"settlement_id": sid, "seq": e["seq"],
                              "ledger_cash_delta_usd": e["cash_delta_usd"],
                              "expected_usd": round(exp, 6)})
        st = MISSING if absent else INCONSISTENT if wrong else PRESENT
        ent = [got[sid] for sid in want if sid in got]
        links.append(_link(
            "LEDGER_RESULT", st, ids=[e["seq"] for e in ent],
            at=ent[-1]["committed_at"] if ent else None,
            why=(None if st == PRESENT else
                 "SETTLEMENT_WITHOUT_ITS_LEDGER_ENTRY: %s" % absent if absent
                 else "LEDGER_CREDIT_DIFFERS_FROM_THE_SETTLEMENT"),
            detail={"realized_pnl_usd": p["realized_pnl_usd"] if p else None,
                    "acquisition_cost_usd": (p or {}).get(
                        "acquisition_cost_usd"),
                    "group_cash_delta_usd": group_cash,
                    "entries": [{k: e.get(k) for k in (
                        "seq", "kind", "cash_delta_usd", "settlement_key",
                        "corrects_seq", "committed_at")} for e in ent],
                    "mismatches": wrong}))
    elif exited:
        sales = [by_fill[x["fill_id"]] for x in mgmt_fills
                 if x["direction"] == "SELL" and x["fill_id"] in by_fill]
        links.append(_link(
            "LEDGER_RESULT", PRESENT, ids=[e["seq"] for e in sales],
            at=sales[-1]["committed_at"] if sales else None,
            detail={"realized_pnl_usd": p["realized_pnl_usd"],
                    "group_cash_delta_usd": group_cash}))
    else:
        links.append(_link(
            "LEDGER_RESULT", MISSING, pending=bool(p),
            why=("PENDING: no realized result while the position is open"
                 if p else "NO_POSITION_FROM_THESE_FILLS"),
            detail={"group_cash_delta_usd": group_cash,
                    "realized_pnl_usd": (p or {}).get("realized_pnl_usd")}))
    # 10 · AUDREY'S AUDIT FINDINGS
    subjects = ([group_id] + order_ids + [x["fill_id"] for x in fills]
                + [s["settlement_id"] for s in settlements]
                + ([handoff["handoff_id"]] if handoff else [])
                + ([decision["decision_id"]] if decision else [])
                + [q["position_key"] for q in pos])
    found = await _findings_for(conn, acct, subjects)
    have = {(x["kind"], x["subject"]) for x in found}
    required = []
    if entry_fills:
        required.append((EV_FIRST_FILL, entry_fills[0]["fill_id"]))
    if handoff:
        required.append((EV_HANDOFF, handoff["handoff_id"]))
    for x in mgmt_fills:
        required.append((EV_MGMT_FILL, x["fill_id"]))
    for s in settlements:
        required.append((EV_SETTLEMENT, s["settlement_id"]))
        if s["outcome"] == "SETTLED_AT_VENUE_PRICE":
            required.append((EV_VENUE_PRICE, s["settlement_id"]))
    absent = [{"kind": k, "subject": s} for k, s in required
              if (k, s) not in have]
    links.append(_link(
        "AUDREY_AUDIT", MISSING if absent else PRESENT,
        ids=[x["finding_id"] for x in found],
        at=found[0]["found_at"] if found else None,
        why=("NOT_YET_AUDITED: %s" % absent if absent else None),
        detail={"required_event_audits": [
            {"kind": k, "subject": s, "audited": (k, s) in have}
            for k, s in required],
            "findings": [{k: x.get(k) for k in (
                "finding_id", "kind", "severity", "subject", "found_at",
                "improvement_task_id")} | {"passed": (x.get("detail") or {})
                                           .get("passed")}
                for x in found]}))
    missing = [x["link"] for x in links
               if x["status"] not in (PRESENT, NOT_APPLICABLE)]
    timeline = sorted(
        [{"at": x["at"], "link": x["link"], "ids": x["ids"][:5]}
         for x in links if x["at"] is not None],
        key=lambda t: (t["at"], t["link"]))
    return {"found": True, "fill_id": fill_id, "group_id": group_id,
            "account_id": acct,
            "strategy": (entry or {}).get("strategy")
            or (fills[0]["strategy"] if fills else None),
            "position": p, "links": links,
            "complete": not missing, "missing": missing,
            "pending": [x["link"] for x in links if x["pending"]],
            "defects": [x["link"] for x in links if x["defect"]],
            "timeline": timeline,
            "data_label": L.DATA_LABEL,
            "fills_are": "SIMULATED (PAPER_SIM_V1), not verified execution"}


async def recent_chains(conn, *, account_id: str = L.ACCOUNT_ID,
                        limit: int = 50, strategy: str | None = None
                        ) -> list:
    """The chain summary of the most recently filled groups."""
    rows = await conn.fetch(
        "SELECT f.group_id, min(f.filled_at) AS first_at, "
        "       (array_agg(f.fill_id ORDER BY f.filled_at, f.fill_id))[1] "
        "         AS first_fill FROM paper_fills f "
        " WHERE f.account_id=$1 AND ($2::text IS NULL OR f.strategy=$2) "
        " GROUP BY f.group_id ORDER BY 2 DESC LIMIT $3", account_id,
        strategy, int(limit))
    out = []
    for r in rows:
        c = await group_chain(conn, r["group_id"], fill_id=r["first_fill"])
        out.append({k: c.get(k) for k in (
            "group_id", "fill_id", "strategy", "complete", "missing",
            "pending", "defects")} | {"first_fill_at": L._epoch(
                r["first_at"])})
    return out


# ═════════════════════════════════════════════════════════════════════
# 3 · AUDREY AUDITS MEANINGFUL EVENTS AS THEY HAPPEN
# ═════════════════════════════════════════════════════════════════════

_UNAUDITED = (" AND NOT EXISTS (SELECT 1 FROM paper_audrey_findings a "
              " WHERE a.account_id = $1 AND a.kind = $2 AND a.subject = %s)")


async def _record(conn, ctx, out, *, kind, subject, severity, detail):
    from . import paper_audrey as PA
    f = await PA.finding(conn, ctx, kind=kind, subject=subject,
                         severity=severity, detail=detail)
    out["audited"][kind] = out["audited"].get(kind, 0) + (1 if f["new"]
                                                          else 0)
    if f["new"] and severity in ("WARNING", "CRITICAL"):
        t = await PA.open_task(conn, ctx, f, detail={"kind": kind,
                                                     "subject": subject})
        out["tasks_opened"] += 1 if t.get("created") else 0
        out["warnings"] += 1
    return f


async def _ledger_for_fill(conn, fill_id: str):
    return await conn.fetchrow(
        "SELECT seq, kind, cash_delta_usd, committed_at FROM paper_ledger "
        " WHERE fill_id=$1 AND kind IN ('FILL', 'SALE')", fill_id)


async def _audit_first_fills(conn, ctx, out, lim):
    acct = ctx["account_id"]
    rows = await conn.fetch(
        "SELECT * FROM (SELECT DISTINCT ON (f.order_id) f.fill_id, "
        "  f.order_id, f.group_id, f.direction, f.qty, f.price, f.fee_usd, "
        "  f.gross_usd, f.filled_at, f.strategy, o.decision_id "
        "  FROM paper_fills f JOIN paper_orders o ON o.order_id=f.order_id "
        " WHERE f.account_id=$1 AND o.role='ENTRY' "
        " ORDER BY f.order_id, f.filled_at, f.fill_id) x WHERE TRUE"
        + _UNAUDITED % "x.fill_id" + " ORDER BY x.filled_at LIMIT $3",
        acct, EV_FIRST_FILL, lim)
    for r in rows:
        x = _rec(r)
        led = await _ledger_for_fill(conn, x["fill_id"])
        exp = _expected_cash(x)
        led_ok = led is not None and led["kind"] == "FILL" and abs(
            float(led["cash_delta_usd"]) - exp) <= CASH_TOLERANCE_USD
        d = (_rec(await conn.fetchrow(
            "SELECT * FROM paper_decisions WHERE decision_id=$1",
            x["decision_id"])) if x.get("decision_id") else None)
        audit = decision_record_audit(d)
        hard = led_ok and d is not None and d.get("verdict") == "ENTER"
        await _record(conn, ctx, out, kind=EV_FIRST_FILL,
                      subject=x["fill_id"],
                      severity="INFO" if hard else "WARNING", detail={
                          "event": "FIRST_SIMULATED_FILL_OF_AN_ENTRY",
                          "fill_id": x["fill_id"], "order_id": x["order_id"],
                          "group_id": x["group_id"],
                          "strategy": x["strategy"],
                          "filled_at": x["filled_at"], "qty": x["qty"],
                          "price": x["price"], "fee_usd": x["fee_usd"],
                          "ledger_seq": None if led is None else led["seq"],
                          "ledger_cash_delta_usd": (
                              None if led is None
                              else float(led["cash_delta_usd"])),
                          "expected_cash_delta_usd": round(exp, 6),
                          "ledger_debit_matches": led_ok,
                          "decision_id": x.get("decision_id"),
                          "decision_found": d is not None,
                          "decision_record": {k: audit.get(k) for k in (
                              "complete", "gaps", "provenance")},
                          "passed": hard,
                          "chain": "/api/command/paper/learning/chain/%s"
                                   % x["fill_id"]})


async def _audit_handoffs(conn, ctx, out, lim):
    acct = ctx["account_id"]
    rows = await conn.fetch(
        "SELECT h.* FROM paper_handoffs h WHERE h.account_id=$1"
        + _UNAUDITED % "h.handoff_id" + " ORDER BY h.first_fill_at "
        "LIMIT $3", acct, EV_HANDOFF, lim)
    for r in rows:
        h = _rec(r)
        o = _rec(await conn.fetchrow(
            "SELECT order_id, strategy, filled_qty, decision_id FROM "
            " paper_orders WHERE order_id=$1", h["entry_order_id"]))
        first = await conn.fetchrow(
            "SELECT fill_id, filled_at FROM paper_fills WHERE order_id=$1 "
            " ORDER BY filled_at, fill_id LIMIT 1", h["entry_order_id"])
        checks = {
            "entry_order_found": o is not None,
            "first_fill_is_the_entrys_earliest": (
                first is not None and first["fill_id"] == h["first_fill_id"]),
            "strategy_matches_the_entry": (
                o is not None and o["strategy"] == h["strategy"]),
            "confirmed_qty_within_filled": (
                o is not None and float(h["confirmed_qty"])
                <= float(o["filled_qty"]) + 1e-9),
            "owner_is_xavier": h["owner"] == "XAVIER"}
        ok = all(checks.values())
        rev = await conn.fetchval(
            "SELECT review_id FROM paper_xavier_reviews WHERE group_id=$1 "
            " ORDER BY reviewed_at LIMIT 1", h["group_id"])
        await _record(conn, ctx, out, kind=EV_HANDOFF,
                      subject=h["handoff_id"],
                      severity="INFO" if ok else "WARNING", detail={
                          "event": "DEREK_TO_XAVIER_HANDOFF",
                          "handoff_id": h["handoff_id"],
                          "group_id": h["group_id"],
                          "strategy": h["strategy"],
                          "first_fill_id": h["first_fill_id"],
                          "first_fill_at": h["first_fill_at"],
                          "confirmed_qty": h["confirmed_qty"],
                          "checks": checks,
                          "first_xavier_review_id": rev,
                          "first_xavier_review": (
                              "RECORDED" if rev else
                              "NOT_YET: Xavier reviews from the first fill "
                              "within the pass budget"),
                          "passed": ok})


async def _audit_mgmt_fills(conn, ctx, out, lim):
    acct = ctx["account_id"]
    rows = await conn.fetch(
        "SELECT f.* FROM paper_fills f WHERE f.account_id=$1 "
        "   AND f.role <> 'ENTRY'" + _UNAUDITED % "f.fill_id"
        + " ORDER BY f.filled_at LIMIT $3", acct, EV_MGMT_FILL, lim)
    for r in rows:
        x = _rec(r)
        led = await _ledger_for_fill(conn, x["fill_id"])
        want_kind = "FILL" if x["direction"] == "BUY" else "SALE"
        exp = _expected_cash(x)
        ok = led is not None and led["kind"] == want_kind and abs(
            float(led["cash_delta_usd"]) - exp) <= CASH_TOLERANCE_USD
        await _record(conn, ctx, out, kind=EV_MGMT_FILL,
                      subject=x["fill_id"],
                      severity="INFO" if ok else "WARNING", detail={
                          "event": "XAVIER_MANAGEMENT_FILL",
                          "fill_id": x["fill_id"], "role": x["role"],
                          "direction": x["direction"],
                          "group_id": x["group_id"],
                          "order_id": x["order_id"],
                          "filled_at": x["filled_at"], "qty": x["qty"],
                          "price": x["price"], "fee_usd": x["fee_usd"],
                          "ledger_seq": None if led is None else led["seq"],
                          "ledger_kind": None if led is None else led["kind"],
                          "expected_cash_delta_usd": round(exp, 6),
                          "passed": ok})


async def _audit_settlements(conn, ctx, out, lim):
    acct = ctx["account_id"]
    for kind, extra in ((EV_SETTLEMENT, ""),
                        (EV_VENUE_PRICE,
                         " AND s.outcome = 'SETTLED_AT_VENUE_PRICE'"),
                        (EV_EXCEPTIONAL,
                         " AND (s.outcome NOT IN ('WON', 'LOST') "
                         "      OR s.version > 1)")):
        rows = await conn.fetch(
            "SELECT s.* FROM paper_settlements s WHERE s.account_id=$1"
            + extra + _UNAUDITED % "s.settlement_id"
            + " ORDER BY s.settled_at, s.version LIMIT $3", acct, kind, lim)
        for r in rows:
            s = _rec(r)
            led = _rec(await conn.fetchrow(
                "SELECT seq, kind, cash_delta_usd, committed_at FROM "
                " paper_ledger WHERE account_id=$1 AND kind IN "
                " ('SETTLEMENT', 'CORRECTION') AND detail->>'settlement_id'"
                " = $2", acct, s["settlement_id"]))
            payout_ok = abs(float(s["qty"]) * float(s["payout_per_contract"])
                            - float(s["payout_usd"])) <= 0.01
            detail: dict[str, Any] = {
                "settlement_id": s["settlement_id"],
                "group_id": s["group_id"], "position_key": s["position_key"],
                "outcome": s["outcome"], "version": s["version"],
                "supersedes": s.get("supersedes"), "qty": s["qty"],
                "payout_per_contract": s["payout_per_contract"],
                "payout_usd": s["payout_usd"],
                "evidence_source": s["evidence_source"],
                "settled_at": s["settled_at"],
                "ledger_seq": None if led is None else led["seq"],
                "ledger_kind": None if led is None else led["kind"],
                "ledger_cash_delta_usd": (None if led is None
                                          else led["cash_delta_usd"]),
                "payout_equals_qty_times_price": payout_ok}
            ok = payout_ok and led is not None and (
                (int(s["version"]) == 1 and led["kind"] == "SETTLEMENT"
                 and abs(float(led["cash_delta_usd"])
                         - float(s["payout_usd"])) <= CASH_TOLERANCE_USD)
                or (int(s["version"]) > 1 and led["kind"] == "CORRECTION"))
            if kind == EV_SETTLEMENT:
                detail["event"] = "POSITION_SETTLED"
            elif kind == EV_VENUE_PRICE:
                ev = s.get("evidence") or {}
                per = float(s["payout_per_contract"])
                vp_ok = (0.0 < per < 1.0 and ev.get("price") is not None
                         and abs(float(ev["price"]) - per) < 1e-6
                         and s["evidence_source"]
                         == "external_valuations.settlement_read")
                detail.update(
                    event="SETTLED_AT_THE_VENUES_OWN_PUBLISHED_PRICE",
                    venue_long_price=ev.get("venue_long_price"),
                    rule=ev.get("rule"), policy=ev.get("policy"),
                    venue_price_checks_passed=vp_ok,
                    never_an_assumed_refund=True)
                ok = ok and vp_ok
            else:
                pos = next((p for p in await L.positions(
                    conn, acct, include_closed=True)
                    if p["position_key"] == s["position_key"]), None)
                avg = float((pos or {}).get(
                    "avg_cost_per_contract_incl_fees") or 0.0)
                cost = round(avg * float(s["qty"]), 6)
                ev_at_entry = await conn.fetchval(
                    "SELECT (d.policy_decision->>'net_expected_profit_usd')"
                    "::float8 FROM paper_orders o JOIN paper_decisions d ON "
                    " d.decision_id=o.decision_id WHERE o.group_id=$1 AND "
                    " o.role='ENTRY' LIMIT 1", s["group_id"])
                detail.update(
                    event=("SETTLEMENT_CORRECTED" if int(s["version"]) > 1
                           else "EXCEPTIONAL_SETTLEMENT"),
                    cost_of_settled_qty_usd=cost,
                    result_usd=round(float(s["payout_usd"]) - cost, 6),
                    expected_net_at_entry_usd=ev_at_entry,
                    effect=("the position was paid an amount the entry's "
                            "ordinary-completion economics did not price"
                            if s["outcome"] == "SETTLED_AT_VENUE_PRICE" else
                            "a venue-declared void: the purchase price back"
                            if s["outcome"] == "VOID_REFUND" else
                            "a corrected settlement; the original version "
                            "and its ledger entry are kept"))
            detail["passed"] = ok
            await _record(conn, ctx, out, kind=kind,
                          subject=s["settlement_id"],
                          severity="INFO" if ok else "WARNING",
                          detail=detail)


async def _audit_ledger(conn, ctx, out, lim):
    acct = ctx["account_id"]
    rows = await conn.fetch(
        "SELECT f.fill_id, f.direction, f.gross_usd, f.fee_usd, f.group_id, "
        "       l.seq, l.kind, l.cash_delta_usd FROM paper_fills f "
        "  LEFT JOIN paper_ledger l ON l.fill_id = f.fill_id "
        "   AND l.kind IN ('FILL', 'SALE') "
        " WHERE f.account_id=$1 AND (l.seq IS NULL OR abs(CASE WHEN "
        "   f.direction = 'BUY' THEN -l.cash_delta_usd - (f.gross_usd + "
        "   f.fee_usd) ELSE l.cash_delta_usd - (f.gross_usd - f.fee_usd) "
        "   END) > 0.005)" + _UNAUDITED % "f.fill_id" + " LIMIT $3",
        acct, EV_LEDGER, lim)
    for r in rows:
        x = _rec(r)
        await _record(conn, ctx, out, kind=EV_LEDGER, subject=x["fill_id"],
                      severity="CRITICAL", detail={
                          "event": ("FILL_WITHOUT_ITS_LEDGER_ENTRY"
                                    if x["seq"] is None else
                                    "LEDGER_ENTRY_DIFFERS_FROM_THE_FILL"),
                          "fill_id": x["fill_id"], "group_id": x["group_id"],
                          "ledger_seq": x["seq"],
                          "ledger_cash_delta_usd": x["cash_delta_usd"],
                          "expected_cash_delta_usd": round(_expected_cash(
                              x), 6), "passed": False})
    rows = await conn.fetch(
        "SELECT s.settlement_id, s.group_id, s.payout_usd, s.version "
        "  FROM paper_settlements s WHERE s.account_id=$1 AND NOT EXISTS "
        " (SELECT 1 FROM paper_ledger l WHERE l.account_id = s.account_id "
        "   AND l.kind IN ('SETTLEMENT', 'CORRECTION') "
        "   AND l.detail->>'settlement_id' = s.settlement_id)"
        + _UNAUDITED % "s.settlement_id" + " LIMIT $3", acct, EV_LEDGER,
        lim)
    for r in rows:
        x = _rec(r)
        await _record(conn, ctx, out, kind=EV_LEDGER,
                      subject=x["settlement_id"], severity="CRITICAL",
                      detail={"event": "SETTLEMENT_WITHOUT_ITS_LEDGER_ENTRY",
                              **x, "passed": False})
    bal = await L.balances(conn, acct, now=float(ctx["now"]))
    if not bal.get("ledger_consistent", True):
        subj = "%s:seq:%s" % (acct, bal.get("last_sequence"))
        if not await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM paper_audrey_findings WHERE "
                " account_id=$1 AND kind=$2 AND subject=$3)", acct,
                EV_LEDGER, subj):
            await _record(conn, ctx, out, kind=EV_LEDGER, subject=subj,
                          severity="CRITICAL", detail={
                              "event": "RUNNING_BALANCES_DISAGREE_WITH_THE_"
                                       "LEDGER_SUM",
                              "last_sequence": bal.get("last_sequence"),
                              "passed": False})


async def step_audit_events(conn, ctx: dict) -> dict:
    """AUDREY'S EVENT AUDIT, EVERY PASS: each meaningful event since the
    last pass gets exactly one finding (idempotent per event, across passes,
    processes and restarts)."""
    out: dict[str, Any] = {"audited": {}, "warnings": 0, "tasks_opened": 0}
    lim = EVENTS_PER_KIND_PER_PASS
    for fn in (_audit_first_fills, _audit_handoffs, _audit_mgmt_fills,
               _audit_settlements, _audit_ledger):
        await fn(conn, ctx, out, lim)
    out["events_audited"] = sum(out["audited"].values())
    return out


# ═════════════════════════════════════════════════════════════════════
# 4 · LESSONS: EACH AGENT'S PERSISTENT MEMORY, FROM FORWARD RECORDS
# ═════════════════════════════════════════════════════════════════════

def provenance_of(selection: dict, ids_by_table: dict) -> dict:
    """WHICH RECORDS PRODUCED A LESSON: the selection, the count, the
    SHA-256 of every contributing 'table:id' (sorted) and a sample."""
    lines = sorted({"%s:%s" % (t, i) for t, ids in ids_by_table.items()
                    for i in ids if i is not None})
    return {"selection": selection, "record_count": len(lines),
            "records": {t: len({i for i in ids if i is not None})
                        for t, ids in ids_by_table.items()},
            "ids_sha256": hashlib.sha256("\n".join(lines).encode()
                                         ).hexdigest(),
            "ids_sample": {t: sorted({str(i) for i in ids
                                      if i is not None})[:25]
                           for t, ids in ids_by_table.items()},
            "reproduce": ("re-run the selection: the SHA-256 of the sorted "
                          "'table:id' lines must equal ids_sha256")}


async def _strategies(conn, acct: str) -> list:
    return [r["strategy"] for r in await conn.fetch(
        "SELECT DISTINCT strategy FROM paper_decisions WHERE account_id=$1 "
        " ORDER BY 1", acct)]


async def _entries(conn, acct, strategy, ws, we) -> list:
    """ENTER decisions of the strategy in the window with their entry
    order (if one was placed)."""
    return [_rec(r) for r in await conn.fetch(
        "SELECT d.decision_id, d.decided_at, d.proposed_qty, d.limit_price, "
        "       d.policy_decision, d.optimistic, d.us_market_slug, "
        "       d.holding_side, o.order_id, o.group_id, o.state, o.qty, "
        "       o.filled_qty FROM paper_decisions d LEFT JOIN paper_orders o "
        "    ON o.decision_id = d.decision_id AND o.role = 'ENTRY' "
        " WHERE d.account_id=$1 AND d.strategy=$2 AND d.verdict='ENTER' "
        "   AND d.decided_at >= $3 AND d.decided_at <= $4 "
        " ORDER BY d.decided_at, d.decision_id", acct, strategy,
        L._ts(ws), L._ts(we))]


async def _derek_refusal_funnel(conn, acct, strategy, ws, we) -> list:
    rows = [_rec(r) for r in await conn.fetch(
        "SELECT decision_id, verdict, refusal, "
        "       (policy_decision->'shortfall'->>'edge_shortfall_pp')::float8 "
        "         AS short_pp, "
        "       (policy_decision->'shortfall'->>'edge_threshold_pp')::float8 "
        "         AS threshold_pp FROM paper_decisions WHERE account_id=$1 "
        "   AND strategy=$2 AND decided_at >= $3 AND decided_at <= $4 "
        " ORDER BY decision_id", acct, strategy, L._ts(ws), L._ts(we))]
    if not rows:
        return []
    by: dict = {}
    for r in rows:
        k = r["refusal"] or "ENTER"
        by[k] = by.get(k, 0) + 1
    from . import derek_policy as DP
    entered = by.get("ENTER", 0)
    refused = {k: v for k, v in by.items() if k != "ENTER"}
    top = max(refused.items(), key=lambda kv: (kv[1], kv[0]))[0] \
        if refused else None
    # THE EDGE THRESHOLD AS RECORDED ON THE DECISIONS (the benchmark
    # policies record their shortfall against it; a strategy that records
    # none is not measured for near misses).
    ths = sorted({r["threshold_pp"] for r in rows
                  if r["threshold_pp"] is not None})
    bench = len(ths) == 1
    threshold = ths[0] if bench else None
    near = (sum(1 for r in rows if r["refusal"] == DP.R_BELOW
                and r["short_pp"] is not None
                and 0 < r["short_pp"] <= NEAR_MISS_PP)
            if bench else None)
    metrics = {"decisions": len(rows), "entered": entered,
               "refused": len(rows) - entered,
               "entry_rate": round(entered / len(rows), 6),
               "by_refusal": dict(sorted(refused.items(),
                                         key=lambda kv: -kv[1])[:12]),
               "binding_refusal": top,
               "edge_threshold_pp_recorded": threshold,
               "near_miss_edge_within_pp": NEAR_MISS_PP,
               "near_miss_edge_refusals": (
                   near if near is not None else
                   "NOT_MEASURED_FOR_THIS_STRATEGY (no single edge "
                   "threshold and shortfall is recorded on its refusals)")}
    stmt = ("%s: of %d decisions in the window, %d entered (%.1f%%); the "
            "most frequent refusal was %s (%d)." % (
                strategy, len(rows), entered, 100.0 * entered / len(rows),
                top or "none", refused.get(top, 0) if top else 0))
    if near:
        stmt += (" %d refusals missed the edge threshold by %.1f pp or "
                 "less." % (near, NEAR_MISS_PP))
    les = {"agent_id": DEREK, "kind": L_REFUSALS, "strategy": strategy,
           "window_start": ws, "window_end": we, "statement": stmt,
           "metrics": metrics,
           "provenance": provenance_of(
               {"table": "paper_decisions", "account_id": acct,
                "strategy": strategy, "decided_at": [ws, we]},
               {"paper_decisions": [r["decision_id"] for r in rows]}),
           "evidence_category": "DECISION_RECORDS_AT_DECISION_TIME"}
    if entered == 0 and len(rows) >= MIN_FUNNEL_DECISIONS:
        les["task"] = ("No %s entry in %d decisions: investigate the binding "
                       "refusal %s" % (strategy, len(rows), top))
    lowered = (None if threshold is None
               else round(threshold - NEAR_MISS_PP, 6))
    # A PROPOSAL STARTS FROM THE ACTIVE THRESHOLD, never from a superseded
    # one: decisions recorded under an earlier version (V1's 5.0 pp before
    # the owner's 0.5 pp decision) argue nothing about the running policy.
    active_pp = None
    if strategy == PARAM_POLICY:
        try:
            head = await _active_parameters(conn, PARAM_POLICY)
            active_pp = (None if head is None else float(
                L._j(head["params"]).get("min_gross_edge_pp")))
        except Exception:                                   # noqa: BLE001
            active_pp = None
    superseded = (threshold is not None and active_pp is not None
                  and abs(threshold - active_pp) > 1e-9)
    if superseded:
        metrics["edge_threshold_pp_active"] = active_pp
    bounded = (strategy == PARAM_POLICY and lowered is not None
               and not superseded and active_pp is not None
               and check_change(C_EDGE, strategy, {
                   "parameter": "min_gross_edge_pp", "from": threshold,
                   "to": lowered}) is None)
    if bench and near and near >= NEAR_MISS_FOR_PROPOSAL and superseded:
        metrics["proposal"] = {
            "status": "NOT_APPLICABLE",
            "why": ("these near misses were recorded under a %s pp "
                    "threshold that is no longer active (the active "
                    "threshold is %s pp); a proposal starts from the "
                    "active threshold only" % (threshold, active_pp))}
    elif bench and near and near >= NEAR_MISS_FOR_PROPOSAL and not bounded:
        # NEAR MISSES WOULD ARGUE FOR A LOWER THRESHOLD; the 0.5 pp floor is
        # an owner mandate (a lower threshold is a separate owner decision),
        # so no in-bounds proposal exists and none is recorded -- said so.
        metrics["proposal"] = {
            "status": "NOT_APPLICABLE",
            "why": ("%d near misses would argue for lowering the edge "
                    "threshold from %s pp to %s pp, which is outside the "
                    "owner-mandated bounds %s (or not a supported policy); "
                    "a lower threshold needs a separate owner decision"
                    % (near, threshold, lowered,
                       PARAM_BOUNDS["min_gross_edge_pp"]))}
    if bench and near and near >= NEAR_MISS_FOR_PROPOSAL and bounded:
        # ONLY THE SUPPORTED, BOUNDED CHANGE IS PROPOSED (the completed-game
        # policy, within 0.5..6.0 pp, at most 1 pp per step) -- reachable
        # only when the active threshold is above the floor.
        les["proposal"] = {
            "change_class": C_EDGE,
            "proposed_change": {"parameter": "min_gross_edge_pp",
                                "from": threshold,
                                "to": round(threshold - NEAR_MISS_PP, 6),
                                "units": "percentage points",
                                "applies_to": strategy},
            "rationale": ("%d refusals in the training window missed the "
                          "%.1f pp threshold by %.1f pp or less; whether "
                          "admitting them pays is a question for FORWARD "
                          "outcomes" % (near, threshold, NEAR_MISS_PP))}
    return [les]


async def _closed_positions(conn, acct, groups: set) -> list:
    return [p for p in await L.positions(conn, acct, include_closed=True)
            if p["group_id"] in groups and float(p["open_qty"]) <= 1e-9]


async def _derek_settled(conn, acct, strategy, ws, we) -> list:
    ents = [e for e in await _entries(conn, acct, strategy, ws, we)
            if e.get("group_id") and float(e.get("filled_qty") or 0) > 0]
    if not ents:
        return []
    by_group = {e["group_id"]: e for e in ents}
    closed = await _closed_positions(conn, acct, set(by_group))
    if not closed:
        return []
    sets = {r["group_id"]: _rec(r) for r in await conn.fetch(
        "SELECT DISTINCT ON (group_id) group_id, settlement_id, outcome, "
        "       version, settled_at FROM paper_settlements "
        " WHERE account_id=$1 AND group_id = ANY($2::text[]) "
        " ORDER BY group_id, version DESC", acct,
        [p["group_id"] for p in closed])}
    rows, realized, expected = [], 0.0, 0.0
    outcomes: dict = {}
    for p in closed:
        e = by_group[p["group_id"]]
        pd = e.get("policy_decision") or {}
        ev, pq = _f(pd.get("net_expected_profit_usd")), _f(e.get(
            "proposed_qty"))
        exp = (None if ev is None or not pq else
               round(ev / pq * float(p["bought_qty"]), 6))
        s = sets.get(p["group_id"])
        oc = (s or {}).get("outcome") or "EXITED_BY_SALES"
        outcomes[oc] = outcomes.get(oc, 0) + 1
        realized += float(p["realized_pnl_usd"])
        expected += exp or 0.0
        rows.append({"group_id": p["group_id"],
                     "decision_id": e["decision_id"], "outcome": oc,
                     "settlement_id": (s or {}).get("settlement_id"),
                     "realized_pnl_usd": p["realized_pnl_usd"],
                     "expected_at_decision_usd": exp})
    exc = sum(v for k, v in outcomes.items()
              if k not in ("WON", "LOST", "EXITED_BY_SALES"))
    gap = round(realized - expected, 6)
    metrics = {"closed_positions": len(closed),
               "open_positions": len(by_group) - len(closed),
               "by_outcome": outcomes,
               "realized_pnl_usd": round(realized, 6),
               "expected_at_decision_usd": round(expected, 6),
               "expected_basis": ("each decision's expected net (known at "
                                  "the decision) scaled to the quantity "
                                  "actually filled; never re-scored"),
               "realized_minus_expected_usd": gap,
               "exceptional_settlements": exc, "positions": rows[:25],
               "sample_size_note": ("%d closed positions: a small sample "
                                    "is reported, not generalised"
                                    % len(closed))}
    stmt = ("%s: %d entries closed (%s); realized simulated P&L $%.2f "
            "against $%.2f expected at decision (difference $%.2f)." % (
                strategy, len(closed), ", ".join(
                    "%s %d" % kv for kv in sorted(outcomes.items())),
                realized, expected, gap))
    les = {"agent_id": DEREK, "kind": L_SETTLED, "strategy": strategy,
           "window_start": ws, "window_end": we, "statement": stmt,
           "metrics": metrics,
           "provenance": provenance_of(
               {"entries": "paper_decisions ENTER with a filled entry order",
                "strategy": strategy, "decided_at": [ws, we],
                "closed": "positions with zero open quantity"},
               {"paper_decisions": [r["decision_id"] for r in rows],
                "paper_settlements": [r["settlement_id"] for r in rows],
                "paper_groups": [r["group_id"] for r in rows]}),
           "evidence_category": ("SIMULATED_EXECUTION_WITH_AUTHORITATIVE_"
                                 "SETTLEMENT")}
    if gap < -CASH_TOLERANCE_USD or exc:
        les["task"] = ("%s: realized $%.2f vs expected $%.2f over %d closed "
                       "entries%s -- review the entry economics" % (
                           strategy, realized, expected, len(closed),
                           " (%d exceptional settlements)" % exc
                           if exc else ""))
    return [les]


async def _derek_fills(conn, acct, strategy, ws, we) -> list:
    ents = [e for e in await _entries(conn, acct, strategy, ws, we)
            if e.get("order_id") and e.get("optimistic")]
    done = [e for e in ents if e["state"] in L.TERMINAL_STATES]
    if not done:
        return []
    fills = {}
    for r in await conn.fetch(
            "SELECT order_id, fill_id, qty, gross_usd, fee_usd FROM "
            " paper_fills WHERE order_id = ANY($1::text[])",
            [e["order_id"] for e in done]):
        fills.setdefault(r["order_id"], []).append(_rec(r))
    oq = aq = og = ag = fees = 0.0
    cq = c_og = c_ag = 0.0
    unfilled = 0
    for e in done:
        opt = e["optimistic"] or {}
        o_qty, o_gross = float(opt.get("filled_qty") or 0), float(
            opt.get("gross_usd") or 0)
        fs = fills.get(e["order_id"], [])
        a_qty = sum(float(x["qty"]) for x in fs)
        a_gross = sum(float(x["gross_usd"]) for x in fs)
        fees += sum(float(x["fee_usd"]) for x in fs)
        oq, aq, og, ag = oq + o_qty, aq + a_qty, og + o_gross, ag + a_gross
        unfilled += 1 if a_qty <= 0 else 0
        if a_qty > 0 and o_qty > 0:
            cq += a_qty
            c_ag += a_gross
            c_og += o_gross / o_qty * a_qty
    ratio = None if oq <= 0 else round(aq / oq, 6)
    slip = None if cq <= 0 else round((c_ag - c_og) / cq, 6)
    metrics = {"entries_terminal": len(done),
               "entries_pending": len(ents) - len(done),
               "unfilled_entries": unfilled,
               "optimistic_qty": round(oq, 6), "simulated_qty": round(aq, 6),
               "fill_ratio_vs_optimistic": ratio,
               "optimistic_gross_usd": round(og, 6),
               "simulated_gross_usd": round(ag, 6),
               "slippage_per_contract_vs_optimistic_usd": slip,
               "fees_usd": round(fees, 6),
               "optimistic_is": ("the decision-time book, no delay, no "
                                 "consumption: a bound, never a fill"),
               "simulated_is": "PAPER_SIM_V1 after the decision delay"}
    stmt = ("%s: %d terminal entries filled %s of the optimistic quantity "
            "(%d unfilled); slippage vs the optimistic VWAP %s per "
            "contract." % (strategy, len(done),
                           "n/a" if ratio is None else "%.1f%%"
                           % (100 * ratio), unfilled,
                           "n/a" if slip is None else "$%.4f" % slip))
    les = {"agent_id": DEREK, "kind": L_FILLS, "strategy": strategy,
           "window_start": ws, "window_end": we, "statement": stmt,
           "metrics": metrics,
           "provenance": provenance_of(
               {"entries": "paper_decisions ENTER with a terminal entry "
                           "order", "strategy": strategy,
                "decided_at": [ws, we]},
               {"paper_decisions": [e["decision_id"] for e in done],
                "paper_orders": [e["order_id"] for e in done],
                "paper_fills": [x["fill_id"] for fs in fills.values()
                                for x in fs]}),
           "evidence_category": "SIMULATED_FILL_VS_OPTIMISTIC_BOUND"}
    if (ratio is not None and ratio < 0.9) or (slip is not None
                                               and slip > 0.005):
        les["task"] = ("%s: simulated fills reached %s of the optimistic "
                       "quantity, slippage %s per contract -- revisit "
                       "sizing against displayed depth" % (
                           strategy, "n/a" if ratio is None else
                           "%.0f%%" % (100 * ratio),
                           "n/a" if slip is None else "$%.4f" % slip))
    return [les]


async def _derek_exceptional(conn, acct, strategy, ws, we) -> list:
    rows = [_rec(r) for r in await conn.fetch(
        "SELECT s.settlement_id, s.group_id, s.outcome, s.version, s.qty, "
        "       s.payout_usd, s.payout_per_contract, s.settled_at, "
        "       o.decision_id, "
        "       (d.policy_decision->>'net_expected_profit_usd')::float8 "
        "         AS ev FROM paper_settlements s JOIN paper_orders o "
        "    ON o.group_id = s.group_id AND o.role = 'ENTRY' "
        "  LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id "
        " WHERE s.account_id=$1 AND o.strategy=$2 AND s.settled_at >= $3 "
        "   AND s.settled_at <= $4 AND (s.outcome NOT IN ('WON', 'LOST') "
        "    OR s.version > 1) ORDER BY s.settled_at, s.settlement_id",
        acct, strategy, L._ts(ws), L._ts(we))]
    if not rows:
        return []
    pos = {p["group_id"]: p for p in await L.positions(
        conn, acct, include_closed=True)}
    items, effect = [], 0.0
    for r in rows:
        p = pos.get(r["group_id"]) or {}
        cost = round(float(p.get("avg_cost_per_contract_incl_fees") or 0)
                     * float(r["qty"]), 6)
        res = round(float(r["payout_usd"]) - cost, 6)
        effect += res
        items.append(dict(r, cost_usd=cost, result_usd=res))
    stmt = ("%s: %d exceptional settlements (%s); their result was $%.2f, "
            "an outcome the entry economics%s did not price." % (
                strategy, len(rows), ", ".join(sorted({
                    r["outcome"] for r in rows})), effect,
                " (conditional on ordinary completion)"
                if "COMPLETED_GAME" in strategy else ""))
    return [{"agent_id": DEREK, "kind": L_EXCEPTIONAL, "strategy": strategy,
             "window_start": ws, "window_end": we, "statement": stmt,
             "metrics": {"exceptional_settlements": len(rows),
                         "result_usd": round(effect, 6),
                         "settlements": items[:25],
                         "exceptional_probabilities": "UNMEASURED"},
             "provenance": provenance_of(
                 {"table": "paper_settlements", "strategy": strategy,
                  "settled_at": [ws, we],
                  "rule": "outcome not WON/LOST, or a corrected version"},
                 {"paper_settlements": [r["settlement_id"] for r in rows],
                  "paper_decisions": [r["decision_id"] for r in rows]}),
             "evidence_category": ("SIMULATED_EXECUTION_WITH_AUTHORITATIVE_"
                                   "SETTLEMENT"),
             "task": ("%s: %d exceptional settlements -- the entry's "
                      "economics assumed ordinary completion" % (
                          strategy, len(rows)))}]


def _exit_counterfactual(review: dict) -> float | None:
    for c in ((review.get("alternatives") or {}).get("candidates") or []):
        if c.get("action") == "EXIT":
            return _f(c.get("worst_case_net_usd"))
    return None


async def _xavier_management(conn, acct, strategy, ws, we) -> list:
    groups = {r["group_id"] for r in await conn.fetch(
        "SELECT group_id FROM paper_handoffs WHERE account_id=$1 "
        "   AND strategy=$2 AND first_fill_at >= $3 AND first_fill_at <= $4",
        acct, strategy, L._ts(ws), L._ts(we))}
    if not groups:
        return []
    revs: dict = {}
    for r in await conn.fetch(
            "SELECT review_id, group_id, reviewed_at, trigger, "
            "       recommendation, measure, action, alternatives "
            "  FROM paper_xavier_reviews WHERE group_id = ANY($1::text[]) "
            " ORDER BY reviewed_at, review_id", list(groups)):
        revs.setdefault(r["group_id"], []).append(_rec(r))
    allpos = {p["group_id"]: p for p in await L.positions(
        conn, acct, include_closed=True) if p["group_id"] in groups}
    actions: dict = {}
    recs: dict = {}
    stale = 0
    for g, rs in revs.items():
        for r in rs:
            a = (r.get("action") or {}).get("taken") or "NONE"
            actions[a] = actions.get(a, 0) + 1
            k = r.get("recommendation") or "NONE"
            recs[k] = recs.get(k, 0) + 1
            stale += 1 if (r.get("measure") or {}).get("stale") else 0
    compared, better, better_stale, cf_sum, rows = 0, 0, 0, 0.0, []
    for g, p in allpos.items():
        if float(p["open_qty"]) > 1e-9 or not revs.get(g):
            continue
        first = revs[g][0]
        cf = _exit_counterfactual(first)
        if cf is None:
            continue
        compared += 1
        diff = round(cf - float(p["realized_pnl_usd"]), 6)
        cf_sum += diff
        st = bool((first.get("measure") or {}).get("stale"))
        if diff > CASH_TOLERANCE_USD:
            better += 1
            better_stale += 1 if st else 0
        rows.append({"group_id": g, "first_review_id": first["review_id"],
                     "realized_pnl_usd": p["realized_pnl_usd"],
                     "exit_at_first_review_net_usd": cf,
                     "exit_minus_realized_usd": diff,
                     "first_review_measure_stale": st})
    closed = sum(1 for p in allpos.values() if float(p["open_qty"]) <= 1e-9)
    metrics = {"groups_managed": len(groups),
               "positions_closed": closed,
               "reviews": sum(len(v) for v in revs.values()),
               "recommendations": recs, "actions_taken": actions,
               "stale_measure_reviews": stale,
               "counterfactual_exit_at_first_review": {
                   "label": COUNTERFACTUAL,
                   "positions_compared": compared,
                   "positions_where_exit_would_have_been_better": better,
                   "of_which_first_measure_stale": better_stale,
                   "exit_minus_realized_usd": round(cf_sum, 6),
                   "basis": ("the first review's displayed exit walk at "
                             "that instant (full exit, after fees) against "
                             "the realized result; the exit was NOT taken, "
                             "so its fill is not established"),
                   "positions": rows[:25]}}
    stmt = ("%s: Xavier managed %d groups (%d closed) over %d reviews; "
            "actions %s; %d reviews ran on a stale measure." % (
                strategy, len(groups), closed, metrics["reviews"],
                ", ".join("%s %d" % kv for kv in sorted(actions.items()))
                or "none", stale))
    if compared:
        stmt += (" COUNTERFACTUAL (not executable proof): exiting at the "
                 "first review would have differed from the realized result "
                 "by $%.2f over %d closed positions." % (cf_sum, compared))
    les = {"agent_id": XAVIER, "kind": L_MANAGEMENT, "strategy": strategy,
           "window_start": ws, "window_end": we, "statement": stmt,
           "metrics": metrics,
           "provenance": provenance_of(
               {"groups": "paper_handoffs first filled in the window",
                "strategy": strategy, "first_fill_at": [ws, we]},
               {"paper_handoffs_groups": sorted(groups),
                "paper_xavier_reviews": [r["review_id"] for rs in
                                         revs.values() for r in rs]}),
           "evidence_category": ("SIMULATED_MANAGEMENT_WITH_COUNTERFACTUAL_"
                                 "COMPARISON")}
    if compared and better * 2 > compared:
        les["task"] = ("%s: in %d of %d closed positions an exit at the "
                       "first review would have beaten the realized result "
                       "(COUNTERFACTUAL) -- review the hold ranking" % (
                           strategy, better, compared))
    if better_stale >= STALE_EXIT_FOR_PROPOSAL:
        les["proposal"] = {
            "change_class": C_STALE_EXIT,
            "proposed_change": {"parameter": "exit_when_measure_stale",
                                "from": False, "to": True,
                                "applies_to": strategy},
            "rationale": ("in %d closed positions whose first review ran on "
                          "a stale measure, the displayed exit beat the "
                          "realized result (COUNTERFACTUAL, not executable "
                          "proof); a forward evaluation decides"
                          % better_stale)}
    return [les]


async def _audrey_coverage(conn, acct, ws, we) -> list:
    rows = await conn.fetch(
        "SELECT o.group_id, min(f.filled_at) AS first_at FROM paper_orders o "
        "  JOIN paper_fills f ON f.order_id = o.order_id "
        " WHERE o.account_id=$1 AND o.role='ENTRY' GROUP BY o.group_id "
        "HAVING min(f.filled_at) >= $2 AND min(f.filled_at) <= $3 "
        " ORDER BY 2 DESC LIMIT $4", acct, L._ts(ws), L._ts(we),
        CHAINS_PER_COVERAGE_LESSON)
    if not rows:
        return []
    complete = pending = defective = 0
    defects: dict = {}
    for r in rows:
        c = await group_chain(conn, r["group_id"])
        if c.get("complete"):
            complete += 1
        elif c.get("defects"):
            defective += 1
            for d in c["defects"]:
                defects[d] = defects.get(d, 0) + 1
        else:
            pending += 1
    fnd = [_rec(r) for r in await conn.fetch(
        "SELECT finding_id, kind, severity FROM paper_audrey_findings "
        " WHERE account_id=$1 AND kind = ANY($2::text[]) "
        "   AND found_at >= $3 AND found_at <= $4", acct, list(EVENT_KINDS),
        L._ts(ws), L._ts(we))]
    by_kind: dict = {}
    sev: dict = {}
    for x in fnd:
        by_kind[x["kind"]] = by_kind.get(x["kind"], 0) + 1
        sev[x["severity"]] = sev.get(x["severity"], 0) + 1
    metrics = {"filled_groups": len(rows), "chains_complete": complete,
               "chains_pending_open_positions": pending,
               "chains_with_defects": defective, "defects_by_link": defects,
               "event_audits_by_kind": by_kind,
               "event_audits_by_severity": sev}
    stmt = ("Audrey: of %d filled groups, %d chains are complete, %d are "
            "pending (open positions) and %d have defects%s; %d event "
            "audits recorded (%s)." % (
                len(rows), complete, pending, defective,
                " (%s)" % ", ".join("%s %d" % kv for kv in sorted(
                    defects.items())) if defects else "", len(fnd),
                ", ".join("%s %d" % kv for kv in sorted(sev.items()))
                or "none"))
    les = {"agent_id": AUDREY, "kind": L_COVERAGE, "strategy": None,
           "window_start": ws, "window_end": we, "statement": stmt,
           "metrics": metrics,
           "provenance": provenance_of(
               {"groups": "entry orders first filled in the window",
                "account_id": acct, "first_fill_at": [ws, we]},
               {"paper_groups": [r["group_id"] for r in rows],
                "paper_audrey_findings": [x["finding_id"] for x in fnd]}),
           "evidence_category": "AUDIT_OF_THE_PAPER_RECORD"}
    if defective:
        les["task"] = ("%d paper chains have defects (%s) -- trace and "
                       "repair the missing links" % (
                           defective, ", ".join(sorted(defects))))
    return [les]


async def derive_lessons(conn, *, account_id: str, now: float) -> tuple:
    """EVERY LESSON THE FORWARD RECORDS SUPPORT NOW, and the errors of the
    derivations that failed (each named, none silently dropped)."""
    ws, we = float(now) - LESSON_WINDOW_S, float(now)
    out, errors = [], {}
    jobs = []
    for s in await _strategies(conn, account_id):
        jobs += [("%s:%s" % (L_REFUSALS, s), _derek_refusal_funnel(
                     conn, account_id, s, ws, we)),
                 ("%s:%s" % (L_SETTLED, s), _derek_settled(
                     conn, account_id, s, ws, we)),
                 ("%s:%s" % (L_FILLS, s), _derek_fills(
                     conn, account_id, s, ws, we)),
                 ("%s:%s" % (L_EXCEPTIONAL, s), _derek_exceptional(
                     conn, account_id, s, ws, we)),
                 ("%s:%s" % (L_MANAGEMENT, s), _xavier_management(
                     conn, account_id, s, ws, we))]
    jobs.append((L_COVERAGE, _audrey_coverage(conn, account_id, ws, we)))
    for name, coro in jobs:
        try:
            out.extend(await coro)
        except Exception as exc:                                # noqa: BLE001
            errors[name] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    return out, errors


def series_key(les: dict) -> str:
    return "%s:%s:%s" % (les["agent_id"], les["kind"],
                         les.get("strategy") or "ACCOUNT")


def lesson_digest(les: dict) -> str:
    """The content of a lesson, without its instants (the window moves with
    every run): unchanged content writes nothing."""
    return _sha({"statement": les["statement"], "metrics": les["metrics"],
                 "ids": les["provenance"]["ids_sha256"]})


async def _lesson_task(conn, *, account_id, les, lesson_id, now) -> str | None:
    """ONE IMPROVEMENT TASK PER LESSON SERIES (agent_tasks, kind
    PAPER_LEARNING_TASK); a later lesson of the series is appended to it as
    an event. Never an IMPROVEMENT candidate."""
    from . import improvement as IMP
    tid = "paper-learn-task:%s" % _h(account_id, series_key(les))
    got = await IMP.create_task(
        conn, assignee=les["agent_id"], created_by="AUDREY", kind=TASK_KIND,
        title="Paper lesson: %s" % les["task"][:180],
        spec={"lesson_series": series_key(les), "account_id": account_id,
              "first_lesson_id": lesson_id, "kind": les["kind"],
              "strategy": les.get("strategy"),
              "evidence_category": les["evidence_category"],
              "basis": FORWARD_ONLY,
              "promotion": ("NONE: a paper lesson opens investigation work; "
                            "a change is proposed and evaluated forward "
                            "separately, never activated from here")},
        evidence=[{"kind": "paper_agent_lessons", "id": lesson_id,
                   "href": "/api/command/paper/learning/lessons"}],
        task_id=tid, now=now)
    if not got.get("ok"):
        return None
    if not got.get("created"):
        seen = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM agent_task_events WHERE "
            " task_id=$1 AND detail->>'lesson_id' = $2)", tid, lesson_id)
        if not seen:
            await IMP.task_event(conn, tid, kind="LESSON_UPDATED",
                                 actor="AUDREY",
                                 detail={"lesson_id": lesson_id,
                                         "statement": les["statement"],
                                         "task": les["task"]}, now=now)
    return tid


async def write_lesson(conn, *, account_id: str, session_id: str | None,
                       les: dict, now: float) -> dict:
    """APPEND A LESSON VERSION when its content changed; else nothing."""
    sk = series_key(les)
    dig = lesson_digest(les)
    last = await conn.fetchrow(
        "SELECT lesson_id, version, digest FROM paper_agent_lessons "
        " WHERE account_id=$1 AND series_key=$2 ORDER BY version DESC "
        " LIMIT 1", account_id, sk)
    if last is not None and last["digest"] == dig:
        return {"written": False, "lesson_id": last["lesson_id"],
                "series_key": sk, "why": "NO_CHANGE"}
    ver = 1 if last is None else int(last["version"]) + 1
    lid = "paperlesson:%s" % _h(account_id, sk, ver)
    task_id = None
    if les.get("task"):
        task_id = await _lesson_task(conn, account_id=account_id, les=les,
                                     lesson_id=lid, now=now)
    got = await conn.fetchval(
        "INSERT INTO paper_agent_lessons (lesson_id, account_id, session_id,"
        " agent_id, kind, strategy, series_key, version, supersedes, "
        " learned_at, window_start, window_end, statement, metrics, "
        " provenance, evidence_category, basis, improvement_task_id, digest)"
        " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,"
        " $15::jsonb,$16,$17,$18,$19) ON CONFLICT DO NOTHING "
        "RETURNING lesson_id", lid, account_id, session_id, les["agent_id"],
        les["kind"], les.get("strategy"), sk, ver,
        None if last is None else last["lesson_id"], L._ts(now),
        None if les.get("window_start") is None
        else L._ts(les["window_start"]), L._ts(les["window_end"]),
        les["statement"], json.dumps(les["metrics"], default=str),
        json.dumps(les["provenance"], default=str),
        les["evidence_category"], FORWARD_ONLY, task_id, dig)
    return {"written": got is not None, "lesson_id": lid, "version": ver,
            "series_key": sk, "improvement_task_id": task_id}


# ═════════════════════════════════════════════════════════════════════
# 5 · IMPROVEMENT PROPOSALS: TRAIN STRICTLY BEFORE A LATER EVALUATION
# ═════════════════════════════════════════════════════════════════════

def check_protocol(*, training_start, training_end, evaluation_start,
                   evaluation_end, proposed_at) -> str | None:
    """THE SPLIT RULE (pure; the database CHECKs it again):
         training_start < training_end <= proposed_at <= evaluation_start
                                                     < evaluation_end
    Returns the named refusal, or None."""
    ts, te = float(training_start), float(training_end)
    es, ee = float(evaluation_start), float(evaluation_end)
    pa = float(proposed_at)
    if not (ts < te) or not (es < ee):
        return R_EMPTY_PERIOD
    if te > es:
        return R_OVERLAP
    if te > pa:
        return R_TRAIN_AFTER_PROPOSAL
    if es < pa:
        return R_PEEK
    return None


async def _proposal_event(conn, pid, *, kind, actor, detail, now) -> None:
    await conn.execute(
        "INSERT INTO paper_improvement_proposal_events (proposal_id, at, "
        " kind, actor, detail) VALUES ($1,$2,$3,$4,$5::jsonb)", pid,
        L._ts(now), kind, actor, json.dumps(detail or {}, default=str))


async def create_proposal(conn, *, account_id: str, agent_id: str,
                          strategy: str | None, change_class: str,
                          proposed_change: dict, rationale: str,
                          proposed_by: str, proposed_at: float,
                          training: tuple, evaluation: tuple,
                          source_lesson_ids: list | None = None,
                          min_evaluation_outcomes: int | None = None) -> dict:
    """RECORD A PROPOSED PAPER CHANGE AND ITS FIXED PROTOCOL. Refuses, by
    name, an unknown class, the wrong agent, and any protocol whose
    training period does not end before its evaluation period (or whose
    evaluation would begin before the proposal)."""
    cls = CHANGE_CLASSES.get(change_class)
    if cls is None:
        return {"ok": False, "refusal": R_UNKNOWN_CLASS}
    if cls["agent"] != agent_id:
        return {"ok": False, "refusal": R_WRONG_AGENT,
                "belongs_to": cls["agent"]}
    chg = check_change(change_class, strategy, proposed_change)
    if chg:
        return {"ok": False, "refusal": chg, "bounds": PARAM_BOUNDS,
                "max_step_pp": PARAM_MAX_STEP_PP}
    ref = check_protocol(training_start=training[0],
                         training_end=training[1],
                         evaluation_start=evaluation[0],
                         evaluation_end=evaluation[1],
                         proposed_at=proposed_at)
    if ref:
        return {"ok": False, "refusal": ref,
                "training": list(training), "evaluation": list(evaluation),
                "proposed_at": proposed_at}
    protocol = {"metric": cls["metric"], "pass_rule": cls["pass_rule"],
                "min_evaluation_outcomes": int(
                    min_evaluation_outcomes
                    or cls["min_evaluation_outcomes"]),
                "training_period": list(training),
                "evaluation_period": list(evaluation),
                "forward_only": ("evaluated only on records decided in the "
                                 "evaluation period, with outcomes known by "
                                 "the evaluation; never on the training "
                                 "records"),
                "evaluated_once": ("after the evaluation period ends; the "
                                   "result is write-once (no re-scoring, no "
                                   "early stopping)"),
                "evaluator": EVALUATOR,
                "activation": ("never automatic: a PASS, a named human "
                               "approver and the %s control row; scope "
                               "PAPER_ONLY" % ACTIVATION_CONTROL_KEY)}
    pid = "paperprop:%s" % _h(account_id, agent_id, change_class,
                              strategy or "", proposed_at)
    try:
        got = await conn.fetchval(
            "INSERT INTO paper_improvement_proposals (proposal_id, "
            " account_id, agent_id, strategy, change_class, proposed_change,"
            " rationale, source_lesson_ids, proposed_by, proposed_at, "
            " training_start, training_end, evaluation_start, "
            " evaluation_end, protocol) VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,"
            " $8,$9,$10,$11,$12,$13,$14,$15::jsonb) ON CONFLICT "
            " (proposal_id) DO NOTHING RETURNING proposal_id", pid,
            account_id, agent_id, strategy,
            change_class, json.dumps(proposed_change, default=str),
            rationale, list(source_lesson_ids or []), proposed_by,
            L._ts(proposed_at), L._ts(training[0]), L._ts(training[1]),
            L._ts(evaluation[0]), L._ts(evaluation[1]),
            json.dumps(protocol, default=str))
    except Exception as exc:                                    # noqa: BLE001
        # THE ONE-OPEN-PROPOSAL INDEX: refused by name, the open one named
        if type(exc).__name__ == "UniqueViolationError":
            prior = await conn.fetchval(
                "SELECT proposal_id FROM paper_improvement_proposals WHERE "
                " account_id=$1 AND agent_id=$2 AND change_class=$3 AND "
                " coalesce(strategy, '') = coalesce($4, '') AND status IN "
                " ('AWAITING_FORWARD_DATA', 'INSUFFICIENT_FORWARD_DATA')",
                account_id, agent_id, change_class, strategy)
            return {"ok": False, "refusal": R_OPEN_EXISTS,
                    "proposal_id": prior}
        raise
    if got is None:
        return {"ok": True, "created": False, "proposal_id": pid}
    await _proposal_event(conn, pid, kind="PROPOSED", actor=proposed_by,
                          detail={"proposed_change": proposed_change,
                                  "protocol": protocol},
                          now=proposed_at)
    return {"ok": True, "created": True, "proposal_id": pid,
            "protocol": protocol}


async def proposal(conn, proposal_id: str) -> dict | None:
    return _rec(await conn.fetchrow(
        "SELECT * FROM paper_improvement_proposals WHERE proposal_id=$1",
        proposal_id))


async def _settled_outcome(conn, slug: str, holding_side: str,
                           now: float) -> dict:
    from . import paper_xavier as PX
    rows = [dict(r) for r in await conn.fetch(
        "SELECT id, buy_intent, outcome, outcome_known, outcome_basis, "
        "       outcome_at FROM external_valuations WHERE us_market_slug=$1 "
        "   AND outcome_basis IS NOT NULL AND (outcome_at IS NULL OR "
        "   outcome_at <= $2) ORDER BY id", slug, L._ts(now))]
    return PX.outcome_for(rows, holding_side=holding_side)


async def _eval_edge(conn, p: dict, *, until: float, now: float) -> dict:
    """THE EDGE-THRESHOLD EVALUATION, on the evaluation period's forward
    records only, in the direction of the proposed change:

      TIGHTEN (to > from)  the decisions ADMITTED in the period whose best-
                           level edge is below the proposed threshold -- the
                           trades the change would have removed. Removing
                           them improves results when they lose: the
                           metric is MINUS their mean result.
      LOOSEN (to < from)   the decisions refused ONLY for the edge whose edge
                           meets the proposed threshold -- trades never
                           placed. Metric: their mean result.

    Result per contract = settled payout - the decision-time best displayed
    price - the per-contract fee of the deployed schedule. Either way the
    comparison is against a course NOT taken: COUNTERFACTUAL_NOT_EXECUTABLE_
    PROOF, never proof the alternative would have executed."""
    from . import derek_policy as DP
    ch = p["proposed_change"] or {}
    frm, new = float(ch["from"]), float(ch["to"])
    tighten = new > frm
    rows = [_rec(r) for r in await conn.fetch(
        "SELECT decision_id, decided_at, verdict, refusals, us_market_slug, "
        "       holding_side, book, policy_decision FROM paper_decisions "
        " WHERE account_id=$1 AND strategy=$2 AND decided_at >= $3 "
        "   AND decided_at < $4 ORDER BY decided_at, decision_id",
        p["account_id"], p["strategy"], L._ts(p["evaluation_start"]),
        L._ts(min(until, p["evaluation_end"])))]
    cand, settled, results, unsettled, unpriced = [], [], [], 0, 0
    for r in rows:
        refs = list(r.get("refusals") or [])
        edge = _f((r.get("policy_decision") or {}).get("gross_edge_pp"))
        lv = (r.get("book") or {}).get("levels") or []
        if edge is None or not lv:
            continue
        if tighten:
            hit = (r.get("verdict") == "ENTER" and not refs
                   and edge < new - 1e-9)
        else:
            hit = refs == [DP.R_BELOW] and edge >= new - 1e-9
        if not hit:
            continue
        cand.append(r["decision_id"])
        oc = await _settled_outcome(conn, r["us_market_slug"],
                                    r["holding_side"], now)
        if oc.get("outcome") is None:
            unsettled += 1
            continue
        px = float(lv[0]["price"])
        pay = {"WON": 1.0, "LOST": 0.0, "VOID_REFUND": px}[oc["outcome"]]
        try:
            fee = float(L._fee(None, 1, px, r["decided_at"]))
        except Exception:                                       # noqa: BLE001
            # A FEE THE SCHEDULE WILL NOT STATE IS NOT A ZERO FEE: the
            # decision is counted as unpriced, never scored on a guess.
            unpriced += 1
            continue
        res = round(pay - px - fee, 6)
        settled.append(r["decision_id"])
        results.append({"decision_id": r["decision_id"],
                        "outcome": oc["outcome"], "price": px,
                        "edge_pp": edge, "fee_usd": fee,
                        "result_per_contract_usd": res})
    mean = (None if not results else
            round(sum(x["result_per_contract_usd"] for x in results)
                  / len(results), 6))
    return {"n": len(settled), "counts": {
        "direction": "TIGHTEN" if tighten else "LOOSEN",
        "evaluation_period_decisions": len(rows),
        "affected_by_the_proposal": len(cand),
        "affected_settled": len(settled),
        "affected_unsettled": unsettled,
        "affected_fee_unpriced": unpriced},
        "metric": (None if mean is None else
                   round(-mean if tighten else mean, 6)),
        "affected_mean_result_per_contract_usd": mean,
        "label": COUNTERFACTUAL, "executable": False,
        "basis": (("decisions ADMITTED in the evaluation period whose best-"
                   "level edge is below the proposed threshold (the trades "
                   "the tightening removes); metric = minus their mean "
                   "per-contract result. Not trading them is the course not "
                   "taken") if tighten else
                  ("decisions refused ONLY for the edge threshold whose "
                   "recorded best-level edge meets the proposed threshold; "
                   "metric = their mean per-contract result. These orders "
                   "were never placed: not proof they would have filled")),
        "records": {"paper_decisions": settled}, "results": results[:50]}


async def _eval_stale_exit(conn, p: dict, *, until: float, now: float
                           ) -> dict:
    """THE STALE-MEASURE EXIT EVALUATION, on the evaluation period's
    forward records only. The exits were NOT taken: COUNTERFACTUAL."""
    es, ee = p["evaluation_start"], min(until, p["evaluation_end"])
    revs = [_rec(r) for r in await conn.fetch(
        "SELECT review_id, group_id, reviewed_at, measure, alternatives "
        "  FROM paper_xavier_reviews WHERE account_id=$1 "
        "   AND ($2::text IS NULL OR strategy=$2) AND reviewed_at >= $3 "
        "   AND reviewed_at < $4 ORDER BY reviewed_at, review_id",
        p["account_id"], p["strategy"], L._ts(es), L._ts(ee))]
    first: dict = {}
    for r in revs:
        if (r.get("measure") or {}).get("stale") and r["group_id"] not in \
                first:
            first[r["group_id"]] = r
    pos = {q["group_id"]: q for q in await L.positions(
        conn, p["account_id"], include_closed=True)}
    results, open_n = [], 0
    for g, r in first.items():
        q = pos.get(g)
        cf = _exit_counterfactual(r)
        closed_at = None if q is None else (
            (q.get("settlement") or {}).get("settled_at")
            or q.get("last_fill_at"))
        if q is None or float(q["open_qty"]) > 1e-9 or cf is None or \
                closed_at is None or closed_at > now:
            open_n += 1
            continue
        results.append({"group_id": g, "review_id": r["review_id"],
                        "exit_net_usd": cf,
                        "realized_pnl_usd": q["realized_pnl_usd"],
                        "exit_minus_realized_usd": round(
                            cf - float(q["realized_pnl_usd"]), 6)})
    mean = (None if not results else round(sum(
        x["exit_minus_realized_usd"] for x in results) / len(results), 6))
    return {"n": len(results), "counts": {
        "evaluation_period_reviews": len(revs),
        "positions_with_a_stale_review": len(first),
        "closed_and_comparable": len(results),
        "open_or_not_comparable": open_n},
        "metric": mean, "label": COUNTERFACTUAL, "executable": False,
        "basis": ("full exit at the first stale-measure review of the "
                  "evaluation period, at that review's displayed exit walk "
                  "after fees, minus the realized result; the exit was not "
                  "taken, so its fill is not established"),
        "records": {"paper_xavier_reviews": [x["review_id"]
                                             for x in results]},
        "results": results[:50]}


_EVALUATORS = {C_EDGE: _eval_edge, C_STALE_EXIT: _eval_stale_exit}


async def run_evaluation(conn, p: dict, *, now: float) -> dict:
    """EVALUATE ONE PROPOSAL'S PROTOCOL (no write). Refuses an overlapping
    or peeking protocol by name; reports INSUFFICIENT_FORWARD_DATA with the
    counts until the evaluation period has ended with enough outcomes."""
    ref = check_protocol(training_start=p["training_start"],
                         training_end=p["training_end"],
                         evaluation_start=p["evaluation_start"],
                         evaluation_end=p["evaluation_end"],
                         proposed_at=p["proposed_at"])
    if ref:
        return {"ok": False, "refusal": ref, "evaluated": False}
    fn = _EVALUATORS.get(p["change_class"])
    if fn is None:
        return {"ok": False, "refusal": R_UNKNOWN_CLASS, "evaluated": False}
    need = int((p.get("protocol") or {}).get("min_evaluation_outcomes")
               or CHANGE_CLASSES[p["change_class"]][
                   "min_evaluation_outcomes"])
    got = await fn(conn, p, until=float(now), now=float(now))
    base = {"ok": True, "at": float(now),
            "training_period": [p["training_start"], p["training_end"]],
            "evaluation_period": [p["evaluation_start"],
                                  p["evaluation_end"]],
            "training_records_used": 0,
            "min_evaluation_outcomes": need, "counts": got["counts"],
            "outcomes": got["n"], "metric": got["metric"],
            "label": got["label"], "executable": got["executable"],
            "basis": got["basis"], "records": got["records"],
            "results": got["results"]}
    if float(now) < float(p["evaluation_end"]):
        return dict(base, evaluated=False, status=S_INSUFFICIENT,
                    reason=R_PERIOD_OPEN,
                    why=("the evaluation period ends at %s; it is scored "
                         "once, after it ends (%d of %d outcomes so far)"
                         % (p["evaluation_end"], got["n"], need)))
    if got["n"] < need:
        return dict(base, evaluated=False, status=S_INSUFFICIENT,
                    reason=R_TOO_FEW,
                    why=("%d forward outcomes in the evaluation period; the "
                         "protocol needs %d" % (got["n"], need)))
    m = float(got["metric"])
    verdict = V_PASS if m > 0 else V_HARM if m < 0 else V_NO_GAIN
    return dict(base, evaluated=True, status=S_EVALUATED, verdict=verdict,
                evaluation_id="papereval:%s" % _h(
                    p["proposal_id"], float(now), m, got["n"],
                    _sha(got["records"])))


async def evaluate_proposal(conn, proposal_id: str, *, now: float) -> dict:
    """ATTEMPT ONE PROPOSAL'S EVALUATION AND RECORD IT: INSUFFICIENT_FORWARD_
    DATA (with the counts, retried later) or the write-once result."""
    p = await proposal(conn, proposal_id)
    if p is None:
        return {"ok": False, "refusal": R_NO_PROPOSAL}
    if p["status"] == S_EVALUATED:
        return {"ok": True, "already_evaluated": True,
                "verdict": p["verdict"], "evaluation": p["evaluation"]}
    if p["status"] == "WITHDRAWN":
        return {"ok": False, "refusal": "THE_PROPOSAL_WAS_WITHDRAWN"}
    got = await run_evaluation(conn, p, now=now)
    if not got.get("ok"):
        await _proposal_event(conn, proposal_id, kind="EVALUATION_REFUSED",
                              actor=EVALUATOR, detail=got, now=now)
        return got
    if not got["evaluated"]:
        prev = p.get("last_attempt") or {}
        changed = (p["status"] != S_INSUFFICIENT
                   or prev.get("reason") != got.get("reason")
                   or prev.get("counts") != got.get("counts"))
        await conn.execute(
            "UPDATE paper_improvement_proposals SET status=$2, "
            " last_attempt=$3::jsonb, updated_at=now() WHERE proposal_id=$1",
            proposal_id, S_INSUFFICIENT, json.dumps(got, default=str))
        if not changed:
            # the same counts and reason as the last attempt: the attempt
            # is on the row; the history keeps only what changed
            return got
        await _proposal_event(conn, proposal_id, kind=S_INSUFFICIENT,
                              actor=EVALUATOR, detail={
                                  k: got.get(k) for k in (
                                      "reason", "why", "counts", "outcomes",
                                      "min_evaluation_outcomes")}, now=now)
        return got
    await conn.execute(
        "UPDATE paper_improvement_proposals SET status=$2, evaluation="
        " $3::jsonb, verdict=$4, evaluated_at=$5, evaluated_by=$6, "
        " last_attempt=$3::jsonb, updated_at=now() WHERE proposal_id=$1",
        proposal_id, S_EVALUATED, json.dumps(got, default=str),
        got["verdict"], L._ts(now), EVALUATOR)
    await _proposal_event(conn, proposal_id, kind="EVALUATED",
                          actor=EVALUATOR, detail={
                              "verdict": got["verdict"],
                              "metric": got["metric"],
                              "outcomes": got["outcomes"],
                              "label": got["label"]}, now=now)
    return got


async def activation_control(conn) -> dict:
    try:
        r = await conn.fetchrow(
            "SELECT enabled, why, updated_by, updated_at FROM paper_control "
            " WHERE control_key=$1", ACTIVATION_CONTROL_KEY)
    except Exception as exc:                                    # noqa: BLE001
        return {"control_key": ACTIVATION_CONTROL_KEY, "enabled": False,
                "why": "unreadable: %s" % type(exc).__name__}
    if r is None:
        return {"control_key": ACTIVATION_CONTROL_KEY, "enabled": False,
                "why": "THE_ACTIVATION_CONTROL_ROW_IS_ABSENT"}
    return {"control_key": ACTIVATION_CONTROL_KEY,
            "enabled": bool(r["enabled"]), "why": r["why"],
            "updated_by": r["updated_by"],
            "updated_at": L._epoch(r["updated_at"])}


def _human(name) -> str | None:
    """A named human: not empty, not an agent, not an evaluator."""
    who = str(name or "").strip()
    if not who or who.upper() in AGENT_IDS or who.upper().startswith(
            ("EVALUATOR", "AGENT")):
        return None
    return who


def validate_parameters(params) -> str | None:
    """THE WHITELIST AND BOUNDS of the completed-game policy's parameters
    (pure; the same rule as paper_benchmark.validate_cg_parameters and the
    migration 186 CHECK -- a test pins the three equal)."""
    if not isinstance(params, dict) or set(params) != set(PARAM_BOUNDS):
        return R_NOT_WHITELISTED
    for k, (lo, hi) in PARAM_BOUNDS.items():
        v = params.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return R_OUT_OF_BOUNDS
        if not (lo - 1e-9 <= float(v) <= hi + 1e-9):
            return R_OUT_OF_BOUNDS
        steps = float(v) / PARAM_GRID_PP
        if abs(steps - round(steps)) > 1e-9:
            return R_OUT_OF_BOUNDS
    return None


def check_change(change_class: str, strategy, change: dict) -> str | None:
    """THE ONE SUPPORTED, BOUNDED PAPER-POLICY CHANGE (pure): the edge
    threshold of the completed-game policy, to a whitelisted, in-bounds,
    on-grid value at most PARAM_MAX_STEP_PP from where it starts."""
    if change_class != C_EDGE:
        return None                       # not a parameter change class
    if strategy != PARAM_POLICY:
        return R_CHANGE_NOT_SUPPORTED
    if (change or {}).get("parameter") not in PARAM_BOUNDS:
        return R_NOT_WHITELISTED
    try:
        frm, to = float(change["from"]), float(change["to"])
    except (KeyError, TypeError, ValueError):
        return R_OUT_OF_BOUNDS
    bad = (validate_parameters({change["parameter"]: to})
           or validate_parameters({change["parameter"]: frm}))
    if bad:
        return bad
    if abs(to - frm) > PARAM_MAX_STEP_PP + 1e-9 or abs(to - frm) < 1e-9:
        return R_STEP_TOO_LARGE
    return None


async def _active_parameters(conn, policy_key: str, *, lock: bool = False):
    return await conn.fetchrow(
        "SELECT h.policy_key, h.active_version_id, h.activation_id, "
        "       v.version_no, v.params, v.proposal_id, v.evaluation_id "
        "  FROM paper_policy_parameter_heads h "
        "  JOIN paper_policy_parameter_versions v "
        "    ON v.version_id = h.active_version_id "
        " WHERE h.policy_key = $1" + (" FOR UPDATE OF h" if lock else ""),
        policy_key)


def activation_checks(p: dict, active_value) -> list:
    """THE DEFINED EVALUATION CHECKS a proposal must pass before a human may
    activate it (pure). Each is named with its values."""
    ev = p.get("evaluation") or {}
    prot = p.get("protocol") or {}
    need = int(prot.get("min_evaluation_outcomes") or CHANGE_CLASSES.get(
        p.get("change_class"), {}).get("min_evaluation_outcomes") or 0)
    ch = p.get("proposed_change") or {}
    split = check_protocol(training_start=p["training_start"],
                           training_end=p["training_end"],
                           evaluation_start=p["evaluation_start"],
                           evaluation_end=p["evaluation_end"],
                           proposed_at=p["proposed_at"])
    chg = check_change(p.get("change_class"), p.get("strategy"), ch)
    out = [
        {"check": "SUPPORTED_BOUNDED_CHANGE", "passed": chg is None
         and p.get("change_class") == C_EDGE, "refusal": chg,
         "policy_key": p.get("strategy"), "change": ch,
         "bounds": PARAM_BOUNDS, "max_step_pp": PARAM_MAX_STEP_PP},
        {"check": "TRAINING_STRICTLY_BEFORE_EVALUATION",
         "passed": split is None, "refusal": split},
        {"check": "EVALUATED_ONCE_WITH_A_PASS",
         "passed": (p.get("status") == S_EVALUATED
                    and p.get("verdict") == V_PASS
                    and ev.get("evaluated") is True
                    and ev.get("verdict") == V_PASS),
         "status": p.get("status"), "verdict": p.get("verdict")},
        {"check": "FORWARD_OUTCOMES_ONLY",
         "passed": (ev.get("training_records_used") == 0
                    and _f(ev.get("at")) is not None
                    and float(ev["at"]) >= float(p["evaluation_end"])
                    and [float(x) for x in ev.get("evaluation_period")
                         or [0, 0]] == [float(p["evaluation_start"]),
                                        float(p["evaluation_end"])]),
         "training_records_used": ev.get("training_records_used"),
         "evaluated_at": ev.get("at")},
        {"check": "MINIMUM_FORWARD_OUTCOMES",
         "passed": need > 0 and int(ev.get("outcomes") or 0) >= need,
         "outcomes": ev.get("outcomes"), "required": need},
        {"check": "EVALUATOR_IS_NOT_THE_PROPOSER",
         "passed": bool(p.get("evaluated_by"))
         and p.get("evaluated_by") != p.get("proposed_by")},
        {"check": "EVALUATION_IDENTIFIED",
         "passed": bool(ev.get("evaluation_id")),
         "evaluation_id": ev.get("evaluation_id")},
        {"check": "STARTS_FROM_THE_ACTIVE_VALUE",
         "passed": (active_value is not None and _f(ch.get("from"))
                    is not None and abs(float(ch["from"])
                                        - float(active_value)) < 1e-9),
         "active_value": active_value, "from": ch.get("from")}]
    return out


async def activate_proposal(conn, proposal_id: str, *, approver: str,
                            now: float | None = None) -> dict:
    """THE EXPLICIT, AUDITED ACTIVATION OF A PASSED PAPER PROPOSAL -- the
    only way a parameter version becomes ACTIVE. Never called by any step.
    It needs the PAPER_LEARNING_PROPOSAL_ACTIVATION control row on, a named
    human approver who is not the proposer, and every activation check
    (supported bounded change, split, PASS, forward-only, minimum outcomes,
    independent evaluator, evaluation id, starts from the active value).
    In ONE transaction under the policy head's row lock: a new IMMUTABLE
    version (the proposal, its evaluation id and the approver), the audit
    row, the head moved to it, the proposal marked active (any proposal it
    supersedes marked inactive). PAPER ONLY: the completed-game paper policy
    reads it on its next decision."""
    at = float(now if now is not None else time.time())
    ctl = await activation_control(conn)
    if not ctl["enabled"]:
        return {"ok": False, "refusal": R_ACTIVATION_OFF, "control": ctl}
    if not str(approver or "").strip():
        return {"ok": False, "refusal": R_NO_APPROVER}
    who = _human(approver)
    if who is None:
        return {"ok": False, "refusal": R_AGENT_APPROVER}
    p = await proposal(conn, proposal_id)
    if p is None:
        return {"ok": False, "refusal": R_NO_PROPOSAL}
    if who == p["proposed_by"]:
        return {"ok": False, "refusal": R_SELF_APPROVER}
    if p["status"] != S_EVALUATED or p["verdict"] != V_PASS:
        return {"ok": False, "refusal": R_NOT_PASSED,
                "status": p["status"], "verdict": p["verdict"]}
    if p.get("active"):
        return {"ok": False, "refusal": R_ALREADY_ACTIVE}
    ch = p["proposed_change"]
    async with conn.transaction():
        head = await _active_parameters(conn, PARAM_POLICY, lock=True)
        if head is None:
            return {"ok": False, "refusal": R_NO_PARAMETER_HEAD}
        cur = L._j(head["params"]) or {}
        checks = activation_checks(p, cur.get(ch.get("parameter")))
        if not all(c["passed"] for c in checks):
            return {"ok": False, "refusal": R_ACTIVATION_CHECKS,
                    "failed": [c["check"] for c in checks
                               if not c["passed"]], "checks": checks}
        new = dict(cur, **{ch["parameter"]: float(ch["to"])})
        bad = validate_parameters(new)
        if bad:
            return {"ok": False, "refusal": bad}
        no = int(await conn.fetchval(
            "SELECT max(version_no) FROM paper_policy_parameter_versions "
            " WHERE policy_key=$1", PARAM_POLICY)) + 1
        vid = "paperparam:%s:V%d" % (PARAM_POLICY, no)
        eid = p["evaluation"]["evaluation_id"]
        body = json.dumps(new, sort_keys=True)
        await conn.execute(
            "INSERT INTO paper_policy_parameter_versions (version_id, "
            " policy_key, version_no, params, params_sha256, source, "
            " proposal_id, evaluation_id, approved_by, created_at) VALUES "
            " ($1,$2,$3,$4::jsonb,$5,'EVALUATED_PROPOSAL',$6,$7,$8,$9)",
            vid, PARAM_POLICY, no, body,
            hashlib.sha256(body.encode()).hexdigest(), proposal_id, eid,
            who, L._ts(at))
        aid = "paperact:%s" % _h(PARAM_POLICY, vid, "ACTIVATE", at)
        await conn.execute(
            "INSERT INTO paper_policy_parameter_activations (activation_id, "
            " policy_key, kind, version_id, previous_version_id, "
            " proposal_id, evaluation_id, actor, reason, control, at) "
            "VALUES ($1,$2,'ACTIVATE',$3,$4,$5,$6,$7,$8,$9::jsonb,$10)",
            aid, PARAM_POLICY, vid, head["active_version_id"], proposal_id,
            eid, who, "activation of an evaluated PASS (%s: %s -> %s)" % (
                ch["parameter"], ch["from"], ch["to"]),
            json.dumps(ctl, default=str), L._ts(at))
        await conn.execute(
            "UPDATE paper_policy_parameter_heads SET active_version_id=$2, "
            " activation_id=$3, updated_at=now() WHERE policy_key=$1",
            PARAM_POLICY, vid, aid)
        await conn.execute(
            "UPDATE paper_improvement_proposals SET active=FALSE, "
            " deactivated_at=$2, updated_at=now() WHERE active AND "
            " strategy=$1 AND change_class=$3", PARAM_POLICY, L._ts(at),
            C_EDGE)
        await conn.execute(
            "UPDATE paper_improvement_proposals SET active=TRUE, "
            " activated_by=$2, activated_at=$3, deactivated_at=NULL, "
            " updated_at=now() WHERE proposal_id=$1", proposal_id, who,
            L._ts(at))
        await _proposal_event(conn, proposal_id, kind="ACTIVATED", actor=who,
                              detail={"control": ctl, "scope": "PAPER_ONLY",
                                      "version_id": vid, "activation_id": aid,
                                      "previous_version_id":
                                          head["active_version_id"],
                                      "evaluation_id": eid, "params": new,
                                      "checks": checks}, now=at)
    return {"ok": True, "proposal_id": proposal_id, "active": True,
            "activated_by": who, "scope": "PAPER_ONLY", "version_id": vid,
            "version_no": no, "params": new, "activation_id": aid,
            "previous_version_id": head["active_version_id"],
            "evaluation_id": eid, "checks": checks}


async def rollback_policy_parameters(conn, *, actor: str, reason: str,
                                     policy_key: str = None,
                                     now: float | None = None) -> dict:
    """RESTORE THE VERSION THAT WAS ACTIVE BEFORE THE CURRENT ONE, atomically
    and audited: under the head's row lock, the version that introduced the
    current one names its predecessor; a ROLLBACK row records both, the head
    moves back, and the rolled-back proposal is marked inactive (the restored
    version's proposal, if any, active again).

    OWNER RULES: it stays available while the activation control is OFF
    (restoring is always permitted); it needs authenticated command access
    (the route), a named operator and a reason, and writes an audit row; it
    restores ONLY a previously APPROVED configuration (the shipped default
    or a version approved from an evaluated proposal); and it NEVER touches
    paper_control -- a disabled strategy stays disabled, the activation
    control keeps its state. The shipped default has nothing before it."""
    pk = policy_key or PARAM_POLICY
    at = float(now if now is not None else time.time())
    who = _human(actor)
    if who is None:
        return {"ok": False, "refusal": R_AGENT_APPROVER
                if str(actor or "").strip() else R_NO_APPROVER}
    if not str(reason or "").strip():
        return {"ok": False, "refusal": R_ROLLBACK_NEEDS_REASON}
    async with conn.transaction():
        head = await _active_parameters(conn, pk, lock=True)
        if head is None:
            return {"ok": False, "refusal": R_NO_PARAMETER_HEAD}
        cur = head["active_version_id"]
        intro = await conn.fetchrow(
            "SELECT activation_id, previous_version_id FROM "
            " paper_policy_parameter_activations WHERE policy_key=$1 "
            "   AND version_id=$2 AND kind IN ('ACTIVATE', "
            "   'SHIPPED_DEFAULT', 'OWNER_DECISION') "
            " ORDER BY at DESC, recorded_at DESC "
            " LIMIT 1", pk, cur)
        prev = None if intro is None else intro["previous_version_id"]
        if prev is None:
            return {"ok": False, "refusal": R_NOTHING_TO_ROLL_BACK,
                    "active_version_id": cur}
        restored = await conn.fetchrow(
            "SELECT version_id, proposal_id, evaluation_id, params, source, "
            "       approved_by FROM paper_policy_parameter_versions "
            " WHERE version_id=$1", prev)
        approved = restored is not None and (
            restored["source"] == "SHIPPED_DEFAULT"
            or (restored["source"] == "OWNER_DECISION"
                and restored["approved_by"])
            or (restored["source"] == "EVALUATED_PROPOSAL"
                and restored["approved_by"] and restored["evaluation_id"]))
        if not approved or validate_parameters(
                L._j(restored["params"])) is not None:
            return {"ok": False, "refusal": R_NOT_AN_APPROVED_CONFIGURATION,
                    "version_id": prev}
        aid = "paperact:%s" % _h(pk, prev, "ROLLBACK", cur, at)
        await conn.execute(
            "INSERT INTO paper_policy_parameter_activations (activation_id, "
            " policy_key, kind, version_id, previous_version_id, "
            " proposal_id, evaluation_id, actor, reason, control, at) "
            "VALUES ($1,$2,'ROLLBACK',$3,$4,$5,$6,$7,$8,$9::jsonb,$10)",
            aid, pk, prev, cur, head["proposal_id"], head["evaluation_id"],
            who, str(reason), json.dumps(await activation_control(conn),
                                         default=str), L._ts(at))
        await conn.execute(
            "UPDATE paper_policy_parameter_heads SET active_version_id=$2, "
            " activation_id=$3, updated_at=now() WHERE policy_key=$1",
            pk, prev, aid)
        if head["proposal_id"]:
            await conn.execute(
                "UPDATE paper_improvement_proposals SET active=FALSE, "
                " deactivated_at=$2, updated_at=now() WHERE proposal_id=$1",
                head["proposal_id"], L._ts(at))
            await _proposal_event(conn, head["proposal_id"],
                                  kind="ROLLED_BACK", actor=who, detail={
                                      "from_version_id": cur,
                                      "to_version_id": prev,
                                      "activation_id": aid,
                                      "reason": str(reason)}, now=at)
        if restored["proposal_id"]:
            await conn.execute(
                "UPDATE paper_improvement_proposals SET active=TRUE, "
                " deactivated_at=NULL, updated_at=now() WHERE "
                " proposal_id=$1 AND activated_by IS NOT NULL",
                restored["proposal_id"])
    return {"ok": True, "policy_key": pk, "rolled_back_version_id": cur,
            "active_version_id": prev, "activation_id": aid,
            "params": L._j(restored["params"]), "actor": who}


async def policy_parameter_state(conn, policy_key: str = None) -> dict:
    """FOR MANAGEMENT: the ACTIVE version (values and provenance), every
    version, the CANDIDATES (proposals of the supported change, with their
    evaluation status -- INSUFFICIENT_FORWARD_DATA stated as such -- and the
    activation checks they would face now), and the activation history."""
    pk = policy_key or PARAM_POLICY
    if not await conn.fetchval(
            "SELECT to_regclass('paper_policy_parameter_heads') IS NOT NULL"):
        return {"policy_key": pk, "status": "UNAVAILABLE",
                "why": "MIGRATION_186_IS_NOT_APPLIED"}
    head = await _active_parameters(conn, pk)
    active_vals = None if head is None else L._j(head["params"])
    versions = [_rec(r) for r in await conn.fetch(
        "SELECT version_id, version_no, params, params_sha256, source, "
        "       proposal_id, evaluation_id, approved_by, created_at "
        "  FROM paper_policy_parameter_versions WHERE policy_key=$1 "
        " ORDER BY version_no", pk)]
    for v in versions:
        v["state"] = ("ACTIVE" if head is not None and v["version_id"]
                      == head["active_version_id"] else "INACTIVE")
    history = [_rec(r) for r in await conn.fetch(
        "SELECT activation_id, kind, version_id, previous_version_id, "
        "       proposal_id, evaluation_id, actor, reason, control, at "
        "  FROM paper_policy_parameter_activations WHERE policy_key=$1 "
        " ORDER BY at DESC, recorded_at DESC LIMIT 100", pk)]
    cands = []
    for r in await conn.fetch(
            "SELECT * FROM paper_improvement_proposals WHERE strategy=$1 "
            "   AND change_class=$2 ORDER BY proposed_at DESC LIMIT 50",
            pk, C_EDGE):
        p = _rec(r)
        ch = p["proposed_change"] or {}
        checks = activation_checks(p, (active_vals or {}).get(
            ch.get("parameter")))
        cands.append({
            "proposal_id": p["proposal_id"], "account_id": p["account_id"],
            "proposed_change": ch, "candidate_params": (
                None if active_vals is None else dict(
                    active_vals, **{ch.get("parameter"): ch.get("to")})),
            "status": p["status"], "verdict": p.get("verdict"),
            "evaluation_id": (p.get("evaluation") or {}).get(
                "evaluation_id"),
            "evaluation_counts": (p.get("last_attempt") or {}).get("counts"),
            "evaluation_reason": (p.get("last_attempt") or {}).get("reason"),
            "evaluation_period": [p["evaluation_start"],
                                  p["evaluation_end"]],
            "training_period": [p["training_start"], p["training_end"]],
            "active": bool(p.get("active")),
            "activated_by": p.get("activated_by"),
            "activatable_now": all(c["passed"] for c in checks),
            "failed_checks": [c["check"] for c in checks
                              if not c["passed"]]})
    return {"policy_key": pk, "parameter": list(PARAM_BOUNDS),
            "bounds": {k: list(v) for k, v in PARAM_BOUNDS.items()},
            "grid_pp": PARAM_GRID_PP, "max_step_pp": PARAM_MAX_STEP_PP,
            "shipped_default": dict(PARAM_DEFAULTS),
            "floor_justification": PARAM_FLOOR_WHY,
            "active": (None if head is None else {
                "version_id": head["active_version_id"],
                "version_no": head["version_no"], "values": active_vals,
                "proposal_id": head["proposal_id"],
                "evaluation_id": head["evaluation_id"],
                "activation_id": head["activation_id"]}),
            "versions": versions, "candidates": cands,
            "activation_history": history,
            "activation_control": await activation_control(conn),
            "fallback": ("if the active version cannot be read or is out of "
                         "bounds, the policy runs the shipped default V1 "
                         "and records why on each decision"),
            "funded": "NONE: no funded module reads these parameters"}


async def _propose_from_lesson(conn, *, account_id, les, lesson_id,
                               now) -> dict | None:
    hint = les.get("proposal")
    if not hint:
        return None
    cls = CHANGE_CLASSES[hint["change_class"]]
    recent = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM paper_improvement_proposals WHERE "
        " account_id=$1 AND agent_id=$2 AND change_class=$3 AND "
        " coalesce(strategy, '') = coalesce($4, '') AND (status IN "
        " ('AWAITING_FORWARD_DATA', 'INSUFFICIENT_FORWARD_DATA') OR "
        " proposed_at > $5))", account_id, les["agent_id"],
        hint["change_class"], les.get("strategy"),
        L._ts(float(now) - PROPOSAL_COOLDOWN_S))
    if recent:
        return {"created": False, "why": "A_RECENT_OR_OPEN_PROPOSAL_EXISTS"}
    # TRAINING: the lesson's window, through this instant. The proposal is
    # recorded a moment later and its EVALUATION starts then: a record
    # decided at this instant belongs to the training side only.
    te = float(now) + PROPOSAL_EPSILON_S
    pa = te
    es = pa
    ee = es + float(cls["evaluation_days"]) * 86400.0
    return await create_proposal(
        conn, account_id=account_id, agent_id=les["agent_id"],
        strategy=les.get("strategy"), change_class=hint["change_class"],
        proposed_change=hint["proposed_change"],
        rationale=hint["rationale"], proposed_by=les["agent_id"],
        proposed_at=pa, training=(float(les["window_start"]), te),
        evaluation=(es, ee), source_lesson_ids=[lesson_id])


# ═════════════════════════════════════════════════════════════════════
# 6 · THE LEARNING STEP OF THE SCHEDULED PAPER PASS
# ═════════════════════════════════════════════════════════════════════

async def _last_run(conn, acct: str) -> float | None:
    v = await conn.fetchval("SELECT value FROM ingestion_state WHERE key=$1",
                            WATERMARK_KEY % acct)
    v = L._j(v) if v is not None else None
    return _f((v or {}).get("at")) if isinstance(v, dict) else None


async def _mark_run(conn, acct: str, at: float, summary: dict) -> None:
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        WATERMARK_KEY % acct, json.dumps(dict(summary, at=at),
                                         default=str)[:20000])


async def learn(conn, *, account_id: str, session_id: str | None,
                now: float) -> dict:
    """DERIVE AND RECORD LESSONS, PROPOSE FROM THEM, EVALUATE WHAT IS DUE.
    Idempotent: unchanged lessons write nothing; one open proposal per
    change; an evaluation is written once."""
    out: dict[str, Any] = {"lessons_written": 0, "lessons_unchanged": 0,
                           "tasks": 0, "proposals_created": 0,
                           "evaluations": {}, "errors": {}}
    lessons, errs = await derive_lessons(conn, account_id=account_id,
                                         now=now)
    out["errors"].update(errs)
    for les in lessons:
        try:
            w = await write_lesson(conn, account_id=account_id,
                                   session_id=session_id, les=les, now=now)
            if w["written"]:
                out["lessons_written"] += 1
                out["tasks"] += 1 if w.get("improvement_task_id") else 0
            else:
                out["lessons_unchanged"] += 1
            pr = await _propose_from_lesson(conn, account_id=account_id,
                                            les=les,
                                            lesson_id=w["lesson_id"],
                                            now=now)
            if pr and pr.get("created"):
                out["proposals_created"] += 1
        except Exception as exc:                                # noqa: BLE001
            out["errors"][series_key(les)] = "%s: %s" % (
                type(exc).__name__, str(exc)[:200])
    for r in await conn.fetch(
            "SELECT proposal_id FROM paper_improvement_proposals WHERE "
            " account_id=$1 AND status IN ('AWAITING_FORWARD_DATA', "
            " 'INSUFFICIENT_FORWARD_DATA') ORDER BY proposed_at",
            account_id):
        try:
            g = await evaluate_proposal(conn, r["proposal_id"], now=now)
            k = g.get("status") or g.get("refusal") or "?"
            out["evaluations"][k] = out["evaluations"].get(k, 0) + 1
        except Exception as exc:                                # noqa: BLE001
            out["errors"][r["proposal_id"]] = "%s: %s" % (
                type(exc).__name__, str(exc)[:200])
    out["error_count"] = len(out["errors"])
    return out


async def step_learning(conn, ctx: dict) -> dict:
    """THE LEARNING STEP: at most once per LEARNING_EVERY_S per account
    (a database watermark, so restarts and several processes agree), and
    only while the pass has budget left."""
    if not await has_schema(conn):
        return {"ran": False, "why": "MIGRATION_185_IS_NOT_APPLIED"}
    acct = ctx["account_id"]
    now = float(ctx["now"])
    last = await _last_run(conn, acct)
    if last is not None and 0.0 <= now - last < LEARNING_EVERY_S:
        return {"ran": False, "why": "NOT_DUE", "last_run_at": last,
                "every_s": LEARNING_EVERY_S}
    if time.monotonic() > ctx.get("deadline", float("inf")):
        return {"ran": False, "why": "PASS_BUDGET_EXHAUSTED"}
    got = await learn(conn, account_id=acct, session_id=ctx.get("session_id"),
                      now=now)
    await _mark_run(conn, acct, now, {k: v for k, v in got.items()
                                      if not isinstance(v, (list,))})
    return dict(got, ran=True)


# ═════════════════════════════════════════════════════════════════════
# 7 · THE MANAGEMENT READS
# ═════════════════════════════════════════════════════════════════════

async def lessons(conn, *, account_id: str = L.ACCOUNT_ID,
                  agent: str | None = None, latest_only: bool = True,
                  limit: int = 200) -> list:
    rows = await conn.fetch(
        "SELECT * FROM paper_agent_lessons WHERE account_id=$1 "
        "   AND ($2::text IS NULL OR agent_id=$2) "
        " ORDER BY learned_at DESC, version DESC LIMIT $3", account_id,
        None if agent is None else agent.upper(), int(limit))
    out, seen = [], set()
    for r in rows:
        x = _rec(r)
        if latest_only and x["series_key"] in seen:
            continue
        seen.add(x["series_key"])
        out.append(x)
    return out


async def proposals(conn, *, account_id: str = L.ACCOUNT_ID,
                    agent: str | None = None, limit: int = 100) -> list:
    rows = await conn.fetch(
        "SELECT * FROM paper_improvement_proposals WHERE account_id=$1 "
        "   AND ($2::text IS NULL OR agent_id=$2) "
        " ORDER BY proposed_at DESC LIMIT $3", account_id,
        None if agent is None else agent.upper(), int(limit))
    out = []
    for r in rows:
        x = _rec(r)
        x["events"] = [_rec(e) for e in await conn.fetch(
            "SELECT at, kind, actor, detail FROM "
            " paper_improvement_proposal_events WHERE proposal_id=$1 "
            " ORDER BY event_id DESC LIMIT 20", x["proposal_id"])]
        out.append(x)
    return out


async def event_audits(conn, *, account_id: str = L.ACCOUNT_ID,
                       kind: str | None = None, limit: int = 100) -> list:
    return [_rec(r) for r in await conn.fetch(
        "SELECT finding_id, session_id, found_at, kind, severity, subject, "
        "       detail, improvement_task_id FROM paper_audrey_findings "
        " WHERE account_id=$1 AND kind = ANY($2::text[]) "
        " ORDER BY found_at DESC, finding_id LIMIT $3", account_id,
        [kind] if kind else list(EVENT_KINDS), int(limit))]


async def decision_record(conn, decision_id: str) -> dict:
    d = _rec(await conn.fetchrow(
        "SELECT * FROM paper_decisions WHERE decision_id=$1", decision_id))
    if d is None:
        return {"found": False, "decision_id": decision_id,
                "refusal": "NO_SUCH_PAPER_DECISION"}
    return {"found": True, "decision_id": decision_id,
            "record_audit": decision_record_audit(d), "record": d}


async def _tasks(conn, agent: str) -> list:
    return [_rec(r) for r in await conn.fetch(
        "SELECT task_id, assignee, kind, title, status, spec, evidence, "
        "       created_at, updated_at FROM agent_tasks "
        " WHERE kind=$1 AND assignee=$2 ORDER BY updated_at DESC LIMIT 25",
        TASK_KIND, agent)]


def _agent_view(agent: str, les: list, props: list) -> dict:
    latest = props[0] if props else None
    return {
        "agent_id": agent,
        "learned": [{"lesson_id": x["lesson_id"], "kind": x["kind"],
                     "strategy": x.get("strategy"),
                     "statement": x["statement"],
                     "learned_at": x["learned_at"], "version": x["version"],
                     "records": (x.get("provenance") or {}).get(
                         "record_count"),
                     "ids_sha256": (x.get("provenance") or {}).get(
                         "ids_sha256"),
                     "improvement_task_id": x.get("improvement_task_id")}
                    for x in les],
        "proposed_change": (
            {"status": "NONE",
             "why": ("no lesson of this agent has met a proposal rule yet "
                     "(rules: %s)" % {
                         DEREK: "%d edge near-misses within %.1f pp on the "
                                "completed-game policy, AND an in-bounds "
                                "change (the 0.5 pp floor is an owner "
                                "mandate, so a lowering is proposed only "
                                "from a tightened threshold)" % (
                                    NEAR_MISS_FOR_PROPOSAL, NEAR_MISS_PP),
                         XAVIER: "%d closed positions where the stale-"
                                 "measure exit beat the realized result "
                                 "(COUNTERFACTUAL)" % STALE_EXIT_FOR_PROPOSAL,
                         AUDREY: "Audrey's lessons open repair tasks; no "
                                 "parameter of hers is proposed from "
                                 "outcomes"}[agent])}
            if latest is None else {
                "proposal_id": latest["proposal_id"],
                "change_class": latest["change_class"],
                "strategy": latest.get("strategy"),
                "proposed_change": latest["proposed_change"],
                "rationale": latest["rationale"],
                "proposed_at": latest["proposed_at"],
                "training_period": [latest["training_start"],
                                    latest["training_end"]],
                "evaluation_period": [latest["evaluation_start"],
                                      latest["evaluation_end"]],
                "source_lesson_ids": latest.get("source_lesson_ids")}),
        "evaluation": (None if latest is None else {
            "status": latest["status"], "verdict": latest.get("verdict"),
            "result": latest.get("evaluation"),
            "last_attempt": {k: (latest.get("last_attempt") or {}).get(k)
                             for k in ("reason", "why", "counts",
                                       "outcomes", "min_evaluation_outcomes",
                                       "label", "at")}}),
        "active": bool(latest and latest.get("active")),
        "active_basis": ("ACTIVE needs an evaluated PASS meeting every "
                         "activation check, a named human approver and the "
                         "%s control row; an active proposal of the "
                         "supported change IS the completed-game policy's "
                         "running parameter version (see policy_parameters)"
                         % ACTIVATION_CONTROL_KEY),
        "proposals_total": len(props)}


async def learning_summary(conn, *, account_id: str = L.ACCOUNT_ID,
                           now: float | None = None) -> dict:
    """THE MANAGEMENT READ: per agent, what was learned, the proposed
    change, its evaluation, active or not -- each section OK / EMPTY /
    UNAVAILABLE (a failed read is never a zero)."""
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {
        "version": VERSION, "as_of": at, "account_id": account_id,
        "data_label": L.DATA_LABEL, "labels": dict(L.LABELS),
        "paper_only": ("real-money execution is disabled; lessons and "
                       "proposals are paper records and cannot reach "
                       "funded execution"),
        "counterfactual_label": COUNTERFACTUAL,
        "counterfactual_means": ("a result for an alternative that was "
                                 "never executed; not proof it could have "
                                 "been executed")}
    if not await has_schema(conn):
        un = {"status": "UNAVAILABLE", "why": "MIGRATION_185_IS_NOT_APPLIED",
              "data": None}
        out.update(agents={a: un for a in AGENT_IDS}, event_audits=un,
                   chains=un, activation_control=un, policy_parameters=un)
        return out
    agents = {}
    for a in AGENT_IDS:
        try:
            les = await lessons(conn, account_id=account_id, agent=a)
            props = await proposals(conn, account_id=account_id, agent=a)
            view = _agent_view(a, les, props)
        except Exception as exc:                                # noqa: BLE001
            agents[a] = _section(error=exc)
            continue
        view["improvement_tasks"] = await _safe(
            _tasks(conn, a), why_empty="NO_PAPER_LEARNING_TASK_FOR_%s" % a)
        agents[a] = _section(view, why_empty=(
            "NO_LESSON_YET: the forward records have not produced one for "
            "%s" % a), empty=not les and not props)
    out["agents"] = agents

    async def _ev_counts():
        rows = await conn.fetch(
            "SELECT kind, severity, count(*) AS n, max(found_at) AS last "
            "  FROM paper_audrey_findings WHERE account_id=$1 "
            "   AND kind = ANY($2::text[]) GROUP BY 1, 2 ORDER BY 1, 2",
            account_id, list(EVENT_KINDS))
        return [{"kind": r["kind"], "severity": r["severity"],
                 "n": int(r["n"]), "last_at": L._epoch(r["last"])}
                for r in rows]
    out["event_audits"] = await _safe(
        _ev_counts(), why_empty="NO_EVENT_AUDITED_YET")
    out["chains"] = await _safe(
        recent_chains(conn, account_id=account_id, limit=20),
        why_empty="NO_SIMULATED_FILL_YET")
    out["activation_control"] = await _safe(
        activation_control(conn), why_empty="ABSENT")
    # THE RUNNING PARAMETER VERSION OF THE COMPLETED-GAME PAPER POLICY: the
    # active version, every version, the candidates with their evaluation
    # status, and the activation / rollback history.
    pp = await _safe(policy_parameter_state(conn),
                     why_empty="NO_PARAMETER_STATE")
    if (pp.get("data") or {}).get("status") == "UNAVAILABLE":
        pp = {"status": "UNAVAILABLE", "why": pp["data"]["why"],
              "data": None}
    out["policy_parameters"] = pp
    return out


def describe() -> dict:
    return {"version": VERSION, "record_version": RECORD_VERSION,
            "event_kinds": list(EVENT_KINDS),
            "lesson_kinds": [L_REFUSALS, L_SETTLED, L_FILLS, L_EXCEPTIONAL,
                             L_MANAGEMENT, L_COVERAGE],
            "change_classes": {k: {kk: vv for kk, vv in v.items()}
                               for k, v in CHANGE_CLASSES.items()},
            "learning_every_s": LEARNING_EVERY_S,
            "lesson_window_s": LESSON_WINDOW_S,
            "activation_control_key": ACTIVATION_CONTROL_KEY,
            "counterfactual_label": COUNTERFACTUAL}
