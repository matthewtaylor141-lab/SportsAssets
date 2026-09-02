"""One trade counted in full despite the $100 single-trade cap.

Owner order 2026-09-01 (evening): the owner placed a manual Polymarket
ticket on the same market as the 0x076daa87 copy of atp-matbel-zsopir;
the venue settled both legs onto the copy row (+$966 on a $93.72 stake)
and the cap moved the whole thing to residual, so the whale's P&L never
saw it. For THIS trade the manual leg counts in the whale's P&L.

The cap is applied in four places -- once in Python, three times in
SQL. These tests pin that every one of them consults the same list, so
the override cannot hold on one surface and not another.
"""
import inspect

from sportsassets.api import app as app_mod
from sportsassets.api import track_record as tr


def test_the_named_trade_is_exempt_and_nothing_else_is():
    assert tr.pnl_cap_exempt("aec-atp-matbel-zsopir-2026-08-30")
    assert tr.pnl_cap_exempt("ATC-ATP-MATBEL-ZSOPIR-2026-08-30-SET1")
    assert not tr.pnl_cap_exempt("aec-atp-matbel-other-2026-08-30")
    assert not tr.pnl_cap_exempt("")
    assert not tr.pnl_cap_exempt(None)


def test_the_sql_patterns_are_the_same_list():
    assert tr.pnl_cap_exempt_patterns() == ["%atp-matbel-zsopir-2026-08-30%"]


def test_the_python_cap_consults_the_exemption():
    src = inspect.getsource(tr.build)
    assert "pnl_cap_exempt(slug)" in src
    # every call site passes the slug, so no caller can cap blind
    assert "_pnl_capped(settled" not in src and "_pnl_capped(True" not in src


def test_every_sql_cap_consults_the_exemption():
    for fn in (app_mod._category_breakdown, app_mod.api_admin_order_audit,
               app_mod.api_breakdown_day_detail, app_mod.api_edge_decay):
        src = inspect.getsource(fn)
        assert "pnl_cap_exempt_patterns()" in src, fn.__name__
        assert "LIKE ANY(" in src, fn.__name__


# ------------------------------------------------ the record, driven

TS_AUG1 = 1785542400.0
TS = TS_AUG1 + 86_400 + 16 * 3600


def _trade(slug, ts, qty, price):
    return {"type": "ACTIVITY_TYPE_TRADE",
            "trade": {"marketSlug": slug, "qty": qty,
                      "price": {"value": price}, "createTime": ts * 1000}}


def _resolution(slug, ts):
    return {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION", "timestamp": ts * 1000,
            "positionResolution": {"marketSlug": slug}}


def _pos(qty, cost, value, realized=0.0, expired=False):
    return {"netPosition": qty, "cost": cost, "cashValue": value,
            "realized": realized, "expired": expired,
            "marketMetadata": {"title": "T", "outcome": "Yes"}}


def _record(slug):
    positions = {slug: _pos(0, 93.72, 0.0, realized=966.0, expired=True)}
    acts = [_trade(slug, TS, 1041, 0.09), _resolution(slug, TS + 3600)]
    return tr.build(positions, acts, TS_AUG1, max_abs_pnl=100.0,
                    copy_slugs={slug})


def test_the_exempt_trade_is_in_the_record_and_a_twin_is_capped():
    """ROUND FOUR: the Python cap was pinned by grep only. Drive the
    builder: the named trade lands in the record; an identical trade
    on another market is excluded over the cap, as before."""
    out = _record("aec-atp-matbel-zsopir-2026-08-30")
    assert [r["market_slug"] for r in out["trades"]] == ["aec-atp-matbel-zsopir-2026-08-30"]
    assert out["trades"][0]["pnl"] == 966.0 and out["trades"][0]["cohort"] == "record"
    assert out["excluded_over_pnl"]["count"] == 0
    assert out["summary"]["net_pnl"] == 966.0
    twin = _record("aec-atp-someone-else-2026-08-30")
    assert twin["trades"] == []
    assert twin["excluded_over_pnl"]["count"] == 1
    assert twin["excluded_over_pnl"]["net_pnl"] == 966.0
