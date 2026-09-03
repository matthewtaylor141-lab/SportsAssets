"""The track record must come from the venue account, windowed honestly."""

from datetime import datetime, timezone

import pytest

from sportsassets.api.track_record import build, classify_slug


@pytest.fixture(autouse=True)
def _no_database(monkeypatch):
    """Keep track_record() off the database.

    Every DB helper here opens a real asyncpg connection, and with no
    database reachable the call does not fail — it HANGS, which wedged
    the entire backend suite (the run never finished, so nothing after
    this module was ever verified). These tests are about record
    assembly and the hydrate guard; persistence has its own coverage.
    """
    from sportsassets import db
    from sportsassets.api import track_record as tr

    async def _none(*_a, **_k):
        return None

    async def _no_pool(*_a, **_k):
        raise RuntimeError("no database in tests")

    # Every DB helper in track_record imports get_pool at CALL time, so
    # one patch here closes all of them; the module's own try/except
    # treats an unreachable database as "not hydrated" and carries on.
    monkeypatch.setattr(db, "get_pool", _no_pool)
    for fn in ("_load_persisted", "_load_legacy_persisted",
               "_persist_payload"):
        monkeypatch.setattr(tr, fn, _none, raising=False)

TS_AUG1 = 1785542400.0     # 2026-08-01T00:00:00Z (raw window boundary)
# Day bucketing is Eastern now, so activity timestamps sit MID-DAY in both
# clocks (16:00Z = noon EDT): a test about windows or settlement math must
# not quietly also be a test about midnight boundary crossings.
NOON = 16 * 3600
TS_JUL30 = TS_AUG1 - 2 * 86_400 + NOON   # 2026-07-30, noon ET
TS_AUG2 = TS_AUG1 + 86_400 + NOON        # 2026-08-02, noon ET


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
    assert out["excluded_undatable"] == 1


# ── Settlements dated without their entry (owner order 2026-09-02) ─────
# The core cases (a resolution with no entry, a short, neither time) live
# in tests/test_settlement_dating.py; these pin the window and the
# fail-closed edges.


def _nested_res(slug, ts, realized, cost):
    """A resolution the way the venue sends it: time nested only."""
    iso = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
            "positionResolution": {
                "marketSlug": slug, "createTime": iso,
                "afterPosition": {"realized": {"value": realized}},
                "beforePosition": {"cost": {"value": cost}}}}


def test_a_resolution_before_the_window_with_no_entry_is_excluded_not_redated():
    """The since floor moves to the settlement time for a row with no
    entry: a pre-window resolution is out (not undatable, not re-dated),
    an in-window one is in."""
    acts = [_nested_res("old", TS_JUL30, 1.0, 1.0),
            _nested_res("new", TS_AUG2, 1.0, 1.0)]
    out = build({}, acts, TS_AUG1)
    assert [r["market_slug"] for r in out["trades"]] == ["new"]
    assert out["excluded_undatable"] == 0
    # ...and through the position path too.
    positions = {"old": _pos(0, 1.0, 0.0, realized=1.0, expired=True),
                 "new": _pos(0, 1.0, 0.0, realized=1.0, expired=True)}
    out = build(positions, acts, TS_AUG1)
    assert [r["market_slug"] for r in out["trades"]] == ["new"]
    assert out["excluded_undatable"] == 0


def test_a_resolution_before_the_window_still_counts_in_the_venue_totals():
    """The VENUE-BASIS headline never windowed on anything; that must not
    change. Only the dated-row machinery moved its floor."""
    out = build({}, [_nested_res("old", TS_JUL30, 1.0, 1.0)], TS_AUG1)
    assert out["trades"] == []
    assert out["venue_totals"]["settled"] == 1
    assert out["venue_totals"]["net_pnl"] == 1.0


def test_a_cash_out_with_no_entry_is_dated_by_the_sale():
    """Sold to zero, entry scrolled out, no resolution: the sale's last
    time dates the row. With no known cost the P&L is the sells' own
    realized only (proceeds minus a zero cost would book the whole sale
    as profit) and the proceeds stand in for the stake."""
    acts = [_sell("g", TS_AUG2 + 3600, 10, 0.44, realized=-0.6)]
    out = build({}, acts, TS_AUG1)
    assert out["excluded_undatable"] == 0
    r = out["trades"][0]
    assert r["settled"] and r["cashed_out"]
    assert r["entry_ts"] is None and r["settled_ts"] == TS_AUG2 + 3600
    assert r["pnl"] == -0.6 and r["stake"] == 4.4
    assert out["summary"]["losses"] == 1
    day = next(d for d in out["daily"] if d["settled"])
    assert day["date"] == "2026-08-02" and day["pnl"] == -0.6
    # The position-row path agrees when the venue still carries the row.
    positions = {"g": _pos(0, 0.0, 0.0, realized=0.0)}
    out = build(positions, acts, TS_AUG1)
    r = out["trades"][0]
    assert r["cashed_out"] and r["pnl"] == -0.6 and r["stake"] == 4.4
    assert r["settled_ts"] == TS_AUG2 + 3600


