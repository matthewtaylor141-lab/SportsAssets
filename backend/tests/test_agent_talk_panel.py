"""TALK TO DEREK / XAVIER / AUDREY: the management conversation on every
agent page, its microphone route and the memory the model is given.

* Each of the three Command Centre pages carries its OWN always-visible
  "Talk to <agent>" panel (text, Send, history, microphone, voice on/off,
  stop, replay), placed ABOVE the records and OUTSIDE the collapsed funded
  section, wired to that agent's persona chat, speech and transcription
  routes -- not only Audrey's.
* POST /api/command/agents/{agent}/transcribe turns a recorded clip into text
  through the server-side provider key; a provider refusal (e.g. the key
  lacks speech_to_text) is a 503 with the SANITIZED diagnostic, never the
  key; an unsupported type is 415.
* Every persona answer reports `context_supplied`: how many earlier turns of
  the conversation were SENT TO THE MODEL, the record facts, the active
  entry policy and the agent's stored lessons -- what the model received,
  not what is merely stored.

ElevenLabs is a fake `httpx.MockTransport`; nothing reaches the network.
"""
from __future__ import annotations

import json

import pytest

from tests import _audrey_chat_fixture as F
from tests import _persona_harness as H
from tests._persona_harness import db, pg  # noqa: F401  (fixture)


# ═════════════════════════════════════════════════════════════════════
# 1 · THE PANEL ON EVERY AGENT PAGE (pure render)
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("kind,name", [("derek", "Derek"),
                                       ("xavier", "Xavier"),
                                       ("audrey", "Audrey")])
def test_each_agent_page_has_its_own_talk_panel_above_the_records(kind, name):
    from sportsassets.api import agent_pages as A
    html = A.page_html(kind)
    assert html.count('id="talk"') == 1
    assert 'data-agent="%s"' % kind in html
    assert "Talk to %s" % name in html
    for el in ('id="talk-in"', 'id="talk-send"', 'id="talk-mic"',
               'id="talk-mute"', 'id="talk-stop"', 'id="talk-log"',
               'id="talk-state"', 'id="talk-new"'):
        assert el in html, (kind, el)
    # ALWAYS VISIBLE: before the ops panels and outside the funded section
    talk = html.index('id="talk"')
    assert talk < html.index('<details class="cc-funded"')
    assert talk < html.index('id="full-record"')
    assert 'id="talk" hidden' not in html
    # the routes the panel calls are that agent's own
    js = A.TALK_JS
    for route in ("'/api/command/agents/' + agent", "/persona/chat",
                  "/persona/conversations/", "/speech", "/transcribe"):
        assert route in js
    # persistence per agent across refresh, and the explicit states
    assert "'cc.talk.' + agent + '.conversation'" in js
    for state in ("Microphone permission denied", "Transcription unavailable",
                  "Voice unavailable", "SIGN-IN REQUIRED", "Memory supplied",
                  "Stop recording"):
        assert state in js, state
    # a conversation is not a policy change, said on the panel itself
    assert "never changes a policy or places an order" in html


def test_the_panel_stays_inside_the_phone_width():
    from sportsassets.api import agent_pages as A
    css = A.TALK_CSS
    assert "@media(max-width:640px)" in css
    assert "overflow-wrap:anywhere" in css
    assert "font-size:16px" in css          # no iOS zoom on focus


# ═════════════════════════════════════════════════════════════════════
# 2 · THE MICROPHONE ROUTE
# ═════════════════════════════════════════════════════════════════════

class _FakeSTT:
    def __init__(self, status=200, text="What is the paper cash?"):
        self.status, self.text, self.calls = status, text, []

    async def handler(self, request):
        import httpx
        self.calls.append({"path": request.url.path,
                           "headers": {k.lower(): v for k, v in
                                       request.headers.items()},
                           "body": request.content})
        if request.url.path != "/v1/speech-to-text":
            return httpx.Response(404, json={"detail": "unknown"})
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": {
                "status": "missing_permissions",
                "message": "The API key is missing the permission "
                           "speech_to_text"}})
        return httpx.Response(200, json={"text": self.text,
                                         "language_code": "eng"})

    def client(self):
        import httpx
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _stt(monkeypatch, fake):
    from sportsassets.agents import persona_speech as PS
    monkeypatch.setenv("ELEVENLABS_API_KEY", H.FAKE_EL_KEY)
    monkeypatch.setattr(PS, "http_client_factory", fake.client)
    return fake


