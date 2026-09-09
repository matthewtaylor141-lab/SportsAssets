"""E19 (2026-09-08, PNL lane 8) -- THE CANDIDATE OPENS ON THE SMALLER READING,
THE FILL COLLAPSE KEEPS DISTINCT FILLS, THE fills-vs-venue PRESET.

Owner (2026-09-08): "Fix all 3 of these immediately. I want to know when he
makes money we make money. This needs to be right." and 13:5xZ "Why did we
only have 12c on Martinez. I see RN1 had 25000 on Martinez". Market
aec-atp-pedmar-frafor-2026-09-08 (hard2/book_534_1424.log, candref_1408.log):
book 534 closed on his flip at 12:10:17Z; the reopen at 12:10:43Z read his
fills' net 11,974.6 against the venue's own per-market snapshot 29,054.9
long / 3,950.8 other (net 25,104.1, drift 0.523) -- BOTH LONG -- and was
refused `drift`, then `side_band` (the market ran 20c), then `drift` on
every later window until the market expired at 14:02:55Z. No book
followed. The fills' net read low because the D1 collapse dropped four
real poll rows (18,374.5 sh) that shared an s1 row's tx hash: the venue's
29,054.9 is the SUM of every row.

(a) rules.smaller_reading and AdmissionFacts.drift_sized_smaller: a fresh
per-market read past MIRROR_DRIFT_MAX with two readings of one sign sizes
the candidate on the smaller magnitude; the OPENING tick's increase gate
honours the same admission; the census counts `drift_smaller_open`.
(b) mirror_shadow.his_fills: a per-match row under a net-leg row collapses
only as its split (the per-match rows summing to the net-leg size) or its
repeat (price to the cent, size within max(0.01 sh, 0.1 %)).
    WITHDRAWN FROM SIZING BY E19b (2026-09-08, the same day): the
    fills-vs-venue preset's first production run (17:07Z, ten minutes
    after the deploy) read the new key FARTHER from the venue than D1's
    on the day's books -- old closer on 22 markets, new closer on 4, the
    summed gap 81,470.2 sh old against 119,856.5 sh new; on live books
    with no later fills the old key equals the venue to the decimal and
    the new key over-reads by thousands (611: -966.9 = venue against
    +19,349.5) -- and the live census went drift 0 -> 7 across the deploy.
    THE READER is D1's his_fills again (7a4b852's text byte for byte);
    lane 8's key is kept UNWIRED as mirror_shadow.his_fills_distinct, the
    reference the preset's new_* columns compute. The collapse pins below
    say, each, which function holds which claim.
(c) the fills-vs-venue preset in render-ops.yml.

The collapse pins EXECUTE the SQL on the scratch Postgres the D1 file
builds (skips visibly when none answers)."""

from __future__ import annotations

import asyncio
import inspect
import re

import pytest
import yaml

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_d1_fills_dedup import D1_CID, K, NS, _d1_rows, _drop, _insert, _scratch
from tests.test_e5_frozen_exits import REPO, YML
from tests.test_mirror_live_worker import CID, M, N, NOW, SLUG, _armed  # noqa: F401 -- the fixture
from tests.test_mirror_live_worker import (SHORT, _census, _Http, _mkt, _places, _pool, _rails_2026_09_06,
                                           _short_world, _shorts_on, _tick, _Venue)
from tests.test_mirror_shadow import _fill

# THE MARTINEZ READINGS at 12:10:43Z (hard2/candref_1408.log): his fills'
# net, the venue's per-market long / other in 534's last plan
FILLS_NET = 11974.6
MKT_LONG, MKT_OTHER = 29054.9, 3950.8
VENUE_NET = MKT_LONG - MKT_OTHER


def _run(coro):
    return asyncio.run(coro)


def _martinez(monkeypatch, bid=0.60, ask=0.61, block=0.0, net=FILLS_NET):
    """His fills' net at 0.61 -- all flow (ingested 10 s ago) unless
    `block` moves part of it 2.5 h back (E12's block-and-add shape) --
    against the venue's per-market read 29,054.9 / 3,950.8; the whole-book
    walk reads nothing of him (the per-market read is what opens a book
    at all). The 2026-09-06 rails: 10 % ratio, $10 exact copy."""
    _rails_2026_09_06(monkeypatch)
    fills = [_fill(M, "BUY", net - block, 0.61, NOW - 10)]
    if block:
        fills.insert(0, _fill(M, "BUY", block, 0.61, NOW - 9000))
    p = _pool(fills=fills, snap=None)
    return p, _Venue(bid=bid, ask=ask), _mkt(MKT_LONG, MKT_OTHER)


def _one_book(p):
    assert len(p.books) == 1, "the open"
    return next(iter(p.books.values()))


# ------------------------------------------------------------ (a) the rule

def test_e19_smaller_reading_is_the_signed_smaller_of_one_sign_and_none_else():
    # Martinez: 11,974.6 against 25,104.1, both long -> the fills' 11,974.6
    assert rules.smaller_reading(FILLS_NET, VENUE_NET) == FILLS_NET
    assert rules.smaller_reading(VENUE_NET, FILLS_NET) == FILLS_NET
    # the short mirror image: two shorts, the smaller short
    assert rules.smaller_reading(-300.0, -800.0) == -300.0
    assert rules.smaller_reading(-800.0, -300.0) == -300.0
    # signs that disagree, a zero on either side: no reading to size on
    assert rules.smaller_reading(300.0, -800.0) is None
    assert rules.smaller_reading(-300.0, 800.0) is None
    assert rules.smaller_reading(0.0, 800.0) is None and rules.smaller_reading(300.0, 0.0) is None
    # a reading that is not a number
    for bad in (None, True, "300", float("nan"), float("inf")):
        assert rules.smaller_reading(bad, 800.0) is None and rules.smaller_reading(300.0, bad) is None


