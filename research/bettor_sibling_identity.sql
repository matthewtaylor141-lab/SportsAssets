-- THE RETAIL-NATIVE IDENTITY OF ONE PRODUCT'S OUTCOME SET.
--
-- Owner directive 2026-09-19 22:2xZ §1/§2. Two jobs:
--
--   §1 wants the venue to ENUMERATE the institutional sibling set
--       rather than have us assume LAF / SJE / NEITHER. The
--       institutional refdata endpoint filters by `symbols` and
--       nothing else (no product filter is documented, and paging the
--       whole catalogue at ListInstruments 6/min is not a read we can
--       take). So the CANDIDATE list is enumerated by the OTHER
--       venue -- every market the retail board actually lists under
--       this product -- and institutional refdata is then asked about
--       exactly those symbols. The venue confirms or denies each; we
--       invent no outcome code.
--
--   §2 wants the retail contract identified NATIVELY: the YES token,
--       the NO token, the question and the settlement wording. Those
--       live on us_premap, whose `identifier` IS the retail venue's
--       own key for the thing traded -- the only retail field that is
--       an identifier rather than a description.
--
-- THE PRODUCT UNDER TEST, from the institutional record
-- (run 35472190984): productId astatc-mls-sje-laf-2026-09-19-sh-ftts.

-- THE PRODUCT IS INLINED AS A LITERAL, not a \set variable: the
-- research runner refuses every psql meta-command but \echo, and that
-- guard is right -- a file that can set variables can set more than a
-- product name. Inlining costs a few repeats and keeps the guard whole.

\echo '--- 1. EVERY RETAIL ROW UNDER THIS PRODUCT (the candidate set) ---'
SELECT 'sib|' || p.identifier
       || '|slug=' || coalesce(p.market_slug, 'NULL')
       || '|side=' || coalesce(p.side_norm, 'NULL')
       || '|intent=' || coalesce(p.intent, 'NULL')
       || '|kind=' || coalesce(p.kind, 'NULL')
       || '|line=' || coalesce(p.line, 'NULL')
  FROM us_premap p
 WHERE p.identifier LIKE 'astatc-mls-sje-laf-2026-09-19-sh-ftts%'
    OR p.market_slug LIKE 'astatc-mls-sje-laf-2026-09-19-sh-ftts%'
 ORDER BY p.identifier, p.side_norm;

\echo ''
\echo '--- 2. THE QUESTION AND EVENT, AS THE RETAIL VENUE WORDS THEM ---'
SELECT 'q|' || p.identifier
       || '|event=' || coalesce(p.event_slug, 'NULL')
       || '|title=' || coalesce(left(p.event_title, 60), 'NULL')
       || '|question=' || coalesce(left(p.question, 130), 'NULL')
  FROM us_premap p
 WHERE p.identifier LIKE 'astatc-mls-sje-laf-2026-09-19-sh-ftts%'
    OR p.market_slug LIKE 'astatc-mls-sje-laf-2026-09-19-sh-ftts%'
 ORDER BY p.identifier
 LIMIT 12;

\echo ''
\echo '--- 3. WHAT THE COLLECTOR HAS OBSERVED ON THESE SYMBOLS ---'
SELECT 'obs|' || o.symbol
       || '|leg=' || coalesce(o.outcome_leg, 'NONE')
       || '|n=' || count(*)
       || '|last_bid=' || coalesce(max(o.microstructure ->> 'bid'), 'NULL')
       || '|last_ask=' || coalesce(max(o.microstructure ->> 'ask'), 'NULL')
       || '|newest=' || to_char(max(o.observed_at), 'HH24:MI:SSZ')
  FROM bettor_opportunities o
 WHERE o.symbol LIKE 'astatc-mls-sje-laf-2026-09-19-sh-ftts%'
 GROUP BY o.symbol, o.outcome_leg
 ORDER BY o.symbol, o.outcome_leg;

\echo ''
\echo '--- 4. DOES THE RETAIL BOARD CARRY A SIBLING OUTCOME AT ALL? ---'
-- If the retail venue lists only ONE row for this product while the
-- institutional venue models a mutually exclusive SET, the two are
-- representing the market differently and that is the finding.
SELECT 'shape|retail_rows=' || count(*)
       || '|distinct_identifiers=' || count(DISTINCT p.identifier)
       || '|distinct_sides=' || count(DISTINCT p.side_norm)
  FROM us_premap p
 WHERE p.identifier LIKE 'astatc-mls-sje-laf-2026-09-19-sh-ftts%'
    OR p.market_slug LIKE 'astatc-mls-sje-laf-2026-09-19-sh-ftts%';

\echo ''
\echo '--- 5. THE SAME SHAPE FOR THE WHOLE EVENT (context) ---'
SELECT 'event|' || coalesce(split_part(p.identifier, '-', 1), '?')
       || '|' || count(*)
  FROM us_premap p
 WHERE p.identifier LIKE 'astatc-mls-sje-laf-2026-09-19%'
 GROUP BY split_part(p.identifier, '-', 1)
 ORDER BY count(*) DESC
 LIMIT 10;
