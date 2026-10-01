"""PERSONA CHAT: three voices, one set of facts.

* "Walk me through the Yankees position." -- each agent cites the SAME
  record ids and states the SAME core numbers, from its own perspective
  (Derek: edge / EV / sizing; Xavier: exposure / trade-off / thesis;
  Audrey: result / decision quality / improvement).
* With no Yankees record the answers SAY SO and invent no position.
* Real records (a Derek V2 decision and a Xavier decision) are cited by id.
* Without ANTHROPIC_API_KEY the route refuses (503 LLM_UNAVAILABLE) and
  stores nothing -- unless records-only mode is asked for, which works.
* A model reply is kept only when every figure in it is a fact; otherwise
  the records answer is returned with the reason.
* Depth adapts (quick / math) and consecutive answers open differently.
"""

import re

from tests import _audrey_chat_fixture as F
from tests import _persona_harness as H
from tests._persona_harness import db, pg  # noqa: F401  (fixture)

Q = "Walk me through the Yankees position."
DEMO = {"demonstration": True}


def _ask(client, agent, message, **kw):
    body = {"message": message, "allow_records_only": True}
    body.update(kw)
    return client.post("/api/command/agents/%s/persona/chat" % agent,
                       json=body, headers=F.desk_headers())


@pg
def test_the_yankees_walkthrough_agrees_and_the_perspectives_differ(
        db, monkeypatch):
    H.no_keys(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    got = {}
    for agent in ("derek", "xavier", "audrey"):
        r = _ask(client, agent, Q, context=DEMO)
        assert r.status_code == 200, r.text
        got[agent] = r.json()
        assert got[agent]["status"] == "ANSWERED"
        assert got[agent]["demonstration"] is True
        assert got[agent]["provider"]["mode"] == "RECORDS_ONLY"
        assert "DEMONSTRATION" in got[agent]["answer"]
        # the stored transcript is the answer; the spoken text is its
        # normalised form, with no citations or dollar signs
        assert got[agent]["text"] == got[agent]["answer"]
        assert "[" not in got[agent]["spoken_text"]
        assert "$" not in got[agent]["spoken_text"]
    ids = {a: H.record_ids(g) for a, g in got.items()}
    assert ids["derek"] == ids["xavier"] == ids["audrey"]
    assert len(ids["derek"]) == 3          # entry, hedge and payoff plan
    for a, g in got.items():
        missing = H.CORE_NUMBERS - H.numbers_in(g["answer"])
        assert not missing, (a, missing)
        # every cited fact is a DEMONSTRATION fact
        assert all(f["source"] == "DEMONSTRATION" for f in g["facts"])
        # the missing evidence is named
        assert any("fees" in m for m in g["missing_evidence"])
        assert "fee" in g["answer"].lower()
    d, x, a = (got[k]["answer"].lower() for k in ("derek", "xavier",
                                                  "audrey"))
    assert "edge" in d and "sizing" in d and "expected value" in d
    assert "exposure" in x and "trade-off" in x and "thesis" in x \
        and "what changed" in x
    assert "result" in a and "decision quality" in a and "improve" in a
    # the perspectives differ: the three openings and bodies are distinct
    assert len({d[:120], x[:120], a[:120]}) == 3


@pg
def test_no_yankees_position_is_said_truthfully(db, monkeypatch):
    H.no_keys(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    seen = []
    for agent in ("derek", "xavier", "audrey"):
        r = _ask(client, agent, Q)
        assert r.status_code == 200, r.text
        g = r.json()
        assert g["status"] == "ANSWERED" and g["found"] is False
        assert g["facts"] == [] and g["citations"] == []
        assert "no production or paper yankees" in g["answer"].lower()
        # WHICHEVER BUILD THIS IS, the paper ledger was looked at and said
        # nothing: either its tables are absent and that is named, or (with
        # migrations 171/172 applied) every paper table was read and matched
        # no Yankees row -- never silently skipped, never a read failure.
        # (the agent's memory -- the active policy and its stored lessons --
        # is read for every answer and is not a search for the position)
        paper = [c for c in g["checked"] if c["source"].startswith("paper")
                 and c["source"] not in ("paper_policy_parameter_heads",
                                         "paper_agent_lessons")]
        assert paper, g["checked"]
        if any(c["status"] == "NOT_IN_THIS_BUILD" for c in paper):
            assert "paper ledger not in this build" in g["missing_evidence"]
        else:
            assert all(c["status"] == "NO_MATCH" and c["matches"] == 0
                       for c in paper), paper
        # no figure is stated at all
        assert not re.search(r"\$\d", g["answer"])
        seen.append(g["answer"])
    assert len(set(seen)) == 3


@pg
def test_real_yankees_records_are_cited_identically(db, monkeypatch):
    H.no_keys(monkeypatch)

    async def _seed():
        c = await F.connect()
        try:
            return await H.seed_yankees(c)
        finally:
            await c.close()
    ids = F.run(_seed())
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    got = {}
    for agent in ("derek", "xavier", "audrey"):
        r = _ask(client, agent, Q)
        assert r.status_code == 200, r.text
        got[agent] = r.json()
        assert got[agent]["found"] is True
    rids = {a: H.record_ids(g) for a, g in got.items()}
    assert rids["derek"] == rids["xavier"] == rids["audrey"]
    assert ("derek_entry_decisions", ids["derek"]) in rids["derek"]
    assert ("bettor_xavier_decisions", ids["xavier"]) in rids["derek"]
    for a, g in got.items():
        nums = H.numbers_in(g["answer"])
        # the V2 record's figures, the same in every answer
        assert {0.61, 0.57, 0.59, 7.0, 0.52, 5.75} <= nums, (a, nums)
        # and nothing that is not a fact
        from sportsassets.agents import persona_chat as PC
        facts = PC.cited_facts(g["answer"], g["facts"])
        assert facts
        assert not PC.ungrounded_numbers(g["answer"], g["facts"])
    # perspective: each leads with its own lane
    assert got["derek"]["answer"].find("entry side") < \
        got["derek"]["answer"].find("Xavier's management call")
    assert "Exposure first" in got["xavier"]["answer"]
    assert "Decision quality" in got["audrey"]["answer"]


@pg
def test_without_the_key_the_chat_refuses_and_stores_nothing(db,
                                                              monkeypatch):
    H.no_keys(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    r = client.post("/api/command/agents/xavier/chat",
                    json={"message": Q}, headers=F.desk_headers())
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["status"] == "LLM_UNAVAILABLE"
    assert body["reason"] == "LLM_UNAVAILABLE_SERVER_KEY_NOT_CONFIGURED"
    assert body["display"] == "AI unavailable: server key not configured"
    assert body["records_only_available"] is True

    async def _count():
        c = await F.connect()
        try:
            return await c.fetchval("SELECT count(*) FROM agent_chat_messages")
        finally:
            await c.close()
    assert F.run(_count()) == 0
    # records-only mode, asked for, works without any key
    r = client.post("/api/command/agents/derek/chat",
                    json={"message": Q, "allow_records_only": True,
                          "context": DEMO}, headers=F.desk_headers())
    assert r.status_code == 200, r.text
    assert r.json()["provider"]["mode"] == "RECORDS_ONLY"
    assert r.json()["voice"]["available"] is False
    assert r.json()["voice"]["display"] == \
        "voice unavailable: server key not configured"
    # no credential at all -> 401
    r = client.post("/api/command/agents/derek/chat", json={"message": Q})
    assert r.status_code == 401


@pg
def test_a_model_reply_is_kept_only_when_every_figure_is_a_fact(
        db, monkeypatch):
    H.no_keys(monkeypatch)
    good = ("DEMONSTRATION position [F1]. Blended probability 0.59 [F8] "
            "against $0.50 [F4] is 9 pp [F9]; about $180 before fees [F11].")
    bad = ("DEMONSTRATION position [F1]. The edge is 12 pp and we expect "
           "$240 [F11].")
    fake = H.use_claude(monkeypatch, H.FakeClaudeStream([
        {"chunks": [good[:30], good[30:]]}, {"chunks": [bad]}]))
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    r = _ask(client, "derek", Q, context=DEMO, allow_records_only=False)
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["provider"]["mode"] == "LLM" and g["answer"] == good
    assert {f["fact_id"] for f in g["facts"]} == {"F1", "F8", "F4", "F9",
                                                  "F11"}
    req = fake.requests[0]["body"]
    assert "You are Derek, the enthusiastic quant" in req["system"]
    assert "Never invent" in req["system"]
    assert "tools" not in req and "thinking" not in req
    assert req["output_config"] == {"effort": "low"}
    assert req.get("fallbacks") == "default"
    assert fake.requests[0]["headers"]["x-api-key"] == H.FAKE_ANTHROPIC_KEY
    # an invented figure: the reply is discarded, the records answer stands
    r = _ask(client, "derek", Q, context=DEMO, allow_records_only=False,
             conversation_id=g["conversation_id"])
    g2 = r.json()
    assert g2["provider"]["mode"] == "RECORDS_ONLY"
    assert g2["provider"]["failure"] == "UNGROUNDED_FIGURE"
    assert 12.0 in g2["provider"]["ungrounded"]
    assert "12 pp" not in g2["answer"] and "$240" not in g2["answer"]
    assert H.FAKE_ANTHROPIC_KEY not in r.text


@pg
def test_depth_adapts_and_answers_do_not_open_the_same_way(db, monkeypatch):
    H.no_keys(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    walk = _ask(client, "xavier", Q, context=DEMO).json()
    cid = walk["conversation_id"]
    quick = _ask(client, "xavier", "Quick: what's the floor?",
                 conversation_id=cid).json()
    assert quick["depth"] == "QUICK"
    assert len(quick["answer"]) < len(walk["answer"]) / 2
    assert "$200" in quick["answer"] and "DEMONSTRATION" in quick["answer"]
    math = _ask(client, "xavier", "Show me the math.",
                conversation_id=cid).json()
    assert math["depth"] == "MATH"
    assert "$2,000 − $1,800 = $200" in math["answer"]
    # the follow-up kept the conversation's selected position
    assert math["demonstration"] is True
    again = _ask(client, "xavier", Q, conversation_id=cid).json()
    first = lambda s: s.split(". ")[0]                          # noqa: E731
    assert first(again["answer"]) != first(math["answer"])
    # the transcript holds both texts for every answer
    t = client.get("/api/command/agents/xavier/persona/conversations/%s"
                   % cid, headers=F.desk_headers()).json()
    answers = [m for m in t["messages"] if m["role"] == "ASSISTANT"]
    assert len(answers) == 4
    assert all(m["spoken_text"] and m["body"] for m in answers)
    assert all(m["persona_version"] == 1 for m in answers)
