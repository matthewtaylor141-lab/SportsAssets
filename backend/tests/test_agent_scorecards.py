"""CAPITAL-CRITICAL: AGENT SCORECARDS BY DECISION AND ECONOMIC QUALITY,
NEVER ACTIVITY (owner R30 program section 18), from production-shaped rows
written by the REAL writers.

  §1 THE PRODUCTION STALE-HOLD FLOOD (research-sql run 37226555657): Xavier
     HOLD reviews in production's pre-R30 shape; Karen's REAL runner
     challenges them, the REAL peer responder has Xavier concede and Audrey
     uphold. Karen's challenge quality is UPHELD / resolved with its n;
     Xavier's false approvals are the SAME RULE over every HOLD review (a
     census, not Karen's throttled sample), joined to the positions;
     Audrey's evaluation latency / evidence come from her resolutions; the
     citations they recorded all resolve; a role that makes no such
     decision is NOT_APPLICABLE with its reason; nothing is a zero.
  §2 DEREK through his records: the decision probability's age at the
     decision (latency), the evidence an ENTER rests on, the 30 s rule on
     Pinnacle-priced entries (read, never changed), Brier against the
     settlement paired with the limit price (positions through the ledger,
     handoffs through paper_xavier's own writer), false approvals by Karen's
     ENTRY_WITHOUT_PROBABILITY rule, false refusals from the lost-
     opportunity ledger (its own writer; UNKNOWABLE reported, excluded).
     XAVIER's value added and false exits from xavier_management's own
     thesis builder / value-add writer over the ledger's fills and the
     venue's settlement evidence (PAPER; ACTUAL never summed in).
  §3 EDDIE through his runner: latency and evidence from his estimates; a
     refused estimate is a BLOCKED queue item whose age is the unresolved
     blocker age; Eddie's hold-back advice on a candidate that filled
     anyway and kept its edge is a false refusal; unmeasured predicted /
     naive losses leave calibration and value UNAVAILABLE; every other card
     reads its own queue (NO_BLOCKER, a measured empty set, when nothing it
     owns is blocked).
  §4 MEMORY USEFULNESS beside the card: lessons retrieved by lesson_usage's
     own writer -- a summary that grants nothing.
  §5 CONSTANTS PINNED TO THEIR SOURCES (the 30 s rule, Eddie's bound, the
     twin window, Xavier's evidence states, Karen's targets / rules).
  §6 PURE: status rules (UNAVAILABLE needs its reason and is never 0;
     SMALL_SAMPLE below MIN_N; an activity name is refused), the window,
     percentiles, the blocker card, the twin card.
  §7 THE CITATION RESOLVER: an existing record resolves, a missing one does
     not, an unmapped kind is UNVERIFIABLE (never counted right or wrong).
  §8 THE READ API: GET only, COMMAND auth, one READ ONLY transaction.
"""
from __future__ import annotations

import pathlib

import pytest

from sportsassets.agents import agent_scorecards as S
from sportsassets.agents import registry as R

from tests import agent_ops_fixture as F

pg = F.pg
NOW = F.NOW
H = 3600.0
ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"


def _cards(got) -> dict:
    """{agent: {metric: metric}} with the shape checks every card obeys."""
    out = {}
    for card in got["agents"]:
        ms = card["metrics"]
        assert [m["metric"] for m in ms] == list(S.METRICS), card["agent"]
        for m in ms:
            assert m["status"] in S.STATUSES, m
            assert not S.NOT_A_SCORE.search(m["metric"]), m
            assert set(m) >= {"value", "n", "window", "status", "reason",
                              "definition", "source", "unit", "detail"}
            if m["status"] in (S.UNAVAILABLE, S.NOT_APPLICABLE,
                               S.NO_BLOCKER):
                assert m["value"] is None and m["reason"], m
            if m["status"] == S.SMALL:
                assert m["value"] is not None and 0 < m["n"] < S.MIN_N, m
            if m["status"] == S.MEASURED and m["metric"] != \
                    "unresolved_blocker_age":
                assert m["value"] is not None and m["n"] >= S.MIN_N, m
        out[card["agent"]] = {m["metric"]: m for m in ms}
    return out


