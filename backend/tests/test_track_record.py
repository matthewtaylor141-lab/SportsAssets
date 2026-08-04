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


def test_copy_sleeve_rows_are_in_the_record_tagged_and_graded():
    """The copy sleeve IS the AI's trading (owner decision 2026-08-04):
    its rows count in the headline record, wear a sleeve tag, and their
    cohort stats still travel separately for per-sleeve grading. A copy
    slug never falls into 'unattributed' even when the engine mirror
    doesn't claim it."""
    positions = {"aec-mlb-shared-2026-08-02": _pos(2, 0.45, 0.5)}
    acts = [_trade("aec-mlb-shared-2026-08-02", TS_AUG2, 2, 0.225)]
    out = build(positions, acts, TS_AUG1,
                attributed=set(),         # mirror claims nothing
                copy_slugs={"aec-mlb-shared-2026-08-02"})
    assert len(out["trades"]) == 1
    assert out["trades"][0]["sleeve"] == "copy"
    assert out["summary"]["trades"] == 1 and out["summary"]["open"] == 1
    assert out["copy_sleeve"] == {"count": 1, "open": 1,
                                  "stake": 0.45, "net_pnl": 0.0}
    assert out["excluded_unattributed"] is None or \
        out["excluded_unattributed"]["count"] == 0


def test_no_attribution_sets_means_the_old_behavior_exactly():
    positions = {"aec-mlb-x-2026-08-02": _pos(2, 1.0, 1.1)}
    acts = [_trade("aec-mlb-x-2026-08-02", TS_AUG2, 2, 0.5)]
    out = build(positions, acts, TS_AUG1)
    assert len(out["trades"]) == 1
    assert out["excluded_unattributed"] is None
    assert out["copy_sleeve"] is None


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


def test_failed_hydrate_serves_the_window_and_arms_a_retry(monkeypatch):
    """A boot-time archive failure must degrade to the fresh window and
    KEEP TRYING — twice in one day a single failed read silently wiped
    history off the site until the next deploy."""
    import asyncio

    from sportsassets.api import track_record as tr

    class FailingPool:
        async def execute(self, q, *a):
            pass

        async def executemany(self, q, rows):
            pass

        async def fetch(self, q, *a):
            raise RuntimeError("too many connections")

    async def fake_get_pool():
        return FailingPool()

    import sportsassets.db as db
    monkeypatch.setattr(db, "get_pool", fake_get_pool)
    monkeypatch.setattr(tr, "_archive_ready", True)
    monkeypatch.setattr(tr, "_archived_ids", set())
    monkeypatch.setattr(tr, "_hydrate_task", None)
    monkeypatch.setitem(tr._archive_cache, "data", None)

    async def run():
        out = await tr._archive_and_union([{"id": "w1"}, {"id": "w2"}])
        # Serves the window acts it was given, does NOT cache them as
        # the archive, and a retry task is armed.
        assert [a["id"] for a in out] == ["w1", "w2"]
        assert tr._archive_cache["data"] is None
        assert tr._hydrate_task is not None and not tr._hydrate_task.done()
        tr._hydrate_task.cancel()

    asyncio.run(run())


def test_slimmed_activities_build_the_identical_record():
    """RAM keeps only what build() reads; the table keeps full payloads.
    If build() grows a field dependency _slim missed, this catches it."""
    from sportsassets.api.track_record import _slim

    acts = [
        _trade("aec-mlb-det-ath-2026-08-02", TS_AUG1 + 100, 4, 0.5),
        _trade("aec-mlb-det-ath-2026-08-02", TS_AUG1 + 200, 2, 0.6),
        _resolution("aec-mlb-det-ath-2026-08-02", TS_AUG2),
        _trade("tsc-atp-x-y-2026-08-02-tg-21pt5", TS_AUG2 + 50, 3, 0.25),
    ]
    # Give the resolution settlement facts + metadata like the venue does.
    acts[2]["positionResolution"].update({
        "beforePosition": {"cost": 2.0, "realized": 0.0,
                           "marketMetadata": {"title": "Tigers ML"}},
        "afterPosition": {"realized": 4.0,
                          "marketMetadata": {"title": "Tigers ML"}},
    })
    positions = {
        "aec-mlb-det-ath-2026-08-02": _pos(0, 2.0, 0.0, realized=0.0, expired=True),
        "tsc-atp-x-y-2026-08-02-tg-21pt5": _pos(3, 0.75, 0.9),
    }
    full = build(positions, acts, TS_AUG1)
    slim = build(positions, [_slim(a) for a in acts], TS_AUG1)
    full.pop("generated_at", None)
    slim.pop("generated_at", None)
    assert full == slim


