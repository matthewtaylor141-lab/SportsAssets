# Trading philosophy — how a trade clears, and why most don't

Current as of 2026-08-02. Every number here is read from `config/` and the
code, not from memory. When config changes, this document is wrong until
it is updated.

---

## 1. The one-sentence thesis

Sharp sportsbooks price sports better than a prediction market's order book
does. Strip the bookmaker's margin out of their odds and you have an
estimate of the true probability. When the venue sells an outcome for
materially less than that estimate, buy it and hold to settlement.

That is the entire idea. Everything else in this document is a defence
against the many ways that idea produces a confident number that is wrong.

**We do not predict games.** We have no model of football. We arbitrage a
disagreement between two pricing systems, one of which is demonstrably
better informed.

---

## 2. Why the guardrails outnumber the strategy

The strategy is one line: `buy when fair − price ≥ threshold`.

Roughly forty gates surround it, because a mispriced market and a
mis-*mapped* market look identical from the inside. Both produce "the venue
is selling at 0.40 and we think it's worth 0.55." One is an opportunity.
The other means our 0.55 describes a different bet entirely — the wrong
team, the wrong line, the wrong half of the game, the wrong player.

The second is far more common than the first. So the system is built to
**refuse by default** and to name the reason for every refusal.

---

## 3. What the fair value is, exactly

### 3.1 Books that count

Only these eight quote into our estimate. Soft/recreational books are
excluded on purpose — including them biases the median toward the soft side
and manufactures edges that are really our own error.

```
pinnacle, betfair_ex_eu, betfair_ex_uk, betfair_ex_au,
smarkets, matchbook, betanysports, lowvig
```

### 3.2 They do not get equal votes

```
pinnacle          3.0     ANCHOR
betfair_ex_eu     3.0     ANCHOR
betfair_ex_uk     3.0     ANCHOR
betfair_ex_au     2.0     ANCHOR
smarkets          2.0
matchbook         2.0
betanysports      1.0
lowvig            1.0
```

Pinnacle is the line the rest of the market prices against; an exchange is
a real market clearing real money with no vig to remove. A small low-margin
book is mostly copying Pinnacle with a lag, and giving it an equal vote
means our "sharp consensus" can literally *be* the follower's opinion when
only two or three books quote — which is exactly where the largest apparent
edges appear.

The combination is a **weighted median**, not a mean. Weights move the
halfway point; they do not let one wild quote drag the estimate.

### 3.3 De-vig

The weighted-median decimal odds for a complete outcome set are converted
to probabilities and normalised to sum to exactly 1.0 (power method,
multiplicative as a cross-check). That normalisation is what removes the
bookmaker's margin.

### 3.4 The anchor rule

**`min_anchor_books: 1`.** At least one of Pinnacle or a listed exchange
must be behind the number, or we refuse to price the market at all.

Measured 2026-07-31 on the live feed:

| market | outcomes/game | anchor present |
|---|---|---|
| MLB player props | 440 | pinnacle ✓ |
| MLB first-5-innings | 3 markets | pinnacle ✓ |
| EPL first half | 7 books on h2h_h1 | pinnacle ✓ |
| **Liga MX props** | 191 | **none** |
| **NHL (July)** | 31 events | **lowvig only, 5 events** |

Same provider, same plan, same request — opposite answers. Whether an
anchor exists is a per-league fact and cannot be assumed.

Refusal reason: `no_sharp_anchor`.

---

## 4. The decision pipeline

A candidate must pass **every** gate below, in this order. Failing any one
gets the outcome passed on, with the named reason counted in telemetry.

### Gate 1 — Is the quote fresh?

Feed quotes older than **30 seconds** cannot back an order. Stale events are
refreshed mid-cycle where possible.

Also enforced: feed age ≤ **60s**, clock skew ≤ **5s**, venue errors ≤ **25
per cycle**, tradeable rate ≥ **0.5**. Any breach halts live trading until
inputs are healthy.

### Gate 2 — Do we know what bet this is?

