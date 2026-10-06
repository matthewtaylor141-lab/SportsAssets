"""mirror_shadow "The seed must be exactly 32 bytes long" (render-ops
37477349299): the configured Ed25519 secret is read in the form it was
given and re-encoded as the SAME key the SDK can decode.

Every case signs through the SDK's own `create_auth_headers` and is verified
against the key's public half, so "normalised" means "the same key signs",
not merely "no exception"."""
from __future__ import annotations

import base64

import pytest
from nacl.signing import SigningKey, VerifyKey
from polymarket_us.auth import create_auth_headers

from sportsassets import venue_key as VK

SEED = bytes(range(1, 33))
SK = SigningKey(SEED)
PUB = bytes(SK.verify_key)
PKCS8 = VK.PKCS8_ED25519_PREFIX + SEED


def _signs_as_our_key(secret: str) -> bool:
    h = create_auth_headers("kid", secret, "GET", "/v1/portfolio/positions")
    msg = (h["X-PM-Timestamp"] + "GET" + "/v1/portfolio/positions").encode()
    VerifyKey(PUB).verify(msg, base64.b64decode(h["X-PM-Signature"]))
    return True


FORMS = {
    "hex32": SEED.hex(),
    "HEX32": SEED.hex().upper(),
    "hex64": (SEED + PUB).hex(),
    "pkcs8_b64": base64.b64encode(PKCS8).decode(),
    "pem": ("-----BEGIN PRIVATE KEY-----\n"
            + base64.b64encode(PKCS8).decode()
            + "\n-----END PRIVATE KEY-----\n"),
    "urlsafe_unpadded": base64.urlsafe_b64encode(SEED).decode().rstrip("="),
    "quoted_ws": '  "%s"\n' % base64.b64encode(SEED).decode(),
    "json_escaped_newline": base64.b64encode(SEED + PUB).decode()[:40]
    + "\\n" + base64.b64encode(SEED + PUB).decode()[40:],
    "pub_then_seed": base64.b64encode(PUB + SEED).decode(),
}


@pytest.mark.parametrize("name", sorted(FORMS))
def test_the_sdk_rejects_the_raw_form_and_signs_after_normalisation(name):
    raw = FORMS[name]
    norm = VK.normalize_secret_key(raw)
    assert base64.b64decode(norm) == SEED
    assert _signs_as_our_key(norm)
    assert VK.describe_secret_key(raw)["usable_after_normalisation"] is True


def test_the_production_error_is_reproduced_from_a_hex_seed():
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        create_auth_headers("kid", SEED.hex(), "GET", "/x")


@pytest.mark.parametrize("raw", [
    base64.b64encode(SEED).decode(),
    base64.b64encode(SEED + PUB).decode(),
])
def test_forms_the_sdk_already_reads_sign_identically(raw):
    assert _signs_as_our_key(VK.normalize_secret_key(raw))
    assert VK.describe_secret_key(raw)["sdk_reads_as_given"] is True


@pytest.mark.parametrize("raw", ["", None, "not-a-key", "abcd", "zz" * 32])
def test_an_unreadable_secret_is_returned_unchanged(raw):
    assert VK.normalize_secret_key(raw) == (raw or "")


def test_describe_never_carries_the_value():
    for raw in FORMS.values():
        d = VK.describe_secret_key(raw)
        blob = repr(d)
        assert SEED.hex() not in blob
        assert base64.b64encode(SEED).decode() not in blob
        assert set(d) <= {"present", "format", "sdk_reads_as_given",
                          "usable_after_normalisation"}


def test_read_client_is_separate_and_order_client_is_untouched(monkeypatch):
    from sportsassets import pmus

    class Cfg:
        pmus_key_id = "kid"
        pmus_secret_key = SEED.hex()

    monkeypatch.setattr(pmus, "settings", lambda: Cfg)
    monkeypatch.setattr(pmus, "_client", None)
    monkeypatch.setattr(pmus, "_read_client", None)
    rc = pmus._get_read_client()
    oc = pmus._get_client()
    assert rc is not oc
    assert oc.secret_key == SEED.hex()            # the order client: as configured
    assert base64.b64decode(rc.secret_key) == SEED
    assert pmus._get_read_client() is rc

    # a secret the SDK already reads: one client, the same object
    Cfg.pmus_secret_key = base64.b64encode(SEED).decode()
    monkeypatch.setattr(pmus, "_client", None)
    monkeypatch.setattr(pmus, "_read_client", None)
    assert pmus._get_read_client() is pmus._get_client()


def test_mirror_shadow_walks_positions_on_the_read_client():
    import inspect
    from sportsassets.workers import mirror_shadow as ms
    src = inspect.getsource(ms.account_positions_walk)
    assert '"_get_read_client"' in src
