# BETTOR RELEASE CANDIDATE — integrated engine, measured data

Restrictions unchanged: `mirror_live=false`, **no real orders, no capital
activation, nothing deployed**. Production remains `3349219`. The pilot
remains unactivated.

> **This revision replaces claims with measurements.** Six statements in the
> previous version were unsupported or wrong; all six are corrected below and
> itemised in
> [`DATA_FINDINGS_2026_09_21.md`](DATA_FINDINGS_2026_09_21.md). The capacity
> analysis in §6 is now bounded by **traded volume**, which had never been
> queried.

**264 tests pass** across the seven modules this work touches
(`release_tests.xml`). Three commands, all exit 0, all outputs committed.

| # | command | artifact |
|---|---|---|
| 1 | `python3 scripts/bettor_shadow_demo.py` | `shadow_demo_output.txt` |
| 2 | `python3 scripts/bettor_replay_real.py` | `replay_real_output.txt` |
| 3 | `python3 scripts/bettor_prospective_runner.py --self-test` | `prospective_selftest_output.txt` |

---

## 1. THE INTEGRATED ENGINE

| component | file | status |
|---|---|---|
| Decision engine | `backend/sportsassets/bettor_decision_engine.py` | working |
| **Published fee schedule** | `backend/sportsassets/bettor_fee_schedule.py` | **working — new** |
| Venue/account contract | `backend/sportsassets/bettor_venue_contract.py` | working |
| Maker economics | `backend/sportsassets/bettor_maker_economics.py` | working |
| Observation adapter | `backend/sportsassets/bettor_observation_adapter.py` | working |
| Shadow loop | `backend/sportsassets/bettor_shadow_loop.py` | working |
| **Live venue read** | `backend/sportsassets/bettor_live_read.py` | **working — new, not deployed** |
| **Settlement ingestion** | `backend/sportsassets/bettor_settlement_ingest.py` | **working — new, not deployed** |
| **Decision-only worker** | `backend/sportsassets/workers/bettor_prospective.py` | **working — new, ABSENT from `workers/all.py` by design** |
| Prospective runner | `scripts/bettor_prospective_runner.py` | working, **not deployed** |

Chain: `observation → normalize → decide → simulated execution → inventory →
settlement → reconciled accounting`. `ShadowAdapter` is a different class from
the production adapter, not the same one in a mode; the prospective runner
imports neither it nor any venue client, asserted by AST scan.

## 2. THE FEE SCHEDULE — PUBLISHED, NOT HYPOTHETICAL, NOT VERIFIED

`fee = θ × contracts × price × (1 − price)`, banker's-rounded to the cent
**per fill**. Two dated schedules, because the date is part of the schedule:

| schedule | θ_taker | θ_maker | taker rounding |
|---|---|---|---|
| `PMUS_PUBLISHED_2026_07_01` | +0.06 | **−0.0125 (rebate)** | per fill, independent |
| `PMUS_PUBLISHED_2026_09_17` | +0.0695 | **−0.0125 (rebate)** | cumulative per order |

`for_date()` requires a date and refuses a fill before the first published
schedule rather than pricing it at the nearest one. The two sources disagree
on taker rounding; **both are implemented and the disagreement is recorded**,
because only a settled statement decides which applied.

**This was an unconnected module, not missing information.**
`research/run85_trackb_fees.py` has carried the published schedule since
2026-07-01 — sourced, dated, reproducing the venue documentation's worked
examples. The engine never imported it and ran on hand-typed flat constants
with the maker side entered **as a charge**. A test pins the 2026-07-01 terms
against that module exactly, so the two copies cannot drift.

**Status is `PUBLISHED`, not `VERIFIED_APPLIED`.** The engine's fee gate has
three states: `NOT_ESTABLISHED` blocks as an unknown cost; `PUBLISHED` may be
**computed and reported** but never **selected** for execution;
`VERIFIED_APPLIED` needs a settled statement, and nothing has one.

**The sign error was the largest single input error in the evaluation.** At
p = 0.485 the venue **pays** 0.00312/contract where the engine subtracted
0.01 — a swing of 0.0131/contract in the maker's favour. **A corrected input
is not an edge**; see §5.

## 3. REAL-OBSERVATION REPLAY — command 2

**16 real rows**, ages 0.307–3.878 s, every one carrying the five-level
ladder, under `('polymarket-us','institutional')` —
`holds_both_legs_independently = UNKNOWN`. The demo fixture is not used.

