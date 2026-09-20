-- §B. CAN BETTOR'S MARKET SYMBOLS BE JOINED TO THE TAPE DETERMINISTICALLY?
--
-- The tape's Symbol column carries strings like
-- 'aec-mlb-min-laa-2026-09-18' and
-- 'aec-setkameua-fasiva-malden-2026-09-18'. us_premap.market_slug is
-- the venue's own market slug. If they are the same namespace, the
-- join is an equality on a venue-native key -- no fuzzy title
-- matching, no team-name matching, no price matching.
--
-- THE LITERALS BELOW ARE OBSERVED, NOT INVENTED. They are the ten
-- busiest symbols in the fetched 20260919 tape file, taken from
-- tape_probe's topSymbolsByRows. Testing on the busiest markets rather
-- than on whichever names sort first is deliberate: a join that works
-- only on quiet markets is not a join we can use.
--
-- READ-ONLY.

WITH tape_symbols(symbol, tape_rows) AS (
    VALUES ('aec-setkameua-fasiva-malden-2026-09-18', 2478),
           ('aec-setkameua-sydole-paldmy-2026-09-18', 2344),
           ('aec-setkamemd-leviur-filmih-2026-09-19', 2341),
           ('aec-itfme-vicbin-livdam-2026-09-18', 2258),
           ('aec-wtadb-gleasopiter-koboriplipue-2026-09-18', 2250),
           ('aec-mlb-min-laa-2026-09-18', 2205),
           ('aec-setkameua-budole-malden-2026-09-18', 2118)
)
SELECT 'TAPE_JOIN_EXACT' AS section,
       t.symbol,
       t.tape_rows,
       count(p.market_slug)                      AS premap_rows,
       count(DISTINCT p.side_norm)               AS sides,
       max(p.team_league)                        AS team_league,
       max(p.sports_type)                        AS sports_type
  FROM tape_symbols t
  LEFT JOIN us_premap p ON p.market_slug = t.symbol
 GROUP BY 1, 2, 3
 ORDER BY t.tape_rows DESC;

-- The headline: did every observed tape symbol find a premap row?
WITH tape_symbols(symbol) AS (
    VALUES ('aec-setkameua-fasiva-malden-2026-09-18'),
           ('aec-setkameua-sydole-paldmy-2026-09-18'),
           ('aec-setkamemd-leviur-filmih-2026-09-19'),
           ('aec-itfme-vicbin-livdam-2026-09-18'),
           ('aec-wtadb-gleasopiter-koboriplipue-2026-09-18'),
           ('aec-mlb-min-laa-2026-09-18'),
           ('aec-setkameua-budole-malden-2026-09-18')
)
SELECT 'TAPE_JOIN_SUMMARY' AS section,
       count(*)                                          AS tape_symbols,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM us_premap p
            WHERE p.market_slug = t.symbol))              AS matched_premap,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM bettor_opportunities o
            WHERE o.symbol = t.symbol))                   AS matched_bettor
  FROM tape_symbols t;

-- And the reverse direction, which is the one that matters for
-- labelling: of the markets BETTOR actually observed, what share are
-- in a namespace the tape could carry? Measured by kind prefix,
-- because the tape file itself is not in the database.
SELECT 'BETTOR_SYMBOL_SHAPE' AS section,
       split_part(o.symbol, '-', 1)               AS kind,
       count(DISTINCT o.symbol)                   AS distinct_markets,
       count(*)                                   AS observations
  FROM bettor_opportunities o
 GROUP BY 1, 2
 ORDER BY observations DESC
 LIMIT 20;
