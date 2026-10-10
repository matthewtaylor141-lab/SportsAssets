"""CAPITAL-CRITICAL: THE ACTUAL LANE ADMITS ONLY EXPLICITLY ADMISSIBLE EVIDENCE.

Independent audit of ebe54a8: a V3 intent became live_eligible = true while
its own decision record said book currency NOT_ESTABLISHED and settlement
compatibility UNKNOWN. These tests prove, against a FAKE venue only, that
Venue.place is called ZERO times when any requirement is missing -- at the
intent (create), again inside the lane from the intent's stored facts, and in
the database itself (migration 200) -- and that exactly one idempotent
submission happens on one complete positive case.
"""
from __future__ import annotations

import pathlib
import time
import uuid

import asyncpg
import pytest

from sportsassets import actual_admission as AA
from sportsassets import execmirror as M
from sportsassets import execmirror_probe as EP
from sportsassets import execution_intent as EI
from sportsassets.agents import paper_benchmark as PB

try:    # pytest collects tests/ as a package; unittest discovery does not
    from tests import admission_fixture as AF
    from tests import paper_harness as H
    from tests.test_execmirror import FakeVenue, KID, SEC
except ImportError:
    import admission_fixture as AF
    import paper_harness as H
    from test_execmirror import FakeVenue, KID, SEC

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"

SLUG = "mlb-adm-test"


def _facts(**over):
    f = AF.admissible_facts(slug=SLUG)
    for path, value in over.items():
        f = AF.mutate(f, path.replace("__", "."), value)
    return f


# ═════════════════════════════════════════════════════════════════════
# THE PURE RULE
# ═════════════════════════════════════════════════════════════════════

# R30A: the settlement-compatibility gate admits only under an approval of its
# current configuration (live_approvals; production approves none). The pure
# cases below state it as approved so each one still tests its own facts; the
# gate's own refusal is the last test of this section.
GATES = frozenset({AA.SETTLEMENT_GATE_ID})


def test_complete_facts_under_an_approved_live_rule_are_admissible():
    got = AA.evaluate(_facts(), slug=SLUG, order_intent="ORDER_INTENT_BUY_LONG",
                      approved_book_rules={AF.TEST_RULE},
                      approved_settlement_gates=GATES)
    assert got["verdict"] == AA.LIVE_ADMISSIBLE, got["refusals"]
    assert len(got["requirements"]) == 11 and len(got["runtime_requirements"]) == 6


def test_production_has_no_approved_live_book_rule():
    """P5 is NOT_ESTABLISHED: no live book rule is approved, so the very
    facts that pass under a test rule are refused in production."""
    assert AA.APPROVED_LIVE_BOOK_RULES == frozenset()
    got = AA.evaluate(_facts(), slug=SLUG)
    assert got["verdict"] == AA.NOT_ADMISSIBLE
    assert got["refusal"] == AA.R_BOOK_CURRENCY


def test_the_benchmark_book_currency_is_not_live_admissible():
    got = AA.evaluate(_facts(book__book_currency=dict(PB.BOOK_CURRENCY)),
                      slug=SLUG, approved_book_rules={AF.TEST_RULE})
    assert got["refusal"] == AA.R_BOOK_CURRENCY


