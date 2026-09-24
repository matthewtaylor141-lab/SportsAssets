-- AUTHORITATIVE FIXTURE METADATA, PERSISTED WITH ITS PROVENANCE.
--
-- The settlement scope guard needs the COMPETITION PHASE and the GAME
-- FORMAT, and the quote-context rule needs ACTUAL EVENT STATE rather than a
-- scheduled start. All three are published per game by the league; none of
-- them is on `markets`. That was an integration gap, and this table closes
-- it.
--
-- EVERY ROW CARRIES WHAT WOULD LET A READER CHECK IT: the source and the URL
-- it came from, the instant it was retrieved, and the FIXTURE BINDING
-- (gamePk, official date, both team names) so the row can be compared with
-- the fixture it claims to describe instead of trusted. `refusals` records
-- the values the reader would not map -- an undeclared game type or state --
-- so an unusable row is visibly unusable rather than absent.
--
-- IT IS NOT A CACHE OF A DECISION INPUT. The decision path READS this table
-- and never fetches, so no outbound call sits inside a decision; acquisition
-- is an explicit, separate action.

CREATE TABLE IF NOT EXISTS fixture_metadata (
    condition_id       text        PRIMARY KEY,
    -- what the guards asked for
    phase              text,
    phase_uncovered    text,
    game_format        text,
    scheduled_innings  integer,
    play_has_begun     boolean,
    event_state_raw    text,
    abstract_state     text,
    actual_start_at    timestamptz,
    start_evidence     text,
    terminal_hint      text,
    -- the fixture binding
    game_pk            bigint,
    official_date      date,
    home_team          text,
    away_team          text,
    game_number        integer,
    double_header      text,
    -- the provenance
    source             text        NOT NULL,
    source_url         text        NOT NULL,
    retrieved_at       timestamptz NOT NULL,
    reader_version     text        NOT NULL,
    refusals           jsonb       NOT NULL DEFAULT '[]'::jsonb,
    raw                jsonb,
    written_at         timestamptz NOT NULL DEFAULT now()
);

-- A fixture is looked up by condition, and audited by game.
CREATE INDEX IF NOT EXISTS fixture_metadata_game_pk
    ON fixture_metadata (game_pk);
CREATE INDEX IF NOT EXISTS fixture_metadata_retrieved_at
    ON fixture_metadata (retrieved_at DESC);
