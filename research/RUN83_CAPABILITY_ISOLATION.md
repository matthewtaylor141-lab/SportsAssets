# RUN 83.3 — CAPABILITY-ISOLATED PMUS OBSERVABILITY

```
STATUS                          = IMPLEMENTED LOCALLY. NOT DEPLOYED. NOT CONNECTED.
UNDERLYING_CREDENTIAL_AUTHORITY = TRADING_CAPABLE_AT_CLIENT_LAYER
COLLECTOR_SIGNING_CAPABILITY    = MARKET_WS_HANDSHAKE_ONLY
SIGNATURE_HOST_BINDING          = ABSENT
HOST_RESTRICTION                = NETWORK_POLICY
BROKER_PHYSICAL_ABSENCE         = ACHIEVED
COLLECTOR_PHYSICAL_ABSENCE      = NOT_ACHIEVED  (IMPORT_GRAPH_ABSENCE only)
mirror_live                     = false
```

`COLLECTOR_SIGNING_CAPABILITY` is an **architectural capability restriction in
our code**. It is **not** a venue-issued key scope. The venue would honour a
correctly signed order from the underlying credential; what prevents one is that
the collector process cannot construct a signature at all.

---

## 1. THE TWO PRINCIPALS

### A. SIGNING BROKER — `pmus-broker/`

| | |
|---|---|
| holds | the PMUS secret (`PMUS_BROKER_KEY_ID` / `PMUS_BROKER_SECRET_KEY`) |
| exposes | `POST /mint` and `GET /healthz`. Nothing else. |
| mints | `GET` `/v1/ws/markets` — and only that |
| dependencies | **`pynacl` only** |
| absent | order client, HTTP client, DB driver, websocket client, the vendor SDK |

The capability is four module constants in `pmusbroker/capability.py`. That file
is the entire authority surface; it is meant to be read in full during review.

