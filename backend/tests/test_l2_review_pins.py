"""L2 review pins (2026-09-07): the copy sleeve's breaker over the mirror's window.

What the review probed beyond the builder's pins:

1. THE DECISION ORDER, BEHAVIOURALLY. L1's text pin on `_STATE_LOSS_STOP`
   was loosened to an rindex (the key is now read first, for the sleeve);
   the order it protected -- loss_breaker, then room, then the day cap,
   then the stop -- is pinned here on the census names two guards at a
   time produce, with the reduce still out.
2. THE STOP KEY UNREADABLE OR MALFORMED ON ITS OWN. Behind a standing stop
   the builder pinned that the re-arm key is not read; behind a stop key
   whose read RAISES, or whose value is not JSON, the same must hold (the
   sleeve reads its full window, the tick is exits-only by the stop's
   name, no receipt is written). Two mutants the builder's pins let live.
3. A RE-ARM `at` IN THE FUTURE FOR THE SLEEVE: beyond the skew tolerance
   the sleeve reads the FULL window (never an empty one) and a -6,000 day
   still refuses; inside it the start is clamped to the tick's clock. A
   mutant dropping `t.now` from the sleeve's `_rearm_at` call read an empty
   window and OPENED under -6,000; the builder's pins let it live.
4. THE THRESHOLD IS THE SLEEVE'S OWN. Both limits are 5,000 in the fixture,
   so a mutant reading `rules.MIRROR_LOSS_STOP_USD` survived every pin:
   here the two are set apart.
5. THE PARAMETER, END TO END. The builder's tick pins replace the sum
   helper; this drives the real helper through the fake and pins the text
   and the one aware-UTC argument the worker sends (asyncpg refuses any
   other shape with DataError, which the helper reads as unreadable).
6. `_last_sleeve` cleared on an overlap tick (the reset the source pin
   names, behaviourally).
7. THE PRESET AGAINST POSTGRES (skips with none): absent / no-`at` /
   null-`at` / inside-24 h keys, the `since` label against the worker's
   start, and the worker's since-text against the same rows -- which the
   preset's rows only reproduce once `manual` / `underdog` are dropped.
   Two pins here FAIL on the L2 tree and are meant to: a naive `at` and a
   future `at` read narrower than the worker does (the review's finding).
8. THE ERROR LINE. Regenerated from a one-space regex, it dropped five
   valid presets (`tables`, `sizes`, `indexes`, `analyze`, `mirror-on`).
   This pin FAILS on the L2 tree and is meant to.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
import re
import time
from datetime import timedelta

import pytest
import yaml

from sportsassets import live_executor as le
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_l1_rearm_window import H24, TRIPPED, _loss_reads, _rearm, _reduce_world
from tests.test_l2_sleeve_window import REARM_DT, REARM_TS, SQL_24H, _recorder
from tests.test_mirror_live_worker import NOW, SLUG, _armed  # noqa: F401 — the fixture
from tests.test_mirror_live_worker import _census, _places, _pool, _tick, _Venue

RENDER_OPS = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"


@pytest.fixture(autouse=True)
def _threshold(monkeypatch):
    monkeypatch.setattr(le, "PMUS_LOSS_BREAKER_USD", 5000.0)


def _sleeve_reads(p):
    """Every sum the tick sent the sleeve's ledger: (sql, args)."""
    return [(x[1], x[2]) for x in p.sent if "sum(pnl)" in x[1]]


def _state_reads(p):
    return [x[2][0] for x in p.sent if "SELECT value FROM ingestion_state" in x[1] and x[2]]


def _reduce_went_out(v):
    pl = _places(v)
    assert ("cancel", "oid-1", SLUG) in v.calls, "the BUY rest is gone"
    assert len(pl) == 1 and pl[0][4] is True, pl


# ------------------------------------------------ 1. the decision order

def test_review_the_sleeve_is_named_before_a_standing_stop(monkeypatch):
    """Both trip: the census says loss_breaker (the first increase-only
    guard), never mirror_loss_stop -- as before L2 -- and the reduce
    goes out. The stop key was read (once) but decided last."""
    _recorder(monkeypatch, {None: -6000.0})
    p, v = _reduce_world()
    p.state["mirror_loss_stop"] = dict(TRIPPED)
    st = _tick(p, v)
    assert _census(st, "loss_breaker") >= 1 and _census(st, "mirror_loss_stop") == 0
    assert _state_reads(p).count("mirror_loss_stop") == 1
    assert p.state["mirror_loss_stop"] == TRIPPED
    _reduce_went_out(v)


