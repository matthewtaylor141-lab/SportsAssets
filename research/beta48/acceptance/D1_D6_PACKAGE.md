# D1–D6 data acquisition + corrected experiment proposal

Trading disabled. No funded orders. Production on `ba87076`.

---

## 1. C2 retired

**`COMBO_touch_flatten_wide` / "C2", +0.0020 per contract — SUPERSEDED
DEVELOPMENT RESULT.** Preserved with its defects named:

| defect | effect |
|---|---|
| **lookahead ladder join** | the queue at entry was read from a book polled *after* entry (median 2.5 s) |
| **per-snapshot queue** | recomputed each interval, so new orders at our price pushed us *back*, inverting price-time priority |
| **depletion as a fill bound** | level shrinkage treated as a constraint on executions, when it also reflects cancels and repricing |

Corrected, it is **−$120.38 at qfrac 0.25**, and negative at every
queue assumption and execution scenario.

**What this is evidence about:** these four policies, on eleven
markets over eight days, under a stated simulated execution model with
our queue position swept because it is unobservable. **It is not a
rejection of market making**, and it is not a statement about any
policy not in the declared set.

---

## 2. Time & Sales — retrieval built, and the schema reconciled from real rows

### Executable

`.github/workflows/timesales-fetch.yml`. Streams each daily CSV on the
runner through a filter; the source file is never held (peak source
disk **zero**, peak memory one 1 MiB chunk), so the 1.8 GB window is a
design parameter, not a blocker. Inputs `symbols`, `dates`,
`max_files`, `max_bytes`; the declared byte total is checked against
`max_bytes` **before** any download.

**Source identity preserved per file**: declared size, declared eTag,
`lastModified`, the header as served, `rows_total`, distinct symbols,
bytes observed, `size_matches_manifest`, and the SHA-256 of the bytes
actually read. Every emitted row carries its `source_file` and
`source_etag`.

**Blocked on one thing, and it is not a capability gap.** GitHub only
exposes `workflow_dispatch` for workflows present on the repository's
**default branch** — and the default branch here **is
`claude/session-njaewf`**, the branch every Render service auto-deploys
from, currently at the authorized `ba87076`. Landing the file there
would redeploy production off a new SHA. **I have not done that.** It
needs your call: either land the workflow there (application code
unchanged, but both services redeploy), or point me at another runner.

### Reconciled from real rows — retrieved, not assumed

Fetched the head of `20260913-time-and-sales.csv` through the
already-dispatchable reader:

```
2026-09-12T17:00:58.066611396-04:00,aec-mlb-laa-wsh-2026-09-12,0.100,3.0400000000
2026-09-12T17:00:58.072400811-04:00,asc-cfb-ala-uk-2026-09-12-neg-3pt5,0.52,47.5000000000
2026-09-12T17:00:58.077293532-04:00,aec-mlb-lad-mia-2026-09-12,0.525,35.5200000000
2026-09-12T17:00:58.088043074-04:00,tec-cfb-champ-2027-01-25-w-ore,0.05,9.0000000000
```

| question | answer, from the data |
|---|---|
| **timestamp format** | ISO 8601, **nanosecond** precision, explicit **−04:00** offset. Not UTC — conversion required against our `local_request_wall_utc` |
| **Symbol vs marketSlug** | **identical namespace and form** — `aec-`, `asc-`, `atc-`, `tec-`. The join key is confirmed |
| **price precision** | **inconsistent** — `0.100`, `0.52`, `0.05`, `0.525`. Both grids appear; parse as decimal, never as a fixed width |
| **quantity** | fractional to 10 dp (`663.7400000000`) — confirms fractional shares |
| **date alignment** | **the file named `20260913` opens with rows stamped `2026-09-12T17:00` ET.** The filename is the **session** date, not the calendar date of every row. Our 09-13→09-20 window therefore needs files `20260913`…`20260921` |
| unexpected symbols | `caoc-d9b05245a34de17c` — an opaque form, neither slug-like nor in our universe. Must be classified, not silently dropped |

**Completeness check to run before any join**, per market per day:
total printed quantity from the tape vs the `sharesTraded` delta we
already hold. A mismatch invalidates the join; it is not averaged away.

**Aggressor identity stays unknown** — the docs say so explicitly — and
so does our queue position. Real prints remove the multi-price
attribution problem and nothing else.

---

## 3. D1–D6

