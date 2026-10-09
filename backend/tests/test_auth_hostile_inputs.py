"""Malformed credentials refuse normally and throttled traffic stays bounded."""
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import time
import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from sportsassets.api import admin_token_guard as G
from sportsassets.api import app as A

GOOD = "a-correct-test-key-of-at-least-32-characters"


@pytest.fixture(autouse=True)
def isolated_auth(monkeypatch):
    G.reset()
    A._UNLOCK_HITS.clear()
    A._PING_HITS.clear()
    monkeypatch.setattr(A, "settings", lambda: SimpleNamespace(
        admin_token=GOOD, desk_password="test-password",
        operator_password="test-operator-password", engine_ingest_token=GOOD))
    yield
    G.reset()
    A._UNLOCK_HITS.clear()
    A._PING_HITS.clear()


@pytest.mark.parametrize("route", ["/healthz", "/api/command/snapshot",
                                   "/api/admin/latency"])
def test_non_ascii_admin_header_cannot_crash_a_route(route):
    with_header = [(b"x-admin-token", b"wrong-\xe9")]
    response = TestClient(A.app).get(route, headers=with_header)
    assert response.status_code == (200 if route == "/healthz" else 401)
    assert len(G._GLOBAL) == 1


@pytest.mark.parametrize("gate", [
    lambda value: A.require_admin(value),
    lambda value: A.require_desk(x_admin_token=value, x_desk_token=""),
    lambda value: A.require_command(bt_command="", x_desk_token="",
                                    x_admin_token=value),
    lambda value: A.require_command_control(x_admin_token=value,
        x_desk_token="", bt_control="", bt_command=""),
    lambda value: A.check_engine_token(value),
])
def test_wrong_unicode_credentials_are_unauthorized_not_exceptions(gate):
    with pytest.raises(HTTPException) as exc:
        gate("wrong-é")
    assert exc.value.status_code == 401


@pytest.mark.parametrize("mint,verify", [
    (A.mint_desk_token, A.desk_token_ok),
    (A.mint_wall_token, A.wall_token_ok),
    (A.mint_control_token, A.control_token_ok),
])
def test_non_ascii_session_signatures_are_false_and_valid_ones_still_verify(
        mint, verify):
    token, _ = mint(now=1000)
    assert verify(token, now=1001)
    parts = token.split(".")
    parts[-1] = "é" * 64
    assert verify(".".join(parts), now=1001) is False


@pytest.mark.parametrize("route", ["/api/desk/unlock", "/api/wall/unlock",
    "/api/command/session", "/api/command/session/control"])
def test_non_ascii_password_is_a_refusal_and_mints_no_session(route):
    response = TestClient(A.app).post(route, json={"password": "wrong-é"})
    assert response.status_code in (200, 401)
    payload = response.json()
    assert payload.get("detail", payload)["ok"] is False
    assert "token" not in response.json()
    assert "set-cookie" not in response.headers


def test_global_throttle_does_not_retain_new_clients():
    for i in range(G.GLOBAL_LIMIT):
        assert G.check({"x-admin-token": "wrong"}, str(i), GOOD, now=1000) is None
    recorded = len(G._FAILS)
    for i in range(G.MAX_CLIENTS * 2):
        assert G.check({"x-admin-token": "wrong"}, "new-%d" % i,
                       GOOD, now=1001) == G.R_THROTTLED
    assert len(G._FAILS) == recorded
    # Throttling still protects the budget, and its owner still has access.
    assert len(G._GLOBAL) == G.GLOBAL_LIMIT
    assert G.check({"x-admin-token": GOOD}, "owner", GOOD, now=1001) is None
    assert G.check({"x-admin-token": "wrong"}, "new-client", GOOD,
                   now=1000 + G.WINDOW_S) is None