# ═════════════════════════════════════════════════════════════════════
# §1 THE PRODUCTION STALE-HOLD FLOOD
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_karen_xavier_and_audrey_from_the_replayed_flood(monkeypatch):
    from tests.test_improvement_clusters import _replay
    conn, tx = await F.tx()
    try:
        a, pos = await _replay(conn, monkeypatch)     # 3 groups x 13 reviews
        now = NOW + 600
        got = await S.scorecards(conn, now=now, window_days=7)
        assert got["single_score"] is None
        assert got["authority"] == "NONE_RECORDS_ONLY"
        c = _cards(got)
        k = c["KAREN"]
        # 39 challenges, all UPHELD by a third party: quality 1.0, n = 39
        assert k["challenge_quality"]["value"] == 1.0
        assert k["challenge_quality"]["n"] == 39
        assert k["challenge_quality"]["status"] == S.MEASURED
        assert k["false_refusal"]["value"] == 0.0     # none overturned
        assert k["false_refusal"]["n"] == 39
        assert k["evidence_completeness"]["value"] == 1.0   # cites target
        lat = [float(r[0]) for r in await conn.fetch(
            "SELECT extract(epoch FROM challenged_at - record_at) FROM "
            " karen_challenges")]
        assert k["decision_latency"]["n"] == 39
        assert k["decision_latency"]["value"] == pytest.approx(
            S.percentile(lat, 0.5), abs=1e-3)
        assert k["decision_latency"]["detail"]["p90_s"] == pytest.approx(
            S.percentile(lat, 0.9), abs=1e-3)
        assert k["false_approval"]["status"] == S.UNAVAILABLE
        assert "NO_INDEPENDENT_DEFECT_CENSUS" in k["false_approval"]["reason"]
        assert k["calibration"]["status"] == S.NOT_APPLICABLE
        assert k["freshness_compliance"]["status"] == S.NOT_APPLICABLE
        assert k["value_added"]["reason"] == "TWIN_SCORECARD_NOT_COMPUTED"
        # every challenge cites the review it challenges: all resolve
        assert k["citation_correctness"]["value"] == 1.0
        assert k["citation_correctness"]["n"] == 39

        x = c["XAVIER"]
        # the SAME RULE over every HOLD review of the window: 39 / 39
        fa = x["false_approval"]
        assert fa["value"] == 1.0 and fa["n"] == 39, fa
        assert fa["detail"]["rule"] == "HOLD_ON_STALE_PROBABILITY"
        assert fa["detail"]["rule_hits"] == 39
        assert fa["detail"]["upheld_challenges"] == 39
        assert fa["detail"]["flagged_positions"] == 3
        assert fa["detail"]["flagged_positions_resolved"] == {"WON": 0,
                                                              "LOST": 0}
        # Xavier conceded every challenge: no resolved dispute to score
        cq = x["challenge_quality"]
        assert cq["status"] == S.UNAVAILABLE
        assert cq["reason"] == "NO_RESOLVED_DISPUTE (39 conceded)"
        assert x["citation_correctness"]["value"] == 1.0   # his responses
        for m in ("decision_latency", "evidence_completeness",
                  "calibration", "freshness_compliance", "value_added"):
            assert x[m]["status"] == S.UNAVAILABLE and x[m]["reason"], m

        au = c["AUDREY"]
        assert au["decision_latency"]["n"] == 39
        assert au["decision_latency"]["value"] >= 0
        assert au["evidence_completeness"]["value"] == 1.0
        assert au["citation_correctness"]["value"] == 1.0
        assert au["false_approval"]["status"] == S.UNAVAILABLE
        for agent in ("XAVIER", "AUDREY"):
            assert c[agent]["unresolved_blocker_age"]["status"] == \
                S.NO_BLOCKER
        # Eddie and Scout are not challenge targets
        for agent in ("EDDIE", "SCOUT"):
            assert c[agent]["challenge_quality"]["status"] == \
                S.NOT_APPLICABLE
        # one agent only
        one = await S.scorecards(conn, now=now, agents=("KAREN",))
        assert [card["agent"] for card in one["agents"]] == ["KAREN"]
        assert _cards(one)["KAREN"]["challenge_quality"]["value"] == 1.0
    finally:
        await F.done(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# §2 DEREK
# ═════════════════════════════════════════════════════════════════════

def _lol(ref, cls, at, *, defect=None, net=None, attribution="THRESHOLD"):
    from sportsassets.lost_opportunity import classify as CL
    return {"decision_ref": ref, "source": "PAPER_DECISION",
            "classifier_version": CL.VERSION, "decided_at": at,
            "strategy": F.EXPLORATION, "league": None, "league_basis": None,
            "us_market_slug": "aec-test-lol", "holding_side": "LONG",
            "classification": cls, "reason": "TEST_FIXTURE",
            "defect": defect, "refusal": "BELOW_MIN_GROSS_EDGE",
            "refusals": ["BELOW_MIN_GROSS_EDGE"],
            "refusal_category": "THRESHOLD", "attribution": attribution,
            "attribution_code": None, "decision_evidence_ids": [ref],
            "decision_time_net_ev_usd": net,
            "decision_time_net_ev_basis": "TEST_FIXTURE",
            "decision_time_executable_price": 0.40,
            "decision_time_qty": 100.0, "decision_time_fees_usd": 0.0,
            "decision_time_cost_usd": 40.0, "policy_min_gross_edge": 0.02,
            "policy_min_net_ev_usd": 0.0,
            "settlement_evidence_id": "papersettle:test",
            "settlement_basis": "PAPER_SETTLEMENT",
            "settled_at": at + H, "settlement_outcome": "WON",
            "payout_per_contract": 1.0, "hypothetical_pnl_usd": 60.0,
            "hypothetical_pnl_why": None, "detail": {}}


@pg
async def test_dereks_card_from_his_records():
    from sportsassets.agents import paper_xavier as PX
    from sportsassets.lost_opportunity import store as LS
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        a = await F.account(conn, "scd")
        fresh = {"p": 0.6, "age_s": 5.0, "limit_s": 30.0,
                 "qualification": "FRESH"}
        def full(i):        # one decision per valuation per strategy
            return {"valuation_id": 1001 + i, "book_obs_id": 2001 + i,
                    "economics": {"expected_net_profit_usd": 4.0}}
        settled = []
        for i, (p, won) in enumerate(((0.7, True), (0.3, False),
                                      (0.6, True), (0.6, False))):
            at = NOW - (30 - i) * H
            did = await F.decision(conn, a, at=at, p=p, limit=0.40,
                                   pinnacle=dict(fresh, p=p), **full(i))
            await F.position(conn, a, at=at + 5, decision_id=did,
                             outcome="WON" if won else "LOST",
                             payout=1.0 if won else 0.0,
                             settle_at=at + 3 * H)
            settled.append(did)
        stale = await F.decision(conn, a, at=NOW - 5 * H, pinnacle=dict(
            fresh, age_s=45.0, qualification="STALE"), **full(9))
        no_p = await F.decision(conn, a, at=NOW - 4 * H, p=None)
        # the handoffs, by paper_xavier's own writer
        await PX.step_handoff(conn, {"account_id": a["account_id"],
                                     "session_id": a["session_id"],
                                     "now": NOW})
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_handoffs WHERE decision_id = "
            " ANY($1::text[])", settled) == 4
        # four settled refusals classified by the ledger's own writer
        refs = [await F.decision(conn, a, at=NOW - (20 - i) * H,
                                 verdict="REFUSE") for i in range(4)]
        rows = [_lol(refs[0], "FALSE_REFUSAL", NOW - 20 * H,
                     defect="STALE_GATE_FIRED_WITHOUT_ITS_CONDITION",
                     net=3.5, attribution="DEFECT"),
                _lol(refs[1], "GOOD_REFUSAL", NOW - 19 * H),
                _lol(refs[2], "GOOD_REFUSAL", NOW - 18 * H),
                _lol(refs[3], "UNKNOWABLE", NOW - 17 * H)]
        assert await LS.save_ledger(conn, run_id="lol-run-test", now=NOW,
                                    rows=rows) == 4
        got = await S.scorecards(conn, now=NOW, window_days=7,
                                 agents=("DEREK",))
        d = _cards(got)["DEREK"]
        # latency: the probability's age at the decision, five recorded it
        assert d["decision_latency"]["n"] == 5
        assert d["decision_latency"]["value"] == 5.0
        assert d["decision_latency"]["status"] == S.SMALL
        # evidence: 5 of 6 entries carry everything; one has no probability
        ec = d["evidence_completeness"]
        assert (ec["value"], ec["n"]) == (round(5 / 6, 6), 6)
        assert ec["detail"]["no_probability"] == 1
        # the 30 s rule over the 5 Pinnacle-priced entries: one was 45 s old
        fr = d["freshness_compliance"]
        assert (fr["value"], fr["n"]) == (0.8, 5), fr
        assert fr["detail"] == {"stale": 1, "unknown_age": 0, "unpriced": 1}
        # Brier over the 4 settled entries, paired with the limit price
        cal = d["calibration"]
        assert cal["n"] == 4
        assert cal["value"] == pytest.approx((0.09 + 0.09 + 0.16 + 0.36) / 4)
        assert cal["detail"]["brier_of_the_limit_price"] == pytest.approx(
            (0.36 + 0.16 + 0.36 + 0.16) / 4)
        assert cal["detail"]["skill_vs_limit_price"] == pytest.approx(
            0.26 - 0.175)
        # false approvals: Karen's ENTRY_WITHOUT_PROBABILITY over every entry
        fa = d["false_approval"]
        assert (fa["value"], fa["n"]) == (round(1 / 6, 6), 6), fa
        assert fa["detail"]["rule_hits"] == 1
        assert fa["detail"]["flagged_resolved_outcomes"] == {
            "WON": 0, "LOST": 0, "UNSETTLED": 1}
        # false refusals: 1 FALSE of 3 classified; UNKNOWABLE excluded
        frf = d["false_refusal"]
        assert (frf["value"], frf["n"]) == (round(1 / 3, 6), 3), frf
        assert frf["detail"]["unknowable"] == 1
        assert frf["detail"]["false_by_attribution"] == {"DEFECT": 1}
        assert frf["detail"]["false_refusal_hypothetical_pnl_usd"] == 60.0
        assert d["value_added"]["status"] == S.UNAVAILABLE
        assert stale and no_p
        # the window bounds what is read: a week later none of it counts
        later = _cards(await S.scorecards(conn, now=NOW + 8 * 86400,
                                          agents=("DEREK",)))["DEREK"]
        assert later["evidence_completeness"]["reason"] == \
            "NO_ENTER_DECISION_IN_WINDOW"
        assert later["false_refusal"]["reason"] == \
            "NO_CLASSIFIED_SETTLED_REFUSAL_IN_WINDOW"
    finally:
        await F.done(conn, tx)


