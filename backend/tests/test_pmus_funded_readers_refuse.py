"""RC5: EVERY FUNDED (RETAIL) PMUS READER REFUSES BY NAME BEFORE ANY
REQUEST WHEN THE FUNDED SLOT HOLDS THE WRONG CREDENTIAL CLASS -- AND NONE
SUBSTITUTES ANOTHER ACCOUNT'S POSITIONS.

Production (pm-acceptance run 37738089957; PMUS credential census on
sportsassets-api AND sportsassets-workers): PMUS_KEY_ID / PMUS_SECRET_KEY
hold the PMX INSTITUTIONAL RSA client (PMX_CLIENT_ID == PMUS_KEY_ID, an RSA
PEM), not the funded retail account's Ed25519 key. The census named the
class; this file proves the readers. Each reader runs against the REAL SDK
client built by pmus._get_client() with:
  * an httpx send that RECORDS every request (none may be authenticated),
  * a spy on the SDK signer (it must never be reached),
and asserts the reader's outcome is a named refusal / unreadable, never a
reading. A source scan proves every funded client is built at one of the
census's three gates. Throwaway keys only; no network; no value in any
output.
"""
from __future__ import annotations

import ast
import asyncio
import base64
import json
import logging
import pathlib
import types

import pytest
from cryptography.hazmat.primitives import serialization as S
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from sportsassets import config as CONFIG
from sportsassets import pmus
from sportsassets import pmus_credential_census as PCC
from sportsassets import venue_key as VK

ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
R = VK.R_NOT_ED25519

RSA_PEM = rsa.generate_private_key(public_exponent=65537, key_size=2048
                                   ).private_bytes(
    S.Encoding.PEM, S.PrivateFormat.PKCS8, S.NoEncryption()).decode()
_ED = ed25519.Ed25519PrivateKey.generate()
_SEED = _ED.private_bytes(S.Encoding.Raw, S.PrivateFormat.Raw,
                          S.NoEncryption())
_PUB = _ED.public_key().public_bytes(S.Encoding.Raw, S.PublicFormat.Raw)
RETAIL_SEC = base64.b64encode(_SEED + _PUB).decode()         # SDK form
PMX_CID = "pmxclientidnotreal0123456789abcd"
FUNDED_KID = "99999999-8888-7777-6666-555555555555"
SLUG = "aec-nfl-aaa-bbb-2026-10-11"


def _settings(monkeypatch, kid, sec):
    cfg = types.SimpleNamespace(pmus_key_id=kid, pmus_secret_key=sec)
    monkeypatch.setattr(CONFIG, "settings", lambda: cfg)
    monkeypatch.setattr(pmus, "settings", lambda: cfg)
    monkeypatch.setattr(pmus, "_client", None)
    monkeypatch.setattr(pmus, "_read_client", None)
    return cfg


@pytest.fixture()
def wire(monkeypatch):
    """Every request the SDK would send: (method, url, signed?). Public reads
    answer 200 {}; nothing ever leaves the process."""
    import httpx
    sent: list = []

    def send(self, request, *a, **k):
        sent.append((request.method, str(request.url),
                     "X-PM-Signature" in request.headers))
        return httpx.Response(200, json={"positions": {}, "eof": True},
                              request=request)
    monkeypatch.setattr(httpx.Client, "send", send)
    return sent


@pytest.fixture()
def signer(monkeypatch):
    import polymarket_us.client as sdk_client
    calls: list = []
    real = sdk_client.create_auth_headers

    def spy(*a, **k):
        calls.append(a[2:])                     # method, path; no key
        return real(*a, **k)
    monkeypatch.setattr(sdk_client, "create_auth_headers", spy)
    return calls


@pytest.fixture()
def production_slot(monkeypatch, wire, signer):
    """sportsassets-api / -workers as production holds them: the funded slot
    holds the PMX client id and its RSA PEM."""
    _settings(monkeypatch, PMX_CID, RSA_PEM)
    yield
    # NOTHING AUTHENTICATED WAS EVER SENT OR SIGNED
    assert [s for s in wire if s[2]] == []
    assert not any("api.polymarket.us" in s[1] for s in wire)
    assert signer == []


