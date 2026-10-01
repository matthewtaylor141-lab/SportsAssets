# REHEARSAL — SYNTHETIC — NOT LIVE EXECUTION

**Paper lifecycle rehearsal (PAPER_LIFECYCLE_REHEARSAL_V1)** — status **PASSED** — 133/133 assertions passed.

Synthetic valuations and books, driven through the production paper paths (`paper_runtime.decide_valuation`, `paper_runtime.paper_pass`) on a LOCAL test database. No venue call, no venue order, not the production paper account.

| | |
|---|---|
| database | `rh_rehearsal` on `127.0.0.1` |
| account | `paper_rehearsal_20261001T142745Z_112450a6` |
| session | `paper_session_fc98cce740_20261001T142745Z` |
| T0 | 2026-10-01T14:27:45.522325+00:00 (1790864865.522325) |
| started / finished | 2026-10-01T14:27:45.418113+00:00 / 2026-10-01T14:27:47.225201+00:00 |

## 1 · Entry (Derek, completed-game policy)

| pos | decision_id | decided_at | valuation | edge pp (threshold) | order_id | qty | limit |
|---|---|---|---|---|---|---|---|
| A | `papercg:3c05fe6c96722d0f75a7affd` | 2026-10-01T14:27:45.522325+00:00 | 1 | 12.0 (5.0) | `paperord:1104f4cbcda9090777e909aa` | 300.0 | 0.5 |
| B | `papercg:caefea4bf0aabe3950abd869` | 2026-10-01T14:27:45.522325+00:00 | 2 | 12.0 (5.0) | `paperord:49bb3ba7b0a283027a4a10a1` | 200.0 | 0.5 |
| C | `papercg:79b31ee42d5ff2298ceb2f38` | 2026-10-01T14:27:45.522325+00:00 | 3 | 12.0 (5.0) | `paperord:e1150ec4a2c59a02be8c631b` | 150.0 | 0.5 |

## 2 · Partial fill (position A)

| fill_id | filled_at | qty | price | fee | gross |
|---|---|---|---|---|---|
| `paperfill:8b034c1961a38d4c387ebc4f` | 2026-10-01T14:27:48.522325+00:00 | 50.0 | 0.49 | 0.870000 | 24.500000 |
| `paperfill:b6b3e48a2d6a671a8b13eb1d` | 2026-10-01T14:27:48.522325+00:00 | 70.0 | 0.5 | 1.220000 | 35.000000 |

Cash debit on the one ledger: **61.590000** = Σ qty·price 59.500000 + fees 2.090000.

## 3 · Handoff to Xavier

| pos | handoff_id | first_fill_at | confirmed_qty | outstanding |
|---|---|---|---|---|
| A | `paperhand:f3d9a279ba073a4ab1d1ed35` | 2026-10-01T14:27:48.522325+00:00 | 120.0 | 0.0 |
| B | `paperhand:fe64c596434bee7d2e85e9c6` | 2026-10-01T14:27:48.522325+00:00 | 200.0 | 0.0 |
| C | `paperhand:e0a54406678ee16b1a86df95` | 2026-10-01T14:27:48.522325+00:00 | 150.0 | 0.0 |

## 4 · Standing protection (one live order per group)

| pos | order_id | qty | limit | state at placement pass |
|---|---|---|---|---|
| A | `paperord:eddd0b9e3141c45e791dfdd9` | 120.0 | 0.55 | RESTING |
| B | `paperord:05b7ed2eb8e16ade00dc66a2` | 200.0 | 0.55 | RESTING |
| C | `paperord:7bc72304bec4834e0857a7a2` | 150.0 | 0.55 | RESTING |

## 5 · Exit (position A)

Review `paperrev:845e2bd40583f9be934c951c` (MARKET_EVENT): **EXIT**, action CANCEL_STANDING_BEFORE_EXIT.

Exit order `paperord:824d41032729720801285568`: SELL 120.0 at limit 0.74, decided 2026-10-01T14:27:59.522325+00:00, FILLED (FILLED).

