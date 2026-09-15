# RUN 83 — FORWARD INSTRUMENTATION / SHADOW OBSERVABILITY

**Implemented and validated offline. Not deployed, not activated, collecting
nothing.** Every commit carries `[skip render]`, so nothing reaches production
until that is explicitly approved — and approval to deploy is still not approval
to collect.

`mirror_live=false`. `ai_trades` / TRUEEDGE untouched.

---

## 1. CURRENT-PATH TRACE (83A) — traced from production write sites

| # | stage | where it lives | timestamps that exist today | clock |
|---|---|---|---|---|
| 1 | arrival — chain | `ingestion/chain.py:1252-1290` `ChainListener.run` (websocket `eth_subscribe`) | `last_event_at` (heartbeat only, **not persisted**) | Python wall |
| 1 | arrival — poll | `ingestion/poller.py:216-226` `poll_wallet` (`GET /trades`) | `raw["timestamp"]` on each row | **remote venue** |
| 1 | arrival — s1 | `ingestion/s1_emitter.py` via `chain.py:759-763` `emitter_observe` | in-memory orphan marks only | Python wall |
| 2 | decode | `chain.py:422/533/570` decoders → `DecodedFill` (`chain.py:98`) | **none — `DecodedFill` has no timestamp field at all** | — |
| 2 | block time | `chain.py:628` `BlockTimestampCache.get` | block timestamp | **remote chain**, with a wall fallback |
| 3 | normalization | `ingestion/pipeline.py:41` `TradeEvent`; persisted at `:120` `ingest_trade_result` | `ts` (remote), `detected_at` (**Python wall**, `:128`), `enriched_at`, `venue_seen_at` (**Postgres `now()`**) | mixed |
| 4 | mapping | `live_executor.py:8641-8758` premap/`resolve_*`; mirror `mirror_shadow.py:923` `map_market` | **none** | — |
| 5 | eligibility | copy: `live_executor.maybe_execute:7957` (14 gates); mirror: `mirror_live._read_mode:4402`, `_global_guards:4449` | `reaction_s` on `live_orders` (**wall − remote**); queue wait is monotonic but **never persisted** | mixed |
| 6 | market-data request | `pmus.side_ask:673`, `mirror_live._bbo:4783` → `mirror_shadow._paced_bbo:1372` → `pmus.bbo_read:806`; depth `mirror_shadow._book_depth:1788` | `t0 = time.monotonic()` (`mirror_live.py:4847`) — **in-memory heartbeat only** | monotonic, unpersisted |
| 7 | market-data response | `_Reading` (`mirror_live.py:6489`) | **`_Reading` has no timestamp field** | — |
| 8 | strategy decision | `analytics/mirror.py:260` `target_shares`, `:927` `plan` | `plan["maker"]["at"]` — Python wall, inside `last_plan` JSON, **overwritten every tick** | Python wall |
| 9 | last point before egress | copy `live_executor.py:9465`; mirror `mirror_live._place_reserved:13243` | `t_send`/`t_reply` in `live_orders.raw` (wall); `placed_at` = Postgres `now()` and **re-stamped on reclaim** (`:8487`) | mixed, **mutable** |

**Three findings that decide the design:**

1. **No venue supplies a timestamp or a sequence number on ANY book/BBO/depth
   response this repository reads** — not the CLOB book, not `pmus.bbo_read`,
   not `_book_depth`. There is no venue-side clock on the market-data path to
   synchronise against.
2. **No monotonic clock is persisted anywhere.** `time.monotonic()` appears only
   in in-memory counters.
3. **`placed_at` is mutable** (re-stamped on reclaim) and `last_plan` is
   overwritten every tick. Timing that can be rewritten cannot be reconstructed.

## 2. CLOCK-DOMAIN TABLE (83B)

