# Reply to the "recut CHUNKER figures to the August window" task

**Read this before starting. Most of what the task asks you to build already
exists in this repo, three of its premises are wrong, and one of its
deliverables cannot be produced from any data that exists.** Building it
again from scratch would produce a second implementation of metrics the
engine already computes — which the task itself forbids, and which is the
specific failure mode most likely to make the recut disagree with the engine
it is meant to describe.

---

## 0. Repository

There is no repository named CHUNKER. The description matches
`matthewtaylor141-lab/SportsAssets`, working directory `edge-engine/`.
Everything below is relative to that.

Work on branch `claude/session-njaewf`.

---

## 1. The blocking answer you were told to give immediately

**Fair-value-at-+60s is NOT persisted per fill.**

`src/edge/ledger/service.py`, table `edge_drift`:

```sql
CREATE TABLE IF NOT EXISTS edge_drift (
    market_key TEXT PRIMARY KEY,   -- one row per MARKET, not per fill
    price      REAL NOT NULL,
    fair_entry REAL NOT NULL,
    ts_entry   REAL NOT NULL,
    fair_later REAL,
    ts_later   REAL,
    category   TEXT
);
```

`market_key` is the primary key. There is no `fill_uid`, no stake, no
league. Consequences you must carry into every figure:

1. Drift, retention, surcharge and shrinkage are **market-level**. They
   cannot be stake-weighted and cannot be joined one-to-one to a position.
2. Under one-position-per-market the join is nonetheless exact, because
   there should be one entry per market. **That assumption was violated on
   2026-08-02** — a defect put ~25–28 entries into each of four MLB markets,
   and each of those markets still contributes exactly one drift
   observation. So the in-window drift sample is biased toward markets the
   caps did not fail on.
3. `positions_export()` exposes the column as `fair_at_plus_60s` with a
   sibling `entries` count. **Do not duplicate a market's single observation
   across multiple entry rows and call it per-position data.** If `entries`
   > 1, the column describes the market, not the entry.

---

## 2. Do not build the tooling — it was built on 2026-08-02

Commit `e41f10b`. Use it, and cite it.

### The commands

```bash
cd edge-engine

# Every figure, absolute window, JSON with n / unit / source / null_reason
python -m edge.cli figures --since 2026-08-01

# figures.json + positions.csv + METHODOLOGY.md from one window
python -m edge.cli export --since 2026-08-01 --out out/recut-2026-08

# entry-basis vs settlement-basis: --all-modes includes PAPER fills
python -m edge.cli figures --since 2026-08-01 --all-modes
```

`--since` accepts `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SSZ`, interpreted as
UTC. Verified: `--since 2026-08-01` resolves to epoch `1785542400.0`.

### Where each metric is implemented — cite these, do not reimplement

| Task metric | Implementation |
|---|---|
| `n_settled`, `wins`, `losses`, `staked`, `net`, `return_pct` | `ledger.performance()` |
| `sd_per_trade`, `se`, `sigma_from_zero` | `ledger.performance()` — added `e41f10b` |
| Daily series | `ledger.performance_daily()` |
| Drift & retention per category | `ledger.drift_report()` |
| Drift surcharge | `ledger.drift_penalties()` |
| Free samples | `ledger.price_drift_report()` |
| Reversion / keep | `ledger.reversion()` |
| Position-level export | `ledger.positions_export()` |
| Settled P&L by category / band | `ledger.performance_by_category()`, `performance_by_band()` |
| Spread cost at entry | `ledger.spread_report()` |
| Figure assembly, null gating | `edge/reporting/figures.py::compute_figures` |
| CSV writer + window assertion | `edge/reporting/export.py::write_positions_csv` |

All eight report functions take **both** `days` (rolling) and `since`
(absolute); absolute wins. Resolved by `ledger.window_start()`.

Before `e41f10b` they took rolling `days` only, so "since 2026-08-01" was
inexpressible and `days=7` asked on 2026-08-02 silently reached back to
2026-07-26.

