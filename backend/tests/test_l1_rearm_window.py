"""L1 (2026-09-07): the loss-stop re-arm restarts the window.

Owner 16:51Z: "Let's rearm completely and let's get everything running
with all the upgrades we just did!" The operator re-arm (render-ops
`mirror-rearm`, confirm=DO) deleted 'mirror_loss_stop' and the worker
placed for 16 minutes; at 17:07:45Z it re-tripped itself --
{"sum": -5023.9545, "books": 214, "limit": 5000.0} -- on the morning's
losses (tripped 09:46:36Z at -5,002.58), which `_SQL_LOSS_SUM`'s
trailing 24 h kept until the next morning: every re-arm re-tripped
within a tick or two of any new loss. A re-arm that cannot hold is not
a re-arm.

The rule: the loss sum counts only what happened AFTER the newest
re-arm. The preset writes 'mirror_loss_rearm' = {"at": now(), "by":
"render-ops", "prior": the deleted stop or null} in the statement that
deletes the stop; the worker reads it beside the stop's key and hands
the statement its ONE parameter, the window's start = GREATEST(now -
24 h, at). A re-arm older than 24 h changes nothing; a malformed key
reads as absent (the FULL window) and logs once; an unreadable one is a
stop; the receipt carries `since` and `rearmed_at`; the mode line prints
`loss=<sum>/<limit> since <HH:MM>`. The limit does not move, a standing
stop key holds whatever the re-arm key says, and reduces still pass
under it. Driven through tests/test_mirror_live_worker's fakes (the
fake reads the window from the parameter, never from its own clock);
tests/test_mirror_loss_sum_real_pg executes the statement and the
preset against Postgres.
"""

from __future__ import annotations

import inspect
import logging
import pathlib
import re
from datetime import datetime, timezone

import pytest
import yaml

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_mirror_live_worker import M, N, NOW, SLUG, _armed  # noqa: F401 — the fixture
from tests.test_mirror_live_worker import (_OTHER, _ZZ, _census, _his, _places, _pool, _settled_book,
                                           _tick, _Venue)

RENDER_OPS = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "render-ops.yml"
H24 = ml.LOSS_WINDOW_S
# the receipt the worker wrote at 17:07:45Z, verbatim: the `prior` a
# re-arm carries, and the standing stop the holding pins seed
TRIPPED = {"at": "2026-09-07T17:07:45Z", "sum": -5023.9545, "books": 214, "limit": 5000.0}
REARM_AT = NOW - 1800                     # the re-arm, half an hour before the tick


def _rearm(at=REARM_AT, prior=TRIPPED):
    return {"at": ml._iso(at), "by": "render-ops", "prior": prior}


def _morning_and_after(p):
    """The 17:07:45Z shape on the fixture clock: the morning's losses
    settled an hour before the tick (one book carrying the tripped sum),
    and a book that lost $100 ten minutes ago -- after the re-arm."""
    _settled_book(p, -1200.0, TRIPPED["sum"], closed_ago=3600, **_ZZ)
    p.add_book(ledger=0, state="closed", realized_pnl=-100.0, settled_pnl=None,
               closed_at=NOW - 600, updated_ts=NOW - 600, opened_ts=NOW - 900, **_OTHER)


def _reduce_world():
    """The every-guard pin's world: a book whose target (100) is under
    its ledger (300) -- a reduce -- and a resting BUY on another book."""
    p = _pool(fills=_his(300, sold=200), snap={M: 100.0, N: 0.0})
    p.add_book(ledger=300)
    other = p.add_book(ledger=0, us_market_slug="aec-atp-other-2026-09-02", condition_id="0xother")
    p.add_order(other)
    v = _Venue(held={SLUG: 300})
    v.rest("oid-1")
    return p, v


def _loss_reads(p):
    return [x for x in p.sent if "ml-loss-sum" in x[1]]


# ---------------------------------------------------- 1. the window start

def test_l1_the_window_starts_24_h_back_with_no_re_arm():
    assert H24 == 24 * 3600
    assert ml._loss_window_start(NOW, None) == NOW - H24
    assert ml._rearm_at(None, None) is None


