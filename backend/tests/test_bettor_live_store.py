"""Durable storage: the policy, the schema, and a real database.

THE DEFECT THIS FILE PINS. `/var/tmp/bettor` passed every fsync and
every atomic replace and lost everything on the next redeploy. Neither
`fsync()` nor `os.replace()` makes ephemeral storage persistent, and no
test asserted the difference because the tests only ever checked that
bytes reached a file.

The Postgres tests run against a REAL PostgreSQL server when
`BETTOR_TEST_PG_DSN` names one, and are skipped otherwise. Skipping is
stated rather than hidden: `scripts/bettor_durability_proof.py` runs
the same path against a real server and kills the process between the
write and the read, which is the event `/var/tmp` cannot survive.
"""
from __future__ import annotations

import asyncio
import json
import os
import re

import pytest

from sportsassets import bettor_live_store as st

PG_DSN = os.environ.get("BETTOR_TEST_PG_DSN")
needs_pg = pytest.mark.skipif(not PG_DSN,
                              reason="BETTOR_TEST_PG_DSN names no server")


def cur(**kw):
    base = {"first_seen_at": 0.0, "last_seen_at": 0.0,
            "last_source_ts": None, "last_decided_at": None,
            "settle_status": None, "settle_attempts": 0,
            "settle_next_at": None, "settle_last_at": None}
    base.update(kw)
    return base


# ── 1. the schema cannot drift from the migration ────────────────────

class TestTheSchemaIsOneText:
    """The worker creates its own tables because the workers never run
    migrations. A migration that drifted from the code would describe
    a schema production does not have."""

    def test_the_migration_holds_the_same_ddl_character_for_character(self):
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.normpath(os.path.join(
            here, "..", "migrations", "093_bettor_live_observation.sql"))
        text = open(path).read()
        assert st.DDL in text, "093 and bettor_live_store.DDL have drifted"

    def test_the_ddl_is_additive_only(self):
        """No ALTER, no DROP, no TRUNCATE: a schema step that could
        remove something is a schema step that could remove someone
        else's something."""
        upper = st.DDL.upper()
        for forbidden in ("ALTER ", "DROP ", "TRUNCATE", "DELETE ",
                          "UPDATE "):
            assert forbidden not in upper

    def test_lane_is_part_of_every_key(self):
        """Retail and institutional, preprod and production, must never
        blend. A lane column you have to remember to filter on is a
        convention; a lane in the primary key is a constraint."""
        assert "PRIMARY KEY (lane, market_id)" in st.DDL
        assert "lane          TEXT        PRIMARY KEY" in st.DDL
        assert "lane          TEXT        NOT NULL" in st.DDL


class TestNoOtherTableIsTouched:
    """Asserted over the EXECUTABLE SQL, not over the prose. The
    docstrings name the tables this module must never write precisely
    so a reader knows the boundary; a grep over the whole source would
    therefore fail on its own documentation, and the next person would
    delete the documentation rather than keep the boundary."""

    def _sql_strings(self):
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(st))
        docstrings = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                              ast.AsyncFunctionDef)):
                body = getattr(n, "body", None) or []
                if body and isinstance(body[0], ast.Expr) and isinstance(
                        getattr(body[0], "value", None), ast.Constant):
                    docstrings.add(id(body[0].value))
        out = []
        for n in ast.walk(tree):
            if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and id(n) not in docstrings):
                v = n.value.upper()
                if any(k in v for k in ("SELECT ", "INSERT ", "DELETE ",
                                        "UPDATE ", "CREATE TABLE")):
                    out.append(n.value)
        return out

    def test_the_sql_is_found_at_all(self):
        """Guard the guard: a test that finds no SQL asserts nothing."""
        assert len(self._sql_strings()) >= 5

    def test_every_table_named_in_sql_is_one_of_ours(self):
        # `excluded` is the UPSERT alias; `set` is the keyword in
        # "DO UPDATE SET", which the UPDATE branch of the pattern sees
        # as a table name. Neither is a table.
        allowed = set(st.OWN_TABLES) | {"excluded", "set"}
        names = set()
        for sql in self._sql_strings():
            for m in re.finditer(
                    r"\b(?:INSERT\s+INTO|FROM|DELETE\s+FROM|UPDATE|JOIN|"
                    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+"
                    r"([a-z_][a-z0-9_]*)", sql, re.IGNORECASE):
                names.add(m.group(1).lower())
        assert names, "no table names parsed out of the SQL"
        assert names <= allowed, (
            "the store names a table that is not its own: %s"
            % (names - allowed))

    def test_no_accounting_table_appears_in_any_statement(self):
        joined = " ".join(self._sql_strings())
        for forbidden in ("ai_trades", "bettor_state_settlements",
                          "bettor_state_observations", "copy_probes",
                          "schema_migrations"):
            assert forbidden not in joined


