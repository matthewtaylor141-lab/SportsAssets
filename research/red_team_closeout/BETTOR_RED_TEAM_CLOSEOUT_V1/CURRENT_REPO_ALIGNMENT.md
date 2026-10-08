# Current BETTOR alignment for Red Team Closeout V1

Verified against the current GitHub state available during package build.

## Production / accepted evidence lineage

The accepted production backend release previously identified is:
- release commit `a91be09f125f0ab0d1f29a0d0866b5cb6f5765fd`
- release tree derived from tested closeout SHA `659edaf21c2deb1dcc032932f38a7ff1ec8456c2`

## Current completion branch finding

`claude/completion-readiness-v1` currently points to:
`2bcdf10c8396a6ffa354c74b6a6ee7c574cb931d`

That commit says the Completion Readiness package was imported unchanged and
its package tests passed.

Important red-team finding: the branch is not yet acceptable as a release
candidate merely because the package was imported. A GitHub compare against
the tested closeout history reported the branch as diverged. Before release,
Claude must explicitly prove the final implementation is a clean descendant of
the accepted production/release lineage (or reconcile every intervening
accepted production change) and then rerun all exact-SHA gates.

## Positive progress already visible on the completion branch

`backend/sportsassets/workers/all.py` no longer imports
`universal_market_plane` into the shared worker import list and uses
`loop_contract` semantics. This indicates source-level runtime isolation work is
underway.

Do not regress that.

## Current profitability truth

EV Probability branch:
`037bdc0b9e056a997eb8c00cbd7a3be246549e70`

Its first real-data receipt remains CASH:
- 13,312 decision rows
- 195 canonical events
- 272 settled markets
- raw BETTOR/Derek loses to venue price OOS
- 0/61 sport x family x regime segments met the minimum independent-event bar
- conservative executable EV lower bounds were negative

Profitability Stack:
`bfe01a2705291fd3aa7ac3684d44ee9a99473d2d`

Current evidence:
- Digital Twin fill agreement ~70.1%
- 200 optimistic twin fills where PAPER expired
- structural arb had no settlement-proven compatible legs in the sampled data
- Governor = CASH

This package must preserve those negative baselines. It must not tune them away.

## Current Kalshi status

The prior Kalshi Canonical Venue package was built separately. At package build
time, `claude/kalshi-canonical-venue-v1` had not yet appeared as a branch.

The red-team closeout depends on that package for:
- canonical economic claim aliases
- explicit YES/NO route preservation
- fee-aware best route
- same-venue and cross-venue Adriana arb

This package adds the missing capital/risk interlocks above that layer:
- canonical exposure lock
- execution sentinel
- venue-health isolation
- truth quorum
- profit-source circuit breakers
- readiness gate
