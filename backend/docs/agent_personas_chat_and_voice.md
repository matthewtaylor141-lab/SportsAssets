# Derek, Xavier and Audrey: persona chat and server-side voice

This is the contract for the UI branch (`ag-cc-ui`). The code is in
`sportsassets/agents/{personas,persona_facts,persona_chat,persona_speech,speech_text}.py`
and `sportsassets/api/agents_persona.py`. The tables are in migration 180.

## Credentials (server side only)

| Env var on `sportsassets-api` | Enables | Without it |
|---|---|---|
| `ANTHROPIC_API_KEY` | persona answers written by the model (Anthropic SDK 1.9.0, model `AUDREY_MODEL` or `claude-opus-5-5`) | chat returns **503 `LLM_UNAVAILABLE`**, or a records-only answer when the request sends `allow_records_only: true` |
| `ELEVENLABS_API_KEY` | `audio/mpeg` speech | speech returns **503 `VOICE_UNAVAILABLE_SERVER_KEY_NOT_CONFIGURED`**, display text "voice unavailable: server key not configured" |

These are optional: `ELEVENLABS_VOICE_ID_DEREK`, `ELEVENLABS_VOICE_ID_XAVIER`,
`ELEVENLABS_VOICE_ID_AUDREY` (pin a voice), `ELEVENLABS_MODEL_ID`
(default `eleven_multilingual_v2`), `ELEVENLABS_OUTPUT_FORMAT` (default
`mp3_44100_128`), `SPEECH_CONCURRENCY` (default 2 per agent),
`SPEECH_RATE_PER_MIN` (default 20 per agent), `SPEECH_CACHE_BYTES` and
`PERSONA_VOICE=off`.

Neither key ever reaches the browser, a response, a stored row or a log line.

## Chat

`POST /api/command/agents/{agent}/persona/chat` is available for `derek`,
`xavier` and `audrey`. It accepts the command read credential (the
`bt_command` cookie, `X-Desk-Token` or `X-Admin-Token`); a control credential
is needed only for Audrey's directives.

`POST /api/command/agents/{derek|xavier}/chat` is the same endpoint. For
Audrey, `/api/command/agents/audrey/chat` remains her existing management chat
and keeps its current contract. Her persona chat is at `/persona/chat`.

Request:
```json
{"message": "Walk me through the Yankees position.",
 "conversation_id": "pc-xavier-…",          // optional; omit to start one
 "context": {"position_id": "…", "decision_id": "…", "intent_id": "…",
             "xavier_decision_id": "…", "demonstration": false},
 "request_id": "…",                          // optional idempotency key
 "allow_records_only": false}
```
`context` carries the page's selected position. It persists for the
conversation, so follow-up questions ("show me the math") keep it. Setting
`{"demonstration": true}` selects the labelled DEMONSTRATION position.

200 response (`status` is `ANSWERED`, `INTERRUPTED`, `REFUSED`,
`DIRECTIVE_RECORDED` or `REQUIRES_OPERATOR_CREDENTIAL`):
```json
{"status": "ANSWERED", "agent": "xavier", "conversation_id": "…",
 "user_message_id": "…:0", "message_id": "…:1",
 "answer": "<the transcript to show>", "text": "<same>",
 "spoken_text": "<its pronunciation-normalised form>",
 "facts": [{"fact_id": "F3", "source": "derek_entry_decisions",
            "record_id": "…", "field": "p_blended", "value": 0.59,
            "text": "blended probability 0.59"}],
 "citations": [{"kind": "derek_entry_decisions", "id": "…"}],
 "missing_evidence": ["…"], "found": true, "demonstration": false,
 "depth": "QUICK|NORMAL|MATH", "provider": {"mode": "LLM|RECORDS_ONLY", "disclosure": "…"},
 "persona": {"version": 1, "display_name": "Xavier"},
 "voice": {"available": false, "display": "voice unavailable: server key not configured",
           "speech_path": "/api/command/agents/xavier/speech"},
 "interrupted_turns": []}
```
Other responses:
- 503 `{"status": "LLM_UNAVAILABLE", "reason": "LLM_UNAVAILABLE_SERVER_KEY_NOT_CONFIGURED", "display": "AI unavailable: server key not configured"}`. Nothing is stored.
- 503 `NO_DATABASE_POOL` or `MIGRATION_180_NOT_APPLIED`.
- 422 for bad input, 409 for a reused `request_id` or another agent's conversation, 202 while the same `request_id` is still in flight.

