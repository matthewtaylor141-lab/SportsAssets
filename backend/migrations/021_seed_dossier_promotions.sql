-- 021: Owner order 2026-08-21 morning ("map in and get the 2 recommended
-- traders added immediately"): the two PROMOTE verdicts from the whale
-- dossier join the copy roster at $100 probation clips. Both were
-- graded on the venue's own lifetime P&L curves (ferrariChampions2026
-- +$2.29M/142d, 86% market win rate; 0x076daa87 +$3.69M/78d, 83%),
-- systematic cadence ~665 entries/day at ~$20 medians. Addresses from
-- the leaderboard census, verified by the lifetime-curve fetch.
-- Pinned: the weekly roster refresh must never rotate a live copy SOURCE.
INSERT INTO whales (address, username, pinned, active)
VALUES
  ('0xfe787d2da716d60e8acff57fb87eb13cd4d10319', 'ferrariChampions2026', TRUE, TRUE),
  ('0x076daa87c4fe1a85402a9b6b8e0a866224388d4c', '0x076daa87', TRUE, TRUE)
ON CONFLICT (address) DO UPDATE
  SET pinned = TRUE, active = TRUE, removed_at = NULL,
      username = EXCLUDED.username;