NEGATIVES = [
    ("book currency NOT_ESTABLISHED", {"book__book_currency__verdict": "NOT_ESTABLISHED"}, AA.R_BOOK_CURRENCY),
    ("book currency STALE", {"book__book_currency__verdict": "STALE"}, AA.R_BOOK_CURRENCY),
    ("book currency GAP", {"book__book_currency__verdict": "GAP"}, AA.R_BOOK_CURRENCY),
    ("book currency UNKNOWN", {"book__book_currency__verdict": "UNKNOWN"}, AA.R_BOOK_CURRENCY),
    ("book currency absent", {"book__book_currency": None}, AA.R_BOOK_CURRENCY),
    ("gap since snapshot", {"book__book_currency__gap_since_snapshot": True}, AA.R_BOOK_CURRENCY),
    ("market subscription refused", {"book__book_currency__subscription_state": "REFUSED"}, AA.R_BOOK_CURRENCY),
    ("unapproved book rule", {"book__book_currency__rule": "P5_BOOK_CURRENCY"}, AA.R_BOOK_CURRENCY),
    ("settlement UNKNOWN", {"settlement__compatibility": "UNKNOWN"}, AA.R_SETTLEMENT),
    ("settlement incompatible", {"settlement__compatibility": "CONFLICTING"}, AA.R_SETTLEMENT),
    ("settlement not established", {"settlement__overall_established": False}, AA.R_SETTLEMENT),
    ("settlement blocker", {"settlement__blockers": ["VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED"]}, AA.R_SETTLEMENT),
    ("research disclosure only", {"settlement__compatibility": None,
                                  "settlement__research_disclosure":
                                  "DISCLOSED_RESEARCH_RISK_NOT_SETTLEMENT_COMPATIBILITY"}, AA.R_SETTLEMENT),
    ("identity check failed", {"identity__checks": [{"check": "payout_outcome_match", "passed": False}]}, AA.R_IDENTITY),
    ("probability stale", {"probability__age_s": 45.0}, AA.R_PROBABILITY),
    ("probability not PinnAPI authority", {"probability__authority_basis": None}, AA.R_PROBABILITY),
    ("probability unmapped", {"probability__mapped": False}, AA.R_PROBABILITY),
    ("market not tradable", {"book__market_state": "MARKET_STATE_HALTED"}, AA.R_TRADABLE),
    ("market state unknown", {"book__market_state": None}, AA.R_TRADABLE),
    ("price unknown", {"price__wire": None}, AA.R_PRICE),
    ("no depth", {"book__depth_within_limit": 0}, AA.R_DEPTH),
    ("fees unknown", {"fees__known": False}, AA.R_FEES),
    ("slippage unrepresented", {"slippage__model": None}, AA.R_SLIPPAGE),
    ("net EV not positive", {"economics__net_expected_profit_usd": 0.0}, AA.R_NET_EV),
]


@pytest.mark.parametrize("name,over,code", NEGATIVES, ids=[n for n, _, _ in NEGATIVES])
def test_every_missing_requirement_fails_closed(name, over, code):
    got = AA.evaluate(_facts(**over), slug=SLUG, approved_book_rules={AF.TEST_RULE},
                      approved_settlement_gates=GATES)
    assert got["verdict"] == AA.NOT_ADMISSIBLE
    assert code in got["refusals"], (name, got["refusals"])


def test_compatible_settlement_without_the_gate_approval_is_refused():
    """R30A: COMPATIBLE settlement facts admit only while the settlement gate's
    configuration is approved; production's constant is empty."""
    assert AA.APPROVED_SETTLEMENT_GATES == frozenset()
    for approved in (None, frozenset(), frozenset({"SOME_OTHER_GATE"})):
        got = AA.evaluate(_facts(), slug=SLUG, approved_book_rules={AF.TEST_RULE},
                          approved_settlement_gates=approved)
        assert got["verdict"] == AA.NOT_ADMISSIBLE
        assert got["refusals"] == [AA.R_SETTLEMENT], got["refusals"]
        req = [r for r in got["requirements"]
               if r["requirement"] == "settlement_live_admissible"]
        assert req and req[0]["gate_approved"] is False
        assert req[0]["why"] == "SETTLEMENT_GATE_APPROVAL_ABSENT_OR_STALE"


def test_facts_bound_to_another_contract_are_refused():
    got = AA.evaluate(_facts(), slug="mlb-some-other-contract",
                      approved_book_rules={AF.TEST_RULE})
    assert got["refusal"] == AA.R_IDENTITY
    got = AA.evaluate(_facts(), slug=SLUG, order_intent="ORDER_INTENT_BUY_SHORT",
                      approved_book_rules={AF.TEST_RULE})
    assert got["refusal"] == AA.R_IDENTITY


