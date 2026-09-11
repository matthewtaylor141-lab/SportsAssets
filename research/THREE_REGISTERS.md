# The three registers — owner instruction, 2026-09-11

Three different economic questions have been blurred together in this work, and
the owner has ruled that they stay separate from here forward. A result in one
register is **never** a conclusion in another. This file exists so the rule
survives the next person, the next query and the next summary.

---

## 1. RN1 STRATEGY ECONOMICS

What RN1 itself is doing, and where its mathematical profit comes from.

Measured on **his** fills, at **his** prices, on **his** venue, with no
reference to whether anyone could follow him.

Examples of statements that belong here:

- the condition-level matched-pair edge, `1 - (vwap_yes + vwap_no)`;
- Check A's cohort returns (COVERED_ONLY +3.644%, MISSING_ONLY +1.438%,
  MIXED +0.986%);
- the entry-price-band profile (+5.98% under 10c … −3.07% at ≥90c) —
  **a lead, not a conclusion**, and explicitly not yet corrected for selection;
- pair completion rates, leg timing, unmatched inventory and its disposition.

## 2. RN1-VENUE REPLICATION ECONOMICS

What it would cost to reproduce RN1's actions **on RN1's own venue and book**,
under the retained probe data.

This register is bounded by what `copy_probes.depth` actually is: the top eight
ask levels from `GET /book` against `clob_api_base` (Polymarket), captured at
`probe_at`. Two exclusions are structural and must be restated every time a
number from this register is quoted:

- **the venue is his, not ours.** Polymarket, not Polymarket US.
- **latency is excluded.** `reaction_s = probe_at − fill_ts` has median
  **−0.73 s**, so the ladder is his book at his own print. Our measured
  whale-fill-to-our-fill latency is **86.8 s median / 382.8 s p90** (E6/E9),
  and none of that movement is in these figures.

So the headline of this register —

> **3.957%** of deployed in PRICE DRAG on the fully-measurable side-forced BUY
> cohort, 27,071 events, $5,399,109

— is **the cost of taking his own book at his own moment with a perfect
zero-latency fill**. It is a LOWER BOUND on replication cost and a generous
one. It is not our execution cost, and it is not a statement about whether his
strategy works.

**THE 6.094% I FIRST REPORTED IS WITHDRAWN AS A SINGLE FIGURE.** It was
3.957% drag + 2.137% fee, and that fee is the `polymarket-us` schedule
(`0.06 * shares * price * (1 - price)`, proof2.py:97/107/111) applied to a
replication priced off **Polymarket's** book. That is a fee schedule carried
across venues by analogy, which the owner has ruled out (2026-09-11), and I
summed it into the headline before the rule existed. The two components stay
separate from here:

| component | value | venue it belongs to | status |
|---|---|---|---|
| price drag | 3.957% of deployed | Polymarket (his) | measured from his own ladder |
| fee | 2.137% of deployed | **Polymarket US (ours)** | schedule estimate, CROSS-VENUE BY ANALOGY — not additive to the drag |

The RN1-side fee treatment that would make a combined figure legitimate is not
in retained data. Until it is, quote the drag, and quote the fee only with its
venue named and never added to a his-venue cost.

Never relabel a number from this register as "BETTOR execution drag".

## 3. OUR REAL EXECUTION ECONOMICS

What BETTOR could actually execute: our venue (Polymarket US), our latency, the
liquidity actually available to us, our fees, fill probability, partial fills,
and every other implementation difference.

**This register is currently the least measured of the three.** What exists:

- `TRUEEDGE rn1`: cf_total +$41,801, paper_actual −$309, lat_cost $41,430;
  `TRUEEDGE-FAST rn1`: cf_total $40,740, paper_actual $8,185, lat_cost $32,475.
- `FVM rn1`: filled 47 roi −0.068 vs missed 21 roi +0.427 — adverse selection
  in our own fills.
- `PROOF`: n=1,509, staked $144,020.55, pnl −$1,390.74, roi −0.97%,
  ci95 [−7.93%, +6.00%] — **INSUFFICIENT**, 14,369 more settled copies needed.

What does **not** exist: a per-fill Polymarket US ask ladder. One grep over
every migration finds exactly one stored ladder — `copy_probes.depth`, which is
register 2's venue. `mirror_shadow` keeps US **top-of-book only** (bid/ask/mark
from `_paced_bbo`), no depth, per tick rather than per fill. PMUS-venue depth
economics are **not historically measurable** and cannot be reconstructed from
retained data.

---

## Locked terminology — SELL side (owner, 2026-09-11)

    gross_parity_long_reference = 1 - rn1_complement_fill_px

It is the **gross parity-equivalent long-side reference implied by RN1's
complement fill**. It is NOT "RN1's long quote" and NOT an exact
contemporaneous "value of the position". Every SELL-required event uses it,
because a SELL-required transition has signed_dn < 0 by construction, which
means the fill was on the complement: no event in that cohort has a direct
same-token reference.

Buckets A / B / C therefore mean exactly one thing:

> **the PMUS best bid was above / at / below the gross parity-equivalent
> reference.**

They do **not** mean profitable / breakeven / unprofitable execution. The
gross comparison stays as run 59 reports it.

A net economic SELL threshold, if one is ever built, is SEPARATE FIELDS with
explicit provenance, never a re-labelling of the gross buckets:

    gross_parity_reference
    pmus_fee_schedule_estimate
    net_pmus_sell_proceeds_estimate
    <RN1-side fee treatment, only as actually supported by retained data>

**Fee schedules are never combined across Polymarket and Polymarket US by
analogy.** This is what invalidated the 6.094% BUY headline above.

### The hard identification boundary — preserve verbatim

    retained PMUS bid present:        20,054 / 32,847 SELL events = 61.05%
    no usable contemporaneous bid:    12,793 / 32,847 SELL events = 38.95%

Nothing about the 38.95% is inferred from the 61.05%.

---

## The rule

- A register-2 result may motivate a question in register 1 or 3. It may never
  answer one.
- Any figure quoted in a summary names its register.
- "Copying RN1's fills is not viable" is a register-2-vs-1 statement. It does
  **not** imply his strategy is unprofitable (register 1 stands on its own
  evidence) and it does **not** tell us what we could execute (register 3).
- No strategy recommendation and no optimisation until the mechanism and the
  selection effects are identified. Owner instruction, 2026-09-11.

`mirror_live=false` throughout this work. Nothing in any register has changed
live trading behaviour.
