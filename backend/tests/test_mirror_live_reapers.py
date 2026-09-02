"""Reaper isolation, both directions (mirror P1 step 8).

Owner order 2026-09-02 ("go for it, let's get this working"). The
mirror's orders live in mirror_orders, a table none of the three
reapers reads for scope, so a reaper that keys on live_orders.status
cannot see one. But the venue-side reaper asks the VENUE, and the two
lost-response searches ask the venue's open book and trade log by
FINGERPRINT: BUY, his cent, the row's whole quantity, inside a time
window. A mirror rest is placed at his cent for the book's whole
quantity on a market a stranded copy row may share -- the panel review
named it the one collision the fingerprint cannot see. So every reaper
now carries the protected id set (the desk's ids plus every mirror
order's) and excludes it BY NAME: never cancelled, never adopted, never
booked onto a copy row. An unreadable set is unreadable: the venue
reaper skips its pass (the stance it has always taken for the desk's
ids), the ledger reaper leaves the row, and the rest lane holds its row
'submitting' with no id -- today's paths, every one.

With no mirror rows the protected set IS today's manual set and every
pass is byte-for-byte today's, which these tests pin against the
fixtures of tests/test_rest_lane_only_touches_lost_copies.py, whose
_Pmus follows the real adapter's rules.
"""
import asyncio
import inspect
import logging
import time

import pytest

from sportsassets import live_executor as le
from tests.test_rest_lane_only_touches_lost_copies import (
    _Pmus, _Row, _bind, _fill, _fingerprint, _kinds, _open)

SLUG = "aec-atp-a-b-2026-09-01"
MIRROR_OID = "mirror-gtc-1"
# The manual read the venue reaper carried inline until this step, and
# the mirror read beside it. The FIRST literal is what _reap_stale_resting_bids
# executed at HEAD before the substitution: pinned so the swap is provably
# a substitution and not a rewrite of the desk's exclusion.
HEAD_MANUAL_SQL = ("SELECT order_id FROM live_orders WHERE order_id IS NOT "
                   "NULL AND COALESCE(whale_username,'') = 'manual'")
MIRROR_SQL = "SELECT order_id FROM mirror_orders WHERE order_id IS NOT NULL"


# ------------------------------------------------------------ fixtures

class _LedgerPool:
    """A ledger that answers each reaper read by the SQL it carries:
    the desk's ids, the mirror's ids, the venue reaper's scope rows, and
    _adopt_lost_bid's other-rows-on-the-slug set. Unknown reads return
    nothing and are recorded, so a test can pin the exact sequence."""

    def __init__(self, scope=(), manual=(), mirror=(), known=(),
                 explained=0.0, manual_raises=False, mirror_raises=False,
                 scope_raises=False, adopt_raises=None):
        self.scope = list(scope)          # [(row_id, slug, placed_ts, pre_ids)]
        self.manual, self.mirror, self.known = list(manual), list(mirror), list(known)
        self.explained = explained
        self.manual_raises, self.mirror_raises = manual_raises, mirror_raises
        self.scope_raises = scope_raises
        self.adopt_raises = adopt_raises
        self.queries: list[tuple] = []

    async def fetch(self, sql, *a):
        self.queries.append(("fetch", sql, a))
        if "mirror_orders" in sql:
            if self.mirror_raises:
                raise RuntimeError('relation "mirror_orders" does not exist')
            return [_Row(order_id=o) for o in self.mirror]
        if "whale_username,'') = 'manual'" in sql:
            if self.manual_raises:
                raise RuntimeError("db blip")
            return [_Row(order_id=o) for o in self.manual]
        if "placed_ts" in sql:
            if self.scope_raises:
                raise RuntimeError("db")
            return [_Row(id=rid, us_market_slug=s, placed_ts=ts,
                         intent="ORDER_INTENT_BUY_LONG", his_price=0.48,
                         requested_shares=100.0, pre_ids=pre)
                    for rid, s, ts, pre in self.scope]
        if "us_market_slug = $1 AND id <> $2" in sql:
            return [_Row(order_id=o) for o in self.known]
        return []

    async def fetchval(self, sql, *a):
        self.queries.append(("fetchval", sql, a))
        return self.explained

    async def execute(self, sql, *a):
        self.queries.append(("execute", sql, a))
        if self.adopt_raises and "SET order_id=$2" in sql:
            raise self.adopt_raises

    def executes(self):
        return [q for q in self.queries if q[0] == "execute"]

    def fetch_sqls(self):
        return [q[1] for q in self.queries if q[0] == "fetch"]


