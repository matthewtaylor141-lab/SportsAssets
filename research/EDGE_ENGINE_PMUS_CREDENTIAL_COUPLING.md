# RESEARCH FINDING — `EDGE_ENGINE_PMUS_CREDENTIAL_COUPLING`

```
OPENED          2026-09-12 (Run 83.2C reads, recorded under Run 83.3)
STATUS          OPEN — recorded, not acted on
SCOPE           edge-engine. NOT a Run 83 component.
ACTION TAKEN    NONE. No edge-engine file was modified.
SECRET VALUES   NOT INSPECTED.
```

## The finding

`edge-engine` reads **one credential slot pair** and hands it to **two
different surfaces**: the authenticated trading client and the PMUS market
websocket.

```
EDGE_PMUS_KEY_ID / EDGE_PMUS_SECRET_KEY        (render.yaml:218-221, sync: false)
        │
        ├─▶ venues/polymarket_us.py:99-101   PolymarketUS(key_id=…, secret_key=…)
        │        └─▶ client.py:132  create_auth_headers  ──▶ POST /v1/orders, …
        │
        └─▶ venues/polymarket_us.py:70       BookStreamer(os.environ["EDGE_PMUS_KEY_ID"],
                                                          os.environ["EDGE_PMUS_SECRET_KEY"])
                 └─▶ pmus_stream.py:136      MarketsWebSocket(key_id=…, secret_key=…)
                      └─▶ base.py:51          create_auth_headers("GET","/v1/ws/markets")
```

`BookStreamer` (`edge-engine/src/edge/venues/pmus_stream.py`, 162 lines) is
gated by `has_credentials()` and `EDGE_PMUS_WS != "0"`. It maintains
`slug -> (ts, marketData)`, subscribes in batches of 200 up to a 4,000-slug
bound, and reconnects with capped backoff.

## What this establishes, and what it does not

**Establishes:** the exact action Run 83 has been treating as unapproved —
presenting a trading-capable credential to `wss://api.polymarket.us/v1/ws/markets`
— is already implemented in this repository, for a different service.

**Does not establish:** that it is running. `render.yaml:213` reads *"LIVE_*
credentials — leave unset while mode: PAPER"*, and all four slots are
`sync: false`.

```
EDGE_PMUS_SLOTS_POPULATED = NOT_IDENTIFIED
```

Determining that means reading production environment values, which is outside
this run's scope and would put credential state where it does not belong. It
was not done.

**Does not constitute authorization.** This is existing architecture. It is not
a precedent, and it does not approve the `EDGE_PMUS_*` credential for Run 83 —
see the decision tree in `RUN83_CAPABILITY_ISOLATION.md` §6, where it is listed
as explicitly not approved.

## Why it is worth its own finding

`BookStreamer`'s own docstring frames the socket as *"an accelerator, never a
dependency"* and *"fail-soft by design"*. That framing is about **availability**,
and it is correct about availability. It is silent on the **credential**
question — that opening the socket at all requires presenting a key which, per
Run 83.2C, can also sign `POST /v1/orders`. The decision was reasonable on the
terms it was taken; those terms simply did not include the authority question,
because the authority question had not been asked yet.

Two things worth carrying forward from it, independent of the credential issue:

- it solves **reconnect and resubscribe**, which the vendor SDK does not ship at
  all, and which Run 83's PMUS channel will need;
- its `prune()` docstring records a real failure mode — measuring subscription
  room against a **lifetime** count, so the 4,000-slug bound silently fills with
  finished games and new markets stop streaming. Any Run 83 subscription manager
  should measure room against the live set, not the lifetime set.

## Recommended disposition (not taken)

1. Review `EDGE_PMUS_WS` and the `BookStreamer` credential wiring on their own
   terms, outside Run 83.
2. If a market-data-only credential is ever provisioned, wiring it here is a
   two-line change at `polymarket_us.py:70-71` — the code already supports
   passing a distinct pair; only the env plumbing assumes one.
3. Leave `EDGE_PMUS_WS=0` available as the existing off switch.

**No edge-engine change is made under Run 83.**
