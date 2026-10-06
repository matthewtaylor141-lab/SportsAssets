"""THE MIRROR SHADOW READS POSITIONS UNDER THE PRODUCTION CREDENTIAL TOPOLOGY.

Production (2026-10-06): the PMUS secret slot holds the PMX institutional
Auth0 client -- PMX_CLIENT_ID == PMUS_KEY_ID, an RSA PEM private key -- so the
venue walk is refused (R_PMUS_SECRET_NOT_ED25519) and every tick used to
abandon: positions_unreadable, abandoned, exit_leg suppressed. Graceful refusal
is not acceptance. These tests pin the fallback that makes the shadow readable
from the funded account's own ledger (mirror_positions_source), labelled with
its source and as-of instant, WITHOUT any order authority, and fail closed when
that source is unreadable too.
"""
from __future__ import annotations

import ast
import asyncio
import base64
import json
import os
import pathlib

import pytest

from sportsassets import mirror_positions_source as MPS
from sportsassets import refusal_taxonomy_table as TT
from sportsassets.workers import mirror_shadow as MS
from tests import test_mirror_shadow as T

RSA_PEM = ("-----BEGIN PRIVATE KEY-----\n"
           + base64.b64encode(b"\x30\x82" + b"\x01" * 600).decode()
           + "\n-----END PRIVATE KEY-----\n")
#: the slot as production stores it: base64 of the PEM
RSA_PEM_B64 = base64.b64encode(RSA_PEM.encode()).decode()
ED25519 = base64.b64encode(bytes(range(32))).decode()
PMX_CLIENT = "pmx-m2m-client-0001"

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

SRC = pathlib.Path(MPS.__file__)


def _run(coro):
    return asyncio.run(coro)


# ── fakes ─────────────────────────────────────────────────────────────

class _Txn:
    def __init__(self, pool, readonly):
        self.pool, self.readonly = pool, readonly

    async def __aenter__(self):
        self.pool.txns.append({"readonly": self.readonly})
        return self

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self, pool):
        self.pool = pool

    def transaction(self, readonly=False, **_kw):
        return _Txn(self.pool, readonly)

    async def fetchrow(self, sql, *a):
        self.pool.ledger_sql.append(sql)
        if self.pool.ledger_raises:
            raise RuntimeError('relation "bettor_funded_intents" does not exist')
        return {"as_of": "2026-10-06T12:00:00+00:00",
                "rows": json.dumps(self.pool.ledger_rows_json)}


class _Acquire:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return _Conn(self.pool)

    async def __aexit__(self, *exc):
        return False


class _LedgerPool(T._Pool):
    """test_mirror_shadow's pool, plus connections that answer the ledger
    statement inside a recorded transaction."""

    def __init__(self, *a, ledger_rows_json=None, ledger_raises=False, **k):
        super().__init__(*a, **k)
        self.ledger_rows_json = ledger_rows_json or []
        self.ledger_raises = ledger_raises
        self.ledger_sql: list = []
        self.txns: list = []

    def acquire(self):
        return _Acquire(self)


class _NoVenueWalk(T._Pmus):
    """The book reads work; the positions walk must never be reached."""

    class _Walk:
        def positions(self, *_a, **_k):
            raise AssertionError("the positions walk reached the venue")

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.portfolio = self._Walk()

    def _get_read_client(self):
        raise AssertionError("the signed read client was built")


def _topology(monkeypatch, *, secret, key_id, pmx_client):
    monkeypatch.setattr(MS, "_configured_secret", lambda: secret)
    monkeypatch.setattr(MS, "_configured_key_id", lambda: key_id)
    monkeypatch.setattr(MS, "_configured_pmx_client_id", lambda: pmx_client)


def _fresh_tick(monkeypatch):
    T._nosleep(monkeypatch)
    monkeypatch.setenv("MIRROR_WHALES", "rn1")
    MS._ratio_cache.update(at=0.0, by_whale={})
    MS._backoff_until = 0.0
    MS._exit_cache.clear()
    MS._unmapped_until.clear()


# ── the production topology ───────────────────────────────────────────

