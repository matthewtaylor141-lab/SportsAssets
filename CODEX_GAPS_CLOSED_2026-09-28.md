# The six verified gaps: what is closed, what is not

Branch `claude/round2`, commit after `d9711c4`. Nothing deployed. Account
`acct_fc2d773a2afa4851` remains paused. The protected worker (`f5d1c05`) and the
default branch are untouched.

Codex was right on every point it raised, and correcting them turned up five
further defects of mine — two of them worse than the ones reported.

---

## 1 · The realism guard did not enforce its stated rule — **closed**

All three counterexamples returned `REAL_FIXTURE`. `classify` looked for
simulation markers and, finding none, returned REAL: the absence of a marker
*was* the evidence, which is the single error the module exists to prevent,
written into the module that prevents it.

REAL now requires an affirmative, recognized `sports_type`. Four verdicts:
REAL, SIMULATED, **CONFLICTING** (a recognized real classification *and* a
simulation marker — two publisher statements that disagree, refused under its
own name), and UNKNOWN with the reason it is unknown — absent, unrecognized, or
a `futures` outright, reported apart because the unrecognized case is how a new
simulated family arrives.

**And the allowlist itself was a guess the first time.** Written from memory it
invented twelve families the venue does not use (`mma_`, `boxing_`, `golf_`,
`motorsport_`, `cycling_`, `rugby_`, `volleyball_`, `handball_`, `snooker_`,
`badminton_`, `aussie_rules_`, `lacrosse_`) and omitted three it does — `ufc_`,
`darts_` and `futures`. On a fail-closed allowlist an omission is a refusal, so
that list would have refused every UFC and darts contract on the board. It is
now read from the venue's own 218 `sports_types` in 14 families (research-sql
run 260), and a test pins every family to its verdict.

A second defect surfaced the same way: after removing the fall-through I did not
add the affirmative REAL branch, so soccer, UFC and table tennis all returned
UNKNOWN. The real and simulated *controls* in the case list caught it — which is
the argument for keeping them beside the counterexamples.

The fixture identity checks were not weakened; the A9 event-namespace test now
asserts both halves rather than the one that still passed.

---

## 2 · Competition-key existence does not establish identity — **closed**

Mappings are confirmed against **fixtures**: one provider fixture whose *both
sides* are recognized in the same venue event title. Two named teams playing
each other in both sources is not a coincidence; a wrong mapping produces no
such match. A weak (one-word) match is counted and cannot confirm — "United"
and "City" appear in every English division.

I first required **two** matches, and that was the wrong measurement rather than
merely a strict one: the provider's competition routinely carries more fixtures
than the venue lists, so counting absolute matches measures venue coverage, not
identity, and a correctly mapped competition with one listed fixture would have
been refused forever.

**`engnl → soccer_england_league2` is wrong, as you said.** The venue's fixtures
are AFC Fylde vs Carlisle United, Barrow vs Scunthorpe, Boreham Wood vs
Kidderminster, Gateshead vs Altrincham, Hornchurch vs Aldershot: the fifth-tier
National League, not the fourth-tier EFL League Two. Applying the same check to
every entry also refuted **`irl1 → soccer_league_of_ireland`** — Athlone Town,
Cobh Ramblers, Kerry FC and Treaty United are the First Division, not the
Premier. Both are out with their evidence and cannot return through the board.

Also in this item:

- **The EPL claim is marked a snapshot**, not a standing fact about the venue.
  What makes the exclusion safe is that the soccer set is derived from the board
  each cycle, so a later appearance is picked up.
- **The fallback board expires.** It had no time limit, so a read that broke in
  October would have kept spending metered credits on September's board and
  reporting it as current coverage. Inside a week: `STALE_SNAPSHOT`. Past it:
  `EXPIRED_SNAPSHOT` with an empty board. My first epoch was `1759017600` —
  **2025**-09-28, a year early — so the snapshot was born expired; a test pins
  the constant to the date it claims.
- **The board SQL shares the classifier.** It hand-excluded two of sixteen
  markers while claiming to share the rule; the WHERE clause is now generated
  from `SIMULATED_MARKERS` and `SIMULATED_SPORTS_TYPE_PREFIXES`.
- **`sports_selection` is persisted** in the heartbeat with the board's evidence
  state, so production can say which competitions were requested and refused,
  and a set derived from an expired snapshot cannot read as current coverage.
- **The metered budget change is stated**: three fetches → four, ~60 → ~80
  credits a cycle, about **+1,900/day** — with the offset that one of the former
  three (`soccer_epl`) could never reach a contract, so *reachable* spend goes
  ~40 → ~80.

---

## 3 · The decision ordering — **closed on the scheduled path**

The order is now: reconcile → shared decision evidence → rank all eligible
actions → persist the selected decision → dispatch that action.

- `manage(defer_dispatch=True)` selects, records, and stops. Reconciliation, the
  economics repair and the settlement close are **not** deferred: they are reads
  and a settlement, not the action being ranked, and deferring them would leave
  the loss stop enforced on a stale number.
- `dispatch_selection` is the same `submit_exit` call **moved, not duplicated**,
  so the deferred path cannot drift — in particular cannot lose
  `inputs_expire_at`, without which `submit_exit` refuses to send on an
  unbounded assessment. It recomputes no price, quantity or proceeds figure.
