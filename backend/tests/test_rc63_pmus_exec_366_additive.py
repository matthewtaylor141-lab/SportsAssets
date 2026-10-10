"""CAPITAL-CRITICAL (rc6.3 pmus-exec, migration 366 rewritten): THE RETAIL
ACTUAL PATH'S SCHEMA CHANGE CANNOT BREAK THE PREVIOUS RELEASE.

The first 366 added a CHECK to the existing execmirror_control and re-wrote
the status CHECK of the existing smalllive_reconciliations to admit STALE.
The upgrade-path receipt (tools/upgrade_path_receipt.py, compatibility())
cannot prove the previous release still works on a schema whose existing
tables gained or changed constraints, and said so: NOT_PROVEN_COMPATIBLE
(backend-tests run 38021391995, tests/test_rc6e_upgrade_path.py, 2 failed).
366 was never applied anywhere, so it is rewritten:

  * the aggregate open + held notional cap lives in a NEW table,
    execmirror_exposure_caps (one row per account, its own CHECK: positive),
    read with a LEFT JOIN; no row = max_order_usd (fail closed small);
  * Audrey's STALE is stored as status PENDING (a word the table's CHECK
    already admits) plus a NEW nullable column, smalllive_reconciliations.
    stale_reason, and READ as STALE by every reader of this release
    (audrey_reconciliation_status). The previous release reads the row as
    PENDING: not MATCHED, not NOT_MIRRORED, never a DISCREPANCY.

Held here, on Postgres (RN1X_TEST_DSN), each a test that fails on the first
366 (or on a design that widened a CHECK / stored a hidden DISCREPANCY):

  §1  366 is ADDITIVE: only CREATE TABLE of the new table, COMMENTs and
      ADD COLUMN of one nullable column with no default and no constraint;
      its rollback removes exactly those. Run against the real database
      inside a transaction: the schema before 366 (the rollback applied) and
      after it, taken with the receipt's own snapshot, are COMPATIBLE by the
      receipt's own gate -- and every constraint, trigger and unique index
      of every pre-existing table is IDENTICAL; 366 applies again after its
      rollback to the same schema; the rollback keeps the rows written.
  §2  the cap: the database refuses a zero, negative or missing cap, an
      unnamed actor and a second cap for one account; no row reads as
      max_order_usd; a schema without the table (rolled back) fails closed
      small instead of failing the claim; the kill switch's reader never
      touches the table.
  §3  Audrey: STALE is stored as PENDING + reason, never as MATCHED /
      NOT_MIRRORED, never as DISCREPANCY; a real PENDING (an order still
      working) is not STALE; a discrepancy found on a stale snapshot stays a
      DISCREPANCY (never hidden); the reason clears when she can decide.
  §4  every reader reads PENDING + a reason as STALE (execmirror_view,
      position_rooms, runtime_slo, redteam/controls) and the readers that
      act on DISCREPANCY or MATCHED (agent_work, the quorum's open
      discrepancies) treat a stale group as neither; a leftover reason on a
      row the previous release rewrote never reads as STALE.
  §5  the previous release's own Audrey statement (ec8b892d) runs unchanged
      on the new schema.

NO CAPITAL, NO MODE CHANGE: a fake venue only; SMALL_LIVE_MODE stays SHADOW.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import time
import uuid
from decimal import Decimal

import asyncpg
import pytest

from sportsassets import audrey_reconciliation_status as ARS
from sportsassets import execmirror as M
from sportsassets import execmirror_view as V
from sportsassets import position_rooms as PR
from sportsassets import runtime_slo as S
from sportsassets.agents import agent_work as AW
from sportsassets.redteam import controls as C

from tests import test_execmirror as TE

pg = TE.pg
BACKEND = pathlib.Path(__file__).resolve().parents[1]
MIG = BACKEND / "migrations"
UP_SQL = (MIG / "366_execmirror_exposure_cap_and_stale_reconciliation.sql"
          ).read_text()
DOWN_SQL = (MIG / "rollback" /
            "366_execmirror_exposure_cap_and_stale_reconciliation.down.sql"
            ).read_text()
CODE = "AUDREY_ACCOUNT_SNAPSHOT_NOT_CURRENT"


def _tool(name):
    spec = importlib.util.spec_from_file_location(
        "rc63_366_" + name, BACKEND / "tools" / ("%s.py" % name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


UP = _tool("upgrade_path_receipt")


def _statements(sql: str) -> list:
    sql = re.sub(r"--[^\n]*", " ", sql)
    return [re.sub(r"\s+", " ", s).strip() for s in sql.split(";")
            if s.strip()]


# ───────────────────────── §1 · the migration is additive ─────────────────

def test_366_is_a_new_table_comments_and_one_nullable_column_and_nothing_else():
    st = _statements(UP_SQL)
    kinds = []
    for s in st:
        u = s.upper()
        if u.startswith("CREATE TABLE IF NOT EXISTS EXECMIRROR_EXPOSURE_CAPS"):
            kinds.append("new_table")
        elif u.startswith("COMMENT ON TABLE EXECMIRROR_EXPOSURE_CAPS") or \
                u.startswith("COMMENT ON COLUMN SMALLLIVE_RECONCILIATIONS."
                             "STALE_REASON"):
            kinds.append("comment")
        elif s == ("ALTER TABLE smalllive_reconciliations ADD COLUMN IF NOT "
                   "EXISTS stale_reason text"):
            kinds.append("nullable_column")
        else:
            kinds.append("OTHER:" + s[:80])
    assert sorted(kinds) == ["comment", "comment", "new_table",
                             "nullable_column"], kinds
    # nothing that could touch an existing table's constraints, triggers,
    # indexes or rows
    body = " ".join(st).upper()
    for forbidden in ("ADD CONSTRAINT", "DROP ", "CREATE TRIGGER",
                      "CREATE UNIQUE INDEX", "CREATE INDEX", "ALTER COLUMN",
                      "EXECMIRROR_CONTROL", "UPDATE ", "DELETE ", "TRUNCATE",
                      "INSERT "):
        # execmirror_control is named only in prose (the comment text of
        # the new table), never as a statement target
        if forbidden == "EXECMIRROR_CONTROL":
            assert not re.search(r"(ALTER|UPDATE|INSERT INTO|DELETE FROM|"
                                 r"TRUNCATE)\s+(TABLE\s+)?EXECMIRROR_CONTROL",
                                 body)
            continue
        assert forbidden not in body, forbidden


def test_the_rollback_removes_exactly_what_366_added():
    assert _statements(DOWN_SQL) == [
        "DROP TABLE IF EXISTS execmirror_exposure_caps",
        "ALTER TABLE smalllive_reconciliations DROP COLUMN IF EXISTS "
        "stale_reason"]


async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


@pg
async def test_the_receipts_own_gate_calls_366_compatible_and_the_rollback_restores_it():
    conn = await TE._conn()
    tx = conn.transaction()
    await tx.start()
    try:
        gid = "g366-" + uuid.uuid4().hex[:6]
        await conn.execute(
            "INSERT INTO smalllive_reconciliations (group_id, venue, status,"
            " stale_reason, discrepancies, chain) VALUES ($1, 'POLYMARKET',"
            " 'PENDING', $2, '[]'::jsonb, '{}'::jsonb)", gid, CODE)
        after = await UP.snapshot(conn)
        await conn.execute(DOWN_SQL)
        before = await UP.snapshot(conn)             # the schema before 366
        # the receipt's own gate over exactly 366's before / after
        verdict = UP.compatibility(before, after)
        assert verdict == {"verdict": UP.COMPATIBLE, "blocking": [],
                           "unproven": []}, verdict
        # the first 366 failed this gate: it is what the gate sees
        assert "execmirror_exposure_caps" in after
        assert "execmirror_exposure_caps" not in before
        assert set(after["smalllive_reconciliations"]["columns"]) - set(
            before["smalllive_reconciliations"]["columns"]) == {"stale_reason"}
        col = after["smalllive_reconciliations"]["columns"]["stale_reason"]
        assert col["notnull"] is False and col["default"] is False
        # every pre-existing table: constraints, unique indexes and triggers
        # IDENTICAL (the gate flags additions and changes, not removals, so
        # equality is asserted too)
        for t, b in before.items():
            a = after[t]
            for kind in ("constraints", "unique_indexes", "triggers"):
                assert a[kind] == b[kind], (t, kind)
            if t != "smalllive_reconciliations":
                assert a["columns"] == b["columns"], t
        # the status CHECK is still migration 198's four words
        chk = before["smalllive_reconciliations"]["constraints"][
            "smalllive_reconciliations_status_check"]
        assert "STALE" not in chk and "NOT_MIRRORED" in chk
        # the rows written survive the rollback (status PENDING)
        assert await conn.fetchval(
            "SELECT status FROM smalllive_reconciliations WHERE group_id=$1",
            gid) == "PENDING"
        await conn.execute(DOWN_SQL)                  # safe to apply twice
        # ... and 366 applies again, to the same schema, twice
        await conn.execute(UP_SQL)
        assert await UP.snapshot(conn) == after
        await conn.execute(UP_SQL)
        assert await UP.snapshot(conn) == after
        assert await conn.fetchval(
            "SELECT stale_reason FROM smalllive_reconciliations"
            " WHERE group_id = $1", gid) is None      # the reason went with the column
    finally:
        await tx.rollback()
        await conn.close()


# ───────────────────────── §2 · the cap table ─────────────────────────────

@pg
async def test_the_database_guarantees_a_configured_cap_is_positive_named_and_single():
    conn = await TE._conn()
    tx = conn.transaction()
    await tx.start()
    try:
        ins = ("INSERT INTO execmirror_exposure_caps (account_id, "
               "max_open_notional_usd, actor) VALUES ($1, $2, $3)")
        await conn.execute("DELETE FROM execmirror_exposure_caps")
        for bad in (Decimal(0), Decimal("-1.00"), None):
            await _expect(conn, (asyncpg.CheckViolationError,
                                 asyncpg.NotNullViolationError),
                          ins, "A", bad, "owner")
        await _expect(conn, asyncpg.CheckViolationError, ins, "A",
                      Decimal("5.00"), "   ")
        await _expect(conn, asyncpg.NotNullViolationError, ins, "A",
                      Decimal("5.00"), None)
        await conn.execute(ins, "A", Decimal("5.00"), "owner")
        await _expect(conn, asyncpg.UniqueViolationError, ins, "A",
                      Decimal("6.00"), "owner")           # one cap per account
        await conn.execute(ins, "B", Decimal("0.01"), "owner")
    finally:
        await tx.rollback()
        await conn.close()


def test_a_non_positive_cap_would_not_be_believed_even_if_it_were_stored():
    assert M.aggregate_cap_usd(Decimal("25"), None) == (
        Decimal("25"), M.CAP_BASIS_DEFAULT)
    for v in (0, Decimal("-3"), Decimal(0)):
        assert M.aggregate_cap_usd(Decimal("25"), v) == (
            Decimal("25"), M.CAP_BASIS_DEFAULT)
    assert M.aggregate_cap_usd(Decimal("25"), Decimal("2.00")) == (
        Decimal("2.00"), M.CAP_BASIS_CONFIGURED)
    # a missing per-order cap is a cap of zero: refuses everything
    assert M.aggregate_cap_usd(None, None)[0] == Decimal(0)


@pg
async def test_no_row_reads_as_the_per_order_cap_and_a_missing_table_does_not_fail_the_claim():
    conn = await TE._conn()
    tx = conn.transaction()
    await tx.start()
    try:
        per_order = Decimal(str(await conn.fetchval(
            "SELECT max_order_usd FROM execmirror_control WHERE id = 1")))
        await conn.execute("DELETE FROM execmirror_exposure_caps")
        assert await M.aggregate_cap(conn) == (per_order, M.CAP_BASIS_DEFAULT)
        await conn.execute(
            "INSERT INTO execmirror_exposure_caps (account_id, "
            "max_open_notional_usd, actor) VALUES ($1, 3.50, 'owner')",
            M.ACTUAL_ACCOUNT_ID)
        assert await M.aggregate_cap(conn) == (Decimal("3.50"),
                                               M.CAP_BASIS_CONFIGURED)
        # another account's row is not this account's cap
        await conn.execute("UPDATE execmirror_exposure_caps SET account_id ="
                           " 'SOMEONE_ELSE'")
        assert await M.aggregate_cap(conn) == (per_order, M.CAP_BASIS_DEFAULT)
        # a schema rolled back to before 366 (the table dropped): the cap is
        # the fail-closed default, the control reader (the kill switch's) is
        # untouched, nothing raises
        await conn.execute(DOWN_SQL)
        assert await M.aggregate_cap(conn) == (per_order, M.CAP_BASIS_DEFAULT)
        assert (await M.control(conn))["id"] == 1
    finally:
        await tx.rollback()
        await conn.close()


# ───────────────────────── pure: the status mapping ───────────────────────

def test_position_rooms_writes_out_the_same_projection_without_importing_it():
    """position_rooms' import graph is pinned exactly (no new module), so its
    copy of the projection is held equal to the module's here."""
    assert PR._AUDREY_STATUS_SQL == ARS.effective_sql()


