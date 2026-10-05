"""THE PROFITABILITY OS PAGE: every section from the loaded inputs. Pure.

Each section is built in isolation: a component that raises is that
section's UNAVAILABLE (named), and every other section still renders.
"""
from __future__ import annotations

from . import autonomy, capital, champion, correlation, drift, execution
from . import existing, frontier, governance, release_twin, sentinel
from . import common as C

VERSION = "POS_OS_V1"

#: section name -> (builder, audit, where the component lives)
SECTIONS = (
    ("champion_challenger", champion.build, C.BUILT,
     "sportsassets/pos_os/champion.py (+ poslearn/scoring.py)"),
    ("regime_detection", existing.regime, C.EXISTS,
     "sportsassets/intel/regime.py"),
    ("execution_cost_learning", execution.build_learning, C.BUILT,
     "sportsassets/pos_os/execution.py"),
    ("capital_hour_optimizer", capital.build_optimizer, C.BUILT,
     "sportsassets/pos_os/capital.py"),
    ("capacity_frontier", frontier.build, C.BUILT,
     "sportsassets/pos_os/frontier.py"),
    ("post_trade_attribution", existing.attribution, C.EXISTS,
     "sportsassets/intel/attribution.py"),
    ("counterfactual_twin", existing.counterfactual, C.EXISTS,
     "sportsassets/profitability/economics.py; sportsassets/twin/engine.py"),
    ("data_quality_sentinel", sentinel.build, C.BUILT,
     "sportsassets/pos_os/sentinel.py"),
    ("experiment_governance", governance.build, C.BUILT,
     "sportsassets/pos_os/governance.py (+ poslearn/experiments.py)"),
    ("model_economic_drift", drift.build, C.BUILT,
     "sportsassets/pos_os/drift.py"),
    ("scenario_correlation", correlation.build, C.BUILT,
     "sportsassets/pos_os/correlation.py"),
    ("execution_policy_league", execution.build_league, C.BUILT,
     "sportsassets/pos_os/execution.py"),
    ("expected_profit_clock", capital.build_clock, C.BUILT,
     "sportsassets/pos_os/capital.py"),
    ("autonomy_health", autonomy.build, C.BUILT,
     "sportsassets/pos_os/autonomy.py"),
    ("release_incident_twin", release_twin.build, C.BUILT,
     "sportsassets/pos_os/release_twin.py"),
)
NAMES = tuple(s[0] for s in SECTIONS)


def build(inputs: dict, *, now: float) -> dict:
    out = {}
    for name, fn, audit, where in SECTIONS:
        try:
            sec = fn(inputs, now=now)
        except Exception as exc:                                # noqa: BLE001
            sec = C.section(C.UNAVAILABLE, "%s:%s:%s" % (
                C.R_COMPONENT_RAISED, type(exc).__name__, str(exc)[:120]),
                audit=audit)
        sec.setdefault("implemented_by", where)
        out[name] = sec
    return out


def summary(sections: dict) -> dict:
    by: dict = {}
    for name, s in sections.items():
        by.setdefault(s["status"], []).append(name)
    return {"by_status": by, "sections": len(sections)}
