# POS agents audit: EDDIE (Head of Execution) and SCOUT (Market Intelligence)

Branch `claude/pos-agents` (backend, migration 217), based on production SHA
`4717460d7d19acd43eb5e48593abe2cf658f3a62` (an ancestor of `origin/claude/cand24`).
The served frontend files (`/eddie`, `/scout` in the agent shell and their
Netlify rewrites) are also on `claude/ui-agents`, based on
`origin/claude/session-njaewf`.

Nothing here is gated, deployed or read back from production: those columns
are PENDING. Nothing is COMPLETE. Every number on a page comes from a
record; unmeasured is NULL / UNAVAILABLE with its reason.

## 1 · Checklist per agent

| Item | EDDIE | SCOUT |
|---|---|---|
| DESIGNED | Yes: shadow execution estimator, the hard rule, outcomes, scorecard, workflow host | Yes: compliant-source registry, feature registry, prospective tournament, scorecard |
| SCHEMA | Yes: `eddie_execution_estimates`, `eddie_execution_outcomes`, `pos_candidate_reviews`, `pos_candidate_review_steps`, view `pos_iface_eddie_execution` | Yes: `scout_sources`, `scout_features`, `scout_feature_observations`, `scout_feature_tournaments`, `scout_tournament_samples`, view `pos_iface_scout_feature_effects` |
| CODED | Yes: `agents/eddie.py`, `eddie_runner.py`, `pos_workflow.py`, `pos_authority.py` | Yes: `agents/scout.py`, `scout_runner.py`, `feature_tournament.py` (the evaluator, not Scout) |
| UNIT TESTED | Yes (pure estimator, hard rule x 24 combinations, outcome, scorecard) | Yes (compliance check, observation fields, challenger rule, scoring) |
| INTEGRATION TESTED | Yes, against a migrated PostgreSQL (runner pass, workflow, interface views) | Yes (registration, ingestion, frozen samples, outcomes, evaluator verdict, runner) |
| AUTHORITY TESTED | Yes: registry + code refusals; DB trigger on 36 approval/control/order/intent/fill tables, by actor column AND by session declaration; task events; loop | Same, plus: cannot record a verdict, set VALIDATED, ADOPT, or ingest a non-compliant source |
| ECONOMIC METRIC DEFINED | Yes: edge preservation, latencies, fill rate and calibration, slippage, spread captured, price improvement, adverse selection, execution alpha, capital-hours consumed / saved, incremental P&L vs naive (counterfactual, ex-ante executable-edge basis) | Yes: features proposed / validated, incremental Brier / log loss, incremental realized edge, lead time vs PinnAPI, stale / false information rate, adoption rate, economic contribution |
| SHADOW RUN | Locally only (test DB built from the c24t template: 25 estimates, 14 outcomes, 10 reviews). Not run in production. | Locally only (seeded TEST fixture rows). Not run in production. |
| EVAL | Predicted vs realized execution loss is recorded per fill; no forward sample in production | The tournament evaluator is built and tested; no forward sample has settled (0 / 200) |
| COMMAND CENTER | `/api/command/eddie`, `/api/command/agents/eddie[/page]`, index | `/api/command/scout`, `/api/command/agents/scout[/page]`, index |
| AGENT DESK | Yes: 3D character at an institutional execution desk; depth / analysis / capital screens drawn from the record | Yes: 3D character at a research desk; sports-feed / features / weather-data screens drawn from the record |
| SLACK | Built (dedicated app, distinct-token refusal, records-only answers, evidence-linked workroom posts); app NOT installed (one admin step) | Same |

## 2 · Capability matrix

Columns: CAPABILITY · OWNER · BRANCH · SHA · MIGRATION · DATA CONTRACT · AUTHORITY LEVEL · UNIT · INTEGRATION · CAPITAL-BOUNDARY · OOS · COUNTERFACTUAL · FAIL-CLOSED TEST · AUDREY AUDIT · KAREN REVIEW · COMMAND CENTER · 3D DESK · SLACK · EXACT-SHA GATE · DEPLOYMENT · PRODUCTION READBACK · FORWARD SAMPLE · ECONOMIC RESULT · STATUS · BLOCKER

SHA = HEAD of the branch named (see the delivery report).

