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
    acct["decisions"] = {}
    for reason, count in REASONS:
        for _ in range(count):
            n += 1
            did = await _decision(c, acct, at=T0 - 3000 + n * 60,
                                  refusal=reason)
            acct["decisions"][did] = reason
    acct["yesterday"] = await _decision(c, acct, at=T0 - 86400,
                                        refusal="YESTERDAY_ONLY")
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
    """The facts as the chat gathers them -- including what each agent
    carries into every answer (the active policy, its stored lessons), so a
    figure quoted from any agent's memory counts as grounded."""
    from sportsassets.agents import persona_facts as PF
    out = _with(lambda c: PF.gather(c, question=Q, context={}, now=T0))
    seen = {(f["source"], f["record_id"], f["field"]) for f in out["facts"]}
    for ag in ("DEREK", "XAVIER", "AUDREY"):
        b = _with(lambda c, ag=ag: PF.gather(c, question=Q, context={},
                                             now=T0, agent=ag))
        for f in b["facts"]:
            k = (f["source"], f["record_id"], f["field"])
            if k not in seen:
                seen.add(k)
                out["facts"].append(f)
    return out


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
    assert acct["yesterday"] not in json.dumps(bundle["facts"])
    # every one of today's decisions, by id, with session and refusal
    for did, reason in acct["decisions"].items():
        d = f["paper_decision:%s" % did]
        assert d["source"] == "paper_decisions" and d["record_id"] == did
        assert d["value"] == "REFUSE"
        assert "refused: %s" % reason in d["text"]
        assert acct["session_id"] in d["text"]
    # what Xavier manages: nothing (no position, order or handoff)
    mg = f["paper_management"]
    assert mg["source"] == "paper_orders" and mg["value"] == 0
    assert "0 open paper position(s)" in mg["text"]
    assert "nothing for Xavier to manage" in mg["text"]
    # the ledger, reconciled
    rc = f["paper_reconciliation"]
    assert rc["value"] is True and rc["source"] == "paper_ledger"
    assert rc["text"].startswith("paper ledger RECONCILES: 1 entries")
    assert "1 INITIAL_FUNDING at sequence %s" % strip["last_sequence"] \
        in rc["text"]
    assert "sum of cash deltas $500,000 against cash $500,000" in rc["text"]
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
    stale = [k for k, v in acct["decisions"].items()
             if v == "PROBABILITY_EVIDENCE_STALE"][0]
    good += (" One of them, %s, was refused because the probability "
             "evidence was stale [%s]." % (stale,
                                           cid("paper_decision:%s" % stale)))
    xav = ("I have nothing to manage on paper: 0 open paper positions and 0 "
           "open management orders [%s], on $500,000 of paper cash [%s] in "
           "session %s [%s]." % (cid("paper_management"),
                                 cid("paper_cash_usd"), acct["session_id"],
                                 cid("paper_session")))
    fake = H.use_claude(monkeypatch, H.FakeClaudeStream([
        {"chunks": [good[:40], good[40:]]}, {"chunks": [xav]}]))
    client = H.build_client(monkeypatch, F.Clock(T0))
    rx = None
    r = _ask(client, "derek")
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["status"] == "ANSWERED"
    assert g["provider"]["mode"] == "LLM", g["provider"]
    assert g["provider"]["failure"] is None
    assert g["answer"] == good
    kinds = {c["kind"] for c in g["citations"]}
    assert {"paper_ledger", "paper_sessions", "paper_decisions"} <= kinds
    assert ("paper_decisions", stale) in {(c["kind"], c["id"])
                                          for c in g["citations"]}
    # Xavier: what he manages -- nothing -- kept, citing the records
    rx = _ask(client, "xavier").json()
    assert rx["provider"]["mode"] == "LLM", rx["provider"]
    assert rx["answer"] == xav
    assert ("paper_orders", "%s:open-orders" % acct["account_id"]) in {
        (c["kind"], c["id"]) for c in rx["citations"]}
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
    from sportsassets.agents import persona_chat as PC
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
    # the records-only fallback is LABELLED as such, in the answer itself,
    # and answers the question asked from the same current records, first
    ans = g["answer"]
    assert ans.startswith("Records-only answer — the AI answer was not used "
                          "(it stated a figure no record holds); everything "
                          "below is quoted from the current records.")
    assert "%d minutes" % m not in ans
    lead = ans.index("The paper account now:")
    assert lead < ans.index("What I manage on paper:") < ans.index(
        "The paper session:") < ans.index("Paper entry decisions, by strategy")
    assert "nothing for Xavier to manage" in ans
    assert not PC.ungrounded_numbers(ans, bundle["facts"], Q)
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
    both = [("paper_account", {}), ("paper_ledger_reconciliation", {})]
    assert AC.route(Q)["calls"] == both
    assert AC.route("What is the cash balance?")["calls"] == both
    assert AC.route("Does the ledger reconcile?")["calls"] == both
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
    for did, reason in acct["decisions"].items():
        assert "Paper decision %s" % did in ans
        assert "paper_decisions:%s" % did in ans
    assert "nothing to manage" in ans
    # and the ledger, read and reconciled, citing its sequence
    seq = d["account"]["last_sequence"]
    assert "Paper ledger RECONCILES for %s (session %s): 1 entries" % (
        acct["account_id"], acct["session_id"]) in ans
    assert "Ledger seq %s INITIAL_FUNDING: cash $500000.00" % seq in ans
    assert "paper_ledger:%s#seq%s" % (acct["account_id"], seq) in ans
    assert _with(_paper_counts) == before


