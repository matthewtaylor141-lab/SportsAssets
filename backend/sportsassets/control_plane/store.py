"""STREAM E'S STORE: the only module that writes cp_decision_snapshots,
cp_post_trade_observations and cp_replay_*, and the bounded readers of those
tables (the API and the passes' own bookkeeping).

Every write is an append-only INSERT ... ON CONFLICT DO NOTHING (the tables
refuse UPDATE / DELETE / TRUNCATE). Nothing here reads a source table: the
learning inputs are read through pit.py.
"""
from __future__ import annotations

import datetime as _dt
import json


def _ts(epoch):
    if epoch is None:
        return None
    if isinstance(epoch, _dt.datetime):
        return epoch
    return _dt.datetime.fromtimestamp(float(epoch), tz=_dt.timezone.utc)


def _js(v) -> str:
    return json.dumps(v, default=str, sort_keys=True)


INSERT_SNAPSHOT = """
    INSERT INTO cp_decision_snapshots (snapshot_id, decision_id,
        opportunity_id, opportunity_key, intent_id, decided_at, as_of,
        evidence_max_recorded_at, sport, league, event, market, contract,
        strategy, strategy_version, sleeve, verdict, model_version,
        config_sha, code_sha, frozen, frozen_sha, snapshot_version, source)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb,
            $14, $15, $16, $17, $18, $19, $20, $21::jsonb, $22, $23, $24)
    ON CONFLICT DO NOTHING
    RETURNING snapshot_id"""


async def insert_snapshot(conn, s: dict) -> bool:
    """ONE frozen snapshot (one INSERT). True when written, False when this
    decision already has its snapshot for this code (idempotent)."""
    got = await conn.fetchval(
        INSERT_SNAPSHOT, s["snapshot_id"], s["decision_id"],
        s["opportunity_id"], s["opportunity_key"], s.get("intent_id"),
        _ts(s["decided_at"]), _ts(s["as_of"]),
        _ts(s.get("evidence_max_recorded_at")), s.get("sport"),
        s.get("league"), s.get("event"), s.get("market"),
        _js(s.get("contract") or {}), s["strategy"],
        s.get("strategy_version"), s["sleeve"], s["verdict"],
        s.get("model_version"), s.get("config_sha"), s["code_sha"],
        _js(s["frozen"]), s["frozen_sha"], s["snapshot_version"],
        s["source"])
    return got is not None


async def frozen_decisions(conn, decision_ids: list, *, code_sha: str) -> set:
    if not decision_ids:
        return set()
    rows = await conn.fetch(
        "SELECT decision_id FROM cp_decision_snapshots WHERE "
        " decision_id = ANY($1::text[]) AND code_sha = $2",
        list(decision_ids), code_sha)
    return {r["decision_id"] for r in rows}


INSERT_OBSERVATION = """
    INSERT INTO cp_post_trade_observations (observation_id, snapshot_id,
        decision_id, opportunity_id, horizon, horizon_clock,
        observation_version, status, unavailable_reason, observed_at,
        evidence_recorded_at, evidence, venue_bid, venue_ask, venue_mid,
        price_move, clv, pnl_usd, metrics, observer_version, code_sha)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13,
            $14, $15, $16, $17, $18, $19::jsonb, $20, $21)
    ON CONFLICT DO NOTHING
    RETURNING observation_id"""


def _num(v):
    if v is None:
        return None
    from decimal import Decimal
    return Decimal(str(round(float(v), 6)))


async def insert_observation(conn, o: dict) -> bool:
    got = await conn.fetchval(
        INSERT_OBSERVATION, o["observation_id"], o["snapshot_id"],
        o["decision_id"], o["opportunity_id"], o["horizon"],
        _ts(o["horizon_clock"]), int(o.get("observation_version") or 1),
        o["status"], o.get("unavailable_reason"), _ts(o.get("observed_at")),
        _ts(o.get("evidence_recorded_at")), _js(o.get("evidence") or {}),
        _num(o.get("venue_bid")), _num(o.get("venue_ask")),
        _num(o.get("venue_mid")), _num(o.get("price_move")),
        _num(o.get("clv")), _num(o.get("pnl_usd")), _js(o["metrics"]),
        o["observer_version"], o["code_sha"])
    return got is not None


async def observed_keys(conn, snapshot_keys: list) -> set:
    """{(snapshot_id, decision_id, horizon, observation_version)} already
    recorded for these snapshot rows (the pass's own bookkeeping)."""
    if not snapshot_keys:
        return set()
    rows = await conn.fetch(
        "SELECT snapshot_id, decision_id, horizon, observation_version FROM "
        " cp_post_trade_observations WHERE (snapshot_id, decision_id) IN "
        " (SELECT * FROM unnest($1::text[], $2::text[]))",
        [k[0] for k in snapshot_keys], [k[1] for k in snapshot_keys])
    return {(r["snapshot_id"], r["decision_id"], r["horizon"],
             r["observation_version"]) for r in rows}


