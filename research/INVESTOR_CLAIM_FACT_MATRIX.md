# INVESTOR-CLAIM FACT MATRIX — BETTOR / RN1 MIRROR

**Factual record only. This is not investor-facing language and must not be
sent to anyone outside.** Its purpose is to establish what is actually
evidenced before any communication is drafted.

Compiled 2026-09-14 from repository evidence. Every figure below is sourced to
a committed artifact and dated by that artifact, not by recollection.

---

## 0. THE LIMIT OF THIS DOCUMENT — READ FIRST

**I have never seen an investor communication.** No deck, email, memo, call
note, data-room document or term sheet exists in this repository, and none has
been supplied to this session. Therefore, for every row below:

```
DATE/DATE RANGE COMMUNICATED   NOT_IDENTIFIED
SOURCE OF CLAIM                NOT_IDENTIFIED
```

What I *can* establish is the second half of each row: what the evidence
showed at a given date, what it shows now, and whether a proposition is
VERIFIED, CONTRADICTED or NOT_IDENTIFIED. The claim text below is taken from
the owner's own characterisation of the investor understanding
("BETTOR was successfully mirroring RN1" and "the mirrored system was
profitable"), decomposed into separately testable propositions.

**To complete this matrix, the actual communications are required.** Until they
are supplied, no row can state what was said, when, or by whom — and this
document deliberately does not guess.

A second limit, stated plainly: this record is built from what the system
**retained**. Several questions below are unanswerable not because the answer is
bad but because the data to answer them was never written. That is itself a
finding, and it is reported as `NOT_IDENTIFIED`, never as zero.

---

## A. WHAT DID THE OLD BETTOR SYSTEM ACTUALLY DO?

Evidenced, from the code and the operational record:

1. **It ingested RN1's activity** from two paths — an on-chain listener and a
   REST poller — and maintained a per-market reading of his net position.
2. **It attempted to map his markets to Polymarket US listings.** This mapping
   is the binding constraint on everything downstream (§B).
3. **It generated a target state per market** and issued real orders to PMUS
   toward that target, with caps, bands and refusal gates.
4. **It placed real orders.** `mirror_orders` carries **11,183 rows**
   (`research/CAUSAL_BRIDGE_CLOSED.md`, run 78, 2026-09-11). Orders were
   submitted, acknowledged, filled, cancelled and refused; incidents, freezes
   and venue outages are logged throughout the operational record.
5. **It was paused.** `mirror_live=false`, owner order, mirror switched to
   exits-only; it has not been re-armed.

So: **the system ran and traded.** That much is not in question. What is in
question is whether what it did constitutes "mirroring RN1", and whether the
result was profitable. Those are §B and §C.

---

## B. WHAT EVIDENCE EXISTED FOR "IT MIRRORED RN1"?

### B.1 Coverage — measured, and low

`docs/mirror-coverage.md` (first committed 2026-09-04, last 2026-09-10), from
probe logs:

```
P0 24h window: markets read / mapped     309 / 56  = 18.1% mapped
                                                     81.9% UNMAPPED
same reading across runs                 19.4%, 15.5%, 16.8%, 17.7%, 18.1%
copy-lane refusals, 30 d                 45,715 refused
  of which attributed to `unmapped`      65.15%
RN1 new positions/day (median)           1,263.5
```

Read plainly: across the observed window, **roughly four of every five RN1
markets the shadow looked at could not be mapped at all**, and two thirds of
all copy-lane refusals were the mapper failing. Whole sports were missing —
NFL moneylines mapped to nothing until a dedicated binding lane was built
(2026-09-11), and college football full-game contracts were found not to exist
in the expected form at all.

### B.2 Attribution — never established

`research/CAUSAL_BRIDGE_CLOSED.md`, run 78, owner-locked 2026-09-11:

> **Tier 1 lineage is NOT IDENTIFIABLE FROM RETAINED DATA.**
> `mirror_orders.trigger_trade_id`, `his_fill_ts` and `first_fill_at` each have
> **zero production write sites and zero populated rows** on 11,183 orders.
> A tier built on them reports NOT IDENTIFIABLE — never zero.

And:

> **The causal bridge does NOT close using retained historical evidence:**
> RN1 complement/position transition → BETTOR harmful command → attributable
> BETTOR execution → realized dollar damage. The first two arrows are
> evidenced. The third is not.

`his_fill_id` was examined as a substitute and ruled **INELIGIBLE for causal
attribution**.

This means: **for none of the 11,183 orders can the system prove which RN1 fill
caused it.** Mirroring was the *intent* and the commanded behaviour; per-order
causal attribution to RN1 was never recorded.

### B.3 Latency — measured, and large

`research/THREE_REGISTERS.md` (2026-09-11), citing E6/E9:

```
whale-fill-to-our-fill latency    86.8 s median / 382.8 s p90
```

A copy arriving a median 87 seconds after the trade it copies is not the same
trade at the same price.

### B.4 A known defect in commanded state

`research/CAUSAL_BRIDGE_CLOSED.md`, locked conclusion 1:

> **The old mirror target-collapse defect is PROVEN IN COMMANDED STATE.**
> 09-02 → 09-05, every negative-net tick recorded `target 0`
> (3,652 / 13,011 / 13,312 / 1,890 ticks), none short.

Conclusion 2 is equally binding: **it is NOT proven that this defect caused the
disproportionate historical dollar losses.** Both halves stand.

---

## C. WHAT EVIDENCE EXISTED FOR "IT WAS PROFITABLE"?

### C.1 The measured profitability figures are RN1's, not BETTOR's

This is the single most important distinction in this document.

`research/THE_TENSION.md` and `research/RETRACTIONS.md` (2026-09-11) carry
figures such as:

```
ALL ELIGIBLE matched gross ROI     1.383%
BRIDGED                            0.804%
UNBRIDGED                          1.556%
matched cost                       $42,578,503
matched gross P&L                  $588,777
```

These are **RN1's own economics**, measured on **his** fills at **his** prices
on **his** venue (Polymarket, not Polymarket US). `research/THREE_REGISTERS.md`
was written on 2026-09-11 specifically to stop this blurring, and its rule is
binding:

> Three different economic questions have been blurred together in this work...
> A result in one register is **never** a conclusion in another.

```
REGISTER 1  RN1 STRATEGY ECONOMICS          measured
REGISTER 2  RN1-VENUE REPLICATION ECONOMICS bounded, his venue, latency EXCLUDED
REGISTER 3  BETTOR EXECUTION ECONOMICS      the one that would be "our P&L"
```

**No verified register-3 realized net P&L for the mirror exists in this
repository.** If an investor communication used a figure like 1.383% as
BETTOR's return, that is a register error, and it is material.

### C.2 The reachable slice is the worse slice

`research/THE_TENSION.md` (2026-09-11), owner statement, both halves measured:

```
BRIDGED (our mapper resolved it)   0.804%   = 0.58x the ALL ELIGIBLE rate
UNBRIDGED                          1.556%   = 1.13x
```

The part of RN1's book BETTOR could actually see and act on carried roughly
**58% of the population edge per dollar**. So even a perfect, costless copy of
the reachable slice would not have reproduced the headline RN1 rate.

### C.3 BETTOR's own execution measurement pointed the other way

From the operational record (FILL lane E14, 2026-09-08):

```
filled entries    ROI -0.0061
missed entries    ROI +0.1669
entries filled    19 of 49 in the measured window
```

Where BETTOR actually got filled, the outcome was *worse* than where it did
not. That is the signature of adverse selection, and it is a register-3
measurement — the right register — pointing away from profitability, not
toward it.

### C.4 Account-level P&L cannot be attributed to the mirror

The trading account carried **hand-placed orders alongside the mirror's**:
29 manual fills on 9 markets in a single day (2026-09-11 operational record),
a manually-registered foreign position of 1,128 shares, and a hand position on
`aec-nfl-ne-sea-2026-09-09` that the owner instructed be left untouched.

Consequence: **an account-level profit or loss figure is not a mirror
performance figure**, because the account contains trades the mirror did not
make. Any historical P&L quoted from the account balance inherits this
contamination.

### C.5 The current native-strategy research says nothing about the old mirror

Track A and Track B-L measure whether a *passive maker* strategy is executable
on PMUS. Both currently classify **C — displayed edge only**, with
`MAKER_FILL_PROBABILITY = NOT_IDENTIFIED`. This research **must not** be used
to validate the historical mirror: it is a different strategy, a different
mechanism, and it has produced no fills either.

---

## D. WHICH ASSUMPTIONS WERE SUBSEQUENTLY DISPROVED?

| assumption | what disproved it | date |
|---|---|---|
| RN1's markets are mappable to PMUS at high coverage | 81.9% unmapped; 65.15% of 45,715 refusals are `unmapped` | 2026-09-04 → 09-10 |
| CFB full-game contracts exist to mirror | no full-game per-side contract; venue `atc` rows are segment props | 2026-09-08 |
| NFL markets map by his slug | venue lists the game one day earlier under different grammar | 2026-09-11 |
| Per-order lineage to an RN1 fill exists | 3 lineage fields, 0 write sites, 0 populated rows on 11,183 orders | 2026-09-11 |
| The matched-quantity / remainder ROI figures | NULL-ignoring `LEAST` overstated quantity by 15.5%; single-leg rows silently dropped from ROI | 2026-09-11 |
| RN1's headline edge is available to us | bridged slice carries 0.58× | 2026-09-11 |
| Getting filled is good | filled ROI −0.0061 vs missed +0.1669 | 2026-09-08 |
| A displayed spread implies an executable maker edge | 0 touches in Track A at 5/10/30/60 s; 0 in B-L BLOCK_3 through 15 m | 2026-09-13 → 09-14 |

---

## E. THE MATRIX

`COMMUNICATED` and `SOURCE` are `NOT_IDENTIFIED` throughout for the reason in
§0. `MATERIAL DIFFERENCE` states the gap between the claim and the evidence.

### CLAIM 1 — "BETTOR was successfully mirroring RN1"

```
COMMUNICATED          NOT_IDENTIFIED
SOURCE                NOT_IDENTIFIED
EVIDENCE AT THE TIME  The system ran, placed real orders (11,183) and managed
                      positions. Coverage was measured at 15.5-18.1% mapped
                      from 2026-09-04 onward, i.e. the low figure was KNOWN
                      contemporaneously, not discovered later.
CURRENT EVIDENCE      Same coverage record; plus per-order causal attribution
                      NOT IDENTIFIABLE (run 78) and 86.8 s median copy latency.
STATUS                CONTRADICTED as stated / VERIFIED only in a much weaker
                      form: "BETTOR operated a system that attempted to mirror
                      RN1 and traded a minority of his markets."
MATERIAL DIFFERENCE   "Successfully mirroring" implies substantial and faithful
                      replication. Measured: ~1 in 5 markets mapped, no
                      per-order lineage, ~87 s median lag.
CORRECTION REQUIRED   LIKELY, if the word "mirroring" was used without a
                      coverage figure attached.
```

### CLAIM 2 — "The mirrored system was profitable"

```
COMMUNICATED          NOT_IDENTIFIED
SOURCE                NOT_IDENTIFIED
EVIDENCE AT THE TIME  NOT_IDENTIFIED. No verified register-3 realized net P&L
                      for the mirror is present in this repository at any date.
CURRENT EVIDENCE      Still none. Additionally: account P&L is contaminated by
                      hand-placed trades (C.4); the one direct execution
                      measurement shows filled ROI -0.0061 vs missed +0.1669.
STATUS                NOT_IDENTIFIED — and the available direct evidence points
                      against, not for.
MATERIAL DIFFERENCE   A profitability claim requires a realized net P&L
                      attributable to the mirror. That figure does not exist.
CORRECTION REQUIRED   LIKELY. This is the most consequential row.
```

### CLAIM 3 — "RN1's returns are BETTOR's returns"

```
COMMUNICATED          NOT_IDENTIFIED (may be implicit rather than stated)
EVIDENCE AT THE TIME  RN1 matched gross ROI 1.383% / bridged 0.804% -- all
                      register 1, his venue, his prices, no latency.
CURRENT EVIDENCE      Unchanged, and THREE_REGISTERS (2026-09-11) forbids the
                      substitution explicitly.
STATUS                CONTRADICTED as an inference about BETTOR.
MATERIAL DIFFERENCE   Register 1 is not register 3. Reachable slice is 0.58x,
                      before any execution cost, latency or slippage.
CORRECTION REQUIRED   YES, if any RN1-derived percentage was presented as
                      BETTOR performance or projected return.
```

### CLAIM 4 — "When RN1 wins, BETTOR wins proportionally"

```
COMMUNICATED          NOT_IDENTIFIED
EVIDENCE AT THE TIME  The standing paired-ratio metric was BUILT to answer this
                      (2026-09-08), which establishes the question was open,
                      not answered.
CURRENT EVIDENCE      No verified sustained proportionality result. Coverage
                      ~18%, latency 87 s median, no per-order lineage.
STATUS                NOT_IDENTIFIED
MATERIAL DIFFERENCE   Proportionality was the design mandate, not a measured
                      outcome.
CORRECTION REQUIRED   LIKELY, if stated as achieved.
```

### CLAIM 5 — "Historical loss was caused by a known, now-fixed defect"

```
COMMUNICATED          NOT_IDENTIFIED
EVIDENCE AT THE TIME  Target-collapse defect PROVEN IN COMMANDED STATE,
                      09-02 -> 09-05.
CURRENT EVIDENCE      Same -- plus the explicit locked finding that it is NOT
                      proven this defect caused the dollar losses, and that the
                      causal bridge does not close.
STATUS                PARTIALLY VERIFIED (the defect) /
                      CONTRADICTED (the causal claim)
MATERIAL DIFFERENCE   "We found and fixed the cause" is a stronger statement
                      than the evidence supports.
CORRECTION REQUIRED   POSSIBLE, depending on wording used.
```

### CLAIM 6 — "The system is operational"

```
EVIDENCE AT THE TIME  Real orders, real fills, real position management,
                      documented incident response.
CURRENT EVIDENCE      Same. Currently PAUSED, mirror_live=false, exits only.
STATUS                VERIFIED (as engineering), with the state caveat.
MATERIAL DIFFERENCE   None, provided "operational" was never used to mean
                      "profitable" or "in full production".
CORRECTION REQUIRED   NO, if the distinction was maintained.
```

---

## F. WHEN EACH MATERIAL FACT BECAME KNOWN

```
2026-09-04   mapping coverage measured at 15.5-19.4%           docs/mirror-coverage.md
2026-09-08   filled ROI -0.0061 vs missed +0.1669 (E14)        operational record
2026-09-08   whale-to-our fill latency 86.8 s med / 382.8 p90  E6/E9
2026-09-08   CFB full-game contracts do not exist (C6)         operational record
2026-09-10   coverage record last updated, still ~18%          docs/mirror-coverage.md
2026-09-11   NULL_AUDIT: quantity overstated 15.5%             research/NULL_AUDIT.md
2026-09-11   RETRACTIONS: prior ROI figures withdrawn          research/RETRACTIONS.md
2026-09-11   THREE_REGISTERS: register conflation forbidden    research/THREE_REGISTERS.md
2026-09-11   THE_TENSION: reachable slice is 0.58x             research/THE_TENSION.md
2026-09-11   CAUSAL_BRIDGE_CLOSED: lineage NOT IDENTIFIABLE    research/CAUSAL_BRIDGE_CLOSED.md
2026-09-11   hand-placed orders found on the account           operational record
2026-09-13   Track A: 0 both-touches / 299 postable pairs      RUN85_TRACK_BL_LIQUID.md
2026-09-14   B-L BLOCK_3: 0 touches through 15 m               RUN85_TRACK_BL_LIQUID.md
```

**The coverage and latency facts predate the deep forensic work by a week.**
They were measurable and measured while the system was running. Nothing in the
2026-09-11 forensic set was needed to know that ~4 of 5 markets were unmapped.

---

## THE THREE-COLUMN DISCIPLINE

Maintained throughout and restated here because it is the rule that keeps this
record honest:

```
WHAT WAS BELIEVED AT THE TIME     not reconstructible without the actual
                                  communications -- NOT_IDENTIFIED
WHAT WAS ACTUALLY MEASURED        the dated evidence in section F
WHAT CURRENT FORENSICS SHOW       sections B and C
```

History is not rewritten with later facts. Where a fact was knowable at the
time, section F says so; where it was not, the date stands as the date.

---

## WHAT THIS DOCUMENT DOES NOT DO

- It does not characterise the old system as successful.
- It does not present simulated, target-state or counterfactual P&L as
  realized P&L.
- It does not present RN1's profitability as BETTOR's.
- It does not treat target-state generation as successful execution.
- It does not treat passive touches as fills.
- It does not use current native-strategy research to validate the historical
  mirror.
- It does not omit contradictory evidence because communications were already
  made.
- It does not draft external language.

## WHAT IS NEEDED TO COMPLETE IT

1. **The actual investor communications** — decks, emails, memos, call notes,
   data-room contents, with dates and authors. Without them the first two
   columns of every row stay `NOT_IDENTIFIED` and no correction can be scoped.
2. **The account's realized P&L series**, separated into mirror-attributable
   and hand-placed, if such a separation is reconstructible at all.
3. **A ruling on whether any external figure was ever sourced from register 1**
   (RN1's economics) rather than register 3 (BETTOR's).

`mirror_live=false`. No capital authorized.