def test_l1_the_window_starts_at_a_re_arm_inside_24_h():
    assert ml._loss_window_start(NOW, REARM_AT) == REARM_AT
    assert ml._loss_window_start(NOW, NOW - 1) == NOW - 1
    # the key as the preset writes it (Postgres's to_jsonb(now()):
    # microseconds and an offset) and as the worker writes its own ISO
    # (seconds, Z); an offset other than UTC is the same instant
    pg = "2026-09-07T16:51:23.123456+00:00"
    assert ml._rearm_at({"at": pg, "by": "render-ops", "prior": None}, None) == pytest.approx(
        datetime(2026, 9, 7, 16, 51, 23, 123456, tzinfo=timezone.utc).timestamp())
    assert ml._rearm_at(_rearm(), None) == pytest.approx(REARM_AT, abs=1.0)
    assert ml._rearm_at({"at": "2026-09-07T18:51:23+02:00"}, None) == \
        ml._rearm_at({"at": "2026-09-07T16:51:23Z"}, None)


def test_l1_a_re_arm_older_than_24_h_changes_nothing():
    assert ml._loss_window_start(NOW, NOW - H24 - 1) == NOW - H24
    assert ml._loss_window_start(NOW, NOW - 90000) == NOW - H24
    assert ml._loss_window_start(NOW, NOW - H24) == NOW - H24       # the edge itself


# ------------------------------------------------ 2. the statement's text

def test_l1_the_statement_reads_its_window_from_the_one_parameter():
    """$1::timestamptz in all three places -- the settled arm's clock,
    the realized arm's, the count's -- and no interval or now() of the
    text's own: the start lives in the Python that computes it."""
    sql = " ".join(ml._SQL_LOSS_SUM.split())
    assert sql.count("> $1::timestamptz") == 3 and sql.count("$1") == 3
    assert "interval" not in sql and "now()" not in sql and "ml-loss-sum" in sql
    assert "COALESCE(closed_at, updated_at) > $1::timestamptz" in sql
    assert "WHERE updated_at > $1::timestamptz AND NOT (state = 'closed' AND settled_pnl IS NOT NULL)" in sql
    assert "(SELECT count(*) FROM mirror_books WHERE updated_at > $1::timestamptz) AS books" in sql
    assert ml._utc(NOW).tzinfo is not None and ml._utc(NOW).timestamp() == pytest.approx(NOW)


def test_l1_the_fake_reads_the_window_from_the_parameter_and_refuses_its_own():
    """The E3 rows: the full window reads -330.95 / 6 as E3 pinned; a
    start 45 min back counts the cashed-out close (30 min) and the open
    book (now) and nothing settled two hours ago; a statement with an
    interval of its own, or one handed no start, is refused."""
    tonight = [(16, -244.75, -315.40), (3, 2.48, 156.17), (19, -2.11, -41.46), (22, 14.64, 19.74)]
    p = _pool()
    for bid, realized, settled in tonight:
        _settled_book(p, realized, settled, closed_ago=7200, us_market_slug=f"aec-set-{bid}-2026-09-06",
                      condition_id=f"0xset{bid}", long_asset=f"tok-s{bid}", other_asset=f"tok-t{bid}")
    p.add_book(ledger=300, realized_pnl=-100.0)
    p.add_book(ledger=0, state="closed", realized_pnl=-50.0, settled_pnl=None,
               closed_at=NOW - 1800, updated_ts=NOW - 1800, **_OTHER)
    _settled_book(p, -999.0, -1500.0, closed_ago=25 * 3600, **_ZZ)
    full = p._run("fetchrow", ml._SQL_LOSS_SUM, (ml._utc(NOW - H24),))
    assert (full["lost"], full["books"]) == (pytest.approx(-330.95), 6)
    part = p._run("fetchrow", ml._SQL_LOSS_SUM, (ml._utc(NOW - 45 * 60),))
    assert (part["lost"], part["books"]) == (pytest.approx(-150.0), 2)
    own = ml._SQL_LOSS_SUM.replace("updated_at > $1::timestamptz) AS books",
                                   "updated_at > now() - interval '24 hours') AS books")
    with pytest.raises(AssertionError, match="one window, the parameter"):
        p._run("fetchrow", own, (ml._utc(NOW - H24),))
    with pytest.raises(AssertionError, match="the window start"):
        p._run("fetchrow", ml._SQL_LOSS_SUM, ())


# --------------------------------------------- 3. the tick, the receipt

