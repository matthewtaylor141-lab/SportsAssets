-- THE REST LANE NEEDS TO BE GRADED AS ITS OWN COHORT.
--
-- Owner order 2026-09-01 ("full throttle, all approved"): after an IOC
-- copy fails to fill, rest a bid at the whale's EXACT price for a few
-- seconds before giving up. Those fills are a different population from
-- both existing tolerance cohorts -- they are copies we would otherwise
-- have lost entirely, caught because the ask came back to his price
-- inside the window -- and the only honest way to judge them is on
-- their own realised P&L with their own interval.
--
-- `lane` is stamped AFTER the fill record is written, by a separate
-- best-effort UPDATE, so a database that has not yet applied this
-- migration can never turn a real fill into an error row. NULL means
-- "the IOC lane", which is every row that exists today.
ALTER TABLE live_orders
    ADD COLUMN IF NOT EXISTS lane text;
