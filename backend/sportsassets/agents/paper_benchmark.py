"""PINNACLE_ONLY_PAPER_BENCHMARK -- AN EXPERIMENTAL PAPER EXECUTION BENCHMARK.

PAPER ONLY, OWNER-AUTHORIZED. It decides on the existing fictional paper
account, inside the existing paper session, under the existing rails, and its
orders are rows in `paper_orders` filled by the paper simulator
(PAPER_SIM_V1). It is EXPERIMENTAL EXECUTION, NOT EVIDENCE OF QUALIFIED OR
PROVEN PROFITABILITY (`DISCLOSURE`, written on every record it produces).

WHAT IT DOES. For each entry-experiment valuation (`external_valuations` of
EXT_PINNACLE_DEVIG_V1_SHADOW, either purpose -- in production every row is
CALIBRATION_ONLY because of the P5 book-currency rule) it records ONE
decision labelled PINNACLE_ONLY_PAPER_BENCHMARK, beside -- never instead of
-- the original two-model decision on the same valuation (migration 182's
strategy key):

  probability   the STORED de-vigged Pinnacle probability of the event THIS
                contract pays on (`probability`, already oriented by the
                lane's single complement inversion), for the contract,
                payout event / complement and settlement terms of the row.
                Refused by name when the contract identity, the payout
                outcome match or the settlement terms are not established
                (the identity and settlement checks of the entry experiment,
                `derek_policy.evaluate` steps 1 and 3, unweakened), and
                PROBABILITY_EVIDENCE_STALE when the Pinnacle reading is
                older than the existing 30 s rule RE-AGED at this decision's
                own instant (`paper_derek._pinnacle`).
  price         NEVER the valuation's displayed quote. A CURRENT paper book
                observation read through the read-only market-data client at
                the decision (our receipt time, `paper_book_observations`):
                the consumed side's DISPLAYED depth only, on the adapter's
                cent grid, net of liquidity other paper orders consumed at
                that observation. PAPER_SIM_V1 has no depth-haircut
                parameter; displayed depth is the conservative maximum.
  edge          p_pinnacle - level price >= 0.05 (5.0 percentage points) AT
                EVERY LEVEL USED: the limit is the deepest level that still
                clears, so the simulator can never fill a level that does
                not (it walks within the limit on the first book observed
                at or after decision + the existing 2 s delay).
  EV            expected profit after fees > 0, with the fee function the
                simulator charges (`bettor_paper_ledger._fee`, the session's
                fee function or the deployed schedule) per walked level, and
                the measured void rate applied when there is one.
  size          the walked depth within the limit, capped by the session's
                target order size and per-order cap (reservation = limit x
                qty + max fees); every other rail -- hedge reserve, per
                market, per fixture, concurrent groups -- is
                `bettor_paper_ledger.submit_order`'s, unchanged.

REFUSALS ARE RECORDS TOO, with the exact shortfall (edge in pp against 5.0,
EV after fees, depth within the limit, Pinnacle and book ages): see
`research/pinnacle_benchmark_readback.sql`.

NEVER AN INTERNAL MODEL. p_internal is NULL, `internal_model` says
{available: false, reason}, p_blended is NULL. Pinnacle stays in
p_pinnacle / pinnacle.

P5 IS DISCLOSED, NOT RESOLVED. Every decision and order carries
book_currency = NOT_ESTABLISHED with its mechanism and basis: the venue read
does not establish that the displayed book is current; its age is bounded only
by our own receipt instant. Funded admission and the P5 rule are untouched.

THE SWITCH (default OFF). Both: the process environment PAPER_BENCHMARK in
(on, 1, true, yes) AND paper_control('PINNACLE_ONLY_PAPER_BENCHMARK').enabled
(migration 182 inserts it enabled: the environment flag is the operator
switch, the row the kill switch). It runs only inside the paper session
(PAPER_SESSION and its row on). With the flag unset nothing here runs: the
pass has no benchmark step and the per-valuation hook returns exactly as
before.

EVERY KEY IS STRATEGY-SPECIFIC. The decision id ('paperbench:' + sha of
session, valuation and STRATEGY) roots every other key: the group
('paperbenchgrp:'), the entry's idempotency key and order id, the fill keys
(order id + book observation + level), the position key (group), the handoff
key (group) and Xavier's management-order keys (group / position). The
two-model strategy keeps its own namespace ('paperdec:' / 'papergrp:'), and
migration 182's unique index is (session, valuation, strategy): neither
strategy can overwrite or suppress the other.

ONE CASH LEDGER. No bankroll of its own and no second funding: its orders
reserve on the account's one `paper_ledger` through
`bettor_paper_ledger.submit_order`, under the same account row lock as every
other paper order, so available cash is never committed twice across
strategies. Only this strategy is enabled for NEW paper entries; the
two-model strategy keeps recording its decisions with its entry switch
(paper_control 'PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2') off.

UNREACHABLE FROM REAL MONEY. This module imports only the paper ledger, the
paper simulator, the pure policy helpers (`derek_policy`) and paper Derek's
pure helpers; it never imports a funded, live or order-submitting module, and
no funded module imports a paper module
(tests/test_paper_records_cannot_reach_the_funded_path.py,
tests/test_pinnacle_only_paper_benchmark.py).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from typing import Any

from .. import bettor_paper_ledger as L
from .. import bettor_paper_simulator as SIM
from .. import bettor_settlement_terms as ST
from . import derek_policy as DP
from . import paper_derek as PD

STRATEGY = "PINNACLE_ONLY_PAPER_BENCHMARK"
#: THE ORIGINAL TWO-MODEL STRATEGY'S LABEL (migration 182's column default).
TWO_MODEL_STRATEGY = DP.POLICY_V2
VERSION = "PINNACLE_ONLY_PAPER_BENCHMARK_V1"
ENV_FLAG = "PAPER_BENCHMARK"
CONTROL_KEY = STRATEGY

DISCLOSURE = (
    "PINNACLE_ONLY_PAPER_BENCHMARK: EXPERIMENTAL PAPER EXECUTION on a "
    "fictional account, deciding on the de-vigged Pinnacle probability "
    "alone. It is NOT evidence of qualified or proven profitability: no "
    "internal model is used, the Pinnacle source's calibration and the venue "
    "book's currency (P5) are not established, and every fill is SIMULATED "
    "(PAPER_SIM_V1). No real money, no real venue order.")

#: THE OWNER'S EDGE: a probability difference on a $0/$1 contract.
MIN_EDGE = 0.05
MIN_EDGE_PP = 5.0
#: A paper book observation older than this at the decision is not current.
BOOK_MAX_AGE_S = 10.0

# ═════════════════════════════════════════════════════════════════════
# THE POLICIES: STRICT (above, unchanged) AND COMPLETED-GAME (experimental)
# ═════════════════════════════════════════════════════════════════════
#
# PINNACLE_COMPLETED_GAME_PAPER (owner-authorized 2026-10-01, PAPER ONLY).
# Same session, same ledger, same rails, positive net EV (its own edge
# threshold: V1 ran 5 pp; V2 runs the owner's 0.5 pp, see below),
# same fresh-quote and current-book checks, same conservative fills. It
# differs from the strict policy in exactly two places:
#
#   MATCH      it requires an EXACT match on fixture, participant, selected
#              outcome, market, line and the GRADING PERIOD OF AN ORDINARILY
#              COMPLETED GAME (regulation-only vs extra-time stays a hard
#              distinction). The postponement / abandonment / suspension
#              terms are NOT required to agree: they are recorded as
#              DISCLOSED RESEARCH RISKS, never as settlement compatibility.
#   ECONOMICS  CONDITIONAL on ordinary completion -- edge and modelled profit
#              after fees on the completed-game outcome only -- and labelled
#              CONDITIONAL_EXPERIMENTAL_NOT_RISK_ADJUSTED. Exceptional-
#              settlement payoffs are shown separately, their probabilities
#              UNMEASURED (never zero, never an invented adjustment).
#
# Its own strategy key and version, its own decision namespace ('papercg:'),
# so the strict policy's decisions and history are untouched.

CG_STRATEGY = "PINNACLE_COMPLETED_GAME_PAPER"
#: THE VERSION EVERY NEW DECISION RECORDS. V1 (5.0 pp) decided everything up
#: to the V2 release and its rows keep saying so; V2 is the owner's
#: 2026-10-01 paper-only decision (migration 188):
#:     p_pinnacle - simulated acquisition price >= 0.005 at EVERY level used
#:     AND conditional expected profit after fees strictly > 0
#: -- half a probability point on a $0/$1 contract, an ABSOLUTE difference,
#: not a return target, with no upper edge limit. V2 also sizes NET OF FEES:
#: a level whose per-contract fee consumes its edge is never bought, so a
#: candidate that clears 0.5 pp gross but not its fees is refused by name.
CG_VERSION_V1 = "PINNACLE_COMPLETED_GAME_PAPER_V1"
CG_VERSION = "PINNACLE_COMPLETED_GAME_PAPER_V2"
CG_DISCLOSURE = (
    "PINNACLE_COMPLETED_GAME_PAPER: EXPERIMENTAL PAPER EXECUTION on a "
    "fictional account. The reference probability is the de-vigged Pinnacle "
    "probability alone. Its economics are CONDITIONAL on the game being "
    "ordinarily completed and are NOT risk-adjusted or proven positive EV: "
    "the venue's and the book's terms for postponed, abandoned or suspended "
    "games are not established as compatible and are carried as disclosed "
    "research risks, with their frequencies unmeasured. Every fill is "
    "SIMULATED (PAPER_SIM_V1). It does not qualify any strategy for real "
    "money. No real money, no real venue order.")
ECONOMICS_LABEL = "CONDITIONAL_EXPERIMENTAL_NOT_RISK_ADJUSTED"

STRICT_POLICY = {"kind": "STRICT", "strategy": STRATEGY, "version": VERSION,
                 "control_key": CONTROL_KEY, "id_prefix": "paperbench",
                 "group_prefix": "paperbenchgrp", "disclosure": DISCLOSURE,
                 "audit_kind": "PINNACLE_ONLY_PAPER_BENCHMARK_FILL_AUDITED",
                 "report_key": "pinnacle_only_paper_benchmark"}
CG_POLICY = {"kind": "COMPLETED_GAME", "strategy": CG_STRATEGY,
             "version": CG_VERSION, "control_key": CG_STRATEGY,
             "id_prefix": "papercg", "group_prefix": "papercggrp",
             "disclosure": CG_DISCLOSURE,
             "audit_kind": "PINNACLE_COMPLETED_GAME_PAPER_FILL_AUDITED",
             "report_key": "pinnacle_completed_game_paper"}
# ── THE MAKER-ENTRY POLICY (owner-authorized 2026-10-01, PAPER ONLY) ───
# The completed-game policy's match, threshold (its ACTIVE parameter version,
# never below the owner's 0.5 pp floor) and positive expected profit after
# fees, reached by RESTING a bid below the ask instead of taking the ask.
# Its own strategy key, version and namespace; decisions in
# `paper_maker.py`. The venue's maker rebate is PUBLISHED but NOT VERIFIED
# AS APPLIED to this account, so every maker fill is charged the TAKER fee.
MAKER_STRATEGY = "PINNACLE_COMPLETED_GAME_MAKER_PAPER"
MAKER_VERSION = "PINNACLE_COMPLETED_GAME_MAKER_PAPER_V1"
MAKER_DISCLOSURE = (
    "PINNACLE_COMPLETED_GAME_MAKER_PAPER: EXPERIMENTAL PAPER EXECUTION on a "
    "fictional account. A RESTING bid priced so that the de-vigged Pinnacle "
    "probability exceeds it by at least the active threshold and the "
    "expected profit after the TAKER fee is positive IF IT FILLS. A resting "
    "order is not a fill: fills are SIMULATED (PAPER_SIM_V1) only when "
    "observed liquidity strictly crosses the bid after the queue ahead; a "
    "touch is never a fill. Economics are conditional on ordinary "
    "completion and on being filled (adverse selection unmeasured); the "
    "venue maker rebate is not assumed. No real money, no real venue order.")
MAKER_POLICY = {"kind": "MAKER", "strategy": MAKER_STRATEGY,
                "version": MAKER_VERSION, "control_key": MAKER_STRATEGY,
                "id_prefix": "papermk", "group_prefix": "papermkgrp",
                "disclosure": MAKER_DISCLOSURE,
                "audit_kind": "PINNACLE_COMPLETED_GAME_MAKER_PAPER_FILL_AUDITED",
                "report_key": "pinnacle_completed_game_maker_paper"}

# ── THE EXPLORATION STRATEGY (owner-authorized 2026-10-01, PAPER ONLY) ──
# A separate, bounded TRAINING strategy that may take positions failing the
# investment policy's edge or after-fee requirement, to generate forward
# experience. Every decision records its estimated edge, fees, selection
# probability and training purpose. Its positions are labelled
# "Training / simulated execution"; negative expected value is a research
# cost, never investment performance. Decisions in `paper_explore.py`.
EXPLORE_STRATEGY = "PINNACLE_EXPLORATION_PAPER"
EXPLORE_VERSION = "PINNACLE_EXPLORATION_PAPER_V3"
EXPLORE_DISCLOSURE = (
    "PINNACLE_EXPLORATION_PAPER: TRAINING / SIMULATED EXECUTION on a "
    "fictional account. Positions are taken to generate forward experience "
    "(fills, management, settlement, audit) and MAY FAIL the investment "
    "policy's edge and after-fee requirements; their expected value is "
    "recorded and may be negative -- a research cost, not investment "
    "performance and not a profitable opportunity. Same data and execution "
    "safeguards as the investment policy. No real money, no real venue "
    "order.")
EXPLORE_LABEL = "Training / simulated execution"
EXPLORE_POLICY = {"kind": "EXPLORATION", "strategy": EXPLORE_STRATEGY,
                  "version": EXPLORE_VERSION, "control_key": EXPLORE_STRATEGY,
                  "id_prefix": "paperexp", "group_prefix": "paperexpgrp",
                  "disclosure": EXPLORE_DISCLOSURE,
                  "audit_kind": "PINNACLE_EXPLORATION_PAPER_FILL_AUDITED",
                  "report_key": "pinnacle_exploration_paper"}

POLICIES = (STRICT_POLICY, CG_POLICY, MAKER_POLICY, EXPLORE_POLICY)
BENCHMARK_STRATEGIES = tuple(p["strategy"] for p in POLICIES)
#: THE POLICIES WHOSE MATCH IS THE COMPLETED-GAME MATCH (exact fixture,
#: outcome, market, line and ordinary grading period): their positions are
#: measured conditional on ordinary completion and may settle at the venue's
#: own published price.
COMPLETED_GAME_KINDS = ("COMPLETED_GAME", "MAKER", "EXPLORATION")
COMPLETED_GAME_STRATEGIES = tuple(p["strategy"] for p in POLICIES
                                  if p["kind"] in COMPLETED_GAME_KINDS)

# ── THE COMPLETED-GAME POLICY'S VERSIONED PARAMETERS (186, 188) ─────────
# ONE whitelisted parameter, min_gross_edge_pp, read from the policy's
# ACTIVE parameter version (paper_policy_parameter_heads) once per pass /
# per hook call, through THIS decision path. Bounds 0.5..6.0 pp on a 0.5 pp
# grid: THE 0.5 pp FLOOR IS AN OWNER MANDATE (2026-10-01, replacing the
# earlier 5.0 pp floor) -- automatic learning can never take the threshold
# below it; anything lower needs a SEPARATE OWNER DECISION and a new
# migration, never a proposal (the database CHECKs the same bounds in
# migration 188 and a test pins the three copies equal). The shipped V1
# (5.0 pp) stays in the version table, so an audited rollback to it remains
# possible. FAIL-CLOSED: an absent table, a failed read or a stored value
# outside the bounds runs the SHIPPED DEFAULT OF THIS CODE VERSION (V2,
# 0.5 pp -- the owner's decision, never anything lower) and records why.
# The strict benchmark has no parameter versions: it always runs MIN_EDGE.
CG_MIN_EDGE_PP_V2 = 0.5
CG_PARAMETERS_V1 = {"min_gross_edge_pp": MIN_EDGE_PP}
CG_PARAMETERS_V2 = {"min_gross_edge_pp": CG_MIN_EDGE_PP_V2}
CG_PARAMETER_BOUNDS = {"min_gross_edge_pp": (0.5, 6.0)}   # 0.5: owner floor
CG_PARAMETER_GRID_PP = 0.5
CG_V1_VERSION_ID = "paperparam:%s:V1" % CG_STRATEGY
CG_V2_VERSION_ID = "paperparam:%s:V2" % CG_STRATEGY
P_ACTIVE = "ACTIVE_VERSION"
P_FALLBACK = "SHIPPED_DEFAULT_FALLBACK"


def validate_cg_parameters(params) -> str | None:
    """The whitelist and the bounds (pure). None when valid."""
    if not isinstance(params, dict) or set(params) != set(
            CG_PARAMETER_BOUNDS):
        return "PARAMETERS_NOT_THE_WHITELISTED_SET"
    for k, (lo, hi) in CG_PARAMETER_BOUNDS.items():
        v = params.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return "PARAMETER_NOT_A_NUMBER:%s" % k
        if not (lo - 1e-9 <= float(v) <= hi + 1e-9):
            return "PARAMETER_OUT_OF_BOUNDS:%s" % k
        steps = float(v) / CG_PARAMETER_GRID_PP
        if abs(steps - round(steps)) > 1e-9:
            return "PARAMETER_OFF_GRID:%s" % k
    return None


def _cg_fallback(why: str) -> dict:
    return {"policy_key": CG_STRATEGY, "source": P_FALLBACK,
            "version_id": CG_V2_VERSION_ID, "version_no": 2,
            "values": dict(CG_PARAMETERS_V2), "fallback_reason": why,
            "proposal_id": None, "evaluation_id": None, "approved_by": None,
            "activation_id": None, "activated_at": None}


async def cg_parameters(conn, ctx: dict) -> dict:
    """THE ACTIVE PARAMETER VERSION OF THE COMPLETED-GAME POLICY, read once
    per pass (the pass context is new every pass; the per-valuation hook
    builds a new one per call) and fail-closed to the shipped default.
    Never raises."""
    cache = ctx.setdefault("policy_parameters", {})
    if CG_STRATEGY in cache:
        return cache[CG_STRATEGY]
    try:
        present = await conn.fetchval(
            "SELECT to_regclass('paper_policy_parameter_heads') IS NOT NULL")
        if not present:
            out = _cg_fallback("PARAMETER_TABLES_ABSENT")
        else:
            r = await conn.fetchrow(
                "SELECT h.active_version_id, h.activation_id, v.version_no, "
                "       v.params, v.params_sha256, v.source, v.proposal_id, "
                "       v.evaluation_id, v.approved_by, a.at AS activated_at,"
                "       a.kind AS activation_kind "
                "  FROM paper_policy_parameter_heads h "
                "  JOIN paper_policy_parameter_versions v "
                "    ON v.version_id = h.active_version_id "
                "   AND v.policy_key = h.policy_key "
                "  JOIN paper_policy_parameter_activations a "
                "    ON a.activation_id = h.activation_id "
                " WHERE h.policy_key = $1", CG_STRATEGY)
            vals = None if r is None else L._j(r["params"])
            bad = ("NO_ACTIVE_VERSION_ROW" if r is None
                   else validate_cg_parameters(vals))
            out = (_cg_fallback(bad) if bad else {
                "policy_key": CG_STRATEGY, "source": P_ACTIVE,
                "version_id": r["active_version_id"],
                "version_no": r["version_no"],
                "values": {k: float(v) for k, v in vals.items()},
                "params_sha256": r["params_sha256"],
                "version_source": r["source"],
                "proposal_id": r["proposal_id"],
                "evaluation_id": r["evaluation_id"],
                "approved_by": r["approved_by"],
                "activation_id": r["activation_id"],
                "activation_kind": r["activation_kind"],
                "activated_at": L._epoch(r["activated_at"]),
                "fallback_reason": None})
    except Exception as exc:                                    # noqa: BLE001
        out = _cg_fallback("PARAMETER_READ_FAILED:%s" % type(exc).__name__)
    out["read_at"] = float(ctx["now"])
    cache[CG_STRATEGY] = out
    return out


def policy_for(strategy) -> dict | None:
    for p in POLICIES:
        if p["strategy"] == str(strategy or ""):
            return p
    return None


def _pol(pol) -> dict:
    return pol if pol is not None else STRICT_POLICY

R_ENV_OFF = "PAPER_BENCHMARK_ENVIRONMENT_FLAG_IS_NOT_ON"
R_CONTROL_OFF = "THE_PINNACLE_ONLY_PAPER_BENCHMARK_CONTROL_ROW_IS_OFF"
R_CONTROL_ABSENT = "THE_PINNACLE_ONLY_PAPER_BENCHMARK_CONTROL_ROW_IS_ABSENT"

R_IDENTITY = DP.R_IDENTITY                    # FIXTURE_IDENTITY_NOT_ESTABLISHED
R_OUTCOME = "PAYOUT_OUTCOME_MATCH_NOT_ESTABLISHED"
R_SETTLEMENT = DP.R_SETTLEMENT                # SETTLEMENT_NOT_SUPPORTED
R_NOT_REAL = DP.R_NOT_REAL
R_NOT_PMUS = PD.R_NOT_PMUS
R_PROBABILITY_UNQUALIFIED = "PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE"
R_NO_PINNACLE = DP.R_NO_PINNACLE
R_STALE = DP.R_STALE                          # PROBABILITY_EVIDENCE_STALE
R_FRESHNESS_UNKNOWN = DP.R_FRESHNESS_UNKNOWN
R_NO_BOOK = PD.R_NO_BOOK
R_BOOK_NOT_CURRENT = "THE_PAPER_BOOK_OBSERVATION_IS_NOT_CURRENT"
R_EDGE = DP.R_BELOW                           # BELOW_MIN_GROSS_EDGE
#: ANOTHER STRATEGY ALREADY HOLDS (or is buying) THIS GAME on the one shared
#: account. Two policies never independently spend the same bankroll on
#: duplicate exposure to one fixture.
R_CROSS_STRATEGY = "ANOTHER_STRATEGY_HOLDS_EXPOSURE_TO_THIS_FIXTURE"
R_NO_QTY = DP.R_NO_QTY
R_FEES = DP.R_FEES
R_NET = DP.R_NET                              # NET_EV_NOT_POSITIVE_AFTER_FEES
#: V2: the gross edge clears the threshold at the best level, but at every
#: such level the simulator's fee per contract is at least the edge.
R_FEES_CONSUME_EDGE = "GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT"
R_ORDER_REFUSED = PD.R_ORDER_REFUSED

BOOK_CURRENCY = {
    "verdict": "NOT_ESTABLISHED",
    "rule": "P5_BOOK_CURRENCY",
    "mechanism": ("PAPER_MARKET_DATA_CLIENT_READ: the read-only client's "
                  "book read, stamped with OUR receipt instant "
                  "(paper_book_observations.observed_at)"),
    "basis": ("the venue read does not establish that the displayed book is "
              "current; its age is bounded only by our own receipt instant. "
              "The price is never claimed proven current"),
    "funded_admission_and_p5_rule": "UNTOUCHED"}

STRATEGY_LABEL = {"strategy": STRATEGY, "disclosure": DISCLOSURE,
                  "book_currency": "NOT_ESTABLISHED"}

#: The entry experiment whose valuations are decided (the value of
#: bettor_external_shadow.EXPERIMENT_ID, stated here so this module imports
#: nothing beyond the paper and pure-policy modules; a test pins equality).
EXPERIMENT_ID = "EXT_PINNACLE_DEVIG_V1_SHADOW"

GAP_NOT_EVIDENCE = "NOT_EVIDENCE_OF_PROFITABILITY"
GAP_NO_MODEL = "NO_INTERNAL_MODEL_BY_DESIGN"

CANDIDATES_SQL = """
    SELECT v.* FROM external_valuations v
     WHERE v.experiment_id = $1
       AND v.decided_at > to_timestamp($2) AND v.decided_at <= to_timestamp($3)
       AND v.us_market_slug IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM paper_decisions d
                        WHERE d.session_id = $4 AND d.valuation_id = v.id
                          AND d.strategy = $6)
     ORDER BY v.decided_at DESC, v.id DESC
     LIMIT $5
