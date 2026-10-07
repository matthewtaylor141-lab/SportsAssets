from __future__ import annotations
from dataclasses import dataclass
import math

EPS = 1e-9

def _clip(p: float) -> float:
    return min(1-EPS, max(EPS, float(p)))

def _logit(p: float) -> float:
    p=_clip(p)
    return math.log(p/(1-p))

def _sigmoid(x: float) -> float:
    return 1/(1+math.exp(-x))

@dataclass(frozen=True)
class ForecastEvidence:
    market_prior: float | None
    bettor_raw: float | None
    bettor_calibrated: float | None
    independent_events: int
    calibration_error: float | None
    oos_logloss_delta_vs_market: float | None
    oos_logloss_delta_ci_low: float | None
    residual_alpha: float | None = None
    residual_signal: float | None = None
    residual_improvement_lb: float | None = None
    evidence_complete: bool = True

@dataclass(frozen=True)
class ProbabilityDecision:
    p_used: float | None
    authority: str
    reason: str
    model_disagreement: float | None
    shrinkage_alpha: float

def choose_probability(
    e: ForecastEvidence,
    *,
    minimum_events: int = 100,
    max_calibration_error: float = 0.05,
) -> ProbabilityDecision:
    """Choose the probability BETTOR is allowed to use.

    Market prior is the incumbent. BETTOR may deviate only when independent,
    out-of-sample evidence proves an improvement. A model that is merely
    confident, recent, or historically profitable does not receive authority.
    """
    if not e.evidence_complete:
        return ProbabilityDecision(None, "ABSTAIN", "EVIDENCE_INCOMPLETE", None, 0.0)
    if e.market_prior is None:
        return ProbabilityDecision(None, "ABSTAIN", "MARKET_PRIOR_UNAVAILABLE", None, 0.0)

    pm=_clip(e.market_prior)
    if e.bettor_raw is None:
        return ProbabilityDecision(pm, "MARKET_PRIOR_ONLY", "BETTOR_PROBABILITY_UNAVAILABLE", None, 0.0)

    disagreement=float(e.bettor_raw)-pm
    if e.independent_events < minimum_events:
        return ProbabilityDecision(pm, "MARKET_PRIOR_ONLY", "INSUFFICIENT_INDEPENDENT_EVENTS", disagreement, 0.0)
    if e.calibration_error is None or e.calibration_error > max_calibration_error:
        return ProbabilityDecision(pm, "MARKET_PRIOR_ONLY", "CALIBRATION_NOT_PROVEN", disagreement, 0.0)
    # delta is BETTOR loss - MARKET loss. Negative is improvement.
    if e.oos_logloss_delta_vs_market is None:
        return ProbabilityDecision(pm, "MARKET_PRIOR_ONLY", "OOS_BENCHMARK_UNMEASURED", disagreement, 0.0)
    if e.oos_logloss_delta_vs_market >= 0:
        return ProbabilityDecision(pm, "MARKET_PRIOR_ONLY", "BETTOR_DOES_NOT_BEAT_MARKET_PRIOR_OOS", disagreement, 0.0)
    # A confidence interval crossing zero cannot establish improvement.
    if e.oos_logloss_delta_ci_low is None or e.oos_logloss_delta_ci_low >= 0:
        # Here ci_low is expressed in "improvement" space only when negative
        # delta has been flipped by the caller; require an explicit residual
        # lower bound for deviation authority instead.
        if e.residual_improvement_lb is None or e.residual_improvement_lb <= 0:
            return ProbabilityDecision(pm, "MARKET_PRIOR_ONLY", "OUTPERFORMANCE_LOWER_BOUND_NOT_POSITIVE", disagreement, 0.0)

    if e.residual_improvement_lb is None or e.residual_improvement_lb <= 0:
        return ProbabilityDecision(pm, "MARKET_PRIOR_ONLY", "RESIDUAL_ALPHA_NOT_PROVEN", disagreement, 0.0)

    # The residual form is deliberately anchored on the market logit. The
    # residual model can only perturb the incumbent by a bounded learned alpha.
    alpha=max(0.0, min(1.0, float(e.residual_alpha or 0.0)))
    signal=float(e.residual_signal or 0.0)
    p=_sigmoid(_logit(pm)+alpha*signal)
    # If a separately calibrated BETTOR estimate exists, do not extrapolate
    # farther from market than that estimate without independent evidence.
    if e.bettor_calibrated is not None:
        pc=_clip(e.bettor_calibrated)
        if abs(pc-pm) < abs(p-pm):
            p=pc
    return ProbabilityDecision(p, "BETTOR_RESIDUAL_ALLOWED", "POSITIVE_OOS_RESIDUAL_EVIDENCE", disagreement, alpha)
