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
The authenticated `GET /api/command/paper/day-one` reports the selected account
and lists every registered epoch (`registered_epochs`: selected or rolled back,
with links to its archive and Audrey reports), so a rolled-back epoch stays
reachable after `day_one` turns false. `GET /api/command/paper/archive/{account_id}`
exposes an account's balances and its ledger newest first, a page at a time
(`limit` up to 500, `before_seq` with the returned `page.next_before_seq` pages
back to the first entry); the selected account is labelled as the current book,
never HISTORICAL. `GET /api/command/paper/audrey?account_id=` reads Audrey's
reports of any account of the selected account's epoch family (an account
outside it is refused by name). The primary account endpoint attaches the epoch
receipt only for a registered account. A Day One label is absent before actual
activation.

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
account's reported day once that day is over, and accounts outside the family
are never touched. Because Xavier's settlement pass keeps applying settlement
revisions to every family account, an archived or rolled-back account can
record a correction after its last reported day closed. THE RULE: whenever a
family account's last activity (its latest fill, settlement version or ledger
entry) is later than its newest Audrey report, the selected account's step
writes that account's current-day version carrying it, and the family day close
writes that day's one final version once it is over -- so every realized gain
or loss on an archived account's ledger appears in an Audrey report. A family
account that records nothing new gets no further version. A switch whose
outgoing version is not stored (a closing write or the version taken by a
writer outside the per-day lock) is refused by name
(`PAPER_SWITCH_AUDREY_VERSION_NOT_STORED` inside
`PAPER_EPOCH_OUTGOING_AUDREY_REPORT_FAILED`); a switch whose outgoing account
already reported a later day (a pass clock ahead of the database's) writes its
version on that newest day instead of being silently skipped.
The agents' research queue (task specs, flow IDs, source-context checks,
claims, its control row and heartbeat), its Slack review posts and its loop
health source, the scheduled profitability research cycle, Audrey's
revenue-reliability proposals, the live-game display's held fixtures and the
opportunity funnel's decision read also resolve the selected account. So do
Command's institutional reports (summary, P&L, performance, positions, blotter,
attribution, risk, reconciliation and the CSV exports), the correlation graph,
the PAPER loss attribution (which names the account), the profitability
validation and profitability scoreboard (which name the account they read).
`intel.attribution.load_paper` has no default account: an omitted account fails
loudly. The red-team risk and learning controls (ATTRIBUTION, PROFIT_BREAKERS,
MULTIPLE_TESTING and the registered study) read the selected account's epoch
family at original timestamps, so an activation neither resets their populations
nor hides Day One's positions from them; before any epoch that family is
`paper_acct_main` alone. The forward scoreboard reconciles its attributed claims
against the ledger of exactly those accounts. Constants that still name
`paper_acct_main` are its own identity (session naming, the owner capital policy
of `bettor_paper_limits`, which epochs inherit through their frozen source
config), defaults of functions every production caller passes an account
(`intel` calibration / risk / regime / sizing / allocator, `profitability.reads`,
`revenue_reliability.evidence.build`), or the SMALL LIVE mirror below.
The archived account's open research tasks (agent tasks whose spec names it)
are left open and are not claimed again after the switch: they drop out of the
research queue, Slack and loop health with no cancel or carry-over record. They
are research only (no order, no ledger entry); closing or carrying them over is
not automated. A newly selected epoch's research control row
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

## Owner decision required before any activation: a flat source vs. a GREEN packet

This is a conflict between two approved requirements; it is documented here and
is NOT resolved in code.

- Activation refuses unless the source PAPER account is flat (no held position,
  open order or reserve: `PAPER_ACCOUNT_HAS_OUTSTANDING_OBLIGATIONS`) and the
  signed packet is GREEN in all 14 categories (every component d>0 and >=95%)
  and was built no more than 900 s earlier.