@pg
def test_g4_audrey_reads_and_reconciles_the_paper_ledger(db, monkeypatch):
    from sportsassets.agents import audrey_chat as AC
    from sportsassets.agents import paper_brief as PB

    acct, _b = _seeded(monkeypatch)
    tool = AC.TOOLS["paper_ledger_reconciliation"]
    assert tool.permission == AC.READ
    assert "paper_ledger_reconciliation" not in AC.MUTATING_TOOLS
    before = _with(_paper_counts)

    async def _run(c):
        async with c.transaction(readonly=True):
            return await AC.run_tool(c, "paper_ledger_reconciliation",
                                     {"entries": 5}, AC.Ctx(
                                         role="desk", label=None, now=T0))
    res = _with(_run)
    assert res["status"] == "OK", res
    assert _with(_paper_counts) == before
    rc = res["data"]
    assert rc["reconciled"] is True and rc["failed_checks"] == []
    assert {c["check"] for c in rc["checks"]} == {
        "CASH_EQUALS_SUM_OF_CASH_DELTAS",
        "RESERVED_EQUALS_SUM_OF_RESERVED_DELTAS",
        "AVAILABLE_EQUALS_CASH_MINUS_RESERVED",
        "LAST_ENTRY_RUNNING_BALANCE_AGREES", "EXACTLY_ONE_INITIAL_FUNDING",
        "INITIAL_FUNDING_EQUALS_STARTING_CASH",
        "INITIAL_FUNDING_IS_THE_FIRST_ENTRY", "ENTRY_COUNT_AGREES"}
    assert rc["entries_count"] == 1
    assert rc["session_id"] == acct["session_id"]
    assert [k["kind"] for k in rc["by_kind"]] == ["INITIAL_FUNDING"]
    e = rc["latest_entries"][0]
    assert e["kind"] == "INITIAL_FUNDING" and e["cash_delta_usd"] == 500000.0
    assert e["seq"] == rc["initial_funding_seq"] == rc["last_seq"]
    assert rc["balances"]["available_usd"] == 500000.0
    assert {"kind": "paper_ledger", "id": "%s#seq%s" % (
        acct["account_id"], e["seq"]),
        "href": "/api/command/paper/account"} in res["citations"]
    assert any(c["kind"] == "paper_sessions" and c["id"] == acct["session_id"]
               for c in res["citations"])
    # a disagreement is reported, named, and never smoothed over
    real = PB.L.balances

    async def _off_by_a_dollar(conn, account_id, **kw):
        b = await real(conn, account_id, **kw)
        return dict(b, cash_usd=b["cash_usd"] + 1.0)
    monkeypatch.setattr(PB.L, "balances", _off_by_a_dollar)
    bad = _with(lambda c: PB.reconcile(c, now=T0))
    assert bad["reconciled"] is False
    assert bad["failed_checks"] == ["CASH_EQUALS_SUM_OF_CASH_DELTAS",
                                    "AVAILABLE_EQUALS_CASH_MINUS_RESERVED"]
    lines = "\n".join(AC._reconciliation_lines(bad))
    assert "DOES NOT RECONCILE" in lines
    assert "CASH_EQUALS_SUM_OF_CASH_DELTAS" in lines


