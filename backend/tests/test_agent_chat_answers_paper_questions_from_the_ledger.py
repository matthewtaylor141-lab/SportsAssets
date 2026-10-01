"""THE PAPER EXPERIMENT IN THE AGENTS' CONVERSATIONS (grounding defects
G1-G4, speech V1-V2).

Production evidence (ddd4050): asked "What are the paper account's cash,
reserved and available balances right now, which paper session is running,
and what have you decided today and why? Cite the records.", Derek and Xavier
fell back to records-only (UNGROUNDED_FIGURE [52.0]) and listed static
paper_accounts fields and a STALE desk account as "Cash" -- no live ledger
balances, no session, no paper decisions. Audrey's management chat had no
tool for them. Speech hid the ElevenLabs error body, and Audrey's
management-chat message ids could not be spoken.

* G1 the persona facts carry the live paper account (the SAME
  `bettor_paper_ledger.balances` the Command Centre strip uses), the active
  session and its health, and today's paper decisions by verdict / refusal;
  a model answer citing them is kept.
* G2 the legacy desk account is labelled as such with its as-of time, and
  never presented as the paper account's cash.
* G3 a model reply that works out a figure of its own (an elapsed time
  between two cited timestamps: the likely origin of production's "52") is
  still rejected -- the check is NOT weakened -- the excerpt is kept for
  diagnosis, and the records-only fallback answers the question asked
  (balances, session, decisions first).
* G4 Audrey's management chat has a READ-ONLY `paper_account` tool (provably
  read-only: it runs inside a READ ONLY transaction) that answers from the
  same source.
* V1 a failing GET /v1/voices keeps the provider's error status
  (invalid_api_key) in the resolution, the speak 503 and persona-status --
  never the key.
* V2 Audrey's stored management-chat replies (conv-...:N) can be spoken;
  her management's own messages and unknown ids cannot.

Both vendors are fakes; nothing reaches the network. All paper data is
SYNTHETIC, on a fresh test paper account (never `paper_acct_main`).
"""

import datetime as dt
import json
import logging
import uuid

from tests import _audrey_chat_fixture as F
from tests import _persona_harness as H
from tests import paper_harness as PH
from tests._persona_harness import db, pg  # noqa: F401  (fixture)

Q = ("What are the paper account's cash, reserved and available balances "
     "right now, which paper session is running, and what have you decided "
     "today and why? Cite the records.")
T0 = H.T0
REASONS = (("NO_RESEARCH_MODEL_CANDIDATE_EXISTS", 5),
           ("PROBABILITY_EVIDENCE_STALE", 2))
LEGACY_ID = "acct_chatt_legacy_desk"
PAPER_TABLES = ("paper_accounts", "paper_ledger", "paper_sessions",
                "paper_session_health", "paper_decisions", "paper_orders",
                "paper_fills", "paper_xavier_reviews", "paper_handoffs")


def _iso(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat(
        timespec="seconds")


async def _seed_paper(c, *, elapsed_min: int = 52) -> dict:
    """A fresh SYNTHETIC paper account: funded once ($500,000), a session
    started `elapsed_min` minutes before its last heartbeat, one recorded
    pass, and Derek's paper decisions -- 5 x NO_RESEARCH_MODEL_CANDIDATE_
    EXISTS and 2 x PROBABILITY_EVIDENCE_STALE today, and one yesterday that
    today's summary must not count."""
    from sportsassets import bettor_paper_session as S

    beat = T0 - 60
    acct = await PH.new_account(c, "chat", now=beat - elapsed_min * 60)
    await S.record_pass(c, acct["session_id"], result={
        "decisions_recorded": 7, "elapsed_s": 1.5}, now=beat)
    n = 0
    for reason, count in REASONS:
        for _ in range(count):
            n += 1
            await _decision(c, acct, at=T0 - 3000 + n * 60, refusal=reason)
    await _decision(c, acct, at=T0 - 86400, refusal="YESTERDAY_ONLY")
    acct["heartbeat"] = beat
    acct["newest_at"] = T0 - 3000 + n * 60
    return acct


async def _decision(c, acct, *, at: float, refusal: str) -> str:
    did = "paper_dec_chatt_%s" % uuid.uuid4().hex[:12]
    await c.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, us_market_slug, fixture, verdict, refusal, refusals, "
        " internal_model, pinnacle, qualification_gaps, policy_version, "
        " simulator_version) VALUES ($1,$2,$3,to_timestamp($4),"
        " 'test-mkt-chatt', 'chatt-fixture', 'REFUSE', $5, ARRAY[$5], "
        " '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, 'TEST_POLICY', 'TEST_SIM')",
        did, acct["session_id"], acct["account_id"], at, refusal)
    return did