def _facts(**over):
    from tests.test_mirror_live_rules import _admitted
    f = _admitted()
    for k, v in over.items():
        setattr(f, k, v)
    return f


def test_e19_admission_admits_past_the_max_only_with_the_flag_beside_a_fresh_market_read():
    d = round(abs(FILLS_NET - VENUE_NET) / VENUE_NET, 6)
    assert d > rules.MIRROR_DRIFT_MAX
    # the landed verdict: drift
    assert rules.admission(_facts(drift=d, snap_market_fresh=True)) == "drift"
    # the flag beside a fresh per-market read admits; the number is untouched
    assert rules.admission(_facts(drift=d, snap_market_fresh=True, drift_sized_smaller=True)) is None
    # the flag without the per-market read (the walk fresh, the market not): drift
    assert rules.admission(_facts(drift=d, snap_fresh=True, snap_market_fresh=None,
                                  drift_sized_smaller=True)) == "drift"
    assert rules.admission(_facts(drift=d, snap_fresh=True, snap_market_fresh=False,
                                  drift_sized_smaller=True)) == "drift"
    # an unreadable or negative drift refuses whatever the flag says
    for bad in (None, -0.1, float("nan"), "0.5", True):
        assert rules.admission(_facts(drift=bad, snap_market_fresh=True, drift_sized_smaller=True)) == "drift"
    # the flag admits only when it IS True (never a truthy stand-in)
    assert rules.admission(_facts(drift=d, snap_market_fresh=True, drift_sized_smaller=1)) == "drift"
    # the default is the fail-closed False, appended LAST
    import dataclasses
    assert rules.AdmissionFacts().drift_sized_smaller is False
    assert dataclasses.fields(rules.AdmissionFacts)[-1].name == "drift_sized_smaller"


# ------------------------------------------------------- (a) the candidate

def test_e19_martinez_shape_a_fresh_read_past_the_max_of_one_sign_opens_on_the_smaller_reading(monkeypatch):
    p, v, http = _martinez(monkeypatch)
    st = _tick(p, v, http=http)
    b = _one_book(p)
    # 10 % of the SMALLER reading (11,974.6 -> 1,197), never of the venue's 25,104
    assert b["target"] == 1197
    plan = b["last_plan"]
    assert plan["snap_market_fresh"] is True and plan["drift_src"] == "market"
    assert plan["drift"] == pytest.approx(0.5228, abs=1e-3), "the real drift number, never a stand-in"
    assert plan["drift_sized_smaller"] == {
        "drift": plan["drift"], "drift_src": "market", "fills_net": pytest.approx(FILLS_NET),
        "venue_net": pytest.approx(VENUE_NET), "net": pytest.approx(FILLS_NET), "sized_from": "smaller"}
    assert _census(st, "drift_smaller_open") == 1 and _census(st, "drift") == 0
    # the OPENING tick placed at his level (E12: every fill is flow): the
    # gate that refused Martinez's reopen does not refuse the open it admitted
    pl = _places(v)
    assert pl and all(x[1] == SLUG for x in pl) and max(x[3] for x in pl) == 1197, pl
    assert all(x[2] <= 0.61 for x in pl), "at his cent, never above it"


def test_e19_signs_that_disagree_open_nothing_and_never_carry_the_name(monkeypatch):
    """His fills long, the venue's per-market read SHORT: smaller_reading
    is None and the two-source reading (_net_for) sizes nothing -- 0 with
    shorts on (`target_zero`), the other side's figure with shorts off
    (refused as a short by the P1 name). Never an open, never the name."""
    for on in (True, False):
        p, v, _ = _martinez(monkeypatch)
        if on:
            _shorts_on(monkeypatch)
        st = _tick(p, v, http=_mkt(0.0, 5000.0))
        assert not p.books and _census(st, "drift_smaller_open") == 0
        assert _census(st, "target_zero") + _census(st, "short_side_refused") >= 1
        assert rules.smaller_reading(FILLS_NET, -5000.0) is None


def test_e19_the_whole_book_walk_never_sizes_the_smaller_reading(monkeypatch):
    """The per-market read unreadable (the venue names neither token), the
    whole-book walk fresh and disagreeing by the same 0.52: drift_src is
    'book' and the candidate is refused `drift` as before."""
    _rails_2026_09_06(monkeypatch)
    p = _pool(fills=[_fill(M, "BUY", FILLS_NET, 0.61, NOW - 10)], snap={M: VENUE_NET, N: 0.0})
    st = _tick(p, _Venue(bid=0.60, ask=0.61), http=_Http(rows=[{"asset": "tok-elsewhere", "size": 900}]))
    assert not p.books and _census(st, "drift") >= 1 and _census(st, "drift_smaller_open") == 0


def test_e19_the_smaller_reading_rounding_to_nothing_is_target_zero_never_an_open(monkeypatch):
    p, v, http = _martinez(monkeypatch, net=0.4)
    st = _tick(p, v, http=http)
    assert not p.books and _census(st, "target_zero") >= 1
    assert _census(st, "drift_smaller_open") == 0 and _census(st, "drift") == 0


