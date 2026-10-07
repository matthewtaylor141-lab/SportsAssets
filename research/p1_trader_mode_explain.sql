-- READ-ONLY. EXPLAIN ANALYZE of the Trader Mode read model's query and its game-state read (developer pass). SELECT only.
SELECT count(*) AS slug_differs FROM us_premap WHERE identifier IS DISTINCT FROM market_slug;
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)

WITH open_positions AS (

    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                       AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                       AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9

), scoped AS (
 SELECT p.*, 'paperpos:'||p.account_id||':'||p.group_id||':'||
        p.us_market_slug||':'||p.holding_side AS position_id,
        count(*) OVER() AS all_open_count,
        count(*) OVER(PARTITION BY p.account_id,p.group_id) AS group_open_count
 FROM open_positions p WHERE p.account_id='paper_acct_main'
), selected AS (
 SELECT * FROM scoped ORDER BY group_id,us_market_slug,holding_side LIMIT 1000
)
SELECT p.*, to_jsonb(ent) AS entry, to_jsonb(pm) AS premap,
       to_jsonb(rev) AS review, to_jsonb(bk) AS book,
       to_jsonb(sett) AS settlement,
       (SELECT coalesce(jsonb_agg(to_jsonb(f) ORDER BY f.filled_at,f.fill_id),'[]'::jsonb)
          FROM paper_fills f WHERE f.account_id=p.account_id
           AND f.group_id=p.group_id AND f.us_market_slug=p.us_market_slug
           AND f.holding_side=p.holding_side) AS fills,
       (SELECT coalesce(jsonb_agg(to_jsonb(o) ORDER BY o.created_at,o.order_id),'[]'::jsonb)
          FROM paper_orders o WHERE o.account_id=p.account_id
           AND o.group_id=p.group_id AND o.us_market_slug=p.us_market_slug
           AND o.holding_side=p.holding_side
           AND o.role IN ('STANDING_PROTECTION','EXIT','REDUCE')
           AND o.state IN ('RESTING','PARTIALLY_FILLED','CANCEL_PENDING','PENDING_SIMULATION')) AS orders,
       (SELECT coalesce(jsonb_agg(to_jsonb(h) ORDER BY h.observed_at),'[]'::jsonb) FROM
          (SELECT b.obs_id,b.observed_at,b.bids,b.offers FROM paper_book_observations b
            WHERE b.us_market_slug=p.us_market_slug AND b.error IS NULL
            ORDER BY b.observed_at DESC,b.obs_id DESC LIMIT 32) h) AS book_history
FROM selected p
LEFT JOIN LATERAL (SELECT o.* FROM paper_orders o WHERE o.account_id=p.account_id
  AND o.group_id=p.group_id AND o.us_market_slug=p.us_market_slug
  AND o.holding_side=p.holding_side AND o.role='ENTRY'
  ORDER BY o.created_at,o.order_id LIMIT 1) ent ON true
LEFT JOIN LATERAL (SELECT u.* FROM us_premap u WHERE u.market_slug=p.us_market_slug
  ORDER BY u.updated_at DESC,u.event_slug LIMIT 1) pm ON true
LEFT JOIN LATERAL (SELECT r.* FROM paper_xavier_reviews r
  WHERE r.account_id=p.account_id AND r.group_id=p.group_id AND r.reviewed_at<=to_timestamp(extract(epoch from now()))
  AND (r.selection #>> '{management_packet,position,position_key}'=p.position_id
       OR (p.group_open_count=1 AND
           r.selection #>> '{management_packet,position,position_key}' IS NULL))
  ORDER BY r.reviewed_at DESC,r.review_id DESC LIMIT 1) rev ON true
LEFT JOIN LATERAL (SELECT b.* FROM paper_book_observations b
  WHERE b.us_market_slug=p.us_market_slug AND b.error IS NULL
  ORDER BY b.observed_at DESC,b.obs_id DESC LIMIT 1) bk ON true
LEFT JOIN LATERAL (SELECT s.* FROM paper_settlements s
  WHERE s.position_key=p.position_id ORDER BY s.version DESC LIMIT 1) sett ON true
ORDER BY p.group_id,p.us_market_slug,p.holding_side

;
EXPLAIN (ANALYZE, BUFFERS) SELECT DISTINCT ON (contract_id) contract_id,payload FROM market_plane_events WHERE kind='TRADER_GAME_STATE' AND contract_id=ANY(ARRAY['game:x']) ORDER BY contract_id,at DESC,event_key DESC;
