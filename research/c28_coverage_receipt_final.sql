-- c28 COVERAGE RECEIPT, FINAL (read-only), Sunday 2026-10-04: c28_coverage_receipt.sql + the F1-F6 first-loss sections of c28_coverage_firstloss.sql, so ONE run id / job id / sha256 covers deliverables 1-3.
-- Three readbacks in one file so one run id / job id / sha256 covers them:
--   N  NFL per-game production receipt: EXPECTED (schedule evidence UNION
--      venue discovery UNION provider events on the ET day) -> PROVIDER
--      EVENT -> NORMALIZED -> VENUE CONTRACT -> EXACT MAPPING -> SETTLEMENT
--      -> PROBABILITY -> DEREK EVALUATED -> REFUSE/ENTER -> PAPER ORDER ->
--      PAPER FILL, the FIRST BLOCKER and a STATUS per game.
--   A  all-sports funnel per league key (provider config, venue board,
--      settlement registry, collector allowlists), ET day.
--   I  coverage incidents: persisted snapshots, collapse alerts and the
--      Audrey findings they wrote, so a provider>0 / downstream-0 league
--      without a FIRST-LOSS finding is visible.
-- Windows: ET day = [2026-10-04 04:00Z, 2026-10-05 04:00Z)
--          UTC day = [2026-10-04 00:00Z, 2026-10-05 00:00Z)
-- Schedule evidence = research/nfl_2026_10_04_expected.json (cand24 run
-- 37176989663, job 111361573239): 14 games, restated in `sched` below so a
-- game the venue has since delisted, or the provider never sent, still has
-- a row.
\echo '== R0 · read instant, schema head =='
SELECT now() AS read_at,
       to_char(now() AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI:SS') AS read_at_et,
       (SELECT max(version) FROM schema_migrations) AS schema_head;

\echo '== R1 · collector last cycle: requested / budget / dropped / rejected / boards =='
SELECT to_timestamp((value->>'at')::float8) AS at,
       round(extract(epoch FROM now()) - (value->>'at')::float8) AS age_s,
       value->'sports_selection'->'requested' AS requested,
       value->'sports_selection'->'metered_budget' AS budget,
       (SELECT jsonb_agg(d->>'key') FROM jsonb_array_elements(
          coalesce(value->'sports_selection'->'budget_dropped', '[]'::jsonb)) d) AS budget_dropped,
       (SELECT jsonb_agg(jsonb_build_object('key', r->>'key', 'token', r->>'our_token',
                                            'refusal', r->>'refusal'))
          FROM jsonb_array_elements(coalesce(value->'sports_selection'->'rejected', '[]'::jsonb)) r) AS rejected,
       value->'sports_selection'->'venue_board'->'tokens' AS soccer_board,
       value->'sports_selection'->'venue_football_board'->'tokens' AS football_board
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
SELECT k AS provider_sport, left(v::text, 600) AS funnel_step
  FROM ingestion_state, jsonb_each(coalesce(value->'funnel_by_provider_sport', '{}'::jsonb)) AS e(k, v)
 WHERE key = 'ext_pinnacle_last_cycle' ORDER BY 1;

\echo '== R2 · PinnAPI scope and heartbeat =='
SELECT (SELECT value->'sport_ids' FROM ingestion_state WHERE key = 'pinnapi_feed_scope') AS scope_sport_ids,
       (SELECT value->>'state' FROM ingestion_state WHERE key = 'pinnapi_feed_last') AS feed_state,
       (SELECT to_timestamp((value->>'beat_at')::float8) FROM ingestion_state
         WHERE key = 'pinnapi_feed_last') AS feed_beat_at;

\echo '== R3 · paper sessions and decision strategies since 2026-10-02 =='
SELECT session_id, account_id, status, started_at, stopped_at, left(coalesce(stopped_why, ''), 80) AS stopped_why
  FROM paper_sessions ORDER BY started_at DESC LIMIT 5;
SELECT strategy, count(*) AS decisions, count(*) FILTER (WHERE verdict = 'ENTER') AS enter,
       min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM paper_decisions WHERE decided_at >= timestamptz '2026-10-02 00:00Z'
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== N1 · NFL PER GAME (ET day 2026-10-04) =='
WITH sched(market_slug, title, ko, home_nick, away_nick) AS (VALUES
  ('aec-nfl-ind-was-2026-10-04', 'IND Colts vs. WAS Commanders', timestamptz '2026-10-04T13:30:00Z', 'commanders', 'colts'),
  ('aec-nfl-ari-nyg-2026-10-04', 'ARI Cardinals vs. NY Giants', timestamptz '2026-10-04T17:00:00Z', 'giants', 'cardinals'),
  ('aec-nfl-dal-hou-2026-10-04', 'DAL Cowboys vs. HOU Texans', timestamptz '2026-10-04T17:00:00Z', 'texans', 'cowboys'),
  ('aec-nfl-gb-tb-2026-10-04', 'GB Packers vs. TB Buccaneers', timestamptz '2026-10-04T17:00:00Z', 'buccaneers', 'packers'),
  ('aec-nfl-jax-cin-2026-10-04', 'JAC Jaguars vs. CIN Bengals', timestamptz '2026-10-04T17:00:00Z', 'bengals', 'jaguars'),
  ('aec-nfl-lar-phi-2026-10-04', 'LA Rams vs. PHI Eagles', timestamptz '2026-10-04T17:00:00Z', 'eagles', 'rams'),
  ('aec-nfl-ne-buf-2026-10-04', 'NE Patriots vs. BUF Bills', timestamptz '2026-10-04T17:00:00Z', 'bills', 'patriots'),
  ('aec-nfl-nyj-chi-2026-10-04', 'NY Jets vs. CHI Bears', timestamptz '2026-10-04T17:00:00Z', 'bears', 'jets'),
  ('aec-nfl-ten-bal-2026-10-04', 'TEN Titans vs. BAL Ravens', timestamptz '2026-10-04T17:00:00Z', 'ravens', 'titans'),
  ('aec-nfl-mia-min-2026-10-04', 'MIA Dolphins vs. MIN Vikings', timestamptz '2026-10-04T20:05:00Z', 'vikings', 'dolphins'),
  ('aec-nfl-den-sf-2026-10-04', 'DEN Broncos vs. SF 49ers', timestamptz '2026-10-04T20:25:00Z', '49ers', 'broncos'),
  ('aec-nfl-kc-lv-2026-10-04', 'KC Chiefs vs. LV Raiders', timestamptz '2026-10-04T20:25:00Z', 'raiders', 'chiefs'),
  ('aec-nfl-lac-sea-2026-10-04', 'LA Chargers vs. SEA Seahawks', timestamptz '2026-10-04T20:25:00Z', 'seahawks', 'chargers'),
  ('aec-nfl-det-car-2026-10-04', 'DET Lions vs. CAR Panthers', timestamptz '2026-10-05T00:20:00Z', 'panthers', 'lions')),
ven AS (
    SELECT market_slug, max(event_title) AS title, min(game_start) AS ko,
           max(side_norm) FILTER (WHERE intent = 'ORDER_INTENT_BUY_SHORT') AS home_nick,
           max(side_norm) FILTER (WHERE intent = 'ORDER_INTENT_BUY_LONG') AS away_nick
      FROM us_premap
     WHERE (split_part(market_slug, '-', 2) = 'nfl'
            OR lower(split_part(coalesce(event_slug, ''), '-', 1)) = 'nfl')
       AND sports_type = 'football_team_full_game_winner'
       AND game_start >= timestamptz '2026-10-04 04:00Z'
       AND game_start <  timestamptz '2026-10-05 04:00Z'
     GROUP BY 1),
g AS (
    SELECT coalesce(s.market_slug, v.market_slug) AS market_slug,
           coalesce(s.title, v.title) AS title, coalesce(v.ko, s.ko) AS ko,
           coalesce(v.home_nick, s.home_nick) AS home_nick,
           coalesce(v.away_nick, s.away_nick) AS away_nick,
           s.market_slug IS NOT NULL AS in_schedule,
           v.market_slug IS NOT NULL AS in_venue_now
      FROM sched s FULL JOIN ven v ON v.market_slug = s.market_slug),
p AS (
    SELECT provider_event_id, max(home) AS home, max(away) AS away,
           max(commence_time) AS ct_txt, max(us_market_slug) AS slug,
           max(CASE WHEN outcome IN ('ADMITTED', 'ALREADY_RECORDED') THEN 99
                    WHEN outcome = 'REFUSED' AND stage ~ '^[1-8]_' THEN substr(stage, 1, 1)::int
                    ELSE 0 END) AS raw_stage,
           max(CASE WHEN outcome IN ('ADMITTED', 'ALREADY_RECORDED') THEN 99
                    WHEN outcome = 'REFUSED' AND stage ~ '^[1-8]_' THEN
                         greatest(substr(stage, 1, 1)::int,
                                  CASE WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END)
                    WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END) AS funnel_reach,
           (array_agg(outcome || coalesce(':' || stage, '') || coalesce(':' || first_refusal, '')
                      ORDER BY cycle_at DESC))[1] AS last_row,
           count(*) AS ledger_rows, count(DISTINCT cycle_id) AS cycles,
           min(cycle_at) AS first_cycle, max(cycle_at) AS last_cycle
      FROM ext_candidate_outcomes
     WHERE sport_key = 'americanfootball_nfl' AND provider_event_id IS NOT NULL
       AND cycle_at >= timestamptz '2026-10-01 00:00Z'
     GROUP BY 1),
pm AS (
    SELECT p.*,
           CASE WHEN p.ct_txt ~ '^[0-9]{4}-' THEN p.ct_txt::timestamptz END AS ct,
           (SELECT g.market_slug FROM g
             WHERE g.market_slug = p.slug
                OR (p.ct_txt ~ '^[0-9]{4}-'
                    AND abs(extract(epoch FROM (p.ct_txt::timestamptz - g.ko))) <= 5400
                    AND lower(coalesce(p.home, '') || ' ' || coalesce(p.away, '')) LIKE '%' || g.home_nick || '%'
                    AND lower(coalesce(p.home, '') || ' ' || coalesce(p.away, '')) LIKE '%' || g.away_nick || '%')
             ORDER BY (g.market_slug = p.slug) DESC NULLS LAST LIMIT 1) AS game_slug
      FROM p),
best AS (
    SELECT DISTINCT ON (game_slug) game_slug, provider_event_id, home, away, ct, slug,
           raw_stage, funnel_reach, last_row, ledger_rows, cycles, first_cycle, last_cycle,
           count(*) OVER (PARTITION BY game_slug) AS provider_events_for_game
      FROM pm WHERE game_slug IS NOT NULL
     ORDER BY game_slug, (slug = game_slug) DESC NULLS LAST, funnel_reach DESC, last_cycle DESC),
rows_ AS (
    SELECT g.market_slug, g.title, g.ko, g.in_schedule, g.in_venue_now, b.*
      FROM g LEFT JOIN best b ON b.game_slug = g.market_slug
    UNION ALL
    SELECT NULL, 'PROVIDER_ONLY: ' || coalesce(pm.away, '?') || ' at ' || coalesce(pm.home, '?'),
           pm.ct, false, false, pm.game_slug, pm.provider_event_id, pm.home, pm.away, pm.ct, pm.slug,
           pm.raw_stage, pm.funnel_reach, pm.last_row, pm.ledger_rows, pm.cycles, pm.first_cycle,
           pm.last_cycle, 1
      FROM pm
     WHERE pm.game_slug IS NULL AND pm.ct >= timestamptz '2026-10-04 04:00Z'
       AND pm.ct < timestamptz '2026-10-05 04:00Z'),
f AS (
    SELECT r.*, v.n_val, v.n_prob, v.last_prob, v.last_purpose, v.last_refusals, v.last_val_at,
           d.n_dec, d.n_enter, d.last_verdict, d.last_refusal, d.last_refusals AS d_refusals,
           d.strategy AS d_strategy, d.last_dec_at,
           o.n_orders, o.n_entry_orders, fl.n_fills
      FROM rows_ r
      LEFT JOIN LATERAL (
          SELECT count(*) AS n_val, count(*) FILTER (WHERE probability IS NOT NULL) AS n_prob,
                 (array_agg(probability ORDER BY decided_at DESC, id DESC))[1] AS last_prob,
                 (array_agg(record_purpose ORDER BY decided_at DESC, id DESC))[1] AS last_purpose,
                 (array_agg(array_to_string(refusals, ',') ORDER BY decided_at DESC, id DESC))[1] AS last_refusals,
                 max(decided_at) AS last_val_at
            FROM external_valuations ev
           WHERE ev.decided_at >= timestamptz '2026-10-02 00:00Z'
             AND (ev.us_market_slug = r.market_slug
                  OR (r.provider_event_id IS NOT NULL AND ev.event_key = r.provider_event_id))) v ON true
      LEFT JOIN LATERAL (
          SELECT count(*) AS n_dec, count(*) FILTER (WHERE verdict = 'ENTER') AS n_enter,
                 (array_agg(verdict ORDER BY decided_at DESC))[1] AS last_verdict,
                 (array_agg(refusal ORDER BY decided_at DESC))[1] AS last_refusal,
                 (array_agg(array_to_string(refusals, ',') ORDER BY decided_at DESC))[1] AS last_refusals,
                 (array_agg(strategy ORDER BY decided_at DESC))[1] AS strategy,
                 max(decided_at) AS last_dec_at
            FROM paper_decisions pd
           WHERE pd.decided_at >= timestamptz '2026-10-02 00:00Z'
             AND pd.strategy LIKE 'DEREK%'
             AND pd.us_market_slug = r.market_slug) d ON true
      LEFT JOIN LATERAL (
          SELECT count(*) AS n_orders, count(*) FILTER (WHERE role = 'ENTRY') AS n_entry_orders
            FROM paper_orders po
           WHERE po.us_market_slug = r.market_slug
             AND po.created_at >= timestamptz '2026-10-02 00:00Z') o ON true
      LEFT JOIN LATERAL (
          SELECT count(*) AS n_fills
            FROM paper_fills pf
           WHERE pf.us_market_slug = r.market_slug
             AND pf.filled_at >= timestamptz '2026-10-02 00:00Z') fl ON true),
c AS (
    SELECT f.*,
           (n_val > 0 OR n_dec > 0) AS evaluated_any,
           coalesce(nullif(array_to_string(ARRAY(
               SELECT x FROM unnest(string_to_array(coalesce(d_refusals, '') || ',' || coalesce(last_refusals, ''), ',')) x
                WHERE x ~ '(SETTLEMENT|_RULE_|DRAW_HANDLING|VOID_|OVERTIME_)' GROUP BY x ORDER BY x), ','), ''), '') AS sett_codes,
           coalesce(nullif(array_to_string(ARRAY(
               SELECT x FROM unnest(string_to_array(coalesce(last_refusals, ''), ',')) x
                WHERE x ~ '(SUPPORTED_SET|QUALIFIED_PINNACLE|PROBABILITY|DEVIG|OVERROUND|FAIR_VALUE|QUALIFIED_MODEL)'
                GROUP BY x ORDER BY x), ','), ''), '') AS prob_codes
      FROM f),
o AS (
    SELECT c.*,
           CASE WHEN provider_event_id IS NULL THEN 'N' ELSE 'Y' END AS c_provider,
           CASE WHEN provider_event_id IS NULL THEN '-'
                WHEN raw_stage >= 3 OR evaluated_any THEN 'Y'
                ELSE 'N' END AS c_normalized,
           CASE WHEN market_slug IS NULL THEN 'N'
                WHEN in_venue_now THEN 'Y' ELSE 'Y(schedule; not in us_premap now)' END AS c_venue,
           CASE WHEN provider_event_id IS NULL THEN '-'
                WHEN slug = market_slug OR evaluated_any THEN 'Y' ELSE 'N' END AS c_exact,
           CASE WHEN NOT evaluated_any THEN '-'
                WHEN sett_codes <> '' OR last_refusal = 'SETTLEMENT_NOT_SUPPORTED' THEN 'N'
                ELSE 'Y' END AS c_settlement,
           CASE WHEN NOT evaluated_any OR n_val = 0 THEN '-'
                WHEN last_prob IS NOT NULL THEN 'Y' ELSE 'N' END AS c_probability,
           CASE WHEN n_dec > 0 THEN 'Y' ELSE 'N' END AS c_derek,
           CASE WHEN n_dec = 0 THEN '-'
                WHEN n_enter > 0 THEN 'ENTER'
                ELSE 'REFUSE:' || coalesce(last_refusal, 'UNNAMED') END AS c_verdict
      FROM c)
SELECT coalesce(title, '?') AS game,
       to_char(ko AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') || 'Z' AS ko_utc,
       to_char(ko AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI') AS ko_et,
       to_char(ko AT TIME ZONE 'America/New_York', 'YYYY-MM-DD') AS et_service_day,
       to_char(ko AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS utc_day,
       CASE WHEN in_schedule THEN 'Y' WHEN market_slug IS NOT NULL THEN 'Y(venue)' ELSE 'N(provider only)' END AS expected,
       coalesce(provider_event_id, '-') AS provider_event,
       c_normalized AS normalized,
       coalesce(market_slug, '-') || ' ' || c_venue AS venue_contract,
       c_exact AS exact_mapping,
       c_settlement || CASE WHEN c_settlement = 'N' THEN ':' || coalesce(nullif(sett_codes, ''), last_refusal) ELSE '' END AS settlement_supported,
       c_probability || CASE WHEN c_probability = 'Y' THEN ':' || round(last_prob::numeric, 4)::text
                             WHEN c_probability = 'N' THEN ':' || coalesce(nullif(prob_codes, ''), 'NO_PROBABILITY') ELSE '' END AS probability_supported,
       c_derek || CASE WHEN n_dec > 0 THEN '(' || n_dec || ')' ELSE '' END AS derek_evaluated,
       c_verdict AS refuse_enter,
       coalesce(n_entry_orders, 0) AS paper_order,
       coalesce(n_fills, 0) AS paper_fill,
       CASE WHEN market_slug IS NULL THEN 'VENUE_CONTRACT:provider event matches no venue game'
            WHEN provider_event_id IS NULL THEN 'PROVIDER_EVENT:no americanfootball_nfl event matched in the ledger'
            WHEN c_normalized = 'N' THEN 'NORMALIZED:' || last_row
            WHEN c_exact = 'N' THEN 'EXACT_MAPPING:' || last_row
            WHEN NOT evaluated_any THEN 'EVALUATION:' || last_row
            WHEN c_settlement = 'N' THEN 'SETTLEMENT:' || coalesce(nullif(sett_codes, ''), last_refusal)
            WHEN c_probability = 'N' THEN 'PROBABILITY:' || coalesce(nullif(prob_codes, ''), 'NO_PROBABILITY')
            WHEN n_dec = 0 THEN 'DEREK_EVALUATED:valuation recorded, no Derek decision'
            WHEN n_enter = 0 THEN 'VERDICT:REFUSE:' || coalesce(last_refusal, 'UNNAMED')
            WHEN coalesce(n_entry_orders, 0) = 0 THEN 'PAPER_ORDER:ENTER without an ENTRY order'
            WHEN coalesce(n_fills, 0) = 0 THEN 'PAPER_FILL:order not filled'
            ELSE '-' END AS first_blocker,
       CASE WHEN market_slug IS NULL THEN 'COVERAGE_INCIDENT'
            WHEN provider_event_id IS NULL THEN 'UNAVAILABLE_AT_PROVIDER'
            WHEN NOT evaluated_any AND last_row LIKE 'REFUSED:%' THEN 'REFUSED_BEFORE_VALUATION'
            WHEN NOT evaluated_any THEN 'COVERAGE_INCIDENT'
            WHEN n_dec = 0 THEN 'EVALUATED_NOT_DECIDED'
            WHEN n_enter = 0 THEN 'REFUSING_BY_POLICY'
            WHEN coalesce(n_fills, 0) > 0 THEN 'FILLED'
            WHEN coalesce(n_entry_orders, 0) > 0 THEN 'ORDERED_NOT_FILLED'
            ELSE 'COVERAGE_INCIDENT' END AS status,
       provider_events_for_game AS prov_events_n, cycles, ledger_rows,
       to_char(first_cycle AT TIME ZONE 'UTC', 'MM-DD HH24:MI') AS first_cycle_utc,
       to_char(last_cycle AT TIME ZONE 'UTC', 'MM-DD HH24:MI') AS last_cycle_utc,
       raw_stage, funnel_reach, last_row AS ledger_last,
       n_val, n_prob, last_purpose, left(coalesce(last_refusals, ''), 300) AS val_refusals,
       d_strategy, left(coalesce(d_refusals, ''), 300) AS derek_refusals,
       to_char(last_dec_at AT TIME ZONE 'UTC', 'MM-DD HH24:MI') AS last_dec_utc
  FROM o
 ORDER BY ko NULLS LAST, title;

\echo '== N2 · NFL per game: ledger refusal histogram since 2026-10-01 (stage:first_refusal -> cycles) =='
SELECT coalesce(us_market_slug, '(no slug) ' || coalesce(away, '?') || ' at ' || coalesce(home, '?')) AS game,
       provider_event_id, outcome || coalesce(':' || stage, '') || coalesce(':' || first_refusal, '') AS outcome_stage_refusal,
       count(*) AS n_rows, min(cycle_at) AS first_at, max(cycle_at) AS last_at
  FROM ext_candidate_outcomes
 WHERE sport_key = 'americanfootball_nfl' AND cycle_at >= timestamptz '2026-10-01 00:00Z'
 GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC LIMIT 120;

\echo '== N3 · NFL Derek decisions since 2026-10-02: verdict x refusal x refusals =='
SELECT strategy, verdict, coalesce(refusal, '') AS refusal, array_to_string(refusals, ',') AS refusals,
       count(*) AS n, count(DISTINCT us_market_slug) AS games, max(decided_at) AS last_at
  FROM paper_decisions
 WHERE decided_at >= timestamptz '2026-10-02 00:00Z' AND us_market_slug LIKE 'aec-nfl-%'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 40;

\echo '== A1 · ALL SPORTS: provider ledger per league key, ET day (cycle_at) =='
WITH r AS (
    SELECT sport_key, family, provider_event_id, first_refusal, stage, us_market_slug, outcome,
           CASE WHEN outcome IN ('ADMITTED', 'ALREADY_RECORDED') THEN 99
                WHEN outcome = 'REFUSED' AND stage ~ '^[1-8]_' THEN
                     greatest(substr(stage, 1, 1)::int, CASE WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END)
                WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END AS reach
      FROM ext_candidate_outcomes
     WHERE cycle_at >= timestamptz '2026-10-04 04:00Z' AND cycle_at < timestamptz '2026-10-05 04:00Z'
       AND provider_event_id IS NOT NULL),
e AS (SELECT sport_key, max(family) AS family, provider_event_id, max(reach) AS reach,
             bool_or(reach = 3 AND coalesce(first_refusal, '') NOT IN (
                 'VENUE_DOES_NOT_LIST_THIS_FIXTURE', 'NO_PREMAP_CONTRACT_FOR_THIS_FIXTURE',
                 'NO_VENUE_NATIVE_EVENT_FOR_FIXTURE', 'NO_VENUE_CONTRACT_FOR_EVENT',
                 'NO_VENUE_NATIVE_CONTRACT_IN_PREMAP')) AS found_at_3
        FROM r GROUP BY 1, 3)
SELECT sport_key AS league, max(family) AS family, count(*) AS provider_events,
       count(*) FILTER (WHERE reach >= 3) AS normalized,
       count(*) FILTER (WHERE reach >= 4 OR found_at_3) AS venue_discovered,
       count(*) FILTER (WHERE reach >= 4) AS mapped,
       count(*) FILTER (WHERE reach >= 5) AS settlement_reach,
       (SELECT jsonb_object_agg(k, n) FROM (
            SELECT coalesce(r2.stage, '') || ':' || coalesce(r2.first_refusal, r2.outcome) AS k,
                   count(DISTINCT r2.provider_event_id) AS n
              FROM r r2 WHERE r2.sport_key = e.sport_key
             GROUP BY 1 ORDER BY 2 DESC LIMIT 6) t) AS top_outcomes
  FROM e GROUP BY sport_key ORDER BY 3 DESC;

\echo '== A2 · ALL SPORTS: evaluated / decided / enter / orders / fills per league, ET day =='
WITH m AS (
    SELECT DISTINCT ON (provider_event_id) provider_event_id, sport_key
      FROM ext_candidate_outcomes
     WHERE provider_event_id IS NOT NULL
       AND cycle_at >= timestamptz '2026-09-20 00:00Z'
     ORDER BY provider_event_id, cycle_at DESC),
v AS (
    SELECT ev.id, coalesce(m.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, '?') || ':' ||
                           lower(split_part(coalesce(ev.us_market_slug, ''), '-', 2))) AS league,
           coalesce(ev.event_key, ev.id::text) AS ek, ev.probability, ev.us_market_slug
      FROM external_valuations ev
      LEFT JOIN m ON m.provider_event_id = ev.event_key
     WHERE ev.record_purpose IN ('ENTRY_DECISION', 'CALIBRATION_ONLY')
       AND ev.decided_at >= timestamptz '2026-10-04 04:00Z' AND ev.decided_at < timestamptz '2026-10-05 04:00Z'),
d AS (
    SELECT coalesce(m.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, '?') || ':' ||
                    lower(split_part(coalesce(pd.us_market_slug, ''), '-', 2))) AS league,
           coalesce(ev.event_key, pd.us_market_slug) AS ek, bool_or(pd.verdict = 'ENTER') AS entered,
           (array_agg(pd.refusal ORDER BY pd.decided_at DESC))[1] AS refusal
      FROM paper_decisions pd
      JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
     WHERE pd.decided_at >= timestamptz '2026-10-04 04:00Z' AND pd.decided_at < timestamptz '2026-10-05 04:00Z'
     GROUP BY 1, 2),
po AS (
    SELECT coalesce(m.sport_key, 'UNATTRIBUTED') AS league,
           count(DISTINCT coalesce(ev.event_key, o.us_market_slug)) AS ordered
      FROM paper_orders o
      JOIN paper_decisions pd ON pd.decision_id = o.decision_id
      JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
     WHERE o.role = 'ENTRY'
       AND o.created_at >= timestamptz '2026-10-04 04:00Z' AND o.created_at < timestamptz '2026-10-05 04:00Z'
     GROUP BY 1),
pf AS (
    SELECT coalesce(m.sport_key, 'UNATTRIBUTED') AS league,
           count(DISTINCT coalesce(ev.event_key, f.us_market_slug)) AS filled
      FROM paper_fills f
      JOIN paper_orders o ON o.order_id = f.order_id
      JOIN paper_decisions pd ON pd.decision_id = o.decision_id
      JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
     WHERE o.role = 'ENTRY'
       AND f.filled_at >= timestamptz '2026-10-04 04:00Z' AND f.filled_at < timestamptz '2026-10-05 04:00Z'
     GROUP BY 1),
lv AS (SELECT league, count(DISTINCT ek) AS evaluated, count(DISTINCT ek) FILTER (WHERE probability IS NOT NULL) AS with_prob
         FROM v GROUP BY 1),
ld AS (SELECT league, count(*) AS decided, count(*) FILTER (WHERE entered) AS entered,
              count(*) FILTER (WHERE NOT entered) AS refused,
              (SELECT jsonb_object_agg(coalesce(refusal, 'NULL'), n) FROM (
                   SELECT refusal, count(*) AS n FROM d d2 WHERE d2.league = d.league AND NOT d2.entered
                    GROUP BY 1 ORDER BY 2 DESC LIMIT 4) t) AS refusals
         FROM d GROUP BY league)
SELECT coalesce(lv.league, ld.league, po.league, pf.league) AS league,
       coalesce(lv.evaluated, 0) AS evaluated, coalesce(lv.with_prob, 0) AS with_prob,
       coalesce(ld.decided, 0) AS decided, coalesce(ld.entered, 0) AS entered,
       coalesce(ld.refused, 0) AS refused, coalesce(po.ordered, 0) AS ordered,
       coalesce(pf.filled, 0) AS filled, ld.refusals
  FROM lv FULL JOIN ld ON ld.league = lv.league
  FULL JOIN po ON po.league = coalesce(lv.league, ld.league)
  FULL JOIN pf ON pf.league = coalesce(lv.league, ld.league, po.league)
 ORDER BY 2 DESC, 4 DESC;

\echo '== A3 · VENUE BOARD: every league token listed with a game on the ET day, by family =='
SELECT lower(split_part(coalesce(event_slug, ''), '-', 1)) AS token,
       mode() WITHIN GROUP (ORDER BY split_part(coalesce(sports_type, ''), '_', 1)) AS family,
       count(DISTINCT event_slug) AS events_et_day,
       count(DISTINCT event_slug) FILTER (WHERE game_start >= now()) AS events_not_started,
       count(DISTINCT market_slug) AS contracts
  FROM us_premap
 WHERE game_start >= timestamptz '2026-10-04 04:00Z' AND game_start < timestamptz '2026-10-05 04:00Z'
   AND event_slug IS NOT NULL
 GROUP BY 1 ORDER BY 2, 3 DESC LIMIT 150;

\echo '== I1 · persisted coverage_funnel_snapshots, 2026-10-04, both timezones =='
SELECT tz, league, sport_family AS fam, provider_events AS prov, normalized_events AS norm,
       venue_discovered AS disc, mapped_events AS mapped, settlement_supported AS settle,
       evaluated_events AS eval, decided_events AS decided, entered_events AS entered,
       refused_events AS refused, ordered_events AS ordered, filled_events AS filled,
       venue_catalogue_events AS venue, unavailable::text AS unavailable,
       to_char(computed_at AT TIME ZONE 'UTC', 'MM-DD HH24:MI') AS computed_utc
  FROM coverage_funnel_snapshots
 WHERE day = date '2026-10-04'
 ORDER BY tz, provider_events DESC NULLS LAST, league;

\echo '== I2 · coverage_collapse_alerts since 2026-10-02 (with the Audrey finding each wrote) =='
SELECT a.day, a.tz, a.league, a.kind, a.stage_from, a.stage_to, a.severity,
       a.audrey_finding_id, a.audrey_refusal, a.detail->>'coverage_status' AS coverage_status,
       left(a.detail->>'statement', 160) AS statement,
       to_char(a.detected_at AT TIME ZONE 'UTC', 'MM-DD HH24:MI') AS detected_utc,
       f.kind AS finding_kind, f.severity AS finding_severity,
       to_char(f.found_at AT TIME ZONE 'UTC', 'MM-DD HH24:MI') AS found_utc
  FROM coverage_collapse_alerts a
  LEFT JOIN paper_audrey_findings f ON f.finding_id = a.audrey_finding_id
 WHERE a.day >= date '2026-10-02'
 ORDER BY a.detected_at DESC LIMIT 60;

\echo '== I3 · Audrey COVERAGE_COLLAPSE findings since 2026-10-02 =='
SELECT finding_id, subject, severity, detail->>'stage_to' AS stage_to,
       detail->>'kind' AS kind, detail->>'day' AS day,
       left(detail->>'statement', 160) AS statement,
       to_char(found_at AT TIME ZONE 'UTC', 'MM-DD HH24:MI') AS found_utc
  FROM paper_audrey_findings
 WHERE kind = 'COVERAGE_COLLAPSE' AND found_at >= timestamptz '2026-10-02 00:00Z'
 ORDER BY found_at DESC LIMIT 60;

\echo '== I4 · coverage_integrity watermark (last pass) =='
SELECT value AS watermark,
       to_timestamp((value->>'at')::float8) AS last_pass_at,
       round(extract(epoch FROM now()) - (value->>'at')::float8) AS age_s
  FROM ingestion_state WHERE key = 'coverage_integrity_last';
\echo '== F1 · NCAAF provider events seen in ET-day cycles: commence, furthest raw stage, slug, refusals =='
SELECT provider_event_id, max(away) || ' at ' || max(home) AS fixture,
       max(commence_time) AS commence_time,
       CASE WHEN max(commence_time) ~ '^[0-9]{4}-' THEN
            to_char(max(commence_time)::timestamptz AT TIME ZONE 'America/New_York', 'YYYY-MM-DD HH24:MI') END AS commence_et,
       max(us_market_slug) AS slug,
       max(CASE WHEN outcome IN ('ADMITTED', 'ALREADY_RECORDED') THEN 99
                WHEN outcome = 'REFUSED' AND stage ~ '^[1-8]_' THEN substr(stage, 1, 1)::int
                ELSE 0 END) AS raw_stage,
       count(*) AS rows_, min(cycle_at) AS first_cycle, max(cycle_at) AS last_cycle,
       (SELECT jsonb_object_agg(k, n) FROM (
            SELECT coalesce(o2.stage, '') || ':' || coalesce(o2.first_refusal, o2.outcome) AS k, count(*) AS n
              FROM ext_candidate_outcomes o2
             WHERE o2.provider_event_id = o.provider_event_id AND o2.sport_key = 'americanfootball_ncaaf'
               AND o2.cycle_at >= timestamptz '2026-10-04 04:00Z' AND o2.cycle_at < timestamptz '2026-10-05 04:00Z'
             GROUP BY 1) t) AS outcomes
  FROM ext_candidate_outcomes o
 WHERE sport_key = 'americanfootball_ncaaf' AND provider_event_id IS NOT NULL
   AND cycle_at >= timestamptz '2026-10-04 04:00Z' AND cycle_at < timestamptz '2026-10-05 04:00Z'
 GROUP BY provider_event_id
 ORDER BY max(us_market_slug) NULLS LAST, max(commence_time);

\echo '== F2 · NCAAF valuations and Derek decisions with decided_at in the ET day =='
SELECT ev.id, ev.us_market_slug, ev.event_key, ev.record_purpose, ev.decided_at,
       left(array_to_string(ev.refusals, ','), 200) AS refusals
  FROM external_valuations ev
 WHERE ev.decided_at >= timestamptz '2026-10-03 04:00Z'
   AND (ev.us_market_slug LIKE '%-cfb-%' OR ev.event_key IN (
        SELECT provider_event_id FROM ext_candidate_outcomes
         WHERE sport_key = 'americanfootball_ncaaf' AND cycle_at >= timestamptz '2026-10-03 04:00Z'))
 ORDER BY ev.decided_at DESC LIMIT 40;

\echo '== F3 · Derek-only verdicts per league token, ET day =='
SELECT lower(split_part(coalesce(us_market_slug, ''), '-', 2)) AS token,
       count(DISTINCT us_market_slug) AS contracts_decided,
       count(DISTINCT us_market_slug) FILTER (WHERE verdict = 'ENTER') AS contracts_enter,
       (array_agg(DISTINCT refusal) FILTER (WHERE refusal IS NOT NULL))[1:5] AS refusals
  FROM paper_decisions
 WHERE strategy = 'DEREK_ENTRY_POLICY_V2'
   AND decided_at >= timestamptz '2026-10-04 04:00Z' AND decided_at < timestamptz '2026-10-05 04:00Z'
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== F4 · paper orders / fills per league token and strategy, ET day =='
SELECT lower(split_part(o.us_market_slug, '-', 2)) AS token, pd.strategy, o.role,
       count(DISTINCT o.order_id) AS orders,
       count(DISTINCT f.fill_id) AS fills
  FROM paper_orders o
  LEFT JOIN paper_decisions pd ON pd.decision_id = o.decision_id
  LEFT JOIN paper_fills f ON f.order_id = o.order_id
 WHERE o.created_at >= timestamptz '2026-10-04 04:00Z' AND o.created_at < timestamptz '2026-10-05 04:00Z'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo '== F5 · snapshots behind the 10-02 / 10-03 alerts (ET) =='
SELECT day, league, provider_events AS prov, normalized_events AS norm, venue_discovered AS disc,
       mapped_events AS mapped, settlement_supported AS settle, evaluated_events AS eval,
       decided_events AS decided, entered_events AS entered, final,
       to_char(computed_at AT TIME ZONE 'UTC', 'MM-DD HH24:MI') AS computed_utc
  FROM coverage_funnel_snapshots
 WHERE tz = 'America/New_York' AND day IN (date '2026-10-02', date '2026-10-03')
   AND provider_events > 0
 ORDER BY day, league;

\echo '== F6 · the NCAAF 10-04 alert row in full =='
SELECT alert_id, stage_from, stage_to, severity, detail::text AS detail, audrey_finding_id,
       detected_at, recorded_at
  FROM coverage_collapse_alerts
 WHERE day = date '2026-10-04';
