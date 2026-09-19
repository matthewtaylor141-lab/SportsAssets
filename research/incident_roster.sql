-- the configured roster, for the detection-stall incident
--
-- NO \echo HERE, AND NO OTHER META-COMMAND. psql -t suppresses the
-- column headers but NOT \echo, so a banner line lands in the output
-- as though it were a row. It did, on 2026-09-19: the runner read it
-- as a 13th wallet, the probe step took the first line as its address,
-- and six candidate spellings all came back curl(3) malformed-URL --
-- which the job then honestly reported as "no spelling returned rows".
-- A cosmetic line turned into a false INCONCLUSIVE.
--
-- THE EXACT CONFIGURED ADDRESS, not the friendly label. The directive
-- is explicit about this, and identity mismatch is one of the named
-- INCONCLUSIVE outcomes -- so the address the ingestion actually
-- watches is the one the venue gets asked about.
--
-- One pipe-delimited line per wallet so the runner can read it without
-- parsing anything: address | label | our newest fill ts | our rows
-- after the frozen cutoff.
--
-- :cutoff_epoch is bound by the caller with psql -v, so the window is
-- the SAME frozen value the venue half uses. A window that drifted
-- between the two halves would make the comparison meaningless.
SELECT w.address || '|' || COALESCE(w.username, 'NO_LABEL') || '|'
       || COALESCE(max(t.ts)::text, 'NONE') || '|'
       || count(t.id) FILTER (
            WHERE t.ts > to_timestamp(:cutoff_epoch))::text
  FROM whales w
  LEFT JOIN trades t ON t.whale_id = w.id
                    AND t.ts > now() - interval '48 hours'
 WHERE w.active IS TRUE
 GROUP BY w.address, w.username
 ORDER BY count(t.id) DESC
 LIMIT 12;
