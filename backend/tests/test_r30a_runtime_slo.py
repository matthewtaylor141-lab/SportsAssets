"""R30A RUNTIME SLOs: GET /api/command/slo -- eight SLOs, each a target, a
measured value, a window and OK / BREACH / UNAVAILABLE(reason).

  §1 JUDGES ON PRODUCTION NUMBERS. The values the read-only production reads
     returned on 2026-10-04 (research-sql runs 37226381750 / 37226551461)
     give the verdicts they should: feed p50 8.61 / p90 25.27 s vs 30 s -> OK;
     reactive latency p50 5.78 / p90 11.07 s vs 12 s -> OK; 58 of 60 open
     positions without a current review -> BREACH; the SCORES component
     FAILED four times before 17:17Z and OK at 17:22 / 18:23 -> BREACH then
     OK; no production cutover -> UNAVAILABLE(NO_PRODUCTION_CUTOVER_RECORDED);
     API 191b299 == workers 191b299 vs the latest receipt -> the comparison.
  §2 NEVER A MANUFACTURED VALUE. An empty window, an absent table, an unset
     SHA is UNAVAILABLE with its reason; no SLO is OK by default.
  §3 THE READ ON POSTGRES, production-shaped rows inserted in a transaction
     that is rolled back: every SLO answers, inside a READ ONLY transaction
     with a statement timeout, and a write inside it is refused.
  §4 TARGETS ARE THE SYSTEM'S OWN BOUNDS, pinned to their sources.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import pytest

from sportsassets import runtime_slo as S

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")
NOW = 1_791_140_400.0          # 2026-10-04 19:00Z, the readback's hour


# ── §1 judges on production numbers ────────────────────────────────────

def test_feed_freshness_production_is_ok_and_a_slow_p90_breaches():
    prod = {"decisions": 4209, "with_age": 3750, "p50": 8.61, "p90": 25.27,
            "over_limit": 0, "limit_max": 30.0}
    got = S.judge_feed(prod)
    assert got["status"] == S.OK
    assert got["measured"]["p50_age_s"] == 8.61
    assert got["measured"]["without_recorded_age"] == 459
    assert got["target"]["p90_age_s_at_most"] == 30.0
    slow = S.judge_feed(dict(prod, p90=31.2))
    assert slow["status"] == S.BREACH
    assert slow["why"] == "P90_PINNACLE_AGE_ABOVE_30S"


def test_decision_latency_production_is_ok_and_past_the_deadline_breaches():
    prod = {"completed": 847, "p50": 5.78, "p90": 11.07, "max": 25.66,
            "timeouts": 33, "orphaned": 19}
    got = S.judge_latency(prod)
    assert got["status"] == S.OK
    assert got["measured"]["orphaned_started"] == 19
    assert S.judge_latency(dict(prod, p90=12.4))["status"] == S.BREACH


def test_open_positions_without_a_current_review_breach():
    """The brief's baseline: 60 open groups, 2 with FRESH evidence."""
    positions = ([{"class": "CURRENT"}] * 2 + [{"class": "WAITING"}] * 51
                 + [{"class": "UNREVIEWED"}] * 7)
    got = S.judge_reviews({"open": 60, "positions": positions})
    assert got["status"] == S.BREACH
    assert got["measured"]["without_current_review"] == 58
    assert got["why"] == "58_OPEN_POSITIONS_WITHOUT_A_CURRENT_REVIEW"
    ok = S.judge_reviews({"open": 1, "positions": [{"class": "CURRENT"}]})
    assert ok["status"] == S.OK
    none = S.judge_reviews({"open": 0, "positions": []})
    assert none["status"] == S.OK
    blind = S.judge_reviews({"open": None, "why": "PAPER_POSITION_COUNT_"
                                                  "UNREADABLE"})
    assert blind["status"] == S.UNAVAILABLE
    assert blind["why"] == "PAPER_POSITION_COUNT_UNREADABLE"


def test_agent_task_age_breaches_only_past_expiry():
    ok = S.judge_tasks({"open_requests": 3, "past_expiry": 0,
                        "oldest_opened_at": NOW - 600}, now=NOW)
    assert ok["status"] == S.OK and ok["measured"]["oldest_open_age_s"] == 600
    bad = S.judge_tasks({"open_requests": 3, "past_expiry": 1,
                         "oldest_opened_at": NOW - 4000}, now=NOW)
    assert bad["status"] == S.BREACH