@pytest.fixture
def stops(monkeypatch):
    seen: list[tuple] = []
    monkeypatch.setattr(le, "_copy_stop",
                        lambda reason, whale=None: seen.append((reason, whale)))
    return seen


@pytest.fixture
def no_sleep(monkeypatch):
    async def _sleep(s):
        return None
    monkeypatch.setattr(le.asyncio, "sleep", _sleep)


@pytest.fixture
def held(monkeypatch):
    """The account read _adopt_lost_bid makes last; (held, avg)."""
    state = {"v": (0, None)}

    async def _held(_slug):
        return state["v"]

    monkeypatch.setattr(le, "_pm_held", _held)

    def _set(v):
        state["v"] = v
    return _set


def _adopted(pool):
    return [q for q in pool.executes()
            if "SET order_id=$2, status='submitting'" in q[1]]


def _booked(pool):
    return [q for q in pool.executes() if "status='filled'" in q[1]]


def _reaper_touched_mirror(pmus, pool, mirror_ids) -> int:
    """The spec's census instrument, computed from what the fakes saw:
    a mirror order id cancelled, read for status, or adopted onto a
    copy row by any reaper. Must read 0 on every fixture here."""
    touched = {c[1] for c in pmus.calls if c[0] in ("cancel", "status")}
    touched |= {q[2][1] for q in _adopted(pool) if len(q[2]) > 1}
    return len(touched & set(mirror_ids))


# ======================================================= the venue reaper

def _venue_pass(pmus, pool, monkeypatch):
    _bind(pmus, monkeypatch)
    return asyncio.run(le._reap_stale_resting_bids(pool))


def _collision(oid=MIRROR_OID, **pool_kw):
    """THE COLLISION FIXTURE (§8.8): an id-less copy row on SLUG placed
    ten minutes ago, and a resting BUY at its cent (0.48 for his 0.48)
    for its whole quantity (100) created 5 s after the row -- inside the
    orphan window, older than TTL+60 s. Without the id set the reaper
    has every reason to call it the row's own orphan."""
    now = time.time()
    placed = now - 600.0
    bid = _open(oid, slug=SLUG, created_ts=placed + 5.0)
    pmus = _Pmus(open_orders=[bid], final_filled=0.0, final_state="cancelled")
    pool = _LedgerPool(scope=[(11, SLUG, placed, None)], **pool_kw)
    return pmus, pool


