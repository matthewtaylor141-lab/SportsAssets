# BETA48 — CHECKPOINT_00_STATE

```
BETA48_STARTED             = 2026-09-15T21:30Z
HEAD                       = 8aa6fa60b5b47a66ced08352202573e3a50b88db
BRANCH                     = claude/session-njaewf
FROZEN_RUNS                = 34971707012 (phasex, cancelled on 60-min timeout mid-capture, evidence sealed 8f925ae)
                             34972147750 (phasex kalshi-only, success, GATE D, evidence f8d095c)
                             35006965533 (phasex live/live, CANCELLED 21:00:58Z, ZERO jobs, never ran)
ACTIVE_RUNS                = 34995913495 Track A #10 in_progress since 16:35:02Z (timeout 340 min -> ~22:15Z)
                             35023098015 Track A #11 pending since 21:00:56Z
RAW_RN1                    = PRESENT   214,609 fills, 0 duplicates, BUY-only, 2026-08-06 .. 2026-09-11
RAW_FERRARI                = ABSENT    wallet 0xfe787d2da716d60e8acff57fb87eb13cd4d10319; not on disk; NOT in the extraction workflow matrix
RAW_HRH                    = ABSENT    wallet 0x5268527977f700f9bf9b6d5cd843859e4e70135d; not on disk
RAW_SWISSTONY              = ABSENT    wallet 0x204f72f35326db932158cba6adff0b9a1da95e14; not on disk
PMUS_FORWARD_DATA          = PRESENT but DISJOINT: two-sided books on 30 slugs, settled outcomes on 10,257 slugs, INTERSECTION = 0 (DATA_GATE = DATA-B)
KALSHI_DATA                = CODE ONLY. No Kalshi contract record has ever been persisted to disk.
PAIR_RECONSTRUCTION_READY  = YES for RN1 (run and sealed). NO for Ferrari / HRH / swisstony — data unreachable.
DIRECTIONAL_RESEARCH_READY = NO. No PMUS dataset carries both a two-sided book and a settled outcome.
                             Track P (P-C) already invalidated ask-side calibration on the RN1 population.
SHADOW_INFRA_READY         = PARTIAL — inventory in flight; mirror shadow paths exist (E38/E24/E25) but are whale-triggered, not native.
PHASE_X_STATUS             = NO RESULT. Every economic field NOT_REACHED. Two independent defects:
                             (1) scheduling — a pending run dies at Track A's next cron;
                             (2) design — EQUIVALENCE_VERIFIED is unreachable (5 dimensions structurally UNRESOLVED).
FIRST_BLOCKER              = Three of the four whale raw datasets are unreachable from this container.
                             All six data hosts return 000. DB credentials are forbidden. The Actions
                             extraction workflow reaches the data but omits Ferrari.
NEXT_ACTION                = Deepen the RN1 reconstruction into the section-5 horizon x ceiling study
                             (the one whale we can actually measure), and put the Ferrari/HRH/swisstony
                             extraction question to Matt as a decision, since the only routes are an
                             Actions run or a DB read.
```

## WHAT WAS PROVEN

1. **RN1's raw fills are complete enough to reconstruct his pair mechanism
   offline.** 214,609 rows, 214,609 distinct `trade_id` — zero
   duplication, so no D1-class double-count. BUY-only, which is the
   signature of complement-exit: he never sells, he buys the other leg.

2. **RN1's matched-pair economics, measured, not asserted** — reusing
   `merge_pnl.replay` unmodified, 196,619 fills, 126,242 entries, 78,500
   merges, $32.28M entry notional:

   | Window | merge P&L | edge_roi | 95% CI (clustered on his GAME) |
   |---|---|---|---|
   | Sample (37d) | +$223,094 | +2.084% | **[−0.195%, +4.362%]** |
   | Last 30d | +$210,531 | +2.150% | [−0.169%, +4.469%] |
   | Last 14d | +$82,217 | +1.205% | [−1.494%, +3.903%] |
   | Last 7d | +$36,011 | **+0.262%** | [−3.420%, +3.944%] |

