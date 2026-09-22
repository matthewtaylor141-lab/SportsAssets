# One integration run, and an economic verdict

Two deliverables, produced together: a production-equivalent BETTOR
observation system that passes end to end, and an auditable answer to
what BETTOR believes is mispriced and at what size.

Nothing here was deployed. Nothing was armed. No order exists, no
trading control moved, no credential was read, moved or printed.
Production remains on `fd39a0a9` with `obs-state = false` and
`live_trading_paused = true`.

---

# PART A — THE INTEGRATION RUN

## A1. The baseline, read from production

| | |
|---|---|
| `sportsassets-workers` running | **`fd39a0a9`**, live since 2026-09-22T08:03:58Z, `new_commit` |
| `sportsassets-api` running | `fd39a0a9` |
| tracked branch | `claude/session-njaewf` |
| worker entry point | `workers/all.py` → `("bettor_live", bettor_live_loop.main)`, called **with no arguments** |
| API entry point | `sh start.sh` → `python -m sportsassets.scripts.migrate` then uvicorn |
| `ingestion_state` | `key text NOT NULL`, `value jsonb NOT NULL` — **two columns** |
| `schema_migrations` | 91 applied, `001_init.sql` → `093_bettor_live_observation.sql` |
| `obs-state` | `false` |
| `live_trading_paused` | `true` |

Operator flags on the workers service, by value where they are
non-secret operational knobs: `BETTOR_LIVE_MAX_CONTRACTS`,
`BETTOR_PROBE_MAX_RPS`, `BETTOR_PROBE_CONCURRENCY`,
`BETTOR_FRAME_CAPTURE_N` — all present and consistent with the approved
limits. `PMUS_KEY_ID` and `PMUS_SECRET_KEY` are **present**; their
values were never read, printed, hashed or logged.

### Discrepancies against the repository — recorded, not silently fixed

1. **The migration set is not self-sufficient.**
   `031_us_premap_signed.sql` runs `ALTER TABLE us_premap ADD COLUMN`,
   and **no migration creates `us_premap`** — `workers/premap.py`
   `_ensure_table` does, at runtime. A database built from the migration
   set alone stops at migration 31. Production is unaffected (the worker
   created the table long ago). Left as a finding: this is a narrow
   repair, not licence to change the migration set.
2. **The image contains two copies of the package.**
   `pip install .` puts `sportsassets` in site-packages, and
   `COPY backend/sportsassets ./sportsassets` puts another at
   `/app/sportsassets`. Which one wins depends on `sys.path`.
   Production runs from `WORKDIR /app`, so `/app/sportsassets` wins and
   `MIGRATIONS_DIR` resolves to `/app/migrations`. Verified in the built
   image. A harness that runs a script from `/` gets the *other* copy
   and silently migrates nothing — which is exactly what happened on the
   first attempt here.
3. **Repository migration count matches production exactly**: 91 files,
   same first and last names. No drift.

## A2. The artifact under test

```
docker build -f backend/Dockerfile -t bettor-lifecycle .    # repo root
```

`backend/Dockerfile` is **unmodified** — the runner refuses to proceed
if `git diff` on it is non-empty. The base layer carries the session
proxy's CA so `pip` can resolve through this environment's transparent
TLS intercept; that adds one trusted root and changes no application
byte. Stated so it is not discovered later.

A disposable PostgreSQL is built **inside the image** by
`sportsassets.scripts.migrate` — the repository's real runner — and its
`ingestion_state` matches production column for column.

### Image identity, stated precisely

```
image id (final build)  sha256:5e3b4cfbe27c615ab2871af5a5792c6b8c6f8aeda5a811ae3880115d5bbd97af
RootFS layers           sha256:8680c8f321832365558f928c44d9037194207a70e4c362c1883a9466a32d4bae
                        sha256:d047c781a6d4b0d477f7b1ed05775e2a6d3d7f7d18a6351783042cc3087887bf
```

