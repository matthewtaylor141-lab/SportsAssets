"""FILL lane 11 (2026-09-09): the candidate's terminal pre-check -- a
market the database already names closed or resolved costs no paced
venue read (docs/mirror-coverage.md section 55).

The rows (hard2/tick_2245.txt, the 22:44:43Z tick at 15 books): the
candidate stage was 33 paced quote reads of markets that had ENDED --
timing.candidates 16.2 s of tick_s 38.0 (rows 320 / 331), the census
no_mark 33 / venue_halted 33 / market_closed 0 (row 300), venue_state
MARKET_STATE_EXPIRED (row 333). Each was quote-read first and refused
afterwards: `no_mark` fires before rules.admission ever consults the
markets row, so whether those 33 rows read closed in the database is
in no file (FILL_C_judge_2.md item 5) -- the count this lane adds,
`cand_market_closed_db`, is what says so per tick (and its readable
copy `short.timing.cand_closed_db`, E6's block: mirror-tick's census
line is cut at 2400 characters and the census name sorts past the
cut). At 02:22Z the same stage read 1.2 s (hard2/ro_0222_a.txt row 28;
the census no_mark 2 on row 8 is tick-wide).

THE RULE, EXACTLY (pinned below): between the game-full memo / the
sibling-token read and `_read_market`, `_tick_candidate` reads the
market's own row (`_market`, `_SQL_MARKET`: closed, resolved,
resolved_prices). When the row says closed or resolved by the SAME
expression rules.admission refuses `market_closed` on --
rules.market_closed_fact: `closed is not False or resolved is not
False`, NULL reads as not-live -- the candidate is refused
`market_closed` (the existing name, counted as every exit name is)
with no venue call, `cand_market_closed_db` is counted beside it,
D1's terminal memo is written exactly as a terminal venue read writes
it (`_terminal_until[(w, cid)] = t.now + ms.UNMAPPED_TTL_S`, 900 s),
and the row's closed / resolved ride the walk's context. A row that
says live is handed to `_read_market` (its `market` parameter) so the
row is read ONCE. An unreadable or absent row -> the quote read as
before, then `market_unreadable` by the existing name. A market with a
book never reaches the pre-check. No rail, no plan field, no decision
word, no migration. Driven through tick_once against the worker
file's fakes (its autouse rails are imported) on test_e7_cand_memo.py's
memo fixtures, beside test_d1_review.py's terminal-memo pins.
"""
import hashlib
import inspect
import pathlib
import re

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_e7_cand_memo import _bbos, _spool
from tests.test_e9_fast_path import _fast
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, SLUG, _armed, _census, _his, _kinds, _places, _pool, _run, _tick, _Http, _Venue,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
NEW_NAME = "cand_market_closed_db"
EXPIRED = "MARKET_STATE_EXPIRED"
LIVE = {"closed": False, "resolved": False, "resolved_prices": None}
CLOSED = {"closed": True, "resolved": False, "resolved_prices": None}
# the 22:44Z cohort: 33 candidate quote reads on EXPIRED markets (tick_2245.txt 300 / 320 / 333)
COHORT = [f"0xc11-{i:02d}" for i in range(33)]

# the functions the plan names as NOT touched, hashed against the tip
# this lane was built on (82ebe77; lane 5's own pins carry the same
# figures for admission, _memo_terminal_book and _venue_market_ended)
UNTOUCHED = {
    "rules.admission": "a10630d6d3a3a62c",
    "_memo_terminal_book": "78bd691a2807a7a8",
    "_read_market": "37da36bcbef5c0a2",
    "_market": "35a418287f514c50",
    "_bbo": "28018a79134e1cae",
    "_venue_call": "26f074e24ff672d8",
    "_cand_cap": "2497ee1c2735433d",
    "_venue_market_ended": "1a02ebcd800fe3c0",
    "_memo_no_mark": "922ad18f477cd9c3",
    "_release_cand_memo": "ba0326c964d77b0f",
    "_cand_memo_skips": "58be2b3b77f762de",
}


def _sha(fn):
    return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]


def _market_reads(p, cid=None):
    """The markets-row reads the tick made (`_SQL_MARKET`, tagged
    ml-market), all or for one condition."""
    return [x for x in p.sent if "ml-market" in x[1] and (cid is None or x[2][0] == cid)]


