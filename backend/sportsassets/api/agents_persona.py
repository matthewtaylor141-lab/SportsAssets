"""THE THREE AGENTS' PERSONA CHAT AND VOICE -- the HTTP surface.

  POST /api/command/agents/{agent}/chat                 read credential
       Derek and Xavier. (For Audrey this path is her existing management
       chat in `agents_chat`, which is registered first and keeps its
       contract; Audrey's persona conversation is at /persona/chat.)
  POST /api/command/agents/{agent}/persona/chat         read credential
       All three. Body {message, conversation_id?, context?: {position_id?,
       decision_id?, intent_id?, xavier_decision_id?, demonstration?},
       request_id?, allow_records_only?}. 200 with {status, message_id,
       answer (the transcript), spoken_text, facts (cited), citations,
       missing_evidence, provider, persona, voice}; 503 LLM_UNAVAILABLE when
       ANTHROPIC_API_KEY is absent and allow_records_only is not set; 503
       NO_DATABASE_POOL / MIGRATION_180_NOT_APPLIED without the database.
  GET  /api/command/agents/{agent}/persona/conversations/{conversation_id}
  POST /api/command/agents/{agent}/persona/conversations/{conversation_id}/interrupt
  POST /api/command/agents/{agent}/speech  (alias /speak)  read credential
       Body {message_id} (or {text_id}). Streams audio/mpeg of the STORED
       message's spoken_text (for Audrey also her management-chat replies,
       ids `conv-...:N`, spoken as the normalised stored body). A
       VOICE_NOT_RESOLVED 503 carries `resolver_detail`: the provider's
       HTTP status and error status (e.g. invalid_api_key,
       missing_permissions) -- never the key. 503 VOICE_UNAVAILABLE_SERVER_KEY_NOT_CONFIGURED
       / VOICE_NOT_RESOLVED / VOICE_PROVIDER_FAILED; 404 unknown message;
       409 interrupted message; 429 over the per-agent limits.
  GET  /api/command/agents/{agent}/persona              read
  GET  /api/command/agents/{agent}/persona/versions     read
  POST /api/command/agents/{agent}/persona              control (append a
       version: {reason, changes?} or {reason, restore_version})
  POST /api/command/agents/{agent}/persona/voice/resolve  control
  GET  /api/command/agents/persona-status               read
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..agents import audrey_chat as AC
from ..agents import directives as D
from ..agents import persona_chat as PC
from ..agents import persona_speech as PS
from ..agents import personas as P
from ..db import get_pool
from .agents_chat import require_read, require_write, resolve_role

router = APIRouter()

#: the clock the routes use; tests substitute a controlled one
_clock = time.time


async def _pool():
    try:
        return await get_pool()
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(status_code=503, detail={
            "status": "UNAVAILABLE", "reason": "NO_DATABASE_POOL",
            "error": type(exc).__name__})


def _agent(agent: str) -> str:
    ag = P.agent_of(agent)
    if ag is None:
        raise HTTPException(status_code=404, detail={
            "reason": P.R_UNKNOWN_AGENT, "agents": ["derek", "xavier",
                                                    "audrey"]})
    return ag


class ChatContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position_id: str | None = Field(default=None, max_length=200)
    decision_id: str | None = Field(default=None, max_length=200)
    intent_id: str | None = Field(default=None, max_length=200)
    xavier_decision_id: str | None = Field(default=None, max_length=200)
    demonstration: bool | None = None


class PersonaChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=PC.MAX_MESSAGE_CHARS)
    conversation_id: str | None = Field(default=None, max_length=100)
    context: ChatContext | None = None
    request_id: str | None = Field(default=None, min_length=8,
                                   max_length=128,
                                   pattern=r"^[A-Za-z0-9][\w:.\-]+$")
    allow_records_only: bool = False


class SpeechBody(BaseModel):
    """Only a stored message can be spoken: there is no free-text field."""
    model_config = ConfigDict(extra="forbid")
    message_id: str | None = Field(default=None, max_length=160)
    text_id: str | None = Field(default=None, max_length=160)


class PersonaVersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=1000)
    changes: dict[str, Any] | None = None
    restore_version: int | None = Field(default=None, ge=1)


_CODES = {PC.S_LLM_UNAVAILABLE: 503, PC.S_UNAVAILABLE: 503,
          PC.S_PENDING: 202}


async def _chat(agent: str, body: PersonaChatBody, role: str) -> Response:
    ag = _agent(agent)
    pool = await _pool()
    ctx = body.context.model_dump(exclude_none=True) if body.context else None
    got = await PC.converse(
        pool, agent=ag, role=role, message=body.message,
        conversation_id=body.conversation_id, context=ctx, now=_clock(),
        request_id=body.request_id,
        allow_records_only=body.allow_records_only)
    if got.get("status") == PC.S_ERROR:
        code = 409 if got.get("error") in (D.R_IDEMPOTENCY_MISMATCH,
                                           PC.R_WRONG_AGENT) else 422
        return JSONResponse(status_code=code, content=got,
                            headers={"Cache-Control": "no-store"})
    return JSONResponse(status_code=_CODES.get(got.get("status"), 200),
                        content=got, headers={"Cache-Control": "no-store"})


@router.post("/api/command/agents/{agent}/persona/chat")
async def persona_chat(agent: str, body: PersonaChatBody,
                       role: str = Depends(resolve_role)) -> Response:
    return await _chat(agent, body, role)


@router.post("/api/command/agents/{agent}/chat")
async def agent_chat(agent: str, body: PersonaChatBody,
                     role: str = Depends(resolve_role)) -> Response:
    return await _chat(agent, body, role)


@router.get("/api/command/agents/{agent}/persona/conversations/"
            "{conversation_id}", dependencies=[Depends(require_read)])
async def persona_transcript(agent: str, conversation_id: str,
                             response: Response) -> dict:
    ag = _agent(agent)
    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        got = await PC.transcript(conn, conversation_id)
    if got is None or got["conversation"].get("agent_id") != ag:
        raise HTTPException(status_code=404, detail={
            "reason": "NO_CONVERSATION_WITH_THAT_ID_FOR_THIS_AGENT"})
    return dict(got, read_only=True)


@router.post("/api/command/agents/{agent}/persona/conversations/"
             "{conversation_id}/interrupt")
async def persona_interrupt(agent: str, conversation_id: str,
                            role: str = Depends(resolve_role)) -> dict:
    ag = _agent(agent)
    pool = await _pool()
    async with pool.acquire() as conn:
        got = await PC.transcript(conn, conversation_id)
    if got is None or got["conversation"].get("agent_id") != ag:
        raise HTTPException(status_code=404, detail={
            "reason": "NO_CONVERSATION_WITH_THAT_ID_FOR_THIS_AGENT"})
    turns = await PC.interrupt(pool, conversation_id, by="USER_INTERRUPT",
                               now=_clock())
    return {"conversation_id": conversation_id, "interrupted_turns": turns,
            "role": role}


# ── speech ──────────────────────────────────────────────────────────

def _speech_error(e: PS.SpeechFailure, *, extra: dict | None = None):
    body = {"status": "VOICE_UNAVAILABLE" if e.status == 503 else "REFUSED",
            "reason": e.reason}
    body.update(e.detail or {})
    if e.reason == PS.R_NO_KEY:
        body["display"] = PS.DISPLAY_NO_KEY
    body.update(extra or {})
    return JSONResponse(status_code=e.status, content=body,
                        headers={"Cache-Control": "no-store"})


async def _speech(agent: str, body: SpeechBody) -> Response:
    ag = _agent(agent)
    mid = body.message_id or body.text_id
    if not mid or (body.message_id and body.text_id
                   and body.message_id != body.text_id):
        raise HTTPException(status_code=422, detail={
            "reason": "SEND_EXACTLY_ONE_OF_message_id_OR_text_id"})
    cfg = PS.voice_config()
    pool = await _pool()
    async with pool.acquire() as conn:
        if not await PC.has_schema(conn):
            raise HTTPException(status_code=503, detail={
                "reason": P.R_NO_SCHEMA})
        try:
            m = await PS.load_speakable(conn, agent=ag, message_id=mid)
        except PS.SpeechFailure as e:
            return _speech_error(e)
        persona = await P.active(conn, ag)
    fallback = (persona.get("voice_profile") or {}).get("browser_fallback")
    if not cfg["configured"]:
        return _speech_error(PS.SpeechFailure(cfg["reason"] or PS.R_NO_KEY),
                             extra={"message_id": mid,
                                    "browser_fallback": fallback})
    res = await P.resolve_voice(pool, ag, now=_clock())
    if res.get("status") != P.RES_RESOLVED or not res.get("voice_id"):
        return _speech_error(PS.SpeechFailure(
            PS.R_NOT_RESOLVED, resolver_reason=res.get("reason"),
            resolver_detail=res.get("provider_error"),
            resolver_source=res.get("source"),
            configured_voice=P.configured_voice_report(ag, persona)),
            extra={"message_id": mid, "browser_fallback": fallback})
    spoken = m["spoken_text"][:PS.MAX_SPOKEN_CHARS]
    key = PS.cache_key(agent=ag, persona_version=persona.get("version"),
                       voice_id=res["voice_id"],
                       model_id=PS.model_for(persona, cfg),
                       settings=PS.voice_settings(persona),
                       output_format=cfg["output_format"],
                       spoken_text=spoken)
    headers = {"Cache-Control": "no-store", "X-Speech-Message-Id": mid,
               "X-Speech-Persona-Version": str(persona.get("version")),
               "X-Speech-Voice-Id": str(res["voice_id"]),
               "X-Speech-Spoken-Sha256": hashlib.sha256(
                   spoken.encode()).hexdigest()}
    hit = PS.CACHE.get(key)
    if hit is not None:
        return Response(content=hit, media_type="audio/mpeg",
                        headers=dict(headers, **{"X-Speech-Cache": "HIT"}))
    refused = PS.LIMITS.acquire(ag, concurrency=cfg["concurrency"],
                                rate_per_min=cfg["rate_per_min"])
    if refused:
        return _speech_error(PS.SpeechFailure(refused, status=429))
    try:
        resp, client, owned = await PS.open_stream(
            voice_id=res["voice_id"], spoken_text=spoken, profile=persona,
            cfg=cfg, agent=ag)
    except PS.SpeechFailure as e:
        PS.LIMITS.release(ag)
        return _speech_error(e, extra={"message_id": mid})
    except Exception as exc:                                    # noqa: BLE001
        PS.LIMITS.release(ag)
        return _speech_error(PS.SpeechFailure(
            PS.R_PROVIDER, provider_error=type(exc).__name__.upper()))

    async def _gen():
        chunks: list = []
        complete = False
        try:
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
                yield chunk
            complete = True
        finally:
            try:
                await resp.aclose()
            finally:
                if owned:
                    await client.aclose()
                PS.LIMITS.release(ag)
            if complete:
                PS.CACHE.put(key, b"".join(chunks),
                             budget=cfg["cache_bytes"])

    return StreamingResponse(_gen(), media_type="audio/mpeg",
                             headers=dict(headers,
                                          **{"X-Speech-Cache": "MISS"}))


@router.post("/api/command/agents/{agent}/speech",
             dependencies=[Depends(require_read)])
async def agent_speech(agent: str, body: SpeechBody) -> Response:
    return await _speech(agent, body)


@router.post("/api/command/agents/{agent}/speak",
             dependencies=[Depends(require_read)])
async def agent_speak(agent: str, body: SpeechBody) -> Response:
    return await _speech(agent, body)


@router.post("/api/command/agents/{agent}/transcribe",
             dependencies=[Depends(require_read)])
async def agent_transcribe(agent: str, request: Request) -> Response:
    """The management microphone: the raw recorded clip (Content-Type
    audio/webm, audio/mp4, ...) -> {"text"}. 503 VOICE_UNAVAILABLE with the
    sanitized provider diagnostic when the provider refuses (e.g. the key
    lacks speech_to_text); the page then says so and keeps typing open."""
    _agent(agent)
    audio = await request.body()
    try:
        got = await PS.transcribe(audio, request.headers.get(
            "content-type", ""), agent=agent)
    except PS.SpeechFailure as e:
        return _speech_error(e)
    return JSONResponse(content=dict(got, status="TRANSCRIBED"),
                        headers={"Cache-Control": "no-store"})


# ── persona profiles ────────────────────────────────────────────────

@router.get("/api/command/agents/persona-status",
            dependencies=[Depends(require_read)])
async def persona_status(response: Response) -> dict:
    """Chat and speech state, with each agent's voice configuration: whether
    a voice id is configured (ELEVENLABS_VOICE_ID_<AGENT> or the profile's
    voice_id -- ids are not secrets), the latest recorded resolution with
    its sanitized provider diagnostic, and the last TTS failure. Never the
    key."""
    response.headers["Cache-Control"] = "no-store"
    tts = PS.tts_status()["by_agent"]
    voices: dict[str, Any] = {}
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            for ag in P.AGENTS:
                prof = await P.active(conn, ag)
                res = await P.latest_resolution(
                    conn, ag, int(prof.get("version") or 0))
                voices[ag.lower()] = {
                    "configured_voice": P.configured_voice_report(ag, prof),
                    "latest_resolution": None if res is None else {
                        k: res.get(k) for k in (
                            "status", "method", "voice_id", "voice_name",
                            "category", "reason", "resolved_at")},
                    "voice_list_diagnostic": ((res or {}).get("detail")
                                              or {}).get("provider_error"),
                    "last_tts_failure": tts.get(ag)}
    except Exception as exc:                                    # noqa: BLE001
        for ag in P.AGENTS:
            voices[ag.lower()] = {
                "configured_voice": P.configured_voice_report(
                    ag, P.default_profile(ag)),
                "latest_resolution": None,
                "unreadable": type(exc).__name__,
                "last_tts_failure": tts.get(ag)}
    return {"chat": PC.describe(), "speech": PS.describe(),
            "voices": voices, "personas": P.VERSION}


@router.get("/api/command/agents/{agent}/persona",
            dependencies=[Depends(require_read)])
async def persona_get(agent: str, response: Response) -> dict:
    ag = _agent(agent)
    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        prof = await P.active(conn, ag)
        res = await P.latest_resolution(conn, ag, int(prof.get("version")
                                                      or 0))
        n = len(await P.history(conn, ag))
    vc = PS.voice_config()
    llm = AC.provider_config()
    return {"agent": ag.lower(), "profile": P.public_profile(prof),
            "versions": n, "voice_resolution": res,
            "voice": {"available": vc["configured"], "reason": vc["reason"],
                      "display": vc["display"], "model_id": vc["model_id"]},
            "llm": {"available": llm["configured"], "reason": llm["reason"],
                    "model": llm["model"]}}


@router.get("/api/command/agents/{agent}/persona/versions",
            dependencies=[Depends(require_read)])
async def persona_versions(agent: str, response: Response) -> dict:
    ag = _agent(agent)
    response.headers["Cache-Control"] = "no-store"
    pool = await _pool()
    async with pool.acquire() as conn:
        rows = await P.history(conn, ag)
    return {"agent": ag.lower(), "versions": [P.public_profile(r)
                                              for r in rows]}


@router.post("/api/command/agents/{agent}/persona")
async def persona_append(agent: str, body: PersonaVersionBody,
                         role: str = Depends(require_write)) -> Response:
    ag = _agent(agent)
    pool = await _pool()
    async with pool.acquire() as conn:
        got = await P.append_version(
            conn, ag, changes=body.changes,
            restore_version=body.restore_version, created_by=role,
            reason=body.reason, now=_clock())
    code = 200 if got.get("ok") else (
        503 if got.get("refusal") == P.R_NO_SCHEMA else 409)
    if got.get("profile"):
        got["profile"] = P.public_profile(got["profile"])
    return JSONResponse(status_code=code, content=got)


@router.post("/api/command/agents/{agent}/persona/voice/resolve")
async def persona_resolve(agent: str,
                          role: str = Depends(require_write)) -> dict:
    ag = _agent(agent)
    pool = await _pool()
    got = await P.resolve_voice(pool, ag, now=_clock(), force=True)
    return {"agent": ag.lower(), "resolution": got, "role": role}