def test_account_block_reconciles_cohort_plus_every_exclusion():
    """account = AI cohort + incidents + copies + unattributed: the number
    that must match the venue app. A page whose headline cannot be tied out
    to the account it claims to read is a page the owner stops trusting."""
    positions = {
        "eng-won": _pos(0, 1.0, 0.0, realized=1.1, expired=True),
        "big-lost": _pos(0, 150.0, 0.0, realized=-150.0, expired=True),
        "copy-open": _pos(4, 2.0, 2.2),
        "stray-won": _pos(0, 3.0, 0.0, realized=0.5, expired=True),
    }
    acts = [_trade("eng-won", TS_AUG2, 2, 0.5),
            _resolution("eng-won", TS_AUG2 + 3600),
            _trade("big-lost", TS_AUG2, 300, 0.5),
            _resolution("big-lost", TS_AUG2 + 3600),
            _trade("copy-open", TS_AUG2, 4, 0.5),
            _trade("stray-won", TS_AUG2, 6, 0.5),
            _resolution("stray-won", TS_AUG2 + 3600)]
    out = build(positions, acts, TS_AUG1, max_stake=100.0,
                attributed={"eng-won", "big-lost"},
                copy_slugs={"copy-open"})
    a = out["account"]
    assert a["trades"] == 4 and a["open"] == 1
    assert a["net_pnl"] == round(1.1 - 150.0 + 0.5, 2)
    assert a["stake"] == round(1.0 + 150.0 + 2.0 + 3.0, 2)
    # and the cohort headline still excludes all three other cohorts
    assert out["summary"]["net_pnl"] == 1.1


def test_paged_walks_to_eof_and_reports_completeness():
    from sportsassets.api.track_record import _paged

    calls = []

    def fake(params):
        calls.append(dict(params))
        page = len(calls)
        return {"positions": {f"s{page}": {}},
                "nextCursor": f"c{page}" if page < 3 else "",
                "eof": page >= 3}

    pages, complete = _paged(fake, {"limit": 100}, max_pages=40, pace=0)
    assert len(pages) == 3 and complete
    assert calls[1]["cursor"] == "c1" and calls[2]["cursor"] == "c2"


def test_paged_cap_reports_incomplete_never_silence():
    from sportsassets.api.track_record import _paged

    def endless(params):
        return {"positions": {}, "nextCursor": "more", "eof": False}

    pages, complete = _paged(endless, {}, max_pages=5, pace=0)
    assert len(pages) == 5 and not complete


def test_paged_retries_a_transient_failure_inside_the_walk():
    from sportsassets.api.track_record import _paged

    state = {"n": 0}

    def flaky(params):
        state["n"] += 1
        if state["n"] == 2:          # second call blows up once
            raise RuntimeError("rate limited")
        return {"nextCursor": "" if state["n"] > 2 else "c", "eof": state["n"] > 2}

    pages, complete = _paged(flaky, {}, max_pages=10, pace=0)
    assert complete and len(pages) == 2


def test_unhydrated_boot_with_deep_history_refuses_to_serve(monkeypatch):
    """A booted process that KNOWS the table holds deep history it has not
    loaded must refuse the request rather than serve the bare window as
    the record — that exact state shipped 215-settled/+$18 to the owner
    while the account's real record was 449/+$72.61 (2026-08-04)."""
    import asyncio

    from sportsassets.api import track_record as tr

    monkeypatch.setattr(tr, "_archived_ids", {f"a{i}" for i in range(50_000)})
    monkeypatch.setitem(tr._archive_cache, "data", None)
    monkeypatch.setitem(tr._raw_cache, "data",
                        {"positions": {}, "activities": [{"id": "w1"}]})
    monkeypatch.setitem(tr._raw_cache, "ts", 9e12)

    class Cfg:
        pmus_key_id = "k"
        pmus_secret_key = "s"

    monkeypatch.setattr(tr, "settings", lambda: Cfg())

    out = asyncio.run(tr.track_record())
    assert out["configured"] and "error" in out
    assert "hydrating" in out["error"]


def test_unhydrated_boot_with_shallow_history_still_serves(monkeypatch):
    """A genuinely young account (little archived history) must NOT be
    bricked by the deep-history guard."""
    import asyncio

    from sportsassets.api import track_record as tr

    monkeypatch.setattr(tr, "_archived_ids", {"a1", "a2"})
    monkeypatch.setitem(tr._archive_cache, "data", None)
    monkeypatch.setitem(tr._raw_cache, "data",
                        {"positions": {}, "activities": []})
    monkeypatch.setitem(tr._raw_cache, "ts", 9e12)

    class Cfg:
        pmus_key_id = "k"
        pmus_secret_key = "s"

    monkeypatch.setattr(tr, "settings", lambda: Cfg())

    out = asyncio.run(tr.track_record())
    assert "error" not in out and out["summary"]["trades"] == 0