def _cohort_pool(row, **kw):
    """test_e7_cand_memo's stamped pool over the 33-market cohort, every
    market's row `row` (a fresh dict each: the tick never writes it)."""
    p = _spool(conds=list(COHORT), **kw)
    for c in COHORT:
        p.markets[c] = dict(row)
    return p


def _memos():
    return {k: v for k, v in ml._terminal_until.items() if k[1] in COHORT or k[1] == CID}


@pytest.fixture(autouse=True)
def _clean_memos(monkeypatch):
    monkeypatch.setattr(ml, "_terminal_until", {})
    ml._cand_refusal_last.clear()
    yield


# ------------------------------------------------------------------ the rule

def test_c11_the_fact_is_admissions_clause_word_for_word_and_null_reads_as_not_live():
    """The expression is shared, not retyped: the helper's return is
    admission's `market_closed` clause with the fact names dropped."""
    asrc = inspect.getsource(rules.admission)
    clause = "if f.market_closed is not False or f.market_resolved is not False:"
    assert asrc.count(clause) == 1
    hsrc = inspect.getsource(rules.market_closed_fact)
    assert hsrc.count("return closed is not False or resolved is not False") == 1
    assert (clause.replace("f.market_closed", "closed").replace("f.market_resolved", "resolved")
            == "if closed is not False or resolved is not False:")
    assert "market_closed_fact" in rules.__all__
    # the truth table: live only on the two bools False
    assert rules.market_closed_fact(False, False) is False
    for closed, resolved in ((True, False), (False, True), (True, True), (None, False), (False, None),
                             (None, None), (0, False), (False, 0), ("false", False), (False, ""),
                             ([], False), (False, float("nan"))):
        assert rules.market_closed_fact(closed, resolved) is True, (closed, resolved)
    # and admission agrees on every shape the helper refuses (family / per_side admitted; test_e17's facts)
    def _facts(**over):
        return rules.AdmissionFacts(**{**dict(
            increases_ok=True, per_fill_usd=50.0, family="moneyline", per_side=False,
            market_closed=False, market_resolved=False, game_too_far_out=False,
            mapping_ok=True, edge_ok=True, cell_ok=True, legacy_row=False,
            slug_recent_copy=False, underdog_coholds=False, venue_net=0.0,
            kalshi_claimed=False, side_band_hit=False, snap_fresh=True, drift=0.0,
            books_live=0, opened_today=0, first_fill_ok=True), **over})
    for closed, resolved in ((True, False), (False, True), (None, False), (False, None), ("x", False)):
        assert rules.admission(_facts(market_closed=closed, market_resolved=resolved)) == "market_closed", (closed, resolved)
        assert rules.market_closed_fact(closed, resolved) is True
    assert rules.admission(_facts(market_closed=False, market_resolved=False)) is None


def test_c11_the_untouched_functions_the_ttl_and_the_terminal_states_are_the_tips():
    for name, want in UNTOUCHED.items():
        fn = rules.admission if name == "rules.admission" else getattr(ml, name)
        assert _sha(fn) == want, name
    assert ms.UNMAPPED_TTL_S == 900.0
    assert ms.STATE_TERMINAL == frozenset({"MARKET_STATE_EXPIRED", "MARKET_STATE_CLOSED", "MARKET_STATE_TERMINATED",
                                           "MARKET_STATE_MATCH_AND_CLOSE_AUCTION"})


# ---------------------------------------------------- the 22:44Z cohort

