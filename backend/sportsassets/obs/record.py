"""Writing the observability record -- append-only, idempotent on replay.

EVERY STATEMENT HERE IS AN INSERT. There is no UPDATE and no DELETE in this
module, and migration 062 backs that with a trigger that raises if one is ever
attempted. A timing field that can be rewritten later is a timing field whose
meaning cannot be established afterwards, which is the defect run 82 spent itself
diagnosing.

REPLAY IS A NO-OP, NOT A DUPLICATE. Both inserts carry ON CONFLICT DO NOTHING
against a unique key -- source_event_id for the event, (event, offset) for a
snapshot -- so a restart that re-observes the same source event records one
observation, not two. Acceptance test 10.

A STATE CHANGE APPENDS. append_transition() is the only way state moves, and it
adds a row rather than editing one.
"""
from __future__ import annotations

import json
import logging
import uuid

from . import clock
from .book import ObservationChannel, Transport
from .config import COLLECTOR_VERSION

log = logging.getLogger(__name__)

_EVENT_SQL = """
INSERT INTO rn1_obs_events (
    obs_event_id, parent_event_id, supersedes_event_id, collector_version,
    source_event_id, source_fill_id, source_tx_hash, source_order_id,
    source_venue, source_lane, source_market_id, source_token_id,
    source_outcome_index,
    source_price, source_size, source_side,
    source_ts, source_ts_provenance, source_ts_clock_domain,
    source_ts_status, source_ts_fallback, source_ts_sync_status,
    receipt_wall, receipt_monotonic, process_boot_id, process_identity,
    ingest_worker,
    normalize_start_monotonic, normalize_done_monotonic,
    map_start_monotonic, map_done_monotonic, mapping_provenance,
    mapping_status, mapping_confidence, dest_venue, dest_market_id,
    dest_token_repr, cross_venue_mapping_id,
    decision_start_monotonic, decision_done_monotonic, eligibility_result,
    rejection_reason, hypothetical_side, hypothetical_qty,
    hypothetical_limit_price,
    shadow_executable_qty, shadow_vwap, shadow_depth_exhausted,
    shadow_queue_state, shadow_notes
) VALUES (
    $1,$2,$3,$4,
    $5,$6,$7,$8,
    $9,$10,$11,$12,
    $13,
    $14,$15,$16,
    $17,$18,$19,
    $20,$21,$22,
    $23,$24,$25,$26,
    $27,
    $28,$29,
    $30,$31,$32,
    $33,$34,$35,$36,
    $37,$38,
    $39,$40,$41,
    $42,$43,$44,
    $45,
    $46,$47,$48,
    $49,$50
)
ON CONFLICT (source_event_id) DO NOTHING
RETURNING obs_event_id
"""

_SNAPSHOT_SQL = """
INSERT INTO rn1_obs_snapshots (
    obs_event_id, process_boot_id, offset_label, offset_target_s,
    scheduled_for_monotonic, observation_channel, transport, feed_identity,
    status, miss_reason,
    request_start_wall, request_start_monotonic,
    response_wall, response_monotonic,
    stream_receive_wall, stream_receive_monotonic, actual_offset_s,
    venue_snapshot_ts, venue_sequence, book_provenance,
    best_bid, best_ask, depth, depth_levels,
    vwap_qa, qa_depth_exhausted, vwap_qb, qb_depth_exhausted
) VALUES (
    $1,$2,$3,$4,
    $5,$6,$7,$8,
    $9,$10,
    $11,$12,
    $13,$14,
    $15,$16,$17,
    $18,$19,$20,
    $21,$22,$23::jsonb,$24,
    $25,$26,$27,$28
)
ON CONFLICT (obs_event_id, offset_label) DO NOTHING
"""

_TRANSITION_SQL = """
INSERT INTO rn1_obs_transitions (
    obs_event_id, seq, process_boot_id, at_wall, at_monotonic,
    stage, state, detail
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
ON CONFLICT (obs_event_id, seq) DO NOTHING
"""

_CLOCK_SQL = """
INSERT INTO rn1_obs_clock_sync (
    process_boot_id, process_identity, observed_wall, observed_monotonic,
    method, host_sync_status, host_offset_s, host_error_s,
    peer_name, peer_server_time, rtt_s, offset_estimate_s,
    offset_uncertainty_s, detail
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb)
"""


def new_event_id() -> str:
    return str(uuid.uuid4())


