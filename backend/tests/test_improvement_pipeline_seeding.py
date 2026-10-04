"""THE IMPROVEMENT PIPELINE RUNNER SEEDS FROM REAL SIGNALS ONLY (migration 221).

  §1 THE COVERAGE CHAIN. A real coverage incident (coverage_integrity.run)
     -> Audrey's finding -> the improvement driver's loop finding (Derek's
     hypothesis, Audrey's SUSTAINED challenge, a PAPER_ONLY experiment) ->
     ONE COVERAGE_INCIDENT item whose HYPOTHESIS and PEER_CHALLENGE are the
     loop's own rows, verbatim, each citing its source; it then waits for
     KAREN (no fabricated challenge). The routed Audrey finding is not
     duplicated as an AUDREY_FINDING item. A second pass writes nothing.
  §2 THE FULL MIRROR. Karen challenges that finding, Derek DISPUTES, Audrey
     UPHOLDS; the loop records a candidate, Xavier's PASS and his eligibility
     mark -> the item reaches ELIGIBLE_CHANGE with the dissent preserved
     (DECIDED by Audrey, consensus false) and waits for a HUMAN APPROVAL the
     runner can never record.
  §3 KAREN UPHELD. An upheld challenge with a DISPUTE becomes an item owned
     by the challenged agent, at EVIDENCE (awaiting the owner's hypothesis),
     with the ORIGIN dissent preserved; a reconciliation challenge is
     protected (two reviews + human); an OPEN or REJECTED challenge seeds
     nothing.
  §4 EDDIE / FALSE REFUSALS / TOURNAMENTS. SKIP_EXECUTION estimates group
     per day (later rows are appended as EVIDENCE, not a transition);
     FALSE_REFUSAL rows group per defect only when the lost-opportunity table
     exists (absent -> nothing, no error); a VALIDATED Scout tournament seeds
     Scout's own predeclared hypothesis.
  §5 BOUNDED, ISOLATED, SWITCHED. The per-pass item budget holds; a source
     that raises is recorded by name while the others run; the kill switch
     stops a pass before it writes; every pass leaves an improve_runs row.
  §6 SLACK. One line per stage transition, under the recording agent's own
     identity (Karen's content only under Karen's dedicated token), at most
     two per pass and twelve per hour; nothing when nothing changed.

ALL DATA IS SYNTHETIC TEST DATA, inside a transaction that is rolled back.
"""
from __future__ import annotations

import json
import time

import asyncpg
import pytest

from sportsassets.agents import collaboration_loop as CL
from sportsassets.agents import coverage_integrity as C
from sportsassets.agents import improvement_driver as D
from sportsassets.agents import improvement_pipeline as P
from sportsassets.agents import improvement_stages as S
from sportsassets.agents import karen as K
from sportsassets.agents import registry as R
from tests import paper_harness as H
from tests import test_coverage_integrity_funnel as COVT

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
NOW = COVT.NOW
DEC = {"kind": "agent_decisions", "id": "adr:imp221-seed-1"}
DEC2 = {"kind": "agent_decisions", "id": "adr:imp221-seed-2"}


async def _tx():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    await R.ensure_identities(conn)
    for i, r in enumerate((DEC, DEC2)):
        await conn.execute(
            "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
            " decided_at) VALUES ($1,$2,'TEST',to_timestamp($3)) "
            "ON CONFLICT DO NOTHING", r["id"], ("DEREK", "XAVIER")[i],
            NOW - 3600)
    return conn, tx


async def _coverage_chain(conn):
    await COVT._seed(conn)
    acct = await H.new_account(conn, "imp", now=NOW - 5 * 86400)
    ctx = {"session_id": acct["session_id"],
           "account_id": acct["account_id"], "now": NOW}
    await C.run(conn, now=NOW, ctx=ctx, days=5)
    res = await D.run(conn, ctx=ctx, now=NOW + 60)
    d = [x for x in res["deficits"] if x["kind"] == "COVERAGE_COLLAPSE"][0]
    assert d["stage"] == CL.BOUNDED_EXPERIMENT, d
    return d["agent_finding_id"]


