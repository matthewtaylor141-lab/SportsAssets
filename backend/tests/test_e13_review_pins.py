"""E13 review pins (2026-09-08): the venue close attacked on its ordering,
its confirmation clock, its interplay with the frozen book, and the
sweep's cursor.

The F1 / F1b pins are INVERTED (the fold landed: a frozen book under a
reason the venue may hold shares through, or whose last plan's venue
reading is not flat, is never closed on the venue's word); the F2 pin
pins the CLOB pass's own rotation window as it stands, accepted and said
in sweep_resolutions' docstring. The rest are mutant-killers
the builder's file does not hold: the confirmation at EXACTLY the TTL,
the flat tolerance's own edge, the desk's rank over the alphabet, the
restart with the E6 memo gone, the gamma row unreadable, a confirmed
state never overwritten by a later read, and the CLOB fallback's own
rotation window.

Driven on the real planner through the worker file's fakes; the sweep
against a scratch Postgres where one answers (skips visibly otherwise).
"""
import inspect

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.analytics import resolution
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from tests.test_e13_venue_close import (
    CID, CLOSED, EXPIRED, KEY, NOW, SLUG, TTL, _bbos, _drop, _e5_pool, _frozen_long, _scratch, _seed, _seed_world,
    _state_writes,
)
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails (_armed)
    BUY, _armed, _census, _places, _pool, _run, _tick, _Venue,
)


# ------------------------------------------------- finding pins (the defect as it stands)

def test_review_f1_book_77s_shape_a_frozen_disagree_book_with_ledger_0_is_closed_while_the_venue_holds_1128():
    """FINDING F1 (HIGH), FOLDED -- the assertions inverted. Before the
    fold `_venue_market_ended` read `ledger_net` alone: a frozen
    `venue_ledger_disagree` book whose LEDGER is 0 while the VENUE holds
    1,128 (book 77's shape: the register's shares, or a lost placement's
    fill the ledger never booked) was 'flat' to it, so the confirmed
    venue state closed the book 'cancelled' before any read -- its
    standing row released, the E5 frozen exit that sized on the venue's
    own position gone, the venue's 1,128 settling into no row of ours.
    Now a frozen book under a reason the venue may hold shares through
    (_VENUE_MAY_HOLD_REASONS) is never closed on the venue's word: the
    book stays frozen, the venue is read, no state is written, the
    confirmation stands (the settle closes it, as a held book)."""
    p = _e5_pool(registered={SLUG: 1128.0})
    b = _frozen_long(p, ledger=0, reason="venue_ledger_disagree", lost=0)
    row = p.rows[b["standing_row_id"]]
    _seed(NOW - 2000, confirmed=EXPIRED)
    v = _Venue(state=EXPIRED, held={SLUG: 1128})
    st = _tick(p, v)
    assert b["state"] == "frozen" and b["frozen_reason"] == "venue_ledger_disagree" and row["status"] == "filled"
    assert _census(st, "venue_market_ended") == 0 and _census(st, "closed_cancelled") == 0
    assert _bbos(v), "the venue's position is read, the book never closed ahead of it"
    assert not _state_writes(p, b["id"])
    assert ml._terminal_book_confirmed == {KEY: EXPIRED} and ml._venue_market_ended(b) is None


def test_review_f1b_a_placement_lost_book_with_ledger_0_and_a_lost_row_is_closed_over_the_lost_fill():
    """FINDING F1 (HIGH), the other frozen shape, FOLDED -- the
    assertions inverted: ledger 0, frozen placement_lost with the BUY of
    300 whose response was lost still in state 'lost' (E5's row), the
    venue holding the 300 it filled. Before the fold the venue's word
    closed the book 'cancelled' over the lost fill. Now the book stays
    frozen (placement_lost is in _VENUE_MAY_HOLD_REASONS), the lost row
    stands on a FROZEN book for E5's attribution, nothing is closed."""
    p = _e5_pool()
    b = _frozen_long(p, ledger=0, reason="placement_lost", lost=300)
    _seed(NOW - 2000, confirmed=EXPIRED)
    v = _Venue(state=EXPIRED, held={SLUG: 300})
    st = _tick(p, v)
    assert b["state"] == "frozen" and p.rows[b["standing_row_id"]]["status"] == "filled"
    assert _census(st, "venue_market_ended") == 0 and _census(st, "closed_cancelled") == 0
    assert not _state_writes(p, b["id"])
    lost = [o for o in p.orders.values() if o["state"] == "lost"]
    assert len(lost) == 1, "the lost row stands on a frozen book, the venue's 300 still attributable"


