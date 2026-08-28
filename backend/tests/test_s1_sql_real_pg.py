"""Every S1 state statement EXECUTES against real Postgres.

Fleet round 7's critical kill existed because the pin suite only ever
grepped SQL text: SQL_RECON_SINCE left $1 untyped, Postgres resolved
'$1 + interval' as interval+interval at PREPARE, and the statement
could never run — the uncorroborated alarm was structurally silent
while 61 pins passed. Text is not proof. This file prepares and
executes every statement the emitter owns against a scratch database
built here, with semantic assertions on the round-6/7 contracts:
delta merge, armed-never-beside-trips, tombstone refusal, the scoped
legacy-scalar strip, and the hostile-shape guards.

Skips (visibly) when no local Postgres answers — the fleet and dev
boxes run one; CI without it loses this file only.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

import sportsassets.ingestion.s1_emitter as s1

DSN_BASE = os.environ.get(
    "S1_SQL_PIN_DSN",
    "postgresql://sportsassets:sportsassets@localhost:5432/postgres")

DDL = """
CREATE TABLE ingestion_state (key text PRIMARY KEY, value jsonb);
CREATE TABLE whales (
    id bigserial PRIMARY KEY, address text, username text,
    active boolean DEFAULT true, banned boolean DEFAULT false);
CREATE TABLE trades (
    id bigserial PRIMARY KEY, whale_id bigint, tx_hash text,
    asset text, source text, dedupe_key text,
    ts timestamptz, detected_at timestamptz,
    venue_seen_at timestamptz, s1_checked_at timestamptz);
CREATE TABLE reconciliation_runs (
    id bigserial PRIMARY KEY,
    started_at timestamptz DEFAULT now(), finished_at timestamptz,
    missed integer, details jsonb);
