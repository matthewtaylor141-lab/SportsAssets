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
