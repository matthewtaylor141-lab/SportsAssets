# Data-availability audit — price-band strategy study on Polymarket US

**Date:** 2026-09-10
**Scope:** Sections 1–4 of the research brief (data acquisition, data audit, market universe,
trade definitions) plus the parameter-grid addendum.
**Status:** Audit complete. **The headline study is not testable on the requested 12-month
period.** No strategy result is reported here, because none is supportable.

Every figure below is a `SELECT` against the production database (`sportsassets-db`,
`dpg-d9gcudurnols73ce4avg-a`) run at 2026-09-10T20:14:47Z via the read-only `data-audit`
preset, or a `grep` against this repository. Nothing is estimated, modelled or assumed unless
the line says so.

---

## 1. Verdict

Against the brief's own classification scheme, the headline question —
*buy the executable ask in 0.30–0.35, rest a sell at 0.40, protective exit at 0.20, over the
most recent completed 12 months of Polymarket US-regulated sports markets* — is:

> **(1) Not testable due to insufficient data.**

Three independent reasons, each sufficient on its own:

1. **There is no historical price endpoint on the US venue.** `backend/sportsassets/pmus.py`
   — the entire US client — contains no `history`, `candle`, `timeseries`, or `prices-history`
   route. It reads live BBO and live book only. Historical US bid/ask and historical US
   order-book depth cannot be *retrieved* at any fidelity, for any period, by any code in this
   repository.
2. **The stored US quote series is 9 days long, not 12 months.** The only table holding a US
   bid *and* ask is `mirror_shadow`: 2026-09-02 → 2026-09-10.
3. **The stored US series is event-triggered, not a market scan.** `mirror_shadow` only writes
   rows for markets that entered the mirror's candidate set — i.e. markets RN1 traded. An
   entry-band study needs to observe every market's ask continuously in order to detect when
   it *enters* the band. We observe a biased subset, at tick cadence, for 9 days.

Consequence for the brief's execution models:

| Model | Buildable for US markets? | Why |
|---|---|---|
| 1 — naive touch | **No** (12 mo) / partial (9 d) | No US price history exists to touch |
| 2 — top-of-book | **No** (12 mo) / partial (9 d) | Only 9 days of two-sided US quotes |
| 3 — queue/depth | **No** | Depth exists for 37 days, ask-side only, event-triggered |

The brief requires the final recommendation to prioritise fill-based (Model 3) results.
**Fill-based results for US markets cannot be produced from any data source available here.**

---

## 2. Environment constraint found first

This session's container **cannot reach Polymarket at all.** The network policy returns 403 on
`CONNECT` for both hosts:

```
gamma-api.polymarket.com   -> ProxyError 403 Forbidden   (2026-09-10T20:0xZ)
gateway.polymarket.us:443  -> gateway answered 403 to CONNECT   (2026-09-10T17:39:12Z ×4)
```

All venue and database access therefore runs on a GitHub Actions runner, which has open
internet — that is why `pmx-preprod.yml` exists and says so. Practical effect on this study: no
ad-hoc data pulls from the analysis environment; every retrieval is a workflow dispatch.

---

## 3. Data inventory — what exists, exactly

### 3.1 Row counts and date coverage

Measured 2026-09-10T20:16:40Z. `markets` counts distinct `condition_id` unless noted.

| Source | Rows | First day | Last day | Span (d) | Markets |
|---|---:|---|---|---:|---:|
| `trades` — whale fills, **GLOBAL venue** | 5,389,214 | 2024-11-16 | 2026-09-10 | 663 | 150,030 |
| `copy_probes` — US ask + depth JSONB | 948,015 | 2026-08-04 | 2026-09-10 | 37 | 77,382 assets |
| `mirror_shadow` — **US bid/ask** | 240,417 | 2026-09-02 | 2026-09-10 | 8 | 6,417 |
| `trade_marks` — global 5/10/60-min marks | 206,262 | 2026-09-02 | 2026-09-10 | 8 | — |
| `mirror_orders` — **our own real orders** | 11,183 | 2026-09-06 | 2026-09-10 | 4 | 1,461 books |
| `mirror_books` | 1,631 | 2026-09-06 | 2026-09-10 | 4 | 1,191 |
| `price_path` — post-fill ask samples | 8,953 | 2026-09-02 | 2026-09-04 | 2 | — |

