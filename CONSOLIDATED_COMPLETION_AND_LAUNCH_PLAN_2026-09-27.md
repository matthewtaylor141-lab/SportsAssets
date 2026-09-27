# CONSOLIDATED COMPLETION AND LAUNCH PLAN

**Date:** 2026-09-27 · **Branch:** `claude/command-center` · **Serving API build:** `ad95d69` · **Worker build:** `f5d1c05` (pinned, unchanged)

This is the single plan §1 asks for. It covers every remaining requirement from the management report's A1–A9 and R1–R7, the eight management-requirement rows, and §2–§10 of the pairing directive. One row per requirement, with implementation, integration point, owner, acceptance criteria, evidence, dependencies and estimate.

**Funded submission is disabled and stays disabled.** Nothing below asks for that to change. The approval package in §10 is prepared *for* a decision, not as one.

---

## 1 · The four states, defined before anything is placed in them

These are not synonyms and the register has previously blurred them. Every row below carries exactly one.

| State | Means | Does **not** mean |
|---|---|---|
| **IMPLEMENTED** | Code exists and is unit-tested. | That any production caller runs it. |
| **TESTED** | The real call sites exercise it, in tests, with the dependency versions the gate installs. | That it has ever run outside a test process. |
| **DEPLOYED** | The exact SHA is serving, read back through the production interface. | That the code path executed. A serving `/healthz` is not an execution. |
| **OPERATIONALLY VERIFIED** | A complete cycle **started and finished on that SHA**, and its own writer identity, configuration, inputs, candidate outcomes and servicing results were read back. | That it traded. Funded execution is a fifth state, reached by nobody here. |

Two asymmetries carried forward deliberately:

- **A serving health endpoint is not a cycle readback.** The `ad95d69` release was read back as serving, but both cycle readbacks carried writer build `1d2db59`. Nothing in this repository is OPERATIONALLY VERIFIED on `ad95d69`.
- **"Refuses in more cases" is not an acceptance criterion.** A gate that fails closed because it is unbound is a coverage gap, not a safety result. 56 of the 152 standing gate failures are exactly that.

---

## 2 · What the two case studies changed about this plan

The studies were read as inputs to §1, and one finding reorders the critical path. It is not a defect; it is a translation that had been assumed rather than established.

**Both accounts' dominant realisation route does not transfer as written.** Ferrari merged **265,628,010 of 335,298,202 pairs (79%)** back into USDC *before resolution*, realising a below-$1 pair's surplus minutes after the second leg filled. RN1 is the same shape. The study states the mechanism plainly: on the Polymarket **global CLOB**, merging is a same-block on-chain operation returning $1 per complete set. Both studies name their venue explicitly as *not* Polymarket US.

On our intended venue the answer differs **by lane**, and `bettor_merge.py` already holds both:

| Lane | `PMUS_NATIVE_MERGE_AVAILABLE` | Mechanism | Consequence for the strategy |
|---|---|---|---|
| **Retail** | `NO` | `VENUE_AUTO_NETS_AT_FILL` — one signed `netPosition` per market slug. Buying NO at 0.51 *is* selling YES at 0.49. | The pair is **realised at the second fill** and capital returns immediately — economically the merge route, *faster*, with no merge call. The "hold both legs, merge later" inventory model does not apply. |
| **Institutional** | `NOT_IDENTIFIED` | — | Not `NO`. `pmx_institutional.py` is market-data only (`ORDER_SUBMISSION_IMPLEMENTATION = NONE`), so its silence about merge is uninformative. `MERGE` stays blocked rather than recorded unavailable. |

Three consequences the plan now carries:

1. **Capital-release timing is a retail advantage, not a gap.** Ferrari's median 22.9 minutes (RN1: 19.9) from first leg to completing leg is time the capital was committed *before* a merge call. Retail netting returns it at the fill.
2. **The accounting must keep the leg actually traded.** The netting is real at the cash level and says nothing about what was executed: a NO buy had a NO fill, NO fees and NO queue position. `bettor_merge.DETERMINATIONS[RETAIL]["doesNotLicense"]` states this; §6's reconciliation must honour it.
3. **The residual term, not the pairing term, is the strategy.** Ferrari: paired book **+$10.8M gross** against **510,107,761 unpaired shares returning −$9.5M** — the study says the directional result dominates the account's total. RN1: +$9.9M paired against a $180.9M-cost residual. An evaluation restricted to completed pairs would have called both machines excellent and been wrong about both strategies. This is now enforced in code (§4 row below).

