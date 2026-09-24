"""The progress layer: freshness, corrections, suspensions, overtime.

WHAT THESE TESTS DO NOT CLAIM. Not one of them shows a connected feed.
`PROGRESS_FEED_CONNECTED` is empty and a test here asserts it, so no sport
is admitted to the complete-policy experiment. These prove the layer a
provider plugs into behaves correctly when it is fed -- including that it
REFUSES the one input that would look exactly like real data.
"""

from __future__ import annotations

import os

import pytest

from sportsassets import bettor_progress_feed as pf
from sportsassets import bettor_rn1x_policy as pol

NOW = 1_790_000_000.0


def _obs(**kw):
    base = {"event_key": "EVT", "sport": "soccer",
            "source": "licensed_scores_feed", "observed_at": NOW - 10,
            "status": pf.IN_PLAY, "period": 2, "period_type": "HALF"}
    base.update(kw)
    return base


# ── provenance: the refusal that matters most ───────────────────────

@pytest.mark.parametrize("src", pf.DERIVED_SOURCES)
def test_a_derived_source_is_refused_by_name_not_by_value(src):
    """The dangerous input is not a missing source but a plausible one.

    `game_start + elapsed` produces a period index that is correctly
    typed, correctly timestamped and wrong whenever there is injury time,
    a stoppage or a long halftime. Every field here is valid; only the
    source is not, and that alone must refuse it.
    """
    v = pf.validate(_obs(source=src))
    assert v["ok"] is False
    assert v["refusal"] == pf.DERIVED, v
    assert v["normalized"] is None


def test_an_observed_source_with_the_same_fields_is_accepted():
    """The mirror image, so the test above cannot pass for the wrong
    reason -- e.g. by every observation being refused."""
    v = pf.validate(_obs(source="licensed_scores_feed"))
    assert v["ok"] is True, v
    assert v["normalized"]["period"] == 2


def test_in_play_is_derived_from_status_and_cannot_disagree():
    """A feed saying SUSPENDED with in_play=True states one fact twice and
    gets it wrong once. The caller's boolean is ignored."""
    v = pf.validate(_obs(status=pf.SUSPENDED, in_play=True))
    assert v["ok"] is True, v
    assert v["normalized"]["in_play"] is False


# ── freshness ───────────────────────────────────────────────────────

def test_a_stale_observation_does_not_license_an_exit():
    u = pf.usable([_obs(observed_at=NOW - 600)], now=NOW, event_key="EVT")
    assert u["ok"] is False
    assert u["refusal"] == pf.STALE, u
    assert u["progress"] is None
    assert u["age_s"] == pytest.approx(600.0)


def test_a_fresh_observation_yields_exactly_the_contract_fields():
    """No elapsed field may reach a rule -- not even as an extra key."""
    u = pf.usable([_obs()], now=NOW, event_key="EVT")
    assert u["ok"] is True, u
    assert set(u["progress"]) == set(pol.PROGRESS_FIELDS), u["progress"]
    assert "elapsed" not in u["progress"]
    assert "elapsed_s" not in u["progress"]


def test_an_observation_from_the_future_is_not_freshness():
    u = pf.usable([_obs(observed_at=NOW + 90)], now=NOW, event_key="EVT")
    assert u["ok"] is False
    assert u["refusal"] == pf.MALFORMED, u


# ── corrections ─────────────────────────────────────────────────────

def test_the_newest_observation_wins():
    rows = [_obs(observed_at=NOW - 90, period=1),
            _obs(observed_at=NOW - 10, period=2)]
    assert pf.latest(rows, event_key="EVT")["period"] == 2


def test_a_correction_restating_the_same_instant_wins_on_revision():
    """A feed that corrects itself keeps the ORIGINAL observed_at, so
    ordering by observed_at alone is a coin toss between the wrong value
    and the right one. Revision breaks the tie, and the superseded row
    stays readable."""
    rows = [_obs(observed_at=NOW - 10, period=1, revision=0),
            _obs(observed_at=NOW - 10, period=3, revision=1)]
    got = pf.latest(rows, event_key="EVT")
    assert got["period"] == 3, got
    # reversing the input order must not change the answer
    assert pf.latest(list(reversed(rows)), event_key="EVT")["period"] == 3


