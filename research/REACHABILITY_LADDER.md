# SETTLEMENT ANALYZABILITY FROM THE SEALED SNAPSHOT

> **REVISION — the STRONG quarantine is now in force.** The first version of
> this document used a U2-only timing witness, which was *weaker* than the
> approved predicate. `U0_SETTLEMENT_TIMING_WITNESS_V1` was drawn and sealed to
> close that gap. **The two rules select the identical set** — 209 conditions /
> 1,694 events / $303,303.16, incremental strong-only exclusions **zero** — so
> every ladder figure below is unchanged. The rule in force is now the approved
> one, and it is reproducible offline. See §G.

Observed at `U2_SNAPSHOT_V1_DRAWN_AT = 2026-09-12T02:53:56.653273Z`.

Produced by `research/gen/reachability_ladder.py` from two committed files and
nothing else — `u2_events_v1.jsonl.gz` and `settlement_v1.jsonl`. **No database.
No `markets`, `market_tokens`, `copy_probes`, `ai_trades` or TRUEEDGE.**

**This is not what BETTOR knew.** It is not the database state at
`AUDIT_CUTOFF_TS`, not settlement arrival, and no historical-cutoff language is
used about settlement metadata anywhere. **Structural well-formedness is not
correctness**: a payout vector that is an array of the right length summing to
one is well-formed; whether the outcome it names actually won has no independent
retained source and is never inferred.

**No economics.** No settlement-realized margin, no remaining margin, no
matched-vs-directional split, no fees, no P&L. Those are 81B, and 81B is not
approved.

---

## A. THE LADDER

| stage | events | conditions | source notional | % events | % notional | Δ events | Δ notional |
|---|---|---|---|---|---|---|---|
| U2_SNAPSHOT_V1 | 214,609 | 17,752 | $48,327,293.76 | 100.000 | 100.000 | — | — |
| LINKABLE_TO_CONDITION | 196,787 | 17,752 | $45,408,210.18 | 91.696 | 93.960 | −17,822 | −$2,919,083.58 |
| LINKABLE_TO_SETTLEMENT_METADATA | 192,824 | 16,943 | $44,941,381.00 | 89.849 | 92.994 | −3,963 | −$466,829.18 |
| STRUCTURALLY_ELIGIBLE | 192,824 | 16,943 | $44,941,381.00 | 89.849 | 92.994 | **0** | $0.00 |
| RESOLVED | 114,237 | 9,545 | $25,030,436.27 | 53.230 | 51.794 | **−78,587** | **−$19,910,944.73** |
| CONSERVATIVE_SETTLEMENT_TIMING_QUARANTINE_REMOVED | 112,543 | 9,336 | $24,727,133.10 | 52.441 | 51.166 | −1,694 | −$303,303.16 |
| **SETTLEMENT_ANALYZABLE** | **112,543** | **9,336** | **$24,727,133.10** | **52.441** | **51.166** | **0** | $0.00 |

**The binding constraint is `RESOLVED`, by an order of magnitude.** It removes
36.6% of events and 41.2% of notional — more than every other stage combined.
That is not a defect: it is markets that had not resolved **as of the snapshot
instant**. It is a property of when the snapshot was drawn, not a permanent fact
about those events, and a later snapshot would move it.

## B. FIRST-FAIL TABLE

Each dropped event is counted exactly once, at the first stage it fails.

| first-fail reason | events | source notional | % events | % notional |
|---|---|---|---|---|
| UNLINKED_CONDITION | 17,822 | $2,919,083.58 | 8.304 | 6.040 |
| NO_SETTLEMENT_METADATA | 3,963 | $466,829.18 | 1.847 | 0.966 |
| TOKEN_METADATA_MISSING | 0 | $0.00 | 0.000 | 0.000 |
| NOT_STRUCTURALLY_BINARY | 0 | $0.00 | 0.000 | 0.000 |
| UNRESOLVED | 78,587 | $19,910,944.73 | 36.619 | 41.200 |
| RESOLVED_PRICES_INVALID | 0 | $0.00 | 0.000 | 0.000 |
| TIMING_QUARANTINE | 1,694 | $303,303.16 | 0.789 | 0.628 |
| PAYOUT_MAPPING_AMBIGUOUS | 0 | $0.00 | 0.000 | 0.000 |
| **SETTLEMENT_ANALYZABLE** | **112,543** | **$24,727,133.10** | **52.441** | **51.166** |