---

## 3 · The critical path

Ordered by what actually blocks funded operation. Everything else can proceed in parallel and most of it already is.

| # | Blocker | State | Why it is on the critical path | Owner | Earliest credible resolution |
|---|---|---|---|---|---|
| **C1** | **Source qualification: 0 of 464 candidates admissible.** The refusal is now decomposed — see the correction immediately below. | OPEN | With zero admissible candidates the system has nothing to trade regardless of every other component. This is the binding constraint. | Engineering + venue capture | Days. The remedy is now named: capture six venue conditions. **Not resolvable by lowering qualification.** |
| **C2** | **`transactTime` semantics unresolved.** M1 precondition **P5** is the only unmet one; four hypotheses (last book change / last trade / settlement-close / representation generation) all remain open, and five freshness predicates were rejected. | OPEN | Freshness cannot be established, so no quote can be qualified at decision time. | External (venue docs) — container egress **denies** `docs.polymarket.us` and `gateway.polymarket.us` | Blocked on venue documentation or an observed disambiguating example. The GitHub runner has egress and is the available route. |
| **C3** | ~~56 gate denials are a coverage gap.~~ **CLOSED this session.** Parsed from the JUnit XML, the 56 were **55 in `tests/test_pmus_post_only.py` + 1 in `test_mirror_live_worker.py`** — one module, not the capital path. | **I·T** | The module's two promises (byte-identical params dict; `post_only`-only refusal reading) were never reached. Now they are, with the gate deciding. | Engineering | **Done.** All 59 pass; 24 further tests prove every control still denies. |
| **C4** | **No cycle observed starting *and* finishing on the release SHA.** | DEPLOYED, not OPERATIONALLY VERIFIED | Without it, "deployed" cannot become "operationally verified" for any requirement. | Engineering | Hours once a release is cut with the worker build advanced past `f5d1c05` — which requires the protected-worker restriction to be lifted by the owner, so **not** independently actionable. |
| **C5** | **Credential rotation built, not executed.** `DESK_PASSWORD` + `SESSION_EPOCH` provisioning added to `command-verify.yml`'s existing `operator_action=rotate`. | IMPLEMENTED, not run | Until run, the Command Centre's protected routes are reachable only by an owner-configured secret, and sessions mintable under the removed default are not yet invalidated. | Owner (runs the authorized workflow) | Minutes, on owner action. The published default is already **removed from code** and missing config already **fails closed** (503). |
| **C6** | **Exposure and writer isolation: UNKNOWN.** Six modules can write `live_orders`; that is a writer inventory, not proof all six trade this account. | OPEN | Account-level reconciliation cannot be trusted across writers until the bindings are established. | Engineering + owner (account facts) | 2–3 days for the binding enumeration; the account facts are owner-supplied. Existing account stays paused. |

**C1 and C2 are the two that matter.** C3–C6 are tractable; C1 and C2 are where the launch actually is, and C2 is externally blocked.

### Correction to C1, made and verified this session: the 464 are **C3, not C4**

The previous census reported **C1 = 0, C4 = 464** — "unresolved for a reason none of the above names" — and that was read as an external blocker. It was our own instrumentation.

`classify_census` consumes **refusal code counts**, and the code every one of the 464 carries (`VOID_ABANDONMENT_RULE_NOT_ESTABLISHED`) does not record which side was silent. So all 464 collapsed into C4. But `bettor_settlement_terms.compare` has decided this per condition all along, between four verdicts that map exactly onto the four classes:

| `compare()` per-condition verdict | Class | Remedy |
|---|---|---|
| `V_MISMATCH` — both stated a rule, payouts differ | **C1** | None. Decisive against. |
| `V_VENUE_SILENT` — book stated one, venue did not | **C3** | Read the venue's publication. |
| `V_BOOK_SILENT` — venue stated one, book did not | **C2** | Capture the bookmaker's rule. |
| `V_BOTH_SILENT` — neither stated one | **C4** | The only genuine C4. |

