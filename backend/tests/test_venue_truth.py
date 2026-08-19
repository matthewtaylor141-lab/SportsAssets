"""Venue-truth P&L rebuild (task #74): the numbers the site serves must
be reconstructable from venue payloads alone, with the exact defects the
2026-08-17 weekly report caught kept impossible: no-side fills priced
off the yes field, settlement cost components treated as our cash,
duplicate fills double-counted, and out-of-window settlements booking
pure revenue as profit."""

from sportsassets.api.venue_truth import (
    build_payload,
    daily_series,
    kalshi_positions,
    pm_positions,
)


def _kx_fill(ticker, side, action, count, px, fee=0.0, tid=None):
    return {
        "trade_id": tid or f"t-{ticker}-{side}-{action}-{count}-{px}",
        "ticker": ticker, "side": side, "action": action,
        "count": count,
        f"{side}_price_dollars": px,
        "fee_dollars": fee,
        "created_time": "2026-08-18T14:00:00Z",
    }


def _kx_settle(ticker, result, revenue, when="2026-08-18T20:00:00Z"):
    return {"ticker": ticker, "market_result": result,
            "revenue_dollars": revenue, "settled_time": when}


class TestKalshiSignedCash:
    def test_winner_realizes_revenue_minus_outlay_minus_fees(self):
        raw = {
            "fills": [_kx_fill("KXT-A", "yes", "buy", 100, 0.40, fee=0.7)],
            "settlements": [_kx_settle("KXT-A", "yes", 100.0)],
        }
        rows = kalshi_positions(raw)
        assert len(rows) == 1
        r = rows[0]
        assert r["settled"] is True
        assert r["cost"] == 40.0
        assert r["realized"] == 59.3  # 100 - 40 - 0.70

    def test_no_side_fill_is_priced_off_the_no_field(self):
        # A no fill carries BOTH price fields; using yes_price would book
        # $0.65 instead of $0.35 of outlay.
        f = _kx_fill("KXT-B", "no", "buy", 100, 0.35)
        f["yes_price_dollars"] = 0.65
        raw = {"fills": [f],
               "settlements": [_kx_settle("KXT-B", "no", 100.0)]}
        r = kalshi_positions(raw)[0]
        assert r["cost"] == 35.0
        assert r["realized"] == 65.0

    def test_duplicate_trade_ids_count_once(self):
        f = _kx_fill("KXT-C", "yes", "buy", 50, 0.50, tid="dup-1")
        raw = {"fills": [f, dict(f)], "settlements": []}
        r = kalshi_positions(raw)[0]
        assert r["cost"] == 25.0
        assert r["open"] is True

    def test_sell_credits_proceeds(self):
        raw = {
            "fills": [
                _kx_fill("KXT-D", "yes", "buy", 100, 0.30, fee=0.5),
                _kx_fill("KXT-D", "yes", "sell", 100, 0.45, fee=0.5),
            ],
            "settlements": [_kx_settle("KXT-D", "no", 0.0)],
        }
        r = kalshi_positions(raw)[0]
        # Sold flat before the loss: 45 - 30 - 1 in fees.
        assert r["realized"] == 14.0

    def test_settlement_without_fills_is_window_incomplete(self):
        raw = {"fills": [],
               "settlements": [_kx_settle("KXT-E", "yes", 200.0)]}
        r = kalshi_positions(raw)[0]
        assert r["window_complete"] is False
        payload = build_payload([], kalshi_positions(raw), "2026-08-10")
        # Pure-revenue phantom profit must not reach any aggregate.
        assert payload["total"]["realized"] == 0.0
        assert payload["total"]["settled"] == 0
        assert payload["kalshi_window_incomplete"]["n"] == 1
        assert "KXT-E" in payload["kalshi_window_incomplete"]["tickers"]


def _pm_trade(slug, qty, px, side="SIDE_BUY", rp=0.0):
    return {"type": "ACTIVITY_TYPE_TRADE",
            "trade": {"marketSlug": slug, "qty": qty, "price": px,
                      "side": side, "realizedPnl": rp,
                      "createTime": "2026-08-18T15:00:00Z"}}