def test_review_f2_the_clob_fallback_rotates_its_own_window_so_what_gamma_asked_and_missed_is_not_re_asked(monkeypatch):
    """FINDING F2 (LOW), PINNED AS IT STANDS. sweep_resolutions reads
    the function twice per cycle -- the gamma pass, then the CLOB pass
    'for whatever Gamma didn't cover' -- and each call ADVANCES the
    cursor, so the CLOB pass's rotation window is the NEXT one, never
    the one gamma just asked: a rotation id gamma asked this cycle and
    did not cover waits for a later cycle's CLOB window to land on it.
    The desk's and the newest are asked by both, as the brief wants."""
    async def _go():
        admin, conn, name = await _scratch()
        try:
            await _seed_world(conn)

            async def _gp():
                return conn
            monkeypatch.setattr(resolution, "get_pool", _gp)
            ids = resolution.unresolved_traded_condition_ids
            first = await ids(limit=6)          # the gamma pass's shape: rotation o1
            second = await ids(limit=6)         # the CLOB pass's shape, right after
            assert first == ["a_book", "b_row", "n1", "n2", "n3", "o1"]
            assert second == ["a_book", "b_row", "n1", "n2", "n3", "o2"]
            assert "o1" not in second, "gamma's uncovered rotation id is not the CLOB's this cycle"
        finally:
            await _drop(admin, conn, name)
    _run(_go())


# --------------------------------------------------------- mutant killers

def test_review_m1_the_confirmation_lands_at_exactly_the_ttl_not_a_second_later():
    """Kills `>=` -> `>` in _memo_terminal_book: the re-read exactly
    UNMAPPED_TTL_S after the first is the memo's own re-read."""
    p = _pool()
    b = p.add_book(ledger=0, gross_buy=0.0)
    _tick(p, _Venue(state=EXPIRED))
    assert ml._terminal_book_seen == {KEY: NOW}
    v = _Venue(state=EXPIRED)
    _tick(p, v, now=NOW + TTL)
    assert _bbos(v), "the memo's TTL has run at exactly now + TTL: read"
    assert ml._terminal_book_confirmed == {KEY: EXPIRED} and b["state"] == "live"
    # one second short of it (the memo dropped by hand): nothing confirms
    p2 = _pool()
    p2.add_book(ledger=0, gross_buy=0.0)
    _seed(NOW - TTL + 1)
    _tick(p2, _Venue(state=EXPIRED))
    assert ml._terminal_book_confirmed == {}


def test_review_m2_the_flat_tolerance_is_the_rules_own_edge_inclusive_on_held():
    """Kills `>=` -> `>` in _venue_market_ended: a ledger of exactly
    FLAT_TOL_SHARES is HELD to the rules, so it is held here."""
    f = ml._venue_market_ended
    ml._terminal_book_confirmed[KEY] = EXPIRED
    assert f({"whale": "rn1", "condition_id": CID, "ledger_net": rules.FLAT_TOL_SHARES}) is None
    assert f({"whale": "rn1", "condition_id": CID, "ledger_net": -rules.FLAT_TOL_SHARES}) is None
    assert f({"whale": "rn1", "condition_id": CID, "ledger_net": rules.FLAT_TOL_SHARES / 2}) == EXPIRED
    assert f({"whale": "rn1", "condition_id": CID, "ledger_net": "0"}) is None, "a string ledger is not a number"
    assert f({"whale": "rn1", "condition_id": CID, "ledger_net": True}) is None, "a bool is not a number"


