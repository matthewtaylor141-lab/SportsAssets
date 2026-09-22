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

### Class C, taker complementary pair — FALSIFIED

| | development | **holdout** |
|---|---|---|
| observations | 100 | **788** |
| median basis `ask + (1 − bid)` | 1.0100 | **1.0050** |
| minimum basis | 1.0050 | **1.0050** |
| below par before fees | 0 | **0** |
| profitable after PMUS fees | 0 | **0** |
| median EV / contract | −0.0354 | **−0.0325** |

`ask + (1 − bid) = 1 + spread` is an identity. No fill model, no fair
value. **Decided, and negative.**

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

A maker's share of flow is its share of the queue:

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

**Working capital, corrected.** An earlier note assumed a two-hour hold
and concluded capital was not the constraint. At the **measured 33-hour
queue time** a position cannot turn faster than it can be entered:

| holding | turns/day | working capital |
|---|---|---|
| 2.0 h (assumed) | 12.00 | $41,667 |
| **33.0 h (measured)** | **0.73** | **$688,179** |

**16.5× the figure the assumption gives.**

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

What is proposed instead, in order:

1. **The bounded observation probe** (already prepared, awaiting
   authorisation). It is decision-only with zero order capability. It
   delivers the first PMUS WebSocket evidence of any kind — MIF-1 — plus
   the real listing envelope (MIF-2) and the clean state frame that
   identifies object **A**. It does **not** identify maker EV, and it is
   not a step toward capital on its own.
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
