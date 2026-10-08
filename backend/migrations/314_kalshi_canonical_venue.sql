-- 314 · KALSHI AS A CANONICAL VENUE: CURRENT BOOKS, STRUCTURED FIXTURES,
-- CLAIM ALIASES AND ROUTE RECEIPTS (Kalshi Canonical Venue V1).
-- MARKET DATA + MAPPING + ROUTING EVIDENCE ONLY. No order, cancel, funding,
-- sizing or capital authority; no P&L: Adriana's migration-265 tables stay
-- the arbitrage system of record and the PAPER ledger the only ledger.

-- the current Kalshi book of every tracked market (upserted, bounded by the
-- tracked set); the YES / NO ask ladders follow Kalshi's documented
-- reciprocal book (kalshi_market_data.ORDERBOOK_PROTOCOL)
CREATE TABLE IF NOT EXISTS kalshi_books_current (
    ticker text PRIMARY KEY,
    event_ticker text NOT NULL,
    series_ticker text NOT NULL,
    yes_bids jsonb NOT NULL DEFAULT '[]'::jsonb,
    no_bids jsonb NOT NULL DEFAULT '[]'::jsonb,
    yes_asks jsonb NOT NULL DEFAULT '[]'::jsonb,
    no_asks jsonb NOT NULL DEFAULT '[]'::jsonb,
    book_basis text,
    record_quotes jsonb,
    readable boolean NOT NULL,
    error text,
    observed_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kalshi_books_current_event
    ON kalshi_books_current (event_ticker);

-- the structured fixture of every Kalshi game event read (milestone ids)
CREATE TABLE IF NOT EXISTS kalshi_fixtures_current (
    event_ticker text PRIMARY KEY,
    series_ticker text NOT NULL,
    sport text,
    league text,
    start_at timestamptz,
    home_id text,
    away_id text,
    home_code text,
    away_code text,
    tie_ticker text,
    team_tickers text[] NOT NULL DEFAULT '{}',
    outcome_kind text,
    mapping_status text NOT NULL CHECK (mapping_status IN (
        'ESTABLISHED', 'NOT_ESTABLISHED')),
    mapping_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    pmus_slug text,
    pmus_mapping_status text,
    pmus_mapping_reasons jsonb,
    milestone_id text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- every executable alias of every canonical claim, with its payoff
-- fingerprint, quote, fee and all-in price (current; one row per path)
CREATE TABLE IF NOT EXISTS canonical_claim_aliases (
    alias_key text PRIMARY KEY,
    event_key text NOT NULL,
    claim_fingerprint text,
    venue text NOT NULL,
    market_id text NOT NULL,
    side text NOT NULL CHECK (side IN ('YES', 'NO')),
    subject text,
    mapping_status text NOT NULL,
    settlement_status text NOT NULL,
    refusals jsonb NOT NULL DEFAULT '[]'::jsonb,
    payoff jsonb NOT NULL DEFAULT '{}'::jsonb,
    best_ask numeric,
    depth integer,
    book_basis text,
    observed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    mode text NOT NULL DEFAULT 'SHADOW' CHECK (mode = 'SHADOW')
);
CREATE INDEX IF NOT EXISTS canonical_claim_aliases_event
    ON canonical_claim_aliases (event_key);

-- the chosen / runner-up / lost route of every claim with >= 1 alias,
-- append-only, one receipt per claim per minute bucket
CREATE TABLE IF NOT EXISTS canonical_route_receipts (
    receipt_id text PRIMARY KEY,
    computed_at timestamptz NOT NULL,
    event_key text NOT NULL,
    claim_fingerprint text NOT NULL,
    qty integer NOT NULL,
    aliases integer NOT NULL,
    chosen jsonb,
    best_single jsonb,
    runner_up jsonb,
    lost jsonb NOT NULL DEFAULT '[]'::jsonb,
    candidates jsonb NOT NULL DEFAULT '[]'::jsonb,
    refusal text,
    mode text NOT NULL DEFAULT 'SHADOW' CHECK (mode = 'SHADOW'),
    production_effect text NOT NULL DEFAULT 'NONE'
        CHECK (production_effect = 'NONE')
);
CREATE INDEX IF NOT EXISTS canonical_route_receipts_at
    ON canonical_route_receipts (computed_at DESC);
