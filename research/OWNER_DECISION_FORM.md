# Owner decision form — funded pilot (for review; nothing chosen, nothing activated)

**Dated:** 2026-09-30, revision 3.

**What this form is:** it records your decisions. It does not activate submission.

**What stays true until you decide otherwise:**
- The three submission switches stay `False` in code until a separate yes/no on a named activation build (§E).
- Nothing has been chosen on your behalf: not the account, not the amounts, not the authorization duration, not a hedge exception, not activation.

**Where the numbers come from:** every figure below comes from the code, and is pinned by tests on the critical list.

## A · What each limit field enforces

**Scope:** limits are checked only for **entries and hedge acquisitions**, on the funded open book at the moment an order is decided. Exits and reductions are exempt.

| Field (accurate name) | Rail | What it bounds | Cap |
|---|---|---|---|
| `capital_usd` | MAX_CAPITAL_DEPLOYED | Cost of every open position, the proposed one included | 3,000 |
| `per_order_usd` (`per_market_usd`) | MAX_MARKET_EXPOSURE | Cost on **one market**. Not per order | 1,000 |
| `event_exposure_usd` | MAX_EVENT_EXPOSURE | Cost on **one event**. A primary and its same-game hedge count together | 1,000 |
| `max_exposure_usd` | MAX_CORRELATED_EXPOSURE | **The same number as capital**; the lower of the two binds | 1,000 |
| `daily_loss_stop_usd` (`cumulative_loss_stop_usd`) | MAX_DRAWDOWN | **No daily reset.** Realised losses (gains don't offset them), plus mark-to-market losses on open positions with a bid, plus the full cost of open positions with no bid, the proposed one included. It refuses only **above** its value | 1,000 |

**Capacity rules that dollar limits do not change:**
- **One open portfolio group in the whole database.** Migration 131's `bettor_funded_one_open_group` is a unique index on `(true)` where `closed_at IS NULL`. A group holds at most one PRIMARY and one HEDGE leg. So at most **one market plus its hedge** can be held at a time, for every account. The pilot trades positions one after another, not ten at once. Proven in `test_the_database_admits_one_open_group_at_a_time`.
- **No price floor is enforced.** The only bound is the probability support [0.02, 0.98], and a buy needs its price below the probability, so prices down to 1¢ are admissible. At 1¢, the 2,000-contract inventory rail binds: a $25 primary would be 2,500 contracts and is capped at 2,000, which is $20.
- **Capital-hours (72,000 USD-hours, not owner-adjustable).** Until this correction, the funded book integrated **every fill ever made, up to "now"**. Closed positions consumed this rail forever, and a multi-day pilot would have been refused on a flat book. It now integrates each fill over its holding period only. One group holds about $50, so this rail could bind only on positions held for about 60 days. Proven in `test_a_closed_position_stops_consuming_capital_hours`.

## B · Proposed pilot limits — for your review, revised

| Field | Proposed | Justification |
|---|---|---|
| `per_market_usd` | **25** | The primary is at most $25: 62 contracts at 40¢, 47 at 52.2¢, 38 at 65¢, 1,250 at 2¢. Enough contracts for partial fills, reductions and real per-contract fees; at most $25 at risk per market. |
| `event_exposure_usd` | **50** | Leaves $25 of room for a same-game hedge beside the primary. |
| `capital_usd` | **50** | One group holds at most a $25 primary and a $25 hedge. The previous $250 was unreachable under the one-group rule and implied capacity that does not exist. |
| `max_exposure_usd` | **50** | The same measured number as capital, so it is set equal and hides no tighter bound. |
| `cumulative_loss_stop_usd` | **100** | A realised-loss budget. A new unhedged $25 primary is allowed while realised losses are at most **$75**. A hedged pair (up to $50 open) is allowed while realised losses are at most **$50**. Money at risk never exceeds realised losses plus the open worst case, which is at most $100. Exits and settlement continue after the stop. |

**Hedge coverage under these limits.** The hedge is sized to at most the unpaired primary quantity. A $25 hedge cap means full coverage only when the hedge is no dearer than the primary. Otherwise coverage is about primary price ÷ hedge price, and the **unpaired remainder stays held, and valued as held**:

| Primary price → contracts | hedge 40¢ | hedge 60¢ | hedge 80¢ | hedge 98.5¢ |
|---|---|---|---|---|
| 30¢ → 83 | 62 (75%) | 41 (49%) | 31 (37%) | 25 (30%) |
| 40¢ → 62 | 62 (100%) | 41 (66%) | **31 (50%)** | 25 (40%) |
| 52.2¢ → 47 | 47 (100%) | 41 (87%) | 31 (66%) | 25 (53%) |
| 65¢ → 38 | 38 (100%) | 38 (100%) | 31 (82%) | 25 (66%) |
| 90¢ → 27 | 27 (100%) | 27 (100%) | 27 (100%) | 25 (93%) |

Your example of 62 contracts at 40¢ hedged at 80¢: 31 contracts are covered and **31 stay unpaired**. Full coverage would need an event cap of at least $75 and a per-market cap of at least $50, and the per-market cap also raises the primary's size. The table is computed with the rail code in `test_the_proposed_pilot_limits_permit_what_the_form_says.py`.

## C · Authorization duration: bounded renewal (implemented)

There are two records:
- **Your owner authorization:** you sign it, with a finite lifetime you choose (§G.3).
- **The system authorization:** expires after 24 hours, and never later than your owner authorization.

**Renewal.** The system authorization renews itself only when **all** of these hold:
- it is live, unrevoked, and within its last 2 hours;
- your owner authorization is unrevoked, uninvalidated, dated and unexpired;
- your owner authorization covers exactly the same account, venue and limit digest;
- a full re-run of `authorize` passes, rechecking account eligibility and reconciliation, calibration, limits, readiness, and your authorization.

Renewal never creates the first authorization, and never extends past your expiry. Any failed check leaves the record to expire. Every attempt is logged. Proven by `test_the_system_authorization_renews_only_under_the_owners` (12 cases).

**The scheduled call is wired** (commit c5ba055): the funded service calls renewal first on every cycle, and the heartbeat reports the outcome — renewed, or the named reason. Proven through `cycle()` in `test_the_scheduled_cycle_renews_the_authorization` (renews under a live owner authorization; lets it expire when the owner revoked). This is not yet in a released build. Until the frozen, gated build is serving and its heartbeat shows renewal, treat the pilot as a **one-day trading pilot**. The final yes/no (§E) is asked only on a build that demonstrates renewal.

**When authorization lapses, is revoked, or is invalidated:** entries and protective hedges are refused. Exits, reductions, settlement, recovery, reconciliation, learning and Xavier's records continue, provided the exit switch is on (§D).

## D · Incident procedure: stop acquisitions, keep protective management

**Which disabled switch the existing test covers.** `test_an_exit_survives_every_entry_side_lapse` runs with an expired or revoked authorization, a paused account, and the shipped state where both **entry** switches (`FUNDED_SUBMISSION_ENABLED`, `REAL_ORDER_SUBMISSION_ENABLED`) are off. It shows an exit then reaches its **own** switch, `FUNDED_EXIT_SUBMISSION_ENABLED`, and depends on nothing else. It does not send an exit, because that switch is also off in shipped code.

**New test.** `test_exits_are_sent_and_acquisitions_refused_in_the_incident_state` drives `cycle()` with only the venue transport substituted, in this state: exit switch **on**, both entry switches off, authorization revoked, account paused. The protective exit **is sent**, and a new entry is refused.

**Procedure** (none of the first three steps redeploys anything):
1. Revoke your owner authorization. This also revokes the system authorization issued on it.
2. Pause the account.
3. Stop the entry loop with the `ext_pinnacle_shadow` control.
4. Verify:
   - the readback shows the exit switch `true` and the authorization revoked;
   - Xavier's records show acquisitions blocked with the gate named;
   - exits and reductions are still dispatchable;
   - reconciliation and recovery run each cycle.
5. **Build rollback while inventory is held** goes to the prepared **exit-only build S0x**, not to S0. S0x is S0 with only the exit switch on; it is gated and readback-verified like S1. Rolling back to S0, with all three switches off, disables exits. That happens only if the exit path itself is faulty, and it is then recorded as the loss of autonomous protective management.

## E · How the activation build is verified

1. **Two candidate builds, gated before any funding:**
   - **S1:** S0 plus a commit changing only the three switch constants and the tests that pin them `False`.
   - **S0x:** S0 with only the exit switch on.

   Each diff is reviewed for exactly that and nothing else.
2. **Full gate on each**, through the production migration path, against S0's accepted report: zero new failures, all critical tests passing, and changed outcomes limited to the named pinned tests.
3. **Readiness shows nothing unmet:** P5, calibration, a reconciled account, your authorization, no open investigation, and renewal present in the cycle.
4. **Your yes/no**, naming S1.
5. **Deploy S1 only**, through `render-ops` → `deploy-api-commit` (40-hex SHA, `confirm=DO`).
6. **Readback from the live process:** `GET /api/admin/pilot-prerequisites` shows `running_build.serving_commit` = S1 and all three switches `true`, and the heartbeat's writer build equals S1.
7. **Rollback while holding:** deploy S0x, then read back the exit switch `true` and both entry switches `false`.

**Render, read live 2026-09-30 00:09Z:**
- `sportsassets-api`: autoDeploy **no**, branch `claude/session-njaewf`.
- `sportsassets-workers`: autoDeploy **yes**.
- When provisioning, use "Save only". The restart is done through `deploy-api-commit`.

## F · Valuation under an unknown void rate

An unknown void probability is **not treated as zero**:
- **No measured rate:** every action is valued at both ends of [0, 1]. A selection may be dispatched with real money only if the **same action wins at both ends**; values are linear in the rate, so it then wins everywhere between.
- **Otherwise:** the ranking is labelled a **conditional research valuation**, and funded dispatch is refused by name.
- **Acquisitions:** never admitted on an unmeasured rate.
- **Measured rate:** the winner must hold across the rate's stated uncertainty range.

Proven in `test_every_action_is_valued_on_one_measure.py` (12 cases).

**Consequence.** Until the void rate is measured (at least 40 settled fixtures from the non-funded observer), funded management acts only where its choice is robust to any void rate, and never acquires a hedge on a fixture that can void.

## G · Your decisions (one in each)

1. **Account:** ☐ A: the account the deployed key belongs to (send a label and a redacted key-page screenshot) ☐ B: another account (send a label; provision its key yourself)
2. **Limits:** ☐ accept §B (25 / 50 / 50 / 50 / 100) ☐ my values: per-market __ / event __ / capital __ / max-exposure __ / cumulative loss stop __
3. **Owner-authorization lifetime:** ☐ ___ days. It is uninterrupted only on a build that demonstrates renewal (§C); otherwise it is a one-day pilot.
4. **Hedges when a limit is reached:** ☐ (i) limits apply to hedges (current) ☐ (ii) allow hedges that lower worst-case loss (a separate, gated code change)
5. **Public trade export:** ☐ (a) keep public ☐ (b) put behind the admin token before funding
6. **Provisioning:** ☐ `FUNDED_RESOLUTION_KEY` and `FUNDED_RESOLUTION_OPERATOR` entered with "Save only", then reply "resolution credentials in place"
7. **P5:** ☐ message sent to the venue

**Not on this form:** the final yes/no on S1.
