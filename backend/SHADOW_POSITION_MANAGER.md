# The shadow position manager

**Shadow only.** Funded trading remains disabled and the
accounting-uncertain account remains paused. Nothing here submits an
order to a venue.

---

## 1 · What is making decisions now

`SHADOW_CHALLENGER_HOLD_RANKED_V1`, running as a **separately versioned
challenger** beside the frozen `MANAGEMENT_PAIR_091_STOP_16_V1`
benchmark. The two manage the *same* assigned inventory under different
decisions and their results are never summed.

The machinery was almost entirely present and stopped **one number**
short. All three modules said so in their own words:

| module | its own refusal |
|---|---|
| `bettor_exit_engine.evaluate` | `EXECUTION_COSTS_IDENTIFIED_RANKING_NOT_IDENTIFIED`; "ranking requires EV_HOLD … FV_BETTOR_INDEPENDENT is NOT_IDENTIFIED" |
| `bettor_mgmt_select.select` | "HOLD is NOT comparable — it needs a settlement model that does not exist" |
| `bettor_mgmt_router.route` | `EV_VS_HOLD: NOT_IDENTIFIED — needs an independent fair value` |

Migration 108 made that number obtainable by fixing the payout identity
on the external probability. The connection is:

```
external_valuations (ELIGIBLE only)  ── the probability ROW, nothing derived
   └─ bettor_mgmt_lifecycle.Managed.decide_challenger
        │   reload → apply newly admitted fills → residual q, basis_per
        ├─ bettor_hold_value.ev_hold(qty=q, …, now=at)
        │      EV_HOLD on THAT inventory, freshness re-checked at THAT clock
        ├─ bettor_mgmt_select.rank_with_hold   ranks HOLD *with* the rest,
        │      deriving HOLD's total from the inventory being ranked
        ├─ bettor_venue_position_model.translate   what the venue does
        └─ bettor_desk.Order / Portfolio   (unchanged)
             └─ bettor_rn1x_store  →  rn1x_decisions
```

The nesting is the fix, not a diagram convention: the valuation happens
**inside** the decision, after the reload and the fills, so the quantity
it is computed on and the clock its freshness is measured against are
both the decision's own.

No new engine, portfolio or accounting system. `decide_challenger` writes
through the same `place()`, the same `Order` state machine and the same
`Portfolio` as the champion, so the one-active-order discipline, the
inventory cap, the cancel/fill race and the invariant cannot diverge
between the arms.

## 2 · Which actions it can execute

| action | status | quantity comes from |
|---|---|---|
| `HOLD` | **ranked** — priced from the external probability | the whole residual |
| `DIRECT_EXIT` | **ranked** | `min(residual, bid depth)` |
| `REDUCE` (sell *some*) | **ranked** | **the levels of the book that pay more than holding**, stopping at the first that does not |
| `TAKE_COMPLEMENT` (incl. loss-limiting completion) | **ranked** | `min(residual, complement ask depth)` |
| post / maintain / cancel / replace a resting order | **decided**, deterministically | matched against the working order's own intent |
| `HOLD_TO_SETTLEMENT` | refused: `SETTLEMENT_SEMANTICS_CONFLICTING_VENUE_PROSE` | — |
| `POST_COMPLEMENT` | **available, never ranked**: `P_FILL_NOT_IDENTIFIED` | — |
| `MERGE` / capital release | `NOT_APPLICABLE` on PMUS, `NOT_IDENTIFIED` on a two-token venue | — |

### The venue's actual position model

