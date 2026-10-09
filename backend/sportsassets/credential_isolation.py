"""ONE VENUE'S KEY NEVER REACHES ANOTHER VENUE'S CLIENT (RC6 red-team,
credential isolation).

Three walls, each with its own test (tests/test_red_team_scenarios.py):

  BY NAME   each venue's client reads only its own environment slots
            (a source scan pins it: no Kalshi signer reads a PMX / PMUS
            name, no PMUS signer a Kalshi / PMX one, ...)
  BY SHAPE  a slot holding another venue's key CLASS is refused before any
            signature (controls.credential_classes; venue_key.
            pmus_slot_refusal -- the PMX RSA PEM, or a Kalshi PEM key file,
            in the PMUS slot)
  BY KEY    what shape cannot tell apart -- a Kalshi RSA key and the PMX
            client's RSA key are both RSA PEMs -- is told apart by the key
            pair itself: the public-key fingerprint of every venue key
            configured in one process. One key pair in two VENUES' slots is
            CREDENTIAL_REUSED_ACROSS_VENUES; the Kalshi signers refuse a key
            that is also configured for another venue.

Fingerprints only (sha256 prefix of the DER public key): never a value,
length or prefix of a secret. Never raises: a slot that holds nothing
loadable has no fingerprint (its own gate names why). Pure over `env`.
"""
from __future__ import annotations

import hashlib
import os

VERSION = "CREDENTIAL_ISOLATION_V1"

#: every venue secret slot that holds a private key: (venue, env name)
SECRET_SLOTS = (
    ("KALSHI", "KALSHI_PRIVATE_KEY_PEM"),
    ("PMX", "PMX_PRIVATE_KEY_B64"),
    ("PMUS", "PMUS_SECRET_KEY"),
    ("PMUS", "PMUS_EXECMIRROR_SECRET_KEY"),
    ("PMUS", "EDGE_PMUS_SECRET_KEY"),
    ("PMUS", "PMUS_MD_SECRET_KEY"),
    ("PMUS", "PMUS_BROKER_SECRET_KEY"),
)
R_CROSS_VENUE_KEY_REUSE = "CREDENTIAL_REUSED_ACROSS_VENUES"


def _spki_fingerprint(public_key) -> str:
    from cryptography.hazmat.primitives import serialization
    der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()[:16]


def key_fingerprint(raw) -> str | None:
    """The public-key fingerprint of the private key `raw` holds in any form
    a signer of this repository loads it from: a PEM (as given, escaped
    newlines, one line, base64 of the PEM -- kalshi_key's forms, which
    cover pmx_institutional's base64-of-PEM) or an Ed25519 seed (venue_key's
    forms). None when nothing loadable is there. Never raises."""
    if raw is None or not str(raw).strip():
        return None
    try:
        from . import kalshi_key as KK
        key, _form = KK.load(raw)
        return _spki_fingerprint(key.public_key())
    except Exception:                                           # noqa: BLE001
        pass
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from . import venue_key as VK
        got = VK._decode(str(raw))
        if got is None:
            return None
        return _spki_fingerprint(
            ed25519.Ed25519PrivateKey.from_private_bytes(got[0]).public_key())
    except Exception:                                           # noqa: BLE001
        return None


def fingerprints(env=None) -> dict:
    """{env name: fingerprint} for every configured, loadable venue key."""
    env = os.environ if env is None else env
    out = {}
    for _venue, name in SECRET_SLOTS:
        fp = key_fingerprint(env.get(name))
        if fp is not None:
            out[name] = fp
    return out


def reuse(env=None, *, fps: dict | None = None) -> list:
    """Every pair of slots of DIFFERENT venues holding the same key pair, as
    "A=B" (env names, sorted). Slots of one venue sharing a key are not a
    cross-venue reuse (and are reported by the PMUS census)."""
    fps = fingerprints(env) if fps is None else fps
    venue = {name: v for v, name in SECRET_SLOTS}
    names = sorted(fps)
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if fps[a] == fps[b] and venue.get(a) != venue.get(b):
                out.append("%s=%s" % (a, b))
    return out


def other_venue_slots_holding(raw, *, venue: str, env=None) -> list:
    """The slots of venues OTHER than `venue` (names only, sorted) that hold
    the same key pair as `raw` -- the value a `venue` signer is about to
    load, from whatever source (an env slot or a key file). [] when none,
    or when `raw` holds nothing loadable."""
    fp = key_fingerprint(raw)
    if fp is None:
        return []
    return sorted(name for name, f in fingerprints(env).items()
                  if f == fp and dict((n, v) for v, n in SECRET_SLOTS)
                  .get(name) != venue)


def reused_by(slot: str, env=None) -> list:
    """The OTHER venues' slots holding the same key pair as `slot` (names
    only); [] when none or when `slot` holds nothing loadable."""
    out = []
    for pair in reuse(env):
        a, b = pair.split("=")
        if slot == a:
            out.append(b)
        elif slot == b:
            out.append(a)
    return sorted(out)
