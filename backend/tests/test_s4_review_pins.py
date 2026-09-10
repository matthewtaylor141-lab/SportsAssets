"""S4 v2 adversarial review, round 2 (2026-09-07): the four round-1
findings re-attacked on the fold, the proof's semantics under three
fake venues, the cover's money direction byte for byte through the
real adapter, the position accounting after a cover fill, the probe's
clock, its key and its two unmanaged leftovers, the 429 path, the
lost-cover road, the legacy CLOSE rows.

Kept in the tree as the S4 review's pins (v3, 2026-09-07). Tests named
test_F1_..F4_ are round 1's findings as the spec reads them; tests
named test_GAP_* asserted what the brief asks for and FAILED on v2
where a gap remained -- the fold (S4 v3) closed the three gaps, so
each keeps its spec line and has the intermediate assertions that
pinned v2's consequence restated (the way F2_restated restates F2),
each docstring saying which. test_F3 and the state pin are restated
the same way: the owner decided GAP 3 (the 429 writes the hour's
hold) and INFO 5 (the echo's state is read). Every other test is a
pin of something checked and found sound.
"""
import inspect
import json

import pytest

from sportsassets import pmus
from sportsassets import venue_pace
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    BUY, CID, GTC_TIF, INTENT, IOC_TIF, M, N, NOW, SELL, SHORT, SLUG, _NoClose, _Pool, _armed,
    _cancels, _census, _fill, _gone, _his, _kinds, _mkt, _places, _pool, _s4_proved,
    _s4_unproven, _short_book, _short_world, _shorts_on, _tick, _unpriced,
)

SELL_SHORT = "ORDER_INTENT_SELL_SHORT"
BUY_LONG = "ORDER_INTENT_BUY_LONG"
KEY = "mirror_s4_proof"

# his cover at 0.40 in long space, two ways: a SELL of the other token
# at 0.60, or a BUY of the long token at 0.40
HIS_SELL_N_060 = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
                  _fill(N, "SELL", 400, 0.60, NOW - 2000)]
HIS_BUY_M_040 = [_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
                 _fill(M, "BUY", 400, 0.40, NOW - 2000)]


def _cover_world(fills, snap, **venue_kw):
    """A proved short book of 300 with his fills as given."""
    p = _short_world(fills=fills, snap=snap)
    b = _short_book(p, ledger=-300)
    venue_kw.setdefault("held", {SLUG: -300})
    return p, b, _NoClose(**venue_kw)


def _unproven_world(fills=None, snap=None, **venue_kw):
    """A short book of 300 with the key ABSENT: the probe is due."""
    p = _short_world(fills=fills or _his(), snap=snap or {M: 300.0, N: 0.0})
    b = _short_book(p, ledger=-300)
    p.state.pop(KEY)
    venue_kw.setdefault("held", {SLUG: -300})
    return p, b, _NoClose(**venue_kw)


class _Echo(_NoClose):
    """A venue whose read-back of the PROBE (the 1-share sell) is
    scripted: `echo` is what open_orders lists for it -- side (the
    desk's spelling; venue_side derives from it), price, intent,
    quantity, slug -- while every other placement rests as the fixture
    does. `echo` None: the probe is accepted and listed under nothing."""

    def __init__(self, *a, echo=None, probe_state="new", **kw):
        super().__init__(*a, **kw)
        self.echo = echo
        self.probe_state = probe_state

    def submit_fok(self, slug, price, qty, sell=False, tif=GTC_TIF, intent=None, post_only=False,
                   good_till=None, paced_pair=False):
        if not (sell and qty == 1):
            return super().submit_fok(slug, price, qty, sell, tif, intent, post_only, good_till, paced_pair)
        self.calls.append(("place", slug, price, qty, sell, tif, intent, post_only, good_till))
        self.n += 1
        oid = f"oid-{self.n}"
        e = dict(self.echo or {})
        self.rest(oid, e.get("side", "BUY"), e.get("price", price), e.get("quantity", qty),
                  e.get("slug", slug), state=self.probe_state, intent=e.get("intent", SELL_SHORT))
        return {"ok": False, "order_id": oid, "status": "new", "fill_price": None,
                "filled_shares": 0.0, "raw": {"response": {"id": oid}}}


def _probe_and_cover(v):
    places = _places(v)
    return [c for c in places if c[3] == 1], [c for c in places if c[3] != 1]


# ------------------------------------------ 1. round 1's findings, on v2

def test_F1_the_read_back_reads_the_venues_own_side_and_a_sell_side_echo_is_refused_wrong_side(monkeypatch):
    _shorts_on(monkeypatch)
    for venue_side in ("ORDER_SIDE_BUY", "ORDER_SIDE_SELL"):
        row = pmus._norm_order({"id": "x", "marketSlug": SLUG, "intent": SELL_SHORT, "side": venue_side,
                                "price": {"value": "0.25"}, "quantity": 1, "state": "ORDER_STATE_NEW"})
        assert row["side"] == "SELL" and row["venue_side"] == venue_side
    assert pmus._norm_order({"id": "x", "intent": SELL_SHORT})["venue_side"] is None
    p, b, v = _unproven_world(ioc_fill=300.0, lift=300.0)
    v.__class__ = _Echo
    v.echo, v.probe_state = {"side": "SELL"}, "new"
    st = _tick(p, v)
    rec = p.state[KEY]
    assert rec["echo"]["side"] == "SELL" and rec["echo"]["venue_side"] == "ORDER_SIDE_SELL"
    assert rec["echo"]["intent"] == SELL_SHORT and rec["proved"] is False and rec["why"] == "wrong_side"
    assert _census(st, "s4_unproven") == 1 and b["ledger_net"] == -300 and _probe_and_cover(v)[1] == []
    assert _cancels(v) == [("cancel", "oid-1", SLUG)]


