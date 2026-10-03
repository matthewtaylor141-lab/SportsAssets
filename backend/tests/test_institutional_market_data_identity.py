"""THE MARKET-DATA IDENTITY: inventory, fingerprints, distinctness, the guard,
and the read-only permissions probe. Fakes only; no network.

  §1  inventory: each candidate's type, presence, identifier fingerprints
      (the probe's own sha256[:12]), verifiable scopes; no value leaks
  §2  distinctness: by value in-process, by fingerprint against another
      service's report, by credential class -- and False on any match
  §3  the guard refuses a market-data credential equal to the retail
      execution key or the funded key (key id, secret or fingerprint)
  §4  the permissions probe: scopes from our own token, whoami allow-listed,
      write:orders made VISIBLE, never a token; refused before any call when
      the guard refuses
  §5  the subscription and the probe route use the same rule
"""

from __future__ import annotations

import base64
import json
import types

import pytest

from sportsassets import bettor_market_subscription as SUB
from sportsassets import execmirror_probe as EP
from sportsassets import market_data_identity as MDI
from sportsassets import pmx_institutional as pmx

RETAIL_KID = "11111111-2222-3333-4444-555555555555"
RETAIL_SEC = "retail-secret-DO-NOT-LEAK-aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
FUNDED_KID = "99999999-8888-7777-6666-555555555555"
FUNDED_SEC = "funded-secret-DO-NOT-LEAK-bbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
PMX_CID = "pmxclientidnotreal0123456789abcd"
PMX_KID = "pmx-kid-not-real-12345"
PMX_PK = base64.b64encode(b"-----BEGIN RSA PRIVATE KEY-----\nNOTAKEY\n"
                          b"-----END RSA PRIVATE KEY-----\n").decode()
PMX_PART = "firms/test-firm/users/not-a-real-participant"

SECRETS = (RETAIL_SEC, FUNDED_SEC, PMX_PK, PMX_CID, PMX_KID, PMX_PART,
           RETAIL_KID, FUNDED_KID)


def _workers_env(**extra):
    """The sportsassets-workers shape: PMX_* and the funded key, no retail
    execution key, no PMUS_MD_*."""
    env = {"PMX_CLIENT_ID": PMX_CID, "PMX_KEY_ID": PMX_KID,
           "PMX_PRIVATE_KEY_B64": PMX_PK, "PMX_PARTICIPANT_ID": PMX_PART,
           "PMUS_KEY_ID": FUNDED_KID, "PMUS_SECRET_KEY": FUNDED_SEC}
    env.update(extra)
    return env


def _api_env(**extra):
    """The sportsassets-api shape: retail execution + funded keys, no PMX_*,
    no PMUS_MD_*."""
    env = {"PMUS_EXECMIRROR_KEY_ID": RETAIL_KID,
           "PMUS_EXECMIRROR_SECRET_KEY": RETAIL_SEC,
           "PMUS_KEY_ID": FUNDED_KID, "PMUS_SECRET_KEY": FUNDED_SEC,
           "EDGE_PMUS_KEY_ID": "edge-kid-not-real"}
    env.update(extra)
    return env


def _cand(inv, name):
    return next(c for c in inv["candidates"] if c["candidate"] == name)


def _no_leak(obj):
    blob = json.dumps(obj, default=str)
    for s in SECRETS:
        assert s not in blob, "a credential value leaked into a report"


# ── §1 inventory ─────────────────────────────────────────────────────

def test_the_fingerprint_is_the_probe_fingerprint():
    assert MDI.fingerprint("abc") == EP.fingerprint("abc")
    assert MDI.fingerprint("") is None


def test_workers_shape_reports_pmx_as_the_institutional_candidate():
    inv = MDI.inventory(_workers_env())
    p = _cand(inv, MDI.PMX)
    assert p["credential_type"] == MDI.TYPE_PMX and p["institutional"]
    assert p["present"] and p["complete"] and p["complete_for_account_paths"]
    assert p["fingerprints"] == {"client_id": MDI.fingerprint(PMX_CID),
                                 "key_id": MDI.fingerprint(PMX_KID)}
    assert p["scopes"]["verifiable"] is True
    assert p["interface"]["participant_id_required_for_market_data"] is False
    assert p["interface"]["grpc_target"] == \
        "grpc-api.prod.polymarketexchange.com:443"
    m = _cand(inv, MDI.PMUS_MD)
    assert not m["present"] and m["fingerprints"]["key_id"] is None
    assert inv["institutional_market_data_credential"] == MDI.PMX
    assert inv["verdict"] == "INSTITUTIONAL_MARKET_DATA_CREDENTIAL_PRESENT"
    assert inv["execution_identities_in_this_process"]["retail_execution"] \
        == {"key_id_env": "PMUS_EXECMIRROR_KEY_ID", "present": False,
            "fingerprint": None}
    _no_leak(inv)


