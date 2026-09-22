# BETTOR — deployment / observation approval request

**One request. Everything authorized is finished.**

| | |
|---|---|
| **Release SHA** | **`0228b1027f6c2e60f9a6d5451c45de209ad08c3d`** |
| branch | `claude/incentive-observation-release` — **no service tracks it** |
| production now | `ba87076` on `claude/session-njaewf`, live since 2026-09-22T13:58:16Z |
| manifest | captured 2026-09-22T21:43:59Z, Actions run **35788331780**, from commit `30c7ae1`, committed in `5cfd211` |
| image | built from this SHA and verified from inside the container |

---

## 1. What is being asked for

**Deploy this SHA to three services, set one variable, and run one
observation day.** Nothing else.

- **Services** (all track `claude/session-njaewf`, `autoDeploy=yes`):
  `edge-shadow`, `sportsassets-workers` (`srv-d9gcv6urnols73ce6erg`),
  `sportsassets-api` (`srv-d9gcv6urnols73ce6er0`).
- **Configuration:** one variable —
  `BETTOR_INCENTIVE_MANIFEST=research/beta48/acceptance/incentive_manifest.json`.
  The observation control stays **false through deployment**; arming is a
  separate explicit act.
- **Scope of the run:** one `[midnight ET, next midnight ET)` window over
  the 12 frozen markets.

**Not requested and not authorized by this:** funded orders, any
trading-control change, credential movement, or any change to the
default branch. `max_contracts` is 0.0 and the runtime adapter has no
order path.

### Request allowance, complete

| | |
|---|---|
| preflight public | cap 6 — **6 spent, 0 remaining**, durable in `preflight_allowance.json` |
| run HTTP | up to 8 (manifest 4 / recheck 2 / retry 2), reserved via `bettor_live_control.reserve()` before dispatch |
| socket | **separate** — 20 connect attempts, 40 subscribe messages, reserved at the connect/subscribe boundary |

The preflight allowance is **exhausted**. A re-capture would need it
raised, which is a decision, not a retry — the script refuses.

### Rollback

Stop and verify first, then remove configuration. Configuration removal
alone is not a stop: the observation control would still be true and the
worker would return to the general discovery loop.

---

## 2. Verified from inside the built image

```
load through the real startup path: True OK
  programs    441 | et_date 2026-09-22 | authenticated False
  freeze      True -- OK
  markets     12 | programmes 1 | events 1
  slugs[0:3]  ccpc-bilbrd-1album-any2026-{alewar, benboo, beyonc}

RUNTIME, on a frozen slug, trading disabled:
  proposal    QUOTE_BOTH_SIDES  bid 0.41  offer 0.43  size 100.0
  rule        entry (DERIVED_FROM_CASE_STUDY)
  engine      PROPOSED_BUT_NOT_SCORED -- MAKE_YES/MAKE_NO
              NOT_IDENTIFIED -- P_FILL_NOT_IDENTIFIED
  effective   NO_TRADE 0.0 | executable False
```

The shared policy proposes; `bettor_decision_engine` disposes; the
effective action is the engine's. That is the runtime path, in the
image, with trading disabled.

---

## 3. What this run can and cannot answer

**Full detail in `OBSERVATION_SCOPE.md`. The short version:**

The captured population is **12 Billboard "#1 album 2026" markets, one
programme (`culture_low_20260921`), one event start time**. Twelve
markets is not twelve independent units.

**It can measure incentive opportunity** — the score denominator
(aggregate resting size walked from best outward), which nothing we hold
has ever measured on a market actually inside a programme. That turns
the reward figures in `ECONOMIC_VERDICT_V2.md` from transferred
scenarios into measurements for these markets. It also exercises the
shared policy's admission surface against real ladders.

**It cannot evaluate the football inventory policy.** Different
instruments, different horizon, no resolution inside the window.
Reporting anything about C3 / R10 / R14 from this run would be answering
a different question while looking like an answer to this one.

**The one frozen policy it can score** is the `BETTOR_POLICY_V1` *entry
surface* — `admit()` plus the two gates — as an admission rate and reason
distribution, alongside the realised incentive score share. Prespecified
in `OBSERVATION_SCOPE.md` before the run.

### A claim withdrawn

`eventStartTime` **is not a resolution timestamp.** The captured value is
2026-12-27T04:59Z — three months past the window. Its meaning is not
constant across categories. The previous handoff said this field supplies
the time-to-resolution input the corpus lacks; that was wrong.

---

## 4. Evidence status, corrected

**The independent-holdout claim is withdrawn.** `policy_final.json`
already swept C0/C2/C3/C4 across four queue fractions over the *whole*
tape including 17–20 September, and the C3 baseline was selected using
it. A protocol committed afterwards does not make those dates untouched.

Every figure in the evaluation is a **development diagnostic**.
`evaluation.json` now carries `qualifies: false` regardless of the
screen. The negatives are preserved as diagnostics, not retuned.

**"Confident negative" is withdrawn too.** Five event clusters do not
settle the mechanism in either direction. What the numbers support:
*this policy, on these five events, lost money, and the decomposition
shows where.*

**What survives independently of the split:** the fee decomposition.
Θ_taker/|Θ_maker| = 4.8 (JUL2026) / 5.6 (SEP2026) is arithmetic on the
published schedule; the per-episode cash split is an accounting identity
over observed fills.

---

## 5. The bounded execution experiment the strategy actually needs

**`P_FILL` is an unresolved execution-model requirement, not a
conservative setting.** The engine refuses to score MAKE_YES/MAKE_NO
because fill probability is not identified, and NOT_IDENTIFIED is not
zero. No amount of observation supplies it: a public feed shows the
book, never our order in it.

Qualifying the strategy requires **resting our own orders and observing
our own fills** — the smallest version being a bounded maker experiment
on a fixed market set, with a hard contract cap, a fixed wall-clock
window, and a kill switch, measuring only: did a resting quote at a
known price and queue position fill, and when.

**That is a separate authorization and is not requested here.** I am not
attaching a sample size or a date to it, and I am not claiming any number
of events or weeks would establish profitability.

---

## 6. Test status, stated exactly

The full backend suite: **445 failed / 10,674 passed / 143 skipped** in
24 minutes. I sampled the reported failures
(`test_render_ops_take_band`, `test_shadow_v2`, `test_workers_boot_stagger`,
`test_s4_review_pins`) in isolation — 6 failed / 127 passed — and ran the
**same selection on `ba87076`**, the production SHA with none of this
work present: **identical 6 failed / 127 passed.** Those are pre-existing.

**Not established: attribution of all 445.** That needs a full baseline
run, which is broad testing and was not authorized. Targeted suites for
the changed code pass: 44 policy/runtime tests, 84 engine/adapter tests,
the manifest suite.
