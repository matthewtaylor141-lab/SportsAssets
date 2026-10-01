"""THE MANAGEMENT OFFICE (Codex agent-office patch, integrated) ON THE REAL
OPERATIONS RECORDS.

  DEREK FIGURES   a refusal's gross edge, fees and net EV come from the same
                  record (shortfall / estimate / best level) when the
                  top-level key is empty, with the source named; nothing is
                  recomputed.
  VERSIONS        the homepage's closest-opportunity read returns the latest
                  decision per market AND policy version, the serving version
                  first, each row with its recorded version, a `historical`
                  flag and its age -- a V1 refusal never reads as today's V2.
  OFFICE PAGES    each agent page carries the office workboard, the
                  position-linked Ask (record id in the question), the
                  version disclosure, the mobile route to the conversation,
                  concise / full answer styles, and the speech guards: a new
                  question or Stop aborts the earlier speech request and a
                  superseded response cannot start speaking.

No order, policy, threshold or ledger write.
"""
from __future__ import annotations

import json
import time

import pytest

from sportsassets import bettor_paper_experiment as EXP
from sportsassets import bettor_paper_ops as OPS
from sportsassets.agents import paper_benchmark as PB
from sportsassets.api import agent_office as OFFICE
from sportsassets.api import agent_pages as P

from tests import paper_harness as H
from tests import paper_live_fixture as PL

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


def test_a_refusal_shows_its_own_recorded_edge_fees_and_ev():
    row = {
        "decision_id": "papercg:x", "strategy": PB.CG_STRATEGY,
        "policy_version": PB.CG_VERSION, "verdict": "REFUSE",
        "refusal": "BELOW_MIN_GROSS_EDGE", "limit_price": None,
        "policy_decision": json.dumps({
            "gross_edge_pp": None, "fees_usd": None,
            "net_expected_profit_usd": None,
            "shortfall": {"edge_pp": -0.747, "ev_after_fees_usd": None}}),
        "economics": json.dumps({"best_level_edge_pp": -0.747,
                                 "levels": [{"price": 0.67, "qty": 10}]})}
    d = OPS.decision_view(row)
    assert d["edge_pp"] == pytest.approx(-0.747)
    assert d["figure_sources"]["edge_pp"] == "shortfall.edge_pp"
    assert d["purchase_price"] == pytest.approx(0.67)
    assert "best level" in d["figure_sources"]["purchase_price"]
    assert d["net_ev_usd"] is None, "never invented"
    # an exploration decision: its estimate carries the figures
    ex = OPS.decision_view({
        "decision_id": "paperexp:x", "strategy": PB.EXPLORE_STRATEGY,
        "verdict": "ENTER", "refusal": None, "limit_price": 0.25,
        "policy_decision": json.dumps({"estimate": {
            "gross_edge_pp_at_best": -0.61, "fees_usd": 4.95,
            "expected_net_profit_usd": -7.26}}),
        "economics": json.dumps({})})
    assert ex["edge_pp"] == pytest.approx(-0.61)
    assert ex["fees_usd"] == pytest.approx(4.95)
    assert ex["net_ev_usd"] == pytest.approx(-7.26)
    # a top-level value is never overridden
    top = OPS.decision_view({
        "decision_id": "papercg:y", "strategy": PB.CG_STRATEGY,
        "verdict": "REFUSE", "refusal": "X", "limit_price": None,
        "policy_decision": json.dumps({"gross_edge_pp": 0.82,
                                       "shortfall": {"edge_pp": 9.9}}),
        "economics": json.dumps({})})
    assert top["edge_pp"] == pytest.approx(0.82)
    assert "edge_pp" not in top["figure_sources"]