def test_review_m3_a_confirmed_state_is_never_overwritten_by_a_later_terminal_read():
    """Kills the `key not in _terminal_book_confirmed` guard: the
    plan's `venue_terminal` names the state the CONFIRMING read carried."""
    # a held book (step M ignores the confirmation) whose re-read says CLOSED after EXPIRED confirmed
    p = _pool()
    b = p.add_book(ledger=300)
    _seed(NOW - 2000, confirmed=EXPIRED)
    st = _tick(p, _Venue(state=CLOSED, held={SLUG: 300}))
    assert b["state"] == "live" and _census(st, "venue_market_ended") == 0
    assert ml._terminal_book_confirmed == {KEY: EXPIRED} and ml._terminal_book_seen == {KEY: NOW - 2000.0}
    assert ml._terminal_book_state == {KEY: CLOSED}, "the W1 memo carries the latest read, the confirmation the first"
    # and on a flat book the plan written on the closing tick names the confirming read's state
    p2 = _pool()
    b2 = p2.add_book(ledger=0, gross_buy=0.0)
    p2.add_order(b2, side=BUY, wire=0.30, qty=300)
    _seed(NOW - 2000, confirmed=EXPIRED, until=NOW + 500, state=CLOSED)
    v = _Venue(state=CLOSED, cancel_ok=False)          # the cancel refused: the order stands, the book 'closing'
    v.rest("oid-1", "BUY", 0.30, 300)
    st2 = _tick(p2, v)
    assert b2["state"] == "closing" and _census(st2, "venue_market_ended") == 1
    assert b2["last_plan"]["venue_terminal"] == EXPIRED


def test_review_m4_the_desks_rank_beats_the_alphabet_a_live_rows_condition_after_the_books(monkeypatch):
    """Kills `ORDER BY c.rank, c.condition_id` -> `ORDER BY c.condition_id`
    (the builder's world has the book's id before the row's by the
    alphabet too): a live row's condition named '0_row' still comes
    after the book's 'a_book'."""
    async def _go():
        admin, conn, name = await _scratch()
        try:
            await _seed_world(conn)
            await conn.execute("INSERT INTO live_orders (condition_id, status) VALUES ('0_row', 'exiting'), "
                               "('0_sub', 'submitting')")

            async def _gp():
                return conn
            monkeypatch.setattr(resolution, "get_pool", _gp)
            got = await resolution.unresolved_traded_condition_ids(limit=0)
            assert got == ["a_book", "0_row", "0_sub", "b_row"], got
        finally:
            await _drop(admin, conn, name)
    _run(_go())


def test_review_m5_a_restart_that_lost_the_e6_memo_but_kept_the_confirmation_still_needs_the_pair():
    """Kills a load that drops the pending entry when the E6 memo has
    no `until` for it (and pins the rule as documented: the pair is two
    reads a TTL apart, one of them before the restart). The E6 memo's
    snapshot drops an `until` that has passed; the confirmation's keeps
    its `seen_at`."""
    ml._terminal_memo_loaded = False
    p = _pool()
    b = p.add_book(ledger=0, gross_buy=0.0)
    p.state[ml._STATE_TERMINAL_MEMO] = {"cand": [], "book": [], "at": ml._iso(NOW - 1000)}
    p.state[ml._STATE_TERMINAL_CONFIRM] = [["rn1", CID, NOW - 1000, None]]
    v = _Venue(state=EXPIRED)
    _tick(p, v)
    assert _bbos(v) and ml._terminal_book_seen == {KEY: NOW - 1000.0}
    assert ml._terminal_book_confirmed == {KEY: EXPIRED} and b["state"] == "live"
    st = _tick(p, _Venue(state=EXPIRED), now=NOW + 30)
    assert b["state"] == "closed" and _census(st, "venue_market_ended") == 1
    # the same restart with the pending read 100 s old: the re-read is the memo's, not the pair's
    ml._terminal_memo_loaded = False
    ml._terminal_book_seen.clear()
    ml._terminal_book_confirmed.clear()
    p2 = _pool()
    b2 = p2.add_book(ledger=0, gross_buy=0.0)
    p2.state[ml._STATE_TERMINAL_CONFIRM] = [["rn1", CID, NOW - 100, None]]
    _tick(p2, _Venue(state=EXPIRED))
    assert ml._terminal_book_confirmed == {} and b2["state"] == "live"
    _tick(p2, _Venue(state=EXPIRED), now=NOW + 30)
    assert b2["state"] == "live", "never on one read after a restart"


