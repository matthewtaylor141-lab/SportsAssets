# Release receipt: 91fd4fc (API-only)

Candidate 91fd4fc2d121922906995d111f3c9f679c0b117e on claude/rel-pipeline:
0fa0fd2 (serving trading release) + learning record and bounded 5-6 pp policy
parameter (5.0 floor, rollback without control changes) + paper-first pages
round 1 + licensed Rocketbox characters + deterministic lifecycle rehearsal
tool + pipeline fixes (paper book read inside the decision deadline,
paper_hook_failures (migration 187), completed-game policy decides first,
start-to-start collector cadence) + funded-status chat label.

Gate: fresh worktree, mp2-template DB migrated by the candidate,
run_gate.sh --timeout=600, gate_verdict.py with the critical list.
ACCEPTED against 0fa0fd2, 70aec64, a8bf, e348, e4dc: 0 new failures; the 70
failures in HEAD are all present in the matched baselines. Files: gate/.

Rehearsal evidence (synthetic, local DB, isolated account, NOT live
execution): research/proofs/rehearsal/ on claude/rel-rehearsal (merged).
