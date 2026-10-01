-- READ-ONLY. WHY DID A COMPLETED-GAME BOOK READ MISS ITS DECISION DEADLINE?
-- B1 each refusal with the recorded book observation's error text
-- B2 every other book observation in the same minute (contention)
\echo '== B1 · deadline refusals and their book observations =='
SELECT d.decided_at, d.valuation_id, d.us_market_slug, d.book_obs_id,
       b.observed_at, left(b.error, 300) AS book_error, b.source, b.read_basis,
       b.recorded_at
  FROM paper_decisions d
  LEFT JOIN paper_book_observations b ON b.obs_id = d.book_obs_id
 WHERE d.refusal = 'BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE'
 ORDER BY d.decided_at;
\echo '== B2 · book observations around each refusal (+/- 30 s) =='
SELECT d.valuation_id, b.obs_id, b.observed_at, b.us_market_slug,
       b.source, b.read_basis, left(coalesce(b.error, 'OK'), 120) AS result
  FROM paper_decisions d
  JOIN paper_book_observations b
    ON b.observed_at BETWEEN d.decided_at - interval '30 seconds'
                         AND d.decided_at + interval '30 seconds'
 WHERE d.refusal = 'BOOK_READ_DID_NOT_FINISH_INSIDE_THE_DECISION_DEADLINE'
 ORDER BY d.valuation_id, b.observed_at
 LIMIT 80;
