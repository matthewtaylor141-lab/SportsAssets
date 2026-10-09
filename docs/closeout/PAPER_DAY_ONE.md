# Account-backed PAPER Day One

This change implements Issue #4; deploying it does not activate an epoch.
SMALL LIVE remains SHADOW. There is no boot-time reset, dashboard activation,
credential change, or real-money execution authority.

Migration 317 adds an immutable epoch registry, append-only activation/rollback
receipts, and a durable selected-account pointer. Existing historical financial
rows remain untouched. The normal PAPER runtime, read models, accounting,
learning, risk views and position rooms resolve this pointer. Each epoch has a
separate account, session, one $500,000 funding entry and an opening equity
snapshot; its cash, PnL and drawdown derive from that ledger. Its frozen effective
risk and sizing policy comes from the source session, including registered
successive epochs. Losses are never erased or relabelled as profits. Strategy lifecycle reads
inherit the nearest recorded state through registered epoch ancestry; opening
a new account cannot release quarantine, retirement or reduced sizing. Existing
named-person recovery rules and forward-evidence requirements remain unchanged.

Read-only inspection: `python -m sportsassets.tools.paper_day_one read`.
The authenticated `GET /api/command/paper/day-one` reports the selected account;
`GET /api/command/paper/archive/{account_id}` exposes historical balances and
the latest 100 ledger entries. Full historical ledger queries remain available.
The primary account endpoint attaches the epoch receipt only for a registered
account. A Day One label is absent before actual activation.

## Conditional operator activation

After Claude integrates the PRs, all four gates must be repeated on the resulting
full SHA. Obtain a new independently signed acceptance packet for that deployed
SHA, verified GitHub/Sigstore bundles, and official trusted roots. The operator
command requires `activate`, `--epoch-id`, `--request-id`, `--evidence-dir`,
`--release-sha`, `--bundle`, `--trusted-root`, and optionally `--gh`.
Do not invoke activation while the acceptance verdict is RED.

The command verifies signatures over the actual packet and checksum manifest,
checks every listed checksum, requires copied/source GREEN agreement, all 14
categories at >=95% with valid denominators and no RED or unreadable units,
completed successful exact-SHA backend-tests/capital-critical/commit-guard/
engine-diagnostic runs, a <=15-minute acceptance window, and matching
RENDER_GIT_COMMIT. Expiry is rechecked after acquiring the switch lock.
There is no `verified=true` input or HTTP write endpoint.

Activation acquires the existing PAPER runtime advisory lock and the selector
row lock. It locks/reconciles the source account and refuses open orders,
reserves or held positions. Account funding, session, opening snapshot, epoch
receipt, selector switch and event commit together. Failure rolls everything
back; retries cannot fund twice. Historical later settlement corrections stay
in their original account. Database guards reject stale order/session/fill/
ledger ownership and pre-epoch valuation attribution. Streams terminate on an
account switch and request a new cursor. Management/equity caches resolve the
durable selector before serving a hit, pin the account for each read and evict
obsolete account entries; rollback cannot reuse the new epoch's cached view.
An unreadable selector fails closed instead of returning cached historical data.

## Rollback

The operator `rollback` action requires epoch and request IDs. It restores the
previous selector only when both accounts reconcile and are free of open
orders, reserves and positions. It cannot abandon Xavier management or delete
an epoch, ledger entry, loss, fill, or receipt. A rolled-back identity cannot be
reactivated; a later epoch needs a new identity and acceptance proof.

DDL rollback 317 is permitted only before any epoch/evidence exists. Database
guards return unchanged before the first activation. The upgrade receipt proves
this narrowly by matching installed definitions and bodies against migration
317 and checking empty epoch history; other added/changed triggers remain
unproven. After activation, old-binary rollback is explicitly not certified;
use the logical account rollback and retain the new schema/application.

Tests use synthetic local accounts and acceptance fixtures, including simulated
signature command failures; those fixtures are not production acceptance.
Independent production signature results are reported separately.