def test_c11_the_2244z_cohort_of_33_closed_rows_costs_no_venue_read_and_is_memoised_900_s():
    """tick_2245.txt 300 / 320 / 333: 33 candidates on EXPIRED markets.
    With their rows reading closed = true: `cand_market_closed_db` 33
    beside `market_closed` 33, a `market_closed` refusal row each, ZERO
    _bbo calls, no_mark / venue_halted 0, the 900 s memo on each, the
    markets row read once per candidate. Next tick: the memo skip, no
    row read, no new refusal row."""
    p = _cohort_pool(CLOSED)
    v = _Venue(state=EXPIRED)
    st = _tick(p, v)
    assert _bbos(v) == [] and "bbo" not in _kinds(v) and st["reads"] == 0
    assert _census(st, NEW_NAME) == 33 and _census(st, "market_closed") == 33
    assert st["short"]["timing"]["cand_closed_db"] == 33, "the readable copy (E6's block)"
    assert _census(st, "no_mark") == 0 and _census(st, "venue_halted") == 0 and _census(st, "market_unreadable") == 0
    assert _census(st, "cand_terminal_skipped") == 0 and _census(st, "cand_unread_capped") == 0
    assert st["venue_state"] is None, "no quote read carried a state"
    assert _memos() == {("rn1", c): NOW + ms.UNMAPPED_TTL_S for c in COHORT}
    assert all(m == NOW + 900.0 for m in _memos().values())
    rows = [r for r in p.cand_refusals if r["refusal"] == "market_closed"]
    assert sorted(r["condition_id"] for r in rows) == sorted(COHORT) and len(p.cand_refusals) == 33
    for r in rows:
        # the 054 row: the name, the slug, the long token; nothing the read would have set
        assert r["us_slug"] == SLUG and r["long_asset"] == M
        assert r["mark"] is None and r["ask"] is None and r["his_net"] is None and r["target"] is None
        assert r["his_px"] is None and r["band"] is None and r["cand_reads"] == 0
    assert len(_market_reads(p)) == 33 and all(len(_market_reads(p, c)) == 1 for c in COHORT), "the row read ONCE"
    assert not p.books and not _places(v) and not p.orders
    # the next tick inside the TTL: the memo skip, no row read, no new row
    p.sent.clear()
    p.cand_refusals.clear()
    v2 = _Venue(state=EXPIRED)
    st2 = _tick(p, v2, now=NOW + 30)
    assert _census(st2, "cand_terminal_skipped") == 33 and _census(st2, NEW_NAME) == 0
    assert _census(st2, "market_closed") == 0 and _bbos(v2) == [] and _market_reads(p) == []
    assert p.cand_refusals == [] and not p.books
    # past the TTL the row is read again, still closed: refused again by the same names
    p.sent.clear()
    v3 = _Venue(state=EXPIRED)
    st3 = _tick(p, v3, now=NOW + ms.UNMAPPED_TTL_S + 1)
    assert _census(st3, NEW_NAME) == 33 and _census(st3, "market_closed") == 33 and _bbos(v3) == []
    assert _memos() == {("rn1", c): NOW + ms.UNMAPPED_TTL_S + 1 + ms.UNMAPPED_TTL_S for c in COHORT}


def test_c11_the_same_33_with_live_rows_read_the_quote_as_before_and_d1_memoises_the_expired_read():
    """The rows `closed = false, resolved = false, resolved_prices NULL`
    (the resolution sweep not yet caught up: task 66): every candidate
    is quote-read as before -- 33 _bbo calls, venue_halted 33, no_mark
    33 (tick_2245 row 300's shape) -- and D1's memo lands on the
    EXPIRED read; `cand_market_closed_db` 0 says the row was NOT
    current. The row is still read once (handed to _read_market)."""
    p = _cohort_pool(LIVE)
    v = _Venue(state=EXPIRED)
    st = _tick(p, v)
    assert len(_bbos(v)) == 33 and st["reads"] == 33
    assert _census(st, "venue_halted") == 33 and _census(st, "no_mark") == 33
    assert _census(st, NEW_NAME) == 0 and _census(st, "market_closed") == 0 and _census(st, "market_unreadable") == 0
    assert st["short"]["timing"]["cand_closed_db"] == 0
    assert st["venue_state"] == EXPIRED
    assert _memos() == {("rn1", c): NOW + ms.UNMAPPED_TTL_S for c in COHORT}, "D1's memo on the EXPIRED read"
    assert len(_market_reads(p)) == 33 and all(len(_market_reads(p, c)) == 1 for c in COHORT), "never twice"
    rows = [r["refusal"] for r in p.cand_refusals]
    assert rows.count("no_mark") == 33 and "market_closed" not in rows
    assert not p.books and not _places(v)