---

## 3. Three premises in the task that are wrong

**a. "Recomputing on a ~32-hour window."** The window is ~40 hours to the
last probe, and the engine went to `PAPER` on 2026-08-02 (commit `ae436c6`).
The live settlement clock is stopped. The window does not grow until it is
re-armed.

**b. "If categories fall below n=12, the charged surcharge is 0.00¢."** Not
true of the engine. `drift_penalties()` makes a category under
`DRIFT_MIN_N` **inherit the overall surcharge**, not trade free. Zero is
reached only when nothing at all is measured. Report `null` for the figure,
but do not tell the user the engine would charge nothing — it would not.

**c. "The stopping rule in §11 is written against 500 settlements."** It is,
but **§11's own headline — "313 settled, 85W/227L, −$5.02 on $227.57" —
cannot be reproduced from any store in this system.** See §5.

---

## 4. What cannot be produced at all

**`out/refusals_2026-08.csv` is not computable.** Refusal counters are
per-cycle funnel counters in `shadow/runner.py::run_cycle`, discarded at the
end of each cycle. Nothing persists them. One ~22-second cycle is visible at
a time via the status heartbeat. There is no window to aggregate.

Producing this file requires a new counter table and a deploy. Say so;
do not sample one cycle and present it as a window.

**Six of the eighteen `trades_2026-08.csv` columns have no source:**
`event_id`, `outcome`, `executable_price` (nothing distinguishes it from
`limit_price`), `ladder_rung` (`tier` is the nearest proxy — `core` vs
`exploration` — not a rung), `resolution` as a string (only `payout`
0.0/1.0 exists), and `market_id` separate from `market_key`.

`positions_export()` emits what does exist. Do not invent the rest.

**Step 6's "recompute `net` two independent ways" cannot be satisfied.**
There is no ledger balance series — no end-minus-start figure to compare a
per-trade sum against. Report the discrepancy you *can* surface instead:
three accounting paths disagreed about the same day's stake at the last
probe — engine ledger $583.08, platform cohort $311.71, risk day-counter
$390.22.

---

## 5. The UNTRACED list — the task says this matters most, and it does

1. **§11's `313 settled, 85W/227L, −$5.02 on $227.57`.** Not reproducible.
   At the 2026-08-02T16:22Z probe: engine ledger reported 1,028 fills,
   $583.08 staked, **0 settled**; platform cohort reported 55 positions,
   $311.71 staked, 3 settled, −$1.58. Neither is 313 or $227.57. **This
   figure should not appear in the document until it can be reproduced.**
2. **Moneyline cohort size is stated three ways**: `n=95` in §4 Gate 3,
   `~126 fills` in the `config/leagues.yaml` comment, `n=100` from the live
   engine.
3. **Draw surcharge disagrees with itself inside the document**: Gate 6 says
   `+2.71¢`, Gate 3 reports drift `−2.54¢` (which implies `+2.54¢`), the
   engine reports `+2.46¢`.
4. **Reversion**: §4 Gate 6 says `keep = 0.997` on `20,108`; engine says
   `0.9964` on `21,086`.
5. **Prop retention n**: `201` in §10, `196` in the Gate 3 config comment.
6. **§11 "~150/day vs ~10/day" settlement rates.** No source in any report.
7. **§4 Gate 8 `daily_loss_halt: $15, or 15% of the day, or 4σ`.** Engine
   reports `halt_at=61.03`, which is neither $15 nor 15% of the $232.78 day
   cap ($34.92). Could not derive it from the documented rule.

Items 2–5 are the same failure: a hand-maintained document with no way to
notice when a number it quotes has moved. That is now fixed for the *new*
document (`edge/reporting/`), not retroactively for `PHILOSOPHY.md`.

---

## 6. Step 2 classification