"""


# ═════════════════════════════════════════════════════════════════════
# THE SWITCH
# ═════════════════════════════════════════════════════════════════════

def env_on() -> bool:
    return str(os.environ.get(ENV_FLAG, "")).strip().lower() in (
        "on", "1", "true", "yes")


async def enablement(conn, pol=None) -> dict:
    """The environment flag AND the policy's kill-switch row. Never raises."""
    pol = _pol(pol)
    CONTROL_KEY = pol["control_key"]                            # noqa: N806
    out: dict[str, Any] = {"strategy": pol["strategy"], "env_flag": ENV_FLAG,
                           "env_on": env_on(), "control_key": CONTROL_KEY}
    if not out["env_on"]:
        return dict(out, enabled=False, refusal=R_ENV_OFF)
    try:
        row = await conn.fetchrow(
            "SELECT enabled, why, updated_by FROM paper_control "
            " WHERE control_key = $1", CONTROL_KEY)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, enabled=False, refusal=R_CONTROL_ABSENT,
                    why="control row unreadable: %s" % type(exc).__name__)
    if row is None:
        return dict(out, enabled=False, refusal=R_CONTROL_ABSENT)
    out.update(control_on=bool(row["enabled"]), control_why=row["why"],
               control_updated_by=row["updated_by"])
    if not row["enabled"]:
        return dict(out, enabled=False, refusal=R_CONTROL_OFF)
    return dict(out, enabled=True, refusal=None)


def decision_id_for(session_id: str, valuation_id, pol=None) -> str:
    pol = _pol(pol)
    return pol["id_prefix"] + ":" + hashlib.sha256(
        ("%s:%s:%s" % (session_id, valuation_id, pol["strategy"])).encode()
    ).hexdigest()[:24]


def group_id_for(decision_id: str) -> str:
    prefix, h = decision_id.split(":", 1)
    for p in POLICIES:
        if p["id_prefix"] == prefix:
            return p["group_prefix"] + ":" + h
    return "paperbenchgrp:" + h


# ═════════════════════════════════════════════════════════════════════
# THE CONTRACT / OUTCOME / SETTLEMENT-TERMS MATCH (pure)
# ═════════════════════════════════════════════════════════════════════

