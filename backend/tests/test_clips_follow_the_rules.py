"""The clip a whale trades at is the rules' number, read from the database.

Owner order 2026-09-01 (evening): whales enter, are promoted and are
demoted by the data. roster_auto writes {whale: usd} to ingestion_state
under live_clip_overrides; the money path adopts it on the same refresh
as the stored roster. These tests pin the adoption:

  * a stored clip beats the hardcoded whale clip and the default
  * a stored 0 is a block, whatever the hardcode says
  * a hand-measured (whale, sport) cell can only TIGHTEN a stored clip
  * the map is read with the roster; a bad cell is dropped, a read
    failure keeps the last adopted map, a stored nothing clears it
  * a demoted whale (clip 0) stays EXITABLE -- his open copies can still
    be sold after he has left every other set
"""
import asyncio
import json

from sportsassets import live_executor as le


def _clips(monkeypatch, m):
    monkeypatch.setattr(le, "_clip_override", m)


def test_a_stored_clip_beats_the_hardcoded_whale_clip(monkeypatch):
    _clips(monkeypatch, {"rn1": 50.0})
    assert le.per_fill_usd("rn1") == 50.0
    _clips(monkeypatch, None)
    assert le.per_fill_usd("rn1") == min(le.PER_FILL_BY_WHALE["rn1"],
                                         le.LIVE_MAX_CLIP_USD)


def test_a_stored_clip_gives_a_newcomer_the_measurement_size(monkeypatch):
    _clips(monkeypatch, None)
    assert le.per_fill_usd("newbie") == min(le.PENNY_TRIAL_PER_FILL_USD,
                                            le.LIVE_MAX_CLIP_USD)
    _clips(monkeypatch, {"newbie": 50.0})
    assert le.per_fill_usd("newbie") == 50.0


def test_a_stored_zero_is_a_block(monkeypatch):
    _clips(monkeypatch, {"rn1": 0.0})
    assert le.per_fill_usd("rn1") == 0.0
    assert le.per_fill_usd("rn1", "aec-atp-a-b-2026-09-01") == 0.0


def test_a_hand_measured_sport_cell_can_only_tighten_a_stored_clip(monkeypatch):
    from sportsassets.copy_sports import sport_of

    slug = "aec-atp-a-b-2026-09-01"
    cell = (("rn1", sport_of(slug)))
    monkeypatch.setitem(le.PER_FILL_BY_WHALE_SPORT, cell, 100.0)
    # cell below the stored clip: the cell binds, exactly as it would
    # with no stored clip at all
    _clips(monkeypatch, {"rn1": 250.0})
    with_stored = le.per_fill_usd("rn1", slug)
    _clips(monkeypatch, None)
    assert with_stored == le.per_fill_usd("rn1", slug)
    # cell above the stored clip: the stored clip binds
    _clips(monkeypatch, {"rn1": 50.0})
    tightened = le.per_fill_usd("rn1", slug)
    monkeypatch.delitem(le.PER_FILL_BY_WHALE_SPORT, cell)
    assert tightened == le.per_fill_usd("rn1", slug)
    # a 0.00 cell stays a block over any stored clip
    monkeypatch.setitem(le.PER_FILL_BY_WHALE_SPORT, cell, 0.0)
    _clips(monkeypatch, {"rn1": 250.0})
    assert le.per_fill_usd("rn1", slug) == 0.0


class _Pool:
    def __init__(self, roster=None, clips=None, clips_raise=False):
        self.roster, self.clips, self.clips_raise = roster, clips, clips_raise

    async def fetchval(self, sql, key, timeout=None):
        if key == le._CLIPS_DB_KEY:
            if self.clips_raise:
                raise RuntimeError("db blip")
            return self.clips
        if key == le._ROSTER_DB_KEY:
            return self.roster
        return None


def _fresh(monkeypatch):
    monkeypatch.setattr(le, "_clip_override", None)
    monkeypatch.setattr(le, "_roster_override", None)
    monkeypatch.setattr(le, "_roster_read_at", 0.0)


def test_the_clips_are_read_with_the_roster_and_a_bad_cell_is_dropped(monkeypatch):
    _fresh(monkeypatch)
    asyncio.run(le.refresh_whale_overrides(_Pool(
        roster=json.dumps(["rn1"]),
        clips=json.dumps({"RN1": 50, "hrh": "not a number", "x": -5}))))
    assert le._whale_set("LIVE_VERIFIED_WHALES") == {"rn1"}
    assert le._clip_override == {"rn1": 50.0, "x": 0.0}


def test_a_clip_read_failure_keeps_the_last_adopted_map(monkeypatch):
    _fresh(monkeypatch)
    asyncio.run(le.refresh_whale_overrides(_Pool(clips=json.dumps({"rn1": 50}))))
    assert le._clip_override == {"rn1": 50.0}
    monkeypatch.setattr(le, "_roster_read_at", 0.0)
    asyncio.run(le.refresh_whale_overrides(_Pool(clips_raise=True)))
    assert le._clip_override == {"rn1": 50.0}


def test_a_stored_nothing_clears_the_clips(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setattr(le, "_clip_override", {"rn1": 50.0})
    asyncio.run(le.refresh_whale_overrides(_Pool(clips=None)))
    assert le._clip_override is None


def test_a_shape_that_is_not_a_map_keeps_the_last_adopted_map(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setattr(le, "_clip_override", {"rn1": 50.0})
    asyncio.run(le.refresh_whale_overrides(_Pool(clips=json.dumps(["rn1"]))))
    assert le._clip_override == {"rn1": 50.0}


def test_a_demoted_whale_stays_exitable(monkeypatch):
    _clips(monkeypatch, {"stranger-demoted": 0.0})
    assert "stranger-demoted" in le.exitable_whales()
    assert le.per_fill_usd("stranger-demoted") == 0.0
    _clips(monkeypatch, None)
    assert "stranger-demoted" not in le.exitable_whales()


def test_the_gates_page_shows_what_binds():
    import inspect

    from sportsassets.api import app as app_mod

    src = inspect.getsource(app_mod.api_gates)
    assert "per_fill_effective" in src and "clip_overrides" in src
    assert "roster_auto" in src
