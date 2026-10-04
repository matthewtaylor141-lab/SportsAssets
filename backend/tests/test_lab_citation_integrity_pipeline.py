"""LAB-B: THE CITATION-INTEGRITY GATE INSIDE THE REAL ANSWER PIPELINE
(persona_chat.converse -> migration 243), its scorecard, its endpoint and its
anti-lookahead reader.

* A model reply citing the wrong fact for a figure exactly one fact holds is
  RE-CITED before it is stored; the stored answer, its stored facts and the
  ledger all show the repair.
* A model reply whose wrong citation cannot be repaired (three facts hold the
  figure) is DISCARDED: the records-only answer is published with the
  integrity reason; the ledger keeps both checks.
* A records-only answer is verified too (all PASS for the demonstration).
* The ledger is append-only and its invariants are CHECK constraints.
* citation_metrics (the function r30b's scorecards read) measures it; a row
  recorded after the clock is invisible (one point-in-time accessor).
* GET /api/command/lab/citation-integrity serves it, read-only.
* The rollback refuses while verdicts exist and applies cleanly when empty.
"""

import pathlib

import pytest

from sportsassets.lab import citation_integrity as CI
from tests import _audrey_chat_fixture as F
from tests import _persona_harness as H
from tests._persona_harness import pg

ROOT = pathlib.Path(__file__).resolve().parents[1]
UP = (ROOT / "migrations" / "243_agent_citation_integrity.sql").read_text()
DOWN = (ROOT / "migrations" / "rollback" /
        "243_agent_citation_integrity.down.sql").read_text()
Q = "Walk me through the Yankees position."
DEMO = {"demonstration": True}


async def _purge_ledger(c):
    async with c.transaction():
        await c.execute("SET LOCAL session_replication_role = replica")
        await c.execute("DELETE FROM agent_citation_verdicts")
        await c.execute("DELETE FROM agent_citation_checks")


@pytest.fixture
def ldb(db):                                                    # noqa: F811
    """The persona harness's clean schema plus migration 243, with the
    ledger emptied before and after."""
    async def _setup():
        c = await F.connect()
        try:
            if not await c.fetchval(
                    "SELECT to_regclass('agent_citation_checks') IS NOT "
                    "NULL"):
                await c.execute(UP)
            await _purge_ledger(c)
        finally:
            await c.close()
    F.run(_setup())
    yield

    async def _down():
        c = await F.connect()
        try:
            await _purge_ledger(c)
        finally:
            await c.close()
    F.run(_down())


from tests._persona_harness import db  # noqa: E402,F401  (fixture)


def _ask(client, agent, message, **kw):
    body = {"message": message, "allow_records_only": True}
    body.update(kw)
    return client.post("/api/command/agents/%s/persona/chat" % agent,
                       json=body, headers=F.desk_headers())


async def _rows(sql, *args):
    c = await F.connect()
    try:
        return [dict(r) for r in await c.fetch(sql, *args)]
    finally:
        await c.close()


@pg
def test_a_wrong_citation_one_fact_can_repair_is_recited_before_storage(
        ldb, monkeypatch):
    H.no_keys(monkeypatch)
    # $180 is held by F11 (expected profit) only; the reply cites F9 (edge)
    reply = ("DEMONSTRATION position [F1]. The expected profit is $180 "
             "before fees [F9].")
    H.use_claude(monkeypatch, H.FakeClaudeStream([{"chunks": [reply]}]))
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    r = _ask(client, "derek", Q, context=DEMO, allow_records_only=False)
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["provider"]["mode"] == "LLM", g["provider"]
    assert g["answer"] == ("DEMONSTRATION position [F1]. The expected profit "
                           "is $180 before fees [F11].")
    assert {f["fact_id"] for f in g["facts"]} == {"F1", "F11"}
    ci = g["citation_integrity"]
    assert ci["model"]["action"] == "REPAIRED_RECITED"
    assert ci["model"]["counts"] == {"WRONG_FACT": 1}
    assert ci["ledger"]["recorded"] is True
    checks = F.run(_rows("SELECT * FROM agent_citation_checks"))
    assert len(checks) == 1
    c = checks[0]
    assert (c["agent_id"], c["stage"], c["primary_text"], c["published"],
            c["action"], c["message_id"], c["authority"]) == (
        "DEREK", "MODEL_REPLY", True, True, "REPAIRED_RECITED",
        g["message_id"], "SHADOW_RESEARCH_ONLY")
    v = F.run(_rows("SELECT * FROM agent_citation_verdicts ORDER BY "
                    "sentence_index"))
    assert [(x["verdict"], x["action"]) for x in v] == [
        ("WRONG_FACT", "REPAIRED_RECITED")]
    assert v[0]["cited_fact_ids"] == ["F9"]
    assert v[0]["repaired_fact_ids"] == ["F11"]
    assert len(v[0]["sentence_sha256"]) == 64
    # the stored transcript is the repaired one
    t = client.get("/api/command/agents/derek/persona/conversations/%s"
                   % g["conversation_id"], headers=F.desk_headers()).json()
    stored = [m for m in t["messages"] if m["role"] == "ASSISTANT"][-1]
    assert stored["body"].endswith("[F11].")
    assert stored["provider"]["citation_integrity"]["model"]["action"] == \
        "REPAIRED_RECITED"


