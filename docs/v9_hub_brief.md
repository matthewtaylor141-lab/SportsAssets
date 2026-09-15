# BettorToken v9 — THE HUB (owner directive 2026-08-28)

The whole business operates from this platform. Four workstreams, in
order, each verified before the next; the S1 fleet continues in
parallel.

## A — Backend data layer
1. `/api/admin/copy-reports` — management reports: whale × sport ×
   category (classify_slug) × period (daily/weekly/monthly/all, ET),
   over live_orders settled+cashed_out copy sleeves, with per-bucket
   n/W-L/staked/pnl/roi and latency (avg + p50 of
   COALESCE(reaction_s, placed_at − trades.ts)); manual/underdog
   excluded; uncapped; CSV variant.
2. `/api/admin/copy-ledger` — order-level ledger export: every copy
   trade with whale, whale-fill ts, detected ts, placed ts, latency_s,
   venue, sport, category, stake, fill, pnl, status; JSON + CSV.
3. Copies P&L epoch: COPIES_EPOCH day '2026-08-28' (ET midnight
   tonight) as the default `since` for /api/copies-record — the
   public record restarts at zero tonight, `rebaselined: true`,
   ?since= override keeps all-time reachable (RECORD_EPOCH pattern;
   never touches history, never the audit paths).
4. copies-record trades rows gain latency_s + venue + sport;
   open block gains by-whale open stake.
5. Desk parity (money gates untouched, every order still through
   MANUAL_MAX_PER_ORDER_USD + daily budget + server re-quote):
   slug→token endpoint so PM positions chart; Kalshi position titles;
   10-level books; open/resting order list + cancel where the venue
   supports it; limit-price ticket (user-set limit, capped);
   PM universe boards from the pmus listing.
6. `/api/admin/hub-telemetry` — one aggregator for Meridian HUD:
   S1 beat, shadow gauge, copy-latency percentiles, today, balances.

## B — Brand v9 (evolve, don't discard)
Gold/cyan signature promoted from the wall palette into the app:
--bt-gold #e8c877 (money/brand), --bt-cyan #69e0ff (live/data);
wordmark TOKEN gradient → gold; sonar-arc monogram mark; v9 theme
layer (depth-field canvas, sonar-pulse live dots, unified motion
tokens, skeletons, count-ups); dataviz categorical palette unchanged
for chart series. Nav IA: Performance / Analytics / Reports / Desk /
Accounts / Meridian / Engine / System / Ops; /meridian route (alias
/jarvis); Meridian visible on desktop nav.

## C — Pages
- /reports (NEW): period selector, whale×sport×type pivot with
  drill-down, latency columns, CSV/PDF downloads, per-whale cards.
- TrackRecord v9: gold hero, epoch-aware (since-launch + all-time
  toggle), ledger rows gain latency + venue chips.
- Accounts v9: balances hero with committed-capital ring,
  per-whale open exposure.
- Desk v9: venue parity per A5 — limit ticket, open-orders tab,
  position charts, titles, deeper books, universe boards; PM mode
  reads like Polymarket, Kalshi mode like Kalshi.
- Meridian v9: keep the voice cockpit; add mission-control HUD
  panels (S1/shadow/latency/edge), whale-constellation canvas,
  today-arc around the orb; complete the jarvis→meridian rename
  with localStorage migration.
- Analytics v9: latency analytics section, epoch toggle.

## D — Verification
tsc+vite green; Playwright screenshots desktop+mobile with
console-error scan; design-critique fleet vs the wow bar; SQL
correctness fleet on the new endpoints (sleeve exclusion, ET
bucketing, epoch semantics); deploy via push, verify live.
