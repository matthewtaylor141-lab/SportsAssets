# COMPLETION REGISTER — audit A1–A9 and management requirements

**Audit reviewed:** `8ece8c86f1841b2b5dbc78bb9504337e97699508`; last production
evidence it saw was `714680b`.
**This register was built against:** `21cc622` plus the working-tree changes
described below, on branch `claude/command-center`.

Status values, and what each one costs to claim:

| status | means |
|---|---|
| **OPEN** | not implemented, or implemented and not yet exercised |
| **IMPLEMENTED** | code exists and its own tests pass. **Not** a claim about production |
| **VERIFIED** | exercised through the scheduled path AND read back from the serving build |
| **EXTERNAL DEPENDENCY** | blocked on a credential, an owner decision, or market evidence |

**Nothing in this register is VERIFIED yet.** The final gate, deploy and
production readback had not run when this was written; every row that says
IMPLEMENTED says so deliberately, and the difference between IMPLEMENTED and
VERIFIED is exactly the production readback the audit asked for.

---

## A1 · Freshness and market interpretation — **PARTIALLY IMPLEMENTED**

**Audit finding.** At audited head the entry loop asserted `LAST_BOOK_CHANGE`
from unchanged two-read observations and marked a recently received book fresh
with a 600-second-old or absent upstream stamp. Both reproduced in the pure
function. Withdrawal reported, replacement not committed.

**The finding is correct, and it understates the second half.** The replacement I
had committed (`ff2c87f`) made our own receipt instant the *whole* admission
test, which admits a snapshot the origin generated four minutes ago and returned
in 20 ms. The gate was worse than the one it replaced, in the opposite
direction.

**Current implementation.**

| component | change |
|---|---|
| `bettor_venue_currency.py` (new) | three verdicts — `ESTABLISHED` / `NOT_ESTABLISHED` / `CONTRADICTED`; three named mechanisms M1 live subscription, M2 conditional revalidation 304, M3 `Date − Age` (a **partial**: bounds the response, not the book). Refuses to derive currency from transport latency, our receipt instant, or repeated identical samples |
| `venue_http_observer.py` (new) | one httpx response hook recording a fixed whitelist of metadata headers. Never a request header, never a body; an exception inside it cannot break a read |
| `pmus.book_read` | installs the observer, so entry **and** funded exits collect the evidence |
| `workers/ext_pinnacle_loop` | `VENUE_STAMP_SEMANTICS = "UNRESOLVED"`; stamp carried as provenance and decides nothing in either direction; admission = established currency **AND** processing delay within bound, neither sufficient alone |
| `bettor_funded_activation.venue_book_age` | consumes the same module; keeps the three instants, acquisition interval, bounded skew allowance and decision lag; reports `measured` separately from `policy` |
| `bettor_funded_management.select_exit` | passes the observation and any mechanism through |
| `book_currency_evidence` | the single seam a mechanism reaches either lane through. **Supplies none today**, with the reason and the required work attached |
| admin probe | V3, captures the response contract, and states the withdrawn conclusion as withdrawn |

**Counterexamples exercised through the scheduled read path** (real
`_read_book_blocking`, real `venue_quote`, transport stubbed at the httpx layer)
— `tests/test_the_venue_response_contract_is_observed.py`:

- minutes-old cached snapshot received 1 s ago → `CONTRADICTED` from `Date − Age`
- absent stamp, no headers → `NOT_ESTABLISHED`, `unmeasured: True`
- uncached fresh response alone → refused, recorded as a **partial**
- established book we then sat on → refused under **our** name
- 304 revalidation → admitted
- future clock, malformed stamp, absent stamp, acquisition delay → funded side,
  `tests/test_one_shared_freshness_rule_and_the_audit_findings.py`
- delayed submission and concurrent reservation wait → pre-existing and still
  passing, `tests/test_the_funded_lifecycle_is_complete.py` §11, including a real
  `FOR UPDATE` held on a second connection with **zero** adapter calls

**Remaining work.** Contract identity: the audit requires venue, competition,
fixture date, payout event, orientation, market structure, period and settlement
scope. The resolver establishes most of these (`premap.resolve` — exact keys,
date agreement, side expansion, `payout_event`, `sports_type` scope tokens,
period via `sportsMarketType`). **I have not audited that list element by element
against the resolver**, so I am not claiming it closed. → **OPEN sub-item.**

