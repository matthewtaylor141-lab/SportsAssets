"""Live position mirroring, phase P1: the pure rules (owner order
2026-09-02, "maximum effort and certainty"). Every rule the live
reconciler decides on top of analytics/mirror's plan is checked here
without a venue or a database, against the P1 panel synthesis: never
above his level, never crossing at placement, the take only after the
wait AND at/through, the paired-out flatten never marketed, the vanish
flatten only with every confirmation, a negative target 0 under the
long-only intent, caps that scale at the mark and can only be lowered
from the environment (never to zero where zero would mean uncapped),
the two waits that can only be lengthened, an unreadable or zero clip
or cap that is NO PLAN rather than a flatten, the booking arithmetic
under the caller's cursor, and the numbered P2 gate."""
import ast
import importlib
import inspect
import itertools
import math
import random
from dataclasses import replace as dc_replace
from decimal import Decimal

import pytest

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as r
from sportsassets.analytics import proof, roster_rules

INTENT = "ORDER_INTENT_BUY_LONG"


def _admitted(**over):
    f = r.AdmissionFacts(
        increases_ok=True, per_fill_usd=50.0, family="moneyline", per_side=False,
        market_closed=False, market_resolved=False, game_too_far_out=False,
        mapping_ok=True, edge_ok=True, cell_ok=True,
        legacy_row=False, slug_recent_copy=False, underdog_coholds=False,
        venue_net=0.0, kalshi_claimed=False, side_band_hit=False,
        snap_fresh=True, drift=0.01, books_live=0, opened_today=0, first_fill_ok=True)
    for k, v in over.items():
        setattr(f, k, v)
    return f


def _p2_numbers(**over):
    n = {"closed_books": 34, "games": 31, "ci95": [0.012, 0.081],
         "at_or_better": 1.0, "maker_share": 0.72, "take_slip_median": 0.0,
         "frozen_ticks": 3, "live_ticks": 4000, "frozen_unresolved": 0,
         "wrong_sign_trip": 0, "order_lost": 0, "overfill": 0,
         "reaper_touched_mirror": 0, "book_settle_disagree": 0,
         "shadow_live_disagree": 0, "drift_p90": 0.03, "capture": 0.61,
         "census_missing": [], "why_overflow": False}
    n.update(over)
    return n


# ------------------------------------------------------------ constants

def test_caps_carry_the_spec_defaults_and_reuse_the_shared_ones():
    assert r.MIRROR_NET_CAP_USD == mi.MARKET_NET_CAP_USD == 250.0
    assert (r.MIRROR_MAX_LIVE_BOOKS, r.MIRROR_MAX_BOOKS_PER_DAY) == (5, 5)
    assert r.MIRROR_DAY_USD == 1250.0 and r.MIRROR_LOSS_STOP_USD == 250.0
    assert r.MIRROR_MAX_ORDER_OPS_PER_TICK == 6 and r.MIRROR_MAX_REPLACES_PER_HOUR == 12
    assert r.MIRROR_REST_TTL_S == 600.0 and r.MIRROR_TAKE_AFTER_S == 120.0
    assert r.MIRROR_FLATTEN_REST_S == 300.0 and r.MIRROR_FLAT_CLOSE_S == 3600.0
    assert r.MIRROR_DRIFT_MAX == 0.05
    assert r.MIRROR_FROZEN_ALERT_S == 600.0 and r.MIRROR_FROZEN_NAME_TICKS == 3
    assert r.MIRROR_FAMILIES == frozenset({"moneyline"})
    # the shared numbers are the shared objects, never restated
    assert r.MIN_PROOF_CLUSTERS is proof.MIN_PROOF_CLUSTERS
    assert r.Z95 is proof.Z95
    assert r.MIN_N_DEMOTE is roster_rules.MIN_N_DEMOTE
    assert r.MIN_N_PROMOTE is roster_rules.MIN_N_PROMOTE
    # the mirror anchors on its OWN constant, deliberately not the
    # per-fill lane's clip: that clip rose to $250 on 2026-09-04 and the
    # mirror must not resize as a side effect of it
    assert r.MIRROR_ANCHOR_CLIP_USD is roster_rules.MIRROR_ANCHOR_CLIP_USD
    assert r.MIRROR_ANCHOR_CLIP_USD == 50.0
    assert not hasattr(r, "MEASURE_CLIP_USD"), "the mirror must not read the per-fill clip"
    # and neither may the worker: harmless while the two numbers agree,
    # but it silently re-couples the mirror so the NEXT per-fill clip
    # change rescales every target -- the hazard the anchor exists for
    import inspect as _i
    from sportsassets.workers import mirror_live as _ml
    _src = _i.getsource(_ml)
    assert "MEASURE_CLIP_USD" not in _src, "the mirror worker must not read the per-fill clip"
    src = inspect.getsource(r)
    for restated in ("MIN_PROOF_CLUSTERS =", "Z95 =", "MIN_N_DEMOTE =", "MIN_N_PROMOTE =",
                     "MEASURE_CLIP_USD =", "MIN_MOVE_USD =", "MIN_MOVE_FRAC =",
                     "MARKET_NET_CAP_USD ="):
        assert restated not in src, restated


def test_env_override_only_lowers_a_cap(monkeypatch):
    monkeypatch.setenv("MIRROR_TEST_CAP", "9999")
    assert r.capped_env("MIRROR_TEST_CAP", 250.0) == 250.0
    monkeypatch.setenv("MIRROR_TEST_CAP", "25")
    assert r.capped_env("MIRROR_TEST_CAP", 250.0) == 25.0
    for bad in ("", "  ", "lots", "nan", "inf"):
        monkeypatch.setenv("MIRROR_TEST_CAP", bad)
        assert r.capped_env("MIRROR_TEST_CAP", 250.0) == 250.0, bad
    monkeypatch.setenv("MIRROR_TEST_CAP", "-5")
    assert r.capped_env("MIRROR_TEST_CAP", 250.0) == 0.0
    monkeypatch.delenv("MIRROR_TEST_CAP")
    assert r.capped_env("MIRROR_TEST_CAP", 250.0) == 250.0
    # the module constants go through the same helper: a raise is
    # ignored at import, a lowering is honoured
    monkeypatch.setenv("MIRROR_MAX_LIVE_BOOKS", "50")
    monkeypatch.setenv("MIRROR_NET_CAP_USD", "25")
    try:
        mod = importlib.reload(r)
        assert mod.MIRROR_MAX_LIVE_BOOKS == 5 and mod.MIRROR_NET_CAP_USD == 25.0
    finally:
        monkeypatch.delenv("MIRROR_MAX_LIVE_BOOKS")
        monkeypatch.delenv("MIRROR_NET_CAP_USD")
        importlib.reload(r)
    assert r.MIRROR_MAX_LIVE_BOOKS == 5 and r.MIRROR_NET_CAP_USD == 250.0


def test_a_zero_or_negative_net_cap_env_never_removes_the_cap(monkeypatch):
    # mi.target_shares caps only while cap_usd > 0, so a zero cap there
    # is NO cap: the reader keeps MIRROR_NET_CAP_USD on a positive
    # floor, and an env of 0 or less lands on the floor, never on the
    # uncapped path (the reviewer's probe: 0.5 x 24,423 at 0.4574 read
    # 12,211 shares uncapped)
    assert r.MIRROR_NET_CAP_FLOOR_USD > 0
    for v in ("0", "0.0", "-1", "-250", "0.5"):
        monkeypatch.setenv("MIRROR_TEST_CAP", v)
        assert r.capped_env("MIRROR_TEST_CAP", 250.0, floor=r.MIRROR_NET_CAP_FLOOR_USD) == 1.0, v
    monkeypatch.setenv("MIRROR_TEST_CAP", "25")
    assert r.capped_env("MIRROR_TEST_CAP", 250.0, floor=1.0) == 25.0
    monkeypatch.setenv("MIRROR_TEST_CAP", "9999")
    assert r.capped_env("MIRROR_TEST_CAP", 250.0, floor=1.0) == 250.0
    monkeypatch.delenv("MIRROR_TEST_CAP")
    # the default floor keeps the old reading for counts and rooms,
    # where zero means none and is the most closed setting
    monkeypatch.setenv("MIRROR_TEST_CAP", "-5")
    assert r.capped_env("MIRROR_TEST_CAP", 250.0) == 0.0
    monkeypatch.delenv("MIRROR_TEST_CAP")
    for v in ("0", "-1"):
        monkeypatch.setenv("MIRROR_NET_CAP_USD", v)
        try:
            mod = importlib.reload(r)
            assert mod.MIRROR_NET_CAP_USD == mod.MIRROR_NET_CAP_FLOOR_USD == 1.0
            t = mod.mirror_target(0.5, 24423.0, 0.4574, 50.0)
            assert t["capped"] is True and t["target"] == int(1.0 / 0.4574) == 2, t
            assert t["target"] < 12211
        finally:
            monkeypatch.delenv("MIRROR_NET_CAP_USD")
            importlib.reload(r)
    assert r.MIRROR_NET_CAP_USD == 250.0


def test_env_override_only_raises_a_wait(monkeypatch):
    # the take wait and the flatten rest are WAITS before a more
    # aggressive action, not caps: an override may only LENGTHEN them
    monkeypatch.setenv("MIRROR_TEST_WAIT", "30")
    assert r.min_wait_env("MIRROR_TEST_WAIT", 120.0) == 120.0
    monkeypatch.setenv("MIRROR_TEST_WAIT", "300")
    assert r.min_wait_env("MIRROR_TEST_WAIT", 120.0) == 300.0
    for bad in ("", "  ", "soon", "nan", "inf", "-5"):
        monkeypatch.setenv("MIRROR_TEST_WAIT", bad)
        assert r.min_wait_env("MIRROR_TEST_WAIT", 120.0) == 120.0, bad
    # 'inf' / '1e400' are not numbers and fall back to the default; a
    # large FINITE wait is the only spelling of "never take"
    for never in ("inf", "Infinity", "1e400", "-inf"):
        monkeypatch.setenv("MIRROR_TEST_WAIT", never)
        assert r.min_wait_env("MIRROR_TEST_WAIT", 120.0) == 120.0, never
    monkeypatch.setenv("MIRROR_TEST_WAIT", "1e9")
    assert r.min_wait_env("MIRROR_TEST_WAIT", 120.0) == 1e9
    monkeypatch.setenv("MIRROR_TAKE_AFTER_S", "1e9")
    try:
        mod = importlib.reload(r)
        assert mod.MIRROR_TAKE_AFTER_S == 1e9
        assert mod.take_allowed(1e9 - 1, None, 0.0, 0.46, 0.47, 0.47, mod.BUY) is False
        assert mod.take_allowed(1e9, None, 0.0, 0.46, 0.47, 0.47, mod.BUY) is True
    finally:
        monkeypatch.delenv("MIRROR_TAKE_AFTER_S")
        importlib.reload(r)
    monkeypatch.delenv("MIRROR_TEST_WAIT")
    assert r.min_wait_env("MIRROR_TEST_WAIT", 120.0) == 120.0
    # the module constants go through the wait helper: a shortening is
    # ignored at import, a lengthening is honoured
    monkeypatch.setenv("MIRROR_TAKE_AFTER_S", "30")
    monkeypatch.setenv("MIRROR_FLATTEN_REST_S", "60")
    try:
        mod = importlib.reload(r)
        assert mod.MIRROR_TAKE_AFTER_S == 120.0 and mod.MIRROR_FLATTEN_REST_S == 300.0
        monkeypatch.setenv("MIRROR_TAKE_AFTER_S", "600")
        monkeypatch.setenv("MIRROR_FLATTEN_REST_S", "900")
        mod = importlib.reload(r)
        assert mod.MIRROR_TAKE_AFTER_S == 600.0 and mod.MIRROR_FLATTEN_REST_S == 900.0
        # the lengthened wait is the one take_allowed defaults to
        assert mod.take_allowed(599.0, None, 0.0, 0.46, 0.47, 0.47, mod.BUY) is False
        assert mod.take_allowed(600.0, None, 0.0, 0.46, 0.47, 0.47, mod.BUY) is True
    finally:
        monkeypatch.delenv("MIRROR_TAKE_AFTER_S")
        monkeypatch.delenv("MIRROR_FLATTEN_REST_S")
        importlib.reload(r)
    assert r.MIRROR_TAKE_AFTER_S == 120.0 and r.MIRROR_FLATTEN_REST_S == 300.0
    # the source records the decision beside the two constants
    src = inspect.getsource(r)
    assert 'min_wait_env("MIRROR_TAKE_AFTER_S"' in src
    assert 'min_wait_env("MIRROR_FLATTEN_REST_S"' in src
    assert 'capped_env("MIRROR_TAKE_AFTER_S"' not in src
    assert 'capped_env("MIRROR_FLATTEN_REST_S"' not in src


def test_a_safe_tick_can_still_cancel_and_a_rest_still_lives(monkeypatch):
    # the ops budget floors at 1 (a cancel-only tick must write once)
    # and the rest TTL at 30 s (an env 0 must not re-place every tick)
    for ops, ttl, want_ops, want_ttl in (("0", "0", 1, 30.0), ("-3", "-1", 1, 30.0),
                                          ("3", "60", 3, 60.0), ("99", "9999", 6, 600.0)):
        monkeypatch.setenv("MIRROR_MAX_ORDER_OPS_PER_TICK", ops)
        monkeypatch.setenv("MIRROR_REST_TTL_S", ttl)
        try:
            mod = importlib.reload(r)
            assert mod.MIRROR_MAX_ORDER_OPS_PER_TICK == want_ops, ops
            assert mod.MIRROR_REST_TTL_S == want_ttl, ttl
        finally:
            monkeypatch.delenv("MIRROR_MAX_ORDER_OPS_PER_TICK")
            monkeypatch.delenv("MIRROR_REST_TTL_S")
            importlib.reload(r)
    assert r.MIRROR_MAX_ORDER_OPS_PER_TICK == 6 and r.MIRROR_REST_TTL_S == 600.0


def test_the_rules_module_is_pure():
    # imports: the standard library and analytics siblings only -- no
    # executor, venue adapter, worker or database module can be reached
    src = inspect.getsource(r)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [("." * node.level) + (node.module or "")]
        else:
            continue
        for name in names:
            assert name in ("math", "os", "dataclasses", "typing", "__future__",
                            ".", ".mirror", ".proof", ".roster_rules"), name
    for forbidden in ("asyncpg", "httpx", "get_pool", "await ", "async def", "time.time",
                      "os.environ.get(\"PMUS", "ingestion_state"):
        assert forbidden not in src, forbidden


# --------------------------------------------------------------- target

def test_negative_target_is_zero_and_the_intent_is_buy_long():
    assert r.ORDER_INTENT == INTENT
    t = r.mirror_target(0.05, -24423.06, 0.4574, 50.0)
    assert t["target"] == 0 and t["refusal"] == "short_side_refused" and t["intent"] == INTENT
    assert t["raw"] < 0
    assert "BUY_SHORT" not in inspect.getsource(r.mirror_target)
    # no ratio / no mark is NO PLAN, never "target zero, flatten"
    assert r.mirror_target(None, 1000, 0.5, 50.0)["target"] is None
    assert r.mirror_target(None, 1000, 0.5, 50.0)["refusal"] == "no_ratio"
    assert r.mirror_target(0.05, 1000, None, 50.0)["refusal"] == "no_mark"
    assert r.mirror_target(0.05, 1000, 1.0, 50.0)["refusal"] == "no_mark"
    assert r.mirror_target("lots", 1000, 0.5, 50.0)["refusal"] == "no_ratio"
    # a demoted clip is NO PLAN, named -- never a target of 0, which
    # mi.plan would read as a flatten
    z = r.mirror_target(0.05, 1000, 0.5, 0.0)
    assert z["target"] is None and z["refusal"] == "clip_zero"


def test_a_zero_or_negative_cap_is_no_exposure_never_no_cap():
    # the reviewer's probe: 0.5 x 24,423 at 0.4574 with cap_usd=0.0
    # read 12,211 shares, because mi.target_shares caps only while
    # cap_usd > 0. Here a cap at or under zero is NO PLAN, by name,
    # before target_shares runs
    for cap in (0.0, 0, -1, -1.0, -250.0, math.nan, math.inf, None, "x", False):
        t = r.mirror_target(0.5, 24423.0, 0.4574, 50.0, cap_usd=cap)
        assert t["target"] is None, (cap, t)
        assert t["refusal"] == "net_cap_zero", (cap, t)
        assert t["raw"] == 0.0 and t["capped"] is False and t["intent"] == INTENT
    # never a target above 0 from a zero cap, whatever the book
    for net in (24423.0, 1.0, -5000.0, 0.0):
        assert r.mirror_target(0.5, net, 0.4574, 50.0, cap_usd=0.0)["target"] is None
        assert r.mirror_target(0.5, net, 0.4574, 50.0, cap_usd=-1)["target"] is None
    # the cap is named before the ratio, the mark and the clip: no
    # exposure is the first thing to say when there is none allowed
    assert r.mirror_target(None, 1.0, None, None, cap_usd=0.0)["refusal"] == "net_cap_zero"
    # the smallest positive cap still caps
    one = r.mirror_target(0.5, 24423.0, 0.4574, 50.0, cap_usd=r.MIRROR_NET_CAP_FLOOR_USD)
    assert one["capped"] is True and one["target"] == int(1.0 / 0.4574) == 2
    # and the module default is that positive floor or above
    assert r.MIRROR_NET_CAP_USD >= r.MIRROR_NET_CAP_FLOOR_USD > 0
    assert inspect.signature(r.mirror_target).parameters["cap_usd"].default == r.MIRROR_NET_CAP_USD