def test_usable_ALSO_refuses_a_derived_source_not_only_validate():
    """The hole a failing test exposed. `usable` used to order raw rows
    directly, so a wall_clock row handed to it skipped the provenance
    refusal -- and `in_play` was never derived from `status`, which made a
    perfectly good in-play row read as not-in-play. The table's CHECK
    would have caught the first on the way in, but a reader that is only
    safe because of the table it happened to read from is not safe.
    """
    u = pf.usable([_obs(source="wall_clock")], now=NOW, event_key="EVT")
    assert u["ok"] is False
    assert u["refusal"] == pf.DERIVED, u
    assert pf.DERIVED in u["refused_on_ingest"], u


def test_a_derived_row_cannot_outrank_a_good_one():
    """Newest-wins must not hand the decision to a refused row."""
    u = pf.usable([_obs(observed_at=NOW - 30, period=2),
                   _obs(observed_at=NOW - 1, period=9,
                        source="game_start_plus_elapsed")],
                  now=NOW, event_key="EVT")
    assert u["ok"] is True, u
    assert u["progress"]["period"] == 2, u
    assert pf.DERIVED in u["refused_on_ingest"], u


def test_another_events_observation_is_never_read():
    rows = [_obs(event_key="OTHER", period=4)]
    assert pf.latest(rows, event_key="EVT") is None
    u = pf.usable(rows, now=NOW, event_key="EVT")
    assert u["refusal"] == "PROGRESS_OBSERVATION_MISSING_NOW"


# ── suspensions, kept distinct ──────────────────────────────────────

@pytest.mark.parametrize("status", [pf.BREAK, pf.SUSPENDED,
                                    pf.ABANDONED, pf.FINAL])
def test_no_non_play_status_licenses_an_exit_and_each_is_named(status):
    u = pf.usable([_obs(status=status)], now=NOW, event_key="EVT")
    assert u["ok"] is False
    assert u["refusal"] == pf.NOT_IN_PLAY, u
    # the SPECIFIC status survives into the output: halftime and abandoned
    # need different handling even though both stop the clock
    assert u["status"] == status, u


# ── overtime, through the frozen rule ───────────────────────────────

@pytest.mark.parametrize("sport,total,over", [("soccer", 2, 3),
                                              ("basketball", 4, 5),
                                              ("football", 4, 6)])
def test_overtime_is_past_halfway_for_every_mapped_sport(sport, total, over):
    rule = pol.DOCUMENTED_MAPPINGS[sport]
    assert rule.total_periods == total
    assert rule({"period": over}) == pol.SECOND_HALF
    assert rule({"period": 1}) == pol.FIRST_HALF


def test_hockey_has_no_rule_because_three_periods_has_no_midpoint():
    assert pol.DOCUMENTED_MAPPINGS["hockey"] is None


# ── the policy's own freshness gate ─────────────────────────────────

def test_event_phase_refuses_a_stale_observation_for_an_admitted_sport(
        monkeypatch):
    """Admission is forced here ONLY to exercise the gate; production has
    no admitted sport and another test asserts that."""
    rule = pol.DOCUMENTED_MAPPINGS["soccer"]
    monkeypatch.setattr(pol, "SECOND_HALF_MAPPING", {"soccer": rule})
    monkeypatch.setattr(pol, "PROGRESS_FEED_CONNECTED", {"soccer": "test"})

    fresh = pol.event_phase(sport="soccer", now=NOW,
                            progress={"observed_at": NOW - 5, "period": 2,
                                      "period_type": "HALF",
                                      "in_play": True})
    assert fresh["phase"] == pol.SECOND_HALF, fresh
    assert fresh["loss_exit_available"] is True

    stale = pol.event_phase(sport="soccer", now=NOW,
                            progress={"observed_at": NOW - 601, "period": 2,
                                      "period_type": "HALF",
                                      "in_play": True})
    assert stale["loss_exit_available"] is False, stale
    assert stale["absence"] == "PROGRESS_OBSERVATION_STALE", stale
    assert stale["age_s"] == pytest.approx(601.0)