def _lane_codes(cand: dict) -> dict:
    """The lane's own refusals on the row, by the check that owns them."""
    out: dict = {"probability": [], "identity": [], "settlement": [],
                 "not_blocking": []}
    for code in cand.get("refusals") or []:
        stage = DP._lane_stage(code)
        if stage == "1_PROBABILITY":
            out["probability"].append(code)
        elif stage == "3_IDENTITY":
            out["identity"].append(code)
        elif stage == "4_SETTLEMENT_SCOPE" or \
                code == "VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED":
            out["settlement"].append(code)
        else:
            out["not_blocking"].append(code)
    return out


def contract_match(cand: dict, row: dict) -> dict:
    """THE MATCH BETWEEN THE STORED PROBABILITY AND THE CONTRACT TRADED.

    Identity and settlement are `derek_policy.evaluate`'s checks 1 and 3 on
    the same candidate (unweakened: settlement passes only when the
    comparison is COMPATIBLE, every rule established and no settlement-stage
    refusal is on the row). The payout outcome match: the probability is of
    the event the contract pays on -- the same event as the de-vig's when
    payout_is_complement is false, NOT(selection) when it is true."""
    lane = _lane_codes(cand)
    checks, refusals = [], []

    def put(name, ok, refusal, detail, **ev):
        checks.append(dict({"check": name, "passed": bool(ok),
                            "refusal": None if ok else refusal,
                            "detail": detail}, **ev))
        if not ok and refusal not in refusals:
            refusals.append(refusal)

    # 1 · IDENTITY (evaluate's C_IDENTITY)
    missing = [k for k in ("us_market_slug", "payout_event", "period",
                           "fixture") if not cand.get(k)]
    why_not = (list(lane["identity"]) + ["missing:%s" % k for k in missing]
               + ([] if cand.get("side") in (DP.LONG, DP.SHORT)
                  else ["side:%r" % cand.get("side")])
               + (["payout outcome not bound"]
                  if cand.get("payout_binding_ok") is False else []))
    put(DP.C_IDENTITY, not why_not, R_IDENTITY,
        ("%s %s on %s, period %s" % (cand.get("side"),
                                     cand.get("payout_event"),
                                     cand.get("us_market_slug"),
                                     cand.get("period"))
         if not why_not else "identity not established: %s" % why_not),
        lane_refusals=lane["identity"])
    # 2 · THE PAYOUT OUTCOME THE PROBABILITY DESCRIBES
    sel = str(row.get("contract_selection") or "")
    pay = str(row.get("payout_event") or "")
    pev = str(row.get("probability_event") or "")
    comp = bool(row.get("payout_is_complement"))
    if not pay:
        ok, why = False, "no payout event is recorded"
    elif comp:
        ok = pay == "NOT(%s)" % sel and bool(sel)
        why = ("complement: the probability is 1 - p(%s), the contract pays "
               "on %s" % (sel, pay)) if ok else (
            "payout_is_complement but the payout event %r is not NOT(%r)"
            % (pay, sel))
    else:
        ok = (not pev or pev == pay) and (not sel or sel == pay)
        why = ("the probability's event and the payout event are the same "
               "event (%s)" % pay) if ok else (
            "payout event %r differs from the probability's event %r / the "
            "selection %r without a complement" % (pay, pev, sel))
    put("payout_outcome_match", ok, R_OUTCOME, why,
        payout_event=pay or None, probability_event=pev or None,
        contract_selection=sel or None, payout_is_complement=comp,
        payout_event_basis=row.get("payout_event_basis"))
    # 3 · SETTLEMENT TERMS (evaluate's C_SETTLEMENT)
    st = dict(cand.get("settlement") or {})
    blockers = [str(b) for b in (st.get("blockers") or [])]
    s_ok = (st.get("compatibility") == "COMPATIBLE"
            and st.get("overall_established") is True
            and not lane["settlement"] and not blockers)
    put(DP.C_SETTLEMENT, s_ok, R_SETTLEMENT,
        ("venue and book settlement terms compared COMPATIBLE and every "
         "rule established") if s_ok else (
            "settlement not established: compatibility=%s, "
            "overall_established=%s, blockers=%s, lane=%s" % (
                st.get("compatibility"), st.get("overall_established"),
                blockers or "NONE_RECORDED", lane["settlement"])),
        compatibility=st.get("compatibility"),
        overall_established=st.get("overall_established"),
        blockers=blockers,
        lane_refusals=lane["settlement"])
    # THE PRECISE REASONS RIDE BEHIND THE CATEGORY, so the decision's
    # refusals name what to establish (a missing rule, a conflicting
    # payout, absent fixture metadata), not only that settlement failed.
    # A row written before blockers were recorded says that instead.
    if not s_ok:
        for b in (blockers or ["SETTLEMENT_BLOCKERS_NOT_RECORDED_ON_THIS_ROW"]):
            if b not in refusals:
                refusals.append(b)
    # 4 · THE LANE QUALIFIED THE PROBABILITY ITSELF (de-vig, mapping, books)
    put("probability_qualified_by_the_lane", not lane["probability"],
        R_PROBABILITY_UNQUALIFIED,
        ("no probability-stage refusal on the row" if not lane["probability"]
         else "the lane refused the probability: %s" % lane["probability"]),
        lane_refusals=lane["probability"])
    # 5 · VENUE
    put("polymarket_us_contract", cand.get("venue") in (None, "PMUS"),
        R_NOT_PMUS, "venue %s" % cand.get("venue"))
    return {"established": not refusals, "refusals": refusals,
            "checks": checks,
            "lane_refusals_not_blocking": lane["not_blocking"],
            "not_blocking_because": (
                "freshness is re-aged here (Pinnacle) or disclosed (P5 venue "
                "currency); execution, sizing and risk are the paper "
                "session's own; CALIBRATION_ONLY is the row's purpose, not "
                "a contract fact")}


# ═════════════════════════════════════════════════════════════════════
# THE COMPLETED-GAME MATCH (pure): ordinary-completion terms, exactly
# ═════════════════════════════════════════════════════════════════════
#
# WHAT IS GRADED WHEN THE GAME IS ORDINARILY COMPLETED, on each side, read
# from DECLARED evidence only:
#
#   book   the captured Pinnacle terms (bettor_settlement_terms), cited
#   venue  the contract's OWN published rules text, persisted on the
#          valuation row by the collector (settlement_comparison.
#          venue_rules_text), matched against the venue's standard wording
#          below -- every listed phrase must be present, no excluding phrase
#          may be. An unrecognised text establishes NOTHING.
#
# A different grading period (e.g. a soccer contract including extra time
# against Pinnacle's 90-minute basis) is a MISMATCH and refuses. The
# exceptional conditions are not part of this match at all.

GP_BASEBALL = "FULL_GAME_INCLUDING_EXTRA_INNINGS"
GP_SOCCER_90 = ("NINETY_MINUTES_PLUS_STOPPAGE_EXCLUDING_EXTRA_TIME_AND_"
                "PENALTIES_DRAW_IS_A_SEPARATE_OUTCOME")

R_GP_TEXT_ABSENT = "VENUE_RULES_TEXT_NOT_RECORDED_ON_THE_VALUATION_ROW"
R_GP_UNKNOWN = "ORDINARY_GRADING_PERIOD_NOT_ESTABLISHED"
R_GP_MISMATCH = "ORDINARY_GRADING_PERIOD_MISMATCH"
R_MARKET = "MARKET_OR_LINE_NOT_A_MONEYLINE_MATCH"
R_PERIOD = "GRADING_PERIOD_NOT_FULL_GAME"
R_FAMILY = "NO_COMPLETED_GAME_TERMS_FOR_THIS_SPORT"

VENUE_GRADING_TEMPLATES = {
    "baseball": {
        "period": GP_BASEBALL,
        "all_of": (r"\bwill settle to the winner of the\b[^.]*\bgame\b",
                   r"\bextra innings are included if played\b"),
        "none_of": (r"\bextra innings (?:are|will be) (?:not|excluded)\b",
                    r"\bafter (?:nine|9) innings\b")},
    "soccer": {
        "period": GP_SOCCER_90,
        "all_of": (r"\bwill settle to the winner at the end of 90 minutes "
                   r"plus stoppage time\b",
                   r"\btied following 90 minutes plus stoppage time\b"
                   r"[^.]*\bsettle to tie\b"),
        "none_of": (r"\bextra time\b[^.]*\binclud",
                    r"\bpenalt(?:y|ies)\b[^.]*\b(?:includ|count)",
                    r"\bincluding extra time\b")},
}


def book_grading_period(family) -> dict | None:
    """Pinnacle's completed-game grading for the money line, cited."""
    if family == "baseball":
        t = ST.BOOK_TERMS[("baseball", "h2h", ST.CTX_PRE_GAME)]
        return {"period": GP_BASEBALL,
                "regulation": t[ST.C_FULL], "extra_innings":
                    t[ST.C_OVERTIME],
                "basis": ("Pinnacle's Game-period Money Line grades the "
                          "completed game, extra innings included (the "
                          "captured terms: COMPLETED_IN_REGULATION and "
                          "DECIDED_AFTER_REGULATION both pay on the final "
                          "score)")}
    if family == "soccer":
        return {"period": GP_SOCCER_90,
                "quote": ST.SOCCER_NINETY_MINUTE_BASIS["quote"],
                "source_url": ST.SOCCER_NINETY_MINUTE_BASIS["source_url"],
                "retrieved_at": ST.SOCCER_NINETY_MINUTE_BASIS["retrieved_at"],
                "basis": ("Pinnacle's match markets grade the result at the "
                          "end of 90 minutes plus stoppage time, excluding "
                          "extra time and a shootout; its 3-way market prices "
                          "the draw as its own outcome")}
    return None


def venue_grading_period(family, prose) -> dict:
    """The venue contract's completed-game grading, from its own text."""
    tpl = VENUE_GRADING_TEMPLATES.get(str(family or ""))
    flat = " ".join(str(prose or "").split()).lower()
    if tpl is None:
        return {"period": None, "refusal": R_FAMILY}
    if not flat:
        return {"period": None, "refusal": R_GP_TEXT_ABSENT}
    import re as _re
    hits = [p for p in tpl["all_of"] if _re.search(p, flat)]
    conflict_prose = flat
    if family == "soccer":
        # A complete, unconditional exclusion is consistent with 90-minute
        # grading. The broad conflict patterns otherwise mistake "not
        # included" or "do not count" for inclusion. Remove ONLY these
        # whole sentences from conflict checking; required grading phrases
        # still come from the original text. Qualified clauses, and any
        # contradictory sentence elsewhere, remain subject to refusal.
        exclusion = (
            r"(?:extra time(?: and penalties)?|penalties) "
            r"(?:(?:is|are|will be) (?:not included|excluded)|"
            r"(?:does|do|will) not count)[.!?]?"
        )
        conflict_prose = " ".join(
            sentence for sentence in _re.split(r"(?<=[.!?])\s+", flat)
            if not _re.fullmatch(exclusion, sentence)
        )
    bad = [p for p in tpl["none_of"] if _re.search(p, conflict_prose)]
    if bad:
        return {"period": "NOT_" + tpl["period"], "refusal": R_GP_MISMATCH,
                "matched_excluding": bad}
    if len(hits) != len(tpl["all_of"]):
        return {"period": None, "refusal": R_GP_UNKNOWN,
                "matched": hits, "required": list(tpl["all_of"])}
    return {"period": tpl["period"], "refusal": None, "matched": hits}


def completed_game_match(cand: dict, row: dict) -> dict:
    """THE COMPLETED-GAME POLICY'S MATCH: fixture and participant, selected
    outcome, market and line, and the ORDINARY grading period -- exactly.
    The exceptional settlement terms are returned as DISCLOSED research
    risks; they are never part of `established`."""
    base = contract_match(cand, row)
    keep = {DP.C_IDENTITY, "payout_outcome_match",
            "probability_qualified_by_the_lane", "polymarket_us_contract"}
    checks = [c for c in base["checks"] if c["check"] in keep]
    refusals = [c["refusal"] for c in checks if not c["passed"]]

    def put(name, ok, refusal, detail, **ev):
        checks.append(dict({"check": name, "passed": bool(ok),
                            "refusal": None if ok else refusal,
                            "detail": detail}, **ev))
        if not ok and refusal not in refusals:
            refusals.append(refusal)

    fam = str(cand.get("sport_family") or row.get("sport_family") or "")
    mk_ok = (str(cand.get("market") or "") == "h2h"
             and cand.get("line") is None)
    put("market_and_line", mk_ok, R_MARKET,
        "market %r, line %r (a money line has no line)"
        % (cand.get("market"), cand.get("line")))
    put("grading_period_full_game",
        str(cand.get("period") or "") == "FULL_GAME", R_PERIOD,
        "contract period %r" % (cand.get("period"),))
    scmp = DP._j(row.get("settlement_comparison")) or {}
    book = book_grading_period(fam)
    venue = venue_grading_period(fam, scmp.get("venue_rules_text"))
    gp_ok = (book is not None and venue.get("refusal") is None
             and venue.get("period") == book["period"])
    put("ordinary_completion_grading_period", gp_ok,
        (R_FAMILY if book is None else (venue.get("refusal")
                                         or R_GP_MISMATCH)),
        ("book and venue both grade the ordinarily completed game as %s"
         % (book or {}).get("period")) if gp_ok else (
            "book %s vs venue %s" % ((book or {}).get("period"),
                                     venue.get("period"))),
        book=book, venue=dict(venue, rules_sha256=scmp.get(
            "venue_rules_sha256")))
    exceptional = {
        "status": "DISCLOSED_RESEARCH_RISK_NOT_SETTLEMENT_COMPATIBILITY",
        "compatibility_recorded": scmp.get("compatibility"),
        "blockers": list(scmp.get("blockers") or []),
        "per_condition": {
            c: r for c, r in (scmp.get("per_condition") or {}).items()
            if c not in (ST.C_FULL, ST.C_OVERTIME)},
        "why": ("postponement, abandonment and suspension terms are not "
                "required to agree under this experimental policy; they are "
                "carried as research risks and never reported as proven "
                "compatibility")}
    return {"established": not refusals, "refusals": refusals,
            "checks": checks, "exceptional_terms": exceptional,
            "policy": CG_VERSION}