Grounding rules:
- The three agents get one fact list, read from the database. It covers the
  paper ledger when its tables exist (otherwise "paper ledger not in this
  build"), funded positions and desk cash, Derek's V2 entry decisions,
  Xavier's decisions, standing orders with their payoff tables, Audrey's
  audits and agent status.
- The answer cites facts as `[F#]`.
- A model reply that states a figure no fact holds, or that claims an action,
  is discarded, and the records-only answer is returned
  (`provider.failure = UNGROUNDED_FIGURE | CLAIMED_AN_ACTION`).
- Authority requests are refused by every agent.
- Only Audrey records directives, through the existing directive path.

Transcript: `GET /api/command/agents/{agent}/persona/conversations/{conversation_id}`
returns the messages (each with `body`, `spoken_text`, `status` and `facts`)
and the turns.

Interrupt: `POST /api/command/agents/{agent}/persona/conversations/{conversation_id}/interrupt`
cancels the answer in flight. A new chat message in the same conversation
does the same. The partial answer is stored with status `INTERRUPTED`, and
its original POST returns `status: "INTERRUPTED"` with `partial: true`.

## Speech

`POST /api/command/agents/{agent}/speech` (alias `/speak`) accepts the command
read credential. The body is `{"message_id": "<an assistant message id>"}`, or
`{"text_id": …}`. No free-text field exists, so only a stored answer can be
spoken.

- 200: streamed `audio/mpeg` of that message's `spoken_text`. The headers are
  `X-Speech-Cache: HIT|MISS`, `X-Speech-Message-Id`,
  `X-Speech-Persona-Version`, `X-Speech-Voice-Id` and
  `X-Speech-Spoken-Sha256`.
- 503: `reason` is `VOICE_UNAVAILABLE_SERVER_KEY_NOT_CONFIGURED` (with
  `display` and `browser_fallback`), `VOICE_NOT_RESOLVED` or
  `VOICE_PROVIDER_FAILED` (with `provider_status` or `provider_error`).
- 404: an unknown message, a user message, or another agent's message.
- 409: an interrupted or empty message.
- 429: `SPEECH_RATE_LIMITED` or `SPEECH_CONCURRENCY_LIMITED`. A cache hit is
  never limited.

Page contract:
1. After each assistant message whose `status` is `ANSWERED`, if the user has
   not muted voice, POST `/speech` with its `message_id`.
2. Play the response through an `<audio>` element and call
   `attachAudio(audioEl)` so the mouth follows the real audio.
3. Mute means sending no request.
4. Replay means sending the same request again. The server returns the cached
   audio (`X-Speech-Cache: HIT`) and makes no second provider call.
5. Interrupt means aborting the fetch (`AbortController`), pausing the audio,
   and then POSTing `/interrupt` if an answer is still in flight.
6. On a 503, show the `display` text. `browser_fallback` gives an optional
   `speechSynthesis` voice hint.

## Personas

- `GET /api/command/agents/{agent}/persona` returns the active profile, the
  recorded voice resolution, and whether voice and the LLM are available.
- `GET /api/command/agents/{agent}/persona/versions` returns the full history.
- `POST /api/command/agents/{agent}/persona` (control credential) takes
  `{"reason": …, "changes": {…}}` or `{"reason": …, "restore_version": n}`
  and appends a new version. Versions are never edited.
- `POST /api/command/agents/{agent}/persona/voice/resolve` (control
  credential) re-runs the resolver. It calls `GET /v1/voices` with the server
  key, considers premade and library voices only (never `cloned`), and
  records the chosen id and name.
- `GET /api/command/agents/persona-status` returns the service description.

## Demonstration

Against the deployed service:
`python -m sportsassets.scripts.persona_demo --base-url … --out demo.json --md demo.md --audio-dir audio`
(credentials come from `PERSONA_DEMO_ADMIN_TOKEN` or `PERSONA_DEMO_COOKIE`).

Locally, records only (no LLM, no audio):
`--in-process --records-only`.
