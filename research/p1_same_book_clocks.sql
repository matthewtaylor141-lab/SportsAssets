-- READ-ONLY. Same-book samples: the stream's venue clock vs the retail
-- book's transactTime, by verdict (last 90 min). Every statement is a SELECT.
SELECT verdict, service,
       count(*) AS n,
       count(*) FILTER (WHERE (retail_book->>'transact_time')::timestamptz
                              = stream_venue_ts) AS same_instant,
       count(*) FILTER (WHERE (retail_book->>'transact_time')::timestamptz
                              > stream_venue_ts) AS retail_newer,
       count(*) FILTER (WHERE (retail_book->>'transact_time')::timestamptz
                              < stream_venue_ts) AS stream_newer,
       count(*) FILTER (WHERE retail_book->>'transact_time' IS NULL)
           AS retail_clock_null,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM
           (retail_book->>'transact_time')::timestamptz - stream_venue_ts)))
           AS median_retail_minus_stream_s
  FROM institutional_same_book_probe
 WHERE probed_at > now() - interval '90 minutes'
   AND verdict IN ('AGREE_TOP_N','AGREE_TOUCH_ONLY','DISAGREE')
 GROUP BY 1, 2 ORDER BY 1, 2;

SELECT verdict, symbol, stream_venue_ts,
       retail_book->>'transact_time' AS retail_ts, stream_received_at,
       retail_response_at
  FROM institutional_same_book_probe
 WHERE probed_at > now() - interval '90 minutes'
   AND verdict IN ('AGREE_TOP_N','AGREE_TOUCH_ONLY','DISAGREE')
 ORDER BY probed_at DESC LIMIT 20;
