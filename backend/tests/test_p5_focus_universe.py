"""P5 FOCUS UNIVERSE: WHAT THE INSTITUTIONAL STREAM, ITS RECORDER AND THE
SAME-BOOK PROBE HOLD, IN PRIORITY ORDER, EXACTLY IDENTIFIED OR UNAVAILABLE.

  §1  tier ordering (pure): actual > intent > paper investment > V3 candidate
      > mapped universe > exploration (diagnostic) > discovery; one member
      per slug at its highest tier, every reason kept; the bound, with what
      it dropped counted per tier
  §2  tier ordering from the database: every reader's SQL runs against the
      migrated schema and puts each seeded contract in its tier
  §3  exploration is never live-eligible: diagnostic only, below every
      investment tier, nothing granted; the allowlist is untouched; the
      table CHECKs grants_live_eligibility false and orders_placed 0
  §4  exact identity only: no record / not listed / bad slug / keys that are
      not one contract / a near-miss slug -> UNAVAILABLE with the reason; an
      UNAVAILABLE member is never subscribed and its probe reads nothing
  §5  every probe observation persists the agreement fields (migration 213)
  §6  S1 counts only comparable, exactly mapped samples; the endpoint's
      sample summary, histogram and current comparable contracts
  §7  the refdata bootstrap is bounded, paced, allow-listed and re-asks an
      unlisted slug only after its retry bound
  §8  the endpoint fields: unmeasured is null with a reason, never 0
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

import asyncpg
import pytest

from sportsassets import execmirror as M
from sportsassets import institutional_api_stream as IAS
from sportsassets import institutional_focus_universe as FU
from sportsassets import institutional_same_book as SB
from sportsassets import institutional_stream as IS
from sportsassets import p5_runtime as P5R
from sportsassets.agents import paper_benchmark as PB
from sportsassets.workers import institutional_md as WMD

from tests import paper_harness as H
from tests.test_institutional_contract_map import AEC, SLUG
from tests.test_institutional_same_book import (books_with, fake_retail,
                                                retail_md)

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


@pytest.fixture(autouse=True)
def _reset():
    IS.reset()
    IAS.reset()
    yield
    IS.reset()
    IAS.reset()


def c(slug, **kw):
    return dict({"slug": slug, "why": "w:%s" % slug}, **kw)


# ── §0 the bound and the strategy names are the system's own ─────────────

def test_the_bound_is_the_stream_bound_and_the_names_are_the_strategys():
    assert FU.MAX_MEMBERS == IAS.MAX_SYMBOLS
    assert FU.MAX_MEMBERS <= IS.MAX_SYMBOLS
    assert FU.INVESTMENT_STRATEGY == PB.CG_STRATEGY
    assert FU.INVESTMENT_VERSION == PB.CG_VERSION
    assert FU.TIERS == ("ACTUAL_OPEN_POSITION", "EXECUTION_INTENT",
                        "PAPER_INVESTMENT_POSITION", "V3_CANDIDATE",
                        "MAPPED_INVESTMENT_UNIVERSE", "EXPLORATION_DIAGNOSTIC",
                        "BROADER_DISCOVERY")


# ── §1 tier ordering (pure) ──────────────────────────────────────────────

def test_tier_ordering_one_member_per_slug_at_its_highest_tier():
    cands = {
        FU.T_DISCOVERY: [c("disc-a-2026")],
        FU.T_UNIVERSE: [c("uni-a-2026"), c("cand-a-2026")],
        FU.T_EXPLORATION: [c("expl-a-2026", strategy="PINNACLE_EXPLORATION_"
                             "PAPER", diagnostic_only=True)],
        FU.T_CANDIDATE: [c("cand-a-2026"), c("intent-a-2026")],
        FU.T_PAPER: [c("paper-a-2026", strategy=FU.INVESTMENT_STRATEGY)],
        FU.T_INTENT: [c("intent-a-2026"), c("intent-b-2026")],
        FU.T_ACTUAL: [c("actual-a-2026", side="LONG")],
    }
    u = FU.prioritize(cands, discovery=["disc-b-2026", "actual-a-2026"])
    order = [(m["retail_slug"], m["tier"]) for m in u["members"]]
    assert order == [
        ("actual-a-2026", FU.T_ACTUAL),
        ("intent-a-2026", FU.T_INTENT), ("intent-b-2026", FU.T_INTENT),
        ("paper-a-2026", FU.T_PAPER),
        ("cand-a-2026", FU.T_CANDIDATE),
        ("uni-a-2026", FU.T_UNIVERSE),
        ("expl-a-2026", FU.T_EXPLORATION),
        ("disc-a-2026", FU.T_DISCOVERY), ("disc-b-2026", FU.T_DISCOVERY)]
    ranks = [m["tier_rank"] for m in u["members"]]
    assert ranks == sorted(ranks)
    assert [m["rank"] for m in u["members"]] == list(range(1, 10))
    by = FU.by_slug(u)
    # every tier a slug qualified for is kept, highest first
    assert [r["tier"] for r in by["intent-a-2026"]["reasons"]] == [
        FU.T_INTENT, FU.T_CANDIDATE]
    assert [r["tier"] for r in by["actual-a-2026"]["reasons"]] == [
        FU.T_ACTUAL, FU.T_DISCOVERY]
    assert u["per_tier"] == {FU.T_ACTUAL: 1, FU.T_INTENT: 2, FU.T_PAPER: 1,
                             FU.T_CANDIDATE: 1, FU.T_UNIVERSE: 1,
                             FU.T_EXPLORATION: 1, FU.T_DISCOVERY: 2}
    assert all(m["live_eligibility_effect"] == "NONE" for m in u["members"])


def test_the_bound_keeps_the_highest_tiers_and_counts_what_it_dropped():
    cands = {FU.T_ACTUAL: [c("act-%02d-x" % i) for i in range(20)],
             FU.T_CANDIDATE: [c("cand-%02d-x" % i) for i in range(20)],
             FU.T_EXPLORATION: [c("expl-%02d-x" % i) for i in range(5)]}
    u = FU.prioritize(cands, discovery=["disc-0-x"])
    assert len(u["members"]) == FU.MAX_MEMBERS == u["bound"]
    assert u["per_tier"][FU.T_ACTUAL] == 20
    assert u["per_tier"][FU.T_CANDIDATE] == 12
    assert u["dropped_beyond_bound"] == {FU.T_CANDIDATE: 8,
                                         FU.T_EXPLORATION: 5,
                                         FU.T_DISCOVERY: 1}
    assert u["candidates_total"] == 46
    # a caller can ask for less, never for more than the stream bound
    assert len(FU.prioritize(cands, limit=5)["members"]) == 5
    assert FU.prioritize(cands, limit=999)["bound"] == FU.MAX_MEMBERS


# ── §2 tier ordering from the database ───────────────────────────────────

async def _seed(conn, tag):
    """One contract per tier (plus an exploration position and a refused
    intent), inside the caller's rolled-back transaction."""
    s = {k: "fu-%s-%s-2026-10-04" % (k, tag) for k in (
        "actual", "intent", "intentref", "paper", "expl", "cand", "uni",
        "paperonly")}
    acct = await H.new_account(conn, "fu")
    a, sess = acct["account_id"], acct["session_id"]
    await conn.execute(
        "INSERT INTO smalllive_handoffs (handoff_id, venue, group_id, "
        " us_market_slug, entry_mirror_id, opened_intent, live_held, "
        " live_bought, first_live_fill_at) VALUES ($1,'POLYMARKET',$2,$3,"
        " $4,'ORDER_INTENT_BUY_LONG',1,1,now())",
        "h-" + tag, "g-" + tag, s["actual"], "ei:x-" + tag)

    async def intent(slug, eligible, state, refusal, strategy=PB.CG_STRATEGY):
        await conn.execute(
            "INSERT INTO execution_intents (intent_id, decision_id, strategy,"
            " us_market_slug, order_intent, group_id, order_type, "
            " time_in_force, paper_target_qty, wire_price, decided_at, "
            " live_eligible, actual_state, actual_refusal, live_eligibility)"
            " VALUES ($1,$2,$3,$4,'ORDER_INTENT_BUY_LONG','g','MARKETABLE',"
            " 'IOC',100,0.5,now(),$5,$6,$7, CASE WHEN $5 THEN "
            " '{\"admission\": {\"verdict\": \"LIVE_ADMISSIBLE\"}}'::jsonb "
            " ELSE '{}'::jsonb END)",
            "ei_" + uuid.uuid4().hex[:20], "paperdec:" + uuid.uuid4().hex,
            strategy, slug, eligible, state, refusal)
    await intent(s["intent"], True, "LANE_NOT_RUNNING", None)
    await intent(s["intentref"], False, "REFUSED", "BOOK_CURRENCY")
    # a PAPER_ONLY intent (an unallowlisted strategy) is never tier 2
    await intent(s["paperonly"], False, "PAPER_ONLY",
                 "STRATEGY_NOT_LIVE_ELIGIBLE",
                 strategy="PINNACLE_EXPLORATION_PAPER")

    async def fill(slug, strategy):
        oid = "paperord:" + uuid.uuid4().hex
        gid = "paper_group_" + uuid.uuid4().hex[:12]
        await conn.execute(
            "INSERT INTO paper_orders (order_id, idempotency_key, account_id,"
            " session_id, group_id, role, direction, holding_side, intent, "
            " us_market_slug, order_type, time_in_force, allow_partial, qty,"
            " limit_price, wire_price, state, decided_at, eligible_at, "
            " expires_at, simulator_version, strategy) VALUES ($1,$1,$2,$3,"
            " $4,'ENTRY','BUY','LONG','ORDER_INTENT_BUY_LONG',$5,'MARKETABLE',"
            " 'IOC',true,10,0.5,0.5,'FILLED',now(),now(),now(),'t',$6)",
            oid, a, sess, gid, slug, strategy)
        await conn.execute(
            "INSERT INTO paper_fills (fill_id, idempotency_key, order_id, "
            " account_id, session_id, group_id, role, direction, "
            " holding_side, us_market_slug, qty, price, wire_price, fee_usd,"
            " gross_usd, filled_at, basis, simulator_version, strategy) "
            " VALUES ($1,$1,$2,$3,$4,$5,'ENTRY','BUY','LONG',$6,10,0.5,0.5,0,"
            " 5,now(),'DEPTH_WALK_WITHIN_LIMIT','t',$7)",
            "paperfill:" + uuid.uuid4().hex, oid, a, sess, gid, slug,
            strategy)
    await fill(s["paper"], PB.CG_STRATEGY)
    await fill(s["expl"], "PINNACLE_EXPLORATION_PAPER")

    async def decision(slug, verdict):
        await conn.execute(
            "INSERT INTO paper_decisions (decision_id, session_id, "
            " account_id, decided_at, us_market_slug, intent, verdict, "
            " refusal, internal_model, pinnacle, qualification_gaps, "
            " policy_version, simulator_version, strategy) VALUES ($1,$2,$3,"
            " now(),$4,'ORDER_INTENT_BUY_LONG',$5,$6,'{}','{}','[]',$7,'t',$8)",
            "paperdec:" + uuid.uuid4().hex, sess, a, slug, verdict,
            None if verdict == "ENTER" else "EDGE_BELOW_THRESHOLD",
            PB.CG_VERSION, PB.CG_STRATEGY)
    await decision(s["cand"], "REFUSE")
    sport = "fu-sport-" + tag
    for slug in (s["cand"], s["uni"]):
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, market_slug, "
            " side_norm, kind, sports_type, game_start) VALUES ($1,$2,$3,"
            " 'yes','moneyline',$4, now() + interval '1 hour')",
            "id-" + slug, "ev-" + tag, slug, sport)
    return s


