"""The $2,500 cap is per TRADE, not per match or per market (owner order
2026-09-09 ~21:05Z).

Owner, verbatim: "I need the 2500 cap to be on a per trade basis not
on a per match or market basis. Meaning we could have more than 2500
on an individual match if there are more than one trades in the same
match". Galatasaray / Sporting (books 1221 and 1267, h2022_db): his
39,446-share short on one moneyline and 28,851 on the other read 3,945
and 2,885 shares at 10%; the per-game $2,500 went to the first book
($2,025.65 at cost) and the second got the $445 left (645 shares,
`game_cap_scaled`) -- 80% and 22% of his proportion.

The rule: rules.MIRROR_NET_CAP_USD is UNBOUNDED by code default
(math.inf, unbounded_env's one spelling), so the per-market cap at the
mark and the per-game room built on it never bind; the $2,500 lives on
the ORDER (rules.MIRROR_CLIP_USD, room_scale), so a target past $2,500
at the mark is reached across more than one rest. The environment may
still LOWER the cap to a finite figure, and that lowered reading is
exactly E1's per-game room -- the suite's autouse fixture sets the
module attribute to $2,500 for every test written under it; THIS
module opts out (NET_CAP_UNBOUNDED) and runs on the production default.
"""

from __future__ import annotations

import importlib
import inspect
import json
import math
import pathlib
import re

import pytest

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_mirror_live_worker import NOW, SLUG, _armed  # noqa: F401 — the fixture
from tests.test_mirror_live_worker import _cancels, _census, _game_world, _places, _shadow_row, _tick

NET_CAP_UNBOUNDED = True
DOCS = pathlib.Path(__file__).resolve().parents[2] / "docs" / "mirror-coverage.md"


# ------------------------------------------------------------ 1. the constant

def test_the_cap_is_unbounded_by_code_default_and_the_environment_may_only_lower_it(monkeypatch):
    assert rules.MIRROR_NET_CAP_USD == math.inf, "the production default (this module opts out of the fixture)"
    src = inspect.getsource(rules)
    assert src.count('MIRROR_NET_CAP_USD = max(MIRROR_NET_CAP_FLOOR_USD, unbounded_env("MIRROR_NET_CAP_USD"))') == 1
    assert 'MIRROR_CLIP_USD = capped_env("MIRROR_CLIP_USD", 2500.0, floor=1.0)' in src, "the per-order clip stays"
    assert rules.MIRROR_CLIP_USD == 2500.0 and mi.MARKET_NET_CAP_USD == 2500.0, "the shadow keeps its number"
    # the environment: a finite figure lowers, 0 / negative land on the $1
    # floor (net_cap_zero is not a cap), 'inf' / junk are the default
    for v, want in (("25", 25.0), ("2500", 2500.0), ("0", 1.0), ("-1", 1.0), ("0.5", 1.0),
                    ("inf", math.inf), ("banana", math.inf), ("", math.inf)):
        monkeypatch.setenv("MIRROR_NET_CAP_USD", v)
        try:
            mod = importlib.reload(rules)
            assert mod.MIRROR_NET_CAP_USD == want, (v, mod.MIRROR_NET_CAP_USD)
        finally:
            monkeypatch.delenv("MIRROR_NET_CAP_USD")
            importlib.reload(rules)
    assert rules.MIRROR_NET_CAP_USD == math.inf