async def _items(conn, kind=None):
    rows = await conn.fetch("SELECT * FROM improve_items WHERE ($1::text IS "
                            " NULL OR source_kind=$1) ORDER BY item_id", kind)
    return [P._row(r) for r in rows]


async def _events(conn, iid):
    return [P._row(r) for r in await conn.fetch(
        "SELECT * FROM improve_events WHERE item_id=$1 ORDER BY event_id",
        iid)]


async def _source_text(conn, ref, field):
    if ref["kind"] == "agent_finding_stages":
        b = await conn.fetchval("SELECT body FROM agent_finding_stages "
                                " WHERE stage_id=$1", int(ref["id"]))
        return json.loads(b).get(field)
    if ref["kind"] == "karen_challenges":
        return await conn.fetchval(
            "SELECT %s FROM karen_challenges WHERE challenge_id=$1"
            % {"challenge": "claim", "response": "response"}[field],
            ref["id"])
    raise AssertionError(ref)


# ── §1 the coverage chain ────────────────────────────────────────────

@pg
async def test_a_coverage_incident_becomes_one_item_with_the_loops_own_words():
    conn, tx = await _tx()
    try:
        fid = await _coverage_chain(conn)
        s = await P.pass_once(conn, now=NOW + 120)
        assert s["status"] == "OK", s
        cov = await _items(conn, "COVERAGE_INCIDENT")
        assert len(cov) == 1
        it = cov[0]
        assert it["owner_agent"] == "DEREK"
        assert it["stage"] == S.PEER_CHALLENGE
        ev = await _events(conn, it["item_id"])
        assert [(e["stage"], e["actor"], e["actor_class"]) for e in ev] == [
            (S.EVIDENCE, S.RUNNER_ACTOR, S.RUNNER),
            (S.HYPOTHESIS, "DEREK", S.OWNER_AGENT),
            (S.PEER_CHALLENGE, "AUDREY", S.PEER_AGENT)]
        # every agent row is the source record's own text
        hyp, chal = ev[1], ev[2]
        assert hyp["body"]["hypothesis"] == await _source_text(
            conn, hyp["source_ref"], "hypothesis")
        assert hyp["body"]["finding_id"] == fid
        assert chal["body"]["challenge"] == await _source_text(
            conn, chal["source_ref"], "challenge")
        assert chal["stance"] == "SUSTAINED"
        for e in ev:
            assert await P.ref_exists(conn, e["source_ref"]), e
        # the evidence is the alert and Audrey's routed finding
        kinds = {r["kind"] for r in it["evidence_refs"]}
        assert kinds == {"coverage_collapse_alerts", "paper_audrey_findings"}
        # it waits for Karen: nothing invented in her place
        nxt = S.next_required(it, ev)
        assert nxt["label"] == "AWAITING PEER CHALLENGE (KAREN)"
        # the routed Audrey finding is not a second item
        routed = {r["id"] for r in it["evidence_refs"]
                  if r["kind"] == "paper_audrey_findings"}
        for a in await _items(conn, "AUDREY_FINDING"):
            assert not routed & {r["id"] for r in a["evidence_refs"]}
        # a second pass writes nothing new
        n_items = await conn.fetchval("SELECT count(*) FROM improve_items")
        n_ev = await conn.fetchval("SELECT count(*) FROM improve_events")
        s2 = await P.pass_once(conn, now=NOW + 180)
        assert s2["created"] == [] and s2["advanced"] == [], s2
        assert await conn.fetchval("SELECT count(*) FROM improve_items") \
            == n_items
        assert await conn.fetchval("SELECT count(*) FROM improve_events") \
            == n_ev
        assert await conn.fetchval("SELECT count(*) FROM improve_runs") == 2
        # the runner wrote no human or engineering step
        assert await conn.fetchval(
            "SELECT count(*) FROM improve_events WHERE actor_class IN "
            " ('HUMAN','ENGINEERING')") == 0
    finally:
        await tx.rollback()
        await conn.close()


