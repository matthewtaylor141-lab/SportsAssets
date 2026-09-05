"""The retention loop touches exactly two tables, in bounded batches,
under a cap, behind a switch -- and refuses, recording why, whenever it
cannot establish that.

The full-disk outage of 2026-09-04/05 (sportsassets-db at 15 GB, the
hostname gone for ~15 hours) is what this loop exists for; a loop that
DELETES is also the one place in the workers where a bug is
irreversible, so every pin here is against the REAL functions with a
pool that records every statement it is handed:

  * only ai_trades and copy_probes are ever named -- in the pinned
    pair, in the module's source, and in the SQL a cycle executes
  * the windows are read off the consumers (app.py's own Query bounds),
    never below them; an env may raise one, and one that cannot be
    read or sits below the floor refuses the whole cycle
  * every DELETE is a bounded batch with a statement timeout and a
    sleep between batches; the per-cycle cap stops a cycle and says so
  * RETENTION=off is read every cycle, including inside main()
  * an unreadable clock or a plan that is not the pinned pair deletes
    nothing and records the reason
  * the state row (and its heartbeat copy) is written on refusal too,
    and a database that refuses the row does not crash the loop
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets.workers import retention as R

HERE = pathlib.Path(__file__).resolve().parent
APP = HERE.parent / "sportsassets" / "api" / "app.py"
MIG_DIR = HERE.parent / "migrations"

NOW = datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc)

IDX_006 = "CREATE INDEX ai_trades_placed_idx ON public.ai_trades USING btree (placed_at DESC)"
IDX_005 = ("CREATE INDEX copy_probes_whale_idx ON public.copy_probes USING btree "
           "(whale_id, probe_at DESC)")
IDX_053 = "CREATE INDEX copy_probes_probe_at_idx ON public.copy_probes USING btree (probe_at)"


class _Pool:
    """Records every statement and serves a scripted number of
    deletable rows per table, so the batches, the cap and the cutoff
    the real code sends are what the assertions read."""

    def __init__(self, rows=None, indexes=None, oldest=None,
                 fail_delete_at: int | None = None, fail_state=False,
                 db_now=NOW):
        self.rows = dict(rows or {})
        self.indexes = indexes if indexes is not None else {
            "ai_trades": [IDX_006], "copy_probes": [IDX_005, IDX_053]}
        self.oldest = dict(oldest or {})
        self.fail_delete_at = fail_delete_at
        self.fail_state = fail_state
        self.db_now = db_now                  # what SELECT now() answers
        self.execs: list[tuple] = []
        self.fetches: list[tuple] = []
        self.state: dict = {}

    async def execute(self, sql, *a, **kw):
        self.execs.append((sql, a, kw))
        if sql.startswith("DELETE FROM "):
            # the Nth DELETE of the cycle (1-based) times out, once
            if self.fail_delete_at is not None and len(self.deletes()) == self.fail_delete_at:
                raise RuntimeError("canceling statement due to statement timeout")
            table = sql.split()[2]
            got = min(int(a[1]), self.rows.get(table, 0))
            self.rows[table] = self.rows.get(table, 0) - got
            return f"DELETE {got}"
        if sql.startswith("INSERT INTO ingestion_state"):
            if self.fail_state:
                raise RuntimeError("could not connect to Postgres")
            self.state[a[0]] = json.loads(a[1])
            return "INSERT 0 1"
        raise AssertionError(f"unexpected write: {sql}")

    async def fetch(self, sql, *a, **kw):
        self.fetches.append((sql, a, kw))
        if "pg_indexes" in sql:
            return [{"indexdef": d} for d in self.indexes.get(a[0], [])]
        raise AssertionError(f"unexpected fetch: {sql}")

    async def fetchval(self, sql, *a, **kw):
        self.fetches.append((sql, a, kw))
        if sql == R.CLOCK_SQL:
            return self.db_now
        m = re.match(r"SELECT (\w+) FROM (\w+) ORDER BY \1 ASC LIMIT 1$", sql)
        if m:
            return self.oldest.get(m.group(2))
        raise AssertionError(f"unexpected fetchval: {sql}")

    def deletes(self, table: str | None = None) -> list[tuple]:
        return [e for e in self.execs
                if e[0].startswith("DELETE FROM ")
                and (table is None or e[0].split()[2] == table)]


@pytest.fixture
def harness(monkeypatch):
    slept: list[float] = []
    beats: list[tuple] = []

    async def _sleep(s):
        slept.append(s)

    async def _hb(service, status="ok", detail=None):
        beats.append((service, status, detail))

    monkeypatch.setattr(R, "_sleep", _sleep)
    monkeypatch.setattr(R, "heartbeat", _hb)
    monkeypatch.setattr(R, "_now", lambda: NOW)
    for k in ("RETENTION", "RETENTION_AI_TRADES_DAYS", "RETENTION_COPY_PROBES_DAYS"):
        monkeypatch.delenv(k, raising=False)
    return {"slept": slept, "beats": beats}


def _run(pool, env=None):
    return asyncio.run(R.run_once(pool, env=env if env is not None else {}))


# ------------------------------------------------------ only two tables

def test_the_pinned_pair_is_exactly_ai_trades_and_copy_probes():
    assert R.PINNED == frozenset({("ai_trades", "placed_at"), ("copy_probes", "probe_at")})
    assert {(t, c) for t, c, *_ in R.TABLES} == R.PINNED
    # the columns are the ones the migrations declare (005, 006)
    assert "placed_at" in MIG_DIR.joinpath("006_ai_trades.sql").read_text()
    assert "probe_at" in MIG_DIR.joinpath("005_copy_probes.sql").read_text()


def test_the_module_source_names_no_other_table():
    """Every FROM/INTO/UPDATE/JOIN/TABLE target in the source -- code,
    comments and docstring alike -- is one of the two tables, the state
    row, or the catalog view the index check reads; the DELETE is an
    f-string over the plan, so the plan's own identifiers are checked
    against every table the migrations create, and only the pinned pair
    plus the state row survive. Nothing is truncated or dropped."""
    src = pathlib.Path(R.__file__).read_text()
    named = set(re.findall(r"\b(?:FROM|INTO|UPDATE|JOIN|TABLE)\s+([a-z_]+)\b", src))
    allowed = {"ai_trades", "copy_probes", "ingestion_state", "pg_indexes"}
    assert named <= allowed, named - allowed
    # the SQL-shaped f-strings interpolate the plan and nothing else
    sql_fstrings = [s for s in re.findall(r'f"([^"]*)"', src) if "FROM" in s]
    assert sql_fstrings, "the DELETE and the oldest read are f-strings over the plan"
    for s in sql_fstrings:
        assert set(re.findall(r"\{(\w+)\}", s)) <= {"table", "ts_col"}, s
    # every table the migrations create, against every quoted identifier
    # in the module: the two, the state row, nothing else
    created = set()
    for p in MIG_DIR.glob("*.sql"):
        created |= set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", p.read_text()))
    assert {"trades", "live_orders", "ai_trades", "copy_probes"} <= created   # the scan works
    quoted = set(re.findall(r'"([a-z_]+)"', src)) | named
    assert quoted & created == {"ai_trades", "copy_probes", "ingestion_state"}, quoted & created
    assert not re.search(r"\b(TRUNCATE|DROP)\b", src)


def test_a_full_cycle_deletes_from_the_two_tables_only(harness):
    pool = _Pool(rows={"ai_trades": 100, "copy_probes": 50})
    out = _run(pool)
    assert out["refused"] is None
    targets = {e[0].split()[2] for e in pool.deletes()}
    assert targets == {"ai_trades", "copy_probes"}
    for sql, _a, _kw in pool.deletes():
        assert re.fullmatch(
            r"DELETE FROM (ai_trades|copy_probes) WHERE id IN "
            r"\(SELECT id FROM \1 WHERE (placed_at|probe_at) < \$1 "
            r"ORDER BY \2 LIMIT \$2\)", sql), sql
    assert out["deleted_total"] == 150


def test_a_plan_that_is_not_the_pinned_pair_deletes_nothing(harness, monkeypatch):
    monkeypatch.setattr(R, "TABLES", R.TABLES + (("trades", "ts", 1, "X"),))
    pool = _Pool(rows={"ai_trades": 100, "copy_probes": 50, "trades": 9})
    out = _run(pool)
    assert out["refused"].startswith("plan does not match the pinned pair")
    assert pool.deletes() == []
    assert pool.state["retention_last"]["refused"] == out["refused"]
    # and the builders refuse a stray pair on their own
    with pytest.raises(R.Refusal):
        R.delete_sql("trades", "ts")
    with pytest.raises(R.Refusal):
        R.oldest_sql("live_orders", "placed_at")


# ------------------------------------------------- windows from consumers

def test_the_floors_cover_what_the_consumers_can_ask_for():
    """Read off app.py itself, so raising an endpoint's bound without
    raising the floor fails here rather than in the served window."""
    src = APP.read_text()
    m = re.search(r"async def ai_trader_report\(days: int = Query\(\d+, le=(\d+)\)", src)
    assert m, "ai_trader_report's days bound moved"
    ai_max_days = int(m.group(1))
    m = re.search(r"async def copy_report\(.*?hours: int = Query\(\d+, le=([\d\s*]+)\)", src)
    assert m, "copy_report's hours bound moved"
    probes_max_days = eval(m.group(1)) / 24  # noqa: S307 -- a literal like 24 * 30
    assert R.AI_TRADES_FLOOR_DAYS >= ai_max_days
    assert R.COPY_PROBES_FLOOR_DAYS >= probes_max_days
    # the probe's own asks sit inside the floors
    wf = HERE.parents[1] / ".github" / "workflows" / "engine-diagnostic.yml"
    if wf.exists():
        w = wf.read_text()
        assert "ai-trader?days=30" in w and "copy-report?hours=720" in w
        assert R.AI_TRADES_FLOOR_DAYS >= 30 and R.COPY_PROBES_FLOOR_DAYS >= 720 / 24


def test_the_cutoff_is_the_floor_back_from_the_cycles_clock(harness):
    pool = _Pool(rows={"ai_trades": 1, "copy_probes": 1})
    out = _run(pool)
    (_s, a, _k), = pool.deletes("ai_trades")
    assert a[0] == NOW - timedelta(days=R.AI_TRADES_FLOOR_DAYS)
    (_s, a, _k), = pool.deletes("copy_probes")
    assert a[0] == NOW - timedelta(days=R.COPY_PROBES_FLOOR_DAYS)
    assert out["tables"]["ai_trades"]["keep_days"] == R.AI_TRADES_FLOOR_DAYS
    assert out["tables"]["copy_probes"]["keep_days"] == R.COPY_PROBES_FLOOR_DAYS


def test_an_env_may_raise_a_window_but_never_lower_it(harness):
    pool = _Pool(rows={"ai_trades": 1, "copy_probes": 1})
    out = _run(pool, env={"RETENTION_AI_TRADES_DAYS": "120"})
    assert out["refused"] is None
    (_s, a, _k), = pool.deletes("ai_trades")
    assert a[0] == NOW - timedelta(days=120)
    assert out["tables"]["ai_trades"]["keep_days"] == 120

    pool = _Pool(rows={"ai_trades": 1, "copy_probes": 1})
    out = _run(pool, env={"RETENTION_COPY_PROBES_DAYS": str(R.COPY_PROBES_FLOOR_DAYS - 1)})
    assert out["refused"].startswith("window below consumer floor")
    assert pool.deletes() == []


def test_an_unreadable_window_deletes_nothing_and_records_why(harness):
    pool = _Pool(rows={"ai_trades": 100, "copy_probes": 50})
    out = _run(pool, env={"RETENTION_AI_TRADES_DAYS": "ninety"})
    assert out["refused"].startswith("window unreadable")
    assert "RETENTION_AI_TRADES_DAYS" in out["refused"]
    assert pool.deletes() == []
    assert out["deleted_total"] == 0
    assert pool.state["retention_last"]["refused"] == out["refused"]
    assert [b[1] for b in harness["beats"]] == ["refused"]


def test_read_windows_alone_refuses_the_same_way():
    with pytest.raises(R.Refusal):
        R.read_windows({"RETENTION_COPY_PROBES_DAYS": "1"})
    with pytest.raises(R.Refusal):
        R.read_windows({"RETENTION_AI_TRADES_DAYS": "90.5"})
    # empty is unset, not zero
    assert R.read_windows({"RETENTION_AI_TRADES_DAYS": ""})["ai_trades"] == R.AI_TRADES_FLOOR_DAYS
    assert R.read_windows({}) == {"ai_trades": R.AI_TRADES_FLOOR_DAYS,
                                  "copy_probes": R.COPY_PROBES_FLOOR_DAYS}


# ---------------------------------------------------- batches and the cap

def test_batches_are_bounded_timed_out_and_paced(harness):
    pool = _Pool(rows={"ai_trades": 12_000, "copy_probes": 0})
    out = _run(pool)
    dels = pool.deletes("ai_trades")
    assert [a[1] for _s, a, _k in dels] == [R.BATCH_ROWS, R.BATCH_ROWS, R.BATCH_ROWS]
    assert all(a[1] <= R.BATCH_ROWS for _s, a, _k in dels)
    assert all("LIMIT $2" in s for s, _a, _k in dels)
    assert all(k.get("timeout") == R.STATEMENT_TIMEOUT_S for _s, _a, k in dels)
    # the third batch returned 2000 < 5000: drained, no fourth statement
    assert out["tables"]["ai_trades"] == {
        **out["tables"]["ai_trades"], "deleted": 12_000, "batches": 3, "capped": False}
    # a sleep between batches, none after the last
    assert harness["slept"].count(R.BATCH_SLEEP_S) == 2
    # the reads are bounded too (the clock read is a scalar, no LIMIT)
    for s, _a, k in pool.fetches:
        assert ("LIMIT" in s or s == R.CLOCK_SQL) and k.get("timeout") == R.STATEMENT_TIMEOUT_S, s


def test_the_cap_stops_the_cycle_and_says_so(harness, monkeypatch):
    monkeypatch.setattr(R, "MAX_ROWS_PER_CYCLE", 20_000)
    pool = _Pool(rows={"ai_trades": 1_000_000, "copy_probes": 1_000_000})
    out = _run(pool)
    # the cap is split across the pair so the first table cannot starve the second
    assert out["tables"]["ai_trades"]["deleted"] == 10_000
    assert out["tables"]["copy_probes"]["deleted"] == 10_000
    assert out["deleted_total"] == 20_000
    assert out["capped"] is True
    assert out["tables"]["ai_trades"]["capped"] is True
    assert len(pool.deletes()) == 4
    assert pool.state["retention_last"]["capped"] is True


def test_the_last_batch_never_exceeds_the_caps_remainder(harness, monkeypatch):
    monkeypatch.setattr(R, "MAX_ROWS_PER_CYCLE", 2 * 7_500)
    pool = _Pool(rows={"ai_trades": 1_000_000, "copy_probes": 0})
    out = _run(pool)
    assert [a[1] for _s, a, _k in pool.deletes("ai_trades")] == [5_000, 2_500]
    # the CYCLE is capped when the first table is, even though the
    # second drained -- this is the flag the probe prints, and 'last
    # table wins' would read 990,000 remaining rows as drained
    assert out["capped"] is True
    assert out["tables"]["ai_trades"]["capped"] is True
    assert out["tables"]["copy_probes"]["capped"] is False
    assert pool.state["retention_last"]["capped"] is True


# ------------------------------------------------------------- the switch

def test_retention_off_deletes_nothing_and_still_writes_the_row(harness):
    pool = _Pool(rows={"ai_trades": 100, "copy_probes": 50})
    out = _run(pool, env={"RETENTION": "off"})
    assert out["refused"] == "RETENTION=off"
    assert out["enabled"] is False
    assert pool.deletes() == []
    assert pool.state["retention_last"]["refused"] == "RETENTION=off"
    assert [b[1] for b in harness["beats"]] == ["off"]


def test_the_switch_is_read_from_the_environment_every_cycle(harness, monkeypatch):
    """main() itself: on for the first cycle, flipped off between
    cycles without a restart, and the second cycle deletes nothing."""
    pool = _Pool(rows={"ai_trades": 10, "copy_probes": 10})
    monkeypatch.setattr(R, "BOOT_DELAY_S", 0.0)

    async def _get_pool():
        return pool

    monkeypatch.setattr(R, "get_pool", _get_pool)
    cycles = {"n": 0}

    class _Stop(Exception):
        pass

    async def _sleep(s):
        if s == R.EVERY_S:
            cycles["n"] += 1
            if cycles["n"] == 1:
                monkeypatch.setenv("RETENTION", "off")
                pool.rows = {"ai_trades": 10, "copy_probes": 10}
            else:
                raise _Stop()

    monkeypatch.setattr(R, "_sleep", _sleep)
    with pytest.raises(_Stop):
        asyncio.run(R.main())
    assert cycles["n"] == 2
    assert len(pool.deletes()) == 2                      # cycle one only
    assert pool.state["retention_last"]["refused"] == "RETENTION=off"
    assert [b[1] for b in harness["beats"]] == ["ok", "off"]


def test_enabled_reads_the_live_environment(monkeypatch):
    monkeypatch.delenv("RETENTION", raising=False)
    assert R.enabled() is True
    monkeypatch.setenv("RETENTION", "off")
    assert R.enabled() is False
    monkeypatch.setenv("RETENTION", "on")
    assert R.enabled() is True


# ---------------------------------------------------------------- clock

def test_an_unreadable_clock_deletes_nothing(harness, monkeypatch):
    def _broken():
        raise OSError("clock_gettime failed")

    monkeypatch.setattr(R, "_now", _broken)
    pool = _Pool(rows={"ai_trades": 100, "copy_probes": 50})
    out = _run(pool)
    assert out["refused"].startswith("clock unreadable")
    assert pool.deletes() == []
    assert pool.state["retention_last"]["refused"].startswith("clock unreadable")
    # the row is still stamped, from a second reading
    assert pool.state["retention_last"]["at"]


def test_a_naive_clock_is_not_a_clock(harness, monkeypatch):
    monkeypatch.setattr(R, "_now", lambda: NOW.replace(tzinfo=None))
    pool = _Pool(rows={"ai_trades": 100, "copy_probes": 50})
    out = _run(pool)
    assert out["refused"] and "not an aware datetime" in out["refused"]
    assert pool.deletes() == []


# -------------------------------------------------------- the state row

def test_the_state_row_carries_the_numbers_the_probe_prints(harness):
    pool = _Pool(rows={"ai_trades": 7, "copy_probes": 3},
                 oldest={"ai_trades": NOW - timedelta(days=96),
                         "copy_probes": NOW - timedelta(days=36)})
    out = _run(pool)
    row = pool.state["retention_last"]
    assert row["at"] == NOW.isoformat(timespec="seconds")
    assert row["refused"] is None and row["deleted_total"] == 10
    assert row["tables"]["ai_trades"]["deleted"] == 7
    assert row["tables"]["copy_probes"]["deleted"] == 3
    assert row["tables"]["ai_trades"]["oldest_kept"] == (NOW - timedelta(days=96)).isoformat(timespec="seconds")
    assert row["tables"]["copy_probes"]["oldest_kept"] == (NOW - timedelta(days=36)).isoformat(timespec="seconds")
    assert isinstance(row["duration_s"], float)
    # the state write is itself bounded
    (s, a, k), = [e for e in pool.execs if e[0].startswith("INSERT INTO ingestion_state")]
    assert a[0] == "retention_last" and k.get("timeout") == R.STATEMENT_TIMEOUT_S
    # and the same payload rides the heartbeat the probe reads
    (svc, status, detail), = harness["beats"]
    assert (svc, status) == ("retention", "ok") and detail is out


def test_a_database_that_refuses_the_row_does_not_crash_the_cycle(harness):
    pool = _Pool(rows={"ai_trades": 1, "copy_probes": 1}, fail_state=True)
    out = _run(pool)                                  # no raise
    assert out["deleted_total"] == 2
    assert pool.state == {}
    assert [b[1] for b in harness["beats"]] == ["ok"]


def test_a_delete_that_times_out_mid_cycle_is_contained(harness):
    pool = _Pool(rows={"ai_trades": 20_000, "copy_probes": 5}, fail_delete_at=2)
    out = _run(pool)                                  # no raise
    blk = out["tables"]["ai_trades"]
    assert blk["deleted"] == 5_000 and blk["batches"] == 1
    assert "statement timeout" in blk["error"]
    assert out["refused"] is None                     # the cycle went on
    # one lost batch costs that table's remainder for THIS cycle, not
    # the other table and not the cycle
    assert out["tables"]["copy_probes"] == {
        **out["tables"]["copy_probes"], "deleted": 5, "error": None}
    assert out["deleted_total"] == 5_005
    assert pool.state["retention_last"]["tables"]["ai_trades"]["error"] == blk["error"]


def test_an_unreadable_delete_status_stops_the_table(harness, monkeypatch):
    class _Odd(_Pool):
        async def execute(self, sql, *a, **kw):
            if sql.startswith("DELETE FROM "):
                self.execs.append((sql, a, kw))
                return "OK"
            return await super().execute(sql, *a, **kw)

    pool = _Odd(rows={"ai_trades": 1, "copy_probes": 1})
    out = _run(pool)
    assert len(pool.deletes("ai_trades")) == 1
    assert "unreadable DELETE status" in out["tables"]["ai_trades"]["error"]


# --------------------------------------------------------------- indexes

def test_index_serves_reads_the_catalog_the_way_the_migrations_wrote_it():
    assert R.index_serves([IDX_006], "placed_at")
    assert R.index_serves([IDX_053], "probe_at")
    assert not R.index_serves([IDX_005], "probe_at"), "005's composite is led by whale_id"
    assert not R.index_serves([], "probe_at")


def test_a_table_without_its_index_is_skipped_with_the_reason(harness):
    pool = _Pool(rows={"ai_trades": 10, "copy_probes": 10},
                 indexes={"ai_trades": [IDX_006], "copy_probes": [IDX_005]})
    out = _run(pool)
    assert pool.deletes("copy_probes") == []
    assert out["tables"]["copy_probes"]["deleted"] == 0
    assert "no index leads with probe_at" in out["tables"]["copy_probes"]["error"]
    assert out["tables"]["ai_trades"]["deleted"] == 10


def test_migration_053_creates_the_probe_at_index_and_only_that():
    files = [p.name for p in sorted(MIG_DIR.glob("*.sql"))]
    (name,) = [f for f in files if f.startswith("053_")]
    assert files.index(name) > files.index("052_copy_exit_legs.sql")
    sql = MIG_DIR.joinpath(name).read_text()
    stmts = [s.strip() for s in re.sub(r"--[^\n]*", "", sql).split(";") if s.strip()]
    assert stmts == ["CREATE INDEX IF NOT EXISTS copy_probes_probe_at_idx ON copy_probes (probe_at)"]
    # migrate.py wraps each file in a transaction; CONCURRENTLY cannot run there
    assert "CONCURRENTLY" not in stmts[0]
    assert R.index_serves([IDX_053], "probe_at")


# ----------------------------------------------------------- registered

def test_retention_is_in_the_supervised_loops():
    """Membership in the IMPORTED list, by identity: a substring scan
    passed with the entry commented out (test-honesty review). The AST
    pin below holds in an environment without the optional push
    dependency too (the stub is the one test_edge_decomposition uses),
    and comments do not survive parsing."""
    import ast
    import sys
    import types

    src = pathlib.Path(R.__file__).with_name("all.py").read_text()
    values = []
    for n in ast.walk(ast.parse(src)):
        # LOOPS is annotated (`LOOPS: list[...] = [...]`), an AnnAssign
        target = (n.target if isinstance(n, ast.AnnAssign)
                  else n.targets[0] if isinstance(n, ast.Assign) and len(n.targets) == 1
                  else None)
        if getattr(target, "id", None) == "LOOPS" and n.value is not None:
            values.append(n.value)
    (value,) = values
    entries = [e for e in value.elts if isinstance(e, ast.Tuple)]
    names = [e.elts[0].value for e in entries]
    assert names.count("retention") == 1, names
    entry, = [e for e in entries if e.elts[0].value == "retention"]
    assert ast.unparse(entry.elts[1]) == "retention.main"

    sys.modules.setdefault("pywebpush", types.SimpleNamespace(
        webpush=None, WebPushException=Exception))
    from sportsassets.workers import all as all_mod

    assert ("retention", R.main) in all_mod.LOOPS
    assert [n for n, _fn in all_mod.LOOPS].count("retention") == 1


# ------------------------------------------------------------------ knobs

def test_read_knobs_defaults_raise_and_refuse():
    assert R.read_knobs({}) == {"every_s": R.EVERY_S, "batch": R.BATCH_ROWS,
                                "cap": R.MAX_ROWS_PER_CYCLE}
    assert R.read_knobs({"RETENTION_BATCH_ROWS": "250"})["batch"] == 250
    assert R.read_knobs({"RETENTION_EVERY_S": "900"})["every_s"] == 900.0
    # empty is unset, not zero
    assert R.read_knobs({"RETENTION_BATCH_ROWS": ""})["batch"] == R.BATCH_ROWS
    for env in ({"RETENTION_BATCH_ROWS": "0"}, {"RETENTION_BATCH_ROWS": "-1"},
                {"RETENTION_BATCH_ROWS": "5k"}, {"RETENTION_BATCH_ROWS": "nan"},
                {"RETENTION_MAX_ROWS_PER_CYCLE": "0"},
                {"RETENTION_EVERY_S": "1h"}, {"RETENTION_EVERY_S": "0"}):
        with pytest.raises(R.Refusal):
            R.read_knobs(env)


def test_a_batch_of_zero_refuses_rather_than_spins(harness, monkeypatch):
    """RETENTION_BATCH_ROWS=0 (an operator's obvious 'slow it to
    nothing') made the first build issue DELETE ... LIMIT 0 forever:
    got(0) < n(0) never drained, deleted never reached the cap, no
    state row, no heartbeat, and RETENTION=off could not stop a cycle
    that never ended (three reviewers reproduced it, 2026-09-05). The
    cycle now refuses before its first statement. The sleep here
    YIELDS so a regression is a timeout, not a hung suite."""
    async def _yield(_s):
        await asyncio.sleep(0)

    monkeypatch.setattr(R, "_sleep", _yield)
    pool = _Pool(rows={"ai_trades": 100, "copy_probes": 50})
    out = asyncio.run(asyncio.wait_for(
        R.run_once(pool, env={"RETENTION_BATCH_ROWS": "0"}), 2.0))
    assert out["refused"].startswith("knob below 1")
    assert "RETENTION_BATCH_ROWS" in out["refused"]
    assert pool.deletes() == []
    assert pool.state["retention_last"]["refused"] == out["refused"]
    assert [b[1] for b in harness["beats"]] == ["refused"]
    # and the belt inside prune_table, for a caller that bypasses run_once
    blk = asyncio.run(asyncio.wait_for(
        R.prune_table(_Pool(rows={"ai_trades": 100}), "ai_trades", "placed_at",
                      NOW, cap=10, batch=0), 2.0))
    assert blk["deleted"] == 0 and blk["batches"] == 0
    assert "below 1" in blk["error"]


def test_a_cap_too_small_for_the_pair_refuses(harness):
    pool = _Pool(rows={"ai_trades": 100, "copy_probes": 50})
    out = _run(pool, env={"RETENTION_MAX_ROWS_PER_CYCLE": "1"})
    assert out["refused"].startswith("cap below the pair")
    assert pool.deletes() == []


def test_the_knobs_are_not_parsed_at_import():
    """A malformed knob must refuse ONE cycle, not raise ValueError
    while workers/all.py imports this module and take every loop down
    with it (containment review 2026-09-05)."""
    src = pathlib.Path(R.__file__).read_text()
    assert not re.search(r"^\w+\s*=\s*(?:int|float)\(\s*os\.environ", src, re.M), \
        "a knob is converted at module scope"
    assert isinstance(R.EVERY_S, float) and isinstance(R.BATCH_ROWS, int)


# ------------------------------------------------------ the database's clock

def test_a_database_clock_that_disagrees_deletes_nothing(harness):
    """ai_trades rows are stamped by Postgres; the cutoff is subtracted
    from this container's clock. A container booted hours ahead would
    put the cutoff past rows the endpoints still serve, up to the cap,
    hour after hour -- the one input that turns a window into
    'everything' (money-safety review 2026-09-05). One bounded read of
    the database's now() per cycle; disagreement is a refusal that
    records both clocks."""
    ahead = NOW + timedelta(seconds=R.CLOCK_SKEW_MAX_S + 1)
    pool = _Pool(rows={"ai_trades": 100, "copy_probes": 50}, db_now=ahead)
    out = _run(pool)
    assert out["refused"].startswith("clock skew")
    assert pool.deletes() == []
    row = pool.state["retention_last"]
    assert row["db_clock"] == ahead.isoformat(timespec="seconds")
    assert row["clock_skew_s"] == pytest.approx(R.CLOCK_SKEW_MAX_S + 1)
    # inside the bound the cycle runs and says what it measured
    pool = _Pool(rows={"ai_trades": 1, "copy_probes": 1},
                 db_now=NOW - timedelta(seconds=30))
    out = _run(pool)
    assert out["refused"] is None and out["clock_skew_s"] == -30.0
    assert out["deleted_total"] == 2
    # the read is bounded and happens before the first DELETE
    (s, _a, k), = [f for f in pool.fetches if f[0] == R.CLOCK_SQL]
    assert k.get("timeout") == R.STATEMENT_TIMEOUT_S


def test_an_unreadable_database_clock_deletes_nothing(harness):
    class _NoClock(_Pool):
        async def fetchval(self, sql, *a, **kw):
            if sql == R.CLOCK_SQL:
                raise RuntimeError("canceling statement due to statement timeout")
            return await super().fetchval(sql, *a, **kw)

    pool = _NoClock(rows={"ai_trades": 100, "copy_probes": 50})
    out = _run(pool)
    assert out["refused"].startswith("db clock unreadable")
    assert pool.deletes() == []
    pool = _Pool(rows={"ai_trades": 100, "copy_probes": 50}, db_now="2026-09-05")
    out = _run(pool)
    assert out["refused"].startswith("db clock unreadable")
    assert pool.deletes() == []