def _pm_resolution(slug, realized, cost, when="2026-08-18T21:00:00Z"):
    return {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
            "timestamp": when,
            "positionResolution": {
                "marketSlug": slug,
                "afterPosition": {"realized": {"value": realized}},
                "beforePosition": {"cost": {"value": cost}},
            }}


class TestPolymarketAfterPosition:
    def test_resolution_realized_is_the_venue_number(self):
        acts = [_pm_trade("mlb-a", 100, 0.40),
                _pm_resolution("mlb-a", 60.0, 40.0)]
        r = pm_positions(acts)[0]
        assert r["settled"] is True
        assert r["realized"] == 60.0
        assert r["cost"] == 40.0

    def test_sideless_sell_detected_by_realized_pnl(self):
        # Observed venue dialect: trade rows with side=None; a nonzero
        # realizedPnl is the sell marker.
        acts = [_pm_trade("ten-b", 50, 0.50, side=None),
                _pm_trade("ten-b", 50, 0.60, side=None, rp=5.0)]
        r = pm_positions(acts)[0]
        assert r["settled"] is False
        assert r["open"] is False       # flat: bought == sold
        assert r["realized"] == 5.0

    def test_unresolved_with_shares_is_open_at_cost_no_pnl_claim(self):
        acts = [_pm_trade("nba-c", 200, 0.25)]
        r = pm_positions(acts)[0]
        assert r["open"] is True
        assert r["cost"] == 50.0
        assert r["realized"] == 0.0


class TestCombinedPayload:
    def test_daily_series_buckets_by_et_day(self):
        # 2026-08-19T02:00Z is still 2026-08-18 in ET.
        kx = kalshi_positions({
            "fills": [_kx_fill("KXT-F", "yes", "buy", 100, 0.50)],
            "settlements": [_kx_settle("KXT-F", "yes", 100.0,
                                       when="2026-08-19T02:00:00Z")],
        })
        days = daily_series(kx)
        assert [d["day"] for d in days] == ["2026-08-18"]
        assert days[0]["realized"] == 50.0

    def test_totals_combine_both_venues(self):
        pm = pm_positions([_pm_trade("mlb-a", 100, 0.40),
                           _pm_resolution("mlb-a", 60.0, 40.0)])
        kx = kalshi_positions({
            "fills": [_kx_fill("KXT-A", "yes", "buy", 100, 0.40, fee=0.7)],
            "settlements": [_kx_settle("KXT-A", "no", 0.0)],
        })
        p = build_payload(pm, kx, "2026-08-10")
        assert p["total"]["settled"] == 2
        assert p["total"]["realized"] == round(60.0 + (-40.7), 2)
        assert p["polymarket_us"]["wins"] == 1
        assert p["kalshi"]["losses"] == 1
        assert p["partial"] is False

    def test_settlements_before_window_stay_frozen(self):
        kx = kalshi_positions({
            "fills": [_kx_fill("KXT-G", "yes", "buy", 100, 0.50)],
            "settlements": [_kx_settle("KXT-G", "yes", 100.0,
                                       when="2026-08-05T12:00:00Z")],
        })
        p = build_payload([], kx, "2026-08-10")
        assert p["total"]["settled"] == 0
        assert p["total"]["realized"] == 0.0

    def test_venue_error_marks_payload_partial(self):
        p = build_payload([], [], "2026-08-10",
                          pm_error="RateLimitError: slow down")
        assert p["partial"] is True
        assert p["polymarket_us"]["error"].startswith("RateLimitError")


