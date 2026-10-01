"""SERVER-SIDE SPEECH FOR DEREK, XAVIER AND AUDREY (ElevenLabs).

The browser never holds a speech credential. `POST /api/command/agents/
{agent}/speech` names a STORED assistant message; this module streams
`audio/mpeg` for that message's stored `spoken_text` -- the pronunciation-
normalised form of exactly the transcript the page shows -- through
ElevenLabs' streaming text-to-speech endpoint with the SERVER key
(`ELEVENLABS_API_KEY`), in the voice the agent's active persona resolved to.

  * No free text: nothing but a stored message can be spoken, so the audio
    and the transcript always match.
  * No key -> 503 VOICE_UNAVAILABLE_SERVER_KEY_NOT_CONFIGURED ("voice
    unavailable: server key not configured"); a provider failure -> 503
    VOICE_PROVIDER_FAILED with the HTTP status or error class. The key is sent
    only in the `xi-api-key` header: never logged, stored or returned.
  * A replay is served from an in-process cache keyed by (agent, persona
    version, voice id, model, voice settings, output format, sha256 of the
    spoken text) -- no second provider call.
  * Per-agent limits: at most SPEECH_CONCURRENCY syntheses in flight and
    SPEECH_RATE_PER_MIN new syntheses a minute (cache hits are free); over
    either -> 429.
"""

from __future__ import annotations

import collections
import hashlib
import json
import logging
import os
import time

from . import personas as P

log = logging.getLogger(__name__)

VERSION = "persona-speech-v1"
TTS_PATH = "/v1/text-to-speech/{voice_id}/stream"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"

R_NO_KEY = "VOICE_UNAVAILABLE_SERVER_KEY_NOT_CONFIGURED"
DISPLAY_NO_KEY = "voice unavailable: server key not configured"
R_NOT_RESOLVED = "VOICE_NOT_RESOLVED"
R_PROVIDER = "VOICE_PROVIDER_FAILED"
R_RATE = "SPEECH_RATE_LIMITED"
R_BUSY = "SPEECH_CONCURRENCY_LIMITED"
R_NOT_FOUND = "NO_STORED_ASSISTANT_MESSAGE_WITH_THAT_ID"
R_NOT_SPEAKABLE = "MESSAGE_IS_NOT_A_COMPLETE_ANSWER"
R_WRONG_AGENT = "MESSAGE_BELONGS_TO_ANOTHER_AGENT"
R_EMPTY = "NOTHING_TO_SPEAK"

MAX_SPOKEN_CHARS = 5000


def _int_env(env, name, default, lo, hi) -> int:
    try:
        v = int((env or os.environ).get(name) or default)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def voice_config(env=None) -> dict:
    """Whether speech can run, and how. Never contains the key."""
    env = os.environ if env is None else env
    key = P.elevenlabs_key(env)
    disabled = (env.get("PERSONA_VOICE") or "").strip().lower() in (
        "off", "0", "false", "disabled", "none")
    reason = None if key and not disabled else (
        "DISABLED_BY_PERSONA_VOICE" if key else R_NO_KEY)
    return {"configured": bool(key) and not disabled, "key_present": bool(key),
            "provider": "elevenlabs", "reason": reason,
            "display": None if reason is None else (
                DISPLAY_NO_KEY if reason == R_NO_KEY else
                "voice unavailable: disabled on this service"),
            "model_id": (env.get("ELEVENLABS_MODEL_ID") or "").strip()
            or P.DEFAULT_TTS_MODEL,
            "output_format": (env.get("ELEVENLABS_OUTPUT_FORMAT") or "")
            .strip() or DEFAULT_OUTPUT_FORMAT,
            "timeout_s": float(_int_env(env, "ELEVENLABS_TIMEOUT_S", 30, 3,
                                        120)),
            "concurrency": _int_env(env, "SPEECH_CONCURRENCY", 2, 1, 16),
            "rate_per_min": _int_env(env, "SPEECH_RATE_PER_MIN", 20, 1, 600),
            "cache_bytes": _int_env(env, "SPEECH_CACHE_BYTES",
                                    48 * 1024 * 1024, 0, 1024 ** 3)}


