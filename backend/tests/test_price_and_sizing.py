"""Owner orders 2026-08-12 (mapping recovery round): same-or-better
price rule, inverse volume<->size scaling, and the deterministic
spread/total resolver's pure pieces."""

import asyncio

import pytest

from sportsassets import live_executor, pmus
from sportsassets.live_executor import volume_normalized_clip
from sportsassets.pmus import _feed_derivative


# ── inverse volume<->size scaling ───────────────────────────────────
class _CountPool:
    def __init__(self, n):
        self.n = n

    async def fetchval(self, sql, *a):
        assert "count(*)" in sql
        return self.n


def _clip(whale, n, slug=None):
    return asyncio.run(volume_normalized_clip(_CountPool(n), whale, slug))


def test_clip_is_base_at_or_under_baseline():
    assert _clip("rn1", 0) == 150.00
    assert _clip("rn1", 40) == 150.00           # exactly baseline
    assert _clip("swisstony", 30) == 200.00


def test_ten_x_fills_means_one_tenth_size():
    assert _clip("rn1", 400) == pytest.approx(15.00)      # 150 * 40/400
    assert _clip("swisstony", 300) == pytest.approx(20.00)  # 200 * 30/300


def test_clip_floors_at_five_dollars_and_never_scales_up():
    assert _clip("rn1", 100000) == 5.00
    assert _clip("rn1", 41) < 150.00


def test_sport_override_is_the_scaling_base():
    # swisstony soccer base is $100 (owner order): 10x -> $10.
    assert _clip("swisstony", 300, "epl-ars-che-2026-08-15") == \
        pytest.approx(10.00)


def test_unreadable_count_degrades_to_base_clip():
    class _Boom:
        async def fetchval(self, sql, *a):
            raise RuntimeError("db down")

    assert asyncio.run(volume_normalized_clip(_Boom(), "rn1")) == 150.00


# ── same-or-better limit (float-floor regression) ───────────────────
def test_pm_limit_is_his_price_floored_to_cent():
    import math

    for his, want in ((0.29, 0.29), (0.30, 0.30), (0.55, 0.55),
                      (0.985, 0.98), (0.997, 0.99)):
        got = min(math.floor(round(his * 100, 6)) / 100.0, 0.99)
        assert got == want, f"his {his}"
        assert got <= his + 1e-9, "never above his price"


# ── feed derivative parsing (pure) ──────────────────────────────────
def test_feed_totals_parse():
    fd = _feed_derivative("mlb-nyy-bos-2026-07-22-o8pt5")
    assert fd == {"base": "mlb-nyy-bos-2026-07-22", "kind": "total",
                  "line": "8pt5", "side": "o", "team": None}
    fd2 = _feed_derivative("epl-ars-che-2026-08-15-u2pt5")
    assert fd2["side"] == "u" and fd2["line"] == "2pt5"


def test_feed_spreads_parse():
    fd = _feed_derivative("mlb-kc-laa-2026-08-14-pos-1pt5")
    assert fd == {"base": "mlb-kc-laa-2026-08-14", "kind": "spread",
                  "line": "1pt5", "side": "pos", "team": None}
    fd2 = _feed_derivative("epl-ars-che-2026-08-15-ars-1pt5")
    assert fd2["team"] == "ars" and fd2["side"] is None
    fd3 = _feed_derivative("epl-ars-che-2026-08-15-che-neg-2pt5")
    assert fd3 == {"base": "epl-ars-che-2026-08-15", "kind": "spread",
                   "line": "2pt5", "side": "neg", "team": "che"}


def test_feed_non_derivatives_refuse():
    assert _feed_derivative("mlb-nyy-bos-2026-07-22") is None       # ML
    assert _feed_derivative("mlb-nyy-bos-2026-07-22-ftts") is None  # btts
    assert _feed_derivative("no-date-here") is None
    assert _feed_derivative("") is None
    assert _feed_derivative("mlb-nyy-bos-2026-07-22-es-2-0") is None


# ── derivative resolver against a faked venue ───────────────────────
class _Markets:
    def __init__(self, table):
        self.table = table

    def retrieve_by_slug(self, slug):
        if slug not in self.table:
            raise KeyError(slug)
        return {"market": self.table[slug]}


class _Client:
    def __init__(self, table):
        self.markets = _Markets(table)


def test_total_resolves_to_the_right_side(monkeypatch):
    table = {"tsc-mlb-nyy-bos-2026-07-22-8pt5": {
        "slug": "tsc-mlb-nyy-bos-2026-07-22-8pt5", "closed": False,
        "question": "Yankees vs Red Sox: O/U 8.5",
        "marketSides": [
            {"identifier": "tsc-mlb-nyy-bos-2026-07-22-8pt5-over",
             "description": "Over"},
            {"identifier": "tsc-mlb-nyy-bos-2026-07-22-8pt5-under",
             "description": "Under"},
        ]}}
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(table))
    r = pmus.resolve_derivative_exact("mlb-nyy-bos-2026-07-22-o8pt5",
                                      "Over 8.5")
    assert r and r["market_slug"].endswith("-over")
    assert r["matched_by"] == "derivative_exact"
    r2 = pmus.resolve_derivative_exact("mlb-nyy-bos-2026-07-22-u8pt5",
                                       "Under 8.5")
    assert r2 and r2["market_slug"].endswith("-under")


def test_spread_requires_team_corroboration(monkeypatch):
    table = {"asc-mlb-kc-laa-2026-08-14-pos-1pt5": {
        "slug": "asc-mlb-kc-laa-2026-08-14-pos-1pt5", "closed": False,
        "question": "Spread: Kansas City Royals (+1.5)",
        "marketSides": []}}
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client(table))
    ok = pmus.resolve_derivative_exact("mlb-kc-laa-2026-08-14-pos-1pt5",
                                       "Kansas City Royals")
    assert ok and ok["market_slug"] == "asc-mlb-kc-laa-2026-08-14-pos-1pt5"
    # Wrong team in the outcome: the parent text does not corroborate.
    bad = pmus.resolve_derivative_exact("mlb-kc-laa-2026-08-14-pos-1pt5",
                                        "Texas Rangers")
    assert bad is None


def test_unlisted_candidate_falls_through(monkeypatch):
    monkeypatch.setattr(pmus, "_get_client", lambda: _Client({}))
    assert pmus.resolve_derivative_exact(
        "mlb-nyy-bos-2026-07-22-o8pt5", "Over") is None