def test_F2_restated_the_short_token_space_venue_is_refused_on_both_books_and_no_cover_ever_rests(monkeypatch):
    """Round 1's F2 with its two intermediate assertions inverted: they
    pinned v1's consequence (the cover resting at 0.70 after the bad
    proof). On v2 the low book fails closed on the post-only refusal
    and the high book is refused `wrong_side`; on neither does a cover
    rest."""
    _shorts_on(monkeypatch)

    class _ShortSpace(_NoClose):
        def submit_fok(self, slug, price, qty, sell=False, tif=GTC_TIF, intent=None, post_only=False,
                       good_till=None, paced_pair=False):
            self.calls.append(("place", slug, price, qty, sell, tif, intent, post_only, good_till))
            self.n += 1
            oid = f"oid-{self.n}"
            if sell and intent == SHORT:
                if round(1.0 - price, 2) >= self.ask:
                    if post_only:
                        return {"ok": False, "order_id": None, "status": "post_only_rejected",
                                "fill_price": None, "filled_shares": 0.0,
                                "raw": {"status_code": 400, "error": "would cross"}}
                    if tif == IOC_TIF:
                        return {"ok": True, "order_id": oid, "status": "filled", "fill_price": self.ask,
                                "filled_shares": float(qty), "raw": {}}
                self.rest(oid, "SELL", price, qty, slug, intent=SELL_SHORT)
                if tif == IOC_TIF:
                    self.orders[oid]["state"] = "cancelled"
                    return {"ok": False, "order_id": oid, "status": "canceled", "fill_price": None,
                            "filled_shares": 0.0, "raw": {}}
                return {"ok": False, "order_id": oid, "status": "new", "fill_price": None,
                        "filled_shares": 0.0, "raw": {"response": {"id": oid}}}
            return super().submit_fok(slug, price, qty, sell, tif, intent, post_only, good_till, paced_pair)

    p = _pool()
    b = _short_book(p, ledger=-300)
    p.state.pop(KEY)
    st = _tick(p, _ShortSpace(bid=0.30, ask=0.32, held={SLUG: -300}))
    assert p.state[KEY]["why"] == "place_refused:post_only_rejected" and b["ledger_net"] == -300
    fills = [_fill(N, "BUY", 400, 0.35, NOW - 2500), _fill(M, "BUY", 400, 0.70, NOW - 2000)]
    p2 = _short_world(fills=fills, snap=None)
    b2 = _short_book(p2, ledger=-300, avg=0.68)
    p2.state.pop(KEY)
    v2 = _ShortSpace(bid=0.70, ask=0.72, held={SLUG: -300})
    st2 = _tick(p2, v2, http=_gone())
    rec = p2.state[KEY]
    assert [c[2:4] for c in _places(v2)] == [(0.65, 1)], "the probe alone; no cover on this venue"
    assert rec["echo"]["price"] == 0.65 and rec["echo"]["venue_side"] == "ORDER_SIDE_SELL"
    assert rec["proved"] is False and rec["why"] == "wrong_side"
    assert _census(st2, "short_cover_rest") == 0 and _census(st2, "s4_unproven") == 1 and b2["ledger_net"] == -300
    assert _census(st, "s4_unproven") == 1


def test_F3_a_429_on_the_probe_in_both_shapes_is_the_circuit_and_writes_the_hours_hold(monkeypatch):
    """Round 2's F3 with its two record assertions restated: v2 left the
    key byte-identical on a 429 (no verdict, no hold) and the plan named
    the prior mismatch; v3 (GAP 3, owner's decision) writes {"proved":
    false, "why": "rate_limited", "at": now} over it so the hour's clock
    holds the probe, and the plan names the 429. Everything else --
    the circuit, the abandon, the backoff skipped, no cover, no cancel,
    no row -- is verbatim."""
    _shorts_on(monkeypatch)

    class RateLimitError(Exception):
        status_code = 429

    def _raw(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                "filled_shares": 0.0, "raw": {"preview": {}, "status_code": 429,
                                              "error": "RateLimitError: Too Many Requests",
                                              "error_type": "RateLimitError", "body": None}}
    for shape in ("raise", "raw"):
        venue_kw = {"place_raises": RateLimitError("429")} if shape == "raise" else {"place": _raw}
        p, b, v = _cover_world(_his(), snap={M: 300.0, N: 0.0}, ioc_fill=300.0, lift=300.0, **venue_kw)
        _s4_unproven(p, why="wrong_price", at=NOW - 7200)
        st = _tick(p, v)
        rec = p.state[KEY]
        assert (rec["proved"], rec["why"], rec["at"], rec["order_id"]) == (False, "rate_limited", NOW, None), shape
        assert _census(st, "rate_limited") == 1 and _census(st, "s4_probe_placed") == 1, shape
        assert st["abandoned"] and st["abandon_reason"] == "rate_limited", shape
        assert _census(st, "backoff_skipped_circuit") == 1, shape
        # the 429 gates the cover by its own name for the hour (v3: the record IS the hold)
        assert _census(st, "s4_unproven") == 1 and b["last_plan"]["s4"] == {"unproven": "rate_limited"}, shape
        assert not _cancels(v) and not p.orders and b["ledger_net"] == -300, shape
        assert venue_pace.penalty_left() > 0.0, shape
        venue_pace._penalty_until = 0.0
    # a 400 whose text carries 429 is a crossing refusal, recorded, abandoning nothing
    def _cross(v, oid, slug, price, qty, sell, tif, intent, post_only, good_till):
        return {"ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
                "filled_shares": 0.0, "raw": {"status_code": 400, "error": "would cross at 0.429"}}
    p3, b3, v3 = _cover_world(_his(), snap={M: 300.0, N: 0.0}, place=_cross)
    p3.state.pop(KEY)
    st3 = _tick(p3, v3)
    assert p3.state[KEY]["why"] == "place_refused:post_only_rejected" and not st3["abandoned"]
    assert _census(st3, "rate_limited") == 0