def test_stale_is_stored_as_pending_with_its_reason_and_read_back_as_stale():
    assert ARS.stored("STALE", CODE) == ("PENDING", CODE)
    for s in ("MATCHED", "DISCREPANCY", "PENDING", "NOT_MIRRORED"):
        assert ARS.stored(s) == (s, None)
    with pytest.raises(ValueError):
        ARS.stored("STALE")                      # a STALE row names its reason
    with pytest.raises(ValueError):
        ARS.stored("SOMETHING_ELSE")
    # every stored status is one the table's CHECK admits
    assert set(ARS.STORED_STATUSES) == {"MATCHED", "DISCREPANCY", "PENDING",
                                        "NOT_MIRRORED"}
    assert ARS.effective("PENDING", CODE) == "STALE"
    assert ARS.effective("PENDING", None) == "PENDING"
    assert ARS.effective("PENDING", "") == "PENDING"
    # a reason left on a row the previous release rewrote reads as what the
    # row says, never STALE
    for s in ("MATCHED", "DISCREPANCY", "NOT_MIRRORED"):
        assert ARS.effective(s, CODE) == s
    row = ARS.effective_row({"group_id": "g", "status": "PENDING",
                             "stale_reason": CODE})
    assert row["status"] == "STALE" and row["stored_status"] == "PENDING"
    assert ARS.effective_row({"group_id": "g"}) == {"group_id": "g"}
    assert "STALE" in ARS.effective_sql("r") and "r.stale_reason" in \
        ARS.effective_sql("r") and "r.status" in ARS.effective_sql("r")