class TestTheVenueReaper:
    def test_the_collision_fixture_is_live_without_the_mirror_read(
            self, monkeypatch, no_sleep):
        """Control: the same bid, its id NOT in mirror_orders, is the
        orphan the reaper exists for -- cancelled, adopted, reconciled.
        Whatever the next test proves, it proves against a live net."""
        pmus, pool = _collision(oid="orphan")
        assert _venue_pass(pmus, pool, monkeypatch) == 1
        assert ("cancel", "orphan", SLUG) in pmus.calls
        assert _adopted(pool) and _adopted(pool)[0][2] == (11, "orphan")

    def test_a_mirror_gtc_is_neither_cancelled_nor_adopted_nor_booked(
            self, monkeypatch, no_sleep, caplog):
        caplog.set_level(logging.WARNING, logger=le.log.name)
        pmus, pool = _collision(mirror=[MIRROR_OID])
        assert _venue_pass(pmus, pool, monkeypatch) == 0
        assert _kinds(pmus) == ["open_orders"]          # listed, never touched
        assert pool.executes() == []                     # no adopt, no booking
        # excluded BY NAME, before the fingerprint is even consulted: the
        # "not our bid" line is the fingerprint's refusal and must not fire
        assert not any("is not our bid" in r.getMessage() for r in caplog.records)
        assert _reaper_touched_mirror(pmus, pool, [MIRROR_OID]) == 0

    def test_the_desk_is_still_spared_by_name(self, monkeypatch, no_sleep):
        pmus, pool = _collision(oid="desk", manual=["desk"])
        assert _venue_pass(pmus, pool, monkeypatch) == 0
        assert "cancel" not in _kinds(pmus) and pool.executes() == []

    def test_an_unreadable_protected_set_sweeps_nothing_and_says_so(
            self, monkeypatch, no_sleep, caplog):
        """Protected set None -> zero venue writes or reads beyond the
        listing, nothing written, and the pass's own log line: the
        stance the reaper has always taken when the desk's ids could
        not be read, now for either read."""
        caplog.set_level(logging.WARNING, logger=le.log.name)
        pmus, pool = _collision(oid="orphan", manual_raises=True)
        assert _venue_pass(pmus, pool, monkeypatch) == 0
        assert _kinds(pmus) == ["open_orders"]
        assert pool.executes() == []
        assert any(r.getMessage() == "resting-bid reaper: manual ids unreadable "
                   "— skipping this pass" for r in caplog.records)

    def test_a_047_absent_ledger_behaves_exactly_as_an_unreadable_manual_set(
            self, monkeypatch, no_sleep, caplog):
        """mirror_orders does not exist (migration 047 unapplied): the
        helper reads None, and the pass is BYTE-IDENTICAL to the pass an
        unreadable manual read has always produced -- the same venue
        calls, the same (absent) writes, the same log text. Not today's
        SWEEP: on an unmigrated ledger the net-under-the-net is off
        until 047 runs, by the helper's own contract (a list that might
        be half a list protects nothing)."""
        caplog.set_level(logging.WARNING, logger=le.log.name)
        pmus_a, pool_a = _collision(oid="orphan", manual_raises=True)
        n_a = _venue_pass(pmus_a, pool_a, monkeypatch)
        lines_a = [r.getMessage() for r in caplog.records]
        caplog.clear()
        pmus_b, pool_b = _collision(oid="orphan", mirror_raises=True)
        n_b = _venue_pass(pmus_b, pool_b, monkeypatch)
        lines_b = [r.getMessage() for r in caplog.records]
        assert (n_a, pmus_a.calls, pool_a.executes()) == (n_b, pmus_b.calls, pool_b.executes())
        assert n_b == 0 and pmus_b.calls == [("open_orders",)]
        assert lines_a == lines_b == ["resting-bid reaper: manual ids unreadable "
                                      "— skipping this pass"]

    def test_with_no_mirror_rows_the_pass_is_todays_pass(self, monkeypatch, no_sleep):
        """Every switch off, 047 applied, no mirror order anywhere: the
        protected set is exactly the manual set, read by the SAME SQL
        the reaper carried inline at HEAD, followed by one extra SELECT
        that returns nothing, then today's scope query -- and the orphan
        is cancelled, adopted and reconciled in today's order."""
        pmus, pool = _collision(oid="orphan", manual=["desk"], mirror=[])
        assert _venue_pass(pmus, pool, monkeypatch) == 1
        sqls = pool.fetch_sqls()
        assert sqls[0] == HEAD_MANUAL_SQL
        assert sqls[1] == MIRROR_SQL
        assert len(sqls) == 3 and "placed_ts" in sqls[2] and "order_id IS NULL" in sqls[2]
        # today's order: list, the reaper's cancel, the reconcile's own
        # cancel-and-read (a done order refuses the second cancel; the
        # terminal read is authoritative either way)
        kinds = _kinds(pmus)
        assert kinds[0] == "open_orders" and kinds[1] == "cancel"
        assert kinds.index("status") > kinds.index("cancel")
        ups = pool.executes()
        assert ups[0][1].startswith("UPDATE live_orders SET order_id=$2, status='submitting'")
        assert ups[0][2] == (11, "orphan")
        assert any("status='unfilled'" in q[1] for q in ups[1:])

    def test_a_pool_of_none_is_unchanged(self, monkeypatch, no_sleep):
        """The reaper is called without a pool from one caller today:
        no ledger, no scope, nothing swept -- and no protected read."""
        pmus = _Pmus(open_orders=[_open("orphan", slug=SLUG)])
        assert _venue_pass(pmus, None, monkeypatch) == 0
        assert _kinds(pmus) == ["open_orders"]

    def test_the_source_reads_the_set_before_the_scope_and_has_no_inline_query(self):
        src = inspect.getsource(le._reap_stale_resting_bids)
        i = src.index("protected = await _protected_order_ids(pool)")
        assert src.index("if protected is None:") > i
        assert "return 0" in src[i:src.index("placed_ts")]
        assert i < src.index("placed_ts") < src.index("cancel_order")
        assert "SELECT order_id FROM live_orders WHERE order_id IS NOT" not in src
        assert 'in protected_ids:' in src and "manual_ids" not in src
        # the skip keeps the log text of the read it replaced, verbatim
        assert ('log.warning("resting-bid reaper: manual ids unreadable — "\n'
                '                        "skipping this pass")') in src