**A docker image ID is not reproducible across builds** — the image
config embeds a creation timestamp, so rebuilding identical content
yields a different ID. Two builds of this branch at different commits
produced different IDs (`f1a7ec4e…` and `5e3b4cfb…`) and **identical
RootFS layer digests**, which is the claim that actually matters: the
shipped filesystem did not change. The invariant to check before
deploying is therefore `git diff cf77a0f <tip> -- backend/` being empty
and `backend/Dockerfile` being unmodified, not an image ID match.

### The four fixtures, kept apart

| fixture | provenance |
|---|---|
| **BBO** | **RECORDED VERBATIM** — `/v1/markets/{slug}/bbo` HTTP 200 `marketData` |
| **BOOK** | **RECORDED VERBATIM** — `/v1/markets/{slug}/book` HTTP 200, paired to the BBO of the same market and segment |
| **LISTING** | **CONSTRUCTED** — no capture of the collection endpoint exists anywhere in the corpus |
| **WEBSOCKET** | **ABSENT** — see below |

Only the venue network boundary is replaced: `pmus._get_client`, and
`MarketStream._run` — one method. `ReplayStream` **subclasses the
production `MarketStream`**, so `book_at`, `_on_market_data`, the
freshness gates, epoch invalidation, `subscribe`, `prune` and the
capture path are production code. Records are labelled
`REPLAY_DECISION`, never `PROSPECTIVE_SHADOW`.

### Missing interface facts — named, not invented

**MIF-1. No recorded PMUS WebSocket frame exists.** The capture logs
hold `/bbo` and `/book` REST bodies only. The one WebSocket taxonomy in
the repository (`run836a_message_taxonomy.json`) is from the **global
Polymarket CLOB** — a different venue and a different protocol. The
socket path here is exercised by replaying recorded **REST `/book`**
bodies through the production `_on_market_data`, which is the same
ingest the socket calls. **That tests our handling. It does not
establish the venue's frame format, its sequencing, its
book-replacement semantics or its `transactTime` semantics.** All four
remain `NOT_ESTABLISHED` and only a live connection can settle them.

**MIF-2. No recorded PMUS `markets.list` response exists.** The listing
envelope is built to the SDK's declared `MarketDetail` fields. The SDK's
declared types have already been wrong once — `MarketBBO` declares its
fields flat and omits `state`, while the venue actually returns them
wrapped in `marketData` with `state` present. So the listing shape is
**declared, not observed**, and is a live-only uncertainty.

**MIF-3. Ferrari's raw fills are not on disk** and the extraction
workflow's matrix does not include the account. One line of config, not
a research problem.

## A3. The lifecycle — 115 checks, 0 failures

`scripts/bettor_lifecycle_run.sh`. Each phase is a separate
`docker run`, so the restart phases cross a **real process boundary**.

| | property | measured |
|---|---|---|
| **L1** | disabled startup makes no venue request | 0 listing, 0 BBO, 0 streams, allowance untouched |
| **L2** | one identity and deadline survive restart | identity, deadline and all three counters byte-identical across a new interpreter |
| **L3** | reservations precede every request | 1 listing / 35 distinct / 35 attempts; every reserved slug present in the row |
| **L4** | failures, retries, empty universe, crashes cannot replenish | 3 post-dispatch crashes; no counter ever moved backwards; ceilings held |
| **L5** | listing → enrichment → selection → stream → decisions | 28 subscribed, 644 frames ingested, **644 decisions persisted**, selection rule pinned |
| **L6** | missing/malformed fields produce explicit refusals | 6 named refusals, plus crossed book and empty body |
| **L7** | DB outage and lost acks fabricate nothing | table renamed under a running worker; 7 flush failures; 24 rows before and after |
| **L8** | stop and deadline end acquisition and close transport | transport closed **1.11 s** after `obs-stop`; zero further requests |
| **L9** | deadline survives failure to write the disarmed state | disarm blocked by a DB trigger; control stayed `true`; the database went on refusing |
| **L10** | restart recovers evidence and obligations | journal 160 and 8 outstanding settlements recovered |
| **L11** | resources, queues, retries, retention bounded | outbox 20,000; cursor cache 5,000; rings 2,000/5,000; retries 2 and 3; poll fixed at 30 s |

