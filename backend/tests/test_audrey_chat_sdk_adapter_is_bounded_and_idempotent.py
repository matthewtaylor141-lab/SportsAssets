"""THE CONVERSATIONAL ADAPTER, TO PRODUCTION STANDARD.

The chat's model path runs through the OFFICIAL Anthropic Python SDK
(`anthropic.AsyncAnthropic`, beta messages). Every test here drives the REAL
SDK -- request building, headers, response parsing, typed errors, retries --
against canned Messages API JSON behind `httpx2.MockTransport`; the network is
never reached.

  * the request obeys the rules for claude-opus-5-5: effort inside
    output_config, no `thinking` / `budget_tokens`, no forced tool_choice, the
    server-side refusal fallback (beta header + fallbacks="default");
  * the assistant turn is appended back UNCHANGED (thinking blocks included),
    and every tool_result of a turn goes back in ONE user message, ids matched;
  * no database connection is held across a provider call;
  * distinct recorded outcomes: SUCCESS, MALFORMED_TOOL_ARGUMENTS,
    TEXT_WITHOUT_TOOL_CALL (a claimed action no tool committed is never
    reported), PROVIDER_REFUSAL / INCOMPLETE_OUTPUT (in the failure matrix of
    proof 21), and a directive is reported only from the committed row;
  * provider-model fallback is recorded apart from the answer mode;
  * retries are bounded by an explicit SDK max_retries; the whole operation
    has a deadline, and its expiry is recorded truthfully;
  * idempotency: a retried or concurrent request yields ONE directive and ONE
    task, proven on the real persistence layer;
  * provider_status() tells the truth about live verification.
"""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

from tests import _audrey_chat_fixture as F

pg = F.pg


@pytest.fixture()
def world():
    if not F.DSN:
        pytest.skip("needs RN1X_TEST_DSN")

    async def _setup():
        c = await F.connect()
        try:
            await F.ensure_schema(c)
            await F.purge(c)
            return await F.seed_world(c)
        finally:
            await c.close()

    seeded = F.run(_setup())
    yield seeded

    async def _teardown():
        c = await F.connect()
        try:
            await F.teardown(c)
        finally:
            await c.close()

    F.run(_teardown())


@pytest.fixture()
def fresh_status(monkeypatch):
    from sportsassets.agents import audrey_chat as AC

    monkeypatch.setattr(AC, "_PROVIDER_STATE", {
        "last_failure": None, "last_failure_at": None,
        "last_success_at": None, "first_live_success_at": None,
        "last_model": None, "attempts": 0, "failures": 0})


async def _q(sql, *args):
    c = await F.connect()
    try:
        return [dict(r) for r in await c.fetch(sql, *args)]
    finally:
        await c.close()


def _post(client, headers, message, cid, request_id=None):
    r = client.post("/api/command/agents/audrey/chat", headers=headers,
                    json={"message": message, "conversation_id": cid,
                          "request_id": request_id or F.rid("sdk")})
    return r


THINKING = {"type": "thinking", "thinking": "",
            "signature": "sig-opaque-0123456789abcdef"}


# ═════════════════════════════════════════════════════════════════════
# 1 · THE REQUEST AND THE PROTOCOL
# ═════════════════════════════════════════════════════════════════════

