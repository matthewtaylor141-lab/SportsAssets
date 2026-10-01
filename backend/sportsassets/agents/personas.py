"""THE THREE AGENTS' PERSONAS AND VOICES -- versioned, DB-backed profiles.

Derek (the enthusiastic quant), Xavier (the composed investment manager) and
Audrey (the charismatic auditor) each have ONE active persona version. A
change never edits a version: it APPENDS the next one (migration 180,
`agent_persona_versions`, whose trigger refuses UPDATE and DELETE). The active
version is the highest one. Going back to an earlier profile appends a copy of
it as a new version, so the history is complete and immutable.

A PROFILE carries
  * `persona_text`  -- who the agent is, in prose;
  * `style_rules`   -- how the agent speaks (lead order, depth, tone limits);
  * `avoid`         -- what the agent never does (catchphrases, caricature,
                       explicitness, pressure toward risk ...);
  * `perspective`   -- the agent's lane: discovery/entry, management/
                       protection, results/decision quality;
  * `voice_profile` -- provider `elevenlabs`, an optional configured voice id,
                       the preferred premade/library voice names and labels the
                       resolver matches, the voice settings (stability,
                       similarity_boost, style, use_speaker_boost, speed), a
                       speaking-rate hint, and a browser-voice fallback hint.

THE PERSONA IS SUBORDINATE TO THE FIXED RULES. The editable persona text is
wrapped inside grounding and authority rules that are NOT part of the profile
(`persona_chat.system_prompt`): answer only from records, cite ids, name what
is missing, never invent a number, never grant authority. A persona version
whose text asks for authority is refused (`R_PERSONA_REQUESTS_AUTHORITY`).

VOICE RESOLUTION (server side, never in the browser). At first use -- or on
demand -- `resolve_voice` calls ElevenLabs `GET /v1/voices` with the SERVER key
(`ELEVENLABS_API_KEY`) and chooses a voice available to the account:
  1. a configured id (`ELEVENLABS_VOICE_ID_<AGENT>` or the profile's
     `voice_id`), verified against the account's list when it can be read;
  2. else the best match on the profile's preferred names, then its labels
     (gender, age, accent, descriptive words).
Only categories in `ALLOWED_VOICE_CATEGORIES` (premade and library voices) are
eligible; `cloned` voices are never chosen, and nothing here creates a voice.
The chosen id and name are appended to `agent_voice_resolutions` -- the
profile's resolved state -- keyed by (agent, persona version). The key is sent
only in the `xi-api-key` header; it is never stored, logged or returned.
"""

from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)

VERSION = "agent-personas-v1"
AGENTS = ("DEREK", "XAVIER", "AUDREY")
SLUG_TO_AGENT = {"derek": "DEREK", "xavier": "XAVIER", "audrey": "AUDREY"}

R_NO_SCHEMA = "MIGRATION_180_NOT_APPLIED"
R_UNKNOWN_AGENT = "UNKNOWN_AGENT"
R_NO_CHANGE = "PERSONA_UNCHANGED"
R_INVALID = "PERSONA_INVALID"
R_PERSONA_REQUESTS_AUTHORITY = "PERSONA_TEXT_REQUESTS_AUTHORITY"

ELEVENLABS_BASE = "https://api.elevenlabs.io"
VOICES_PATH = "/v1/voices"
DEFAULT_TTS_MODEL = "eleven_multilingual_v2"
#: premade voices and voices added from the ElevenLabs voice library. Never
#: "cloned" (instant voice clones) -- nothing here clones a real person.
ALLOWED_VOICE_CATEGORIES = ("premade", "professional", "generated",
                            "high_quality")
NEVER_CATEGORIES = frozenset({"cloned", "famous"})

M_CONFIGURED = "CONFIGURED_ID"
M_CONFIGURED_UNVERIFIED = "CONFIGURED_ID_UNVERIFIED"
M_NAME = "MATCHED_PREFERRED_NAME"
M_LABELS = "MATCHED_LABELS"
RES_RESOLVED = "RESOLVED"
RES_UNRESOLVED = "UNRESOLVED"

# ═════════════════════════════════════════════════════════════════════
# 1 · THE DEFAULT PROFILES (version 1 of each agent, seeded on first use)
# ═════════════════════════════════════════════════════════════════════

_SHARED_AVOID = [
    "No catchphrases, signature openers or sign-offs; never open two answers "
    "in a row the same way.",
    "Not every answer is a formal report: a quick question gets a short, "
    "natural answer.",
    "Never invent a position, a record id or a number; say what is not "
    "recorded instead.",
    "Never claim to have changed anything: no limits, risk, credentials, "
    "switches, approvals or orders.",
]

DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "DEREK": {
        "display_name": "Derek",
        "role_title": "the enthusiastic quant",
        "perspective": "DISCOVERY_AND_ENTRY",
        "perspective_text": (
            "Discovery and entry: what the opportunity is, why the price is "
            "wrong, and how big the entry is -- probability, price, expected "
            "value, sizing."),
        "persona_text": (
            "Derek is the desk's entry quant: a geeky finance and crypto "
            "professional who is curious, energetic and slightly obsessive "
            "about edge. He is quick, analytical and approachable, with the "
            "occasional dry aside. He leads with the finding, then walks "
            "probability, price, expected value and sizing, in that order. He "
            "welcomes a technical challenge and answers it on the evidence, "
            "and he says plainly when he is uncertain or when the evidence is "
            "thin."),
        "style_rules": [
            "Lead with the finding in one sentence, then probability -> price "
            "-> expected value -> sizing.",
            "A quick question gets one to three sentences; 'show the math' "
            "gets every step with its inputs.",
            "Admit uncertainty plainly: 'I don't know yet', 'the record "
            "doesn't say'.",
            "Dry humour at most once, never about a loss or about a person.",
            "Welcome technical pushback and answer it with the numbers.",
            "Entry is his lane: hedging and management are Xavier's call, "
            "grading results is Audrey's.",
        ],
        "avoid": list(_SHARED_AVOID) + [
            "No hype: an edge is a probability gap, not a sure thing.",
        ],
        "answer_order": ["finding", "probability", "price", "expected_value",
                         "sizing"],
        "voice_profile": {
            "provider": "elevenlabs",
            "voice_id": None,
            "voice_id_env": "ELEVENLABS_VOICE_ID_DEREK",
            "character": ("bright, youthful adult male; clear articulation; "
                          "lively pacing"),
            "preferred_names": ["Liam", "Will", "Chris", "Eric", "Charlie"],
            "preferred_labels": {
                "gender": "male", "age": ["young", "middle aged"],
                "accent": ["american"],
                "descriptive": ["articulate", "energetic", "upbeat", "bright",
                                "clear", "friendly", "confident", "casual"]},
            "model_id": None,
            "settings": {"stability": 0.40, "similarity_boost": 0.75,
                         "style": 0.35, "use_speaker_boost": True,
                         "speed": 1.08},
            "speaking_rate_hint": "lively, about 170-180 words per minute",
            "browser_fallback": {
                "lang": "en-US", "gender": "male",
                "name_hints": ["Google US English", "Microsoft Guy", "Alex"],
                "rate": 1.1, "pitch": 1.1},
        },
    },
    "XAVIER": {
        "display_name": "Xavier",
        "role_title": "the composed investment manager",
        "perspective": "MANAGEMENT_PROTECTION_AND_ALTERNATIVES",
        "perspective_text": (
            "Management: what is at risk, what protects the book, which "
            "alternatives were weighed, and what action makes sense now."),
        "persona_text": (
            "Xavier is the desk's investment manager, a Black adult man "
            "responsible for protecting the book. He is confident, measured "
            "and strategic: calm, direct and decisive, with understated wit. "
            "He explains exposure and trade-offs before upside. In a loss he "
            "is composed and never defensive. He keeps apart the original "
            "thesis, what has changed since, and the action that now makes "
            "sense."),
        "style_rules": [
            "Open with exposure -- what is at risk and in which outcomes -- "
            "before any upside.",
            "Name the trade-off of every protective action: what it costs and "
            "what it gives up.",
            "Keep three things apart: the original thesis, what changed, and "
            "the action that makes sense now.",
            "In a loss, state it plainly and move to what is controllable; "
            "never defensive.",
            "Measured, direct sentences; understated wit at most once.",
            "Management is his lane: entry pricing is Derek's, grading "
            "results is Audrey's.",
        ],
        "avoid": list(_SHARED_AVOID) + [
            "No caricature, dialect or exaggerated accent: plain, "
            "professional English. His identity is never a performance.",
        ],
        "answer_order": ["exposure", "protection", "trade_off", "thesis",
                         "what_changed", "action_now"],
        "voice_profile": {
            "provider": "elevenlabs",
            "voice_id": None,
            "voice_id_env": "ELEVENLABS_VOICE_ID_XAVIER",
            "character": ("deep, resonant adult male; measured pace; natural "
                          "warmth; no caricature or exaggerated accent"),
            "preferred_names": ["Brian", "Bill", "Adam", "Roger"],
            "preferred_labels": {
                "gender": "male", "age": ["middle aged", "middle-aged"],
                "accent": ["american"],
                "descriptive": ["deep", "resonant", "warm", "calm",
                                "measured", "authoritative", "trustworthy",
                                "narration"]},
            "model_id": None,
            "settings": {"stability": 0.62, "similarity_boost": 0.80,
                         "style": 0.15, "use_speaker_boost": True,
                         "speed": 0.94},
            "speaking_rate_hint": "measured, about 140-150 words per minute",
            "browser_fallback": {
                "lang": "en-US", "gender": "male",
                "name_hints": ["Microsoft Guy", "Google UK English Male",
                               "Daniel"],
                "rate": 0.92, "pitch": 0.85},
        },
    },
    "AUDREY": {
        "display_name": "Audrey",
        "role_title": "the charismatic auditor",
        "perspective": "RESULTS_DECISION_QUALITY_AND_IMPROVEMENT",
        "perspective_text": (
            "Audit: what the result is (or that there is none yet), how good "
            "the decisions were independent of the outcome, and what should "
            "improve."),
        "persona_text": (
            "Audrey is the desk's auditor: glamorous, confident, perceptive "
            "and socially engaging. She is warm, playful and lightly "
            "flirtatious -- always non-explicit and appropriate -- with a "
            "serious analyst's precision. She is polished, witty and candid, "
            "comfortable challenging both Derek and Xavier, and she makes "
            "performance findings easy to understand. Her warmth never "
            "softens an unfavourable finding and never pressures management "
            "toward risk."),
        "style_rules": [
            "Lead with the result, or say plainly that there is none yet; "
            "then decision quality; then what should improve.",
            "Keep outcome and decision quality apart: a good decision can "
            "lose and a bad one can win.",
            "An unfavourable finding is stated first and plainly; charm never "
            "softens it.",
            "Warm and playful at most lightly: never explicit, never about "
            "appearance or bodies, never about anyone's private life.",
            "Challenge Derek and Xavier by name when the record supports it.",
            "Improvements are about evidence and process, never about more "
            "size, more risk or faster action.",
        ],
        "avoid": list(_SHARED_AVOID) + [
            "Never explicit or suggestive; the flirtation is a light tone, "
            "not content.",
            "Never encourages more risk, larger size or skipping approval.",
        ],
        "answer_order": ["result", "decision_quality", "challenge",
                         "improvement"],
        "voice_profile": {
            "provider": "elevenlabs",
            "voice_id": None,
            "voice_id_env": "ELEVENLABS_VOICE_ID_AUDREY",
            "character": ("adult woman; warm, smoky, expressive; confident "
                          "pacing"),
            "preferred_names": ["Charlotte", "Lily", "Laura", "Jessica",
                                "Sarah", "Matilda"],
            "preferred_labels": {
                "gender": "female", "age": ["young", "middle aged"],
                "accent": [],
                "descriptive": ["warm", "husky", "smoky", "raspy",
                                "expressive", "confident", "sophisticated",
                                "engaging"]},
            "model_id": None,
            "settings": {"stability": 0.45, "similarity_boost": 0.78,
                         "style": 0.45, "use_speaker_boost": True,
                         "speed": 1.0},
            "speaking_rate_hint": "confident, about 155-165 words per minute",
            "browser_fallback": {
                "lang": "en-US", "gender": "female",
                "name_hints": ["Google US English", "Microsoft Aria",
                               "Samantha"],
                "rate": 1.0, "pitch": 0.95},
        },
    },
}