def test_c11_a_live_row_on_an_open_market_opens_the_book_as_before_with_the_row_read_once():
    """The ordinary candidate (the fixture market OPEN, his 300 long):
    the pre-check passes, the quote is read, the book opens and places
    exactly as before; the markets row was read once, before the
    quote, and handed on."""
    p = _pool()
    v = _Venue()
    st = _tick(p, v)
    assert p.books and _places(v) and _census(st, NEW_NAME) == 0 and _census(st, "market_closed") == 0
    assert ("rn1", CID) not in ml._terminal_until
    # the candidate's one read, then the opened book's own step-M read
    # (_tick_book reads the row once, before the plan, as before): two
    # on the tick that opens, never a second for the candidate
    positions = [i for i, x in enumerate(p.sent) if "ml-market" in x[1] and x[2][0] == CID]
    opened = next(i for i, x in enumerate(p.sent) if "INSERT INTO mirror_books" in x[1])
    assert len([i for i in positions if i < opened]) == 1 and len(positions) == 2
    # a candidate refused AFTER the read (the side band: the ask 30c
    # over his 0.31) reads the row exactly once -- handed to _read_market
    ml._cand_refusal_last.clear()
    p2 = _pool()
    v2 = _Venue(bid=0.60, ask=0.62)
    st2 = _tick(p2, v2)
    assert _census(st2, "side_band") == 1 and len(_bbos(v2)) == 1 and not p2.books
    assert len(_market_reads(p2, CID)) == 1, "the row read once, never twice"
    assert _census(st2, NEW_NAME) == 0 and ("rn1", CID) not in ml._terminal_until
    # the row read sits BEFORE the quote read in the tick's own order
    # (the fake records the venue and the pool separately: pin the
    # source order instead)
    src = inspect.getsource(ml._tick_candidate)
    assert src.index("mk = await _market(t, cid)") < src.index("_read_market(t, w, cid, slug, la, oa, fills, market=mk)")


# ------------------------------------------------------------ fail closed

def test_c11_an_unreadable_or_absent_row_reads_the_quote_as_before_and_is_market_unreadable():
    """The row raising, or absent: the pre-check refuses nothing (None
    is not a fact), the quote is read as before, and the candidate is
    `market_unreadable` by the existing name -- no memo, no row of
    this lane's. _read_market makes its own read of the row on a
    candidate that arrives without one (its `market is None` branch,
    untouched), so the unreadable shape costs two DATABASE reads and
    the one venue read it always cost."""
    for shape in ("raises", "absent"):
        ml._cand_refusal_last.clear()          # one pair across the shapes: a fresh transition each
        p = _pool()
        if shape == "raises":
            p.raise_on.append(("ml-market", RuntimeError("blip")))
        else:
            del p.markets[CID]
        v = _Venue()
        st = _tick(p, v)
        assert len(_bbos(v)) == 1 and st["reads"] == 1, shape
        assert _census(st, "market_unreadable") == 1 and _census(st, "market_closed") == 0, shape
        assert _census(st, NEW_NAME) == 0 and ("rn1", CID) not in ml._terminal_until, shape
        assert not p.books and not _places(v) and not p.orders, shape
        assert len(_market_reads(p, CID)) == 2, shape
        assert [r["refusal"] for r in p.cand_refusals] == ["market_unreadable"], shape
    # a blip on the pre-check alone that clears by the second read is the live row: opened as before
    p = _pool()
    once = {"n": 0}
    orig = p._run

    def _blip(kind, sql, a):
        if "ml-market" in " ".join(sql.split()) and once["n"] == 0:
            once["n"] += 1
            p.sent.append((kind, " ".join(sql.split()), a))
            raise RuntimeError("blip")
        return orig(kind, sql, a)

    p._run = _blip
    v = _Venue()
    st = _tick(p, v)
    assert p.books and _places(v) and _census(st, "market_unreadable") == 0 and _census(st, NEW_NAME) == 0


def test_c11_a_null_closed_or_resolved_is_market_closed_here_as_admission_refuses_it_today():
    """NULL on either fact, or resolved alone, or both true: the
    pre-check refuses exactly the rows admission refuses `market_closed`
    -- and it did so AFTER the quote read before this lane
    (test_mirror_live_worker's candidate pin: a readable closed row is
    market_closed by the rules module)."""
    shapes = [
        {"closed": None, "resolved": False, "resolved_prices": None},
        {"closed": False, "resolved": None, "resolved_prices": None},
        {"closed": None, "resolved": None, "resolved_prices": None},
        {"closed": False, "resolved": True, "resolved_prices": [0, 1]},
        {"closed": True, "resolved": True, "resolved_prices": [0, 1]},
        {"closed": True, "resolved": False, "resolved_prices": None},
        {"closed": "true", "resolved": False, "resolved_prices": None},
    ]
    for mk in shapes:
        ml._terminal_until.clear()
        ml._cand_refusal_last.clear()
        p = _pool()
        p.markets[CID] = dict(mk)
        v = _Venue()
        st = _tick(p, v)
        assert _bbos(v) == [] and st["reads"] == 0, mk
        assert _census(st, "market_closed") == 1 and _census(st, NEW_NAME) == 1, mk
        assert _census(st, "market_unreadable") == 0 and _census(st, "no_mark") == 0, mk
        assert ml._terminal_until == {("rn1", CID): NOW + ms.UNMAPPED_TTL_S}, mk
        assert [r["refusal"] for r in p.cand_refusals] == ["market_closed"], mk
        assert not p.books and not _places(v), mk
    # the one live shape reads and opens
    ml._terminal_until.clear()
    p = _pool()
    p.markets[CID] = dict(LIVE)
    v = _Venue()
    st = _tick(p, v)
    # the candidate's read, then the opened book's own (the tick that opens plans it)
    assert len(_bbos(v)) >= 1 and p.books and _census(st, NEW_NAME) == 0 and _census(st, "market_closed") == 0


