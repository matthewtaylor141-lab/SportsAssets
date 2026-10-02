# WebSocket-triggered paper evaluation, 2026-10-02

This is an implementation handoff, NOT a deployed/accepted release.

## Apply without replacing newer work

The source integration was built against candidate 12 (`0723a6a`). Its collector,
feed cache and affected paper modules were checked against that branch. Apply on
an isolated branch from your current serving descendant; do not reset to Codex's
worktree or disturb a running gate.

1. Apply `01-primary-source.patch` only if the prior primary-source package has
   not already been ported. It replaces the Pinnacle reference, carries source
   provenance, and rechecks it after awaited paper book reads.
2. Apply `02-reactive-evaluations.patch`. If 01 was ported manually, review/port
   the collector changes; never discard a newer settlement or identity check.
3. Incorporate `research/pinnapi-reactive-20261002/install.sql` in the **next
   unused numbered migration**. A number was intentionally not invented while
   your release branches are advancing. The worker refuses to evaluate if its
   STARTED audit cannot be written.
4. Put `test_pinnapi_primary_source.py` and `test_pinnapi_reactive.py` on the
   critical list. Review any transport/import/read registry failures on their
   merits; do not simply waive them.
5. Run the real-Postgres collector-path, paper lifecycle, feed ownership,
   freshness, isolation and release suites on the exact integrated SHA. Add a
   full-cycle proof using only stubbed provider/venue I/O: snapshot -> discovery
   registration -> changed WS frame -> real collector valuation -> real paper
   hook -> decision, without calling another periodic cycle. Show stale book,
   insufficient depth, adverse executable price, and changed feed epoch refuse.
6. After the exact candidate passes and the audit migration applies, deploy API
   only. Set `PINNAPI_REACTIVE_PAPER=on` on the API with existing PAPER_SESSION on
   and feed control armed. This switch is independent and ships off. No funded
   switch, mirror control, worker deployment or edge-shadow setting is changed.
7. Let the ordinary discovery pass register supported events. Confirm a later
   WebSocket change triggers an evaluation between collection cycles. Run the
   readback and follow its valuation IDs through actual decisions/orders/fills.
   Report p50/p95 receipt-to-worker and receipt-to-decision, observed venue book
   age/depth/fees, refusal counts and **distinct new position/contract IDs**.
   Worker completion alone does not prove an order or fill.

## What the code does

- FeedCache invokes a synchronous callback after applying the entire frame.
  The callback never performs network/DB I/O and cannot block socket ingestion
  awaiting a book. A callback exception does not stop ingestion.
- One worker, a 128-event latest-update queue, 512 bounded discovery seeds
  (16 KiB each), 30-minute discovery lifetime. Repeated identical versions do
  not evaluate; bursts combine to the newest version. Queue pressure is counted.
- Genuine known-time full-game moneyline changes wake the worker immediately,
  independently of the collection loop's sleep. No new socket or extra paid
  REST call per tick. Initial discovery is still required.
- The worker reuses a single-event, paper-only mode of the existing collector:
  venue-native identity is resolved again; rules/fixture/book/fee/depth checks
  and the actual paper hook are reused. The full global market scan, metered
  discovery fetch, funded servicing, shadow inventory write, funded attempt,
  outcome join and periodic calibration are skipped on this path.
- No fallback to a stale REST price on a reactive attempt. Authority, phase,
  epoch, price version, raw quote age and original independent-book timestamps
  are checked. All outcomes come from one feed snapshot; Pinnacle counts once.
- 12-second evaluation deadline plus bounded audit reads/writes. Timeouts record
  any already-persisted valuation IDs; they do not blindly retry submissions.
  STARTED rows left by a crash remain visible as incomplete.
- Durable per-attempt trigger/queue/start/finish clocks, version, counters,
  refusal result and valuation IDs. The valuation source also carries the
  trigger attempt ID. Existing same-contract/idempotency protections remain.

## Local verification / remaining limitations

30 local standard-library tests pass: 17 primary-source and 13 reactive tests.
They exercise actual feed frames/cache, source selection, worker scheduling,
start/stop wiring and timeout/revocation handling. The fresh-book test uses a
venue I/O seam and changes executable price/depth after notification. Existing
primary tests also exercise actual devig/valuation/persistence argument paths.
Compilation and `git diff --check` pass.

**No claim of a full collector/Postgres gate, live latency, order or fill.** This
container has no pytest/asyncpg/database stack; that is why step 5 is required.
The SQL installation/readback also require your real-Postgres check.

This is full-game moneyline for already discovered baseball/soccer fixtures on
the existing supported venue path. It does not establish universal market,
Kalshi, spread, total or in-play settlement support. Existing matching/settlement
refusals remain. Independent corroborating books can still be stale; their
clocks are not refreshed by PinnAPI. A single queue cannot guarantee every edge
is captured, and venue pacing remains in force. Price updates during a book
read invalidate that evaluation; a newer queued change gets its own attempt.

The new event handler deliberately cannot directly place funded orders. Any
separately authorized mirror must consume the existing paper order lifecycle,
with its own enabled state, quantity rounding and actual-fill audit unchanged.

Rollback: set PINNAPI_REACTIVE_PAPER=off (API restart), or deploy the previous
accepted descendant after the usual ancestry review. Do not delete audit rows,
reset the paper ledger or disable unrelated position management.
