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

            # ── round 35: contested fields fold server-side ────────
            await c.execute(s1.SQL_WRITE, K, json.dumps(
                {"counters": {}, "contested": {"500": 1.0, "600": 2.0},
                 "contested_floor": 450}))
            await c.execute(s1.SQL_WRITE, K, json.dumps(
                {"counters": {}, "contested": {},
                 "contested_floor": 0}))
            doc = json.loads(await c.fetchval(s1.SQL_READ, K))
            assert float(doc["contested_floor"]) == 450, \
                "round 35: GREATEST — a concurrent process that " \
                "never saw the flood cannot lower the proven floor"
            assert set(doc["contested"]) == {"500", "600"}, \
                "round 35: union — a bare flush cannot erase marks"
            await c.execute(s1.SQL_WRITE, K, json.dumps(
                {"counters": {}, "contested": {"700": 3.0},
                 "contested_floor": 600}))
            doc = json.loads(await c.fetchval(s1.SQL_READ, K))
            assert float(doc["contested_floor"]) == 600
            assert set(doc["contested"]) == {"700"}, \
                "marks at or below the folded floor are pruned — " \
                "the floor already covers them"

            # ── SQL_TRIP: union, disarm, tombstone refusal ─────────
            r = await c.fetchrow(s1.SQL_TRIP, K, "uncorroborated:1",
                                 1000.0)
            assert bool(r["wrote"]) and not bool(r["had"]), \
                "round 29: a first recording reports the transition"
            r = await c.fetchrow(s1.SQL_TRIP, K, "uncorroborated:2",
                                 2000.0)
            assert bool(r["wrote"]) and not bool(r["had"])
            r = await c.fetchrow(s1.SQL_TRIP, K, "uncorroborated:1",
                                 9999.0)
            assert bool(r["wrote"]) and bool(r["had"]), \
                "round 29: a re-trip of a standing reason reports " \
                "had=true — the caller must not re-count the firing"
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
            assert bool(row["removed"]) is True, \
                "round 26: the clear that transitions says so"
            row = await c.fetchrow(s1.SQL_CLEAR, K, "uncorroborated:1")
            assert bool(row["removed"]) is False, \
                "round 26: a redundant clear (another process already " \
                "released the reason) reports NO transition — the " \
                "caller must not bump s1.trip_self_cleared on it"
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
            r = await c.fetchrow(s1.SQL_TRIP, K, "uncorroborated:1",
                                 1000.0)
            assert not bool(r["wrote"]), "the tombstone refusal is " \
                "visible in the transition signal (round 29 shape)"
            doc = json.loads(await c.fetchval(s1.SQL_READ, K))
            assert "uncorroborated:1" not in doc["trips"]
            r = await c.fetchrow(s1.SQL_TRIP, K, "uncorroborated:1",
                                 9e12)      # newer than any tombstone
            assert bool(r["wrote"]) and not bool(r["had"])
            doc = json.loads(await c.fetchval(s1.SQL_READ, K))
            assert "uncorroborated:1" in doc["trips"]

            # ── SQL_TRIP_INIT: the missing-row birth (round 29) ────
            K2 = "s1_pin_" + uuid.uuid4().hex[:8]
            r = await c.fetchrow(s1.SQL_TRIP, K2, "x", 1.0)
            assert r is None, "no state row: the union defers"
            r = await c.fetchrow(s1.SQL_TRIP_INIT, K2, "x", 1.0)
            assert r is not None and bool(r["wrote"]), \
                "the row is born tripped and disarmed"
            r = await c.fetchrow(s1.SQL_TRIP_INIT, K2, "x", 1.0)
            assert r is None, \
                "a lost insert race returns no row — the caller " \
                "retries the union"
            r = await c.fetchrow(s1.SQL_TRIP, K2, "x", 1.0)
            assert bool(r["wrote"]) and bool(r["had"]), \
                "the retried union sees the winner's recording"
            d2 = json.loads(await c.fetchval(s1.SQL_READ, K2))
            assert d2["trips"] == {"x": 1.0} and d2["armed"] is False

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
                    float(s1.RECON_VENUE_LAG_S), fill_epoch)
            row = await c.fetchrow(s1.SQL_RECON_SINCE, *args)
            assert int(row["n"]) == 0, "no run recorded -> defer"
            # round 8: complete=true with no reached-timestamp is a
            # truncated-feed shape and must NOT cover
            await c.execute(
                "INSERT INTO reconciliation_runs (started_at, "
                "finished_at, missed, details) VALUES "
                "(now() - interval '2 hours', now() - interval "
                "'110 minutes', 0, $1::jsonb)",
                json.dumps({"per_wallet": {"0xw": 0, "cov:0xw": {
                    "complete": True, "oldest": None}}}))
            row = await c.fetchrow(s1.SQL_RECON_SINCE, *args)
            assert int(row["n"]) == 0, \
                "round 8: 'complete' alone never waives the fill-span " \
                "requirement — a degraded feed truncates politely"
            # a walk that provably reached below the fill's ts covers
            await c.execute(
                "UPDATE reconciliation_runs SET details = $1::jsonb",
                json.dumps({"per_wallet": {"0xw": 0, "cov:0xw": {
                    "complete": False, "newest": fill_epoch + 20,
                    "oldest": fill_epoch - s1.RECON_TS_MARGIN_S - 50}}}))
            # round 20: coverage takes TWO independent clean runs
            ran = await c.fetchrow(s1.SQL_RECON_SINCE, *args)
            assert int(ran["n"]) == 1, "one covering run is not enough"
            await c.execute(
                "INSERT INTO reconciliation_runs (started_at, "
                "finished_at, missed, details) SELECT started_at, "
                "finished_at, missed, details FROM reconciliation_runs "
                "LIMIT 1")
            ran = await c.fetchrow(s1.SQL_RECON_SINCE, *args)
            assert int(ran["n"]) == 1, \
                "round 39: byte-identical geometry is ONE testimony " \
                "however many rows carry it — a frozen (or poisoned) " \
                "feed cannot decorrelate itself"
            await c.execute(
                "UPDATE reconciliation_runs SET details = $1::jsonb "
                "WHERE id = (SELECT max(id) FROM reconciliation_runs)",
                json.dumps({"per_wallet": {"0xw": 0, "cov:0xw": {
                    "complete": False, "newest": fill_epoch + 60,
                    "oldest": fill_epoch - s1.RECON_TS_MARGIN_S - 50}}}))
            ran = await c.fetchrow(s1.SQL_RECON_SINCE, *args)
            assert int(ran["n"]) == 2, \
                "TWO span-covering runs with DIFFERING newest " \
                "testimony (the feed re-seated between walks) cover " \
                "— this exact call could never execute before the " \
                "round-7 cast"
            # round 38: a frozen-index walk spans BELOW the fill but
            # its newest served row never reached it — no cover; and a
            # pre-round-38 run with no 'newest' key defers fail-closed
            await c.execute(
                "UPDATE reconciliation_runs SET details = $1::jsonb",
                json.dumps({"per_wallet": {"0xw": 0, "cov:0xw": {
                    "complete": False, "newest": fill_epoch - 7200,
                    "oldest": fill_epoch - s1.RECON_TS_MARGIN_S - 50}}}))
            rn = await c.fetchrow(s1.SQL_RECON_SINCE, *args)
            assert int(rn["n"]) == 0, \
                "round 38: a walk whose newest row never reached the " \
                "fill provably never served it — a frozen index must " \
                "not testify as coverage"
            await c.execute(
                "UPDATE reconciliation_runs SET details = $1::jsonb",
                json.dumps({"per_wallet": {"0xw": 0, "cov:0xw": {
                    "complete": False,
                    "oldest": fill_epoch - s1.RECON_TS_MARGIN_S - 50}}}))
            rn = await c.fetchrow(s1.SQL_RECON_SINCE, *args)
            assert int(rn["n"]) == 0, \
                "round 38: no 'newest' testimony defers, fail-closed"
            await c.execute(
                "UPDATE reconciliation_runs SET details = $1::jsonb",
                json.dumps({"per_wallet": {"0xw": 0, "cov:0xw": {
                    "complete": False, "newest": fill_epoch + 20,
                    "oldest": fill_epoch - s1.RECON_TS_MARGIN_S - 50}}}))
            # the failed: key still blocks
            await c.execute("UPDATE reconciliation_runs SET details = "
                            "details || '{\"per_wallet\": {\"0xw\": 0, "
                            "\"failed:0xw\": 1}}'")
            row = await c.fetchrow(s1.SQL_RECON_SINCE, *args)
            assert int(row["n"]) == 0, "failed: blocks every run"

            # ── SQL_MARK: a RETURNING transition, exactly-once ─────
            won = await c.fetch(s1.SQL_MARK, [rows[0]["id"]])
            assert [r["id"] for r in won] == [rows[0]["id"]]
            again = await c.fetch(s1.SQL_MARK, [rows[0]["id"]])
            assert again == [], \
                "round 8: a second stamper wins nothing and counts " \
                "nothing — judgment is exactly-once across processes"
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
            # round 8: a NON-NUMERIC tombstone value must never wedge
            # the persist — it reads as no tombstone and the trip lands
            await c.execute(
                "UPDATE ingestion_state SET value = jsonb_set(value, "
                "'{trips_cleared}', '{\"stuck:1\": \"yesterday\"}') "
                "WHERE key = $1", K)
            await c.execute(s1.SQL_TRIP, K, "stuck:1", 6.0)
            doc = json.loads(await c.fetchval(s1.SQL_READ, K))
            assert doc["trips"].get("stuck:1") == 6.0, \
                "garbage tombstone values are ignored, never fatal"
            # round 10/29: the tombstone refusal is visible in the
            # transition signal — wrote=false — which _persist_trip
            # reads as 'refused' instead of falsely reporting landed
            await c.fetchrow(s1.SQL_CLEAR, K, "stuck:1")
            r = await c.fetchrow(s1.SQL_TRIP, K, "stuck:1", 6.0)
            assert not bool(r["wrote"]), dict(r)
            # round 10: an ARRAY trips field must not 500 the clear —
            # the operator's only release path heals it to an object
            await c.execute(
                "UPDATE ingestion_state SET value = jsonb_set(value, "
                "'{trips}', '[\"junk\"]'::jsonb) WHERE key = $1", K)
            row = await c.fetchrow(s1.SQL_CLEAR, K, "whatever")
            assert row is not None
            doc = json.loads(await c.fetchval(s1.SQL_READ, K))
            assert doc["trips"] == {}, \
                "the clear heals a non-object trips field in place"
        finally:
            await _drop(admin, c, name)

    asyncio.run(run())


