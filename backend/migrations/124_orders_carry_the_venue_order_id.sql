-- AN ORDER SENT TO A VENUE HAS TWO IDENTITIES, AND THE TABLE HELD ONE.
--
-- `rn1x_orders.order_id` is OURS -- the client order id we generate before
-- the submission leaves. The venue answers with ITS own id, and that is the
-- handle every later call needs: a poll, a cancel, and above all RECOVERY
-- after a restart, where our rows are a claim and the venue's open-order
-- list is the truth.
--
-- Before this column there was nowhere to put it. The first executor draft
-- parked it in `decision_id`, which is a FOREIGN KEY to `rn1x_decisions` --
-- so it both violated the key and destroyed the link between an order and
-- the decision that produced it. Two identities need two columns.
--
-- NULL is the right default and means exactly one thing: this order has no
-- venue-side identity. A modelled order never had one; an order that was
-- written but whose acknowledgement never came back has not got one YET,
-- which is precisely the state recovery must be able to see.

BEGIN;

ALTER TABLE rn1x_orders
    ADD COLUMN IF NOT EXISTS venue_order_id text;

COMMENT ON COLUMN rn1x_orders.venue_order_id IS
    'The VENUE''s own identifier for this order, as returned by its '
    'acknowledgement. NULL means there is none: either the order is '
    'modelled and never went anywhere, or it was recorded and the '
    'acknowledgement has not come back -- and those two are different '
    'states that recovery has to tell apart. `order_id` stays OUR client '
    'order id and `decision_id` stays the link to the decision.';

CREATE INDEX IF NOT EXISTS rn1x_orders_venue_order_id_idx
    ON rn1x_orders (venue_order_id)
    WHERE venue_order_id IS NOT NULL;

COMMIT;
