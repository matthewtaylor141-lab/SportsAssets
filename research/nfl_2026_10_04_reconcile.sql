-- cand24 NFL reconciliation for Sunday 2026-10-04 (read-only).
-- One row per venue-listed NFL game on the America/New_York day:
--   EXPECTED -> PINNAPI (a provider event the collector saw) -> NORMALIZED ->
--   VENUE CONTRACT -> EXACT MAP -> SETTLEMENT -> PROBABILITY ->
--   DEREK EVALUATED -> ENTER/REFUSE, the first stage that did not pass, and
--   a MISSING list. Expected = research/nfl_2026_10_04_expected.json (14).
--   ET  day = [2026-10-04 04:00Z, 2026-10-05 04:00Z)
-- Provider key americanfootball_nfl; venue token 'nfl'; Derek's strategy
-- DEREK_ENTRY_POLICY_V2. PinnAPI scope is printed separately (football is
-- not in it); the PINNAPI column is the scheduled collector's provider event.
\echo '== R0 · read instant =='
SELECT now() AS read_at, now() AT TIME ZONE 'America/New_York' AS read_at_et;

\echo '== R1 · collector: requested set, budget, football board, NFL funnel step =='
SELECT to_timestamp((value->>'at')::float8) AS at,
       value->'sports_selection'->'requested' AS requested,
       value->'sports_selection'->'budget_dropped' AS budget_dropped,
       value->'sports_selection'->'venue_football_board'->'tokens' AS football_board,
       (SELECT jsonb_agg(r) FROM jsonb_array_elements(
          coalesce(value->'sports_selection'->'rejected', '[]'::jsonb)) r
         WHERE r->>'our_token' IN ('nfl', 'cfb')) AS football_rejected,
       left((value->'funnel_by_provider_sport'->'americanfootball_nfl')::text, 900) AS nfl_step
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== R2 · PinnAPI feed scope (sport ids streamed) =='
SELECT value->'sport_ids' AS scope_sport_ids FROM ingestion_state
 WHERE key = 'pinnapi_feed_scope';

\echo '== R3 · per game: EXPECTED -> ... -> ENTER/REFUSE =='
WITH exp AS (
    SELECT event_slug, market_slug, max(event_title) AS title,
           min(game_start) AS gs,
           max(team_name) FILTER (WHERE intent = 'ORDER_INTENT_BUY_SHORT') AS home,
           max(team_name) FILTER (WHERE intent = 'ORDER_INTENT_BUY_LONG') AS away,
           max(side_norm) FILTER (WHERE intent = 'ORDER_INTENT_BUY_SHORT') AS home_nick,
           max(side_norm) FILTER (WHERE intent = 'ORDER_INTENT_BUY_LONG') AS away_nick
      FROM us_premap
     WHERE split_part(market_slug, '-', 2) = 'nfl'
       AND sports_type = 'football_team_full_game_winner'
       AND game_start >= timestamptz '2026-10-04 04:00Z'
       AND game_start <  timestamptz '2026-10-05 04:00Z'
     GROUP BY 1, 2),
led AS (
    SELECT provider_event_id, max(home) AS p_home, max(away) AS p_away,
           max(commence_time) AS ct, max(us_market_slug) AS slug,
           max(CASE WHEN outcome IN ('ADMITTED', 'ALREADY_RECORDED') THEN 99
                    WHEN outcome = 'REFUSED' AND stage ~ '^[1-8]_' THEN
                         greatest(substr(stage, 1, 1)::int,
                                  CASE WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END)
                    WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END) AS reach,
           (array_agg(first_refusal ORDER BY cycle_at DESC))[1] AS refusal,
           (array_agg(outcome ORDER BY cycle_at DESC))[1] AS outcome,
           count(*) AS ledger_rows, max(cycle_at) AS last_cycle
      FROM ext_candidate_outcomes
     WHERE sport_key = 'americanfootball_nfl'
       AND cycle_at >= timestamptz '2026-10-02 00:00Z'
       AND provider_event_id IS NOT NULL
     GROUP BY 1),