def test_reconciliation_age():
    # production: 8 MATCHED reconciliations, newest 145,102 s old, and no
    # open ACTUAL position (SMALL LIVE is SHADOW)
    got = S.judge_reconciliation([], NOW - 145102, now=NOW)
    assert got["status"] == S.OK
    assert got["measured"]["open_actual_positions"] == 0
    assert got["measured"]["newest_reconciliation_age_s"] == 145102
    never = S.judge_reconciliation([{"group_id": "g1",
                                     "reconciled_at": None}], None, now=NOW)
    assert never["status"] == S.BREACH
    assert never["why"] == "OPEN_ACTUAL_POSITION_NEVER_RECONCILED"
    stale = S.judge_reconciliation([{"group_id": "g1",
                                     "reconciled_at": NOW - 600}],
                                   NOW - 600, now=NOW)
    assert stale["status"] == S.BREACH
    fresh = S.judge_reconciliation([{"group_id": "g1",
                                     "reconciled_at": NOW - 90}],
                                   NOW - 90, now=NOW)
    assert fresh["status"] == S.OK


def test_opportunity_score_age_on_the_production_sequence():
    failed = {"status": "FAILED", "started_at": NOW - 9000,
              "error": "QueryCanceledError: canceling statement due to "
                       "statement timeout"}
    got = S.judge_scores(failed, None, None, now=NOW)
    assert got["status"] == S.BREACH and got["why"] == "LATEST_SCORES_RUN_FAILED"
    ok = {"status": "OK", "started_at": NOW - 2200, "error": None}
    got = S.judge_scores(ok, NOW - 2190, NOW - 2210, now=NOW)
    assert got["status"] == S.OK
    old = S.judge_scores(ok, NOW - 4 * 3600, NOW - 4 * 3600, now=NOW)
    assert old["status"] == S.BREACH
    assert S.judge_scores(None, None, None, now=NOW)["status"] == \
        S.UNAVAILABLE


def test_parity_without_a_cutover_is_unavailable_not_zero():
    got = S.judge_parity(None, None)
    assert got["status"] == S.UNAVAILABLE
    assert got["why"] == "NO_PRODUCTION_CUTOVER_RECORDED"
    assert got["measured"] is None
    cut = {"cutover_at": NOW - 3600, "release_sha": "a" * 40}
    assert S.judge_parity(cut, {"rows": 4, "divergences": 0})["status"] == S.OK
    bad = S.judge_parity(cut, {"rows": 4, "divergences": 1})
    assert bad["status"] == S.BREACH


def test_release_state():
    sha = "191b2992406af8350bc9f0276de5b2fa15caf80f"
    rec = {"items": [{"sha": sha, "hash_verified": True, "file": "r.json",
                      "generated_at": "2026-10-04T17:00:00Z", "state": "GATED"},
                     {"sha": "f" * 40, "hash_verified": False,
                      "generated_at": "2026-10-05T00:00:00Z"}]}
    ok = S.judge_release({"sha": sha}, {"sha": sha}, rec)
    assert ok["status"] == S.OK, "a TAMPERED receipt is never the latest"
    mis = S.judge_release({"sha": sha}, {"sha": "c" * 40}, rec)
    assert mis["status"] == S.BREACH and mis["why"] == "API_WORKERS_MISALIGNED"
    old = S.judge_release({"sha": "d" * 40}, {"sha": "d" * 40}, rec)
    assert old["why"] == "RUNNING_SHA_IS_NOT_THE_LATEST_RECEIPT"
    none = S.judge_release({"sha": None}, {"sha": sha}, rec)
    assert none["status"] == S.UNAVAILABLE and none["why"] == \
        "API_SHA_UNAVAILABLE"


# ── §2 never a manufactured value ──────────────────────────────────────

def test_empty_windows_are_unavailable_with_a_reason():
    assert S.judge_feed({"decisions": 0})["why"] == "NO_DECISIONS_IN_WINDOW"
    assert S.judge_feed({"decisions": 5, "with_age": 0})["why"] == \
        "NO_RECORDED_PINNACLE_AGE"
    assert S.judge_latency({"completed": 0})["why"] == \
        "NO_COMPLETED_REACTIVE_EVALUATION_IN_WINDOW"
    for got in (S.judge_feed(None), S.judge_latency(None),
                S.judge_scores(None, None, None, now=NOW),
                S.judge_parity(None, None),
                S.judge_release({}, {}, {})):
        assert got["status"] == S.UNAVAILABLE and got["why"]
        assert got["target"] and got["window"]


