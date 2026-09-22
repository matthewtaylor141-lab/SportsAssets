# Retrieval coverage, account history, corrected policy comparison

Commits `6579f4f` (transport), `294fad2` (history query), `2a143ab`
(tape-driven replay). Branch `claude/bettor-none-pool-fix` — no service
tracks it. No orders, no capital, no credential movement, no production
change. The expired observation budget stays expired.

---

## 1. Retrieval coverage — complete, and it is eight files, not nine

| file | declared bytes | bytes read | truncated | rows | malformed | kept |
|---|---:|---:|:--:|---:|---:|---:|
| 20260913 | 276,620,306 | 276,620,306 | no | 3,128,234 | 0 | 93,375 |
| 20260914 | 203,424,837 | 203,424,837 | no | 2,298,705 | 0 | 1,671 |
| 20260915 | 181,449,046 | 181,449,046 | no | 2,061,851 | 0 | 1,609 |
| 20260916 | 181,159,652 | 181,159,652 | no | 2,069,137 | 0 | 3,595 |
| 20260917 | 174,547,611 | 174,547,611 | no | 2,000,088 | 0 | 998 |
| 20260918 | 200,164,152 | 200,164,152 | no | 2,265,799 | 0 | 2,767 |
| 20260919 | 267,206,437 | 267,206,437 | no | 3,028,336 | 0 | 17,625 |
| 20260920 | 377,904,398 | 377,904,398 | no | 4,314,723 | 0 | 83 |

**1.86 GB, 21,166,873 rows, 0 malformed, 121,723 prints for our twelve
markets.** Declared `Content-Length` equals bytes actually read on every
file; none hit the 400 MB cap. Nothing is preserved as partial because
nothing was partial.

**Eight, not nine.** Derived from retrieved rows, not filenames: file
`YYYYMMDD` covers `(YYYYMMDD−1)T17:00 → YYYYMMDDT16:59:59` ET, and the
capture ends 2026-09-20T15:52Z (11:52 ET on 09-20), inside the 20260920
session. `20260921` would be entirely after the capture.

**The boundary rule is not absolute.** Seven files run exactly one
session. `20260915` also carries **two rows stamped 2026-09-12**. Two
rows in 21.2 million — but "each file is exactly one session" is false,
and a join built on it would be wrong in a way nothing would catch.

### Dispatch SHAs, verified — and one error of mine

I re-dispatched the four cancelled dates against `claude/session-njaewf`
(the **default** branch) at `ba87076`. That commit has **no**
`.github/scripts/`, **no** fragment handling, and the **old global**
concurrency group. Runs 53–56 never streamed: they took the plain curl
path, pulled ~200 MB into `/tmp/page.html` and ran an HTML text
extractor over it; run 54 was cancelled by exactly the group I had just
fixed. **Three of them report "success."** A green run is not a
retrieved file. Re-dispatched against `claude/bettor-none-pool-fix`
(`6950fb8`) as runs 57–60 — all streamed, none cancelled. The
authoritative pass is runs 61–68 at `6579f4f`.

Artifacts and raw job logs are both served from
`*.blob.core.windows.net`, which this org's egress policy answers with
`CONNECT 403`. An artifact the analysis container cannot open is not a
retrieval, so the rows now travel on an orphan branch,
`claude/tape-data`, carrying only `tape/`. `fetch-docs.yml` gained
`contents: write` for that one push, and I removed its "no repository
writes" header rather than keep a promise it no longer honours.

---

## 2. Account history — five mechanisms, kept apart

`mirror_orders`: **11,183 real orders**, 2026-09-06 → 09-10, 1,461
books. Zero venue requests spent. **Lane note:** this is the RN1 mirror
lane, not BETTOR. Read as evidence about *venue mechanics* only; no P&L
from it enters BETTOR accounting.

| stage | count |
|---|---:|
| submitted (row written) | 11,183 |
| venue accepted (`order_id` present) | 10,865 |
| any quantity filled | 4,518 |
| maker flag TRUE | 7,484 |

Terminal: 5,417 cancelled (402 with partial fill), 3,876 filled, 1,571
expired (240 with fill), **198 `post_only_rejected`**, **72
`preview_mismatch`**, 48 lost.

### The maker fill rate, measured rather than assumed

| post_only | crossing at placement | orders | filled | **% filled** | median s to terminal |
|:--:|:--:|---:|---:|---:|---:|
| no | no | 6,717 | 1,711 | **25.5%** | 90.1 |
| no | yes | 2,127 | 2,127 | 100.0% | 1.2 |
| yes | no | 2,292 | 633 | **27.6%** | 100.1 |
| yes | yes | 47 | 47 | 100.0% | 5.6 |

