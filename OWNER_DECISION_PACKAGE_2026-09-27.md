# OWNER DECISION PACKAGE — bounded funded pilot

**Nothing in this document is approved, and nothing in it has been enabled.**
Funded submission is disabled in code. This is what an owner needs in order to
decide, and the exact changes that would follow a decision.

**It asks for no secret in this conversation and contains none.**

---

## 0 · The verdict, four ways

| | verdict | why |
|---|---|---|
| **Engineering verified** | **NO** | see §6. The binding item is that no mechanism can currently establish that a venue book is current |
| **Ready for an authorized funded pilot** | **NO** | no reconciled account, no credential, no approved limits, and the release has not been gated or deployed |
| **Actual funded execution and reconciliation verified** | **NO** | **zero verified real orders from the new autonomous EV lane.** Every lifecycle proof for *this* lane used a **substituted transport**. Earlier, separate activity exists and is not this lane's record — §0a |
| **Strategy profitability validated** | **NO** | zero autonomous positions. No calibrated source. A payout conflict and an absent edge that engineering cannot fix |

No deployment, passing suite or modelled profit moves the last two.

### 0a · A correction to the scope of that third verdict

I wrote **"zero real orders have ever been sent."** That was wrong, and wrong in
the direction that flatters this lane: it erases earlier activity by making the
whole system sound untouched.

**What the evidence actually says.** `live_orders` is the funded lane's own table
and production holds **166,585 rows** in it, from the earlier live beta
(`008_live_orders_venue.sql`: *"The LIVE beta can now execute on the …"*). Those
are historical rows on a different path, under different code, at a different
time. `bettor_desk_controls` already reports them correctly —
`live_orders_rows_all_time` separate from `funded_orders_this_lane_submitted: 0`,
with a note saying the research lane submitted none of them. My prose did not
match my own instrumentation.

**The two statements, kept apart, because neither substitutes for the other:**

| | statement | basis |
|---|---|---|
| **this lane** | **zero verified real orders from the new autonomous EV lane.** Its writer `CHECK`s `order_submitted FALSE`; the three submission constants are `False`; no order-submission path is nameable from the shadow loop | tests + schema constraint |
| **legacy** | **166,585 historical `live_orders` rows exist** from the earlier live beta. They are not reconciled into this lane's books and are **not** this lane's track record | production row count |

**Both directions of the error matter.** Legacy activity must not disappear — it
happened, and it sits in a table this system still reads. And it must not become
this lane's track record — 166,585 rows of earlier beta execution prove nothing
about an EV lane that has submitted nothing. Nowhere in this package, the
register or the management report is a legacy number summed with an autonomous
one.

**What it does not change.** The verdict stays **NO**. *This* lane's funded
execution and reconciliation are unverified, and a legacy row count cannot
verify them.

---

## 1 · The bounded initial operating scope being proposed

This is the scope I propose; it is not in force. Each restriction is named with
**where it is enforced**, because a restriction the account can reach around is
not a restriction.

| | proposal | enforced where |
|---|---|---|
| **venue** | exactly one: `PMUS` (Polymarket US institutional) | `bettor_funded_activation.venue_class` — a venue outside the map refuses by name; `authorize` records the venue in the binding and submission compares against it |
| **account** | exactly one named account, bound under `ACCOUNT_KEY` | `account_selection` + the reconciliation evidence check (§4). A second account is not reachable without a new binding and a new reconciliation |
| **contracts** | money-line (`h2h`) only, full-game scope only, on the two sports the lane maps | the resolver: `premap.resolve` returns a contract only on an exact key match with date agreement; `sportsMarketType` scope tokens gate period. A contract outside this refuses `NO_VENUE_NATIVE_CONTRACT_IN_PREMAP` |
| **settlement** | only where the venue's and the bookmaker's payout rules are **COMPATIBLE** | `_settlement_compatibility` — `UNKNOWN` and `INCOMPLETE` both refuse. This is the refusal that ends 419 of 1,018 valuations and it must not be waived |
| **direction** | opening long exposure only; exits reduce | `bettor_entry_execution` action mapping; exits run through `bettor_funded_management` and cannot open |
| **limits** | §3, as the element-wise minimum of frozen and approved | `EX.effective_limits` takes `MIN(frozen, approved)` — approving a **larger** number than the frozen rail changes nothing |

**What "genuinely unreachable" rests on, stated honestly.** Three independent
server-side gates, any one of which refuses: the three submission constants are
`False`; the process-bound `execution_gate` authorises every submission at the
venue boundary; and without `PMUS_KEY_ID`/`PMUS_SECRET_KEY` **no client can be
constructed at all**. A test asserts no order-submission path is even *nameable*
from the shadow loop.

