-- 008: live_orders venue routing. The LIVE beta can now execute on the
-- regulated Polymarket US exchange (per-outcome markets, own slugs) in
-- addition to the global CLOB. us_market_slug records the mapped US market
-- a copy was placed on; mapping failures are recorded as status='rejected'
-- with the reason in error.

ALTER TABLE live_orders
    ADD COLUMN IF NOT EXISTS venue TEXT NOT NULL DEFAULT 'polymarket-clob',
    ADD COLUMN IF NOT EXISTS us_market_slug TEXT;
