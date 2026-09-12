# pmus-broker

A fixed-function process that holds the PMUS venue secret and exposes exactly
one operation: mint authentication for `GET /v1/ws/markets`.

**Not deployed. Not connected. No credential is configured anywhere.**

## Why it exists

Run 83.2C established from the vendor SDK that a single credential signs both
the market-websocket handshake (`websocket/base.py:51`) and order creation
(`client.py:132`, reached from `resources/orders.py:24`). The same `key_id` and
`secret_key` attributes feed both. So:

```
SAME_KEY_CAN_SIGN_TRADING_REQUESTS = SUPPORTED   (client layer)
```

The Run 83 collector must therefore never hold that key. This process holds it
instead, and offers callers no way to name what gets signed.

## The authority surface

`pmusbroker/capability.py` — four constants. Read it in full; there is nothing
else to review.

```
CAPABILITY_ID = "PMUS_MARKET_WS_HANDSHAKE"
METHOD        = "GET"
PATH          = "/v1/ws/markets"
```

`canonical_signing_message(timestamp_ms)` takes a clock reading and nothing
else. There is no `sign(method, path)` and no equivalent primitive — a test
walks every public callable in the package and fails if one grows a parameter
named `method`, `path`, `url`, `host`, `message`, `payload`, `endpoint` or
`scheme`.

## What is deliberately absent

| absent | why |
|---|---|
| `polymarket-us` | the distribution contains the order client; the process holding the secret must not contain it |
| `httpx` / `requests` / `aiohttp` | signing needs no network. This process cannot reach the venue. |
| `asyncpg` / `psycopg` | no database access |
| `websockets` | it never connects to anything |

Dependencies: **`pynacl`**. That one line is the containment story, and
`test_broker_manifest_excludes_the_order_client` asserts it.

The four lines of Ed25519 are reimplemented rather than imported. The
divergence risk is paid for by
`test_the_broker_signing_matches_the_vendor_sdk_byte_for_byte`, which runs both
implementations in the test environment and compares.

## API

```
POST /mint            requires  X-Broker-Caller: <PMUS_BROKER_CALLER_TOKEN>
     body (optional)  {"consumer": "<label>"}       -- ledger metadata only
     200              {mint_id, capability_id, minted_at_wall_ms,
                       advisory_max_age_ms, single_use: true, headers: {...}}
     400              SIGNING_PARAMETER_REFUSED:<keys>   -- a body named a
                                                            method/path/host
     401              CALLER_IDENTITY_ABSENT | CALLER_IDENTITY_REJECTED
     503              BROKER_NOT_CONFIGURED

GET  /healthz         capability labels and mint counts. No credential state.
```

`consumer` is a label. It never enters the signed message — a test mints under
a consumer string that *is* a trading path and asserts the canonical message is
unchanged.

## Environment

```
PMUS_BROKER_KEY_ID        the venue key id       (sync: false)
PMUS_BROKER_SECRET_KEY    base64 Ed25519 secret  (sync: false)
PMUS_BROKER_CALLER_TOKEN  the collector's identity
PMUS_BROKER_BIND          default 127.0.0.1
PMUS_BROKER_PORT          default 8787
```

Named `PMUS_BROKER_*` rather than `PMUS_*` so the backend production trading
credential cannot be picked up by inheriting a familiar variable name. **Which
credential belongs here is an open owner decision** — see
`research/RUN83_CAPABILITY_ISOLATION.md` §6. The backend production credential
and `EDGE_PMUS_*` are both explicitly not approved.

With no caller token set, the broker mints nothing at all. It fails closed.

## The residual risk, stated plainly

**A compromised broker is a compromised trading credential.** The key it holds
is `TRADING_CAPABLE_AT_CLIENT_LAYER`; arbitrary code execution in this process
can sign anything the venue would accept. Having no HTTP client means it could
not itself deliver a signed order — but it could produce one for something else
to deliver.

The fixed capability constrains the **interface**, not the **process**. Nothing
in this design removes that risk, and it must not be described as read-only.
