-- THE FUNDED PILOT NEEDS ITS OWN LEDGER, AND HERE IS WHY IT IS NOT ONE OF
-- THE THREE THAT ALREADY EXIST.
--
--   rn1x_orders    carries `CHECK (is_modelled)`. It is a MODELLED-ONLY
--                  table: a funded order structurally cannot be written to
--                  it, which is the schema already enforcing "a shadow fill
--                  cannot stand in for a funded fill". Good. Leave it.
--
--   live_orders    IS the real-money ledger and it would fit -- `trade_id`,
--                  `whale_username` and `lane` are all nullable. But the
--                  copy lane's never-add claim, its asset sweeps, its
--                  volume governor and its two reapers all read that table,
--                  and that lane is protected. Putting EV rows where those
--                  queries look, to save a CREATE TABLE, trades a real risk
--                  to a running money path for no gain.
--
--   bettor_*       the desk's shadow tables. Shadow by construction.
--
-- So: two tables for the funded pilot lane, and the ACCOUNTING FUNCTIONS are
-- reused rather than rewritten -- `live_executor.fill_cash` for the
-- side-aware cash a fill consumed, and `calibration_fees.expected_fee` for
-- the deployed fee schedule.
--
-- THE TWO PROPERTIES THESE TABLES EXIST TO GUARANTEE:
--
--  1. INTENT IS DURABLE BEFORE THE VENUE IS CALLED. A row reaches
--     `INTENT_RECORDED` and is committed before any request goes out, so a
--     lost acknowledgement leaves evidence that we may already have an order
--     at the venue. Recovery reconciles; it never resubmits blind.
--
--  2. A FILL IS IDENTIFIED BY THE VENUE'S OWN IDENTITY. `fill_id` is
--     `fvf:<venue_order_id>:<venue_fill_id>` and it is the PRIMARY KEY, so
--     the tenth delivery of one execution writes what the first did. An
--     execution the venue cannot name is NOT ingested -- it is recorded
--     unresolved, exactly as the test-venue executor does.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_funded_intents (
    -- OUR client order id, minted before the send. Never the venue's.
    intent_id           text PRIMARY KEY,
    account_id          text NOT NULL,
    venue               text NOT NULL,
    venue_class         text NOT NULL,
    us_market_slug      text NOT NULL,
    -- THE CANONICAL EVENT KEY, carried so exposure can be enforced per
    -- EVENT and not only per market. NOT NULL: an intent that cannot name
    -- its event cannot be checked against the event rail, and guessing one
    -- is how a rail gets silently skipped.
    event_key           text NOT NULL,
    order_intent        text NOT NULL
        CHECK (order_intent IN ('ORDER_INTENT_BUY_LONG',
                                'ORDER_INTENT_BUY_SHORT')),
    limit_price         numeric NOT NULL CHECK (limit_price > 0
                                                AND limit_price < 1),
    quantity            integer NOT NULL CHECK (quantity > 0),
    -- WHAT THIS ORDER COMMITS, in the same space the venue takes it: for a
    -- long that is price x qty, for a short (1 - price) x qty. One number,
    -- computed once, and the rails are checked against THIS.
    collateral_usd      numeric NOT NULL CHECK (collateral_usd > 0),
    -- The effective-limit digest the authorization was consumed against, so
    -- a later reader can tell which rails this order was admitted under.
    effective_digest    text NOT NULL,
    decision_ref        jsonb NOT NULL DEFAULT '{}'::jsonb,
    state               text NOT NULL
        CHECK (state IN ('INTENT_RECORDED',   -- committed, nothing sent yet
                         'SEND_ATTEMPTED',    -- the request left this process
                         'ACKNOWLEDGED',      -- the venue named an order id
                         'PARTIALLY_FILLED',
                         'FILLED',
                         'CANCELLED',
                         'REJECTED',          -- the venue refused it
                         'ABANDONED',          -- refused before anything left
                         'UNRESOLVED')),      -- we do not know; exposure stands
    venue_order_id      text,
    unresolved_reason   text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    sent_at             timestamptz,
    resolved_at         timestamptz,
    provenance          text NOT NULL DEFAULT 'FUNDED_PILOT_EXECUTION',
    raw                 jsonb NOT NULL DEFAULT '{}'::jsonb
);

