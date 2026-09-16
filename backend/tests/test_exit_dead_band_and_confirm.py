"""Two exit-lane blind spots, measured in production and closed here.

Owner order 2026-08-26: "if we buy one of their positions, and they sell
that position, we need to do the exact same behavior." Production beat:
exits_sold 0, lifetime. The adversarial workflow traced the zero to two
structural gaps, both of the instrument-cannot-see-its-subject family:

  * TRUNCATED BOOKS. Whales whose venue books exceed POSITIONS_MAX read
    as partial every cycle, and the partial branch excluded EVERY
    vanished asset -- so their FULL exits (the largest dollar class the
    lane exists for) were skipped forever. vanished_live:89 against
    exit_attempts:3, and the venue ledger census shows RN1 at 10k+
    position rows against a 6,000 cap. Now: a vanished asset WE HOLD
    can be positively confirmed gone via a filtered per-market read.
  * THE SUB-MIN_SHRINK DEAD BAND. diff_exits drops a <5% shrink as
    noise -- but the snapshot then advanced anyway, so a whale trimming
    3%/cycle was re-measured against a fresh baseline forever and never
    accumulated. Now the baseline PINS until the cumulative fraction
    crosses MIN_SHRINK. No floor moved.
"""

from __future__ import annotations

import asyncio

import pytest

from sportsassets.workers import whale_exits as we


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else []

    def json(self):
        return self._body


class _Http:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, params))
        return self.resp


class _Pool:
    def __init__(self, cid="0xcond"):
        self.cid = cid

    async def fetchval(self, sql, *a):
        return self.cid


def _row(asset="tok-A", cid="0xcond", size=0.0):
    return {"asset": asset, "conditionId": cid, "size": size}


class TestConfirmGoneFailsClosed:
    def _run(self, http, pool=None):
        return asyncio.run(we._confirm_gone(
            http, pool or _Pool(), "0xwhale", "tok-A"))

    def test_confirmed_gone_when_market_answers_without_the_leg(self):
        http = _Http(_Resp(body=[_row(asset="tok-B")]))
        assert self._run(http) is True
        # and the read was filtered + dust-inclusive
        _, params = http.calls[0]
        assert params["market"] == "0xcond"
        assert params["sizeThreshold"] == 0

    def test_still_held_is_not_gone(self):
        http = _Http(_Resp(body=[_row(asset="tok-A", size=250.0)]))
        assert self._run(http) is False

    def test_zero_size_row_is_gone(self):
        http = _Http(_Resp(body=[_row(asset="tok-A", size=0.0)]))
        assert self._run(http) is True

    def test_an_empty_response_is_refused(self):
        """A roster whale with no positions AT ALL is not a plausible
        read -- and it is exactly what an ignored filter plus an empty
        page would look like. Unknown is not gone."""
        http = _Http(_Resp(body=[]))
        assert self._run(http) is False

    def test_an_unfiltered_response_is_refused(self):
        """THE trap this helper exists to avoid. If the venue ignored
        the `market` parameter it would return the unfiltered first
        page; reading tok-A's absence from THAT as proof of exit fires
        a 100% sell on a position the whale still holds. Any row from a
        different market means the filter was not honoured."""
        http = _Http(_Resp(body=[_row(asset="tok-Z", cid="0xother")]))
        assert self._run(http) is False

    def test_no_condition_id_is_refused(self):
        class NoCid(_Pool):
            async def fetchval(self, sql, *a):
                return None

        http = _Http(_Resp(body=[_row(asset="tok-B")]))
        assert asyncio.run(we._confirm_gone(
            http, NoCid(), "0xwhale", "tok-A")) is False

    def test_http_error_is_refused(self):
        http = _Http(_Resp(status=500))
        assert self._run(http) is False

    def test_an_exception_is_refused(self):
        class Boom:
            async def get(self, *a, **k):
                raise RuntimeError("venue down")

        assert asyncio.run(we._confirm_gone(
            Boom(), _Pool(), "0xwhale", "tok-A")) is False


class TestTheDeadBandPins:
    def _to_save(self, prev, now, partial=False):
        """The exact pin arithmetic from _cycle's save block, run on
        real inputs. Kept in lockstep by the source assertion below."""
        to_save = dict(prev) if partial else {}
        to_save.update(now)
        for asset, before in prev.items():
            after = now.get(asset)
            if after is None or before <= 0 or after >= before:
                continue
            if (before - after) / before < we.MIN_SHRINK:
                to_save[asset] = before
        return to_save

    def test_the_source_matches_the_simulation(self):
        import inspect

        src = inspect.getsource(we._cycle)
        for line in ("if after is None or before <= 0 or after >= before:",
                     "if (before - after) / before < MIN_SHRINK:",
                     "to_save[asset] = before"):
            assert line in src, f"pin arithmetic drifted: {line!r}"

    def test_a_sub_threshold_trim_keeps_the_old_baseline(self):
        assert self._to_save({"a": 100.0}, {"a": 97.0}) == {"a": 100.0}

    def test_a_trim_at_the_threshold_advances(self):
        """>= MIN_SHRINK enters the diff instead; the pin must not
        shadow it or the same exit fires twice from a stale base."""
        assert self._to_save({"a": 100.0}, {"a": 95.0}) == {"a": 95.0}

    def test_the_cumulative_walk_crosses_in_the_diff(self):
        """The point of the pin: 3%/cycle, invisible per-observation,
        crosses MIN_SHRINK against the pinned baseline within two
        cycles and enters diff_exits with the CUMULATIVE fraction."""
        prev = {"a": 100.0}
        s1 = self._to_save(prev, {"a": 97.0})          # pinned at 100
        assert s1 == {"a": 100.0}
        assert we.diff_exits(s1, {"a": 94.0}, set()) == [("a", 0.06)]

    def test_a_readd_clears_the_pin(self):
        """Recovery at or above the baseline is the un-pin: noise must
        not accumulate into a false exit."""
        assert self._to_save({"a": 100.0}, {"a": 101.0}) == {"a": 101.0}
        assert self._to_save({"a": 100.0}, {"a": 100.0}) == {"a": 100.0}

    def test_growth_and_new_assets_are_untouched(self):
        assert self._to_save({}, {"b": 50.0}) == {"b": 50.0}
        assert self._to_save({"a": 100.0}, {"a": 100.0, "b": 5.0}) == {
            "a": 100.0, "b": 5.0}
