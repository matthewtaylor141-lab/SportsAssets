"""The track record must come from the venue account, windowed honestly."""

from sportsassets.api.track_record import build, classify_slug

TS_AUG1 = 1785542400.0     # 2026-08-01T00:00:00Z
TS_JUL30 = TS_AUG1 - 2 * 86_400
TS_AUG2 = TS_AUG1 + 86_400


def _trade(slug, ts, qty, price):
    return {"type": "ACTIVITY_TYPE_TRADE",
            "trade": {"marketSlug": slug, "qty": qty,
                      "price": {"value": price}, "createTime": ts * 1000}}


def _resolution(slug, ts):
    return {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION", "timestamp": ts * 1000,
            "positionResolution": {"marketSlug": slug}}


def _pos(qty, cost, value, realized=0.0, expired=False, title="T"):
    return {"netPosition": qty, "cost": cost, "cashValue": value,
            "realized": realized, "expired": expired,
            "marketMetadata": {"title": title, "outcome": "Yes"}}


def test_slug_classification_names_the_bet():
    assert classify_slug("astatc-mlb-sf-sd-2026-08-02-k-mickin-gte6")["category"] == "Player Prop"
    assert classify_slug("astatc-mlb-x")["sport"] == "Baseball"
    assert classify_slug("atc-mlb-min-sea-2026-08-02-f5-sea")["category"] == "Segment"
    assert classify_slug("tsc-wta-a-b-2026-08-02-tg-21pt5")["category"] == "Total"
    assert classify_slug("tsc-wta-a-b-2026-08-02-tg-21pt5")["sport"] == "Tennis"
    assert classify_slug("aec-mlb-det-ath-2026-08-02")["category"] == "Moneyline"
    assert classify_slug("atc-ekst-kat-rad-2026-08-02-draw")["sport"] == "Soccer"


def test_pre_window_entries_are_excluded_not_redated():
    positions = {"aec-mlb-old-x-2026-07-30": _pos(2, 1.0, 1.1),
                 "aec-mlb-new-y-2026-08-02": _pos(2, 1.0, 1.1)}
    acts = [_trade("aec-mlb-old-x-2026-07-30", TS_JUL30, 2, 0.5),
            _trade("aec-mlb-new-y-2026-08-02", TS_AUG2, 2, 0.5)]
    out = build(positions, acts, TS_AUG1)
    slugs = [r["market_slug"] for r in out["trades"]]
    assert slugs == ["aec-mlb-new-y-2026-08-02"]


def test_a_position_with_no_venue_trades_is_excluded_not_guessed():
    out = build({"aec-mlb-mystery-2026-08-02": _pos(2, 1.0, 1.1)}, [], TS_AUG1)
    assert out["trades"] == []


def test_entry_price_is_the_venues_own_vwap():
    positions = {"s": _pos(5, 1.6, 1.7)}
    acts = [_trade("s", TS_AUG2, 2, 0.30), _trade("s", TS_AUG2 + 60, 3, 0.34)]
    out = build(positions, acts, TS_AUG1)
    row = out["trades"][0]
    assert row["entry_price"] == round((2 * 0.30 + 3 * 0.34) / 5, 4)
    assert row["fills"] == 2


def test_summary_and_daily_come_from_settled_money_only():
    positions = {
        "won": _pos(0, 1.0, 0.0, realized=1.2, expired=True),
        "lost": _pos(0, 1.0, 0.0, realized=-1.0, expired=True),
        "open": _pos(2, 1.0, 1.15),
    }
    acts = [_trade("won", TS_AUG2, 2, 0.5), _resolution("won", TS_AUG2 + 3600),
            _trade("lost", TS_AUG2, 2, 0.5), _resolution("lost", TS_AUG2 + 3600),
            _trade("open", TS_AUG2, 2, 0.5)]
    out = build(positions, acts, TS_AUG1)
    s = out["summary"]
    assert (s["trades"], s["settled"], s["open"]) == (3, 2, 1)
    assert s["net_pnl"] == 0.2 and s["settled_stake"] == 2.0
    assert s["roi"] == 0.1 and s["win_rate"] == 0.5
    day = next(d for d in out["daily"] if d["settled"])
    assert day["pnl"] == 0.2 and day["pnl_estimated"] is False


def test_a_settlement_without_a_venue_timestamp_is_flagged_estimated():
    positions = {"won": _pos(0, 1.0, 0.0, realized=1.2, expired=True)}
    acts = [_trade("won", TS_AUG2, 2, 0.5)]     # no resolution activity
    out = build(positions, acts, TS_AUG1)
    day = next(d for d in out["daily"] if d["settled"])
    assert day["pnl_estimated"] is True