def test_review_m6_the_gamma_row_unreadable_the_venues_confirmed_word_still_ends_a_flat_book():
    """Kills a step M that put the venue close after the
    `market_unreadable` hold: the row absent (mk None), the plan says
    market_live None, the episode close is handed False."""
    p = _pool()
    b = p.add_book(ledger=0, gross_buy=0.0)
    p.markets.pop(CID, None)
    _seed(NOW - 2000, confirmed=EXPIRED)
    st = _tick(p, _Venue(state=EXPIRED))
    assert b["state"] == "closed" and _census(st, "venue_market_ended") == 1
    assert _census(st, "market_unreadable") == 0
    assert [a[1:] for a in _state_writes(p, b["id"])] == [("closing", "venue_market_ended"),
                                                          ("closed", "closed_cancelled")]


def test_review_m7_the_closing_verdict_is_the_rules_and_a_held_book_never_reaches_it():
    """Kills `_venue_market_ended` reading `_terminal_book_state` (the
    W1 memo, ONE read) instead of the confirmed dict: a W1 memo standing
    alone on a flat book closes nothing."""
    p = _pool()
    b = p.add_book(ledger=0, gross_buy=0.0)
    ml._terminal_book_until[KEY] = NOW + 500
    ml._terminal_book_state[KEY] = EXPIRED
    st = _tick(p, _Venue(state=EXPIRED))
    assert b["state"] == "live" and b["last_reason"] == "no_mark"
    assert _census(st, "venue_market_ended") == 0 and _census(st, "book_terminal_skipped") == 1
    assert ml._venue_market_ended(b) is None
    # and the source: the verdict reads the confirmed dict, never the W1 state dict
    src = inspect.getsource(ml._venue_market_ended)
    assert "_terminal_book_confirmed.get(" in src and "_terminal_book_state" not in src
    assert "ms.STATE_TERMINAL" in src


def test_review_m8_the_persisted_confirmation_is_forgotten_on_the_settle_path_too():
    """Kills the `_forget_terminal_confirm` call in _close_settled: a
    book the venue settled leaves no entry behind, and the next write
    of the confirm key carries none of it."""
    p = _pool()
    b = p.add_book(ledger=300)
    _seed(NOW - 2000, confirmed=EXPIRED, until=NOW + 500)
    p.rows[b["standing_row_id"]]["status"] = "settled"
    p.rows[b["standing_row_id"]]["pnl"] = 12.0
    _tick(p, _Venue(held={SLUG: 300}))
    assert b["state"] == "closed"
    assert KEY not in ml._terminal_book_seen and KEY not in ml._terminal_book_confirmed
    assert ml._terminal_confirm_snapshot(NOW + 1) == []


def test_review_m9_the_sweeps_wrap_never_re_asks_this_calls_own_ids_and_an_empty_wrap_resets_the_cursor(monkeypatch):
    """Kills a wrap that passes the cursor (not '') or forgets the
    exclusion list: with the whole remainder smaller than the room the
    wrap returns nothing and the cursor is written back to the start."""
    async def _go():
        admin, conn, name = await _scratch()
        try:
            await _seed_world(conn)

            async def _gp():
                return conn
            monkeypatch.setattr(resolution, "get_pool", _gp)
            ids = resolution.unresolved_traded_condition_ids
            await ids(limit=8)                  # o1, o2, o3: cursor o3
            got = await ids(limit=100)          # o4, o5, o6, z_closed, then the wrap: o1, o2, o3 -- each once
            assert got == ["a_book", "b_row", "n1", "n2", "n3", "o4", "o5", "o6", "z_closed", "o1", "o2", "o3"]
            assert len(got) == len(set(got))
            raw = await conn.fetchval("SELECT value FROM ingestion_state WHERE key = $1", resolution.SWEEP_CURSOR_KEY)
            assert resolution._cursor_of(raw) == "o3"
            # the whole remainder inside one call from the start: the wrap is empty, the cursor the start
            await conn.execute("DELETE FROM ingestion_state WHERE key = $1", resolution.SWEEP_CURSOR_KEY)
            got = await ids(limit=100)
            assert got[5:] == ["o1", "o2", "o3", "o4", "o5", "o6", "z_closed"]
            raw = await conn.fetchval("SELECT value FROM ingestion_state WHERE key = $1", resolution.SWEEP_CURSOR_KEY)
            assert raw is None, "nothing moved: nothing written"
        finally:
            await _drop(admin, conn, name)
    _run(_go())


