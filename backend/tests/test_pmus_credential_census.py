"""P1 closeout: PMUS retail credential routing -- a shape-only census of every
candidate env NAME in both services, the credential-class control no longer
hiding the funded slot behind the execution-mirror slot, and the API's funded
readers refusing a non-Ed25519 slot by name before any client.

Production at 08828d04 (render-ops env-keys 2026-10-07, names only):
  sportsassets-api      PMUS_KEY_ID/PMUS_SECRET_KEY (PMX-shaped),
                        PMUS_EXECMIRROR_KEY_ID/_SECRET_KEY, EDGE_PMUS_KEY_ID/
                        _SECRET_KEY, PMX_*, KALSHI_*, VAPID_*
  sportsassets-workers  PMUS_KEY_ID/PMUS_SECRET_KEY (PMX-shaped), PMX_*,
                        KALSHI_*, VAPID_* -- no other PMUS-named pair
Fakes only; no network; no value, length or prefix may appear in any output.
"""
from __future__ import annotations

import asyncio
import base64
import json
import types

import pytest
from cryptography.hazmat.primitives import serialization as S
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from sportsassets import pmus_credential_census as PCC
from sportsassets import venue_key as VK
from sportsassets.redteam import controls as C
from sportsassets.workers import mirror_shadow as MS

RSA_PEM = rsa.generate_private_key(public_exponent=65537, key_size=2048
                                   ).private_bytes(
    S.Encoding.PEM, S.PrivateFormat.PKCS8, S.NoEncryption()).decode()
RSA_B64 = base64.b64encode(RSA_PEM.encode()).decode()
_ED = ed25519.Ed25519PrivateKey.generate()
_SEED = _ED.private_bytes(S.Encoding.Raw, S.PrivateFormat.Raw,
                          S.NoEncryption())
_PUB = _ED.public_key().public_bytes(S.Encoding.Raw, S.PublicFormat.Raw)
RETAIL_SEC = base64.b64encode(_SEED + _PUB).decode()            # SDK form
RETAIL_HEX = _SEED.hex()                                         # re-encodable
EDGE_SEC = base64.b64encode(bytes(range(64))).decode()
RETAIL_KID = "11111111-2222-3333-4444-555555555555"
EDGE_KID = "77777777-8888-7777-6666-555555555555"
FUNDED_KID = "99999999-8888-7777-6666-555555555555"
PMX_CID = "pmxclientidnotreal0123456789abcd"                     # 32 chars
PMX_KID = "pmx-kid-not-real-12345"
VAPID = base64.urlsafe_b64encode(bytes(range(1, 33))).decode().rstrip("=")
KALSHI_KID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def workers_env(**extra):
    """sportsassets-workers by NAME, as production holds it now: the funded
    slot holds the PMX client (client id + the same RSA key as raw PEM)."""
    env = {"PMUS_KEY_ID": PMX_CID, "PMUS_SECRET_KEY": RSA_PEM,
           "PMX_CLIENT_ID": PMX_CID, "PMX_KEY_ID": PMX_KID,
           "PMX_PRIVATE_KEY_B64": RSA_B64,
           "PMX_PARTICIPANT_ID": "firms/x/users/not-real",
           "KALSHI_API_KEY_ID": KALSHI_KID,
           "KALSHI_PRIVATE_KEY_PEM": "not-a-real-kalshi-key",
           "VAPID_PRIVATE_KEY": VAPID, "PMUS_MIRROR": "off",
           "PMUS_MIRROR_WHALES": "abc", "DATABASE_URL": "postgresql://x"}
    env.update(extra)
    return env


def api_env(**extra):
    env = workers_env(PMUS_EXECMIRROR_KEY_ID=RETAIL_KID,
                      PMUS_EXECMIRROR_SECRET_KEY=RETAIL_SEC,
                      EDGE_PMUS_KEY_ID=EDGE_KID, EDGE_PMUS_SECRET_KEY=EDGE_SEC)
    env.update(extra)
    return env


SECRETS = (RSA_PEM, RSA_B64, RETAIL_SEC, RETAIL_HEX, EDGE_SEC, RETAIL_KID,
           EDGE_KID, FUNDED_KID, PMX_CID, PMX_KID, VAPID, KALSHI_KID)


def no_leak(obj):
    blob = json.dumps(obj, default=str)
    for v in SECRETS:
        assert v not in blob, "a credential value leaked"
    for v in (RETAIL_SEC, RSA_PEM, PMX_CID):
        # no length either: a number equal to a secret's length never
        # appears as a value
        assert ": %d" % len(v) not in blob and ": %d," % len(v) not in blob


# ── the census ──────────────────────────────────────────────────────────

