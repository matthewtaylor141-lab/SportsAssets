"""EDDIE'S SHADOW EXECUTION ESTIMATES, THE HARD RULE, OUTCOMES, THE SCORECARD,
THE RUNNER AND THE CANDIDATE-REVIEW WORKFLOW (migration 217).

  * THE HARD RULE: Eddie never recommends executing (EXECUTE_NOW /
    REST_LIMIT / SPLIT) a candidate whose expected executable EV is <= 0 --
    or unmeasured -- whatever its theoretical edge. Pure (`estimate`,
    `enforce_hard_rule`), at the write (`record_estimate`) and in the
    database (CHECK eddie_estimates_hard_rule_ck);
  * every unmeasured field is NULL with a named reason (CHECK too);
  * outcomes compare predicted with realized execution loss;
  * the scorecard is null (UNAVAILABLE) until measured;
  * the runner is bounded, failure-isolated, idempotent and has a kill
    switch; the workflow records seven grounded steps in order.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from sportsassets.agents import eddie as E
from sportsassets.agents import eddie_runner as ER
from sportsassets.agents import pos_workflow as W
from sportsassets.agents import registry as R

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
T0 = 1_790_000_000.0


def _book(offers, bids, at=T0 - 5, obs_id=9):
    return {"obs_id": obs_id, "observed_at": at, "tick": None,
            "market_state": None,
            "offers": [{"px": {"value": "%.2f" % p}, "qty": str(q)}
                       for p, q in offers],
            "bids": [{"px": {"value": "%.2f" % p}, "qty": str(q)}
                     for p, q in bids]}


def _cand(**kw):
    c = {"decision_id": "paper_d_t217", "decided_at": T0,
         "us_market_slug": "m-t217", "holding_side": "LONG",
         "proposed_qty": 1000, "limit_price": 0.56, "p_blended": 0.62,
         "economics": {"acquisition": {"fees_usd": 10.0}}}
    c.update(kw)
    return c


def _hist(adv_t=0.005, adv_m=0.01, fill_t=0.9, fill_m=0.4, ttf=5.0):
    def v(x):
        return {"value": x, "why": None if x is not None else "NONE (n=0)"}
    return {"fill_rate": {E.TAKER: v(fill_t), E.MAKER: v(fill_m)},
            "time_to_fill": {E.TAKER: v(ttf), E.MAKER: v(ttf * 20)},
            "adverse_selection": {E.TAKER: v(adv_t), E.MAKER: v(adv_m)},
            "latency": v(0.5), "queue": {"n": 0}}


# ════════════════════════════════════════════════════════════════════
# THE ESTIMATE (pure)
# ════════════════════════════════════════════════════════════════════

def test_a_positive_net_executable_edge_executes_now():
    e = E.estimate(_cand(), _book([(0.52, 5000)], [(0.50, 5000)]), _hist(),
                   now=T0 + 1)
    assert e["theoretical_edge_pp"] == pytest.approx(0.62 - 0.51)
    assert e["spread_cost_pp"] == pytest.approx(0.01)
    assert e["expected_slippage_pp"] == pytest.approx(0.0)
    assert e["expected_fees_pp"] == pytest.approx(0.01)
    loss = 0.01 + 0.0 + 0.01 + 0.005
    assert e["expected_execution_loss_pp"] == pytest.approx(loss)
    assert e["expected_net_executable_edge_pp"] == pytest.approx(0.11 - loss)
    assert e["expected_executable_ev_usd"] == pytest.approx(
        0.9 * (0.11 - loss) * 1000)
    assert e["recommendation"] == E.EXECUTE_NOW
    assert e["execution_style"] == E.TAKER
    assert e["unmeasured"] == {}
    assert e["authority"] == "SHADOW_ONLY"
    assert {"kind": "paper_decisions", "id": "paper_d_t217"} in \
        e["evidence_refs"]
    assert set(e["dimensions"]) == set(E.DIMENSIONS)
    for k, d in e["dimensions"].items():
        assert d["status"] in ("MEASURED", "UNAVAILABLE"), k
        assert d.get("why") or d.get("source"), k


def test_the_hard_rule_a_theoretical_edge_that_execution_eats_is_skipped():
    # p - mid > 0 (theoretical edge), but a wide spread, deep slippage and
    # adverse selection make the executable edge negative
    e = E.estimate(_cand(p_blended=0.56),
                   _book([(0.55, 100), (0.60, 5000)], [(0.50, 5000)]),
                   _hist(adv_t=0.02, adv_m=0.05), now=T0 + 1)
    assert e["theoretical_edge_pp"] > 0
    assert e["expected_net_executable_edge_pp"] < 0
    assert e["recommendation"] == E.SKIP
    assert e["recommendation"] not in E.EXECUTING


def test_unmeasured_economics_wait_and_every_null_is_named():
    e = E.estimate(_cand(), _book([(0.52, 5000)], [(0.50, 5000)]),
                   _hist(adv_t=None, adv_m=None), now=T0 + 1)
    assert e["recommendation"] == E.WAIT
    assert e["expected_net_executable_edge_pp"] is None
    for k in ("adverse_selection", "net_executable_edge"):
        assert e["unmeasured"].get(k), k
    # no book at all: every book-derived field is null with its reason
    e = E.estimate(_cand(), None, _hist(), now=T0 + 1)
    assert e["recommendation"] == E.WAIT
    for k in ("theoretical_edge", "spread_cost", "slippage",
              "max_executable_size", "net_executable_edge"):
        assert e["unmeasured"].get(k), k


def test_a_stale_book_waits_and_no_edge_at_the_mid_is_skipped():
    e = E.estimate(_cand(), _book([(0.52, 5000)], [(0.50, 5000)],
                                  at=T0 - 600), _hist(), now=T0 + 1)
    assert e["recommendation"] == E.WAIT
    assert "BOOK_STALE" in e["recommendation_reason"]
    e = E.estimate(_cand(p_blended=0.45), _book([(0.52, 5000)],
                                                [(0.50, 5000)]),
                   _hist(), now=T0 + 1)
    assert e["recommendation"] == E.SKIP


def test_split_when_only_part_of_the_size_is_economic():
    e = E.estimate(_cand(proposed_qty=3000),
                   _book([(0.52, 1000), (0.70, 5000)], [(0.50, 5000)]),
                   _hist(fill_m=0.02), now=T0 + 1)
    assert e["max_executable_qty"] == pytest.approx(1000)
    assert e["recommendation"] == E.SPLIT
    assert e["execution_style"] == E.SPLIT_TAKER
    assert e["expected_executable_ev_usd"] > 0


def test_rest_limit_only_when_the_resting_ev_is_positive_and_larger():
    # a wide spread: taker loses, resting at the bid keeps the edge
    e = E.estimate(_cand(p_blended=0.60),
                   _book([(0.60, 5000)], [(0.50, 5000)]),
                   _hist(adv_t=0.0, adv_m=0.01, fill_m=0.5), now=T0 + 1)
    assert e["inputs"]["styles"][E.TAKER]["net_pp"] < 0
    assert e["recommendation"] == E.REST_LIMIT
    assert e["expected_net_executable_edge_pp"] > 0
    assert e["expected_executable_ev_usd"] > 0


@pytest.mark.parametrize("rec", list(E.EXECUTING))
@pytest.mark.parametrize("net,ev,fill", [
    (0.0, 10.0, 0.9), (-0.01, 10.0, 0.9), (0.02, 0.0, 0.9),
    (0.02, -1.0, 0.9), (0.02, 5.0, 0.0), (None, 5.0, 0.9),
    (0.02, None, 0.9), (0.02, 5.0, None)])
def test_enforce_hard_rule_never_lets_an_execution_through(rec, net, ev,
                                                           fill):
    got, overridden = E.enforce_hard_rule(rec, net_pp=net, ev_usd=ev,
                                          fill_p=fill)
    assert overridden is True and got in (E.WAIT, E.SKIP)
    assert E.enforce_hard_rule(rec, net_pp=0.01, ev_usd=0.5,
                               fill_p=0.5) == (rec, False)
    assert E.enforce_hard_rule(E.SKIP, net_pp=None, ev_usd=None,
                               fill_p=None) == (E.SKIP, False)


def test_the_outcome_compares_predicted_with_realized_loss():
    est = {"estimate_id": "eex:t", "decision_id": "d", "holding_side": "LONG",
           "decided_at": T0, "expected_execution_loss_pp": 0.02,
           "unmeasured": {},
           "inputs": {"mid": 0.51, "best_acquisition": 0.52,
                      "naive_execution_loss_pp": 0.03}}
    fills = [{"fill_id": "f1", "qty": 600, "price": 0.52, "fee_usd": 6.0,
              "filled_at": T0 + 3, "us_market_slug": "m"},
             {"fill_id": "f2", "qty": 400, "price": 0.53, "fee_usd": 4.0,
              "filled_at": T0 + 4, "us_market_slug": "m"}]
    orders = [{"order_id": "o", "created_at": T0 + 1, "reserved_usd": 600.0}]
    after = {"bids": [{"px": {"value": "0.49"}, "qty": "1"}],
             "offers": [{"px": {"value": "0.51"}, "qty": "1"}]}
    o = E.outcome_of(est, orders, fills, after, now=T0 + 400)
    vwap = (600 * 0.52 + 400 * 0.53) / 1000
    assert o["fill_vwap"] == pytest.approx(vwap)
    assert o["realized_fees_pp"] == pytest.approx(0.01)
    assert o["realized_adverse_selection_pp"] == pytest.approx(0.51 - 0.50)
    assert o["realized_execution_loss_pp"] == pytest.approx(
        vwap - 0.51 + 0.01 + 0.01)
    assert o["predicted_execution_loss_pp"] == 0.02
    assert o["decision_to_submit_s"] == pytest.approx(1.0)
    assert o["submit_to_ack_s"] is None
    assert o["unmeasured"]["submit_to_ack"]
    assert E.outcome_of(est, orders, [], None, now=T0) is None


def test_the_scorecard_is_unavailable_until_measured():
    m = E.summarise_metrics([], [], {})
    for name, x in m["metrics"].items():
        assert x["value"] is None and x["numerator"] is None, name
        assert x["status"] == "UNAVAILABLE" and x["why"], name
    assert set(m["metrics"]) >= {
        "edge_preservation_pct", "decision_to_submit_s", "submit_to_ack_s",
        "ack_to_fill_s", "fill_rate", "fill_probability_calibration",
        "slippage_pp", "slippage_saved_lost_pp", "spread_captured_pp",
        "price_improvement_pp", "adverse_selection_pp", "execution_alpha_pp",
        "cancel_replace_effectiveness", "capital_hours_consumed",
        "capital_hours_saved", "incremental_pnl_vs_naive_usd"}
    # a SKIP that filled at a loss is a counterfactual saving
    ests = [{"estimate_id": "a", "recommendation": E.SKIP,
             "expected_fill_probability": 0.9, "expected_slippage_pp": 0.0,
             "limit_price": 0.56}]
    outs = [{"estimate_id": "a", "recommendation": E.SKIP,
             "theoretical_edge_pp": 0.01, "realized_execution_loss_pp": 0.03,
             "filled_qty": 100.0, "fill_vwap": 0.55,
             "realized_slippage_pp": 0.0, "realized_spread_cost_pp": 0.01,
             "naive_execution_loss_pp": 0.03,
             "predicted_execution_loss_pp": 0.02, "capital_hours": 1.5}]
    m = E.summarise_metrics(ests, outs, {"a": True})
    assert m["metrics"]["incremental_pnl_vs_naive_usd"]["value"] == \
        pytest.approx(2.0)
    assert m["metrics"]["fill_rate"]["value"] == 1.0
    assert m["metrics"]["capital_hours_saved"]["value"] == 1.5
    assert m["metrics"]["fill_probability_calibration"]["value"] is None


# ════════════════════════════════════════════════════════════════════
# THE DATABASE
# ════════════════════════════════════════════════════════════════════

async def _tx():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    return conn, tx


def _row(e):
    cols = list(E._COLS) + ["estimated_at", "unmeasured", "dimensions",
                            "inputs", "evidence_refs"]
    return cols


@pg
@pytest.mark.asyncio
async def test_the_database_refuses_an_executing_row_without_positive_ev():
    import asyncpg
    conn, tx = await _tx()
    try:
        did = await conn.fetchval("SELECT decision_id FROM paper_decisions "
                                  " WHERE verdict='ENTER' LIMIT 1")
        if did is None:
            pytest.skip("no paper candidate in the template database")
        for net, ev in ((-0.01, 5.0), (0.02, 0.0), (None, 5.0),
                        (0.02, None)):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO eddie_execution_estimates (estimate_id, "
                    " decision_id, estimator_version, estimated_at, "
                    " theoretical_edge_pp, expected_net_executable_edge_pp, "
                    " expected_executable_ev_usd, expected_fill_probability,"
                    " recommendation, recommendation_reason, unmeasured, "
                    " evidence_refs) VALUES ('eex:hr',$4,'T',"
                    " now(), 0.1, $1, $2, 0.9, 'EXECUTE_NOW', 'r', $3::jsonb,"
                    " jsonb_build_array(jsonb_build_object('kind', "
                    " 'paper_decisions', 'id', $4::text)))",
                    net, ev, json.dumps({k: "t" for k in (
                        "fees", "spread_cost", "slippage",
                        "adverse_selection", "time_to_fill",
                        "capital_hours", "max_executable_size",
                        "net_executable_edge")}), did)
            await sp.rollback()
        # an unnamed null is refused too
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO eddie_execution_estimates (estimate_id, "
                " decision_id, estimator_version, estimated_at, "
                " recommendation, recommendation_reason, evidence_refs) "
                " VALUES ('eex:u',$1,'T',now(),'WAIT','r',"
                " '[{\"kind\":\"paper_decisions\",\"id\":\"x\"}]')", did)
        await sp.rollback()
        # record_estimate refuses a forged executing estimate in code
        est = E.estimate(_cand(decision_id=did),
                         _book([(0.52, 5000)], [(0.50, 5000)]), _hist(),
                         now=time.time())
        forged = dict(est, recommendation=E.EXECUTE_NOW,
                      expected_executable_ev_usd=-1.0)
        got = await E.record_estimate(conn, forged)
        assert got["ok"] is False
        assert got["refusal"] == "HARD_RULE_VIOLATION_REFUSED"
        # the genuine one persists, once, fixed at creation
        got = await E.record_estimate(conn, est)
        assert got["ok"] and got["created"], got
        again = await E.record_estimate(conn, est)
        assert again["ok"] and again["created"] is False
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("UPDATE eddie_execution_estimates SET "
                               " recommendation='WAIT' WHERE estimate_id=$1",
                               est["estimate_id"])
        await sp.rollback()
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_runner_pass_estimates_reviews_and_heartbeats(monkeypatch):
    conn, tx = await _tx()
    try:
        if not await conn.fetchval(
                "SELECT count(*) FROM paper_decisions WHERE verdict='ENTER'"):
            pytest.skip("the template database holds no paper candidate")
        now = await conn.fetchval(
            "SELECT extract(epoch FROM max(decided_at)) + 60 FROM "
            " paper_decisions WHERE verdict='ENTER'")
        await R.ensure_identities(conn)
        s = await ER.pass_once(conn, now=float(now))
        assert s["phase_errors"] == {}, s
        assert s["status"] in ("ESTIMATES_RECORDED", "NO_NEW_CANDIDATE")
        assert s["authority"] == "SHADOW_ONLY"
        n = await conn.fetchval("SELECT count(*) FROM "
                                " eddie_execution_estimates")
        assert 0 < n <= ER.MAX_ESTIMATES_PER_PASS
        # THE HARD RULE HOLDS ON EVERY PERSISTED ROW
        bad = await conn.fetchval(
            "SELECT count(*) FROM eddie_execution_estimates WHERE "
            " recommendation IN ('EXECUTE_NOW','REST_LIMIT','SPLIT') AND NOT "
            " (expected_executable_ev_usd > 0 AND "
            "  expected_net_executable_edge_pp > 0)")
        assert bad == 0
        st = await R.status_of(conn, R.EDDIE)
        assert st["state"] in ("DECISION_RECORDED", "IDLE")
        assert st["runs"] >= 1
        # idempotent: a second pass writes no new estimate for the same
        # candidates
        s2 = await ER.pass_once(conn, now=float(now))
        assert not set(x["estimate_id"] for x in s2["estimated"]) & set(
            x["estimate_id"] for x in s["estimated"])
        # THE WORKFLOW: seven grounded steps, in order, per reviewed candidate
        rv = await W.reviews(conn, limit=3)
        assert rv, "a review is recorded"
        r = rv[0]
        assert r["steps_recorded"] == 7
        assert [x["step"] for x in r["steps"]] == [s for _, s, _ in W.STEPS]
        for step in r["steps"]:
            assert step["evidence_refs"] and step["question"] and \
                step["response"], step
            assert step["recorded_by"] == "POS_WORKFLOW"
            assert step["status"] in ("ANSWERED", "NO_RECORD",
                                      "NOT_APPLICABLE")
        # append-only and ordered, in the database
        import asyncpg
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("UPDATE pos_candidate_review_steps SET "
                               " response='x' WHERE review_id=$1 AND seq=1",
                               r["review_id"])
        await sp.rollback()
        sp = conn.transaction()
        await sp.start()
        await conn.execute("INSERT INTO pos_candidate_reviews (review_id, "
                           " decision_id, opened_at) VALUES ('pcr:t', "
                           " 'no-such', now())")
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute(
                "INSERT INTO pos_candidate_review_steps (review_id, seq, "
                " step, agent, question, status, evidence_refs, response, at)"
                " VALUES ('pcr:t', 2, 'KAREN_CHALLENGE', 'KAREN', 'q', "
                " 'NO_RECORD', '[{\"kind\":\"k\",\"id\":\"i\"}]', 'r', now())")
        await sp.rollback()
    finally:
        await tx.rollback()
        await conn.close()


@pytest.mark.asyncio
async def test_the_kill_switch_stops_the_loop_before_any_read(monkeypatch):
    monkeypatch.setenv("EDDIE_RUNNER_ENABLED", "0")
    assert ER.enabled() is False

    async def no_pool():
        raise AssertionError("the disabled runner touched the database")
    await ER.run(no_pool, first_delay_s=0)
    monkeypatch.setenv("EDDIE_RUNNER_ENABLED", "1")
    assert ER.enabled() is True


@pytest.mark.asyncio
async def test_a_failing_phase_is_isolated_and_never_raises():
    class Boom:
        async def fetchval(self, *a, **k):
            if "to_regclass('eddie_execution_estimates')" in a[0]:
                return True
            raise RuntimeError("db down")

        async def fetch(self, *a, **k):
            raise RuntimeError("db down")

        async def fetchrow(self, *a, **k):
            raise RuntimeError("db down")

        async def execute(self, *a, **k):
            raise RuntimeError("db down")

        def transaction(self):
            raise RuntimeError("db down")
    s = await ER.pass_once(Boom(), now=T0)
    assert s["status"] == "FAILED"
    assert s["phase_errors"]


# the interface the other streams read (pos-twin: pos_iface_* views with
# these exact columns; pos-learn: eddie_execution_estimates by name)
TWIN_EDDIE = ("decision_id", "at", "qty", "baseline_vwap", "eddie_vwap",
              "baseline_fee_usd", "eddie_fee_usd")
TWIN_SCOUT = ("feature_id", "decision_id", "at", "p_with", "p_without",
              "status")
LEARN_EDDIE = ("us_market_slug", "execution_uncertainty", "fill_probability",
               "estimated_at")


@pg
@pytest.mark.asyncio
async def test_the_interface_views_carry_the_contracted_columns():
    conn, tx = await _tx()
    try:
        def cols(rows):
            return {r["column_name"] for r in rows}
        q = ("SELECT column_name FROM information_schema.columns WHERE "
             " table_name=$1")
        assert set(TWIN_EDDIE) <= cols(await conn.fetch(
            q, "pos_iface_eddie_execution"))
        assert {"baseline_fill_ratio", "eddie_fill_ratio"} <= cols(
            await conn.fetch(q, "pos_iface_eddie_execution"))
        assert set(TWIN_SCOUT) <= cols(await conn.fetch(
            q, "pos_iface_scout_feature_effects"))
        assert set(LEARN_EDDIE) <= cols(await conn.fetch(
            q, "eddie_execution_estimates"))
        if not await conn.fetchval(
                "SELECT count(*) FROM paper_decisions WHERE verdict='ENTER'"):
            return
        now = await conn.fetchval(
            "SELECT extract(epoch FROM max(decided_at)) + 60 FROM "
            " paper_decisions WHERE verdict='ENTER'")
        await R.ensure_identities(conn)
        await ER.pass_once(conn, now=float(now))
        rows = await conn.fetch("SELECT v.*, e.fill_probability, "
                                " e.execution_uncertainty, "
                                " e.expected_fill_probability FROM "
                                " pos_iface_eddie_execution v JOIN "
                                " eddie_execution_estimates e USING "
                                " (estimate_id)")
        assert rows
        for r in rows:
            if r["recommendation"] not in E.EXECUTING:
                assert r["eddie_vwap"] is None and r["eddie_fee_usd"] is None
            assert r["baseline_vwap"] is None or 0 < r["baseline_vwap"] < 1
            if r["expected_fill_probability"] is not None:
                assert r["execution_uncertainty"] == pytest.approx(
                    1 - r["expected_fill_probability"])
    finally:
        await tx.rollback()
        await conn.close()
