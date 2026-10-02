"""Owner-authorized main paper account: $1k entries, cash-bounded portfolio."""
from copy import deepcopy
from decimal import Decimal
from unittest.mock import AsyncMock
import pytest
from sportsassets import bettor_paper_limits as P
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_session as S
from sportsassets.agents import paper_explore as E, paper_derek as D
from tests import paper_harness as H


def legacy_config():
    return {"risk": {"per_order_cap_usd": 5000, "per_market_cap_usd": 10000,
                     "per_fixture_cap_usd": 15000, "max_concurrent_groups": 150,
                     "hedge_reserve_fraction": .2},
            "entry": {"target_order_usd": 5000, "pinnacle_max_age_s": 30}}


def test_effective_policy_preserves_frozen_config_and_other_accounts():
    old = legacy_config(); before = deepcopy(old)
    new = P.effective_config(old, P.ACCOUNT_ID)
    assert old == before
    assert new["entry"]["target_order_usd"] == 1000
    assert new["entry"]["pinnacle_max_age_s"] == 30
    assert new["risk"]["per_order_cap_usd"] is None
    for key in ("per_market_cap_usd", "per_fixture_cap_usd", "max_concurrent_groups"):
        assert new["risk"][key] is None
    assert new["risk"]["hedge_reserve_fraction"] == 0
    assert new["capital_policy"]["version"] == P.VERSION
    assert P.effective_config(old, "other") == before
    assert P.describe("funded_account") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("available,reserve,role,refusal", [
    (1000, 1000, "ENTRY", None),
    (999, 1000, "ENTRY", L.R_INSUFFICIENT),
    (500000, 1001, "ENTRY", None),
    (500000, 4900, "ENTRY", None),
    (500000, 4900, "HEDGE", None)])
async def test_locked_accounting_ignores_old_caps_but_never_overspends(
        monkeypatch, available, reserve, role, refusal):
    # These legacy admission queries MUST NOT run for the main account.
    monkeypatch.setattr(L, "_open_groups", AsyncMock(side_effect=AssertionError))
    monkeypatch.setattr(L, "_exposure", AsyncMock(side_effect=AssertionError))
    order = {"account_id": P.ACCOUNT_ID, "role": role,
             "us_market_slug": "m", "fixture": "f", "group_id": "g"}
    got = await L._check_caps(None, order, reserve=Decimal(reserve),
                             cs={"cash": Decimal(500000), "available": Decimal(available)},
                             caps=legacy_config()["risk"])
    assert (got or {}).get("refusal") == refusal


@pytest.mark.asyncio
async def test_losses_and_exposure_remain_measured_without_admission_stops(monkeypatch):
    conn = AsyncMock(); conn.fetchval.return_value = 20000
    monkeypatch.setattr(E, "_groups", AsyncMock(return_value={"g"}))
    monkeypatch.setattr(L, "positions", AsyncMock(return_value=[
        {"group_id": "g", "cost_basis_usd": 20000, "open_qty": 100,
         "realized_pnl_usd": -5000}]))
    monkeypatch.setattr(L, "cash_state", AsyncMock(return_value={"available": Decimal(450000)}))
    got = await E.limits_state(conn, P.ACCOUNT_ID)
    assert got["exposure_usd"] == 40000 and got["realized_losses_usd"] == 5000
    assert got["headroom_usd"] == 450000 and not got["loss_stop_reached"]
    assert got["limits"]["loss_stop_usd"] is None
    assert got["limits"]["max_aggregate_exposure_usd"] is None


def test_session_view_reports_effective_policy_beside_original_sha():
    row = {"session_id": "paper_s", "account_id": P.ACCOUNT_ID,
           "started_at": None, "config": legacy_config(), "config_sha": "frozen",
           "simulator_version": "v1", "reporting_tz": "UTC", "status": "ACTIVE"}
    got = S.session_view(row)
    assert got["config"] == legacy_config() and got["config_sha"] == "frozen"
    assert got["effective_config"]["entry"]["target_order_usd"] == 1000
    assert got["capital_policy"]["version"] == P.VERSION


@pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
@pytest.mark.asyncio
async def test_legacy_order_above_target_is_preserved(monkeypatch):
    from sportsassets import bettor_paper_simulator as SIM
    conn = await H.connect()
    try:
        acct = await H.new_account(conn, "legacy4900")
        order = H.order(acct, key="legacy", qty=9800, limit=.5)
        old = await L.submit_order(conn, order, caps=legacy_config()["risk"],
                                   fee_fn=H.flat_fee(0), now=H.T0)
        assert old["ok"]
        monkeypatch.setattr(P, "ACCOUNT_ID", acct["account_id"])
        result = await SIM.simulate_order(conn, old["order"]["order_id"],
                                          now=H.T0 + 5, fee_fn=H.flat_fee(0))
        assert await conn.fetchval("SELECT state FROM paper_orders WHERE order_id=$1", old["order"]["order_id"]) in L.OPEN_STATES
        balance = await L.balances(conn, acct["account_id"], now=H.T0 + 5)
        assert balance["cash_usd"] == 500000 and balance["reserved_usd"] == 4900
        assert balance["ledger_consistent"]
        assert await conn.fetchval("SELECT count(*) FROM paper_fills WHERE order_id=$1",
                                   old["order"]["order_id"]) == 0
    finally:
        await conn.close()


@pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
@pytest.mark.asyncio
async def test_real_ledger_allows_six_1000_reservations_without_reset(monkeypatch):
    conn = await H.connect()
    try:
        acct = await H.new_account(conn, "capital1000")
        monkeypatch.setattr(P, "ACCOUNT_ID", acct["account_id"])
        original = await S.active_session(conn, acct["account_id"])
        for i in range(6):
            order = H.order(acct, key="entry%d" % i, slug="market%d" % i,
                            fixture="same-fixture", qty=2000, limit=.5)
            got = await L.submit_order(conn, order, caps=legacy_config()["risk"],
                                       fee_fn=H.flat_fee(0), now=H.T0)
            assert got["ok"], got
            again = await L.submit_order(conn, order, caps=legacy_config()["risk"],
                                         fee_fn=H.flat_fee(0), now=H.T0)
            assert again["duplicate"]
        balances = await L.balances(conn, acct["account_id"], now=H.T0)
        assert balances["cash_usd"] == 500000
        assert balances["reserved_usd"] == 6000
        assert balances["ledger_consistent"]
        resumed = await S.active_session(conn, acct["account_id"])
        assert resumed["session_id"] == original["session_id"]
        assert resumed["config_sha"] == original["config_sha"]
        assert resumed["effective_config"]["entry"]["target_order_usd"] == 1000
    finally:
        await conn.close()


@pytest.fixture
def cg_only():
    """Strategy switches flip SYNCHRONOUSLY, before the async proof runs
    (the helper uses its own event loop), and are put back afterwards."""
    from tests import test_paper_exploration_maker_and_throughput as T
    from sportsassets.agents import paper_benchmark as B
    T._only(B.CG_STRATEGY)
    yield
    T._only(B.CG_STRATEGY, B.EXPLORE_STRATEGY)


@pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
@pytest.mark.asyncio
async def test_completed_game_uses_1000_with_an_existing_5000_session(monkeypatch, cg_only):
    import time
    from tests import paper_live_fixture as PL
    from tests import test_paper_exploration_maker_and_throughput as T
    from sportsassets.agents import paper_benchmark as B
    conn = await H.connect(); now = time.time() + 5
    monkeypatch.setenv(B.ENV_FLAG, "on")
    monkeypatch.setenv(S.ENV_FLAG, "on")
    try:
        acct, transport = await T._setup(conn, "capital_cg", now)
        monkeypatch.setattr(P, "ACCOUNT_ID", acct["account_id"])
        frozen = await S.active_session(conn, acct["account_id"])
        assert frozen["config"]["entry"]["target_order_usd"] == 5000
        valuation = await PL.valuation(conn, decided_at=now - 10, p_pin=.70,
                                        compatibility="INCOMPATIBLE")
        transport.set(valuation["slug"], offers=[(.50, 5000)], bids=[(.48, 5000)])
        client = PL.client(transport)
        result = await T._pass(conn, acct, transport, now, client)
        assert not result["errors"], result
        decision = await T._dec(conn, acct, valuation["valuation_id"], B.CG_STRATEGY)
        assert decision["verdict"] == "ENTER", dict(decision)
        assert H.j(decision["provenance"])["capital_policy"]["version"] == P.VERSION
        order = await conn.fetchrow("SELECT * FROM paper_orders WHERE decision_id=$1",
                                    decision["decision_id"])
        assert 990 < float(order["reserved_usd"]) <= 1000
        result = await T._pass(conn, acct, transport, now + 5, client)
        assert not result["errors"], result
        cost = await conn.fetchval("SELECT -sum(cash_delta_usd) FROM paper_ledger "
                                   "WHERE order_id=$1 AND kind='FILL'", order["order_id"])
        assert 990 < float(cost) <= 1000
        resumed = await S.active_session(conn, acct["account_id"])
        assert resumed["config_sha"] == frozen["config_sha"]
        assert resumed["session_id"] == frozen["session_id"]
        assert client.mutation_attempts == 0
    finally:
        await PL.purge_everything(conn)
        await conn.close()


@pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
@pytest.mark.asyncio
async def test_a_strategy_does_not_re_enter_the_contract_it_holds(monkeypatch):
    """Owner policy: other contracts on the same fixture, and other
    strategies, are allowed; the SAME strategy re-entering the SAME contract
    and side it holds is refused under the lock (an add, not a new initial
    position). It is allowed again once nothing is working or open there."""
    conn = await H.connect()
    try:
        acct = await H.new_account(conn, "samecontract")
        monkeypatch.setattr(P, "ACCOUNT_ID", acct["account_id"])
        caps = legacy_config()["risk"]

        def order(key, slug, strategy="PINNACLE_EXPLORATION_PAPER"):
            o = H.order(acct, key=key, slug=slug, fixture="one-fixture",
                        qty=2000, limit=.5)
            o["strategy"] = strategy
            return o

        first = await L.submit_order(conn, order("a1", "contract-a"), caps=caps,
                                     fee_fn=H.flat_fee(0), now=H.T0)
        assert first["ok"], first
        again = await L.submit_order(conn, order("a2", "contract-a"), caps=caps,
                                     fee_fn=H.flat_fee(0), now=H.T0)
        assert again["ok"] is False
        assert again["refusal"] == L.R_SAME_CONTRACT_HELD and again["under_lock"]
        assert again["by"][0]["group_id"] == first["order"]["group_id"]
        other_contract = await L.submit_order(conn, order("b1", "contract-b"),
                                              caps=caps, fee_fn=H.flat_fee(0),
                                              now=H.T0)
        assert other_contract["ok"], other_contract
        other_strategy = await L.submit_order(
            conn, order("c1", "contract-a", "PINNACLE_COMPLETED_GAME_PAPER"),
            caps=caps, fee_fn=H.flat_fee(0), now=H.T0)
        assert other_strategy["ok"], other_strategy
        # the repeated identical order is still an idempotent duplicate
        dup = await L.submit_order(conn, order("a1", "contract-a"), caps=caps,
                                   fee_fn=H.flat_fee(0), now=H.T0)
        assert dup["duplicate"]
        # once the first entry is no longer working (and never filled),
        # a new entry on that contract is allowed
        await conn.execute("UPDATE paper_orders SET state='EXPIRED' "
                           "WHERE order_id=$1", first["order"]["order_id"])
        later = await L.submit_order(conn, order("a3", "contract-a"), caps=caps,
                                     fee_fn=H.flat_fee(0), now=H.T0)
        assert later["ok"], later
        assert P.describe(acct["account_id"])["same_strategy_same_contract"]\
            .startswith("NOT_RE_ENTERED")
    finally:
        await conn.close()


@pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
@pytest.mark.asyncio
async def test_two_concurrent_entries_on_one_contract_exactly_one_wins(monkeypatch):
    """CONCURRENCY: two connections submit the same strategy/contract/side
    at once. The account lock serializes them; the second sees the first's
    order under the lock and is refused, so exactly one reservation exists."""
    import asyncio
    c0 = await H.connect()
    c1, c2 = await H.connect(), await H.connect()
    try:
        acct = await H.new_account(c0, "concurrent")
        monkeypatch.setattr(P, "ACCOUNT_ID", acct["account_id"])
        caps = legacy_config()["risk"]

        def order(key):
            o = H.order(acct, key=key, slug="contract-x", fixture="fx-c",
                        qty=2000, limit=.5)
            o["strategy"] = "PINNACLE_EXPLORATION_PAPER"
            return o

        for _ in range(5):          # repeat to exercise both interleavings
            await c0.execute("UPDATE paper_orders SET state='EXPIRED' "
                             "WHERE account_id=$1 AND state <> 'EXPIRED'",
                             acct["account_id"])
            n = await c0.fetchval("SELECT count(*) FROM paper_orders "
                                  "WHERE account_id=$1", acct["account_id"])
            a, b = await asyncio.gather(
                L.submit_order(c1, order("k%da" % n), caps=caps,
                               fee_fn=H.flat_fee(0), now=H.T0),
                L.submit_order(c2, order("k%db" % n), caps=caps,
                               fee_fn=H.flat_fee(0), now=H.T0))
            oks = [g for g in (a, b) if g.get("ok")]
            refused = [g for g in (a, b) if not g.get("ok")]
            assert len(oks) == 1 and len(refused) == 1, (a, b)
            assert refused[0]["refusal"] == L.R_SAME_CONTRACT_HELD
            assert refused[0]["under_lock"] is True
            live = await c0.fetchval(
                "SELECT count(*) FROM paper_orders WHERE account_id=$1 AND "
                "state = ANY($2::text[])", acct["account_id"],
                list(L.OPEN_STATES))
            assert live == 1
        bal = await L.balances(c0, acct["account_id"], now=H.T0)
        assert bal["ledger_consistent"]
    finally:
        for c in (c0, c1, c2):
            await c.close()


@pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
@pytest.mark.asyncio
async def test_re_entry_is_refused_while_open_and_allowed_after_closure(monkeypatch):
    """A FILLED entry with open inventory blocks a new entry on that contract
    and side; once the position is sold to zero, a new entry is allowed.
    $1,000 stays a target: the third entry is $1,500 and is accepted."""
    from sportsassets import bettor_paper_simulator as SIM
    conn = await H.connect()
    try:
        acct = await H.new_account(conn, "reentry")
        monkeypatch.setattr(P, "ACCOUNT_ID", acct["account_id"])
        caps = legacy_config()["risk"]
        strat = "PINNACLE_EXPLORATION_PAPER"
        slug = "contract-r"

        def entry(key, qty, at):
            o = H.order(acct, key=key, slug=slug, fixture="fx-r", qty=qty,
                        limit=.5, at=at)
            o["strategy"] = strat
            return o

        first = await L.submit_order(conn, entry("e1", 100, H.T0), caps=caps,
                                     fee_fn=H.zero_fee, now=H.T0)
        assert first["ok"], first
        await H.observe(conn, slug, H.T0 + 2.5, offers=[(0.50, 100)])
        r = await SIM.simulate_order(conn, first["order"]["order_id"],
                                     now=H.T0 + 3, fee_fn=H.zero_fee)
        assert r["state"] == "FILLED", r
        held = await L.submit_order(conn, entry("e2", 100, H.T0 + 4),
                                    caps=caps, fee_fn=H.zero_fee, now=H.T0 + 4)
        assert held["refusal"] == L.R_SAME_CONTRACT_HELD
        assert held["by"][0]["state"] == "OPEN_POSITION"
        # close the position: sell the 100 held contracts
        gid = first["order"]["group_id"]
        sell = H.order(acct, key="x1", slug=slug, fixture="fx-r", qty=100,
                       limit=.40, direction="SELL", role="EXIT",
                       group_id=gid, at=H.T0 + 5)
        sell["strategy"] = strat
        s = await L.submit_order(conn, sell, caps=caps, fee_fn=H.zero_fee,
                                 now=H.T0 + 5)
        assert s["ok"], s
        await H.observe(conn, slug, H.T0 + 7.5, bids=[(0.52, 100)])
        r = await SIM.simulate_order(conn, s["order"]["order_id"],
                                     now=H.T0 + 8, fee_fn=H.zero_fee)
        assert r["state"] == "FILLED", r
        assert not [p for p in await L.positions(conn, acct["account_id"])
                    if p["group_id"] == gid and p["open_qty"] > 1e-9]
        again = await L.submit_order(conn, entry("e3", 3000, H.T0 + 9),
                                     caps=caps, fee_fn=H.zero_fee,
                                     now=H.T0 + 9)
        assert again["ok"], again
        assert float(again["order"]["reserved_usd"]) == pytest.approx(1500.0)
        bal = await L.balances(conn, acct["account_id"], now=H.T0 + 9)
        assert bal["ledger_consistent"]
    finally:
        await conn.close()
