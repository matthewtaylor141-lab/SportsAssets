"""Run 83.2 acceptance tests for the collector end to end -- no DB, no network.

REWRITTEN FROM THE V1 FILE. The V1 version drove collector.observe(),
collector._handle() and collector._run_plan(), none of which exist any more: the
population filter split observe() into stamp()/admit(), and the request-per-slot
walk was replaced by a central scheduler over a local cache. The twelve
acceptance conditions are unchanged and every one is still covered; what changed
is the mechanism they are asserted against.

Two V1 tests are deliberately NOT carried over, and the reason matters:

  * test_the_forward_offsets_are_all_captured asserted that the ask DRIFTED
    between offsets, because V1 made a fresh venue read per offset and the fake
    venue walked the book one cent per call. The V2 primary channel samples ONE
    cached state, so two offsets closer together than the refresh interval
    return the SAME price BY DESIGN. Asserting drift would now be asserting a
    fiction. It is replaced by
    test_two_offsets_sharing_one_cached_state_are_visibly_the_same_state, which
    pins the honest version: the prices match, and the rows say so via an
    identical venue_book_hash and a growing cache_age_ms.

  * test_an_exhausted_ladder_is_recorded_as_exhausted_not_as_a_price covered
    Q_A/Q_B VWAP on the snapshot writer. The VWAP columns exist on
    rn1_obs_samples and are not yet populated by the V2 sampler, so the test
    would have passed vacuously on a None. It is recorded here as an
    ACKNOWLEDGED GAP rather than kept as a green check on unwritten code, and
    test_the_vwap_columns_are_not_yet_populated states it explicitly.

  1  mirror_live=false throughout       -- test_the_collector_never_reads_or_writes_mirror_live
  2  zero real order API calls          -- test_obs_safety.py (static + dynamic)
  3  source event ids survive           -- test_the_source_event_id_survives_end_to_end
  4  no silent source ts fallback       -- test_obs_clock_and_schedule.py
  5  monotonic non-decreasing           -- test_the_sample_instants_do_not_go_backwards
  6  explicit causal ids join the stages-- test_every_row_is_joined_by_an_explicit_id
  7  immediate sample captured          -- test_the_zero_offset_samples_the_state_held_at_receipt
  8  forward samples captured           -- test_every_offset_produces_exactly_one_result_row
  9  depth kept exactly                 -- test_the_depth_ladder_is_stored_exactly
  10 replay does not duplicate          -- test_a_replayed_event_is_not_a_second_observation
  11 append-only                        -- test_the_writer_issues_no_update_or_delete
  12 failure cannot enable trading      -- test_stamp_and_admit_swallow_every_failure
"""
from __future__ import annotations

import ast
import asyncio
import json
import pathlib

import pytest

from sportsassets.obs import cache as obs_cache
from sportsassets.obs import clock, collector, record, scheduler
from sportsassets.obs.book import ObservationChannel, _levels

BACKEND = pathlib.Path(__file__).resolve().parents[1]
_CHANNEL = ObservationChannel.LOCAL_CACHE_BATCH_POLL_PATH
FAST = (("0ms", 0.0), ("a", 0.01), ("b", 0.02))