async def _isolate_the_candidate_tier(conn) -> None:
    """THE CANDIDATE TIER IS READ FROM A SHARED TEST DATABASE (R30A).

    `FU._candidates` lists at most 2 x MAX_MEMBERS (64) distinct slugs of
    recent V3 decisions, ENTER first. Other proofs in the same database
    (the paper-pass proofs of every policy, the NFL ones among them) leave
    their own V3 ENTER decisions inside the one-hour window, and once 64
    such slugs exist this proof's seeded REFUSE candidate is ranked past
    the cap and the lookup fails with a KeyError -- a count of OTHER proofs'
    residue, not a tier-ordering defect (reproduced by preloading 70 ENTER
    slugs: the same KeyError at the base commit). Those rows are removed
    INSIDE this proof's transaction, which is rolled back, so nothing is
    deleted for good and every assertion below is unchanged; the append-only
    trigger is bypassed for that one statement only."""
    await conn.execute("SET LOCAL session_replication_role = replica")
    await conn.execute(
        "DELETE FROM paper_decisions WHERE strategy = $1 AND "
        " policy_version = $2 AND decided_at > now() - "
        " make_interval(secs => $3)", FU.INVESTMENT_STRATEGY,
        FU.INVESTMENT_VERSION, FU.CANDIDATE_WINDOW_S * 2)
    await conn.execute("SET LOCAL session_replication_role = origin")


