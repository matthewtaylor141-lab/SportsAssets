# Xavier: standing protective orders (strict fallback)

Module: `sportsassets/bettor_xavier_standing_orders.py` (writer, policy,
lifecycle) and `sportsassets/agents/xavier_standing_view.py` (read-only view
for the workspace and Audrey). Migration: `157_xavier_standing_protective_orders.sql`.

## What it is

A standing protective order is ONE resting hedge order, on ONE selected
hedge instrument, at a protective price, for a group whose primary holds
CONFIRMED inventory. It is a management policy
(`STANDING_PROTECTIVE_ORDER_POLICY_V1`, policy key
`XAVIER_STANDING_ORDER_POLICY` in `agent_policy_versions`), not Derek's entry
policy. Its code default is DISABLED; only a person's write
(`activate_version`) enables it, and the funded submission switches are
untouched by it.

It is not a second execution path, book or selector:

* it runs only inside the group authority -- `ext_pinnacle_loop._service_once`
  (execution lock) -> `bettor_funded_pair_cycle.pass_once` (Xavier's group
  advisory lock) -> `maintain` (before the group is decided) and `govern`
  (after the group's decision and Xavier's record persisted);
* a placement is a Xavier decision row (`chosen_action =
  STANDING_PROTECTIVE_ORDER`, its plan digest), its dispatch claim, the leg
  reservation and `bettor_funded_execution.submit_for_decision`, sent
  GOOD_TILL_DATE with a `goodTillTime`;