# ── §4 targets are the system's own bounds ─────────────────────────────

def test_targets_are_pinned_to_their_sources():
    import inspect

    from sportsassets import execmirror as EXM
    from sportsassets import pinnapi_reactive as R
    from sportsassets.profitability import runner as POS
    deadline = inspect.signature(R.Scheduler.__init__).parameters[
        "deadline"].default
    assert S.DECISION_LATENCY_TARGET_S == deadline
    assert S.RECONCILE_EVERY_S == EXM.MANAGEMENT_EVERY_S
    assert S.SCORES_CYCLE_S == POS.CYCLE_S
    assert S.FEED_FRESHNESS_TARGET_S == 30.0


# ── §3 the read on Postgres ────────────────────────────────────────────

async def _seed(c, now):
    """Production-shaped rows, timestamped around `now` (a future instant,
    so the windows hold only these rows)."""
    acct = await c.fetchval("SELECT account_id FROM paper_accounts LIMIT 1")
    sess = None
    if acct is None:
        acct = "paper_acct_slo_test"
        await c.execute(
            "INSERT INTO paper_accounts (account_id, account_key, "
            "starting_cash_usd) VALUES ($1, $1, 500000)", acct)
    sess = "paper_session_slo_%s" % uuid.uuid4().hex[:8]
    await c.execute(
        "INSERT INTO paper_sessions (session_id, account_id, started_at, "
        "config, config_sha, simulator_version) VALUES ($1, $2, "
        "to_timestamp($3), '{}', 'x', 'test')", sess, acct, now - 7200)
    for i, age in enumerate((4.0, 8.6, 12.0, 25.3, 28.0)):
        await c.execute(
            "INSERT INTO paper_decisions (decision_id, session_id, "
            "account_id, decided_at, verdict, refusal, internal_model, "
            "pinnacle, qualification_gaps, policy_version, "
            "simulator_version) VALUES ($1, $2, $3, to_timestamp($4), "
            "'REFUSE', 'BELOW_MIN_GROSS_EDGE', '{}', $5::jsonb, '[]', "
            "'p', 'test')", "paper_dec_slo_%d_%s" % (i, sess), sess, acct,
            now - 600 - i, json.dumps({"age_s": age, "limit_s": 30.0}))
    # a decision with no Pinnacle on it: counted, not aged
    await c.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        "decided_at, verdict, refusal, internal_model, pinnacle, "
        "qualification_gaps, policy_version, simulator_version) VALUES "
        "($1, $2, $3, to_timestamp($4), 'REFUSE', 'NO_PINNACLE', '{}', "
        "'{}', '[]', 'p', 'test')", "paper_dec_slo_np_" + sess, sess, acct,
        now - 900)
    for i, (rec, fin) in enumerate(((now - 300, now - 294.2),
                                    (now - 200, now - 189.0),
                                    (now - 100, now - 96.0))):
        detail = {"attempt_id": "a%d" % i, "received_at": rec,
                  "queued_at": rec + 0.1, "evaluation_started_at": rec + 0.2,
                  "finished_at": fin, "held": True, "state": "COMPLETED"}
        await c.execute(
            "INSERT INTO pinnapi_reactive_attempts (attempt_id, event_id, "
            "state, detail, created_at, updated_at) VALUES ($1, '123', "
            "'COMPLETED', $2::jsonb, to_timestamp($3), to_timestamp($4))",
            "slo_%s_%d" % (sess, i), json.dumps(detail), rec, fin)
    await c.execute(
        "INSERT INTO pinnapi_reactive_attempts (attempt_id, event_id, state, "
        "detail, created_at, updated_at) VALUES ($1, '124', 'STARTED', "
        "'{\"held\": true}', to_timestamp($2), to_timestamp($2))",
        "slo_%s_orphan" % sess, now - 3600)
    for comp, status, at in (("SCORES", "FAILED", now - 9000),
                             ("SCORES", "OK", now - 2200)):
        await c.execute(
            "INSERT INTO lol_runs (run_id, component, started_at, "
            "finished_at, status, version, duration_ms) VALUES ($1, $2, "
            "to_timestamp($3), to_timestamp($4), $5, 'LOL_RUNNER_V1', 1)",
            "lolrun:slo_%s_%d" % (sess, int(at)), comp, at, at + 11, status)
    await c.execute(
        "INSERT INTO smalllive_handoffs (handoff_id, venue, group_id, "
        "us_market_slug, entry_mirror_id, live_held, live_bought, "
        "first_live_fill_at) VALUES ($1, 'POLYMARKET', $2, 'mkt-slo', 'm1', "
        "1, 1, to_timestamp($3))", "ho_" + sess, "grp_" + sess, now - 4000)
    return sess


