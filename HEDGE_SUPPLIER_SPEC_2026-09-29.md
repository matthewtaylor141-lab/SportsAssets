# The remaining hedge suppliers: what each needs, from the real interfaces

Written against the actual contracts, not from memory, so the next work does not
start with a guess. Codex asked for these to stay on the implementation plan
while the immutable gate runs; this is that plan.

## What `discover` requires, and why it cannot be stubbed

`bettor_funded_pair_cycle.discover(held_leg=, candidate_legs=, …)` consumes
`bettor_indirect_structures.Leg`, whose docstring is explicit:

> Every field is required to be stated. `None` on a field this leg's kind needs
> is a refusal, not a default: an unstated overtime rule or an unstated
> orientation makes the payout function unknowable, and a structure built on an
> unknowable leg is a fabricated hedge.

The fields: `condition_id`, `fixture_id`, `kind`, `period`, `overtime`, `backs`,
`line`, `over_under`, `quantity`, `cost_cents_per_unit`, `tie_rule`.

`discover` then matches on `Leg.grading_key()` — fixture, period, variable,
overtime treatment. **Two legs differing on any of those are graded against
different random variables however similar their titles look.** So a supplier
that guessed `overtime` would be manufacturing settlement compatibility, which
is the thing the whole module exists to refuse.

## Supplier 1 — `held_leg`

| field | source | state |
|---|---|---|
| `condition_id`, `fixture_id` | `bettor_funded_intents` + `us_premap.event_slug` | available |
| `kind`, `line`, `backs` | `us_premap.kind`, `.line`, `.side_norm` | available |
| `quantity`, `cost_cents_per_unit` | the intent's residual and basis | available |
| `period` | the period-metadata read already in `ext_pinnacle_loop` | available |
| `overtime` | **captured venue settlement text** | must be read |
| `tie_rule` | **captured venue settlement text** | must be read |

The last two are the blocker and they are not a plumbing problem: `us_premap`
carries no overtime or tie rule. They come from the venue's own settlement prose
via `bettor_venue_settlement` / `bettor_settlement_terms`, and where the prose
was never captured for a contract the leg must refuse — the modules already say
so ("we do not supply the venue's rules from memory").

## Supplier 2 — `candidate_legs`

The complementary contracts on the same fixture. `us_premap` can enumerate them
(`event_slug` → sibling `market_slug`s, which the existing period-metadata query
already counts as `sibling_markets`). Each needs the same eleven fields, so each
needs its own captured settlement text. **This is where the real cost sits:** one
settlement capture per candidate contract, and the venue reads are behind
`venue_pace`, a process-wide serial gate.

## Supplier 3 — `region_probabilities`

`bettor_funded_model.region_probabilities` / `predict_for` exist and are governed
by the promotion bar (migration 135, `MIN_EVALUATION_ROWS=40`,
`MIN_SKILL_MARGIN=0.01`). Production has **no approved model** — the registry is
empty — so this supplier returns nothing until one is promoted, and promotion
needs the prospective non-funded evidence path. It must not be substituted with
venue-implied prices: that is the "independently validated edge" the directive
forbids.

## Supplier 4 — per-candidate evaluation

Codex: *rank eligible candidates rather than selecting the first admitted
contract.* Once suppliers 1–3 exist this is mechanical: build an
`FD.indirect_candidate` per admitted contract, score each on its own fee and
depth reading, and pass the ranked set. It is blocked only by the per-contract
inputs, and the step already reports `hedge_candidates_not_ranked` rather than
implying a ranking happened.

## The four `cycle()` proofs, and what each still waits on

| proof | waits on |
|---|---|
| HOLD beats EXIT/REDUCE, hedge beats HOLD and is selected | suppliers 1–3 |
| EXIT/REDUCE beats the hedge and dispatches its own matching plan | suppliers 1–2 (the hedge must be a real candidate for the comparison to mean anything) |
| no eligible hedge leaves an independently valid exit executable | **available now** — the exit path is wired and bound |
| exactly one ordinary decision persists before exactly one dispatch | **available now** |

The last two are producible against the current tree; the first two are not, and
no substitution of the missing ranking or final candidate would make them proofs
of integration — which is exactly what Codex said not to do.

## Indirect middles remain to be *evaluated*, not assumed

Preference has to be earned by executable, fee-adjusted value and verified
settlement outcomes. Two things already in the tree bear on that and neither
supports an assumption of superiority:

- the scale repair (a 10-unit middle was valued at $0.17 instead of $4.40) means
  earlier per-unit figures understated *and* that the corrected figures have not
  been re-tested against outcomes;
- `SETTLEMENT_COVERAGE` on the historical cohort recorded **no compatible
  settlement result at all** — INCOMPATIBLE 527, UNKNOWN 353, NOT_RECORDED 246.
  That establishes no verified middle outcome in that cohort. It does not
  establish that none can qualify, and it certainly does not establish that a
  moneyline/opposing-spread combination cannot lose.

## Book currency stays unresolved

`P5_DOCUMENTED_TIMING` is the single unmet precondition and only the venue can
supply it. Not to be manufactured from movement, receipt time or a fabricated
subscription; four acceptance tests hold those closed.