def test_F4_a_lost_cover_rest_is_adopted_by_its_wire_intent_and_a_lost_cover_ioc_books_from_the_trade_log(monkeypatch):
    _shorts_on(monkeypatch)
    # the GTC rest, lost after the venue rested it: adopted, live
    p, b, v = _cover_world(HIS_SELL_N_060, snap=None, bid=0.38, ask=0.45,
                           place_raises=RuntimeError("read timeout"), rest_on_raise=True)
    st = _tick(p, v, http=_gone())
    assert [c[2:6] for c in _places(v)] == [(0.40, 300, True, GTC_TIF)]
    o = next(iter(p.orders.values()))
    assert v.orders["oid-1"]["venue_side"] == "ORDER_SIDE_BUY" and v.orders["oid-1"]["intent"] == SELL_SHORT
    assert (o["state"], o["order_id"], o["side"], o["intent"]) == ("open", "oid-1", BUY, SELL_SHORT)
    assert b["state"] == "live" and _census(st, "placement_lost") == 0
    # the unit: intent to intent when both name one; a SELL_LONG or BUY_LONG at our cent is not ours
    row = {"side": BUY, "intent": SELL_SHORT}
    ours = {"order_id": "x", "intent": SELL_SHORT, "side": "SELL", "price": 0.40, "quantity": 300.0}
    assert ml._on_book_matches(row, ours, 0.40, 300)
    for bad in ("ORDER_INTENT_SELL_LONG", BUY_LONG, "ORDER_INTENT_BUY_SHORT"):
        assert not ml._on_book_matches(row, {**ours, "intent": bad}, 0.40, 300), bad
    assert not ml._on_book_matches(row, {**ours, "price": 0.41}, 0.40, 300)
    assert not ml._on_book_matches(row, {**ours, "quantity": 299.0}, 0.40, 300)
    # THE COVER lost with nothing resting: frozen placement_lost, the row 'placing'; past the
    # window the trade log names our order (a contract BUY of 300) and books it.
    # E31: the order that is lost here is no longer the ceiling-cent IOC at 0.41 -- there is
    # no cover take. It is the MAKER REST at min(floor(his 0.40), ask 0.41 - MAKER_TICK) =
    # 0.40, GTC, post_only, and the row's `tif` reads GTC where it read IOC. Everything the
    # test is about -- the lost placement, the freeze, the trade-log adoption by our own
    # (price, quantity, contract side) and the SELL control -- is unchanged, at the new cent
    p2, b2, v2 = _cover_world(HIS_SELL_N_060, snap=None, bid=0.38, ask=0.41,
                              place_raises=RuntimeError("read timeout"), rest_on_raise=False)
    st2 = _tick(p2, v2, http=_gone())
    assert [c[2:6] for c in _places(v2)] == [(0.40, 300, True, GTC_TIF)]
    o2 = next(iter(p2.orders.values()))
    assert o2["state"] == "placing" and o2["order_id"] is None and o2["tif"] == "GTC"
    assert o2["wire"] == 0.40 and o2["intent"] == SELL_SHORT
    assert b2["state"] == "frozen" and b2["frozen_reason"] == "placement_lost" and b2["ledger_net"] == -300
    assert _census(st2, "placement_lost") == 1
    trade = {"qty": 300.0, "price": 0.40, "side": "ORDER_SIDE_BUY", "ts": NOW + 1, "realized_pnl": 0.0,
             "order_id": "venue-77", "order_qty": 300.0, "order_price": 0.40}
    v3 = _NoClose(bid=0.38, ask=0.41, held={SLUG: -300}, trades=[trade])
    st3 = _tick(p2, v3, now=NOW + 1300, http=_gone())
    assert o2["order_id"] == "venue-77" and o2["state"] == "filled" and b2["ledger_net"] == 0
    assert ("trades", SLUG, pytest.approx(NOW - 30.0)) in v3.calls
    assert _census(st3, "short_flatten_close") == 1 and "close" not in _kinds(v3)
    # the control: a trade the venue lists as a contract SELL at our cent and size is not the cover's
    p4, b4, v4 = _cover_world(HIS_SELL_N_060, snap=None, bid=0.38, ask=0.41,
                              place_raises=RuntimeError("read timeout"), rest_on_raise=False)
    _tick(p4, v4, http=_gone())
    o4 = next(iter(p4.orders.values()))
    v5 = _NoClose(bid=0.38, ask=0.41, held={SLUG: -300}, trades=[{**trade, "side": "ORDER_SIDE_SELL"}])
    st5 = _tick(p4, v5, now=NOW + 1300, http=_gone())
    assert o4["state"] == "lost" and b4["ledger_net"] == -300 and _census(st5, "order_lost") == 1


# ---------------------------------------------- 2. the proof's semantics

def test_the_proof_semantics_only_a_contract_buy_with_the_closing_intent_at_our_price_and_qty_1_proves(monkeypatch):
    """What the venue must echo for `proved: true`: the probe's own id
    on our slug, price == what we sent in the LONG token's space, qty 1,
    Order.side == ORDER_SIDE_BUY (the contract side), Order.intent ==
    SELL_SHORT. (a) proves and the cover goes the same tick; (b) side
    BUY with the OPENING long intent (the venue silently opened a long
    instead of closing the short) is `wrong_intent`; (c) the price in
    the other token's space (1 - p) is `wrong_price`; and the rest of
    the table. Every mismatch cancels the probe order and gates the
    cover; the SDK's Order type carries both `side` and `intent`, so
    both are demanded (a missing one fails closed)."""
    _shorts_on(monkeypatch)
    px = ml._s4_probe_price(0.30)                       # 0.25 on the fixture's 0.30/0.32
    table = [
        ("a: side BUY, SELL_SHORT, our price, qty 1", {}, None),
        ("b: side BUY, the OPENING long intent", {"intent": BUY_LONG}, "wrong_intent"),
        ("c: the price in the other token's space", {"price": round(1.0 - px, 2)}, "wrong_price"),
        ("c': the other space AND the other side", {"price": round(1.0 - px, 2), "side": "SELL"}, "wrong_price"),
        ("d: side SELL (an order in another space)", {"side": "SELL"}, "wrong_side"),
        ("e: the side not reported", {"side": None}, "side_missing"),
        ("f: the intent not reported", {"intent": None}, "intent_missing"),
        ("g: qty 2", {"quantity": 2}, "wrong_quantity"),
        ("h: another slug", {"slug": "other-slug"}, "wrong_slug"),
    ]
    for label, echo, why in table:
        p, b, v = _unproven_world(ioc_fill=300.0, lift=300.0)
        v.__class__ = _Echo
        v.echo, v.probe_state = echo, "new"
        st = _tick(p, v)
        rec = p.state[KEY]
        probes, covers = _probe_and_cover(v)
        assert probes == [("place", SLUG, px, 1, True, GTC_TIF, SHORT, True, None)], label
        assert _cancels(v) == [("cancel", "oid-1", SLUG)], label
        if why is None:
            assert rec["proved"] is True and rec["why"] is None, label
            assert rec["echo"]["venue_side"] == "ORDER_SIDE_BUY" and rec["echo"]["intent"] == SELL_SHORT, label
            assert rec["echo"]["price"] == px and rec["echo"]["quantity"] == 1.0, label
            assert rec["bid"] == 0.30 and rec["ask"] == 0.32 and rec["price"] == px, label
            # E31: the proved cover is no longer the IOC at the CEILING cent 0.32, through
            # the ask. It is the post-only rest at min(buy_wire(his 0.31), ask - MAKER_TICK)
            # = 0.31 -- his own cent, GTC, post_only True, never at or through the ask. The
            # ledger still reaches 0 because this world's taker lifts the fresh rest
            assert covers == [("place", SLUG, 0.31, 300, True, GTC_TIF, SHORT, True, None)], label
            assert b["ledger_net"] == 0 and _census(st, "s4_proved") == 1, label
        else:
            assert rec["proved"] is False and rec["why"] == why, label
            assert covers == [] and b["ledger_net"] == -300 and _census(st, "s4_unproven") == 1, label
            assert b["last_plan"]["s4"] == {"unproven": why}, label
    # the order of the checks: the side before the intent, the price before the side
    src = inspect.getsource(ml._s4_probe)
    assert src.index('rec["why"] = "wrong_price"') < src.rindex("S4_VENUE_BUY") < src.index('"wrong_intent"')