def test_round16_judged_stamp_is_conditional():
    """fleet r16, EXECUTED: the judged stamp lands ONLY while the row
    is still venue-unseen — a venue stamp committing before the stamp
    instant refuses the transition atomically, so a false verdict can
    never become permanent past its own evidence."""
    async def run():
        admin, c, name = await _scratch()
        try:
            wid = await c.fetchval(
                "INSERT INTO whales (address, username) "
                "VALUES ('0xw', 'w') RETURNING id")
            unseen = await c.fetchval(
                "INSERT INTO trades (whale_id, tx_hash, asset, source, "
                "dedupe_key, ts, detected_at) VALUES ($1, '0xa', 'a', "
                "'s1', 'k1', now(), now()) RETURNING id", wid)
            seen = await c.fetchval(
                "INSERT INTO trades (whale_id, tx_hash, asset, source, "
                "dedupe_key, ts, detected_at, venue_seen_at) VALUES "
                "($1, '0xb', 'b', 's1', 'k2', now(), now(), now()) "
                "RETURNING id", wid)
            won = await c.fetch(s1.SQL_MARK_JUDGED, [unseen, seen])
            assert [r["id"] for r in won] == [unseen], \
                "the venue-stamped row refuses the judged transition"
            still = await c.fetchval(
                "SELECT s1_checked_at FROM trades WHERE id = $1", seen)
            assert still is None, \
                "the refused row stays unstamped — the next sweep " \
                "confirms it through the ok path"
            ok = await c.fetchrow(s1.SQL_RECHECK, seen)
            assert bool(ok["ok"]) is True
            won2 = await c.fetch(s1.SQL_MARK, [seen])
            assert [r["id"] for r in won2] == [seen], \
                "the unconditional confirmed stamp still transitions"
        finally:
            await _drop(admin, c, name)

    asyncio.run(run())


