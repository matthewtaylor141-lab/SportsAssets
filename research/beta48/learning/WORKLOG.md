# The learning engine — durable work log

This file is the work log the mandate requires. It is append-only in
practice: entries are added at the bottom, and an entry that turns out
to be wrong is corrected by a later entry that says so, never by
editing the original. A chat session is not required to read it.

Companion files in this directory:

| File | What it is |
|---|---|
| `AUDIT.md` | What exists, what runs, what is tested, what is missing — with executable paths |
| `WORKLOG.md` | This file: what was done, when, and what it measured |

---

## 2026-09-23 — Entry 1. The audit's decisive finding, before any implementation

**Method.** Read the repository directly: `workers/all.py` for what is
deployed, every `CREATE TABLE` in `backend/` for what is persisted, an
import scan for machine-learning dependencies, and the module headers
of the EV, risk, capital, shadow and dataset layers.

### What is there

A great deal, and most of it is directly reusable:

* **Cohort fill ingestion runs in production.** `workers/poller.py` →
  `ingestion/poller.py` (venue Data API) and
  `workers/chain_listener.py` → `ingestion/chain.py` (on-chain
  `OrderFilled`). Both write `trades`.
* **The `trades` schema already carries the two clocks the mandate
  requires.** `ts` is the fill's own time; `detected_at` is when our
  pipeline first saw it. Any model trained here can therefore be held
  to what was knowable when, rather than to what is knowable now. It
  also has `dedupe_key TEXT NOT NULL UNIQUE`, so deduplication is
  enforced by the database rather than by a code path that can be
  skipped.
* **An EV, risk and capital layer exists and is wired.**
  `bettor_ev_bridge.py` (784 lines), `bettor_decision_engine.py` (866),
  `bettor_risk_engine.py` (305), `bettor_capital_allocator.py` (264).
* **Prospective decision recording exists.**
  `workers/bettor_prospective.py` writes a decision before any outcome
  is known and refuses to re-decide an observation. It is deliberately
  **not** in `workers/all.py`.
* **Learning-dataset *interfaces* exist and are careful.**
  `bettor_exit_dataset.py`, `bettor_p_fill_dataset.py`,
  `bettor_exit_ml.py`. They fix the field list and the output shape
  before the first row, which is the right order.

### What is not there

**There is no trained model anywhere in this system, and no code that
could produce one.**

| Check | Result |
|---|---|
| `sklearn` / `torch` / `xgboost` / `lightgbm` / `statsmodels` imported under `backend/` | **none** |
| `numpy` available in the runtime at all | **absent** — not a dependency of `backend/pyproject.toml`, not installed |
| Any `CREATE TABLE` for a model, model version, training run or prediction | **none**, across 70+ tables |
| Production code that fits parameters to data | **none**. The only `.fit(` calls in the repository are two research scripts, `research/beta48/shadow/p_bettor_independent_v{2,3}.py` |
| `bettor_exit_ml.py` | its own header: *"BUILT NOW, TRAINED LATER"* |
| `bettor_p_fill.py` | its own header: P_FILL is `NOT_IDENTIFIED` |

`shadow_policy_versions` is a version table for **hand-written
policies**, not for trained models. That distinction is the whole point
of the mandate's instruction not to describe a hand-written rule as a
trained learning system, so it is recorded here in those terms.

### The consequence for implementation

No ML dependency is installed and adding one to the deployed image is a
production change outside the current authorization. So the learning
layer is being built in **pure standard-library Python**: logistic
regression with L2, isotonic calibration, gradient-boosted decision
stumps, and discrete-time hazard models. All four are a few hundred
lines each, deterministic, testable without a venue, and interpretable
by construction — which is also what the mandate asks for first
("useful, interpretable baselines first"). Adding a numerical library
later remains available and would be a separate, separately-justified
release.

### Constraint respected

The 2026-09-23 observation run continues untouched. Nothing in this
work reads the collector's control row, spends its allowance or is
pushed to `claude/session-njaewf`.

---

## 2026-09-23 12:10Z — Entry 2. The corpus, measured

`render-ops sql cohort-inventory`, run 35858684521. **6,106,954 fills,
22 accounts, 47,412 in the last 24 hours.** Full table in `AUDIT.md`.

The finding that changes what can be claimed: **median detection lag
is months for three of the four named case studies.** Ferrari 87 days,
swisstony 229 days, kch123 200 days, HomeRunHazard 31 days. RN1 is 431
seconds; nigiri99 265 s; 0x076daa87 370 s; 0x99a093burst 41 minutes.

