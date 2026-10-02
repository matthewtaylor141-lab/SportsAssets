# Explicit owner correction: around $1,000 per training entry

The owner's direct instruction supersedes Codex's $250 proposal:

> No I want the entry position to be around $1,000 per position

Apply this correction to the staged, undeployed Derek training V2 branch.
Do not restart the capabilities gate or modify its candidate. No further
confirmation of the $1,000 entry amount is needed: the owner supplied it.

## Scope

- New simulated exploration entries use a $1,000 budget including fees.
  Whole-contract sizing at the current best price targets that budget; it is
  reduced by displayed unconsumed depth or remaining aggregate headroom.
  Conservative simulator partial fills can be smaller. Do not fabricate depth,
  force a fill, or chase a worse price just to reach the target.
- The order reservation and fee-inclusive fill remain capped at $1,000.
- Sampling target 12 and floor 70% from the staged V2 are retained, including
  the original fixture draw seed and stable decision idempotency keys.
- Existing $5,000 aggregate training exposure/reservation cap and $1,000 gross
  realized-loss stop remain. At full size that is only about five concurrent
  positions; existing positions and reservations consume this same budget.
  The entry correction is not permission to silently raise the aggregate cap.
- Existing positions are not resized. Xavier, the shared ledger, account
  funding, investment-entry economics, maker switch and real-money paths are
  unchanged.

V2 is still undeployed according to the latest report, so this is a correction
to that candidate rather than a new deployed policy version. If it has since
served decisions, assign a distinct new version instead and preserve V2 rows.

The read model now displays $1,000. The decision condition key no longer embeds
the obsolete $100 amount: `entry_cost_incl_fees_within_budget` carries the
numeric threshold. No consumer of the old literal was found in the inspected
code; check your newer branch for one when integrating.

## Verification and release

The local tests exercise five prices with deep books and require reservation
costs above $990 and at most $1,000, plus shallow depth and the existing locked
entry/exposure/loss/fixture limits. The real-Postgres lifecycle test now requires
both a reservation and a fee-inclusive fill above $990 and at most $1,000, then
the existing ledger debit, handoff and audit checks. It also checks the persisted
budget condition's $1,000 threshold.

Real-Postgres cases cannot run in this Codex environment. Run them against your
fresh test database and use the full exact-SHA gate before API-only deployment.
Keep capabilities release and census/feed work progressing independently.

After release, report the actual running session caps, the strategy's $1,000
entry cap, and the first qualifying entry's reservation, executed quantity,
price, fees, source book, cash debit, Xavier handoff and Audrey audit. If the
entry is below target, show its recorded depth/headroom/partial-fill reason.
Also show remaining aggregate headroom; reaching $5,000 is a distinct blocker.

This is a correction patch after the prior package's patch 02. Do not reapply
patch 01 or patch 02. Codex has not deployed or altered production here.
