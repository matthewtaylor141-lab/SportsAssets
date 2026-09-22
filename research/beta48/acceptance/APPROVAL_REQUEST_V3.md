# BETTOR — one consolidated approval request

Everything authorised has been finished. This is the whole of what is
left, in one place, with its scope, limits, verification and rollback.

**Release branch:** `claude/incentive-observation-release`
**Head:** `48b466a` (no service tracks this branch)
**Production:** `ba87076` on `claude/session-njaewf`, live since
2026-09-22T13:58:16Z

---

## 1. The one genuine external dependency

**The observation release cannot collect anything until a programme
manifest is captured, and nothing available to me can capture one.**

The committed `research/beta48/acceptance/incentive_manifest.json` is an
**empty capture**: `programs: 0`, freeze preview
`MANIFEST_HAS_NO_QUALIFYING_PROGRAMS`. That is fail-closed and correct —
the run would return INSUFFICIENT COVERAGE rather than fall back to a
seeded universe — but it means arming the release today collects nothing.

### Every route investigated, and why each is closed

| route | result |
|---|---|
| direct from this container | `gateway.polymarket.us` returns **403 CONNECT**; proxy reports `connect_rejected … policy denial`. Re-verified this session. |
| `incentive-manifest.yml` (written, on the release branch) | **404 on dispatch** — `workflow_dispatch` resolves the workflow file on the default branch, and this file is not on it. |
| `beta48-forward-capture.yml`, `beta48-substantive-capture.yml` | On the default branch and they **do** reach the venue, so Actions runners have egress. But each runs a **fixed** set of scripts; `beta48-substantive-capture` checks out an arbitrary `code_sha` yet still invokes `substantive_select.py` / `capture_manifest.py` / `substantive_capture.py` by name. Adding a new file at my SHA would never be executed, and repurposing one of those frozen capture scripts is exactly the unrelated modification the standing restrictions forbid. |
| `fetch-docs.yml` | Docs hosts only. |
| `beta48-capability-docs.yml`, `run85-phase2a.yml` | Run their own scripts from the default branch. |
| reconstruct from existing evidence | P3 recorded programme **parameters** (pools, discount factors, target sizes, one `programId`, one `eventStartTime`) but **not the market slugs**, so a ≥10-market allowlist cannot be rebuilt. |

### The two ways to unblock, smallest first

**(A) Allow `gateway.polymarket.us` in this environment's network policy.**
Smallest change, no repository effect, no service restart. The capture is
then one command:

```
python research/beta48/capture_incentive_manifest.py --et-date <YYYY-MM-DD>
```

Bounded at **6 unauthenticated GETs**, enforced in-process, counted
including retries and pagination, and its spent count is written into the
artifact. `/v1/incentives` takes no credentials and none are sent;
`/v1/incentives/earnings` (which is authenticated) is never touched.

**(B) Add `incentive-manifest.yml` to the default branch.**
Needs approval because `claude/session-njaewf` is the auto-deploy branch
for all three Render services, and a push there triggers a rebuild — an
empty backend diff does not establish that services will not restart.

**I recommend (A).** It is the smaller change and it touches no branch
any service tracks.

---

## 2. What approval would cover, if you choose to proceed to observation

Stated exactly, with nothing implied beyond it.

- **Deployment scope:** three services — `edge-shadow`,
  `sportsassets-workers` (`srv-d9gcv6urnols73ce6erg`),
  `sportsassets-api` (`srv-d9gcv6urnols73ce6er0`) — all of which track
  `claude/session-njaewf` with `autoDeploy=yes`.
- **Configuration:** `BETTOR_INCENTIVE_MANIFEST` pointing at the
  committed manifest path. The observation control stays **false**
  through deployment and is armed as a separate, explicit act.
- **One observation run:** a single `[midnight ET, next midnight ET)`
  window.
- **Request allowance, complete and explicit:** up to **6 preflight
  public requests** (the pre-deploy capture, out of band) **plus up to 8
  run HTTP requests** (manifest 4 / recheck 2 / retry 2), with the socket
  allowances **separate** (20 connects, 40 subscribe messages). Preparation
  requests are not free because they precede arming. Every unit is
  durably reserved through `bettor_live_control.reserve()` before
  dispatch; a crash or an overlapping worker cannot replenish it.
- **Rollback:** stop and verify first, then remove configuration.
  Configuration removal alone is not a stop.

**Not covered, and not requested:** funded orders, any trading-control
change, credential movement, or an increase in deployment authority.
`max_contracts` is 0.0 and the runtime adapter holds no order path.

---

## 3. What the observation run would and would not buy

**Would:** the live ladder with depth, and `eventStartTime` from the
incentives API — the time-to-resolution input the captured corpus lacks
entirely, and the one the economic verdict names as the missing lever.

**Would not:** evidence of live profitability. That needs our own orders
resting in the book. A public feed shows the book, never our order in it.

**Would not, yet:** a decisive economic answer. The binding constraint is
**independent events, not episodes**. The existing corpus has five; a
defensible interval needs twenty or more, which at the current capture
rate is roughly a month of collection. One day of observation is a
plumbing proof, not an economic one.

---

## 4. Where the work actually stands

| | |
|---|---|
| Shared policy, used by replay **and** runtime | done, `bettor_policy.py` + `bettor_policy_runtime.py`, with the replay's last inlined rule wired through and a regression that reproduces prior numbers exactly |
| Engine authority preserved | the policy proposes, `bettor_decision_engine` disposes; refusals are never upgraded |
| Known repairs | manifest delivery (Dockerfile **and** `.dockerignore`, verified by building the image), unequal liquidation, socket reservation at the boundary, the state-vocabulary seam |
| Prespecified evaluation | protocol frozen in `851cb23` before the split was read; one touch; result in `ECONOMIC_VERDICT_V2.md` |
| Economic verdict | **no candidate qualifies** — −74 to −100 out of sample, intervals excluding zero on the negative side |
| Next candidate | volatility gate, implemented, developed, **not validated**; prespecified for fresh data |

The honest summary for management: **the system is built and its economics
are negative on the evidence available.** The reason is measured rather
than guessed — completing maker fills as a taker costs 4.8× the maker
credit, and not completing them exposes the book to a single adverse move
that exceeds every fee saving in the corpus. The candidate that addresses
that tension exists in code and cannot be tested on any data we currently
hold.
