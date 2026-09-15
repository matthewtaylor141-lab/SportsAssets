"""Partial cash-outs surface on the copy ledger (owner report 2026-09-02:
sells did not show on the ledger and the P&L was wrong)."""
import inspect

from sportsassets.api import copies_record as cr


def _p(whale="rn1", pnl=3.25, filled_usd=48.0, remaining=40.0, orig=100.0, slug="s"):
    return {"whale": whale, "pnl": pnl, "filled_usd": filled_usd,
            "remaining_shares": remaining, "orig_shares": orig,
            "us_market_slug": slug, "venue": "polymarket-us", "latency_s": 0.8}


def test_partials_are_their_own_lines_and_never_settled_rows():
    rows = [_p(), _p(pnl=-1.1, slug="t"), _p(pnl=0.0, slug="zero"),
            _p(whale="not-a-copy-whale", slug="x")]
    out = cr.partials_list(rows)
    assert [r["slug"] for r in out] == ["s", "t"]
    assert out[0]["status"] == "partial_cashout" and out[0]["day"] is None
    assert out[0]["pnl"] == 3.25 and out[0]["remaining_shares"] == 40.0
    assert out[0]["orig_shares"] == 100.0 and out[0]["latency_s"] == 0.8
    # the settled scoreline ignores them: today_stats keys on day == today
    assert cr.today_stats(out, "2026-09-02") == {"pnl": 0.0, "settled": 0, "wins": 0, "losses": 0}


def test_build_lists_partials_ahead_of_the_settled_ledger():
    src = inspect.getsource(cr.build)
    assert "lo.status = 'filled' AND COALESCE(lo.pnl, 0) <> 0" in src
    assert 'out["trades"] = partials + out["trades"]' in src
    assert '"realized": round(sum(p["pnl"] for p in partials), 2)' in src