@pg
async def test_every_reader_puts_each_seeded_contract_in_its_tier():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        tag = uuid.uuid4().hex[:8]
        await _isolate_the_candidate_tier(conn)
        s = await _seed(conn, tag)
        g = await FU.gather(conn)
        assert all(v["status"] == "MEASURED" for v in g["status"].values()), \
            g["status"]
        where = {}
        for tier in FU.TIERS:
            for i, cand in enumerate(g["candidates"][tier]):
                where.setdefault(cand["slug"], (tier, i))
        assert where[s["actual"]][0] == FU.T_ACTUAL
        assert where[s["intent"]][0] == FU.T_INTENT
        assert where[s["intentref"]][0] == FU.T_INTENT
        # live-admissible before imminent (refused at admission)
        assert where[s["intent"]][1] < where[s["intentref"]][1]
        assert where[s["paper"]][0] == FU.T_PAPER
        assert where[s["expl"]][0] == FU.T_EXPLORATION
        assert where[s["cand"]][0] == FU.T_CANDIDATE
        assert where[s["uni"]][0] == FU.T_UNIVERSE
        assert s["paperonly"] not in where          # PAPER_ONLY: not tier 2
        u = FU.prioritize(g["candidates"])
        mine = [m for m in u["members"] if m["retail_slug"] in s.values()]
        assert [m["tier_rank"] for m in mine] == sorted(
            m["tier_rank"] for m in mine)
        cand = FU.by_slug(u)[s["cand"]]
        # the candidate is also in the mapped universe: kept at tier 4
        assert [r["tier"] for r in cand["reasons"]] == [FU.T_CANDIDATE,
                                                        FU.T_UNIVERSE]
        assert FU.by_slug(u)[s["expl"]]["diagnostic_only"] is True
    finally:
        await tx.rollback()
        await conn.close()


class _NoTables:
    async def fetchval(self, sql, *a):
        return False

    async def fetch(self, *a):
        raise AssertionError("no table, no read")