# ====================================================== the ledger reaper

def _row(age_s=1200.0, status="submitting", pre_ids=None):
    return _Row(id=11, order_id=None, us_market_slug=SLUG, status=status,
                requested_shares=100.0, intent="ORDER_INTENT_BUY_LONG",
                his_price=0.48, limit_price=None, age_s=age_s,
                pre_ids=pre_ids, whale_username="rn1", add_of=None)


class TestTheLedgerReaper:
    def test_the_open_book_fixture_is_live_without_the_mirror_read(
            self, monkeypatch, no_sleep, held):
        bid = _fingerprint(slug=SLUG, age_s=1195.0)          # 5 s after the row
        pmus = _Pmus(open_orders=[bid])
        pool = _LedgerPool()
        out = asyncio.run(le._adopt_lost_bid(pool, pmus, _row(), 1200.0))
        assert out == "gtc-lost" and _adopted(pool)[0][2] == (11, "gtc-lost")

    def test_a_mirror_gtc_on_the_book_is_never_adopted(self, monkeypatch, no_sleep, held):
        """Same bid, its id in mirror_orders: the open-book search skips
        it, the trade log is asked next, and with nothing there and a
        clean account the row is a clean blind mark -- no id written."""
        bid = dict(_fingerprint(slug=SLUG, age_s=1195.0), order_id=MIRROR_OID)
        pmus = _Pmus(open_orders=[bid], trades=[])
        pool = _LedgerPool(mirror=[MIRROR_OID])
        out = asyncio.run(le._adopt_lost_bid(pool, pmus, _row(), 1200.0))
        assert out is None
        assert _adopted(pool) == [] and "cancel" not in _kinds(pmus)
        assert ("trades", SLUG) in pmus.calls           # the search moved on
        assert _reaper_touched_mirror(pmus, pool, [MIRROR_OID]) == 0

    def test_the_trade_log_fixture_is_live_without_the_mirror_read(
            self, monkeypatch, no_sleep, held):
        """Control: a fill the venue names for an order of our exact
        size at our cent, inside the window, id unknown to every ledger
        row -> reconciled from the venue's ORDER record by that id."""
        held((100, 0.48))
        pmus = _Pmus(open_orders=[], trades=[_fill(100.0, order_id="lost-9")],
                     final_filled=100.0, final_avg=0.48, final_state="filled")
        pool = _LedgerPool()
        out = asyncio.run(le._adopt_lost_bid(pool, pmus, _row(), 1200.0))
        assert out == "booked" and ("status", "lost-9") in pmus.calls
        assert _booked(pool)

    @pytest.mark.parametrize("explained,outcome", [(100.0, None), (0.0, "position")])
    def test_a_mirror_fill_in_the_trade_log_is_never_booked_onto_a_copy_row(
            self, monkeypatch, no_sleep, held, explained, outcome):
        """The mirror's order filled at his cent for the book's whole
        quantity inside the copy row's window. Its id is in
        mirror_orders, so the fill is nobody's on the copy ledger: not
        booked, not read for status, not adopted. With the standing row
        explaining the shares the row is a clean blind mark; with
        nothing explaining them it is NAMED for a human -- never a fill."""
        held((100, 0.48))
        pmus = _Pmus(open_orders=[], trades=[_fill(100.0, order_id=MIRROR_OID)],
                     final_filled=100.0, final_avg=0.48, final_state="filled")
        pool = _LedgerPool(mirror=[MIRROR_OID], explained=explained)
        out = asyncio.run(le._adopt_lost_bid(pool, pmus, _row(), 1200.0))
        assert out == outcome
        assert _booked(pool) == [] and _adopted(pool) == []
        assert ("status", MIRROR_OID) not in pmus.calls
        if outcome == "position":
            named = [q for q in pool.executes() if "status='error'" in q[1]]
            assert named and "venue holds a POSITION" in named[0][2][1]
        assert _reaper_touched_mirror(pmus, pool, [MIRROR_OID]) == 0

    def test_a_revisited_named_row_carries_the_same_exclusion(
            self, monkeypatch, no_sleep, held):
        """The 48-hour revisit reads only the trade log and the account
        (round eight); the mirror's fill is excluded there too."""
        held((100, 0.48))
        pmus = _Pmus(open_orders=[], trades=[_fill(100.0, order_id=MIRROR_OID)],
                     final_filled=100.0, final_avg=0.48, final_state="filled")
        pool = _LedgerPool(mirror=[MIRROR_OID], explained=100.0)
        out = asyncio.run(le._adopt_lost_bid(pool, pmus, _row(status="error"), 1200.0))
        assert out is None and _booked(pool) == []
        assert ("open_orders",) not in pmus.calls and "status" not in _kinds(pmus)

    @pytest.mark.parametrize("kw", [{"manual_raises": True}, {"mirror_raises": True}])
    def test_an_unreadable_protected_set_leaves_the_row_untouched(
            self, monkeypatch, no_sleep, held, kw, caplog):
        """Either read failing -> 'unreadable', the outcome the caller
        already has for an unreadable venue: nothing asked of the venue,
        nothing written, the row left for the next pass."""
        caplog.set_level(logging.WARNING, logger=le.log.name)
        pmus = _Pmus(open_orders=[_fingerprint(slug=SLUG, age_s=1195.0)],
                     trades=[_fill(100.0, order_id="lost-9")])
        pool = _LedgerPool(**kw)
        out = asyncio.run(le._adopt_lost_bid(pool, pmus, _row(), 1200.0))
        assert out == "unreadable"
        assert pmus.calls == [] and pool.executes() == []
        assert any("protected order ids unreadable" in r.getMessage()
                   for r in caplog.records)

    def test_both_searches_exclude_the_snapshot_and_the_protected_set(
            self, monkeypatch, no_sleep, held):
        """The open-book search's `exclude` and the trade-log `known`
        set both carry pre_ids | protected: the row's own snapshot, the
        desk's ids, the mirror's ids."""
        seen: dict = {}

        async def _find(pmus_, slug, shares, cents, window=None, exclude=None):
            seen["exclude"] = set(exclude or ())
            return None, True

        monkeypatch.setattr(le, "_find_lost_rest_bid", _find)
        pmus = _Pmus(open_orders=[], trades=[])
        pool = _LedgerPool(manual=["desk"], mirror=[MIRROR_OID], known=["other-row"])
        asyncio.run(le._adopt_lost_bid(pool, pmus, _row(pre_ids='["pre-X"]'), 1200.0))
        assert seen["exclude"] == {"pre-X", "desk", MIRROR_OID}
        src = inspect.getsource(le._adopt_lost_bid)
        assert "exclude=pre_ids | protected)" in src
        assert "known |= pre_ids | protected" in src
        # read once, before either search, after the row's own sanity checks
        assert (src.index("shares < 1") < src.index("_protected_order_ids(pool)")
                < src.index("_find_lost_rest_bid(") < src.index("recent_trades"))