def test_absent_facts_are_refused():
    for facts in (None, {}):
        got = AA.evaluate(facts, approved_book_rules={AF.TEST_RULE})
        assert got["verdict"] == AA.NOT_ADMISSIBLE and got["refusal"] == AA.R_FACTS_ABSENT


def test_the_benchmark_records_the_settlement_disclosure_as_a_disclosure():
    """The completed-game paper decision's facts carry the exceptional terms
    as a research disclosure; admission never reads them as compatibility."""
    cand = {"us_market_slug": SLUG, "side": "ORDER_INTENT_BUY_LONG",
            "settlement": {"compatibility": "UNKNOWN", "overall_established": True,
                           "blockers": []}}
    match = {"checks": [], "probability_authority": {"basis": AA.PINNAPI_AUTHORITY},
             "exceptional_terms": {
                 "status": "DISCLOSED_RESEARCH_RISK_NOT_SETTLEMENT_COMPATIBILITY"}}
    f = PB.admission_facts(cand=cand, pin={"qualified": True, "age_s": 1, "limit_s": 30},
                           match=match, p=0.6, obs=None, md=None, sized={}, econ=None,
                           book_age=None)
    assert f["settlement"]["research_disclosure"].startswith("DISCLOSED_RESEARCH_RISK")
    assert f["book"]["book_currency"]["verdict"] == "NOT_ESTABLISHED"
    got = AA.evaluate(f, slug=SLUG, approved_book_rules={AF.TEST_RULE})
    assert AA.R_SETTLEMENT in got["refusals"] and AA.R_BOOK_CURRENCY in got["refusals"]


# ═════════════════════════════════════════════════════════════════════
# THE LANE AND THE DATABASE: Venue.place is called ZERO times
# ═════════════════════════════════════════════════════════════════════

class _Env:
    pass


async def _env(monkeypatch, *, cap=25, fingerprint_of=KID):
    conn = await asyncpg.connect(H.DSN)
    AF.approve_test_rule(monkeypatch)
    # R30A: the canonical SMALL LIVE authorization, stated as an assumption
    # (refused without it: tests/test_live_parity_convergence.py)
    AF.assume_canonical_live_authorization(monkeypatch)
    monkeypatch.setenv(EP.KEY_ID_ENV, KID)
    monkeypatch.setenv(EP.SECRET_ENV, SEC)
    e = _Env()
    e.conn = conn
    e.before = dict(await conn.fetchrow("SELECT * FROM execmirror_control WHERE id = 1"))
    await conn.execute("TRUNCATE smalllive_reviews, smalllive_handoffs, "
                       "smalllive_reconciliations, execmirror_fills, execmirror_events, "
                       "execmirror_snapshots, execmirror_orders")
    await conn.execute(
        """UPDATE execmirror_control SET enabled = true, stopped = false,
             stop_done_at = NULL, cutover_at = now(), account_fingerprint = $1,
             max_order_usd = $2, scale = 1000 WHERE id = 1""",
        EP.fingerprint(fingerprint_of), cap)
    e.venue = FakeVenue()
    e.venue.bp = 1000.0
    e.mirror = M.Mirror(lambda: e.venue)
    await e.mirror.snapshot(conn, await M.control(conn))
    e.lane = EI.ActualLane(None, e.mirror)
    return e


async def _close(e):
    b = e.before
    await e.conn.execute(
        """UPDATE execmirror_control SET enabled = $1, stopped = $2, cutover_at = $3,
             account_fingerprint = $4, max_order_usd = $5 WHERE id = 1""",
        b["enabled"], b["stopped"], b["cutover_at"], b["account_fingerprint"],
        b["max_order_usd"])
    await e.conn.close()


