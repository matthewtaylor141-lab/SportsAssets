# CANONICAL SERIALIZATION SPECIFICATION — AUDIT SNAPSHOT V1

**Version: v1. Fixed before the draw. Never changed after.** Changing any rule
below changes every hash and breaks the pin, so a change means a new version
with a new name, not an edit to this one.

Two sealed components, drawn together in one transaction:

| component | file | contents |
|---|---|---|
| `U2_SNAPSHOT_V1` | `research/snapshots/u2_events_v1.jsonl` | one line per U2 event (trade × qualifying probe), **including the retained ask depth array** |
| `SETTLEMENT_SNAPSHOT_V1` | `research/snapshots/settlement_v1.jsonl` | one line per distinct condition in `U2_SNAPSHOT_V1` |
| manifest | `research/snapshots/manifest_v1.txt` | counts, hashes, provenance |

Extractor: `research/gen/draw_audit_snapshot.sql` (**one statement**).
Workflow: `.github/workflows/research-snapshot.yml` (read-only against the DB).

---

## 0. NAMING — PERMANENT, AND NOT INTERCHANGEABLE

**`RUN 81A ORIGINAL`** — a historical result calculated on the live U2
population that existed while run 81A executed (2026-09-12 02:04–02:10Z):
214,651 events, 17,755 conditions, $48,327,388.19 source notional. It is a valid
measurement of that population. **It is not byte-for-byte reproducible**: that
event set was never materialised, and `copy_probes` retention has deleted rows
oldest-first ever since. **That exact event set is NOT RECONSTRUCTIBLE.**

**`U2_SNAPSHOT_V1`** — the immutable evidence population observed at
`U2_SNAPSHOT_V1_DRAWN_AT`.

**These are never called identical, and the snapshot is never called "the frozen
Run-81A population."** The manifest prints the delta in events, conditions and
source notional against the three RUN 81A ORIGINAL controls above.

**`SETTLEMENT_SNAPSHOT_V1`** — settlement metadata state observed at
`U2_SNAPSHOT_V1_DRAWN_AT` for the condition universe **derived from
`U2_SNAPSHOT_V1`**. It is NOT what BETTOR knew at `AUDIT_CUTOFF_TS`, NOT the
database state at the cutoff, NOT settlement arrival, and NOT a verified
outcome. The historical-at-cutoff state is NOT RECONSTRUCTIBLE.

---

## 1. THE EVENT POPULATION AND THE PROBE-SELECTION RULE

```
U2_SNAPSHOT_V1
  = every (trade, copy_probes) PAIR satisfying, at
    AUDIT_CUTOFF_TS = 2026-09-12T00:00:00Z:

      trades.whale_id  = RN1
      trades.ts          <= cutoff
      trades.detected_at <= cutoff
      copy_probes.trade_id = trades.id
      copy_probes.whale_id = RN1
      copy_probes.probe_at <= cutoff
      copy_probes.book_ok
      copy_probes.error IS NULL
      copy_probes.depth IS NOT NULL
```

**THE PROBE-SELECTION RULE, PRESERVED VERBATIM FROM RUN 81A: there is none.**
81A wrote a plain `trades t JOIN copy_probes c ON c.trade_id = t.id` with no
`DISTINCT`, no `LATERAL … LIMIT 1`, and no aggregation. So a trade carrying *n*
qualifying probes contributed *n* rows, and **81A's 214,651 is a count of
(trade, probe) pairs, not of distinct trades.** Its `source_notional` of
$48,327,388.19 counts such a trade's notional *n* times for the same reason.

That rule is deterministic and is kept exactly. The snapshot is keyed on
`copy_probes.id` — the probe's own primary key (`005_copy_probes.sql:8`,
`BIGSERIAL PRIMARY KEY`) — so every row names **the exact retained probe row**,
never "some probe for this trade".

The multiplicity census is printed in the manifest and is not assumed:
`U2_TRADES_WITH_0_PROBES`, `U2_TRADES_WITH_1_PROBE`, `U2_TRADES_WITH_GT1_PROBE`,
`U2_MAX_PROBES_PER_TRADE`, `U2_ROWS_FROM_GT1_TRADES`.

## 2. THE CONDITION UNIVERSE IS DERIVED FROM THE EVENT ROWS

```
SETTLEMENT universe = DISTINCT condition_id FROM the SELECTED event rows
                      WHERE condition_id IS NOT NULL
```

It is **not** re-derived from live `copy_probes` after event selection — it is a
CTE reading the same selected rows inside the same statement, so the two
components cannot describe different populations.
`U2_EVENTS_WITH_NULL_CONDITION` counts events naming no condition; they cannot
belong to a condition universe and are reported, not dropped silently.