def test_a_sale_with_no_entry_and_no_realized_is_undatable_not_a_push():
    """A sale with realizedPnl 0 and no entry in hand is what a short's
    OPENING sell looks like when the positions walk has not (yet) listed
    it. The sold ledger alone cannot tell it from a closing sale, so it
    is undatable, never a settled push (second adversarial review,
    2026-09-02)."""
    acts = [_sell("g", TS_AUG2 + 3600, 10, 0.44)]     # realizedPnl 0
    out = build({}, acts, TS_AUG1)
    assert out["trades"] == [] and out["excluded_undatable"] == 1
    assert out["summary"]["settled"] == 0


def test_an_opening_sell_stays_undatable_across_refreshes_until_it_resolves():
    """The two-refresh case: the positions walk runs before the
    activities walk and is capped, so a fresh short is absent from
    `positions` on one refresh and present (netPosition -10) on the
    next. Both readings must say the same thing -- undatable -- or the
    settled count would drop by one between refreshes and the monotonic
    guard would refuse the fresh build. The resolution dates it."""
    opening = TestSoldLedgerClassification._deep_trade(
        "sh", TS_AUG2, 10, 0.40, "ORDER_SIDE_SELL", rp=0.0)
    absent = build({}, [opening], TS_AUG1)
    assert absent["trades"] == [] and absent["excluded_undatable"] == 1
    present = build({"sh": _pos(-10, 6.0, 5.5, realized=0.0)}, [opening], TS_AUG1)
    assert present["trades"] == [] and present["excluded_undatable"] == 1
    assert absent["summary"]["settled"] == present["summary"]["settled"] == 0
    resolved = build({"sh": _pos(-10, 6.0, 0.0, realized=4.0, expired=True)},
                     [opening, _nested_res("sh", TS_AUG2 + 86_400, 4.0, 6.0)],
                     TS_AUG1)
    assert resolved["excluded_undatable"] == 0
    assert resolved["trades"][0]["settled_ts"] == TS_AUG2 + 86_400


def test_a_zero_realized_sale_reads_the_same_with_or_without_its_position_row():
    """Symmetry with the sold-only loop (third adversarial review,
    2026-09-02): a netPosition-0 row beside a zero-realized sale with no
    entry does not turn the sale into a cash-out, so the settled count
    cannot flip when the positions walk drops the row. A non-zero
    realized on the position row is money the venue reports, and dates."""
    sale = TestSoldLedgerClassification._deep_trade(
        "z", TS_AUG2 + 3600, 10, 0.44, "ORDER_SIDE_SELL", rp=0.0)
    without = build({}, [sale], TS_AUG1)
    with_row = build({"z": _pos(0, 0.0, 0.0, realized=0.0)}, [sale], TS_AUG1)
    assert without["trades"] == [] and without["excluded_undatable"] == 1
    assert with_row["trades"] == [] and with_row["excluded_undatable"] == 1
    money = build({"z": _pos(0, 0.0, 0.0, realized=0.7)}, [sale], TS_AUG1)
    assert money["excluded_undatable"] == 0
    assert money["trades"][0]["pnl"] == 0.7 and money["trades"][0]["cashed_out"] is True


def test_a_closing_sale_with_no_entry_is_still_dated_by_the_sale():
    """A non-zero realizedPnl is the venue saying the sale CLOSED a lot:
    that row is a cash-out dated on the sale, stake = proceeds."""
    acts = [TestSoldLedgerClassification._deep_trade(
        "c", TS_AUG2 + 3600, 10, 0.44, "ORDER_SIDE_SELL", rp=0.7)]
    out = build({}, acts, TS_AUG1)
    assert out["excluded_undatable"] == 0
    r = out["trades"][0]
    assert r["cashed_out"] is True and r["pnl"] == 0.7
    assert r["settled_ts"] == TS_AUG2 + 3600 and r["entry_ts"] is None


