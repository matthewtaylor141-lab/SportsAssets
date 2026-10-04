"""MEMORY USEFULNESS (owner R30 program section 19, migration 234 §2), on
Postgres and pure.

  §1 MIGRATION: retrievals are point in time (a lesson learned after the
     decision is refused), name an existing lesson of the agent, never a
     superseded one, carry no influence; supersessions only ever LOWER a
     weight, name the evaluator version (never an agent or a person), carry
     no authority; nothing follows SUPERSEDE; both are append-only.
  §2 RETRIEVAL through the real writer: Derek's ENTER decisions get the
     lessons in force AT THE DECISION (the series version of that instant,
     strategy-matched first, general ones after); a decision before a lesson
     never gets it; Xavier's first review of a position gets his memory
     LESSON (written by agent_memory.promote); a superseded lesson is
     skipped.
  §3 USEFULNESS AND SUPERSESSION on forward INVESTMENT-sleeve outcomes read
     by the profitability validation's own reader (paper ledger fills +
     settlements): a lesson whose decisions lost while comparable decisions
     won is DOWNWEIGHTED on a before / after comparison (never superseded on
     one), SUPERSEDED on a contemporaneous one; TRAINING-sleeve outcomes
     never count; small samples are reported as SMALL_SAMPLE; the same
     evidence never lowers a weight twice; a superseded lesson is never
     retrieved again.
  §4 THE STATISTICS (pure).
"""
from __future__ import annotations

import json
import uuid

import asyncpg
import pytest

from sportsassets.agents import agent_memory as M
from sportsassets.agents import lesson_usage as LU
from sportsassets.agents import registry as R

from tests import agent_ops_fixture as F

pg = F.pg
NOW = F.NOW
H = 3600.0
INV = "DEREK_ENTRY_POLICY_V2"          # an INVESTMENT-sleeve strategy


async def _lesson(conn, acct, *, agent="DEREK", strategy=INV, series=None,
                  version=1, at, kind="REFUSAL_FUNNEL") -> str:
    """A paper_agent_lessons row in the learning record's shape (185)."""
    lid = "paperlesson:%s" % uuid.uuid4().hex[:20]
    await conn.execute(
        "INSERT INTO paper_agent_lessons (lesson_id, account_id, agent_id, "
        " kind, strategy, series_key, version, learned_at, window_end, "
        " statement, metrics, provenance, evidence_category, basis, digest)"
        " VALUES ($1,$2,$3,$4,$5,$6,$7,to_timestamp($8),to_timestamp($8),"
        " 'TEST_FIXTURE_LESSON','{}'::jsonb,$9::jsonb,'FORWARD_RECORDS',"
        " 'FORWARD_RECORDS_ONLY',$10)",
        lid, acct["account_id"], agent, kind, strategy,
        series or "series-%s" % uuid.uuid4().hex[:8], version, float(at),
        json.dumps({"record_count": 3, "ids_sha256": "a" * 64}),
        "d" * 64)
    return lid


async def _retrievals(conn, decision_id) -> list:
    return [dict(r) for r in await conn.fetch(
        "SELECT lesson_id, rank, weight, influence, relevance "
        "  FROM agent_lesson_retrievals WHERE decision_id = $1 "
        " ORDER BY rank", decision_id)]


async def _entry(conn, acct, *, at, strategy=INV, won=None, qty=100,
                 settle_after=3 * H):
    """A Derek ENTER decision and its filled position (and settlement)."""
    did = await F.decision(conn, acct, at=at, strategy=strategy, qty=qty)
    pos = await F.position(
        conn, acct, at=at + 5, strategy=strategy, qty=qty, decision_id=did,
        outcome=None if won is None else ("WON" if won else "LOST"),
        payout=None if won is None else (1.0 if won else 0.0),
        settle_at=at + settle_after)
    return did, pos


