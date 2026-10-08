"""RC5: EVERY KALSHI SIGNER SUPPORTS BOTH DOCUMENTED KEY TYPES, FROM THE
BYTES AS CONFIGURED -- AND NO ORDER PATH BECOMES REACHABLE.

Kalshi documents two API key types (research/kalshi_canonical_venue/
REP_PRODUCTION_CONTRACT_2026-10-07/docs/getting_started_api_keys.md):
Ed25519 (recommended, the default; signs the pre-sign text itself) and RSA
(RSA-PSS / SHA-256 / MGF1-SHA256 / salt = digest length), and "the PEM header
does not identify the key type". Production's KALSHI_PRIVATE_KEY_PEM is an
Ed25519 PKCS#8 PEM, 119 characters with its trailing newline; the plane's
signed account-limits read answers 200 with it (pm-acceptance run
37738089957), while the REST client called it UNREADABLE ("not an RSA private
key") and CREDENTIAL_CLASSES read RED (MISMATCH:KALSHI:ED25519_PEM).

Throwaway keys generated in-process only; no network; no value is ever in
an output. Kalshi live money is NOT ACTIVATED and stays so: the last section
proves the order signer is still unreachable with a now-loadable key.
"""
from __future__ import annotations

import ast
import asyncio
import base64
import json
import pathlib

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization as S
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

from sportsassets import kalshi_key as KK
from sportsassets import kalshi_venue as KV
from sportsassets import kalshi_ws as KWS
from sportsassets import market_plane_guard as G
from sportsassets.redteam import controls as C

from tests import kalshi_fixtures as F

ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
KID = "0b6c2f9e-1d2a-4c3b-9e8f-7a6b5c4d3e2f"       # synthetic, UUID-shaped
NOW = 1_790_000_000.0
PATH = "/trade-api/v2/portfolio/balance"

_ED = ed25519.Ed25519PrivateKey.generate()
ED_PEM = _ED.private_bytes(S.Encoding.PEM, S.PrivateFormat.PKCS8,
                           S.NoEncryption()).decode()
_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
#: PKCS#8 RSA: the SAME "BEGIN PRIVATE KEY" header as the Ed25519 key
RSA_PKCS8 = _RSA.private_bytes(S.Encoding.PEM, S.PrivateFormat.PKCS8,
                               S.NoEncryption()).decode()
#: PKCS#1 RSA: what Kalshi hands out for an RSA key
RSA_PKCS1 = _RSA.private_bytes(S.Encoding.PEM,
                               S.PrivateFormat.TraditionalOpenSSL,
                               S.NoEncryption()).decode()
EC_PEM = ec.generate_private_key(ec.SECP256R1()).private_bytes(
    S.Encoding.PEM, S.PrivateFormat.PKCS8, S.NoEncryption()).decode()
SECRETS = (ED_PEM, RSA_PKCS8, RSA_PKCS1, EC_PEM, KID)


def no_leak(obj):
    blob = json.dumps(obj, default=str)
    for v in SECRETS:
        assert v not in blob
        body = "".join(v.splitlines()[1:-1])
        assert not body or body[:24] not in blob


def env_with(pem, **extra):
    e = {KV.KEY_ID_ENV: KID, KV.PRIVATE_KEY_PEM_ENV: pem, KV.ENV_ENV: "prod"}
    e.update(extra)
    return e


def _spy_loader(monkeypatch):
    """Record the exact bytes handed to the PEM parser (then parse them)."""
    from cryptography.hazmat.primitives import serialization as real
    seen: list = []
    orig = real.load_pem_private_key

    def spy(data, password=None, *a, **k):
        seen.append(bytes(data))
        return orig(data, password, *a, **k)
    monkeypatch.setattr(real, "load_pem_private_key", spy)
    return seen


# ── the production key: Ed25519, 119 characters with its trailing newline ──

def test_the_production_shape_is_an_ed25519_pem_of_119_chars_with_newline():
    assert len(ED_PEM) == 119 and ED_PEM.endswith("-----END PRIVATE KEY-----\n")
    key, form = KK.load(ED_PEM)
    assert KK.key_type(key) == KK.KEY_ED25519 and form == KK.FORM_AS_GIVEN