def test_a_settlement_dated_row_files_its_stake_under_its_settlement_day():
    """The day series foots to the summary: a row with no entry day
    files deployed/trades under its settlement day, so sum(daily.deployed)
    == summary.deployed (second adversarial review, 2026-09-02)."""
    out = build({}, [_nested_res("r", TS_AUG2 + 7200, 1.0, 3.0)], TS_AUG1)
    assert out["excluded_undatable"] == 0 and out["summary"]["deployed"] == 3.0
    assert sum(d["deployed"] for d in out["daily"]) == out["summary"]["deployed"]
    assert sum(d["trades"] for d in out["daily"]) == out["summary"]["trades"] == 1
    day = [d for d in out["daily"] if d["trades"] == 1][0]
    assert day["settled"] == 1 and day["pnl"] == 1.0 and day["pnl_estimated"] is False


def test_an_open_short_with_no_entry_is_not_a_settlement():
    """netPosition below zero with no resolution is a LIVE short whose
    only trade is its opening SELL. Dating it by that sell would file a
    live position as a push; it stays undatable until it resolves."""
    positions = {"sh": _pos(-10, 6.0, 5.5, realized=0.0)}
    opening = TestSoldLedgerClassification._deep_trade(
        "sh", TS_AUG2, 10, 0.40, "ORDER_SIDE_SELL", rp=0.0)
    out = build(positions, [opening], TS_AUG1)
    assert out["trades"] == [] and out["excluded_undatable"] == 1
    # Once it resolves, the resolution dates it.
    out = build({"sh": _pos(-10, 6.0, 0.0, realized=4.0, expired=True)},
                [opening, _nested_res("sh", TS_AUG2 + 86_400, 4.0, 6.0)],
                TS_AUG1)
    assert out["excluded_undatable"] == 0
    r = out["trades"][0]
    assert r["pnl"] == 4.0 and r["settled_ts"] == TS_AUG2 + 86_400


def test_rows_with_an_entry_are_untouched_by_settlement_dating():
    """A row that has an entry keeps its entry-window rule: a pre-window
    entry resolved inside the window is still excluded, not re-dated on
    its resolution."""
    positions = {"old": _pos(0, 1.0, 0.0, realized=1.0, expired=True)}
    acts = [_trade("old", TS_JUL30, 2, 0.5), _nested_res("old", TS_AUG2, 1.0, 1.0)]
    out = build(positions, acts, TS_AUG1)
    assert out["trades"] == [] and out["excluded_undatable"] == 0


def test_settlement_dated_rows_take_their_place_in_the_tape_by_that_time():
    acts = [_trade("early", TS_AUG2, 2, 0.5),
            _nested_res("late", TS_AUG2 + 7200, 1.0, 1.0),
            _trade("latest", TS_AUG2 + 10_000, 2, 0.5)]
    positions = {"early": _pos(2, 1.0, 1.1), "latest": _pos(2, 1.0, 1.1)}
    out = build(positions, acts, TS_AUG1)
    assert [r["market_slug"] for r in out["trades"]] == ["latest", "late", "early"]


def test_a_settlement_dated_row_reaches_the_unattributed_daily_slice():
    acts = [_sell("stray", TS_AUG2 + 3600, 10, 0.44, realized=-0.6)]
    out = build({}, acts, TS_AUG1, attributed={"something-else"})
    assert out["trades"] == []
    ex = out["excluded_unattributed"]
    assert ex["count"] == 1 and ex["net_pnl"] == -0.6
    assert [d["date"] for d in ex["daily"]] == ["2026-08-02"]


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
    assert day["date"] == "2026-08-02"     # the EASTERN day of settlement
    assert day["pnl"] == 0.2 and day["pnl_estimated"] is False


def test_a_settlement_without_a_venue_timestamp_is_flagged_estimated():
    positions = {"won": _pos(0, 1.0, 0.0, realized=1.2, expired=True)}
    acts = [_trade("won", TS_AUG2, 2, 0.5)]     # no resolution activity
    out = build(positions, acts, TS_AUG1)
    day = next(d for d in out["daily"] if d["settled"])
    assert day["pnl_estimated"] is True