@pg
def test_derek_and_xavier_explain_their_actual_paper_decisions(
        db, monkeypatch):
    """Records-only (no key): Derek cites each of today's paper decisions by
    id with verdict and refusal, the session and the live balances; Xavier
    says what he manages -- nothing -- citing the records."""
    H.no_keys(monkeypatch)
    acct, bundle = _seeded(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(T0))
    got = {}
    for agent in ("derek", "xavier"):
        r = client.post("/api/command/agents/%s/persona/chat" % agent,
                        json={"message": Q, "allow_records_only": True},
                        headers=F.desk_headers())
        assert r.status_code == 200, r.text
        got[agent] = r.json()
        assert got[agent]["provider"]["mode"] == "RECORDS_ONLY"
    d = got["derek"]
    ans = d["answer"]
    assert ans.index("The paper account now:") < ans.index(
        "The paper session:") < ans.index("Paper decisions — mine, today:")
    assert "paper account %s cash $500,000" % acct["account_id"] in ans
    assert "reserved $0" in ans and "available $500,000" in ans
    assert "active paper session %s" % acct["session_id"] in ans
    cited = {(c["kind"], c["id"]) for c in d["citations"]}
    for did, reason in acct["decisions"].items():
        assert "paper decision %s" % did in ans
        assert ("paper_decisions", did) in cited
    assert "REFUSE -- refused: NO_RESEARCH_MODEL_CANDIDATE_EXISTS" in ans
    assert "REFUSE -- refused: PROBABILITY_EVIDENCE_STALE" in ans
    assert ("paper_sessions", acct["session_id"]) in cited
    x = got["xavier"]
    xa = x["answer"]
    assert xa.index("What I manage on paper:") < xa.index(
        "Paper entry decisions, by strategy")
    assert "0 open paper position(s), 0 open paper management order(s)" in xa
    assert "nothing for Xavier to manage" in xa
    assert ("paper_orders", "%s:open-orders" % acct["account_id"]) in {
        (c["kind"], c["id"]) for c in x["citations"]}
    for a in (ans, xa):
        from sportsassets.agents import persona_chat as PC
        assert not PC.ungrounded_numbers(a, bundle["facts"], Q)


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

class _RefusingEL(H.FakeElevenLabs):
    """GET /v1/voices and/or the TTS request answer with ElevenLabs' error
    body -- which, as some providers do, echoes the key back -- and a
    request-id header. The key must never be returned, stored or logged."""

    def __init__(self, *, voices=True, tts=False, status=401,
                 code="invalid_api_key"):
        super().__init__()
        self.refuse_voices, self.refuse_tts = voices, tts
        self.status, self.code = status, code

    async def handler(self, request):
        import httpx

        path = request.url.path
        refuse = (path == "/v1/voices" and self.refuse_voices) or (
            path.startswith("/v1/text-to-speech/") and self.refuse_tts)
        if refuse:
            self.calls.append({"path": path, "method": request.method,
                               "params": dict(request.url.params),
                               "headers": {}, "body": None})
            return httpx.Response(self.status, headers={
                "request-id": "req_chatt_%s" % path.count("/")}, json={
                "detail": {"status": self.code,
                           "message": "Invalid API key: %s"
                                      % H.FAKE_EL_KEY}})
        return await super().handler(request)


def _demo_answer(client, agent):
    return client.post("/api/command/agents/%s/persona/chat" % agent,
                       json={"message": "Walk me through the Yankees "
                                        "position.",
                             "allow_records_only": True,
                             "context": {"demonstration": True}},
                       headers=F.desk_headers()).json()


