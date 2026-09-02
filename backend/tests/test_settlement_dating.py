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