@pg
def test_the_request_obeys_the_model_rules_and_the_tool_protocol(
        world, monkeypatch, fresh_status):
    from sportsassets.agents import audrey_chat as AC

    monkeypatch.setenv("ANTHROPIC_API_KEY", F.FAKE_KEY)
    first = [THINKING,
             {"type": "text", "text": "Reading both records."},
             {"type": "tool_use", "id": "toolu_a", "name": "why_hold",
              "input": {"account_id": F.ACCT_A}},
             {"type": "tool_use", "id": "toolu_b", "name": "agents_status",
              "input": {}}]

    def _answer(body):
        ids = F.ids_in(F.last_tool_result_text(body), r"xav:[0-9a-f]{24}")
        return "Xavier held [bettor_xavier_decisions:%s]." % ids[0]

    fake = F.use_fake(monkeypatch, F.FakeModel([
        lambda body: (200, F.message(first, "tool_use")),
        F.final_text(_answer)]))
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    r = _post(client, F.desk_headers(), "Why did Xavier HOLD instead of pair "
              "on %s?" % F.ACCT_A, "chatt-sdk-shape")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["outcome"] == AC.O_SUCCESS
    assert got["provider"]["answer_mode"] == AC.MODE_LLM
    assert got["provider"]["requested_model"] == "claude-opus-5-5"
    assert got["provider"]["answered_model"] == "claude-opus-5-5"
    assert got["provider"]["provider_fallback_used"] is False
    assert world["hold_id"] in got["answer"]

    assert len(fake.requests) == 2
    for req in fake.requests:
        assert req["path"] == "/v1/messages"
        h = req["headers"]
        assert h["x-api-key"] == F.FAKE_KEY
        assert h["anthropic-version"]
        assert "server-side-fallback-2026-07-01" in h["anthropic-beta"]
        b = req["body"]
        assert b["model"] == "claude-opus-5-5"
        assert b["output_config"] == {"effort": "low"}
        assert b["fallbacks"] == "default"
        assert "thinking" not in b
        assert "budget_tokens" not in json.dumps(b)
        assert b.get("tool_choice") in (None, {"type": "auto"})
        assert F.FAKE_KEY not in json.dumps(b)
        # no database connection is held while the provider is called
        assert req["pool_in_use"] == 0
    # the assistant turn goes back EXACTLY as received, thinking included
    second = fake.requests[1]["body"]["messages"]
    assert second[-2] == {"role": "assistant", "content": first}
    # every tool_result of that turn in ONE user message, ids matched
    results = second[-1]
    assert results["role"] == "user"
    assert [b["type"] for b in results["content"]] == ["tool_result"] * 2
    assert [b["tool_use_id"] for b in results["content"]] == ["toolu_a",
                                                              "toolu_b"]
    assert all(b["is_error"] is False for b in results["content"])
    # the first request's history is untouched by the second (append-only)
    assert fake.requests[1]["body"]["messages"][:len(
        fake.requests[0]["body"]["messages"])] == \
        fake.requests[0]["body"]["messages"]

    # a success through an INJECTED client is not live verification
    st = AC.provider_status()
    assert st["status_text"] == AC.STATUS_PENDING_LIVE
    assert st["first_success_at"] is None
    assert st["last_success_at"].startswith("2031-03-04")


@pg
def test_a_provider_model_fallback_is_recorded_apart_from_the_answer_mode(
        world, monkeypatch, fresh_status):
    from sportsassets.agents import audrey_chat as AC

    monkeypatch.setenv("ANTHROPIC_API_KEY", F.FAKE_KEY)
    F.use_fake(monkeypatch, F.FakeModel([F.final_text(
        lambda body: "Derek is pricing; Xavier is idle.",
        model="claude-opus-4-8")]))
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    got = _post(client, F.desk_headers(), "What are Derek and Xavier doing?",
                "chatt-sdk-fallback").json()
    p = got["provider"]
    assert p["answer_mode"] == AC.MODE_LLM
    assert p["fallback_enabled"] is True
    assert p["requested_model"] == "claude-opus-5-5"
    assert p["answered_model"] == "claude-opus-4-8"
    assert p["provider_fallback_used"] is True
    stored = F.run(_q("SELECT provider FROM audrey_messages WHERE "
                      " conversation_id='chatt-sdk-fallback' AND "
                      " role='AUDREY'"))[0]["provider"]
    stored = json.loads(stored) if isinstance(stored, str) else stored
    assert stored["answered_model"] == "claude-opus-4-8"
    assert stored["provider_fallback_used"] is True
    assert stored["answer_mode"] == "LLM"


# ═════════════════════════════════════════════════════════════════════
# 2 · DISTINCT OUTCOMES
# ═════════════════════════════════════════════════════════════════════

