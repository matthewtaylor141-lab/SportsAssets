# Is there an autonomous shadow opportunity right now? A bounded assessment

Measured 2026-09-25 against production at SHA `52a7104` (API-only release),
`command-verify` runs 67 (36187858957) and 68 (36188978059), 6-hour window.
The funnel comes from **one** live cycle at 20:51:17Z — the loop's cadence is
`CYCLE_S = 900 s`, so both runs read the same cycle heartbeat and the funnel
is one measurement observed twice, not two. Every number is read from the run,
not derived. Where the two runs differ it is only the rolling window's own
totals (run 67: 150 rows, 107 UNKNOWN; run 68: 145 rows, 102 UNKNOWN), and
`POSITIVE_EDGE 0` in both.

**The answer: no. No candidate qualified, and no trade was forced.** The
autonomous research lane holds **0 positions** and its P&L is empty.

**And the blockers are three different things, at three different stages.**
Keeping them apart is the point of this document, because they have nothing
to do with one another and only one of them is about economics:

| sport | where it stops | what that is |
|---|---|---|
| soccer (EPL, Liga MX) | **identity / catalogue** — before any payout comparison | 29 priced fixtures, 0 evaluated |
| baseball (MLB) | **settlement** — an established conflict, plus silence | 43 INCOMPATIBLE, 107 UNKNOWN |
| baseball (MLB) | **price** — independent of settlement | 99 priced, **0 positive edge** |

---

## 1 · The observed universe, and where it narrows

The venue's open, fresh markets in the supported sports, counted per label
from the rows themselves:

```
markets_considered 296     venue universe by label {"MLB": 66, "Soccer": 230}
```

**Soccer is not absent from the venue. There are 230 open Soccer rows.** That
was the first thing worth establishing, and it rules out "the venue does not
list it" as an explanation.

The funnel, per provider sport (`funnel_by_provider_sport`, new in this
release, reporting only — no gate reads it):

| provider sport | venue open+fresh | provider events | with Pinnacle h2h | **mapped to a venue contract** | evaluated | written |
|---|---|---|---|---|---|---|
| `soccer_epl` | 230 | 20 | 20 | **0** | 0 | 0 |
| `soccer_mexico_ligamx` | 230 | 9 | 9 | **3** | **0** | 0 |
| `baseball_mlb` | 66 | 23 | 16 | 10 | 6 | 6 |

Refusals, per sport:

```
soccer_epl             {"NO_VENUE_CONTRACT_FOR_EVENT": 20}
soccer_mexico_ligamx   {"NO_VENUE_CONTRACT_FOR_EVENT": 6}
baseball_mlb           {"NO_PINNACLE_ON_EVENT": 7,
                        "VENUE_MAPPING_AMBIGUOUS": 3,
                        "VENUE_CONTRACT_IS_A_SEGMENT_NOT_FULL_GAME": 2,
                        "VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE": 3}
```

### The soccer finding, precisely

**A connected source does supply the probability.** All 20 EPL events and all
9 Liga MX events carried a Pinnacle h2h. Coverage on the source side is
complete for what the provider returns.

**None of them reaches a valuation, and the reason is the contract's
identity, not its payout.** Two distinct failures:

* **EPL: 20 of 20 matched no venue contract at all.** The 230 Soccer rows do
  not contain a PMUS money line for any EPL fixture the provider returned.
* **Liga MX: 3 of 9 matched a `markets` row, and then died at the premap.**
  The venue's own error text, verbatim:

  ```
  NO_VENUE_NATIVE_CONTRACT_IN_PREMAP
    "the venue's own catalogue carries no contract for this fixture and
     outcome. `markets.slug` is the GLOBAL id and the venue does not accept it"
    mex-atla-mon1-2026-09-25-mon1
    mex-tij-atl-2026-09-25-exact-score-0-3
    mex-gua-que-2026-09-26-gua
  ```

  So those Soccer rows are **global-CLOB** rows, not PMUS-native contracts.
  And note the middle one: `exact-score-0-3` is an **exact-score** market, not
  a money line — it matched on team names and would have been the wrong payout
  event even if the venue had accepted the slug. (The equivalent guards do
  fire for baseball: `VENUE_CONTRACT_IS_A_LINE_MARKET_NOT_A_MONEYLINE` 3,
  `..._SEGMENT_NOT_FULL_GAME` 2.)

**Therefore soccer's payout rules were never compared, on either side.** The
settlement census over the window contains **no soccer rows at all** (§2). I
am not reporting soccer as incompatible. I am reporting it as **unexamined at
the payout level, for an upstream reason**, which is a different finding with
a different remedy.

