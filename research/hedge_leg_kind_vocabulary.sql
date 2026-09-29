-- THE KIND AND THE PERIOD COME FROM sports_type, NOT FROM THE SIDE CLASS.
--
-- Run 263 refuted the side-class derivation I had written, twice over:
--
--   * an `aec-` moneyline row's side_norm is the TEAM NAME ('doosan bears',
--     'broncos'), not yes/no -- so "yes/no with no handicap = moneyline"
--     refuses every real moneyline, which is every position this lane holds;
--   * an `aec-` moneyline row nonetheless carries a `line` value (20, 30, 00,
--     55) which is NOT a handicap. A supplier that read "line present =>
--     spread" would have built every moneyline as a spread at line 20. That
--     is the fabricated-hedge case, reached by reading a populated column.
--
-- What DOES name both facts is `sports_type`:
--     football_team_full_game_winner   -> moneyline, full game
--     football_team_first_half_spread  -> spread,    first half
--
-- So the supplier needs the real vocabulary of trailing tokens, with counts,
-- and a refusal for anything it does not recognise. 215 distinct values
-- exist; an allowlist assembled from memory already broke this lane once
-- (the realism classifier invented twelve families and omitted ufc_, darts_
-- and futures), so it is read in full here with no LIMIT that could hide one.

\echo == 0 EVERY sports_type, WITH ITS SIDE CLASS AND WHETHER A HANDICAP IS STATED ==
SELECT sports_type,
       count(*)                                              AS rows,
       count(DISTINCT left(market_slug, 4))                  AS slug_prefixes,
       min(left(market_slug, 4))                             AS a_prefix,
       count(*) FILTER (WHERE side_norm IN ('yes','no'))      AS yes_no,
       count(*) FILTER (WHERE side_norm IN ('over','under'))  AS over_under,
       count(*) FILTER (WHERE signed IS NOT NULL
                          AND signed <> '')                  AS signed_stated,
       count(*) FILTER (WHERE line IS NOT NULL
                          AND line <> '')                    AS line_stated
  FROM us_premap
 GROUP BY sports_type
 ORDER BY rows DESC;

\echo == 1 THE TRAILING TOKENS: WHAT NAMES THE KIND ==
-- The last token of a sports_type is the market kind on the rows seen so far
-- (winner / spread / total). This is the whole distinct set of last tokens,
-- so an allowlist can be complete rather than plausible.
SELECT split_part(sports_type, '_', -1)     AS last_token,
       count(*)                             AS rows,
       count(DISTINCT sports_type)          AS types,
       min(sports_type)                     AS an_example
  FROM us_premap
 WHERE sports_type IS NOT NULL
 GROUP BY 1
 ORDER BY rows DESC;

\echo == 2 THE PERIOD TOKENS ==
-- `first_half` sits between the subject and the kind. This lists every
-- interior token so the period map can be complete too.
SELECT t.tok, count(*) AS occurrences, count(DISTINCT p.sports_type) AS types,
       min(p.sports_type) AS an_example
  FROM us_premap p
 CROSS JOIN LATERAL unnest(string_to_array(p.sports_type, '_')) AS t(tok)
 WHERE p.sports_type IS NOT NULL
 GROUP BY t.tok
 ORDER BY occurrences DESC
 LIMIT 80;

\echo == 3 THE aec- MONEYLINE: IS THE SLUG REALLY SHARED BY BOTH SIDES ==
-- If both sides of a moneyline carry the SAME market_slug, then the opposite
-- side is not a second settling holding -- it is netting on one instrument,
-- and a candidate sharing the held slug must be refused as such.
SELECT left(market_slug, 4)                       AS slug_prefix,
       count(*)                                   AS rows,
       count(DISTINCT market_slug)                AS distinct_slugs,
       round(count(*)::numeric
             / greatest(count(DISTINCT market_slug), 1), 2) AS rows_per_slug,
       count(DISTINCT intent)                     AS distinct_intents
  FROM us_premap
 GROUP BY 1
 ORDER BY rows DESC
 LIMIT 20;

\echo == 4 WHAT line MEANS ON AN aec- ROW, SINCE IT IS NOT A HANDICAP ==
SELECT line, count(*) AS rows, count(DISTINCT sports_type) AS types,
       min(sports_type) AS an_example
  FROM us_premap
 WHERE left(market_slug, 4) = 'aec-'
 GROUP BY line
 ORDER BY rows DESC
 LIMIT 20;

\echo == 5 A BASEBALL MONEYLINE ROW IN FULL, WHICH IS WHAT THIS LANE HOLDS ==
SELECT market_slug, event_slug, event_title, question, sports_type,
       side_norm, team_abbr, team_name, line, signed, intent, kind
  FROM us_premap
 WHERE sports_type LIKE 'baseball%'
 ORDER BY updated_at DESC
 LIMIT 4;