async def _thesis(conn, XM, pos, slug, at):
    """Xavier's entry thesis, built and recorded by xavier_management's own
    pure builder and writer (migration 206)."""
    t = XM.build_thesis(
        kind="PAPER", group_id=pos["group_id"],
        position_ref=pos["position_key"], decision_id=None,
        strategy=F.EXPLORATION, slug=slug, holding_side="LONG",
        entered_at=at, first_fill_at=at + 2, qty=100, entry_cost_usd=40.0,
        entry_fees_usd=0.0, entry_price_per_contract=0.40, p=0.62,
        probability_source="ENTRY_DECISION_PINNACLE",
        probability_source_at=at - 5, limit_s=30.0, event_start_at=None,
        assumptions={}, evidence_refs=[],
        exit_walk={"sold": 100.0, "unsold": 0.0, "proceeds_usd": 38.0,
                   "fees_usd": 0.0}, exit_basis={"obs_id": 1})
    assert (await XM.record_thesis(conn, t))["ok"]
    return await XM.thesis_for(conn, "PAPER", pos["group_id"])


@pg
async def test_xaviers_value_added_and_false_exits_from_his_value_add():
    """Three PAPER positions bought at 0.40: one held to a LOST settlement
    (adds nothing against holding), one sold at 0.55 before that loss (+$55
    against holding), one sold at 0.55 before a WON settlement (-$45: the
    exit fell short of holding). The value-add rows come from
    xavier_management.compute_value_add over the ledger's fills and the
    venue's settlement evidence."""
    import time as _time

    from sportsassets.agents import xavier_management as XM
    from tests import paper_live_fixture as PL
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        a = await F.account(conn, "scdx")
        at = NOW - 29 * H
        lost = "%sscx-l-%s" % (PL.SYN, a["account_id"][-8:])
        won = "%sscx-w-%s" % (PL.SYN, a["account_id"][-8:])
        v_lost = await PL.valuation(conn, slug=lost, decided_at=at - H)
        v_won = await PL.valuation(conn, slug=won, decided_at=at - H)
        held = await F.position(conn, a, slug=lost, at=at, outcome="LOST",
                                payout=0.0, settle_at=NOW - 10 * H)
        good = await F.position(conn, a, slug=lost, at=at + 10)
        bad = await F.position(conn, a, slug=won, at=at + 20)
        await F.exit_fill(conn, a, good, qty=100, price=0.55,
                          at=NOW - 20 * H)
        await F.exit_fill(conn, a, bad, qty=100, price=0.55,
                          at=NOW - 20 * H)
        await PL.settle_valuation(conn, v_lost["valuation_id"], outcome=0)
        await PL.settle_valuation(conn, v_won["valuation_id"], outcome=1)
        for pos, slug in ((held, lost), (good, lost), (bad, won)):
            t = await _thesis(conn, XM, pos, slug, at)
            got = await XM.compute_value_add(conn, t)
            assert got["ok"] and got["status"] == "FINAL", got
        inc = sorted(float(S._j(r["incremental"])[
            "ACTUAL_XAVIER_minus_HOLD_TO_SETTLEMENT"]["pnl_usd"])
            for r in await conn.fetch("SELECT incremental FROM "
                                      " xavier_value_add"))
        assert inc == [-45.0, 0.0, 55.0]
        # value-add rows are stamped by the database clock
        x = _cards(await S.scorecards(conn, now=_time.time() + 60,
                                      window_days=1,
                                      agents=("XAVIER",)))["XAVIER"]
        va = x["value_added"]
        assert va["value"] == pytest.approx(10.0 / 3, abs=1e-6)
        assert va["n"] == 3 and va["status"] == S.SMALL
        assert va["detail"]["total_usd"] == 10.0
        assert va["detail"]["actual_book"] is None    # never summed in
        fr = x["false_refusal"]
        assert (fr["value"], fr["n"]) == (0.5, 2), fr   # 1 of 2 exits
    finally:
        await F.done(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# §3 EDDIE, AND THE UNRESOLVED BLOCKER AGE
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_eddies_card_and_a_blocked_item_from_his_runner(monkeypatch):
    from sportsassets.agents import eddie as E
    from sportsassets.agents import eddie_runner as ER
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        a = await F.account(conn, "scde")
        t = NOW
        from tests import paper_harness as PH
        d1 = await F.decision(conn, a, at=t - 120)
        d2 = await F.decision(conn, a, at=t - 110)
        # d3: a recorded venue book before the decision, and Derek's entry
        # filled anyway -- Eddie's runner measures its outcome
        slug = "aec-test-scde-%s" % a["account_id"][-6:]
        obs = await PH.observe(conn, slug, t - 140, bids=[(0.38, 500)],
                               offers=[(0.40, 500)])
        d3 = await F.decision(conn, a, at=t - 130, slug=slug,
                              book_obs_id=obs, limit=0.40)
        await F.position(conn, a, at=t - 100, decision_id=d3, slug=slug,
                         price=0.40)
        real = E.record_estimate

        async def record(conn_, est):
            if est["decision_id"] == d2:
                return {"ok": False, "refusal": "HARD_RULE_VIOLATION_REFUSED"}
            return await real(conn_, est)
        monkeypatch.setattr(E, "record_estimate", record)
        s = await ER.pass_once(conn, now=t + 300)
        assert s["refused"] == {d2: "HARD_RULE_VIOLATION_REFUSED"}, s
        now = t + 400
        got = await S.scorecards(conn, now=now, window_days=1)
        c = _cards(got)
        e = c["EDDIE"]
        ests = await conn.fetch(
            "SELECT extract(epoch FROM estimated_at - decided_at)::float8 AS "
            " lat, (unmeasured IS NULL OR unmeasured = '{}'::jsonb) AS full "
            " FROM eddie_execution_estimates WHERE decision_id = "
            " ANY($1::text[])", [d1, d3])
        assert len(ests) == 2
        assert e["decision_latency"]["n"] == 2
        assert e["decision_latency"]["value"] == pytest.approx(
            S.percentile([r["lat"] for r in ests], 0.5))
        assert e["decision_latency"]["status"] == S.SMALL
        assert e["evidence_completeness"]["n"] == 2
        assert e["evidence_completeness"]["value"] == S.rate(
            sum(1 for r in ests if r["full"]), 2)
        # d3's outcome, as Eddie's runner measured it: his hold-back advice
        # on a candidate that filled anyway and kept its edge is a false
        # refusal; the unmeasured predicted / naive losses leave
        # calibration and value UNAVAILABLE -- never a zero
        o = await conn.fetchrow(
            "SELECT e.recommendation, x.realized_execution_loss_pp AS rl, "
            "       e.theoretical_edge_pp AS te, "
            "       x.predicted_execution_loss_pp AS pl, "
            "       x.naive_execution_loss_pp AS nl "
            "  FROM eddie_execution_outcomes x JOIN eddie_execution_estimates"
            "  e USING (estimate_id) WHERE x.decision_id = $1", d3)
        assert o is not None and o["rl"] is not None and o["te"] is not None
        fr = e["false_refusal"]
        if o["recommendation"] in S.HOLD_BACK_RECS:
            assert fr["n"] == 1
            assert fr["value"] == (1.0 if o["rl"] < o["te"] else 0.0)
            assert e["false_approval"]["status"] == S.UNAVAILABLE
        else:
            assert e["false_approval"]["n"] == 1
            assert fr["status"] == S.UNAVAILABLE
        assert o["pl"] is None and o["nl"] is None
        for m in ("calibration", "value_added"):
            assert e[m]["status"] == S.UNAVAILABLE and e[m]["reason"], m
        # the refused estimate: a BLOCKED queue item, aged from the block
        b = e["unresolved_blocker_age"]
        rq = await conn.fetchrow(
            "SELECT r.request_id, r.blocker, extract(epoch FROM "
            " r.enqueued_at)::float8 AS enq, (SELECT extract(epoch FROM "
            " min(at))::float8 FROM agent_work_request_events x WHERE "
            " x.request_id = r.request_id AND x.outcome = 'BLOCKED') AS blk "
            " FROM agent_work_requests r WHERE r.group_id = $1", d2)
        since = rq["enq"] if rq["blocker"] else rq["blk"]
        assert b["status"] == S.MEASURED and b["n"] == 1, b
        assert b["value"] == pytest.approx(now - since, abs=0.2)
        assert b["detail"]["oldest"]["request_id"] == rq["request_id"]
        assert b["detail"]["oldest"]["blocker"] == \
            "HARD_RULE_VIOLATION_REFUSED"
        assert b["detail"]["by_blocker"] == {"HARD_RULE_VIOLATION_REFUSED": 1}
        assert b["window"]["basis"] == "OPEN_QUEUE_ITEMS_AT_READ"
        for agent in ("DEREK", "XAVIER", "AUDREY", "KAREN", "SCOUT",
                      "CHIEF_ALLOCATOR"):
            assert c[agent]["unresolved_blocker_age"]["status"] in (
                S.NO_BLOCKER, S.MEASURED)
            if c[agent]["unresolved_blocker_age"]["status"] == S.NO_BLOCKER:
                assert "NO_OPEN_ITEM_IS_BLOCKED" in \
                    c[agent]["unresolved_blocker_age"]["reason"]
    finally:
        await F.done(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# §4 MEMORY USEFULNESS BESIDE THE CARD
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_memory_usefulness_is_summarised_and_grants_nothing():
    from sportsassets.agents import lesson_usage as LU
    from tests.test_lesson_usage import INV, _lesson
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        a = await F.account(conn, "scdm")
        lid = await _lesson(conn, a, at=NOW - 3 * H)
        await F.decision(conn, a, at=NOW - H, strategy=INV)
        got = await LU.retrieve(conn, account_id=a["account_id"], now=NOW)
        assert got["retrievals"] >= 1, got
        card = (await S.scorecards(conn, now=NOW, agents=("DEREK",)))[
            "agents"][0]
        mem = card["memory_usefulness"]
        assert mem["status"] == S.MEASURED
        assert mem["decisions_with_lessons"] == 1
        assert mem["lessons_retrieved"] == 1
        assert mem["authority"].startswith("NONE")
        assert lid
        x = (await S.scorecards(conn, now=NOW, agents=("XAVIER",)))[
            "agents"][0]["memory_usefulness"]
        assert x == {"status": S.UNAVAILABLE,
                     "reason": "NO_LESSON_RETRIEVED_IN_WINDOW"}
    finally:
        await F.done(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# §5 CONSTANTS PINNED TO THEIR SOURCES
# ═════════════════════════════════════════════════════════════════════

def test_the_constants_are_their_sources():
    from sportsassets import xavier_freshness as XF
    from sportsassets.agents import agent_work as AW
    from sportsassets.agents import eddie as E
    from sportsassets.agents import karen as K
    from sportsassets.agents import karen_runner as KR
    from sportsassets.twin import runner as TR
    from sportsassets.workers import ext_pinnacle_loop as L
    # the 30 s probability rule is READ, never a scorecard's own number
    assert S.FRESHNESS_RULE_S == L.PINNACLE_MAX_AGE_S == 30.0
    assert S.EDDIE_MAX_BOOK_AGE_S == E.MAX_BOOK_AGE_S
    assert S.TWIN_WINDOW_DAYS == TR.WINDOW_DAYS
    assert S.E_FRESH == XF.E_FRESH and S.E_NONE == XF.E_NONE
    assert tuple(S.NON_ACTIONS) == tuple(XF.NON_ACTIONS)
    assert set(S.EXECUTE_RECS) | set(S.HOLD_BACK_RECS) == set(
        E.RECOMMENDATIONS)
    assert not set(S.EXECUTE_RECS) & set(S.HOLD_BACK_RECS)
    assert set(S.CHALLENGE_TARGETS) == set(K.TARGETS)
    assert set(S.EVALUATORS) == set(K.EVALUATOR_FOR.values())
    assert S.K_RESEARCH == AW.K_RESEARCH
    assert S.DEREK_APPROVAL_RULE in KR.RULES
    assert S.XAVIER_APPROVAL_RULE in KR.RULES
    assert set(S.AGENTS) == set(R.AGENTS) | {"CHIEF_ALLOCATOR"}
    twin_src = (ROOT / "twin" / "scorecards.py").read_text()
    for _a, (t_agent, names) in S.TWIN_VALUE.items():
        for n in names:
            base = n.replace("_paper_basis", "")
            assert ('"%s", "%s' % (t_agent, base)) in twin_src or (
                '"%s"' % base) in twin_src, n


# ═════════════════════════════════════════════════════════════════════
# §6 PURE
# ═════════════════════════════════════════════════════════════════════

def test_the_status_rules():
    w = S.window(NOW, 86400.0)
    assert w == {"start": NOW - 86400.0, "end": NOW, "seconds": 86400.0,
                 "basis": "TRAILING"}
    m = S.metric("DEREK", "calibration", win=w, definition="d", source=[],
                 value=None, n=0, reason="NO_SETTLED_ENTRY")
    assert (m["status"], m["value"], m["reason"]) == (
        S.UNAVAILABLE, None, "NO_SETTLED_ENTRY")
    m = S.metric("DEREK", "calibration", win=w, definition="d", source=[],
                 value=None)
    assert m["status"] == S.UNAVAILABLE and m["reason"] == "NOT_MEASURED"
    m = S.metric("DEREK", "calibration", win=w, definition="d", source=[],
                 value=0.2, n=5)
    assert m["status"] == S.SMALL and "n=5 < 30" in m["reason"]
    m = S.metric("DEREK", "calibration", win=w, definition="d", source=[],
                 value=0.2, n=30)
    assert (m["status"], m["reason"]) == (S.MEASURED, None)
    # a measured zero stays a zero; it is not turned into UNAVAILABLE
    m = S.metric("DEREK", "false_refusal", win=w, definition="d", source=[],
                 value=0.0, n=40)
    assert (m["status"], m["value"]) == (S.MEASURED, 0.0)
    with pytest.raises(ValueError):
        S.metric("DEREK", "heartbeats", win=w, definition="d", source=[])
    with pytest.raises(ValueError):
        S.metric("DEREK", "reviews_count", win=w, definition="d", source=[])
    with pytest.raises(ValueError):
        S.metric("DEREK", "calibration", win=w, definition="d", source=[],
                 status=S.NOT_APPLICABLE)            # needs its reason
    na = S.na("KAREN", "calibration", w, "NO_PROBABILISTIC_CLAIM")
    assert (na["status"], na["value"]) == (S.NOT_APPLICABLE, None)
    for bad in (0, 31, "x", None):
        with pytest.raises(ValueError):
            S.window_seconds(bad)
    assert S.window_seconds(7) == 7 * 86400.0
    assert S.rate(0, 0) is None and S.rate(1, 4) == 0.25
    xs = [1.0, 2.0, 3.0, 4.0]
    assert S.percentile(xs, 0.5) == 2.5
    assert S.percentile(xs, 0.9) == pytest.approx(3.7)
    assert S.percentile([], 0.5) is None


def test_the_blocker_card():
    it = {"request_id": "r1", "owner": "EDDIE", "kind": "EXECUTION_ESTIMATE",
          "blocker": None, "enqueued_at": NOW - 500, "overdue": False}
    b = S.blocker_card("EDDIE", [it], NOW, {})
    assert b["status"] == S.NO_BLOCKER and b["value"] is None
    assert b["reason"] == "NO_OPEN_ITEM_IS_BLOCKED (1 open)"
    blocked = [dict(it, request_id="r2", blocker="X", overdue=True),
               dict(it, request_id="r3", blocker="Y",
                    enqueued_at=NOW - 900)]
    b = S.blocker_card("EDDIE", [it] + blocked, NOW, {"r2": NOW - 100})
    assert b["status"] == S.MEASURED and b["n"] == 2
    assert b["value"] == 900.0                 # r3, blocked since enqueue
    assert b["detail"]["oldest"]["request_id"] == "r3"
    assert b["detail"]["median_age_s"] == 500.0
    assert b["detail"]["overdue_items"] == 1
    assert b["detail"]["by_blocker"] == {"X": 1, "Y": 1}


def test_the_twin_card():
    row = {"agent": "DEREK", "metric": "accepted_opportunity_pnl",
           "book": "PAPER", "value": 2.5, "sample_n": 40, "status":
           "MEASURED", "reason": None, "basis": "b", "unit": "usd/position",
           "run_id": "twin-run-1", "computed_at": NOW}
    c = S.twin_card("DEREK", {("DEREK", "accepted_opportunity_pnl"): row},
                    why_absent=None)
    assert (c["status"], c["value"], c["n"]) == (S.MEASURED, 2.5, 40)
    assert c["window"]["end"] == NOW
    assert c["window"]["seconds"] == S.TWIN_WINDOW_DAYS * 86400.0
    c = S.twin_card("DEREK", {("DEREK", "accepted_opportunity_pnl"): dict(
        row, value=None, status="UNAVAILABLE",
        reason="NO_SETTLED_POSITION_IN_WINDOW")}, why_absent=None)
    assert c["status"] == S.UNAVAILABLE
    assert c["reason"] == "TWIN:NO_SETTLED_POSITION_IN_WINDOW"
    c = S.twin_card("DEREK", {("DEREK", "accepted_opportunity_pnl"): dict(
        row, status="INSUFFICIENT_SAMPLE", sample_n=4)}, why_absent=None)
    assert c["status"] == S.SMALL and c["value"] == 2.5
    c = S.twin_card("DEREK", {}, why_absent="TABLE_NOT_DEPLOYED:x")
    assert c["reason"] == "TABLE_NOT_DEPLOYED:x"
    # Karen: loss avoided minus profit sacrificed, both measured
    k = {("KAREN", "loss_avoided_paper_basis"): dict(
            row, agent="KAREN", metric="loss_avoided_paper_basis",
            value=10.0, sample_n=31),
         ("KAREN", "profit_sacrificed_paper_basis"): dict(
            row, agent="KAREN", metric="profit_sacrificed_paper_basis",
            value=4.0, sample_n=33)}
    c = S.twin_card("KAREN", k, why_absent=None)
    assert (c["value"], c["n"], c["status"]) == (6.0, 31, S.MEASURED)


# ═════════════════════════════════════════════════════════════════════
# §7 THE CITATION RESOLVER
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_citations_resolve_through_the_221_map(monkeypatch):
    conn, tx = await F.tx()
    try:
        a = await F.account(conn, "scdc")
        p = await F.position(conn, a, at=NOW - 5 * H)
        rid = await F.stale_hold_review(conn, a, group_id=p["group_id"],
                                        at=NOW - 4 * H)

        async def refs(cx):
            out = {x: [] for x in S.AGENTS}
            out["DEREK"] = [("paper_xavier_reviews", rid),
                            ("paper_xavier_reviews", "paperrev:missing"),
                            ("scout_feature_observations", "obs-1"),
                            ("karen_challenge_events", "not-a-number")]
            return out, {"test": 4}
        monkeypatch.setattr(S, "_citation_refs", refs)
        cx = S._Ctx(conn, NOW, 86400.0)
        got = await S.citations(cx)
        d = got["DEREK"]
        # 1 resolves, 2 do not (a missing review; a non-integer key of an
        # integer-keyed kind); the unmapped kind is UNVERIFIABLE
        assert (d["value"], d["n"]) == (round(1 / 3, 6), 3), d
        assert d["detail"]["unresolved"] == 2
        assert d["detail"]["unverifiable_kinds"] == {
            "scout_feature_observations": 1}
        assert {"kind": "paper_xavier_reviews", "id": "paperrev:missing"} \
            in d["detail"]["unresolved_examples"]
        assert got["XAVIER"]["reason"] == "NO_CITATION_IN_WINDOW"
    finally:
        await F.done(conn, tx)


# ═════════════════════════════════════════════════════════════════════
# §8 THE READ API
# ═════════════════════════════════════════════════════════════════════

def test_the_scorecard_route_is_get_only_and_requires_a_command_session():
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    from sportsassets.api import command_agent_scorecards as API
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "").startswith(API.PATH):
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert API.PATH in paths
    assert all(m <= {"GET", "HEAD"} for m in paths.values()), paths
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get(API.PATH).status_code == 401
    assert client.get(API.PATH + "?window_days=0").status_code in (401, 422)
    assert client.post(API.PATH).status_code in (401, 405)


@pg
async def test_the_scorecard_read_answers_inside_a_read_only_transaction():
    from sportsassets.api import command_agent_scorecards as API
    conn, tx = await F.tx()
    try:
        got = await API.read(conn, now=NOW, window_days=7)
        assert [c["agent"] for c in got["agents"]] == list(S.AGENTS)
        _cards(got)
        out = API._envelope(got)
        assert out["status"] == "OK"
        assert out["authority"] == "NONE_RECORDS_ONLY"
        assert "NEVER ACTIVITY" in out["disclosure"]
        one = await API.read(conn, now=NOW, window_days=3, agent="SCOUT")
        assert [c["agent"] for c in one["agents"]] == ["SCOUT"]
        assert one["window"]["seconds"] == 3 * 86400.0
        with pytest.raises(ValueError):
            await API.read(conn, now=NOW, agent="NOBODY")
        assert conn.is_in_transaction()
    finally:
        await F.done(conn, tx)