- `pass_once` dispatches **all four** selectable actions. HOLD sends nothing,
  and that is one of the four rather than a gap.
- `funded_pair_inputs` is the real supplier; where a reading is unavailable it
  returns the *name* of the missing input and substitutes nothing.

**Five defects found by wiring it, four of them mine and one severe:**

1. **The exit arrived unscored and silently lost to HOLD.** I read
   `expected_net_usd` off the selection; `select_exit` carries it on the
   matching candidate inside `ranking`. Absent, the exit could not win — so the
   lane chose HOLD over an exit it had already decided was right, and an
   unscored exit losing to HOLD looks exactly like a considered decision to
   hold. That is the worst shape this class of defect can take.
2. **A better-scoring action nobody can take blocked the one that could.**
   `TAKE_COMPLEMENT` won at +$1.37, `pass_once` refused it as not an action this
   lane takes, and the dispatchable exit was blocked. Non-dispatchable
   candidates are now `not_rankable` with a named blocker — visible, unable to
   win — the same rule already applied to a limit breach.
3. `discover` crashed on a supplier with no held leg, taking the exit dispatch
   down with it: a lane with no hedge reader could not act on its own held
   position at all, which is strictly worse than what it replaced.
4. The same absence broke the fixture argument (`None.fixture_id`).
5. The group-id guard refused *before* the decision, so a position with no
   portfolio group could not be exited. A group is needed to acquire a second
   leg, not to sell what is already held.

**Evidence, through `ext_pinnacle_loop.cycle()` with the transport substituted
at `pmus._get_client`:** `test_the_scheduled_path_selects_executes_recovers_and_reports`
shows `manage` submitting nothing, the ranking dispatching, the order carrying
the selection's own price and quantity, and the fill recovered across a restart.

**Loss containment is kept and corrected.** You are right that "an exit at a
loss is one nobody would take" is false. Case B is a $0.41 exit on a $0.60
basis, reported as locking a loss, and it **still executes**; a pure-function
test asserts a −$0.85 exit beating a −$2.10 hold is selected. Positive absolute
P&L is not an execution prerequisite anywhere in this path.

**Still open in this item:** the indirect hedge is not yet a candidate on the
scheduled lane. `PAIR_INPUT_READINESS` names the two unwired readings — the
venue complementary-contract read and the region probability source. Until they
are wired the ranking compares HOLD against the exit only.

---

## 4 · M1 — **not closed, and it is not a socket away**

Confirmed against the deployed code, not described:

- `book_currency_evidence()` → `subscription: None`, `revalidation: None` —
  **no qualifying subscription evidence** in the production reader.
- status `M1_NOT_AVAILABLE_ON_THIS_FEED`.
- missing preconditions: **`P5_DOCUMENTED_TIMING` only**. P1 replacement
  authority, P2 liveness, P3 continuity, P4 identity and P6 resynchronisation
  are all available or ours and built.

**So connection engineering is complete, and that is the point: it means the
remaining requirement cannot be closed by more of it.** P5 is a property of the
*published protocol* — the venue documents no as-of instant, no latency bound
and no staleness contract; zero sentences matched. No amount of subscribing
supplies it.

**The two supported alternatives, from the module rather than from me:**

1. the venue documenting `transactTime`'s semantics, or any as-of guarantee —
   not ours to produce;
2. **a read of a demonstrably *moving* book that separates last-change from
   now.** This one *is* ours, and it is a measurement rather than a
   documentation change: it is the concrete next step for M1.

I have not changed the precondition, passed a fabricated subscription
dictionary, or substituted receipt age. Four tests pin exactly those shortcuts
closed, including that `our_processing_delay_is_not_currency` remains in the
currency module.

---

## 5 · Remaining integration — **partly done, no scope reset**

| requirement | state |
|---|---|
| reservation recovery in the scheduled caller | done — `pass_once`'s first step, unconditional |
| double-count repair preserved | done — 9 boundary tests pass on the merged tree |
| candidate→order binding | partial — the dispatcher sends the selection's own numbers and recomputes none; the acquisition binding tests stand |
| both legs' residual reporting | not advanced this batch |
| scheduled learning producing a versioned evaluation that changes a later decision | **not done** |

The learning demonstration is the largest single item still outstanding and I am
not claiming progress on it.

---

## 6 · Release — **not gated, so nothing is proposed for release**

No SHA is put forward. The migrated-PostgreSQL suites have been run on `r2db`
(131–135) with **1,179 passing** across the funded, reservation, rails,
exposure, group, lifecycle, realism, boundary, pinnacle, ranking, management,
ordering and decision selections, and **7 failures all present in the matched
baseline**. That is not the full gate: the whole-suite run on the final merged
SHA with zero capital-critical skips has not been executed on this tree, and
until it is there is nothing to release and no build to read back.

---

## What I am not claiming

That the trader operates. Item 3's ordering is repaired and proved through the
scheduled caller; the hedge is still not a candidate there, M1 is blocked on a
venue guarantee, and the learning loop is unbuilt. No funded activation is
requested and no order has been sent to a real venue.
