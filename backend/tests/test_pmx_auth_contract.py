"""THE AUTHENTICATION CONTRACT: two audiences that are not the same value.

The defect this file exists to prevent is a conflation, not a typo. An
Auth0 private-key-JWT exchange carries TWO audience values and they mean
different things:

  the `aud` CLAIM inside the signed assertion   -> who consumes the
                                                   assertion: Auth0's
                                                   TOKEN ENDPOINT
  the `audience` FORM FIELD of the token request -> which API the issued
                                                   access token is for:
                                                   the REST base

Until 2026-09-19 the assertion was signed with the bare issuer,
`https://pmx-preprod.us.auth0.com/`. The venue accepted that on
2026-09-10 -- so it is a value Auth0 tolerates -- but it is not what the
current documentation specifies, and a tolerated value is not a contract.

THE ENCODING IS THE OPPOSITE CASE and the asymmetry is deliberate. The
documentation's example POSTs JSON. Retained venue evidence (runs 1-24 on
2026-09-10, workflow revision a95b54e) shows form encoding was answered
200 with a usable bearer token. An ACCEPTED encoding is not replaced on
the strength of an example, so the form body is pinned here too -- and
pinned on the WIRE, not just as a constant, because a constant nobody
sends proves nothing.

NOTHING HERE OPENS A SOCKET. The session and the JWT library are both
doubles that record what they were handed.
"""
from __future__ import annotations

import base64
import json
import pathlib
import sys
import types

import pytest

HERE = pathlib.Path(__file__).resolve()
INST = HERE.parents[2] / "research" / "institutional"
sys.path.insert(0, str(INST))

import pmx_preprod_ops as ops                                  # noqa: E402

AUTH0 = "pmx-preprod.us.auth0.com"
EXPECTED_ASSERTION_AUD = "https://pmx-preprod.us.auth0.com/oauth/token"
EXPECTED_TOKEN_AUDIENCE = "https://api.preprod.polymarketexchange.com"
FORM = "application/x-www-form-urlencoded"


# ── the two constants, spelled out ───────────────────────────────────

class TestTheTwoAudiencesAreDifferentValues:

    def test_the_client_assertion_aud_is_the_token_endpoint(self):
        assert ops.CLIENT_ASSERTION_AUD == EXPECTED_ASSERTION_AUD

    def test_the_token_request_audience_is_the_rest_base(self):
        assert ops.TOKEN_REQUEST_AUDIENCE == EXPECTED_TOKEN_AUDIENCE

    def test_they_are_not_the_same_value(self):
        # the whole point: a future edit that collapses them fails here
        assert ops.CLIENT_ASSERTION_AUD != ops.TOKEN_REQUEST_AUDIENCE

    def test_the_assertion_aud_is_not_the_bare_issuer(self):
        # what we sent until 2026-09-19, kept only as evidence
        assert ops.CLIENT_ASSERTION_AUD != \
            ops.CLIENT_ASSERTION_AUD_PREVIOUSLY_ACCEPTED
        assert ops.CLIENT_ASSERTION_AUD_PREVIOUSLY_ACCEPTED == \
            "https://pmx-preprod.us.auth0.com/"

    def test_the_assertion_aud_names_auth0_and_the_audience_names_the_api(self):
        assert AUTH0 in ops.CLIENT_ASSERTION_AUD
        assert AUTH0 not in ops.TOKEN_REQUEST_AUDIENCE
        assert "polymarketexchange.com" in ops.TOKEN_REQUEST_AUDIENCE
        assert "polymarketexchange.com" not in ops.CLIENT_ASSERTION_AUD

    def test_nothing_falls_back_to_the_previously_accepted_value(self):
        # the old value may be RECORDED. It may not be REACHED: a silent
        # retry would leave us unable to say which audience the venue took.
        src = (INST / "pmx_preprod_net.py").read_text()
        assert "CLIENT_ASSERTION_AUD_PREVIOUSLY_ACCEPTED" not in src


# ── what actually goes on the wire ───────────────────────────────────

class _RecordingJWT(types.ModuleType):
    """A stand-in for pyjwt that records the claims it was asked to sign."""

    def __init__(self):
        super().__init__("jwt")
        self.claims = None
        self.headers = None
        self.algorithm = None

    def encode(self, claims, key, algorithm=None, headers=None):
        self.claims = dict(claims)
        self.headers = dict(headers or {})
        self.algorithm = algorithm
        return "signed.assertion.value"


class _Response:
    status_code = 200

    def json(self):
        return {"access_token": "t", "expires_in": 86400}


class _RecordingSession:
    """Records the token POST. `json=` is absent on purpose: if the call
    ever switches to a JSON body this double raises rather than passing."""

    def __init__(self):
        self.url = None
        self.data = None
        self.headers = None

    def post(self, url, data=None, headers=None, timeout=None):
        self.url = url
        self.data = data
        self.headers = dict(headers or {})
        return _Response()


