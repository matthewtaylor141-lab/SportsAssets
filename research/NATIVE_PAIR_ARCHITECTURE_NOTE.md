# RESEARCH NOTE ONLY — a native, non-copying architecture

**Status: NOT APPROVED, NOT BUILT, NOT SCHEDULED.** Owner instruction
2026-09-11: record the idea so it is not lost, build nothing. Nothing in this
file is a recommendation, a feasibility claim, or a profitability claim. No
code, migration, worker, env switch or order path may cite this file as
authority. `mirror_live=false`.

---

## The concept

BETTOR stops using RN1 as the live execution trigger and instead watches, in its
own right, **several economically equivalent representations of the same
outcome** — for example:

- PMUS  Team A YES
- PMUS  Team B NO
- Kalshi Team A YES
- Kalshi Team B NO

For a genuine two-outcome event these *may* be economically equivalent exposure,
**subject to the exact contract and settlement rules** — that qualifier is the
whole of the risk, not a footnote.

The system would:

1. prove contract equivalence;
2. normalize every equivalent exposure into one signed unit;
3. maintain real-time books across the representations;
4. choose the cheapest executable representation for a desired exposure;
5. construct complementary positions when the **all-in pair cost** is
   sufficiently below $1;
6. account for fees, depth, partial-fill risk, settlement mismatch and capital
   usage;
7. operate independently of RN1.

RN1 becomes research and training data, not the trigger.

## Why this is being written down now

The Run 78 result closed the question of whether the historical mirror damage
can be attributed; it did not close the question of whether *reactive copying is
the right architecture at all*. The latency/edge-decay audit now beginning
(`LATENCY_AUDIT_DESIGN.md`) is the study whose outcome bears on that. If that
study returns OUTCOME B — most of RN1's edge is gone before the first defensible
moment a follower can react — then the reactive architecture is the thing to
question, and this note is where the alternative already sits.

## The equivalence bar — nothing is arbitrage until these are proven

**Similar market titles DO NOT establish equivalence.** Title matching is how
the existing mapper works and it is a *coverage* device, not a *settlement*
proof. Before any two contracts may be treated as complementary legs, all of
the following must be verified from the venues' own contract terms:

- the exact proposition being settled;
- the participants (exact identity, not name similarity);
- the event (exact fixture, including the venue-date disagreements already
  observed — his `nfl-ne-sea-2026-09-10` vs the venue's `-2026-09-09`);
- the settlement source (which feed decides, and what happens when feeds
  disagree);
- cancellation and postponement rules;
- retirement / DNP / walkover rules;
- overtime, extra time and tiebreak treatment;
- void rules;
- payout behaviour, including timing and any partial-payout mechanic.

Any one of these unproven and the pair is **not** equivalent; it is two related
bets with a basis, and the "cost below $1" arithmetic does not describe the
payoff.

## Known unmeasured inputs — these gate the idea, not decorate it

- **Fees.** No fee column exists anywhere in `backend/migrations/`. The venue's
  fee and settlement mechanic are being measured by the $1 pair probe (task
  #126) and are **not known today**. A pair cost "below $1" is meaningless
  without them.
- **Depth.** Executable size at a price, on both venues at the same defensible
  instant, is not retained today for the Kalshi side at all.
- **Partial-fill risk.** A one-legged pair is a directional position, not a
  hedge. The mirror has already produced one-legged states in production
  (placement_lost, cancel_pending freezes).
- **Settlement mismatch.** Two venues that resolve the same fixture differently
  turn a "riskless" pair into a total loss on one leg.
- **Capital usage and collateral treatment**, which differ by venue and change
  the return per dollar even when the pair is sound.

## What would have to happen before this is even a proposal

1. The latency/edge-decay study completes and its outcome is recorded.
2. The pair probe (task #126) returns a measured fee and settlement mechanic.
3. A contract-equivalence verifier is designed and adversarially reviewed —
   against the eight bullets above, on real fixtures, including the known
   date-disagreement and mascot-naming families.
4. The owner decides, explicitly, that this is worth building.

Until all four, this file is a note.

`mirror_live=false`. Do not enable trading.