| column | lane | clock | site |
|---|---|---|---|
| `trades.ts` | chain | remote block timestamp **with a wall fallback** | `chain.py:642` |
| `trades.ts` | poll | remote Data-API timestamp | `poller.py:107` |
| `trades.ts` | s1 | remote block timestamp, hash-verified, no fallback | `s1_emitter.py:1081` |
| `trades.detected_at` | all | **Python wall** | `pipeline.py:128` |
| `copy_probes.probe_at` | all | **Python wall**, same process | `copy_probe.py:110` |
| `copy_probes.reaction_s` | all | **wall − remote** (cross-domain) | `copy_probe.py:135` |

**Two families, enforced in code** (`obs/clock.py`): `*_wall` for correlation and
audit; `*_monotonic` for durations. Every monotonic reading carries a
`process_boot_id`, and `elapsed_between()` **raises** rather than subtracting
across a restart — where the monotonic origin has moved and the difference would
be a plausible number with no meaning.

**`SourceTimestamp` makes silent substitution impossible.** `MISSING` must carry
`None`; `None` must be declared `MISSING`; a substituted local clock must declare
`FALLBACK_SUBSTITUTED`. Enforced in `__post_init__` *and* by a `CHECK` constraint
in migration 062.

**`chain.py:642` now declares its fallback.** The expression
`result.get("timestamp", hex(int(time.time())))` put our own wall clock into
`trades.ts` with nothing recording it. **The value is unchanged**; a ledger,
a counter and a warning line are new, and `was_fallback(block)` lets the record
store `FALLBACK_SUBSTITUTED` instead of passing it off as chain time.

## 3–4. SCHEMA AND CODE CHANGES

**New — migration `062_rn1_observability.sql`** (next after 061): four
append-only tables — `rn1_obs_events`, `rn1_obs_snapshots`,
`rn1_obs_transitions`, `rn1_obs_clock_sync`. Append-only is a **trigger that
raises on UPDATE and DELETE**, not a convention. Unique keys make replay a no-op:
`source_event_id` on events, `(event, offset)` on snapshots. There is deliberately
**no fill-probability column** — it cannot be measured from a book snapshot, and
a fabricated one would be indistinguishable from a measured one once stored.

**New package `sportsassets/obs/`** — `clock.py`, `config.py`, `schedule.py`,
`book.py`, `record.py`, `collector.py`. **New worker**
`workers/rn1_observability.py`, registered in `workers/all.py` *immediately
before* the memory watch (the watch stays last on purpose — it wants its first
RSS reading after everything else boots).

**Changed (additive only):**
- `ingestion/pipeline.py` — `_obs_receipt(ev)` as the **first statement** of
  `ingest_trade_result`. The receipt instant must be stamped at arrival;
  stamping it after the pool round-trip would measure the pool. It is
  synchronous, does no I/O, and cannot raise into the caller.
- `ingestion/pipeline.py` — `TradeEvent` gains `ts_provenance` / `ts_fallback`,
  both defaulted, **not** part of `dedupe_key` and **not** written to `trades`.
- `ingestion/chain.py` — the fallback ledger above; both `TradeEvent` sites
  declare provenance.
- `tests/test_e26_chain_sweep.py` — two source-hash pins re-pinned, with the
  reason recorded beside each.

**The book is read by plain HTTP GET, not the venue SDK.** The SDK object that
reads a book is the same object the egress functions hang off; a read-only GET
cannot reach one. It also reads **the same endpoint U2 was built from**, so the
forward curve and the historical measurement are the same quantity.

### That channel is named, and the name is a limit, not a boast

Every snapshot row carries `observation_channel`, `transport` and
`feed_identity`, and the migration **refuses an unnamed channel** rather than
defaulting one.

```
LEGACY_COMPARABLE_BOOK_PATH   the raw CLOB /book HTTP read
```

It is here **because it is comparable to U2** — the population 81A, 81B and 82
all measured came from this endpoint's ladders, so a forward curve read this way
is the *same quantity* as the historical one.

**It must not be read as `FASTEST_AVAILABLE_BOOK_PATH`, and it must not be read
as `EARLIEST_ACTIONABLE_MARKET_STATE`.** Those are three different claims and
only the first is supported. `FAST_STREAM_PATH` / `websocket_stream` are
**declared in the schema and in the code constants so a second channel can be
added later and compared against this one rather than pooled with it** — an
average over two channels belongs to neither. **Nothing implements a stream
channel and nothing activates one in run 83**, and a test asserts the obs package
contains no websocket code.

