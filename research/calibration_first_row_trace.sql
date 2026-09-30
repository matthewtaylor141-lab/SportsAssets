-- READ-ONLY. WHY A FIXTURE WITH A KNOWN OUTCOME IS NOT A RESOLVED FIXTURE.
--
-- The evaluator keeps the EARLIEST valuation per event_key and classifies
-- that row. This lists, for every in-scope fixture in the 90-day window, the
-- earliest row, the earliest row that carries a probability, and whether each
-- was joined to a venue outcome -- so each fixture's path to RESOLVED,
-- RESOLVED_BUT_NO_PROBABILITY or UNRESOLVED is visible row by row.

\echo '== F1 · per fixture: earliest row vs earliest priced row =='
WITH s AS (
  SELECT id, event_key, observed_at, decided_at, probability, outcome_known,
         outcome_basis, record_purpose, us_market_slug, admissible,
         refusals
    FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND decided_at >= now() - interval '90 days'
     AND version = 'PINNACLE_DEVIG_V1' AND devig_method = 'power'
     AND sport_family IN ('baseball', 'soccer') AND market = 'h2h'
     AND coalesce(event_key, '') <> ''),
first_any AS (
  SELECT DISTINCT ON (event_key) * FROM s ORDER BY event_key, observed_at, id),
first_priced AS (
  SELECT DISTINCT ON (event_key) * FROM s WHERE probability IS NOT NULL
   ORDER BY event_key, observed_at, id),
agg AS (
  SELECT event_key, count(*) AS rows,
         count(*) FILTER (WHERE outcome_known) AS rows_joined,
         count(*) FILTER (WHERE probability IS NOT NULL) AS rows_priced,
         bool_or(outcome_known) AS any_joined
    FROM s GROUP BY event_key)
SELECT CASE
         WHEN fa.outcome_known AND fa.probability IS NOT NULL THEN 'A_RESOLVED'
         WHEN fa.outcome_known AND fa.probability IS NULL
              AND fp.id IS NOT NULL AND fp.outcome_known THEN 'B_FIRST_ROW_UNPRICED_LATER_PRICED_ROW_JOINED'
         WHEN fa.outcome_known AND fa.probability IS NULL
              AND fp.id IS NOT NULL THEN 'C_FIRST_ROW_UNPRICED_PRICED_ROW_NOT_JOINED'
         WHEN fa.outcome_known AND fa.probability IS NULL THEN 'D_NO_ROW_EVER_PRICED'
         WHEN NOT fa.outcome_known AND a.any_joined THEN 'E_FIRST_ROW_NOT_JOINED_LATER_ROWS_JOINED'
         ELSE 'F_NOTHING_JOINED_YET' END            AS path,
       count(*)                                     AS fixtures,
       sum(a.rows)                                  AS rows,
       sum(a.rows_joined)                           AS rows_joined,
       min(fa.event_key)                            AS example_event_key
  FROM first_any fa
  JOIN agg a USING (event_key)
  LEFT JOIN first_priced fp USING (event_key)
 GROUP BY 1 ORDER BY 1;

\echo '== F2 · the earliest rows that carry no probability: what refused them =='
WITH s AS (
  SELECT * FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND decided_at >= now() - interval '90 days'
     AND version = 'PINNACLE_DEVIG_V1' AND devig_method = 'power'
     AND sport_family IN ('baseball', 'soccer') AND market = 'h2h'
     AND coalesce(event_key, '') <> ''),
fa AS (SELECT DISTINCT ON (event_key) * FROM s ORDER BY event_key, observed_at, id)
SELECT coalesce(refusals[1], '(none)') AS first_refusal, count(*) AS fixtures,
       min(us_market_slug) AS example_slug
  FROM fa WHERE probability IS NULL
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== F3 · fixtures whose earliest row is unjoined while later rows are joined =='
WITH s AS (
  SELECT * FROM external_valuations
   WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND decided_at >= now() - interval '90 days'
     AND version = 'PINNACLE_DEVIG_V1' AND devig_method = 'power'
     AND sport_family IN ('baseball', 'soccer') AND market = 'h2h'
     AND coalesce(event_key, '') <> ''),
fa AS (SELECT DISTINCT ON (event_key) * FROM s ORDER BY event_key, observed_at, id),
j AS (SELECT event_key, min(id) FILTER (WHERE outcome_known) AS first_joined_id,
             count(*) FILTER (WHERE NOT outcome_known) AS unjoined_rows,
             count(*) FILTER (WHERE outcome_known) AS joined_rows
        FROM s GROUP BY event_key)
SELECT fa.event_key, fa.id AS first_id, fa.decided_at, fa.us_market_slug,
       fa.record_purpose, fa.probability IS NOT NULL AS priced,
       coalesce(fa.refusals[1], '(none)') AS first_refusal,
       j.first_joined_id, j.joined_rows, j.unjoined_rows
  FROM fa JOIN j USING (event_key)
 WHERE NOT fa.outcome_known AND j.joined_rows > 0
 ORDER BY fa.decided_at;
