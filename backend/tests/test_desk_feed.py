"""Desk v8 feed (owner contract 2026-08-22): venue-style market cards
from the SAME venue listings desk-games uses — card shape {id, venue,
title, league, volume_usd|null, close_time|null, outcomes:[{label, id,
price}], history_id}, volume normalized to dollars per venue (never
invented — null when the venue doesn't say), sorted volume desc
nulls-last, capped at 60, league filter passed through (everything
included)."""

import pytest

from sportsassets import pmus
from sportsassets.api import app as app_mod

CARD_KEYS = {"id", "venue", "title", "league", "volume_usd",
             "close_time", "outcomes", "history_id"}


# ── Kalshi volume normalization (_kvol: the _kcents discipline, no
#    0-1 clamp) ───────────────────────────────────────────────────────


def test_kvol_dollars_twin_wins_over_cents():
    assert app_mod._kvol({"volume": 5500,
                          "volume_dollars": "123.45"}) == 123.45


def test_kvol_plain_field_is_cents():
    assert app_mod._kvol({"volume": 5500}) == 55.0


def test_kvol_falls_back_to_24h_then_null():
    assert app_mod._kvol({"volume_24h": 200}) == 2.0
    assert app_mod._kvol({"volume_24h_dollars": "9.5"}) == 9.5
    assert app_mod._kvol({}) is None
    assert app_mod._kvol({"volume": "junk",
                          "volume_dollars": ""}) is None


def test_kalshi_shape_carries_volume_usd():
    row = app_mod._kalshi_shape(
        {"ticker": "KXMLBGAME-26AUG22NYYBOS-NYY", "title": "NYY at BOS",
         "yes_ask": 45, "volume": 1000}, "KXMLBGAME")
    assert row["volume_usd"] == 10.0


# ── PM volume probing (defensive spellings, total preferred) ─────────


def test_pm_event_volume_probes_spellings():
    assert pmus._ev_volume_usd({"volume": 12.5, "liquidity": 999}) == 12.5
    assert pmus._ev_volume_usd({"volumeNum": "77"}) == 77.0
    assert pmus._ev_volume_usd({"volume24hr": 5}) == 5.0
    assert pmus._ev_volume_usd({"liquidity": 3.2}) == 3.2
    assert pmus._ev_volume_usd({"volume": "n/a",
                                "volume24hr": 4}) == 4.0
    assert pmus._ev_volume_usd({}) is None


# ── Feed: Polymarket path ────────────────────────────────────────────


def _pm_event(slug, league="mlb", volume=None, close=None, n_ml=2):
    return {
        "slug": slug, "title": slug.upper(), "league": league,
        "start": None, "volume_usd": volume, "close_time": close,
        "markets": [
            {"us_slug": f"atc-{slug}-side{i}", "kind": "atc",
             "label": f"Game — Side{i}", "price": 0.4 + i / 10}
            for i in range(n_ml)],
    }


def _wire_pm(monkeypatch, events):
    monkeypatch.setattr(pmus, "list_desk_events", lambda: events)


async def test_pm_card_shape_and_history_id(monkeypatch):
    _wire_pm(monkeypatch, [
        _pm_event("mlb-nyy-bos-2026-08-22", volume=1234.5,
                  close="2026-08-22T23:00:00Z")])
    out = await app_mod.api_desk_feed(venue="polymarket", league="all")
    assert set(out) == {"cards", "counts"}
    (c,) = out["cards"]
    assert set(c) == CARD_KEYS
    assert c["venue"] == "polymarket" and c["league"] == "mlb"
    assert c["volume_usd"] == 1234.5
    assert c["close_time"] == "2026-08-22T23:00:00Z"
    o = c["outcomes"][0]
    assert set(o) == {"label", "id", "price"}
    assert o["id"] == "atc-mlb-nyy-bos-2026-08-22-side0"
    assert o["label"] == "Side0"          # venue label, side part only
    assert c["history_id"] == o["id"]     # charts without a 2nd lookup
    assert out["counts"]["mlb"] == 1 and out["counts"]["all"] == 1


async def test_pm_volume_desc_nulls_last(monkeypatch):
    _wire_pm(monkeypatch, [
        _pm_event("mlb-low", volume=10.0),
        _pm_event("mlb-none", volume=None),
        _pm_event("mlb-high", volume=99.0),
    ])
    out = await app_mod.api_desk_feed(venue="polymarket", league="all")
    assert [c["id"] for c in out["cards"]] == [
        "mlb-high", "mlb-low", "mlb-none"]


