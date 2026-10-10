"""CAPITAL-CRITICAL: THE LEAGUE READS ARE PLAN-ROBUST, BOUNDED AND UNCHANGED IN
WHAT THEY COUNT (RC6.3b pass-stall).

Production, 2026-10-10 (read-only readbacks): the five league statements
(EVALUATED_SQL, DECISIONS_SQL, ORDERS_SQL, FILLS_SQL, ACTUAL_SQL) take 0.1-0.5 s
from a fresh session on all four coverage windows -- with literal values,
prepared under force_generic_plan and under force_custom_plan, as written or
with the m / ms CTEs MATERIALIZED -- yet pg_stat_statements shows EVALUATED_SQL
2,051 calls, mean 410 ms, max 46.5 s and ACTUAL_SQL 1,800 calls, mean 901 ms,
max 59.4 s, and on the API's long-lived pooled connection the first of them
ran 50+ s every pass. The leading hypothesis (NOT proven): a plan cached by
the pooled connection on old statistics. Whatever the cause, the reads must not
depend on it.

Proved here, on a real Postgres:

  * every coverage read runs inside its transaction with
    plan_cache_mode = force_custom_plan (a plan made for THIS call's values,
    never a cached generic plan) and a statement_timeout of its own;
  * on a connection poisoned with force_generic_plan -- the pathology's shape --
    the coverage statements still run custom plans only (the server's own
    generic_plans counter stays at 0);
  * a statement that cannot be read in its time is cancelled on the server and
    reads as NULL with SOURCE_READ_FAILED, never a zero, and the rest of the run
    goes on; one that hangs past the step's budget is cancelled on the server
    and leaves the connection usable;
  * THE RESULTS ARE THE SAME AS THE STATEMENTS THEY REPLACE: over seeded rows
    (leagues by the event's ledger league, by the venue contract's ledger
    league when the event key is unknown to the ledger, the UNATTRIBUTED
    fallback, the latest ledger row winning, the 14-day lookback and the +1 day
    bound, distinct counting, decided / entered / refused, entry orders only,
    fills, and the actual intents with their mirror fills), funnel_for_day
    returns exactly what the verbatim statements of af40bea2 return.

SYNTHETIC rows on a far-past day in a scratch test database; no venue.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import time
import uuid

import asyncpg
import pytest

from sportsassets.agents import coverage_integrity as C

from tests import paper_harness as H
from tests.rc63b_cov_support import (  # noqa: F401  (main_account is a fixture)
    DAY, GUARD_S, NOW, clean, league_name, main_account, run_step,
    seed_league, step_ctx, watermark)

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
DAY0 = _dt.date(2026, 3, 14)

#: THE STATEMENTS AS THEY WERE AT af40bea2, VERBATIM (the five league reads,
#: each with its m / ms league maps). The equivalence proof runs these on the
#: seeded rows and compares them with what funnel_for_day now returns.
BASELINE_EVALUATED_SQL = '''
    WITH
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league, count(DISTINCT coalesce(ev.event_key,
                                                 ev.id::text)) AS n
      FROM external_valuations ev
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE ev.record_purpose IN ('ENTRY_DECISION', 'CALIBRATION_ONLY')
       AND ev.decided_at >= to_timestamp($1) AND ev.decided_at < to_timestamp($2)
     GROUP BY 1
'''

BASELINE_DECISIONS_SQL = '''
    WITH
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC),
    d AS (
        SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league, coalesce(ev.event_key, pd.us_market_slug) AS ek,
               bool_or(pd.verdict = 'ENTER') AS entered
          FROM paper_decisions pd
          JOIN external_valuations ev ON ev.id = pd.valuation_id
          LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
         WHERE pd.decided_at >= to_timestamp($1)
           AND pd.decided_at < to_timestamp($2)
         GROUP BY 1, 2)
    SELECT league, count(*) AS decided,
           count(*) FILTER (WHERE entered) AS entered,
           count(*) FILTER (WHERE NOT entered) AS refused
      FROM d GROUP BY league
'''

BASELINE_ORDERS_SQL = '''
    WITH
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league,
           count(DISTINCT coalesce(ev.event_key, po.us_market_slug)) AS n
      FROM paper_orders po
      JOIN paper_decisions pd ON pd.decision_id = po.decision_id
      JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE po.role = 'ENTRY'
       AND po.created_at >= to_timestamp($1) AND po.created_at < to_timestamp($2)
     GROUP BY 1
'''

BASELINE_FILLS_SQL = '''
    WITH
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league,
           count(DISTINCT coalesce(ev.event_key, pf.us_market_slug)) AS n
      FROM paper_fills pf
      JOIN paper_orders po ON po.order_id = pf.order_id
      JOIN paper_decisions pd ON pd.decision_id = po.decision_id
      JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE po.role = 'ENTRY'
       AND pf.filled_at >= to_timestamp($1) AND pf.filled_at < to_timestamp($2)
     GROUP BY 1
'''

BASELINE_ACTUAL_SQL = '''
    WITH
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league,
           count(DISTINCT coalesce(ev.event_key, ei.us_market_slug))
               AS actual_intents,
           count(DISTINCT coalesce(ev.event_key, ei.us_market_slug))
               FILTER (WHERE ei.actual_mirror_id IS NOT NULL)
               AS actual_submitted,
           count(DISTINCT coalesce(ev.event_key, ei.us_market_slug))
               FILTER (WHERE EXISTS (
                   SELECT 1 FROM execmirror_orders xo
                    WHERE xo.execution_intent_id = ei.intent_id
                      AND coalesce(xo.cum_qty, 0) > 0)) AS actual_filled
      FROM execution_intents ei
      LEFT JOIN external_valuations ev ON ev.id = ei.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE ei.decided_at >= to_timestamp($1) AND ei.decided_at < to_timestamp($2)
     GROUP BY 1
'''

BASELINE = {
    "EVALUATED_SQL": BASELINE_EVALUATED_SQL,
    "DECISIONS_SQL": BASELINE_DECISIONS_SQL,
    "ORDERS_SQL": BASELINE_ORDERS_SQL,
    "FILLS_SQL": BASELINE_FILLS_SQL,
    "ACTUAL_SQL": BASELINE_ACTUAL_SQL,
}


# ═════════════════════════════════════════════════════════════════════
# SEEDING (direct rows, inside a transaction that is rolled back)
# ═════════════════════════════════════════════════════════════════════

def _at(day: int, hour: int = 10) -> float:
    """Epoch of 2026-03-<day> <hour>:00 UTC."""
    return _dt.datetime(2026, 3, day, hour, tzinfo=_dt.timezone.utc).timestamp()


async def _ledger(conn, *, at, league, event, slug, tag):
    await conn.execute(
        "INSERT INTO ext_candidate_outcomes (cycle_id, cycle_at, sport_key, "
        " family, queue_position, provider_event_id, us_market_slug, stage, "
        " outcome, first_refusal) VALUES ($1,to_timestamp($2),$3,'family',0,"
        " $4,$5,NULL,'ADMITTED',NULL)",
        "rc63b-eq-%s-%s" % (tag, uuid.uuid4().hex[:6]), at, league, event,
        slug)


async def _val(conn, *, event, slug, family="football", at=None,
               purpose="ENTRY_DECISION") -> int:
    return await conn.fetchval(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, "
        " contract_selection, sport_family, market, raw_odds, "
        " outcomes_priced, expected_outcomes, decision, admissible, "
        " record_purpose, event_key, us_market_slug, decided_at, "
        " calibration_only_evidence) VALUES ('RC63B_EQ','v',"
        " 'EXTERNAL_BOOKMAKER_VALUATION','test','pinnacle','power',"
        " 'polymarket_us','home',$1,'h2h','{}'::jsonb,2,2,'NO_TRADE',false,$2,"
        " $3,$4,to_timestamp($5), $6::jsonb) RETURNING id",
        family, purpose, event, slug, at if at is not None else _at(14),
        None if purpose == "ENTRY_DECISION" else
        '{"usable_for_orders": false, "basis": "RC63B"}')


_N = {"i": 0}


def _uid(prefix: str) -> str:
    _N["i"] += 1
    return "%s-rc63b-%d-%s" % (prefix, _N["i"], uuid.uuid4().hex[:6])


async def _decision(conn, acct, *, vid, slug, verdict, at=None):
    did = _uid("paperdec")
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, valuation_id, us_market_slug, holding_side, intent, "
        " fixture, verdict, refusal, internal_model, pinnacle, "
        " qualification_gaps, policy_version, simulator_version, strategy) "
        "VALUES ($1,$2,$3,to_timestamp($4),$5,$6,'LONG',"
        " 'ORDER_INTENT_BUY_LONG','fx',$7,$8,'{}'::jsonb,'{}'::jsonb,"
        " '[]'::jsonb,'p','s','DEREK_ENTRY_POLICY_V2')",
        did, acct["session_id"], acct["account_id"],
        at if at is not None else _at(14, 11), vid, slug, verdict,
        None if verdict == "ENTER" else "REFUSED_FOR_THE_PROOF")
    return did


async def _order(conn, acct, *, did, slug, role="ENTRY", at=None):
    oid = _uid("paperord")
    t = at if at is not None else _at(14, 12)
    await conn.execute(
        "INSERT INTO paper_orders (order_id, idempotency_key, account_id, "
        " session_id, group_id, role, direction, holding_side, intent, "
        " us_market_slug, order_type, time_in_force, allow_partial, qty, "
        " limit_price, wire_price, state, decided_at, eligible_at, "
        " expires_at, simulator_version, decision_id, strategy, created_at)"
        " VALUES ($1,$2,$3,$4,$5,$6,'BUY','LONG','ORDER_INTENT_BUY_LONG',$7,"
        " 'MARKETABLE','IOC',true,10,0.5,0.5,'FILLED',to_timestamp($8),"
        " to_timestamp($8),to_timestamp($8 + 90),'s',$9,"
        " 'DEREK_ENTRY_POLICY_V2',to_timestamp($8))",
        oid, "k-" + oid, acct["account_id"], acct["session_id"], "g-" + oid,
        role, slug, t, did)
    return oid


async def _fill(conn, acct, *, oid, slug, at=None):
    fid = _uid("paperfill")
    await conn.execute(
        "INSERT INTO paper_fills (fill_id, idempotency_key, order_id, "
        " account_id, session_id, group_id, role, direction, holding_side, "
        " us_market_slug, qty, price, wire_price, fee_usd, gross_usd, "
        " filled_at, basis, simulator_version, strategy) VALUES ($1,$2,$3,"
        " $4,$5,'g','ENTRY','BUY','LONG',$6,10,0.5,0.5,0,5,to_timestamp($7),"
        " 'DEPTH_WALK_WITHIN_LIMIT','s','DEREK_ENTRY_POLICY_V2')",
        fid, "k-" + fid, oid, acct["account_id"], acct["session_id"], slug,
        at if at is not None else _at(14, 13))


async def _intent(conn, *, did, vid, slug, mirror_cum=None, submitted=False):
    """An actual-side intent; `submitted` makes it live-admitted and gives it
    a mirror order filled `mirror_cum` (None = no mirror order)."""
    iid = _uid("ei")
    await conn.execute(
        "INSERT INTO execution_intents (intent_id, decision_id, valuation_id,"
        " strategy, us_market_slug, order_intent, group_id, order_type, "
        " time_in_force, paper_target_qty, wire_price, decided_at, "
        " live_eligible, live_eligibility, actual_state, actual_refusal) "
        "VALUES ($1,$2,$3,'DEREK_ENTRY_POLICY_V2',$4,'ORDER_INTENT_BUY_LONG',"
        " 'g','MARKETABLE','IOC',10,0.5,to_timestamp($5),$6,$7::jsonb,$8,$9)",
        iid, did, vid, slug, _at(14, 12), submitted,
        json.dumps({"admission": {"verdict": "LIVE_ADMISSIBLE"}}
                   if submitted else {}),
        "DISPATCHED" if submitted else "PAPER_ONLY",
        None if submitted else "NOT_LIVE_FOR_THE_PROOF")
    if submitted:
        mid = _uid("m")
        await conn.execute(
            "INSERT INTO execmirror_orders (mirror_id, execution_intent_id, "
            " role, us_market_slug, intent, order_type, tif, state, cum_qty) "
            "VALUES ($1,$2,'ENTRY',$3,'ORDER_INTENT_BUY_LONG','MARKETABLE',"
            " 'IOC','FILLED',$4)", mid, iid, slug, mirror_cum or 0)
        await conn.execute("UPDATE execution_intents SET actual_mirror_id=$2 "
                           "WHERE intent_id=$1", iid, mid)
    return iid


NCAAF = "americanfootball_ncaaf"
MLB = "baseball_mlb"


async def _seed_equivalence(conn, acct) -> None:
    """The league-attribution cases, each with its own counts."""
    # --- the ledger (what the league maps are built from)
    await _ledger(conn, at=_at(14), league=NCAAF, event="ev-n1",
                  slug="us-n1", tag="n1")
    await _ledger(conn, at=_at(14), league=NCAAF, event="ev-n2",
                  slug="us-n2", tag="n2")
    await _ledger(conn, at=_at(14), league=MLB, event="ev-m1", slug="us-m1",
                  tag="m1")
    await _ledger(conn, at=_at(14), league=MLB, event="ev-m2", slug="us-m2",
                  tag="m2")
    # THE LATEST LEDGER ROW WINS: older under another key, newer under NCAAF
    await _ledger(conn, at=_at(10), league="pinnapi_football",
                  event="ev-s1", slug="us-s1", tag="s1a")
    await _ledger(conn, at=_at(13), league=NCAAF, event="ev-s1",
                  slug="us-s1", tag="s1b")
    # THE SLUG FALLBACK: the valuation's event key is unknown to the ledger,
    # the ledger recorded its venue contract under MLB
    await _ledger(conn, at=_at(14), league=MLB, event="ev-f1", slug="us-f1",
                  tag="f1")
    # THE EVENT'S LEAGUE BEATS THE SLUG'S: ev-p1 is NCAAF, its contract MLB
    await _ledger(conn, at=_at(14), league=NCAAF, event="ev-p1",
                  slug="us-p1x", tag="p1a")
    await _ledger(conn, at=_at(14), league=MLB, event="ev-p1y",
                  slug="us-p1", tag="p1b")
    # THE LOOKBACK: a ledger row more than 14 days before the window start
    # (2026-03-14 less 14 days = 02-28) does not attribute
    await _ledger(conn, at=_at(14, 0) - 15 * DAY, league=MLB,
                  event="ev-old", slug="us-old", tag="old")
    # THE UPPER BOUND: a row after window end + 1 day does not attribute
    await _ledger(conn, at=_at(17), league=NCAAF, event="ev-future",
                  slug="us-future", tag="fut")

    # --- the valuations (decided on the window's day, 2026-03-14)
    v1 = await _val(conn, event="ev-n1", slug="us-n1")
    v1b = await _val(conn, event="ev-n1", slug="us-n1",
                     at=_at(14) + 5)                            # same event
    v2 = await _val(conn, event="ev-n2", slug="us-n2")
    v3 = await _val(conn, event="ev-m1", slug="us-m1")
    v4 = await _val(conn, event="sticky-f1", slug="us-f1")      # slug path
    v5 = await _val(conn, event="ev-s1", slug="us-s1")          # latest wins
    v6 = await _val(conn, event="ev-p1", slug="us-p1")          # event wins
    v7 = await _val(conn, event="ev-old", slug="us-old", family="tennis")
    await _val(conn, event="ev-future", slug="us-future", family="tennis")
    await _val(conn, event="ev-m2", slug="us-m2",
               purpose="CALIBRATION_ONLY")
    await _val(conn, event=None, slug="us-m1")                  # no key
    # outside the window: yesterday's valuation of ev-n1 is not counted today
    await _val(conn, event="ev-n1", slug="us-n1", at=_at(13, 12))
    # a valuation with no ledger trace and no slug: UNATTRIBUTED:<family>
    v11 = await _val(conn, event="ghost-1", slug="us-ghost",
                     family="tennis")

    # --- the decisions
    d1 = await _decision(conn, acct, vid=v1, slug="us-n1", verdict="ENTER")
    await _decision(conn, acct, vid=v1b, slug="us-n1", verdict="REFUSE")
    await _decision(conn, acct, vid=v2, slug="us-n2", verdict="REFUSE")
    await _decision(conn, acct, vid=v3, slug="us-m1", verdict="REFUSE")
    d4 = await _decision(conn, acct, vid=v4, slug="us-f1", verdict="ENTER")
    d5 = await _decision(conn, acct, vid=v7, slug="us-old", verdict="ENTER")
    await _decision(conn, acct, vid=v5, slug="us-s1", verdict="REFUSE")
    await _decision(conn, acct, vid=v6, slug="us-p1", verdict="REFUSE")
    await _decision(conn, acct, vid=v11, slug="us-ghost", verdict="REFUSE")
    # decided outside the window: not counted
    v12 = await _val(conn, event="ev-n2", slug="us-n2", at=_at(14) + 7)
    await _decision(conn, acct, vid=v12, slug="us-n2", verdict="ENTER",
                    at=_at(13, 11))

    # --- the entry orders (an EXIT order is not an entry)
    o1 = await _order(conn, acct, did=d1, slug="us-n1")
    o4 = await _order(conn, acct, did=d4, slug="us-f1")
    o5 = await _order(conn, acct, did=d5, slug="us-old")
    await _order(conn, acct, did=d1, slug="us-n1", role="EXIT")

    # --- the fills (of entry orders)
    await _fill(conn, acct, oid=o1, slug="us-n1")
    await _fill(conn, acct, oid=o4, slug="us-f1")
    await _fill(conn, acct, oid=o5, slug="us-old", at=_at(13, 9))   # outside

    # --- the actual-side intents
    await _intent(conn, did=d1, vid=v1, slug="us-n1", submitted=True,
                  mirror_cum=3)                                  # NCAAF filled
    await _intent(conn, did=d4, vid=v4, slug="us-f1", submitted=True,
                  mirror_cum=0)                                  # MLB, 0 fill
    await _intent(conn, did=d5, vid=None, slug="us-old")         # no valuation


class _Spy:
    """The connection, delegating, noting at each fetch which plan mode and
    statement timeout the read runs under and whether it is in a transaction."""

    def __init__(self, real):
        self._real = real
        self.seen = []

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def fetch(self, sql, *args, **kw):
        self.seen.append((
            " ".join(sql.split()[:3]),
            await self._real.fetchval("SHOW plan_cache_mode"),
            await self._real.fetchval("SHOW statement_timeout"),
            self._real.is_in_transaction()))
        return await self._real.fetch(sql, *args, **kw)


SOURCES = (
    ("evaluated", "EVALUATED_SQL", {"n": "evaluated_events"}),
    ("decisions", "DECISIONS_SQL", {"decided": "decided_events",
                                    "entered": "entered_events",
                                    "refused": "refused_events"}),
    ("orders", "ORDERS_SQL", {"n": "ordered_events"}),
    ("fills", "FILLS_SQL", {"n": "filled_events"}),
    ("actual", "ACTUAL_SQL", {"actual_intents": "actual_intents",
                              "actual_submitted": "actual_submitted",
                              "actual_filled": "actual_filled"}),
)


def _expected_from_baseline(rows_by_source: dict) -> dict:
    """{league: {column: n}} as the af40bea2 statements give it (a league the
    statement does not return has no entry for that column)."""
    out: dict = {}
    for name, _, cols in SOURCES:
        for g in rows_by_source[name]:
            r = out.setdefault(g["league"], {})
            for src, col in cols.items():
                r[col] = int(g[src] or 0)
    return out


@pg
@pytest.mark.parametrize("poisoned", [False, True],
                         ids=["fresh_connection", "poisoned_with_generic_plans"])
async def test_the_league_counts_are_the_same_as_the_statements_they_replace(
        poisoned):
    """THE EQUIVALENCE PROOF, on a fresh connection and on one poisoned with
    generic plans (the pathology's shape): same counts, and every read of the
    code under test ran its custom plan (FAILS on af40bea2: its reads run
    whatever plan the connection holds). Seeded rows cover: the event's ledger league,
    the venue contract's ledger league when the event key is unknown to the
    ledger (the ms slug fallback), the UNATTRIBUTED:<family> fallback, the
    event's league beating the contract's, the latest ledger row winning, the
    14-day lookback and the +1 day bound, distinct counting (two valuations of
    one event, a valuation with no event key), decided / entered / refused,
    entry orders only, fills, and actual intents with their mirror fills."""
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        acct = await H.new_account(conn, "eq", now=NOW - 5 * DAY)
        await _seed_equivalence(conn, acct)
        start, end = C.day_window(DAY0, "UTC")
        if poisoned:
            await conn.execute("SET plan_cache_mode = force_generic_plan")

        # THE BASELINE: the verbatim af40bea2 statements, as written
        baseline = {}
        for name, const, _ in SOURCES:
            baseline[name] = [dict(r) for r in await conn.fetch(
                BASELINE[const], start, end)]
        want = _expected_from_baseline(baseline)

        # THE CODE UNDER TEST (through a spy that notes each read's plan mode)
        spy = _Spy(conn)
        got = await C.funnel_for_day(spy, DAY0, "UTC")
        assert [m for _, m, _, _ in spy.seen] == ["force_custom_plan"] * 7, \
            spy.seen
        leagues = got["leagues"]
        for league, cols in want.items():
            assert league in leagues, (league, sorted(leagues))
            for col, n in cols.items():
                assert leagues[league][col] == n, (league, col, n,
                                                   leagues[league][col])
        # nothing the baseline did not see is invented: every count the new
        # read reports for a league the baseline knows is the baseline's, and
        # a league only the new read has carries zeros / provider counts only
        for league in set(leagues) - set(want):
            for _, _, cols in SOURCES:
                for col in cols.values():
                    assert not leagues[league].get(col), (league, col)

        # AND THE NUMBERS THEMSELVES (so the proof does not rest on the
        # baseline alone): hand-counted from the seeded rows
        ev = {k: v["evaluated_events"] for k, v in leagues.items()
              if v.get("evaluated_events")}
        assert ev == {
            # ev-n1 (two valuations, one event), ev-n2, ev-s1 (latest ledger
            # row = NCAAF), ev-p1 (the event's league beats the contract's)
            NCAAF: 4,
            # ev-m1, sticky-f1 (via the contract us-f1), ev-m2 (calibration
            # only), and the valuation with no event key (via us-m1)
            MLB: 4,
            # ev-old (outside the lookback), ev-future (past the bound), ghost
            "UNATTRIBUTED:tennis": 3}, ev
        dec = {k: (v["decided_events"], v["entered_events"],
                   v["refused_events"]) for k, v in leagues.items()
               if v.get("decided_events")}
        assert dec == {
            # ev-n1 ENTER + REFUSE -> one event, entered; ev-n2 REFUSE;
            # ev-s1 REFUSE; ev-p1 REFUSE (yesterday's ENTER is outside)
            NCAAF: (4, 1, 3),
            # ev-m1 REFUSE; sticky-f1 ENTER
            MLB: (2, 1, 1),
            # ev-old ENTER (unattributed), ghost-1 REFUSE
            "UNATTRIBUTED:tennis": (2, 1, 1)}, dec
        assert {k: v["ordered_events"] for k, v in leagues.items()
                if v.get("ordered_events")} == {
            NCAAF: 1, MLB: 1, "UNATTRIBUTED:tennis": 1}
        assert {k: v["filled_events"] for k, v in leagues.items()
                if v.get("filled_events")} == {NCAAF: 1, MLB: 1}
        act = {k: (v["actual_intents"], v["actual_submitted"],
                   v["actual_filled"]) for k, v in leagues.items()
               if v.get("actual_intents")}
        assert act == {NCAAF: (1, 1, 1), MLB: (1, 1, 0),
                       "UNATTRIBUTED:unknown": (1, 0, 0)}, act
        # a measured zero is a zero; nothing is NULL here (all sources read)
        assert not got["unavailable"]
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# PLAN-ROBUST: A CUSTOM PLAN, ON EVERY READ, WHATEVER THE CONNECTION HOLDS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_every_coverage_read_runs_with_a_custom_plan_and_a_statement_timeout():
    """FAILS on af40bea2: the reads run under whatever plan_cache_mode the
    connection holds ('auto' by default, 'force_generic_plan' on a poisoned
    one) and no statement timeout."""
    conn = await asyncpg.connect(H.DSN)
    try:
        spy = _Spy(conn)
        got = await C.funnel_for_day(spy, DAY0, "UTC")
        assert len(spy.seen) == 7           # provider, 5 league reads, venue
        for sql, mode, _, in_tx in spy.seen:
            assert mode == "force_custom_plan", (sql, mode)
            assert in_tx is True, "SET LOCAL is the read's own transaction"
        want_timeout = "%ds" % (C.READ_STATEMENT_TIMEOUT_MS // 1000)
        for sql, _, timeout, _ in spy.seen:
            assert timeout == want_timeout, (sql, timeout)
        # and it does not leak: outside the read the connection is as it was
        assert await conn.fetchval("SHOW plan_cache_mode") == "auto"
        assert await conn.fetchval("SHOW statement_timeout") == "0"
    finally:
        await conn.close()


@pg
async def test_on_a_connection_poisoned_with_generic_plans_the_coverage_reads_still_use_custom_plans():
    """THE PATHOLOGY'S SHAPE: a pooled connection whose statements run on
    cached generic plans. The server's own counters (pg_prepared_statements
    generic_plans / custom_plans) show the coverage statements never run a
    generic plan after the fix. FAILS on af40bea2: every one does."""
    conn = await asyncpg.connect(H.DSN)
    try:
        await conn.execute("SET plan_cache_mode = force_generic_plan")
        await C.funnel_for_day(conn, DAY0, "UTC")
        await C.funnel_for_day(conn, DAY0 - _dt.timedelta(days=1), "UTC")
        rows = await conn.fetch(
            "SELECT generic_plans, custom_plans FROM pg_prepared_statements "
            " WHERE (statement ILIKE '%ext_candidate_outcomes%' "
            "    OR statement ILIKE '%us_premap%') "
            "   AND statement NOT ILIKE '%pg_prepared_statements%'")
        assert len(rows) >= 7, "the coverage statements are the ones counted"
        assert sum(r["generic_plans"] for r in rows) == 0
        assert sum(r["custom_plans"] for r in rows) >= 14
    finally:
        await conn.close()


@pg
async def test_a_read_without_plan_cache_mode_support_still_reads(monkeypatch):
    """A server that predates plan_cache_mode (PostgreSQL < 12) is not asked to
    set it: the read still runs, bounded by its statement timeout."""
    conn = await asyncpg.connect(H.DSN)
    try:
        spy = _Spy(conn)
        monkeypatch.setattr(C, "_supports_plan_cache_mode",
                            lambda c: False)
        await C.funnel_for_day(spy, DAY0, "UTC")
        assert all(mode == "auto" for _, mode, _, _ in spy.seen)
        assert all(t == "%ds" % (C.READ_STATEMENT_TIMEOUT_MS // 1000)
                   for _, _, t, _ in spy.seen)
    finally:
        await conn.close()


def test_the_read_timeout_is_a_fraction_of_the_runs_budget():
    assert C.READ_STATEMENT_TIMEOUT_MS / 1000.0 < C.RUN_BUDGET_S
    assert C.READ_STATEMENT_TIMEOUT_MS >= 5000, \
        "ten times a normal read (0.1-0.5 s), not tighter than the data needs"


# ═════════════════════════════════════════════════════════════════════
# A STATEMENT THAT CANNOT BE READ IN TIME, OR HANGS ON THE SERVER
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_hung_league_statement_is_cancelled_on_the_server_and_the_connection_is_usable(
        main_account, monkeypatch):
    conn = await asyncpg.connect(H.DSN)
    try:
        a = await main_account(conn)
        league = league_name()
        await seed_league(conn, league)
        marker = "rc63b-hung-evaluated-%s" % uuid.uuid4().hex[:6]
        monkeypatch.setattr(
            C, "EVALUATED_SQL",
            "SELECT pg_sleep(3600), 'x'::text AS league, 1 AS n "
            "FROM (SELECT $1::float8 AS a, $2::float8 AS b) p /* %s */"
            % marker)
        # the server-side timeout is out of the way: the step's own budget
        # is what cuts the statement
        monkeypatch.setattr(C, "READ_STATEMENT_TIMEOUT_MS", 3_600_000)
        monkeypatch.setattr(C, "RUN_BUDGET_S", 1.0)
        res = await run_step(conn, step_ctx(a, NOW))
        assert res["timed_out"] is True and res["error"] == C.R_RUN_TIMED_OUT
        assert "evaluated" in res["in_flight"], res["in_flight"]
        other = await asyncpg.connect(H.DSN)
        try:
            running = await other.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE state='active' "
                " AND query LIKE $1", "%" + marker + "%")
            assert running == 0, "the statement is not left running"
        finally:
            await other.close()
        # the connection is usable and not left in a transaction
        assert not conn.is_in_transaction()
        assert await conn.fetchval("SELECT 1") == 1
        assert (await watermark(conn))["timed_out"] is True
    finally:
        await clean(conn)
        await conn.close()


@pg
async def test_a_source_that_cannot_be_read_in_time_is_null_with_its_reason_and_the_run_goes_on(
        main_account, monkeypatch):
    conn = await asyncpg.connect(H.DSN)
    try:
        a = await main_account(conn)
        league = league_name()
        await seed_league(conn, league)
        monkeypatch.setattr(
            C, "EVALUATED_SQL",
            "SELECT pg_sleep(3600), 'x'::text AS league, 1 AS n "
            "FROM (SELECT $1::float8 AS a, $2::float8 AS b) p")
        monkeypatch.setattr(C, "READ_STATEMENT_TIMEOUT_MS", 300)
        t0 = time.monotonic()
        res = await run_step(conn, step_ctx(a, NOW))
        assert res["ran"] is True and res.get("error") is None, res
        assert time.monotonic() - t0 < 20.0
        row = await conn.fetchrow(
            "SELECT * FROM coverage_funnel_snapshots WHERE league=$1 AND "
            " tz='UTC' AND day='2026-03-14'", league)
        assert row["provider_events"] == 3            # the other sources read
        assert row["evaluated_events"] is None        # never a zero
        un = json.loads(row["unavailable"])
        assert un["evaluated_events"] == "%s:QueryCanceledError" % \
            C.R_READ_FAILED
        assert not conn.is_in_transaction()
    finally:
        await clean(conn)
        await conn.close()
