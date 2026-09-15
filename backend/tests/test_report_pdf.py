"""Branded PDF reports — builders are pure and never fail on empty."""

from __future__ import annotations

from sportsassets.api.report_pdf import kalshi_manual_pdf, master_report_pdf


def _rep():
    return {
        "period": "monthly",
        "rows": [{"whale": "RN1", "sport": "tennis", "category": "Moneyline",
                  "bucket": "2026-08", "n": 12, "wins": 7, "losses": 5,
                  "staked": 600.0, "pnl": 42.5, "roi": 0.0708,
                  "lat_p50_s": 2.5}],
        "by_whale": [{"whale": "RN1", "n": 12, "wins": 7, "losses": 5,
                      "staked": 600.0, "pnl": 42.5, "roi": 0.0708,
                      "lat_p50_s": 2.5}],
        "latency": {"n": 12, "avg_s": 3.1, "p50_s": 2.5},
    }


def _ledger():
    return [{"day": f"2026-08-{d:02d}", "status": "settled",
             "pnl": (d % 3 - 1) * 20.0} for d in range(1, 20)]


def test_master_report_is_a_real_pdf():
    pdf = master_report_pdf(_rep(), _ledger(), "window epoch → today")
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000


def test_master_report_survives_an_empty_ledger():
    pdf = master_report_pdf({"period": "monthly", "rows": [],
                             "by_whale": [], "latency": {}}, [], "empty")
    assert pdf.startswith(b"%PDF")


def test_kalshi_manual_report_builds_with_and_without_orders():
    orders = [{"placed_at": "2026-08-28T21:00:00Z",
               "market_title": "Yankees vs Red Sox",
               "us_market_slug": "kxmlb-nyy-bos", "side": "BUY",
               "limit_price": 0.56, "fill_price": 0.55,
               "filled_shares": 100, "filled_usd": 55.0,
               "status": "filled", "pnl": None}]
    assert kalshi_manual_pdf(orders).startswith(b"%PDF")
    assert kalshi_manual_pdf([]).startswith(b"%PDF")