def test_a_late_night_settlement_buckets_to_the_eastern_day():
    """03:00Z Aug 5 is 11pm ET Aug 4: Tuesday's calendar box, not
    Wednesday's. UTC bucketing pushed every post-8pm-ET settlement onto
    the NEXT day's box — Wednesday wore Tuesday night's -$16 two days
    running (owner report 2026-08-05)."""
    ts_settle = TS_AUG1 + 4 * 86_400 + 3 * 3600   # 2026-08-05T03:00:00Z
    positions = {"won": _pos(0, 1.0, 0.0, realized=1.2, expired=True)}
    acts = [_trade("won", TS_AUG2, 2, 0.5), _resolution("won", ts_settle)]
    out = build(positions, acts, TS_AUG1)
    assert [d["date"] for d in out["daily"] if d["settled"]] == ["2026-08-04"]
    assert all(d["date"] != "2026-08-05" for d in out["daily"])


def test_the_accounts_first_utc_hours_fold_into_day_one():
    """02:00Z Aug 1 is 10pm ET Jul 31. The window boundary is UTC
    midnight, so the entry IS in the record — and it lands in the
    record's FIRST day (2026-08-01), not a phantom 2026-07-31 box the
    August month view never shows (owner report 2026-08-05: the opening
    session's +$45 vanished from Aug 1). Only pre-window-date days fold
    forward; every later day still buckets Eastern."""
    since_ts = datetime.strptime("2026-08-01", "%Y-%m-%d") \
        .replace(tzinfo=timezone.utc).timestamp()   # as track_record parses
    positions = {"late-jul31": _pos(2, 1.0, 1.1)}
    acts = [_trade("late-jul31", TS_AUG1 + 2 * 3600, 2, 0.5)]
    out = build(positions, acts, since_ts)
    assert [r["market_slug"] for r in out["trades"]] == ["late-jul31"]
    assert out["trades"][0]["entry_date"] == "2026-08-01"
    assert [d["date"] for d in out["daily"]] == ["2026-08-01"]


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


def test_pnl_cap_excludes_big_swings_both_directions_and_discloses():
    """Owner directive 2026-08-06: no single trade may move any displayed
    P&L by more than the cap — either direction. Excluded rows leave every
    figure and arrive disclosed in excluded_over_pnl; the account tie-out
    still reconciles."""
    positions = {
        "small-won": _pos(0, 1.0, 0.0, realized=1.1, expired=True),
        "big-won": _pos(0, 40.0, 0.0, realized=150.0, expired=True),
        "big-lost": _pos(0, 120.0, 0.0, realized=-120.0, expired=True),
    }
    acts = [_trade("small-won", TS_AUG2, 2, 0.5),
            _resolution("small-won", TS_AUG2 + 3600),
            _trade("big-won", TS_AUG2, 80, 0.5),
            _resolution("big-won", TS_AUG2 + 3600),
            _trade("big-lost", TS_AUG2, 240, 0.5),
            _resolution("big-lost", TS_AUG2 + 3600)]
    out = build(positions, acts, TS_AUG1, max_abs_pnl=100.0)
    assert [r["market_slug"] for r in out["trades"]] == ["small-won"]
    assert out["summary"]["net_pnl"] == 1.1
    day = next(d for d in out["daily"] if d["settled"])
    assert day["pnl"] == 1.1
    ex = out["excluded_over_pnl"]
    assert ex == {"limit": 100.0, "count": 2, "open": 0,
                  "stake": 160.0, "net_pnl": 30.0}
    assert out["account"]["net_pnl"] == round(1.1 + 30.0, 2)


def test_pnl_cap_applies_to_copy_sleeve_rows_too():
    positions = {"cp-big": _pos(0, 90.0, 0.0, realized=140.0, expired=True),
                 "cp-ok": _pos(0, 3.0, 0.0, realized=2.5, expired=True)}
    acts = [_trade("cp-big", TS_AUG2, 180, 0.5),
            _resolution("cp-big", TS_AUG2 + 3600),
            _trade("cp-ok", TS_AUG2, 6, 0.5),
            _resolution("cp-ok", TS_AUG2 + 3600)]
    out = build(positions, acts, TS_AUG1,
                copy_slugs={"cp-big", "cp-ok"}, max_abs_pnl=100.0)
    assert out["copy_sleeve"]["count"] == 1
    assert out["copy_sleeve"]["net_pnl"] == 2.5
    assert out["excluded_over_pnl"]["count"] == 1
    assert out["excluded_over_pnl"]["net_pnl"] == 140.0