| # | method | URL / endpoint | body | pagination | retries | byte bound | runtime | artifact |
|---|---|---|---|---|---|---|---|---|
| **D1** | GET | `https://www.polymarketexchange.com/files/time-and-sales/manifest.json` | — | none | 2 | ~50 KB | <5 s | `timesales_report.json` |
| **D2** | GET | `.../files/time-and-sales/{filename}?v={eTag}` × 9 (`20260913`…`20260921`) | — | none | 2/file | **cap 2.5 GB**, ~223 MB/file | ~25 min | `timesales_filtered.csv.gz` + report |
| **D3** | GET | `/v1/incentives` | — | `nextCursor`, **cap 10** | 2 | <1 MB | <2 min | `incentives.json` |
| **D4** | GET | `/v1/account/balances` | — | none | 2 | <10 KB | <5 s | `balances_at_rest.json` |
| **D5** | GET | `/v1/portfolio/positions` | — | `nextCursor`, cap 2 | 2 | <100 KB | <5 s | `positions_at_rest.json` |
| **D6** | POST | `/v1/order/preview` | `{"request":{"marketSlug":"<2-tick market>","intent":"ORDER_INTENT_BUY_LONG","type":"ORDER_TYPE_LIMIT","price":{"value":"0.44","currency":"USD"},"quantity":4,"tif":"TIME_IN_FORCE_GOOD_TILL_CANCEL"}}` | none | **0** | <10 KB | <5 s | `preview_contract.json` |

**Request arithmetic: 1 + 9 + 10 + 2 + 2 + 3 = 27.** (D2 is 9, not 8 —
the date-alignment finding adds `20260921`.) Retries are inside each
row's bound. Pacing 0.10 req/s for D3–D6.

### Authorization status

| | |
|---|---|
| **D1, D2** | **public, no credentials, no account.** Already within read-only research authorization. Blocked only by the default-branch dispatch constraint above |
| **D3, D4, D5** | **authenticated reads against our own account. NEW ALLOWANCE REQUIRED.** The probe budget is expired and is **not** being re-armed; this is a separate, smaller, read-only grant of 14 requests |
| **D6** | **POST. NEW ALLOWANCE REQUIRED, and quarantined** — see §4 |

**No re-arm of the expired probe. No expansion of its budget.**

---

## 4. Preview semantics — what I can and cannot establish

**Retracted:** my previous inference from "the SDK response type has no
`executions` field." That is an SDK artefact, not the endpoint
contract.

**What I did establish**, from
`https://docs.polymarket.us/institutional/oapi-schemas/trading-schema.json`:

> `"UnsolicitedCxlReason"` — `CONNECTION_LOSS`, `LOGOUT`,
> `EXCHANGE_OPTION`, `OTHER`. *"a code to identify the reason for an
> unsolicited cancellation."*

**The venue cancels our resting orders on connection loss and on
logout.** That is documented behaviour with direct consequences for a
maker policy — our quotes do not survive a session drop — and it is now
sourced rather than assumed.

**What I did NOT establish: the preview contract itself.** The trading
schema is ~1,275 lines and I retrieved only its tail. The preview
request/response definition sits above what I pulled. **Reading it is
the next step, and it is free** — one more `fetch-docs` call, no
allowance needed. It should happen before D6 is authorized, not after.

### Ledger search — M1 is the only route, and here is the search

Before claiming that, I looked:

| searched | result |
|---|---|
| `research/` and `backend/` for balance / position / activity / ledger artefacts | `LEDGERS.md` (RN1 economics), `forward/ledger.py`, `shadow/position_state.py` — **none records our PMUS account** |
| captured responses for `account/balances`, `portfolio/positions`, `portfolio/activities` | **zero** — the only files mentioning these endpoints are my own documents |
| `backend/migrations/` for collateral / buying_power / unsettled / margin columns | one hit, `050_mirror_shorts.sql`; `api_positions` holds **tracked third-party accounts on the global CLOB**, not us |

**No BETTOR order has ever been placed**, so no ledger of ours can
contain a collateral release. M1 is the only route **unless** the
preview contract turns out to report collateral — which is exactly why
reading it comes first.

---

## 5. Experiment arithmetic, reconciled line by line

`bettor_experiment_budget.py`. Reference book: bid 0.49 / ask 0.51,
one-cent grid, 4 contracts — the most expensive point on the fee curve.

| | M1 | M2 | M3 |
|---|---|---|---|
| order | BUY_LONG 4 @ 0.44 GTC | BUY_LONG 4 @ 0.49 GTC, cancel at once | BUY_LONG 4 @ 0.51 then BUY_SHORT 4 @ 0.51, both taker |
| **gross order exposure** | $1.7600 | $1.9600 | $4.0800 |
| **peak committed capital** | $1.7600 | $1.9600 | $4.0800 |
| maker rebate if filled | $0.0100 | $0.0100 | — |
| taker fees | — | — | $0.1400 |
| residual if cleanup fails | 4 long | 4 long | a matched pair, pays exactly $4.00 |
| **maximum loss** | **$1.7500** | **$1.9500** | **$2.1100** |