def test_c11_a_wrongly_closed_row_waits_its_memo_and_his_newer_fill_does_not_release_it():
    """A row that reads closed while the venue is OPEN: refused, the
    900 s memo written; his newer fill (a wake) does not release D1's
    first-read memo (the plan: lane 13 adds the release on the
    confirming read only) -- the shape test_d1_review pins for the
    venue's own EXPIRED read, here for the row's word."""
    p = _pool()
    p.markets[CID] = dict(CLOSED)
    st = _tick(p, _Venue())
    assert _census(st, NEW_NAME) == 1 and ml._terminal_until == {("rn1", CID): NOW + 900.0}
    p.markets[CID] = dict(LIVE)                      # the row corrected
    ml._WOKEN.add(CID)                               # and he traded it again
    v = _Venue()
    st2 = _tick(p, v, now=NOW + 60)
    assert st2["woken"] == [CID] and _census(st2, "cand_terminal_skipped") == 1
    assert _bbos(v) == [] and not p.books and _census(st2, NEW_NAME) == 0
    v3 = _Venue()
    st3 = _tick(p, v3, now=NOW + ms.UNMAPPED_TTL_S + 1)
    assert _census(st3, "cand_terminal_skipped") == 0 and len(_bbos(v3)) >= 1 and p.books, "read again after the TTL"
    assert _census(st3, NEW_NAME) == 0 and _census(st3, "market_closed") == 0


def test_c11_a_market_with_a_book_never_reaches_the_pre_check():
    """A book on the closed market takes _tick_book's closing branch
    (the book's own `market_closed`, no candidate memo); the candidate
    path names it `book_seen` before any row read."""
    p = _pool()
    b = p.add_book(ledger=300)
    p.markets[CID] = dict(CLOSED)
    v = _Venue(held={SLUG: 300})
    st = _tick(p, v)
    assert b["state"] == "closing" and _census(st, "market_closed") == 1
    assert _census(st, NEW_NAME) == 0 and ("rn1", CID) not in ml._terminal_until
    assert "bbo" not in _kinds(v)
    # the candidate function itself on a market with a book
    ml._current_stats = ml._new_stats()
    try:
        p2 = _pool()
        p2.markets[CID] = dict(CLOSED)
        t = ml._Tick(pool=p2, pmus=_Venue(), http=_Http(), now=NOW, stats=ml._current_stats)
        t.books_seen.add(("rn1", CID))
        assert _run(ml._tick_candidate(t, "rn1", CID)) == "book_seen"
        assert _market_reads(p2) == [] and ml._terminal_until == {}
        assert ml._current_stats["census"][NEW_NAME] == 0
    finally:
        ml._current_stats = None


def test_c11_a_refusal_before_the_read_writes_no_reopen_record_on_a_turned_market():
    """FILL lane 5's own rule: only a refusal WITH a verdict (`mark` on
    the context) is a reopen refused. The pre-check refuses before the
    read, so a turned market whose row already reads closed writes the
    054 row and nothing on the closed book's plan -- and costs no turn
    read either."""
    p = _pool()
    closed = p.add_book(ledger=0, state="closed", standing_status="cashed_out",
                        last_plan={"sign_flip": True, "net": -300.0, "close": "cashed_out",
                                   "turn": {"from": ml.ORDER_INTENT, "to": ml.ORDER_INTENT_SHORT,
                                            "his_net": -300.0, "at": NOW - 26.0}})
    p.markets[CID] = dict(CLOSED)
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, NEW_NAME) == 1 and _census(st, "market_closed") == 1 and _census(st, "reopen_refused") == 0
    assert not [x for x in p.sent if "ml-book-turn" in x[1]] and not [x for x in p.sent if "ml-book-reopen-refused" in x[1]]
    assert "reopen_refused" not in closed["last_plan"] and closed["state"] == "closed"
    assert [r["refusal"] for r in p.cand_refusals] == ["market_closed"] and _bbos(v) == []