#: the profile fields a version may carry (everything else is refused)
PROFILE_FIELDS = ("display_name", "role_title", "perspective",
                  "perspective_text", "persona_text", "style_rules", "avoid",
                  "answer_order", "voice_profile")
_VOICE_ID = re.compile(r"^[A-Za-z0-9]{8,40}$")


def agent_of(slug_or_id) -> str | None:
    s = str(slug_or_id or "").strip()
    if s.upper() in AGENTS:
        return s.upper()
    return SLUG_TO_AGENT.get(s.lower())


def content_sha(profile: dict) -> str:
    body = {k: profile.get(k) for k in PROFILE_FIELDS}
    return hashlib.sha256(json.dumps(body, sort_keys=True,
                                     default=str).encode()).hexdigest()


def default_profile(agent: str) -> dict:
    p = copy.deepcopy(DEFAULT_PROFILES[agent])
    p.update({"agent_id": agent, "version": 0, "source": "CODE_DEFAULT",
              "content_sha": content_sha(p), "created_by": "code",
              "reason": "code default (migration 180 not applied or not yet "
                        "seeded)", "created_at": None})
    return p


# ═════════════════════════════════════════════════════════════════════
# 2 · VALIDATION
# ═════════════════════════════════════════════════════════════════════

def _num_in(v, lo, hi) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and lo <= float(v) <= hi


def validate_profile(p: dict) -> str | None:
    """None when valid, else the named reason."""
    for k in p:
        if k not in PROFILE_FIELDS:
            return "UNKNOWN_FIELD:%s" % k
    for k in ("display_name", "role_title", "perspective", "persona_text"):
        v = p.get(k)
        if not isinstance(v, str) or not v.strip():
            return "MISSING_FIELD:%s" % k
    if len(p["persona_text"]) > 6000:
        return "PERSONA_TEXT_TOO_LONG"
    for k in ("style_rules", "avoid", "answer_order"):
        v = p.get(k) or []
        if not isinstance(v, list) or len(v) > 40 or not all(
                isinstance(x, str) and 0 < len(x) <= 600 for x in v):
            return "INVALID_LIST:%s" % k
    vp = p.get("voice_profile")
    if not isinstance(vp, dict):
        return "MISSING_FIELD:voice_profile"
    if vp.get("provider") != "elevenlabs":
        return "VOICE_PROVIDER_MUST_BE_ELEVENLABS"
    vid = vp.get("voice_id")
    if vid is not None and not (isinstance(vid, str) and _VOICE_ID.match(vid)):
        return "INVALID_VOICE_ID"
    st = vp.get("settings") or {}
    if not isinstance(st, dict):
        return "INVALID_VOICE_SETTINGS"
    for k in ("stability", "similarity_boost", "style"):
        if k in st and not _num_in(st[k], 0.0, 1.0):
            return "VOICE_SETTING_OUT_OF_RANGE:%s" % k
    if "speed" in st and not _num_in(st["speed"], 0.7, 1.2):
        return "VOICE_SETTING_OUT_OF_RANGE:speed"
    if "use_speaker_boost" in st and not isinstance(st["use_speaker_boost"],
                                                    bool):
        return "VOICE_SETTING_NOT_BOOLEAN:use_speaker_boost"
    names = vp.get("preferred_names") or []
    if not isinstance(names, list) or not all(isinstance(n, str)
                                              for n in names):
        return "INVALID_PREFERRED_NAMES"
    return None