def test_pnl_cap_reads_open_positions_at_mark_to_market():
    positions = {"open-big": _pos(500, 50.0, 190.0),   # +140 unrealized
                 "open-ok": _pos(2, 1.0, 1.15)}
    acts = [_trade("open-big", TS_AUG2, 500, 0.1),
            _trade("open-ok", TS_AUG2, 2, 0.5)]
    out = build(positions, acts, TS_AUG1, max_abs_pnl=100.0)
    assert [r["market_slug"] for r in out["trades"]] == ["open-ok"]
    assert out["excluded_over_pnl"] == {"limit": 100.0, "count": 1,
                                        "open": 1, "stake": 50.0,
                                        "net_pnl": 0.0}


def test_pnl_cap_outranks_the_unattributed_bucket():
    """A capped swing must not reach the unattributed cohort — its daily
    slice folds into the software category downstream, which would smuggle
    the excluded money back into a displayed P&L."""
    positions = {"ghost-big": _pos(0, 50.0, 0.0, realized=-130.0,
                                   expired=True)}
    acts = [_trade("ghost-big", TS_AUG2, 100, 0.5),
            _resolution("ghost-big", TS_AUG2 + 3600)]
    out = build(positions, acts, TS_AUG1, attributed={"something-else"},
                max_abs_pnl=100.0)
    assert out["excluded_over_pnl"]["count"] == 1
    assert out["excluded_unattributed"]["count"] == 0
    assert out["excluded_unattributed"]["daily"] == []


def test_pnl_cap_covers_resolution_only_rows():
    """Settled markets the positions payload no longer carries settle the
    record through their resolution — the cap must reach that path too."""
    res = {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
           "timestamp": (TS_AUG2 + 3600) * 1000,
           "positionResolution": {
               "marketSlug": "gone-big",
               "beforePosition": {"realized": {"value": 180.0},
                                  "cost": {"value": 60.0}}}}
    acts = [_trade("gone-big", TS_AUG2, 120, 0.5), res]
    out = build({}, acts, TS_AUG1, max_abs_pnl=100.0)
    assert out["trades"] == []
    assert out["excluded_over_pnl"]["count"] == 1
    assert out["excluded_over_pnl"]["net_pnl"] == 180.0


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

