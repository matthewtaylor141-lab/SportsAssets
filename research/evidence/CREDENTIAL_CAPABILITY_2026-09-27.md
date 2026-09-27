# VENUE CREDENTIAL CAPABILITY — read directly, 2026-09-27

**Source:** `command-verify` job `credential-capability`, run
[36344339547](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/36344339547),
`head_sha = ad95d69`, retrieved `2026-09-27T19:26:28Z`.
**No credential was sent. No order was placed.** Order authority is never tested
by sending an order.

---

## 0 · The claim under test, and its correction

> I wrote that the credential should be provisioned as a **read-only key**, and
> cited the venue's retail authentication page as support.

That page describes **how a key authenticates**. It does not thereby establish
that a **read-only scope is offered**, and I ran the two together. The pages were
read to settle it.

---

## 1 · What was retrieved

| page | HTTP | bytes |
|---|---|---|
| `docs.polymarket.us/api-reference/authentication` | **200** | 412,178 |
| `docs.polymarket.us/api-reference/introduction` | **200** | 365,042 |
| `docs.polymarket.us/quickstart` | 404 | — |
| `docs.polymarket.us/api-reference/api-keys` | 404 | — |
| `docs.polymarket.us/developers/api-keys` | 404 | — |

## 2 · The result, per question

| question | authentication page | introduction page |
|---|---|---|
| **a read-only scope is offered** | **0 sentences — UNANSWERED** | **0 sentences — UNANSWERED** |
| **scopes or permissions at all** | **0 sentences — UNANSWERED** | **0 sentences — UNANSWERED** |
| onboarding reads (balances, positions, orders, executions) | 2 | 3 |
| submission permission | 1 | 1 |
| servicing permission (cancel / modify) | 1 | 2 |
| key-creation choices | 6 | 0 |
| revocation or rotation | 1 | 0 |

## 3 · The finding, and it is stronger than "unsupported"

**Key creation, verbatim:**

> *"Create an API key — Click to create a new key."*
> *"You'll get a Key ID and a Secret Key."*
> *"Your secret key is shown only once."*

**One key. No scope choice is described at any point.**

**And what that one key reaches, verbatim from the introduction page:**

> | Group | What you get |
> |---|---|
> | **Orders** | **Place, modify, cancel, and query orders** |
> | Portfolio | View positions and trading activity |
> | Account | Check balances and buying power |

**Those are API GROUPS, not permission scopes.** Nothing on either page suggests a
key can be issued limited to a subset of them, and the word *scope* does not
appear at all.

### So the correction runs in two directions, not one

| | |
|---|---|
| **the claim is unsupported** | no retrieved page mentions a read-only scope |
| **and PERMISSION GRANULARITY IS UNVERIFIED** | the word *scope* does not appear on either page, so we do not know what the provisioning screen offers |

> **⚠ CORRECTED AGAIN, and this is my error in the opposite direction.** I first
> wrote that the likely reality is *"worse than unsupported"* and that a key
> should be **assumed to carry full order authority**. That inverts the same
> mistake: **absence of documented scopes does not prove every issued key has
> every permission.** A venue may issue narrow keys and simply not document them.
>
> **"Unverified" licenses exactly one action: look at the provisioning screen.**
> It licenses no conclusion about what a key can do, in either direction — and I
> made a conclusion in each direction within a day.

### And "two constants are the only protection" was wrong in both directions too

I wrote that if read-only onboarding cannot be separated from submission, our two
code constants are the only thing between a provisioned credential and order
authority. `backend/sportsassets/submission_surface.py` enumerates it instead:

**It OVERSTATED the exposure. There are TEN independent gates**, any one of which
refuses — four code constants, a process-bound gate, an authorization row, an
eligible reconciled account, a fresh exposure measurement, the credential itself,
and the funded schema. They are not all constants and they do not all clear the
same way.

