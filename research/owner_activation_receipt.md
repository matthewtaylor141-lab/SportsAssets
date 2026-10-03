# Owner activation receipt — prepared actions (never self-approved)

Everything below is an OWNER action. No agent, workflow or Claude session
performs it. Each is independent; none places an order by itself.

## 1 · Xavier small-live management policy (record only; nothing loads it)

Artifact `XAVIER_SMALL_LIVE_MANAGEMENT_V1`, version `1`,
sha256 `c59f3957e01691787c3ebccfbb67b5b7786fcc76929f0ffcc52ba2a5e416375e`,
stored in `agent_policy_artifacts` (migration 201), status `READY_FOR_OWNER_APPROVAL`.
Read it first: `GET /api/command/agents/xavier/management-policy` (authenticated).

```sql
UPDATE agent_policy_artifacts
   SET status = 'APPROVED',
       owner_approval_actor = '<owner name / email>',
       owner_approved_at = now(),
       owner_approval_statement = 'I approve XAVIER_SMALL_LIVE_MANAGEMENT_V1 v1, sha256 c59f3957…416375e.'
 WHERE policy_id = 'XAVIER_SMALL_LIVE_MANAGEMENT_V1' AND version = '1'
   AND status = 'READY_FOR_OWNER_APPROVAL'
   AND sha256 = 'c59f3957e01691787c3ebccfbb67b5b7786fcc76929f0ffcc52ba2a5e416375e';
```
The database refuses an agent/blank approver, a hash mismatch, text edits and
backward status moves. Approval activates no risk limit or capital authority.

## 2 · Live book-currency rule P5_LIVE_STREAM_BOOK_V1 (risk-policy decision)

Artifact in `live_rule_artifacts` (migration 204), version `1`,
sha256 `b2a49354b08b50d4afa5246cfa153e4df590f9c1623b3fa9ccae5a3b343b564a`,
status `READY_FOR_OWNER_APPROVAL`. Rule text, bounds, the venue-guaranteed vs
locally-bounded split and the residual risks NE1–NE7:
`research/p5_live_stream_book_v1.md`.

Approving it alone admits NOTHING today: the rule also requires (a) the
institutional stream running in the API process (PMX_* credentials on the API
service), (b) an exact retail-slug → institutional-symbol mapping with the
single-book premise verified by a simultaneous read, and (c) the decision priced
from that same stream observation (C12). Until all three exist every actual
order is refused `ADMISSION_BOOK_CURRENCY_NOT_LIVE_ADMISSIBLE`.

```sql
UPDATE live_rule_artifacts
   SET status = 'APPROVED', owner_approval_actor = '<owner name / email>',
       owner_approved_at = now(),
       owner_approval_statement = 'I approve P5_LIVE_STREAM_BOOK_V1 version 1, sha256 b2a49354…564a, and accept its not_established residuals NE1-NE7.'
 WHERE rule_id = 'P5_LIVE_STREAM_BOOK_V1' AND version = '1'
   AND status = 'READY_FOR_OWNER_APPROVAL'
   AND sha256 = 'b2a49354b08b50d4afa5246cfa153e4df590f9c1623b3fa9ccae5a3b343b564a';
```
Withdraw: `SET status = 'SUPERSEDED'` (effective on the next intent and lane run).

## 3 · Credential placement (owner, Render dashboard; never in chat)

Add `PMX_CLIENT_ID`, `PMX_KEY_ID`, `PMX_PRIVATE_KEY_B64` (and `PMX_PARTICIPANT_ID`)
to the **sportsassets-api** service so the read-only institutional stream can run
where decisions are made; then set `INSTITUTIONAL_MD_STREAM=on` on the API. The
stream client is structurally orderless (tests: test_institutional_md_orderless.py),
but the venue grants this credential `write:orders` — only our code prevents use.

## 4 · Actual lane activation (last, owner only)

Only after 1–3 and a fresh read-only retail reconciliation:
`ping.yml verify=execmirror-enable` (resets stopped, new cutover). Scale 1:1,000,
$25 per-order cap. Stop: `verify=execmirror-stop`.

## Open decisions surfaced by the workers audit

* 7 open mirror books on the FUNDED account (book 1200 frozen, ledger −1,444,
  venue 0) lose automated exit management once the legacy `mirror_live` loop is
  not started. Their management is an owner decision.
* edge-shadow (suspended, autoDeploy=no) holds Kalshi and Polymarket US keys;
  resuming it is a separate owner decision.
* Optional: remove the funded `PMUS_KEY_ID`/`PMUS_SECRET_KEY` from workers.
