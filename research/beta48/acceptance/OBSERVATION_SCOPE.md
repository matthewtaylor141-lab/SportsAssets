# What the captured population can evaluate — and what it cannot

Manifest captured 2026-09-22T21:43:59Z, GitHub Actions run 35788331780,
from commit `30c7ae1`. Unauthenticated `GET /v1/incentives`, 4 pages,
441 programme rows, zero errors. Frozen allowlist committed in `5cfd211`.

**This document exists because two experiments were about to be conflated.**
Observing an incentive programme measures incentive opportunity. It does
not validate an inventory policy that was developed on football.

---

## What was actually captured

| | |
|---|---|
| qualifying markets | **12** (gate required ≥10) |
| distinct programmes | **1** — `culture_low_20260921` |
| distinct event start times | **1** |
| category | CUL / MUSIC — Billboard "#1 album of 2026" |
| reward pool | $50/day · discount factor 0.25 · target size 500 |
| period | `daily_event` |
| instrument state | `INSTRUMENT_STATE_OPEN` |
| `event_start_time` | **2026-12-27T04:59:00Z** |

Slugs: `ccpc-bilbrd-1album-any2026-{alewar, benboo, beyonc, bileil,
charoa, chaxcx, coldpl, doechi, dualip, eminem, fraoce, jusbie}`.

**Twelve markets, one programme, one event.** The manifest's own
independence note says it: markets sharing a programme id or an event
start time are not independent statistical units.

---

## `event_start_time` is not a resolution timestamp

This matters because the whole volatility-gate line of reasoning was
aimed at *not quoting into a resolving event*, and I had written that
the incentives API supplies the missing time-to-resolution input.

It does not. `event_start_time` here is **2026-12-27** — over three
months after the observation window. It is the programme's event
reference, not the moment the market resolves, and for these
instruments the two are not close to each other. On a live sporting
market the same field would sit near kickoff; on a year-end award
market it sits at the award. **The field's meaning is not constant
across categories, so it cannot be read as "time to resolution"
anywhere without checking what it refers to.**

The claim in the previous handoff — that the observation release
captures the time-to-resolution input the corpus lacks — is withdrawn.

---

## What this population CAN evaluate

**1. Incentive opportunity, which is the thing it is actually for.**
`Score = DiscountFactor^(ticks from best) × OrderSize`, each side
normalised to 1.0 once Target Size is met. The denominator is the
*aggregate resting size walked from best outward* — a quantity nothing
we hold has ever measured on a market that is actually in a programme.
A day of ladder observation on these 12 markets gives:

- the realised score denominator, second by second;
- what share a given clip at a given tick offset would have earned;
- how often Target Size (500) is actually met, per side;
- whether the $1.00 minimum payout is reachable at plausible clips.

That converts every incentive figure in `ECONOMIC_VERDICT_V2.md` from a
**transferred scenario** into a measurement — for *these* markets.

**2. The shared policy's decision-time behaviour on real live ladders.**
`admit`, `quote_prices`, `quote_size` and the two gates run against real
depth, with the engine's verdict recorded. That exercises the runtime
path end to end. It produces **decisions, not P&L**.

**3. Feed mechanics.** Epoch continuity, liveness, initial-ladder
arrival, and whether quiet books are being correctly distinguished from
stale ones.

---

## What this population CANNOT evaluate

**The football inventory policy.** Different instruments, different
category, different horizon, no resolution inside the window. The
episodes that produced the entire measured loss were opened during live
college football play; nothing resembling that occurs on a year-end
album market. Running the observation and then reporting anything about
C3/R10/R14 would be answering a different question while looking like an
answer to this one.

**Anything requiring our own orders.** Fill rate, queue position, and
therefore `P_FILL`. A public feed shows the book, never our order in it.

**Any P&L.** No orders are placed, `max_contracts` is 0.0, and the
runtime adapter holds no order path.

---

## Which frozen policy the collected population can actually score

Exactly one, and it is not an inventory policy:

> **`BETTOR_POLICY_V1` entry surface** — `admit()` with
> `min_spread_ticks`, the mid band, `is_open()`, and the two gates
> (`flow_cover`, `vol_cover`) — evaluated as an **admission rate and a
> reason distribution** over 12 incentive-programme markets for one ET
> day, together with the **incentive score share** those admitted books
> would have earned.

Prespecified before the run, so it is not chosen after seeing the data:

- **Reported:** admitted / stood-aside counts per market, the reason for
  every stand-aside, realised score denominator per side per sampled
  second, implied share for a 100-contract clip at the touch and at one
  tick back, and the fraction of sampled seconds where Target Size was met.
- **Not reported, because it is not evaluable here:** net P&L, fill rate,
  capital-hours, per-capital-hour, and any statement about C3, R10, R14 or
  the taker-fee mechanism.
- **Units:** 12 markets, **1 event, 1 programme**. Any interval computed as
  though there were 12 independent units is invalid, and none is computed.

---

## Honest limits of even that

One programme on one day is one observation of the incentive
environment, not a distribution over it. It can show that the score
machinery works against real depth and give a concrete share figure for
that day. It cannot establish that the figure is typical, and no number
of additional days on the *same* programme and event would make those
markets independent of each other.