`settlement_taxonomy.decompose_census` now reads the comparisons instead of the summary. Applied to the shape the register records for the 464 — **seven** applicable conditions for a baseball money line, the book side captured with citations, the venue's 380-character `description` stating **one** — the result is:

```
compare verdict:  UNKNOWN          unstated conditions: 6
candidate_class:  C3_VENUE_RULE_OR_SCOPE_NOT_HELD
established:      COMPLETED_IN_REGULATION
```

**464 candidates → C3 = 464, C4 = 0.** The remedy is capture of six named venue conditions, and the register already retracts the claim that the venue does not publish them: `docs.polymarket.us/sitemap.xml` answers 200 with **618 enumerated URLs** and `robots.txt` carries `ai-train=yes, search=yes, ai-input=yes`.

**Four things this does not establish**, stated in code and asserted by test:

1. It **admits nothing**. A C3 candidate is exactly as refused as a C4 one; the class names the remedy, not an outcome. `compare()` still returns `UNKNOWN`.
2. It does **not** predict how the comparison resolves once the venue side is read. It may resolve compatible, or resolve into a **C1 conflict** — which would be decisive against, and worse than the present state.
3. It lowers **no** qualification requirement. `silence_is_not_agreement` stays `True`.
4. It is derived from the shape the register **records** for the 464, not from a re-run against the live candidates. Re-running needs egress the container denies (`docs.polymarket.us`, `gateway.polymarket.us`), so this is `IMPLEMENTED·TESTED`, and the live re-run is the next step on the GitHub runner.

The 464's shape also says the capture is cheap: **six conditions, one sport family, one market type**, all named. That is a materially different work item from "the venue is incompatible".

---

## 4 · The consolidated register

`I` = implemented · `T` = tested · `D` = deployed · `OV` = operationally verified. Estimates are engineering days for work that is not externally blocked.

### Freshness, interpretation and qualification

| Req | Implementation | Integration point | State | Acceptance criteria | Evidence | Dependencies | Est |
|---|---|---|---|---|---|---|---|
| **A1** M1 freshness | `bettor_stream_currency`, `obs/streamstate.DepthAuthority` | decision-time quote qualification | I·T | P1–P6 all met on the live feed; a freshness predicate that survives adversarial review | `COMPLETION_REGISTER` §A1 — P1 NOT ESTABLISHED, P3 NOT AVAILABLE, **P5 the only unmet blocker** | **C2**, venue docs | Blocked |
| **A5** Valuation qualification | `bettor_source_calibration`, `bettor_pinnacle_devig` | `edge_gate` | I·T | prospective calibration evidence on unseen fixtures; a qualified source for ≥1 admissible market | register §A5 OPEN / external | **C1** | Blocked on C1 |
| **S-2** Decompose the 464 | `settlement_taxonomy.decompose_census` + `classify_comparison` | `bettor_settlement_terms.compare` output | **I·T** | every candidate in exactly one class with a named missing side — **no candidate reclassified by relaxing a requirement** | 24 tests; **C3 = 464, C4 = 0** on the recorded shape | — | **Done (I·T)** |
| **S-2b** Re-run the decomposition on live candidates | same | census job | OPEN | the live 464 decompose with the same mechanism, from a build with egress | — | GitHub-runner egress | 0.5 d |
| **S-2c** Capture the six silent venue conditions | `bettor_live_read.read_rules_text` | `compare`'s venue side | OPEN | all seven conditions stated by the venue, cited — **and the comparison may then return `INCOMPATIBLE`, which is a valid and worse outcome** | 618 sitemap URLs published, `ai-train=yes` | runner egress | **1–2 d** |
| **S-3** Investigate a compatible market/source pair | new | `bettor_universe` selection | OPEN | one fixture where book rules, venue rules and scope all hold, reached without weakening any gate | — | S-2c | 2 d after S-2c |

### Accounting, fees and reconciliation