class TestSnapshotAssembly:
    def test_cold_start_builds_in_background_then_serves(self, monkeypatch):
        import asyncio

        from sportsassets.api import venue_truth as vt

        monkeypatch.setattr(vt, "_snap",
                            {"ts": 0.0, "payload": None, "building": False})

        async def fake_kx():
            return ({"fills": [_kx_fill("KXT-A", "yes", "buy", 100, 0.40)],
                     "settlements": [_kx_settle("KXT-A", "yes", 100.0)]},
                    None)

        async def fake_acts(since_day, timeout=240):
            return [_pm_trade("mlb-a", 100, 0.40),
                    _pm_resolution("mlb-a", 60.0, 40.0)]

        from sportsassets.api import pmus_account
        monkeypatch.setattr(vt, "_kalshi_raw_from_heartbeat", fake_kx)
        monkeypatch.setattr(pmus_account, "_week_activities", fake_acts)

        async def no_db(*a, **k):
            return []
        monkeypatch.setattr(vt, "_persist_days", no_db)
        monkeypatch.setattr(vt, "_frozen_days", no_db)

        async def scenario():
            first = await vt.snapshot()
            assert first.get("building") is True
            # Let the background refresh task complete.
            for _ in range(50):
                await asyncio.sleep(0.01)
                if vt._snap["payload"] is not None:
                    break
            second = await vt.snapshot()
            return second

        out = asyncio.run(scenario())
        assert out.get("building") is None
        assert out["total"]["settled"] == 2
        assert out["total"]["realized"] == 120.0  # 60 PM + 60 KX
        assert out["partial"] is False

    def test_kalshi_missing_marks_partial_but_pm_still_serves(self,
                                                              monkeypatch):
        import asyncio

        from sportsassets.api import venue_truth as vt

        monkeypatch.setattr(vt, "_snap",
                            {"ts": 0.0, "payload": None, "building": False})

        async def fake_kx():
            return None, "no kalshi_export_raw in engine heartbeat"

        async def fake_acts(since_day, timeout=240):
            return [_pm_trade("mlb-a", 100, 0.40),
                    _pm_resolution("mlb-a", 60.0, 40.0)]

        from sportsassets.api import pmus_account
        monkeypatch.setattr(vt, "_kalshi_raw_from_heartbeat", fake_kx)
        monkeypatch.setattr(pmus_account, "_week_activities", fake_acts)

        async def no_db(*a, **k):
            return []
        monkeypatch.setattr(vt, "_persist_days", no_db)
        monkeypatch.setattr(vt, "_frozen_days", no_db)

        async def scenario():
            await vt.snapshot()
            for _ in range(50):
                await asyncio.sleep(0.01)
                if vt._snap["payload"] is not None:
                    break
            return await vt.snapshot()

        out = asyncio.run(scenario())
        assert out["partial"] is True
        assert "kalshi_export_raw" in out["kalshi"]["error"]
        assert out["polymarket_us"]["settled"] == 1


class TestDayLedgerPersistence:
    def test_persistable_rows_are_per_day_in_window_only(self):
        from sportsassets.api.venue_truth import persistable_day_rows

        kx = kalshi_positions({
            "fills": [
                _kx_fill("KXT-H", "yes", "buy", 100, 0.50),
                _kx_fill("KXT-I", "yes", "buy", 100, 0.40, fee=0.7),
            ],
            "settlements": [
                _kx_settle("KXT-H", "yes", 100.0,
                           when="2026-08-18T20:00:00Z"),
                # Before the window: must not be frozen by this build.
                _kx_settle("KXT-I", "no", 0.0,
                           when="2026-08-05T20:00:00Z"),
            ],
        })
        rows = persistable_day_rows(kx, "kalshi", "2026-08-10")
        assert rows == [("2026-08-18", "kalshi", 1, 1, 0, 50.0, 50.0)]

    def test_undated_and_incomplete_rows_never_freeze(self):
        from sportsassets.api.venue_truth import persistable_day_rows

        kx = kalshi_positions({
            "fills": [],
            # No fills in window: window_complete=False.
            "settlements": [_kx_settle("KXT-J", "yes", 200.0)],
        })
        assert persistable_day_rows(kx, "kalshi", "2026-08-10") == []
        pm = pm_positions([
            {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
             "positionResolution": {
                 "marketSlug": "undated-x",
                 "afterPosition": {"realized": {"value": 10.0}},
                 "beforePosition": {"cost": {"value": 5.0}}}},
        ])
        assert pm[0]["settled_at"] is None
        assert persistable_day_rows(pm, "polymarket-us", "2026-08-10") == []