@pg
def test_v1_a_voice_list_failure_is_diagnosed_without_the_key(
        db, monkeypatch, caplog):
    H.no_keys(monkeypatch)
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("ELEVENLABS_VOICE_ID_XAVIER", "premadeBrian00001")
    H.use_elevenlabs(monkeypatch, _RefusingEL(voices=True))
    client = H.build_client(monkeypatch, F.Clock(T0))
    ans = _demo_answer(client, "derek")
    r = client.post("/api/command/agents/derek/speak",
                    json={"message_id": ans["message_id"]},
                    headers=F.desk_headers())
    assert r.status_code == 503
    b = r.json()
    assert b["reason"] == "VOICE_NOT_RESOLVED"
    assert b["resolver_reason"] == "VOICE_LIST_HTTP_401"
    d = b["resolver_detail"]
    assert d["endpoint"] == "GET /v1/voices"
    assert d["required_permission"] == "voices_read"
    assert d["http_status"] == 401
    assert d["provider_error_status"] == "invalid_api_key"
    assert d["provider_message"].startswith("Invalid API key")
    assert d["provider_request_id"] == "req_chatt_2"
    assert b["configured_voice"]["present"] is False
    assert b["configured_voice"]["env_var"] == "ELEVENLABS_VOICE_ID_DEREK"
    # the recorded resolution (reused for the retry window) keeps it too
    r2 = client.post("/api/command/agents/derek/speak",
                     json={"message_id": ans["message_id"]},
                     headers=F.desk_headers())
    assert r2.json()["resolver_source"] == "RECORDED"
    assert r2.json()["resolver_detail"]["provider_error_status"] == \
        "invalid_api_key"
    p = client.get("/api/command/agents/derek/persona",
                   headers=F.desk_headers()).json()
    assert p["voice_resolution"]["detail"]["provider_error"][
        "provider_error_status"] == "invalid_api_key"
    st = client.get("/api/command/agents/persona-status",
                    headers=F.desk_headers()).json()
    vl = st["speech"]["voice_list"]
    assert vl["last_failure"] == "VOICE_LIST_HTTP_401"
    assert vl["provider_error"]["provider_error_status"] == "invalid_api_key"
    assert st["speech"]["required_permissions"] == {
        "GET /v1/voices": "voices_read",
        "POST /v1/text-to-speech/{voice_id}/stream": "text_to_speech"}
    v = st["voices"]
    assert v["derek"]["voice_list_diagnostic"]["http_status"] == 401
    assert v["derek"]["configured_voice"]["present"] is False
    # a configured voice id is reported, with where it came from
    assert v["xavier"]["configured_voice"] == {
        "env_var": "ELEVENLABS_VOICE_ID_XAVIER",
        "env_voice_id": "premadeBrian00001", "profile_voice_id": None,
        "configured_voice_id": "premadeBrian00001", "present": True,
        "source": "env", "invalid_configured_value": False}
    for text in (r.text, r2.text, json.dumps(p), json.dumps(st),
                 caplog.text):
        assert H.FAKE_EL_KEY not in text


@pg
def test_v1_a_tts_failure_is_diagnosed_without_the_key(db, monkeypatch,
                                                        caplog):
    H.no_keys(monkeypatch)
    caplog.set_level(logging.DEBUG)
    H.use_elevenlabs(monkeypatch, _RefusingEL(
        voices=False, tts=True, status=403, code="missing_permissions"))
    client = H.build_client(monkeypatch, F.Clock(T0))
    ans = _demo_answer(client, "xavier")
    r = client.post("/api/command/agents/xavier/speak",
                    json={"message_id": ans["message_id"]},
                    headers=F.desk_headers())
    assert r.status_code == 503
    b = r.json()
    assert b["reason"] == "VOICE_PROVIDER_FAILED"
    assert b["provider_status"] == 403
    d = b["provider_diagnostic"]
    assert d["endpoint"] == \
        "POST /v1/text-to-speech/premadeBrian00001/stream"
    assert "?" not in d["endpoint"]
    assert d["required_permission"] == "text_to_speech"
    assert d["http_status"] == 403
    assert d["provider_error_status"] == "missing_permissions"
    assert d["provider_request_id"] == "req_chatt_4"
    st = client.get("/api/command/agents/persona-status",
                    headers=F.desk_headers()).json()
    t = st["voices"]["xavier"]["last_tts_failure"]
    assert t["provider_error_status"] == "missing_permissions"
    assert t["required_permission"] == "text_to_speech"
    assert st["speech"]["tts"]["last_failure"]["agent"] == "XAVIER"
    for text in (r.text, json.dumps(st), caplog.text):
        assert H.FAKE_EL_KEY not in text