def test_an_unreadable_or_zero_clip_is_no_plan_never_a_flatten():
    # the reviewer's chain: clip None -> 0.0 -> ratio_eff 0 -> target
    # 0 -> mi.plan(0, 40, 40) -> SELL_LONG 40 'flatten'. Now: no plan
    for clip, name in ((None, "clip_unreadable"), ("lots", "clip_unreadable"),
                       (math.nan, "clip_unreadable"), (math.inf, "clip_unreadable"),
                       (True, "clip_unreadable"),
                       (0.0, "clip_zero"), (0, "clip_zero"), (-5.0, "clip_zero")):
        t = r.mirror_target(0.05, 1000.0, 0.5, clip)
        assert t["target"] is None and t["refusal"] == name, (clip, t)
        assert t["raw"] == 0.0 and t["capped"] is False
        # no SELL plan is derivable from it: mi.plan cannot even take
        # the target, and the resting order is cancelled by name so
        # the worker HOLDS the book
        with pytest.raises(TypeError):
            mi.plan(t["target"], 40, 40, mi.Book(bid=0.49, ask=0.51), 0.5, 0.5)
        o = r.OpenOrder(r.BUY, 0.47, 100, 100, placed_at=1000.0)
        assert r.keep_or_replace(o, None, 1100.0) == "no_plan"
        assert r.keep_or_replace(o, None, 1100.0, cancel_reason=t["refusal"]) == name
    assert r.mirror_target(0.05, 1000.0, 0.5, 0.0)["ratio_eff"] == 0.0
    assert r.mirror_target(0.05, 1000.0, 0.5, None)["ratio_eff"] is None
    # the clip scales the ratio at book OPEN only: after open the
    # worker passes the book's stored ratio with the anchor clip
    # (scale 1), so a later clip cut never re-rates the book
    opened = r.mirror_target(0.1, 1000, 0.5, 25.0)
    assert opened["ratio_eff"] == 0.05 and opened["target"] == 50
    later = r.mirror_target(opened["ratio_eff"], 1000, 0.5, r.MIRROR_ANCHOR_CLIP_USD)
    assert later["ratio_eff"] == 0.05 and later["target"] == 50
    # and the increase re-check is where a later clip cut bites
    assert r.admission(_admitted(per_fill_usd=0.0), increase=True) == "clip_zero"
    assert r.admission(_admitted(per_fill_usd=None), increase=True) == "clip_unreadable"


def test_an_unread_position_is_no_plan_never_a_flatten():
    # mi.target_shares reads `net or 0.0`: None/False/'' -> 0 -> target
    # 0 -> a SELL of the whole book; inf -> a cap-sized BUY; nan/'x'
    # raised. Now every one is NO PLAN, by name, before it is reached
    for net in (None, False, True, "", "x", "5", "24423", math.nan, math.inf, -math.inf, b"5"):
        t = r.mirror_target(0.5, net, 0.4574, 50.0)
        assert t["target"] is None and t["refusal"] == "no_position", (net, t)
        assert t["raw"] == 0.0 and t["capped"] is False and t["ratio_eff"] is None
        with pytest.raises(TypeError):
            mi.plan(t["target"], 40, 40, mi.Book(bid=0.49, ask=0.51), 0.5, 0.5)
        o = r.OpenOrder(r.BUY, 0.47, 100, 100, placed_at=1000.0)
        assert r.keep_or_replace(o, None, 1100.0, cancel_reason=t["refusal"]) == "no_position"
    # a net of exactly 0 IS a reading: he holds nothing, target 0
    z = r.mirror_target(0.5, 0.0, 0.4574, 50.0)
    assert z["target"] == 0 and z["refusal"] is None
    assert r.mirror_target(0.5, 0, 0.4574, 50.0)["target"] == 0
    # named after the mark and before the clip
    assert r.mirror_target(0.5, None, None, 50.0)["refusal"] == "no_mark"
    assert r.mirror_target(0.5, None, 0.5, None)["refusal"] == "no_position"
    # nothing that is not a number reaches mi.target_shares: every
    # argument the wrapper forwards has been read by the one rule
    for bad in (None, True, "x", math.nan, math.inf):
        for args in ((bad, 1000.0, 0.5, 50.0), (0.5, bad, 0.5, 50.0),
                     (0.5, 1000.0, bad, 50.0), (0.5, 1000.0, 0.5, bad)):
            assert r.mirror_target(*args)["target"] is None, args
        assert r.mirror_target(0.5, 1000.0, 0.5, 50.0, cap_usd=bad)["target"] is None


def test_a_caller_can_only_tighten_a_cap_never_raise_one():
    # the environment is downward-only; so is every argument that
    # shadows a constant (the reviewer's probes: cap_usd=1e9 read
    # 12,211 shares uncapped, ttl_s=1e9 kept an order at age 1e8,
    # drift_max=1.0 allowed increases on 50% drift, flat_close_s=0
    # cashed out at once, ratio=100 ignored mi.RATIO_MAX)
    capped = int(r.MIRROR_NET_CAP_USD / 0.4574)
    for cap in (1e9, 1e308, Decimal("1e6"), 251.0, r.MIRROR_NET_CAP_USD + 1e-9):
        t = r.mirror_target(0.5, 24423.0, 0.4574, 50.0, cap_usd=cap)
        assert t["capped"] is True and t["target"] == capped, (cap, t)
    assert r.mirror_target(0.5, 24423.0, 0.4574, 50.0, cap_usd=25.0)["target"] == int(25.0 / 0.4574)
    # the ratio is capped at mi.RATIO_MAX -- the shared object, never restated
    assert r.mirror_target(100.0, 100.0, 0.5, 50.0)["target"] == int(mi.RATIO_MAX * 100.0) == 100
    assert r.mirror_target(100.0, 100.0, 0.5, 50.0)["ratio_eff"] == mi.RATIO_MAX
    assert r.mirror_target(1e300, 1e10, 0.5, 50.0)["target"] == int(r.MIRROR_NET_CAP_USD / 0.5)
    assert r.mirror_target(0.5, 100.0, 0.5, 50.0)["ratio_eff"] == 0.5
    assert r.mirror_target(mi.RATIO_MAX, 100.0, 0.5, 50.0)["target"] == 100
    # keep_or_replace: ttl_s only shortens the order's life
    o = r.OpenOrder(r.BUY, 0.47, 100, 100, placed_at=0.0)
    p = mi.Plan(r.BUY, 100, 0.47, "x")
    assert r.keep_or_replace(o, p, 1e8, ttl_s=1e9) == "replace"
    assert r.keep_or_replace(o, p, r.MIRROR_REST_TTL_S, ttl_s=1e9) == "replace"
    assert r.keep_or_replace(o, p, r.MIRROR_REST_TTL_S - 1, ttl_s=1e9) == "keep"
    assert r.keep_or_replace(o, p, 100.0, ttl_s=60.0) == "replace"
    assert r.keep_or_replace(o, p, 59.0, ttl_s=60.0) == "keep"
    # drift_rule: drift_max only tightens the bound
    assert r.drift_rule(1000.0, 500.0, True, False, drift_max=1.0).refusal == "drift"
    assert r.drift_rule(1000.0, 500.0, True, False, drift_max=math.inf).refusal == "drift"
    assert r.drift_rule(1000.0, 960.0, True, False, drift_max=1.0).increase_ok is True
    assert r.drift_rule(1000.0, 960.0, True, False, drift_max=0.01).refusal == "drift"
    # episode_close: flat_close_s only lengthens the flat wait
    flat = r.BookState(ledger_net=0.0, gross_buy_usd=10.0)
    for short in (0.0, -5.0, 1.0, r.MIRROR_FLAT_CLOSE_S - 1):
        assert r.episode_close(flat, False, False, 0.0, 0, flat_close_s=short) is None, short
        assert r.episode_close(flat, False, False, r.MIRROR_FLAT_CLOSE_S - 1, 0, flat_close_s=short) is None, short
        assert r.episode_close(flat, False, False, r.MIRROR_FLAT_CLOSE_S, 0, flat_close_s=short) == "cashed_out", short
    longer = r.MIRROR_FLAT_CLOSE_S * 2
    assert r.episode_close(flat, False, False, r.MIRROR_FLAT_CLOSE_S, 0, flat_close_s=longer) is None
    assert r.episode_close(flat, False, False, longer, 0, flat_close_s=longer) == "cashed_out"


def test_the_mark_must_be_on_the_ladder():
    # a subnormal mark (1e-320) loosened the cap to cap/mark shares
    for m in (1e-320, 0.005, 0.0099999, 0.991, 0.999, 1 - 1e-16, 0.0, -0.0, 1.0, -0.5, 1e308):
        t = r.mirror_target(0.5, 24423.0, m, 50.0)
        assert t["target"] is None and t["refusal"] == "no_mark", (m, t)
    # the ladder's edges are marks, and the cap scales at them
    low = r.mirror_target(0.5, 1e6, 0.01, 50.0)
    assert low["capped"] is True and low["target"] == int(r.MIRROR_NET_CAP_USD / 0.01)
    high = r.mirror_target(0.5, 1e6, 0.99, 50.0)
    assert high["capped"] is True and high["target"] == int(r.MIRROR_NET_CAP_USD / 0.99)
    assert r.mirror_target(0.5, 24423.0, 0.01, 50.0)["target"] == 12211    # under the cap at 0.01


def test_an_exotic_float_that_raises_is_not_a_number():
    class Boom:
        def __float__(self):
            raise RuntimeError("boom")

    class Neg:
        def __float__(self):
            return -0.0

    for fn in (r.buy_wire, r.sell_wire):
        assert fn(Boom()) is None
    assert r.mirror_target(0.5, Boom(), 0.5, 50.0)["refusal"] == "no_position"
    assert r.mirror_target(Boom(), 100.0, 0.5, 50.0)["refusal"] == "no_ratio"
    assert r.mirror_target(0.5, 100.0, Boom(), 50.0)["refusal"] == "no_mark"
    assert r.mirror_target(0.5, 100.0, 0.5, Boom())["refusal"] == "clip_unreadable"
    assert r.room_scale(Boom(), 0.5, 1.0, 1.0, 1.0, 1.0) == 0
    assert r.room_scale(10, Boom(), 1.0, 1.0, 1.0, 1.0) == 0
    assert r.select_flatten(0, Boom(), 0.0, True, 0.0, 0.0, True, True) == "vanish_unconfirmed"
    assert r.book_buy(r.BookState(), Boom(), 0.5).refusal == "bad_delta"
    assert r.book_buy(r.BookState(), 5, Boom()).refusal == "bad_price"
    assert r.drift_rule(Boom(), 1.0, True, False).refusal == "snapshot_stale"
    assert r.at_or_through(r.BUY, 0.46, Boom(), 0.47) is False
    assert r.admission(_admitted(per_fill_usd=Boom())) == "clip_unreadable"
    assert r.p2_verdict(_p2_numbers(maker_share=Boom()))[1] == ["unreadable:maker_share"]
    # a negative zero is a reading of nothing, not garbage
    assert r.mirror_target(0.5, Neg(), 0.5, 50.0)["target"] == 0
    assert r.mirror_target(0.5, Decimal("-0"), 0.5, 50.0)["target"] == 0


def test_cap_scales_the_target_at_the_mark_and_a_lowered_cap_scales_further():
    t = r.mirror_target(50.0 / 861.8, 24423.06, 0.4574, 50.0)
    assert t["capped"] is True and t["target"] == int(250.0 / 0.4574)
    small = r.mirror_target(50.0 / 861.8, 24423.06, 0.4574, 50.0, cap_usd=25.0)
    assert small["capped"] is True and small["target"] == int(25.0 / 0.4574)
    un = r.mirror_target(50.0 / 861.8, 2780.0, 0.31, 50.0)
    assert un["capped"] is False and un["target"] == int(50.0 / 861.8 * 2780.0)


def test_ratio_anchors_to_the_per_fill_clip_and_never_rises_above_the_shadow_ratio():
    full = r.mirror_target(0.1, 1000, 0.5, 50.0)
    assert full["ratio_eff"] == 0.1 and full["target"] == 100
    half = r.mirror_target(0.1, 1000, 0.5, 25.0)
    assert half["ratio_eff"] == 0.05 and half["target"] == 50
    promoted = r.mirror_target(0.1, 1000, 0.5, 250.0)
    assert promoted["ratio_eff"] == 0.1 and promoted["target"] == 100


# --------------------------------------------------------------- prices

def test_buy_wire_never_above_his_level_and_never_above_the_bid():
    cents = [i / 100.0 for i in range(1, 100)]
    for his in cents:
        for bid in cents:
            # 1,000 shares so the move clears mi.plan's $5 dead band at
            # every mark and a price exists to floor
            p = mi.plan(1000, 0, 0, mi.Book(bid=bid, ask=min(0.99, bid + 0.01)), his, bid)
            w = r.buy_wire(p.price)
            assert w is not None
            assert w <= his + 1e-9 and w <= bid + 1e-9, (his, bid, w)
    # off-cent levels floor, never round up
    assert r.buy_wire(0.474) == 0.47 and r.buy_wire(0.479) == 0.47
    assert r.buy_wire(0.005) is None and r.buy_wire(1.0) is None and r.buy_wire(None) is None
    assert r.buy_wire("x") is None


def test_buy_wire_agrees_with_the_executor_source():
    from sportsassets import live_executor as le
    for i in range(10, 990):
        px = i / 1000.0
        assert r.buy_wire(px) == le.rest_tick(le.wire_limit(px, INTENT), INTENT), px
    assert r.buy_wire(0.474) == le.rest_tick(le.wire_limit(0.474, INTENT), INTENT) == 0.47


def test_sell_wire_is_ceiled_at_his_equivalent_and_capped_at_the_top_tick():
    for q in [i / 100.0 for i in range(1, 100)]:
        his_equiv = round(1.0 - q, 4)
        for ask in [i / 100.0 for i in range(1, 100)]:
            p = mi.plan(0, 100, 100, mi.Book(bid=max(0.01, ask - 0.01), ask=ask), his_equiv, ask)
            w = r.sell_wire(p.price)
            assert w is not None
            assert w >= min(0.99, his_equiv) - 1e-9 and w >= min(0.99, ask) - 1e-9
            assert w <= 0.99
    assert r.sell_wire(0.531) == 0.54 and r.sell_wire(0.995) == 0.99 and r.sell_wire(1.2) == 0.99
    assert r.sell_wire(0) is None and r.sell_wire(None) is None


def test_a_rest_never_crosses_at_placement():
    for bid_c in range(1, 98):
        bid = bid_c / 100.0
        ask = round(bid + 0.01, 2)
        for his in (bid - 0.03, bid, bid + 0.02, ask, ask + 0.05):
            if not (0.0 < his < 1.0):
                continue
            p = mi.plan(1000, 0, 0, mi.Book(bid=bid, ask=ask), his, bid)
            w = r.buy_wire(p.price)
            assert w < ask, (bid, ask, his, w)          # a BUY under the ask cannot take
            s = mi.plan(0, 1000, 1000, mi.Book(bid=bid, ask=ask), round(1 - his, 4), ask)
            sw = r.sell_wire(s.price)
            assert sw > bid or sw == 0.99, (bid, ask, his, sw)   # a SELL over the bid cannot take
    assert r.plan_wire(mi.Plan(None, 0, None, "on target")) is None


def test_buy_price_and_sell_price_floor_and_ceil_the_unrounded_level():
    # the defect: mi.plan rounds min(his, bid) to 4 places BEFORE the
    # floor, so his 0.47996 is plan.price 0.48 and the wire 0.48 is a
    # cent ABOVE him; buy_price floors the exact minimum
    p = mi.plan(1000, 0, 0, mi.Book(bid=0.49, ask=0.50), 0.47996, 0.49)
    assert p.price == 0.48 and r.buy_wire(p.price) == 0.48 > 0.47996   # the shadow's figure
    assert r.buy_price(0.47996, 0.49) == 0.47 <= 0.47996                # the worker's wire
    assert r.buy_price(0.47, 0.49) == 0.47 and r.buy_price(0.49, 0.47) == 0.47
    assert r.buy_price(0.479999, 0.999999) == 0.47
    # sells: his equivalent 0.52004 rounds to 0.52, UNDER him; the
    # ceiling of the exact maximum is 0.53
    s = mi.plan(0, 100, 100, mi.Book(bid=0.50, ask=0.51), 0.52004, 0.51)
    assert s.price == 0.52 and r.sell_wire(s.price) == 0.52 < 0.52004
    assert r.sell_price(0.52004, 0.51) == 0.53 >= 0.52004
    assert r.sell_price(0.52, 0.51) == 0.52 and r.sell_price(0.51, 0.52) == 0.52
    assert r.sell_price(0.995, 0.5) == 0.99 and r.sell_price(0.5, 0.985) == 0.99
    # no fact, no price: his level unreadable is not "rest at the bid"
    for bad in (None, 0.0, 1.0, -0.5, math.nan, math.inf, True, "0.47", b"1"):
        assert r.buy_price(bad, 0.49) is None, bad
        assert r.buy_price(0.47, bad) is None, bad
        assert r.sell_price(bad, 0.51) is None, bad
        assert r.sell_price(0.52, bad) is None, bad
    assert r.buy_price(0.005, 0.5) is None                # floors under the ladder
    # keep_or_replace compares the resting cent to the worker's wire,
    # not to the plan's rounded price -- else the 0.47996 book would
    # replace every tick
    o = r.OpenOrder(r.BUY, 0.47, 1000, 1000, placed_at=1000.0)           # the plan's 1000 shares
    assert r.keep_or_replace(o, p, 1100.0) == "replace"                  # plan_wire 0.48 != 0.47
    assert r.keep_or_replace(o, p, 1100.0, wire=r.buy_price(0.47996, 0.49)) == "keep"
    assert r.keep_or_replace(o, p, 1100.0, wire=None) == "no_price"
    assert r.keep_or_replace(o, p, 1100.0, wire=0.48) == "replace"


