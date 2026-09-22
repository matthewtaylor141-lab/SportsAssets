# The smallest experiment that resolves P_FILL

**Status: SPECIFIED, NOT AUTHORIZED.** Preparing this is authorized;
placing orders is not. Nothing in this document has been run.

---

## Why an experiment is the only route

`bettor_decision_engine` refuses to score `MAKE_YES` / `MAKE_NO` because
`P_FILL` is `NOT_IDENTIFIED`, and `NOT_IDENTIFIED` is not zero. That is
not a conservative setting to be relaxed — it is a required input that
does not exist.

It cannot be supplied by observation, and the reason is structural: a
public feed shows the book, never *our order in it*. Three quantities
are invisible from outside and all three are needed:

1. **Queue position.** The venue publishes aggregate size per level, not
   per-order queues. The replay sweeps a queue-ahead fraction across
   {0.00, 0.25, 0.50, 1.00} precisely because it cannot know one, and
   every fill figure inherits that sweep.
2. **Whether a trade would have hit us.** Time-and-sales carries price,
   time and quantity — no side, no aggressor flag, no counterparty.
3. **Our own market impact.** A resting quote inside the spread changes
   what others do. The recorded book never saw our order.

The measured consequence: on the development corpus, **85 % of episodes
never filled**, and the entire measured P&L — positive and negative —
came from the 15 % that did. Every economic number therefore rests on an
unmeasured quantity.

---

## What the experiment measures, and nothing else

> **Given a resting quote at a known price, placed at a known time, on a
> known book: did it fill, how much of it, and how long did it take?**

That is the whole objective. Explicitly **not** measured in this
experiment: profitability, strategy validation, inventory policy, or net
economics. Those need the fill model this produces, plus a separate
evaluation.

### Recorded per order

| field | why it is needed |
|---|---|
| placement time, price, size | the decision instant |
| full ladder at placement | depth ahead at our price and better |
| aggregate size at our price, before and after | our position within the level |
| every partial fill: time, size, price | partial fills are the normal case, not the exception |
| cancel time and size remaining | a cancel is an outcome, not a missing row |
| trailing flow and realised volatility at placement | so the fill model can condition on decision-time inputs the live engine also has |
| time to first fill, time to full fill | the hazard, not just the rate |

The output is a **fill hazard conditioned on decision-time observables**
— which is exactly the shape `bettor_p_fill` expects and currently
refuses to produce.

---

## The bound: smallest honest design

| | |
|---|---|
| **markets** | the 12 frozen incentive markets, or a named sports set — one set, fixed before the first order |
| **clip** | 1–5 contracts per side. Large enough to fill, small enough that the loss ceiling is trivial |
| **max concurrent orders** | 2 (one per side, one market at a time) |
| **hard contract cap** | a fixed total across the whole experiment, enforced by the same durable reservation as the request allowance — `bettor_live_control.reserve()` before dispatch, so a crash or an overlapping worker cannot replenish it |
| **wall-clock window** | one fixed window, no extension, no replacement run |
| **kill switch** | the existing DB-backed stop control, fail-closed, checked at the same cadence as the observation loop |
| **loss ceiling** | bounded by construction: worst case is every contract bought at its quoted price and settling worthless |

### What "worst case" actually is

At a 5-contract clip on both sides of one market at a mid near 0.50, the
maximum at risk per round trip is about **$5**, plus fees. Multiply by
the hard contract cap to get the experiment's ceiling. **That number
must be stated and approved as a cash figure before any order** — not
derived afterwards.

---

## Why it must be real orders

A paper or shadow order does not answer the question. The three unknowns
above are all *about our order being in the book*: a simulated order has
no queue position, is never hit, and changes nothing. The experiment is
small **because** it is real, not instead of being real.

---

## What it would and would not settle

**Would:** `P_FILL` conditioned on decision-time observables, for the
market set and window tested. That unblocks `MAKE_YES`/`MAKE_NO` scoring
in the engine and turns the maker actions from `NOT_IDENTIFIED` into
priced ones.

**Would not:** profitability. A fill model makes the EV *computable*; it
does not make it positive. The two costs already measured — taker
completion at ~5× the maker credit, and adverse selection on the fills
that do happen — remain exactly as measured, and a fill model prices
them rather than removing them.

**Would not:** generalise beyond the tested set. A fill hazard measured
on 12 correlated culture markets is a fact about those markets.

---

## Approval this needs, stated separately

1. Funded orders enabled for a named market set, with a stated cash
   ceiling.
2. A trading-control change (`mirror_live`, `MAX_CONTRACTS`) — currently
   false and 0.0, and not changed by anything in the observation
   release.
3. The hard contract cap as a number.

**None of those is requested here.** This document exists so the request,
when it is made, is a single specified decision rather than another
round of design.
