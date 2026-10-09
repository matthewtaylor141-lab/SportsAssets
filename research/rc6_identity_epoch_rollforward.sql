-- READ-ONLY. RC6 lane identity-debt (d): the $500,000 MANAGEMENT EPOCH's exact
-- roll-forward inputs, exported for backend/tools/epoch_rollforward_receipt.py
-- (stdlib only; it replays the append-only paper ledger and feeds the same
-- rows to bettor_paper_epoch.management_book). SELECT only.
--
-- ONE statement, one text column `line`, every row `EPOCHRF|<section>|<chunk>|
-- <json>`: the manifest first (row counts the tool checks, so a truncated
-- output is refused, never read as a smaller ledger), then the paper_ledger
-- rows (every one, for the replay), the fills as bettor_paper_epoch.FILLS_SQL
-- reads them, every settlement version (with only the outcome-read instants
-- of its evidence), the books at the epoch for each position open at it, and
-- the latest book for each position open now. Amounts and instants are TEXT
-- (exact decimals; the tool never reads a float).
--
-- THE TWO CONSTANTS are in `params`: the epoch (2026-10-05 00:00
-- America/New_York) and the account. The capital-critical test runs this very
-- file against a real Postgres fixture with only those two literals replaced.
WITH params AS (
  SELECT timestamptz '2026-10-05 04:00:00+00' AS epoch,
         'paper_acct_main'::text AS acct,
         300::numeric AS mark_max_age_s
), lg AS (
  SELECT l.seq, l.kind, l.cash_delta_usd, l.reserved_delta_usd, l.cash_after_usd,
         l.reserved_after_usd, l.committed_at, l.fill_id, l.position_key,
         l.settlement_key, l.corrects_seq
    FROM paper_ledger l, params p WHERE l.account_id = p.acct
), fl AS (
  SELECT f.fill_id, f.group_id, f.us_market_slug, f.holding_side, f.direction, f.qty,
         f.gross_usd, f.fee_usd, coalesce(l.committed_at, f.recorded_at) AS at, l.seq,
         f.strategy,
         'paperpos:' || f.account_id || ':' || f.group_id || ':' || f.us_market_slug
           || ':' || f.holding_side AS pk
    FROM paper_fills f
    LEFT JOIN paper_ledger l ON l.fill_id = f.fill_id AND l.kind IN ('FILL', 'SALE')
    JOIN params p ON f.account_id = p.acct
), st AS (
  SELECT s.position_key, s.settlement_id, s.version, s.qty, s.outcome,
         s.payout_per_contract, s.payout_usd, s.settled_at,
         jsonb_build_object(
           'rows', coalesce((SELECT jsonb_agg(jsonb_build_object('outcome_at', r -> 'outcome_at'))
                               FROM jsonb_array_elements(CASE WHEN jsonb_typeof(s.evidence -> 'rows') = 'array'
                                                              THEN s.evidence -> 'rows' ELSE '[]'::jsonb END) r),
                            '[]'::jsonb),
           'evidence', coalesce((SELECT jsonb_agg(jsonb_build_object('settlement_read_at', r -> 'settlement_read_at'))
                                   FROM jsonb_array_elements(CASE WHEN jsonb_typeof(s.evidence -> 'evidence') = 'array'
                                                                  THEN s.evidence -> 'evidence' ELSE '[]'::jsonb END) r),
                                '[]'::jsonb)) AS ev
    FROM paper_settlements s, params p WHERE s.account_id = p.acct
), sq AS (
  SELECT DISTINCT ON (position_key) position_key, qty FROM st
   ORDER BY position_key, version DESC
), held_e AS (
  -- positions open at the epoch, as bettor_paper_epoch.read derives them
  SELECT pk, max(us_market_slug) AS slug, max(holding_side) AS side, sum(q) AS q
    FROM (SELECT fl.pk, fl.us_market_slug, fl.holding_side,
                 CASE WHEN fl.direction = 'BUY' THEN fl.qty ELSE -fl.qty END AS q
            FROM fl, params p WHERE fl.at < p.epoch
          UNION ALL
          SELECT lg.position_key, NULL, NULL, -coalesce(sq.qty, 0)
            FROM lg JOIN sq ON sq.position_key = lg.position_key, params p
           WHERE lg.kind = 'SETTLEMENT' AND lg.committed_at < p.epoch) x
   GROUP BY pk HAVING sum(q) > 0.000001
), held_now AS (
  SELECT fl.pk, max(fl.us_market_slug) AS slug
    FROM fl LEFT JOIN sq ON sq.position_key = fl.pk
   GROUP BY fl.pk
  HAVING sum(CASE WHEN fl.direction = 'BUY' THEN fl.qty ELSE -fl.qty END)
         - max(coalesce(sq.qty, 0)) > 0.000001
), eslugs AS (SELECT DISTINCT slug FROM held_e WHERE slug IS NOT NULL),
nslugs AS (SELECT DISTINCT slug FROM held_now WHERE slug IS NOT NULL),
ebooks AS (
  SELECT e.slug, jsonb_build_object(
    'slug', e.slug,
    'window', (SELECT jsonb_build_object('obs_id', o.obs_id,
                        'observed_at', extract(epoch FROM o.observed_at)::text,
                        'bids', o.bids, 'offers', o.offers, 'market_state', o.market_state)
                 FROM paper_book_observations o, params p
                WHERE o.us_market_slug = e.slug AND o.error IS NULL
                  AND o.observed_at <= p.epoch
                  AND o.observed_at >= p.epoch - make_interval(secs => p.mark_max_age_s::float8)
                ORDER BY o.observed_at DESC LIMIT 1),
    'last', (SELECT jsonb_build_object('obs_id', o.obs_id,
                      'observed_at', extract(epoch FROM o.observed_at)::text,
                      'market_state', o.market_state)
               FROM paper_book_observations o, params p
              WHERE o.us_market_slug = e.slug AND o.error IS NULL AND o.observed_at <= p.epoch
              ORDER BY o.observed_at DESC LIMIT 1),
    'reads', (SELECT jsonb_build_object('total', count(*),
                       'failed', count(*) FILTER (WHERE o.error IS NOT NULL),
                       'failed_example', min(o.error))
                FROM paper_book_observations o, params p
               WHERE o.us_market_slug = e.slug AND o.observed_at <= p.epoch
                 AND o.observed_at >= p.epoch - make_interval(secs => p.mark_max_age_s::float8)),
    'after', (SELECT jsonb_build_object('obs_id', o.obs_id,
                       'observed_at', extract(epoch FROM o.observed_at)::text)
                FROM paper_book_observations o, params p
               WHERE o.us_market_slug = e.slug AND o.error IS NULL AND o.observed_at > p.epoch
               ORDER BY o.observed_at ASC LIMIT 1)) AS j
    FROM eslugs e
), nbooks AS (
  SELECT n.slug, jsonb_build_object(
    'slug', n.slug,
    'latest', (SELECT jsonb_build_object('obs_id', o.obs_id,
                        'observed_at', extract(epoch FROM o.observed_at)::text,
                        'bids', o.bids, 'offers', o.offers)
                 FROM paper_book_observations o
                WHERE o.us_market_slug = n.slug AND o.error IS NULL
                ORDER BY o.observed_at DESC LIMIT 1)) AS j
    FROM nslugs n
), out AS (
  SELECT 0 AS so, 0 AS chunk, 'manifest' AS sect, jsonb_build_object(
           'version', 'RC6_IDENTITY_EPOCH_ROLLFORWARD_EXPORT_V1',
           'account_id', (SELECT acct FROM params),
           'epoch_at', (SELECT extract(epoch FROM epoch)::text FROM params),
           'mark_max_age_s', (SELECT mark_max_age_s::text FROM params),
           'db_now', extract(epoch FROM now())::text,
           'ledger_rows', (SELECT count(*) FROM lg),
           'fill_rows', (SELECT count(*) FROM fl),
           'settlement_rows', (SELECT count(*) FROM st),
           'epoch_book_slugs', (SELECT count(*) FROM eslugs),
           'now_book_slugs', (SELECT count(*) FROM nslugs))::text AS j
  UNION ALL
  SELECT 1, c, 'ledger', jsonb_agg(r ORDER BY seq)::text FROM (
    SELECT (row_number() OVER (ORDER BY seq) - 1) / 60 AS c, seq,
           jsonb_build_array(seq, kind, cash_delta_usd::text, reserved_delta_usd::text,
                             cash_after_usd::text, reserved_after_usd::text,
                             extract(epoch FROM committed_at)::text, fill_id, position_key,
                             settlement_key, corrects_seq) AS r
      FROM lg) x GROUP BY c
  UNION ALL
  SELECT 2, c, 'fills', jsonb_agg(r ORDER BY fill_id)::text FROM (
    SELECT (row_number() OVER (ORDER BY fill_id, seq) - 1) / 60 AS c, fill_id,
           jsonb_build_array(fill_id, group_id, us_market_slug, holding_side, direction,
                             qty::text, gross_usd::text, fee_usd::text,
                             extract(epoch FROM at)::text, seq, strategy) AS r
      FROM fl) x GROUP BY c
  UNION ALL
  SELECT 3, c, 'settlements', jsonb_agg(r ORDER BY settlement_id)::text FROM (
    SELECT (row_number() OVER (ORDER BY settlement_id) - 1) / 60 AS c, settlement_id,
           jsonb_build_array(position_key, settlement_id, version, qty::text, outcome,
                             payout_per_contract::text, payout_usd::text,
                             extract(epoch FROM settled_at)::text, ev) AS r
      FROM st) x GROUP BY c
  UNION ALL
  SELECT 4, c, 'epochbooks', jsonb_agg(j ORDER BY slug)::text FROM (
    SELECT (row_number() OVER (ORDER BY slug) - 1) / 20 AS c, slug, j FROM ebooks) x
   GROUP BY c
  UNION ALL
  SELECT 5, c, 'nowbooks', jsonb_agg(j ORDER BY slug)::text FROM (
    SELECT (row_number() OVER (ORDER BY slug) - 1) / 20 AS c, slug, j FROM nbooks) x
   GROUP BY c
)
SELECT 'EPOCHRF|' || sect || '|' || chunk || '|' || j AS line FROM out ORDER BY so, chunk;
