# IMMUTABLE SETTLEMENT SNAPSHOT — DESIGN FOR 81B

**PROPOSAL ONLY. NOT EXECUTED. NOTHING DISPATCHED.** Run 81B is not approved and
does not start. `mirror_live=false`. `ai_trades` / TRUEEDGE untouched.

---

## 1. THE ANSWER TO THE QUESTION YOU ASKED FIRST

> If exact historical-at-cutoff state cannot be reconstructed, say so explicitly.

**It cannot be reconstructed. Not approximately, not partially — at all.**

This is read from the schema, not inferred from the drift:

- `markets` (`backend/migrations/001_init.sql:27`) has **one row per condition and
  no history**. `condition_id` is the primary key; the ingest path upserts onto
  it. There is no version column, no valid-from/valid-to, no audit table, no
  append-only shadow anywhere in `backend/migrations/`.
- `markets.updated_at` is `DEFAULT now()` and is **overwritten on every upsert**
  (established in run 80). It records the last write, so it cannot bound an
  earlier state and cannot even tell you how many writes preceded it.
- `resolved_at` is not an arrival timestamp. `gamma.py` writes
  `COALESCE(<the venue's own closedTime/endDate>, now())`, so it carries **two
  semantics in one column** and is a property of the event, never of our
  knowledge of it.
- **No raw venue payload is retained anywhere**, so the value a previous upsert
  wrote is not recoverable from a stored response either.
- `market_tokens` (`001_init.sql:45`) — the token→outcome-index map that payout
  mapping depends on — is equally mutable: upserted, and
  `ON DELETE CASCADE` from `markets`.

So there is no artifact in the system from which the state of `markets` at
2026-09-12T00:00:00Z could be rebuilt. The one-event/$7.28 drift between runs
80.5 and 80.5b is not a bug to fix; it is this property becoming visible.

**Therefore the snapshot below freezes a state we can observe, not a state we can
reconstruct**, and its name says so.

---

## 2. WHAT THE SNAPSHOT MEANS — THE PERMANENT CAVEAT

The snapshot is, verbatim and in every artifact that carries it:

> **Settlement metadata state observed at SNAPSHOT_DRAWN_AT for events whose
> stored `resolved_at` satisfies the historical cutoff predicate.**

It is **NOT**:

> ~~what BETTOR knew at the historical cutoff~~

This distinction is permanent and is carried in the snapshot header, in the file
name, in every 81B statement's output, and in the findings. No 81B figure may be
described in point-in-time-knowledge language.

---

## 3. WHAT IS FROZEN

One row per condition, drawn from the real schema (no guessed fields):

| field | source | why 81B needs it |
|---|---|---|
| `condition_id` | `markets.condition_id` (PK) | the join key and the sort key |
| `resolved` | `markets.resolved` | the cohort predicate |
| `resolved_at` | `markets.resolved_at` | the cutoff predicate and the quarantine |
| `resolved_prices` | `markets.resolved_prices` (jsonb) | the payout vector |
| `slug` | `markets.slug` | diagnosis and readability only |
| `sport` | `markets.sport` | segmentation |

Plus one row per token, because payout mapping is `trades.asset` → outcome index
→ `resolved_prices[index]`, and that map lives in a second mutable table:

| field | source |
|---|---|
| `token_id` | `market_tokens.token_id` (PK) |
| `condition_id` | `market_tokens.condition_id` |
| `outcome` | `market_tokens.outcome` |
| `outcome_index` | `market_tokens.outcome_index` |

