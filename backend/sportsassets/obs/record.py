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
    scheduled_for_monotonic, status, miss_reason,
    request_start_wall, request_start_monotonic,
    response_wall, response_monotonic, actual_offset_s,
    venue_snapshot_ts, venue_sequence, book_provenance,
    best_bid, best_ask, depth, depth_levels,
    vwap_qa, qa_depth_exhausted, vwap_qb, qb_depth_exhausted
) VALUES (
    $1,$2,$3,$4,
    $5,$6,$7,
    $8,$9,
    $10,$11,$12,
    $13,$14,$15,
    $16,$17,$18::jsonb,$19,
    $20,$21,$22,$23
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
    actual = None
    if resp is not None:
        actual = resp.monotonic - slot.anchor_monotonic

    await pool.execute(
        _SNAPSHOT_SQL,
        obs_event_id, clock.PROCESS_BOOT_ID, slot.label, slot.target_s,
        slot.due_at, slot.status, slot.miss_reason,
        req.wall if req else None, req.monotonic if req else None,
        resp.wall if resp else None, resp.monotonic if resp else None, actual,
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
