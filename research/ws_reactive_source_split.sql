-- Paper decisions in the last 24 h by valuation source (read-only): does the
-- WebSocket primary source reach ENTER at all, and what refuses it?
\echo '== S1 · decisions by strategy, valuation provider, verdict and refusal (24 h) =='
SELECT d.strategy, v.provider, d.verdict, coalesce(d.refusal, '-') AS refusal,
       count(*) AS decisions, max(d.decided_at) AS latest
  FROM paper_decisions d JOIN external_valuations v ON v.id = d.valuation_id
 WHERE d.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 5 DESC;
\echo '== S2 · valuations by provider and whether they carry the outcome-depth refusal (24 h) =='
SELECT provider, record_purpose, sport_family,
       count(*) AS valuations,
       count(*) FILTER (WHERE 'OUTCOME_DEPTH_BELOW_FLOOR' = ANY(refusals)) AS thin_outcome,
       count(*) FILTER (WHERE 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED' = ANY(refusals)) AS book_currency_refused,
       max(observed_at) AS latest
  FROM external_valuations WHERE observed_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;
\echo '== S3 · outcome_books recorded on WS-primary valuations (24 h, sample) =='
SELECT id, observed_at, sport_family, outcome_books::text AS outcome_books,
       left(refusals::text, 200) AS refusals
  FROM external_valuations
 WHERE observed_at > now() - interval '24 hours' AND provider = 'pinnapi.com/raw-websocket'
 ORDER BY id DESC LIMIT 10;
