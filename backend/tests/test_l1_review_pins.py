"""L1 review pins (2026-09-07): the loss-stop re-arm restarts the window.

What the review probed beyond the builder's pins, in the order the
brief's rule states them:

1. THE WINDOW ONLY EVER SHRINKS TOWARD THE RE-ARM INSTANT, NEVER PAST
   NOW. A re-arm `at` in the future -- a hand-edited row, or a DB clock
   ahead of the worker's -- must not hand the statement a start the
   rows can never pass: with `at` = now + 1 h the sum reads 0 for an
   hour, with `at` = now + 10 y the loss stop is off for ten years. The
   fail-closed reading, pinned here: an `at` beyond a small skew
   tolerance is MALFORMED (absent, the FULL window, the one warning),
   never a shorter window; an `at` inside the tolerance (a DB clock a
   second or two ahead) is the re-arm it says it is and the tick holds
   (never re-tripping on the morning's losses for the skew's duration).
2. A standing stop key holds against any re-arm key -- older, newer,
   malformed, unreadable -- and the re-arm key is not even read.
3. Malformed shapes the builder did not list: an empty `at`, a date
   with no clock (naive), a lowercase `z`, an epoch as a string, a JSON
   null `at`, an empty object, a list `at`, a doubled offset.
4. A re-arm older than the window through the tick: the receipt says
   `since` = the edge and `rearmed_at` = the old instant (not null: it
   is a valid key, just an old one).
5. The receipt's bounds and the mode line's, the preset's shape as the
   runner executes it (one -c string, one `;` pair, the CTE inside its
   own statement, the ON CONFLICT target the migration declares).
6. Killers for the mutants the builder's pins let live (none survived
   in the review's run; the boundary and read-order pins below are
   kept as belt and braces).
"""

from __future__ import annotations

import inspect
import json
import logging
import pathlib
import re
from datetime import datetime

import pytest
import yaml

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_l1_rearm_window import (H24, REARM_AT, TRIPPED, _loss_reads, _morning_and_after, _rearm,
                                        _reduce_world)
from tests.test_mirror_live_worker import GTC_TIF, IOC_TIF, NOW, SLUG, _armed  # noqa: F401 — the fixture
from tests.test_mirror_live_worker import _census, _places, _pool, _tick, _Venue


@pytest.fixture(autouse=True)
def _fixtures_limit(monkeypatch):
    """The fixtures here are test_l1_rearm_window's, cut at the $5,000 stop;
    the live default is the owner's $10,000 since 2026-09-09 (pinned by
    source there). The tick reads the attribute at tick time."""
    monkeypatch.setattr(rules, "MIRROR_LOSS_STOP_USD", 5000.0)

RENDER_OPS = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "001_init.sql"


def _rearm_reads(p):
    return [x for x in p.sent if "SELECT value FROM ingestion_state" in x[1] and x[2] and x[2][0] == "mirror_loss_rearm"]


def _warned(caplog):
    return [x for x in caplog.records if "mirror_loss_rearm malformed" in x.getMessage()]


# ------------------------------------ 1. a re-arm in the future (clock skew)

def test_review_the_window_start_never_passes_now():
    """The start is GREATEST(now - 24 h, at) clamped to now at most: a
    re-arm instant ahead of the tick's clock cannot open an empty window
    the rows can never enter."""
    assert ml._loss_window_start(NOW, NOW + 1) <= NOW
    assert ml._loss_window_start(NOW, NOW + 3600) <= NOW
    assert ml._loss_window_start(NOW, NOW + 10 * 365 * 86400) <= NOW
    # and never wider than the window's own edge
    assert ml._loss_window_start(NOW, NOW + 3600) >= NOW - H24


@pytest.mark.parametrize("ahead", [3600, 30 * 86400, 10 * 365 * 86400])
def test_review_a_re_arm_far_in_the_future_reads_as_malformed_and_the_full_window_stands(ahead, caplog):
    """`at` an hour or more ahead of the tick's clock is not a re-arm
    the worker can key a window to: absent, the FULL window (the
    parameter handed is the 24 h edge), the one warning, and the
    morning's losses trip the stop exactly as with no key. Fail closed
    toward the wider window -- never a shorter (empty) one."""
    p = _pool()
    _morning_and_after(p)
    p.state["mirror_loss_rearm"] = _rearm(at=NOW + ahead, prior=TRIPPED)
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, _Venue())
    reads = _loss_reads(p)
    assert len(reads) == 1
    assert reads[0][2][0].timestamp() == pytest.approx(NOW - H24, abs=1.0), "the full window, not an empty one"
    assert _census(st, "mirror_loss_stop") >= 1
    r = p.state["mirror_loss_stop"]
    assert r["sum"] == pytest.approx(-5123.9545) and r["rearmed_at"] is None
    assert r["since"] == ml._iso(NOW - H24)
    assert len(_warned(caplog)) == 1, [x.getMessage() for x in caplog.records]