def test_e19_two_readings_within_the_max_take_the_landed_path_and_no_new_name(monkeypatch):
    _rails_2026_09_06(monkeypatch)
    p = _pool(snap=None)
    st = _tick(p, _Venue(), http=_mkt(300.0, 0.0))
    b = _one_book(p)
    assert b["last_plan"]["drift"] == 0.0 and "drift_sized_smaller" not in b["last_plan"]
    assert _census(st, "drift_smaller_open") == 0


def test_e19_the_short_mirror_image_sizes_the_smaller_short(monkeypatch):
    """His net -300 on the fixture market (100 long / 400 other) against
    the venue's -800 (100 / 900): drift 0.625, both short -> the book
    opens SHORT on the smaller short, -300, never -800."""
    _shorts_on(monkeypatch)
    p = _short_world()
    v = _Venue()
    st = _tick(p, v, http=_mkt(100.0, 900.0))
    b = _one_book(p)
    assert b["intent"] == SHORT and b["target"] == -300
    plan = b["last_plan"]
    assert plan["drift"] == pytest.approx(0.625) and plan["drift_sized_smaller"]["net"] == -300.0
    assert plan["drift_sized_smaller"]["fills_net"] == -300.0 and plan["drift_sized_smaller"]["venue_net"] == -800.0
    assert _census(st, "drift_smaller_open") == 1 and _census(st, "short_open") == 1
    pl = _places(v)
    assert len(pl) == 1 and pl[0][3] == 300 and pl[0][6] == SHORT


def test_e19_e12_at_his_level_opens_on_the_smaller_net_and_past_the_band_flows_only(monkeypatch):
    # (i) his block (10,000 at 0.61, 2.5 h old) and his add (1,974.6, 10 s
    # old); the mark 0.605 sits UNDER his cost on a long -- a better price
    # than his, which lane 1 (E12's at-or-better allowance, section 35)
    # admits by name before the 2c tolerance is consulted (`at_or_better`;
    # on the lane's own base it read `within_tol`): the block is admitted
    # and the book opens on the whole smaller net
    p, v, http = _martinez(monkeypatch, block=10000.0)
    st = _tick(p, v, http=http)
    b = _one_book(p)
    assert b["target"] == 1197 and b["last_plan"]["catchup"]["why"] == "at_or_better"
    assert _census(st, "open_catchup") == 1 and _census(st, "drift_smaller_open") == 1
    assert max(x[3] for x in _places(v)) == 1197
    # (ii) the same fills, the mark 8.5c over his cost (inside the 15c side
    # band, past the 2c tolerance): flow only -- 10 % of the 1,974.6 he
    # added since first sight, never the block
    p2, v2, http2 = _martinez(monkeypatch, bid=0.69, ask=0.70, block=10000.0)
    st2 = _tick(p2, v2, http=http2)
    b2 = _one_book(p2)
    assert b2["target"] == 197 and b2["flow_base"] == pytest.approx(10000.0)
    assert _census(st2, "open_flow_only") == 1 and _census(st2, "drift_smaller_open") == 1
    assert _census(st2, "side_band") == 0
    pl = _places(v2)
    assert pl and max(x[3] for x in pl) == 197


L34_DRIFT_SECTION = '''    drift, drift_src = _drift_for(r)
    net, snap_net = _net_for(r, drift, short=shorts)
    # THE DRIFT HIS FILLS EXPLAIN (lane 4, 2026-09-08; $14,254/6 h of his
    # fills refused `drift`): the whole-book walk is older than one tick
    # and his fills' net stands above it on the leg by exactly the adds
    # the chain / s1 lanes ingested after the walk's clock -- the
    # disagreement is the walk's age, not a reading nobody made -- so the
    # INCREASE is sized on the fills' net (mi.drift_explained, named on
    # the plan). A reduce never sizes on a reading (E15 below, E12b's
    # hold kept); the per-market read (stamped this tick) and any
    # unexplained drift refuse as today
    fills_net_all = mi.his_net(r.his_long, r.his_other)
    drift_ex = None
    if (drift.refusal == "drift" and drift_src == "book" and r.snap_age is not None
            and float(r.snap_age) > POLL_S and _num(t.snap_read_at.get(w)) is not None):
        # the walk's own clock (the _Tick field's paragraph); a walk whose
        # read this tick did not stamp explains nothing
        snap_at = float(t.snap_read_at[w]) - float(r.snap_age)
        ex = mi.drift_explained(fills, la, oa, short, fills_net_all, snap_net, snap_at)
        if ex is not None:
            drift = rules.DriftRule(True, "derived", None, drift.drift)
            net = fills_net_all
            drift_ex = {"snapshot_at": snap_at, **ex}
'''


