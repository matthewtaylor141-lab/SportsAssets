"""Proactive management updates and Slack research assignment."""
import json
import uuid

import pytest

from sportsassets import slack_updates as U
from tests import paper_harness as H

SNAP = {
    "at": 1790950000.0,
    "account": {"cash_usd": 495170.11, "reserved_usd": 1250.0,
                "available_usd": 493920.11, "realized_pnl_usd": -12.5,
                "unrealized_pnl_usd": -40.0, "marks_complete": True,
                "open_positions": 17, "last_sequence": 97},
    "pnl": [{"strategy": "PINNACLE_EXPLORATION_PAPER", "kind": "TRAINING",
             "realized_pnl_usd": -12.5, "unrealized_pnl_usd": -40.0,
             "unrealized_marked_only_usd": -40.0, "open_cost_basis_usd": 4998.32,
             "open_positions": 17},
            {"strategy": "PINNACLE_COMPLETED_GAME_PAPER",
             "kind": "EXPERIMENTAL_BENCHMARK", "realized_pnl_usd": 0.0,
             "unrealized_pnl_usd": 0.0, "unrealized_marked_only_usd": 0.0,
             "open_cost_basis_usd": 0.0, "open_positions": 0}],
    "reconcile": {"reconciled": True},
    "research": [{"status": "OPEN", "n": 3}],
    "cycle_at": 1790949900.0, "pass_at": 1790949950.0}


def test_the_account_block_keeps_every_figure_separate_and_labelled():
    t = U.account_block(SNAP)
    assert "Simulated cash $495,170.11" in t and "reserved $1,250.00" in t
    assert "Realized P&L $-12.50" in t and "unrealized P&L $-40.00" in t
    assert "Training: realized $-12.50" in t and "exposure $4,998.32" in t
    assert "Investment / benchmark: realized $0.00" in t
    assert "reconciled" in t and "seq 97" in t and "2026-" in t
    assert "command.bettortoken.com/audrey" in t
    assert "no real money" in t


def test_alerts_name_the_condition_and_stay_quiet_when_healthy():
    assert U.alerts(SNAP, SNAP["at"]) == []
    bad = dict(SNAP, reconcile={"reconciled": False,
                                "failed_checks": [{"check": "cash_chain"}]},
               cycle_at=SNAP["at"] - 3600,
               research=[{"status": "REJECTED", "n": 2}])
    got = U.alerts(bad, SNAP["at"])
    kinds = [k for k, _, _ in got]
    assert kinds == ["reconciliation", "service:cycle", "research"]
    assert "cash_chain" in got[0][2] and "60 min" in got[1][2]
    # an unchanged condition has an unchanged fingerprint (deduplicated)
    assert U.alerts(bad, SNAP["at"] + 60)[0][1] == got[0][1]


@pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
@pytest.mark.asyncio
async def test_updates_are_scheduled_once_and_not_recomputed_between(monkeypatch):
    """Owner cadence (2026-10-02): a consolidated progress update every 30
    minutes 9 a.m.-6 p.m. Eastern, Audrey's hourly reconciliation (merged
    into the on-the-hour update in business hours), the daily report after
    6 p.m. Eastern; nothing recomputed between due times."""
    conn = await H.connect()
    calls = []

    async def snap(c, now):
        calls.append(now)
        return dict(SNAP, at=now, cycle_at=now - 60, pass_at=now - 30)

    async def act(c, since, now):
        return {"decisions": 0, "approved": 0, "refusals": [], "entries": 0,
                "other_orders": 0, "last_entry_at": now, "unanswered": []}

    async def no_mirror(c):
        return None
    monkeypatch.setattr(U, "snapshot", snap)
    monkeypatch.setattr(U, "activity", act)
    monkeypatch.setattr(U, "mirror_snapshot", no_mirror)
    try:
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           U.STATE_KEY)
        t0 = 1790949600.0                       # 14:00 UTC = 10:00 EDT
        first = [k for k, _ in await U.due(conn, t0, revision=1)]
        assert first == ["update:initial:1", "update:progress:2026-10-02T10:00"]
        assert await U.due(conn, t0 + 60, revision=1) == []     # no read
        assert len(calls) == 1
        assert [k for k, _ in await U.due(conn, t0 + 1800, revision=1)] == \
            ["update:progress:2026-10-02T10:30"]
        assert [k for k, _ in await U.due(conn, t0 + 3600, revision=1)] == \
            ["update:progress:2026-10-02T11:00"]                # hourly merged
        late = 1790978400.0                     # 22:00 UTC = 18:00 EDT
        keys = [k for k, _ in await U.due(conn, late + 600, revision=1)]
        assert "update:progress:2026-10-02T18:00" in keys
        assert "update:daily:2026-10-02" in keys
        night = late + 5 * 3600                 # 23:00 EDT: hourly only
        assert [k for k, _ in await U.due(conn, night, revision=1)] == \
            ["update:hourly:2026-10-03T03"]
        assert [k for k, _ in await U.due(conn, t0, revision=2)][0] == \
            "update:initial:2"                  # a re-enable briefs again
    finally:
        await conn.execute("DELETE FROM ingestion_state WHERE key=$1",
                           U.STATE_KEY)
        await conn.close()