def test_the_proof_reads_the_orders_state_and_not_its_tif(monkeypatch):
    """Round 2's LOW pin, restated for INFO 5 (v3): the proof now READS
    the echo's state. An echo the venue lists in ORDER_STATE_PENDING_RISK
    (accepted for listing, not yet past risk) still proves -- it is one
    of the three standing states (new, pending_new, pending_risk) -- and
    a filled or cancelled echo is `wrong_state` (the venue parsed our
    fields but nothing stands). A tif other than GTC is still not
    compared. pmus.open_orders applies no state filter (every row the
    venue lists is returned), so the proof's own state check is what
    makes its reading 'the venue lists our order STANDING this way'."""
    _shorts_on(monkeypatch)
    for state, proved in (("pending_risk", True), ("new", True), ("pending_new", True),
                          ("filled", False), ("canceled", False)):
        p, b, v = _unproven_world(ioc_fill=300.0, lift=300.0)
        v.__class__ = _Echo
        v.echo, v.probe_state = {}, state
        v.open_orders = lambda slugs=None, v=v: [v._norm(o) for o in v.orders.values()]   # the venue lists it
        _tick(p, v)
        assert p.state[KEY]["proved"] is proved and p.state[KEY]["echo"]["state"] == state, state
        assert p.state[KEY]["why"] == (None if proved else "wrong_state"), state
    src = inspect.getsource(ml._s4_probe).split("wrong_slug")[1].split("cancelled whatever")[0]
    assert "S4_RESTING_STATES" in src and "tif" not in src
    assert inspect.getsource(pmus.open_orders).count("state") == 0


# ---------------------------------------------- 3. the money direction

def test_every_cover_path_hands_the_adapter_sell_true_with_the_books_buy_short_and_the_real_adapter_wires_sell_short(monkeypatch):
    """For a short book (ledger -300) every cover -- the rest at
    floor(his), the cover the ceiling cent used to take, the unpriced
    vanish, the sign flip, the partial reduce -- is (slug, price, qty,
    sell=True, tif, intent=BUY_SHORT, post_only, None); the real
    pmus.submit_fok builds from exactly that call a body whose intent is
    SELL_SHORT, never BUY_LONG, with no side field and no preview, and
    never consults the position (a caller naming the intent never
    reaches position_side). His 0.40 on the long token and his 0.60 on
    the other token produce the same body byte for byte."""
    _shorts_on(monkeypatch)
    monkeypatch.setattr(pmus, "position_side", lambda slug: (_ for _ in ()).throw(AssertionError("position read")))
    sent = []

    class _Orders:
        def preview(self, req):
            raise AssertionError("a SELL is never previewed")

        def create(self, params):
            sent.append(dict(params))
            return {"id": f"venue-{len(sent)}", "executions": []}

    class _Cli:
        orders = _Orders()
    monkeypatch.setattr(pmus, "_get_client", lambda: _Cli())
    worlds = {
        "rest at floor(his): his SELL of N at 0.60": (HIS_SELL_N_060, None, _gone(), dict(bid=0.38, ask=0.45),
                                                       [(0.40, 300, GTC_TIF, True)]),
        "rest at floor(his): his BUY of M at 0.40": (HIS_BUY_M_040, None, _gone(), dict(bid=0.38, ask=0.45),
                                                      [(0.40, 300, GTC_TIF, True)]),
        # E31: THE THREE IOC ROADS ARE THE SAME REST NOW. Each was a cover that crossed --
        # the ceiling cent 0.41 AT the ask, the vanish's 0.42 = ask + 2c, the sign flip's
        # 0.32 AT the ask -- and each is a post-only GTC rest at min(buy_wire(his), ask -
        # MAKER_TICK), or at the touch's own inside tick where there is no level of his:
        #   0.41 IOC  -> 0.40 rest   his 0.40 is under ask - a tick (0.40): HIS cent
        #   0.42 IOC  -> 0.39 rest   unpriced: ask 0.40 - a tick, `unpriced_touch`
        #   0.32 IOC  -> 0.31 rest   his 0.31 is at ask - a tick (0.31): HIS cent
        # `post_only` False -> True on all three, the tif IOC -> GTC. Each world's taker
        # still lifts the fresh rest, so every ledger this test reads is unmoved
        "the ceiling cent's cover, now a rest": (HIS_SELL_N_060, None, _gone(),
                                                 dict(bid=0.38, ask=0.41, ioc_fill=300.0, lift=300.0),
                                                 [(0.40, 300, GTC_TIF, True)]),
        "unpriced vanish, now a rest at the touch": (_unpriced(), None, _gone(),
                                                     dict(bid=0.30, ask=0.40, ioc_fill=300.0, lift=300.0),
                                                     [(0.39, 300, GTC_TIF, True)]),
        "sign flip, now a rest": (_his(), {M: 300.0, N: 0.0}, None,
                                  dict(bid=0.30, ask=0.32, ioc_fill=300.0, lift=300.0),
                                  [(0.31, 300, GTC_TIF, True)]),
        "partial reduce rest": (_his(300, other_size=400, other_px=0.72), {M: 300.0, N: 400.0},
                                _mkt(300.0, 400.0), dict(bid=0.30, ask=0.40), [(0.31, 200, GTC_TIF, True)]),
    }
    bodies = {}
    for label, (fills, snap, http, venue_kw, want) in worlds.items():
        p, b, v = _cover_world(fills, snap=snap, **venue_kw)
        st = _tick(p, v, http=http)
        calls = _places(v)
        assert [(c[2], c[3], c[5], c[7]) for c in calls] == want, label
        for c in calls:
            assert c[4] is True and c[6] == SHORT and c[8] is None, (label, c)
            sent.clear()
            res = pmus.submit_fok(*c[1:])
            assert res["order_id"] and len(sent) == 1, label
            body = sent[0]
            assert body["intent"] == SELL_SHORT and "side" not in body, (label, body)
            assert body["quantity"] == c[3] and body["price"] == {"value": f"{c[2]:.2f}", "currency": "USD"}, label
            assert body["tif"] == c[5] and body["marketSlug"] == SLUG and body["type"] == "ORDER_TYPE_LIMIT", label
            assert ("participateDontInitiate" in body) is bool(c[7]) and "goodTillTime" not in body, label
            bodies[label] = body
        rows = list(p.orders.values())
        assert rows and all((o["side"], o["intent"]) == (BUY, SELL_SHORT) for o in rows), label
        assert "close" not in _kinds(v) and _census(st, "book_error") == 0, label
    assert bodies["rest at floor(his): his SELL of N at 0.60"] == bodies["rest at floor(his): his BUY of M at 0.40"] == {
        "marketSlug": SLUG, "intent": SELL_SHORT, "type": "ORDER_TYPE_LIMIT",
        "price": {"value": "0.40", "currency": "USD"}, "quantity": 300, "tif": GTC_TIF,
        "synchronousExecution": True, "participateDontInitiate": True}
    # the adapter refuses a caller handing it a SELL intent or a BUY_LONG on a short cover
    assert pmus.submit_fok(SLUG, 0.4, 300, True, GTC_TIF, SELL_SHORT)["status"] == "bad_intent"
    assert pmus._exit_intent(SLUG, SHORT) == SELL_SHORT and pmus._exit_intent(SLUG, INTENT) == "ORDER_INTENT_SELL_LONG"
    assert BUY_LONG not in {b_["intent"] for b_ in bodies.values()}


