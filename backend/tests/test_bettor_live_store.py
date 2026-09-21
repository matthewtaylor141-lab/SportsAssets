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
    base = st.new_cursor(0.0)
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
        # `excluded` is the UPSERT alias; `set` is the keyword in
        # "DO UPDATE SET", which the UPDATE branch of the pattern sees
        # as a table name. `information_schema.columns` is a CATALOG
        # READ -- the startup schema check -- and writes nothing.
        allowed = set(st.OWN_TABLES) | {"excluded", "set",
                                        "information_schema"}
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

        for s in first:
            t = st.settle_transition(st.SETTLE_PENDING,
                                     cursors[s]["settle_attempts"], now=now,
                                     failures=cursors[s]["settle_failures"])
            t.pop("retired")
            cursors[s].update(t)

        second = st.due_for_settlement(cursors, now=now, limit=25)
        assert not set(second) & set(first), (
            "a contract that just answered PENDING was read again in the "
            "very next pass")

    def test_a_new_contract_is_read_in_the_next_pass_however_many_pend(self):
        now = 1000.0
        cursors = {"p%03d" % i: cur(first_seen_at=i,
                                    settle_status=st.SETTLE_PENDING,
                                    settle_attempts=1,
                                    settle_next_at=now - 1)   # all DUE
                   for i in range(500)}
        cursors["fresh"] = cur(first_seen_at=now)
        assert st.due_for_settlement(cursors, now=now, limit=25)[0] == "fresh"

    def test_every_contract_is_eventually_attempted(self):
        now, n = 1000.0, 120
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
                                         now=now,
                                         failures=cursors[s]["settle_failures"])
                t.pop("retired")
                cursors[s].update(t)
            now += 1.0
        assert attempted == set(cursors), "some contract was never read"


class TestOnlyAnAuthoritativeResolutionCompletesCollection:
    """THE DEFECT: RESOLVED_DERIVED was retired alongside RESOLVED, so
    outcome collection closed on an INFERENCE FROM A PRICE and the
    venue's own settlement endpoint could never confirm or contradict
    it."""

    def test_only_resolved_is_terminal(self):
        assert st.TERMINAL_STATUSES == (st.SETTLE_RESOLVED,)
        assert st.SETTLE_RESOLVED_DERIVED not in st.TERMINAL_STATUSES

    def test_an_authoritative_resolution_is_retired(self):
        now = 1000.0
        t = st.settle_transition(st.SETTLE_RESOLVED, 0, now=now,
                                 outcome="1.0000")
        assert t["retired"] is True
        assert t["settle_next_at"] is None
        assert t["settle_outcome"] == "1.0000"
        t.pop("retired")
        c = cur()
        c.update(t)
        assert st.due_for_settlement({"m": c}, now=now + 10 ** 9,
                                     limit=25) == []
        assert st.has_outstanding_obligation(c) is False

    def test_a_derived_outcome_is_preserved_and_still_scheduled(self):
        now = 1000.0
        t = st.settle_transition(st.SETTLE_RESOLVED_DERIVED, 0, now=now,
                                 outcome="1.0000")
        assert t["retired"] is False
        assert t["settle_status"] == st.SETTLE_RESOLVED_DERIVED
        # PRESERVED SEPARATELY. It is never written to settle_outcome,
        # which is the authoritative field.
        assert t["settle_derived_outcome"] == "1.0000"
        assert t["settle_derived_at"] == now
        assert "settle_outcome" not in t
        assert t["settle_next_at"] > now
        c = cur()
        c.update({k: v for k, v in t.items() if k != "retired"})
        assert st.has_outstanding_obligation(c) is True
        assert st.due_for_settlement({"m": c}, now=t["settle_next_at"],
                                     limit=9) == ["m"]

    def test_the_first_derivation_time_is_not_overwritten(self):
        t = st.settle_transition(st.SETTLE_RESOLVED_DERIVED, 3, now=9000.0,
                                 derived_at=100.0,
                                 derived_outcome="1.0000",
                                 outcome="0.0000")
        assert t["settle_derived_at"] == 100.0
        assert t["settle_derived_outcome"] == "1.0000"

    def test_a_derived_outcome_can_still_become_authoritative(self):
        c = cur()
        c.update({k: v for k, v in st.settle_transition(
            st.SETTLE_RESOLVED_DERIVED, 0, now=0.0,
            outcome="1.0000").items() if k != "retired"})
        later = st.settle_transition(
            st.SETTLE_RESOLVED, c["settle_attempts"], now=500.0,
            derived_at=c["settle_derived_at"],
            derived_outcome=c["settle_derived_outcome"],
            outcome="1.0000")
        assert later["retired"] is True
        assert later["settle_outcome"] == "1.0000"