# ───────────────────────── §3 · Audrey's writer ───────────────────────────

@pytest.fixture(autouse=True)
async def _clean():
    if not TE.DSN:
        yield
        return
    conn = await TE._conn()
    try:
        before = dict(await conn.fetchrow(
            "SELECT * FROM execmirror_control WHERE id = 1"))
    finally:
        await conn.close()
    yield
    conn = await TE._conn()
    try:
        cols = [c for c in before if c != "id"]
        await conn.execute(
            "UPDATE execmirror_control SET %s WHERE id = 1" % ", ".join(
                "%s = $%d" % (c, i + 1) for i, c in enumerate(cols)),
            *[before[c] for c in cols])
        await conn.execute("TRUNCATE smalllive_reviews, smalllive_handoffs, "
                           "smalllive_reconciliations")
        await conn.execute("TRUNCATE execmirror_fills, execmirror_events, "
                           "execmirror_snapshots, execmirror_orders, "
                           "execmirror_exposure_caps")
    finally:
        await conn.close()


def _j(v):
    return json.loads(v) if isinstance(v, str) else (v or {})


async def _snapshot_row(conn, age_s) -> None:
    await conn.execute(
        """INSERT INTO execmirror_snapshots (at, account_fingerprint, balances,
             positions, open_orders, reconciliation)
           VALUES (now() - make_interval(secs => $1), 'fp',
                   '[{"currency": "USD", "buyingPower": 100,
                      "currentBalance": 100}]'::jsonb,
                   '[]'::jsonb, 0, '{"reconciled": true, "differences": {}}'::jsonb)""",
        float(age_s))


