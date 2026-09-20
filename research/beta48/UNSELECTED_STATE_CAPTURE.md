# BETTOR_UNSELECTED_STATE_V2 — the frozen sampling rule

Owner directive 2026-09-20: "Freeze the sampling rule BEFORE row 1."

V2 frozen 2026-09-20 19:31Z, before any outcome matured. `RULE_SHA` is computed over the
frozen rule and stamped on every row, so a rule edited later without a
version bump appears as a second sha against the same
`UNIVERSE_VERSION` — the condition §4 forbids, made detectable rather
than trusted.

Authoritative copy: `backend/sportsassets/bettor_state_capture.py`.
This file is the readable statement of it, not a second source.

---

## WHAT IT MEASURES, AND THE THREE THINGS IT DOES NOT

| | estimand | status |
|---|---|---|
| **A** state economics | `E[SETTLEMENT − QUOTE \| STATE]` | **what this dataset measures** |
| **B** fill probability | `P(FILL \| STATE, QUOTE)` | NOT_IDENTIFIED |
| **C** fill-conditional adverse selection | `E[SETTLEMENT − QUOTE \| FILLED, STATE]` | NOT_IDENTIFIED |
| **D** maker EV | B, C, fees, rebates, inventory | NOT_IDENTIFIED |

The quantity is named `UNCONDITIONAL_QUOTE_TO_SETTLEMENT_VALUE`.
`UNCONDITIONAL_MAKER_ADVERSE_SELECTION` raises `ForbiddenName` — a
statistic's name is the part that survives into a summary, so the name
is enforced rather than documented.

**Never substitute A for C. Never substitute A for D.**

---

## §4. THE FROZEN RULE

```
UNIVERSE_VERSION  = BETTOR_UNSELECTED_STATE_V2
RULE_SHA          = c3af1bfca7f36c22…  (stamped on every row)
```

**ELIGIBILITY_RULE** — a venue market is eligible iff it carries a
resolvable venue-native identity (`market_slug`, `event_slug`,
`side_norm` all present) and its premap row was refreshed within 7,200s.
Nothing about price, spread, depth, volume, volatility or expected
profitability enters eligibility.

**MARKET_TYPES_INCLUDED** — every market type, whether or not
`bettor_sport_mapping` resolves it to a sport. An unresolved sport is a
gap in the mapping, not a reason to drop the row.

**MARKET_TYPES_EXCLUDED** — the declared non-sport categories
(`election_`). A category exclusion fixed before collection, not an
economic one.

**SAMPLING_CADENCE** — 300s buckets, one row per market per bucket,
read across the 5 ticks inside the bucket rather than in one burst. A
second read in the same bucket is discarded by primary key, so the row
count measures the market and not how often the loop ran.

**MAX_MARKETS** — 40 **markets** per cycle (8 per 60s tick). Markets,
not legs: the venue's book endpoint takes a slug and knows nothing about
sides, so the yes and no legs of one market share one read. A slice
holding more records `SLICE_TRUNCATED` with the count and the worker
logs an error — **a cap that binds routinely is a fixed panel, not a
rotation.**

**MARKET_SELECTION_METHOD** —
`slice(id) = int(sha256("BETTOR_UNSELECTED_STATE_V2|" + id)[:8], 16) % 787`,
where `id` is the market slug.
A market's slice depends only on its venue identifier and the frozen
version string. No book has been read at the moment the choice is made,
so the choice cannot depend on one.

**ROTATION_METHOD** — `cycle = floor(epoch/300) % 787`; that cycle's
slice is the bucket's membership, read across the bucket's 5 ticks.
Full rotation 236,100s = **65.58h = 2.73 days**. The start of the
within-slice ordering advances one place per rotation, so a tick
abandoned to rate limiting does not drop the same tail every pass.

> The period is deliberately **not** commensurate with 24h. A period
> that divides or equals a day would sample every market at the same
> hour forever — a market drawn at 03:00 UTC would never be seen
> pregame. Time of day tracks kickoff times, kickoff times track
> liquidity, and liquidity tracks the economics being measured, so a
> 24h period is a selection on economics arrived at by arithmetic
> rather than by intent. 787 is prime and 65.58h is not a whole number
> of days, so sampling times precess through the clock.

Sized on the **measured** universe (`research/bettor_unselected_sizing.sql`,
2026-09-20 19:26Z): 47,078 eligible legs = 23,539 markets across 1,493
events. 23,539 / 787 ≈ **30 per slice** against a cap of 40 — headroom
before the cap binds again. Throughput **11,520 reads/day**, full pass
**2.73 days**.

**TIME_TO_EVENT_REQUIREMENTS** — NONE. Recorded on every row, filters
nothing. A window would select states by how close they are to
resolution, which is a selection on the dynamics being measured.