def test_api_shape_has_no_market_data_credential_and_says_so():
    inv = MDI.inventory(_api_env())
    assert not _cand(inv, MDI.PMX)["present"]
    assert not _cand(inv, MDI.PMUS_MD)["present"]
    assert inv["usable_for_market_data"] == []
    assert inv["verdict"] == "NO_MARKET_DATA_CREDENTIAL_IN_THIS_PROCESS"
    known = inv["execution_identities_in_this_process"]
    assert known["retail_execution"]["fingerprint"] == \
        MDI.fingerprint(RETAIL_KID)
    assert known["funded"]["fingerprint"] == MDI.fingerprint(FUNDED_KID)
    _no_leak(inv)


def test_pmx_without_a_participant_is_still_complete_for_market_data():
    env = _workers_env()
    del env["PMX_PARTICIPANT_ID"]
    p = MDI.pmx_candidate(env)
    assert p["complete"] is True and p["complete_for_account_paths"] is False


def test_a_pmus_md_key_is_retail_and_its_scopes_are_not_verifiable():
    inv = MDI.inventory(_api_env(PMUS_MD_KEY_ID="md-kid-not-real",
                                 PMUS_MD_SECRET_KEY="md-sec-not-real"))
    m = _cand(inv, MDI.PMUS_MD)
    assert m["credential_type"] == MDI.TYPE_PMUS and not m["institutional"]
    assert m["scopes"]["verifiable"] is False
    assert inv["verdict"] == "RETAIL_MARKET_DATA_KEY_ONLY"
    assert inv["institutional_market_data_credential"] is None
    assert "md-sec-not-real" not in json.dumps(inv)


# ── §2 distinctness ──────────────────────────────────────────────────

def test_distinct_by_value_when_both_are_in_the_process():
    m = MDI.pmus_md_candidate(_api_env(PMUS_MD_KEY_ID="md-kid",
                                       PMUS_MD_SECRET_KEY="md-sec"))
    assert m["distinct_from_retail_execution"] is True
    assert m["distinct_from_retail_execution_basis"] == \
        "COMPARED_BY_VALUE_IN_THIS_PROCESS"
    assert m["distinct_from_funded"] is True


def test_same_class_without_the_other_key_is_unknown_not_true():
    env = {"PMUS_MD_KEY_ID": "md-kid", "PMUS_MD_SECRET_KEY": "md-sec"}
    m = MDI.pmus_md_candidate(env)
    assert m["distinct_from_retail_execution"] is None
    assert m["distinct_from_funded"] is None


def test_peer_fingerprints_settle_it_across_services():
    env = {"PMUS_MD_KEY_ID": "md-kid", "PMUS_MD_SECRET_KEY": "md-sec"}
    peers = {"retail_execution": MDI.fingerprint(RETAIL_KID),
             "funded": MDI.fingerprint(FUNDED_KID)}
    m = MDI.pmus_md_candidate(env, peers)
    assert m["distinct_from_retail_execution"] is True
    assert m["distinct_from_retail_execution_basis"] == \
        "COMPARED_BY_FINGERPRINT_TO_PEER_REPORT"
    # the same key on the other service: a fingerprint match is a refusal
    m = MDI.pmus_md_candidate(env, {"retail_execution":
                                    MDI.fingerprint("md-kid")})
    assert m["distinct_from_retail_execution"] is False
    assert m["distinct_from_retail_execution_basis"] == \
        "FINGERPRINT_EQUAL_TO_PEER_REPORT"


def test_pmx_is_distinct_by_class_when_no_retail_key_is_present():
    p = MDI.pmx_candidate(_workers_env())
    assert p["distinct_from_retail_execution"] is True
    assert p["distinct_from_retail_execution_basis"] == \
        "DIFFERENT_CREDENTIAL_CLASS"
    # the funded key IS in the workers process: compared by value
    assert p["distinct_from_funded"] is True
    assert p["distinct_from_funded_basis"] == \
        "COMPARED_BY_VALUE_IN_THIS_PROCESS"


