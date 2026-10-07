"""mirror_shadow's QUOTE READ needs no key (production 2026-10-06 21:21Z).

Positions were readable from the funded ledger, yet every tick was abandoned:
the bbo/book quote read went through the authenticated retail client whose
secret slot holds the PMX RSA credential, so each read raised before it was
sent, named no venue state, and three such misses abandon the tick (the exit
leg reads `suppressed`). The quote endpoints are public: with an unusable
PMUS secret the read goes through the keyless, GET-only, paced client. A real
Ed25519 key keeps the authenticated client."""
from __future__ import annotations

import base64
import types

from sportsassets import institutional_same_book as SB
from sportsassets.workers import mirror_shadow as MS

RSA_PEM = ("-----BEGIN PRIVATE KEY-----\n"
           + base64.b64encode(b"\x30\x82" + b"\x01" * 600).decode()
           + "\n-----END PRIVATE KEY-----\n")
ED25519 = base64.b64encode(bytes(range(32))).decode()


def _fake_pmus(calls):
    def client():
        calls.append("auth_client")
        return "AUTH"

    def bbo_read(c, slug):
        calls.append(("bbo_read", c if isinstance(c, str) else "KEYLESS",
                      slug))
        return {"bid": 0.44, "ask": 0.46, "state": "MARKET_STATE_OPEN",
                "error": None}
    return types.SimpleNamespace(_get_client=client, bbo_read=bbo_read)


def test_the_production_topology_reads_quotes_keyless(monkeypatch):
    monkeypatch.setattr(MS, "_configured_secret",
                        lambda: base64.b64encode(RSA_PEM.encode()).decode())
    monkeypatch.setattr(MS, "pace", lambda *_a, **_k: None)
    made = []
    monkeypatch.setattr(SB, "_keyless_client",
                        lambda: made.append(1) or object())
    MS._KEYLESS["client"] = None
    calls: list = []
    q = MS._paced_bbo(_fake_pmus(calls), "aec-nfl-tb-dal-2026-10-08")
    assert q["state"] == "MARKET_STATE_OPEN" and q["bid"] == 0.44
    assert "auth_client" not in calls                 # never the signer
    assert calls == [("bbo_read", "KEYLESS", "aec-nfl-tb-dal-2026-10-08")]
    assert MS.quote_client_kind() == MS.QUOTE_CLIENT_KEYLESS
    MS._paced_bbo(_fake_pmus(calls), "x")
    assert made == [1]                                # built once, reused
    MS._KEYLESS["client"] = None


def test_a_real_ed25519_key_keeps_the_authenticated_client(monkeypatch):
    monkeypatch.setattr(MS, "_configured_secret", lambda: ED25519)
    monkeypatch.setattr(MS, "pace", lambda *_a, **_k: None)
    calls: list = []
    MS._paced_bbo(_fake_pmus(calls), "s")
    assert calls[0] == "auth_client"
    assert calls[1] == ("bbo_read", "AUTH", "s")
    assert MS.quote_client_kind() == MS.QUOTE_CLIENT_AUTH


def test_the_keyless_client_is_get_only():
    c = SB._keyless_client()
    assert c.key_id is None and c.secret_key is None
    assert isinstance(c._http._transport, SB.ReadOnlyTransport)
