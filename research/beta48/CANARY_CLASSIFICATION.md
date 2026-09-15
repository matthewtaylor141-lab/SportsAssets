# CANARY_CLASSIFICATION — post-repair

Frozen record of the defect the kch123 canary exposed, what it could and
could not bias, and the status of every previously reported RN1 result.
Repair landed in `f93e0f2`; this file classifies it.

---

## The block

**`CANARY_DEFECT` =** TWO defects, both in the reconstruction/reporting
layer, both surfaced by the kch123 job of run `35027932105`:

- **D1 — a significance verdict with no cluster floor.** kch123's
  `LAST_7D` returned 5 closed lots in 5 clusters and the estimator
  reported *"PROFITABLE at 95% — +47.06%, interval [+47.0516%,
  +47.0705%]"*: an interval 0.019 pp wide on five observations. The same
  account's `LAST_14D` (17 lots) read *"LOSING at 95%"*.
- **D2 — regime windows anchored to each account's own last fill.**
  `LAST_7D` meant "the last seven days *this account* traded", not the
  last seven calendar days.

**`ROOT_CAUSE` =**

- **D1:** `merge_pnl.replay` computes a cluster-robust interval but gates
  only on *"fewer than two closed lots"* (`merge_pnl.py:79`).
  `proof.py` has defined `MIN_PROOF_CLUSTERS = 30` since it was written
  and `merge_pnl` does not import it. At five near-identical clusters the
  ratio estimator's between-cluster variance collapses toward zero, so
  the interval narrows to nothing and the verdict becomes noise wearing a
  significance label.
- **D2:** `window()` cut from `max(f["t"])` — the account's own final
  fill — instead of a common wall-clock reference. kch123's pull ends
  2026-06-29, so its "current regime" was late June while RN1's was
  mid-September. The two were then placed in the same table.

**`AFFECTS_RAW_EXTRACTION` = NO.** `reference_pull.py` and
`reference_meta.py` are untouched by the repair and were untouched by the
defect. The raw `trades_raw.csv` / `markets_meta.csv` a defective run
produced are byte-for-byte what a repaired run produces from the same
request. The defect is downstream of the pull.

**`AFFECTS_RECONSTRUCTION` = YES.** Both defects live in
`whale_reconstruct.py`: D1 in the verdict emitted per window, D2 in which
fills each window contains.

**`AFFECTS_RETURN_CHANNEL_ONLY` = NO.** The gzip+base64 log channel
transported the defective values faithfully. The corruption was in what
was computed, not in what was transmitted.

**`COULD_BIAS_PAIR_PNL` = YES, for windowed figures only.**
Precisely: `realized_merge_pnl` over a *given set of fills* is unaffected
by either defect — D1 touches only the verdict string, D2 touches only
window membership. But D2 changes which fills are in `LAST_7D` /
`LAST_14D` / `LAST_30D` / `LAST_60D`, so the windowed pair P&L changes.
**LIFETIME is unaffected**, because `window(days=None)` ignores the
anchor and a test pins that.

**`COULD_BIAS_COMPLETION_RATE` = YES, for windowed grids only.**
`completion_grid` is computed per window, so D2 moves `LAST_30D` and
`LAST_7D` completion rates. The LIFETIME grid is unaffected. D1 cannot
touch a completion rate at all — `completion_grid` never reads a verdict,
and a test asserts that.

**`COULD_BIAS_DIRECTIONAL_PNL` = YES, for windowed figures only,** by the
same mechanism as pair P&L. LIFETIME settled/directional economics are
unaffected.

**`RN1_LOCAL_RESULTS_AFFECTED` = YES** — regime-window results only. The
full enumeration is below.

**`ALREADY_RUNNING_WHALE_JOBS_AFFECTED` = YES.** Every job of run
`35027932105` ran pre-repair code. That run is preserved as
`INVALID_EVIDENCE` and its kch123 job is retained as the defect exhibit.
Run `35028887477` and run `35034361586` are post-repair.

**`REPAIR` =**

- **D1:** `gate_verdict()` suppresses the verdict below 30 clusters to
  `NOT_DEMONSTRATED_INSUFFICIENT_CLUSTERS`, retaining the estimator's own
  wording as `edge_verdict_raw`. The point estimate and the interval are
  still reported — they are facts about the window. Only the *word*
  "PROFITABLE" is withheld from a sample that cannot support it. The
  floor is `proof.py`'s own 30, not a new number.
- **D2:** `--as-of`, a COMMON wall-clock reference (default: now),
  applied to every regime window and every completion grid. LIFETIME
  ignores it. The census additionally carries `AS_OF_UTC` and
  `DAYS_SINCE_LAST_FILL_AT_AS_OF`, so a dormant account is visible as
  dormant instead of being silently re-dated. An empty window reports
  `NO_FILLS_IN_WINDOW` instead of being skipped.

**`TEST_PROVING_REPAIR` =** `research/beta48/test_canary_repair.py`,
11 tests, all passing, re-run green after every subsequent change.

- D2: a 78-day-dormant account has an EMPTY last-7-days window; the same
  calendar span applies to every account; LIFETIME ignores the anchor.
- D1: the verdict is suppressed below 30 clusters, passed through above
  30, and the floor equals `proof.py`'s own constant.
