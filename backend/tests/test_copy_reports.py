"""Management-report engine pins (owner order 2026-08-28)."""

from __future__ import annotations

from sportsassets.api import copy_reports as cr


def _row(whale="rn1", day="2026-08-27", slug="aec-atp-a-b-2026-08-27",
         status="settled", stake=100.0, pnl=10.0, lat=1.5, **kw):
    return {"whale": whale, "day": day, "slug": slug, "status": status,
            "stake": stake, "pnl": pnl, "latency_s": lat,
            "side": "BUY", "venue": "polymarket-us", "id": 1,
            "his_price": 0.5, "fill_price": 0.5, "shares": 200.0,
            "whale_ts": None, "detected_at": None, "placed_at": None,
            "settled_at": None, "detect_lag_s": 0.5, **kw}


def test_bucket_keys():
    assert cr._bucket("2026-08-27", "daily") == "2026-08-27"
    assert cr._bucket("2026-08-27", "weekly") == "2026-08-24"  # Monday
    assert cr._bucket("2026-08-27", "monthly") == "2026-08"
    assert cr._bucket("2026-08-27", "all") == "all"
    assert cr._bucket("", "daily") == "undated"
    assert cr._bucket("junk", "weekly") == "undated"


def test_report_aggregates_whale_sport_category_and_latency():
    rows = [
        _row(pnl=10.0, lat=1.0),
        _row(pnl=-5.0, lat=3.0),
        _row(slug="tsc-mlb-a-b-2026-08-27-8pt5", pnl=7.0, lat=2.0),
        _row(whale="swisstony", slug="asc-nba-x-y-2026-08-27-3pt5",
             pnl=4.0, lat=None),                 # sweep lane: no latency
        _row(status="filled", pnl=None, lat=0.5),  # open: never counted
    ]
    rep = cr.report(rows, period="all")
    assert rep["period"] == "all"
    # report rows are management-facing: DISPLAY names, not ledger keys
    tennis = next(r for r in rep["rows"]
                  if r["whale"] == "RN1" and r["category"] == "Moneyline")
    assert tennis["n"] == 2 and tennis["wins"] == 1 and tennis["losses"] == 1
    assert tennis["pnl"] == 5.0 and tennis["staked"] == 200.0
    assert tennis["lat_avg_s"] == 2.0 and tennis["lat_p50_s"] == 2.0
    total_rn1 = next(w for w in rep["by_whale"] if w["whale"] == "RN1")
    assert total_rn1["n"] == 3 and total_rn1["pnl"] == 12.0
    st = next(w for w in rep["by_whale"] if w["whale"] == "SwissTony")
    assert st["lat_avg_s"] is None and st["lat_n"] == 0, \
        "NULL reaction (sweep lane) must not fabricate a latency"
    assert rep["latency"]["n"] == 3


def test_weekly_buckets_split_across_weeks():
    rows = [_row(day="2026-08-21", pnl=1.0),      # week of 08-17
            _row(day="2026-08-27", pnl=2.0)]      # week of 08-24
    rep = cr.report(rows, period="weekly")
    buckets = {r["bucket"] for r in rep["rows"]}
    assert buckets == {"2026-08-17", "2026-08-24"}


def test_ledger_rows_window_and_shape():
    raw = [dict(_row(day="2026-08-20"), latency_s=2.345),
           dict(_row(day="2026-08-27"), latency_s=1.111)]
    rows = cr.ledger_rows(raw, since="2026-08-25")
    assert len(rows) == 1
    r = rows[0]
    assert r["whale"] == "RN1", "ledger rows are display-named"
    assert r["latency_s"] == 1.11
    assert r["sport"] == "Tennis" and r["category"] == "Moneyline"


def test_csv_roundtrip():
    raw = [_row()]
    rows = cr.ledger_rows(raw)
    text = cr.to_csv(rows, cr.LEDGER_CSV_COLS)
    lines = text.strip().splitlines()
    assert lines[0].startswith("id,whale,day,slug,sport,category")
    assert "RN1" in lines[1] and "1.5" in lines[1]
