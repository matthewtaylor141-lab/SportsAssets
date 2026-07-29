# CLAUDE.md — Edge Engine build spec

## What this is
A sports prediction-market trading engine reverse-engineered from a full-history
analysis of Polymarket account 0x204f...5e14 ("swisstony"): 5,349,724 fills,
$935M staked, $21.45M realized PnL, Aug 2025 – Jul 2026. The strategy was
measured empirically (see `data/calib_*.csv`), not guessed. This spec encodes
everything the measurement supports. Anything not supported by the data is
marked ASSUMPTION and must be validated in shadow mode before capital.

## The measured strategy (ground truth — do not deviate without evidence)
1. **Buy-only, hold to resolution.** The reference account placed ZERO sell
   orders in 5.35M fills. Two-sided exposure is achieved by buying the
   complementary outcome token. Realization happens at settlement. Do not
   build exit logic in v1; build entry logic and settlement accounting.
2. **Fair value from sharp sportsbook odds.** Entries beat their price in 17
   of 20 bands — only a de-vigged sharp-book feed produces that signature at
   this breadth (every soccer league on earth, same hour). Feed is licensed
   (OpticOdds / TheOddsAPI / Sportradar tier), never scraped.
3. **Edge threshold, fee-aware.** Buy outcome O at price p when
   `fair(O) - p >= threshold(venue, band)`. Polymarket taker fee = 0;
   Kalshi taker fee ≈ 0.07*p*(1-p) per contract → on Kalshi either execute
   MAKER-side (fee-free) or add the fee into the threshold.
   p is whatever `adapter.plan_entry(book)` returns, NOT automatically the
   ask: Polymarket US rests one tick inside the spread (GTC + post-only), so
   the threshold is judged at the price we would actually pay. Resting orders
   are reaped on a TTL and give their one-per-event claim back unfilled; a
   market that fails to fill as maker crosses on the next look. PAPER always
   crosses — a paper fill at a resting price invents a queue position we
   never held, and that is how a shadow record starts lying about ROI.
4. **Band filter (measured).** Trade every band with proven edge; skip only
   the measured dead zones. `config/bands.yaml` is GENERATED, never hand-
   written — `scripts/gen_bands.py` reads `data/calib_price.csv` (moneyline)
   and `scripts/gen_category_bands.py` writes the spread/total windows. The
   cents figures below are the STRONGEST bands, not the tradeable ones;
   transcribing this list into the config once cost ~45% of the tradeable
   price space, so regenerate rather than retype.
   Strongest bands (stake-weighted, cents): 05-10c:+2.7, 15-20c:+2.3,
   30-35c:+3.6, 45-50c:+2.9, 75-80c:+2.6, 80-85c:+2.2, 85-90c:+1.4.
   DEAD ZONES (do not trade): 40-45c (-0.4), 65-70c (0.0), 90-95c (0.0).
   Derivatives (spread/total) do NOT inherit the moneyline dead zones and are
   priced symmetrically about the line — see the header of
   `scripts/gen_category_bands.py` for why.
5. **League filter (measured).** Positive-ROI leagues (allowlist) and
   negative/flat leagues (blocklist) in `config/leagues.yaml`, keyed by the
   Polymarket slug-prefix map recovered from the data. Highlights —
   allow: fifwc, epl, lal, fl1, uel, cbb, acn, lib, bra, spl, bl2, wta, nba.
   BLOCK: ucl, bun, elc, tur, por (all measured net-negative), atp, mlb (flat).
6. **Size discipline (measured).** ROI by fill size: +2.5–3.1% up to $10K,
   +1.1% at $10–50K, NEGATIVE above $50K. Hard caps: per-fill $5K default
   ($10K max), per-market exposure $25K, slice large intents into small fills.
7. **Ramp expectation (measured).** Reference account: months 1–2 negative,
   profitable at scale from month 4. Shadow mode (60–90 days, zero capital)
   replaces this burn-in.

