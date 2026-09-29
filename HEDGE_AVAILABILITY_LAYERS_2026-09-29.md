# Hedge availability, separated into layers — and a claim withdrawn

## The claim I withdraw

I reported: *"2,440 of the 2,531 fixtures carrying any admitted contract carry
exactly one. A hedge needs a second admitted contract on the same fixture, so on
96% of those fixtures an indirect hedge is structurally unavailable — not blocked
by missing plumbing, and not fixable by finishing the suppliers."*

That was a measurement of **my own supported-type filter**, presented as a fact
about the venue. Two things were wrong with it:

1. **It counted the whole `us_premap` table.** That table is a rolling catalogue
   with no delete, so it holds finished fixtures beside live ones. Of 3,396
   fixtures with an event slug, only **1,871 start in the future**; the rest
   cannot be traded at all and were padding the denominator.
2. **It called a parser gap a venue gap.** Run 265 had already shown the shape of
   the error — `soccer_team_full_time_winner`, 1,338 rows over 223 events,
   excluded for no reason but a spelling my suffix list did not carry — and I
   reported the number anyway.

## The five layers, measured on future fixtures

Authorized read-only route, run 267, `us_premap` where `game_start > now()`.

| layer | fixtures | venue instruments | of which our parser supports |
|---|---|---|---|
| **L0** no second venue instrument | 907 | 907 | 905 |
| **L1** second instruments exist, our parser does not support their type | **634** | **8,738** | 570 |
| **L2+** two or more supported instruments | **330** | 8,272 | **1,645** |

Whole table, for comparison — this is where my 2,440 came from: L0 1,503, L1
1,189, L2+ 723.

So the corrected reading is:

- **L0 is a real venue fact, and it is 907 fixtures — 48% of the tradeable
  window, not 96%.** Single-instrument fixtures do exist in quantity and no
  amount of parser work reaches them.
- **L1 is ours.** 634 future fixtures carry 8,738 venue instruments between them
  and our parser recognises 570. That is the largest single block and it is an
  engineering gap, not a market fact.
- **L2+ already exists today: 330 future fixtures with 1,645 supported
  instruments.** The priority set is not hypothetical.

## The priority set, named

Run 266 statement 4, future fixtures already inside the parser:

| fixture | supported instruments | types | starts (UTC) |
|---|---|---|---|
| `nfl-pit-cle-2026-10-01` | **73** | full-game winner, full-game spread, first-half spread | 2026-10-02 00:15 |
| `cfb-wkent-nmxst-2026-10-01` | 48 | same three | 2026-10-02 00:00 |
| 18 × `unl-*-2026-09-29/10-01` | 11 each | full-time winner, full-game spread, first-half spread | 2026-09-29 16:00 onward |

One complete supported path should be built against `nfl-pit-cle-2026-10-01` or
one of the UNL fixtures. Both carry three distinct supported types on one
fixture, which is what a margin-graded structure needs.

## The L1 gap, ranked by what it would open

The next suffix to support is a measurement, not a preference:

| unsupported type | future fixtures | instruments |
|---|---|---|
| `table_tennis_set_1_winner` / `_set_2_` / `_set_3_` | 324 each | 324 each |
| `table_tennis_match_total_sets` | 324 | 648 |
| `tennis_match_games_spread` | 164 | 984 |
| `tennis_match_exact_score` | 164 | 656 |
| `tennis_match_total_games` / `_total_sets` / `_sets_spread` | 164 each | 164–492 |
| `futures` | 66 | 3,189 |

A caution that goes with the table: most of these are **not** simply missing
patterns. A set winner is graded on sets, a games spread on games, an exact score
on the score pair — none is VAR_MARGIN, VAR_TOTAL or VAR_WIN3, and the period
vocabulary has no member for "set 2". Supporting them means **extending the
variable and period vocabulary in `bettor_indirect_structures`**, which is real
design work with a real correctness risk (a set total and a games total sharing a
grading key is the same class of error as the corners total). `futures` is not a
fixture at all and should stay out.

So L1 is our gap and it is not a cheap one. The honest ordering is: build the
complete path on L2+ first, where the vocabulary already fits.

## L2 and L3: what gates them

**L2 — settlement.** The overtime rule is read per contract from the venue's own
prose at cycle time, and **nothing persists it**. Run 267 found no per-contract
venue settlement table in production (`bettor_state_settlements` and
`rn1x_terminal_settlements` are different things). So every cycle re-reads the
prose behind `venue_pace`, and a fixture's L2 status cannot be queried in advance
— only discovered one paced read at a time. Persisting captured prose is a
concrete engineering item.

**L3 — qualified probabilities.** The registry is not merely empty. Run 267:

| table | exists in production |
|---|---|
| `bettor_funded_intents` | yes |
| `us_premap` | yes |
| `external_valuations` | yes |
| `bettor_funded_models` | **no** |
| `bettor_funded_decisions` | **no** |
| `bettor_funded_decision_outcomes` | **no** |
| `bettor_funded_leg_reservations` | **no** |
| `bettor_funded_operation_evidence` | **no** |

**The entire migration 131–135 schema is undeployed.** That is consistent with
production carrying 125–128, and it is the top-level blocker for the lifecycle:
the decision ledger, the reservation table, the outcome versions and the model
registry all live in it. Until it is deployed there is nowhere in production to
persist a pairing decision, reserve a leg, version an outcome, or approve a
model — so the prospective learning pipeline cannot run in production even once
its code is finished.

That release is owner-gated and stays gated: migration 131 is to be kept out of
production until its replacement invariants pass, and nothing here changes that.

## What this does and does not establish

It establishes that hedge instruments exist on 330 tradeable fixtures today, that
the largest remaining block is our own parser, and that the production schema for
the whole decision path is absent.

It establishes nothing about whether any of those 1,645 supported instruments
carries a profitable, fee-adjusted, executable structure. Price, depth and
currency are per-contract cycle-time reads, and a qualified probability needs an
approved model. Preference for indirect middles remains something to be earned by
measurement, not assumed.
