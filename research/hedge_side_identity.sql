-- CAN A HELD FUNDED POSITION BE BOUND TO THE SIDE IT ACTUALLY HOLDS?
--
-- §4 OF THE DIRECTIVE, and the defect it names:
--
--   "ROW_SQL selects by market_slug with LIMIT 1 without binding to the held
--    side... Do not pretend opposite sides are separate venue instruments to
--    avoid fixing the identity model."
--
-- ROW_SQL is `WHERE market_slug = $1 LIMIT 1` with NO ORDER BY. Run 264
-- measured exactly 2.00 rows per market_slug, so the held leg's orientation --
-- which participant it backs, and therefore its whole payout function -- comes
-- from whichever of the two rows Postgres happens to return. A leg built from
-- the wrong row has its payout inverted, silently.
--
-- The repair needs a KEY. This run asks what actually distinguishes the two
-- rows of one market_slug, and whether `bettor_funded_intents` carries enough
-- to pick one of them. It does not assume an answer: the last two times I
-- assumed a column's meaning here (`kind`, then the side class) the measurement
-- refuted it.
--
-- A SECOND, SEPARATE QUESTION, also §4: the ranking currently emits two
-- candidates with the SAME condition_id and different scores. If the catalogue
-- has one row per side, then the candidate identity must be (slug, side) and
-- not slug -- and section 6 below counts how often one slug carries two
-- admissible sides, which is how much of the candidate set the collapse hides.
--
-- READ-ONLY. Only \echo, the one meta-command the guard permits.

\echo == 0 HOW MANY ROWS PER market_slug, AND WHAT VARIES WITHIN ONE ==
-- If `identifier` is constant within a slug then it is not the side key. If
-- side_norm varies on every slug, it is the candidate.
SELECT count(*)                                        AS rows,
       count(DISTINCT market_slug)                     AS slugs,
       round(count(*)::numeric
             / nullif(count(DISTINCT market_slug), 0), 3) AS rows_per_slug,
       count(DISTINCT identifier)                      AS identifiers,
       count(DISTINCT (market_slug, side_norm))        AS slug_side_pairs,
       count(DISTINCT (market_slug, identifier))       AS slug_identifier_pairs
  FROM us_premap;

\echo == 1 WITHIN ONE market_slug, WHICH COLUMNS DIFFER BETWEEN THE ROWS ==
-- The answer names the binding key. A column with 2 distinct values on
-- essentially every slug separates the sides; a column with 1 does not.
WITH per_slug AS (
  SELECT market_slug,
         count(*)                          AS n,
         count(DISTINCT identifier)        AS d_identifier,
         count(DISTINCT side_norm)         AS d_side_norm,
         count(DISTINCT coalesce(team_abbr, ''))  AS d_team_abbr,
         count(DISTINCT intent)            AS d_intent,
         count(DISTINCT coalesce(signed, ''))     AS d_signed,
         count(DISTINCT coalesce(line, ''))       AS d_line,
         count(DISTINCT sports_type)       AS d_sports_type
    FROM us_premap
   GROUP BY market_slug)
SELECT n                                            AS rows_in_slug,
       count(*)                                     AS slugs,
       count(*) FILTER (WHERE d_identifier > 1)      AS identifier_varies,
       count(*) FILTER (WHERE d_side_norm > 1)       AS side_norm_varies,
       count(*) FILTER (WHERE d_team_abbr > 1)       AS team_abbr_varies,
       count(*) FILTER (WHERE d_intent > 1)          AS intent_varies,
       count(*) FILTER (WHERE d_signed > 1)          AS signed_varies,
       count(*) FILTER (WHERE d_line > 1)            AS line_varies,
       count(*) FILTER (WHERE d_sports_type > 1)     AS sports_type_varies
  FROM per_slug
 GROUP BY n
 ORDER BY n;

\echo == 2 THE intent VOCABULARY, AND WHETHER IT IS THE SIDE ==
SELECT intent, count(*) AS rows,
       count(DISTINCT market_slug) AS slugs,
       count(DISTINCT left(market_slug, 4)) AS prefixes
  FROM us_premap
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 20;