| Req | Implementation | Integration point | State | Acceptance criteria | Evidence | Dependencies | Est |
|---|---|---|---|---|---|---|---|
| **A2/R1** Fee exactness | `bettor_fee_schedule`, `fee_consumers` — `Θ·C·p·(1−p)`, banker's rounding, per-fill cap with running cumulative cap, effective **2026-09-25T04:00:00Z** | every fill path | I·T | expected vs observed fee reconciles per fill on live data | register §A2, §R1 | live fills | Blocked on funding |
| **§6** One reconciled accounting model | `bettor_venue_position_model`, `bettor_merge`, `bettor_inventory` | account-level reconciliation across **every** writer | I·T | reconciles at account level across all writers; unknown marks render visibly provisional | `bettor_merge.DETERMINATIONS`; `doesNotLicense` keeps the traded leg | **C6** | 3 d after C6 |
| **§2** Case-study reproduction | **OPEN** — headline tables read, not independently reproduced | research only; never a funded-training label | OPEN | RN1 and Ferrari reproduced from raw manifests (36,707 / 24,179 logged requests) under FIFO **and** the moving-average sensitivity; named defects quarantined and quantified; the $14.1M vs $12.58M and $3.1M vs $1.02M leaderboard gaps explained without forcing unlike definitions to agree | study Sections 3, 4, 7 list every defect: 8+11 deficit conditions, 1 oversold, 1,873,605 tied timestamps, 5,739 tokens absent from position endpoints, 204 anomalous 10¢ fills | raw ledgers + `tests.py`/`run.py` from the study authors | **4–5 d** once the raw artefacts are supplied; **not** actionable without them |

### Decision, pairing and inventory

| Req | Implementation | Integration point | State | Acceptance criteria | Evidence | Dependencies | Est |
|---|---|---|---|---|---|---|---|
| **§3** Eight eligible actions, ranked | `bettor_ev_actions` (16 canonical actions), `bettor_decision_engine.decide` | the existing engine — no competing engine | **I·T** | all eight compared at each decision, ranked on forward economics | **`FORM_INDIRECT_HEDGE` added this session** — it was genuinely absent; `HEDGE` is the direct complement only | — | **Done (I·T)** |
| **§3** Sunk cost must not force a worse decision | `bettor_completion_policy.rank` | `decide` ranking | **I·T** | ranking invariant to basis; the accounting loss reported beside it, never as an input | 53 tests; ordering asserted invariant across basis $0.00–$5.00 | — | **Done (I·T)** |
| **§4** Completion forecast + one-leg-only branch | `bettor_completion_policy.initiation_value` | pre-entry gate | **I·T** | both branches priced; adverse selection, partial fills, capital charge and residual disposition all required, never defaulted | the directive's example reproduces exactly: −$0.15 / −$0.16 / −$0.25 | calibration of `p_completion` | **I·T; calibration open** |
| **§4** Every initiation enters the results | `bettor_completion_policy.Cohort` | evaluation harness | **I·T** | count identity enforced; `assert_identity` raises on any unaccounted initiation | test reproduces the Ferrari shape: +10.8 completed, −9.5 residual, net +1.3 | — | **Done (I·T)** |
| **§5** Indirect structures | `bettor_indirect_structures` | `FORM_INDIRECT_HEDGE`'s `STRUCTURE_ESTABLISHED` term | **I·T** | payoff table exhaustive over the outcome space incl. tie/push/void/postponement; refuses on unshared grading variable; no share in two structures | 53 tests; **Bears ML + Panthers +4.5 proved**: $2 only on margins 1–4, $1.50 on a tie, and the actual fixture landed outside the region | — | **Done (I·T)** |
| **§5** `P_MIDDLE_LANDS` | **OPEN** | the same action's second required term | OPEN | a calibrated middle-landing probability on unseen fixtures | both studies' middles averaged **above** $1 ($1.2024 / $1.1711) — above-par middles need this term or they carry a guaranteed shortfall | fixture data | 3 d |

### Execution, venue and lifecycle

