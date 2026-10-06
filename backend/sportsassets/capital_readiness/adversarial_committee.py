"""Adversarial pre-capital investment committee (pure, SHADOW).

Its mandate is to find reasons NOT to trust a high-confidence trade. It never
creates an ENTER decision; a challenge can only reduce confidence or block a
research readiness verdict.
"""
from __future__ import annotations
from . import common as C

VERSION = "ADVERSARIAL_INVESTMENT_COMMITTEE_V1"
CRITICAL = {
    "identity_unproven", "settlement_ambiguous", "freshness_failed",
    "book_unreadable", "calibration_out_of_domain", "hidden_correlation",
    "liquidity_cliff", "model_leakage", "feed_divergence",
}


def review(evidence: dict):
    findings=[]
    for k in sorted(CRITICAL):
        v=evidence.get(k)
        if v is True:
            findings.append({"kind": k, "severity": "CRITICAL"})
    warnings=[]
    for k in ("thin_sample", "high_execution_uncertainty", "capacity_near_limit",
              "regime_novel", "forecast_unvalidated"):
        if evidence.get(k) is True:
            warnings.append({"kind": k, "severity": "WARNING"})
    veto=bool(findings)
    return C.envelope("OK", None, veto=veto, findings=findings, warnings=warnings,
                      confidence_multiplier=0.0 if veto else (0.5 if warnings else 1.0),
                      can_only_reduce_confidence=True, version=VERSION)
