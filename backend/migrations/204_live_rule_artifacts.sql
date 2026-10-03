-- ══════════════════════════════════════════════════════════════════════
-- 204 · VERSIONED LIVE-RULE ARTIFACTS (OWNER APPROVAL), AND
--       P5_LIVE_STREAM_BOOK_V1 STORED READY_FOR_OWNER_APPROVAL (NOT APPROVED)
-- ══════════════════════════════════════════════════════════════════════
--
-- WHY. The actual lane admits a book-currency verdict only under an
-- APPROVED live rule (actual_admission). sportsassets/live_book_currency.py
-- implements P5_LIVE_STREAM_BOOK_V1 over the resident institutional stream
-- book and declares it as ONE canonical, hashed document. This migration
-- stores that exact text for the OWNER to approve or reject. A test pins
-- this file's literal and hash against the module.
--
-- WHY A NEW TABLE (not agent_policy_artifacts, 201). That table requires
-- agent_id IN ('DEREK','XAVIER','AUDREY') and documents an agent's
-- behaviour that nothing loads. A live rule belongs to no agent, and its
-- APPROVED state IS read: live_rule_artifacts.approved_live_book_rules
-- admits the rule id in execution_intent.create and ActualLane._run --
-- but ONLY while the row is APPROVED with an owner record AND its stored
-- sha256 equals the deployed code's rule hash. Any read failure, missing
-- table, missing row or hash mismatch admits nothing.
--
-- WHAT THE DATABASE ENFORCES (mirroring 201, tightened).
--   * APPROVED only with an owner approval record: owner_approval_actor
--     (non-blank; never DEREK / XAVIER / AUDREY / CLAUDE / SYSTEM, never
--     AGENT* / CLAUDE* / MIGRATION*, never the row's created_by),
--     owner_approved_at and a non-blank owner_approval_statement (CHECK).
--     Approval fields exist only on APPROVED or SUPERSEDED rows.
--   * A row is INSERTED only as DRAFT or READY_FOR_OWNER_APPROVAL: an
--     approval is always a later act on text that already exists (trigger).
--   * The stored sha256 IS the SHA-256 of the stored canonical text, and the
--     jsonb document is that text, naming the same rule_id and version.
--   * No artifact carries a risk limit, capital authority, credential,
--     account authority, submission switch, approval or activation key.
--   * Content is immutable once stored; status moves only forward
--     (DRAFT -> READY -> APPROVED | REJECTED | SUPERSEDED, APPROVED ->
--     SUPERSEDED); the approval record is write-once; a row is never
--     deleted (trigger).
--
-- NOT APPROVED HERE. No code path in this repository writes this table;
-- the owner records an approval as an operator action
-- (research/p5_live_stream_book_v1.md, "Owner approval action").
--
-- IDEMPOTENT: IF NOT EXISTS / OR REPLACE / ON CONFLICT DO NOTHING.
-- No BEGIN/COMMIT of its own: the runner applies the file inside one
-- transaction together with its schema_migrations row.

CREATE TABLE IF NOT EXISTS live_rule_artifacts (
    rule_id                  text        NOT NULL,
    version                  text        NOT NULL,
    title                    text        NOT NULL,
    canonical_json           text        NOT NULL,
    document                 jsonb       NOT NULL,
    sha256                   text        NOT NULL,
    status                   text        NOT NULL,
    created_by               text        NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    owner_approval_actor     text,
    owner_approved_at        timestamptz,
    owner_approval_statement text,
    status_changed_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (rule_id, version),
    CONSTRAINT live_rule_artifacts_rule_id_ck CHECK (
        rule_id ~ '^[A-Z][A-Z0-9_]{2,80}$'),
    CONSTRAINT live_rule_artifacts_status_ck CHECK (status IN (
        'DRAFT', 'READY_FOR_OWNER_APPROVAL', 'APPROVED', 'REJECTED',
        'SUPERSEDED')),
    CONSTRAINT live_rule_artifacts_sha_ck CHECK (
        sha256 ~ '^[0-9a-f]{64}$'
        AND sha256 = encode(sha256(convert_to(canonical_json, 'UTF8')),
                            'hex')),
    CONSTRAINT live_rule_artifacts_document_ck CHECK (
        jsonb_typeof(document) = 'object'
        AND document = canonical_json::jsonb
        AND document->>'rule_id' = rule_id
        AND document->>'version' = version),
    -- A RULE DESCRIPTION IS NOT A GRANT OF LIMITS OR AUTHORITY.
    CONSTRAINT live_rule_artifacts_no_authority_ck CHECK (
        NOT (document ?| ARRAY[
            'risk_limits', 'limits', 'capital_usd', 'max_downside_usd',
            'max_incremental_capital_usd', 'per_order_usd', 'max_order_usd',
            'event_exposure_usd', 'daily_loss_stop_usd', 'credentials',
            'api_key', 'account_authority', 'capital_authority',
            'submission_enabled', 'approved', 'approved_by', 'approved_at',
            'activation', 'activate', 'status', 'owner_approval_actor'])),
    -- APPROVED ONLY WITH AN OWNER APPROVAL RECORD.
    CONSTRAINT live_rule_artifacts_owner_approval_ck CHECK (
        status <> 'APPROVED'
        OR (owner_approval_actor IS NOT NULL
            AND btrim(owner_approval_actor) <> ''
            AND owner_approved_at IS NOT NULL
            AND owner_approval_statement IS NOT NULL
            AND btrim(owner_approval_statement) <> '')),
    -- THE APPROVAL RECORD BELONGS ONLY TO AN APPROVED (OR LATER SUPERSEDED)
    -- ROW, AND ITS ACTOR IS NEVER AN AGENT, A SYSTEM ACTOR OR THE CREATOR.
    CONSTRAINT live_rule_artifacts_approval_fields_ck CHECK (
        (owner_approval_actor IS NULL AND owner_approved_at IS NULL
         AND owner_approval_statement IS NULL)
        OR status IN ('APPROVED', 'SUPERSEDED')),
    CONSTRAINT live_rule_artifacts_actor_not_agent_ck CHECK (
        owner_approval_actor IS NULL
        OR (upper(btrim(owner_approval_actor)) NOT IN
                ('DEREK', 'XAVIER', 'AUDREY', 'CLAUDE', 'SYSTEM')
            AND upper(btrim(owner_approval_actor)) NOT LIKE 'AGENT%'
            AND upper(btrim(owner_approval_actor)) NOT LIKE 'CLAUDE%'
            AND upper(btrim(owner_approval_actor)) NOT LIKE 'MIGRATION%'
            AND upper(btrim(owner_approval_actor))
                <> upper(btrim(created_by))))
);

CREATE OR REPLACE FUNCTION live_rule_artifacts_guard() RETURNS trigger
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status NOT IN ('DRAFT', 'READY_FOR_OWNER_APPROVAL') THEN
            RAISE EXCEPTION 'live_rule_artifacts: %@% must be stored DRAFT or '
                            'READY_FOR_OWNER_APPROVAL; an approval is a later '
                            'act on stored text', NEW.rule_id, NEW.version;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'live_rule_artifacts: an artifact is never '
                        'deleted (%@%)', OLD.rule_id, OLD.version;
    END IF;
    IF NEW.rule_id IS DISTINCT FROM OLD.rule_id
       OR NEW.version IS DISTINCT FROM OLD.version
       OR NEW.title IS DISTINCT FROM OLD.title
       OR NEW.canonical_json IS DISTINCT FROM OLD.canonical_json
       OR NEW.document IS DISTINCT FROM OLD.document
       OR NEW.sha256 IS DISTINCT FROM OLD.sha256
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'live_rule_artifacts: the content of %@% is '
                        'immutable; a change is a new version',
                        OLD.rule_id, OLD.version;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'DRAFT'
            AND NEW.status IN ('READY_FOR_OWNER_APPROVAL', 'REJECTED'))
        OR (OLD.status = 'READY_FOR_OWNER_APPROVAL'
            AND NEW.status IN ('APPROVED', 'REJECTED', 'SUPERSEDED'))
        OR (OLD.status = 'APPROVED' AND NEW.status = 'SUPERSEDED')) THEN
        RAISE EXCEPTION 'live_rule_artifacts: % -> % is not a permitted '
                        'status change for %@%', OLD.status, NEW.status,
                        OLD.rule_id, OLD.version;
    END IF;
    IF OLD.owner_approval_actor IS NOT NULL AND (
           NEW.owner_approval_actor IS DISTINCT FROM OLD.owner_approval_actor
           OR NEW.owner_approved_at IS DISTINCT FROM OLD.owner_approved_at
           OR NEW.owner_approval_statement
              IS DISTINCT FROM OLD.owner_approval_statement)
    THEN
        RAISE EXCEPTION 'live_rule_artifacts: the owner approval record '
                        'of %@% is write-once', OLD.rule_id, OLD.version;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_changed_at := now();
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS live_rule_artifacts_guard_trg ON live_rule_artifacts;
CREATE TRIGGER live_rule_artifacts_guard_trg
    BEFORE INSERT OR UPDATE OR DELETE ON live_rule_artifacts
    FOR EACH ROW EXECUTE FUNCTION live_rule_artifacts_guard();