def test_e19_the_existing_book_path_is_lane_34s_byte_for_byte_and_a_standing_book_refuses_drift_as_today(monkeypatch):
    """The drift section of _tick_book (from `drift, drift_src =
    _drift_for(r)` to `venue_int = int(r.venue)`) is lane 34's text,
    byte for byte; the E19 arm sits at the increase gate and reads the
    in-memory flag the candidate set on the OPENING tick alone. A
    standing book -- read from its row next tick, no flag -- under the
    same disagreement refuses its increase `drift` as today."""
    src = inspect.getsource(ml._tick_book)
    start = src.index("    drift, drift_src = _drift_for(r)\n")
    end = src.index("    venue_int = int(r.venue)\n")
    assert src[start:end] == L34_DRIFT_SECTION
    # the review's CRITICAL-1 fold: the ONE smaller_reading call in
    # _tick_book is the opening tick's clamp before mirror_target, on the
    # flag alone, after lane 34's section (test_pnl_l8_review_pins)
    assert src.count("smaller_reading(") == 1 and "_drift_smaller" in src
    assert src.index("smaller_reading(") > end
    # the arm's shape: the flag, the market read, the drift name -- nothing else lifts it
    arm = src[src.index('inc_refusal = drift.refusal or "snapshot_stale"'):src.index("    increasing = (target < ledger)")]
    assert 'inc_refusal == "drift" and drift_src == "market"' in arm
    assert 'book.get("_drift_smaller") is not None' in arm
    # tick 1: the open (the flag set, consumed by the first plan)
    p, v, http = _martinez(monkeypatch)
    _tick(p, v, http=http)
    b = _one_book(p)
    assert "_drift_smaller" not in b and b["target"] == 1197
    placed = len(_places(v))
    # tick 2: he adds 5,000, the venue 5,000 with him (drift 0.44 stands);
    # the book is read from its row: the increase is refused `drift`
    p.fills = [_fill(M, "BUY", FILLS_NET, 0.61, NOW - 10), _fill(M, "BUY", 5000.0, 0.62, NOW + 50)]
    st2 = _tick(p, v, now=NOW + 60, http=_mkt(MKT_LONG + 5000.0, MKT_OTHER))
    assert _census(st2, "drift") >= 1 and _census(st2, "drift_smaller_open") == 0
    assert len(_places(v)) == placed, "no new increase on the standing book"


# ------------------------------------------------------- (b) the collapse

def test_e19_kostyuks_shape_stays_collapsed_to_the_venues_share():
    """D1's verbatim rows: every shared tx's poll rows SUM to its chain
    row (15,164 = 4,996 + 5,172 + 4,996), so they are its maker splits
    and collapse: 55,993.4 / 29,555.0, the exit worker's snapshot to the
    share, ten rows and 46,376.2 sh dropped. E19b: the claim holds on
    THE READER (his_fills, D1's key: this IS D1's figure) and on the
    unwired reference alike -- the one shape both keys agree on, so it is
    read on both here."""
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, _d1_rows())
            fills = await ms.his_fills(c, "rn1", D1_CID)
            pos = mi.net_positions(fills)
            assert abs(pos[K] - 55993.4) <= 1.0 and abs(pos[NS] - 29555.0) <= 1.0, pos
            assert ms.his_fills_dedup() == {"dup_rows": 10, "dup_shares": 46376.2}
            ref = mi.net_positions(await ms.his_fills_distinct(c, "rn1", D1_CID))
            assert ref == pos and ms.his_fills_distinct_dedup() == {"dup_rows": 10, "dup_shares": 46376.2}
            # the brief's shape too: one fill reported by chain and poll with
            # identical price and size collapses (under either key)
            await c.execute("DELETE FROM trades")
            await _insert(c, [("chain", "0xsame", K, "BUY", 15164.0, 0.563, 1),
                              ("poll", "0xsame", K, "BUY", 15164.0, 0.563, 1)])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            assert mi.net_positions(fills)[K] == 15164.0 and ms.his_fills_dedup() == {"dup_rows": 1, "dup_shares": 15164.0}
            assert mi.net_positions(await ms.his_fills_distinct(c, "rn1", D1_CID))[K] == 15164.0
        finally:
            await _drop(admin, c, name)
    _run(run())


def _martinez_rows():
    """The long-side rows of hard2/book_534_1424.log verbatim (his_ts,
    source, size, price), the shared txs named by their seen clock: the
    four chain rows at 0.51 (11:24Z), s1 5,245 @0.59 (12:08:40Z), the
    12:09:24Z sweep (s1 5,225 @0.62, poll 3,601 @0.61, poll 5,245 @0.61),
    the 12:09:45Z sweep (s1 5,225 @0.61, poll 4,283.5 @0.60); the
    other-side chain rows to 12:10:07Z (3,950.9) and the 12:23:33Z pair
    (s1 5,225 @0.15, poll 5,245 @0.15)."""
    return [
        ("chain", "0xm1124a", K, "BUY", 9.7, 0.51, 0), ("chain", "0xm1124b", K, "BUY", 2.0, 0.51, 1),
        ("chain", "0xm1124c", K, "BUY", 213.7, 0.51, 12), ("chain", "0xm1124d", K, "BUY", 5.0, 0.51, 12),
        ("chain", "0xo1125", NS, "BUY", 20.4, 0.51, 64), ("chain", "0xo1127", NS, "BUY", 408.2, 0.51, 225),
        ("chain", "0xo1144", NS, "BUY", 3519.1, 0.31, 1239),
        ("s1", "0xm120840", K, "BUY", 5245.0, 0.59, 2680),
        ("s1", "0xm120924", K, "BUY", 5225.0, 0.62, 2724),
        ("poll", "0xm120924", K, "BUY", 3601.0, 0.61, 2724),
        ("poll", "0xm120924", K, "BUY", 5245.0, 0.61, 2724),
        ("chain", "0xo120925", NS, "BUY", 3.2, 0.37, 2725),
        ("s1", "0xm120945", K, "BUY", 5225.0, 0.61, 2745),
        ("poll", "0xm120945", K, "BUY", 4283.5, 0.60, 2745),
        ("s1", "0xo122333", NS, "BUY", 5225.0, 0.15, 3573),
        ("poll", "0xo122333", NS, "BUY", 5245.0, 0.15, 3573),
    ]