# ---------------------------------------------- 4. position accounting

def test_after_a_cover_fill_the_ledger_moves_toward_zero_never_past_it_realized_is_the_short_formula_and_the_shadow_agrees(monkeypatch):
    _shorts_on(monkeypatch)
    # E31: the whole cover on avg 0.32 covers at 0.30, not 0.31 -- and the ledger arithmetic
    # is unchanged, so realized moves with the cent. Before this lane the cover took at the
    # CEILING cent (his 0.30 + MIRROR_EXIT_TOL = 0.31, THROUGH the ask of 0.31) and realized
    # (0.32 - 0.31) x 300 = +3. It now rests at min(buy_wire(his 0.30), ask - MAKER_TICK) =
    # 0.30 -- HIS OWN cent, one tick under the ask -- and the taker who comes there lifts it,
    # so realized is (0.32 - 0.30) x 300 = +6 and gross_sell_usd 300 x (1 - 0.30) = 210.
    # THE CENT MOVED IN OUR FAVOUR: this is the spread the lane exists to stop paying
    p, b, v = _cover_world([_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
                            _fill(N, "SELL", 400, 0.70, NOW - 2000)], snap=None, bid=0.30, ask=0.31, ioc_fill=300.0, lift=300.0)
    st = _tick(p, v, http=_gone())
    assert [c[2:6] for c in _places(v)] == [(0.30, 300, True, GTC_TIF)]
    assert b["ledger_net"] == 0 and b["realized_pnl"] == pytest.approx(6.0) and b["avg_cost"] == 0.32
    assert b["gross_sell_usd"] == pytest.approx(300 * (1 - 0.30), abs=1e-3)
    assert _census(st, "short_flatten_close") == 1 and _census(st, "shadow_live_disagree") == 0
    assert _census(st, "maker_fill_at_create") == 1, "the venue's own aggressor bool: we were the maker"
    o = next(iter(p.orders.values()))
    assert o["state"] == "filled" and o["realized"] == pytest.approx(6.0) and o["wire"] == 0.30
    # the book closes once flat for MIRROR_FLAT_CLOSE_S with the venue read at 0 (the long
    # book's own rule), never on the fill report alone
    assert b["state"] == "live"
    st2 = _tick(p, _NoClose(bid=0.30, ask=0.31, held={SLUG: 0}), now=NOW + 5, http=_gone())
    assert b["state"] == "live" and b["last_plan"]["close"] == "not_due"
    st2 = _tick(p, _NoClose(bid=0.30, ask=0.31, held={SLUG: 0}), now=NOW + 3601, http=_gone())
    # FILL lane 5 (2026-09-08): this tick is the quiet rotation's SKIP (on target at its
    # last read, no fill of his inside HOT_S), which hands the flat-clock guard no reading
    # -- held `he_holds_unread`, live (re-pinned from `closed` on the skip); the rotation's
    # read tick reads his 0 on the short axis (400 bought, 400 sold) and the clock closes
    assert b["state"] == "live" and b["last_plan"]["close"] == "he_holds_unread"
    assert _census(st2, "closed_cashed_out") == 0 and _census(st2, "he_holds_unread") == 1
    for i in range(1, int(ml.QUIET_EVERY_TICKS) + 2):
        st2 = _tick(p, _NoClose(bid=0.30, ask=0.31, held={SLUG: 0}), now=NOW + 3601 + 30 * i, http=_gone())
        if b["state"] == "closed":
            break
    assert b["state"] == "closed" and _census(st2, "closed_cashed_out") == 1 and _census(st2, "he_holds") == 0
    # a partial cover: ledger -100, avg untouched, realized on the 200.
    # E31, the same substitution: his newest cover-direction fill is 0.30, whose ceiling 0.31
    # was the IOC's cent AT the ask; the rest goes at min(buy_wire(0.30), 0.31 - MAKER_TICK)
    # = 0.30, so realized on the 200 is (0.32 - 0.30) x 200 = +4, not +2
    fills = [_fill(M, "BUY", 300, 0.30, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2000)]
    p2, b2, v2 = _cover_world(fills, snap={M: 300.0, N: 400.0}, bid=0.30, ask=0.31, ioc_fill=200.0, lift=200.0)
    st2 = _tick(p2, v2, http=_mkt(300.0, 400.0))
    assert b2["target"] == -100 and [c[2:6] for c in _places(v2)] == [(0.30, 200, True, GTC_TIF)]
    assert b2["ledger_net"] == -100 and b2["avg_cost"] == 0.32 and b2["realized_pnl"] == pytest.approx(4.0)
    assert _census(st2, "short_flatten_close") == 0 and _census(st2, "shadow_live_disagree") == 0
    # the quantity: min(plan, |ledger|, ceil(held)); never the whole slug
    assert ml._cover_qty({"intent": SHORT, "ledger_net": -300, "_held": 300.0}, 500) == 300
    assert ml._cover_qty({"intent": SHORT, "ledger_net": -300, "_held": 150.4}, 500) == 151
    assert ml._cover_qty({"intent": SHORT, "ledger_net": -300, "_held": None}, 500) == 300
    assert ml._cover_qty({"intent": SHORT, "ledger_net": -300, "_held": 0.0}, 500) == 0
    # the venue reports the IOC filled PAST the leg: the ledger stops at 0 -- one lot is dust,
    # more is the overfill (frozen, tripped), never a ledger past zero
    for reported, dust, over in ((301.0, 1, 0), (303.0, 0, 1)):
        def _over(v_, oid, slug, price, qty, sell, tif, intent, post_only, good_till, n=reported):
            return {"ok": True, "order_id": oid, "status": "filled", "fill_price": price,
                    "filled_shares": n, "raw": {}}
        p3, b3, v3 = _cover_world(HIS_SELL_N_060, snap=None, bid=0.38, ask=0.41, place=_over)
        st3 = _tick(p3, v3, http=_gone())
        assert b3["ledger_net"] == 0, reported
        assert _census(st3, "ledger_dust") == dust and _census(st3, "mirror_overspend") == 0, reported
        assert (b3["state"] == "frozen") is bool(over) and (p3.state.get("mirror_live") is False) is bool(over), reported
    # the rules: book_sell in leg space on a short
    bs = rules.book_sell(rules.BookState(ledger_net=-300, avg_cost=0.32, intent=SHORT), 300, 0.31)
    assert bs.state.ledger_net == 0 and bs.realized == pytest.approx(3.0) and not bs.overfill


