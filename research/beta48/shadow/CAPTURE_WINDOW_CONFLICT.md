# The substantive capture window: corrected timeline and a reservation

SUPERSEDES the first version of this file (commit 46a0454), which used
WORKFLOW AGE as service time. That was wrong. The figures it produced are
withdrawn in section 0.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
No schedule has been changed, no run cancelled, no concurrency altered.

## 0. WITHDRAWN

| withdrawn figure | why it was wrong | corrected value |
|---|---|---|
| `beta48-forward-capture` median service **167.6 min** | `updated_at − run_started_at` is queue wait + runtime | job runtime **33m18s** (n=3, range 33m17s–33m19s) |
| daily demand **2.23x capacity** | built on the inflated service time | **1.11x** (1597.8 min/day vs 1440) |
| domain busy **91.6%** of 32.6 h | counted queued runs as busy | withdrawn; see §2 for what busy actually means here |
| longest idle gap **59.0 min** | derived from the same intervals | withdrawn; see §3 |
| the **132-minute** projected window | built on the above | withdrawn; replaced by §4 |
| "omit **three** fires" | built on the above | replaced by **two**, §4 |

`run85-phase2-capture`'s ~300 min figure happened to survive the correction
(true job runtime 4h59m33s) because its queue waits were small next to its
runtime. It was still derived the wrong way and is re-derived below.

## 1. Corrected per-run timeline, from job timestamps

`job.created_at` is when GitHub created the job — i.e. when the run left the
concurrency queue and the lock was acquired. `job.started_at` adds runner
acquisition. Neither is invented; both come from the jobs API.

| run | workflow | queue (run_started → job created) | job runtime | venue-read span |
|---|---|---|---|---|
| 35150204376 | run85 | 0m00s | 4h59m32s | 4h59m07s |
| 35165178033 | forward | 1h56m29s | 33m17s | 32m49s |
| 35180811691 | run85 | 17m12s | 4h59m33s | 4h59m08s |
| 35193319208 | forward | 2h14m10s | 33m19s | 32m48s |
| 35214834063 | run85 | 0m01s | 4h59m34s | 4h59m08s |
| 35224744353 | forward | 3h12m35s | 33m18s | 32m49s |
| 35247311790 | run85 | 16m15s | 4h59m32s | 4h59m08s |

Venue-read span is the collector's own capture step (`Capture one segment`
for run85; `Enumerate` → `Depth` for forward-capture), so it is the interval
in which venue requests were actually issued. It is essentially the whole
job runtime in both cases — the pre-execution delay involved no venue
contact at all.

Both collectors are FIXED-DURATION, to the second:
- `run85-phase2-capture`: 4h59m33s ± 1s (n=4). A 5-hour segment.
- `beta48-forward-capture`: 33m18s ± 1s (n=3).

## 2. Concurrency-lock occupancy, where observable

The lock handoff is directly visible: the next job's `created_at` equals the
previous job's `completed_at`, to the second.

| releasing run | completed | acquiring run | job created | gap |
|---|---|---|---|---|
| run85 35150204376 | 02:03:09Z | forward 35165178033 | 02:03:10Z | 1 s |
| run85 35180811691 | 09:25:44Z | forward 35193319208 | 09:25:45Z | 1 s |
| run85 35214834063 | 16:16:17Z | forward 35224744353 | 16:16:18Z | 1 s |
| forward 35224744353 | 16:49:39Z | run85 35247311790 | 16:49:39Z | 0 s |
| run85 35247311790 | 21:49:14Z | run85 35274725330 | 21:49:15Z | 1 s |

Two gaps in the same window were NOT handoffs — 02:36:32Z→04:26:08Z
(1h49m36s) and 09:59:08Z→11:16:40Z (1h17m32s). In both, the domain was
genuinely idle: no job existed and no venue request was being issued. So
real idle time does occur; it was hidden by the earlier method.

DISPLACEMENT, also directly observed. Three `beta48-forward-capture` runs
have **zero jobs** — they never executed and never touched the venue:

| run | fired | cancelled | newer fire that displaced it |
|---|---|---|---|
| 35255713568 | 17:55:50Z | 21:01:48Z | forward 35274310199 at 21:01:47Z |
| 35274310199 | 21:01:47Z | 21:05:53Z | run85 35274725330 at 21:05:52Z |
| 35288911395 | 23:54:44Z | still pending | — |

Each was cancelled one second after a newer run entered the group. That is
the single-pending-slot rule, evidenced rather than assumed.

## 3. What the corrected numbers say

Daily demand, job runtime only:

- `run85-phase2-capture`: 299.55 min x 4 fires = 1198.2 min/day
- `beta48-forward-capture`: 33.30 min x 12 fires = 399.6 min/day
- Total **1597.8 min/day against 1440 min = 1.11x capacity.**

Oversubscribed, but mildly — not 2.23x. The binding constraint is not
aggregate load, it is the shape:

