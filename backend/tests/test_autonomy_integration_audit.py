"""Regression cases found by tracing the real integration, not module names."""
import asyncio
from contextlib import asynccontextmanager

import pytest

from sportsassets import bettor_desk as dk
from sportsassets import bettor_desk_loop as loop
from sportsassets import bettor_mgmt_lifecycle as lc
from sportsassets import bettor_rn1x_policy as policy
from sportsassets import bettor_rn1x_run as runner


def fee(qty, price):
    return qty * price * 0.01


def managed():
    return lc.Managed(condition_id="c", outcome_index=0, seed_qty=100,
                      seed_price=0.60, at=100, fee_fn=fee, queue_share=1)


def trade(i, leg, px, ts, detected=None, size=100):
    return dict(id=i, whale_id=1, outcome_index=leg, side="BUY", size=size,
                price=px, ts=ts, detected_at=ts if detected is None else detected)


def replay(rows, **kw):
    return runner.run(rows=rows, source_whale_id=1, condition_id="c",
                      fee_fn=fee, fee_basis="CONTROLLED_FEE", queue_share=1,
                      initial_inventory_verified=True, **kw)


def test_management_policy_places_pair_order_and_preserves_queue_priority():
    m = managed()
    first = m.manage_policy(at=101, decision_id="one")
    assert first["acted"] and first["selected_action"] == "POST_COMPLEMENT"
    o = m.open_orders()[0]
    assert o.limit_price + .60 + fee(100, o.limit_price) / 100 <= .91
    second = m.manage_policy(at=102, decision_id="two")
    assert not second["acted"] and second["order_id"] == o.order_id
    assert len(m.orders) == 1
    assert not first["phase"]["loss_exit_available"]


def test_replacement_waits_for_cancel_and_recalculates_residual():
    m = managed()
    a = m.place("POST_COMPLEMENT", at=101, price=.30, qty=100, decision_id="a")
    b = m.place("DIRECT_EXIT", at=102, price=.40, qty=100, decision_id="b")
    assert b["refused"] == "WAIT_CANCEL_ACK" and b["placed"] is None
    assert len(m.open_orders()) == 1
    fills = m.on_print(at=103, outcome_index=1, price=.30, size=40, evidence_id="x")
    assert fills[0]["raced_a_pending_cancel"]
    m.acknowledge_cancels(104)
    b = m.place("DIRECT_EXIT", at=105, price=.40, qty=100, decision_id="b-new")
    assert b["qty"] == 60
    m.on_print(at=106, outcome_index=0, price=.40, size=100, evidence_id="y")
    assert m.matched() == 40 and m.residual() == 0
    assert m.state()["invariant"]["ok"]
    assert m.orders[a["placed"]].state == dk.CANCELLED


@pytest.mark.parametrize("at", [100, 101, 1001])
def test_print_before_placement_at_placement_or_at_expiry_cannot_fill(at):
    m = managed()
    m.place("POST_COMPLEMENT", at=101, price=.30, qty=100, decision_id="a")
    assert m.on_print(at=at, outcome_index=1, price=.30, size=100,
                      evidence_id=str(at)) == []
    assert m.matched() == 0


def test_loss_exit_is_gated_by_observed_phase(monkeypatch):
    # Controlled mapping only; no production sport/feed is invented.
    monkeypatch.setitem(policy.SECOND_HALF_MAPPING, "TEST", lambda p: p["phase"])
    m = managed()
    first = m.manage_policy(at=101, decision_id="a", sport="TEST",
                            progress={"phase": policy.FIRST_HALF}, bid=.40, bid_size=100)
    assert first["selected_action"] == "POST_COMPLEMENT"
    second = m.manage_policy(at=102, decision_id="b", sport="TEST",
                             progress={"phase": policy.SECOND_HALF}, bid=.40, bid_size=100)
    assert second["selected_action"] == "DIRECT_EXIT"
    assert second["operating_state"] == "WAIT_CANCEL_ACK"
    m.acknowledge_cancels(103)
    third = m.manage_policy(at=104, decision_id="c", sport="TEST",
                            progress={"phase": policy.SECOND_HALF}, bid=.40, bid_size=100)
    assert third["acted"] and m.open_orders()[0].side == "SELL"


def test_runner_uses_existing_policy_and_real_portfolio_end_to_end():
    out = replay([trade(1, 0, .60, 100), trade(2, 1, .30, 102)],
                 payouts={"0": 1, "1": 0}, resolved_at=200)
    assert out["policy"]["policy_id"] == policy.POLICY_ID
    assert out["steps"]["MANAGE"]["events"][0]["fills"][0]["qty"] == 100
    assert out["accounting"]["reconciles"]
    assert out["accounting"]["realized_pnl_usd"] == pytest.approx(9.70)
    assert out["steps"]["COMPARE"]["hold_to_settlement_net_usd"] == 40


def test_late_received_print_before_order_is_not_a_fill():
    out = replay([trade(1, 0, .60, 100, 110), trade(2, 1, .30, 105, 120)])
    assert out["accounting"]["matched_qty"] == 0
    assert out["accounting"]["residual_qty"] == 100
    assert not out["prospective"] and out["mode"] == runner.HISTORICAL


def test_print_is_not_an_executable_bid_for_loss_exit():
    out = replay([trade(1, 0, .60, 100), trade(2, 0, .10, 200)])
    assert all(o["side"] == "BUY" for o in out["final_state"]["all_orders"])
    assert out["accounting"]["residual_qty"] == 100


def test_unknown_starting_inventory_is_not_assumed_flat():
    out = runner.run(rows=[trade(1, 0, .60, 100)], source_whale_id=1)
    assert out["failed_step"] == "CLASSIFY"
    assert out["steps"]["CLASSIFY"]["kind"] == "UNKNOWN"


def test_duplicate_source_events_do_not_change_results():
    rows = [trade(1, 0, .60, 100), trade(2, 1, .30, 102, size=40)]
    assert replay(rows) == replay(rows + [rows[1]])


def test_default_fee_schedule_loads_without_import_error():
    assert runner._fee_fn()(qty=100, price=.50) > 0


@pytest.mark.asyncio
async def test_standby_retries_lock_and_reaches_startup_without_a_new_deploy(monkeypatch):
    acquired = iter([False, True])
    calls = []

    async def acquire(conn):
        calls.append("lock")
        return next(acquired)

    async def sleep(_):
        if len(calls) != 1:
            raise AssertionError("standby idled without retrying the lock")

    async def ensure(*args, **kwargs):
        calls.append("startup")
        raise asyncio.CancelledError

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield object()

    async def get_pool():
        return Pool()

    monkeypatch.setattr(loop, "enabled", lambda: True)
    monkeypatch.setattr(loop, "_acquire", acquire)
    monkeypatch.setattr(loop.asyncio, "sleep", sleep)
    monkeypatch.setattr(loop.ACC, "ensure_account", ensure)
    with pytest.raises(asyncio.CancelledError):
        await loop.run(get_pool)
    assert calls == ["lock", "lock", "startup"]
