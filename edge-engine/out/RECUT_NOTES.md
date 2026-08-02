# August re-baseline — recut notes

**Window (UTC): `2026-08-01T00:00:00Z` → `2026-08-02T16:22:07Z` (40h 22m).**

Window end is the last complete data instant available: the engine heartbeat
read at `2026-08-02T16:21:54.397464Z` via the `engine-diagnostic` workflow run
[30756459863](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/30756459863),
job 91519353775.

**Bottom line: almost nothing in this document can be recut, and the reason is
not the short window. It is that the store holding the measurements is not
reachable from here and does not support an absolute time filter. Every figure
below that reads `null` reads `null` because the data does not support it, not
because I ran out of room.**

---

## 0. The blocking finding, stated first

You asked to be told immediately if fair-value-at-+60s is not persisted per
fill. **It is not.**

`edge_drift` (`src/edge/ledger/service.py:148`) is declared:

```sql
CREATE TABLE IF NOT EXISTS edge_drift (
    market_key TEXT PRIMARY KEY,   -- ← one row per MARKET
    price      REAL NOT NULL,
    fair_entry REAL NOT NULL,
    ts_entry   REAL NOT NULL,
    fair_later REAL,
    ts_later   REAL,
    category   TEXT
);
```

`market_key` is the primary key. There is one drift observation per market, not
per fill, and no `fill_uid`, no stake, and no league. Consequences:

1. **Drift, retention, surcharge and shrinkage are market-level, not
   fill-level.** They cannot be weighted by stake and cannot be joined to a
   position row one-to-one.
2. **The repeated-entry defect makes this worse than it sounds.** A market we
   entered ~28 times (see §4.1) contributes exactly **one** drift observation.
   The drift sample is therefore biased toward markets we touched once, which
   are systematically the ones the caps did not fail on.
3. `out/trades_2026-08.csv` cannot carry a true per-position
   `fair_at_plus_60s`. It could carry the market's single observation
   duplicated across that market's rows, which would be a fabricated join. I
   have not done that.

A second, independent blocker compounds it — see §1.

---

## 1. Data source inventory

| source | where it lives | reachable from here? | absolute-time filter? |
|---|---|---|---|
| fills (`fills`), positions, realizations, `edge_drift`, `price_drift`, `mode_log` | SQLite on the `edge-shadow` Render worker, `/var/edge-data/edge_ledger.sqlite3` | **no** | n/a |
| shadow/would-be fills (`engine_fills`) | Postgres behind `sportsassets-api` | partially — via `/api/engine/fills`, `/api/engine/summary` | **no** |
| worker-computed aggregates (drift, reversion, spread cost, by-band, by-category, funnel) | recomputed in-process, published inside the `/api/engine/status` heartbeat `detail` blob | yes, **as aggregates only** | **no** |
| free-sample pool (`price_drift`) | same SQLite worker DB | **no** | n/a |
| refusal telemetry | per-cycle counters in the funnel; not persisted | yes, **one cycle at a time** | n/a |
| reference-account study (5.35M fills) | `data/calib_*.csv`, `data/top5_cross_account.csv`, in repo | yes | n/a (historical, fixed) |

**Row counts and time ranges: not obtainable.** This container has no ledger
database — `find . -name '*.sqlite3'` returns nothing. The repository was
cloned fresh; the data lives only on the worker's mounted disk.

### 1.1 The report functions cannot express this window

Every report in `src/edge/ledger/service.py` takes a **rolling** `days: int`,
never an absolute instant:

```
spread_report(days=7)            performance_by_band(days=7)
performance_by_category(days=7)  performance(days=7)
drift_report(days=7)             price_drift_report(days=7)
reversion(days=7)                drift_penalties(days=7)
```

`days=7` from 2026-08-02 reaches back to 2026-07-26 and therefore includes
**five and a half days of pre-window data**. Per integrity rule 4 I have not
carried any of those values forward as a window figure, including the ones that
look unlikely to have changed.

Making the recut computable requires, in this order:

1. add a `since: float | None` parameter to the eight functions above
   (extend the existing implementations — do **not** write second versions);
2. add a CSV export command that reads them;
3. expose it, or run it on the worker;
4. deploy, and re-probe.

That is a code change plus a deploy. I have not made it in this pass because
you asked for figures and a diff report, not a schema change, and because the
deploy interacts with the live incident in §4.1.

### 1.2 What the API does and does not carry

`engine_fills` (`backend/sportsassets/api/app.py:225`) has `ts`, `venue`,
`market_id`, `outcome_id`, `league`, `band`, `limit_price`, `size_usd`,
`fair_value`, `edge`, `would_fill`, `settled`, `payout`, `pnl`, `settled_at`.

