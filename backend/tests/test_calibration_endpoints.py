"""The calibration limits at the DEPLOYED ENTRY POINTS.

test_calibration_budget.py proves the arithmetic. That is not the same
claim. The arithmetic was right before this module existed and it lived
in a dict that died with the request -- a restart would have reported $0
spent and a free lifecycle slot while a real order was still resting.

So these tests drive the HTTP routes, with a fake asyncpg pool standing
in for the database, and check the properties an operator actually
depends on: an unreadable ledger is a 503 and never a fresh budget; the
stop is durable and refuses admission; a refusal comes back NAMED with a
409; approval takes a reserve and sends nothing.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from sportsassets import calibration as cal
from sportsassets.api import app as app_mod

ADMIN = "admin-token-for-tests"


class _Settings:
    admin_token = ADMIN
    desk_password = "letmein"
    pmus_key_id = ""
    pmus_secret_key = ""


class FakePool:
    """Enough asyncpg for calibration_store, keyed off SQL keywords."""

    def __init__(self, session=None, lifecycles=None, broken=False):
        self.session = session
        self.lifecycles = list(lifecycles or [])
        self.broken = broken
        self.executed = []

    # -- reads ------------------------------------------------------
    async def fetchrow(self, sql, *a):
        if self.broken:
            raise RuntimeError("connection reset")
        if "FROM calibration_sessions" in sql:
            return self.session
        return None

    async def fetch(self, sql, *a):
        if self.broken:
            raise RuntimeError("connection reset")
        if "FROM calibration_lifecycles" in sql:
            return list(self.lifecycles)
        return []

    # -- writes -----------------------------------------------------
    async def execute(self, sql, *a):
        if self.broken:
            raise RuntimeError("connection reset")
        self.executed.append((sql, a))
        if "INSERT INTO calibration_sessions" in sql and self.session is None:
            self.session = _session_row()
        if "INSERT INTO calibration_lifecycles" in sql:
            if any(lc["state"] in cal.OPEN_STATES for lc in self.lifecycles):
                raise RuntimeError("UniqueViolationError: "
                                   "calibration_one_open_lifecycle")
            self.lifecycles.append(_lifecycle_row(a))
        if "UPDATE calibration_sessions" in sql and "stopped = true" in sql:
            self.session = dict(self.session, stopped=True, stopped_by=a[1],
                                stop_reason=a[2])
        return "UPDATE 1"


def _session_row(**over):
    row = {
        "session_id": "MICRO-EXEC-CAL-1",
        "experiment": cal.EXPERIMENT,
        "authorised_by": cal.AUTHORISED_BY,
        "max_all_in_usd": 5.00,
        "max_spend_usd": 100.00,
        "max_open": 1,
        "spent_usd": 0.0,
        "venue": "polymarket-us",
        "environment": "PRODUCTION",
        "stopped": False,
        "stopped_at": None, "stopped_by": None, "stop_reason": None,
    }
    row.update(over)
    return row


def _lifecycle_row(args):
    return {
        # POSITIONS, and they moved when `environment` joined the INSERT at
        # $4 (migration 067). Keeping them in step with the statement is the
        # point of this helper: a stale index here reads the order type as
        # the price and the failure looks like a conversion error.
        "client_order_id": args[1], "venue": args[2], "environment": args[3],
        "market_id": args[5],
        "outcome": args[6], "side": args[7], "quantity": args[10],
        "price": args[9], "reserve_usd": args[13], "all_in_usd": args[13],
        "spent_usd": 0.0, "state": "APPROVED", "venue_order_id": None,
        "venue_terminal_state": None, "fills_reconciled": False,
        "created_at": None, "updated_at": None,
    }


def ticket(**over):
    t = {
        "venue": "polymarket-us", "environment": "PRODUCTION",
        "account": "bettortoken-main",
        "marketId": "aec-atp-xxx-yyy-2026-09-18", "outcome": "XXX to win",
        "side": "BUY", "orderType": "LIMIT_GTC_POST_ONLY",
        "clientOrderId": "CAL-0001", "expiry": "2026-09-18T23:00:00Z",
        "price": 0.40, "quantity": 10, "tick": 0.01, "venueMinQuantity": 1,
        "entryFeeReserve": 0.20, "exitFeeReserve": 0.40,
        "feeModel": "venue schedule 2026-09, conservative upper bound",
        "inventoryPlan": "hold to settlement; no re-entry",
        "operatorStop": "POST /api/calibration/stop",
        "stateFresh": True,
    }
    t.update(over)
    return t


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_mod, "settings", lambda: _Settings())
    return TestClient(app_mod.app)


@pytest.fixture
def pool(monkeypatch):
    p = FakePool(session=_session_row())

    async def _get_pool():
        return p
    import sportsassets.db as db
    import sportsassets.calibration_store as store
    monkeypatch.setattr(db, "get_pool", _get_pool)
    monkeypatch.setattr(store, "get_pool", _get_pool)
    return p


@pytest.fixture
def writes_enabled(monkeypatch):
    """Lift the BOUNDED MONITORING RELEASE gate for one test.

    The gate is a release decision, not the budget's logic, and it has
    its own tests in TestTheMonitoringReleaseGate (including that its
    default really is off). The classes below are about the accounting
    -- the reserve, the concurrency index, the stop, the release rule --
    and they would be testing the gate instead of the budget if the gate
    stayed on. `require_calibration_writes` reads the module global at
    call time, so setting it here is enough.
    """
    monkeypatch.setattr(app_mod, "CALIBRATION_WRITES_ENABLED", True)


H = {"X-Admin-Token": ADMIN}


class TestTheRoutesAreGuarded:
    def test_every_calibration_route_refuses_an_anonymous_caller(self, client):
        assert client.get("/api/calibration/state").status_code == 401
        assert client.post("/api/calibration/preflight",
                           json={}).status_code == 401
        assert client.post("/api/calibration/approve",
                           json={}).status_code == 401
        assert client.post("/api/calibration/stop", json={}).status_code == 401


class TestAnUnreadableLedgerIsNotAFreshBudget:
    def test_state_answers_503_rather_than_zero_spent(self, client,
                                                      monkeypatch):
        broken = FakePool(broken=True)

        async def _get_pool():
            return broken
        import sportsassets.calibration_store as store
        monkeypatch.setattr(store, "get_pool", _get_pool)
        r = client.get("/api/calibration/state", headers=H)
        assert r.status_code == 503
        assert "UNREADABLE" in r.json()["detail"].upper()

    def test_a_missing_session_row_is_named_not_invented(self, client,
                                                         monkeypatch):
        empty = FakePool(session=None)
        # ensure_session's INSERT is what creates it; with that removed a
        # read must refuse rather than synthesise a fresh $100.
        import sportsassets.calibration_store as store

        async def _get_pool():
            return empty
        monkeypatch.setattr(store, "get_pool", _get_pool)

        async def _no_create(pool=None, session_id=store.SESSION_ID):
            return None
        monkeypatch.setattr(store, "ensure_session", _no_create)
        r = client.get("/api/calibration/state", headers=H)
        assert r.status_code == 503
        assert store.R_NO_SESSION in r.json()["detail"]


class TestPreflightAtTheRoute:
    def test_a_clean_ticket_is_admissible_but_not_submittable(self, client,
                                                             pool):
        r = client.post("/api/calibration/preflight",
                        json={"ticket": ticket(), "checks": {}}, headers=H)
        body = r.json()
        assert r.status_code == 200
        assert body["durable"] is True
        assert body["allInCost"] == 4.60
        # The budget is fine; the unproved checks are not.
        assert cal.R_CASH_UNKNOWN in body["refusals"]     # no venue configured
        assert body["submittable"] is False
        assert "cancellationPathVerified" in body["unprovedChecks"]

    def test_no_ev_is_invented_anywhere_in_the_response(self, client, pool):
        body = client.post("/api/calibration/preflight",
                           json={"ticket": ticket()}, headers=H).json()
        assert "netEv" not in json.dumps(body)


class TestApprovalTakesAReserveAndSendsNothing:
    def test_approval_must_confirm_the_exact_client_order_id(
            self, client, pool, writes_enabled):
        r = client.post("/api/calibration/approve",
                        json={"ticket": ticket(), "approved_by": "matt",
                              "confirm": "CAL-0002"}, headers=H)
        assert r.status_code == 400
        assert "NOT_CONFIRMED" in r.json()["detail"]

    def test_an_unattributed_approval_is_refused(self, client, pool,
                                                 writes_enabled, monkeypatch):
        monkeypatch.setattr(app_mod, "settings", lambda: _Settings())
        r = client.post("/api/calibration/approve",
                        json={"ticket": ticket(), "approved_by": "",
                              "confirm": "CAL-0001"}, headers=H)
        assert r.status_code == 409
        assert "APPROVAL_NOT_ATTRIBUTED" in r.json()["detail"]

    def test_a_refused_ticket_comes_back_named_with_409(self, client, pool,
                                                        writes_enabled):
        r = client.post("/api/calibration/approve",
                        json={"ticket": ticket(exitFeeReserve=None),
                              "approved_by": "matt", "confirm": "CAL-0001"},
                        headers=H)
        assert r.status_code == 409
        assert cal.R_FEES_UNBOUNDED in r.json()["detail"]

    def test_the_response_says_plainly_that_nothing_was_sent(
            self, client, pool, writes_enabled):
        r = client.post("/api/calibration/approve",
                        json={"ticket": ticket(), "approved_by": "matt",
                              "confirm": "CAL-0001"},
                        headers=H)
        # Cash is unknown in this harness, so the budget refuses it --
        # which is itself the point: an unfunded ticket never reserves.
        assert r.status_code == 409
        assert cal.R_CASH_UNKNOWN in r.json()["detail"]

    def test_a_funded_ticket_reserves_and_the_second_is_refused_by_the_index(
            self, client, pool, writes_enabled, monkeypatch):
        import sportsassets.api.pmus_account as PA

        async def _snap():
            return {"configured": True, "account": {"cash": 5000.0}}
        monkeypatch.setattr(PA, "account_snapshot", _snap)

        r = client.post("/api/calibration/approve",
                        json={"ticket": ticket(), "approved_by": "matt",
                              "confirm": "CAL-0001"}, headers=H)
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["submitted"] is False
        assert "NOTHING WAS SENT" in body["note"]
        assert body["budget"]["reserved"] == 4.60
        assert body["budget"]["remaining"] == 95.40

        # The in-process check catches the ordinary second ticket first.
        r2 = client.post("/api/calibration/approve",
                         json={"ticket": ticket(clientOrderId="CAL-0002"),
                               "approved_by": "matt", "confirm": "CAL-0002"},
                         headers=H)
        assert r2.status_code == 409
        assert cal.R_CONCURRENCY in r2.json()["detail"]

    def test_the_database_index_is_the_limit_when_the_read_was_stale(
            self, client, writes_enabled, monkeypatch):
        """THE RACE THE PYTHON CHECK CANNOT WIN.

        `cal.refusals` reads the session, then `reserve` inserts. Between
        those two moments another instance -- or a retried request --
        can insert its own lifecycle. The read that says "none open" is
        then already wrong, and a limit that lives only in that read is
        not a limit.

        Here the reads report an empty book (the stale view) while the
        INSERT raises the unique violation, which is exactly what the
        database does in that race. The refusal must still come back
        named, and it must say the index caught it.
        """
        import sportsassets.api.pmus_account as PA

        async def _snap():
            return {"configured": True, "account": {"cash": 5000.0}}
        monkeypatch.setattr(PA, "account_snapshot", _snap)

        class RacingPool(FakePool):
            async def execute(self, sql, *a):
                if "INSERT INTO calibration_lifecycles" in sql:
                    raise RuntimeError("UniqueViolationError: "
                                       "calibration_one_open_lifecycle")
                return await FakePool.execute(self, sql, *a)

        racing = RacingPool(session=_session_row())   # fetch() -> no rows

        async def _get_pool():
            return racing
        import sportsassets.calibration_store as store
        monkeypatch.setattr(store, "get_pool", _get_pool)

        r = client.post("/api/calibration/approve",
                        json={"ticket": ticket(), "approved_by": "matt",
                              "confirm": "CAL-0001"}, headers=H)
        assert r.status_code == 409, r.json()
        detail = r.json()["detail"]
        assert cal.R_CONCURRENCY in detail
        assert "database index" in detail
        assert "not a code check" in detail


class TestTheOperatorStop:
    def test_the_stop_is_durable_attributed_and_refuses_admission(
            self, client, pool, writes_enabled, monkeypatch):
        import sportsassets.api.pmus_account as PA

        async def _snap():
            return {"configured": True, "account": {"cash": 5000.0}}
        monkeypatch.setattr(PA, "account_snapshot", _snap)

        s = client.post("/api/calibration/stop",
                        json={"by": "matt", "reason": "halt the experiment"},
                        headers=H)
        assert s.status_code == 200 and s.json()["stopped"] is True
        assert s.json()["budget"]["stoppedBy"] == "matt"

        pre = client.post("/api/calibration/preflight",
                          json={"ticket": ticket(), "checks": {}},
                          headers=H).json()
        assert "OPERATOR_STOP_ENGAGED" in pre["refusals"]
        assert pre["submittable"] is False

        r = client.post("/api/calibration/approve",
                        json={"ticket": ticket(), "approved_by": "matt",
                              "confirm": "CAL-0001"}, headers=H)
        assert r.status_code == 409
        assert "OPERATOR_STOP_ENGAGED" in r.json()["detail"]

    def test_the_stop_does_not_pretend_to_cancel_anything(self):
        """A stop refuses admission. It does not make a resting order
        impossible to fill, so it must not release a reserve."""
        import inspect

        from sportsassets import calibration_store as store
        src = inspect.getsource(store.stop)
        assert "reserve" not in src.split('"""')[2]   # no reserve write
        assert "Reserves already held stay held" in src