class TestAHealthyOpenMarketIsNotAFailure:
    """THE DEFECT: one attempt counter advanced on every result,
    PENDING included, and the contract was ABANDONED at twelve. A
    market may legitimately stay open far longer than that."""

    def test_there_is_no_abandoned_status_at_all(self):
        assert not hasattr(st, "SETTLE_ABANDONED")

    def test_a_hundred_pending_reads_never_retire_a_contract(self):
        now, c = 0.0, cur()
        for _ in range(100):
            t = st.settle_transition(st.SETTLE_PENDING, c["settle_attempts"],
                                     now=now, failures=c["settle_failures"])
            assert t["retired"] is False
            assert t["settle_status"] == st.SETTLE_PENDING
            c.update({k: v for k, v in t.items() if k != "retired"})
            now = t["settle_next_at"]
        assert c["settle_attempts"] == 100
        assert c["settle_failures"] == 0
        assert st.has_outstanding_obligation(c) is True

    def test_pending_backoff_is_capped_not_terminal(self):
        c, now = cur(), 0.0
        gaps = []
        for _ in range(20):
            t = st.settle_transition(st.SETTLE_PENDING, c["settle_attempts"],
                                     now=now, failures=c["settle_failures"])
            gaps.append(t["settle_next_at"] - now)
            c.update({k: v for k, v in t.items() if k != "retired"})
            now = t["settle_next_at"]
        assert gaps[0] == st.PENDING_BASE_S
        assert gaps == sorted(gaps)
        assert max(gaps) == st.PENDING_MAX_S

    def test_event_timing_beats_a_blind_backoff(self):
        """A market resolving in an hour should be looked at shortly
        after that, not on whatever rung of a backoff we happen to be
        on."""
        now = 1000.0
        event_at = now + 3600.0
        t = st.settle_transition(st.SETTLE_PENDING, 8, now=now,
                                 event_at=event_at)
        assert t["settle_next_at"] == event_at + st.EVENT_GRACE_S

    def test_a_past_event_falls_back_to_the_backoff(self):
        now = 10_000.0
        t = st.settle_transition(st.SETTLE_PENDING, 0, now=now,
                                 event_at=now - 10_000.0)
        assert t["settle_next_at"] == now + st.PENDING_BASE_S

    def test_a_successful_read_clears_the_failure_run(self):
        t = st.settle_transition(st.SETTLE_PENDING, 9, now=0.0, failures=4)
        assert t["settle_failures"] == 0
        assert t["settle_status"] == st.SETTLE_PENDING


class TestFailedReadsEscalateVisiblyAndAreNeverDropped:

    def test_failures_are_counted_apart_from_attempts(self):
        c, now = cur(), 0.0
        for _ in range(3):
            t = st.settle_transition(st.SETTLE_UNREADABLE,
                                     c["settle_attempts"], now=now,
                                     failures=c["settle_failures"])
            c.update({k: v for k, v in t.items() if k != "retired"})
        assert c["settle_attempts"] == 3 and c["settle_failures"] == 3

    def test_a_run_of_failures_escalates_by_name(self):
        c, now = cur(), 0.0
        for _ in range(st.READ_FAILURE_ESCALATE_AT):
            t = st.settle_transition(st.SETTLE_UNREADABLE,
                                     c["settle_attempts"], now=now,
                                     failures=c["settle_failures"])
            c.update({k: v for k, v in t.items() if k != "retired"})
        assert c["settle_status"] == st.SETTLE_READ_ESCALATED

    def test_an_escalated_contract_is_still_scheduled(self):
        """Dropping it would be losing an obligation quietly."""
        c = cur(settle_status=st.SETTLE_READ_ESCALATED,
                settle_failures=9, settle_next_at=100.0)
        assert st.has_outstanding_obligation(c) is True
        assert st.due_for_settlement({"m": c}, now=200.0, limit=9) == ["m"]

    def test_an_escalated_contract_sorts_behind_every_healthy_one(self):
        """A broken slug spends only the budget left over."""
        cursors = {"broken": cur(settle_status=st.SETTLE_READ_ESCALATED,
                                 settle_next_at=0.0),
                   "healthy": cur(settle_status=st.SETTLE_PENDING,
                                  settle_next_at=50.0),
                   "new": cur(first_seen_at=99.0)}
        assert st.due_for_settlement(cursors, now=100.0, limit=9) == [
            "new", "healthy", "broken"]

    def test_a_failing_contract_that_recovers_leaves_escalation(self):
        c, now = cur(), 0.0
        for _ in range(st.READ_FAILURE_ESCALATE_AT + 2):
            t = st.settle_transition(st.SETTLE_UNREADABLE,
                                     c["settle_attempts"], now=now,
                                     failures=c["settle_failures"])
            c.update({k: v for k, v in t.items() if k != "retired"})
        assert c["settle_status"] == st.SETTLE_READ_ESCALATED
        t = st.settle_transition(st.SETTLE_PENDING, c["settle_attempts"],
                                 now=now, failures=c["settle_failures"])
        assert t["settle_status"] == st.SETTLE_PENDING
        assert t["settle_failures"] == 0