It does **not** have: `category`, `fair_at_plus_60s`, `threshold_required`,
`ladder_rung`, or an `executable_price` distinct from `limit_price`. Six of the
eighteen columns you specified for `trades_2026-08.csv` have no source.
`/api/engine/fills` is additionally capped at `limit ≤ 500` with no time
parameter, so a window cannot be paged out of it.

---

## 2. Classification of every number in PHILOSOPHY.md

### CONFIG — no date, unaffected by the window

| § | claim | source |
|---|---|---|
| 3.1 | the eight-book list | `feed.py` `ANCHOR_BOOKS`/`BOOK_WEIGHTS` |
| 3.2 | weights 3.0/3.0/3.0/2.0/2.0/2.0/1.0/1.0 | `feed.py` `BOOK_WEIGHTS` |
| 3.4 | `min_anchor_books: 1` | `config/risk.yaml` |
| 4/G1 | 30s quote age, 60s feed age, 5s skew, 25 venue errors, 0.5 tradeable rate | `config/risk.yaml` |
| 4/G3 | `blocked_categories`, `blocklist`, `category_blocks`, `unknown_league_policy` | `config/leagues.yaml` |
| 4/G5 | all 16 moneyline band thresholds; spread/total 3.0/2.5/3.0¢; prop 4.0/3.5/4.0¢ | `config/bands.yaml` (generated) |
| 4/G6 | 3.0¢ surcharge cap, ≥12 observation minimum | `service.py` `DRIFT_MAX_PENALTY`, `DRIFT_MIN_N` |
| 4/G7 | `max_believable_edge: 0.08` | `config/risk.yaml` |
| 4/G8 | all nine risk caps | `config/risk.yaml` |
| 5 | ladder `{at:1.0,$1.00}`, `{at:2.0,$1.50}` | `config/risk.yaml` |
| 7 | `min_edge 0.008`, `min_consensus_books 3`, `budget_share 25%`, `max_fills 250` | `config/risk.yaml` |
| 8 | `enabled: true`, `max_sets: 1`, `max_books_per_cycle: 3`, +10¢ completion slip, 10¢/set refusal | `config/risk.yaml`, `arbitrage.py` |

**One CONFIG claim is now factually wrong** and needs editing regardless of the
recut: §4 Gate 4 says *"maker-first enabled (default on)"* and §11 says
*"Mode: LIVE_BETA"*. Both changed in commit `ae436c6` — mode is `PAPER`,
`EDGE_PMUS_MAKER_FIRST` defaults to `0`.

### MEASURED_OURS — must be recut, and cannot be

| § | claim | recut status |
|---|---|---|
| 3.4 | the 2026-07-31 anchor-coverage table (440 props, 191 Liga MX, 31 NHL) | **out of window** (07-31) |
| 4/G3 | `moneyline n=95 −2.34¢ ret 0.239`, `draw n=31 −2.54¢ ret 0.146`, `free pool n=12,142 +0.01¢` | **not window-filterable** |
| 4/G6 | surcharges `draw +2.71¢`, `moneyline +2.34¢`, `overall +0.98¢` | **not window-filterable** |
| 4/G6 | `keep = 0.997` on 20,108 free samples | **not window-filterable** |
| 10 | the entire self-measurement table | **not window-filterable** |
| 11 | `313 settled, 85W/227L, −$5.02 on $227.57 (−2.21%)` | **see §3 — untraceable** |
| 11 | three-day trajectory −21% → +11% → −2% | **not recoverable** (no per-day settlement timestamp; §4.3) |

### MEASURED_EXTERNAL — not ours, flagged for your labelling decision

| § | claim |
|---|---|
| 6 | 5,349,724 fills, **zero sell orders** |
| 6 | buy-only held across **all six** profitable accounts |
| 12 | top-500 largest fills lost **$1.07M** |
| 4/G5 | the four dead zones, "measured negative or flat across 5.35M reference fills" |
| — | (repo, not the document) `data/calib_price.csv`, `calib_league.csv`, `capacity_sizebuckets.csv`, `regime_monthly_exact.csv`, `top5_cross_account.csv` |

Per your instruction these are neither recut nor deleted. Each needs an
explicit relabel from you as **third-party observational research on another
trader's public on-chain history**, not BettorToken track record. As the
document currently reads — §6 "Evidence:" and §12 rule 2 — a reader has no cue
that these are someone else's trades.

### DERIVED

| § | claim | depends on |
|---|---|---|
| 11 | per-trade SD ≈ 1.7 | the 313-settlement population |
| 11 | standard error ~9.8% | same |
| 11 | **0.23σ from zero** | same |
| 11 | ~9,600 settlements to confirm a 2% edge | assumed 2% effect + assumed SD |
| 11 | ~150/day vs ~10/day settlement rate | fill mix |
| 5 | "4¢ takes $1.50 against a 2¢ bar, $1.00 against 3.5¢" | ladder config (arithmetic only — verified correct) |
| 4/G8 | "retention 0.239 says fair follows the price about three-quarters of the way" | the drift table |