def test_wire_property_over_six_decimal_levels():
    # 10,000 random 6-decimal levels x random 2-decimal books: a BUY
    # wire is never above him and never above the bid; a SELL wire is
    # never under min(0.99, his equivalent) nor under min(0.99, ask)
    rng = random.Random(20260902)
    n = 0
    while n < 10_000:
        his = round(rng.uniform(0.01, 0.999999), 6)
        bid = round(rng.uniform(0.01, 0.98), 2)
        w = r.buy_price(his, bid)
        if w is None:
            assert min(his, bid) < 0.01, (his, bid)
            continue
        assert w <= his + 1e-12 and w <= bid + 1e-12, (his, bid, w)
        assert w >= min(his, bid) - 0.01, (his, bid, w)                 # floored, not dropped
        equiv = round(rng.uniform(0.000001, 0.999999), 6)
        ask = round(rng.uniform(0.02, 0.99), 2)
        sw = r.sell_price(equiv, ask)
        assert sw is not None and sw <= 0.99
        assert sw >= min(0.99, equiv) - 1e-12 and sw >= min(0.99, ask) - 1e-12, (equiv, ask, sw)
        assert sw <= max(equiv, ask) + 0.01 + 1e-12                   # ceiled, not lifted
        n += 1
    # every 5-decimal level on the whole ladder, exhaustively
    for i in range(1000, 99999):
        his = i / 100000.0
        w = r.buy_price(his, 0.99)
        assert w is not None and w <= his + 1e-12, (his, w)
        sw = r.sell_price(his, 0.01)
        assert sw is not None and sw >= min(0.99, his) - 1e-12, (his, sw)


def test_wires_never_raise():
    for bad in (math.inf, -math.inf, math.nan, True, False, "0.5", b"x", None, 1e308, -1e308, 10**400):
        assert r.buy_wire(bad) is None, bad
        assert r.sell_wire(bad) in (None, 0.99), bad
    assert r.sell_wire(1e308) == 0.99 and r.sell_wire(10**400) is None
    assert r.sell_wire(True) is None and r.buy_wire(True) is None
    assert r.sell_wire(0.5) == 0.5 and r.sell_wire(0.501) == 0.51


def test_wire_property_over_full_precision_floats():
    # the floor's own round(..., 6) reads a level a hair under a cent
    # as the cent (0.479999999 -> 48.0): the post-condition steps it
    # down, so the guarantee holds at ANY precision, not just the
    # 6-8 decimals ingestion writes
    for his, want in ((0.479999999, 0.47), (0.4799999999, 0.47), (0.5799999999, 0.57),
                      (0.4699999999999, 0.46), (0.47 - 1e-9, 0.46), (0.47 + 1e-9, 0.47),
                      (0.5800000001, 0.58), (0.11995, 0.11), (0.12996, 0.12)):
        w = r.buy_price(his, 0.99)
        assert w == want and w <= his, (his, w)
    for eq, want in ((0.520000001, 0.53), (0.5200000001, 0.53), (0.53000000001, 0.54),
                     (0.52 + 1e-9, 0.53), (0.52, 0.52), (0.52004, 0.53)):
        sw = r.sell_price(eq, 0.01)
        assert sw == want and sw >= eq, (eq, sw)
    rng = random.Random(3)
    for _ in range(200_000):
        his = rng.uniform(0.01, 0.999999)
        bid = rng.uniform(0.01, 0.999999)
        w = r.buy_price(his, bid)
        assert w is not None and w <= his and w <= bid, (his, bid, w)
        assert w >= min(his, bid) - 0.02, (his, bid, w)               # the floor and at most one step
        equiv = rng.uniform(0.000001, 0.999999)
        ask = rng.uniform(0.000001, 0.999999)
        sw = r.sell_price(equiv, ask)
        assert sw is not None and sw <= 0.99
        assert sw >= min(0.99, equiv) and sw >= min(0.99, ask), (equiv, ask, sw)
        assert sw == 0.99 or sw <= max(equiv, ask) + 0.02, (equiv, ask, sw)
    # every cent, a 10^-k under and over it, k = 1..11
    for c in range(1, 100):
        for k in range(1, 12):
            under = c / 100.0 - 10.0 ** -k
            if 0.01 <= under < 1.0:
                w = r.buy_price(under, 0.99)
                assert w is not None and w <= under, (under, w)
                sw = r.sell_price(under, 0.01)
                assert sw is not None and sw >= min(0.99, under), (under, sw)
            over = c / 100.0 + 10.0 ** -k
            if 0.01 <= over < 1.0:
                w = r.buy_price(over, 0.99)
                assert w is not None and w <= over, (over, w)
                sw = r.sell_price(over, 0.01)
                assert sw is not None and sw >= min(0.99, over), (over, sw)


def test_price_rules_in_a_locked_or_inverted_book():
    # bid >= ask: the LEVEL bound holds (never above him, never above
    # the bid); the no-cross bound cannot, and is not claimed -- the
    # post-only 400 arms the take and a fill would still be at or
    # under him
    rng = random.Random(11)
    crossed = 0
    for _ in range(20_000):
        his = round(rng.uniform(0.01, 0.999999), 6)
        ask = round(rng.uniform(0.01, 0.96), 2)
        bid = round(min(0.99, ask + rng.choice((0.0, 0.01, 0.03))), 2)     # locked or inverted
        assert bid >= ask
        w = r.buy_price(his, bid)
        assert w is not None and w <= his and w <= bid, (his, bid, ask, w)
        if w >= ask:
            crossed += 1
        equiv = round(1.0 - his, 6)
        sw = r.sell_price(equiv, ask)
        assert sw is not None and sw >= min(0.99, equiv) and sw >= min(0.99, ask), (equiv, ask, sw)
    assert crossed > 0                     # the case exists; it is bounded by the level, not avoided
    # zero-width and edge books
    for bid, ask in ((0.01, 0.01), (0.99, 0.99), (0.01, 0.99), (0.99, 0.01), (0.5, 0.5)):
        for his in (0.005, 0.01, 0.4999999, 0.5, 0.99, 0.999999):
            w = r.buy_price(his, bid)
            assert w is None or (w <= his and w <= bid), (his, bid, w)
            if w is None:
                assert min(his, bid) < 0.01
            equiv = round(1.0 - his, 6)
            sw = r.sell_price(equiv, ask)
            assert sw is not None and sw >= min(0.99, equiv) and sw >= min(0.99, ask), (equiv, ask, sw)
    # a post-only refusal in such a book is the one thing that arms the take
    assert r.take_arms(400) is True and r.take_arms(429) is False


# ------------------------------------------------------------- the take

def test_take_only_after_the_wait_and_at_or_through():
    wait = r.MIRROR_TAKE_AFTER_S
    # BUY resting at 0.47: not yet waited, book there -> no
    assert r.take_allowed(wait - 1, None, 1000.0, 0.46, 0.47, 0.47, r.BUY) is False
    # waited, book above him -> no (held under target: resting_above_level)
    assert r.take_allowed(wait, None, 1000.0, 0.47, 0.48, 0.47, r.BUY) is False
    # waited AND at his level -> the one IOC at the same wire
    assert r.take_allowed(wait, None, 1000.0, 0.46, 0.47, 0.47, r.BUY) is True
    assert r.take_allowed(wait, None, 1000.0, 0.45, 0.46, 0.47, r.BUY) is True   # through
    # the post-only rejection arms the same wait
    assert r.take_allowed(None, 1000.0, 1000.0 + wait - 1, 0.46, 0.47, 0.47, r.BUY) is False
    assert r.take_allowed(None, 1000.0, 1000.0 + wait, 0.46, 0.47, 0.47, r.BUY) is True
    # SELL resting at 0.54: bid must be at/over
    assert r.take_allowed(wait, None, 1000.0, 0.53, 0.55, 0.54, r.SELL) is False
    assert r.take_allowed(wait, None, 1000.0, 0.54, 0.55, 0.54, r.SELL) is True
    # unreadable quote or wire is never marketable
    assert r.take_allowed(wait, None, 1000.0, None, None, 0.47, r.BUY) is False
    assert r.take_allowed(wait, None, 1000.0, 0.46, 0.47, None, r.BUY) is False
    assert r.take_allowed(None, None, 1000.0, 0.46, 0.47, 0.47, r.BUY) is False
    assert r.at_or_through("SOMETHING", 0.5, 0.5, 0.5) is False


def test_take_property_never_pays_above_him():
    # for every wire and ask, allowed implies ask <= wire (his level or better)
    for wire_c in range(1, 100):
        for ask_c in range(1, 100):
            ok = r.take_allowed(r.MIRROR_TAKE_AFTER_S, None, 0.0, None, ask_c / 100.0,
                                wire_c / 100.0, r.BUY)
            assert ok == (ask_c <= wire_c)


def test_take_inputs_fail_closed_and_the_wait_cannot_be_shortened():
    wait = r.MIRROR_TAKE_AFTER_S
    # a wire off the ladder, or not a number, is never at anything
    for bad in (0.0, 0.005, 1.0, 0.995, -0.47, True, False, "0.47", math.nan, math.inf, None):
        assert r.at_or_through(r.BUY, 0.46, 0.47, bad) is False, bad
        assert r.at_or_through(r.SELL, 0.54, 0.55, bad) is False, bad
        assert r.take_allowed(wait, None, 1000.0, 0.01, 0.01, bad, r.BUY) is False, bad
    assert r.at_or_through(r.BUY, 0.46, 0.47, 0.01) is False and r.at_or_through(r.BUY, 0.0, 0.01, 0.01) is True
    assert r.at_or_through(r.SELL, 0.99, 0.99, 0.99) is True
    # a quote that is not a number is not a quote
    for bad in (True, "0.46", math.nan, math.inf):
        assert r.at_or_through(r.BUY, 0.46, bad, 0.47) is False, bad
        assert r.at_or_through(r.SELL, bad, 0.55, 0.54) is False, bad
    # an age or clock that is not a finite number has not waited
    for bad in (math.nan, math.inf, True, "600", b"600", None):
        assert r.take_allowed(bad, None, 1000.0, 0.46, 0.47, 0.47, r.BUY) is False, bad
        assert r.take_allowed(None, bad, 1000.0, 0.46, 0.47, 0.47, r.BUY) is False, bad
        assert r.take_allowed(None, 0.0, bad, 0.46, 0.47, 0.47, r.BUY) is False, bad
    # wait_s can only lengthen: a caller's 1 s is still the constant
    assert r.take_allowed(wait - 1, None, 1000.0, 0.46, 0.47, 0.47, r.BUY, wait_s=1) is False
    assert r.take_allowed(wait, None, 1000.0, 0.46, 0.47, 0.47, r.BUY, wait_s=1) is True
    assert r.take_allowed(wait, None, 1000.0, 0.46, 0.47, 0.47, r.BUY, wait_s=0) is True
    for bad in (None, math.nan, True, "1", -5):
        assert r.take_allowed(wait, None, 1000.0, 0.46, 0.47, 0.47, r.BUY, wait_s=bad) is True, bad
        assert r.take_allowed(wait - 1, None, 1000.0, 0.46, 0.47, 0.47, r.BUY, wait_s=bad) is False, bad
    # and a longer wait is honoured
    assert r.take_allowed(wait, None, 1000.0, 0.46, 0.47, 0.47, r.BUY, wait_s=wait * 2) is False
    assert r.take_allowed(wait * 2, None, 1000.0, 0.46, 0.47, 0.47, r.BUY, wait_s=wait * 2) is True
    assert r.take_allowed(None, 0.0, wait * 2 - 1, 0.46, 0.47, 0.47, r.BUY, wait_s=wait * 2) is False


def test_take_arms_only_on_a_crossing_refusal():
    assert r.take_arms(400) is True
    for code in (401, 403, 404, 429, 500, 503, 0, -400, None, "400", 400.0, True, False, [400]):
        assert r.take_arms(code) is False, code


# the two shapes of a post-only refusal (to-a-tee program Phase 7):
# the adapter's raw dict for (a) an HTTP 400 and (b) a 200 carrying a
# REJECTED order with a REJECTED execution
REJ_STATE, REJ_EXEC = "ORDER_STATE_REJECTED", "EXECUTION_TYPE_REJECTED"
_SHAPE_400 = {"status_code": 400, "error": "post-only order would cross",
              "body": {"message": "would cross", "code": 7}, "preview": {}}
_SHAPE_200 = {"status_code": 200, "order_state": REJ_STATE, "execution_type": REJ_EXEC,
              "post_only_cross": True, "order_id": "ord-1", "preview": {}}


def test_the_rejected_names_are_the_sdks_own_spelling():
    # the rules module cannot import the venue SDK (the purity pin), so
    # it restates the two enum strings; this pins them to the SDK's
    # Literal types, character for character
    import typing

    from polymarket_us.types import orders as sdk_orders

    assert r.ORDER_STATE_REJECTED == REJ_STATE
    assert r.EXECUTION_TYPE_REJECTED == REJ_EXEC
    assert r.ORDER_STATE_REJECTED in typing.get_args(sdk_orders.OrderState)
    assert r.EXECUTION_TYPE_REJECTED in typing.get_args(sdk_orders.ExecutionType)
    assert "ORDER_STATE_REJECTED" in r.__all__ and "EXECUTION_TYPE_REJECTED" in r.__all__


def test_take_arms_on_both_refusal_shapes_and_on_nothing_else():
    # shape (a): the bare int (today's path) and the adapter's dict
    assert r.take_arms(400) is True
    assert r.take_arms(dict(_SHAPE_400)) is True
    assert r.take_arms({"status_code": 400}) is True
    # a 400 arms whatever else rides in raw: the extra keys of the
    # second shape do not un-arm the first
    assert r.take_arms({"status_code": 400, "post_only_cross": False}) is True
    assert r.take_arms({"status_code": 400, "post_only_cross": True, "execution_type": REJ_EXEC}) is True
    assert r.take_arms({"status_code": 400, "execution_type": "EXECUTION_TYPE_NEW"}) is True
    # shape (b): the 200 that carries the venue's refusal
    assert r.take_arms(dict(_SHAPE_200)) is True
    assert r.take_arms({"status_code": 200, "post_only_cross": True, "execution_type": REJ_EXEC}) is True
    # order_state and order_id ride beside for the census; neither is
    # a condition, so their absence does not un-arm a refusal the
    # adapter stamped post_only_cross
    assert r.take_arms({"status_code": 200, "post_only_cross": True, "execution_type": REJ_EXEC,
                        "order_state": None, "order_id": None}) is True
    # every non-arming input: other codes in both shapes
    for code in (401, 403, 404, 409, 422, 429, 500, 503, 0, -400, 200, 201, 204):
        assert r.take_arms(code) is False, code
        assert r.take_arms({"status_code": code}) is False, code
        assert r.take_arms({"status_code": code, "error": "no"}) is False, code
    # a status that is not the int it arrived as
    for bad in ("400", "200", 400.0, 200.0, True, False, None, Decimal("400"), [400], b"400"):
        assert r.take_arms(bad) is False, bad
        assert r.take_arms({"status_code": bad}) is False, bad
        assert r.take_arms({"status_code": bad, "post_only_cross": True, "execution_type": REJ_EXEC}) is False, bad
    # a dict missing a key its shape needs
    assert r.take_arms({}) is False
    assert r.take_arms({"post_only_cross": True, "execution_type": REJ_EXEC}) is False
    assert r.take_arms({"status_code": 200}) is False
    assert r.take_arms({"status_code": 200, "post_only_cross": True}) is False
    assert r.take_arms({"status_code": 200, "execution_type": REJ_EXEC}) is False
    assert r.take_arms({"status_code": 200, "order_state": REJ_STATE, "execution_type": REJ_EXEC}) is False
    assert r.take_arms({"status_code": 200, "order_state": REJ_STATE, "post_only_cross": True}) is False
    # a 200 whose post_only_cross is not the bool True
    for flag in (False, None, 1, 1.0, "True", "true", [True], Decimal("1")):
        assert r.take_arms({"status_code": 200, "post_only_cross": flag, "execution_type": REJ_EXEC}) is False, flag
    # a 200 whose execution type is spelled any other way
    for et in ("REJECTED", "rejected", "EXECUTION_TYPE_REJECTED ", " EXECUTION_TYPE_REJECTED",
               "execution_type_rejected", "EXECUTION_TYPE_CANCELED", "EXECUTION_TYPE_NEW",
               "EXECUTION_TYPE_EXPIRED", REJ_STATE, None, True, 1, b"EXECUTION_TYPE_REJECTED",
               ["EXECUTION_TYPE_REJECTED"]):
        assert r.take_arms({"status_code": 200, "post_only_cross": True, "execution_type": et}) is False, et
    # containers that are not the adapter's dict
    for junk in ([400], (400,), {400}, [_SHAPE_400], "post_only_rejected", object(), mi.Plan(None, 0, None, "x")):
        assert r.take_arms(junk) is False, junk
    # the inputs are never mutated by the reading
    a, b = dict(_SHAPE_400), dict(_SHAPE_200)
    r.take_arms(a)
    r.take_arms(b)
    assert a == _SHAPE_400 and b == _SHAPE_200