def test_review_m10_the_rails_by_source_no_knob_no_env_no_column_the_settle_untouched():
    src = inspect.getsource(ml)
    e13 = "".join(inspect.getsource(f) for f in (ml._memo_terminal_book, ml._venue_market_ended,
                                                 ml._forget_terminal_confirm, ml._terminal_confirm_snapshot,
                                                 ml._load_terminal_memo, ml._persist_terminal_memo))
    assert "os.environ" not in e13 and "capped_env" not in e13 and "getenv" not in e13
    assert "ALTER TABLE" not in e13 and "_settle_pmus_from_venue" not in e13
    rsrc = inspect.getsource(resolution)
    assert "os.environ" not in rsrc and "getenv" not in rsrc and "CREATE TABLE" not in rsrc
    assert ms.UNMAPPED_TTL_S == 900.0 and "UNMAPPED_TTL_S" in inspect.getsource(ml._memo_terminal_book)
    # the venue close never names a held book: the verdict is None before the memo is even read
    vsrc = inspect.getsource(ml._venue_market_ended)
    assert vsrc.index("FLAT_TOL_SHARES") < vsrc.index("_terminal_book_confirmed")
    assert src.count('"venue_market_ended"') >= 2


# ------------------------------------------ fold re-review pins (2026-09-08, the F1 fold attacked)

def test_fold_review_g1_the_frozen_gate_reads_the_plan_as_the_pool_serves_it_json_text():
    """FINDING G1 (HIGH), FOLDED (the gate decodes the plan with _jsonish). The pool sets no jsonb
    codec (db.py: create_pool with no init), so `mirror_books.last_plan`
    reaches the tick as JSON TEXT -- every other reader of the plan in
    the worker goes through `_jsonish(book.get("last_plan"))`. The F1
    fold's frozen gate reads it raw and asks `isinstance(lp, dict)`: on
    the real row the plan is a str, the venue reading None, the verdict
    None -- so NO frozen book ever closes on the venue's word in
    production, 455's shape included (the fake pool json.loads the
    plan, so the builder's end-to-end sees a dict and closes). Fails
    closed, no money moves; the brief's target shape is inert.
    FOLD (one line): `lp = _jsonish(book.get("last_plan"))`; then the
    text plan below reads EXPIRED and the source pin flips."""
    import json
    ml._terminal_book_confirmed[KEY] = EXPIRED
    text = '{"kind": "no_plan", "venue": 0, "at": 1.0, "venue_terminal": "MARKET_STATE_EXPIRED"}'
    b = {"whale": "rn1", "condition_id": CID, "ledger_net": 0, "state": "frozen", "frozen_reason": "cancel_pending",
         "last_plan": text}
    assert ml._venue_market_ended({**b, "last_plan": json.loads(text)}) == EXPIRED, "the fake's shape closes"
    assert ml._venue_market_ended(b) == EXPIRED, "FOLDED: the text plan is decoded (was None as it stood)"
    src = inspect.getsource(ml._venue_market_ended)
    assert '_jsonish(book.get("last_plan"))' in src, "FOLDED: the gate decodes the plan as the other readers do"
    # every other reader of the plan in the worker parses the text first
    wsrc = inspect.getsource(ml)
    assert wsrc.count('_jsonish(book.get("last_plan"))') >= 7
    assert 'b.last_plan' in ml._SQL_BOOK_COLS_056 and "set_type_codec" not in wsrc
    # and once folded a held text reading still holds, junk text is no reading
    for held in ('{"venue": 1128}', '{"venue": -300.0}', '{"venue": "0"}', '{"kind": "no_plan"}', 'junk', ''):
        assert ml._venue_market_ended({**b, "last_plan": held}) is None, held