The venue's outcome text must resolve to exactly one bet, cross-checked
against every available signal (slug structure, title, point value, segment
token). **Any disagreement between signals makes the market untradeable
rather than guessed.**

Refusal reasons seen live: `no_side_match_moneyline`, `unresolved_slug_code`,
`no_draw_quote`, `no_sharp_quote_spread`, `no_sharp_quote_segment_{f5,h1,i1..i9}`,
`prop_no_threshold`, `prop_ambiguous_stat`.

**Two specific traps, both pinned by tests:**

- **`N+` is a half-point bet.** "5+ strikeouts" is P(X ≥ 5) = **Over 4.5**,
  not Over 5.0 — on a whole line, exactly 5 pushes. We pair `N+` only
  against `N − 0.5` and refuse a whole-number line outright.
- **Stat names overlap across roles.** `hits` is a batter market;
  `hits allowed` is a pitcher market. Longest phrase wins; ties refuse.

A segment market is **never** priced off the full-game line. Those are
different bets.

### Gate 3 — Is this league or category allowed?

```
blocked_categories: [moneyline]        ← GLOBAL quarantine, all leagues
blocklist:          [ucl, bun, elc, tur, por]
category_blocks:    nfl/cfb/mlb/atp/ere → moneyline
unknown_league_policy: allow
```

**Moneyline is globally quarantined as of 2026-08-02.** Measured on our own
fills:

```
moneyline  n=95  drift −2.34c  retention 0.239
draw       n=31  drift −2.54c  retention 0.146
free pool  n=12,142  +0.01c    ← same categories, when we DON'T buy
```

Three-quarters of the claimed edge on a game line evaporates within a minute
of entry. The free-sample pool shows no such move on markets we didn't
touch, so it is adverse selection, not noise. A surcharge only made us take a
losing bet less often; the quarantine removes the cohort. **Reversible by
config — it is a quarantine pending evidence, not a verdict.**

An unlisted league is *unmeasured*, not disproven, so it is allowed. The
nightly report grades per league and anything that doesn't pay gets added to
the blocklist.

### Gate 4 — What price would we actually pay?

Not the ask. `adapter.plan_entry(book)` returns the price we would really
transact at. With **maker-first enabled (default on)** the engine rests one
tick inside the spread (GTC + post-only); the threshold is judged at *that*
price. Resting orders are reaped on a TTL, hand back their claim unfilled,
and cross on the next look.

PAPER mode always crosses — a paper fill at a resting price invents a queue
position we never held, and that is how a shadow record starts lying about
ROI.

### Gate 5 — Is the price in a tradeable band?

Thresholds are **per 5-cent band and per category**. Full moneyline table
(retained for spreads/totals/props/segments; moneyline itself is quarantined
at Gate 3):

| band | min edge | band | min edge |
|---|---|---|---|
| 0.00–0.05 | 3.0¢ | 0.45–0.50 | 2.0¢ |
| 0.05–0.10 | 3.0¢ | 0.50–0.55 | 2.0¢ |
| 0.10–0.15 | 3.0¢ | 0.55–0.60 | 2.0¢ |
| 0.15–0.20 | 2.5¢ | 0.60–0.65 | 2.0¢ |
| 0.20–0.25 | 2.5¢ | 0.70–0.75 | 1.5¢ |
| 0.25–0.30 | 2.5¢ | 0.75–0.80 | 1.5¢ |
| 0.30–0.35 | 2.5¢ | 0.80–0.85 | 1.5¢ |
| 0.35–0.40 | 2.5¢ | 0.85–0.90 | 1.2¢ |

**Dead zones — never traded at any edge:** `0.40–0.45`, `0.65–0.70`,
`0.90–0.95`, `0.95–1.00`. These were measured negative or flat across
5.35M reference fills.

Derivatives do **not** inherit the moneyline dead zones — they are two-sided
line bets priced symmetrically:

