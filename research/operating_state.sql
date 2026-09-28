-- READ-ONLY. The authorized lane's actual operating state in production:
-- whether the scheduled loop is armed, whether the kill switch is engaged,
-- and what the last recorded cycle decided.
--
-- No writes. Every statement is a SELECT.

SELECT key,
       left(value::text, 400) AS value
  FROM ingestion_state
 WHERE key IN ('ext_pinnacle_shadow',
               'live_trading_paused',
               'research_shadow_uncalibrated',
               'mirror_loss_stop',
               'bettor_live_observation')
 ORDER BY key;

-- The last scheduled cycle, projected: when it ran, which build wrote it,
-- its state, and whether it carried a funded servicing decision at all.
SELECT to_timestamp((value->>'at')::float8) AS cycle_at,
       value->>'writer'                      AS writer,
       value->>'state'                       AS cycle_state,
       value->>'cycle_label'                 AS cycle_label,
       (value ? 'funded_servicing')          AS carries_a_servicing_decision,
       left((value->'funded_servicing')::text, 600) AS servicing
  FROM ingestion_state
 WHERE key = 'ext_pinnacle_last_cycle';

-- What the funded book actually holds. Zero rows is a legitimate answer
-- and is NOT the same as an unreadable book.
SELECT count(*)                                    AS intents,
       count(*) FILTER (WHERE kind = 'ENTRY')      AS entries,
       count(*) FILTER (WHERE state = 'UNRESOLVED') AS unresolved,
       coalesce(sum(residual_qty), 0)::float8      AS residual_total
  FROM bettor_funded_intents;

-- And how many real valuations the lane has written, so "the loop runs"
-- can be distinguished from "the loop produces nothing".
SELECT count(*)            AS external_valuations,
       max(observed_at)    AS newest_observation,
       count(DISTINCT us_market_slug) AS distinct_markets
  FROM external_valuations;
