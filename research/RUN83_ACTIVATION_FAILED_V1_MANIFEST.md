# RUN83_ACTIVATION_FAILED_V1 — IMMUTABLE SEAL MANIFEST

```
COHORT_ID                   = RUN83_ACTIVATION_FAILED_V1
SEALED_AT                   = 2026-09-12
SPEC                        = SNAPSHOT_CANONICAL_SPEC.md@v1 (serialization below)
RESEARCH_ECONOMICS_ELIGIBLE = FALSE
DISQUALIFICATION_REASON     = INSTRUMENT_CAPACITY_FAILURE
PRODUCTION_ROWS             = RETAINED. Not deleted. Deletion needs separate approval.
```

## 1. THE ACTIVATION

| | |
|---|---|
| deployed SHA | **`0321f4c`** |
| env set (no effect — env changes do not redeploy) | 2026-09-12T15:42:22.237Z |
| restart (no effect — a restart reuses the deployed env snapshot) | 15:49:27.607Z |
| effective activation deploy dispatched | 15:55:50.825Z |
| **worker availability / collection start** | **2026-09-12T15:56:25+00:00** |
| first collector row (`rn1_obs_clock_sync`) | 15:56:37.841309Z |
| first event `receipt_wall` | **2026-09-12 15:56:37.846450+00** |
| last event `receipt_wall` | **2026-09-12 16:09:02.317695+00** |
| last row written (`rn1_obs_snapshots.row_written_at`) | 2026-09-12 16:09:58.325064+00 |
| disable env-set | 16:06:17.434Z |
| disabling redeploy dispatched | ~16:07:35Z |
| collection span | 744.47 s (12 min 24 s) |
| `process_boot_id` (1 distinct) | **`951da7a0-c5e6-44ef-a314-c103c3d1c51f`** |

## 2. CONFIGURATION IN FORCE

```
RN1_OBSERVABILITY_SHADOW        = true
RN1_OBSERVABILITY_RPS           = 2.0      <- NOT the approved 3.0
RN1_OBSERVABILITY_MAX_INFLIGHT  = 8        (code default)
RN1_OBSERVABILITY_SUBJECT_WHALE_ID = (did not exist in V1)
CLOB_BASE_URL                   = https://clob.polymarket.com (code default)
COLLECTOR_VERSION               = rn1-obs/1
preregistration_version         = RUN83_PREREG_V1 (implicit; V1 stamped none)
mirror_live                     = false throughout
```

`RN1_OBSERVABILITY_RPS` was set to 2.0 rather than the approved 3.0 because
pre-activation check #6 could not be satisfied: no documented venue limit was
reachable, no 429 against `clob.polymarket.com` is recorded anywhere in the
repository, and the only "≈3 req/s" figure in the codebase traces by
`git log -S` to run 83's own commits. Reported as
**`RPS_LIMIT_NOT_ESTABLISHED`**.

## 3. CANONICAL ROW COUNTS

| table | rows |
|---|---|
| `rn1_obs_events` | **12,535** |
| `rn1_obs_snapshots` | **125,270** |
| `rn1_obs_transitions` | **25,062** |
| `rn1_obs_clock_sync` | **3** |

Storage measured at seal time: **82,493,440 bytes** across the four tables and
their indexes (43 MB snapshots, 10,072 kB events, 5,408 kB transitions, 48 kB
clock_sync, 19,272 kB indexes/sequences) = **6,581 bytes per observed event**.

## 4. CANONICAL SERIALIZATION SPECIFICATION

Each digest is `SHA-256` over a UTF-8 byte string built as follows. The
statements that produce them are `research/run831_freshness_and_seal.sql`
statements 7 and 8; they are reproducible from the sealed rows without this
document.

