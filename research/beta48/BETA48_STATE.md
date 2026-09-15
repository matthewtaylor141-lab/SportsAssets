# BETA48 — actual current state, reconstructed from the repository

Produced 2026-09-15T21:4xZ, before any beta strategy code was written, as
directive section 1 requires. Everything below was verified by reading
files or running the offline reconstruction in this directory. Anything
not verified is marked NOT_VERIFIED rather than assumed.

Nothing frozen was modified. This directory is new and isolated.

---

## 1. Repository

| | |
|---|---|
| HEAD | `8aa6fa60b5b47a66ced08352202573e3a50b88db` |
| BRANCH | `claude/session-njaewf` (also the default branch) |
| WORKING TREE | clean at the time of reconstruction |
| REPO VISIBILITY | public |

## 2. Actions — running and frozen

| Run | Workflow | State | Evidence |
|---|---|---|---|
| `34995913495` | run85-phase2-capture (Track A #10) | **in_progress** since 16:35:02Z, `timeout-minutes: 340` → hard stop ~22:15:02Z | not touched |
| `35023098015` | run85-phase2-capture (Track A #11) | **pending** since 21:00:56Z | not touched |
| `35006965533` | phasex-crossvenue (live/live) | **cancelled 21:00:58Z, jobs.total_count == 0 — never ran** | see §5 |
| `34971707012` | phasex-crossvenue (live/live) | cancelled 17:22:49Z by `timeout-minutes: 60` mid-capture | evidence sealed at `8f925ae` |
| `34972147750` | phasex-crossvenue (kalshi-only) | success 13:00Z, GATE D | evidence at `f8d095c` |

Track A and Track B-L share the concurrency group `run85-phase2-capture`
(`run85-phase2-capture.yml:52`, `phasex-crossvenue.yml:56-59`), both with
`cancel-in-progress: false`.

## 3. Data actually on disk

### 3.1 The four whale raw datasets

| Dataset | Status | Where |
|---|---|---|
| **RAW_RN1** | **PRESENT** | `research/snapshots/u2_events_v1.jsonl.gz` — 214,609 rows, 214,609 distinct `trade_id` (zero duplication), 17,752 distinct conditions, **BUY-only**, `ts` span 2026-08-06T04:36:32Z .. 2026-09-11T23:59:34Z |
| **RAW_FERRARI** | **ABSENT** | no fills on disk |
| **RAW_HRH** | **ABSENT** | no fills on disk |
| **RAW_SWISSTONY** | **ABSENT** | no fills on disk |

`u2_events_v1` carries, per RN1 fill: `condition_id`,
`condition_id_effective`, `outcome_index`, `side`, `size`, `price`,
`notional`, `ts`, `trade_id`, `market_slug`, `sport`, `outcome` — plus
BETTOR's contemporaneous ask-ladder probe (`best_ask`, `depth`,
`depth_levels`, `probe_at`, `reaction_s`, `book_ok`).

That is exactly the row shape
`backend/sportsassets/analytics/merge_pnl.py::step()` consumes
(`condition_id`, `outcome_index`, `size`, `price`, `side`), so the pair
reconstruction runs offline with no database.

`research/snapshots/settlement_v1.jsonl` — 17,752 conditions, of which
**9,545 resolve to a clean one-hot payout** and 8,207 are NOT_RESOLVED.

### 3.2 Why the other three are absent, and what it would take

The whale fill history lives in two places, **both unreachable from this
container**. Every host probed returned `000`:

```
data-api.polymarket.com        000
clob.polymarket.com            000
gamma-api.polymarket.com       000
gateway.polymarket.us          000
api.elections.kalshi.com       000
sportsassets-api.onrender.com  000
```

1. **Production Postgres** — `merge_pnl.whale_merge_pnl()` and
   `decompose.py` both read `trades × trade_marks × markets`
   (`merge_pnl.py:661-676`). No credentials here, and loading one is
   forbidden.
2. **Polymarket data-api** — `.github/workflows/whale-full-history.yml`
   pulls each wallet's full fill history on a GitHub Actions runner
   ("this runner has open internet") and ships aggregates back as
   gzip+base64 blob lines, with raw dumps in an Actions artifact.

**The extraction workflow does not currently cover Ferrari.** Its matrix
is `rn1`, `w2c33`, `homerunhazard`, `swisstony`, `kch123`
(`whale-full-history.yml:29-44`).

### 3.3 Whale identity map (verified)

| Name | Wallet | Source |
|---|---|---|
| RN1 | `0x2005d16a84ceefa912d4e380cd32e7ff827875ea` | `whale-full-history.yml:31` |
| swisstony | `0x204f72f35326db932158cba6adff0b9a1da95e14` | `whale-full-history.yml:40` |
| HomeRunHazard | `0x5268527977f700f9bf9b6d5cd843859e4e70135d` | `012_seed_copy_portfolio_whales.sql:11` |
| kch123 | `0x6a72f61820b26b1fe4d956e17b6dc2a1ea3033ee` | `012_seed_copy_portfolio_whales.sql:10` |
| **ferrariChampions2026** | **`0xfe787d2da716d60e8acff57fb87eb13cd4d10319`** | `021_seed_dossier_promotions.sql:11` |
| w2c33 (distinct account) | `0x2c335066fe58fe9237c3d3dc7b275c2a034a0563` | `019_pin_whale_0x2c33.sql:10` |

Ferrari is **not** `0x2c33`; `engine-diagnostic.yml:355` treats
`ferrarichampions2026` and the `0x2c33…` key as separate accounts.

### 3.4 PMUS / Kalshi / book-depth data

| | |
|---|---|
| PMUS two-sided book + depth | `research/evidence/capture/`, `research/evidence/trackbl/`, `run85_phase2*` tarballs |
| PMUS settled outcomes | 10,257 slugs (archive walk) |
| **Overlap of the two** | **ZERO** — the archaeology found a 0-row join: 30 slugs with a two-sided book vs 10,257 with an outcome, intersection 0 (`research/TRACK_P_DATA_ARCHAEOLOGY.md`, DATA_GATE = DATA-B) |
| Kalshi contract records | **NEVER PERSISTED** — the frozen driver wrote `phasex_kalshi_records.json` only on the X2 path, which no run reached |
| Kalshi reachability | VERIFIED from GitHub Actions (HTTP 404 on `/`), blocked here |

## 4. Reusable infrastructure (do not rebuild — directive §17)

| Component | File | Status |
|---|---|---|
| Merge/pair replay (matched vs directional) | `backend/sportsassets/analytics/merge_pnl.py::replay` | pure stdlib, offline-usable, **reused verbatim** |
| Selection-vs-timing decomposition | `backend/sportsassets/analytics/decompose.py` | pure, but needs DB price marks (`p_5m`, `p_10m`, `p_60m`, `p_pre`) |
| Cluster-robust ROI intervals | `backend/sportsassets/analytics/proof.py` | pure stdlib |
| PMUS discovery walk | `research/run85_trackbl_block.py::discover` | proven, 2.5 s spacing floor |
| Depth-aware VWAP, fee registry, equivalence gate | `research/run85_phasex_economics.py` | proven, unmodified |
| Read-only venue clients | `research/run85_phasex_kalshi.py` | GET-only, audited `AUDIT = PASS` |
| Read-only import-closure audit | `research/trackx_readonly_audit.py` | passes on both entry points |

## 5. Phase X status

`35006965533` was **cancelled at 21:00:58Z, two seconds after Track A #11
entered the shared concurrency group at 21:00:56Z, with zero jobs
created.** It never ran. This is GitHub's documented behaviour: with
`cancel-in-progress: false` only one run may be *pending* per group, and
a newly queued run cancels the previously pending one.

Consequence: **any Phase X run queued behind Track A dies at Track A's
next cron rather than waiting.** Track A's cron is `0 */6 * * *` and its
segments run up to 340 minutes, so the pending window is routinely
shorter than the wait. Re-dispatching unchanged reproduces the failure.

No Phase X diagnostic result exists. Every economic field is NOT_REACHED.

Separately, and independent of scheduling: the frozen equivalence design
**cannot** return `EQUIVALENCE_VERIFIED`, because `dimensions()`
hard-codes `None` on at least one venue for SPORT, PROPOSITION,
START_TIME, EXTRA_TIME and OTHER_MATERIAL_CONDITIONS, and
`compare_dimension()` scores an absent side UNRESOLVED. Pinned by
`research/test_run85_phasex_index.py::test_five_dimensions_can_never_match_so_verified_is_unreachable`.

Groundwork for the correction (authoritative field mapping, PMUS half)
is in this session's scratchpad and summarised in §7 below.

## 6. Track verdicts in force

| Track | Verdict | Meaning |
|---|---|---|
| Track P | `TRACK_P_GATE = P-C` | No validated native predictive edge. ROI +10.5% TRAIN → +0.5% VALIDATION → **−11.1% HOLDOUT**, 95% CI [−15.5%, −6.6%] on 1,170 independent markets. Negative at **zero** fees, so no fee correction revives it. Preserved permanently as a negative result; **must not be retuned**. |
| Track P archaeology | `DATA_GATE = DATA-B` | Every ingredient exists; the join is empty. Needs a forward capture with settlement follow-up. |
| Track B-L | `FINDING B-1` (locked) | A taker/taker complementary pair costs `1 + spread` — guaranteed loss before fees. |
| Track B-L BLOCK_4 | binding | 22,297-share displayed bid queue vs 180 shares traded in 16 min, **zero touches**. Displayed maker edge need not become executable edge. |
| Three registers | locked | predictive / executable-taker / maker-execution edge are never collapsed. |
| PMUS fees | VERIFIED | `Fee = Θ·C·p·(1−p)`, `Θ_taker = +0.06`, `Θ_maker = −0.0125` (rebate), banker's rounding per fill, effective 2026-07-01. |
| Kalshi fees | **ABSENT** | the only number in the repo is our own constant (`edge-engine/src/edge/venues/kalshi.py:304`), never read back from the venue. |

## 7. PMUS authoritative field availability (from the 20 sealed PX1 records)

VERIFIED 20/20: `event.title`, `event.tags[].league.sportId`,
`event.tags[].slug`, `event.tags[].league.name`,
`event.tags[].league.automaticResolution`, `market.gameStartTime`,
`event.startTime`, `event.endDate`, `market.endDate`,
`market.sportsMarketTypeV2` (incl. `line`), `marketSides[].team.name`,
**`marketSides[].long`** (authoritative economic orientation),
`market.outcomes[n]`, **`market.feeCoefficient`**,
`market.orderPriceMinTickSize`, `market.minimumTradeQty`,
`market.status`.

Partial: `event.tags[].league.resolution` 16/20,
`settlementPriceCalculationMethod` 15/20, `market.rulesDisclaimer` 1/20.

Prose-derived from `market.description` (present 20/20, 388–597 chars):
`OTHER_RESOLUTION_CONDITIONS` 20/20, postponement 19/20, cancellation
16/20, draw 14/20, **overtime 3/20**, **void 0/20**.

Genuinely absent: `event.participants` (0/20) and any sport-NAME string.

## 8. Mirror / production trading state

`mirror_live = false` since 15:14:10Z on 2026-09-09 (owner order 15:13Z),
mode `exits`. Not re-armed. No order has been placed by this session.

## 9. First measured result — RN1 pair reconstruction

Run offline by `research/beta48/rn1_pair_reconstruction.py`, reusing
`merge_pnl.replay` unmodified. Evidence in
`research/beta48/evidence/`.

Ingestion: 214,609 rows → **196,619 kept**; 17,822 dropped for a missing
`condition_id` (8.3%) and 168 for an unidentified leg. 126,242 entries,
78,500 merges, **0 sells** (he exits by buying the complement, exactly as
`merge_pnl`'s docstring describes).

| Window | fills | merges | entry $ | merge P&L $ | edge_roi | 95% CI (cluster-robust, clustered on his GAME) |
|---|---|---|---|---|---|---|
| Sample (37d) | 196,619 | 78,500 | 32,276,739 | +223,094 | **+2.084%** | **[−0.195%, +4.362%]** |
| Last 30d | 189,174 | 76,057 | 31,549,573 | +210,531 | +2.150% | [−0.169%, +4.469%] |
| Last 14d | 129,616 | 52,322 | 22,934,875 | +82,217 | +1.205% | [−1.494%, +3.903%] |
| Last 7d | 73,455 | 30,546 | 12,619,063 | +36,011 | **+0.262%** | [−3.420%, +3.944%] |

`LAST_60D` is identical to the sample because the sample is only 37 days
long; it is not an independent window and is not reported as one.

**Every window's 95% interval contains zero, and the point estimate
decays monotonically as the window narrows: +2.08% → +2.15% → +1.21% →
+0.26%. `merge_pnl`'s own `edge_verdict` for every window is the string
`NOT DEMONSTRATED`.**

The estimator also carries its own coverage caveat verbatim: *"$8,338,997
of cost sits in positions that are still open OR whose market has no
recorded payout. They are EXCLUDED, not assumed."*

Hold-vs-merge counterfactual on the graded subset (`edge_coverage` 0.74):

| Window | holding − merging |
|---|---|
| Sample | **+$31,797** (holding would have been better) |
| Last 30d | +$30,267 |
| Last 14d | **−$82,769** (merging was better) |
| Last 7d | −$80,382 |

### What this does and does not establish

**Does:** RN1's own matched-pair economics over 2026-08-06..09-11, on his
own raw fills, using the desk's own estimator.

**Does NOT:** (a) it is RN1's fill prices, not BETTOR's — his fills are
not achievable fills for us (directive §14); (b) he traded the Polymarket
global CLOB, whose fee schedule is **not** the verified PMUS schedule, so
this ROI is not transferable to PMUS economics without re-costing;
(c) `edge_coverage` is 0.742, so a quarter of deployed dollars are
ungraded; (d) 8.3% of his fills are dropped for lack of a condition id.

**Reconciliation against the directive's report-derived hypothesis for
RN1 ("strong positive matched-pair economics"):** the raw data gives a
positive point estimate that is **not statistically distinguishable from
zero in any window**, and is near zero in the current regime. The
hypothesis is NOT CONFIRMED on this sample. It is not refuted either —
the interval is wide. `RN1_PAIR_EDGE = NOT_IDENTIFIED (point +2.08%, CI
crosses zero)`.

## 10. Blockers, in priority order

1. **Three of four whale raw datasets are unreachable from this
   container.** Ferrari, HRH and swisstony cannot be reconstructed here.
   The only paths are the Actions extraction workflow (which omits
   Ferrari) or a database read (forbidden). Directive §5's four-account
   comparison is therefore **NOT_IDENTIFIED for 3 of 4 accounts**.
2. **No BETTOR-specific fill evidence exists for any passive strategy.**
   BLOCK_4 measured zero touches on a 22,297-share displayed queue.
   Micro-live gate condition 12 cannot pass today.
3. **Phase X cannot complete while queued behind Track A** (§5).
4. **No PMUS dataset has both a two-sided book and a settled outcome**
   (DATA_GATE = DATA-B), so a directional fair-value model cannot be
   trained and validated on PMUS data as things stand.
