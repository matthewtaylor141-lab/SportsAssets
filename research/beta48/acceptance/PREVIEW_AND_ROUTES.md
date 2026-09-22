# Preview contract, execution routes, and a claim I withdraw

## 1. The documented preview contract — retrieved

`https://docs.polymarket.us/institutional/oapi-schemas/trading-schema.json`

```
PreviewOrderRequest   { "request": InsertOrderRequest }
PreviewOrderResponse  { "previewOrder": Order }
```

The response field is **`previewOrder`**, not `order` — the SDK's
`{order: Order}` is an SDK naming choice, not the wire contract.

**`Order` carries no collateral, buying-power or margin field.** Its
documented properties: `bestLimit`, `goodTillTime`,
`immediatelyExecutableLimit`, `lastTradeId`,
`commissionNotionalTotalCollected`, `selfMatchPreventionInstruction`,
`orderCapacity`, `ignorePriceValidityChecks`, `lastTransactTime`,
`makerCommissionsBasisPoints`, `manualOrderIndicator`,
`fractionalQuantityScale`, `priceToQuantityFilled`.

**So preview cannot answer the collateral question** — now established
from the contract rather than from the SDK's missing field.

**What preview CAN answer**, and it is not nothing:
`commissionNotionalTotalCollected` and `makerCommissionsBasisPoints`
give the venue's own fee arithmetic for a specified order, which is a
direct check on the engine's fee model without placing anything.

### Four documented facts that bear on a maker policy

| | |
|---|---|
| `priceToQuantityFilled` | quantity filled **at each price** over an order's life — per-price fill detail we could record, which the replay currently has to infer |
| `fractionalQuantityScale` | "Divide raw integer quantities by this value for proper scale" — quantities are scaled integers, per instrument |
| `SelfMatchPreventionInstruction` | `REJECT_AGGRESSOR` / **`CANCEL_RESTING`** / `REMOVE_BOTH`. A two-sided maker that also crosses out can have **its own resting order cancelled by its own aggressive order**, depending on this setting |
| `OrderState` | includes `PENDING_RISK` and `PENDING_REPLACE` alongside `PENDING_CANCEL` |

## 2. The cancellation claim — corrected

I wrote that "the venue cancels our resting orders on connection loss
and logout." **That is more than the evidence supports.**

`UnsolicitedCxlReason` is a **reason code carried on a cancellation
message**: `CONNECTION_LOSS`, `LOGOUT`, `EXCHANGE_OPTION`, `OTHER`. It
establishes that unsolicited cancellation **can** occur and what
reasons the venue reports for it. It does **not** establish that any
particular disconnect cancels anything of ours.

In particular the **order session and the market-data session are
different connections**. Our streaming socket dropping says nothing
about resting orders. Whether cancel-on-disconnect applies depends on
the order session's own configuration, which is **not established** and
must not be relied on as a control.

## 3. Execution routes — resolved without touching production

| route | result |
|---|---|
| this container streams the CSVs | **NO.** Egress proxy `CONNECT 403` for both `www.polymarketexchange.com` and `docs.polymarket.us` — organization policy |
| download a workflow artifact here | **NO.** `productionresultssa14.blob.core.windows.net` also `CONNECT 403` |
| a new workflow on this branch | **NO.** `workflow_dispatch` requires the workflow **name** on the default branch, and the default branch **is** `claude/session-njaewf` — the branch production auto-deploys from |
| **an existing dispatchable workflow, running this branch's copy** | **YES** — and this is the route |

`workflow_dispatch` runs the workflow file **from the ref dispatched**;
only the name must exist on the default branch. `fetch-docs.yml`
already does. So the logic lives on this branch and **production is not
altered to register anything.**

The input schema is fixed by the default branch's copy, so a new input
cannot be added without touching production. A **URL fragment** rides
inside the existing `url` input and is never sent to the server:

```
...-schema.json#op=preview                extract that operation and
                                          every schema it $refs
...YYYYMMDD-time-and-sales.csv#stream     stream ONE file, filter to
                                          our 12 markets, emit an
                                          artifact
```

**No production change is requested.**

## 4. A claim I withdraw: "M1 is the only route"

`backend/sportsassets/venue_reconcile.py` already reads our account:

```python
venue_positions()    -> client.portfolio.positions(...)
venue_open_orders()  -> pmus.open_orders()
venue_activities()   -> client.portfolio.activities(
                            ["ACTIVITY_TYPE_TRADE",
                             "ACTIVITY_TYPE_POSITION_RESOLUTION", ...])
```

and its own header says: *"SETTLED PROCEEDS — cash from
POSITION_RESOLUTION activities. Realised."* It reconciles those against
a **`live_orders`** table whose `order_id` is, in the module's words,
*"what we recorded when we submitted."*

**So orders have been submitted from some system path.** "No BETTOR
strategy order" was never the same claim as "this account has no
history", and I conflated them. `venue-reconcile.yml` is on the default
branch, carries `PMUS_KEY_ID`/`PMUS_SECRET_KEY`, and gates them behind
`tests/test_venue_reconcile_is_read_only.py` — credentials are not in
the environment of the step that proves the reader cannot trade.

**Historical settled proceeds may therefore already answer the
capital-release question with no order and no new capital.** That is
the next read, and it changes what D3–D5 should ask for.

## 5. D3–D5: authorized, not yet spent, and why

The 14-request authorization is **unused**. I stopped before spending
it because the finding above changes the questions:

- if `live_orders` and POSITION_RESOLUTION activities already contain a
  submission→fill→settlement→proceeds sequence, the release timing is
  **observable from history** rather than from a new order;
- a blind `balances`/`positions` read at rest, with no order outstanding
  and possibly no position, would show an idle account and settle
  nothing.

**Proposed use of the 14, revised:** first a read-only SQL count of
`live_orders` and any recorded fills (**zero venue requests**), then
target the remaining budget at the activity window that history points
at. I will proceed on that basis unless you say otherwise.

**The expired observation budget stays expired. No orders. No transfers.
No control changes. No credential movement.**