# ── §2 the full mirror ───────────────────────────────────────────────

@pg
async def test_the_mirror_reaches_eligible_change_and_preserves_the_dissent():
    conn, tx = await _tx()
    try:
        fid = await _coverage_chain(conn)
        # Karen challenges Derek's finding; Derek disputes; Audrey upholds
        cid = "kc:imp221-test"
        await conn.execute(
            "INSERT INTO karen_challenges (challenge_id, target_agent, "
            " target_kind, target_id, finding_id, detector, claim, severity,"
            " evidence_refs, record_at, challenged_at) VALUES ($1,'DEREK',"
            " 'agent_findings',$2,$2,'TEST_LOOP_CHALLENGE',"
            " 'The collapse may be a provider outage, not a mapping defect.',"
            " 'MEDIUM',$3::jsonb,to_timestamp($4),to_timestamp($5))",
            cid, fid, json.dumps([{"kind": "agent_findings", "id": fid}]),
            NOW + 60, NOW + 70)
        got = await K.respond(conn, cid, agent="DEREK", stance="DISPUTE",
                              response="The provider still lists the events; "
                                       "it is a mapping defect.",
                              at=NOW + 80, evidence_refs=[DEC])
        assert got["ok"], got
        got = await K.resolve(conn, cid, resolver="AUDREY", outcome="UPHELD",
                              reason="The provider listing is stale.",
                              at=NOW + 90, evidence_refs=[DEC2])
        assert got["ok"], got
        # the loop goes on: candidate, Xavier's PASS, his eligibility mark
        got = await CL.record_candidate(
            conn, fid, actor="DEREK", description="map the NCAAF aliases",
            change={"kind": "MAPPING_ALIAS"}, at=NOW + 95)
        assert got["ok"], got
        got = await CL.record_evaluation(
            conn, fid, actor="XAVIER", outcome="PASS", data_start=NOW + 100,
            data_end=NOW + 200, evidence_refs=[DEC2], at=NOW + 300,
            metric_value=1.0, samples=10)
        assert got["ok"], got
        got = await CL.mark_release_eligible(conn, fid, actor="XAVIER",
                                             at=NOW + 310)
        assert got["ok"], got
        # Karen's challenge links the improvement the way she records it
        s = await P.pass_once(conn, now=NOW + 400)
        assert s["status"] == "OK", s
        it = (await _items(conn, "COVERAGE_INCIDENT"))[0]
        ev = await _events(conn, it["item_id"])
        assert [(e["stage"], e["actor"]) for e in ev] == [
            (S.EVIDENCE, S.RUNNER_ACTOR), (S.HYPOTHESIS, "DEREK"),
            (S.PEER_CHALLENGE, "AUDREY"), (S.PEER_CHALLENGE, "KAREN"),
            (S.OWNER_RESPONSE, "DEREK"), (S.EXPERIMENT, "DEREK"),
            (S.EXPERIMENT, "DEREK"), (S.INDEPENDENT_EVALUATION, "XAVIER"),
            (S.ELIGIBLE_CHANGE, "XAVIER")]
        assert it["stage"] == S.ELIGIBLE_CHANGE
        resp = ev[4]
        assert resp["stance"] == "DISPUTE"
        assert resp["body"]["response"] == await _source_text(
            conn, resp["source_ref"], "response")
        assert ev[3]["body"]["challenge"] == await _source_text(
            conn, ev[3]["source_ref"], "challenge")
        assert ev[7]["outcome"] == "PASS"
        assert ev[5]["experiment_refs"][0]["kind"] == "agent_finding_stages"
        # the dissent is preserved: decided by Audrey, never consensus
        ds = [P._row(r) for r in await conn.fetch(
            "SELECT * FROM improve_disagreements WHERE item_id=$1",
            it["item_id"])]
        assert len(ds) == 1
        d = ds[0]
        assert d["state"] == "DECIDED" and d["resolved_by"] == "AUDREY"
        assert d["consensus"] is False
        assert {p["party"] for p in d["positions"]} == {"KAREN", "DEREK"}
        assert "mapping defect" in [p for p in d["positions"]
                                    if p["party"] == "DEREK"][0]["position"]
        # and it waits for a human the runner can never be
        nxt = S.next_required(it, ev)
        assert nxt["code"] == "AWAITING_HUMAN_APPROVAL"
        assert nxt["actor_class"] == S.HUMAN
        s2 = await P.pass_once(conn, now=NOW + 500)
        assert s2["advanced"] == []
        assert await conn.fetchval(
            "SELECT count(*) FROM improve_events WHERE actor_class IN "
            " ('HUMAN','ENGINEERING') OR stage='CONTROLLED_RELEASE'") == 0
    finally:
        await tx.rollback()
        await conn.close()


