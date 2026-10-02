# All five agent capabilities — additive after office v5

The owner asked for all five capabilities, not another roadmap. These are
implemented and wired locally. This patch is separate from your running feed
and v5 gates. Apply it to the next isolated candidate after the accepted v5
changes; keep your newer feed, freshness, voice tests and critical-list entries.

## What is connected

1. **Persistent work plans.** Existing `agent_tasks`/`agent_task_events` store
   objectives, owners, priorities, due dates, dependencies, bounded attempts,
   claim tokens, leases, original investigation evidence and genuine replies.
   Management can assign a three-agent plan from each office. No new schema.
2. **Event-driven collaboration.** A separate API-lifecycle research worker
   admits real handoffs, open Audrey recommendations and settlements. A source
   record creates one linked three-agent plan. Each review requires its earlier
   peer reviews to have completed. The real persona service supplies the agent's
   existing logic, policy, memory and attributed peer opinions. A fallback,
   pending reply, demonstration or uncited answer cannot complete a review.
3. **Investigation toolkit.** Typed, account-scoped reads for balances,
   positions/orders/reviews/settlements, decisions, recorded rules, latest
   recorded books, recommendations, lessons, proposals and work. Every call is
   bounded. Missing/stale evidence is explicit; read time is not quote time.
   Audrey's operational findings become high-priority investigation cases.
4. **Forward-testing experiments.** A management form registers hypotheses and
   fixed training/evaluation windows in the EXISTING learning system. The
   existing evaluator and sample floors remain authoritative. The worker
   evaluates due registered protocols; insufficient outcomes remain insufficient.
   Existing paper-learning auto-proposals keep working. No automatic policy
   activation, threshold reduction, venue order or code modification is added.
5. **Capability scorecards.** Office views report decision/refusal cohorts by
   policy version, review freshness/missingness, first-review latency, audit
   findings, genuine reviews and overdue work. Denominators, sampling limits and
   windows are explicit. Experiments show their registered protocol and result.
   Forecast calibration is explicitly NOT_SCORED until a defensible frozen
   forecast/outcome cohort exists; operational activity is not intelligence or
   profitability. The complete capability read can be downloaded.

Ordinary persona chat receives up to five active assigned goals. Discussing a
work card attaches its exact task ID and reads only that agent's scoped task.

## Controls and operational bounds

The control key is `agent.capabilities.v1:paper_acct_main`. It defaults OFF.
After the exact candidate passes the release gate, deploy API only and enable
the research worker through its audited control endpoint. The owner authorized
this research capability; another broad permission request is unnecessary.

`POST /api/command/agents/capabilities/control`, existing CONTROL auth:

```json
{"actor":"Matt Taylor","enabled":true,"hourly_limit":24}
```

The same control is in Plans → Research schedule. Limits: 1–120 provider-request
claims/hour, default 24; at most two active claims across API processes; 300
nonterminal reviews; three execution attempts; 120s fenced leases; bounded
pending polls with backoff across the persona service's 300s recovery window.
One process executes one model call at a time. No database lock/connection is
held during a model call. Deployment overlap shares the durable claim budget.
Shutdown cancels the research worker before closing the pool.

This is additional research capacity, NOT a second trading scheduler. Existing
Xavier management and Audrey operational audits continue independently. The
research queue can lag a high-volume feed; 24 requests/hour is not a claim that
every fill receives an immediate three-model discussion. Admission-capacity
failures show DEGRADED in its heartbeat. Raising the request budget does not
increase trading authority. The official event/ledger record remains intact.

## Release checks — do not skip

The patch adds three files to `backend/tools/capital_critical_tests.txt`:
`test_agent_capabilities.py`, `test_agent_capability_routes.py`, and
`test_agent_capabilities_postgres.py`. Preserve your v5 memory-test entry and all
other newer critical entries if the list's end conflicts. The learning-boundary
allowlist adds exactly two research callers, not a wildcard.

Run all three against a fresh DB with migrations through 189 or your newer head.
**The two real-Postgres tests were not run in Codex's current environment.**
They must execute, not skip: concurrent admission/claim, cancellation, restart
fencing, original-evidence retention, one genuine completion and durable quota.
The bundled local receipt distinguishes these skips from passing contract tests.

Then run your exact-SHA full gate. Validate the lifecycle hook stays outside the
ext-writer/feed ownership path. Do not change protected workers, edge-shadow,
venue credentials, learning-activation permissions or real-money switches.

After deploy:

1. Read `GET /api/command/agents/capabilities` with COMMAND auth; reconcile its
   main account, control, heartbeat and task IDs with the same DB snapshot.
2. Enable research with CONTROL auth. Let a real handoff/recommendation admit a
   case. Read one complete three-agent review chain, cited record IDs, genuine
   persona message IDs and dependencies. A template ACK is not a response.
3. Reload during a review; prove one completion. Disable research; prove no new
   claim while already-running read-only work may finish. Re-enable afterward.
4. Check all three offices signed in, desktop + mobile. Inspect Plans,
   Collaboration, Investigate, Experiments and Scorecard. Save a real research
   goal; discuss it through chat. Confirm a failed read retains labelled stale
   data rather than zeroing it. Run your real Safari/voice checks separately.
5. Register a prospective protocol. Confirm no result before the fixed end or
   without the minimum outcomes; show no activation caused by registration.

The local `browser-check.cjs` uses explicitly synthetic records and proves UI
contracts, not production database/model availability. Re-render its three HTML
inputs from your candidate's `_cc_page_html` before rerunning. Do not publish
these synthetic test pages as the management site.

## Slack integration without a duplicate brain

`GET /api/command/agents/capabilities/events?after=<event_id>` is a COMMAND-auth,
read-only, cursor-based adapter of COMMITTED `GENUINE_REVIEW` events. It returns
the agent, task ID, message ID, evidence IDs, office URL and a stable delivery
key. Nothing in this patch sends a Slack message or handles a token.

Wire your existing installed-app receiver/outbox to these canonical events.
Apply your exact team/app/channel/principal allowlists. Persist an idempotent
delivery intent and its cursor transactionally, send under the correct installed
agent app, use plain-text blocks, and reconcile ambiguous Slack network outcomes.
Do not advance the cursor before the intent commits. Do not run the older
three-model Slack case worker again for a source now owned by capability work:
that would duplicate reviews. Slack bot messages never create a second case.
Keep management @mentions/DMs on the existing scoped, read-only persona adapter.

App installation, receiver availability and outbound Slack delivery were NOT
verified by this package. Preserve that distinction in the release receipt.

## Provenance and deployment status

Local base: office-v5 parent `74a3151c`, whose source snapshot was `fa41a91`.
Your v5 candidate is `b5670c3` on `7a896b0`; apply this delta after its gate, onto its accepted descendant. Do not apply v5 again. Use an additive integration. Do not
replace entire app/persona modules or overwrite the current learning bounds.
No production API, database, channel, environment or worker was changed here.
The GitHub connection previously rejected writes with HTTP 403, so this is a
tested local source handoff, not a claimed deployed release.