```
EVENT_IDENTITY_DIGEST
    fields   : source_event_id
    order by : source_event_id           (ascending, C collation)
    joiner   : "\n" between records, none trailing
    encoding : UTF-8, no BOM
    hash     : sha256(convert_to(string_agg(...), 'UTF8'))

SNAPSHOT_IDENTITY_DIGEST
    fields   : obs_event_id::text || '|' || offset_label || '|' || status
    order by : obs_event_id::text, offset_label
    joiner   : "\n"

TRANSITION_IDENTITY_DIGEST
    fields   : obs_event_id::text || '|' || stage || '|' || state
    order by : obs_event_id::text, stage, state
    joiner   : "\n"

CLOCK_SYNC_IDENTITY_DIGEST
    fields   : sync_id::text || '|' || COALESCE(host_sync_status, 'NULL')
    order by : sync_id
    joiner   : "\n"
```

Field separator is `|` (U+007C). It cannot occur in a UUID, an integer, an
offset label from the pre-registered ladder, or any value of the `status` /
`stage` / `state` CHECK constraints, so no combination of values can produce the
same joined string as a different combination.

## 5. UNCOMPRESSED CANONICAL HASHES

```
EVENT_IDENTITY_DIGEST       sha256 131ad1ef28271df7083ca2eff8f3ca1724a34929051689a9ca7e99c4b5b7358a
SNAPSHOT_IDENTITY_DIGEST    sha256 41b2a3fb5c878493f0f080a1138dd6c2341f0d0f95f0d593dfd4f7e498c22331
TRANSITION_IDENTITY_DIGEST  sha256 4609219ab124cd5ea952b017f698b5137cef59900a294d2712fc84f3e3aea9fb
CLOCK_SYNC_IDENTITY_DIGEST  sha256 5758e485863d8b5caf44891827d998ad65934bcaad2fb2c186ff15a9dfa31e40
```

Drawn 2026-09-12T16:38:53Z from `sportsassets-db`
(`dpg-d9gcudurnols73ce4avg-a`), source file sha256
`05ac666df2167e26b0e1bf3cf2aa5fa273ce4cb36395cc842707d8d991f32371`.

## 6. WHY THE COHORT IS DISQUALIFIED

| | |
|---|---|
| captured | **337 of 125,270 scheduled (0.27%)** |
| captured at 0 / 100 / 250 / 500 ms / 1 s / 2 s | **0 at every one of the six** |
| captured at 60 s | 315 of 337 |
| actual venue reads | 381 → **0.476/s against a 2.0/s ceiling (the pacer was idle)** |
| `MISSED_WINDOW` | 124,345 (99.26%) |
| `SKIPPED_PACING` | 544 (0.43%) |
| `VENUE_ERROR http_404` | 44 |

Two independent causes, either of which alone left a broken instrument:

1. **No population filter.** 11,867 of 12,535 events (94.671%) belonged to the
   other nineteen wallets on the ingestion roster; RN1 was 668 (5.329%). 11,855
   of the poll lane's 12,125 events were fills the ledger had already held — up
   to **36.9 days** — re-presented by its page walk, each carrying a fresh
   receipt anchor.
2. **The semaphore was the scheduler.** One `Semaphore(max_inflight())` slot
   held per event for its whole 60 s horizon ⇒ capacity `8/60 = 0.133 events/s`
   against a measured RN1-only arrival rate of 0.187–0.255/s. **It failed even
   at the correct population.**

## 7. WHAT THE COHORT MAY NOT BE USED FOR

The 337 captures are **not** usable to estimate latency decay, and the
disqualification is stronger than scheduler selection alone:

- capture was selected by scheduler survival and window length (315 of 337 sit
  at the single offset whose window closes at 72 s, which is where the
  scheduler arrived);
- **94.6% of the cohort was not live flow**, so most captures are anchored to
  fills BETTOR first received days earlier — the anchor is not the quantity it
  claims to be;
- the share of the 337 that is both RN1 and live was deliberately not computed.
  The cohort is disqualified on either ground alone, and producing that number
  would only invite its use.

## 8. VERIFICATION

`research/run831_seal_verify.sql` re-derives all four digests by an
**independently formulated** expression (different aggregation construction,
same specification) and prints them beside the values above for byte comparison,
together with the row counts and the activation interval. A re-derivation that
disagrees means the production rows have changed since sealing — which the
append-only triggers on all four tables should make impossible.

Independent re-derivation from exported bytes, rather than in-database, requires
moving 137,870 rows out of the runner's log surface and is **not yet done**; it
is recorded here as an open item rather than claimed.
