# Market coverage and cross-venue comparison — 2026-10-02

## Status

This package contains implemented, locally tested code. It is NOT deployed.
GitHub branch creation returned HTTP 403 (Resource not accessible by integration).
Direct git fetch is unavailable in the Codex container. The patch is isolated
from your gates. Apply to a NEW integration branch on your newest accepted line.

The changed census file was fetched from candidate 53c4767, blob
270a1c6f3f0092cb4ab569e4de2ba573d958d5e3. The same file served in 102572f.
The local repository has older unrelated files; this package includes only
the explicit changes below, not that older repository tree. Do not deploy the
Codex worktree wholesale. Preserve all your newer commits.

## Apply

1. Freeze your current release/gate; create a separate worktree and branch.
2. Run `git apply --check market-coverage.patch` then `git apply market-coverage.patch`.
3. Review any context conflict rather than dropping it. No credentials are in the package.
4. Run:

```
PYTHONPATH=backend python -m unittest discover -s backend/tests -p test_market_coverage_v1.py -v
PYTHONPATH=backend pytest -q backend/tests/test_market_coverage_v1.py backend/tests/test_pinnapi_feed_runtime.py
```

5. Run your real-Postgres ownership/census suites and normal release gate on the
exact candidate SHA. Add the new coverage tests to the critical list in your
current branch; the package intentionally does not overwrite that moving list.
6. Deploy only after acceptance; read the current feed census again. Report
event identity, family classification, current price eligibility, and actual
decision consumption as separate stages. MATCHED_SUPPORTED is not permission
to trade. All trade controls, sizing and venue adapters are unchanged here.

## Implemented

### Census repair (wired into existing heartbeat code through census API)

- Reads venue team_name/team_id/team_league rather than parsing display titles.
- Matches two complete participant names plus finite start times, independent
  of home/away display order. No substring aliases or team nickname guesses.
- Rejects conflicting league/start records and ambiguous meetings.
- Uses explicit sports_type, keeping player props, partial-game winners,
  spreads, totals and unrecognized types separate from full-game winners.
- Reuses the existing side-aware premap clock-artifact algorithm to recognize
  the observed `line=00` derived from `5:00 AM`, without erasing genuine lines.
  A dependency-free copy is AST-pinned to premap. It adds no venue network call.
- Never claims census identity proves settlement or executable coverage.

### PinnAPI REST inventory (implemented, not yet wired to ingestion)

`pinnapi_market_inventory.inventory` enumerates moneyline (including draw),
spreads, totals and team totals across provider periods and pregame/live event
types, retains parent relationships and exact decimal lines, and records named
gaps for special/unknown structures. This is REST decimal odds, not the raw
WebSocket American-odds parser. Neither response.last nor event.last is accepted
as a price-change timestamp. Every output remains unqualified for decisions.

### Venue/management comparison (implemented, not yet wired to agents)

`market_comparison.compare_entries` ranks equal-quantity executable buys by
full-depth cost plus an explicit venue fee function. It requires identical
canonical identity/period/side/line and a reviewed settlement-equivalence class,
fresh nonfuture book evidence, open status and ready venue/account. Duplicate
snapshots, unavailable fees, unproven rebates and insufficient depth refuse.
Tests show either Polymarket US or Kalshi winning on economics, including when
fees reverse the apparent advantage. No venue is preferred by a fixed rule.

`compare_management_actions` compares HOLD, own-venue exit, direct pair and
indirect pair proposals using a common audited scenario payoff matrix and
net incremental cash. Unknown probabilities never become zero and produce no
EV ranking. Exceptional outcomes must be represented. Buying on Kalshi does
not close a Polymarket holding. This calculator does NOT certify the supplied
scenario domain or grant execution authority.

## Validation performed

- 24 new stdlib behavioral tests passed, with no DB/network/order calls.
- Four existing pure census test functions passed after updating their fixtures
  to contain the structured participants and types now required. Executed their
  actual function bodies via AST because this container lacks pytest/asyncpg;
  this is not a claim that the full runtime/DB suite passed.
- Production read-only query confirmed the new us_premap columns exist.
- Observed two-MLB-event replay: 608 rows previously NO_FEED_EVENT become
  MATCHED_UNSUPPORTED_FAMILY. This fixes identity; it does NOT create 608
  supported trades. These fixtures' captured rows are other market families.
- Observed KBO moneyline replay: two rows previously classified as line
  markets become full-game moneyline-family matches after the existing clock
  proof. Settlement and current quote eligibility remain unproven.
- Replays combine separately captured catalogue and heartbeat samples, with
  synthetic provider IDs; they are not a simultaneous production snapshot.

## Work still required to satisfy the owner's full request

1. Bind actual PinnAPI prices into Derek/Xavier (C2) using current feed owner
   authority and verified per-price timestamps. The serving feed is still
   observe-only in the directly verified 102572f readback. Preserve independent
   second-book rules; replacing Pinnacle does not add a new independent book.
2. Prioritize/retry venue books within freshness budgets; record each missed
   attempt and before/after timeout rates. This package does not change scheduling.
3. Build reviewed source adapters into Contract/Quote: exact source IDs,
   competition, meeting/game identity, selected side, signed line, period, live
   status, fees/depth, and published settlement evidence. Do not turn titles
   or similar payouts on one example into proof. Keep ambiguity visible.
4. Verify spread sign, pushes, Asian quarter lines/partial payouts, soccer draw,
   regulation/OT, innings/pitchers, suspended/postponed games and venue-specific
   resolution separately. Unknown families/specials stay in the coverage queue.
5. Wire both venue books to the comparator and persist every alternative and
   reason. Xavier's cross-venue proposals need joint payoff validation across
   ordinary and exceptional outcomes, capital/margin constraints, fill risk,
   reconciliation and instrument-specific costs. No assumption of atomic fills.
6. Owner identifies the existing institutional Kalshi account as the tiny-test
   account. Verify its identity read-only using existing server-side credentials.
   Do not reuse unrelated institutional positions/orders as test inventory.
   This package creates no credentials, orders, cancels or live activation.
7. Preserve ~$1,000 average INITIAL PAPER sizing. For the 1:1000 real test,
   calculate from actual instrument quantity/payout units; persist intended
   scale, venue minimums, rounding, submitted size, actual fills and exceptions
   separately. Never increase real risk just to pass a venue minimum silently.
8. Publish distinct first-filled contract+side positions and fixtures, re-entries,
   family/sport/live coverage, stale/read failure denominators and latency.
   Match income/cash/exposure per venue and an aggregate view without treating
   collateral in separate accounts as transferable in the decision instant.

## Verified documentation / evidence sources

- https://pinnapi.com/llms-full.txt (read 2026-10-02); user also supplied the
  current docs explaining the two different `last` fields.
- https://docs.polymarket.us/api-reference/markets/get-markets
  Structured marketSides/team records, sportsMarketType, line and description.
- https://docs.kalshi.com/api-reference/market/get-market
  Exact ticker/event, rules_primary/rules_secondary, strikes and status; do not
  guess outcome orientation from city-only titles.
- Production readbacks at 19:23–19:45Z through authorized Render read-only access.
- User's 15:16 screenshot contains a secret: do NOT add it to source/evidence.

## Completion receipt required

Exact serving SHA; fresh per-venue coverage denominator; distinct input→decision
IDs consuming PinnAPI; normal collector→paper order→simulator fill→Xavier→Audrey
evidence; measured fee/depth-aware venue ranking and rejected alternatives;
real mirror outcomes reported separately. Do not claim all-market coverage or
live execution from these pure tests or a connected stream.
