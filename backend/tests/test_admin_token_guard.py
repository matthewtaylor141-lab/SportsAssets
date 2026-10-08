"""Wrong X-Admin-Token guesses are throttled; the right token never is.

2026-10-08: a read-only preview run showed the credential production accepts
as X-Admin-Token is two characters long, and nothing limited how many wrong
values a caller could try against require_admin / require_command. Rotation
is the owner's fix (reported as a blocker); api/admin_token_guard.py slows
guessing meanwhile without changing any credential."""
from __future__ import annotations

import pytest

from sportsassets.api import admin_token_guard as G

GOOD = "a-correct-token-of-reasonable-length-0123"


@pytest.fixture(autouse=True)
def _clean():
    G.reset()
    yield
    G.reset()


def _h(token, xff=None):
    h = {"x-admin-token": token}
    if xff is not None:
        h["x-forwarded-for"] = xff
    return h


def test_a_client_is_refused_after_its_budget_of_wrong_guesses():
    for i in range(G.PER_CLIENT_LIMIT):
        assert G.check(_h("w%d" % i), "1.2.3.4", GOOD, now=1000.0) is None
    assert G.check(_h("again"), "1.2.3.4", GOOD, now=1001.0) == G.R_THROTTLED


def test_the_correct_token_is_never_throttled_even_mid_attack():
    for i in range(G.GLOBAL_LIMIT + 5):
        G.check(_h("w%d" % i), "10.0.0.%d" % (i % 200), GOOD, now=1000.0)
    assert G.check(_h(GOOD), "10.0.0.1", GOOD, now=1001.0) is None
    # whitespace-tolerant like require_admin
    assert G.check(_h(GOOD + "\n"), "10.0.0.1", GOOD, now=1001.0) is None


def test_rotating_the_leftmost_forwarded_hop_does_not_reset_the_budget():
    """The rightmost X-Forwarded-For hop is the edge's; the leftmost is the
    caller's own claim."""
    for i in range(G.PER_CLIENT_LIMIT):
        G.check(_h("w%d" % i, xff="9.9.9.%d, 203.0.113.7" % i), "edge",
                GOOD, now=1000.0)
    assert G.check(_h("x", xff="8.8.8.8, 203.0.113.7"), "edge", GOOD,
                   now=1000.0) == G.R_THROTTLED
    assert G.client_key({"x-forwarded-for": "1.1.1.1, 2.2.2.2"}, "h") == \
        "2.2.2.2"
    assert G.client_key({}, "h") == "h"


def test_guesses_spread_over_many_addresses_hit_the_global_ceiling():
    for i in range(G.GLOBAL_LIMIT):
        assert G.check(_h("w%d" % i), "198.51.100.%d" % i, GOOD,
                       now=1000.0) is None
    assert G.check(_h("w"), "192.0.2.1", GOOD, now=1000.0) == G.R_THROTTLED


def test_the_budget_recovers_after_the_window():
    for i in range(G.PER_CLIENT_LIMIT):
        G.check(_h("w%d" % i), "1.2.3.4", GOOD, now=1000.0)
    assert G.check(_h("x"), "1.2.3.4", GOOD, now=1000.0) == G.R_THROTTLED
    assert G.check(_h("x"), "1.2.3.4", GOOD,
                   now=1000.0 + G.WINDOW_S + 1) is None


def test_no_header_or_an_unconfigured_key_is_not_counted_as_a_guess():
    assert G.check({}, "1.2.3.4", GOOD, now=1.0) is None
    assert G.check(_h("   "), "1.2.3.4", GOOD, now=1.0) is None
    assert G._GLOBAL == []
    # an unset key accepts nothing (require_admin refuses) and still counts
    assert G.token_ok("anything", "") is False


def test_a_short_signing_key_is_named_in_the_private_log_only(caplog):
    caplog.set_level("WARNING")
    assert G.warn_if_weak("Qz") is True
    assert G.warn_if_weak(GOOD) is False
    assert G.warn_if_weak("") is False
    text = caplog.text
    assert "below the 32-character minimum" in text
    assert "Qz" not in text and " 2 " not in text   # no value, no length


def test_the_app_refuses_a_throttled_guesser_before_any_route(monkeypatch):
    from fastapi.testclient import TestClient
    from sportsassets.api import app as app_mod

    class _S:
        admin_token = GOOD
        desk_password = "letmein"

    monkeypatch.setattr(app_mod, "settings", lambda: _S())
    client = TestClient(app_mod.app)
    for i in range(G.PER_CLIENT_LIMIT):
        r = client.get("/api/command/snapshot",
                       headers={"X-Admin-Token": "guess-%d" % i})
        assert r.status_code == 401
    r = client.get("/api/command/snapshot",
                   headers={"X-Admin-Token": "one-more"})
    assert r.status_code == 429
    assert r.json()["detail"] == G.R_THROTTLED
    # the holder of the real credential is still served (not 401 / 429)
    r = client.get("/api/system/seen-origins",
                   headers={"X-Admin-Token": GOOD})
    assert r.status_code == 200
