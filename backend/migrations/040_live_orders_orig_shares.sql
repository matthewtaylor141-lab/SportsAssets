-- THE EXIT TARGET NEEDS A BASE THAT DOES NOT MOVE.
--
-- mirror_exit sizes each trim to a TARGET rather than a fraction,
-- precisely so that repeated trims cannot compound:
--
--     _orig        = row["qty"]                    -- live_orders.filled_shares
--     _target_hold = _orig * (1.0 - closed_frac)
--     qty          = ours - _target_hold
--
-- The comment above it (live_executor.py:1125-1135) states the whole
-- point: "he is closed_frac out of what he bought, therefore we should
-- still hold that same fraction of what WE bought".
--
-- But mirror_exit then REWRITES filled_shares to the post-sale
-- remainder, so on the second trim `_orig` is no longer what we bought
-- -- it is what we have left. The cumulative fraction lands on an
-- already-shrunken base and compounds, which is the exact defect the
-- target form was written to prevent.
--
-- Whale 20% then 40% out of a 1,000-share book, us holding 200:
--     trim 1: target 160, sell 40, filled_shares <- 160
--     trim 2: target 160*0.6 = 96, sell 64  -> we hold 96
--     correct:                   200*0.6    -> we hold 120
--
-- We leave 20% faster than the whale does. The direction costs edge and
-- spread rather than principal, but it is not the methodology the owner
-- asked us to mirror.
--
-- orig_shares is written ONCE, at the first exit, from the pre-sale
-- filled_shares -- the one moment the original is still on the row --
-- and never rewritten after. Nothing on the INSERT paths has to change,
-- so there is no way for a new insert site to forget it.
--
-- BACKFILL IS DELIBERATELY A NO-OP FOR HISTORY. Rows already partially
-- exited have lost their original count and it cannot be recovered:
-- copy_exit_applied records closed_frac, not shares. Seeding those with
-- today's filled_shares reproduces exactly today's behaviour rather
-- than inventing a number, so the migration cannot retroactively change
-- any past sizing decision or any settled P&L.
ALTER TABLE live_orders
    ADD COLUMN IF NOT EXISTS orig_shares double precision;

UPDATE live_orders
   SET orig_shares = filled_shares
 WHERE orig_shares IS NULL
   AND filled_shares IS NOT NULL;