Those rows were backfilled, not observed. They can teach behaviour and
support economic reconstruction. They cannot support any claim about
acting in time, and this log records that before a model exists rather
than after somebody quotes one.

Second structural fact: six accounts including Ferrari, swisstony and
HomeRunHazard have **zero SELL fills**. Exits are complement buys, so
exit reconstruction reads the complement, never a sell.

`shadow_positions` and `shadow_position_events`: **0 rows**. The
position-lifecycle code has never run on real data.

## 2026-09-23 12:15Z — Entry 3. The kernel, and a review pin doing its job

`backend/sportsassets/learn/kernel.py` and `metrics.py` are in, with 31
tests that check closed forms rather than "the loss went down": the
intercept equals the log-odds on an uninformative feature; a known beta
is recovered from labels simulated by the model itself; separable data
stays finite; PAV pools a hand-worked inversion exactly; a censored
hazard subject contributes ONE person-period, not four.

**A review pin caught a real weakening I introduced.** To get unaligned
psql output I had expanded `${PFMT:-}` unquoted into the psql command
line, and `test_l1_review_pins::test_review_the_preset_as_the_runner_executes_it`
failed — that pin exists exactly to stop that line drifting, and an
unquoted expansion in a command position is the drift it watches for.
Fixed by writing the flags as literals in two branches so the pinned
invocation is present unchanged. The pin was right and the first
version was wrong.

### Baseline comparison, completed and corrected

The earlier baseline run was **invalid** and is withdrawn: it reported
10,113 outcomes against a commit that collects 11,266, so it was 1,153
tests short. Re-run in a clean `git worktree` at the branch point.

| | Collected | Failed | Passed |
|---|---:|---:|---:|
| `c8b9704` (branch point) | 11,266 | 445 | 10,675 |
| `c600a24` (head) | 11,401 | 446 | 10,797 |

On the **11,262 shared tests**: 445 fail at base, 446 at head.
**One regression, zero fixes** — the review pin above, now fixed.
On the **139 added tests**: zero failures.

The 445 pre-existing failures are not from this work and are not being
chased under this mandate. Two of them
(`test_l2_review_pins::test_review_the_error_line_names_every_case_label`,
`test_render_ops_hourly::test_the_help_line_is_the_case_labels_with_hourly_last`)
assert that the render-ops help line names every case label; they
failed at the branch point and still do, and the read-only cases added
here are additional labels they would also want named. Recorded so it
is not a surprise later.

## 2026-09-23 12:22Z — Entry 4. A correction: the latency ceiling I reported was wrong

`render-ops sql ingest-latency`, run 35859981810. Restricted to fills
whose OWN time is inside the last 7 days, so no historical backfill can
enter the sample.

**Forward ingestion is sub-second on the chain path, not months.**

| Account | Source | Fills (7d) | Median lag | p95 | Max |
|---|---|---:|---:|---:|---:|
| RN1 | chain | 80,305 | **−0.6 s** | 0.3 s | 88.4 s |
| ferrariChampions2026 | chain | 59,173 | **−0.6 s** | 0.3 s | 117.8 s |
| 0x99a093burst | chain | 40,748 | −0.6 s | 0.2 s | 118.6 s |
| nigiri99 | chain | 33,842 | −0.6 s | 0.4 s | 118.6 s |
| HomeRunHazard | chain | 26,493 | −0.6 s | 0.3 s | 118.5 s |
| RN1 | poll | 13,741 | 145.7 s | 299.0 s | 4,163 s |
| RN1 | s1 | 953 | 4.4 s | 5.5 s | 36.0 s |

**Entry 2 was wrong about the ceiling and is corrected here.** Ferrari's
lifetime median of 87 days was entirely an artifact of a historical
import. Its forward lag is **−0.6 seconds**. Nothing about "three of
the four case studies cannot support a claim about acting in time"
survives this read: on the chain path they all can. What the lifetime
figure actually measured was WHEN THE HISTORY WAS IMPORTED, which is a
fact about our backfill and not about our latency.

**THREE SOURCES, NOT TWO.** `chain` (sub-second), `poll` (~150–300 s
median) and `s1` (~4.4 s). The `trades` DDL in the repository declares
`CHECK (source IN ('chain','poll'))`, so `s1` arrived after that
constraint was written — worth reconciling separately.

