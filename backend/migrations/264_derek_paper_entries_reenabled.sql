-- 264: DEREK'S PAPER ENTRIES RE-ENABLED (PAPER EXECUTION ONLY).
--
-- Owner decision, 2026-10-04 (P0 incident, decision 2): re-enable
-- PAPER_ENTRIES for Derek's two-model strategy (DEREK_ENTRY_POLICY_V2) for
-- PAPER execution only, and correct the stale reason migration 182 wrote.
--
-- THE STALE REASON. Migration 182 switched this row off with the reason
-- "owner: only the PINNACLE_ONLY_PAPER_BENCHMARK may open new paper
-- entries". Migration 184 then switched that benchmark's own entries off
-- (the completed-game policy became the active experiment), so the reason
-- named a strategy that itself could not enter. Measured on production
-- (191b299, 2026-10-04): the off switch refused ~8 qualified INVESTMENT
-- ENTER decisions a day as STRATEGY_ENTRIES_DISABLED.
--
-- WHAT CHANGES: this one paper_control row only. A Derek decision that
-- would ENTER now places its PAPER order through
-- bettor_paper_ledger.submit_order, under every rail that already applies.
--
-- WHAT DOES NOT CHANGE (owner, verbatim list): settlement requirements,
-- freshness requirements, the gross-edge threshold, the net-EV threshold,
-- concentration limits, sizing limits, risk limits, live capital
-- permissions. No threshold, limit, policy parameter, sleeve, live / SMALL
-- LIVE switch or funded control is touched here. SMALL LIVE stays SHADOW.
--
-- APPLIED ONCE, NEVER OVERRIDING A LATER DECISION: the row is flipped only
-- while it still carries migration 182's own state (enabled = FALSE,
-- updated_by = 'migration 182'). If anyone has changed it since, this
-- migration leaves it exactly as it is. An absent row is inserted enabled
-- (absent already meant OFF; the owner decision is ON). Idempotent.
UPDATE paper_control
   SET enabled = TRUE,
       why = 'owner decision 2026-10-04 (P0 incident, decision 2): PAPER '
             'entries re-enabled for DEREK_ENTRY_POLICY_V2, PAPER execution '
             'only. Corrects migration 182''s stale reason (it named the '
             'PINNACLE_ONLY_PAPER_BENCHMARK, whose own entries migration 184 '
             'switched off). Settlement, freshness, gross-edge and net-EV '
             'thresholds, concentration, sizing and risk limits and live '
             'capital permissions are unchanged; SMALL LIVE stays SHADOW.',
       updated_by = 'migration 264',
       updated_at = now()
 WHERE control_key = 'PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2'
   AND enabled = FALSE
   AND updated_by = 'migration 182';

INSERT INTO paper_control (control_key, enabled, why, updated_by)
VALUES ('PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2', TRUE,
        'owner decision 2026-10-04 (P0 incident, decision 2): PAPER entries '
        'enabled for DEREK_ENTRY_POLICY_V2, PAPER execution only (the row was '
        'absent). Thresholds, limits and live capital permissions unchanged; '
        'SMALL LIVE stays SHADOW.',
        'migration 264')
ON CONFLICT (control_key) DO NOTHING;
