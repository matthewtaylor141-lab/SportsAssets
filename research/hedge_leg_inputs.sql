-- WHAT THE VENUE CATALOGUE CAN ACTUALLY SUPPLY FOR AN INDIRECT LEG.
--
-- `bettor_indirect_structures.Leg` requires eleven STATED facts. Six are
-- meant to come from `us_premap`, and the supplier about to be written must
-- map real column VALUES onto the module vocabulary -- KIND_MONEYLINE /
-- KIND_SPREAD / KIND_TOTAL / KIND_THREE_WAY, and backs A / B / DRAW.
--
-- RUN 262 REFUTED THE MAPPING I WAS ABOUT TO WRITE, on its first statement:
--
--   * `kind` is the single literal 'side' on all 60,540 rows. It does NOT
--     name the market type. A supplier reading `kind` would have built every
--     leg as one type, silently;
--   * `line` is TEXT, so `line * 2` is an operator error -- and a Fraction
--     parsed from free text needs a refusal for the unparseable case;
--   * `side_norm` is yes/no (23,156 each), over/under (4,664 each) and
--     otherwise a participant name.
--
-- So the market type is carried somewhere else, and this run asks WHERE
-- rather than assuming. Read-only; only \echo, which is the one
-- meta-command the guard permits.

\echo == 0 HOW MANY DISTINCT VALUES DOES EACH CANDIDATE TYPE COLUMN TAKE ==
-- If `kind` is constant, the type must be in the slug, the question, the
-- intent, or `signed`. Count first, then look.
SELECT count(*)                                   AS rows,
       count(DISTINCT kind)                       AS kinds,
       count(DISTINCT intent)                     AS intents,
       count(DISTINCT sports_type)                AS sports_types,
       count(*) FILTER (WHERE signed IS NOT NULL
                          AND signed <> '')       AS signed_present,
       count(*) FILTER (WHERE line IS NOT NULL
                          AND line <> '')         AS line_present,
       count(DISTINCT left(market_slug, 4))       AS slug_prefixes
  FROM us_premap;

\echo == 1 THE SLUG PREFIX IS THE MARKET FAMILY: PREFIX x SIDE CLASS ==
-- `aec-` and `asc-` appear throughout the mapping code as distinct families.
-- This crosses the prefix against the side class and against whether a
-- signed handicap is stated, which is what separates a spread from a
-- moneyline without reading prose.
SELECT left(market_slug, 4)                                AS slug_prefix,
       CASE WHEN side_norm IN ('yes','no')     THEN 'yes_no'
            WHEN side_norm IN ('over','under') THEN 'over_under'
            ELSE 'participant_name' END                    AS side_class,
       (signed IS NOT NULL AND signed <> '')               AS signed_stated,
       count(*)                                            AS rows,
       count(DISTINCT sports_type)                         AS sports_types,
       min(line)                                           AS a_line,
       max(line)                                           AS another_line
  FROM us_premap
 GROUP BY 1, 2, 3
 ORDER BY rows DESC
 LIMIT 30;

\echo == 2 THE LINE TEXT: SHAPE AND WHETHER IT IS HALF INTEGER ==
-- An integer line can land exactly on the number, and `missing_facts`
-- refuses such a leg unless the push rule was captured -- so this split
-- decides how many candidates are constructible at all. Cast explicitly;
-- `line` is text and a bad cast must not abort the file.
SELECT CASE WHEN line IS NULL OR line = ''            THEN 'absent'
            WHEN line !~ '^-?[0-9]+(\.[0-9]+)?$'      THEN 'not_a_plain_number'
            WHEN (line::numeric * 2) = floor(line::numeric * 2)
             AND line::numeric <> floor(line::numeric) THEN 'half_integer'
            WHEN line::numeric = floor(line::numeric)  THEN 'integer'
            ELSE 'other' END                          AS line_shape,
       count(*)                                       AS rows,
       min(line)                                      AS example,
       count(DISTINCT left(market_slug, 4))           AS prefixes
  FROM us_premap
 GROUP BY 1
 ORDER BY rows DESC;

\echo == 3 SIBLING CONTRACTS ON ONE EVENT: THE CANDIDATE LEG SET ==
-- The contracts a hedge could be built from are the other market_slugs
-- under the same event_slug. `event_slug` and `team_abbr` are selected
-- because ORIENTATION depends on them: `backs` must name the fixture's
-- FIRST listed participant, and the only ordering authority on the row is
-- the order the two team codes appear in the event slug. A slug without two
-- codes must make the leg refuse.
WITH widest AS (
  SELECT event_slug, count(*) AS n
    FROM us_premap
   WHERE event_slug IS NOT NULL
   GROUP BY event_slug
   ORDER BY n DESC
   LIMIT 2)
SELECT w.n                       AS siblings_on_event,
       left(p.event_slug, 34)    AS event_slug,
       p.team_abbr, p.side_norm, p.line, p.signed, p.intent,
       left(p.market_slug, 46)   AS market_slug,
       p.sports_type
  FROM widest w
  JOIN us_premap p USING (event_slug)
 ORDER BY w.n DESC, p.market_slug, p.side_norm
 LIMIT 44;

\echo == 4 A PLAIN TWO SIDED EVENT, WHICH IS WHAT A HELD POSITION LOOKS LIKE ==
SELECT left(p.event_slug, 34)    AS event_slug,
       left(p.event_title, 40)   AS event_title,
       p.team_abbr, p.team_name, p.side_norm, p.line, p.signed, p.intent,
       left(p.market_slug, 46)   AS market_slug
  FROM us_premap p
 WHERE p.event_slug = (SELECT event_slug FROM us_premap
                        WHERE sports_type LIKE 'baseball%'
                          AND event_slug IS NOT NULL
                        GROUP BY event_slug HAVING count(*) BETWEEN 2 AND 8
                        ORDER BY max(game_start) DESC NULLS LAST LIMIT 1)
 ORDER BY p.market_slug, p.side_norm
 LIMIT 20;

\echo == 5 HOW MANY EVENTS CARRY MORE THAN ONE CONTRACT AT ALL ==
-- A hedge needs a second contract on the same fixture. If most events are a
-- single pair, the candidate set is structurally thin -- a fact about the
-- venue, not about the supplier.
SELECT CASE WHEN n <= 2 THEN '1-2'
            WHEN n <= 4 THEN '3-4'
            WHEN n <= 8 THEN '5-8'
            WHEN n <= 20 THEN '9-20'
            ELSE '21+' END          AS contracts_per_event,
       count(*)                     AS events,
       sum(n)                       AS contracts
  FROM (SELECT event_slug, count(*) AS n
          FROM us_premap
         WHERE event_slug IS NOT NULL
         GROUP BY event_slug) t
 GROUP BY 1
 ORDER BY 1;

\echo == 6 THE HELD FUNDED POSITIONS AND THEIR CATALOGUE ROW ==
-- The held_leg supplier starts from a funded intent. If the intent slug has
-- no catalogue row, the leg refuses -- so this join IS the supplier.
SELECT i.intent_id, i.us_market_slug, i.order_intent, i.quantity,
       (p.market_slug IS NOT NULL)  AS catalogue_row_present,
       p.side_norm, p.line, p.signed, p.sports_type, p.event_slug
  FROM bettor_funded_intents i
  LEFT JOIN us_premap p ON p.market_slug = i.us_market_slug
 ORDER BY i.intent_id
 LIMIT 30;