def test_review_room_and_the_day_cap_are_still_named_before_the_stop(monkeypatch):
    """The stop key is read first now; its DECISION still sits last: with
    no room and a standing stop the name is no_budget_room, with the day
    cap filled and a standing stop it is mirror_day_cap."""
    async def _room(pool, cfg):
        return 0.0, 0.0
    orig_room = le._copy_day_room
    monkeypatch.setattr(le, "_copy_day_room", _room)
    p, v = _reduce_world()
    p.state["mirror_loss_stop"] = dict(TRIPPED)
    st = _tick(p, v)
    assert _census(st, "no_budget_room") >= 1 and _census(st, "mirror_loss_stop") == 0
    _reduce_went_out(v)
    monkeypatch.setattr(le, "_copy_day_room", orig_room)
    p, v = _reduce_world()
    b = next(b for b in p.books.values() if b["us_market_slug"] == SLUG)
    p.add_order(b, state="filled", cash_usd=2000.0, order_id="old")     # the day arm's shape
    p.state["mirror_loss_stop"] = dict(TRIPPED)
    st = _tick(p, v)
    assert _census(st, "mirror_day_cap") >= 1 and _census(st, "mirror_loss_stop") == 0
    assert _state_reads(p).count("mirror_loss_stop") == 1
    _reduce_went_out(v)


# ------------------------- 2. the stop key unreadable or malformed alone

def test_review_a_stop_key_whose_read_raises_is_the_stop_and_the_re_arm_is_not_read(monkeypatch):
    orig = ml._state
    reads = []

    async def _st(pool, key):
        reads.append(key)
        if key == "mirror_loss_stop":
            return None, "ConnectionDoesNotExistError"
        if key == "mirror_loss_rearm":
            raise AssertionError("the re-arm key must not be read behind an unreadable stop key")
        return await orig(pool, key)
    monkeypatch.setattr(ml, "_state", _st)
    seen = _recorder(monkeypatch, {None: -100.0})
    p, v = _reduce_world()
    p.state["mirror_loss_rearm"] = _rearm()
    st = _tick(p, v)
    assert seen == [None], "the sleeve reads its FULL window behind an unreadable stop key"
    assert _census(st, "mirror_loss_stop") >= 1 and _census(st, "loss_breaker") == 0
    assert reads.count("mirror_loss_stop") == 1 and "mirror_loss_rearm" not in reads
    assert not _loss_reads(p) and "mirror_loss_stop" not in p.state and ml._last_loss is None
    assert ml._last_sleeve == {"sum": -100.0, "limit": 5000.0, "since": None}
    _reduce_went_out(v)


@pytest.mark.parametrize("value", ["{not json", "", "x"])
def test_review_a_malformed_stop_key_is_the_stop_and_the_re_arm_is_not_read(monkeypatch, value):
    """_state hands ("{not json") back as (None, "malformed") and "" / "x"
    likewise: an error, the stop's own stance -- the sleeve's window is the
    full one, the re-arm key stays unread, no receipt overwrites the
    (unreadable) one standing."""
    seen = _recorder(monkeypatch, {None: -100.0})
    p, v = _reduce_world()
    p.state["mirror_loss_stop"] = value
    p.state["mirror_loss_rearm"] = _rearm()
    st = _tick(p, v)
    assert seen == [None]
    assert _census(st, "mirror_loss_stop") >= 1
    assert "mirror_loss_rearm" not in _state_reads(p)
    assert not _loss_reads(p) and p.state["mirror_loss_stop"] == value
    _reduce_went_out(v)


def test_review_a_re_arm_key_of_bad_json_is_the_full_window_for_the_sleeve_not_a_stop(monkeypatch, caplog):
    """_state's own "malformed" (unparseable JSON, the one shape the
    builder's malformed pin did not drive through the sleeve): absent, the
    full window handed as the parameter, the one warning, no stop, and
    with a clean ledger the candidate opens. A mutant reading it as a
    raised read sent the 24 h text instead and survived every pin."""
    seen = _recorder(monkeypatch, {ml._utc(NOW - H24): -10.0})
    p = _pool()
    p.state["mirror_loss_rearm"] = "{not json"
    v = _Venue()
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, v)
    assert seen == [ml._utc(NOW - H24)]
    assert _census(st, "loss_breaker") == 0 and _census(st, "mirror_loss_stop") == 0
    assert [c[1] for c in _places(v)] == [SLUG]
    assert ml._last_sleeve == {"sum": -10.0, "limit": 5000.0, "since": ml._iso(NOW - H24)}
    assert ml._last_loss["rearmed_at"] is None and ml._last_loss["since"] == ml._iso(NOW - H24)
    assert len([x for x in caplog.records if "mirror_loss_rearm malformed" in x.getMessage()]) == 1


