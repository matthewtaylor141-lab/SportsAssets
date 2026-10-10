"""Shared harness for the persona chat / voice proofs (migration 180).

* Builds on the Audrey chat harness (`_audrey_chat_fixture`): the same known
  test-only command credentials decide every role through the real auth
  functions in `api/app.py`, the same counting pool (a real asyncpg
  connection per acquire), the same 2031 clock.
* BOTH VENDORS ARE FAKED. The Claude Messages API is a scripted
  server-sent-event stream behind `httpx2.MockTransport` (the REAL Anthropic
  SDK builds the request and parses the stream); ElevenLabs is a scripted
  `httpx.MockTransport`. No test reaches the network, and a vendor that is
  not expected to be called fails the test if it is.
* Migrations 156 and 180 are applied if absent; persona rows and the test
  conversations are purged around every test.
"""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

from tests import _audrey_chat_fixture as F

DSN = F.DSN
pg = F.pg
T0 = F.T0
ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION_180 = ROOT / "migrations" / "180_agent_personas_chat_and_voice.sql"
FAKE_ANTHROPIC_KEY = F.FAKE_KEY
FAKE_EL_KEY = "el-test-FAKE-elevenlabs-key-never-real-9f8e7d6c5b4a"
AUDIO = b"ID3\x04\x00fake-mpeg-audio-" + bytes(range(64))
CORE_NUMBERS = {1000.0, 0.5, 2000.0, 0.6, 0.58, 0.59, 9.0, 180.0, 800.0,
                0.4, 200.0, 2200.0}


async def ensure_schema(c) -> None:
    await F.ensure_schema(c)
    if not await c.fetchval(
            "SELECT to_regclass('agent_chat_messages') IS NOT NULL"):
        await c.execute(MIGRATION_180.read_text())


async def purge(c) -> None:
    await F.purge(c)
    async with c.transaction():
        await c.execute("SET LOCAL session_replication_role = replica")
        if await c.fetchval(
                "SELECT to_regclass('agent_chat_messages') IS NOT NULL"):
            await c.execute("DELETE FROM agent_chat_turns")
            await c.execute("DELETE FROM agent_chat_messages")
            await c.execute("DELETE FROM agent_chat_conversations")
            await c.execute("DELETE FROM agent_voice_resolutions")
            await c.execute("DELETE FROM agent_persona_versions")
        await c.execute("DELETE FROM derek_entry_decisions WHERE "
                        " decision_id LIKE 'drk-pers-%'")


def reset_process_state():
    from sportsassets.agents import persona_chat as PC
    from sportsassets.agents import persona_speech as PS
    from sportsassets.agents import personas as P

    PS.CACHE.clear()
    PS.LIMITS.clear()
    P._RESOLVED_CACHE.clear()
    PC._INFLIGHT.clear()


@pytest.fixture
def db():
    """A clean schema before and after the test."""
    async def _setup():
        c = await F.connect()
        try:
            await ensure_schema(c)
            await purge(c)
        finally:
            await c.close()
    F.run(_setup())
    reset_process_state()
    yield
    reset_process_state()

    async def _down():
        c = await F.connect()
        try:
            await purge(c)
        finally:
            await c.close()
    F.run(_down())