@pytest.mark.parametrize("field", ["PMX_CLIENT_ID", "PMX_KEY_ID",
                                   "PMX_PARTICIPANT_ID"])
def test_a_pmx_identifier_equal_to_an_execution_key_is_not_distinct(field):
    p = MDI.pmx_candidate(_workers_env(**{field: FUNDED_KID}))
    assert p["distinct_from_funded"] is False
    assert p["distinct_from_funded_basis"] == "IDENTIFIER_EQUAL"
    p = MDI.pmx_candidate(_workers_env(
        **{field: RETAIL_KID, "PMUS_EXECMIRROR_KEY_ID": RETAIL_KID}))
    assert p["distinct_from_retail_execution"] is False


def test_a_shared_secret_is_not_distinct():
    p = MDI.pmx_candidate(_workers_env(PMX_PRIVATE_KEY_B64=FUNDED_SEC))
    assert p["distinct_from_funded"] is False
    assert p["distinct_from_funded_basis"] == "SECRET_EQUAL"


# ── §3 the guard ─────────────────────────────────────────────────────

def test_the_guard_admits_a_distinct_pmx():
    assert MDI.guard(MDI.PMX, env=_workers_env()) is None


def test_the_guard_refuses_absent_unknown_and_execution_identities():
    assert MDI.guard(MDI.PMX, env=_api_env()) == MDI.G_ABSENT
    assert MDI.guard("NOPE", env=_api_env()) == MDI.G_UNKNOWN
    assert MDI.guard(MDI.PMX, env=_workers_env(PMX_KEY_ID=FUNDED_KID)) == \
        MDI.G_IS_FUNDED
    env = _api_env(PMUS_MD_KEY_ID=RETAIL_KID, PMUS_MD_SECRET_KEY="x")
    assert MDI.guard(MDI.PMUS_MD, env=env) == MDI.G_IS_RETAIL_EXECUTION
    env = _api_env(PMUS_MD_KEY_ID="other", PMUS_MD_SECRET_KEY=FUNDED_SEC)
    assert MDI.guard(MDI.PMUS_MD, env=env) == MDI.G_IS_FUNDED
    peers = {"funded": MDI.fingerprint(PMX_KID)}
    env = {k: v for k, v in _workers_env().items()
           if not k.startswith("PMUS_")}
    assert MDI.guard(MDI.PMX, env=env, peer_fingerprints=peers) == \
        MDI.G_IS_FUNDED


def test_a_refused_candidate_is_not_usable_in_the_inventory():
    inv = MDI.inventory(_workers_env(PMX_CLIENT_ID=FUNDED_KID))
    assert inv["usable_for_market_data"] == []
    assert inv["institutional_market_data_credential"] is None


def test_guard_key_pair():
    env = _api_env()
    assert MDI.guard_key_pair("md", "mds", env=env) is None
    assert MDI.guard_key_pair(RETAIL_KID, "x", env=env) == \
        MDI.G_IS_RETAIL_EXECUTION
    assert MDI.guard_key_pair("x", FUNDED_SEC, env=env) == MDI.G_IS_FUNDED
    assert MDI.guard_key_pair("", "x", env=env) == MDI.G_ABSENT


# ── §4 the read-only permissions probe ───────────────────────────────

def _token(scopes):
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode() \
            .rstrip("=")
    return "%s.%s.sig" % (b64({"alg": "RS256"}), b64({"scope": scopes}))


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class _Session:
    def __init__(self, whoami_status=200, whoami_body=None):
        self.calls = []
        self.whoami_status = whoami_status
        self.whoami_body = whoami_body or {
            "firm": "firms/test-firm", "firmType": "FIRM_TYPE_PARTICIPANT",
            "user": "firms/test-firm/users/someone", "email": "x@y.z",
            "token": "never-echoed"}

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append((method, url))
        assert method == "GET" and url.endswith("/v1/whoami")
        return _Resp(self.whoami_status, self.whoami_body)