async def _seed_legacy_desk(c) -> None:
    """A STALE legacy desk account row (the production 'Cash' that was not
    the paper account). FK checks are off for this one test row only."""
    async with c.transaction():
        await c.execute("SET LOCAL session_replication_role = replica")
        await c.execute(
            "INSERT INTO bettor_desk_account_state (account_id, desk_id, "
            " cash_usd, starting_cash_usd, realized_pnl_usd, fees_usd, "
            " updated_at) VALUES ($1, 'desk-chatt', 97958.35, 100000, "
            " -2041.65, 0, to_timestamp($2)) ON CONFLICT DO NOTHING",
            LEGACY_ID, T0 - 8 * 86400)


async def _drop_legacy_desk(c) -> None:
    async with c.transaction():
        await c.execute("SET LOCAL session_replication_role = replica")
        await c.execute("DELETE FROM bettor_desk_account_state WHERE "
                        " account_id = $1", LEGACY_ID)


def _with(fn):
    async def _go():
        c = await F.connect()
        try:
            return await fn(c)
        finally:
            await c.close()
    return F.run(_go())


def _use_account(monkeypatch, acct):
    from sportsassets.agents import paper_brief as PB
    monkeypatch.setattr(PB, "ACCOUNT_ID", acct["account_id"])


def _gather():
    from sportsassets.agents import persona_facts as PF
    return _with(lambda c: PF.gather(c, question=Q, context={}, now=T0))


def _by_field(bundle) -> dict:
    return {f["field"]: f for f in bundle["facts"]}


def _ask(client, agent, message=Q, **kw):
    body = {"message": message}
    body.update(kw)
    return client.post("/api/command/agents/%s/chat" % agent, json=body,
                       headers=F.desk_headers())


def _seeded(monkeypatch, elapsed_min=52):
    """Seed until the elapsed figure is not ALSO some fact's number (the
    ledger's commit time is wall-clock, so a collision is possible in
    principle); returns (account, bundle)."""
    from sportsassets.agents import persona_chat as PC
    for m in (elapsed_min, elapsed_min + 1, elapsed_min + 3):
        acct = _with(lambda c: _seed_paper(c, elapsed_min=m))
        _use_account(monkeypatch, acct)
        bundle = _gather()
        if m not in PC.allowed_numbers(bundle["facts"], Q):
            acct["elapsed_min"] = m
            return acct, bundle
    raise AssertionError("could not seed a non-colliding elapsed figure")


# ── G1 ──────────────────────────────────────────────────────────────