@pytest.fixture
def private_db(monkeypatch):
    """A DATABASE THAT HOLDS ONLY WHAT THIS TEST WRITES.

    For a proof of ABSENCE -- "no Yankees record exists, and the answer says
    so". The persona fact search is a text search over the WHOLE paper ledger
    and every other table it reads, by design (it finds the position a person
    asks about wherever it was written), so such a proof is only as true as
    the database it reads: one neighbour that COMMITS a record naming the
    team -- and the paper tables are append-only, nothing cleans them -- turns
    the answer into "found". Many suites do: paper_harness.order() defaults
    its market slug to "test-mkt-yankees", and the position-room, correlation,
    management-view and collector proofs seed "New York Yankees" books. They
    pass alone and on a fresh database; the second capital-critical run on
    one database found their leftovers (test_agent_persona_chat_is_grounded
    ::test_no_yankees_position_is_said_truthfully).

    So the test runs on a database of its own: created empty on the shared
    server, migrated by production's runner (exactly as
    test_fresh_database_migrates does), pointed at by the Audrey chat
    harness's DSN for the test's duration, and dropped afterwards. The
    shared database is neither read nor written by the test. Request it
    BEFORE `db`, which then purges and seeds this database."""
    import os
    import subprocess
    import sys
    import urllib.parse
    import uuid

    asyncpg = pytest.importorskip("asyncpg")
    shared = F.DSN
    name = "persona_%s" % uuid.uuid4().hex[:12]
    parts = urllib.parse.urlsplit(shared)
    private = urllib.parse.urlunsplit(parts._replace(path="/" + name))

    async def _admin(sql):
        c = await asyncpg.connect(shared)
        try:
            await c.execute(sql)
        finally:
            await c.close()

    F.run(_admin('CREATE DATABASE "%s"' % name))
    try:
        done = subprocess.run(
            [sys.executable, "-m", "sportsassets.scripts.migrate"],
            cwd=str(ROOT), env=dict(os.environ, DATABASE_URL=private),
            capture_output=True, text=True, timeout=1200)
        assert done.returncode == 0, done.stderr[-4000:]
        monkeypatch.setattr(F, "DSN", private)
        monkeypatch.setattr(sys.modules[__name__], "DSN", private)
        yield private
    finally:
        F.run(_admin('DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % name))


def no_keys(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "AUDREY_PROVIDER",
              "PERSONA_VOICE", "ELEVENLABS_VOICE_ID_DEREK",
              "ELEVENLABS_VOICE_ID_XAVIER", "ELEVENLABS_VOICE_ID_AUDREY"):
        monkeypatch.delenv(k, raising=False)
    F.no_network(monkeypatch)
    forbid_elevenlabs(monkeypatch)


def forbid_elevenlabs(monkeypatch):
    from sportsassets.agents import persona_speech as PS
    from sportsassets.agents import personas as P

    def _never():
        raise AssertionError("ElevenLabs must not be called")
    monkeypatch.setattr(P, "http_client_factory", _never)
    monkeypatch.setattr(PS, "http_client_factory", _never)


def build_client(monkeypatch, clock):
    starlette = pytest.importorskip("starlette.testclient")
    from fastapi import FastAPI

    from sportsassets.api import agents_chat as AGC
    from sportsassets.api import agents_persona as AGP
    from sportsassets.api import app as A

    monkeypatch.setattr(A, "settings", lambda: F.Cfg(), raising=False)
    monkeypatch.setenv("AUDREY_PROVIDER_MAX_RETRIES", "0")

    async def _get():
        return F.CountingPool()

    monkeypatch.setattr(AGC, "get_pool", _get)
    monkeypatch.setattr(AGC, "_clock", clock)
    monkeypatch.setattr(AGP, "get_pool", _get)
    monkeypatch.setattr(AGP, "_clock", clock)
    app = FastAPI()
    app.include_router(AGC.router)
    app.include_router(AGP.router)
    return starlette.TestClient(app, raise_server_exceptions=True)


# ── the fake Claude Messages API (a streamed response) ──────────────

def _event(name: str, data: dict) -> bytes:
    return ("event: %s\ndata: %s\n\n" % (name, json.dumps(data))).encode()


class FakeClaudeStream:
    """Each call pops one script step: a list of text chunks (optionally
    with `stall_after=n`: after n chunks the stream waits forever, as a slow
    model would), or an (status, json) error."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.requests = []
        self.first_chunk = asyncio.Event()

    async def handler(self, request):
        import httpx2

        body = json.loads(request.content.decode() or "{}")
        self.requests.append({"path": request.url.path, "body": body,
                              "headers": {k.lower(): v for k, v in
                                          request.headers.items()}})
        if not self.steps:
            raise AssertionError("the fake model was called more often than "
                                 "scripted")
        step = self.steps.pop(0)
        if isinstance(step, tuple):
            return httpx2.Response(step[0], json=step[1])
        chunks = step["chunks"]
        stall = step.get("stall_after")
        stop = step.get("stop", "end_turn")
        model = step.get("model", "claude-opus-5-5")
        fake = self

        async def _body():
            yield _event("message_start", {
                "type": "message_start", "message": {
                    "id": "msg_fake", "type": "message", "role": "assistant",
                    "model": model, "content": [], "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 1}}})
            yield _event("content_block_start", {
                "type": "content_block_start", "index": 0,
                "content_block": {"type": "text", "text": ""}})
            for i, ch in enumerate(chunks):
                yield _event("content_block_delta", {
                    "type": "content_block_delta", "index": 0,
                    "delta": {"type": "text_delta", "text": ch}})
                fake.first_chunk.set()
                if stall is not None and i + 1 == stall:
                    await asyncio.sleep(3600)
            yield _event("content_block_stop", {"type": "content_block_stop",
                                                "index": 0})
            yield _event("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": 5}})
            yield _event("message_stop", {"type": "message_stop"})
        return httpx2.Response(200, headers={
            "content-type": "text/event-stream"}, content=_body())

    def client(self):
        import httpx2

        return httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler))


def use_claude(monkeypatch, fake: FakeClaudeStream) -> FakeClaudeStream:
    from sportsassets.agents import audrey_chat as AC

    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC_KEY)
    monkeypatch.delenv("AUDREY_PROVIDER", raising=False)
    monkeypatch.setattr(AC, "http_client_factory", fake.client)
    return fake


# ── the fake ElevenLabs API ─────────────────────────────────────────

VOICES = [
    {"voice_id": "clonedLiam0000001", "name": "Liam", "category": "cloned",
     "labels": {"gender": "male", "age": "young"}},
    {"voice_id": "premadeLiam000001", "name": "Liam", "category": "premade",
     "labels": {"gender": "male", "age": "young", "accent": "american",
                "descriptive": "articulate"}},
    {"voice_id": "premadeBrian00001", "name": "Brian", "category": "premade",
     "labels": {"gender": "male", "age": "middle aged", "accent": "american",
                "descriptive": "deep"}},
    {"voice_id": "premadeCharl00001", "name": "Charlotte",
     "category": "premade",
     "labels": {"gender": "female", "age": "young", "descriptive": "husky"}},
    {"voice_id": "premadeGeorge0001", "name": "George", "category": "premade",
     "labels": {"gender": "male", "age": "middle aged", "accent": "british",
                "descriptive": "warm"}},
]


class FakeElevenLabs:
    def __init__(self, *, tts_status: int = 200, voices_status: int = 200):
        self.tts_status = tts_status
        self.voices_status = voices_status
        self.calls = []

    async def handler(self, request):
        import httpx

        self.calls.append({"method": request.method,
                           "path": request.url.path,
                           "params": dict(request.url.params),
                           "headers": {k.lower(): v for k, v in
                                       request.headers.items()},
                           "body": (json.loads(request.content.decode())
                                    if request.content else None)})
        if request.url.path == "/v1/voices":
            if self.voices_status != 200:
                return httpx.Response(self.voices_status,
                                      json={"detail": "nope"})
            return httpx.Response(200, json={"voices": VOICES})
        if request.url.path.startswith("/v1/text-to-speech/"):
            if self.tts_status != 200:
                return httpx.Response(self.tts_status, json={
                    "detail": {"status": "quota_exceeded"}})

            async def _audio():
                yield AUDIO[:20]
                await asyncio.sleep(0)
                yield AUDIO[20:]
            return httpx.Response(200, headers={"content-type": "audio/mpeg"},
                                  content=_audio())
        return httpx.Response(404, json={"detail": "unknown path"})

    def client(self):
        import httpx

        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def tts_calls(self):
        return [c for c in self.calls
                if c["path"].startswith("/v1/text-to-speech/")]


def use_elevenlabs(monkeypatch, fake: FakeElevenLabs) -> FakeElevenLabs:
    from sportsassets.agents import persona_speech as PS
    from sportsassets.agents import personas as P

    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_EL_KEY)
    monkeypatch.setattr(P, "http_client_factory", fake.client)
    monkeypatch.setattr(PS, "http_client_factory", fake.client)
    return fake


def numbers_in(text: str) -> set:
    from sportsassets.agents import persona_chat as PC

    return {x for x, _d in PC._numbers(text)}


def record_ids(got: dict) -> set:
    return {(c["kind"], c["id"]) for c in got.get("citations") or []}


async def seed_yankees(c, *, now: float = T0) -> dict:
    """A REAL Yankees entry decision (Derek V2 record shape, written with
    SQL into derek_entry_decisions) and a Xavier decision on the same market
    through `bettor_xavier.record_decision`."""
    from sportsassets import bettor_xavier as X

    pd = {"policy_name": "DEREK_ENTRY_POLICY_V2",
          "policy_version": "DEREK_ENTRY_POLICY_V2",
          "p_internal": 0.61, "p_pinnacle": 0.57, "p_blended": 0.59,
          "gross_edge_pp": 7.0, "threshold_gross_edge_pp": 5.0,
          "executable_price": 0.52, "qty": 100,
          "acquisition_cost_usd": 52.0, "expected_gross_profit_usd": 7.0,
          "fees_usd": 1.25, "net_expected_profit_usd": 5.75,
          "instrument": {"participant": "New York Yankees"}}
    await c.execute(
        "INSERT INTO derek_entry_decisions (decision_id, fixture, "
        " us_market_slug, side, decided_at, policy_version, pinnacle_p, "
        " model_p, model_version, gross_edge_pp, executable_price, qty, "
        " expected_gross_profit_usd, fees_usd, expected_net_profit_usd, "
        " checks, verdict, evidence, decided_by) VALUES ('drk-pers-nyy-1', "
        " 'mlb-nyy-bos-2031-03-04', 'mlb-nyy-bos-2031-03-04', 'YES', "
        " to_timestamp($1), 'DEREK_ENTRY_POLICY_V2', 0.57, 0.61, 'm-test', "
        " 0.07, 0.52, 100, 7.0, 1.25, 5.75, '[]'::jsonb, 'ENTER', "
        " $2::jsonb, 'AFTER_CYCLE')", now - 7200,
        json.dumps({"policy_decision": pd}))
    x = await X.record_decision(
        c, account_id="acct-chatt-nyy", venue=F.VENUE,
        intent_id="chatt-intent-nyy", decided_at=now - 3600,
        responsibility_state=X.HELD,
        execution_eligibility=X.E_SUBMISSION_DISABLED,
        alternatives=[{"action": "HOLD", "qty": 100, "value_usd": 5.75},
                      {"action": "PAIR_COMPLETE",
                       "blocker": "NO_EXECUTABLE_HEDGE_DEPTH"}],
        reasoning={"why": "HOLD 5.75 beats an unavailable pair"},
        expected_economics={"expected_net_usd": 5.75},
        residual_exposure={"unpaired_qty": 100}, evidence={},
        obligations=[], chosen_action="HOLD",
        us_market_slug="mlb-nyy-bos-2031-03-04")
    assert x["ok"], x
    return {"derek": "drk-pers-nyy-1", "xavier": x["xavier_decision_id"]}