**One gap in the scope claim, and my earlier mitigation for it was wrong.**
`MAX_EVENT_EXPOSURE`'s aggregation scope is
`EVERY_OPEN_POSITION_IN_THIS_LANE`. Two lanes sharing one account can each
satisfy their own rails and together exceed the account's.

I wrote: *"Until it is, the pilot procedure below holds **one position at a
time**, which makes the gap unreachable in practice rather than merely
unlikely."* **That is withdrawn.** One position *in this lane* is a fact about
this lane. The venue sees one account, and what the legacy copier, the manual
sleeve, or an unresolved submission has already committed there is unaffected by
how many positions this lane holds. The mechanism I leaned on — the one-live-intent
guarantee — is a **UNIQUE INDEX on `bettor_funded_intents`**, and a row in
`live_orders` does not violate it. It bounds this lane's rows, not the account's
risk.

**There are two honest routes and `bettor_account_exposure` implements the
machinery for both**, rather than choosing one by assertion:

| route | what it requires | state |
|---|---|---|
| **ENFORCE** | measure exposure across every path sharing the account and gate on the total | **built.** `account_exposure()` reads this lane, `live_orders` (which since migration 014 carries *both* the legacy copier and the manual sleeve), and the venue. It counts **held contracts, working orders and unresolved submissions** — the last at **full requested size**, because an unknown outcome treated as zero is how a timed-out submission becomes a double position. It **fails closed**: a required path that cannot be read makes the total `UNREADABLE`, never a partial sum that looks like a total |
| **ISOLATE** | demonstrate that no other automated or manual path *can* add exposure under the approved scope | **NOT DEMONSTRATED.** Five named requirements, each with the exhibit that would show it: one API key on the account, no manual login, no pre-existing holdings, no open orders, no unresolved submissions. `isolation_evidence()` returns `NOT_DEMONSTRATED` until each is supplied, and there is no way to pass it by asserting it |

**The binding dependency on ENFORCE.** The venue is the authority on what the
account holds — including anything put there by a path this repository does not
know about — and reading it needs the account credential. Without that read the
total is `UNREADABLE` and the gate refuses. That is the correct state, and it is
the difference between measuring exposure and assuming it.

**An absent table and a failed read are kept apart.** A table that does not
exist holds no rows, so that path is knowably zero; a table we could not read may
hold anything. The venue path has no table, so it can never report "absent" — no
missing migration can quietly excuse the authority.

---

## 2 · The account, and the credential steps

### The account that exists today

`acct_fc2d773a2afa4851` is **paused** and its accounting is **unresolved**. It
stays paused. Its only route out is `bettor_account_onboarding.resolve_existing`,
which runs the same four venue reconciliations and unpauses **only** if they
pass. No flag, endpoint or argument unpauses a row because someone decided to,
and a *new* account id does not inherit another account's evidence.

**So the owner names either that account (after its accounting is reconciled) or
a different one. Both are owner decisions and neither is made here.**

### Credential provisioning — the exact steps, and no secret in chat

1. Provision `PMUS_KEY_ID` and `PMUS_SECRET_KEY` as **service environment
   variables on `sportsassets-api`**, via the Render dashboard, or via
   `render-ops action=env-set`, which masks the value and never prints it.
2. **Never** in a chat message, a commit, a PR body, a workflow input or a log
   line. The value is read only from the service environment.
3. Read-only reconciliation **first**, before any approval:
   `POST /api/admin/funded-account-reconcile`. It performs four venue reads —
   balances, positions, open orders, executions — compares them against this
   system's own book, and persists the evidence with its source, account
   identity, retrieval instant, completeness, verdicts and discrepancies. **It
   submits no order and writes nothing to the account row**, so a read can never
   promote an account.
4. Confirm with `GET /api/admin/funded-account-reconciliation`, which returns
   that evidence **and its age**. It expires after 2 hours — a reconciliation
   describes one instant.
5. **A discrepancy stops the process.** It means our book and the venue's
   disagree before a single funded order exists.

Until a credential exists, all four reads record `UNREADABLE` — which is the
reconciliation working, not a gap in it.

---

## 3 · Proposed typed limits — UNAPPROVED

Every rail with its **unit**, its **window** and its **aggregation scope**, from
the single typed schema (`bettor_entry_execution.RAIL_TYPES`). Read the units:
two of these are not dollars.

