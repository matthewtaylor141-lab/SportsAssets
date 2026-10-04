-- Candidate 22 runtime readback 3 (read-only): stream evidence, same-book probe, NCAAF events, Xavier freshness.
\echo '== S1 · stream evidence process rows since 02:44Z =='
SELECT service, process_id, stream_state, connection_id, connection_epoch, connects_total, reconnects_total,
       max(recorded_at) AS last_at, count(*) AS rows
  FROM institutional_stream_evidence WHERE symbol = '*' AND recorded_at > '2026-10-04 02:44:00+00'
 GROUP BY 1,2,3,4,5,6,7 ORDER BY last_at DESC LIMIT 6;
\echo '== S2 · per-symbol evidence (latest per symbol) =='
SELECT DISTINCT ON (symbol) symbol, recorded_at, connection_epoch, gap_open, gap_events, regressions_total,
       round(receipt_age_s::numeric,3) AS receipt_age_s, round(venue_receipt_skew_s::numeric,3) AS skew_s,
       updates_in_minute, max_interarrival_s, instrument_state, current_ok, current_refusal,
       left(top_n::text, 160) AS top
  FROM institutional_stream_evidence WHERE symbol <> '*' AND recorded_at > '2026-10-04 02:44:00+00'
 ORDER BY symbol, recorded_at DESC LIMIT 20;
\echo '== S3 · receipt-age distribution per symbol (all minutes since 02:44Z) =='
SELECT symbol, count(*) AS minutes, sum(CASE WHEN current_ok THEN 1 ELSE 0 END) AS current_ok_minutes,
       round(min(receipt_age_s)::numeric,3) AS min_age, round(percentile_cont(0.5) WITHIN GROUP (ORDER BY receipt_age_s)::numeric,3) AS p50_age,
       sum(updates_in_minute) AS updates
  FROM institutional_stream_evidence WHERE symbol <> '*' AND recorded_at > '2026-10-04 02:44:00+00' GROUP BY 1 ORDER BY 1 LIMIT 20;
\echo '== S4 · same-book probe verdicts =='
SELECT verdict, verdict_reason, count(*) AS n, sum(orders_placed) AS orders_placed, min(probed_at), max(probed_at)
  FROM institutional_same_book_probe GROUP BY 1,2 ORDER BY 3 DESC LIMIT 10;
SELECT probed_at, symbol, retail_slug, identity_ok, identity_refusal, stream_ok, stream_refusal, retail_ok, retail_error,
       within_window, verdict, best_bid_equal, best_offer_equal, left(diff::text,200) AS diff
  FROM institutional_same_book_probe ORDER BY probed_at DESC LIMIT 6;
\echo '== N1 · NCAAF decisions (cfb), sample events =='
SELECT d.decided_at, d.strategy, d.verdict, d.refusal, d.us_market_slug, d.label->>'event_title' AS title
  FROM paper_decisions d WHERE d.us_market_slug LIKE '%-cfb-%' ORDER BY d.decided_at DESC LIMIT 10;
SELECT count(*) AS cfb_decisions, count(DISTINCT us_market_slug) AS cfb_contracts,
       sum(CASE WHEN verdict='ENTER' THEN 1 ELSE 0 END) AS enter_n FROM paper_decisions WHERE us_market_slug LIKE '%-cfb-%';
SELECT count(*) AS cfb_paper_orders FROM paper_orders o JOIN paper_decisions d ON d.decision_id = o.decision_id WHERE d.us_market_slug LIKE '%-cfb-%';
\echo '== N2 · NCAAF provider-side outcomes (ext_candidate_outcomes, last 24 h) =='
SELECT stage, outcome, first_refusal, count(*) AS n, count(DISTINCT provider_event_id) AS events, max(recorded_at) AS last_at FROM ext_candidate_outcomes
 WHERE recorded_at > now() - interval '24 hours' AND sport_key = 'americanfootball_ncaaf'
 GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 12;
\echo '== X1 · Xavier assessments since 718b532 by trigger and evidence state =='
SELECT position_kind, trigger, evidence_state, count(*) AS n, round(min(probability_age_s)::numeric,1) AS min_age,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY probability_age_s)::numeric,1) AS p50_age, max(assessed_at) AS last_at
  FROM xavier_management_assessments WHERE assessed_at > '2026-10-04 00:19:37+00' GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 15;
\echo '== X2 · latest assessment per open paper group =='
SELECT DISTINCT ON (a.group_id) a.group_id, a.assessed_at, a.trigger, a.evidence_state, round(a.probability_age_s::numeric,1) AS prob_age_s,
       a.probability_source, a.recommendation, a.thesis_state
  FROM xavier_management_assessments a WHERE a.assessed_at > now() - interval '30 minutes'
 ORDER BY a.group_id, a.assessed_at DESC LIMIT 20;
\echo '== X3 · fresh reviews since deploy: examples =='
SELECT assessed_at, group_id, trigger, round(probability_age_s::numeric,1) AS age, probability_source, recommendation
  FROM xavier_management_assessments WHERE evidence_state = 'FRESH_CURRENT_PROBABILITY' AND assessed_at > '2026-10-04 00:19:37+00'
 ORDER BY assessed_at DESC LIMIT 8;