| CAPABILITY | OWNER | BRANCH | SHA | MIGRATION | DATA CONTRACT | AUTHORITY LEVEL | UNIT | INTEGRATION | CAPITAL-BOUNDARY | OOS | COUNTERFACTUAL | FAIL-CLOSED TEST | AUDREY AUDIT | KAREN REVIEW | COMMAND CENTER | 3D DESK | SLACK | EXACT-SHA GATE | DEPLOYMENT | PRODUCTION READBACK | FORWARD SAMPLE | ECONOMIC RESULT | STATUS | BLOCKER |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Eddie identity + registry + deny list | EDDIE | claude/pos-agents | HEAD | 217 | `agent_identities` row EDDIE; `tool_permissions.authority_status=SHADOW_ONLY` | SHADOW_ONLY | PASS | PASS | PASS (trigger, 36 tables) | n/a | n/a | PASS (unknown agent refused; kill switch) | NONE (no Audrey audit of Eddie yet) | NONE (no Karen detector targets Eddie) | YES | YES | n/a | PENDING | PENDING | PENDING | n/a | n/a | SHADOW | deploy + gate |
| Eddie shadow execution estimate per Derek candidate | EDDIE | claude/pos-agents | HEAD | 217 | `eddie_execution_estimates` (theoretical edge, spread, fees, slippage, adverse selection, execution loss, net executable edge, fill probability, time to fill, capital-hours, max size, EV, recommendation, `unmeasured`, `dimensions`) | SHADOW_ONLY | PASS | PASS | PASS | n/a (estimates are ex ante) | n/a | PASS (no book / stale book / unmeasured inputs -> WAIT; never an execute) | NONE | NONE | YES | YES (analysis screen) | answers from records | PENDING | PENDING | PENDING | 0 production | NONE | SHADOW | adverse selection needs >= 20 recorded markouts before any net edge is measured |
| Eddie HARD RULE (no execute when expected executable EV <= 0) | EDDIE | claude/pos-agents | HEAD | 217 (`eddie_estimates_hard_rule_ck`) | CHECK + `enforce_hard_rule` + `record_estimate` refusal | SHADOW_ONLY | PASS (24 combinations) | PASS (DB refuses 4 forged rows) | PASS | n/a | n/a | PASS (unmeasured EV -> WAIT, never execute) | NONE | NONE | YES | YES | n/a | PENDING | PENDING | PENDING | n/a | n/a | SHADOW | none |
| Eddie predicted vs realized execution loss | EDDIE | claude/pos-agents | HEAD | 217 | `eddie_execution_outcomes` (PAPER source) | SHADOW_ONLY | PASS | PASS | PASS | n/a | YES (naive taker loss beside realized) | PASS | NONE | NONE | YES | YES (capital screen) | evidence-linked post | PENDING | PENDING | PENDING | 0 production | NONE | SHADOW | ACTUAL fills are not read yet (schema accepts source=ACTUAL) |
| Eddie scorecard | EDDIE | claude/pos-agents | HEAD | 217 | `GET /api/command/eddie` `.metrics.metrics.*` {value, numerator, denominator, status, why, definition} | SHADOW_ONLY | PASS | PASS | n/a | n/a | YES (incremental P&L vs naive, ex-ante basis) | PASS (null when unmeasured) | NONE | NONE | YES | YES (economic score) | yes | PENDING | PENDING | PENDING | 0 | NONE | SHADOW | submit->ack / ack->fill UNAVAILABLE (paper has no venue ack) |
| Candidate-review workflow (Derek -> Karen -> Scout -> Eddie -> Allocator -> Audrey -> Xavier) | EDDIE (host) | claude/pos-agents | HEAD | 217 | `pos_candidate_reviews`, `pos_candidate_review_steps` (question, agent, evidence_refs, response, disagreement, resolution, experiment_ref, result) | SHADOW (records only) | PASS | PASS | PASS (writes declared as EDDIE) | n/a | n/a | PASS (unverifiable evidence -> NO_RECORD) | reads Audrey's risk recompute | reads Karen's challenges | YES | n/a | evidence-linked post | PENDING | PENDING | PENDING | 0 | n/a | SHADOW | steps are assembled by POS_WORKFLOW from each agent's record, not answered live by each agent |
| Eddie loop findings (disagreement -> EVIDENCE + HYPOTHESIS, routed) | EDDIE | claude/pos-agents | HEAD | 203 + 217 | `agent_findings` proposer EDDIE | no release marks | PASS | PASS | PASS | n/a | n/a | PASS | not yet | not yet | YES (via loop) | n/a | n/a | PENDING | PENDING | PENDING | 0 | n/a | SHADOW | later stages need a peer |
| Scout identity + registry + deny list | SCOUT | claude/pos-agents | HEAD | 217 | `agent_identities` row SCOUT; `authority_status=RESEARCH_SHADOW_ONLY` | RESEARCH_SHADOW_ONLY | PASS | PASS | PASS | n/a | n/a | PASS | NONE | NONE | YES | YES | n/a | PENDING | PENDING | PENDING | n/a | n/a | RESEARCH | deploy + gate |
| Scout compliant-source framework | SCOUT | claude/pos-agents | HEAD | 217 | `scout_sources` (licensing class, usage terms, declared checks, compliance_passed); DB guard on features / observations | RESEARCH_SHADOW_ONLY | PASS | PASS | PASS | n/a | n/a | PASS (non-compliant source refused by the DB) | NONE | NONE | YES | YES (sports-feed screen) | n/a | PENDING | PENDING | PENDING | n/a | n/a | RESEARCH | MLB terms: legal review before any non-research use |
| Scout source: MLB Stats API schedule (existing `fixture_metadata`) | SCOUT | claude/pos-agents | HEAD | 217 | observations from `fixture_metadata` only (no network call) | RESEARCH_SHADOW_ONLY | PASS | PASS | PASS | n/a | n/a | PASS (no row -> nothing observed) | NONE | NONE | YES | YES | n/a | PENDING | PENDING | PENDING | 0 production | n/a | RESEARCH | `fixture_metadata` is empty in the test template; production row count unknown |
| Scout source: weather | SCOUT | claude/pos-agents | HEAD | 217 | declared and REFUSED (no licensed feed) | n/a | PASS | PASS | n/a | n/a | n/a | PASS | n/a | n/a | YES | YES (weather screen says NO LICENSED SOURCE) | n/a | PENDING | PENDING | PENDING | n/a | n/a | REJECTED | no licensing basis |
| Scout features (2, predeclared) | SCOUT | claude/pos-agents | HEAD | 217 | `scout_features` (source, feature, event scope, identity basis, expected mechanism, predeclared hypothesis, licensing, forward test, incremental value) | RESEARCH_SHADOW_ONLY | PASS | PASS | PASS (cannot VALIDATE / ADOPT own feature) | PENDING (0 / 200 settled) | n/a | PASS | NONE | NONE | YES | YES | evidence-linked post | PENDING | PENDING | PENDING | 0 / 200 | NONE | RESEARCH | forward sample |
| Feature tournament (PinnAPI vs PinnAPI + feature) | CALIBRATION_ENGINE (role) | claude/pos-agents | HEAD | 217 | `scout_feature_tournaments` (frozen spec), `scout_tournament_samples` (frozen predictions), verdict by the evaluator | evaluator only | PASS | PASS (end-to-end REJECTED) | PASS (verdict by SCOUT refused) | YES (predictions frozen after the freeze, before outcomes; feature values known before the prediction) | n/a | PASS (no verdict below min sample; VALIDATED needs the predeclared improvement) | NONE | NONE | YES | YES | yes | PENDING | PENDING | PENDING | 0 / 200 | NONE | RESEARCH | forward sample |
| Scout scorecard | SCOUT | claude/pos-agents | HEAD | 217 | `GET /api/command/scout` `.metrics.metrics.*` | RESEARCH_SHADOW_ONLY | PASS | PASS | n/a | YES | n/a | PASS | NONE | NONE | YES | YES | yes | PENDING | PENDING | PENDING | 0 | NONE | RESEARCH | lead time and false-information rate UNAVAILABLE (named reasons) |
| No-authority DB boundary (both) | EDDIE, SCOUT | claude/pos-agents | HEAD | 217 | `aa_pos_agents_no_authority_trg` on 36 tables; `pos_task_events_no_authority_trg`; loop actor CHECK | none | PASS | PASS | PASS | n/a | n/a | PASS (an ordinary session is untouched; column-less tables fixed) | NONE | NONE | YES (authority section) | YES (badge) | n/a | PENDING | PENDING | PENDING | n/a | n/a | READY_FOR_REVIEW | review of the trigger on hot order tables |
| Interface views for pos-twin / pos-learn | EDDIE, SCOUT | claude/pos-agents | HEAD | 217 | `pos_iface_eddie_execution`, `pos_iface_scout_feature_effects`; generated columns `fill_probability`, `execution_uncertainty` on `eddie_execution_estimates` | read only | n/a | PASS (columns pinned) | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | PENDING | PENDING | PENDING | n/a | n/a | READY_FOR_REVIEW | coordinator to confirm the column semantics |
| Personas + persona chat | EDDIE, SCOUT | claude/pos-agents | HEAD | 217 (persona CHECK) | `agent_persona_versions` rows; facts only from their own records | read only | PASS | PASS | PASS (refusal voice) | n/a | n/a | PASS | n/a | n/a | YES (Talk panel) | n/a | n/a | PENDING | PENDING | PENDING | n/a | n/a | SHADOW | LLM voice ids unset (browser fallback) |
| Role briefs | EDDIE, SCOUT | claude/pos-agents | HEAD | - | `role_brief.SQL/FOCUS` | read only | PASS | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | PENDING | PENDING | PENDING | n/a | n/a | SHADOW | none |
| Command Center pages + desks | EDDIE, SCOUT | claude/pos-agents (API page), claude/ui-agents (shell + rewrites) | HEAD | - | `GET /api/command/agents/{eddie,scout}/page`; `/eddie`, `/scout` | read only (no button on the desk) | PASS (node) | PASS (Chromium 1440 / 390) | PASS (no submit / trade affordance) | n/a | n/a | PASS (no record -> NOT MEASURED; stale -> UNAVAILABLE pose) | n/a | n/a | YES | YES | n/a | PENDING | PENDING | PENDING | n/a | n/a | READY_FOR_REVIEW | none |
| Slack dedicated apps | EDDIE, SCOUT | claude/pos-agents | HEAD | 217 (delivery CHECK) | `SLACK_{EDDIE,SCOUT}_{BOT_TOKEN,SIGNING_SECRET,APP_ID}`; manifests | read only | PASS | PASS (mocked transport) | PASS (distinct-token refusal) | n/a | n/a | PASS (not configured -> nothing sent) | n/a | n/a | n/a | n/a | BUILT, NOT INSTALLED | PENDING | PENDING | PENDING | n/a | n/a | BLOCKED | one admin install per app (research/eddie_scout_slack_setup.md) |