| rail | unit | window | scope | frozen | **proposed** | effective |
|---|---|---|---|---|---|---|
| `MAX_MARKET_EXPOSURE` | USD | open book | one condition id | $1,000 | **$25** | $25 |
| `MAX_EVENT_EXPOSURE` | USD | open book | one venue event slug | $1,000 | **$25** | $25 |
| `MAX_CAPITAL_DEPLOYED` | USD | open book | this lane's book | $3,000 | **$50** | $50 |
| `MAX_CORRELATED_EXPOSURE` | USD | open book | this lane's book | $1,000 | **$25** | $25 |
| `MAX_DRAWDOWN` | USD | **cumulative, no reset** | this lane's book | $1,000 | **$20** | $20 |
| `MAX_RESIDUAL_INVENTORY` | **CONTRACTS** | open book | this lane's book | 2,000 | — *no owner field reaches this rail* | 2,000 contracts |
| `MAX_CAPITAL_HOURS` | **USD·HOURS** | accrued | this lane's book | 72,000 | — *no owner field reaches this rail* | 72,000 |

**Three things to read carefully before accepting:**

- **`MAX_DRAWDOWN` is not daily.** The owner field is called
  `daily_loss_stop_usd` for compatibility with already-recorded approvals, and
  the rail has **no date filter and no reset**. A $20 approval means: refuse the
  next entry once worst-case exposed loss reaches $20, and keep refusing until
  positions settle or mark better. It does **not** lift tomorrow. A real daily
  window would need a date-bounded query and a declared reset boundary with a
  timezone; neither exists and neither is invented.
  `cumulative_loss_stop_usd` is accepted as the accurate synonym.
- **`MAX_RESIDUAL_INVENTORY` is contracts, not dollars.** An earlier version of
  this table showed "$50" here, which would have had you approving **fifty
  contracts** — roughly $18–$33 at observed prices. Corrected.
- **Two rails cannot be tightened by approval at all.** They have no owner field.
  They stay at their frozen amounts and the table no longer implies otherwise.

Reading of the set: **one position at a time, about $25 of exposure, and the lane
stops adding once worst-case exposed loss reaches $20.**

---

## 4 · Emergency controls

| control | route / mechanism | effect | reaches |
|---|---|---|---|
| **stop entry** | `ingestion_state` control row, fail-closed | no new exposure. Servicing continues by design — a stopped entry loop is not a reason to stop managing an open position | scheduled lane |
| **halt the desk** | `POST /api/command/bettor/control/*` behind the **operator** cookie | operator-scoped; a read credential is refused by name | desk controls |
| **revoke authorization** | the authorization record carries a finite expiry and a revocation flag | submission requires an affirmative answer, not merely a consumed record | funded submission |
| **pull the credential** | remove `PMUS_KEY_ID`/`PMUS_SECRET_KEY` from the service env | **no client can be constructed**, so nothing can be sent at all | hardest stop |
| **flip the switches back** | three constants, §5 | each independently reversible | funded submission |
| **cancel outstanding** | `bettor_funded_management.cancel_outstanding` | an acknowledgement is `CANCEL_PENDING` until venue evidence makes it terminal; **late fills still matter** | live orders |

**Servicing survives an expired entry grant and a stopped entry permission** —
deliberately, so stopping entry cannot strand existing exposure.

---

## 5 · The activation change, exactly

Three constants, three files, each a one-line change and each independently
reversible:

```
backend/sportsassets/bettor_funded_execution.py
-  FUNDED_SUBMISSION_ENABLED = False      +  True    # new funded exposure

backend/sportsassets/bettor_funded_management.py
-  FUNDED_EXIT_SUBMISSION_ENABLED = False +  True    # exits and cancels

backend/sportsassets/bettor_entry_execution.py
-  REAL_ORDER_SUBMISSION_ENABLED = False  +  True    # any real order at all
```

**In two releases, servicing first.** `FUNDED_EXIT_SUBMISSION_ENABLED` alone
cannot open a position — it can only reduce one — so it carries no new risk, and
it means that if the entry switch is ever flipped the lane can already get out.
Entry second, after the first funded position's whole lifecycle has been
reconciled against the venue.

---

## 6 · The bounded pilot procedure

Run only after the owner names the account, accepts limits, and the release is
gated and deployed.

1. **Reconcile** (§2, steps 3–5). A discrepancy stops here.
2. **Approve the limit set** — `POST /api/admin/funded-limits/approve`.
3. **Activate** — `POST /api/command/bettor/control/activate`. Returns 409 with
   the unmet list until every check is met. **This is not the same as enabling
   funded submission.**