class _Client:
    """Stands in for pmx.Institutional: no Auth0, no socket."""

    def __init__(self, scopes, status=200, whoami_status=200):
        self._scopes, self._status = scopes, status
        self.session = _Session(whoami_status)
        self.last_scopes, self.last_auth_ms = [], 1.0
        self.minted = 0
        self._tok = _token(scopes)

    def _mint(self):
        self.minted += 1
        if self._status != 200:
            return self._status, None, 0, "access_denied"
        self.last_scopes = pmx.scopes_of(self._tok)
        return 200, self._tok, 86400, ""

    def token(self):
        return self._tok if self._status == 200 else None

    def read(self, name, symbol=""):
        raise AssertionError("verify_permissions must not read market data")


PROD_SCOPES = ("read:marketdata read:instruments read:l2marketdata "
               "read:orders write:orders read:reports read:positions "
               "read:dropcopy read:accounts write:accounts read:funding")


def test_permissions_probe_reports_scopes_and_makes_write_visible(monkeypatch):
    monkeypatch.setattr(pmx, "presence", lambda env=None: {"verdict": None})
    c = _Client(PROD_SCOPES)
    out = MDI.verify_permissions(c, env=_workers_env())
    assert out["state"] == "VERIFIED" and out["auth_status"] == pmx.A_OK
    assert out["read_marketdata"] and out["read_l2marketdata"]
    assert out["write_orders_granted"] is True
    assert out["venue_enforced_read_only"] is False
    assert out["ORDER_SUBMISSION_IMPLEMENTATION"] == "NONE"
    ident = out["whoami"]["identity"]
    assert ident["firm"] == "firms/test-firm"
    assert ident["firmType"] == "FIRM_TYPE_PARTICIPANT"
    assert ident["user_fingerprint"] == \
        MDI.fingerprint("firms/test-firm/users/someone")
    blob = json.dumps(out)
    assert c._tok not in blob and "never-echoed" not in blob
    assert "x@y.z" not in blob and "firms/test-firm/users/someone" not in blob
    assert c.session.calls == [
        ("GET", "https://api.prod.polymarketexchange.com/v1/whoami")]
    _no_leak(out)


def test_a_read_only_token_reads_as_venue_enforced_read_only(monkeypatch):
    monkeypatch.setattr(pmx, "presence", lambda env=None: {"verdict": None})
    out = MDI.verify_permissions(
        _Client("read:marketdata read:l2marketdata read:instruments"),
        env=_workers_env())
    assert out["write_orders_granted"] is False
    assert out["venue_enforced_read_only"] is True


def test_a_rejected_token_stops_before_whoami(monkeypatch):
    monkeypatch.setattr(pmx, "presence", lambda env=None: {"verdict": None})
    c = _Client("", status=401)
    out = MDI.verify_permissions(c, env=_workers_env())
    assert out["state"] == pmx.A_REJECTED and c.session.calls == []


def test_the_guard_refuses_before_any_venue_call():
    c = _Client(PROD_SCOPES)
    out = MDI.verify_permissions(c, env=_workers_env(PMX_KEY_ID=FUNDED_KID))
    assert out["state"] == MDI.G_IS_FUNDED
    assert c.minted == 0 and c.session.calls == []
    out = MDI.verify_permissions(c, env=_api_env())
    assert out["state"] == MDI.G_ABSENT and c.minted == 0


def test_whoami_summary_allow_lists():
    s = MDI.whoami_summary({"firm": "f", "participant": "p", "secret": "s"})
    assert s["firm"] == "f" and s["participant_fingerprint"] == \
        MDI.fingerprint("p")
    assert "secret" not in {k for k in s if k != "keys"}
    assert "s" not in [v for k, v in s.items() if k != "keys"]
    assert MDI.whoami_summary(None) == {"shape": "NoneType"}


def test_the_module_has_no_order_path():
    import inspect
    src = inspect.getsource(MDI).lower()
    for verb in ("place_order", "create_order", "submit_order",
                 "cancel_order", "/v1/trading", "insertorder"):
        assert verb not in src


# ── §5 one rule everywhere ───────────────────────────────────────────

def test_the_probe_carries_the_inventory(monkeypatch):
    for k, v in _workers_env().items():
        monkeypatch.setenv(k, v)
    k = EP.keys_present()
    inv = k["market_data"]["inventory"]
    assert inv["institutional_market_data_credential"] == MDI.PMX
    _no_leak(k)


