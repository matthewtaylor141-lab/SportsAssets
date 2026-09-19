"""THE PRODUCTION LANE CANNOT SEND AN ORDER, and that is checked here.

Authorized by the owner on 2026-09-19: a read-only production
verification against an account holding no funds. "Read-only" is worth
nothing as a description, so it is enforced three ways and each is
pinned below:

  1. an allow-list -- `request_for` refuses an unknown name before a
     socket is opened, so a mutating path cannot be reached by passing
     its name;
  2. absence -- no insert, cancel, replace, preview or funding path
     appears in either production source file, asserted against the
     text, and the workflow re-checks it before staging a credential;
  3. separation -- production reads PMX_PROD_*, preprod reads PMX_*,
     the host guards are mirror images, and neither is parameterized.

NOTHING HERE OPENS A SOCKET.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve()
ROOT = HERE.parents[2]
INST = ROOT / "research" / "institutional"
sys.path.insert(0, str(INST))

import pmx_production_read as prod                             # noqa: E402
import pmx_preprod_ops as pre                                  # noqa: E402

SOURCES = (INST / "pmx_production_read.py", INST / "pmx_production_net.py")

# The venue's mutating surface, from its own OpenAPI. If one of these
# ever appears in the production lane, this file fails before anything
# reaches a runner.
MUTATING = ("/v1/trading/orders", "/v1/trading/orders/cancel",
            "/v1/trading/orders/preview", "/v1/trading/orders/replace",
            "/v1/funding")


class TestItCannotSendAnOrder:

    def test_no_mutating_path_appears_in_the_source(self):
        for path in SOURCES:
            text = path.read_text()
            for bad in MUTATING:
                assert bad not in text, "%s names %s" % (path.name, bad)

    def test_every_allowed_path_is_a_read(self):
        for name, (method, path, _shape) in prod.READ_ONLY_PATHS.items():
            assert method in ("GET", "POST"), name
            # the only POSTs are searches and page reads
            if method == "POST":
                assert ("/search" in path or "/refdata/" in path
                        or "/positions/balance" in path), (name, path)
            for bad in MUTATING:
                assert bad not in path, (name, path)

    def test_an_unknown_name_is_refused_before_a_socket_opens(self):
        for name in ("order", "cancel", "preview", "replace", "funding",
                     "/v1/trading/orders", ""):
            with pytest.raises(prod.NotAReadPath):
                prod.request_for(name)

    def test_the_refusal_says_no_order_path_exists(self):
        with pytest.raises(prod.NotAReadPath) as exc:
            prod.request_for("order")
        assert "no" in str(exc.value) and "order" in str(exc.value)

    def test_the_receipt_states_no_order_path_is_present(self):
        assert prod.receipt("verify")["orderPathPresent"] is False


class TestItCannotLeaveProduction:

    def test_every_read_url_is_a_production_host(self):
        for name in prod.READS:
            _method, url, _body = prod.request_for(name, "acct")
            assert url.startswith("https://api.prod.polymarketexchange.com")

    def test_a_preprod_host_is_refused(self):
        for host in ("https://api.preprod.polymarketexchange.com/v1/whoami",
                     "https://pmx-preprod.us.auth0.com/oauth/token"):
            with pytest.raises(prod.NotProduction):
                prod.assert_production(host)

    def test_the_preprod_guard_still_refuses_production(self):
        """The mirror image. Neither guard was loosened to build this."""
        for host in ("https://api.prod.polymarketexchange.com/v1/whoami",
                     "https://pmx-prod.us.auth0.com/oauth/token"):
            with pytest.raises(pre.NotPreprod):
                pre.assert_preprod(host)

    def test_the_two_lanes_share_no_host(self):
        assert not (prod._PRODUCTION_HOSTS & pre._PREPROD_HOSTS)


class TestTheTwoLanesCannotTakeEachOthersKeys:

    def test_production_reads_only_prod_named_credentials(self):
        assert prod.CREDENTIALS_EXPECTED == (
            "PMX_PROD_CLIENT_ID", "PMX_PROD_PARTICIPANT_ID",
            "PMX_PROD_KEY_ID", "PMX_PROD_PRIVATE_KEY_B64")
        assert prod.ACCOUNT_ENV == "PMX_PROD_ACCOUNT"

    def test_no_credential_name_is_shared_with_preprod(self):
        assert not (set(prod.CREDENTIALS_EXPECTED)
                    & set(pre.CREDENTIALS_EXPECTED))

    def test_the_production_source_never_names_a_preprod_slot(self):
        for path in SOURCES:
            text = path.read_text()
            for name in pre.CREDENTIALS_EXPECTED:
                # PMX_PROD_CLIENT_ID contains no preprod name as a token;
                # check for the bare slot followed by a quote or brace
                assert '"%s"' % name not in text, (path.name, name)

    def test_presence_reports_names_without_reading_values(self, monkeypatch):
        for name in prod.CREDENTIALS_EXPECTED:
            monkeypatch.delenv(name, raising=False)
        out = prod.credential_presence()
        assert out["verdict"] == prod.A_SECRET_MISSING
        assert sorted(out["missing"]) == sorted(prod.CREDENTIALS_EXPECTED)
        monkeypatch.setenv("PMX_PROD_CLIENT_ID", "a-real-looking-value")
        out = prod.credential_presence()
        assert out["present"] == ["PMX_PROD_CLIENT_ID"]
        assert "a-real-looking-value" not in json.dumps(out)


class TestTheAudiencesAreApartHereToo:

    def test_the_assertion_aud_is_the_production_token_endpoint(self):
        assert prod.CLIENT_ASSERTION_AUD == \
            "https://pmx-prod.us.auth0.com/oauth/token"

    def test_the_request_audience_is_the_production_rest_base(self):
        assert prod.TOKEN_REQUEST_AUDIENCE == \
            "https://api.prod.polymarketexchange.com"

    def test_they_are_not_the_same_value(self):
        assert prod.CLIENT_ASSERTION_AUD != prod.TOKEN_REQUEST_AUDIENCE

    def test_the_encoding_is_the_one_evidence_supports(self):
        assert prod.TOKEN_REQUEST_ENCODING == \
            "application/x-www-form-urlencoded"

    def test_production_values_are_not_the_preprod_ones(self):
        assert prod.CLIENT_ASSERTION_AUD != pre.CLIENT_ASSERTION_AUD
        assert prod.TOKEN_REQUEST_AUDIENCE != pre.TOKEN_REQUEST_AUDIENCE


class TestWhatItWillAndWillNotWriteDown:

    def test_a_receipt_refuses_a_credential(self):
        for bad in ("-----BEGIN PRIVATE KEY-----", "Bearer eyJhbGciOi"):
            with pytest.raises(RuntimeError):
                prod.receipt("verify", note=bad)

    def test_a_summary_carries_counts_not_payloads(self):
        body = {"positions": [{"symbol": "s", "qty": 1, "avgPx": 0.5}] * 3}
        out = prod.summarize("positions", 200, body)
        assert out["positionCount"] == 3
        assert "avgPx" not in json.dumps(out)

    def test_identity_carries_resource_names_only(self):
        body = {"firm": "firms/x", "user": "firms/x/users/y",
                "secretInternalField": "should not travel"}
        out = prod.summarize("whoami", 200, body)
        assert out["identity"] == {"firm": "firms/x",
                                   "user": "firms/x/users/y"}

    def test_scopes_come_from_our_own_token_and_the_token_does_not(self):
        import base64

        claims = base64.urlsafe_b64encode(
            json.dumps({"scope": "read:marketdata read:positions"}).encode()
        ).decode().rstrip("=")
        token = "h.%s.sig" % claims
        assert prod.scopes_of(token) == ["read:marketdata", "read:positions"]
        assert prod.scopes_of("not-a-token") == []
        out = prod.receipt("verify", scopes=prod.scopes_of(token))
        assert token not in json.dumps(out)

    def test_the_receipt_is_marked_production_evidence(self):
        r = prod.receipt("verify")
        assert r["environment"] == "PRODUCTION"
        assert r["evidenceClass"] == "OBSERVED_PRODUCTION"
        # preprod evidence is a different class and stays that way
        assert pre.receipt("auth")["environment"] == "PREPROD"

    def test_production_grpc_is_still_not_identified(self):
        """Explaining WHY no stream is opened is fine; opening one is not.
        The host is spelled four ways in the documentation and none is
        confirmed, so this lane never dials it."""
        assert prod.PRODUCTION_GRPC_TARGET == "NOT_IDENTIFIED"
        assert prod.receipt("verify")["grpcTarget"] == "NOT_IDENTIFIED"
        for path in SOURCES:
            text = path.read_text()
            for opener in ("import grpc", "secure_channel", "Stub(",
                           "CreateOrderSubscription"):
                assert opener not in text, (path.name, opener)


class TestTheWorkflow:

    WF = ROOT / ".github" / "workflows" / "pmx-production.yml"

    def test_it_is_dispatch_only(self):
        import yaml

        spec = yaml.safe_load(self.WF.read_text())
        triggers = spec.get(True) or spec.get("on")
        assert set(triggers) == {"workflow_dispatch"}

    def test_it_offers_no_confirm_input_because_nothing_mutates(self):
        import yaml

        spec = yaml.safe_load(self.WF.read_text())
        inputs = (spec.get(True) or spec.get("on"))["workflow_dispatch"]
        assert set(inputs["inputs"]) == {"reads"}

    def test_it_checks_for_an_order_path_before_staging_a_credential(self):
        src = self.WF.read_text()
        assert src.index("Confirm the lane has no order path") < \
            src.index("secrets.PMX_PROD_")
        for bad in ("/v1/trading/orders", "/v1/funding"):
            assert bad in src          # named in the grep guard

    def test_it_never_reads_a_preprod_credential(self):
        src = self.WF.read_text()
        for name in pre.CREDENTIALS_EXPECTED:
            assert "secrets.%s " % name not in src
            assert "secrets.%s }}" % name not in src