One thing the run did establish on our own side: the bookmaker's **soccer**
rules are now captured verbatim
(`backend/tests/fixtures/pinnacle_soccer_rules_2026_09_25.json`), because the
terms extractor's vocabulary had been baseball-only and that section had never
been read. It is recorded and **not** yet turned into `BOOK_TERMS`: with zero
soccer candidates, a soccer book side would change no verdict. The fixture
lists what is still missing for a real soccer comparison — the venue's own
soccer prose, whether its contract is 2-way or 3-way, and whether it grades on
90 minutes or on the match result (Pinnacle excludes extra time and
penalties; a venue that pays "the winner of the match" would disagree on
exactly that branch). None of that is assumed.

---

## 2 · Payout rules established affirmatively, never by absence

```
-- 3b · settlement coverage, over the window --
  COMPATIBLE rows 0
  UNKNOWN       rows 107  admissible 0  distinct_slugs 10
  INCOMPATIBLE  rows  43  admissible 0  distinct_slugs  9
     UNKNOWN      / baseball  rows 107  admissible 0  distinct_slugs 10
     INCOMPATIBLE / baseball  rows  43  admissible 0  distinct_slugs  9
  conflicting conditions {"POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED": 43}
```

Every row is baseball. The per-sport split is what makes that visible; the
aggregate alone would have hidden it.

The venue prose was read live, per candidate the lane actually evaluated —
no longer pinned to two hardcoded baseball slugs. Each description is
379–384 characters and yields exactly one stated condition of seven:

```
STATED     POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED
           -> PAYS_THE_LAST_FAIR_MARKET_PRICE_OF_THE_CONTRACT_NOT_A_STAKE_RETURN
NOT STATED the other six
conflicts  []   sentences 4
```

Two rules were held to throughout:

* **A missing sentence, or a pattern that did not match, stays UNKNOWN.** It
  is never read as agreement. 107 rows are UNKNOWN for exactly that reason.
* **Nothing was selected because `PAY_LAST_FAIR_MARKET_PRICE` was absent.**
  The 43 INCOMPATIBLE rows are a conflict *stated by both sides* on one
  condition: the book returns the stake, the venue pays the last fair market
  price. That verdict stands.

---

## 3 · Executable net edge, for correctly matched fresh candidates

```
in_window 150 evaluated   0 admissible   150 refused   reconciles true
  1_PROBABILITY         51
  4_SETTLEMENT_SCOPE    59
  5_EXECUTION_ESTIMATE  40
reached_execution_estimate 0   walk_took_levels 0
negative_edge_WITH_a_walk     0    (measured against walked depth)
negative_edge_WITHOUT_a_walk  99   (top of book only)
never_priced_at_all          110   (refused before 5_EXECUTION_ESTIMATE)
POSITIVE_EDGE                  0   edge_not_computed 51
execution_mode CROSSING_THE_ASK_AT_THE_TAKER_FEE
excludes       ANY_PASSIVE_OR_MAKER_FILL_ASSUMPTION
```

**Observed universe 296 venue markets → compatible subset 0 → positive-edge
count 0.** `POSITIVE_EDGE` is now its own counter rather than something
inferred from two negative counts and a total; a derived zero is not a
measurement.

**Crossing, not passive.** Every edge here is `p − ask − fee` with the
**taker** fee (`fee_fn(..., maker=False)`), i.e. the cost of crossing the
ask. The existing maker/passive experiment has its own fill assumptions and
pays a different fee side; **none of its numbers are in this census**, and the
census now says so on the row rather than leaving a reader to assume.

One direction of inference is valid here and the other is not. For a long
entry the best ask is the cheapest price available, so a non-positive edge at
the top of the book rules out a positive edge at any size. The converse — a
positive top-of-book edge surviving to a claim about depth — is not used;
`walk_took_levels 0` means no book was walked and no capacity claim is
available.

Two measurement faults in the same cycle, recorded rather than smoothed over:
one `VENUE_BOOK_READ_FAILED: TimeoutError` on `aec-mlb-pit-det-2026-09-25`,
and one `NO_VENUE_NATIVE_CONTRACT_IN_PREMAP` on `mlb-chc-bos-2026-09-25`.
Neither is a price finding.

---

## 4 · No candidate qualified

```
-- 4 · what this lane HOLDS, by provenance --
  positions 0
-- 5 · reconciled P&L, per lane --
  autonomous_entry  realised -  fees -  unrealised -  total -  marked 0/unmarked 0
```

