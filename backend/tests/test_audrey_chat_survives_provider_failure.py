"""PROOF 21 (chat part): THE PROVIDER FAILING NEVER BREAKS THE ANSWER.

With a provider key configured, the Claude Messages API (a fake transport --
the network is never reached) times out, hangs past the bounded timeout,
returns an HTTP error, returns garbage, refuses, or the connection fails. In
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


class _ConnectError(Exception):
    pass


class ReadTimeout(Exception):
    """Named like httpx's, as the real transport would raise."""


def _status(code, error_type="api_error", message=""):
    def _step(body):
        return code, {"type": "error", "error": {"type": error_type,
                                                 "message": message}}
    return _step


def _garbage(body):
    return 200, {"unexpected": True}


def _refusal(body):
    return 200, {"id": "m", "type": "message", "role": "assistant",
                 "stop_reason": "refusal", "content": []}


async def _hang(url, *, headers, json_body, timeout_s):
    await asyncio.sleep(3600)


FAILURES = [
    ("timeout-error", [asyncio.TimeoutError()], "TIMEOUT"),
    ("read-timeout", [ReadTimeout()], "TIMEOUT"),
    ("http-500", [_status(500, message="upstream error with key %s"
                          % F.FAKE_KEY)], "HTTP_500:api_error"),
    ("http-529", [_status(529, "overloaded_error")],
     "HTTP_529:overloaded_error"),
    ("http-401", [_status(401, "authentication_error")],
     "HTTP_401:authentication_error"),
    ("connect-error", [_ConnectError("connection refused to %s"
                                     % F.FAKE_KEY)],
     "TRANSPORT_ERROR:_ConnectError"),
    ("malformed", [_garbage], "MALFORMED_RESPONSE"),
    ("refusal", [_refusal], "PROVIDER_REFUSAL"),
    ("runtime-error", [RuntimeError("boom")], "TRANSPORT_ERROR:RuntimeError"),
    ("tool-loop", [F.tool_use("agents_status", {})] * 4,
     "TOOL_ROUNDS_EXCEEDED"),
]


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
@pytest.mark.parametrize("name,script,reason", FAILURES,
                         ids=[f[0] for f in FAILURES])
def test_a_provider_failure_is_disclosed_and_answered_from_records(
        world, monkeypatch, name, script, reason):
    from sportsassets.agents import audrey_chat as AC

    monkeypatch.setenv("ANTHROPIC_API_KEY", F.FAKE_KEY)
    fake = F.FakeModel(list(script))
    monkeypatch.setattr(AC, "httpx_transport", fake)
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    cid = "chatt-fail-%s" % name
    r = client.post("/api/command/agents/audrey/chat", headers=F.desk_headers(),
                    json={"message": "Why did Xavier HOLD instead of pair on "
                                     "%s?" % F.ACCT_A,
                          "conversation_id": cid})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == AC.S_ANSWERED
    assert got["provider"]["mode"] == AC.MODE_DETERMINISTIC
    assert got["provider"]["configured"] is True
    assert got["provider"]["failure"] == reason
    assert got["provider"]["disclosure"] == AC.DISCLOSE_UNAVAILABLE
    assert got["answer"].startswith("[%s]" % AC.DISCLOSE_UNAVAILABLE)
    # the deterministic answer still carries the evidence
    assert world["hold_id"] in got["answer"]
    assert world["hold_id"] in [c["id"] for c in got["citations"]]
    assert "NO_EXECUTABLE_HEDGE_DEPTH" in got["answer"]
    stored = F.run(_provider_of(cid))
    assert stored["failure"] == reason
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
    monkeypatch.setattr(AC, "httpx_transport", _hang)
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    got = client.post("/api/command/agents/audrey/chat",
                      headers=F.desk_headers(),
                      json={"message": "What are Derek and Xavier doing?",
                            "conversation_id": "chatt-fail-hang"}).json()
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
    assert "key" not in json.dumps(cfg).lower().replace(
        "no_anthropic_api_key", "")
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    got = client.post("/api/command/agents/audrey/chat",
                      headers=F.desk_headers(),
                      json={"message": "What are Derek and Xavier doing?",
                            "conversation_id": "chatt-fail-nokey"}).json()
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
    fake = F.FakeModel([F.final_text(lambda body: "Noted.")])
    monkeypatch.setattr(AC, "httpx_transport", fake)
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    cid = "chatt-secret"
    msg = ("What are Derek and Xavier doing? My key is %s and the service "
           "token is chatt-service-token-value-xyz; password: hunter2hunter2"
           % F.FAKE_KEY)
    got = client.post("/api/command/agents/audrey/chat",
                      headers=F.desk_headers(),
                      json={"message": msg, "conversation_id": cid}).json()
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

    async def _explode(*a, **kw):
        raise ValueError("provider exploded")

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
                    transport=_explode,
                    conversation_id="chatt-direct"))
            return outs
        finally:
            await c.close()

    for got in F.run(_go()):
        assert got["status"] == AC.S_ANSWERED, got
        assert got["provider"]["failure"] == "TRANSPORT_ERROR:ValueError"
        assert got["answer"].startswith("[%s]" % AC.DISCLOSE_UNAVAILABLE)