**Normalization:** 16 accepted, 0 rejected.

**Depth is read from the ladder's top level, not from `yes_depth`.** Measured
across 939 rows carrying both: `yes_depth.ask` equals the **five-level sum**
on **939 of 939**, and the top level on 0 except where the two coincide. The
median ratio is **15.29×** and the maximum **550,001×**. An order sized from
the cumulative figure is not a large order at the quote — it is an order
walking four levels up a book the venue never showed at the touch.

**Pairing:** 0 complement pairs. Venue-wide, 11 market_ids carry two
`outcome_leg` labels — but all 11 have **one** `instrument_id` and **one**
`condition_id`, `condition_id` is `NOT_IDENTIFIED` on all 1,538 rows, and
`instrument_id` equals `market_id`. **There is no leg-level venue identifier
in the capture**, so whether those labels are genuine complements is not
checkable from our data. `no_ask`/`no_bid` are `NOT_IDENTIFIED` on 100% of
rows: the sibling instrument is never read.

**Decisions:** 16 of 16 → `NO_TRADE`. Blockers:
`FV_BETTOR_INDEPENDENT_NOT_VALIDATED` ×64, `P_FILL_NOT_IDENTIFIED` ×32,
`NO_PAIRED_INVENTORY` ×16, `COMPLEMENT_ABSENT` ×16,
`MERGE_NOT_OBSERVED_ON_INSTITUTIONAL` ×16.

**Execution:** 0 orders. Ledger residual 0, fees 0, basis 0, positions 0.
**Manufacturing a fill to produce a number is precisely what this run must
not do.**

**Settlement:** `bettor_state_settlements` holds 0 rows, **0 fills**, 0
settled. `record_settlement()` is **defined and never called** — a missing
ingestion path, not evidence that nothing resolved. **552 of 1,538
observations are of events that had already started** (`time_to_event_s`
negative, `live_status` LIVE), so the previous version's "no outcome has
matured for any observation" was unsupported. An event starting is not an
event resolving, and the capture records no end time, so it is still not
readable — but it was never established.

## 4. PROSPECTIVE RUNNER — command 3

**Status: PREPARED AND SELF-TESTED, NOT RUNNING.** Self-test passes: decided
16, duplicates refused 16, rejected 0; dedup, restart and the no-order path
(AST scan of calls and imports) all verified.

**Freshness now uses three clocks and requires all three.** `book_received_ts`
is populated on every captured row and was being treated as **optional**, so
a row lacking it skipped the check and was treated as verified. A naive
timestamp is **refused**, not assumed to be UTC. Decision time is measured
**per observation**, not once per batch.

**A source-to-receipt delay is not clock skew.** Measured across 1,203 rows:
**zero** have a source stamp in our receipt's future, so there is **no
measured clock disagreement at all**. The old `abs(received − source) > 120 s`
test would have flagged the majority of rows, because what it was measuring
was the sampler's read cadence. Transport only runs forward; only a negative
delay can show the clocks disagree. Separately, **we cannot decide on a book
we have not received** — a causality check inside our own pipeline, which the
self-test caught after the skew rewrite dropped it.

**The median source-to-receipt delay is 549.6 s — 9.2 minutes**, against a
10-second decision bound. That is the binding constraint on a live feed and it
is now measured.

## 5. STRATEGY EVIDENCE

v2 protocol frozen **before** any v2 evaluation:
[`PREREGISTRATION_MAKER_V2.md`](PREREGISTRATION_MAKER_V2.md). v1 is preserved
unchanged; nothing was retuned.

Maker round trip on the 16 real books, quoted at **10 contracts** (fees round
per fill, so a per-contract figure does not carry to another clip size):

| mid move | exit route | positive | negative | median/contract |
|---|---|---|---|---|
| 0.000 | cross out | 0 | 16 | −0.013000 |
| 0.000 | rest out | 16 | 0 | **+0.055000** |
| −0.005 | cross out | 0 | 16 | −0.018000 |
| −0.005 | rest out | 16 | 0 | +0.050000 |
| −0.010 | rest out | 15 | 1 | +0.045000 |
| **−0.020** | **rest out** | **9** | **7** | **+0.035000** |
| −0.020 | cross out | 0 | 16 | −0.033000 |