**CONFIG** (no date, unaffected by the window): the eight-book list and
weights (`fairvalue/feed.py::BOOK_WEIGHTS`); `min_anchor_books`; all 16
moneyline band thresholds and the four dead zones; prop/spread/total
category bands (`config/bands.yaml`, **generated** — see integrity rule 6);
`max_believable_edge`; all Gate 8 caps; the size ladder; exploration
parameters; arbitrage parameters; `blocklist`, `category_blocks`,
`blocked_categories`, `unknown_league_policy`.

Two CONFIG claims are now factually stale regardless of the recut: §4 Gate 4
says maker-first is "default on" and §11 says "Mode: LIVE_BETA". Both
changed in `ae436c6` — mode is `PAPER`, `EDGE_PMUS_MAKER_FIRST` defaults
to `0`.

**MEASURED_OURS** (recut): headline record; three-day trajectory; drift by
category; retention by category; free-sample counts and drift; reversion
keep; the Gate 3 quarantine cohort table; the 2026-07-31 anchor-coverage
table (440 props / 191 Liga MX / 31 NHL — **out of window**, dated 07-31).

**MEASURED_EXTERNAL** (do not recut, do not delete, needs the owner's
labelling decision): 5,349,724 fills; zero sell orders across that history;
buy-only confirmed across six profitable accounts; the $1.07M top-500 loss;
the 5.35M fills backing the four dead zones. Repo files:
`data/calib_price.csv`, `calib_league.csv`, `capacity_sizebuckets.csv`,
`regime_monthly_exact.csv`, `top5_cross_account.csv`.

As §6 and §12 currently read, nothing signals these are another trader's
public on-chain history rather than BettorToken's record. That is the
labelling decision to put to the owner.

**DERIVED**: per-trade SD; standard error; the 0.23σ figure; the ~9,600
settlements-to-confirm estimate; the §5 ladder worked examples (arithmetic
only — verified correct).

---

## 7. Step 4 — what breaks

**Moneyline quarantine evidence.** Imposed on a pre-window cohort. In-window
it is empty or far below `n=12`. **The quarantine remains correct policy** —
−2.34¢ drift at 0.239 retention is a large effect, and the free-sample
control at +0.01¢ rules out staleness — but after a re-baseline the document
asserts it with no supporting evidence inside the window. Relabel as policy
carried forward from a pre-window measurement, pending re-measurement.

**Drift surcharges.** Recut to `null`. See §3b — do not report this as the
engine charging 0.00¢.

**Stopping rule.** In-window settlements: **3** (platform cohort) or **0**
(engine ledger) against a target of 500. That is 0.6%. **Do not project a
completion date.** Three settlements over 40 hours is not a rate, the
population is contaminated (§8), and the engine is in PAPER so the live rate
is currently zero.

**Statistical power.** In-window `sigma_from_zero` is `null` — at n=3 no
arrangement of outcomes is distinguishable from zero. State it as **"no
evidence, of anything, in either direction"**, not "weaker evidence". The
cumulative figure was 0.23σ. The re-baseline does not make the record look
better; it makes it look like what it is.

Note `compute_figures` will null `sigma_from_zero` for a second reason worth
understanding: a sample where every trade returns identically has zero
dispersion, hence no standard error. That is a null, never an infinity.

**Dead zones and buy-only.** Both rest entirely on MEASURED_EXTERNAL.
Excluded, both become inherited priors with no in-window support. Flag, do
not resolve. One asymmetry to hand the owner: buy-only has independent
support from venue arithmetic that holds regardless of the reference account
(~2¢ to cross against a 2–3¢ claimed edge — a round trip costs more than the
position earns). The dead zones have no such independent support.

---

## 8. The finding that should change the sequencing

**The in-window fill population is contaminated, and a recut over it is
measuring a defect.**

Three control failures were live during the window:

1. **Per-market cap breached ~18×.** `aec-mlb-det-ath` $28.09,
   `aec-mlb-stl-tor` $27.54, `atc-mlb-stl-tor-f5-stl` $26.10,
   `aec-mlb-wsh-atl` $25.44 — against a **$1.50** cap. Single fills size
   correctly, so these are ~25–28 repeated entries each. Root cause:
   `reap_pmus_makers()` cleared the maker order context before the venue's
   activity feed reported the fill, which both stranded the fill
   permanently (only parked markets are searched by `sync_pmus_fills`) and
   handed the one-per-market claim back. Fixed in `1861cc4`.
2. **Blocklist leak.** `soccer_uefa_champs_league_qualification` did not
   resolve to `ucl`, so Champions League qualifiers were admitted by
   `unknown_league_policy: allow` despite `ucl` being blocklisted. Two open
   positions confirmed. Fixed in `1861cc4` (longest-prefix resolution).
3. **Day budget mis-denominated** — sized off cash remaining, so it chased
   its own spend. Not an overspend; a cap shrinking under a cumulative
   number. Fixed in `1861cc4`.

Any per-category or per-band figure computed over this window weights the
runaway markets ~28×.

**Also: do not trust the diagnostic's `system` / `manual` split.** It
classified by a `cost <= $5` boundary and therefore reported all twenty of
the engine's own runaway positions as hand-placed. The heuristic is circular
— it assumes the caps held in order to decide who placed a trade. Relabelled
in `1861cc4`.

---

## 9. Recommendation to put to the owner

The task's stated rationale is that a clean window is the honest baseline for
grading the corrected engine. **2026-08-01 is not that window.** The
corrections landed on 2026-08-02 (`ae436c6`, `1861cc4`) and are not yet
deployed. A window starting 2026-08-01 measures the engine that had the bug,
under caps that were not holding, for 40 hours, on 3 settlements.

The defensible baseline starts **when the fix is deployed and verified**.
Until then the recut produces a document of nulls — which is an honest
output, and also not a useful one.

Concretely: deploy `1861cc4`, confirm from the diagnostic that per-market
exposure is back inside $1.50 and that no `ucl` position opens, re-arm from
PAPER, then set the window to that instant and run
`edge export --since <instant>`.

---

## 10. Deliverables status

`out/` on branch `claude/session-njaewf` already contains the null-state
artifacts from the first pass:

- `out/figures_2026-08.json` — every figure `null`, each with `null_reason`
- `out/trades_2026-08.csv` — header only, reason recorded in the file
- `out/refusals_2026-08.csv` — header only, not computable (§4)
- `out/RECUT_NOTES.md` — classification, breakage, UNTRACED, assumptions
- `scripts/recut_august.py` — regenerates all four

Once the data is reachable, `edge export --since 2026-08-01` supersedes
`scripts/recut_august.py` for figures and positions. The refusals CSV stays
uncomputable until refusal counters are persisted.

**One data-access constraint that governs all of this:** the ledger is
SQLite on the `edge-shadow` Render worker at
`/var/edge-data/edge_ledger.sqlite3`. It is not reachable from a checkout —
a fresh clone has no database, and only worker-computed aggregates surface,
inside the `/api/engine/status` heartbeat. **The export commands must be run
on the worker**, or their output published from it. As of `e41f10b` the
worker generates and publishes a methodology bundle at startup and each day
rollover, readable at
`https://sportsassets-api.onrender.com/api/engine/methodology?format=md`.

Do not report figures as computed unless you actually ran the command
somewhere the database exists.

---

## 11. Confidence

- **High** — the blockers, schema facts and function signatures. Read
  directly from code, line-cited.
- **High** — the three control failures. Read from a live probe with slugs
  and dollar amounts (Actions run 30756459863).
- **Moderate** — the UNTRACED list. A path I did not find could explain the
  313-settlement figure.
- **Weakest link, and it is not close:** until the export runs where the
  database lives, nothing in this repository can reproduce a historical
  figure. Three accounting paths disagree about a single day's stake. A
  re-baseline is a reporting exercise; what the evidence calls for first is
  a reproducible export before any number — old or new — is trusted.
