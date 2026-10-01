-- FIXTURE EVIDENCE FOR A VENUE-NATIVE CONTRACT, UNDER ITS OWN KEY.
--
-- `fixture_metadata` (migration 115) is keyed by the GLOBAL condition id. A
-- contract whose identity came from the venue's own catalogue has none, so
-- `acquire_fixture_scope` refused it by name (FIXTURE_METADATA_HAS_NO_
-- CONDITION_KEY) and its settlement comparison could never see a competition
-- phase, a game format or an event state. Writing such a row under a
-- borrowed or invented condition id would hand one contract another's game.
--
-- This table keys the SAME kind of evidence by (venue, venue_fixture_key):
-- the venue's own event identity, namespaced -- e.g. ('PMUS',
-- 'event:atc-unl-gre-ger-2026-10-04'). No global id is stored or implied.
--
-- Every row carries its source, URL, retrieval time and the source's own
-- match id. A row is written ONLY from an authoritative schedule read that
-- matched the fixture on both team names and the official date; anything
-- else is a named refusal on the candidate and NO row.

CREATE TABLE IF NOT EXISTS venue_fixture_metadata (
    venue              text        NOT NULL,
    venue_fixture_key  text        NOT NULL,
    sport_family       text        NOT NULL,
    competition        text,
    phase              text,
    game_format        text,
    play_has_begun     boolean,
    event_state_raw    text,
    actual_start_at    timestamptz,
    scheduled_kickoff  timestamptz,
    start_evidence     text,
    official_date      date,
    home_team          text,
    away_team          text,
    orientation        text,
    source_match_id    text,
    source             text        NOT NULL,
    source_url         text        NOT NULL,
    retrieved_at       timestamptz NOT NULL,
    reader_version     text        NOT NULL,
    refusals           jsonb       NOT NULL DEFAULT '[]'::jsonb,
    raw                jsonb,
    written_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (venue, venue_fixture_key),
    CHECK (venue_fixture_key LIKE 'event:%')
);

CREATE INDEX IF NOT EXISTS venue_fixture_metadata_retrieved_at
    ON venue_fixture_metadata (retrieved_at DESC);

COMMENT ON TABLE venue_fixture_metadata IS
    'Authoritative fixture evidence (phase, format, event state) for venue-native contracts, keyed by the venue''s own namespaced event identity; never a global condition id.';