**The "rest out" row moved from negative to positive when the fee sign was
corrected.** That is a fact about the model's sensitivity to one input, and
it is the reason v2 is stricter than v1 rather than looser: an evaluation
whose conclusion turns on one assumption was measuring the assumption.

**The passive-exit row assumes a SECOND resting order fills.** It carries two
unmeasured fill probabilities, not one — the entry's and the exit's,
conditional on the entry having filled and the market having moved. Under
rule 2 that makes it `UNRESOLVED` whatever its sign.

### Verdicts under the frozen rules

| candidate | verdict |
|---|---|
| **M1** (passive quote, held/exited) | **UNRESOLVED.** Needs `conditional_reference_move`, entry `p_fill` and — on the passive route — exit `p_fill`, all `NOT_IDENTIFIED`. Rule 5 additionally requires `VERIFIED_APPLIED` fees; the schedule is `PUBLISHED`. |
| **M2** (maker first leg + completion) | **UNRESOLVED — BLOCKED ON CAPABILITY.** `holds_both_legs_independently` is UNKNOWN, and the capture holds no leg-level identifier with which to establish a complement at all. |
| **I1** (completion-vs-exit policy) | **SUPPORTED AS A POLICY, NOT AS AN EDGE.** It selects the higher-incremental-cash action on every constructed case and excludes sunk basis. A correctness property. |

**No candidate is SUPPORTED as an edge.** The sample is 16 contracts against
a required 200; the interval test was not reached. Acceptance rules were not
relaxed — rule 5 was added.

### Data, assumptions, uncertainty

- **Observed:** 16 real books; 939 rows for depth shape; 1,203 for clocks;
  1,538 for maturity and identity; 609 for traded volume.
- **Published:** the fee schedule. Documented by the venue, never seen applied
  to this account.
- **Assumed:** every grid value, and that a resting exit fills at all.
- **Synthetic:** every fill. **BETTOR has never rested an order.**

## 6. CAPACITY — $500,000/DAY, BOUNDED BY MEASUREMENT

### 6.1 The arithmetic

$500,000/day of executed notional at ~$0.50/contract = **1,000,000 contract
executions/day ≈ 11.57/sec**; at 10 contracts/order, 100,000 orders/day ≈
1.16/sec. *(The earlier version labelled the order count as the contract count
and was 10× wrong. That correction stands.)*

**Turnover definitions**, since the earlier version's were ambiguous:
*executed notional* counts both sides of a round trip; *position notional*
counts entries only; *working capital* = position notional ÷ turns per day,
which needs a holding period.

### 6.2 What is measured

| requirement | measured | verdict |
|---|---|---|
| Decision throughput | 21,917 obs/sec single-threaded | clears by ~4 orders of magnitude |
| **Traded notional available** | **$396,361/day across all 396 observed markets** (1,106,312 shares) | **the target needs ~63–126% of every dollar traded in the observed universe** |
| Books meeting every pilot condition | **130 rows / 130 markets** of 1,526 observed | |
| Eligible books fresh enough to act on | **18 of 449 within 10 s (4.0%)**; median eligible age 406.5 s | the feed is the constraint |
| Executable size at the touch | p10 **2**, median **75**, p90 3,000 contracts | |
| Spread (795 rows) | p10 0.0100, median 0.0300, **p90 0.9400** | top decile effectively untradeable |
| **Measured holding period** | **none exists — 0 fills anywhere** | working capital `NOT_IDENTIFIED` |
| Positive-EV action | **none** | |

### 6.3 The conclusion

**The shortfall is not a gap to be narrowed by scaling.** To execute
$500,000/day of notional BETTOR would have to be a majority of everything
that trades in the markets it observes. That is a market-share statement, not
a throughput one, and the software clearing its requirement by four orders of
magnitude does not touch it.

**Economically supportable turnover today: $0** — no action has identified
positive EV, and trading negative-EV opportunities to manufacture turnover is
excluded.

**"One hour and one day are scenarios, not bounds" — confirmed.** The
repository contains **zero fills**, so no holding period has ever been
measured and any capital figure for this strategy is a scenario.

### 6.4 What these figures do not establish

Our observed universe is **not** the venue: 1,526 markets seen by a research
sampler not built for coverage. Expanding it is now a specific measurable
requirement rather than an assumption. Traded volume is what the market did
**without us**. Displayed depth is a loose upper bound on executable depth —
BLOCK_4: 22,297 displayed shares against 180 traded in 16 minutes, zero
touches.

