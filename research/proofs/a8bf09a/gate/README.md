# Release gate: frozen candidate a8bf09a (eef233b + Pinnacle-only paper benchmark 9c8a353 + chat e411eba)
- Clean immutable checkout /tmp/claude-0/gate_a8bf; fresh DB from mp2 migrated to 182; run_gate.sh --timeout=600; 03:51:29Z start.
- pytest exit 1 (expected): 70 failed, 16,115 passed, 48 skipped, 3 xfailed.
- vs e348ebf: ACCEPTED, 0 new failures. vs dff544c: ACCEPTED, 0 new failures. vs eef233b (deployed): ACCEPTED, 0 new failures.