@pg
def test_every_slo_answers_from_production_shaped_rows_read_only():
    import asyncpg

    async def main():
        c = await asyncpg.connect(DSN)
        now = time.time() + 5 * 365 * 86400.0
        sha = "191b2992406af8350bc9f0276de5b2fa15caf80f"
        tx = c.transaction()
        await tx.start()
        try:
            sess = await _seed(c, now)
            await c.execute(
                "INSERT INTO ingestion_state (key, value) VALUES "
                "('workers_boot', $1::jsonb) ON CONFLICT (key) DO UPDATE "
                "SET value = EXCLUDED.value",
                json.dumps({"commit_sha": sha, "at": "2026-10-04T17:19:38"}))
            async with c.transaction():
                await c.execute("SET LOCAL statement_timeout = %d"
                                % S.STATEMENT_TIMEOUT_MS)
                body = await S.read_slos(
                    c, now=now, api={"sha": sha},
                    receipts={"items": [{"sha": sha, "hash_verified": True,
                                         "generated_at": "2026-10-04",
                                         "file": "x.json"}]})
            return sess, body
        finally:
            await tx.rollback()
            await c.close()

    sess, body = asyncio.run(main())
    by = {s["slo"]: s for s in body["slos"]}
    assert len(by) == 8
    feed = by["FEED_FRESHNESS"]
    assert feed["status"] == S.OK, feed
    assert feed["measured"]["decisions"] == 6
    assert feed["measured"]["with_recorded_age"] == 5
    assert abs(feed["measured"]["p50_age_s"] - 12.0) < 1e-9
    lat = by["DECISION_LATENCY"]
    assert lat["status"] == S.OK, lat
    assert lat["measured"]["completed"] == 3
    assert abs(lat["measured"]["max_s"] - 11.0) < 1e-9
    assert lat["measured"]["orphaned_started"] == 1
    scores = by["OPPORTUNITY_SCORE_AGE"]
    assert scores["status"] == S.OK, scores
    recon = by["RECONCILIATION_AGE"]
    assert recon["status"] == S.BREACH
    assert recon["why"] == "OPEN_ACTUAL_POSITION_NEVER_RECONCILED"
    assert "grp_" + sess in recon["measured"]["never_reconciled"]
    assert by["PARITY_DIVERGENCE"]["status"] == S.UNAVAILABLE
    assert by["PARITY_DIVERGENCE"]["why"] == "NO_PRODUCTION_CUTOVER_RECORDED"
    assert by["RELEASE_STATE"]["status"] == S.OK, by["RELEASE_STATE"]
    assert by["AGENT_TASK_AGE"]["status"] in (S.OK, S.BREACH)
    rv = by["OPEN_POSITIONS_WITHOUT_FRESH_REVIEW"]
    assert rv["status"] in (S.OK, S.BREACH, S.UNAVAILABLE)
    if rv["status"] != S.UNAVAILABLE:
        m = rv["measured"]
        assert m["open_positions"] == m["with_current_review"] + \
            m["without_current_review"]
    assert set(body["breaches"]) == {s["slo"] for s in body["slos"]
                                     if s["status"] == S.BREACH}