async def _intent(conn, *, facts=None, strategy=PB.CG_STRATEGY, version=PB.CG_VERSION,
                  qty=2702, wire=0.55, decided_ago=0.5, book_ago=0.5, slug=None):
    now = time.time()
    did = "dec_adm_%s" % uuid.uuid4().hex[:12]
    slug = slug or "mlb-adm-%s" % did[-6:]
    if facts is None:
        facts = AF.admissible_facts(slug=slug, wire=wire)
    return await EI.create(
        conn, decision_id=did, valuation_id=None, strategy=strategy,
        policy_version=version, slug=slug, order_intent="ORDER_INTENT_BUY_LONG",
        holding_side="LONG", group_id="grp_" + did, order_type="MARKETABLE",
        time_in_force="IOC", paper_target_qty=qty, limit_price=wire, wire_price=wire,
        book_obs_id=None, book_observed_at=now - book_ago, decided_at=now - decided_ago,
        evidence={"admission_facts": facts}, timeline={})


def _slugged(slug, **over):
    f = AF.admissible_facts(slug=slug)
    for path, value in over.items():
        f = AF.mutate(f, path.replace("__", "."), value)
    return f


INTENT_NEGATIVES = [
    ("book_currency_not_established", {"book__book_currency__verdict": "NOT_ESTABLISHED"}, AA.R_BOOK_CURRENCY),
    ("book_currency_stale", {"book__book_currency__verdict": "STALE"}, AA.R_BOOK_CURRENCY),
    ("book_currency_gap", {"book__book_currency__verdict": "GAP"}, AA.R_BOOK_CURRENCY),
    ("market_subscription_refused", {"book__book_currency__subscription_state": "REFUSED"}, AA.R_BOOK_CURRENCY),
    ("settlement_unknown", {"settlement__compatibility": "UNKNOWN"}, AA.R_SETTLEMENT),
    ("settlement_incompatible", {"settlement__compatibility": "CONFLICTING"}, AA.R_SETTLEMENT),
    ("identity_mapping_differs", {"identity__us_market_slug": "mlb-a-different-contract"}, AA.R_IDENTITY),
]


@pg
@pytest.mark.parametrize("name,over,code", INTENT_NEGATIVES,
                         ids=[n for n, _, _ in INTENT_NEGATIVES])
async def test_inadmissible_evidence_is_refused_at_the_intent_and_never_placed(
        monkeypatch, name, over, code):
    e = await _env(monkeypatch)
    try:
        slug = "mlb-adm-%s" % uuid.uuid4().hex[:6]
        it = await _intent(e.conn, slug=slug, facts=_slugged(slug, **over))
        assert it["live_eligible"] is False
        assert it["actual_state"] == EI.A_REFUSED and it["actual_refusal"] == code
        assert EI.dispatch(it) is False
        assert (await e.lane._run(e.conn, it["intent_id"]))["state"] == "NOT_DISPATCHED"
        assert e.venue.placed == []
    finally:
        await _close(e)


@pg
async def test_a_dispatched_row_with_inadmissible_facts_is_refused_inside_the_lane(monkeypatch):
    """Defence in depth: even an intent row that claims admission (written by
    any path) is re-derived from its own stored facts before any claim."""
    e = await _env(monkeypatch)
    try:
        slug = "mlb-adm-%s" % uuid.uuid4().hex[:6]
        it = await _intent(e.conn, slug=slug)
        assert it["actual_state"] == EI.A_DISPATCHED
        bad = _slugged(slug, book__book_currency__verdict="NOT_ESTABLISHED",
                       settlement__compatibility="UNKNOWN")
        await e.conn.execute(
            "UPDATE execution_intents SET evidence = jsonb_set(evidence, '{admission_facts}',"
            " $2::jsonb) WHERE intent_id = $1", it["intent_id"], __import__("json").dumps(bad))
        got = await e.lane._run(e.conn, it["intent_id"])
        assert got["state"] == EI.A_REFUSED and got["refusal"] == AA.R_BOOK_CURRENCY
        assert AA.R_SETTLEMENT in got["admission_refusals"]
        assert e.venue.placed == []
        assert await e.conn.fetchval(
            "SELECT count(*) FROM execmirror_orders WHERE execution_intent_id = $1",
            it["intent_id"]) == 0
    finally:
        await _close(e)