@pg
def test_an_unrepairable_wrong_citation_discards_the_model_reply(
        ldb, monkeypatch):
    H.no_keys(monkeypatch)
    # 2,000 is held by three facts (qty, hedge qty, the EV arithmetic); the
    # reply cites the hedge COST: wrong, and no single fact to re-cite
    reply = "DEMONSTRATION position [F1]. Exposure first: 2,000 contracts " \
            "[F13]."
    H.use_claude(monkeypatch, H.FakeClaudeStream([{"chunks": [reply]}]))
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    g = _ask(client, "xavier", Q, context=DEMO,
             allow_records_only=False).json()
    p = g["provider"]
    assert p["mode"] == "RECORDS_ONLY" and p["failure"] == \
        "CITATION_INTEGRITY", p
    assert p["integrity_reason"] == "CITATION_INTEGRITY:WRONG_FACT"
    assert g["answer"].startswith(
        "Records-only answer — the AI answer was not used (a citation did "
        "not support its sentence)")
    assert "[F13]." not in g["answer"]
    ci = g["citation_integrity"]
    assert ci["model"]["action"] == "FELL_BACK_TO_RECORDS_ONLY"
    assert ci["records"]["action"] == "PUBLISHED_VERIFIED"
    checks = F.run(_rows("SELECT stage, primary_text, published, action, "
                         " integrity_reason FROM agent_citation_checks "
                         "ORDER BY check_id"))
    assert checks == [
        {"stage": "MODEL_REPLY", "primary_text": True, "published": False,
         "action": "FELL_BACK_TO_RECORDS_ONLY",
         "integrity_reason": "CITATION_INTEGRITY:WRONG_FACT"},
        {"stage": "RECORDS_ONLY_ANSWER", "primary_text": False,
         "published": True, "action": "PUBLISHED_VERIFIED",
         "integrity_reason": None}]
    fell = F.run(_rows("SELECT v.verdict, v.action FROM "
                       "agent_citation_verdicts v JOIN agent_citation_checks "
                       "c USING (check_id) WHERE c.stage = 'MODEL_REPLY'"))
    assert {"verdict": "WRONG_FACT", "action": "FELL_BACK_TO_RECORDS_ONLY"} \
        in fell