def exceptional_scenarios(*, cand: dict, row: dict, qty: float,
                          vwap) -> dict:
    """THE EXCEPTIONAL-SETTLEMENT PAYOFFS, apart from the conditional EV.
    Per contract and in total, from the venue's OWN stated payout where it
    states one; probabilities UNMEASURED -- never zero, never invented."""
    scmp = DP._j(row.get("settlement_comparison")) or {}
    per = dict(scmp.get("per_condition") or {})
    q = float(qty or 0.0)
    v = None if vwap is None else float(vwap)
    out = {}
    for cond in (ST.C_NOT_PLAYED, ST.C_SUSPENDED_BEYOND,
                 ST.C_SUSPENDED_RESUMED, ST.C_CALLED_FINAL,
                 ST.C_STOPPED_EARLY):
        r = per.get(cond)
        if r is None:
            continue
        vp = r.get("venue_payout")
        sc = {"venue_payout": vp, "book_payout": r.get("book_payout"),
              "verdict": r.get("verdict"),
              "probability": "UNMEASURED",
              "probability_note": ("no measured frequency for this "
                                   "condition is held; it is not set to "
                                   "zero and no risk adjustment is applied")}
        if vp == ST.PAY_LAST_FAIR_MARKET_PRICE and v is not None:
            sc.update(payoff_per_contract_range=[round(-v, 9),
                                                 round(1.0 - v, 9)],
                      payoff_total_range_usd=[round(-v * q, 6),
                                              round((1.0 - v) * q, 6)],
                      payoff_basis=("the venue settles at the contract's "
                                    "last fair market price S, unknown "
                                    "until published: payoff S - entry "
                                    "price, S in [0, 1]"))
        elif vp in (ST.PAY_ON_FINAL, ST.PAY_ON_PARTIAL,
                    ST.PAY_ON_PARTIAL_WALKOFF) and v is not None:
            sc.update(payoff_per_contract_range=[round(-v, 9),
                                                 round(1.0 - v, 9)],
                      payoff_basis="won or lost on the graded score")
        elif vp == ST.PAY_STAKE_BACK and v is not None:
            sc.update(payoff_per_contract_range=[0.0, 0.0],
                      payoff_basis="the venue states a stake return")
        else:
            sc.update(payoff_per_contract_range=None,
                      payoff_basis=("the venue states no payout for this "
                                    "condition: UNKNOWN"))
        out[cond] = sc
    return {"label": ECONOMICS_LABEL, "scenarios": out,
            "included_in_conditional_ev": False}


def conditional_economics(*, p: float, takes: list, fee_fn,
                          at: float) -> dict:
    """EV under ORDINARY COMPLETION ONLY: per walked level, q (p - price),
    the simulator's fees. No void scaling and no refund assumption -- the
    exceptional states are priced separately (`exceptional_scenarios`)."""
    e = economics(p=p, takes=takes, fee_fn=fee_fn, at=at,
                  void_states={"applied": False})
    return dict(e, label=ECONOMICS_LABEL, conditional_on="ORDINARY_COMPLETION",
                void_scale=None,
                not_claimed=("risk-adjusted EV; proven positive EV; any "
                             "exceptional-settlement frequency"))


# ═════════════════════════════════════════════════════════════════════
# THE ECONOMICS ON THE OBSERVED BOOK (pure but for the fee function)
# ═════════════════════════════════════════════════════════════════════

def level_edges(levels: list, p: float, *, min_edge: float = MIN_EDGE
                ) -> list:
    """Each level's gross edge; `clears_5pp` is the fixed 5 pp check and
    `clears_min_edge` the check against the threshold this decision runs
    (MIN_EDGE for the strict policy; the completed-game policy's ACTIVE
    parameter version)."""
    out = []
    for lv in levels:
        e = DP.gross_edge(p, lv["price"])
        out.append({"price": lv["price"], "wire": lv["wire"],
                    "qty": float(lv["qty"]),
                    "edge_pp": None if e is None else round(e * 100.0, 9),
                    "clears_5pp": DP.clears(e, MIN_EDGE),
                    "clears_min_edge": DP.clears(e, min_edge),
                    "min_edge_pp": round(min_edge * 100.0, 9)})
    return out


def fee_per_contract(fee_fn, price, at) -> float:
    """The simulator's fee for ONE contract at `price`, from its own fee
    function evaluated on a large block (so cent rounding of a single
    contract does not overstate it). The ledger still charges the exact fee
    of each simulated fill."""
    n = 10000
    return float(L._fee(fee_fn, n, float(price), at)) / n


def size_within_edge(levels: list, *, p: float, consumed: dict,
                     target_usd: float, cap_usd: float,
                     fee_per_contract_max: float,
                     min_edge: float = MIN_EDGE,
                     net_fee_fn=None) -> dict:
    """The deepest level whose price still clears the threshold sets the
    limit (the ladder is best first, so the clearing levels are a prefix);
    the quantity walks the displayed depth not already consumed, up to that
    limit, capped so that qty x limit + max fees stays within min(target,
    per-order cap). Whole contracts.

    NET OF FEES (`net_fee_fn`, the completed-game V2 policy): a level is
    used only if p - price - fee per contract at that price is STRICTLY
    positive as well -- at a 0.5 pp threshold the fee can consume the whole
    gross edge, and such a level is never bought."""
    ok, gross_ok, fee_stop = [], 0, None
    for lv in levels:
        e = DP.gross_edge(p, lv["price"])
        if not DP.clears(e, min_edge):
            break
        gross_ok += 1
        if net_fee_fn is not None:
            fpc = float(net_fee_fn(lv["price"]))
            if not (e - fpc > DP.EDGE_TOLERANCE_PP):
                fee_stop = {"price": lv["price"],
                            "gross_edge_pp": round(e * 100.0, 9),
                            "fee_per_contract_usd": round(fpc, 9),
                            "net_edge_pp": round((e - fpc) * 100.0, 9)}
                break
        ok.append(lv)
    if not ok:
        return {"qty": 0, "limit": None, "wire": None,
                "depth_within_limit": 0.0, "levels_clearing_gross": gross_ok,
                "fee_stop": fee_stop,
                "why": ("FEES_CONSUME_THE_EDGE_AT_EVERY_CLEARING_LEVEL"
                        if gross_ok else "NO_LEVEL_CLEARS_THE_THRESHOLD")}
    limit = float(ok[-1]["price"])
    depth = sum(max(0.0, float(lv["qty"]) - float(consumed.get(
        SIM._wk(lv["wire"]), 0.0))) for lv in ok)
    budget = float(target_usd) if cap_usd is None else min(float(target_usd), float(cap_usd))
    per = limit + float(fee_per_contract_max)
    qty = math.floor(min(depth, budget / per if per > 0 else 0.0) + 1e-9)
    return {"qty": int(max(qty, 0)), "limit": limit, "wire": ok[-1]["wire"],
            "levels_used": len(ok), "levels_clearing_gross": gross_ok,
            "fee_stop": fee_stop, "depth_within_limit": round(depth, 6),
            "budget_usd": budget, "budget_per_contract_usd": round(per, 9)}


def economics(*, p: float, takes: list, fee_fn, at: float,
              void_states: dict) -> dict:
    """EV of the simulated acquisition: per walked level, gross q (p - px);
    fees by the simulator's fee function; the measured void rate scales the
    gross (a void returns the price; fees are not assumed refunded)."""
    qty = sum(float(t["take"]) for t in takes)
    cost = sum(float(t["take"]) * float(t["price"]) for t in takes)
    fees, fee_rows, fees_ok = 0.0, [], True
    try:
        for t in takes:
            fe = float(L._fee(fee_fn, t["take"], t["price"], at))
            fees += fe
            fee_rows.append({"price": t["price"], "qty": t["take"],
                             "fee_usd": fe})
    except Exception as exc:                                    # noqa: BLE001
        fees_ok = False
        fee_rows.append({"error": "%s: %s" % (type(exc).__name__,
                                              str(exc)[:160])})
    gross = sum(float(t["take"]) * (float(p) - float(t["price"]))
                for t in takes)
    scale = ((1.0 - float(void_states["void_rate"]))
             if void_states.get("applied") else 1.0)
    net = (round(gross * scale - fees, 9) if fees_ok else None)
    vwap = (cost / qty) if qty > 0 else None
    e_vwap = DP.gross_edge(p, vwap) if vwap is not None else None
    return {"qty": round(qty, 6), "acquisition_cost_usd": round(cost, 9),
            "vwap": None if vwap is None else round(vwap, 9),
            "edge_at_vwap_pp": (None if e_vwap is None
                                else round(e_vwap * 100.0, 9)),
            "worst_level_edge_pp": (None if not takes else round(
                DP.gross_edge(p, max(float(t["price"]) for t in takes))
                * 100.0, 9)),
            "fees_usd": round(fees, 9) if fees_ok else None,
            "fees_ok": fees_ok, "fee_basis": fee_rows,
            "fee_function": ("bettor_paper_ledger._fee: the fee function the "
                             "paper simulator charges per fill"),
            "expected_gross_profit_usd": round(gross, 9),
            "void_scale": scale,
            "expected_net_profit_usd": net,
            "net_ev_positive": (net is not None and net > 0)}


def _shortfall(*, pin: dict, best_edge_pp=None, ev=None, depth=None,
               qty=None, book_age=None, threshold_pp=MIN_EDGE_PP) -> dict:
    return {"edge_pp": best_edge_pp, "edge_threshold_pp": threshold_pp,
            "edge_shortfall_pp": (None if best_edge_pp is None else
                                  round(max(0.0, threshold_pp - best_edge_pp),
                                        9)),
            "ev_after_fees_usd": ev, "ev_threshold_usd": 0.0,
            "depth_within_limit": depth, "qty": qty,
            "pinnacle_age_s": pin.get("age_s"),
            "pinnacle_limit_s": pin.get("limit_s"),
            "pinnacle_qualification": pin.get("qualification"),
            "book_age_s": book_age, "book_max_age_s": BOOK_MAX_AGE_S}


def _alternatives(md, *, side: str, p) -> dict:
    out = {"NO_TRADE": {"expected_net_usd": 0.0}}
    other = "SHORT" if side == "LONG" else "LONG"
    lv = SIM.levels_for(md, direction="BUY", holding_side=other)
    if lv["levels"] and p is not None:
        best = lv["levels"][0]
        e = DP.gross_edge(1.0 - float(p), best["price"])
        out["OPPOSITE_SIDE_SAME_MARKET"] = {
            "holding_side": other, "best_price": best["price"],
            "displayed_qty": best["qty"],
            "gross_edge_pp": None if e is None else round(e * 100.0, 9),
            "measure": "1 - p_pinnacle (the complement on the same measure)"}
    else:
        out["OPPOSITE_SIDE_SAME_MARKET"] = {"status": "NO_LEVELS"}
    return out


def qualification_gaps(*, cand: dict) -> list:
    st = dict(cand.get("settlement") or {})
    return [
        {"gap": GAP_NOT_EVIDENCE, "status": "DISCLOSED", "detail": DISCLOSURE},
        {"gap": GAP_NO_MODEL, "status": "NOT_USED",
         "detail": ("no internal model is used; p_internal is NULL and "
                    "Pinnacle is never substituted into it")},
        {"gap": PD.GAP_CALIBRATION, "status": "NOT_CLAIMED",
         "detail": ("the Pinnacle source's calibration is not a "
                    "qualification this benchmark claims or reads (the "
                    "collection worker that measures it is not imported "
                    "here); source version %s"
                    % (cand.get("pinnacle") or {}).get("source_version"))},
        {"gap": PD.GAP_P5, "status": "OPEN",
         "detail": ("book_currency NOT_ESTABLISHED (P5): %s; the valuation "
                    "row is %s and its displayed quote is never the "
                    "execution price" % (BOOK_CURRENCY["basis"],
                                         cand.get("record_purpose")))},
        {"gap": PD.GAP_EXECUTION, "status": "SIMULATED",
         "detail": ("PAPER_SIM_V1: %s; no depth-haircut parameter exists, "
                    "displayed depth only" % ", ".join(
                        SIM.ASSUMPTIONS["marketable"]))},
        {"gap": PD.GAP_SETTLEMENT,
         "status": ("COMPATIBLE" if st.get("compatibility") == "COMPATIBLE"
                    else "OPEN"),
         "detail": "settlement comparison %s" % st.get("compatibility")}]