@pytest.mark.parametrize("pem,want", [
    (ED_PEM, KK.KEY_ED25519), (RSA_PKCS8, KK.KEY_RSA),
    (RSA_PKCS1, KK.KEY_RSA)])
def test_the_type_is_read_from_the_parsed_key_never_the_header(pem, want):
    """Ed25519 and PKCS#8 RSA share the header "BEGIN PRIVATE KEY"."""
    d = KK.describe(pem)
    assert d["loadable"] and d["type"] == want and d["form"] == KK.FORM_AS_GIVEN
    assert (d.get("bits") == 2048) == (want == KK.KEY_RSA)
    no_leak(d)


def test_any_other_algorithm_is_refused_by_name_in_every_signer():
    with pytest.raises(KK.KeyRefused) as e:
        KK.load(EC_PEM)
    assert e.value.code == KK.R_KEY_TYPE_NOT_DOCUMENTED
    assert isinstance(e.value, ValueError)
    ec_key = S.load_pem_private_key(EC_PEM.encode(), password=None)
    # the WS signer used to fall through to the RSA call (a TypeError)
    for signer in (lambda: KWS.sign(ec_key, "1", "GET", KWS.WS_PATH),
                   lambda: KV.sign(ec_key, "1", "GET", PATH)):
        with pytest.raises(KK.KeyRefused) as e:
            signer()
        assert e.value.code == KK.R_KEY_TYPE_NOT_DOCUMENTED
    st = KV.credential_state(env_with(EC_PEM))
    assert st["state"] == KV.KALSHI_CREDENTIAL_UNREADABLE
    assert st["private_key"]["refusal"] == KK.R_KEY_TYPE_NOT_DOCUMENTED


# ── bytes as configured ────────────────────────────────────────────────

def test_the_rest_client_parses_the_configured_bytes_trailing_newline_kept(
        monkeypatch):
    seen = _spy_loader(monkeypatch)
    st = KV.credential_state(env_with(ED_PEM))
    assert st["state"] == "PRESENT"
    assert seen and seen[0] == ED_PEM.encode()          # every byte, as set
    assert seen[0].endswith(b"\n")
    assert st["private_key_form"] == KK.FORM_AS_GIVEN


def test_a_key_file_is_parsed_byte_for_byte(monkeypatch, tmp_path):
    p = tmp_path / "kalshi.key"
    p.write_bytes(ED_PEM.encode())
    seen = _spy_loader(monkeypatch)
    st = KV.credential_state({KV.KEY_ID_ENV: KID,
                              KV.PRIVATE_KEY_PATH_ENV: str(p),
                              KV.ENV_ENV: "demo"})
    assert st["state"] == "PRESENT" and seen[0] == p.read_bytes()


def test_the_ws_signer_parses_the_configured_bytes_trailing_newline_kept(
        monkeypatch):
    seen = _spy_loader(monkeypatch)
    key = KWS.load_private_key(ED_PEM)
    assert KK.key_type(key) == KK.KEY_ED25519
    assert seen[0] == ED_PEM.encode() and seen[0].endswith(b"\n")


def test_a_paste_is_repaired_only_when_it_does_not_load_as_given():
    escaped = ED_PEM.strip().replace("\n", "\\n")
    b64 = base64.b64encode(ED_PEM.encode()).decode()
    for raw, form in ((escaped, KK.FORM_ESCAPED_NEWLINES),
                      (b64, KK.FORM_BASE64_OF_PEM)):
        key, got = KK.load(raw)
        assert got == form
        # the SAME key: identical deterministic Ed25519 signature
        assert KK.sign(key, b"x") == KK.sign(_ED, b"x")
        assert KV.normalize_pem(raw) != raw
    # a value that loads as given is returned as given, byte for byte
    assert KV.normalize_pem(ED_PEM) == ED_PEM
    assert KV.normalize_pem(RSA_PKCS1) == RSA_PKCS1
    st = KV.credential_state(env_with(escaped))
    assert st["state"] == "PRESENT"
    assert st["private_key_form"] == KK.FORM_ESCAPED_NEWLINES