# ------------------------------------------------------ 5. the probe

def test_the_probe_runs_at_most_once_an_hour_on_the_clock_never_on_a_frozen_book_and_its_key_survives_a_fresh_tick(monkeypatch):
    _shorts_on(monkeypatch)
    # the clock: a mismatch 3599 s ago holds; 3600 s ago probes
    for age, probes in ((3599.0, 0), (3600.0, 1), (10.0, 0), (86400.0, 1)):
        p, b, v = _cover_world(_his(), snap={M: 300.0, N: 0.0}, ioc_fill=300.0, lift=300.0)
        _s4_unproven(p, why="not_found", at=NOW - age)
        st = _tick(p, v)
        assert _census(st, "s4_probe_placed") == probes and (p.state[KEY]["proved"] is bool(probes)), age
        assert (b["ledger_net"] == 0) is bool(probes), age
    assert ml.S4_PROBE_GAP_S == 3600.0
    # frozen: a book frozen by the venue reading this tick (held -250 vs ledger -300) and one
    # frozen from before: no probe, the key untouched, no cover
    p2, b2, v2 = _unproven_world(held={SLUG: -250}, ioc_fill=300.0, lift=300.0)
    st1 = _tick(p2, v2)          # E16: the first disagreeing read is a suspect -- no probe on it either
    assert b2["state"] == "live" and KEY not in p2.state and not _places(v2) and _census(st1, "s4_probe_placed") == 0
    st2 = _tick(p2, v2, now=NOW + 15)
    assert b2["state"] == "frozen" and b2["frozen_reason"] == "venue_ledger_disagree"
    assert KEY not in p2.state and not _places(v2) and _census(st2, "s4_probe_placed") == 0
    p3, b3, v3 = _unproven_world(ioc_fill=300.0, lift=300.0)
    b3.update(state="frozen", frozen_reason="placement_lost", frozen_ts=NOW - 100)
    p3.add_order(b3, side=BUY, wire=0.31, qty=300, kind="reduce", state="placing", order_id=None,
                 placed_ts=NOW - 100, intent=SELL_SHORT)
    st3 = _tick(p3, v3)
    assert b3["state"] == "frozen" and KEY not in p3.state and not _places(v3)
    assert _census(st3, "s4_probe_placed") == 0
    # the key is state: tick 1 proves and writes; tick 2 on the SAME pool (a fresh _Tick) reads it
    # and probes nothing; a brand-new pool seeded with the record alone probes nothing either
    p4, b4, v4 = _unproven_world(ioc_fill=300.0, lift=300.0)
    st4 = _tick(p4, v4)
    assert _census(st4, "s4_probe_placed") == 1 and p4.state[KEY]["proved"] is True and b4["ledger_net"] == 0
    assert p4.state[KEY]["at"] == NOW and p4.state[KEY]["order_id"] == "oid-1"
    rec = json.loads(json.dumps(p4.state[KEY]))
    b4["ledger_net"] = -300                    # the same book, short again (the fixture's shortcut)
    p4.rows[b4["standing_row_id"]]["filled_shares"] = 300.0
    v4b = _NoClose(held={SLUG: -300}, ioc_fill=300.0, lift=300.0)
    st4b = _tick(p4, v4b, now=NOW + 5)
    assert _census(st4b, "s4_probe_placed") == 0 and [c[3] for c in _places(v4b)] == [300]
    p5, b5, v5 = _cover_world(_his(), snap={M: 300.0, N: 0.0}, ioc_fill=300.0, lift=300.0)
    p5.state[KEY] = rec
    st5 = _tick(p5, v5, now=NOW + 5)
    assert _census(st5, "s4_probe_placed") == 0 and b5["ledger_net"] == 0
    # written through the state statement (ml-state-write), as JSON
    assert any("ml-state-write" in s and a[0] == KEY for _, s, a in p4.sent)