def test_review_a_re_arm_a_moment_ahead_of_the_workers_clock_is_the_re_arm_it_says(caplog):
    """A DB clock two seconds ahead of the worker's stamps every re-arm
    two seconds into the worker's future for its first tick: that is
    NOT malformed (the full window would re-trip the stop on the
    morning's losses within the skew -- the 16:51Z failure again), and
    the start handed is never past the re-arm's instant nor back at
    the window's edge: the tick holds."""
    p = _pool()
    _morning_and_after(p)
    p.state["mirror_loss_rearm"] = _rearm(at=NOW + 2, prior=TRIPPED)
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, _Venue())
    assert not _warned(caplog)
    reads = _loss_reads(p)
    assert len(reads) == 1
    start = reads[0][2][0].timestamp()
    assert NOW - 60 <= start <= NOW + 2, start
    assert _census(st, "mirror_loss_stop") == 0 and "mirror_loss_stop" not in p.state
    assert ml._last_loss["rearmed_at"] == ml._iso(NOW + 2)
    assert ml._last_loss["sum"] >= -100.0, "never the morning's losses"


# ------------------------------------------------ 2. the standing stop key

@pytest.mark.parametrize("rearm", [
    _rearm(at=NOW - 10, prior=None),                     # newer than the stop
    _rearm(at=NOW - 10 * 3600, prior=None),              # older than the stop
    _rearm(at=NOW + 3600, prior=None),                   # in the future
    {"by": "render-ops"},                                # malformed
    "{not json",                                         # unparseable
])
def test_review_a_standing_stop_holds_and_the_re_arm_key_is_not_even_read(rearm, caplog):
    p, v = _reduce_world()
    p.state["mirror_loss_stop"] = dict(TRIPPED)
    p.state["mirror_loss_rearm"] = rearm
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, v)
    assert _census(st, "mirror_loss_stop") >= 1
    assert p.state["mirror_loss_stop"] == TRIPPED, "the receipt is not rewritten"
    assert not _loss_reads(p) and not _rearm_reads(p), "nothing read behind a standing stop"
    assert not _warned(caplog) and ml._last_loss is None
    assert ("cancel", "oid-1", SLUG) in v.calls
    pl = _places(v)
    assert [c[3:6] for c in pl] == [(200, True, IOC_TIF), (200, True, GTC_TIF)], "the reduce goes out: the IOC, then its same-tick rest at his cent (E14b)"


def test_review_an_unreadable_re_arm_key_behind_a_standing_stop_is_still_the_stop(monkeypatch):
    calls = []
    orig = ml._state

    async def _st(pool, key):
        calls.append(key)
        if key == "mirror_loss_rearm":
            raise AssertionError("the re-arm key must not be read behind a standing stop")
        return await orig(pool, key)
    monkeypatch.setattr(ml, "_state", _st)
    p, v = _reduce_world()
    p.state["mirror_loss_stop"] = dict(TRIPPED)
    st = _tick(p, v)
    assert _census(st, "mirror_loss_stop") >= 1 and "mirror_loss_rearm" not in calls
    assert p.state["mirror_loss_stop"] == TRIPPED


def test_review_the_stop_key_is_read_before_the_re_arm_key(monkeypatch):
    """The order of the two reads: the stop's first, the re-arm's only
    with no stop standing (a mutant reading the re-arm and the sum
    first, then blocking, would still spend a sum read per tick behind
    the stop and could trip a receipt over the standing one)."""
    calls = []
    orig = ml._state

    async def _st(pool, key):
        calls.append(key)
        return await orig(pool, key)
    monkeypatch.setattr(ml, "_state", _st)
    p = _pool()
    p.state["mirror_loss_rearm"] = _rearm()
    _tick(p, _Venue())
    assert calls.index("mirror_loss_stop") < calls.index("mirror_loss_rearm")
    assert len(_loss_reads(p)) == 1


# ------------------------------------ 3. malformed shapes the builder did not list