### UNTRACED — I could not tie these to a source

You said this list matters more than the figures. It is the most important
section here.

1. **`313 settled, 85W/227L, −$5.02 on $227.57` (§11) — I cannot reproduce it
   from any store I can reach, and the two stores that should agree do not.**
   At probe time:
   - engine ledger, last 7d, live only: **1,028 fills, $583.08 staked,
     `settled 0`, realized $0**;
   - platform cohort: **55 positions, $311.71 staked, 3 settled, −$1.58**;
   - risk manager's own day counter: **$390.22 deployed**.

   Three numbers for the same quantity. None is 313 settlements or $227.57.
   The 313 figure predates the current stores or came from a fourth path. **It
   should not appear in the document until it can be reproduced.**

2. **§4 Gate 3 says `n=95` for moneyline; `config/leagues.yaml` says `~126
   fills` for the same measurement; the live probe says `n=100`.** Three
   sample counts for one cohort.

3. **§4 Gate 6 lists `draw +2.71¢` but the live probe reports `draw +2.46¢`,
   and Gate 3 reports drift `−2.54¢`.** The surcharge should equal
   `max(0, −drift)`, so Gate 3 and Gate 6 disagree with each other *within the
   document* (2.54 vs 2.71) as well as with the engine (2.46).

4. **§4 Gate 6 says `keep = 0.997` across `20,108` samples; the probe says
   `0.9964` across `21,086`.** Drifting apart in real time — expected for a
   live figure in a static document, which is exactly why §10's "current"
   column is a maintenance hazard.

5. **§10 claims prop retention `1.002` on n=201 and §4 Gate 3's config comment
   claims n=196.** Same cohort, two counts.

6. **§11 "~150/day vs ~10/day"** — no source. Not in any report I can find.

7. **§4 Gate 8 `daily_loss_halt: $15, or 15% of the day, or 4σ`** — the probe
   reports `halt_at=61.03`, which is neither $15 nor 15% of $232.78 ($34.92).
   I could not derive 61.03 from the documented rule.

---

## 3. In-window figures

See `out/figures_2026-08.json`. Every figure is `null`. The `null_reason` on
each is one of:

- `no_absolute_window_filter` — the implementing function takes rolling `days`
  only (§1.1);
- `store_unreachable` — lives on the worker's disk (§1);
- `not_persisted_per_fill` — `edge_drift` is keyed by market (§0);
- `n_below_threshold` — genuinely too few observations;
- `source_untraced` — see §2 UNTRACED.

**The one figure I can state with confidence is the sample size, and it is the
one that matters:** in-window settlements are **3** (platform cohort) or **0**
(engine ledger). At n=3, every statistic in Step 3 is `null` on sample size
alone, independent of every other blocker.

`out/trades_2026-08.csv` and `out/refusals_2026-08.csv` are written
**header-only**, with the reason recorded in each file. I did not populate them
with partial or reconstructed rows.

---

## 4. What breaks

### 4.1 A live control incident outranks the re-baseline

The probe surfaced three cap failures, not one. Reporting them here because
they change what the recut is measuring:

- **Per-market cap.** `aec-mlb-det-ath` $28.09, `aec-mlb-stl-tor` $27.54,
  `atc-mlb-stl-tor-f5-stl` $26.10, `aec-mlb-wsh-atl` $25.44 — against a
  **$1.50** cap. A single fill sizes correctly, so these are ~25–28 repeated
  entries each.
- **Day budget.** `spent=390.22` against `day_cap=232.78` — **68% over**, with
  `fills_left=0`. The day cap did not bind either.
- **Blocklist leak.** Two open positions in a blocklisted league:
  `atc-ucl-din-kau-2026-08-04-din` and `atc-ucl-mja-sba-2026-08-04-draw`.
  `SPORT_KEY_LEAGUE` (`feed.py:83`) maps `soccer_uefa_champs_league → ucl`,
  but **not** `soccer_uefa_champs_league_qualification`. Champions League
  qualifiers arrive unmapped, `unknown_league_policy: allow` admits them, and
  the `ucl` blocklist entry never fires. This is precisely the failure mode
  `CLAUDE.md` warns about: *"Blocked sport keys must appear in
  `SPORT_KEY_LEAGUE` or the blocklist cannot reach them."*

**The diagnostic's `system` / `manual` split is wrong and should not be
trusted.** It classifies by a `cost <= $5.00` boundary, so the engine's own
runaway positions are labelled `manual`. All twenty "manual" positions are
`aec-mlb-*` or `atc-mlb-*-f5-*` dated 2026-08-02 — a systematic pattern, not
hand-placed trades. You were right to push back on this; the heuristic is
circular, since it assumes the caps held in order to decide who placed a trade.

