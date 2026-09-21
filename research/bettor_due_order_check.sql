-- DOES THE NEW DUE-QUERY ORDERING PARSE AND SORT AS INTENDED?
--
-- The repair puts a boolean expression first in ORDER BY:
--     ORDER BY (abs(extract(epoch FROM (now() - observed_at)) - h) > tol),
--              observed_at
-- FALSE sorts before TRUE in Postgres, so in-band tasks lead and
-- oldest-first still breaks ties inside each group. A boolean sort key
-- with a bound parameter is exactly the kind of thing that parses in
-- my head and fails at runtime, so it is executed here before it is
-- deployed rather than after.
--
-- Read only. No settlement term. Literals stand in for bind params.

SELECT 'A_ORDER_PROBE' AS section,
       lpad(row_number() OVER ()::text, 2, '0')
       || ' h=' || h.horizon::text,
       'age=' || round(extract(epoch FROM (now() - o.observed_at))::numeric, 0)::text
       || ' inBand=' || (abs(extract(epoch FROM (now() - o.observed_at))
                             - h.horizon) <= 30)::text
  FROM (VALUES (300)) AS h(horizon)
 CROSS JOIN bettor_state_observations o
 WHERE o.observed_at <= now() - ((h.horizon - 30) || ' seconds')::interval
   AND o.observed_at >  now() - ((h.horizon + 600) || ' seconds')::interval
 ORDER BY (abs(extract(epoch FROM (now() - o.observed_at))
                - h.horizon) > 30),
          o.observed_at
 LIMIT 12
