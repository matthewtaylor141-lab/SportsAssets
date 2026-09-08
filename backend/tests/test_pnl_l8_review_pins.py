"""PNL lane 8 (E19) -- the adversarial review's pins (2026-09-08).

THE FINDING PINS WERE WRITTEN AS THE DEFECTS STOOD and FOLDED the same
day (2026-09-08): each now asserts the corrected figure the review asked
for. The other pins hold what the review verified (the preset on the
real migrations, the arm's reach).

CRITICAL-1 (workers/mirror_live.py _tick_book, the E19 arm at the
increase gate; `net_sized = net if flow is None else flow`): admission
sizes the candidate on rules.smaller_reading, but the OPENING tick --
_tick_candidate hands the row to _tick_book in the same tick -- sized the
target on `flow` = HIS FILLS' net less the block (mi.flow_net), and the
arm lifts `drift` for it. When the fills were the LARGER reading the book
opened and PLACED ratio x the fills' net: fills 30,000 against the venue's
25,104.1 (drift 0.163, both long) was admitted at 2,510 and placed 3,000;
the short face (fills -300, the venue -100) placed 300. FOLDED: right
before mirror_target the opening tick's `net_sized` is clamped to
rules.smaller_reading(net_sized, the flag's `net`) -- 0.0 when the signs
part -- so the open never sizes past the reading admission judged; the
$2,500 game cap stands on top of it.
HIGH-1 (the arm sits in _tick_book, lane 34's function): ACCEPTED by the
orchestrator -- lane 34 lands before lane 8 and the arm and the clamp are
confined to the opening tick by the in-memory flag; the reach pin holds.
MEDIUM-1 / LOW-1 / LOW-2 (workers/mirror_shadow.py his_fills): the repeat
arm collapses ONE per-match row per net-leg row (a ranked pairing); the
split arm measures the splits against ONE source's net-leg rows; both
arms judge price as the same cent by round(., 2) on both sides or within
half a cent in numeric to the mil (float8 read 0.615 - 0.61 over 0.005).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re

import pytest

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_d1_fills_dedup import DSN_BASE, D1_CID, K, _drop, _insert, _scratch
from tests.test_e19_smaller_reading import FILLS_NET, MKT_LONG, MKT_OTHER, VENUE_NET, _martinez, _one_book
from tests.test_e5_frozen_exits import REPO, YML
from tests.test_mirror_live_worker import _armed  # noqa: F401 -- the fixture
from tests.test_mirror_live_worker import SHORT, _Venue, _census, _mkt, _places, _short_world, _shorts_on, _tick


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------ CRITICAL-1, folded

def test_l8_review_CRITICAL_1_the_opening_tick_sizes_at_or_under_the_admitted_reading(monkeypatch):
    """His fills 30,000 (all flow) against the venue's per-market 25,104.1:
    smaller_reading = 25,104.1, admission's target 2,510 -- and the opening
    tick planned 3,000 (10 % of `flow` = the fills' net) and PLACED it.
    Folded: the opening tick's sizing axis is clamped to the smaller
    reading before mirror_target -- 2,510 planned and placed, never 3,000;
    the plan still shows both readings (flow_net 30,000, net 25,104.1)."""
    p, v, http = _martinez(monkeypatch, net=30000.0)
    st = _tick(p, v, http=http)
    b = _one_book(p)
    plan = b["last_plan"]
    sm = plan["drift_sized_smaller"]
    assert rules.smaller_reading(30000.0, VENUE_NET) == VENUE_NET
    assert sm["net"] == pytest.approx(VENUE_NET) and sm["fills_net"] == pytest.approx(30000.0)
    assert plan["net"] == pytest.approx(VENUE_NET), "the two-source reading IS the smaller"
    assert plan["flow_net"] == pytest.approx(30000.0), "the fills' axis is still read and recorded"
    assert _census(st, "drift_smaller_open") == 1 and _census(st, "drift") == 0
    # THE FOLD: 2,510 planned and placed, the figure admission judged
    assert b["target"] == 2510 == int(0.10 * VENUE_NET)
    pl = _places(v)
    assert pl and max(x[3] for x in pl) == 2510, pl
    # the clamp sits in _tick_book before the target, on the flag alone
    src = inspect.getsource(ml._tick_book)
    assert src.index('sm = book.get("_drift_smaller")') < src.index('tg = rules.mirror_target(book.get("ratio"), net_sized')
    assert 'clamped = rules.smaller_reading(net_sized, sm.get("net"))' in src
    assert "net_sized = 0.0 if clamped is None else clamped" in src


def test_l8_review_CRITICAL_1_the_reading_bounds_the_open_before_the_game_cap(monkeypatch):
    """Fills 60,000 against the venue's 25,104.1 (drift 0.58): the opening
    tick once sized 6,000 on the fills' axis and the $2,500 game cap cut
    it to 4,132 at the 0.605 mark -- 1,622 over the 2,510 admission sized.
    Folded: the reading bounds it first (2,510; $1,518 at the mark, under
    the cap), and the cap still stands on top."""
    p, v, http = _martinez(monkeypatch, net=60000.0)
    _tick(p, v, http=http)
    b = _one_book(p)
    assert b["last_plan"]["drift_sized_smaller"]["net"] == pytest.approx(VENUE_NET)
    assert b["target"] == 2510 < int(2500.0 / 0.605) == 4132
    assert 2510 * 0.605 <= 2500.0
    assert max(x[3] for x in _places(v)) == 2510


def test_l8_review_CRITICAL_1_the_short_face_opens_the_smaller_short(monkeypatch):
    """His fills -300 (100 long / 400 other) against the venue's -100
    (100 / 200): smaller_reading -100, and the book once opened SHORT at
    -300 -- the LARGER short -- placing 300 BUY_SHORT. Folded: -100, and
    100 BUY_SHORT placed."""
    _shorts_on(monkeypatch)
    p = _short_world()
    v = _Venue()
    st = _tick(p, v, http=_mkt(100.0, 200.0))
    b = _one_book(p)
    sm = b["last_plan"]["drift_sized_smaller"]
    assert sm["net"] == -100.0 and sm["fills_net"] == -300.0 and sm["venue_net"] == -100.0
    assert _census(st, "drift_smaller_open") == 1
    assert b["intent"] == SHORT and b["target"] == -100           # the reading admission judged
    pl = _places(v)
    assert len(pl) == 1 and pl[0][3] == 100 and pl[0][6] == SHORT


# --------------------------------------------------- HIGH-1: the arm's reach

def test_l8_review_HIGH_1_the_arm_lives_in_tick_book_and_only_the_candidates_flag_reaches_it():
    """The bar: the existing-book path changed at all. The arm is in
    _tick_book (lane 34's function); its one key is set in exactly one
    place, _tick_candidate, on the in-memory dict of the tick that opened
    the book, and _write_plan pops it. Nothing read from a row carries it."""
    live = inspect.getsource(ml)
    assert live.count('book["_drift_smaller"] = {') == 1
    assert 'book["_drift_smaller"] = {' in inspect.getsource(ml._tick_candidate)
    assert 'book.get("_drift_smaller") is not None' in inspect.getsource(ml._tick_book)
    assert 'book.pop("_drift_smaller", None)' in inspect.getsource(ml._write_plan)
    assert "_drift_smaller" not in inspect.getsource(ml._tick)          # the walk hands row dicts only
    assert "_drift_smaller" not in ml._sql_book_read.__doc__ if ml._sql_book_read.__doc__ else True


# ------------------------------------------ MEDIUM-1 / LOW-1: the collapse

def test_l8_review_MEDIUM_1_two_identical_real_fills_under_one_tx_keep_the_second():
    """He sweeps two makers of exactly 5,000 @0.61 in one tx; the s1 lane
    carries one record, the poll both. The repeat arm once collapsed EVERY
    per-match row that repeats a net-leg row (his net read 5,000 for
    10,000). Folded: one per-match row per net-leg row -- the pairs are
    ranked by id on both sides and hold where the ranks agree -- so the
    second poll row counts: 10,000, one row / 5,000 sh dropped. Beside it
    two s1 records and three poll rows of one size: two collapse, 15,000."""
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, [("s1", "0xtwin", K, "BUY", 5000.0, 0.61, 1),
                              ("poll", "0xtwin", K, "BUY", 5000.0, 0.61, 1),
                              ("poll", "0xtwin", K, "BUY", 5000.0, 0.61, 1)])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            assert mi.net_positions(fills)[K] == 10000.0
            assert ms.his_fills_dedup() == {"dup_rows": 1, "dup_shares": 5000.0}
            await _insert(c, [("s1", "0xtrip", K, "BUY", 5000.0, 0.61, 2), ("s1", "0xtrip", K, "BUY", 5000.0, 0.61, 2),
                              ("poll", "0xtrip", K, "BUY", 5000.0, 0.61, 2), ("poll", "0xtrip", K, "BUY", 5000.0, 0.61, 2),
                              ("poll", "0xtrip", K, "BUY", 5000.0, 0.61, 2)])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            assert mi.net_positions(fills)[K] == 10000.0 + 15000.0
            assert ms.his_fills_dedup() == {"dup_rows": 3, "dup_shares": 15000.0}
        finally:
            await _drop(admin, c, name)
    _run(run())


def test_l8_review_LOW_1_the_chain_s1_collision_keeps_the_splits_collapsed():
    """D1's admitted over-read (test_d1_review: chain and s1 on one key
    both kept) read 30,328 for Kostyuk's 15,164; under E19 as built the
    poll splits no longer summed to the DOUBLED leg (30,328) so they
    counted as well: 45,492. Folded: the splits are measured against ONE
    source's net-leg rows (chain's 15,164, or s1's) and collapse -- 30,328,
    D1's admitted collision alone, the three splits dropped."""
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, [("chain", "0xc", K, "BUY", 15164.0, 0.563, 1), ("s1", "0xc", K, "BUY", 15164.0, 0.563, 1),
                              ("poll", "0xc", K, "BUY", 4996.0, 0.560, 1), ("poll", "0xc", K, "BUY", 5172.0, 0.560, 1),
                              ("poll", "0xc", K, "BUY", 4996.0, 0.570, 1)])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            assert mi.net_positions(fills)[K] == 30328.0
            assert ms.his_fills_dedup() == {"dup_rows": 3, "dup_shares": 15164.0}
        finally:
            await _drop(admin, c, name)
    _run(run())