\echo == 3 ONE FIXTURE, PRINTED IN FULL, PER FAMILY PREFIX ==
-- Not a summary. The two rows of one slug side by side, for a moneyline, a
-- spread and a total, so the shape is read rather than inferred from counts.
WITH pick AS (
  SELECT DISTINCT ON (left(market_slug, 4)) left(market_slug, 4) AS fam,
         market_slug
    FROM us_premap
   WHERE market_slug LIKE 'aec-%' OR market_slug LIKE 'asc-%'
      OR market_slug LIKE 'tsc-%'
   ORDER BY left(market_slug, 4), market_slug)
SELECT p.fam, u.market_slug, u.identifier, u.side_norm, u.team_abbr,
       u.intent, u.signed, u.line, u.sports_type
  FROM pick p JOIN us_premap u USING (market_slug)
 ORDER BY p.fam, u.side_norm;

\echo == 4 WHAT DOES A FUNDED INTENT CARRY THAT COULD PICK A SIDE ==
-- order_intent, held_is_long, leg_role. If order_intent's vocabulary is the
-- same as us_premap.intent, the join is direct. If not, the side has to come
-- from somewhere else and this run says so instead of inventing it.
SELECT order_intent,
       held_is_long,
       count(*) AS intents,
       count(DISTINCT us_market_slug) AS slugs
  FROM bettor_funded_intents
 GROUP BY 1, 2
 ORDER BY 3 DESC
 LIMIT 20;

\echo == 5 EVERY FUNDED INTENT, JOINED TO ITS CATALOGUE ROWS ==
-- THE OPERATIVE QUESTION. For each real held position: how many catalogue rows
-- does its slug match, and does anything it carries reduce that to one?
SELECT i.intent_id, i.us_market_slug, i.order_intent, i.held_is_long,
       i.state, i.residual_qty,
       count(u.*)                                          AS catalogue_rows,
       count(*) FILTER (WHERE u.intent = i.order_intent)    AS rows_matching_order_intent,
       string_agg(DISTINCT u.side_norm, ' | ')              AS sides_available
  FROM bettor_funded_intents i
  LEFT JOIN us_premap u ON u.market_slug = i.us_market_slug
 GROUP BY i.intent_id, i.us_market_slug, i.order_intent, i.held_is_long,
          i.state, i.residual_qty
 ORDER BY i.intent_id
 LIMIT 60;

\echo == 6 HOW MUCH OF THE CANDIDATE SET DOES A SLUG-ONLY IDENTITY HIDE ==
-- The sibling read is DISTINCT ON (market_slug, coalesce(team_abbr, side_norm))
-- so it already returns one row per side -- but downstream keys everything by
-- slug. This counts, per fixture, the distinct slugs against the distinct
-- (slug, side) pairs: the difference is candidates that collapse onto one
-- identity in quoting, valuation, ranking, persistence and dispatch.
SELECT count(DISTINCT event_slug)                        AS fixtures,
       sum(d_slugs)                                      AS total_slugs,
       sum(d_sides)                                      AS total_slug_side_pairs,
       sum(d_sides) - sum(d_slugs)                       AS candidates_hidden_by_slug_only,
       count(*) FILTER (WHERE d_sides > d_slugs)         AS fixtures_affected
  FROM (SELECT event_slug,
               count(DISTINCT market_slug)                       AS d_slugs,
               count(DISTINCT (market_slug, side_norm))           AS d_sides
          FROM us_premap
         WHERE game_start IS NOT NULL
           AND game_start > now()
         GROUP BY event_slug) t;

\echo == 7 THE SAME, FOR THE THREE PRIORITY FIXTURES NAMED EARLIER ==
SELECT event_slug,
       count(*)                                    AS rows,
       count(DISTINCT market_slug)                 AS slugs,
       count(DISTINCT (market_slug, side_norm))     AS slug_side_pairs
  FROM us_premap
 WHERE event_slug IN ('nfl-pit-cle-2026-10-01',
                      'cfb-wkent-nmxst-2026-10-01')
 GROUP BY event_slug
 ORDER BY event_slug;