Each snapshot records a request/response pair **or** a stream receive, never both
invented: a polled channel fills the first, a pushed channel the second, and
`actual_offset_s` is measured from whichever one actually carried the data.

## 5. SAFETY PROOF (83F)

> **THE OBSERVABILITY SHADOW HAS NO CODE PATH THAT SUBMITS, MODIFIES, CANCELS OR
> REPLACES A REAL ORDER.**

**Statically** — `tests/test_obs_safety.py` walks the package's transitive
first-party import graph and fails unless every module reached is on a reviewed
**allow-list**. An allow-list, not a deny-list of order modules: a deny-list goes
stale the day someone adds a new egress; an allow-list fails the moment the
collector reaches anything nobody reviewed. Today the closure is
`obs.*` + `sportsassets.db`. A name-pattern check and a source-text check back
it up.

**Dynamically** — the package is imported with every order-shaped callable in the
tree replaced by a tripwire that fails the run if called.

**Inert by default** — `RN1_OBSERVABILITY_SHADOW` unset ⇒ `main()` logs one line
and returns. No value in `obs/config.py` is read by the trading path, so no
failure in the instrumentation can change what the trading path does
(acceptance 12).

## 6. TEST RESULTS

```
tests/test_obs_safety.py              4 passed
tests/test_obs_clock_and_schedule.py 16 passed
tests/test_obs_collector.py          21 passed
tests/test_obs_config_independence.py 20 passed
                                     61 passed
```

**Full backend suite: 42 failed / 7,634 passed / 99 skipped / 3 xfailed (5m58s).**
None of the 42 is run 83's. Attributed rather than assumed, by running the three
affected files at the **pre-run-83 commit `8dd4e6e`** in a separate worktree and
at `HEAD`, in isolation:

| | `8dd4e6e` (before run 83) | `HEAD` (after) |
|---|---|---|
| `test_mirror_live_worker.py` + `test_pnl_l34_review_pins.py` + `test_pmus_account.py` | **1 failed / 389 passed** | **1 failed / 389 passed** |

Identical, and the single failure is
`test_pmus_account::test_a_position_we_sold_is_settled_on_the_day_of_the_sale` —
the known date-dependent pin (task #90), which fails on the clean tip too. The
other 13 named failures pass in isolation at **both** commits, so they are the
order- and load-dependent class already recorded as task #124 (the same tree has
given 183 and 112 failures on different runs) and task #89. Run 83 adds none of
them.

The targeted regression that *does* cover the changed code — memory_watch,
boot_stagger, pipeline, chain, e26, dedupe, obs — is **264 passed / 9 skipped**.

| # | acceptance condition | proved by |
|---|---|---|
| 1 | `mirror_live=false` throughout | `test_the_collector_never_reads_or_writes_mirror_live` (AST, not prose) |
| 2 | zero real order API calls | `test_obs_safety.py` — static allow-list + dynamic tripwire |
| 3 | source event ids survive end to end | `test_the_source_event_id_survives_end_to_end` |
| 4 | no silent source timestamp fallback | `SourceTimestamp` invariants + `chain.py` fallback ledger |
| 5 | monotonic non-decreasing per event | clock test + `test_the_forward_offsets_are_all_captured` |
| 6 | explicit causal ids join the stages | `test_every_row_is_joined_by_an_explicit_id` |
| 7 | immediate book snapshot captured | `test_the_immediate_snapshot_is_captured` |
| 8 | forward snapshots captured in a test | `test_the_forward_offsets_are_all_captured` |
| 9 | depth arrays retain exact values | `test_the_depth_ladder_is_stored_exactly` |
| 10 | restart/replay does not duplicate | `test_a_replayed_event_is_not_a_second_observation` |
| 11 | append-only semantics hold | writer SQL inspected + migration triggers asserted |
| 12 | instrumentation failure cannot enable trading | `test_observe_swallows_every_failure`, queue-full drop test, `test_instrumentation_failure_cannot_enable_trading` |
| 13 | **observability runs with trading OFF** (fail closed on a coupling) | `test_the_collector_starts_with_trading_off`, `test_the_shadow_flag_is_the_only_input_to_shadow_enabled` |
| 14 | **the shadow flag cannot mutate `mirror_live`** | `test_setting_the_shadow_flag_does_not_change_any_trading_switch` (behavioural) + `test_no_observability_module_can_write_a_trading_switch` (AST) + 12 per-switch read checks |
| 15 | **no deletion of forward observations** | `test_the_retention_worker_cannot_reach_the_observability_tables`, `test_the_migration_forbids_deletes_not_just_updates` |