def test_mirror_target_and_game_room_admit_the_unbounded_cap_and_still_honour_a_lowered_one():
    # unbounded: the arithmetic target, never capped, cap None reads the module
    t = rules.mirror_target(0.1, -39446.0, 0.36, 50.0, allow_short=True)
    assert t["refusal"] is None and t["target"] == -3944 and t["capped"] is False
    assert rules.mirror_target(0.1, -39446.0, 0.36, 50.0, cap_usd=math.inf, allow_short=True)["target"] == -3944
    assert rules.mirror_target(0.5, 24423.0, 0.4574, 50.0)["target"] == 12211 and \
        rules.mirror_target(0.5, 24423.0, 0.4574, 50.0)["capped"] is False
    # a caller's finite cap still tightens (the argument never raises past the module: the module is unbounded)
    low = rules.mirror_target(0.5, 24423.0, 0.4574, 50.0, cap_usd=25.0)
    assert low["capped"] is True and low["target"] == int(25.0 / 0.4574)
    big = rules.mirror_target(0.5, 24423.0, 0.4574, 50.0, cap_usd=1e9)
    assert big["capped"] is False and big["target"] == 12211
    # no exposure is still named first
    for bad in (0.0, -1.0, math.nan, -math.inf, "2500", True, None if False else 0):
        assert rules.mirror_target(0.5, 24423.0, 0.4574, 50.0, cap_usd=bad)["refusal"] == "net_cap_zero", bad
    # the game room: unbounded is math.inf whatever the siblings hold;
    # a lowered cap reads E1's arithmetic byte for byte
    assert rules.game_room(1800.0) == math.inf and rules.game_room(None) == math.inf
    assert rules.game_room(1800.0, math.inf) == math.inf
    assert rules.game_room(1800.0, 2500.0) == 700.0 and rules.game_room(None, 2500.0) == 0.0
    assert rules.game_room(0.0, 2500.0) == 2500.0 and rules.game_room(3000.0, 2500.0) == 0.0
    # positive infinity is the ONE spelling of no cap: NaN and -inf are no
    # room, fail closed (the review's HIGH-2, mutant M12)
    assert rules.game_room(1800.0, math.nan) == 0.0 and rules.game_room(1800.0, -math.inf) == 0.0
    assert rules.game_room(0.0, math.nan) == 0.0 and rules.game_room(0.0, -math.inf) == 0.0
    assert rules.game_capped(3000, 1400, 0, False) == (1400, "game_cap_scaled")
    assert rules.mirror_target.__defaults__[0] is None, "cap_usd None reads the module at call time"


# -------------------------------------------------- 2. the tick, per trade

def test_a_second_market_of_a_game_over_2500_is_sized_at_the_full_ten_percent(monkeypatch):
    """test_the_cap_is_per_game_...'s world: A holds 3,600 @ 0.50 =
    $1,800 at cost; B's 10% of 30,000 is 3,000 sh = $1,500 at the mark,
    $3,300 on the game. Under the per-game cap B was scaled to 1,400
    (`game_cap_scaled`); per trade it is sized at 3,000, one rest ($1,470
    at the wire, under the per-order clip), the plan's game_room null,
    no game_cap name, the exposure still recorded -- and the plan is
    JSON without an infinity."""
    p, a, b, v, http = _game_world(monkeypatch)
    assert rules.MIRROR_NET_CAP_USD == math.inf
    st = _tick(p, v, http=http)
    assert a["target"] == 3600 and a["last_plan"]["game_room"] is None
    assert a["last_plan"]["game_exposure"] == 0.0 and "game_cap" not in a["last_plan"]
    assert b["target"] == 3000 and "game_cap" not in b["last_plan"]
    assert b["last_plan"]["game_room"] is None and b["last_plan"]["game_exposure"] == 1800.0
    assert b["last_plan"]["target_raw"] == 3000.0
    pl = _places(v)
    # E31 (FILL lane 31, 2026-09-10): the entry rests at the maker wire
    # min(buy_wire(his 0.50), ask - MAKER_TICK) = 0.50, where it joined the bid
    # at 0.49. The SIZING is this test's subject and is untouched ($1,500 at the
    # wire now, still under the per-order clip)
    assert len(pl) == 1 and pl[0][1:5] == (SLUG, 0.50, 3000, False), pl
    for k in ("game_cap_scaled", "game_cap_full", "game_unreadable"):
        assert _census(st, k) == 0, k
    json.dumps(b["last_plan"], default=str, allow_nan=False)
    json.dumps(a["last_plan"], default=str, allow_nan=False)