Other tables of size: `notification_outbox` 4.93M, `positions` 336,273, `market_tokens`
238,171, `us_premap` 145,152, `markets` 119,090, `pmus_activity_archive` 43,902,
`mirror_candidate_refusals` 42,570, `api_positions` 41,103, `market_starts` 15,300,
`mirror_fill_answers` 12,832. (Counts from `pg_stat_user_tables.n_live_tup`, which is an
estimate; the seven rows above are exact `count(*)`.)

### 3.2 The US quote series in detail — the number that decides the study

`mirror_shadow`, whole table:

| obs | with **both** bid and ask | ask > bid | markets | US slugs | first | last | days with data | mean spread | median spread |
|---:|---:|---:|---:|---:|---|---|---:|---:|---:|
| 240,453 | 169,726 (70.6%) | 169,726 (100%) | 6,418 | 2,341 | 2026-09-02 | 2026-09-10 | 9 | 0.0286 | **0.0100** |

Two-sided observations per market:

| two-sided obs | markets |
|---|---:|
| 500+ | **0** |
| 100–499 | 656 |
| 30–99 | 971 |
| 10–29 | 391 |
| 1–9 | 227 |
| none | 4,173 |

**No market anywhere in the dataset has 500 two-sided quotes.** 1,627 markets have ≥30. A
path-dependent entry→target/stop reconstruction on ~30–500 samples spread over a market's life
cannot resolve intrabar ordering, which the brief explicitly forbids resolving arbitrarily
(§6).

### 3.3 The global whale tape — 14 months, but the wrong object

`trades` is the one source with real longitudinal coverage:

| month | trades | days with data | markets |
|---|---:|---:|---:|
| 2024-11 | 682 | 1 | 1 |
| *(gap: 2024-12 … 2025-06 — no rows)* | | | |
| 2025-07 | 4,570 | 26 | 294 |
| 2025-08 | 20,551 | 31 | 1,149 |
| 2025-09 | 52,845 | 30 | 2,364 |
| 2025-10 | 184,370 | 31 | 5,016 |
| 2025-11 | 407,471 | 30 | 8,306 |
| 2025-12 | 350,805 | 31 | 7,245 |
| 2026-01 | 138,092 | 31 | 3,813 |
| 2026-02 | 63,348 | 28 | 2,137 |
| 2026-03 | 104,953 | 31 | 4,359 |
| 2026-04 | 319,313 | 30 | 8,502 |
| 2026-05 | 428,268 | 31 | 10,074 |
| 2026-06 | 618,432 | 30 | 11,852 |
| 2026-07 | 655,970 | 31 | 19,715 |
| 2026-08 | 1,520,628 | 31 | 51,484 |
| 2026-09 | 518,996 | 10 (month partial) | 15,772 |

Continuous coverage runs **2025-07-01 → 2026-09-10**, with no month missing more than a few
days. The 2024-11 row is a single-day backfill artifact separated from the series by a
seven-month gap; it should be excluded, not interpolated across.

**Why this does not rescue the study.** These are *fills by 22 rostered whales on Polymarket
global*, not a market tape and not the US venue. They cannot answer "when did this contract's
ask enter 0.30–0.35", because we only observe prices at moments a rostered whale happened to
trade. Using them as a price series would import a severe selection bias — the observations
exist *because* an informed trader acted.

### 3.4 Order-book depth

`copy_probes` is the only table holding depth (`depth JSONB`, top ask levels; plus `best_ask`,
`best_ask_usd`, `vwap_1k`, `vwap_5k`, `fillable_1k`, `fillable_5k`). Limits:

- **Ask side only.** No bid depth is stored anywhere, so a resting *sell* can never be queued.
- **Event-triggered.** One row per whale fill we probed, at probe time. Not a time series.
- **Pruned at 37 days** by `workers/retention.py` (`COPY_PROBES_FLOOR_DAYS = 30 + 7`), enforced
  hourly. Older depth is already deleted and unrecoverable.