@pg
async def test_stale_book_and_stale_decision_are_never_placed(monkeypatch):
    e = await _env(monkeypatch)
    try:
        a = await _intent(e.conn, book_ago=EI.MAX_BOOK_AGE_S + 5)
        assert (await e.lane._run(e.conn, a["intent_id"]))["refusal"] == EI.R_BOOK_STALE
        b = await _intent(e.conn, decided_ago=EI.MAX_DECISION_AGE_S + 5)
        assert (await e.lane._run(e.conn, b["intent_id"]))["refusal"] == EI.R_DECISION_STALE
        assert e.venue.placed == []
    finally:
        await _close(e)


@pg
async def test_unapproved_strategy_and_policy_version_are_never_placed(monkeypatch):
    e = await _env(monkeypatch)
    try:
        for strategy, version in (("SOMETHING_UNAPPROVED", "X_V1"),
                                  ("PINNACLE_EXPLORATION_PAPER", "PINNACLE_EXPLORATION_PAPER_V3"),
                                  (PB.CG_STRATEGY, "PINNACLE_COMPLETED_GAME_PAPER_V9")):
            it = await _intent(e.conn, strategy=strategy, version=version)
            assert it["live_eligible"] is False and it["actual_state"] == EI.A_PAPER_ONLY
            assert (await e.lane._run(e.conn, it["intent_id"]))["state"] == "NOT_DISPATCHED"
        assert e.venue.placed == []
    finally:
        await _close(e)


@pg
async def test_a_different_account_fingerprint_is_never_placed(monkeypatch):
    e = await _env(monkeypatch, fingerprint_of="some-other-retail-key-id")
    try:
        it = await _intent(e.conn)
        got = await e.lane._run(e.conn, it["intent_id"])
        assert got["refusal"] == EI.R_ACCOUNT_CHANGED
        assert e.venue.placed == []
    finally:
        await _close(e)


@pg
async def test_below_venue_minimum_and_above_the_cap_are_never_placed(monkeypatch):
    e = await _env(monkeypatch, cap=25)
    try:
        small = await _intent(e.conn, qty=300)            # 0.3 contracts -> 0
        got = await e.lane._run(e.conn, small["intent_id"])
        assert got["refusal"] == M.BELOW_VENUE_MINIMUM
        big = await _intent(e.conn, qty=60000, wire=0.55)  # 60 x 0.55 = $33 > $25
        got = await e.lane._run(e.conn, big["intent_id"])
        assert got["refusal"] == M.ABOVE_ORDER_CAP
        assert e.venue.placed == []
    finally:
        await _close(e)


@pg
async def test_the_decision_depth_must_cover_the_actual_quantity(monkeypatch):
    e = await _env(monkeypatch)
    try:
        slug = "mlb-adm-%s" % uuid.uuid4().hex[:6]
        it = await _intent(e.conn, slug=slug, qty=5000,
                           facts=_slugged(slug, book__depth_within_limit=2))
        got = await e.lane._run(e.conn, it["intent_id"])
        assert got["refusal"] == AA.R_DEPTH and e.venue.placed == []
    finally:
        await _close(e)