def test_workers_production_shape_has_no_retail_key_under_any_name():
    got = PCC.census(workers_env(), service="sportsassets-workers")
    no_leak(got)
    assert got["values_exposed"] is False
    assert got["funded_slot_shape"] == PCC.P_RSA
    assert got["verdict"] == PCC.V_NONE
    assert got["retail_ed25519_pairs_under_other_names"] == []
    # a VAPID P-256 scalar is 32 bytes and a Kalshi id is a UUID: their
    # names fix their class, so neither is a PMUS candidate
    assert got["ed25519_material_under_unpaired_names"] == []
    assert "VAPID_PRIVATE_KEY" not in got["other_candidate_names"]
    assert "KALSHI_API_KEY_ID" not in got["other_candidate_names"]
    # the PMX collision is named by NAME: same identifier, same RSA key
    f = got["pairs"]["PMUS_FUNDED"]
    assert "PMX_CLIENT_ID" in f["key_id_equals"]
    assert "PMX_PRIVATE_KEY_B64" in f["secret_equals"]
    assert f["readers"]


def test_api_production_shape_reports_retail_keys_elsewhere_never_routes():
    got = PCC.census(api_env(), service="sportsassets-api")
    no_leak(got)
    assert got["funded_slot_shape"] == PCC.P_RSA
    assert got["verdict"] == PCC.V_ELSEWHERE
    assert got["retail_ed25519_pairs_under_other_names"] == [
        "EDGE_PMUS", "PMUS_EXECMIRROR"]
    assert got["pairs"]["PMUS_EXECMIRROR"]["role"].startswith(
        "EXECUTION_MIRROR_ACCOUNT_SEPARATE")
    assert got["pairs"]["EDGE_PMUS"]["role"] == \
        "EDGE_ENGINE_KEY_NOT_APPROVED_FOR_REUSE"
    assert got["account_identity"].startswith("NOT_PROVABLE_BY_SHAPE")
    # distinct identities are reported as distinct (no equality)
    assert got["pairs"]["PMUS_EXECMIRROR"]["key_id_equals"] == []
    assert got["pairs"]["EDGE_PMUS"]["secret_equals"] == []


def test_a_retail_key_under_an_unexpected_name_is_found_by_shape():
    got = PCC.census(workers_env(POLYMARKET_US_SECRET=RETAIL_SEC,
                                 POLYMARKET_US_KEY=RETAIL_KID))
    no_leak(got)
    o = got["other_candidate_names"]
    assert o["POLYMARKET_US_SECRET"] == PCC.N_ED25519
    assert o["POLYMARKET_US_KEY"] == PCC.N_UUID
    assert got["ed25519_material_under_unpaired_names"] == [
        "POLYMARKET_US_SECRET"]
    assert got["verdict"] == PCC.V_ELSEWHERE
    # an operator flag under a PMUS name is listed by shape, never valued
    assert o["PMUS_MIRROR"] == PCC.N_OTHER


def test_random_32_byte_tokens_are_listed_never_counted():
    """Render `generateValue: true` (ADMIN_TOKEN on the workers) is base64 of
    32 random bytes -- Ed25519-SHAPED, not a PMUS key. Listed by name, and
    the verdict stays 'no retail key under any name'."""
    admin = base64.b64encode(bytes(range(100, 132))).decode()
    got = PCC.census(workers_env(ADMIN_TOKEN=admin, SOME_TOKEN=RETAIL_HEX))
    assert got["other_candidate_names"]["ADMIN_TOKEN"] == PCC.N_ED25519
    assert got["ed25519_shaped_bytes_under_unrelated_names"] == [
        "ADMIN_TOKEN", "SOME_TOKEN"]
    assert got["ed25519_material_under_unpaired_names"] == []
    assert got["verdict"] == PCC.V_NONE
    assert admin not in json.dumps(got)


def test_a_real_funded_key_in_any_encoding_is_usable():
    for sec, want in ((RETAIL_SEC, PCC.P_RETAIL),
                      (RETAIL_HEX, PCC.P_RETAIL_REENCODE)):
        got = PCC.census(workers_env(PMUS_KEY_ID=FUNDED_KID,
                                     PMUS_SECRET_KEY=sec))
        no_leak(got)
        assert got["funded_slot_shape"] == want
        assert got["verdict"] == PCC.V_FUNDED_USABLE


@pytest.mark.parametrize("kid,sec,want", [
    ("", "", PCC.P_ABSENT), (FUNDED_KID, "", PCC.P_KEY_ID_ONLY),
    ("", RETAIL_SEC, PCC.P_SECRET_ONLY), (PMX_CID, RSA_PEM, PCC.P_RSA),
    (PMX_CID, RSA_B64, PCC.P_RSA), (FUNDED_KID, "nope", PCC.P_OTHER)])
def test_pair_shapes(kid, sec, want):
    assert PCC.pair_shape(kid, sec) == want


# ── the credential-class control no longer hides the funded slot ───────

