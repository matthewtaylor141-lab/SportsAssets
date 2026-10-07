-- READ-ONLY. Sizing for the Trader Mode read model (developer pass): row
-- counts, sizes and indexes of the tables its query and the score writer
-- read. Every statement is a SELECT.
SELECT relname, n_live_tup, pg_size_pretty(pg_total_relation_size(relid)) total
  FROM pg_stat_user_tables
 WHERE relname IN ('market_plane_events','paper_fills','paper_orders',
                   'paper_book_observations','paper_xavier_reviews',
                   'paper_settlements','us_premap')
 ORDER BY n_live_tup DESC;
SELECT tablename, indexdef FROM pg_indexes
 WHERE tablename IN ('market_plane_events','paper_xavier_reviews','paper_book_observations',
                     'paper_fills','paper_settlements','us_premap')
 ORDER BY 1;
SELECT kind, count(*) FROM market_plane_events GROUP BY 1 ORDER BY 2 DESC;
SELECT count(*) FROM paper_xavier_reviews;