**The four zero buckets are tested, not vacuous.** Each was evaluated against a
real population and returned zero:

| bucket | witnesses it was tested on |
|---|---|
| TOKEN_METADATA_MISSING / NOT_STRUCTURALLY_BINARY | 192,824 events / 16,943 conditions |
| RESOLVED_PRICES_INVALID | 114,237 events / 9,545 conditions |
| PAYOUT_MAPPING_AMBIGUOUS | 112,543 events / 9,336 conditions |

Every condition that reached the structural gate is a clean binary — exactly two
tokens, outcome indices `{0,1}` once each, both token ids present. Every
resolved one carries an array payout vector of matching length summing to 1.
Every surviving event's `asset` names exactly one token of its own condition.

## C. EVENT-COUNT CLOSURE

```
sum(first_fail) 102,066  +  analyzable 112,543  =  214,609
U2_SNAPSHOT_V1                                  =  214,609      CLOSES
```

## D. SOURCE-NOTIONAL CLOSURE

```
sum(first_fail) $23,600,160.66  +  analyzable $24,727,133.10  =  $48,327,293.76
U2_SNAPSHOT_V1                                                =  $48,327,293.76
gap $0.000001                                                    CLOSES
```

## E. CONDITION-COUNT TRANSITIONS

| stage | conditions | Δ |
|---|---|---|
| U2_SNAPSHOT_V1 | 17,752 | — |
| LINKABLE_TO_CONDITION | 17,752 | 0 |
| LINKABLE_TO_SETTLEMENT_METADATA | 16,943 | −809 |
| STRUCTURALLY_ELIGIBLE | 16,943 | 0 |
| RESOLVED | 9,545 | −7,398 |
| CONSERVATIVE_SETTLEMENT_TIMING_QUARANTINE_REMOVED | 9,336 | −209 |
| SETTLEMENT_ANALYZABLE | 9,336 | 0 |

Note the shape of the first step: **17,822 events drop without removing a single
condition.** The unlinked arm names no condition at all, so it cannot take one
with it — which is why the event and condition ladders must be read separately
and are never collapsed into one "coverage" number.

## F. EXACT DEFINITION OF `SETTLEMENT_ANALYZABLE`

An event is `SETTLEMENT_ANALYZABLE` iff **all** of the following hold against the
sealed snapshot, evaluated in this order:

1. `condition_id_effective IS NOT NULL` — either `trades.condition_id` was
   present (`CONDITION_DIRECT`), or it was absent and the asset mapped to exactly
   one condition in sealed token metadata (`CONDITION_RECOVERED_UNIQUE_TOKEN`).
   A `CONDITION_CONFLICT` keeps the original and is flagged, never repaired.
2. That condition has a sealed settlement row with `market_row_present = true`.
3. `token_metadata_present = true`, `token_count = 2`, the sealed token
   `outcome_index` values are exactly `{0, 1}` once each, and both `token_id`s
   are present.
4. `resolved = true`, `resolved_prices_type = 'array'`, `payout_len = token_count`,
   every payout element non-null, and the payouts sum to 1 within `1e-6`.
5. The condition is **not** quarantined: no retained event in the snapshot on
   that condition has `ts` later than the condition's sealed `resolved_at`.
6. The event's `asset` appears **exactly once** in that condition's sealed token
   list, that token's `outcome_index` is a valid index into the payout vector,
   and the vector has exactly one element equal to 1.

### One limit of this definition, stated rather than discovered later

- **Condition (6) requires a unique winning outcome.** *Unique-winner
  eligibility would reject a genuine 0.5/0.5 terminal vector; no such vector
  exists in the sealed analyzable population.* The rule is kept as implemented
  for this audit rather than redesigned around a zero-witness edge case.

The quarantine limit that stood here in the first version is **closed** — see
§G. Condition (5) now reads against the U0 witness, which is the approved
predicate.

---

## G. THE STRONG QUARANTINE

`U0_SETTLEMENT_TIMING_WITNESS_V1` is a sealed supplemental artifact whose only
purpose is to make the approved predicate reproducible offline. It **supplements
and does not replace** `U2_SNAPSHOT_V1`, which was not redrawn.

