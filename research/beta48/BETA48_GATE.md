# BETA48 — the gate

Evidence: run `35034361586`'s archived raw fills, reconstructed at one
common as-of `2026-09-15T23:09:17Z`, six accounts, every payload
delivered through git and verified against the SHA256 its own runner
wrote. All six reconcile to the estimator's own closed-lot totals at
$0.00 on **stake and P&L**.

---

## The short version

The preregistered band relationship **replicated perfectly in the pair
channel and did not survive to money.**

- MERGE channel, all six accounts: Spearman −0.94 to −1.00, cheap bands
  strongly positive, expensive bands negative, exactly one sign crossing
  each. Textbook.
- TOTAL economics, same accounts, same bands, same bucket: Spearman
  −0.26, −0.43, +0.03, −0.03, −0.77, −1.00. **Two of six** clear the
  preregistered −0.7. The low-positive/high-negative shape is gone in
  four of six. **14 of 36 bands flip sign** between the two views.

The gap is survivorship, and it was predictable from the same table that
produced the finding: 28%–81% of the cheapest opens never pair, the
highest residual rate of any band. Pairing succeeds least often exactly
where the merge edge looks best. kch123's 0.00–0.10 band is the case in
one line: **MERGE_ROI +59.49%, TOTAL_ROI −92.84%** — it merged $4,336 of
stake profitably while the legs that never found a complement settled
for −$142,150.

`ENGINE_A_BAND_CANDIDATE = DEAD.` Not threshold-mined, not rescued.

---

## A. Data

| account | role | reconciles | residual (stake / pnl) | rows kept | days since last fill |
|---|---|---|---|---|---|
| rn1 | DISCOVERY | YES | $0.00 / $0.00 | 4,692,866 | −0.01 |
| ferrarichampions2026 | VALIDATION | YES | $0.00 / $0.00 | 1,958,843 | −0.0 |
| homerunhazard | CONTROL | YES | $0.00 / $0.00 | 595,223 | −0.0 |
| kch123 | CONTROL | YES | $0.00 / $0.00 | 175,076 | 78.1 |
| w2c33 | CONTROL | YES | $0.00 / $0.00 | 623,639 | −0.0 |
| swisstony | FLAGGED_EXCLUDED | YES | $0.00 / $0.00 | 6,059,643 | 20.58 |

`EXACT_BUCKET_PNL_ATTRIBUTION = IDENTIFIED` for all six, against
`lot_s`/`lot_p` — a harder target than the merge subtotal the earlier
split was accepted on. swisstony reconciles but stays excluded from
clean ground truth on its own unresolved two-source discrepancy; that
is a different defect and this reconciliation does not clear it.

RN1's pull contains fills later than the common as-of, so LIFETIME means
"the whole pull", not "everything up to the as-of". Regime windows are
not reported: `LAST_7D` means "the seven days before this run", so it is
not a fixed quantity and two runs' regime numbers are not a replication
of each other. `CURRENT_REGIME_RESULT = NOT_REPORTED`,
`CURRENT_REGIME_AS_OF = n/a`.

---

## B. The preregistered test, run unmodified

The only change to `cross_account_discriminator.py` was its input
directory, via an env var whose default is the byte-identical
preregistered path. No statistic, threshold, criterion, band or account
list moved.

`PAIR_MECHANISM_CROSS_ACCOUNT_REPLICATION = REPLICATED (MERGE CHANNEL ONLY)`

| account | rho MERGE | signs MERGE | rho TOTAL | signs TOTAL |
|---|---|---|---|---|
| rn1 | −1.0000 | `++++--` | −1.0000 | `++++++` |
| ferrarichampions2026 | −1.0000 | `++++--` | −0.2571 | `+-++-+` |
| swisstony | −1.0000 | `+++---` | −0.7714 | `++++++` |
| homerunhazard | −0.9429 | `+++---` | −0.4286 | `++++++` |
| kch123 | −0.9429 | `++----` | +0.0286 | `-++-++` |
| w2c33 | −0.9429 | `++----` | −0.0286 | `+--+-+` |

- `LOW_POSITIVE_HIGH_NEGATIVE_SIGN_TEST` — MERGE: **TRUE, 6/6**.
  TOTAL: **FALSE**.
- `COMMON_BAND_MIX_REWEIGHTING` — forcing all six to one stake mix moves
  every account's ROI by under a point and flips nobody:
  ferrari +7.06%→+6.23%, rn1 +4.86%→+4.55%, swisstony +0.10%→+0.33%,
  hrh −1.04%→−0.97%, kch123 −6.61%→−6.44%, w2c33 −8.04%→−7.58%.
- `ACCOUNT_MIX_EFFECT = REFUTED.` The 3/3 account-level sign split is
  **not** explained by where each account's merges sit. This kills the
  reading I proposed last session from kch123's shape alone — *"negative
  overall only because 83% of its merges sit in the mid bands where it
  loses"*. It is recorded as refuted, not dropped.