def test_the_execmirror_slot_no_longer_hides_an_rsa_funded_slot():
    api = C.credential_classes(api_env())
    no_leak(api)
    # before: PMUS read the execution-mirror pair first -> ED25519 on the API
    assert api["PMUS"] == "POLYMARKET_EXCHANGE_RSA_M2M"
    assert api["PMUS_SLOTS"] == {
        "PMUS_KEY_ID/PMUS_SECRET_KEY": "POLYMARKET_EXCHANGE_RSA_M2M",
        "PMUS_EXECMIRROR_KEY_ID/PMUS_EXECMIRROR_SECRET_KEY":
            "POLYMARKET_US_ED25519"}
    g = C.credentials({"api": api,
                       "workers": C.credential_classes(workers_env())})
    assert g["status"] == C.RED
    assert "CREDENTIAL_CLASS_MISMATCH:PMUS:POLYMARKET_EXCHANGE_RSA_M2M" in \
        g["blockers"]
    act = " ".join(g["evidence"]["owner_actions"])
    assert "api PMUS_KEY_ID/PMUS_SECRET_KEY" in act
    assert "workers PMUS_KEY_ID/PMUS_SECRET_KEY" in act
    no_leak(g)


def test_both_pmus_slots_retail_is_green_and_one_slot_alone_still_counts():
    ok = C.credential_classes(api_env(PMUS_KEY_ID=FUNDED_KID,
                                      PMUS_SECRET_KEY=RETAIL_SEC))
    assert ok["PMUS"] == "POLYMARKET_US_ED25519"
    assert C.credentials({"api": ok})["evidence"]["verdicts"]["PMUS"] == \
        "MATCHES"
    only_exec = C.credential_classes({"PMUS_EXECMIRROR_KEY_ID": RETAIL_KID,
                                      "PMUS_EXECMIRROR_SECRET_KEY":
                                          RETAIL_SEC})
    assert only_exec["PMUS"] == "POLYMARKET_US_ED25519"
    assert C.credential_classes({})["PMUS"] is None


# ── the API's funded readers refuse by name, before any client ─────────

def test_signing_secret_refuses_by_name_and_reencodes_the_same_key():
    assert VK.R_NOT_ED25519 == MS.R_PMUS_SECRET_NOT_ED25519
    for bad in (RSA_PEM, RSA_B64):
        with pytest.raises(VK.SecretNotEd25519) as e:
            VK.signing_secret(bad)
        assert str(e.value) == VK.R_NOT_ED25519
    assert VK.signing_secret(RETAIL_HEX) == base64.b64encode(_SEED).decode()
    assert VK.signing_secret("") == ""


def _no_client(monkeypatch, made):
    import polymarket_us

    def ctor(**kw):
        made.append(sorted(kw))
        raise AssertionError("a PMUS client was constructed")
    monkeypatch.setattr(polymarket_us, "PolymarketUS", ctor)


def _settings(monkeypatch, module, kid, sec):
    cfg = types.SimpleNamespace(pmus_key_id=kid, pmus_secret_key=sec)
    monkeypatch.setattr(module, "settings", lambda: cfg)


def test_track_record_fetch_refuses_before_any_client(monkeypatch):
    from sportsassets.api import track_record as TR
    made: list = []
    _no_client(monkeypatch, made)
    _settings(monkeypatch, TR, PMX_CID, RSA_PEM)
    monkeypatch.setattr(TR, "_paged", lambda *a, **k: pytest.fail(
        "a page was requested"))
    with pytest.raises(VK.SecretNotEd25519):
        TR._fetch_raw()
    assert made == []


def test_pmus_account_snapshot_names_the_refusal(monkeypatch):
    from sportsassets.api import pmus_account as PA
    made: list = []
    _no_client(monkeypatch, made)
    _settings(monkeypatch, PA, PMX_CID, RSA_PEM)
    PA._cache.update(ts=0.0, data=None)
    got = asyncio.run(PA.account_snapshot())
    assert got == {"configured": True, "error":
                   "SecretNotEd25519: PMUS_SECRET_SLOT_HOLDS_NO_ED25519_KEY"}
    assert made == []
    no_leak(got)


def test_pmus_account_hands_the_sdk_the_same_key_re_encoded(monkeypatch):
    import polymarket_us
    from sportsassets.api import pmus_account as PA
    seen: list = []

    class Fake:
        def __init__(self, **kw):
            seen.append(kw["secret_key"])
            self.portfolio = types.SimpleNamespace(
                positions=lambda p: {"positions": {}, "eof": True})
    monkeypatch.setattr(polymarket_us, "PolymarketUS", Fake)
    _settings(monkeypatch, PA, FUNDED_KID, RETAIL_HEX)
    assert PA._fetch_all_positions_sync() == {}
    assert seen == [base64.b64encode(_SEED).decode()]


def test_the_workers_boot_marker_carries_the_census_without_values(
        monkeypatch):
    from sportsassets.workers import all as W
    for k, v in workers_env().items():
        monkeypatch.setenv(k, v)
    got = W._boot_marker("2026-10-07T00:00:00Z")
    c = got["pmus_credential_census"]
    assert c["service"] == "sportsassets-workers"
    assert c["funded_slot_shape"] == PCC.P_RSA
    no_leak(c)
    no_leak(got["credential_classes"])
