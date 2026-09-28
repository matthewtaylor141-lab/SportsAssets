-- READ-ONLY. THE MAPPING FUNNEL AND THE SUPPORTED MARKET UNIVERSE.
--
-- Audit A04: "Current candidates usually fail mapping; the one mapped
-- candidate fails currency before economic evaluation." And the handoff:
-- "Verify the current mapping funnel and supported market universe before
-- spending more provider credits."
--
-- THE CAPTURED CYCLE THE AUDIT READ: 158 venue markets considered, ZERO
-- evaluated, refusals = 22 missing venue mappings, 9 events without
-- Pinnacle, 1 ambiguous mapping, 1 book-currency contradiction. The sole
-- mapped candidate was `aec-mlb-chc-sd-2026-09-30`.
--
-- WHAT THIS FILE ESTABLISHES, AND WHAT IT CANNOT. It reads the CURRENT
-- funnel from the heartbeat and the venue catalogue, so it says where
-- candidates are lost NOW. It cannot tell you whether a mapping is
-- ABSENT because the venue lacks the contract or because our resolver
-- cannot see it -- section 4 separates those two by checking whether the
-- venue row exists at all.

-- ─────────────────────────────────────────────────────────────────────
-- 1 · THE WRITER, AND THE FUNNEL IT RECORDED. Per sport, per stage.
-- ─────────────────────────────────────────────────────────────────────
WITH h AS (SELECT value::jsonb AS v FROM ingestion_state
            WHERE key = 'ext_pinnacle_last_cycle')
SELECT (v -> 'writer' ->> 'build')                AS writer_build,
       to_timestamp((v ->> 'at')::float8)         AS cycle_at,
       (v ->> 'cycle_label')                      AS cycle_label,
       (v ->> 'markets_considered')               AS venue_markets,
       (v ->> 'evaluated')                        AS evaluated,
       (v ->> 'written')                          AS written
  FROM h;

