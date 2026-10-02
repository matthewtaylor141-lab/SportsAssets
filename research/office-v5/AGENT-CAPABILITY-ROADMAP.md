# Bettor agent capability upgrade

## Included in this patch: usable, inspectable memory

The existing code already derives forward-record lessons, records improvement
proposals, evaluates them and supports bounded parameter activation. Do not build
another independent learning loop or silently change its authority.

This patch connects improved retrieval to the existing `persona_facts.gather`
path used by persona chat. It provides:

- Account and agent isolation. The previous lesson query filtered only by agent.
- The latest version per lesson series; older versions cannot defeat corrections.
- At most 48 candidates, three selected lessons and 1,200 characters per lesson.
- A two-second query deadline; failure is reported as missing evidence.
- Question relevance, source record counts, provenance fingerprints, evidence
  categories and dates. Future-dated or unsupported lessons are excluded.
- Historical labels after 14 days, which indicate revalidation rather than
  silently turning an old observation into a current rule.
- In-answer disclosure of which lessons were supplied. A lesson remains an
  observation, never an instruction, policy change or demonstrated profitability.

No retraining, policy activation, new trade, Slack send, or deployment is performed
by this patch. The main paper account remains server-selected. This is not a
multi-tenant authorization layer. The memory metadata must not be used to enlarge
an agent's trading permissions.

## Next capabilities, in delivery order

| Capability | Concrete behavior | Completion evidence |
|---|---|---|
| Persistent work plans | Each agent tracks goals, priorities, due times, dependencies and a named next action in existing task records. Work survives process restarts. | Restart during a claimed task; one completion, no duplicate action, overdue work visible. |
| Event-driven collaboration | Derek's fill/handoff triggers Xavier's review and Audrey's chain audit. Findings create bounded review cases with genuine responses; system acknowledgements remain distinct. | One production record chain with owner, deadline, genuine response and resolution IDs; duplicate delivery yields no duplicate work. |
| Shared evidence tools | Typed, read-only tools for account snapshots, positions/orders, current market state, settlement rules, prior decisions, lessons and research. Calls carry account, source time, request ID and bounded cost. | Tool results agree with the same database snapshot; unsupported or stale evidence is explicit. |
| Active investigation | Missing outcomes, mapping gaps and stale quotes create prioritized research tasks, ranked by affected exposure and missed evaluations. Agents may investigate within their budgets. | A diagnosed incident is fixed or assigned, and its measured recurrence changes. No invented completion. |
| Forward evaluation lab | Register hypothesis, baseline, cohort, evaluation window, costs, sample requirement and promotion criterion before observing evaluation outcomes. Compare against the existing policy without changing the live ledger. | Frozen evaluation specification and future-only outcome report, with uncertainty, failures and exclusions. |
| Probability calibration | Measure reliability by sport, market, phase and model version; track Brier/log loss and drift alongside economics and fill quality. | Untouched forward cohort with reproducible scoring. Conversation fluency is not probability-model accuracy. |
| Counterfactual management | Compare Xavier's chosen action with feasible alternatives using the contemporaneous book, fees and available quantity; keep counterfactuals separate from executed P&L. | Report cannot use later prices for an earlier decision or assume an unobserved fill. |
| Bounded adaptation | Automatically refresh evidence and proposals. Activate only parameters and scopes permitted by the existing control/approval system, with reversible versions and degradation triggers. | Old/new decisions retain their versions; rollback preserves controls, money history and attribution. |
| Management memory | Convert a management instruction into a proposed, scoped objective with provenance and authority review. Keep personal chat separate from approved operating objectives. | Agent explains which objective it is following and links the source; messages cannot override trading controls. |
| Capability scorecards | Show coverage, stale/missed evaluations, execution quality, review latency, unresolved findings, forecast calibration and measured improvements by version. | Every figure has a denominator, window, source and uncertainty; no fill-count target is treated as proof of intelligence. |

## Role-specific priorities

**Derek:** broader correctly mapped live and prematch market coverage; fresh
multi-book probabilities; fee/depth-aware selection; attribution of no-entry
reasons; calibrated forecasts. The PinnAPI connection alone is not full coverage.
200–500 positions/day is a throughput objective, not permission to bypass economics.

**Xavier:** freshness-aware alternatives, verified standing orders and reservations,
scenario exposure, settlement handling, review deadlines and execution quality.
Do not allow stale reference probabilities to rank a discretionary sale.

**Audrey:** independent ledger/fee checks, genuine response tracking, missing-link
and drift detection, prospective experiments, and measured follow-up. Distinguish
observation, hypothesis, implementation and demonstrated improvement.

## Integration boundaries

Reuse the existing task, case, provenance and proposal tables before adding new
schema. Pin tool permissions to each agent. Untrusted Slack, venue and research
text is evidence, not executable instructions. Bound loops, retries, context and
model spend. Share typed evidence records rather than unrestricted bot chatter.
Read-only management interfaces may propose work; they do not grant execution.

Do not claim Slack is connected because the connector can read a workspace. Verify
installed bot identities, receiver signatures, agent routing and actual delivery
separately. This package does not create apps or credentials.
