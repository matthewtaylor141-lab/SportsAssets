# RUN 83.4 — CALLER AUTHENTICATION AND THE NETWORK BOUNDARY

```
BROKER_CALLER_AUTH_PROPOSED   = PRIVATE_NETWORK_PLUS_ROTATED_BEARER  (option 2)
SIGNATURE_HOST_BINDING        = ABSENT
HOST_RESTRICTION              = NETWORK_POLICY
PLATFORM_DOCS_REACHABLE       = NO  (egress denied from this session)
```

**What I could not verify, stated first.** The hosting platform's documentation
is unreachable from this session — `render.com` and `api-docs.render.com` are
both `connect_rejected` at the egress proxy. So everything below about platform
capability is **repository evidence** (`render.yaml`, which is the deployed
configuration) or is marked `NOT_IDENTIFIED`. I have not invented a control, and
where I do not know, the line says so rather than guessing.

---

## 1. BROKER CALLER AUTHENTICATION

### The preference order, answered against actual evidence

**Option 1 — workload identity or mTLS.** `BROKER_CALLER_AUTH_MTLS = NOT_IDENTIFIED`.

Nothing in `render.yaml` uses, references, or implies a workload-identity or
mTLS mechanism. The service-to-service features the file actually demonstrates
are:

| evidence | line | what it shows |
|---|---|---|
| `ipAllowList: []` on the keyvalue service | `render.yaml:72` | a real reachability control; the repo's own comment reads *"only reachable from services in this Render account"* |
| `fromService:` env injection | `render.yaml:111-112` | one service's value wired into another at deploy time, without a shared secret in the repo |
| `type: worker` services have no inbound surface | `render.yaml:164, 186` | a worker is not addressable from outside |

That is network-level and configuration-level separation. It is **not** caller
identity: none of it lets the broker distinguish *which* service called it.
Whether the platform offers something stronger is unestablished, and I am not
going to claim it does on the strength of what other platforms offer.

**Option 2 — private network plus an independently rotated bearer credential.**
**PROPOSED.** This is what the evidence supports:

- the broker runs as a service with **no public ingress**, reachable only from
  services in the same account (the property `ipAllowList: []` already relies on
  for the keyvalue service);
- the collector presents `X-Broker-Caller`, compared with `hmac.compare_digest`;
- the token is **independently rotated** — its own value, its own schedule, not
  shared with, derived from, or rotated alongside any venue credential;
- with no token configured the broker mints nothing at all (503), so a
  misconfiguration fails closed rather than open.

**Option 3 — bearer alone.** Not proposed, and explicitly not what is deployed
if option 2's private-network half is ever dropped.

### Residual risk of option 2, stated rather than buried

> **A bearer token authenticates possession, not purpose.** Anyone who obtains
> it can request mints — the collector's own image, a process sharing its host,
> anything that can read its environment. The private network bounds *who can
> reach the broker*; the token bounds *who is answered*. Neither establishes
> that the caller is the collector doing collector work.
>
> What limits the damage is not the token. It is that **the thing being handed
> out is a market-socket handshake and nothing else** — a stolen token yields
> material valid for `GET /v1/ws/markets`, which the offline test proves fails
> at `/v1/orders` and `/v1/ws/private`. That is the control that actually
> matters, and it is cryptographic rather than operational.

### Broker enforcement, as built

| requirement | status | where |
|---|---|---|
| fixed capability ID | **built** — `PMUS_MARKET_WS_HANDSHAKE`, a module constant | `capability.py` |
| expected collector identity | **built** — default-deny, constant-time compare | `server.py::authorize` |
| no caller-supplied signing fields | **built** — `SIGNING_PARAMETER_REFUSED`, and no such parameter exists to begin with | `server.py::screen_body` |
| rate limit on mint | **BUILT THIS RUN** — token bucket, default 30/min | `server.py::RateLimiter` |

The rate limit is per-process and in-memory. It bounds a runaway or a
brute-force loop; it is **not** a defence against a caller who legitimately
holds the token and paces itself.

---

## 2. THE NETWORK BOUNDARY, CONCRETELY

### The rule, and why it cannot be a code check

The venue signs `timestamp + method + path`. **The hostname is not in the signed
message.** A minted header is therefore valid for `GET /v1/ws/markets` on *any*
host that accepts this key. `transport.assert_expected_destination()` exists and
checks the URL, but it is a **misconfiguration check, not a control**: anything
running in the collector's process could open a different socket without asking
it. The boundary has to be outside the process.

### Proposed configuration

**Collector — `rn1-collector`**

| destination | port | why |
|---|---|---|
| `api.polymarket.us` | 443 | the PMUS market websocket. **The only venue host.** |
| the broker's private address | 8787 | `/mint` |
| the observability database | 5432 | `rn1_obs_*` rows |

Everything else default-deny. In particular **`gateway.polymarket.us` must be
unreachable** — that is the unauthenticated REST gateway, and while it serves
market data rather than orders, leaving it open weakens the "one venue host"
statement for no benefit. `clob.polymarket.com` and
`ws-subscriptions-clob.polymarket.com` are denied until the CLOB channel is
separately approved.

**Broker — `pmus-broker`**

| direction | rule |
|---|---|
| inbound | from `rn1-collector` only; **no public ingress** |
| outbound | **NONE.** Signing requires no network. |

The broker having zero egress is the strongest single line in this document: a
process that cannot reach the venue cannot deliver anything it signs.

### The honest limitation — read this before relying on it

**A hostname allow-list is not a path allow-list.** `api.polymarket.us` serves
both `/v1/ws/markets` **and** `/v1/orders`. They are the same host and the same
port. So:

```
CAN THE PLATFORM SEPARATE THE PMUS WEBSOCKET FROM THE TRADING REST SURFACE?
    NOT_IDENTIFIED — and on the evidence available, probably NOT.
```

Network policy at the host level cannot tell them apart. Separating them needs
either a forward proxy that inspects the request line and permits only
`GET /v1/ws/markets`, or a platform egress control with path awareness. The
first is buildable by us; the second is `NOT_IDENTIFIED` for this platform and I
could not check.

**What this means in practice, stated plainly:** with a host-level allow-list
alone, a compromised collector process *could reach* the trading REST endpoint.
What it could not do is **authenticate** to it — it holds no key, and a market
handshake does not verify against `/v1/orders`. So the containment against
order placement is the *credential* separation, not the network. The network
policy's job is narrower than it first appears: it keeps traffic away from
hosts that have no business being contacted, and it is one layer, not the layer.

That is why these stay separate labels and why neither is upgraded:

```
SIGNATURE_HOST_BINDING = ABSENT           a cryptographic fact
HOST_RESTRICTION       = NETWORK_POLICY   an operational obligation
```

---

## 3. WHAT IS NOT DONE

No service created. No environment variable set. No network policy applied. No
deploy. Every item above is a proposal for review.