# ── §3 Karen upheld ─────────────────────────────────────────────────

async def _upheld(conn, *, detector, claim, stance="DISPUTE",
                  outcome="UPHELD", resolver="AUDREY", target=DEC, base=NOW):
    """`base`: when the challenge is opened (base - 3000), answered (- 2000)
    and resolved (- 1000). The challenged record keeps its own time (the
    seeded decision, NOW - 3600) whatever the base."""
    got = await K.open_challenge(
        conn, target_agent="DEREK", target_kind=target["kind"],
        target_id=target["id"], detector=detector, claim=claim,
        severity="HIGH", evidence_refs=[target], record_at=NOW - 3600,
        at=base - 3000)
    assert got["ok"], got
    cid = got["challenge_id"]
    if stance:
        got = await K.respond(conn, cid, agent="DEREK", stance=stance,
                              response="The decision cites its valuation.",
                              at=base - 2000, evidence_refs=[target])
        assert got["ok"], got
    if outcome:
        got = await K.resolve(conn, cid, resolver=resolver, outcome=outcome,
                              reason="The valuation is absent from the "
                                     "record.", at=base - 1000,
                              evidence_refs=[target])
        assert got["ok"], got
    return cid


@pg
async def test_an_upheld_karen_challenge_seeds_an_item_with_the_dissent_kept():
    conn, tx = await _tx()
    try:
        cid = await _upheld(conn, detector="DECISION_WITHOUT_EVIDENCE",
                            claim="Derek's decision cites no valuation.")
        open_cid = await _upheld(conn, detector="ENTRY_WITHOUT_PROBABILITY",
                                 claim="still open", stance=None,
                                 outcome=None, target=DEC2)
        s = await P.pass_once(conn, now=NOW)
        assert s["status"] == "OK", s
        ks = await _items(conn, "KAREN_UPHELD_CHALLENGE")
        assert [i["source_key"] for i in ks] == [cid]
        assert open_cid not in {i["source_key"] for i in ks}
        it = ks[0]
        assert it["owner_agent"] == "DEREK" and it["stage"] == S.EVIDENCE
        assert {"kind": "karen_challenges", "id": cid} in it["evidence_refs"]
        assert DEC in it["evidence_refs"]
        ev = await _events(conn, it["item_id"])
        assert S.next_required(it, ev)["label"] == \
            "AWAITING HYPOTHESIS (DEREK)"
        ds = [P._row(r) for r in await conn.fetch(
            "SELECT * FROM improve_disagreements WHERE item_id=$1",
            it["item_id"])]
        assert len(ds) == 1 and ds[0]["stage"] == "ORIGIN"
        assert ds[0]["state"] == "DECIDED" and ds[0]["consensus"] is False
        assert [p["position"] for p in ds[0]["positions"]] == [
            "Derek's decision cites no valuation.",
            "The decision cites its valuation."]
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_reconciliation_challenge_is_protected_and_rejected_is_not_seeded():
    conn, tx = await _tx()
    try:
        await _upheld(conn, detector="RECONCILIATION_DISCREPANCY_OPEN",
                      claim="The ledger and the venue balance disagree.",
                      stance="CONCEDE")
        await _upheld(conn, detector="HOLD_ON_STALE_PROBABILITY",
                      claim="stale", outcome="REJECTED", resolver="AUDREY",
                      target=DEC2)
        await P.pass_once(conn, now=NOW)
        ks = await _items(conn, "KAREN_UPHELD_CHALLENGE")
        assert len(ks) == 1
        it = ks[0]
        assert "ACCOUNTING" in it["protected_areas"]
        assert it["requires_human_review"] is True
        assert it["required_independent_reviews"] == 2
        # a concession leaves no disagreement to preserve
        assert await conn.fetchval("SELECT count(*) FROM "
                                   " improve_disagreements") == 0
    finally:
        await tx.rollback()
        await conn.close()