@pg
def test_g1_book_facts_carry_live_balances_session_and_todays_decisions(
        db, monkeypatch):
    acct, bundle = _seeded(monkeypatch)

    async def _strip(c):
        # the Command Centre strip's own source, for the same account
        from sportsassets import bettor_paper_ledger as L
        return await L.balances(c, acct["account_id"], now=T0)
    strip = _with(_strip)
    f = _by_field(bundle)
    assert bundle["scope"] == "BOOK" and bundle["found"] is True
    rid = "%s#seq%s" % (acct["account_id"], strip["last_sequence"])
    for field, key in (("paper_cash_usd", "cash_usd"),
                       ("paper_reserved_usd", "reserved_usd"),
                       ("paper_available_usd", "available_usd"),
                       ("paper_equity_usd", "total_equity_usd"),
                       ("paper_realized_pnl_usd", "realized_pnl_usd"),
                       ("paper_unrealized_pnl_usd", "unrealized_pnl_usd"),
                       ("paper_last_sequence", "last_sequence")):
        assert f[field]["source"] == "paper_ledger"
        assert f[field]["record_id"] == rid
        assert f[field]["value"] == strip[key], (field, f[field])
    assert strip["cash_usd"] == 500000.0 and strip["reserved_usd"] == 0.0
    assert "$500,000" in f["paper_cash_usd"]["text"]
    assert "$500,000" in f["paper_available_usd"]["text"]
    assert _iso(strip["last_updated_at"]) in f["paper_last_sequence"]["text"]
    # the active session and its health
    sess = f["paper_session"]
    assert sess["source"] == "paper_sessions"
    assert sess["record_id"] == acct["session_id"]
    assert acct["session_id"] in sess["text"] and "ACTIVE" in sess["text"]
    health = f["paper_session_health"]
    assert health["value"] == 1
    assert _iso(acct["heartbeat"]) in health["text"]
    assert "1 passes, 0 errors" in health["text"]
    # today's decisions: 7 (yesterday's excluded), by refusal, the newest
    today = f["paper_decisions_today"]
    assert today["value"] == 7 and "7 recorded" in today["text"]
    assert "0 ENTER, 7 REFUSE" in today["text"]
    assert _iso(acct["newest_at"]) in today["text"]
    for reason, n in REASONS:
        g = f["paper_decisions_today:%s" % reason]
        assert g["value"] == n and reason in g["text"]
    assert "YESTERDAY_ONLY" not in json.dumps(bundle["facts"])
    newest = f["paper_newest_decision"]
    assert newest["source"] == "paper_decisions"
    assert "PROBABILITY_EVIDENCE_STALE" in newest["text"]
    # the paper facts come FIRST, and no static paper_accounts dump remains
    assert [x["source"] for x in bundle["facts"][:3]] == ["paper_ledger"] * 3
    assert not any(x["source"] == "paper_accounts" for x in bundle["facts"])


@pg
def test_g1_a_model_answer_citing_the_paper_facts_is_kept(db, monkeypatch):
    H.no_keys(monkeypatch)
    acct, bundle = _seeded(monkeypatch)
    f = _by_field(bundle)
    cid = lambda k: f[k]["fact_id"]                             # noqa: E731
    good = (
        "Right now the paper account holds $500,000 in cash [%s], $0 "
        "reserved [%s] and $500,000 available [%s]. Session %s is the one "
        "running [%s]. Today's record shows 7 paper decisions, all refusals "
        "[%s]: 5 because no research model candidate exists [%s] and 2 "
        "because the probability evidence was stale [%s]." % (
            cid("paper_cash_usd"), cid("paper_reserved_usd"),
            cid("paper_available_usd"), acct["session_id"],
            cid("paper_session"), cid("paper_decisions_today"),
            cid("paper_decisions_today:NO_RESEARCH_MODEL_CANDIDATE_EXISTS"),
            cid("paper_decisions_today:PROBABILITY_EVIDENCE_STALE")))
    fake = H.use_claude(monkeypatch, H.FakeClaudeStream([
        {"chunks": [good[:40], good[40:]]}]))
    client = H.build_client(monkeypatch, F.Clock(T0))
    r = _ask(client, "derek")
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["status"] == "ANSWERED"
    assert g["provider"]["mode"] == "LLM", g["provider"]
    assert g["provider"]["failure"] is None
    assert g["answer"] == good
    kinds = {c["kind"] for c in g["citations"]}
    assert {"paper_ledger", "paper_sessions", "paper_decisions"} <= kinds
    # the model was given the paper facts and the legacy-account rule
    req = fake.requests[0]["body"]
    sent = json.dumps(req)
    assert "paper_cash_usd" in sent and acct["session_id"] in sent
    assert "LEGACY desk account" in req["system"]
    assert "do not count rows" in req["system"]


# ── G2 ──────────────────────────────────────────────────────────────