def test_fold_review_g1b_the_pool_serves_a_jsonb_column_as_text_real_pg():
    """The why of G1 on the real driver: a jsonb value fetched through
    asyncpg with no codec set is a str, and _jsonish reads it."""
    import pytest
    from tests.test_e12_flow_only import DSN_BASE

    async def _go():
        asyncpg = pytest.importorskip("asyncpg")
        try:
            conn = await asyncpg.connect(DSN_BASE, timeout=4)
        except Exception:  # noqa: BLE001 — no local PG: skip, never fake
            pytest.skip("no local postgres for the G1 pin")
        try:
            v = await conn.fetchval("""SELECT '{"kind": "no_plan", "venue": 0}'::jsonb""")
            assert isinstance(v, str), type(v)
            assert ml._jsonish(v) == {"kind": "no_plan", "venue": 0}
            row = await conn.fetchrow("""SELECT '{"venue": 0}'::jsonb AS last_plan, 0 AS ledger_net""")
            assert isinstance(row["last_plan"], str)
        finally:
            await conn.close()
    _run(_go())


def test_fold_review_g2_every_reason_of_the_set_by_its_own_name_holds_a_frozen_book_over_a_flat_plan():
    """Kills the set minus any one name (five mutants the builder's file
    let live: its parametrize derives from the set, so a dropped name
    drops its own case; the F1 / F1b pins run with no plan, so the
    plan gate holds them whatever the set says). The plan's venue
    reading is FLAT here -- stale, written before the venue's shares
    appeared -- so the reason alone holds the book."""
    ml._terminal_book_confirmed[KEY] = EXPIRED
    names = ["venue_ledger_disagree", "placement_lost", "lost_ambiguous", "order_lost", "wrong_sign_trip",
             "wrong_sign_hold"]    # E20
    assert sorted(ml._VENUE_MAY_HOLD_REASONS) == sorted(names) and isinstance(ml._VENUE_MAY_HOLD_REASONS, frozenset)
    base = {"whale": "rn1", "condition_id": CID, "ledger_net": 0, "state": "frozen", "last_plan": {"venue": 0.0}}
    for reason in names:
        assert ml._venue_market_ended({**base, "frozen_reason": reason}) is None, reason
        assert ml._venue_market_ended({**base, "frozen_reason": reason, "last_plan": {"venue": 0}}) is None, reason
    assert ml._venue_market_ended({**base, "frozen_reason": "cancel_pending"}) == EXPIRED
    # each name is a reason the worker freezes under
    wsrc = inspect.getsource(ml)
    for reason in names:
        assert f'"{reason}"' in wsrc, reason
    # end to end, 77's shape and the lost-fill shape with a STALE flat plan reading: still frozen
    for reason, held, lost in (("venue_ledger_disagree", 1128, 0), ("placement_lost", 300, 300),
                               ("lost_ambiguous", 300, 0), ("order_lost", 300, 0), ("wrong_sign_trip", 300, 0),
                               ("wrong_sign_hold", 300, 0)):
        ml._terminal_book_seen.clear()
        ml._terminal_book_confirmed.clear()
        ml._terminal_book_until.clear()
        ml._terminal_book_state.clear()
        p = _e5_pool(registered=({SLUG: 1128.0} if held == 1128 else None))
        b = _frozen_long(p, ledger=0, reason=reason, lost=lost, last_plan={"kind": "no_plan", "venue": 0.0})
        _seed(NOW - 2000, confirmed=EXPIRED)
        v = _Venue(state=EXPIRED, held={SLUG: held})
        st = _tick(p, v)
        assert b["state"] == "frozen" and b["frozen_reason"] == reason, reason
        assert _census(st, "venue_market_ended") == 0 and _census(st, "closed_cancelled") == 0, reason
        assert not _state_writes(p, b["id"]) and _bbos(v), reason
        assert p.rows[b["standing_row_id"]]["status"] == "filled", reason