@pg
async def test_closest_rows_carry_their_version_and_serving_rows_come_first():
    conn = await H.connect()
    now = time.time()
    try:
        await PL.purge_everything(conn)
        acct = await PL.new_account(conn, "officever", now=now)
        slug = "aec-mlb-office-ver-%d" % int(now)

        async def dec(version, gross, at, tag):
            await conn.execute(
                "INSERT INTO paper_decisions (decision_id, session_id, "
                " account_id, decided_at, us_market_slug, holding_side, "
                " intent, verdict, refusal, refusals, p_pinnacle, economics, "
                " policy_version, policy_decision, strategy, label, "
                " simulator_version, internal_model, pinnacle, "
                " qualification_gaps) VALUES ($1,$2,$3,to_timestamp($4),$5,"
                " 'LONG','LONG','REFUSE','BELOW_MIN_GROSS_EDGE',"
                " ARRAY['BELOW_MIN_GROSS_EDGE'],0.6,$6::jsonb,$7,'{}'::jsonb,"
                " $8,'{}'::jsonb,'test','{}'::jsonb,'{}'::jsonb,'[]'::jsonb)",
                "papercg:officever:%s:%d" % (tag, int(now)),
                acct["session_id"], acct["account_id"], at, slug,
                json.dumps({"best_level_edge_pp": gross,
                            "threshold_edge_pp": 5.0 if version ==
                            PB.CG_VERSION_V1 else 0.5,
                            "levels": [{"price": 0.6}]}),
                version, PB.CG_STRATEGY)
        # V1 with the HIGHER edge, older; V2 lower edge, newer; and two V2
        # rows for the same market (only the latest survives)
        await dec(PB.CG_VERSION_V1, 0.82, now - 3600, "v1")
        await dec(PB.CG_VERSION, 0.10, now - 900, "v2old")
        await dec(PB.CG_VERSION, 0.20, now - 60, "v2new")
        rows = [r for r in await EXP.closest(conn, now, acct["account_id"])
                if r["us_market_slug"] == slug]
        assert [r["policy_version"] for r in rows] == [PB.CG_VERSION,
                                                        PB.CG_VERSION_V1]
        cur, hist = rows
        assert cur["historical"] is False and hist["historical"] is True
        assert cur["gross_pp"] == pytest.approx(0.20), "latest per version"
        assert hist["threshold_pp"] == pytest.approx(5.0)
        assert cur["serving_version"] == PB.CG_VERSION
        assert 50 <= cur["age_s"] <= 120
        assert 3500 <= hist["age_s"] <= 3700
    finally:
        await PL.purge_everything(conn)
        await conn.close()


def _page(kind):
    return P._cc_page_html(kind)


@pytest.mark.parametrize("kind", ["derek", "xavier", "audrey"])
def test_each_office_page_carries_the_workboard_and_conversation(kind):
    h = _page(kind)
    assert "Management office · " in h and "office-layout" in h
    assert 'id="talk"' in h and 'id="talk-in"' in h
    # v4: exactly one mobile launcher (the desk dock); the earlier jump
    # button was removed so the page never shows two
    assert "desk-mobile-talk" in h, "mobile route to the conversation"
    assert "office-talk-jump" not in h
    assert 'office-answer-style' in h
    assert "Full analysis -- walk me through" in h
    assert "Briefly, answer conversationally" in h
    # the avatar module and its audio hook are the existing ones
    assert "/api/command/agents/static/cc_avatar.js" in h
    assert "attachAudio" in h


def test_the_position_question_carries_the_group_id_and_versions_are_disclosed():
    js = OFFICE.OFFICE_JS
    assert "Position group '+p.group_id" in js
    # v3: MLB logos only for an exact MLB identity; served as static files
    assert "club-logo" in js
    assert "Decision '+r.decision_id" in js
    assert "Explain recommendation '+r.recommendation_id" in js
    assert "Historical policy version" in js
    assert "its refusal uses the rules at evaluation, not today" in js
    # missing values are said, never zero
    assert "Not recorded" in js
    assert "This is not an empty portfolio." in js
    # automated acknowledgements stay distinct from agent responses
    assert "SYSTEM" in js or "automated" in js.lower()


def test_speech_is_superseded_by_a_new_question_or_stop():
    tj = P.TALK_JS
    # every new question stops (aborts) the previous speech first
    assert "stopAudio(); addQ(text)" in tj
    # Stop aborts the outstanding request and advances the generation
    assert "speechEpoch++; if(speechAbort){speechAbort.abort()" in tj
    # a response that arrives after a newer request never plays
    assert "if(generation!==speechEpoch)return;" in tj
    # text send is not blocked by speech download
    assert "speak(j.message_id, false); else" in tj
    assert "await speak(j.message_id" not in tj
    sj = OFFICE.SPEECH_JS
    assert "MediaSource" in sj and "isTypeSupported('audio/mpeg')" in sj
    assert "buffered" in tj, "the buffered fallback is disclosed"