async def insert_event(pool, *, obs_event_id: str, source_event_id: str,
                       source_lane: str, source_venue: str,
                       receipt: clock.Instant,
                       source_ts: clock.SourceTimestamp,
                       ingest_worker: str,
                       parent_event_id: str | None = None,
                       supersedes_event_id: str | None = None,
                       **fields) -> str | None:
    """Append one observability event. Returns the id, or None if already seen.

    source_ts is a SourceTimestamp, never a bare datetime, so the value cannot
    reach the database without its provenance, its clock domain and its status.
    """
    row = await pool.fetchrow(
        _EVENT_SQL,
        obs_event_id, parent_event_id, supersedes_event_id, COLLECTOR_VERSION,
        source_event_id, fields.get("source_fill_id"),
        fields.get("source_tx_hash"), fields.get("source_order_id"),
        source_venue, source_lane, fields.get("source_market_id"),
        fields.get("source_token_id"), fields.get("source_outcome_index"),
        fields.get("source_price"), fields.get("source_size"),
        fields.get("source_side"),
        source_ts.value, source_ts.provenance, source_ts.clock_domain,
        source_ts.status, source_ts.fallback, source_ts.sync_status,
        receipt.wall, receipt.monotonic, receipt.process_boot_id,
        receipt.process_identity, ingest_worker,
        fields.get("normalize_start_monotonic"),
        fields.get("normalize_done_monotonic"),
        fields.get("map_start_monotonic"), fields.get("map_done_monotonic"),
        fields.get("mapping_provenance"), fields.get("mapping_status"),
        fields.get("mapping_confidence"), fields.get("dest_venue"),
        fields.get("dest_market_id"), fields.get("dest_token_repr"),
        fields.get("cross_venue_mapping_id"),
        fields.get("decision_start_monotonic"),
        fields.get("decision_done_monotonic"),
        fields.get("eligibility_result"), fields.get("rejection_reason"),
        fields.get("hypothetical_side"), fields.get("hypothetical_qty"),
        fields.get("hypothetical_limit_price"),
        fields.get("shadow_executable_qty"), fields.get("shadow_vwap"),
        fields.get("shadow_depth_exhausted"), fields.get("shadow_queue_state"),
        fields.get("shadow_notes"),
    )
    return None if row is None else str(row["obs_event_id"])


async def insert_snapshot(pool, *, obs_event_id: str, slot, snap=None,
                          vwap_qa=None, qa_exhausted=None,
                          vwap_qb=None, qb_exhausted=None) -> None:
    """Append one forward snapshot -- captured OR missed.

    A missed offset is written, with its reason. It is never left absent: an
    absent row and a failed read are indistinguishable afterwards, and only one
    of them is honest about what happened.
    """
    req = snap.request_start if snap is not None else None
    resp = snap.response if snap is not None else None
    stream = snap.stream_receive if snap is not None else None
    # The instant the snapshot SITS AT on the curve is when the data arrived --
    # the response for a polled channel, the stream receive for a pushed one --
    # never when we got round to writing the row.
    arrival = resp or stream
    actual = None
    if arrival is not None:
        actual = arrival.monotonic - slot.anchor_monotonic

    await pool.execute(
        _SNAPSHOT_SQL,
        obs_event_id, clock.PROCESS_BOOT_ID, slot.label, slot.target_s,
        slot.due_at,
        snap.observation_channel if snap else ObservationChannel.LEGACY_COMPARABLE_BOOK_PATH,
        snap.transport if snap else Transport.HTTP_GET,
        snap.feed_identity if snap else None,
        slot.status, slot.miss_reason,
        req.wall if req else None, req.monotonic if req else None,
        resp.wall if resp else None, resp.monotonic if resp else None,
        stream.wall if stream else None, stream.monotonic if stream else None,
        actual,
        snap.venue_snapshot_ts if snap else None,
        snap.venue_sequence if snap else None,
        snap.provenance if snap else None,
        snap.best_bid if snap else None, snap.best_ask if snap else None,
        json.dumps(snap.asks) if snap and snap.asks else None,
        snap.depth_levels if snap else None,
        vwap_qa, qa_exhausted, vwap_qb, qb_exhausted,
    )


async def append_transition(pool, *, obs_event_id: str, seq: int, stage: str,
                            state: str, detail: dict | None = None) -> None:
    """The only way state moves: a new row, never an edit to an old one."""
    at = clock.now()
    await pool.execute(
        _TRANSITION_SQL, obs_event_id, seq, clock.PROCESS_BOOT_ID,
        at.wall, at.monotonic, stage, state,
        json.dumps(detail) if detail else None,
    )


async def record_clock_sync(pool, *, method: str, **fields) -> None:
    """Record what is known about clock synchronisation, including nothing.

    A row saying the host's sync status could not be read is worth writing: it
    dates the ignorance. The alternative -- writing nothing when nothing is known
    -- is what leaves a later reader unable to tell an unsynchronised host from an
    unobserved one.
    """
    at = clock.now()
    await pool.execute(
        _CLOCK_SQL, clock.PROCESS_BOOT_ID, clock.PROCESS_IDENTITY,
        at.wall, at.monotonic, method,
        fields.get("host_sync_status"), fields.get("host_offset_s"),
        fields.get("host_error_s"), fields.get("peer_name"),
        fields.get("peer_server_time"), fields.get("rtt_s"),
        fields.get("offset_estimate_s"), fields.get("offset_uncertainty_s"),
        json.dumps(fields.get("detail")) if fields.get("detail") else None,
    )


