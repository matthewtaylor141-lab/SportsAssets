-- 249: THE VENUE CATALOGUE'S COMPLETENESS RECEIPT AND EVERY LISTING'S STATE
-- (R30A P0 incident -- coverage -> trade starvation -- stream inc-catalogue,
-- 2026-10-04).
--
-- THE OWNER: "Audit and repair now for: pagination, result limits,
-- market-per-event limits, first-page-only behavior, first-market-only
-- behavior, sport filters, league filters, active/open-state filters, live vs
-- pregame filtering, duplicate keys accidentally collapsing distinct markets.
-- We need the full tradable catalogue, not a sample of it."
--
-- WHAT PRODUCTION SAID (read-only):
--   * research-sql run 37233672878, K3a: of 40,094 `us_premap` rows re-seen in
--     90 minutes, ZERO started more than 12 h before their sighting and ZERO
--     start more than 96 h after it -- the sweep's start-time window was the
--     only door into the catalogue.
--   * fetch-docs runs 37233823157 / 37233829391 (the venue's public gateway,
--     two GETs of limit 2): open, tradable sports events outside that window
--     exist -- "National League Champion" / "World Series Champion"
--     (startTime 2026-09-07, every market MARKET_STATUS_OPEN). All four events
--     read carried `marketCounts.numMarkets` and a `period` word; `live` was
--     on the two sports events only (absent on the two politics events) and
--     `ended` on none. None of it was stored.
--   * research-sql run 37238634518 R2: the in-window futures (four ALDS / NLDS
--     series winners, the ESL Pro League season winner) left the catalogue
--     11.6-12.0 h after their start while still listed.
--   * premap_last carried `events`, `rows`, `pages_walked` and `truncated` for
--     the LAST sweep only, overwritten every 30 minutes (and every 3 minutes
--     for the fast lane): no history of what any refresh kept or dropped, so
--     "is the catalogue complete" had no answer for yesterday.
--
-- WHAT THIS ADDS
--   1. us_premap.listing_state / listing_state_source / listing_pass: the
--      venue's own live / pregame / ended word for the row's event (its `live`
--      flag, its `ended` flag or its period word -- or the schedule, named as
--      an estimate when the venue said nothing, which is an expected case) and
--      the calendar slice that read it. The writer (workers/premap._ensure_table) adds the same
--      three columns itself, as it does every column it writes, because the
--      workers can boot before the API's migrate; these statements are
--      IF NOT EXISTS and the CHECKs are added only where absent.
--   2. venue_catalogue_receipts: one APPEND-ONLY row per refresh, any lane
--      (full, fast, calendar) -- requests (every request sent, failed probe
--      rungs and per-event detail reads included), pages, listings seen / kept
--      / dropped (with the precise
--      reasons by sport, league and family in `receipt`), side keys qualified
--      or refused, truncation, and the outcome. The arithmetic is a CHECK, not
--      a promise: kept + dropped = seen, a TRUNCATED outcome is exactly a
--      truncated refresh, and COMPLETE is claimable only by a receipt that
--      says every pass reached a natural end of the venue's board.
--
-- Nothing here touches an order, a price, a threshold or a decision.

-- `us_premap` is CODE-CREATED (workers/premap._ensure_table); 031 and 055 assume
-- it exists, and a scratch database built from migrations alone has no such
-- table. This block therefore runs only where the table exists -- production,
-- and every database a sweep has touched -- and the writer adds the same three
-- columns itself wherever it creates the table.
DO $$
BEGIN
    IF to_regclass('us_premap') IS NULL THEN
        RAISE NOTICE '249: us_premap absent (code-created by the premap '
                     'sweep); its listing columns are added by the writer';
        RETURN;
    END IF;
    ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS listing_state text;
    ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS listing_state_source text;
    ALTER TABLE us_premap ADD COLUMN IF NOT EXISTS listing_pass text;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'us_premap_listing_state_known') THEN
        ALTER TABLE us_premap ADD CONSTRAINT us_premap_listing_state_known
            CHECK (listing_state IS NULL OR listing_state IN
                   ('LIVE', 'PREGAME', 'NOT_LIVE', 'ENDED', 'STARTED',
                    'UNKNOWN'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'us_premap_listing_state_source_known') THEN
        ALTER TABLE us_premap ADD CONSTRAINT us_premap_listing_state_source_known
            CHECK (listing_state_source IS NULL OR listing_state_source IN
                   ('VENUE_LIVE_FLAG', 'VENUE_ENDED_FLAG', 'VENUE_PERIOD_WORD',
                    'SCHEDULE_ESTIMATE', 'NO_EVIDENCE'));
    END IF;
    -- a state never travels without the evidence it came from
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'us_premap_listing_state_sourced') THEN
        ALTER TABLE us_premap ADD CONSTRAINT us_premap_listing_state_sourced
            CHECK ((listing_state IS NULL) = (listing_state_source IS NULL));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'us_premap_listing_pass_known') THEN
        ALTER TABLE us_premap ADD CONSTRAINT us_premap_listing_pass_known
            CHECK (listing_pass IS NULL OR listing_pass IN
                   ('WINDOW', 'AHEAD', 'STARTED_EARLIER', 'FAST',
                    'MARKETS_FALLBACK'));
    END IF;
    COMMENT ON COLUMN us_premap.listing_state IS
        'The row''s event state when the sweep read it: LIVE / PREGAME / '
        'NOT_LIVE / ENDED from the venue''s own live and ended flags and its '
        'period word (a final word such as FT is ENDED), STARTED / PREGAME from '
        'the schedule only when the venue stated nothing (see '
        'listing_state_source), UNKNOWN with neither. Migration 249; '
        'venue_catalogue.listing_state.';
    COMMENT ON COLUMN us_premap.listing_state_source IS
        'Where listing_state came from: VENUE_LIVE_FLAG, VENUE_ENDED_FLAG, '
        'VENUE_PERIOD_WORD, SCHEDULE_ESTIMATE (the venue said nothing; the '
        'start time decided) or NO_EVIDENCE.';
    COMMENT ON COLUMN us_premap.listing_pass IS
        'The calendar slice of the refresh that read the row: WINDOW (now-12h .. '
        'now+96h), AHEAD (beyond +96h), STARTED_EARLIER (before -12h), FAST (the '
        'imminent window) or MARKETS_FALLBACK (the degraded markets.list path).';
