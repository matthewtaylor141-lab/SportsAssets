# Codex security closeout, October 9, 2026

Base: `claude/red-team-closeout-v1` at
`b3f1b0cdaa159a08ee8f65d2f6bde83e76112f86`.
Branch: `codex/bettor-closeout-2026-10-09`.

Codex owns this isolated security increment. Claude owns RC6 integration and
production release. No release-branch update, deployment, credential rotation,
capital-authority change, order, or live activation is part of this increment.
SMALL LIVE remains SHADOW. The selected frontend checkout remains untouched.

## Confirmed defects and remediation

| Severity | Subsystem | Reproduction | Remediation | Owner |
|---|---|---|---|---|
| P1 | Authentication | Non-ASCII admin headers, passwords and signed-session signatures raise TypeError rather than returning a normal refusal; the header failure also crashes a public health request | Compare credential text as UTF-8 bytes in constant time, without normalization or changing callers' whitespace/scope rules | Codex |
| P1 | Admin throttle | 4,000 fresh clients after the global limit grows the map to 4,060 entries, exceeding its declared 2,000 bound | Check global budget before allocating client state; discard empty expired budgets | Codex |
| P1 | Unlock throttle | Caller-controlled leftmost forwarding hop resets the guess budget; 2,000 recent clients exceed the 1,000-key storage bound | Use the edge-appended rightmost hop; refuse new clients at capacity until expired budgets release it | Codex |
| P1 | Public admin diagnostic | Headerless `/api/admin/ping` bypasses the admin-header guard and can grow its separate map; spoofed forwarding hops reset its budget | Reuse the bounded atomic client budget and preserve ten requests/minute even for valid credentials | Codex |
| P1 | Concurrent throttle accounting | The original code overspends client/global/unlock budgets when requests interleave between read and charge | Serialize in-memory budget read/charge with a short lock, holding no I/O or await | Codex |

`concurrency-baseline.json` records the original b3f1b0cd functions under an
adversarial scheduler: 120 requests on 24 threads, a 1 ms yield between read and
charge. Recorded guesses were client 21/10, global 83/60, unlock 120/10. These
are deterministic interleaving challenges, not production rate measurements.
The new regression suite also exercises non-ASCII, bidi text, surrogate text,
long input, distinct Unicode representations, spoofed forwarding hops,
saturated storage, expiry, valid credentials and all session scopes.

Before the auth repair the first regression run had 16 failures / 1 pass.
After that repair, the added unlock regressions independently failed 2 / 19.
The complete relevant pinned-environment selection now passes 88 / 88,
including the existing auth/scope suites, runtime manifest and fresh-database
migration. This is local validation, not a GitHub gate verdict or production
acceptance. New hostile-input tests are mandatory in the capital-critical list.

## Environment and acceptance boundaries

Pinned interpreter: the repository Dockerfile's Python 3.12.3 image by digest.
Dependencies: requirements.lock and requirements-test.lock; pip check passes;
runtime_manifest confirms 87/87 distributions and exact interpreter. The cloud
proxy's public CA bundle is supplied to pip, including its build subprocesses;
TLS verification stays enabled. PostgreSQL 16 is local and disposable.

The selected older frontend checkout's fresh-migration defect is ALREADY FIXED
in this RC6 candidate by bootstrap_collector_tables. Codex does not duplicate
that implementation. The candidate's existing fresh-database and repeatability
regressions pass.

Production's public health read reports commit `732cc0c`, database healthy.
This does not establish workers/market-plane SHAs, production freshness,
profitability, stability, or signed acceptance. Exact-SHA GitHub checks and
production readback remain required after this increment. No subjective
95% score or profitability claim is made.

Next: independent RC6 freshness/Xavier/economics/receipt tests, exact-SHA
backend-tests/capital-critical/commit-guard/engine-diagnostic, then review by
Claude. A merge creates a new candidate SHA and requires gates on that SHA.

The public diagnostic follow-up independently reproduced two failures on the first security increment (headerless storage saturation and forwarded-hop rotation). Three additional regression cases pass after the repair, including its unchanged valid-credential request limit. The final candidate supersedes 8a86f614; exact-SHA gates are required again for the changed code.
