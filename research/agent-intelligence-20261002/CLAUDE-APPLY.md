# Agent intelligence follow-up — 2026-10-02

Apply AFTER the reactive release is accepted, deployed, activated and evidenced,
and AFTER the Agent Role Briefs package. Do not restart candidate 15's gate.
This package is an additive research/UI patch, not an execution release.

## Delivered code

1. `intelligence_reports.py`: bounded last-24h collector-loss report, last-30d
   forecast evaluation, and connector-health/contribution reads.
2. `cross_venue_research.py`: normalized Polymarket US / Polymarket / Kalshi
   recorded-book action comparator. No order routing, credentials, URLs or
   new venue client. BUY elsewhere is a hedge, not a same-position exit.
3. Four allowlisted evidence tools on the EXISTING authenticated tools route.
   No new endpoint or extra write permission. Research timeouts, read-only
   transactions and 40k output limit stay in place.
4. Role briefs append a measured report (Derek: missed opportunities;
   Xavier: connector health; Audrey: forecast evaluation). Existing position
   evidence includes cross-venue comparison, or an explicit missing-data reason.
   The research worker's four-fact priority remains unchanged.
5. Intelligence tab, readable summaries, evidence detail and JSON downloads.
6. Tests and guard registrations. No arbitrary tool, SQL, connector install,
   model-choice, sizing, mirror or real-money change.

## Apply

Base dependency: original role-brief commit/package a5c3d986, already received.
The capabilities baseline was re-read at `claude/cand14-reactive`; tools file
13849e4e7331dfea84418370106463eddd9bda22 and panel
1c63849ba3bfcd5e997ca904198bea15f077b325 match the expected pre-brief baseline.

Make an isolated follow-up branch from the latest accepted release plus the
role-brief integration. `git apply --check agent-intelligence.patch`, review,
then apply. Preserve newer work. Do not cherry-pick the local snapshot history.
No migration is needed by these reads. The read-only existing guard registry
adds exactly two table references for the reports module, categorized as
research/reporting reads of both record purposes, never execution selectors.
The funded-import proof is strengthened to cover the two new modules.
The existing tab pin and old three-tool research fixture are updated explicitly.

## Required acceptance

Add `tests/test_agent_intelligence_reports.py` to the critical list. Keep
`tests/test_agent_role_briefs.py` required. Run the full capability, chat,
work, Slack, page transport, endpoint, isolation and calibration registry
suites in the gate environment on real Postgres, then the exact-SHA gate.
Local proof: 27 new deterministic tests plus 9 role-brief tests pass; these
are NOT a replacement for the full gate.

Run all four SQL selectors from intelligence_reports.py against a freshly
migrated gate database with production-shaped rows. Three selectors were
executed on production read-only at 23:31:48Z: opportunities and forecasts
both returned the 501-row truncation sentinel, contribution grouping one row.
The book-health selector also executed successfully: 8,758 ordinary reads and
452 shared reads at the later observation window. These are query-validation
observations, not a permanent baseline or an execution-success claim.

Verify live payload size when a role brief embeds the report and when Xavier's
position includes alternatives/protection plus the new comparison. A report
that exceeds the budget must be unavailable, not silently asserted complete.
Verify the saved investigation AND context_facts supplied to the model contain
source IDs, missingness, and sample truncation. Verify genuine research and
peer-review message IDs plus actual Slack delivery IDs separately.

Browser fixture test:

    python3 research/agent-intelligence-20261002/render_fixture.py
    node research/agent-intelligence-20261002/browser_acceptance.cjs

Requires Playwright + Chromium available to node. It checks desktop and phone,
report display/download, missing cross-venue data and HTTP-error behavior.
JavaScript syntax passed locally. Browser run NOT completed locally: no browser
binary, and the download proxy returned invalid archives. Run this plus signed-in
production checks; do not call browser acceptance passed based on syntax.

## Exact open integration gaps (do not hide)

### Cross-venue adapter

The comparator is implemented and tested, but NO live snapshot producer is
installed by this patch. The existing database has Kalshi claims/catalogue
records, not verified executable Kalshi depth evidence for these positions.
The cross_venue tool correctly returns NO_VERIFIED_CROSS_VENUE_SNAPSHOT until
an authenticated read-only adapter publishes the schema below. Do not seed a
synthetic snapshot in production or mark mappings verified by an LLM assertion.

