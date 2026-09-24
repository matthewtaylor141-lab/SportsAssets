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
external_valuations (ELIGIBLE only)
   └─ bettor_hold_value.ev_hold            EV_HOLD + provenance + freshness
        └─ bettor_mgmt_select.rank_with_hold   ranks HOLD *with* the rest
             └─ bettor_venue_position_model.translate   what the venue does
                  └─ bettor_mgmt_lifecycle.Managed.decide_challenger
                       └─ bettor_desk.Order / Portfolio   (unchanged)
                            └─ bettor_rn1x_store  →  rn1x_decisions
```

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

**Freshness** is now 120 s — `bettor_progress_feed.MAX_AGE_S`, the bound
this stack already applies to in-play observations — for in-play, break,
suspended **and unknown**; 1,800 s only on a declared `PRE_MATCH`;
`FINAL`/`ABANDONED` refused outright; and a caller may only tighten it.

**Three further bugs the real path exposed**, none reachable by a unit
test on the selector: the continuing record carried no `policy`, so every
management decision landed on a fabricated second position row;
`created_at_runtime` used one run-level floor, so a reloaded order was
stamped with the current cycle's clock and migration 104 correctly refused
its own fills; and order ids were double-prefixed on reload, minting a
second order row and breaking one-active-order **in the store**. Migration
110 holds every challenger decision taken under the broken connection,
rows and refusals preserved; the frozen benchmark never used this builder
and is untouched.

## 4 · One traceable example

Verified end to end against real Postgres (migrations 100→109), through
the real writer and the real store:

```
position  RN1X_SHADOW_CHALLENGER_HOLD_RANKED_V1
          :SHADOW_CHALLENGER_HOLD_RANKED_V1:900001
seed      100 contracts @ 0.57  (ASSIGNED at RN1's own fill price)

D0000  policy_version   BETTOR_MGMT_LIFECYCLE_CHALLENGER_V1
       governing_rule   SHADOW_CHALLENGER_HOLD_RANKED_V1
       hold_value_usd   13.00   basis EXTERNAL_LABELLED_PROBABILITY
       payout_identity  row pays on "Chicago Cubs"; probability event
                        "Chicago Cubs"; complement NOT applied
       freshness        100 s from the bookmaker's own observation
       alternatives     REDUCE 16.272 | DIRECT_EXIT 15.760 | HOLD 13.000
                        + TAKE_COMPLEMENT
       refused          HOLD_TO_SETTLEMENT (settlement prose),
                        POST_COMPLEMENT (P_FILL), MERGE (not applicable)
       SELECTED         REDUCE × 70      ← levels 1 and 2 beat holding at
                        0.70; level 3 at 0.60 does not, and that is where
                        the quantity stops
       venue            net_effect REDUCE, creates_second_leg FALSE,
                        merge NOT_APPLICABLE
       operating_state  ORDER_WORKING
       reconciles       TRUE

fill   10 @ 0.80 (partial, queue share 0.25 of an observed print)

D0001  re-decided on the REMAINDER; the working order no longer matches
       → CANCEL_THEN_REPLACE, operating_state WAIT_CANCEL_ACK
       one active order throughout

accounting  realized 2.22  residual 90  invariant OK
persisted   1 position, 2 decisions, 1 order, 1 fill
```

## 4a · Production evidence, separated by what it establishes

| item | result |
|---|---|
| Required image gate, `backend-image-check` run 25 | **PASS** on `5b19bc5` — the SHA that was tested |
| API-only release, `render-ops` run 1546 `deploy-api-commit` | **SUCCESS**, `5b19bc5` deployed to `sportsassets-api` |
| `command-verify` run 31 on the deployed build | **SUCCESS** — 18 of 18 checks; step 19 correctly skipped (not armed on this run) |
| ↳ `RN1X — the AUTHENTICATED read, against the persisted records` | **PASS** (a 401/403 would fail it) |
| ↳ `RN1X — the two arms, and what each one actually decided` | **PASS** — the deployed build serves the `arms` panel and the extended trace |
| ↳ `Service health under ordinary command load` | **PASS**, 2 min 48 s |
| Focused regression, every test file referencing a changed module (30 files) | **1055 passed, 6 failed** |
| ↳ those 6, on baseline `6aefc45` | **identical 6 failures** — pre-existing (`.github/workflows/calibration-evidence.yml` absent), **0 new** |
| Full suite on HEAD | 11443 passed, **458 failed**, 198 skipped, 3 xfailed, 24 min |
| ↳ full-suite baseline comparison | **INCOMPLETE** — still running; not reported as passing or failing |

**What the arms step's PASS does and does not establish.** It establishes
that the deployed build answers an authenticated `rn1x/overview` with an
`arms` panel and serves the extended trace. It does **not** establish
that a prospective challenger position exists in production: the step
treats "no challenger position yet" as an allowed branch, and the run
log itself is not retrievable from this container (the egress proxy
rejects `results-receiver.actions.githubusercontent.com`), so the
printed counts were not read back.

**On the 458 full-suite failures.** `pytest -q` truncated the identity
list to 24 lines, so I do not have all 458 identities and cannot yet
separate new from existing across the whole suite. What is established:
`test_workflow_size_guard` fails on **`render-ops.yml` at 511,578
bytes** — a file this change does not touch; the workflow this change
does edit, `command-verify.yml`, sits at 62,765 bytes with 449,235 of
headroom. Of the 24 printed identities, one file
(`test_prospective_flow_accounting.py`) references a changed module, and
it **passes in isolation on both HEAD and baseline** and passed in the
focused 30-file run — so that failure is order- or shared-database
dependent, not a defect from this change.

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

## 5 · Exactly what is still limited

1. **No prospective challenger decision has occurred in production yet.**
   The lane is deployed and armed; whether it produces a position depends
   on a live RN1 seed *and* an eligible valuation **for the same
   exposure** — matching the condition is no longer sufficient, by
   design. Until both coincide the lane reports its state and writes
   nothing, and that refusal is stored. Every challenger decision taken
   before this correction is **held** by migration 110.
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
5. **`HOLD_TO_SETTLEMENT` cannot be priced** — the venue's settlement
   prose is `CONFLICTING_VENUE_PROSE`. Overtime and void compatibility
   remain unresolved and are separate work.
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