def test_round11_dirty_coverage_and_the_tombstone_clock():
    """fleet r11, EXECUTED: (major) a reconciler walk that skipped ANY
    unusable row is DIRTY and never covers — round 9 stopped a stub
    testifying FOR coverage but left a stub-riddled walk free to claim
    clean coverage of a span whose rows it skipped, false-tripping
    STICKY on a correct emission; (minor) the judgment-site refresh
    reads PG's own clock (SQL_NOW), so a tombstone written with now()
    is always outrun by genuinely fresh evidence regardless of
    app-host clock skew."""
    async def run():
        admin, c, name = await _scratch()
        try:
            wid = await c.fetchval(
                "INSERT INTO whales (address, username) "
                "VALUES ('0xw', 'w') RETURNING id")
            await c.execute(
                "INSERT INTO trades (whale_id, tx_hash, asset, source, "
                "dedupe_key, ts, detected_at) VALUES ($1, '0xt', 'a', "
                "'s1', 'k1', now() - interval '3 hours', "
                "now() - interval '3 hours')", wid)
            row = (await c.fetch(s1.SQL_SWEEP, float(s1.CORROBORATE_S),
                                 wid, "salt"))[0]
            args = (row["detected_at"], "0xw",
                    row["ts"].timestamp() - s1.RECON_TS_MARGIN_S,
                    float(s1.RECON_VENUE_LAG_S), row["ts"].timestamp())

            def cov(dirty, seat=30):
                d = {"complete": True,
                     "newest": row["ts"].timestamp() + seat,  # r38 span
                     "oldest": row["ts"].timestamp()
                     - s1.RECON_TS_MARGIN_S - 3600}
                if dirty is not ...:
                    d["dirty"] = dirty
                return json.dumps({"per_wallet": {"0xw": 0,
                                                  "cov:0xw": d}})

            await c.execute(
                "INSERT INTO reconciliation_runs (started_at, "
                "finished_at, missed, details) VALUES "
                "(now() - interval '2 hours', now() - interval "
                "'110 minutes', 0, $1::jsonb)", cov(2))
            rn = await c.fetchrow(s1.SQL_RECON_SINCE, *args)
            assert int(rn["n"]) == 0, \
                "a walk that skipped unusable rows NEVER covers — the " \
                "fill's own row may be among what it skipped"
            await c.execute(
                "UPDATE reconciliation_runs SET details = $1::jsonb",
                cov("lots"))
            rn = await c.fetchrow(s1.SQL_RECON_SINCE, *args)
            assert int(rn["n"]) == 0, \
                "a malformed dirty shape defers, never covers"
            await c.execute(
                "UPDATE reconciliation_runs SET details = $1::jsonb",
                cov(0))
            # round 20: a SECOND clean covering run is required
            await c.execute(
                "INSERT INTO reconciliation_runs (started_at, "
                "finished_at, missed, details) VALUES "
                "(now() - interval '1 hour', now() - interval "
                "'50 minutes', 0, $1::jsonb)", cov(0, seat=90))
            rn = await c.fetchrow(s1.SQL_RECON_SINCE, *args)
            assert int(rn["n"]) == 2, \
                "two clean (dirty=0) spanning walks with different " \
                "newest testimony cover"
            await c.execute(
                "UPDATE reconciliation_runs SET details = $1::jsonb "
                "WHERE id = (SELECT min(id) FROM reconciliation_runs)",
                cov(...))
            rn = await c.fetchrow(s1.SQL_RECON_SINCE, *args)
            assert int(rn["n"]) == 2, \
                "pre-round-11 runs (no dirty key) keep covering"

            # ── SQL_NOW: the tombstone's own clock ─────────────────
            db_now = await c.fetchval(s1.SQL_NOW)
            assert isinstance(db_now, float) and db_now > 1e9
            K = s1.STATE_KEY
            await c.fetchrow(s1.SQL_TRIP_INIT, K, "uncorroborated:9",
                             1000.0)
            await c.fetchrow(s1.SQL_CLEAR, K, "uncorroborated:9")
            r = await c.fetchrow(s1.SQL_TRIP, K, "uncorroborated:9",
                                 1000.0)
            assert not bool(r["wrote"]), \
                "the stale re-persist is refused by the tombstone"
            fresh = float(await c.fetchval(s1.SQL_NOW))
            r = await c.fetchrow(s1.SQL_TRIP, K, "uncorroborated:9",
                                 fresh)
            assert bool(r["wrote"]), \
                "a refresh read from the tombstone's own clock LANDS " \
                "— the round-11 skew cycle terminates on first retry"
        finally:
            await _drop(admin, c, name)

    asyncio.run(run())