# ── §4 Eddie, false refusals, tournaments ───────────────────────────

UNMEASURED = {k: "TEST_FIXTURE_NOT_MEASURED" for k in (
    "theoretical_edge", "fees", "spread_cost", "slippage",
    "adverse_selection", "fill_probability", "time_to_fill",
    "capital_hours", "max_executable_size", "net_executable_edge")}


async def _skip(conn, n, at):
    await conn.execute(
        "INSERT INTO eddie_execution_estimates (estimate_id, decision_id, "
        " estimator_version, estimated_at, recommendation, "
        " recommendation_reason, unmeasured, evidence_refs) VALUES ($1,$2,"
        " 'TEST',to_timestamp($3),'SKIP_EXECUTION',"
        " 'expected net executable edge <= 0 after fees',$4::jsonb,$5::jsonb)",
        "est:imp221-%d" % n, "dec:imp221-%d" % n, at, json.dumps(UNMEASURED),
        json.dumps([{"kind": "paper_decisions", "id": "dec:imp221-%d" % n}]))


@pg
async def test_eddie_skips_group_per_day_and_later_rows_append_evidence():
    conn, tx = await _tx()
    try:
        await _skip(conn, 1, NOW - 600)
        await _skip(conn, 2, NOW - 500)
        await P.pass_once(conn, now=NOW)
        es = await _items(conn, "EDDIE_SKIP_EXECUTION")
        assert len(es) == 1
        it = es[0]
        assert it["owner_agent"] == "EDDIE"
        assert len(it["evidence_refs"]) == 2
        assert "EXECUTION_AUTHORIZATION" in it["protected_areas"]
        await _skip(conn, 3, NOW - 100)
        await P.pass_once(conn, now=NOW + 60)
        assert len(await _items(conn, "EDDIE_SKIP_EXECUTION")) == 1
        ev = await _events(conn, it["item_id"])
        assert [(e["stage"], e["is_transition"]) for e in ev] == [
            (S.EVIDENCE, True), (S.EVIDENCE, False)]
        assert ev[1]["evidence_refs"] == [
            {"kind": "eddie_execution_estimates", "id": "est:imp221-3"}]
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_false_refusals_seed_only_when_the_ledger_exists():
    conn, tx = await _tx()
    try:
        if await conn.fetchval("SELECT to_regclass('lol_ledger') IS NOT NULL"):
            # migration 220 is applied: inside this rolled-back transaction
            # set the real (append-only) ledger aside so the scenario below
            # starts from "no ledger" and controls every row it reads
            await conn.execute("ALTER TABLE lol_ledger RENAME TO "
                               "lol_ledger_set_aside_by_test")
        assert await P.seed_false_refusals(conn, now=NOW) == []
        await conn.execute(
            "CREATE TABLE lol_ledger (ledger_id text PRIMARY KEY, "
            " decision_ref text, defect text, attribution text, league text,"
            " decided_at timestamptz, decision_time_net_ev_usd float8, "
            " classification text, classified_at timestamptz)")
        for i, (cls, defect) in enumerate((
                ("FALSE_REFUSAL", "STALE_GATE_FIRED_WITHOUT_STALE_INPUT"),
                ("FALSE_REFUSAL", "STALE_GATE_FIRED_WITHOUT_STALE_INPUT"),
                ("GOOD_REFUSAL", None))):
            await conn.execute(
                "INSERT INTO lol_ledger VALUES ($1,$2,$3,'FRESHNESS',"
                " 'basketball_nba',to_timestamp($4),$5,$6,to_timestamp($4))",
                "lol:%d" % i, "dec:%d" % i, defect, NOW - 900 + i,
                0.42 if defect else None, cls)
        await P.pass_once(conn, now=NOW)
        fr = await _items(conn, "FALSE_REFUSAL")
        assert len(fr) == 1
        assert fr[0]["owner_agent"] == "DEREK"
        assert {r["id"] for r in fr[0]["evidence_refs"]} == {"lol:0", "lol:1"}
        assert "RISK" in fr[0]["protected_areas"]      # freshness is risk
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_validated_scout_tournament_seeds_scouts_own_hypothesis():
    from sportsassets.agents import scout as SC
    conn, tx = await _tx()
    try:
        now = time.time()
        await SC.register_sources(conn, now=now)
        await SC.register_features(conn, now=now)
        f = (await SC.features(conn))[0]
        min_sample = await conn.fetchval(
            "SELECT min_sample FROM scout_feature_tournaments "
            " WHERE tournament_id=$1", f["tournament_id"])
        await conn.execute(
            "UPDATE scout_feature_tournaments SET n=$2, baseline_score=0.25,"
            " challenger_score=0.2, improvement=0.05, verdict='VALIDATED', "
            " verdict_reason='out-of-sample Brier gain', "
            " evaluated_by='CALIBRATION_ENGINE', "
            " evaluated_at=frozen_at + interval '1 minute' "
            " WHERE tournament_id=$1", f["tournament_id"], min_sample)
        await P.pass_once(conn, now=time.time() + 5)
        ts = await _items(conn, "TOURNAMENT_VERDICT")
        assert len(ts) == 1
        it = ts[0]
        assert it["owner_agent"] == "SCOUT" and it["stage"] == S.HYPOTHESIS
        ev = await _events(conn, it["item_id"])
        hyp = await conn.fetchval("SELECT predeclared_hypothesis FROM "
                                  " scout_features WHERE feature_id=$1",
                                  f["feature_id"])
        assert ev[1]["body"]["hypothesis"] == hyp
        assert ev[1]["source_ref"] == {"kind": "scout_features",
                                       "id": f["feature_id"]}
        assert S.next_required(it, ev)["label"] == \
            "AWAITING PEER CHALLENGE (KAREN + DEREK)"
    finally:
        await tx.rollback()
        await conn.close()


