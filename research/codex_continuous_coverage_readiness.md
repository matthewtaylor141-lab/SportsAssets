# Continuous coverage and in-play delivery brief

Audited 2026-10-01 against SportsAssets `5a00cd30` plus presentation-only Codex changes. This is a code and provider-documentation audit, not a new production deployment. Claude remains the integration/release owner.

## Required outcome

Derek should continuously evaluate the complete **available, entitled venue catalogue**, pregame and in-play, including moneyline, totals, spreads, alternate spreads and team totals. Target internal evaluation within one second of receiving a relevant update, with a one-second sweep to detect missed updates, stale inputs and overdue work. Management should see actual coverage and latency, not infer them from a recently refreshed page.

The owner's volume target is **200–500 distinct newly filled position groups per day**. Report it separately from evaluations, orders, partial fills, reviews and correlated positions on the same fixture. It is a capacity target, not permission to manufacture favourable EV or count the same outcome as independent evidence.

## Concrete current constraints

| Code | Observed constraint |
|---|---|
| `workers/ext_pinnacle_loop.py` | `CYCLE_S = 900`, `MAX_PER_CYCLE = 40`, `MAX_METERED_SPORTS_PER_CYCLE = 4`; the odds request uses `markets=h2h`. A separate calibration lane also has a bounded allowance. This is not exhaustive one-second coverage. |
| Same collector | Reads `play_has_begun` and constructs event context. That supports phase-aware collection but does not prove a successful in-play order/fill path. |
| `agents/paper_benchmark.py` | Completed-game matching supports the implemented full-game baseball/soccer moneylines. Its baseball grading helper reads the pregame terms record. In-play terms must be checked explicitly, not inferred from that helper. |
| `agents/paper_explore.py` | Full-game moneylines only; $100 entry-cost ceiling including fees, $5,000 aggregate exposure, $1,000 cumulative realized-loss stop, fixture sampling and one exploration entry per fixture that is open **or has ever filled**. |
| `venue_pace.py` | Process-wide minimum request gap 0.35 seconds, with cooldown/backoff. Removing it to REST-poll every market once a second would not create a sustainable feed. |

At approximately $100 per position, $5,000 supports approximately 50 concurrent positions. Acquiring 200–500 per day at that size requires roughly $20,000–$50,000 of daily acquisition and 4–10 turns of that exposure budget. Smaller positions change this arithmetic; exits, liquidity, fees and unique fixture supply must be measured. Keep the existing controls until an explicit versioned capacity change has been reviewed.

## Feed capability is the first dependency

The Odds API's published intervals are 40 seconds in-play for main moneyline/spread/total markets, 60 seconds for additional/alternate/period markets, and 10 seconds for exchange data. Polling faster cannot make its reference observations one second old. Its v4 API supports live and upcoming events; this repository's `h2h` selection is a separate restriction.

Polymarket **US** documents authenticated market WebSockets for books and trades, with a maximum of 100 markets per subscription and multiple subscriptions available. Its trader-guide market-data page also describes a different HTTP-stream approach. Resolve the applicable protocol/version by a read-only connection using the existing entitled account before implementation. Do not substitute the international Polymarket API.

Inventory existing feed entitlements, supported market families, source timestamps, subscription limits, quotas and measured update intervals. Do not expose keys. If no existing reference feed meets the requested latency, identify the exact missing capability and a concrete provider/plan option; do not claim that a server upgrade solves it.

Sources checked 2026-10-01:

- https://the-odds-api.com/sports-odds-data/update-intervals.html
- https://the-odds-api.com/liveapi/guides/v4/index.html
- https://docs.polymarket.us/api-reference/websocket/markets
- https://docs.polymarket.us/trader-guide/market-data

## Implementation sequence

1. **Coverage census.** Maintain every current venue contract with exact event, participant, outcome, line, period, grading rules and game phase. Every contract has a named state: covered, unmatched, unsupported type, missing reference, stale, suspended or ended. Publish counts by sport, market family and pregame/in-play. A catalogue refresh can remain slower than price updates.
2. **Continuous input.** Subscribe to the venue's verified streaming protocol and the fastest entitled reference source. Timestamp source publication, local receipt, normalization, evaluation and order eligibility independently. Retain source timestamps on cache reuse. Handle duplicates, out-of-order updates, reconnects and sequence gaps according to the protocol. Rebuild a valid snapshot before using a book after a gap.
3. **Deterministic evaluation.** Feed changed contracts into a bounded queue; evaluate on relevant updates and sweep the cached universe each second. Use versioned numeric policy logic, not an LLM call per market per second. Measure p50/p95/p99 and worst receive-to-evaluate delay, backlog and skipped/coalesced work under the actual subscribed universe. A one-second internal target is distinct from upstream freshness. Never restamp old odds to satisfy the 30-second rule.
4. **Market-family expansion.** Add exact mapping and probability handling for spreads, totals, alternates and team totals. Prove line signs, team identity, period, overtime, draw, push/half-win/half-loss and exceptional settlement where applicable. A moneyline probability is not a spread or total probability. Keep unsupported contracts visible in the census with a reason until their mapping is proved.
5. **In-play lifecycle.** Carry observed score/clock/inning and event state. Revalidate after scores, suspensions, resumptions and market halts; reject invalidated or stale inputs. Prove entry after the observed start through the actual collector, decision, delayed simulator, shared ledger, Xavier handoff and Audrey audit. Do not use a pregame quote with a new receipt time.
6. **Safe throughput.** Key decisions to input revision and policy version while preserving order idempotency, account locking, fixture exposure ownership and budgets. Re-evaluation must not produce duplicate positions. Allocate requests among new entries and existing-position management so expanding Derek's coverage cannot starve Xavier. Preserve conservative fills, fees and visible training labels.
7. **Management view.** Push changes to the pages with a bounded polling fallback. Show catalogue coverage, quote source age, local pipeline delay, last evaluation, last agent pass and last ledger activity separately. Show pregame/in-play and market type per position. Keep old records on a failed read, but mark them stale. The Codex office patch advances displayed ages every second; it does **not** make new odds arrive every second.
8. **Learning and storage.** Audit missed/stale evaluations as well as executed positions. Keep fixture-level cohorts and separate training/exploration from investment results. Persist the exact inputs for every decision/order, but bound redundant raw-tick retention and monitor growth; the prior disk incident must not recur through unbounded per-second writes. Apply lessons only through the established versioned evaluation and activation controls.

## Release evidence

- Actual catalogue total reconciles with covered and blocked states; no silent 40-contract truncation presented as full coverage.
- Measured internal latency and source freshness under load, reconnect, rate limit and suspension scenarios.
- A genuine in-play simulated fill trace, plus one proved lifecycle for each newly enabled market family. If there is no qualifying live event, keep that item pending; fixture proofs are not live evidence.
- Distinct positions/day, unique fixtures/day, open exposure, net fees, fills and exits reported independently; no guarantee of 200–500 qualifying opportunities or of never missing one.
- Same-snapshot ledger reconciliation; no duplicate entries under concurrent input; protected workers unchanged, edge-shadow suspended, maker-entry state unchanged and real-money execution disabled.

Ship the presentation improvements independently after the normal gate. Do not hold them for this architecture work, and do not describe this brief as an implemented continuous engine.