**THE NEGATIVE LAG IS REAL AND MATTERS.** `detected_at` is a median 0.6 s
BEFORE `ts` on the chain path, min −1.3 s. `ts` is the block timestamp
and `detected_at` is our receipt instant, so they are two different
clocks and ours is not strictly later. Any point-in-time feature cut
must therefore use `max(ts, detected_at)` rather than assuming
`detected_at >= ts`; assuming otherwise would let a feature be built
from a fill we had not yet seen.

**A REAL INGESTION OUTAGE, now visible.** The largest detection gap in
seven days is **5,967 s (1 h 39 m) on 2026-09-19 17:16→18:56**, with
further gaps of 20 m, 15 m, 10 m and 5 m the same afternoon. Since
2026-09-21 the largest is 95 s. That afternoon is a hole in coverage
and must be excluded from any window a model is evaluated on, rather
than read as a quiet cohort.

**What this changes.** A model that requires a newly detected cohort
action is feasible on the chain path. The distinction the mandate asks
for still stands and is now separable:

* **reactive models** — conditioned on a newly detected cohort action.
  Feasible; budget ~1 s detection on chain, ~150–300 s on poll.
* **historical policies** — trained on cohort history, generating our
  decisions independently of any live cohort event. Unaffected by
  detection latency entirely.

## 2026-09-23 12:30Z — Entry 5. The cross-check found a real bug

`backend/tools/learn_crosscheck.py`, against numpy 2.4.6 / scikit-learn
1.9.1 installed with `pip --target` into a scratch directory. **No
production dependency changed.** 30 checks: 7 logistic cases against
`LogisticRegression` with matched objectives (C = 1/l2 on
pre-standardised features), 6 isotonic against `IsotonicRegression`,
the hazard's inner logistic against sklearn on its own person-period
design, log loss / Brier / AUC against sklearn, and 9 refusal and
stability cases.

**It found a genuine bug on the first run.** The hazard's survival
curve disagreed with a hand-computed Kaplan-Meier by 0.021. Cause: a
subject censored at exactly a bucket's END EDGE survived that whole
bucket, and `_expand` indexed off `bucket_of`, which puts `t == edge`
INSIDE that bucket — so those subjects were dropped from it. At-risk
340 where Kaplan-Meier counted 380; hazard 0.2647 where the truth was
0.2368.

**The bias ran the wrong way.** A denominator that is too small
over-states the hazard — over-predicting that the action happens, which
is the direction that makes a trading system act when it should wait.

Fixed: a censored subject contributes a survival for every bucket whose
END EDGE is at or before its censoring time. Agreement is now 1.4e-12.
Pinned by two tests in the repository suite so it cannot return in an
environment without sklearn. 33 kernel tests pass.

The second initial failure was the check being wrong about sklearn, not
the kernel: sklearn 1.9 warns and returns `nan` for a single-class AUC
rather than raising. The check now asserts the property that matters —
neither side yields a usable number — and notes that `nan` is the
weaker refusal, because it propagates silently through a comparison
while the kernel's `UNDEFINED` carries its reason.

## 2026-09-23 12:35Z — Entry 6. The command-centre regression, closed

**Tested commit: `9f7387c590596f7e7a342ce017bce7b2ae123bc9`** on
`claude/command-center`.

Focused rerun of the repaired pin, not a broad cycle:

```
tests/test_l1_review_pins.py::test_review_the_preset_as_the_runner_executes_it
    1 passed
tests/test_l1_review_pins.py                       32 passed
command centre + evidence store + learning kernel 168 passed
```

### Collected ids reconciled against terminal outcomes

| | Collected (unique ids) | Terminal outcomes | passed | failed | skipped | xfailed | Unexplained |
|---|---:|---:|---:|---:|---:|---:|---:|
| base `c8b9704` | 11,266 | 11,266 | 10,675 | 445 | 143 | 3 | **0** |
| head `c600a24` | 11,401 | 11,389 | 10,797 | 446 | 143 | 3 | **0** |

The head's −12 is **exactly** the twelve tests added in `b5634ff`
(`TestTheReleaseMigrationMatchesTheCode` ×5,
`TestTheApiReleaseCannotReachAWorker` ×7), which landed **after** the
head suite run began at `c600a24` and so were visible to the later
`--collect-only` but not to the run. Named individually in the commit;
nothing is unaccounted for.