Nothing was created, nothing was forced, and no speculative model was added
to manufacture a candidate. The two acceptance positions keep their
synthetic, modelled, unfunded provenance and are not counted as autonomous
output.

---

## 5 · What would change the answer, cheapest first

Ordered by where the funnel actually stops, not by interest.

1. **A PMUS-native soccer money line, and a mapping that reaches it.** The
   binding soccer blocker is identity: 230 Soccer rows exist, and the ones
   that match carry global-CLOB slugs the venue will not accept. The concrete
   question is whether PMUS lists soccer money lines natively at all, and if
   so under which market type — a catalogue read, not a model. Until that is
   answered, soccer's payout compatibility is **unknown and unexaminable**,
   and capturing more rules changes nothing.
2. **The venue's soccer prose**, once a soccer candidate exists, so the
   comparison can run affirmatively on both sides. The bookmaker half is
   already captured.
3. **Baseball stays refused on settlement**, and that refusal is correct: the
   conflict on the price-settled branch is established, not missing scope.
   Separately, and independently, the price refuses too — `POSITIVE_EDGE 0`
   on 99 priced rows. Neither is fixed by the other.

---

## 6 · Houston, closed separately

This is about ONE named position and says nothing about the RN1 lane's
`with_observed_runtime_decision` count, which is a different set of four
legacy positions. It is not cited here.

Run 68 (run id 36188978059), subject
`...:ACCEPTANCE_SHADOW_MANAGER_DEMO_V1:-144218439`.

**The acquisition, once aimed at the subject's own fixture** — the condition
from its input-chain, the official date from the trailing date on its own
slug, taken from its trace:

```
condition       0x878147f1...            from the subject's input-chain
subject_slug    aec-mlb-hou-ath-2026-09-25   from the subject's trace
fixture_date    2026-09-25               from the slug's trailing date
schedule_http   200  bytes 21736
acquire_http    200   ok true   refusal -
  why             the phase, the format and the actual state are all declared
  game_pk         824947
  phase           REGULAR_SEASON (R)
  game_format     STANDARD_NINE_INNING  innings 9
  play_has_begun  -   state Scheduled
  start_evidence  ACTUAL_START_REPORTED_BY_A_PROGRESS_SOURCE
  binding         Houston Astros at Athletics on 2026-09-25
  source          MLB Stats API, schedule    retrieved_at 21:03:13Z
```

**The readback — the next two decisions, from the deployed loop:**

```
D0165  21:04:43Z  HOLD x10  HOLD_BY_FALLBACK_RULE
       compat INCOMPATIBLE   terms INCOMPATIBLE   book_held true
       unstated 6   mismatched ["POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED"]
       first_fail 5_EXIT_LADDER
       reason  EV_HOLD is EV_HOLD_DISQUALIFIED_SETTLEMENT_INCOMPATIBLE
D0166  21:06:29Z  HOLD x10  HOLD_BY_FALLBACK_RULE
       compat INCOMPATIBLE   terms INCOMPATIBLE   book_held true
       unstated 6   mismatched ["POSTPONED_OR_ABANDONED_AND_NEVER_COMPLETED"]
       ranked DIRECT_EXIT=-0.21   TAKE_COMPLEMENT=-0.21
```

**The question is answered: Houston's remaining refusal is an ESTABLISHED
CONFLICT, not missing scope.** Before the row existed the same position read
`compatibility UNKNOWN`, `terms UNKNOWN`, `book_terms_held -`, 7 conditions
unstated. With the row present: `book_held true`, 6 unstated, and the
mismatch named. The verdict moved UNKNOWN → INCOMPATIBLE, which is the
truthful outcome and is not a trade.

**What this does NOT establish, stated plainly.** The `fixture_metadata` row
was written by the **manual admin acquisition in this run at 21:03:13Z**, and
both decisions follow it. Whether the manager's *own* acquisition would have
produced it is still unshown: `fixt_acq` and `fixt_row` both printed `-`
because the manager records those on its pass output and they are not
persisted onto the decision's stored `input_chain`. That is the one remaining
gap on this question, and it is a persistence/reporting gap, not a verdict.

---

## 7 · What stays unchanged

Guard-rails unchanged throughout: funded trading and funded submission
disabled, the accounting-uncertain account paused, `PINNACLE_MAX_AGE_S = 30.0`
untouched, the INCOMPATIBLE verdict preserved, and nothing here submits an
order to a venue.
