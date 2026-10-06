-- READ-ONLY. The DISAGREE and RETAIL_BOOK_UNREADABLE same-book samples in
-- the last 60 min: what the two books said, the receipt ages, the window,
-- and the retail read error. Every statement is a SELECT.
SELECT probed_at, symbol, inst_best_bid, inst_best_ask, retail_best_bid,
       retail_best_ask, inst_receipt_age_s, retail_receipt_age_s,
       service, window_s, stream_changed_in_window, best_bid_equal,
       best_offer_equal, left(diff::text, 300) AS diff,
       left(stream_book::text, 300) AS stream_book,
       left(retail_book::text, 300) AS retail_book
  FROM institutional_same_book_probe
 WHERE probed_at > now() - interval '60 minutes' AND verdict = 'DISAGREE'
 ORDER BY probed_at DESC LIMIT 25;

SELECT service, left(retail_error, 160)
           AS err, count(*) AS n
  FROM institutional_same_book_probe
 WHERE probed_at > now() - interval '60 minutes'
   AND incomparable_reason = 'RETAIL_BOOK_UNREADABLE'
 GROUP BY 1, 2 ORDER BY n DESC LIMIT 15;

SELECT service, verdict, count(*) FROM institutional_same_book_probe
 WHERE probed_at > now() - interval '60 minutes' GROUP BY 1, 2 ORDER BY 1, 3 DESC;
