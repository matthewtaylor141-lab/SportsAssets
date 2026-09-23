-- POSITIONS ARE KEYED PER BOOK. The primary key was
-- (desk_id, condition_id, outcome_index), which excluded the account --
-- so when the new book took a position in a market the CLOSED book
-- also held, the upsert matched the preserved row and overwrote it,
-- moving it into the new account. Three preserved legs were absorbed
-- this way before it was caught.
--
-- A PRIMARY KEY CANNOT INCLUDE account_id DIRECTLY, because the
-- preserved rows carry NULL there and a key column must be NOT NULL --
-- and back-filling them is exactly the attribution that does not
-- exist. So the constraint becomes a UNIQUE INDEX over
-- coalesce(account_id, ''), which admits the NULL rows as their own
-- group and keeps every book separate.
ALTER TABLE bettor_desk_positions
    DROP CONSTRAINT IF EXISTS bettor_desk_positions_pkey;

CREATE UNIQUE INDEX IF NOT EXISTS bettor_desk_positions_book_idx
    ON bettor_desk_positions
       (desk_id, coalesce(account_id, ''), condition_id, outcome_index);
