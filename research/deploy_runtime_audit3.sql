-- READ-ONLY. Root-cause audit, group deploy-runtime, part 3: the production
-- server version (the upgrade-path gate builds on PostgreSQL 16) and the
-- timeouts a migration run inherits (the runner sets none itself). SELECT only.

\echo == 1 production server version
SELECT version();

\echo == 2 timeouts and limits in force for a fresh session
SELECT name, setting, unit, source
  FROM pg_settings
 WHERE name IN ('lock_timeout', 'statement_timeout', 'idle_in_transaction_session_timeout', 'server_version', 'max_connections')
 ORDER BY name;

\echo == 3 role and database level overrides of those settings
SELECT coalesce(r.rolname, 'ALL_ROLES') AS role_name, coalesce(d.datname, 'ALL_DATABASES') AS database_name, s.setconfig
  FROM pg_db_role_setting s
  LEFT JOIN pg_roles r ON r.oid = s.setrole
  LEFT JOIN pg_database d ON d.oid = s.setdatabase
 ORDER BY 1, 2;