| category | 0.10–0.40 | 0.40–0.61 | 0.61–0.91 |
|---|---|---|---|
| spread | 3.0¢ | 2.5¢ | 3.0¢ |
| total | 3.0¢ | 2.5¢ | 3.0¢ |

| category | 0.05–0.40 | 0.40–0.61 | 0.61–0.96 |
|---|---|---|---|
| **prop** | **4.0¢** | **3.5¢** | **4.0¢** |

Props carry a 1¢ surcharge over the equivalent total and a wider price span.
They are **unmeasured** for this engine — the calibration dataset has no prop
history — and the mapping is new, so the bar absorbs some of that until the
nightly report has graded a few hundred settlements.

An unlisted band is not tradeable. Not proven ≠ safe.

`bands.yaml` is **generated** by `scripts/gen_bands.py` and
`scripts/gen_category_bands.py`. Editing it by hand does not survive — the
test suite regenerates it.

### Gate 6 — Does the edge clear the bar *after* the drift surcharge?

```
required = band_threshold + drift_surcharge(category)
```

The surcharge is the measured adverse move in fair value in the minute after
we buy, per category, recomputed continuously from our own fills. Live
values as of 2026-08-02:

```
draw       +2.71¢
moneyline  +2.34¢
overall    +0.98¢
```

A threshold is a bar our *estimate* must clear. If fair value reliably moves
against us right after we buy, the estimate is optimistic by exactly that
much, and a bar ignoring it is set below the real cost of trading.

Only adverse drift is charged. Favourable drift is clamped to zero — being
early is not a licence to be looser. The surcharge is capped at **3.0¢** so
one noisy week cannot halt everything, and requires **≥12 observations**
before it charges anything.

**Shrinkage** (an alternative, proportional correction) applies instead of
the surcharge when reversion is measurable: it multiplies the edge by the
measured surviving fraction. Currently `keep = 0.997` across 20,108 free
samples, i.e. essentially no shrinkage — apparent edge does **not** revert in
proportion to its size, so winner's-curse-from-our-own-noise is *not* the
mechanism. The two corrections never both apply; that would charge twice for
the same error.

### Gate 7 — Is the edge believable?

**`max_believable_edge: 0.08`.** Any edge above 8¢ is refused and logged,
never traded, in any mode.

Real measured edges run 1–4¢. A 20¢ edge is a mapping error, a resolution
mismatch, or a stale quote wearing a costume. This guard has already caught
a live per-inning market priced off a full-game line (ask 0.98 vs 0.0214
fair) and one of my own test cases.

### Gate 8 — Do the risk caps allow it?

```
mode:                          LIVE_BETA
per_fill_usd_default:          $1.00
per_fill_usd_max:              $1.50
per_market_exposure_usd:       $1.50
per_event_exposure_usd:        $5.00
per_day_deployment:            100% of live buying power
one_position_per_market:       true      ← never add to a position
one_position_per_event:        false     ← each line is its own bet
daily_loss_halt:               $15, or 15% of the day, or 4σ
halt_hours:                    72        ← no manual override
```

**One position per market, never per event.** A single game lists a
moneyline plus a ladder of spreads and totals — 20–40 separately priced
bets. Claiming the whole event took one and abandoned the rest.

**Never add to a position.** Not on a favourable move (adding at a worse
price mathematically dilutes the edge — buy at 0.40 with fair 0.45 for a 5¢
edge, add at 0.44 and the blended edge is 3¢). Not on an adverse move
(the price falling is evidence our estimate was wrong — retention 0.239 says
fair follows the price about three-quarters of the way).

**Per-event cap exists because sides of one game are not independent.**
Exactly one of them pays. A per-market cap alone permitted a ticket on every
outcome the venue lists; we found the engine holding home, away *and* draw
on the same match.

The day budget is a **share of live buying power**, not a fixed number. A
fixed daily cap is a trade-count cap in disguise: at $1 a fill, "$25/day"
means "25 trades a day" no matter how many edges exist.

