"""THE PERSONA DEMONSTRATION, REPEATABLE: three agents, one set of facts.

For Derek, Xavier and Audrey in turn:
  1. "Walk me through the Yankees position." against the real records. When
     no Yankees position exists the answer says so, and the script then asks
     "Walk me through our current cash and positions."
  2. The same question on the clearly labelled DEMONSTRATION position
     ($1,000 Yankees ML at $0.50; internal 0.60, Pinnacle 0.58, blended 0.59;
     Red Sox +2.5 hedge $800 at $0.40; floors $200 / $2,200 / $200 before
     fees) -- the voice sample.
  3. One follow-up in the same conversation.
Each answer is then spoken through POST /api/command/agents/{agent}/speech
(unless --records-only) and the audio byte count recorded (and the MP3 saved
with --audio-dir). A verification section checks that the three DEMONSTRATION
answers cite the same record ids and the same core numbers and that their
perspectives differ.

    # against the deployed service (credentials from the environment only;
    # nothing secret is printed or written)
    PERSONA_DEMO_ADMIN_TOKEN=... python -m sportsassets.scripts.persona_demo \\
        --base-url https://<sportsassets-api> --out demo.json --md demo.md \\
        --audio-dir demo_audio
    #   or PERSONA_DEMO_COOKIE="bt_command=..." for the command cookie

    # in-process against DATABASE_URL, records-only (no LLM, no audio)
    python -m sportsassets.scripts.persona_demo --in-process --records-only \\
        --out demo.json --md demo.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import uuid

YANKEES = "Walk me through the Yankees position."
CASH = "Walk me through our current cash and positions."
FOLLOW_UPS = {"derek": "Show me the math on the edge.",
              "xavier": "What happens if the Red Sox win?",
              "audrey": "Was the hedge worth it?"}
CORE_NUMBERS = {1000.0, 0.5, 2000.0, 0.6, 0.58, 0.59, 9.0, 180.0, 800.0,
                0.4, 200.0, 2200.0}
PERSPECTIVE = {"derek": ("edge", "sizing", "expected value"),
               "xavier": ("exposure", "trade-off", "thesis"),
               "audrey": ("result", "decision quality", "improve")}


class _Http:
    def __init__(self, base_url: str, *, token: str | None,
                 cookie: str | None):
        import httpx
        headers = {"accept": "application/json"}
        if token:
            headers["x-admin-token"] = token
        if cookie:
            headers["cookie"] = cookie
        self.c = httpx.Client(base_url=base_url.rstrip("/"), headers=headers,
                              timeout=120.0)

    def post(self, path, body):
        return self.c.post(path, json=body)


class _InProcess:
    """The persona and chat routers over DATABASE_URL, with the read role
    granted locally (no credential exists in this mode)."""

    def __init__(self):
        from fastapi import FastAPI
        from starlette.testclient import TestClient

        from ..api import agents_chat as AGC
        from ..api import agents_persona as AGP
        app = FastAPI()
        app.include_router(AGC.router)
        app.include_router(AGP.router)

        async def _role():
            return "command"
        app.dependency_overrides[AGC.require_read] = _role
        app.dependency_overrides[AGC.resolve_role] = _role
        # one event loop for every request (the database pool is bound to
        # the loop that created it)
        self.c = TestClient(app).__enter__()

    def post(self, path, body):
        return self.c.post(path, json=body)


def _numbers(text: str) -> set:
    t = re.sub(r"\[[^\]]*\]", " ", text or "")
    out = set()
    for m in re.finditer(r"(?<![\w.])\$?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|"
                         r"\d+(?:\.\d+)?)", t):
        try:
            out.add(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return out


def _ask(cli, agent, message, *, records_only, conversation_id=None,
         context=None) -> dict:
    body = {"message": message, "allow_records_only": bool(records_only),
            "request_id": "demo-%s-%s" % (agent, uuid.uuid4().hex[:12])}
    if conversation_id:
        body["conversation_id"] = conversation_id
    if context:
        body["context"] = context
    r = cli.post("/api/command/agents/%s/persona/chat" % agent, body)
    try:
        got = r.json()
    except ValueError:
        got = {"status": "HTTP_%d" % r.status_code, "raw": r.text[:300]}
    got["_http_status"] = r.status_code
    return got


def _speak(cli, agent, message_id, *, audio_dir, tag) -> dict:
    if not message_id:
        return {"status": "NO_MESSAGE"}
    r = cli.post("/api/command/agents/%s/speech" % agent,
                 {"message_id": message_id})
    ctype = r.headers.get("content-type", "")
    if r.status_code == 200 and ctype.startswith("audio/"):
        out = {"status": "OK", "bytes": len(r.content),
               "cache": r.headers.get("x-speech-cache"),
               "voice_id": r.headers.get("x-speech-voice-id")}
        if audio_dir:
            p = pathlib.Path(audio_dir)
            p.mkdir(parents=True, exist_ok=True)
            f = p / ("%s_%s.mp3" % (agent, tag))
            f.write_bytes(r.content)
            out["file"] = str(f)
        return out
    try:
        body = r.json()
    except ValueError:
        body = {}
    return {"status": "HTTP_%d" % r.status_code, "bytes": 0,
            "reason": body.get("reason"), "display": body.get("display")}


def _turn(got: dict) -> dict:
    return {"question": None, "status": got.get("status"),
            "http_status": got.get("_http_status"),
            "reason": got.get("reason"),
            "conversation_id": got.get("conversation_id"),
            "message_id": got.get("message_id"),
            "transcript": got.get("answer"),
            "spoken_text": got.get("spoken_text"),
            "cited_facts": got.get("facts") or [],
            "citations": got.get("citations") or [],
            "missing_evidence": got.get("missing_evidence") or [],
            "found": got.get("found"),
            "demonstration": got.get("demonstration"),
            "provider": got.get("provider"),
            "persona": got.get("persona")}


def run(cli, *, records_only: bool, audio_dir: str | None) -> dict:
    out = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "mode": "RECORDS_ONLY" if records_only else "LLM_AND_VOICE",
           "agents": {}}
    for agent in ("derek", "xavier", "audrey"):
        a: dict = {"turns": []}
        real = _ask(cli, agent, YANKEES, records_only=records_only)
        t = dict(_turn(real), question=YANKEES, kind="REAL_RECORDS")
        a["turns"].append(t)
        if real.get("found") is False:
            cash = _ask(cli, agent, CASH, records_only=records_only)
            a["turns"].append(dict(_turn(cash), question=CASH,
                                   kind="CASH_AND_POSITIONS"))
        demo = _ask(cli, agent, YANKEES, records_only=records_only,
                    context={"demonstration": True})
        a["turns"].append(dict(_turn(demo), question=YANKEES,
                               kind="DEMONSTRATION_VOICE_SAMPLE"))
        fu = _ask(cli, agent, FOLLOW_UPS[agent], records_only=records_only,
                  conversation_id=demo.get("conversation_id"))
        a["turns"].append(dict(_turn(fu), question=FOLLOW_UPS[agent],
                               kind="DEMONSTRATION_FOLLOW_UP"))
        for i, turn in enumerate(a["turns"]):
            if records_only:
                turn["audio"] = {"status": "SKIPPED_RECORDS_ONLY",
                                 "bytes": 0}
            else:
                turn["audio"] = _speak(cli, agent, turn["message_id"],
                                       audio_dir=audio_dir,
                                       tag="%d_%s" % (i, turn["kind"]
                                                      .lower()))
        out["agents"][agent] = a
    out["verification"] = verify(out)
    return out


def verify(out: dict) -> dict:
    samples = {ag: next((t for t in a["turns"]
                         if t["kind"] == "DEMONSTRATION_VOICE_SAMPLE"), None)
               for ag, a in out["agents"].items()}
    ids = {ag: sorted({(c["kind"], c["id"]) for c in (t or {}).get(
        "citations") or []}) for ag, t in samples.items()}
    nums = {ag: sorted(CORE_NUMBERS - _numbers((t or {}).get("transcript")
                                               or ""))
            for ag, t in samples.items()}
    persp = {ag: [w for w in PERSPECTIVE[ag]
                  if w not in ((t or {}).get("transcript") or "").lower()]
             for ag, t in samples.items()}
    same_ids = len({json.dumps(v) for v in ids.values()}) == 1 and \
        bool(ids.get("derek"))
    return {"same_record_ids": same_ids,
            "record_ids": ids.get("derek"),
            "core_numbers_missing": nums,
            "all_core_numbers_present": not any(nums.values()),
            "perspective_words_missing": persp,
            "perspectives_differ": not any(persp.values()) and len({
                ((t or {}).get("transcript") or "")[:160]
                for t in samples.values()}) == 3,
            "labelled_demonstration": all(
                "DEMONSTRATION" in ((t or {}).get("transcript") or "")
                for t in samples.values())}


def to_markdown(out: dict) -> str:
    lines = ["# Persona demonstration (%s)" % out["mode"], "",
             "Generated %s. Every DEMONSTRATION answer is about the clearly "
             "labelled DEMONSTRATION position, which is not a production or "
             "paper record." % out["generated_at"], ""]
    for ag, a in out["agents"].items():
        lines += ["## %s" % ag.title(), ""]
        for t in a["turns"]:
            lines += ["**Q (%s):** %s" % (t["kind"], t["question"]), "",
                      "Status: %s; provider: %s; audio: %s bytes (%s)" % (
                          t["status"], (t.get("provider") or {}).get("mode"),
                          (t.get("audio") or {}).get("bytes"),
                          (t.get("audio") or {}).get("status")), "",
                      "> " + (t.get("transcript") or t.get("reason")
                              or "").replace("\n", "\n> "), ""]
            if t.get("cited_facts"):
                lines.append("Cited facts: " + "; ".join(
                    "%s = %s (%s:%s)" % (f["fact_id"], f["text"],
                                         f["source"], f["record_id"])
                    for f in t["cited_facts"]))
                lines.append("")
            if t.get("missing_evidence"):
                lines += ["Missing evidence: " + "; ".join(
                    t["missing_evidence"]), ""]
    v = out["verification"]
    lines += ["## Verification", "",
              "- same record ids across the three DEMONSTRATION answers: %s"
              % v["same_record_ids"],
              "- every core number in every answer: %s"
              % v["all_core_numbers_present"],
              "- perspectives differ (Derek edge/sizing/EV, Xavier exposure/"
              "trade-off/thesis, Audrey result/decision quality/improve): %s"
              % v["perspectives_differ"],
              "- labelled DEMONSTRATION: %s" % v["labelled_demonstration"],
              ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--base-url")
    g.add_argument("--in-process", action="store_true")
    ap.add_argument("--records-only", action="store_true",
                    help="no language model and no audio")
    ap.add_argument("--out", required=True)
    ap.add_argument("--md")
    ap.add_argument("--audio-dir")
    args = ap.parse_args(argv)
    if args.in_process:
        cli = _InProcess()
    else:
        cli = _Http(args.base_url,
                    token=os.environ.get("PERSONA_DEMO_ADMIN_TOKEN") or None,
                    cookie=os.environ.get("PERSONA_DEMO_COOKIE") or None)
    out = run(cli, records_only=args.records_only, audio_dir=args.audio_dir)
    pathlib.Path(args.out).write_text(json.dumps(out, indent=2,
                                                 default=str) + "\n")
    if args.md:
        pathlib.Path(args.md).write_text(to_markdown(out))
    v = out["verification"]
    print("persona demo: mode=%s same_ids=%s numbers=%s perspectives=%s "
          "labelled=%s -> %s" % (out["mode"], v["same_record_ids"],
                                 v["all_core_numbers_present"],
                                 v["perspectives_differ"],
                                 v["labelled_demonstration"], args.out))
    return 0 if (v["same_record_ids"] and v["all_core_numbers_present"]
                 and v["perspectives_differ"]
                 and v["labelled_demonstration"]) else 1


if __name__ == "__main__":
    sys.exit(main())
