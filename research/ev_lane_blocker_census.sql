-- WHY THE AUTONOMOUS EV LANE HAS OPENED NOTHING, asked of production.
--
-- The question is not "is the loop running" -- it is, LIVE, 556 markets a cycle.
-- The question is WHICH STEP ENDS EVERY CANDIDATE, and whether that step is an
-- engineering gap inside our supported scope (acquisition, mapping, an
-- unimplemented integration) or an established economic or payout conflict. The
-- first is work for me; the second is a valid NO_TRADE.
--
-- READ-ONLY. Every statement is a SELECT.

\echo == 1 . DOES THE STRATEGY OWN ANY POSITION AT ALL, EVER ==
SELECT provenance,
       count(*)                             AS positions,
       min(decision_ts)                     AS first_seen,
       max(decision_ts)                     AS last_seen
  FROM rn1x_positions
 GROUP BY provenance
 ORDER BY positions DESC;

\echo
\echo == 2 . EVERY VALUATION THE LANE HAS EVER WRITTEN, BY OUTCOME ==
SELECT date_trunc('day', decided_at)        AS day,
       decision,
       admissible,
       eligibility,
       count(*)                             AS rows
  FROM external_valuations
 GROUP BY 1, 2, 3, 4
 ORDER BY 1 DESC, 5 DESC
 LIMIT 40;

\echo
\echo == 3 . THE INELIGIBLE REASON, WHICH IS THE ACTUAL BLOCKER PER ROW ==
SELECT coalesce(ineligible_reason, '(none)') AS ineligible_reason,
       count(*)                              AS rows,
       max(decided_at)                       AS most_recent
  FROM external_valuations
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 30;

\echo
\echo == 4 . AND THE REFUSAL ARRAY, UNNESTED, WHICH IS WHERE THE ENGINE STOPPED ==
SELECT r                                    AS refusal,
       count(*)                             AS rows,
       max(v.decided_at)                     AS most_recent
  FROM external_valuations v
  LEFT JOIN LATERAL unnest(
        CASE WHEN v.refusals IS NULL OR cardinality(v.refusals) = 0
             THEN ARRAY['(no refusal recorded)']::text[]
             ELSE v.refusals END) AS r ON TRUE
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 40;

\echo
\echo == 5 . THE MOST RECENT TWENTY VALUATIONS IN FULL, THE DECISION EVIDENCE ==
SELECT decided_at, sport_family, us_market_slug, mapped_outcome,
       probability::numeric(6,4)                   AS p,
       executable_price::numeric(6,4)              AS ask,
       cost_per_contract::numeric(6,4)             AS cost,
       estimated_edge_per_contract::numeric(8,5)   AS edge,
       decision, admissible, eligibility,
       coalesce(ineligible_reason, '-')            AS why_not,
       array_to_string(refusals, '|')              AS refusals
  FROM external_valuations
 ORDER BY decided_at DESC
 LIMIT 20;

\echo
\echo == 6 . THE SETTLEMENT COMPARISON, WHICH IS A PAYOUT CONFLICT NOT A BUG ==
SELECT coalesce(settlement_comparison->>'compatibility', '(absent)') AS compatibility,
       count(*)                                                     AS rows,
       max(decided_at)                                              AS most_recent
  FROM external_valuations
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 20;

\echo
\echo == 7 . THE LAST CYCLE THE WRITER RECORDED, WITH ITS FUNNEL AND REFUSALS ==
SELECT key,
       to_timestamp((value->>'at')::float8)          AS reported_at,
       value->>'state'                               AS state,
       value->>'markets_considered'                  AS markets_considered,
       value->>'evaluated'                           AS evaluated,
       value->>'written'                             AS written,
       jsonb_pretty(coalesce(value->'refusals', '{}'::jsonb))      AS refusals,
       jsonb_pretty(coalesce(value->'funnel_by_provider_sport',
                             '{}'::jsonb))                          AS funnel
  FROM ingestion_state
 WHERE key IN ('ext_pinnacle_last_cycle', 'ext_pinnacle_last_cycle_standby')
 ORDER BY key;

\echo
\echo == 8 . EVERY MAPPED CANDIDATE OF THAT CYCLE AGAINST ITS FIRST REFUSAL ==
SELECT jsonb_pretty(coalesce(value->'mapped_candidate_ledger', '[]'::jsonb))
         AS mapped_candidate_ledger
  FROM ingestion_state
 WHERE key = 'ext_pinnacle_last_cycle';

\echo
\echo == 9 . HOW MUCH VENUE CATALOGUE WE HOLD TO MAP AGAINST ==
SELECT count(*)                                        AS market_rows,
       count(DISTINCT sport)                           AS sports,
       count(*) FILTER (WHERE slug IS NOT NULL)        AS with_slug,
       max(updated_at)                                 AS most_recent_update
  FROM markets;

\echo
\echo == 10 . AND THE CONTROL ROW THAT GATES EVERY CYCLE ==
SELECT key, value
  FROM ingestion_state
 WHERE key LIKE '%ext_pinnacle%' OR key LIKE '%external_valuation%'
 ORDER BY key;