def test_over_limit_positions_are_excluded_and_always_disclosed():
    """The record may present a capped view; it may never hide the cap.
    Excluded rows leave every figure AND arrive in the payload as a count,
    their stake, and their net P&L — so the page can say what it omits."""
    positions = {
        "small-won": _pos(0, 1.0, 0.0, realized=1.1, expired=True),
        "big-lost": _pos(0, 150.0, 0.0, realized=-150.0, expired=True),
        "big-open": _pos(100, 120.0, 118.0),
    }
    acts = [_trade("small-won", TS_AUG2, 2, 0.5),
            _resolution("small-won", TS_AUG2 + 3600),
            _trade("big-lost", TS_AUG2, 300, 0.5),
            _resolution("big-lost", TS_AUG2 + 3600),
            _trade("big-open", TS_AUG2, 240, 0.5)]
    out = build(positions, acts, TS_AUG1, max_stake=100.0)
    assert [r["market_slug"] for r in out["trades"]] == ["small-won"]
    assert out["summary"]["net_pnl"] == 1.1          # the big loss is OUT...
    ex = out["excluded_over_limit"]                  # ...and DISCLOSED
    assert ex == {"limit": 100.0, "count": 2, "open": 1,
                  "stake": 270.0, "net_pnl": -150.0}


def test_no_cap_means_no_exclusion_and_a_null_disclosure():
    positions = {"big": _pos(0, 150.0, 0.0, realized=-150.0, expired=True)}
    acts = [_trade("big", TS_AUG2, 300, 0.5), _resolution("big", TS_AUG2 + 60)]
    out = build(positions, acts, TS_AUG1)
    assert len(out["trades"]) == 1
    assert out["excluded_over_limit"] is None


def test_a_cap_with_nothing_over_it_still_shows_the_rule():
    """Zero exclusions is information too: the reader sees the rule exists
    and that nothing currently trips it."""
    positions = {"small": _pos(2, 1.0, 1.1)}
    out = build(positions, [_trade("small", TS_AUG2, 2, 0.5)], TS_AUG1,
                max_stake=100.0)
    assert out["excluded_over_limit"]["count"] == 0


def test_a_resolved_market_absent_from_positions_still_settles_the_record():
    """The venue REMOVES resolved markets from the positions payload — the
    resolution activity is often the only record a settled trade leaves.
    Missing it is how the live site showed $0 P&L on 10 'settled' dust rows
    while the account had realized money."""
    positions = {}      # resolved market: gone from the payload entirely
    acts = [
        _trade("atc-alsv-aik-org-2026-08-02-org", TS_AUG2, 2, 0.5),
        {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION", "timestamp": (TS_AUG2 + 7200) * 1000,
         "positionResolution": {
             "marketSlug": "atc-alsv-aik-org-2026-08-02-org",
             "beforePosition": {"cost": 1.01, "realized": 0.0},
             "afterPosition": {"realized": 5.04,
                               "marketMetadata": {"title": "AIK vs Örgryte"}}}},
    ]
    out = build(positions, acts, TS_AUG1)
    assert len(out["trades"]) == 1
    row = out["trades"][0]
    assert row["settled"] and row["pnl"] == 5.04 and row["stake"] == 1.01
    assert out["summary"]["net_pnl"] == 5.04
    assert out["summary"]["wins"] == 1


def test_a_resolution_overrides_a_lagging_position_row():
    """The position row can still read realized=0 after the market resolves;
    the resolution activity is the settlement record and wins."""
    positions = {"m": _pos(2, 1.0, 0.0, realized=0.0, expired=True)}
    acts = [
        _trade("m", TS_AUG2, 2, 0.5),
        {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION", "timestamp": (TS_AUG2 + 60) * 1000,
         "positionResolution": {"marketSlug": "m",
                                "beforePosition": {"cost": 1.0},
                                "afterPosition": {"realized": -1.0}}},
    ]
    out = build(positions, acts, TS_AUG1)
    assert out["trades"][0]["pnl"] == -1.0
    assert out["summary"]["losses"] == 1 and out["summary"]["wins"] == 0


