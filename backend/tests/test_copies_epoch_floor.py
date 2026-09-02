"""The copies record floors the engine's Kalshi block on the display
window (owner order 2026-09-02; the probe's EPOCHCHECK caught the
record serving since=2026-09-01 with a first day of 2026-08-05)."""
from sportsassets.api import copies_record as cr


def _d(day, pnl, settled=1, wins=1, losses=0, staked=10.0):
    return {"day": day, "pnl": pnl, "settled": settled, "wins": wins,
            "losses": losses, "staked": staked}


def test_days_before_the_window_are_cut_and_the_total_rebuilt():
    kexp = {"daily": [_d("2026-08-05", 5.0), _d("2026-08-31", 3.0),
                      _d("2026-09-01", -2.0, wins=0, losses=1), _d("2026-09-02", 4.0)],
            "total": {"settled": 4, "wins": 3, "losses": 1, "pnl": 10.0, "staked": 40.0},
            "by_whale": {"rn1": {"pnl": 10.0}},
            "open": {"count": 2, "stake": 20.0}}
    out = cr.floor_export(kexp, "2026-09-01")
    assert [d["day"] for d in out["daily"]] == ["2026-09-01", "2026-09-02"]
    assert out["total"]["pnl"] == 2.0 and out["total"]["settled"] == 2
    assert out["total"]["wins"] == 1 and out["total"]["losses"] == 1
    assert out["total"]["staked"] == 20.0 and out["total"]["roi"] == 0.1
    assert out["by_whale"] == {}                 # lifetime split cannot be windowed
    assert out["open"] == {"count": 2, "stake": 20.0}
    assert out["floored_to"] == "2026-09-01"
    assert kexp["total"]["pnl"] == 10.0          # input untouched


def test_a_block_already_inside_the_window_passes_through_unchanged():
    kexp = {"daily": [_d("2026-09-01", 1.0)], "total": {"pnl": 1.0},
            "by_whale": {"rn1": {"pnl": 1.0}}}
    out = cr.floor_export(kexp, "2026-09-01")
    assert out["total"] == {"pnl": 1.0} and out["by_whale"] == {"rn1": {"pnl": 1.0}}
    assert "floored_to" not in out


def test_none_and_junk_pass_through():
    assert cr.floor_export(None, "2026-09-01") is None
    assert cr.floor_export({"daily": "x", "total": {}}, "2026-09-01")["daily"] == []


def test_build_floors_the_kalshi_block_before_merging():
    import inspect

    src = inspect.getsource(cr.build)
    assert "floor_export(await _kalshi_copies_export(pool), since_day)" in src