@pg
def test_a_records_only_answer_is_verified_and_recorded(ldb, monkeypatch):
    H.no_keys(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    g = _ask(client, "audrey", Q, context=DEMO).json()
    assert g["provider"]["mode"] == "RECORDS_ONLY"
    assert g["citation_integrity"]["records"]["action"] == \
        "PUBLISHED_VERIFIED"
    c = F.run(_rows("SELECT * FROM agent_citation_checks"))
    assert len(c) == 1 and c[0]["stage"] == "RECORDS_ONLY_ANSWER"
    assert c[0]["primary_text"] is True          # no model composed it
    assert c[0]["material_sentences"] >= 3
    assert c[0]["material_sentences"] == c[0]["cited_material_sentences"]
    v = F.run(_rows("SELECT verdict FROM agent_citation_verdicts"))
    assert v and all(x["verdict"] == "PASS" for x in v)


@pg
def test_the_ledger_is_append_only_and_its_invariants_hold(ldb):
    import asyncpg

    async def _go():
        c = await F.connect()
        try:
            cid = await c.fetchval(
                "INSERT INTO agent_citation_checks (agent_id, "
                " conversation_id, stage, composer, primary_text, published, "
                " checked_at, sentences, material_sentences, "
                " cited_material_sentences, verdict_counts, action, "
                " verifier_version) VALUES ('XAVIER', 'pc-x', 'MODEL_REPLY', "
                " 'MODEL', true, true, now(), 2, 1, 1, '{\"PASS\": 1}', "
                " 'PUBLISHED_VERIFIED', 'v') RETURNING check_id")
            bad = [
                # a verified sentence must be a PASS
                ("INSERT INTO agent_citation_verdicts (check_id, agent_id, "
                 " conversation_id, sentence_index, sentence_sha256, "
                 " cited_fact_ids, verdict, failing, action, "
                 " verifier_version) VALUES ($1, 'XAVIER', 'pc-x', 0, "
                 " repeat('a', 64), '{F1}', 'WRONG_FACT', '[{}]', "
                 " 'PUBLISHED_VERIFIED', 'v')"),
                # NO_CITATION cites nothing
                ("INSERT INTO agent_citation_verdicts (check_id, agent_id, "
                 " conversation_id, sentence_index, sentence_sha256, "
                 " cited_fact_ids, verdict, failing, action, "
                 " verifier_version) VALUES ($1, 'XAVIER', 'pc-x', 1, "
                 " repeat('a', 64), '{F1}', 'NO_CITATION', '[{}]', "
                 " 'STATED_UNSUPPORTED', 'v')"),
                # a repair names what it cited
                ("INSERT INTO agent_citation_verdicts (check_id, agent_id, "
                 " conversation_id, sentence_index, sentence_sha256, "
                 " cited_fact_ids, verdict, failing, action, "
                 " verifier_version) VALUES ($1, 'XAVIER', 'pc-x', 2, "
                 " repeat('a', 64), '{F1}', 'WRONG_FACT', '[{}]', "
                 " 'REPAIRED_RECITED', 'v')"),
                # the authority is research only
                ("INSERT INTO agent_citation_verdicts (check_id, agent_id, "
                 " conversation_id, sentence_index, sentence_sha256, "
                 " cited_fact_ids, verdict, failing, action, "
                 " verifier_version, authority) VALUES ($1, 'XAVIER', "
                 " 'pc-x', 3, repeat('a', 64), '{F1}', 'PASS', '[]', "
                 " 'PUBLISHED_VERIFIED', 'v', 'ORDER_AUTHORITY')"),
            ]
            for sql in bad:
                sp = c.transaction()
                await sp.start()
                with pytest.raises(asyncpg.CheckViolationError):
                    await c.execute(sql, cid)
                await sp.rollback()
            # a discarded reply must be unpublished and name its reason
            sp = c.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await c.execute(
                    "INSERT INTO agent_citation_checks (agent_id, "
                    " conversation_id, stage, composer, primary_text, "
                    " published, checked_at, sentences, material_sentences, "
                    " cited_material_sentences, verdict_counts, action, "
                    " verifier_version) VALUES ('XAVIER', 'pc-x', "
                    " 'MODEL_REPLY', 'MODEL', true, true, now(), 1, 1, 1, "
                    " '{}', 'FELL_BACK_TO_RECORDS_ONLY', 'v')")
            await sp.rollback()
            for sql in ("UPDATE agent_citation_checks SET published = false",
                        "DELETE FROM agent_citation_checks"):
                sp = c.transaction()
                await sp.start()
                with pytest.raises(asyncpg.RaiseError,
                                   match="append-only"):
                    await c.execute(sql)
                await sp.rollback()
        finally:
            await c.close()
    F.run(_go())


@pg
def test_citation_metrics_and_the_point_in_time_reader(ldb, monkeypatch):
    from sportsassets.lab import citation_integrity_store as CIS
    H.no_keys(monkeypatch)
    replies = ["DEMONSTRATION position [F1]. The expected profit is $180 "
               "before fees [F9].",
               "DEMONSTRATION position [F1]. Exposure first: 2,000 contracts "
               "[F13]."]
    H.use_claude(monkeypatch, H.FakeClaudeStream(
        [{"chunks": [x]} for x in replies]))
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    for _ in replies:
        assert _ask(client, "derek", Q, context=DEMO,
                    allow_records_only=False).status_code == 200

    async def _go():
        c = await F.connect()
        try:
            m = {x["metric"]: x for x in await CIS.citation_metrics(
                c, agent="DEREK", now=H.T0 + 60)}
            # a deliberately FUTURE-dated check: recorded "an hour later"
            facts = [{"fact_id": "F1", "source": "s", "record_id": "r",
                      "field": "x", "value": 1.5, "text": "cash $1.50"}]
            g = CI.gate("Cash is $9.99 [F1].", facts)
            await CIS.record(c, agent="DEREK", conversation_id="pc-future",
                             message_id=None, turn_id=None,
                             stage=CI.ST_MODEL, checked=g, facts=facts,
                             primary=True, published=False,
                             now=H.T0 + 3600)
            before = {x["metric"]: x for x in await CIS.citation_metrics(
                c, agent="DEREK", now=H.T0 + 60)}
            after = {x["metric"]: x for x in await CIS.citation_metrics(
                c, agent="DEREK", now=H.T0 + 7200)}
            seen_then = await CIS.as_of(c, "checks", clock=H.T0 + 60,
                                        since=H.T0 - 86400)
            seen_later = await CIS.as_of(c, "checks", clock=H.T0 + 7200,
                                         since=H.T0 - 86400)
            answers_before = await CIS.as_of(c, "answers", clock=H.T0 - 1,
                                             since=H.T0 - 86400)
            answers_at = await CIS.as_of(c, "answers", clock=H.T0 + 1,
                                         since=H.T0 - 86400)
            retro = await CIS.retrospective(c, clock=H.T0 + 1,
                                            since=H.T0 - 86400)
            other = await CIS.citation_metrics(c, agent="SCOUT",
                                               now=H.T0 + 60)
            return (m, before, after, seen_then, seen_later, answers_before,
                    answers_at, retro, other)
        finally:
            await c.close()
    (m, before, after, seen_then, seen_later, answers_before, answers_at,
     retro, other) = F.run(_go())
    assert [x for x in m] == list(CIS.CITATION_METRICS)
    assert m["records_only_fallback_rate"]["value"] == 0.5
    assert m["records_only_fallback_rate"]["denominator"] == 2
    assert m["wrong_fact_rate"]["numerator"] == 2
    assert m["correction_rate"]["value"] == 0.5          # 1 of 2 repaired
    assert m["citation_coverage_pct"]["value"] == 100.0
    assert m["wrong_fact_rate"]["status"] == "SMALL_SAMPLE"
    assert m["wrong_fact_rate"]["detail"]["interval95"] is not None
    # the future-dated row is invisible at the earlier clock ...
    assert before == m
    assert all(r["conversation_id"] != "pc-future" for r in seen_then)
    # ... and present once the clock passes it
    assert any(r["conversation_id"] == "pc-future" for r in seen_later)
    assert after["records_only_fallback_rate"]["denominator"] == 3
    assert answers_before == [] and len(answers_at) == 2
    assert retro["answers_read"] == 2
    assert retro["by_agent"]["DEREK"]["ALL"]["answers"] == 2
    assert all(x["status"] == "UNAVAILABLE" and x["value"] is None
               for x in other)


@pg
def test_the_endpoint_serves_the_scorecard_read_only(ldb, monkeypatch):
    starlette = pytest.importorskip("starlette.testclient")
    from fastapi import FastAPI

    from sportsassets.api import app as A
    from sportsassets.api import command_lab_citation as CL
    H.no_keys(monkeypatch)
    chat = H.build_client(monkeypatch, F.Clock(H.T0))
    assert _ask(chat, "xavier", Q, context=DEMO).status_code == 200
    monkeypatch.setattr(A, "settings", lambda: F.Cfg(), raising=False)

    async def _pool():
        return F.CountingPool()
    monkeypatch.setattr(CL, "_pool", _pool)
    monkeypatch.setattr(CL, "_clock", lambda: H.T0 + 60)
    app = FastAPI()
    app.include_router(CL.router)
    client = starlette.TestClient(app)
    assert client.get("/api/command/lab/citation-integrity").status_code \
        == 401
    r = client.get("/api/command/lab/citation-integrity?retrospective=1",
                   headers=F.desk_headers())
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["authority"] == "SHADOW_RESEARCH_ONLY"
    assert b["ledger"]["present"] is True
    assert set(b["agents"]) == set(CL.AGENTS)
    x = {m["metric"]: m for m in b["agents"]["XAVIER"]}
    assert x["citation_coverage_pct"]["value"] == 100.0
    assert b["retrospective"]["by_agent"]["XAVIER"]["ALL"]["answers"] == 1
    assert client.get("/api/command/lab/citation-integrity?agent=NOBODY",
                      headers=F.desk_headers()).status_code == 404
    one = client.get("/api/command/lab/citation-integrity?agent=xavier",
                     headers=F.desk_headers()).json()
    assert list(one["agents"]) == ["XAVIER"]


@pg
def test_the_rollback_refuses_while_verdicts_exist_and_applies_when_empty(
        ldb, monkeypatch):
    import asyncpg
    H.no_keys(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    assert _ask(client, "derek", Q, context=DEMO).status_code == 200

    async def _go():
        c = await F.connect()
        try:
            sp = c.transaction()
            await sp.start()
            with pytest.raises(asyncpg.RaiseError, match="rollback refused"):
                await c.execute(DOWN)
            await sp.rollback()
            sp = c.transaction()
            await sp.start()
            try:
                await c.execute("SET LOCAL session_replication_role = "
                                "replica")
                await c.execute("DELETE FROM agent_citation_verdicts")
                await c.execute("DELETE FROM agent_citation_checks")
                await c.execute("SET LOCAL session_replication_role = "
                                "origin")
                await c.execute(DOWN)
                assert await c.fetchval(
                    "SELECT to_regclass('agent_citation_checks')") is None
                await c.execute(UP)              # and it comes back
                assert await c.fetchval(
                    "SELECT to_regclass('agent_citation_verdicts')") \
                    is not None
            finally:
                await sp.rollback()
        finally:
            await c.close()
    F.run(_go())


@pg
def test_a_verifier_fault_fails_closed(ldb, monkeypatch):
    """A reply the verifier could not check is never published: the
    records-only answer is, with the reason named (and the records-only
    answer names the fault rather than hiding it)."""
    H.no_keys(monkeypatch)
    reply = "DEMONSTRATION position [F1]. The floor is $200 [F19]."
    H.use_claude(monkeypatch, H.FakeClaudeStream([{"chunks": [reply]}]))

    def _boom(*a, **k):
        raise RuntimeError("verifier fault")
    monkeypatch.setattr(CI, "gate", _boom)
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    g = _ask(client, "xavier", Q, context=DEMO,
             allow_records_only=False).json()
    assert g["status"] == "ANSWERED"
    assert g["provider"]["mode"] == "RECORDS_ONLY"
    assert g["provider"]["failure"] == "CITATION_INTEGRITY_UNVERIFIED"
    assert g["provider"]["citation_integrity_error"] == "RuntimeError"
    assert g["answer"].startswith("Records-only answer — the AI answer was "
                                  "not used (its citations could not be "
                                  "verified)")
    assert g["citation_integrity"]["ledger"] == {
        "recorded": False, "why": "NOTHING_VERIFIED"}


def test_every_records_only_demonstration_answer_verifies():
    from sportsassets.agents import persona_chat as PC
    from sportsassets.agents import persona_facts as PF
    f = PF.demonstration_facts()
    bundle = {"facts": f.items, "demonstration": True, "found": True,
              "scope": "POSITION", "missing": f.missing}
    n = 0
    for agent in ("DEREK", "XAVIER", "AUDREY"):
        for intent in ("math", "red_sox_win", "middle", "fees", "hedge",
                       "improve", "confidence", "changed", "walkthrough"):
            for depth in ("QUICK", "NORMAL", "MATH"):
                d = PC.compose_records_only(agent, bundle, "q", depth=depth,
                                            intent=intent, seed="s",
                                            previous=None)
                r = CI.verify(d, f.items, question="q",
                              skip_prefixes=PC.integrity_skip_prefixes())
                assert r["passed"], (agent, intent, depth, [
                    (s["text"], s["failing"]) for s in r["sentences"]
                    if s["verdict"] not in (None, CI.PASS)])
                n += r["material"]
    assert n > 100


def test_the_existing_kept_model_answers_still_pass():
    """The model replies the persona tests keep (test_agent_persona_chat_is
    _grounded, test_agent_persona_chat_interruption) are verified PASS."""
    from sportsassets.agents import persona_facts as PF
    f = PF.demonstration_facts().items
    for good in ("DEMONSTRATION position [F1]. Blended probability 0.59 [F8] "
                 "against $0.50 [F4] is 9 pp [F9]; about $180 before fees "
                 "[F11].",
                 "DEMONSTRATION position [F1]. The floor is $200 [F19]."):
        assert CI.gate(good, f)["action"] == CI.A_VERIFIED
