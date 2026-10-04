-- P0 INCIDENT (2026-10-04) segment COLLECTOR SPORT CAP, third read (read-only).
-- WHICH SHARP BOOKS THE METERED REQUEST ACTUALLY DELIVERS TODAY. The collector
-- requests regions=eu,uk,us and reads only Pinnacle plus SHARP_BOOKS
-- (betfair_ex_eu, betfair_ex_uk, betfair_ex_au, smarkets, matchbook,
-- betanysports, lowvig). The PinnAPI-primary valuations record, per outcome,
-- the independent books that corroborated the price (reference_input ->
-- independent_books). The set of book keys seen there over 7 days is the
-- observed subset of SHARP_BOOKS present in today's eu,uk,us responses.
-- Bounded: 7 days, aggregates only.
\echo '== K0 read instant =='
SELECT now() AS read_at;

\echo '== K1 corroborating book keys seen in PinnAPI-primary valuations, 7 d, by family =='
SELECT v.sport_family, b.book, count(*) AS outcome_mentions,
       count(DISTINCT v.id) AS valuations
  FROM external_valuations v,
       jsonb_each(CASE WHEN jsonb_typeof(v.settlement_comparison->'reference_input'->'independent_books') = 'object'
                       THEN v.settlement_comparison->'reference_input'->'independent_books'
                       ELSE '{}'::jsonb END) AS o(label, books),
       jsonb_array_elements_text(CASE WHEN jsonb_typeof(o.books) = 'array' THEN o.books
                                      ELSE '[]'::jsonb END) AS b(book)
 WHERE v.decided_at >= now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo '== K2 outcome_books distribution by provider and family, 7 d =='
SELECT sport_family, provider, outcome_books, count(*) AS n
  FROM external_valuations
 WHERE decided_at >= now() - interval '7 days'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
