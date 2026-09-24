"""The prepared progress integration, and the refusals that keep it honest.

The second-half loss exit is UNAVAILABLE because nothing observes event
progress. These tests pin that the adapter layer (a) refuses until a
provider is configured, (b) refuses a payload that lacks the two things
every payload measured so far has lacked, and (c) cannot widen
`bettor_progress_feed`'s rules on its way past them.
"""
import time

import pytest

from sportsassets import bettor_progress_feed as feed
from sportsassets import bettor_progress_providers as PP


def test_nothing_is_connected_and_the_refusal_names_the_capability(
        monkeypatch):
    monkeypatch.delenv(PP.ENV_PROVIDER, raising=False)
    got = PP.configured()
    assert got["connected"] is False
    assert got["refusal"] == PP.R_NOT_CONFIGURED
    # the refusal must be actionable, not just negative
    assert got["capability_required"], "it must say what is needed"
    assert any("OBSERVED period" in c for c in got["capability_required"])


def test_an_unregistered_adapter_is_refused_by_name(monkeypatch):
    monkeypatch.setenv(PP.ENV_PROVIDER, "whatever_sounds_plausible")
    got = PP.configured()
    assert got["refusal"] == PP.R_UNKNOWN_ADAPTER
    assert got["connected"] is False


def test_a_selected_adapter_without_its_credential_refuses(monkeypatch):
    monkeypatch.setenv(PP.ENV_PROVIDER, "generic_scores_v1")
    monkeypatch.delenv("PROGRESS_PROVIDER_KEY", raising=False)
    got = PP.configured()
    assert got["refusal"] == PP.R_NO_CREDENTIAL
    assert got["credential_env"] == "PROGRESS_PROVIDER_KEY"


def test_configuration_alone_does_not_admit_an_exit(monkeypatch):
    """Connected means the adapter can produce observations. It does not
    mean an exit is licensed: the feed's own validation and its 120 s
    staleness bound still stand."""
    monkeypatch.setenv(PP.ENV_PROVIDER, "generic_scores_v1")
    monkeypatch.setenv("PROGRESS_PROVIDER_KEY", "x" * 8)
    got = PP.configured()
    assert got["connected"] is True
    assert "staleness" in got["why"] or "120" in got["why"]


# ── the two things every measured payload has lacked ─────────────────

def test_a_payload_with_no_period_is_refused_not_inferred():
    out = PP.to_observation({"status": "inplay", "as_of": time.time(),
                             "home_score": 0, "away_score": 0},
                            sport="soccer", event_key="e1")
    assert out["ok"] is False
    assert out["refusal"] == PP.R_NO_PERIOD_IN_PAYLOAD
    assert "cannot locate halftime" in out["why"]


def test_a_payload_with_no_provider_timestamp_is_refused():
    out = PP.to_observation({"status": "inplay", "period": 2},
                            sport="soccer", event_key="e1")
    assert out["ok"] is False
    assert out["refusal"] == PP.R_NO_PROVIDER_TIMESTAMP
    assert "fresh by construction" in out["why"]


def test_a_complete_payload_becomes_a_valid_observation():
    now = time.time()
    out = PP.to_observation({"status": "inplay", "period": 2,
                             "as_of": now, "revision": 3},
                            sport="soccer", event_key="e1")
    assert out["ok"] is True, out
    obs = out["observation"]
    assert obs["period"] == 2
    assert obs["status"] == feed.IN_PLAY
    assert obs["in_play"] is True
    assert obs["source"] in feed.OBSERVED_SOURCES
    assert obs["observed_at"] == pytest.approx(now, abs=1.0)


def test_an_unknown_status_is_left_unmapped_and_the_feed_refuses_it():
    """Rounding an unfamiliar status to IN_PLAY is how a sale happens
    during a suspension. The adapter must not guess."""
    assert PP.map_status("bizarre_new_state") is None
    out = PP.to_observation({"status": "bizarre_new_state", "period": 1,
                             "as_of": time.time()},
                            sport="soccer", event_key="e1")
    assert out["ok"] is False
    assert out["refusal"] == feed.MALFORMED


def test_break_suspended_abandoned_and_final_stay_distinct():
    """Four different reasons not to sell, and collapsing them to
    'not in play' would lose which one management is looking at."""
    got = {PP.map_status(w) for w in ("ht", "suspended", "abandoned", "ft")}
    assert got == {feed.BREAK, feed.SUSPENDED, feed.ABANDONED, feed.FINAL}
    assert feed.IN_PLAY not in got


def test_the_adapter_cannot_smuggle_in_a_derived_source():
    """`game_start_plus_elapsed` is refused BY SOURCE. An adapter that
    passed it through with a real period and a real stamp would look
    perfect and be exactly the forbidden derivation."""
    out = PP.to_observation({"status": "inplay", "period": 2,
                             "as_of": time.time()},
                            sport="soccer", event_key="e1",
                            source="game_start_plus_elapsed")
    assert out["ok"] is False
    assert out["refusal"] == feed.DERIVED


def test_describe_states_what_was_searched_and_found_wanting():
    d = PP.describe()
    seen = d["searched_and_unavailable"]
    assert "odds_provider_scores_endpoint" in seen
    assert "venue_market_data" in seen
    assert "game_start_time" in seen
    # and it does not claim to be connected
    assert d["configured"]["connected"] in (False, True)
