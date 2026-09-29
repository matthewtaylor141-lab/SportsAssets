-- down for 142. The attempts are a measurement record only -- nothing gates
-- on them -- so the table may be dropped; the rows go with it.
DROP TABLE IF EXISTS bettor_pair_observation_attempts;
DROP FUNCTION IF EXISTS bettor_pair_obs_attempt_is_append_only();
