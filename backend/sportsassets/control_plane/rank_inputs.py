"""RANK INPUTS FROM THE DECISION-TIME RECORD (control plane, stream C). Pure.

The ranker and the allocator read ONE candidate shape (documented in
ranking.py / allocator.py). This module builds it from the immutable record
the canonical decision already writes -- the canonical decision intent
(migration 225: Derek's verdict, Eddie's executable estimate, the
Opportunity Score, Allie's allocation, the evidence ids and the contract) and
the decision's instrument label (paper_decisions.label: teams, competition,
market type) -- and nothing else. Every field the record does not carry is
left None, and the ranker turns it into an UNAVAILABLE component with its
reason (spread, probability half-width, agent reliability, edge half-life,
settlement-exception rate, expected fill fraction: their producers are in
flight in other streams and plug in here when they land).

IDS (control plane contract, stream E's ids.py). The base this stream built
on has no control_plane/ids.py and canonical_intent.py carries no
opportunity id, so the two contract formulas are implemented here EXACTLY as
the contract states them and pinned by a test:

  opportunity_id = 'opp_' + sha256(fixture|us_market_slug|holding_side|line|
                                   scope)[:24]          (None -> '')
  snapshot_id    = 'snap_' + sha256(opportunity_id|decision_clock|
                                    code_sha)[:24]      (clock as %.3f)

After integration these delegate to ids.py (same definition).
"""
from __future__ import annotations

import hashlib

from . import ranking as RK

num = RK.num


def _s(v) -> str:
    return "" if v is None else str(v)


def opportunity_id(*, fixture, us_market_slug, holding_side, line=None,
                   scope=None) -> str:
    body = "|".join(_s(x) for x in (fixture, us_market_slug, holding_side,
                                     line, scope))
    return "opp_" + hashlib.sha256(body.encode()).hexdigest()[:24]


def snapshot_id(*, opportunity_id: str, decision_clock: float,
                code_sha: str) -> str:
    body = "%s|%.3f|%s" % (opportunity_id, float(decision_clock),
                           _s(code_sha))
    return "snap_" + hashlib.sha256(body.encode()).hexdigest()[:24]