async def test_pm_cap_60_counts_uncapped(monkeypatch):
    _wire_pm(monkeypatch, [
        _pm_event(f"mlb-game-{i:03d}", volume=float(i))
        for i in range(70)])
    out = await app_mod.api_desk_feed(venue="polymarket", league="all")
    assert len(out["cards"]) == 60
    assert out["counts"]["all"] == 70
    # cap keeps the TOP of the volume sort, not the first 60 seen
    assert out["cards"][0]["volume_usd"] == 69.0
    assert all(c["volume_usd"] >= 10.0 for c in out["cards"])


async def test_pm_league_filter_and_everything(monkeypatch):
    _wire_pm(monkeypatch, [
        _pm_event("mlb-a", league="mlb", volume=1.0),
        _pm_event("nba-b", league="nba", volume=2.0),
    ])
    only = await app_mod.api_desk_feed(venue="polymarket", league="mlb")
    assert [c["id"] for c in only["cards"]] == ["mlb-a"]
    # counts still describe the whole board (the venue category rail)
    assert only["counts"] == {"mlb": 1, "nba": 1,
                              "all": 2, "everything": 2}
    every = await app_mod.api_desk_feed(venue="polymarket",
                                        league="everything")
    assert len(every["cards"]) == 2


async def test_pm_venue_error_degrades_to_empty_feed(monkeypatch):
    def boom():
        raise RuntimeError("venue down")

    monkeypatch.setattr(pmus, "list_desk_events", boom)
    out = await app_mod.api_desk_feed(venue="polymarket", league="all")
    assert out["cards"] == [] and out["counts"]["all"] == 0


# ── Feed: Kalshi sports path ─────────────────────────────────────────


def _kraw(ticker, title, sub, ask_cents, volume=None, close=None):
    m = {"ticker": ticker, "title": title, "yes_sub_title": sub,
         "yes_ask": ask_cents, "close_time": close}
    if volume is not None:
        m["volume"] = volume
    return m


def _wire_kalshi_fetch(monkeypatch, shaped_rows):
    async def fake_fetch(series_list, q="", max_close_h=None, cap=60):
        return shaped_rows

    monkeypatch.setattr(app_mod, "_kalshi_fetch", fake_fetch)


async def test_kalshi_cards_group_sides_and_sum_volume(monkeypatch):
    rows = [
        app_mod._kalshi_shape(
            _kraw("KXMLBGAME-26AUG22NYYBOS-NYY", "NYY at BOS Winner?",
                  "Yankees", 45, volume=1000,
                  close="2026-08-22T23:00:00Z"), "KXMLBGAME"),
        app_mod._kalshi_shape(
            _kraw("KXMLBGAME-26AUG22NYYBOS-BOS", "NYY at BOS Winner?",
                  "Red Sox", 57, volume=500,
                  close="2026-08-22T22:00:00Z"), "KXMLBGAME"),
        app_mod._kalshi_shape(
            _kraw("KXWTAMATCH-26AUG22ABC-XX", "A vs B", "A", 30),
            "KXWTAMATCH"),
    ]
    _wire_kalshi_fetch(monkeypatch, rows)
    out = await app_mod.api_desk_feed(venue="kalshi", league="all")
    assert len(out["cards"]) == 2
    game = next(c for c in out["cards"]
                if c["id"] == "KXMLBGAME-26AUG22NYYBOS")
    assert set(game) == CARD_KEYS
    assert game["league"] == "mlb"
    assert game["title"] == "NYY at BOS"          # ' Winner?' stripped
    assert game["volume_usd"] == 15.0             # (1000+500) cents
    assert game["close_time"] == "2026-08-22T22:00:00Z"   # min
    assert [o["id"] for o in game["outcomes"]] == [
        "KXMLBGAME-26AUG22NYYBOS-NYY", "KXMLBGAME-26AUG22NYYBOS-BOS"]
    assert game["outcomes"][0]["label"] == "Yankees"
    assert game["outcomes"][0]["price"] == 0.45
    assert game["history_id"] == "KXMLBGAME-26AUG22NYYBOS-NYY"
    # volume-known game sorts before the volume-null tennis match
    assert out["cards"][0]["id"] == "KXMLBGAME-26AUG22NYYBOS"
    tennis = out["cards"][1]
    assert tennis["league"] == "tennis" and tennis["volume_usd"] is None
    assert out["counts"] == {"mlb": 1, "tennis": 1, "all": 2}