def no_leak(obj):
    blob = json.dumps(obj, default=str)
    for v in (RSA_PEM, PMX_CID, RETAIL_SEC, FUNDED_KID):
        assert v not in blob
    assert "".join(RSA_PEM.splitlines()[1:2])[:24] not in blob


# ── the shared funded client: the gate in front of the SDK signer ──────

def test_the_shared_client_refuses_every_authenticated_request_by_name(
        production_slot, wire):
    c = pmus._get_client()
    assert pmus.CREDENTIAL_GATE["installed"] is True
    assert pmus.CREDENTIAL_GATE["refusal"] == R
    for call in (lambda: c.portfolio.positions({"limit": 100}),
                 lambda: c.portfolio.activities({"limit": 1}),
                 lambda: c.account.balances(),
                 lambda: c.orders.list(None),
                 # an order write is refused the same way, before signing:
                 # nothing is added, a certain failure is only named
                 lambda: c.orders.create({"marketSlug": SLUG})):
        with pytest.raises(VK.SecretNotEd25519) as e:
            call()
        assert str(e.value) == R
    # public market data still flows (no key, no signature)
    c.markets.bbo(SLUG)
    assert wire and all(not signed for _m, _u, signed in wire)
    no_leak(pmus.CREDENTIAL_GATE)


def test_the_read_client_refuses_the_same_way(production_slot):
    """_get_read_client re-encodes an Ed25519 key; an RSA PEM is not one, so
    it is the shared client -- gated."""
    c = pmus._get_read_client()
    assert c is pmus._get_client()
    with pytest.raises(VK.SecretNotEd25519):
        c.portfolio.positions({"limit": 100})


def test_a_real_retail_key_is_never_gated(monkeypatch, wire, signer):
    """A key the SDK can sign with passes unchanged: signed and sent."""
    _settings(monkeypatch, FUNDED_KID, RETAIL_SEC)
    c = pmus._get_client()
    assert pmus.CREDENTIAL_GATE["installed"] is False
    c.portfolio.positions({"limit": 100})
    assert signer and wire[-1][2] is True


def test_an_absent_pair_builds_a_public_client_the_sdk_refuses_unsent(
        monkeypatch, wire, signer):
    from polymarket_us.errors import AuthenticationError
    _settings(monkeypatch, "", "")
    c = pmus._get_client()
    assert pmus.CREDENTIAL_GATE["installed"] is False
    with pytest.raises(AuthenticationError):
        c.portfolio.positions({"limit": 100})
    assert wire == [] and signer == []


# ── every reader of the funded account, one by one ─────────────────────

def test_pmus_account_holds_fails_closed_and_names_the_refusal(
        production_slot, monkeypatch, caplog):
    monkeypatch.setattr(pmus, "_pos_cache", {"ts": 0.0, "slugs": frozenset()})
    monkeypatch.setattr(pmus, "_pos_read_failing", False)
    caplog.set_level(logging.WARNING, logger=pmus.log.name)
    assert pmus.account_holds(SLUG) is True              # HELD: refuse a buy
    assert any("SecretNotEd25519" in m and R in m for m in caplog.messages)


def test_pmus_position_side_and_balances_refuse(production_slot):
    assert pmus.position_side(SLUG) is None              # unreadable
    with pytest.raises(VK.SecretNotEd25519):
        pmus.balances()


def test_mirror_live_position_reads_refuse(production_slot, monkeypatch):
    from sportsassets.workers import mirror_live as ML
    monkeypatch.setattr(ML, "pace", lambda *a, **k: None)
    echo, _pages = ML._position_echo(pmus, SLUG)
    assert echo is None
    with pytest.raises(VK.SecretNotEd25519):
        asyncio.run(ML._pm_held(types.SimpleNamespace(pmus=pmus), SLUG))


def test_the_shadow_and_live_tick_walk_refuses_before_any_page(
        production_slot, monkeypatch):
    from sportsassets.workers import mirror_shadow as MS
    monkeypatch.setattr(MS, "pace", lambda *a, **k: pytest.fail(
        "the walk paced a page"))
    MS._UNUSABLE_LOGGED.clear()
    got = asyncio.run(MS.account_positions_walk(pmus))
    assert got == (None, 0, False)
    assert MS.pmus_secret_unusable_reason(pmus) == R