A genuinely resting order filled **~26%** of the time. `post_only`
barely moves it (27.6% vs 25.5%). **Partial fills are real:** of 4,518
orders with any fill, 643 (14.2%) were partial — 459 under half (mean
fraction 0.163), 184 at half or more (0.751).

### Fee fields: present in the schema, null in practice

Receipt census over the venue's **actual** responses:

| key | rows with key | rows populated |
|---|---:|---:|
| `preview` | 10,862 | 10,862 |
| `response` (carries `executions`) | 10,862 | 10,862 |
| `expected_cost` (**ours**, computed) | 3,790 | 3,790 |
| **`venue_cost`** | 3,790 | **0** |

**`venue_cost` is null in every row where the key exists.** Our fee
model has *not* been validated against a venue-reported fee, because the
venue never reported one in this history. A fee field existing in a
response schema is not a fee field carrying a value.

### Settlement ≠ buying power — reported separately, as instructed

`mirror_books`: 1,624 closed, but only **889 carry `settled_pnl`**.
Settlement timing is observable for 55% of closed books. **Buying power
is not observable at all** — no table in this schema records account
balance. Release timing is **not established**, and is not inferred from
settlement timing. The query says so in its own output.

`live_orders` holds 166,585 rows across lanes `ioc`, `mirror`, `rest` —
a further place to investigate, not yet a complete execution sequence.

---

## 3. Corrected policy comparison

> **SUPERSEDED by `EXECUTION_CALIBRATION.md` section 5.** The
> tape column below was produced by a fill model that treated an
> EMPTY print list -- the tape saying nothing traded -- as "no
> tape" and fell back to the snapshot proxy. Every quiet interval
> was therefore still being filled by inferred volume. The
> conclusion (C2 negative at every queue assumption) survives the
> fix; the numbers do not.


The snapshot proxy's **total volume was accurate** (ratio 1.00 vs the
tape on eight of twelve markets). The defect was **price attribution**:
all of an interval's volume was priced at `lastTradePx`, one price,
while a single interval carries up to **120 distinct prices**. And the
opportunity is rarer than the proxy implied — intervals containing at
least one real print range from **34.7%** (portst-ore) down to **0.2%**
(ame-tij).

Same candidates, same four queue fractions, same accounting; only the
fill model changes.

| cand | qfrac | snapshot net $ | snapshot /ctr | **tape net $** | **tape /ctr** |
|---|---:|---:|---:|---:|---:|
| C0 | 0.00 | −134.56 | −0.002434 | −50.38 | **−0.000667** |
| C0 | 0.25 | −199.56 | −0.012123 | −168.82 | **−0.008155** |
| C0 | 0.50 | −198.62 | −0.012911 | −240.69 | **−0.013174** |
| C0 | 1.00 | −159.17 | −0.010851 | −181.59 | **−0.010859** |
| C2 | 0.00 | −126.72 | −0.013365 | −78.48 | **−0.006555** |
| C2 | 0.25 | −120.38 | −0.034123 | −78.17 | **−0.015619** |
| C2 | 0.50 | −113.84 | −0.039039 | −83.36 | **−0.022599** |
| C2 | 1.00 | −110.21 | −0.045336 | −100.82 | **−0.034442** |

**C2 is negative under every queue assumption.** The published +0.0020
per contract does not survive. C2 is a retired development result, not a
candidate for capital.

### Three limits on this table

1. **Episode counts differ between columns** (C0 qfrac 0.00: 734 vs
   817). I had written that they were "identical by construction"; the
   table beneath that sentence disproved it. Episodes are
   non-overlapping, so a model that fills more ends episodes later and
   fits fewer into the same tape. These are comparable *policy outcomes
   over one corpus*, **not** a paired per-episode comparison.
2. **Neither column reproduces `DECISION_PACKAGE_V2`**, nor should it.
   That table predates the causal-join, queue-persistence and depletion
   repairs. The snapshot column here is the *post-repair* snapshot
   model, which is why C2 reads ≈ −$127 rather than +$1.23.
3. **The tape carries no side and no aggressor flag** — four columns
   only. A print establishes that trading reached a price; it never
   establishes who initiated it. It is used only to test whether trading
   reached our quote.

Every episode now records `tape_intervals` and `snapshot_intervals`, so
the two fill models cannot be summed without the mixture being visible.

---

## 4. Unspent and unchanged

- **D3–D5: 0 of 14 requests spent.** History answered the maker-fill and
  fee questions with no venue call. What history cannot answer — when
  proceeds become reusable buying power — is the one thing worth
  spending the allowance on, and needs a window chosen deliberately.
- **M1–M3 unexecuted.** No funded order.
- Trading controls, deployment boundaries and lane separation unchanged.
