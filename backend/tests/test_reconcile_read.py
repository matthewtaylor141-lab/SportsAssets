"""The reconciliation reads: unreadable is not empty, short is not whole.

The properties that matter here are the ones whose absence let me write
down a wrong conclusion the first time:

  * an empty list with ok=True and a failed read must never be the same
    value, because "the venue holds nothing" was nearly recorded as a
    fact when it could have been a failed call;
  * a paged walk that stops early must say so, because a SIX-PAGE bound
    in pmus_account._fetch_sync is the entire reason the account card
    showed 25 trades and I reported that as the venue's trade history;
  * no function here may return an account identifier, a balance or a
    credential.
"""

from __future__ import annotations

import pytest

from sportsassets import pmus
from sportsassets.api import reconcile_read as rr


# ── a fake venue client ──────────────────────────────────────────────

class FakeOrders:
    def __init__(self, pages=None, raises=None):
        self.pages = pages if pages is not None else [{"orders": [],
                                                       "eof": True}]
        self.raises = raises
        self.calls = 0

    def list(self, params=None):
        if self.raises:
            raise self.raises
        i = min(self.calls, len(self.pages) - 1)
        self.calls += 1
        return self.pages[i]


class FakePortfolio:
    def __init__(self, activity_pages=None, positions=None):
        self.activity_pages = activity_pages or [{"activities": [],
                                                  "eof": True}]
        self._positions = positions if positions is not None else {
            "positions": {}, "eof": True}
        self.activity_calls = 0

    def activities(self, params=None):
        i = min(self.activity_calls, len(self.activity_pages) - 1)
        self.activity_calls += 1
        return self.activity_pages[i]

    def positions(self, params=None):
        return self._positions


class FakeAccount:
    def __init__(self, balances=None, raises=None):
        self._balances = balances if balances is not None else {
            "balances": [{"currentBalance": "10", "assetNotional": "0"}]}
        self.raises = raises

    def balances(self):
        if self.raises:
            raise self.raises
        return self._balances


class FakeClient:
    def __init__(self, account=None, portfolio=None, orders=None):
        self.account = account or FakeAccount()
        self.portfolio = portfolio or FakePortfolio()
        self.orders = orders or FakeOrders()


