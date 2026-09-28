# The credential request, narrowed — 2026-09-28

**What I asked for was wrong in two ways**, and the owner named both.

> *"The key-creation screen may reveal available settings; it is not necessarily the only evidence source and does not prove server-side enforcement. Ask me only for the names of available options or a redacted screenshot — never a key or secret."*

## Error 1 — I called one screen "the one thing only you can read"

I wrote that *"'Unverified' licenses exactly one action: look at the provisioning screen"* and asked you to open it. That overstated a UI observation as the sole and sufficient evidence source. It is neither.

**Other evidence sources exist, and three are available without you:**

| Source | What it could establish | Status |
|---|---|---|
| The venue's **partner/institutional** docs | The combo quota's wording references *"both Retail and Institutional APIs"*, so an Institutional surface is referenced and unread. It may document scopes the Retail pages do not. | **Not read.** I can fetch it. |
| `/partners/orders/data-model` | Linked from `/fees` under *"fees on execution reports"*. Partner-facing docs often state permissioning. | **Not read.** I can fetch it. |
| A **behavioural probe** on the existing key | Whether the credential is *accepted* on a read endpoint versus refused on a write one. A 403 on an order path with a 200 on a positions path would be positive evidence of narrowing. | **Not run**, and see the constraint below. |
| The key-creation screen | What options the UI **offers**. | Needs you. |

## Error 2 — a UI option is not server-side enforcement

Even a screen showing a "read-only" checkbox would establish that the venue **offers** the setting. It would not establish that the server **enforces** it. Those are different claims and I ran them together, which is the same fusion error as the Q1/Q2 confusion in the market-data work.

**The only thing that establishes enforcement is behaviour**: a key provisioned as read-only, presented to a write endpoint, and refused. And that probe has a hard constraint I will not cross — your standing instruction, which I restate because it binds here:

> *"Never test order authority by sending an unauthorized order."*

So enforcement can be probed **only** on endpoints that are safe to be refused on. `POST /v1/orders` is not one of them. A `DELETE /v1/rfqs/{rfqId}` on a nonexistent RFQ, or a `POST /v1/combos` (which mints an instrument, not an order, but consumes the shared weekly quota) are candidates — each needs its own assessment before being called safe, and I am not asserting any of them safe here.

---

## What I am asking you for, narrowed

**Only this:** the **names of the options** the API-key creation screen offers — or a **redacted screenshot** with any Key ID and Secret Key removed.

**Never** a key, a secret, a token, or any value. If a secret appears in a screenshot, do not send it; the names alone are what I need.

That is the whole request. If the screen offers no choices, "there were no options" is a complete and useful answer.

---

## And the state to record if it cannot be established

Per your instruction — *"If read-only permissions cannot be established, state the credential's authority as unknown or broader than desired and document the isolation required before provisioning"* — this is the state that goes on record:

> **CREDENTIAL AUTHORITY: UNKNOWN, AND TO BE TREATED AS BROADER THAN DESIRED.**
>
> The published Retail documentation states no scopes: zero sentences on read-only scope, zero on scopes or permissions at all, and the word *scope* does not appear. Key creation is documented as one key — *"Create an API key… You'll get a Key ID and a Secret Key"* — with no choice described. The three things the introduction lists (**Orders**: place, modify, cancel, query; **Portfolio**; **Account**) are **API groups, not permission scopes**.
>
> Absence of documented scopes proves neither that a narrow key exists nor that every key carries full order authority. **So the authority is unknown, and unknown is operated as full order authority** — because that is the assumption under which containment is sound, not because it has been shown.

### The isolation required before provisioning, assuming full order authority

Each of these is a property of how the credential is *held*, not of what it can do — so each holds whether or not the venue offers scopes:

1. **One credential, one service.** `PMUS_KEY_ID` / `PMUS_SECRET_KEY` on the API service only. Not in a workflow input, not in a second service, not in a runner environment. Provisioned by `render-ops env-set` so the value never enters a log or a chat message.
2. **The order path stays behind its own switch.** `FUNDED_EXIT_SUBMISSION_ENABLED` ships `False` and is code, not configuration — a credential with full authority still cannot reach `submit_fok` while that is false.
3. **The kill switch is read at submission time, inside the adapter, fail-closed.** `live_trading_paused` is consulted on every send rather than at start-up, so pausing does not need a deploy.
4. **Cancellation stays ungated.** Whatever else is switched off, the ability to stop working orders must not be.
5. **`execution_gate.authorize` gates every order-creating call**, including `close_position` — which sends an unpriced market exit and is the most powerful call in the surface.
6. **No read-only *claim* anywhere in the code or the approval package.** If the authority is unknown, nothing may be written as though a narrow key were in force. The isolation is the control; the credential is not.

**What this costs.** Treating the key as full-authority is strictly more conservative than the truth can be, so nothing is under-protected by it. The only cost is that we cannot claim defence-in-depth from the credential itself — the containment is entirely in the switches above, and if one of those is wrong there is no second barrier. That is the honest position and it is worth stating before provisioning rather than after.

---

## What I will do without you

1. Fetch the partner/institutional documentation and `/partners/orders/data-model` — two pages that may document permissioning and that I have not read.
2. Record the state above in the approval package, replacing the request that asked you to settle it alone.

If those pages establish scopes, your screenshot becomes a confirmation rather than the only evidence. If they do not, the request stands at exactly the size stated above: **option names, or a redacted screenshot. No secrets.**