m AS (
    SELECT e.*, l.provider_event_id, l.slug, l.reach, l.refusal, l.outcome,
           l.ledger_rows
      FROM exp e
      LEFT JOIN LATERAL (
          SELECT * FROM led
           WHERE led.slug = e.market_slug
              OR (led.ct ~ '^[0-9]{4}-'
                  AND abs(extract(epoch FROM (led.ct::timestamptz - e.gs))) <= 5400
                  AND lower(led.p_home || ' ' || led.p_away) LIKE '%' || e.home_nick || '%'
                  AND lower(led.p_home || ' ' || led.p_away) LIKE '%' || e.away_nick || '%')
           ORDER BY (led.slug = e.market_slug) DESC NULLS LAST, led.last_cycle DESC
           LIMIT 1) l ON true),
val AS (
    SELECT DISTINCT ON (us_market_slug) us_market_slug, id, record_purpose,
           probability, refusals
      FROM external_valuations
     WHERE us_market_slug IN (SELECT market_slug FROM exp)
       AND decided_at >= timestamptz '2026-10-02 00:00Z'
     ORDER BY us_market_slug, decided_at DESC, id DESC),
dk AS (
    SELECT DISTINCT ON (us_market_slug) us_market_slug, verdict, refusal
      FROM paper_decisions
     WHERE us_market_slug IN (SELECT market_slug FROM exp)
       AND strategy = 'DEREK_ENTRY_POLICY_V2'
       AND decided_at >= timestamptz '2026-10-02 00:00Z'
     ORDER BY us_market_slug, decided_at DESC)
SELECT m.title,
       to_char(m.gs AT TIME ZONE 'UTC', 'MM-DD HH24:MI') AS ko_utc,
       to_char(m.gs AT TIME ZONE 'America/New_York', 'MM-DD HH24:MI') AS ko_et,
       'Y' AS expected,
       coalesce(m.provider_event_id, '-') AS pinnapi,
       CASE WHEN coalesce(m.reach, 0) >= 3 OR v.id IS NOT NULL THEN 'Y' ELSE '-' END AS normalized,
       m.market_slug AS venue_contract,
       CASE WHEN m.slug = m.market_slug OR v.id IS NOT NULL THEN 'Y' ELSE '-' END AS exact_map,
       CASE WHEN v.id IS NULL THEN '-'
            ELSE coalesce(nullif(array_to_string(ARRAY(
                   SELECT c FROM unnest(v.refusals) c
                    WHERE c ~ '(SETTLEMENT|_RULE_|DRAW_HANDLING|VOID_|OVERTIME_)'), ','), ''),
                 'ESTABLISHED') END AS settlement,
       CASE WHEN v.id IS NULL THEN '-'
            WHEN v.probability IS NOT NULL THEN round(v.probability::numeric, 4)::text
            ELSE coalesce(nullif(array_to_string(ARRAY(
                   SELECT c FROM unnest(v.refusals) c
                    WHERE c ~ '(SUPPORTED_SET|QUALIFIED_PINNACLE|PROBABILITY)'), ','), ''),
                 'NO_PROBABILITY') END AS probability,
       CASE WHEN d.verdict IS NOT NULL THEN 'Y' ELSE '-' END AS derek_evaluated,
       coalesce(d.verdict || coalesce(':' || d.refusal, ''), '-') AS enter_refuse,
       CASE WHEN d.verdict IS NOT NULL THEN 'VERDICT'
            WHEN v.id IS NOT NULL THEN 'DEREK_EVALUATED'
            WHEN m.provider_event_id IS NULL THEN 'PINNAPI'
            WHEN coalesce(m.reach, 0) < 3 THEN 'NORMALIZED'
            WHEN m.slug IS DISTINCT FROM m.market_slug THEN 'EXACT_MAP'
            ELSE 'SETTLEMENT' END AS stopped_at,
       coalesce(m.outcome || ':' || m.refusal, '') AS ledger_last
  FROM m
  LEFT JOIN val v ON v.us_market_slug = m.market_slug
  LEFT JOIN dk d ON d.us_market_slug = m.market_slug
 ORDER BY m.gs, m.title;