def test_production_topology_pmx_rsa_in_the_pmus_slot_reads_positions_from_the_funded_ledger(
        monkeypatch):
    """THE EXACT PRODUCTION TOPOLOGY: the PMUS slot holds the PMX RSA PEM
    client and PMX_CLIENT_ID == PMUS_KEY_ID. The tick is READABLE: no
    positions_unreadable, not abandoned, the exit leg not suppressed, the
    source and as-of named, and no venue walk, no order, no cancel."""
    _fresh_tick(monkeypatch)
    _topology(monkeypatch, secret=RSA_PEM_B64, key_id=PMX_CLIENT,
              pmx_client=PMX_CLIENT)
    assert MS.pmus_secret_unusable_reason() == MS.R_PMUS_SECRET_NOT_ED25519
    p = _LedgerPool(fills=T.HIS, ledger_rows_json=[
        {"slug": T.SLUG, "src": "live_orders", "net": 147.0, "n": 1},
        {"slug": "other-slug", "src": "bettor_funded_intents", "net": -7.0, "n": 1}],
        ledger_rows=[{"sh": 147.0, "intent": "ORDER_INTENT_BUY_LONG"}],
        whales_ratio_fills=T._ratio_fills())
    pm = _NoVenueWalk(bid=0.30, ask=0.32)
    stats = _run(MS.tick_once(p, pm, now_ts=5000.0))

    assert not stats.get("positions_unreadable")
    assert not stats.get("abandoned")
    assert stats["status"] == "ok"
    assert stats["exit_leg"] != {"state": "suppressed"}
    assert "error" not in stats["exit_leg"]
    assert stats["venue_positions"] == 2 and stats["markets"] == 1 and stats["rows"] == 1
    # the book was read and the shadow planned (a would-order, never an order)
    assert stats["would_orders"] == 1 and pm.calls == [("bbo", T.SLUG)]
    src = stats["positions_source"]
    assert src["source"] == MPS.SRC_LEDGER
    assert src["authority"] == MPS.AUTHORITY_LEDGER
    assert src["as_of"] == "2026-10-06T12:00:00+00:00"
    assert src["primary_refusal"] == MS.R_PMUS_SECRET_NOT_ED25519
    assert src["pmus_slot_is_pmx_rsa_client"] is True
    assert src["pmx_client_id_equals_pmus_key_id"] is True
    assert src["pmx_positions_fallback"] == MPS.PMX_FALLBACK_REJECTED
    # ONE read, READ ONLY, and the row says where its venue_net came from
    assert len(p.ledger_sql) == 1 and p.txns == [{"readonly": True}]
    ins = [w for w in p.writes if "INSERT INTO mirror_shadow" in w[0]]
    assert len(ins) == 1
    blob = json.dumps([str(x) for x in ins[0][1]])
    assert MPS.SRC_LEDGER in blob and MPS.AUTHORITY_LEDGER in blob
    # no credential value reaches the census
    assert PMX_CLIENT not in json.dumps(stats, default=str)
    assert RSA_PEM_B64 not in json.dumps(stats, default=str)


def test_the_fallback_reading_drives_the_plan_and_the_exit_leg_stays_shadow(monkeypatch):
    """venue_net is the ledger's figure; the plan is a would-order on a row,
    and the pmus fake raises on submit_fok / cancel_order -- nothing is
    placed by the shadow on this path."""
    T._nosleep(monkeypatch)
    _topology(monkeypatch, secret=RSA_PEM, key_id=PMX_CLIENT, pmx_client=PMX_CLIENT)
    p = _LedgerPool(fills=T.HIS, ledger_rows_json=[
        {"slug": T.SLUG, "src": "live_orders", "net": 147.0, "n": 1}],
        ledger_rows=[{"sh": 147.0, "intent": "ORDER_INTENT_BUY_LONG"}])
    positions, src = _run(MS.tick_positions(p, _NoVenueWalk()))
    assert positions == {T.SLUG: 147.0} and src["source"] == MPS.SRC_LEDGER
    row = _run(MS.shadow_market(p, _NoVenueWalk(bid=0.30, ask=0.32), "rn1", T.CID,
                                T.RATIO, {}, positions=positions))
    assert row["venue_net"] == 147.0 and row["ledger_net"] == 147
    assert not str(row["reason"]).startswith("frozen")
    assert row["reason"] != "venue unreadable"
    # a WOULD-order on the row (the shadow's plan), never a placement: the
    # adapter fake raises on submit_fok / cancel_order and was not reached
    assert row["would_side"] == "BUY_LONG" and row["would_fill"] is None


# ── the primary path is unchanged ─────────────────────────────────────