def test_absent_tables_make_a_tier_unmeasured_never_guessed():
    g = asyncio.run(FU.gather(_NoTables()))
    assert set(g["status"]) == set(FU.TIER_TABLES)
    assert all(v["status"] == "UNMEASURED" and "absent" in v["why"]
               for v in g["status"].values())
    assert all(v == [] for v in g["candidates"].values())


# ── §3 exploration is never live-eligible ────────────────────────────────

def test_exploration_is_diagnostic_only_and_nothing_is_granted():
    u = FU.prioritize({FU.T_EXPLORATION: [c("expl-x-2026",
                                            strategy="PINNACLE_EXPLORATION_"
                                            "PAPER")],
                       FU.T_PAPER: [c("inv-x-2026",
                                      strategy=FU.INVESTMENT_STRATEGY)]})
    expl = FU.by_slug(u)["expl-x-2026"]
    assert expl["diagnostic_only"] is True
    assert expl["tier_rank"] > max(FU.TIER_RANK[t] for t in (
        FU.T_ACTUAL, FU.T_INTENT, FU.T_PAPER, FU.T_CANDIDATE, FU.T_UNIVERSE))
    assert expl["live_eligibility_effect"] == "NONE"
    # the allowlist is exactly what it was, and exploration is not in it
    assert M.LIVE_ELIGIBLE == {PB.CG_STRATEGY: (
        "PINNACLE_COMPLETED_GAME_PAPER_V2", "PINNACLE_COMPLETED_GAME_PAPER_V3")}
    ok, ev = M.live_eligibility({"strategy": "PINNACLE_EXPLORATION_PAPER",
                                 "decision_policy_version":
                                     PB.EXPLORE_VERSION, "role": "ENTRY"})
    assert ok is False and ev["class"] == \
        "EXPLORATION_RESEARCH_COST_PAPER_ONLY"
    # the module never names the allowlist or an eligibility setter
    import inspect
    src = inspect.getsource(FU)
    for name in ("LIVE_ELIGIBLE", "live_eligibility(", "live_eligible =",
                 "APPROVED_LIVE_BOOK_RULES"):
        assert name not in src, name


@pg
async def test_the_universe_table_refuses_a_grant_an_order_or_an_odd_tier():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    ins = ("INSERT INTO institutional_focus_universe (computed_at, "
           " process_id, service, version, universe_id, bound, rank, tier, "
           " tier_rank, why, retail_slug, identity_status, unavailable_reason,"
           " institutional_symbol, diagnostic_only, grants_live_eligibility,"
           " orders_placed) VALUES (now(),'p','s','v',$1,32,1,$2,$3,'w',$4,"
           " $5,$6,$7,$8,$9,$10)")
    try:
        await conn.execute(ins, "u0", FU.T_EXPLORATION, 6, "x-ok", "EXACT",
                           None, "x-ok", True, False, 0)
        bad = [
            ("u1", FU.T_EXPLORATION, 6, "x1", "EXACT", None, "x1", True,
             True, 0),                                     # a grant
            ("u2", FU.T_EXPLORATION, 6, "x2", "EXACT", None, "x2", True,
             False, 1),                                    # an order
            ("u3", FU.T_EXPLORATION, 6, "x3", "EXACT", None, "x3", False,
             False, 0),                                    # not diagnostic
            ("u4", FU.T_ACTUAL, 2, "x4", "EXACT", None, "x4", False, False,
             0),                                           # tier / rank
            ("u5", FU.T_ACTUAL, 1, "x5", "EXACT", None, "other", False,
             False, 0),                                    # symbol <> slug
            ("u6", FU.T_ACTUAL, 1, "x6", "UNAVAILABLE", None, None, False,
             False, 0),                                    # no reason
            ("u7", FU.T_ACTUAL, 1, "x7", "FUZZY", None, None, False, False,
             0),                                           # unknown status
        ]
        for args in bad:
            with pytest.raises(asyncpg.CheckViolationError):
                async with conn.transaction():
                    await conn.execute(ins, *args)
    finally:
        await tx.rollback()
        await conn.close()


# ── §4 exact identity only ───────────────────────────────────────────────

def _m(slug=SLUG, side="LONG"):
    return {"retail_slug": slug, "side": side, "bettor_event": "mlb:sd-mil",
            "refs": {}}