def test_a_market_whose_ten_percent_is_over_2500_at_the_mark_is_reached_across_more_than_one_rest(monkeypatch):
    """Galatasaray / Sporting's shape on one market: his 100,000 shares,
    10% = 10,000 sh at 0.50 = $5,000 at the mark. The per-market cap
    once scaled it to 5,000 sh; per trade the target is 10,000 and the
    per-order clip (MIRROR_CLIP_USD $2,500 at the wire) sizes the FIRST
    rest -- the rest of the target follows on later ticks, each rest at
    most $2,500.

    RE-PINNED AT E31 (FILL lane 31, 2026-09-10): the wire the clip divides is
    the maker wire min(buy_wire(his 0.50), ask - MAKER_TICK) = 0.50, not the
    bid 0.49, so the first rest is $2,500 / 0.50 = 5,000 shares where it was
    5,102. The clip's rule is the subject and is unchanged."""
    p, a, b, v, http = _game_world(monkeypatch, his_b=100000.0)
    # the shadow's own row is CAPPED at its $2,500 (5,000 sh at 0.50): the
    # live check reconstructs the raw and re-caps at this lane's cap, so
    # the two never disagree on the difference
    p.shadow.append(_shadow_row(5000, 0.10, 100000.0, capped=True))
    st = _tick(p, v, http=http)
    assert b["target"] == 10000 and b["last_plan"]["target_raw"] == 10000.0
    assert "game_cap" not in b["last_plan"] and b["last_plan"]["game_room"] is None
    pl = _places(v)
    assert len(pl) == 1 and pl[0][1] == SLUG and pl[0][2] == 0.50
    qty = pl[0][3]
    assert qty == int(2500.0 / 0.50) == 5000, "one rest, clipped per order at $2,500"
    assert qty * 0.50 <= 2500.0 and qty < 10000
    assert _census(st, "game_cap_scaled") == 0 and _census(st, "shadow_live_disagree") == 0
    assert _census(st, "shadow_check_skipped") == 0


def _decisions(p):
    return [tuple(a) for k, s, a in p.sent if "ml-order-decision" in s]


def test_a_standing_clipped_rest_stands_past_the_floor_it_is_not_replaced_by_its_own_twin(monkeypatch):
    """The review's HIGH-1. His 100,000 @0.50: the target is 10,000, the
    first rest 5,000 (the $2,500 clip at the maker wire 0.50 -- E31, FILL
    lane 31: 5,102 at the bid 0.49 before this lane). That rest standing 60 s
    (past the 45 s rest-life floor, under the TTL) at the same cent is
    KEPT: no cancel, no place, `open_order_pending`; never `replace_qty`
    for a quantity the clip can never place. Young (20 s) it is kept
    with no `add_pending` either: the rest is compared against the plan
    as the clip would size it (_act's p_cmp)."""
    p, a, b, v, http = _game_world(monkeypatch, his_b=100000.0)
    o = p.add_order(b, wire=0.50, qty=5000, placed_ts=NOW - 60)
    v.rest("oid-1", "BUY", 0.50, 5000)
    st = _tick(p, v, http=http)
    assert not _cancels(v) and not _places(v), (_cancels(v), _places(v))
    assert _decisions(p) == [] and b["last_plan"].get("replaced") is None
    assert b["last_plan"]["open_order"] == o["id"] and _census(st, "open_order_pending") == 1
    assert p.orders[o["id"]]["state"] == "open"
    p2, a2, b2, v2, http2 = _game_world(monkeypatch, his_b=100000.0)
    p2.add_order(b2, wire=0.50, qty=5000, placed_ts=NOW - 20)
    v2.rest("oid-1", "BUY", 0.50, 5000)
    _tick(p2, v2, http=http2)
    assert not _cancels(v2) and not _places(v2) and "add_pending" not in b2["last_plan"]
    src = inspect.getsource(ml._act)
    assert "rules.rest_decision(oo, p_cmp, t.now" in src and "dataclasses.replace(p, qty=clip_sh)" in src