"""


async def _scratch():
    asyncpg = pytest.importorskip("asyncpg")
    try:
        admin = await asyncpg.connect(DSN_BASE, timeout=4)
    except Exception:  # noqa: BLE001 — no local PG: skip, never fake
        pytest.skip("no local postgres for the real-SQL pin")
    name = "s1_sql_pin_" + uuid.uuid4().hex[:10]
    await admin.execute(f'CREATE DATABASE "{name}"')
    conn = await asyncpg.connect(
        DSN_BASE.rsplit("/", 1)[0] + "/" + name, timeout=4)
    for stmt in DDL.split(";"):
        if stmt.strip():
            await conn.execute(stmt)
    return admin, conn, name


async def _drop(admin, conn, name):
    await conn.close()
    await admin.execute(f'DROP DATABASE "{name}"')
    await admin.close()


def test_every_state_statement_prepares_and_executes():
    async def run():
        admin, c, name = await _scratch()
        try:
            K = s1.STATE_KEY
            # ── SQL_WRITE: insert, then DELTA merge ────────────────
            await c.execute(s1.SQL_WRITE, K, json.dumps(
                {"counters": {"s1.emitted": 3}, "armed": False}))
            await c.execute(s1.SQL_WRITE, K, json.dumps(
                {"counters": {"s1.emitted": 2, "s1.errors": 1},
                 "armed": True, "cert_reason": "green"}))
            doc = json.loads(await c.fetchval(s1.SQL_READ, K))
            assert doc["counters"] == {"s1.emitted": 5, "s1.errors": 1}, \
                "counters are deltas the server ADDS, never absolutes"
            assert doc["armed"] is True

            # ── SQL_TRIP: union, disarm, tombstone refusal ─────────
            await c.execute(s1.SQL_TRIP, K, "uncorroborated:1", 1000.0)
            await c.execute(s1.SQL_TRIP, K, "uncorroborated:2", 2000.0)
            await c.execute(s1.SQL_TRIP, K, "uncorroborated:1", 9999.0)
            doc = json.loads(await c.fetchval(s1.SQL_READ, K))
            assert doc["trips"] == {"uncorroborated:1": 1000.0,
                                    "uncorroborated:2": 2000.0}, \
                "atomic union; the FIRST timestamp survives a re-trip"
            assert doc["armed"] is False
            # a flush claiming armed=true cannot override live trips
            await c.execute(s1.SQL_WRITE, K, json.dumps(
                {"counters": {}, "armed": True}))
            doc = json.loads(await c.fetchval(s1.SQL_READ, K))
            assert doc["armed"] is False, "armed never persists beside trips"

            # ── SQL_CLEAR: one reason, dict tombstones, scoped strip ─
            await c.execute(
                "UPDATE ingestion_state SET value = value || "
                "'{\"tripped\": \"key_selfcheck\"}' WHERE key = $1", K)
            row = await c.fetchrow(s1.SQL_CLEAR, K, "uncorroborated:1")
            trips = row["trips"]
            trips = trips if isinstance(trips, dict) else json.loads(trips)
            assert "uncorroborated:1" not in trips
            assert "uncorroborated:2" in trips
            doc = json.loads(await c.fetchval(s1.SQL_READ, K))
            assert doc.get("tripped") == "key_selfcheck", \
                "round 7: clearing a DIFFERENT reason must not erase " \
                "the legacy scalar's uncleared sticky trip"
            row = await c.fetchrow(s1.SQL_CLEAR, K, "key_selfcheck")
            doc = json.loads(await c.fetchval(s1.SQL_READ, K))
            assert "tripped" not in doc, \
                "clearing the scalar's own reason removes it"
            assert set(doc["trips_cleared"]) == {"uncorroborated:1",
                                                 "key_selfcheck"}, \
                "tombstones are a DICT — every clear is remembered"
            # tombstone refuses the stale re-persist, admits a new trip
            await c.execute(s1.SQL_TRIP, K, "uncorroborated:1", 1000.0)
            doc = json.loads(await c.fetchval(s1.SQL_READ, K))
            assert "uncorroborated:1" not in doc["trips"]
            await c.execute(s1.SQL_TRIP, K, "uncorroborated:1",
                            9e12)      # newer than any tombstone
            doc = json.loads(await c.fetchval(s1.SQL_READ, K))
            assert "uncorroborated:1" in doc["trips"]

            # ── the SWEEP family binds and runs ────────────────────
            wid = await c.fetchval(
                "INSERT INTO whales (address, username) "
                "VALUES ('0xw', 'w') RETURNING id")
            await c.execute(
                "INSERT INTO trades (whale_id, tx_hash, asset, source, "
                "dedupe_key, ts, detected_at) VALUES ($1, '0xt', 'a', "
                "'s1', 'k1', now() - interval '3 hours', "
                "now() - interval '3 hours')", wid)
            ws = await c.fetch(s1.SQL_SWEEP_WALLETS,
                               float(s1.CORROBORATE_S), "salt")
            assert [w["whale_id"] for w in ws] == [wid]
            rows = await c.fetch(s1.SQL_SWEEP, float(s1.CORROBORATE_S),
                                 wid, "salt")
            assert len(rows) == 1 and rows[0]["ok"] is False
            assert await c.fetchval(s1.SQL_BACKLOG,
                                    float(s1.CORROBORATE_S)) == 1

            # ── SQL_RECON_SINCE: the round-7 critical, semantically ─
            det = rows[0]["detected_at"]
            fill_epoch = rows[0]["ts"].timestamp()
            args = (det, "0xw", fill_epoch - s1.RECON_TS_MARGIN_S,
                    float(s1.RECON_VENUE_LAG_S))
            assert await c.fetchrow(s1.SQL_RECON_SINCE, *args) is None, \
                "no run recorded -> defer"
            await c.execute(
                "INSERT INTO reconciliation_runs (started_at, "
                "finished_at, missed, details) VALUES "
                "(now() - interval '2 hours', now() - interval "
                "'110 minutes', 0, $1::jsonb)",
                json.dumps({"per_wallet": {"0xw": 0, "cov:0xw": {
                    "complete": True, "oldest": None}}}))
            ran = await c.fetchrow(s1.SQL_RECON_SINCE, *args)
            assert ran is not None, \
                "a complete covering run started after the lag margin " \
                "MUST cover — this exact call could never execute " \
                "before the round-7 cast"
            # the failed: key still blocks
            await c.execute("UPDATE reconciliation_runs SET details = "
                            "details || '{\"per_wallet\": {\"0xw\": 0, "
                            "\"failed:0xw\": 1}}'")
            assert await c.fetchrow(s1.SQL_RECON_SINCE, *args) is None

            # ── SQL_MARK + SQL_PROBE bind and run ──────────────────
            await c.execute(s1.SQL_MARK, [rows[0]["id"]])
            assert await c.fetchval(s1.SQL_BACKLOG,
                                    float(s1.CORROBORATE_S)) == 0
            got = await c.fetch(s1.SQL_PROBE, "0xt", wid, "a")
            assert [r["dedupe_key"] for r in got] == ["k1"]

            # ── hostile shapes never wedge the writers (round 7) ───
            await c.execute(
                "UPDATE ingestion_state SET value = "
                "'{\"counters\": []}'::jsonb WHERE key = $1", K)
            await c.execute(s1.SQL_WRITE, K, json.dumps(
                {"counters": {"s1.emitted": 1}}))
            doc = json.loads(await c.fetchval(s1.SQL_READ, K))
            assert doc["counters"] == {"s1.emitted": 1}, \
                "a non-object counters field heals instead of wedging"
            await c.execute(
                "UPDATE ingestion_state SET value = '\"junk\"'::jsonb "
                "WHERE key = $1", K)
            await c.execute(s1.SQL_TRIP, K, "key_selfcheck", 5.0)
            doc = json.loads(await c.fetchval(s1.SQL_READ, K))
            assert doc["trips"] == {"key_selfcheck": 5.0}
            assert doc.get("state_repaired") is True, \
                "a scalar doc is replaced FAIL-VISIBLY, the trip lands"
        finally:
            await _drop(admin, c, name)

    asyncio.run(run())