# ═════════════════════════════════════════════════════════════════════
# §1 THE MIGRATION
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_retrievals_and_supersessions_are_guarded():
    conn, tx = await F.tx()
    try:
        a = await F.account(conn, "lu1")
        lid = await _lesson(conn, a, at=NOW - H)
        did = await F.decision(conn, a, at=NOW, strategy=INV)
        ins = ("INSERT INTO agent_lesson_retrievals (retrieval_id, agent_id,"
               " lesson_table, lesson_id, lesson_learned_at, decision_table,"
               " decision_id, decided_at, retrieved_at, rank, weight, "
               " retriever_version, influence) VALUES ($1,$2,"
               " 'paper_agent_lessons',$3,to_timestamp($4),"
               " 'paper_decisions',$5,to_timestamp($6),now(),1,1,"
               " 'LESSON_RETRIEVAL_V1',$7)")
        X = asyncpg.IntegrityConstraintViolationError
        C = asyncpg.CheckViolationError
        await F.expect(conn, C, ins, "alr:" + "1" * 24, "DEREK", lid,
                       NOW + 10, did, NOW, "NONE_RECORD_ONLY")  # future
        await F.expect(conn, C, ins, "alr:" + "1" * 24, "DEREK", lid,
                       NOW - H, did, NOW, "ADVISORY")      # influence
        await F.expect(conn, X, ins, "alr:" + "1" * 24, "XAVIER", lid,
                       NOW - H, did, NOW, "NONE_RECORD_ONLY")  # not his
        await conn.execute(ins, "alr:" + "1" * 24, "DEREK", lid, NOW - H,
                           did, NOW, "NONE_RECORD_ONLY")
        await F.expect(conn, X, "UPDATE agent_lesson_retrievals SET rank=2")
        sup = ("INSERT INTO agent_lesson_supersessions (supersession_id, "
               " agent_id, lesson_table, lesson_id, action, previous_weight,"
               " weight, basis, evidence, evidence_refs, decided_by, "
               " decided_at, grants_authority) VALUES ($1,'DEREK',"
               " 'paper_agent_lessons',$2,$3,$4,$5,"
               " 'FORWARD_INVESTMENT_OUTCOMES',$6::jsonb,$7::jsonb,$8,"
               " to_timestamp($9),$10)")
        ev = json.dumps({"n_used": 2, "n_comparable": 2,
                         "mean_difference_usd": -1.0})
        refs = json.dumps([{"kind": "paper_agent_lessons", "id": lid}])
        await F.expect(conn, C, sup, "als:" + "1" * 24, lid, "UPWEIGHT", 1.0,
                       0.9, ev, refs, "MEMORY_USEFULNESS_V1", NOW, False)
        await F.expect(conn, C, sup, "als:" + "1" * 24, lid, "DOWNWEIGHT",
                       1.0, 1.0, ev, refs, "MEMORY_USEFULNESS_V1", NOW,
                       False)                              # did not fall
        await F.expect(conn, C, sup, "als:" + "1" * 24, lid, "DOWNWEIGHT",
                       1.0, 0.5, ev, refs, "DEREK", NOW, False)  # an agent
        await F.expect(conn, C, sup, "als:" + "1" * 24, lid, "DOWNWEIGHT",
                       1.0, 0.5, ev, refs, "MEMORY_USEFULNESS_V1", NOW,
                       True)                               # authority
        await F.expect(conn, X, sup, "als:" + "1" * 24, lid, "DOWNWEIGHT",
                       0.8, 0.5, ev, refs, "MEMORY_USEFULNESS_V1", NOW,
                       False)                    # previous is not current
        await conn.execute(sup, "als:" + "1" * 24, lid, "DOWNWEIGHT", 1.0,
                           0.5, ev, refs, "MEMORY_USEFULNESS_V1", NOW, False)
        await conn.execute(sup, "als:" + "2" * 24, lid, "SUPERSEDE", 0.5,
                           0.0, ev, refs, "MEMORY_USEFULNESS_V1", NOW + 1,
                           False)
        await F.expect(conn, X, sup, "als:" + "3" * 24, lid, "DOWNWEIGHT",
                       0.5, 0.25, ev, refs, "MEMORY_USEFULNESS_V1", NOW + 2,
                       False)                    # nothing follows SUPERSEDE
        # a superseded lesson is never retrieved for a later decision
        d2 = await F.decision(conn, a, at=NOW + 10, strategy=INV)
        await F.expect(conn, X, ins, "alr:" + "2" * 24, "DEREK", lid,
                       NOW - H, d2, NOW + 10, "NONE_RECORD_ONLY")
        await F.expect(conn, X, "DELETE FROM agent_lesson_supersessions")
    finally:
        await F.done(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# §2 RETRIEVAL
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_retrieval_is_point_in_time_and_strategy_first():
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        a = await F.account(conn, "lu2")
        acct = a["account_id"]
        general = await _lesson(conn, a, strategy=None, at=NOW - 10 * H)
        v1 = await _lesson(conn, a, series="s-inv", version=1, at=NOW - 2 * H)
        other = await _lesson(conn, a, strategy="PINNACLE_EXPLORATION_PAPER",
                              at=NOW - 2 * H)
        d_before = await F.decision(conn, a, at=NOW - 3 * H, strategy=INV)
        d_after = await F.decision(conn, a, at=NOW - H, strategy=INV)
        v2 = await _lesson(conn, a, series="s-inv", version=2,
                           at=NOW - 30 * 60)
        d_late = await F.decision(conn, a, at=NOW - 10 * 60, strategy=INV)
        got = await LU.retrieve(conn, account_id=acct, now=NOW)
        assert got["decisions"] >= 3 and not got["errors"], got
        assert [r["lesson_id"] for r in await _retrievals(conn, d_before)] \
            == [general]
        ra = await _retrievals(conn, d_after)
        assert [r["lesson_id"] for r in ra] == [v1, general]
        assert ra[0]["influence"] == "NONE_RECORD_ONLY"
        assert F.j(ra[0]["relevance"])["strategy_match"] is True
        assert [r["lesson_id"] for r in await _retrievals(conn, d_late)] == \
            [v2, general]
        assert other not in {r["lesson_id"] for d in (d_before, d_after,
                                                      d_late)
                             for r in await _retrievals(conn, d)}
        # idempotent: a second pass retrieves nothing new
        again = await LU.retrieve(conn, account_id=acct, now=NOW + 1)
        assert again["retrievals"] == 0
        # Xavier's first review of a position gets his memory LESSON
        p = await F.position(conn, a, at=NOW - 5 * H)
        rv0 = await F.stale_hold_review(conn, a, group_id=p["group_id"],
                                        at=NOW - 4 * H)
        mem = await M.promote(conn, {
            "agent_id": "XAVIER", "memory_kind": "LESSON",
            "subject_type": "paper_position", "subject_id": p["group_id"],
            "summary": "Stale evidence is WAITING, never HOLD.",
            "evidence_refs": [{"kind": "paper_xavier_reviews", "id": rv0}],
            "confidence": 0.9, "deriver": "test_fixture"}, now=NOW - 3 * H)
        assert mem["ok"] and mem["created"], mem
        p2 = await F.position(conn, a, at=NOW - 2 * H)
        rv = await F.stale_hold_review(conn, a, group_id=p2["group_id"],
                                       at=NOW - H)
        await F.stale_hold_review(conn, a, group_id=p2["group_id"],
                                  at=NOW - H + 60)          # not the first
        await LU.retrieve(conn, account_id=acct, now=NOW + 2)
        rx = await _retrievals(conn, rv)
        assert [r["lesson_id"] for r in rx] == [mem["memory_id"]]
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_lesson_retrievals WHERE "
            " decision_table='paper_xavier_reviews' AND decision_id IN ("
            " SELECT review_id FROM paper_xavier_reviews WHERE group_id=$1)",
            p2["group_id"]) == 1
        # (the review before the memory was learned got none)
        assert await _retrievals(conn, rv0) == []
    finally:
        await F.done(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# §3 USEFULNESS AND SUPERSESSION
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_harmful_before_after_evidence_downweights_once():
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        a = await F.account(conn, "lu3")
        acct = a["account_id"]
        # BEFORE the lesson: four INVESTMENT entries that won
        for i in range(4):
            await _entry(conn, a, at=NOW - 20 * H + i * 600, won=True,
                         qty=99 + i)
        lid = await _lesson(conn, a, at=NOW - 10 * H)
        # AFTER it, with it in context: four that lost
        used = []
        for i in range(4):
            did, _ = await _entry(conn, a, at=NOW - 5 * H + i * 600,
                                  won=False, qty=99 + i)
            used.append(did)
        # a TRAINING-sleeve loss in the same span never counts
        await _entry(conn, a, at=NOW - 5 * H + 300, won=False,
                     strategy="PINNACLE_EXPLORATION_PAPER")
        await LU.retrieve(conn, account_id=acct, now=NOW)
        rep = await LU.usefulness(conn, account_id=acct, now=NOW)
        lm = [x for x in rep["lessons"] if x["lesson_id"] == lid][0]
        m = lm["measure"]
        assert m["design"] == LU.D_BEFORE_AFTER
        assert m["n_used"] == 4 and m["n_comparable"] == 4, m
        assert m["status"] == LU.SMALL                  # reported as such
        assert m["mean_difference_usd"] < -90
        assert m["harmful_95"] and m["harmful_99"]
        assert lm["influence"] == "NONE_RECORD_ONLY"
        assert sorted(d["id"] for d in lm["used_decisions"]) == sorted(used)
        got = await LU.supersede(conn, account_id=acct, now=NOW)
        assert got["downweighted"] == [lid] and got["superseded"] == [], got
        row = await conn.fetchrow(
            "SELECT action, previous_weight, weight, decided_by, basis, "
            "       evidence, grants_authority FROM "
            " agent_lesson_supersessions WHERE lesson_id=$1", lid)
        assert row["action"] == "DOWNWEIGHT" and float(row["weight"]) == 0.5
        assert row["decided_by"] == LU.VERSION
        assert row["basis"] == "FORWARD_INVESTMENT_OUTCOMES"
        assert row["grants_authority"] is False
        assert F.j(row["evidence"])["design"] == LU.D_BEFORE_AFTER
        # the same evidence never lowers it twice
        again = await LU.supersede(conn, account_id=acct, now=NOW + 3600)
        assert again["downweighted"] == []
        assert await LU.weight_at(conn, "paper_agent_lessons", lid,
                                  NOW + 7200) == 0.5
    finally:
        await F.done(conn, tx)


@pg
async def test_harmful_contemporaneous_evidence_supersedes():
    """Inside the lesson's span, three newer strategy lessons outrank it
    for a while (they are later superseded as setup), so comparable
    decisions WITHOUT it exist in the same span: the contemporaneous
    comparison may SUPERSEDE, and the lesson is never retrieved again."""
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        a = await F.account(conn, "lu4")
        acct = a["account_id"]
        lid = await _lesson(conn, a, at=NOW - 30 * H)
        used, comps = [], []
        for i in range(4):                      # with it: lost
            did, _ = await _entry(conn, a, at=NOW - 24 * H + i * 600,
                                  won=False, qty=99 + i)
            used.append(did)
        await LU.retrieve(conn, account_id=acct, now=NOW - 23 * H)
        newer = [await _lesson(conn, a, at=NOW - 22 * H) for _ in range(3)]
        for i in range(4):                      # outranked: without it, won
            did, _ = await _entry(conn, a, at=NOW - 21 * H + i * 600,
                                  won=True, qty=99 + i)
            comps.append(did)
        await LU.retrieve(conn, account_id=acct, now=NOW - 20 * H)
        for d in comps:
            assert lid not in [r["lesson_id"]
                               for r in await _retrievals(conn, d)]
        # setup: the three newer lessons are superseded, so it ranks again
        for k, n in enumerate(newer):
            await conn.execute(
                "INSERT INTO agent_lesson_supersessions (supersession_id, "
                " agent_id, lesson_table, lesson_id, action, "
                " previous_weight, weight, basis, evidence, evidence_refs, "
                " decided_by, decided_at) VALUES ($1,'DEREK',"
                " 'paper_agent_lessons',$2,'SUPERSEDE',1,0,"
                " 'FORWARD_INVESTMENT_OUTCOMES',$3::jsonb,$4::jsonb,"
                " 'MEMORY_USEFULNESS_V1',to_timestamp($5))",
                "als:%024d" % (k + 1), n,
                json.dumps({"n_used": 0, "n_comparable": 0,
                            "mean_difference_usd": None}),
                json.dumps([{"kind": "paper_agent_lessons", "id": n}]),
                NOW - 19 * H)
        did, _ = await _entry(conn, a, at=NOW - 18 * H, won=False, qty=98)
        used.append(did)
        await LU.retrieve(conn, account_id=acct, now=NOW - 17 * H)
        assert lid in [r["lesson_id"] for r in await _retrievals(conn, did)]
        rep = await LU.usefulness(conn, account_id=acct, now=NOW)
        m = [x for x in rep["lessons"] if x["lesson_id"] == lid][0][
            "measure"]
        assert m["design"] == LU.D_CONTEMPORANEOUS, m
        assert m["n_used"] == 5 and m["n_comparable"] == 4
        assert m["harmful_99"] is True
        got = await LU.supersede(conn, account_id=acct, now=NOW)
        assert got["superseded"] == [lid], got
        assert await LU.weight_at(conn, "paper_agent_lessons", lid,
                                  NOW + 1) == 0.0
        # never retrieved again
        late, _ = await _entry(conn, a, at=NOW + 60, won=None)
        await LU.retrieve(conn, account_id=acct, now=NOW + 120)
        assert lid not in [r["lesson_id"]
                           for r in await _retrievals(conn, late)]
    finally:
        await F.done(conn, tx)


@pg
async def test_without_comparable_evidence_nothing_moves():
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        a = await F.account(conn, "lu5")
        acct = a["account_id"]
        lid = await _lesson(conn, a, at=NOW - 10 * H)
        await _entry(conn, a, at=NOW - 5 * H, won=False)
        await _entry(conn, a, at=NOW - 4 * H, won=None)        # unresolved
        await LU.retrieve(conn, account_id=acct, now=NOW)
        rep = await LU.usefulness(conn, account_id=acct, now=NOW)
        m = [x for x in rep["lessons"] if x["lesson_id"] == lid][0][
            "measure"]
        assert m["status"] == LU.UNAVAILABLE
        assert m["mean_difference_usd"] is None and m["why"]
        got = await LU.supersede(conn, account_id=acct, now=NOW)
        assert got["downweighted"] == got["superseded"] == []
    finally:
        await F.done(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# §4 THE STATISTICS (pure)
# ═════════════════════════════════════════════════════════════════════

def _rows(vals, tag):
    return [{"event": "%s-%d" % (tag, i), "outcome": v}
            for i, v in enumerate(vals)]


def test_the_comparison_and_the_action():
    u = LU.compare(_rows([-1.0], "u"), _rows([1.0, 2.0], "c"))
    assert u["status"] == LU.UNAVAILABLE and LU.action_for(u, 1.0) is None
    # one event with three positions is one observation
    same = [{"event": "e1", "outcome": -1.0}] * 3
    assert LU.compare(same, _rows([1.0, 2.0], "c"))["n_used"] == 1
    m = LU.compare(_rows([-40, -41, -39, -40.5], "u"),
                   _rows([60, 59, 61, 60.5], "c"))
    assert m["status"] == LU.SMALL and m["harmful_99"]
    assert LU.action_for(dict(m, design=LU.D_CONTEMPORANEOUS), 1.0) == {
        "action": "SUPERSEDE", "weight": 0.0}
    assert LU.action_for(dict(m, design=LU.D_BEFORE_AFTER), 1.0) == {
        "action": "DOWNWEIGHT", "weight": 0.5}
    noisy = LU.compare(_rows([-40, 50, -45, 55], "u"),
                       _rows([10, -5, 12, -8], "c"))
    assert not noisy["harmful_95"] and LU.action_for(noisy, 1.0) is None
    helpful = LU.compare(_rows([60, 59, 61], "u"), _rows([-40, -41, -39],
                                                         "c"))
    assert LU.action_for(helpful, 1.0) is None      # never upweights
    big = LU.compare(_rows([float(-i % 7) for i in range(40)], "u"),
                     _rows([float(i % 5) for i in range(40)], "c"))
    assert big["status"] == LU.MEASURED
    assert LU.action_for(m, 0.0) is None            # superseded: nothing
    assert LU.t_crit(30, 0.95) == 1.697 and LU.t_crit(1, 0.99) == 31.821
    assert abs(LU.t_crit(1000, 0.99) - 2.33) < 0.01
    from sportsassets.profitability import validation as V
    assert LU.T95 == V.T95_ONE_SIDED
    assert [c["lesson_id"] for c in LU.rank([
        {"lesson_id": "a", "strategy_match": False, "weight": 1,
         "learned_at": 9},
        {"lesson_id": "b", "strategy_match": True, "weight": 0.5,
         "learned_at": 1},
        {"lesson_id": "c", "strategy_match": True, "weight": 1,
         "learned_at": 1}])] == ["c", "b", "a"]