def test_the_shadow_never_reads_the_institutional_account(
        production_slot, monkeypatch):
    """The fallback is the funded ledger, labelled; the PMX positions read
    (another account) is never built, however the slot is configured."""
    from sportsassets import mirror_positions_source as MPS
    from sportsassets import pmx
    from sportsassets.workers import mirror_shadow as MS
    monkeypatch.setattr(pmx, "_get_client", lambda: pytest.fail(
        "the institutional client was built"))
    monkeypatch.setattr(pmx, "_Portfolio", lambda *a, **k: pytest.fail(
        "an institutional positions read was built"))
    monkeypatch.setattr(MS, "_configured_pmx_client_id", lambda: PMX_CID)

    async def ledger(_pool, _sql):
        return {"aec-x": 3.0}, {"source": MPS.SRC_LEDGER,
                                "authority": MPS.AUTHORITY_LEDGER}
    monkeypatch.setattr(MPS, "ledger_positions", ledger)
    pos, receipt = asyncio.run(MS.tick_positions(object(), pmus))
    assert pos == {"aec-x": 3.0}
    assert receipt["source"] == MPS.SRC_LEDGER
    assert receipt["authority"] == "LEDGER_DERIVED_NOT_VENUE_CONFIRMED"
    assert receipt["primary_refusal"] == R
    assert receipt["pmx_positions_fallback"] == MPS.PMX_FALLBACK_REJECTED
    no_leak(receipt)


def test_venue_reconcile_and_the_api_reconcile_read_refuse(production_slot):
    from sportsassets import venue_reconcile as VR
    from sportsassets.api import reconcile_read as RR
    with pytest.raises(VK.SecretNotEd25519):
        VR.venue_positions()
    with pytest.raises(VK.SecretNotEd25519):
        VR.venue_activities(["ACTIVITY_TYPE_TRADE"], 0.0)
    cap = RR.capability_probe()
    for part in ("balances", "positions", "activities", "orders"):
        assert cap[part] == {"ok": False, "error": "SecretNotEd25519"}
    assert cap["identity_available"] is False


def test_the_funded_lane_and_onboarding_readers_refuse(production_slot):
    from sportsassets import bettor_account_onboarding as BAO
    from sportsassets import bettor_funded_account as BFA
    got = BFA.read_positions_sync(pmus._get_client())
    assert got["ok"] is False and got["pages"] == 0
    assert got["refusal"] == BFA.R_POSITIONS_READ_RAISED
    assert got["error"] == "SecretNotEd25519"
    r = BAO.read_positions(pmus)
    assert r["verdict"] == BAO.UNREADABLE and R in r["error"]
    b = BAO.read_balances(pmus)
    assert b["verdict"] == BAO.UNREADABLE
    no_leak([got, r, b])


def test_the_api_readers_refuse_before_any_client(production_slot,
                                                  monkeypatch):
    import polymarket_us
    from sportsassets.api import pmus_account as PA
    from sportsassets.api import track_record as TR
    monkeypatch.setattr(polymarket_us, "PolymarketUS", lambda **k:
                        pytest.fail("a PMUS client was constructed"))
    for module in (PA, TR):
        monkeypatch.setattr(module, "settings", pmus.settings)
    for fn in (TR._fetch_raw, PA._fetch_sync, PA._fetch_all_positions_sync,
               lambda: PA._fetch_week_activities_sync("2026-10-01")):
        with pytest.raises(VK.SecretNotEd25519) as e:
            fn()
        assert str(e.value) == R
    assert TR._funded_refusal() == R


# ── the census names every reader and the gate that refuses it ────────

def test_every_funded_reader_is_covered_by_a_named_gate():
    covered = [r for g in PCC.FUNDED_READER_GATES for r in g["covers"]]
    assert sorted(covered) == sorted(PCC.FUNDED_READERS)
    assert len(set(covered)) == len(covered)
    assert {g["refusal"] for g in PCC.FUNDED_READER_GATES} == {R}
    assert PCC.R_FUNDED_NOT_ED25519 == R