def test_take_arms_truth_table_over_the_dict_keys():
    # sweep every combination of the three keys over small pools: the
    # only arming dicts are status 400 (any other keys) and status 200
    # with post_only_cross True and the exact REJECTED execution type
    codes = (400, 200, 429, "400", 400.0, True, None, "absent")
    flags = (True, False, None, 1, "True", "absent")
    types = (REJ_EXEC, "EXECUTION_TYPE_CANCELED", "REJECTED", None, "absent")
    armed = 0
    for code, flag, et in itertools.product(codes, flags, types):
        d = {}
        if code != "absent":
            d["status_code"] = code
        if flag != "absent":
            d["post_only_cross"] = flag
        if et != "absent":
            d["execution_type"] = et
        # the oracle reads the code as the rule must: the int it
        # arrived as (400.0 == 400 in Python, and is not a status)
        is_int = isinstance(code, int) and not isinstance(code, bool)
        want = (is_int and code == 400) or (is_int and code == 200 and flag is True and et == REJ_EXEC)
        got = r.take_arms(d)
        assert got is want, (d, got)
        armed += got
    # 400 x every flag x every type, plus the one 200 row
    assert armed == len(flags) * len(types) + 1
    # a foreign __eq__ on the keys' values never runs: post_only_cross
    # is read by identity and execution_type is compared only as a str

    class Boom:
        def __eq__(self, other):
            raise RuntimeError("eq")

        __hash__ = object.__hash__

    assert r.take_arms({"status_code": 200, "post_only_cross": Boom(), "execution_type": REJ_EXEC}) is False
    assert r.take_arms({"status_code": 200, "post_only_cross": True, "execution_type": Boom()}) is False
    assert r.take_arms({"status_code": Boom()}) is False
    assert r.take_arms(Boom()) is False


def test_a_take_never_fires_on_a_non_quote():
    # a quote must be a cent on the ladder like the wire: 0.0 and -1.0
    # used to read as "the ask is under our cent" and fire the take,
    # and 1e-12 is not a price anything trades at
    for q in (0.0, -0.0, -1.0, 1e-12, 0.000001, 0.0099, 0.991, 0.999999, 1.0, 1.5, 5.0, 1e308,
              Decimal("0"), Decimal("1")):
        assert r.at_or_through(r.BUY, 0.46, q, 0.47) is False, q
        assert r.at_or_through(r.SELL, q, 0.55, 0.54) is False, q
        assert r.take_allowed(999.0, None, 0.0, 0.46, q, 0.47, r.BUY) is False, q
        assert r.take_allowed(999.0, None, 0.0, q, 0.55, 0.54, r.SELL) is False, q
    assert r.at_or_through(r.BUY, 0.46, Decimal("0.4"), 0.47) is True
    assert r.at_or_through(r.BUY, 0.46, 0.01, 0.47) is True
    assert r.at_or_through(r.SELL, 0.99, 0.55, 0.54) is True
    # an arm time before the epoch, or after now, is no arm; a
    # negative rest age has not waited
    assert r.take_allowed(None, -1e308, 0.0, 0.46, 0.47, 0.47, r.BUY) is False
    assert r.take_allowed(None, -1.0, 1000.0, 0.46, 0.47, 0.47, r.BUY) is False
    assert r.take_allowed(None, 2000.0, 1000.0, 0.46, 0.47, 0.47, r.BUY) is False
    assert r.take_allowed(None, 0.0, r.MIRROR_TAKE_AFTER_S, 0.46, 0.47, 0.47, r.BUY) is True
    assert r.take_allowed(-5.0, None, 0.0, 0.46, 0.47, 0.47, r.BUY) is False


# -------------------------------------------------------------- flatten

def test_paired_out_target_zero_never_selects_the_slippage_path():
    # he still holds the other token by fills: paired out, rest at 1 - q
    assert r.select_flatten(0, 0.0, 5000.0, True, 0.0, 5000.0, True, True) == "flatten_paired"
    # he holds the long token by fills, flat by snapshot: still paired
    assert r.select_flatten(0, 100.0, 0.0, True, 0.0, 0.0, True, True) == "flatten_paired"
    # gone by fills but a fresh snapshot shows him holding: paired
    assert r.select_flatten(0, 0.0, 0.0, True, 0.0, 12.0, True, True) == "flatten_paired"
    # a target above zero is a reduce, not a flatten
    assert r.select_flatten(40, 0.0, 0.0, True, 0.0, 0.0, True, True) is None


def test_vanish_needs_every_confirmation():
    gone = (True, True)                         # market_live, confirm_gone
    # a fresh, complete snapshot showing him flat: vanished
    assert r.select_flatten(0, 0.0, 0.0, True, 0.0, 0.0, *gone) == "flatten_vanished"
    assert r.select_flatten(0, 0, 0, True, 0, 0, *gone, snap_partial=False) == "flatten_vanished"
    # RN1: the positions walk is always truncated, so the snapshot is
    # never fresh AND complete; with fills-derived zero on both tokens
    # the mirror's own _confirm_gone is the positive confirmation
    # (addendum section 8) and the snapshot may be absent or partial
    assert r.select_flatten(0, 0.0, 0.0, False, 0.0, 0.0, *gone) == "flatten_vanished"
    assert r.select_flatten(0, 0.0, 0.0, None, None, None, *gone) == "flatten_vanished"
    assert r.select_flatten(0, 0.0, 0.0, True, 0.0, 0.0, *gone, snap_partial=True) == "flatten_vanished"
    assert r.select_flatten(0, 0.0, 0.0, False, 0.0, 0.0, *gone, snap_partial=True) == "flatten_vanished"
    assert r.select_flatten(0, 0.0, 0.0, False, None, None, *gone, snap_partial=True) == "flatten_vanished"
    # a FRESH snapshot that shows him HOLDING either token stays
    # paired, never vanished -- complete or partial
    assert r.select_flatten(0, 0.0, 0.0, True, 12.0, 0.0, *gone) == "flatten_paired"
    assert r.select_flatten(0, 0.0, 0.0, True, 0.0, 12.0, *gone, snap_partial=False) == "flatten_paired"
    assert r.select_flatten(0, 0.0, 0.0, True, 0.0, 12.0, *gone, snap_partial=True) == "flatten_paired"
    # the market not live, or _confirm_gone not True: unconfirmed,
    # whatever the snapshot says
    for fresh, partial in ((True, False), (True, True), (True, None), (False, None), (None, None), (False, True)):
        for live, gone_ in ((False, True), (None, True), (True, False), (True, None), (False, False), (None, None)):
            out = r.select_flatten(0, 0.0, 0.0, fresh, 0.0, 0.0, live, gone_, snap_partial=partial)
            assert out == "vanish_unconfirmed", (fresh, partial, live, gone_, out)
    # a fresh complete snapshot whose sizes could not be read: unconfirmed
    assert r.select_flatten(0, 0.0, 0.0, True, None, 0.0, *gone, snap_partial=False) == "vanish_unconfirmed"
    assert r.select_flatten(0, 0.0, 0.0, True, 0.0, None, *gone) == "vanish_unconfirmed"
    # fills on either token: paired, before any snapshot is consulted
    assert r.select_flatten(0, 3.0, 0.0, False, None, None, *gone, snap_partial=True) == "flatten_paired"
    assert r.select_flatten(0, 0.0, 3.0, None, None, None, *gone) == "flatten_paired"


def test_flatten_truth_table_nothing_else_reaches_the_slippage_path():
    sizes = (0.0, 5.0, None)
    flags = (True, False, None)
    seen = set()
    for hl, ho, fresh, partial, sl, so, live, gone in itertools.product(
            sizes, sizes, flags, flags, sizes, sizes, flags, flags):
        out = r.select_flatten(0, hl, ho, fresh, sl, so, live, gone, snap_partial=partial)
        seen.add(out)
        assert out in ("flatten_paired", "flatten_vanished", "vanish_unconfirmed")
        if hl is None or ho is None:
            assert out == "vanish_unconfirmed"
            continue
        if out == "flatten_vanished":
            # the one and only door: live, gone, both fills 0, no fresh sighting of him
            assert live is True and gone is True and hl == 0 and ho == 0
            assert not (fresh is True and ((sl or 0) > 0 or (so or 0) > 0))
            assert ((fresh is True and partial is not True and sl == 0 and so == 0)
                    or (fresh is not True or partial is True))
        if out == "flatten_paired":
            assert hl > 0 or ho > 0 or (fresh is True and ((sl or 0) > 0 or (so or 0) > 0))
        if not (live is True and gone is True):
            assert out != "flatten_vanished"
    assert seen == {"flatten_paired", "flatten_vanished", "vanish_unconfirmed"}


def test_flatten_flags_must_be_bool_or_none():
    gone = (True, True)
    # snap_fresh=1 read as "snapshot absent" and walked past a fresh
    # sighting of him (12 shares) into the slippage path
    for flag in (1, 0, "True", "False", 1.0, 0.0, Decimal("1"), [True], object()):
        assert r.select_flatten(0, 0.0, 0.0, flag, 12.0, 0.0, *gone) == "vanish_unconfirmed", flag
        assert r.select_flatten(0, 0.0, 0.0, flag, 0.0, 0.0, *gone) == "vanish_unconfirmed", flag
        assert r.select_flatten(0, 0.0, 0.0, True, 0.0, 0.0, *gone, snap_partial=flag) == "vanish_unconfirmed", flag
        assert r.select_flatten(0, 0.0, 0.0, False, 0.0, 0.0, *gone, snap_partial=flag) == "vanish_unconfirmed", flag
        assert r.select_flatten(0, 0.0, 0.0, True, 0.0, 0.0, flag, True) == "vanish_unconfirmed", flag
        assert r.select_flatten(0, 0.0, 0.0, True, 0.0, 0.0, True, flag) == "vanish_unconfirmed", flag
        # fills that show him holding still name the paired book first
        assert r.select_flatten(0, 3.0, 0.0, flag, 0.0, 0.0, flag, flag, snap_partial=flag) == "flatten_paired", flag
    # the truth table over garbage flags: nothing reaches the slippage path
    garbage = (1, 0, "True", 1.0)
    for fresh, partial, live, gone_ in itertools.product(garbage + (True,), repeat=4):
        if fresh is True and partial is True and live is True and gone_ is True:
            continue
        out = r.select_flatten(0, 0.0, 0.0, fresh, 0.0, 0.0, live, gone_, snap_partial=partial)
        assert out == "vanish_unconfirmed", (fresh, partial, live, gone_, out)


def test_an_unread_size_never_reaches_the_slippage_path():
    # a None fill used to read as zero on the one path that accepts
    # slippage; every size reading is now REQUIRED as a number, and
    # an unmade reading is unconfirmed (never flatten_vanished)
    every = (True, 0.0, 0.0, True, True)     # snap_fresh, snap_long, snap_other, live, gone
    for bad in (None, "x", math.nan, math.inf, True):
        assert r.select_flatten(0, bad, 0.0, *every) == "vanish_unconfirmed", bad
        assert r.select_flatten(0, 0.0, bad, *every) == "vanish_unconfirmed", bad
        assert r.select_flatten(0, bad, bad, *every) == "vanish_unconfirmed", bad
        # nor does an unread fill reach the paired answer through a
        # holding on the other token: the reading is simply not made
        assert r.select_flatten(0, bad, 5000.0, *every) == "vanish_unconfirmed", bad
        # the snapshot readings on the same path are held to the same
        # standard: a token absent from a completed read is 0.0, passed
        # by the worker as snap.get(token, 0.0), never None
        assert r.select_flatten(0, 0.0, 0.0, True, bad, 0.0, True, True) == "vanish_unconfirmed", bad
        assert r.select_flatten(0, 0.0, 0.0, True, 0.0, bad, True, True) == "vanish_unconfirmed", bad
    # a target that is no plan is not a flatten either
    assert r.select_flatten(None, 0.0, 0.0, *every) is None
    assert r.select_flatten("x", 0.0, 0.0, *every) is None


def test_only_an_int_is_a_flatten_target_and_a_size_is_never_negative():
    every = (True, 0.0, 0.0, True, True)
    # only an int is a target: a bool, a float (0.0, -0.0), a string,
    # None, a huge int that is not zero -> not a flatten
    for not_target in (True, False, 0.0, -0.0, "0", None, 10**400, 1, -1, [0]):
        assert r.select_flatten(not_target, 0.0, 0.0, *every) is None, not_target
    assert r.select_flatten(0, 0.0, 0.0, *every) == "flatten_vanished"
    # a negative size is not a size: unconfirmed, never vanished
    for neg in (-1.0, -0.001, -1):
        assert r.select_flatten(0, neg, 0.0, *every) == "vanish_unconfirmed", neg
        assert r.select_flatten(0, 0.0, neg, *every) == "vanish_unconfirmed", neg
        assert r.select_flatten(0, 0.0, 0.0, True, neg, 0.0, True, True) == "vanish_unconfirmed", neg
        assert r.select_flatten(0, 0.0, 0.0, True, 0.0, neg, True, True) == "vanish_unconfirmed", neg
    # an int too large for a float (OverflowError inside float()) is
    # caught like every other bad reading
    assert r.select_flatten(0, 10**400, 0.0, *every) == "vanish_unconfirmed"
    assert r.select_flatten(0, 0.0, 0.0, True, 10**400, 0.0, True, True) == "vanish_unconfirmed"
    assert r.drift_of(10**400, 1.0) is None and r.room_scale(10**400, 0.5, 250.0, 1.0, 1.0, 1.0) == 0


# ----------------------------------------------------------------- room

def test_room_scale_is_the_smallest_room_at_the_wire_or_nothing():
    assert r.room_scale(1000, 0.5, 250.0, 1000.0, 5000.0, 1250.0) == 500
    assert r.room_scale(1000, 0.5, 250.0, 60.0, 5000.0, 1250.0) == 120
    assert r.room_scale(1000, 0.5, 250.0, 1000.0, 5000.0, 7.0) == 14
    assert r.room_scale(10, 0.5, 250.0, 1000.0, 5000.0, 1250.0) == 10
    # under one share: over_room
    assert r.room_scale(10, 0.5, 250.0, 0.4, 5000.0, 1250.0) == 0
    assert r.room_scale(10, 0.5, 250.0, 0.0, 5000.0, 1250.0) == 0
    assert r.room_scale(10, 0.5, 250.0, -3.0, 5000.0, 1250.0) == 0
    # an unreadable room or wire is no room
    assert r.room_scale(10, 0.5, 250.0, None, 5000.0, 1250.0) == 0
    assert r.room_scale(10, None, 250.0, 100.0, 5000.0, 1250.0) == 0
    assert r.room_scale(10, 0.0, 250.0, 100.0, 5000.0, 1250.0) == 0
    assert r.room_scale(0, 0.5, 250.0, 100.0, 5000.0, 1250.0) == 0
    assert r.room_scale(10, 0.1, 250.0, 5.0, 5000.0, 1250.0) == 10   # 5/0.1 floors to 50, not 49


def test_room_scale_never_raises_and_reads_nothing_that_is_not_a_number():
    good = (0.5, 250.0, 1000.0, 5000.0, 1250.0)
    # the quantity: inf used to raise in int(); a bool, a string, a
    # fraction, a negative are no quantity
    for bad in (math.inf, -math.inf, math.nan, True, False, "10", b"10", None, 10.5, -10):
        assert r.room_scale(bad, *good) == 0, bad
    assert r.room_scale(10.0, *good) == 10
    # the wire: nan used to raise in floor()
    for bad in (math.nan, math.inf, -math.inf, True, "0.5", None, 0.0, -0.5):
        assert r.room_scale(10, bad, 250.0, 1000.0, 5000.0, 1250.0) == 0, bad
    # every room
    for i in range(4):
        for bad in (math.nan, math.inf, -math.inf, True, "250", None):
            rooms = [250.0, 1000.0, 5000.0, 1250.0]
            rooms[i] = bad
            assert r.room_scale(10, 0.5, *rooms) == 0, (i, bad)