3. **Whale identity resolved.** ferrariChampions2026 is
   `0xfe787d2da716d60e8acff57fb87eb13cd4d10319`, and it is a different
   account from `0x2c33…`, which the diagnostic treats separately.

4. **Phase X run 35006965533 died by concurrency supersession**, 2 s
   after Track A #11 queued, with zero jobs. Mechanism confirmed, not
   inferred.

## WHAT WAS DISPROVEN

1. **"RN1 shows strong positive matched-pair economics" is NOT CONFIRMED
   on the raw data.** Every window's 95% interval contains zero, and the
   point estimate decays monotonically toward zero as the window narrows
   (+2.08% → +2.15% → +1.21% → +0.26%). The directive listed this as a
   report-derived hypothesis to verify; verification did not carry it.
   The production estimator's own verdict string, for EVERY window, is
   `NOT DEMONSTRATED`. It is not refuted either — the intervals are wide.
   `RN1_PAIR_EDGE = NOT_IDENTIFIED`.

2. **"Merging is how you capture the edge" does not hold uniformly.**
   The hold-vs-merge counterfactual on the graded subset flips sign:
   holding would have beaten merging by **+$31,797** over the sample and
   +$30,267 over 30 days, but merging beat holding by **−$82,769** over
   14 days and −$80,382 over 7. Merge-as-exit is regime-dependent, which
   is precisely the Engine C question and not a settled answer.

## WHAT REMAINS NOT_IDENTIFIED

- Ferrari, HomeRunHazard, swisstony pair AND directional economics — no data here.
- The section-5 four-account contrast (why RN1/Ferrari complete cheap pairs and HRH/swisstony expensive ones) — 3 of 4 accounts missing.
- `BETTOR_PASSIVE_FILL_PROBABILITY` — no BETTOR-specific fill evidence exists. BLOCK_4 measured **zero touches** against a 22,297-share displayed queue in 16 minutes.
- Kalshi fee schedule — our own constant only, never venue-verified. Unknown fee ≠ zero.
- Whether RN1's Polymarket-CLOB ROI transfers to PMUS economics — different venue, different fee schedule, not re-costed.

## CURRENT BEST STRATEGY

None yet promoted. The only measured candidate mechanism is
complement-pair construction, and on the one account we can measure it
is not statistically distinguishable from zero in the current regime.

## CURRENT EXPECTED EDGE

`NOT_IDENTIFIED`. Reporting a number here would mean reporting RN1's
point estimate as if the interval did not cross zero, and as if his fills
were our fills. Neither is true.

## CURRENT EXECUTION EVIDENCE

`NONE for any native strategy.` The only BETTOR-specific passive
execution measurement in the repo is BLOCK_4: zero touches. Micro-live
gate condition 12 cannot pass on today's evidence.

## CURRENT BLOCKER

Three of four whale datasets are unreachable from this container. Both
routes to them — the production database and the Polymarket data-api —
are closed here (all six hosts return `000`), and the Actions workflow
that can reach the data does not include Ferrari.

## NEXT SIX HOURS

1. Extend the RN1 reconstruction to the directive's §5 grid: complement
   completion at 5 s … settlement × pair ceilings .90 … 1.01+, broken
   down by sport, market type, first-leg price, first-side identity,
   fill size and week. This is the one account measurable offline and it
   is where the mechanism question can actually be answered.
2. Re-cost RN1's completed pairs under the **verified PMUS** fee schedule
   (Θ_taker +0.06, Θ_maker −0.0125, banker's rounding per fill) to see
   whether the mechanism survives BETTOR's actual venue economics.
3. Separate `REFERENCE_ACCOUNT_COMPLETION` from
   `BETTOR_PASSIVE_FILL_PROBABILITY` in code, so the two can never be
   conflated downstream.
4. Put the Ferrari/HRH/swisstony extraction decision to Matt — it needs
   his authorization, not mine.