# ── 2. the settlement schedule makes progress ────────────────────────

class TestSettlementSchedulingMakesProgress:
    """THE DEFECT: reading "the 25 oldest observed" means a contract
    that never resolves is permanently the oldest, so a pending
    population holds the batch forever and a market that settled an
    hour ago is never read."""

    def test_a_pending_population_cannot_monopolize_the_batch(self):
        now = 1000.0
        cursors = {"p%03d" % i: cur(first_seen_at=i) for i in range(100)}
        first = st.due_for_settlement(cursors, now=now, limit=25)
        assert len(first) == 25

        # Every one answers PENDING and backs off.
        for s in first:
            t = st.settle_transition(st.SETTLE_PENDING,
                                     cursors[s]["settle_attempts"], now=now)
            t.pop("retired")
            cursors[s].update(t)

        second = st.due_for_settlement(cursors, now=now, limit=25)
        assert not set(second) & set(first), (
            "a contract that just answered PENDING was read again in the "
            "very next pass")

    def test_a_new_contract_is_read_in_the_next_pass_however_many_pend(self):
        """The progress property, stated as a test: never-attempted
        contracts sort ahead of every attempted one."""
        now = 1000.0
        cursors = {"p%03d" % i: cur(first_seen_at=i,
                                    settle_status=st.SETTLE_PENDING,
                                    settle_attempts=1,
                                    settle_next_at=now - 1)   # all DUE
                   for i in range(500)}
        cursors["fresh"] = cur(first_seen_at=now)
        due = st.due_for_settlement(cursors, now=now, limit=25)
        assert due[0] == "fresh"

    def test_every_contract_is_eventually_attempted(self):
        """No starvation: run the scheduler until the tracked set is
        exhausted and assert the set of attempted slugs is the whole
        population."""
        now = 1000.0
        n = 120
        cursors = {"m%03d" % i: cur(first_seen_at=i) for i in range(n)}
        attempted: set = set()
        for _ in range(64):
            due = st.due_for_settlement(cursors, now=now, limit=25)
            if not due:
                now += 600.0
                continue
            attempted.update(due)
            for s in due:
                t = st.settle_transition(st.SETTLE_PENDING,
                                         cursors[s]["settle_attempts"],
                                         now=now)
                t.pop("retired")
                cursors[s].update(t)
            now += 1.0
        assert attempted == set(cursors), "some contract was never read"

    def test_an_authoritative_resolution_is_retired(self):
        now = 1000.0
        c = cur()
        t = st.settle_transition(st.SETTLE_RESOLVED, 0, now=now)
        assert t["retired"] is True
        assert t["settle_next_at"] is None
        t.pop("retired")
        c.update(t)
        assert st.due_for_settlement({"m": c}, now=now + 10 ** 9,
                                     limit=25) == []

    def test_a_derived_resolution_is_retired_too_but_stays_distinct(self):
        t = st.settle_transition(st.SETTLE_RESOLVED_DERIVED, 3, now=0.0)
        assert t["retired"] is True
        assert t["settle_status"] == st.SETTLE_RESOLVED_DERIVED
        assert t["settle_status"] != st.SETTLE_RESOLVED

    def test_retries_back_off_and_are_capped(self):
        delays = [st.settle_backoff_s(i) for i in range(0, 20)]
        assert delays[0] == st.SETTLE_BASE_S
        assert delays == sorted(delays)
        assert max(delays) == st.SETTLE_MAX_S

    def test_a_contract_that_never_reads_is_abandoned_by_name(self):
        """Not silently retried forever: a slug we can never read would
        otherwise consume its share of the budget indefinitely."""
        now, attempts, status = 0.0, 0, None
        for _ in range(st.SETTLE_MAX_ATTEMPTS + 2):
            t = st.settle_transition(st.SETTLE_UNREADABLE, attempts, now=now)
            attempts, status = t["settle_attempts"], t["settle_status"]
            if t["retired"]:
                break
        assert status == st.SETTLE_ABANDONED
        assert attempts == st.SETTLE_MAX_ATTEMPTS

    def test_terminal_contracts_are_never_candidates(self):
        cursors = {"a": cur(settle_status=st.SETTLE_RESOLVED),
                   "b": cur(settle_status=st.SETTLE_ABANDONED),
                   "c": cur()}
        assert st.due_for_settlement(cursors, now=0.0, limit=9) == ["c"]


