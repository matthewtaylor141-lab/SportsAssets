"""THE PREPROD OPERATIONS CONTRACT, tested against the venue's own docs.

The schemas asserted here are not paraphrases. They come from the sealed
documentation capture on branch `beta48-capability/docs-35047231583`:

  /api-reference/report/search-orders.md   sha256 fb233e7900dd...
  /streaming-endpoints/order-stream.md     sha256 b80a874a621b...
  /streaming-endpoints/getting-started.md  sha256 147816ea2f5e...

NOTHING HERE OPENS A SOCKET. `pmx_preprod_ops` is the half with no
network in it, which is exactly why it can be tested.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve()
INST = HERE.parents[2] / "research" / "institutional"
sys.path.insert(0, str(INST))

import pmx_preprod_ops as ops                                  # noqa: E402


# ── the preprod boundary ─────────────────────────────────────────────

class TestNothingReachesProduction:
    def test_the_three_preprod_hosts_are_allowed(self):
        for u in (ops.REST_BASE, "https://" + ops.AUTH0_DOMAIN + "/oauth/token",
                  ops.GRPC_TARGET):
            assert ops.assert_preprod(u)

    @pytest.mark.parametrize("bad", [
        "https://api.prod.polymarketexchange.com/v1/whoami",
        "grpc-api.prod.polymarketexchange.com:443",
        "https://pmx-prod.us.auth0.com/oauth/token",
        "https://api.preprod.polymarketexchange.com.evil.test/x",
    ])
    def test_production_and_lookalikes_are_refused(self, bad):
        with pytest.raises(ops.NotPreprod):
            ops.assert_preprod(bad)

    def test_the_module_names_no_production_host(self):
        src = pathlib.Path(ops.__file__).read_text()
        for bad in ("api.prod.polymarketexchange", "grpc-api.prod.",
                    "pmx-prod."):
            assert bad not in src, bad


# ── 1. authentication: four outcomes, kept apart ─────────────────────

class TestTheFourAuthOutcomes:
    def test_all_four_secrets_empty_is_secret_missing(self):
        got = ops.credential_presence({})
        assert got["verdict"] == ops.A_SECRET_MISSING
        assert got["missing"] == list(ops.CREDENTIALS_EXPECTED)

    def test_some_present_is_unavailable_to_this_workflow(self):
        got = ops.credential_presence({"PMX_CLIENT_ID": "a",
                                       "PMX_KEY_ID": "b"})
        assert got["verdict"] == ops.A_SECRET_UNAVAILABLE
        assert got["present"] == ["PMX_CLIENT_ID", "PMX_KEY_ID"]
        assert "PMX_PRIVATE_KEY_B64" in got["missing"]

    def test_whitespace_is_not_a_secret(self):
        got = ops.credential_presence({n: "   " for n in ops.CREDENTIALS_EXPECTED})
        assert got["verdict"] == ops.A_SECRET_MISSING

    def test_all_four_present_has_no_verdict_of_its_own(self):
        got = ops.credential_presence({n: "x" for n in ops.CREDENTIALS_EXPECTED})
        assert got["verdict"] is None

    def test_no_value_is_ever_returned(self):
        got = ops.credential_presence({n: "SUPER-SECRET-%s" % n
                                       for n in ops.CREDENTIALS_EXPECTED})
        assert "SUPER-SECRET" not in json.dumps(got)

    def test_the_limit_of_the_distinction_is_stated(self):
        # A secret that does not exist and one not exposed to this
        # workflow BOTH arrive empty. Saying so is the point.
        why = ops.credential_presence({})["why"]
        assert "not exposed" in why and "repository settings" in why

    def test_token_rejected_is_not_permission_denied(self):
        assert ops.classify_auth(401, None) == ops.A_REJECTED
        assert ops.classify_auth(403, None) == ops.A_REJECTED

    def test_authenticated_but_forbidden_is_its_own_verdict(self):
        # 2026-09-10: /v1/accounts/accounts answered 403 with a token
        # that carried read:accounts. The credential was fine.
        assert ops.classify_auth(200, 403) == ops.A_PERMISSION_DENIED

    def test_authenticated_and_permitted(self):
        assert ops.classify_auth(200, 200) == ops.A_OK

    def test_a_transport_failure_is_not_a_rejection(self):
        assert ops.classify_auth(None, None, "ConnectionError") == \
            ops.A_UNREACHABLE

    def test_identity_keeps_names_and_drops_the_payload(self):
        got = ops.identity_of({"user": "firms/f/users/u", "firm": "firms/f",
                               "balance": 1000000, "secretThing": "x"})
        assert got["confirmed"] is True
        assert got["fields"]["user"] == "firms/f/users/u"
        assert "balance" not in json.dumps(got)
        assert "secretThing" not in json.dumps(got)

    def test_a_non_object_whoami_confirms_nothing(self):
        assert ops.identity_of("nope")["confirmed"] is False


# ── 2. the documented search contract ────────────────────────────────

SPEC = {"accounts": ["firms/f/accounts/a"], "symbol": "aec-cs2-x-2026-09-10",
        "startTime": "2026-09-10T00:00:00Z", "endTime": "2026-09-11T00:00:00Z",
        "orderId": "CDHM9PJV16R7"}


class TestTheSearchBodyIsTheDocumentedOne:
    def test_accounts_is_a_list_not_a_string(self):
        b = ops.search_body(SPEC, "orderId", "CDHM9PJV16R7")
        assert b["accounts"] == ["firms/f/accounts/a"]
        assert isinstance(b["accounts"], list)

    def test_the_unestablished_filters_are_not_used(self):
        # `account`, `participantId`, `clientAccountId` and
        # `clientParticipantId` are NOT substituted for `accounts`: their
        # filtering behaviour has not been established, and a scoped
        # search built on a filter that may not filter answers nothing.
        b = ops.search_body(SPEC, "orderId", "x")
        for field in ("account", "participantId", "clientAccountId",
                      "clientParticipantId"):
            assert field not in b, field

    def test_orderId_and_clordId_are_never_sent_together(self):
        a = ops.search_body(SPEC, "orderId", "O-1")
        assert a["orderId"] == "O-1" and "clordId" not in a
        b = ops.search_body({**SPEC, "clordId": "C-1"}, "clordId", "C-1")
        assert b["clordId"] == "C-1" and "orderId" not in b

    def test_they_are_singular_strings(self):
        b = ops.search_body(SPEC, "orderId", "O-1")
        assert isinstance(b["orderId"], str)
        for guessed in ("orderIds", "clordIds", "clOrdIds"):
            assert guessed not in b

    def test_the_state_filter_field_is_the_documented_name(self):
        b = ops.search_body({**SPEC, "orderStateFilter":
                             "ORDER_STATE_FILTER_CLOSED"}, "orderId", "O-1")
        assert b["orderStateFilter"] == "ORDER_STATE_FILTER_CLOSED"
        assert "stateFilter" not in b        # the name I had guessed before

    def test_the_scope_travels_on_every_page(self):
        b = ops.search_body(SPEC, "orderId", "O-1", page_token="tok")
        assert b["pageToken"] == "tok"
        assert b["accounts"] and b["symbol"] and b["startTime"] and b["endTime"]

    def test_a_key_other_than_the_two_is_refused(self):
        with pytest.raises(ValueError):
            ops.search_body(SPEC, "clientAccountId", "x")


class TestTheScopeIsRequiredBeforeAnySearch:
    def test_a_complete_spec_has_no_blockers(self):
        assert ops.scope_blockers(SPEC) == []

    @pytest.mark.parametrize("drop", ["accounts", "symbol", "startTime",
                                      "endTime"])
    def test_each_missing_scope_field_blocks(self, drop):
        spec = {k: v for k, v in SPEC.items() if k != drop}
        blockers = ops.scope_blockers(spec)
        assert any(ops.B_SCOPE in b and drop.rstrip("s") in b
                   for b in blockers), blockers

    def test_an_empty_accounts_list_blocks(self):
        assert ops.scope_blockers({**SPEC, "accounts": []})
        assert ops.scope_blockers({**SPEC, "accounts": ["  "]})

    def test_an_account_string_instead_of_a_list_blocks(self):
        assert ops.scope_blockers({**SPEC, "accounts": "firms/f/accounts/a"})

    def test_neither_identifier_blocks(self):
        spec = {k: v for k, v in SPEC.items() if k != "orderId"}
        assert any("orderId or clordId" in b
                   for b in ops.scope_blockers(spec))


class TestRowsAreValidatedAgainstWhatWasAsked:
    def _row(self, **over):
        r = {"id": "CDHM9PJV16R7", "clordId": "c-1",
             "account": "firms/f/accounts/a",
             "symbol": "aec-cs2-x-2026-09-10", "state": "ORDER_STATE_FILLED"}
        r.update(over)
        return r

    def test_a_matching_row_validates(self):
        assert ops.validate_row(self._row(), SPEC)["ok"] is True

    def test_another_account_does_not_validate(self):
        v = ops.validate_row(self._row(account="firms/other/accounts/z"), SPEC)
        assert v["ok"] is False and "account" in v["why"]

    def test_another_instrument_does_not_validate(self):
        v = ops.validate_row(self._row(symbol="aec-other"), SPEC)
        assert v["ok"] is False and "symbol" in v["why"]

    def test_a_row_that_is_not_an_object_does_not_validate(self):
        assert ops.validate_row(["nope"], SPEC)["ok"] is False
        assert ops.validate_row(None, SPEC)["ok"] is False

    def test_a_row_with_no_id_does_not_validate(self):
        assert ops.validate_row(self._row(id=None), SPEC)["ok"] is False
        assert ops.validate_row(self._row(id=1234), SPEC)["ok"] is False


class TestTheVerdictNeverLaundersAFailureIntoNotFound:
    def _q(self, *blockers):
        return [{"query": "orderId", "pages": 1, "rows": 0,
                 "blockers": list(blockers)}]

    def test_a_clean_empty_walk_is_not_found(self):
        v = ops.verdict(self._q(), [])
        assert v["status"] == ops.R_NOT_FOUND

    def test_and_not_found_says_it_is_not_permission_to_retry(self):
        v = ops.verdict(self._q(), [])
        assert "NOT permission to send another order" in v["meaning"]
        assert "trace package" in v["meaning"]

    def test_an_http_error_is_FAILED(self):
        v = ops.verdict(self._q("%s: page 1 answered 503" % ops.B_HTTP), [])
        assert v["status"] == ops.R_FAILED

    def test_malformed_json_is_FAILED(self):
        v = ops.verdict(self._q("%s: page 1 did not parse" % ops.B_MALFORMED),
                        [])
        assert v["status"] == ops.R_FAILED

    def test_an_invalid_row_shape_is_INCONCLUSIVE(self):
        v = ops.verdict(self._q("%s: page 1 no string id" % ops.B_ROW_SHAPE),
                        [])
        assert v["status"] == ops.R_INCONCLUSIVE

    def test_a_repeated_page_token_is_INCONCLUSIVE(self):
        v = ops.verdict(self._q("%s: page 3 repeated" % ops.B_TOKEN_REPEAT), [])
        assert v["status"] == ops.R_INCONCLUSIVE

    def test_a_walk_cut_by_the_page_cap_is_INCONCLUSIVE(self):
        v = ops.verdict(self._q("%s: stopped at 50" % ops.B_PAGE_CAP), [])
        assert v["status"] == ops.R_INCONCLUSIVE

    @pytest.mark.parametrize("blocker", [
        "%s: page 1 answered 503", "%s: page 1 did not parse",
        "%s: page 1 bad row", "%s: repeated", "%s: cap",
    ])
    def test_no_failure_class_can_become_not_found(self, blocker):
        for cls in (ops.B_HTTP, ops.B_MALFORMED, ops.B_ROW_SHAPE,
                    ops.B_TOKEN_REPEAT, ops.B_PAGE_CAP):
            v = ops.verdict(self._q(blocker % cls), [])
            assert v["status"] != ops.R_NOT_FOUND, (cls, v["status"])

    def test_a_match_is_reconciled_but_NOT_a_reconciled_lifecycle(self):
        v = ops.verdict(self._q(), [{"on": "orderId", "id": "X"}])
        assert v["status"] == ops.R_OK
        assert "NOT a reconciled lifecycle" in v["meaning"]
        assert "fills, cancellation and settlement" in v["meaning"]

    def test_a_match_beside_a_hard_failure_is_still_FAILED(self):
        # The venue errored on another page; we do not get to keep the
        # good half and call the walk complete.
        v = ops.verdict(self._q("%s: page 2 answered 500" % ops.B_HTTP),
                        [{"on": "orderId", "id": "X"}])
        assert v["status"] == ops.R_FAILED


# ── 3. the gRPC subscription request, from generated messages ────────

class _RecordingRequest:
    """Stands in for the GENERATED CreateOrderSubscriptionRequest.

    It records the keyword names it was constructed with, so the test
    asserts the FIELD NAMES the venue documents rather than asserting
    that some object was made.
    """

    def __init__(self, **kw):
        self.kw = kw


class _RecordingTradingPb2:
    def __init__(self):
        self.made = []

    def CreateOrderSubscriptionRequest(self, **kw):             # noqa: N802
        r = _RecordingRequest(**kw)
        self.made.append(r)
        return r


class TestTheSubscriptionRequestIsTheDocumentedOne:
    def test_the_three_documented_fields_and_no_others(self):
        pb = _RecordingTradingPb2()
        ops.build_subscription_request(pb, symbols=["s1"],
                                       accounts=["firms/f/accounts/a"],
                                       snapshot_only=True)
        assert set(pb.made[0].kw) == {"symbols", "accounts", "snapshot_only"}

    def test_empty_lists_rather_than_none(self):
        # "Empty list = all symbols" / "all accounts for authenticated
        # user" -- None would be a type error on the generated message.
        pb = _RecordingTradingPb2()
        ops.build_subscription_request(pb)
        kw = pb.made[0].kw
        assert kw["symbols"] == [] and kw["accounts"] == []

    def test_snapshot_only_is_a_bool(self):
        pb = _RecordingTradingPb2()
        ops.build_subscription_request(pb, snapshot_only=1)
        assert pb.made[0].kw["snapshot_only"] is True

    def test_the_target_service_and_rpc_are_named(self):
        assert ops.GRPC_TARGET == \
            "grpc-api.preprod.polymarketexchange.com:443"
        src = pathlib.Path(ops.__file__).read_text()
        assert "polymarket.v1.OrderEntryAPI" in src
        assert "CreateOrderSubscription" in src

    def test_the_canonical_bundle_is_the_proto_source(self):
        # The generated client comes from the venue's own downloadable
        # bundle, generated on the runner. Nothing hand-writes a message.
        assert ops.PROTO_BUNDLE_URL.startswith("https://")
        assert "grpc_tools.protoc" in ops.PROTOC_CMD
        assert "polymarket/v1" in ops.PROTOC_CMD


class _Field:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Response:
    def __init__(self, which, session_id="sess-1", **payload):
        self._which = which
        self.session_id = session_id
        for k, v in payload.items():
            setattr(self, k, v)

    def HasField(self, name):                                   # noqa: N802
        return name == self._which


class TestTheStreamIsObservedNotAssumed:
    def test_a_heartbeat_is_counted(self):
        seen = {}
        ops.observe_message(_Response("heartbeat"), seen)
        assert seen["heartbeat"] == 1 and seen["sessionId"] == "sess-1"

    def test_an_empty_snapshot_is_still_a_snapshot(self):
        seen = {}
        ops.observe_message(
            _Response("snapshot", snapshot=_Field(orders=[])), seen)
        assert seen["snapshot"] == 1
        assert seen.get("snapshotOrders", 0) == 0

    def test_snapshot_orders_yield_the_documented_identifiers(self):
        seen = {}
        order = _Field(id="O-1", clord_id="C-1", account="firms/f/accounts/a")
        ops.observe_message(
            _Response("snapshot", snapshot=_Field(orders=[order])), seen)
        assert seen["orderIds"] == ["O-1"]
        assert seen["clordIds"] == ["C-1"]      # snake_case, per the proto
        assert seen["accounts"] == ["firms/f/accounts/a"]

    def test_an_execution_aggressor_flag_is_counted(self):
        seen = {}
        ops.observe_message(
            _Response("update", update=_Field(
                executions=[_Field(aggressor=True), _Field(aggressor=False)])),
            seen)
        assert seen["executions"] == 2 and seen["aggressorTrue"] == 1

    def test_an_empty_snapshot_is_connection_evidence_only(self):
        limit = ops.STREAM_EVIDENCE_LIMIT
        assert "NOT evidence of a fill" in limit
        assert "replay completeness" in limit
        assert "profitability" in limit
        assert "full-market aggressor coverage" in limit


# ── receipts ─────────────────────────────────────────────────────────

class TestAReceiptCannotCarryACredential:
    def test_the_ordinary_fields_are_allowed(self, monkeypatch):
        monkeypatch.setenv("GITHUB_RUN_ID", "123")
        monkeypatch.setenv("GITHUB_SHA", "deadbeef")
        r = ops.receipt("auth", verdict=ops.A_OK, tokenStatus=200)
        assert r["runId"] == "123" and r["executedRevision"] == "deadbeef"
        assert r["environment"] == "PREPROD"

    @pytest.mark.parametrize("poison", [
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----",
        "Bearer eyJhbGciOiJSUzI1NiJ9.x.y",
    ])
    def test_a_credential_shaped_value_is_refused(self, poison):
        with pytest.raises(RuntimeError, match="credential"):
            ops.receipt("auth", oops=poison)

    def test_an_access_token_field_is_refused(self):
        with pytest.raises(RuntimeError):
            ops.receipt("auth", body={"access_token": "abc"})

    def test_a_signed_assertion_is_refused(self):
        with pytest.raises(RuntimeError):
            ops.receipt("auth", request={"client_assertion": "x.y.z"})

    def test_write_receipt_refuses_too(self, tmp_path):
        with pytest.raises(RuntimeError):
            ops.write_receipt(tmp_path / "r.json",
                              {"k": "-----BEGIN PRIVATE KEY-----"})
        assert not (tmp_path / "r.json").exists()


# ── the network half makes no decisions ──────────────────────────────

class TestTheSplitIsReal:
    def test_the_contract_module_imports_no_network_library(self):
        src = pathlib.Path(ops.__file__).read_text()
        for lib in ("import requests", "import grpc", "import httpx"):
            assert lib not in src, lib

    def test_the_network_module_sends_no_order(self):
        net = pathlib.Path(ops.__file__).with_name("pmx_preprod_net.py")
        src = net.read_text()
        for path in ("/v1/trading/orders", "/v1/trading/orders/cancel",
                     "CreateOrder(", "CancelOrder(", "ReplaceOrder("):
            assert path not in src, path

    def test_the_network_module_takes_its_verdicts_from_the_contract(self):
        net = pathlib.Path(ops.__file__).with_name("pmx_preprod_net.py")
        src = net.read_text()
        assert "ops.verdict(" in src
        assert "ops.classify_auth(" in src
        assert "ops.scope_blockers(" in src
        assert "ops.validate_row(" in src

    def test_the_verified_auth_exchange_is_not_swapped_for_the_example(self):
        net = pathlib.Path(ops.__file__).with_name("pmx_preprod_net.py")
        src = net.read_text()
        # ours: aud = https://<domain>/ , form-encoded
        assert '"aud": "https://%s/" % ops.AUTH0_DOMAIN' in src
        assert "application/x-www-form-urlencoded" in src
        # the streaming example's shape, NOT adopted
        assert "/oauth/token\"," not in src.split("aud")[1][:80]