# =============================================== the rest lane's lost response

class _BookAlways(_Pmus):
    """A venue that lists the same bids before AND after our placement:
    the owner's (or the mirror's) bid that predates ours."""

    def open_orders(self, slugs=None):
        self.calls.append(("open_orders",))
        return list(self._open)


def _rest(pmus, pool, monkeypatch, **kw):
    kw = _bind(pmus, monkeypatch, **kw)
    args = dict(pool=pool, row_id=7, us_slug=SLUG, his_price=0.48, shares=100.0,
                intent="ORDER_INTENT_BUY_LONG", whale="rn1", reaction=1.2)
    args.update(kw)
    return asyncio.run(le._rest_after_ioc(**args))


class TestTheRestLanesLostResponse:
    def test_the_lost_response_fixture_is_live_without_the_mirror_read(
            self, monkeypatch, stops, no_sleep):
        pmus = _Pmus(place_raises=True, open_orders=[_fingerprint()])
        out = _rest(pmus, _LedgerPool(), monkeypatch)
        assert out is None and ("cancel", "gtc-lost", SLUG) in pmus.calls

    def test_the_search_cannot_adopt_a_mirror_id(self, monkeypatch, stops, no_sleep):
        """The venue accepted our GTC and the read timed out; the only
        bid of our fingerprint on the book is the mirror's. Not ours:
        the row is held 'submitting' with no id for the reapers, and the
        mirror's order is never cancelled."""
        pmus = _Pmus(place_raises=True,
                     open_orders=[dict(_fingerprint(), order_id=MIRROR_OID)])
        pool = _LedgerPool(mirror=[MIRROR_OID])
        out = _rest(pmus, pool, monkeypatch)
        assert out is not None and out["status"] == "rest_unknown"
        assert out["order_id"] is None
        assert "held 'submitting' with no id" in out["raw"]["why"]
        assert "cancel" not in _kinds(pmus)
        assert ("rest_place_error", "rn1") in stops
        assert _reaper_touched_mirror(pmus, pool, [MIRROR_OID]) == 0

    def test_the_search_excludes_the_snapshot_and_the_protected_set(
            self, monkeypatch, stops, no_sleep):
        """Symmetric: a bid on the book BEFORE we placed is excluded by
        the snapshot; the desk's and the mirror's by name. Both sets
        reach the search as one `exclude`."""
        seen: dict = {}
        real = le._find_lost_rest_bid

        async def _find(pmus_, slug, shares, cents, window=None, exclude=None):
            seen["exclude"] = set(exclude or ())
            return await real(pmus_, slug, shares, cents, window=window,
                              exclude=exclude)

        monkeypatch.setattr(le, "_find_lost_rest_bid", _find)
        pre = dict(_fingerprint(age_s=20.0), order_id="pre-X")
        pmus = _BookAlways(place_raises=True, open_orders=[pre])
        pool = _LedgerPool(manual=["desk"], mirror=[MIRROR_OID])
        out = _rest(pmus, pool, monkeypatch)
        assert seen["exclude"] == {"pre-X", "desk", MIRROR_OID}
        assert out["status"] == "rest_unknown" and out["order_id"] is None
        assert out["raw"]["pre_ids"] == ["pre-X"]
        assert "cancel" not in _kinds(pmus)

    @pytest.mark.parametrize("kw", [{"manual_raises": True}, {"mirror_raises": True}])
    def test_an_unreadable_protected_set_holds_the_row_with_no_id(
            self, monkeypatch, stops, no_sleep, kw, caplog):
        """Unreadable -> not found: the book is not searched again after
        the raise (the one listing is the pre-placement snapshot), and
        the row is held 'submitting' with no id -- today's not-found path."""
        caplog.set_level(logging.WARNING, logger=le.log.name)
        pmus = _Pmus(place_raises=True, open_orders=[_fingerprint()])
        out = _rest(pmus, _LedgerPool(**kw), monkeypatch)
        assert out["status"] == "rest_unknown" and out["order_id"] is None
        assert _kinds(pmus).count("open_orders") == 1
        assert "cancel" not in _kinds(pmus)
        assert ("rest_place_error", "rn1") in stops
        assert any("protected order ids unreadable" in r.getMessage()
                   for r in caplog.records)

    def test_the_source_reads_the_set_inside_the_except_and_passes_the_union(self):
        src = inspect.getsource(le._rest_cycle)
        exc = src.index("the RESPONSE is lost, not the order")
        i = src.index("protected = await _protected_order_ids(pool)")
        assert exc < i < src.index("exclude=pre_ids | protected)") < src.index("if found is None:")
        # the unreadable branch is "not found", never a guess at an id
        branch = src[i:src.index("if found is None:")]
        assert "found = None" in branch