# ── §5 bounded, isolated, switched ──────────────────────────────────

@pg
async def test_the_budget_the_isolation_and_the_kill_switch(monkeypatch):
    conn, tx = await _tx()
    try:
        for n in range(4):
            await _skip(conn, 10 + n, NOW - 86400 * (n + 1))
        monkeypatch.setattr(P, "MAX_NEW_ITEMS_PER_PASS", 2)

        async def broken(conn, *, now):
            raise RuntimeError("source down")
        monkeypatch.setattr(P, "SOURCES", (("broken", broken),
                                           ("eddie", P.seed_eddie)))
        s = await P.pass_once(conn, now=NOW)
        assert "source:broken" in s["errors"]
        assert s["status"] == "PARTIAL"
        assert len(s["created"]) == 2                     # the budget holds
        s = await P.pass_once(conn, now=NOW + 1)
        assert len(s["created"]) == 2
        assert len(await _items(conn, "EDDIE_SKIP_EXECUTION")) == 4
        rows = await conn.fetch("SELECT status FROM improve_runs ORDER BY "
                                " started_at")
        assert [r["status"] for r in rows] == ["PARTIAL", "PARTIAL"]
        monkeypatch.setenv(P.ENV_KILL, "0")
        before = await conn.fetchval("SELECT count(*) FROM improve_events")
        s = await P.pass_once(conn, now=NOW + 2)
        assert s["status"] == "DISABLED"
        assert await conn.fetchval("SELECT count(*) FROM improve_events") \
            == before
    finally:
        await tx.rollback()
        await conn.close()