## Capacity envelope (honest numbers — do not inflate)
Second participant splitting the sub-$10K opportunity: assume 20–40% capture
of ~$457M/yr steady-state staking at ~2–2.5% ROI → $1.8–4.5M/yr gross UPPER
BOUND before slippage, on $1–2M working capital. Shadow mode measures the
real number. On Kalshi the opportunity set is smaller (majors only) but
uncontested by the reference account; treat venue choice as an empirical
outcome of dual-venue shadow logging, not a prior belief.

## Architecture (4 services + shadow harness)
```
src/edge/
  fairvalue/    feed.py (odds feed client), devig.py (multiplicative + power),
                poisson.py (Dixon-Coles goals model -> exact score / totals / spread probs)
  venues/       base.py (VenueAdapter ABC), polymarket.py (CLOB WS + REST),
                kalshi.py (REST/WS, maker-first), mapper.py (feed event -> venue market id;
                seeded by slug-prefix league map)
  execution/    engine.py (threshold check, sizing, slicing, caps), risk.py
  ledger/       positions.py, settlement.py, pnl.py  (port of the audited
                average-cost pipeline — reuse, do not rewrite)
  shadow/       runner.py (logs would-be fills with book snapshot, NO orders),
                grader.py (scores shadow fills at resolution using the same
                methodology as the Phase 1 study — apples to apples)
config/         bands.yaml, leagues.yaml, venues.yaml, risk.yaml
```

## Build order (each step has tests before the next)
1. `ledger/` — port from the existing pipeline; it is already audited.
2. `fairvalue/devig.py` + `poisson.py` with unit tests against known odds.
3. `venues/mapper.py` — hardest unglamorous problem; fuzzy team-name matching
   feed<->venue; measure match rate, require >95% on allowlist leagues.
4. `shadow/runner.py` on Polymarket public WS (no auth needed to read books).
5. Kalshi adapter (auth, maker order semantics) in shadow.
6. 60–90 day dual-venue shadow. Grade weekly. Capital decision comes from
   `shadow/grader.py` output, nowhere else.

## Hard rules
- No live orders until shadow grader shows ≥1.5% ROI net of modeled fees and
  slippage over ≥60 days and ≥5,000 shadow fills per venue.
- Never exceed size caps to "make back" anything. The reference account's
  top-500 largest fills lost $1.07M — size discipline IS the strategy.
- Log every decision input (odds snapshot, book snapshot, fair value,
  threshold) so losing periods are diagnosable.
- Venue ToS review before live trading on either venue.

## Reference data (from the Phase 1 study — keep in repo)
- data/calib_price.csv      — edge by entry band
- data/calib_league.csv     — edge by league (slug prefix)
- data/capacity_sizebuckets.csv — ROI by fill size
- data/regime_monthly_exact.csv — monthly ramp

## Cross-account validation (top 6 sports leaderboard, full-history extraction)
Five additional top-PnL sports wallets were extracted and scored with the same
methodology (DEEDDIT, asparagus2012, Sparkling8899, Allezpapa, Jsram; see
data/top5_cross_account.csv). Findings, and what they mean for this build:

1. **Buy-only / hold-to-resolution is universal** across all six accounts.
   Structural venue fact — v1 needs no exit logic. (Confirmed n=6.)
2. **Only the reference account is systematizable.** The other winners are
   concentrated conviction bettors (16–174 markets lifetime vs 146,508).
   Their per-band stats are small-sample noise; do NOT import their numbers
   into config. Calibration authority remains the 5.34M-fill dataset.
3. **Leaderboard PnL is unreliable.** Sparkling8899 shows +$3.6M on the
   board but is -0.2% ROI lifetime on $20.3M staked (World Cup gains,
   MLB/NFL losses). All performance claims in this project use full-history
   realized accounting; never trust window/display figures.
4. **Dead-zone nuance.** The 40-45c dead zone is specific to the reference
   account's mechanism (conviction bettors were profitable there on tiny n).
   Keep it excluded for this engine (same mechanism), tag as
   SHADOW-TESTABLE rather than structural.
