"""ONE DECISION -> ONE EXECUTION INTENT -> PAPER + ACTUAL, CONCURRENTLY.

Owner correction 2026-10-03: the authoritative object is the qualified
investment decision. PAPER and ACTUAL are SIBLINGS derived from it, never
parent and child, and the actual lane never waits for the paper order, its
persistence or its simulated fill.

End to end through the real reactive path (fresh PinnAPI WS change -> the
real collector cycle -> the V3 investment decision) with the PAPER SIMULATOR
DELIBERATELY STALLED, against a FAKE retail venue (no real-money order):

  * the actual order is submitted while the paper order is still stalled;
  * an identical WS frame does not create a second submission;
  * a below-minimum 1:1,000 quantity refuses ACTUAL but PAPER proceeds;
  * an actual fill reaches Xavier directly (no paper handoff awaited);
  * Audrey links both branches to the same decision.

The lane's own gates on synthetic intents (fast, no feed):
  * a stale decision and a stale executable book refuse;
  * an unapproved strategy, an un-promoted version and exploration never
    become submit-eligible;
  * an ambiguous submission is reconciled against the venue before anything
    is retried, and a re-dispatch never submits twice.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import decision_hooks as DH
from sportsassets import execmirror as M
from sportsassets import execmirror_probe as EP
from sportsassets import execution_intent as EI
from sportsassets import pinnapi_reactive as R
from sportsassets.agents import paper_benchmark as PB

try:    # pytest collects tests/ as a package; unittest discovery does not
    from tests import test_pinnapi_reactive_integration as RI
    from tests.test_pinnapi_reactive_integration import env, pg  # noqa: F401
    from tests.test_execmirror import FakeVenue, KID, SEC
except ImportError:
    import test_pinnapi_reactive_integration as RI
    from test_pinnapi_reactive_integration import env, pg  # noqa: F401
    from test_execmirror import FakeVenue, KID, SEC

try:
    from tests import admission_fixture as AF
except ImportError:
    import admission_fixture as AF


def _admissible_world(e, monkeypatch):
    """The venue state an actual order requires, made explicit for the fake
    venue: an OPEN market and a book whose currency is ESTABLISHED under an
    approved live rule (a test-only rule; production approves none)."""
    AF.approve_test_rule(monkeypatch)
    monkeypatch.setattr(PB, "BOOK_CURRENCY", {
        "verdict": "ESTABLISHED", "rule": AF.TEST_RULE,
        "subscription_state": "RUNNING",
        "mechanism": "TEST_FIXTURE", "basis": "test-only approved live rule"})
    e.venue.market_state = "MARKET_STATE_OPEN"
    # THE SETTLEMENT FIXTURE: the venue's and the book's terms compare
    # COMPATIBLE with every rule established (the real fixture prose leaves
    # six conditions unstated, which the admission correctly refuses).
    from sportsassets.workers import ext_pinnacle_loop as LOOP
    real = LOOP._settlement_compatibility

    def compatible(srule):
        return dict(real(srule), compatibility="COMPATIBLE",
                    overall_established=True, blockers=[], attest_unmet=[])
    monkeypatch.setattr(LOOP, "_settlement_compatibility", compatible)


async def _arm_actual(e, monkeypatch, *, cap=25):
    """The retail account's control enabled for this test, its key present,
    the runner's snapshot taken -- and the process's lane + decision hook
    installed exactly as `execmirror.run` installs them."""
    monkeypatch.setenv(EP.KEY_ID_ENV, KID)
    monkeypatch.setenv(EP.SECRET_ENV, SEC)
    e.ctl_before = dict(await e.conn.fetchrow("SELECT * FROM execmirror_control WHERE id = 1"))
    await e.conn.execute("TRUNCATE smalllive_reviews, smalllive_handoffs, "
                         "smalllive_reconciliations, execmirror_fills, execmirror_events, "
                         "execmirror_snapshots, execmirror_orders")
    await e.conn.execute(
        """UPDATE execmirror_control SET enabled = true, stopped = false,
             stop_done_at = NULL, flatten_on_stop = false, cutover_at = now(),
             account_fingerprint = $1, baseline = '{}'::jsonb, max_order_usd = $2,
             scale = 1000 WHERE id = 1""", EP.fingerprint(KID), cap)
    e.retail = FakeVenue()
    e.retail.bp = 1000.0
    e.mirror = M.Mirror(lambda: e.retail, paper_account=e.acct["account_id"])
    await e.mirror.snapshot(e.conn, await M.control(e.conn))

    async def get_pool():
        return e.pool
    EI.start(get_pool, e.mirror)


async def _disarm(e):
    EI.stop()
    if getattr(e, "ctl_before", None):
        c = e.ctl_before
        await e.conn.execute(
            """UPDATE execmirror_control SET enabled = $1, stopped = $2,
                 cutover_at = $3, account_fingerprint = $4, max_order_usd = $5
               WHERE id = 1""", c["enabled"], c["stopped"], c["cutover_at"],
            c["account_fingerprint"], c["max_order_usd"])


def _stall_paper(monkeypatch):
    """The paper order write blocks until released: the simulator side is
    stalled for as long as the test holds it."""
    gate = asyncio.Event()
    entered = asyncio.Event()
    real = L.submit_order

    async def stalled(*a, **k):
        entered.set()
        await gate.wait()
        return await real(*a, **k)
    monkeypatch.setattr(L, "submit_order", stalled)
    return gate, entered


async def _wait(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if await cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def _cg_decisions(e):
    """The completed-game ENTER decisions of this session (the discovery
    cycle's own valuation is refused and is not one of them)."""
    return [dict(r) for r in await e.conn.fetch(
        "SELECT * FROM paper_decisions WHERE session_id = $1 AND strategy = $2"
        " AND verdict = 'ENTER' ORDER BY decided_at", e.acct["session_id"], PB.CG_STRATEGY)]


async def _paper_entries(e):
    return await e.conn.fetchval(
        "SELECT count(*) FROM paper_orders WHERE account_id = $1 AND role = 'ENTRY'",
        e.acct["account_id"])


# ═════════════════════════════════════════════════════════════════════
# END TO END: one WS change -> one decision -> PAPER + ACTUAL, paper stalled
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_actual_is_submitted_while_the_paper_simulator_is_stalled(env, monkeypatch):
    e = env
    RI._single_source(e)
    _admissible_world(e, monkeypatch)
    e.venue.bids = [(0.50, 2500)]         # enough depth for >= 1 live contract
    await RI._start_and_discover(e)
    await _arm_actual(e, monkeypatch)
    try:
        gate, entered = _stall_paper(monkeypatch)
        e.retail.behaviour = [{"fill": 2}]
        RI.tick(e.cache, e.eid, RI.WS_EDGE)                 # the WS change
        # the PAPER sibling reaches its (stalled) order write ...
        assert await _wait(lambda: _async(entered.is_set())), "paper never began"
        # ... and the ACTUAL sibling is submitted while paper is still stalled
        if not await _wait(lambda: _async(len(e.retail.placed) == 1)):
            rows = [dict(r) for r in await e.conn.fetch(
                "SELECT intent_id, actual_state, actual_refusal, evidence, timeline"
                " FROM execution_intents")]
            raise AssertionError("the actual order waited for the paper order: %r" % rows)
        assert await _paper_entries(e) == 0                 # paper still stalled
        ds = await _cg_decisions(e)
        assert len(ds) == 1 and ds[0]["verdict"] == "ENTER", ds
        d = ds[0]
        it = dict(await e.conn.fetchrow(
            "SELECT * FROM execution_intents WHERE decision_id = $1", d["decision_id"]))
        assert it["live_eligible"] is True and it["policy_version"] == PB.CG_VERSION
        assert it["intent_id"] == EI.intent_id_for(d["decision_id"])
        placed = e.retail.placed[0]
        assert placed["quantity"] == it["live_qty"] >= 1
        assert placed["tif"] == M.TIF["IOC"]
        assert float(placed["price"]["value"]) == float(it["wire_price"])
        # the actual fill went straight to Xavier: no paper fill exists yet
        assert await _wait(lambda: e.conn.fetchval(
            "SELECT count(*) FROM smalllive_handoffs WHERE group_id = $1", it["group_id"]))
        assert await e.conn.fetchval(
            "SELECT count(*) FROM paper_fills WHERE group_id = $1", it["group_id"]) == 0
        it = dict(await e.conn.fetchrow(
            "SELECT * FROM execution_intents WHERE intent_id = $1", it["intent_id"]))
        tl = json.loads(it["timeline"]) if isinstance(it["timeline"], str) else it["timeline"]
        for k in ("decision_complete", "intent_created", "lane_start", "submit_start",
                  "ack", "first_fill_seen", "xavier_handoff"):
            assert k in tl, (k, sorted(tl))
        assert tl["submit_start"]["utc_ns"] >= tl["intent_created"]["utc_ns"]
        # release the paper sibling: it completes independently
        gate.set()
        assert await _wait(lambda: _async_val(_paper_entries(e), 1))
        rows = await RI._wait_terminal(e)
        assert [r["state"] for r in rows] == ["COMPLETED"], rows
        # an identical WS frame never produces a second submission
        RI.tick(e.cache, e.eid, RI.WS_EDGE)
        await asyncio.sleep(1.0)
        assert len(e.retail.placed) == 1
        # Audrey links BOTH branches to the SAME decision
        await e.mirror.audrey_reconcile(e.conn)
        rec = await e.conn.fetchrow(
            "SELECT * FROM smalllive_reconciliations WHERE group_id = $1", it["group_id"])
        chain = json.loads(rec["chain"]) if isinstance(rec["chain"], str) else rec["chain"]
        link = chain["links"][0]
        assert link["relation"] == "SIBLING_OF_ONE_DECISION"
        assert link["execution_intent_id"] == it["intent_id"]
        assert link["decision_id"] == d["decision_id"] and link["decision_found"] is True
        assert link["paper_order_id"] is not None
        assert link["divergence"]["paper_sibling"] == "PRESENT"
        # the management page: ONE DECISION -> SIMULATED + ACTUAL, with latency
        from sportsassets import execmirror_view as V
        page = await V.small_live(e.conn)
        row = next(x for x in page["decisions"]["rows"]
                   if x["execution_intent_id"] == it["intent_id"])
        assert row["decision"]["decision_id"] == d["decision_id"]
        assert row["simulated"]["label"] == "SIMULATED" and row["simulated"]["paper_order_id"]
        assert row["actual"]["label"] == "ACTUAL" and row["actual"]["venue_order_id"]
        assert row["actual"]["rounded_qty"] == it["live_qty"]
        assert row["latency_ms"]["decision_to_submit_ms"] is not None
        assert row["latency_ms"]["decision_to_submit_ms"] >= 0
        assert page["decisions"]["latency_ms"]["decision_to_submit_ms"]["n"] >= 1
    finally:
        await _disarm(e)


@pg
async def test_a_below_minimum_live_quantity_refuses_actual_and_paper_proceeds(env, monkeypatch):
    e = env
    RI._single_source(e)
    _admissible_world(e, monkeypatch)
    e.venue.bids = [(0.50, 300)]          # paper target 300 -> 0.3 live contracts
    await RI._start_and_discover(e)
    await _arm_actual(e, monkeypatch)
    try:
        RI.tick(e.cache, e.eid, RI.WS_EDGE)
        rows = await RI._wait_terminal(e)
        assert [r["state"] for r in rows] == ["COMPLETED"], rows
        ds = await _cg_decisions(e)
        assert len(ds) == 1 and ds[0]["verdict"] == "ENTER", ds
        assert await _wait(lambda: e.conn.fetchval(
            "SELECT actual_state = 'REFUSED' FROM execution_intents WHERE decision_id = $1",
            ds[0]["decision_id"]))
        it = dict(await e.conn.fetchrow(
            "SELECT * FROM execution_intents WHERE decision_id = $1", ds[0]["decision_id"]))
        assert it["actual_refusal"] == M.BELOW_VENUE_MINIMUM
        assert it["live_qty"] == 0 and float(it["live_raw_qty"]) < 0.5
        assert e.retail.placed == []                        # never enlarged
        assert await _paper_entries(e) == 1                 # PAPER proceeded
    finally:
        await _disarm(e)


@pg
async def test_the_audited_defect_a_real_v3_decision_with_unestablished_book_and_unknown_settlement_is_never_placed(env, monkeypatch):
    """The independent audit's case through the REAL path: a fresh PinnAPI
    change -> a V3 ENTER whose decision record says book currency
    NOT_ESTABLISHED and settlement UNKNOWN. The intent is REFUSED (not live
    eligible), Venue.place is called zero times, and PAPER proceeds."""
    e = env
    RI._single_source(e)
    e.venue.bids = [(0.50, 2500)]
    await RI._start_and_discover(e)
    await _arm_actual(e, monkeypatch)
    try:
        RI.tick(e.cache, e.eid, RI.WS_EDGE)
        rows = await RI._wait_terminal(e)
        assert [r["state"] for r in rows] == ["COMPLETED"], rows
        ds = await _cg_decisions(e)
        assert len(ds) == 1 and ds[0]["verdict"] == "ENTER", ds
        it = dict(await e.conn.fetchrow(
            "SELECT * FROM execution_intents WHERE decision_id = $1", ds[0]["decision_id"]))
        assert it["live_eligible"] is False and it["actual_state"] == EI.A_REFUSED
        lel = json.loads(it["live_eligibility"]) if isinstance(it["live_eligibility"], str) \
            else it["live_eligibility"]
        refusals = lel["admission"]["refusals"]
        from sportsassets import actual_admission as AA
        assert AA.R_BOOK_CURRENCY in refusals and AA.R_SETTLEMENT in refusals, refusals
        assert lel["admission"]["book_currency"]["verdict"] == "NOT_ESTABLISHED"
        assert lel["admission"]["settlement"]["compatibility"] == "UNKNOWN"
        assert lel["admission"]["settlement"]["research_disclosure_counts"] is False
        await asyncio.sleep(0.5)
        assert e.retail.placed == []                        # zero Venue.place
        assert await e.conn.fetchval(
            "SELECT count(*) FROM execmirror_orders WHERE execution_intent_id = $1",
            it["intent_id"]) == 0
        assert await _paper_entries(e) == 1                 # PAPER proceeded
    finally:
        await _disarm(e)


async def _async(v):
    return v


async def _async_val(coro, want):
    return (await coro) == want


# ═════════════════════════════════════════════════════════════════════
# THE LANE'S OWN GATES on synthetic intents (no feed)
# ═════════════════════════════════════════════════════════════════════

async def _lane_env(monkeypatch):
    import asyncpg
    conn = await asyncpg.connect(RI.H.DSN)
    AF.approve_test_rule(monkeypatch)
    monkeypatch.setenv(EP.KEY_ID_ENV, KID)
    monkeypatch.setenv(EP.SECRET_ENV, SEC)
    before = dict(await conn.fetchrow("SELECT * FROM execmirror_control WHERE id = 1"))
    await conn.execute("TRUNCATE smalllive_reviews, smalllive_handoffs, "
                         "smalllive_reconciliations, execmirror_fills, execmirror_events, "
                         "execmirror_snapshots, execmirror_orders")
    await conn.execute(
        """UPDATE execmirror_control SET enabled = true, stopped = false,
             stop_done_at = NULL, cutover_at = now(), account_fingerprint = $1,
             max_order_usd = 25, scale = 1000 WHERE id = 1""", EP.fingerprint(KID))
    venue = FakeVenue()
    venue.bp = 1000.0
    mirror = M.Mirror(lambda: venue)
    await mirror.snapshot(conn, await M.control(conn))
    return conn, venue, mirror, EI.ActualLane(None, mirror), before


async def _restore(conn, before):
    await conn.execute(
        """UPDATE execmirror_control SET enabled = $1, stopped = $2, cutover_at = $3,
             account_fingerprint = $4, max_order_usd = $5 WHERE id = 1""",
        before["enabled"], before["stopped"], before["cutover_at"],
        before["account_fingerprint"], before["max_order_usd"])
    await conn.close()


async def _intent(conn, *, strategy="PINNACLE_COMPLETED_GAME_PAPER",
                  version=PB.CG_VERSION, qty=2702, decided_ago=0.5, book_ago=0.5,
                  facts=None, slug=None):
    now = time.time()
    did = "dec_lane_%s" % uuid.uuid4().hex[:12]
    slug = slug or "mlb-lane-%s" % did[-4:]
    return await EI.create(
        conn, decision_id=did, valuation_id=None, strategy=strategy,
        policy_version=version, slug=slug,
        order_intent="ORDER_INTENT_BUY_LONG", holding_side="LONG",
        group_id="grp_" + did, order_type="MARKETABLE", time_in_force="IOC",
        paper_target_qty=qty, limit_price=0.55, wire_price=0.55, book_obs_id=None,
        book_observed_at=now - book_ago, decided_at=now - decided_ago,
        evidence={"admission_facts": facts if facts is not None else
                  AF.admissible_facts(slug=slug)}, timeline={})


@pg
async def test_stale_decision_and_stale_book_refuse_the_actual_lane(monkeypatch):
    conn, venue, mirror, lane, before = await _lane_env(monkeypatch)
    try:
        a = await _intent(conn, decided_ago=EI.MAX_DECISION_AGE_S + 5)
        assert (await lane._run(conn, a["intent_id"]))["refusal"] == EI.R_DECISION_STALE
        b = await _intent(conn, book_ago=EI.MAX_BOOK_AGE_S + 5)
        assert (await lane._run(conn, b["intent_id"]))["refusal"] == EI.R_BOOK_STALE
        assert venue.placed == []
    finally:
        await _restore(conn, before)


@pg
async def test_unapproved_unpromoted_and_exploration_never_become_submit_eligible(monkeypatch):
    conn, venue, mirror, lane, before = await _lane_env(monkeypatch)
    try:
        for strategy, version in (("PINNACLE_EXPLORATION_PAPER", "PINNACLE_EXPLORATION_PAPER_V3"),
                                  ("SOMETHING_UNAPPROVED", "X_V1"),
                                  ("PINNACLE_COMPLETED_GAME_PAPER",
                                   "PINNACLE_COMPLETED_GAME_PAPER_V4"),
                                  ("PINNACLE_COMPLETED_GAME_PAPER", None)):
            it = await _intent(conn, strategy=strategy, version=version)
            assert it["live_eligible"] is False and it["actual_state"] == EI.A_PAPER_ONLY
            assert it["actual_refusal"] == M.STRATEGY_NOT_LIVE_ELIGIBLE
            assert EI.dispatch(it) is False
            assert (await lane._run(conn, it["intent_id"]))["state"] == "NOT_DISPATCHED"
        assert venue.placed == []
    finally:
        await _restore(conn, before)


@pg
async def test_an_ambiguous_submission_reconciles_before_any_retry(monkeypatch):
    conn, venue, mirror, lane, before = await _lane_env(monkeypatch)
    try:
        it = await _intent(conn)
        venue.behaviour = [{"raise": 504, "create_anyway": True}]
        venue.orders.clear()
        # an IOC that the venue kept resting is what makes the order findable
        got = await lane._run(conn, it["intent_id"])
        assert got["state"] == "UNKNOWN" and len(venue.placed) == 1
        # re-dispatching the same intent never submits again
        await conn.execute("UPDATE execution_intents SET actual_state = 'DISPATCHED'"
                           " WHERE intent_id = $1", it["intent_id"])
        assert (await lane._run(conn, it["intent_id"]))["state"] == "DUPLICATE"
        assert len(venue.placed) == 1
        # the runner reconciles against the venue (never resends)
        for o in venue.orders.values():
            o["state"] = "ORDER_STATE_NEW"
        await mirror.recover(conn)
        row = await conn.fetchrow(
            "SELECT * FROM execmirror_orders WHERE execution_intent_id = $1", it["intent_id"])
        assert row["state"] == "OPEN" and row["venue_order_id"] is not None
        assert len(venue.placed) == 1
    finally:
        await _restore(conn, before)


def test_the_decision_hook_is_the_only_seam():
    """The paper decision modules reach execution only through the hook the
    executing process installs; with no hook they record paper-only."""
    assert EI.on_decision.__name__ == "on_decision"
    assert DH.DECISION_HOOK is None or DH.DECISION_HOOK is EI.on_decision
