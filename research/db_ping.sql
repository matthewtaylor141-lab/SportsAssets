-- READ-ONLY connectivity check: server time and version only.
\echo '== DB reachability =='
SELECT now() AS server_time, current_setting('server_version') AS version;
