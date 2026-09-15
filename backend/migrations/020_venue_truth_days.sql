-- Venue-truth day ledger (task #74). The rebuild's sources are rolling
-- windows (Kalshi's raw export carries ~15 days), so without a durable
-- day table the site's venue-truth record would forget its own history
-- as the window rolls. Each (day, venue) row is upserted while the day
-- is inside the live window and simply stops being touched once the
-- window moves past it — frozen by omission, no freeze logic to get
-- wrong. Aggregates only (settled cash per ET day), never positions.
CREATE TABLE IF NOT EXISTS venue_truth_days (
    day         text NOT NULL,          -- ET calendar day, YYYY-MM-DD
    venue       text NOT NULL,          -- 'polymarket-us' | 'kalshi'
    settled     integer NOT NULL DEFAULT 0,
    wins        integer NOT NULL DEFAULT 0,
    losses      integer NOT NULL DEFAULT 0,
    cost        double precision NOT NULL DEFAULT 0,
    realized    double precision NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (day, venue)
);