`engine_fills.book` holds a top-of-book snapshot at decision time; `mirror_shadow` holds best
bid/ask only, no sizes.

### 3.5 What is genuinely high quality here

`mirror_orders` (11,183 rows, 4 days) is **real execution evidence, not a simulation**: every
row carries the wire price, `post_only`, `qty`, `filled`, `avg_px`, `state`, `maker`, and
`reason`. For the one question the brief cares most about — *would a resting order have
filled* — this beats any queue model, within its 4-day window and at the prices we actually
rested at. Measured earlier today: resting buy 26.5% (924 orders), resting sell 30.7% (1,415),
taking buy 45.6% (4,360), taking sell 41.3% (4,484).

`pmus_activity_archive` (43,902 rows) is the US venue's own activity feed for **our account**,
archived from a sliding ~1,200-row window — ground truth for our fills, not a market tape.

---

## 4. Market universe — what §3 of the brief asks for, and what is missing

`markets` (119,090 rows) is a **global Polymarket** universe keyed by CTF `condition_id`:

| sport | markets | resolved | closed |
|---|---:|---:|---:|
| Soccer | 47,535 | 40,420 | 40,430 |
| Tennis | 18,166 | 14,702 | 14,932 |
| Non-Sports | 17,246 | 14,225 | 14,243 |
| MLB | 15,894 | 14,290 | 14,294 |
| NBA | 7,419 | 7,296 | 7,296 |
| Other-Sports | 7,377 | 5,922 | 5,991 |
| NFL | 3,250 | 2,142 | 2,145 |
| NHL | 1,634 | 1,629 | 1,629 |
| MMA | 570 | 334 | 337 |
| Golf | 2 | 1 | 1 |

`markets.updated_at` starts 2026-07-22 — metadata refresh began then, so pre-July market
metadata is absent even where the market itself is older.

Fields the brief requires that we **do not hold**:

- **A US market universe at all.** US identity lives only in `us_premap` (145,152 rows) and
  `mirror_books.us_market_slug` — the subset of RN1's flow we could resolve. There is no table
  enumerating tradable US markets.
- `event_id`, market creation time, market close time, `volume`, `liquidity`, active/inactive
  status. `market_starts` (15,300 rows) supplies `game_start` for a fraction of markets.
- **Market type is not a column.** It is inferable from the US slug grammar (`aec-` moneyline,
  `asc-` spread, `tsc-` total, `atc-` per-outcome, `astatc-` props) but only for mapped rows.
- **Pre-game vs live status** is not recorded. It is partly derivable from `market_starts`.
- Cancellations, corrections, halts and suspensions are not stored as history.

`resolved_prices` on `markets` does give settlement payout per outcome index — settlement data
is the one universe field that is clean.

---

## 5. Fees

Reconstructible only in part, and only for our own trades. The US venue populates `fees` and
`baseCost` beside `cost` on position objects, and `cost = baseCost + fees` holds exactly.
Measured today across 8 settled positions (6 paid a fee): the rate sits near
**6% of `shares × price × (1 − price)`** on five of six, and **the one position built entirely
from resting orders paid nothing**, while every position that paid contained a taking fill.

Treat that as an estimate from n=6, not a schedule. There is no historical fee schedule stored,
and `pmus._commission_fields:2492` names three keys that have never held a value — our code
reads the wrong field names, so fee capture is not yet automatic.

---

## 6. The one rigorous result available today: break-even arithmetic

This needs no backtest. Take the proposed configuration at the band midpoint, entry 0.325,
target 0.40, stop 0.20:

- win = 0.400 − 0.325 = **+0.075** per share
- loss = 0.325 − 0.200 = **−0.125** per share
- `required_probability = avg_loss / (avg_win + avg_loss)` = 0.125 / 0.200 = **62.5%**

**Before any costs, the configuration must reach the target first 62.5% of the time merely to
break even.** The reward:risk is 1:1.67 against.

Now add costs, using the ~6% fee estimate above and assuming both legs cross (taker):