def test_a_standing_cover_rest_is_not_taken_while_the_key_reads_unproven_this_tick(monkeypatch):
    """Defence in depth on the standing-rest branch: the rest was placed
    under a proof; this tick the key is unreadable (or a mismatch was
    written by hand). The take is refused `s4_unproven`, the rest
    stands, no IOC, no cancel."""
    _shorts_on(monkeypatch)
    p, b, v = _cover_world([_fill(M, "BUY", 100, 0.31, NOW - 3000), _fill(N, "BUY", 400, 0.72, NOW - 2500),
                            _fill(N, "SELL", 400, 0.70, NOW - 2000)], snap=None, bid=0.30, ask=0.31, ioc_fill=300.0, lift=300.0)
    p.add_order(b, side=BUY, wire=0.30, qty=300, kind="flatten_paired", intent=SELL_SHORT)
    v.rest("oid-1", "BUY", 0.30, 300, intent=SELL_SHORT)
    _s4_unproven(p, why="wrong_price", at=NOW - 10)
    st = _tick(p, v, http=_gone())
    assert not _places(v) and not _cancels(v) and _census(st, "s4_unproven") == 1
    assert b["ledger_net"] == -300 and v.orders["oid-1"]["state"] == "new"
    assert b["last_plan"]["s4"] == {"unproven": "wrong_price"}


def test_GAP_a_filled_probe_is_booked_and_the_next_cover_is_sized_off_the_ledger_the_row_moved(monkeypatch):
    """The brief: a filled probe (the post-only latch tripped -- the
    shape seen at 23:16Z, a post-only rest filled at create) is BOOKED
    and the proof recorded `filled`, the cover still gated. On v2 the
    record said `filled_at_create` and the cover was gated (sound), but
    the share was booked NOWHERE: no mirror_orders row, no ledger move,
    no freeze -- the venue held one share the ledger did not, inside
    the 1-share venue/ledger tolerance, and an hour on the cover was
    sized off the LEDGER (300) against a venue short of 299. Restated
    for v3 (GAP 1): the share is on an `s4_probe` row and the ledger
    reads -299, so the cover an hour on is 299 (the one intermediate
    assertion inverted: `[1, 300]` -> `[1, 299]`). The spec line at the
    end is the round-2 one, verbatim."""
    _shorts_on(monkeypatch)

    class _ProbeFills(_NoClose):
        def submit_fok(self, slug, price, qty, sell=False, tif=GTC_TIF, intent=None, post_only=False,
                       good_till=None, paced_pair=False):
            if not (sell and qty == 1):
                return super().submit_fok(slug, price, qty, sell, tif, intent, post_only, good_till, paced_pair)
            self.calls.append(("place", slug, price, qty, sell, tif, intent, post_only, good_till))
            self.n += 1
            oid = f"oid-{self.n}"
            self.rest(oid, "BUY", price, qty, slug, state="filled", filled=1.0, avg=self.ask, intent=SELL_SHORT)
            return {"ok": True, "order_id": oid, "status": "filled", "fill_price": self.ask,
                    "filled_shares": 1.0, "raw": {}}
    p, b, v = _unproven_world(ioc_fill=300.0, lift=300.0)
    v.__class__ = _ProbeFills
    st = _tick(p, v)
    rec = p.state[KEY]
    assert rec["proved"] is False and rec["why"] == "filled_at_create" and "cancel" not in rec
    assert rec["echo"] == {"filled_shares": 1.0, "fill_price": 0.32, "status": "filled"}
    assert _census(st, "s4_unproven") == 1 and not _cancels(v)
    assert b["ledger_net"] == -299 and _census(st, "s4_probe_filled") == 1, "v3: the share booked"
    # the venue now holds -299 against a ledger of -299: the book stays live
    v2 = _NoClose(held={SLUG: -299}, ioc_fill=300.0, lift=300.0)
    _tick(p, v2, now=NOW + 3600)
    assert b["state"] == "live" and p.state[KEY]["proved"] is True
    assert [c[3] for c in _places(v2)] == [1, 299], "the cover is 299 on a venue short of 299 (v3)"
    assert b["ledger_net"] == 0
    # THE SPEC: the probe's share is on a row or on the ledger, or the book is frozen by name
    booked_rows = [o for o in p.orders.values() if o.get("kind") not in (None, "reduce", "flatten_paired",
                                                                         "flatten_vanished", "take")]
    assert (booked_rows or b["ledger_net"] == -299 or b["state"] == "frozen"
            or _census(st, "venue_ledger_disagree") == 1), \
        "the filled probe's share is booked nowhere and nothing is named"


def test_GAP_a_probe_the_venue_keeps_after_a_failed_cancel_is_retried_every_tick_and_never_probed_beside(monkeypatch):
    """A cancel the venue refused (or a 429 on the cancel) leaves the
    1-share probe resting with no row. On v2 the record named it
    `cancel_failed` with the id and an ERROR was logged -- and that was
    the end of it: no tick retried the cancel, no path adopted it, and
    the hour's next probe OVERWROTE the record (the id gone from state)
    while placing a second 1-share order beside the first. Restated
    for v3 (GAP 2): the record keeps the id (`cancel.ok` False), every
    tick retries the cancel before the walk, and an hour on there is
    NO second probe -- the retry succeeds, the id is cleared and the
    hour restarts from then (the two intermediate assertions inverted:
    no probe, the old order cancelled, the record's id None). The spec
    line at the end is the round-2 one, verbatim."""
    _shorts_on(monkeypatch)
    p, b, v = _unproven_world(ioc_fill=300.0, lift=300.0, cancel_ok=False)
    st = _tick(p, v)
    rec = p.state[KEY]
    assert rec["proved"] is False and rec["why"] == "cancel_failed" and rec["order_id"] == "oid-1"
    assert rec["cancel"]["ok"] is False and rec["cancel"]["error"] == "boom" and v.orders["oid-1"]["state"] == "new"
    assert _census(st, "s4_unproven") == 1 and _census(st, "rate_limited") == 0
    # an hour on: NO second probe; the first is cancelled again, and this time the venue accepts
    v2 = _NoClose(held={SLUG: -300}, ioc_fill=300.0, lift=300.0)
    v2.orders, v2.n = v.orders, v.n                     # the venue still lists oid-1; ids continue
    _tick(p, v2, now=NOW + 3600)
    assert not _places(v2) and v2.orders["oid-1"]["state"] == "cancelled"
    assert p.state[KEY]["order_id"] is None and p.state[KEY]["at"] == NOW + 3600, "cleared; the hour restarts"
    # THE SPEC: the order the venue kept is cancelled again, or named on this tick's record
    assert ("cancel", "oid-1", SLUG) in v2.calls or "oid-1" in json.dumps(p.state[KEY]), \
        "the un-cancelled probe order is neither retried nor remembered"