def test_c11_the_fast_paths_wake_takes_the_pre_check_too():
    """His fill wakes a market with no book whose row reads closed:
    the fast tick walks _walk_candidate -> _tick_candidate, so the
    pre-check refuses it with no venue call and the same memo."""
    p = _pool()
    p.markets[CID] = dict(CLOSED)
    st0 = _tick(p, _Venue())           # the full tick's walk (the fast tick reads its positions)
    assert _census(st0, NEW_NAME) == 1
    ml._terminal_until.clear()
    ml._cand_refusal_last.clear()
    p.cand_refusals.clear()
    v = _Venue()
    st = _fast(p, v)
    assert st["fast"]["on"] is True and st["fast"]["placed"] == 0 and _bbos(v) == [] and not p.books
    assert _census(st, "fast_tick") == 1 and _census(st, "fast_tick_skipped") == 0
    assert _census(st, NEW_NAME) == 1 and _census(st, "market_closed") == 1
    assert st["short"]["timing"]["cand_closed_db"] == 1
    assert ml._terminal_until == {("rn1", CID): NOW + 1 + ms.UNMAPPED_TTL_S}
    assert [r["refusal"] for r in p.cand_refusals] == ["market_closed"]


def test_c11_an_abandoning_tick_and_the_caps_are_as_before():
    """The pre-check sits after the caps' own checks in the walk: a
    tick with no positions walk still refuses by the row without a
    venue read (the positions walk is not what the row needs), and the
    memo skip and the caps stand untouched (hashed above)."""
    p = _pool(snap=None)
    p.markets[CID] = dict(CLOSED)
    v = _Venue()
    st = _tick(p, v)
    assert _census(st, "market_closed") >= 1 and _census(st, NEW_NAME) == 1 and _bbos(v) == []
    assert not p.books


# ------------------------------------------- the names, the source, the docs

