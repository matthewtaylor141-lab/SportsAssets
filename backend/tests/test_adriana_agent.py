"""ADRIANA, HEAD OF ARBITRAGE: THE EIGHTH AGENT (migration 265).

What these tests pin (the arbitrage arithmetic itself is pinned by
tests/test_adriana_arb_engine.py):

  * IDENTITY: canonical id ADRIANA / slug adriana, a woman, SHADOW_ONLY,
    version 1 PENDING_OWNER_APPROVAL, her own (unassigned) voice -- and the
    migration seed equals the code;
  * NO AUTHORITY, in code and in the database: her registry allow list
    holds no order, credential, capital or approval tool; her modules import
    no venue / order module, open no connection and write only her own
    tables; a session declared as ADRIANA is refused on control and task-
    approval writes; her machine-style labels are machine actors to the
    learning and improvement pipelines;
  * HER RECORDS are append-only, SHADOW, production_effect NONE, and an
    opportunity row can only ever say GUARANTEED_AFTER_COSTS;
  * THE CENSUS fails closed: venue settlement terms not read -> every
    structure REFUSED with VOID_TERMS_NOT_ESTABLISHED (a would-be structure
    is a CONDITIONAL candidate, never an opportunity); Kalshi books not
    recorded -> the venue is UNAVAILABLE with the reason;
  * A REAL PASS end to end on the database: heartbeat, run, scan,
    refusals, review request to Audrey, blocker tasks parked WAITING, her
    floor seat deployed with recorded monitor values and outputs;
  * SLACK fails closed without her own app; the API is read-only.
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from sportsassets.agents import adriana as AD
from sportsassets.agents import adriana_arb as A
from sportsassets.agents import identity as I
from sportsassets.agents import pos_authority as PA
from sportsassets.agents import registry as R

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN not set")
MODS = {"adriana.py": ROOT / "sportsassets" / "agents" / "adriana.py",
        "adriana_runner.py": ROOT / "sportsassets" / "agents"
        / "adriana_runner.py",
        "adriana_api.py": ROOT / "sportsassets" / "api" / "adriana_api.py"}
_ORDER_MODULES = ("venue", "kalshi", "clob", "live_executor",
                  "bettor_funded", "submission", "execmirror",
                  "bettor_xavier", "workers", "pmus", "pmx", "edge_gate",
                  "entry_execution", "execution_gate")
OWN = {"adriana_arb_scans", "adriana_arb_opportunities",
       "adriana_arb_refusals"}


# ═════════════════════════════════════════════════════════════════════
# 1 · IDENTITY
# ═════════════════════════════════════════════════════════════════════

def test_adriana_is_the_eighth_canonical_identity():
    assert R.ADRIANA == "ADRIANA" and R.ADRIANA in R.AGENTS
    assert R.ADRIANA in R.SHADOW_AGENTS
    assert I.AGENTS[-1] == "ADRIANA" and len(I.AGENTS) == 8
    assert I.SLUGS["ADRIANA"] == "adriana"
    assert I.agent_of("adriana") == I.agent_of("Adriana") == "ADRIANA"
    row = I.identity_row("adriana")
    assert I.validate_identity(row) is None
    assert row["presentation"] == "FEMALE"
    assert row["authority_status"] == "SHADOW_ONLY"
    assert row["role"] == "HEAD_OF_ARBITRAGE"
    assert row["title"] == "Head of Arbitrage"
    assert row["approved_by"] == I.PENDING and row["approved_at"] is None
    assert row["source_directive"] == "PM_DIRECTIVE_2026-10-05_ADRIANA"
    # her signature is her own
    others = {I.identity_row(a)["signature"] for a in I.AGENTS
              if a != "ADRIANA"}
    assert row["signature"] not in others
    v = I.VOICE_SPEC["ADRIANA"]
    assert v["assignment"] == I.A_UNASSIGNED and v["provider_voice_id"] is None
    assert v["voice_profile_id"] == "vp-adriana-v1"
    assert len({I.VOICE_SPEC[a]["provider_voice_alias"] for a in I.AGENTS}) \
        == 8


def test_the_migration_seed_is_generated_from_the_code():
    sql = (ROOT / "migrations" / "265_adriana_arbitrage_agent.sql").read_text()
    row = I.identity_row("ADRIANA")
    assert "$q$%s$q$" % row["content_sha"] in sql
    assert "$q$%s$q$" % I.voice_sha(I.VOICE_SPEC["ADRIANA"]) in sql
    assert "PENDING_OWNER_APPROVAL', NULL" in sql
    assert (ROOT / "migrations" / "rollback"
            / "265_adriana_arbitrage_agent.down.sql").is_file()


# ═════════════════════════════════════════════════════════════════════
# 2 · NO AUTHORITY, IN CODE
# ═════════════════════════════════════════════════════════════════════

def test_her_tools_hold_no_order_credential_capital_or_approval():
    perms = R.IDENTITIES[R.ADRIANA]["tool_permissions"]
    assert perms["authority_status"] == "SHADOW_ONLY"
    assert perms["order_path"] is None
    for t in R.NEVER_GRANTED + R.SHADOW_DENIED:
        assert not R.permits(R.ADRIANA, t), t
        assert not PA.may(R.ADRIANA, t), t
    for t in PA.FORBIDDEN_ACTIONS:
        assert not PA.may(R.ADRIANA, t), t
    for t in ("order.submit", "cancel.order", "dispatch.anything",
              "request.entry", "promote.model", "submit.order"):
        assert not PA.may(R.ADRIANA, t), t
    assert PA.may(R.ADRIANA, "write.arb_records")
    assert PA.may(R.ADRIANA, "read.books")
    assert PA.refuse_authority("adriana", "order.submit_direct")[
        "refusal"] == "ADRIANA_HAS_NO_AUTHORITY"
    with pytest.raises(PA.NoAuthority):
        PA.assert_may(R.ADRIANA, "write.capital_allocation")
    assert PA.actor_of("agent:adriana") == "ADRIANA"
    assert PA.actor_of("ADRIANA-arb") == "ADRIANA"
    assert PA.actor_of("Adriana Ruiz") is None
    assert AD.assert_no_authority() and A.assert_no_authority()
    assert AD.AUTHORITY == {**A.AUTHORITY}
    for k in ("submit", "cancel", "credentials", "capital"):
        assert AD.AUTHORITY[k] is False


@pytest.mark.parametrize("name", sorted(MODS))
def test_her_modules_import_no_order_path_and_write_only_her_tables(name):
    src = MODS[name].read_text()
    for node in ast.walk(ast.parse(src)):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [(node.module or "")] + [a.name for a in node.names]
        for mod in mods:
            for bad in _ORDER_MODULES:
                assert bad not in mod, (name, mod)
    low = src.lower()
    written = set(re.findall(r"(?:insert into|update|delete from)\s+"
                             r"([a-z_]+)", low)) - {"of", "set"}
    assert written <= OWN, (name, written)
    for forbidden in ("chat.postmessage", "httpx", "requests.", "urllib",
                      "socket", "submit_for_decision", "claim_dispatch",
                      "create_order", "cancel_order", "os.environ"):
        assert forbidden not in low, (name, forbidden)


def test_the_api_has_no_write_route_and_needs_the_credential():
    from fastapi.testclient import TestClient
    from sportsassets.api import app as APP
    from tests.test_agent_workspaces_show_runtime_records import _route_paths
    paths = ("/api/command/adriana", "/api/command/agents/adriana",
             "/api/command/adriana/scans",
             "/api/command/adriana/opportunities/{opportunity_id}")
    routes = set(_route_paths(APP.app.routes))
    for p in paths:
        assert p in routes, p
    c = TestClient(APP.app)
    for p in paths:
        assert c.get(p.replace("{opportunity_id}", "adr-opp-x")
                     ).status_code == 401, p
    src = MODS["adriana_api.py"].read_text()
    assert not re.search(r"@router\.(post|put|patch|delete)", src)
    assert src.count("Depends(require_read)") == 4


def test_the_slack_bridge_fails_closed_without_her_own_app(monkeypatch):
    from sportsassets import slack_bridge as S
    for k in list(os.environ):
        if k.startswith("SLACK_ADRIANA_"):
            monkeypatch.delenv(k, raising=False)
    assert "adriana" in S.AGENTS and S.DEDICATED["adriana"] == "adriana:"
    ident = S.dedicated_identity("adriana")
    assert ident["ok"] is False
    assert ident["why"] == "ADRIANA_SLACK_APP_NOT_CONFIGURED"
    assert S.impersonation({"agent": "adriana", "source_key": "adriana:x"}) \
        == "ADRIANA_SLACK_APP_NOT_CONFIGURED_NOTHING_SENT"
    assert S.impersonation({"agent": "eddie", "source_key": "adriana:x"}) \
        == "IMPERSONATION_REFUSED_ADRIANA_CONTENT_ON_ANOTHER_TOKEN"
    # someone else's token is never hers
    monkeypatch.setenv("SLACK_ADRIANA_BOT_TOKEN", "xoxb-shared")
    monkeypatch.setenv("SLACK_EDDIE_BOT_TOKEN", "xoxb-shared")
    assert S.dedicated_identity("adriana")["distinct"] is False


def test_she_is_on_the_floor_the_loop_and_the_work_states():
    from sportsassets import agent_work_state as W
    from sportsassets.agents import collaboration_loop as CL
    from sportsassets.agents import improvement_stages as IS
    from sportsassets.api import command_floor as F
    seat = F.SEAT_BY_SLUG["adriana"]
    assert seat["agent"] == "ADRIANA" and seat["kind"] == "POS_AGENT"
    assert seat["deploy_table"] == "adriana_arb_scans"
    assert seat["authority_level"] == "SHADOW_ONLY"
    assert "ADRIANA" in W.AGENTS and W.MARKET_SOURCES["ADRIANA"] == ("venue",)
    assert "ADRIANA" in CL.SHADOW_PARTICIPANTS
    assert CL.default_peer("ADRIANA") == "KAREN"
    assert "ADRIANA" in IS.OWNER_AGENTS and IS.EVALUATOR_FOR["ADRIANA"] \
        == "AUDREY"


# ═════════════════════════════════════════════════════════════════════
# 3 · THE CENSUS (pure)
# ═════════════════════════════════════════════════════════════════════

NOW = datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc)


def _lv(p, q):
    return {"px": {"value": str(p)}, "qty": str(q)}


def _row(slug, ask, bid, age_s, qty=50, sport="basketball", error=None):
    return {"us_market_slug": slug, "observed_at": NOW - timedelta(
        seconds=age_s), "offers": [_lv(ask, qty)], "bids": [_lv(bid, qty)],
        "error": error, "sport": sport}


INVERTED = [  # OVER 210.5 asks 0.40 while OVER 211.5 is bid 0.47: a middle
    _row("tsc-nba-lal-bos-2026-10-05-210pt5", "0.40", "0.38", 3),
    _row("tsc-nba-lal-bos-2026-10-05-211pt5", "0.49", "0.47", 2)]
TERMS = {(A.POLYMARKET_US, A.TOTAL): {
    "void_payout": Decimal("0.5"), "postponed_payout": Decimal("0.5"),
    "rule": "TEST_TERMS_ESTABLISHED", "label": "TEST"}}


def test_the_slug_grammar_reads_totals_and_spreads_only():
    t = AD.parse_slug("tsc-nba-lal-bos-2026-10-05-fh-110pt5")
    assert t["family"] == A.TOTAL and t["period"] == "FIRST_HALF"
    assert t["threshold"] == Decimal("110.5")
    assert t["event"] == "nba-lal-bos-2026-10-05"
    s = AD.parse_slug("asc-nfl-ne-sea-2026-09-09-pos-3pt5")
    assert s["family"] == A.SPREAD and s["threshold"] == Decimal("-3.5")
    assert AD.parse_slug("asc-nfl-ne-sea-2026-09-09-neg-3pt5")[
        "threshold"] == Decimal("3.5")
    assert AD.parse_slug("asc-nfl-ne-sea-2026-09-09-3pt5") is None
    assert AD.parse_slug("aec-nba-lal-bos-2026-10-05") is None
    assert AD.parse_slug("") is None


def test_unread_settlement_terms_refuse_every_structure():
    r = AD.census(INVERTED, NOW)
    assert r["opportunities"] == []
    assert r["census"]["conditional_candidates"] == 1
    assert r["census"]["void_terms_established"] is False
    for rec in r["refusals"]:
        assert rec["verdict"] == A.REFUSED
        assert AD.VOID_TERMS_NOT_ESTABLISHED in A.reason_codes(rec)
    top = r["refusals"][0]
    assert top["conditional_on"] == AD.HYPOTHESIS["label"]
    assert A.reason_codes(top)[0] == AD.VOID_TERMS_NOT_ESTABLISHED
    assert Decimal(top["economics"]["worst_case_net_profit"]) > 0
    # every considered structure is recorded exactly once
    assert r["census"]["pairs_considered"] == len(r["refusals"]) == 4


def test_established_terms_prove_the_middle_and_nothing_else():
    r = AD.census(INVERTED, NOW, void_terms=TERMS)
    assert len(r["opportunities"]) == 1
    opp = r["opportunities"][0]
    assert opp["verdict"] == A.GUARANTEED_AFTER_COSTS
    assert opp["structure_kind"] == A.MIDDLE_FLOOR
    assert Decimal(opp["economics"]["worst_case_net_profit"]) > 0
    assert len(r["refusals"]) == 3
    # MUTATION: the same book 31 s old is stale -> refused, not proven
    stale = [dict(x, observed_at=NOW - timedelta(seconds=31))
             for x in INVERTED]
    r2 = AD.census(stale, NOW, void_terms=TERMS)
    assert r2["opportunities"] == []
    # the payoff is judged before the books: the three impossible pairs stay
    # PAYOFF_FLOOR_BELOW_COST, the would-be middle is refused as STALE_BOOK
    codes = [A.reason_codes(x) for x in r2["refusals"]]
    assert len(codes) == 4 and sum("STALE_BOOK" in c for c in codes) == 1
    assert sum(c == ["PAYOFF_FLOOR_BELOW_COST"] for c in codes) == 3
    # MUTATION: raise the ask past breakeven -> refused
    dear = [dict(INVERTED[0], offers=[_lv("0.47", 50)]), INVERTED[1]]
    assert AD.census(dear, NOW, void_terms=TERMS)["opportunities"] == []
    # MUTATION: established terms for a different family prove nothing
    other = {(A.POLYMARKET_US, A.SPREAD): TERMS[(A.POLYMARKET_US, A.TOTAL)]}
    assert AD.census(INVERTED, NOW, void_terms=other)["opportunities"] == []


def test_skipped_rows_are_counted_by_reason_never_guessed():
    rows = INVERTED + [
        _row("aec-nba-lal-bos-2026-10-05", "0.5", "0.48", 1),
        _row("tsc-nba-lal-bos-2026-10-05-212", "0.4", "0.38", 1),
        _row("not-a-slug", "0.4", "0.38", 1),
        _row("tsc-nba-lal-bos-2026-10-05-213pt5", "0.3", "0.28", 1,
             error="TIMEOUT")]
    u = AD.build_universe(rows)
    assert u["skipped"] == {
        "MONEYLINE_TIE_AND_OVERTIME_TERMS_NOT_MODELLED": 1,
        "WHOLE_NUMBER_LINE_PUSH_TERMS_NOT_READ": 1,
        "SLUG_GRAMMAR_NOT_READ": 1, "BOOK_READ_FAILED": 1}
    assert u["markets"] == 2


def test_venues_without_recorded_books_are_unavailable_with_the_reason():
    v = AD.venue_support(AD.census(INVERTED, NOW))
    assert v[A.POLYMARKET_US]["status"] == "SUPPORTED"
    assert v[A.KALSHI]["status"] == "UNAVAILABLE"
    assert v[A.KALSHI]["why"].startswith("NO_KALSHI_BOOK_SOURCE")
    assert v[A.POLYMARKET]["status"] == "UNAVAILABLE"
    empty = AD.venue_support(AD.census([], NOW))
    assert empty[A.POLYMARKET_US]["status"] == "UNAVAILABLE"
    assert empty[A.POLYMARKET_US]["why"] == "NO_RECORDED_BOOK_IN_THE_WINDOW"


# ═════════════════════════════════════════════════════════════════════
# 4 · THE DATABASE
# ═════════════════════════════════════════════════════════════════════

async def _tx():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    return conn, tx


async def _refused(conn, sql, *args, match=None):
    import asyncpg
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(asyncpg.PostgresError) as e:
            await conn.execute(sql, *args)
        if match:
            assert match in str(e.value), str(e.value)
    finally:
        await sp.rollback()


@pg
@pytest.mark.asyncio
async def test_the_seed_equals_the_code():
    conn, tx = await _tx()
    try:
        row = await I.current_identity(conn, "ADRIANA")
        spec = I.identity_row("ADRIANA")
        for k in I.IDENTITY_FIELDS + ("identity_version", "content_sha",
                                      "source_directive", "source_ref"):
            assert row[k] == spec[k], k
        vp = await I.current_voice_profile(conn, "ADRIANA")
        for k in ("voice_profile_id", "provider", "provider_voice_alias",
                  "provider_voice_id", "locale", "speaking_rate",
                  "assignment", "style_instructions"):
            assert vp[k] == I.VOICE_SPEC["ADRIANA"][k], k
        # her authority is fixed in the database too
        await _refused(conn, (
            "INSERT INTO agent_identity_versions (agent_id, identity_version,"
            " display_name, title, presentation, role, mission, "
            " personality_traits, communication_style, default_voice_profile,"
            " expertise_domains, decision_principles, may, may_not, "
            " signature, authority_status, content_sha, approved_by) "
            "VALUES ('ADRIANA', 2, 'A', 't', 'MALE', 'r', 'm', '[]', 's', "
            " 'vp', '[]', '[]', '[]', '[\"x\"]', 'sig', 'SHADOW_ONLY', $1, "
            " 'PENDING_OWNER_APPROVAL')"), spec["content_sha"])
        await _refused(conn, (
            "INSERT INTO agent_identity_versions (agent_id, identity_version,"
            " display_name, title, presentation, role, mission, "
            " personality_traits, communication_style, default_voice_profile,"
            " expertise_domains, decision_principles, may, may_not, "
            " signature, authority_status, content_sha, approved_by) "
            "VALUES ('ADRIANA', 2, 'A', 't', 'FEMALE', 'r', 'm', '[]', 's', "
            " 'vp', '[]', '[]', '[]', '[\"x\"]', 'sig', "
            " 'ENTRY_REQUEST_THROUGH_GATED_PATH', $1, "
            " 'PENDING_OWNER_APPROVAL')"), spec["content_sha"])
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_database_refuses_her_any_authority():
    conn, tx = await _tx()
    try:
        assert await conn.fetchval("SELECT pos_agent_actor('agent:adriana')") \
            == "ADRIANA"
        assert await conn.fetchval(
            "SELECT pos_agent_actor('Adriana Ruiz')") is None
        assert await conn.fetchval(
            "SELECT improve_is_machine_actor('ADRIANA')") is True
        assert await conn.fetchval(
            "SELECT poslearn_is_agent_actor('adriana-arb')") is True
        sp = conn.transaction()
        await sp.start()
        await PA.act_as(conn, "ADRIANA")
        await _refused(conn, "INSERT INTO paper_control (control_key, "
                             "enabled, updated_by) VALUES ('x', true, 'y')",
                       match="ADRIANA_HAS_NO_AUTHORITY")
        await sp.rollback()
        await R.ensure_identities(conn)
        t = await R.create_task(conn, assignee="ADRIANA", created_by="ADRIANA",
                                kind="T", title="t", spec={},
                                task_id="adr-test-task")
        assert t["ok"], t
        await _refused(conn, (
            "INSERT INTO agent_task_events (task_id, at, kind, actor, detail)"
            " VALUES ('adr-test-task', now(), 'APPROVED', 'ADRIANA', "
            " '{\"status_to\": \"APPROVED\"}')"),
            match="ADRIANA_HAS_NO_AUTHORITY")
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_her_records_are_append_only_shadow_and_honest():
    conn, tx = await _tx()
    try:
        auth = json.dumps(AD.AUTHORITY)
        ins = ("INSERT INTO adriana_arb_scans (scan_id, started_at, "
               " finished_at, status, why, engine_version, venues, "
               " markets_read, books_fresh, structures_considered, "
               " opportunities, refusals_total, refusals_recorded, "
               " by_verdict, by_kind, by_code, limits, authority) VALUES "
               " ($1, now(), now(), $2, $3, 'v', '{}', 0, 0, $4, $5, $6, "
               " $7, '{}', '{}', '{}', '{}', $8::jsonb)")
        await conn.execute(ins, "s1", "OK", None, 2, 0, 2, 2, auth)
        # counts must add up; a non-OK pass names its reason
        await _refused(conn, ins, "s2", "OK", None, 3, 0, 2, 2, auth)
        await _refused(conn, ins, "s3", "NO_EVIDENCE", None, 0, 0, 0, 0, auth)
        await _refused(conn, ins, "s4", "OK", None, 2, 0, 2, 3, auth)
        # a pass that claims any authority is refused
        bad = json.dumps(dict(AD.AUTHORITY, submit=True))
        await _refused(conn, ins, "s5", "OK", None, 2, 0, 2, 2, bad)
        await _refused(conn, "UPDATE adriana_arb_scans SET why='x'",
                       match="append-only")
        await _refused(conn, "DELETE FROM adriana_arb_scans",
                       match="append-only")
        opp = ("INSERT INTO adriana_arb_opportunities (opportunity_id, "
               " scan_id, structure_kind, event_key, venues, legs, verdict, "
               " max_qty, min_payout_usd, total_cost_usd, net_profit_usd, "
               " edge_per_set_usd, books, economics, leg_plan, "
               " evidence_refs, decided_at, mode) VALUES ($1, 's1', "
               " 'MIDDLE_FLOOR', 'e', '{POLYMARKET_US}', '[[{}],[{}]]', $2, "
               " 10, 1, 9, $3, 0.1, '[]', '{}', 'null', "
               " '[{\"kind\": \"adriana_arb_scans\", \"id\": \"s1\"}]', "
               " now(), $4)")
        await conn.execute(opp, "o1", "GUARANTEED_AFTER_COSTS",
                           Decimal("1"), "SHADOW")
        # only a proven, profitable, SHADOW structure is an opportunity
        await _refused(conn, opp, "o2", "REFUSED", Decimal("1"), "SHADOW")
        await _refused(conn, opp, "o3", "GUARANTEED_AFTER_COSTS",
                       Decimal("0"), "SHADOW")
        await _refused(conn, opp, "o4", "GUARANTEED_AFTER_COSTS",
                       Decimal("1"), "LIVE")
        ref = ("INSERT INTO adriana_arb_refusals (refusal_id, scan_id, "
               " structure_kind, venues, legs, codes, primary_code, detail, "
               " decided_at) VALUES ($1, 's1', 'PAIR', '{POLYMARKET_US}', "
               " '[]', $2, $3, '{}', now())")
        await conn.execute(ref, "r1", ["STALE_BOOK"], "STALE_BOOK")
        await _refused(conn, ref, "r2", [], "STALE_BOOK")
        await _refused(conn, ref, "r3", ["STALE_BOOK"], "OTHER")
        # the rollback refuses while she has left records
        rb = (ROOT / "migrations" / "rollback"
              / "265_adriana_arbitrage_agent.down.sql").read_text()
        await _refused(conn, rb, match="rollback refused")
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_real_pass_records_collaborates_and_seats_her_on_the_floor():
    from sportsassets.agents import adriana_runner as RUN
    from sportsassets.api import command_floor as F
    conn, tx = await _tx()
    try:
        now = time.time()
        for slug, ask, bid, age in (
                ("tsc-nba-lal-bos-2099-01-01-210pt5", "0.40", "0.38", 3),
                ("tsc-nba-lal-bos-2099-01-01-211pt5", "0.49", "0.47", 2),
                ("aec-nba-lal-bos-2099-01-01", "0.50", "0.48", 2)):
            await conn.execute(
                "INSERT INTO paper_book_observations (us_market_slug, "
                " observed_at, source, bids, offers, read_basis) VALUES "
                " ($1, to_timestamp($2), 'TEST', $3::jsonb, $4::jsonb, "
                " 'TEST')", slug, now - age, json.dumps([_lv(bid, 50)]),
                json.dumps([_lv(ask, 50)]))
        await R.ensure_identities(conn)
        s = await RUN.pass_once(conn, now=now)
        assert s["status"] == "CENSUS_RECORDED", s
        assert s["phase_errors"] == {}, s
        assert s["opportunities"] == 0 and s["conditional"] >= 1
        scan = await conn.fetchrow(
            "SELECT * FROM adriana_arb_scans WHERE scan_id=$1", s["scan_id"])
        assert scan["status"] == "OK" and scan["mode"] == "SHADOW"
        assert scan["production_effect"] == "NONE"
        assert scan["refusals_total"] == scan["refusals_recorded"] >= 4
        venues = json.loads(scan["venues"])
        assert venues["KALSHI"]["status"] == "UNAVAILABLE"
        assert await conn.fetchval(
            "SELECT count(*) FROM adriana_arb_opportunities") == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_conversation_messages WHERE "
            " from_agent='ADRIANA' AND to_agent='AUDREY' AND "
            " message_kind='REVIEW_REQUEST'") == 1
        st = {r["task_id"]: r["status"] for r in await conn.fetch(
            "SELECT task_id, status FROM agent_tasks WHERE "
            " assignee='ADRIANA'")}
        assert st == {"adriana-task-void-terms": "WAITING",
                      "adriana-task-kalshi-books": "WAITING"}
        hb = await conn.fetchrow(
            "SELECT state, runs, errors FROM agent_status WHERE "
            " agent_id='ADRIANA'")
        assert hb["state"] == "DECISION_RECORDED" and hb["errors"] == 0
        floor = await F.build_floor(conn, now=now + 5)
        a = next(x for x in floor["agents"] if x["agent"] == "ADRIANA")
        assert a["deployed"] is True and a["deploy_why"] is None
        assert a["state"] != "NOT_DEPLOYED"
        assert a["work_state"] not in (None, "HANDOFF_PENDING")
        mon = {m["label"]: m["value"] for m in a["monitor"]}
        assert mon["Census passes (24h)"] == 1
        assert mon["Proven after costs (24h)"] == 0
        assert mon["Refused (last pass)"] == scan["refusals_total"]
        assert any(e["from"] == "ADRIANA" and e["to"] == "AUDREY"
                   for e in floor["edges"])
        d = await F.build_agent_detail(conn, "adriana", now=now + 5)
        assert d["outputs"] and all(o["verdict"] == "REFUSED"
                                    for o in d["outputs"])
        # a second pass inside the window: same blockers, no duplicate tasks
        s2 = await RUN.pass_once(conn, now=now + 1)
        assert s2["collaboration"]["tasks"] == 0
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_no_book_in_the_window_waits_for_evidence_and_says_why():
    from sportsassets.agents import adriana_runner as RUN
    conn, tx = await _tx()
    try:
        await R.ensure_identities(conn)
        s = await RUN.pass_once(conn, now=time.time() + 10 * 86400)
        assert s["status"] == "NO_RECORDED_BOOK_IN_WINDOW", s
        scan = await conn.fetchrow(
            "SELECT status, why, structures_considered FROM "
            " adriana_arb_scans WHERE scan_id=$1", s["scan_id"])
        assert scan["status"] == "NO_EVIDENCE"
        assert scan["why"].startswith("NO_RECORDED_BOOK")
        assert scan["structures_considered"] == 0
        assert await conn.fetchval(
            "SELECT state FROM agent_status WHERE agent_id='ADRIANA'") \
            == "WAITING_FOR_EVIDENCE"
    finally:
        await tx.rollback()
        await conn.close()
