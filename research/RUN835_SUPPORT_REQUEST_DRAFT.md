# RETAIL PMUS CREDENTIAL — SUPPORT REQUEST DRAFT

Send only if the developer console does not settle it. Nothing here names a key
id, a key name, or any secret material.

**Support channel: `NOT_IDENTIFIED`.** The vendor README points to
`polymarket.us/developer` for key generation and says nothing about support
contact; the institutional docs route through a registration portal, which is a
different product. Whatever retail contact route the console exposes is the one
to use — I could not reach it to check.

---

## The message

> **Subject:** API key permissions — market-data-only credential?
>
> Hello,
>
> Two questions about Polymarket US retail API keys.
>
> 1. Can you issue an API key that may authenticate to the market-data
>    websocket (`/v1/ws/markets`) but is prohibited **server-side** from
>    creating, modifying, cancelling or closing orders, and from any funding or
>    account mutation?
>
> 2. If not, can we create an **additional, independently revocable** API key
>    without affecting or replacing an existing key?
>
> Also, briefly:
>
> * Can multiple API keys exist on one account at the same time?
> * Can each key be revoked individually?
> * Are IP restrictions available per key?
>
> Thanks.

---

## How to read the reply

Whatever comes back is `OFFICIAL_SUPPORT` and closes this. The mapping:

| reply | case | verdict |
|---|---|---|
| Yes to Q1 — a key can be issued without order authority | **CASE 1** | `CREDENTIAL_PATH = DEDICATED_READ_ONLY`, approvable **YES** |
| No to Q1, yes to Q2 — separate revocable key, still trading-capable | **CASE 2** | `DEDICATED_TRADING_CAPABLE_CONTAINED`, **OWNER_DECISION_REQUIRED** |
| No to both — only the existing key can be used | **CASE 3** | `EXISTING_TRADING_CREDENTIAL`, approvable **NO** |
| No answer, or an answer that does not address server-side enforcement | **CASE 4** | unchanged |

Two readings to be careful of, because both look like a yes and are not:

- **"The websocket is market-data only"** answers a question about the
  *endpoint*, not about the *key*. Run 83.2C already established the endpoint
  carries no order path. What we need is whether the **credential** is refused
  at `/v1/orders`.
- **"You can just use it for market data"** is a statement about intended use,
  not about server-side authority. Only a refusal enforced at the venue counts.

If the reply is ambiguous on that point, the follow-up is one line: *"To confirm
— would that key be rejected by the server if it were used to submit an order?"*