**Dynamic ids.** Four collected ids differ between the two collections
— `test_bettor_live_loop::test_it_fails_closed_on_every_bad_budget`
and `test_l2_review_pins::test_review_the_preset_reads_a_naive_or_future_at`
embed `datetime.now()` in their parametrize ids. They pair 1:1 across
the two runs, so they shift which string appears and never the count.
They are the only reason the raw id sets differ by 4 in each direction.

**xfails and skips are carried, not netted.** 143 skipped and 3 xfailed
on both sides, unchanged by this work.

No further broad run. The 445 pre-existing base failures remain
out of scope and untouched.

## 2026-09-23 12:45Z — Entry 7. The first trained model, and the chain end to end

### Corrections handled inside the implementation, not beside it

**Forward ingestion is identified by LANE, not by date.**
`learn/dataset.ingestion_mode()` classifies `chain` and `s1` as live
listeners by construction and splits `poll` at `shadow_v2.py`'s own
`LATE_POLL_ROW_S = 900`, because the poller both tails live and repairs
history. Filtering on a recent `ts` excludes old fills, not recent
backfills, and that was the flaw.

**The negative difference is a clock-semantics finding, and the
repository already knew it.** `obs/clock.py` (run 83B): *"92.04% of the
chain lane's `detected_at - ts` differences are NEGATIVE — an elapsed
time cannot be negative, so those numbers were never elapsed times."*
My "sub-second forward latency" in Entry 4 was therefore **also wrong**,
in the other direction. It was never a latency at all.

Timestamp origins, read from the code:

| Field | Origin | Set when | Mutated later? |
|---|---|---|---|
| `ts` | block producer (chain) or venue (poll) | at ingest | **no** |
| `detected_at` | our process wall clock, `pipeline.py:331` | immediately before the INSERT | **no** — the `ON CONFLICT DO UPDATE SET` list touches only `condition_id`, `outcome`, `outcome_index`, `enriched_at` and `venue_seen_at` |
| `venue_seen_at` | the **venue's** feed, migration 034 | on a poll duplicate of an s1-won fill | first stamp wins |

Chain `ts` also has a declared fallback: when the RPC returns 200 with
no block timestamp, `chain.py` substitutes the local wall clock and
counts it. Those rows' `ts` is not a chain time.

**`available_at = max(ts, detected_at)` is used and labelled a
CONSERVATIVE CUTOFF.** It does not establish persistence, indexing, or
that any consumer was running, and `clock_note` says so in the dataset.
`venue_seen_at` is carried through so a later reader can do better.

**The 5,967 s interval is an UNEXPLAINED DETECTION GAP, not a confirmed
outage.** No source-health record, cursor or reconnect log has been
examined. It is excluded and the exclusion is recorded: 56 entry rows
fell inside it and 25 more had horizons overlapping it — all 81 dropped,
because a complementary fill could have happened in the hole and
scoring that as a negative would teach the model RN1 does not complete.

### The trained baseline

RN1, last 10 days, deterministic 1-in-20 condition sample: **4,860
fills, 443 conditions, 1,186 entry rows, 0 censored, base rate 0.552.**
Target: will RN1 be OBSERVED buying the complement within 1 hour.

EVAL block: **114 decided rows, 31 conditions.**

| Model | Log loss | AUC | Skill vs base | ECE |
|---|---:|---:|---:|---:|
| base rate | 0.6160 | 0.500 | 0.0% | 0.118 |
| **ridge** | **0.5172** | **0.725** | **+16.0%** | **0.084** |
| stumps | 0.5579 | 0.635 | +9.4% | 0.103 |
| ridge + isotonic | 0.6117 | 0.703 | +0.7% | 0.218 |

**Calibration made it worse and was NOT promoted.** Block base rates:
TRAIN 0.610, CALIB 0.384, EVAL 0.728. The target is strongly
non-stationary across ten days, so an isotonic curve fitted on the
middle block encodes that block's low rate and drags predictions down
where the rate has risen. `p_calibrated` is NULL on every stored row.

What it says, in words: RN1 completes **less** as entry price moves from
0.50 (−1.32 log-odds per unit) and **less** on the first fill in a
market (−0.61); **more** once several fills are in it (the stumps break
at 7.5 prior fills, +1.17).

### Persisted, and prospective

`bettor_learn_model` / `bettor_learn_prediction` created and verified
from the catalog (35 columns, 6 indexes). Model `rn1_complement_1h`
v1, status **CANDIDATE**, code_sha `8976c227`.