PMUS keeps **one signed `netPosition` per market slug**
(`docs/rn1-two-sided-design.md` §1; `live_executor.classify_exit`;
`pmus._norm_order` — "a BUY_SHORT reads ORDER_SIDE_SELL, short-truth
6/6"). So buying the other side **reduces** the position share-for-share
rather than creating a second leg. On that venue the manager therefore
records **no matched pair, no locked P&L from two legs, no merge**, and
the capital returns as the reduction's own consequence — not as a merge,
which is not claimed to exist. Every reduction is capped at the held
quantity: a request above it is capped, never reversed into the opposite
bet. The two-token model is kept intact for the chain venue, where
`bettor_merge`'s `NOT_IDENTIFIED` still governs.

## 3 · The selection rule, declared

* **Objective** — maximise economic value per contract over the actions
  that can be priced at all, net of the fees and depth each one faces.
* **Constraints** — order ≤ remaining inventory; no reversal; one active
  management order per residual leg; the venue's position model decides
  the inventory consequence; no ranking on a price without depth; no
  invented passive-fill probability.
* **Tie-breaking**, in order — HOLD; then cash now over cash at
  settlement; then `DIRECT_EXIT` over `TAKE_COMPLEMENT` (fewer venue
  assumptions); then the larger executable quantity.
* **Minimum improvement** — `0.005` per contract (half a tick),
  **declared, not fitted**. Below it the book is not churned.

**It is not an EV optimisation and must never be reported as one.** The
probability is an external bookmaker's de-vigged price with **no
established calibration interval on these markets**. This is a declared
rule consuming a labelled forecast. It may lose to holding and it may
lose to exiting.

### When the evidence is insufficient

Named fallback: `EXPOSURE_TRIGGER_RULE_V1`, the frozen benchmark's own
guard, reused. With `EV_HOLD` `NOT_IDENTIFIED`, ranking the remaining
candidates selects an exit **every time** — because it is the only action
carrying a number — which is "liquidating the book for want of a
settlement model". So WHETHER is handed back to the declared trigger,
which answers on observed inputs only, and the priced subset is ranked
only once it fires.

Three hold states, counted apart and never summed:

| state | meaning |
|---|---|
| `HOLD_BY_DECISION` | priced against every alternative and won |
| `HOLD_BY_FALLBACK_RULE` | `EV_HOLD` unknown; the trigger evaluated real inputs and did not fire |
| `HOLD_FOR_MISSING_INPUT` | **not a decision** — nothing was evaluated; the policy was blind |

## 3a · The production connection, corrected

Independent inspection of deployed `5b19bc5` found four defects in the
**connection** — upstream of a selector, venue model and accounting that
were all working correctly. All four are fixed and demonstrated through
the real builder → ranking → lifecycle → store path.

| # | defect in `5b19bc5` | corrected |
|---|---|---|
| 1 | exit prices were the opposite side's **acquisition cost**, transposed | `exit_ladder`: exit = 1 − acquisition(opposite), complement = acquisition(opposite) |
| 2 | `payout_event_held` read off the valuation row, so the identity check compared it against itself | `position_identity` resolves it from `market_tokens` on the seed's own outcome index |
| 3 | a position was decided **once, ever** | `manage_open_positions` runs every challenger cycle over already-open positions |
| 4 | 1,800 s admissible in play; hold value aged once per position | bound keyed to a **declared** event state, strict by default; re-aged at every decision |

On YES bid **.60** / ask **.63**:

```
                shipped                 correct
held LONG       bid .40  comp .63       exit .60   comp .40   qty from the BIDS
held SHORT      bid .63  comp .40       exit .37   comp .63   qty from the ASKS
```

Both prices come off the **same** ladder, which is not a coincidence: on
a one-signed-net venue selling our side and buying the other side are one
order, so their values must agree exactly. The shipped pair invented a
spread between `DIRECT_EXIT` and `TAKE_COMPLEMENT` and the ranking picked
winners on it.

### The fifth defect: HOLD valued on the seed, ranked against the residual

Independent inspection of deployed `12260cb` found the four repairs sound
and a **fifth** defect, in the **ordering**. `manage_open_positions`
computed the hold value from `pos["seed_qty"]`/`pos["seed_price"]`
*before* `manage_open_position` reloaded the portfolio and applied newly
admitted fills; `decide_challenger` then ranked the actual residual
against that seed-sized number. After a partial exit, HOLD carried the
value of contracts already sold.

The reported case, before fees — seed 100 @ .57, residual 80, p .70,
executable exit .72 on all 80:

```
                        HOLD        DIRECT_EXIT     selected
shipped 12260cb        13.00           12.00          HOLD      wrong
corrected              10.40           12.00          DIRECT_EXIT
```

The selection itself was wrong, not merely the reported number. The
earlier .85 example never exposed it because .85 beats both hold values.

Fixed in two places, both required:

- **the ordering** — the worker no longer builds a hold value at all
  (`_ev_at` is deleted, not corrected). `decide_challenger` receives the
  probability **row** and values holding *after* the reload and the newly
  admitted fills, against the residual `q` and basis it is about to rank,
  at that decision's own clock — so freshness is re-checked there too. The
  decision records `hold_valued_on: {qty, basis_per_contract, at, after:
  RELOAD_AND_NEWLY_ADMITTED_FILLS}`.
- **the structure** — `rank_with_hold` now *derives* HOLD's total from the
  inventory being ranked (`p × q − basis`) instead of reading
  `ev_hold_usd` off the record, and when a supplied record disagrees it
  reports `quantity_mismatch` naming both quantities. A record built on
  another quantity can no longer decide anything, wherever it came from.

Realized P&L stays out of every alternative: the 20 already sold are
realized and sunk, reported separately on `resulting_inventory`
(`realized_pnl_usd`, `invariant_ok`), and no candidate is sized above the
residual.

### Freshness: the odds source's own rule, not the progress feed's

The 120 s in `12260cb` was taken from `bettor_progress_feed.MAX_AGE_S`,
which bounds a **period or clock observation**. A probability is an
**odds quote**, and the applicable rule is the odds engine's own
`bettor_pinnacle_devig.MAX_QUOTE_AGE_S = 30 s` ("no order without a quote
fresher than `max_age_s`", adopted unchanged). These measure different
inputs; the progress feed's threshold establishes **nothing** about odds
freshness, and describing it as if it did was wrong.

So the bound is **30 s** in every admissible state — in-play, break,
suspended and unknown, and pre-match too — aged from the bookmaker's own
`observed_at`, never from our receipt. `FINAL`/`ABANDONED` are refused
outright, and a caller may only tighten.

The 1,800 s pre-match allowance survives only as a **separately versioned
experiment**, `PRE_MATCH_QUOTE_AGE_RELAXATION_V1_EXPERIMENTAL`, **off
unless named by id**, and applied only on a *declared* `PRE_MATCH`: named
during play it is refused and recorded as `freshness_relaxation_refused`.
It is a labelled experimental change to the established rule, not the
rule. `FRESHNESS_SOURCE` in `bettor_hold_value` carries the citation and
the `not_from` disclaimer in the code itself.

**Three further bugs the real path exposed**, none reachable by a unit
test on the selector: the continuing record carried no `policy`, so every
management decision landed on a fabricated second position row;
`created_at_runtime` used one run-level floor, so a reloaded order was
stamped with the current cycle's clock and migration 104 correctly refused
its own fills; and order ids were double-prefixed on reload, minting a
second order row and breaking one-active-order **in the store**.

**Containment, held not deleted.** Migration 110 holds every challenger
decision taken under `5b19bc5`. Migration 111 holds, from the `12260cb` /
`8272872` rows, exactly the two classes the fifth defect and the wrong
bound could have changed: a decision whose residual differs from its seed
(or records no residual), and a decision whose probability was older than
30 s. A decision taken while the residual still equalled the seed was
valued on the right quantity and stays eligible — containing it would be
over-reach, and a test asserts that partition row by row. The frozen
benchmark never called `bettor_hold_value`, never read the exit ladder and
never ran through `manage_open_positions`; it is untouched, and that too
is asserted.

## 4 · One traceable example

**The reported case itself**, verified end to end against real Postgres
(migrations 100→111) through the real worker → reload → fills →
valuation → ranking → store path. A quote observed 10 s before each
decision, inside the 30 s odds bound:

```
position  RN1X_SHADOW_CHALLENGER_HOLD_RANKED_V1
          :SHADOW_CHALLENGER_HOLD_RANKED_V1:993001
seed      100 contracts @ 0.57  (ASSIGNED at RN1's own fill price)
p         0.70 on "Chicago Cubs", the event this position pays on

cycle 1  t+30  book bid .90 × 20
D0000  hold_value_usd   13.00  on qty 100      ← residual IS the seed here
       freshness        age 10.0 s  bound 30.0 s
                        bound_source bettor_pinnacle_devig.MAX_QUOTE_AGE_S
       alternatives     DIRECT_EXIT 16.87 × 20 | REDUCE 16.87 × 20
                        | TAKE_COMPLEMENT 16.87 × 20 | HOLD 13.00 × 100
       SELECTED         DIRECT_EXIT × 20   ← .90 is available on 20 only
       operating_state  ORDER_WORKING       reconciles TRUE

print   80 @ 0.91 crosses the resting sell → fill 20 @ 0.91
        (real trades row, evidence_id trade:993500)

cycle 2  t+60  book bid .72 × 80          ← THE REPORTED CASE
D0001  hold_value_usd   10.40  on qty 80       ← NOT the seed-sized 13.00
       hold_input       qty_valued 80, basis_per_contract_valued 0.57,
                        ev_hold_basis DERIVED_FROM_THE_INVENTORY_BEING_RANKED,
                        quantity_mismatch None
       hold_valued_on   after RELOAD_AND_NEWLY_ADMITTED_FILLS
       alternatives     DIRECT_EXIT 10.88 × 80 | TAKE_COMPLEMENT 10.88 × 80
                        | HOLD 10.40 × 80
       SELECTED         DIRECT_EXIT × 80
                        ← under the shipped seed-sized HOLD of 13.00 this
                          same book selected HOLD. 10.40 is the value of
                          the 80 actually held; 12.00 gross / 10.88 net is
                          what exiting them is worth.
       residual         80      realized 6.69      reconciles TRUE

orders  O-...-001 SELL 20 @ .90 FILLED · O-...-002 SELL 80 @ .72 RESTING
        one active order throughout; 1 position row, 2 decisions, 1 fill

restart residual 80.0000  realized 6.6900  open [O-...-002]
        two independent rebuilds identical · invariant OK
        replayed 2 orders, 1 fill
```

Every candidate in cycle 2 is sized to the 80 actually held; the 20 sold
are realized (6.69) and reported on `resulting_inventory`, in no
candidate's value. Candidate values are **portfolio** values net of fees —
cycle 1's 16.87 is 20 sold at .90 *plus* the 80 kept valued at 0.70 —
which is why they are comparable to HOLD at all.

## 4a · Production evidence, separated by what it establishes

| item | result |
|---|---|
| Required image gate, `backend-image-check` run 29 | **PASS** on `33a205a` (the code fix) |
| Required image gate, `backend-image-check` run 30 | **PASS** on `79ac406` (the released SHA) |
| API-only release, `render-ops` run 1549 `deploy-api-commit` | **SUCCESS** — deploy `dep-daql2abtqb8s73b11ji0`; `sportsassets-api` **live on `79ac406`** at 16:35:59Z |
| ↳ the worker was not touched | its live deploy is still `f5d1c05` from 11:43:58Z, read before and after the release |
| Focused regression, 17 files referencing a changed module, with a DSN | **287 passed, 0 failed** |
| Wider keyword sweep (`bettor|rn1x|mgmt|ladder|exit|hold|position|challenger`) on HEAD | 3425 passed, **22 failed** |
| ↳ the same sweep on baseline `8272872`, in a separate worktree | 3421 passed, **22 failed** |
| ↳ failure identities, diffed | **IDENTICAL SETS — 0 new, 0 fixed.** The 4 extra passes are the 4 new regressions |
| Pre-existing, unrelated: `test_workflow_size_guard` | fails on **`render-ops.yml` at 511,578 bytes — 422 bytes of headroom**. `command-verify.yml` is 70,811 with 441,189 spare |
| Production state read at 16:35Z (`render-ops` 1550, `sql rn1x-tables`) | `rn1x_shadow = true`; heartbeat `ok` 16:34:29Z; lane C `REPLAYED`, cursor moving |
| ↳ the challenger's fallback-hold decisions | **5 at 16:11Z → 16 at 16:35Z.** The management phase IS re-deciding the same position in production — blind, with `EV_HOLD_NOT_IDENTIFIED` |
| `command-verify` run 34 on the deployed `79ac406`, `arm_external=on` | see the trace below |

**The run logs ARE readable after all, and that changes what is
established.** Earlier reports said the printed counts could not be read
back because the egress proxy rejects
`results-receiver.actions.githubusercontent.com`. That is true of a
direct download from this container, but the GitHub MCP server returns
job log content, so from run 33 onward the authenticated production
output is quoted here rather than inferred from a green conclusion. Two
claims in the earlier report were weaker than the evidence: a challenger
position DOES exist in production, and its decisions ARE being retaken
across cycles.

**What run 33's authenticated read actually printed** (16:11Z, on
`8272872`):

```
arm  MANAGEMENT_PAIR_091_STOP_16_V1    pos=345  dec=5639  selected=656
arm  MANAGEMENT_PAIR_091_STOP_16_V1    pos=4    dec=1067  selected=14
arm  SHADOW_CHALLENGER_HOLD_RANKED_V1  pos=1    dec=5     selected=5
                                       held_by_choice=5  BLIND=0
challenger trace  ...:221817729  HTTP 200
  D0000  HOLD x 45.0   HOLD_BY_FALLBACK_RULE   input_available false
         ev_basis EV_HOLD_NOT_IDENTIFIED   hold_value_usd null
         residual 45.0   inventory_cost 15.30   invariant_ok true
         venue_translation  VENUE_POSITION_MODEL_NOT_ESTABLISHED (venue null)
         eligibility INELIGIBLE_BROKEN_PRODUCTION_CONNECTION  ← migration 110
```

So in production, today: one challenger position, 45 contracts held at
15.30, every decision on it a **blind** hold by the declared fallback
rule, and every one of them already contained by migration 110. No
priced hold has occurred in production yet, because no admissible
Pinnacle valuation has matched that position's own exposure.

**On the wider sweep's 22 failures.** They are byte-for-byte the same 22
identities on HEAD and on baseline `8272872`, so this change introduces
none of them. They are workflow-content and venue-parameter tests
(`test_render_ops_*`, `test_pmus_post_only`, `test_e25/e28/e29/e5`),
unrelated to the position manager; the earlier report's unresolved
"458 full-suite failures" is superseded by this diff for every file that
touches a changed module, and remains unexamined for the rest of the
suite.

### Continuing management, demonstrated

One position, two cycles, the book moving between them, **no new RN1
entry** — the thing the shipped lane could not do at all:

```
cycle 1  t+30   bid .60 (exit proceeds)   HOLD × 100        residual 100
                                          hold .70 beats the book
   ...   t+45   an observed print at .86 fills 20 of the resting sell
cycle 2  t+60   bid .85 (exit proceeds)   DIRECT_EXIT × 40  residual  80
                                          prints applied 1, cancel-then-replace

ONE position row · ONE order row · 2 decisions at 2 real distinct
timestamps · D0000 then D0001 (appending) · both reconcile
freshness re-aged per decision: 30 s, then 10 s

restart recovery: two independent rebuilds from the ledger →
  residual 80.0000  realized 5.6300  one open order  invariant OK  identical
```

## 4b · The acceptance position: what it is, and what it is not

**Why it exists.** The only open challenger position is an ATP tennis
fixture (`aec-atp-fraron-peddia-2026-09-23`, held SHORT on "Pedro
Boscardin Dias", residual 45.0 at 0.34). Links 1 and 2 of the chain PASS
on it — the tokens establish the payout event and `premap.resolve`
returns the venue slug and the intent, agreeing with the tokens — and the
chain then stops at `3_PROVIDER_FIXTURE` with
`THE_HELD_SPORT_IS_NOT_IN_THE_PROVIDER_SET`. That is a coverage fact
about tennis, not a defect in the connection, and **repeated blind
decisions on it are not the exit policy operating.** Waiting for an RN1
fill to land on a covered fixture is waiting on a coincidence, so the
manager is demonstrated on a position whose inputs exist.

**How it is created.** `seed_acceptance_position` walks the SAME input
chain a management decision walks, before it writes anything, over the
covered markets that are open, unresolved, recently updated and have both
tokens present. A candidate that fails any link is refused BY NAME and
the next one is tried; if none survives, nothing is written and the
refusals are the answer. On success exactly one `rn1x_positions` row is
written — no order, no fill, no valuation row — at the **contemporaneous
executable acquisition price off the ladder the intent names**, with the
fee taken from `bettor_fee_schedule.LATEST.fill_fee` and the basis stated
as `qty × price + fee`.

**What it is not**, stamped on the row rather than asserted here:

| column | value | what it rules out |
|---|---|---|
| `policy` | `ACCEPTANCE_SHADOW_MANAGER_DEMO_V1` | neither the frozen benchmark's policy id nor the challenger's, so no arm total can absorb it |
| `provenance` | `ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY` | it is not an observed acquisition |
| `entry_kind` | `ACCEPTANCE_MODELLED_ENTRY` | it is not an executed order |
| `source_account` | `ACCEPTANCE_HARNESS_NOT_AN_OBSERVED_ACCOUNT` | it is not RN1-derived: no whale, no cohort |
| `source_trade_id` | a NEGATIVE sentinel, a blake2b digest of the venue slug under `BETTOR_ACCEPTANCE_SENTINEL_V1` | it can never be read as a `trades.id`, and it is the same number in every process |

Migration 113 adds the column with a CHECK that only declared provenance
values are storable, so a future writer cannot invent a third label
silently.

**The identity is stable, and a repair adopts rather than replaces.** The
sentinel was originally `-(abs(hash(("ACCEPTANCE", slug))) % 2e9)`, and
Python salts `hash` for `str` per interpreter: measured across four
`PYTHONHASHSEED` values that derivation returned `-1646678549`,
`-224103108`, `-539497139` and `-516966637` for one slug, so the
"idempotent" repeat call would have written a SECOND position on the same
market at the next restart while the route reported idempotence.
`acceptance_sentinel` is now a blake2b digest over a versioned namespace
and returns `-1968964254` for `aec-mlb-az-col-2026-09-24` at every one of
those seeds. Independently of the identifier, `seed_acceptance_position`
**looks up** an existing open acceptance position by
`(experiment_id, policy)` first; when one is found it writes nothing and
returns `adopted: true` with that row, so a row written under the old
unstable scheme is adopted and no inventory is added. The regression runs
the seeder in two real separate interpreters at two hash seeds and asserts
the same `position_id`, an unchanged row count, quantity and basis — and
that the salted hash really does differ, so the defect is reproduced
rather than assumed.

**Why it cannot reach a benchmark number.** `ARMS_SQL` groups by
`(experiment_id, policy)`, so the acceptance policy is its own line
beside the champion and the challenger and is never summed with either.
The realised-P&L and open-inventory aggregations (`PNL_SQL`,
`OPEN_POSITIONS_SQL`) are asked only of the HISTORICAL and PROSPECTIVE
experiment ids; the acceptance position is in the challenger experiment,
so it is outside both. This is a property of the existing queries, not a
filter added for it, and the arms lines are printed in the acceptance
step rather than described.

**Nothing about it is funded.** The route returns `submits_orders: false`
and `funded: false`, the execution flag is untouched, and the
accounting-uncertain account remains paused.

## 5 · Exactly what is still limited

1. **No PRICED challenger decision has occurred in production yet.**
   Corrected from the earlier report, which said no challenger decision
   had occurred at all: the authenticated read shows one challenger
   position (45 contracts, cost 15.30) and its decisions being retaken
   across cycles. Every one of them is **blind** —
   `EV_HOLD_NOT_IDENTIFIED`, `input_available false`, HOLD by the
   declared fallback rule — because no admissible Pinnacle valuation has
   matched that position's own exposure, and the venue is unresolved on
   that row (`VENUE_POSITION_MODEL_NOT_ESTABLISHED`). So the ranking has
   never run on live inputs in production. A blind hold is a decision and
   is recorded as one, but it is not evidence that the ranking works.
   Every challenger decision taken before these corrections is **held** —
   migration 110 for the four `5b19bc5` defects, migration 111 for the
   seed-sized HOLD and the wrong freshness bound.
2. **The venue book is not readable from every context.** When it is
   not, `EV_HOLD` may still be present — HOLD becomes the only priced
   action and every exit is refused `NO_BID`. `book_available` is carried
   separately so a hold-only lane is not mistaken for one that
   considered exits and declined them.
3. **No calibration interval exists** for the de-vigged Pinnacle
   probability on these markets. The hold value is a point estimate with
   its uncertainty *named*, not bounded. `external_valuations.outcome`
   is the column that will support the calibration, and it populates
   only after events resolve.
4. **The de-vig method is an open question** (power vs multiplicative);
   which calibrates better is not established.
5. **Every HOLD value in production is CONDITIONAL, and the condition is
   the bookmaker's abandonment rule that we do not hold.** This was
   previously written as "`HOLD_TO_SETTLEMENT` cannot be priced", which
   put the dependency under the wrong action name. The hold value is
   `p × qty − basis` and `p × qty` is realised **at settlement**, so the
   terminal rules govern the ordinary HOLD the ranking uses, not only the
   explicit `HOLD_TO_SETTLEMENT` action. `ev_hold` now stamps
   `terminal_rule` and `value_is_conditional` on the number it returns,
   `rank_with_hold` carries both onto the HOLD candidate and into
   `alternatives.ranked`, and link `4b` records
   `conditions = "THE ORDINARY HOLD VALUE TOO"` beside its
   `blocks_outright = HOLD_TO_SETTLEMENT`.
   * The venue's own prose **is now read** — `read_rules_text` fetches
     `description` / `assetPriceTerms` / `rules` /
     `resolutionSource` / `resolutionCriteria` from the listing, paced and
     cached for an hour, and `attest` matches it against declared
     per-family patterns with three outcomes: AGREES (established,
     evidence class `ATTESTED_FROM_VENUE_PUBLISHED_RULES_TEXT`),
     CONTRADICTS (`OVERTIME_RULE_CONFLICTS_WITH_BOOK_RULE` — louder than
     unknown), or silent (unchanged). The earlier
     `CONFLICTING_VENUE_PROSE` reading was a statement about text nobody
     had fetched.
   * `BOOK_VOID_RULE` ships **empty on purpose**. A value in it is a
     claim about a third party's published terms and may be added only
     with a citation, never from recollection. The measured consequence
     is that production reports `VOID_ABANDONMENT_BOOK_RULE_NOT_HELD`,
     `overall_established` is `false`, and **no decision can satisfy
     `SETTLEMENT_COMPATIBILITY_ESTABLISHED`** — so `command-verify`
     reports COMPLETE and CONDITIONAL as separate verdicts and a
     conditional row is never counted as a complete, verified
     HOLD-versus-exit comparison. Closing it is a **data-capture task**:
     capture the bookmaker's abandonment/void terms from its published
     rules with a citation. Until then the number is computed and
     labelled, not withdrawn — refusing to compute would assert the
     position is worthless, which is the assertion most likely to force
     an exit.
6. **`POST_COMPLEMENT` is never ranked.** There is no BETTOR-native
   resting evidence, so no `p_fill`. The frozen benchmark posts one by
   *declared rule*, which is a different basis from ranking it.
7. **Simultaneous coordinated orders are not supported**
   (`SUPPORTS_SIMULTANEOUS = False`). One active management order per
   residual leg, deliberately.
8. **Event progress is connected but not configured.**
   `connected_sports()` now reads the provider configuration at call
   time — before this change `PROGRESS_FEED_CONNECTED` was a literal
   empty dict, so configuring a provider would have changed nothing.
   It remains fail-closed: with no `PROGRESS_PROVIDER` and no
   credential on the service, the second-half loss exit stays
   `UNAVAILABLE`. Soccer, basketball and football become admitted the
   moment a credential is present; hockey does not, because no halfway
   rule is written for three periods.
9. **All fills are modelled** under
   `PRINT_THROUGH_WITH_QUEUE_SHARE_V1`, licensed by an observed print by
   someone else at a declared queue share. None of it is evidence that
   our order would have filled.
10. **Open inventory is reported at cost, never marked.** No
    contemporaneous book is retained for those instants.
11. **The 30 s odds bound is adopted, not derived.** It is the odds
    engine's own declared rule, and nothing here establishes that 30 s
    is the right bound for *pricing a hold* rather than for placing an
    entry — only that the applicable established rule is now the one
    being applied, and that the progress feed's 120 s is not evidence
    about odds at all. The pre-match relaxation to 1,800 s is a
    **separately versioned experiment**
    (`PRE_MATCH_QUOTE_AGE_RELAXATION_V1_EXPERIMENTAL`), off unless named
    by id, and it is not calibrated either; it is a labelled hypothesis
    that a pre-match moneyline moves slowly enough, with no measurement
    behind it yet.
12. **Whether a stale-bounded decision changed anything is not
    measured.** Migration 111 holds the rows the fifth defect *could*
    have changed, on the exact criterion that its HOLD was valued on a
    quantity other than the residual. It does not establish how many of
    those selections would in fact have differed; that would need each
    row's contemporaneous ladder, which is not retained.