**Scope:** conditions carrying at least one U2 event at `AUDIT_CUTOFF_TS`, which
bounds the snapshot at **≤ 17,775 conditions** (run 80's U2 condition count) and,
at the usual two tokens per binary condition, roughly ≤ 40,000 token rows. Small
enough to commit; large enough to be the whole 81B universe.

**Header rows carried inside the snapshot itself:**

```
AUDIT_CUTOFF_TS    2026-09-12T00:00:00Z     (the historical predicate)
SNAPSHOT_DRAWN_AT  <the draw instant>       (when the state was observed)
SOURCE_COMMIT      <git sha of the drawing SQL>
CONDITION_ROWS     <n>
TOKEN_ROWS         <m>
CONTENT_SHA256     <hex>
```

---

## 4. HOW IT IS DRAWN — READ-ONLY, AND NOT BY QUERYING `markets` AGAIN

> Do NOT simply query the current markets table again and call the result fixed.

Agreed, and the design does not. The difference is that the result is
**extracted from the database exactly once, hashed, and then committed**, after
which every 81B statement reads the committed bytes and **never touches
`markets` or `market_tokens` again**. A second query of a mutable table is not a
snapshot; a committed, hash-pinned extract of one is.

Mechanism, in three steps:

**(a) Draw.** A new workflow `research-snapshot.yml` — read-only, same rails as
`research-sql.yml` (`default_transaction_read_only=on`, `ON_ERROR_STOP=1`, the
mutating-keyword guard, `\echo`-only meta-commands). It runs `psql --csv -f
research/gen/draw_settlement_snapshot.sql` and redirects stdout to a file. Plain
`SELECT` output — **no `COPY`, no `\copy`, no temp table, no write of any kind**,
so it passes the existing guards unchanged. This is a workflow-only addition and
needs your approval like any other.

**(b) Pin.** The drawing SQL computes its own content hash **in the database**,
in the same statement that produces the rows:

```
encode(sha256(convert_to(
  string_agg(row_text, E'\n' ORDER BY condition_id), 'UTF8')), 'hex')
```

Canonical rendering, fixed once: rows sorted by `condition_id` ascending; fields
tab-separated in the declared order; `NULL` rendered as the literal `\N`;
timestamps as `to_char(... , 'YYYY-MM-DD"T"HH24:MI:SS.USOF')`; `resolved_prices`
as Postgres' own `jsonb` text form (array order preserved). The hash is printed
to the run log **and** written into the artifact, so the two can be checked
against each other by anyone, later, without database access.

**(c) Commit.** The CSV is uploaded as a GitHub Actions artifact *and* converted
to `research/gen/settlement_snapshot.sql` — a single `VALUES` literal wrapped in
a CTE — which is committed to `claude/session-njaewf`. Committed bytes under git
are the actual immutability guarantee; the Actions artifact is the provenance
record showing where those bytes came from and when.

---

## 5. HOW 81B CONSUMES IT

Every 81B statement opens with the committed snapshot CTE and joins to it. The
words `markets` and `market_tokens` **do not appear in the 81B file at all** —
which is mechanically checkable, and I will add it to `check_sql.py` as a
file-scoped rule rather than relying on my own care.

Belt and braces, because the trade side is still read live: 81B runs in **one
repeatable-read transaction** (`psql -1` with
`default_transaction_isolation=repeatable read`), so `trades` and `copy_probes`
also present one MVCC snapshot to every statement in the file. That is an
isolation flag, not a write, and costs nothing.

Result: the settlement state is frozen *across runs* by the commit, and the trade
state is frozen *within a run* by the transaction. Re-running 81B on the same
commit reproduces every settlement figure exactly.

`CONSERVATIVE_SETTLEMENT_TIMING_QUARANTINE` is preserved unchanged and is applied
**against the snapshot**, not against live `markets`. It stays documented as a
timing-integrity quarantine and a sensitivity rule. Nothing in 81B implies the
quarantined payout vectors are invalid — run 80.5b found **zero** payout-vector
defects in either arm, and that finding is carried into 81B's output rather than
left in a prior document.

---

## 6. WHAT THE SNAPSHOT DOES **NOT** FIX

Stated now, not discovered later:

1. **It does not recover the historical state.** It freezes an observation, and
   the gap between the draw instant and the cutoff is unmeasurable in both size
   and direction. A condition resolved after the cutoff but stamped with a
   `resolved_at` before it enters the cohort and is indistinguishable from one
   that was already there.
2. **It does not make `resolved_at` mean one thing.** The two-semantics defect
   and the midnight mechanism survive the freeze intact.
3. **It does not verify a winner.** The payout vectors are structurally valid;
   whether the named winning outcome is the true one has no independent retained
   source. That UNKNOWN is frozen along with everything else, not resolved by
   freezing it.
4. **It does not supply a fee.** `IDENTIFIABLE_FEES` stays UNKNOWN.
5. **It gives no clock.** `CLOCK_UNRESOLVED` stands.

---

## 7. WHAT I NEED FROM YOU BEFORE ANY OF THIS RUNS

1. Approval to add `research-snapshot.yml` (workflow-only, read-only, `[skip
   render]`).
2. A decision on scope: bound the snapshot to U2-bearing conditions (≤ 17,775,
   my recommendation — it is exactly 81B's universe and keeps the committed file
   small), or take all resolved conditions RN1 traded (33,419).
3. Approval of the canonical rendering in §4(b), since changing it later changes
   every hash and breaks the pin.

None of this is dispatched. Run 81B does not start.