def test_l1_the_17_07_shape_a_re_arm_holds_and_the_tick_counts_only_what_came_after():
    """The tripped stop of 17:07:45Z (sum -5023.95) deleted, a re-arm
    half an hour before the tick, a book that lost $100 after it: the
    sum is -100, not -5,123.95 -- the tick does not trip, the candidate
    opens, and the start the statement was handed IS the re-arm's
    instant. The same rows with no re-arm key read -5,123.95 and trip:
    the key is what holds."""
    stop = float(rules.MIRROR_LOSS_STOP_USD)
    assert stop == 5000.0, "the limit does not change (capped_env 5000; env may only lower)"
    p = _pool()
    _morning_and_after(p)
    p.state["mirror_loss_rearm"] = _rearm()
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "mirror_loss_stop") == 0 and "mirror_loss_stop" not in p.state
    assert [c[1] for c in _places(v)] == [SLUG], "under the stop: the candidate opens"
    assert ml._last_loss == {"sum": -100.0, "books": 1, "limit": stop,
                             "since": ml._iso(REARM_AT), "rearmed_at": ml._iso(REARM_AT)}
    reads = _loss_reads(p)
    assert len(reads) == 1 and reads[0][2][0].timestamp() == pytest.approx(REARM_AT, abs=1.0)
    p2 = _pool()
    _morning_and_after(p2)
    v2 = _Venue()
    st2 = _tick(p2, v2)
    assert _census(st2, "mirror_loss_stop") >= 1 and not _places(v2)
    r = p2.state["mirror_loss_stop"]
    assert r["sum"] == pytest.approx(TRIPPED["sum"] - 100.0) and r["sum"] == pytest.approx(-5123.9545)
    assert r["books"] == 2 and r["rearmed_at"] is None and r["since"] == ml._iso(NOW - H24)


def test_l1_a_trip_receipt_carries_since_and_rearmed_at():
    """A loss after the re-arm past the limit trips: the receipt is the
    old four fields plus `since` (the window's start: the re-arm) and
    `rearmed_at`; with no key, `since` is the 24 h edge and `rearmed_at`
    null. The census name and the exits-only tick are as they were."""
    stop = float(rules.MIRROR_LOSS_STOP_USD)
    p = _pool()
    _settled_book(p, -0.4 * stop, -1.1 * stop, closed_ago=600, **_ZZ)
    p.state["mirror_loss_rearm"] = _rearm()
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "mirror_loss_stop") >= 1 and not _places(v)
    r = p.state["mirror_loss_stop"]
    assert list(r) == ["at", "sum", "books", "limit", "since", "rearmed_at"]
    assert r == {"at": ml._iso(NOW), "sum": pytest.approx(-1.1 * stop), "books": 1, "limit": stop,
                 "since": ml._iso(REARM_AT), "rearmed_at": ml._iso(REARM_AT)}
    assert ml._last_loss == {k: val for k, val in r.items() if k != "at"}
    p2 = _pool()
    _settled_book(p2, -0.4 * stop, -1.1 * stop, closed_ago=600, **_ZZ)
    _tick(p2, _Venue())
    r2 = p2.state["mirror_loss_stop"]
    assert r2["since"] == ml._iso(NOW - H24) and r2["rearmed_at"] is None
    assert r2["sum"] == pytest.approx(-1.1 * stop) and r2["limit"] == stop


@pytest.mark.parametrize("value", [
    {"by": "render-ops", "prior": None},                        # no `at`
    {"at": "yesterday", "by": "render-ops"},                    # not an instant
    {"at": "2026-09-07T17:30:00", "by": "render-ops"},          # naive: no offset
    {"at": 1788802200, "by": "render-ops"},                     # not a string
    ["2026-09-07T17:30:00Z"],                                   # not an object
    True,
    "{not json",                                                # unparseable JSON
])
def test_l1_a_malformed_re_arm_key_reads_as_absent_and_logs_once(value, caplog):
    """Fail closed toward the FULL window: with the morning's losses an
    hour old and a malformed key, the stop trips on -5,123.95 exactly as
    with no key (never a shorter window), one warning across two ticks
    that read it; and with nothing old to trip on the candidate opens --
    malformed is absent, not a stop."""
    p = _pool()
    _morning_and_after(p)
    p.state["mirror_loss_rearm"] = value
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, _Venue())
        assert _census(st, "mirror_loss_stop") >= 1
        r = p.state.pop("mirror_loss_stop")
        assert r["sum"] == pytest.approx(-5123.9545) and r["rearmed_at"] is None
        assert r["since"] == ml._iso(NOW - H24)
        st2 = _tick(p, _Venue(), now=NOW + 30)
        assert _census(st2, "mirror_loss_stop") >= 1 and p.state["mirror_loss_stop"]["rearmed_at"] is None
        assert len(_loss_reads(p)) == 2
    warned = [x for x in caplog.records if "mirror_loss_rearm malformed" in x.getMessage()]
    assert len(warned) == 1 and warned[0].levelno == logging.WARNING, [x.getMessage() for x in caplog.records]
    assert "the full 24 h window stands" in warned[0].getMessage()
    p3 = _pool()
    p3.state["mirror_loss_rearm"] = value
    v3 = _Venue()
    st3 = _tick(p3, v3)
    assert _census(st3, "mirror_loss_stop") == 0 and [c[1] for c in _places(v3)] == [SLUG]
    assert ml._last_loss["rearmed_at"] is None and ml._last_loss["since"] == ml._iso(NOW - H24)