def test_v1_provider_diagnostics_are_sanitized():
    import httpx

    from sportsassets.agents import personas as P
    key = "xi-secret-key-0123456789abcdefghijklmnop"
    e = P.provider_error(httpx.Response(403, headers={
        "x-request-id": "abc-123"}, json={"detail": {
            "status": "missing_permissions",
            "message": "The API key you used is missing the permission "
                       "voices_read to execute this operation."}}), key)
    assert e == {"provider": "elevenlabs", "endpoint": "GET /v1/voices",
                 "required_permission": "voices_read", "http_status": 403,
                 "provider_error_status": "missing_permissions",
                 "provider_message": "The API key you used is missing the "
                                     "permission voices_read to execute this "
                                     "operation.",
                 "provider_request_id": "abc-123", "error_class": None}
    e = P.provider_error(httpx.Response(401, json={
        "detail": "bad Bearer %s and api_key=%s" % (key, key[:10])}), key)
    assert key not in json.dumps(e) and key[:10] not in json.dumps(e)
    assert e["provider_message"].startswith("bad Bearer [redacted]")
    e = P.provider_error(httpx.Response(500, text="<html>oops</html>"), key)
    assert e["http_status"] == 500 and e["provider_error_status"] is None
    # a status that is not a plain token is dropped, not echoed; a long
    # message is cut to 300 characters
    e = P.provider_error(httpx.Response(401, json={"detail": {
        "status": "x" * 80 + " " + key, "message": "word " * 200}}), key)
    assert e["provider_error_status"] is None
    assert len(e["provider_message"]) == 300
    # a request id that carries the key is not kept
    e = P.provider_error(httpx.Response(401, headers={"request-id": key},
                                        json={}), key)
    assert e["provider_request_id"] is None


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
    # seq 0 is the management's message, seq 1 and 2 the two tool records
    # (paper_account, paper_ledger_reconciliation), seq 3 the reply -- the
    # same shape as production's unspeakable id conv-...:3
    assert mid.startswith("conv-") and mid.endswith(":3")
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
                     ("audrey", conv + ":2"), ("audrey", conv + ":9"),
                     ("derek", mid)):
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


# ── G6 · strategies are reported apart ───────────────────────────────

async def _bench_decision(c, acct, *, at: float, refusal: str) -> str:
    did = "paperbench_chatt_%s" % uuid.uuid4().hex[:12]
    await c.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, us_market_slug, fixture, verdict, refusal, refusals, "
        " internal_model, pinnacle, qualification_gaps, policy_version, "
        " simulator_version, strategy) VALUES ($1,$2,$3,to_timestamp($4),"
        " 'test-mkt-chatt', 'chatt-fixture', 'REFUSE', $5, ARRAY[$5], "
        " '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, 'TEST_POLICY', 'TEST_SIM', "
        " 'PINNACLE_ONLY_PAPER_BENCHMARK')",
        did, acct["session_id"], acct["account_id"], at, refusal)
    return did