@pg
def test_g2_the_legacy_desk_account_is_labelled_and_never_the_paper_cash(
        db, monkeypatch):
    H.no_keys(monkeypatch)
    acct, _b = _seeded(monkeypatch)
    _with(_seed_legacy_desk)
    try:
        bundle = _gather()
        legacy = [x for x in bundle["facts"]
                  if x["source"] == "bettor_desk_account_state"]
        assert len(legacy) == 1
        lf = legacy[0]
        assert lf["field"] == "legacy_desk_cash_usd"
        assert lf["text"].startswith("LEGACY desk account %s" % LEGACY_ID)
        assert "NOT the paper account" in lf["text"]
        assert "$97,958.35 as of" in lf["text"]
        assert "2031-02-24" in lf["text"]               # the as-of time
        # the paper account's cash is the ledger's, not the legacy figure
        f = _by_field(bundle)
        assert f["paper_cash_usd"]["value"] == 500000.0
        client = H.build_client(monkeypatch, F.Clock(T0))
        for agent in ("derek", "xavier", "audrey"):
            r = client.post("/api/command/agents/%s/persona/chat" % agent,
                            json={"message": Q, "allow_records_only": True},
                            headers=F.desk_headers())
            assert r.status_code == 200, r.text
            ans = r.json()["answer"]
            assert "Cash: desk account" not in ans
            assert "Legacy desk account, not the paper account" in ans
            # the paper balances lead; the legacy account comes after them
            assert ans.index("paper account %s cash $500,000"
                             % acct["account_id"]) < ans.index(
                "LEGACY desk account")
    finally:
        _with(_drop_legacy_desk)


# ── G3 ──────────────────────────────────────────────────────────────

@pg
def test_g3_a_figure_the_model_works_out_itself_is_still_rejected(
        db, monkeypatch):
    """Production's ungrounded [52.0]. The rejected reply is not stored, so
    its text cannot be recovered; the checker accepts any number a fact
    holds (and its rounding, x100 and /100), so 52 was a number NO fact held:
    a figure the model derived itself. With the facts it had (session start,
    heartbeats, rows), the plausible derivations are an elapsed time between
    two cited timestamps or a tally of rows. Both are what FIXED_RULES
    forbid ("do no arithmetic the facts do not show"), so the check stays
    strict; what changes is that the counts are now facts (G1) and the
    fallback answers the question."""
    H.no_keys(monkeypatch)
    acct, bundle = _seeded(monkeypatch, elapsed_min=52)
    m = acct["elapsed_min"]
    f = _by_field(bundle)
    derived = (
        "Paper cash is $500,000 [%s] with $0 reserved [%s]. Session %s "
        "[%s] has been running for %d minutes, judging by its last "
        "heartbeat [%s]." % (
            f["paper_cash_usd"]["fact_id"], f["paper_reserved_usd"]["fact_id"],
            acct["session_id"], f["paper_session"]["fact_id"], m,
            f["paper_session_health"]["fact_id"]))
    H.use_claude(monkeypatch, H.FakeClaudeStream([{"chunks": [derived]}]))
    client = H.build_client(monkeypatch, F.Clock(T0))
    r = _ask(client, "xavier")
    assert r.status_code == 200, r.text
    g = r.json()
    p = g["provider"]
    assert g["status"] == "ANSWERED" and p["mode"] == "RECORDS_ONLY"
    assert p["failure"] == "UNGROUNDED_FIGURE"
    assert p["ungrounded"] == [float(m)]
    assert p["ungrounded_context"][0]["figure"] == float(m)
    assert "%d minutes" % m in p["ungrounded_context"][0]["excerpt"]
    # the stored message's provider record keeps the excerpt for diagnosis
    t = client.get("/api/command/agents/xavier/persona/conversations/%s"
                   % g["conversation_id"], headers=F.desk_headers()).json()
    stored = [x for x in t["messages"] if x["role"] == "ASSISTANT"][-1]
    assert stored["provider"]["ungrounded_context"] == p["ungrounded_context"]
    # the records-only fallback answers the question asked, first
    ans = g["answer"]
    assert "%d minutes" % m not in ans
    lead = ans.index("The paper account now:")
    assert lead < ans.index("The paper session:") < ans.index(
        "Paper decisions:")
    assert "cash $500,000" in ans and "reserved $0" in ans
    assert "available $500,000" in ans
    assert acct["session_id"] in ans
    assert "7 recorded -- 0 ENTER, 7 REFUSE" in ans
    assert "NO_RESEARCH_MODEL_CANDIDATE_EXISTS" in ans
    assert "PROBABILITY_EVIDENCE_STALE" in ans
    assert "account_key" not in ans and "currency" not in ans


