-- READ-ONLY. THE FULL STORED settlement_comparison FOR ONE MLB AND ONE
-- SOCCER VALUATION THE PINNACLE_ONLY_PAPER_BENCHMARK REFUSED
-- (compatibility UNKNOWN on both). Nothing here writes.

\echo '== C1 · MLB valuation 1901 (fixture read) =='
SELECT jsonb_pretty(settlement_comparison) FROM external_valuations WHERE id = 1901;

\echo '== C2 · soccer valuation 1904 (no fixture key) =='
SELECT jsonb_pretty(settlement_comparison) FROM external_valuations WHERE id = 1904;

\echo '== C3 · the newest fixture_metadata rows for the two MLB games =='
SELECT condition_id, game_pk, left(row_to_json(f)::text, 1500) AS row
  FROM fixture_metadata f
 WHERE game_pk IN (849840, 849844)
 ORDER BY game_pk;