## 3. ABSENCE IS DATA

The universe is built first and the mutable tables are `LEFT JOIN`ed onto it:

| flag | meaning |
|---|---|
| `market_row_present` | a `markets` row existed at draw |
| `token_metadata_present` | ≥1 `market_tokens` row existed at draw |

Both are carried `false` rather than the condition vanishing. A production row
appearing after the draw can never enter later analysis: the condition is
already in the file, marked absent.

## 4. ATOMICITY

The extractor is **one SQL statement**, so the event selection, the derived
universe, `markets` and `market_tokens` are read from one transaction snapshot
by construction — there is no window between reads at all. The workflow also
runs it under `psql -1` with `default_transaction_isolation=repeatable read` and
`default_transaction_read_only=on`.

The isolation **actually in force** is read from the server
(`current_setting('transaction_isolation')`, `transaction_read_only`) into the
manifest, not asserted in prose. `U2_SNAPSHOT_V1_DRAWN_AT` is `now()` — the
transaction start instant — so it describes the same snapshot as every row.

## 5. CANONICAL RENDERING

| # | rule | fixed as |
|---|---|---|
| 1 | event row order | `ORDER BY probe_id` (bigint, the exact selected-probe discriminator and a unique key) |
| 2 | condition row order | `ORDER BY condition_id COLLATE "C"` — byte order, so the result cannot depend on the server's locale or collation version |
| 3 | timestamps | UTC, `to_char(t AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')` — fixed 6 fractional digits, trailing `Z` |
| 4 | NULL | JSON `null`, one representation, including for absent rows |
| 5 | booleans | JSON `true` / `false` |
| 6 | **all numerics** | **exact canonical text: `v::numeric::text`, rendered as a JSON string** |
| 7 | arrays | payouts in stored array order (= the outcome index the vector is indexed by); depth levels in stored array order; tokens `ORDER BY outcome_index NULLS LAST, token_id COLLATE "C"` |
| 8 | text | JSON string escaping produced by `json_build_object` |
| 9 | key order | explicit in `json_build_object`; **`jsonb` is deliberately not used for row objects** — its key order is internal (length, then bytes) and would be a rule this file does not control |

### Amendment to rule 6, made BEFORE the draw

The first draft of this spec specified fixed-scale payouts,
`to_char(v, 'FM9999990.000000')`, guarded by a round-trip losslessness check.
That is replaced, before anything was drawn, by **exact canonical numeric text**
(`::numeric::text`) applied uniformly to every numeric in both components.

Reasons, both of which matter here:

- **It is lossless by construction, not by assertion.** Postgres `numeric` has an
  exact decimal representation and never renders in scientific notation, so
  `v::numeric::text::numeric = v` always. The old fixed-scale rule could silently
  truncate a depth share carrying more than six decimals, and would have had to
  *refuse the whole draw* on a guard failure rather than record the evidence.
- **One rule for payouts and depth.** Two numeric rules in one artifact is a
  canonicalization rule waiting to be applied to the wrong column.

`GUARD_PAYOUT_ROUNDTRIP_LOSSY` is therefore removed rather than kept as a check
that is now trivially zero — a guard that cannot fail is the vacuity failure
this audit keeps finding, and it does not get to live in the tool built to stop
it. `GUARD_PAYOUT_NON_NUMBER` and `GUARD_DEPTH_NON_NUMBER` remain, because a
non-numeric element is a real thing the data could contain.

Numbers are rendered as **JSON strings**, not JSON numbers, so no consumer's
JSON parser can renormalise them (a `float` round-trip through most parsers is
not identity). Consumers cast on read.

### Fixed key order

Event object:
```
probe_id, trade_id, condition_id, asset, outcome, outcome_index, side,
ts, detected_at, source, sport, market_slug, event_slug,
size, price, notional,
probe_at, reaction_s, his_price, his_size, his_notional,
best_ask, best_ask_usd, book_ok, error,
depth_type, depth_levels, depth
```
`depth` is an array of 2-element arrays: `[price, shares]`, both canonical
numeric strings, in stored array order.

Condition object:
```
condition_id, market_row_present, market_slug, event_slug, sport, closed,
resolved, resolved_at, resolved_prices_type, payout_len, payouts,
token_metadata_present, token_count, tokens
```
Token object: `outcome_index, token_id, outcome`.

## 6. FILE FORMAT

