# RESERVATION R1 — executable, not yet authorized

One-time reservation of a protected lock tenure for the FROZEN substantive
capture. Derived from job-level timestamps (see ../CAPTURE_WINDOW_CONFLICT.md),
not from workflow age.

## The insight this rests on

`cancel-in-progress: false` means a RUNNING job is never displaced; only a
PENDING one is. Five observed handoffs show the lock passing in <= 1 second
from one job's `completed_at` to the next job's `created_at`. So the capture
does not need an idle window — it needs to be the sole pending run at one
handoff, after which the concurrency group protects it for its whole run.

## Sequence

| step | when | what |
|---|---|---|
| 0 | on approval | `sh reservation/apply_R1.sh` — omits the two occurrences |
| 1 | 2026-09-18 ~02:48:50Z | run85 35274725330 releases the lock (projected) |
| 2 | immediately after | forward-capture 35288911395, already pending, runs 33m18s IN FULL — nothing is displaced |
| 3 | 2026-09-18 02:49Z–03:20Z | dispatch `beta48-substantive-capture.yml`, ref `claude/session-njaewf`, inputs `{"code_sha":"bbce49dae0f20d9bad86bfc8f3322f28dc097859","capture_seconds":"5400"}` — UNCHANGED |
| 4 | 2026-09-18 ~03:22Z | our job is created = lock acquired; write the immutable manifest |
| 5 | immediately after step 4 | `sh reservation/restore_R1.sh` |
| 6 | 2026-09-18 06:00Z | backstop check-in runs `restore_R1.sh` unconditionally (idempotent) |
| 7 | 2026-09-18 ~05:12Z | capture completes; harvest gate runs |

## Clean-start gate still applies at step 3

The dispatch happens only if `venue_domain.domain_idle(active, known)` reports
`ACTIVE_DIRECT_CONFLICTS = 0` and `PENDING_DIRECT_CONFLICTS = 0` against the
real 19-workflow known list, with `DOMAIN_AUDIT = PASS` and
`capture_quality.thresholds_intact()["INTACT"] is True`. The reservation makes
the gate passable; it does not replace it or excuse a failing one.

Note the sequencing consequence: at step 3 the domain is NOT idle — the
forward-capture segment is running. Dispatching into a running domain is what
the gate forbids, because a pending run can be displaced. R1 removes the two
fires that could displace it, which is the whole point, but the gate as
written will still refuse. That divergence is named, not worked around:
see NEXT_OPERATIONAL_BLOCKER in the handover.

## Not in scope

No active-run cancellation. No schedule disabled. No concurrency weakening or
renaming. No change to collector SHA, selection, capture_seconds, polling,
measurement definitions or quality thresholds. No suspension of
reconciliation, cancellation or risk-reducing exits. No orders, no capital,
no credentials. mirror_live=false.