@pytest.fixture
def client(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(pmus, "_get_client", lambda: c)
    return c


def _page(n, cursor=None, eof=False, key="activities"):
    return {key: [{"type": "ACTIVITY_TYPE_TRADE", "i": i} for i in range(n)],
            "nextCursor": cursor or "", "eof": eof}


# ── resting orders: unreadable is not empty ──────────────────────────

class TestRestingOrdersDistinguishUnreadableFromEmpty:

    def test_a_genuinely_empty_book_is_ok_with_zero(self, client):
        client.orders.pages = [{"orders": [], "eof": True}]
        r = rr.resting_orders()
        assert r["ok"] is True
        assert r["count"] == 0
        assert r["complete"] is True

    def test_none_is_a_failed_read_not_an_empty_book(self, client):
        """pmus.open_orders does `client.orders.list(params) or {}` and
        turns this into []. That is the value that would have let 'no
        outstanding commitments' be written down."""
        client.orders.pages = [None]
        with pytest.raises(rr.VenueUnreadable) as e:
            rr.resting_orders()
        assert "not an empty book" in str(e.value)

    def test_an_absent_orders_key_is_a_failed_read(self, client):
        client.orders.pages = [{"error": "upstream unavailable"}]
        with pytest.raises(rr.VenueUnreadable) as e:
            rr.resting_orders()
        assert "not an empty book" in str(e.value)

    def test_a_non_mapping_response_is_a_failed_read(self, client):
        client.orders.pages = ["<html>502</html>"]
        with pytest.raises(rr.VenueUnreadable):
            rr.resting_orders()

    def test_orders_not_a_list_is_a_failed_read(self, client):
        client.orders.pages = [{"orders": {"weird": 1}}]
        with pytest.raises(rr.VenueUnreadable):
            rr.resting_orders()

    def test_an_exception_propagates_as_unreadable(self, client):
        client.orders.raises = ConnectionError("reset")
        with pytest.raises(rr.VenueUnreadable):
            rr.resting_orders()

    def test_real_orders_come_back(self, client):
        client.orders.pages = [{"orders": [{"orderId": "a"},
                                           {"orderId": "b"}], "eof": True}]
        r = rr.resting_orders()
        assert r["count"] == 2 and r["complete"] is True

    def test_it_is_the_venue_not_the_manual_lane_table(self):
        """The endpoint I previously presented as outstanding venue
        commitments was a database read. This one must reach the SDK."""
        import inspect

        src = inspect.getsource(rr.resting_orders)
        # The docstring NAMES the database endpoint in order to say this
        # is not it, so strip the prose and check the code.
        code = src.split('"""')[2] if src.count('"""') >= 2 else src
        assert "orders.list" in code
        assert "live_orders" not in code
        assert "whale_username" not in code


# ── activity paging: short is not whole ──────────────────────────────

class TestHistoricalActivityFinishesOrSaysItDidNot:

    def test_it_pages_past_six(self, client):
        """SIX is the bound in _fetch_sync, and it is why the account
        card showed 25 trades while the ledger needed five weeks."""
        pages = [_page(100, cursor="c%d" % i) for i in range(9)]
        pages.append(_page(7, eof=True))
        client.portfolio.activity_pages = pages

        r = rr.historical_activity()
        assert r["pages"] == 10, "it stopped at %d pages" % r["pages"]
        assert r["count"] == 907
        assert r["complete"] is True
        assert r["stop_reason"] == "eof"

    def test_hitting_the_page_bound_is_reported_not_hidden(self, client):
        client.portfolio.activity_pages = [_page(100, cursor="more")]
        r = rr.historical_activity(max_pages=3)
        assert r["complete"] is False
        assert r["stop_reason"] == "page bound reached"
        assert r["pages"] == 3
        assert "PREFIX" in r["note"]

    def test_eof_without_a_cursor_stops_cleanly(self, client):
        client.portfolio.activity_pages = [_page(4, eof=True)]
        r = rr.historical_activity()
        assert r["complete"] is True and r["count"] == 4

    def test_an_empty_book_is_complete_not_broken(self, client):
        client.portfolio.activity_pages = [{"activities": [], "eof": True}]
        r = rr.historical_activity()
        assert r["ok"] is True and r["count"] == 0 and r["complete"] is True

    def test_a_failure_mid_walk_raises_rather_than_truncating(self,
                                                              monkeypatch,
                                                              client):
        calls = {"n": 0}

        def boom(params=None):
            calls["n"] += 1
            if calls["n"] > 2:
                raise ConnectionError("reset")
            return _page(100, cursor="c")

        client.portfolio.activities = boom
        with pytest.raises(rr.VenueUnreadable) as e:
            rr.historical_activity()
        assert "after 2 page(s)" in str(e.value)

    def test_it_asks_for_trades_and_resolutions(self, client):
        seen = {}

        def capture(params=None):
            seen.update(params or {})
            return _page(1, eof=True)

        client.portfolio.activities = capture
        rr.historical_activity()
        assert "ACTIVITY_TYPE_TRADE" in seen["types"]
        assert "ACTIVITY_TYPE_POSITION_RESOLUTION" in seen["types"]


# ── identity: a verdict, never the identifier ────────────────────────

class TestAccountIdentityNeverLeaksTheIdentifier:

    def test_no_identity_field_stays_a_blocker(self, client):
        client.account._balances = {
            "balances": [{"currentBalance": "10", "assetNotional": "0"}]}
        r = rr.account_identity("0xabc")
        assert r["verdict"] == "no_identity"
        assert "cannot be tied to the ledger" in r["note"]

    def test_a_match_reports_the_field_not_the_value(self, client):
        client.account._balances = {
            "balances": [{"currentBalance": "10",
                          "accountId": "0xDEADBEEF"}]}
        r = rr.account_identity("0xdeadbeef")
        assert r["verdict"] == "match"
        assert r["field"] == "accountId"
        assert "0xDEADBEEF" not in str(r), "the identifier was returned"
        assert "deadbeef" not in str(r).lower()

    def test_a_mismatch_reports_the_field_not_the_value(self, client):
        client.account._balances = {
            "balances": [{"walletAddress": "0xAAA"}]}
        r = rr.account_identity("0xBBB")
        assert r["verdict"] == "mismatch"
        assert "0xAAA" not in str(r)
        assert r["identity_fields"] == ["walletAddress"]

    def test_an_unreadable_account_is_not_a_mismatch(self, client):
        client.account.raises = ConnectionError("reset")
        r = rr.account_identity("0xabc")
        assert r["verdict"] == "unreadable"

    def test_nothing_configured_says_so(self, client):
        client.account._balances = {"balances": [{"proxyAddress": "0xAAA"}]}
        r = rr.account_identity(None)
        assert r["verdict"] == "no_expected"
        assert "0xAAA" not in str(r)

    def test_the_verdict_is_the_whole_public_answer(self, client):
        """Whatever the outcome, a balance never comes back either."""
        client.account._balances = {
            "balances": [{"currentBalance": "20972.89",
                          "accountId": "0xAAA"}]}
        for expected in ("0xaaa", "0xbbb", None):
            r = rr.account_identity(expected)
            assert "20972.89" not in str(r)
            assert "0xAAA" not in str(r)


# ── the probe: key names only ────────────────────────────────────────

class TestCapabilityProbeReturnsNoValues:

    def test_it_returns_key_names_and_no_values(self, client):
        client.account._balances = {
            "balances": [{"currentBalance": "20972.89",
                          "accountId": "0xSECRETACCOUNT"}]}
        client.orders.pages = [{"orders": [{"orderId": "ord-123"}],
                                "eof": True}]
        p = rr.capability_probe()
        blob = str(p)
        assert "20972.89" not in blob
        assert "0xSECRETACCOUNT" not in blob
        assert "ord-123" not in blob
        assert p["read_values"] is False

    def test_it_names_the_identity_field_when_one_exists(self, client):
        client.account._balances = {"balances": [{"accountId": "x"}]}
        p = rr.capability_probe()
        assert p["identity_available"] is True
        assert "accountId" in p["identity_fields_found"]

    def test_it_reports_absence_as_absence(self, client):
        client.account._balances = {"balances": [{"currentBalance": "1"}]}
        p = rr.capability_probe()
        assert p["identity_available"] is False
        assert p["identity_fields_found"] == []

    def test_one_endpoint_failing_does_not_fail_the_probe(self, client):
        client.orders.raises = ConnectionError("reset")
        p = rr.capability_probe()
        assert p["orders"]["ok"] is False
        assert p["balances"]["ok"] is True

    def test_it_reads_one_page_of_each(self, client):
        seen = {}

        def cap_positions(params=None):
            seen["positions"] = dict(params or {})
            return {"positions": {}, "eof": True}

        def cap_acts(params=None):
            seen["activities"] = dict(params or {})
            return {"activities": [], "eof": True}

        client.portfolio.positions = cap_positions
        client.portfolio.activities = cap_acts
        rr.capability_probe()
        assert seen["positions"]["limit"] == 1
        assert seen["activities"]["limit"] == 1


class TestNothingHereCanWrite:

    def test_no_submission_or_write_path_is_reachable(self):
        import inspect

        src = inspect.getsource(rr)
        for forbidden in ("orders.create", "orders.close_position",
                          "submit_fok", "INSERT", "UPDATE ", "DELETE "):
            assert forbidden not in src, forbidden

    def test_it_uses_the_existing_runtime_credentials_only(self):
        import inspect

        src = inspect.getsource(rr)
        assert "pmus._get_client" in src
        for forbidden in ("pmus_secret_key", "pmus_key_id", "os.environ"):
            assert forbidden not in src, (
                "%s appears; credentials must stay where they are" % forbidden)