# ═════════════════════════════════════════════════════════════════════
# THE CONTEXT
# ═════════════════════════════════════════════════════════════════════

CONTEXT_TTL_S = 300.0
_CONTEXT_CACHE: dict = {}


async def _context(conn, ctx: dict) -> dict:
    """Once per pass (or per CONTEXT_TTL_S for the per-valuation hook): the
    measured void rate."""
    if "benchmark" in ctx:
        return ctx["benchmark"]
    key = ctx.get("context_cache_key")
    now = float(ctx["now"])
    if key is not None:
        hit = _CONTEXT_CACHE.get(key)
        if hit is not None and 0.0 <= now - hit["at"] < CONTEXT_TTL_S:
            ctx["benchmark"] = hit["ctx"]
            return ctx["benchmark"]
    ctx["benchmark"] = {"void": await DP.void_measure(conn, through=now)}
    if key is not None:
        _CONTEXT_CACHE.clear()
        _CONTEXT_CACHE[key] = {"at": now, "ctx": ctx["benchmark"]}
    return ctx["benchmark"]


# ═════════════════════════════════════════════════════════════════════
# ONE BOOK READ, SHARED BY EVERY STRATEGY DECIDING THE SAME MARKET
# ═════════════════════════════════════════════════════════════════════

async def book_for(conn, ctx: dict, slug: str, *, basis: str) -> dict:
    """THE CURRENT PAPER BOOK OBSERVATION FOR `slug` IN THIS CONTEXT.

    The first strategy deciding a market in a pass (or in one valuation's
    hook call) reads the book through the read-only client and records it;
    a later strategy deciding the same market reuses THAT observation while
    it is still current (our receipt instant within BOOK_MAX_AGE_S of now --
    every caller still applies the same age check to the decision). An
    unreadable or deadline-cut read is NOT reused: the next strategy reads
    again. Fewer venue requests, never a staler price. Returns {got, obs,
    reused} or {deferred: True} when the pass's read budget is spent."""
    cache = ctx.setdefault("books_by_slug", {})
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    hit = cache.get(slug)
    if hit is not None and not hit["obs"].get("error") and (
            float(clock()) - float(hit["obs"]["observed_at"])
            <= BOOK_MAX_AGE_S):
        return dict(hit, reused=True)
    cfg = ctx["config"]
    if ctx["books_read"] >= int(cfg["cadence"]["max_book_reads_per_pass"]):
        return {"deferred": True}
    got = await PD.read_book_within_deadline(ctx, slug)
    ctx["books_read"] += 1
    obs = await SIM.record_book(conn, slug=slug, read=got,
                                source="PAPER_MARKET_DATA_CLIENT",
                                read_basis=basis)
    cache[slug] = {"got": got, "obs": obs}
    return {"got": got, "obs": obs, "reused": False}


def book_source(bk: dict) -> str:
    """Where a decision's book came from, for the attempt record."""
    if bk.get("reused"):
        return "REUSED_IN_THIS_EVALUATION"
    got = bk.get("got") or {}
    if got.get("shared_read"):
        return "SHARED_RECENT_PROCESS_READ"
    if PD.book_deadline_refusal(got):
        return "READ_CUT_BY_DEADLINE_OR_COOLDOWN"
    if got.get("error") or (bk.get("obs") or {}).get("error"):
        return "READ_FAILED"
    return "FRESH_VENUE_READ"


#: THE BOOK-READ RETRY BUDGET: at most ONE retry per valuation, only while
#: the venue cooldown (plus a margin for the read itself) ends before the
#: Pinnacle reading's 30 s age limit -- a retry that could only meet a stale
#: probability is not scheduled; the decision is recorded at once instead.
BOOK_RETRY_MARGIN_S = 2.0
BOOK_RETRY_MAX_WAIT_S = 20.0


def book_retry_plan(ctx: dict, got: dict, pin: dict) -> dict:
    """Pure: whether a deadline- or cooldown-cut book read earns its one
    retry. `ctx['book_retry_ok']` is set only by the in-cycle hook's first
    attempt."""
    g = got or {}
    detail = g.get("gate_detail") or {}
    cool = detail.get("seconds_left")
    try:
        cool = None if cool is None else max(0.0, float(cool))
    except (TypeError, ValueError):
        cool = None
    out = {"cooldown_s": cool, "retry": False}
    if not ctx.get("book_retry_ok"):
        return dict(out, why="NO_RETRY_BUDGET_IN_THIS_ATTEMPT")
    after = (cool if cool is not None else 1.0) + 0.25
    if after > BOOK_RETRY_MAX_WAIT_S:
        return dict(out, why="COOLDOWN_LONGER_THAN_THE_RETRY_CAP",
                    cap_s=BOOK_RETRY_MAX_WAIT_S)
    age, limit = pin.get("age_s"), pin.get("limit_s")
    if age is None or limit is None:
        return dict(out, why="PINNACLE_AGE_UNKNOWN")
    projected = float(age) + after + BOOK_RETRY_MARGIN_S
    if projected > float(limit):
        return dict(out, why="PINNACLE_WOULD_BE_STALE_AFTER_THE_COOLDOWN",
                    projected_pinnacle_age_s=round(projected, 3),
                    limit_s=limit)
    return dict(out, retry=True, after_s=round(after, 3),
                projected_pinnacle_age_s=round(projected, 3), limit_s=limit)


# ═════════════════════════════════════════════════════════════════════
# EVERY ATTEMPTED EVALUATION, RECORDED (migration 189)
# ═════════════════════════════════════════════════════════════════════

async def record_attempt(conn, ctx: dict, *, valuation_id, strategy: str,
                         via: str, res: dict, attempt_no: int = 1,
                         elapsed_s=None) -> None:
    """One `paper_evaluation_attempts` row for one strategy's attempt on one
    valuation, whatever happened. Never raises; an absent table is
    skipped (the attempt is still in the decision or hook-failure record)."""
    r = res or {}
    if r.get("timeout"):
        outcome = "TIMEOUT"
    elif r.get("error"):
        outcome = "ERROR"
    elif r.get("deferred") and r.get("why") == "BOOK_RETRY":
        outcome = "DEFERRED_FOR_BOOK_RETRY"
    elif r.get("deferred"):
        outcome = "DEFERRED_BOOK_BUDGET"
    elif r.get("duplicate"):
        outcome = "DUPLICATE"
    elif r.get("retry_outcome") in ("RETRY_SCHEDULED", "RETRY_NOT_SCHEDULED"):
        outcome = r["retry_outcome"]
    elif r.get("decision_id") or r.get("verdict"):
        outcome = "DECIDED"
    else:
        return                      # switched off: not an attempt
    try:
        await conn.execute(
            "INSERT INTO paper_evaluation_attempts (session_id, account_id, "
            " valuation_id, strategy, via, attempt_no, outcome, decision_id, "
            " verdict, refusal, book_source, book_age_s, cooldown_s, "
            " elapsed_s, detail) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,"
            " $12,$13,$14,$15::jsonb)",
            ctx.get("session_id"), ctx.get("account_id"),
            None if valuation_id is None else int(valuation_id),
            str(strategy), via, int(attempt_no), outcome,
            r.get("decision_id"), r.get("verdict"), r.get("refusal"),
            r.get("book_source"), r.get("book_age_s"), r.get("cooldown_s"),
            elapsed_s if elapsed_s is not None else r.get("elapsed_s"),
            json.dumps({k: r.get(k) for k in (
                "why", "error", "retry", "order_id", "reused_book",
                "selection") if r.get(k) is not None}, default=str))
    except Exception:                                           # noqa: BLE001
        pass


# ═════════════════════════════════════════════════════════════════════
# THE DECISION
# ═════════════════════════════════════════════════════════════════════