# ── §6 Slack ─────────────────────────────────────────────────────────

def _slack_env(monkeypatch, *, karen_shares_derek=False):
    monkeypatch.setenv("SLACK_TEAM_ID", "T1")
    monkeypatch.setenv("SLACK_ALLOWED_CHANNEL_IDS", "CWORK")
    monkeypatch.setenv("SLACK_WORKROOM_CHANNEL_ID", "CWORK")
    monkeypatch.setenv("SLACK_MANAGEMENT_USER_IDS", "UMGR")
    for a in ("DEREK", "XAVIER", "AUDREY", "KAREN", "EDDIE", "SCOUT"):
        tok = "xoxb-%s" % a.lower()
        if a == "KAREN" and karen_shares_derek:
            tok = "xoxb-derek"
        monkeypatch.setenv("SLACK_%s_BOT_TOKEN" % a, tok)
        monkeypatch.setenv("SLACK_%s_SIGNING_SECRET" % a, "sec-%s" % a)
        monkeypatch.setenv("SLACK_%s_APP_ID" % a, "A%s" % a)


async def _live_item(conn, now):
    """A Karen-upheld item whose transitions happen now (inside the Slack
    window), then a hypothesis mirrored by hand through the runner's own
    write path with a real source.

    THE CHALLENGE IS RESOLVED RELATIVE TO THAT `now` (R30A ci, 2026-10-04).
    It used to be resolved at the fixed fixture instant (NOW - 1000, i.e.
    2026-09-20 17:43Z) while the pass ran at the wall clock, and
    `seed_karen` reads only challenges resolved within LOOKBACK_S (14 days)
    of its `now`. Both Slack tests therefore passed until 2026-10-04
    17:43Z and failed from then on -- `s["created"]` empty, IndexError --
    in capital-critical run 37230040128 and here, with no code changed. The
    rule (an upheld challenge seeds an item for 14 days) is untouched; the
    fixture's challenge is now one the rule admits on every day the test
    runs."""
    await _upheld(conn, detector="DECISION_WITHOUT_EVIDENCE",
                  claim="Derek's decision cites no valuation.", base=now)
    s = await P.pass_once(conn, now=now)
    return s["created"][0]