# ------------------------------------- 3. a re-arm in the future, the sleeve

@pytest.mark.parametrize("ahead", [3600, 30 * 86400])
def test_review_a_future_re_arm_reads_the_full_sleeve_window_and_refuses(monkeypatch, ahead, caplog):
    """The mirror's own books are clean; the sleeve lost 6,000 over 24 h
    and nothing since the (future) key. Malformed is the FULL window: the
    tick refuses loss_breaker. A mutant handing the sleeve an unclamped
    start read an empty window and opened."""
    seen = _recorder(monkeypatch, {ml._utc(NOW - H24): -6000.0, ml._utc(NOW): 0.0})
    p = _pool()
    p.state["mirror_loss_rearm"] = _rearm(at=NOW + ahead)
    v = _Venue()
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, v)
    assert seen == [ml._utc(NOW - H24)]
    assert _census(st, "loss_breaker") >= 1 and not _places(v)
    assert ml._last_sleeve == {"sum": -6000.0, "limit": 5000.0, "since": ml._iso(NOW - H24)}
    assert len([x for x in caplog.records if "mirror_loss_rearm malformed" in x.getMessage()]) == 1


def test_review_a_re_arm_inside_the_skew_clamps_the_sleeves_start_to_the_tick(monkeypatch):
    seen = _recorder(monkeypatch, {ml._utc(NOW): -10.0})
    p = _pool()
    p.state["mirror_loss_rearm"] = _rearm(at=NOW + 2)
    v = _Venue()
    st = _tick(p, v)
    assert seen == [ml._utc(NOW)] and _census(st, "loss_breaker") == 0
    assert [c[1] for c in _places(v)] == [SLUG]
    assert ml._last_sleeve["since"] == ml._iso(NOW)


# --------------------------------------- 4. the threshold is the sleeve's own

def test_review_the_threshold_is_the_sleeves_not_the_mirrors(monkeypatch):
    assert float(rules.MIRROR_LOSS_STOP_USD) == 5000.0
    monkeypatch.setattr(le, "PMUS_LOSS_BREAKER_USD", 1000.0)
    _recorder(monkeypatch, {REARM_DT: -1500.0})
    p = _pool()
    p.state["mirror_loss_rearm"] = _rearm()
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "loss_breaker") >= 1 and not _places(v)
    assert ml._last_sleeve == {"sum": -1500.0, "limit": 1000.0, "since": ml._iso(REARM_TS)}
    assert _census(st, "mirror_loss_stop") == 0 and "mirror_loss_stop" not in p.state
    monkeypatch.setattr(le, "PMUS_LOSS_BREAKER_USD", 9000.0)
    _recorder(monkeypatch, {REARM_DT: -6000.0})
    p = _pool()
    p.state["mirror_loss_rearm"] = _rearm()
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "loss_breaker") == 0 and [c[1] for c in _places(v)] == [SLUG]
    assert ml._last_sleeve["limit"] == 9000.0


# ------------------------------------------ 5. the parameter, end to end

