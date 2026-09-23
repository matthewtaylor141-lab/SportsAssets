# PINNACLE_DEVIG_V1 — external bookmaker valuation

Shadow only. No funded orders. `external_valuations.order_submitted` has a
CHECK that it is FALSE, so the schema cannot store a submitted order.

---

## 1 · What the provider actually is, measured

`feed-coverage` run **35933793563**, 2026-09-23T23:28:56–59Z, against the
real plan. Not assumed, not remembered:

| sport | events | Pinnacle | segments with Pinnacle |
|---|---|---|---|
| `baseball_mlb` | 19 | **13 (68%)** | h2h/spreads/totals_1st_5_innings |
| `soccer_epl` | 20 | **20 (100%)** | h2h_h1 |
| `soccer_mexico_ligamx` | 9 | **9 (100%)** | none sharp |
| `basketball_nba` | 41 | **0** | none |
| `icehockey_nhl` | 33 | **0** | none |

- **Provider:** TheOddsAPI v4 (`api.the-odds-api.com`), 82 sports on this
  plan. Pinnacle is one bookmaker *inside* its payload, filtered client
  side — there is no separate Pinnacle API.
- **Request shape:** `GET /v4/sports/{key}/odds?regions=eu,uk,us&markets=…&oddsFormat=decimal`
- **Budget:** the whole probe cost **99 credits**
  (`x-requests-used` 8,063,058 → 8,063,157). Featured bulk ≈ 18–21 per
  sport; per-event segments 6–9; props 6–15.
- **Props carry no sharp book on any sport** (MLB 173 outcomes, EPL 56,
  NBA 18 — none sharp), so props are out of scope entirely.

**So `SUPPORTED` is `soccer/h2h` and `baseball/h2h`.** Basketball and
hockey get their own refusal, `PINNACLE_DOES_NOT_QUOTE_THIS_SPORT`,
because "not supported yet" and "the book does not price it" have
different remedies.

## 2 · Reuse, and its one hard limit

Reused as **method and semantics**, cited in the module:

