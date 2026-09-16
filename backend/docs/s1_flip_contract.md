# S1 — the chain-source flip: contract and certification criteria

Status: **flag-off implementation in progress** (2026-08-27). This
document is the durable home of the flip contract; the scratchpad
copies died with five container re-provisions in one day.

## What S1 is

Path A (`ingestion/chain.py`) already emits `TradeEvent(source="chain")`
into the shared `ingest_trade_result` pipeline for every fill its
receipt reconstruction can produce, and those rows already reach the
executor at block latency (rn1: chain 10/10 in SPORTS-PROV). The gap is
the fill shapes `_handle_v3` cannot reconstruct:

- events 2..N of a tx (`_v3_seen` lets the first 0xd543adfd event claim
  the whole tx),
- multi-token receipts (bundles, mint-matched complement legs),
- mixed tok_in+tok_out receipts, missing-USDC-leg receipts.

Those are exactly the shapes `shadow_v2.decode_shadow_views` +
`classify_mints` decode per-event with integer-exact aggregate
tie-outs. S1 promotes that decode to a second live chain emitter for
precisely those fills, under the venue-shaped per-market policy (the
agg record for any market with an agg view; the exec set for markets
without one). Everything downstream — pipeline, dedupe, fan-out,
executor money gates — is byte-untouched.

Why it matters in dollars: the executor's staleness cap
(`LIVE_MAX_REACTION_S=90`, swisstony 15s) measures from the whale's
fill timestamp. Poll lag runs 130–200s p50, so the fills S1 covers
mostly die stale today; chain rows arrive at ~-1s reaction and pass.
TRUEEDGE prices the class at five figures of counterfactual edge per
whale-month (lat_cost).

## Hard constraints (violating any = the change is wrong)

- **C1 — money gates never weaken.** Nothing downstream of
  `ingest_trade_result` changes. The executor's one-copy-per-asset
  guard remains the final backstop against any double-ingest.
- **C2 — the instrument stays independent.** The shadow's observe path
  stays sync/zero-I/O/never-raises, and its counters keep measuring
  as if the emitter did not exist. The evidence stream that certifies
  the flip must not be contaminated by the flip. Emission is
  observed by its OWN counters, never by mutating shadow evidence.
- **C3 — no emission of untrustworthy records.** Dropped records,
  unproven mint transforms, gating-flagged groups, unresolved
  timestamps, stale replays: never emitted. When in doubt, don't
  emit — the poller carries the fill at poll latency, as today.
- **C4 — no key-divergent double emission vs `_handle_v3`.** If the
  receipt path claimed (tx, wallet), S1 does not emit its own view.
  Key-identical overlap is safe (dedupe collapses); key-divergent
  double emission is the catastrophic case and must be structurally
  excluded, not probabilistically.
- **C5 — armed only behind flag AND certification.** Env flag
  (default OFF) AND a runtime check of the shadow's persisted state:
  see criteria below. Flag without certification refuses to arm,
  loudly, every time it is evaluated.
- **C6 — dedupe-key fidelity.** Emitted (size, price, ts) must
  reproduce, through the production `make_dedupe_key`, exactly the
  key the venue's own poll row for that record would carry. Price
  uses the round() variant (proven by keyimpl=0 across the whole
  production window); ts is the resolved block timestamp.

## Certification criteria (the arm gate reads these from the shadow's
## persisted state row — the same numbers the probes print)

1. Window age ≥ 7 days (`window_start`), with zero GATING counter
   movement since the window started (div.*, key_impl_mismatch,
   agg_tieout_fail, mint_side_anomaly, per_exec_ambiguous, orphan_*,
   poll_uncovered_unexplained, ts_never_resolved_live).
2. `compared_execs` delta over the window ≥ 500.
3. `key_impl_mismatch` == 0 cumulative.
4. `sim_ven_residual_dup` == 0 cumulative (the venue-shaped policy has
   never once implied a double-ingest).
5. `ts_avail.gt60s` == 0 and `ts_never_resolved_live` == 0 over the
   window (timestamp resolution SLO — the key needs exact ts).
6. `agg_tieout_fail` == 0 cumulative.

Production evidence as of 2026-08-27 19:26 UTC: tie-outs 385/0,
keyimpl 0, ven residual 0 (per-exec 122, agg 35), ts gt60=0 never=0,
writer_conflict acquitted as deploy-quantized (+8/deploy, +0 in both
measured quiet windows).

### The window rule, corrected with the granularity verdict (2026-08-27)

As originally coded the window could never reach 7 days, for reasons
that are not decode wrongness:

- any commit-hash change reset it (every push, even docs-only);
- per-exec residuals reset it — but under the venue's PROVEN mixed
  granularity, per-exec residuals grow with every taker fill; they
  indict the per-exec POLICY (disqualified), not the decode;
- leading-policy flaps between the two pure policies (neither of
  which ships) reset it.

The corrected rule: the certification window resets on (a) GATING
movement, (b) sim_ven_residual_dup movement (the candidate's own
residual), (c) a change in the DECODER FINGERPRINT — a hash of the
decode-critical function sources (decode_shadow_views, classify_mints,
agg_tieout, rec_keys, rec_prices), so editing the decode resets the
clock but unrelated deploys do not — and (d) corrupt resets. Leading
flips and exec/agg residuals remain counted and rendered as
diagnostics but do not reset the window. This is an evolution the
original flip criteria anticipated ("granularity verdict" was itself
a criterion; the verdict is MIXED).

### Round-6 protocol upgrades (2026-08-28)

Ten confirmed kills (two CRITICAL) reshaped three subsystems:

- **Trip state is server-side only.** The flush's document write can
  no longer name `trips`/`trips_cleared` at all; a trip is one atomic
  jsonb union (`SQL_TRIP`, existing timestamp wins, `armed` forced
  false in the same statement, refused when the reason's tombstone is
  newer), and an operator clear (`POST /api/admin/s1-clear-trip`,
  `SQL_CLEAR`) removes exactly one reason and records a PER-REASON
  tombstone dict — a second clear can never forget the first, and a
  stale in-memory copy can never resurrect a cleared trip. Flush
  counters ship as deltas the server adds under the row lock.
- **Coverage means the fill, not the wallet.** The reconciler records
  `cov:<addr>` per wallet (feed exhausted, or the oldest venue ts the
  run reached); the sweep's covering run must start
  `RECON_VENUE_LAG_S` after detection and provably span the fill's
  own timestamp. The sweep judges per-wallet windows so a whale whose
  reconciler sweep keeps failing defers only its own rows. A trip is
  made durable BEFORE its row is stamped judged.
- **Post-finalize re-entry is structurally excluded.** Any s1 row for
  (tx, wallet, asset) that the current pending entry did not write
  forbids emission (`s1.abstain.s1_row_preexists`) — the deep-reorg
  re-add (new block ts, new key) and the straggler leg of an
  already-emitted tx both refuse; genuine bundle siblings written by
  this entry proceed via `ingested_keys`. Burn-in's `would_emit` is
  deduped by a process-level (tx, wallet, asset) LRU that survives
  entry pop. RPC economics: decode runs before timestamp resolution
  (a foreign tx costs zero RPC), block timestamps are cached per
  (block, hash), and the head poll fires only when the WS head feed
  has actually gone quiet.

## Non-goals

- S1 does not replace the poller. Poll remains the reconciliation
  source and the carrier for anything the decode refuses.
- S1 does not touch `_handle_v3`'s existing emissions.
- S1 is not an executor change; sizing/caps/staleness stay as they are
  (chain rows simply pass the existing staleness gate honestly).