| fill_id | filled_at | qty | price | fee | SALE ledger seq / delta |
|---|---|---|---|---|---|
| `paperfill:af16f1024e74287956f5e39c` | 2026-10-01T14:28:02.522325+00:00 | 120.0 | 0.74 | 1.600000 | 11 / 87.200000 |

Realized P&L (A): **25.610000** (acquisition 61.590000 incl. fees).

## 6 · Exceptional settlement (position B) and a pending case (C)

B: `paperset:ea0a3aaa6ae0a93bcf63c556` outcome **SETTLED_AT_VENUE_PRICE** at **0.43**/contract × 200.0 = 86.000000 (evidence `external_valuations.settlement_read`); ledger seq 12 delta 86.000000; realized P&L -17.480000. Never an assumed refund.

C: no published price — open qty 150.0, settlement None (pending).

## 7 · Restart recovery

- **7a_restart_mid_lifecycle** — New runtime context after the protection is resting: hooks and passes rerun, nothing duplicated: hook_reruns_are_duplicates=PASS, pass_T+8_after_restart_ran_without_errors=PASS, pass_T+8_after_restart_no_venue_mutation=PASS, session_resumed_not_recreated=PASS, pass_T+9_after_restart_ran_without_errors=PASS, pass_T+9_after_restart_no_venue_mutation=PASS, no_duplicates_after_restart=PASS
- **7b_restart_cancel_pending** — New runtime context while the protection's cancel is pending: hook_reruns_are_duplicates_with_cancel_pending=PASS
- **7c_restart_at_the_end** — New runtime context after the exit and the settlement: hooks and passes rerun: pass_T+26_after_restart_ran_without_errors=PASS, pass_T+26_after_restart_no_venue_mutation=PASS, pass_T+27_after_restart_ran_without_errors=PASS, pass_T+27_after_restart_no_venue_mutation=PASS, no_duplicates_after_the_final_restart=PASS, each_event_audited_exactly_once=PASS

## 8 · Audit

Audrey's event audits:

| kind | subject | severity | passed | found_at |
|---|---|---|---|---|
| PAPER_EVENT_HANDOFF | `paperhand:fe64c596434bee7d2e85e9c6` | INFO | True | 2026-10-01T14:27:48.522325+00:00 |
| PAPER_EVENT_FIRST_FILL | `paperfill:8b034c1961a38d4c387ebc4f` | INFO | True | 2026-10-01T14:27:48.522325+00:00 |
| PAPER_EVENT_HANDOFF | `paperhand:e0a54406678ee16b1a86df95` | INFO | True | 2026-10-01T14:27:48.522325+00:00 |
| PAPER_EVENT_FIRST_FILL | `paperfill:f7a396cc6769cd754174796f` | INFO | True | 2026-10-01T14:27:48.522325+00:00 |
| PAPER_EVENT_FIRST_FILL | `paperfill:5393d13ea29a896878cfe4bc` | INFO | True | 2026-10-01T14:27:48.522325+00:00 |
| PAPER_EVENT_HANDOFF | `paperhand:f3d9a279ba073a4ab1d1ed35` | INFO | True | 2026-10-01T14:27:48.522325+00:00 |
| PAPER_EVENT_MANAGEMENT_FILL | `paperfill:af16f1024e74287956f5e39c` | INFO | True | 2026-10-01T14:28:02.522325+00:00 |
| PAPER_EVENT_EXCEPTIONAL_OUTCOME | `paperset:ea0a3aaa6ae0a93bcf63c556` | INFO | True | 2026-10-01T14:28:08.522325+00:00 |
| PAPER_EVENT_SETTLEMENT | `paperset:ea0a3aaa6ae0a93bcf63c556` | INFO | True | 2026-10-01T14:28:08.522325+00:00 |
| PAPER_EVENT_SETTLED_AT_VENUE_PRICE | `paperset:ea0a3aaa6ae0a93bcf63c556` | INFO | True | 2026-10-01T14:28:08.522325+00:00 |

Ledger (every entry):

