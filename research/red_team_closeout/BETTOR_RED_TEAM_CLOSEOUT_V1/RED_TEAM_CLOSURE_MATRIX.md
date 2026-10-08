# Red Team Closure Matrix

Every identified failure mode has a named control. `CLOSED_IN_PACKAGE_REQUIRES_INTEGRATION_PROOF` means the code/control exists in this ZIP, but production closure requires Claude to bind it to BETTOR and return exact-SHA/runtime evidence.

| # | Failure mode | Control | Artifact | Proof |
|---:|---|---|---|---|
| 1 | Upgrade exists only in research | Release gate: exact tested/release/deployed SHA + production readback | `release_guard.py` | T21-T25 |
| 2 | Branch/release contamination | Descendant-of-accepted-base check; exact-SHA gates | `release_guard.py` | T21-T25 |
| 3 | Worker OOM returns | Source-level UMP isolation dependency + runtime soak requirement + RSS gate in prior Completion package | `vendor/BETTOR_COMPLETION_READINESS_PATCH_V1.zip` | integration |
| 4 | Process alive but market data dead | Stream currency gate requires complete authoritative snapshot, no gap, current book | `stream_guard.py` | T47-T48 |
| 5 | PMX silently falls back to REST | Source-specific health/readback; do not blend source names | `freshness_guard.py` | T19-T20 |
| 6 | Kalshi/Polymarket health contamination | Per-venue health domains; pair requires both healthy | `freshness_guard.py` | T19-T20 |
| 7 | Wrong fixture mapped | Dependency on Kalshi canonical package structured mapping | `vendor/BETTOR_KALSHI_CANONICAL_VENUE_V1.zip` | dependency |
| 8 | False YES/NO equivalence | Dependency on exhaustive payoff-vector claim fingerprints | `vendor/BETTOR_KALSHI_CANONICAL_VENUE_V1.zip` | dependency |
| 9 | Wrong Kalshi wire semantics | Explicit-book requirement + canonical package adapter contract | `vendor/BETTOR_KALSHI_CANONICAL_VENUE_V1.zip` | dependency |
| 10 | Same-market YES/NO mistaken for arb | Adriana existing same-market exclusion + canonical claim requirement | `integration/CURRENT_SYSTEM_BINDING.md` | integration |
| 11 | Screen price beats all-in cost | Fee guard + canonical best-all-in router dependency | `fee_guard.py` | T26-T27 |
| 12 | Stale/wrong fee schedule | Versioned fee evidence; unknown fee ineligible | `fee_guard.py` | T26-T27 |
| 13 | Top-of-book depth illusion | Capacity frontier + full depth/VWAP in Kalshi package | `capacity_guard.py` | T39-T40 |
| 14 | Equivalent aliases hide duplicate exposure | Canonical exposure lock | `claim_exposure.py` | T01-T03 |
| 15 | Asynchronous arb books | Venue health/stream gates + execution revalidation | `freshness_guard.py/two_leg_sentinel.py` | T05,T19-T20 |
| 16 | Guaranteed economics confused with guaranteed execution | Two-leg sentinel; only LOCKED after matched fills | `two_leg_sentinel.py` | T04-T11 |
| 17 | Cross-venue cash unavailable | Truth quorum requires venue balances; readiness stays fail-closed | `truth_quorum.py` | T12-T15 |
| 18 | Order retry duplicates exposure | Chaos acceptance requires reconcile-before-retry; integrate existing Kalshi ambiguity logic | `CHAOS_ACCEPTANCE_MATRIX.md` | C10 |
| 19 | Partial fills mis-accounted | Two-leg sentinel tracks matched/unmatched qty and repair state | `two_leg_sentinel.py` | T08-T11 |
| 20 | Reconnect creates phantom freshness | Complete snapshot required after connect | `stream_guard.py` | T47 |
| 21 | Clock skew | Future/stale book semantics required in venue adapters; chaos case | `CHAOS_ACCEPTANCE_MATRIX.md` | C14 |
| 22 | 429 retry storm | Independent venue health + rate-limit RED; no SLA widening | `freshness_guard.py` | T19 |
| 23 | New listings missed | Dependency on completion/Kalshi packages: cached catalogue + state-change path | `vendor packages` | integration |
| 24 | Settlement rules change after mapping | Rules fingerprint invalidates certificate | `settlement_guard.py` | T28 |
| 25 | Directional model remains wrong | Final readiness requires positive edge; prior negative receipts immutable | `readiness.py` | T50-T51 |
| 26 | Repeated rows inflate sample | Independent-event gate | `sample_integrity.py` | T34-T36 |
| 27 | Strategy mining finds fake winners | Preregistered candidate / holdout / PBO / DSR gate | `sample_integrity.py` | T37-T38 |
| 28 | Bad subgroup hidden by global results | Integrate regime-specific authority; readiness accepts only proven sleeves | `CLAUDE_DIRECTIVE.md` | integration |
| 29 | Digital Twin lies about fills | Twin certification gate | `digital_twin_gate.py` | T31-T33 |
| 30 | Maker selection bias | Directive requires conditional-on-fill + opportunity-cost evidence | `CLAUDE_DIRECTIVE.md` | integration |
| 31 | Execution costs measured at wrong horizon | Directive requires only observed horizons; unknown remains UNMEASURED | `CLAUDE_DIRECTIVE.md` | integration |
| 32 | Xavier hindsight | Directive preserves frozen entry counterfactuals | `CLAUDE_DIRECTIVE.md` | integration |
| 33 | Allie double-counts diversification | Canonical exposure lock + event concentration | `claim_exposure.py` | T01-T03 |
| 34 | Karen over-blocks | Correct economic value = saved loss - false-block cost | `karen_value.py` | T43 |
| 35 | Agent feedback self-reinforcement | Independent sample/holdout + Audrey governance directive | `sample_integrity.py` | T34-T38 |
| 36 | Management failure while entries continue | Directive preserves strict management rail; no new entry on stale/unprotected held positions | `CLAUDE_DIRECTIVE.md` | integration |
| 37 | Settlement losses disappear | Truth quorum + attribution/reconciliation mandate | `truth_quorum.py/attribution.py` | T12-T15,T44-T45 |
| 38 | Agent P&L double-counted | Attribution must reconcile exactly to realized P&L | `attribution.py` | T44-T45 |
| 39 | Ledger and venue disagree | Truth quorum freezes readiness on mismatch | `truth_quorum.py` | T12-T15 |
| 40 | Wrong credential in slot | Credential-class gate | `credential_guard.py` | T46 |
| 41 | Execution authority leaks | Directive preserves AST/import/process locks and SHADOW authority | `CLAUDE_DIRECTIVE.md` | integration |
| 42 | Env toggle activates live money | Directive requires code/release approval; env alone cannot grant authority | `CLAUDE_DIRECTIVE.md` | integration |
| 43 | UI reports stale green | Metric truth gate | `ui_truth.py` | T41-T42 |
| 44 | Global freshness hides held stale position | Separate held/priority/venue health requirements | `freshness_guard.py/readiness.py` | T19-T20,T50-T51 |
| 45 | Capacity extrapolated from tiny trades | Positive capacity frontier only | `capacity_guard.py` | T39-T40 |
| 46 | Positive PAPER is luck | Independent-event + lower-bound / forward evidence mandate | `sample_integrity.py/readiness.py` | T34-T38,T51 |
| 47 | Revenue target forces trades | Deployment capped at proven positive capacity | `capacity_guard.py` | T40 |
| 48 | Arb too rare for revenue reliability | Mechanism-specific capacity and profit breaker; combine only proven sleeves | `profit_breaker.py/capacity_guard.py` | T16-T18,T39 |
| 49 | Migration drift | Applied fingerprint + fresh DB + forward-only gate | `migration_guard.py` | T49 |
| 50 | Tested code differs from deployed code | Exact tested/release/deployed SHA equality | `release_guard.py` | T22-T23 |