**Every result above is SIMULATED TRANSPORT.** It is not a live venue
connection and is not reported as one.

### Three times I tested my own stub instead of the production handler

Worth recording, because it is the same mistake that cost three probes.

`ctl.reserve` and `PgStore.flush` **already catch every exception and
fail closed.** Replacing either with a raising function — which the
first two revisions of L7 did — tests the harness's stub and skips the
code that matters. The phase now renames `ingestion_state`, and later
`bettor_live_journal`, out from under a **running** worker so the
failure happens at the server. It then measured something better than
expected: the **control read** refuses first (`CONTROL_UNREADABLE`),
which is an earlier and safer gate than the discovery gate I had
assumed.

### Two findings reported rather than papered over

- **L6-1** A BBO body with **no `state` field is not refused.**
  `assess()` refuses only when state is present and not OPEN; a missing
  state falls through to the listing's `active` flag. This is
  deliberate — the same function assesses listing rows, which carry
  `active` and no `state` — but it means a BBO that omits `state` is
  admitted on something the BBO did not confirm. **Not changed:**
  tightening it alters the frozen selection rule, and `state` is present
  on 30,590 of 30,590 recorded bodies, so it is unobserved in practice.
  Recommended as a separate, reviewable change.
- **L6-2** An **unparsable price reports `ONE_SIDED_BOOK`**, because
  `_f()` returns `None` for junk and the one-sided test runs first. The
  market *is* refused; only the reason code is wrong — and reason codes
  are what the counters group by. Misleading telemetry, no behavioural
  risk.

## A4. Regression check — the full suite, run on both sides

| | deployed `fd39a0a9` | branch `e3535e9` |
|---|---|---|
| failed | **446** | **446** |
| passed | 10,507 | **10,511** |
| skipped / xfailed | 134 / 3 | 134 / 3 |
| wall clock | 26m18s | 25m31s |

The failing **test ids** were compared, not the counts alone:

```
comm -23 branch prod   ->  0   (regressions)
comm -13 branch prod   ->  0   (newly fixed)
```

**Zero difference in either direction.** The branch passes four more
tests, which are exactly the four it adds. Zero of the 446 is in any
`bettor_live` file.

An earlier read of this same evidence ran only the 446 ids in isolation
and got 179 failures on both sides; that was a weaker check — running a
subset changes the order and the module-level state other tests leave
behind. The full-suite comparison above supersedes it and says the same
thing more strongly. The 179 pre-existing failures named there are a
subset of these 446, all in mirror/pmus/calibration/pnl modules, and are
out of scope for this run.

## A5. The probe, as one operation

`.github/workflows/bettor-probe.yml`. One dispatch does all of it:

1. **Preflight** — both services' running SHA against an exact expected
   commit; the four operator flags by value; credential **presence**
   only; `obs-state` stopped; `live_trading_paused` true. Nothing is
   armed until every line passes.
2. **Archive** the previous probe row to an artifact *before* `obs-arm`
   replaces it, and refuse to continue if the archive is empty.
3. **Arm once**, then **read the row back** — new identity ≠ old, all
   three counters 0, caps 40/160/18, deadline 1,800 s.
4. **Run and watch** the counters the worker actually moves.
5. **Stop** on useful evidence, the probe deadline, or a self-disarm;
   then verify acquisition halted within 58 s and the transport within
   90 s.
6. **Preserve** the armed row, final row, decision breakdown and summary
   as artifacts.

Structural guarantees: `obs-arm` appears **once**; `obs-stop` runs in an
`if: always()` step so a cancelled or failed run still disarms; a
`concurrency` group of one makes a second dispatch **wait** rather than
arm over the first; the run refuses to report success if the control is
not `false` or the trading pause moved.

---

# PART B — THE ECONOMICS