**The signing function takes no signing parameters.**
`canonical_signing_message(timestamp_ms)` takes a clock reading and nothing
else — there is no argument by which a caller could substitute a method or a
path, because the function has nowhere to put one. `mint_market_ws_headers`
takes the credential (from the process's own environment) and a `consumer`
label. The label is ledger metadata; it never enters the signed message, and a
test mints under a consumer string that *is* a trading path to prove the
canonical message is unchanged.

**Why the signing is reimplemented rather than imported.** `polymarket_us`
ships the signer in the same distribution as `resources/orders.py`. Importing
the SDK for four lines of Ed25519 would put the order client inside the one
process that holds the secret — the worst possible place for it. The
divergence risk that creates is paid for by an equivalence test that runs both
implementations in the test environment (where the SDK is present) and compares
the headers byte for byte.

**Default deny, three ways:** no caller token configured → 503 and it mints
nothing ever; token absent or wrong → 401 via `hmac.compare_digest`; a body
carrying a signing-shaped key → 400 `SIGNING_PARAMETER_REFUSED`. The third does
not provide the security — the mint operation has no such parameter — it exists
so an *attempt* is a loud error rather than a silently ignored field. A quietly
dropped `{"path": "/v1/orders"}` looks identical to a successfully constrained
one, and we would rather see the difference.

### B. RUN 83 COLLECTOR — `backend/sportsassets/obs/`

Never holds: the secret key, a general signer, an order client, private
websocket credentials, or wallet material. It can obtain one set of three header
values already minted for the market handshake.

`handshake.py` reads exactly two environment variables — `RN1_OBS_BROKER_URL`
and `RN1_OBS_BROKER_CALLER_TOKEN`. Neither is a venue credential.

---

## 2. HOST BINDING — TWO SEPARATE CONTROLS, NEVER ONE

The venue signs `timestamp + method + path`. **The hostname is not in the signed
message.** A header minted for `GET /v1/ws/markets` is therefore valid for that
method and path on *any* host that accepts this key.

```
SIGNATURE_HOST_BINDING = ABSENT          <- a cryptographic fact
HOST_RESTRICTION       = NETWORK_POLICY  <- an operational obligation
```

These are recorded as two controls because treating them as one is how a
firewall rule gets mistaken for a cryptographic guarantee. The network policy
must restrict the collector's outbound destination to
`wss://api.polymarket.us/v1/ws/markets`, deny the trading REST host outright,
and forbid arbitrary proxy configuration.

What the signature *does* bind is the method and path. That is a real property
and it is used: a header captured from the market handshake **cannot** be
replayed against `/v1/orders` or `/v1/ws/private`. **Header scope is not key
scope** — the same key mints a new header for any path on demand — which is why
the broker, not the collector, holds it.

---

## 3. HEADER LIFETIME AND REPLAY

The scheme carries a timestamp and **no nonce**, and the server's tolerance
window is not documented anywhere reachable. We cannot state how long a header
set is accepted or how many times. Every mint is therefore **single use
operationally**:

- `HandshakeMaterial.consume()` hands the headers over exactly once and forgets
  them; a second call raises `HandshakeAlreadyConsumed`.
- `connect_with_fresh_mint()` requests a **new** mint on every attempt. A test
  drives a failing-then-succeeding connect and asserts two *distinct*
  signatures were used.
- The `finally` block discards material on every path — success, refusal, or an
  exception from inside a websocket library.

The broker ledger records mint id, wall time, capability id, consumer. There is
**no field** for a signature, timestamp header, access key, or secret, and
`record()` reads only metadata off the handshake object. Both
`MintedHandshake` and `HandshakeMaterial` override `__repr__` to `<redacted>`,
so an object landing in a `%s` or an f-string cannot leak.

---

## 4. PROCESS ISOLATION AND THE MANIFEST — THE HONEST ANSWER

| | broker | collector |
|---|---|---|
| order client physically installed | **no** | **yes** |
| verdict | `BROKER_PHYSICAL_ABSENCE = ACHIEVED` | `COLLECTOR_PHYSICAL_ABSENCE = NOT_ACHIEVED` |
| what is proven instead | manifest = `pynacl` | `IMPORT_GRAPH_ABSENCE` |

**The collector does not achieve physical absence today, and cannot without a
deployment change.** `sportsassets/obs/` runs inside `sportsassets-workers`
(registered at `workers/all.py:334` as `rn1_obs`), and that image installs
`polymarket-us` and `py-clob-client` because `live_executor` needs them. The
collector runs in the **same process as the mirror**.

So the collector's guarantee is import-graph absence, proved three ways — the
reviewed allow-list in `test_obs_safety.py`, the AST import scan in
`test_run833_capability_isolation.py`, and the dynamic tripwire import. That is
real, but it is a property of what the code imports, not of what is installed:
a late import or a `getattr` inside a function body is reachable in principle,
which is exactly why the dynamic proof exists.

Closing it requires **a separate collector service and image** — see §7. The gap
is pinned by `test_the_collector_image_still_contains_the_sdk_and_we_say_so`, so
this document cannot start claiming otherwise without a test failing.

---

## 5. RESIDUAL RISKS — STATED, NOT MITIGATED AWAY

1. **A compromised broker is a compromised trading credential.** The broker
   holds a key that is `TRADING_CAPABLE_AT_CLIENT_LAYER`. Arbitrary code
   execution in that process can sign anything the venue would accept. The fixed
   capability constrains the *interface*, not the *process*. Its lack of an HTTP
   client means it could not itself deliver a signed order to the venue — but it
   could produce one for something else to deliver. **This risk is not removed
   by anything in this design.**
2. **Collector physical absence not achieved** (§4).
3. **Network policy is the only host restriction** (§2). If it is
   misconfigured, a minted header reaches whatever host is reachable.
4. **Replay window unknown.** Single-use is our discipline, not a venue
   guarantee. A captured header may be replayable by an attacker within an
   unknown tolerance — though only for `GET /v1/ws/markets`.
5. **The broker's caller token is a bearer secret.** Anyone who obtains it can
   request mints. It authenticates the collector; it does not authenticate the
   *purpose*.
6. **`PMUS_FULL_REPLACEMENT_UNCONFIRMED`** and
   **`CLOB_PRICE_CHANGE_SEMANTICS_UNRESOLVED`** remain open. Both are handled by
   refusal rather than assumption, but neither is resolved.
7. **Server-side authority remains unknown.** Nothing here establishes what the
   venue would permit this key to do.

---

## 6. CREDENTIAL PROVISIONING DECISION TREE

```
Is a venue-enforced READ-ONLY market-data credential available?
├── YES → use it. Broker still mints (defence in depth), but the
│         underlying authority label changes and §5.1 shrinks sharply.
│         PMUS_CONNECTION_SAFE_TO_APPROVE = YES, subject to containment.
└── NO
    └── Can a SEPARATE, REVOCABLE credential be provisioned for the broker,
        distinct from every production trading credential?
        ├── YES → provision it as PMUS_BROKER_KEY_ID / PMUS_BROKER_SECRET_KEY.
        │         Authority stays TRADING_CAPABLE_AT_CLIENT_LAYER, but the blast
        │         radius is one revocable key that touches no production path.
        │         → OWNER DECISION REQUIRED (this is CASE C).
        └── NO  → EXPLICIT FURTHER APPROVAL REQUIRED. Reusing an existing
                  trading credential is NOT approved by this document.

NOT APPROVED, explicitly:
  * backend production PMUS_KEY_ID / PMUS_SECRET_KEY — the executor's key
  * EDGE_PMUS_KEY_ID / EDGE_PMUS_SECRET_KEY — not approved merely because
    BookStreamer already uses them (see EDGE_ENGINE_PMUS_CREDENTIAL_COUPLING)

SEPARATE_CREDENTIAL_ISSUANCE = NOT_IDENTIFIED (venue-side; unestablished)
```

The broker's variables are named `PMUS_BROKER_*` rather than `PMUS_*`
deliberately: the production trading credential must not become reachable by
inheriting a familiar variable name.

---

## 7. DEPLOYMENT TOPOLOGY FOR A PASSIVE TEST (proposed, not provisioned)

```
  ┌────────────────────────┐         ┌──────────────────────────┐
  │  pmus-broker           │  /mint  │  rn1-collector           │
  │  private service       │<────────│  its OWN service & image │
  │  no public ingress     │  caller │                          │
  │  deps: pynacl          │  token  │  deps: httpx, websockets │
  │  egress: NONE          │         │  NO polymarket_us        │
  │  no DB                 │         │  egress allow-list:      │
  └────────────────────────┘         │    api.polymarket.us:443 │
                                     │    the broker            │
                                     │    the database          │
                                     └──────────────────────────┘
```

Four properties this topology buys that today's does not:

1. the collector image carries no SDK → `COLLECTOR_PHYSICAL_ABSENCE = ACHIEVED`;
2. the collector is not the mirror's process, so an instrument fault cannot
   perturb the money path (and vice versa);
3. the broker has no public ingress and no egress at all;
4. the egress allow-list is enforceable per service rather than per image.

**Nothing here is provisioned.** No service created, no env var set, no deploy.

---

## 8. PRODUCTION / ENVIRONMENT CHANGES THAT WOULD EVENTUALLY BE REQUIRED

None of these has been made. Listed so the deployment ask is one reviewable
list rather than a discovery during rollout.

| # | change | where | note |
|---|---|---|---|
| 1 | new private service `pmus-broker` | render.yaml | no public ingress |
| 2 | `PMUS_BROKER_KEY_ID`, `PMUS_BROKER_SECRET_KEY` | broker only | `sync: false`; §6 decides which credential |
| 3 | `PMUS_BROKER_CALLER_TOKEN` | broker + collector | shared bearer; `sync: false` |
| 4 | new service `rn1-collector`, own image without the SDK | render.yaml | §7 |
| 5 | `RN1_OBS_BROKER_URL`, `RN1_OBS_BROKER_CALLER_TOKEN` | collector | broker address, not a venue address |
| 6 | egress allow-list | collector | `HOST_RESTRICTION = NETWORK_POLICY` |
| 7 | migration **064** | database | applied by the normal boot path |
| 8 | `RN1_OBSERVABILITY_SHADOW`, `RN1_OBSERVABILITY_SUBJECT_WHALE_ID` | collector | the activation switches — **still off** |
| 9 | de-register `rn1_obs` from `workers/all.py` | code | once the collector has its own service |

Item 8 is the activation and is **not** requested here. Items 1–7 and 9 are the
containment; they can all be reviewed and staged before any question of
connecting arises.

---

## 9. WHAT REMAINS TRUE FROM EARLIER RUNS

- The ten pre-registered offsets are unchanged: 0 ms, 100 ms, 250 ms, 500 ms,
  1 s, 2 s, 5 s, 10 s, 30 s, 60 s — on **both** stream channels, from the same
  anchor, with the channel inside the slot identity so PMUS and CLOB slots can
  never collide.
- The 0 ms rule is local-monotonic only:
  `latest_state_receive_monotonic <= source_receipt_monotonic`, and
  `state_age_at_receipt_ms` is their difference. Venue timestamps are
  provenance and are stored as TEXT so arithmetic against a local reading is
  awkward rather than easy.
- `RPS_LIMIT_NOT_ESTABLISHED`, `HISTORY_BACKFILL_OBSERVABILITY_REACHABLE =
  FALSE`, and the `RUN83_ACTIVATION_FAILED_V1` seal are all unchanged.
