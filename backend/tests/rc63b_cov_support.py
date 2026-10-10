"""Shared fixtures for the RC6.3b coverage proofs (pass-stall): far-past
synthetic days, a scratch account standing in for the main paper account, and
cleanup of what the proofs commit. ALL DATA IS SYNTHETIC; nothing touches the
live paper account."""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import uuid

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets.agents import coverage_integrity as C

from tests import paper_harness as H

UTC = _dt.timezone.utc
#: 2026-03-14 18:00 UTC = 14:00 in New York: the same local date in both
NOW = _dt.datetime(2026, 3, 14, 18, 0, tzinfo=UTC).timestamp()
DAY = 86400.0
TAG = "rc63b"
#: a guard so a candidate with no budget FAILS the proofs instead of hanging
GUARD_S = 30.0


def league_name() -> str:
    return "%s_league_%s" % (TAG, uuid.uuid4().hex[:8])


async def seed_league(conn, league: str) -> None:
    """One ledger cycle and one valuation per event, yesterday and today, so
    every day persists a row for `league`."""
    for back, at in ((1, NOW - DAY), (0, NOW - 3600)):
        evs = ["%s-%d-%d" % (league, back, i) for i in range(3)]
        cid = "%s-cyc-%s-%d" % (TAG, league, back)
        for i, ev in enumerate(evs):
            await conn.execute(
                "INSERT INTO ext_candidate_outcomes (cycle_id, cycle_at, "
                " sport_key, family, queue_position, provider_event_id, "
                " us_market_slug, stage, outcome, first_refusal) VALUES "
                " ($1,to_timestamp($2),$3,'family',$4,$5,$6,NULL,"
                " 'ADMITTED',NULL)", cid, at, league, i, ev, "us-%s" % ev)
            await conn.execute(
                "INSERT INTO external_valuations (experiment_id, version, "
                " source_class, provider, book, devig_method, venue, "
                " contract_selection, sport_family, market, raw_odds, "
                " outcomes_priced, expected_outcomes, decision, admissible, "
                " record_purpose, event_key, us_market_slug, decided_at) "
                "VALUES ($1,'v','EXTERNAL_BOOKMAKER_VALUATION','test',"
                " 'pinnacle','power','polymarket_us','home','family','h2h',"
                " '{}'::jsonb,2,2,'NO_TRADE',false,'ENTRY_DECISION',$2,$3,"
                " to_timestamp($4))", TAG.upper(), ev, "us-%s" % ev, at + 1)


async def clean(conn) -> None:
    await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                       C.WATERMARK_KEY)
    await conn.execute("DELETE FROM paper_audrey_findings WHERE kind = $1",
                       "COVERAGE_RUN_FAILED")
    await conn.execute("DELETE FROM coverage_funnel_snapshots WHERE league "
                       "LIKE $1", TAG + "_league_%")
    await conn.execute("DELETE FROM coverage_collapse_alerts WHERE league "
                       "LIKE $1", TAG + "_league_%")
    await conn.execute("DELETE FROM external_valuations WHERE experiment_id=$1",
                       TAG.upper())
    await conn.execute("DELETE FROM ext_candidate_outcomes WHERE cycle_id "
                       "LIKE $1", TAG + "-cyc-%")


@pytest.fixture
def main_account(monkeypatch):
    """The step runs on the MAIN paper account only: a scratch account stands
    in for it (the same constant the step compares against)."""
    # the proofs use sub-second budgets: the minimum a run may start with is
    # lowered to match (the pins and the no-pass-time proof set it back)
    monkeypatch.setattr(C, "MIN_RUN_BUDGET_S", 0.1, raising=False)

    async def make(conn):
        a = await H.new_account(conn, "cov", now=NOW - 5 * DAY)
        monkeypatch.setattr(L, "ACCOUNT_ID", a["account_id"])
        return a
    return make


def step_ctx(a, at, **over):
    ctx = {"session_id": a["session_id"], "account_id": a["account_id"],
           "now": at, "clock": (lambda: at)}
    ctx.update(over)
    return ctx


async def watermark(conn) -> dict | None:
    v = await conn.fetchval("SELECT value FROM ingestion_state WHERE key=$1",
                            C.WATERMARK_KEY)
    return None if v is None else (json.loads(v) if isinstance(v, str)
                                   else dict(v))


async def run_step(conn, ctx):
    return await asyncio.wait_for(C.step(conn, ctx), GUARD_S)