def test_room_scale_needs_a_cent_on_the_ladder_and_a_finite_quotient():
    # the reviewer's probes raised OverflowError in floor(inf)
    assert r.room_scale(10, 1e-320, 250.0, 100.0, 5000.0, 1250.0) == 0
    assert r.room_scale(10**6, 1e-300, 1e308, 1e308, 1e308, 1e308) == 0
    # a sub-cent or over-the-ladder wire is no wire
    for w in (0.001, 0.009, 0.0099, 1e-300, 0.991, 0.995, 1.0, 5.0):
        assert r.room_scale(10, w, 250.0, 100.0, 5000.0, 1250.0) == 0, w
    assert r.room_scale(10, 0.01, 250.0, 100.0, 5000.0, 1250.0) == 10
    assert r.room_scale(10, 0.99, 250.0, 100.0, 5000.0, 1250.0) == 10
    assert r.room_scale(10, Decimal("0.5"), 250.0, 100.0, 5000.0, 1250.0) == 10
    assert r.room_scale(Decimal("10"), 0.5, 250.0, 100.0, 5000.0, 1250.0) == 10
    # a quotient that overflows is no room
    assert r.room_scale(10**6, 0.01, 1e308, 1e308, 1e308, 1e308) == 0
    assert r.room_scale(10**6, 0.5, 1e307, 1e307, 1e307, 1e307) == 10**6


# ------------------------------------------------------------ open order

def test_keep_when_the_order_still_says_what_the_plan_says():
    o = r.OpenOrder(r.BUY, 0.47, 100, 100, placed_at=1000.0)
    p = mi.Plan(r.BUY, 100, 0.47, "increase toward target")
    assert r.keep_or_replace(o, p, now=1000.0 + r.MIRROR_REST_TTL_S - 1) == "keep"
    # leaves within a share, or within MIN_MOVE_FRAC of the plan's qty
    assert r.keep_or_replace(r.OpenOrder(r.BUY, 0.47, 100, 99.5, 1000.0), p, 1100.0) == "keep"
    assert r.keep_or_replace(r.OpenOrder(r.BUY, 0.47, 100, 98, 1000.0), p, 1100.0) == "keep"
    # the plan's off-cent price floors to the same cent
    assert r.keep_or_replace(o, mi.Plan(r.BUY, 100, 0.474, "x"), 1100.0) == "keep"


def test_replace_on_side_cent_quantity_or_ttl():
    o = r.OpenOrder(r.BUY, 0.47, 100, 100, placed_at=1000.0)
    p = mi.Plan(r.BUY, 100, 0.47, "increase toward target")
    assert r.keep_or_replace(o, p, now=1000.0 + r.MIRROR_REST_TTL_S) == "replace"
    assert r.keep_or_replace(o, mi.Plan(r.SELL, 100, 0.53, "reduce"), 1100.0) == "replace"
    assert r.keep_or_replace(o, mi.Plan(r.BUY, 100, 0.46, "x"), 1100.0) == "replace"
    assert r.keep_or_replace(o, mi.Plan(r.BUY, 100, 0.48, "x"), 1100.0) == "replace"
    assert r.keep_or_replace(o, mi.Plan(r.BUY, 60, 0.47, "x"), 1100.0) == "replace"
    assert r.keep_or_replace(r.OpenOrder(r.BUY, 0.47, 100, None, 1000.0), p, 1100.0) == "replace"
    assert r.keep_or_replace(r.OpenOrder(r.BUY, 0.47, 100, 100, None), p, 1100.0) == "replace"


def test_an_order_the_plan_does_not_want_is_cancelled_by_name():
    o = r.OpenOrder(r.BUY, 0.47, 100, 100, placed_at=1000.0)
    p = mi.Plan(r.BUY, 100, 0.47, "increase toward target")
    assert r.keep_or_replace(o, p, 1100.0, cancel_reason="venue_ledger_disagree") == "venue_ledger_disagree"
    assert r.keep_or_replace(o, p, 1100.0, cancel_reason="mode_db_off") == "mode_db_off"
    assert r.keep_or_replace(o, mi.Plan(None, 0, None, "on target"), 1100.0) == "on_target"
    assert r.keep_or_replace(o, None, 1100.0) == "no_plan"
    assert r.keep_or_replace(o, mi.Plan(r.BUY, 100, None, "no price to rest at"), 1100.0) == "no_price"


def test_keep_or_replace_fails_closed_on_every_input():
    p = mi.Plan(r.BUY, 100, 0.47, "increase toward target")
    good = r.OpenOrder(r.BUY, 0.47, 100, 100, placed_at=1000.0)
    assert r.keep_or_replace(good, p, 1100.0) == "keep"
    # an unreadable wire, age, leaves, TTL or clock is never 'keep'
    for bad in (None, math.nan, math.inf, -math.inf, True, "0.47"):
        assert r.keep_or_replace(dc_replace(good, wire=bad), p, 1100.0) == "replace", bad
        assert r.keep_or_replace(dc_replace(good, placed_at=bad), p, 1100.0) == "replace", bad
        assert r.keep_or_replace(dc_replace(good, leaves=bad), p, 1100.0) == "replace", bad
        assert r.keep_or_replace(good, p, bad) == "replace", bad
        assert r.keep_or_replace(good, p, 1100.0, ttl_s=bad) == "replace", bad
    # an order placed in the future has an unreadable age
    assert r.keep_or_replace(good, p, 999.0) == "replace"
    assert r.keep_or_replace(good, p, 1000.0) == "keep"
    # a plan quantity that is not a number
    for bad in (None, math.nan, True, "100"):
        assert r.keep_or_replace(good, mi.Plan(r.BUY, bad, 0.47, "x"), 1100.0) == "replace", bad
    # a cancel reason that is given -- even blank -- is a cancel, never
    # a keep; a blank one is named
    assert r.keep_or_replace(good, p, 1100.0, cancel_reason="") == "cancel_unnamed"
    assert r.keep_or_replace(good, p, 1100.0, cancel_reason="   ") == "cancel_unnamed"
    assert r.keep_or_replace(good, p, 1100.0, cancel_reason="loss_stop") == "loss_stop"
    assert r.keep_or_replace(good, p, 1100.0, cancel_reason=None) == "keep"
    # a wire off the ladder is no price
    for bad in (0.0, 0.005, 1.0, math.nan, True, "0.47"):
        assert r.keep_or_replace(good, p, 1100.0, wire=bad) == "no_price", bad


def test_keep_or_replace_wants_a_cent_and_a_plan_of_at_least_a_share():
    o = r.OpenOrder(r.BUY, 0.47, 100, 100, placed_at=0.0)
    p = mi.Plan(r.BUY, 100, 0.47, "x")
    assert r.keep_or_replace(o, p, 10.0, wire=Decimal("0.47")) == "keep"
    assert r.keep_or_replace(o, p, 10.0, wire=0.47 + 1e-9) == "keep"
    # a wire off a cent is not a wire the worker computed
    for off in (0.475, 0.471, 0.4699, 0.4650001):
        assert r.keep_or_replace(o, p, 10.0, wire=off) == "no_price", off
    # a plan under one share cannot be what an order is for
    half = r.OpenOrder(r.BUY, 0.47, 1, 0.5, 0.0)
    assert r.keep_or_replace(half, mi.Plan(r.BUY, 0, 0.47, "x"), 10.0) == "replace"
    assert r.keep_or_replace(half, mi.Plan(r.BUY, 0.5, 0.47, "x"), 10.0) == "replace"
    assert r.keep_or_replace(o, mi.Plan(r.BUY, -100, 0.47, "x"), 10.0) == "replace"
    assert r.keep_or_replace(r.OpenOrder(r.BUY, 0.47, 100, -5, 0.0), p, 10.0) == "replace"
    # the SELL side through the worker's wire, like the BUY side
    so = r.OpenOrder(r.SELL, 0.53, 100, 100, 0.0)
    sp = mi.Plan(r.SELL, 100, 0.52, "reduce")
    assert r.keep_or_replace(so, sp, 10.0) == "replace"                       # plan_wire 0.52 != 0.53
    assert r.keep_or_replace(so, sp, 10.0, wire=r.sell_price(0.52004, 0.51)) == "keep"


def test_keep_or_replace_and_plan_wire_refuse_objects_that_are_not_theirs():
    p = mi.Plan(r.BUY, 100, 0.47, "x")
    o = r.OpenOrder(r.BUY, 0.47, 100, 100, placed_at=0.0)
    # an order that is not an OpenOrder, a plan that is neither None
    # nor a Plan: 'replace', never an AttributeError -- and a cancel
    # reason still wins
    for junk in (5, "x", object(), {"side": r.BUY}, mi.Book(0.4, 0.5), r.BookState(), [p]):
        assert r.keep_or_replace(junk, p, 10.0) == "replace", junk
        assert r.keep_or_replace(o, junk, 10.0) == "replace", junk
        assert r.keep_or_replace(junk, junk, 10.0) == "replace", junk
        assert r.keep_or_replace(junk, p, 10.0, cancel_reason="halted") == "halted", junk
        assert r.plan_wire(junk) is None, junk
    assert r.keep_or_replace(None, p, 10.0) == "replace"
    assert r.keep_or_replace(None, None, 10.0) == "replace"
    assert r.keep_or_replace(o, None, 10.0) == "no_plan"
    # sides are compared only as the two strings: Decimal('sNaN') on
    # both sides raised InvalidOperation from !=
    snan = Decimal("sNaN")
    assert r.keep_or_replace(r.OpenOrder(snan, 0.47, 100, 100, 0.0), mi.Plan(snan, 100, 0.47, "x"), 10.0) == "replace"
    assert r.keep_or_replace(o, mi.Plan(snan, 100, 0.47, "x"), 10.0) == "replace"
    assert r.keep_or_replace(r.OpenOrder(snan, 0.47, 100, 100, 0.0), p, 10.0) == "replace"
    assert r.plan_wire(mi.Plan(snan, 100, 0.47, "x")) is None
    for side in ("BUY_SHORT", "buy_long", b"BUY_LONG", 1, True):
        assert r.plan_wire(mi.Plan(side, 100, 0.47, "x")) is None, side
        assert r.keep_or_replace(r.OpenOrder(side, 0.47, 100, 100, 0.0),
                                 mi.Plan(side, 100, 0.47, "x"), 10.0) == "replace", side
    # a reason is never compared, only rendered -- and rendering cannot raise
    assert r.keep_or_replace(o, mi.Plan(r.BUY, 100, 0.47, snan), 10.0) == "keep"
    assert r.keep_or_replace(o, mi.Plan(None, 0, None, snan), 10.0) == "snan"

    class Unrenderable:
        def __bool__(self):
            raise RuntimeError("no")

    assert r.plan_reason_key(Unrenderable()) == "no_plan"
    assert r.keep_or_replace(o, mi.Plan(None, 0, None, Unrenderable()), 10.0) == "no_plan"


def test_a_resting_order_off_a_cent_is_replaced():
    # the plan's wire had to be a cent; the RESTING order's wire is
    # held to the same rule -- an order at 0.475 is not one this book
    # placed, and 'keep' would leave it standing
    p = mi.Plan(r.BUY, 100, 0.47, "x")
    for off in (0.475, 0.479, 0.4799, 0.4701, 0.46999, 0.005, 0.995):
        assert r.keep_or_replace(r.OpenOrder(r.BUY, off, 100, 100, 0.0), p, 10.0) == "replace", off
        assert r.keep_or_replace(r.OpenOrder(r.BUY, off, 100, 100, 0.0), p, 10.0, wire=0.47) == "replace", off
    for on in (0.47, 0.47 + 1e-9, 0.47 - 1e-9, Decimal("0.47")):
        assert r.keep_or_replace(r.OpenOrder(r.BUY, on, 100, 100, 0.0), p, 10.0) == "keep", on
    assert r.keep_or_replace(r.OpenOrder(r.BUY, 0.46, 100, 100, 0.0), p, 10.0) == "replace"


def test_plan_reasons_map_to_census_names_including_the_dead_bands():
    assert r.plan_reason_key("on target") == "on_target"
    assert r.plan_reason_key("under one share") == "under_one_share"
    assert r.plan_reason_key("under the dollar dead band") == "dead_band"
    assert r.plan_reason_key("inside hysteresis") == "hysteresis"
    assert r.plan_reason_key("no price to rest at") == "no_price"
    assert r.plan_reason_key("frozen: venue and ledger disagree") == "venue_ledger_disagree"
    assert r.plan_reason_key("venue unreadable") == "positions_unreadable"
    assert r.plan_reason_key("Something New?") == "something_new"
    assert r.plan_reason_key(None) == "no_plan"
    # the dead bands are mi.plan's, reused not restated: a $4 move at
    # the mark is refused, a 1% move inside hysteresis is refused, and
    # a flatten crosses both
    bk = mi.Book(bid=0.30, ask=0.32)
    assert r.plan_reason_key(mi.plan(110, 100, 100, bk, 0.31, 0.31).reason) == "dead_band"
    # 15 shares at 0.50 is $7.50 (over the dollar band) and 1.5% of the
    # target (inside the 2% hysteresis)
    half = mi.Book(bid=0.49, ask=0.51)
    assert r.plan_reason_key(mi.plan(1000, 985, 985, half, 0.5, 0.5).reason) == "hysteresis"
    fl = mi.plan(0, 3, 3, bk, 0.69, 0.31)
    assert fl.side == r.SELL and fl.qty == 3 and r.sell_wire(fl.price) == 0.69


# -------------------------------------------------------------- booking

def test_book_buy_keeps_a_weighted_average_and_the_peak_stake():
    s0 = r.BookState()
    b1 = r.book_buy(s0, 100, 0.40)
    assert b1.refusal is None and b1.booked == 100 and b1.usd == 40.0
    assert (b1.state.ledger_net, b1.state.avg_cost, b1.state.gross_buy_usd) == (100, 0.40, 40.0)
    assert b1.state.peak_exposure_usd == 40.0
    b2 = r.book_buy(b1.state, 100, 0.50, usd=50.0)
    assert b2.state.ledger_net == 200 and b2.state.avg_cost == 0.45
    assert b2.state.gross_buy_usd == 90.0 and b2.state.peak_exposure_usd == 90.0
    # the original state is untouched: the caller commits or rolls back
    assert s0.ledger_net == 0 and s0.avg_cost is None
    # a repeated application books twice -- idempotency is the cursor's
    twice = r.book_buy(b2.state, 100, 0.50)
    assert twice.state.ledger_net == 300


def test_book_sell_realizes_against_the_average_and_a_rebuy_from_flat_resets_it():
    s = r.book_buy(r.BookState(), 200, 0.45).state
    sell = r.book_sell(s, 50, 0.55)
    assert sell.refusal is None and sell.booked == 50 and sell.overfill is False
    assert sell.realized == round((0.55 - 0.45) * 50, 4) == 5.0
    assert sell.state.ledger_net == 150 and sell.state.avg_cost == 0.45
    assert sell.state.gross_sell_usd == 27.5 and sell.state.realized_pnl == 5.0
    # the peak stake does not fall with a sale
    assert sell.state.peak_exposure_usd == 90.0
    flat = r.book_sell(sell.state, 150, 0.40)
    assert flat.state.ledger_net == 0 and flat.realized == round(-0.05 * 150, 4)
    assert flat.state.realized_pnl == round(5.0 - 7.5, 4) and flat.state.avg_cost == 0.45
    # flat-then-rebuy: the new episode's average is the new fill, not
    # the old cost carried across zero
    again = r.book_buy(flat.state, 10, 0.60)
    assert again.state.avg_cost == 0.60 and again.state.ledger_net == 10
    assert again.state.gross_buy_usd == round(90.0 + 6.0, 4)
    assert again.state.peak_exposure_usd == 90.0


def test_overfill_books_only_the_ledger_and_is_flagged():
    s = r.book_buy(r.BookState(), 40, 0.50).state
    over = r.book_sell(s, 45, 0.50)
    assert over.overfill is True and over.booked == 40 and over.state.ledger_net == 0
    assert over.realized == 0.0
    nothing = r.book_sell(over.state, 5, 0.50)
    assert nothing.overfill is True and nothing.booked == 0 and nothing.realized is None
    assert nothing.state == over.state


def test_booking_refuses_what_it_cannot_read():
    s = r.book_buy(r.BookState(), 40, 0.50).state
    assert r.book_buy(s, 0, 0.5).refusal == "nothing_to_book"
    assert r.book_buy(s, -3, 0.5).refusal == "nothing_to_book"
    assert r.book_buy(s, 3, None).refusal == "bad_price"
    assert r.book_buy(s, 3, 1.0).refusal == "bad_price"
    assert r.book_sell(s, 3, "x").refusal == "bad_price"
    assert r.book_sell(s, 0, 0.5).refusal == "nothing_to_book"
    # a held position without a known cost cannot realize anything
    odd = r.BookState(ledger_net=10, avg_cost=None)
    bad = r.book_sell(odd, 5, 0.5)
    assert bad.refusal == "avg_cost_unknown" and bad.booked == 0 and bad.state == odd


