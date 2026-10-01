-- READ-ONLY. THE VENUE-NATIVE SOCCER EVENTS THAT MATCHED ONLY ONE TEAM:
-- the provider's two names against the venue's two participants, from the
-- latest cycle record, so an alias gap can be told from a genuine mismatch.
\echo '== P1 · partial matches in the latest cycle record =='
SELECT p.value AS partial_match
  FROM ingestion_state s,
       jsonb_path_query(s.value, 'strict $.**.partial_matches[*]') p(value)
 WHERE s.key = 'ext_pinnacle_last_cycle'
 LIMIT 40;
\echo '== P2 · one-team refusals with their provider names =='
SELECT r.value AS refusal_record
  FROM ingestion_state s,
       jsonb_path_query(s.value,
         'strict $.** ? (@.refusal == "VENUE_NATIVE_EVENT_MATCHES_ONLY_ONE_TEAM")') r(value)
 WHERE s.key = 'ext_pinnacle_last_cycle'
 LIMIT 20;