`psql -A -t -X` — unaligned, tuples-only, no headers — so stdout is the artifact,
one line per row. Lines are tagged so one statement can emit all three
components and the workflow can split them without ambiguity:

```
#KEY<TAB>VALUE      manifest lines
E<TAB>{...}         one U2 event
C<TAB>{...}         one condition
```

The workflow strips the `E<TAB>` / `C<TAB>` tag when writing each `.jsonl`, so a
committed file contains only its own canonical payload lines.

## 7. HASHES — FOUR, PLUS A MANIFEST, NONE OVERSOLD

| name | computed by | covers |
|---|---|---|
| `U2_EVENT_DB_CANONICAL_SHA256` | the database, in the same statement | the event payload lines only, newline-joined, no trailing newline, in rule-1 order |
| `SETTLEMENT_DB_CANONICAL_SHA256` | the database, same statement | the condition payload lines only, same construction, rule-2 order |
| `U2_EVENT_COMMITTED_FILE_SHA256` | `sha256sum` after the draw | the whole committed `.jsonl` |
| `SETTLEMENT_COMMITTED_FILE_SHA256` | `sha256sum` after the draw | the whole committed `.jsonl` |
| `AUDIT_SNAPSHOT_MANIFEST_SHA256` | `sha256sum` after the draw | the whole `manifest_v1.txt`, which binds cutoff, drawn-at, isolation, counts, the four hashes above and the spec's own hash |

The manifest cannot contain its own hash, so `AUDIT_SNAPSHOT_MANIFEST_SHA256`
lives in `HASHES.txt`, the run log and the commit message — never inside the file
it describes.

**Reproducible offline from the committed bytes:**
```
grep -c ''                       u2_events_v1.jsonl      # row count
perl -0pe 's/\n\z//' u2_events_v1.jsonl   | sha256sum    # DB canonical hash
sha256sum u2_events_v1.jsonl                             # committed-file hash
sha256sum manifest_v1.txt                                # manifest hash
```

The workflow **re-derives each DB canonical hash from the bytes it actually
wrote** and fails on mismatch. A pin never checked against the bytes on disk is
not a pin.

### Compression

If a component is committed gzipped, `gzip -9 -n` is used (`-n` drops the name
and mtime, which are the non-deterministic parts). **The authoritative identity
is always the uncompressed canonical hash**; a `.gz` file's own sha256 is
recorded as a *transport* checksum only, because gzip output can differ between
implementations and versions. That distinction is stated wherever both appear —
a compressed hash is never presented as the evidence's identity.

## 8. GUARDS — inside the artifact, so they describe the same snapshot

The workflow publishes nothing if any is non-zero:

| guard | fires when |
|---|---|
| `GUARD_EVENT_COUNT_MISMATCH` | emitted event lines ≠ selected U2 rows |
| `GUARD_COND_COUNT_MISMATCH` | emitted condition lines ≠ distinct universe conditions |
| `GUARD_DEPTH_NOT_ARRAY` | a selected probe's `depth` is non-null but not a JSON array |
| `GUARD_DEPTH_MALFORMED` | a depth level is not a 2-element array |
| `GUARD_DEPTH_NON_NUMBER` | a depth price or size is not a JSON number |
| `GUARD_PAYOUT_NON_NUMBER` | a payout array element is not a JSON number |
| `GUARD_ROW_HAS_NEWLINE` | a rendered row contains newline, tab or carriage return, breaking one-line-per-row |

## 9. STRUCTURAL_BINARY_ELIGIBILITY

The snapshot carries **ingredients, not a verdict**, so a consumer derives the
flag and the derivation stays reviewable: `token_count`, the token list with
`outcome_index` and `outcome`, `payout_len`, the `payouts` array in outcome
order, `resolved_prices_type`, and the two presence flags. Everything run
80.5b's payout-integrity statement tested is derivable from these without
touching a live table.

## 10. HOW A SEALED ANALYSIS READS THIS — AND WHY IT IS NOT SQL

A sealed analysis **cannot be a psql script**. psql can load a local file only
through `\copy` (a meta-command the runner bars) or a temp table (which
`default_transaction_read_only=on` bars), so any SQL analysis would have to
reach back to the live tables — the exact thing sealing forbids.

So `RUN 81A-S` and, later, 81B read the committed `.jsonl` directly, **with no
database connection at all**. That is strictly stronger than a sealed-SQL guard:
there is no live table in scope to fall back to. The SQL sealed-input checker is
still extended to reject `markets`, `market_tokens` and `copy_probes` in any
`rn1_run81b*` file, so the weaker path is closed too if it is ever taken.
