-- READ-ONLY. Institutional same-book evidence accrual after the PMX stream
-- came up (release 2514236): per verdict / reason in the last 2 h, the
-- per-symbol comparable counts in the 24 h window (SUPPORTED needs >= 30 at
-- >= 95% agreement, p5_runtime), and the latest held-mark refresh runs'
-- source counts. Every statement is a SELECT.
SELECT verdict, incomparable_reason, count(*) AS n,
       count(DISTINCT symbol) AS symbols, max(probed_at) AS newest
  FROM institutional_same_book_probe
 WHERE probed_at > now() - interval '2 hours'
 GROUP BY 1, 2 ORDER BY n DESC;

SELECT symbol,
       count(*) FILTER (WHERE verdict IN ('AGREE_TOP_N','AGREE_TOUCH_ONLY',
                                          'DISAGREE')) AS comparable,
       count(*) FILTER (WHERE verdict IN ('AGREE_TOP_N','AGREE_TOUCH_ONLY'))
           AS agree,
       count(*) FILTER (WHERE verdict = 'DISAGREE') AS disagree,
       count(*) AS samples
  FROM institutional_same_book_probe
 WHERE probed_at > now() - interval '24 hours' AND symbol IS NOT NULL
 GROUP BY 1 ORDER BY comparable DESC LIMIT 60;

SELECT run_id, started_at, institutional_books, stream_books,
       left(sources::text, 900) AS sources
  FROM paper_mark_refresh_runs
 ORDER BY started_at DESC LIMIT 5;
