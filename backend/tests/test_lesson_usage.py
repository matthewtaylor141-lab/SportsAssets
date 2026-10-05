"""MEMORY USEFULNESS (owner R30 program section 19, migration 301 §2), on
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
     one), SUPERSEDED on a contemporaneous one -- each on a MEASURED
     comparison (30 independent events per arm), never on a small sample;
     TRAINING-sleeve outcomes never count; the same evidence never lowers a
     weight twice; a superseded lesson is never retrieved again, is
     excluded from the conversation context (learning_context.retrieve) and
     from the agent's own context bundle, and a downweighted one ranks
     lower there.
  §3b EVERY AGENT'S LESSONS: Karen's and Eddie's 224 LESSON memories (the
     only ones production holds) are retrieved for their own decisions of
     record; Eddie's are measured through the outcomes of the decisions he
     estimated; Karen's are UNAVAILABLE with the reason (no position follows
     a challenge); a lesson never in force for a decision is listed, never
     left out.
  §4 THE STATISTICS (pure): SMALL_SAMPLE and zero-variance comparisons never
     act; the permutation test must agree with the t bound.

WHY PINS CHANGED (R30B review): SUPERSEDE / DOWNWEIGHT had fired on
SMALL_SAMPLE evidence (4 losses against 4 wins; two identical losses per
arm). The proofs now build 30+ independent events per arm, and the pure
pins assert that small or zero-variance comparisons never act.
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
        # the evidence is a MEASURED comparison (301: 30 events per arm)
        ev = json.dumps({"n_used": 30, "n_comparable": 30,
                         "mean_difference_usd": -1.0, "status": "MEASURED"})
        small = json.dumps({"n_used": 4, "n_comparable": 4,
                            "mean_difference_usd": -100.0,
                            "status": "SMALL_SAMPLE"})
        refs = json.dumps([{"kind": "paper_agent_lessons", "id": lid}])
        await F.expect(conn, C, sup, "als:" + "1" * 24, lid, "UPWEIGHT", 1.0,
                       0.9, ev, refs, "MEMORY_USEFULNESS_V1", NOW, False)
        # (R30B review) never on a small sample, however harmful it looks
        await F.expect(conn, C, sup, "als:" + "1" * 24, lid, "SUPERSEDE",
                       1.0, 0.0, small, refs, "MEMORY_USEFULNESS_V1", NOW,
                       False)
        await F.expect(conn, C, sup, "als:" + "1" * 24, lid, "SUPERSEDE",
                       1.0, 0.0, json.dumps(dict(json.loads(ev), n_used=29)),
                       refs, "MEMORY_USEFULNESS_V1", NOW, False)
        # a decision source belongs to exactly one agent
        await F.expect(conn, C, ins.replace("'paper_decisions'",
                                            "'karen_challenges'"),
                       "alr:" + "9" * 24, "DEREK", lid, NOW - H, did, NOW,
                       "NONE_RECORD_ONLY")
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

ARM = 30                                # = LU.MIN_EVENTS_FOR_MEASURED


async def _derek_memory_lesson(conn, acct, *, at, summary,
                               expires_at=None):
    """A Derek LESSON memory written by the one memory writer
    (agent_memory.promote), grounded on a real paper decision."""
    ref = await F.decision(conn, acct, at=at - 60, strategy=INV)
    mem = await M.promote(conn, {
        "agent_id": "DEREK", "memory_kind": "LESSON",
        "subject_type": "entry_calibration",
        "subject_id": "paper_enter:%s" % uuid.uuid4().hex[:8],
        "summary": summary, "expires_at": expires_at,
        "evidence_refs": [{"kind": "paper_decisions", "id": ref}],
        "confidence": 0.8, "deriver": "test_fixture"}, now=at)
    assert mem["ok"] and mem["created"], mem
    return mem["memory_id"]


@pg
async def test_harmful_before_after_evidence_downweights_once():
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        a = await F.account(conn, "lu3")
        acct = a["account_id"]
        # BEFORE the lesson: 30 INVESTMENT entries that won
        for i in range(ARM):
            await _entry(conn, a, at=NOW - 20 * H + i * 600, won=True,
                         qty=99 + i)
        # the lesson: one of Derek's 224 LESSON memories
        lid = await _derek_memory_lesson(
            conn, a, at=NOW - 10 * H,
            summary="Enter on thin agreement when the edge is large.")
        # an OLDER memory, at full weight: it had expired before the
        # decisions below, so it was never in force for them
        other = await _derek_memory_lesson(
            conn, a, at=NOW - 12 * H, expires_at=NOW - 6 * H,
            summary="Fees consume small edges.")
        # AFTER it, with it in force: 30 that lost
        used = []
        for i in range(ARM):
            did, _ = await _entry(conn, a, at=NOW - 5 * H + i * 300,
                                  won=False, qty=99 + i)
            used.append(did)
        # a TRAINING-sleeve loss in the same span never counts
        await _entry(conn, a, at=NOW - 5 * H + 100, won=False,
                     strategy="PINNACLE_EXPLORATION_PAPER")
        await LU.retrieve(conn, account_id=acct, now=NOW)
        rep = await LU.usefulness(conn, account_id=acct, now=NOW)
        lm = [x for x in rep["lessons"] if x["lesson_id"] == lid][0]
        m = lm["measure"]
        assert m["design"] == LU.D_BEFORE_AFTER
        assert m["n_used"] == ARM and m["n_comparable"] == ARM, m
        assert m["status"] == LU.MEASURED
        assert m["mean_difference_usd"] < -50
        assert m["harmful_95"] and m["harmful_99"]
        assert m["permutation_p"] < 0.01
        assert lm["influence"] == "NONE_RECORD_ONLY"
        assert sorted(d["id"] for d in lm["used_decisions"]) == \
            sorted(used)[:LU.MAX_EVIDENCE_REFS]
        assert rep["retrieval"].startswith("POINT_IN_TIME_RECONSTRUCTION")
        got = await LU.supersede(conn, account_id=acct, now=NOW)
        assert lid in got["downweighted"] and got["superseded"] == [], got
        row = await conn.fetchrow(
            "SELECT action, previous_weight, weight, decided_by, basis, "
            "       evidence, grants_authority FROM "
            " agent_lesson_supersessions WHERE lesson_id=$1", lid)
        assert row["action"] == "DOWNWEIGHT" and float(row["weight"]) == 0.5
        assert row["decided_by"] == LU.VERSION
        assert row["basis"] == "FORWARD_INVESTMENT_OUTCOMES"
        assert row["grants_authority"] is False
        assert F.j(row["evidence"])["design"] == LU.D_BEFORE_AFTER
        assert F.j(row["evidence"])["status"] == LU.MEASURED
        # the same evidence never lowers it twice
        again = await LU.supersede(conn, account_id=acct, now=NOW + 3600)
        assert lid not in again["downweighted"]
        assert await LU.weight_at(conn, "agent_memory_events", lid,
                                  NOW + 7200) == 0.5
        # WHAT THE AGENT READS (R30B review): in Derek's own context bundle
        # the downweighted lesson ranks after his full-weight memory
        from sportsassets.agents import agent_context as AC
        # (R30 tails integration) the bundle is read at its widest bound:
        # a shared test database keeps DEREK memories other proofs commit at
        # the wall clock, which outrank this proof's NOW-dated rows inside
        # the default 20; the ranking and the exclusion asserted below are
        # exactly the default bundle's (same query, same ORDER BY)
        ctx = await AC.build_context(conn, "DEREK", now=NOW + 7200,
                                     memory_limit=500)
        mine = [x["memory_id"] for x in ctx["own_memories"]
                if x["memory_id"] in (lid, other)]
        assert mine == [other, lid], mine
        w = {x["memory_id"]: x["lesson_weight"] for x in ctx["own_memories"]}
        assert w[lid] == 0.5 and w[other] == 1.0
    finally:
        await F.done(conn, tx)


@pg
async def test_harmful_contemporaneous_evidence_supersedes():
    """Inside the lesson's span, three newer strategy lessons outrank it for
    a while (later replaced by versions of another strategy, so they leave
    INV's context), so 30 comparable decisions WITHOUT it exist in the same
    span: the contemporaneous comparison may SUPERSEDE; the lesson is never
    retrieved again and leaves the conversation context."""
    from sportsassets.agents import learning_context as LC
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        a = await F.account(conn, "lu4")
        acct = a["account_id"]
        lid = await _lesson(conn, a, at=NOW - 40 * H)
        used, comps = [], []
        for i in range(ARM):                    # with it: lost
            did, _ = await _entry(conn, a, at=NOW - 39 * H + i * 300,
                                  won=False, qty=99 + i)
            used.append(did)
        await LU.retrieve(conn, account_id=acct, now=NOW - 36 * H)
        series = ["s-out-%d-%s" % (k, uuid.uuid4().hex[:6]) for k in range(3)]
        for sk in series:
            await _lesson(conn, a, series=sk, at=NOW - 35 * H)
        for i in range(ARM):                    # outranked: without it, won
            did, _ = await _entry(conn, a, at=NOW - 34 * H + i * 300,
                                  won=True, qty=99 + i)
            comps.append(did)
        await LU.retrieve(conn, account_id=acct, now=NOW - 31 * H)
        for d in comps:
            assert lid not in [r["lesson_id"]
                               for r in await _retrievals(conn, d)]
        # the three series move to another strategy: out of INV's context
        for sk in series:
            await _lesson(conn, a, series=sk, version=2,
                          strategy="PINNACLE_EXPLORATION_PAPER",
                          at=NOW - 30 * H)
        did, _ = await _entry(conn, a, at=NOW - 29 * H, won=False, qty=98)
        used.append(did)
        await LU.retrieve(conn, account_id=acct, now=NOW - 28 * H)
        assert lid in [r["lesson_id"] for r in await _retrievals(conn, did)]
        rep = await LU.usefulness(conn, account_id=acct, now=NOW)
        m = [x for x in rep["lessons"] if x["lesson_id"] == lid][0][
            "measure"]
        assert m["design"] == LU.D_CONTEMPORANEOUS, m
        assert m["n_used"] == ARM + 1 and m["n_comparable"] == ARM
        assert m["status"] == LU.MEASURED and m["harmful_99"] is True
        got = await LU.supersede(conn, account_id=acct, now=NOW)
        assert lid in got["superseded"], got
        assert await LU.weight_at(conn, "paper_agent_lessons", lid,
                                  NOW + 1) == 0.0
        # never retrieved again
        late, _ = await _entry(conn, a, at=NOW + 60, won=None)
        await LU.retrieve(conn, account_id=acct, now=NOW + 120)
        assert lid not in [r["lesson_id"]
                           for r in await _retrievals(conn, late)]
        # ... and the conversation context excludes it, by name (R30B
        # review: learning_context.retrieve had kept serving it)
        sel = await LC.retrieve(conn, account_id=acct, agent="derek",
                                question="what about the refusal funnel",
                                now=NOW + 200)
        assert lid not in [x["lesson_id"] for x in sel["lessons"]]
        assert "SUPERSEDED_BY_FORWARD_EVIDENCE" in sel["rejected"]
    finally:
        await F.done(conn, tx)


@pg
async def test_a_superseded_memory_leaves_the_agents_own_context():
    """The agent's own context bundle (agent_memory.private_memories with
    include_superseded=False) leaves a superseded LESSON out; the
    operator's full view still shows it, as historical. (The supersession
    row is test setup carrying a MEASURED-shaped evidence document; the
    evaluator's own path is proven above.)"""
    from sportsassets.agents import agent_context as AC
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        a = await F.account(conn, "lu6")
        lid = await _derek_memory_lesson(conn, a, at=NOW - 10 * H,
                                         summary="A harmful belief.")
        keep = await _derek_memory_lesson(conn, a, at=NOW - 9 * H,
                                          summary="A sound belief.")
        await conn.execute(
            "INSERT INTO agent_lesson_supersessions (supersession_id, "
            " agent_id, lesson_table, lesson_id, action, previous_weight, "
            " weight, basis, evidence, evidence_refs, decided_by, "
            " decided_at) VALUES ($1,'DEREK','agent_memory_events',$2,"
            " 'SUPERSEDE',1,0,'FORWARD_INVESTMENT_OUTCOMES',$3::jsonb,"
            " $4::jsonb,'MEMORY_USEFULNESS_V1',to_timestamp($5))",
            "als:%024d" % 7, lid,
            json.dumps({"n_used": 30, "n_comparable": 30, "status":
                        "MEASURED", "mean_difference_usd": -10.0}),
            json.dumps([{"kind": "agent_memory_events", "id": lid}]),
            NOW - H)
        # (R30 tails integration) the bundle is read at its widest bound:
        # a shared test database keeps DEREK memories other proofs commit at
        # the wall clock, which outrank this proof's NOW-dated rows inside
        # the default 20; the ranking and the exclusion asserted below are
        # exactly the default bundle's (same query, same ORDER BY)
        ctx = await AC.build_context(conn, "DEREK", now=NOW,
                                     memory_limit=500)
        ids = [x["memory_id"] for x in ctx["own_memories"]]
        assert keep in ids and lid not in ids
        full = await M.private_memories(conn, reader=M.OPERATOR,
                                        owner="DEREK", limit=500)
        row = [x for x in full if x["memory_id"] == lid][0]
        assert row["lesson_weight"] == 0.0 and row["historical"] is True
    finally:
        await F.done(conn, tx)