@pg
def test_malformed_tool_arguments_are_rejected_whole_and_recorded(
        world, monkeypatch):
    from sportsassets.agents import audrey_chat as AC

    monkeypatch.setenv("ANTHROPIC_API_KEY", F.FAKE_KEY)
    fake = F.use_fake(monkeypatch, F.FakeModel([
        F.tool_uses([("why_hold", {"account_id": 123}),
                     ("xavier_decision", {"decision_id": "x", "bogus": 1}),
                     ("assign_task", {"directive_id": "dir-x"})]),
        F.final_text(lambda body: "I could not read those records.")]))
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    got = _post(client, F.ADMIN, "What are Derek and Xavier doing?",
                "chatt-sdk-args").json()
    assert got["outcome"] == AC.O_MALFORMED_ARGS
    calls = got["tool_calls"][1:]
    assert [c["why"] for c in calls] == [
        "INVALID_ARGUMENTS:ARGUMENT_NOT_A_STRING:account_id",
        "INVALID_ARGUMENTS:UNKNOWN_ARGUMENT:bogus",
        "INVALID_ARGUMENTS:MISSING_ARGUMENT:agent"]
    results = fake.requests[1]["body"]["messages"][-1]["content"]
    assert [r["is_error"] for r in results] == [True, True, True]
    assert all("INVALID_ARGUMENTS" in r["content"] for r in results)
    # nothing partial ran: no task was assigned
    assert F.run(_q("SELECT 1 FROM agent_tasks WHERE task_id LIKE "
                    " 'task-dir-x%'")) == []


@pg
def test_a_claimed_action_without_a_committed_tool_result_is_not_reported(
        world, monkeypatch):
    from sportsassets.agents import audrey_chat as AC

    monkeypatch.setenv("ANTHROPIC_API_KEY", F.FAKE_KEY)
    F.use_fake(monkeypatch, F.FakeModel([F.final_text(
        lambda body: "I have created a directive to reduce drawdown and "
                     "assigned it to Xavier.")]))
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    got = _post(client, F.ADMIN, "What are Derek and Xavier doing?",
                "chatt-sdk-claim").json()
    assert got["outcome"] == AC.O_TEXT_WITHOUT_TOOL
    assert got["provider"]["answer_mode"] == AC.MODE_DETERMINISTIC
    assert got["answer"].startswith("[%s]" % AC.DISCLOSE_DISCARDED)
    assert "I have created" not in got["answer"]
    assert "directive" not in got and "committed_directive_id" not in got
    assert F.run(_q("SELECT 1 FROM management_directives WHERE "
                    " conversation_id='chatt-sdk-claim'")) == []


@pg
def test_a_directive_is_reported_only_from_the_committed_row(world,
                                                             monkeypatch):
    from sportsassets.agents import directives as D

    monkeypatch.setenv("ANTHROPIC_API_KEY", F.FAKE_KEY)
    F.use_fake(monkeypatch, F.FakeModel([
        F.tool_use("create_directive", {
            "instruction": "Have Xavier reduce unpaired exposure below "
                           "$200"}),
        F.final_text(lambda body: "I have created the directive.")]))
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    got = _post(client, F.ADMIN, "What are Derek and Xavier doing?",
                "chatt-sdk-commit").json()
    assert got["outcome"] == "SUCCESS"
    did = got["committed_directive_id"]
    rows = F.run(_q("SELECT directive_id, status, task_ids FROM "
                    " management_directives WHERE conversation_id = "
                    " 'chatt-sdk-commit'"))
    assert [r["directive_id"] for r in rows] == [did]
    assert rows[0]["status"] == D.ACTIVE
    assert rows[0]["task_ids"] == [D.task_id_for(did, "XAVIER")]


def test_authorization_is_rechecked_at_execution_time():
    from sportsassets.agents import audrey_chat as AC
    from sportsassets.agents import directives as D

    ctx = AC.Ctx(role="desk", label=None, now=F.T0)
    got = F.run(AC.run_tool(None, "create_directive",
                            {"instruction": "Reduce drawdown"}, ctx))
    assert got["status"] == "REFUSED" and got["why"] == D.R_REQUIRES_OPERATOR


# ═════════════════════════════════════════════════════════════════════
# 3 · BOUNDS
# ═════════════════════════════════════════════════════════════════════

