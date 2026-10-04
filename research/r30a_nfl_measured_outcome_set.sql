-- R30A NFL STREAM, read-only: the MEASURED Pinnacle outcome set for NFL money lines,
-- the venue rules text the collector persisted for them, and the probability ages.
--
-- WHY: bettor_pinnacle_devig.SUPPORTED admits a (sport, market) only "by
-- measurement" (the book quotes it with a COMPLETE outcome set). Since cand22 an
-- unsupported market keeps the book's priced set on the row (raw_odds,
-- outcomes_priced) "so the measured-set rule can be evaluated". This reads that
-- record for americanfootball_nfl: how many outcomes, which names, any Draw,
-- the overround, the venue rules text (one or several wordings), and the
-- received_at - observed_at age the 30 s rule judges.
\echo '== M0 · read instant =='
SELECT now() AS read_at, (SELECT max(version) FROM schema_migrations) AS schema_head;

\echo '== M1 · NFL valuation rows: outcome-set shape (all time) =='
SELECT count(*) AS rows,
       count(DISTINCT us_market_slug) AS contracts,
       min(decided_at) AS first_at, max(decided_at) AS last_at,
       count(*) FILTER (WHERE outcomes_priced = 2) AS priced_2,
       count(*) FILTER (WHERE outcomes_priced = 3) AS priced_3,
       count(*) FILTER (WHERE outcomes_priced NOT IN (2, 3)) AS priced_other,
       count(*) FILTER (WHERE raw_odds ? 'Draw' OR raw_odds ? 'draw' OR raw_odds ? 'Tie') AS with_draw_key,
       count(*) FILTER (WHERE book = 'pinnacle') AS book_pinnacle,
       count(*) FILTER (WHERE probability IS NOT NULL) AS with_probability
  FROM external_valuations
 WHERE sport_family = 'football' AND us_market_slug LIKE 'aec-nfl-%';

\echo '== M2 · per provider: outcome counts, overround range =='
SELECT provider, outcomes_priced, expected_outcomes, count(*) AS n,
       round(min(overround)::numeric, 5) AS overround_min,
       round(max(overround)::numeric, 5) AS overround_max
  FROM external_valuations
 WHERE sport_family = 'football' AND us_market_slug LIKE 'aec-nfl-%'
 GROUP BY 1, 2, 3 ORDER BY 1, 2;

\echo '== M3 · ten newest NFL rows: the priced set as recorded =='
SELECT id, decided_at, us_market_slug, contract_selection, provider,
       outcomes_priced, raw_odds, round(overround::numeric, 5) AS overround,
       round(extract(epoch FROM (received_at - observed_at))::numeric, 3) AS receipt_minus_observed_s,
       record_purpose, refusals[1:4] AS first_refusals
  FROM external_valuations
 WHERE sport_family = 'football' AND us_market_slug LIKE 'aec-nfl-%'
 ORDER BY decided_at DESC LIMIT 10;

\echo '== M4 · the venue rules text persisted on NFL rows: distinct wordings =='
SELECT md5(settlement_comparison->>'venue_rules_text') AS text_md5,
       count(*) AS rows, count(DISTINCT us_market_slug) AS contracts,
       left(regexp_replace(settlement_comparison->>'venue_rules_text',
                           'This market will settle to the winner of the .* NFL game scheduled for [A-Za-z]+ [0-9]+, [0-9]{4}\.',
                           '<WINNER-OF-NAMED-GAME-ON-DATE>.'), 420) AS text_with_names_masked
  FROM external_valuations
 WHERE sport_family = 'football' AND us_market_slug LIKE 'aec-nfl-%'
   AND settlement_comparison ? 'venue_rules_text'
 GROUP BY 1, 4 ORDER BY 2 DESC LIMIT 10;

\echo '== M5 · NFL catalogue rows on the venue: sports_type and start instants (next 8 days) =='
SELECT sports_type, count(*) AS contracts, min(game_start) AS first_start, max(game_start) AS last_start,
       count(*) FILTER (WHERE lower(coalesce(event_title, '') || ' ' || coalesce(question, '')) LIKE '%pro bowl%') AS pro_bowl_rows,
       count(*) FILTER (WHERE lower(coalesce(event_title, '') || ' ' || coalesce(question, '')) LIKE '%preseason%') AS preseason_rows
  FROM us_premap
 WHERE market_slug LIKE 'aec-nfl-%' AND game_start > now() - interval '1 day'
   AND game_start < now() + interval '8 days'
 GROUP BY 1 ORDER BY 2 DESC;
