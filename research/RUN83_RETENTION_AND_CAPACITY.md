# RUN 83 — RETENTION AND CAPACITY

**The rule, up front:**

```
NO DELETION of run 83 forward observations until the first analysis cohort
has been explicitly sealed.
```

This is the copy_probes lesson applied before the fact rather than after. U2 was
being deleted oldest-first on a 37-day clock *while* runs 80–82 were measuring
it; two draws four minutes apart disagreed by 42 events, and the historical
population 81A ORIGINAL measured is **not reconstructible**. That must not happen
to the forward data.

---

## 1. RETENTION

| | |
|---|---|
| retention duration | **unbounded** — no expiry |
| deletion mechanism | **none.** There is no DELETE in `obs/record.py`, and migration 062 puts a trigger on all four tables that **raises** on DELETE *and* UPDATE |
| minimum evidence floor | the whole population; nothing is trimmed |
| deletion disabled until sealed? | **yes, by construction** — a delete cannot execute at all, sealed cohort or not. Enabling one would need a migration that drops the trigger, which is a visible, reviewable act |
| snapshot/seal cadence | first seal when the first analysis cohort is cut; thereafter before each analysis run that will be cited |
| seal method | the `SNAPSHOT_CANONICAL_SPEC.md@v1` machinery that sealed U2 — canonical serialization, `UNCOMPRESSED_CANONICAL_SHA256` as the authoritative identity, an `EVENT_IDENTITY_DIGEST` over sorted ids, committed under `research/snapshots/` |

**The existing retention worker cannot reach these tables.**
`workers/retention.py` is pinned to exactly two: `ai_trades` by `placed_at` and
`copy_probes` by `probe_at`. It holds the plan in `TABLES` and re-checks it
against `PINNED` on **every cycle** — two separate objects on purpose, so an edit
that adds a table to one and not the other *refuses* rather than deleting. A test
now pins `rn1_obs_*` as absent from both.

---

## 2. CAPACITY — FROM MEASURED RATES, NOT ESTIMATES OF RATES

**Event rate, counted from the sealed U0 witness** (962,509 rows,
2025-07-09 → 2026-09-11):

| | events/day |
|---|---|
| mean, last 14 complete days | **11,324** |
| peak day in that window (2026-09-06) | **15,278** |
| most recent complete day (2026-09-11) | **16,184** |
| last four days | 11,004 → 13,579 → 14,995 → 16,184 — **rising** |

**Design rate used below: 17,000 events/day**, above the observed peak because
the trend is up.

**Row size.** Measured from U2's own ladders (40,000-row sample): the endpoint
returns at most 8 levels, mean 7.58, and the ask ladder serialises to **mean 118
bytes, p90 131, max 143**. Around that, the column widths and Postgres' heap and
index overhead are *estimated* — there is no database here to measure against, and
these are stated as estimates:

| row | est. bytes incl. indexes |
|---|---|
| `rn1_obs_snapshots` (ladder ~130 B + ~370 B columns/overhead + ~90 B indexes) | ~**600** |
| `rn1_obs_events` | ~**800** |
| `rn1_obs_transitions` × 2 | ~**500** |
| **per observed event** (1 event + 10 snapshots + 2 transitions) | ~**7.3 KB** |

| window | storage |
|---|---|
| per day | **~124 MB** |
| per week | **~0.87 GB** |
| 30 days | **~3.7 GB** |
| 90 days | **~11 GB** |

Duplicates are cheap: a replayed event is refused by the unique key on
`source_event_id` **before** any snapshot is scheduled, so a second ingestion of
the same fill costs one failed insert and no venue reads.

For scale: the August incident that took the database's hostname down for ~15
hours filled a **15 GB** disk. 30 days of this is 3.7 GB — material but not in
that class. **90 days is not.**

---

## 3. THE REAL CONSTRAINT IS NOT DISK — IT IS THE READ RATE

10 snapshots per event at 17,000 events/day is **170,000 reads/day = 1.97
reads per second sustained**.

The collector's default pacer is **2.0 reads/s**.

> **At the design rate the pacer runs at ~98% utilisation, and at the most recent
> observed day (16,184) at ~94%.** There is no headroom for a burst.

**Why that matters more than it looks.** A read the pacer delays past its window
is recorded `SKIPPED_PACING` — honestly, but the misses would not be random.
They would cluster in exactly the busiest minutes, which are the minutes with the
most price movement. That is a **selection effect on the forward curve itself**:
the instrument would be least likely to observe precisely the events it exists to
measure. A miss rate that correlates with market activity is worse than a lower
overall coverage that is uncorrelated.

This is a capacity decision, not something to paper over. The options, with what
each costs:

| option | cost |
|---|---|
| **raise `RN1_OBSERVABILITY_RPS` to 3–4** | the venue 429'd a board walk above ~3 req/s on 2026-08-23; the collector shares a process and an HTTP client with the mirror's reads. Lower risk while trading is paused (exits only) than it would be live |
| **sample events** (observe 1 in N) | breaks per-event completeness; needs a pre-registered, unbiased sampling rule, and the sampling must be recorded per event |
| **thin the offset grid** | changes the pre-registration, which is frozen. Would be an amendment with a reason, not a tuning knob |
| **accept the misses** | **not recommended** — the bias above |

**My recommendation:** run the first cohort with `RN1_OBSERVABILITY_RPS=3.0`
while `mirror_live=false`, and read the `SKIPPED_PACING` counters from the first
day before deciding anything further. The pause is the safest window there will
be to find out what the venue tolerates, and the counters make the answer
visible rather than inferred. **This is a recommendation, not a change** — the
default in the code stays 2.0 until you say otherwise.

---

## 4. WHAT GETS REPORTED BESIDE EVERY FIGURE

The collector already counts `dropped_queue_full`, `dropped_disabled`,
`snapshots_missed`, `snapshots_error` and `events_duplicate`. Any analysis of the
forward curve prints them beside the result. A gap in the evidence is visible or
it is not evidence.