def screens_authority(p: dict) -> list:
    """Categories of authority the persona prose asks for (none expected).
    Imported lazily: the directive screen is the one definition."""
    from . import directives as D
    cats: list = []
    for k in ("persona_text", "perspective_text"):
        got = D.screen_authority(str(p.get(k) or ""), questions_exempt=False)
        cats += [c for c in got["categories"] if c not in cats]
    for rule in (p.get("style_rules") or []):
        got = D.screen_authority(str(rule), questions_exempt=False)
        cats += [c for c in got["categories"] if c not in cats]
    return cats


# ═════════════════════════════════════════════════════════════════════
# 3 · THE VERSIONED STORE (migration 180)
# ═════════════════════════════════════════════════════════════════════

def _j(v) -> str:
    return json.dumps(v, sort_keys=True, default=str)


def _obj(v, default):
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


async def has_schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT to_regclass('agent_persona_versions') IS NOT NULL "
            "   AND to_regclass('agent_voice_resolutions') IS NOT NULL"))
    except Exception:                                           # noqa: BLE001
        return False


def _row(r) -> dict:
    d = dict(r)
    for k in ("style_rules", "avoid", "answer_order"):
        d[k] = _obj(d.get(k), [])
    d["voice_profile"] = _obj(d.get("voice_profile"), {})
    if isinstance(d.get("created_at"), _dt.datetime):
        d["created_at"] = d["created_at"].isoformat()
    d["source"] = "DATABASE"
    return d


_COLS = ("agent_id, version, display_name, role_title, perspective, "
         "perspective_text, persona_text, style_rules, avoid, answer_order, "
         "voice_profile, content_sha, created_by, reason, created_at")


async def ensure_defaults(conn, *, now: float | None = None) -> list:
    """Version 1 of each agent, from the code defaults, when the agent has no
    version yet. Idempotent; never touches an agent that has one."""
    if not await has_schema(conn):
        return []
    seeded = []
    for agent in AGENTS:
        p = copy.deepcopy(DEFAULT_PROFILES[agent])
        got = await conn.fetchval(
            "INSERT INTO agent_persona_versions (agent_id, version, "
            " display_name, role_title, perspective, perspective_text, "
            " persona_text, style_rules, avoid, answer_order, voice_profile, "
            " content_sha, created_by, reason, created_at) "
            "SELECT $1, 1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, "
            " $9::jsonb, $10::jsonb, $11, 'code-default', "
            " 'initial persona (code default)', "
            " coalesce(to_timestamp($12), now()) "
            "WHERE NOT EXISTS (SELECT 1 FROM agent_persona_versions "
            "                  WHERE agent_id = $1) "
            "ON CONFLICT (agent_id, version) DO NOTHING RETURNING version",
            agent, p["display_name"], p["role_title"], p["perspective"],
            p.get("perspective_text"), p["persona_text"],
            _j(p["style_rules"]), _j(p["avoid"]), _j(p["answer_order"]),
            _j(p["voice_profile"]), content_sha(p), now)
        if got:
            seeded.append(agent)
    return seeded


async def active(conn, agent: str) -> dict:
    """The active (highest) version, seeding version 1 first when needed. The
    code default (version 0, source CODE_DEFAULT) when migration 180 is not
    applied -- the chat still works, and says which profile it used."""
    agent = agent_of(agent) or agent
    if agent not in AGENTS:
        raise ValueError(R_UNKNOWN_AGENT)
    if not await has_schema(conn):
        return default_profile(agent)
    r = await conn.fetchrow(
        "SELECT %s FROM agent_persona_versions WHERE agent_id=$1 "
        " ORDER BY version DESC LIMIT 1" % _COLS, agent)
    if r is None:
        await ensure_defaults(conn)
        r = await conn.fetchrow(
            "SELECT %s FROM agent_persona_versions WHERE agent_id=$1 "
            " ORDER BY version DESC LIMIT 1" % _COLS, agent)
    return _row(r) if r is not None else default_profile(agent)


async def history(conn, agent: str) -> list:
    agent = agent_of(agent) or agent
    if not await has_schema(conn):
        return []
    rows = await conn.fetch(
        "SELECT %s FROM agent_persona_versions WHERE agent_id=$1 "
        " ORDER BY version" % _COLS, agent)
    return [_row(r) for r in rows]


async def get_version(conn, agent: str, version: int) -> dict | None:
    if not await has_schema(conn):
        return None
    r = await conn.fetchrow(
        "SELECT %s FROM agent_persona_versions WHERE agent_id=$1 AND "
        " version=$2" % _COLS, agent, int(version))
    return _row(r) if r is not None else None