* fills land in the one book through `bettor_funded_book.ingest_fills`
  (idempotent on the venue's execution id);
* desirability versus HOLD is `agents.xavier_policy.apply` (the active
  policy's own rule, capital preservation included) on the approved payout-
  state distribution over the one payout-table builder.

## Venue capability and why only one live order per group

The installed SDK (polymarket-us 1.0.2) offers GTC/GTD/IOC/FOK limit orders,
post-only, order states including PENDING_CANCEL / PENDING_REPLACE, create /
retrieve / open orders / cancel / modify / cancel-all-by-slug, and a private
websocket with order snapshots and updates. It does NOT offer linked /
one-cancels-other orders across markets, a cross-market quantity cap, or
cancel-on-disconnect. `EXCHANGE_LINKED_EXCLUSIVITY = UNAVAILABLE_OR_UNVERIFIED`;
STRICT_FALLBACK is the only mode implemented, and
`second_live_hedge_permitted` refuses a second live hedge order whatever a
caller claims (a mocked guarantee is not evidence).

A database lock (or unique index) can stop us SENDING a second order. It
cannot stop two orders that were ALREADY SENT from both matching at the
venue: two resting hedges of 10 on different instruments against 10 held can
both fill, 20 hedge against 10 primary (reproduced by proof (c)). So at most
one live or potentially-live hedge order may exist per group -- accepted,
pending-new, partially filled with leaves, pending-cancel, pending-replace, a
lost acknowledgement, a claim with no recorded outcome. Every other candidate
is monitored and never submitted. Splitting quantity across instruments is
refused (`SPLITTING_HEDGE_QUANTITY_ACROSS_INSTRUMENTS_NEEDS_A_SEPARATE_POLICY_DECISION`).

**Trade-off (disclosed on every plan and in the workspace).** Switching
instruments is cancel -> confirmed terminal read (the venue's cumulative
quantity equal to the ledger's) -> recomputed capacity -> replacement. The
replacement loses its queue position, and between the cancel and the
terminal read the group has no resting protection (a cancel round trip plus a
reconciliation read: the next order event, at worst one servicing interval).

## Capacity and the invariant

Capacity is the CONFIRMED primary residual (from the fills ledger) less the
hedge held -- never the intended primary quantity. The invariant

    hedge held + hedge fill-capable <= confirmed primary

is checked every pass; if the primary shrinks below it, the live order is
cancelled first and the unresolved exposure is reported. Capacity
(`bettor_standing_capacity_reservations`, one RESERVED row per group) is
released only on a confirmed terminal state; the database refuses a release
while the hedge intent is still outstanding. A cancel request or
acknowledgement is not terminal.

## Floor classes (exactly one per evaluated hedge)

Per matched pair: primary cost + entry fees (actual, from the book) + hedge
cost + hedge fee (dated taker schedule, rounded up, no rebate) + other costs
+ buffer, against the payout table (`bettor_indirect_structures.payoff_table`)
over the claimed settlement states.

* `POSITIVE_FLOOR_ALL_ESTABLISHED_STATES` -- positive in every ordinary and
  extraordinary state, all established;
* `POSITIVE_FLOOR_CONDITIONAL_ON_ORDINARY_SETTLEMENT` -- positive in every
  ordinary state; an extraordinary state is unknown (never a refund, never
  50c: valued at 0) or does not pay more (a basis refund nets minus the fees);
* `CONTROLLED_LOSS_PAIR` -- ordinary states established, the cheapest does not
  cover the cost (e.g. a 60c hedge on a 50c primary);
* `FLOOR_NOT_ESTABLISHABLE` -- an ordinary state undetermined, a missing
  grading fact, an unpriced fee, a fractional quantity.

A classification records the fee-schedule identity and the settlement
identity (both legs' rules and prose hash); a change of either -- or of the
class -- invalidates it and cancels the order.

## Lifecycle

Derived from the book and appended to `bettor_standing_order_events`
(append-only): PLAN_RECORDED, CAPACITY_RESERVED, PLACEMENT_CLAIMED,
SENT_ACKNOWLEDGED_RESTING / SENT_FILLED_ON_ARRIVAL / SENT_OUTCOME_UNKNOWN /
REFUSED_BY_THE_VENUE, FILL_OBSERVED, INSTRUMENT_SELECTED (first fill),
COVERAGE_RECORDED (the payout table of the actual combined inventory),
CANCEL_REQUESTED, TERMINAL_CONFIRMED, CAPACITY_RELEASED, FLOOR_INVALIDATED,
DESIRABILITY_LOST, SWITCH_REQUESTED, RESIZE_REQUESTED,
INSTRUMENT_TRANSITION_EVALUATED / _REFUSED, REMAINDER_REPLACEMENT_REFUSED,
CAPACITY_EXCEEDED, PRIMARY_CLOSED, AUTHORIZATION_EXPIRED,
MARKET_SUSPENDED_OR_CLOSED, EXPIRY_PASSED, EXIT_WAITS_FOR_HEDGE_TERMINAL,
EXIT_REFUSED_HEDGE_WOULD_EXCEED_PRIMARY, VENUE_ORDER_EVENT,
VENUE_MARKET_EVENT, PRICE_TOUCH_IS_NOT_A_FILL.

An EXIT or REDUCE of a group whose standing hedge order is fill-capable is
held back: the order is cancelled first, and the exit is decided again on
what is held once the order is terminal and reconciled. A primary exit that
would leave more hedge than primary is refused, with the residual valued and
reported. Emergency and mandatory controls are not routed through this step.

**If the process dies**: a GOOD_TILL_DATE standing order stays live at the
venue until its `goodTillTime` (or a fill or a cancel), then the venue expires
it; a GOOD_TILL_CANCEL order would stay live indefinitely, so the policy never
sends one. On restart the state is rebuilt from the database and the venue's
order reads; nothing is resent, and an ambiguous send stays potentially live
until a venue read establishes otherwise.

## Events

`ext_pinnacle_loop.on_private_order_message` (the SDK's `order_update` /
`order_snapshot`) records the message, ingests any execution it carries into
the one book, then runs the same `_service_once`;
`on_market_message` (book / trade / market state) records it -- a price that
touches our limit is not a fill -- and runs the same pass (throttled per
slug). A message arriving while a pass holds the execution lock re-runs the
pass once when it finishes. The 60-second servicing task remains the
reconciliation backstop. `attach_private_feed` registers the handlers on an
SDK `PrivateWebSocket`; no live private websocket is connected in this
deployment (it needs the venue credential the deployment does not hold).

## Reading it

`GET /api/command/agents/xavier` (positions -> each group's
`standing_protection`; versions -> `standing_order_policy`),
`GET /api/command/agents/xavier/standing-orders`, the Xavier page's
positions card, and Audrey's daily report (`standing_orders`: invariant, at
most one live order, releases only on terminal, selection, covered /
uncovered, resting orders, fill-capable, lifecycle, floor class, capability).

## Known limitations

* Once the selected hedge holds contracts, an unfilled remainder cannot be
  re-offered after its order ends: it would be a second open HEDGE intent,
  which migration 131's one-open-leg-per-role index refuses. It is recorded
  as REMAINDER_REPLACEMENT_REFUSED (the adapter's `modify` is not used).
* A change of instrument after the first fill is refused and recorded as a
  transition needing separate evaluation; it is never taken automatically.
* The private websocket is not connected in this deployment.