@pytest.mark.parametrize("raw,code", [
    ("", KK.R_KEY_ABSENT), ("   \n", KK.R_KEY_ABSENT),
    ("not-a-key", KK.R_KEY_NOT_PEM),
    ("-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----\n",
     KK.R_KEY_UNLOADABLE)])
def test_nothing_loadable_is_named(raw, code):
    d = KK.describe(raw)
    assert d["loadable"] is False and d["refusal"] == code


# ── signing: Ed25519 itself, RSA-PSS by digest-length salt ─────────────

def test_the_rest_signer_signs_ed25519_over_ts_method_path():
    sig = KV.sign(_ED, "1790000000000", "get", PATH)
    # independent verification: the raw message, Ed25519 (RFC 8032)
    _ED.public_key().verify(base64.b64decode(sig),
                            b"1790000000000GET" + PATH.encode())
    assert len(base64.b64decode(sig)) == 64
    assert KV.verify(_ED.public_key(), sig, "1790000000000", "GET", PATH)
    assert not KV.verify(_ED.public_key(), sig, "1790000000001", "GET", PATH)
    assert not KV.verify(_RSA.public_key(), sig, "1790000000000", "GET", PATH)


def test_the_rest_signer_keeps_rsa_pss_sha256_with_digest_salt():
    sig = KV.sign(_RSA, "1", "GET", PATH)
    _RSA.public_key().verify(
        base64.b64decode(sig), b"1GET" + PATH.encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256())


def test_the_ws_and_rest_signers_are_one_implementation():
    """Ed25519 is deterministic: both signers give the SAME signature for the
    same pre-sign text; RSA-PSS (randomised) verifies under both."""
    ws = KWS.sign(_ED, "7", "GET", "/trade-api/v2/account/limits")
    rest = KV.sign(_ED, "7", "GET", "/trade-api/v2/account/limits")
    assert ws == rest
    for s in (KWS.sign(_RSA, "7", "GET", "/trade-api/v2/account/limits"),
              KV.sign(_RSA, "7", "GET", "/trade-api/v2/account/limits")):
        assert KV.verify(_RSA.public_key(), s, "7", "GET",
                         "/trade-api/v2/account/limits")


def test_the_rest_client_now_reads_with_the_production_key_class():
    """Before: KALSHI_CREDENTIAL_UNREADABLE ("not an RSA private key")."""
    st = KV.credential_state(env_with(ED_PEM))
    assert st["state"] == "PRESENT" and st["complete"] is True
    assert st["private_key"] == {"type": "ED25519", "loadable": True}
    assert len(st["public_key_fingerprint"]) == 16
    no_leak(st)
    t = F.RecordingTransport(F.BALANCE_1234)
    c = KV.KalshiClient(t, env=env_with(ED_PEM), clock=lambda: NOW)
    assert c.balance().status == 200
    h = t.sent[0]["headers"]
    assert h["KALSHI-ACCESS-TIMESTAMP"] == str(int(NOW * 1000))
    _ED.public_key().verify(
        base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]),
        (h["KALSHI-ACCESS-TIMESTAMP"] + "GET" + PATH).encode())


# ── the red-team control accepts the documented classes, truthfully ────

def test_the_control_accepts_both_documented_kalshi_classes():
    from sportsassets.red_team import credential_guard as RTCRED
    for pem, want in ((ED_PEM, C.KALSHI_ED25519_API_KEY),
                      (RSA_PKCS8, C.KALSHI_RSA_API_KEY),
                      (RSA_PKCS1, C.KALSHI_RSA_API_KEY)):
        cls = C.credential_classes({"KALSHI_API_KEY_ID": KID,
                                    "KALSHI_PRIVATE_KEY_PEM": pem})
        assert cls["KALSHI"] == want
        g = C.credentials({"api": cls, "workers": cls})
        assert g["evidence"]["verdicts"]["KALSHI"] == "MATCHES"
        assert not [b for b in g["blockers"] if ":KALSHI:" in b]
        no_leak(g)
    ev = g["evidence"]
    assert ev["approved"]["KALSHI"] == [C.KALSHI_ED25519_API_KEY,
                                        C.KALSHI_RSA_API_KEY]
    assert "getting_started_api_keys" in ev["approved_basis"]["KALSHI"]
    # the imported package is untouched: it still names one class
    assert ev["expected"] == dict(RTCRED.EXPECTED)
    assert RTCRED.EXPECTED["KALSHI"] == "KALSHI_RSA_API_KEY"
    # PMX / PMUS keep the package's single class
    assert C.APPROVED_CLASSES["PMUS"] == (RTCRED.EXPECTED["PMUS"],)
    assert C.APPROVED_CLASSES["PMX"] == (RTCRED.EXPECTED["PMX"],)


