-- RC6.2 review of lane p-coverage (independent reviewer), read only.
-- THE TERMS-READER CHANGE ON THE WHOLE POPULATION, not a sample. The lane's
-- bettor_settlement_terms change adds a payout phrase ("resolve(s|d) to a
-- fair price"), a never-played phrase ("not (be) started") and three
-- delayed-then-played phrases that drop a C_NOT_PLAYED reading from a
-- sentence ("after the rescheduled X has finished", "once the X is
-- complete(d)", "but begins within"). Its regression fixture holds 57
-- never-attested PMUS money-line texts. This counts EVERY active rules text
-- that any new phrase touches, by venue and market type, and returns the
-- distinct PMUS texts so the reader can be run on them under both trees.
--   V1  active rows / distinct rule fingerprints touched, by venue x type
--   V2  PMUS: up to 6 distinct texts per market type (lowest md5 of sha)
--   V3  plane fixture scope: PMUS soccer / baseball full-game winners whose
--       event has a venue_fixture_metadata row with phase AND format, by
--       sport x valued (settlement_basis) x reader_version
-- Public venue text only; no secret, no write.
\echo === V1. active rows and distinct fingerprints touched by a new phrase: venue x market type ===
WITH t AS (
  SELECT g.venue, coalesce(g.market_type, '<none>') AS market_type,
         r.rules_sha256,
         (coalesce(r.rules_text, '') || ' ' || coalesce(r.rules_secondary, ''))
           AS txt
    FROM market_plane_registry g
    JOIN market_plane_rules r USING (contract_id)
   WHERE g.active)
SELECT venue, market_type, count(*) AS n,
       count(DISTINCT rules_sha256) AS fingerprints,
       count(*) FILTER (WHERE txt ~* '\yresolve[sd]?\s+to\s+a\s+fair\s+price\y')
         AS fair_price,
       count(*) FILTER (WHERE txt ~* '\ynot\s+(be\s+)?started\y') AS not_started,
       count(*) FILTER (WHERE txt ~* '\yafter\s+the\s+rescheduled\s+\w+\s+has\s+finished\y'
                           OR txt ~* '\yonce\s+the\s+\w+\s+is\s+completed?\y'
                           OR txt ~* '\ybut\s+begins\s+within\y') AS delayed_played
  FROM t
 WHERE txt ~* '\yresolve[sd]?\s+to\s+a\s+fair\s+price\y'
    OR txt ~* '\ynot\s+(be\s+)?started\y'
    OR txt ~* '\yafter\s+the\s+rescheduled\s+\w+\s+has\s+finished\y'
    OR txt ~* '\yonce\s+the\s+\w+\s+is\s+completed?\y'
    OR txt ~* '\ybut\s+begins\s+within\y'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC LIMIT 200;
\echo === V2. PMUS distinct texts touched: [market_type, sha, n_rows, text] ===
WITH t AS (
  SELECT coalesce(g.market_type, '<none>') AS market_type, r.rules_sha256,
         max(r.rules_text) AS rules_text, count(*) AS n
    FROM market_plane_registry g
    JOIN market_plane_rules r USING (contract_id)
   WHERE g.active AND g.venue = 'POLYMARKET_US'
     AND (r.rules_text ~* '\yresolve[sd]?\s+to\s+a\s+fair\s+price\y'
          OR r.rules_text ~* '\ynot\s+(be\s+)?started\y'
          OR r.rules_text ~* '\yafter\s+the\s+rescheduled\s+\w+\s+has\s+finished\y'
          OR r.rules_text ~* '\yonce\s+the\s+\w+\s+is\s+completed?\y'
          OR r.rules_text ~* '\ybut\s+begins\s+within\y')
   GROUP BY 1, 2),
k AS (SELECT t.*, row_number() OVER (PARTITION BY market_type
                                     ORDER BY md5(rules_sha256)) AS rk
        FROM t)
SELECT json_build_array(market_type, rules_sha256, n, rules_text)::text AS j
  FROM k WHERE rk <= 6 ORDER BY market_type, rk LIMIT 300;
\echo === V3. plane fixture scope held for PMUS soccer / baseball full-game winners ===
SELECT g.sport, coalesce(g.settlement_basis, '-') AS basis,
       coalesce(m.reader_version, '<no row>') AS reader,
       (m.phase IS NOT NULL AND m.game_format IS NOT NULL) AS scoped,
       count(*) AS n
  FROM market_plane_registry g
  LEFT JOIN venue_fixture_metadata m
    ON m.venue = 'PMUS' AND m.venue_fixture_key = 'event:' || g.event_id
 WHERE g.active AND g.venue = 'POLYMARKET_US'
   AND g.sport IN ('soccer', 'baseball') AND g.family = 'WINNER'
   AND g.period = 'FULL_EVENT'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC LIMIT 60;
