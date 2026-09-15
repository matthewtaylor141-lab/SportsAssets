-- 012: Owner directive 2026-08-06 — the copy book becomes a four-account,
-- sport-weighted portfolio (kch123: basketball/football/hockey;
-- HomeRunHazard: baseball/WNBA; RN1: tennis; swisstony: soccer + rest).
-- Seed the two NEW source wallets so ingestion (poller + chain listener)
-- tracks their fills. Addresses are from the fill-level forensic reports,
-- cross-verified against the venue leaderboard census (2026-08-06 probe).
-- Pinned: the weekly roster refresh must never rotate a live copy SOURCE.
INSERT INTO whales (address, username, pinned, active)
VALUES
  ('0x6a72f61820b26b1fe4d956e17b6dc2a1ea3033ee', 'kch123', TRUE, TRUE),
  ('0x5268527977f700f9bf9b6d5cd843859e4e70135d', 'HomeRunHazard', TRUE, TRUE)
ON CONFLICT (address) DO UPDATE
  SET pinned = TRUE, active = TRUE, removed_at = NULL,
      username = EXCLUDED.username;
