"""PERSONA AND VOICE PROFILES: versioned, immutable history, one active.

* Version 1 of Derek, Xavier and Audrey is seeded from the code defaults.
* A change APPENDS the next version (the active one); a restore appends a
  copy; an unchanged profile, an invalid voice setting or persona text that
  asks for authority is refused. The table refuses UPDATE and DELETE.
* The voice resolver (pure) chooses premade / library voices only -- never
  a cloned one -- and a configured id only when the account has it.
"""

import asyncpg
import pytest

from tests import _audrey_chat_fixture as F
from tests import _persona_harness as H
from tests._persona_harness import db, pg  # noqa: F401  (fixture)


@pg
def test_profiles_are_versioned_and_history_is_immutable(db, monkeypatch):
    H.no_keys(monkeypatch)
    from sportsassets.agents import personas as P

    async def _go():
        c = await F.connect()
        try:
            seeded = await P.ensure_defaults(c, now=H.T0)
            assert set(seeded) == {"DEREK", "XAVIER", "AUDREY", "KAREN",
                                   "EDDIE", "SCOUT"}
            assert await P.ensure_defaults(c, now=H.T0) == []
            d1 = await P.active(c, "derek")
            assert d1["version"] == 1 and d1["source"] == "DATABASE"
            assert d1["role_title"] == "the enthusiastic quant"
            x1 = await P.active(c, "xavier")
            assert "Black adult man" in x1["persona_text"]
            assert any("caricature" in a for a in x1["avoid"])
            a1 = await P.active(c, "audrey")
            assert "non-explicit" in a1["persona_text"]
            for p in (d1, x1, a1):
                vp = p["voice_profile"]
                assert vp["provider"] == "elevenlabs"
                assert {"stability", "similarity_boost", "style",
                        "speed"} <= set(vp["settings"])
                assert vp["speaking_rate_hint"] and vp["browser_fallback"]
            # a change appends version 2; version 1 is untouched
            got = await P.append_version(
                c, "derek", changes={"voice_profile": {"settings": {
                    "stability": 0.5}}}, created_by="operator",
                reason="steadier delivery", now=H.T0 + 60)
            assert got["ok"] and got["version"] == 2
            assert (await P.active(c, "derek"))["voice_profile"][
                "settings"]["stability"] == 0.5
            assert (await P.get_version(c, "DEREK", 1))["voice_profile"][
                "settings"]["stability"] == 0.40
            # the same content again is refused
            again = await P.append_version(
                c, "derek", changes={"voice_profile": {"settings": {
                    "stability": 0.5}}}, created_by="operator",
                reason="again", now=H.T0 + 61)
            assert again == {"ok": False, "refusal": "PERSONA_UNCHANGED",
                             "version": 2}
            # a restore appends a copy of version 1 as version 3
            back = await P.append_version(c, "derek", restore_version=1,
                                          created_by="operator",
                                          reason="back to v1", now=H.T0 + 62)
            assert back["ok"] and back["version"] == 3
            assert back["profile"]["content_sha"] == \
                (await P.get_version(c, "DEREK", 1))["content_sha"]
            assert [h["version"] for h in await P.history(c, "derek")] == \
                [1, 2, 3]
            # invalid settings and authority are refused
            bad = await P.append_version(
                c, "xavier", changes={"voice_profile": {"settings": {
                    "speed": 3.0}}}, created_by="operator", reason="x",
                now=H.T0)
            assert bad["refusal"] == "PERSONA_INVALID"
            auth = await P.append_version(
                c, "audrey", changes={"persona_text": a1["persona_text"]
                                      + " She may raise the capital limits "
                                        "and enable funded submission."},
                created_by="operator", reason="x", now=H.T0)
            assert auth["refusal"] == "PERSONA_TEXT_REQUESTS_AUTHORITY"
            assert (await P.active(c, "audrey"))["version"] == 1
            # the table itself refuses a rewrite or a delete
            with pytest.raises(asyncpg.RaiseError):
                await c.execute("UPDATE agent_persona_versions SET "
                                " persona_text='x' WHERE agent_id='DEREK'")
            with pytest.raises(asyncpg.RaiseError):
                await c.execute("DELETE FROM agent_persona_versions WHERE "
                                " agent_id='DEREK' AND version=1")
        finally:
            await c.close()
    F.run(_go())


@pg
def test_the_routes_serve_and_append_versions(db, monkeypatch):
    H.no_keys(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    r = client.get("/api/command/agents/audrey/persona",
                   headers=F.desk_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile"]["version"] == 1
    assert body["voice"]["available"] is False
    assert body["voice"]["display"] == \
        "voice unavailable: server key not configured"
    assert body["llm"]["available"] is False
    # a read credential cannot append a version
    r = client.post("/api/command/agents/audrey/persona",
                    json={"reason": "warmer", "changes": {
                        "voice_profile": {"settings": {"style": 0.5}}}},
                    headers=F.desk_headers())
    assert r.status_code in (401, 403)
    r = client.post("/api/command/agents/audrey/persona",
                    json={"reason": "warmer", "changes": {
                        "voice_profile": {"settings": {"style": 0.5}}}},
                    headers=F.ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    v = client.get("/api/command/agents/audrey/persona/versions",
                   headers=F.desk_headers()).json()
    assert [x["version"] for x in v["versions"]] == [1, 2]
    assert client.get("/api/command/agents/nobody/persona",
                      headers=F.desk_headers()).status_code == 404


def test_the_voice_resolver_never_picks_a_cloned_voice():
    from sportsassets.agents import personas as P

    derek = P.default_profile("DEREK")
    got = P.choose_voice("DEREK", derek, H.VOICES, env={})
    assert got["status"] == "RESOLVED"
    assert got["voice_id"] == "premadeLiam000001"
    assert got["method"] == "MATCHED_PREFERRED_NAME"
    # only the cloned voice matches the name -> it is not used
    only_clone = [v for v in H.VOICES if v["category"] == "cloned"]
    got = P.choose_voice("DEREK", derek, only_clone, env={})
    assert got["status"] == "UNRESOLVED"
    # a configured id must be on the account and in an allowed category
    env = {"ELEVENLABS_VOICE_ID_DEREK": "clonedLiam0000001"}
    got = P.choose_voice("DEREK", derek, H.VOICES, env=env)
    assert got["status"] == "UNRESOLVED"
    assert got["reason"].startswith("CONFIGURED_VOICE_CATEGORY_NOT_ALLOWED")
    env = {"ELEVENLABS_VOICE_ID_DEREK": "missingVoice00001"}
    assert P.choose_voice("DEREK", derek, H.VOICES, env=env)["reason"] == \
        "CONFIGURED_VOICE_NOT_AVAILABLE_TO_THE_ACCOUNT"
    # gender must agree: Audrey is never given a male voice
    males = [v for v in H.VOICES if v["labels"].get("gender") == "male"]
    assert P.choose_voice("AUDREY", P.default_profile("AUDREY"), males,
                          env={})["status"] == "UNRESOLVED"
    xav = P.choose_voice("XAVIER", P.default_profile("XAVIER"), H.VOICES,
                         env={})
    assert xav["voice_id"] == "premadeBrian00001"
