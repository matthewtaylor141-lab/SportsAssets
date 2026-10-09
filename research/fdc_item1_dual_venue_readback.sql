-- READ-ONLY. FINAL DELIVERY CONTRACT item 1 (dual-venue decision) and the
-- Kalshi half of item 5: what production holds today for a Kalshi x
-- Polymarket US venue comparison. Every statement is a SELECT. Sections:
--   V1 Kalshi registry ontology: who wrote the rows (ontology version present
--      or not), mapped vs gap, top gap codes, mapped families
--   V2 Kalshi fixtures: fixture mapping and PMUS counterpart mapping status
--   V3 canonical claim aliases: by venue and status; claims carried by BOTH
--      venues (settlement-equivalent cross-venue claims) and how many of
--      those have a book on both sides observed in the last 30 s / 5 min
--   V4 canonical route receipts (SHADOW, production_effect NONE): 24 h
--      volume, refusals, chosen topology and venue of the best single route,
--      receipts where both venues had an eligible alias
--   V5 Kalshi fee terms captured
--   V6 Adriana claim scans and recorded opportunities (SHADOW)
--   V7 PAPER decisions in 24 h: all keyed by a PMUS slug; the Derek
--      not-PMUS refusal count
--   V8 Kalshi execution state: control row, reconciliations, intents, fills
\echo V1a KALSHI REGISTRY rows by writer (ontology version stamped or not), active, mapped
SELECT coalesce(ontology->'meaning'->>'ontology_version', 'NO_ONTOLOGY_VERSION') AS ontology_version,
       active,
       (jsonb_array_length(coalesce(ontology->'gaps', '[]'::jsonb)) = 0) AS no_gaps,
       count(*) AS rows_n, min(updated_at) AS oldest_write, max(updated_at) AS newest_write
  FROM market_plane_registry WHERE venue = 'KALSHI'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo V1b KALSHI REGISTRY active rows: top first gap codes
SELECT coalesce(ontology->'gaps'->>0, 'NONE') AS first_gap, count(*) AS rows_n
  FROM market_plane_registry WHERE venue = 'KALSHI' AND active
 GROUP BY 1 ORDER BY 2 DESC LIMIT 15;

\echo V1c KALSHI REGISTRY active mapped rows (no gaps) by sport, family, period
SELECT sport, family, period, count(*) AS rows_n
  FROM market_plane_registry
 WHERE venue = 'KALSHI' AND active
   AND jsonb_array_length(coalesce(ontology->'gaps', '[]'::jsonb)) = 0
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 25;

\echo V1d KALSHI REGISTRY active rows by coverage_state and settlement_state
SELECT coverage_state, settlement_state, count(*) AS rows_n
  FROM market_plane_registry WHERE venue = 'KALSHI' AND active
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15;