def test_g3_the_checker_rejects_derived_sums_and_accepts_stated_totals():
    """Policy, pure: a sum / difference of cited facts is NOT accepted (the
    check is unchanged); once the total is itself a fact it is."""
    from sportsassets.agents import persona_chat as PC
    parts = [{"fact_id": "F1", "source": "paper_decisions", "record_id": "a",
              "field": "x", "value": 5, "text": "5 refused for reason A"},
             {"fact_id": "F2", "source": "paper_decisions", "record_id": "b",
              "field": "y", "value": 2, "text": "2 refused for reason B"}]
    text = "Derek refused 7 markets today [F1] [F2]."
    assert PC.ungrounded_numbers(text, parts) == [7.0]
    total = parts + [{"fact_id": "F3", "source": "paper_decisions",
                      "record_id": "c", "field": "t", "value": 7,
                      "text": "7 paper decisions recorded today"}]
    assert PC.ungrounded_numbers(text, total) == []
    # an elapsed time between two cited timestamps is a derived figure too
    times = [{"fact_id": "F1", "source": "paper_sessions", "record_id": "s",
              "field": "a", "value": None,
              "text": "started 2031-03-04T14:07:00+00:00"},
             {"fact_id": "F2", "source": "paper_sessions", "record_id": "s",
              "field": "b", "value": None,
              "text": "last heartbeat 2031-03-04T14:59:00+00:00"}]
    assert PC.ungrounded_numbers("Running 52 minutes [F1] [F2].",
                                 times) == [52.0]
    ex = PC.ungrounded_context("Running 52 minutes [F1] [F2].", [52.0])
    assert ex[0]["figure"] == 52.0
    assert ex[0]["excerpt"].startswith("Running 52 minutes")
    # a second, independent way a natural reply to "what have you decided"
    # is discarded: "I recorded ..." reads as a claimed action. The check is
    # kept; the fixed rules now tell the model to say what the records show
    from sportsassets.agents import audrey_chat as AC
    assert AC._CLAIM.search("Today I recorded 7 paper decisions [F11].")
    assert not AC._CLAIM.search("Today's record shows 7 paper decisions.")
    assert any("never say you created or recorded" in r
               for r in PC.FIXED_RULES)


# ── G4 ──────────────────────────────────────────────────────────────

async def _paper_counts(c) -> dict:
    out = {}
    for t in PAPER_TABLES:
        out[t] = await c.fetchval('SELECT count(*) FROM "%s"' % t)
    out["health"] = [dict(r) for r in await c.fetch(
        "SELECT session_id, passes, errors, heartbeat_at FROM "
        " paper_session_health ORDER BY session_id")]
    return out