# ── 3. bounded cursors ───────────────────────────────────────────────

class TestCursorsAreBounded:

    def test_eviction_prefers_terminal_cursors(self):
        cursors = {"keep": cur(last_seen_at=1.0),
                   "done": cur(last_seen_at=999.0,
                               settle_status=st.SETTLE_RESOLVED)}
        out = st.evict_to(cursors, 1)
        assert out["evicted"] == 1
        assert "keep" in cursors and "done" not in cursors
        assert out["evicted_unresolved"] == 0

    def test_evicting_an_unresolved_cursor_is_counted_separately(self):
        cursors = {"a": cur(last_seen_at=1.0), "b": cur(last_seen_at=2.0)}
        out = st.evict_to(cursors, 1)
        assert out["evicted"] == 1 and out["evicted_unresolved"] == 1

    def test_under_the_cap_nothing_moves(self):
        cursors = {"a": cur()}
        assert st.evict_to(cursors, 10) == {"evicted": 0,
                                            "evicted_unresolved": 0}
        assert cursors


# ── 4. choosing a backend ────────────────────────────────────────────

class TestChoosingAStoreRefusesEphemeralByDefault:

    def test_the_default_backend_is_postgres(self, monkeypatch):
        monkeypatch.delenv("BETTOR_LIVE_STATE", raising=False)
        got = st.choose()
        assert got["ok"] is True
        assert got["backend"] == st.BACKEND_PG
        assert "redeploy" in got["store"].durable_across

    def test_a_file_path_that_is_not_a_declared_disk_is_refused(
            self, monkeypatch, tmp_path):
        """THE WHOLE POINT. /var/tmp passes fsync and dies on deploy."""
        monkeypatch.setenv("BETTOR_LIVE_STATE", "file")
        monkeypatch.setenv("BETTOR_LIVE_STATE_DIR", str(tmp_path))
        monkeypatch.delenv("BETTOR_LIVE_STATE_DISK", raising=False)
        monkeypatch.delenv(st.ALLOW_EPHEMERAL_ENV, raising=False)
        got = st.choose()
        assert got["ok"] is False
        assert "redeploy" in got["why"]

    def test_a_declared_disk_is_accepted_and_says_what_it_survives(
            self, monkeypatch, tmp_path):
        monkeypatch.setenv("BETTOR_LIVE_STATE", "file")
        monkeypatch.setenv("BETTOR_LIVE_STATE_DIR", str(tmp_path))
        monkeypatch.setenv("BETTOR_LIVE_STATE_DISK", "1")
        got = st.choose()
        assert got["ok"] is True
        assert "redeploy" in got["store"].durable_across

    def test_memory_must_be_chosen_deliberately(self, monkeypatch):
        monkeypatch.setenv("BETTOR_LIVE_STATE", "memory")
        monkeypatch.delenv(st.ALLOW_EPHEMERAL_ENV, raising=False)
        assert st.choose()["ok"] is False
        monkeypatch.setenv(st.ALLOW_EPHEMERAL_ENV, "1")
        got = st.choose()
        assert got["ok"] is True and got["store"].durable_across == ()

    def test_an_unknown_backend_is_named(self, monkeypatch):
        monkeypatch.setenv("BETTOR_LIVE_STATE", "s3")
        got = st.choose()
        assert got["ok"] is False and "unknown" in got["why"]


