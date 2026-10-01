# Release gate: frozen candidate 70aec64 (a8bf09a + per-strategy attribution in agent chat)
- Clean immutable checkout /tmp/claude-0/gate_lbl, at 70aec64. Fresh DB from mp2, migrated to 182. run_gate.sh --timeout=600. Started 04:36:09Z, pytest finished 04:59:42Z.
- pytest exit 1 (expected): 70 failed, 16,116 passed, 48 skipped, 3 xfailed. The timeline's "pytest-exit=0" is the echo's status, not pytest's.
- Verdicts, each ACCEPTED with 0 new failures:
  - vs a8bf09a (deployed);
  - vs e348ebf;
  - vs e4dc132 (198 baseline failures no longer failing).
