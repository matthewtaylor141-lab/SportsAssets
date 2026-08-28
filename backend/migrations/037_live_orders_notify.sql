-- Live order-confirmation stream (owner order 2026-08-28): every
-- write to live_orders — autonomous copy fills, mirror exits, manual
-- desk orders, cash-outs, settlements — announces itself ON COMMIT
-- via pg_notify, so the desk renders a confirmation the instant the
-- venue's answer is recorded: no polling, and ZERO streaming code at
-- the many write sites (the trigger covers executor, workers, and
-- desk paths alike). Fires on INSERT and on STATUS changes only —
-- enrichment updates (slug backfill, raw payloads) stay silent.
CREATE OR REPLACE FUNCTION notify_live_order() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('live_orders_events', json_build_object(
        'id',            NEW.id,
        'op',            TG_OP,
        'status',        NEW.status,
        'side',          NEW.side,
        'venue',         NEW.venue,
        'whale',         NEW.whale_username,
        'slug',          NEW.us_market_slug,
        'shares',        NEW.filled_shares,
        'fill_price',    NEW.fill_price,
        'filled_usd',    NEW.filled_usd,
        'requested_usd', NEW.requested_usd,
        'pnl',           NEW.pnl,
        'error',         left(NEW.error, 120),
        'at',            extract(epoch from now())
    )::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS live_orders_notify ON live_orders;
CREATE TRIGGER live_orders_notify
    AFTER INSERT OR UPDATE OF status ON live_orders
    FOR EACH ROW EXECUTE FUNCTION notify_live_order();