COMMENT ON TABLE bettor_funded_intents IS
    'The funded pilot lane''s order ledger. A row is committed BEFORE the '
    'venue is called, so a lost acknowledgement leaves evidence rather than '
    'silence. Separate from live_orders because the copy lane''s claims, '
    'sweeps and reapers read that table and that lane is protected; separate '
    'from rn1x_orders because that table is modelled-only by CHECK.';

-- THE STATES IN WHICH THIS LANE MAY ALREADY HAVE EXPOSURE AT THE VENUE.
-- Used by the headroom check and by the concurrency guard below, so both
-- read one definition.
CREATE OR REPLACE FUNCTION bettor_funded_intent_is_live(s text)
RETURNS boolean AS $$
    SELECT s IN ('INTENT_RECORDED', 'SEND_ATTEMPTED', 'ACKNOWLEDGED',
                 'PARTIALLY_FILLED', 'UNRESOLVED')
$$ LANGUAGE sql IMMUTABLE;

-- ONE OPEN FUNDED INTENT AT A TIME, ENFORCED BY THE DATABASE.
--
-- The pilot proposal said "at most one open position at a time" as a
-- mitigation for the event rail that cannot see siblings. A sentence in a
-- proposal is not a mitigation: two concurrent submissions would both pass a
-- SELECT-then-INSERT check and both reach the venue. A UNIQUE index on a
-- constant expression over the live subset makes the SECOND insert fail at
-- the database, whatever the interleaving.
CREATE UNIQUE INDEX IF NOT EXISTS bettor_funded_one_live_intent
    ON bettor_funded_intents ((true))
    WHERE bettor_funded_intent_is_live(state);

CREATE INDEX IF NOT EXISTS bettor_funded_intents_account_idx
    ON bettor_funded_intents (account_id, venue, state);
CREATE INDEX IF NOT EXISTS bettor_funded_intents_event_idx
    ON bettor_funded_intents (event_key)
    WHERE bettor_funded_intent_is_live(state);
CREATE UNIQUE INDEX IF NOT EXISTS bettor_funded_intents_venue_order_idx
    ON bettor_funded_intents (venue_order_id)
    WHERE venue_order_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS bettor_funded_fills (
    -- fvf:<venue_order_id>:<venue_fill_id>. The VENUE'S identity, so a
    -- redelivery is the same row and ON CONFLICT DO NOTHING is honest.
    fill_id             text PRIMARY KEY,
    intent_id           text NOT NULL REFERENCES bettor_funded_intents
                            (intent_id),
    venue_order_id      text NOT NULL,
    venue_fill_id       text NOT NULL,
    at                  timestamptz NOT NULL,
    qty                 numeric NOT NULL CHECK (qty > 0),
    price               numeric NOT NULL CHECK (price > 0 AND price < 1),
    -- The cash this fill actually consumed, from live_executor.fill_cash:
    -- the LONG formula on a long, (1 - price) x qty on a short.
    cash_usd            numeric NOT NULL,
    fee_usd             numeric NOT NULL,
    fee_basis           text NOT NULL,
    raw                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    recorded_at         timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE bettor_funded_fills IS
    'Funded executions, keyed by the VENUE''s own fill identity so repeated '
    'delivery cannot double-count. An execution the venue does not name is '
    'NOT written here: it is recorded on the intent as UNRESOLVED.';

CREATE INDEX IF NOT EXISTS bettor_funded_fills_intent_idx
    ON bettor_funded_fills (intent_id);

COMMIT;
