-- BETTOR Revenue Reliability Stack V1
-- READ-ONLY EXTRACTS AGAINST CURRENT VERIFIED PRODUCTION CONTRACTS.
-- No INSERT / UPDATE / DELETE / DDL.

-- 1) Strategy lifecycle + current profitability state.
SELECT
    l.account_id,
    l.strategy,
    l.state AS lifecycle_state,
    l.rule_id,
    l.evidence AS lifecycle_evidence,
    l.recorded_at AS lifecycle_at
FROM paper_strategy_lifecycle_current_v l
ORDER BY l.account_id, l.strategy;

-- 2) Profitability evaluation stream: calibrated probability, all-in EV,
-- capital-hour economics, shrinkage factors and CASH/ENTER verdict.
SELECT
    account_id,
    strategy,
    stage,
    decision_id,
    order_key,
    us_market_slug,
    holding_side,
    fixture,
    sport,
    market_family,
    regime,
    p_raw,
    p_used,
    market_price,
    calibration_weight,
    qty_in,
    qty_out,
    capacity_factor,
    correlation_factor,
    capital_hour_factor,
    all_in_ev_usd,
    ev_per_contract_usd,
    ev_per_capital_hour,
    expected_hold_hours,
    residual_haircut_per_contract,
    learned_adverse_per_contract,
    fill_probability,
    verdict,
    refusal,
    detail,
    evaluated_at
FROM paper_profitability_evaluations
ORDER BY evaluated_at;

-- 3) Explicit CASH decisions.
SELECT *
FROM paper_cash_decisions
ORDER BY pass_at;

-- 4) Strategy tournament, already shadow/no-authority.
SELECT
    tournament_version,
    strategy,
    sleeve,
    event_key,
    opportunity_id,
    decided_at,
    v1_status,
    v1_score,
    v1_predicted_net_usd,
    v2_status,
    v2_score,
    v2_predicted_net_lcb_usd,
    authority
FROM opportunity_score_tournament
ORDER BY decided_at;

-- 5) Counterfactual variants and settled outcomes.
SELECT
    v.account_id,
    v.strategy,
    v.eval_id,
    v.us_market_slug,
    v.holding_side,
    v.fixture,
    v.variant,
    v.style,
    v.qty,
    v.vwap,
    v.p_used,
    v.fill_probability,
    v.cost_usd,
    v.fees_usd,
    v.expected_ev_usd,
    v.decided_at,
    o.outcome,
    o.payout_per_contract,
    o.counterfactual_pnl_usd,
    o.settled_at
FROM paper_counterfactual_variants v
LEFT JOIN paper_counterfactual_variant_outcomes o
  ON o.variant_id = v.variant_id
ORDER BY v.decided_at, v.eval_id, v.variant;

-- 6) Derek economics: discovery/entry should be scored against positive net EV.
SELECT
    decision_id,
    fixture,
    us_market_slug,
    side,
    decided_at,
    policy_version,
    pinnacle_p,
    model_p,
    executable_price,
    qty,
    gross_edge_pp,
    expected_gross_profit_usd,
    fees_usd,
    expected_net_profit_usd,
    expected_net_roi,
    verdict,
    refusal,
    features
FROM derek_entry_decisions
ORDER BY decided_at;

-- 7) Karen challenge quality, including blocked and false-block evidence.
SELECT
    challenge_id,
    target_agent,
    target_kind,
    detector,
    severity,
    challenged_at,
    state,
    blocked,
    outcome,
    false_block,
    downstream_impact
FROM karen_challenges
ORDER BY challenged_at;

-- 8) Xavier frozen management counterfactual value-add.
SELECT
    x.thesis_id,
    x.strategy,
    x.group_id,
    x.us_market_slug,
    x.holding_side,
    x.entry_ev_per_contract_usd,
    x.entry_ev_usd,
    x.counterfactuals AS frozen_counterfactuals,
    v.status AS value_add_status,
    v.outcome_basis,
    v.counterfactuals AS realized_counterfactuals,
    v.incremental,
    v.computed_at
FROM xavier_entry_theses x
LEFT JOIN LATERAL (
    SELECT *
    FROM xavier_value_add v0
    WHERE v0.thesis_id = x.thesis_id
    ORDER BY v0.computed_at DESC
    LIMIT 1
) v ON true
ORDER BY x.entered_at;

-- 9) Audrey's governed improvement records / holdout trials.
SELECT
    candidate_id,
    change_class,
    change_kind,
    hypothesis,
    success_metrics,
    harm_metrics,
    state,
    proposed_by,
    evaluated_by,
    evaluation,
    approved_by,
    release_scope,
    created_at
FROM improvement_candidates
ORDER BY created_at;

SELECT
    trial_id,
    candidate_id,
    segment,
    holdout_id,
    evidence_category,
    metrics,
    verdict,
    evaluated_by,
    created_at
FROM improvement_trials
ORDER BY created_at;