## 3 · Simplifications versus the brief (honest list)

1. **Adverse selection** is the mean recorded markout of paper fills (mid at
   the fill's book vs the first recorded book 30-300 s later), per style.
   With fewer than 20 markouts it is unmeasured, so the net executable edge
   is unmeasured and the recommendation is WAIT. In the test template there
   are 18, so every estimate there is WAIT. That is the rule working, not a
   result.
2. **Fill probability** is the recorded terminal fill rate of paper orders of
   the same style (MARKETABLE/IOC vs RESTING/GTD), not a calibrated per-order
   model; **time to fill** is the recorded median.
3. **Capital-hours** are *execution* capital-hours (capital reserved while the
   order works). Holding time to settlement is not linked
   (`capital_turnover` UNAVAILABLE).
4. **Maximum executable size** is computed with adverse selection 0 when that
   is unmeasured and labelled `UPPER_BOUND_EXCLUDES_UNMEASURED_ADVERSE_SELECTION`.
5. **Dimensions** not measurable from current data carry named UNAVAILABLE
   reasons: cancel/replace economics, market impact, time-to-event,
   capital turnover; price improvement only after a fill; live/pregame and
   venue tick only when the recorded book carries them.
6. **ACTUAL execution outcomes** are not read yet; the outcome table accepts
   `source='ACTUAL'`. **submit->ack** and **ack->fill** are UNAVAILABLE (the
   paper simulator has no venue ack).
7. **Incremental P&L vs naive** is a counterfactual on an ex-ante
   executable-edge basis (theoretical edge minus realized execution loss,
   times filled quantity, for filled candidates Eddie advised against), not
   settled P&L.
8. **The workflow** is assembled by `pos_workflow` (recorded_by
   POS_WORKFLOW, hosted by Eddie's runner) from each agent's own record; it
   does not ask each agent to answer live. A step without a record is
   NO_RECORD / NOT_APPLICABLE and cites the candidate. The Audrey step reads
   the latest book-level risk recompute (not per candidate).
9. **The evaluator** (`feature_tournament.evaluate`, identity
   CALIBRATION_ENGINE) is deterministic code run inside Scout's runner pass;
   independence is enforced by the database (no verdict by EDDIE / SCOUT, the
   spec frozen, VALIDATED only with the predeclared improvement), not by a
   separate process. "Cost / complexity / latency" is expressed as the
   predeclared minimum improvement (0.001 Brier) a feature must clear.
10. **Scout's one source** is the existing official MLB schedule path: Scout
    reads the already-ingested `fixture_metadata` table and makes no network
    call. MLB Advanced Media's terms restrict commercial redistribution; the
    classification is internal research only and needs a legal review before
    any other use. The weather source is declared and refused.
11. **Lead time before PinnAPI** and **false-information rate** are
    UNAVAILABLE with reasons (no line-move attribution; no correction feed).
12. **The session-declared boundary** (`bettor.acting_agent`) is set by Eddie's
    and Scout's own write transactions; a writer that did not declare itself
    is caught only by the actor-column checks. Their modules import no order,
    venue or funded path (AST-tested).
13. **The 3D characters** are original procedural rigs in the existing
    `cc_characters.js` (no external model); the desks are procedural
    geometry whose screens are canvas textures redrawn from the desk record.
14. **The page shell** for Eddie and Scout is the plain workspace shell (like
    Karen's), so inside the `/eddie` frame the page's own nav also shows.
15. **The persona Talk panel's** first-visit "no conversation yet" read uses
    an opt-in `?absent=empty` (HTTP 200, empty) on these two pages only; the
    default 404 is unchanged for the other agents.
16. Screenshots were taken against a local test database built from the
    c24t template (synthetic paper data) plus four rows labelled
    `TEST_FIXTURE_FOR_SCREENSHOT` in `fixture_metadata`; they are not
    production.
