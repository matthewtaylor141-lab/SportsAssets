# RUN THE READ-ONLY COLLECTOR FROM AN EXISTING BETTOR ENVIRONMENT

No egress workaround. This is the already-built read-only collector, run
from a machine BETTOR already controls that legitimately reaches both
venues.

## Proof from the code path — run this first, anywhere

```bash
python3 research/trackx_readonly_audit.py     # exit 0 = the five properties hold
```

It walks the **real import closure** (every `_load`, `spec_from_file_location`
and `with_name` the entry point reaches, transitively — 8 files for Phase X,
6 for P2), strips comments and docstrings via the AST, then greps the
executable lines. Current result, `AUDIT = PASS`:

| property | result | how it is established |
|---|---|---|
| **1. No POST/PUT/PATCH/DELETE possible** | CLEAN | No write verb appears in any executable line of either closure. Phase X's Kalshi client additionally raises `ReadOnlyViolation` on any method that is not GET. |
| **2. No order-placement function imported or reachable** | CLEAN | No `place_order`, `cancel_order`, `create_order`, `submit_order`, `/orders` or `portfolio` in the closure. The client object has no such attribute — pinned by `test_10_no_write_endpoint_is_reachable`. |
| **3. `mirror_live` remains false** | CLEAN | The string never appears. Nothing in the closure can read or set it; the collectors do not touch the database where it lives. |
| **4. No production position modified** | CLEAN | No `asyncpg`, `get_pool`, `UPDATE`, `INSERT INTO` or `DELETE FROM` anywhere in the closure. The collectors' only output is files under `research/evidence/`. |
| **5. No production trading worker invoked** | CLEAN | No `live_executor`, `sportsassets.workers`, `workers.all`, `import edge` or `KalshiAdapter`. Phase X deliberately uses its **own** read client rather than the trading adapter. |
| credentials | CLEAN | No `os.environ`, `getenv`, `PRIVATE_KEY`, `SECRET_KEY`, `ACCESS-KEY`, `load_pem_private_key` or `ADMIN_TOKEN`. The collector **cannot authenticate even if asked to**. |
| hosts reachable by a request | `gateway.polymarket.us`, `api.elections.kalshi.com` | Nothing else. `api.polymarket.us` and `trading-api.kalshi.com` do not appear. `docs.polymarket.us` appears **only as a provenance citation string** (`SOURCE = ...` recording where a fee rule came from) and is never requested — the audit reports it separately rather than waving it through. |

Two audit refinements were needed and are worth naming, because each was a
false positive that a lazier fix would have turned into a blind spot: a line
**defining a denylist** of order paths is the guard, not the threat (exempted
by the name being assigned, so a real `place_order` call still fails even in
that file); and a URL bound to `SOURCE` is provenance, not a network call
(recognised by the name it binds to, not by the pattern).

---

## `EXISTING_HOST_CANDIDATE`

**Primary: a BETTOR developer workstation.** Zero production risk, no Render
involvement, nothing shared with a trading process. This is the recommended
host and the fastest path today.

**Secondary: Render Shell on `sportsassets-workers`** (worker, `standard`
= 2 GB, does not serve traffic). Use only if the workstation's network is
restricted.

### What is already proven about each venue

| host | PMUS `gateway.polymarket.us` | Kalshi `api.elections.kalshi.com` |
|---|---|---|
| Render (`sportsassets-api` / `-workers` / `edge-shadow`) | **not proven** — production PMUS code targets `api.polymarket.us` (`obs/handshake.py:51`, `pmus.py:50`), a different subdomain. Same registrable domain, so very likely, but not evidenced. | **proven in production** — `app.py:1465` `KALSHI_PUBLIC_API` and `edge-engine/src/edge/venues/kalshi.py:25` both call it live, unauthenticated |
| GitHub Actions runner | **proven** — every Track B-L block and Phase 2 capture ran there against this host | **untested**, and see the note below |
| developer workstation | unknown until the one-liner below | unknown until the one-liner below |

```
BOTH_VENUES_REACHABLE_FROM_HOST = NOT_VERIFIED
```

I will not claim otherwise from this container: I cannot reach either host,
so every row above is inference from code and past runs, not a live probe.
The 20-second command below settles it on whichever host you pick.

### About GitHub Actions — surfacing, not acting

Actions is BETTOR-controlled and **verifiably reaches PMUS**. It is the one
environment already proven for half the problem. But you told me twice not to
use Actions for the Kalshi side, so I have not tested Kalshi there and have
not prepared a workflow that would. If you want that route, say so
explicitly and it is a one-line addition to an existing workflow. Until then
it stays off the table.

