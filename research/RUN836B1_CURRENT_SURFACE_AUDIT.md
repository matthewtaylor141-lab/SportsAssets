# RUN 83.6B.1 — CURRENT-SURFACE COMPATIBILITY AUDIT

```
CURRENT_SDK_IDENTIFIED              = YES   (polymarket-client 0.10.0)
LEGACY_CLOB_BOOK_HASH_ALGORITHM_PUBLISHED = YES  (archived py-clob-client 0.34.6)
CURRENT_BOOK_HASH_ALGORITHM_PUBLISHED     = NO   (no hash helper in the current SDK)
HARNESS_CURRENT_SURFACE_COMPATIBLE  = NO -> corrected -> YES
SAMPLING_MARKETS_CURRENT_STATUS     = NOT_IDENTIFIED
CLOB_TEST_NETWORK_REACHABLE         = NO      (unchanged)
CLOB_PRICE_CHANGE_SEMANTICS         = UNRESOLVED (unchanged)
```

**Source inspection only. No CLOB connection was made. No credential was used.
Neither SDK was added as a runtime dependency of anything.**

---

## 1. WHAT WAS READ

| artifact | how obtained | role |
|---|---|---|
| `polymarket-client` **0.10.0** | PyPI wheel, extracted read-only outside the repo | **CURRENT_SDK_SOURCE** — the authority for every row below |
| `py-clob-client` **0.34.6** | already in hand; confirmed newest release | **LEGACY_SDK_SOURCE** — historical comparison only |

PyPI is reachable from this environment (it sits in the egress proxy's
`noProxy` list); every Polymarket host still answers 403 to CONNECT, which is
why this is a source audit and not a probe.

`py-clob-client` 0.34.6 **is** the newest release of that package — so the
legacy artifact we had was never stale, it is simply archived. Its newest
release being the archived one is what makes the unified client the authority.

---

## 2. THE COMPATIBILITY TABLE

| # | HARNESS_COMPONENT | CURRENT_IMPLEMENTATION (before) | CURRENT_OFFICIAL_SURFACE | MATCH |
|---|---|---|---|---|
| 1 | CLOB websocket host/path | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | same — `environments.py:100` | **YES** |
| 2 | REST host | `https://clob.polymarket.com` | same — `environments.py:99` | **YES** |
| 3 | Subscribe payload | `{"assets_ids": [...], "type": "market"}` | `{"type": "market", "assets_ids": [...], "custom_feature_enabled": bool}` — `market_protocol.py:41` | **NO** — field missing |
| 4 | Identifier field name | `assets_ids` | `assets_ids` on the wire; `asset_ids` is Python-side only, `token_ids` a deprecated alias | **YES** |
| 5 | Initial subscription | one frame at open, no later mutation | same shape; the SDK adds `{"operation":"subscribe"/"unsubscribe"}` updates only when a caller adds/removes tokens mid-socket | **YES** (the harness never mutates) |
| 6 | Order-book REST | `GET /book?token_id=...` | `("/book", {"token_id": ...})` — `_internal/actions/clob.py:148` | **YES** |
| 7 | Heartbeat / ping | none (library-level ws ping only) | app-level text `PING` every **10.0 s**, `PONG` expected, stale at **30.0 s** — `streams/clob/heartbeat.py` | **NO** — absent |
| 8 | Redirects / alternate hosts | `follow_redirects=False`, allow-list of two | the SDK uses these two hosts directly; no redirect handling, no alternate host | **YES** |
| 9 | Book-summary hash | preserved raw, never validated during collection | **no hash helper exists in the current SDK** (no `sha1` anywhere; the only `hashlib` uses are an HMAC and a pagination fingerprint) | **YES** — and see §3 |
| 10 | Event model names | not interpreted; raw frames stored | `book`, `price_change`, `last_trade_price`, `tick_size_change`, `best_bid_ask`, `new_market`, `market_resolved`; wire is flat `{event_type, ...}` | **YES** (recorder) |
| 11 | Market discovery | `/sampling-markets`, described as a liquidity proxy | absent from the current SDK; the SDK discovers via **gamma-api.polymarket.com `/markets/keyset`** | **NO** — language, see §4 |
| 12 | `price_change` frame shape *(test fake)* | single `asset_id` + `changes[]` | `price_changes` **list**, each entry with its own `asset_id` (alias `token_id`), `side`, and its own `hash` — `models/clob/market_events.py:33,101` | **NO** — fake only |

Three rows were NO on substance (3, 7, 11) and one on test fidelity (12).
Everything else matched already.

---

## 3. THE HASH — a correction carried forward

Run 83.2A recorded "no CLOB hash algorithm is published". That was **false**:
the algorithm is in the archived `py_clob_client/utilities.py`, and the earlier
read missed it by reading the websocket surface rather than `utilities.py`.
`LEGACY_CLOB_BOOK_HASH_ALGORITHM_PUBLISHED = YES` is locked.

The new fact this audit adds is the other half:

