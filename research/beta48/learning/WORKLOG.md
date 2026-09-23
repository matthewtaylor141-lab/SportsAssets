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