The circuit breaker scales with trade count (`daily_loss_halt_sigmas: 4`).
A fixed halt that is 4σ at 25 trades is coin-flip noise at 1,000 and would
lock the engine out for 72h on an ordinary day.

---

## 5. Position sizing — the ladder

```yaml
size_ladder:
  - {at: 1.0, usd: 1.00}    # clears the bar        → standard ticket
  - {at: 2.0, usd: 1.50}    # twice the bar         → larger ticket
```

`at` is a **multiple of the threshold**, not a cent figure. The bar already
varies by band, category and measured drift — a fixed cent trigger would
mean something different in every market, generous where the bar is low and
unreachable where it is high.

Worked: a 4¢ edge takes **$1.50** against a 2¢ bar, and **$1.00** against a
3.5¢ bar. Same edge, different quality, different stake.

An unknown or zero threshold takes the **standard** ticket, never the top
rung. Staking the maximum on the markets we understand least is the most
expensive direction for a default to fail in.

**Deliberately not Kelly.** Kelly sizes on the edge you *believe* you have,
so it amplifies estimation error exactly as readily as edge — and our
estimate is the thing currently under suspicion.

---

## 6. Exit policy: there isn't one

**Buy-only, hold to resolution.** No exit logic exists in v1.

Evidence: the reference account placed **zero sell orders in 5,349,724
fills**, and buy-only/hold-to-resolution held across all six profitable
accounts studied. With 5.35M opportunities, if round-tripping paid on this
venue class someone would have found it.

The arithmetic agrees for our venue. Polymarket US quotes in whole cents; at
a 50¢ contract, crossing is ~2¢ ≈ 4% of stake, against a claimed edge of
2–3¢. **A round trip costs more than the position earns.** Recycling capital
faster at −2% per cycle doesn't multiply returns, it multiplies losses.

Settlement is also a free exit: exactly $1 or $0, no spread, no slippage, no
liquidity risk.

The capital-recycling benefit is captured instead by **settlement priority** —
nearest kick-offs are priced first, so the same bankroll turns over more
often, and when a cycle truncates it drops the games that would have locked
money up longest.

---

## 7. The exploration tier

Sub-threshold edges trade on a capped budget, tagged separately, so the
threshold is a *measurement* rather than an assumption.

```
min_edge:              0.008   (+ the drift surcharge)
min_consensus_books:   3
budget_share:          25% of the day's deployment
max_fills_per_day:     250
```

Fails **closed**: an unknown consensus depth is not permission. A 1¢ edge
agreed by six books is a signal; the same 1¢ from one book is a rounding
error.

---

## 8. Arbitrage (dutch books)

Separate path, separate logic. A complete outcome set purchasable for under
$1 is guaranteed profit — exactly one leg resolves true and pays $1.

```
enabled:              true
max_sets:             1     contract per leg, per opportunity
max_books_per_cycle:  3
```

**The guarantee holds only when every leg is owned.** Three legs of four is
not an arbitrage; it is a naked directional position at a price nobody
judged. The execution path is built around that:

1. **Thinnest leg first** — the one most likely to fail fails while we own
   nothing.
2. **A failure ends the sequence** — no buying the rest of a set we already
   know won't complete at these prices.
3. **If legs are owned when one fails, completion outranks price** — buy the
   missing legs up to **+10¢** slip. Completing at a small loss is capped at
   `(paid − $1.00)` and known immediately; a naked leg is unbounded and
   unknown until settlement.
4. **Completion buys, never sells** — hold-to-resolution accounting stays
   intact.
5. **Fill-or-kill throughout**, and a short fill counts as a *failed* leg.
6. **Books claiming >10¢/set are refused unexecuted** — that is a mapping
   error or resolution mismatch, not free money.
7. Legs come from **one venue only** — across venues, outcomes can settle on
   different criteria, so the set is not a partition and the guarantee is
   fiction.

Sizing is one set because the venue's fill-or-kill semantics are documented
by the SDK but **have never been observed against the live API**. At one set
the cost of that assumption being wrong is cents.

---

## 9. Worked examples