def test_identity_is_exact_or_unavailable_with_the_reason():
    ok = FU.identify(_m(), AEC)
    assert ok["status"] == FU.EXACT and ok["reason"] is None
    assert ok["institutional_symbol"] == SLUG
    assert ok["institutional_side"] == "LONG"
    assert ok["institutional_event_id"] == "mlb-sd-mil-2026-10-03"
    assert ok["market_type"] == "moneyline"
    assert ok["settlement"]["binary_yes"] is True
    assert ok["settlement"]["cftc_instrument_id"] == SLUG
    assert ok["bettor_event"] == "mlb:sd-mil"
    assert ok["member_side_maps_exactly"] is True
    # no record: not read yet / read and not listed
    assert FU.identify(_m(), None, attempted=False)["reason"] == FU.U_NOT_READ
    assert FU.identify(_m(), None)["reason"] == FU.U_NOT_LISTED
    # a slug that is not a venue slug is never sent anywhere
    assert FU.identify(_m("../orders"), AEC)["reason"] == FU.U_SLUG
    # a near-miss slug (one character) against the record: NOT matched
    near = FU.identify(_m(SLUG.replace("2026-10-03", "2026-10-04")), AEC)
    assert near["status"] == FU.UNAVAILABLE
    assert near["reason"] == "REGISTERED_ID_SYMBOL_AND_SLUG_ARE_NOT_ONE_" \
                             "CONTRACT"
    assert near["institutional_symbol"] is None
    # a record whose own keys disagree
    rec = json.loads(json.dumps(AEC))
    rec["metadata"]["cftc_instrument_id"] = "aec-mlb-nyy-bos-2026-10-03"
    assert FU.identify(_m(), rec)["reason"] == \
        "REGISTERED_ID_SYMBOL_AND_SLUG_ARE_NOT_ONE_CONTRACT"
    # the side is not established -> refused by name
    rec = json.loads(json.dumps(AEC))
    rec["metadata"]["long_participant_id"] = "mlb-mil"
    assert FU.identify(_m(), rec)["reason"] == \
        "LONG_SIDE_IS_NOT_ESTABLISHED_AS_THE_EVENTS_FIRST_PARTICIPANT"
    # a SHORT member: the instrument's book is exact; its own side is the
    # other side of that book and is recorded as such (never priced here)
    short = FU.identify(_m(side="SHORT"), AEC)
    assert short["status"] == FU.EXACT
    assert short["member_side_maps_exactly"] is False
    assert short["member_side_refusal"] == \
        "RETAIL_NO_LEG_IS_THE_SHORT_SIDE_OF_THE_LONG_INSTRUMENT"


def test_only_exact_members_are_subscribed_and_unavailable_ones_read_nothing():
    other = "aec-nfl-x-y-2026-10-04"
    u = FU.prioritize({FU.T_ACTUAL: [c(SLUG)], FU.T_INTENT: [c(other)]})

    class Store:
        def instrument(self, s):
            return {"record": AEC} if s == SLUG else None
    FU.attach(u, record_for=lambda s: Store().instrument(s) and AEC)
    assert FU.exact_symbols(u) == [SLUG]
    assert FU.by_slug(u)[other]["unavailable_reason"] == FU.U_NOT_LISTED
    IS.BOOKS.set_state(IS.S_IDLE, "test")
    assert WMD.subscribe_universe(Store(), u) == [SLUG]
    assert IS.BOOKS.wanted() == [SLUG]           # the UNAVAILABLE one: never


# ── §5 every probe observation persists the agreement fields ────────────

class _Pool:
    def __init__(self, conn):
        self.conn = conn

    async def execute(self, sql, *a):
        return await self.conn.execute(sql, *a)

    async def fetch(self, sql, *a):
        return await self.conn.fetch(sql, *a)

    async def fetchval(self, sql, *a):
        return await self.conn.fetchval(sql, *a)


