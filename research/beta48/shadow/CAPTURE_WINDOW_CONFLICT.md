# The substantive capture window: measured conflict and the smallest fix

STATUS: `CAPTURE_WINDOW_STATUS = NOT_AVAILABLE_UNDER_CURRENT_SCHEDULE`
Requires a management decision. No schedule has been changed, no run
cancelled, no concurrency altered.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.

## 1. What was measured

Every workflow that can reach the venue shares one GitHub concurrency group,
`pmus-public-read-global`, with `cancel-in-progress: false`. Nineteen
workflows are in that group (`venue_domain.audit()`), of which exactly two
are on a schedule; the other seventeen are `workflow_dispatch` only.

| workflow | cron | fires/day | observed duration (n, min / median / max) | timeout |
|---|---|---|---|---|
| `run85-phase2-capture` | `0 */6 * * *` | 4 | 6 runs, 219.1 / **299.7** / 316.8 min | 340 |
| `beta48-forward-capture` | `0 */2 * * *` | 12 | 8 runs, 4.1 / **167.6** / 225.9 min | 120 |

Durations are `run_started_at` → `updated_at` on real runs from the GitHub
API over 2026-09-16T16:11Z → 2026-09-18T00:45Z.

Observed domain occupancy over that 32.6-hour window: **29.8 h busy, 91.6 %**.

Idle gaps between venue-touching runs in that window, longest first:

| gap | from | to | next holder |
|---|---|---|---|
| 59.0 min | 2026-09-17T10:17:36Z | 2026-09-17T11:16:39Z | run85-phase2-capture |
| 44.3 min | 2026-09-17T02:47:08Z | 2026-09-17T03:31:26Z | beta48-rate-confirm |
| 33.0 min | 2026-09-17T03:32:20Z | 2026-09-17T04:05:21Z | beta48-rate-confirm |
| 17.2 min | 2026-09-17T09:59:09Z | 2026-09-17T10:16:20Z | beta48-substantive-capture |
| 9.9 min | 2026-09-17T02:36:33Z | 2026-09-17T02:46:29Z | beta48-rate-confirm |

**Gaps of 90 minutes or more: 0 of 5. Longest observed gap: 59.0 minutes.**
The frozen capture needs 5400 s of paced collection plus selection, isolation
sweeps and sealing — call it 110 minutes of domain tenure.

## 2. The exact scheduling conflict

It is a PERIOD conflict, not a load conflict, and it is structural.

A GitHub concurrency group holds at most ONE pending run: a newer pending
run displaces the older one. So the domain stays continuously occupied
whenever **at least one new fire arrives during every service period**.

- `beta48-forward-capture` fires every **120 min**.
- Median service is **168 min** (forward-capture) and **300 min** (run85).

Both service times exceed the 120-minute fire interval. A fire therefore
always lands while the domain is busy, always becomes the pending run, and
always starts the instant the running job ends. The domain never drains.

The daily arithmetic says the same thing. Demand is
`4 x 300 + 12 x 168 = 3216` minutes per day against 1440 minutes of wall
clock — **2.23x capacity**. The excess is absorbed by GitHub silently
cancelling displaced pending runs, which is why the run history shows
`cancelled` scheduled runs that never collected anything.

Consequences that follow directly, and which no re-arming cadence can fix:

1. Polling the gate every 45–90 minutes cannot succeed. The gate is correct
   and the domain is genuinely never idle.
2. Dispatching into the busy domain would leave the substantive capture
   PENDING, where the next `beta48-forward-capture` fire (at most 120
   minutes away) would displace and cancel it before it collected a row.
3. `beta48-forward-capture` is also losing collection to itself: at a
   167.6-minute median against a 120-minute period, it cannot complete one
   run before the next fire queues behind it.

## 3. The smallest prospective change (FOR APPROVAL)

Not proposed: cancelling a running collector, disabling a schedule
permanently, relaxing the concurrency group, shortening `capture_seconds`,
or changing the pinned collector, selection or quality thresholds. None of
those is on the table and none has been done.

Proposed: **omit three of the sixteen scheduled fires on one nominated
date**, leaving both crons otherwise untouched and restoring them after.

On date D (UTC):

| workflow | change | fires omitted |
|---|---|---|
| `run85-phase2-capture` | `0 */6 * * *` → `0 0,6,12 * * *` for date D | 18:00Z |
| `beta48-forward-capture` | `0 */2 * * *` → `0 0-16/2,22 * * *` for date D | 18:00Z, 20:00Z |

Resulting timeline on date D, using the observed medians:

- 12:00Z `run85-phase2-capture` runs, ends ≈ 17:00Z.
- 16:00Z `beta48-forward-capture` is pending, starts ≈ 17:00Z, ends ≈ 19:48Z.
- No further scheduled fire until 22:00Z.
- **Domain idle ≈ 19:48Z → 22:00Z = 132 minutes ≥ the 110 minutes required.**

The substantive capture is dispatched at the start of that window, under the
existing clean-start gate — which still has to pass on its own measurement,
not on this projection.

Why this is the minimum: removing fires without a coordinated quiet band
does not work. Even with `beta48-forward-capture` reduced to zero, run85
alone leaves only `360 − 300 = 60` minutes between cycles at the median,
which is still short of 110. The window has to be made by skipping one
run85 occurrence; the two forward-capture omissions only stop that window
being consumed the moment it opens.

Cost of the change: one run85 phase-2 capture cycle and two forward-capture
cycles, on one day. Both are recurring collectors that will run again on
their normal schedule the following day.

## 4. What is NOT blocked by this

Collection authorization is unchanged. Decision-grade trading remains
blocked on `DEPENDENCE_MODEL_VALIDATED` and is unaffected by any of the
above. Indirect venue load through our own API is reported separately and
remains `NOT_ESTABLISHED` — no GitHub concurrency group gates it and nothing
counts those requests, so its magnitude stays `NOT_IDENTIFIED`.