| seq | kind | cash Δ | reserved Δ | cash after | ref |
|---|---|---|---|---|---|
| 2 | INITIAL_FUNDING | 500000.000000 | 0.000000 | 500000.000000 | `` |
| 3 | ORDER_SUBMITTED | 0.000000 | 155.210000 | 500000.000000 | `paperord:1104f4cbcda9090777e909aa` |
| 4 | ORDER_SUBMITTED | 0.000000 | 103.480000 | 500000.000000 | `paperord:49bb3ba7b0a283027a4a10a1` |
| 5 | ORDER_SUBMITTED | 0.000000 | 77.610000 | 500000.000000 | `paperord:e1150ec4a2c59a02be8c631b` |
| 6 | FILL | -25.370000 | -25.868333 | 499974.630000 | `paperfill:8b034c1961a38d4c387ebc4f` |
| 7 | FILL | -36.220000 | -36.215667 | 499938.410000 | `paperfill:b6b3e48a2d6a671a8b13eb1d` |
| 8 | RESERVATION_RELEASED | 0.000000 | -93.126000 | 499938.410000 | `paperord:1104f4cbcda9090777e909aa` |
| 9 | FILL | -103.480000 | -103.480000 | 499834.930000 | `paperfill:f7a396cc6769cd754174796f` |
| 10 | FILL | -77.610000 | -77.610000 | 499757.320000 | `paperfill:5393d13ea29a896878cfe4bc` |
| 11 | SALE | 87.200000 | 0.000000 | 499844.520000 | `paperfill:af16f1024e74287956f5e39c` |
| 12 | SETTLEMENT | 86.000000 | 0.000000 | 499930.520000 | `paperpos:paper_rehearsal_20261001T142745Z_112450a6:papercggrp:caefea4bf0aabe3950abd869:paper-live-syn-rehearsal-20261001t142745z-112450a6-b:LONG:venue-final:paper-live-syn-rehearsal-20261001t142745z-112450a6-b` |

Reconciliation: 500,000 − buys 242.680000 + sales 87.200000 + settlements 86.000000 = **499930.520000**; ledger sum 499930.520000; consistent True.

Linked chains:

- **A** `papercggrp:3c05fe6c96722d0f75a7affd`: complete=True, missing=[], pending=[], defects=[]
    - DEREK_DECISION: PRESENT papercg:3c05fe6c96722d0f75a7affd
    - ENTRY_ORDER: PRESENT paperord:1104f4cbcda9090777e909aa
    - ENTRY_FILLS: PRESENT paperfill:8b034c1961a38d4c387ebc4f, paperfill:b6b3e48a2d6a671a8b13eb1d
    - LEDGER_CASH_DEBIT: PRESENT 6, 7
    - XAVIER_HANDOFF: PRESENT paperhand:f3d9a279ba073a4ab1d1ed35
    - XAVIER_REVIEWS: PRESENT paperrev:e2982e677e2d4ef0d58cda5b, paperrev:845e2bd40583f9be934c951c, paperrev:5a607415fe5f9287c5d8939f
    - XAVIER_ACTIONS: PRESENT paperord:eddd0b9e3141c45e791dfdd9, paperord:824d41032729720801285568
    - EXIT_OR_SETTLEMENT: PRESENT paperfill:af16f1024e74287956f5e39c
    - LEDGER_RESULT: PRESENT 11
    - AUDREY_AUDIT: PRESENT paperfind:1230efeb653a375779358117, paperfind:22921684e6d94955cc577ee2, paperfind:8fba3fa2510d26fe1b98e8f9, paperfind:67b697669e9a4dfce7500430