def test_e19_martinez_shape_counts_every_row_to_the_venues_29054_9():
    """E19b RE-PIN (2026-09-08). The 29,054.9 claim now holds on the
    UNWIRED reference alone (his_fills_distinct). THE READER -- his_fills,
    D1's key byte for byte -- reads Martinez's long at 15,925.4 (chain
    230.4 + s1 15,695; the four poll rows under s1 keys dropped, 18,374.5
    sh) and the net 11,974.5 at 12:10:43Z's rows: the under-read E19b
    ACCEPTS on the fills' side, because the fills-vs-venue first run
    (17:07Z) showed lane 8's key over-reading the venue on the day's live
    books (old closer 22 markets, new closer 4; book 611 old_net -966.9 =
    venue against new_net +19,349.5). Part (a) -- rules.smaller_reading --
    covers the reopen from the venue's side and is untouched (the pins
    above)."""
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, _martinez_rows())
            # THE READER: D1's figure
            fills = await ms.his_fills(c, "rn1", D1_CID)
            pos = mi.net_positions(fills)
            assert pos[K] == pytest.approx(230.4 + 15695.0, abs=0.05), pos
            assert pos[NS] == pytest.approx(3950.9 + 5225.0, abs=0.05), pos
            assert ms.his_fills_dedup() == {"dup_rows": 4, "dup_shares": 18374.5}
            # the net the reopen READ at 12:10:43Z (the other side's chain rows
            # alone then): 11,974.5 on these rows, rounded to the tenth as
            # the log prints them; candref_1408.log's own figure is 11,974.6
            # (FILLS_NET), a tenth apart on the rows' rounding
            assert mi.his_net(pos[K], 3950.9) == pytest.approx(11974.5, abs=0.05)
            assert abs(mi.his_net(pos[K], 3950.9) - FILLS_NET) <= 0.11
            # THE REFERENCE: lane 8's key reads the venue's own per-market
            # snapshot in 534's last plan, mkt_long 29,054.9
            ref = mi.net_positions(await ms.his_fills_distinct(c, "rn1", D1_CID))
            assert ref[K] == pytest.approx(MKT_LONG, abs=0.05), ref
            # the other side: the chain rows to 12:10:07Z (the venue's 3,950.8
            # within its own rounding) plus the 12:23:33Z pair, two fills
            assert ref[NS] == pytest.approx(3950.9 + 5225.0 + 5245.0, abs=0.05), ref
            assert ms.his_fills_distinct_dedup() == {"dup_rows": 0, "dup_shares": 0.0}
            # the reference's read never moves the reader's counter
            assert ms.his_fills_dedup() == {"dup_rows": 4, "dup_shares": 18374.5}
            # and the net the reopen should have read at 12:10:43Z
            assert mi.his_net(MKT_LONG, 3950.9) == pytest.approx(25104.0, abs=0.1)
            # D1's key spelled out on the same rows: the four poll rows, 18,374.5 sh
            old = await c.fetch(
                "SELECT sum(size)::float8 AS s, count(*) AS n FROM (SELECT t.size, COALESCE(t.source, '') IN ('chain', 's1') AS leg, "
                "bool_or(COALESCE(t.source, '') IN ('chain', 's1')) OVER (PARTITION BY t.whale_id, lower(t.tx_hash), t.asset, upper(t.side)) AS has "
                "FROM trades t) x WHERE NOT x.leg AND x.has")
            assert old[0]["n"] == 4 and old[0]["s"] == pytest.approx(18374.5)
        finally:
            await _drop(admin, c, name)
    _run(run())


def test_e19_a_tx_with_three_price_levels_counts_all_three():
    """E19b RE-PIN: 'all three' holds on the unwired reference; THE READER
    (D1) keeps the s1 record alone and drops both poll rows under its key
    (5,225; two rows / 8,846 sh dropped) -- less of his flow, never more."""
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, [("s1", "0xsweep", K, "BUY", 5225.0, 0.62, 1),
                              ("poll", "0xsweep", K, "BUY", 3601.0, 0.61, 1),
                              ("poll", "0xsweep", K, "BUY", 5245.0, 0.61, 1)])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            assert [(f["source"], f["size"], f["price"]) for f in fills] == [("s1", 5225.0, 0.62)]
            assert mi.net_positions(fills)[K] == 5225.0
            assert ms.his_fills_dedup() == {"dup_rows": 2, "dup_shares": 3601.0 + 5245.0}
            ref = await ms.his_fills_distinct(c, "rn1", D1_CID)
            assert [(f["source"], f["size"], f["price"]) for f in ref] == [
                ("s1", 5225.0, 0.62), ("poll", 3601.0, 0.61), ("poll", 5245.0, 0.61)]
            assert mi.net_positions(ref)[K] == 5225.0 + 3601.0 + 5245.0
            assert ms.his_fills_distinct_dedup() == {"dup_rows": 0, "dup_shares": 0.0}
        finally:
            await _drop(admin, c, name)
    _run(run())


