# Kalshi canonical venue: production findings at 088af82 (2026-10-07)

Source: the `pm-acceptance` workflow run 37687742013 (read only, `post_receipt` blank). It read
`GET /api/command/venues` and `/api/command/completion-readiness` from the serving API
(088af82d, workers 088af82d, schema 314), plus Render deploys and events.

## What production showed

| Item | Value at 088af82 |
|---|---|
| KALSHI_HEALTH | DEGRADED: 2,890 requests, 2,888 ok, 2 × HTTP 429; catalogue stopped at page 117 on 429 (`complete: false`) |
| Kalshi book freshness | 120 / 140 tracked books within the 30 s SLA (85.7 %) |
| POLYMARKET_HEALTH | OK: 476 books / 15 min, 35 errors, newest 1.2 s (kept apart from Kalshi) |
| Kalshi fixtures | 619 seen, 71 established, 23 mapped to PMUS |
| Canonical claims | **0 fingerprinted.** Every Kalshi alias refused `UNKNOWN_STATES` (all result states). PMUS aliases refused only `UNKNOWN_STATES:DRAW…` |
| Adriana claim scan | 139 structures, all SAME_VENUE COMPLEMENT, i.e. same-market YES + NO evaluated as pairs (the pre-rep-contract engine). Refusals: STALE_BOOK 71, BOOK_TIME_IN_FUTURE 21, BOOK_MISSING 13, PAYOFF_FLOOR_BELOW_COST 34 |
| Completion readback | `UNAVAILABLE: QueryCanceledError` (one statement past the 45 s budget failed the whole read) |
| Runtime | API and workers live at 088af82, 0 OOM / 0 server_failed since live; `sportsassets-market-plane` ABSENT (owner action) |

## Root causes and what changed on `claude/red-team-closeout-v1`

1. **Same-market YES/NO evaluated as arbitrage pairs.** The Kalshi rep's contract (ba65b682) fixes this:
   one market's YES and NO are one pool that nets, so Adriana evaluates cross-market alias
   combinations only. This was not yet deployed at 088af82.
2. **`BOOK_TIME_IN_FUTURE` (21).** Adriana took the scan clock before reading the books, so books the
   workers persisted during the read looked future-dated. Scans now evaluate as of the read
   (7174479b). A venue clock genuinely ahead of ours is still refused (red team C14).
3. **Completion readback failed whole.** Every section now runs in its own savepoint with its own
   budget and timing. A failing section is named UNAVAILABLE evidence that fails the readiness
   closed (7174479b).
4. **No Kalshi claim could fingerprint.** The market text ("If Cleveland wins the … game", "resolve
   based on the official final result") never states overtime or extra innings, nor (MLB, NBA,
   NHL) what a tie pays. The settlement parser was right not to invent either. Kalshi states both
   in each series' rulebook (`contract_terms_url`). Those rulebooks are now recorded byte for byte
   (`CONTRACT_TERMS_2026-10-07/`). A rulebook is bound only while the worker's live hash equals the
   recorded bytes, and only to entire-game contracts. The market's own text is read first. Hockey
   gets no fixed tie payout because its rulebook says "may resolve to No" (32a785fe).

## What stays fail-closed, and why (owner / rep questions)

- **Cancellation and delay past the window.** Each Kalshi market settles at its own "fair price".
  Nothing states that two strikes' fair prices sum to $1, so Yankees YES and Rays NO (and one
  market's YES and NO across that state) are separate claims, not aliases.
  *Question for the rep:* "On a cancelled or not-completed game, do the team strikes of one event
  settle at fair prices that sum to $1.00?"
- **PMUS tie state.** PMUS rule text prices overtime but not a tie, so cross-venue NBA/MLB claims
  stay NOT_ESTABLISHED until PMUS states the tie or void payout.
- **NHL tie.** HOCKEYWINNINGINPERIOD gives no fixed tie payout, so the state stays unknown.
- **Kalshi WebSocket books.** Proof needs the read-only Kalshi API key on the dedicated
  `sportsassets-market-plane` service (owner action). Until then, REST stays the bootstrap and
  recovery source, and the WS proof items are NOT ESTABLISHED in production.

KALSHI LIVE MONEY = NOT ACTIVATED · ADRIANA = SHADOW ONLY · NO AUTHORITY EXPANDED