4. **Servicing switch only** (§5, release one). Verify: a management cycle runs,
   reconciles, and submits nothing because there is nothing to exit.
5. **Entry switch** (§5, release two). **One position at a time is no longer
   offered as the mitigation for the cross-lane gap** — see §1. The pilot must
   run with either the account-wide total measured (which needs the venue read)
   or isolation demonstrated. One position at a time remains a sensible *size*
   limit and is not a risk control across lanes. It is
   what makes the cross-lane aggregation gap unreachable.
6. **One position, full lifecycle, reconciled**: intent before send, affirmative
   authorization, expiry rechecked immediately before submission, limit binding,
   execution identity, partial and late fills, atomic accounting, then exit or
   settlement, then cash reconciled against venue evidence.
7. **Stop and report** before a second position.

---

## 7 · Release blockers, current state

| # | blocker | state | what is missing |
|---|---|---|---|
| 1 | market-data currency + contract identity through the production reader | **NOT ESTABLISHED** | M1 is **verified unavailable** on this feed (no sequence number ⇒ gaps undetectable; no snapshot/delta marker ⇒ a message is not known to be a whole book). M2 depends on the book endpoint emitting `ETag`/`Last-Modified`, which **requires the V3 probe to be deployed to answer**. Contract identity is partly established by the resolver and has not been checked element by element |
| 2 | fee policy, cash, fills, residual, observed charges | **PARTIAL** | the fee-documentation retrieval job is **running now**; rounding stays unreconciled until its output is read. Unrealised is reported UNMEASURED — honest, not a defensible mark |
| 3 | account-wide exposure, consistent units, loss window | **PARTIAL** | units and loss-window semantics are done and typed; **cross-lane aggregation is not implemented** |
| 4 | capital-relevant routes and credentials | **PARTIAL** | the unauthenticated funded-account read is fixed; the provider-key server-side proxy is **not done** |
| 5 | onboarding exercised, evidence persisted | **IMPLEMENTED, UNEXERCISED** | needs a credential to run for real |
| 6 | submission/management/cancel/recovery/stop/accounting via the production path | **SUBSTITUTED TRANSPORT ONLY** | has never run against a live venue |
| 7 | exact-SHA gate, deploy, serving build + Command Centre verified | **NOT DONE** | the suite is running; gate and deploy follow |

---

## 8 · Realistic completion estimate and the next blocking dependency

**The M2 determination has since RUN, and this section's two branches are both
superseded.** It was read directly from a GitHub runner — see
`research/evidence/VENUE_BOOK_PROTOCOL_2026-09-27.md`. The result was neither
branch below:

* a validator **is** present (`Last-Modified`), and the conditional GET returns
  304 — so the *exchange* exists;
* and the same read showed `last-modified` stamped **today** over a book that
  cannot have moved since February, so **M2 is CONTRADICTED as a market-data
  clock**, with evidence, rather than unavailable for want of a validator.

**What the completed determination supports, at its real width:**

> **Under the current evidence requirements, this market-data path does not
> qualify.**

**And the two things it does NOT support — both of which this section asserted:**

| written below | why it overreaches |
|---|---|
| "blocker 1 has **no engineering route** on the current market-data path" | a predicate failing on one path does not establish that no predicate can succeed on any path. Other endpoints, another subscription type, a vendor feed with its own contract and a quantity we have not identified are **untested, not excluded**. |
| "a funded pilot is **not reachable by engineering effort**" | that is a claim about the whole space of engineering, drawn from one negative test. It is withdrawn. |

The options I can **name** are a venue timing contract, a different market-data
interface, or an explicitly approved policy exception — which is a **policy
decision with stated assumptions, not a measurement**, is kept `DISABLED` in
`bettor_admission_policy`, and will not be adopted silently. **Naming three
options is not enumerating them**, so no "only remaining options" claim is made
here. The estimate for the remaining *identified* engineering (cross-lane
aggregation, contract-identity checks, the provider-key proxy, fee integration)
stands at **2–4 working days**; the market-data requirement is not in that number
and has no schedule.

**Capital readiness today: NO-GO.** Not because of missing approval — because
blocker 1 is unresolved, blocker 7 has not run, and blocker 6 has never touched a
real venue. I will update the moment the probe answers.

**What is genuinely blocked on you, and only on you:** the named account, the
credential provisioning, and acceptance of the typed limits. Those are not on the
engineering critical path today — blocker 1 is.
