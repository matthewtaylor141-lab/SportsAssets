"""PROOF 21 (chat part): THE PROVIDER FAILING NEVER BREAKS THE ANSWER.

With a provider key configured, the Claude Messages API -- reached through the
REAL Anthropic SDK over an httpx2.MockTransport, so the network is never
reached -- times out, hangs past the bounded timeout, returns 401/403/429/
500/529, returns garbage, refuses (with or without stop_details), stops at
max_tokens or keeps pausing, or the connection fails. In
every case the answer is still produced from the records, deterministically,
with the disclosure 'AI provider unavailable — answered directly from records';
the failure reason is stored on the answer; no exception escapes. With no key
at all the disclosure says 'not configured' and the transport is never called.
Credentials never appear in any stored message, in a provider record, or in a
request body sent to the model.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests import _audrey_chat_fixture as F

pg = F.pg


@pytest.fixture()
def world():
    if not F.DSN:
        pytest.skip("needs RN1X_TEST_DSN")

    async def _setup():
        c = await F.connect()
        try:
            await F.ensure_schema(c)
            await F.purge(c)
            return await F.seed_world(c)
        finally:
            await c.close()

    seeded = F.run(_setup())
    yield seeded

    async def _teardown():
        c = await F.connect()
        try:
            await F.teardown(c)
        finally:
            await c.close()

    F.run(_teardown())


def _httpx2():
    import httpx2
    return httpx2


def _garbage(body):
    return 200, {"unexpected": True}


def _refusal(body):
    return 200, F.message([], "refusal")


def _refusal_with_category(body):
    return 200, F.message([], "refusal", stop_details={
        "type": "refusal", "category": "cyber",
        "explanation": "declined by a safety classifier"})


def _max_tokens(body):
    return 200, F.message([{"type": "text", "text": "Xavier held becau"}],
                          "max_tokens")


def _pause(body):
    return 200, F.message([{"type": "text", "text": "..."}], "pause_turn")


async def _hang(body):
    await asyncio.sleep(3600)


def _failures():
    h = _httpx2()
    return [
        ("read-timeout", [h.ReadTimeout("read timed out")], "TIMEOUT",
         "PROVIDER_UNAVAILABLE"),
        ("http-500", [F.api_error(500, "api_error",
                                  "upstream error with key %s" % F.FAKE_KEY)],
         "HTTP_500", "PROVIDER_UNAVAILABLE"),
        ("http-529", [F.api_error(529, "overloaded_error")],
         "PROVIDER_OVERLOADED", "PROVIDER_UNAVAILABLE"),
        ("http-429", [F.api_error(429, "rate_limit_error")],
         "PROVIDER_RATE_LIMITED", "PROVIDER_UNAVAILABLE"),
        ("http-401", [F.api_error(401, "authentication_error")],
         "PROVIDER_CREDENTIAL_REJECTED", "PROVIDER_UNAVAILABLE"),
        ("http-403", [F.api_error(403, "permission_error")],
         "PROVIDER_CREDENTIAL_REJECTED", "PROVIDER_UNAVAILABLE"),
        ("connect-error", [h.ConnectError("connection refused to %s"
                                          % F.FAKE_KEY)],
         "CONNECTION_FAILED", "PROVIDER_UNAVAILABLE"),
        ("runtime-error", [RuntimeError("boom")], "CONNECTION_FAILED",
         "PROVIDER_UNAVAILABLE"),
        ("malformed", [_garbage], "MALFORMED_RESPONSE",
         "PROVIDER_UNAVAILABLE"),
        ("refusal", [_refusal], "PROVIDER_REFUSAL", "PROVIDER_REFUSAL"),
        ("refusal-category", [_refusal_with_category],
         "PROVIDER_REFUSAL:cyber", "PROVIDER_REFUSAL"),
        ("max-tokens", [_max_tokens], "MAX_TOKENS", "INCOMPLETE_OUTPUT"),
        ("pause-turn-limit", [_pause] * 4, "PAUSE_TURN_LIMIT",
         "INCOMPLETE_OUTPUT"),
        ("tool-loop", [F.tool_use("agents_status", {})] * 4,
         "TOOL_ROUNDS_EXCEEDED", "INCOMPLETE_OUTPUT"),
    ]


FAILURE_IDS = ["read-timeout", "http-500", "http-529", "http-429",
               "http-401", "http-403", "connect-error", "runtime-error",
               "malformed", "refusal", "refusal-category", "max-tokens",
               "pause-turn-limit", "tool-loop"]


async def _stored_text(cid_prefix: str) -> str:
    c = await F.connect()
    try:
        rows = await c.fetch(
            "SELECT body, citations::text AS c, provider::text AS p, "
            " tool_calls::text AS t FROM audrey_messages WHERE "
            " conversation_id LIKE $1", cid_prefix + "%")
        return "\n".join("%s|%s|%s|%s" % (r["body"], r["c"], r["p"], r["t"])
                         for r in rows)
    finally:
        await c.close()


async def _provider_of(cid: str) -> dict:
    c = await F.connect()
    try:
        v = await c.fetchval(
            "SELECT provider FROM audrey_messages WHERE conversation_id=$1 "
            " AND role='AUDREY' ORDER BY seq DESC LIMIT 1", cid)
        return json.loads(v) if isinstance(v, str) else v
    finally:
        await c.close()


@pg
@pytest.mark.parametrize("name", FAILURE_IDS)
def test_a_provider_failure_is_disclosed_and_answered_from_records(
        world, monkeypatch, name):
    from sportsassets.agents import audrey_chat as AC

    _n, script, reason, outcome = [f for f in _failures() if f[0] == name][0]
    monkeypatch.setenv("ANTHROPIC_API_KEY", F.FAKE_KEY)
    fake = F.use_fake(monkeypatch, F.FakeModel(list(script)))
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    cid = "chatt-fail-%s" % name
    r = client.post("/api/command/agents/audrey/chat", headers=F.desk_headers(),
                    json={"message": "Why did Xavier HOLD instead of pair on "
                                     "%s?" % F.ACCT_A,
                          "conversation_id": cid, "request_id": F.rid("f")})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == AC.S_ANSWERED
    assert got["provider"]["mode"] == AC.MODE_DETERMINISTIC
    assert got["provider"]["answer_mode"] == AC.MODE_DETERMINISTIC
    assert got["provider"]["configured"] is True
    assert got["provider"]["failure"] == reason
    assert got["outcome"] == outcome
    assert got["provider"]["requested_model"] == "claude-opus-5-5"
    assert got["provider"]["disclosure"] == AC.DISCLOSE_UNAVAILABLE
    assert got["answer"].startswith("[%s]" % AC.DISCLOSE_UNAVAILABLE)
    # the deterministic answer still carries the evidence
    assert world["hold_id"] in got["answer"]
    assert world["hold_id"] in [c["id"] for c in got["citations"]]
    assert "NO_EXECUTABLE_HEDGE_DEPTH" in got["answer"]
    # max_retries=0 in this harness: one attempt per failing request
    if name not in ("pause-turn-limit", "tool-loop"):
        assert len(fake.requests) == 1
    st = AC.provider_status()
    assert st["last_failure"] == reason
    assert st["last_failure_at"].startswith("2031-03-04")
    stored = F.run(_provider_of(cid))
    assert stored["failure"] == reason
    assert stored["outcome"] == outcome
    assert stored["disclosure"] == AC.DISCLOSE_UNAVAILABLE
    assert F.FAKE_KEY not in F.run(_stored_text(cid))
    for req in fake.requests:
        assert F.FAKE_KEY not in json.dumps(req["body"])


@pg
def test_a_hanging_provider_is_cut_off_by_the_bounded_timeout(world,
                                                             monkeypatch):
    from sportsassets.agents import audrey_chat as AC

    monkeypatch.setenv("ANTHROPIC_API_KEY", F.FAKE_KEY)
    monkeypatch.setenv("AUDREY_PROVIDER_TIMEOUT_S", "0.1")   # clamped to 2s
    assert AC.provider_config()["timeout_s"] == 2.0
    F.use_fake(monkeypatch, F.FakeModel([_hang]))
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    got = client.post("/api/command/agents/audrey/chat",
                      headers=F.desk_headers(),
                      json={"message": "What are Derek and Xavier doing?",
                            "conversation_id": "chatt-fail-hang",
                            "request_id": F.rid("hang")}).json()
    assert got["status"] == AC.S_ANSWERED
    assert got["provider"]["failure"] == "TIMEOUT"
    assert got["answer"].startswith("[%s]" % AC.DISCLOSE_UNAVAILABLE)
    assert "DEREK" in [c["id"] for c in got["citations"]]


@pg
def test_no_key_means_not_configured_and_the_transport_is_never_called(
        world, monkeypatch):
    from sportsassets.agents import audrey_chat as AC

    F.no_network(monkeypatch)
    cfg = AC.provider_config()
    assert cfg["configured"] is False and cfg["reason"] == \
        "NO_ANTHROPIC_API_KEY"
    assert cfg["model"] == "claude-opus-5-5"
    assert cfg["key_present"] is False
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    got = client.post("/api/command/agents/audrey/chat",
                      headers=F.desk_headers(),
                      json={"message": "What are Derek and Xavier doing?",
                            "conversation_id": "chatt-fail-nokey",
                            "request_id": F.rid("nokey")}).json()
    assert got["provider"]["mode"] == AC.MODE_DETERMINISTIC
    assert got["provider"]["failure"] is None
    assert got["answer"].startswith("[%s]" % AC.DISCLOSE_NOT_CONFIGURED)
    d = client.get("/api/command/agents/audrey/chat/describe",
                   headers=F.desk_headers()).json()
    assert d["chat"]["provider"]["mode"] == "DETERMINISTIC"
    assert {t["name"]: t["permission"] for t in d["tools"]}[
        "create_directive"] == "CONTROL"
    # AUDREY_PROVIDER=off disables a configured key, and says so
    monkeypatch.setenv("ANTHROPIC_API_KEY", F.FAKE_KEY)
    monkeypatch.setenv("AUDREY_PROVIDER", "off")
    assert AC.provider_config()["reason"] == "DISABLED_BY_AUDREY_PROVIDER"
    assert F.FAKE_KEY not in json.dumps(AC.provider_config())


@pg
def test_secrets_in_a_message_are_redacted_before_storage_and_the_model(
        world, monkeypatch):
    from sportsassets.agents import audrey_chat as AC

    monkeypatch.setenv("ANTHROPIC_API_KEY", F.FAKE_KEY)
    monkeypatch.setenv("CHATT_SERVICE_TOKEN", "chatt-service-token-value-xyz")
    fake = F.use_fake(monkeypatch, F.FakeModel(
        [F.final_text(lambda body: "Noted.")]))
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    cid = "chatt-secret"
    msg = ("What are Derek and Xavier doing? My key is %s and the service "
           "token is chatt-service-token-value-xyz; password: hunter2hunter2"
           % F.FAKE_KEY)
    got = client.post("/api/command/agents/audrey/chat",
                      headers=F.desk_headers(),
                      json={"message": msg, "conversation_id": cid,
                            "request_id": F.rid("secret")}).json()
    assert got["provider"]["mode"] == AC.MODE_LLM
    stored = F.run(_stored_text(cid))
    for secret in (F.FAKE_KEY, "chatt-service-token-value-xyz",
                   "hunter2hunter2"):
        assert secret not in stored, secret
        assert secret not in json.dumps(fake.requests[0]["body"]), secret
        assert secret not in json.dumps(got), secret
    assert "[REDACTED]" in stored


@pg
def test_no_exception_escapes_the_service_even_when_called_directly(
        world, monkeypatch):
    from sportsassets.agents import audrey_chat as AC

    def _explode(request):
        raise ValueError("provider exploded")

    def _client():
        httpx2 = _httpx2()
        return httpx2.AsyncClient(transport=httpx2.MockTransport(_explode))

    async def _go():
        c = await F.connect()
        try:
            env = {"ANTHROPIC_API_KEY": F.FAKE_KEY}
            outs = []
            for q in ("What are Derek and Xavier doing?",
                      "Which decisions helped or hurt performance?",
                      "What did today's audit find?",
                      "What change are you proposing?",
                      "What happened to the directive I gave yesterday?",
                      "Why was the trade on fixture %s refused?"
                      % F.FIXTURE):
                outs.append(await AC.handle_message(
                    c, role="desk", message=q, now=F.T0, env=env,
                    http_client=_client(),
                    conversation_id="chatt-direct"))
            return outs
        finally:
            await c.close()

    for got in F.run(_go()):
        assert got["status"] == AC.S_ANSWERED, got
        assert got["provider"]["failure"] == "CONNECTION_FAILED"
        assert got["answer"].startswith("[%s]" % AC.DISCLOSE_UNAVAILABLE)
