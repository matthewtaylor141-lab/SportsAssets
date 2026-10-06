-- P0 coverage (2026-10-06): the venue's OWN rows behind the remaining
-- MAPPED / NORMALIZED / SETTLEMENT software first losses of the 24 h census.
-- Read-only. Every section reads the venue catalogue or our own records.

\echo '== M1: NCAAF nickname refusals -- the venue rows of each fixture'
SELECT event_slug, market_slug, intent, team_name, side_norm, team_abbr,
       team_safe_name, event_title, game_start
  FROM us_premap
 WHERE sports_type = 'football_team_full_game_winner'
   AND event_slug LIKE 'cfb-%'
   AND game_start BETWEEN timestamptz '2026-10-08 22:00Z'
                      AND timestamptz '2026-10-09 01:00Z'
   AND (lower(event_title) LIKE '%ark%' OR lower(event_title) LIKE '%ala%'
        OR lower(event_title) LIKE '%utsa%' OR lower(event_title) LIKE '%tex%'
        OR lower(event_title) LIKE '%fl%' OR lower(event_title) LIKE '%usf%')
 ORDER BY event_slug, intent;

\echo '== M2: the ledger rows of the two NCAAF nickname refusals'
SELECT provider_event_id, home, away, commence_time, first_refusal, codes
  FROM ext_candidate_outcomes
 WHERE first_refusal = 'VENUE_NATIVE_NICKNAME_DOES_NOT_CONFIRM_THE_TEAM'
   AND cycle_at >= now() - interval '48 hours'
 ORDER BY cycle_at DESC LIMIT 6;

\echo '== M3: Goias vs Athletic Club -- every venue soccer winner row naming either'
SELECT event_slug, market_slug, intent, team_name, side_norm, team_abbr,
       team_safe_name, team_league, event_title, game_start
  FROM us_premap
 WHERE sports_type = 'soccer_team_full_time_winner'
   AND game_start BETWEEN timestamptz '2026-10-06 18:00Z'
                      AND timestamptz '2026-10-07 04:00Z'
   AND (lower(coalesce(team_name, '')) LIKE '%goi%'
        OR lower(coalesce(team_name, '')) LIKE '%athletic%'
        OR lower(event_title) LIKE '%goi%'
        OR lower(event_title) LIKE '%athletic%')
 ORDER BY event_slug, market_slug, intent;

\echo '== M4: the ledger rows of the one-team, primary-ambiguous and no-exact refusals'
SELECT provider_event_id, sport_key, home, away, commence_time,
       first_refusal, codes
  FROM ext_candidate_outcomes
 WHERE first_refusal IN ('VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM',
                         'PINNAPI_PRIMARY_FIXTURE_AMBIGUOUS',
                         'PINNAPI_PRIMARY_NO_EXACT_FIXTURE')
   AND cycle_at >= now() - interval '48 hours'
 ORDER BY first_refusal, cycle_at DESC LIMIT 12;

\echo '== M5: Riestra / Central Cordoba and Bosnia / Poland -- venue rows'
SELECT event_slug, market_slug, intent, team_name, team_safe_name,
       team_league, event_title, game_start
  FROM us_premap
 WHERE sports_type = 'soccer_team_full_time_winner'
   AND game_start BETWEEN timestamptz '2026-10-05 16:00Z'
                      AND timestamptz '2026-10-05 23:00Z'
   AND (lower(event_title) LIKE '%riestra%' OR lower(event_title) LIKE '%cordoba%'
        OR lower(event_title) LIKE '%bosnia%' OR lower(event_title) LIKE '%poland%'
        OR lower(coalesce(team_name, '')) LIKE '%riestra%'
        OR lower(coalesce(team_name, '')) LIKE '%bosnia%')
 ORDER BY event_slug, market_slug, intent;

\echo '== S1: the two NCAAF SETTLEMENT_NOT_SUPPORTED valuations and decisions'
SELECT v.id, v.us_market_slug, v.record_purpose, v.refusals,
       v.settlement_comparison->>'venue_rules_text' AS venue_rules_text,
       v.settlement_comparison->'blockers' AS blockers,
       v.settlement_comparison->>'compatibility' AS compatibility
  FROM external_valuations v
 WHERE v.us_market_slug IN ('aec-cfb-charlt-ntx-2026-10-10',
                            'aec-cfb-minnst-pur-2026-10-10')
 ORDER BY v.id DESC LIMIT 4;

SELECT d.valuation_id, d.us_market_slug, d.strategy, d.verdict, d.refusal,
       d.refusals, d.decided_at
  FROM paper_decisions d
 WHERE d.us_market_slug IN ('aec-cfb-charlt-ntx-2026-10-10',
                            'aec-cfb-minnst-pur-2026-10-10')
 ORDER BY d.decided_at DESC LIMIT 12;