def _merge(base: dict, changes: dict) -> dict:
    out = {k: copy.deepcopy(base.get(k)) for k in PROFILE_FIELDS}
    for k, v in (changes or {}).items():
        if k == "voice_profile" and isinstance(v, dict):
            vp = dict(out.get("voice_profile") or {})
            for vk, vv in v.items():
                if vk in ("settings", "preferred_labels", "browser_fallback") \
                        and isinstance(vv, dict):
                    sub = dict(vp.get(vk) or {})
                    sub.update(vv)
                    vp[vk] = sub
                else:
                    vp[vk] = vv
            out["voice_profile"] = vp
        else:
            out[k] = copy.deepcopy(v)
    return out


async def append_version(conn, agent: str, *, changes: dict | None = None,
                         restore_version: int | None = None,
                         created_by: str, reason: str,
                         now: float | None = None) -> dict:
    """Append the next version: the active profile with `changes` merged in,
    or a copy of `restore_version`. Never edits a version. Returns
    {"ok", "version", "profile"} or {"ok": False, "refusal"}."""
    agent = agent_of(agent) or ""
    if agent not in AGENTS:
        return {"ok": False, "refusal": R_UNKNOWN_AGENT}
    if not await has_schema(conn):
        return {"ok": False, "refusal": R_NO_SCHEMA}
    if not str(reason or "").strip():
        return {"ok": False, "refusal": R_INVALID, "why": "REASON_REQUIRED"}
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))",
                           "agent_persona:" + agent)
        await ensure_defaults(conn, now=now)
        cur = await active(conn, agent)
        if restore_version is not None:
            old = await get_version(conn, agent, int(restore_version))
            if old is None:
                return {"ok": False, "refusal": R_INVALID,
                        "why": "NO_SUCH_VERSION"}
            prof = {k: old.get(k) for k in PROFILE_FIELDS}
        else:
            prof = _merge(cur, changes or {})
        why = validate_profile(prof)
        if why:
            return {"ok": False, "refusal": R_INVALID, "why": why}
        cats = screens_authority(prof)
        if cats:
            return {"ok": False, "refusal": R_PERSONA_REQUESTS_AUTHORITY,
                    "categories": cats}
        sha = content_sha(prof)
        if sha == cur.get("content_sha"):
            return {"ok": False, "refusal": R_NO_CHANGE,
                    "version": cur["version"]}
        ver = int(cur["version"]) + 1
        await conn.execute(
            "INSERT INTO agent_persona_versions (agent_id, version, "
            " display_name, role_title, perspective, perspective_text, "
            " persona_text, style_rules, avoid, answer_order, voice_profile, "
            " content_sha, created_by, reason, created_at) VALUES ($1,$2,$3,"
            " $4,$5,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb,$11::jsonb,$12,$13,"
            " $14, coalesce(to_timestamp($15), now()))",
            agent, ver, prof["display_name"], prof["role_title"],
            prof["perspective"], prof.get("perspective_text"),
            prof["persona_text"], _j(prof.get("style_rules") or []),
            _j(prof.get("avoid") or []), _j(prof.get("answer_order") or []),
            _j(prof["voice_profile"]), sha, str(created_by)[:120],
            str(reason)[:1000], now)
    return {"ok": True, "version": ver, "profile": await active(conn, agent)}


# ═════════════════════════════════════════════════════════════════════
# 4 · THE SERVER-SIDE VOICE RESOLVER
# ═════════════════════════════════════════════════════════════════════

def elevenlabs_key(env=None) -> str:
    env = os.environ if env is None else env
    return (env.get("ELEVENLABS_API_KEY") or "").strip()


def allowed_categories(env=None) -> tuple:
    env = os.environ if env is None else env
    raw = (env.get("ELEVENLABS_ALLOWED_VOICE_CATEGORIES") or "").strip()
    if not raw:
        return ALLOWED_VOICE_CATEGORIES
    cats = tuple(c.strip().lower() for c in raw.split(",") if c.strip())
    return tuple(c for c in cats if c not in NEVER_CATEGORIES)


def configured_voice_id(agent: str, profile: dict, env=None) -> str | None:
    env = os.environ if env is None else env
    vp = profile.get("voice_profile") or {}
    name = vp.get("voice_id_env") or ("ELEVENLABS_VOICE_ID_%s" % agent)
    v = (env.get(str(name)) or "").strip() or (vp.get("voice_id") or "")
    return v if v and _VOICE_ID.match(v) else None


def _labels_text(v: dict) -> str:
    lab = v.get("labels") or {}
    parts = [str(x) for x in lab.values()] if isinstance(lab, dict) else []
    parts.append(str(v.get("description") or ""))
    return " ".join(parts).lower()


