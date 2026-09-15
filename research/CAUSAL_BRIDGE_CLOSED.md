# The BETTOR causal bridge — CLOSED, owner order 2026-09-11

Run 78 (`research/rn1_causal_ledger.sql`, job 103444909974, `psql exit=0`, 15
statements, sha256 `585432516935c0c18f0993275803495c4ca6a83ff004bad80795e800a405a178`)
is the **authoritative** causal-bridge result. The owner has locked the
conclusions below. They are not to be re-opened, re-worded, or softened by a
later summary; a future result that contradicts one of them is a retraction and
goes in `RETRACTIONS.md` under its own heading, never a silent overwrite.

---

## The locked conclusions

1. **The old mirror target-collapse defect is PROVEN IN COMMANDED STATE.**
   09-02 → 09-05, every negative-net tick recorded `target 0` (3,652 / 13,011 /
   13,312 / 1,890 ticks), none short.

2. **It is NOT proven that this defect caused the disproportionate historical
   dollar losses.**

3. **The causal bridge does NOT close using retained historical evidence:**

       RN1 complement / position transition
         -> BETTOR harmful command
           -> attributable BETTOR execution
             -> realized dollar damage

   The first two arrows are evidenced. The third is not.

4. **Tier 1 lineage is NOT IDENTIFIABLE FROM RETAINED DATA.**
   `mirror_orders.trigger_trade_id`, `his_fill_ts` and `first_fill_at` each have
   **zero production write sites and zero populated rows** on 11,183 orders.
   A tier built on them reports NOT IDENTIFIABLE — never zero. A filter that
   cannot fire has measured nothing.

5. **`his_fill_id` remains INELIGIBLE for causal attribution.**
   Statement 0b: 7,887 populated of 11,183 (70.527%), nothing before
   2026-09-08 15:54; 4,582 distinct; nine digits, all numeric; joins to
   `trades.id` at 100.000% of populated rows. It fails two of the five
   questions — the matched trades span **3 distinct ingestion sources**
   (stability not established), and **1,387 ids are carried by more than one
   order, max 136** (one source fill fans out to many commands, so it cannot
   single out which command an execution belongs to). Populated is not
   verified; verified is not eligible.

6. **Do not manufacture a dollar damage estimate or loss ratio from the old
   mirror defect.** Statement 10 refuses the ratio and the refusal stands: five
   candidate bottom lines spanning −$464.61 to −$24,089.44, structurally
   disagreeing (realized-only ≈ −$4.9k vs settlement-inclusive ≈ −$21–24k).

7. **Preserve this wording exactly:**

   > No liquidation attributable through the populated retained evidence paths
   > was observed for the P1-consistent commands across the tested windows.

8. **That sentence is NOT proof that no liquidation occurred.** It is a
   statement about the evidence we retained, not about the world.

9. **Run 77 attribution is discarded.** Its Tier 1A zero was produced by a tier
   later shown unavailable and must not be quoted, including as a bound.

10. **Run 78 is the authoritative causal-bridge result.**

11. **`mirror_live=false`. Trading stays paused.** Nothing in this file, and
    nothing in the work that produced it, changes live trading behaviour.

---

## The supporting numbers, for the record

Statement 7, 521 harmful commands, **identical at all five windows**
(900 s / 1 h / 6 h / 24 h / 30 d) — widening the window changed nothing:

| resolution | commands | share |
|---|---|---|
| `0 NO_EXECUTION` | 146 | 28.02% |
| `TIER_1A_DIRECT_UNSUPERSEDED` (primary) | **0** | NOT IDENTIFIABLE |
| `TIER_1B_DIRECT_SUPERSEDED` | **0** | NOT IDENTIFIABLE |
| `TIER_2S_TARGET_LINEAGE_SUPERSEDED` | 1 | 0.19% |
| `TIER_3_TARGET_STATE` (secondary) | 13 | 2.50% |
| `TIER_3S_TARGET_STATE_SUPERSEDED` | 8 | 1.54% |
| `TIER_4_TEMPORAL_SENSITIVITY_ONLY` (never primary) | 353 | 67.75% |

`tier_1a_shares` = 0 and `tier_1a_notional` = 0.00 at every window, reported as
NOT IDENTIFIABLE. 289 of the 353 temporal-only commands carry an intervening
supersession.

Statement 8, the 79 `CONSISTENT_WITH_P1_LONG_ONLY` commands, identical at every
window: 78 `NO_EXECUTION` (58 conditions, 9,541 commanded shares), 1
`TIER_3S_TARGET_STATE_SUPERSEDED` (413 commanded shares), tier-1A progress 0.

Statement 9 (counterfactual by window x horizon): **0 rows** — there is no
attributed execution to run a counterfactual against.

Statement 1's classes 5 (`IDENTIFIED_REALIZED_DAMAGE`, 38 events) and 6
(`PARTIALLY_IDENTIFIED_DAMAGE`, 323 events) are **temporal association only**.
Statement 7 is the same population resolved by evidence tier, and none of it
clears secondary. Quoting class 5 or 6 as identified damage contradicts
conclusion 2.

### Gate 3 is NOT APPLICABLE, not passing

Statement 12: gate1 521/0/0 PASS · gate2 375 executed / 0 PASS · **gate3 witness
0 / violations 0** · gate4 306/0 PASS · gate5 353/0 PASS.

Gate 3 checks that attributed quantity never exceeds commanded quantity on tier
1A rows. There are no tier 1A rows. By the standing rule — *a zero violation
count beside a zero witness count reads as NOT TESTED, never as PASS* — gate 3
tested nothing. Four gates passed, not five.

---

## What would close the bridge

Not more queries against what we kept. A going-forward production write of
source-fill -> order lineage, with a single-writer namespace and one-to-one
cardinality, so that a future command's execution can be attributed rather than
associated. That is a production change and trading is paused, so it is the
owner's to schedule.

`mirror_live=false`.