@pg
def test_g4_audreys_paper_account_tool_is_read_only_and_answers(
        db, monkeypatch):
    from sportsassets.agents import audrey_chat as AC

    acct, _b = _seeded(monkeypatch)
    tool = AC.TOOLS["paper_account"]
    assert tool.permission == AC.READ
    assert "paper_account" not in AC.MUTATING_TOOLS
    assert AC.route(Q)["calls"] == [("paper_account", {})]
    assert AC.route("What is the cash balance?")["calls"] == \
        [("paper_account", {})]
    # PROVABLY READ-ONLY: it runs inside a READ ONLY transaction, where any
    # write would raise, and changes no paper row
    before = _with(_paper_counts)

    async def _run(c):
        async with c.transaction(readonly=True):
            return await AC.run_tool(c, "paper_account", {}, AC.Ctx(
                role="desk", label=None, now=T0))
    res = _with(_run)
    assert res["status"] == "OK", res
    assert _with(_paper_counts) == before
    d = res["data"]
    assert d["account"]["cash_usd"] == 500000.0
    assert d["account"]["reserved_usd"] == 0.0
    assert d["account"]["available_usd"] == 500000.0
    assert d["session"]["session_id"] == acct["session_id"]
    assert d["decisions_today"]["total"] == 7
    assert d["decisions_today"]["by_verdict"] == {"REFUSE": 7}
    kinds = {c["kind"] for c in res["citations"]}
    assert {"paper_ledger", "paper_sessions", "paper_decisions"} <= kinds
    # through her management chat (deterministic: no key)
    F.no_network(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(T0))
    r = client.post("/api/command/agents/audrey/chat",
                    json={"message": Q, "request_id": F.rid("paper")},
                    headers=F.desk_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    ans = body["answer"]
    assert "cash $500000.00, reserved $0.00" in ans
    assert "available $500000.00" in ans
    assert "Active paper session %s" % acct["session_id"] in ans
    assert "7 recorded — 0 ENTER, 7 REFUSE" in ans
    assert "5 × NO_RESEARCH_MODEL_CANDIDATE_EXISTS" in ans
    assert "paper_sessions:%s" % acct["session_id"] in ans
    assert _with(_paper_counts) == before


@pg
def test_g4_the_model_is_offered_the_paper_tool_and_can_call_it(
        db, monkeypatch):
    from sportsassets.agents import audrey_chat as AC

    acct, _b = _seeded(monkeypatch)
    F.no_network(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", F.FAKE_KEY)
    seen = {}

    def _final(body):
        seen["tool_result"] = F.last_tool_result_text(body)
        return ("Paper cash is $500000.00 with $0.00 reserved "
                "[paper_sessions:%s]." % acct["session_id"])
    fake = F.use_fake(monkeypatch, F.FakeModel([
        F.tool_use("paper_account", {}), F.final_text(_final)]))
    before = _with(_paper_counts)
    client = H.build_client(monkeypatch, F.Clock(T0))
    r = client.post("/api/command/agents/audrey/chat",
                    json={"message": "What is the paper session doing?",
                          "request_id": F.rid("paperllm")},
                    headers=F.desk_headers())
    assert r.status_code == 200, r.text
    names = [t["name"] for t in fake.requests[0]["body"]["tools"]]
    assert "paper_account" in names
    assert '"cash_usd": 500000.0' in seen["tool_result"]
    assert acct["session_id"] in seen["tool_result"]
    assert r.json()["provider"]["mode"] == AC.MODE_LLM
    assert _with(_paper_counts) == before


# ── V1 ──────────────────────────────────────────────────────────────

class _RefusingVoices(H.FakeElevenLabs):
    """GET /v1/voices answers 401 with ElevenLabs' error body -- which, as
    some providers do, echoes the key back. It must never be returned."""

    async def handler(self, request):
        import httpx

        if request.url.path == "/v1/voices":
            self.calls.append({"path": request.url.path})
            return httpx.Response(401, json={"detail": {
                "status": "invalid_api_key",
                "message": "Invalid API key: %s" % H.FAKE_EL_KEY}})
        return await super().handler(request)


@pg
def test_v1_the_voice_list_failure_keeps_the_provider_error_not_the_key(
        db, monkeypatch, caplog):
    H.no_keys(monkeypatch)
    caplog.set_level(logging.DEBUG)
    H.use_elevenlabs(monkeypatch, _RefusingVoices())
    client = H.build_client(monkeypatch, F.Clock(T0))
    ans = client.post("/api/command/agents/derek/persona/chat",
                      json={"message": "Walk me through the Yankees "
                                       "position.",
                            "allow_records_only": True,
                            "context": {"demonstration": True}},
                      headers=F.desk_headers()).json()
    r = client.post("/api/command/agents/derek/speak",
                    json={"message_id": ans["message_id"]},
                    headers=F.desk_headers())
    assert r.status_code == 503
    b = r.json()
    assert b["reason"] == "VOICE_NOT_RESOLVED"
    assert b["resolver_reason"] == "VOICE_LIST_HTTP_401"
    assert b["resolver_detail"]["provider_status"] == 401
    assert b["resolver_detail"]["provider_error_status"] == "invalid_api_key"
    assert b["resolver_detail"]["provider_message"].startswith(
        "Invalid API key")
    # the recorded resolution (reused for the retry window) keeps it too
    r2 = client.post("/api/command/agents/derek/speak",
                     json={"message_id": ans["message_id"]},
                     headers=F.desk_headers())
    assert r2.json()["resolver_detail"]["provider_error_status"] == \
        "invalid_api_key"
    assert r2.json()["resolver_source"] == "RECORDED"
    p = client.get("/api/command/agents/derek/persona",
                   headers=F.desk_headers()).json()
    assert p["voice_resolution"]["detail"]["provider_error"][
        "provider_error_status"] == "invalid_api_key"
    st = client.get("/api/command/agents/persona-status",
                    headers=F.desk_headers()).json()
    vl = st["speech"]["voice_list"]
    assert vl["last_failure"] == "VOICE_LIST_HTTP_401"
    assert vl["provider_error"]["provider_error_status"] == "invalid_api_key"
    for text in (r.text, r2.text, json.dumps(p), json.dumps(st),
                 caplog.text):
        assert H.FAKE_EL_KEY not in text


def test_v1_provider_error_summaries_are_scrubbed():
    import httpx

    from sportsassets.agents import personas as P
    key = "xi-secret-key-0123456789abcdefghijklmnop"
    e = P.provider_error(httpx.Response(403, json={"detail": {
        "status": "missing_permissions",
        "message": "The API key you used is missing the permission "
                   "voices_read to execute this operation."}}), key)
    assert e == {"provider_status": 403,
                 "provider_error_status": "missing_permissions",
                 "provider_message": "The API key you used is missing the "
                                     "permission voices_read to execute this "
                                     "operation."}
    e = P.provider_error(httpx.Response(401, json={"detail": "bad %s" % key}),
                         key)
    assert key not in json.dumps(e) and e["provider_message"].startswith(
        "bad ")
    e = P.provider_error(httpx.Response(500, text="<html>oops</html>"), key)
    assert e["provider_status"] == 500 and e["provider_error_status"] is None
    # a status that is not a plain token is dropped, not echoed
    e = P.provider_error(httpx.Response(401, json={"detail": {
        "status": "x" * 80 + " " + key}}), key)
    assert e["provider_error_status"] is None


# ── V2 ──────────────────────────────────────────────────────────────

@pg
def test_v2_audreys_management_chat_replies_can_be_spoken(db, monkeypatch):
    H.no_keys(monkeypatch)
    F.no_network(monkeypatch)
    el = H.use_elevenlabs(monkeypatch, H.FakeElevenLabs())
    _seeded(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(T0))
    r = client.post("/api/command/agents/audrey/chat",
                    json={"message": Q, "request_id": F.rid("paper")},
                    headers=F.desk_headers())
    assert r.status_code == 200, r.text
    got = r.json()
    mid = got["message_id"]
    # seq 0 is the management's message, seq 1 the tool record, seq 2 the
    # reply (production's failing id was such a reply: conv-...:3)
    assert mid.startswith("conv-") and mid.endswith(":2")
    s = client.post("/api/command/agents/audrey/speak",
                    json={"message_id": mid}, headers=F.desk_headers())
    assert s.status_code == 200, s.text
    assert s.content == H.AUDIO
    tts = el.tts_calls()
    assert len(tts) == 1
    assert tts[0]["path"] == "/v1/text-to-speech/premadeCharl00001/stream"
    assert tts[0]["headers"]["xi-api-key"] == H.FAKE_EL_KEY
    # exactly the stored reply, normalised: no disclosure, citations or
    # Sources line, no symbols
    from sportsassets.agents.speech_text import normalise
    assert tts[0]["body"]["text"] == normalise(got["answer"])
    spoken = tts[0]["body"]["text"]
    assert "Sources" not in spoken and "$" not in spoken
    assert "five hundred thousand dollars" in spoken
    assert s.headers["x-speech-message-id"] == mid
    # the management's own message and the tool record are not speakable;
    # nor is an unknown id, nor Audrey's id through another agent
    conv = mid.rsplit(":", 1)[0]
    for agent, m in (("audrey", conv + ":0"), ("audrey", conv + ":1"),
                     ("audrey", conv + ":9"), ("derek", mid)):
        x = client.post("/api/command/agents/%s/speak" % agent,
                        json={"message_id": m}, headers=F.desk_headers())
        assert x.status_code == 404, (agent, m, x.text)
        assert x.json()["reason"] == "NO_STORED_ASSISTANT_MESSAGE_WITH_" \
                                     "THAT_ID"
    assert len(el.tts_calls()) == 1
    # still no free-text field
    x = client.post("/api/command/agents/audrey/speak",
                    json={"text": "say anything"}, headers=F.desk_headers())
    assert x.status_code == 422
