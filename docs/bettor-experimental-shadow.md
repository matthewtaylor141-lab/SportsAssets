# BETTOR_EXPERIMENTAL_SHADOW

The research lane. Owner directive 2026-09-19 21:2xZ ("MAJOR SHADOW
RESEARCH CORRECTION"), 21:5xZ (institutional L2), 22:0xZ (the scale and
identity gate), 22:2xZ (identity), 22:4xZ (the first trade) and 23:0xZ
(the evidence bridge).

## Why it exists

Management saw hundreds of BETTOR decisions, zero trades, $0 played and
no disagreement evidence — "honest but experimentally insufficient."
The answer was two lanes, not one:

| | `BETTOR_EV_SHADOW` | `BETTOR_EXPERIMENTAL_SHADOW` |
|---|---|---|
| purpose | decision-grade evidence | falsify candidate models |
| may sit at zero trades | **yes, indefinitely** | no — that is the point |
| unproven model | refuses | is the experiment |
| invalid input | refuses | refuses |
| promotion | the existing frozen framework | **none, ever** |

The two lanes share no table, no counter and no figure. This lane is
not a member of `lanes.LANES`, and nothing it produces is evidence
about the decision-grade lane in either direction.

## The loop

```
  focus-set sampler  ──▶ bettor_experimental_observations
        │                       (this lane's own table)
        ▼
  seal at T0 ────────▶ bettor_experimental_seals     (written BEFORE
        │                                             any arrival book)
        ├──────────▶ bettor_l2_requests  ──▶  the GitHub bridge
        ▼                                          │
  arrival walk ◀───────── bettor_l2_evidence ◀─────┘
        ▼
  bettor_experimental_decisions + _positions
        ▼
  bettor_experimental_markouts   (30S / 60S / 300S, appended)
```

## The five things this lane refuses to do

**1. Decide against a book it has already seen.** The bridge answers
minutes after the request, so the seal and its execution fall on
different ticks. A seal held in memory is lost on restart, and the
obvious repair — decide now against a book already in hand — *is* the
lookahead. The seal is persisted first, and the ledger proves the
ordering: `sealed_at` precedes the evidence's `received_timestamp` on
every executed row, or the row does not exist. The verification read's
§4 counts the violations; it must be 0.

**2. Rewrite an action into a refusal.** A `BUY_NO` that cannot be
executed records `BUY_NO` beside
`BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE`. Collapsing the two into
`NO_TRADE` would erase the model's output, and the later question —
*was the model right when we could not act?* — would have no evidence
behind it. ACTION and EXECUTION_STATUS are separate columns and
separate table columns on the screen.

**3. Report an unmeasured thing as a zero.** Unfilled means the book
was walked and gave nothing; NOT_IDENTIFIED means it could not be
walked, and its notionals are NULL. A position with no observed markout
has *no* P&L, not zero P&L, and the COMMAND panel prints the unmarked
count beside the figure it is excluded from.

**4. Pool two latency regimes.** A book fetched by a CI runner minutes
after the decision is a different execution environment from a
persistent worker's. Every row carries `latency_regime`, the schema
CHECKs it, and no figure is ever computed across both.

**5. Rank on a tiny sample.** The leaderboard prints every figure and
says SAMPLE TOO SMALL below 30 marked positions.

## The frozen registry

Declared before the first outcome was known; each declaration is
hashed, and `verify_all()` re-derives every hash at boot. A rule edited
under a live experiment fails the check and the worker refuses to seal.

| | model | state | why not armed |
|---|---|---|---|
| X1 | short-horizon direction | ARMED | |
| X1C | the null control beside X1 | ARMED | |
| X2 | relative value | AWAITING | needs the complement leg captured at the same instant |
| X3 | microprice | AWAITING | the BBO feed carries no touch sizes; a microprice with sizes assumed equal IS the midpoint |
| X4 | order-flow imbalance | AWAITING | OFI is defined on changes in resting size; with no sizes there is no OFI at all, not a noisy one |
| X5 | external disagreement | AWAITING | no writer populates the external consensus |

## Two findings production produced that no test had

**The leg binding gated on a key the feature builder never sets.**
`bind_leg` is called on `microstructure_of`'s output, which carries
`status: MEASURED` and no `readable` key at all. The gate read only
`readable`, so 127 opportunities across 71 symbols were all stamped
market-level and the eligible population was empty while every
component reported healthy. Every fixture had been hand-built with
`readable: True` — a key the real producer never emits. The pin now
builds its input by calling the real producer.

**The collector samples each market about once an hour.** It selects
with `ORDER BY updated_at DESC LIMIT 10` over a churning board, so it
almost never revisits a market: verified at 00:01Z as
`YES_CONTRACT_BOOK n=17 symbols=17`. X1's frozen rule is the signed
drift over the last five *captured samples* against a 60-second
horizon, so on that feed it could never fire.

The rule was **not** tuned to fit the feed — lowering the sample
minimum after discovering the rule cannot fire is tuning a frozen
experiment to manufacture activity, and stretching the window would
feed a 60-second momentum rule samples an hour apart. The feed changed
instead: the lane samples its own focus set of eight markets once a
tick, into its own table. Because the eligibility rule string is hashed
onto every population, the populations drawn under each feed are
permanently distinguishable rather than silently merged.

## The evidence bridge, and what it cannot become

The production PMX private key exists only as a GitHub repository
secret. The authenticated lane therefore holds the credential and
carries **market data** across — never the credential. It reads
instruments / BBO / L2 read-only and writes exactly two tables. A job
step greps for every mutating path *before* a credential is staged, the
emitted SQL is greped again before psql sees it, and the whole file is
published as an artifact. A trading capability would need a new table,
a new module and a new review. That is the point of the shape.

Latency is measured, never manufactured: `venue_request_ms` for the
call itself and `bridge_latency_ms` for the whole round trip, both on
every row.

## The caveat that travels with every bridge-era number

X1's horizon is 60 seconds. The bridge's cadence is ten minutes. So
**bridge-era X1 results are a weak test of X1** — they measure the rule
under a latency it was not designed for, and most short-horizon
markouts miss their tolerance and record NOT_IDENTIFIED with their lag.
That is a real finding about the regime rather than a gap in the data,
and it is why the schema refuses to pool these rows with a future
persistent worker's.

## Standing invariants

`NOT_DECISION_GRADE` true, `REAL_ORDER_SUBMITTED` false,
`CAPITAL_AT_RISK` 0 — each by CHECK, not by promise. No order path
exists in this lane's import graph. Nothing here promotes anything.

## Reading it

* COMMAND → Experimental shadow (`#shadow/experimental`)
* `research-sql` → `bettor_experimental_verify.sql`