# ── 5. the file backend ──────────────────────────────────────────────

class TestTheFileBackend:

    def store(self, tmp_path):
        return st.FileStore(str(tmp_path), durable_across_redeploy=True)

    def test_records_and_cursors_round_trip(self, tmp_path):
        s = self.store(tmp_path)
        asyncio.run(s.start())
        out = asyncio.run(s.flush([{"market_id": "m1", "kind": "DECISION"}],
                                  {"m1": cur(last_source_ts="T1")}))
        assert out == {"records": 1, "cursors": 1, "failures": 0,
                       "rotated": 0}
        got = asyncio.run(self.store(tmp_path).load())
        assert got["cursors"]["m1"]["last_source_ts"] == "T1"

    def test_recovery_does_not_read_the_journal(self, tmp_path):
        """Restart time is bounded by markets, not by record count."""
        s = self.store(tmp_path)
        asyncio.run(s.start())
        asyncio.run(s.flush([{"market_id": "m%d" % i} for i in range(5000)],
                            {"m1": cur()}))
        got = asyncio.run(self.store(tmp_path).load())
        assert len(got["cursors"]) == 1

    def test_a_missing_cursor_file_recovers_nothing_and_does_not_raise(
            self, tmp_path):
        got = asyncio.run(self.store(tmp_path).load())
        assert got["cursors"] == {} and got["cursor_error"] is None

    def test_a_corrupt_cursor_file_is_named(self, tmp_path):
        s = self.store(tmp_path)
        asyncio.run(s.start())
        open(s.cursor_path, "w").write("{not json")
        got = asyncio.run(s.load())
        assert got["cursor_error"] == "JSONDecodeError"

    def test_another_lanes_cursor_file_is_refused(self, tmp_path):
        s = self.store(tmp_path)
        asyncio.run(s.start())
        open(s.cursor_path, "w").write(json.dumps(
            {"lane": "polymarket-us/retail/whatever", "cursors": {"m": {}}}))
        got = asyncio.run(s.load())
        assert got["cursor_error"] == "LANE_MISMATCH"
        assert got["cursors"] == {}

    def test_the_journal_rotates_so_the_disk_is_bounded(self, tmp_path):
        s = st.FileStore(str(tmp_path), max_bytes=2048,
                         durable_across_redeploy=True)
        asyncio.run(s.start())
        for _ in range(6):
            asyncio.run(s.flush([{"market_id": "m", "pad": "x" * 200}
                                 for _ in range(10)], {}))
        assert os.path.exists(s.journal + ".1"), "the journal never rotated"
        # ONE generation is kept, so the disk cost is two files of at
        # most max_bytes plus the batch that crossed the line -- a
        # bound, not a hope. Rotation renames the live file away, so
        # after the last flush there may be no live file at all.
        live = os.path.getsize(s.journal) if os.path.exists(s.journal) else 0
        assert live + os.path.getsize(s.journal + ".1") < 4 * s.max_bytes
        assert not os.path.exists(s.journal + ".2")

    def test_a_write_failure_is_counted_not_raised(self, tmp_path):
        s = st.FileStore(str(tmp_path / "nope" / "deeper"),
                         durable_across_redeploy=True)
        s.journal = str(tmp_path / "missing" / "d.jsonl")
        out = asyncio.run(s.flush([{"market_id": "m"}], {}))
        assert out["failures"] == 1 and out["records"] == 0

    def test_an_undeclared_path_warns_in_its_own_start_result(self, tmp_path):
        s = st.FileStore(str(tmp_path))
        got = asyncio.run(s.start())
        assert "redeploy" in got["warning"]
        assert got["durable_across"] == ["restart"]


# ── 6. a real database ───────────────────────────────────────────────