**M3's derivation, because the worst path is not the obvious one.**
Both legs fill → pay $4.08, receive $4.00, pay $0.14 fees = **−$0.22**.
**One leg only** → pay $2.04 for a single leg that can settle at zero,
plus $0.07 fee = **−$2.11**. The worst path is the *incomplete pair*,
not the complete one.

| | |
|---|---|
| gross order exposure, all three | **$7.80** |
| **peak committed capital, SEQUENTIAL** | **$4.08** ← the funding requirement |
| peak committed capital if run concurrently | $7.80 (not the plan) |
| **maximum combined loss** | **$5.81** |

### Every change from the previous numbers

| | |
|---|---|
| **$16.00 exposure → $7.80** | $16.00 summed rounded-up collateral *ceilings* ($4+$4+$8). $7.80 is the actual order notionals |
| **$12.20 → $12.24 was itself wrong** | I said the difference was "$0.14 of fee", but $4.00 + $0.14 = $4.14, not the $4.24 I wrote. **The stated reason did not produce the stated number.** |
| **$12.24 → $5.81** | the old totals reported collateral ceilings as maximum loss. Losses are now derived per path, including failed cleanup and residual |
| **new: peak vs sum** | the experiments are sequential, so the funding requirement is the largest ($4.08), not the total ($7.80). Neither previous report distinguished these |

**A preview that fails to answer a question is a reason to propose an
experiment, not permission to place an order.** M1–M3 require their own
capital authorization regardless of what D6 returns.

---

## 6. Final declared policy set — with and without rebates

Corrected replay, `TRADE_ONLY` scenario. **No retuning: this set is
final.**

| policy | rebates | qfrac | eps | filled | events | sum $ | per contract |
|---|---|---:|---:|---:|---:|---:|---:|
| P0 two-sided base | PUBLISHED | 0.25 | 791 | 165 | 11 | −199.56 | −0.036567 |
| | PUBLISHED | 1.00 | 795 | 147 | 11 | −159.17 | −0.000934 |
| | EXCLUDED | 0.25 | 791 | 165 | 11 | −222.49 | −0.036977 |
| P1 inventory-aware | PUBLISHED | 0.25 | 855 | 121 | 11 | −251.36 | −0.036732 |
| | EXCLUDED | 0.25 | 855 | 121 | 11 | −267.45 | −0.037094 |
| **P2 wide/touch/flatten** | PUBLISHED | 0.25 | 374 | 40 | **5** | **−120.38** | **−0.004242** |
| | PUBLISHED | 1.00 | 386 | 22 | 5 | −110.21 | −0.003887 |
| | EXCLUDED | 0.25 | 374 | 40 | 5 | −126.34 | −0.004430 |
| P3 complete-not-exit | PUBLISHED | 0.25 | 866 | 212 | 11 | −299.92 | −0.037049 |
| | EXCLUDED | 0.25 | 866 | 212 | 11 | −325.48 | −0.037452 |

**Positive point estimates: NONE. Lower bounds above zero: NONE.**

Rebates move the total by roughly $20 over 791 episodes and **never
change the sign**. P2 remains the least-bad structure and is still
losing four tenths of a cent per contract.

**Incentive-aware LP: still not evaluable, and the reason is unchanged
and structural.** The observed programme was 53 markets, all UFC; our
capture contains **zero** UFC markets. D3 is what changes that. Nothing
here books incentive revenue, and no share of any pool below 100% can
be justified while competitor scores are unobservable — nor may 100% be
assumed.

---

## 7. What I need from you

1. **A route for D1/D2.** Land `timesales-fetch.yml` on
   `claude/session-njaewf` (application code unchanged; both Render
   services will redeploy off a new SHA), or nominate another runner.
2. **A read-only allowance for D3–D5**: 14 requests, our account, no
   orders. Separate from the expired probe budget, which stays expired.
3. **Nothing for D6 yet** — I will read the preview contract from the
   trading schema first, at no cost, and come back.

M1–M3 remain unfunded and unrequested.

---

## 8. Reproducing

```bash
python research/beta48/bettor_experiment_budget.py   # the $5.81 derivation
python research/beta48/bettor_capital.py             # capital-hours
python research/beta48/bettor_tape.py                # causal ladders
python -m pytest research/beta48/test_bettor_policy_ev.py -q   # 29
```

Data status: **DEVELOPMENT** throughout.
