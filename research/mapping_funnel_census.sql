-- READ-ONLY. THE FUNNEL, WITH LABELS THAT MATCH WHAT THE COUNTERS PROVE.
--
-- ── WHAT WAS WRONG WITH MY FIRST VERSION ────────────────────────────
-- An independent review corrected three identity assumptions, all mine:
--
--  1 · "70 VENUE MARKETS" WAS NOT 70 US CONTRACTS. `MARKETS_SQL` reads the
--      GLOBAL `markets` catalogue, and `resolve_venue_identity` documents
--      in its own refusal text that `markets.slug` is "a GLOBAL id and the
--      venue does not accept it". A global fixture row is not evidence that
--      a Polymarket US contract exists.
--
--  2 · `mapped_to_a_venue_contract` INCREMENTS BEFORE RESOLUTION. The loop
--      bumps it after `vmap.map_event()` and BEFORE
--      `resolve_venue_identity()` runs. So "mapped 1" for MLB means a
--      GLOBAL FIXTURE MATCHED -- not that a US contract was resolved. The
--      counter name overstates the counter.
--
--  3 · THE SAME 70 SOCCER ROWS APPEAR TWICE. `venue_markets_open_and_fresh`
--      is computed per SPORT FAMILY, so soccer_epl and soccer_mexico_ligamx
--      both report the family's 70. They are not two league inventories and
--      must not be added or read as such.
--
-- AND THE JOIN I USED TO "PROVE" MISSING COVERAGE WAS MEANINGLESS:
-- `LEFT JOIN us_premap p ON p.market_slug = m.slug` compares the US
-- contract slug in `us_premap.market_slug` against the GLOBAL slug in
-- `markets.slug`. Two namespaces. An unmatched row proved nothing. It is
-- removed rather than loosened -- the resolver's real rules (deterministic
-- keys, game agreement including the venue's calendar-adjacent dating, and
-- the venue's own side expansion) are what decide this, and
-- `premap.resolve_explain()` already reports which of its SIX failure
-- modes fired. That needs Python, not SQL: see
-- `research/resolve_explain_census.py`.
--
-- THE HONEST STAGE LADDER, and which stages this file can and cannot see:
--
--   1 provider event                       heartbeat: provider_events
--   2 matching global fixture              heartbeat: mapped_to_a_venue_contract
--   3 resolved US contract                 NOT in the heartbeat counters --
--                                          only as a ledger refusal
--   4 correct outcome/intent               ledger refusal
--   5 successful book read                 ledger refusal + venue_errors
--   6 qualified evidence (currency)        ledger refusal
--   7 complete economic evaluation         external_valuations.decision
--   8 eligible funded decision             funded intents (currently zero)

-- ─────────────────────────────────────────────────────────────────────
-- 1 · THE WRITER AND THE CYCLE.
-- ─────────────────────────────────────────────────────────────────────
WITH h AS (SELECT value::jsonb AS v FROM ingestion_state
            WHERE key = 'ext_pinnacle_last_cycle')
SELECT (v -> 'writer' ->> 'build')                AS writer_build,
       to_timestamp((v ->> 'at')::float8)         AS cycle_at,
       (v ->> 'cycle_label')                      AS cycle_label,
       (v ->> 'markets_considered')               AS global_fixtures_considered,
       (v ->> 'evaluated')                        AS evaluated,
       (v ->> 'written')                          AS written
  FROM h;