@pg
def test_a_recorded_clip_is_transcribed_with_the_server_key(db, monkeypatch):
    H.no_keys(monkeypatch)
    fake = _stt(monkeypatch, _FakeSTT())
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    r = client.post("/api/command/agents/xavier/transcribe",
                    content=b"\x1aE\xdf\xa3fake-webm-clip",
                    headers=dict(F.desk_headers(),
                                 **{"content-type": "audio/webm;codecs=opus"}))
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "What is the paper cash?"
    assert r.json()["status"] == "TRANSCRIBED"
    call = fake.calls[0]
    assert call["headers"]["xi-api-key"] == H.FAKE_EL_KEY
    assert b"scribe_v1" in call["body"] and b"fake-webm-clip" in call["body"]
    assert H.FAKE_EL_KEY not in r.text


@pg
def test_a_provider_refusal_is_named_and_never_leaks_the_key(db,
                                                              monkeypatch):
    H.no_keys(monkeypatch)
    _stt(monkeypatch, _FakeSTT(status=401))
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    r = client.post("/api/command/agents/derek/transcribe", content=b"x" * 64,
                    headers=dict(F.desk_headers(),
                                 **{"content-type": "audio/mp4"}))
    assert r.status_code == 503
    b = r.json()
    assert b["reason"] == "VOICE_PROVIDER_FAILED"
    d = b["provider_diagnostic"]
    assert d["http_status"] == 401
    assert d["provider_error_status"] == "missing_permissions"
    assert d["required_permission"] == "speech_to_text"
    assert H.FAKE_EL_KEY not in r.text


@pg
def test_wrong_type_no_key_and_no_session_are_refused(db, monkeypatch):
    H.no_keys(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    r = client.post("/api/command/agents/derek/transcribe", content=b"x",
                    headers=dict(F.desk_headers(),
                                 **{"content-type": "text/plain"}))
    assert r.status_code == 415
    r = client.post("/api/command/agents/derek/transcribe", content=b"x" * 9,
                    headers=dict(F.desk_headers(),
                                 **{"content-type": "audio/webm"}))
    assert r.status_code == 503
    assert r.json()["reason"] == "VOICE_UNAVAILABLE_SERVER_KEY_NOT_CONFIGURED"
    r = client.post("/api/command/agents/derek/transcribe", content=b"x" * 9,
                    headers={"content-type": "audio/webm"})
    assert r.status_code in (401, 403)


# ═════════════════════════════════════════════════════════════════════
# 3 · WHAT THE MODEL WAS GIVEN
# ═════════════════════════════════════════════════════════════════════

@pg
def test_every_answer_reports_what_the_model_received(db, monkeypatch):
    H.no_keys(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    first = client.post("/api/command/agents/xavier/persona/chat",
                        json={"message": "What are you managing right now?",
                              "allow_records_only": True},
                        headers=F.desk_headers())
    assert first.status_code == 200, first.text
    a = first.json()
    cs = a["context_supplied"]
    assert cs["model_composed"] is False          # no key: records-only
    assert cs["prior_turns"] == 0 and cs["prior_turns_stored"] == 0
    assert cs["facts"] == a["facts_considered"]
    assert "records-only" in cs["basis"]
    cid = a["conversation_id"]
    second = client.post("/api/command/agents/xavier/persona/chat",
                         json={"message": "And why is that?",
                               "conversation_id": cid,
                               "allow_records_only": True},
                         headers=F.desk_headers())
    assert second.status_code == 200, second.text
    cs2 = second.json()["context_supplied"]
    # the earlier question and answer are in the stored history; with no
    # model nothing was SENT, and the report says so instead of claiming it
    assert cs2["prior_turns_stored"] == 2 and cs2["prior_turns"] == 0
    # the conversation reads back for a refreshed page
    t = client.get("/api/command/agents/xavier/persona/conversations/%s" % cid,
                   headers=F.desk_headers())
    assert t.status_code == 200
    roles = [m["role"] for m in t.json()["messages"]]
    assert roles == ["USER", "ASSISTANT", "USER", "ASSISTANT"]
    # another agent cannot read it
    assert client.get("/api/command/agents/derek/persona/conversations/%s"
                      % cid, headers=F.desk_headers()).status_code == 404


def test_the_context_report_counts_turns_sent_to_a_model():
    from sportsassets.agents import persona_chat as PC
    hist = [{"role": "USER", "body": "q1"}, {"role": "ASSISTANT",
                                             "body": "a1"},
            {"role": "USER", "body": "q2"}]
    got = PC._context_supplied(hist, {"facts": [1, 2, 3], "memory": {
        "policy": {"version_id": "paperparam:X:V2", "threshold_pp": 0.5},
        "lessons": [{"lesson_id": "paperlesson:1"}]}},
        {"mode": PC.MODE_LLM})
    assert got["model_composed"] is True and got["prior_turns"] == 2
    assert got["policy"]["threshold_pp"] == 0.5
    assert got["lessons"] == [{"lesson_id": "paperlesson:1"}]
    assert json.dumps(got)
