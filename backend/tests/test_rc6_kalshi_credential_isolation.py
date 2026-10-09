"""KALSHI CREDENTIALS ARE KALSHI'S OWN (RC6 lane K, category 13).

Production RC5 (pm-acceptance 37836393458, red_team.json
readiness.controls.CREDENTIAL_CLASSES): status RED with ONE blocker,
CREDENTIAL_CLASS_MISMATCH:PMUS:POLYMARKET_EXCHANGE_RSA_M2M; evidence.verdicts
= {PMX: MATCHES, PMUS: MISMATCH_PATH_BLOCKED, KALSHI: MATCHES}; the Kalshi
slot holds KALSHI_ED25519_API_KEY in api and workers (approved: Ed25519 or
RSA). The scorecard's Kalshi unit `credential_class_control` read the
AGGREGATE status, so the PMUS owner action (a funded retail Ed25519 key)
failed the Kalshi category. These tests pin the evidence the proposed
evaluator correction reads -- Kalshi's own verdict, per slot -- and the
isolation it rests on:

  * the control's verdict per venue slot is independent: a PMUS mismatch
    never names or changes the KALSHI verdict, and a Kalshi mismatch never
    changes PMUS / PMX;
  * the Kalshi slot is classified from the Kalshi env names alone, and the
    Kalshi signers read no other venue's env names (nor the reverse), so one
    venue's key can never be picked up as another's;
  * an absent Kalshi key is NOT_PROVISIONED, never MATCHES.

(These document existing behaviour -- they pass on 412c4962 -- and guard the
evidence the evaluator change in this lane's report relies on.)
"""
from __future__ import annotations

import ast
import pathlib
import re

from sportsassets.redteam import controls as C

PKG = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
PMX_OK = "POLYMARKET_EXCHANGE_RSA_M2M"
PMUS_OK = "POLYMARKET_US_ED25519"


def _proc(pmus, kalshi):
    return {"PMX": PMX_OK, "PMUS": pmus, "KALSHI": kalshi,
            "PMUS_SLOTS": {"PMUS_KEY_ID/PMUS_SECRET_KEY": pmus}}


def test_the_production_shape_reds_on_pmus_only_and_kalshi_matches():
    r = C.credentials({"api": _proc(PMX_OK, C.KALSHI_ED25519_API_KEY),
                       "workers": _proc(PMX_OK, C.KALSHI_ED25519_API_KEY)})
    assert r["status"] == "RED"
    assert r["blockers"] == ["CREDENTIAL_CLASS_MISMATCH:PMUS:%s" % PMX_OK]
    v = r["evidence"]["verdicts"]
    assert v == {"PMX": "MATCHES", "PMUS": "MISMATCH_PATH_BLOCKED",
                 "KALSHI": "MATCHES"}
    assert not any("KALSHI" in b for b in r["blockers"])
    assert not any("KALSHI" in a for a in r["evidence"]["owner_actions"])
    assert "KALSHI" not in r["evidence"]["not_provisioned"]


def test_each_venue_verdict_is_its_own():
    # Kalshi wrong, PMUS right: only Kalshi is named
    r = C.credentials({"api": _proc(PMUS_OK, "UNRECOGNISED_SHAPE")})
    assert r["evidence"]["verdicts"]["KALSHI"] == "MISMATCH_PATH_BLOCKED"
    assert r["evidence"]["verdicts"]["PMUS"] == "MATCHES"
    assert r["blockers"] == ["CREDENTIAL_CLASS_MISMATCH:KALSHI:"
                             "UNRECOGNISED_SHAPE"]
    # both documented Kalshi types match; PMUS right -> GREEN
    for k in (C.KALSHI_ED25519_API_KEY, C.KALSHI_RSA_API_KEY):
        r = C.credentials({"api": _proc(PMUS_OK, k)})
        assert r["status"] == "GREEN"
        assert r["evidence"]["verdicts"]["KALSHI"] == "MATCHES"


def test_an_absent_kalshi_key_is_not_provisioned_never_matches():
    r = C.credentials({"api": _proc(PMUS_OK, None)})
    assert r["evidence"]["verdicts"]["KALSHI"] == "NOT_PROVISIONED_PATH_BLOCKED"
    assert "KALSHI" in r["evidence"]["not_provisioned"]
    # and the slot is classified from the Kalshi env names ONLY: another
    # venue's keys in this process never fill it
    env = {"PMX_KEY_ID": "pmx-id", "PMX_PRIVATE_KEY_B64": "eA==",
           "PMUS_KEY_ID": "pmus-id", "PMUS_SECRET_KEY": "eA=="}
    assert C.credential_classes(env)["KALSHI"] is None


_ENV = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")


def _env_names(path: pathlib.Path) -> set:
    """Every whole env-variable NAME a module spells (a bare prefix such as
    pmus_credential_census.NOT_PMUS_PREFIXES' "KALSHI_" -- an exclusion --
    is not a name)."""
    names = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and _ENV.fullmatch(n.value) and n.value.startswith(
                    ("KALSHI_", "PMUS_", "PMX_", "POLY")):
            names.add(n.value)
    return names


def test_the_kalshi_signers_read_no_other_venue_s_credentials():
    for mod in ("kalshi_key.py", "kalshi_ws.py", "kalshi_venue.py",
                "kalshi_account.py", "workers/kalshi_ws_market_data.py"):
        bad = {n for n in _env_names(PKG / mod)
               if n.startswith(("PMUS_", "PMX_", "POLY"))}
        assert not bad, (mod, bad)


def test_the_polymarket_signers_read_no_kalshi_credentials():
    for mod in ("venue_key.py", "pmx.py", "pmx_institutional.py",
                "pmus_credential_census.py", "market_data_identity.py"):
        p = PKG / mod
        if not p.exists():
            continue
        bad = {n for n in _env_names(p) if n.startswith("KALSHI_")}
        assert not bad, (mod, bad)
