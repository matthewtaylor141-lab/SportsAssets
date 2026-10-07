CLAUDE — PROFITABILITY STACK V1 INTEGRATION DIRECTIVE

This package contains five independent research/PAPER-SHADOW workstreams:
1. Execution Truth Lab
2. Structural Arb Graph
3. Digital Twin Exchange
4. Profitability Governor
5. P&L Attribution

DO NOT MERGE THIS DIRECTLY INTO THE ACTIVE CLOSEOUT RELEASE.

Create a separate branch/worktree such as:
  claude/profitability-stack

and vendor the package under:
  research/profitability_stack/

NON-NEGOTIABLE:
- no live order authority
- no cancel authority
- no credential changes
- no historical PAPER rewrites
- SMALL LIVE remains SHADOW
- no risk/capital limit increases
- no weakening freshness, settlement, profitability, or protection gates
- no fixed daily turnover requirement
- no model may graduate because it is merely least negative

PHASE A — IMPORT + TEST
Run the package unchanged first.
Post:
- new research SHA
- exact files imported
- package tests/pass count
- dependency receipt

PHASE B — BETTOR READ-ONLY ADAPTERS

EXECUTION TRUTH
Build immutable adapters for:
- venue × sport × family × price band × game regime
- fill / non-fill
- queue/depth
- actual fees/rebates
- subsequent marks 100ms / 500ms / 1s / 5s
- cancel/replace timing
- maker conditional adverse selection
- taker slippage
- edge decay
Required output:
MAKE / TAKE / WAIT / REFUSE economics with conservative lower bound.

STRUCTURAL ARB
Build payoff vectors only from canonical settlement identity and explicit rules.
Scan:
- cross-venue complements
- exhaustive baskets
- implication bounds
- generic state-contingent superhedges
Required output:
all-in cost, guaranteed payout, guaranteed profit, executable depth,
settlement states, and exact rule provenance.
No label-based equivalence.

DIGITAL TWIN
Feed historical events in strict event-time order.
Wrap actual BETTOR production decision functions where possible.
Model:
- book updates
- trades
- queue
- partial fills
- submit latency
- cancel latency
- stale cancel risk
- settlement
No future event may be visible to a decision.

PROFITABILITY GOVERNOR
Use independent canonical event/settlement identity as the statistical unit.
Never treat repeated evaluations of one event as independent.
Required:
- unique independent event count
- decision-row count separately
- forward-only time-uniform lower/upper bounds
- calibration gate
- execution gate
- settlement gate
- capacity gate
- CASH fallback

P&L ATTRIBUTION
For every settled forward PAPER/SHADOW decision report:
- expected profit
- realized profit
- outcome variance
- execution residual
- management residual
- settlement residual
- reconciliation error
Aggregate by:
sport × family × regime × venue × strategy.

PHASE C — IMMUTABLE RECEIPTS
Every methodology version gets:
- code SHA
- dataset SHA
- receipt SHA
- extraction/query SHA
- time window
- unique event count
- decision count
- exact assumptions
Never overwrite an older CASH/negative receipt.

PHASE D — ACCEPTANCE
No PAPER integration until the research branch produces:
- real-data Execution Truth receipt
- real-data Structural Arb census
- no-lookahead Digital Twin receipt
- Governor status by strategy/family/regime
- reconciled P&L Attribution receipt

If no positive lower-bound executable economics exist:
  VERDICT = CASH

Do not tune thresholds to force a positive result.

NEXT ACCEPTABLE CHECKPOINT:
1. separate research SHA
2. all package tests green
3. read-only BETTOR adapters SHA
4. first real-data receipt for all five modules
5. exact CASH / SHADOW / challenger reasons
6. no production activation