def test_c11_the_census_place_the_emit_site_the_source_shape_and_no_knob():
    keys = ml.CENSUS_KEYS
    assert keys.count(NEW_NAME) == 1 and len(set(keys)) == len(keys)
    # one name, before E19's key, after FILL lane 5's three; E21 (FILL lane 10) placed its six -- FILL lane 16 (one name, turn_woke_fast) landed first, so every index here moved by one more
    # fast_* names between this one and the key (-14 -> -20)
    assert keys[-21] == NEW_NAME and keys[-13] == "drift_smaller_open" and keys[-12] == "registered_no_increase"
    # E22 (FILL lane 22) landed ahead of this lane and sits between FILL lane 5's three and this name (-17:-14 -> -24:-20)
    assert keys[-25:-21] == ("lost_fill_adopted", "lost_fill_unread", "lost_fill_unexplained", "lost_fill_ambiguous")
    assert keys[-28:-25] == ("he_holds", "he_holds_unread", "reopen_refused")
    assert keys[-20] == "turn_woke_fast"
    assert keys[-1] == "cand_terminal_skipped" and keys[-8:-4] == ("fast_tick", "fast_tick_placed", "fast_tick_skipped", "fast_tick_failed")
    assert ml._new_stats()["census"][NEW_NAME] == 0
    assert NEW_NAME not in ml._INTEG_CENSUS_KEYS and "market_closed" not in ml._CAND_NOT_RECORDED
    whole = inspect.getsource(ml)
    src = inspect.getsource(ml._tick_candidate)
    # the one emit site, in _tick_candidate, beside the existing name's
    assert whole.count(f'_mirror_stop("{NEW_NAME}", w)') == 1 and f'_mirror_stop("{NEW_NAME}", w)' in src
    assert src.count('_mirror_stop("market_closed", w)') == 1
    # the source order: after book_seen / the game-full memo / the sibling read, before _read_market
    i_seen = src.index("in t.books_seen")
    i_full = src.index('return "cand_game_full_skipped"')
    i_sib = src.index("oa = await t.pool.fetchval(_SQL_SIBLING_TOKEN, cid, la)")
    i_mk = src.index("mk = await _market(t, cid)")
    i_fact = src.index('rules.market_closed_fact(mk["closed"], mk["resolved"])')
    i_stop = src.index(f'_mirror_stop("{NEW_NAME}", w)')
    i_memo = src.index("_terminal_until[(w, cid)] = t.now + ms.UNMAPPED_TTL_S")
    i_ctx = src.index('d.update(market_closed=mk["closed"], market_resolved=mk["resolved"])')
    i_ret = src.index('return "market_closed"')
    i_read = src.index("r = await _read_market(t, w, cid, slug, la, oa, fills, market=mk)")
    assert i_seen < i_full < i_sib < i_mk < i_fact < i_stop < i_memo < i_ctx < i_ret < i_read
    assert src.count("mk = await _market(t, cid)") == 1 and src.count("_read_market(") == 1
    assert 'if mk is not None and rules.market_closed_fact(mk["closed"], mk["resolved"]):' in src
    # the memo write is D1's, byte for byte, twice in the candidate alone (the venue's word and the row's)
    assert whole.count("_terminal_until[(w, cid)] = t.now + ms.UNMAPPED_TTL_S") == 2
    assert src.count("_terminal_until[(w, cid)] = t.now + ms.UNMAPPED_TTL_S") == 2
    assert "_terminal_until" not in inspect.getsource(ml._read_market) and "_terminal_until" not in inspect.getsource(ml._market)
    # the expression is the rules module's, never retyped in the worker
    assert 'mk["closed"] is not False' not in src and 'mk["resolved"] is not False' not in src
    # no knob, no rail, no migration, no decision word
    assert "MIRROR_CAND_TERMINAL" not in whole and "MIRROR_CAND_MARKET" not in whole and 'capped_env("MIRROR_CAND' not in whole
    assert "cand_market_closed_db" not in inspect.getsource(rules)
    # no migration from this lane: 061 is lane 9's (landed after), nothing sorts past it
    migs = sorted(p.name for p in pathlib.Path(ml.__file__).resolve().parents[3].joinpath("backend", "migrations").glob("*.sql"))
    assert migs[-1] == "061_fill_answers_cause_orders_fast.sql" and "cand" not in migs[-1]
    assert "cand_market_closed_db" not in (ROOT / "backend" / "migrations" / "059_mirror_orders_send_record.sql").read_text()


def test_c11_the_rows_facts_ride_the_walks_context_and_no_verdict_does():
    """The pre-check's refusal carries the row's own word on the ctx
    (the plan: `d.update(market_closed=, market_resolved=)`) and NO
    verdict -- `mark` / `ask` / `his_net` / `target` never reach it --
    which is exactly what lane 5's `_note_reopen_refused` keys on
    ("mark" not in d: a refusal before the read writes nothing)."""
    ml._current_stats = ml._new_stats()
    try:
        for row in (CLOSED, {"closed": False, "resolved": True, "resolved_prices": [0, 1]},
                    {"closed": None, "resolved": False, "resolved_prices": None}):
            ml._terminal_until.clear()
            p = _pool()
            p.markets[CID] = dict(row)
            t = ml._Tick(pool=p, pmus=_Venue(), http=_Http(), now=NOW, stats=ml._current_stats)
            d: dict = {}
            assert _run(ml._tick_candidate(t, "rn1", CID, ctx=d)) == "market_closed", row
            assert d["market_closed"] is row["closed"] and d["market_resolved"] is row["resolved"], row
            assert "mark" not in d and "ask" not in d and "his_net" not in d and "target" not in d, row
            assert d["slug"] == SLUG and d["long_asset"] == M, row
            assert ml._terminal_until == {("rn1", CID): NOW + 900.0}, row
    finally:
        ml._current_stats = None
        ml._terminal_until.clear()


def test_c11_every_name_is_emitted_here():
    """The lane's one name, driven (the worker file's coverage read
    imports this)."""
    test_c11_the_2244z_cohort_of_33_closed_rows_costs_no_venue_read_and_is_memoised_900_s()


def test_c11_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## \d+\. The candidate's terminal pre-check \(2026-09-09, FILL lane 11\)", doc, re.M), \
        "the lane 11 section header"
    for k in (NEW_NAME, "market_closed_fact", "market_closed", "market_unreadable", "UNMAPPED_TTL_S",
              "_terminal_until", "book_seen", "test_fill_c11_cand_terminal_db.py", "16.2", "tick_2245"):
        assert k in doc, k