Full detail: `research/beta48/STRATEGY_EVIDENCE_MAP.md`.
Model: `research/beta48/bettor_policy_ev.py` (25 tests).
Test: `research/beta48/bettor_economic_test.py`.

## B1. One accounting model, and what it refuses to do

Every action — do nothing, quote, cancel, cross, cross a pair, hold,
reduce, complete, close — is priced by **one** function on the
**verified** PMUS schedule: `fee = Θ·C·p·(1−p)`, Θ_taker `+0.06`,
Θ_maker `−0.0125`, banker's rounding **per fill**.

Three inputs are not identified from any evidence we hold, and the model
**returns `NOT_IDENTIFIED` naming them** rather than guessing:
`P_FILL`, `E[settlement − quote | FILLED, state]`, `REBATE_ELIGIBILITY`.

The spread is never double-counted: a pair is priced as its two legs'
cash flows, and `COMPLETE_PAIR` subtracts the sunk first leg.

Rounding is not a detail at small size: a **1-contract** fill at p=0.445
earns a **$0.00** rebate; ten contracts earn **$0.03**; a thousand
one-lots earn **nothing at all** while one thousand-lot earns a real
credit.

## B2. The test, on a holdout that was not opened first

Policies and evaluation rules were written and frozen before the holdout
was read. The holdout is **788 two-sided OPEN observations across 8
markets**, pulled from the raw capture segments. The curated 400-pair
file is development data — the integration harness uses it — and is
reported separately.

### Class C, taker complementary pair — two claims, kept apart

An earlier version of this section ran them together. They are not the
same kind of statement and they do not have the same strength.

**(a) An arithmetic identity, about the same book.**
`ask + (1 − bid) = 1 + spread` holds for every two-sided book with a
non-negative spread, by algebra. No data, no fill model, no fair value,
and no sample size makes it more or less true. **Buying both sides of
one market at the touch cannot cost less than par**, and the PMUS taker
fee is charged on both legs on top of that. This part is settled.

**It does not generalise to other pair structures.** It says nothing
about the same outcome across two venues, two different markets on one
event, a maker leg with a taker completion, or any structure whose legs
are not the two sides of one book. Those are separate hypotheses.

**(b) An empirical claim, about this sample — and the sample is small.**

| | development | **holdout** |
|---|---|---|
| observations | 100 | 788 |
| **distinct markets** | — | **8** ← the real n |
| observations per market (min/med/max) | — | **2 / 21 / 383** |
| median basis | 1.0100 | 1.0050 |
| minimum basis | 1.0050 | 1.0050 |
| below par before fees | 0 | **0** |
| profitable after PMUS fees | 0 | **0** |
| median EV / contract | −0.0354 | −0.0325 |

**Eight markets, repeatedly sampled, one of them contributing 383 of the
788 rows.** The spread *distribution* — how far from par the pair
actually costs — rests on n = 8, not n = 788. The verdict `FALSIFIED`
is carried by (a), which needs no sample at all; (b) is consistent with
it and is not independent corroboration.

### Classes A and B, passive maker — NOT_IDENTIFIED

The policy returns **DO_NOTHING**, and says why: `P_FILL`,
`ADVERSE_SELECTION_GIVEN_FILL`, `REBATE_ELIGIBILITY`. A round trip whose
EV is unidentified counts as churn, not as a pass.

Measured on PMUS: gross maker half-spread **+0.0025/share** on the
holdout cohort; rebate at the median mid **+0.0029/share** at a 100 lot.
Unmeasured, and decisive: what a resting order earns against the flow
that meets it.

The one fill-observed dataset gives **−0.0090/share** (95% CI
[−0.0143, −0.0038], n = 9,337 conditions), and the PMUS rebate covers
**32.2%** of that. **That is a stress bound on one flow regime**, not
BETTOR's expected economics.

### A tick finding that changes quoting