@pg
def test_retries_are_bounded_by_an_explicit_sdk_max_retries(
        world, monkeypatch, fresh_status):
    from sportsassets.agents import audrey_chat as AC

    monkeypatch.setenv("ANTHROPIC_API_KEY", F.FAKE_KEY)
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    monkeypatch.setenv("AUDREY_PROVIDER_MAX_RETRIES", "1")
    assert AC.provider_config()["max_retries"] == 1
    fast = {"retry-after-ms": "1"}
    fake = F.use_fake(monkeypatch, F.FakeModel([
        F.api_error(429, "rate_limit_error", headers=fast),
        F.api_error(429, "rate_limit_error", headers=fast)]))
    got = _post(client, F.desk_headers(), "What are Derek and Xavier doing?",
                "chatt-sdk-429").json()
    assert len(fake.requests) == 2                 # one try + one retry
    assert got["provider"]["failure"] == "PROVIDER_RATE_LIMITED"
    assert got["answer"].startswith("[%s]" % AC.DISCLOSE_UNAVAILABLE)

    fake = F.use_fake(monkeypatch, F.FakeModel([
        F.api_error(529, "overloaded_error", headers=fast),
        F.final_text(lambda body: "Derek is pricing.")]))
    got = _post(client, F.desk_headers(), "What are Derek and Xavier doing?",
                "chatt-sdk-529").json()
    assert len(fake.requests) == 2
    assert got["outcome"] == AC.O_SUCCESS

    monkeypatch.setenv("AUDREY_PROVIDER_MAX_RETRIES", "7")
    assert AC.provider_config()["max_retries"] == 1   # capped


@pg
def test_the_whole_operation_has_a_deadline_and_its_expiry_is_recorded(
        world, monkeypatch):
    from sportsassets.agents import audrey_chat as AC

    F.no_network(monkeypatch)
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    monkeypatch.setenv("AUDREY_CHAT_DEADLINE_S", "1")

    async def _slow(conn, args, ctx):
        await asyncio.sleep(10)

    monkeypatch.setattr(AC.TOOLS["agents_status"], "fn", _slow)
    rid = F.rid("deadline")
    got = _post(client, F.desk_headers(), "What are Derek and Xavier doing?",
                "chatt-sdk-deadline", request_id=rid).json()
    assert got["status"] == AC.S_DEADLINE
    assert got["outcome"] == AC.O_DEADLINE
    rows = F.run(_q("SELECT role, outcome FROM audrey_messages WHERE "
                    " conversation_id='chatt-sdk-deadline' ORDER BY seq"))
    assert rows == [{"role": "MANAGEMENT", "outcome": None},
                    {"role": "AUDREY", "outcome": "DEADLINE_EXCEEDED"}]
    # the same request replayed returns the recorded state, not a new run
    again = _post(client, F.desk_headers(), "What are Derek and Xavier "
                  "doing?", "chatt-sdk-deadline", request_id=rid).json()
    assert again["replayed"] is True and again["status"] == AC.S_DEADLINE
    assert len(F.run(_q("SELECT 1 FROM audrey_messages WHERE "
                        " conversation_id='chatt-sdk-deadline'"))) == 2


@pg
def test_the_model_gets_only_what_is_left_of_the_deadline(world,
                                                          monkeypatch):
    from sportsassets.agents import audrey_chat as AC

    async def _hang(body):
        await asyncio.sleep(3600)

    monkeypatch.setenv("ANTHROPIC_API_KEY", F.FAKE_KEY)
    F.use_fake(monkeypatch, F.FakeModel([_hang]))
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    monkeypatch.setenv("AUDREY_CHAT_DEADLINE_S", "7")
    got = _post(client, F.desk_headers(), "What are Derek and Xavier doing?",
                "chatt-sdk-budget").json()
    # the provider was cut off early enough for the records to answer
    assert got["status"] == AC.S_ANSWERED
    assert got["provider"]["failure"] == "TIMEOUT"
    assert got["answer"].startswith("[%s]" % AC.DISCLOSE_UNAVAILABLE)