**A defect the sample event caught.** `source_token_id` is an `Observation`
attribute, not one of `fields`, so it reached the book reader and never the event
row. Every other test exercises the venue read rather than the stored identity,
so nothing else would have seen it. Fixed, with a regression test.

## 7. SAMPLE SHADOW EVENT

`research/RUN83_SAMPLE_EVENT.txt` — **produced by running the collector**, not
hand-written: fake venue, fake pool, offsets shortened to 0/100/250 ms, the ask
walking half a cent per read so the curve visibly moves. Note in it:
`venue_snapshot_ts` and `venue_sequence` are `null`, honestly — the venue
supplies neither.

## 8. PROSPECTIVE ANALYSIS PRE-REGISTRATION

`research/RUN83_PREREGISTRATION.md`, written and frozen **before** any
observation is collected. Primary question, primary scenario `Q_A`, the ten
offsets, five endpoints, the pre-specified handling of a zero or negative
denominator, and three named outcomes that would each falsify a different reading
— written down in advance so the observed one cannot later be described as
predicted.

## 9. REMAINING LIMITATIONS

1. **83I — `SOURCE_TO_RECEIPT_LATENCY = NOT IDENTIFIED`, and run 83 does not
   change that.** See §9A below; the earlier wording in this file overstated it
   and is corrected there.

2. **The receipt anchor is arrival at `ingest_trade_result`, not at the socket.**
   For the chain lane the decode and the block-timestamp RPC happen *before* it,
   so that interval is inside the anchor rather than measured. Moving the stamp
   into `chain._handle_log` is a further change and is not in this run.
3. **A book snapshot is not a fill.** No queue position, no partial fills, no
   cancellation, no market impact of our own. The curve is executable price *as
   observed*.
4. **Depth beyond what the endpoint returns is not identified**, exactly as in
   81B — `DEPTH_EXHAUSTED` is `NOT IDENTIFIED`, never extrapolated.
5. **The collector is paced and capped** (default 2 reads/s, 8 events in flight)
   and a full queue **drops and counts**. An instrument that degrades what it
   measures is worse than none — but it means coverage is not guaranteed to be
   100% of events, and the drop counters must be read beside any result.
6. **Fees and rebates remain `NOT IDENTIFIED`.** Nothing here measures them.
7. **`mirror_shadow` and `price_path` already exist** and overlap in spirit.
   Nothing was merged into them: `price_path` keys off `live_orders`, which is
   empty while trading is paused, and anchors at `placed_at` with 30 s+ offsets.

---

## WHAT HAPPENS NEXT — NOTHING, WITHOUT A WORD FROM YOU

Nothing is deployed. Nothing is collecting. To proceed there are two separate
decisions, and the first does not imply the second:

1. **Deploy** the code (drop `[skip render]` / push). This restarts the workers,
   which are currently running exits on open positions. Migration 062 creates
   four empty tables at the API's next boot.
2. **Activate** collection — set `RN1_OBSERVABILITY_SHADOW=true`. Only then does
   anything get recorded, and it still places no orders.


---

## 9A. THE SOURCE-CLOCK BOUND, PER LANE (corrected)