def test_e19_the_s1_poll_pair_with_identical_price_and_size_collapses():
    """E19b RE-PIN: the cent-witness verdicts (0xcent one fill, 0xoff two)
    hold on the unwired reference; THE READER (D1) collapses every poll
    row under an s1 key -- the three s1 rows alone, 5,445 sh dropped."""
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, [("s1", "0xpair", K, "BUY", 5245.0, 0.59, 1),
                              ("poll", "0xpair", K, "BUY", 5245.0, 0.59, 1),
                              # the price to the CENT: 0.590 and 0.5904 are one price
                              ("s1", "0xcent", K, "BUY", 100.0, 0.590, 5),
                              ("poll", "0xcent", K, "BUY", 100.0, 0.5904, 5),
                              # a cent apart at the same size: two fills (the reference)
                              ("s1", "0xoff", K, "BUY", 100.0, 0.59, 9),
                              ("poll", "0xoff", K, "BUY", 100.0, 0.60, 9)])
            fills = await ms.his_fills(c, "rn1", D1_CID)
            assert [(f["source"], f["size"]) for f in fills] == [("s1", 5245.0), ("s1", 100.0), ("s1", 100.0)]
            assert ms.his_fills_dedup() == {"dup_rows": 3, "dup_shares": 5445.0}
            ref = await ms.his_fills_distinct(c, "rn1", D1_CID)
            assert [(f["source"], f["size"]) for f in ref] == [
                ("s1", 5245.0), ("s1", 100.0), ("s1", 100.0), ("poll", 100.0)]
            assert ms.his_fills_distinct_dedup() == {"dup_rows": 2, "dup_shares": 5345.0}
        finally:
            await _drop(admin, c, name)
    _run(run())


def test_e19_a_size_off_by_a_thousandth_collapses_by_a_share_on_a_hundred_not_and_5225_5245_are_two():
    """E19b RE-PIN: the dust verdicts hold on the unwired reference; THE
    READER (D1) drops every poll row under an s1 key whatever its size
    (10,200 / 5,225; four rows, 15,455.001 sh)."""
    async def run():
        admin, c, name = await _scratch()
        try:
            await _insert(c, [("s1", "0xdust", K, "BUY", 100.0, 0.5, 1),
                              ("poll", "0xdust", K, "BUY", 100.001, 0.5, 1),       # within 0.01 sh
                              ("s1", "0xshare", K, "BUY", 100.0, 0.5, 5),
                              ("poll", "0xshare", K, "BUY", 101.0, 0.5, 5),        # 1 sh = 1 %: two fills (the reference)
                              ("s1", "0xpermille", K, "BUY", 10000.0, 0.5, 9),
                              ("poll", "0xpermille", K, "BUY", 10009.0, 0.5, 9),   # 0.09 %: within 0.1 %
                              ("s1", "0xmartinez", NS, "BUY", 5225.0, 0.15, 13),
                              ("poll", "0xmartinez", NS, "BUY", 5245.0, 0.15, 13)])  # 20 sh, 0.38 %: two fills (the reference)
            fills = await ms.his_fills(c, "rn1", D1_CID)
            pos = mi.net_positions(fills)
            assert pos[K] == 100.0 + 100.0 + 10000.0 and pos[NS] == 5225.0
            assert ms.his_fills_dedup() == {"dup_rows": 4, "dup_shares": 100.001 + 101.0 + 10009.0 + 5245.0}
            ref = mi.net_positions(await ms.his_fills_distinct(c, "rn1", D1_CID))
            assert ref[K] == 100.0 + 100.0 + 101.0 + 10000.0 and ref[NS] == 5225.0 + 5245.0
            assert ms.his_fills_distinct_dedup() == {"dup_rows": 2, "dup_shares": 10109.001}
        finally:
            await _drop(admin, c, name)
    _run(run())


def test_e19_every_reader_of_the_collapsed_figure_reads_it_through_his_fills():
    """One function holds the key: the live tick (the candidate, the book),
    the shadow (his_paired_sh, fills_dedup_*), the report. Nothing else
    partitions his rows by tx. E19b RE-PIN: that one function is D1's
    key again (the one clause, no split arm, no repeat pairing); lane 8's
    two arms live on the UNWIRED reference `his_fills_distinct`, which no
    money path names and the module itself never calls."""
    from sportsassets.analytics import mirror_report as mr
    sql = inspect.getsource(ms.his_fills)
    assert "(COALESCE(f.source, '') NOT IN ('chain', 's1') AND f.has_net_leg) AS collapsed" in sql
    for frag in ("match_sum", "match_vwap", "leg_vwap", "rep.m_rank", "tx_key", "WINDOW w AS"):
        assert frag not in sql, frag
    # the reference carries the review's fold as lane 8 built it: the leg
    # is ONE source's rows, the repeat pairs are ranked one-to-one, the
    # price witness is the same cent or half a cent in numeric to the mil
    ref = inspect.getsource(ms.his_fills_distinct)
    assert "WINDOW w AS (PARTITION BY f.whale_id, f.tx_key, f.asset, upper(f.side))" in ref
    assert "abs(k.match_sum - leg.leg_size) <= greatest(0.01, 0.001 * leg.leg_size)" in ref
    assert "GROUP BY n.source) leg" in ref
    assert "round(n.price::numeric, 2) = round(m.price::numeric, 2)" in ref
    assert "OR abs(round(n.price::numeric, 3) - round(m.price::numeric, 3)) <= 0.005)" in ref
    assert "abs(n.size - m.size) <= greatest(0.01, 0.001 * n.size)" in ref
    assert "WHERE rep.m_id = k.id AND rep.m_rank = rep.n_rank" in ref
    assert "NOT WIRED" in ref and "81,470.2" in ref and "119,856.5" in ref and "_FILLS_DEDUP_DISTINCT" in ref
    # no money path names the reference; every reader is his_fills
    for mod in (ml, mr):
        src = inspect.getsource(mod)
        assert "has_net_leg" not in src and "ms.his_fills(" in src, mod.__name__
        assert "his_fills_distinct" not in src, mod.__name__
    live = inspect.getsource(ml)
    assert "d = ms.his_fills_dedup()" in inspect.getsource(ml._count_fills_dedup)
    assert live.count("await ms.his_fills(t.pool, w, cid)") == 2      # the candidate, the book
    shadow = inspect.getsource(ms)
    assert shadow.index("fills = await his_fills(pool, whale, condition_id)") < shadow.index(
        "his_paired_sh=round(min(his_long, his_other), 4)")
    assert shadow.count("his_fills_distinct(") == 1, "the def alone: the module never calls it"
    for fn in (ms.shadow_market, ms.tick_once, ms.map_market, ms.compute_ratio, ms.active_conditions):
        assert "his_fills_distinct" not in inspect.getsource(fn), fn.__name__