-- ─────────────────────────────────────────────────────────────────────
-- 2 · STAGES 1-2 PER SPORT, RELABELLED. `global_fixtures_in_family` is a
--     per-FAMILY figure and is repeated across leagues of that family --
--     it is printed with that warning in its own column name.
-- ─────────────────────────────────────────────────────────────────────
WITH h AS (SELECT value::jsonb -> 'funnel_by_provider_sport' AS f
             FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle')
SELECT k                                           AS provider_sport_key,
       (f -> k ->> 'family')                       AS family,
       (f -> k ->> 'venue_markets_open_and_fresh')  AS global_fixtures_in_family,
       (f -> k ->> 'provider_events')               AS s1_provider_events,
       (f -> k ->> 'with_pinnacle_h2h')             AS s1b_with_pinnacle,
       (f -> k ->> 'mapped_to_a_venue_contract')    AS s2_global_fixture_matched,
       (f -> k ->> 'evaluated')                     AS s7_evaluated,
       (f -> k -> 'refusals')                       AS refusals
  FROM h, jsonb_object_keys(coalesce(h.f, '{}'::jsonb)) AS k
 ORDER BY (f -> k ->> 'provider_events')::int DESC NULLS LAST;

-- ─────────────────────────────────────────────────────────────────────
-- 3 · STAGES 3-6: EVERY CANDIDATE THAT MATCHED A GLOBAL FIXTURE, against
--     the ONE condition that stopped it. This is where a resolved US
--     contract, the intent, the book read and the currency are decided,
--     and it is the only place the heartbeat records them.
-- ─────────────────────────────────────────────────────────────────────
WITH h AS (SELECT value::jsonb -> 'mapped_candidate_ledger' AS l
             FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle')
SELECT (e ->> 'stage')                            AS stage,
       (e ->> 'first_refusal')                    AS first_blocking_condition,
       count(*)                                   AS candidates,
       min(e ->> 'global_slug')                   AS example_global_slug,
       min(e ->> 'us_market_slug')                AS example_us_slug
  FROM h, jsonb_array_elements(coalesce(h.l, '[]'::jsonb)) AS e
 GROUP BY stage, first_blocking_condition
 ORDER BY candidates DESC;

-- EVERY LEDGER ROW IN FULL, so a single candidate can be traced input by
-- input rather than summarised. Bounded by the ledger's own size.
WITH h AS (SELECT value::jsonb -> 'mapped_candidate_ledger' AS l
             FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle')
SELECT jsonb_pretty(e) AS candidate
  FROM h, jsonb_array_elements(coalesce(h.l, '[]'::jsonb)) AS e;

-- ─────────────────────────────────────────────────────────────────────
-- 4 · THE BOOK-READ ERROR, IN FULL. The loop stores a sanitized
--     diagnostic per distinct venue error: stage, resolver, slugs,
--     intent, refusal and the venue's own message. This is the immediate
--     actionable item for the one candidate that reached stage 5.
-- ─────────────────────────────────────────────────────────────────────
WITH h AS (SELECT value::jsonb -> 'venue_errors' AS v
             FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle')
SELECT jsonb_pretty(e) AS venue_error
  FROM h, jsonb_array_elements(coalesce(h.v, '[]'::jsonb)) AS e;

-- ─────────────────────────────────────────────────────────────────────
-- 5 · THE COMPLETE BOUNDED GLOBAL CANDIDATE SET, NOT A 40-ROW SAMPLE.
--     A sample cannot establish that none of the family's fixtures are
--     the relevant ones. These are exactly the rows MARKETS_SQL selects.
-- ─────────────────────────────────────────────────────────────────────
SELECT sport,
       count(*)                                   AS open_and_fresh_global,
       min(updated_at)                            AS oldest_update,
       max(updated_at)                            AS newest_update
  FROM markets
 WHERE NOT closed AND NOT resolved
   AND updated_at >= now() - INTERVAL '2 days'
 GROUP BY sport
 ORDER BY open_and_fresh_global DESC;

-- ALL of them for the sports the lane asks for, with the fields the
-- resolver actually keys on: competition/event title, market title, the
-- date in the slug, and the condition id.
SELECT slug, event_slug, event_title, title, condition_id, updated_at
  FROM markets
 WHERE NOT closed AND NOT resolved
   AND updated_at >= now() - INTERVAL '2 days'
   AND sport IN ('soccer', 'baseball')
 ORDER BY sport, updated_at DESC;

-- ─────────────────────────────────────────────────────────────────────
-- 6 · STAGE 7-8: the evaluation history and the funded book, so "zero
--     candidates" is distinguished from "evaluated and refused".
-- ─────────────────────────────────────────────────────────────────────
SELECT decision,
       count(*)                                   AS rows_,
       count(DISTINCT us_market_slug)              AS markets,
       min(decided_at)                            AS first_at,
       max(decided_at)                            AS last_at
  FROM external_valuations
 GROUP BY decision
 ORDER BY rows_ DESC;

SELECT count(*) AS funded_intents,
       count(*) FILTER (WHERE state = 'FILLED')    AS filled,
       count(*) FILTER (WHERE closed_at IS NULL)   AS open_positions
  FROM bettor_funded_intents;