def test_GAP_a_429_on_the_probe_holds_it_for_the_hour_so_the_probe_abandons_one_tick_not_every_tick(monkeypatch):
    """E2's rule routes the 429 (rate_limited, the circuit, the tick
    abandoned) -- F3 folded, sound. What v2 left: a mismatch was
    recorded with `at` and held the probe for an hour; a 429 recorded
    nothing, so while the venue rate-limited creates the probe fired on
    the first live short book of EVERY tick, before that book's plan
    and before every book after it in the walk, and abandoned the tick
    each time: `tick_abandoned` N of N ticks, every exit on every book
    unmanaged for as long as the venue said 429. A placement's 429 has
    the same effect but is a needed order; the probe is a diagnostic.
    The owner decided (v3, GAP 3): the 429 writes {"proved": false,
    "why": "rate_limited", "at": now} -- the hour's hold -- so the four
    ticks probe once and abandon once (the per-tick assertions inverted:
    tick 0 alone probes and abandons; the key holds the 429). The spec
    line at the end is the round-2 one, verbatim."""
    _shorts_on(monkeypatch)

    class RateLimitError(Exception):
        status_code = 429
    p, b, v = _unproven_world(ioc_fill=300.0, lift=300.0, place_raises=RateLimitError("429"))
    abandoned = 0
    for i in range(4):
        st = _tick(p, v, now=NOW + 5 * i)
        abandoned += int(bool(st["abandoned"]))
        assert _census(st, "s4_probe_placed") == (1 if i == 0 else 0), i
        assert st.get("abandon_reason") == ("rate_limited" if i == 0 else None), i
        venue_pace._penalty_until = 0.0
    assert abandoned == 1 and p.state[KEY]["why"] == "rate_limited" and p.state[KEY]["at"] == NOW
    venue_pace._penalty_until = 0.0
    # the control: a mismatch holds the probe for the hour and abandons nothing
    p2, b2, v2 = _unproven_world(ioc_fill=300.0, lift=300.0, place=lambda *a: {
        "ok": False, "order_id": None, "status": "post_only_rejected", "fill_price": None,
        "filled_shares": 0.0, "raw": {"status_code": 400, "error": "would cross"}})
    st_a = _tick(p2, v2)
    st_b = _tick(p2, v2, now=NOW + 5)
    assert _census(st_a, "s4_probe_placed") == 1 and _census(st_b, "s4_probe_placed") == 0
    assert not st_a["abandoned"] and not st_b["abandoned"]
    # THE SPEC (the brief's "retried next hour"): a 429 holds the probe like a mismatch does
    assert abandoned <= 1, "the probe abandons every tick while the venue limits it"


# ----------------------------------------------------- 6. legacy / paths

def test_close_position_is_reachable_from_the_long_slippage_leg_alone_and_the_legacy_close_reader_stands():
    """RE-PINNED AT E31 (FILL lane 31, 2026-09-10). This test's subject --
    the ONE reader of `close_position`, the long flatten's slippage leg in
    `_flatten_send` -- is retired: E31 deletes `_flatten_send` and the
    flatten rests at the touch's own inside tick instead (E31 B). So the
    count goes 1 -> 0 and the pin is STRONGER than it was: no path of this
    worker can send an unpriced market order at all. The adapter's
    `pmus.close_position` is untouched (it is the P1 flatten's own
    function and other callers may exist), and the LEGACY CLOSE READER
    stands exactly as it did -- a row written `tif == 'CLOSE'` before this
    lane can still be in flight and must still be reconciled."""
    src = inspect.getsource(ml)
    assert src.count("t.pmus.close_position") == 0, "1 -> 0: the slippage leg is gone"
    assert not hasattr(ml, "_flatten_send"), "E31: the slippage leg's own function"
    from tests.test_e31_maker_only import _code
    for fn in (ml._act, ml._flatten_vanished, ml._s4_probe, ml._s4_probe_rate_limited, ml._place_reserved,
               ml._lost_response, ml._reconcile_placing, ml._place):
        # the paragraphs may still NAME the retired leg (that is the record); no line of
        # code may reach it
        assert "close_position" not in _code(fn), fn.__name__
    # what stands at the slippage leg's site: the flatten is a post-only rest like any other
    assert "return await _place(t, book, r, kind, BUY, limit, qty, his_px, p, plan)" in \
        inspect.getsource(ml._flatten_vanished)
    # the adapter keeps the call; nothing in the worker reaches it
    assert callable(pmus.close_position)
    # the legacy reader, byte for byte
    assert 'if o.get("tif") == "CLOSE":' in inspect.getsource(ml._reconcile_placing)
    assert "_reconcile_lost_close" in inspect.getsource(ml._reconcile_placing)


def test_the_probe_is_placed_on_a_high_priced_book_too_where_only_the_side_tells_the_spaces_apart(monkeypatch):
    """Pinned as built (for the owner's reading of the proof): on a
    book quoted 0.90/0.92 the probe goes at 0.85; in the OTHER space
    that is 'buy the long at <= 0.15', also non-crossing, so the
    post-only acceptance says nothing about the space there -- the
    venue's `side` is the whole proof on a high book. On a low book
    (0.30/0.32) the other-space reading crosses (buy at <= 0.75 vs ask
    0.32) and the post-only refusal fails closed on its own."""
    _shorts_on(monkeypatch)
    p, b, v = _unproven_world(bid=0.90, ask=0.92, ioc_fill=300.0, lift=300.0)
    _tick(p, v)
    assert [c[2:4] for c in _places(v)][0] == (0.85, 1) and p.state[KEY]["proved"] is True
    assert p.state[KEY]["bid"] == 0.90 and round(1 - 0.85, 2) < 0.92
    assert round(1 - ml._s4_probe_price(0.30), 2) >= 0.32
