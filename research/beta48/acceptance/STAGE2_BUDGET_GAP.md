# THE CEILINGS ARE NOT ENFORCED — stopped before `obs-run`

Stage 2B carried a pre-arming condition:

> Before arming, verify request budgets are reserved durably before dispatch,
> not merely counted afterward. Listing retries and restarts must not replenish
> the total allowance. Concurrent increment tests alone do not establish this.
> If the implementation cannot enforce these ceilings, stop before obs-run and
> report the specific gap.

**The implementation cannot enforce them.** Four specific gaps, each measured
by execution against real PostgreSQL and the real `main()`, not by reading the
code. `obs-run` was not set. The budget was never armed.

The condition was right to single this out, and right that my earlier evidence
did not establish it. I had verified that `consume_budget` is atomic under
twenty concurrent decrements. Atomicity of a decrement says nothing about
**when** the decrement happens relative to the request it is supposed to pay
for, and that is where all four gaps live.

Reproduce: `python scripts/bettor_budget_reservation_probe.py --dsn ...`
(exit 1 today).

---

## G1 — the charge is skipped entirely when no market qualifies

`main()` dispatches every BBO read inside `_discover`, and only afterwards
calls `consume_budget`. But the `EMPTY_UNIVERSE` early return sits **between**
them:

```
1335   first = await _discover(...)          # <-- every BBO read is dispatched here
...
1366       return {"started": False, "why": "EMPTY_UNIVERSE", ...}   # <-- returns
...
1404       await ctl.consume_budget(control_pool, consumed)          # <-- never reached
```

Measured, Case A — one lifetime, nothing failing, no crash:

```
venue: 1 listing call, 40 BBO attempts, 40 DISTINCT markets
budget row: consumed=0 remaining=40
   the row was charged for what was dispatched   0   expected 40  *** MISMATCH ***
```

Forty distinct markets were read and the durable row still says nothing has
been spent.

This is not an edge case — it is the **expected** outcome. At the historical
6.35% admit rate, 40 candidates yields ~2.5 eligible markets, and zero is
ordinary. So the ordinary path spends the allowance and records none of it.

## G2 — dispatch precedes the charge even on the success path

A worker that dies between the dispatch and the charge has spent the venue
requests and recorded nothing, and `workers/all.py:run_forever` restarts it.
Measured, Case B — four lifetimes against **one** armed budget, crashing at
`loop.recover()` (a real, unguarded crash point that sits after dispatch and
before the charge):

```
lifetime 1:  40 BBO attempts,  40 distinct so far, row consumed=0
lifetime 2:  80 BBO attempts,  40 distinct so far, row consumed=0
lifetime 3: 120 BBO attempts,  40 distinct so far, row consumed=0
lifetime 4: 160 BBO attempts,  40 distinct so far, row consumed=0

   the budget row still says the probe may continue   True   expected False
```

160 BBO attempts, and the row believes the probe has not started.

## G3 — `max_distinct` bounds *successful enrichments*, not distinct markets requested

`consumed = coverage["distinct_enriched"]`, and
`coverage = dict(coverage, distinct_enriched=len(rows))` counts only markets
that **parsed**. A market that was requested and failed cost a venue request
and is charged nothing. Worse, `_discover` re-derives its per-round room from
the same number:

```python
left = max_distinct - len(by_slug)      # rows, i.e. SUCCESSES
```

so failures buy another round of fresh markets. Measured, Case C — one
lifetime, no crash, 30 of the first 40 reads failing:

```
70 BBO attempts over 70 DISTINCT markets, 30 of them failing
the row was charged: 0
   the charge equals the distinct markets requested   0   expected 70
```

**Seventy distinct markets against a ceiling of forty, inside a single
lifetime, with no restart involved.** With `PROBE_ROUNDS_AT_START = 2` the
per-lifetime worst case is 80 distinct markets — twice the approved ceiling.

## G4 — listing requests have no durable accounting at all

`_list_candidates` bounds pages and retries **per invocation, in memory**.
`bettor_live_control.py` contains no listing counter — the only durable writes
are `distinct_consumed` and the control row. Case B shows four listing sweeps
against one armed budget. Restarts replenish the 18-request listing allowance
in full.

---

## What the ceiling actually is today

The absolute `deadline_at` **is** durable and correct — it is stored at arm
time and is not extended by a restart. It is therefore the only thing bounding
total exposure. Within 1800 s, with the escalating no-start backoff
(300/900/1800/3600 s) and up to 80 distinct markets per lifetime:

| | Approved | Actual worst case |
|---|---|---|
| Distinct markets | 40 | ~240 (3 lifetimes × 80) |
| BBO attempts | 160 | ~240+ |
| Listing requests | 18 | ~54 (3 × 18) |

So roughly **6× the approved distinct-market ceiling**, bounded only by the
deadline. Not unbounded — but not the authorized experiment.

## Why nothing is at risk right now

`obs-run` was not set and the budget row was never armed. Both are required,
and they fail closed independently: with no `bettor_live_probe_state` row,
`read_budget` returns `BUDGET_UNREADABLE` and the loop refuses **even if**
`obs-run` were set. Stage 2A is deployed and idle behind the `false` control.

## What a fix requires

Not a patch to the charge site — a change of order. Reservation must happen
**before** dispatch and be keyed to *markets requested*:

1. Reserve N distinct slugs durably in one statement that both decrements and
   returns the granted slugs, so a crash loses the allowance rather than
   granting it.
2. Charge **attempted** markets, not enriched rows, and drop the
   `left = max_distinct - len(by_slug)` re-derivation so failures cannot buy
   another round.
3. Move the charge above the `EMPTY_UNIVERSE` return, or out of `main()`'s
   happy path entirely and into the probe.
4. Give the listing its own durable counter in the same row.

That is a real change to the acquisition path, with its own tests, and it
changes the approved code SHA. It is not something to slip in under an
approval granted for `88d429ed`.