@pg
async def test_karens_and_eddies_lessons_are_retrieved_and_reported():
    """(R30B review) Production's 224 LESSON memories are Karen's and
    Eddie's, and retrieval covered only Derek's and Xavier's decisions."""
    from sportsassets.agents import eddie as E
    from sportsassets.agents import karen as K
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        a = await F.account(conn, "lu7")
        acct = a["account_id"]
        # KAREN: a detector lesson, then a challenge she opens
        ref = "adr:lu7-%s" % uuid.uuid4().hex[:8]
        await conn.execute(
            "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
            " decided_at) VALUES ($1,'DEREK','TEST',to_timestamp($2))",
            ref, NOW - 5 * H)
        first = await K.open_challenge(
            conn, target_agent="DEREK", target_kind="agent_decisions",
            target_id=ref, detector="DECISION_WITHOUT_EVIDENCE",
            claim="no evidence", severity="MEDIUM",
            evidence_refs=[{"kind": "agent_decisions", "id": ref}],
            record_at=NOW - 5 * H, at=NOW - 4 * H)
        assert first["ok"], first
        km = await M.promote(conn, {
            "agent_id": "KAREN", "memory_kind": "LESSON",
            "subject_type": "karen_detector",
            "subject_id": "DECISION_WITHOUT_EVIDENCE",
            "summary": "This detector's challenges are usually upheld.",
            "evidence_refs": [{"kind": "karen_challenges",
                               "id": first["challenge_id"]}],
            "confidence": 0.7, "deriver": "test_fixture"}, now=NOW - 3 * H)
        assert km["ok"] and km["created"], km
        ref2 = "adr:lu7-%s" % uuid.uuid4().hex[:8]
        await conn.execute(
            "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
            " decided_at) VALUES ($1,'DEREK','TEST',to_timestamp($2))",
            ref2, NOW - 2 * H)
        later = await K.open_challenge(
            conn, target_agent="DEREK", target_kind="agent_decisions",
            target_id=ref2, detector="DECISION_WITHOUT_EVIDENCE",
            claim="no evidence", severity="MEDIUM",
            evidence_refs=[{"kind": "agent_decisions", "id": ref2}],
            record_at=NOW - 2 * H, at=NOW - H)
        assert later["ok"], later
        # EDDIE: an execution lesson, then an estimate of a filled decision
        did, _ = await _entry(conn, a, at=NOW - 2 * H, won=False)
        em = await M.promote(conn, {
            "agent_id": "EDDIE", "memory_kind": "LESSON",
            "subject_type": "execution_calibration",
            "subject_id": "all_measured_outcomes",
            "summary": "Slippage runs above the estimate on thin books.",
            "evidence_refs": [{"kind": "paper_decisions", "id": did}],
            "confidence": 0.6, "deriver": "test_fixture"},
            now=NOW - 2 * H - 600)
        assert em["ok"] and em["created"], em
        # Eddie's REAL runner writes his estimate of the decision
        from sportsassets.agents import eddie_runner as ER
        s_ = await ER.pass_once(conn, now=NOW - 2 * H + 300)
        est = E.estimate_id_for(did)
        assert await conn.fetchval(
            "SELECT count(*) FROM eddie_execution_estimates WHERE "
            " estimate_id = $1", est) == 1, s_
        await LU.retrieve(conn, account_id=acct, now=NOW)
        rk = await conn.fetch(
            "SELECT decision_table, decision_id FROM agent_lesson_retrievals"
            " WHERE lesson_id = $1", km["memory_id"])
        assert [(r["decision_table"], r["decision_id"]) for r in rk] == [
            ("karen_challenges", later["challenge_id"])]   # not the first
        re_ = await conn.fetch(
            "SELECT decision_table, decision_id FROM agent_lesson_retrievals"
            " WHERE lesson_id = $1", em["memory_id"])
        assert [(r["decision_table"], r["decision_id"]) for r in re_] == [
            ("eddie_execution_estimates", est)]
        rep = await LU.usefulness(conn, account_id=acct, now=NOW)
        by = {x["lesson_id"]: x for x in rep["lessons"]}
        mk = by[km["memory_id"]]["measure"]
        assert mk["status"] == LU.UNAVAILABLE
        assert mk["why"] == "%s:karen_challenges" % LU.R_NO_OUTCOME
        me = by[em["memory_id"]]
        # Eddie's estimate shares its decision's INVESTMENT outcome
        assert me["used_decisions"] == [
            {"table": "eddie_execution_estimates", "id": est}]
        assert me["measure"]["status"] == LU.UNAVAILABLE
        assert me["measure"]["why"].startswith("FEWER_THAN_2_EVENTS")
        # a lesson never in force for a decision of record is listed
        never = {x["lesson_id"]: x for x in rep["never_in_force"]}
        assert km["memory_id"] not in never
        nl = await _lesson(conn, a, agent="AUDREY", strategy=None,
                           at=NOW - H)
        rep = await LU.usefulness(conn, account_id=acct, now=NOW)
        never = {x["lesson_id"]: x for x in rep["never_in_force"]}
        assert never[nl]["measure"] == {"status": LU.UNAVAILABLE,
                                        "why": LU.R_NEVER}
        # and the scorecards' memory summary now sees Karen and Eddie
        from sportsassets.agents import agent_scorecards as S
        cards = await S.scorecards(conn, now=NOW, window_days=1,
                                   agents=("KAREN", "EDDIE"))
        mem = {c["agent"]: c["memory_usefulness"] for c in cards["agents"]}
        assert mem["KAREN"]["status"] == "MEASURED"
        assert mem["EDDIE"]["status"] == "MEASURED"
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
    # (pin changed, R30B review) 4 losses against 4 wins is a SMALL_SAMPLE:
    # reported -- the t bound alone would call it harmful at 99% -- and
    # never acted on (an exact test on 0/4 vs 4/4 gives p = 1/70)
    m = LU.compare(_rows([-40, -41, -39, -40.5], "u"),
                   _rows([60, 59, 61, 60.5], "c"))
    assert m["status"] == LU.SMALL and m["upper_99_usd"] < 0
    assert m["permutation_p"] > 0.01 and m["harmful_99"] is False
    for design in (LU.D_CONTEMPORANEOUS, LU.D_BEFORE_AFTER):
        assert LU.action_for(dict(m, design=design), 1.0) is None
    # two identical losses per arm: no interval at all, never harmful
    z = LU.compare(_rows([-10.0, -10.0], "u"), _rows([-2.0, -2.0], "c"))
    assert z["status"] == LU.SMALL and z["harmful_99"] is None
    assert z["upper_99_usd"] is None and LU.R_ZERO_VAR in z["why"]
    assert LU.action_for(dict(z, design=LU.D_CONTEMPORANEOUS), 1.0) is None
    z = LU.compare(_rows([-1.0] * 40, "u"), _rows([0.5] * 40, "c"))
    assert z["status"] == LU.MEASURED and z["harmful_95"] is None
    assert LU.action_for(z, 1.0) is None
    # MEASURED (30+ events per arm) and both tests agree: it acts
    big_loss = LU.compare(_rows([-40 - (i % 3) for i in range(30)], "u"),
                          _rows([60 + (i % 3) for i in range(30)], "c"))
    assert big_loss["status"] == LU.MEASURED and big_loss["harmful_99"]
    assert LU.action_for(dict(big_loss, design=LU.D_CONTEMPORANEOUS),
                         1.0) == {"action": "SUPERSEDE", "weight": 0.0}
    assert LU.action_for(dict(big_loss, design=LU.D_BEFORE_AFTER), 1.0) == {
        "action": "DOWNWEIGHT", "weight": 0.5}
    # win / lose outcomes, 30 per arm: 12 of 30 won vs 18 of 30 -- the
    # permutation test does not find it harmful at 95%
    wl = LU.compare(_rows([60.0] * 12 + [-40.0] * 18, "u"),
                    _rows([60.0] * 18 + [-40.0] * 12, "c"))
    assert wl["status"] == LU.MEASURED and wl["harmful_95"] is False
    assert LU.permutation_p([1.0, 2.0], [1.0, 2.0]) == \
        LU.permutation_p([1.0, 2.0], [1.0, 2.0])     # deterministic
    assert LU.MIN_EVENTS_FOR_MEASURED == 30     # = migration 301's CHECK
    assert "(evidence ->> 'n_used')::numeric >= 30" in F.UP
    noisy = LU.compare(_rows([-40, 50, -45, 55], "u"),
                       _rows([10, -5, 12, -8], "c"))
    assert not noisy["harmful_95"] and LU.action_for(noisy, 1.0) is None
    helpful = LU.compare(_rows([60, 59, 61], "u"), _rows([-40, -41, -39],
                                                         "c"))
    assert LU.action_for(helpful, 1.0) is None      # never upweights
    big = LU.compare(_rows([float(-i % 7) for i in range(40)], "u"),
                     _rows([float(i % 5) for i in range(40)], "c"))
    assert big["status"] == LU.MEASURED
    assert LU.action_for(big_loss, 0.0) is None     # superseded: nothing
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