def test_a_real_ed25519_key_still_uses_the_primary_venue_walk(monkeypatch):
    _fresh_tick(monkeypatch)
    _topology(monkeypatch, secret=ED25519, key_id="9f1c2d3e-0000-4000-8000-000000000001",
              pmx_client=PMX_CLIENT)
    assert MS.pmus_secret_unusable_reason() is None
    p = _LedgerPool(fills=T.HIS)
    pm = T._Pmus(bid=0.30, ask=0.32, held={"other-slug": 5.0})
    stats = _run(MS.tick_once(p, pm, now_ts=5000.0))
    assert pm.portfolio.calls == 1, "the venue walk ran"
    assert p.ledger_sql == [] and p.txns == [], "the ledger was not read"
    assert stats["positions_source"]["source"] == MPS.SRC_VENUE
    assert not stats.get("positions_unreadable") and stats["venue_positions"] == 1


def test_a_failed_venue_walk_under_a_real_key_does_not_fall_back(monkeypatch):
    """The ledger is not a stand-in for a venue that answered badly."""
    _fresh_tick(monkeypatch)
    _topology(monkeypatch, secret=ED25519, key_id="k", pmx_client=PMX_CLIENT)
    p = _LedgerPool(fills=T.HIS)
    stats = _run(MS.tick_once(p, T._Pmus(raise_walk=True), now_ts=6000.0))
    assert stats["positions_unreadable"] is True and stats["abandoned"] is True
    assert p.ledger_sql == []
    assert stats["positions_source"]["source"] == MPS.SRC_VENUE
    MS._backoff_until = 0.0


# ── fail closed ───────────────────────────────────────────────────────

def test_both_sources_unreadable_fail_closed_with_a_named_reason(monkeypatch):
    _fresh_tick(monkeypatch)
    _topology(monkeypatch, secret=RSA_PEM_B64, key_id=PMX_CLIENT, pmx_client=PMX_CLIENT)
    p = _LedgerPool(fills=T.HIS, ledger_raises=True)
    pm = _NoVenueWalk()
    stats = _run(MS.tick_once(p, pm, now_ts=7000.0))
    assert stats["positions_unreadable"] is True and stats["abandoned"] is True
    assert stats["status"] == "degraded" and stats["markets"] == 0 and pm.calls == []
    assert stats["positions_unreadable_reason"] == MPS.R_NO_POSITIONS_SOURCE
    src = stats["positions_source"]
    assert src["primary_refusal"] == MS.R_PMUS_SECRET_NOT_ED25519
    assert src["fallback_refusal"] == MPS.R_LEDGER_POSITIONS_UNREADABLE
    assert stats["exit_leg"] == {"state": "suppressed"}
    assert MS._backoff_until == 7000.0 + MS.BACKOFF_S
    MS._backoff_until = 0.0


def test_an_unrecognised_non_ed25519_slot_does_not_fall_back(monkeypatch):
    """Only the production topology (an RSA PEM in the slot) is served by the
    fallback; any other unusable credential fails closed by name."""
    _fresh_tick(monkeypatch)
    _topology(monkeypatch, secret="not-a-key-of-any-kind", key_id="x",
              pmx_client=PMX_CLIENT)
    assert MS.pmus_secret_unusable_reason() == MS.R_PMUS_SECRET_NOT_ED25519
    p = _LedgerPool(fills=T.HIS)
    stats = _run(MS.tick_once(p, _NoVenueWalk(), now_ts=8000.0))
    assert stats["positions_unreadable"] is True and stats["abandoned"] is True
    assert stats["positions_unreadable_reason"] == MPS.R_NO_POSITIONS_SOURCE
    assert stats["positions_source"]["fallback_refusal"] == MPS.R_SLOT_NOT_PMX_RSA
    assert p.ledger_sql == []
    MS._backoff_until = 0.0


def test_an_unreadable_ledger_row_refuses_the_whole_reading():
    for rows in ([{"slug": "", "src": "live_orders", "net": 1.0, "n": 1}],
                 [{"slug": "a", "src": "live_orders", "net": "NaN", "n": 1}],
                 [{"slug": "a", "src": "live_orders", "net": None, "n": 1}],
                 [{"slug": "a", "src": "somewhere_else", "net": 1.0, "n": 1}],
                 {"not": "a list"}):
        with pytest.raises(ValueError):
            MPS.positions_from_rows("t", json.dumps(rows))
    pos, rec = MPS.positions_from_rows("t", json.dumps([
        {"slug": "A", "src": "live_orders", "net": 5.0, "n": 2},
        {"slug": "a", "src": "mirror_registered_positions", "net": -5.0, "n": 1},
        {"slug": "b", "src": "bettor_funded_intents", "net": 3.0, "n": 1}]))
    assert pos == {"b": 3.0}, "a slug netting to zero is not held"
    assert rec["source"] == MPS.SRC_LEDGER and rec["as_of"] == "t"