@pg
async def test_the_database_refuses_live_eligibility_without_admission(monkeypatch):
    """Migration 200: neither a live_eligible intent without a LIVE_ADMISSIBLE
    admission nor an actual order for an inadmissible intent can be written."""
    e = await _env(monkeypatch)
    tx = e.conn.transaction()
    await tx.start()
    try:
        await e.conn.execute((MIG / "200_actual_admission_guard.sql").read_text())
        slug = "mlb-adm-%s" % uuid.uuid4().hex[:6]
        refused = await _intent(e.conn, slug=slug,
                                facts=_slugged(slug, settlement__compatibility="UNKNOWN"))
        sp = e.conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await e.conn.execute(
                "UPDATE execution_intents SET live_eligible = true, actual_state ="
                " 'DISPATCHED', actual_refusal = NULL WHERE intent_id = $1",
                refused["intent_id"])
        await sp.rollback()
        sp = e.conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await e.conn.execute(
                "INSERT INTO execmirror_orders (mirror_id, execution_intent_id, role,"
                " us_market_slug, intent, order_type, tif, state) VALUES ($1, $2,"
                " 'ENTRY', $3, 'ORDER_INTENT_BUY_LONG', 'MARKETABLE', 'IOC', 'SUBMITTING')",
                "ei:" + refused["intent_id"], refused["intent_id"], slug)
        await sp.rollback()
    finally:
        await tx.rollback()
        await _close(e)


# ═════════════════════════════════════════════════════════════════════
# THE ONE COMPLETE POSITIVE CASE (fake venue)
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_one_admissible_decision_makes_exactly_one_idempotent_submission(monkeypatch):
    """Fresh PinnAPI -> valid V3 decision facts -> LIVE_ADMISSIBLE settlement
    -> current executable book under an approved (test) live rule -> positive
    net EV -> one execution intent -> ONE fake-venue submission. A duplicate
    dispatch creates no second order; an ambiguous submission is reconciled
    against the venue, never blindly resent."""
    e = await _env(monkeypatch)
    try:
        it = await _intent(e.conn, qty=2702, wire=0.55)
        assert it["live_eligible"] is True and it["actual_state"] == EI.A_DISPATCHED
        lel = it["live_eligibility"]
        lel = __import__("json").loads(lel) if isinstance(lel, str) else lel
        assert lel["admission"]["verdict"] == AA.LIVE_ADMISSIBLE
        assert lel["admission"]["digest"]
        e.venue.behaviour = [{"fill": 2}]
        got = await e.lane._run(e.conn, it["intent_id"])
        assert got["state"] == EI.A_SUBMITTED, got
        assert len(e.venue.placed) == 1
        placed = e.venue.placed[0]
        # rc6.3 pmus-sizing: 2,702 / 1,000 = 2.702 is ROUNDED DOWN to 2
        # contracts (half-even sent 3: ABOVE the 1:1000 target)
        assert placed["quantity"] == 2 and float(placed["price"]["value"]) == 0.55
        assert placed["tif"] == M.TIF["IOC"]
        # duplicate dispatch: no second order
        await e.conn.execute("UPDATE execution_intents SET actual_state = 'DISPATCHED',"
                             " actual_refusal = NULL WHERE intent_id = $1", it["intent_id"])
        assert (await e.lane._run(e.conn, it["intent_id"]))["state"] == "DUPLICATE"
        assert len(e.venue.placed) == 1
        # an ambiguous submission is reconciled, never resent
        amb = await _intent(e.conn, qty=2702)
        e.venue.behaviour = [{"raise": 504, "create_anyway": True}]
        got = await e.lane._run(e.conn, amb["intent_id"])
        assert got["state"] == "UNKNOWN" and len(e.venue.placed) == 2
        await e.conn.execute("UPDATE execution_intents SET actual_state = 'DISPATCHED'"
                             " WHERE intent_id = $1", amb["intent_id"])
        assert (await e.lane._run(e.conn, amb["intent_id"]))["state"] == "DUPLICATE"
        for o in e.venue.orders.values():
            o["state"] = "ORDER_STATE_NEW"
        await e.mirror.recover(e.conn)
        row = await e.conn.fetchrow(
            "SELECT * FROM execmirror_orders WHERE execution_intent_id = $1", amb["intent_id"])
        assert row["venue_order_id"] is not None
        assert len(e.venue.placed) == 2
    finally:
        await _close(e)