# =========================================================================
# RUN 83.2 -- the V2 write surface
# =========================================================================
# THREE TABLES INSTEAD OF ONE, because V1 conflated a plan with a result and
# could therefore express neither cleanly. rn1_obs_plan holds the intention and
# is written once; rn1_obs_samples holds the outcome; rn1_obs_feed_events holds
# the cache's own lifecycle. All three are append-only by trigger (migration
# 063), which is why nothing here ever UPDATEs.

# THE SUBJECT COLUMNS GO IN THE INSERT, NOT A FOLLOW-UP UPDATE.
#
# The obvious shape was: reuse V1's _EVENT_SQL unchanged, then UPDATE the row
# with the columns migration 063 added. That is illegal here and the illegality
# is the good kind: migration 062 put rn1_obs_append_only() on rn1_obs_events
# as BEFORE UPDATE OR DELETE FOR EACH ROW, so the second statement would RAISE
# on every admitted event. Found while writing this, not after deploying it.
#
# So V2 gets its own INSERT carrying all of it at once. V1's _EVENT_SQL stays
# byte-for-byte for the tests that pin it; the two differ only by the eight
# columns 063 added, and both keep the ON CONFLICT (source_event_id) DO NOTHING
# that refuses a replay BEFORE any slot is planned.
_EVENT_V2_SQL = _EVENT_SQL.replace(
    "    shadow_queue_state, shadow_notes\n) VALUES (",
    "    shadow_queue_state, shadow_notes,\n"
    "    subject_whale_id, subject_wallet_address, subject_username,\n"
    "    subject_admission_reason, canonical_was_insert,\n"
    "    admission_monotonic, admission_delay_ms, preregistration_version\n"
    ") VALUES (",
).replace(
    "    $49,$50\n)",
    "    $49,$50,\n    $51,$52,$53,$54,$55,$56,$57,$58\n)",
)

_PLAN_SQL = """
INSERT INTO rn1_obs_plan (
    observation_slot_id, obs_event_id, source_event_id, observation_channel,
    target_offset_ms, preregistration_version, process_boot_id,
    receipt_wall, receipt_monotonic, target_monotonic, window_ms,
    planned_at_wall, planned_at_monotonic, source_token_id, source_market_id
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
ON CONFLICT (observation_slot_id) DO NOTHING
"""

_SAMPLE_SQL = """
INSERT INTO rn1_obs_samples (
    observation_slot_id, obs_event_id, process_boot_id, observation_channel,
    target_offset_ms, target_monotonic, actual_sample_monotonic,
    actual_sample_wall, sample_lateness_ms, status, miss_reason,
    source_token_id, source_market_id,
    state_received_wall, state_received_monotonic, cache_age_ms,
    best_bid, best_ask, depth, depth_levels,
    vwap_qa, qa_depth_exhausted, vwap_qb, qb_depth_exhausted,
    venue_snapshot_ts, venue_sequence, venue_book_hash, book_provenance,
    feed_session_id, feed_bootstrap_status, feed_reconnect_count,
    state_validity, continuity_status,
    request_start_wall, request_start_monotonic, response_wall,
    response_monotonic
) VALUES (
    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,
    $21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,$35,$36,$37
)
ON CONFLICT (observation_slot_id) DO NOTHING
"""

_FEED_SQL = """
INSERT INTO rn1_obs_feed_events (
    process_boot_id, feed_session_id, observation_channel, at_wall,
    at_monotonic, kind, tokens_tracked, tokens_refreshed, refresh_duration_ms,
    http_status, detail
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
"""