---

## C. The economic question

**Does opening in a band make money?** Not: does the pair channel make
money given that a pair happened.

`MERGE_ROI` vs `TOTAL_ROI`, LIFETIME, both reconciling exactly:

| account | band | MERGE_ROI | TOTAL_ROI | flip |
|---|---|---|---|---|
| kch123 | 0.00–0.10 | +59.49% | **−92.84%** | YES |
| w2c33 | 0.10–0.30 | +13.83% | −4.94% | YES |
| ferrari | 0.10–0.30 | +32.34% | −2.00% | YES |
| hrh | 0.50–0.70 | −3.03% | +1.51% | YES |
| w2c33 | 0.50–0.70 | −8.64% | +5.90% | YES |
| kch123 | 0.30–0.50 | −8.26% | +18.31% | YES |
| … | | | | 14 of 36 |

Unanimity bar, fixed in `band_total_economics.py` **before** the
three-channel data existed — a band is structurally profitable only if
`TOTAL_ROI > 0` in every reconciling account:

| band | positive | worst |
|---|---|---|
| 0.00–0.10 | 5/6 | −92.84% |
| 0.10–0.30 | 4/6 | −4.94% |
| 0.30–0.50 | 5/6 | −2.00% |
| 0.50–0.70 | 5/6 | −2.67% |
| 0.70–0.90 | 4/6 | −0.48% |
| **0.90–1.01** | **6/6** | **+0.69%** |

### The one band that clears the bar is not a candidate

`0.90-1.01` passes the letter of the rule and must not be promoted, for
four separate reasons, each sufficient on its own:

1. **It is not a pair mechanism.** Its MERGE channel is *negative in all
   six accounts*; the positive total is entirely SETTLED. The behaviour
   it describes is "buy a heavy favourite and hold to settlement" — a
   directional bet, not Engine A, and not what BETTOR was building.
2. **It is post-hoc and in the opposite direction.** The preregistered
   hypothesis was that LOW bands pay. That is refuted. Selecting the one
   band of six that survives, after the registered direction failed, is
   a six-way search. Under a naive null of 50/50 signs per account,
   P(a given band unanimous over 6) = 0.0156, and
   P(at least one of six) = **9.1%**.
3. **The margins are thin**: +0.69% to +3.68% ROI, against a venue with
   fees and a spread this measurement does not charge.
4. **Its stake share is wildly unstable** across accounts — 0.82%
   (kch123) to 20.81% (w2c33) of lot stake — so the accounts are not
   doing the same thing there.

It is recorded as `POST_HOC_OBSERVATION`, needs its own out-of-sample
test before it is anything, and **is not** an Engine A candidate.

### The strongest objection to this kill, and its answer

`TOTAL_ROI` charges the reference accounts' *holding* behaviour against
the pair mechanism. A BETTOR engine might instead open a leg and, if no
complement arrives, **sell out** rather than hold to settlement — and
would then not eat the settled losses.

That variant is not measurable from this evidence, and the reason is
itself a finding: **these accounts essentially never sell.** The SELL
channel is 0.00%–0.54% of lot stake across all six (homerunhazard:
exactly zero). There is no sample of exits to price one from.

`UNPAIRED_LEG_EXIT_COST = NOT_IDENTIFIED.` And it may not be assumed
cheap: a cheap leg goes unpaired precisely because no counterparty wants
that side, which is the same condition that makes it expensive to exit.

---

## D. Data mining controls

| label | account | why |
|---|---|---|
| DISCOVERY | rn1 | the band relationship was found here; it cannot also confirm it |
| VALIDATION | ferrarichampions2026 | independent, pair-positive |
| CONTROL | homerunhazard, kch123, w2c33 | pair-negative |
| FLAGGED, excluded from clean ground truth | swisstony | unresolved two-source discrepancy |

Controls available for confounding, unchanged and not invented:
sport/question PARTIAL (top-25 only), first-leg size AVAILABLE (no P&L),
time period AVAILABLE (no P&L); league, market type, first-leg side,
time-to-event and liquidity/book state all `NOT_IDENTIFIED`.

One threshold search was run in this whole exercise — the six-band
unanimity sweep in section C — and its multiple-comparisons cost is
stated above rather than hidden. No sub-bands, horizons or account
subsets were searched after the preregistered test failed.

---

## E. Translating to BETTOR

`REFERENCE_ACCOUNT_MATCHED_PNL != BETTOR_EXPECTED_PAIR_PNL`, and the
three registers stay apart:

- `REFERENCE_ACCOUNT_COMPLETION` = **MEASURED** — what these accounts
  achieved with their own orders, queue position and size.
- `BETTOR_PASSIVE_FILL_PROBABILITY` = **NOT_IDENTIFIED.** BLOCK_4
  stands: a 22,297-share displayed bid queue against 180 shares traded
  in 16 minutes, **zero touches**.
- `BETTOR_EXPECTED_PAIR_PNL` = **NOT_IDENTIFIED.**

