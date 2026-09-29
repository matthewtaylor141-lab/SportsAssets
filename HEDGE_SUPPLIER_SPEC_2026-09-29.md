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

---

# ADDENDUM, after reading the catalogue instead of describing it

Runs 262–265 of the authorized read-only route. Everything above the line was
written from the module interfaces; this was written from the venue's rows,
and it corrects the section above in three places and adds one measurement
that changes what this item can claim.

## Three corrections to the plan above

The table under "Supplier 1" marked `kind`, `line` and `backs` **available**
from `us_premap`. All three were wrong.

| plan said | the catalogue says |
|---|---|
| `kind` from `us_premap.kind` | `kind` is the literal `'side'` on all 60,540 rows |
| `line` from `us_premap.line` | on a moneyline that column is the GAME START MINUTE — every value two digits in 00..59, and the baseball rows carrying `'00'` start at 9:00 AM UTC |
| `backs` from `side_norm` | `side_norm` is the team's own NAME on a moneyline and `yes`/`no` on a spread; neither states which participant is listed first |

Two successive versions of the derivation read one of those columns and were
refuted. A third defect survived inspection and appeared only when the real
row went through: `Leg.line` is defined against **team A's** margin whichever
side the leg backs, so the B-side row's `+10.5` had to become `-10.5`. Left
alone it inverts the payout function of every B-side spread, and a pair built
from one reads as a middle that cannot lose while being a doubled position.

## One fact measured rather than asserted

**Every one of the venue's seventeen slug prefixes carries exactly 2.00 rows
per `market_slug` and exactly 2 distinct intents** — `asta`, `tsc-`, `asc-`,
`atc-`, `tec-`, `aec-` and eleven more, without exception across 60,540 rows.
One instrument per market; the side is carried only by the order intent. So
the opposite side of a held contract is netting on one instrument and can
never be a second settling holding. That was previously an assertion about how
the venue works; it is now a measurement.

## The measurement that bounds this item

The `Leg` vocabulary grades three variables — signed MARGIN, combined TOTAL,
three-way WIN3 — over six named periods. 215 `sports_type` values exist and
most name neither: `soccer_game_total_corners` (560 rows),
`football_player_receiving_yards` (946), `tennis_match_total_games` (1,686),
`table_tennis_match_total_sets` (1,796), `futures` (6,562). Mapping a corners
total onto VAR_TOTAL would give two legs a shared `grading_key()` while one
settles on corners and the other on points, and `discover` would report a
guaranteed minimum payout that does not exist. So the allowlist is narrow by
construction, and run 265 measured what that costs:

| distinct admitted contracts on the fixture | events |
|---|---|
| 1 | **2,440** |
| 4–9 | 79 |
| 42–74 | 12 |

**2,440 of the 2,531 fixtures carrying any admitted contract carry exactly
one.** A hedge needs a second admitted contract on the same fixture that is
not the held instrument, so on 96% of those fixtures an indirect hedge is
structurally unavailable — not blocked by missing plumbing, and not fixable by
finishing the suppliers.

Two things follow, and both are reportable rather than arguable:

- the remaining supplier work is worth doing and is now done for the facts the
  catalogue can supply, but it does not make indirect hedging available at
  scale on this venue's current board;
- **`bettor_funded_intents` holds zero rows.** There is no held position in
  production for `held_leg` to be built from, so the two `cycle()` proofs that
  need a real held position and a real candidate cannot be produced from
  production data today, by anyone, regardless of code.

Both remain true after this batch. Neither is a reason to stop building the
supplier; both are reasons not to describe the supplier as making hedging
available.

## Still not supplied, and unchanged

`region_probabilities` needs an APPROVED model and the production registry is
empty. Venue-implied prices must not be substituted for it. `P5_DOCUMENTED_
TIMING` remains the single unmet book-currency precondition and only the venue
can supply it.