- The approved judge (`scorecard_14.py` at `42616dcd`) scores a unit with a zero
  denominator UNMEASURED and not passing. With nothing held, the Xavier unit
  `held_positions_with_complete_current_packet` is UNMEASURED [0,0] (or
  READ_UNAVAILABLE where the Xavier rate is unread), and so is the
  Data-freshness unit `held_positions_fresh`: "Xavier and agent coordination"
  and "Data freshness and latency" cannot pass, so a packet built on a flat
  account is never GREEN. The real approved-judge packet (pm-acceptance
  38002788631, release 16d23450) shows exactly this: `paper_freshness`
  open_positions 0 and both units UNMEASURED [0,0].
- The only path today is to hold positions when the packet is built and be
  fully flat within the 15-minute window, so the packet attests a book that no
  longer exists at activation.

The owner chooses one of (the GREEN requirement itself is not loosened under
any option):

1. A reviewed judge rule for a deliberately flat activation packet: the two
   held-position units are scored NOT_APPLICABLE (named, counted, never a pass
   by omission) only when the packet also attests the flat receipt the
   activation will check -- a judge change, so a new pinned `JUDGE_SHA` after
   review.
2. Activation carries open positions into the archive under continued Xavier
   management (Xavier's settlement pass already walks the epoch family; its
   exit/protection management of archived holdings and the account-local
   accounting would need their own review), so the packet can be built and
   activated on a held book.
3. A documented, deliberate flatten procedure: an operator-run sequence that
   exits every position, then builds and signs the packet and activates
   inside 900 s -- with the stated caveat that the packet attests positions
   that are closed at activation.

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
account; tightening the selected account is never refused. One write on a
non-selected account is accepted: a tightening of a registered ANCESTOR of the
selected account (the account a rollback restores), strictly tighter than that
ancestor's current state read under the same lock. It only makes the restore
target stricter (the selected account inherits it wherever it has no event of
its own), so nothing is released. ROLLBACK PREPARATION: an automatic
REDUCED_SIZE, SHADOW_ONLY or QUARANTINED on the epoch -- typically while it is
losing, exactly when a rollback is wanted -- makes the rollback refuse
`ROLLBACK_WOULD_RELEASE_STRATEGY_RESTRICTION`; a named person then tightens the
same strategy on the previous account (`paper_acct_main` for the first epoch) to
at least the epoch's state, and the rollback goes through with the restriction
kept on the restored account. A loosening of a non-selected account, and any
write on a deselected descendant (a rolled-back epoch), stay refused by name.
Losses from rolled-back accounts remain in subsequent risk
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
The automated evidence says so too: the release readback
(`GET /api/command/release`, `acc/release.json` in the pm-acceptance packet)
carries production's read-only `paper_epochs` (epochs ever activated,
rollbacks, selected account), and when the release adds migration 317 over the
rollback target, `tools/rollback_readiness.py` (when that readback is in its
input) and the judge's `rollback_ready` unit report NOT_READY by name once any
epoch exists (`ROLLBACK_PAPER_EPOCH_ACTIVATED`, rolled-back epochs included)
or when the state cannot be read (`ROLLBACK_PAPER_EPOCH_STATE_UNREAD`). In the
pm-acceptance job the rollback step runs before the production readbacks, so
its record says `RELEASE_READBACK_NOT_IN_ACC` and the judge, which runs after
them, decides; moving that step after "Production readbacks" (a workflow
change left to owner review) would let the record carry the same verdict.
Both rules run from the judge checkout (the commit pm-acceptance is
dispatched from), so they take effect only in runs dispatched from a commit
that contains them; a packet judged at the pinned `42616dcd`, the only judge
activation accepts, does not apply them. Re-pinning `JUDGE_SHA` after review
is an owner decision; the anchor is unchanged here. The `paper_epochs`
readback itself is served by the deployed API.

Tests use synthetic local accounts and acceptance fixtures, including simulated
signature command failures; those fixtures are not production acceptance.
Independent production signature results are reported separately.
