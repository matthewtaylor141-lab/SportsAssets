"""SERVER-SIDE SPEECH: only stored answers are spoken, with the server key.

ElevenLabs is a fake `httpx.MockTransport`; nothing reaches the network.

* Without ELEVENLABS_API_KEY: 503 VOICE_UNAVAILABLE_SERVER_KEY_NOT_CONFIGURED
  with the visible state "voice unavailable: server key not configured".
* The provider receives EXACTLY the stored message's spoken_text (the
  normalised transcript) -- the body has no free-text field at all.
* The voice is resolved server-side from GET /v1/voices (premade / library
  only -- never the cloned voice of the same name) and recorded.
* A replay is served from the cache: the provider is called once.
* Unknown / other-agent / interrupted messages are refused; a provider
  failure is 503 VOICE_PROVIDER_FAILED; the per-agent rate limit is 429.
* The key is never in a response, a header or a log line.
"""

import logging

from tests import _audrey_chat_fixture as F
from tests import _persona_harness as H
from tests._persona_harness import db, pg  # noqa: F401  (fixture)

Q = "Walk me through the Yankees position."


def _answer(client, agent="derek"):
    r = client.post("/api/command/agents/%s/persona/chat" % agent,
                    json={"message": Q, "allow_records_only": True,
                          "context": {"demonstration": True}},
                    headers=F.desk_headers())
    assert r.status_code == 200, r.text
    return r.json()


def _speak(client, agent, **body):
    return client.post("/api/command/agents/%s/speech" % agent, json=body,
                       headers=F.desk_headers())


