# The tension — owner statement, 2026-09-11

> RN1's matched-pair economics appear real and measurable. But the subset where
> BETTOR has the best PMUS mapping/execution evidence is materially worse than
> the full RN1 population on matched edge.

Both halves are measured, not asserted. Stated with their numbers:

## Fact 1 — the matched mechanism is real and measurable

Settlement-free, on the structurally eligible population (run 63):

| | conditions | matched cost | matched gross P&L | matched gross ROI |
|---|---|---|---|---|
| ALL ELIGIBLE | 22,686 | $42,578,503 | $588,777 | **1.383%** |

Eligibility is **86.429% of conditions / 90.134% of acquisition cost /
90.643% of matched cost**, with **zero** structurally ineligible conditions and
**14,305 of 14,305** retained RN1 payout vectors summing to exactly 1. This
does not depend on settlement, on our mapper, or on who won.

## Fact 2 — the reachable subset carries materially less of it

| | matched gross ROI | ratio to ALL ELIGIBLE |
|---|---|---|
| BRIDGED (our mapper resolved it) | **0.804%** | **0.58×** |
| ALL ELIGIBLE | 1.383% | 1.00× |
| UNBRIDGED | 1.556% | 1.13× |

The part of RN1's book where we have the best PMUS mapping and execution
evidence carries **roughly 58% of the population matched edge per dollar of
matched cost**, and **52% of the unbridged rate**. In dollars: $79,034 of gross
matched P&L on $9,828,983 of bridged matched cost, against $509,743 on
$32,749,520 unbridged.

---

## What this establishes

A **reachability** problem distinct from an execution-cost problem. Even before
any register-2 replication drag or register-3 execution cost, the slice of his
book we can currently see and act on is the lower-edge slice.

## What this does NOT establish

**Nothing causal about mapping.** `has_us_bridge` is our own mapper's output.
Three explanations remain open and are not yet separated:

1. **Composition.** Bridged is **62.3% Tennis** by within-group acquisition
   cost against **43.3%** unbridged. If Tennis simply carries less matched edge
   than Soccer/MLB/other, the gap could be composition rather than anything
   about mapping. **This is the leading candidate and it is cheap to test.**
2. **Market character.** Markets our mapper resolves may be the more liquid,
   more competed, tighter-spread ones — where pair cost sits closer to 1 for
   everyone. Note p50 pair cost 0.9843 bridged vs 0.9811 unbridged: the bridged
   pairs genuinely cost more.
3. **Candidate-generation path.** Our own selection of what to attempt to map
   may correlate with market characteristics in ways not captured above.

**And the collider warning still applies.** Conditioning on `has_us_bridge` can
induce relationships absent from the full population. Any bridged-only result
stays `MAPPER_SELECTED_SUBPOPULATION`.

## The discriminating test

Does the bridged/unbridged matched-edge gap **survive within sport**?

- If the gap largely disappears once Tennis is compared to Tennis and Soccer to
  Soccer, the finding is **composition** — and the lever is which sports we can
  map, not the mapping itself.
- If the gap persists **within** each sport, composition is ruled out and the
  remaining candidates are market character and candidate-generation path.

Sport is a **class B** (exogenous, contextual) variable, available on 86.43% of
conditions and 90.13% of acquisition cost, so this test does not lean on any
class-A fill-derived quantity.

Market type would sharpen it further and is **step C** — the audited Python
classifier `market_type_of`, never an SQL port.

---

## Registers

Matched edge is **register 1** (RN1 strategy economics). Bridge status is a
property of **our** system. The combination is a statement about the
**reachability of register-1 edge**, which is legitimate — but it is not a
register-2 or register-3 result and must not be quoted as one, and it does not
yet incorporate any replication drag or execution cost.

`mirror_live=false`.