-- ── P5_LIVE_STREAM_BOOK_V1, READY_FOR_OWNER_APPROVAL ───────────────────
-- sha256 = b2a49354b08b50d4afa5246cfa153e4df590f9c1623b3fa9ccae5a3b343b564a
-- Inserted NOT approved: no owner approval record; the actual lane
-- admits nothing under this rule until the owner approves THIS text.
INSERT INTO live_rule_artifacts (rule_id, version, title,
                                 canonical_json, document, sha256,
                                 status, created_by)
SELECT 'P5_LIVE_STREAM_BOOK_V1', '1',
       'Live book currentness from the resident institutional stream',
       d.txt, d.txt::jsonb, 'b2a49354b08b50d4afa5246cfa153e4df590f9c1623b3fa9ccae5a3b343b564a',
       'READY_FOR_OWNER_APPROVAL', 'migration 204'
  FROM (SELECT $rule${"applies_to":"a book read from the resident institutional gRPC market-data stream (institutional_stream.current(symbol)) for the institutional symbol exactly mapped to the retail contract being decided, evaluated in the deciding process at the decision instant; never a REST book read","approval_requirement":"Status becomes APPROVED only on live_rule_artifacts with an owner approval record (owner_approval_actor, owner_approved_at, owner_approval_statement); the actor is never an agent or the creator; the canonical text and its sha256 are immutable. The rule is admissible only while the stored sha256 equals the deployed code's SHA256.","authority_granted":"NONE","authority_statement":"This document grants no risk limit, capital authority, credential, account authority or submission switch. An owner approval makes the rule id admissible as a book-currency rule in actual_admission; every other admission requirement and every runtime gate (switch, account, cap, buying power, decision age, idempotency) is unchanged.","bounds":{"max_receipt_age_s":2.0,"max_venue_receipt_skew_s":2.0,"max_verdict_age_at_actual_submit_s":2.0,"receipt_future_tolerance_s":0.25,"tradable_instrument_states":["INSTRUMENT_STATE_OPEN"]},"bounds_rationale":"2 s receipt age: five times tighter than the 10 s receipt bound the paper and actual lanes apply to the REST book, and well inside the stream's own 15 s liveness and 30 s snapshot bounds; with no documented heartbeat or re-snapshot interval, only a recent message for the symbol bounds a quiet book, so quiet books fail closed. 2 s venue/receipt skew: an NTP-disciplined host and a healthy stream are milliseconds apart; anything beyond 2 s means a queued (slow-consumer) delivery or a clock fault, and both refuse. 2 s verdict age at submit: without it the 10 s decision-age allowance would let a 2 s-current book reach the venue 12 s old.","evidence_split":{"locally_bounded":[{"id":"LB1_CONNECTION_EPOCH","implemented_by":"institutional_stream.ResidentBooks (on_connected / on_disconnected / current)","statement":"The book was received on the CURRENT connection epoch, which is open. Any disconnect discards every held book (GAP) until a complete book arrives on a LATER epoch; a book from an earlier epoch is never aged into currency."},{"id":"LB2_COMPLETE_BOOK_AFTER_CONNECT_RECONNECT_GAP","implemented_by":"snapshot.on_current_connection and gap is null","statement":"A complete book for this symbol has been received on this epoch after the most recent connect, reconnect or gap, and no gap is open for it."},{"id":"LB3_RECEIPT_AGE","statement":"now - (our receipt instant of that book) <= 2.0 s by the deciding host's clock. On a quiet book this is the ONLY staleness bound (no heartbeat interval is documented), so a book that has not produced a message for this symbol within 2.0 s is STALE."},{"id":"LB4_VENUE_TS_PRESENT_MONOTONIC_AND_SKEW_BOUNDED","statement":"transact_time is present; it never went backwards for this symbol within the epoch (a regression is a GAP until an update at or past the high-water mark); and |receipt - transact_time| <= 2.0 s. This bounds the SUM of server-to-receipt delay and host-clock offset; it does not bound either alone."},{"id":"LB5_MARKET_OPEN","statement":"The instrument state carried by the stream (or refdata) is one of INSTRUMENT_STATE_OPEN; the stream's own checks (book not hidden, not crossed, scales known) pass."},{"id":"LB6_EXACT_CONTRACT_IDENTITY","statement":"An identity mapping (retail slug <-> institutional symbol) returned EXACT for this slug, and the evaluated book is that symbol's. No mapping, or any other answer, never passes."},{"id":"LB7_PRICED_FROM_THIS_BOOK","statement":"The decision's executable price and depth were read from THIS stream observation (same epoch, same receipt instant). A price from another book (e.g. the REST paper book) is NOT_ESTABLISHED under this rule."},{"id":"LB8_VERDICT_AGE_AT_SUBMIT","statement":"The actual lane submits only while now - verdict.evaluated_at <= 2.0 s; otherwise it refuses LIVE_BOOK_VERDICT_STALE_AT_ACTUAL_SUBMIT."}],"not_established":[{"id":"NE1_UNDELIVERED_CHANGES","statement":"Book changes made after the message's server stamp and not yet delivered are not bounded: no change-to-delivery bound is documented, and the undocumented slow_consumer_skip_to_head parameter implies server-side queueing of unstated delay. LB3 + LB4 bound only how old the LAST DELIVERED book is."},{"id":"NE2_DEBOUNCING_AND_COALESCING","statement":"Whether every change is published is not established for the gRPC stream: 'sent on every change to the order book' appears only on trader-guide/market-data (which describes an HTTP-streaming product); retail debouncing batches 'at regular intervals' with no interval stated."},{"id":"NE3_QUIET_BOOK_STALENESS","statement":"No heartbeat interval is documented and heartbeats carry no timestamp and are per connection. A quiet book is therefore bounded only by requiring a recent message for THIS symbol (LB3); quiet markets will read STALE."},{"id":"NE4_TRANSACT_TIME_EVENT","statement":"Whether transact_time is the matching-engine instant of the last book change, the publisher's generation instant or the gateway's send instant is not stated."},{"id":"NE5_HOST_CLOCK_OFFSET","statement":"The deciding host's offset from venue time is not measured by this rule; LB4 bounds offset plus delay jointly."},{"id":"NE6_INTRA_EPOCH_LOSS","statement":"Without a sequence number, a dropped or coalesced message inside one epoch is undetectable; only epoch breaks and timestamp regressions are. Full replacement means the latest complete book supersedes any lost one."},{"id":"NE7_RETAIL_ORDER_ROUTE_SAME_BOOK","statement":"That an order entered through the retail route executes against the same book the institutional stream publishes is the identity mapping's claim (LB6), not this rule's."}],"venue_guaranteed":[{"id":"VG1_SNAPSHOT_ON_SUBSCRIBE_AND_RECONNECT","quotes":[{"quote":"Market data | Snapshot, then updates (`snapshot_only: false`). | Reconnect. Take the new snapshot.","url":"https://docs.polymarket.us/streaming-endpoints/streaming-best-practices"},{"quote":"Order stream and market data send a snapshot, then updates.","url":"https://docs.polymarket.us/trader-guide/streaming-apis"}],"statement":"After a market-data subscribe, and after a reconnect, the stream sends a snapshot of the book before further updates."},{"caveat":"Other pages say 'Snapshot, then updates' without defining 'updates'; no page documents delta semantics. The rule treats every message as a full replacement, as the stream does.","id":"VG2_EACH_MESSAGE_IS_A_COMPLETE_BOOK","quotes":[{"quote":"snapshot-style updates (each message is complete); treat each message as a full update unless documentation specifies delta semantics","url":"https://docs.polymarket.us/trader-guide/market-data"},{"quote":"as snapshots (full book, not deltas)","url":"https://docs.polymarket.us/trader-guide/market-data"}],"statement":"Each market-data message is a complete book (full replacement, not a delta); the documented MarketDataUpdate carries no delta or action field."},{"caveat":"Which server and which event (matching-engine book change, publisher generation, gateway send) is NOT stated.","id":"VG3_TRANSACT_TIME_IS_A_SERVER_TIMESTAMP","quotes":[{"quote":"Server timestamp of update","url":"https://docs.polymarket.us/streaming-endpoints/market-data-stream"},{"quote":"Server timestamp of update","url":"https://docs.polymarket.us/streaming-endpoints/proto-reference"}],"statement":"Every MarketDataUpdate may carry transact_time, defined only as a server timestamp of the update."},{"id":"VG4_CONTINUOUS_UPDATES_WHILE_SUBSCRIBED","quotes":[{"quote":"If `False` (default), receive continuous updates.","url":"https://docs.polymarket.us/streaming-endpoints/market-data-stream"}],"statement":"With snapshot_only false (the default) the stream keeps sending updates after the snapshot."},{"caveat":"No interval, no timestamp, no per-market meaning is stated.","id":"VG5_HEARTBEATS_ARE_CONNECTION_KEEPALIVES","quotes":[{"quote":"Keep-alive messages to confirm connection is active.","url":"https://docs.polymarket.us/streaming-endpoints/market-data-stream"},{"quote":"If you stop receiving heartbeats, the connection may be stale. Consider reconnecting.","url":"https://docs.polymarket.us/streaming-endpoints/market-data-stream"}],"statement":"Heartbeats confirm the CONNECTION is active; their absence means the connection may be stale."},{"basis":"DOCUMENTED_FIELD_LIST (the absence of a sequence field)","documented_fields":["symbol","bids","offers","state","stats","transact_time","book_hidden","price_scale","quantity_scale"],"id":"VG6_NO_SEQUENCE_NUMBER","quotes":[],"sources":["https://docs.polymarket.us/streaming-endpoints/market-data-stream","https://docs.polymarket.us/streaming-endpoints/proto-reference"],"statement":"The documented MarketDataUpdate fields are symbol, bids, offers, state, stats, transact_time, book_hidden, price_scale, quantity_scale: there is no sequence field. Recorded as SEQUENCE_NOT_PROVIDED_BY_VENUE."}]},"kind":"LIVE_BOOK_CURRENCY_RULE","relation_to_review":"This rule does NOT meet the activating documentation D1-D4 the 2026-10-03 review reserved for a documented-timing rule (research/p5_live_book_currency_review.md section 4); verdict B on documented timing stands. It is a locally bounded rule whose residual risks are the not_established items above; approving it is the owner accepting those residuals for the bounded actual lane, not a claim that the venue guarantees currency.","requirements":[{"fails_as":["NOT_ESTABLISHED","REFUSED"],"id":"C1_IDENTITY_EXACT","statement":"LB6. Unmapped -> NOT_ESTABLISHED (IDENTITY_MAPPING_NOT_ESTABLISHED); mapped to another symbol or not EXACT -> REFUSED."},{"fails_as":["NOT_ESTABLISHED","REFUSED"],"id":"C2_STREAM_RUNNING","statement":"The stream runs in the deciding process; a venue refusal of the credential is REFUSED."},{"fails_as":["NOT_ESTABLISHED","GAP"],"id":"C3_CONNECTION_EPOCH_ALIVE","statement":"LB1. No epoch -> NOT_ESTABLISHED; book held when the epoch ended -> GAP."},{"fails_as":["NOT_ESTABLISHED","GAP"],"id":"C4_COMPLETE_BOOK_ON_THIS_EPOCH","statement":"LB2. A gap since the last complete book -> GAP; none received yet on this epoch -> NOT_ESTABLISHED."},{"fails_as":[],"id":"C5_SEQUENCE_INTEGRITY","statement":"VG6: SEQUENCE_NOT_PROVIDED_BY_VENUE; integrity rests on C3, C4 and C7 (epoch + monotonic venue timestamp)."},{"fails_as":["NOT_ESTABLISHED"],"id":"C6_VENUE_TS_PRESENT","statement":"transact_time present on the book."},{"fails_as":["GAP"],"id":"C7_VENUE_TS_MONOTONIC_IN_EPOCH","statement":"LB4 monotonicity."},{"fails_as":["NOT_ESTABLISHED","STALE"],"id":"C8_RECEIPT_AGE","statement":"LB3."},{"fails_as":["NOT_ESTABLISHED","STALE"],"id":"C9_VENUE_RECEIPT_SKEW","statement":"LB4 skew: venue ts older than receipt by more than the bound -> STALE; ahead of it by more -> NOT_ESTABLISHED."},{"fails_as":["NOT_ESTABLISHED","REFUSED"],"id":"C10_MARKET_OPEN","statement":"LB5 state. Unknown -> NOT_ESTABLISHED; any other state -> REFUSED."},{"fails_as":["NOT_ESTABLISHED","GAP","STALE","REFUSED"],"id":"C11_STREAM_BOOK_CURRENT","statement":"institutional_stream.current() answered ok; its refusal code is classified, never ignored."},{"fails_as":["NOT_ESTABLISHED"],"id":"C12_PRICED_FROM_THIS_BOOK","statement":"LB7."},{"fails_as":["ACTUAL_REFUSAL"],"id":"C13_VERDICT_AGE_AT_SUBMIT","statement":"LB8, enforced by execution_intent.ActualLane."}],"rule_id":"P5_LIVE_STREAM_BOOK_V1","sequence":"SEQUENCE_NOT_PROVIDED_BY_VENUE","title":"Live book currentness from the resident institutional stream","verdict_rule":"ESTABLISHED only when C1-C12 all pass; otherwise the most severe failing class in the order REFUSED > GAP > NOT_ESTABLISHED > STALE.","version":"1"}$rule$::text AS txt) d
ON CONFLICT (rule_id, version) DO NOTHING;