def test_zero_realized_settlements_are_pushes_not_losses():
    """Ten dust rows realizing exactly zero must not render as 0W-10L."""
    positions = {f"d{i}": _pos(0, 0.03, 0.0, realized=0.0, expired=True)
                 for i in range(3)}
    acts = [_trade(f"d{i}", TS_AUG2, 1, 0.03) for i in range(3)]
    out = build(positions, acts, TS_AUG1)
    s = out["summary"]
    assert s["settled"] == 3 and s["wins"] == 0 and s["losses"] == 0


def test_positive_attribution_excludes_what_the_engine_never_claimed():
    """A size cap alone let every non-engine fill under $100 wear the AI's
    record — the 2026-08-02 arb-bug cohort did exactly that. With the
    engine's own claimed slugs supplied, unclaimed positions are excluded
    AND disclosed, never blended in."""
    positions = {"aec-mlb-ours-2026-08-02": _pos(2, 1.0, 1.1),
                 "aec-atp-rogue-2026-08-02": _pos(30, 17.55, 16.0)}
    acts = [_trade("aec-mlb-ours-2026-08-02", TS_AUG2, 2, 0.5),
            _trade("aec-atp-rogue-2026-08-02", TS_AUG2, 30, 0.585)]
    out = build(positions, acts, TS_AUG1,
                attributed={"aec-mlb-ours-2026-08-02"})
    assert [r["market_slug"] for r in out["trades"]] == ["aec-mlb-ours-2026-08-02"]
    ex = out["excluded_unattributed"]
    assert ex["count"] == 1 and ex["open"] == 1
    assert ex["stake"] == 17.55


def test_copy_sleeve_positions_are_their_own_cohort_not_the_engines():
    """Copy-sleeve fills are excluded FIRST — even if the engine's mirror
    also touched the market, the copy trade never inflates the AI record."""
    positions = {"aec-mlb-shared-2026-08-02": _pos(2, 0.45, 0.5)}
    acts = [_trade("aec-mlb-shared-2026-08-02", TS_AUG2, 2, 0.225)]
    out = build(positions, acts, TS_AUG1,
                attributed={"aec-mlb-shared-2026-08-02"},
                copy_slugs={"aec-mlb-shared-2026-08-02"})
    assert out["trades"] == []
    assert out["excluded_copy_sleeve"]["count"] == 1
    assert out["excluded_unattributed"]["count"] == 0


def test_no_attribution_sets_means_the_old_behavior_exactly():
    positions = {"aec-mlb-x-2026-08-02": _pos(2, 1.0, 1.1)}
    acts = [_trade("aec-mlb-x-2026-08-02", TS_AUG2, 2, 0.5)]
    out = build(positions, acts, TS_AUG1)
    assert len(out["trades"]) == 1
    assert out["excluded_unattributed"] is None
    assert out["excluded_copy_sleeve"] is None


# ── archive refresh: warm path must not re-read the table ──────────────

def test_warm_archive_refresh_appends_without_rereading_the_table(monkeypatch):
    """Re-parsing the whole archive every ~30s refresh was the API's memory
    ratchet (glibc thread arenas keep freed parse pages; RSS climbed to the
    2 GB kill line three times on 2026-08-03). Warm refreshes must touch
    only the NEW rows; the full table read happens once, at cold boot."""
    import asyncio

    from sportsassets.api import track_record as tr

    class FakePool:
        def __init__(self):
            self.fetches, self.execs = [], []

        async def execute(self, q, *a):
            self.execs.append(q)

        async def executemany(self, q, rows):
            self.execs.append((q, rows))

        async def fetch(self, q, *a):
            self.fetches.append(q)
            return []

    pool = FakePool()

    async def fake_get_pool():
        return pool

    import sportsassets.db as db
    monkeypatch.setattr(db, "get_pool", fake_get_pool)
    monkeypatch.setattr(tr, "_archive_ready", True)
    monkeypatch.setattr(tr, "_archived_ids", {"a1"})
    monkeypatch.setitem(tr._archive_cache, "data", [{"id": "a1"}])

    out = asyncio.run(tr._archive_and_union([{"id": "a1"}, {"id": "a2"}]))

    assert [a["id"] for a in out] == ["a1", "a2"]
    assert pool.fetches == []                      # no full-table re-read
    inserted = [r for q, r in pool.execs if isinstance(r, list)]
    assert len(inserted) == 1 and len(inserted[0]) == 1  # only a2 upserted

    # Cold boot (empty in-process cache) DOES hydrate from the table.
    monkeypatch.setitem(tr._archive_cache, "data", None)
    asyncio.run(tr._archive_and_union([{"id": "a1"}]))
    assert any("SELECT payload" in q for q in pool.fetches)