@pytest.fixture()
def wire(monkeypatch):
    """Call `mint_token` with every socket and signature replaced."""
    fake_jwt = _RecordingJWT()
    monkeypatch.setitem(sys.modules, "jwt", fake_jwt)
    pem = b"-----BEGIN PRIVATE KEY-----\nnot-a-key\n-----END PRIVATE KEY-----\n"
    monkeypatch.setenv("PMX_PRIVATE_KEY_B64", base64.b64encode(pem).decode())
    monkeypatch.setenv("PMX_CLIENT_ID", "client-id")
    monkeypatch.setenv("PMX_KEY_ID", "key-id")

    import pmx_preprod_net as net

    session = _RecordingSession()
    status, token, ttl, why = net.mint_token(session)
    assert (status, token) == (200, "t")
    return fake_jwt, session


class TestTheTokenCallOnTheWire:

    def test_the_signed_assertion_carries_the_token_endpoint(self, wire):
        fake_jwt, _ = wire
        assert fake_jwt.claims["aud"] == EXPECTED_ASSERTION_AUD

    def test_the_form_field_carries_the_rest_base(self, wire):
        _, session = wire
        assert session.data["audience"] == EXPECTED_TOKEN_AUDIENCE

    def test_the_two_values_sent_are_different(self, wire):
        fake_jwt, session = wire
        assert fake_jwt.claims["aud"] != session.data["audience"]

    def test_the_body_is_form_encoded_and_not_json(self, wire):
        # retained venue evidence, not a preference: see
        # ops.TOKEN_REQUEST_ENCODING_EVIDENCE
        _, session = wire
        assert session.headers["Content-Type"] == FORM
        assert isinstance(session.data, dict)
        assert session.data["grant_type"] == "client_credentials"
        assert session.data["client_assertion_type"] == \
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

    def test_the_encoding_constant_records_why_it_was_not_changed(self):
        assert ops.TOKEN_REQUEST_ENCODING == FORM
        assert "2026-09-10" in ops.TOKEN_REQUEST_ENCODING_EVIDENCE

    def test_the_post_goes_to_the_preprod_token_endpoint(self, wire):
        _, session = wire
        assert session.url == "https://%s/oauth/token" % AUTH0

    def test_the_assertion_is_rs256_with_the_key_id_in_the_header(self, wire):
        fake_jwt, _ = wire
        assert fake_jwt.algorithm == "RS256"
        assert fake_jwt.headers["kid"] == "key-id"

    def test_the_assertion_expiry_is_its_own_clock(self, wire):
        # 60 s on the assertion is NOT the access token's lifetime
        fake_jwt, _ = wire
        assert fake_jwt.claims["exp"] - fake_jwt.claims["iat"] == 60


# ── the same correction in the adapter that trades ───────────────────

class TestTheAdapterSignsTheSameThing:

    def test_pmx_has_the_two_audiences_apart(self):
        from sportsassets import pmx

        assert pmx.CLIENT_ASSERTION_AUD == EXPECTED_ASSERTION_AUD
        assert pmx.TOKEN_REQUEST_AUDIENCE == EXPECTED_TOKEN_AUDIENCE
        assert pmx.CLIENT_ASSERTION_AUD != pmx.TOKEN_REQUEST_AUDIENCE
        assert pmx.CLIENT_ASSERTION_AUD_PREVIOUSLY_ACCEPTED == \
            "https://%s/" % AUTH0

    def test_pmx_agrees_with_the_preprod_module(self):
        from sportsassets import pmx

        assert pmx.CLIENT_ASSERTION_AUD == ops.CLIENT_ASSERTION_AUD
        assert pmx.TOKEN_REQUEST_AUDIENCE == ops.TOKEN_REQUEST_AUDIENCE

    def test_the_adapter_signs_the_token_endpoint_on_the_wire(self,
                                                              monkeypatch):
        from sportsassets import pmx

        fake_jwt = _RecordingJWT()
        monkeypatch.setitem(sys.modules, "jwt", fake_jwt)

        sent = {}

        class _Sess:
            def request(self, method, url, data=None, headers=None,
                        timeout=None, **kw):
                sent.update(method=method, url=url, data=data,
                            headers=dict(headers or {}), kw=kw)
                return _Response()

        monkeypatch.setattr(pmx, "_session_get", lambda: _Sess())
        monkeypatch.setattr(pmx, "_private_key", lambda: "pem")
        monkeypatch.setattr(pmx, "pace", lambda *a, **k: None)
        monkeypatch.setattr(pmx, "_env", lambda name: {
            "PMX_CLIENT_ID": "client-id", "PMX_KEY_ID": "key-id"}[name])

        tok, ttl = pmx._mint_token()
        assert tok == "t"
        assert fake_jwt.claims["aud"] == EXPECTED_ASSERTION_AUD
        assert sent["data"]["audience"] == EXPECTED_TOKEN_AUDIENCE
        assert sent["headers"]["Content-Type"] == FORM
        assert "json" not in sent["kw"]


