"""mirror_shadow's positions walk is skipped -- by name, before any venue
call -- when the PMUS secret slot holds no Ed25519 key in any encoding
(production: the PMX RSA key sits there and every walk raised "The seed must
be exactly 32 bytes long"). A real Ed25519 key walks as before."""
from __future__ import annotations

import asyncio
import base64
import types

from sportsassets.workers import mirror_shadow as MS

RSA_PEM = ("-----BEGIN PRIVATE KEY-----\n"
           + base64.b64encode(b"\x30\x82" + b"\x01" * 600).decode()
           + "\n-----END PRIVATE KEY-----\n")
ED25519 = base64.b64encode(bytes(range(32))).decode()


def _pmus(secret, calls, monkeypatch):
    monkeypatch.setattr(MS, "_configured_secret", lambda: secret)

    def client():
        calls.append("client")
        raise RuntimeError("walk reached the venue client")
    return types.SimpleNamespace(_get_client=client, _get_read_client=client)


def test_a_non_ed25519_secret_skips_the_walk_by_name_with_no_venue_call(
        monkeypatch):
    calls: list = []
    pm = _pmus(base64.b64encode(RSA_PEM.encode()).decode(), calls,
               monkeypatch)
    assert MS.pmus_secret_unusable_reason(pm) == MS.R_PMUS_SECRET_NOT_ED25519
    got = asyncio.run(MS.account_positions_walk(pm))
    assert got == (None, 0, False)
    assert calls == []


def test_a_real_ed25519_secret_walks_as_before(monkeypatch):
    calls: list = []
    pm = _pmus(ED25519, calls, monkeypatch)
    assert MS.pmus_secret_unusable_reason(pm) is None
    positions, _pages, _limited = asyncio.run(MS.account_positions_walk(pm))
    assert positions is None          # the fake client raised: a failed walk
    assert calls == ["client"]        # ... but it DID reach the client


def test_an_uninspectable_or_absent_secret_behaves_exactly_as_before():
    def boom():
        raise RuntimeError("no settings")
    assert MS.pmus_secret_unusable_reason(secret_fn=boom) is None
    assert MS.pmus_secret_unusable_reason(secret_fn=lambda: None) is None