**Effect on the recut:** the in-window fill population is contaminated by the
defect. Any per-category or per-band figure computed over it weights the
runaway markets ~28×. A clean baseline needs a window that starts *after* the
fix, not at 2026-08-01.

### 4.2 The moneyline quarantine has no in-window evidence

It was imposed on `n=95` (or 126, or 100 — see §2 UNTRACED 2), which is
pre-window. In-window the cohort cannot even be counted (§1.1).

**The quarantine remains correct policy.** −2.34¢ drift with 0.239 retention is
a large effect and the free-sample control at +0.01¢ rules out staleness. But
after this re-baseline the document would assert it with **no supporting
evidence inside the window**. It should be relabelled as *policy carried
forward from a pre-window measurement, pending re-measurement*.

### 4.3 Drift surcharges read 0.00¢ — because unmeasured, not because absent

`DRIFT_MIN_N = 12`. In-window, per-category counts are unobtainable, so every
surcharge recuts to `null`. If the recut were run naively it would emit
**0.00¢**, and the engine would read that as *"no adverse selection, trade at
the base bar"* — the most expensive possible misreading, on the exact cohort
measured at −2.3¢.

This is not hypothetical: `strategy_filter` takes `drift_penalty=0.0` as its
default. **An unmeasured surcharge and a measured-zero surcharge are the same
value to the engine and must not be.** That is a defect worth fixing whether or
not you proceed with the re-baseline.

### 4.4 The stopping rule cannot be evaluated

§11 requires 500 settlements under the corrected engine. In-window: **3**
(platform) or **0** (ledger).

- Against 500: **0.6% complete**.
- Projected date: **`null`**. Three settlements over 40 hours is not a rate. I
  will not extrapolate it, and the population is contaminated per §4.1 anyway.
- The engine is now `PAPER` (commit `ae436c6`), so the live settlement rate is
  **zero** until it is re-armed. The clock is not running.

### 4.5 Statistical power — worse, and you should expect that

Cumulative: 0.23σ from zero. In-window at n=3: **`null`**, and no arrangement
of 3 settlements produces a distinguishable result. The honest statement is not
"weaker evidence" but **"no evidence, of anything, in either direction."**

The re-baseline does not make the record look better. It makes it look like
what it is: an engine with no measured edge and a broken cap, that has been
running for 40 hours.

### 4.6 Dead zones and buy-only become inherited assumptions

Both rest entirely on MEASURED_EXTERNAL (§2). Excluded from the window, the
four dead zones and the no-exit policy have **zero in-window support** and
become inherited priors. Flagged, not resolved — your call.

Note the asymmetry when you decide: the buy-only policy is additionally
supported by venue arithmetic that holds independently of the reference account
(§6: ~2¢ crossing cost against a 2–3¢ claimed edge). The dead zones have no
such independent support — they are purely the external study.

---

## 5. Verification performed

| check | result |
|---|---|
| `net` two independent ways | **not performed** — no in-window position rows exist to sum, and no ledger balance series is reachable |
| assert no row predates window start | **vacuously true** — both CSVs are header-only; the assertion is written into the generator but has no rows to test |
| entry-basis vs settlement-basis populations | **both null.** Entry-basis: not filterable (§1.1). Settlement-basis: additionally blocked by §4.3's missing resolution timestamp |
| timezone | ledger timestamps are Unix epoch (`ts REAL`), zone-free; Postgres uses `timestamptz`. No mixing. Window boundary is unambiguous |

## 6. Confidence, and the weakest link

- **High confidence:** the blockers themselves — schema, function signatures,
  API surface. These are read directly from code and I have cited line numbers.
- **High confidence:** the three cap breaches. Read from a live probe with
  slugs and dollar amounts.
- **Moderate:** the UNTRACED list. I searched the stores I can reach; a
  fourth path I have not found could explain the 313-settlement figure.
- **The weakest link, and it is not close:** *nothing in this repository can
  currently reproduce a historical figure.* The reports are rolling-window
  only, the raw store is unreachable, and three separate accounting paths
  disagree about how much has been staked today. A re-baseline is a reporting
  exercise; what the evidence actually calls for is a reproducible export
  before any number — old or new — is trusted.

## 7. Assumptions made

1. "CHUNKER" is this repository (`matthewtaylor141-lab/SportsAssets`,
   `edge-engine/`). The description matches exactly; no repository named
   CHUNKER is reachable from this session.
2. Window start `2026-08-01T00:00:00Z` as specified. Ledger timestamps are
   zone-free epoch seconds, so no conversion was required.
3. Window end is the probe instant, not local midnight — it is the latest
   instant for which data exists.
4. "Our fills" means `mode IN ('LIVE_BETA','LIVE')`. PAPER fills are excluded;
   after `ae436c6` all new fills are PAPER, which is a boundary to watch.