@pg
async def test_probe_rows_persist_every_agreement_field_and_the_tier():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        other = "aec-nfl-x-y-2026-10-04"
        b = books_with()
        u = FU.prioritize({FU.T_INTENT: [c(SLUG, event="mlb:sd-mil")],
                           FU.T_DISCOVERY: [c(other)]})

        class Store:
            def instrument(self, s):
                return {"record": AEC} if s == SLUG else None
        FU.attach(u, record_for=lambda s: (Store().instrument(s) or {}).get(
            "record"))
        focus = {m["retail_slug"]: dict(m, universe_id=u["universe_id"])
                 for m in u["members"]}
        calls = []
        pid = "institutional_md:test:fu:" + uuid.uuid4().hex[:6]
        out = await WMD.probe_same_book(
            _Pool(conn), Store(), [m["retail_slug"] for m in u["members"]],
            process_id=pid, current=b.current, focus=focus,
            retail_read=fake_retail(retail_md(), calls=calls))
        assert out["written"] == 2 and calls == [SLUG]
        rows = {r["symbol"]: dict(r) for r in await conn.fetch(
            "SELECT * FROM institutional_same_book_probe WHERE "
            "process_id = $1", pid)}
        a = rows[SLUG]
        assert a["verdict"] == SB.V_AGREE and a["agreed"] is True
        assert a["institutional_symbol"] == SLUG and a["retail_slug"] == SLUG
        assert a["identity_exact"] is True and a["outcome_side"] == "YES"
        assert a["bettor_event"] == "mlb:sd-mil"
        assert str(a["inst_best_bid"]) == "0.450000"
        assert str(a["inst_best_ask"]) == "0.470000"
        assert str(a["retail_best_bid"]) == "0.450000"
        assert str(a["retail_best_ask"]) == "0.470000"
        assert a["connection_epoch"] == 1 and a["gap_state"] == "NO_GAP"
        assert 0 <= a["inst_receipt_age_s"] < 5
        assert 0 <= a["retail_receipt_age_s"] < 5
        assert a["compared_at"] is not None
        assert a["incomparable_reason"] is None
        assert a["focus_tier"] == FU.T_INTENT and a["focus_tier_rank"] == 2
        assert a["focus_universe_id"] == u["universe_id"]
        assert a["orders_placed"] == 0
        n = rows[other]
        assert n["verdict"] == SB.V_NC and n["agreed"] is None
        assert n["incomparable_reason"] == SB.NC_IDENTITY
        assert n["identity_exact"] is False
        assert n["institutional_symbol"] is None
        assert n["focus_tier"] == FU.T_DISCOVERY
        # the CHECKs: no agreement for a non-comparable row, no comparable
        # verdict on a non-exact identity, no orders
        for sql in (
                "INSERT INTO institutional_same_book_probe (process_id, "
                "service, symbol, retail_slug, identity_ok, verdict, agreed)"
                " VALUES ('p','s','x','x',false,'NOT_COMPARABLE',true)",
                "INSERT INTO institutional_same_book_probe (process_id, "
                "service, symbol, retail_slug, identity_ok, verdict, "
                "identity_exact) VALUES ('p','s','x','x',false,'AGREE_TOP_N',"
                "false)",
                "INSERT INTO institutional_same_book_probe (process_id, "
                "service, symbol, retail_slug, identity_ok, verdict, "
                "incomparable_reason) VALUES ('p','s','x','x',true,"
                "'AGREE_TOP_N','X')",
                "INSERT INTO institutional_same_book_probe (process_id, "
                "service, symbol, retail_slug, identity_ok, verdict, "
                "orders_placed) VALUES ('p','s','x','x',true,'AGREE_TOP_N',"
                "1)"):
            with pytest.raises(asyncpg.CheckViolationError):
                async with conn.transaction():
                    await conn.execute(sql)
        # and the universe snapshot itself
        assert await FU.persist(_Pool(conn), u, process_id=pid,
                                service="test",
                                wanted=[SLUG]) == 2
        snap = {r["retail_slug"]: dict(r) for r in await conn.fetch(
            "SELECT * FROM institutional_focus_universe WHERE "
            "universe_id = $1", u["universe_id"])}
        assert snap[SLUG]["identity_status"] == FU.EXACT
        assert snap[SLUG]["stream_wanted"] is True
        assert snap[other]["unavailable_reason"] == FU.U_NOT_LISTED
        assert snap[other]["stream_wanted"] is False
        assert all(r["grants_live_eligibility"] is False
                   and r["orders_placed"] == 0 for r in snap.values())
    finally:
        await tx.rollback()
        await conn.close()


def test_a_gapped_stream_book_is_recorded_with_its_gap_state():
    b = books_with()
    b.on_disconnected("test")
    r = SB.sample(SLUG, record=AEC, books_current=b.current,
                  retail_read=fake_retail(retail_md()))
    assert r["verdict"] == SB.V_NC
    assert r["incomparable_reason"] == SB.NC_STREAM
    assert r["gap_state"] == IS.R_GAP_CONNECTION
    assert r["agreed"] is None and r["identity_exact"] is True


# ── §6 S1 counts only comparable, exactly mapped samples ─────────────────

def _row(symbol, verdict, *, exact=True, reason=None):
    return {"probed_at": None, "version": SB.VERSION, "symbol": symbol,
            "retail_slug": symbol, "identity_ok": exact,
            "identity": {"ok": exact, "institutional_symbol":
                         symbol if exact else None},
            "verdict": verdict, "verdict_reason": reason,
            "stream_changed_in_window": False,
            "identity_exact": None if not exact else True,
            "agreed": None if verdict == SB.V_NC else
            verdict != SB.V_DISAGREE,
            "incomparable_reason": reason if verdict == SB.V_NC else None}