# ── no order authority ────────────────────────────────────────────────

def test_the_guard_refuses_every_statement_that_is_not_a_read():
    for bad in ("INSERT INTO live_orders VALUES (1)", "UPDATE live_orders SET x=1",
                "DELETE FROM live_orders", "SELECT 1; DELETE FROM live_orders",
                "DROP TABLE live_orders", ""):
        with pytest.raises(MPS.NotARead):
            MPS.guard_read(bad)
    from sportsassets.live_executor import ORDER_INTENT_SQL
    sql = MPS.ledger_positions_sql(ORDER_INTENT_SQL)
    assert MPS.guard_read(sql) == sql
    with pytest.raises(MPS.NotARead):
        MPS.ledger_positions_sql("1) ; DELETE FROM live_orders; SELECT (1")

    class _Boom:
        def acquire(self):
            raise AssertionError("a refused statement opened a connection")
    with pytest.raises(MPS.NotARead):
        _run(MPS.read_only_fetchrow(_Boom(), "UPDATE live_orders SET status='x'"))


def test_the_source_imports_no_order_module_and_names_no_order_method():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {(node.module or "")} | {a.name for a in node.names}
    forbidden = {"pmus", "pmx", "pmx_institutional", "live_executor", "execution_gate",
                 "bettor_funded_execution", "bettor_entry_execution", "kalshi_orders",
                 "venue_sdk", "requests", "httpx", "polymarket_us"}
    assert not (imported & forbidden), imported & forbidden
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for word in ("submit", "cancel", "place_order", "replace_order", "preview",
                 "close_position", "post", "put", "delete"):
        assert not any(word in c.lower() for c in called), (word, called)


def test_the_fallback_tick_reaches_nothing_on_the_venue_adapter_but_the_book_read(monkeypatch):
    """Only bbo_read may be touched on the adapter during a fallback tick."""
    _fresh_tick(monkeypatch)
    _topology(monkeypatch, secret=RSA_PEM_B64, key_id=PMX_CLIENT, pmx_client=PMX_CLIENT)
    touched: list = []

    class _Strict:
        def bbo_read(self, client, slug):
            touched.append("bbo_read")
            return {"bid": 0.30, "ask": 0.32, "state": "MARKET_STATE_OPEN", "error": None}

        def __getattr__(self, name):
            touched.append(name)
            if name in ("_get_client", "_get_read_client"):
                return lambda: None
            raise AttributeError(name)

    p = _LedgerPool(fills=T.HIS, ledger_rows_json=[])
    stats = _run(MS.tick_once(p, _Strict(), now_ts=9000.0))
    assert not stats.get("abandoned")
    bad = [t for t in touched if any(w in t.lower() for w in
                                     ("submit", "cancel", "order", "close", "positions"))]
    assert bad == [], bad


def test_the_ledger_intent_rule_is_live_executors_own():
    """The statement signs live_orders exactly as mirror_shadow.ledger_net
    does: live_executor's ORDER_INTENT_SQL, handed in, never a copy."""
    from sportsassets.live_executor import ORDER_INTENT_SQL
    assert " ".join(ORDER_INTENT_SQL.split()) in MPS.ledger_positions_sql(ORDER_INTENT_SQL)
    assert "{response,executions" not in SRC.read_text(encoding="utf-8")


def test_the_new_refusals_are_classified():
    for code in (MPS.R_SLOT_NOT_PMX_RSA, MPS.R_LEDGER_POSITIONS_UNREADABLE,
                 MPS.R_NO_POSITIONS_SOURCE, MPS.R_NOT_A_READ):
        assert code in TT.TABLE, code


def test_the_topology_is_read_by_shape_only():
    t = MPS.credential_topology(PMX_CLIENT, RSA_PEM_B64, PMX_CLIENT)
    assert t["pmus_slot_is_pmx_rsa_client"] is True
    assert t["pmx_client_id_equals_pmus_key_id"] is True
    assert PMX_CLIENT not in json.dumps(t) and RSA_PEM_B64 not in json.dumps(t)
    assert MPS.fallback_allowed(t)
    t2 = MPS.credential_topology("9f1c2d3e-0000-4000-8000-000000000001", ED25519, PMX_CLIENT)
    assert t2["pmus_slot_is_pmx_rsa_client"] is False and not MPS.fallback_allowed(t2)
    assert t2["pmx_client_id_equals_pmus_key_id"] is False


