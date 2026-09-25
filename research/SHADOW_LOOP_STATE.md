# The autonomous shadow loop: what runs, and the one blocker that stops it

Measured 2026-09-25. Live API SHA `6be6f0c`, confirmed by readback.
Funded submission stays disabled; the accounting-uncertain account stays
paused; `claude/session-njaewf` untouched.

---

## 1 · The loop is built and running. Nothing qualifies.

Your four numbered items map onto code that already exists and executes on
a schedule. The gap is not the loop — it is that **no current candidate
clears the gate**, for one reason.

| Your item | State | Evidence |
|---|---|---|
| 1 · Scheduled EV evaluation on supported markets, recording probability, provenance, depth, fees, edge, size, decision | **RUNNING** | 464 candidates evaluated in 24 h, every one persisted to `external_valuations` with its refusals. Heartbeat `ext_pinnacle_last_cycle` at build `6be6f0c`, `state: LIVE`, control row `ext_pinnacle_shadow = true` |
| 2 · Shadow positions for qualifying decisions via the existing simulator | **WIRED, UNEXERCISED** | `cycle()` calls `inv.plan_entry` → `inv.persist_entry` on `ADMITTED`, writing the same four tables the manager reads. **0 admitted**, so 0 created |
| 3 · Scheduled manager revisits inventory, HOLD vs alternatives, exits through the order lifecycle | **RUNNING** | 435 positions, 758 orders, 58 fills, 431 outcomes. The acceptance position took decisions at 14:46:10 and 14:48:25 today — the scheduled loop, on the same position |
| 4 · Display inventory, cost basis, orders, fills, fees, realised, unrealised, total P&L | **TWO REAL GAPS, both fixed here** | see §3 |

**Autonomous entries created: 0.** `rn1x_positions.provenance` splits this
cleanly and there is not one
`AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW` row. The 1 acceptance
position is `ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY` and does not count.

---

## 2 · The exact blockers, by code and count

24-hour window, `EXT_PINNACLE_DEVIG_V1_SHADOW`:
**464 evaluated · 0 admissible · 464 refused · 333 priced** (all 464 carry
both a venue-native slug and a global condition id).

```
VOID_ABANDONMENT_RULE_NOT_ESTABLISHED         464   ← 100%, the binding one
NO_ACTION_HAS_POSITIVE_NET_EDGE               445
EXECUTION_ESTIMATE_NOT_IDENTIFIED             442
OVERTIME_RULE_NOT_ESTABLISHED                 233
RISK_GATE_BLOCKED                             231   ← calibration
SIZING_POLICY_NOT_APPLICABLE                  209
NO_OBSERVED_DEPTH_INSIDE_THE_BREAK_EVEN_LIMIT 138
INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED        131
NO_QUALIFIED_MODEL                            131
QUOTE_STALE                                   131
```

Candidates clearing probability **and** settlement scope: **0**.

### Missing calibration is NOT the only blocker, so the research-shadow mode is not what unblocks this

You asked for a labelled uncalibrated research-shadow mode *if* calibration
were the only blocker. It is not, and building one would not produce a
single entry.

`external_source_calibration` has **zero rows** — calibration is genuinely
NOT ESTABLISHED, and I have fabricated nothing. But it gates stage 7, and
`VOID_ABANDONMENT_RULE_NOT_ESTABLISHED` gates stage 4 on **100%** of
candidates. Clearing the risk gate on 231 rows would leave all 464 still
refused upstream. Beyond that, `NO_ACTION_HAS_POSITIVE_NET_EDGE` on 445 is
the honest economic answer and I am not touching it.

**If you want the mode anyway** — as a standing capability rather than as
this unblock — say so and I will build it behind its own control row, with
`MODEL_TRUST_DRIFT` still reading NOT_EVALUABLE, every position labelled
`UNCALIBRATED_RESEARCH_SHADOW`, and no path to `guarded_submit`. It is
maybe an hour. It just will not create an entry today.

### What `VOID_ABANDONMENT_RULE_NOT_ESTABLISHED` actually is

Traced by **running** `attest` on realistic MLB inputs, not by reading it:

1. With `phase` and `game_format` unset, `book_terms()` returns `{}` and
   every condition reads `BOOK_STATES_NO_RULE_FOR_THIS_CONDITION`. The
   captured Pinnacle terms are scoped to `REGULAR_SEASON` +
   `STANDARD_NINE_INNING` and withheld until both are established.
2. With both set, the book side appears and
   `POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED` **matches** — it leaves
   `unstated_conditions` and is absent from `mismatched_conditions`.
3. The verdict is **still UNKNOWN**, because `compare` returns
   `COMPATIBLE` only when *every* applicable condition is stated by
   *both* sides. `silence_is_not_agreement = True`, deliberately.

Seven conditions apply to a baseball money line. The only venue prose we
hold is the listing's `description` field, **measured at 380 characters**,
which states one. Six stay silent.

**This is structural.** A 380-character blurb will never state seven
conditions. It cannot be cleared by reading the listing again.

