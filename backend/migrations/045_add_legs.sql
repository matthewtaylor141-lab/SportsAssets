-- 045: ADD LEGS (owner order 2026-09-02, "make all 4 of these changes
-- now and get it firing immediately"). A whale's fresh BUY on an
-- outcome HIS filled copy row already holds is copied as an ADD LEG
-- and merged into that standing row on fill: shares and cost add, the
-- fill price becomes the share-weighted average, orig_shares grows so
-- a later partial exit's fraction is of everything ever bought. The
-- leg's own row is retired as 'merged' -- a NEW terminal status: it is
-- neither a refusal (money went out) nor a position (the standing row
-- carries it), and a status no consumer enumerates is a row every
-- status-filtered read ignores, which is the safe direction for a row
-- that stands for no position and no refusal.
--
-- The one-fill-per-asset claim (011, 016) put 'submitting' in its
-- predicate, so an add leg -- in flight beside the standing 'filled'
-- row on the same asset -- could not even be INSERTed. The claim is
-- split in two, each still a hard database guarantee:
--
--   one_fill_per_asset      at most ONE standing position per asset
--                           ('filled' / 'settled'), as before
--   one_inflight_per_asset  at most ONE order in flight per asset, so
--                           two detections racing on one asset still
--                           serialize at the INSERT and two add legs
--                           cannot both be in flight
--
-- What is deliberately NO LONGER refused at the database: a row in
-- flight while a 'filled' row stands on the same asset. That shape is
-- exactly an add leg; every non-add case of it is refused by the
-- executor's referees (already-taken, never-add, one-per-game) before
-- any order is sent, as they always were -- the index was the backstop
-- behind them, and the backstop now permits only what the referees
-- explicitly declare an add.
--
-- Existing rows satisfy both new predicates by construction: under the
-- old index no asset ever held two rows across ('submitting', 'filled',
-- 'settled') at once.

ALTER TABLE live_orders DROP CONSTRAINT IF EXISTS live_orders_status_check;
ALTER TABLE live_orders ADD CONSTRAINT live_orders_status_check
    CHECK (status IN ('submitting', 'filled', 'unfilled', 'rejected',
                      'error', 'settled', 'cashed_out', 'exiting',
                      'open', 'cancelled', 'merged'));

DROP INDEX IF EXISTS live_orders_one_fill_per_asset;
CREATE UNIQUE INDEX IF NOT EXISTS live_orders_one_fill_per_asset
    ON live_orders (asset)
    WHERE status IN ('filled', 'settled')
      AND COALESCE(whale_username, '') NOT IN ('manual', 'underdog');

CREATE UNIQUE INDEX IF NOT EXISTS live_orders_one_inflight_per_asset
    ON live_orders (asset)
    WHERE status = 'submitting'
      AND COALESCE(whale_username, '') NOT IN ('manual', 'underdog');