-- ─────────────────────────────────────────────────────────────────────
-- 2 · PER SPORT: how many provider events arrived, how many carried
--     Pinnacle, how many mapped, how many were evaluated. This is the
--     funnel the audit asked to be reported per stage.
-- ─────────────────────────────────────────────────────────────────────
WITH h AS (SELECT value::jsonb -> 'funnel_by_provider_sport' AS f
             FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle')
SELECT k                                          AS sport_key,
       (f -> k ->> 'family')                      AS family,
       (f -> k ->> 'venue_markets_open_and_fresh') AS venue_open,
       (f -> k ->> 'provider_events')              AS provider_events,
       (f -> k ->> 'with_pinnacle_h2h')            AS with_pinnacle,
       (f -> k ->> 'mapped_to_a_venue_contract')   AS mapped,
       (f -> k ->> 'evaluated')                    AS evaluated,
       (f -> k -> 'refusals')                      AS refusals
  FROM h, jsonb_object_keys(coalesce(h.f, '{}'::jsonb)) AS k
 ORDER BY (f -> k ->> 'provider_events')::int DESC NULLS LAST;

-- ─────────────────────────────────────────────────────────────────────
-- 3 · EVERY CANDIDATE'S FIRST REFUSAL, from the mapped-candidate ledger.
--     One row per candidate against the ONE reason it stopped -- so a
--     cause and its downstream consequence are not counted as two
--     independent problems, which the audit warns inflates the inventory.
-- ─────────────────────────────────────────────────────────────────────
WITH h AS (SELECT value::jsonb -> 'mapped_candidate_ledger' AS l
             FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle')
SELECT (e ->> 'stage')                            AS stage,
       (e ->> 'first_refusal')                    AS first_refusal,
       count(*)                                   AS candidates,
       min(e ->> 'us_market_slug')                AS example_slug
  FROM h, jsonb_array_elements(coalesce(h.l, '[]'::jsonb)) AS e
 GROUP BY stage, first_refusal
 ORDER BY candidates DESC;

-- ─────────────────────────────────────────────────────────────────────
-- 4 · IS THE MAPPING MISSING BECAUSE THE VENUE HAS NO CONTRACT, OR
--     BECAUSE WE CANNOT RESOLVE ONE? Two different owners, and the
--     refusal code alone does not distinguish them.
--
--     TABLE NAMES CORRECTED. My first version queried `us_markets`, which
--     does not exist -- the loop's own MARKETS_SQL reads `markets`, and
--     `us_premap` keys on `market_slug`, not `us_market_slug`. The SQL
--     died at section 4 and sections 1-3 had already returned, which is
--     the right failure order but was avoidable by reading the producer.
-- ─────────────────────────────────────────────────────────────────────
SELECT count(*)                                              AS markets_total,
       count(*) FILTER (WHERE NOT closed AND NOT resolved)     AS open_markets,
       count(DISTINCT sport)                                  AS sports,
       count(*) FILTER (WHERE condition_id IS NOT NULL)        AS with_condition_id,
       count(*) FILTER (WHERE NOT closed AND NOT resolved
                        AND updated_at >= now() - INTERVAL '2 days')
                                                              AS open_and_fresh
  FROM markets;

-- WHICH SPORTS THE VENUE ACTUALLY LISTS. A sport the venue does not carry
-- is a universe fact, not a mapper bug. THE DECISIVE QUESTION for the 20
-- EPL refusals: the loop saw 70 open soccer markets and mapped NONE of
-- them, so either those 70 are not EPL, or the mapper cannot match them.
SELECT sport,
       count(*)                                     AS markets,
       count(*) FILTER (WHERE NOT closed AND NOT resolved) AS open_now,
       count(*) FILTER (WHERE NOT closed AND NOT resolved
                        AND updated_at >= now() - INTERVAL '2 days')
                                                    AS open_and_fresh,
       min(slug)                                    AS example_slug,
       min(event_title)                             AS example_event
  FROM markets
 GROUP BY sport
 ORDER BY open_and_fresh DESC NULLS LAST, markets DESC;

-- THE OPEN SOCCER MARKETS BY EVENT TITLE. If these are not EPL fixtures,
-- the 20 refusals are honest coverage and no mapper change helps.
SELECT slug, event_title, title, updated_at
  FROM markets
 WHERE sport = 'soccer' AND NOT closed AND NOT resolved
   AND updated_at >= now() - INTERVAL '2 days'
 ORDER BY updated_at DESC
 LIMIT 40;

-- ─────────────────────────────────────────────────────────────────────
-- 5 · THE PREMAP: the resolver's own binding table, keyed on
--     `market_slug`. A market with no premap row cannot be resolved to an
--     instrument regardless of what the catalogue carries -- OUR gap.
-- ─────────────────────────────────────────────────────────────────────
SELECT count(*)                                              AS premap_rows,
       count(DISTINCT market_slug)                            AS distinct_slugs,
       count(*) FILTER (WHERE event_slug IS NOT NULL)          AS with_event_slug
  FROM us_premap;

SELECT m.sport,
       count(*)                                     AS open_fresh_without_premap,
       min(m.slug)                                  AS example
  FROM markets m
  LEFT JOIN us_premap p ON p.market_slug = m.slug
 WHERE NOT m.closed AND NOT m.resolved
   AND m.updated_at >= now() - INTERVAL '2 days'
   AND p.market_slug IS NULL
 GROUP BY m.sport
 ORDER BY open_fresh_without_premap DESC;

-- ─────────────────────────────────────────────────────────────────────
-- 6 · THE 1,126-ROW HISTORY's first failing link, so the CURRENT funnel
--     can be compared against it rather than against a memory of it.
-- ─────────────────────────────────────────────────────────────────────
SELECT decision,
       count(*)                                   AS rows_,
       count(DISTINCT us_market_slug)              AS markets,
       min(decided_at)                            AS first_at,
       max(decided_at)                            AS last_at
  FROM external_valuations
 GROUP BY decision
 ORDER BY rows_ DESC;
