# Codex security closeout, October 9, 2026

Base: `claude/red-team-closeout-v1` at
`b3f1b0cdaa159a08ee8f65d2f6bde83e76112f86`.
Branch: `codex/bettor-closeout-2026-10-09`, PR #1.
Claude owns independent integration approval and production release.
No release-branch change, deployment, credential rotation or capital-authority
change occurs here. SMALL LIVE remains SHADOW.

## Corrections required by integration review

Rejected head `38e1da046428b4a933cfac16f4833840f4919db9` reproduced both
introduced lockouts. Five regression cases failed: another client's correct
COMMAND read/control passwords were refused after ten wrong guesses through
a shared Netlify egress; a newly arriving valid desk/COMMAND client or admin
monitor was refused when 1,000 recent clients occupied the map.

The successor takes the integrator's release-scoped option: preserve the base
first-hop proxy client key for unlock/ping, while retaining the admin guard's
separate rightmost-hop identity and global failure ceiling. It introduces no
proxy secret or environment requirement. This retains the base forwarding
trust model; it does not claim cryptographically authenticated proxy identity
or repair caller-controlled first-hop spoofing. A signed Netlify proxy is a
separate configuration/workflow change if the owner chooses it later.

Unlock/ping maps use bounded OrderedDict LRU eviction, rather than denying all
new clients at capacity. Current retained clients keep the existing 10/minute
limit; eviction can remove an older client's budget, a deliberate bounded
storage/availability tradeoff requested by the integrator. This is not a
claim of exact unlimited-client tracking. Expiry/eviction avoids full-map scans
on every refusal. Atomic read/charge remains protected by a short lock.
Admin wrong-token traffic retains its 10/client and 60/global per 600 seconds,
with global-first rejection before allocating new client state. Its identity
joins every X-Forwarded-For header line before selecting the final hop.

Non-ASCII credentials compare UTF-8 bytes in constant time without Unicode
normalization, changing whitespace rules, accepting defaults, or changing
session scopes. The remaining release-receipt admin and Slack signature
comparisons now use the same helper. Correct credentials remain required.

## Measured validation

The rejected head fails all five newly added lockout regressions. Corrected
pinned auth selection: **117/117 passing** (`test_auth_hostile_inputs`,
`test_admin_token_guard`, `test_desk_auth`, `test_wall`,
`test_the_operator_session_is_scoped`, `test_no_published_default_credential`).
It includes valid sign-ins/cookies after saturation, duplicate forwarding
headers, expiry, Unicode/surrogates, preserved valid-token ping limits and
concurrent accounting. Runtime/fresh-database and full exact-SHA gates are
reported separately after source freeze; prior SHA receipts do not substitute.

The recorded 21/83/120 overspending baseline in `concurrency-baseline.json`
uses OS threads with an injected 1 ms sleep between read and charge. It is a
thread-interleaving stress test, not a reproduced production async-event-loop
P1: production's no-await path does not provide that interleaving. The locks
are retained as bounded defensive accounting, with no I/O under them.

Pinned environment: Python 3.12.3 from production's digest, locked runtime/test
dependencies, PostgreSQL 16, Node 24.19.0 and jq 1.7.1. TLS verification remains
enabled. Synthetic credentials and local databases are not production proof.

Verified production collection 37947064416 is for release
`3d5af039d42026df16d9dad6ec0d4587b0141101` and implementation
`b3f1b0cdaa159a08ee8f65d2f6bde83e76112f86`. Its acceptance remains RED.
Both collected packets and manifests pass independent Sigstore verification;
this does not establish >=95% production readiness, freshness or profitability.

The successor must be independently re-reviewed. Approved integration creates
a new SHA requiring all four gates; creating a release SHA requires them again.