# --------------------------------------------------------- (c) the preset

def test_e19_the_fills_vs_venue_preset_carries_both_keys_and_the_venues_figures_in_case_order():
    text = YML.read_text()
    yaml.safe_load(text)
    line = next(ln for ln in text.splitlines() if ln.lstrip().startswith('*) echo "sql: arg must be one of'))
    names = line.split("one of ", 1)[1].split(" (got", 1)[0].split("|")
    labels = re.findall(r"^ {16}([a-z0-9-]+)\) ", text[text.index('case "$ARG" in'):text.index(line)], re.M)
    # beside the drift measures (book-drift, drift-16); the pinned neighbours
    # (nf-venue|exits-paired|take-band|exits-band|closed-while-he-traded|hourly since
    # FILL lane 0b, books-new|flow-books|nf-his,
    # latency-census|fills-answered|close-rows) are untouched
    assert names == labels and names.index("fills-vs-venue") == names.index("drift-16") + 1
    assert "drift-16|fills-vs-venue|books-new|" in line
    # FILL lane 4 placed fill-answers between closed-while-he-traded and hourly
    assert "nf-venue|exits-paired|take-band|exits-band|closed-while-he-traded|fill-answers|hourly (got" in line
    assert names[-1] == "hourly"
    body = text[text.index("fills-vs-venue) SQL="):text.index("books-new) SQL=")]
    sql = body.split('SQL="', 1)[1].split('"; TO=', 1)[0]
    assert sql.count("; ") >= 1 and sql.rstrip().endswith(";"), "two statements: per market, the totals"
    for col in ("chain_rows", "chain_sh", "s1_rows", "s1_sh", "poll_rows", "poll_sh", "old_long", "old_other",
                "new_long", "new_other", "old_dropped_sh", "new_dropped_sh", "kept_usd", "later_sh",
                "mkt_long", "mkt_other", "snap_net", "flow_net", "bk.drift", "bk.drift_src", "gap_old", "gap_new"):
        assert col in sql, col
    # both keys SQL-side, the new one the collapse's own two arms
    assert "(NOT k.net_leg AND k.has_net_leg) AS old_collapsed" in sql
    assert "abs(k.match_sum - leg.leg_size) <= greatest(0.01, 0.001 * leg.leg_size)" in sql
    assert "GROUP BY n.source) leg" in sql
    assert "rep.m_rank = rep.n_rank)), false) AS new_collapsed" in sql
    assert "round(k.match_vwap::numeric, 2) = round(leg.leg_vwap::numeric, 2)" in sql
    assert "OR abs(round(n.price::numeric, 3) - round(m.price::numeric, 3)) <= 0.005)" in sql
    assert "ORDER BY gap_new DESC NULLS LAST" in sql and "interval '24 hours'" in sql
    assert "EXISTS (SELECT 1 FROM bk WHERE bk.condition_id = t.condition_id)" in sql
    assert "count(*) AS markets" in sql and "new_closer" in sql and "old_closer" in sql
    assert "$" not in sql, "nothing for the shell to expand"