**IDENTITY_REQUIREMENTS** — venue-native deterministic fields only, with
the venue's raw `sports_type`/`team_league` carried beside the mapped
values. No fuzzy title matching, no approximate team-name matching, no
price matching.

**BOOK_READABILITY_REQUIREMENTS** — **NONE FOR INCLUSION.** A selected
market writes a row whether or not its book parses; an unreadable book
is stored with a named reason. Requiring a readable book would condition
the frame on readability, and readability tracks liquidity.

**RETENTION_POLICY** — append-only. No `UPDATE`, no `DELETE` in the
writer; outcomes live in separate tables keyed to the observation, so no
future value can rewrite the state that preceded it.

---

## §3. WHAT SELECTION MAY NOT DEPEND ON

`RN1_TRADED_IT` · `FERRARI_TRADED_IT` · `BETTOR_LIKES_IT` ·
`SPREAD_LOOKS_PROFITABLE` · `LATER_OUTCOME_WAS_INTERESTING` ·
`MAKER_ECONOMICS_LOOK_ATTRACTIVE`

Selection is a pure function of (venue identifier, clock). A test
asserts over `select()`'s signature and its code — with comments and
string literals stripped — that no book, price, depth, volume, outcome
or settlement term appears in it.

The existing BETTOR lane orders its universe by `updated_at DESC`, which
selects on recent venue activity. Correct for a decision lane watching
live markets; fatal here. It is **not** reused: this capture fetches the
whole eligible set with no `ORDER BY` and no `LIMIT`, and lets the hash
decide.

---

## §6. OUTCOMES, APPENDED SEPARATELY

Observable horizons: **60s, 300s, 900s, 3600s**. Each appended row
carries `ACTUAL_LAG_S` — a read landing 74s after T0 is a 60s-horizon
row whose actual lag is 74, never relabelled as 60 and never
interpolated toward it.

Not observable: **5s, 15s, 30s** — `NOT_OBSERVABLE_AT_THIS_CADENCE`. The
loop ticks at 60s and the venue is paced deliberately, so no read exists
between T0 and T0+60s. A horizon with no read behind it would be an
interpolation presented as an observation.

Settlement carries `SETTLEMENT_SEMANTICS_STATUS` as its own field,
currently `SEMANTICS_NOT_VERIFIED`: "did this slug settle at 1 for the
side we quoted" is a venue convention, not an arithmetic fact, and a
dataset that assumed it would carry a sign error nobody could see.

Every outcome row carries `is_not_a_fill`, CHECKed at the schema level.
**This dataset contains no order, so it contains no fill.**

---

## STANDING CONSTRAINTS

`READ_ONLY = true` · no order path in the import graph (tested) ·
`CAPITAL_AT_RISK = 0` · `mirror_live = false` · mandate not activated ·
COMMAND not deployed.

Kill switch: `BETTOR_STATE_CAPTURE=off`.


---

## V1 — SUPERSEDED AFTER 8 MINUTES, ROWS RETAINED

V1 ran 19:23:35Z → 19:31Z on 2026-09-20 and wrote 14 rows. It was
replaced because **its rotation was not a rotation.**

V1 sized 277 slices and a cap of 40 against an *assumed* ~9,700
eligible legs, giving ~35 per slice. The measured figure is **47,078**
— 170 per slice. The cap therefore bound on every pass, and because the
within-slice ordering is a stable hash, the same 40 markets were drawn
from each slice forever: a fixed panel of ~11,000 legs with ~36,000
never sampled. Every V1 row carries `slice_truncated = true`, which is
how it was caught in the first two buckets.

The regression test meant to prevent exactly this asserted against the
*assumed* 9,700 and passed. An assumption asserted against itself
proves nothing; the test now reads `MEASURED_UNIVERSE`, which carries
the query that produced it.

Two further defects fixed in the same version:

- **One book read per leg.** The venue's book endpoint takes a slug, so
  the yes and no legs issued two identical requests for one payload.
  V2 samples markets. Halves the requirement, 47,078 → 23,539.
- **Whole slice read as one burst.** 40 requests at 0.4s through a
  shared gateway is ~16s at 2.5 req/s on top of every other loop.
  **Ten of V1's first fourteen reads came back HTTP 429.** V2 spreads
  the bucket across its 5 ticks; rate-limited ticks skip follow-ups
  rather than pushing through the limit.

**V1's rows are kept.** They are honest observations of the states they
saw and carry their own `universe_version` and `rule_sha`; deleting
them would be a retrospective edit of collected data. They are simply
not a sample of the universe, and any analysis must filter to V2.

**No outcome was seen before the change.** Every maturation count was 0
and no settlement existed; the status query carries no
settlement-minus-quote term by construction. §4 requires the version
bump regardless — the rule changed, so the version changed — but the
change was forced by a coverage defect visible in the first two
buckets, not by anything the data said.
