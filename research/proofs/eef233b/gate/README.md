# Release gate: frozen candidate eef233b (ddd4050 + Derek same-day bootstrap fix)
- Immutable checkout /tmp/claude-0/gate_eef2 at eef233b935f3f70b7e79d38a83b04df7555206af (clean), fresh DB from mp2 template migrated to 181, run_gate.sh --timeout=600, started 02:35:37Z, finished 02:58:51Z.
- pytest exit 1 (expected: matched baselines fail tests): 70 failed, 16,082 passed, 48 skipped, 3 xfailed.
- Verdict vs e348ebf (gate_e348_out/head_report.json): ACCEPTED, 0 new failures, 70 common.
- Verdict vs dff544c (gate_e4dc_out/base_report.json): ACCEPTED, 0 new failures, 198 no longer failing, 70 common.
- Critical list: backend/tools/capital_critical_tests.txt at eef233b.
This gate covers eef233b only. Any later chat-fix candidate needs its own gate.