@pg
async def test_slack_posts_one_line_per_transition_under_its_own_agent(
        monkeypatch):
    from sportsassets import slack_bridge as B
    _slack_env(monkeypatch)
    monkeypatch.setattr(P, "SOURCES", (("karen", P.seed_karen),))
    conn, tx = await _tx()
    try:
        now = time.time()
        await conn.execute("DELETE FROM agent_slack_delivery "
                           " WHERE source_key ~ '(^|:)improve:'")
        iid = await _live_item(conn, now)
        it, ev = await P.load(conn, iid)
        # a peer challenge by Karen and one by Xavier (only the first is a
        # transition), mirrored through the runner's write path
        async with P.runner_tx(conn):
            # the owner's hypothesis first (a real record of Derek's)
            got = await P.add_event(conn, it, ev, {
                "stage": S.HYPOTHESIS, "actor": "DEREK",
                "actor_class": S.OWNER_AGENT, "at": now + 1,
                "body": {"hypothesis": "decisions skip the valuation join"},
                "source_ref": DEC})
            assert got["ok"], got
            for actor, cls, ref in (("KAREN", S.CHALLENGER, DEC),
                                    ("XAVIER", S.PEER_AGENT, DEC2)):
                got = await P.add_event(conn, it, ev, {
                    "stage": S.PEER_CHALLENGE, "actor": actor,
                    "actor_class": cls, "at": now + 2, "stance": "CHALLENGES"
                    if cls == S.CHALLENGER else "SUSTAINED",
                    "body": {"challenge": "c by %s" % actor},
                    "source_ref": ref})
                assert got["ok"], got
        trans = [e for e in ev if e["is_transition"]]
        assert [e["stage"] for e in trans] == [S.EVIDENCE, S.HYPOTHESIS,
                                               S.PEER_CHALLENGE]
        await B.publish_improvement_posts(conn)
        rows = await conn.fetch(
            "SELECT agent, source_key, answer FROM agent_slack_delivery "
            " WHERE source_key ~ '(^|:)improve:' ORDER BY created_at, "
            " source_key")
        assert len(rows) == 2                      # two per pass
        await B.publish_improvement_posts(conn)
        rows = await conn.fetch(
            "SELECT agent, source_key, answer FROM agent_slack_delivery "
            " WHERE source_key ~ '(^|:)improve:' ORDER BY source_key")
        assert len(rows) == 3                      # one per transition
        by_key = {r["source_key"]: r for r in rows}
        ids = {e["stage"]: e["event_id"] for e in trans}
        assert by_key["improve:%d" % ids[S.EVIDENCE]]["agent"] == "audrey"
        assert by_key["improve:%d" % ids[S.HYPOTHESIS]]["agent"] == "derek"
        k = by_key["karen:improve:%d" % ids[S.PEER_CHALLENGE]]
        assert k["agent"] == "karen"
        assert iid in k["answer"] and "/improvements?item=" in k["answer"]
        assert "karen_challenges" in by_key[
            "improve:%d" % ids[S.EVIDENCE]]["answer"]
        assert "AWAITING PEER CHALLENGE (XAVIER)" in k["answer"] or \
            "AWAITING OWNER RESPONSE (DEREK)" in k["answer"]
        # nothing changed -> nothing posted
        await B.publish_improvement_posts(conn)
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_slack_delivery "
            " WHERE source_key ~ '(^|:)improve:'") == 3
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_slack_respects_token_separation_and_the_hourly_cap(monkeypatch):
    from sportsassets import slack_bridge as B
    _slack_env(monkeypatch, karen_shares_derek=True)
    monkeypatch.setattr(P, "SOURCES", (("karen", P.seed_karen),))
    conn, tx = await _tx()
    try:
        now = time.time()
        await conn.execute("DELETE FROM agent_slack_delivery "
                           " WHERE source_key ~ '(^|:)improve:'")
        iid = await _live_item(conn, now)
        it, ev = await P.load(conn, iid)
        async with P.runner_tx(conn):
            await P.add_event(conn, it, ev, {
                "stage": S.HYPOTHESIS, "actor": "DEREK",
                "actor_class": S.OWNER_AGENT, "at": now + 1,
                "body": {"hypothesis": "h"}, "source_ref": DEC})
            await P.add_event(conn, it, ev, {
                "stage": S.PEER_CHALLENGE, "actor": "KAREN",
                "actor_class": S.CHALLENGER, "at": now + 2,
                "stance": "CHALLENGES", "body": {"challenge": "c"},
                "source_ref": DEC})
        for _ in range(3):
            await B.publish_improvement_posts(conn)
        rows = await conn.fetch("SELECT agent, source_key FROM "
                                " agent_slack_delivery WHERE source_key ~ "
                                " '(^|:)improve:'")
        # Karen's token is not her own: her transition is not posted under
        # anyone's token
        assert {r["agent"] for r in rows} == {"audrey", "derek"}
        assert not any(r["source_key"].startswith("karen:") for r in rows)
        # the hourly cap
        monkeypatch.setattr(B, "IMPROVE_POSTS_PER_HOUR", 2)
        await conn.execute("DELETE FROM agent_slack_delivery "
                           " WHERE source_key ~ '(^|:)improve:'")
        _slack_env(monkeypatch)
        for _ in range(3):
            await B.publish_improvement_posts(conn)
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_slack_delivery "
            " WHERE source_key ~ '(^|:)improve:'") == 2
    finally:
        await tx.rollback()
        await conn.close()
