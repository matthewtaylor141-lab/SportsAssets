-- 022: Owner order 2026-08-21 afternoon ("lets get these two live and
-- running immediately on Kalshi, take a zero off their trades"): the
-- two BUILD-THE-RAILS verdicts from the non-sports dossier join the
-- tracked roster so ingestion (poller fast lane) detects their fills.
-- Both were graded on the venue's own lifetime P&L curves
-- (0xf705fa04 +$2.28M crypto price-band systematic; JnStrtPrdctnMrkts
-- +$799k/5mo pure crypto). They are CRYPTO copy sources for the
-- engine's Kalshi crypto leg ONLY — deliberately absent from
-- COPY_WHALES and AI_TRADER_SOURCE, so the Polymarket sports executor
-- never fires on them. Pinned: the weekly roster refresh must never
-- rotate a live copy source.
INSERT INTO whales (address, username, pinned, active)
VALUES
  ('0xf705fa045201391d9632b7f3cde06a5e24453ca7', '0xf705fa04', TRUE, TRUE),
  ('0x1465b79bff7992bc703e1aafb3683b1089647072', 'JnStrtPrdctnMrkts', TRUE, TRUE)
ON CONFLICT (address) DO UPDATE
  SET pinned = TRUE, active = TRUE, removed_at = NULL,
      username = EXCLUDED.username;