def test_expired_clients_do_not_accumulate_across_attack_windows():
    for window in range(G.MAX_CLIENTS // G.GLOBAL_LIMIT + 2):
        now = window * (G.WINDOW_S + 1)
        for i in range(G.GLOBAL_LIMIT):
            G.check({"x-admin-token": "wrong"}, "%d-%d" % (window, i),
                    GOOD, now=now)
    assert len(G._FAILS) <= G.MAX_CLIENTS


def test_unlock_budget_uses_the_edges_hop_not_the_callers_claim():
    client = TestClient(A.app)
    for i in range(10):
        response = client.post("/api/desk/unlock", json={"password": "wrong"},
            headers={"x-forwarded-for": "spoof-%d, 203.0.113.7" % i})
        assert response.status_code == 200
        assert response.json()["ok"] is False
    response = client.post("/api/desk/unlock", json={"password": "wrong"},
        headers={"x-forwarded-for": "different-spoof, 203.0.113.7"})
    assert response.status_code == 429


def test_unlock_client_map_is_bounded_even_when_every_client_is_recent(monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1000)
    hits = {}
    for i in range(2000):
        request = SimpleNamespace(headers={}, client=SimpleNamespace(host=str(i)))
        refused = A._throttled(hits, request)
        assert refused == (i >= 1000)
        assert len(hits) <= 1000
    # Existing budgets still work, and expired clients release capacity.
    old = SimpleNamespace(headers={}, client=SimpleNamespace(host="0"))
    assert A._throttled(hits, old) is False
    monkeypatch.setattr("time.time", lambda: 1060)
    new = SimpleNamespace(headers={}, client=SimpleNamespace(host="new"))
    assert A._throttled(hits, new) is False


@pytest.mark.parametrize("shared_client,limit", [
    (True, G.PER_CLIENT_LIMIT), (False, G.GLOBAL_LIMIT)])
def test_concurrent_wrong_guesses_cannot_overspend_a_budget(
        monkeypatch, shared_client, limit):
    original_blocked = G.blocked

    def competing_read(key, now):
        verdict = original_blocked(key, now)
        # Force the window between reading and charging a budget. This
        # models concurrent handlers without depending on scheduler luck.
        time.sleep(0.001)
        return verdict

    monkeypatch.setattr(G, "blocked", competing_read)

    def guess(i):
        return G.check({"x-admin-token": "wrong-é"},
            "shared" if shared_client else str(i), GOOD, now=1000)

    with ThreadPoolExecutor(max_workers=24) as workers:
        results = list(workers.map(guess, range(120)))
    assert results.count(None) == limit
    assert results.count(G.R_THROTTLED) == 120 - limit
    assert len(G._GLOBAL) == limit
    assert len(G._FAILS) <= limit
    assert G.check({"x-admin-token": GOOD}, "owner", GOOD, now=1001) is None


@pytest.mark.parametrize("text", ["é", "\u202e", "\ud800", "é" * 65536])
def test_hostile_text_is_a_mismatch_without_normalizing_credentials(text):
    assert G.token_ok(text, GOOD) is False


def test_text_comparison_does_not_normalize_distinct_credentials():
    assert G.token_ok("é", "e\u0301") is False
    assert G.token_ok("é", "é") is True


def test_concurrent_unlock_guesses_cannot_overspend_the_client_budget():
    class CompetingHits(dict):
        def get(self, key, default=None):
            result = super().get(key, default)
            time.sleep(0.001)
            return result

    hits = CompetingHits()
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="shared"))
    with ThreadPoolExecutor(max_workers=24) as workers:
        verdicts = list(workers.map(lambda _: A._throttled(hits, request), range(120)))
    assert verdicts.count(False) == 10
    assert verdicts.count(True) == 110
    assert len(hits["shared"]) == 10


def test_headerless_ping_cannot_allocate_unbounded_client_state():
    async def requests():
        for i in range(1200):
            request = SimpleNamespace(headers={}, client=SimpleNamespace(host=str(i)))
            if i < 1000:
                result = await A.admin_ping(request, x_admin_token="")
                assert result["match"] is False
            else:
                with pytest.raises(HTTPException) as exc:
                    await A.admin_ping(request, x_admin_token="")
                assert exc.value.status_code == 429
            assert len(A._PING_HITS) <= 1000
    asyncio.run(requests())


def test_ping_cannot_reset_its_budget_by_spoofing_the_leftmost_hop():
    client = TestClient(A.app)
    for i in range(10):
        response = client.post("/api/admin/ping", headers={
            "x-forwarded-for": "spoof-%d, 203.0.113.7" % i})
        assert response.status_code == 200
    response = client.post("/api/admin/ping", headers={
        "x-forwarded-for": "another-spoof, 203.0.113.7"})
    assert response.status_code == 429


def test_ping_preserves_its_existing_limit_even_for_the_correct_credential():
    client = TestClient(A.app)
    for _ in range(10):
        response = client.post("/api/admin/ping", headers={"x-admin-token": GOOD})
        assert response.status_code == 200 and response.json()["match"] is True
    assert client.post("/api/admin/ping", headers={
        "x-admin-token": GOOD}).status_code == 429