- entry fee at 0.325: 0.06 × 0.325 × 0.675 = 0.0132/share
- exit fee at 0.400: 0.06 × 0.400 × 0.600 = 0.0144/share
- exit fee at 0.200: 0.06 × 0.200 × 0.800 = 0.0096/share
- net win = 0.075 − 0.0132 − 0.0144 = **+0.0474**
- net loss = 0.125 + 0.0132 + 0.0096 = **−0.1478**
- required probability = 0.1478 / 0.1952 = **≈ 75.7%**

**Taking both ways, the configuration needs roughly a 76% target-first rate to break even** —
before spread, slippage, partial fills, or the settlements that resolve before either threshold.

If both legs rest as post-only (which today's fee evidence suggests is free), the requirement
stays at 62.5%, but the strategy then inherits the fill problem instead: our measured resting
fill rates are 26.5% (buy) and 30.7% (sell), so a large share of intended entries and target
exits simply never happen.

*Labelling per the brief: the 62.5% figure is arithmetic. The 75.7% figure is a model estimate
resting on an n=6 fee measurement. Neither is a backtest result, and neither says whether the
observed target-first rate clears the bar — that is the untestable part.*

---

## 7. What would make the study testable

In priority order.

1. **Start collecting the US board now.** `mirror_shadow` already writes bid/ask/mark per tick
   and has proven itself over 9 days and 240k rows. Widening it from RN1's candidates to the
   whole tradable US board, with sizes at top of book, produces a genuine Model-2 dataset. At
   the current write rate that is a statistically usable panel in roughly 8–12 weeks and a
   12-month panel in a year. **Nothing else on this list substitutes for it.**
2. **Record top-of-book sizes, both sides.** Best bid/ask without quantities cannot support
   Model 3 at any horizon. This is a small schema change with a large payoff.
3. **Fix fee capture.** Read the venue's real `fees`/`baseCost` fields rather than the three
   dead keys at `pmus._commission_fields:2492`, so the fee schedule is measured continuously
   instead of inferred from six settlements.
4. **Raise the `copy_probes` retention floor** if depth history matters — currently 37 days and
   deleting daily.
5. **Then, and only then, run the parameter grid.** The entry-band × target × stop × exit-method
   × sport grid in the addendum is roughly 10⁴–10⁵ cells. Running it on 9 days of biased,
   one-sided-depth data would produce confident-looking numbers with no information in them, and
   the multiple-testing correction the brief mandates (§10) would correctly annihilate every one.

**A defensible interim study** is possible and worth doing: Model 2 only, the 1,627 US markets
with ≥30 two-sided quotes, 2026-09-02 → 2026-09-10, pre-specified bands only, no out-of-sample
period, reported as a **pilot that establishes the pipeline and the definitions** — not as
evidence about expectancy. Its value is that the ingestion, the trade-path logic, the leakage
audit and the report generator all get built and tested, so that when the panel is long enough
the study runs on one command.

---

## 8. Reproduction

```
# row counts per table
gh workflow run render-ops.yml -f action=sql -f service=sportsassets-db -f arg=tables

# this audit (five SELECTs, read-only, writes nothing)
gh workflow run render-ops.yml -f action=sql -f service=sportsassets-db -f arg=data-audit
```

Run 34524985692 (`tables`) and run 34525195782 (`data-audit`) are the exact runs quoted above.
Both are read-only. The preset SQL is in `.github/workflows/render-ops.yml` under the
`data-audit)` case label.

---

## 9. Limitations of this audit

- Table sizes outside the seven exact rows are `n_live_tup` estimates and can differ materially
  from `count(*)` — `price_path` estimated 0 and counts 8,953.
- Coverage is measured on ingestion timestamps, not venue event times; a row's `at`/`probe_at`
  is when we observed, not necessarily when the market moved.
- `mirror_shadow`'s 9-day span is measured on rows present today. Nothing in `retention.py`
  prunes it, so 2026-09-02 is when the writer started, not a deletion boundary.
- No claim is made here about whether any band is profitable. The audit's finding is that the
  question cannot currently be answered, which is not the same as an answer.