| Req | Implementation | Integration point | State | Acceptance criteria | Evidence | Dependencies | Est |
|---|---|---|---|---|---|---|---|
| **§6** Complementary-acquisition semantics | `bettor_venue_position_model`, `live_executor.classify_exit` | order construction | I·T | economic interpretation *and* actual wire instruction both preserved | retail: one signed `netPosition`; complement acquisition **is** a sale of the first | — | **Done (I·T)** |
| **§6** Lifecycle completeness | `bettor_entry_execution`, `bettor_funded_execution`, `execution_gate` | submission path | I·T | durable intent, acks, rejections, partial fills, duplicate delivery, missed-fill recovery, unknown submission outcomes, reservations, restart recovery — all exercised | register §A3, §A9 | funded account | Blocked on funding |
| **§6** Servicing survives expired authorization | `bettor_mgmt_lifecycle` | scheduled servicing | I·T | ownership-bound servicing continues when entries stop | register §A3 proven against a **substituted transport** — labelled | live account | Blocked |
| **A7/R6** Reliability, rollback, emergency controls | `bettor_desk_controls`, `render-ops` | deploy route | I·T·D | monitoring + alert + incident path exercised on the serving SHA | register §A7, §R6 PARTIAL | **C4** | 2 d |

### Learning

| Req | Implementation | Integration point | State | Acceptance criteria | Evidence | Dependencies | Est |
|---|---|---|---|---|---|---|---|
| **§7** Decision-time feature ledger | `bettor_exit_dataset`, `bettor_p_fill_dataset` | decision loop | I·T | features, actions, predictions, chosen action, executable prices, fills, costs, inventory changes, outcomes all recorded at decision time, no lookahead | tasks L3/L4 pending | — | **3 d, actionable now** |
| **§7** Chronological evaluation + baselines | `bettor_exit_ml`, `learn_gate` | evaluation harness | I·T (partial) | unseen fixtures; one fixture's observations in one partition; compared against no-trade / hold / immediate-exit / conservative-pairing | register §R7 PARTIAL | §7 ledger | 3 d |
| **§7** Promotion controls | `bettor_model_inventory`, `shadow_experiment_registry` | model registry | **OPEN** | versioned evaluation, documented criteria, rollback, audit trail; **learning cannot change risk limits, eligibility, authorization or evidence requirements** | register: "no promotion path to trading authority, **by design**" | — | **4 d, actionable now** |
| **§7** No hypothetical fill counted as an execution | `bettor_shadow_execution` | shadow ledger | I·T | shadow and funded books never summed | register §A9 | — | **Done (I·T)** |

### Command Centre, credentials and capital readiness

| Req | Implementation | Integration point | State | Acceptance criteria | Evidence | Dependencies | Est |
|---|---|---|---|---|---|---|---|
| **A6** Published-default credential | `config.py` (both defaults removed), `app._signing_key` (503), `_session_epoch` | all protected routes | **I·T·D** | default fails; legitimate sign-in works; read/control/admin boundaries intact; sessions mintable under the default invalidated | 26 tests; `credential_posture()` returns no value, length or fingerprint | **C5** (run the rotation) | **Owner action** |
| **A6/R3** Credential consumers and scopes | `submission_surface` — 10 gates, 5 mutation names across 2 venues, 11 consumers, AST-parsed | provisioning | I·T | provisioning a key cannot enable another lane | — | — | **Done (I·T)** |
| **A6/R3** Read-only diagnostic interface | `bettor_read_only_venue.ReadOnlyVenue` | diagnostics | **I·T** | mutation names **absent**, not guarded; allowlist so a sixth mutation fails closed | 57 tests; `hasattr` False, lookup fails before arguments evaluate, `__slots__` blocks grafting | — | **Done (I·T)** |
| **A6/R3** Adopt it at each diagnostic call site | `adoption()` names the six modules | 6 diagnostic modules | OPEN | every diagnostic holds the constrained interface | `adoption()` reports each as CONSTRAINED / DIRECT_VENUE_MODULE / NO_VENUE_IMPORT — reported, not claimed | — | 1–2 d |
| **§8** Command Centre panels | `bettor_command_center`, `bettor_command_view` | the published page | I·T·D (partial) | strategy/model version, serving build, scheduler health, opportunities + refusal reasons, orders/acks/fills/unresolved, direct pairs / indirect structures / unpaired inventory, gross pairing gains / pairing losses / residual results / net portfolio P&L, expected vs observed fees, account exposure/limits/authorization, learning results/promotions/rollbacks — research, demonstration, shadow and funded **separate and unsummed** | register §A6, §A8 | the new `§5`/`§4` modules supply the pairing panels | **3 d, actionable now** |
| **§8** No capacity extrapolation | `bettor_capacity_fingerprint`, `bettor_capacity_harness` | capacity claims | I·T | a database microbenchmark is never presented as trading capacity; turnover never forced to a target | register §A8 | — | **Done (I·T)** |
| **§9** Release on a frozen exact SHA | `ops/gate/frozen_gate.sh` — JUnit XML, per-run pip-freeze manifest | release route | I·T | failure **identities** compared against a same-SHA control; isolated passes never used to explain full-suite failures | **gate noise floor ≥ ±1 identity per run**, measured; valid baseline **152** (the 177 was invalid — no pycryptodome) | — | **Done (I·T)** |
| **§9** Gate denials classified and repaired | `tests/gate_harness.py` — real loop on a background thread, pool double, `read_state`/`_decide` unmodified | `tests/test_pmus_post_only.py` | **I·T** | the tests cover the params dict, with the gate deciding; every control still denies | 152 classified: 56 gate-denial (**now 1**), 48 AssertionError, 34 RuntimeError, 9 ValueError, 4 other. Harness proven not to weaken the gate: 24 tests, incl. the 2026-09-21 truthiness defect | — | **Done (I·T)** |
| **§9** One unresolved failure | — | — | **EXPLICITLY UNRESOLVED** | attributed or repaired | `test_after_a_cover_fill…` `assert 'closed' == 'live'` reproduces **above** the noise floor | — | 1 d |
| **§10** Approval package | this document + `LAUNCH_DECISION_2026-09-27.md` | owner review | In progress | concrete account, permissions, typed limits, scope, remaining risks | — | C1, C5, C6 | On C1 |