`STRUCTURAL_SELECTION_EDGE` and `EXECUTION_REALIZABILITY` are separate
questions and this sprint answers only the first — in the negative. That
ordering matters: there is no longer an execution question to ask about
the band rule, because there is no structural edge behind it to execute.

---

## F. THE GATE

| field | value |
|---|---|
| `BETA48_DATA_GATE` | **PASS** — six accounts, one as-of, exact attribution on stake and P&L, transport verified by hash |
| `BEST_CANDIDATE` | **NONE** |
| `STRUCTURAL_EDGE_VALIDATED` | **NO** — replicated in the MERGE channel, refuted on TOTAL economics |
| `BETTOR_EXECUTION_VALIDATED` | **NO** — never tested, and now moot for this candidate |
| `READY_FOR_SHADOW` | **NO** — there is nothing to shadow |
| `READY_FOR_MICRO_LIVE` | **NO** |
| `BIGGEST_REMAINING_UNKNOWN` | `UNPAIRED_LEG_EXIT_COST` — what a leg that finds no complement can actually be sold for. It decides whether *any* pair mechanism is viable, and no reference account supplies it, because none of them sell |
| `SHORTEST_EXPERIMENT_TO_RESOLVE_IT` | Passive-rest exit probe: rest a minimum-size SELL on a leg at a ladder of discounts to mid and record touch, fill and time-to-fill. Resolves `UNPAIRED_LEG_EXIT_COST` and `BETTOR_PASSIVE_FILL_PROBABILITY` — the two open unknowns — from the same order flow. Real money, therefore **requires explicit approval** and is not started here |
| `ESTIMATED_TIME_TO_RESOLVE` | ~2 h to build and shadow; 3–5 trading days of live probes for a usable fill sample |

**ACTION, per the standing rule.** No Engine A candidate survives, so
**Engine A closes for this sprint.** No re-dispatch, no Track A change,
no production order, no capital. `mirror_live = false`.

---

## G. Two things that must not be glossed

### RN1's two sources still disagree, and are neither averaged nor picked

RN1 has two independent measurements of the same LIFETIME band split.
Both are reported; neither is selected.

| band | retained snapshot | run `35034361586` | diff (pp) |
|---|---|---|---|
| 0.00–0.10 | +87.834% | +66.639% | **−21.20** |
| 0.10–0.30 | +22.588% | +23.020% | +0.43 |
| 0.30–0.50 | +5.230% | +8.567% | +3.34 |
| 0.50–0.70 | −1.410% | +1.353% | +2.76 |
| 0.70–0.90 | −4.745% | −1.018% | +3.73 |
| 0.90–1.01 | −3.676% | −1.119% | +2.56 |

They agree on **shape** — both monotone decreasing, both crossing zero
between 0.50–0.70 and 0.70–0.90 — and disagree on **level** in every
band, by 2.1 to 21.2 pp. `RN1_TWO_SOURCE_COVERAGE_DISCREPANCY` stays
open and unexplained.

It does not move this gate: RN1 is DISCOVERY, the kill came from TOTAL
economics across all six accounts, and the level disagreement is inside
the MERGE channel that the TOTAL result already supersedes. But it is a
standing reason not to quote any single RN1 band number as a quantity.

### A provenance rule I did not follow

The standing rule is that evidence commits are separate from analysis
commits. In this sprint `ee7329c` and `eb7e383` each carry **both** the
`blobs_v3` payloads and the analysis that reads them. The evidence is
independently verifiable regardless — each payload is hash-matched to
the `.sha256` its own runner wrote, on its own `beta48-evidence/<run>/`
branch, and those branches are untouched by any analysis commit — so
nothing here is unprovable. But the rule exists so that evidence cannot
be quietly edited to fit an analysis in the same change, and I did not
honour it. Recorded rather than left for someone to notice.

---

## H. What this sprint established that keeps its value

1. The MERGE vs SETTLED decomposition, and now its per-band form:
   `pnl_by_open_band` books every closed lot the estimator books to the
   band the leg opened in, reconciling to `lot_s`/`lot_p` at $0.00.
2. A band's MERGE economics are **not** its TOTAL economics — 14 of 36
   bands flip sign. This generalises the standing rule about headline
   `edge_roi` and should be applied to every future bucketed result.
3. `ACCOUNT_MIX_EFFECT = REFUTED`, and `PAIR_BASIS_DISCRIMINATOR =
   NOT_SUPPORTED` before it. Two hypotheses closed on evidence.
4. The return channel: hand-transcribing evidence out of a log failed on
   2 of 4 chunks even with per-chunk hashes. Evidence now travels by git
   and nothing is retyped.
5. These accounts do not sell (0.00%–0.54% of lot stake). Any strategy
   that depends on exiting a leg is unsupported by this evidence base.

mirror_live = false. Read only. No order, no capital, no credential, no
production write. Track A untouched. Phase X untouched.
