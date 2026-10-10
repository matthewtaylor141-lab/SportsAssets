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
Stopping rules, realized PAPER/shadow forward economics and the churn and
turnover controls (re-entry and fixture cooldowns, the recent-refusal cooldown,
the reprice deadband and the hourly entry cap) read every registered epoch of
the same root, including rolled-back children, using their original windows,
so neither activation nor rollback clears them. Unrelated legacy accounts remain isolated. Learned models retain their source account,
model ID and fit timestamp; refits use historical observations and an empty refit
cannot replace a measured inherited model. Accounting remains account-local.

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
`--release-sha` and `--bundle`. Verifier and root overrides are rejected.
Do not invoke activation while the acceptance verdict is RED.

The command verifies signatures over the actual packet and checksum manifest,
checks every listed checksum, requires copied/source GREEN agreement, all 14
categories at >=95% with valid denominators and no RED or unreadable units,
completed successful exact-SHA backend-tests/capital-critical/commit-guard/
engine-diagnostic runs and a <=15-minute acceptance window. The reviewed judge
is pinned to `42616dcdb3c80effaa6406faeda100ae995872cb` in both the packet and
Sigstore `--signer-digest` verification. GitHub CLI 2.102.0 at
`/usr/local/bin/gh` is pinned by binary SHA256 and must be root-owned beneath
root-owned directories; the bundled official roots are pinned and snapshotted
before verification. Updating these anchors requires reviewed code.

Under the activation lock the command reads the three fixed production services
directly from Render with `RENDER_API_KEY`, requires a single live deployment per
service on the accepted release SHA, and refuses an in-progress rollout. Missing
read credentials, verifier provisioning or deployment identity fail closed.
An invoking shell's `RENDER_GIT_COMMIT` cannot attest deployment identity.
Expiry is rechecked after acquiring the lock and checking the services.
There is no `verified=true` input or HTTP write endpoint.

Activation acquires the existing PAPER runtime advisory lock and the selector
row lock. It locks/reconciles the source account and refuses open orders,
reserves or held positions. Account funding, session, opening snapshot, epoch
receipt, selector switch and event commit together. Failure rolls everything
back; retries cannot fund twice. Historical later settlement corrections stay
in their original account: Xavier's production settlement pass walks every
registered account of the same risk family, including rolled-back children and
sibling epochs, with its own session and the ledger's own account lock. Pending
shadow counterfactuals and policy variants are settled across that same family;
counterfactual outcomes
remain distinct from realized ledger P&L. Audrey's update snapshot and three
PAPER-pass audit steps, and both default shadow runners, resolve the selected
account. Archived accounts do not gain selected-account audit authority.
Audrey's daily report of the account being deselected is not left stale: at
activation and at rollback, under the same lock and before the selector moves,
the outgoing session's reported days that are over are closed and its current
day gets a version covering everything recorded up to the switch (a switch
whose outgoing report cannot be written is refused,
`PAPER_EPOCH_OUTGOING_AUDREY_REPORT_FAILED`). Audrey's day close on the
selected account also writes the one final version of every other family
account's reported day once that day is over; nothing else is written for an
archived account, and accounts outside the family are never touched.
The agents' research queue (task specs, flow IDs, source-context checks,
claims, its control row and heartbeat), its Slack review posts and its loop
health source, the scheduled profitability research cycle, Audrey's
revenue-reliability proposals and the live-game display's held fixtures also
resolve the selected account. A newly selected epoch's research control row
starts from its nearest ancestor's (the manager's on/off and hourly budget
carry across the switch). The actual-execution mirror (SMALL LIVE, SHADOW)
deliberately stays on `paper_acct_main`: after activation it mirrors no new
PAPER order, and pointing live mirroring at Day One is an owner decision.
Database guards reject stale order/session/fill/
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
Rollback also refuses when the previous account's strategy state would be less
restrictive than the selected epoch's state. It cannot silently undo quarantine
or retirement; the existing named-person recovery workflow still governs any
upward transition. Every strategy-lifecycle write takes the selector row FOR
SHARE, so a rollback waits for a tightening already in flight and then refuses,
and a tightening that waited for a switch is refused by name
(`PAPER_EPOCH_ACCOUNT_IS_NOT_SELECTED`) instead of landing on the archived
account; tightening the selected account is never refused. Losses from rolled-back accounts remain in subsequent risk
and learning populations without moving cash or changing accounting receipts.

DDL rollback 317 is permitted only before any epoch/evidence exists and only
in a READ COMMITTED transaction. It takes the PAPER advisory lock before
checking evidence and holds it through every DROP. REPEATABLE READ and
SERIALIZABLE are refused because a pre-lock snapshot can hide a concurrent
activation. Use `psql -v ON_ERROR_STOP=1` so a refusal also produces a nonzero
operator exit status. Every DROP
is inside the same refusing DO statement, so plain psql's continue-on-error
behavior cannot destroy evidence. Database
guards return unchanged before the first activation. The upgrade receipt proves
this narrowly by matching installed definitions and bodies against migration
317 and checking empty epoch history; other added/changed triggers remain
unproven. After activation, old-binary rollback is explicitly not certified;
use the logical account rollback and retain the new schema/application.

Tests use synthetic local accounts and acceptance fixtures, including simulated
signature command failures; those fixtures are not production acceptance.
Independent production signature results are reported separately.
