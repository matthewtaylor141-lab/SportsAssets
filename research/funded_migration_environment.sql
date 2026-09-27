-- WHAT IS DIFFERENT ABOUT THIS SERVER, since the file itself is not.
--
-- Migration 126 applies cleanly on a fresh database and again against a
-- database carrying exactly production's pre-state (125 applied, both
-- dependent indexes present, zero rows), in one asyncpg simple-query batch,
-- inside the runner's own outer transaction. Production raised
-- `function bettor_funded_order_is_outstanding(text) does not exist` while
-- applying that same byte-identical file -- the file has only ever had one
-- version in history, and the deployed commit carries it.
--
-- Parsing is deterministic, so what remains is the server: its version and
-- the settings this role and database actually run with. This asks for those,
-- including any per-role or per-database setting that a session-level read
-- would never show.
--
-- READ-ONLY.

\echo == 1 . THE SERVER, AND THE SETTINGS THAT GOVERN FUNCTION VALIDATION ==
SELECT version()                                        AS server,
       current_setting('server_version_num')            AS version_num,
       current_setting('check_function_bodies')          AS check_function_bodies,
       current_setting('search_path')                    AS search_path,
       current_setting('standard_conforming_strings')    AS std_strings,
       current_setting('transform_null_equals')          AS transform_null_equals;

\echo
\echo == 2 . EVERY PER-ROLE AND PER-DATABASE SETTING, which a session read hides ==
SELECT coalesce(r.rolname, '(all roles)') AS role_name,
       coalesce(d.datname, '(all databases)') AS database_name,
       s.setconfig
  FROM pg_db_role_setting s
  LEFT JOIN pg_roles r ON r.oid = s.setrole
  LEFT JOIN pg_database d ON d.oid = s.setdatabase
 ORDER BY role_name, database_name;

\echo
\echo == 3 . WHICH SCHEMAS EXIST, and whether one is named after the role ==
SELECT n.nspname AS schema_name,
       pg_get_userbyid(n.nspowner) AS owner,
       (n.nspname = current_user) AS is_the_dollar_user_schema
  FROM pg_namespace n
 WHERE n.nspname NOT LIKE 'pg_%'
 ORDER BY n.nspname;

\echo
\echo == 4 . WHAT DEPENDS ON THE ONE FUNDED FUNCTION THAT ALREADY EXISTS ==
SELECT d.classid::regclass AS dependent_kind,
       d.objid             AS dependent_oid,
       pg_describe_object(d.classid, d.objid, d.objsubid) AS dependent_object,
       d.deptype
  FROM pg_depend d
 WHERE d.refobjid = (
         SELECT p.oid FROM pg_proc p
           JOIN pg_namespace n ON n.oid = p.pronamespace
          WHERE p.proname = 'bettor_funded_intent_is_live'
          LIMIT 1)
 ORDER BY dependent_object;

\echo
\echo == 5 . AND WHAT THIS ROLE IS ACTUALLY PERMITTED IN public ==
--
-- The privilege list is printed RAW rather than asked about by name: the
-- surface's read-only guard refuses the word that names the privilege, and
-- weakening a guard to read a diagnostic is the wrong trade. The ACL says the
-- same thing, and `pg_roles` says whether the role needs it.
SELECT current_user                    AS whoami,
       n.nspname                       AS schema_name,
       pg_get_userbyid(n.nspowner)     AS schema_owner,
       n.nspacl                        AS privilege_list,
       (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)
                                       AS is_superuser
  FROM pg_namespace n
 WHERE n.nspname = 'public';
