-- READ-ONLY. REVENUE RELIABILITY V1: the live readback's own queries (sportsassets/revenue_reliability/evidence.py),
-- parameters inlined, each row as one JSON object {q, r} for the offline receipt.
SELECT json_build_object('q', 'lifecycle', 'r', row_to_json(x))::text FROM (SELECT strategy, state, from_state, rule_id, why, evidence,
               extract(epoch FROM recorded_at) AS recorded_at
          FROM paper_strategy_lifecycle_current_v
         WHERE account_id = 'paper_acct_main' ORDER BY strategy LIMIT 200) x;
SELECT json_build_object('q', 'positions', 'r', row_to_json(x))::text FROM (SELECT t.thesis_id, t.strategy, t.group_id, t.us_market_slug, t.holding_side, t.decision_id,
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
         WHERE t.entered_at > now() - make_interval(days => 90)
         ORDER BY t.entered_at LIMIT 20000) x;
SELECT json_build_object('q', 'models', 'r', row_to_json(x))::text FROM (SELECT DISTINCT ON (kind) kind, version, observations, payload,
               extract(epoch FROM fitted_at) AS fitted_at
          FROM paper_profitability_models WHERE account_id = 'paper_acct_main'
         ORDER BY kind, fitted_at DESC) x;
SELECT json_build_object('q', 'segments', 'r', row_to_json(x))::text FROM (SELECT coalesce(e.sport, 'UNKNOWN') AS sport, coalesce(e.market_family, 'UNKNOWN') AS family,
               coalesce(e.regime, 'UNKNOWN') AS regime, count(*) AS evaluations,
               count(DISTINCT e.fixture) AS fixtures,
               count(*) FILTER (WHERE e.verdict = 'ENTER') AS enters,
               avg(e.ev_per_contract_usd) AS mean_ev_per_contract,
               extract(epoch FROM max(e.evaluated_at)) AS last_evaluated_at,
               count(r.contract_id) AS in_registry,
               count(*) FILTER (WHERE r.settlement_state = 'SETTLEMENT_PROVEN_COMPATIBLE') AS settlement_proven
          FROM paper_profitability_evaluations e
          LEFT JOIN market_plane_registry r ON r.contract_id = e.us_market_slug
         WHERE e.account_id = 'paper_acct_main' AND e.evaluated_at > now() - make_interval(days => 90)
         GROUP BY 1, 2, 3 ORDER BY 1, 2, 3 LIMIT 2000) x;
SELECT json_build_object('q', 'karen', 'r', row_to_json(x))::text FROM (SELECT target_agent, detector, category, production_effect, count(*) AS challenges,
               count(*) FILTER (WHERE blocked) AS blocked,
               count(*) FILTER (WHERE false_block) AS false_blocks,
               count(*) FILTER (WHERE false_block IS NOT NULL) AS false_block_assessed,
               count(DISTINCT target_id) AS targets,
               count(*) FILTER (WHERE downstream_impact IS NOT NULL) AS with_impact
          FROM karen_challenges
         WHERE challenged_at > now() - make_interval(days => 90)
         GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 200) x;
SELECT json_build_object('q', 'variants', 'r', row_to_json(x))::text FROM (SELECT v.eval_id, v.strategy, v.fixture, v.variant, v.style, v.qty, v.expected_ev_usd,
               o.counterfactual_pnl_usd, o.pnl_class, extract(epoch FROM o.settled_at) AS settled_at
          FROM paper_counterfactual_variants v
          JOIN paper_counterfactual_variant_outcomes o ON o.variant_id = v.variant_id
         WHERE v.decided_at > now() - make_interval(days => 90)
         ORDER BY v.eval_id LIMIT 20000) x;
SELECT json_build_object('q', 'allocations', 'r', row_to_json(x))::text FROM (SELECT a.run_id, a.candidate_id, a.candidate_kind, a.group_id, a.shadow_weight, a.shadow_usd,
               extract(epoch FROM a.computed_at) AS computed_at
          FROM intel_allocations a
         WHERE a.computed_at > now() - make_interval(days => 90)
           AND a.group_id IS NOT NULL
         ORDER BY a.computed_at LIMIT 20000) x;
SELECT json_build_object('q', 'reconciliation', 'r', row_to_json(x))::text FROM (WITH f AS (
          SELECT o.group_id, o.us_market_slug, o.holding_side,
                 sum(CASE WHEN o.direction = 'BUY' THEN f.qty ELSE -f.qty END) AS net_qty,
                 sum(CASE WHEN o.direction = 'BUY' THEN -f.qty * f.price ELSE f.qty * f.price END)
                   - sum(f.fee_usd) AS fill_cash
            FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
           WHERE o.group_id IN (SELECT group_id FROM xavier_value_add WHERE status = 'FINAL'
                                  AND computed_at > now() - make_interval(days => 90))
           GROUP BY 1, 2, 3),
        s AS (
          SELECT DISTINCT ON (group_id, us_market_slug, holding_side) group_id, us_market_slug, holding_side,
                 payout_per_contract
            FROM paper_settlements ORDER BY group_id, us_market_slug, holding_side, version DESC)
        SELECT f.group_id, f.us_market_slug, f.holding_side, f.net_qty, f.fill_cash, s.payout_per_contract,
               (SELECT (v.counterfactuals->'ACTUAL_XAVIER'->>'pnl_usd')::float8 FROM xavier_value_add v
                 WHERE v.group_id = f.group_id AND v.status = 'FINAL' ORDER BY v.computed_at DESC LIMIT 1)
                 AS value_add_actual_pnl
          FROM f LEFT JOIN s USING (group_id, us_market_slug, holding_side) LIMIT 20000) x;
SELECT json_build_object('q', 'tournament', 'r', row_to_json(x))::text FROM (SELECT tournament_version, strategy, v1_status, v2_status, authority, count(*) AS entries,
               count(v2_predicted_net_lcb_usd) AS with_v2_lcb, avg(v2_predicted_net_lcb_usd) AS mean_v2_lcb
          FROM opportunity_score_tournament
         WHERE decided_at > now() - make_interval(days => 90)
         GROUP BY 1, 2, 3, 4, 5 ORDER BY 6 DESC LIMIT 200) x;
SELECT json_build_object('q', 'improvements', 'r', row_to_json(x))::text FROM (SELECT candidate_id, change_class, change_kind, state, proposed_by, evaluated_by, approved_by,
               release_scope, extract(epoch FROM created_at) AS created_at
          FROM improvement_candidates ORDER BY created_at DESC LIMIT 200) x;
