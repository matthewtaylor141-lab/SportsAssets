"""The execution-mirror account probe reads, and only reads.

  * its reader exposes an allowlist of five reads and no mutation name;
  * the module's source never names a venue mutation (AST walk);
  * the credential is read only from PMUS_EXECMIRROR_KEY_ID / _SECRET_KEY,
    never from the funded PMUS_KEY_ID / PMUS_SECRET_KEY, and no output
    carries it;
  * authentication failure, partial reads and a fresh account are each
    reported under their own state.
"""
import ast
import inspect
import json

import pytest

from sportsassets import execmirror_probe as EP

MUTATIONS = {"create", "cancel", "modify", "cancel_all", "close_position",
             "preview", "submit_fok", "cancel_order", "post_order",
             "create_order"}
KID, SEC = "kid-EXECMIRROR-test-1234", "c2VjcmV0LXNob3VsZC1uZXZlci1sZWFr"


class _Err(Exception):
    def __init__(self, status, msg="nope"):
        super().__init__(msg)
        self.status_code = status


class FakeReader:
    def __init__(self, *, fail_balances=None, positions=None, orders=None,
                 acts=None, fail_orders=False):
        self.calls = []
        self.fail_balances, self.fail_orders = fail_balances, fail_orders
        self._pos = positions if positions is not None else {}
        self._orders = orders or []
        self._acts = acts or []

    def balances(self):
        self.calls.append("balances")
        if self.fail_balances:
            raise _Err(self.fail_balances, "unauthorized key " + KID)
        return {"balances": [{"currency": "USD", "currentBalance": 25.0,
                              "buyingPower": 25.0, "openOrders": 0.0,
                              "pendingWithdrawals": [{"bankId": "b1"}],
                              "secretish": "x"}]}

    def positions(self, cursor=None):
        self.calls.append("positions")
        return {"positions": self._pos, "eof": True}

    def open_orders(self):
        self.calls.append("open_orders")
        if self.fail_orders:
            raise _Err(500)
        return {"orders": self._orders}

    def activities(self):
        self.calls.append("activities")
        return {"activities": self._acts}

    def market(self, slug):
        self.calls.append("market:" + slug)
        return {"market": {"slug": slug, "minTickSize": "0.01",
                           "minOrderSize": 1, "active": True,
                           "apiKey": "should-not-appear",
                           "outcomes": [{"name": "Yes"}]}}


def _no_sleep(_):
    return None


def test_the_reader_exposes_reads_only():
    names = {n for n in dir(EP._Reader) if not n.startswith("_")}
    assert names == {"balances", "positions", "open_orders", "activities",
                     "market"}
    assert not (names & MUTATIONS)


def test_the_module_source_names_no_venue_mutation():
    tree = ast.parse(inspect.getsource(EP))
    hits = [n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr in MUTATIONS]
    assert hits == [], hits


def test_the_credential_comes_from_its_own_names_only(monkeypatch):
    monkeypatch.delenv(EP.KEY_ID_ENV, raising=False)
    monkeypatch.delenv(EP.SECRET_ENV, raising=False)
    monkeypatch.setenv("PMUS_KEY_ID", "funded-kid")
    monkeypatch.setenv("PMUS_SECRET_KEY", "funded-secret")
    k = EP.keys_present()
    assert k["complete"] is False and k["key_fingerprint"] is None
    assert EP.account_snapshot(sleep=_no_sleep)["state"] == "CREDENTIAL_ABSENT"
    monkeypatch.setenv(EP.KEY_ID_ENV, KID)
    monkeypatch.setenv(EP.SECRET_ENV, SEC)
    k = EP.keys_present()
    assert k["complete"] is True and k["distinct_from_funded_key"] is True
    assert k["key_fingerprint"] == EP.fingerprint(KID)
    monkeypatch.setenv("PMUS_KEY_ID", KID)
    assert EP.keys_present()["distinct_from_funded_key"] is False
    blob = json.dumps(EP.keys_present())
    assert KID not in blob and SEC not in blob


