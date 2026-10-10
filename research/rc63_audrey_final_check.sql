\echo A1 AUDREY DAILY REPORTS by report day since 2026-10-06 (versions, finals, newest version, reconciles)
SELECT session_id, report_day, count(*) AS versions, count(*) FILTER (WHERE final) AS finals,
       max(version) AS newest_version, max(generated_at) AS newest_generated,
       bool_and(reconciles) AS all_reconcile,
       (array_agg(final ORDER BY version DESC))[1] AS newest_is_final
FROM paper_audrey_reports
WHERE report_day >= DATE '2026-10-06'
GROUP BY session_id, report_day ORDER BY report_day DESC, session_id;
\echo A2 FINAL VERSIONS EVER (all time)
SELECT report_day, version, generated_at, reconciles, final FROM paper_audrey_reports WHERE final ORDER BY generated_at DESC LIMIT 10;
\echo A3 COUNT of reports and finals all time
SELECT count(*) AS reports, count(*) FILTER (WHERE final) AS finals FROM paper_audrey_reports;
\echo A4 AUDREY PAPER FINDINGS since the RC6.3a deploy by kind
SELECT kind, severity, count(*), max(found_at) FROM paper_findings WHERE found_at >= TIMESTAMPTZ '2026-10-10 03:50:00+00' GROUP BY kind, severity ORDER BY 3 DESC LIMIT 20;