def test_a_key_of_another_type_or_no_key_is_still_a_mismatch():
    ecc = C.credential_classes({"KALSHI_API_KEY_ID": KID,
                                "KALSHI_PRIVATE_KEY_PEM": EC_PEM})
    assert ecc["KALSHI"] == C.KALSHI_KEY_TYPE_NOT_DOCUMENTED
    g = C.credentials({"api": ecc})
    assert g["status"] == C.RED
    assert "CREDENTIAL_CLASS_MISMATCH:KALSHI:%s" % \
        C.KALSHI_KEY_TYPE_NOT_DOCUMENTED in g["blockers"]
    act = " ".join(g["evidence"]["owner_actions"])
    assert "api holds %s" % C.KALSHI_KEY_TYPE_NOT_DOCUMENTED in act
    assert "Ed25519 (recommended) or RSA" in act
    no_leak(g)
    kid_only = C.credential_classes({"KALSHI_API_KEY_ID": KID})
    assert kid_only["KALSHI"] == "UNRECOGNISED_SHAPE"
    assert C.credential_classes({})["KALSHI"] is None


def test_an_old_build_reporting_the_shape_label_is_named_as_deploy_skew():
    g = C.credentials({"api": C.credential_classes(
        {"KALSHI_API_KEY_ID": KID, "KALSHI_PRIVATE_KEY_PEM": ED_PEM}),
        "workers": {"KALSHI": "ED25519_PEM"}})
    assert "CREDENTIAL_CLASS_MISMATCH:KALSHI:ED25519_PEM" in g["blockers"]
    act = " ".join(g["evidence"]["owner_actions"])
    assert "deploy this build there" in act and "RSA only" not in act


# ── no provider read-only key class is claimed ──────────────────────────

def test_no_text_claims_a_read_only_kalshi_key():
    from sportsassets.completion import read as CR
    from sportsassets.workers import kalshi_ws_market_data as R
    rt = CR.runtime_block(None, None, None, now=NOW)
    svc = {o["item"]: o for o in CR.owner_blockers(rt, {}, None)}[
        "DEDICATED_MARKET_PLANE_SERVICE"]["action"]
    for text in (R.OWNER_ACTION, svc, R.CREDENTIAL_SCOPE):
        low = text.lower()
        assert "read-only use" not in low and "read-only key)" not in low
    assert "no read-only key class" in R.OWNER_ACTION
    assert "no read-only key class" in svc
    assert "market_plane_guard" in svc and "not the provider" in svc
    assert R.CREDENTIAL_SCOPE.startswith("ACCOUNT_WIDE_KEY")


def test_the_ws_heartbeat_names_the_key_type_and_the_scope():
    from sportsassets.workers import kalshi_ws_market_data as R
    k = R.key_class({KWS.PRIVATE_KEY_PEM_ENV: ED_PEM})
    assert k == {"type": "ED25519", "form": "AS_GIVEN", "refusal": None,
                 "documented_types": ["ED25519", "RSA"]}
    h = R.health(KWS.WsBooks(), None, now=NOW, limits=None,
                 pace=KWS.pacing(None), key=k)
    assert h["key"]["type"] == "ED25519"
    assert h["credential_scope"] == R.CREDENTIAL_SCOPE
    assert h["authority"] == "MARKET_DATA_READ_ONLY_NO_ORDER_AUTHORITY"
    no_leak(h)


