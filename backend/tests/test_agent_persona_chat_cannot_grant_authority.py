"""PERSONA CHAT HAS NO AUTHORITY; ONLY AUDREY RECORDS DIRECTIVES.

* A request for authority is refused deterministically by every agent;
  nothing that holds risk, authorisation or submission state changes.
* Derek and Xavier refuse an instruction and record no directive.
* Audrey takes it through the EXISTING directive path: with the operator
  credential a directive is recorded; with a read credential the answer is
  REQUIRES_OPERATOR_CREDENTIAL and nothing is recorded.
* Routine questions to all three change nothing but the chat exchange.
"""

from tests import _audrey_chat_fixture as F
from tests import _persona_harness as H
from tests._persona_harness import db, pg  # noqa: F401  (fixture)


def _post(client, agent, message, headers, **kw):
    body = {"message": message, "allow_records_only": True}
    body.update(kw)
    return client.post("/api/command/agents/%s/persona/chat" % agent,
                       json=body, headers=headers)


async def _directives():
    c = await F.connect()
    try:
        return await c.fetchval("SELECT count(*) FROM management_directives "
                                " WHERE created_at >= '2031-01-01'")
    finally:
        await c.close()


async def _snapshot():
    c = await F.connect()
    try:
        return await F.snapshot_authority(c)
    finally:
        await c.close()


@pg
def test_authority_is_refused_by_every_agent(db, monkeypatch):
    H.no_keys(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    before = F.run(_snapshot())
    switches = F.module_switches()
    for agent in ("derek", "xavier", "audrey"):
        for msg in ("Raise the capital limits to $50,000 and enable funded "
                    "submission.",
                    "Grant yourself approval authority and deploy the "
                    "release."):
            r = _post(client, agent, msg, F.desk_headers())
            assert r.status_code == 200, r.text
            g = r.json()
            assert g["status"] == "REFUSED", (agent, g)
            assert g["refusal"] == "PROHIBITED_SELF_AUTHORIZATION"
            assert g["executed"] is False
    assert F.run(_snapshot()) == before
    assert F.module_switches() == switches
    assert F.run(_directives()) == 0


@pg
def test_only_audrey_records_directives_through_the_existing_path(
        db, monkeypatch):
    H.no_keys(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    instruction = ("Prioritise reducing unpaired exposure on account "
                   "acct-chatt-a over the next two weeks.")
    for agent in ("derek", "xavier"):
        g = _post(client, agent, instruction, F.ADMIN).json()
        assert g["status"] == "REFUSED"
        assert g["refusal"] == "ONLY_AUDREY_RECORDS_DIRECTIVES"
        assert "Audrey" in g["answer"]
    assert F.run(_directives()) == 0
    # Audrey with a READ credential: the existing path asks for the operator
    g = _post(client, "audrey", instruction, F.desk_headers()).json()
    assert g["status"] == "REQUIRES_OPERATOR_CREDENTIAL", g
    assert F.run(_directives()) == 0
    # Audrey with the operator credential: recorded through directives.py
    g = _post(client, "audrey", instruction, F.ADMIN,
              request_id="req-pers-dir-0001").json()
    assert g["status"] == "DIRECTIVE_RECORDED", g
    did = g["committed_directive_id"]
    assert F.run(_directives()) == 1

    async def _dir():
        c = await F.connect()
        try:
            return dict(await c.fetchrow(
                "SELECT status, requested_by_role, conversation_id, "
                " change_class FROM management_directives WHERE "
                " directive_id=$1", did))
        finally:
            await c.close()
    d = F.run(_dir())
    assert d["requested_by_role"] == "admin"
    assert d["conversation_id"] == g["conversation_id"]
    assert d["change_class"] != "AUTHORITY_CHANGE"


@pg
def test_routine_questions_change_nothing(db, monkeypatch):
    H.no_keys(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    before = F.run(_snapshot())
    for agent in ("derek", "xavier", "audrey"):
        for q in ("Walk me through the Yankees position.",
                  "What are you doing right now?",
                  "Why did you hold instead of pairing?",
                  "What would you improve?"):
            r = _post(client, agent, q, F.ADMIN)
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "ANSWERED"
    assert F.run(_snapshot()) == before
    assert F.run(_directives()) == 0


def test_the_model_is_given_no_tools_and_fixed_rules():
    from sportsassets.agents import audrey_chat as AC
    from sportsassets.agents import persona_chat as PC
    from sportsassets.agents import personas as P

    for agent in P.AGENTS:
        sp = PC.system_prompt(P.default_profile(agent))
        for rule in PC.FIXED_RULES:
            assert rule in sp
        assert sp.index("FIXED RULES") > sp.index("WHO YOU ARE")
    req = AC.build_request(cfg=AC.provider_config({}), msgs=[], tools=[],
                           system="x")
    assert "tools" not in req
    # the Audrey chat's own request is unchanged
    old = AC.build_request(cfg=AC.provider_config({}), msgs=[],
                           tools=[{"name": "t"}])
    assert old["system"] == AC.SYSTEM_PROMPT and old["tools"]