**Evidence required to reach VERIFIED:** deploy the exact SHA and read back a
completed scheduled cycle whose candidate ledger shows
`VENUE_BOOK_CURRENCY_NOT_ESTABLISHED` with the mechanisms it lacked named.

---

## A2 · Monetary accounting — **PARTIALLY IMPLEMENTED / EXTERNAL DEPENDENCY**

**Audit finding.** Expected fees use half-up; current published policy uses
half-even with cumulative taker-fill treatment. Independent counterexample
differs by one cent.

**The finding is correct and I cannot verify the schedule from here.** Outbound
HTTPS to `docs.polymarket.us` is denied by this environment's egress policy
(`CONNECT` → 403). I will not change a fee arithmetic to match a page I have not
read; that is the same error as the `min(p, 1−p)` defect this module already
records.

**Implemented.**

- `ROUNDING_UNRECONCILED` on every `expected_fee` answer, with
  `roundingVerified: False`, the half-even figure beside the half-up one, and
  `roundingIsMaterialHere` on an exact tie.
- Six discriminating vectors computed from the documented formula with both modes
  evaluated **independently** — `(120, 0.50) → 2.09 vs 2.08`, `(280, 0.50) →
  4.87 vs 4.86`, and four more. One collected fee at any of them closes the
  question.
- `CUMULATIVE_TAKER_ADJUSTMENT_UNRECONCILED`; a multi-fill expectation is marked
  `PROVISIONAL` with the error bounded at one cent per fill.
- `FEE_ARITHMETIC_IS_EXACT = False` and `FEE_ARITHMETIC_EXACTNESS_BLOCKED_BY`, so
  no report may call the arithmetic exact while this holds.
- Already present and unchanged: `expected_fee` / `reserve_allowance` /
  `collected_fee` are three separate quantities that may never be compared for
  equality; `Decimal` throughout.

**OPEN.** Effective-date versioning of the policy; product-specific schedules;
execution-report units; and a defensible unrealised mark. Unrealised is currently
reported **UNMEASURED**, which is honest but is not the defensible mark the audit
asks for.

**EXTERNAL DEPENDENCY.** Reading the current published schedule, or one observed
collected fee on a discriminating vector.

---

## A3 · Activation / onboarding integration — **IMPLEMENTED, PENDING VERIFICATION**

**Audit finding.** The activation package promised a live onboarding
reconciliation through readiness; the reviewed endpoint read registry/market
checks, and the prerequisites endpoint returned `onboarding.describe`. No call
from these routes to `onboarding.reconcile` or `mark_eligible`.

**Verified as correct by inspection.** `grep` across the package found no caller
of `reconcile` outside the module's own file. The four reads were complete, good
code, and dead.

**Current implementation.**

- `POST /api/admin/funded-account-reconcile` — the documented operator path.
  Performs the four venue reads, compares against this system's own book, and
  **persists** source, account identity, retrieval instant, completeness,
  verdicts and discrepancies. Submits no order. Writes **nothing** to the account
  row, so a read can never promote an account. Defaults to the bound account, so
  an operator cannot reconcile one account and read readiness against another.
- `GET /api/admin/funded-account-reconciliation` — the record, its **age**, and
  whether it still holds.
