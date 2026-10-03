# Role evidence upgrade — independent of the reactive release

Apply the enclosed single patch on an isolated descendant of your newest
accepted candidate. DO NOT restart candidate 14's gate to include this research
upgrade. The capability_tools.py and capability_runtime.py baselines were read
from claude/cand14-reactive before editing (blobs 13849e4e7331dfea84418370106463eddd9bda22
and 1e50154814de9c1f4987ec840231a5aa05a21d65).
Do not cherry-pick Codex's baseline-import history.

## Delivered behavior

- New allowlisted read-only tool `role_brief`, scoped to the existing paper main
  account and research worker's 2-second read-only transaction/time budget.
- Derek: recent refusals ranked by DISTINCT contract/side within each
  strategy/version; repeated valuations do not appear as extra opportunities.
  ENTER is explicitly a signal, not an accepted order, fill or new position.
- Xavier: newest review per group within the sample, with stale/fresh-at-review/
  unknown explicitly separated. Groups are not claimed to be currently open.
  His existing position tool now includes actual recorded alternatives,
  confirmed protection and incomplete-search evidence alongside the action.
- Audrey: recorded severity/kind and source finding IDs, explicitly not claims
  of resolved defects or improved profitability.
- General research uses account + lessons + role brief + feed coverage (four
  facts). Contextual position/decision reads retain priority. Research questions
  now include the agent's specific analytical objective.
- No new model, connector, network request, trading action, policy activation,
  Slack sender, autonomous code execution or expanded permission is added.
  Existing persisted research and Slack delivery workflows remain responsible
  for recording and delivering generated reviews.

## Evidence and boundaries

Nine local behavioral/dispatch tests pass. Actual toolbox read-only transaction
and account bindings are exercised with a fake DB connection; actual research
request selection is exercised, without model calls. The three SQL selectors
were executed read-only against production at 2026-10-02 23:14:13Z; all returned
101 bounded rows, which correctly means the 100-row summaries will be labelled
TRUNCATED, not population totals. All requested columns also exist in production.
This is not a full Postgres release gate or demonstrated improvement in results.

Sample window is the last 24 hours, newest 100 records plus one truncation
sentinel. This is deliberately an inexpensive research orientation tool, not
an all-market opportunity census or a statistical model evaluation. Cohorts
stay separate; a contract may occur under several refusal reasons.

## Integration acceptance

1. Apply/port the patch preserving your newest capability fixes. Add
   tests/test_agent_role_briefs.py to the required tests. Extend any explicit
   tool-name contract to include role_brief; never relax arbitrary tool access.
2. Run capability/agent chat/work/Slack/isolation tests and the normal release
   gate on the exact candidate, in a fresh database. Check that the added Xavier
   review fields fit the existing 40k evidence ceiling with production-shaped
   records. If not, return an explicit bounded latest-review view, not silently
   omitted alternatives.
3. After accepted deployment, run an existing authorized general research task
   for each agent. Confirm the saved investigation actually includes role_brief,
   and the persona supplied facts cite its source IDs and truncation status.
   For Xavier, request an existing position and verify recorded alternatives
   and protection status reach the facts. Do not create a synthetic trade.
4. Show the genuine research result/peer review and existing Slack delivery IDs.
   No new review or conversation establishes performance improvement: require
   a separately frozen, evaluated forward proposal for any change.

## Next useful capabilities (not implemented by this patch)

- A distinct-contract opportunity-loss census joining discovery, matching,
  freshness, executable books, decisions and fills, so missing-before-decision
  markets are visible as well as recorded refusals.
- Account-aware cross-venue action comparison with exact settlement/payoff
  equivalence, fees, executable depth and independent leg-fill risk; never
  claim a hedge simply because team names match.
- Forecast/outcome cohorts for calibration, proper scoring and versioned
  forward policy evaluation, with explicit training/evaluation separation.
- A connector inventory with purpose, entitlement, data freshness, useful
  coverage and accountable tool owners. Add connectors only for a demonstrated
  evidence gap, never let agents grant themselves credentials or trading rights.