# ── the workflow's own inline exchange ───────────────────────────────

class TestTheWorkflowSignsTheSameThing:

    WF = HERE.parents[2] / ".github" / "workflows" / "pmx-preprod.yml"

    def test_the_inline_assertion_signs_the_token_endpoint(self):
        src = self.WF.read_text()
        assert '"aud": f"https://{dom}/oauth/token"' in src

    def test_the_inline_assertion_no_longer_signs_the_bare_issuer(self):
        src = self.WF.read_text()
        assert '"aud": f"https://{dom}/"' not in src

    def test_the_request_audience_is_still_the_api_base(self):
        src = self.WF.read_text()
        assert "PMX_AUDIENCE: https://api.preprod.polymarketexchange.com" in src
        assert '"audience": aud' in src

    def test_the_inline_body_is_still_form_encoded(self):
        src = self.WF.read_text()
        assert '"Content-Type": "application/x-www-form-urlencoded"' in src


# ── the gRPC hostname: four spellings, three candidates, no rotation ──

class TestTheGrpcHostnameIsNotSettled:

    NO_ENV = "grpc-api.polymarketexchange.com:443"

    def test_the_three_documented_preprod_spellings_are_candidates(self):
        assert ops.GRPC_CANDIDATES == (
            "grpc-api.preprod.polymarketexchange.com:443",
            "grpc-preprod.polymarketexchange.com:443",
            "grpc.preprod.polymarketexchange.com:443",
        )

    def test_every_candidate_passes_the_preprod_boundary(self):
        for target in ops.GRPC_CANDIDATES:
            assert ops.assert_preprod(target) == target

    def test_the_spelling_with_no_environment_is_refused(self):
        # /changelog names grpc-api.polymarketexchange.com with no env at
        # all. An unqualified host could be PRODUCTION, so it is not a
        # candidate and the boundary refuses it.
        with pytest.raises(ops.NotPreprod):
            ops.assert_preprod(self.NO_ENV)
        assert self.NO_ENV not in ops.GRPC_CANDIDATES

    def test_the_configured_target_is_one_of_the_candidates(self):
        assert ops.GRPC_TARGET in ops.GRPC_CANDIDATES

    def test_production_stays_not_identified(self):
        assert ops.PRODUCTION_GRPC_TARGET == "NOT_IDENTIFIED"
        with pytest.raises(ops.NotPreprod):
            ops.assert_preprod(ops.PRODUCTION_GRPC_TARGET)


class TestTheProbeKeepsItsFiveStagesApart:

    def test_the_five_stages_are_named(self):
        assert ops.GRPC_STAGES == ("DNS_RESOLUTION", "TCP_TLS_CONNECTION",
                                   "GRPC_CHANNEL_READY", "AUTHENTICATED_RPC",
                                   "PERMISSION_RESULT")

    def test_a_host_that_fails_dns_says_nothing_about_the_service(self):
        out = ops.grpc_probe_verdict([
            {"target": "grpc-preprod.polymarketexchange.com:443",
             "stages": {s: None for s in ops.GRPC_STAGES} |
                       {ops.G_DNS: False}}])
        assert out["authenticatedRpc"] == []
        assert out["reachedStage"][ops.G_RPC] == []
        assert "not evidence that the institutional gRPC" in out["meaning"]

    def test_one_host_failing_does_not_hide_another_succeeding(self):
        out = ops.grpc_probe_verdict([
            {"target": "a.preprod.polymarketexchange.com:443",
             "stages": {s: False for s in ops.GRPC_STAGES}},
            {"target": "b.preprod.polymarketexchange.com:443",
             "stages": {s: True for s in ops.GRPC_STAGES}}])
        assert out["authenticatedRpc"] == \
            ["b.preprod.polymarketexchange.com:443"]

    def test_the_probe_never_rotates_the_configured_target(self):
        out = ops.grpc_probe_verdict([
            {"target": "grpc.preprod.polymarketexchange.com:443",
             "stages": {s: True for s in ops.GRPC_STAGES}}])
        assert out["targetRotated"] is False
        assert out["configuredTarget"] == ops.GRPC_TARGET
        assert ops.GRPC_TARGET == "grpc-api.preprod.polymarketexchange.com:443"
        # it reports what reached which stage; it does not pick a winner
        assert not any(k.lower().startswith("use") for k in out)

    def test_the_probe_verdict_carries_production_as_not_identified(self):
        out = ops.grpc_probe_verdict([])
        assert out["productionGrpcTarget"] == "NOT_IDENTIFIED"
        assert out["environment"] == "PREPROD"

    def test_the_verdict_is_serializable_as_a_receipt(self):
        out = ops.receipt("grpc-probe", **ops.grpc_probe_verdict([]))
        assert json.loads(json.dumps(out, default=str))["environment"] == \
            "PREPROD"
