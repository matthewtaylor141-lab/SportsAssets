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


def test_per_sport_and_per_whale_daily_splits():
    rows = [
        dict(_row("rn1", "2026-08-18", 40.0), sport="baseball"),
        dict(_row("rn1", "2026-08-18", -25.0, 50.0), sport="tennis"),
        dict(_row("rn1", "2026-08-19", 10.0), sport="baseball"),
        dict(_row("swisstony", "2026-08-18", 60.0, 200.0), sport="soccer"),
    ]
    out = scorecard(rows)
    ws = {(w["whale"], w["sport"]): w for w in out["by_whale_sport"]}
    assert ws[("RN1", "baseball")]["pnl"] == 50.0
    assert ws[("RN1", "baseball")]["settled"] == 2
    assert ws[("RN1", "baseball")]["roi"] == 0.25
    assert ws[("RN1", "tennis")]["losses"] == 1
    assert ws[("SwissTony", "soccer")]["staked"] == 200.0
    dw = {(d["day"], d["whale"]): d for d in out["daily_by_whale"]}
    assert dw[("2026-08-18", "RN1")]["pnl"] == 15.0
    assert dw[("2026-08-18", "RN1")]["staked"] == 150.0
    assert dw[("2026-08-19", "RN1")]["settled"] == 1


def test_software_cohort_is_the_complement():
    from sportsassets.api.copies_record import software_scorecard

    rows = [
        _row("rn1", "2026-08-18", 40.0),          # copy: excluded
        _row("underdog", "2026-08-18", 5.0),      # named sleeve: excluded
        _row("arb", "2026-08-18", 5.0),
        _row("manual", "2026-08-18", 5.0),
        _row("", "2026-08-18", -300.0),           # unattributed: counted
        _row("", "2026-08-19", -100.0, 200.0),
        _row("retired-engine", "2026-08-19", 50.0),
    ]
    out = software_scorecard(rows)
    assert out["total"]["settled"] == 3
    assert out["total"]["pnl"] == -350.0
    days = {d["day"]: d for d in out["daily"]}
    assert days["2026-08-18"]["pnl"] == -300.0
    assert days["2026-08-19"]["pnl"] == -50.0
    assert days["2026-08-19"]["staked"] == 300.0
