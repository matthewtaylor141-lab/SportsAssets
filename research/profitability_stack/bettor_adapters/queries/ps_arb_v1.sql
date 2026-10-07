-- READ-ONLY. PROFITABILITY STACK V1 -- structural-arb census input. For every
-- venue event (registry event_id: the venue's own id, never a label) with >= 2
-- contracts that have recorded books: each 5-minute bucket of the last 5
-- days, every leg's latest book inside the bucket (bid / ask / top qty /
-- observed_at), its structured ontology (metric, period, line, operator,
-- subject, side), its rules-document hash and settlement state. One JSON
-- object per (event, bucket).
WITH reg AS (
  SELECT contract_id, venue, event_id, family, period, settlement_state,
         settlement_evidence->>'rules_sha256' rules_sha, ontology->'meaning' meaning,
         ontology->'sides' sides
    FROM market_plane_registry WHERE active AND event_id IS NOT NULL),
ob AS (
  SELECT DISTINCT ON (o.us_market_slug, floor(extract(epoch FROM o.observed_at)/300))
         o.us_market_slug slug, floor(extract(epoch FROM o.observed_at)/300) bkt, o.observed_at,
         (SELECT max((l->'px'->>'value')::float8) FROM jsonb_array_elements(
             CASE WHEN jsonb_typeof(o.bids)='array' THEN o.bids ELSE '[]' END) l) bid,
         (SELECT min((l->'px'->>'value')::float8) FROM jsonb_array_elements(
             CASE WHEN jsonb_typeof(o.offers)='array' THEN o.offers ELSE '[]' END) l) ask,
         (SELECT (l->>'qty')::float8 FROM jsonb_array_elements(
             CASE WHEN jsonb_typeof(o.bids)='array' THEN o.bids ELSE '[]' END) l
           ORDER BY (l->'px'->>'value')::float8 DESC LIMIT 1) bid_q,
         (SELECT (l->>'qty')::float8 FROM jsonb_array_elements(
             CASE WHEN jsonb_typeof(o.offers)='array' THEN o.offers ELSE '[]' END) l
           ORDER BY (l->'px'->>'value')::float8 LIMIT 1) ask_q
    FROM paper_book_observations o
   WHERE o.error IS NULL AND o.observed_at > now() - interval '5 days'
     AND o.us_market_slug IN (SELECT contract_id FROM reg)
   ORDER BY o.us_market_slug, floor(extract(epoch FROM o.observed_at)/300), o.observed_at DESC),
legs AS (
  SELECT reg.event_id, ob.bkt, json_agg(json_build_object(
           'contract_id', reg.contract_id, 'venue', reg.venue, 'family', reg.family, 'period', reg.period,
           'meaning', reg.meaning, 'sides', reg.sides, 'rules_sha', reg.rules_sha,
           'settlement_state', reg.settlement_state, 'bid', ob.bid, 'ask', ob.ask,
           'bid_q', ob.bid_q, 'ask_q', ob.ask_q, 'at', extract(epoch FROM ob.observed_at))) legs,
         count(*) n, max(ob.observed_at) - min(ob.observed_at) spread
    FROM ob JOIN reg ON reg.contract_id = ob.slug
   GROUP BY reg.event_id, ob.bkt)
SELECT json_build_object('event_id', event_id, 'bucket', bkt * 300, 'n_legs', n,
                         'skew_s', extract(epoch FROM spread), 'legs', legs)
  FROM legs WHERE n >= 2 AND spread <= interval '60 seconds'
 ORDER BY bkt, event_id;
SELECT venue, count(*) contracts, count(DISTINCT event_id) events FROM market_plane_registry
 WHERE active GROUP BY 1;