The adapter must use existing venue client/account infrastructure, preserve
venue identity (Polymarket US and international are different), and persist
only allowlisted evidence under this exact account/group-scoped key:

    ingestion_state.key = agent.cross_venue_evidence:<account_id>:<group_id>

Required snapshot fields: schema=CROSS_VENUE_RESEARCH_V1, snapshot_id,
account_id, group_id, exhaustive_mutually_exclusive=true, scenario_evidence_id,
scenarios[{id,probability}], forecast_id, forecast_version, forecast_at,
held_payoff{scenario: USD settlement payoff}, target_quantity, actions[].

Each action needs action_id, venue, side (BUY/SELL), contract_id, book_id,
book_at, OPEN market_state, verified native mapping/rules with evidence IDs,
scenario_evidence_id, payoff_per_contract for EVERY scenario, rules_id,
mapping_evidence_id, fee_evidence_id, size_rules_id, quantity_step,
minimum_quantity, minimum_notional, and a monotonic executable ladder:
levels[{price,quantity,fee_upper_bound_per_contract}]. For BUY, include current
available_cash, balance_at, balance_evidence_id; for SELL, current available
quantity, inventory_at/evidence_id plus matching inventory venue/contract.
All money uses USD, quantity contracts. Compute real size-dependent venue fee
ceilings conservatively in the adapter; do not use a generic fixed fee guess.
Book, forecast and account snapshots must be at most 30 seconds old and never
future-stamped. Publication is not execution authorization. Research outcomes
are snapshotted by the existing task investigation; this latest cache key is
not an immutable order/fill audit log.

Adapter acceptance must use genuine PM/Kalshi books, explicit team/event/period/
line/outcome mapping, overtime/void/postponement/push handling, and a complete
mutually exclusive settlement state space. Evidence for unequal contracts
must supply each state payoff; matching names are insufficient. Include venue
minimums, lot rounding, available collateral/inventory, fee basis, partial-depth
results and the residual exposure if zero/some/all quantity fills. Preserve the
existing execution approval path. No atomic two-venue fill is assumed.

### Forecast interpretation

Scores are Brier loss, clipped log loss and calibration bins for persisted
source forecasts, separated by experiment/version/provider/devig method,
record purpose, sport, family, period and line. These are producer forecast
versions, NOT proof that an internal learned-model version improved. To score
new internal models, persist their immutable pre-outcome forecast and actual
model artifact/version, then adapt them into the same scorer with a frozen
forward cohort. Never relabel strategy versions as model versions.

One earliest forecast per event/contract/cohort WITHIN the bounded sample is
scored; newest 500 rows, up to 8 cohort summaries, omissions disclosed. No
population calibration, statistical superiority or P&L claim. Pending/future
outcomes stay pending, invalid chronology is excluded. Do not change policy
thresholds based on a small descriptive report. Use existing registered
forward experiments for activation evidence.

### Missed opportunities

The existing candidate ledger records provider event/family, not every market
on every venue. Only audited early-return reasons count as confirmed
pre-valuation losses. Generic REFUSED can still produce calibration records
and reach Derek; those remain unproven. DEFERRED is counted separately.
Latest state per event/family avoids counting every repeated cycle or a mapping
transition as a new lost opportunity. Unseen markets, queue losses not in the
ledger, lost dollar EV and complete universe coverage remain UNKNOWN. A future
exact attempt->valuation->decision join requires stable IDs in the collector;
do not fake it by joining loose timestamps.

### Connector completeness

PinnAPI heartbeat/cache/coverage, The Odds API credit counters, persisted primary
provider contributions and PMUS paper-book reads are real sources. Secondary
book contributions, Kalshi live-book health, model/voice API usage and invoice
costs are explicitly uninstrumented. Add those from existing authenticated
provider counters before claiming a complete paid-connector ROI dashboard.
Connection, messages, valuations, decisions, orders and fills are different
units. WebSocket value is not the number of REST calls.

## Release sequence

Reactive gate/release first. Deploy accepted code + migration while reactive is
OFF, verify readiness, THEN enable it and prove a real between-cycle decision.
Role briefs next. This follow-up after that, or integrate both research patches
into one separate candidate after reactive acceptance if still unapplied.
Do not touch protected workers, edge-shadow, real-money or mirror settings.
Report local, gated, deployed and exercised states separately.
