-- WHAT THE VENUE CATALOGUE CAN ACTUALLY SUPPLY FOR AN INDIRECT LEG.
--
-- `bettor_indirect_structures.Leg` requires eleven STATED facts. Six of them
-- are meant to come from `us_premap`, and the supplier about to be written
-- must map real column VALUES onto the module vocabulary -- KIND_MONEYLINE /
-- KIND_SPREAD / KIND_TOTAL / KIND_THREE_WAY, and backs A / B / DRAW. A
-- mapping written from the column NAMES would be a guess, and a guessed
-- orientation is exactly the fabricated-hedge case the module refuses.
--
-- So: what values do `kind` and `side_norm` take, is `line` populated where
-- the kind needs one, and what do an event's sibling contracts look like
-- side by side? Read-only; no keyword this workflow refuses.

-- Only \echo is permitted by the workflow guard; \pset and \timing are
-- refused, which is how run 261 failed before a single statement ran.

\echo == 0 DISTINCT kind, WITH LINE AND SIDE COVERAGE ==
SELECT kind,
       count(*)                                        AS rows,
       count(DISTINCT event_slug)                      AS events,
       count(line)                                     AS with_line,
       count(side_norm)                                AS with_side,
       count(DISTINCT side_norm)                       AS distinct_sides
  FROM us_premap
 GROUP BY kind
 ORDER BY rows DESC;

\echo == 1 DISTINCT side_norm PER kind ==
SELECT kind, side_norm, count(*) AS rows
  FROM us_premap
 GROUP BY kind, side_norm
 ORDER BY kind, rows DESC
 LIMIT 40;

\echo == 2 IS THE LINE HALF INTEGER OR INTEGER, PER kind ==
-- An integer line can land exactly on the number. `missing_facts` refuses a
-- leg on an integer line unless the push rule was captured, so the split
-- decides how many candidates are even constructible.
SELECT kind,
       count(*) FILTER (WHERE line IS NULL)                      AS no_line,
       count(*) FILTER (WHERE (line * 2) = floor(line * 2)
                          AND line <> floor(line))              AS half_integer,
       count(*) FILTER (WHERE line = floor(line))               AS integer_line
  FROM us_premap
 GROUP BY kind
 ORDER BY kind;

\echo == 3 SIBLING CONTRACTS ON ONE EVENT: THE CANDIDATE LEG SET ==
-- The complementary contracts a hedge would be built from are the other
-- market_slugs under the same event_slug. This is the widest event in the
-- catalogue, so it is the most a candidate set could ever be.
WITH widest AS (
  SELECT event_slug, count(*) AS n
    FROM us_premap
   WHERE event_slug IS NOT NULL
   GROUP BY event_slug
   ORDER BY n DESC
   LIMIT 3)
-- `event_slug` and `team_abbr` are selected because ORIENTATION depends on
-- them: `backs` must name the fixture's FIRST listed participant, and the
-- only ordering authority in the row is the order the two team codes appear
-- in the event slug. If a slug does not carry two codes, the leg refuses.
SELECT w.n AS siblings_on_event,
       left(p.event_slug, 40)   AS event_slug,
       p.team_abbr,
       p.kind, p.side_norm, p.line, p.signed,
       left(p.market_slug, 50)  AS market_slug,
       p.sports_type
  FROM widest w
  JOIN us_premap p USING (event_slug)
 ORDER BY w.n DESC, p.kind, p.side_norm, p.line
 LIMIT 60;

\echo == 4 HOW MANY EVENTS CARRY MORE THAN ONE CONTRACT AT ALL ==
-- A hedge needs a second contract on the same fixture. If nearly every event
-- is a single moneyline pair, the candidate set is structurally thin and that
-- is a fact about the venue, not about the supplier.
SELECT CASE WHEN n = 1 THEN '1'
            WHEN n = 2 THEN '2'
            WHEN n <= 4 THEN '3-4'
            WHEN n <= 8 THEN '5-8'
            ELSE '9+' END                AS contracts_per_event,
       count(*)                          AS events
  FROM (SELECT event_slug, count(*) AS n
          FROM us_premap
         WHERE event_slug IS NOT NULL
         GROUP BY event_slug) t
 GROUP BY 1
 ORDER BY 1;

\echo == 5 THE HELD FUNDED POSITIONS, AND WHETHER THEIR SLUG IS IN us_premap ==
-- The held_leg supplier starts from a funded intent. If the intent slug has
-- no catalogue row, the leg refuses -- so the join is the supplier itself.
SELECT i.intent_id, i.us_market_slug, i.order_intent, i.quantity,
       (p.market_slug IS NOT NULL)   AS catalogue_row_present,
       p.kind, p.side_norm, p.sports_type, p.event_slug
  FROM bettor_funded_intents i
  LEFT JOIN us_premap p ON p.market_slug = i.us_market_slug
 ORDER BY i.intent_id
 LIMIT 30;