def test_a_present_but_undocumented_key_is_named_never_a_crash_loop(
        monkeypatch):
    from sportsassets.workers import kalshi_ws_market_data as R
    beats: list = []

    async def beat(service, status, detail, **_k):
        beats.append((service, status, detail))

    class Stop(Exception):
        pass

    async def stop(_s):
        raise Stop()
    monkeypatch.setattr(R, "heartbeat", beat)
    monkeypatch.setattr(R.asyncio, "sleep", stop)
    monkeypatch.setattr(R.KWS, "websockets_connect", lambda *a, **k:
                        pytest.fail("a connection was attempted"))
    with pytest.raises(Stop):
        asyncio.run(R.run(env={KWS.KEY_ID_ENV: KID,
                               KWS.PRIVATE_KEY_PEM_ENV: EC_PEM}))
    (svc, status, d), = beats
    assert status == "blocked" and d["state"] == "OWNER_ACTION_REQUIRED"
    assert d["why"] == KK.R_KEY_TYPE_NOT_DOCUMENTED
    assert d["key"]["refusal"] == KK.R_KEY_TYPE_NOT_DOCUMENTED
    no_leak(d)


# ── the order signer gains key handling and stays unreachable ───────────

def test_with_a_loadable_ed25519_key_submit_is_still_refused_unsent():
    """Kalshi live money NOT ACTIVATED: the key now loads (state PRESENT),
    and the order is still refused at the gate before any transport."""
    for e in (env_with(ED_PEM),                                  # switch off
              env_with(ED_PEM, **{KV.ENABLED_ENV: "1"})):        # no control
        F.RaisingTransport.calls = 0
        c = KV.KalshiClient(F.RaisingTransport(), env=e, clock=lambda: NOW)
        assert c.credential_state()["state"] == "PRESENT"
        got = c.submit({"state": "PLANNED", "payload": {"ticker": "K"}},
                       control=None, reconciliation=None)
        assert isinstance(got, KV.Refusal) and got.sent is False
        assert got.code in (KV.KALSHI_SMALLLIVE_DISABLED,
                            KV.KALSHI_CONTROL_DISABLED)
        assert F.RaisingTransport.calls == 0
    assert KV.smalllive_env_enabled({}) is False          # default OFF


def _calls(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Name) and n.func.id == name) or
        (isinstance(n.func, ast.Attribute) and n.func.attr == name))]


def test_no_runner_constructs_the_kalshi_client():
    """The only KalshiClient construction in the backend is the class itself
    (its methods build nothing): no worker, API route, agent or lane can
    reach submit / cancel, whatever key is configured."""
    offenders = []
    for p in ROOT.rglob("*.py"):
        rel = str(p.relative_to(ROOT))
        if rel == "kalshi_venue.py":
            continue
        if _calls(ast.parse(p.read_text()), "KalshiClient"):
            offenders.append(rel)
    assert offenders == []


def test_the_shared_key_module_holds_no_transport_and_no_order_path():
    src = (ROOT / "kalshi_key.py").read_text()
    tree = ast.parse(src)
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0])
            if n.level:
                mods |= {a.name for a in n.names}
    assert mods <= {"__future__", "base64", "binascii", "re", "cryptography"}
    for word in ("/portfolio", "orders", "submit", "cancel", "requests",
                 "httpx", "websockets", "KalshiClient"):
        assert word not in src.replace("cannot send anything", ""), word
    # the plane may import it; the order modules stay blocked there
    assert "sportsassets.kalshi_key" not in G.ORDER_MODULES
    assert {"sportsassets.kalshi_venue", "sportsassets.kalshi_orders"} <= \
        G.ORDER_MODULES


def test_the_ws_runtime_closure_gains_the_key_module_not_the_order_client():
    from tests.test_kalshi_ws_market_data import _closure
    seen = _closure(["kalshi_ws", "workers.kalshi_ws_market_data"])
    leaves = {s.split(".")[-1] for s in seen}
    assert "kalshi_key" in leaves
    assert not leaves & {"kalshi_venue", "kalshi_orders", "kalshi_account",
                         "live_executor", "pmus", "execution_gate"}
