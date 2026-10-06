"""One conservative capital-readiness verdict.

Hard operational/economic gates dominate scores. A red hard gate always yields
recommended capital $0. Passing software gates is necessary but not sufficient:
forward economic evidence and a positive scale-twin lower bound are required.
"""
from __future__ import annotations

from . import common as C

VERSION = "CAPITAL_READINESS_SCORE_V1"
HARD_GATES = (
    "ci_exact_sha_green",
    "small_live_shadow",
    "freshness_gte_95",
    "mirror_positions_readable",
    "mirror_exit_unsuppressed",
    "software_reds_zero",
    "management_epoch_reconciled",
    "xavier_complete",
    "production_canary_clean",
    "profitability_bind_active",
    "forward_economics_positive",
)


def score(*, gates: dict, agent_championship=None, scale_twin=None,
          forecast=None, calibration=None, execution=None):
    missing = [g for g in HARD_GATES if gates.get(g) is not True]
    hard_green = not missing

    subs = []
    def add(v):
        if v is not None:
            subs.append(C.clamp(v))

    ach = agent_championship or {}
    agents = ach.get("agents") or {}
    if agents:
        add(sum(1 for a in agents.values() if a.get("economically_positive")) / len(agents))
    add((calibration or {}).get("score"))
    add((execution or {}).get("score"))
    add((forecast or {}).get("validation_score"))
    soft = sum(subs) / len(subs) if subs else 0.0

    twin_cap = int((scale_twin or {}).get("recommended_capital_usd") or 0)
    recommended = twin_cap if hard_green else 0
    status = "GREEN" if hard_green else "RED"
    return C.envelope(
        "OK", None, readiness_status=status,
        readiness_score=C.rnd(soft if hard_green else soft * 0.5, 4),
        hard_gates={g: bool(gates.get(g)) for g in HARD_GATES},
        blocking_gates=missing,
        recommended_capital_usd=recommended,
        measured_scale_twin_capital_usd=twin_cap,
        live_authority_granted=False,
        owner_promotion_required=True,
        version=VERSION,
    )