| from | what |
|---|---|
| `edge/fairvalue/devig.py` | multiplicative and power de-vig, and its own note that the reference account's calibration signature is consistent with **power** on a Pinnacle-class feed |
| `edge/fairvalue/feed.py` | `ANCHOR_BOOKS` (pinnacle weight 3.0), the **30 s** `is_fresh` hard rule, `observed_at` vs `received_at`, per-**outcome** depth (the winner's-curse audit), and "a segment we have no quotes for is REFUSED, never priced off the full game" |
| `edge/venues/mapper.py` | team-name normalisation for event identity |

**Could not be imported.** `edge.fairvalue.devig` imports
`scipy.optimize.brentq`; scipy and numpy are absent from the image and
`edge` is not an installed package here (`import edge` →
`ModuleNotFoundError`). So the arithmetic is re-implemented in the standard
library and the power root **bisected** instead.

Verified against an **independent** root solve (coarse scan + 300
bisections, not a second call into the same code) on five odds sets
including a 1.01/80/200 tail: **max deviation 1.03e-13**.

## 3 · What the source refuses, and why each one exists

| refusal | the failure it prevents |
|---|---|
| `OUTCOME_SET_INCOMPLETE` | a 3-way soccer market de-vigged over 2 outcomes returns a number that still looks exactly like a probability |
| `PINNACLE_NOT_IN_THIS_PAYLOAD` | silently substituting lowvig or smarkets for the book the source is named after |
| `PINNACLE_DOES_NOT_QUOTE_THIS_SPORT` | reporting "no data" when the truth is "this book does not price it" |
| `QUOTE_STALE` (>30 s) / `QUOTE_HAS_NO_TIMESTAMP` | trading on a price that has moved |
| `MAPPING_AMBIGUOUS` / `SELECTION_NOT_IN_OUTCOME_SET` | a near-match pricing a *different* bet that still prices cleanly. **No fuzzy fallback** — the mapper's 0.9-similarity instrument is deliberately the wrong tool here, because the direction is reversed |
| `PERIOD_DOES_NOT_MATCH` | a full-game price serving a first-half contract. A field declared on one side only is a refusal, not agreement |
| `SETTLEMENT_RULE_DOES_NOT_MATCH` | regulation-90 priced against an includes-extra-time contract |
| `LINE_DOES_NOT_MATCH` | a prop or spread paired against the wrong number |

## 4 · Wired into the production path, labelled

`bettor_entry_gate.admit` gained two **additive** parameters. With no
external source its behaviour is byte-identical and a test asserts that.

The external source satisfies **`INDEPENDENT_FAIR_VALUE`** under an
explicitly enabled experiment and **`QUALIFIED_MODEL` not at all**:

```
qualified_model                      False
belief_provenance                    EXTERNAL_BOOKMAKER_VALUATION
is_a_qualified_settlement_model      False
```

`bettor_fair_value` is untouched — `FV_BETTOR_INDEPENDENT` is still
`NOT_IDENTIFIED`, the champion is still `B0_VENUE_PRICE`, and a test
asserts both. This experiment is a different question from that one: not
"is our model better than the market" but "does a sharp book disagree with
this venue by more than the cost of crossing".

`shadow_bettor.decide` forwards `opportunity["entryInputs"]` straight into
the gate, so the production worker path carries it with **no separate
plumbing**. A test calls `decide` exactly as the worker does and gets an
admissible BUY with `orderSubmitted False`; called again without
`entryInputs`, the same function returns `NO_TRADE`.

Execution and risk checks all still bind — missing `p_fill`, zero size,
blocked risk, unreadable book and zero depth each refuse, parameterised so
removing one fails exactly one test. The comparison is
**probability − ask − fee**, against the **ask** because a resting price
invents a queue position we never held; a test asserts no bid appears
anywhere in the module.

## 5 · The real run

`feed-coverage` run **35935500538**, 2026-09-23T23:49:52Z. 42 outcomes
priced from Pinnacle's complete sets:

| event | p(home) | age s | books | overround |
|---|---|---|---|---|
| Fulham v Hull City | 0.5811 | 2.6 | 4 | 0.0553 |
| Manchester United v Tottenham | 0.5740 | 2.6 | 5 | 0.0553 |
| Atlanta Braves v Cincinnati Reds | 0.5740 | 13.9 | 5 | 0.0457 |
| Boston Red Sox v Cleveland | 0.5584 | 13.9 | 5 | 0.0487 |
| Seattle Mariners v Houston | 0.5430 | 13.9 | 7 | 0.0198 |
| Liverpool v Manchester City | 0.3535 | 2.6 | 5 | 0.0497 |
| Cruz Azul v Toluca | 0.3496 | 29.8 | 6 | 0.0499 |
| Necaxa v América | 0.2416 | 29.8 | 6 | 0.0541 |

- quote age: **min 2.6 · median 13.9 · max 29.8 s**
- **0 of 42** below the 2-book per-outcome floor
- refusals: `NO_PINNACLE_ON_EVENT` **6**

**Two findings in those numbers.** Liga MX quotes arrive at **29.8 s**,
0.2 s inside the 30 s rule — that competition will intermittently fail
freshness, and that is the feed's property, not our code's. And MLB
overrounds (0.0198–0.0487) are materially tighter than soccer's
(0.0487–0.0553), which is what a 2-outcome market versus a 3-outcome one
looks like.

## 6 · The blocker, stated exactly

Neither authorized execution context holds both halves:

| | odds key | database |
|---|---|---|
| GitHub runner | **`EDGE_ODDS_API_KEY` yes** | `DATABASE_URL` **NO** — referenced by `calibration-evidence.yml` but not actually a repository secret; measured **empty** at 23:44:08Z |
| `sportsassets-api` | **NO** — `env-keys` 22:10:54Z lists no odds-feed key at all | **yes** |

`EDGE_ODDS_API_KEY` is provisioned on `edge-shadow`. **A production
credential is never copied between services from here**, so closing this is
an owner action, and it is one of exactly two:

1. provision `EDGE_ODDS_API_KEY` on `sportsassets-api` (then the in-API
   path values live, persists and the tile goes LIVE), **or**
2. set `DATABASE_URL` as a repository secret (then the runner persists,
   maps venue contracts and the census fills).

Until one of those happens: **no venue mapping, no executable ask, nothing
persisted, and no entry decision is claimed in either direction.** A
probability with no executable price beside it is not an opportunity.

## 7 · What is NOT established

- **No opportunity has been found.** 42 valuations are one half of a
  comparison.
- **No calibration result.** Calibration needs outcomes joined to
  predictions recorded beforehand; the ledger enforces that ordering with a
  `BEFORE INSERT` trigger, and it has no rows yet.
- **No net shadow return, and no execution-sensitivity measurement.**
- **Not validated against the venue price.** The internal challenger was
  measured *worse* (Δ log loss −0.00926); this source has not been measured
  against it at all and must not be read as succeeding where that failed.
- **Every fill would still be modelled.** `P_FILL` remains
  `NOT_IDENTIFIED`.