def test_the_market_data_identity_is_reported_apart_from_both_execution_keys(monkeypatch):
    monkeypatch.setenv(EP.KEY_ID_ENV, KID)
    monkeypatch.setenv(EP.SECRET_ENV, SEC)
    monkeypatch.setenv("PMUS_KEY_ID", "funded-kid")
    monkeypatch.delenv(EP.MD_KEY_ID_ENV, raising=False)
    monkeypatch.delenv(EP.MD_SECRET_ENV, raising=False)
    md = EP.keys_present()["market_data"]
    assert md["complete"] is False and md["key_fingerprint"] is None
    assert md["distinct_from_execution_mirror_key"] is None
    monkeypatch.setenv(EP.MD_KEY_ID_ENV, "md-kid")
    monkeypatch.setenv(EP.MD_SECRET_ENV, "md-secret")
    md = EP.keys_present()["market_data"]
    assert md["complete"] is True and md["key_fingerprint"] == EP.fingerprint("md-kid")
    assert md["distinct_from_execution_mirror_key"] is True
    assert md["distinct_from_funded_key"] is True
    monkeypatch.setenv(EP.MD_KEY_ID_ENV, KID)
    assert EP.keys_present()["market_data"]["distinct_from_execution_mirror_key"] is False
    blob = json.dumps(EP.keys_present())
    assert "md-secret" not in blob and KID not in blob and SEC not in blob


def test_a_fresh_account_reads_back_whole(monkeypatch):
    monkeypatch.setenv(EP.KEY_ID_ENV, KID)
    monkeypatch.setenv(EP.SECRET_ENV, SEC)
    r = FakeReader()
    out = EP.account_snapshot(r, sleep=_no_sleep)
    assert out["state"] == "OK" and out["authenticated"] is True
    assert out["fresh_account"] is True and out["open_positions"] == 0
    assert out["balances"][0]["currentBalance"] == 25.0
    assert out["balances"][0]["pending_withdrawals"] == 1
    assert "secretish" not in out["balances"][0]
    assert r.calls == ["balances", "positions", "open_orders", "activities"]
    blob = json.dumps(out)
    assert KID not in blob and SEC not in blob


def test_positions_and_orders_are_reported_not_hidden(monkeypatch):
    r = FakeReader(
        positions={"mlb-x-2026": {"netPosition": "3", "qtyAvailable": "3",
                                  "cost": {"value": "1.50", "currency": "USD"},
                                  "marketMetadata": {"slug": "mlb-x-2026",
                                                     "outcome": "Yes"}}},
        orders=[{"id": "o1", "marketSlug": "mlb-x-2026", "quantity": 2,
                 "price": {"value": "0.55", "currency": "USD"},
                 "state": "ORDER_STATE_NEW"}],
        acts=[{"type": "ACTIVITY_TYPE_TRADE", "createTime": "2026-10-02T17:00:00Z"}])
    out = EP.account_snapshot(r, sleep=_no_sleep)
    assert out["fresh_account"] is False and out["open_positions"] == 1
    assert out["positions"][0]["cost"] == "1.50"
    assert out["open_orders"][0]["price"] == "0.55"
    assert out["activity"]["by_type"] == {"ACTIVITY_TYPE_TRADE": 1}


def test_authentication_failure_is_named_and_redacted():
    out = EP.account_snapshot(FakeReader(fail_balances=401), sleep=_no_sleep)
    assert out["state"] == "AUTHENTICATION_FAILED"
    assert KID not in json.dumps(out)
    assert "positions" not in out


def test_a_failed_read_makes_the_snapshot_partial_not_fresh():
    out = EP.account_snapshot(FakeReader(fail_orders=True), sleep=_no_sleep)
    assert out["state"] == "PARTIAL" and out["fresh_account"] is False
    assert out["open_orders_error"]["status"] == 500


def test_market_rules_keep_constraints_and_drop_secret_looking_fields():
    r = FakeReader()
    out = EP.market_rules(["a-slug", "b-slug"], r, sleep=_no_sleep)
    rules = out["markets"]["a-slug"]["rules"]
    assert rules["minTickSize"] == "0.01" and rules["minOrderSize"] == 1
    assert "apiKey" not in rules and "should-not-appear" not in json.dumps(out)
    assert r.calls == ["market:a-slug", "market:b-slug"]