**493 of 788** holdout observations are quoted on a **half-cent grid**
(0.600 / 0.605), not a cent. A policy assuming a 1c tick quotes
**through the touch** on 63% of this cohort. The model now reads
`market.orderPriceMinTickSize` per market.

## B3. $500,000/day — NOT SUPPORTED BY MEASURED OPPORTUNITY

$500k/day at the holdout's median mid is **1,398,601 shares/day**, or
**58,275 shares/hour**.

| | |
|---|---|
| market-wide volume (BLOCK_4, one market, 16 min) | **675 shares/hour** |
| market-wide volume (THROUGHPUT_V2, 6 markets, 7.18 market-hours) | **25 shares/hour** |
| addressable census | **2,204 markets** |

**Everything from here to the end of this section is a MODEL
ESTIMATE, not a measurement.** It assumes our share of the flow equals
our share of the displayed size at our price. That is not measured on
this venue and is wrong in at least two known directions: it ignores
**time priority** (we join the back of the queue, so early fills go to
older orders) and it ignores **cancellation** (the queue ahead can
evaporate without trading).

```
MODEL_QUEUE_SHARE = ASSUMED_PROPORTIONAL_NOT_MEASURED
MODEL_QUEUE_WAIT  = ASSUMED_FIFO_AT_MEASURED_VOLUME
```

A maker's *modelled* share of flow is its share of the queue:

| our display | share of flow | markets needed | vs 2,204 census |
|---|---|---|---|
| 100 shares | 0.45% | 19,336 | **≈9× the census** |
| 1,000 shares | 4.29% | 2,011 | 91% of every market, continuously |
| 5,000 shares | 18.32% | 471 | within, at 5,000 shares per market |

At the **lower measured volume**, a 1,000-share display needs **54,155
markets — 25× the whole census.**

And the queue does not clear: **22,297 shares displayed ahead**, **33.0
hours** to the front at the optimistic volume, and **zero touches in 16
minutes** the one time it was watched.

**Working capital — three components, and it is NOT_IDENTIFIED.**

An earlier note derived capital from a two-hour holding assumption. I
then replaced that with the *queue wait*, which was wrong in a second
way: queue wait is a model estimate, and waiting in a queue is not the
only thing that commits capital. Queue wait ends exactly where the
holding period begins — they are different quantities and neither
substitutes for the other.

| component | driver | status |
|---|---|---|
| **1. Inventory holding** | time from fill to exit or settlement | **NOT_IDENTIFIED** — no BETTOR fill has ever occurred, so no holding time has been observed |
| **2. Outstanding-order collateral** | capital locked by *resting* orders that have not filled | **NOT_IDENTIFIED** — whether PMUS locks collateral on a resting order, and at what haircut, is not documented in anything we have read back from the venue. For a widely-quoting maker this term could dominate the other two, and it is entirely unmeasured |
| **3. Release mechanics** | how fast capital returns on cancel, partial fill and settlement | **NOT_IDENTIFIED** — same-day vs next-day release changes required capital by the settlement cycle, independently of any trading decision |

```
WORKING_CAPITAL_FOR_500K_PER_DAY = NOT_IDENTIFIED
```

Two of three components have never been observed and the third needs a
venue fact we have not read back.

**The $41,667 figure is a SCENARIO, not an unconditional lower bound.**
I called it a lower bound twice, and that was wrong both times. It is
the value of component 1 alone — components 2 and 3 set to zero — under
**two stated conditions**:

1. an assumed **two-hour inventory holding period**, which has never
   been observed because BETTOR has never had a fill; and
2. a **turnover convention** in which $500,000/day of turnover is
   financed by capital recycling at that holding period
   (`$500,000 × 2h / 24h = $41,667`).

Change either condition and the number changes with it: a four-hour
hold doubles it, a twenty-minute hold cuts it to a sixth. It is
therefore a **lower bound only WITHIN those two conditions**, and
unconditionally it is neither a bound nor an estimate — it is one point
on a curve whose x-axis (holding time) is unmeasured.

