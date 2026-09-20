-- COVERAGE UNDER THE BACKOFF, BY CAPTURE VERSION.
--
-- Owner 2026-09-20 §3: "Replace 'the frame is unbiased' with a
-- narrower, supported description until the selection design and
-- resulting coverage are established. Report eligible, scheduled,
-- attempted, skipped by backoff, rate-limited and readable
-- observations separately."
--
-- THE DISTINCTION THAT MATTERS. The SCHEDULED set -- what the rotation
-- picked -- is a function of identifier and clock alone. The ATTEMPTED
-- set is smaller, because a tick that shrinks its budget or abandons on
-- repeated refusals never reaches the tail of its own share. A market
-- that was scheduled and never attempted leaves NO ROW AT ALL, so it is
-- invisible in this table and must be inferred from the gap between
-- what the rule would have picked and what landed.
--
-- That is why "the frame is unbiased" was too strong: it describes the
-- DESIGN, and the design is only realised where a read was attempted.
--
-- Read only. Descriptive. Carries no settlement term.

SELECT 'A_ROWS_BY_VERSION' AS section,
       universe_version || ' | ' || left(rule_sha, 12) AS k,
       count(*)::text AS v
  FROM bettor_state_observations
 GROUP BY 2

UNION ALL

-- ATTEMPTED and its outcomes, split by version. Every row here is an
-- attempt: the capture writes a row whether or not the read succeeded.
SELECT 'B_ATTEMPT_OUTCOME',
       universe_version || ' | ' ||
       CASE WHEN book_readability_status = 'READABLE' THEN 'READABLE'
            WHEN book_readability_status LIKE '%RateLimit%'
                 THEN 'RATE_LIMITED'
            ELSE 'UNREADABLE_OTHER' END,
       count(*)::text
  FROM bettor_state_observations
 GROUP BY 2

UNION ALL

-- COVERAGE. Distinct markets and events actually reached, per version.
SELECT 'C_COVERAGE', universe_version || ' | ' || k, v FROM (
    SELECT universe_version,
           'MARKETS_REACHED' AS k,
           count(DISTINCT market_id)::text AS v
      FROM bettor_state_observations GROUP BY 1
    UNION ALL
    SELECT universe_version, 'EVENTS_REACHED',
           count(DISTINCT event_id)::text
      FROM bettor_state_observations GROUP BY 1
    UNION ALL
    SELECT universe_version, 'MARKETS_READABLE_AT_LEAST_ONCE',
           count(DISTINCT market_id) FILTER (
               WHERE book_readability_status = 'READABLE')::text
      FROM bettor_state_observations GROUP BY 1
    UNION ALL
    SELECT universe_version, 'CYCLES_TOUCHED',
           count(DISTINCT selection_cycle)::text
      FROM bettor_state_observations GROUP BY 1
    UNION ALL
    -- DOES THE CURSOR STILL ADVANCE WHEN BUDGETS SHRINK? If it does,
    -- successive cycles differ. A frozen cursor would show the same
    -- small cycle set repeating.
    SELECT universe_version, 'MIN_CYCLE',
           min(selection_cycle)::text
      FROM bettor_state_observations GROUP BY 1
    UNION ALL
    SELECT universe_version, 'MAX_CYCLE',
           max(selection_cycle)::text
      FROM bettor_state_observations GROUP BY 1
    UNION ALL
    SELECT universe_version, 'ROWS_WITH_TRUNCATED_SLICE',
           count(*) FILTER (WHERE slice_truncated)::text
      FROM bettor_state_observations GROUP BY 1
) c

UNION ALL

-- REVISITS. How often a market that was reached is reached again --
-- the quantity that decides whether short-horizon dynamics are
-- observable at all.
SELECT 'D_REVISITS',
       universe_version || ' | visits=' || least(visits, 5)::text
       || CASE WHEN visits > 5 THEN '+' ELSE '' END,
       count(*)::text
  FROM (SELECT universe_version, market_id, count(*) AS visits
          FROM bettor_state_observations
         GROUP BY 1, 2) r
 GROUP BY 2

UNION ALL

-- MISSING FOLLOW-UPS, not just missing initial reads. An observation
-- whose horizon has passed with no mid row is a gap in the outcome
-- side, and it is counted here rather than left to be noticed.
SELECT 'E_FOLLOWUP_GAPS', k, v FROM (
    SELECT 'OBSERVATIONS_PAST_60S' AS k,
           count(*)::text AS v
      FROM bettor_state_observations
     WHERE observed_at < now() - interval '90 seconds'
    UNION ALL
    SELECT 'OF_THOSE_WITH_A_60S_MID',
           count(*)::text
      FROM bettor_state_observations o
     WHERE o.observed_at < now() - interval '90 seconds'
       AND EXISTS (SELECT 1 FROM bettor_state_mids m
                    WHERE m.observation_id = o.observation_id
                      AND m.horizon_s = 60)
    UNION ALL
    SELECT 'OBSERVATIONS_PAST_300S',
           count(*)::text
      FROM bettor_state_observations
     WHERE observed_at < now() - interval '330 seconds'
    UNION ALL
    SELECT 'OF_THOSE_WITH_A_300S_MID',
           count(*)::text
      FROM bettor_state_observations o
     WHERE o.observed_at < now() - interval '330 seconds'
       AND EXISTS (SELECT 1 FROM bettor_state_mids m
                    WHERE m.observation_id = o.observation_id
                      AND m.horizon_s = 300)
    UNION ALL
    SELECT 'MID_ROWS_TOTAL', count(*)::text FROM bettor_state_mids
) e

UNION ALL

-- THROUGHPUT, for the completion estimates. Rows per minute over the
-- window each version actually ran.
SELECT 'F_THROUGHPUT',
       universe_version,
       round(count(*)::numeric / nullif(
           GREATEST(extract(epoch FROM (max(observed_at)
                                        - min(observed_at))) / 60.0,
                    1), 0), 2)::text || ' rows/min over '
       || round(extract(epoch FROM (max(observed_at)
                                    - min(observed_at))) / 60.0, 1)::text
       || ' min'
  FROM bettor_state_observations
 GROUP BY 2

ORDER BY 1, 2