def test_the_census_reports_reconciliation_without_any_substitute():
    env = {"PMUS_KEY_ID": PMX_CID, "PMUS_SECRET_KEY": RSA_PEM,
           "PMX_CLIENT_ID": PMX_CID,
           "PMUS_EXECMIRROR_KEY_ID": FUNDED_KID,
           "PMUS_EXECMIRROR_SECRET_KEY": RETAIL_SEC}
    c = PCC.census(env, service="sportsassets-api")
    fr = c["funded_reconciliation"]
    assert fr["state"] == PCC.FR_NOT_CONFIRMABLE
    assert R in fr["funded_readers"]
    assert fr["substitute"].startswith("NO_INSTITUTIONAL_OR_OTHER_ACCOUNT")
    # the retail-shaped execution-mirror key is listed, never used for
    # the funded account (a different account by design)
    assert fr["other_retail_pairs_not_used"] == ["PMUS_EXECMIRROR"]
    assert c["pairs"]["PMUS_FUNDED"]["reader_gates"]
    no_leak(c)
    ok = PCC.census({"PMUS_KEY_ID": FUNDED_KID, "PMUS_SECRET_KEY": RETAIL_SEC})
    assert ok["funded_reconciliation"]["state"] == PCC.FR_CONFIRMABLE
    absent = PCC.census({})
    assert absent["funded_reconciliation"]["state"] == PCC.FR_ABSENT


def _calls(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Name) and n.func.id == name) or
        (isinstance(n.func, ast.Attribute) and n.func.attr == name))]


def test_every_funded_client_is_built_at_a_gate():
    """SOURCE SCAN. A PolymarketUS client built with the FUNDED secret is
    built either in pmus (whose builders install the credential gate) or
    behind venue_key.signing_secret; nothing else builds one, so no reader
    can reach the funded account around the three gates."""
    funded_builders = []
    for p in ROOT.rglob("*.py"):
        rel = str(p.relative_to(ROOT))
        src = p.read_text()
        tree = ast.parse(src)
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for call in _calls(fn, "PolymarketUS"):
                kws = {k.arg: ast.unparse(k.value) for k in call.keywords}
                sec = kws.get("secret_key", "")
                if not sec:
                    continue                      # public client
                body = ast.unparse(fn)
                if rel == "pmus.py":
                    funded_builders.append((rel, fn.name))
                    assert "_install_credential_gate(" in body, fn.name
                elif "pmus_secret_key" in sec:
                    funded_builders.append((rel, fn.name))
                    assert sec.startswith("signing_secret("), (rel, fn.name)
                else:
                    # the execution-mirror account's own key, never the
                    # funded slot
                    assert "pmus_secret_key" not in body, (rel, fn.name)
                    assert "PMUS_SECRET_KEY" not in body.replace(
                        "PMUS_EXECMIRROR_SECRET_KEY", ""), (rel, fn.name)
    assert sorted(funded_builders) == sorted([
        ("api/pmus_account.py", "_fetch_all_positions_sync"),
        ("api/pmus_account.py", "_fetch_sync"),
        ("api/pmus_account.py", "_fetch_week_activities_sync"),
        ("api/track_record.py", "_fetch_raw"),
        ("pmus.py", "_get_client"), ("pmus.py", "_get_read_client")])


# ── completion: venue-confirmed only on a reading; blockers only on proof ─

def test_a_failed_venue_walk_is_never_venue_confirmed():
    """Before: a walk attempted with a usable key that FAILED carried
    source = the venue walk and read VENUE_CONFIRMED."""
    from sportsassets.completion import read as CR
    hb = {"detail": {"positions_source": {
        "source": CR.VENUE_CONFIRMED_SOURCE, "as_of_epoch": 99.0,
        "unreadable": "venue_walk_failed"}}, "beat_at": 100.0}
    v = CR.venue_positions_block(hb, now=110.0)
    assert v["venue_confirmed"] is False and v["status"] == "NOT_CONFIRMED"
    assert v["why"] == CR.WHY_VENUE_WALK_FAILED
    # a usable key whose walk failed is not a credential finding
    assert "owner_action" not in v and "credential_proof" not in v
    ok = CR.venue_positions_block({"detail": {"positions_source": {
        "source": CR.VENUE_CONFIRMED_SOURCE, "as_of_epoch": 99.0}},
        "beat_at": 100.0}, now=110.0)
    assert ok["status"] == "VENUE_CONFIRMED"