class SpeechFailure(Exception):
    def __init__(self, reason: str, status: int = 503, **detail):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.detail = detail


# ── the cache (replay never calls the provider again) ───────────────

class SpeechCache:
    def __init__(self):
        self._d: collections.OrderedDict[str, bytes] = \
            collections.OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> bytes | None:
        v = self._d.get(key)
        if v is None:
            self.misses += 1
            return None
        self._d.move_to_end(key)
        self.hits += 1
        return v

    def put(self, key: str, audio: bytes, *, budget: int) -> None:
        if not audio or len(audio) > budget:
            return
        old = self._d.pop(key, None)
        if old is not None:
            self._bytes -= len(old)
        self._d[key] = audio
        self._bytes += len(audio)
        while self._bytes > budget and self._d:
            _k, v = self._d.popitem(last=False)
            self._bytes -= len(v)

    def clear(self):
        self._d.clear()
        self._bytes = 0
        self.hits = self.misses = 0

    def stats(self) -> dict:
        return {"entries": len(self._d), "bytes": self._bytes,
                "hits": self.hits, "misses": self.misses}


CACHE = SpeechCache()


def cache_key(*, agent: str, persona_version, voice_id: str, model_id: str,
              settings: dict, output_format: str, spoken_text: str) -> str:
    text_sha = hashlib.sha256(spoken_text.encode()).hexdigest()
    blob = json.dumps([agent, persona_version, voice_id, model_id,
                       settings, output_format, text_sha], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


# ── per-agent limits ────────────────────────────────────────────────

class AgentLimiter:
    def __init__(self):
        self.in_flight: dict[str, int] = collections.defaultdict(int)
        self.recent: dict[str, collections.deque] = \
            collections.defaultdict(collections.deque)

    def acquire(self, agent: str, *, concurrency: int, rate_per_min: int,
                now: float | None = None) -> str | None:
        """None when admitted (the caller MUST release), else the refusal."""
        now = time.monotonic() if now is None else now
        q = self.recent[agent]
        while q and now - q[0] > 60.0:
            q.popleft()
        if self.in_flight[agent] >= concurrency:
            return R_BUSY
        if len(q) >= rate_per_min:
            return R_RATE
        self.in_flight[agent] += 1
        q.append(now)
        return None

    def release(self, agent: str) -> None:
        self.in_flight[agent] = max(0, self.in_flight[agent] - 1)

    def clear(self):
        self.in_flight.clear()
        self.recent.clear()


LIMITS = AgentLimiter()


# ── the provider ────────────────────────────────────────────────────

def http_client_factory():
    """The HTTP client for ElevenLabs TTS. None in production (a fresh
    `httpx.AsyncClient`); tests substitute one over `httpx.MockTransport`."""
    return None


def voice_settings(profile: dict) -> dict:
    st = dict((profile.get("voice_profile") or {}).get("settings") or {})
    return {k: st[k] for k in ("stability", "similarity_boost", "style",
                               "use_speaker_boost", "speed") if k in st}


def model_for(profile: dict, cfg: dict) -> str:
    return (profile.get("voice_profile") or {}).get("model_id") \
        or cfg["model_id"]


async def open_stream(*, voice_id: str, spoken_text: str, profile: dict,
                      cfg: dict, env=None):
    """Send the TTS request and return (response, client, owned) once the
    provider has ANSWERED 2xx; the caller iterates `response.aiter_bytes()`
    and closes both. Raises SpeechFailure (503) otherwise. The key is read
    here, put in one header, and nowhere else."""
    import httpx
    key = P.elevenlabs_key(env)
    if not key:
        raise SpeechFailure(R_NO_KEY)
    injected = http_client_factory()
    client = injected or httpx.AsyncClient(timeout=cfg["timeout_s"])
    owned = injected is None
    url = P.ELEVENLABS_BASE + TTS_PATH.format(voice_id=voice_id)
    body = {"text": spoken_text, "model_id": model_for(profile, cfg),
            "voice_settings": voice_settings(profile)}
    try:
        req = client.build_request(
            "POST", url, params={"output_format": cfg["output_format"]},
            headers={"xi-api-key": key, "accept": "audio/mpeg",
                     "content-type": "application/json"},
            content=json.dumps(body))
        resp = await client.send(req, stream=True)
    except Exception as exc:                                    # noqa: BLE001
        if owned:
            await client.aclose()
        why = type(exc).__name__.upper()
        log.warning("speech provider request failed: %s", why)
        raise SpeechFailure(R_PROVIDER, provider_error=why) from None
    if resp.status_code // 100 != 2:
        code = resp.status_code
        try:
            await resp.aclose()
        finally:
            if owned:
                await client.aclose()
        log.warning("speech provider answered HTTP %d", code)
        raise SpeechFailure(R_PROVIDER, provider_status=code)
    return resp, client, owned


async def load_speakable(conn, *, agent: str, message_id: str) -> dict:
    """The stored assistant message to speak. Raises SpeechFailure (404 /
    409) when it is not one.

    Two stores hold assistant text: the persona chat's
    `agent_chat_messages` (all three agents), and -- for AUDREY only -- her
    management chat's `audrey_messages` (POST /api/command/agents/audrey/
    chat, ids `conv-...:N`). From the latter only her own replies (role
    AUDREY) are spoken, as the pronunciation-normalised form of exactly the
    stored body; a management (user) or tool message is not speakable, and
    there is still no free text."""
    from . import persona_chat as PC
    m = await PC.message(conn, message_id)
    if m is None and agent == "AUDREY":
        m = await _audrey_management_message(conn, message_id)
    if m is None or m.get("role") != "ASSISTANT":
        raise SpeechFailure(R_NOT_FOUND, status=404)
    if m.get("agent_id") != agent:
        raise SpeechFailure(R_WRONG_AGENT, status=404)
    if m.get("status") == "INTERRUPTED":
        raise SpeechFailure(R_NOT_SPEAKABLE, status=409,
                            message_status=m.get("status"))
    spoken = (m.get("spoken_text") or "").strip()
    if not spoken:
        raise SpeechFailure(R_EMPTY, status=409)
    return m


async def _audrey_management_message(conn, message_id: str) -> dict | None:
    """One of Audrey's own stored replies in the management chat
    (`audrey_messages`, migration 156), shaped like a persona-chat message,
    or None. Read-only."""
    from . import audrey_chat as AC
    from .speech_text import normalise
    if not await AC.has_schema(conn):
        return None
    r = await conn.fetchrow(
        "SELECT message_id, conversation_id, seq, role, body, outcome "
        "  FROM audrey_messages WHERE message_id = $1", str(message_id))
    if r is None:
        return None
    role = {"AUDREY": "ASSISTANT", "MANAGEMENT": "USER"}.get(r["role"],
                                                           r["role"])
    return {"message_id": r["message_id"],
            "conversation_id": r["conversation_id"], "seq": r["seq"],
            "agent_id": "AUDREY", "role": role, "status": "COMPLETE",
            "outcome": r["outcome"], "body": r["body"],
            "spoken_text": normalise(r["body"] or "") if role == "ASSISTANT"
            else None, "store": "audrey_messages"}


def describe(env=None) -> dict:
    cfg = voice_config(env)
    return {"version": VERSION,
            "voice": {k: cfg[k] for k in ("configured", "key_present",
                                          "provider", "reason", "display",
                                          "model_id", "output_format",
                                          "concurrency", "rate_per_min")},
            "cache": CACHE.stats(),
            "voice_list": P.voice_list_status(),
            "speaks": "only stored assistant messages (their spoken_text): "
                      "persona-chat answers, and for Audrey also her "
                      "management-chat (agents_chat) answers"}