**It UNDERSTATED the problem. One environment credential is visible to ELEVEN
modules** — including `live_executor`, the legacy copier — and *neither constant
guards those.* Only the process-bound `execution_gate` does, because it sits
**inside** the adapter rather than at the call sites.

### And the mutation surface is not one adapter — I got this wrong twice

My first table listed two functions in `pmus`. Parsed properly, **five modules
reach the venue mutation surface across TWO venues:**

| module | how it reaches the venue |
|---|---|
| `bettor_funded_execution` | `submit_fok` |
| `bettor_funded_management` | `submit_fok`, `cancel_order` |
| `live_executor` | `create_order` + **`post_order` — a second path to polymarket-CLOB that does not pass through `pmus` at all**, plus three callable references |
| `workers/mirror_live` | `submit_fok`, `cancel_order` **passed as callables** |
| `workers/underdog` | `submit_fok` **passed as a callable** |

Three of those pass the function to `asyncio.to_thread` rather than calling it, so
**no call-site scan finds them** — and my first two scans did not.

> This repository had already caught this exact class of error on 2026-09-21, by
> walking the AST for venue-client constructors, and recorded that no
> hand-written inventory had ever listed the CLOB path — *"including the one I
> wrote the day before."* **I then wrote another hand inventory and repeated it.**
> The enumeration is now a parse with a regression test, not a list.

**What is genuinely reassuring, and it is structural rather than a promise:** the
`execution_gate` is inside the adapter, so all four `pmus` routes are covered
however they are invoked, and the CLOB path carries its own explicit
`_gate.authorize("submit_clob")`.

**What is genuinely missing:** a read-only diagnostic still calls the same `pmus`
module the submitting lane calls. A flag that says *"do not submit"* is weaker
than an object with no submit method, and the constrained read-only interface is
recorded as **NOT IMPLEMENTED** rather than claimed.

### One more correction in passing

I described the venue's scheme loosely as "API-key authentication". The page shows
**Ed25519 request signing** — `X-PM-Access-Key`, `X-PM-Timestamp`, and a signature
over `timestamp + method + path` — with the secret used to sign rather than sent.
That is a better scheme than a bearer key, and it is not what I said it was.

---

## 4 · Three things kept apart, which I had merged

| | what it is | state |
|---|---|---|
| **1** | permissions needed for **read-only onboarding** — balances, positions, open orders, executions | the endpoints exist; **no scope limited to them is documented** |
| **2** | permissions needed **later for submission and servicing** | documented as the same key's first group. **Strictly larger, and apparently not separable** |
| **3** | **our own application controls** | `REAL_ORDER_SUBMISSION_ENABLED = False` (`bettor_entry_execution.py:572`), `POLICY_ADMISSION_ENABLED = False` (`bettor_admission_policy.py:74`) |

> **Group 3 is not evidence about the venue's scopes.** It is ours. And because
> group 1 cannot currently be separated from group 2, **group 3 is the only thing
> standing between a provisioned credential and order authority** — which makes
> those two constants load-bearing in a way "read-only first" would have hidden.

## 5 · What this changes about provisioning

* **Report what the provisioning screen offers, before creating a key.** Do not
  plan around an assumed scope in either direction: a read-only option is not
  established, and neither is its absence. The screen is the evidence.
* **Do not rely on a venue-side scope as the control**, whichever it turns out to
  be. Our side must refuse independently — and it does, through ten enumerated
  gates rather than the two I claimed.
* **The credential's blast radius is eleven modules**, including the legacy
  copier. Provisioning for onboarding reads makes it available to the submitting
  lane in the same process, so the separation comes from the gates and from a
  constrained interface — the latter being **not yet implemented**.
* **Revocation and rotation are barely documented** — one matching sentence, and it
  is a code sample. Before a key is issued, the revocation path must be
  established from the provisioning screen, because a credential we cannot revoke
  is not a credential we should hold.
* **The secret is shown once.** Provisioning must write it straight into the
  service's environment, never into a chat, a ticket or a file in this repository.