> **Withdrawn.** An earlier version of this file said *"with the clock-sync table
> populated on an NTP-disciplined host, BOUNDED becomes reachable for chain/s1."*
> **That is not automatically true and it is retracted.** NTP discipline
> constrains **BETTOR's local wall clock and nothing else**. It says nothing
> about the offset or error of the *remote* clock that produced the source
> timestamp. Bounding one end of a cross-domain interval does not bound the
> interval.

**The standard, stated once.** `SOURCE_TO_RECEIPT_LATENCY = BOUNDED` requires a
defensible uncertainty interval spanning **both** clock domains:

```
uncertainty = (BETTOR clock error, measured)  +  (source clock error, established)
```

Both terms must be *evidenced*, not assumed. And one more condition that is easy
to skip: **the bound must be narrower than the quantity being claimed.** A
two-sided bound of ±5 s is honest and useless for an interval believed to be
around a second — it would be `BOUNDED` in name and `NOT IDENTIFIED` in effect,
and would be reported as the former, which is worse than reporting neither.

**Two timestamps rendering as UTC is not synchronisation.** No subtraction is
performed across domains because both formats parse.

### Per-lane: what evidence each would need

| lane | source timestamp | BETTOR-side term | SOURCE-side term — what is required | status |
|---|---|---|---|---|
| **A_S1** | Polygon block timestamp, hash-verified against block hash and parentHash, no wall fallback (`s1_emitter.py:1081`) | obtainable: per-event `adjtimex` offset + max error, recorded in `rn1_obs_clock_sync` | **A cited, protocol-enforced bound on how far a Bor block timestamp may deviate from true UTC** — the validity conditions validators actually reject a block for, not folklore. Monotonicity against the parent alone does not bound deviation from UTC. Not established in this repository or in this run. | **NOT IDENTIFIED** |
| **A_CHAIN** | same block timestamp, **plus** the substitution path at `chain.py:642` | same | everything A_S1 needs, **and** separability of substituted rows. Run 83 supplies the second half: `ts_fallback` now declares a substitution, so rows written after this lands are separable. **Rows written before it are not**, and no later work can separate them. | **NOT IDENTIFIED** |
| **A_POLL** | Data-API `raw["timestamp"]` (`poller.py:107`) | same | **(a)** authoritative documented semantics — whether the field is the venue's fill time, the block time it reports, or the API's own write time is **not established anywhere in this tree**; **(b)** a server-time endpoint on the same host with measured round-trip, which would give a ±RTT/2 offset bound. Neither exists. Compounded by `detected_at` having two indistinguishable writers (live poller, missed-fill reconciler). | **NOT IDENTIFIED** |
| **A_BACKFILL** | Data-API activity timestamp, same parser as poll | **not obtainable per row** — `detected_at` is stamped once per whale for an entire backfill (`history.py:91`) | everything poll needs, and migration `003_backfill_source.sql` retroactively relabels poll rows into this lane, so the lane is not even a single ingestion path | **NOT IDENTIFIED**, and structurally the furthest from it |

### Forms of evidence that would count

Any of these, for the source term, with the BETTOR term measured alongside:

- **authoritative source timestamp semantics** — documentation from the venue or
  protocol stating what instant the field denotes;
- **documented source clock synchronisation** — the source's own discipline and
  its stated error;
- **a server-time endpoint with measured RTT**, giving offset ∈ ±RTT/2. *Nothing
  this repository calls exposes one*, on any venue, on any path;
- **protocol timestamps with known semantics** — a field whose meaning is fixed
  by a specification rather than by convention;
- **block timestamp bounds where defensible** — i.e. where the consensus rules
  demonstrably reject a block outside a stated window, with that window cited;
- **another independently validated clock relationship** — e.g. a third source
  whose relationship to both ends is separately established.

### Until both ends are bounded

```
SOURCE_TO_RECEIPT_LATENCY = NOT IDENTIFIED
```

in every lane. Run 83 makes the **BETTOR-side term measurable** and makes
substitutions **declarable**. Neither of those is the source-side term, and
neither on its own moves any lane out of `NOT IDENTIFIED`.