async def _rec(conn, group_id):
    return await conn.fetchrow(
        "SELECT *, " + ARS.effective_sql() + " AS read_status FROM"
        " smalllive_reconciliations WHERE group_id = $1", group_id)


async def _events(conn, kind) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM execmirror_events WHERE kind = $1", kind)


@pg
async def test_stale_is_pending_plus_a_reason_and_clears_when_she_can_decide(
        monkeypatch):
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        po = await TE._paper_order(conn, acct, qty=2702,
                                   strategy="PINNACLE_EXPLORATION_PAPER",
                                   policy_version="PINNACLE_EXPLORATION_PAPER_V3")
        await _snapshot_row(conn, 200)
        await mirror.audrey_reconcile(conn)
        r = await _rec(conn, po["group_id"])
        assert (r["status"], r["stale_reason"], r["read_status"]) == (
            "PENDING", CODE, "STALE")
        assert _j(r["discrepancies"]) == []
        assert _j(r["chain"])["stale"]["would_be"] == "NOT_MIRRORED"
        assert await _events(conn, "AUDREY_RECONCILIATION_STALE") == 1
        # the same stale pass again: nothing changes, no second event, the
        # status's change time is not moved
        changed = r["changed_at"]
        await mirror.audrey_reconcile(conn)
        r2 = await _rec(conn, po["group_id"])
        assert r2["read_status"] == "STALE" and r2["changed_at"] == changed
        assert await _events(conn, "AUDREY_RECONCILIATION_STALE") == 1
        # a current snapshot: she can decide; the reason is gone, the change
        # is recorded
        await _snapshot_row(conn, 5)
        await mirror.audrey_reconcile(conn)
        r3 = await _rec(conn, po["group_id"])
        assert (r3["status"], r3["stale_reason"], r3["read_status"]) == (
            "NOT_MIRRORED", None, "NOT_MIRRORED")
        assert r3["changed_at"] > changed
        assert await _events(conn, "AUDREY_RECONCILIATION_NOT_MIRRORED") == 1
        assert _j(r3["chain"])["stale"] is None
    finally:
        await conn.close()