def test_review_the_worker_sends_the_since_text_with_one_aware_utc_instant():
    """The real helper through the fake: a re-arm inside 24 h sends the
    since-text once, with one argument -- an aware UTC datetime at the
    re-arm's instant -- and never the 24 h text; the fake answers the
    breaker's figure and the tick refuses by name."""
    p = _pool()
    p.state["mirror_loss_rearm"] = _rearm()
    p.lost_24h = -1e6
    v = _Venue()
    st = _tick(p, v)
    reads = _sleeve_reads(p)
    assert len(reads) == 1
    sql, args = reads[0]
    assert sql == " ".join(le._SQL_LOSS_BREAKER_SINCE.split()) and "interval" not in sql and "now()" not in sql
    assert len(args) == 1 and args[0].tzinfo is not None and args[0].utcoffset() == timedelta(0)
    assert args[0] == REARM_DT and args[0].timestamp() == pytest.approx(REARM_TS, abs=1.0)
    assert _census(st, "loss_breaker") >= 1 and not _places(v)
    assert ml._last_sleeve == {"sum": -1e6, "limit": 5000.0, "since": ml._iso(REARM_TS)}
    # no key: the same text, the 24 h edge as the instant; and the copy
    # lane's 24 h text is what a STANDING stop makes the sleeve read
    p = _pool()
    p.lost_24h = -10.0
    v = _Venue()
    _tick(p, v)
    (sql, args), = _sleeve_reads(p)
    assert "$1::timestamptz" in sql and args[0] == ml._utc(NOW - H24)
    assert [c[1] for c in _places(v)] == [SLUG]
    p = _pool()
    p.state["mirror_loss_stop"] = dict(TRIPPED)
    _tick(p, _Venue())
    assert _sleeve_reads(p) == [(SQL_24H, ())]


# ------------------------------------------- 6. the stale reading, cleared

def test_review_an_overlap_tick_clears_the_last_sleeve_reading():
    ml._last_sleeve = {"sum": -1.0, "limit": 5000.0, "since": None}
    ml._last_loss = {"sum": -1.0}
    p = _pool()

    async def _go():
        async with ml._TICK_LOCK:
            return await ml.tick_once(p, _Venue(), None, now_ts=NOW)
    st = asyncio.run(_go())
    assert st["status"] == "overlap" and st["skipped_overlap"] is True
    assert ml._last_sleeve is None and ml._last_loss is None
    assert not _sleeve_reads(p)


# ------------------------------------------- 7. the preset against Postgres

def _preset_sql() -> str:
    wf = yaml.safe_load(RENDER_OPS.read_text())
    runs = [str(s.get("run") or "") for job in wf["jobs"].values() for s in (job.get("steps") or [])]
    run = next(r for r in runs if "loss-breaker)" in r)
    return re.search(r'^\s+loss-breaker\) SQL="(.*?)"; TO=30000 ;;$', run, flags=re.M).group(1)


_ROWS = [  # (lane, whale, status, hours ago, pnl)
    ("mirror", "rn1", "settled", 20.0, -5000.0),
    ("mirror", "rn1", "settled", 0.4, -100.0),
    (None, "swisstony", "cashed_out", 0.3, -50.0),
    (None, "manual", "settled", 0.2, -900.0),
    (None, "underdog", "settled", 0.1, -800.0),
    ("mirror", "rn1", "filled", 0.1, None),
    ("mirror", "rn1", "settled", 30.0, -7000.0),
]


async def _pg_world():
    from tests.test_mirror_loss_sum_real_pg import _scratch
    admin, conn, name = await _scratch()
    await conn.execute("CREATE TABLE live_orders (id bigserial PRIMARY KEY, lane text, whale_username text, "
                       "status text NOT NULL DEFAULT 'filled', settled_at timestamptz, pnl numeric(24,6), "
                       "us_market_slug text)")
    for lane, whale, status, h, pnl in _ROWS:
        await conn.execute("INSERT INTO live_orders (lane, whale_username, status, settled_at, pnl, us_market_slug) "
                           "VALUES ($1, $2, $3, now() - $4::interval, $5, 'slug')",
                           lane, whale, status, timedelta(hours=h), pnl)
    return admin, conn, name


async def _preset_rows(conn, sql):
    """The first statement's rows as psql would print them (the runner's
    -c string is two statements; asyncpg runs one at a time)."""
    first = sql.split(";")[0]
    return [dict(r) for r in await conn.fetch(first)]


async def _with_key(conn, value):
    await conn.execute("DELETE FROM ingestion_state")
    if value is not None:
        await conn.execute("INSERT INTO ingestion_state VALUES ('mirror_loss_rearm', $1::jsonb)", value)