def test_l8_review_LOW_2_the_kostyuk_identical_pair_and_the_half_cent_pair_both_collapse():
    """The bar's Kostyuk shape (identical price and size from two sources,
    chain + poll) collapses; a poll price a half cent off its s1 record
    (0.615 against 0.61) once read as two fills -- 0.615 - 0.61 in float8
    is a hair over 0.005. Folded: both arms judge price as the same cent
    by round(., 2) on both sides or within half a cent in numeric to the
    mil, so the pair is one fill (the venue quotes cents; a full cent off
    stays two fills -- E19's 0.60 / 0.59 pin)."""
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, [("chain", "0xk", K, "BUY", 15164.0, 0.563, 1), ("poll", "0xk", K, "BUY", 15164.0, 0.563, 1),
                              ("s1", "0xh", K, "BUY", 5000.0, 0.61, 5), ("poll", "0xh", K, "BUY", 5000.0, 0.615, 5)])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            assert mi.net_positions(fills)[K] == 15164.0 + 5000.0
            assert ms.his_fills_dedup() == {"dup_rows": 2, "dup_shares": 20164.0}
        finally:
            await _drop(admin, c, name)
    _run(run())


# ------------------------------------ the preset on the REAL migrations

def _preset_sql():
    text = YML.read_text()
    body = text[text.index("fills-vs-venue) SQL="):text.index("books-new) SQL=")]
    sql = body.split('SQL="', 1)[1].split('"; TO=', 1)[0]
    depth, cur, stmts = 0, "", []
    for ch in sql:
        cur += ch
        depth += (ch == "(") - (ch == ")")
        if ch == ";" and depth == 0:
            stmts.append(cur.strip())
            cur = ""
    return stmts