## 7. REMAINING REQUIREMENTS FOR A BOUNDED LIVE PILOT

| # | requirement | status |
|---|---|---|
| 1 | Published fee schedule wired | **DONE** |
| 2 | Depth read as top-of-book | **DONE** |
| 3 | Freshness: required receipt stamp, transport ≠ skew, per-observation decision time | **DONE** |
| 4 | One combined exposure limit; pre-trade worst-case loss budget | **DONE** — enforced in the loop |
| 5 | Two-phase cancel so a replacement never overlaps what it replaces | **DONE** |
| 6 | Fee schedule `VERIFIED_APPLIED` from a settled statement | **missing** — needs a fill. But `feeCoefficient` is on the public market payload and is a direct check on which schedule is in force (0.06 = 2026-07-01, 0.0695 = 2026-09-17), for one public read |
| 7 | `holds_both_legs_independently` resolved | **UNKNOWN** — probe RUN 2026-09-21, blocked: `PMUS_KEY_ID`/`PMUS_SECRET_KEY` are empty in CI, so the client is public-only and all four account reads return `AuthenticationError` |
| 8 | Account identity verified | **blocked, same cause** — verdict `unreadable`, `identity_fields_found: []` |
| 9 | Decision-latency read path | **written, not deployed** — `bettor_live_read.read_book`; the capture feed's median delay is 549.6 s and the live path's is unmeasured |
| 10 | `record_settlement()` wired | **written, not deployed** — `bettor_settlement_ingest`; the table is still empty because nothing has run |
| 11 | `p_fill` measured | **impossible without a pilot** |
| 12 | Execution gate deployed | built, tested, **undeployed** |
| 13 | A candidate SUPPORTED under frozen rules | **none** |

**Item 11 is the genuine circularity** — measuring fill probability requires
resting orders, and resting orders require a pilot. The smallest honest break
is [`PILOT_PROPOSAL.md`](PILOT_PROPOSAL.md) v2, whose sole deliverable is a
`p_fill` measurement.

### 7.1 The live connection — implementation, read-only run, deployment

The directive draws this line and so does the code:

| | what it is | status |
|---|---|---|
| **IMPLEMENTATION** | `bettor_live_read`, `bettor_settlement_ingest`, `workers/bettor_prospective`. Code that, given an authenticated client, reads. | **DONE.** Writing it activates nothing. |
| **READ-ONLY RUN** | Calling it against the venue. Reads a public book and a market listing; submits nothing; writes no accounting record. | `bettor-capability-probe.yml`, dispatch-only, proof-before-keys, key names and verdicts only. |
| **DEPLOYMENT** | Running it on a schedule in production. | **NOT DONE and not requested by writing the files.** `bettor_prospective` is deliberately absent from `workers/all.py` and a test asserts it. |

**Exact deployment, if and when authorized** — `PILOT_PROPOSAL.md` §7: add
`bettor_prospective` to `sportsassets-workers`; migrate
`bettor_prospective_decisions` keyed by `observation_id`. **No new credential,
no new permission, no order path. Rollback: remove the worker from the
service.** It writes only its own table.

**The settlement ingestion's outcome-field list is a hypothesis.** The SDK is
not documented to carry a resolution field and our capture has never held one,
so a market payload with none comes back `UNREADABLE` **with the key names
that were present** — which corrects the list with one read rather than more
guessing. Four statuses are reported separately (`RESOLVED` / `PENDING` /
`UNREADABLE` / `UNMATCHED`) plus `INGESTED`, and **`INGESTED` is not
`RESOLVED`**: a resolution read and not written is a resolution we do not
have, and reporting the read count as the stored count is exactly how a
pipeline looks like it works while its table stays empty.

## 8. WHAT IS AND IS NOT ESTABLISHED

**Established:** the engine consumes real venue data, normalizes it under
institutional semantics with correctly-sized depth and three-clock freshness,
prices every action on the venue's published fee schedule, evaluates every
alternative with explicit economics, manages simulated execution and inventory
under one combined exposure limit and a forward-looking loss budget,
reconciles cash to residual 0, and refuses every action it cannot justify —
on real data, not fixtures.

**Not established:** any edge, any fill rate, any profitability, any holding
period, and any capacity above zero. Class C stays FALSIFIED within its tested
scope; Class D stays LOCKED; Track P stays preserved as a negative result and
is not revisited — it was negative at zero fees, so no fee correction revives
it.