- **B** `papercggrp:caefea4bf0aabe3950abd869`: complete=True, missing=[], pending=[], defects=[]
    - DEREK_DECISION: PRESENT papercg:caefea4bf0aabe3950abd869
    - ENTRY_ORDER: PRESENT paperord:49bb3ba7b0a283027a4a10a1
    - ENTRY_FILLS: PRESENT paperfill:f7a396cc6769cd754174796f
    - LEDGER_CASH_DEBIT: PRESENT 9
    - XAVIER_HANDOFF: PRESENT paperhand:fe64c596434bee7d2e85e9c6
    - XAVIER_REVIEWS: PRESENT paperrev:9185b83ac765b6a20043e44d
    - XAVIER_ACTIONS: PRESENT paperord:05b7ed2eb8e16ade00dc66a2
    - EXIT_OR_SETTLEMENT: PRESENT paperset:ea0a3aaa6ae0a93bcf63c556
    - LEDGER_RESULT: PRESENT 12
    - AUDREY_AUDIT: PRESENT paperfind:02a48a58c9039f8aa45f2e18, paperfind:5d2173d994eb9da51565ffdf, paperfind:6a8c7fd89a4f580359711b4e, paperfind:019afc1a1998c6a4e982d103
- **C** `papercggrp:79b31ee42d5ff2298ceb2f38`: complete=False, missing=['EXIT_OR_SETTLEMENT', 'LEDGER_RESULT'], pending=['EXIT_OR_SETTLEMENT', 'LEDGER_RESULT'], defects=[]
    - DEREK_DECISION: PRESENT papercg:79b31ee42d5ff2298ceb2f38
    - ENTRY_ORDER: PRESENT paperord:e1150ec4a2c59a02be8c631b
    - ENTRY_FILLS: PRESENT paperfill:5393d13ea29a896878cfe4bc
    - LEDGER_CASH_DEBIT: PRESENT 10
    - XAVIER_HANDOFF: PRESENT paperhand:e0a54406678ee16b1a86df95
    - XAVIER_REVIEWS: PRESENT paperrev:e75499a55396c7762c85eb6d
    - XAVIER_ACTIONS: PRESENT paperord:7bc72304bec4834e0857a7a2
    - EXIT_OR_SETTLEMENT: MISSING 
    - LEDGER_RESULT: MISSING 
    - AUDREY_AUDIT: PRESENT paperfind:27a5e474b6ac52d563bd6848, paperfind:6ca7cf5f0c2621b64805b987, paperfind:f9a5e0a540d330bcc22162e7

## Assertions

