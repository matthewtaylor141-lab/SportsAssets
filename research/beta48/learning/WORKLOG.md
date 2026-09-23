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