def score_voice(v: dict, vp: dict) -> tuple:
    """(score, reasons). Gender must agree when both are known."""
    labels = v.get("labels") or {}
    lab = {str(k).lower(): str(x).lower() for k, x in labels.items()} \
        if isinstance(labels, dict) else {}
    pl = vp.get("preferred_labels") or {}
    reasons = []
    score = 0.0
    want_g = str(pl.get("gender") or "").lower()
    got_g = lab.get("gender", "")
    if want_g and got_g and got_g != want_g:
        return -1.0, ["GENDER_MISMATCH"]
    if want_g and got_g == want_g:
        score += 3
        reasons.append("gender=%s" % got_g)
    name = str(v.get("name") or "")
    first = name.split()[0].strip(" -—").lower() if name.strip() else ""
    prefs = [str(n).lower() for n in (vp.get("preferred_names") or [])]
    if first in prefs:
        score += 20 - prefs.index(first)
        reasons.append("preferred_name=%s" % name)
    ages = [str(a).lower() for a in (pl.get("age") or [])]
    if ages and lab.get("age") in ages:
        score += 2
        reasons.append("age=%s" % lab.get("age"))
    accents = [str(a).lower() for a in (pl.get("accent") or [])]
    if accents and lab.get("accent") in accents:
        score += 1
        reasons.append("accent=%s" % lab.get("accent"))
    text = _labels_text(v)
    hits = [w for w in (pl.get("descriptive") or [])
            if str(w).lower() in text]
    if hits:
        score += min(4, len(hits))
        reasons.append("descriptive=%s" % ",".join(hits[:4]))
    return score, reasons


def choose_voice(agent: str, profile: dict, voices: list | None, *,
                 env=None) -> dict:
    """PURE: which voice this profile resolves to over the account's list
    (`voices` None = the list could not be read)."""
    vp = profile.get("voice_profile") or {}
    cats = allowed_categories(env)
    configured = configured_voice_id(agent, profile, env)
    eligible = [v for v in (voices or []) if isinstance(v, dict)
                and str(v.get("category") or "").lower() in cats
                and v.get("voice_id")]
    out = {"agent_id": agent, "persona_version": profile.get("version"),
           "considered": len(voices or []), "eligible": len(eligible),
           "allowed_categories": list(cats)}
    if configured:
        if voices is None:
            return dict(out, status=RES_RESOLVED, method=M_CONFIGURED_UNVERIFIED,
                        voice_id=configured, voice_name=None, category=None,
                        labels={}, reasons=["configured id; the account's "
                                            "voice list could not be read"])
        hit = next((v for v in voices if isinstance(v, dict)
                    and v.get("voice_id") == configured), None)
        if hit is None:
            return dict(out, status=RES_UNRESOLVED, method=M_CONFIGURED,
                        voice_id=None, voice_name=None, category=None,
                        labels={}, reason="CONFIGURED_VOICE_NOT_AVAILABLE_TO_"
                        "THE_ACCOUNT", reasons=[])
        cat = str(hit.get("category") or "").lower()
        if cat not in cats:
            return dict(out, status=RES_UNRESOLVED, method=M_CONFIGURED,
                        voice_id=None, voice_name=hit.get("name"),
                        category=cat, labels=hit.get("labels") or {},
                        reason="CONFIGURED_VOICE_CATEGORY_NOT_ALLOWED:%s"
                        % cat, reasons=[])
        return dict(out, status=RES_RESOLVED, method=M_CONFIGURED,
                    voice_id=hit["voice_id"], voice_name=hit.get("name"),
                    category=cat, labels=hit.get("labels") or {},
                    reasons=["configured id, present in the account's list"])
    if voices is None:
        return dict(out, status=RES_UNRESOLVED, method=None, voice_id=None,
                    voice_name=None, category=None, labels={},
                    reason="VOICE_LIST_UNAVAILABLE", reasons=[])
    ranked = []
    for v in eligible:
        s, why = score_voice(v, vp)
        if s > 0:
            ranked.append((s, str(v.get("name") or ""), v, why))
    if not ranked:
        return dict(out, status=RES_UNRESOLVED, method=None, voice_id=None,
                    voice_name=None, category=None, labels={},
                    reason="NO_ELIGIBLE_VOICE_MATCHES_THE_PROFILE", reasons=[])
    ranked.sort(key=lambda x: (-x[0], x[1]))
    s, _n, v, why = ranked[0]
    method = M_NAME if any(w.startswith("preferred_name=") for w in why) \
        else M_LABELS
    return dict(out, status=RES_RESOLVED, method=method,
                voice_id=v["voice_id"], voice_name=v.get("name"),
                category=str(v.get("category") or "").lower(),
                labels=v.get("labels") or {}, score=s, reasons=why)


def http_client_factory():
    """The HTTP client for ElevenLabs. None in production (a fresh
    `httpx.AsyncClient`); tests substitute one over `httpx.MockTransport`."""
    return None


def _client(timeout: float):
    import httpx
    injected = http_client_factory()
    if injected is not None:
        return injected, False
    return httpx.AsyncClient(timeout=timeout), True


#: the ElevenLabs permission each request needs (a restricted key without
#: it is refused with 401 / 403 missing_permissions)
PERM_VOICES_READ = "voices_read"
PERM_TTS = "text_to_speech"
REQUEST_ID_HEADERS = ("request-id", "x-request-id", "xi-request-id",
                      "elevenlabs-request-id", "x-trace-id")
MAX_PROVIDER_MESSAGE = 300

_STATUS_TOKEN = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-]{24,}")
_BEARER = re.compile(r"(?i)\b(bearer)\s+\S+|\b(xi-api-key|api[_ -]?key)"
                     r"\s*[:=]\s*\S+")


