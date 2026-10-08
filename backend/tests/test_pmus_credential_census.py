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


# ── review round: no class or owner action a readback does not carry ───

_ED_PEM = _ED.private_bytes(S.Encoding.PEM, S.PrivateFormat.PKCS8,
                            S.NoEncryption()).decode()


def test_an_ed25519_pem_is_never_called_rsa():
    """slot_shape calls ANY PEM 'RSA'. An Ed25519 PKCS#8 PEM (119 chars --
    the production KALSHI_PRIVATE_KEY_PEM length on both services) is not
    an RSA key: in the KALSHI slot it read KALSHI_RSA_API_KEY / MATCHES."""
    assert len(_ED_PEM) == 119
    k = C.credential_classes({"KALSHI_API_KEY_ID": KALSHI_KID,
                              "KALSHI_PRIVATE_KEY_PEM": _ED_PEM})
    assert k["KALSHI"] == "ED25519_PEM"
    g = C.credentials({"api": k})
    assert g["evidence"]["verdicts"]["KALSHI"] == "MISMATCH_PATH_BLOCKED"
    assert any("RSA only" in a for a in g["evidence"]["owner_actions"])
    # an RSA Kalshi key still matches
    r = C.credential_classes({"KALSHI_API_KEY_ID": KALSHI_KID,
                              "KALSHI_PRIVATE_KEY_PEM": RSA_PEM})
    assert r["KALSHI"] == "KALSHI_RSA_API_KEY"
    # an Ed25519 PEM in the funded PMUS slot is not "the PMX RSA class"
    p = C.credential_classes({"PMUS_KEY_ID": FUNDED_KID,
                              "PMUS_SECRET_KEY": _ED_PEM})
    assert p["PMUS"] == "ED25519_PEM"
    act = " ".join(C.credentials({"api": p})["evidence"]["owner_actions"])
    assert "RSA" not in act and "ED25519_PEM" in act
    no_leak(g)


def test_the_owner_action_states_the_class_found_and_survives_deploy_skew():
    odd = C.credential_classes({"PMUS_KEY_ID": FUNDED_KID,
                                "PMUS_SECRET_KEY": "truncated-paste"})
    act = " ".join(C.credentials({"api": odd})["evidence"]["owner_actions"])
    assert "UNRECOGNISED_SHAPE" in act and "RSA" not in act
    # workers still on a build without PMUS_SLOTS: still named
    old_workers = {"PMX": "POLYMARKET_EXCHANGE_RSA_M2M",
                   "PMUS": "POLYMARKET_EXCHANGE_RSA_M2M", "KALSHI": None}
    g = C.credentials({"api": C.credential_classes(api_env()),
                       "workers": old_workers})
    act = " ".join(g["evidence"]["owner_actions"])
    assert "api PMUS_KEY_ID/PMUS_SECRET_KEY holds" in act
    assert "workers PMUS holds POLYMARKET_EXCHANGE_RSA_M2M" in act


def test_the_completion_owner_action_carries_only_evidence():
    from sportsassets.completion import read as CR
    hb = {"detail": {"positions_source": {
        "source": "FUNDED_ACCOUNT_LEDGER_DERIVED",
        "primary_refusal": CR.R_PMUS_NOT_ED25519,
        "pmus_slot_is_pmx_rsa_client": True,
        "pmx_client_id_equals_pmus_key_id": True}}, "beat_at": 100.0}
    rsa_api = CR.api_funded_slot_shape(api_env())
    v = CR.venue_positions_block(hb, now=100.0, api_slot_shape=rsa_api)
    a = v["owner_action"]
    assert "sportsassets-workers (mirror_shadow heartbeat: the slot holds " \
        "the PMX RSA client)" in a
    assert "sportsassets-api (this process: RSA_PEM_PRIVATE_KEY_SHAPE" in a
    # the API slot fixed: only the workers are named
    ok_api = CR.api_funded_slot_shape(api_env(PMUS_KEY_ID=FUNDED_KID,
                                              PMUS_SECRET_KEY=RETAIL_SEC))
    a2 = CR.venue_positions_block(hb, now=100.0,
                                  api_slot_shape=ok_api)["owner_action"]
    assert "sportsassets-api" not in a2 and "sportsassets-workers" in a2
    # not read: pointed at the readback that has it, never asserted
    a3 = CR.venue_positions_block(hb, now=100.0)["owner_action"]
    assert "its slot is not read here" in a3
    assert "both slots hold" not in a + a2 + a3
    no_leak(v)


def test_track_record_refusal_is_labelled_logged_once_never_respawned(
        monkeypatch, caplog):
    from sportsassets.api import track_record as TR
    made: list = []
    _no_client(monkeypatch, made)
    _settings(monkeypatch, TR, PMX_CID, RSA_PEM)
    TR._raw_cache.update(ts=0.0, data=None)
    TR._payload_cache.update(ts=0.0, data=None)
    TR._REFUSAL_LOGGED.clear()

    async def persisted():
        return {"summary": {"settled": 1}}
    monkeypatch.setattr(TR, "_load_persisted", persisted)
    monkeypatch.setattr(TR, "_fetch_raw", lambda: pytest.fail(
        "a cold fetch was spawned for a deterministic refusal"))

    async def go():
        a = await TR.track_record()
        b = await TR.track_record()
        await asyncio.sleep(0)          # any spawned task would run here
        return a, b
    caplog.set_level("WARNING")
    a, b = asyncio.run(go())
    for r in (a, b):
        assert r["live_refresh_refused"] == VK.R_NOT_ED25519
        assert r["served_by"] == "cold_boot_persisted"
    assert made == []
    lines = [m for m in caplog.messages if "live refresh refused" in m]
    assert len(lines) == 1
    no_leak(a)


def test_the_api_census_never_raises_out_of_the_readout(monkeypatch):
    from sportsassets.redteam import readiness as R

    def boom(**_k):
        raise RuntimeError("injected")
    monkeypatch.setattr(PCC, "census", boom)
    out, timing = R.api_pmus_census()
    assert out == {"error": "RuntimeError"}
    assert timing["ok"] is False
    assert R.SECTION_CONTROLS["pmus_census_api"] == ("CREDENTIAL_CLASSES",)


def test_an_absent_funded_slot_is_reported_beside_a_present_execmirror_slot():
    only_exec = C.credential_classes({"PMUS_EXECMIRROR_KEY_ID": RETAIL_KID,
                                      "PMUS_EXECMIRROR_SECRET_KEY":
                                          RETAIL_SEC})
    g = C.credentials({"api": only_exec})
    # not a mismatch (absence blocks only its own path) -- but visible
    assert g["evidence"]["verdicts"]["PMUS"] == "MATCHES"
    assert g["evidence"]["pmus_slots_not_provisioned"] == [
        "api PMUS_KEY_ID/PMUS_SECRET_KEY"]
