# R1A — IDLE-START RESERVATION (selected direction)

R1 is SUPERSEDED. Its apply/restore scripts are renamed to
`SUPERSEDED_*.sh.txt` and are not executable. R1 relied on winning a lock
handoff from a running collector, which would have required dispatching while
the domain was not idle — a bypass of the frozen gate. Not taken.

R1A starts only from a genuinely idle domain, exactly as the pinned
`venue_domain.py` already requires. No gate change, no bypass.

## Two corrections that R1A depends on

1. **GitHub reports a concurrency-held run as `pending`, not `queued`.**
   Every earlier gate reading in this session passed only the `in_progress`
   run into `domain_idle()`, so `PENDING_DIRECT_CONFLICTS` read 0 for the
   wrong reason. With the pending run included it reads 1, and
   `KNOWN_DIRECT_PMUS_COLLECTORS_PENDING = ['beta48-forward-capture']`. The
   gate was right; the input was incomplete.

2. **A cron edit cannot be assumed to cancel an occurrence whose nominal
   time has already passed.** Observed `run85-phase2-capture` fires are
   2026-09-16T21:03:28Z and 2026-09-17T04:08:56Z, 11:16:39Z, 16:33:24Z,
   21:05:52Z. None is near the nominal 00/06/12/18Z of `0 */6 * * *`, and
   the inter-fire intervals are 7h05m, 7h08m, 5h17m, 4h32m. Nothing in the
   available evidence maps an actual fire to a nominal occurrence, so the
   earlier claim of "exactly two omitted occurrences" is **WITHDRAWN**. It
   is not supported by run state or trigger evidence.

## R1A sequence

1. Let `run85-phase2-capture` 35274725330 complete normally. Do not cancel.
2. Let `beta48-forward-capture` 35288911395 — already pending — acquire the
   domain and complete normally. Do not displace.
3. After BOTH have actually completed, re-evaluate the complete clean-start
   gate, passing **every** in_progress AND pending venue-touching run.
4. Dispatch the frozen capture only if `ACTIVE_DIRECT_CONFLICTS = 0`,
   `PENDING_DIRECT_CONFLICTS = 0`, `DOMAIN_AUDIT = PASS` and
   `capture_quality.thresholds_intact()["INTACT"] is True`.
5. **Verify acquisition, not acknowledgement.** Poll `list_workflow_jobs`
   for the dispatched run until a job exists with a `created_at`. A job
   created = the domain was acquired. A dispatch response is not proof.
6. If another collector takes the slot first, do not displace it and do not
   bypass the gate. Report the changed state and wait for the next idle
   moment.

Whether a schedule change is needed at all is decided at step 3, from
measurement. R1A does not require one to be applied in advance, and none is
requested here.

## Bounded temporary schedule change — SPECIFICATION ONLY, NOT REQUESTED

Provided so that an approval, if ever sought, has something exact to approve.
Nothing below has been applied and no approval is being asked for it now.

| field | value |
|---|---|
| reservation id | `R1A-2026-09-18` |
| original value, run85-phase2-capture | `cron: "0 */6 * * *"` |
| original value, beta48-forward-capture | `cron: "0 */2 * * *"` |
| temporary value, run85-phase2-capture | `cron: "0 6,12,18 * * *"` |
| temporary value, beta48-forward-capture | `cron: "0 0,4,6,8,10,12,14,16,18,20,22 * * *"` |
| effective period | from the apply commit until the restore commit, bounded by the expiry below |
| expiry (absolute) | `2026-09-18T06:00:00Z` |
| expiry enforcement | scheduled check-in at the expiry that runs the restore unconditionally, whatever the capture did and whether or not the session is attending |
| restoration identifier | marker file `reservation/ACTIVE_R1A-2026-09-18.json` carrying the id, both original values, the apply commit SHA and the expiry; restoration is complete when the marker is absent AND both cron lines equal their originals |
| restoration check | the restore rewrites only the two cron lines by exact string match, is idempotent, and then asserts `git diff` of the apply→restore range touches those two lines and the marker only — so unrelated concurrent edits to either workflow are preserved, not reverted |
| what it does NOT claim | that it cancels any specific already-delivered or already-scheduled occurrence; see correction 2 |

## Preserved, unchanged

Collector SHA `bbce49dae0f20d9bad86bfc8f3322f28dc097859`, selection,
`capture_seconds` 5400, request pacing (`REQUEST_INTERVAL_S` 4.0, rate
"0.25"), `FROZEN_QUALITY_THRESHOLDS_SHA`
`70e6ddda88ad4141ae3ed58691d8322cea4bf37a83c89efd481792c47243e7fb`,
concurrency group `pmus-public-read-global`, `cancel-in-progress: false`.
No active or pending run cancelled. No EV architecture, model, training or
tuning. No orders, no capital. mirror_live=false.

## Indirect isolation is NOT waived

Scheduling approval concerns the GitHub concurrency domain only. It says
nothing about our API and application workers, which reach the venue outside
any GitHub group. `INDIRECT_BETTOR_PMUS_LOAD_ISOLATION = NOT_ESTABLISHED`,
`INDIRECT_CONFOUND_MAGNITUDE = NOT_IDENTIFIED`, and this does not change
because a window was granted. Reconciliation, cancellations and
risk-reducing exits stay intact and are not suspended for the capture.

Which of the two regimes the approved experiment is under —
DISCLOSED_RESIDUAL (capture proceeds with indirect traffic present, recorded
in the manifest as an uncontrolled confound) or ISOLATION_REQUIRED (capture
waits until indirect venue requests are instrumented and shown to be absent
or bounded) — has not been stated and is not inferable from the scheduling
decision. It is an open approval item.
