-- THE FUNDED LANE'S PRODUCTION STATE. Every statement is a SELECT.
--
-- Run through the authorized read-only route:
--   research-sql.yml  file=funded_state.sql  service=sportsassets-db
--
-- WHY HERE AND NOT IN render-ops.yml: that file holds the trading kill switch
-- and stands 422 bytes under GitHub's 512,000-byte workflow ceiling, which
-- `scripts/check_workflows.py` already fails it for. I added a statement to it
-- earlier today and took it over the ceiling; every dispatch returned
-- startup_failure until I reverted. research-sql exists for exactly this and
-- cannot write: its guard refuses any mutating keyword.
--
-- WHY NOT A NEW WORKFLOW: GitHub only dispatches a workflow that exists on the
-- DEFAULT branch, and the default branch here is protected and auto-deploying.
-- research-sql is already there, so a versioned .sql file on a feature branch
-- is the route that works without touching either.

\echo == 1 · WHICH FUNDED MIGRATIONS ARE APPLIED IN PRODUCTION ==
SELECT version, applied_at::timestamptz(0) AS applied
  FROM schema_migrations
 WHERE substring(version, 1, 3) IN
       ('125','126','127','128','131','132','133','134','135')
 ORDER BY version;

\echo == 2 · WHICH FUNDED OBJECTS EXIST -- a half-applied schema is not a lane ==
SELECT table_name
  FROM information_schema.tables
 WHERE table_schema = 'public' AND table_name LIKE 'bettor_funded%'
 ORDER BY table_name;

\echo == 3 · THE FUNDED BOOK BY STATE. An empty book is a result, not silence ==
SELECT coalesce(state, 'none')       AS state,
       coalesce(kind, 'none')        AS kind,
       count(*)                      AS n,
       round(sum(collateral_usd), 2) AS collateral_usd,
       round(sum(residual_qty), 2)   AS residual_qty
  FROM bettor_funded_intents
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo == 4 · FUNDED TOTALS ==
SELECT (SELECT count(*) FROM bettor_funded_intents)   AS intents,
       (SELECT count(*) FROM bettor_funded_fills)     AS fills,
       (SELECT count(*) FROM bettor_funded_economics) AS economic_events,
       (SELECT round(coalesce(sum(amount_usd), 0), 2)
          FROM bettor_funded_economics)               AS net_signed_usd;

\echo == 5 · THE ACCOUNT AND ITS RECONCILIATION STATUS ==
SELECT account_id, status, paused, accounting_status,
       paused_at::timestamptz(0)        AS paused_at,
       last_verified_at::timestamptz(0) AS last_verified_at,
       left(coalesce(pause_reason, ''), 110) AS pause_reason
  FROM bettor_desk_accounts
 ORDER BY opened_at;

\echo == 6 · THE ACCOUNTING INCIDENT AND ITS MEASURED DRIFT ==
SELECT incident_id, pnl_reliability,
       measured ->> 'identity_drift_usd' AS identity_drift_usd,
       preserved_counts
  FROM bettor_desk_incidents
 ORDER BY incident_id;

\echo == 7 · FUNDED AUTHORIZATION KEYS -- NAME AND SIZE ONLY, NEVER A VALUE ==
SELECT key, length(value::text) AS value_chars
  FROM ingestion_state
 WHERE key LIKE 'bettor_funded%'
 ORDER BY key;

\echo == 8 · EVERY LANE'S LAST WORD, NEWEST FIRST ==
SELECT service, status,
       beat_at::timestamptz(0) AS beat_at,
       extract(epoch FROM (now() - beat_at))::int AS age_s
  FROM service_heartbeats
 ORDER BY beat_at DESC
 LIMIT 15;

\echo == 9 · THE ENTRY LANE'S OWN FUNNEL -- what ends every candidate ==
SELECT eligibility,
       coalesce(ineligible_reason, '(none)') AS ineligible_reason,
       count(*)                              AS n,
       max(observed_at)::timestamptz(0)      AS newest
  FROM external_valuations
 GROUP BY 1, 2
 ORDER BY 3 DESC
 LIMIT 20;

\echo == 10 · AND WHETHER ANY FUNDED DECISION HAS EVER BEEN ADMITTED ==
SELECT count(*)                          AS valuations_total,
       count(*) FILTER (WHERE admissible) AS admissible,
       min(observed_at)::timestamptz(0)   AS oldest,
       max(observed_at)::timestamptz(0)   AS newest
  FROM external_valuations;
