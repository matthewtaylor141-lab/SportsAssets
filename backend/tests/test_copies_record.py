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


def test_daily_covers_the_full_window():
    """Owner order 2026-08-22: the 31-day truncation silently cut the
    calendar once the window outgrew a month."""
    rows = [_row("rn1", f"2026-06-{d:02d}", 1.0) for d in range(1, 31)] \
        + [_row("rn1", f"2026-07-{d:02d}", 1.0) for d in range(1, 11)]
    out = scorecard(rows)
    assert len(out["daily"]) == 40
    assert out["daily"][0]["day"] == "2026-07-10"    # newest first
    assert out["daily"][-1]["day"] == "2026-06-01"


def test_trades_list_copy_rows_only_display_named():
    from sportsassets.api.copies_record import trades_list

    rows = [
        dict(_row("swisstony", "2026-08-19", 60.0, 200.0),
             us_market_slug="atc-epl-a-b-2026-08-19-a", status="settled"),
        dict(_row("manual", "2026-08-19", 500.0),
             us_market_slug="x", status="settled"),
        dict(_row("rn1", "2026-08-18", -25.0, 75.0),
             us_market_slug="tsc-mlb-c-d-2026-08-18-o8pt5",
             status="cashed_out"),
    ]
    out = trades_list(rows)
    assert out == [
        {"day": "2026-08-19", "whale": "SwissTony",
         "slug": "atc-epl-a-b-2026-08-19-a", "stake": 200.0,
         "pnl": 60.0, "status": "settled"},
        {"day": "2026-08-18", "whale": "RN1",
         "slug": "tsc-mlb-c-d-2026-08-18-o8pt5", "stake": 75.0,
         "pnl": -25.0, "status": "cashed_out"},
    ]


def test_trades_list_caps_at_limit_preserving_order():
    from sportsassets.api.copies_record import trades_list

    rows = [dict(_row("rn1", "2026-08-18", float(i)), status="settled")
            for i in range(450)]
    out = trades_list(rows)
    assert len(out) == 400
    assert out[0]["pnl"] == 0.0        # newest-first input order kept


def test_today_stats_scoreline():
    from sportsassets.api.copies_record import today_stats

    rows = [
        _row("rn1", "2026-08-22", 150.0),       # uncapped: counts whole
        _row("rn1", "2026-08-22", -10.0),
        _row("swisstony", "2026-08-22", 0.0),   # push: neither W nor L
        _row("rn1", "2026-08-21", 99.0),        # yesterday: excluded
        _row("manual", "2026-08-22", 500.0),    # not a copy: excluded
    ]
    out = today_stats(rows, "2026-08-22")
    assert out == {"pnl": 140.0, "settled": 3, "wins": 1, "losses": 1}


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


# ── Kalshi copy-sleeve merge (owner order 2026-08-22: homepage must
# include Kalshi copy volume + P&L; PM ledger alone showed ~half) ──


def test_merge_totals_adds_and_recomputes_ratios():
    from sportsassets.api.copies_record import merge_totals

    pm = {"settled": 100, "wins": 60, "losses": 40,
          "pnl": 1000.0, "staked": 5000.0, "roi": 0.2, "win_rate": 0.6}
    k = {"settled": 80, "wins": 35, "losses": 45,
         "pnl": -120.5, "staked": 4200.0}
    m = merge_totals(pm, k)
    assert m["settled"] == 180 and m["wins"] == 95 and m["losses"] == 85
    assert m["pnl"] == 879.5 and m["staked"] == 9200.0
    assert m["roi"] == round(879.5 / 9200.0, 4)
    assert m["win_rate"] == round(95 / 180, 4)


def test_merge_daily_unions_days():
    from sportsassets.api.copies_record import merge_daily

    pm = [{"day": "2026-08-21", "settled": 10, "wins": 6, "losses": 4,
           "pnl": 100.0, "staked": 2000.0}]
    k = [{"day": "2026-08-21", "settled": 5, "wins": 2, "losses": 3,
          "pnl": -40.0, "staked": 900.0},
         {"day": "2026-08-20", "settled": 3, "wins": 3, "losses": 0,
          "pnl": 60.0, "staked": 500.0}]
    out = merge_daily(pm, k)
    assert [d["day"] for d in out] == ["2026-08-21", "2026-08-20"]
    assert out[0]["settled"] == 15 and out[0]["pnl"] == 60.0
    assert out[1]["staked"] == 500.0


def test_merge_by_whale_folds_copy_whales_only():
    from sportsassets.api.copies_record import merge_by_whale

    pm = [{"whale": "RN1", "settled": 10, "wins": 6, "losses": 4,
           "pnl": 500.0, "staked": 2000.0, "roi": 0.25}]
    k = {"rn1": {"settled": 4, "wins": 1, "losses": 3,
                 "pnl": -80.0, "staked": 700.0},
         "0xf705fa04": {"settled": 99, "wins": 99, "losses": 0,
                        "pnl": 9999.0, "staked": 9999.0}}
    out = merge_by_whale(pm, k)
    rn1 = next(w for w in out if w["whale"] == "RN1")
    assert rn1["settled"] == 14 and rn1["pnl"] == 420.0
    assert not any("f705" in str(w["whale"]).lower() for w in out), \
        "crypto whales are not copies and must never merge in"