@pg
def test_g6_benchmark_decisions_are_never_reported_as_dereks(
        db, monkeypatch):
    """The live 04:22Z demonstration counted 12 PINNACLE_ONLY_PAPER_BENCHMARK
    refusals as "Derek's paper decisions". Each strategy is a fact of its
    own, every decision fact names its strategy, the two are never summed
    under Derek, and Audrey's records-only answer splits them too."""
    from sportsassets.agents import audrey_chat as AC
    from sportsassets.agents import persona_chat as PC

    acct = _with(lambda c: _seed_paper(c))
    _use_account(monkeypatch, acct)
    bench = {}

    async def _seed_bench(c):
        for i in range(3):
            did = await _bench_decision(c, acct, at=T0 - 600 + i * 60,
                                        refusal="SETTLEMENT_NOT_SUPPORTED")
            bench[did] = "SETTLEMENT_NOT_SUPPORTED"
    _with(_seed_bench)
    f = _by_field(_gather())
    total = f["paper_decisions_today"]
    assert total["value"] == 10
    assert "all strategies together: 10 recorded -- 0 ENTER, 10 REFUSE" \
        in total["text"]
    assert ("by strategy: DEREK_ENTRY_POLICY_V2 7, "
            "PINNACLE_ONLY_PAPER_BENCHMARK 3") in total["text"]
    derek = f["paper_decisions_today@DEREK_ENTRY_POLICY_V2"]
    assert derek["value"] == 7
    assert derek["text"].startswith("Derek's two-model entry policy")
    assert "/api/command/paper/derek" in derek["text"]
    bm = f["paper_decisions_today@PINNACLE_ONLY_PAPER_BENCHMARK"]
    assert bm["value"] == 3 and bm["record_id"] in bench
    assert "not evidence of qualified or proven profitability" in bm["text"]
    assert "/api/command/paper/benchmark" in bm["text"]
    # the benchmark's reason is keyed under the benchmark, not under Derek
    assert "paper_decisions_today:SETTLEMENT_NOT_SUPPORTED" not in f
    g = f["paper_decisions_today@PINNACLE_ONLY_PAPER_BENCHMARK:"
          "SETTLEMENT_NOT_SUPPORTED"]
    assert g["value"] == 3
    assert "by PINNACLE_ONLY_PAPER_BENCHMARK REFUSED: " in g["text"]
    assert f["paper_decisions_today:NO_RESEARCH_MODEL_CANDIDATE_EXISTS"][
        "value"] == 5
    for did in bench:
        assert ("strategy PINNACLE_ONLY_PAPER_BENCHMARK" in
                f["paper_decision:%s" % did]["text"])
    for did in acct["decisions"]:
        assert ("strategy DEREK_ENTRY_POLICY_V2" in
                f["paper_decision:%s" % did]["text"])
    # Xavier's section names both strategies; no "Derek's paper decisions"
    assert "Derek's paper decisions" not in json.dumps(
        PC.PAPER_AGENT_LEADS)
    # the brief's citations point each decision at its own page
    from sportsassets.agents import paper_brief as PB

    async def _sum(c):
        return await PB.summary(c, now=T0)
    s = _with(_sum)
    hrefs = {c["id"]: c["href"] for c in PB.citations(s)
             if c["kind"] == "paper_decisions"}
    assert hrefs and all(
        (h == "/api/command/paper/benchmark") == (i in bench)
        for i, h in hrefs.items())
    # Audrey's records-only answer splits them, with the labels
    F.no_network(monkeypatch)
    client = H.build_client(monkeypatch, F.Clock(T0))
    r = client.post("/api/command/agents/audrey/chat",
                    json={"message": Q, "request_id": F.rid("paperstrat")},
                    headers=F.desk_headers())
    assert r.status_code == 200, r.text
    ans = r.json()["answer"]
    assert "all strategies together: 10 recorded — 0 ENTER, 10 REFUSE" in ans
    assert ("Derek's two-model entry policy (DEREK_ENTRY_POLICY_V2): 7 "
            "recorded — 0 ENTER, 7 REFUSE") in ans
    assert ("PINNACLE_ONLY_PAPER_BENCHMARK (experimental paper execution, "
            "not evidence of qualified or proven profitability): 3 recorded "
            "— 0 ENTER, 3 REFUSE; 3 × SETTLEMENT_NOT_SUPPORTED") in ans
    for did in bench:
        assert ("Paper decision %s (strategy PINNACLE_ONLY_PAPER_BENCHMARK)"
                % did) in ans
    assert AC is not None


def test_a_clock_time_is_not_a_figure_but_an_invented_amount_still_is():
    from sportsassets.agents import persona_chat as PC
    facts = [{"fact_id": "F1", "value": 2.91, "text": "realised $2.91",
              "record_id": "paperfill:1"}]
    ok = "Realised $2.91 [F1]; filled at 2026-10-01T22:42:55 UTC, reviewed 09:05."
    got = PC.ungrounded_numbers(ok, facts)
    assert 55.0 not in got and 42.0 not in got and 5.0 not in got   # the clock times
    assert set(got) <= {2026.0, 10.0}       # the calendar date is still checked
    bad = "Realised $2.91 [F1] and a further $7.13 at 22:42:55."
    assert PC.ungrounded_numbers(bad, facts) == [7.13]