| | |
|---|---|
| `U0_WITNESS_DRAWN_AT` | 2026-09-12T11:57:18.899518Z |
| isolation (read from the server) | `repeatable read`, `transaction_read_only=on` |
| `U0_WITNESS_EVENT_COUNT` | **962,509** |
| `U0_EXPECTED_CONTROL` / delta | 962,509 / **0 — REPRODUCES** |
| `U0_WITNESS_CONDITION_COUNT` | 46,309 |
| `U0_WITNESS_NULL_CONDITION_ROWS` | 18,068 |
| guards | both zero |
| gzip determinism | `byte_identical_on_repeat` |
| `U0_WITNESS_UNCOMPRESSED_CANONICAL_SHA256` *(authoritative identity)* | `83ad6a7e8b0e1c33368f86471352a0d014fe4fc02ae8c712335c76234e9dd7a5` |
| `U0_WITNESS_COMPRESSED_TRANSPORT_SHA256` *(not an identity)* | `62b1d9cd62f3b2982f8d11e6db8bfc50d57876336b307f799c3315d86d327521` |
| `U0_WITNESS_MANIFEST_SHA256` | `d42d06a33b17ff590afee3230d67c203ae8eae21747ccca75d3bc0a2d0c8e8e5` |
| size | 163,544,050 B uncompressed / 9,734,327 B gzip (committed gzipped) |

**The U0 control reproduces exactly.** Run 80 measured 962,509 at 00:16Z and run
81A re-read it at 02:04Z; this witness, drawn at 11:57Z — nearly twelve hours
later — returns 962,509 with delta 0. The workflow would have refused to seal on
a mismatch.

### The comparison

| rule | conditions | U2 events | U2 source notional |
|---|---|---|---|
| `WEAK_U2_ONLY_QUARANTINE` | 209 | 1,694 | $303,303.16 |
| `STRONG_U0_WITNESS_QUARANTINE` | **209** | **1,694** | **$303,303.16** |
| `INCREMENTAL_STRONG_ONLY_EXCLUSIONS` | **0** | **0** | **$0.00** |

**The stronger rule adds nothing on this cohort.** Every condition the U0
witness catches was already caught by the U2 witness. The gap I flagged was real
as a possibility and is empirically empty here.

Read that precisely, and not as more than it is:

- It is **not** a proof that the two rules are equivalent in general. It is a
  measurement on the conditions this cohort can reach.
- **28,557 of the U0 witness's 46,309 conditions are not datable from sealed
  bytes at all** — they have no row in the settlement snapshot, because that
  snapshot's universe is derived from U2. They carry no U2 events, so they
  cannot affect the U2 cohort either way; but the stronger rule's reach over
  them is untested and is not claimed.
- Of the 17,752 conditions the snapshot does carry, only **9,545 have a
  `resolved_at`** at all — the rest cannot witness a timing anomaly because
  there is no resolution instant to compare against.

I am not offering a mechanism for why the incremental is zero. The figure is
measured; the cause is not tested.

---

## CROSS-CHECKS THAT PASSED

**The timing quarantine reproduces run 80.5 exactly.** Run 80.5 computed the
quarantine in SQL against live tables and got **1,694 events / $303,303.16 /
209 conditions**. This ladder, in Python against sealed bytes with no database,
gets **1,694 / $303,303.16 / 209**. Different implementation, different data
path, same answer to the cent.

**`NO_SETTLEMENT_METADATA` and the linkage census agree, and coherently.** The
3,963 events here are the same count as `direct_token_unmapped` in the sealed
linkage census. That is not a coincidence: `market_tokens` cascades on delete
from `markets`, so a condition with no `markets` row has no token rows either,
and its events fail both tests for one underlying reason.

## CARRIED FORWARD

**`UNLINKABLE_TO_CONDITION_WITH_RETAINED_METADATA`** — 17,822 events /
$2,919,083.58, all `CONDITION_TOKEN_UNMAPPED`. That label is exact: **within
retained token metadata**. It is not "impossible to link in principle" —
`market_tokens` as sealed simply does not carry these tokens.

**Direct validation of the token map**, from the sealed linkage census: 192,824
direct events where a token mapping exists; **192,824 / 192,824 agree, 0
conflict, 0 ambiguity**. That is the evidence supporting use of retained token
mapping wherever it is present.
