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

## 5 · Exactly what is still limited

1. **No prospective challenger decision has occurred in production yet.**
   The lane is deployed and armed; whether it has produced a position
   depends on a live RN1 seed *and* an eligible valuation for the same
   condition. Until both coincide the lane reports its state and writes
   nothing, and that refusal is stored.
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