def test_booking_is_a_pure_function_and_idempotency_is_the_cursors():
    # same inputs -> same outputs, the input state never mutated: the
    # caller (the booked_filled cursor) decides what is booked once
    s0 = r.BookState(ledger_net=200, avg_cost=0.45, gross_buy_usd=90.0,
                     peak_exposure_usd=90.0, realized_pnl=0.0)
    before = dc_replace(s0)
    a = r.book_buy(s0, 100, 0.50, usd=50.0)
    b = r.book_buy(s0, 100, 0.50, usd=50.0)
    assert a == b and a.state == b.state and a.state is not s0
    assert s0 == before
    x = r.book_sell(s0, 50, 0.55)
    y = r.book_sell(s0, 50, 0.55)
    assert x == y and x.state == y.state and x.state is not s0
    assert s0 == before
    # applied twice to the same fill it books it twice: that is the
    # cursor's line, not this function's
    twice = r.book_buy(a.state, 100, 0.50, usd=50.0)
    assert twice.state.ledger_net == 400 and a.state.ledger_net == 300
    assert r.book_sell(x.state, 50, 0.55).state.ledger_net == 100
    # refusals return the very input state
    for bad in (r.book_buy(s0, None, 0.5), r.book_buy(s0, 5, None), r.book_sell(s0, math.nan, 0.5)):
        assert bad.state is s0 and bad.refusal is not None
    assert s0 == before


def test_book_buy_never_sees_the_wire_and_its_refusal_list_is_unchanged():
    """The mirror's overspend seam, stated where it lives. `book_buy` is
    handed a PRICE, never the order's wire, so any finite price in (0,1)
    books: a rest that fills above its own cent inflates avg_cost and
    gross_buy_usd here with nothing anywhere to detect it. The
    comparison belongs to the caller -- `mirror_live._book_delta` holds
    `o["wire"]` and is the single booking entry point for all three fill
    paths -- and this pins that it was NOT added here, because a
    refusal in this function would strand shares the venue has already
    given us."""
    s = r.book_buy(r.BookState(), 100, 0.30).state
    assert s.avg_cost == 0.30
    over = r.book_buy(s, 100, 0.35)          # five cents above any cent we could wire
    assert over.refusal is None and over.state.ledger_net == 200
    assert round(over.state.avg_cost, 4) == 0.325 and over.state.gross_buy_usd == 65.0
    assert "wire" not in inspect.signature(r.book_buy).parameters
    tree = ast.parse(inspect.getsource(r.book_buy))
    names = {n.value for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str) and "\n" not in n.value}
    assert names == {"bad_delta", "nothing_to_book", "bad_price", "bad_usd", "bad_state",
                     "avg_cost_unknown"}, sorted(names)
    assert "mirror_overspend" in (r.book_buy.__doc__ or ""), "the contract names its caller"


def test_booking_refuses_garbage_and_never_corrupts_the_ledger():
    s = r.book_buy(r.BookState(), 40, 0.50).state
    bad_nums = (math.nan, math.inf, -math.inf, True, False, "5", b"5", None, 10**400)
    # delta: nan used to leave ledger nan with refusal None; inf, ledger inf
    for bad in bad_nums:
        bb = r.book_buy(s, bad, 0.5)
        assert bb.refusal == "bad_delta" and bb.state is s and bb.booked == 0.0, bad
        bs = r.book_sell(s, bad, 0.5)
        assert bs.refusal == "bad_delta" and bs.state is s and bs.overfill is False, bad
    assert r.book_buy(s, 0, 0.5).refusal == "nothing_to_book"
    assert r.book_sell(s, -3.0, 0.5).refusal == "nothing_to_book"
    # usd: nan/inf/-20 used to corrupt gross_buy_usd; None is the identity
    for bad in (math.nan, math.inf, -20.0, -0.01, True, "20", b"20"):
        bb = r.book_buy(s, 10, 0.5, usd=bad)
        assert bb.refusal == "bad_usd" and bb.state is s, bad
    assert r.book_buy(s, 10, 0.5, usd=None).state.gross_buy_usd == 25.0
    assert r.book_buy(s, 10, 0.5, usd=0.0).state.gross_buy_usd == 20.0
    # price: finite in (0, 1), a number
    for bad in (math.nan, math.inf, True, "0.5", 0.0, 1.0, -0.1, None):
        assert r.book_buy(s, 10, bad).refusal == "bad_price", bad
        assert r.book_sell(s, 10, bad).refusal == "bad_price", bad
    # avg_cost that is not a number: nothing realized, nothing averaged
    for bad in (math.nan, math.inf, True, "0.45"):
        odd = r.BookState(ledger_net=10, avg_cost=bad)
        sell = r.book_sell(odd, 5, 0.5)
        assert sell.refusal == "avg_cost_unknown" and sell.state is odd and sell.realized is None, bad
        buy = r.book_buy(odd, 5, 0.5)
        assert buy.refusal == "avg_cost_unknown" and buy.state is odd, bad
    # shares held without a cost cannot carry an average either
    assert r.book_buy(r.BookState(ledger_net=10, avg_cost=None), 5, 0.5).refusal == "avg_cost_unknown"
    assert r.book_buy(r.BookState(ledger_net=0, avg_cost=None), 5, 0.5).refusal is None
    # a ledger column that is not a number, or a negative ledger, is
    # no ledger: bad_state, never arithmetic on it
    for col in ("ledger_net", "gross_buy_usd", "gross_sell_usd", "peak_exposure_usd", "realized_pnl"):
        for bad in (math.nan, math.inf, True, "1"):
            broken = dc_replace(s, **{col: bad})
            assert r.book_buy(broken, 5, 0.5).refusal == "bad_state", (col, bad)
            assert r.book_sell(broken, 5, 0.5).refusal == "bad_state", (col, bad)
    assert r.book_buy(dc_replace(s, ledger_net=-1.0), 5, 0.5).refusal == "bad_state"
    assert r.book_sell(dc_replace(s, ledger_net=-1.0), 5, 0.5).refusal == "bad_state"
    # a state that is not a BookState is no book -- never an EMPTY one
    # (book_buy(None, 5, 0.5) used to book 5 shares onto nothing)
    for junk in (None, 5, "x", object(), {"ledger_net": 40.0}, [r.BookState()], mi.Plan(None, 0, None, "x")):
        bb = r.book_buy(junk, 5, 0.5)
        assert bb.refusal == "bad_state" and bb.state is junk and bb.booked == 0.0, junk
        bs = r.book_sell(junk, 5, 0.5)
        assert bs.refusal == "bad_state" and bs.state is junk and bs.overfill is False, junk
        assert bs.realized is None and bs.booked == 0.0
    # a None column is 0 (the row's default), and the booking fills it in
    hollow = r.BookState(ledger_net=None, gross_buy_usd=None, peak_exposure_usd=None, realized_pnl=None)
    filled = r.book_buy(hollow, 10, 0.5)
    assert filled.refusal is None and filled.state.ledger_net == 10 and filled.state.gross_buy_usd == 5.0
    # property: whatever goes in, every column that comes out is finite
    rng = random.Random(7)
    pool = [0.5, 10, -3, 0.0, math.nan, math.inf, True, None, "x", 1e300, 10**400]
    state = r.BookState()
    for _ in range(2000):
        d, px, usd = rng.choice(pool), rng.choice(pool + [0.5, 0.31]), rng.choice(pool)
        bk = r.book_buy(state, d, px, usd=usd) if rng.random() < 0.6 else r.book_sell(state, d, px)
        state = bk.state
        for col in ("ledger_net", "gross_buy_usd", "gross_sell_usd", "peak_exposure_usd", "realized_pnl"):
            assert math.isfinite(float(getattr(state, col))), (col, state)
        assert state.avg_cost is None or math.isfinite(state.avg_cost)
        assert state.ledger_net >= 0


def test_booking_refuses_an_overflow_and_a_cost_off_the_ladder():
    # the reviewer's probe: a 1e308 ledger plus 1e308 shares wrote
    # ledger inf with refusal None; the post-check refuses it
    big = r.BookState(ledger_net=1e308, avg_cost=0.5, gross_buy_usd=5e307, peak_exposure_usd=5e307)
    b = r.book_buy(big, 1e308, 0.5)
    assert b.refusal == "bad_state" and b.state is big and b.booked == 0.0
    assert math.isfinite(b.state.ledger_net)
    g = r.book_buy(r.BookState(ledger_net=0, gross_buy_usd=1.7e308), 10, 0.5, usd=1.7e308)
    assert g.refusal == "bad_state" and g.state.gross_buy_usd == 1.7e308
    s = r.book_sell(r.BookState(ledger_net=1e308, avg_cost=0.01, realized_pnl=1.7e308), 1e308, 0.99)
    assert s.refusal == "bad_state" and s.state.realized_pnl == 1.7e308
    # an average that lands off the ladder (1e308 shares average to 0.0) is refused too
    assert r.book_buy(r.BookState(ledger_net=1e308, avg_cost=0.5), 1e308, 0.5).refusal == "bad_state"
    # avg_cost must be a price in (0, 1): 5.0 and -0.5 realize nothing
    for bad in (5.0, -0.5, 1.0, 0.0, 1.5, Decimal("2")):
        sell = r.book_sell(r.BookState(ledger_net=10, avg_cost=bad), 5, 0.5)
        assert sell.refusal == "avg_cost_unknown" and sell.realized is None and sell.booked == 0.0, bad
        buy = r.book_buy(r.BookState(ledger_net=10, avg_cost=bad), 5, 0.5)
        assert buy.refusal == "avg_cost_unknown" and buy.state.avg_cost == bad, bad
    assert r.book_sell(r.BookState(ledger_net=10, avg_cost=0.999999), 5, 0.5).refusal is None
    assert r.book_sell(r.BookState(ledger_net=10, avg_cost=Decimal("0.45")), 5, 0.5).realized == 0.25
    # a flat book with a garbage cost starts afresh on the next buy
    assert r.book_buy(r.BookState(ledger_net=0, avg_cost=5.0), 5, 0.5).state.avg_cost == 0.5
    # Decimal and a whole-valued delta are readings; a dust delta books dust, the cursor's line
    assert r.book_buy(r.BookState(), Decimal("3"), Decimal("0.5")).state.ledger_net == 3.0
    assert r.book_buy(r.BookState(), -0.0, 0.5).refusal == "nothing_to_book"


def test_dust_never_books_and_a_dust_ledger_is_flat():
    # addendum section 9: a fractional venue fill can leave a ledger of
    # 1e-8 that never closes; ONE tolerance, 1e-6 shares, for the
    # bookings and the episode
    assert r.FLAT_TOL_SHARES == 1e-6
    s = r.book_buy(r.BookState(), 40, 0.5).state
    for dust in (1e-12, 1e-7, 9.9e-7):
        assert r.book_buy(s, dust, 0.5).refusal == "nothing_to_book", dust
        sale = r.book_sell(s, dust, 0.5)
        assert sale.refusal == "nothing_to_book" and sale.state is s and sale.booked == 0.0, dust
    assert r.book_buy(s, 1e-6, 0.5).refusal is None
    assert r.book_sell(s, 1e-6, 0.5).booked == 1e-6
    # a sale onto a dust ledger books nothing; 5 shares sold against
    # 3e-7 held is still an overfill (a short), flagged
    dusty = r.BookState(ledger_net=3e-7, avg_cost=0.5)
    sale = r.book_sell(dusty, 5, 0.5)
    assert sale.booked == 0.0 and sale.refusal is None and sale.overfill is True and sale.state is dusty
    held = r.BookState(ledger_net=40.0, avg_cost=0.5)
    assert r.book_sell(held, 40.0 + 5e-7, 0.5).overfill is False
    assert r.book_sell(held, 40.0 + 2e-6, 0.5).overfill is True
    # the episode: a ledger under the tolerance is flat, at or over it is held
    assert r.episode_close(r.BookState(ledger_net=1e-8, gross_buy_usd=10.0), True, False, None, 0) == "cashed_out"
    assert r.episode_close_reason(r.BookState(ledger_net=9e-7, gross_buy_usd=10.0), True, False, None, 0) == "cashed_out"
    assert r.episode_close_reason(r.BookState(ledger_net=1e-6, gross_buy_usd=10.0), True, False, None, 0) == "held"
    assert r.episode_close_reason(r.BookState(ledger_net=0.5, gross_buy_usd=10.0), True, False, None, 0) == "held"
    assert r.episode_close(r.BookState(ledger_net=5e-7), True, False, None, 0) == "cancelled"


# ---------------------------------------------------------------- drift

def test_drift_rule_refuses_increases_and_reduces_from_the_smaller_reading():
    ok = r.drift_rule(1000.0, 990.0, fresh=True, partial=False)
    assert (ok.increase_ok, ok.reduce_from, ok.refusal) == (True, "derived", None)
    assert ok.drift == 0.01                     # 10 / max(1000, 990, 1)
    inc_ok, src, *_ = r.drift_rule(1000.0, 990.0, True, False)
    assert (inc_ok, src) == (True, "derived")
    drifted = r.drift_rule(1000.0, 900.0, True, False)
    assert (drifted.increase_ok, drifted.reduce_from, drifted.refusal) == (False, "smaller", "drift")
    assert drifted.drift == 0.1
    stale = r.drift_rule(1000.0, 1000.0, fresh=False, partial=False, last_fresh_agreed=True)
    assert (stale.increase_ok, stale.reduce_from, stale.refusal) == (False, "derived", "snapshot_stale")
    partial = r.drift_rule(1000.0, 1000.0, fresh=True, partial=True)
    assert partial.refusal == "snapshot_stale" and partial.increase_ok is False
    unread = r.drift_rule(1000.0, None, fresh=None, partial=None)
    assert unread.increase_ok is False and unread.refusal == "snapshot_stale"
    # a partial flag that was not read is not "not partial": fresh
    # means read fresh AND read complete
    half_read = r.drift_rule(1000.0, 990.0, True, None)
    assert half_read.refusal == "snapshot_stale" and half_read.increase_ok is False
    assert half_read.drift is None and half_read.reduce_from == "smaller"
    assert r.drift_rule(1000.0, 990.0, True, None, last_fresh_agreed=True).reduce_from == "derived"
    assert r.drift_rule(1000.0, 990.0, True, "no").refusal == "snapshot_stale"
    assert r.drift_rule(1000.0, 990.0, True, 0).refusal == "snapshot_stale"
    assert r.drift_rule(1000.0, 990.0, 1, False).refusal == "snapshot_stale"


def test_drift_readings_fail_closed_and_the_default_is_the_smaller():
    # nan/inf readings used to pass (nan > 0.05 is False), (None, None)
    # read as agreement at 0.0, 'x' raised: every one is now stale,
    # from the smaller, with no number
    stale = r.DriftRule(False, "smaller", "snapshot_stale", None)
    for bad in (math.nan, math.inf, -math.inf, None, "x", "990", True, -1.0, 10**400):
        assert r.drift_rule(bad, 990.0, True, False) == stale, bad
        assert r.drift_rule(1000.0, bad, True, False) == stale, bad
        assert r.drift_rule(bad, bad, True, False) == stale, bad
        assert r.drift_of(bad, 990.0) is None and r.drift_of(1000.0, bad) is None, bad
    assert r.drift_rule(None, None, True, False) == stale
    assert r.drift_rule(math.nan, math.nan, True, False, last_fresh_agreed=True) == stale
    assert r.drift_of(0, 0) == 0.0 and r.drift_of(5, 5.0) == 0.0
    assert r.drift_of(1e308, 1.0) == 1.0 and r.drift_of(1e308, -0.0) == 1.0
    # an unreadable bound refuses the increase
    assert r.drift_rule(1000.0, 990.0, True, False, drift_max=math.nan).increase_ok is False
    assert r.drift_rule(1000.0, 990.0, True, False, drift_max=None).refusal == "drift"
    # the permissive default is gone: absent the worker's assertion
    # that the last fresh read agreed, reductions come from the smaller
    assert inspect.signature(r.drift_rule).parameters["last_fresh_agreed"].default is False
    assert r.drift_rule(1000.0, 1000.0, False, False).reduce_from == "smaller"
    assert r.drift_rule(1000.0, 1000.0, False, False, last_fresh_agreed=None).reduce_from == "smaller"
    assert r.drift_rule(1000.0, 1000.0, False, False, last_fresh_agreed=1).reduce_from == "smaller"
    assert r.drift_rule(1000.0, 1000.0, False, False, last_fresh_agreed=True).reduce_from == "derived"
    # stale, and the last fresh read disagreed: reductions from the smaller
    assert r.drift_rule(1000.0, 800.0, False, False, last_fresh_agreed=False).reduce_from == "smaller"
    # exactly at the bound is within it
    assert r.drift_rule(1000.0, 950.0, True, False).increase_ok is True
    assert r.drift_of(0.0, 0.0) == 0.0 and r.drift_of(0.0, 0.5) == 0.5