---

## 5 · Delivered this session

- `backend/sportsassets/bettor_indirect_structures.py` + 53 tests — §5 complete as implemented-and-tested, including the Bears/Panthers proof from the located contracts.
- `backend/sportsassets/bettor_completion_policy.py` + 53 tests — §4's deterministic example, the sunk-cost invariance proof, and cohort accounting over every initiation.
- `FORM_INDIRECT_HEDGE` added to the single canonical action table with its exposure effect on both axes — §3's eighth action, which had no representation anywhere.
- `settlement_taxonomy.decompose_census` / `classify_comparison` + 24 tests — §4's four-way split, computed from the comparisons rather than from refusal-code names. **The 464 move from C4 to C3**, which names the remedy and shrinks the work item, while admitting nothing.
- 130 tests over the action table, EV bridge, risk engine, exit engine, capital allocator and pair engine re-run and still passing.

---

## 6 · The four verdicts, separate

1. **Engineering verified:** **NO, in part.** §3's action table, §4's completion policy and §5's indirect structures are IMPLEMENTED and TESTED. Nothing in this batch is DEPLOYED or OPERATIONALLY VERIFIED. 152 standing gate failures remain, 56 of them a coverage gap on the capital path, and one failure is explicitly unresolved.
2. **Ready for an authorized funded pilot:** **NO.** C1 (0 of 464 candidates admissible) and C2 (`transactTime` unresolved) both block it, and C2 is externally blocked on venue documentation this container cannot reach.
3. **Funded execution and reconciliation verified:** **NO.** Zero autonomous orders have ever been placed. Exposure and writer isolation are **UNKNOWN**. The existing account stays paused.
4. **Profitability supported by prospective results:** **NO.** Every profit figure in this document is a historical measurement of two other accounts on a different venue, under a lot convention whose sensitivity case moves it. No prospective result exists. Successful imitation would not establish positive EV, and the studies' own residual terms show the pairing machinery can be excellent while the strategy loses money.

---

## 7 · What I am not claiming

- Not that the corrections applied are the requirements completed.
- Not that a serving health endpoint is a cycle.
- Not that the case studies' merge economics transfer to our venue — retail nets instead, and institutional is `NOT_IDENTIFIED`, which is not `NO`.
- Not that `MIDDLE` means hedged: both studies' middle books averaged above par.
- Not that the 464 settlement refusals are proven incompatibility. **C1 = 0.** They are a venue-side capture gap (C3), which is a different thing.
- Not that reclassifying them to C3 improves the launch position. Zero candidates are still admissible, and capturing the six conditions could resolve them into C1 conflicts, which would be worse.