def test_fold_review_g3_a_frozen_book_with_shares_on_the_ledger_never_closes_on_a_flat_plan_reading():
    """Kills the ledger gate dropped for the frozen book (survived the
    builder's file): frozen cancel_pending, LEDGER 300, the last plan's
    venue reading 0 (the venue sold what we hold, or a stale reading)
    -- the ledger holds it first, before any plan or memo is read. And
    the plan reading's own tolerance is the rules' 1e-6: 0.4 of a share
    is HELD, so is -0.4; a reading of None (the positions walk failed:
    the plan carries `venue` None) is no reading."""
    ml._terminal_book_confirmed[KEY] = EXPIRED
    f = ml._venue_market_ended
    base = {"whale": "rn1", "condition_id": CID, "state": "frozen", "frozen_reason": "cancel_pending"}
    assert f({**base, "ledger_net": 300, "last_plan": {"venue": 0}}) is None
    assert f({**base, "ledger_net": -300, "last_plan": {"venue": 0.0}}) is None
    assert f({**base, "ledger_net": 1, "last_plan": {"venue": 0.0}}) is None
    assert rules.FLAT_TOL_SHARES == 1e-6
    assert f({**base, "ledger_net": 0, "last_plan": {"venue": 0.4}}) is None, "0.4 of a share is held"
    assert f({**base, "ledger_net": 0, "last_plan": {"venue": -0.4}}) is None
    assert f({**base, "ledger_net": 0, "last_plan": {"venue": 1e-7}}) == EXPIRED, "under the rules' tolerance: flat"
    assert f({**base, "ledger_net": 0, "last_plan": {"venue": None}}) is None, "NULL reading: no reading"
    assert f({**base, "ledger_net": 0, "last_plan": {"venue": float("inf")}}) is None
    assert f({**base, "ledger_net": 0, "last_plan": {"venue": True}}) is None
    # a live held book on a flat plan reading: the ledger holds it too (the plan is never read on live)
    assert f({**base, "state": "live", "ledger_net": 300, "last_plan": {"venue": 0}}) is None
    # end to end: frozen, ledger 300, a stale flat plan, the venue holding 300, the state confirmed -- frozen still
    p = _e5_pool()
    b = _frozen_long(p, ledger=300, reason="cancel_pending", lost=0, last_plan={"kind": "no_plan", "venue": 0.0})
    _seed(NOW - 2000, confirmed=EXPIRED)
    st = _tick(p, _Venue(state=EXPIRED, held={SLUG: 300}))
    assert b["state"] == "frozen" and _census(st, "venue_market_ended") == 0 and not _state_writes(p, b["id"])


def test_fold_review_g4_455s_confirming_read_writes_the_venue_reading_the_close_reads_next_tick():
    """Kills a no_mark plan without `venue` (the frozen book's terminal
    read is the no_mark refusal's plan, {**plan, kind no_plan}, and the
    plan dict carries r.venue): after the confirming read the plan says
    venue 0 and kind no_plan; the close the tick after reads exactly
    that. A positions walk that failed on the confirming read leaves
    `venue` None on the plan, so the close waits for a read that saw
    the venue (fail closed), and lands on the next one."""
    p = _e5_pool()
    b = p.add_book(ledger=0, avg_cost=None, state="frozen", frozen_reason="cancel_pending", frozen_ts=NOW - 100)
    _seed(NOW - 800, until=NOW + 100)
    _tick(p, _Venue(state=EXPIRED))
    assert b["last_plan"]["kind"] == "no_plan" and "venue" not in b["last_plan"], "the memo skip's plan"
    assert ml._venue_market_ended(b) is None
    _tick(p, _Venue(state=EXPIRED), now=NOW + 101)
    assert ml._terminal_book_confirmed == {KEY: EXPIRED} and b["state"] == "frozen"
    assert b["last_plan"]["kind"] == "no_plan" and b["last_plan"]["venue"] == 0, b["last_plan"]
    assert ml._venue_market_ended(b) == EXPIRED
    # the plan's venue reading None (the walk failed): held until a read sees the venue
    b["last_plan"] = {**b["last_plan"], "venue": None}
    st = _tick(p, _Venue(state=EXPIRED), now=NOW + 131)
    assert b["state"] == "frozen" and _census(st, "venue_market_ended") == 0
    assert b["last_plan"]["kind"] == "no_plan" and "venue" not in b["last_plan"], "the memo skip again: no reading"
    ml._terminal_book_until.clear()                       # the memo's TTL run by hand: the next tick reads
    st2 = _tick(p, _Venue(state=EXPIRED), now=NOW + 161)
    assert b["state"] == "frozen" and b["last_plan"]["venue"] == 0 and _census(st2, "venue_market_ended") == 0
    st3 = _tick(p, _Venue(state=EXPIRED), now=NOW + 191)
    assert b["state"] == "closed" and _census(st3, "venue_market_ended") == 1 and _census(st3, "closed_cancelled") == 1