\echo V2a KALSHI FIXTURES by fixture mapping and PMUS counterpart mapping (all rows, and starting within -4 h .. +36 h)
SELECT mapping_status, coalesce(pmus_mapping_status, 'NULL') AS pmus_mapping_status,
       count(*) AS fixtures,
       count(*) FILTER (WHERE start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours') AS in_window,
       count(*) FILTER (WHERE pmus_slug IS NOT NULL) AS with_pmus_slug,
       max(updated_at) AS newest_write
  FROM kalshi_fixtures_current GROUP BY 1, 2 ORDER BY 3 DESC;

\echo V2b KALSHI FIXTURES by sport: fixture ESTABLISHED and PMUS ESTABLISHED (in window)
SELECT coalesce(sport, 'NULL') AS sport, count(*) AS fixtures,
       count(*) FILTER (WHERE mapping_status = 'ESTABLISHED') AS fixture_established,
       count(*) FILTER (WHERE pmus_mapping_status = 'ESTABLISHED') AS pmus_established
  FROM kalshi_fixtures_current
 WHERE start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 20;

\echo V2c KALSHI FIXTURES top PMUS-not-established first reasons (in window)
SELECT coalesce(pmus_mapping_reasons->>0, 'NONE') AS first_reason, count(*) AS fixtures
  FROM kalshi_fixtures_current
 WHERE start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'
   AND coalesce(pmus_mapping_status, '') <> 'ESTABLISHED'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 12;

\echo V3a CANONICAL CLAIM ALIASES by venue, mapping, settlement status (all rows and observed in 30 s / 5 min)
SELECT venue, mapping_status, settlement_status, count(*) AS aliases,
       count(*) FILTER (WHERE claim_fingerprint IS NOT NULL) AS with_claim,
       count(*) FILTER (WHERE observed_at >= now() - interval '30 seconds') AS book_30s,
       count(*) FILTER (WHERE observed_at >= now() - interval '5 minutes') AS book_5m,
       max(updated_at) AS newest_write
  FROM canonical_claim_aliases GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 20;

\echo V3b CROSS-VENUE CLAIMS: fingerprints carried by a KALSHI alias AND a POLYMARKET_US alias
WITH c AS (
  SELECT claim_fingerprint,
         bool_or(venue = 'KALSHI') AS has_k, bool_or(venue = 'POLYMARKET_US') AS has_p,
         bool_or(venue = 'KALSHI' AND observed_at >= now() - interval '30 seconds') AS k30,
         bool_or(venue = 'POLYMARKET_US' AND observed_at >= now() - interval '30 seconds') AS p30,
         bool_or(venue = 'KALSHI' AND observed_at >= now() - interval '5 minutes') AS k5,
         bool_or(venue = 'POLYMARKET_US' AND observed_at >= now() - interval '5 minutes') AS p5,
         max(updated_at) AS newest
    FROM canonical_claim_aliases WHERE claim_fingerprint IS NOT NULL
   GROUP BY 1)
SELECT count(*) AS claims,
       count(*) FILTER (WHERE has_k AND has_p) AS cross_venue_claims,
       count(*) FILTER (WHERE has_k AND NOT has_p) AS kalshi_only,
       count(*) FILTER (WHERE has_p AND NOT has_k) AS pmus_only,
       count(*) FILTER (WHERE has_k AND has_p AND k30 AND p30) AS cross_both_books_30s,
       count(*) FILTER (WHERE has_k AND has_p AND k5 AND p5) AS cross_both_books_5m,
       count(*) FILTER (WHERE has_k AND has_p AND newest >= now() - interval '1 hour') AS cross_written_1h
  FROM c;

\echo V3c CROSS-VENUE CLAIMS newest 8: event, aliases per venue, best ask per venue, book ages
WITH x AS (
  SELECT claim_fingerprint FROM canonical_claim_aliases WHERE claim_fingerprint IS NOT NULL
   GROUP BY 1 HAVING bool_or(venue = 'KALSHI') AND bool_or(venue = 'POLYMARKET_US'))
SELECT left(a.claim_fingerprint, 12) AS claim, a.event_key, a.venue, a.market_id, a.side, a.subject,
       a.best_ask, a.depth, a.book_basis,
       round(extract(epoch FROM now() - a.observed_at)::numeric, 1) AS book_age_s, a.updated_at
  FROM canonical_claim_aliases a JOIN x USING (claim_fingerprint)
 ORDER BY a.updated_at DESC LIMIT 16;

\echo V4a CANONICAL ROUTE RECEIPTS 24 h: volume, refusal, chosen topology
SELECT coalesce(refusal, 'CHOSEN') AS outcome, coalesce(chosen->>'topology', '-') AS topology,
       count(*) AS receipts, count(DISTINCT claim_fingerprint) AS claims,
       min(computed_at) AS first_at, max(computed_at) AS last_at
  FROM canonical_route_receipts WHERE computed_at >= now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC;

\echo V4b ROUTE RECEIPTS 24 h: venue of the best single route, and receipts where BOTH venues had an eligible alias
WITH r AS (
  SELECT receipt_id, best_single->>'venue' AS best_venue, runner_up->>'venue' AS runner_venue,
         (SELECT bool_or(e->>'venue' = 'KALSHI' AND (e->>'eligible')::boolean) FROM jsonb_array_elements(candidates) e) AS k_ok,
         (SELECT bool_or(e->>'venue' = 'POLYMARKET_US' AND (e->>'eligible')::boolean) FROM jsonb_array_elements(candidates) e) AS p_ok,
         (SELECT bool_or(e->>'venue' = 'POLYMARKET_US') FROM jsonb_array_elements(candidates) e) AS p_any
    FROM canonical_route_receipts WHERE computed_at >= now() - interval '24 hours')
SELECT coalesce(best_venue, 'NONE') AS best_single_venue, coalesce(runner_venue, 'NONE') AS runner_up_venue,
       count(*) AS receipts,
       count(*) FILTER (WHERE k_ok AND p_ok) AS both_venues_eligible,
       count(*) FILTER (WHERE p_any) AS pmus_alias_present,
       count(*) FILTER (WHERE p_any AND NOT coalesce(p_ok, false)) AS pmus_present_not_eligible
  FROM r GROUP BY 1, 2 ORDER BY 3 DESC;

\echo V4c ROUTE RECEIPTS 24 h: why POLYMARKET_US and KALSHI candidates were ineligible
SELECT e->>'venue' AS venue, coalesce(e->>'reason', '-') AS reason, count(*) AS candidates
  FROM canonical_route_receipts r, jsonb_array_elements(r.candidates) e
 WHERE r.computed_at >= now() - interval '24 hours' AND NOT coalesce((e->>'eligible')::boolean, false)
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15;

\echo V4d ROUTE RECEIPTS newest 6 with both venues eligible (if any): chosen, best, runner up
WITH r AS (
  SELECT *,
         (SELECT bool_or(e->>'venue' = 'KALSHI' AND (e->>'eligible')::boolean) FROM jsonb_array_elements(candidates) e) AS k_ok,
         (SELECT bool_or(e->>'venue' = 'POLYMARKET_US' AND (e->>'eligible')::boolean) FROM jsonb_array_elements(candidates) e) AS p_ok
    FROM canonical_route_receipts WHERE computed_at >= now() - interval '7 days')
SELECT receipt_id, computed_at, event_key, qty, chosen->>'topology' AS topology,
       chosen->>'all_in' AS chosen_all_in, best_single->>'venue' AS best_venue,
       best_single->>'all_in' AS best_all_in, runner_up->>'venue' AS runner_venue,
       runner_up->>'all_in' AS runner_all_in
  FROM r WHERE k_ok AND p_ok ORDER BY computed_at DESC LIMIT 6;

\echo V4e ROUTE RECEIPTS all time: count and newest, mode and production_effect
SELECT mode, production_effect, count(*) AS receipts, max(computed_at) AS newest
  FROM canonical_route_receipts GROUP BY 1, 2;

\echo V5 KALSHI FEE TERMS captured by kind and fee type
SELECT kind, coalesce(fee_type, 'NULL') AS fee_type, count(*) AS terms,
       count(DISTINCT series_ticker) AS series, min(fee_multiplier) AS min_mult,
       max(fee_multiplier) AS max_mult, max(first_observed_at) AS newest
  FROM kalshi_fee_terms GROUP BY 1, 2 ORDER BY 3 DESC;

\echo V6a ADRIANA SCANS 24 h by status: markets read, structures considered, opportunities
SELECT status, count(*) AS scans, sum(markets_read) AS markets_read,
       sum(structures_considered) AS structures, sum(opportunities) AS opportunities,
       max(finished_at) AS newest, max(mode) AS mode, max(production_effect) AS production_effect
  FROM adriana_arb_scans WHERE finished_at >= now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC;

\echo V6b ADRIANA OPPORTUNITIES (GUARANTEED_AFTER_COSTS only) all time and 24 h, by venue set and kind
SELECT array_to_string(venues, '+') AS venues, structure_kind, count(*) AS all_time,
       count(*) FILTER (WHERE decided_at >= now() - interval '24 hours') AS last_24h,
       max(decided_at) AS newest
  FROM adriana_arb_opportunities GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12;

\echo V7 PAPER DECISIONS 24 h by strategy and verdict: PMUS slug present, Derek not-PMUS refusal
SELECT strategy, verdict, count(*) AS decisions,
       count(*) FILTER (WHERE us_market_slug IS NOT NULL) AS with_pmus_slug,
       count(*) FILTER (WHERE 'NOT_A_SUPPORTED_POLYMARKET_US_CONTRACT' = ANY(refusals)) AS refused_not_pmus
  FROM paper_decisions WHERE decided_at >= now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC;

\echo V8a KALSHI SMALL LIVE control row
SELECT id, enabled, stopped, kalshi_env, scale, max_order_usd, revision,
       (key_fingerprint IS NOT NULL) AS key_fingerprint_set,
       (reconciliation_id IS NOT NULL) AS reconciliation_set, updated_at
  FROM kalshi_smalllive_control;

\echo V8b KALSHI account reconciliations, live intents, fills, events (all time)
SELECT (SELECT count(*) FROM kalshi_account_reconciliations) AS reconciliations,
       (SELECT max(at) FROM kalshi_account_reconciliations) AS newest_reconciliation,
       (SELECT count(*) FROM kalshi_live_intents) AS intents,
       (SELECT count(*) FROM kalshi_live_intents WHERE venue_order_id IS NOT NULL) AS intents_with_venue_order,
       (SELECT count(*) FROM kalshi_live_fills) AS fills,
       (SELECT count(*) FROM kalshi_live_events) AS events;

\echo V8c KALSHI account limits receipts (signed read-only GET) newest 3
SELECT receipt_id, as_of FROM kalshi_account_limits_receipts ORDER BY as_of DESC LIMIT 3;
