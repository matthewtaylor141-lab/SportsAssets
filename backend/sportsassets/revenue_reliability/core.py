"""Revenue Reliability Stack V1 -- decision logic, standard library only.

A faithful port of research/revenue_reliability_stack/bettor_revenue_reliability
(certification, tournament, regime, reliability, counterfactuals, daily
contract). The production image carries no numpy, so the logic is restated
here and held to the vendored package by a parity test
(backend/tests/test_revenue_reliability_parity.py) that runs both on the same
randomized inputs.

Nothing in this module reads or writes a database, places or cancels an
order, changes a lifecycle row, raises a limit or grants authority: every
output is an economic description. Certification is never authority; CASH is
a valid champion; a least-negative candidate is never promoted.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

MODE = "PAPER_SHADOW_READBACK_NO_AUTHORITY"
PACKAGE = "BETTOR_REVENUE_RELIABILITY_STACK_V1"
PACKAGE_BASE_SHA = "659edaf21c2deb1dcc032932f38a7ff1ec8456c2"

LIFECYCLE_STATES = ("ACTIVE_CHAMPION", "ACTIVE_CHALLENGER", "REDUCED_SIZE",
                    "SHADOW_ONLY", "QUARANTINED", "RETIRED")
NO_ENTRY_STATES = frozenset({"SHADOW_ONLY", "QUARANTINED", "RETIRED"})
LICENSES = ("LICENSED", "PROBATION", "SHADOW_ONLY", "SUSPENDED")
REGIME_STATUSES = ("ABSTAIN", "SHADOW_ONLY", "CASH", "ELIGIBLE_FOR_EXISTING_GATED_PATH")


@dataclass(frozen=True)
class AgentContract:
    canonical_id: str
    display_name: str
    role: str
    current_authority: str
    economic_job: str
    can_grant_capital: bool = False


#: mirrors migration 266 and the package's current_contract.AGENTS; pinned by tests
AGENTS = {
    "DEREK": AgentContract("DEREK", "Derek", "Discovery & entry", "ENTRY_REQUEST_THROUGH_GATED_PATH",
                           "Find positive executable opportunities and refuse non-positive entries."),
    "KAREN": AgentContract("KAREN", "Karen", "Adversarial review", "CHALLENGE_ONLY_ZERO_AUTHORITY",
                           "Challenge assumptions and measure false-block / saved-loss value."),
    "CHIEF_ALLOCATOR": AgentContract("CHIEF_ALLOCATOR", "Allie", "Capital allocation", "SHADOW_WEIGHTS_ONLY",
                                     "Allocate only among independently eligible positive-capacity strategies."),
    "ARCHER": AgentContract("ARCHER", "Archer", "Execution & microstructure", "SHADOW_ONLY",
                            "Improve executable economics via MAKE / TAKE / WAIT / REFUSE evidence."),
    "XAVIER": AgentContract("XAVIER", "Xavier", "Portfolio management", "MANAGEMENT_DISPATCH_THROUGH_CLAIM_PATH",
                            "Improve realized management value versus frozen entry counterfactuals."),
    "AUDREY": AgentContract("AUDREY", "Audrey", "Audit & reconciliation", "AUDIT_NO_ORDER_PATH",
                            "Reconcile evidence, P&L and governed improvement records."),
    "SCOUT": AgentContract("SCOUT", "Scout", "Research & signals", "RESEARCH_SHADOW_ONLY",
                           "Improve evidence quality and research coverage without capital authority."),
    "ADRIANA": AgentContract("ADRIANA", "Adriana", "Structural arbitrage", "SHADOW_ONLY",
                             "Find settlement-compatible, all-in positive structural arbitrage."),
}


# ── agent certification (package certification.py) ───────────────────────
@dataclass(frozen=True)
class AgentEvidence:
    agent_id: str
    independent_events: int
    expected_value_usd: float
    realized_value_usd: float
    lower_bound_value_per_event: float
    calibration_error: float | None = None
    reconciliation_error_usd: float | None = None
    false_block_rate: float | None = None
    evidence_complete: bool = True
    current_authority: str = ""


@dataclass(frozen=True)
class Certification:
    agent_id: str
    license: str
    reason: str
    evidence: dict


def certify_agent(ev: AgentEvidence, *, minimum_events: int = 100, max_calibration_error: float = 0.05,
                  max_reconciliation_error_usd: float = 1e-6, max_false_block_rate: float = 0.20) -> Certification:
    reasons = []
    if not ev.evidence_complete:
        reasons.append("EVIDENCE_INCOMPLETE")
    if ev.independent_events < minimum_events:
        reasons.append("INSUFFICIENT_INDEPENDENT_EVENTS")
    if ev.lower_bound_value_per_event <= 0:
        reasons.append("POSITIVE_LOWER_BOUND_NOT_PROVEN")
    if ev.calibration_error is not None and ev.calibration_error > max_calibration_error:
        reasons.append("CALIBRATION_ERROR_TOO_HIGH")
    if ev.reconciliation_error_usd is not None and abs(ev.reconciliation_error_usd) > max_reconciliation_error_usd:
        reasons.append("RECONCILIATION_ERROR_TOO_HIGH")
    if ev.false_block_rate is not None and ev.false_block_rate > max_false_block_rate:
        reasons.append("FALSE_BLOCK_RATE_TOO_HIGH")
    if "RECONCILIATION_ERROR_TOO_HIGH" in reasons or (not ev.evidence_complete and ev.independent_events >= minimum_events):
        lic = "SUSPENDED"
    elif reasons:
        lic = "SHADOW_ONLY" if ev.independent_events < minimum_events else "PROBATION"
    else:
        lic = "LICENSED"
    return Certification(ev.agent_id, lic, ";".join(reasons) if reasons else "POSITIVE_INDEPENDENT_VALUE_PROVEN",
                         asdict(ev))


def certification_does_not_expand_authority(cert: Certification, current_authority: str) -> dict:
    return {"agent_id": cert.agent_id, "license": cert.license, "current_authority": current_authority,
            "authority_changed": False, "capital_authority_granted": False}


# ── champion / challenger with CASH incumbent (package tournament.py) ─────
@dataclass(frozen=True)
class CandidateMetrics:
    name: str
    independent_events: int
    mean_net_per_event: float
    lower_bound_net_per_event: float
    brier: float | None = None
    log_loss: float | None = None
    calibration_error: float | None = None
    drawdown_usd: float = 0.0
    capacity_usd: float = 0.0
    capital_hour_profit: float | None = None
    evidence_complete: bool = True


@dataclass(frozen=True)
class TournamentDecision:
    incumbent: str
    selected: str
    status: str
    reason: str
    rankings: list


def _rank_score(c: CandidateMetrics) -> tuple:
    return (c.lower_bound_net_per_event,
            c.capital_hour_profit if c.capital_hour_profit is not None else -math.inf,
            -c.drawdown_usd,
            -(c.calibration_error if c.calibration_error is not None else math.inf),
            -(c.brier if c.brier is not None else math.inf),
            -(c.log_loss if c.log_loss is not None else math.inf))


def run_tournament(candidates, *, incumbent: str = "CASH", minimum_events: int = 100,
                   require_absolute_positive: bool = True) -> TournamentDecision:
    rankings, eligible = [], []
    for c in candidates:
        blockers = []
        if not c.evidence_complete:
            blockers.append("EVIDENCE_INCOMPLETE")
        if c.independent_events < minimum_events:
            blockers.append("INSUFFICIENT_EVENTS")
        if require_absolute_positive and c.lower_bound_net_per_event <= 0:
            blockers.append("ABSOLUTE_POSITIVE_LOWER_BOUND_NOT_PROVEN")
        rankings.append({"name": c.name, "eligible": not blockers, "blockers": blockers,
                         "lower_bound_net_per_event": c.lower_bound_net_per_event,
                         "mean_net_per_event": c.mean_net_per_event, "independent_events": c.independent_events})
        if not blockers:
            eligible.append(c)
    if not eligible:
        return TournamentDecision(incumbent, "CASH", "NO_PROMOTION",
                                  "NO_CANDIDATE_BEATS_CASH_ON_ABSOLUTE_FORWARD_ECONOMICS", rankings)
    best = max(eligible, key=_rank_score)
    return TournamentDecision(incumbent, best.name,
                              "CHALLENGER_SELECTED" if incumbent == "CASH" else "PROMOTION_CANDIDATE",
                              "BEST_ELIGIBLE_ABSOLUTE_POSITIVE_FORWARD_CANDIDATE", rankings)


# ── regime / abstention authority (package regime.py) ─────────────────────
@dataclass(frozen=True)
class RegimeEvidence:
    sport: str
    family: str
    regime: str
    independent_events: int
    calibration_error: float | None
    lower_bound_net_ev: float | None
    freshness_ok: bool
    settlement_ok: bool
    evidence_complete: bool = True


@dataclass(frozen=True)
class RegimeDecision:
    authority: str
    reason: str
    segment: tuple


def decide_regime(e: RegimeEvidence, *, minimum_events: int = 100, max_calibration_error: float = 0.05) -> RegimeDecision:
    seg = (e.sport, e.family, e.regime)
    if not e.evidence_complete:
        return RegimeDecision("ABSTAIN", "UNMEASURED_OR_INCOMPLETE_EVIDENCE", seg)
    if not e.freshness_ok:
        return RegimeDecision("ABSTAIN", "FRESHNESS_NOT_PROVEN", seg)
    if not e.settlement_ok:
        return RegimeDecision("ABSTAIN", "SETTLEMENT_IDENTITY_NOT_PROVEN", seg)
    if e.independent_events < minimum_events:
        return RegimeDecision("SHADOW_ONLY", "INSUFFICIENT_INDEPENDENT_EVENTS", seg)
    if e.calibration_error is None or e.calibration_error > max_calibration_error:
        return RegimeDecision("SHADOW_ONLY", "CALIBRATION_NOT_PROVEN", seg)
    if e.lower_bound_net_ev is None or e.lower_bound_net_ev <= 0:
        return RegimeDecision("CASH", "POSITIVE_EXECUTABLE_EV_NOT_PROVEN", seg)
    return RegimeDecision("ELIGIBLE_FOR_EXISTING_GATED_PATH", "POSITIVE_REGIME_EVIDENCE", seg)


# ── reliability-aware shadow allocation (package reliability.py) ──────────
@dataclass(frozen=True)
class StrategyEvidence:
    name: str
    independent_events: int
    mean_daily_profit: float
    lower_bound_daily_profit: float
    daily_std: float
    max_drawdown: float
    capital_hour_profit: float
    capacity_usd: float
    current_lifecycle: str
    evidence_complete: bool = True


@dataclass(frozen=True)
class PortfolioPlan:
    allocations: dict
    expected_daily_profit: float
    reliability_score: float
    cash: float
    reasons: dict


def _eligible(s: StrategyEvidence, min_events: int):
    if not s.evidence_complete:
        return False, "EVIDENCE_INCOMPLETE"
    if s.current_lifecycle in NO_ENTRY_STATES:
        return False, "LIFECYCLE_%s" % s.current_lifecycle
    if s.independent_events < min_events:
        return False, "INSUFFICIENT_INDEPENDENT_EVENTS"
    if s.lower_bound_daily_profit <= 0:
        return False, "LOWER_BOUND_DAILY_PROFIT_NOT_POSITIVE"
    if s.capital_hour_profit <= 0:
        return False, "CAPITAL_HOUR_PROFIT_NOT_POSITIVE"
    if s.capacity_usd <= 0:
        return False, "NO_POSITIVE_CAPACITY"
    return True, "ELIGIBLE"


def optimize_reliable_portfolio(strategies, *, bankroll: float, correlation=None, minimum_events: int = 100,
                                max_strategy_fraction: float = 0.25, reliability_penalty: float = 1.0) -> PortfolioPlan:
    reasons, eligible = {}, []
    for s in strategies:
        ok, why = _eligible(s, minimum_events)
        reasons[s.name] = why
        if ok:
            eligible.append(s)
    if not eligible:
        return PortfolioPlan({"CASH": float(bankroll)}, 0.0, 0.0, float(bankroll), reasons)
    raw = []
    for s in eligible:
        denom = max(1e-9, s.daily_std + reliability_penalty * abs(s.max_drawdown) / 100.0)
        raw.append(max(0.0, s.lower_bound_daily_profit) * max(0.0, s.capital_hour_profit) / denom)
    tot = sum(raw)
    if tot <= 0:
        return PortfolioPlan({"CASH": float(bankroll)}, 0.0, 0.0, float(bankroll), reasons)
    weights = [x / tot for x in raw]
    if correlation is not None:
        n = len(eligible)
        if len(correlation) != n or any(len(row) != n for row in correlation):
            raise ValueError("correlation shape must match eligible strategies")
        pen = []
        for i in range(n):
            others = [abs(float(correlation[i][j])) for j in range(n) if i != j]
            avg = sum(others) / len(others) if others else 0.0
            pen.append(max(0.0, 1.0 - avg))
        weights = [w * p for w, p in zip(weights, pen)]
        tw = sum(weights)
        if tw > 0:
            weights = [w / tw for w in weights]
    allocations, remaining = {}, float(bankroll)
    for i, s in sorted(enumerate(eligible), key=lambda z: weights[z[0]], reverse=True):
        target = bankroll * float(weights[i])
        cap = min(bankroll * max_strategy_fraction, s.capacity_usd, remaining)
        amt = max(0.0, min(target, cap))
        if amt > 0:
            allocations[s.name] = amt
            remaining -= amt
    allocations["CASH"] = max(0.0, remaining)
    expected = sum(allocations.get(s.name, 0.0) / max(1.0, s.capacity_usd) * s.mean_daily_profit for s in eligible)
    pstd = sum(allocations.get(s.name, 0.0) / max(1.0, s.capacity_usd) * s.daily_std for s in eligible)
    rel = expected / max(1e-9, pstd) if expected > 0 else 0.0
    return PortfolioPlan(allocations, expected, rel, allocations["CASH"], reasons)


# ── counterfactual value-add (package counterfactuals.py) ─────────────────
@dataclass(frozen=True)
class CounterfactualScore:
    agent_id: str
    baseline: str
    actual_value_usd: float
    baseline_value_usd: float
    incremental_value_usd: float
    status: str
    reason: str


def score_increment(agent_id, baseline, actual_value_usd, baseline_value_usd) -> CounterfactualScore:
    inc = float(actual_value_usd) - float(baseline_value_usd)
    return CounterfactualScore(agent_id, baseline, float(actual_value_usd), float(baseline_value_usd), inc,
                               "POSITIVE_VALUE_ADD" if inc > 0 else "NO_POSITIVE_VALUE_ADD",
                               "ACTUAL_BEAT_PREDECLARED_COUNTERFACTUAL" if inc > 0
                               else "ACTUAL_DID_NOT_BEAT_PREDECLARED_COUNTERFACTUAL")


def xavier_management_alpha(actual_pnl, hold_to_settlement_pnl, immediate_exit_pnl) -> dict:
    vs_hold = score_increment("XAVIER", "HOLD_TO_SETTLEMENT", actual_pnl, hold_to_settlement_pnl)
    vs_exit = score_increment("XAVIER", "IMMEDIATE_EXIT", actual_pnl, immediate_exit_pnl)
    cons = min(vs_hold.incremental_value_usd, vs_exit.incremental_value_usd)
    return {"vs_hold": vs_hold, "vs_immediate_exit": vs_exit, "conservative_incremental_value_usd": cons,
            "positive_against_both": cons > 0}


def archer_execution_alpha(realized, benchmark):
    return score_increment("ARCHER", "BEST_FEASIBLE_EXECUTION_BENCHMARK", realized, benchmark)


def allie_allocation_alpha(realized, equal_weight):
    return score_increment("CHIEF_ALLOCATOR", "EQUAL_WEIGHT_POSITIVE_ELIGIBLE_SET", realized, equal_weight)


def derek_entry_alpha(realized, baseline):
    return score_increment("DEREK", "MARKET_PRIOR_OR_CASH_BASELINE", realized, baseline)


def karen_challenge_value(saved_loss_usd, false_block_cost_usd):
    return score_increment("KAREN", "NO_CHALLENGE", saved_loss_usd, -abs(false_block_cost_usd))


def audrey_reconciliation_score(unexplained_residual_usd, tolerance_usd=1e-6) -> dict:
    return {"agent_id": "AUDREY", "unexplained_residual_usd": float(unexplained_residual_usd),
            "tolerance_usd": float(tolerance_usd),
            "reconciled": abs(float(unexplained_residual_usd)) <= float(tolerance_usd)}


# ── daily revenue readiness (package daily_contract.py) ───────────────────
@dataclass(frozen=True)
class StrategyReadiness:
    strategy: str
    lifecycle: str
    independent_events: int
    lower_bound_daily_profit: float
    positive_capacity_usd: float
    capital_hour_profit: float
    primary_blocker: str | None = None


def build_daily_brief(bankroll_usd, strategies, allocations, expected_daily_profit_usd, *, as_of: str) -> dict:
    cap = sum(max(0.0, s.positive_capacity_usd) for s in strategies
              if s.lower_bound_daily_profit > 0 and s.lifecycle not in NO_ENTRY_STATES)
    planned = sum(v for k, v in allocations.items() if k != "CASH")
    cash = float(allocations.get("CASH", max(0.0, bankroll_usd - planned)))
    status = "READY_WITH_PROVEN_CAPACITY" if planned > 0 and expected_daily_profit_usd > 0 else "CASH"
    return {"as_of": as_of, "bankroll_usd": float(bankroll_usd), "proven_positive_capacity_usd": float(cap),
            "planned_capital_usd": float(planned), "cash_usd": float(cash),
            "expected_daily_profit_usd": float(expected_daily_profit_usd),
            "strategies": [asdict(s) for s in strategies], "overall_status": status,
            "reason": "POSITIVE_CAPACITY_ALLOCATED" if status != "CASH" else "NO_PROVEN_POSITIVE_CAPACITY"}


# ── shared statistics (event-clustered) ───────────────────────────────────
def cluster_lower_bound(values, clusters, z: float = 1.645) -> dict:
    """Mean per row with an event-clustered standard error; the unit is the
    cluster (event / position), never the row."""
    g = {}
    for v, c in zip(values, clusters):
        g.setdefault(c, []).append(float(v))
    n = sum(len(x) for x in g.values())
    k = len(g)
    if n == 0:
        return {"rows": 0, "events": 0, "mean": None, "lower": None, "upper": None, "se": None}
    mean = sum(sum(x) for x in g.values()) / n
    if k < 2:
        return {"rows": n, "events": k, "mean": mean, "lower": None, "upper": None, "se": None}
    ss = sum((sum(x) - mean * len(x)) ** 2 for x in g.values())
    se = math.sqrt(k / (k - 1) * ss) / n
    return {"rows": n, "events": k, "mean": mean, "lower": mean - z * se, "upper": mean + z * se, "se": se}