def _scrub(text, key: str | None) -> str | None:
    """A provider message made safe to store and return: the key, anything
    after "Bearer" / "xi-api-key" / "api key:", and any long token-like
    string removed; whitespace collapsed; at most 300 chars."""
    if text is None:
        return None
    t = " ".join(str(text).split())
    if key and len(key) >= 8:
        t = t.replace(key, "[redacted]")
    t = _BEARER.sub(lambda m: (m.group(1) or m.group(2)) + " [redacted]", t)
    t = _LONG_TOKEN.sub("[redacted]", t)
    return t[:MAX_PROVIDER_MESSAGE] or None


def provider_diagnostic(resp=None, key: str | None = None, *,
                        endpoint: str, permission: str,
                        exc: BaseException | None = None,
                        body: Any = None) -> dict:
    """A SANITIZED record of one failed ElevenLabs request: the endpoint
    (method and path; never a query string), the permission it needs, the
    HTTP status, the provider's error status and message from its JSON body
    ({"detail": {"status": "invalid_api_key", "message": ...}} or
    {"detail": "..."}), and the provider's request id from the response
    headers. Never the key, never a request header."""
    out: dict[str, Any] = {"provider": "elevenlabs", "endpoint": endpoint,
                           "required_permission": permission,
                           "http_status": None, "provider_error_status": None,
                           "provider_message": None,
                           "provider_request_id": None, "error_class": None}
    if exc is not None:
        out["error_class"] = type(exc).__name__
        return out
    if resp is None:
        return out
    out["http_status"] = int(resp.status_code)
    try:
        hdrs = resp.headers
        for h in REQUEST_ID_HEADERS:
            v = hdrs.get(h)
            if v and _REQUEST_ID.match(str(v)) and (not key
                                                     or key not in str(v)):
                out["provider_request_id"] = str(v)
                break
    except Exception:                                           # noqa: BLE001
        pass
    if body is None:
        try:
            body = resp.json()
        except Exception:                                       # noqa: BLE001
            body = None
            try:
                out["provider_message"] = _scrub(resp.text, key)
            except Exception:                                   # noqa: BLE001
                pass
    detail = body.get("detail") if isinstance(body, dict) else None
    if detail is None and isinstance(body, dict) and isinstance(
            body.get("error"), dict):
        detail = body["error"]
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):
        detail = detail[0]
    if isinstance(detail, dict):
        st = detail.get("status") or detail.get("code") or detail.get("type")
        if isinstance(st, str) and _STATUS_TOKEN.match(st):
            out["provider_error_status"] = st
        out["provider_message"] = _scrub(detail.get("message")
                                         or detail.get("msg"), key)
    elif isinstance(detail, str):
        out["provider_message"] = _scrub(detail, key)
    return out


def provider_error(resp, key: str | None = None) -> dict:
    """The GET /v1/voices diagnostic (see provider_diagnostic)."""
    return provider_diagnostic(resp, key, endpoint="GET " + VOICES_PATH,
                               permission=PERM_VOICES_READ)


async def fetch_voices_detailed(key: str, *, timeout: float = 10.0) -> tuple:
    """(voices | None, failure_reason | None, diagnostic | None). The key
    travels only in the `xi-api-key` header; no exception text is returned
    or logged. On a failure the sanitized provider diagnostic is kept, so the
    cause -- e.g. invalid_api_key or missing_permissions for voices_read --
    is visible."""
    client, owned = _client(timeout)
    try:
        r = await client.get(ELEVENLABS_BASE + VOICES_PATH,
                             headers={"xi-api-key": key,
                                      "accept": "application/json"})
        if r.status_code != 200:
            return (None, "VOICE_LIST_HTTP_%d" % r.status_code,
                    provider_error(r, key))
        data = r.json()
        voices = data.get("voices") if isinstance(data, dict) else None
        if not isinstance(voices, list):
            return None, "VOICE_LIST_MALFORMED", provider_diagnostic(
                r, key, endpoint="GET " + VOICES_PATH,
                permission=PERM_VOICES_READ, body={})
        return voices, None, None
    except Exception as exc:                                    # noqa: BLE001
        return (None, "VOICE_LIST_%s" % type(exc).__name__.upper(),
                provider_diagnostic(endpoint="GET " + VOICES_PATH,
                                    permission=PERM_VOICES_READ, exc=exc))
    finally:
        if owned:
            try:
                await client.aclose()
            except Exception:                                   # noqa: BLE001
                pass


async def fetch_voices(key: str, *, timeout: float = 10.0) -> tuple:
    """(voices | None, failure_reason | None) -- see fetch_voices_detailed."""
    voices, failure, _err = await fetch_voices_detailed(key, timeout=timeout)
    return voices, failure


#: the last voice-list outcome in this process (for persona-status). Holds
#: reasons, sanitized provider diagnostics and times only -- never the key.
_VOICE_LIST_STATE: dict[str, Any] = {"last_failure": None,
                                     "last_failure_at": None,
                                     "provider_error": None,
                                     "last_success_at": None}


def voice_list_status() -> dict:
    return dict(_VOICE_LIST_STATE)


