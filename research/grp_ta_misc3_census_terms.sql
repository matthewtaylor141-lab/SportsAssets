-- READ-ONLY. Root-cause audit, group truth-agents, part 6: (a) what the
-- recorded-books census established while it still read contracts (its
-- void-terms summary on the last OK scans), (b) the age of the events behind
-- the funded-ledger slugs mirror_shadow plans on. SELECT only.

\echo == 1 census scans that read contracts: the void-terms summary recorded on each (newest 8 OK scans)
SELECT scan_id, started_at, markets_read, books_fresh,
       by_code -> 'void_terms' ->> 'contracts' AS vt_contracts,
       by_code -> 'void_terms' ->> 'established' AS vt_established,
       by_code -> 'void_terms' -> 'not_established' AS vt_not_established,
       by_code -> 'void_terms' -> 'rules' AS vt_rules
  FROM adriana_arb_scans WHERE scan_id LIKE 'adr-scan-%' AND status = 'OK' AND markets_read > 0
 ORDER BY started_at DESC LIMIT 8;

\echo == 2 the same summary aggregated over the OK census scans of the last 48 h
SELECT count(*) AS ok_scans, min(started_at) AS first_ok, max(started_at) AS last_ok,
       sum(CASE WHEN (by_code -> 'void_terms' ->> 'established')::int = (by_code -> 'void_terms' ->> 'contracts')::int THEN 1 ELSE 0 END) AS scans_with_all_established,
       sum(CASE WHEN (by_code -> 'void_terms' ->> 'established')::int < (by_code -> 'void_terms' ->> 'contracts')::int THEN 1 ELSE 0 END) AS scans_with_some_not_established
  FROM adriana_arb_scans
 WHERE scan_id LIKE 'adr-scan-%' AND status = 'OK' AND markets_read > 0 AND started_at > now() - interval '48 hours'
   AND by_code -> 'void_terms' IS NOT NULL;

\echo == 3 funded-ledger slugs the shadow plans on: the game start behind each, from the premap
SELECT count(*) AS held_slugs,
       count(*) FILTER (WHERE gs IS NULL) AS no_premap_game_start,
       count(*) FILTER (WHERE gs < now() - interval '30 days') AS started_over_30_days_ago,
       count(*) FILTER (WHERE gs >= now() - interval '30 days' AND gs < now() - interval '2 days') AS started_2_to_30_days_ago,
       count(*) FILTER (WHERE gs >= now() - interval '2 days') AS started_within_2_days_or_future,
       min(gs) AS oldest_start, max(gs) AS newest_start
  FROM (SELECT h.slug, (SELECT min(p.game_start) FROM us_premap p WHERE p.market_slug = h.slug) AS gs
          FROM (SELECT lower(btrim(us_market_slug)) AS slug FROM live_orders
                 WHERE venue = 'polymarket-us' AND status IN ('filled', 'exiting') AND us_market_slug IS NOT NULL GROUP BY 1) h) x;

\echo == 4 the same held slugs: any paper settlement or paper fill row for the slug (a resolution trace)
SELECT count(*) AS held_slugs,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_settlements s WHERE s.us_market_slug = h.slug)) AS with_paper_settlement,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_fills f WHERE f.us_market_slug = h.slug)) AS with_paper_fill
  FROM (SELECT lower(btrim(us_market_slug)) AS slug FROM live_orders
         WHERE venue = 'polymarket-us' AND status IN ('filled', 'exiting') AND us_market_slug IS NOT NULL GROUP BY 1) h;