**Five predictions whose outcomes could not exist when they were
computed** — `decision_at + 3600` beyond the extract's read instant.
p 0.496–0.604 against a 0.610 base rate; maturing
**13:22:34Z–13:31:59Z**. All five `unmatured`. The model was **refitted
from the training extract** before scoring and the script asserts the
refitted base rate matches the report's to 1e-12, so a loss of
determinism fails loudly instead of scoring with different coefficients.

### The EV engine's answer: a documented refusal

`bettor_ev_bridge.evaluate()` on a live-shaped book returns:

```
bestAction        NO_TRADE
actionEvStatus    NOT_IDENTIFIED
P_FILL_STATUS     NOT_IDENTIFIED
P_FILL_SOURCE     BETTOR_NATIVE_ADMITTED_FILLS_REQUIRED
FV_BETTOR_INDEPENDENT  NOT_IDENTIFIED
```

**This model does not change that, and must not.** It predicts RN1's
next observed action. The EV engine is blocked on P_FILL — the
probability that **our** resting order fills — which requires our own
admitted fills and cannot be supplied by any amount of cohort
behaviour. The refusal is the correct output and the correct
integration result.

**The chain, end to end, as it actually stands:**

    cohort record (4,860 real fills)            DONE
    reconstructed state (1,186 entry rows)      DONE
    model prediction (ridge, +16.0% skill)      DONE
    persisted, versioned, unmatured (5 rows)    DONE
    our EV decision                             REFUSED, and named
    risk check                                  not reached
    auditable proposal                          not reached
    later outcome                               matures 13:22Z
    evaluation                                  pending maturity

## 2026-09-23 12:55Z — Entry 8. The five were not entry-time forecasts

### Timing, verified

| | |
|---|---|
| feature / label data cutoff | **12:43:28.070Z** (extract `read_at`) |
| scoring | 12:44:16.819Z |
| durable write | **12:45:15Z** |
| entries | 12:22:34Z – 12:31:59Z |
| **entry → durable write** | **796 – 1,361 s (22% – 38% of the horizon)** |

**They cannot be read as entry-time forecasts of the following hour,
and I presented them that way.**

What is true: the features **are** entry-time — `D.build` uses only
fills whose `available_at` is strictly before the entry — and no
qualifying complement had been seen through the 12:43:28Z cutoff, which
is how those rows were selected. What is **not** true: the write is
107 s after that cutoff, so there is a blind window in which a
complement could have landed unseen. Both facts are stored on the rows.

### The target, redefined and retrained

v2 predicts completion in the **remainder** of the hour **given no
completion so far** — the question a mid-horizon prediction answers.
Trained and evaluated on that same target, not transplanted:

| | unconditional | conditional (1,117 s) |
|---|---:|---:|
| entry rows | 1,186 | 749 |
| left risk set early | 0 | **437** |
| train base rate | 0.610 | **0.308** |
| EVAL decided | 114 | **65** |
| EVAL conditions | 31 | **23** |
| ridge log loss | 0.5172 | 0.7402 |
| ridge AUC | 0.725 | 0.706 |
| **ridge skill** | +16.0% | **+6.5%** |
| ridge ECE | 0.084 | 0.213 |

**+6.5% on 65 decided rows across 23 conditions** is the honest figure.
+16.0% was for a target those predictions do not answer.

### The artifact

sha256 `81b249a86f52423380ae4df8...`, 1,376 bytes, 14 coefficients with
centre, scale and feature schema. **Loading it reproduces every EVAL
prediction to 1e-15.** Scoring now loads this artifact; it does not
refit. My previous determinism assertion checked only the base rate,
which does not establish identical coefficients.

### Claim discipline

* **Ridge is FROZEN.** It was chosen after comparing EVAL results, so
  that split served **model selection** and its numbers are not an
  unbiased estimate of future skill. No further variant is compared
  on it.
* Prior inspection: every row at or before the CALIB boundary was
  inspected while this pipeline was built.
* **These coefficients describe an association in observed fills. They
  are not RN1's reason for acting**, and no causal reading is offered.
* v1's five rows are **relabelled `INVALID_AS_ENTRY_TIME` and
  preserved**, never deleted. v1's model row is
  `SUPERSEDED_TARGET_MISMATCH`.

### Execution history — the other half, and it exists

`exec-history`, read 12:55:47Z:

| table | rows | span | states |
|---|---:|---|---|
| `mirror_orders` | **11,183** | 2026-09-06 → 09-10 | filled **3,876**, cancelled 5,417, expired 1,571, rejected 270, lost 48 |
| `live_orders` | 6,939 | — | settled 5,271, merged 581, cashed_out 457, cancelled 382, error 196, filled 52 |
| `engine_fills` | 349,411 | — | — |

**`mirror_orders` is 11,183 of OUR OWN orders with terminal states** —
a 34.7% raw fill rate, with limit prices and timestamps. That is the
raw material for an execution baseline, and it is a different evidence
class from the four forbidden P_FILL substitutes `bettor_p_fill.py`
names: these are our orders, not a whale's completion, a touch, a price
move or displayed depth.

What it does **not** obviously carry, and what a bounded funded pilot
would have to measure: **queue position at placement**, **book depth at
our price**, and **whether a cancellation was ours or the venue's**.
Those three are the specific missing measurements.

`P_FILL_NOT_IDENTIFIED` stays intact until that is settled.

### What runs automatically vs what I ran by hand

**Nothing in this learning loop runs automatically yet.** Every step so
far — extract, train, score, seed — was a manual dispatch or a local
script. The scheduled scoring worker and outcome joiner are not built.
That distinction is recorded here rather than implied by the existence
of tables.

Outcome resolution is scheduled for 13:35Z, after the last prediction
matures at 13:31:59Z plus a margin for delayed poll arrivals.

---

## 9. Ferrari components 1 and 2 — a null result, and a kernel defect

2026-09-23T13:08Z → 13:2xZ.

**Built.** `learn/ferrari.py` (`BETTOR_LEARN_FERRARI_V1`) and
`tools/learn_train_ferrari.py`, on the extract case
`train-extract-ferrari` (run 35864978620). Reuses the kernel, metrics,
split, clock and coverage rules unchanged.

**Sampled by CONDITION, not by fill** — 1 in 20 markets, then every
fill of each. Sampling fills would cut pairs in half and the
reconstruction would measure the sampler. 19,890 fills, 1,006
conditions, zero SELLs.

**Missing prior inventory: asked and answered.** The query counted
Ferrari's pre-window fills per sampled condition. It returned **zero
conditions** — and that is corroborated, not assumed: Ferrari's
conditions live a **median 0.02 days**, p95 0.2, max 1.69. A market
that lives under two days cannot carry inventory from sixty days back.

**Component 1 census.** 1,006 new positions and 3,719 adds to a leg
that is not yet paired: **Ferrari builds a position over ~3.7 further
fills before anything pairs.** No entry classifier was fitted, on
purpose — NEW-vs-ADD is determined by a feature in the vector, and
ENTER-vs-SKIP needs the markets Ferrari passed over, which fills do not
hold. Named missing fact: a point-in-time record of live markets.

**Component 2, two targets.** `complete` 46.7%, `clears` 27.4%,
**cleared given completed 58.7%** — so **41.3% of hour-horizon
completions lock a loss**, against 37.0% at condition level in §6.
Different measures, different windows; neither confirms the other.
Median pair price 0.9800, p95 1.1700, mean gross edge +3.09¢, mean
paired share of the entry 0.69. Gross throughout: `trades` has no fees.

**THE RESULT IS NULL AND IT IS RECORDED AS NULL.** Ridge AUC 0.598
(complete) / 0.584 (clears) looked like signal. The EVAL rows sit in
**169 markets**, not 837 independent draws. A delete-one-market
jackknife — `metrics.clustered_jackknife`, deterministic, no RNG, so
artifacts still reproduce — puts **every AUC interval across 0.5 and
every skill interval across 0**. No demonstrated skill, either target.
Largest market is 4.4% of rows, so the interval's own validity
condition holds. Not retuned.

Why: the features are Ferrari's own fills and the clock — no book, no
opposing flow, no price path. Strongest weight in both models is
`entry_price_dist_from_half`, which is a fact about market structure,
not about Ferrari.

**A defect in the shared kernel, found by this run.** `Isotonic`
returned exactly 0.0 on a pooled block of zeros. **12 of 837 held-out
rows got p = 0 and six of them completed**, contributing 0.198 of a
0.857 log loss on their own; excluding them the calibrated model was
0.669 against a 0.689 base rate. Fixed with a shared affine shrink at
fit time. The per-block Laplace correction — the obvious fix — breaks
monotonicity, and there is a test that asserts it would have inverted
that exact pair. Frozen artifacts unmoved (`from_dict` is verbatim, and
RN1 v2 has no calibrator). Cross-check 33/33 with three new checks.
Kernel tests 39 → 46.