def test_after_the_first_rest_fills_the_next_tick_places_the_remainder(monkeypatch):
    """The ledger holds the first rest's 5,102: the next tick places the
    remainder 4,898 (10,000 - 5,102), under the clip, and cancels nothing."""
    p, a, b, v, http = _game_world(monkeypatch, his_b=100000.0, b_ledger=5102)
    _tick(p, v, http=http)
    pl = _places(v)
    assert b["target"] == 10000 and len(pl) == 1 and pl[0][1] == SLUG and pl[0][3] == 4898, pl
    assert not _cancels(v)


def test_an_unreadable_sibling_under_the_unbounded_cap_is_sized_in_full_and_names_nothing(monkeypatch):
    """The review's MEDIUM-1: test_an_unreadable_exposure_is_no_room's
    world (A's avg_cost and last_plan None, B walked first). Under the
    unbounded cap there is no room to fail toward: B is sized at its
    full target (4,000 of his 40,000), the plan's null `game_exposure`
    records the unreadable sum, and `game_unreadable` is NOT counted
    (it is a hold's name; the hourly preset reads it as one)."""
    p, a, b, v, http = _game_world(monkeypatch, his_b=40000.0, b_ledger=200, a_ledger=100, b_first=True)
    a["avg_cost"] = None
    a["last_plan"] = None
    assert b["id"] < a["id"]
    st = _tick(p, v, http=http)
    assert b["last_plan"]["game_exposure"] is None and b["last_plan"]["game_room"] is None
    assert b["target"] == 4000 and "game_cap" not in b["last_plan"]
    assert _census(st, "game_unreadable") == 0 and _census(st, "game_cap_full") == 0
    pl = _places(v)
    assert len(pl) == 1 and pl[0][1] == SLUG and pl[0][3] == 3800, pl
    assert "math.isfinite(room)" in inspect.getsource(ml._tick_book).split('_mirror_stop("game_unreadable"')[0][-600:]


def test_a_lowered_cap_from_the_environment_re_arms_the_per_game_room(monkeypatch):
    """The environment may still lower the cap; at $2,500 the E1 room
    is byte for byte what it was: B scaled to 1,400, game_cap_scaled."""
    p, a, b, v, http = _game_world(monkeypatch)
    monkeypatch.setattr(rules, "MIRROR_NET_CAP_USD", 2500.0)
    st = _tick(p, v, http=http)
    assert a["last_plan"]["game_room"] == 2500.0
    assert b["target"] == 1400 and b["last_plan"]["game_cap"] == "game_cap_scaled"
    assert b["last_plan"]["game_room"] == 700.0 and _census(st, "game_cap_scaled") == 1


def test_the_docs_name_the_order():
    text = DOCS.read_text()
    assert re.search(r"^## \d+\. The cap is per trade \(2026-09-09, owner order\)", text, re.M)
    assert "per trade basis not on a per match or market basis" in text
    assert "MIRROR_NET_CAP_USD" in text and "MIRROR_CLIP_USD" in text


def test_the_suite_fixture_is_the_lowered_reading_not_the_default():
    """The autouse fixture in conftest sets the module attribute for the
    tests written under the $2,500 cap and this module opts out: the
    real default is unbounded here."""
    src = (pathlib.Path(__file__).resolve().parent / "conftest.py").read_text()
    assert "NET_CAP_UNBOUNDED" in src and 'monkeypatch.setattr(rules, "MIRROR_NET_CAP_USD", 2500.0)' in src
    assert rules.MIRROR_NET_CAP_USD == math.inf
    assert "game_room" in inspect.getsource(ml._tick_book) and "math.isfinite(room)" in inspect.getsource(ml._tick_book)