def test_l1_an_unreadable_re_arm_key_is_a_stop_and_a_reduce_still_passes(monkeypatch):
    """A raised read of the re-arm key stops exactly as the stop key's
    does: refused by name, no sum read, no receipt written, the BUY rest
    cancelled -- and the reduce goes out (the exit carve-out)."""
    orig = ml._state

    async def _st(pool, key):
        if key == "mirror_loss_rearm":
            return None, "ConnectionDoesNotExistError"
        return await orig(pool, key)
    monkeypatch.setattr(ml, "_state", _st)
    p, v = _reduce_world()
    st = _tick(p, v)
    assert _census(st, "mirror_loss_stop") >= 1
    assert "mirror_loss_stop" not in p.state and ml._last_loss is None
    assert not _loss_reads(p), "no sum was read"
    assert ("cancel", "oid-1", SLUG) in v.calls
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True, pl


def test_l1_a_standing_stop_holds_against_a_newer_re_arm_key_and_a_reduce_passes():
    """Only the preset's DELETE clears a stop: with the 17:07:45Z stop
    standing and a re-arm key NEWER than it, the tick is exits-only by
    the stop's name, reads no sum, rewrites no receipt; the reduce goes
    out. (The every-guard pin in test_mirror_live_worker re-runs the
    same carve-out.)"""
    p, v = _reduce_world()
    p.state["mirror_loss_stop"] = dict(TRIPPED)
    p.state["mirror_loss_rearm"] = _rearm(at=NOW - 10, prior=None)
    st = _tick(p, v)
    assert _census(st, "mirror_loss_stop") >= 1
    assert p.state["mirror_loss_stop"] == TRIPPED and ml._last_loss is None
    assert not _loss_reads(p)
    assert ("cancel", "oid-1", SLUG) in v.calls
    pl = _places(v)
    assert len(pl) == 1 and pl[0][4] is True, pl


# ---------------------------------------------------------- 4. the preset

def _rearm_preset() -> tuple[str, str]:
    wf = yaml.safe_load(RENDER_OPS.read_text())
    runs = [str(s.get("run") or "") for job in wf["jobs"].values() for s in (job.get("steps") or [])]
    src = next(r for r in runs if "mirror-rearm)" in r)
    m = re.search(r'mirror-rearm\) need_confirm; SQL="(.*?)"; TO=30000 ;;', src)
    assert m, "the mirror-rearm preset is not where render-ops.yml kept it"
    return src, m.group(1)


def test_l1_the_preset_writes_the_rearm_key_and_the_prior_stop_in_the_statement_that_deletes_it():
    src, sql = _rearm_preset()
    stmts = [s.strip() for s in sql.split(";") if s.strip()]
    assert len(stmts) == 2, stmts
    first, select = stmts
    assert first == ("WITH gone AS (DELETE FROM ingestion_state WHERE key = 'mirror_loss_stop' RETURNING value) "
                     "INSERT INTO ingestion_state (key, value) VALUES ('mirror_loss_rearm', "
                     "jsonb_build_object('at', now(), 'by', 'render-ops', 'prior', (SELECT value FROM gone))) "
                     "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value")
    assert select == "SELECT key, value FROM ingestion_state WHERE key LIKE 'mirror_%' ORDER BY key"
    # inside the runner's double-quoted bash string: no `$` to expand, no
    # quote to end it, no CTE reaching across the `;`
    assert "$" not in sql and '"' not in sql and "\\" not in sql and "gone" not in select
    # confirm=DO stands and the arg list still names it
    assert "|mirror-rearm|" in src and "mirror-rearm (DO)" in RENDER_OPS.read_text()
    assert ml._STATE_LOSS_REARM == "mirror_loss_rearm" and ml._STATE_LOSS_STOP == "mirror_loss_stop"
    # the shape the key takes, as the worker reads it
    assert ml._rearm_at({"at": "2026-09-07T16:51:23.123456+00:00", "by": "render-ops", "prior": TRIPPED}, None)