async def test_kalshi_league_param_picks_series(monkeypatch):
    seen = {}

    async def fake_fetch(series_list, q="", max_close_h=None, cap=60):
        seen["series"] = series_list
        seen["max_close_h"] = max_close_h
        return []

    monkeypatch.setattr(app_mod, "_kalshi_fetch", fake_fetch)
    out = await app_mod.api_desk_feed(venue="kalshi", league="tennis")
    assert seen["series"] == ["KXATPMATCH", "KXWTAMATCH"]
    # 48h -> 168h (2026-08-22): a between-tournaments weekend left the
    # venue with ~5 open sports markets, all beyond 48h — the desk
    # showed All=0. The board now always shows the coming week.
    assert seen["max_close_h"] == 168
    assert out["cards"] == [] and out["counts"]["all"] == 0


# ── Feed: Kalshi league=everything (full-universe sweep reuse) ───────


async def test_kalshi_everything_cards_from_event_sweep(monkeypatch):
    evs = [
        {"event_ticker": "KXFED-26SEP", "title": "Fed decision Winner?",
         "markets": [
             {"ticker": "KXFED-26SEP-CUT", "yes_sub_title": "Cut",
              "yes_ask": 61, "status": "open", "volume": 300,
              "close_time": "2026-09-17T18:00:00Z"},
             {"ticker": "KXFED-26SEP-HOLD", "yes_sub_title": "Hold",
              "yes_ask": 40, "status": "open",
              "volume_dollars": "8.00",
              "close_time": "2026-09-17T18:00:00Z"},
             {"ticker": "KXFED-26SEP-DEAD", "yes_sub_title": "x",
              "yes_ask": 1, "status": "settled"},   # closed: dropped
         ]},
        {"event_ticker": "", "markets": [{"ticker": "X"}]},  # no id
        {"event_ticker": "KXEMPTY-1", "markets": []},        # no mkts
    ]

    async def fake_all():
        return evs

    monkeypatch.setattr(app_mod, "_kalshi_all_open_events", fake_all)
    out = await app_mod.api_desk_feed(venue="kalshi",
                                      league="everything")
    (c,) = out["cards"]
    assert set(c) == CARD_KEYS
    assert c["id"] == "KXFED-26SEP" and c["league"] == "everything"
    assert c["title"] == "Fed decision"
    assert c["volume_usd"] == 11.0    # 300 cents + $8.00 twin
    assert [o["id"] for o in c["outcomes"]] == [
        "KXFED-26SEP-CUT", "KXFED-26SEP-HOLD"]
    assert c["history_id"] == "KXFED-26SEP-CUT"
    assert out["counts"] == {"everything": 1, "all": 1}


async def test_kalshi_everything_cap_60_nulls_last(monkeypatch):
    def ev(i, vol):
        m = {"ticker": f"KXT{i:03d}-A-B", "yes_sub_title": "Yes",
             "yes_ask": 50, "status": "open"}
        if vol is not None:
            m["volume"] = vol
        return {"event_ticker": f"KXT{i:03d}-A", "title": f"T{i}",
                "markets": [m]}

    evs = [ev(i, (i + 1) * 100) for i in range(65)]
    evs.append(ev(900, None))                      # null volume

    async def fake_all():
        return evs

    monkeypatch.setattr(app_mod, "_kalshi_all_open_events", fake_all)
    out = await app_mod.api_desk_feed(venue="kalshi",
                                      league="everything")
    assert len(out["cards"]) == 60
    assert out["counts"]["everything"] == 66
    assert out["cards"][0]["volume_usd"] == 65.0   # top volume first
    # the null-volume card lost the cap race to every priced card
    assert all(c["volume_usd"] is not None for c in out["cards"])


async def test_unknown_kalshi_league_is_empty_not_500(monkeypatch):
    async def fake_fetch(series_list, q="", max_close_h=None, cap=60):
        assert series_list == []
        return []

    monkeypatch.setattr(app_mod, "_kalshi_fetch", fake_fetch)
    out = await app_mod.api_desk_feed(venue="kalshi", league="curling")
    assert out["cards"] == []