END $$;

CREATE TABLE IF NOT EXISTS venue_catalogue_receipts (
    id                  bigserial PRIMARY KEY,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    lane                text NOT NULL CHECK (lane IN ('full', 'fast',
                                                      'calendar')),
    started_at          timestamptz NOT NULL,
    finished_at         timestamptz NOT NULL,
    outcome             text NOT NULL
                        CHECK (outcome IN ('COMPLETE', 'TRUNCATED', 'PARTIAL',
                                           'FAILED')),
    pages_read          integer NOT NULL CHECK (pages_read >= 0),
    requests            integer NOT NULL CHECK (requests >= 0),
    events_seen         integer NOT NULL CHECK (events_seen >= 0),
    events_kept         integer NOT NULL CHECK (events_kept >= 0),
    events_dropped      integer NOT NULL CHECK (events_dropped >= 0),
    markets_seen        integer NOT NULL CHECK (markets_seen >= 0),
    markets_kept        integer NOT NULL CHECK (markets_kept >= 0),
    markets_dropped     integer NOT NULL CHECK (markets_dropped >= 0),
    sides_written       integer NOT NULL CHECK (sides_written >= 0),
    side_keys_qualified integer NOT NULL DEFAULT 0
                        CHECK (side_keys_qualified >= 0),
    side_keys_refused   integer NOT NULL DEFAULT 0
                        CHECK (side_keys_refused >= 0),
    truncated           boolean NOT NULL,
    version             text NOT NULL CHECK (length(version) > 0),
    receipt             jsonb NOT NULL
                        CHECK (jsonb_typeof(receipt) = 'object'),
    CONSTRAINT venue_catalogue_receipts_clock
        CHECK (finished_at >= started_at),
    -- a page is a request that answered events: never more pages than requests
    CONSTRAINT venue_catalogue_receipts_pages_are_requests
        CHECK (pages_read <= requests),
    -- every listing seen is kept or dropped -- by a named reason in `receipt`
    CONSTRAINT venue_catalogue_receipts_events_reconcile
        CHECK (events_kept + events_dropped = events_seen),
    CONSTRAINT venue_catalogue_receipts_markets_reconcile
        CHECK (markets_kept + markets_dropped = markets_seen),
    -- TRUNCATED is exactly a refresh that ran out of request budget while the
    -- venue was still serving full pages: neither can be claimed without the
    -- other
    CONSTRAINT venue_catalogue_receipts_truncation_named
        CHECK (truncated = (outcome = 'TRUNCATED')),
    -- COMPLETE only when the receipt itself says every pass reached a natural
    -- end of the venue's board
    CONSTRAINT venue_catalogue_receipts_complete_is_evidenced
        CHECK (outcome <> 'COMPLETE'
               OR (receipt ->> 'complete') = 'true')
);

CREATE INDEX IF NOT EXISTS venue_catalogue_receipts_recent
    ON venue_catalogue_receipts (lane, recorded_at DESC);

COMMENT ON TABLE venue_catalogue_receipts IS
    'One append-only completeness receipt per us_premap refresh (workers/premap.'
    'refresh, any lane: full, fast, calendar): requests and pages per pass, how '
    'each pass ended, '
    'listings seen / kept / dropped with the precise reason by sport, league and '
    'family, the venue''s own market count per event against what arrived '
    'inline, live / pregame states, side keys qualified or refused. Migration 249 '
    '(R30A P0 incident, inc-catalogue).';

CREATE OR REPLACE FUNCTION venue_catalogue_receipts_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'VENUE_CATALOGUE_RECEIPTS_APPEND_ONLY: % on % is refused '
                    '(a refresh receipt is a record of what the catalogue held)',
                    TG_OP, TG_TABLE_NAME;
END $$;

DROP TRIGGER IF EXISTS venue_catalogue_receipts_append_only_trg
    ON venue_catalogue_receipts;
CREATE TRIGGER venue_catalogue_receipts_append_only_trg
    BEFORE UPDATE OR DELETE ON venue_catalogue_receipts
    FOR EACH ROW EXECUTE FUNCTION venue_catalogue_receipts_append_only();
DROP TRIGGER IF EXISTS venue_catalogue_receipts_no_truncate_trg
    ON venue_catalogue_receipts;
CREATE TRIGGER venue_catalogue_receipts_no_truncate_trg
    BEFORE TRUNCATE ON venue_catalogue_receipts
    FOR EACH STATEMENT EXECUTE FUNCTION venue_catalogue_receipts_append_only();