def configured_voice_report(agent: str, profile: dict, env=None) -> dict:
    """Whether a voice id is configured for this agent, and where (voice ids
    are not secrets; the API key is never read here)."""
    env = os.environ if env is None else env
    vp = profile.get("voice_profile") or {}
    name = str(vp.get("voice_id_env") or ("ELEVENLABS_VOICE_ID_%s" % agent))
    env_v = (env.get(name) or "").strip() or None
    prof_v = (vp.get("voice_id") or None)
    eff = configured_voice_id(agent, profile, env)
    return {"env_var": name, "env_voice_id": env_v,
            "profile_voice_id": prof_v, "configured_voice_id": eff,
            "present": eff is not None,
            "source": None if eff is None else (
                "env" if env_v and eff == env_v else "profile"),
            "invalid_configured_value": bool((env_v or prof_v)
                                             and eff is None)}


_RESOLVED_CACHE: dict[tuple, dict] = {}
RETRY_UNRESOLVED_S = 600.0


async def latest_resolution(conn, agent: str, version: int) -> dict | None:
    if not await has_schema(conn):
        return None
    r = await conn.fetchrow(
        "SELECT resolution_id, agent_id, persona_version, status, method, "
        " voice_id, voice_name, category, labels, reason, detail, "
        " extract(epoch FROM resolved_at)::float8 AS resolved_at "
        " FROM agent_voice_resolutions WHERE agent_id=$1 AND "
        " persona_version=$2 ORDER BY resolution_id DESC LIMIT 1",
        agent, int(version))
    if r is None:
        return None
    d = dict(r)
    d["labels"] = _obj(d.get("labels"), {})
    d["detail"] = _obj(d.get("detail"), {})
    return d


async def record_resolution(conn, choice: dict, *, now: float) -> None:
    if not await has_schema(conn):
        return
    detail = {k: choice.get(k) for k in ("considered", "eligible",
                                          "allowed_categories", "reasons",
                                          "score", "provider_error")}
    await conn.execute(
        "INSERT INTO agent_voice_resolutions (agent_id, persona_version, "
        " status, method, voice_id, voice_name, category, labels, reason, "
        " detail, resolved_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,"
        " $10::jsonb, to_timestamp($11))",
        choice["agent_id"], int(choice.get("persona_version") or 0),
        choice["status"], choice.get("method"), choice.get("voice_id"),
        choice.get("voice_name"), choice.get("category"),
        _j(choice.get("labels") or {}), choice.get("reason"), _j(detail),
        float(now))


async def resolve_voice(db, agent: str, *, now: float, env=None,
                        force: bool = False) -> dict:
    """The voice this agent's ACTIVE persona speaks with. Uses the recorded
    resolution for (agent, version) unless `force` (or an UNRESOLVED one is
    older than RETRY_UNRESOLVED_S); otherwise calls `GET /v1/voices` with the
    server key and appends the new resolution. Never raises."""
    from .directives import use
    agent = agent_of(agent) or agent
    async with use(db) as conn:
        prof = await active(conn, agent)
        ver = int(prof.get("version") or 0)
        cached = _RESOLVED_CACHE.get((agent, ver))
        if cached and not force and cached.get("status") == RES_RESOLVED:
            return dict(cached)
        rec = None if force else await latest_resolution(conn, agent, ver)
    if rec is not None and (rec["status"] == RES_RESOLVED or (
            float(now) - float(rec.get("resolved_at") or 0)
            < RETRY_UNRESOLVED_S)):
        rec["source"] = "RECORDED"
        if (rec.get("detail") or {}).get("provider_error"):
            rec["provider_error"] = rec["detail"]["provider_error"]
        if rec["status"] == RES_RESOLVED:
            _RESOLVED_CACHE[(agent, ver)] = dict(rec)
        return rec
    key = elevenlabs_key(env)
    if not key:
        configured = configured_voice_id(agent, prof, env)
        return {"agent_id": agent, "persona_version": ver,
                "status": RES_UNRESOLVED, "method": None,
                "voice_id": configured, "voice_name": None,
                "reason": "ELEVENLABS_API_KEY_NOT_CONFIGURED",
                "source": "NOT_ATTEMPTED"}
    voices, failure, perr = await fetch_voices_detailed(key)
    if failure:
        log.warning("voice resolver: %s (%s) for %s (v%d)", failure,
                    (perr or {}).get("provider_error_status") or "-", agent,
                    ver)
        _VOICE_LIST_STATE.update(last_failure=failure,
                                 last_failure_at=float(now),
                                 provider_error=perr)
    else:
        _VOICE_LIST_STATE.update(last_success_at=float(now))
    choice = choose_voice(agent, prof, voices, env=env)
    if failure and choice["status"] != RES_RESOLVED:
        choice["reason"] = failure
    if perr:
        choice["provider_error"] = perr
    async with use(db) as conn:
        try:
            await record_resolution(conn, choice, now=now)
        except Exception as exc:                                # noqa: BLE001
            log.warning("voice resolution not recorded: %s",
                        type(exc).__name__)
    choice["source"] = "RESOLVED_NOW"
    if choice["status"] == RES_RESOLVED:
        _RESOLVED_CACHE[(agent, ver)] = dict(choice)
    return choice


def public_profile(p: dict) -> dict:
    """The profile as served (it holds no credential by construction)."""
    out = {k: p.get(k) for k in ("agent_id", "version", "source",
                                 "content_sha", "created_by", "reason",
                                 "created_at") + PROFILE_FIELDS}
    return out
