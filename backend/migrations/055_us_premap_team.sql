-- 055: THE VENUE'S OWN TEAM FIELD ON us_premap (C6, 2026-09-07; the
-- college-football bucket, gap_cfb.md §0/§3 and the PREMAP-TEAM probe of
-- 14:15:54Z).
--
-- NUMBERING. 055: 053 is landed and 054 is W2's (the same day); 048/050/051 are the reserved numbers
-- of docs/mirror-to-a-tee-program.md:184 and 050 is landed too.
-- migrate.py applies the sorted glob, so the gap at 048/051 stays
-- harmless (052's and 053's headers say the same).
--
-- WHY. The sweep (workers/premap._market_rows) kept, per venue side,
-- only the normalised DESCRIPTION -- 'cardinals', 'rebels' -- and the
-- venue's SDK payload carries beside it a `team` dict the sweep
-- dropped: {abbreviation 'lou', safeName 'Louisville', name 'Cardinals',
-- id 1085, league 'cfb', ...} on every aec-cfb side (the probe lines
-- are quoted in docs/mirror-coverage.md, the C6 section). That dict is
-- the venue STATING, on the moneyline row itself, that the mascot at
-- this side is the school with this slug code -- the one binding no
-- stored row carried, which left every mascot-named spread at
-- spread:subject-uncertified and every cfb moneyline at
-- grammar_echo_unverified. Stored, the C4 subject step and the C1
-- grammar class read the binding from the venue's own row instead of
-- assuming the aec side order equals the slug's code order.
--
-- THE COLUMNS (the names are shared with the soccer builder's patch of
-- the same day so both merge; all nullable, unbackfilled: the sweep
-- rewrites every live row within one full cycle, exactly as 031 did
-- for `signed`, and a row not yet swept reads NULL, which every reader
-- takes as 'the venue stated nothing' and refuses):
--   team_abbr       team.abbreviation, lower-cased -- the slug code
--   team_name       team.name, folded as the club names are (the MASCOT
--                   on college rows: 'cardinals'; never read as the
--                   school)
--   team_safe_name  team.safeName, folded the same way -- the SCHOOL
--                   ('louisville', 'ole miss'); the field C6 certifies
--                   the subject from
--   team_id         teamId / team.id, the venue's numeric team id
--   team_league     team.league ('cfb')
--   game_start      market.gameStartTime, as the venue states it
--   sports_type     market.sportsMarketType
-- The atc yes/no rows carry team=null on both sides and write NULL.
--
-- Re-runnable: IF NOT EXISTS on every column, like 031.

ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS team_abbr text;
ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS team_name text;
ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS team_safe_name text;
ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS team_id bigint;
ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS team_league text;
ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS game_start timestamptz;
ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS sports_type text;
