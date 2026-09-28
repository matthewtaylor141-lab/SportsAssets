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
-- ─────────────────────────────────────────────────────────────────────
SELECT count(*)                                            AS venue_markets_total,
       count(*) FILTER (WHERE closed_at IS NULL)            AS open_markets,
       count(DISTINCT sport)                                AS sports,
       count(*) FILTER (WHERE condition_id IS NOT NULL)      AS with_condition_id
  FROM us_markets;

-- WHICH SPORTS THE VENUE ACTUALLY LISTS, against the sports we ASK for.
-- A sport the venue does not carry is a universe fact, not a mapper bug.
SELECT sport,
       count(*)                                   AS markets,
       count(*) FILTER (WHERE closed_at IS NULL)   AS open_now,
       min(slug)                                  AS example
  FROM us_markets
 GROUP BY sport
 ORDER BY open_now DESC NULLS LAST, markets DESC;

-- ─────────────────────────────────────────────────────────────────────
-- 5 · THE PREMAP: the resolver's own binding table. A market with no
--     premap row cannot be resolved to an instrument regardless of what
--     the catalogue carries, and that is OUR gap rather than the venue's.
-- ─────────────────────────────────────────────────────────────────────
SELECT count(*)                                            AS premap_rows,
       count(DISTINCT us_market_slug)                       AS distinct_slugs,
       count(*) FILTER (WHERE event_slug IS NOT NULL)        AS with_event_slug
  FROM us_premap;

-- OPEN VENUE MARKETS WITH NO PREMAP ROW -- the actionable gap.
SELECT m.sport,
       count(*)                                   AS open_without_premap,
       min(m.slug)                                AS example
  FROM us_markets m
  LEFT JOIN us_premap p ON p.us_market_slug = m.slug
 WHERE m.closed_at IS NULL AND p.us_market_slug IS NULL
 GROUP BY m.sport
 ORDER BY open_without_premap DESC;

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