@pg
async def test_s1_counts_only_comparable_exactly_mapped_samples():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("DELETE FROM institutional_same_book_probe")
        n = P5R.SAME_BOOK_MIN_COMPARABLE
        rows = [_row("aec-mlb-a-b-2026-10-04", SB.V_AGREE)
                for _ in range(n - 1)]
        # comparable verdicts WITHOUT an exact identity never count, however
        # many there are (the probe never writes one; the count does not
        # depend on that)
        rows += [_row("f1-prop-x-2026-10-04", SB.V_AGREE, exact=False)
                 for _ in range(10)]
        rows += [_row("f1-prop-y-2026-10-04", SB.V_NC,
                      reason=SB.NC_IDENTITY, exact=False)
                 for _ in range(7)]
        rows += [_row("aec-mlb-a-b-2026-10-04", SB.V_NC,
                      reason=SB.NC_STREAM) for _ in range(3)]
        assert await SB.persist(_Pool(conn), rows, process_id="p",
                                service="s") == len(rows)
        sb = await P5R.same_book_evidence(conn)
        assert sb["totals"]["comparable"] == n - 1
        assert sb["status"] == "INCONCLUSIVE"         # 29 < 30: not proven
        assert sb["totals"]["not_comparable"] == 20
        s = await P5R.same_book_samples(conn)
        assert s["status"] == "MEASURED"
        assert s["sample_count"] == len(rows)
        assert s["comparable_count"] == n - 1
        assert s["agreement_pct"] == 100.0
        assert s["comparable_verdicts_without_exact_identity"] == 10
        assert s["incomparable_reasons"] == {
            P5R.NC_NOT_EXACT: 10, SB.NC_IDENTITY: 7, SB.NC_STREAM: 3}
        assert [x["symbol"] for x in s["current_comparable_contracts"]] == [
            "aec-mlb-a-b-2026-10-04"]
        assert s["required"] == {"min_comparable": 30, "min_agree_pct": 95.0}
        # one more exact agreement reaches the rule's own threshold
        await SB.persist(_Pool(conn), [_row("aec-mlb-a-b-2026-10-04",
                                            SB.V_AGREE)],
                         process_id="p", service="s")
        assert (await P5R.same_book_evidence(conn))["status"] == "SUPPORTED"
        # the thresholds themselves are untouched
        assert (P5R.SAME_BOOK_MIN_COMPARABLE, P5R.SAME_BOOK_MIN_AGREE_RATE) \
            == (30, 0.95)
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_with_no_comparable_sample_the_agreement_is_null_with_a_reason():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("DELETE FROM institutional_same_book_probe")
        s = await P5R.same_book_samples(conn)
        assert s["sample_count"] == 0 and s["comparable_count"] == 0
        assert s["agreement_pct"] is None
        assert s["agreement_pct_why"] == \
            "NO_COMPARABLE_EXACTLY_MAPPED_SAMPLES_IN_WINDOW"
    finally:
        await tx.rollback()
        await conn.close()


# ── §7 the refdata bootstrap ─────────────────────────────────────────────

class _Client:
    def __init__(self, listed=()):
        self.listed = set(listed)
        self.calls = []

    def read(self, path, symbol):
        self.calls.append((path, symbol))
        rec = dict(AEC, symbol=symbol) if symbol in self.listed else None
        return {"status": 200, "ms": 1,
                "body": {"instruments": [rec] if rec else []}}


class _Store:
    def __init__(self):
        self.d = {}

    def instrument(self, s):
        return self.d.get(s)

    def put_instrument(self, s, record, *, price_scale, qty_scale):
        self.d[s] = {"record": record, "priceable": bool(price_scale)}


def test_bootstrap_is_bounded_allow_listed_and_retries_unlisted_late(
        monkeypatch):
    import sportsassets.venue_pace as VP
    monkeypatch.setattr(VP, "pace", lambda s: None)
    cl, st, att = _Client(listed=[SLUG]), _Store(), {}
    slugs = [SLUG, "aec-nfl-x-y-2026-10-04", "../orders", "aec-c-d-2026"]
    out = WMD.bootstrap_universe(cl, st, slugs, att, now=1000.0)
    assert out == {"read": 2, "listed": 1, "notListed": 1}
    assert cl.calls == [("instruments", SLUG),
                        ("instruments", "aec-nfl-x-y-2026-10-04")]
    WMD.bootstrap_universe(cl, st, slugs, att, now=1001.0)
    assert cl.calls[-1] == ("instruments", "aec-c-d-2026")
    assert all(p == "instruments" for p, _ in cl.calls)
    assert "../orders" not in [s for _, s in cl.calls]
    n = len(cl.calls)
    # nothing is due again until its bound passes
    WMD.bootstrap_universe(cl, st, slugs, att, now=1100.0)
    assert len(cl.calls) == n
    WMD.bootstrap_universe(cl, st, slugs, att,
                           now=1001.0 + WMD.UNIVERSE_RETRY_UNLISTED_S)
    assert [s for _, s in cl.calls[n:]] == ["aec-nfl-x-y-2026-10-04",
                                            "aec-c-d-2026"]
    assert WMD.UNIVERSE_BOOTSTRAPS_PER_SWEEP == 2


# ── §8 the endpoint fields ───────────────────────────────────────────────

class _NoP5Tables:
    async def fetchval(self, sql, *a):
        return False if "to_regclass" in sql else None

    async def fetch(self, *a):
        raise AssertionError("no table, no read")

    async def fetchrow(self, *a):
        raise AssertionError("no table, no read")


def test_unmeasured_endpoint_fields_are_null_with_a_reason_never_zero():
    res = asyncio.run(P5R.evaluate(_NoP5Tables(), env={}))
    fu, sb, cp = (res["focus_universe"], res["same_book_samples"],
                  res["c12_proofs"])
    assert fu["status"] == "UNMEASURED" and fu["count"] is None
    assert fu["per_tier"] is None and fu["members"] is None and fu["why"]
    assert sb["status"] == "UNMEASURED" and sb["sample_count"] is None
    assert sb["agreement_pct"] is None and sb["why"]
    assert sb["incomparable_reasons"] is None
    assert cp["status"] == "UNMEASURED" and cp["count"] is None
    assert cp["records"] is None and cp["why"]
    # the existing fields are all still there
    for k in ("verdict", "predicates", "first_blocking", "runtime_evidence",
              "per_symbol", "deciding_process", "predicate_order"):
        assert k in res


