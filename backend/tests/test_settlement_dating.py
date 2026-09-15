"""93.6% of the book was date-stamped with the day we READ it.

A POSITION_RESOLUTION carries its timestamp NESTED under
positionResolution, not at the top level. The archive writer used
_act_ts, which reads only the top level, so every resolution archived
got ts = 0.0 — and the settlement writer treats zero as "unknown":

    settled_at = COALESCE($3, settled_at, now())

The analytics crawl reaches ~79 of 1,229 markets live, so the archive
supplies up to 93.6% of settlements. Against a -$13,398.96 book that is
roughly -$12,542 landing on a calendar day that did not lose it.

This is a REPORTING defect, not a trading one — no order changes — and
that makes it worse rather than better. The owner's standing rule is
that a displayed number must be what it claims to be, and a daily P&L
calendar that attributes a fortnight of losses to one box is not.

_any_ts already existed and already read the nested time. It was never
wired to the archive.
"""

import inspect

from sportsassets.api import track_record as tr
from sportsassets.api.pmus_account import _act_ts, _any_ts

RESOLUTION = {"id": "r1", "type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
              "positionResolution": {"marketSlug": "aec-x",
                                     "createTime": "2026-08-20T10:00:00Z"}}
TRADE = {"id": "t1", "type": "ACTIVITY_TYPE_TRADE",
         "trade": {"marketSlug": "aec-y",
                   "createTime": "2026-08-19T09:00:00Z"}}


class TestTheNestedTimestampIsFound:
    def test_the_old_reader_returns_zero_on_a_resolution(self):
        """The bug, pinned. Not a hypothetical."""
        assert _act_ts(RESOLUTION) == 0.0

    def test_the_new_reader_finds_it(self):
        assert _any_ts(RESOLUTION) > 0

    def test_it_finds_a_nested_trade_time_too(self):
        assert _act_ts(TRADE) == 0.0
        assert _any_ts(TRADE) > 0

    def test_a_top_level_timestamp_still_works(self):
        assert _any_ts({"createTime": "2026-08-20T10:00:00Z"}) > 0

    def test_a_genuinely_timeless_row_is_still_zero(self):
        """Unknown must stay unknown — inventing a time would be the
        same lie in the other direction."""
        assert _any_ts({}) == 0.0
        assert _any_ts({"positionResolution": {}}) == 0.0


class TestTheArchiveUsesIt:
    def test_the_writer_reads_the_nested_time(self):
        src = inspect.getsource(tr)
        assert "float(_any_ts(a) or 0.0)" in src
        assert "float(_act_ts(a) or 0.0)" not in src

    def test_the_reason_is_recorded_beside_it(self):
        src = inspect.getsource(tr)
        assert "the day it resolved" in src
        assert "93.6%" in src


class TestTheFallbackStillMeansUnknown:
    def test_a_zero_timestamp_still_falls_through_to_now(self):
        """The COALESCE is correct and stays. A settlement whose time is
        genuinely unrecoverable has to land somewhere, and 'now' is the
        honest choice — the defect was that 93.6% of rows reached it
        when their time was sitting one level down in the payload."""
        from sportsassets.analytics import engine

        src = inspect.getsource(engine)
        assert "settled_at = COALESCE($3, settled_at, now())" in src

    def test_the_settle_path_prefers_the_archived_time(self):
        from sportsassets.analytics import engine

        src = inspect.getsource(engine)
        assert 'if t.get("ts"):' in src


class TestTheRecordBuilderUsesItToo:
    """2026-09-02: the RECORD builder (track_record.build) still read the
    resolution time with _act_ts, so settled_ts was 0 for every
    resolution whose time sits nested under positionResolution, fell
    through to entry_ts, and the day P&L filed each long on the day it
    was BOUGHT. The daily reconciliation against the ledger (which dates
    by the venue's resolution time) then showed a residual on money that
    tied to the cent per market."""

    def test_a_nested_resolution_time_dates_the_settlement(self):
        from tests.test_track_record import TS_AUG2, TS_AUG1, _pos, _trade

        ts_aug3 = TS_AUG2 + 86_400
        nested = {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
                  "positionResolution": {
                      "marketSlug": "s",
                      "createTime": "2026-08-03T16:00:00Z",       # noon ET, nested only
                      "afterPosition": {"realized": {"value": 0.4}},
                      "beforePosition": {"cost": {"value": 5.0}}}}
        positions = {"s": _pos(0, 0.0, 0.0, realized=0.4, expired=True)}
        out = tr.build(positions, [_trade("s", TS_AUG2, 10, 0.50), nested], TS_AUG1)
        r = out["trades"][0]
        assert r["settled"] and r["settled_ts"] == ts_aug3
        daily = out["daily"]
        by_day = {d["date"]: d for d in (daily.values() if isinstance(daily, dict) else daily)}
        assert by_day["2026-08-03"]["pnl"] == 0.4 and by_day["2026-08-03"]["settled"] == 1
        assert by_day["2026-08-02"]["settled"] == 0, "bought on the 2nd, resolved on the 3rd"

    def test_both_resolution_readers_use_the_nested_reader(self):
        src = inspect.getsource(tr)
        assert '"ts": _any_ts(act),' in src
        assert '"ts": _act_ts(act),' not in src
        from sportsassets.api import pmus_account as pa
        src2 = inspect.getsource(pa)
        assert "when = _any_ts(act)" in src2 and "when = _act_ts(act)" not in src2