@pytest.mark.parametrize("value", [
    {"at": "", "by": "render-ops"},                              # empty
    {"at": "2026-09-07", "by": "render-ops"},                    # a date, no clock: naive
    {"at": "2026-09-07T17:30:00z", "by": "render-ops"},          # lowercase z
    {"at": "1788802200", "by": "render-ops"},                    # an epoch as a string
    {"at": None, "by": "render-ops"},                            # JSON null
    {},                                                          # an empty object
    {"at": ["2026-09-07T17:30:00Z"], "by": "render-ops"},        # a list
    {"at": "2026-09-07T17:30:00+00:00Z", "by": "render-ops"},    # a doubled offset
    {"at": "2026-09-07T17:30:00 UTC", "by": "render-ops"},       # a zone word
    {"at": "2026-13-45T00:00:00Z", "by": "render-ops"},          # no such date
    [],
    0,
    "",
])
def test_review_more_malformed_shapes_read_as_absent_and_the_full_window_stands(value, caplog):
    assert ml._rearm_at(value, None) is None
    p = _pool()
    _morning_and_after(p)
    p.state["mirror_loss_rearm"] = value
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, _Venue())
    assert _census(st, "mirror_loss_stop") >= 1
    r = p.state["mirror_loss_stop"]
    assert r["sum"] == pytest.approx(-5123.9545) and r["rearmed_at"] is None
    assert r["since"] == ml._iso(NOW - H24)
    reads = _loss_reads(p)
    assert len(reads) == 1 and reads[0][2][0].timestamp() == pytest.approx(NOW - H24, abs=1.0)
    # JSON null and "" are what _state hands back as (None, None) / ("", None):
    # absent-shaped values may log nothing; every other shape logs exactly once
    assert len(_warned(caplog)) <= 1


def test_review_the_shapes_the_preset_and_the_worker_write_parse():
    """The two producers' shapes: Postgres's to_jsonb(now()) under a UTC
    session and under a non-UTC one (the runner's session zone is the
    server's), and the worker's own _iso (seconds, Z)."""
    utc = ml._rearm_at({"at": "2026-09-07T18:01:14.649851+00:00", "by": "render-ops", "prior": None}, None)
    ny = ml._rearm_at({"at": "2026-09-07T14:01:14.649851-04:00", "by": "render-ops", "prior": None}, None)
    assert utc == ny == pytest.approx(datetime.fromisoformat("2026-09-07T18:01:14.649851+00:00").timestamp())
    assert ml._rearm_at({"at": ml._iso(REARM_AT)}, None) == pytest.approx(REARM_AT, abs=1.0)


# ---------------------------------------- 4. an old re-arm through the tick

def test_review_a_re_arm_older_than_the_window_through_the_tick_carries_rearmed_at_and_the_edge(caplog):
    p = _pool()
    _morning_and_after(p)
    p.state["mirror_loss_rearm"] = _rearm(at=NOW - 25 * 3600, prior=None)
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, _Venue())
    assert not _warned(caplog), "a valid old key is not malformed"
    assert _census(st, "mirror_loss_stop") >= 1
    r = p.state["mirror_loss_stop"]
    assert r["since"] == ml._iso(NOW - H24) and r["rearmed_at"] == ml._iso(NOW - 25 * 3600)
    assert r["sum"] == pytest.approx(-5123.9545) and r["books"] == 2
    assert _loss_reads(p)[0][2][0].timestamp() == pytest.approx(NOW - H24, abs=1.0)


def test_review_the_trip_boundary_is_the_limit_itself():
    """lost == -limit trips (<=), as before L1."""
    stop = float(rules.MIRROR_LOSS_STOP_USD)
    p = _pool()
    p.add_book(ledger=0, state="closed", realized_pnl=-stop, settled_pnl=None,
               closed_at=NOW - 600, updated_ts=NOW - 600, opened_ts=NOW - 900)
    p.state["mirror_loss_rearm"] = _rearm()
    st = _tick(p, _Venue())
    assert _census(st, "mirror_loss_stop") >= 1
    assert p.state["mirror_loss_stop"]["sum"] == pytest.approx(-stop)


# -------------------------------------------- 5. bounds: receipt, mode line, preset

def test_review_the_receipt_is_bounded_and_its_instants_parse():
    p = _pool()
    _morning_and_after(p)
    _tick(p, _Venue())
    r = p.state["mirror_loss_stop"]
    assert set(r) == {"at", "sum", "books", "limit", "since", "rearmed_at"}
    for k in ("at", "since"):
        d = datetime.fromisoformat(r[k].replace("Z", "+00:00"))
        assert d.tzinfo is not None and len(r[k]) == 20 and r[k].endswith("Z")
    assert r["rearmed_at"] is None
    assert len(json.dumps(r)) < 240, "mirror-preflight prints left(value::text, 240)"
    p2 = _pool()
    _morning_and_after(p2)
    p2.state["mirror_loss_rearm"] = _rearm(at=NOW - 25 * 3600, prior={"huge": "x" * 100000})
    _tick(p2, _Venue())
    r2 = p2.state["mirror_loss_stop"]
    assert "prior" not in r2 and len(json.dumps(r2)) < 240, "the prior never rides into the receipt"