async def insert_event_v2(pool, *, obs_event_id: str, pending) -> bool:
    """Append the event row for an ADMITTED observation. True if newly written.

    One statement, carrying the subject and the admission timings together with
    everything V1 recorded (see _EVENT_V2_SQL for why it cannot be two).

    THE ANCHOR WRITTEN HERE IS THE PRE-INSERT RECEIPT, not the admission
    instant. pending.receipt was stamped on entry to ingest_trade_result and is
    passed through untouched; admission_monotonic and admission_delay_ms carry
    the canonical insert's cost separately. A reader can therefore always
    recover both "when did BETTOR first hold this event" and "how long before
    the instrument could act on it", and no arithmetic conflates them.
    """
    from .slot import PREREGISTRATION_VERSION          # noqa: PLC0415

    f = pending.fields
    row = await pool.fetchrow(
        _EVENT_V2_SQL,
        obs_event_id, None, None, COLLECTOR_VERSION,
        pending.source_event_id, f.get("source_fill_id"),
        f.get("source_tx_hash"), f.get("source_order_id"),
        pending.source_venue, pending.source_lane, f.get("source_market_id"),
        pending.source_token_id, f.get("source_outcome_index"),
        f.get("source_price"), f.get("source_size"), f.get("source_side"),
        pending.source_ts.value, pending.source_ts.provenance,
        pending.source_ts.clock_domain, pending.source_ts.status,
        pending.source_ts.fallback, pending.source_ts.sync_status,
        pending.receipt.wall, pending.receipt.monotonic,
        pending.receipt.process_boot_id, pending.receipt.process_identity,
        pending.ingest_worker,
        None, None, None, None, None, None, None, None, None, None, None,
        None, None, None, None, None, None, None, None, None, None, None, None,
        # -- migration 063 --
        pending.subject_whale_id, f.get("subject_wallet_address"),
        pending.subject_username, "ADMITTED_SUBJECT_FIRST_RECEIPT", True,
        pending.admission.monotonic if pending.admission else None,
        pending.admission_delay_ms, PREREGISTRATION_VERSION,
    )
    return row is not None


async def insert_plan(pool, slots) -> int:
    """Append every planned slot for one event. Written BEFORE any sampling.

    executemany, one statement, so a burst costs one round trip per event rather
    than ten. ON CONFLICT DO NOTHING makes a replay idempotent: the deterministic
    observation_slot_id means the same intention cannot acquire two identities.
    """
    if not slots:
        return 0
    rows = [(s.observation_slot_id, s.obs_event_id, s.source_event_id,
             s.observation_channel, s.target_offset_ms,
             s.preregistration_version, s.process_boot_id, s.receipt_wall,
             s.receipt_monotonic, s.target_monotonic, s.window_ms,
             s.planned_at_wall, s.planned_at_monotonic, s.source_token_id,
             s.source_market_id) for s in slots]
    async with pool.acquire() as conn:
        await conn.executemany(_PLAN_SQL, rows)
    return len(rows)


async def insert_samples(pool, rows) -> int:
    """Append results. A row per slot outcome, including the non-measurements.

    PROCESS_RESTARTED_BEFORE_CAPTURE and CACHE_MISS are written exactly like a
    capture, because "the instrument held no state" and "the instrument died" are
    findings about BETTOR's knowledge and are as important as a price. V1's
    lesson was the opposite shape: 125,270 rows that looked like measurements.
    """
    if not rows:
        return 0
    payload = [(
        r["observation_slot_id"], r["obs_event_id"], r["process_boot_id"],
        r["observation_channel"], r["target_offset_ms"], r.get("target_monotonic"),
        r.get("actual_sample_monotonic"), r.get("actual_sample_wall"),
        r.get("sample_lateness_ms"), r["status"], r.get("miss_reason"),
        r.get("source_token_id"), r.get("source_market_id"),
        r.get("state_received_wall"), r.get("state_received_monotonic"),
        r.get("cache_age_ms"), r.get("best_bid"), r.get("best_ask"),
        json.dumps(r["depth"]) if r.get("depth") is not None else None,
        r.get("depth_levels"), r.get("vwap_qa"), r.get("qa_depth_exhausted"),
        r.get("vwap_qb"), r.get("qb_depth_exhausted"),
        r.get("venue_snapshot_ts"), r.get("venue_sequence"),
        r.get("venue_book_hash"), r.get("book_provenance"),
        r.get("feed_session_id"), r.get("feed_bootstrap_status"),
        r.get("feed_reconnect_count"), r.get("state_validity"),
        r.get("continuity_status"), r.get("request_start_wall"),
        r.get("request_start_monotonic"), r.get("response_wall"),
        r.get("response_monotonic"),
    ) for r in rows]
    async with pool.acquire() as conn:
        await conn.executemany(_SAMPLE_SQL, payload)
    return len(payload)


async def insert_feed_event(pool, *, session_id: str, channel: str, kind: str,
                            tokens_tracked=None, tokens_refreshed=None,
                            refresh_duration_ms=None, http_status=None,
                            detail=None) -> None:
    """Append one cache/feed lifecycle row.

    Kept out of the sample rows so a sample REFERENCES a feed state instead of
    restating it, and so the feed's history survives periods in which nothing was
    sampled -- which is exactly when a reader most wants to know what the feed
    was doing.
    """
    at = clock.now()
    await pool.execute(
        _FEED_SQL, at.process_boot_id, session_id, channel, at.wall,
        at.monotonic, kind, tokens_tracked, tokens_refreshed,
        refresh_duration_ms, http_status,
        json.dumps(detail) if detail is not None else None)