def test_the_subscription_refuses_an_execution_secret(monkeypatch):
    built = []
    monkeypatch.setattr(SUB, "MarketSubscription",
                        lambda *a, **k: built.append(a))
    SUB.reset()
    monkeypatch.setenv(SUB.ENV_FLAG, "on")
    monkeypatch.delenv(SUB.ENV_KEY_SOURCE, raising=False)
    monkeypatch.setenv("PMUS_EXECMIRROR_SECRET_KEY", RETAIL_SEC)
    got = SUB.start_default(settings=types.SimpleNamespace(
        pmus_key_id=FUNDED_KID, pmus_secret_key=FUNDED_SEC,
        pmus_md_key_id="some-other-kid", pmus_md_secret_key=RETAIL_SEC))
    assert got["state"] == SUB.S_MD_KEY_IS_EXECUTION_KEY and not built
    SUB.reset()
    got = SUB.start_default(settings=types.SimpleNamespace(
        pmus_key_id=FUNDED_KID, pmus_secret_key=FUNDED_SEC,
        pmus_md_key_id="some-other-kid", pmus_md_secret_key=FUNDED_SEC))
    assert got["state"] == SUB.S_MD_KEY_IS_EXECUTION_KEY and not built
    SUB.reset()


def test_the_subscription_refuses_the_shared_funded_key(monkeypatch):
    built = []
    monkeypatch.setattr(SUB, "MarketSubscription",
                        lambda *a, **k: built.append(a))
    SUB.reset()
    monkeypatch.setenv(SUB.ENV_FLAG, "on")
    monkeypatch.setenv(SUB.ENV_KEY_SOURCE, SUB.KEY_SHARED)
    got = SUB.start_default(settings=types.SimpleNamespace(
        pmus_key_id=FUNDED_KID, pmus_secret_key=FUNDED_SEC))
    assert got["state"] == SUB.S_MD_KEY_IS_EXECUTION_KEY and not built
    SUB.reset()


def test_the_worker_boot_heartbeat_carries_the_identity_report(monkeypatch):
    """sportsassets-workers is where PMX_* lives: its institutional_md boot
    heartbeat is where the inventory (fingerprints, never values) is read."""
    import asyncio

    from sportsassets.workers import institutional_md as W

    for k in list(_workers_env()) + ["PMUS_EXECMIRROR_KEY_ID"]:
        monkeypatch.delenv(k, raising=False)
    for k, v in _workers_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("INSTITUTIONAL_MD", raising=False)
    monkeypatch.setattr(W.pmx, "presence",
                        lambda env=None: {"verdict": "CREDENTIAL_MISSING",
                                          "missing": ["X"]})
    beats = []

    class Stop(Exception):
        pass

    async def fake_heartbeat(service, status, payload):
        beats.append((service, status, payload))
        raise Stop()

    monkeypatch.setattr(W, "heartbeat", fake_heartbeat)
    with pytest.raises(Stop):
        asyncio.run(W.run())
    payload = beats[0][2]
    inv = payload["marketDataIdentity"]
    assert inv["institutional_market_data_credential"] == MDI.PMX
    p = _cand(inv, MDI.PMX)
    assert p["fingerprints"]["key_id"] == MDI.fingerprint(PMX_KID)
    assert p["distinct_from_funded"] is True
    _no_leak(payload)


def test_the_admin_probe_route_serves_market_data_and_md_verify(monkeypatch):
    import asyncio

    from fastapi import Response

    from sportsassets.api import app as A

    for k, v in _api_env().items():
        monkeypatch.setenv(k, v)
    for k in ("PMX_CLIENT_ID", "PMX_KEY_ID", "PMX_PRIVATE_KEY_B64",
              "PMX_PARTICIPANT_ID", "PMUS_MD_KEY_ID", "PMUS_MD_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    out = asyncio.run(A.admin_execmirror_probe(
        Response(), {"action": "market_data",
                     "peer_fingerprints": {"funded": "abc", "junk": "x"}}))
    assert out["verdict"] == "NO_MARKET_DATA_CREDENTIAL_IN_THIS_PROCESS"
    assert "stream" in out
    _no_leak(out)
    out = asyncio.run(A.admin_execmirror_probe(Response(),
                                               {"action": "md_verify"}))
    assert out["state"] == MDI.G_ABSENT and out["read_only"] is True