- `EVIDENCE_MAX_AGE_S = 7200`, labelled a **policy allowance** ("a chosen bound,
  not a derived one").
- Three separate refusals because each needs a different action:
  `NO_RECONCILIATION_EVIDENCE_HAS_EVER_BEEN_RECORDED`,
  `THE_RECONCILIATION_EVIDENCE_IS_OLDER_THAN_ITS_BOUND`,
  `THE_RECORDED_EVIDENCE_IS_FOR_A_DIFFERENT_ACCOUNT`.
- `_completeness()` refuses an unanswered **or partially paged** read.
- `readiness`'s `account_selected_and_clean` now requires the registry row to be
  clean **and** a complete, passing, in-bound reconciliation for that account;
  `registry_flag_is_not_evidence` states why both.
- The readiness route's docstring says plainly that it does **not** contact the
  venue and does **not** rerun reconciliation.

**Tests:** `tests/test_the_audit_findings_are_closed.py` — 19 pass, including a
real-database record → read-back → other-account → expiry cycle.

**Unchanged, deliberately:** `acct_fc2d773a2afa4851` stays paused. Its only route
out is `resolve_existing`, which runs the same four reads and unpauses only if
they pass. No flag, endpoint or argument unpauses it because someone decided to.

**Evidence required to reach VERIFIED:** one run against a live credential,
whose reads will currently all record `UNREADABLE` — which is the reconciliation
working, not a gap in it. → **EXTERNAL DEPENDENCY** for the live run.

---

## A4 · Risk contract — **PARTIALLY IMPLEMENTED**

**Why PARTIALLY and not IMPLEMENTED.** Units, the loss window, the aggregation
scope and the single limit list are repaired. **Cross-lane account exposure is
not**, and that is not a detail: `SCOPE_WHOLE_BOOK` currently means *this lane's*
open book. Two lanes drawing on one account can each satisfy their own rails and
together exceed the account's. A finding is not closed because part of it is.

**Audit finding.** Event limit supported in execution but not required by
activation; "daily loss" is drawdown; residual inventory is contracts but the
proposal displays dollars.

**All three confirmed, and I found a fourth.** `REQUIRED_LIMITS` existed in
**three** places — four names in `bettor_desk_controls`, four in
`bettor_funded_activation`, five in `bettor_entry_execution`. That is *how*
`event_exposure_usd` became enforceable but not requestable.

**Current implementation.**

- `EX.RAIL_TYPES` — one typed declaration per rail: unit, window, aggregation
  scope, what it measures, and its owner field. `RAIL_TYPES_COVER_EVERY_DECLARED_RAIL`
  is asserted True.
- Units: `USD`, `CONTRACTS`, `USD_HOURS`. `format_rail()` is the single formatter,
  so a contract count cannot acquire a dollar sign on its way to a screen.
  `MAX_RESIDUAL_INVENTORY` → `"2000 contracts"`; `MAX_CAPITAL_HOURS` → `"72000 $·h"`.
- `MAX_RESIDUAL_INVENTORY` and `MAX_CAPITAL_HOURS` carry `owner_field: None` and
  `not_owner_approvable` — no approval reaches them, and they no longer appear to.
- `MAX_DRAWDOWN`: `window = CUMULATIVE_OVER_THE_OPEN_BOOK_NO_RESET`, with
  `the_owner_field_name_is_inaccurate` ("it says daily… no reset") and
  `a_real_daily_window_would_need` a date-bounded query plus a declared reset
  boundary with a timezone. **Neither exists and neither is invented.**
  `cumulative_loss_stop_usd` accepted as the accurate synonym; the old key kept
  because recorded approvals use it.
- `EX.typed_limits()` — the side-by-side readback: frozen, owner-proposed,
  effective, each with its unit and display, plus both digests.
- All three `REQUIRED_LIMITS` are now one list, pinned equal by test, with the
  concrete consequence shown: without `event_exposure_usd`, a $25 per-order
  approval leaves `MAX_EVENT_EXPOSURE` at $1,000; with it, $25.

**OPEN.** Account-wide treatment when multiple lanes share capital; outstanding
orders, unresolved submissions and reserved exits are counted by the funded path
but the typed schema does not yet declare *which* lanes each rail aggregates over
beyond `EVERY_OPEN_POSITION_IN_THIS_LANE`.

---

## A5 · Valuation qualification — **OPEN / EXTERNAL DEPENDENCY**

**Audit finding.** Whale completion models are not settlement models; source
calibration, contract compatibility and current evidence remain necessary; no
qualified funded opportunity established.

**Correct, and the code already says so.** `bettor_model_inventory` reports
`scorer_exists_for_entry: false`, `feeds_the_entry_gate: false`, fitted targets
`COHORT_COMPLEMENTARY_FILL_WITHIN_H` and
`COHORT_COMPLEMENTARY_FILL_BELOW_PARITY_WITHIN_H` — both **execution** questions
— against a required `EVENT_SETTLEMENT_PROBABILITY`. No fit result can change a
trading rule.

**The measured cause of zero opportunities**, from 1,018 valuations:

| refusal | count | kind |
|---|---|---|
| `VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE` | 419 | established payout conflict — **must not be waived** |
| `NO_ACTION_HAS_POSITIVE_NET_EDGE` | 967 | established economics: p ≈ ask, 2-cent cost closes it |
| `NO_OBSERVED_DEPTH_INSIDE_THE_BREAK_EVEN_LIMIT` | 438 | established economics |
| `VENUE_QUOTE_STALE` | dominant first refusal | **was** an engineering defect; A1 replaces it with `BOOK_CURRENCY_NOT_ESTABLISHED`, which is still engineering |

**Note the arithmetic, because it bounds what A1 buys:** the payout conflict and
the absent edge end every recent candidate *independently*. Clearing A1 makes the
lane **evaluable**; it will not by itself produce a trade. Nothing more is
claimed for it.

**OPEN.** Prospective source calibration with independent fixtures and
predeclared criteria — `external_source_calibration` has no passing measurement,
so `MODEL_TRUST_DRIFT` reads `NOT_EVALUABLE`.

---

## A6 · Frontend credential design — **PARTIALLY IMPLEMENTED**

**Audit finding.** Legacy MERIDIAN retains browser-readable provider/admin
credentials in `localStorage` while newer command controls use scoped HttpOnly
cookies.

**Inspected, confirmed, and worse than stated.** `frontend/src/pages/Jarvis.tsx`
kept three long-lived secrets in `localStorage`:

| key | what it grants |
|---|---|
| `meridian_anthropic_key` | billing-bearing access to the owner's Anthropic account, sent direct to `api.anthropic.com` under `anthropic-dangerous-direct-browser-access` |
| `meridian_eleven_key` | the ElevenLabs provider account |
| `meridian_admin_token` | **API-wide admin authority** |

**The part the audit did not say, and it is the sharpest version of the finding:**
`frontend/src/lib/desk.ts` states in its own header that tokens "live in
sessionStorage only (never localStorage, never logged)". MERIDIAN then wrote the
admin token to `localStorage` anyway. Not two designs coexisting — the newer
one's stated invariant being broken by the older page beside it.

**Why it matters without a demonstrated XSS.** `localStorage` is durable and
origin-readable. It survives every tab close and reboot, so a single script
injection at any future moment reads a credential typed months earlier.

**Implemented.**

- All three secrets moved to `sessionStorage`. Preferences (voice, speed, voice
  id) stay durable — they carry no authority.
- `purgeDurableSecrets()` runs on every load: an existing install's durable copy
  is **moved** to `sessionStorage` and **deleted** from `localStorage`. An install
  predating this change is repaired, not merely stopped from worsening.
- Secret reads no longer fall back to `localStorage`, and every save removes the
  durable key again — so a durable copy cannot be re-admitted through an older
  tab, a stale bundle or a hand-edited value.
- `claude.ts`'s header said "kept in localStorage". Corrected, with the residual
  risk stated rather than implied away.
- Reviewed and **acceptable as-is**: `sa_wall_token` is durable in `localStorage`
  by deliberate design — scope `wall:`, read-only, 7-day, because an office TV
  must survive a reboot without a keyboard. That is a scoped credential with a
  documented reason, which is what the audit asks for.

**Honest limit of this repair.** Session-scoping reduces the window from "forever"
to "this tab". `sessionStorage` is **not** a security boundary: a provider key in
the browser at all is readable by script on this origin while the tab is open.

**OPEN.** The real repair is a server-side proxy holding the provider key behind a
scoped streaming endpoint — a backend change, **not done**. Also OPEN: the
cross-role test matrix the audit asks for (read vs control vs admin, session
expiry, revocation, origin protections, direct unauthorized calls). The
server-side checks exist — `require_admin`, `require_command`, the control-cookie
split, and a readback asserting a read cookie is refused on a control route — but
I have not assembled them into one deliberate matrix.

**Not deployed.** The static frontend release is independent of the API release,
so this ships only when the frontend is published, which remains separate.

---

## A7 · Reliability and deployment — **PARTIALLY IMPLEMENTED**

**Implemented.** PostgreSQL 18 migration validation in CI (`funded-pg18`, the
version production runs); funded schema as an enforced release condition — every
submission path refuses when a required object is missing, while diagnostic and
general API availability is preserved; explicit-SHA API-only deploy; the
protected worker pinned at `f5d1c05`.

**Measured this session.** Full suite against the corrected tree: **175 failure
identities vs the recorded 177 baseline — one new, now fixed; four baseline
failures now pass.** The new one was a test asserting `VENUE_TRANSACT_TIME` *is*
an establishing basis, which the A1 correction makes false.

**A real finding I should record against myself:** PostgreSQL died mid-session
from a full filesystem (98% used, 1.1 GB free), which produced a wholesale
DB-test failure that looked like a code regression and was not. 43 stale gate
checkouts were consuming ~13 GB. Freed; a release comparison must not share a
disk with its own history.

**OPEN.** Investigating the ~175 baseline failures and either resolving the
capital-critical ones or quarantining each with a named owner and rationale. The
audit is right that an unchanged failure count is regression evidence, not
readiness. I have an identity-level diff, which is stronger than a count, and I
have **not** triaged the standing 175.

---

## A8 · Product and capacity claims — **PARTIALLY IMPLEMENTED**

**Implemented in the Command Centre operating view:** serving SHA and arm state;
per-candidate funnel with the first refusal that ended each one; order and fill
counts read from the order tables beside the valuation count, with
`a_valuation_row_is_not_an_order` stated on the payload; three books side by side
and never summed; P&L completeness with provisional and UNMEASURED preserved;
activation blockers; and binding blockers each labelled by **kind** and **owner**
so "engineering has not finished" is never displayed as "the two venues do not
pay on the same event".

**OPEN.** End-to-end capacity measurement across discovery, provider budgets,
venue requests, execution, management and accounting, with bursts, retries,
downtime and process recovery. Writer throughput does not establish opportunity
volume, and I have not measured the former as the latter. Also open: the
action-by-action capability matrix tied to the actual venue — in particular that
institutional native merge is `NOT_IDENTIFIED` and retail auto-netting is not a
merge.

---

## A9 · Shadow exposure — **IMPLEMENTED, PENDING VERIFICATION**

**Audit finding.** The recorded shadow open-book query lacks event identity for
cross-position event exposure, while the newer funded path carries its own event
key.

**Confirmed, and the consequence is worse than "lacks".** `exposure_from_rows`
sums a row into `MAX_EVENT_EXPOSURE` only when `r["event_key"]` equals the
candidate's. With the column unselected, that key was `None` on every row and the
condition never held — so the rail was not lenient, it was **reading a quantity
that was never selected**. Reproduced: two open $40 positions on one fixture plus
a proposed $10 gave `MAX_EVENT_EXPOSURE = 10.0` and a $25 cap looked satisfied;
with the identity present it reads **90.0** and the cap is breached.

**Current implementation.**

- `OPEN_BOOK_SQL` joins `us_premap` on `market_slug = p.venue_market_slug` and
  selects `event_slug AS event_key` plus `event_key_resolved`.
- The candidate's key now comes from **the same column** —
  `resolve_venue_identity` reads `us_premap.event_slug` for the contract. This
  matters: passing the odds provider's `event_id` would have left the rail at
  zero even on a fully repaired query, because the two are different namespaces.
- `event_exposure_is_measurable()` — an unresolvable row makes the rail
  `NOT_EVALUABLE` and the plan refuses with
  `OPEN_BOOK_ROW_HAS_NO_RESOLVABLE_EVENT_IDENTITY`, rather than reporting a
  partial sum and calling the rail satisfied. Settled rows are excluded because
  they occupy no exposure.

**The audit's warning is respected:** the funded fix did not repair this, and
this fix is the shadow lane's own.

---

## Management requirements beyond A1–A9

| requirement | status | note |
|---|---|---|
| Bettor selects its own candidates | **IMPLEMENTED** | live, ~556 markets/cycle |
| Bettor executes its own trades | **OPEN** | zero autonomous orders. A1 blocker + A5 economics |
| Bettor manages its own positions | **IMPLEMENTED** (modelled) / **OPEN** (funded) | scheduled servicing proven end to end against a **substituted transport**; never against a live funded account |
| Reconciles its own P&L | **PARTIALLY** | three books separate, never summed; fee exactness blocked (A2); unrealised UNMEASURED |
| Improves through measured results | **OPEN** | two execution targets fit prospectively; no promotion path to trading authority, by design |
| Supports controlled capital deployment | **EXTERNAL DEPENDENCY** | account, credential, approved typed limits, activation — all owner decisions, and A1/A2/A6/A7 engineering still open |
| Separate owner-authorized risk-reduction procedure when valuation is unavailable | **OPEN** | the audit is right that this must not hide behind HOLD. Not implemented |
| Learning/revenue-validation loop with challenger promotion | **OPEN** | not implemented |

---

## The three verdicts the instruction asks for

**1 · Engineering ready — NO.** A1's contract-identity sub-item, A2's schedule
verification and unrealised mark, A4's cross-lane aggregation, A6's server-side proxy and role matrix, A7's
baseline triage and A8's capacity measurement are open.

**2 · Controlled funded qualification ready — NO.** No eligible reconciled
account (the only one is paused with unresolved accounting), no credential, no
approved typed limits, and the reviewed release has not been deployed or read
back.

**3 · Strategy validated for scaling — NO.** Zero prospective autonomous trades.
No calibrated source. A payout conflict and an absent edge that more engineering
cannot fix.

**Nothing here is a request for owner action yet**, because the authorized
engineering is not finished. When it is, the account, limits, remaining evidence
and activation steps will be presented together, as one package.

---

## Added after the register's first version — the permission matrix (A6)

Building the deliberate read/control/admin matrix the audit asked for
(`backend/tests/test_the_permission_matrix_is_deliberate.py`) found something
reading routes one at a time would not have.

**The matrix classifies every route by the guard it actually carries**, read out
of the app's own dependency table — so a route that forgets its guard fails a
test instead of being discovered by whoever calls it first. Exercised against the
real ASGI app: unauthenticated calls on all three classes, a read credential on a
control route (403 with the named reason), a control cookie on an admin route
(refused), a wrong admin token, and an unset admin secret — which must refuse
everyone rather than admit everyone.

**One capital-relevant hole, found and fixed.** `GET /api/pmus-account` returned
the funded account's **value, cash, open positions, realised P&L and recent
trades** to any unauthenticated caller. It is a GET, so it moved no money — and
it disclosed the entire state of the account the money sits in. Now behind
`require_desk`, with its frontend caller sending the desk token. The static
frontend releases separately from the API, so between those two releases that
page's account panel will 401; that is the correct failure and it is stated
rather than left to be found.

**A standing residue of 46 routes, frozen and named — OPEN.** These carry no
dependency guard and no recorded inline check. **Six of them accept a write with
no credential:**

| route | method |
|---|---|
| `/api/engine/manual-kalshi-result` | POST |
| `/api/engine/kud-result` | POST |
| `/api/engine/methodology` | POST |
| `/api/push/subscribe` | POST |
| `/api/push/unsubscribe` | POST |
| `/api/prefs/{user_key}` | PUT |

The other 40 are public reads — legacy copy-trading and report routes
(`/api/track-record`, `/api/whales/*`, `/api/venue-truth`, `/api/copy-report`
and similar).

**Why they are frozen and not fixed.** The protected copying machinery calls some
of them, and adding a guard to a route whose caller does not authenticate breaks
production quietly. Each needs its caller identified first. The set is asserted to
match **exactly**, so a new unguarded route fails and guarding one requires
visibly removing it from the inventory — a ceiling alone would let one be fixed
while another appeared and still report progress.

**This is the part of A6 that "purging localStorage" does not touch**, and the
instruction is right about that. Server-side authority for the provider key is
still OPEN; so is this residue.

---

## Corrections to this register's own previous version

**I over-reported the unauthenticated writes.** The earlier entry said six. Three
of them — `/api/engine/manual-kalshi-result`, `/api/engine/kud-result`,
`/api/engine/methodology` — **already call `check_engine_token` inside the
handler**, and the edge-engine callers already send `X-Engine-Token`. My matrix
read only the dependency table, so it reported authenticated routes as naked.
That is a false positive in a security inventory, which is the failure that makes
an inventory get ignored. The matrix now detects inline credential comparisons
from the handler's own source, and the residue is **37 routes, 3 of them
writes** — not 46 and 6.

**The three genuine unauthenticated writes, traced to callers, effects and
audience:**

| route | caller | effect | audience |
|---|---|---|---|
| `POST /api/push/subscribe` | `frontend/src/lib/push.ts` | inserts a push endpoint under a client-named `user_key` | anonymous browser |
| `POST /api/push/unsubscribe` | `frontend/src/lib/push.ts` | **was** `DELETE … WHERE endpoint=$1` — no ownership check | anonymous browser |
| `PUT /api/prefs/{user_key}` | `frontend/src/pages/Alerts.tsx` | overwrites notification preferences for a UUID key | anonymous browser |

**One of the three was fixed, and it was the one that mattered.**
`push/unsubscribe` deleted by endpoint alone, so anybody who learned or guessed a
push endpoint could silently switch off somebody else's alerts. It now requires
the owning `user_key`, refuses a request without one rather than defaulting, and
returns the same answer for a wrong key as for an already-removed row — telling
them apart would confirm that an endpoint exists under another key. The frontend
caller sends the key in the same change.

**The residual, stated rather than papered over.** A client-generated UUID is not
a strong credential: a caller holding both the endpoint and the key is
indistinguishable from the owner, and `PUT /api/prefs/{user_key}` still has no
authorization beyond knowing the key. These carry notification preferences, no
capital and no market data. Proper per-user authentication for them is **OPEN**.

**No coordinated release is required for any of this.** The engine routes were
already authenticated end to end; the push change is compatible because the
frontend holds the key already. The protected worker is untouched.

---

## A1 · M1 verified — and it does not hold on this feed

Asked to show what M1 actually establishes, its primary source, and the
production reader supplying its evidence. The answer is a refusal, and it is the
right one.

**Primary source: the shipped client**, `polymarket_us.websocket`, inspected —
because the SDK the image installs is the contract this system is bound by,
whatever any prose says. `bettor_stream_currency.FEED_CONTRACT` records it and
`tests/test_m1_is_verified_not_asserted.py` **re-derives every field from the
installed SDK**, so a contract change breaks a test instead of silently
invalidating a verdict built on it.

**M1 needs four preconditions. Two are absent:**

| | precondition | status |
|---|---|---|
| P1 | full-replacement authority | **NOT ESTABLISHED** — nothing distinguishes a snapshot from an increment: no `type`, no `isSnapshot`, no `action`. The "full order book" claim is a *docstring*, and `obs/streamstate.DepthAuthority` already refuses that inference in this repository's own words |
| P2 | connection liveness | available — a `Heartbeat` type exists |
| P3 | gap-free continuity | **NOT AVAILABLE** — no sequence number, message id or continuity field of any kind. A dropped message leaves no trace, so a gap cannot be detected, only assumed absent. `transactTime` cannot substitute: its meaning is unresolved, and even a last-change stamp would not reveal a message missing *between* two changes |
| P4 | instrument identity | available — `marketSlug`, compared not assumed |

Also verified: `base._message_loop` emits `close` and **returns**. The client does
not reconnect, resubscribe or resynchronise — all three are the caller's
responsibility.

**So M1 is `M1_NOT_AVAILABLE_ON_THIS_FEED`, and the refusal is not relieved by
passing a `subscription` argument** — the missing preconditions are properties of
the feed and a caller cannot supply them.

**The production reader is real.** `bettor_stream_currency.evidence_for` is what
`book_currency_evidence` calls; it returns `None` and names *which guarantee* is
missing, which distinguishes "the feed cannot" from "nothing is wired" — different
pieces of work.

**Built anyway, and correct regardless:** connection-epoch invalidation. A book
held across a drop has an undetectable number of missed updates in front of it, so
every market's state is **discarded, not aged**. The verdict is **computed** from
live state, not hard-coded: a test flips the two feed properties and the same code
admits, on a number stricter than anything this lane has used.

**Consequence, stated plainly:** the lane continues to refuse every candidate on
`VENUE_BOOK_CURRENCY_NOT_ESTABLISHED`. M2 remains the nearer route and needs the
book endpoint to emit `ETag` or `Last-Modified`, which the V3 clock probe reports
per read and which has not been run yet.

---

## Gate safety (correction 4)

`ops/gate/frozen_gate.sh`: a per-checkout **lock naming its PID** — a live lock is
never pruned whatever its age, because "keep the newest two" would delete a
concurrent baseline run underneath itself; `PROTECT_DIRS` for the baseline by
name; busy databases skipped by `pg_stat_activity`; evidence copied out **before**
any prune. Migrations now run with `ON_ERROR_STOP=1` and a failure **voids** the
gate — the previous version sent migration errors to `/dev/null`, which is exactly
how the PostgreSQL 18 rollback produced a suite full of plausible failures. The
database and free space are **sampled every 15 s throughout the run**, not once at
the end, and any outage, any drop below 500 MB, or an overall timeout **deletes the
identity list and exits non-zero** — an invalid environment must not leave a
usable failure count behind.
