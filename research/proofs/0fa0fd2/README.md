# Release receipt: 0fa0fd2 (completed-game paper policy, API-only)

Candidate: 0fa0fd286a874491e226aaa6c5464cb3b82df846 on claude/rel-settle
(d9914b5 + b9aeaf7 review fixes + 0fa0fd2 Codex soccer exclusion patch).

## Gate
Fresh worktree at the SHA, database from the mp2 template migrated by the
candidate, tools/run_gate.sh --timeout=600, tools/gate_verdict.py with the
critical list. Verdict ACCEPTED against all four matched baselines
(70aec64 gate_lbl, a8bf, e348, e4dc): 0 new failures; 70 failures in HEAD,
every one already failing in the matched baseline. Files: gate/.

d9914b5 was REJECTED by the same gate (one critical failure: the
external_valuations reader pin, 6 -> 8 for Xavier's venue-price read). The
pin was re-reviewed and updated in b9aeaf7; that test passes in 0fa0fd2.

## Xavier trade-off test (critical list), same order and environment
test_a_loss_directive_becomes_an_evaluated_committed_xavier_candidate:
- full gate, fresh DB: PASSED on 70aec64 and on 0fa0fd2.
- targeted list (common_list.txt, identical order), fresh DB per commit:
  70aec64 passed it (1 unrelated persona failure); 0fa0fd2 passed it.
- the same list again on the now-used DB: FAILED on BOTH commits (with the
  same persona test). SHARED, pre-existing, state-dependent; not introduced.
Files: xavier_tradeoff_same_order/.