---

## `EXACT_COMMAND`

### Step 0 — reachability (20 seconds, decides everything)

```bash
for h in gateway.polymarket.us api.elections.kalshi.com; do
  printf '%-34s ' "$h"
  curl -sS -o /dev/null -w '%{http_code}\n' --max-time 12 "https://$h/" || echo UNREACHABLE
done
```

Two non-`000` responses ⇒ `BOTH_VENUES_REACHABLE_FROM_HOST = VERIFIED`,
continue. Otherwise stop and tell me which host failed.

### Step 1 — get the code and prove it is read-only

The repository is public, so this needs **no credential**.

```bash
cd /tmp
git clone --depth 1 -b claude/session-njaewf \
  https://github.com/matthewtaylor141-lab/SportsAssets.git px
cd px
python3 -m pip install --quiet --user httpx        # only if httpx is absent
python3 research/trackx_readonly_audit.py          # MUST print AUDIT = PASS
```

**If the audit does not print `AUDIT = PASS`, stop and send me the output.**
Do not run the collector.

### Step 2 — run Phase X (cross-venue, both venues from this one process)

```bash
python3 research/run85_phasex_run.py \
  --series KXNFLGAME KXNFLSPREAD KXMLBGAME KXNBAGAME KXEPLGAME \
  2>&1 | tee /tmp/phasex_run.log
```

Runtime roughly 10–30 minutes: a 2.5 s floor on PMUS discovery and a 1.0 s
floor on Kalshi, with a sealed receipt for every request including the ones
that fail.

### Step 3 (optional, same host) — start the P2 forward capture

```bash
python3 research/trackp2_capture.py --stage discover
python3 research/trackp2_capture.py --stage snapshot   # then every ~5 min
python3 research/trackp2_capture.py --stage settle     # then hourly
```

`discover` is the only one that needs running immediately; `snapshot` and
`settle` are idempotent and safe to put on a 5-minute and hourly cron.

---

## `EXPECTED_OUTPUT`

Phase X prints a report ending in one gate line and writes three files under
`research/evidence/phasex/`:

```
PMUS_SPORTS_MARKETS          = <n>   (live board)
KALSHI_SPORTS_MARKETS        = <n>
POTENTIAL_MATCHES            = <n>
EQUIVALENCE_VERIFIED         = <n>
EQUIVALENCE_REJECTED         = <n>
EQUIVALENCE_NOT_IDENTIFIED   = <n>
VERIFIED_PAIRS_WITH_FRESH_BOOKS = <n>
GATE = A | B | C | D | E
```

```
research/evidence/phasex/phasex_report.txt          human-readable
research/evidence/phasex/phasex_result.json         the gate + counts
research/evidence/phasex/phasex_kalshi_records.json normalized contracts
research/evidence/phasex/phasex_receipts.json       every request, hashed
```

A plausible honest outcome remains `GATE = D` (equivalence bottleneck): the
payoff proof chain is strict by design and a moneyline whose venue fields
contradict its prose is refused rather than guessed. That is a measurement,
not a failure.

---

## `HOW_TO_RETURN_OUTPUT_TO_CLAUDE`

One command, then paste the result:

```bash
cd /tmp/px && tar -czf /tmp/phasex_evidence.tgz \
  research/evidence/phasex research/evidence/trackp2 2>/dev/null
base64 -w0 /tmp/phasex_evidence.tgz > /tmp/phasex_evidence.b64
wc -c /tmp/phasex_evidence.b64
```

- If under ~200 KB: paste the contents of `/tmp/phasex_evidence.b64`.
- Otherwise paste `/tmp/phasex_run.log` plus
  `research/evidence/phasex/phasex_result.json`, and send the `.tgz`
  separately.

Do **not** push from that host — pushing needs a credential and none of this
requires one.

---

## If no BETTOR environment can reach both

Then the permission needed is precise and small:

> **Who:** whoever administers outbound network policy for the BettorToken
> research environment (Render account owner for a Render host; IT/network
> for a workstation).
> **What:** outbound HTTPS, **GET only**, to exactly
> `gateway.polymarket.us:443` and `api.elections.kalshi.com:443`.
> **Not needed:** any credential, any account access, any inbound rule, any
> other host.

That is the same ask as `PHASEX_EGRESS_REQUEST.md`, now with the added fact
that **both** hosts must be open **from the same machine** — Phase X X2
compares two books captured seconds apart, and two environments cannot
produce a contemporaneous pair.

`mirror_live = false`. No capital, no orders, no production writes, no
credential changes.