def test_e19_the_census_name_the_docs_and_no_knob():
    keys = ml.CENSUS_KEYS
    assert keys.count("drift_smaller_open") == 1
    assert keys[keys.index("drift_smaller_open") + 1] == "registered_no_increase"
    # FILL lane 11 (2026-09-09) placed cand_market_closed_db immediately before
    # this key; FILL lane 5 (2026-09-08) he_holds / he_holds_unread /
    # reopen_refused before that; T2 (FILL lane 4) and FILL lane 3 before those,
    # E14, E14b and E20 before them (the chain from this key, the convention
    # every lane follows)
    # E23 (FILL lane 23) placed its six names immediately before the key (the chain moves by six more)
    # E23 (FILL lane 23, 2026-09-09) placed its six cancel_fill_* / disagree_fill_*
    assert keys[keys.index("drift_smaller_open") - 1] == "disagree_fill_ambiguous"
    assert keys[keys.index("drift_smaller_open") - 2] == "disagree_fill_unexplained"
    assert keys[keys.index("drift_smaller_open") - 3] == "disagree_fill_unread"
    assert keys[keys.index("drift_smaller_open") - 4] == "disagree_fill_adopted"
    assert keys[keys.index("drift_smaller_open") - 5] == "cancel_fill_unread"
    assert keys[keys.index("drift_smaller_open") - 6] == "cancel_fill_late"
    # E21 (FILL lane 10, 2026-09-09) placed its six fast_* names immediately before this key,
    # after FILL lane 11's one (the chain grows by six)
    assert keys[keys.index("drift_smaller_open") - 7] == "fast_status_unread"
    assert keys[keys.index("drift_smaller_open") - 8] == "fast_add_took"
    assert keys[keys.index("drift_smaller_open") - 9] == "fast_add_replaced"
    assert keys[keys.index("drift_smaller_open") - 10] == "fast_add_kept"
    assert keys[keys.index("drift_smaller_open") - 11] == "fast_his_add"
    assert keys[keys.index("drift_smaller_open") - 12] == "fast_order_open"
    # FILL lane 11 (2026-09-09) placed its one name immediately before this key, after E22's four (the chain grows by one)
    # FILL lane 16 placed its one `turn_woke_fast` before E21's six (the chain moves by one more)
    assert keys[keys.index("drift_smaller_open") - 13] == "turn_woke_fast"
    assert keys[keys.index("drift_smaller_open") - 14] == "cand_market_closed_db"
    # E22 (FILL lane 22, 2026-09-09) placed its four lost_fill_* names immediately
    # before this key, after FILL lane 5's three (the chain grows by four)
    assert keys[keys.index("drift_smaller_open") - 15] == "lost_fill_ambiguous"
    assert keys[keys.index("drift_smaller_open") - 16] == "lost_fill_unexplained"
    assert keys[keys.index("drift_smaller_open") - 17] == "lost_fill_unread"
    assert keys[keys.index("drift_smaller_open") - 18] == "lost_fill_adopted"
    assert keys[keys.index("drift_smaller_open") - 19] == "reopen_refused"
    assert keys[keys.index("drift_smaller_open") - 20] == "he_holds_unread"
    assert keys[keys.index("drift_smaller_open") - 21] == "he_holds"
    assert keys[keys.index("drift_smaller_open") - 22] == "fill_answers_absent"
    assert keys[keys.index("drift_smaller_open") - 23] == "fill_answer_write_failed"
    assert keys[keys.index("drift_smaller_open") - 24] == "order_open_his_exit"
    assert keys[keys.index("drift_smaller_open") - 25] == "cover_in_band"
    assert keys[keys.index("drift_smaller_open") - 26] == "exit_take_in_band"
    assert keys[keys.index("drift_smaller_open") - 27] == "take_in_band"
    assert keys[keys.index("drift_smaller_open") - 28] == "exit_take_rested"
    assert keys[keys.index("drift_smaller_open") - 29] == "wrong_sign_hold"
    assert keys[keys.index("drift_smaller_open") - 30] == "adopt_prior_venue_settled"
    assert keys[-1] == "cand_terminal_skipped" and len(set(keys)) == len(keys)
    src = inspect.getsource(rules)
    # the knob count of the tip (lane 1's MIRROR_CATCHUP_PCT and
    # MIRROR_CATCHUP_MAX_CENTS make 20 rails; lane 6's MIRROR_REST_MIN_LIFE_S
    # the fourth wait; the lane's own base read 18 and 3): E19 adds none;
    # FILL lane 0a (2026-09-08) made the candidate's side band the 21st rail
    # (LIVE_SIDE_PRICE_BAND_MAX, the default 0.15 as its ceiling); E14
    # (FILL lane 2, 2026-09-08) adds ONE downward-only rail,
    # MIRROR_TAKE_BAND (21 -> 22), pinned by name in test_e14_take_band; FILL
    # lane 3 (2026-09-08) adds ONE more downward-only rail, MIRROR_EXIT_TAKE_BAND
    # (22 -> 23, inert at its default), pinned by name in test_fill_x1_exit_band
    # E22 (FILL lane 22, 2026-09-09) adds ONE wait that may only LENGTHEN,
    # MIRROR_LOST_FILL_REREAD_S (min_wait_env 4 -> 5), pinned by name in test_e22_lost_fill_adopt
    assert src.count("capped_env(") == 23 and src.count("min_wait_env(") == 5
    assert "MIRROR_DRIFT_MAX = capped_env" in src and "E19" in inspect.getsource(rules.admission)
    for name in ("MIRROR_SMALLER", "SMALLER_READING", "DRIFT_SMALLER"):
        assert name not in src, "no knob"
    doc = (REPO / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. E19 \(2026-09-08, PNL lane 8\)", doc, re.M)
    for k in ("drift_smaller_open", "drift_sized_smaller", "smaller_reading", "fills-vs-venue", "29,054.9",
              "18,374.5", "0.1 %"):
        assert k in doc, k
    # E19b: the withdrawal is written up under its own header with the
    # first run's numbers and the reference's name
    assert re.search(r"^## 43\. E19b \(2026-09-08\)", doc, re.M)
    for k in ("his_fills_distinct", "81,470.2", "119,856.5", "old_closer 22", "new_closer 4", "drift 0 -> 7"):
        assert k in doc, k