**CLEARS.** MLB prop, `Shota Imanaga 5+ strikeouts`.
Parsed → `pitcher_strikeouts / Over / 4.5`. Pinnacle quotes both sides at
exactly 4.5 → de-vigged fair 0.540. Anchor present. Venue asks 0.50.
Raw edge 4.0¢. Prop bar at 0.50 is 3.5¢; drift surcharge for props ≈ 0.
4.0¢ ≥ 3.5¢ → clears. 4.0/3.5 = 1.14× → below the 2× rung → **$1.00**.

**PASSED — wrong line.** Same market, but Pinnacle only quotes 5.5.
`5+` is Over 4.5, and 4.5 ≠ 5.5. Refused: `no_prop_quote_at_point`. We do
not interpolate; that is a different bet.

**PASSED — category quarantined.** Liga MX moneyline, venue 0.45, fair 0.50,
5¢ edge. Refused at Gate 3: `moneyline` is globally quarantined.

**PASSED — no anchor.** Liga MX first-half total. Three books quote, none of
them Pinnacle or an exchange. Refused: `no_sharp_anchor`.

**PASSED — implausible.** Per-inning market asking 0.98 against a 0.0214
fair — a 96¢ "edge". Refused at Gate 7. That is a full-game line priced
against a single inning.

**PASSED — dead zone.** Spread at 0.42 with a 4¢ edge would clear on
threshold, but 0.40–0.45 is a moneyline dead zone… and does **not** apply
here, because derivatives don't inherit it. It clears. The same 0.42 price on
a moneyline would be refused outright.

---

## 10. What we measure on ourselves, continuously

| metric | what it catches | current |
|---|---|---|
| **edge drift** (fills) | adverse selection, per category | ml −2.34¢, draw −2.54¢, prop +0.01¢ |
| **retention** | fraction of claimed edge surviving 1 min | ml 0.239, draw 0.146, **prop 1.002** |
| **free samples** (priced, not bought) | background staleness — costs nothing, keeps measuring when nothing trades | 20,108 obs, +0.01¢ |
| **reversion** | winner's curse from our own noise | keep 0.997 → **not the mechanism** |
| **spread cost** | what crossing costs vs the edge claimed | *built, not yet read* |
| **P&L by category** | whether props are real or a mapping artefact | *built, first data ~24h out* |
| **P&L by band** | whether the inherited global-Polymarket calibration transfers here | *built, n too small to act on* |

**A caveat that matters:** retention 1.002 on props does **not** validate the
prop mapping. Retention compares our fair value against *itself* a minute
later — a stably-wrong number scores 1.00 perfectly. Only settled P&L can
detect a mapping error, and that is the number still outstanding.

---

## 11. Honest status

- **Mode:** LIVE_BETA, $1–1.50 tickets, ~$400 bankroll.
- **Record:** 313 settled, 85W/227L, **−$5.02 on $227.57 staked (−2.21%)**.
  At n=313 with per-trade SD ≈ 1.7, the standard error is ~9.8% — this is
  **0.23σ from zero**. We have no statistical evidence of an edge in either
  direction.
- Three-day trajectory: −21% → +11% → −2%. All noise.
- **To confirm a 2% edge takes ~9,600 settlements.** Props made that
  arithmetic survivable (~150/day vs ~10/day); before them it was five years.

**Stopping rule.** If after 500 settlements under the corrected engine the
point estimate is still negative and retention has not risen above ~0.6,
the premise should be treated as disproven and the system stopped. That
decision should be made by a threshold, not by whoever is most invested in
the answer.

---

## 12. The rules that override everything

1. Refuse rather than guess. An unmapped market is not a cheap one.
2. Never exceed a size cap to make back a loss. The reference account's
   top-500 largest fills lost **$1.07M**. Size discipline *is* the strategy.
3. Log every decision input. A losing period must be diagnosable from the
   record alone.
4. Config is measurement, not opinion. A number here should be traceable to
   something observed.
5. When a measurement contradicts the model, the measurement wins.