def test_event_phase_says_so_when_no_now_was_supplied(monkeypatch):
    """Silence about freshness would be the worst option: the reading
    happens, and nothing tells the reader it was not age-gated."""
    rule = pol.DOCUMENTED_MAPPINGS["soccer"]
    monkeypatch.setattr(pol, "SECOND_HALF_MAPPING", {"soccer": rule})
    monkeypatch.setattr(pol, "PROGRESS_FEED_CONNECTED", {"soccer": "test"})
    out = pol.event_phase(sport="soccer",
                          progress={"observed_at": 0, "period": 2,
                                    "period_type": "HALF", "in_play": True})
    assert out["freshness_checked"] is False, out
    assert "caller owns freshness" in out["freshness_note"]


# ── the restriction itself ──────────────────────────────────────────

def test_production_admits_NO_sport_to_the_complete_policy_experiment():
    """The point of the whole exercise. Building the layer must not have
    quietly admitted anything."""
    assert pol.PROGRESS_FEED_CONNECTED == {}, pol.PROGRESS_FEED_CONNECTED
    assert pol.SECOND_HALF_MAPPING == {}, pol.SECOND_HALF_MAPPING
    for sport in ("soccer", "basketball", "football", "hockey", "cricket"):
        out = pol.event_phase(sport=sport, now=NOW)
        assert out["admitted_to_complete_policy"] is False, (sport, out)
        assert out["loss_exit_available"] is False, (sport, out)
    assert pf.describe()["connected_providers"] == []


# ── the trigger is not a maximum ────────────────────────────────────

def test_the_realised_loss_is_reported_apart_from_the_trigger():
    """The measured case: 0.57 basis, 100 contracts, executed 0.47."""
    got = pol.realised_vs_trigger(allocated_cost_usd=57.00,
                                  realised_net_usd=-11.73)
    assert got["trigger_loss_fraction"] == pytest.approx(0.16, abs=1e-9)
    assert got["realised_loss_fraction"] == pytest.approx(0.2058, abs=5e-5)
    assert got["worse_than_trigger_by"] == pytest.approx(0.0458, abs=5e-5)
    assert got["trigger_is_a_level_not_a_maximum"] is True
    assert "WORSE" in got["why"]


def test_an_unexecuted_position_has_no_realised_fraction():
    got = pol.realised_vs_trigger(allocated_cost_usd=57.00,
                                  realised_net_usd=None)
    assert got["status"] == pol.NOT_IDENTIFIED
    assert got["realised_loss_fraction"] is None
    assert got["worse_than_trigger_by"] is None


# ── persistence, against the real CHECK constraints ─────────────────

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")


@pg
@pytest.mark.asyncio
async def test_the_table_itself_refuses_what_the_module_refuses():
    """The module's rules must also be the SCHEMA's rules. A validator is
    bypassable by any other writer; a CHECK is not."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            open("migrations/101_rn1x_event_progress.sql").read())
        await conn.execute("DELETE FROM rn1x_event_progress "
                          "WHERE event_key = 'EVT_PG'")

        # a derived source cannot be inserted AT ALL
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO rn1x_event_progress (event_key, sport, source, "
                "observed_at, status, in_play) VALUES ('EVT_PG','soccer',"
                "'game_start_plus_elapsed', now(), 'IN_PLAY', true)")

        # in_play may not disagree with status
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO rn1x_event_progress (event_key, sport, source, "
                "observed_at, status, in_play) VALUES ('EVT_PG','soccer',"
                "'licensed_scores_feed', now(), 'SUSPENDED', true)")

        # period 0 is not a period
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO rn1x_event_progress (event_key, sport, source, "
                "observed_at, status, period, in_play) VALUES ('EVT_PG',"
                "'soccer','licensed_scores_feed', now(), 'IN_PLAY', 0, true)")

        # THE COLUMN THAT MUST NOT EXIST
        assert await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'rn1x_event_progress' "
            "AND column_name IN ('elapsed','elapsed_s','clock')") == 0

        # and a correction round-trips through record/read_usable
        import time
        now = time.time()
        for rev, period in ((0, 1), (1, 3)):
            r = await pf.record(conn, _obs(
                event_key="EVT_PG", observed_at=now - 5, revision=rev,
                period=period))
            assert r["ok"] is True, r
        got = await pf.read_usable(conn, "EVT_PG", now=now)
        assert got["ok"] is True, got
        assert got["progress"]["period"] == 3, got
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_event_progress "
            "WHERE event_key = 'EVT_PG'") == 2, "the superseded row is kept"
    finally:
        await conn.execute("DELETE FROM rn1x_event_progress "
                           "WHERE event_key = 'EVT_PG'")
        await conn.close()
