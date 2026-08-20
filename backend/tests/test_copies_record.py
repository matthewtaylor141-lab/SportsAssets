"""Copies cohort scorecard (owner order 2026-08-20: show that the
system is profitable): uncapped per-whale record from the order audit,
copies ONLY — sleeves that are not whale copies must never inflate it,
and unknown usernames are excluded, not guessed in."""

from sportsassets.api.copies_record import scorecard


def _row(whale, day, pnl, stake=100.0):
    return {"whale": whale, "day": day, "pnl": pnl, "filled_usd": stake}


def test_totals_and_per_whale_split():
    rows = [
        _row("rn1", "2026-08-18", 40.0),
        _row("rn1", "2026-08-18", -25.0),
        _row("swisstony", "2026-08-19", 60.0, 200.0),
        _row("0x2c335066fe58fe9237c3d3dc7b275c2a034a0563-1759935795465",
             "2026-08-19", 10.0),
    ]
    out = scorecard(rows)
    assert out["total"]["settled"] == 4
    assert out["total"]["pnl"] == 85.0
    assert out["total"]["staked"] == 500.0
    assert out["total"]["roi"] == 0.17
    assert out["total"]["wins"] == 3 and out["total"]["losses"] == 1
    assert out["by_whale"][0]["whale"] == "SwissTony"   # biggest pnl first
    assert out["by_whale"][0]["roi"] == 0.3
    names = {w["whale"] for w in out["by_whale"]}
    assert "0x2c33" in names                            # display name


def test_non_copy_sleeves_never_count():
    rows = [
        _row("rn1", "2026-08-18", 40.0),
        _row("underdog", "2026-08-18", 500.0),
        _row("arb", "2026-08-18", 500.0),
        _row("manual", "2026-08-18", 500.0),
        _row("", "2026-08-18", 500.0),
        _row("somebody-new", "2026-08-18", 500.0),
    ]
    out = scorecard(rows)
    assert out["total"]["settled"] == 1
    assert out["total"]["pnl"] == 40.0


def test_daily_series_newest_first_and_no_undated():
    rows = [_row("rn1", "2026-08-17", 10.0),
            _row("rn1", "2026-08-19", -5.0),
            _row("rn1", None, 99.0)]
    out = scorecard(rows)
    assert [d["day"] for d in out["daily"]] == ["2026-08-19", "2026-08-17"]
    # The undated row still counts in totals (it settled; the venue just
    # gave no usable stamp) — it only stays out of the calendar.
    assert out["total"]["settled"] == 3
