-- One FILLED copy per proposition, ever (owner directive 2026-08-03:
-- "if he takes cardinals moneyline 10x, I just want it copied once").
-- The predicate covers in-flight and money-spent states; rejected
-- (unmappable) and unfilled (price out of tolerance) rows stay
-- retryable — an attempt that spent nothing must not retire the bet.
CREATE UNIQUE INDEX IF NOT EXISTS live_orders_one_fill_per_asset
    ON live_orders (asset)
    WHERE status IN ('submitting', 'filled', 'settled');