> **The current unified SDK ships no book-hash helper at all.** There is no
> `sha1` call anywhere in its 182 modules. The two `hashlib` uses are an HMAC
> (`_internal/hmac.py`, sha256) and a pagination cursor fingerprint
> (`_internal/pagination.py`, sha256) — neither is a book hash.

So whether the production stream still hashes the way the archived client
hashed is **unverified**, and the harness has been told not to rely on it. Both
`book` and each `price_change` entry still carry a `hash` field in the current
models, so the raw capture preserves the material either way. The offline
analysis may *test* the legacy algorithm against the captured bytes — a match
would be a finding; a mismatch would mean the legacy algorithm no longer
describes the surface, and the REST witness still stands alone. **Absence of a
current helper is not evidence the algorithm changed, and a legacy helper is
not evidence it did not.**

---

## 4. `/sampling-markets` AND THE DISCOVERY LANGUAGE

`SAMPLING_MARKETS_CURRENT_STATUS = NOT_IDENTIFIED`. The string does not appear
anywhere in the current SDK. That is **not** proof the venue stopped serving
it — only that the current client no longer uses it.

Per the standing instruction, the discovery mode no longer calls anything
"liquid". It prints **PUBLIC_MARKET_DISCOVERY_CANDIDATES**, states that
"sampling" is the venue's own word whose current meaning is not established
here, and on a 404 says plainly that the endpoint is archived rather than
falling back anywhere.

The current SDK's own discovery runs against **a different host**
(`gamma-api.polymarket.com`, `/markets/keyset`, with `liquidity_num_min` /
`volume_num_min` filters). That host was **deliberately not added** to the
harness allow-list: discovery is a convenience you can skip by pasting token
ids, and it is not worth widening the instrument's network surface to get it.

---

## 5. THE CORRECTIONS MADE (minimum, and nothing else)

1. **Subscribe frame** now `{"type": "market", "assets_ids": [...],
   "custom_feature_enabled": <bool>}`, field for field.
   **The default is `false`, because the SDK's own default is `false`** — the
   frame the harness sends by default is the frame the official client sends by
   default. `--custom-feature-enabled` is offered as a switch: that flag gates
   the `best_bid_ask`, `new_market` and `market_resolved` event classes, and
   its server-side effect on `book` and `price_change` is **not established**,
   so turning it on is a second, separate capture rather than the baseline.
   Whichever was used is written into the manifest.
2. **Application-level heartbeat**: text `PING` every 10 s (`--heartbeat-interval`,
   `0` disables). This is not just liveness — **without it a venue-initiated
   idle close would be recorded as an involuntary disconnect and would
   contaminate the reconnect experiment**, which is one of the things the
   capture exists to measure. The venue's `PONG` replies are recorded as
   ordinary frames, not filtered out. Each session row carries
   `heartbeats_sent`.
3. **Named surface constants** (`SUBSCRIBE_TYPE`, `SUBSCRIBE_IDENTIFIER_FIELD`,
   `HEARTBEAT_*`, `REST_BOOK_PATH`, `REST_BOOK_PARAM`) with the SDK file and
   line each was copied from, and all of them echoed into `manifest.json` —
   so the analysis never has to guess which protocol shape produced the bytes.
4. **Discovery language** per §4.
5. **Test fake** updated to the current multi-token `price_changes` shape and
   made to answer `PING` with `PONG`.
6. **Docstring** corrected: the hash paragraph now says LEGACY and unverified.

The recorder still interprets nothing. `custom_feature_enabled` and the
heartbeat are the only new bytes it puts on the wire, and both are copied from
the official client.

### Tests

`research/test_run836b_capture.py`: **23 passed** (the 20 required properties,
plus three new pins — the heartbeat constants match the SDK; the subscribe
frame is the current frame with the flag defaulting off and the heartbeat
actually firing; a `PONG` reaches the record). The outbound-operations pin was
tightened rather than relaxed: it now demands **exactly two** websocket sends —
the subscribe frame and the heartbeat, the latter identified by constant — so a
third send appearing later still fails.

Nothing contacted a venue. Script SHA-256:

```
09a4939b4f5b970e4ea814b6b053cdbbcff0161f8bf2a579d8728e225d481643
```

(previous: `88b200c6…`, capture_version `run836b/1` → `run836b/2`)

---

## 6. WHAT THIS AUDIT DID NOT DO

- Did not connect to CLOB, PMUS, gamma, or anything else.
- Did not add `polymarket-client` or `py-clob-client` as a runtime dependency
  of the harness, the collector, the broker or the backend. The harness's
  dependencies are still exactly `websockets` and `httpx`.
- Did not validate any hash during collection.
- Did not reopen the collector, the broker, the ten offsets, the 0 ms
  semantics, or migration 064.
- Did not resolve `CLOB_PRICE_CHANGE_SEMANTICS_UNRESOLVED`,
  `PMUS_FULL_REPLACEMENT_UNCONFIRMED` or `STREAM_CONTINUITY_UNVERIFIED`. Those
  need the capture, not the source.
- Did not touch the credential position: `CREDENTIAL_PATH = NOT_IDENTIFIED`,
  `PMUS_CONNECTION_SAFE_TO_APPROVE = NO`, `mirror_live = false`.