class TestASettlementIsDatedWithoutItsEntry:
    """2026-09-02 (owner order "go for it, let's get this working"): the
    record dated a settlement ONLY through its entry trade. The venue's
    activity window is ~8 h deep and the archive is frozen per process,
    so every settlement whose entry had scrolled out was dropped as
    'undatable' (577 -> 544 across one afternoon) and the record read
    "settled 6, pnl -39.59" for four hours while the venue resolved
    markets all afternoon. The resolution carries its own time, realized
    and cost: it dates the row by itself."""

    @staticmethod
    def _nested_resolution(slug, iso, realized, cost):
        return {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
                "positionResolution": {
                    "marketSlug": slug,
                    "createTime": iso,                   # nested only
                    "afterPosition": {"realized": {"value": realized},
                                      "marketMetadata": {"title": "Late"}},
                    "beforePosition": {"cost": {"value": cost}}}}

    def test_a_resolution_with_no_entry_trade_settles_on_its_own_day(self):
        from tests.test_track_record import TS_AUG1

        res = self._nested_resolution("s", "2026-08-03T16:00:00Z", 0.4, 5.0)
        out = tr.build({}, [res], TS_AUG1)      # no trade activity at all
        assert out["excluded_undatable"] == 0
        assert len(out["trades"]) == 1
        r = out["trades"][0]
        assert r["settled"] and r["pnl"] == 0.4 and r["stake"] == 5.0
        assert r["entry_ts"] is None and r["entry_date"] is None
        assert r["settled_ts"] == _any_ts(res)
        assert r["title"] == "Late"
        by_day = {d["date"]: d for d in out["daily"]}
        assert by_day["2026-08-03"]["settled"] == 1
        assert by_day["2026-08-03"]["pnl"] == 0.4
        assert by_day["2026-08-03"]["pnl_estimated"] is False
        assert out["summary"]["settled"] == 1 and out["summary"]["wins"] == 1

    def test_a_position_row_with_no_entry_is_dated_by_its_resolution(self):
        """The position payload still carries the row (expired, realized)
        but the entry scrolled out: same outcome, dated on resolution day."""
        from tests.test_track_record import TS_AUG1, _pos

        res = self._nested_resolution("s", "2026-08-03T16:00:00Z", -1.0, 2.0)
        positions = {"s": _pos(0, 2.0, 0.0, realized=-1.0, expired=True)}
        out = tr.build(positions, [res], TS_AUG1)
        assert out["excluded_undatable"] == 0
        r = out["trades"][0]
        assert r["settled"] and r["pnl"] == -1.0 and r["stake"] == 2.0
        assert r["entry_ts"] is None
        by_day = {d["date"]: d for d in out["daily"]}
        assert by_day["2026-08-03"]["settled"] == 1
        assert out["summary"]["losses"] == 1

    def test_a_short_is_dated_by_its_resolution(self):
        """A venue SELL-to-open is routed to the sold ledger, never to
        entries, so a short could never be dated even with a perfect
        archive. Followed by its resolution it is dated on that day."""
        from tests.test_track_record import TS_AUG1, TS_AUG2, TestSoldLedgerClassification

        opening_sell = TestSoldLedgerClassification._deep_trade(
            "sh", TS_AUG2, 10, 0.40, "ORDER_SIDE_SELL", rp=0.0)
        res = self._nested_resolution("sh", "2026-08-03T16:00:00Z", 4.0, 6.0)
        out = tr.build({}, [opening_sell, res], TS_AUG1)
        assert out["excluded_undatable"] == 0
        r = out["trades"][0]
        assert r["settled"] and r["pnl"] == 4.0 and r["stake"] == 6.0
        assert r["entry_ts"] is None
        assert r["settled_ts"] == _any_ts(res)
        by_day = {d["date"]: d for d in out["daily"]}
        assert by_day["2026-08-03"]["settled"] == 1
        assert "2026-08-02" not in by_day, "the opening sell is not a day"

    def test_a_row_with_neither_time_is_still_undatable(self):
        """Unknown stays unknown: a resolution with no time anywhere and
        no entry cannot be dated, and is counted rather than guessed."""
        from tests.test_track_record import TS_AUG1, _pos

        timeless = {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
                    "positionResolution": {
                        "marketSlug": "t",
                        "afterPosition": {"realized": {"value": 1.0}},
                        "beforePosition": {"cost": {"value": 1.0}}}}
        out = tr.build({}, [timeless], TS_AUG1)
        assert out["trades"] == [] and out["excluded_undatable"] == 1
        # ...and a bare position row with no trade and no resolution.
        out = tr.build({"u": _pos(2, 1.0, 1.1)}, [], TS_AUG1)
        assert out["trades"] == [] and out["excluded_undatable"] == 1

    def test_the_slimmed_twin_is_dated_exactly_like_the_raw_row(self):
        """The reviewer's repro (2026-09-02). Everything build() receives
        in production is slimmed — every refresh unions through _slim and
        the request path takes the archive twin over the raw window row
        — and the old _slim kept only the TOP-LEVEL time, so the nested
        resolution time the venue actually sends was gone before build()
        ever saw it: raw excluded_undatable 0, slimmed twin 1. The dating
        path never fired live."""
        from tests.test_track_record import TS_AUG1

        raw = self._nested_resolution("s", "2026-08-03T16:00:00Z", 0.4, 5.0)
        raw["id"] = "r-slim"
        slim = tr._slim(raw)
        assert "createTime" not in slim["positionResolution"]
        assert slim["timestamp"] == _any_ts(raw)
        a = tr.build({}, [raw], 1.0)
        b = tr.build({}, [slim], 1.0)
        assert a["excluded_undatable"] == 0
        assert b["excluded_undatable"] == 0
        for k in ("trades", "summary", "daily", "venue_totals"):
            assert a[k] == b[k]
        assert b["trades"][0]["settled_ts"] == _any_ts(raw)
        # Through the position path as well.
        pos = {"s": {"netPosition": 0, "cost": 5.0, "cashValue": 0.0,
                     "realized": 0.4, "expired": True,
                     "marketMetadata": {"title": "Late", "outcome": "Yes"}}}
        assert (tr.build(pos, [raw], TS_AUG1)["excluded_undatable"]
                == tr.build(pos, [slim], TS_AUG1)["excluded_undatable"] == 0)

    def test_a_money_less_resolution_with_no_entry_is_not_a_push(self):
        """A resolution carrying no cost and no realized, with no entry
        trade, would be kept as a settled push at stake 0: it says
        nothing about the strategy and dilutes win_rate (adversarial
        review 2026-09-02). Undatable-equivalent: excluded and counted,
        through BOTH the resolution-only path and the position path."""
        from tests.test_track_record import TS_AUG1, TS_AUG2, _pos, _trade

        bare = self._nested_resolution("z", "2026-08-03T16:00:00Z", 0.0, 0.0)
        out = tr.build({}, [bare], TS_AUG1)
        assert out["trades"] == [] and out["excluded_undatable"] == 1
        assert out["summary"]["settled"] == 0
        assert out["summary"]["win_rate"] is None
        # Position path: the venue still carries the row, also money-less.
        out = tr.build({"z": _pos(0, 0.0, 0.0, realized=0.0, expired=True)},
                       [bare], TS_AUG1)
        assert out["trades"] == [] and out["excluded_undatable"] == 1
        # The position row's own money rescues it: cost known, kept.
        out = tr.build({"z": _pos(0, 2.0, 0.0, realized=0.0, expired=True)},
                       [bare], TS_AUG1)
        assert out["excluded_undatable"] == 0
        assert out["trades"][0]["stake"] == 2.0 and out["trades"][0]["pnl"] == 0.0
        # Rows WITH an entry are untouched: the same money-less
        # resolution behind an entry trade is still the push it was.
        out = tr.build({}, [_trade("z", TS_AUG2, 2, 0.5), bare], TS_AUG1)
        assert out["excluded_undatable"] == 0
        r = out["trades"][0]
        assert r["settled"] and r["pnl"] == 0.0 and r["stake"] == 1.0
        assert out["summary"]["settled"] == 1 and out["summary"]["win_rate"] == 0.0