Two useful facts either side of it:

- **The scope inputs exist.** MLB StatsAPI serves `gameType=R` and
  `scheduledInnings=9` per game — exactly `PHASE_REGULAR` and `FMT_NINE`.
  Confirmed today across all 12 games. That half is not a blocker.
- **The book side is captured**, with citations, from
  `www.pinnacle.com/en/future/betting-rules` retrieved
  2026-09-24T20:30:22Z.

So the gap is one thing: **the venue does not publish per-condition
settlement terms anywhere a reader can reach.**

I asked. `capture_terms` now takes a list of candidates in one dispatch.
Eight candidate URLs on two venue hosts, 2026-09-25T14:42Z, from a runner
with ordinary egress:

```
docs.polymarket.us/settlement     404   (117 KB of doc-site shell in the body)
docs.polymarket.us/rules          404
docs.polymarket.us/market-rules   404
docs.polymarket.us/resolution     404
docs.polymarket.us/sports-rules   404
docs.polymarket.us/               200   406 words, 0 settlement keywords matched
polymarket.us/rules               200   236 KB HTML, 80 words: a sign-up offer
polymarket.us/terms               200   236 KB HTML, 80 words: the same offer
```

The documentation root mentions none of `void`, `abandon`, `graded` or
`innings`. The two 200s are client-rendered shells and the extractor's own
`LIKELY_CLIENT_RENDERED` branch refused to call them a capture. Note the
404 bodies are 117 KB — a byte count alone would have read as a hit.

For contrast, the same run retrieved Pinnacle's rules page in full: 629
lines including per-condition prose down to *"If a match starts and isn't
completed within 12 hours of kickoff, then all bets on uncompleted periods
will be voided"* and the 85-minute referee exception. **The book publishes
this; the venue does not.**

Nothing was written into the settlement tables — `BOOK_TERMS` still carries
only the Pinnacle capture. A retrieval is evidence; deciding which sentence
states which condition is a reading that belongs beside a quote and a
retrieval time. The attempts are recorded in
`bettor_venue_settlement.VENUE_TERMS_CAPTURE_ATTEMPTS`, the way
`CAPTURE_ATTEMPTS` records the Pinnacle side, so a blocked retrieval is a
durable fact rather than the memory of a failed command.

**This is an external dependency, not a code defect.** The routes forward,
in order of soundness:

1. **Ask the venue** for its published grading rules, or for a rendered /
   API-served terms document. One reachable per-condition document clears
   464 candidates at once. This is the highest-value single action
   available and it is yours to take, not mine.
2. **Narrow the applicable condition set** to those a money line can
   actually turn on, with the reasoning recorded — a deliberate change to
   `applicable_conditions`, reviewed, not a quiet loosening. It would
   weaken the gate and I have not done it.
3. Accept that supported-market autonomous entry stays blocked and keep
   collecting refusal evidence, which is what is happening now.

---

## 3 · Item 4: two real gaps in the display, both fixed

### The autonomous entry lane was not in the P&L at all

`_pnl_status` iterated exactly two experiment ids — historical and
prospective management. A position created by
`EXT_PINNACLE_DEVIG_V1_SHADOW` would have carried a cost basis, owed fees
and settled **without ever appearing in the displayed book**. Nothing had
exposed it because that lane has produced no position — which is precisely
when a silent omission is invisible. All three lanes are now read, and the
lane's id comes from `EXT.EXPERIMENT_ID` rather than a copied literal.

### Unrealised P&L was the string `NOT_IDENTIFIED`; it now has a basis

The stated reason — *"a midpoint is where nobody transacted"* — is right
and is kept. The conclusion was too strong. The manager **already**
computes a price we may use, every cycle, for exactly the held quantity:
`DIRECT_EXIT` prices selling the leg **into the observed bid**, capped at
that bid's own depth, net of the production fee schedule.

```
MARK_BASIS = EXECUTABLE_EXIT_NET_OF_FEES_ON_OBSERVED_DEPTH
```

That is transactable, and it is the number the exit decision is already
made on — so marking at anything else would make the dashboard and the
manager disagree about what a position is worth. Open inventory is still
reported at cost as well: a mark and a basis are different facts.

Four collapses it refuses, each of which would flatter the number:

- **An unmarked position is not worth zero.** It is excluded and named,
  passing the ranker's own blocker through (`NO_BID`,
  `NO_EXECUTABLE_DEPTH`, `FEE_SCHEDULE_NOT_ESTABLISHED`) rather than
  inventing a vocabulary the engine cannot be reconciled against.
- **A depth-capped mark covers only the slice the book would take.** The
  remainder is `unmarked_residual_qty`; its `retained_value_usd` comes
  from the HOLD model and carries
  `MODEL_DERIVED_HOLD_VALUE_NOT_A_TRANSACTABLE_MARK`, never added in.
- **A total is only a total when every open position is marked.**
  Otherwise `total_pnl_status: PARTIAL` with the count and the blockers.
