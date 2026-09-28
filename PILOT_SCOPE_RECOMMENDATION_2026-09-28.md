# The pilot: one recommendation, and why the previous choice was not mine to hand over

**The owner's instruction:** *"Recommend one concrete scope with reasons and acceptance criteria. Do not ask me to choose adoption without first establishing whether any eligible venue inventory exists, its ownership, basis and reconciliation."*

I asked you to choose between adoption and entry without doing the work that makes the choice answerable. Having now done it, **adoption is not available**, so there was never a real choice to offer.

Measured read-only against production: `research/eligible_venue_inventory.sql`, research-sql run 247, `2026-09-28T12:56:31Z`, `psql exit=0`.

---

## 1 · Is there eligible venue inventory? No — and every candidate is excluded for a stated reason

| Candidate source | Rows | Verdict |
|---|---:|---|
| Funded book (`bettor_funded_intents`) | **0** intents, 0 entries, 0 open, 0 residual, **0 accounts** | Nothing to service. |
| Our own unsettled filled orders | 52 orders, **\$16,180.53** filled | **Excluded on age.** All 160,857 unsettled orders fall in the `older_than_14d` class — **not one is within 14 days.** An unsettled order on a fixture weeks past is an unrecorded settlement, not live inventory. Treating \$16,180.53 as adoptable would be exactly the error the age class exists to catch. |
| `positions` | 408,162 rows, **22 distinct owners** | **Excluded on ownership.** Keyed by `whale_id` — the mirrored accounts. Adopting one books someone else's inventory as ours. |
| `api_positions` | 43,375 rows, **22 distinct owners** | **Excluded on ownership**, same reason. |

**What this does not establish, and I will not let it pass as established.** The authoritative answer is the venue's own `GET /v1/portfolio/get-user-positions` for our account. That needs the venue credential and an egress route this container does not have. So the correct statement is: **no eligible inventory can be established from our own records, and the venue has not been asked.** A venue read could still show positions our records lost track of — which is itself a reason the reconciliation step below comes first.

## 2 · And the account is not eligible either

There is exactly **one** desk account:

| | |
|---|---|
| `account_id` | `acct_fc2d773a2afa4851` |
| `desk_id` | `live1` |
| `status` | ACTIVE |
| **`paused`** | **true** |
| **`accounting_status`** | **`ACCOUNTING_UNCERTAIN`** |
| `opening_balance` | 100,000 |
| note | *"New shadow account following an accounting-recovery defect."* |

This is the account a pilot would have to name, and it is **paused with uncertain accounting**. Your standing instruction is to keep it paused until reconciliation succeeds. Reconciliation has not succeeded. **So the pilot fails on the account before it fails on the inventory.**

Standing reconciliation discrepancies: **0 rows** — which is not the same as reconciled. An empty discrepancy table on a book with no positions is uninformative.

---

## 3 · The recommendation: one scope, and it is not a funded trade

> **RECOMMENDED SCOPE — "Qualify the servicing path against the venue, with no capital at risk."**

Your own line settles it: *"No pilot is ready while its necessary servicing evidence cannot be qualified."* Book currency is unqualified in production, the account is unreconciled, and there is no inventory. A funded pilot authorised today would refuse to act on the first, could not lawfully start on the second, and would have nothing to act on under the third. **Recommending one anyway would be recommending an idle pilot and calling it progress.**

**What the recommended scope is, concretely:**

| Step | Action | Capital at risk |
|---|---|---:|
| **S1** | Read `GET /v1/portfolio/get-user-positions` for `acct_fc2d773a2afa4851` through the existing authorized workflow route. Establishes whether venue inventory exists at all, with its basis. | **\$0** |
| **S2** | Reconcile that answer against `venue_truth_days` and the 52 unsettled filled orders. Resolve `ACCOUNTING_UNCERTAIN` or state precisely what blocks it. | **\$0** |
| **S3** | Qualify book currency: run the M2 validator probe on the book path (does the endpoint emit an ETag / Last-Modified?) and implement **connection continuity** (P3 as replaced) — reconnect, resubscribe, resynchronise, which the client currently does not do. | **\$0** |
| **S4** | Re-measure evaluability on a cycle with the freshness change **active**, per point 3. | **\$0** (provider credits only) |

**Acceptance criteria, each a readback rather than a judgement:**