**Still nothing in this loop runs automatically.**

---

## 10. The five v1 predictions, resolved

2026-09-23T13:55:35Z read; `render-ops sql rn1-resolve` run 35870471085.

| entry | p | UNCOND | hit | lane | t-to-event |
|---|---:|---:|---|---|---:|
| 12:22:34Z | 0.4960 | **0** | — | — | — |
| 12:24:04Z | 0.6040 | **0** | — | — | — |
| 12:25:25Z | 0.5166 | **1** | 13:13:08Z | chain | 2,863 s |
| 12:29:06Z | 0.5590 | **1** | 13:13:08Z | chain | 2,641 s |
| 12:31:59Z | 0.5702 | **1** | 13:26:19Z | chain | 3,260 s |

Base rate 0.6099 on all five. **3 of 5 completed.**

**The labels are FINAL, not provisional — measured, not assumed.** A
passed deadline is not "no complement": a lane that stopped delivering
is indistinguishable from a quiet book in the fills alone. So per-lane
recency was read at the same instant.

| lane | RN1 fills / age | all whales / age |
|---|---|---|
| chain | 489 / **1 s** | 4,820 / **0 s** |
| poll | 244 / 68 s | 2,134 / 20 s |
| s1 | 16 / 177 s | 36 / 35 s |

No lane stale. Every row had matured **1,416–1,981 s** before the read,
all past the ten-minute completeness margin.

**The 2026-09-19 gap cannot reach these rows.** Every horizon lies in
2026-09-23, so none overlaps 09-19. Exclusion and inclusion give
identical labels on all five, and the report carries
`changes_the_label: false` per row rather than asserting it in prose.

**Both targets scored.** All five were still in the risk set at
entry + 1117 s — the three hits landed at 2,641–3,260 s — so the
conditional target agrees with the unconditional one *here*. That is a
fact about these five rows, not a general property.

`validity` stays **INVALID_AS_ENTRY_TIME** on all five. They were
written at 12:44:16Z, twelve to twenty-two minutes after their entries,
so as entry-time forecasts of the whole following hour they are
invalid. Scoring a target does not repair the claim a row was recorded
under.

### What five outcomes demonstrate

**That the join runs end to end. Nothing else.** For the record, `p`
was 0.4960 and 0.6040 on the two that did NOT complete and 0.5166–
0.5702 on the three that did — which orders no better than chance, and
at n = 5 could not have shown anything either way. No skill,
calibration or profitability claim is made or available.

### Two things this cost, worth keeping

1. **The smoke test earned its place.** It caught the scorer expecting
   ten columns after the statement had been shrunk to seven — before a
   single real row was parsed.
2. **`render-ops.yml` is against a hard 512,000-byte ceiling.** The
   first `rn1-resolve` dispatch returned `startup_failure` with no log
   and no annotation, which looks exactly like a quoting bug and is
   not one. See `research/beta48/acceptance/RENDER_OPS_SIZE_CEILING.md`;
   the first thing to check is `wc -c`.

## 11. The improvement loop — two cycles, nothing promoted, and two defects the checks could not see

### 11.1 Cycle 1 (gate V1) — five variants, five rejections

Each candidate was tied to a measured line of the loss attribution:
`C1_NARROW_BAND` → settlement, `C2_REQUIRE_CLEARANCE` and
`C3_PATIENT_EXIT` → exit-vs-basis, `C4_SMALLER_CLIP` → stranded
capital, `C5_SHORTER_REST` → turnover. None passed both partitions.

**The more useful result was about the gate itself.** V1 ranked on the
zero-marked lower terminal bound, which has three measured defects:

1. **No resolving power.** `lower = realized − unresolved_cost`; the
   bound spans ~$34,000 on $100,000 while the realized figures are
   ±$1,000. The instrument is 9–34× wider than its effect.
2. **A degenerate objective.** Its optimum is to hold no inventory,
   i.e. trade nothing. `MIN_DECIDED = 50` was meant to bind there and
   did not — every candidate decided 300–1,800 orders. Both of V1's
   TRAIN accepts (C1, C4) are dose reductions.
3. **It inverted the sign.** V1 scored the baseline *worse* on
   VALIDATION at queue_share 0.50 (−$4,435 vs −$3,315) while its
   realized P&L *rose* (+$974 vs +$41).

### 11.2 Gate V2 — declared in `2e23798`, before cycle 2 ran