@needs_pg
class TestThePostgresBackend:
    """Against a REAL PostgreSQL server. Skipped without
    BETTOR_TEST_PG_DSN; `scripts/bettor_durability_proof.py` runs the
    same path and kills the process between write and read."""

    def fresh(self):
        async def go():
            s = st.PgStore(dsn=PG_DSN)
            await s.start()
            pool = await s._get_pool()
            async with pool.acquire() as con:
                for t in st.OWN_TABLES:
                    await con.execute("DELETE FROM %s WHERE lane = $1" % t,
                                      st.LANE)
            await s.close()
        asyncio.run(go())

    def test_the_schema_creates_itself_idempotently(self):
        async def go():
            for _ in range(2):
                s = st.PgStore(dsn=PG_DSN)
                out = await s.start()
                assert out["ok"] is True
                await s.close()
        asyncio.run(go())

    def test_records_cursors_and_ledger_survive_a_new_store_object(self):
        self.fresh()

        async def go():
            a = st.PgStore(dsn=PG_DSN)
            await a.start()
            await a.flush([{"loop": "L", "kind": "DECISION",
                            "market_id": "m1", "source_ts": "T1",
                            "status": "DECIDED", "selected": "NO_TRADE"}],
                          {"m1": cur(last_source_ts="T1")})
            await a.save_ledger('{"cash": 12.5}')
            await a.close()

            b = st.PgStore(dsn=PG_DSN)
            await b.start()
            got = await b.load()
            await b.close()
            return got

        got = asyncio.run(go())
        assert got["cursors"]["m1"]["last_source_ts"] == "T1"
        assert json.loads(got["ledger"])["cash"] == 12.5
        assert got["journal_rows"] == 1

    def test_recovery_is_bounded_by_cursors_not_by_journal_rows(self):
        self.fresh()

        async def go():
            s = st.PgStore(dsn=PG_DSN)
            await s.start()
            await s.flush([{"loop": "L", "kind": "DECISION",
                            "market_id": "m%d" % (i % 3)}
                           for i in range(3000)],
                          {"m%d" % i: cur() for i in range(3)})
            got = await s.load()
            await s.close()
            return got

        got = asyncio.run(go())
        assert got["journal_rows"] == 3000
        assert len(got["cursors"]) == 3
        assert got["load_seconds"] < 2.0

    def test_the_journal_is_pruned_to_its_row_cap(self):
        self.fresh()

        async def go():
            s = st.PgStore(dsn=PG_DSN)
            await s.start()
            await s.flush([{"loop": "L", "kind": "DECISION",
                            "market_id": "m"} for _ in range(400)], {})
            pruned = await s.prune(max_rows=100, cursor_retention_days=7)
            got = await s.load()
            await s.close()
            return pruned, got

        pruned, got = asyncio.run(go())
        assert pruned["journal_rows_deleted"] == 300
        assert got["journal_rows"] == 100

    def test_stale_cursors_are_pruned_by_age(self):
        self.fresh()

        async def go():
            import time
            s = st.PgStore(dsn=PG_DSN)
            await s.start()
            await s.flush([], {"old": cur(last_seen_at=time.time() - 30 * 86400),
                               "new": cur(last_seen_at=time.time())})
            pruned = await s.prune(max_rows=10 ** 9, cursor_retention_days=7)
            got = await s.load()
            await s.close()
            return pruned, got

        pruned, got = asyncio.run(go())
        assert pruned["cursors_deleted"] == 1
        assert set(got["cursors"]) == {"new"}

    def test_a_failed_flush_is_counted_rather_than_raised(self):
        async def go():
            s = st.PgStore(dsn=PG_DSN)
            await s.start()
            # A record whose JSON cannot be built is the store's problem
            # to count, not the loop's to crash on.
            out = await s.flush([{"loop": "L", "kind": "DECISION",
                                  "market_id": object()}], {})
            await s.close()
            return out

        out = asyncio.run(go())
        assert out["failures"] == 1 and "error" in out


def test_describe_states_what_each_backend_survives():
    d = st.describe()
    assert d["default_backend"] == st.BACKEND_PG
    assert "redeploy" in d["durability"][st.BACKEND_PG]
    assert d["durability"][st.BACKEND_MEMORY] == "nothing"
    assert d["bounds"]["recovery_cost"].startswith("O(cursors)")