@pg
def test_an_overdue_work_request_and_a_divergence_since_cutover_breach():
    import asyncpg

    async def main():
        c = await asyncpg.connect(DSN)
        now = time.time() + 5 * 365 * 86400.0
        tx = c.transaction()
        await tx.start()
        try:
            rid = "awr_slo_" + uuid.uuid4().hex[:10]
            await c.execute(
                "INSERT INTO agent_work_requests (request_id, agent_id, kind, "
                "position_kind, group_id, us_market_slug, reason, batch_id, "
                "enqueued_at, expires_at) VALUES ($1, 'XAVIER', "
                "'PROBABILITY', 'PAPER', 'g-slo', 'mkt-slo', "
                "'WAITING_FOR_FRESH_EVIDENCE', 'b1', to_timestamp($2), "
                "to_timestamp($3))", rid, now - 7200, now - 3700)
            await c.execute(
                "INSERT INTO agent_work_open (agent_id, position_kind, "
                "group_id, kind, request_id, opened_at) VALUES ('XAVIER', "
                "'PAPER', 'g-slo', 'PROBABILITY', $1, to_timestamp($2))",
                rid, now - 7200)
            sha = "a" * 40
            hid = await c.fetchval(
                "INSERT INTO live_parity_hook_installs (process, commit_sha, "
                "hooks) VALUES ('api', $1, ARRAY['DECISION']) "
                "RETURNING install_id", sha)
            await c.execute(
                "INSERT INTO live_parity_cutover (id, cutover_at, "
                "release_sha, api_sha, workers_sha, migrations, "
                "hook_install_id, small_live_mode, small_live_halted, "
                "capital_activated, evidence, recorded_by) VALUES (1, "
                "to_timestamp($2), $1, $1, $1, ARRAY['225','226'], $3, "
                "'SHADOW', false, false, '{}', 'test')", sha, now - 86400,
                hid)
            iid = "cdi_" + uuid.uuid4().hex[:24]
            for eid, adapter, mode, scale, state in (
                    ("e_p_" + iid, "PAPER", "SIMULATED", 1, "PAPER_SUBMITTED"),
                    ("e_l_" + iid, "SMALL_LIVE", "SHADOW", 1000,
                     "SHADOW_PROPOSED")):
                await c.execute(
                    "INSERT INTO canonical_intent_executions (execution_id, "
                    "intent_kind, intent_id, intent_sha, adapter, mode, "
                    "adapter_version, state, requested, capital_scale) "
                    "VALUES ($1, 'DECISION', $2, $3, $4, $5, 'v1', $6, '{}', "
                    "$7)", eid, iid, "b" * 64, adapter, mode, state, scale)
            await c.execute(
                "INSERT INTO live_parity_ledger (parity_id, parity_version, "
                "intent_kind, intent_id, intent_sha, sleeve, "
                "paper_execution_id, live_execution_id, capital_scale, "
                "parity_state, divergence_fields, comparison, created_at) "
                "VALUES ($1, 'v1', 'DECISION', $2, $3, 'INVESTMENT', $4, $5, "
                "1000, 'LOGIC_DIVERGENCE', ARRAY['holding_side'], '{}', "
                "to_timestamp($6))", "lp_" + iid, iid, "b" * 64,
                "e_p_" + iid, "e_l_" + iid, now - 600)
            async with c.transaction():
                body = await S.read_slos(c, now=now, api={"sha": None},
                                         receipts={"items": []})
        finally:
            await tx.rollback()
            await c.close()
        return body

    body = asyncio.run(main())
    by = {s["slo"]: s for s in body["slos"]}
    tasks = by["AGENT_TASK_AGE"]
    assert tasks["status"] == S.BREACH, tasks
    assert tasks["measured"]["past_expiry"] >= 1
    assert tasks["measured"]["by_agent"].get("XAVIER", 0) >= 1
    par = by["PARITY_DIVERGENCE"]
    assert par["status"] == S.BREACH, par
    assert par["measured"]["logic_divergences"] == 1
    rel = by["RELEASE_STATE"]
    assert rel["status"] == S.UNAVAILABLE and rel["why"] == \
        "API_SHA_UNAVAILABLE"


@pg
def test_a_missing_table_is_unavailable_and_the_rest_still_answer():
    import asyncpg

    async def main():
        c = await asyncpg.connect(DSN)
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute("DROP TABLE lol_opportunity_scores CASCADE")
            async with c.transaction():
                body = await S.read_slos(c, now=time.time(),
                                         api={"sha": None},
                                         receipts={"items": []})
        finally:
            await tx.rollback()
            await c.close()
        return body

    body = asyncio.run(main())
    by = {s["slo"]: s for s in body["slos"]}
    sc = by["OPPORTUNITY_SCORE_AGE"]
    assert sc["status"] == S.UNAVAILABLE
    assert sc["why"].startswith("TABLE_NOT_DEPLOYED")
    assert sc["target"] and sc["window"]
    assert len(by) == 8


@pg
def test_the_slo_read_is_refused_a_write():
    import asyncpg

    async def main():
        c = await asyncpg.connect(DSN)
        try:
            async with c.transaction(readonly=True):
                await c.execute("SET LOCAL statement_timeout = %d"
                                % S.STATEMENT_TIMEOUT_MS)
                body = await S.read_slos(c, api={"sha": None},
                                         receipts={"items": []})
                assert len(body["slos"]) == 8
                with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                    await c.execute("INSERT INTO ingestion_state (key, value)"
                                    " VALUES ('slo_probe', '{}')")
        finally:
            await c.close()

    asyncio.run(main())
