"""Order-confirmation stream — trigger, fan-out, SSE, auth.

The zero-latency promise is structural: the migration-037 trigger
fires pg_notify ON COMMIT of every live_orders INSERT / status
change, one listener fans out to bounded per-client queues, and the
SSE generator relays without polling. The real trigger is executed
against local Postgres; enrichment-only updates must stay silent.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import uuid

import pytest

import os

DSN_BASE = os.environ.get(
    "S1_SQL_PIN_DSN",
    "postgresql://sportsassets:sportsassets@localhost:5432/postgres")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations" / \
    "037_live_orders_notify.sql"

DDL = """
CREATE TABLE live_orders (
    id BIGSERIAL PRIMARY KEY,
    whale_username TEXT, side TEXT NOT NULL,
    venue TEXT NOT NULL DEFAULT 'polymarket-us', us_market_slug TEXT,
    requested_usd NUMERIC(24,6) NOT NULL DEFAULT 0,
    filled_shares NUMERIC(24,6) NOT NULL DEFAULT 0,
    fill_price NUMERIC(10,6), filled_usd NUMERIC(24,6) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'submitting',
    pnl NUMERIC(24,6), error TEXT, raw JSONB);
"""


def test_trigger_columns_exist_in_the_real_schema():
    """Schema-sync pin: every NEW.<col> the trigger reads must be a
    real live_orders column defined in the migrations chain — a
    rename would otherwise break every future write at boot."""
    cols = set(re.findall(r"NEW\.(\w+)", MIG.read_text()))
    chain = ""
    for p in (MIG.parent / "007_live_orders.sql",
              MIG.parent / "008_live_orders_venue.sql"):
        chain += p.read_text()
    for c in cols:
        assert re.search(rf"\b{c}\b", chain), \
            f"trigger reads NEW.{c} but no migration defines it"


def test_trigger_fires_on_insert_and_status_change_only():
    async def run():
        asyncpg = pytest.importorskip("asyncpg")
        try:
            admin = await asyncpg.connect(DSN_BASE, timeout=4)
        except Exception:  # noqa: BLE001
            pytest.skip("no local postgres for the real-SQL pin")
        name = "ostream_pin_" + uuid.uuid4().hex[:10]
        await admin.execute(f'CREATE DATABASE "{name}"')
        conn = await asyncpg.connect(
            DSN_BASE.rsplit("/", 1)[0] + "/" + name, timeout=4)
        try:
            await conn.execute(DDL)
            await conn.execute(MIG.read_text())
            got: list[dict] = []
            listener = await asyncpg.connect(
                DSN_BASE.rsplit("/", 1)[0] + "/" + name, timeout=4)
            listener_ready = asyncio.Event()

            def on_note(c, pid, ch, payload):
                got.append(json.loads(payload))
                listener_ready.set()

            await listener.add_listener("live_orders_events", on_note)
            rid = await conn.fetchval(
                "INSERT INTO live_orders (whale_username, side, "
                "us_market_slug, status, requested_usd) "
                "VALUES ('rn1','BUY','aec-x-y-2026', 'submitting', 46.15) "
                "RETURNING id")
            await asyncio.wait_for(listener_ready.wait(), 3)
            assert got[-1]["op"] == "INSERT"
            assert got[-1]["status"] == "submitting"
            assert got[-1]["whale"] == "rn1"

            # a status change announces itself with the fill facts
            listener_ready.clear()
            await conn.execute(
                "UPDATE live_orders SET status='filled', "
                "filled_shares=74, fill_price=0.62, filled_usd=45.88 "
                "WHERE id=$1", rid)
            await asyncio.wait_for(listener_ready.wait(), 3)
            assert got[-1]["op"] == "UPDATE"
            assert got[-1]["status"] == "filled"
            assert float(got[-1]["fill_price"]) == 0.62

            # an enrichment-only update stays SILENT
            n_before = len(got)
            await conn.execute(
                "UPDATE live_orders SET us_market_slug='aec-x-y-new', "
                "raw='{}'::jsonb WHERE id=$1", rid)
            await conn.execute("SELECT 1")
            await asyncio.sleep(0.3)
            assert len(got) == n_before, \
                "slug/raw backfills must not spam the desk"
        finally:
            await listener.close()
            await conn.close()
            await admin.execute(f'DROP DATABASE "{name}"')
            await admin.close()
    asyncio.run(run())


def test_fanout_never_blocks_on_a_stalled_client():
    from sportsassets.api import order_stream as osm

    fast: asyncio.Queue = asyncio.Queue(maxsize=4)
    stalled: asyncio.Queue = asyncio.Queue(maxsize=1)
    stalled.put_nowait("old")                       # full forever
    osm._subs.add(fast)
    osm._subs.add(stalled)
    try:
        for i in range(3):
            osm._on_notify(None, 0, osm.CHANNEL, f"p{i}")
        assert fast.qsize() == 3, "healthy clients get every event"
        assert stalled.qsize() == 1, "the stalled client just drops"
    finally:
        osm._subs.discard(fast)
        osm._subs.discard(stalled)


def test_sse_generator_relays_orders_and_keeps_alive(monkeypatch):
    from sportsassets.api import order_stream as osm

    monkeypatch.setattr(osm, "ensure_listener", lambda: None)

    class _Req:
        def __init__(self):
            self.n = 0

        async def is_disconnected(self):
            self.n += 1
            return self.n > 2                       # two loop passes

    async def run():
        gen = osm.sse_events(_Req())
        out = [await gen.__anext__()]               # retry preamble
        osm._on_notify(None, 0, osm.CHANNEL, '{"id":1,"status":"filled"}')
        out.append(await gen.__anext__())
        await gen.aclose()
        return out

    out = asyncio.run(run())
    assert out[0].startswith("retry:")
    assert "event: order" in out[1] and '"filled"' in out[1]
    assert not osm._subs, "the queue is released on close"


def test_stream_auth_refuses_without_a_token():
    from fastapi.testclient import TestClient

    from sportsassets.api.app import app

    with TestClient(app) as client:
        r = client.get("/api/desk/stream")
        assert r.status_code == 403