def _epoch(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    return num(v)


def _j(v):
    if isinstance(v, str):
        import json
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v or {}


#: the agent whose decision a canonical intent carries: every canonical
#: intent is qualified by Derek's verdict (its `derek` component)
INTENT_AGENT = "DEREK"


def from_canonical_intent(intent: dict, *, label: dict | None = None,
                          probability_max_age_s=None,
                          code_sha: str) -> dict:
    """ONE RANK / ALLOCATION CANDIDATE from one recorded canonical decision
    intent. `probability_max_age_s` is the frozen session's probability
    freshness rule (read by readers_capital.session_rules_at, never set
    here). Pure."""
    ev = _j(intent.get("evidence"))
    op = _j(intent.get("opportunity_score"))
    ed = _j(intent.get("eddie"))
    al = _j(intent.get("allie"))
    dk = _j(intent.get("derek"))
    ct = _j(intent.get("contract"))
    sb = _j(intent.get("sizing_basis"))
    lab = dict(label or {})
    created = _epoch(intent.get("created_at"))
    recorded = _epoch(intent.get("recorded_at"))
    slug = intent.get("us_market_slug")
    side = intent.get("holding_side")
    fixture = ct.get("fixture")
    oid = opportunity_id(fixture=fixture, us_market_slug=slug,
                         holding_side=side, line=lab.get("line"),
                         scope=lab.get("period"))
    sid = snapshot_id(opportunity_id=oid, decision_clock=created or 0.0,
                      code_sha=code_sha)
    qty = num(intent.get("target_qty"))
    limit = num(intent.get("limit_price"))
    e_ok = ed.get("status") == "MEASURED"
    o_ok = op.get("status") == "MEASURED"
    a_ok = al.get("status") == "MEASURED"
    # E[net executable $ | fill]: the Opportunity Score's (pos_capacity,
    # conditional on fill); else Derek's modelled net of the acquisition
    # (also conditional on the fill) -- the basis says which
    net_cond = num(op.get("expected_net_executable_ev_usd"))
    net_basis = "OPPORTUNITY_SCORE_EXECUTABLE_EV_CONDITIONAL_ON_FILL"
    if net_cond is None:
        net_cond = num(dk.get("expected_net_profit_usd"))
        net_basis = "DEREK_MODELLED_NET_OF_THE_ACQUISITION (conditional)"
    fp = num(ed.get("expected_fill_probability")) if e_ok else None
    fp_src = "EDDIE_ESTIMATE_AT_DECISION"
    if fp is None:
        fp = num(op.get("fill_probability"))
        fp_src = "OPPORTUNITY_SCORE_FILL_PROBABILITY"
    req = num(al.get("expected_capital_required_usd")) if a_ok else None
    if req is None:
        req = num(sb.get("acquisition_cost_usd"))
    unit = None if req is None or not qty else req / qty
    hours = num(al.get("expected_hours_to_release")) if a_ok else None
    p = num(ev.get("probability"))
    pin_at = num(ev.get("pinnacle_observed_at"))
    age = None if pin_at is None or created is None else created - pin_at
    style = ed.get("execution_style") if e_ok else None
    max_q = num(ed.get("max_executable_qty")) if e_ok else None
    cap_by = ({style or "UNSTATED": max_q * limit}
              if max_q is not None and limit else {})
    verdict = dk.get("verdict")
    teams = [t for t in (lab.get("home_team"), lab.get("away_team")) if t]
    lag_n = num((al.get("evidence") or {}).get("settlement_lag_samples"))
    allie_inputs = None
    if a_ok and hours is not None and created is not None:
        # her inputs as recorded at the decision: the hours to release are
        # carried whole (start - decision + lag), so the start is stated as
        # decision + hours with a zero residual lag -- the same sum she used
        allie_inputs = {
            "eddie_ev_usd": num(ed.get("expected_executable_ev_usd"))
            if e_ok else None,
            "modelled_net_usd": num(dk.get("expected_net_profit_usd")),
            "event_start_at": created + hours * 3600.0,
            "decided_at": created, "median_lag_s": 0.0,
            "lag_n": int(lag_n or 0),
            "displayed_depth_qty": num(sb.get("depth_within_limit")),
            "basis": "allie's recorded hours to capital release"}
    return {
        "opportunity_id": oid, "snapshot_id": sid,
        "decision_id": intent.get("decision_id"),
        "intent_id": intent.get("intent_id"),
        "strategy": intent.get("strategy"),
        "strategy_class": intent.get("strategy"),
        "agent": INTENT_AGENT,
        "decided_at": created,
        "inputs_recorded_at": max(x for x in (created, recorded)
                                  if x is not None)
        if (created is not None or recorded is not None) else None,
        "gates": {"cleared": verdict == "ENTER",
                  "failures": [] if verdict == "ENTER"
                  else ["DEREK_VERDICT_%s" % verdict]},
        "raw_ev_usd": None,
        "net_ev_conditional_usd": net_cond, "net_ev_basis": net_basis,
        "fill_probability": fp,
        "fill_probability_source": fp_src if fp is not None else None,
        "expected_fill_fraction": None,
        "expected_execution_cost_usd": None,
        "execution_cost_sd_usd": None,
        "execution_cost_sd_per_contract": None,
        "probability": p, "probability_half_width": None,
        "probability_age_s": age,
        "probability_max_age_s": num(probability_max_age_s),
        "unit_cost": unit, "limit_price": limit, "requested_usd": req,
        "requested_qty": qty, "size_usd": req,
        "net_edge_per_contract": (None if net_cond is None or not qty
                                  else net_cond / qty),
        "execution_style": style, "capacity_usd_by_style": cap_by,
        "spread": None,
        "expected_hours_to_release": hours,
        "hours_basis": "allie: event start - decision + median recorded "
                       "settlement lag" if hours is not None else None,
        "agent_reliability": None, "edge_half_life_s": None,
        "expected_time_to_fill_s": None, "settlement_exception_upper": None,
        "holding_side": side,
        "exposure_keys": {
            "EVENT": fixture or ct.get("event_key"), "MARKET": slug,
            "TEAM": teams or None, "SPORT": ct.get("sport_family"),
            "LEAGUE": lab.get("competition"), "STRATEGY":
                intent.get("strategy"), "AGENT": INTENT_AGENT,
            "MARKET_FAMILY": lab.get("market_type"), "VENUE":
                intent.get("venue") or "POLYMARKET"},
        "allie_inputs": allie_inputs,
        "allie_recorded": al if al else None,
        "model_versions": {
            "intent_version": intent.get("intent_version"),
            "strategy_version": intent.get("strategy_version"),
            "derek_policy_version": dk.get("policy_version"),
            "eddie_estimator_version": ed.get("estimator_version"),
            "opportunity_score_version": op.get("version"),
            "allie_version": al.get("version"),
            "parameters_version": ev.get("parameters_version")},
        "source_record": {"table": "canonical_decision_intents",
                          "intent_id": intent.get("intent_id"),
                          "content_sha": intent.get("content_sha")},
    }