class FakeConn:
    def __init__(self, pool) -> None:
        self._pool = pool

    async def executemany(self, sql, rows):
        for args in rows:
            self._pool.statements.append((sql, tuple(args)))
            if "rn1_obs_plan" in sql:
                self._pool.seen_slots.add(args[0])
            elif "rn1_obs_samples" in sql:
                self._pool.seen_samples.add(args[0])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakePool:
    """Records every statement. Honours the ON CONFLICT of the event insert."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self.seen_source_ids: set[str] = set()
        self.seen_slots: set[str] = set()
        self.seen_samples: set[str] = set()

    async def fetchrow(self, sql, *args):
        self.statements.append((sql, args))
        source_event_id = args[4]
        if source_event_id in self.seen_source_ids:
            return None                      # ON CONFLICT (source_event_id)
        self.seen_source_ids.add(source_event_id)
        return {"obs_event_id": args[0]}

    async def execute(self, sql, *args):
        self.statements.append((sql, args))

    def acquire(self):
        return FakeConn(self)

    def rows(self, table: str) -> list[tuple]:
        return [a for s, a in self.statements if table in s]


def _cache_with_state(token="token-1", ask=0.50, vhash="h1"):
    mc = obs_cache.MarketStateCache()
    mc.states[token] = obs_cache.TokenState(
        token_id=token, received=clock.now(),
        bids=[(0.48, 80.0)], asks=[(ask, 100.0), (ask + 0.01, 250.0)],
        market_id="cond-1", venue_ts="1757000000", venue_hash=vhash)
    mc.hot[token] = 0.0
    return mc


@pytest.fixture
def subject_on(monkeypatch):
    monkeypatch.setenv("RN1_OBSERVABILITY_SHADOW", "true")
    monkeypatch.setenv("RN1_OBSERVABILITY_SUBJECT_WHALE_ID", "2")
    monkeypatch.setattr(collector, "_QUEUE", asyncio.Queue(maxsize=8))
    return 2


def _stamp(source_event_id="dedupe-abc", whale_id=2, size=1000.0):
    return collector.stamp(
        source_event_id=source_event_id, source_lane="chain",
        source_venue="polymarket", source_token_id="token-1",
        source_ts=clock.SourceTimestamp.missing(
            "polygon_block_timestamp", clock.ClockDomain.SOURCE_CHAIN),
        ingest_worker="test", subject_whale_id=whale_id,
        subject_username="RN1", source_market_id="cond-1",
        source_size=size, source_price=0.49, source_side="BUY")


async def _drive(pool, mc, pending, offsets=FAST):
    """One admitted event, its plan, and its samples -- the whole V2 path."""
    sched = scheduler.DueTimeScheduler()
    obs_event_id = record.new_event_id()
    written = await record.insert_event_v2(pool, obs_event_id=obs_event_id,
                                           pending=pending)
    if not written:
        return sched, obs_event_id
    mc.promote(pending.source_token_id, 40)
    slots = scheduler.plan_for_event(
        obs_event_id=obs_event_id, source_event_id=pending.source_event_id,
        receipt=pending.receipt, channel=_CHANNEL,
        source_token_id=pending.source_token_id,
        source_market_id=pending.fields.get("source_market_id"),
        offsets=offsets)
    await record.insert_plan(pool, slots)
    sched.push_all(slots)
    # Drain every slot as it comes due, exactly as _sample_loop does.
    deadline = max(s.target_monotonic for s in slots) + 0.1
    import time as _t
    while sched.pending and _t.monotonic() < deadline:
        await sched.wait_until_next(max_sleep=0.01)
        ready = sched.pop_ready(_t.monotonic())
        if ready:
            await record.insert_samples(pool, [
                scheduler.resolve_local(s, mc.sample(s.source_token_id))
                for s in ready])
    return sched, obs_event_id


# ----------------------------------------------------------------- 3, 6, 7, 8
def test_the_source_event_id_survives_end_to_end(subject_on):
    pool, mc = FakePool(), _cache_with_state()
    pending = _stamp("dedupe-xyz")
    assert collector.admit(pending, was_insert=True)
    asyncio.run(_drive(pool, mc, pending))

    events = pool.rows("rn1_obs_events")
    assert len(events) == 1
    assert events[0][4] == "dedupe-xyz", "the source id reaches the event row"
    # And it reaches the plan, which is what the slot id is derived from.
    assert all(a[2] == "dedupe-xyz" for a in pool.rows("rn1_obs_plan"))


def test_every_row_is_joined_by_an_explicit_id(subject_on):
    """Attribution is by id, never by timestamp proximity -- which is exactly
    what stops working when clocks disagree."""
    pool, mc = FakePool(), _cache_with_state()
    pending = _stamp()
    collector.admit(pending, was_insert=True)
    _, event_id = asyncio.run(_drive(pool, mc, pending))

    assert all(a[1] == event_id for a in pool.rows("rn1_obs_plan"))
    assert all(a[1] == event_id for a in pool.rows("rn1_obs_samples"))
    # Every result names the plan row it answers, and every plan row is answered.
    planned = {a[0] for a in pool.rows("rn1_obs_plan")}
    answered = {a[0] for a in pool.rows("rn1_obs_samples")}
    assert answered == planned, (
        "a result exists with no plan, or a plan with no result -- the V1 model "
        "could not tell either case from a slot that never existed")


def test_the_zero_offset_samples_the_state_held_at_receipt(subject_on):
    """The 0 ms definition: whatever was ALREADY in the cache at the anchor."""
    pool, mc = FakePool(), _cache_with_state(ask=0.50)
    pending = _stamp()
    collector.admit(pending, was_insert=True)
    asyncio.run(_drive(pool, mc, pending))

    zero = [a for a in pool.rows("rn1_obs_samples") if a[4] == 0]
    assert len(zero) == 1
    assert zero[0][9] == scheduler.SampleStatus.CAPTURED_LOCAL
    assert zero[0][17] == pytest.approx(0.50), "best_ask is the held state's ask"
    assert zero[0][15] is not None, "cache_age_ms is on the row"


def test_every_offset_produces_exactly_one_result_row(subject_on):
    pool, mc = FakePool(), _cache_with_state()
    pending = _stamp()
    collector.admit(pending, was_insert=True)
    asyncio.run(_drive(pool, mc, pending))

    samples = pool.rows("rn1_obs_samples")
    assert {a[4] for a in samples} == {0, 10, 20}, "one row per offset"
    assert all(a[9] == scheduler.SampleStatus.CAPTURED_LOCAL for a in samples)


def test_the_sample_instants_do_not_go_backwards(subject_on):
    """Acceptance 5, at the event level, on the monotonic family only."""
    pool, mc = FakePool(), _cache_with_state()
    pending = _stamp()
    collector.admit(pending, was_insert=True)
    asyncio.run(_drive(pool, mc, pending))

    by_offset = {a[4]: a[6] for a in pool.rows("rn1_obs_samples")}
    assert by_offset[0] <= by_offset[10] <= by_offset[20]


def test_two_offsets_sharing_one_cached_state_are_visibly_the_same_state(subject_on):
    """THE HONEST REPLACEMENT for V1's 'the curve moved' assertion.

    A polled cache resolves nothing finer than its refresh interval, so offsets
    inside one interval return the same state. That is not a flat curve being
    passed off as a measurement -- it is two rows that SAY they are the same
    state, via an identical venue_book_hash and a cache_age_ms that grows by the
    gap between them. The analysis reports distinct_state_fraction from exactly
    this before it reports any price figure.
    """
    pool, mc = FakePool(), _cache_with_state(ask=0.50, vhash="same-hash")
    pending = _stamp()
    collector.admit(pending, was_insert=True)
    asyncio.run(_drive(pool, mc, pending))

    rows = {a[4]: a for a in pool.rows("rn1_obs_samples")}
    assert rows[0][17] == rows[20][17], "no refresh happened, so no price moved"
    assert rows[0][26] == rows[20][26] == "same-hash", (
        "the venue's own content hash must be on both rows: it is what lets the "
        "analysis tell 'the price did not move' from 'we did not look again'")
    assert rows[20][15] > rows[0][15], (
        "cache_age_ms must grow between offsets sharing one state -- otherwise a "
        "nominal 20ms point masquerades as a true 20ms measurement")


# ------------------------------------------------------------------------- 9
def test_the_depth_ladder_is_stored_exactly(subject_on):
    pool, mc = FakePool(), _cache_with_state(ask=0.50)
    pending = _stamp()
    collector.admit(pending, was_insert=True)
    asyncio.run(_drive(pool, mc, pending))

    zero = [a for a in pool.rows("rn1_obs_samples") if a[4] == 0][0]
    assert zero[19] == 2, "depth_levels matches the ladder length"
    # The ladder itself round-trips through the cache unchanged.
    assert mc.states["token-1"].asks == [(0.50, 100.0), (0.51, 250.0)]


def test_a_malformed_level_is_skipped_not_coerced_to_zero():
    """A zero-size level would silently change a VWAP. A shorter ladder can only
    make a row DEPTH_EXHAUSTED, which the analysis already reports honestly."""
    assert _levels([["0.5", "10"], ["bad", "10"], ["0.6", None], ["0.7", "5"]]) \
        == [(0.5, 10.0), (0.7, 5.0)]


def test_the_vwap_columns_are_not_yet_populated(subject_on):
    """AN ACKNOWLEDGED GAP, asserted so it cannot be mistaken for coverage.

    rn1_obs_samples carries vwap_qa / qa_depth_exhausted / vwap_qb /
    qb_depth_exhausted because the pre-registration's primary endpoint needs
    them, and the V2 sampler does not fill them in yet. Pinning the NULL is the
    difference between a known gap and a green test over unwritten code.
    """
    pool, mc = FakePool(), _cache_with_state()
    pending = _stamp()
    collector.admit(pending, was_insert=True)
    asyncio.run(_drive(pool, mc, pending))
    zero = [a for a in pool.rows("rn1_obs_samples") if a[4] == 0][0]
    assert zero[20] is None and zero[21] is None, (
        "vwap_qa is now populated -- remove this test and assert the real "
        "Q_A walk, including DEPTH_EXHAUSTED, instead of the NULL")


# ------------------------------------------------------------------------ 10
def test_a_replayed_event_is_not_a_second_observation(subject_on):
    """A restart re-observing the same source event records ONE observation."""
    pool, mc = FakePool(), _cache_with_state()
    for _ in range(3):
        pending = _stamp("same-key")
        collector.admit(pending, was_insert=True)
        asyncio.run(_drive(pool, mc, pending))

    assert len(pool.rows("rn1_obs_events")) == 3, "three inserts were attempted"
    assert len(pool.seen_source_ids) == 1, "one survived the unique key"
    # AND NO SLOT WAS PLANNED FOR THE REPLAYS. The event insert's ON CONFLICT is
    # consulted before any plan row is written, so a replay costs one refused
    # insert and nothing else -- not ten plan rows and ten samples.
    assert len(pool.seen_slots) == 3, "one plan per offset, from the first pass"
    assert len(pool.rows("rn1_obs_plan")) == 3


def test_a_replay_that_is_not_a_first_receipt_never_reaches_the_plan(subject_on):
    """The canonical dedupe refuses it at admission, before any row exists."""
    pool, mc = FakePool(), _cache_with_state()
    pending = _stamp("never-admitted")
    assert not collector.admit(pending, was_insert=False)
    assert pool.statements == [], "a refused event wrote nothing at all"


# ------------------------------------------------------------------------ 11
def test_the_writer_issues_no_update_or_delete():
    """Append-only, checked over the writer's actual SQL.

    Including the V2 statements: _EVENT_V2_SQL, _PLAN_SQL, _SAMPLE_SQL and
    _FEED_SQL. migration 063 puts rn1_obs_append_only() on all three new tables,
    so an UPDATE here would raise in production rather than fail quietly -- and
    that is exactly how the first draft of insert_event_v2 was caught trying to
    fill the subject columns with a follow-up UPDATE.
    """
    src = pathlib.Path(record.__file__).read_text()
    for statement in ("UPDATE rn1_obs", "DELETE FROM rn1_obs", "TRUNCATE"):
        assert statement not in src, f"the obs writer contains {statement!r}"
    for name in ("_EVENT_V2_SQL", "_PLAN_SQL", "_SAMPLE_SQL", "_FEED_SQL"):
        assert name in src, f"{name} is missing from the writer"
    assert record._PLAN_SQL.strip().startswith("INSERT INTO rn1_obs_plan")
    assert record._SAMPLE_SQL.strip().startswith("INSERT INTO rn1_obs_samples")
    assert "ON CONFLICT (observation_slot_id) DO NOTHING" in record._PLAN_SQL
    assert "ON CONFLICT (observation_slot_id) DO NOTHING" in record._SAMPLE_SQL


def test_the_v2_event_insert_carries_the_subject_columns():
    """The subject cannot arrive by a later UPDATE; it must be in the INSERT."""
    sql = record._EVENT_V2_SQL
    assert sql.strip().startswith("INSERT INTO rn1_obs_events")
    for col in ("subject_whale_id", "subject_wallet_address", "subject_username",
                "subject_admission_reason", "canonical_was_insert",
                "admission_monotonic", "admission_delay_ms",
                "preregistration_version"):
        assert col in sql, f"{col} is not in the V2 event insert"
    assert "$58" in sql, "the parameter list was not extended to match"
    assert "ON CONFLICT (source_event_id) DO NOTHING" in sql


def test_the_migration_enforces_append_only_on_every_new_table():
    sql = (BACKEND / "migrations" / "063_rn1_observability_v2.sql").read_text()
    for table in ("rn1_obs_plan", "rn1_obs_samples", "rn1_obs_feed_events"):
        assert f"CREATE TRIGGER {table}_append_only" in sql
        assert f"BEFORE UPDATE OR DELETE ON {table}" in sql
    # And it must not touch the sealed V1 tables beyond adding NULL columns.
    for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM rn1_obs",
                      "ALTER COLUMN", "DROP COLUMN"):
        assert forbidden not in sql, (
            f"migration 063 contains {forbidden!r}; RUN83_ACTIVATION_FAILED_V1 "
            f"is sealed in those tables and its digests must stay valid")


def test_the_migration_requires_a_capture_to_carry_its_age():
    sql = (BACKEND / "migrations" / "063_rn1_observability_v2.sql").read_text()
    assert "rn1_obs_samples_capture_has_age_ck" in sql
    assert "status <> 'CAPTURED_LOCAL' OR cache_age_ms IS NOT NULL" in sql


def test_a_local_sample_cannot_carry_an_http_request_in_the_schema():
    sql = (BACKEND / "migrations" / "063_rn1_observability_v2.sql").read_text()
    assert "rn1_obs_samples_local_has_no_request_ck" in sql


# ------------------------------------------------------------------------ 12
def test_stamp_and_admit_swallow_every_failure(monkeypatch, subject_on):
    """Instrumentation failure must not reach the ingestion path."""
    def boom(*a, **k):
        raise RuntimeError("clock exploded")

    monkeypatch.setattr(collector.clock, "now", boom)
    before = collector.STATS["stamp_exceptions"]
    assert collector.stamp(
        source_event_id="x", source_lane="chain", source_venue="polymarket",
        source_token_id="t", source_ts=clock.SourceTimestamp.missing("p"),
        ingest_worker="test", subject_whale_id=2) is None
    assert collector.STATS["stamp_exceptions"] == before + 1


def test_admit_swallows_a_broken_queue(monkeypatch, subject_on):
    class Exploding:
        def put_nowait(self, _):
            raise RuntimeError("queue exploded")

    pending = _stamp()
    monkeypatch.setattr(collector, "_QUEUE", Exploding())
    before = collector.STATS["admit_exceptions"]
    assert collector.admit(pending, was_insert=True) is False
    assert collector.STATS["admit_exceptions"] == before + 1


def test_stamp_is_a_no_op_when_the_flag_is_off(monkeypatch):
    monkeypatch.delenv("RN1_OBSERVABILITY_SHADOW", raising=False)
    monkeypatch.setenv("RN1_OBSERVABILITY_SUBJECT_WHALE_ID", "2")
    before = collector.STATS["dropped_disabled"]
    assert _stamp() is None
    assert collector.STATS["dropped_disabled"] == before + 1


def test_stamp_is_a_no_op_when_no_subject_is_configured(monkeypatch):
    """DELIBERATELY FAILING PRE-FIX: V1 had no such check.

    The flag alone used to be enough, which is how 94.671% of the first cohort
    came from wallets nobody had nominated.
    """
    monkeypatch.setenv("RN1_OBSERVABILITY_SHADOW", "true")
    monkeypatch.delenv("RN1_OBSERVABILITY_SUBJECT_WHALE_ID", raising=False)
    monkeypatch.setattr(collector, "_QUEUE", asyncio.Queue(maxsize=8))
    before = collector.STATS["dropped_not_configured"]
    assert _stamp() is None, (
        "the collector stamped an observation with no subject configured")
    assert collector.STATS["dropped_not_configured"] == before + 1


def test_a_full_queue_drops_and_counts_rather_than_blocking(monkeypatch,
                                                            subject_on):
    """The collector must never slow the path it is attached to."""
    monkeypatch.setattr(collector, "_QUEUE", asyncio.Queue(maxsize=1))
    first = _stamp("k1")
    assert collector.admit(first, was_insert=True) is True
    before = collector.STATS["dropped_queue_full"]
    second = _stamp("k2")
    assert collector.admit(second, was_insert=True) is False
    assert collector.STATS["dropped_queue_full"] == before + 1


# --------------------------------------------------------------- 1, and labels
def _module_literals(path: pathlib.Path) -> set[str]:
    """Every name, attribute and string LITERAL in a module, minus docstrings."""
    tree = ast.parse(path.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                out.add(node.value)
    return out


def test_the_collector_never_reads_or_writes_mirror_live():
    """The collector observes while trading is paused, and cannot change that.

    Acceptance 1 as a property of the code, not of one run: the string is absent
    from every module in the package, so no branch can reach the switch.
    """
    obs = BACKEND / "sportsassets" / "obs"
    for path in sorted(obs.glob("*.py")):
        literals = _module_literals(path)
        assert "mirror_live" not in literals, f"{path.name} names mirror_live"
        assert "ingestion_state" not in literals, (
            f"{path.name} reaches ingestion_state, where the switch lives")


def test_every_sample_names_its_observation_channel(subject_on):
    """LOCAL_CACHE_BATCH_POLL_PATH is a label, not a claim about speed."""
    pool, mc = FakePool(), _cache_with_state()
    pending = _stamp()
    collector.admit(pending, was_insert=True)
    asyncio.run(_drive(pool, mc, pending))
    assert all(a[3] == _CHANNEL for a in pool.rows("rn1_obs_samples"))
    assert all(a[3] == _CHANNEL for a in pool.rows("rn1_obs_plan"))


def test_no_row_claims_to_be_a_stream(subject_on):
    """FAST_STREAM_PATH stays unimplemented: no vendor feed exists to be it.

    Established from py-clob-client 0.34.6 -- the SDK has no websocket client at
    all. Writing a polled cache's rows under a stream's name would be the same
    error as LEGACY_COMPARABLE_BOOK_PATH claiming to be the fastest available.
    """
    pool, mc = FakePool(), _cache_with_state()
    pending = _stamp()
    collector.admit(pending, was_insert=True)
    asyncio.run(_drive(pool, mc, pending))
    for a in pool.rows("rn1_obs_samples"):
        assert a[3] != ObservationChannel.FAST_STREAM_PATH
        assert a[32] == obs_cache.Continuity.UNVERIFIED, (
            "continuity was claimed as verified. The venue supplies a book hash "
            "and NO sequence number, so a gap between polls cannot be ruled "
            "out and continuity stays unverified by standing rule.")


def test_the_fast_stream_path_is_named_but_not_implemented():
    """The channel constant exists; no module claims it, and none opens a socket.

    Checked over the AST, not the text: cache.py's docstring EXPLAINS that the
    SDK has no websocket client, and a substring scan of prose cannot tell an
    explanation from an implementation. (The first version of this test failed
    on exactly that.)
    """
    cache_py = BACKEND / "sportsassets" / "obs" / "cache.py"
    literals = _module_literals(cache_py)
    assert "FAST_STREAM_PATH" not in literals, (
        "cache.py labels its rows FAST_STREAM_PATH. It is a polled cache.")
    # No websocket client anywhere in the package, and no ws endpoint literal.
    obs = BACKEND / "sportsassets" / "obs"
    for path in sorted(obs.glob("*.py")):
        tree = ast.parse(path.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "websockets" not in imported and "websocket" not in imported, (
            f"{path.name} imports a websocket client. FAST_STREAM_PATH stays "
            f"unimplemented until a real pushed feed is established.")
        assert not [s for s in _module_literals(path)
                    if s.startswith("wss://")], (
            f"{path.name} carries a wss:// endpoint literal")


def test_the_cache_sample_performs_no_io():
    """The scheduled operation must be a lookup. This is the V2 thesis."""
    tree = ast.parse((BACKEND / "sportsassets" / "obs" / "cache.py").read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "sample")
    assert not isinstance(fn, ast.AsyncFunctionDef)
    assert not [n for n in ast.walk(fn) if isinstance(n, ast.Await)], (
        "MarketStateCache.sample() awaits something. A request started after "
        "receipt cannot observe receipt-time state -- measured p50 duration was "
        "170ms, so the 0ms and 100ms offsets would be inside the round trip.")


def test_a_cache_refresh_failure_invalidates_rather_than_trusting_stale_state():
    mc = _cache_with_state()
    assert mc.states["token-1"].invalidated is False
    assert mc.invalidate_all("refresh failed: http_503") == 1
    s = mc.sample("token-1")
    assert s.validity == obs_cache.StateValidity.INVALIDATED
    assert s.cache_age_ms is not None, "an invalid state still reports its age"


def test_the_books_response_parser_keeps_the_venue_timestamp_and_hash():
    states = obs_cache.parse_books_response([{
        "asset_id": "tok", "market": "cond", "timestamp": "1757000000",
        "hash": "abc123", "bids": [["0.4", "10"]], "asks": [["0.42", "20"]]}])
    assert len(states) == 1
    assert states[0].venue_ts == "1757000000"
    assert states[0].venue_hash == "abc123"
    assert states[0].asks == [(0.42, 20.0)]


def test_the_parser_stamps_one_arrival_instant_for_the_whole_batch():
    """They DID arrive in one response; pretending otherwise would be invention."""
    states = obs_cache.parse_books_response([
        {"asset_id": "a", "bids": [], "asks": []},
        {"asset_id": "b", "bids": [], "asks": []}])
    assert states[0].received.monotonic == states[1].received.monotonic