\echo '== R4 · stage totals (of the expected games) =='
WITH exp AS (
    SELECT DISTINCT market_slug FROM us_premap
     WHERE split_part(market_slug, '-', 2) = 'nfl'
       AND sports_type = 'football_team_full_game_winner'
       AND game_start >= timestamptz '2026-10-04 04:00Z'
       AND game_start <  timestamptz '2026-10-05 04:00Z')
SELECT (SELECT count(*) FROM exp) AS expected,
       (SELECT count(DISTINCT provider_event_id) FROM ext_candidate_outcomes
         WHERE sport_key = 'americanfootball_nfl'
           AND cycle_at >= timestamptz '2026-10-02 00:00Z') AS provider_events_seen,
       (SELECT count(DISTINCT us_market_slug) FROM ext_candidate_outcomes
         WHERE sport_key = 'americanfootball_nfl'
           AND us_market_slug IN (SELECT market_slug FROM exp)) AS exact_mapped,
       (SELECT count(DISTINCT us_market_slug) FROM external_valuations
         WHERE us_market_slug IN (SELECT market_slug FROM exp)) AS valued,
       (SELECT count(DISTINCT us_market_slug) FROM paper_decisions
         WHERE us_market_slug IN (SELECT market_slug FROM exp)
           AND strategy = 'DEREK_ENTRY_POLICY_V2') AS derek_decided,
       (SELECT count(DISTINCT us_market_slug) FROM paper_decisions
         WHERE us_market_slug IN (SELECT market_slug FROM exp)
           AND strategy = 'DEREK_ENTRY_POLICY_V2' AND verdict = 'ENTER') AS derek_enter;

\echo '== R5 · MISSING: expected games with no provider event in the ledger =='
WITH exp AS (
    SELECT market_slug, max(event_title) AS title, min(game_start) AS gs,
           max(side_norm) FILTER (WHERE intent = 'ORDER_INTENT_BUY_SHORT') AS home_nick,
           max(side_norm) FILTER (WHERE intent = 'ORDER_INTENT_BUY_LONG') AS away_nick
      FROM us_premap
     WHERE split_part(market_slug, '-', 2) = 'nfl'
       AND sports_type = 'football_team_full_game_winner'
       AND game_start >= timestamptz '2026-10-04 04:00Z'
       AND game_start <  timestamptz '2026-10-05 04:00Z'
     GROUP BY 1)
SELECT e.market_slug, e.title,
       to_char(e.gs AT TIME ZONE 'America/New_York', 'MM-DD HH24:MI') AS ko_et,
       CASE WHEN NOT coalesce((SELECT value->'sports_selection'->'requested'
                                 FROM ingestion_state
                                WHERE key = 'ext_pinnacle_last_cycle')
                              ? 'americanfootball_nfl', false)
            THEN 'NOT_REQUESTED_BY_THE_COLLECTOR'
            ELSE 'REQUESTED_NO_PROVIDER_EVENT_FOR_THIS_GAME' END AS reason
  FROM exp e
 WHERE NOT EXISTS (
     SELECT 1 FROM ext_candidate_outcomes o
      WHERE o.sport_key = 'americanfootball_nfl'
        AND o.cycle_at >= timestamptz '2026-10-02 00:00Z'
        AND (o.us_market_slug = e.market_slug
             OR (lower(coalesce(o.home, '') || ' ' || coalesce(o.away, ''))
                   LIKE '%' || e.home_nick || '%'
                 AND lower(coalesce(o.home, '') || ' ' || coalesce(o.away, ''))
                   LIKE '%' || e.away_nick || '%')))
 ORDER BY e.gs, e.market_slug;