def test_the_chat_is_not_on_the_scheduler_or_servicing_path():
    root = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
    offenders = []
    for path in list((root / "workers").glob("*.py")) + \
            list(root.glob("bettor_funded_*.py")) + \
            [root / "bettor_xavier.py"]:
        src = path.read_text()
        if "audrey_chat" in src or "agents_chat" in src \
                or "directives" in src and "agents" in src:
            offenders.append(path.name)
    assert offenders == []


# ═════════════════════════════════════════════════════════════════════
# 4 · IDEMPOTENCY ON THE REAL PERSISTENCE LAYER
# ═════════════════════════════════════════════════════════════════════

TEXT_ONE_TASK = "Have Xavier reduce unpaired exposure below $200"


async def _counts(cid):
    c = await F.connect()
    try:
        d = await c.fetch("SELECT directive_id, task_ids FROM "
                          " management_directives WHERE conversation_id=$1",
                          cid)
        ids = [r["directive_id"] for r in d]
        t = await c.fetchval("SELECT count(*) FROM agent_tasks WHERE "
                             " directive_id = ANY($1::text[])", ids)
        m = await c.fetchval("SELECT count(*) FROM audrey_messages WHERE "
                             " conversation_id=$1", cid)
        return len(ids), t, m
    finally:
        await c.close()


@pg
def test_a_retried_chat_request_returns_the_original_result(world,
                                                            monkeypatch):
    from sportsassets.agents import audrey_chat as AC

    F.no_network(monkeypatch)
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    rid = F.rid("retry")
    first = _post(client, F.ADMIN, TEXT_ONE_TASK, "chatt-idem-seq", rid)
    assert first.status_code == 200
    first = first.json()
    assert first["status"] == AC.S_DIRECTIVE and first["replayed"] is False
    before = F.run(_counts("chatt-idem-seq"))
    assert before[:2] == (1, 1)
    again = _post(client, F.ADMIN, TEXT_ONE_TASK, "chatt-idem-seq",
                  rid).json()
    assert again["replayed"] is True
    assert again["committed_directive_id"] == first["committed_directive_id"]
    assert again["message_id"] == first["message_id"]
    assert F.run(_counts("chatt-idem-seq")) == before     # nothing appended
    # the same key for a DIFFERENT request is refused
    r = _post(client, F.ADMIN, "Something else entirely.", "chatt-idem-seq",
              rid)
    assert r.status_code == 409
    # a missing / malformed key is rejected by the route
    r = client.post("/api/command/agents/audrey/chat", headers=F.ADMIN,
                    json={"message": TEXT_ONE_TASK})
    assert r.status_code == 422


@pg
def test_concurrent_duplicates_yield_one_directive_and_one_task(world):
    from sportsassets.agents import audrey_chat as AC
    from sportsassets.agents import directives as D

    async def _both_chat():
        c1, c2 = await F.connect(), await F.connect()
        try:
            rid = F.rid("conc")
            return await asyncio.gather(*[
                AC.handle_message(c, role="admin", message=TEXT_ONE_TASK,
                                  conversation_id="chatt-idem-conc",
                                  now=F.T0, env={}, request_id=rid)
                for c in (c1, c2)])
        finally:
            await c1.close()
            await c2.close()

    a, b = F.run(_both_chat())
    assert F.run(_counts("chatt-idem-conc"))[:2] == (1, 1)
    firsts = [x for x in (a, b) if not x.get("replayed")]
    assert len(firsts) == 1 and firsts[0]["status"] == AC.S_DIRECTIVE
    other = [x for x in (a, b) if x.get("replayed")][0]
    assert other["status"] in (AC.S_PENDING, AC.S_DIRECTIVE)

    # the persistence layer alone, without the request table: two
    # connections create the same request's directive at once
    async def _both_create():
        c1, c2 = await F.connect(), await F.connect()
        try:
            rid = F.rid("conc-db")
            return await asyncio.gather(*[
                D.create(c, instruction=TEXT_ONE_TASK, requester_role="admin",
                         requester_label="ops", now=F.T0,
                         conversation_id="chatt-idem-db", request_id=rid)
                for c in (c1, c2)])
        finally:
            await c1.close()
            await c2.close()

    x, y = F.run(_both_create())
    assert sorted([x["created"], y["created"]]) == [False, True]
    assert x["directive"]["directive_id"] == y["directive"]["directive_id"]
    assert F.run(_counts("chatt-idem-db"))[:2] == (1, 1)


