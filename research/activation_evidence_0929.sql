-- READ-ONLY. ITEM 7: THE ACCOUNT, ITS AUTHORIZATION, THE FUNDED BOOK AND THE
-- LAST SCHEDULED CYCLE -- read from production, not taken from a report.
--
-- WHY THIS EXISTS. The activation package has to state the account's
-- reconciliation status, the exact enforced limits and the authorization that
-- is on record. A checkpoint reported them as of an earlier session; this reads
-- them now, from the rows the gates themselves read.
--
-- WHAT IT DELIBERATELY DOES NOT READ. No balance, cash, buying power or equity
-- -- `bettor_funded_activation.REGISTRY_FORBIDDEN_FIELDS` names them and this
-- follows it. No credential of any kind: the activation records hold account
-- identifiers, approved dollar limits, an expiry and a revocation flag, and the
-- query selects those by name rather than printing whole rows where it can.
--
-- TABLES THAT MAY NOT EXIST IN PRODUCTION (131-135 ship with the release under
-- test) are counted through `to_regclass` + `query_to_xml`, so a missing table
-- reports NULL instead of aborting every section after it.

\echo '== 1 · the canonical account registry: identity and state only =='
SELECT account_id, desk_id, status, paused,
       left(coalesce(pause_reason, ''), 160)               AS pause_reason,
       paused_at, accounting_status,
       left(coalesce(accounting_detail::text, ''), 400)     AS accounting_detail,
       last_verified_at,
       left(coalesce(last_verified_detail::text, ''), 400)  AS last_verified_detail,
       opened_at, closed_at
  FROM bettor_desk_accounts
 ORDER BY account_id;

\echo '== 2 · the three activation records the funded gates read =='
SELECT key,
       length(value::text)                                  AS bytes,
       left(value::text, 1200)                              AS value
  FROM ingestion_state
 WHERE key IN ('bettor_funded_account_binding',
               'bettor_funded_limits_approved',
               'bettor_funded_authorization')
 ORDER BY key;

\echo '== 3 · which funded tables exist in this database =='
SELECT t AS table_name, to_regclass(t) IS NOT NULL AS present
  FROM unnest(ARRAY[
        'bettor_funded_intents', 'bettor_funded_fills',
        'bettor_funded_economics', 'bettor_funded_discrepancies',
        'bettor_funded_portfolio_groups', 'bettor_funded_leg_reservations',
        'bettor_funded_decisions', 'bettor_funded_decision_outcomes',
        'bettor_funded_operation_evidence', 'bettor_funded_models',
        'bettor_funded_group_results']) AS t;

\echo '== 4 · the funded book, counted (NULL = table absent) =='
SELECT t AS table_name,
       CASE WHEN to_regclass(t) IS NOT NULL THEN
         (xpath('/row/c/text()',
                query_to_xml(format('select count(*) as c from %I', t),
                             false, true, '')))[1]::text::bigint
       END AS row_count
  FROM unnest(ARRAY[
        'bettor_funded_intents', 'bettor_funded_fills',
        'bettor_funded_portfolio_groups', 'bettor_funded_leg_reservations',
        'bettor_funded_decisions', 'bettor_funded_decision_outcomes',
        'bettor_funded_models']) AS t;

\echo '== 5 · states within the funded book (NULL = table absent) =='
SELECT t AS table_name,
       CASE WHEN to_regclass(t) IS NOT NULL THEN
         (xpath('/row/c/text()',
                query_to_xml(format(
                  'select coalesce(string_agg(s || '':'' || n, '', '' order by s), ''none'') as c '
                  'from (select state::text as s, count(*) as n from %I group by state) q', t),
                  false, true, '')))[1]::text
       END AS by_state
  FROM unnest(ARRAY['bettor_funded_intents', 'bettor_funded_leg_reservations',
                    'bettor_funded_models']) AS t;

\echo '== 6 · applied migrations from 125 up =='
SELECT version, applied_at
  FROM schema_migrations
 WHERE version >= '125'
 ORDER BY version;

\echo '== 7 · the last scheduled cycle and the entry-loop control =='
SELECT key,
       length(value::text)                                  AS bytes,
       left(value::text, 3500)                              AS value
  FROM ingestion_state
 WHERE key IN ('ext_pinnacle_last_cycle', 'ext_pinnacle_shadow')
 ORDER BY key;