def test_warm_archive_refresh_folds_in_place_without_rereading_the_table(monkeypatch):
    """Re-parsing the whole archive every ~30s refresh was the API's memory
    ratchet (glibc thread arenas keep freed parse pages; RSS climbed to the
    2 GB kill line three times on 2026-08-03). Warm refreshes must touch
    only the NEW rows; the full table read happens once, at cold boot.
    Since design D (2026-09-03) the archive is the folded ledgers and a
    warm refresh folds the new rows INTO it, in place."""
    import asyncio

    from sportsassets.api import track_record as tr

    class _Tx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Cursor:
        async def fetch(self, n):
            return []

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

        # Streaming-cursor hydrate path (2026-08-09): one scan, batched.
        def acquire(self):
            pool = self

            class _Acq:
                async def __aenter__(self):
                    class _Conn:
                        def transaction(self):
                            return _Tx()

                        async def cursor(self, q, *a):
                            pool.fetches.append(q)
                            return _Cursor()
                    return _Conn()

                async def __aexit__(self, *a):
                    return False
            return _Acq()

    pool = FakePool()

    async def fake_get_pool():
        return pool

    import sportsassets.db as db
    monkeypatch.setattr(db, "get_pool", fake_get_pool)
    monkeypatch.setattr(tr, "_archive_ready", True)
    monkeypatch.setattr(tr, "_archived_ids", {"a1"})
    monkeypatch.setitem(tr._snap_state, "at", 9e12)   # no rolling save here

    # Activities carry a real type: the union keeps only what build()
    # reads (memory fix 2026-08-25), so an untyped fixture would be
    # filtered out and prove nothing about the fold.
    _T = "ACTIVITY_TYPE_TRADE"

    def _fill(aid, qty):
        return {"id": aid, "type": _T,
                "trade": {"marketSlug": "s", "qty": qty,
                          "price": {"value": 0.5}, "createTime": TS_AUG2 * 1000}}

    led = tr._ArchiveLedgers()
    led.fold(_fill("a1", 2))
    monkeypatch.setitem(tr._archive_cache, "data", led)
    out = asyncio.run(tr._archive_and_union([_fill("a1", 2), _fill("a2", 3)]))

    assert out is led, "the warm path folds into the SAME archive object"
    assert led.rows == 2 and set(led.ids) == {"a1", "a2"}
    assert led.entries["s"]["qty"] == 5.0 and led.entries["s"]["fills"] == 2
    assert pool.fetches == []                      # no full-table re-read
    inserted = [r for q, r in pool.execs if isinstance(r, list)]
    assert len(inserted) == 1 and len(inserted[0]) == 1  # only a2 upserted

    # The refresh union must ALSO drop unread types, or the boot-time
    # saving evaporates within a day as the venue window re-adds them
    # at ~15k/day — the memory ratchet, returning by another door.
    monkeypatch.setattr(tr, "_archived_ids", {"a1", "a2"})
    out2 = asyncio.run(tr._archive_and_union([
        _fill("a2", 3),
        {"id": "a3", "type": "ACTIVITY_TYPE_DEPOSIT"},
    ]))
    assert out2 is led and led.rows == 2 and "a3" not in led.ids, \
        "a deposit is never read by build() and must not be retained"

    # A DEEP_SWEEP can re-show a row the week-deep _archived_ids has
    # forgotten but the ledgers already hold. The row form appended it
    # again (a second fill on the same market); the ledgers refuse the
    # repeat id, so the archive cannot inflate through this door.
    monkeypatch.setattr(tr, "_archived_ids", set())
    asyncio.run(tr._archive_and_union([_fill("a1", 2)]))
    assert led.rows == 2 and led.entries["s"]["qty"] == 5.0

    # Cold boot (empty in-process cache) DOES hydrate from the table —
    # via one streaming cursor scan, never repeated all-rows fetches.
    monkeypatch.setitem(tr._archive_cache, "data", None)
    monkeypatch.setitem(tr._hydrate_progress, "ledgers", None)
    monkeypatch.setitem(tr._hydrate_progress, "last", "")
    monkeypatch.setitem(tr._hydrate_progress, "running", False)
    cold = asyncio.run(tr._archive_and_union([{"id": "a1"}]))
    assert any("payload FROM pmus_activity_archive" in q
               and "ORDER BY id" in q for q in pool.fetches)
    assert isinstance(cold, tr._ArchiveLedgers) and cold.rows == 0
    assert tr._hydrate_progress["ledgers"] is None, "completion clears the buffer"


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
    monkeypatch.setitem(tr._hydrate_progress, "ledgers", None)
    monkeypatch.setitem(tr._hydrate_progress, "last", "")
    monkeypatch.setitem(tr._hydrate_progress, "running", False)

    async def run():
        out = await tr._archive_and_union(
            [{"id": "w1", "type": "ACTIVITY_TYPE_TRADE"},
             {"id": "w2", "type": "ACTIVITY_TYPE_TRADE"}])
        # No archive is available: nothing is cached as the archive (the
        # request path then serves the venue window it holds itself),
        # and a retry task is armed.
        assert out is None
        assert tr._archive_cache["data"] is None
        assert tr._hydrate_task is not None and not tr._hydrate_task.done()
        tr._hydrate_task.cancel()

    asyncio.run(run())


