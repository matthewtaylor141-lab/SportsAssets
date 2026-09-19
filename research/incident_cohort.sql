-- FREEZE THE MARKET COHORT, FROM PRE-CUTOFF ROWS ONLY.
--
-- Owner directive 2026-09-19: "From the 12 wallets' genuine activity
-- immediately before the common cutoff, freeze the exact CONDITION_IDS
-- ... Do not choose markets after seeing post-cutoff activity."
--
-- THE SELECTION CANNOT SEE THE ANSWER. The window is bounded ABOVE by
-- the frozen cutoff, so this query is structurally incapable of picking
-- a market because of what happened afterwards -- there is no predicate
-- here that could reach a post-cutoff row even if someone wanted it to.
-- :lookback_s is bound by the caller and is declared in the workflow's
-- inputs before the run, not chosen once the result is visible.
--
-- WHY CONDITION_ID AND NOT SLUG. The slug is a label the venue may
-- restyle; the condition id is the market's identity on chain. The slug
-- rides along only so a human reading the summary can see what these
-- markets were.
--
-- One pipe-delimited line per market: condition_id | slug | our rows in
-- the window | our newest ts in the window.
SELECT t.condition_id || '|'
       || COALESCE(NULLIF(t.market_slug, ''), 'NO_SLUG') || '|'
       || count(*)::text || '|'
       || max(t.ts)::text
  FROM trades t
  JOIN whales w ON w.id = t.whale_id
 WHERE w.active IS TRUE
   AND t.condition_id IS NOT NULL
   AND t.condition_id <> ''
   -- BOTH BOUNDS ARE PRE-CUTOFF. Nothing after the cutoff is visible to
   -- this statement at all.
   AND t.ts <= to_timestamp(:cutoff_epoch)
   AND t.ts >  to_timestamp(:cutoff_epoch) - (:lookback_s * interval '1 second')
 GROUP BY t.condition_id, t.market_slug
 -- Deterministic: newest first, then by id, so the cohort hash is
 -- reproducible across runs of the same window.
 ORDER BY max(t.ts) DESC, t.condition_id
 LIMIT :cohort_cap;
