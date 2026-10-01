-- READ-ONLY. WHY DOES THE INVESTMENT POLICY REFUSE aec-mlb-sd-mil-2026-10-03
-- AS BELOW_MIN_GROSS_EDGE AT A 0.82 pp BEST-LEVEL GROSS EDGE?
-- T1 its completed-game decisions before and after the 20:39Z release
-- T2 the attempt rows (book source) for those valuations
-- T3 the books those decisions used (source, levels)
\echo '== T1 · completed-game decisions on sd-mil (last 8 h) =='
SELECT d.decision_id, d.decided_at, d.valuation_id, d.holding_side, d.verdict,
       d.refusal, d.refusals, d.p_pinnacle, d.book_obs_id, d.policy_version,
       left(d.economics::text, 900) AS economics,
       left((d.policy_decision)::text, 700) AS policy_decision
  FROM paper_decisions d
 WHERE d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND d.us_market_slug = 'aec-mlb-sd-mil-2026-10-03'
   AND d.decided_at > now() - interval '8 hours'
 ORDER BY d.decided_at DESC LIMIT 14;

\echo '== T2 · attempts on those valuations =='
SELECT a.at, a.valuation_id, a.strategy, a.via, a.outcome, a.verdict,
       a.refusal, a.book_source, a.book_age_s, a.cooldown_s
  FROM paper_evaluation_attempts a
 WHERE a.valuation_id IN (SELECT valuation_id FROM paper_decisions
                           WHERE us_market_slug = 'aec-mlb-sd-mil-2026-10-03'
                             AND decided_at > now() - interval '3 hours')
 ORDER BY a.at DESC LIMIT 20;

\echo '== T3 · the books those decisions used =='
SELECT b.obs_id, b.observed_at, b.source, b.read_basis, b.error,
       left(b.offers::text, 300) AS offers, left(b.bids::text, 300) AS bids, b.venue_ts
  FROM paper_book_observations b
 WHERE b.obs_id IN (SELECT book_obs_id FROM paper_decisions
                     WHERE us_market_slug = 'aec-mlb-sd-mil-2026-10-03'
                       AND decided_at > now() - interval '8 hours')
 ORDER BY b.observed_at DESC LIMIT 8;