class TestReleaseAtTheRoute:
    def test_a_cancel_acknowledgement_does_not_release(self, client, pool,
                                                       writes_enabled):
        r = client.post("/api/calibration/release"
                        "?client_order_id=CAL-0001&fills_reconciled=false",
                        headers=H)
        assert r.status_code == 409
        assert "RESERVE_HELD" in r.json()["detail"]

    def test_a_terminal_state_without_reconciled_fills_does_not_release(
            self, client, pool, writes_enabled):
        r = client.post("/api/calibration/release?client_order_id=CAL-0001"
                        "&venue_terminal_state=CANCELLED"
                        "&fills_reconciled=false", headers=H)
        assert r.status_code == 409
        assert "fills not reconciled" in r.json()["detail"]


# ── the bounded monitoring release gate ──────────────────────────────

class TestTheMonitoringReleaseGate:
    """What ships in the release of 2026-09-18, checked at the routes.

    A gate asserted in a comment is not a gate. These drive the actual
    endpoints with a valid admin token and a working ledger, so the only
    thing that can refuse them is the gate itself.
    """

    def test_the_gate_is_off_by_default_in_this_release(self):
        assert app_mod.CALIBRATION_WRITES_ENABLED is False

    @pytest.mark.parametrize("path,payload", [
        ("/api/calibration/approve", {"ticket": ticket(),
                                      "approved_by": "matt",
                                      "confirm": "CAL-0001"}),
        ("/api/calibration/resume", {"by": "matt"}),
    ])
    def test_the_write_routes_refuse_with_the_named_reason(self, client, pool,
                                                           path, payload):
        r = client.post(path, json=payload, headers=H)
        assert r.status_code == 503
        assert "CALIBRATION_WRITES_DISABLED_IN_THIS_RELEASE" in r.json()["detail"]

    def test_release_is_refused_even_with_both_conditions_satisfied(self,
                                                                    client,
                                                                    pool):
        r = client.post("/api/calibration/release?client_order_id=CAL-0001"
                        "&venue_terminal_state=FILLED&fills_reconciled=true",
                        headers=H)
        assert r.status_code == 503
        assert "CALIBRATION_WRITES_DISABLED" in r.json()["detail"]

    def test_the_monitoring_routes_still_work(self, client, pool):
        assert client.get("/api/calibration/state",
                          headers=H).status_code == 200
        assert client.post("/api/calibration/preflight",
                           json={"ticket": ticket()},
                           headers=H).status_code == 200

    def test_the_operator_stop_is_deliberately_still_enabled(self, client,
                                                             pool):
        """The stop removes authority rather than granting it. Shipping a
        monitoring surface whose stop is disabled would mean the one
        control an operator may need in a hurry is the one that does not
        work."""
        r = client.post("/api/calibration/stop",
                        json={"by": "matt", "reason": "drill"}, headers=H)
        assert r.status_code == 200 and r.json()["stopped"] is True

    def test_no_route_can_reach_a_venue_submission_path(self):
        """The strongest form of 'venue submission disabled': there is no
        import of the execution module anywhere a request can reach."""
        import pathlib
        api = pathlib.Path(app_mod.__file__).resolve().parent
        for p in api.glob("*.py"):
            assert "calibration_execute" not in p.read_text(), p.name