def test_review_the_mode_line_fragment_is_bounded_whatever_the_window_holds(caplog):
    stats = ml._new_stats()
    stats.update(mode=ml.MODE_ON, whales=["rn1"], books_live=2, orders_open=1)
    loss = {"sum": -1e300, "books": 10 ** 9, "limit": 5000.0, "since": None, "rearmed_at": "y" * 1000,
            "prior": "z" * 5000}
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS, loss)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode="))
    head = line[:line.index(" venue=")]
    assert " loss=-1e+300/5000.0 since " in head and "y" * 5 not in head and "z" * 5 not in head
    assert len(head) < 200 and "prior" not in head
    # a real tick's reading renders to the minute
    p = _pool()
    _morning_and_after(p)
    p.state["mirror_loss_rearm"] = _rearm()
    st = _tick(p, _Venue())
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(st, ml.MODE_LINE_EVERY_TICKS, ml._last_loss)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode="))
    assert re.search(r" loss=-100\.0/5000\.0 since \d\d:\d\d venue=", line), line


def test_review_the_preset_as_the_runner_executes_it():
    """One `-c` string, double-quoted in bash; two statements; the CTE
    referenced only inside its own INSERT; ON CONFLICT (key) against
    the PRIMARY KEY the migration declares; ON_ERROR_STOP set, so a
    failure anywhere in the string rolls the whole transaction back."""
    src = RENDER_OPS.read_text()
    wf = yaml.safe_load(src)
    runs = [str(s.get("run") or "") for job in wf["jobs"].values() for s in (job.get("steps") or [])]
    run = next(r for r in runs if "mirror-rearm)" in r)
    m = re.search(r'mirror-rearm\) need_confirm; SQL="(.*?)"; TO=30000 ;;', run)
    sql = m.group(1)
    assert 'psql "$EXT" -X -v ON_ERROR_STOP=1 -c "$SQL"' in run
    stmts = [s for s in sql.split(";") if s.strip()]
    assert len(stmts) == 2 and sql.rstrip().endswith(";")
    assert stmts[0].count("gone") == 2 and "gone" not in stmts[1]      # defined once, read once
    assert "(SELECT value FROM gone)" in stmts[0], "a scalar subquery: null with no stop, the row with one"
    assert "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value" in stmts[0]
    assert re.search(r"CREATE TABLE IF NOT EXISTS ingestion_state \(\s*key\s+TEXT PRIMARY KEY", MIGRATION.read_text())
    assert not re.search(r"[$`\\\"]", sql), "nothing bash expands inside the double quotes"
    assert len(sql) < 600
    # the DELETE and the INSERT are one statement: neither can land without the other
    assert stmts[0].strip().startswith("WITH gone AS (DELETE FROM ingestion_state WHERE key = 'mirror_loss_stop'")
    assert "INSERT INTO ingestion_state (key, value) VALUES ('mirror_loss_rearm'" in stmts[0]
    assert "mirror-rearm (DO)" in src and "need_confirm;" in run[run.index("mirror-rearm)"):][:40]


# ------------------------------------------------- 6. what the code must keep

def test_review_the_re_arm_read_sits_inside_loss_stop_only():
    """The re-arm key is read in one place, _loss_stop, and nothing
    else in the worker reads or writes it (only the preset writes it)."""
    src = inspect.getsource(ml)
    assert src.count("_state(t.pool, _STATE_LOSS_REARM)") == 1
    assert "_write_state(t.pool, _STATE_LOSS_REARM" not in src and "_write_state(pool, _STATE_LOSS_REARM" not in src
    # L2: the one read is _read_rearm (cached on the tick); _loss_stop and
    # the sleeve read in _global_guards both go through it, neither reads
    # the key itself
    assert "_STATE_LOSS_REARM" in inspect.getsource(ml._read_rearm)
    assert "_read_rearm(t)" in inspect.getsource(ml._loss_stop)
    assert "_read_rearm(t)" in inspect.getsource(ml._global_guards)
    assert "_state(t.pool, _STATE_LOSS_REARM)" not in inspect.getsource(ml._loss_stop)
    assert "_state(t.pool, _STATE_LOSS_REARM)" not in inspect.getsource(ml._global_guards)