def test_review_the_preset_runs_and_its_since_label_is_the_workers_start():
    from tests.test_mirror_loss_sum_real_pg import _drop

    async def _go():
        admin, conn, name = await _pg_world()
        try:
            sql = _preset_sql()
            now = time.time()
            rearm = now - 1800
            shapes = {
                None: now - H24,
                '{"by": "render-ops"}': now - H24,
                '{"at": null}': now - H24,
                '{"at": "%s", "by": "render-ops", "prior": null}' % ml._iso(rearm): rearm,
                '{"at": "%s"}' % ml._iso(now - 25 * 3600): now - H24,
                "7": now - H24,
                '["x"]': now - H24,
            }
            for value, start in shapes.items():
                await _with_key(conn, value)
                rows = await _preset_rows(conn, sql)
                labels = {r["window"] for r in rows}
                assert len(labels) == 2 and "sleeve 24h" in labels, (value, labels)
                since = next(x for x in labels if x != "sleeve 24h")
                assert since.startswith("since ") and since[6:] == ml._iso(start)[11:19], (value, since, ml._iso(start))
                # the worker's own reading over the same rows: the since-text,
                # excluding manual / underdog -- what the preset's rows sum to
                # only once those two whales are dropped
                worker = await le._loss_breaker_sum(conn, ml._utc(start))
                shown = sum(float(r["pnl"]) for r in rows if r["window"] != "sleeve 24h")
                kept = sum(float(r["pnl"]) for r in rows
                           if r["window"] != "sleeve 24h" and r["whale"] not in ("manual", "underdog"))
                assert worker == pytest.approx(kept) and shown != pytest.approx(kept), (value, worker, shown)
                assert all(r["status"] in ("settled", "cashed_out") for r in rows)
            # the second statement runs and reads only the last 24 h, newest first
            tail = await conn.fetch(sql.split(";")[1])
            assert [float(r["pnl"]) for r in tail] == [-800.0, -900.0, -50.0, -100.0, -5000.0]
            assert not any(k in sql.upper() for k in ("DELETE ", "UPDATE ", "INSERT ", "DROP ", "TRUNCATE "))
        finally:
            await _drop(admin, conn, name)
    asyncio.run(_go())


@pytest.mark.parametrize("value, why", [
    ('{"at": "%s"}' % ml._iso(time.time() - 1800)[:19], "a naive at: the worker reads it as malformed, the full window"),
    ('{"at": "%s"}' % ml._iso(time.time() + 3600), "a future at: the worker reads it as malformed, the full window"),
])
def test_review_the_preset_reads_a_naive_or_future_at_as_the_worker_does(value, why):
    """EXPECTED TO FAIL on the L2 tree (review finding): the preset's
    `(value->>'at')::timestamptz` accepts a naive `at` in the session's
    zone and a future one as-is, so its `since` window is NARROWER than
    the worker's (empty, for the future one: the label vanishes) while the
    worker refuses on the full window. The operator reading the preset
    would see a sum the worker never compared."""
    from tests.test_mirror_loss_sum_real_pg import _drop

    async def _go():
        admin, conn, name = await _pg_world()
        try:
            await _with_key(conn, value)
            rows = await _preset_rows(conn, _preset_sql())
            labels = {r["window"] for r in rows}
            assert len(labels) == 2, (why, labels)
            since = next(x for x in labels if x != "sleeve 24h")
            assert since[6:] == ml._iso(time.time() - H24)[11:19], (why, since)
        finally:
            await _drop(admin, conn, name)
    asyncio.run(_go())


# ---------------------------------------------------------- 8. the error line

def test_review_the_error_line_names_every_case_label():
    """EXPECTED TO FAIL on the L2 tree (review finding): the regenerated
    `arg must be one of` line was built from a regex wanting exactly one
    space after `label)`, so `tables)   SQL=`, `sizes)    SQL=`,
    `indexes)  SQL=`, `analyze)  need_confirm;` and `mirror-on)  need_confirm;`
    -- five valid presets, one of them the switch that turns the mirror
    ON -- dropped out of the operator's help text, and the builder's pin
    (`line == labels`) locks the loss in."""
    wf = yaml.safe_load(RENDER_OPS.read_text())
    runs = [str(s.get("run") or "") for job in wf["jobs"].values() for s in (job.get("steps") or [])]
    run = next(r for r in runs if "loss-breaker)" in r)
    cases = re.findall(r"^\s+([a-z0-9-]+)\)\s+(?:need_confirm; )?SQL=", run, flags=re.M)
    line = re.search(r'\*\) echo "sql: arg must be one of ([^ ]+) \(got', run).group(1).split("|")
    assert "mirror-on" in cases and "tables" in cases
    assert sorted(cases) == sorted(line), (sorted(set(cases) - set(line)), sorted(set(line) - set(cases)))