@pg
async def test_the_endpoint_reports_the_newest_universe_snapshot():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("DELETE FROM institutional_focus_universe")
        empty = await P5R.focus_universe_evidence(conn)
        assert empty["count"] is None and empty["status"] == "UNMEASURED"
        assert empty["why"].startswith("NO_FOCUS_UNIVERSE_SNAPSHOT")
        u = FU.prioritize({FU.T_ACTUAL: [c(SLUG)],
                           FU.T_CANDIDATE: [c("aec-nfl-x-y-2026-10-04")],
                           FU.T_EXPLORATION: [c("expl-a-2026-10-04")]})
        FU.attach(u, record_for=lambda s: AEC if s == SLUG else None)
        assert await FU.persist(_Pool(conn), u, process_id="p1",
                                service="institutional_md") == 3
        f = await P5R.focus_universe_evidence(conn)
        assert f["status"] == "MEASURED" and f["count"] == 3
        assert f["per_tier"][FU.T_ACTUAL] == 1
        assert f["per_tier"][FU.T_CANDIDATE] == 1
        assert f["per_tier"][FU.T_EXPLORATION] == 1
        assert f["per_tier"][FU.T_INTENT] == 0
        assert f["exact"] == 1 and f["unavailable"] == 2
        assert f["unavailable_reasons"] == {FU.U_NOT_LISTED: 2}
        m = {x["retail_slug"]: x for x in f["members"]}
        assert m[SLUG]["identity_status"] == FU.EXACT
        assert m[SLUG]["institutional_symbol"] == SLUG
        assert m[SLUG]["tier"] == FU.T_ACTUAL
        assert m["expl-a-2026-10-04"]["diagnostic_only"] is True
        assert m["aec-nfl-x-y-2026-10-04"]["unavailable_reason"] == \
            FU.U_NOT_LISTED
        assert f["by_service"]["institutional_md"]["count"] == 3
        res = P5R.assemble(await P5R.gather(conn, env={}))
        assert res["focus_universe"]["universe_id"] == u["universe_id"]
        assert res["same_book_samples"]["status"] == "MEASURED"
        assert res["c12_proofs"]["status"] == "MEASURED"
    finally:
        await tx.rollback()
        await conn.close()


def test_the_api_stream_subscribes_only_exact_members(monkeypatch):
    """The API's universe pass: every member is bootstrapped (the workers'
    allow-listed refdata read), only the EXACT ones are wanted."""
    other = "aec-nfl-x-y-2026-10-04"
    IS.BOOKS.set_state(IS.S_IDLE, "test")
    monkeypatch.setattr(IAS, "running", lambda: True)

    def boot(client, s):
        return {"record": AEC if s == SLUG else
                dict(AEC, symbol=other)}       # listed, but not one contract
    got = asyncio.run(IAS.refresh_once(symbols=[SLUG, other], bootstrap=boot,
                                       now=time.time()))
    assert got["bootstrapped"] == 2 and got["subscribed"] == 1
    assert IS.BOOKS.wanted() == [SLUG]
    IAS._UNIVERSE.update(FU.prioritize({FU.T_INTENT: [c(SLUG), c(other)]}))
    snap = IAS.universe_snapshot()
    st = {m["retail_slug"]: m["identity_status"] for m in snap["members"]}
    assert st == {SLUG: FU.EXACT, other: FU.UNAVAILABLE}
    assert IAS.describe()["focus_universe"]["exact"] == 1


# ── §9 migration 213: idempotent, and its rollback is clean ─────────────

@pg
async def test_213_is_idempotent_and_rolls_back_cleanly():
    import pathlib
    mig = pathlib.Path(__file__).resolve().parents[1] / "migrations"
    up = (mig / "213_p5_focus_universe.sql").read_text()
    down = (mig / "rollback" / "213_p5_focus_universe.down.sql").read_text()
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()

    async def cols():
        return {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE "
            "table_name = 'institutional_same_book_probe'")}
    try:
        await conn.execute(up)                       # applied twice: no-op
        assert set(SB.COLUMNS_213) <= await cols()
        await conn.execute(down)
        assert not set(SB.COLUMNS_213) & await cols()
        assert set(SB.COLUMNS_210) <= await cols()   # 210 is untouched
        for t in ("institutional_focus_universe", "p5_c12_decision_proof"):
            assert not await conn.fetchval("SELECT to_regclass($1) IS NOT "
                                           "NULL", t)
        # without 213 the probe still writes its 210 columns
        b = books_with()
        r = SB.sample(SLUG, record=AEC, books_current=b.current,
                      retail_read=fake_retail(retail_md()))
        assert await SB.persist(_Pool(conn), [r], process_id="p213",
                                service="s") == 1
        await conn.execute(up)
        await conn.execute(up)
        assert set(SB.COLUMNS_213) <= await cols()
    finally:
        await tx.rollback()
        await conn.close()
