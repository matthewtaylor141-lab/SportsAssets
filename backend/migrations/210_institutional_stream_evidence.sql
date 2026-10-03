-- ══════════════════════════════════════════════════════════════════════
-- 210 · INSTITUTIONAL gRPC STREAM RUNTIME EVIDENCE, AND THE SAME-BOOK PROBE
-- ══════════════════════════════════════════════════════════════════════
--
-- WHY. The institutional market-data stream (institutional_stream) runs in
-- the WORKERS process -- the only service holding PMX_* -- while the P5
-- live book rule is evaluated in the API process. The stream's heartbeat
-- digest is overwritten every beat. These two tables make its behaviour
-- durable and readable from the API (GET /api/command/p5/evidence) and from
-- the production readback SQL:
--
--   institutional_stream_evidence  one row per (process, symbol, minute)
--       written by institutional_stream_evidence.persist from the stream's
--       own events: connection identity + epoch, connect / reconnect times,
--       first complete book after (re)connect, gap events, venue
--       transact_time vs local receipt (age, skew), message counts,
--       instrument state, top-N depth, and the stream's current() answer
--       with its evidence. symbol = '*' is the process row.
--
--   institutional_same_book_probe  one row per probe sample: the resident
--       institutional stream book and the retail Polymarket US public book
--       (GET /v1/markets/{slug}/book) read within one bounded window for an
--       EXACTLY mapped symbol (institutional_contract_map), and whether they
--       agree after scale conversion. Evidence for or against the single-
--       book premise. orders_placed is CHECKed to 0: the probe is a read.
--
-- WRITTEN ONLY when INSTITUTIONAL_MD_STREAM=on in the workers process. No
-- credential, token or key value is ever stored. Append / upsert only.
--
-- IDEMPOTENT: IF NOT EXISTS. No BEGIN/COMMIT of its own: the runner applies
-- the file inside one transaction together with its schema_migrations row.

CREATE TABLE IF NOT EXISTS institutional_stream_evidence (
    id                                  bigserial PRIMARY KEY,
    minute                              timestamptz NOT NULL,
    symbol                              text        NOT NULL,
    process_id                          text        NOT NULL,
    service                             text        NOT NULL,
    recorded_at                         timestamptz NOT NULL DEFAULT now(),
    evaluated_at_epoch                  double precision,
    version                             text,
    stream_state                        text,
    stream_state_why                    text,
    connection_id                       text,
    connection_epoch                    integer,
    connected                           boolean,
    connected_at                        timestamptz,
    connects_total                      integer,
    reconnects_total                    integer,
    connects_in_minute                  integer,
    disconnects_in_minute               integer,
    first_connect_at                    timestamptz,
    last_connect_at                     timestamptz,
    last_disconnect_at                  timestamptz,
    last_disconnect_why                 text,
    first_complete_book_at              timestamptz,
    first_complete_book_after_connect_s double precision,
    gap_open                            text,
    gap_events                          jsonb NOT NULL DEFAULT '[]'::jsonb,
    regressions_total                   integer,
    regressions_in_minute               integer,
    messages_total                      bigint,
    messages_in_minute                  integer,
    updates_total                       integer,
    updates_in_minute                   integer,
    stray_in_minute                     integer,
    max_interarrival_s                  double precision,
    venue_ts                            timestamptz,
    received_at                         timestamptz,
    receipt_age_s                       double precision,
    venue_receipt_skew_s                double precision,
    skew_min_s                          double precision,
    skew_p50_s                          double precision,
    skew_max_s                          double precision,
    skew_samples                        integer,
    instrument_state                    text,
    state_source                        text,
    price_scale                         bigint,
    qty_scale                           bigint,
    depth_bids                          integer,
    depth_offers                        integer,
    top_n                               jsonb,
    current_ok                          boolean,
    current_refusal                     text,
    current_read                        jsonb,
    identity                            jsonb,
    extra                               jsonb,
    CONSTRAINT institutional_stream_evidence_key
        UNIQUE (process_id, symbol, minute),
    CONSTRAINT institutional_stream_evidence_minute_ck
        CHECK (date_trunc('minute', minute) = minute)
);
CREATE INDEX IF NOT EXISTS institutional_stream_evidence_minute_idx
    ON institutional_stream_evidence (minute DESC);
CREATE INDEX IF NOT EXISTS institutional_stream_evidence_symbol_idx
    ON institutional_stream_evidence (symbol, minute DESC);

CREATE TABLE IF NOT EXISTS institutional_same_book_probe (
    id                       bigserial PRIMARY KEY,
    probed_at                timestamptz NOT NULL DEFAULT now(),
    process_id               text        NOT NULL,
    service                  text        NOT NULL,
    version                  text,
    symbol                   text        NOT NULL,
    retail_slug              text        NOT NULL,
    identity_ok              boolean     NOT NULL,
    identity_refusal         text,
    identity                 jsonb,
    stream_ok                boolean,
    stream_refusal           text,
    connection_epoch         integer,
    connection_id            text,
    stream_received_at       timestamptz,
    stream_venue_ts          timestamptz,
    retail_ok                boolean,
    retail_error             text,
    retail_request_at        timestamptz,
    retail_response_at       timestamptz,
    window_s                 double precision,
    max_window_s             double precision,
    within_window            boolean,
    stream_changed_in_window boolean,
    matched_stream_read      text,
    verdict                  text        NOT NULL,
    verdict_reason           text,
    top_n                    integer,
    best_bid_equal           boolean,
    best_offer_equal         boolean,
    touch_qty_equal          boolean,
    levels_equal             boolean,
    stream_book              jsonb,
    retail_book              jsonb,
    diff                     jsonb,
    orders_placed            integer     NOT NULL DEFAULT 0,
    CONSTRAINT institutional_same_book_probe_verdict_ck CHECK (verdict IN (
        'AGREE_TOP_N', 'AGREE_TOUCH_ONLY', 'DISAGREE', 'NOT_COMPARABLE')),
    CONSTRAINT institutional_same_book_probe_no_orders_ck
        CHECK (orders_placed = 0)
);
CREATE INDEX IF NOT EXISTS institutional_same_book_probe_at_idx
    ON institutional_same_book_probe (probed_at DESC);
CREATE INDEX IF NOT EXISTS institutional_same_book_probe_symbol_idx
    ON institutional_same_book_probe (symbol, probed_at DESC);
