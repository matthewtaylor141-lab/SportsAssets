"""INTERRUPTION: a new message cancels the answer in flight; the partial
answer is stored and marked INTERRUPTED.

The model is a fake stream that sends its first words and then stalls, as a
slow answer would. A second message in the same conversation cancels it: the
first call returns INTERRUPTED with the partial text, the stored message says
INTERRUPTED and names the message that interrupted it, its turn is
INTERRUPTED, and an interrupted answer cannot be spoken.
"""

import asyncio

from tests import _audrey_chat_fixture as F
from tests import _persona_harness as H
from tests._persona_harness import db, pg  # noqa: F401  (fixture)

Q = "Walk me through the Yankees position."


@pg
def test_a_new_message_interrupts_the_answer_in_flight(db, monkeypatch):
    H.no_keys(monkeypatch)
    fake = H.use_claude(monkeypatch, H.FakeClaudeStream([
        {"chunks": ["DEMONSTRATION position [F1]. Exposure first: ",
                    "2,000 contracts [F5]"], "stall_after": 1},
        {"chunks": ["DEMONSTRATION position [F1]. The floor is $200 "
                    "[F19]."]}]))
    from sportsassets.agents import persona_chat as PC

    async def _go():
        pool = F.CountingPool()
        first = asyncio.ensure_future(PC.converse(
            pool, agent="xavier", role="command", message=Q,
            conversation_id="pc-xavier-intr-0001",
            context={"demonstration": True}, now=H.T0))
        await asyncio.wait_for(fake.first_chunk.wait(), 20)
        second = await PC.converse(
            pool, agent="xavier", role="command",
            message="Actually, just the floor?",
            conversation_id="pc-xavier-intr-0001", now=H.T0 + 5)
        return await asyncio.wait_for(first, 20), second

    a, b = F.run(_go())
    assert a["status"] == "INTERRUPTED"
    assert a["partial"] is True
    assert a["answer"] == "DEMONSTRATION position [F1]. Exposure first: "
    assert a["interrupted_by"] == b["user_message_id"]
    assert b["status"] == "ANSWERED"
    assert b["interrupted_turns"]
    assert b["answer"].endswith("The floor is $200 [F19].")

    async def _read():
        c = await F.connect()
        try:
            m = await c.fetchrow("SELECT status, body, interrupted_by, "
                                 " in_reply_to FROM agent_chat_messages "
                                 " WHERE message_id=$1", a["message_id"])
            t = await c.fetchrow("SELECT state, interrupted_by, "
                                 " assistant_message_id FROM "
                                 " agent_chat_turns WHERE assistant_message_id"
                                 " = $1", a["message_id"])
            return m, t
        finally:
            await c.close()
    m, t = F.run(_read())
    assert m["status"] == "INTERRUPTED"
    assert m["body"] == a["answer"]
    assert m["interrupted_by"] == b["user_message_id"]
    assert m["in_reply_to"] == a["user_message_id"]
    assert t["state"] == "INTERRUPTED"

    # an interrupted answer is not spoken
    H.use_elevenlabs(monkeypatch, H.FakeElevenLabs())
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    r = client.post("/api/command/agents/xavier/speech",
                    json={"message_id": a["message_id"]},
                    headers=F.desk_headers())
    assert r.status_code == 409
    assert r.json()["reason"] == "MESSAGE_IS_NOT_A_COMPLETE_ANSWER"


@pg
def test_the_interrupt_route_stops_the_answer(db, monkeypatch):
    H.no_keys(monkeypatch)
    fake = H.use_claude(monkeypatch, H.FakeClaudeStream([
        {"chunks": ["DEMONSTRATION position [F1]. Let me ", "walk"],
         "stall_after": 1}]))
    from sportsassets.agents import persona_chat as PC

    async def _go():
        pool = F.CountingPool()
        first = asyncio.ensure_future(PC.converse(
            pool, agent="derek", role="command", message=Q,
            conversation_id="pc-derek-intr-0002",
            context={"demonstration": True}, now=H.T0))
        await asyncio.wait_for(fake.first_chunk.wait(), 20)
        turns = await PC.interrupt(pool, "pc-derek-intr-0002",
                                   by="USER_INTERRUPT", now=H.T0 + 1)
        return await asyncio.wait_for(first, 20), turns

    a, turns = F.run(_go())
    assert turns and a["status"] == "INTERRUPTED"
    assert a["interrupted_by"] == "USER_INTERRUPT"
    assert a["answer"] == "DEMONSTRATION position [F1]. Let me "

    async def _turns():
        c = await F.connect()
        try:
            return [dict(r) for r in await c.fetch(
                "SELECT state FROM agent_chat_turns WHERE conversation_id="
                " 'pc-derek-intr-0002'")]
        finally:
            await c.close()
    assert [t["state"] for t in F.run(_turns())] == ["INTERRUPTED"]


@pg
def test_a_turn_moves_once_and_messages_are_never_rewritten(db,
                                                            monkeypatch):
    import asyncpg
    import pytest

    H.no_keys(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(H.T0))
    r = client.post("/api/command/agents/audrey/persona/chat",
                    json={"message": Q, "allow_records_only": True,
                          "context": {"demonstration": True}},
                    headers=F.desk_headers())
    mid = r.json()["message_id"]

    async def _try():
        c = await F.connect()
        try:
            with pytest.raises(asyncpg.RaiseError):
                await c.execute("UPDATE agent_chat_messages SET body='x' "
                                " WHERE message_id=$1", mid)
            with pytest.raises(asyncpg.RaiseError):
                await c.execute("DELETE FROM agent_chat_messages WHERE "
                                " message_id=$1", mid)
            with pytest.raises(asyncpg.RaiseError):
                await c.execute("UPDATE agent_chat_turns SET state="
                                " 'IN_FLIGHT' WHERE assistant_message_id=$1",
                                mid)
        finally:
            await c.close()
    F.run(_try())
