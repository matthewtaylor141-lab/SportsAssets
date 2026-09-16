# Edge Engine

Reverse-engineered sports prediction-market engine. Read CLAUDE.md first —
it is the build spec, seeded with measured calibration data in data/.
Shadow (PAPER) mode before capital, always. The mode line and the shadow
gate live in config/risk.yaml.

## What runs

One process: `python -m edge.shadow.runner` — the full decision loop every
`EDGE_CYCLE_SECONDS` (default 120):

    feed (TheOddsAPI, sharp books, de-vig) →
    map to venue markets (fuzzy, <0.95 confidence = untradeable) →
    order book (Kalshi + Polymarket US) →
    entry rule: fair − ask − fee ≥ band threshold (dead zones excluded) →
    risk: caps / one-per-event / circuit breaker / watchdog / kill switch →
    PAPER: ledger-logged fill  ·  LIVE_*: real order (maker-first on Kalshi,
    preview-verified FOK on Polymarket US) →
    settlement into the average-cost ledger; nightly report vs reference curves.

Everything durable lives in one SQLite file (`EDGE_LEDGER_DB`, default
`data/edge_ledger.sqlite3`): fills with full decision records, positions,
realizations, match-rate stats, engine state, mode audit log.

## Runbook

```bash
pip install .                         # or: pip install -r requirements.txt
export EDGE_ODDS_API_KEY=...          # licensed odds feed (required)
python -m edge.shadow.runner          # PAPER mode — logs, never orders

python -m edge.cli status             # mode, guards, PnL summary
python -m edge.cli match-rate         # mapper match rates (24h)
python -m edge.cli report             # generate the nightly report now
python -m edge.cli check-live         # go-live checklist (exit 0 = clean)
python -m edge.cli kill               # halt all trading immediately
python -m edge.cli resume             # release kill switch ONLY
```

Docker: `docker build . && docker run` uses the same entrypoint; on Render
this is the `edge-shadow` worker service.

## Going live (LIVE_BETA)

1. Run PAPER for long enough that `edge check-live` sees ≥95% mapper match
   rate on allowlisted leagues (needs ≥20 events in the last 24h).
2. Set venue credentials in the environment:
   - Kalshi: `EDGE_KALSHI_KEY_ID`, `EDGE_KALSHI_PRIVATE_KEY` (PEM) or
     `EDGE_KALSHI_PRIVATE_KEY_PATH`
   - Polymarket US: `EDGE_PMUS_KEY_ID`, `EDGE_PMUS_SECRET_KEY`
     (created at polymarket.us/developer)
3. `python -m edge.cli check-live` — every item must show ✓.
4. A human edits `config/risk.yaml`: `mode: LIVE_BETA`. Restart.
   The runner re-runs the checklist at startup and refuses (drops back to
   PAPER, logged) if anything is unchecked.

Beta caps (config/risk.yaml `profiles.live_beta`): $10 default / $25 max
per fill, $50 per market, $250/day split per venue, ONE position per event
ever, −$100 daily (realized+marked) circuit breaker → 72h halt with
auto-resume and **no manual override anywhere in this codebase**.

`mode: LIVE` (full measured caps) additionally requires the shadow gate:
≥60 days, ≥5,000 graded fills per venue, ≥1.5% net ROI — from
`edge.cli report` / grader output, nowhere else.

## Hard rules (encoded, not documented)

- No order without a <30s-fresh feed quote (`FeedEvent.is_fresh`).
- <0.95 mapping confidence = UNMAPPED, untradeable (`mapper.TRADEABLE_SCORE`).
- Dead zones 40–45c / 65–70c / 90c+ and blocklisted leagues (ucl, bun, elc,
  tur, por, ere, atp, mlb) are unconditionally untradeable (config).
- Never add to an event (`events_traded` atomic claim); never resize on
  losses (sizing reads static caps, PnL is not an input to size).
- Every fill carries its full decision record (feed snapshot, fair value,
  book, threshold, caps state) into the ledger.
- Fees are logged per venue per fill; the report recomputes net edge.

## Venue note (honest calibration caveat)

The calibration data (bands, leagues, sizes) was measured on the GLOBAL
Polymarket book. This engine trades **Kalshi + Polymarket US** — different
books, different liquidity, different fees. The measured configs are the
prior; the PAPER grader measured on THESE venues' books is the evidence
that gates capital. Optional: set `EDGE_GLOBAL_PM=1` to also paper-log the
global book for side-by-side comparison (never traded).
