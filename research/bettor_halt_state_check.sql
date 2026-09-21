-- THE REMAINING R5 GATES, read from the authoritative store.
--
-- R5 (workers/whale_exits.py -> live_executor.mirror_exit) is enabled
-- by default, its whale allowlist is populated, PMUS credentials are
-- present on the running workers service, and 52 live_orders rows sit
-- at status='filled'. The gates still unaccounted for are the DB pause
-- row and the overspend halt, both read at runtime from ingestion_state.
--
-- Read only. Values of halt/pause keys only -- no credentials.
SELECT 'HALT_STATE' AS section, key, left(value::text, 200) AS v
  FROM ingestion_state
 WHERE key IN ('live_trading_paused', 'mirror_live', 'overspend_halt',
               'live_copy_halt', 'copy_halted', 'kill_switch')
ORDER BY 1, 2