def test_drift_net_rule_reads_the_net_so_an_equal_leg_merge_is_no_drift():
    # the program's case (Phase 1): his fills say +5,000 Yes / +5,000
    # No, he merges the pair on-chain, the venue shows 0 / 0. Per
    # token that is drift 1.0 on both sides -- a lifelong lock-out on
    # increases for a position that is flat; on the net it is 0
    assert r.drift_of(5000.0, 0.0) == 1.0
    assert r.drift_net_rule(5000.0, 5000.0, 0.0, 0.0) == 0.0
    # both nets zero: nothing to disagree about, 0.0 not a division
    assert r.drift_net_rule(0.0, 0.0, 0.0, 0.0) == 0.0
    assert r.drift_net_rule(0, 0, 0, 0) == 0.0
    assert r.drift_net_rule(1200.0, 1200.0, 300.0, 300.0) == 0.0
    # a partial merge: fills +1,000 / +200, venue 800 / 0 -- the same
    # net 800 on both, drift 0
    assert r.drift_net_rule(1000.0, 200.0, 800.0, 0.0) == 0.0
    # a one-sided add reads the per-token number
    assert r.drift_net_rule(1000.0, 0.0, 990.0, 0.0) == r.drift_of(1000.0, 990.0) == 0.01
    assert r.drift_net_rule(1000.0, 0.0, 900.0, 0.0) == r.drift_of(1000.0, 900.0) == 0.1
    assert r.drift_net_rule(990.0, 0.0, 1000.0, 0.0) == 0.01
    # and on the OTHER token (his short of our long): the same number
    assert r.drift_net_rule(0.0, 1000.0, 0.0, 990.0) == 0.01
    assert r.drift_net_rule(0.0, 1000.0, 0.0, 900.0) == 0.1
    # a sign disagreement is the full disagreement it is: fills net
    # +100, venue net -100 -> |200| / 100 = 2.0
    assert r.drift_net_rule(100.0, 0.0, 0.0, 100.0) == 2.0
    # one side read, the other not: fills +1,000, venue flat -> 1.0
    assert r.drift_net_rule(1000.0, 0.0, 0.0, 0.0) == 1.0
    assert r.drift_net_rule(0.0, 0.0, 1000.0, 0.0) == 1.0
    # exactly at the bound, and the 6-place rounding
    assert r.drift_net_rule(1000.0, 0.0, 950.0, 0.0) == 0.05
    assert r.drift_net_rule(3.0, 0.0, 1.0, 0.0) == round(2.0 / 3.0, 6)
    # Decimal and whole-valued ints are readings
    assert r.drift_net_rule(Decimal("1000"), Decimal("0"), Decimal("990"), 0) == 0.01
    # the denominator is the larger |net| with no share floor: a
    # sub-share net against a flat venue is full disagreement (the
    # closed reading), where the per-token rule's 1-share floor reads
    # 0.5 -- the integrating phase chooses which number rides
    assert r.drift_net_rule(0.5, 0.0, 0.0, 0.0) == 1.0 and r.drift_of(0.5, 0.0) == 0.5
    # every input by the per-token standard: None / bool / str / NaN /
    # inf / a negative / an int too big for a float -> None, never a
    # guess, in each of the four positions
    good = [1000.0, 200.0, 800.0, 0.0]
    for bad in (None, True, False, "1000", "", b"1", math.nan, math.inf, -math.inf,
                -1.0, -0.001, -1, 10**400):
        for i in range(4):
            args = list(good)
            args[i] = bad
            assert r.drift_net_rule(*args) is None, (i, bad)
        assert r.drift_net_rule(bad, bad, bad, bad) is None, bad
    # negatives refused even where they would net to agreement
    assert r.drift_net_rule(-5.0, -5.0, 0.0, 0.0) is None
    assert r.drift_net_rule(100.0, 0.0, 100.0, -0.0) == 0.0     # a negative zero is a reading of nothing

    class Boom:
        def __float__(self):
            raise RuntimeError("boom")

    assert r.drift_net_rule(Boom(), 0.0, 0.0, 0.0) is None
    assert r.drift_net_rule(0.0, 0.0, 0.0, Boom()) is None
    # an overflow in the arithmetic is no number either
    assert r.drift_net_rule(1e308, 0.0, 0.0, 1e308) is None
    # the pinned per-token rule is untouched beside it
    assert r.drift_rule(5000.0, 0.0, True, False).refusal == "drift"
    assert r.drift_of(5000.0, 0.0) == 1.0
    assert "drift_net_rule" in r.__all__


# -------------------------------------------------------- episode close

def test_episode_close_rules():
    bought = r.book_buy(r.BookState(), 20, 0.5).state
    flat = r.book_sell(bought, 20, 0.5).state
    never = r.BookState()
    # flat + market closed -> cashed_out; never filled -> cancelled
    assert r.episode_close(flat, True, False, None, 0) == "cashed_out"
    assert r.episode_close(never, True, False, None, 0) == "cancelled"
    # shares still held on a closed market: settlement closes it, not us
    assert r.episode_close(bought, True, False, None, 0) is None
    # he has left and we are flat
    assert r.episode_close(flat, False, True, None, 0) == "cashed_out"
    # flat at target 0 long enough
    assert r.episode_close(flat, False, False, r.MIRROR_FLAT_CLOSE_S, 0) == "cashed_out"
    assert r.episode_close(flat, False, False, r.MIRROR_FLAT_CLOSE_S - 1, 0) is None
    assert r.episode_close(flat, False, False, None, 0) is None
    # an order still non-terminal, or an unreadable count: nothing closes
    assert r.episode_close(flat, True, True, r.MIRROR_FLAT_CLOSE_S, 1) is None
    assert r.episode_close(flat, True, True, r.MIRROR_FLAT_CLOSE_S, None) is None
    # unreadable market / vanish facts never close on their own
    assert r.episode_close(flat, None, None, None, 0) is None


def test_episode_close_fails_closed_on_garbage_and_names_why():
    bought = r.book_buy(r.BookState(), 20, 0.5).state
    flat = r.book_sell(bought, 20, 0.5).state
    never = r.BookState()
    # open_orders must be a whole count: False/'0'/0.0-as-a-string,
    # nan, a negative, None, a bool never close, and are named
    for bad in (False, True, "0", b"0", None, math.nan, math.inf, -1, 0.5):
        assert r.episode_close(flat, True, True, r.MIRROR_FLAT_CLOSE_S, bad) is None, bad
        assert r.episode_close(never, True, True, r.MIRROR_FLAT_CLOSE_S, bad) is None, bad
        assert r.episode_close_reason(never, True, True, None, bad) == "bad_open_orders", bad
    assert r.episode_close(never, True, False, None, 0.0) is None                # a count arrives as an int
    assert r.episode_close_reason(never, True, False, None, 0.0) == "bad_open_orders"
    assert r.episode_close_reason(flat, True, False, None, 1) == "orders_open"
    # a ledger that is not a number never closes -- and is never 'cancelled'
    for col in ("ledger_net", "gross_buy_usd", "gross_sell_usd", "peak_exposure_usd", "realized_pnl"):
        for bad in (math.nan, math.inf, -math.inf, True, "0"):
            broken = dc_replace(never, **{col: bad})
            assert r.episode_close(broken, True, True, r.MIRROR_FLAT_CLOSE_S, 0) is None, (col, bad)
            assert r.episode_close_reason(broken, True, True, None, 0) == "bad_state", (col, bad)
    assert r.episode_close_reason(dc_replace(never, ledger_net=-1.0), True, True, None, 0) == "bad_state"
    # a state that is not a BookState never closes as an empty book
    # (episode_close(None, True, False, None, 0) used to be 'cancelled')
    for junk in (None, 5, "x", object(), {"ledger_net": 40.0}, [never], mi.Plan(None, 0, None, "x")):
        assert r.episode_close(junk, True, False, None, 0) is None, junk
        assert r.episode_close(junk, True, True, r.MIRROR_FLAT_CLOSE_S, 0) is None, junk
        assert r.episode_close_reason(junk, True, False, None, 0) == "bad_state", junk
    # the rest of the census
    assert r.episode_close_reason(bought, True, False, None, 0) == "held"
    assert r.episode_close_reason(flat, False, False, None, 0) == "not_due"
    assert r.episode_close_reason(flat, False, False, r.MIRROR_FLAT_CLOSE_S - 1, 0) == "not_due"
    assert r.episode_close_reason(flat, False, False, r.MIRROR_FLAT_CLOSE_S, 0) == "cashed_out"
    assert r.episode_close_reason(never, False, False, r.MIRROR_FLAT_CLOSE_S, 0) == "cancelled"
    for bad in (math.nan, math.inf, True, "9999", None):
        assert r.episode_close_reason(flat, False, False, bad, 0) == "not_due", bad
        assert r.episode_close_reason(flat, False, False, 9999.0, 0, flat_close_s=bad) == "not_due", bad
    # every "why not" is None to episode_close: only the two closes come through
    for args in ((never, True, True, None, None), (never, True, True, None, 1),
                 (dc_replace(never, ledger_net=math.nan), True, True, None, 0),
                 (bought, True, False, None, 0), (flat, False, False, None, 0)):
        assert r.episode_close(*args) is None, args
        assert r.episode_close_reason(*args) not in ("cashed_out", "cancelled"), args


def test_an_open_order_count_must_arrive_as_an_int():
    # counted, never computed: 0.0, -0.0 and Decimal('0') used to close
    never = r.BookState()
    for bad in (0.0, -0.0, Decimal("0"), 1.0, "0", False, True, None, math.nan, -1, 0.5):
        assert r.episode_close(never, True, False, None, bad) is None, bad
        assert r.episode_close_reason(never, True, False, None, bad) == "bad_open_orders", bad
    assert r.episode_close(never, True, False, None, 0) == "cancelled"
    assert r.episode_close_reason(never, True, False, None, 2) == "orders_open"
    assert r.episode_close_reason(never, True, False, None, 10**400) == "orders_open"


# ------------------------------------------------------------ admission

def test_admission_admits_the_clean_candidate_and_names_the_first_refusal_in_order():
    assert r.admission(_admitted()) is None
    assert r.admission(r.AdmissionFacts()) == "mode_env_off"
    assert r.admission(r.AdmissionFacts(increases_refusal="mode_db_off")) == "mode_db_off"
    expect = [
        ({"increases_ok": False, "increases_refusal": "mode_db_unreadable"}, "mode_db_unreadable"),
        ({"per_fill_usd": 0.0}, "clip_zero"),
        ({"family": "total"}, "family"),
        ({"per_side": True}, "per_side_unsupported"),
        ({"market_closed": True}, "market_closed"),
        ({"market_resolved": True}, "market_closed"),
        ({"game_too_far_out": True}, "game_too_far_out"),
        ({"mapping_ok": False, "mapping_why": "quarantine"}, "mapping:quarantine"),
        ({"edge_ok": False, "edge_why": "not funded"}, "edge_gate:not funded"),
        ({"cell_ok": False, "cell_clause": "off_cell"}, "cell_gate_off_cell"),
        ({"legacy_row": True}, "legacy_row"),
        ({"slug_recent_copy": True}, "slug_recent_copy"),
        ({"underdog_coholds": True}, "underdog_coholds"),
        ({"venue_net": 12.0}, "venue_already_holds"),
        ({"venue_net": -12.0}, "venue_already_holds"),
        ({"kalshi_claimed": True}, "kalshi_claimed"),
        ({"side_band_hit": True}, "side_band"),
        ({"snap_fresh": False}, "snapshot_stale"),
        ({"drift": 0.06}, "drift"),
        ({"books_live": r.MIRROR_MAX_LIVE_BOOKS}, "max_books"),
        ({"opened_today": r.MIRROR_MAX_BOOKS_PER_DAY}, "max_books"),
        ({"first_fill_ok": False}, "first_fill_gate"),
    ]
    for over, name in expect:
        assert r.admission(_admitted(**over)) == name, over
    # two refusals: the earlier gate names it
    assert r.admission(_admitted(legacy_row=True, first_fill_ok=False)) == "legacy_row"
    assert r.admission(_admitted(per_fill_usd=0.0, legacy_row=True)) == "clip_zero"


def test_admission_fails_closed_on_every_unread_fact():
    unread = [
        ({"increases_ok": None}, "mode_env_off"),
        ({"per_fill_usd": None}, "clip_unreadable"),
        ({"family": None}, "family"),
        ({"per_side": None}, "per_side_unsupported"),
        ({"market_closed": None}, "market_closed"),
        ({"market_resolved": None}, "market_closed"),
        ({"game_too_far_out": None}, "game_too_far_out"),
        ({"mapping_ok": None}, "mapping:unreadable"),
        ({"edge_ok": None}, "edge_gate:unreadable"),
        ({"cell_ok": None}, "cell_gate_unreadable"),
        ({"legacy_row": None}, "legacy_row"),
        ({"slug_recent_copy": None}, "slug_recent_copy"),
        ({"underdog_coholds": None}, "underdog_coholds"),
        ({"venue_net": None}, "positions_unreadable"),
        ({"venue_net": "??"}, "positions_unreadable"),
        ({"kalshi_claimed": None}, "kalshi_claimed"),
        ({"side_band_hit": None}, "side_band"),
        ({"snap_fresh": None}, "snapshot_stale"),
        ({"drift": None}, "drift"),
        ({"drift": float("nan")}, "drift"),
        ({"books_live": None}, "max_books"),
        ({"opened_today": None}, "max_books"),
        ({"first_fill_ok": None}, "first_fill_gate"),
    ]
    for over, name in unread:
        assert r.admission(_admitted(**over)) == name, over


def test_admission_parses_every_number_and_never_raises():
    # a flag admits only when it IS the admitting value
    for bad in (None, 1, "yes", "True", [True]):
        assert r.admission(_admitted(increases_ok=bad)) == "mode_env_off", bad
        assert r.admission(_admitted(increases_ok=bad, increases_refusal="mode_db_off")) == "mode_db_off", bad
    assert r.admission(_admitted(increases_ok=False, increases_refusal=None)) == "mode_env_off"
    assert r.admission(_admitted(increases_ok=False, increases_refusal="")) == "mode_env_off"
    # the clip by mirror_target's rule: True/inf/'50' are unreadable,
    # not 1/unbounded/50
    for bad in (True, math.inf, "50", b"50", math.nan, None):
        assert r.admission(_admitted(per_fill_usd=bad)) == "clip_unreadable", bad
        assert r.admission(_admitted(per_fill_usd=bad), increase=True) == "clip_unreadable", bad
    for zero in (0, 0.0, -1, -50.0):
        assert r.admission(_admitted(per_fill_usd=zero)) == "clip_zero", zero
    # family must be a string in the set (a list used to raise)
    for bad in (["moneyline"], {"moneyline"}, 5, b"moneyline"):
        assert r.admission(_admitted(family=bad)) == "family", bad
    # the why strings never raise when they are not strings
    assert r.admission(_admitted(mapping_ok=False, mapping_why=5)) == "mapping:unreadable"
    assert r.admission(_admitted(edge_ok=False, edge_why="")) == "edge_gate:unreadable"
    assert r.admission(_admitted(cell_ok=False, cell_clause=None)) == "cell_gate_unreadable"
    # the venue net: False is not 0 and '0' is not 0
    for bad in (False, True, "0", b"0", math.nan, math.inf, None):
        assert r.admission(_admitted(venue_net=bad)) == "positions_unreadable", bad
    assert r.admission(_admitted(venue_net=0)) is None
    assert r.admission(_admitted(venue_net=1e-9)) == "venue_already_holds"
    # drift by the same rule
    for bad in ("0.01", True, math.nan, math.inf, -0.01, None):
        assert r.admission(_admitted(drift=bad)) == "drift", bad
    assert r.admission(_admitted(drift=0)) is None
    assert r.admission(_admitted(drift=r.MIRROR_DRIFT_MAX)) is None
    # the counts: nan/'x'/inf/-1/a fraction/a bool never raise, all refuse
    for bad in (math.nan, "x", "4", math.inf, -1, 2.5, True, False, None, 10**400):
        assert r.admission(_admitted(books_live=bad)) == "max_books", bad
        assert r.admission(_admitted(opened_today=bad)) == "max_books", bad
    assert r.admission(_admitted(books_live=4.0, opened_today=0)) is None
    assert r.admission(_admitted(books_live=r.MIRROR_MAX_LIVE_BOOKS - 1)) is None
    # every fact in the dataclass defaults to its fail-closed value
    assert r.admission(r.AdmissionFacts(increases_ok=True)) == "clip_unreadable"
    assert r.AdmissionFacts().per_side is None and r.AdmissionFacts().increases_ok is None
    # per-side markets: refused on open, not part of the increase re-check
    assert r.admission(_admitted(per_side=True)) == "per_side_unsupported"
    assert r.admission(_admitted(per_side=1)) == "per_side_unsupported"
    assert r.admission(_admitted(per_side=True), increase=True) is None


def test_facts_that_are_not_facts_and_defaults_that_are_not_numbers():
    # something that is not an AdmissionFacts used to raise AttributeError
    for junk in (None, object(), 5, "facts", {}, [r.AdmissionFacts()], mi.Plan(None, 0, None, "x")):
        assert r.admission(junk) == "facts_unreadable", junk
        assert r.admission(junk, increase=True) == "facts_unreadable", junk
    # a cap with an unreadable default is its floor (the most closed);
    # a wait with one never elapses
    for bad in ("SELL_LONG", None, math.nan, True, b"5", math.inf):
        assert r.capped_env("MIRROR_NOPE", bad) == 0.0, bad
        assert r.capped_env("MIRROR_NOPE", bad, floor=1.0) == 1.0, bad
        assert r.capped_env("MIRROR_NOPE", 250.0, floor=bad) == 250.0, bad
        assert r.min_wait_env("MIRROR_NOPE", bad) == math.inf, bad
    assert r.min_wait_env("MIRROR_NOPE", -1) == math.inf
    assert r.capped_env("MIRROR_NOPE", 250.0, floor=None) == 250.0
    assert r.capped_env("MIRROR_NOPE", Decimal("5")) == 5.0