# ── 3. bounded cursors, without losing obligations ───────────────────

class TestCursorsAreBoundedAsACacheNotAsTheRecord:
    """THE DEFECT: an in-memory cap that dropped a cursor dropped that
    contract's settlement schedule with it, so a market stopped being
    checked because the process was busy."""

    def test_the_queue_is_read_from_the_store_not_from_memory(self):
        for cls in (st.PgStore, st.FileStore, st.MemoryStore):
            assert hasattr(cls, "due_for_settlement"), cls.__name__
            assert hasattr(cls, "outcome_report"), cls.__name__

    def test_eviction_prefers_contracts_with_no_outstanding_obligation(self):
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

    def test_an_unflushed_cursor_is_never_evicted(self):
        """Dropping a cached row loses nothing; dropping an UNWRITTEN
        update loses the update."""
        cursors = {"dirty": cur(last_seen_at=1.0),
                   "clean": cur(last_seen_at=2.0)}
        out = st.evict_to(cursors, 1, dirty={"dirty"})
        assert "dirty" in cursors and "clean" not in cursors
        assert out["kept_dirty"] == 1

    def test_under_the_cap_nothing_moves(self):
        cursors = {"a": cur()}
        out = st.evict_to(cursors, 10)
        assert out["evicted"] == 0 and out["evicted_unresolved"] == 0
        assert cursors

    def test_a_derived_outcome_still_counts_as_an_obligation(self):
        assert st.has_outstanding_obligation(
            cur(settle_status=st.SETTLE_RESOLVED_DERIVED)) is True
        assert st.has_outstanding_obligation(
            cur(settle_status=st.SETTLE_RESOLVED)) is False


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
        assert out == {"records": 1, "cursors": 1, "cursors_total": 1,
                       "failures": 0, "rotated": 0}
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

    def test_a_flush_of_one_cursor_does_not_delete_the_others(self,
                                                              tmp_path):
        """THE DEFECT: the caller hands over the DIRTY cursors, not all
        of them -- it cannot hand over all of them, because the cache
        is bounded. Writing what was handed over replaced the file with
        a subset and deleted every contract that had not changed in
        that batch."""
        s = self.store(tmp_path)
        asyncio.run(s.start())
        asyncio.run(s.flush([], {"a": cur(), "b": cur(), "c": cur()}))
        asyncio.run(s.flush([], {"b": cur(last_source_ts="T2")}))
        got = asyncio.run(self.store(tmp_path).load())
        assert set(got["cursors"]) == {"a", "b", "c"}
        assert got["cursors"]["b"]["last_source_ts"] == "T2"

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

    def test_concurrent_starts_all_succeed(self):
        """MEASURED FIRST: twelve sessions running this DDL at once gave
        2 successes and 10 failures. The API's migration runner and this
        worker boot in the SAME deployment, so that race is the normal
        case, not the corner case."""
        async def go():
            pool = await st.PgStore(dsn=PG_DSN)._get_pool()
            async with pool.acquire() as con:
                for t in reversed(st.OWN_TABLES):
                    await con.execute("DROP TABLE IF EXISTS %s CASCADE" % t)
            stores = [st.PgStore(dsn=PG_DSN, boot_id="b%d" % i)
                      for i in range(10)]
            outs = await asyncio.gather(*(x.start() for x in stores))
            for x in stores:
                await x.close()
            return outs

        outs = asyncio.run(go())
        assert all(o["ok"] for o in outs), [o for o in outs if not o["ok"]]
        assert all(o["schema_attempts"] <= st.SCHEMA_ATTEMPTS for o in outs)

    def test_a_concurrent_migration_runner_does_not_break_the_worker(self):
        """The real pairing: migration 093 applied by the API's runner
        while the worker calls start(). Both take the same advisory
        lock, so whoever is second waits and finds the tables there."""
        here = os.path.dirname(os.path.abspath(__file__))
        sql = open(os.path.normpath(os.path.join(
            here, "..", "migrations",
            "093_bettor_live_observation.sql"))).read()
        assert "pg_advisory_xact_lock(%d)" % st.SCHEMA_LOCK_KEY in sql

        async def runner():
            # exactly what sportsassets/scripts/migrate.py does
            import asyncpg
            con = await asyncpg.connect(PG_DSN)
            try:
                async with con.transaction():
                    await con.execute(sql)
                return "ok"
            except Exception as exc:  # noqa: BLE001
                return type(exc).__name__
            finally:
                await con.close()

        async def go():
            pool = await st.PgStore(dsn=PG_DSN)._get_pool()
            async with pool.acquire() as con:
                for t in reversed(st.OWN_TABLES):
                    await con.execute("DROP TABLE IF EXISTS %s CASCADE" % t)
            worker = st.PgStore(dsn=PG_DSN, boot_id="w")
            a, b, c = await asyncio.gather(runner(), worker.start(),
                                           runner())
            await worker.close()
            return a, b, c

        a, b, c = asyncio.run(go())
        assert b["ok"] is True, b
        assert (a, c) == ("ok", "ok"), (a, c)

    def test_a_schema_that_never_arrives_is_an_explicit_refusal(self):
        """The failure path is named and bounded, not a hang."""
        async def go():
            s = st.PgStore(dsn=PG_DSN)
            await s.start()
            pool = await s._get_pool()
            async with pool.acquire() as con:
                await con.execute("DROP TABLE IF EXISTS bettor_live_cursor "
                                  " CASCADE")
                await con.execute("CREATE TABLE bettor_live_cursor "
                                  " (lane TEXT, market_id TEXT)")
            out = await st.PgStore(dsn=PG_DSN).start()
            async with pool.acquire() as con:
                await con.execute("DROP TABLE IF EXISTS bettor_live_cursor "
                                  " CASCADE")
            await s.close()
            return out

        out = asyncio.run(go())
        assert out["ok"] is False
        assert out["why"] == "SCHEMA_NOT_READY"
        assert out["attempts"] == st.SCHEMA_ATTEMPTS
        assert any("settle_failures" in m for m in out["missing"])
        assert "093" in out["remedy"]

    def test_the_bounded_wait_is_actually_bounded(self):
        assert st.SCHEMA_LOCK_TIMEOUT_MS <= 30_000
        assert st.SCHEMA_ATTEMPTS <= 8
        assert sum(st.SCHEMA_BACKOFF_S) <= 30.0

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
            # DISTINCT observations: the retry guard keys on content,
            # so 3,000 identical records are one record by design.
            await s.flush([{"loop": "L", "kind": "DECISION",
                            "market_id": "m%d" % (i % 3),
                            "source_ts": "T%d" % i,
                            "decided_at": "D%d" % i}
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
                            "market_id": "m", "source_ts": "T%d" % i,
                            "decided_at": "D%d" % i}
                           for i in range(400)], {})
            pruned = await s.prune(max_rows=100, cursor_retention_days=7)
            got = await s.load()
            await s.close()
            return pruned, got

        pruned, got = asyncio.run(go())
        assert pruned["journal_rows_deleted"] == 300
        assert got["journal_rows"] == 100

    def test_only_a_completed_contract_ages_out_of_the_cursor_table(self):
        """THE DEFECT: the age prune ignored settlement status, so it
        deleted exactly the markets that had been open longest -- the
        unresolved obligations retention exists FOR."""
        self.fresh()
        import time
        old = time.time() - 30 * 86400

        async def go():
            s = st.PgStore(dsn=PG_DSN)
            await s.start()
            await s.flush([], {
                "old_done": cur(last_seen_at=old,
                                settle_status=st.SETTLE_RESOLVED),
                "old_pending": cur(last_seen_at=old,
                                   settle_status=st.SETTLE_PENDING),
                "old_derived": cur(last_seen_at=old,
                                   settle_status=st.SETTLE_RESOLVED_DERIVED),
                "old_escalated": cur(last_seen_at=old,
                                     settle_status=st.SETTLE_READ_ESCALATED),
                "old_never": cur(last_seen_at=old),
                "new": cur(last_seen_at=time.time())})
            pruned = await s.prune(max_rows=10 ** 9, cursor_retention_days=7)
            got = await s.load()
            await s.close()
            return pruned, got

        pruned, got = asyncio.run(go())
        assert pruned["cursors_deleted"] == 1, "only the RESOLVED one"
        assert pruned["cursors_retained_unresolved"] == 4
        assert set(got["cursors"]) == {"old_pending", "old_derived",
                                       "old_escalated", "old_never", "new"}

    def test_a_replayed_batch_does_not_inflate_the_record_count(self):
        """A commit can succeed and its acknowledgment be lost. The
        retry must be a no-op, or every such event silently inflates
        the observation count and every rate derived from it."""
        self.fresh()
        batch = [{"loop": "L", "kind": "DECISION", "market_id": "m1",
                  "source_ts": "T%d" % i, "decided_at": "D%d" % i,
                  "status": "DECIDED", "selected": "NO_TRADE"}
                 for i in range(50)]
        for r in batch:
            r["record_key"] = st.record_key(r)

        async def go():
            s = st.PgStore(dsn=PG_DSN)
            await s.start()
            a = await s.flush(list(batch), {})
            b = await s.flush(list(batch), {})     # the lost-ack retry
            got = await s.load()
            await s.close()
            return a, b, got

        a, b, got = asyncio.run(go())
        assert a["failures"] == 0 and b["failures"] == 0
        assert got["journal_rows"] == 50, (
            "the retry inserted a second copy of every record")

    def test_two_records_of_the_same_market_at_different_times_both_land(self):
        """The retry guard must not deduplicate genuine observations."""
        self.fresh()

        async def go():
            s = st.PgStore(dsn=PG_DSN)
            await s.start()
            rows = []
            for i in range(10):
                r = {"loop": "L", "kind": "DECISION", "market_id": "m1",
                     "source_ts": "T%d" % i, "decided_at": "D%d" % i,
                     "status": "DECIDED"}
                r["record_key"] = st.record_key(r)
                rows.append(r)
            await s.flush(rows, {})
            got = await s.load()
            await s.close()
            return got

        assert asyncio.run(go())["journal_rows"] == 10

    def test_the_settlement_queue_comes_from_the_store(self):
        self.fresh()
        now = 5000.0

        async def go():
            s = st.PgStore(dsn=PG_DSN)
            await s.start()
            await s.flush([], {
                "done": cur(settle_status=st.SETTLE_RESOLVED),
                "new": cur(first_seen_at=1.0),
                "due": cur(settle_status=st.SETTLE_PENDING,
                           settle_next_at=now - 10),
                "later": cur(settle_status=st.SETTLE_PENDING,
                             settle_next_at=now + 10_000),
                "derived": cur(settle_status=st.SETTLE_RESOLVED_DERIVED,
                               settle_next_at=now - 5),
                "broken": cur(settle_status=st.SETTLE_READ_ESCALATED,
                              settle_next_at=now - 900)})
            got = await s.due_for_settlement(now=now, limit=10)
            rep = await s.outcome_report()
            await s.close()
            return got, rep

        got, rep = asyncio.run(go())
        assert got["slugs"][0] == "new", "never-attempted must sort first"
        assert got["slugs"][-1] == "broken", "escalated must sort last"
        assert "done" not in got["slugs"], "an authoritative read is done"
        assert "later" not in got["slugs"], "not due yet"
        assert "derived" in got["slugs"], (
            "a derived outcome is still owed an authoritative one")
        assert got["outstanding"] == 5
        assert rep["authoritative"] == 1
        assert rep["awaiting_authoritative"] == 1
        assert rep["read_escalated"] == 1

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
