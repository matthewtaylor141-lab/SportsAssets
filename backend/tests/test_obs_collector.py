"""Run 83 acceptance tests for the collector end to end, with no database
and no network: a fake pool that records every statement, and a fake venue.

Between this file, test_obs_safety.py and test_obs_clock_and_schedule.py, the
twelve acceptance conditions are covered as follows:

  1  mirror_live=false throughout       -- test_the_collector_never_reads_or_writes_mirror_live
  2  zero real order API calls          -- test_obs_safety.py (static + dynamic)
  3  source event ids survive           -- test_the_source_event_id_survives_end_to_end
  4  no silent source ts fallback       -- test_obs_clock_and_schedule.py + the chain declaration test
  5  monotonic non-decreasing           -- test_obs_clock_and_schedule.py + here, per event
  6  explicit causal ids join the stages-- test_every_row_is_joined_by_an_explicit_id
  7  immediate snapshot captured        -- test_the_immediate_snapshot_is_captured
  8  forward snapshots captured         -- test_the_forward_offsets_are_all_captured
  9  depth kept exactly                 -- test_the_depth_ladder_is_stored_exactly
  10 replay does not duplicate          -- test_a_replayed_event_is_not_a_second_observation
  11 append-only                        -- test_the_writer_issues_no_update_or_delete
  12 failure cannot enable trading      -- test_observe_swallows_every_failure
"""
from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

from sportsassets.obs import clock, collector, record, schedule
from sportsassets.obs.book import BookSnapshot, _levels

BACKEND = pathlib.Path(__file__).resolve().parents[1]