- **A mark older than the management cadence is stale, not current** —
  reported as `R_STALE`, excluded, and distinguished from having no price
  at all, because one needs a cycle to run and the other needs a bid.

`by_provenance` is carried through, so an acceptance position is never
counted as evidence the engine entered anything.

**A correction: I built this on a guess and production disproved it.** I
expected `rn1x_decisions.alternatives` to be a JSON *array* of candidates.
Asked of the three newest rows, production answered `alt_type = object`,
`alt_len = 0`, `actions = NULL` — a *mapping*, and an empty one. Empty is
correct for those rows (their fixtures are finished, so no bid exists and
nothing was rankable), but a reader expecting a list would have called a
live exit absent. Both shapes are now accepted and an empty ranking is
named `THE_DECISION_RANKED_NOTHING` rather than reported as malformed.

32 tests, run against the real tables rather than a mock pool — a mock
would prove the Python and nothing about whether the SQL parses, which is
the half that broke twice today.

---

## 4 · The positive lifecycle, demonstrated in the controlled test

It already exists, and it passes: `test_the_entry_lane_reaches_inventory.py`
and `test_the_entry_lane_reaches_the_exit_path.py`, **23/23**, against a
real Postgres with migrations applied.

`test_an_entry_created_position_runs_the_whole_lifecycle` drives
`loop.cycle()` with a seeded calibration row and a stubbed provider
observation, and asserts `ENTRY_INVENTORY_WRITTEN == 1`, then that fills
equal seed quantity, fees are positive, and the basis matches the
accounting the writer returned. `test_a_later_cycle_on_a_held_exposure_adds_no_executions`
then advances the whole clock a full 900 s cycle with a *new* provider
observation and re-acquired fixture evidence, and asserts no new order, no
new fill, no quantity drift.

**This is a controlled harness and is labelled as one.** The calibration
row is seeded by the test; the provider is stubbed. It proves the lifecycle
is wired and duplicate-safe. It is not evidence of edge and not evidence
that a live candidate would clear the gate.

---

## 5 · Regression attribution, against a real baseline

The earlier comparison was invalid: I ran the *same modified tree* from two
directories and called it a baseline. It was not. This is the real one.

| | Pre-change `25cd967` | Post-change `730dcaa` |
|---|---|---|
| Tree | git worktree at that commit | working tree |
| Database | `rn1xbase` (own, migrated) | `rn1xtest` |
| Invocation | `backend/`, `pytest tests/ -q --tb=no` | identical |
| Result | **447 failed, 11,809 passed** | **446 failed, 11,842 passed** |

Diffing the failure identities:

- **441 failures are common to both.** Pre-existing, untouched by anything
  in this work, and not attributable to it.
- **7 fixed** — the four `TestTheEvidenceWorkflowIsTheReservation` cases and
  the two `TestTheMigrationSaysWhatTheCodeRelies_on` cases that the path
  anchoring resolved, plus `test_rn1x_model_loop`.
- **6 differ in the other direction**, all source-inspection tests on
  `ext_pinnacle_loop` and its neighbours:
  `test_payout_event_is_priced_once`,
  `test_payout_identity_is_not_read_off_the_intent`,
  `test_settlement_attestation` (×2),
  `test_standby_does_not_erase_the_writer`,
  `test_venue_native_identity`.

**All six pass in isolation** — 42/42 when their files are run together. So
they are not straightforward breakage from the edits. Whether they are
order- or state-dependent under full-suite conditions is being settled by a
repeat post-change run under identical conditions; if the same six reappear
they are deterministic and I will fix them rather than explain them.

Note the pre-change run passed 11,809 and the post-change run 11,842: +33,
consistent with the new tests added, and the failure count moved by 1.

## 6 · The path-family defect that made me misreport to you

Tests read repository files by relative path in **four** mutually
incompatible families:

```
"migrations/103_external_valuations.sql"          → relative to backend/
"backend/migrations/066_...sql"                   → relative to the root
".github/workflows/calibration-evidence.yml"     → relative to the root
"../research/beta48/.../replay_sample_rows.json"  → backend/, at IMPORT time
```

No single working directory satisfies all four, so **the suite's failure
count depended on where pytest was started.** Run from `backend/`, six
calibration tests failed on a file that exists. Run from the root, twelve
entry-lane tests failed on a migration that exists, and one module failed
to import at all.

**This is what produced the wrong report I gave you earlier** — eight
failures in the funded execution path, with a missing
`.github/workflows/calibration-evidence.yml` named among the causes. The
file was not missing. The count was wrong.

Fixed in `backend/tests/conftest.py` via `pytest_configure`, not a fixture:
the fourth family is read on the module's import line, during collection,
before any fixture of any scope has run. `BACKEND_ROOT` / `REPO_ROOT` and
`backend_path()` / `repo_path()` are exported for readers that need the
other anchor, and the three unanchored call sites now use them.

Collection from the repo root went from **1 error** to **12,434 tests
collected**, and the combined set now returns **126 passed, identical from
both directories**.