def test_slimmed_activities_build_the_identical_record():
    """RAM keeps only what build() reads; the table keeps full payloads.
    If build() grows a field dependency _slim missed, this catches it."""
    from sportsassets.api.track_record import _slim

    acts = [
        _trade("aec-mlb-det-ath-2026-08-02", TS_AUG1 + NOON + 100, 4, 0.5),
        _trade("aec-mlb-det-ath-2026-08-02", TS_AUG1 + NOON + 200, 2, 0.6),
        _resolution("aec-mlb-det-ath-2026-08-02", TS_AUG2),
        _trade("tsc-atp-x-y-2026-08-02-tg-21pt5", TS_AUG2 + 50, 3, 0.25),
        # A SELL must survive slimming: dropping side/realizedPnl turned
        # archived cash-outs back into buys (found live 2026-08-08).
        _sell("tsc-atp-x-y-2026-08-02-tg-21pt5", TS_AUG2 + 90, 1, 0.30,
              realized=0.05),
        # A resolution the way the venue actually sends it — time NESTED
        # only, no top-level timestamp — with no entry trade in hand. The
        # slim row used to drop that time, so this row was undatable
        # after slimming and dated before it (adversarial review
        # 2026-09-02): the equivalence held only because the old test
        # never carried a nested-only resolution.
        _nested_res("aec-mlb-late-2026-08-02", TS_AUG2 + 7200, 0.7, 3.0),
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
    # Not vacuously equal: the nested-only resolution is DATED on both
    # sides, not undatable on both.
    assert full["excluded_undatable"] == 0
    assert "aec-mlb-late-2026-08-02" in {r["market_slug"] for r in full["trades"]}


def test_slim_lifts_the_time_build_reads_and_nothing_else_moves():
    """The slim row's top-level timestamp is exactly the value build()
    reads: for a trade `_act_ts(act) or _act_ts(trade)` (top level first,
    so a trade carrying both keeps the top-level one), for a resolution
    `_any_ts(act)`. Proven for every placement the venue has used."""
    from sportsassets.api.track_record import _slim
    from sportsassets.api.pmus_account import _act_ts, _any_ts

    nested_only = _trade("m", TS_AUG2, 1, 0.5)
    top_only = {"type": "ACTIVITY_TYPE_TRADE", "timestamp": TS_AUG2 * 1000,
                "trade": {"marketSlug": "m", "qty": 1, "price": {"value": 0.5}}}
    both = {"type": "ACTIVITY_TYPE_TRADE", "timestamp": TS_AUG2 * 1000,
            "trade": {"marketSlug": "m", "qty": 1, "price": {"value": 0.5},
                      "createTime": (TS_AUG2 + 999) * 1000}}
    for a in (nested_only, top_only, both):
        want = _act_ts(a) or _act_ts(a["trade"])
        s = _slim(a)
        assert s["timestamp"] == want
        assert (_act_ts(s) or _act_ts(s["trade"])) == want
    assert _slim(both)["timestamp"] == TS_AUG2       # top level wins
    assert _slim(both)["trade"]["createTime"] == TS_AUG2 + 999

    res = _nested_res("r", TS_AUG2 + 60, 1.0, 1.0)
    assert _slim(res)["timestamp"] == _any_ts(res) == TS_AUG2 + 60
    assert _any_ts(_slim(res)) == _any_ts(res)
    top_res = _resolution("r", TS_AUG2 + 61)
    assert _slim(top_res)["timestamp"] == _any_ts(top_res) == TS_AUG2 + 61
    # A resolution with no time anywhere stays timeless after slimming:
    # unknown is carried, never invented.
    timeless = {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
                "positionResolution": {"marketSlug": "t"}}
    assert _slim(timeless)["timestamp"] is None


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


# ── Cash-outs (owner directive 2026-08-08: sold trades count) ─────────


def _sell(slug, ts, qty, price, realized=0.0):
    return {"type": "ACTIVITY_TYPE_TRADE",
            "trade": {"marketSlug": slug, "qty": qty, "side": "TRADE_SIDE_SELL",
                      "price": {"value": price}, "createTime": ts * 1000,
                      "realizedPnl": {"value": realized}}}


def test_sells_do_not_inflate_the_entry_vwap():
    positions = {"s": _pos(0, 0.0, 0.0, realized=0.4)}
    acts = [_trade("s", TS_AUG2, 10, 0.50),
            _sell("s", TS_AUG2 + 3600, 10, 0.54, realized=0.4)]
    out = build(positions, acts, TS_AUG1)
    r = out["trades"][0]
    assert r["entry_price"] == 0.50        # the sell must not average in
    assert r["fills"] == 1


def test_sold_out_position_row_settles_with_the_sale_pnl():
    """netPosition 0, market unresolved: the sale IS the settlement —
    counted, dated to the sale, flagged cashed_out."""
    positions = {"s": _pos(0, 0.0, 0.0, realized=0.0)}   # venue realized lags
    acts = [_trade("s", TS_AUG2, 10, 0.50),
            _sell("s", TS_AUG2 + 3600, 10, 0.54, realized=0.4)]
    out = build(positions, acts, TS_AUG1)
    r = out["trades"][0]
    assert r["settled"] and r["cashed_out"]
    assert r["pnl"] == 0.4
    assert r["settled_ts"] == TS_AUG2 + 3600
    assert out["summary"]["net_pnl"] == 0.4
    assert out["summary"]["wins"] == 1


def test_vanished_cashed_out_position_is_synthesized_from_the_sells():
    """Fully sold AND dropped from the positions payload with no
    resolution: the record must rebuild it from entry + sell trades."""
    acts = [_trade("gone", TS_AUG2, 10, 0.50),
            _sell("gone", TS_AUG2 + 3600, 10, 0.44, realized=-0.6)]
    out = build({}, acts, TS_AUG1)
    r = out["trades"][0]
    assert r["market_slug"] == "gone"
    assert r["settled"] and r["cashed_out"]
    assert r["pnl"] == -0.6
    assert out["summary"]["losses"] == 1


def test_sell_without_realizedpnl_falls_back_to_proceeds_minus_cost():
    acts = [_trade("g2", TS_AUG2, 10, 0.50),
            _sell("g2", TS_AUG2 + 3600, 10, 0.56)]
    out = build({}, acts, TS_AUG1)
    assert out["trades"][0]["pnl"] == 0.6   # 10*(0.56-0.50)


def test_snapshot_roundtrip_preserves_the_slim_archive():
    """The compact snapshot is the one-read boot path that ended the
    grind-vs-dying-database cycle (2026-08-09): what goes in must come
    out byte-identical, or the record silently changes shape."""
    from sportsassets.api.track_record import _pack_rows, _unpack_rows

    rows = [{"id": f"a{i}", "type": "ACTIVITY_TYPE_TRADE",
             "timestamp": 1000.0 + i,
             "trade": {"marketSlug": "mlb-tor-phi-2026-08-08", "qty": i,
                       "price": {"value": 0.5}, "side": None,
                       "realizedPnl": None, "createTime": 1000.0 + i}}
            for i in range(500)]
    assert _unpack_rows(_pack_rows(rows)) == rows


class TestSoldLedgerClassification:
    """Task #69 fixes, from the 2026-08-19 raw-feed audit (6,747 trades):
    the venue's top-level trade side is ALWAYS None — the truth lives on
    the nested execution order — and the old rp!=0 fallback misbooked
    zero-P&L sells as buys and short-closing BUYS as sales."""

    @staticmethod
    def _deep_trade(slug, ts, qty, price, side, rp=0.0):
        return {"type": "ACTIVITY_TYPE_TRADE",
                "trade": {"marketSlug": slug, "qty": qty,
                          "price": {"value": price},
                          "createTime": ts * 1000,
                          "side": None,
                          "realizedPnl": {"value": rp},
                          "aggressorExecution": {
                              "order": {"side": side}}}}

    def test_zero_pnl_sell_is_a_sale_not_a_buy(self):
        # Sold at exactly avg cost: rp == 0. The old heuristic booked
        # this as a BUY, inflating the entry VWAP and hiding the sale.
        acts = [_trade("mlb-z", TS_AUG2, 100, 0.50),
                self._deep_trade("mlb-z", TS_AUG2 + 60, 100, 0.50,
                                 "ORDER_SIDE_SELL", rp=0.0)]
        out = build({}, acts, TS_AUG1)
        srow = next(s for s in out["sold_markets"] if s["slug"] == "mlb-z")
        assert srow["qty"] == 100
        assert srow["proceeds"] == 50.0

    def test_short_closing_buy_realizes_but_adds_no_proceeds(self):
        # ORDER_SIDE_BUY with realized P&L (closing a short): the loss is
        # real cash and must count, but the buy's qty*price is money OUT,
        # not sale proceeds — the old code padded proceeds with it.
        acts = [self._deep_trade("wta-x", TS_AUG2, 587, 0.09,
                                 "ORDER_SIDE_BUY", rp=-435.07)]
        out = build({}, acts, TS_AUG1)
        srow = next(s for s in out["sold_markets"] if s["slug"] == "wta-x")
        assert srow["proceeds"] == 0.0
        assert srow["qty"] == 0.0
        assert srow["realized"] == -435.07

    def test_rp_fallback_still_works_when_no_side_anywhere(self):
        # Slim-archive rows from before the deep-side capture carry
        # side=None and no execution objects; nonzero rp still marks
        # them as position-closing so archived cash-outs keep counting.
        acts = [{"type": "ACTIVITY_TYPE_TRADE",
                 "trade": {"marketSlug": "ten-y", "qty": 50,
                           "price": {"value": 0.60},
                           "createTime": (TS_AUG2 + 60) * 1000,
                           "side": None,
                           "realizedPnl": {"value": 5.0}}}]
        out = build({}, acts, TS_AUG1)
        srow = next(s for s in out["sold_markets"] if s["slug"] == "ten-y")
        assert srow["realized"] == 5.0
        assert srow["proceeds"] == 30.0

    def test_slim_archive_captures_the_deep_side(self):
        from sportsassets.api.track_record import _slim

        slim = _slim(self._deep_trade(
            "mlb-q", TS_AUG2, 10, 0.40, "ORDER_SIDE_SELL", rp=1.0))
        assert slim["trade"]["side"] == "ORDER_SIDE_SELL"