class FakePool:
    """Records every statement. Honours the ON CONFLICT of the event insert."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self.seen_source_ids: set[str] = set()
        self.seen_slots: set[tuple[str, str]] = set()

    async def fetchrow(self, sql, *args):
        self.statements.append((sql, args))
        source_event_id = args[4]
        if source_event_id in self.seen_source_ids:
            return None                      # ON CONFLICT (source_event_id)
        self.seen_source_ids.add(source_event_id)
        return {"obs_event_id": args[0]}

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        if "rn1_obs_snapshots" in sql:
            key = (args[0], args[2])
            if key in self.seen_slots:
                return                        # ON CONFLICT (event, offset)
            self.seen_slots.add(key)

    def rows(self, table: str) -> list[tuple]:
        return [a for s, a in self.statements if table in s]


class FakeResponse:
    status_code = 200

    def __init__(self, payload) -> None:
        self._payload = payload

    def json(self):
        return self._payload


class FakeHttp:
    """A venue that answers with a walking book, so the curve actually moves."""

    def __init__(self) -> None:
        self.calls = 0

    async def get(self, url, params=None, timeout=None):
        self.calls += 1
        # The ask drifts up one cent per read: a deterministic deterioration.
        ask = 0.50 + 0.01 * (self.calls - 1)
        return FakeResponse({
            "asks": [[f"{ask:.6f}", "100.0"], [f"{ask + 0.01:.6f}", "250.0"]],
            "bids": [["0.48", "80.0"]],
        })


def _obs(source_event_id="dedupe-abc", size=1000.0):
    return collector.Observation(
        receipt=clock.now(),
        source_event_id=source_event_id,
        source_lane="chain",
        source_venue="polymarket",
        source_token_id="token-1",
        source_ts=clock.SourceTimestamp.missing(
            "polygon_block_timestamp", clock.ClockDomain.SOURCE_CHAIN),
        ingest_worker="test",
        fields={"source_size": size, "source_price": 0.49, "source_side": "BUY"},
    )


@pytest.fixture
def fast_offsets(monkeypatch):
    """Millisecond offsets so a full plan runs inside a test."""
    monkeypatch.setattr(schedule, "OFFSETS",
                        (("0ms", 0.0), ("a", 0.01), ("b", 0.02)))
    return 3


# ----------------------------------------------------------------- 3, 6, 7, 8
def test_the_source_event_id_survives_end_to_end(fast_offsets):
    pool, http = FakePool(), FakeHttp()
    obs = _obs("dedupe-xyz")
    asyncio.run(collector._handle(pool, http, obs, collector.Pacer(1000.0), ""))

    events = pool.rows("rn1_obs_events")
    assert len(events) == 1
    assert events[0][4] == "dedupe-xyz", "the source id reaches the event row"


def test_every_row_is_joined_by_an_explicit_id(fast_offsets):
    """Attribution is by id, never by timestamp proximity -- which is exactly
    what stops working when clocks disagree."""
    pool, http = FakePool(), FakeHttp()
    asyncio.run(collector._handle(pool, http, _obs(), collector.Pacer(1000.0), ""))

    event_id = pool.rows("rn1_obs_events")[0][0]
    assert all(a[0] == event_id for a in pool.rows("rn1_obs_snapshots"))
    assert all(a[0] == event_id for a in pool.rows("rn1_obs_transitions"))


def test_the_immediate_snapshot_is_captured(fast_offsets):
    pool, http = FakePool(), FakeHttp()
    asyncio.run(collector._handle(pool, http, _obs(), collector.Pacer(1000.0), ""))

    zero = [a for a in pool.rows("rn1_obs_snapshots") if a[2] == "0ms"]
    assert len(zero) == 1
    assert zero[0][5] == schedule.SnapshotStatus.CAPTURED
    assert zero[0][16] == pytest.approx(0.50), "the first ask is the 0ms ask"


def test_the_forward_offsets_are_all_captured(fast_offsets):
    pool, http = FakePool(), FakeHttp()
    asyncio.run(collector._handle(pool, http, _obs(), collector.Pacer(1000.0), ""))

    snaps = pool.rows("rn1_obs_snapshots")
    assert {a[2] for a in snaps} == {"0ms", "a", "b"}, "one row per offset"
    assert all(a[5] == schedule.SnapshotStatus.CAPTURED for a in snaps)
    # The measured offsets increase -- acceptance test 5 at the event level.
    by_label = {a[2]: a[11] for a in snaps}
    assert by_label["0ms"] <= by_label["a"] <= by_label["b"]
    # And the curve moved, which is the point of the whole instrument.
    asks = {a[2]: a[16] for a in snaps}
    assert asks["b"] > asks["0ms"]


# ------------------------------------------------------------------------- 9
def test_the_depth_ladder_is_stored_exactly(fast_offsets):
    pool, http = FakePool(), FakeHttp()
    asyncio.run(collector._handle(pool, http, _obs(), collector.Pacer(1000.0), ""))

    zero = [a for a in pool.rows("rn1_obs_snapshots") if a[2] == "0ms"][0]
    ladder = json.loads(zero[17])
    assert ladder == [[0.5, 100.0], [0.51, 250.0]]
    assert zero[18] == 2, "depth_levels matches the ladder length"


def test_a_malformed_level_is_skipped_not_coerced_to_zero():
    """A zero-size level would silently change a VWAP. A shorter ladder can only
    make a row DEPTH_EXHAUSTED, which the analysis already reports honestly."""
    assert _levels([["0.5", "10"], ["bad", "10"], ["0.6", None], ["0.7", "5"]]) \
        == [(0.5, 10.0), (0.7, 5.0)]


def test_an_exhausted_ladder_is_recorded_as_exhausted_not_as_a_price(fast_offsets):
    pool, http = FakePool(), FakeHttp()
    # 100 + 250 shares on the book; Q_B = 10,000 cannot be covered.
    asyncio.run(collector._handle(pool, http, _obs(size=10000.0),
                                  collector.Pacer(1000.0), ""))
    zero = [a for a in pool.rows("rn1_obs_snapshots") if a[2] == "0ms"][0]
    vwap_qb, qb_exhausted = zero[21], zero[22]
    assert vwap_qb is None and qb_exhausted is True
    # Q_A = 1,000 is also beyond the 350 on the book.
    assert zero[19] is None and zero[20] is True


# ------------------------------------------------------------------------ 10
def test_a_replayed_event_is_not_a_second_observation(fast_offsets):
    """A restart re-observing the same source event records ONE observation."""
    pool, http = FakePool(), FakeHttp()
    for _ in range(3):
        asyncio.run(collector._handle(pool, http, _obs("same-key"),
                                      collector.Pacer(1000.0), ""))
    assert len(pool.rows("rn1_obs_events")) == 3, "three inserts were attempted"
    assert len(pool.seen_source_ids) == 1, "one survived the unique key"
    # And only the first attempt did any venue work.
    assert len(pool.rows("rn1_obs_snapshots")) == 3


# ------------------------------------------------------------------------ 11
def test_the_writer_issues_no_update_or_delete():
    """Append-only, checked over the writer's actual SQL.

    The SQL CONSTANTS are inspected rather than the file text: the module's
    prose says "no UPDATE and no DELETE" in English, and a check that cannot
    tell a statement from a sentence would pass or fail for the wrong reason.

    Migration 062 enforces this with a trigger too; both matter -- the trigger
    catches anything the writer does not, and this catches it before deploy.
    """
    statements = [v for k, v in vars(record).items()
                  if k.endswith("_SQL") and isinstance(v, str)]
    assert len(statements) == 4, "four writers: event, snapshot, transition, clock"
    for sql in statements:
        upper = " ".join(sql.upper().split())
        assert upper.startswith("INSERT INTO")
        for verb in ("UPDATE ", "DELETE ", " SET "):
            assert verb not in upper, f"a writer statement contains {verb.strip()}"


def test_the_migration_enforces_append_only_on_every_table():
    sql = (BACKEND / "migrations" / "062_rn1_observability.sql").read_text()
    for table in ("rn1_obs_events", "rn1_obs_snapshots",
                  "rn1_obs_transitions", "rn1_obs_clock_sync"):
        assert f"CREATE TRIGGER {table}_append_only" in sql
        assert f"BEFORE UPDATE OR DELETE ON {table}" in sql


def test_the_migration_binds_a_missing_source_timestamp_to_a_null():
    """No shape exists in which our clock sits in the source column as SUPPLIED."""
    sql = (BACKEND / "migrations" / "062_rn1_observability.sql").read_text()
    assert "CHECK ((source_ts_status = 'MISSING') = (source_ts IS NULL))" in sql


# ------------------------------------------------------------------------ 12
def test_observe_swallows_every_failure(monkeypatch):
    """Instrumentation failure must not reach the ingestion path."""
    monkeypatch.setattr(collector, "shadow_enabled", lambda: True)

    class Exploding:
        def put_nowait(self, _):
            raise RuntimeError("boom")

    monkeypatch.setattr(collector, "_QUEUE", Exploding())
    before = collector.STATS["observe_exceptions"]
    collector.observe(source_event_id="x", source_lane="chain",
                      source_venue="v", source_token_id="t",
                      source_ts=clock.SourceTimestamp.missing("p", "d"),
                      ingest_worker="w")          # must not raise
    assert collector.STATS["observe_exceptions"] == before + 1


def test_observe_is_a_no_op_when_the_flag_is_off(monkeypatch):
    monkeypatch.setattr(collector, "shadow_enabled", lambda: False)
    before = collector.STATS["dropped_disabled"]
    collector.observe(source_event_id="x", source_lane="chain",
                      source_venue="v", source_token_id="t",
                      source_ts=clock.SourceTimestamp.missing("p", "d"),
                      ingest_worker="w")
    assert collector.STATS["dropped_disabled"] == before + 1


def test_a_full_queue_drops_and_counts_rather_than_blocking(monkeypatch):
    """The collector must never slow the path it is attached to."""
    monkeypatch.setattr(collector, "shadow_enabled", lambda: True)
    monkeypatch.setattr(collector, "_QUEUE", asyncio.Queue(maxsize=1))
    before = collector.STATS["dropped_queue_full"]
    for _ in range(5):
        collector.observe(source_event_id="x", source_lane="chain",
                          source_venue="v", source_token_id="t",
                          source_ts=clock.SourceTimestamp.missing("p", "d"),
                          ingest_worker="w")
    assert collector.STATS["dropped_queue_full"] == before + 4


# ------------------------------------------------------------------------- 1
def _code_identifiers_and_literals(path: pathlib.Path) -> set[str]:
    """Every name, attribute and string LITERAL in a module, minus docstrings.

    Prose is excluded deliberately. obs/config.py explains its relationship to
    mirror_live in English, and a check that cannot tell a sentence from a
    statement would be testing the comments.
    """
    import ast as _ast
    tree = _ast.parse(path.read_text(), filename=str(path))
    docstrings = set()
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.Module, _ast.ClassDef, _ast.FunctionDef,
                             _ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], _ast.Expr) \
               and isinstance(body[0].value, _ast.Constant) \
               and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    out: set[str] = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Name):
            out.add(node.id)
        elif isinstance(node, _ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, _ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            out.add(node.value)
    return out


def test_the_collector_never_reads_or_writes_mirror_live():
    """The collector observes while trading is paused, and cannot change that.

    It never reads mirror_live to decide whether to act, because it never acts.
    """
    for name in ("collector", "record", "book", "schedule", "config", "clock"):
        path = BACKEND / "sportsassets" / "obs" / f"{name}.py"
        tokens = _code_identifiers_and_literals(path)
        offenders = [t for t in tokens if "mirror_live" in t]
        assert not offenders, f"obs/{name}.py uses mirror_live in code: {offenders}"


def test_a_venue_error_is_recorded_as_an_error_not_as_a_missing_row(fast_offsets):
    """A failed read is written, with its reason. Absence is never the record."""
    pool = FakePool()

    class Broken:
        async def get(self, *a, **k):
            raise ConnectionError("venue down")

    asyncio.run(collector._handle(pool, Broken(), _obs(),
                                  collector.Pacer(1000.0), ""))
    snaps = pool.rows("rn1_obs_snapshots")
    assert len(snaps) == 3, "every offset still produced a row"
    assert all(a[5] == schedule.SnapshotStatus.VENUE_ERROR for a in snaps)
    assert all("ConnectionError" in (a[6] or "") for a in snaps)


def test_a_book_read_that_fails_carries_no_prices():
    snap = BookSnapshot(ok=False, request_start=clock.now(), error="http_500")
    assert snap.best_ask is None and snap.best_bid is None
    assert snap.depth_levels == 0


def test_the_source_token_id_reaches_the_event_row(fast_offsets):
    """It is an Observation attribute, not one of `fields`.

    It reached the book reader and never the row until the sample event in
    research/RUN83_SAMPLE_EVENT.txt showed the column arriving null. A
    regression here would be invisible in every other test, because every
    other test exercises the venue read rather than the stored identity.
    """
    pool, http = FakePool(), FakeHttp()
    asyncio.run(collector._handle(pool, http, _obs(), collector.Pacer(1000.0), ""))
    assert pool.rows("rn1_obs_events")[0][11] == "token-1"