def test_no_other_account_or_source_is_ever_venue_confirmation():
    from sportsassets.completion import read as CR
    for src in ("PMX_INSTITUTIONAL_POSITIONS", "FUNDED_ACCOUNT_LEDGER_DERIVED",
                "PMUS_EXECMIRROR_ACCOUNT"):
        v = CR.venue_positions_block({"detail": {"positions_source": {
            "source": src}}, "beat_at": 100.0}, now=100.0)
        assert v["venue_confirmed"] is False
        assert v["status"] == "NOT_CONFIRMED"
        # no refusal beside it: nothing proves a credential is missing
        assert v["why"] == CR.WHY_NO_CREDENTIAL_PROOF


def test_the_owner_blocker_is_declared_only_beside_its_proof():
    from sportsassets.completion import read as CR
    hb = {"detail": {"positions_source": {
        "source": "FUNDED_ACCOUNT_LEDGER_DERIVED",
        "primary_refusal": CR.R_PMUS_NOT_ED25519,
        "pmus_slot_shape": "RSA_PEM_PRIVATE_KEY_SHAPE_NOT_A_PMUS_RETAIL_KEY",
        "pmus_slot_is_pmx_rsa_client": True}}, "beat_at": 100.0}
    api = "RSA_PEM_PRIVATE_KEY_SHAPE_NOT_A_PMUS_RETAIL_KEY"
    v = CR.venue_positions_block(hb, now=100.0, api_slot_shape=api)
    assert v["status"] == "OWNER_CREDENTIAL_REQUIRED"
    proof = v["credential_proof"]
    assert any(p.startswith("sportsassets-workers: mirror_shadow "
                            "precondition %s" % CR.R_PMUS_NOT_ED25519)
               for p in proof)
    assert any(p.startswith("sportsassets-api: funded slot shape RSA_PEM")
               for p in proof)
    assert v["api_funded_readers"] == {"slot_shape": api,
                                       "state": CR.API_READERS_REFUSED}
    rt = CR.runtime_block(None, None, None, now=100.0)
    ob = {o["item"]: o for o in CR.owner_blockers(rt, v, None)}
    blk = ob["PMUS_RETAIL_POSITION_CONFIRMATION"]
    assert blk["proven_by"] == proof
    # a status without its proof never becomes an owner blocker
    bare = dict(v, credential_proof=[])
    assert "PMUS_RETAIL_POSITION_CONFIRMATION" not in {
        o["item"] for o in CR.owner_blockers(rt, bare, None)}
    no_leak([v, blk])


def test_a_fixed_workers_slot_beside_a_wrong_api_slot_still_names_the_api():
    """The shadow is venue-confirmed (the workers' key is retail), the API's
    slot still holds the RSA client: the API's funded readers refuse, and
    the blocker names only the API, proven by its own shape."""
    from sportsassets.completion import read as CR
    api = "RSA_PEM_PRIVATE_KEY_SHAPE_NOT_A_PMUS_RETAIL_KEY"
    v = CR.venue_positions_block({"detail": {"positions_source": {
        "source": CR.VENUE_CONFIRMED_SOURCE}}, "beat_at": 100.0},
        now=100.0, api_slot_shape=api)
    assert v["status"] == "VENUE_CONFIRMED"
    assert v["api_funded_readers"]["state"] == CR.API_READERS_REFUSED
    rt = CR.runtime_block(None, None, None, now=100.0)
    ob = {o["item"]: o for o in CR.owner_blockers(rt, v, None)}
    assert "PMUS_RETAIL_POSITION_CONFIRMATION" not in ob
    blk = ob["PMUS_API_FUNDED_READERS"]
    assert blk["proven_by"] == ["sportsassets-api: funded slot shape %s "
                                "(this process)" % api]
    assert "sportsassets-api" in blk["action"]
    assert "sportsassets-workers" not in blk["action"]
    fixed = CR.venue_positions_block({"detail": {"positions_source": {
        "source": CR.VENUE_CONFIRMED_SOURCE}}, "beat_at": 100.0}, now=100.0,
        api_slot_shape="PMUS_RETAIL_ED25519_API_KEY_SHAPE")
    assert "PMUS_API_FUNDED_READERS" not in {
        o["item"] for o in CR.owner_blockers(rt, fixed, None)}
