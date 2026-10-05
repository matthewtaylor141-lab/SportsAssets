"""THE FROZEN, VERSIONED SCENARIO CATALOG. Pure.

A scenario is a spec: {scenario_key, version, world, params, assumptions}.
Its identity is the sha256 of the canonical spec -- scenario_id =
'twinscn:' + sha[:24] -- and it is stored once in twin_scenarios, where a
trigger refuses any change. Changing a world's parameters means a NEW
version (a new spec, a new id); an existing (key, version) whose spec no
longer hashes to what is stored is refused by store.register_scenarios, so
nothing can be re-tuned after its results are seen.

Every scenario is COUNTERFACTUAL research evidence, never production truth.
"""
from __future__ import annotations

from . import common as C

VERSION = "TWIN_SCENARIOS_V1"

_IMMEDIATE_EXIT_ASSUMPTION = (
    "sell the full entry inventory at the handoff instant: Xavier's thesis "
    "IMMEDIATE_EXIT plan frozen at entry when it covers the whole entry, "
    "else a walk of the latest recorded book at or before that instant "
    "(<= max_book_age_s old) at the position's entry fee rate; unabsorbed "
    "quantity is held to settlement")

CATALOG = (
    {"scenario_key": "RECORDED", "version": 1, "world": "RECORDED",
     "params": {},
     "assumptions": ["replays exactly what was recorded; the world equals "
                     "the baseline -- an integrity check of the replay"]},
    {"scenario_key": "DEREK_THRESHOLD_2PC", "version": 1,
     "world": "DEREK_THRESHOLD", "params": {"min_net_edge_pc": 0.02},
     "assumptions": [
         "enter iff p - planned price - planned fee per contract >= 0.02",
         "a world-only entry fills its planned qty (capped by depth within "
         "limit) at the planned price and fee rate, held to settlement",
         "a recorded ENTER that did not fill does not fill in the world"]},
    {"scenario_key": "DEREK_THRESHOLD_5PC", "version": 1,
     "world": "DEREK_THRESHOLD", "params": {"min_net_edge_pc": 0.05},
     "assumptions": [
         "enter iff p - planned price - planned fee per contract >= 0.05",
         "a world-only entry fills its planned qty (capped by depth within "
         "limit) at the planned price and fee rate, held to settlement"]},
    {"scenario_key": "SIZING_HALF", "version": 1, "world": "SIZING",
     "params": {"multiplier": 0.5},
     "assumptions": ["recorded P&L scaled linearly with quantity"]},
    {"scenario_key": "SIZING_DOUBLE", "version": 1, "world": "SIZING",
     "params": {"multiplier": 2.0},
     "assumptions": ["recorded P&L scaled linearly with quantity",
                     "quantity capped by the decision's depth within limit"]},
    {"scenario_key": "XAVIER_ALWAYS_HOLD", "version": 1,
     "world": "XAVIER_ALWAYS_HOLD", "params": {},
     "assumptions": ["entry inventory held to settlement; no sale, "
                     "reduction or hedge"]},
    {"scenario_key": "XAVIER_IMMEDIATE_EXIT", "version": 1,
     "world": "XAVIER_IMMEDIATE_EXIT",
     "params": {"max_book_age_s": 120.0, "prefer_thesis_plan": True},
     "assumptions": [_IMMEDIATE_EXIT_ASSUMPTION]},
    {"scenario_key": "ALLOCATOR_EQUAL_WEIGHT", "version": 1,
     "world": "ALLOCATOR",
     "params": {"rule": "EQUAL_WEIGHT", "slots": 10,
                "sleeve_usd": C.SLEEVE_NOTIONAL_USD},
     "assumptions": ["each new position receives an equal slot of the "
                     "sleeve (sleeve / 10), capped by the free sleeve at "
                     "its decision instant (positions closed by then free "
                     "their slot)",
                     "P&L = notional x the position's recorded return per "
                     "dollar of entry cost"]},
    {"scenario_key": "ALLOCATOR_INDEPENDENT_SIZING", "version": 1,
     "world": "ALLOCATOR",
     "params": {"rule": "INDEPENDENT_FIXED_FRACTION", "fraction": 0.05,
                "sleeve_usd": C.SLEEVE_NOTIONAL_USD},
     "assumptions": ["each position sized independently at 5% of the sleeve "
                     "with no portfolio constraint"]},
    {"scenario_key": "ALLOCATOR_INTEL_SHADOW", "version": 1,
     "world": "ALLOCATOR",
     "params": {"rule": "INTEL_SHADOW_ALLOCATOR",
                "sleeve_usd": C.SLEEVE_NOTIONAL_USD},
     "assumptions": ["notional = the latest intel_allocations shadow_usd "
                     "for the decision recorded at or before its decision "
                     "instant; none recorded -> unscored"]},
    {"scenario_key": "EDDIE_ALTERNATE_EXECUTION", "version": 1,
     "world": "EDDIE_EXECUTION", "params": {},
     "assumptions": ["requires pos_iface_eddie_execution (217); the "
                     "recorded P&L with Archer's fill VWAP and fee for the "
                     "recorded quantity; no Archer row at decision -> the "
                     "recorded execution"]},
    {"scenario_key": "SCOUT_FEATURE_INCLUDED", "version": 1,
     "world": "SCOUT_FEATURE",
     "params": {"mode": "INCLUDED", "min_net_edge_pc": 0.0},
     "assumptions": ["requires pos_iface_scout_feature_effects (217); "
                     "enter iff p_with - planned price - fee >= 0"]},
    {"scenario_key": "SCOUT_FEATURE_EXCLUDED", "version": 1,
     "world": "SCOUT_FEATURE",
     "params": {"mode": "EXCLUDED", "min_net_edge_pc": 0.0},
     "assumptions": ["requires pos_iface_scout_feature_effects (217); "
                     "enter iff p_without - planned price - fee >= 0"]},
    {"scenario_key": "KAREN_BLOCK_ACCEPTED", "version": 1,
     "world": "KAREN_BLOCK",
     "params": {"mode": "ACCEPTED", "max_book_age_s": 120.0},
     "assumptions": ["a blocking Karen challenge on the decision or group "
                     "recorded before entry prevents it; one recorded while "
                     "the position is open exits it at that instant into "
                     "the latest book (entry fee rate)"]},
    {"scenario_key": "KAREN_BLOCK_IGNORED", "version": 1,
     "world": "KAREN_BLOCK", "params": {"mode": "IGNORED"},
     "assumptions": ["Karen has no authority in production, so the "
                     "recorded world IS the ignored world; kept as the "
                     "explicit pair of KAREN_BLOCK_ACCEPTED"]},
)


def spec_of(entry: dict) -> dict:
    """The canonical spec (what is hashed)."""
    spec = {"scenario_key": entry["scenario_key"],
            "version": int(entry["version"]), "world": entry["world"],
            "assumptions": list(entry["assumptions"]),
            "label": C.COUNTERFACTUAL, "research_only": True}
    spec.update(entry["params"])
    return spec


def frozen(entry: dict) -> dict:
    spec = spec_of(entry)
    digest = C.sha(spec)
    return {"scenario_id": "twinscn:" + digest[:24],
            "scenario_key": entry["scenario_key"],
            "version": int(entry["version"]), "world": entry["world"],
            "spec": spec, "spec_sha256": digest}


def catalog() -> list:
    return [frozen(e) for e in CATALOG]