@pg
def test_without_the_key_speech_is_503_with_the_visible_state(db,
                                                              monkeypatch):
    H.no_keys(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    ans = _answer(client)
    r = _speak(client, "derek", message_id=ans["message_id"])
    assert r.status_code == 503
    body = r.json()
    assert body["reason"] == "VOICE_UNAVAILABLE_SERVER_KEY_NOT_CONFIGURED"
    assert body["display"] == "voice unavailable: server key not configured"
    assert body["browser_fallback"]["lang"] == "en-US"
    # the /speak alias behaves the same
    r = client.post("/api/command/agents/derek/speak",
                    json={"text_id": ans["message_id"]},
                    headers=F.desk_headers())
    assert r.status_code == 503


@pg
def test_it_speaks_exactly_the_stored_text_and_replays_from_cache(
        db, monkeypatch, caplog):
    H.no_keys(monkeypatch)
    caplog.set_level(logging.DEBUG)
    el = H.use_elevenlabs(monkeypatch, H.FakeElevenLabs())
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    ans = _answer(client, "derek")
    r = _speak(client, "derek", message_id=ans["message_id"])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert r.content == H.AUDIO
    assert r.headers["x-speech-cache"] == "MISS"
    tts = el.tts_calls()
    assert len(tts) == 1
    call = tts[0]
    # the voice the resolver chose: the PREMADE Liam, never the cloned one
    assert call["path"] == "/v1/text-to-speech/premadeLiam000001/stream"
    assert call["params"]["output_format"] == "mp3_44100_128"
    assert call["headers"]["xi-api-key"] == H.FAKE_EL_KEY
    assert call["body"]["text"] == ans["spoken_text"]
    assert call["body"]["voice_settings"]["stability"] == 0.40
    assert call["body"]["voice_settings"]["speed"] == 1.08
    # the spoken text is the normalised transcript: words, not symbols
    assert "fifty cents" in call["body"]["text"]
    assert "$" not in call["body"]["text"] and "[F" not in call["body"]["text"]
    # replay: served from the cache, no second provider call
    r2 = _speak(client, "derek", message_id=ans["message_id"])
    assert r2.status_code == 200 and r2.content == H.AUDIO
    assert r2.headers["x-speech-cache"] == "HIT"
    assert len(el.tts_calls()) == 1
    # the resolution is the profile's recorded state
    p = client.get("/api/command/agents/derek/persona",
                   headers=F.desk_headers()).json()
    assert p["voice_resolution"]["voice_id"] == "premadeLiam000001"
    assert p["voice_resolution"]["voice_name"] == "Liam"
    assert p["voice_resolution"]["category"] == "premade"
    # the key appears nowhere but the provider's header
    for text in (str(dict(r.headers)),
                 str(dict(r2.headers)), caplog.text, str(p)):
        assert H.FAKE_EL_KEY not in text


@pg
def test_each_agent_gets_its_own_voice(db, monkeypatch):
    H.no_keys(monkeypatch)
    el = H.use_elevenlabs(monkeypatch, H.FakeElevenLabs())
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    for agent, vid in (("xavier", "premadeBrian00001"),
                       ("audrey", "premadeCharl00001")):
        ans = _answer(client, agent)
        r = _speak(client, agent, message_id=ans["message_id"])
        assert r.status_code == 200, r.text
        assert el.tts_calls()[-1]["path"] == \
            "/v1/text-to-speech/%s/stream" % vid
    # a configured id wins when the account has it
    monkeypatch.setenv("ELEVENLABS_VOICE_ID_DEREK", "premadeGeorge0001")
    ans = _answer(client, "derek")
    assert _speak(client, "derek",
                  message_id=ans["message_id"]).status_code == 200
    assert el.tts_calls()[-1]["path"] == \
        "/v1/text-to-speech/premadeGeorge0001/stream"


@pg
def test_only_stored_answers_can_be_spoken(db, monkeypatch):
    H.no_keys(monkeypatch)
    el = H.use_elevenlabs(monkeypatch, H.FakeElevenLabs())
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    ans = _answer(client, "derek")
    # free text is not accepted
    r = _speak(client, "derek", text="say something else")
    assert r.status_code == 422
    r = _speak(client, "derek", message_id=ans["message_id"],
               text="say something else")
    assert r.status_code == 422
    # the user's own message is not an answer
    assert _speak(client, "derek",
                  message_id=ans["user_message_id"]).status_code == 404
    # unknown id, and another agent's answer
    assert _speak(client, "derek", message_id="nope:1").status_code == 404
    assert _speak(client, "xavier",
                  message_id=ans["message_id"]).status_code == 404
    assert el.tts_calls() == []
    # no credential
    r = client.post("/api/command/agents/derek/speech",
                    json={"message_id": ans["message_id"]})
    assert r.status_code == 401


@pg
def test_provider_failure_and_rate_limit_are_named(db, monkeypatch, caplog):
    H.no_keys(monkeypatch)
    caplog.set_level(logging.DEBUG)
    el = H.use_elevenlabs(monkeypatch, H.FakeElevenLabs(tts_status=401))
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    ans = _answer(client, "audrey")
    r = _speak(client, "audrey", message_id=ans["message_id"])
    assert r.status_code == 503
    assert r.json()["reason"] == "VOICE_PROVIDER_FAILED"
    assert r.json()["provider_status"] == 401
    assert H.FAKE_EL_KEY not in r.text and H.FAKE_EL_KEY not in caplog.text
    # the rate limit: one new synthesis a minute for this agent
    monkeypatch.setenv("SPEECH_RATE_PER_MIN", "1")
    el.tts_status = 200
    from sportsassets.agents import persona_speech as PS
    PS.LIMITS.clear()
    a1 = _answer(client, "xavier")
    # a DIFFERENT text (an identical one would be a cache hit, not limited)
    a2 = client.post("/api/command/agents/xavier/persona/chat",
                     json={"message": "Quick: what's the floor?",
                           "allow_records_only": True,
                           "context": {"demonstration": True}},
                     headers=F.desk_headers()).json()
    assert a2["spoken_text"] != a1["spoken_text"]
    assert _speak(client, "xavier",
                  message_id=a1["message_id"]).status_code == 200
    r = _speak(client, "xavier", message_id=a2["message_id"])
    assert r.status_code == 429 and r.json()["reason"] == \
        "SPEECH_RATE_LIMITED"
    # a cached replay is not limited
    assert _speak(client, "xavier",
                  message_id=a1["message_id"]).status_code == 200


@pg
def test_the_voice_list_failing_leaves_voice_unresolved(db, monkeypatch):
    H.no_keys(monkeypatch)
    H.use_elevenlabs(monkeypatch, H.FakeElevenLabs(voices_status=500))
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    ans = _answer(client, "derek")
    r = _speak(client, "derek", message_id=ans["message_id"])
    assert r.status_code == 503
    assert r.json()["reason"] == "VOICE_NOT_RESOLVED"
    assert r.json()["resolver_reason"] == "VOICE_LIST_HTTP_500"