def test_l8_review_the_fills_vs_venue_preset_runs_on_the_real_migrations():
    """migrations/*.sql applied in order to a scratch database (031 and 055
    alone need the code-created us_premap table and are skipped by name),
    Martinez's rows and 534's plan seeded, both statements executed: the
    columns the notes promise come back. Skips visibly without Postgres."""
    asyncpg = pytest.importorskip("asyncpg")

    async def run():
        try:
            admin = await asyncpg.connect(DSN_BASE, timeout=4)
        except Exception:  # noqa: BLE001
            pytest.skip("no local postgres for the preset-on-real-schema pin")
        import uuid
        name = "l8rev_" + uuid.uuid4().hex[:10]
        await admin.execute(f'CREATE DATABASE "{name}"')
        c = await asyncpg.connect(DSN_BASE.rsplit("/", 1)[0] + "/" + name, timeout=4)
        try:
            skipped = []
            for path in sorted((REPO / "backend" / "migrations").glob("*.sql")):
                try:
                    await c.execute(path.read_text())
                except Exception as exc:  # noqa: BLE001
                    assert path.name in ("031_us_premap_signed.sql", "055_us_premap_team.sql"), (path.name, exc)
                    skipped.append(path.name)
            await c.execute("INSERT INTO whales (id, address, username) VALUES (999, '0xrn1rev', 'RN1')")
            cid, la, oa = "0xmartinez-review", "tok-m", "tok-o"
            rows = [("chain", "0xm1124a", la, 230.4, 0.51, 0), ("chain", "0xo1125", oa, 3950.9, 0.31, 60),
                    ("s1", "0xm120840", la, 5245.0, 0.59, 2680), ("s1", "0xm120924", la, 5225.0, 0.62, 2724),
                    ("poll", "0xm120924", la, 3601.0, 0.61, 2724), ("poll", "0xm120924", la, 5245.0, 0.61, 2724),
                    ("s1", "0xm120945", la, 5225.0, 0.61, 2745), ("poll", "0xm120945", la, 4283.5, 0.60, 2745)]
            for i, (src, tx, asset, size, price, dt) in enumerate(rows):
                await c.execute(
                    "INSERT INTO trades (whale_id, tx_hash, asset, condition_id, side, size, price, notional, ts, "
                    "source, detected_at, dedupe_key) VALUES (999, $1, $2, $3, 'BUY', $4, $5, $6, "
                    "now() - interval '3 hours' + make_interval(secs => $7), $8, now(), $9)",
                    tx, asset, cid, size, price, round(size * price, 6), float(dt), src, f"rev{i}")
            plan = {"mkt_long": MKT_LONG, "mkt_other": MKT_OTHER, "snap_net": 25104.1, "flow_net": FILLS_NET,
                    "drift": 0.523, "drift_src": "market", "at": 1_788_000_000.0}
            await c.execute("INSERT INTO mirror_books (whale, condition_id, us_market_slug, long_asset, other_asset, "
                            "state, last_plan) VALUES ('rn1', $1, 'aec-atp-pedmar-frafor-2026-09-08', $2, $3, "
                            "'closed', $4::jsonb)", cid, la, oa, json.dumps(plan))
            stmts = _preset_sql()
            assert len(stmts) == 2
            per = [dict(r) for r in await c.fetch(stmts[0])]
            tot = [dict(r) for r in await c.fetch(stmts[1])]
            assert len(per) == 1 and per[0]["slug"] == "aec-atp-pedmar-frafor-2026-09-08"
            for col in ("chain_rows", "s1_sh", "poll_sh", "old_net", "new_net", "old_dropped_sh", "new_dropped_sh",
                        "kept_usd", "later_sh", "mkt_long", "snap_net", "flow_net", "drift", "drift_src", "gap_old", "gap_new"):
                assert col in per[0], col
            assert float(per[0]["old_dropped_sh"]) == 13129.5 and float(per[0]["new_dropped_sh"]) == 0.0
            assert len(tot) == 1 and int(tot[0]["markets"]) == 1 and float(tot[0]["raw_sh"]) == 33005.8
            assert skipped == ["031_us_premap_signed.sql", "055_us_premap_team.sql"]
        finally:
            await c.close()
            await admin.execute(f'DROP DATABASE "{name}"')
            await admin.close()
    _run(run())


