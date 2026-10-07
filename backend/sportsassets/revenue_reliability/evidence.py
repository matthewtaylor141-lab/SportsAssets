"""Revenue Reliability V1 -- READ-ONLY evidence binding to BETTOR's records.

Every query is a bounded SELECT over a table BETTOR already writes; nothing
here inserts, updates, deletes or creates. There is no second book: realized
economics come from the frozen Xavier value-add records (migration 206, the
paper fills + settlement of each closed position), counterfactuals stay
counterfactual (migrations 305 / 311, pnl_class NOT_REALIZED_PNL), lifecycle
comes from paper_strategy_lifecycle_current_v (290), calibration / residual
models from paper_profitability_models (309), bankroll from the paper ledger.

Production schema differences from the package's mapping (documented, not
papered over):
  * derek_entry_decisions exists but is EMPTY; Derek's entries are the
    DEREK_ENTRY_POLICY_V2 paper positions (xavier_entry_theses.strategy).
  * paper_counterfactual_outcomes does not exist; the 305 outcomes table is
    paper_shadow_counterfactual_outcomes (read for the shadow counterfactuals).
  * the existing decision-time score tournament (migration 300) has its own
    reader and endpoint (/api/command/opportunity-score-tournament); its rows are
    all v1/v2 UNAVAILABLE so far, so it is referenced, never re-read here.
  * Archer and the Chief Allocator have no order path. Archer has no table of
    its own execution decisions; Allie's shadow weights are intel_allocations
    (migration 208).

`build(rows, ...)` is pure: the live endpoint and the offline research run
feed it the same query results.
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from datetime import datetime, timezone

from . import core as C

LOOKBACK_DAYS = 90
MIN_EVENTS = 100            # the package's minimum independent events
EVENT_DAY_S = 86400.0
MIN_CORR_DAYS = 5           # fewer days of daily P&L: correlation UNMEASURED

QUERIES = {
    "lifecycle": """
        SELECT strategy, state, from_state, rule_id, why, evidence,
               extract(epoch FROM recorded_at) AS recorded_at
          FROM paper_strategy_lifecycle_current_v
         WHERE account_id = $1 ORDER BY strategy LIMIT 200""",
    "positions": """
        SELECT t.thesis_id, t.strategy, t.group_id, t.us_market_slug, t.holding_side, t.decision_id,
               extract(epoch FROM t.entered_at) AS entered_at, t.entry_qty, t.entry_cost_usd,
               t.entry_fees_usd, t.entry_probability, t.entry_ev_usd,
               extract(epoch FROM t.event_start_at) AS event_start_at,
               v.status, v.outcome_basis, v.counterfactuals, v.incremental,
               extract(epoch FROM v.computed_at) AS computed_at,
               pm.event_slug, pm.sports_type, pm.team_league
          FROM xavier_entry_theses t
          LEFT JOIN LATERAL (SELECT status, outcome_basis, counterfactuals, incremental, computed_at
                               FROM xavier_value_add v0 WHERE v0.thesis_id = t.thesis_id
                              ORDER BY v0.computed_at DESC LIMIT 1) v ON true
          LEFT JOIN LATERAL (SELECT event_slug, sports_type, team_league FROM us_premap
                              WHERE market_slug = t.us_market_slug LIMIT 1) pm ON true
         WHERE t.entered_at > now() - make_interval(days => $1)
         ORDER BY t.entered_at LIMIT 20000""",
    "models": """
        SELECT DISTINCT ON (kind) kind, version, observations, payload,
               extract(epoch FROM fitted_at) AS fitted_at
          FROM paper_profitability_models WHERE account_id = $1
         ORDER BY kind, fitted_at DESC LIMIT 20""",
    "segments": """
        SELECT coalesce(e.sport, 'UNKNOWN') AS sport, coalesce(e.market_family, 'UNKNOWN') AS family,
               coalesce(e.regime, 'UNKNOWN') AS regime, count(*) AS evaluations,
               count(DISTINCT e.fixture) AS fixtures,
               count(*) FILTER (WHERE e.verdict = 'ENTER') AS enters,
               avg(e.ev_per_contract_usd) AS mean_ev_per_contract,
               extract(epoch FROM max(e.evaluated_at)) AS last_evaluated_at,
               count(r.contract_id) AS in_registry,
               count(*) FILTER (WHERE r.settlement_state = 'SETTLEMENT_PROVEN_COMPATIBLE') AS settlement_proven
          FROM paper_profitability_evaluations e
          LEFT JOIN market_plane_registry r ON r.contract_id = e.us_market_slug
         WHERE e.account_id = $1 AND e.evaluated_at > now() - make_interval(days => $2)
         GROUP BY 1, 2, 3 ORDER BY 1, 2, 3 LIMIT 2000""",
    "karen": """
        SELECT target_agent, detector, category, production_effect, count(*) AS challenges,
               count(*) FILTER (WHERE blocked) AS blocked,
               count(*) FILTER (WHERE false_block) AS false_blocks,
               count(*) FILTER (WHERE false_block IS NOT NULL) AS false_block_assessed,
               count(DISTINCT target_id) AS targets,
               count(*) FILTER (WHERE downstream_impact IS NOT NULL) AS with_impact
          FROM karen_challenges
         WHERE challenged_at > now() - make_interval(days => $1)
         GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 200""",
    "variants": """
        SELECT v.eval_id, v.strategy, v.fixture, v.variant, v.style, v.qty, v.expected_ev_usd,
               o.counterfactual_pnl_usd, o.pnl_class, extract(epoch FROM o.settled_at) AS settled_at
          FROM paper_counterfactual_variants v
          JOIN paper_counterfactual_variant_outcomes o ON o.variant_id = v.variant_id
         WHERE v.decided_at > now() - make_interval(days => $1)
         ORDER BY v.eval_id LIMIT 20000""",
    "allocations": """
        SELECT a.run_id, a.candidate_id, a.candidate_kind, a.group_id, a.shadow_weight, a.shadow_usd,
               extract(epoch FROM a.computed_at) AS computed_at
          FROM intel_allocations a
         WHERE a.computed_at > now() - make_interval(days => $1)
           AND a.group_id IN (SELECT group_id FROM xavier_value_add WHERE status = 'FINAL')
         ORDER BY a.computed_at DESC LIMIT 20000""",
    "reconciliation": """
        WITH f AS (
          SELECT o.group_id, o.us_market_slug, o.holding_side,
                 sum(CASE WHEN o.direction = 'BUY' THEN f.qty ELSE -f.qty END) AS net_qty,
                 sum(CASE WHEN o.direction = 'BUY' THEN -f.qty * f.price ELSE f.qty * f.price END)
                   - sum(f.fee_usd) AS fill_cash
            FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
           WHERE o.group_id IN (SELECT group_id FROM xavier_value_add WHERE status = 'FINAL'
                                  AND computed_at > now() - make_interval(days => $1))
           GROUP BY 1, 2, 3),
        s AS (
          SELECT DISTINCT ON (group_id, us_market_slug, holding_side) group_id, us_market_slug, holding_side,
                 payout_per_contract
            FROM paper_settlements ORDER BY group_id, us_market_slug, holding_side, version DESC)
        SELECT f.group_id, f.us_market_slug, f.holding_side, f.net_qty, f.fill_cash, s.payout_per_contract,
               (SELECT (v.counterfactuals->'ACTUAL_XAVIER'->>'pnl_usd')::float8 FROM xavier_value_add v
                 WHERE v.group_id = f.group_id AND v.status = 'FINAL' ORDER BY v.computed_at DESC LIMIT 1)
                 AS value_add_actual_pnl
          FROM f LEFT JOIN s USING (group_id, us_market_slug, holding_side) LIMIT 20000""",
    "improvements": """
        SELECT candidate_id, change_class, change_kind, state, proposed_by, evaluated_by, approved_by,
               release_scope, extract(epoch FROM created_at) AS created_at
          FROM improvement_candidates ORDER BY created_at DESC LIMIT 200""",
}


def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _j(x):
    if isinstance(x, (dict, list)) or x is None:
        return x
    try:
        return json.loads(x)
    except (TypeError, ValueError):
        return None


def event_of(slug: str | None, event_slug: str | None) -> str | None:
    if event_slug:
        return str(event_slug)
    import re
    m = re.match(r"^[a-z]+-([a-z0-9]+(?:-[a-z0-9]+)*?-\d{4}-\d{2}-\d{2})", str(slug or ""))
    return m.group(1) if m else None


def _day(ts) -> str | None:
    return None if ts is None else datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d")


# ── settled positions (one source: frozen Xavier value-add) ───────────────
def settled_positions(rows) -> list[dict]:
    out = []
    for r in rows:
        if r.get("status") != "FINAL":
            continue
        cf = _j(r.get("counterfactuals")) or {}
        act, hold, ex = (cf.get(k) or {} for k in ("ACTUAL_XAVIER", "HOLD_TO_SETTLEMENT", "IMMEDIATE_EXIT"))
        a = _f(act.get("pnl_usd"))
        if a is None:
            continue
        out.append({
            "thesis_id": r["thesis_id"], "strategy": r.get("strategy"), "group_id": r.get("group_id"),
            "slug": r.get("us_market_slug"), "event": event_of(r.get("us_market_slug"), r.get("event_slug")),
            "sport": (r.get("team_league") or "UNKNOWN").lower(),
            "family": (r.get("sports_type") or "UNKNOWN").upper(),
            "regime": ("UNKNOWN" if r.get("event_start_at") is None or r.get("entered_at") is None else
                       ("PREGAME" if float(r["entered_at"]) < float(r["event_start_at"]) else "IN_PLAY")),
            "entered_at": _f(r.get("entered_at")), "closed_at": _f(r.get("computed_at")),
            "cost_usd": _f(r.get("entry_cost_usd")) or 0.0, "qty": _f(r.get("entry_qty")) or 0.0,
            "expected_usd": _f(r.get("entry_ev_usd")),
            "actual_pnl": a, "actual_turnover": _f(act.get("turnover_usd")),
            "actual_drawdown": _f(act.get("max_drawdown_usd")),
            "hold_pnl": _f(hold.get("pnl_usd")) if hold.get("available", True) else None,
            "exit_pnl": _f(ex.get("pnl_usd")) if ex.get("available", True) else None,
            "outcome_basis": r.get("outcome_basis"),
        })
    return out


# ── agent value-add + certification ───────────────────────────────────────
def _agent(agent_id, *, events, expected, realized, lb_per_event, blocker, extra=None, calibration_error=None,
           reconciliation_error=None, false_block_rate=None, complete=True):
    contract = C.AGENTS[agent_id]
    # an unmeasured lower bound is not positive: it can never certify
    ev = C.AgentEvidence(agent_id, int(events), float(expected or 0.0), float(realized or 0.0),
                         float(lb_per_event) if lb_per_event is not None else 0.0,
                         calibration_error, reconciliation_error, false_block_rate, complete,
                         contract.current_authority)
    cert = C.certify_agent(ev, minimum_events=MIN_EVENTS)
    return {"agent_id": agent_id, "display_name": contract.display_name, "role": contract.role,
            "current_authority": contract.current_authority, "license": cert.license,
            "certification_reason": cert.reason, "independent_events": int(events),
            "expected_contribution_usd": expected, "realized_contribution_usd": realized,
            "lower_bound_incremental_value_per_event_usd": lb_per_event,
            "sample_adequacy": "ADEQUATE" if events >= MIN_EVENTS else "INSUFFICIENT_%d_OF_%d" % (events, MIN_EVENTS),
            "exact_blocker": blocker or (None if cert.license == "LICENSED" else cert.reason),
            "authority": C.certification_does_not_expand_authority(cert, contract.current_authority),
            **(extra or {})}


def _by_event_lb(values_by_event: dict) -> dict:
    vals, cl = [], []
    for e, v in values_by_event.items():
        vals.append(v); cl.append(e)
    return C.cluster_lower_bound(vals, cl)


def agents_block(pos, karen_rows, variant_rows, alloc_rows, recon_rows) -> dict:
    out, value = {}, {}
    # DEREK: entries of his own policy, held to settlement (the entry's own
    # value, independent of management) versus the CASH baseline of $0
    der = [p for p in pos if p["strategy"] == "DEREK_ENTRY_POLICY_V2" and p["hold_pnl"] is not None]
    ev_d = defaultdict(float)
    for p in der:
        ev_d[p["event"]] += p["hold_pnl"]
    lb = _by_event_lb(ev_d)
    realized = sum(p["hold_pnl"] for p in der)
    expected = sum(p["expected_usd"] or 0.0 for p in der)
    value["DEREK"] = C.derek_entry_alpha(realized, 0.0)
    out["DEREK"] = _agent("DEREK", events=len(ev_d), expected=expected, realized=realized,
                          lb_per_event=lb["lower"], blocker=None,
                          extra={"benchmark": "CASH_BASELINE_ZERO (entry held to settlement vs not entering)",
                                 "positions": len(der),
                                 "note": "derek_entry_decisions is empty in production; DEREK_ENTRY_POLICY_V2 "
                                         "paper positions are Derek's entries"})
    # XAVIER: actual management vs BOTH frozen counterfactuals, per event the
    # smaller of the two increments
    xa = [p for p in pos if p["hold_pnl"] is not None and p["exit_pnl"] is not None]
    inc = defaultdict(float)
    vs_h = vs_e = 0.0
    for p in xa:
        inc[p["event"]] += min(p["actual_pnl"] - p["hold_pnl"], p["actual_pnl"] - p["exit_pnl"])
        vs_h += p["actual_pnl"] - p["hold_pnl"]
        vs_e += p["actual_pnl"] - p["exit_pnl"]
    lbx = _by_event_lb(inc)
    xm = C.xavier_management_alpha(sum(p["actual_pnl"] for p in xa), sum(p["hold_pnl"] for p in xa),
                                   sum(p["exit_pnl"] for p in xa))
    value["XAVIER"] = xm
    out["XAVIER"] = _agent("XAVIER", events=len(inc), expected=0.0,
                           realized=xm["conservative_incremental_value_usd"], lb_per_event=lbx["lower"], blocker=None,
                           extra={"benchmark": "FROZEN_MIGRATION_206 HOLD_TO_SETTLEMENT and IMMEDIATE_EXIT",
                                  "positions": len(xa), "vs_hold_to_settlement_usd": vs_h,
                                  "vs_immediate_exit_usd": vs_e,
                                  "positive_against_both": xm["positive_against_both"]})
    # KAREN: saved loss of VALID blocking challenges minus false-block cost.
    # A challenge that blocked nothing has no economic effect either way.
    chal = sum(int(r["challenges"]) for r in karen_rows)
    blocked = sum(int(r["blocked"]) for r in karen_rows)
    fb = sum(int(r["false_blocks"]) for r in karen_rows)
    impact = sum(int(r["with_impact"]) for r in karen_rows)
    value["KAREN"] = C.karen_challenge_value(0.0, 0.0)
    out["KAREN"] = _agent("KAREN", events=blocked, expected=0.0, realized=0.0, lb_per_event=None,
                          blocker=("NO_BLOCKING_CHALLENGES: %d challenges, %d blocked, %d false blocks, %d with "
                                   "recorded downstream impact -- saved loss and false-block cost are both "
                                   "unmeasurable, so no value is claimed" % (chal, blocked, fb, impact))
                          if blocked == 0 else None,
                          false_block_rate=(fb / blocked) if blocked else None,
                          extra={"challenges": chal, "blocked": blocked, "false_blocks": fb,
                                 "by_detector": [{k: (int(v) if isinstance(v, (int, float)) and k != "detector" else v)
                                                  for k, v in r.items()} for r in karen_rows]})
    # CHIEF ALLOCATOR (Allie): shadow-weighted realized return per $ versus
    # equal weight over the SAME candidates of each run (only candidates whose
    # position has a final realized P&L)
    by_group = {p["group_id"]: p for p in pos}
    runs = defaultdict(list)
    for a in alloc_rows:
        p = by_group.get(a.get("group_id"))
        if p and p["actual_turnover"]:
            runs[a["run_id"]].append((float(a["shadow_weight"]), p["actual_pnl"] / p["actual_turnover"]))
    alpha_run = {}
    for rid, xs in runs.items():
        tw = sum(w for w, _ in xs)
        if len(xs) < 2 or tw <= 0:
            continue
        alpha_run[rid] = sum(w * r for w, r in xs) / tw - sum(r for _, r in xs) / len(xs)
    lba = _by_event_lb(alpha_run)
    value["CHIEF_ALLOCATOR"] = C.allie_allocation_alpha(sum(alpha_run.values()), 0.0)
    out["CHIEF_ALLOCATOR"] = _agent(
        "CHIEF_ALLOCATOR", events=len(alpha_run), expected=0.0, realized=sum(alpha_run.values()),
        lb_per_event=lba["lower"],
        blocker=None if alpha_run else "NO_ALLOCATION_RUN_WITH_TWO_OR_MORE_SETTLED_CANDIDATES",
        extra={"benchmark": "EQUAL_WEIGHT over the same run's candidates (return per $ of turnover)",
               "unit": "allocation run", "eligible_positive_strategy_set": "EMPTY_TODAY (see tournament)"})
    # ARCHER: no execution decisions of its own are recorded (SHADOW_ONLY, no
    # order path). The execution evidence library is the 311 variants.
    styles = defaultdict(lambda: [0, 0.0])
    for v in variant_rows:
        if v.get("counterfactual_pnl_usd") is None:
            continue
        s = styles[v["variant"]]
        s[0] += 1
        s[1] += float(v["counterfactual_pnl_usd"])
    value["ARCHER"] = None
    out["ARCHER"] = _agent("ARCHER", events=0, expected=0.0, realized=0.0, lb_per_event=None,
                           blocker="NO_ARCHER_EXECUTION_DECISIONS_RECORDED: Archer is SHADOW_ONLY with no order "
                                   "path; realized-vs-best-feasible execution cannot be attributed to it",
                           extra={"counterfactual_execution_library_NOT_REALIZED": {
                               k: {"settled": n, "counterfactual_pnl_usd": usd} for k, (n, usd) in sorted(styles.items())}})
    # AUDREY: unexplained reconciliation residual = value-add realized P&L vs
    # the same position recomputed from paper fills + settlement
    resid, n_rec, unrec = 0.0, 0, 0
    per_group = defaultdict(lambda: [0.0, None])
    for r in recon_rows:
        g = per_group[r["group_id"]]
        held = _f(r["net_qty"]) or 0.0
        pay = _f(r.get("payout_per_contract"))
        g[0] += (_f(r["fill_cash"]) or 0.0) + (held * pay if (pay is not None and abs(held) > 1e-9) else 0.0)
        g[1] = _f(r.get("value_add_actual_pnl"))
    for gid, (cash, va) in per_group.items():
        if va is None:
            unrec += 1
            continue
        n_rec += 1
        resid += cash - va
    out["AUDREY"] = _agent("AUDREY", events=n_rec, expected=0.0, realized=0.0, lb_per_event=None,
                           blocker=None if n_rec else "NO_RECONCILABLE_POSITIONS",
                           reconciliation_error=round(resid, 6) if n_rec else None,
                           extra={"unexplained_reconciliation_residual_usd": round(resid, 6),
                                  "positions_reconciled": n_rec, "positions_without_value_add": unrec,
                                  "score": C.audrey_reconciliation_score(resid, 0.01) if n_rec else None,
                                  "reconciliation_status": ("RECONCILED" if n_rec and abs(resid) <= 0.01 else
                                                            "UNRECONCILED" if n_rec else "UNMEASURED"),
                                  "license_note": "the package licenses on positive dollar value; reconciliation "
                                                  "quality is reported beside it, never substituted for it",
                                  "basis": "xavier_value_add ACTUAL_XAVIER pnl vs paper_fills + paper_settlements"})
    value["AUDREY"] = out["AUDREY"]["score"]
    for a in ("SCOUT", "ADRIANA"):
        out[a] = _agent(a, events=0, expected=0.0, realized=0.0, lb_per_event=None,
                        blocker="NO_ECONOMIC_CONTRIBUTION_RECORDED_FOR_THIS_ROLE")
        value[a] = None
    return {"agents": out, "value_add": value}


def _ser(x):
    from dataclasses import asdict, is_dataclass
    if is_dataclass(x):
        return asdict(x)
    if isinstance(x, dict):
        return {k: _ser(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_ser(v) for v in x]
    return x


# ── strategies: tournament, reliability, capacity, correlation ────────────
def strategies_block(pos, lifecycle_rows, bankroll) -> dict:
    life = {r["strategy"]: r for r in lifecycle_rows}
    by_s = defaultdict(list)
    for p in pos:
        by_s[p["strategy"]].append(p)
    names = sorted(set(by_s) | set(life))
    # each closed position's realized P&L is attributed to its ENTRY day (the
    # decision day); the value-add record's computed_at is a batch time, not
    # a close time, so it would bunch days together
    days = sorted({_day(p["entered_at"]) for p in pos if p["entered_at"]})
    daily = {s: {d: 0.0 for d in days} for s in names}
    for p in pos:
        d = _day(p["entered_at"])
        if d:
            daily[p["strategy"]][d] += p["actual_pnl"]
    cands, evidence, per = [], [], {}
    for s in names:
        ps = by_s.get(s, [])
        ev = defaultdict(float)
        for p in ps:
            ev[p["event"]] += p["actual_pnl"]
        lb = _by_event_lb(ev)
        series = [daily[s][d] for d in days]
        nd = len(series)
        mean_d = sum(series) / nd if nd else 0.0
        std_d = math.sqrt(sum((x - mean_d) ** 2 for x in series) / (nd - 1)) if nd > 1 else 0.0
        lb_d = mean_d - 1.645 * std_d / math.sqrt(nd) if nd > 1 else -math.inf
        cum = peak = mdd = 0.0
        streak = worst = 0
        for x in series:
            cum += x
            peak = max(peak, cum)
            mdd = min(mdd, cum - peak)
            streak = streak + 1 if x < 0 else 0
            worst = max(worst, streak)
        lev = (_j(life.get(s, {}).get("evidence")) or {}).get("rolling") or {}
        cap_hours = _f(lev.get("capital_hours"))
        realized = sum(p["actual_pnl"] for p in ps)
        cph = (realized / cap_hours) if cap_hours else None
        state = life.get(s, {}).get("state") or "NO_LIFECYCLE_ROW"
        # executable capacity: only a strategy with a positive event-clustered
        # lower bound has any, and then only what it DEMONSTRABLY executed
        # (mean capital deployed per observed day) -- never extrapolated
        deployed = sum(p["cost_usd"] for p in ps)
        capacity = (deployed / nd) if (nd and lb["lower"] is not None and lb["lower"] > 0) else 0.0
        per[s] = {"lifecycle": state, "lifecycle_rule": life.get(s, {}).get("rule_id"),
                  "settled_positions": len(ps), "independent_events": len(ev),
                  "realized_pnl_usd": realized, "expected_usd": sum(p["expected_usd"] or 0.0 for p in ps),
                  "net_per_event": lb, "days": nd, "mean_daily_pnl_usd": mean_d,
                  "lower_bound_daily_pnl_usd": None if not math.isfinite(lb_d) else lb_d,
                  "daily_std_usd": std_d, "daily_variance_usd2": std_d ** 2, "max_drawdown_usd": mdd,
                  "worst_losing_streak_days": worst,
                  "expected_losing_streak": ("UNMEASURED_FEWER_THAN_30_DAYS" if nd < 30 else None),
                  "capital_hours": cap_hours, "usd_per_capital_hour": cph,
                  "executable_capacity_usd": capacity,
                  "capacity_basis": ("demonstrated: mean capital deployed per observed day, and 0 unless the "
                                     "strategy has a positive event-clustered lower bound"),
                  "lifecycle_rolling_ci95": [lev.get("pnl_ci95_low"), lev.get("pnl_ci95_high")]}
        cands.append(C.CandidateMetrics(s, len(ev), lb["mean"] if lb["mean"] is not None else 0.0,
                                        lb["lower"] if lb["lower"] is not None else -math.inf,
                                        drawdown_usd=abs(mdd), capacity_usd=capacity, capital_hour_profit=cph,
                                        evidence_complete=state != "NO_LIFECYCLE_ROW"))
        evidence.append(C.StrategyEvidence(s, len(ev), mean_d, lb_d if math.isfinite(lb_d) else -1.0, std_d,
                                           abs(mdd), cph if cph is not None else -1.0, capacity, state,
                                           evidence_complete=state != "NO_LIFECYCLE_ROW"))
    tour = C.run_tournament(cands, incumbent="CASH", minimum_events=MIN_EVENTS)
    # correlation of daily P&L between strategies (measured; never assumed)
    corr = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            xa, xb = [daily[a][d] for d in days], [daily[b][d] for d in days]
            if len(days) >= MIN_CORR_DAYS:
                ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
                va = sum((x - ma) ** 2 for x in xa)
                vb = sum((x - mb) ** 2 for x in xb)
                c = sum((x - ma) * (y - mb) for x, y in zip(xa, xb)) / math.sqrt(va * vb) if va and vb else None
            else:
                c = None
            corr["%s|%s" % (a, b)] = c
    plan = C.optimize_reliable_portfolio(evidence, bankroll=bankroll, minimum_events=MIN_EVENTS)
    deployed = sum(v for k, v in plan.allocations.items() if k != "CASH")
    util = deployed / bankroll if bankroll else None
    return {"strategies": per, "candidates": names,
            "tournament": _ser(tour) | {"cash_is_incumbent": True,
                                        "rule": "promotion only on an absolute positive event-clustered lower bound "
                                                "with >= %d independent events; never least-negative" % MIN_EVENTS},
            "correlation_daily_pnl": corr, "plan": _ser(plan), "capital_utilization": util,
            "days_observed": days, "daily_pnl_basis": "realized P&L by entry (decision) day",
            "correlation_basis": "Pearson of daily P&L; UNMEASURED (null) below %d days" % MIN_CORR_DAYS}


# ── regime matrix ─────────────────────────────────────────────────────────
def regimes_block(seg_rows, models, pos, now) -> list:
    cal = (models.get("CALIBRATION") or {}).get("payload") or {}
    cells = (_j(cal) or {}).get("cells") or {}
    res_cells = (_j((models.get("RESIDUAL") or {}).get("payload")) or {}).get("cells") or {}
    out = []
    for r in seg_rows:
        key = "%s|%s|%s" % (str(r["sport"]).lower(), str(r["family"]).upper(), str(r["regime"]).upper())
        cell = cells.get(key) or cells.get("%s|%s|%s" % (r["sport"], r["family"], r["regime"])) or {}
        n_cal = int(cell.get("n") or 0)
        cal_err = None
        if cell.get("status") not in (None, "INSUFFICIENT") and cell.get("mean_p") is not None and cell.get("observed") is not None:
            cal_err = abs(float(cell["mean_p"]) - float(cell["observed"]))
        last = _f(r.get("last_evaluated_at"))
        fresh = last is not None and now - last < EVENT_DAY_S
        proven = int(r.get("settlement_proven") or 0)
        evals = int(r.get("evaluations") or 0)
        settle_ok = evals > 0 and proven == evals
        rc = res_cells.get("*|%s|%s" % (r["family"], r["regime"])) or {}
        lb_ev = None
        if r.get("mean_ev_per_contract") is not None and rc.get("residual_per_contract") is not None:
            lb_ev = float(r["mean_ev_per_contract"]) + float(rc["residual_per_contract"])
        e = C.RegimeEvidence(str(r["sport"]), str(r["family"]), str(r["regime"]), int(r.get("fixtures") or 0),
                             cal_err, lb_ev, fresh, settle_ok, evidence_complete=evals > 0)
        d = C.decide_regime(e, minimum_events=MIN_EVENTS)
        out.append({"sport": e.sport, "family": e.family, "regime": e.regime, "status": d.authority,
                    "reason": d.reason, "independent_events": e.independent_events, "evaluations": evals,
                    "calibration_cell_n": n_cal, "calibration_error": cal_err,
                    "calibration_status": cell.get("status") or "NO_CELL",
                    "freshness_ok": fresh, "last_evaluated_at": last,
                    "settlement_proven_contracts": proven, "settlement_ok": settle_ok,
                    "mean_all_in_ev_per_contract": _f(r.get("mean_ev_per_contract")),
                    "lower_bound_ev_per_contract": lb_ev,
                    "lower_bound_basis": "mean all-in EV per contract + the RESIDUAL model's realized residual "
                                         "per contract for this family x regime (unmeasured if either is missing)",
                    "colour": {"ELIGIBLE_FOR_EXISTING_GATED_PATH": "GREEN", "SHADOW_ONLY": "AMBER",
                               "CASH": "RED", "ABSTAIN": "RED"}[d.authority]})
    return out


# ── contribution waterfall (realized, frozen records only) ────────────────
def waterfall_block(pos) -> dict:
    """forecast -> execution -> allocation -> management -> settlement ->
    variance -> realized, per closed position, from the frozen value-add:
      expected  = entry EV recorded at entry
      hold      = held to settlement (entry decision's realized value)
      actual    = what management actually did
    expected -> hold is forecast error + outcome variance (not separable per
    position without a market prior at entry); hold -> actual is management."""
    rows = [p for p in pos if p["expected_usd"] is not None and p["hold_pnl"] is not None]
    exp = sum(p["expected_usd"] for p in rows)
    hold = sum(p["hold_pnl"] for p in rows)
    act = sum(p["actual_pnl"] for p in rows)
    return {"positions": len(rows), "events": len({p["event"] for p in rows}),
            "steps": [{"step": "FORECAST (expected at entry)", "usd": exp},
                      {"step": "EXECUTION", "usd": None,
                       "why": "inside the recorded entry cost; the frozen record carries no separate execution leg"},
                      {"step": "ALLOCATION", "usd": None, "why": "no capital allocation was applied (all strategies "
                                                               "sized by policy; Allie is shadow-weights only)"},
                      {"step": "OUTCOME VARIANCE + FORECAST ERROR (held to settlement minus expected)",
                       "usd": hold - exp},
                      {"step": "MANAGEMENT (actual minus held to settlement)", "usd": act - hold},
                      {"step": "SETTLEMENT", "usd": 0.0, "why": "realized P&L already uses the settlement payout"},
                      {"step": "REALIZED", "usd": act}],
            "identity_check_usd": act - (exp + (hold - exp) + (act - hold))}


# ── Audrey improvement candidates (proposals only; see improvements.py) ───
def weaknesses(agents, strat, regimes) -> list:
    out = []
    x = agents["agents"]["XAVIER"]
    if x["lower_bound_incremental_value_per_event_usd"] is not None and x["lower_bound_incremental_value_per_event_usd"] <= 0:
        out.append(("XAVIER", "MANAGEMENT_POLICY", x))
    d = agents["agents"]["DEREK"]
    if d["lower_bound_incremental_value_per_event_usd"] is not None and d["lower_bound_incremental_value_per_event_usd"] <= 0:
        out.append(("DEREK", "SEGMENT_CALIBRATION", d))
    if agents["agents"]["KAREN"]["independent_events"] == 0:
        out.append(("KAREN", "CHALLENGE_DETECTOR", agents["agents"]["KAREN"]))
    return out


# ── the whole readback ─────────────────────────────────────────────────────
def build(rows: dict, *, bankroll_usd: float | None, bankroll_basis: str, now: float | None = None,
          account_id: str = "paper_acct_main") -> dict:
    now = float(now if now is not None else time.time())
    pos = settled_positions(rows.get("positions") or [])
    models = {r["kind"]: r for r in rows.get("models") or []}
    bankroll = float(bankroll_usd) if bankroll_usd is not None else 0.0
    agents = agents_block(pos, rows.get("karen") or [], rows.get("variants") or [],
                          rows.get("allocations") or [], rows.get("reconciliation") or [])
    strat = strategies_block(pos, rows.get("lifecycle") or [], bankroll)
    regimes = regimes_block(rows.get("segments") or [], models, pos, now)
    wf = waterfall_block(pos)
    plan = strat["plan"]
    readiness = []
    for s, v in strat["strategies"].items():
        readiness.append(C.StrategyReadiness(s, v["lifecycle"], v["independent_events"],
                                             v["lower_bound_daily_pnl_usd"] if v["lower_bound_daily_pnl_usd"] is not None else -1.0,
                                             v["executable_capacity_usd"], v["usd_per_capital_hour"] or 0.0,
                                             plan["reasons"].get(s) if plan["reasons"].get(s) != "ELIGIBLE" else None))
    as_of = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    brief = C.build_daily_brief(bankroll, readiness, plan["allocations"], plan["expected_daily_profit"], as_of=as_of)
    active = [s for s, v in strat["strategies"].items() if v["lifecycle"] not in C.NO_ENTRY_STATES
              and v["lifecycle"] != "NO_LIFECYCLE_ROW"]
    disabled = [s for s in strat["strategies"] if s not in active]
    pos_regimes = [r for r in regimes if r["status"] == "ELIGIBLE_FOR_EXISTING_GATED_PATH"]
    blockers = sorted({"%s:%s:%s" % (s, v["lifecycle"], v["lifecycle_rule"] or "")
                       for s, v in strat["strategies"].items() if v["lifecycle"] in C.NO_ENTRY_STATES} |
                      {"%s:%s" % (r["name"], b) for r in strat["tournament"]["rankings"] for b in r["blockers"]})
    cash_reasons = []
    if not pos_regimes:
        cash_reasons.append("NO_REGIME_IS_ELIGIBLE_FOR_THE_EXISTING_GATED_PATH")
    if strat["tournament"]["selected"] == "CASH":
        cash_reasons.append(strat["tournament"]["reason"])
    for s, why in plan["reasons"].items():
        if why != "ELIGIBLE":
            cash_reasons.append("%s:%s" % (s, why))
    daily = {"title": "BETTOR DAILY REVENUE READINESS", **brief,
             "management_bankroll_usd": bankroll_usd, "bankroll_basis": bankroll_basis,
             "expected_range_usd": [0.0, 0.0] if brief["planned_capital_usd"] == 0 else None,
             "active_strategies": active, "disabled_strategies": disabled,
             "positive_capacity_regimes": [(r["sport"], r["family"], r["regime"]) for r in pos_regimes],
             "abstained_regimes": sum(1 for r in regimes if r["status"] == "ABSTAIN"),
             "regime_status_counts": {k: sum(1 for r in regimes if r["status"] == k) for k in C.REGIME_STATUSES},
             "agent_certification": {k: v["license"] for k, v in agents["agents"].items()},
             "major_profitability_blockers": blockers,
             "capital_hour_economics": {s: v["usd_per_capital_hour"] for s, v in strat["strategies"].items()},
             "correlation_concentration": strat["correlation_daily_pnl"],
             "reasons_unused_capital_is_cash": cash_reasons,
             "what_has_earned_capital_today": [k for k, v in plan["allocations"].items() if k != "CASH"] or "NOTHING -- CASH"}
    return {"version": "BETTOR_REVENUE_RELIABILITY_READBACK_V1", "mode": C.MODE, "package": C.PACKAGE,
            "package_base_sha": C.PACKAGE_BASE_SHA, "as_of": as_of, "account_id": account_id,
            "authority_changed": False, "small_live": "SHADOW",
            "counterfactual_is_never_realized_pnl": True,
            "evidence_counts": {k: len(v or []) for k, v in rows.items()},
            "settled_positions": len(pos), "unique_events": len({p["event"] for p in pos}),
            "agent_scoreboard": agents["agents"], "agent_value_add": _ser(agents["value_add"]),
            "strategy_tournament": strat["tournament"], "strategies": strat["strategies"],
            "reliability_plan": strat["plan"] | {"capital_utilization": strat["capital_utilization"],
                                                 "turnover_target": None,
                                                 "rule": "allocate only eligible + positive + capacity-proven; "
                                                         "everything else CASH"},
            "correlation_daily_pnl": strat["correlation_daily_pnl"],
            "regime_matrix": regimes, "contribution_waterfall": wf,
            "existing_score_tournament": {"endpoint": "/api/command/opportunity-score-tournament",
                                          "note": "read by its own module; not evidence of edge"},
            "improvement_candidates_existing": rows.get("improvements") or [],
            "verified_weaknesses": [{"agent": a, "kind": k} for a, k, _ in weaknesses(agents, strat, regimes)],
            "daily_revenue_readiness": daily}