async def decide_one(conn, ctx: dict, row: dict, pol=None) -> dict:
    """ONE BENCHMARK DECISION, persisted first; an ENTER then submits ONE
    paper order naming it (the simulator fills it after the delay)."""
    pol = _pol(pol)
    cg = pol["kind"] == "COMPLETED_GAME"
    STRATEGY = pol["strategy"]                                  # noqa: N806
    VERSION = pol["version"]                                    # noqa: N806
    DISCLOSURE = pol["disclosure"]                              # noqa: N806
    cfg = ctx["config"]
    ent = cfg["entry"]
    sim_cfg = cfg["simulator"]
    clock = ctx.get("clock") or (lambda: float(ctx["now"]))
    at = float(clock())
    fee_fn = ctx.get("fee_fn")
    for k in ("last_book_source", "last_book_age_s", "last_book_cooldown_s"):
        ctx.pop(k, None)
    bctx = await _context(conn, ctx)
    row = dict(row)
    cand = DP.candidate_from_row(row)
    side = PD.holding_side_of(cand.get("side"))
    did = decision_id_for(ctx["session_id"], cand["valuation_id"], pol)
    cat = await DP.catalogue_row(conn, cand.get("us_market_slug"))
    fxr = await DP.fixture_row(conn, cand.get("condition_id"))
    label = dict(DP.instrument_label(cand, catalogue_row=cat,
                                     fixture_row=fxr),
                 strategy=STRATEGY, disclosure=DISCLOSURE,
                 book_currency="NOT_ESTABLISHED",
                 **({"policy_version": VERSION,
                     "economics_label": ECONOMICS_LABEL} if cg else {}))
    refusals: list = []
    # THE THRESHOLD THIS DECISION RUNS: the completed-game policy's ACTIVE
    # parameter version (fail-closed to V1); the strict policy's constant.
    params = await cg_parameters(conn, ctx) if cg else None
    min_edge_pp = (float(params["values"]["min_gross_edge_pp"]) if cg
                   else MIN_EDGE_PP)
    min_edge = min_edge_pp / 100.0
    match = (completed_game_match(cand, row) if cg
             else contract_match(cand, row))
    refusals.extend(match["refusals"])
    real = DP._realism(cand, cat)
    if real["status"] == DP.FAIL:
        refusals.append(R_NOT_REAL)
    # ── THE PROBABILITY: stored, oriented, re-aged at THIS instant ─────
    pin = PD._pinnacle(cand, at=at, max_age=float(ent["pinnacle_max_age_s"]))
    pin.update(decided_via=ctx.get("decided_via") or PD.DECIDED_VIA_PASS,
               decided_at=at, valuation_decided_at=cand.get("decided_at"),
               decision_lag_after_valuation_s=(
                   None if cand.get("decided_at") is None
                   else round(at - float(cand["decided_at"]), 3)),
               strategy=STRATEGY, probability_is=(
                   "the stored de-vigged Pinnacle probability of the event "
                   "this contract pays on (external_valuations.probability, "
                   "oriented once by the lane)"),
               contract_match=match, real_event=real,
               valuation_record_purpose=cand.get("record_purpose"),
               displayed_quote_used_as_price=False)
    if pin.get("refusal"):
        refusals.append(pin["refusal"])
    cross = await cross_strategy_exposure(
        conn, account_id=ctx["account_id"], strategy=STRATEGY,
        slug=cand.get("us_market_slug"), fixture=cand.get("fixture"))
    pin["cross_strategy_exposure"] = cross
    if cross["held"]:
        refusals.append(R_CROSS_STRATEGY)
    p = pin.get("p")
    obs, md, levels, edges = None, None, [], []
    sized: dict = {"qty": 0, "limit": None, "wire": None}
    econ: dict | None = None
    book_age = None
    if not refusals:
        # THE BOOK IS READ ONLY FOR A CANDIDATE THAT COULD STILL ENTER.
        bk = await book_for(conn, ctx, cand["us_market_slug"],
                            basis=STRATEGY)
        if bk.get("deferred"):
            return {"deferred": True, "why": "BOOK_READ_BUDGET"}
        got, obs = bk["got"], bk["obs"]
        md = obs.get("market_data")
        lv = SIM.levels_for(md, direction="BUY", holding_side=side)
        levels = lv["levels"]
        book_age = round(max(0.0, float(clock()) - float(obs["observed_at"])),
                         3)
        ctx["last_book_source"] = book_source(bk)
        ctx["last_book_age_s"] = book_age
        if PD.book_deadline_refusal(got):
            retry = book_retry_plan(ctx, got, pin)
            ctx["last_book_cooldown_s"] = retry.get("cooldown_s")
            if retry["retry"]:
                # NOT RECORDED YET: one bounded retry after the cooldown, while
                # the Pinnacle reading is still inside its 30 s rule. The
                # retry decides (and records) this valuation either way.
                return {"deferred": True, "why": "BOOK_RETRY",
                        "retry_after_s": retry["after_s"], "retry": retry}
            refusals.append(PD.R_BOOK_DEADLINE)
        elif obs.get("error") or not levels:
            refusals.append(R_NO_BOOK)
        elif book_age > BOOK_MAX_AGE_S:
            refusals.append(R_BOOK_NOT_CURRENT)
        else:
            edges = level_edges(levels, p, min_edge=min_edge)
            consumed = await SIM._consumed(conn, cand["us_market_slug"],
                                           lv["side"], obs["obs_id"])
            sized = size_within_edge(
                levels, p=p, consumed=consumed,
                target_usd=float(ent["target_order_usd"]),
                cap_usd=cfg["risk"].get("per_order_cap_usd"),
                fee_per_contract_max=float(L.max_fee_for(
                    1, 0.5, at=at, fee_fn=fee_fn)), min_edge=min_edge,
                net_fee_fn=((lambda px: fee_per_contract(fee_fn, px, at))
                            if cg else None))
            if not edges[0]["clears_min_edge"]:
                refusals.append(R_EDGE)
            elif (sized.get("why")
                  == "FEES_CONSUME_THE_EDGE_AT_EVERY_CLEARING_LEVEL"):
                refusals.append(R_FEES_CONSUME_EDGE)
            elif sized["qty"] < 1:
                refusals.append(R_NO_QTY)
            else:
                walk = SIM.walk(levels, consumed=consumed,
                                limit=sized["limit"], qty=sized["qty"],
                                direction="BUY", allow_partial=True)
                if cg:
                    # CONDITIONAL ON ORDINARY COMPLETION. No refund and no
                    # void rate is assumed; exceptional payoffs are shown
                    # separately with their probabilities UNMEASURED.
                    states = {"applied": False,
                              "why": ("not used: this policy's EV is "
                                      "conditional on ordinary completion")}
                    base_e = conditional_economics(
                        p=p, takes=walk["takes"], fee_fn=fee_fn, at=at)
                    base_e["exceptional_settlement"] = exceptional_scenarios(
                        cand=cand, row=row, qty=base_e["qty"],
                        vwap=base_e["vwap"])
                else:
                    states = DP.settlement_states(bctx["void"],
                                                  void_refunds_price=True)
                    base_e = economics(p=p, takes=walk["takes"],
                                       fee_fn=fee_fn, at=at,
                                       void_states=states)
                econ = dict(base_e,
                            settlement_states=states,
                            walk=[{"price": t["price"], "wire": t["wire"],
                                   "take": t["take"],
                                   "edge_pp": round(DP.gross_edge(
                                       p, t["price"]) * 100.0, 9)}
                                  for t in walk["takes"]])
                if not econ["fees_ok"]:
                    refusals.append(R_FEES)
                elif not econ["net_ev_positive"]:
                    refusals.append(R_NET)
    verdict = DP.ENTER if not refusals else DP.REFUSE
    best_edge = edges[0]["edge_pp"] if edges else None
    short = _shortfall(pin=pin, best_edge_pp=best_edge,
                       ev=(econ or {}).get("expected_net_profit_usd"),
                       depth=sized.get("depth_within_limit"),
                       qty=sized.get("qty"), book_age=book_age,
                       threshold_pp=min_edge_pp)
    economics_rec = {
        "strategy": STRATEGY, "version": VERSION, "disclosure": DISCLOSURE,
        "probability": p, "probability_basis": "PINNACLE_ONLY_DEVIGGED_STORED",
        "threshold_edge_pp": min_edge_pp,
        "threshold_edge_probability": round(min_edge, 9),
        "edge_rule": ("p_pinnacle - level price >= %.4f at EVERY level used"
                      % min_edge
                      + ("; and p - price - fee per contract > 0 at every "
                         "level bought" if cg else "")),
        "levels_clearing_gross": sized.get("levels_clearing_gross"),
        "fee_stop": sized.get("fee_stop"),
        "ev_rule": "expected net profit after the simulator's fees > 0",
        "levels": edges[:10], "best_level_edge_pp": best_edge,
        "limit_price": sized.get("limit"),
        "levels_used": sized.get("levels_used"),
        "depth_within_limit": sized.get("depth_within_limit"),
        "depth_basis": ("DISPLAYED depth only, net of paper liquidity "
                        "already consumed at this observation; PAPER_SIM_V1 "
                        "has no depth-haircut parameter"),
        "budget_usd": sized.get("budget_usd"),
        "proposed_qty": sized.get("qty"),
        "latency": {"decision_to_execution_delay_s": float(
            sim_cfg["decision_to_execution_delay_s"]),
            "fill_rule": sim_cfg.get("marketable")},
        "book_age_s": book_age, "book_max_age_s": BOOK_MAX_AGE_S,
        "book_currency": BOOK_CURRENCY,
        "acquisition": econ, "shortfall": short,
        "refusals": refusals}
    if cg:
        economics_rec.update(
            label=ECONOMICS_LABEL, conditional_on="ORDINARY_COMPLETION",
            ev_rule=("modelled net profit after the simulator's fees > 0, "
                     "CONDITIONAL on the game being ordinarily completed; "
                     "not risk-adjusted, not proven positive EV"),
            exceptional_terms=match.get("exceptional_terms"),
            mapping_assumptions={
                "reference": "PINNACLE_DEVIG_V1 (stored, oriented once by "
                             "the lane)",
                "devig_method": (cand.get("pinnacle") or {}).get("method"),
                "overround": (cand.get("pinnacle") or {}).get("overround"),
                "payout_event": cand.get("payout_event"),
                "payout_is_complement": cand.get("payout_is_complement"),
                "sport_family": cand.get("sport_family"),
                "grading_period": next(
                    ((c.get("book") or {}).get("period")
                     for c in match["checks"]
                     if c["check"] == "ordinary_completion_grading_period"),
                    None)})
    conditions = [
        {"condition": "contract_outcome_settlement_match",
         "passed": match["established"], "refusals": match["refusals"]},
        {"condition": "pinnacle_fresh_at_the_decision_instant",
         "passed": pin.get("qualified") is True,
         "value": pin.get("age_s"), "threshold": pin.get("limit_s"),
         "units": "seconds", "refusal": pin.get("refusal")},
        {"condition": "current_paper_book_with_depth",
         "passed": (None if obs is None else
                    bool(levels) and book_age is not None
                    and book_age <= BOOK_MAX_AGE_S),
         "value": book_age, "threshold": BOOK_MAX_AGE_S, "units": "seconds"},
        {"condition": ("edge_at_least_min_gross_edge_pp_at_every_level_used"
                       if cg else "edge_at_least_5pp_at_every_level_used"),
         "passed": None if not edges else bool(edges[0]["clears_min_edge"]),
         "value": best_edge, "threshold": min_edge_pp,
         "units": "percentage points"},
        {"condition": "positive_ev_after_fees",
         "passed": (False if R_FEES_CONSUME_EDGE in refusals else
                    None if econ is None else econ["net_ev_positive"]),
         "value": (econ or {}).get("expected_net_profit_usd"),
         "threshold": 0.0, "units": "USD",
         "rule": "strictly greater than zero",
         **({"fee_stop": sized.get("fee_stop")}
            if R_FEES_CONSUME_EDGE in refusals else {})}]
    policy_decision = {
        "strategy": STRATEGY, "policy_version": VERSION,
        "threshold_edge_pp": min_edge_pp,
        "threshold_edge_probability": round(min_edge, 9),
        "disclosure": DISCLOSURE, "p_internal": None, "p_blended": None,
        "economics_label": ECONOMICS_LABEL if cg else None,
        "p_pinnacle": p, "conditions": conditions,
        "gross_edge_pp": best_edge,
        "edge_at_vwap_pp": (econ or {}).get("edge_at_vwap_pp"),
        "net_expected_profit_usd": (econ or {}).get(
            "expected_net_profit_usd"),
        "fees_usd": (econ or {}).get("fees_usd"),
        "shortfall": short, "book_currency": BOOK_CURRENCY,
        "admitted": verdict == DP.ENTER,
        "refusal": refusals[0] if refusals else None,
        "refusals": refusals}
    if cg:
        # WHICH PARAMETER VERSION DECIDED THIS, AND WHERE IT CAME FROM
        # (migration 186), in the record's existing JSON.
        policy_decision["parameters"] = params
        economics_rec["parameters"] = params
    gaps = qualification_gaps(cand=cand)
    optimistic = (SIM.optimistic_fill(md, direction="BUY", holding_side=side,
                                      qty=sized["qty"], limit=sized["limit"])
                  if verdict == DP.ENTER else None)
    internal_rec = {"available": False, "p": None, "strategy": STRATEGY,
                    "reason": ("%s uses no "
                               "internal model by design; Pinnacle is never "
                               "substituted into this field (it is in "
                               "p_pinnacle / pinnacle)" % STRATEGY)}
    book = None if obs is None else {
        "book_obs_id": obs["obs_id"], "observed_at": obs["observed_at"],
        "observed_at_is": "OUR_RECEIPT_INSTANT",
        "age_at_decision_s": book_age, "error": obs.get("error"),
        "side_consumed": SIM.side_consumed("BUY", side or "LONG"),
        "levels": levels[:10], "depth_levels": len(levels),
        "displayed_depth": round(sum(float(x["qty"]) for x in levels), 6),
        "book_currency": BOOK_CURRENCY,
        "basis": "OBSERVED_PAPER_BOOK_LEVELS_NOT_THE_VALUATION_QUOTE"}
    rec = {"decision_id": did, "verdict": verdict, "strategy": STRATEGY,
           "refusal": refusals[0] if refusals else None,
           "refusals": refusals, "shortfall": short,
           "book_source": ctx.get("last_book_source"),
           "book_age_s": ctx.get("last_book_age_s"),
           "cooldown_s": ctx.get("last_book_cooldown_s")}
    alts = _alternatives(md, side=side or "LONG", p=p)
    # THE LEARNING RECORD (migration 185): versions, the inputs as read with
    # their SHA-256, prices, fees, alternatives and a plain explanation, in
    # the same INSERT. Building it never stops the decision being recorded.
    provenance = PD.decision_provenance(
        strategy=STRATEGY, code_version=VERSION, policy_version=VERSION,
        row=row, session=ctx.get("session"), verdict=verdict,
        refusals=refusals, policy_decision=policy_decision,
        internal_model=internal_rec, pinnacle=pin, book=book,
        limit_price=sized.get("limit"), qty=sized.get("qty"),
        alternatives=alts, optimistic=optimistic,
        simulator_version=cfg["simulator_version"],
        decided_via=pin.get("decided_via"), at=at)
    inserted = await conn.fetchval(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, valuation_id, us_market_slug, holding_side, intent, "
        " fixture, label, verdict, refusal, refusals, p_internal, "
        " internal_model, p_pinnacle, pinnacle, p_blended, book_obs_id, "
        " book, proposed_qty, limit_price, economics, qualification_gaps, "
        " policy_version, policy_decision, alternatives, optimistic, "
        " simulator_version, strategy, provenance) VALUES ($1,$2,$3,$4,$5,"
        " $6,$7,$8,$9,$10::jsonb,$11,$12,$13,NULL,$14::jsonb,$15,$16::jsonb,"
        " NULL,$17,$18::jsonb,$19,$20,$21::jsonb,$22::jsonb,$23,$24::jsonb,"
        " $25::jsonb,$26::jsonb,$27,$28,$29::jsonb) ON CONFLICT DO NOTHING "
        " RETURNING decision_id",
        did, ctx["session_id"], ctx["account_id"], L._ts(at),
        cand["valuation_id"], cand.get("us_market_slug"), side,
        cand.get("side"), cand.get("fixture"),
        json.dumps(label, default=str), verdict, rec["refusal"], refusals,
        json.dumps(internal_rec, default=str), p,
        json.dumps(pin, default=str),
        None if obs is None else obs["obs_id"],
        None if book is None else json.dumps(book, default=str),
        (None if not sized.get("qty") else L.D(sized["qty"])),
        (None if sized.get("limit") is None else L.D(sized["limit"])),
        json.dumps(economics_rec, default=str), json.dumps(gaps, default=str),
        VERSION, json.dumps(policy_decision, default=str),
        json.dumps(alts, default=str),
        None if optimistic is None else json.dumps(optimistic, default=str),
        cfg["simulator_version"], STRATEGY,
        json.dumps(provenance, default=str))
    if inserted is None:
        return dict(rec, duplicate=True)
    if verdict != DP.ENTER:
        return rec
    # ── ONLY NOW, THE PAPER ORDER ─────────────────────────────────────
    delay = float(sim_cfg["decision_to_execution_delay_s"])
    order = {"idempotency_key": "%s:ENTRY" % did,
             "account_id": ctx["account_id"],
             "session_id": ctx["session_id"],
             "group_id": group_id_for(did), "role": "ENTRY",
             "direction": "BUY", "holding_side": side,
             "intent": cand.get("side"),
             "us_market_slug": cand["us_market_slug"],
             "fixture": cand.get("fixture"), "label": label,
             "order_type": ent["order_type"],
             "time_in_force": ent["time_in_force"],
             "allow_partial": bool(ent["allow_partial"]),
             "qty": sized["qty"], "limit_price": sized["limit"],
             "wire_price": sized["wire"], "decision_id": did,
             "decided_at": at, "eligible_at": at + delay,
             "expires_at": at + float(sim_cfg["marketable_ttl_s"]),
             "simulator_version": cfg["simulator_version"],
             "strategy": STRATEGY}
    got = await L.submit_order(conn, order, caps=cfg["risk"],
                               fee_fn=fee_fn, now=at, exclusive_fixture=True)
    rec["order"] = {k: got.get(k) for k in ("ok", "refusal", "duplicate")}
    if got.get("ok"):
        rec["order_id"] = got["order"]["order_id"]
        rec["eligible_at"] = at + delay
    else:
        rec["order_refusal"] = got.get("refusal")
        await PD._finding(conn, ctx, kind=R_ORDER_REFUSED, subject=did,
                          detail={"refusal": got.get("refusal"),
                                  "decision_id": did, "at": at,
                                  "strategy": STRATEGY,
                                  "disclosure": DISCLOSURE,
                                  **{k: v for k, v in got.items()
                                     if k not in ("ok",)}})
    return rec