- Changed-nothing-else: every economic field survives gating
  byte-identical; the frozen horizons / ceilings / bands / buckets are
  unchanged; `merge_pnl.py` is byte-identical to HEAD **by `git diff`**;
  `completion_grid` never reads a verdict.

One of those tests was itself wrong first and its docstring keeps the
record: it grepped `merge_pnl` for the word INSUFFICIENT and failed — not
because the file had been edited, but because `merge_pnl` already uses
that word for its own two-lot floor. Replaced with a `git diff` check,
which is strictly stronger.

---

## Every previously reported RN1 result, classified

No old result is replaced here. Old and corrected values are shown side
by side and the old ones remain in the record.

| # | RN1 result | basis | status |
|---|---|---|---|
| 1 | `RN1_PAIR_EDGE = NOT_IDENTIFIED` | LIFETIME | **UNAFFECTED** |
| 2 | `RN1_PAIR_POINT_ESTIMATE = POSITIVE` | LIFETIME | **UNAFFECTED** |
| 3 | `RN1_PAIR_EDGE_STATISTICALLY_DEMONSTRATED = NO` | LIFETIME | **UNAFFECTED** |
| 4 | LIFETIME `edge_roi = +2.084%`, CI [−0.195%, +4.362%], 196,619 fills, 78,500 merges | LIFETIME | **VALID** — identical before and after the repair, and a test pins that LIFETIME ignores the anchor |
| 5 | `RN1_RECENT_EDGE_DECAY = OBSERVED_IN_POINT_ESTIMATES` | self-anchored regime series | **INVALIDATED as computed** — see below. The *label* survives on the corrected series; the numbers behind it do not |
| 6 | Regime series LAST_30D / LAST_14D / LAST_7D | self-anchored | **INVALIDATED / MUST_RERUN** — rerun in `35028887477` |
| 7 | Ceiling × horizon completion grid, LIFETIME | LIFETIME | **UNAFFECTED** |
| 8 | Ceiling × horizon completion grid, LAST_30D / LAST_7D | self-anchored | **MUST_RERUN** — rerun in `35028887477` |
| 9 | `RN1_FIRST_LEG_PRICE_ASSOCIATION = OBSERVED` | LIFETIME | **UNAFFECTED** |
| 10 | `MERGE_VS_HOLD_IS_REGIME_DEPENDENT = NOT_ESTABLISHED` | disjoint slices cut by `slice_by_days`, self-anchored | **conclusion UNAFFECTED, inputs MUST_RERUN** — the negative result rests on `cf_coverage` swinging 0.745 / 0.357 / 0.460 / 0.719, which is a property of settlement recording, not of the anchor. The slice numbers themselves are self-anchored and must not be quoted |
| 11 | `PAIR_BASIS_DISCRIMINATOR = NOT_SUPPORTED` | LIFETIME bands | **UNAFFECTED** |
| 12 | MERGE vs SETTLED decomposition | LIFETIME | **UNAFFECTED** |

### Item 5/6 in full — old vs corrected, both retained

Measured at the repair commit's as-of:

| window | OLD (self-anchored) | CORRECTED (as-of) |
|---|---|---|
| LIFETIME | +2.084%, 196,619 fills | +2.084%, 196,619 — **UNCHANGED** |
| LAST_30D | +2.150%, 189,174 | +1.97%, 184,036 |
| LAST_14D | +1.205%, 129,616 | +0.65%, 98,957 |
| LAST_7D | +0.262%, 73,455 | +0.70%, 38,784 |

The headline is unchanged: every interval still contains zero, every
verdict is still NOT DEMONSTRATED, and the decay is still visible. The
intermediate points move materially — `LAST_7D` nearly halves in fills.

**A property of the repair that must be stated, because it constrains how
these can ever be used as evidence:** an as-of-anchored window is a
function of *when the run happens*. `LAST_7D` means "the seven days
before this run", so it legitimately differs between `35028887477`
(as-of 22:25Z) and any later run. Regime windows are therefore **not a
fixed quantity** and two runs' regime numbers are not a replication of
each other. Only LIFETIME is stable across runs. Any current-regime claim
must name its as-of.

---

## Scope control

`REFERENCE_ACCOUNT_COMPLETION`, `BETTOR_PASSIVE_FILL_PROBABILITY` and
`BETTOR_EXPECTED_PAIR_PNL` are three different quantities and only the
first is measured anywhere in this repository.

- `REFERENCE_ACCOUNT_COMPLETION` = **MEASURED.** What a reference account
  achieved with its own orders, its own queue position and its own size.
- `BETTOR_PASSIVE_FILL_PROBABILITY` = **NOT_IDENTIFIED.** No BETTOR-side
  forward evidence exists. BLOCK_4 stands: a 22,297-share displayed bid
  queue against 180 shares traded in 16 minutes, **zero touches**.
- `BETTOR_EXPECTED_PAIR_PNL` = **NOT_IDENTIFIED.** Depends on the above.

A reference account's completion rate is not a BETTOR fill probability
and must never be substituted for one.

---

mirror_live = false. Read only. No order, no capital, no credential, no
production write. Track A untouched. Phase X untouched.