Ranks on the **cost-marked** terminal (= `realized`, by the ledger
identity) with the bounds as a required disclosure. Then closes the
three holes that opens: G1 turnover floor (a dose reduction cannot win
by shrinking), G3 unresolved-cost ceiling (capital cannot be parked and
called profit), G4 margin must exceed the bound-width change else
`NOT_RESOLVED`, G7 `d(realized)/d(queue_share)` must agree in sign
across partitions.

**V1 was not reapplied to cycle 1.** Rescoring a finished cycle under a
gate written after seeing its results is the tuning this loop exists to
prevent.

### 11.3 Cycle 2 — five rejections again, with a sharper reason

The dominant rejection moved from "worse drawdown" (a noise-scale
comparison) to **`NOT_RESOLVED`**: C1's $381 against a $6,071
bound-width change, C4's $237 against $8,739, C2's $3 against $41.

**G7 fails on the BASELINE.** `d(realized)/d(queue_share)` is negative
on TRAIN and positive on VALIDATION for every policy tested including
the frozen active one. Applying the criterion only to challengers would
have hidden that. The instability is a property of the corpus, so it is
not fixable by another entry/exit/sizing knob — and the next justified
experiment therefore targets the uncertainty (resolve inventory, accrue
the live lane) rather than the P&L.

### 11.4 Two checks that could not fail, both found by reading numbers

**(a) A bare `setattr` on the policy.** A mistyped candidate knob would
have attached a dead attribute, left the policy UNCHANGED, and reported
"no difference from baseline" — a null result manufactured by a typo.
It now refuses with the list of real knobs.

**(b) The live lane booked every fill free for 37 minutes.**
`bettor_desk_loop.run()` took `fee_fn=None` and `Desk` turned it into a
silent zero-fee lambda. The tell was realized P&L reading *exactly*
$0.00 after 334 fills, which is impossible under a schedule whose maker
side is a rebate.

**And `invariant_ok` read `t` on all 109 snapshots, truthfully.** The
identity `cash + inventory_cost − realized == starting_cash` holds
equally well when no cost was ever charged — it is a CONSISTENCY check,
not a COMPLETENESS one, and cannot see an accounting input that was
never supplied. `fees_usd = 0` is equally what a fee-free book and a
costless window look like.

Fixed by making the schedule the default on `run()` (the same one the
replay books against), by giving every snapshot and ledger row a
`fee_basis` of `NO_FEE_SCHEDULE_GROSS` or `FEE_SCHEDULE_APPLIED`, and
by having the invariant publish its own `does_not_prove` list. Verified
in production at `15:24:19Z`: realized accruing $10.06 → $11.48 with
the identity still exact.

### 11.5 The live lane, verified

| | 14:41:49Z | 15:12:46Z | 15:24:31Z |
|---|---:|---:|---:|
| decisions | 375 | 2,176 | 2,278 |
| orders | 35 | 100 | 136 |
| ledger | 18 | 109 | — |
| cursor | 221,451,008 | 221,465,688 | 221,466,298 |

**Restart-safety demonstrated across the `503a42b` deploy**, not
asserted: every action's `first` timestamp is unchanged (NO_TRADE
14:35:43, ENTER 14:35:44, FILLED 14:35:56, HOLD 14:36:01), so no
history was replayed under the live label; the cursor is monotone;
there is exactly one `desk_state` row (single writer); the identity is
exact on every snapshot.

### 11.6 The publish route, and what it cost

The acceptance probe found the published bundle carried **neither the
`desk` nor the `learning` tab** — Netlify had not built since before
the desk work. Both Render services track `claude/session-njaewf` with
`autoDeploy=yes`, and `deploy-api-commit` explicitly cannot reach a
worker or Netlify; there is no Netlify hook or token in the repository.
So publishing required a commit on the collector's branch.

**The `[skip render]` mechanism was verified from the deploy history
before relying on it**, not assumed: `7f76fd9` carries the directive
and appears only with trigger `api`, never `new_commit`, while
`d630d3d` without it deployed as `new_commit`.

Pushed `3d2bc87` — `frontend/public/command/` plus one test file whose
fetch-guard assertion had gone stale, verified by path to contain no
backend runtime code, migration, workflow or service config.
**Afterwards the worker's deploy list was byte-identical to the
baseline**: newest row still `7f76fd9 · live · api · 10:26:55Z`, no
`new_commit` row. The collector was not touched.