# ── against Postgres: the one statement, in a read-only transaction ──

@pg
async def test_the_ledger_statement_derives_the_funded_account_positions_canonically():
    asyncpg = pytest.importorskip("asyncpg")
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
    try:
        async with pool.acquire() as c:
            await _clean(c)
            await c.execute(
                "INSERT INTO live_orders (asset, side, his_price, limit_price, requested_usd, "
                " requested_shares, status, filled_shares, raw, venue, us_market_slug) VALUES "
                "('mps-t0','BUY',0.5,0.5,1,10,'filled',10,'{\"preview\":{\"intent\":\"ORDER_INTENT_BUY_LONG\"}}','polymarket-us','mps-aec-a'),"
                "('mps-t1','BUY',0.5,0.5,1,4,'exiting',4,'{\"preview\":{\"intent\":\"ORDER_INTENT_BUY_SHORT\"}}','polymarket-us','mps-aec-b'),"
                "('mps-t2','BUY',0.5,0.5,1,99,'settled',99,'{}','polymarket-us','mps-aec-a'),"
                "('mps-t3','BUY',0.5,0.5,1,50,'filled',50,'{}','polymarket-clob','mps-aec-a')")
            await c.execute(
                "INSERT INTO bettor_funded_portfolio_groups (group_id, account_id, venue, "
                " event_key, structure) VALUES ('mps-g1','mps-acct','PMUS','mps-ev1','SINGLE_LEG')")
            await c.execute(
                "INSERT INTO bettor_funded_intents (intent_id, account_id, venue, venue_class, "
                " us_market_slug, event_key, order_intent, limit_price, quantity, collateral_usd, "
                " effective_digest, state, residual_qty, portfolio_group_id, leg_role) VALUES "
                "('mps-i1','mps-acct','PMUS','FUNDED','MPS-AEC-C','mps-ev1',"
                " 'ORDER_INTENT_BUY_SHORT',0.4,10,6,'d','FILLED',7,'mps-g1','PRIMARY')")
            await c.execute(
                "INSERT INTO bettor_funded_intents (intent_id, account_id, venue, venue_class, "
                " us_market_slug, event_key, order_intent, limit_price, quantity, collateral_usd, "
                " effective_digest, state, residual_qty, closed_at, closed_reason) VALUES "
                "('mps-i2','mps-acct','PMUS','FUNDED','mps-aec-d','mps-ev2',"
                " 'ORDER_INTENT_BUY_LONG',0.4,10,4,'d','FILLED',10, now(), 'SETTLED_BY_THE_VENUE')")
            await c.execute(
                "INSERT INTO mirror_registered_positions (whale, us_market_slug, condition_id, "
                " asset, shares, side, registered_by, note, source) VALUES "
                "('rn1','mps-aec-a','c','t1',3,'LONG','test','t','test')")
        from sportsassets.live_executor import ORDER_INTENT_SQL
        positions, rec = await MPS.ledger_positions(pool, ORDER_INTENT_SQL)
        mine = {k: v for k, v in (positions or {}).items() if k.startswith("mps-")}
        assert mine == {"mps-aec-a": 13.0, "mps-aec-b": -4.0, "mps-aec-c": -7.0}, (mine, rec)
        assert rec["source"] == MPS.SRC_LEDGER and rec["as_of"]
        assert rec["by_table"]["bettor_funded_intents"]["slugs"] >= 1

        # THE SECOND WALL: a write that got past the guard is refused by
        # the database inside the READ ONLY transaction
        with pytest.raises(asyncpg.exceptions.ReadOnlySQLTransactionError):
            await MPS.read_only_fetchrow(
                pool, "WITH x AS (DELETE FROM live_orders WHERE us_market_slug = 'mps-aec-a' "
                      "RETURNING 1) SELECT count(*) FROM x")
        async with pool.acquire() as c:
            assert await c.fetchval(
                "SELECT count(*) FROM live_orders WHERE us_market_slug='mps-aec-a'") == 3
            await _clean(c)
    finally:
        await pool.close()


async def _clean(c):
    await c.execute("DELETE FROM mirror_registered_positions WHERE us_market_slug LIKE 'mps-%'")
    await c.execute("DELETE FROM bettor_funded_intents WHERE intent_id LIKE 'mps-%'")
    await c.execute("DELETE FROM bettor_funded_portfolio_groups WHERE group_id LIKE 'mps-%'")
    await c.execute("DELETE FROM live_orders WHERE us_market_slug LIKE 'mps-%'")