@pg
def test_directive_create_and_confirm_routes_are_idempotent(world,
                                                           monkeypatch):
    from sportsassets.agents import directives as D

    F.no_network(monkeypatch)
    client = F.build_client(monkeypatch, F.Clock(F.T0))
    rid = F.rid("form-idem")
    body = {"request_id": rid, "objective": "Reduce losses on that account"}
    r1 = client.post("/api/command/agents/audrey/directives",
                     headers=F.ADMIN, json=body).json()
    r2 = client.post("/api/command/agents/audrey/directives",
                     headers=F.ADMIN, json=body).json()
    did = r1["directive"]["directive_id"]
    assert r1["replayed"] is False and r2["replayed"] is True
    assert r2["directive"]["directive_id"] == did
    assert r1["directive"]["status"] == D.DRAFT
    crid = F.rid("confirm-idem")
    c1 = client.post("/api/command/agents/audrey/directives/%s/confirm"
                     % did, headers=F.ADMIN,
                     json={"request_id": crid, "accounts": [F.ACCT_A]})
    c2 = client.post("/api/command/agents/audrey/directives/%s/confirm"
                     % did, headers=F.ADMIN,
                     json={"request_id": crid, "accounts": [F.ACCT_A]})
    assert c1.status_code == 200 and c2.status_code == 200
    assert c2.json()["replayed"] is True
    rows = F.run(_q("SELECT status, task_ids FROM management_directives "
                    " WHERE directive_id=$1", did))
    assert rows[0]["status"] == D.ACTIVE
    assert len(rows[0]["task_ids"]) == 2
    tasks = F.run(_q("SELECT count(*) AS n FROM agent_tasks WHERE "
                     " directive_id=$1", did))
    assert tasks[0]["n"] == 2
    evs = F.run(_q("SELECT kind FROM management_directive_events WHERE "
                   " directive_id=$1 ORDER BY event_id", did))
    assert [e["kind"] for e in evs].count("CLARIFIED") == 1


# ═════════════════════════════════════════════════════════════════════
# 5 · THE HEALTH READ
# ═════════════════════════════════════════════════════════════════════

@pg
def test_provider_status_reports_presence_never_the_value(world, monkeypatch,
                                                          fresh_status):
    from sportsassets.agents import audrey_chat as AC

    monkeypatch.setenv("ANTHROPIC_API_KEY", F.FAKE_KEY)
    st = AC.provider_status()
    assert st["configured"] is True and st["key_present"] is True
    assert st["model"] == "claude-opus-5-5"
    assert st["sdk_version"] and st["sdk_version"].startswith("1.")
    assert st["status_text"] == \
        "implemented/tested; live provider verification pending"
    assert st["first_success_at"] is None and st["last_failure"] is None
    assert F.FAKE_KEY not in json.dumps(st)

    AC._record_provider(ok=False, reason="TIMEOUT", model="claude-opus-5-5",
                        now=F.T0, live=True)

    async def _ws():
        c = await F.connect()
        try:
            return await AC.workspace_sections(c, now=F.T0)
        finally:
            await c.close()
    prov = F.run(_ws())["provider"]["data"]
    assert prov["last_failure"] == "TIMEOUT"
    assert prov["last_failure_at"].startswith("2031-03-04")
    assert prov["status_text"] == AC.STATUS_PENDING_LIVE
    assert F.FAKE_KEY not in json.dumps(prov)
    # only a success through the SDK's OWN client counts as live
    AC._record_provider(ok=True, reason=None, model="claude-opus-5-5",
                        now=F.T0 + 60, live=True)
    st = AC.provider_status()
    assert st["status_text"] == AC.STATUS_LIVE_VERIFIED
    assert st["first_success_at"].startswith("2031-03-04")