def test_l8_review_the_help_line_is_in_case_order_around_the_preset():
    text = YML.read_text()
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    names = line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    assert names == labels and names[names.index("fills-vs-venue") - 1] == "drift-16"
    assert names[names.index("fills-vs-venue") + 1] == "books-new"


# ------------------------------------------- the surviving mutants' kills

def test_l8_review_kill_M15_the_refusal_row_carries_the_smaller_reading_with_shorts_off():
    """Mutant M15 (`net = smaller` dropped) survived the builder's pins:
    with shorts ON `_net_for` already sizes sign x min(|a|, |b|), so the
    E19 line is redundant there. With shorts OFF `_net_for` reads
    min(-300, -800) = -800 (the LARGER short) and E19's line puts the
    smaller, -300, on the refusal row -- the one observable difference.
    His fills -300 against the venue's -800, the knob off: refused
    `short_side_refused` with his_net -300 on the row, never -800."""
    p = _short_world()
    st = _tick(p, _Venue(), http=_mkt(100.0, 900.0))
    assert not p.books and _census(st, "short_side_refused") >= 1
    assert [r["refusal"] for r in p.cand_refusals] == ["short_side_refused"]
    assert p.cand_refusals[0]["his_net"] == -300.0
    assert rules.smaller_reading(-300.0, -800.0) == -300.0


def test_l8_review_kill_M06_M14_M20_the_candidates_guards_are_the_text_the_notes_name():
    """Mutants M06 (sized on drift_src 'book' too), M14 (the fact True
    whatever `smaller`), M20 (sized on `snapshot_stale` too) survived:
    each guard is belt-and-braces over admission's own checks (the fresh
    read, `snapshot_stale` before `drift`) and `_net_for`'s own sizing, so
    no behaviour parts. Pinned as text so a loosening is seen."""
    src = inspect.getsource(ml._tick_candidate)
    assert 'if drift.refusal == "drift" and _drift_src == "market":' in src
    assert "drift_sized_smaller=smaller is not None)" in src
    assert src.count("smaller_reading(") == 1