@pg
async def test_a_working_order_is_pending_not_stale_even_on_a_stale_snapshot(
        monkeypatch):
    """PENDING with no reason is the order still working; PENDING + a reason
    is STALE. A stale snapshot must not turn the first into the second (she
    has a better answer than 'cannot decide': it is not final yet)."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        grp = "paper_group_%s" % uuid.uuid4().hex[:8]
        did = await TE._decision(conn, acct, "mlb-test-%s" % grp[-4:])
        a = await TE._paper_order(conn, acct, qty=3000, tif="GTD",
                                  otype="RESTING", group=grp, decision_id=did)
        await mirror.snapshot(conn, await M.control(conn))
        assert (await mirror.lane._run(conn, a["intent_id"]))["state"] == \
            "SUBMITTED"
        await conn.execute("UPDATE execmirror_snapshots SET at = at - "
                           "interval '10 minutes'")
        await mirror.audrey_reconcile(conn)
        r = await _rec(conn, a["group_id"])
        assert (r["status"], r["stale_reason"], r["read_status"]) == (
            "PENDING", None, "PENDING")
        assert _j(r["chain"])["stale"] is None
    finally:
        await conn.close()


@pg
async def test_a_discrepancy_on_a_stale_snapshot_stays_a_discrepancy_never_hidden(
        monkeypatch):
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        orphan = await TE._paper_order(conn, acct, qty=2702)   # no decision
        venue.behaviour = [{"fill": 3}]
        await mirror.tick(conn)
        r = await _rec(conn, orphan["group_id"])
        assert r["status"] == "DISCREPANCY", r
        await conn.execute("UPDATE execmirror_snapshots SET at = at - "
                           "interval '10 minutes'")
        mirror._last_management = 0.0
        await mirror.audrey_reconcile(conn)
        r = await _rec(conn, orphan["group_id"])
        assert (r["status"], r["stale_reason"], r["read_status"]) == (
            "DISCREPANCY", None, "DISCREPANCY")
        codes = [d["code"] for d in _j(r["discrepancies"])]
        assert "ACTUAL_ORDER_WITHOUT_DECISION" in codes
        assert _j(r["chain"])["stale"] is None
    finally:
        await conn.close()


# ───────────────────────── §4 · the readers ───────────────────────────────

async def _rows(conn) -> dict:
    """One group per stored shape, written as migration 366 stores them."""
    ids = {k: "g366r-%s-%s" % (k, uuid.uuid4().hex[:6]) for k in (
        "stale", "pending", "disc", "match", "leftover", "notm")}
    spec = {"stale": ("PENDING", CODE), "pending": ("PENDING", None),
            "disc": ("DISCREPANCY", None), "match": ("MATCHED", None),
            # a MATCHED row the previous release rewrote, reason left behind
            "leftover": ("MATCHED", CODE), "notm": ("NOT_MIRRORED", None)}
    for k, (st, why) in spec.items():
        await conn.execute(
            "INSERT INTO smalllive_reconciliations (group_id, venue, status,"
            " stale_reason, discrepancies, chain) VALUES ($1, 'POLYMARKET',"
            " $2, $3, $4::jsonb, '{}'::jsonb)", ids[k], st, why,
            json.dumps([{"code": "X"}] if k == "disc" else []))
    return ids


EXPECT = {"stale": "STALE", "pending": "PENDING", "disc": "DISCREPANCY",
          "match": "MATCHED", "leftover": "MATCHED", "notm": "NOT_MIRRORED"}


@pg
async def test_every_reader_of_this_release_reads_pending_plus_a_reason_as_stale():
    conn = await TE._conn()
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("TRUNCATE smalllive_reviews, smalllive_handoffs, "
                           "smalllive_reconciliations")
        ids = await _rows(conn)
        groups = list(ids.values())
        want = {ids[k]: v for k, v in EXPECT.items()}
        # execmirror_view (Command's small-live and decision views)
        m = await V._management(conn, [{"group_id": g} for g in groups])
        got = {g: m["reconciliations"][g]["status"] for g in groups}
        assert got == want, got
        assert V.RECONCILIATION_MEANING["STALE"].startswith("not decided")
        # position_rooms (the room's Audrey section)
        a = await PR._audrey(conn, [], groups)
        assert {r["group_id"]: r["status"] for r in a["reconciliations"]} == \
            want
        # redteam/controls (the quorum's evidence), for the OPEN hand-offs
        for k in ("stale", "pending", "match"):
            await conn.execute(
                "INSERT INTO smalllive_handoffs (handoff_id, venue, group_id,"
                " us_market_slug, entry_mirror_id, live_held, live_bought,"
                " first_live_fill_at) VALUES ($1, 'POLYMARKET', $2, $3, 'm1',"
                " 1, 1, now())", "ho-" + ids[k], ids[k], "mkt-" + k)
        ev = {r["group_id"]: r["status"] for r in await conn.fetch(
            C.AUDREY_POSITIONS_SQL) if r["group_id"] in groups}
        assert ev == {ids["stale"]: "STALE", ids["pending"]: "PENDING",
                      ids["match"]: "MATCHED"}, ev
        # the quorum's open discrepancies count DISCREPANCY only
        _rows_, open_disc = await C.quorum_rows(
            conn, now=time.time(), venue_confirmed=True,
            market_data_green=True, detail={})
        assert open_disc == 1
        # runtime_slo's RECONCILIATION_AGE names it STALE, as a breach
        async with conn.transaction():
            body = await S.read_slos(conn, now=time.time(),
                                     api={"sha": None}, receipts={"items": []})
        slo = {s["slo"]: s for s in body["slos"]}["RECONCILIATION_AGE"]
        assert slo["status"] == S.BREACH
        assert slo["measured"]["not_matched"][ids["stale"]] == "STALE"
        assert slo["measured"]["not_matched"][ids["pending"]] == "PENDING"
        assert ids["match"] not in slo["measured"]["not_matched"]
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_stale_group_is_neither_a_discrepancy_nor_a_match_for_the_work_queues():
    conn = await TE._conn()
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("TRUNCATE smalllive_reviews, smalllive_handoffs, "
                           "smalllive_reconciliations")
        ids = await _rows(conn)
        # Audrey's backlog: DISCREPANCY only -- the stale group raises none
        backlog = await AW._backlog_reconciliation(conn, time.time(), 50)
        assert [b["subject"] for b in backlog] == [ids["disc"]]
        # a work item is resolved by MATCHED only -- never by STALE, never
        # by a leftover reason on a row that is not PENDING
        items = [{"request_id": "r-" + k, "subject": ids[k]}
                 for k in ("stale", "pending", "disc", "match", "leftover")]
        done = await AW._resolve_reconciliation(conn, items, time.time())
        assert sorted(done) == ["r-leftover", "r-match"]
    finally:
        await tx.rollback()
        await conn.close()


# ───────────────────────── §5 · the previous release's writer ─────────────

#: the previous release's (ec8b892d, execmirror.audrey_reconcile) statement,
#: verbatim: it names its own columns, knows nothing of stale_reason
PREVIOUS_RELEASE_AUDREY_UPSERT = (
    """INSERT INTO smalllive_reconciliations (group_id, venue, status, discrepancies, chain)
                   VALUES ($1,$2,$3,$4::jsonb,$5::jsonb)
                   ON CONFLICT (group_id) DO UPDATE SET status = EXCLUDED.status,
                     discrepancies = EXCLUDED.discrepancies, chain = EXCLUDED.chain,
                     reconciled_at = now(),
                     changed_at = CASE WHEN smalllive_reconciliations.status
                                       IS DISTINCT FROM EXCLUDED.status
                                       THEN now() ELSE smalllive_reconciliations.changed_at END""")


@pg
async def test_the_previous_releases_audrey_statement_runs_on_the_new_schema():
    conn = await TE._conn()
    tx = conn.transaction()
    await tx.start()
    try:
        g = "g366-prev-" + uuid.uuid4().hex[:6]
        # a group the current release recorded as STALE ...
        await conn.execute(
            "INSERT INTO smalllive_reconciliations (group_id, venue, status,"
            " stale_reason, discrepancies, chain) VALUES ($1, 'POLYMARKET',"
            " 'PENDING', $2, '[]'::jsonb, '{}'::jsonb)", g, CODE)
        assert (await _rec(conn, g))["read_status"] == "STALE"
        # ... and the previous release, on its next pass, rewrites it
        await conn.execute(PREVIOUS_RELEASE_AUDREY_UPSERT, g, "POLYMARKET",
                           "MATCHED", "[]", "{}")
        r = await _rec(conn, g)
        assert r["status"] == "MATCHED"
        assert r["stale_reason"] == CODE          # it knows nothing of it
        assert r["read_status"] == "MATCHED"      # and it is never read as STALE
        # a group it writes for the first time: no reason, no complaint
        g2 = "g366-prev2-" + uuid.uuid4().hex[:6]
        await conn.execute(PREVIOUS_RELEASE_AUDREY_UPSERT, g2, "POLYMARKET",
                           "NOT_MIRRORED", "[]", "{}")
        r2 = await _rec(conn, g2)
        assert (r2["status"], r2["stale_reason"], r2["read_status"]) == (
            "NOT_MIRRORED", None, "NOT_MIRRORED")
        # ... and the current release then rewrites both columns
        await conn.execute(
            """INSERT INTO smalllive_reconciliations
                 (group_id, venue, status, stale_reason, discrepancies, chain)
               VALUES ($1,'POLYMARKET','PENDING',$2,'[]'::jsonb,'{}'::jsonb)
               ON CONFLICT (group_id) DO UPDATE SET status = EXCLUDED.status,
                 stale_reason = EXCLUDED.stale_reason""", g, CODE)
        assert (await _rec(conn, g))["read_status"] == "STALE"
        # what the previous release's readers meet: PENDING, not MATCHED, not
        # DISCREPANCY (runtime_slo wants MATCHED; agent_work, karen_runner and
        # the quorum act on DISCREPANCY only)
        assert (await conn.fetchrow(
            "SELECT status FROM smalllive_reconciliations WHERE group_id=$1",
            g))["status"] == "PENDING"
    finally:
        await tx.rollback()
        await conn.close()


# ───────────────────────── the claim with the table gone ──────────────────

@pg
async def test_the_claim_fails_closed_small_on_a_schema_without_the_cap_table(
        monkeypatch):
    """A release rolled back to before 366 (the table dropped by the rollback
    script) while this code still runs: the claim does not fail and does not
    open: the cap is the per-order cap. A filled $1.10 holding plus a second
    $1.10 order is $2.20 > $2.00: refused by name."""
    conn = await TE._conn()
    tx = conn.transaction()
    await tx.start()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch, cap=2)
        await conn.execute(DOWN_SQL)
        a = await TE._paper_order(conn, acct, qty=2000)
        b = await TE._paper_order(conn, acct, qty=2000)
        await mirror.snapshot(conn, await M.control(conn))
        venue.behaviour = [{"fill": 2}]
        assert (await mirror.lane._run(conn, a["intent_id"]))["state"] == \
            "SUBMITTED"
        rb = await mirror.lane._run(conn, b["intent_id"])
        assert rb["state"] == "REFUSED"
        assert rb["refusal"] == "ACTUAL_OPEN_AND_HELD_NOTIONAL_ABOVE_CAP"
        assert Decimal(rb["cap_usd"]) == Decimal("2.00")
        assert rb["cap_basis"] == M.CAP_BASIS_DEFAULT
        assert len(venue.placed) == 1
    finally:
        await tx.rollback()
        await conn.close()