1. **S1** — a venue positions response is on file, with per-market quantity and basis, for the named account. *Fails if the credential cannot read it: that answers point 6's question at the same time.*
2. **S2** — every position the venue reports is either matched to our records or recorded as a named discrepancy, and `accounting_status` moves off `ACCOUNTING_UNCERTAIN` **or** the specific blocker is named. *No pilot proceeds while this reads uncertain.*
3. **S3** — `book_currency_evidence` returns a mechanism for at least one real market, and a funded `select_exit` on that market refuses for some reason **other than** `R_BOOK_NOT_FRESH`. That is the narrowest possible proof that the currency blocker is gone.
4. **S4** — the provider-lag / our-lag split re-measured on a new cycle, with stale-on-arrival refusals counted separately, and **no claim that admissions follow**.

**Only when 1–4 hold does a funded pilot become a proposal**, and at that point its scope is determined by what S1 found rather than by a choice between two hypotheticals.

---

## 4 · The limits: how they interact, and what the loss stop actually enforces

You are right that I presented a loss budget as if it were a guarantee, and right that average historical market cost is a scale reference rather than a justification. Both are corrected here.

**What each control is, precisely.** All five bind in `bettor_entry_execution.authorize_submission` — **at submission time**. None is a continuous breaker.

| Control | Binds | What it actually prevents | What it does **not** prevent |
|---|---|---|---|
| `capital_usd` \$100 | `MAX_CAPITAL_DEPLOYED` | A submission that would push total deployed capital over \$100 | Existing deployed capital from losing all of itself |
| `per_order_usd` \$25 | `MAX_MARKET_EXPOSURE` | One market holding more than \$25 | Four markets holding \$25 each |
| `event_exposure_usd` \$25 | `MAX_EVENT_EXPOSURE` | Two positions on one event compounding | Four positions on four events all losing |
| `max_exposure_usd` \$100 | `MAX_CORRELATED_EXPOSURE` | Correlated exposure above deployed capital | Uncorrelated positions all losing together |
| `daily_loss_stop_usd` \$40 | `MAX_DRAWDOWN` | **A new submission once drawdown reaches \$40** | **Any loss on inventory already held** |

### The worst case is \$100, not \$40

**\$40 does not stop losses at \$40.** Four \$25 orders fill, exhausting `capital_usd`. All four settle to \$0. The realised loss is **\$100** — the full deployed capital — and the \$40 stop never binds, because it only gates a *fifth* submission and capital already forbade one.

**And there is a worse interaction, which comes straight out of today's partial-exit work.** `realised()` books on **position closure** (`closed_at IS NOT NULL`). `MAX_DRAWDOWN` reads that figure. So while all four positions are open and losing, **drawdown reads \$0.00** and the loss stop is blind precisely when it would matter. It can only fire *after* something closes — by which point the loss it was meant to prevent has been taken. The approval package already recorded that no continuous funded breaker exists; what it did not say is that the submission-time stop is also **blind to open losses by construction**.

**So the honest statement of exposure:**

| | |
|---|---:|
| **Worst-case realised loss** | **\$100** — equal to `capital_usd`, if all inventory settles worthless |
| Worst-case loss before the stop can bind | **\$100** — the stop needs a closure to see anything |
| What \$40 enforces | no *new* submission after \$40 of **closed** losses |
| What bounds the pilot | **`capital_usd`, and only `capital_usd`** |

**Outstanding commitments.** A working (unfilled) order commits collateral at intent for an entry; migration 127 establishes that *an exit commits no collateral*. Whether `MAX_CAPITAL_DEPLOYED` counts reserved-but-unfilled commitments alongside fills is the one interaction I have **not** verified, and it matters: if the rail counts fills only, working orders could carry exposure past the cap. **Unverified — it goes in S2's acceptance, not into a limits table presented as sound.**

### The limits restated with an honest basis

The \$2,157.51 average per-market cost is a **scale reference** — it says the legacy lane operated two orders of magnitude above this proposal. It is **not** the justification for a loss budget. The justification is:

> **`capital_usd` is the loss budget, because it is the worst case.** Set it to the amount you are willing to lose in full to find out whether the software behaves on real money. Everything else is a shape constraint on how that amount is spread, not a second line of defence.

At \$100: four markets at \$25, one per event. Enough contracts per order (~41 at 0.60) to exercise depth-capping, a partial fill leaving a residual, and a multi-level `REDUCE` — the mechanics that actually broke in testing. **The \$40 stop is worth keeping as a "stop adding" rule, but it must not be described as a \$40 maximum loss, and I described it that way.**

---

## 5 · What I am not claiming

- **Not** that no venue inventory exists — only that none can be established from our records, and the venue has not been asked.
- **Not** that \$16,180.53 of unsettled filled orders is inventory. Every one is >14 days old and the likeliest reading is unrecorded settlement.
- **Not** that 0 discrepancies means reconciled. On an empty book it means nothing.
- **Not** that the recommended scope leads to a funded pilot. It establishes whether one is possible.
