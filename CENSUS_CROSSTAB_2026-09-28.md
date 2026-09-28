# THE CORRECTED CENSUS — and three more of my claims it refutes

**Source:** `research/census_crosstab.sql` via `research-sql.yml` against the production read replica.
**Run:** 36370099250 · job 108764425070 · `psql exit=0` · 2026-09-28T02:30:57Z · query sha256 `66fab8b1…`
**Window:** 2026-09-24T14:23:52Z → 2026-09-27T19:36:11Z

---

## 1 · Scale: I have been reporting evaluation rows as candidates

| | |
|---|---:|
| Evaluation rows | **1,126** |
| **Distinct candidates** | **62** |
| Distinct events | **54** |
| **Evaluations per candidate** | **18.16** |
| Admissible | **0** |

Every count I have given you — "1,126 candidates", "C1 = 527", "C4 = 564" — was **evaluation rows**, not candidates. The same 62 candidates were each evaluated about 18 times over three days. The settlement split stands as a row count and is roughly 18× overstated as a candidate count.

---

## 2 · The overlap you predicted, measured

| | Rows |
|---|---:|
| `NO_ACTION_HAS_POSITIVE_NET_EDGE` | 1,073 |
| `EXECUTION_ESTIMATE_NOT_IDENTIFIED` | 1,067 |
| **Both** | **1,056** |
| Edge refused, execution estimate present | **17** |
| Execution unmeasured, edge not refused | 11 |

**98.4% of the "no positive edge" refusals co-occur with a missing execution estimate.** The economics refusal is overwhelmingly an artefact of a missing input, not an economic finding. Treating 1,073 as "negative economics" was wrong, exactly as you said.

---

## 3 · The cross-tab

| Bucket | Rows | Distinct candidates | Events |
|---|---:|---:|---:|
| **1 Qualified non-positive economics** | 17 | **9** | 9 |
| **2 Economics not refused, blocked elsewhere** | 42 | **22** | 21 |
| **3 Execution economics unmeasured** | 668 | 54 | 53 |
| **4 Valuation evidence missing** | 399 | 59 | 52 |
| 5 Established settlement conflict *(alone)* | 0 | — | — |
| 6 Settlement evidence unresolved *(alone)* | 0 | — | — |

Genuinely measured non-positive economics: **9 candidates.** Not 1,073 rows, not 527.

Buckets 5 and 6 are empty because **every** settlement refusal co-occurs with an earlier valuation or execution gap. No candidate is blocked by settlement and nothing else.

---

## 4 · "42 blocked on settlement alone" is refuted

| Stage | Rows where it left a positive trace |
|---|---:|
| Settlement scope captured (`settlement_rule`) | 1,126 |
| Currency stage ran (`age_s`) | 1,126 |
| All outcomes priced | 1,126 |
| Valuation stage ran (`overround`) | 727 |
| **Execution stage produced an estimate** | **59** |
| **Every other stage ran *and* passed** | **0** |

**Zero.** My "42 blocked on settlement alone" counted rows carrying a settlement code and no other family's code — reading absence of a refusal as evidence the stage passed. That is the error you named, and this is the measurement that settles it. No candidate has cleared every other stage.

Also worth stating: `settlement_rule` is populated on **all 1,126** rows. Contract-specific settlement scope is captured, which further undercuts my "venue publishes nothing" conclusion from the capture.

---

## 5 · The largest actionable cause of unmeasured execution economics

**The execution-estimate stage produces an estimate on 59 of 1,126 rows — 5.2%.** That single gap is upstream of both economics buckets: it blocks 668 rows / 54 candidates directly, and it is the reason 1,056 "no positive edge" refusals cannot be read as economic findings.

Where it *did* run (59 rows), the picture is ordinary rather than hopeless: 17 non-positive, **42 not refused on economics at all**, average overround 0.0399.

**So the priority is inverted from what I told you.** It is not settlement. It is:

1. `EXECUTION_ESTIMATE_NOT_IDENTIFIED` — 94.8% of evaluations never get an execution estimate. Largest actionable cause; engineering, no external dependency identified yet.
2. `QUOTE_STALE` — the single most common *first* refusal (399 rows), and the A1/M1 `transactTime` blocker.
3. Settlement — frequently the first refusal (727 rows across four codes) but never the only one.

---

## 6 · First refusal versus every refusal

| First refusal | Rows where first | Avg total refusals |
|---|---:|---:|
| `QUOTE_STALE` | 399 | 7.84 |
| `VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE` | 306 | 5.53 |
| `VOID_ABANDONMENT_RULE_NOT_ESTABLISHED` | 226 | 5.66 |
| `OVERTIME_RULE_NOT_ESTABLISHED` | 180 | 3.94 |
| `VOID_ABANDONMENT_BOOK_RULE_NOT_HELD` | 15 | 6.00 |

Evaluation continues past the first refusal (3.9–7.8 codes per row), so a later code's presence does not mean that stage ran on good inputs — which is why §2's overlap matters and why bucket 1 is only 17 rows.

---

## 7 · What is still not established

- **Not** that the 9 qualified non-positive candidates are representative. 62 candidates over three days on 54 events is a small, bounded sample.
- **Not** that fixing the execution estimate produces admissions. It would move 668 rows into a bucket where economics can be *judged*, which is not the same as judged favourably.
- **Not** any claim about profitability. Admissible is 0 and no order has been placed.
