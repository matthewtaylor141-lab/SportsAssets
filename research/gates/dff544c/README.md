# Release gate record: dff544c (claude/round2)

All runs used `tools/run_gate.sh <checkout> <db> <out> --timeout=600` with the
same GATE_TOOLS_DIR, a fresh database created from template `mp2` then migrated
by `sportsassets.scripts.migrate`, python 3.11.15, pytest 9.1.1 and an identical
pip freeze (a5fd9ef9e4c78dd2). Verdicts are from the committed `tools/gate_verdict.py`.

| # | Candidate | Baseline report | Verdict |
|---|---|---|---|
| 1 | 7a3ba7b | aca3564 (29 Sep) | NEW_FAILURES: 1 critical (reader census; fixed in dff544c) + mirror-family failures |
| 2 | dff544c run 1 | aca3564 (29 Sep) | NEW_FAILURES: 95 identities |
| 3 | dff544c run 2 | aca3564 (29 Sep) | NEW_FAILURES: 67 identities |
| 4 | dff544c run 2 | b51378d, same machine, same hour (14:46Z) | ACCEPTED: 0 new |
| 5 | dff544c run 1 | b51378d, same machine, same hour | ACCEPTED: 0 new |

The identities new against aca3564 (mirror, loss-stop, fill and review-pin suites)
all passed when their files were run in isolation on dff544c (file 7). They also
reproduce on the serving baseline b51378d (runs 4/5: the baseline had 181 failures,
a superset of the candidate's 153). Root cause remains unresolved.

Migration state differs only by the candidate's `151_pair_observation_admission.sql`.
Collection: the candidate adds 7 test files; the 4 baseline-only ids are parameterised
tests whose ids embed wall-clock timestamps (the same tests collect under new ids).
Collection errors: none in either. Setup failures: none. Skips: 48 and 48.
All 186 declared critical entries passed in the candidate.
