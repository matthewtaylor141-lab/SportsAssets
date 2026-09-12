# RUN 83.5 — CREDENTIAL RESOLUTION

```
CREDENTIAL_PATH                         = NOT_IDENTIFIED      (CASE 4)
PMUS_PASSIVE_TEST_CREDENTIAL_APPROVABLE = NO
PMUS_CONNECTION_SAFE_TO_APPROVE         = NO                  (unchanged)
```

No credential was loaded, created or inspected. No connection was made. Nothing
was deployed.

---

## 1. WHAT I COULD AND COULD NOT REACH

| source | result |
|---|---|
| `docs.polymarket.us` | **403 at the egress proxy** — `gateway answered 403 to CONNECT (policy denial)` |
| `polymarket.us`, `polymarket.us/developer` | same 403 |
| `api.polymarket.us`, `gateway.polymarket.us` | same 403 |
| developer console | **no access** — requires the owner's session |
| official SDK package (`polymarket-us 0.1.2`) | **readable**, including its vendor README (`METADATA`) |
| official institutional documentation | **readable** — 31 pages fetched from `docs.polymarket.us` on 2026-09-10, retained in this session's scratchpad |

So `VENUE_CONSOLE_OBSERVED` is unavailable to me for anything. `VENUE_DOCUMENTED`
is available only for the **institutional** surface and for the vendor package
README.

---

## 2. THE RETAIL PMUS CREDENTIAL — the one the broker was built for

The official vendor README says, in full, on the subject of credentials:

> *"Polymarket US uses Ed25519 signature authentication. Generate API keys at
> polymarket.us/developer."* — `polymarket_us-0.1.2.dist-info/METADATA:147`

It then shows **one** `key_id`/`secret_key` pair driving `orders.create(...)`,
`ws.private()` and `ws.markets()` from the same client object — consistent with
Run 83.2C's finding, and adding nothing about permissions.

| # | question | answer | provenance |
|---|---|---|---|
| 1 | API-key permission scopes? | **NOT_IDENTIFIED** | — |
| 2 | Market-data-only / read-only key? | **NOT_IDENTIFIED** | — |
| 3 | Order create/cancel/modify disableable server-side? | **NOT_IDENTIFIED** | — |
| 4 | Market websocket enabled while trading REST disabled? | **NOT_IDENTIFIED** | — |
| 5 | Independently revocable credentials? | **NOT_IDENTIFIED** | — |
| 6 | Multiple simultaneous credentials per account? | **NOT_IDENTIFIED** — the README says *"API keys"* plural, which is suggestive and is not evidence | VENUE_DOCUMENTED (wording only) |
| 7 | New credential without touching production? | **NOT_IDENTIFIED** | — |
| 8 | IP or other server-side restrictions? | **NOT_IDENTIFIED** | — |

Two pieces of `SDK_SOURCE` evidence, neither of which resolves anything:

- **The SDK has no key-management surface at all** — no create, list, rotate or
  revoke endpoint. Key lifecycle lives in the console, which matches
  "Generate API keys at polymarket.us/developer". This says where the answer is,
  not what it is.
- **`PermissionDeniedError` (403) is a distinct class from
  `AuthenticationError` (401)** (`errors.py:85`, raised at `client.py:192`). The
  venue therefore distinguishes *authenticated but not permitted* from *not
  authenticated*. That is **consistent with** scopes and is **not proof** of
  them: a 403 could equally be geo-restriction, account state, KYC or market
  status.

**Nothing above is inferred from SDK absence**, per the standing rule. The
console is the authority and I cannot reach it.

---

## 3. THE FINDING THIS RUN ACTUALLY TURNED UP

**Polymarket US operates a second, institutional API whose credentials carry
strict, server-enforced scopes.** This is `VENUE_DOCUMENTED` —
`docs.polymarket.us/trader-guide/authentication`, fetched 2026-09-10T13:39Z:

> *"Scopes (strict; 403 / PERMISSION_DENIED `permission denied: missing required
> scope <scope>`)"*

The documented scope set:

```
read:marketdata      BBO, market data subscriptions
read:l2marketdata    full order-book depth (premium)
read:instruments     reference data
read:orders          open orders, previews, subscriptions
write:orders         INSERT / CANCEL / REPLACE / MODIFY
read:reports         order/trade/execution search
read:positions       positions, balances, ledgers
read:dropcopy        post-trade feed
read:accounts        /v1/whoami, /v1/users
read:funding / write:funding
```

Order placement requires `write:orders`, and **market data requires only
`read:marketdata`**. A credential granted `read:marketdata` without
`write:orders` would be precisely the server-enforced read-only market-data
credential CASE 1 describes. The venue enforces it centrally and returns a named
403.

### Why this does **not** make the answer CASE 1

**It is a different credential on a different surface, and that surface is not
the channel Run 83 measures.**

| | retail PMUS (Run 83's channel) | institutional Exchange Gateway |
|---|---|---|
| credential | Ed25519 API key | RSA private-key JWT → Auth0 bearer (180 s) |
| host | `api.polymarket.us` | `api.prod.polymarketexchange.com` |
| market data | `wss://api.polymarket.us/v1/ws/markets` | **long-lived HTTP streaming — "no WebSockets"** |
| subscribe | `{"subscribe": {...marketSlugs}}` | `POST /v1/marketdata/subscribe` |
| prices | string decimals | **integer-encoded strings** (`"550"` ÷ price_scale) |
| delivery | unspecified | **at-least-once**, dedupe by message id |
| snapshot/delta | unestablished | first message a snapshot, then *"deltas or new snapshots"* |
| scopes | NOT_IDENTIFIED | **documented and enforced** |

Adopting the institutional feed would be a **channel change, not a credential
change**: the broker mints an Ed25519 signature for `GET /v1/ws/markets` and
does not speak Auth0 JWT at all; the collector's decoder is written to the PMUS
frame shape; and `PMUS_FAST_STREAM_PATH` in the preregistration names the retail
socket. That is a redesign, which this run is forbidden to do and which I have
not done. **It is an option for the owner, not a finding that changes the case.**

Three further constraints on that option, all `VENUE_DOCUMENTED`:

- **Credentials are venue-issued, not self-service.** Client IDs are delivered
  by the Polymarket team after an onboarding submission. Obtaining a
  scope-restricted credential is an `OFFICIAL_SUPPORT` request, not a console
  action.
- **Revocation is venue-mediated** — rotation is documented as *"generate a new
  pair, new onboarding submission with the new public key, they add it, switch,
  then ask them to remove the old"*. That sequence implies two public keys can be
  registered at once, so **multiple credentials are supported at least
  transiently**; it also means revocation is not immediate or self-served.
- **Which scopes BettorToken's existing preprod institutional credential
  actually carries is `NOT_IDENTIFIED`.** Determining it means presenting the
  credential and reading the token's scope claim, which this run forbids. I did
  not do it, and did not open `inst/creds/`.

---

## 4. WHAT WOULD RESOLVE THIS IN MINUTES — for the owner, not for me

Every retail question above is answerable from the console I cannot reach. At
`polymarket.us/developer`, signed in:

1. Is there a **"Create API key"** flow, and does it present any permission,
   scope, or access-level choice? If yes, **the exact option names and their
   descriptions** are the whole answer.
2. Does the key list show **more than one key**, and a **per-key revoke**?
3. Is there any **read-only**, **market-data**, or **view-only** label?
4. Is there an **IP allow-list** field?
5. Can a key be created **without touching the existing production key** — i.e.
   does creating a second key leave the first listed and active?

Stop at the permission-selection screen; nothing needs to be created to answer
1–5. If the console offers no permissions at all, that is itself the answer, and
it moves this to CASE 2 rather than leaving it at CASE 4.

If the console shows nothing, the remaining authoritative route is
`OFFICIAL_SUPPORT`: ask Polymarket directly whether a retail API key can be
issued with market-data access and without order permissions — and note, when
asking, that their institutional product already implements exactly that
distinction.

---

## 5. CASE

**CASE 4 — venue capability cannot be established.**

```
CREDENTIAL_PATH                         = NOT_IDENTIFIED
PMUS_PASSIVE_TEST_CREDENTIAL_APPROVABLE = NO
```

Not approved and unchanged: production `PMUS_*`, `EDGE_PMUS_*`.
`SEPARATE_CREDENTIAL_ISSUANCE = NOT_IDENTIFIED`.

The engineering locks are untouched: `COLLECTOR_PHYSICAL_ABSENCE = ACHIEVED`,
`OFFLINE_HANDSHAKE_CHAIN_VERIFIED = YES`, `ISOLATED_ARTIFACT_READY = YES`,
`READY_FOR_CREDENTIAL_DECISION = YES`. Nothing in this audit contradicts the
collector, the broker, the ten offsets, the 0 ms semantics, the immutable
captures, the channel separation or migration 064, so none of them was reopened.

`PMUS_FULL_REPLACEMENT_UNCONFIRMED`, `STREAM_CONTINUITY_UNVERIFIED` and
`CLOB_PRICE_CHANGE_SEMANTICS_UNRESOLVED` all still hold. The institutional
streaming documentation describes at-least-once delivery and snapshot-then-delta
semantics **for the institutional feed**, and that says nothing about the PMUS
retail socket or the CLOB socket. Those facts are not transferable and have not
been transferred.