def test_every_progress_update_answers_the_four_management_questions():
    s = dict(SNAP, at=1790949600.0)
    act = {"decisions": 12, "approved": 0, "refusals": [("SETTLEMENT_NOT_SUPPORTED", 9)],
           "entries": 0, "other_orders": 1, "last_entry_at": 1790930000.0,
           "unanswered": []}
    text = U.progress_text(s, act, None, {}, label="Progress update",
                           with_reconciliation=False)
    for part in ("What changed:", "Why it matters:", "Next:",
                 "Management action needed:", "SIMULATED (paper account):",
                 "ACTUAL (live account, 1:1,000 mirror)"):
        assert part in text
    assert "SETTLEMENT_NOT_SUPPORTED (9)" in text


def test_progress_slots_are_eastern_business_hours_only():
    assert U.progress_slot(1790949600.0) == "2026-10-02T10:00"     # 10:00 EDT
    assert U.progress_slot(1790949600.0 + 29 * 60) == "2026-10-02T10:00"
    assert U.progress_slot(1790949600.0 + 31 * 60) == "2026-10-02T10:30"
    assert U.progress_slot(1790949600.0 - 2 * 3600) is None        # 08:00 EDT
    assert U.progress_slot(1790978400.0 + 10 * 60) == "2026-10-02T18:00"
    assert U.progress_slot(1790978400.0 + 40 * 60) is None         # 18:40 EDT


def test_escalations_address_only_the_verified_managers(monkeypatch):
    monkeypatch.setenv("SLACK_MANAGEMENT_USER_IDS", "U0C64BKD2JE,U0C6ADEKMTQ")
    now = 1790949600.0
    act = {"decisions": 5, "approved": 0, "refusals": [("SETTLEMENT_NOT_SUPPORTED", 5)],
           "entries": 0, "other_orders": 0, "last_entry_at": now - 4 * 3600,
           "unanswered": []}
    m = {"enabled": True, "account": {"balances": [{"buyingPower": 40.0}]}}
    out = U.decision_escalations(dict(SNAP, at=now), act, m, now)
    kinds = [k for k, _, _ in out]
    assert kinds == ["no-entries", "mirror-funding"]
    for _, _, text in out:
        assert text.startswith("{{@U0C64BKD2JE}} {{@U0C6ADEKMTQ}} Decision needed")


@pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
@pytest.mark.asyncio
async def test_a_slack_assignment_creates_one_durable_three_agent_flow():
    import asyncpg
    from sportsassets import slack_bridge as S
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=2)
    did = "slack-test-" + uuid.uuid4().hex[:8]
    job = {"delivery_id": did, "agent": "xavier", "requested_by": "U123",
           "question": "<@U999> assign: compare exit alternatives for the "
                       "open exploration positions"}
    try:
        ans = await S.assignment(pool, job)
        assert ans and "Research assigned" in ans and "nothing is approved" in ans
        again = await S.assignment(pool, job)          # Slack retry: same ids
        assert again == ans
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT task_id, assignee, created_by, spec FROM agent_tasks "
                " WHERE spec->>'source_key' = $1 "
                " ORDER BY jsonb_array_length(spec->'dependencies')",
                "slack:" + did)
        assert [r["assignee"] for r in rows] == ["XAVIER", "DEREK", "AUDREY"]
        assert {r["created_by"] for r in rows} == {"slack:U123"}
        spec = json.loads(rows[0]["spec"]) if isinstance(rows[0]["spec"], str) \
            else rows[0]["spec"]
        assert spec["authority"] == "RESEARCH_ONLY"
        assert await S.assignment(pool, dict(job, question="what is my cash?")) \
            is None
    finally:
        # task records are append-only audit history; the unique source key
        # keeps this test isolated without deleting them
        await pool.close()