```
WORKING_CAPITAL_FOR_500K_PER_DAY        = NOT_IDENTIFIED
CAPITAL | 2h hold, components 2,3 = 0   = $41,667   (a SCENARIO)
```

The honest form of the claim is: *if* the holding period were two
hours, *and* resting orders locked no collateral, *and* capital
returned instantly, *then* the requirement would be $41,667 — and since
two of those three are known to be false or unmeasured, the real figure
is higher by an unknown amount.

This is a statement about **measured opportunity**, not a proof of
impossibility. A venue with more volume, a larger addressable census, or
a queue we are early in would change every line of it. What it does
exclude is manufacturing the turnover by crossing the spread: Part B2
shows every crossed pair in the holdout loses.

## B4. The answer to the four questions

> **What does BETTOR believe is mispriced?**
> On today's evidence, **nothing it can demonstrate.** The one
> execution prize that is measurable on PMUS is the spread, and the term
> that decides whether a maker keeps it is unmeasured.

> **Why should our chosen action earn money after all costs?**
> For Class C it should not, and does not — decided on a holdout with
> fees. For Classes A and B the question is **not answerable** with what
> we hold, and the model says so instead of producing a number.

> **What evidence supports that belief?**
> 788 holdout observations with fees charged; a verified fee schedule; a
> 33-hour measured queue; zero observed touches; and six whale accounts
> whose pair channel **changes sign three to three**.

> **At what size does it remain credible?**
> Class C: no size. Classes A and B: **no size is yet credible**,
> because no size can be priced. The capacity work says that even if the
> sign were favourable, $500k/day is not reachable from the measured
> opportunity on this venue.

## B5. The next capital decision, concretely

**No policy passes the economic evaluation, so no pilot is proposed.**
Weakening a criterion to produce one would be the failure mode this
programme exists to avoid.

### What the observation probe can and cannot settle

Stated before the next one is proposed, because the first probe has now
run and the distinction decided how to read its result.

**It CAN settle, given a qualifying universe and enough time:**

- **Liquidity** — displayed size, depth, how often a touch moves, how
  wide books really are across market families.
- **Latency** — venue response times, socket frame cadence, and the
  staleness of the book a decision would be made on.
- **Unconditional price movement** — the raw material for
  `E[SETTLEMENT − QUOTE | STATE]`: which states later move adversely,
  and the clean state frame that would *size* any eventual pilot.

  **OBSERVATION ALONE DOES NOT ESTABLISH THIS ESTIMAND.** Watching
  quotes produces the QUOTE side and the STATE side; it produces
  neither the SETTLEMENT side nor a valid estimate. Three further
  things are required and none of them is a by-product of streaming:

  1. **Matched authoritative outcomes.** Each observed state must be
     joined to the venue's own settlement for that market. The
     settlement ingest exists, but a join is not a measurement until
     the match rate and the unmatched residue are both reported.
  2. **Adequate coverage.** Enough distinct markets, across enough
     families and enough time, that the estimate is not a description
     of one evening's one market family. A bounded probe of a few
     markets cannot deliver this however many frames it receives.
  3. **A valid statistical evaluation.** Repeated observations of the
     same market are not independent draws; a held-out split, a
     clustered interval and a pre-declared horizon are what turn a
     sample mean into a claim. The Class C work already shows how far
     apart "788 observations" and "8 markets" can be.

  A probe therefore moves this estimand from *unstartable* to
  *started*. It does not settle it.

**It CANNOT settle, ever, by itself:**

- **Fill-conditioned maker EV.** The probe never rests an order, so it
  never observes a fill, so it cannot measure
  `E[SETTLEMENT − QUOTE | FILLED, STATE]` or `P(FILL | STATE, QUOTE)`.
  Removing state-selection bias leaves **fill-selection bias exactly
  where it was**.

Only BETTOR's own admitted fills identify the maker EV. That needs an
order path and capital — neither authorised, and neither justified until
the state frame exists.

What is proposed instead, in order:

1. **A second bounded observation probe**, with the sampling fix the
   first one's result calls for (see below). It is decision-only with
   zero order capability. It would deliver the first PMUS WebSocket
   evidence of any kind — MIF-1 — plus the real listing envelope
   (MIF-2) and the state frame that identifies object **A**. It does
   **not** identify maker EV, and it is not a step toward capital.
2. **Add `ferrarichampions2026` to the extraction workflow matrix** —
   one line, closes MIF-3, and makes the four-account comparison
   possible for the first time.
3. **Only then**, and only if the state frame shows states that do not
   move adversely, a tightly bounded maker pilot whose weakest
   assumption is `P_FILL` — with acceptance, rejection and scaling
   criteria fixed before it arms, and every fill and cash movement
   reconciled.

**A passing integration run establishes none of this.** It establishes
that the collector will do what it says, stop when told, and not spend
what it was not given. Venue connectivity, profitability and
institutional readiness are three separate questions, and this run
answers none of them.

---

# PART C — WHAT THE LIVE PROBE CHANGED

Probe `3c413436-68ba-4e50-b106-5d349b372567`, 2026-09-22, on `e8f616a`.
Verdict **INCOMPLETE**. Full evidence:
`research/beta48/acceptance/live_probe_3c413436/`.

## C1. A documented claim is overturned

The record said the rate limit was **NOT ESTABLISHED** — 65,980
responses, zero 429s, no `RateLimit-*` header, observed 0.030–0.285
req/s — with the caveat that the evidence was unauthenticated while the
worker is authenticated.

**That caveat was the whole story.** At **0.227 req/s**, inside the old
envelope, the authenticated worker took **seven HTTP 429s in 134
seconds** across six markets, with server-directed holds of 7–10 s.

```
PMUS_AUTHENTICATED_BBO_RATE_LIMIT = EXISTS_VALUE_NOT_IDENTIFIED
```

The approved pacing of 0.25 req/s was derived from the unauthenticated
corpus and **is too fast**. The limit's value, window and scope
(per-credential? per-IP? per-endpoint?) remain unmeasured — seven
observations in one window on one market family is not a
characterisation.

**This does not change the economic verdict.** That turns on
fill-conditioned adverse selection, not on polling. It does change the
denominator of any freshness or coverage claim a collector makes.

## C2. The selection rule, measured live

```
candidates=3000 probed=40 enriched=40
excluded: ONE_SIDED_BOOK 28, SPREAD_BELOW_MIN_TICKS 12, admitted 0
```

Every probed market returned a usable BBO — the 429s cost time, not
data. The two binding exclusions are a **one-sided book** (70%) and a
spread **under two ticks** (30%). Neither is "the market is empty": a
one-sided book has one side quoted, and a sub-two-tick spread is too
*tight* for the rule.

That second bucket connects to the tick finding. At the half-cent grid
that 493 of 788 holdout observations sit on, `MIN_SPREAD_TICKS = 2`
demands a **full cent** of spread, so a market quoted 0.600/0.605 is
refused for being one tick wide.

**Scope:** 40 markets, one moment, and the listing prefix delivered a
single family (`aachc-mlb-bavg-*-leader-*` — long-dated many-outcome
futures, close to the worst case for two-sided quoting). This is not a
measurement of the venue; it is a measurement of the head of one
listing.

## C3. The sampling fix the result calls for

The probe spent its entire 40-market allowance on one unrepresentative
slice because that is what the listing prefix handed it. A second probe
should **sample across the listing** rather than take its head, or
filter candidates by family **before** spending distinct slots.

Both change *what is probed*, not *what is admitted*, so neither touches
the frozen selection rule. That distinction is the point.

## C4. What did not move

The maker classes are still `NOT_IDENTIFIED`, for exactly the reason
stated in Part B: the probe rests no order, observes no fill, and so
cannot reach `E[SETTLEMENT − QUOTE | FILLED, STATE]`. No WebSocket
opened, so MIF-1 is still open. No decision was persisted, so the state
frame is still empty. Nothing in this run moves the capital decision.