# ═════════════════════════════════════════════════════════════════════
# THE PASS STEP AND THE PER-VALUATION HOOK
# ═════════════════════════════════════════════════════════════════════

async def step(conn, ctx: dict, pol=None, decide=None) -> dict:
    """THE BENCHMARK'S PAPER STEP (after Derek's, before the delayed fill
    step, which simulates its orders too), for one policy. `decide` is the
    policy's own decision function (the maker and exploration modules pass
    theirs); every attempt is a `paper_evaluation_attempts` row."""
    pol = _pol(pol)
    en = await enablement(conn, pol)
    out: dict[str, Any] = {"strategy": pol["strategy"],
                           "enabled": en["enabled"],
                           "decisions_recorded": 0, "orders_submitted": 0,
                           "verdicts": {}, "refusals": {}, "deferred": 0}
    if not en["enabled"]:
        return dict(out, refusal=en["refusal"])
    cfg = ctx["config"]
    at = float(ctx["now"])
    rows = [dict(r) for r in await conn.fetch(
        CANDIDATES_SQL, EXPERIMENT_ID,
        at - float(cfg["entry"]["valuation_lookback_s"]), at + 1.0,
        ctx["session_id"], int(cfg["cadence"]["max_decisions_per_pass"]),
        pol["strategy"])]
    out["candidates"] = len(rows)
    ctx.setdefault("pending_entries", [])
    fn = decide or decide_one
    for row in rows:
        if time.monotonic() > ctx["deadline"]:
            out["budget_exhausted"] = True
            break
        t0 = time.monotonic()
        try:
            rec = await fn(conn, ctx, row, pol)
        except Exception as exc:                                # noqa: BLE001
            k = "BENCHMARK_DECISION:%s" % type(exc).__name__
            out.setdefault("errors", {})[k] = str(exc)[:200]
            await record_attempt(
                conn, ctx, valuation_id=row.get("id"),
                strategy=pol["strategy"], via="PAPER_PASS",
                res={"error": "%s: %s" % (type(exc).__name__,
                                          str(exc)[:200])},
                elapsed_s=round(time.monotonic() - t0, 3))
            continue
        await record_attempt(conn, ctx, valuation_id=row.get("id"),
                             strategy=pol["strategy"], via="PAPER_PASS",
                             res=rec,
                             elapsed_s=round(time.monotonic() - t0, 3))
        if rec.get("deferred"):
            out["deferred"] += 1
            continue
        if rec.get("duplicate"):
            out["already_recorded"] = out.get("already_recorded", 0) + 1
            continue
        out["decisions_recorded"] += 1
        out["verdicts"][rec["verdict"]] = out["verdicts"].get(
            rec["verdict"], 0) + 1
        if rec.get("refusal"):
            out["refusals"][rec["refusal"]] = out["refusals"].get(
                rec["refusal"], 0) + 1
        if rec.get("order_id"):
            out["orders_submitted"] += 1
            if rec.get("resting"):
                # a resting order is simulated on books observed after its
                # placement (the books / simulate steps), not by the
                # marketable entry's delayed fill step
                out["resting_orders_placed"] = out.get(
                    "resting_orders_placed", 0) + 1
            else:
                ctx["pending_entries"].append(rec)
    return out


async def step_completed_game(conn, ctx: dict) -> dict:
    """THE COMPLETED-GAME PAPER POLICY'S STEP (same pass, own strategy)."""
    return await step(conn, ctx, CG_POLICY)


async def decide_for_hook(conn, ctx: dict, row: dict, *,
                          timeout_s: float, pol=None, decide=None) -> dict:
    """The per-valuation hook's benchmark decision: guarded, bounded, never
    raises (CancelledError excepted). The context is SHARED across the
    strategies deciding this valuation (`books_by_slug`): the deadline is
    reset per strategy, the book read is not repeated while it is current."""
    pol = _pol(pol)
    STRATEGY = pol["strategy"]                                  # noqa: N806
    t0 = time.monotonic()
    try:
        en = await enablement(conn, pol)
        if not en["enabled"]:
            return {"decided": False, "why": en["refusal"]}
        ctx.setdefault("books_by_slug", {})      # shared by the copies
        ctx = dict(ctx, deadline=time.monotonic() + float(timeout_s))
        ctx.pop("benchmark", None)
        rec = await asyncio.wait_for((decide or decide_one)(
            conn, ctx, row, pol), timeout_s)
        return dict({k: rec.get(k) for k in (
            "decision_id", "verdict", "refusal", "order_id", "duplicate",
            "deferred", "strategy", "book_source", "book_age_s",
            "cooldown_s", "retry_after_s", "retry", "selection")},
            decided=not rec.get("deferred"), why=rec.get("why"),
            elapsed_s=round(time.monotonic() - t0, 3))
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        return {"decided": False, "strategy": STRATEGY, "timeout": True,
                "error": "TimeoutError: the in-cycle decision exceeded its "
                         "%.1f s budget" % float(timeout_s),
                "elapsed_s": round(time.monotonic() - t0, 3)}
    except Exception as exc:                                    # noqa: BLE001
        return {"decided": False, "strategy": STRATEGY,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                "elapsed_s": round(time.monotonic() - t0, 3)}


# ═════════════════════════════════════════════════════════════════════
# XAVIER: THE SAME POLICY FOR THE LIFE OF THE POSITION
# ═════════════════════════════════════════════════════════════════════

async def cross_strategy_exposure(conn, *, account_id: str, strategy: str,
                                  slug, fixture) -> dict:
    """OPEN EXPOSURE TO THE SAME CONTRACT OR FIXTURE HELD BY ANOTHER
    STRATEGY on this account: an entry order still working, or a filled
    entry whose position is still open. Read-only; never raises (an
    unreadable answer is treated as held, so it refuses)."""
    from .. import bettor_paper_limits as LIMITS
    if LIMITS.uses_owner_policy(account_id):
        return {"held": False, "by": [], "allocation_rule": "NO_FIXTURE_ALLOCATION_LIMIT", "capital_policy": LIMITS.VERSION}
    try:
        rows = await conn.fetch(
            "SELECT DISTINCT o.strategy, o.group_id, o.us_market_slug, "
            "       o.state, o.filled_qty "
            "  FROM paper_orders o WHERE o.account_id=$1 AND o.role='ENTRY' "
            "   AND o.strategy <> $2 AND (o.us_market_slug = $3 "
            "        OR ($4::text IS NOT NULL AND o.fixture = $4)) "
            "   AND (o.state = ANY($5::text[]) OR o.filled_qty > 0)",
            account_id, strategy, slug, fixture, list(L.OPEN_STATES))
        if not rows:
            return {"held": False, "by": []}
        open_groups = {p["group_id"] for p in await L.positions(
            conn, account_id) if p["open_qty"] > 1e-9}
        by = [{"strategy": r["strategy"], "group_id": r["group_id"],
               "us_market_slug": r["us_market_slug"], "state": r["state"]}
              for r in rows if r["state"] in L.OPEN_STATES
              or r["group_id"] in open_groups]
        return {"held": bool(by), "by": by}
    except Exception as exc:                                    # noqa: BLE001
        return {"held": True, "by": [], "error": type(exc).__name__}


async def group_strategy(conn, group_id: str) -> str:
    """The strategy of a paper group: its entry order's (migration 182). A
    group never switches policy."""
    s = await conn.fetchval(
        "SELECT strategy FROM paper_orders WHERE group_id=$1 "
        "   AND role='ENTRY' ORDER BY created_at LIMIT 1", group_id)
    return s or TWO_MODEL_STRATEGY


SOURCE_FEED_CURRENT = "PINNAPI_FEED_CURRENT"


async def xavier_measure(conn, ctx: dict, *, pos: dict,
                         strategy=None, feed=None) -> dict:
    """P(the held side pays) for a BENCHMARK position, on the benchmark's
    own measure -- the de-vigged Pinnacle probability alone, for the same
    contract and payout outcome -- never the two-model blend:

      PINNACLE_ONLY_CURRENT     a reading for the same contract within the
                                lookback that is fresh under the 30 s rule
      PINNAPI_FEED_CURRENT      no fresh reading, but `feed` (the caller's
                                in-process PinnAPI cache read for the held
                                contract, paper_xavier._held_feed) answered
                                ok under the same 30 s rule: fresh, with the
                                feed's provenance
      PINNACLE_ONLY_LATEST      the latest such reading, older (stale, said)
      ENTRY_TIME_MEASURE        the entry decision's p_pinnacle (stale, said)

    A feed that refuses leaves the stale measure exactly as it was, with
    the refusal named in `feed_refusal`. Without `feed` nothing is read.
    """
    pol = policy_for(strategy) or STRICT_POLICY
    STRATEGY = pol["strategy"]                                  # noqa: N806
    DISCLOSURE = pol["disclosure"]                              # noqa: N806
    c = ctx.get("clock")
    at = float(c()) if c else float(ctx["now"])
    ent = ctx["config"]["entry"]
    lookback = float(ent["valuation_lookback_s"])
    max_age = float(ent["pinnacle_max_age_s"])
    intent = DP.LONG if pos["holding_side"] == "LONG" else DP.SHORT
    d = await conn.fetchrow(
        "SELECT d.decision_id, d.p_pinnacle, d.decided_at, d.valuation_id "
        "  FROM paper_decisions d JOIN paper_orders o "
        "    ON o.decision_id = d.decision_id "
        " WHERE o.group_id=$1 AND o.role='ENTRY' AND d.strategy=$2 LIMIT 1",
        pos["group_id"], STRATEGY)
    base = {"strategy": STRATEGY, "p_internal": None,
            "internal_model": {"available": False},
            "void_applied": False, "disclosure": DISCLOSURE}
    if pol["kind"] in COMPLETED_GAME_KINDS:
        # THE TWO KINDS OF STATE, NEVER BLENDED: p below is the held side's
        # probability IF THE GAME IS ORDINARILY COMPLETED. What the position
        # pays if it is not is the venue's own stated payout, with its
        # frequency unmeasured -- no hedge is described as covering it.
        base.update(
            measure_is="CONDITIONAL_ON_ORDINARY_COMPLETION",
            economics_label=ECONOMICS_LABEL,
            exceptional_states=(
                "not priced into p: the venue's own payout applies (e.g. the "
                "last fair market price for a game not rescheduled within "
                "two weeks), frequency UNMEASURED; no hedge is described as "
                "guaranteeing profit across states that are not established"))
    contract = None
    if d is not None and d["valuation_id"] is not None:
        contract = await conn.fetchrow(
            "SELECT payout_event, payout_is_complement FROM "
            " external_valuations WHERE id=$1", int(d["valuation_id"]))
    stale_out = None
    if contract is not None:
        v = await conn.fetchrow(
            "SELECT id, probability, observed_at FROM external_valuations "
            " WHERE us_market_slug=$1 AND buy_intent=$2 "
            "   AND probability IS NOT NULL AND payout_event=$3 "
            "   AND payout_is_complement=$4 "
            "   AND decided_at > to_timestamp($5) "
            " ORDER BY decided_at DESC LIMIT 1", pos["us_market_slug"],
            intent, contract["payout_event"],
            bool(contract["payout_is_complement"]), at - lookback)
        if v is not None:
            obs = L._epoch(v["observed_at"])
            raw_age = None if obs is None else at - obs
            age = None if raw_age is None else round(raw_age, 3)
            # a stamp after `at` is a clock disagreement, never fresh
            # Compare before display rounding: -0.0004 rounds to -0.0,
            # and 30.0004 rounds to 30.0. Neither is inside [0, 30].
            fresh = raw_age is not None and 0 <= raw_age <= max_age
            reading = dict(base, p=float(v["probability"]),
                           source=("PINNACLE_ONLY_CURRENT" if fresh
                                   else "PINNACLE_ONLY_LATEST"),
                           p_pinnacle=float(v["probability"]),
                           pinnacle_at=obs, pinnacle_age_s=age,
                           pinnacle_limit_s=max_age, valuation_id=v["id"],
                           stale=not fresh)
            if fresh:
                return reading
            stale_out = reading
    if stale_out is None and d is not None and d["p_pinnacle"] is not None:
        stale_out = dict(base, p=float(d["p_pinnacle"]),
                         source="ENTRY_TIME_MEASURE",
                         at=L._epoch(d["decided_at"]), stale=True,
                         why=("no Pinnacle reading for this contract within "
                              "the lookback; the entry decision's p_pinnacle "
                              "is used and labelled stale"))
    if stale_out is None:
        stale_out = dict(base, p=None, source=None, stale=True,
                         why="NO_SETTLEMENT_MEASURE_FOR_THIS_POSITION")
    if feed is None:
        return stale_out
    # THE HELD CONTRACT ON THE IN-PROCESS FEED: only an ok read under the
    # same limit becomes fresh; any refusal keeps the stale measure as is.
    try:
        cur = await feed(
            conn, pos=pos, at=at, max_age_s=max_age,
            payout_event=None if contract is None
            else contract["payout_event"],
            payout_is_complement=bool(contract is not None
                                      and contract["payout_is_complement"]))
    except Exception as exc:                                    # noqa: BLE001
        cur = {"ok": False, "reason": "FEED_READ_FAILED",
               "error": type(exc).__name__}
    if not cur.get("ok"):
        return dict(stale_out, feed_refusal=cur.get("reason"),
                    feed_detail={k: cur[k] for k in (
                        "sport_id", "feed_event_id", "market_key",
                        "designation", "provenance", "why", "error")
                        if cur.get(k) is not None})
    prov = cur["provenance"]
    return dict(base, p=float(cur["p"]), source=SOURCE_FEED_CURRENT,
                p_pinnacle=float(cur["p"]),
                pinnacle_at=prov["source_change_ms"] / 1000.0,
                pinnacle_age_s=prov.get("quote_age_s"),
                pinnacle_limit_s=max_age, stale=False,
                feed={"epoch": prov.get("epoch"),
                      "quote_age_s": prov.get("quote_age_s"),
                      "source_change_ms": prov.get("source_change_ms"),
                      "frame_ts_ms": prov.get("frame_ts_ms"),
                      "received_ms": prov.get("received_ms"),
                      "evaluated_ms": prov.get("evaluated_ms"),
                      "parser": prov.get("parser"),
                      "stream": prov.get("stream"),
                      "sport_id": cur.get("sport_id"),
                      "feed_event_id": cur.get("feed_event_id"),
                      "market_key": cur.get("market_key"),
                      "designation": cur.get("designation"),
                      "payout_event": cur.get("payout_event"),
                      "payout_is_complement": cur.get("payout_is_complement"),
                      "p_selection": cur.get("p_selection"),
                      "devig": cur.get("devig")},
                replaced_stale_source=stale_out.get("source"),
                replaced_stale_age_s=stale_out.get("pinnacle_age_s"))