- [x] 0_setup · dsn_host_is_local
- [x] 0_setup · account_is_a_rehearsal_account
- [x] 0_setup · schema_has_the_learning_record
- [x] 0_setup · no_foreign_candidate_valuations_in_the_window
- [x] 0_setup · paper_session_enabled_env_and_row
- [x] 0_setup · completed_game_policy_enabled
- [x] 0_setup · strict_benchmark_off
- [x] 0_setup · session_created
- [x] 0_setup · funded_once_with_500000
- [x] 1_entry · A_cg_decision_enter
- [x] 1_entry · A_no_venue_mutation_in_hook
- [x] 1_entry · B_cg_decision_enter
- [x] 1_entry · B_no_venue_mutation_in_hook
- [x] 1_entry · C_cg_decision_enter
- [x] 1_entry · C_no_venue_mutation_in_hook
- [x] 1_entry · A_decision_is_cg_policy
- [x] 1_entry · A_recorded_mlb_venue_wording_graded
- [x] 1_entry · A_edge_at_least_5pp
- [x] 1_entry · A_conditional_economics_label
- [x] 1_entry · A_one_entry_order_naming_the_decision
- [x] 1_entry · B_decision_is_cg_policy
- [x] 1_entry · B_recorded_mlb_venue_wording_graded
- [x] 1_entry · B_edge_at_least_5pp
- [x] 1_entry · B_conditional_economics_label
- [x] 1_entry · B_one_entry_order_naming_the_decision
- [x] 1_entry · C_decision_is_cg_policy
- [x] 1_entry · C_recorded_mlb_venue_wording_graded
- [x] 1_entry · C_edge_at_least_5pp
- [x] 1_entry · C_conditional_economics_label
- [x] 1_entry · C_one_entry_order_naming_the_decision
- [x] 2_partial_fill · pass_T+3_fill_ran_without_errors
- [x] 2_partial_fill · pass_T+3_fill_no_venue_mutation
- [x] 2_partial_fill · A_partial_fills_on_two_levels
- [x] 2_partial_fill · A_filled_less_than_ordered
- [x] 2_partial_fill · A_order_terminal_ioc_remainder_canceled
- [x] 2_partial_fill · A_fills_are_simulator_fills_of_the_cg_strategy
- [x] 2_partial_fill · A_fees_charged_per_fill
- [x] 2_partial_fill · A_cash_debit_equals_qty_x_price_plus_fees
- [x] 2_partial_fill · A_one_fill_ledger_entry_per_fill
- [x] 2_partial_fill · A_unfilled_reservation_released
- [x] 2_partial_fill · one_ledger_cash_is_funding_minus_all_buys
- [x] 2_partial_fill · B_filled_completely
- [x] 2_partial_fill · C_filled_completely
- [x] 3_handoff · A_handoff_owner_xavier
- [x] 3_handoff · A_confirmed_qty_is_the_actual_filled_qty
- [x] 3_handoff · A_handoff_from_the_first_fill
- [x] 3_handoff · B_handoff_owner_xavier
- [x] 3_handoff · B_confirmed_qty_is_the_actual_filled_qty
- [x] 3_handoff · B_handoff_from_the_first_fill
- [x] 3_handoff · C_handoff_owner_xavier
- [x] 3_handoff · C_confirmed_qty_is_the_actual_filled_qty
- [x] 3_handoff · C_handoff_from_the_first_fill
- [x] 3_handoff · A_confirmed_qty_is_not_the_ordered_qty
- [x] 4_standing_protection · A_exactly_one_live_standing_order
- [x] 4_standing_protection · A_standing_qty_is_the_actual_qty
- [x] 4_standing_protection · A_protective_price_recovers_cost_and_fees
- [x] 4_standing_protection · B_exactly_one_live_standing_order
- [x] 4_standing_protection · B_standing_qty_is_the_actual_qty
- [x] 4_standing_protection · B_protective_price_recovers_cost_and_fees
- [x] 4_standing_protection · C_exactly_one_live_standing_order
- [x] 4_standing_protection · C_standing_qty_is_the_actual_qty
- [x] 4_standing_protection · C_protective_price_recovers_cost_and_fees
- [x] 4_standing_protection · A_first_review_holds_and_places_protection
- [x] 4_standing_protection · pass_T+6_steady_ran_without_errors
- [x] 4_standing_protection · pass_T+6_steady_no_venue_mutation
- [x] 4_standing_protection · single_live_hedge_invariant_after_a_steady_pass
- [x] 7a_restart_mid_lifecycle · hook_reruns_are_duplicates
- [x] 7a_restart_mid_lifecycle · pass_T+8_after_restart_ran_without_errors
- [x] 7a_restart_mid_lifecycle · pass_T+8_after_restart_no_venue_mutation
- [x] 7a_restart_mid_lifecycle · session_resumed_not_recreated
- [x] 7a_restart_mid_lifecycle · pass_T+9_after_restart_ran_without_errors
- [x] 7a_restart_mid_lifecycle · pass_T+9_after_restart_no_venue_mutation
- [x] 7a_restart_mid_lifecycle · no_duplicates_after_restart
- [x] 5a_exit_recommendation · pass_T+12_exit_book_ran_without_errors
- [x] 5a_exit_recommendation · pass_T+12_exit_book_no_venue_mutation
- [x] 5a_exit_recommendation · A_exit_recommended_on_the_market_event
- [x] 5a_exit_recommendation · A_exit_value_beats_hold_on_the_same_measure
- [x] 5a_exit_recommendation · A_protection_not_filled_queue_ahead_served_first
- [x] 7b_restart_cancel_pending · hook_reruns_are_duplicates_with_cancel_pending
- [x] 5b_exit_sale · pass_T+14_exit_submit_ran_without_errors
- [x] 5b_exit_sale · pass_T+14_exit_submit_no_venue_mutation
- [x] 5b_exit_sale · A_protection_cancel_confirmed_terminal
- [x] 5b_exit_sale · A_exit_order_for_the_whole_actual_qty
- [x] 5b_exit_sale · no_second_live_order_while_exiting
- [x] 5b_exit_sale · pass_T+17_exit_fill_ran_without_errors
- [x] 5b_exit_sale · pass_T+17_exit_fill_no_venue_mutation
- [x] 5b_exit_sale · A_exit_filled
- [x] 5b_exit_sale · A_sale_ledger_entries_proceeds_minus_fees
- [x] 5b_exit_sale · A_closed_and_realized_pnl_is_proceeds_minus_cost
- [x] 6_exceptional_settlement · pass_T+20_postponed_unpublished_ran_without_errors
- [x] 6_exceptional_settlement · pass_T+20_postponed_unpublished_no_venue_mutation
- [x] 6_exceptional_settlement · nothing_settles_before_the_venue_publishes
- [x] 6_exceptional_settlement · pending_reason_is_no_venue_price
- [x] 6_exceptional_settlement · venue_publication_has_no_outcome_basis
- [x] 6_exceptional_settlement · pass_T+23_venue_price_published_ran_without_errors
- [x] 6_exceptional_settlement · pass_T+23_venue_price_published_no_venue_mutation
- [x] 6_exceptional_settlement · B_settled_at_the_venue_price
- [x] 6_exceptional_settlement · B_settlement_ledger_credit_is_the_venue_payout
- [x] 6_exceptional_settlement · B_never_an_assumed_refund
- [x] 6_exceptional_settlement · B_protection_released_on_the_settled_market
- [x] 6_exceptional_settlement · C_no_published_price_stays_open_and_pending
- [x] 7c_restart_at_the_end · pass_T+26_after_restart_ran_without_errors
- [x] 7c_restart_at_the_end · pass_T+26_after_restart_no_venue_mutation
- [x] 7c_restart_at_the_end · pass_T+27_after_restart_ran_without_errors
- [x] 7c_restart_at_the_end · pass_T+27_after_restart_no_venue_mutation
- [x] 7c_restart_at_the_end · no_duplicates_after_the_final_restart
- [x] 7c_restart_at_the_end · each_event_audited_exactly_once
- [x] 8_audit · A_first_fill_audited_and_passed
- [x] 8_audit · A_handoff_audited_and_passed
- [x] 8_audit · B_first_fill_audited_and_passed
- [x] 8_audit · B_handoff_audited_and_passed
- [x] 8_audit · C_first_fill_audited_and_passed
- [x] 8_audit · C_handoff_audited_and_passed
- [x] 8_audit · A_exit_sale_audited_and_passed
- [x] 8_audit · B_PAPER_EVENT_SETTLEMENT_audited_and_passed
- [x] 8_audit · B_PAPER_EVENT_SETTLED_AT_VENUE_PRICE_audited_and_passed
- [x] 8_audit · B_PAPER_EVENT_EXCEPTIONAL_OUTCOME_audited_and_passed
- [x] 8_audit · venue_price_audit_says_never_an_assumed_refund
- [x] 8_audit · no_ledger_inconsistency_found_by_audrey
- [x] 8_audit · every_event_audit_info_severity
- [x] 8_audit · ledger_reconciles_with_fills_and_settlements
- [x] 8_audit · no_reservation_left_on_terminal_orders
- [x] 8_audit · running_balance_agrees_at_every_entry
- [x] 8_audit · one_funding_entry_only
- [x] 8_audit · A_chain_complete_no_missing_links
- [x] 8_audit · B_chain_complete_no_missing_links
- [x] 8_audit · A_chain_has_every_link_in_order
- [x] 8_audit · B_chain_has_every_link_in_order
- [x] 8_audit · A_chain_closed_by_the_exit_sale
- [x] 8_audit · C_chain_open_position_pending_not_defective
- [x] 8_audit · fill_chain_of_the_first_partial_fill_reads_the_same_group
- [x] 8_audit · production_account_untouched_in_this_database
- [x] 8_audit · session_health_zero_venue_mutations

_REHEARSAL — SYNTHETIC — NOT LIVE EXECUTION_