- `run85` occupies **83.2%** of each 6-hour period, leaving a **60.4-minute**
  residue. Shorter than the ~110 minutes the frozen capture needs.
- Inside any run85-free stretch, `beta48-forward-capture` fires every 120 min
  and holds the lock 33.3 min, leaving **86.7-minute** gaps. Also shorter
  than 110.

So no naturally occurring gap is long enough, which is the same conclusion as
before — but for a different and much narrower reason, and with a much
cheaper fix.

## 4. The reservation: the capture does not need an idle window

The correction exposes something the first version missed. `cancel-in-progress`
is **false**, and the handoff table shows a RUNNING job is never displaced —
only a PENDING one is. The substantive capture therefore does not need 110
minutes of idle domain. It needs to **win one lock handoff**. Once it holds
the lock, the concurrency group protects it for its whole 5400-second run,
and later fires simply queue as they already do.

That reduces the ask from "manufacture a two-hour quiet period" to "keep the
pending slot clear for the few minutes between dispatch and handoff".

### Projected handoff, from the live run

`run85-phase2-capture` 35274725330: job created 2026-09-17T21:49:15Z (after
43m23s queued behind its predecessor), `Capture one segment` started
2026-09-17T21:49:36Z. At the observed 4h59m08s that step ends
**2026-09-18T02:48:44Z**, job completes **~2026-09-18T02:48:50Z**.

`beta48-forward-capture` 35288911395 is already pending and takes the lock at
that moment, running 33m18s to **~2026-09-18T03:22Z**.

### RESERVATION R1 — earliest feasible protected window

| field | value |
|---|---|
| dispatch window | 2026-09-18 **02:49Z – 03:20Z** (after 35288911395 has STARTED, so nothing queued is displaced) |
| projected lock acquisition | 2026-09-18 **~03:22Z** |
| capture duration | 5400 s paced + selection/sealing ≈ **110 min** |
| projected release | 2026-09-18 **~05:12Z** |
| displaces | **nothing** — 35288911395 runs first, in full |

### Occurrences to omit — exactly two

Only a fire arriving between dispatch and acquisition can displace the
capture. In that span the candidates are:

| # | workflow | occurrence to omit | why |
|---|---|---|---|
| 1 | `run85-phase2-capture` | nominal **2026-09-18 00:00Z** | delivery drift is 3h05m–7h08m (observed fires 21:03:28, 04:08:56, 11:16:39, 16:33:24, 21:05:52), so this occurrence can arrive anywhere in ~01:35Z–04:15Z and would both displace the pending capture and then hold the lock for 5 h |
| 2 | `beta48-forward-capture` | nominal **2026-09-18 02:00Z** | fires inside the dispatch window |

Nothing else is touched. The 04:00Z forward-capture fire and the next run85
fire arrive while the capture is RUNNING and simply queue — the same
behaviour they already have today.

Cost: one 5-hour run85 segment and one 33-minute forward segment, once.

### Expiry and restoration

GitHub has no native expiry for a cron omission, and an in-workflow guard
cannot help — displacement happens at queue time, before any step runs. So
the mechanism is explicit and three-layered:

1. **Apply**: one commit editing two cron lines
   (`run85-phase2-capture` `0 */6 * * *` → `0 6,12,18 * * *`;
   `beta48-forward-capture` `0 */2 * * *` → `0 0,4,6,8,10,12,14,16,18,20,22 * * *`).
2. **Restore**: the exact revert commit, prepared in advance and pushed the
   moment the capture's job is observed created (lock acquired). Both crons
   are back before their next occurrence.
3. **Backstop**: a check-in armed for **2026-09-18T06:00Z** that pushes the
   revert unconditionally if step 2 did not happen, whatever the capture did.

Layer 2 is the normal path; layer 3 guarantees the omission cannot outlive
the reservation even if the session is interrupted.

### What is NOT proposed

No active run cancelled. No schedule disabled. No concurrency group weakened
or renamed. No change to the collector SHA, selection, `capture_seconds`,
polling, measurement definitions or quality thresholds. No suspension of
reconciliation, cancellation or risk-reducing exits — none of those is in
this concurrency group and none would be touched to obtain a window.

## 5. Load isolation, resolved separately

**Direct** (GitHub collectors): measurable and measured above. A clean group
proves only that no *workflow* in the group is reading the venue.

**Indirect** (our API and application workers): `NOT_ESTABLISHED`,
`INDIRECT_CONFOUND_MAGNITUDE = NOT_IDENTIFIED`. These reach the venue through
our own API, which no GitHub concurrency group gates and which emits no
counter this session can read. `mirror_live=false` (exits-only) still
performs venue reads — position and order-status polling, reconciliation and
exit management all continue, and they must, so the capture will be taken
with indirect load present and that fact recorded in the manifest rather than
asserted away. Establishing it would need a venue-request counter on the API
and worker paths, exported where the capture job can read it; that
instrumentation does not exist and is not part of this reservation.