def test_increase_recheck_is_the_starred_clauses_only():
    # a live book keeps adding while mode, clip, mapping, edge and cell hold
    live = _admitted(legacy_row=True, slug_recent_copy=True, venue_net=40.0, books_live=5,
                     opened_today=5, snap_fresh=False, family="total", market_closed=None,
                     per_side=True)
    assert r.admission(live, increase=True) is None
    assert r.admission(_admitted(increases_ok=False, increases_refusal="mode_db_off"),
                       increase=True) == "mode_db_off"
    assert r.admission(_admitted(per_fill_usd=0), increase=True) == "clip_zero"
    assert r.admission(_admitted(mapping_ok=False, mapping_why="hold"), increase=True) == "mapping:hold"
    assert r.admission(_admitted(edge_ok=False), increase=True) == "edge_gate:unreadable"
    assert r.admission(_admitted(cell_ok=False, cell_clause="league"), increase=True) == "cell_gate_league"


def test_a_per_market_fresh_read_admits_where_the_whole_book_walk_is_stale():
    # Phase 1: RN1's positions walk is truncated on every probe, so
    # snap_fresh is never True for him and `snapshot_stale` refused
    # every RN1 candidate. A per-market read of both tokens, stamped
    # fresh and complete for THAT market, is the second sight of him
    import dataclasses

    # the default is the fail-closed None, appended last so nothing
    # built positionally changes meaning
    assert r.AdmissionFacts().snap_market_fresh is None
    assert dataclasses.fields(r.AdmissionFacts)[-1].name == "snap_market_fresh"
    # the existing helper never sets it and still admits on snap_fresh
    assert r.admission(_admitted()) is None
    # the per-market read admits where the walk is stale or unread
    for walk in (None, False):
        assert r.admission(_admitted(snap_fresh=walk, snap_market_fresh=True)) is None, walk
        # and the drift clause still rides after it: the name that
        # follows is drift, not snapshot_stale
        assert r.admission(_admitted(snap_fresh=walk, snap_market_fresh=True, drift=0.06)) == "drift", walk
        assert r.admission(_admitted(snap_fresh=walk, snap_market_fresh=True, drift=None)) == "drift", walk
    assert r.admission(_admitted(snap_fresh=True, snap_market_fresh=True)) is None
    assert r.admission(_admitted(snap_fresh=True, snap_market_fresh=None)) is None
    assert r.admission(_admitted(snap_fresh=True, snap_market_fresh=False)) is None
    # neither sight: the existing name
    for walk in (None, False):
        for market in (None, False):
            assert r.admission(_admitted(snap_fresh=walk, snap_market_fresh=market)) == "snapshot_stale", (walk, market)
    # only the bool True admits: 1, 'True', 1.0, a list are unread
    for bad in (1, 0, "True", "true", 1.0, [True], Decimal("1"), object()):
        assert r.admission(_admitted(snap_fresh=None, snap_market_fresh=bad)) == "snapshot_stale", bad
        assert r.admission(_admitted(snap_fresh=False, snap_market_fresh=bad)) == "snapshot_stale", bad
        assert r.admission(_admitted(snap_fresh=bad, snap_market_fresh=bad)) == "snapshot_stale", bad
        # a garbage walk flag beside a true market read still admits
        assert r.admission(_admitted(snap_fresh=bad, snap_market_fresh=True)) is None, bad
    # the clause keeps its place: side_band before it, drift after it
    assert r.admission(_admitted(side_band_hit=True, snap_fresh=False, snap_market_fresh=True)) == "side_band"
    assert r.admission(_admitted(snap_fresh=False, snap_market_fresh=False, drift=0.06)) == "snapshot_stale"
    # not part of the increase re-check, like snap_fresh
    assert r.admission(_admitted(snap_fresh=False, snap_market_fresh=False), increase=True) is None
    # the worker's keyword construction is unaffected: every field the
    # worker names today is still accepted and the new one is optional
    kw = {f.name: getattr(_admitted(), f.name) for f in dataclasses.fields(r.AdmissionFacts)
          if f.name != "snap_market_fresh"}
    assert r.admission(r.AdmissionFacts(**kw)) is None
    assert r.admission(r.AdmissionFacts(**{**kw, "snap_fresh": False})) == "snapshot_stale"
    assert r.admission(r.AdmissionFacts(**{**kw, "snap_fresh": False, "snap_market_fresh": True})) is None


# -------------------------------------------------------------- P2 gate

def test_p2_verdict_passes_on_the_spec_numbers():
    ok, failures = r.p2_verdict(_p2_numbers())
    assert ok is True and failures == []
    # the interval may arrive as lo/hi or as roi and se under the one Z95
    n = _p2_numbers()
    del n["ci95"]
    n.update({"ci_lo": 0.012, "ci_hi": 0.081})
    assert r.p2_verdict(n) == (True, [])
    n = _p2_numbers()
    del n["ci95"]
    n.update({"roi": 0.05, "se": 0.02})
    assert r.p2_verdict(n) == (True, [])
    n.update({"roi": 0.05, "se": 0.05})       # 0.05 - 1.96 x 0.05 < 0
    assert r.p2_verdict(n) == (False, ["ci_lo<=0"])
    assert r.demotion_due(_p2_numbers()) is False


def test_p2_verdict_fails_on_29_games():
    ok, failures = r.p2_verdict(_p2_numbers(games=29))
    assert ok is False and failures == [f"games<{proof.MIN_PROOF_CLUSTERS}"]
    ok, failures = r.p2_verdict(_p2_numbers(closed_books=29, games=29))
    assert failures == [f"books<{roster_rules.MIN_N_PROMOTE}", f"games<{proof.MIN_PROOF_CLUSTERS}"]
    assert r.p2_verdict(_p2_numbers(closed_books=30, games=30)) == (True, [])


def test_p2_verdict_fails_on_an_upper_bound_below_zero_and_names_the_demotion():
    ok, failures = r.p2_verdict(_p2_numbers(ci95=[-0.09, -0.01]))
    assert ok is False and failures == ["ci_lo<=0", "demoted"]
    assert r.demotion_due(_p2_numbers(ci95=[-0.09, -0.01])) is True
    # under MIN_N_DEMOTE books the reading is not yet a demotion
    n = _p2_numbers(ci95=[-0.09, -0.01], closed_books=roster_rules.MIN_N_DEMOTE - 1)
    assert r.demotion_due(n) is False and "demoted" not in r.p2_verdict(n)[1]
    # a lower bound at zero is not proof
    assert r.p2_verdict(_p2_numbers(ci95=[0.0, 0.08])) == (False, ["ci_lo<=0"])
    assert r.demotion_due({}) is False


def test_p2_verdict_fails_on_maker_share_below_half_and_on_a_fill_above_him():
    assert r.p2_verdict(_p2_numbers(maker_share=0.49)) == (False, [f"maker_share<{r.P2_MAKER_SHARE_MIN}"])
    assert r.p2_verdict(_p2_numbers(maker_share=0.5)) == (True, [])
    assert r.p2_verdict(_p2_numbers(at_or_better=0.999)) == (False, ["at_or_better<1"])
    assert r.p2_verdict(_p2_numbers(take_slip_median=0.011)) == (False, ["take_slip>1c"])


def test_p2_verdict_fails_on_any_integrity_counter():
    for key in r.P2_INTEGRITY_COUNTERS:
        ok, failures = r.p2_verdict(_p2_numbers(**{key: 1}))
        assert ok is False and failures == [key], key
    assert r.p2_verdict(_p2_numbers(frozen_ticks=40, live_ticks=4000)) == (False, ["frozen_ticks>=1%"])
    assert r.p2_verdict(_p2_numbers(frozen_ticks=39, live_ticks=4000)) == (True, [])
    assert r.p2_verdict(_p2_numbers(live_ticks=0)) == (False, ["unreadable:live_ticks"])
    assert r.p2_verdict(_p2_numbers(drift_p90=0.051)) == (False, [f"drift_p90>{r.MIRROR_DRIFT_MAX}"])
    assert r.p2_verdict(_p2_numbers(census_missing=["overfill", "expired"])) == (
        False, ["census_missing:expired,overfill"])
    assert r.p2_verdict(_p2_numbers(why_overflow=True)) == (False, ["why_overflow"])


def test_p2_verdict_reads_nothing_from_memory():
    ok, failures = r.p2_verdict({})
    assert ok is False
    for key in ("closed_books", "games", "ci95", "at_or_better", "maker_share",
                "take_slip_median", "frozen_ticks", "live_ticks", "drift_p90", "capture",
                "census_missing", "why_overflow", *r.P2_INTEGRITY_COUNTERS):
        assert f"unreadable:{key}" in failures, key
    assert r.p2_verdict(_p2_numbers(closed_books=None))[1] == ["unreadable:closed_books"]
    assert r.p2_verdict(_p2_numbers(maker_share="most"))[1] == ["unreadable:maker_share"]
    assert r.p2_verdict(_p2_numbers(drift_p90=math.inf))[1] == ["unreadable:drift_p90"]
    assert r.p2_verdict(_p2_numbers(ci95=[0.01, None]))[1] == ["unreadable:ci95"]
    assert r.p2_verdict(_p2_numbers(census_missing=None))[1] == ["unreadable:census_missing"]
    assert r.p2_verdict("nonsense")[0] is False


def test_p2_interval_and_every_number_are_held_to_one_reading():
    # the interval used to be exempt: [inf, inf], [True, True], lo > hi
    # and a negative se all PASSED the gate
    for ci in ([math.inf, math.inf], [True, True], [0.5, 0.1], [math.nan, 0.1], [0.01, math.inf],
               ["0.01", "0.08"], [0.01], [0.01, 0.08, 0.1], "0.01,0.08", 0.05, [None, 0.1], [0.1, None]):
        assert r.p2_verdict(_p2_numbers(ci95=ci)) == (False, ["unreadable:ci95"]), ci
        assert r.demotion_due(_p2_numbers(ci95=ci)) is False, ci
    n = _p2_numbers()
    del n["ci95"]
    assert r.p2_verdict({**n, "ci_lo": 0.08, "ci_hi": 0.01}) == (False, ["unreadable:ci95"])
    assert r.p2_verdict({**n, "ci_lo": 0.01, "ci_hi": math.inf}) == (False, ["unreadable:ci95"])
    assert r.p2_verdict({**n, "ci_lo": 0.01}) == (False, ["unreadable:ci95"])
    assert r.p2_verdict({**n, "ci_lo": True, "ci_hi": True}) == (False, ["unreadable:ci95"])
    assert r.p2_verdict({**n, "roi": 0.05, "se": -0.01}) == (False, ["unreadable:ci95"])
    assert r.p2_verdict({**n, "roi": 0.05, "se": math.nan}) == (False, ["unreadable:ci95"])
    assert r.p2_verdict({**n, "roi": True, "se": 0.01}) == (False, ["unreadable:ci95"])
    assert r.p2_verdict({**n, "roi": 0.05}) == (False, ["unreadable:ci95"])
    assert r.p2_verdict({**n, "roi": 0.05, "se": 0.0}) == (True, [])
    assert r.p2_verdict({**n, "ci_lo": 0.01, "ci_hi": 0.01}) == (True, [])
    # a malformed first form never falls through to a good second one
    assert r.p2_verdict({**n, "ci95": [math.inf, math.inf], "ci_lo": 0.01, "ci_hi": 0.08}) == (
        False, ["unreadable:ci95"])
    # (5) capture: read for plausibility, reported by capture_short,
    # never gated by its size
    assert r.p2_verdict(_p2_numbers(capture=0.3)) == (True, [])
    assert r.p2_verdict(_p2_numbers(capture=0.0)) == (True, [])
    for bad in (None, math.nan, math.inf, -0.1, True, "0.6"):
        assert r.p2_verdict(_p2_numbers(capture=bad)) == (False, ["unreadable:capture"]), bad
        assert r.capture_short(_p2_numbers(capture=bad)) is None, bad
    assert r.capture_short(_p2_numbers(capture=0.3)) is True
    assert r.capture_short(_p2_numbers(capture=r.P2_CAPTURE_MIN)) is False
    assert r.capture_short(_p2_numbers()) is False and r.capture_short({}) is None
    assert r.capture_short({"capture": r.P2_CAPTURE_MIN - 1e-9}) is True
    # plausibility: a figure that cannot be is a figure not computed
    for key, bads in (("at_or_better", (1.2, -0.1, 1.0000001)),
                      ("maker_share", (1.5, -0.01)),
                      ("take_slip_median", (-2.0, -1.01)),
                      ("frozen_ticks", (-1, 2.5)),
                      ("live_ticks", (-1, 2.5)),
                      ("closed_books", (34.5, -1, "34", True)),
                      ("games", (-3, 31.5)),
                      ("drift_p90", (-0.1,)),
                      *((c, (-1, 0.5)) for c in r.P2_INTEGRITY_COUNTERS)):
        for bad in bads:
            assert r.p2_verdict(_p2_numbers(**{key: bad})) == (False, [f"unreadable:{key}"]), (key, bad)
    # the plausible edges pass
    assert r.p2_verdict(_p2_numbers(take_slip_median=-1.0, frozen_ticks=0, drift_p90=0.0)) == (True, [])
    assert r.p2_verdict(_p2_numbers(closed_books=34.0, games=31.0)) == (True, [])
    assert r.demotion_due(_p2_numbers(closed_books=-5, ci95=[-0.09, -0.01])) is False


def test_p2_games_never_exceed_closed_books():
    # a game is counted through a closed book: more games than books
    # is a count nobody made (the reviewer's probe: 100 games over 30
    # books passed)
    assert r.p2_verdict(_p2_numbers(closed_books=30, games=100)) == (False, ["unreadable:games"])
    assert r.p2_verdict(_p2_numbers(closed_books=34, games=35)) == (False, ["unreadable:games"])
    assert r.p2_verdict(_p2_numbers(closed_books=34, games=34)) == (True, [])
    assert r.p2_verdict(_p2_numbers(closed_books=29, games=30)) == (
        False, [f"books<{roster_rules.MIN_N_PROMOTE}", "unreadable:games"])
    assert r.p2_verdict(_p2_numbers(closed_books=29, games=29)) == (
        False, [f"books<{roster_rules.MIN_N_PROMOTE}", f"games<{proof.MIN_PROOF_CLUSTERS}"])
    # an interval that arrives as Decimals is an interval
    assert r.p2_verdict(_p2_numbers(ci95=(Decimal("0.012"), Decimal("0.081")))) == (True, [])


@pytest.mark.parametrize("ledger", [0, 3, 40, 100, 250])
def test_a_negative_raw_target_never_yields_a_short_plan(ledger):
    # the long-only chain: a negative raw target is 0, and a plan from 0
    # is at most a SELL of what we hold -- never BUY_SHORT
    t = r.mirror_target(0.05, -5000.0, 0.5, 50.0)
    assert t["target"] == 0 and t["refusal"] == "short_side_refused"
    p = mi.plan(t["target"], ledger, ledger, mi.Book(bid=0.49, ask=0.51), 0.5, 0.5)
    assert p.side in (None, r.SELL) and p.qty <= ledger
    assert (p.side == r.SELL) == (ledger > 0) and p.qty == ledger


# ---------------------------------------------------------------- addendum §11
# three fail-closed nits from the rules module's final review (owner
# order 2026-09-02 "go for it, let's get this working")


def test_a_side_whose_eq_raises_is_not_a_side():
    class Boom:
        def __eq__(self, other):
            raise RuntimeError("eq")

        __hash__ = object.__hash__

    # a marketable book, so only the side guard can refuse
    assert r.at_or_through(r.BUY, 0.46, 0.47, 0.47) is True
    for bad in (Boom(), None, 1, b"BUY", ["BUY"]):
        assert r.at_or_through(bad, 0.46, 0.47, 0.47) is False


def test_a_census_item_whose_str_raises_is_an_unreadable_census():
    class Boom:
        def __str__(self):
            raise RuntimeError("str")

    ok, failures = r.p2_verdict(_p2_numbers(census_missing=[Boom()]))
    assert ok is False and failures == ["unreadable:census_missing"]
    # and a readable census still names what is missing
    assert r.p2_verdict(_p2_numbers(census_missing=["b", "a"])) == (False, ["census_missing:a,b"])


def test_an_env_name_that_is_not_a_string_reads_as_absent(monkeypatch):
    monkeypatch.setenv("MIRROR_TEST_FLOAT", "0.5")
    assert r._env_float("MIRROR_TEST_FLOAT") == 0.5
    for bad in (None, 1, b"MIRROR_TEST_FLOAT", ["MIRROR_TEST_FLOAT"]):
        assert r._env_float(bad) is None