# ========================================================== the instrument

def test_reaper_touched_mirror_is_computable_and_discriminating(
        monkeypatch, no_sleep, held, stops):
    """The census key the P2 gate reads: 0 across every collision
    fixture above -- and > 0 on the control fixture when the same id
    is counted as the mirror's, so a 0 means the exclusion held, not
    that the instrument is blind."""
    pmus, pool = _collision(oid="orphan")
    _venue_pass(pmus, pool, monkeypatch)
    assert _reaper_touched_mirror(pmus, pool, ["orphan"]) == 1
    for pmus, pool, run in (
            (*_collision(mirror=[MIRROR_OID]), lambda p, q: _venue_pass(p, q, monkeypatch)),
            (_Pmus(open_orders=[dict(_fingerprint(slug=SLUG, age_s=1195.0), order_id=MIRROR_OID)],
                   trades=[_fill(100.0, order_id=MIRROR_OID)]),
             _LedgerPool(mirror=[MIRROR_OID], explained=100.0),
             lambda p, q: asyncio.run(le._adopt_lost_bid(q, p, _row(), 1200.0))),
            (_Pmus(place_raises=True, open_orders=[dict(_fingerprint(), order_id=MIRROR_OID)]),
             _LedgerPool(mirror=[MIRROR_OID]),
             lambda p, q: _rest(p, q, monkeypatch))):
        run(pmus, pool)
        assert _reaper_touched_mirror(pmus, pool, [MIRROR_OID]) == 0