async def insert_replay_run(conn, run: dict, events: list) -> bool:
    """ONE replay run and its ordered event stream, in one transaction. The
    run id is a function of its inputs, config, stages and code, so an
    identical re-run is a no-op (and its output sha is pinned equal)."""
    async with conn.transaction():
        got = await conn.fetchval(
            "INSERT INTO cp_replay_runs (run_id, account_id, inputs_sha, "
            " config, config_sha, stages, code_sha, clock_start, clock_end, "
            " starting_cash_usd, events, output_sha, summary) VALUES ($1, $2,"
            " $3, $4::jsonb, $5, $6::jsonb, $7, $8, $9, $10, $11, $12, "
            " $13::jsonb) ON CONFLICT DO NOTHING RETURNING run_id",
            run["run_id"], run["account_id"], run["inputs_sha"],
            _js(run["config"]), run["config_sha"], _js(run["stages"]),
            run["code_sha"], _ts(run["clock_start"]), _ts(run["clock_end"]),
            _num(run["starting_cash_usd"]), len(events), run["output_sha"],
            _js(run["summary"]))
        if got is None:
            return False
        await conn.executemany(
            "INSERT INTO cp_replay_events (run_id, seq, clock, stage, kind, "
            " opportunity_id, snapshot_id, payload, payload_sha) VALUES ($1,"
            " $2, $3, $4, $5, $6, $7, $8::jsonb, $9)",
            [(run["run_id"], e["seq"], _ts(e["clock"]), e["stage"], e["kind"],
              e.get("opportunity_id"), e.get("snapshot_id"),
              _js(e["payload"]), e["payload_sha"]) for e in events])
    return True


# ═══════════════════════════ bounded readers (API) ══════════════════════

SNAPSHOT_LIST = """
    SELECT snapshot_id, decision_id, opportunity_id, intent_id, decided_at,
           as_of, sport, league, event, market, strategy, strategy_version,
           sleeve, verdict, model_version, code_sha, frozen_sha, source,
           recorded_at
      FROM cp_decision_snapshots
     WHERE ($1::timestamptz IS NULL OR decided_at >= $1)
       AND ($2::text IS NULL OR strategy = $2)
       AND ($3::text IS NULL OR verdict = $3)
     ORDER BY decided_at DESC, snapshot_id, decision_id
     LIMIT $4"""


async def list_snapshots(conn, *, since=None, strategy=None, verdict=None,
                         limit: int = 50) -> list:
    return [dict(r) for r in await conn.fetch(
        SNAPSHOT_LIST, _ts(since), strategy, verdict, int(limit))]


async def snapshot_rows(conn, snapshot_id: str) -> list:
    """Every decision row of one snapshot id (the opportunity at one instant
    may carry several strategies' decisions)."""
    return [dict(r) for r in await conn.fetch(
        "SELECT * FROM cp_decision_snapshots WHERE snapshot_id = $1 "
        " ORDER BY decision_id", snapshot_id)]


async def observations_of(conn, snapshot_id: str) -> list:
    return [dict(r) for r in await conn.fetch(
        "SELECT * FROM cp_post_trade_observations WHERE snapshot_id = $1 "
        " ORDER BY decision_id, horizon_clock, observation_version",
        snapshot_id)]


async def snapshot_counts(conn) -> dict:
    row = await conn.fetchrow(
        "SELECT count(*) AS snapshots, count(DISTINCT opportunity_id) AS "
        " opportunities, count(*) FILTER (WHERE verdict = 'ENTER') AS enters,"
        " min(decided_at) AS first_decided_at, max(decided_at) AS "
        " last_decided_at FROM cp_decision_snapshots")
    obs = await conn.fetch(
        "SELECT horizon, status, count(*) AS n FROM cp_post_trade_observations"
        " GROUP BY 1, 2 ORDER BY 1, 2")
    return {"snapshots": dict(row or {}), "observations": [dict(r)
                                                           for r in obs]}


LEARNING_ROWS = """
    SELECT o.snapshot_id, o.decision_id, o.horizon, o.status,
           o.unavailable_reason, o.price_move, o.clv, o.pnl_usd, o.metrics,
           s.strategy, s.sleeve, s.verdict, s.opportunity_id,
           s.frozen #>> '{fields,event,value,fixture}' AS fixture,
           s.frozen #>> '{fields,event,value,event_key}' AS event_key
      FROM cp_post_trade_observations o
      JOIN cp_decision_snapshots s USING (snapshot_id, decision_id)
     WHERE ($1::timestamptz IS NULL OR s.decided_at >= $1)
       AND ($2::text IS NULL OR s.sleeve = $2)
       AND ($3::text IS NULL OR s.strategy = $3)
     ORDER BY o.recorded_at DESC
     LIMIT $4"""


async def learning_rows(conn, *, since=None, sleeve=None, strategy=None,
                        limit: int = 5000) -> list:
    return [dict(r) for r in await conn.fetch(
        LEARNING_ROWS, _ts(since), sleeve, strategy, int(limit))]