# ═════════════════════════════════════════════════════════════════════
# AUDREY: THE BENCHMARK'S OWN SECTION AND FILL AUDIT
# ═════════════════════════════════════════════════════════════════════

async def has_records(conn, account_id: str, pol=None) -> bool:
    STRATEGY = _pol(pol)["strategy"]                          # noqa: N806
    try:
        return bool(await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM paper_decisions WHERE "
            " account_id=$1 AND strategy=$2)", account_id, STRATEGY))
    except Exception:                                           # noqa: BLE001
        return False


async def report_section(conn, *, account_id: str, t0, t1,
                         pol=None) -> dict:
    """THE BENCHMARK'S PERFORMANCE ROWS FOR THE REPORT DAY, apart from the
    two-model strategy's, each labelled with the strategy."""
    pol = _pol(pol)
    STRATEGY, VERSION = pol["strategy"], pol["version"]         # noqa: N806
    DISCLOSURE = pol["disclosure"]                              # noqa: N806
    dec = await conn.fetch(
        "SELECT verdict, coalesce(refusal, 'ENTER') AS reason, count(*) AS n,"
        "       count(DISTINCT us_market_slug) AS markets "
        "  FROM paper_decisions WHERE account_id=$1 AND strategy=$2 "
        "   AND decided_at >= $3 AND decided_at < $4 GROUP BY 1, 2 "
        " ORDER BY 3 DESC", account_id, STRATEGY, t0, t1)
    q = await conn.fetchrow(
        "SELECT count(*) AS n, "
        "       sum((policy_decision->>'net_expected_profit_usd')::float8) "
        "         AS ev, avg((policy_decision->>'gross_edge_pp')::float8) "
        "         AS edge FROM paper_decisions WHERE account_id=$1 "
        "   AND strategy=$2 AND verdict='ENTER' AND decided_at >= $3 "
        "   AND decided_at < $4", account_id, STRATEGY, t0, t1)
    fills = await conn.fetchrow(
        "SELECT count(*) AS n, coalesce(sum(f.gross_usd), 0) AS gross, "
        "       coalesce(sum(f.fee_usd), 0) AS fees, "
        "       coalesce(sum(f.qty), 0) AS qty, "
        "       count(DISTINCT f.us_market_slug) AS markets "
        "  FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id "
        " WHERE f.account_id=$1 AND o.strategy=$2 AND f.direction='BUY' "
        "   AND f.filled_at >= $3 AND f.filled_at < $4", account_id,
        STRATEGY, t0, t1)
    orders = await conn.fetch(
        "SELECT state, count(*) AS n FROM paper_orders WHERE account_id=$1 "
        "   AND strategy=$2 AND created_at >= $3 AND created_at < $4 "
        " GROUP BY 1", account_id, STRATEGY, t0, t1)
    handoffs = await conn.fetchval(
        "SELECT count(*) FROM paper_handoffs WHERE account_id=$1 "
        "   AND strategy=$2", account_id, STRATEGY)
    groups = {r["group_id"] for r in await conn.fetch(
        "SELECT DISTINCT group_id FROM paper_orders WHERE account_id=$1 "
        "   AND strategy=$2", account_id, STRATEGY)}
    pos = [p for p in await L.positions(conn, account_id,
                                        include_closed=True)
           if p["group_id"] in groups]
    rep = {
        "strategy": STRATEGY, "version": VERSION, "disclosure": DISCLOSURE,
        "decisions": [{"strategy": STRATEGY, "verdict": r["verdict"],
                       "reason": r["reason"], "decisions": int(r["n"]),
                       "markets": int(r["markets"])} for r in dec],
        "entries_at_decision_time": {
            "strategy": STRATEGY, "entries": int(q["n"] or 0),
            "expected_net_usd": q["ev"], "mean_best_level_edge_pp": q["edge"],
            "basis": "what was known at each decision; not scored on "
                     "outcomes"},
        "orders_by_state": {r["state"]: int(r["n"]) for r in orders},
        "fills": {"strategy": STRATEGY, "fills": int(fills["n"]),
                  "acquisition_usd": round(float(fills["gross"])
                                           + float(fills["fees"]), 6),
                  "fees_usd": float(fills["fees"]),
                  "qty": float(fills["qty"]),
                  "distinct_markets": int(fills["markets"])},
        "handoffs_to_xavier_total": int(handoffs or 0),
        "positions": {"strategy": STRATEGY, "count": len(pos),
                      "open": sum(1 for p in pos if p["open_qty"] > 1e-9),
                      "realized_pnl_usd": round(sum(
                          p["realized_pnl_usd"] for p in pos), 6),
                      "open_cost_basis_usd": round(sum(
                          p["cost_basis_usd"] for p in pos), 6)},
        "included_in_account_totals": True,
        "one_ledger": "paper_ledger (the benchmark shares the account's "
                      "single cash ledger; no separate funding)"}
    if pol["kind"] == "COMPLETED_GAME":
        rep.update(await _completed_game_results(
            conn, account_id=account_id, pos=pos, groups=groups))
    return rep


async def audit_fills(conn, actx: dict, finding, pol=None) -> list:
    """AUDREY'S CHECK OF EVERY BENCHMARK ENTRY THAT FILLED: each simulated
    fill has its one ledger FILL entry, and the group has been handed to
    Xavier. One INFO finding per group (WARNING when either is missing)."""
    pol = _pol(pol)
    STRATEGY, DISCLOSURE = pol["strategy"], pol["disclosure"]  # noqa: N806
    out = []
    rows = await conn.fetch(
        "SELECT o.group_id, o.order_id, o.decision_id, o.filled_qty, "
        "       (SELECT count(*) FROM paper_fills f "
        "         WHERE f.order_id = o.order_id) AS fills, "
        "       (SELECT count(*) FROM paper_ledger l JOIN paper_fills f "
        "          ON f.fill_id = l.fill_id WHERE f.order_id = o.order_id "
        "         AND l.kind = 'FILL') AS ledger_fills, "
        "       (SELECT coalesce(-sum(l.cash_delta_usd), 0) FROM paper_ledger l"
        "         WHERE l.order_id = o.order_id AND l.kind = 'FILL') AS debit,"
        "       EXISTS (SELECT 1 FROM paper_handoffs h WHERE "
        "         h.group_id = o.group_id AND h.strategy = o.strategy) "
        "         AS handed "
        "  FROM paper_orders o WHERE o.account_id=$1 AND o.strategy=$2 "
        "   AND o.role='ENTRY' AND o.filled_qty > 0", actx["account_id"],
        STRATEGY)
    for r in rows:
        ok = int(r["fills"]) == int(r["ledger_fills"]) and bool(r["handed"])
        out.append(await finding(
            conn, actx, kind=pol["audit_kind"],
            subject=r["group_id"], severity="INFO" if ok else "WARNING",
            detail={"strategy": STRATEGY, "disclosure": DISCLOSURE,
                    "order_id": r["order_id"],
                    "decision_id": r["decision_id"],
                    "filled_qty": float(r["filled_qty"]),
                    "simulated_fills": int(r["fills"]),
                    "ledger_fill_entries": int(r["ledger_fills"]),
                    "ledger_fill_debits_usd": float(r["debit"]),
                    "handed_to_xavier": bool(r["handed"]),
                    "passed": ok},
            scope="%s:%s" % (r["filled_qty"], r["handed"])))
    return out


async def _completed_game_results(conn, *, account_id: str, pos: list,
                                  groups: set) -> dict:
    """WHAT ACTUALLY HAPPENED TO THE COMPLETED-GAME POLICY'S POSITIONS: the
    simulated P&L (realised, and marked where open), the assumptions each
    entry was made under, and every exceptional settlement with its effect
    against the ordinary-completion assumption. Nothing here promotes or
    qualifies the strategy for real money."""
    ents = await conn.fetch(
        "SELECT d.decision_id, d.us_market_slug, d.p_pinnacle, "
        "       d.economics->>'label' AS label, "
        "       (d.economics->'acquisition'->>'expected_net_profit_usd')"
        "         ::float8 AS conditional_ev, "
        "       d.economics->'acquisition'->'exceptional_settlement' AS exc, "
        "       o.group_id "
        "  FROM paper_decisions d JOIN paper_orders o "
        "    ON o.decision_id = d.decision_id AND o.role = 'ENTRY' "
        " WHERE d.account_id=$1 AND d.strategy=$2 AND d.verdict='ENTER'",
        account_id, CG_STRATEGY)
    sets = await conn.fetch(
        "SELECT s.group_id, s.us_market_slug, s.outcome, s.qty, "
        "       s.payout_per_contract, s.payout_usd, s.evidence_source, "
        "       s.settled_at FROM paper_settlements s "
        " WHERE s.account_id=$1 AND s.group_id = ANY($2::text[]) "
        " ORDER BY s.settled_at", account_id, list(groups))
    by_group = {e["group_id"]: e for e in ents}
    exc = []
    for x in sets:
        if x["outcome"] in ("WON", "LOST"):
            continue
        p = next((q for q in pos if q["group_id"] == x["group_id"]), None)
        cost = float((p or {}).get("acquisition_cost_usd") or 0.0)
        exc.append({"group_id": x["group_id"],
                    "market": x["us_market_slug"], "outcome": x["outcome"],
                    "payout_per_contract": float(x["payout_per_contract"]),
                    "payout_usd": float(x["payout_usd"]),
                    "evidence_source": x["evidence_source"],
                    "acquisition_cost_usd": cost,
                    "result_usd": round(float(x["payout_usd"]) - cost, 6),
                    "conditional_ev_at_entry_usd": (by_group.get(
                        x["group_id"]) or {}).get("conditional_ev"),
                    "effect": ("an exceptional settlement: the position was "
                               "paid the venue's own published amount, which "
                               "the ordinary-completion EV did not price")})
    realized = round(sum(q["realized_pnl_usd"] for q in pos), 6)
    unreal = round(sum(float(q.get("unrealized_pnl_usd") or 0.0)
                       for q in pos if q["open_qty"] > 1e-9), 6)
    return {
        "actual_simulated_pnl": {
            "realized_usd": realized, "unrealized_marked_usd": unreal,
            "pending_positions": sum(1 for q in pos
                                     if q["open_qty"] > 1e-9),
            "basis": "the one paper ledger; fills and settlements as booked"},
        "entry_assumptions": {
            "entries": len(ents), "economics_label": ECONOMICS_LABEL,
            "conditional_ev_sum_usd": round(sum(
                float(e["conditional_ev"] or 0.0) for e in ents), 6),
            "conditional_on": "ORDINARY_COMPLETION",
            "exceptional_probabilities": "UNMEASURED"},
        "exceptional_outcomes": exc,
        "exceptional_effect_usd": round(sum(e["result_usd"] for e in exc), 6),
        "qualification": ("NONE: this experiment does not qualify or promote "
                          "any strategy for real-money trading")}


def describe() -> dict:
    return {"strategy": STRATEGY, "version": VERSION, "env_flag": ENV_FLAG,
            "control_key": CONTROL_KEY, "min_edge_pp": MIN_EDGE_PP,
            "book_max_age_s": BOOK_MAX_AGE_S, "disclosure": DISCLOSURE,
            "book_currency": BOOK_CURRENCY,
            "completed_game": {
                "strategy": CG_STRATEGY, "version": CG_VERSION,
                "shipped_min_edge_pp": CG_MIN_EDGE_PP_V2,
                "shipped_min_edge_probability": CG_MIN_EDGE_PP_V2 / 100.0,
                "bounds_pp": list(CG_PARAMETER_BOUNDS["min_gross_edge_pp"]),
                "entry_rule": ("p_pinnacle - simulated acquisition price >= "
                               "the active threshold at every level used, "
                               "the fee per contract below the edge at "
                               "every level bought, and conditional "
                               "expected profit after fees > 0"),
                "previous_versions": {CG_VERSION_V1: {"min_edge_pp": 5.0}}}}
