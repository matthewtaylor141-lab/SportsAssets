# THE ACTUAL CENSUS — and the two claims of mine it refutes

**Source:** `research/settlement_census_actual.sql` via `research-sql.yml` (read-only, mutation-guarded) against the **production read replica**.
**Run:** 36365547950 · job 108751120503 · `psql exit=0` · 2026-09-28T01:19:52Z · SHA `0e3b1d4`
**Window:** 30 days · **1,126 candidate rows** · every row `baseball` / `h2h` / `FULL_GAME`

---

## 1 · I was wrong, in both directions

I previously reported **C1 = 0** and **C3 = 464**, derived by handing a *reconstructed* per-condition shape to `classify_comparison`. The actual evidence records say:

| Class | Candidates | Distinct events | Admissible |
|---|---:|---:|---:|
| `C4_OTHER_UNRESOLVED` | **564** | 22 | 0 |
| `C1_ESTABLISHED_PAYOUT_CONFLICT` | **527** | 40 | 0 |
| `C2_BOOK_RULE_NOT_HELD` | **35** | 15 | 0 |
| `C3_VENUE_RULE_OR_SCOPE_NOT_HELD` | **0** | — | — |
| **Total** | **1,126** | | **0** |

**C1 is 527, not 0.** There *are* established payout conflicts, on 40 distinct events. **C3 is 0, not 464.** My reconstruction was wrong about which class the candidates fall in, and the reconstruction is exactly why — this is the difference the directive insisted on.

### By refusal code

| Code | Class | Missing side | Candidates |
|---|---|---|---:|
| `VOID_ABANDONMENT_RULE_NOT_ESTABLISHED` | C4 | not recorded by this code | 564 |
| `VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE` | **C1** | neither — both sides stated a rule | **527** |
| `OVERTIME_RULE_NOT_ESTABLISHED` | C4 | not recorded by this code | 242 |
| `VOID_ABANDONMENT_BOOK_RULE_NOT_HELD` | C2 | **BOOKMAKER** | 35 |

---

## 2 · This also refutes my settlement-capture conclusion

I wrote that the venue "does not publish a deterministic payout per condition" and that "further reading will not change that" — the second phrase the directive told me to withdraw. **Withdrawn**, and the census shows the first was wrong too:

`VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE` fires on 527 candidates. `compare()` emits a *conflict* only when **both sides stated a payout**. So for those candidates the venue's void/abandonment payout **was captured and compared** — it simply disagreed with the bookmaker's. The rulebook was the wrong document: the per-contract terms reach the comparator through the market's own `description` / `assetPriceTerms` (`bettor_live_read.read_rules_text`), which is the route Chapter 10.2 delegates to.

Further: **`settlement_rule` is populated on every row** — `FULL_GAME_INCLUDING_EXTRA_INNINGS`. Contract-specific settlement scope is captured, not missing.

**What stands from the capture:** the rulebook quotations are accurate, and 10.3(a)'s power to modify Payout Condition at any time remains a real standing risk to disclose. **What does not stand:** the conclusion that the venue publishes no payout for these conditions.

---

## 3 · Six versus seven conditions — the discrepancy explained

I wrote "six conditions" in one place and "seven" in another. Neither came from the comparator's actual behaviour.

- `bettor_settlement_terms.CONDITIONS` has **seven** members, and `APPLICABLE_CONDITIONS[("baseball","h2h")]` is all seven. That is the comparator's requirement.
- "Six" was my own arithmetic from a reconstructed example — the venue's 380-character description stating one of seven, leaving six silent. **That reconstruction is not evidence** and the census contradicts it.
- The census shows only **two** settlement conditions actually generate refusals in this corpus: **void/abandonment** (564 unresolved + 527 conflicting + 35 book-side-missing) and **overtime** (242 unresolved). The other five conditions produce no refusal code at all in the window.

So the capture target was never "six conditions". It is **two conditions that actually refuse**, mapped to `C_NOT_PLAYED` and `C_OVERTIME`. General rulebook clauses did not populate contract-specific grading, and I should not have implied they could.

---

## 4 · Settlement is not the binding constraint

§2 required the independent blockers stay visible. They dominate:

| Blocker family | Candidates blocked | Code occurrences |
|---|---:|---:|
| SETTLEMENT | 1,126 | 1,368 |
| ECONOMICS (`NO_ACTION_HAS_POSITIVE_NET_EDGE`) | 1,073 | 1,561 |
| INSTRUMENT_IDENTITY (`EXECUTION_ESTIMATE_NOT_IDENTIFIED`) | 1,067 | 1,067 |
| OTHER (risk gate, sizing, depth) | 946 | 2,507 |
| MARKET_DATA_CURRENCY (`QUOTE_STALE`) | 399 | 399 |

```
candidates 1126 · zero_refusals 0 · blocked_on_settlement 1126
settlement_is_the_only_family   42
settlement_clear                 0
every_family_clear               0
admissible                       0
```

**Only 42 of 1,126 candidates are blocked on settlement alone.** Resolving settlement entirely would leave 1,084 still refused on economics, instrument identity, depth, risk sizing or quote currency. Settlement is necessary and nowhere near sufficient, and my earlier framing of it as *the* binding constraint was wrong.

---

## 5 · The five buckets §3 asked for

| Bucket | Count | Evidence |
|---|---:|---|
| Terms captured and **compatible** | **0** | no candidate reaches `COMPATIBLE` |
| Terms captured and **conflicting** | **527** | `VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE`, 40 events |
| **Venue** terms not captured | **0** | no C3 code fires in the window |
| **Source** terms not captured | **35** | `VOID_ABANDONMENT_BOOK_RULE_NOT_HELD` |
| Scope / interpretation **unresolved** | **806** | void-abandonment 564 + overtime 242, side not recorded by the code |

The 806 remain side-ambiguous because `external_valuations` persists refusal **codes**, not the per-condition verdicts `compare()` produced. Splitting them needs those verdicts persisted — a schema change, named rather than papered over, and *not* something a reconstruction may stand in for.

---

## 6 · What to do next, bounded

1. **Persist the per-condition verdicts** beside the refusal codes, so the 806 resolve into C2/C3/C4 from evidence. Engineering; no external dependency.
2. **Inspect the 527 conflicts** — the venue and bookmaker payouts are both known, so the disagreement is readable now. If the venue's void rule is 50-50 and the bookmaker voids the wager, that is a genuine incompatibility for those events and the right response is different markets, not more capture.
3. **Prepare, do not send,** an exchange clarification request for the residual ambiguity. No external correspondence without authorization.
4. **Stop treating settlement as the gate.** `NO_ACTION_HAS_POSITIVE_NET_EDGE` (1,073) and `EXECUTION_ESTIMATE_NOT_IDENTIFIED` (1,067) need the same attention.