# ------------------------------------------------------- 5. the mode line

def _mode_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")]


def test_l1_the_mode_line_prints_the_loss_and_its_window(caplog):
    """`loss=<sum>/<limit> since <HH:MM>` beside the day rail, before
    `venue=` and the trailing dict (the logs action keeps 400 characters
    of a line), the start to the minute -- five characters, whatever the
    string; nothing on a tick that read none (the quiet line is what
    test_mirror_live_day_cap pins); main() hands the line the tick's own
    reading, cleared before every tick, and no stats key was added (the
    served surface's 38 base keys stand)."""
    stats = ml._new_stats()
    stats.update(mode=ml.MODE_ON, whales=["rn1"], books_live=2, orders_open=1)
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS)
    assert "loss=" not in _mode_lines(caplog)[0]
    assert _mode_lines(caplog)[0].startswith(
        "mirror_live mode=on whales=['rn1'] books=2 open=1 day=None venue=None stats={")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS,
                      {"sum": -100.0, "books": 1, "limit": 5000.0,
                       "since": "2026-09-07T17:30:00Z", "rearmed_at": "2026-09-07T17:30:00Z"})
    assert _mode_lines(caplog)[0].startswith(
        "mirror_live mode=on whales=['rn1'] books=2 open=1 day=None loss=-100.0/5000.0 since 17:30 "
        "venue=None stats={")
    caplog.clear()
    stats.update(abandoned=True, abandon_reason="venue_halted")
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS,
                      {"sum": -5023.9545, "limit": 5000.0, "since": "x" * 500})
    assert " day=None loss=-5023.9545/5000.0 since xxxxx venue=None abandon=venue_halted stats={" \
        in _mode_lines(caplog)[0]
    # main() hands over the tick's reading: what the tick read, or None
    assert "_mode_line(stats, ticks, _last_loss)" in inspect.getsource(ml.main)
    p = _pool()
    _tick(p, _Venue())
    assert ml._last_loss == {"sum": 0.0, "books": 0, "limit": float(rules.MIRROR_LOSS_STOP_USD),
                             "since": ml._iso(NOW - H24), "rearmed_at": None}
    p2 = _pool()
    p2.state["mirror_loss_stop"] = dict(TRIPPED)
    _tick(p2, _Venue())
    assert ml._last_loss is None
    assert "loss" not in ml._new_stats() and len(ml._new_stats()) == 38


# ----------------------------------------------- 6. what does not change

def test_l1_the_limit_and_the_refusal_order_are_untouched(monkeypatch):
    assert rules.MIRROR_LOSS_STOP_USD == 5000.0
    monkeypatch.setenv("MIRROR_LOSS_STOP_USD", "9000")
    assert rules.capped_env("MIRROR_LOSS_STOP_USD", 5000.0) == 5000.0, "env may only lower"
    monkeypatch.setenv("MIRROR_LOSS_STOP_USD", "1000")
    assert rules.capped_env("MIRROR_LOSS_STOP_USD", 5000.0) == 1000.0
    # _increases_refusal's order, by its return lines
    assert re.findall(r"return (.+)", inspect.getsource(ml._increases_refusal)) == [
        "t.cancel_all", '"mode_env_off"', "t.mode_db_refusal", "t.increase_block",
        '"mode_env_off"', '"whales_unreadable"', '"mode_db_off"', '"demoted"', "None"]
    # the stop's read sits where it did: after the day cap, the last guard
    src = inspect.getsource(ml._global_guards)
    assert src.index("mirror_day_cap") < src.index("_STATE_LOSS_STOP") < src.index("await _loss_stop(t)")
    assert "_STATE_LOSS_REARM" in inspect.getsource(ml._loss_stop)
