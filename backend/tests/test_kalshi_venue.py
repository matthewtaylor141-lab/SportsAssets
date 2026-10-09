"""THE KALSHI ADAPTER, CREDENTIAL-FREE AND FAIL-CLOSED, PROVED OFFLINE.

No network, no real credential. A throwaway RSA key is generated in-process
for the signing tests; every venue answer is a recorded fixture shaped from
the edge-engine adapter's field usage (tests/kalshi_fixtures.py).

  credentials   presence / shape / fingerprint only; one-line PEM repair
  signing       RSA-PSS-SHA256 over '{ts}{METHOD}{/trade-api/v2/path}',
                salt = digest length, verified with the public key
  fail-closed   with no credential every authenticated call is a typed
                KALSHI_CREDENTIAL_ABSENT refusal and the transport is never
                touched (a transport that raises if called)
  the gate      env switch, durable control, same-key reconciliation, fresh
  account       read-only snapshot, EMPTY / NOT_EMPTY / UNREADABLE
  translation   intent -> V2 body, ticks, minimums, rounding as execmirror
  fees          ceil(0.07 * C * P * (1 - P)) to the cent, per order
  state         transitions, acknowledgement / poll / fill parsers, cancel,
                ambiguous submission reconciled by client_order_id
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import uuid
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.hashes import SHA256

from sportsassets import execmirror as EM
from sportsassets import kalshi_account as KA
from sportsassets import kalshi_orders as KO
from sportsassets import kalshi_venue as KV
from sportsassets.kalshi_mapping import MappingVerdict

from tests import kalshi_fixtures as F

KID = "0b6c2f9e-1d2a-4c3b-9e8f-7a6b5c4d3e2f"     # synthetic, UUID-shaped
NOW = 1_790_000_000.0


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def pem(key):
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


@pytest.fixture()
def env(pem):
    return {KV.KEY_ID_ENV: KID, KV.PRIVATE_KEY_PEM_ENV: pem, KV.ENV_ENV: "prod"}


def _control(fp, **k):
    c = {"enabled": True, "stopped": False, "key_fingerprint": fp,
         "kalshi_env": "prod"}
    c.update(k)
    return c


def _recon(fp, **k):
    r = {"key_fingerprint": fp, "complete": True, "verdict": "EMPTY",
         "baseline_accepted": False, "at": NOW - 60}
    r.update(k)
    return r


# ───────────────────────────── credentials ─────────────────────────────

def test_absent_credentials_report_absent_and_nothing_else():
    st = KV.credential_state({})
    assert st["state"] == KV.KALSHI_CREDENTIAL_ABSENT
    assert st["complete"] is False and st["key_fingerprint"] is None
    assert not any(st["names"].values())


def test_present_credentials_report_shape_and_fingerprints_never_values(env, pem):
    st = KV.credential_state(env)
    assert st["state"] == "PRESENT" and st["complete"] is True
    assert st["key_id_shape"] == "UUID" and st["environment"] == "prod"
    assert st["key_fingerprint"] == hashlib.sha256(KID.encode()).hexdigest()[:12]
    assert st["private_key"] == {"type": "RSA", "bits": 2048, "loadable": True}
    assert len(st["public_key_fingerprint"]) == 16
    text = repr(st)
    assert KID not in text and "BEGIN" not in text
    body = "".join(pem.splitlines()[1:-1])
    assert body[:24] not in text
    assert st["smalllive_enabled_env"] is False      # default OFF


def test_a_one_line_or_escaped_pem_paste_is_repaired(env, pem):
    one_line = " ".join(pem.strip().splitlines())
    escaped = pem.strip().replace("\n", "\\n")
    for v in (one_line, escaped, '"%s"' % escaped):
        st = KV.credential_state(dict(env, **{KV.PRIVATE_KEY_PEM_ENV: v}))
        assert st["state"] == "PRESENT", v[:40]


def test_a_key_path_is_accepted(env, pem, tmp_path):
    p = tmp_path / "k.pem"
    p.write_text(pem)
    e = {KV.KEY_ID_ENV: KID, KV.PRIVATE_KEY_PATH_ENV: str(p), KV.ENV_ENV: "demo"}
    st = KV.credential_state(e)
    assert st["state"] == "PRESENT" and st["environment"] == "demo"


def test_an_unreadable_key_or_missing_env_is_typed(env):
    bad = dict(env, **{KV.PRIVATE_KEY_PEM_ENV: "-----BEGIN PRIVATE KEY-----\nnope\n"
                                                 "-----END PRIVATE KEY-----"})
    st = KV.credential_state(bad)
    assert st["state"] == KV.KALSHI_CREDENTIAL_UNREADABLE
    assert st["private_key"]["loadable"] is False
    noenv = {k: v for k, v in env.items() if k != KV.ENV_ENV}
    assert KV.credential_state(noenv)["state"] == KV.KALSHI_ENV_UNSET
    assert KV.credential_state(dict(env, **{KV.ENV_ENV: "staging"}))["state"] \
        == KV.KALSHI_ENV_UNSET


def test_a_non_rsa_key_is_unreadable(env):
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    st = KV.credential_state(dict(env, **{KV.PRIVATE_KEY_PEM_ENV: k}))
    assert st["state"] == KV.KALSHI_CREDENTIAL_UNREADABLE


# ───────────────────────────── signing ─────────────────────────────

def test_the_signature_is_rsa_pss_sha256_over_ts_method_path(key):
    path = "/trade-api/v2/portfolio/balance"
    sig = KV.sign(key, "1790000000000", "get", path)
    pub = key.public_key()
    assert KV.verify(pub, sig, "1790000000000", "GET", path)
    # independent verification: message bytes, PSS, MGF1-SHA256, salt 32
    pub.verify(base64.b64decode(sig), b"1790000000000GET" + path.encode(),
               padding.PSS(mgf=padding.MGF1(SHA256()), salt_length=32), SHA256())
    for ts, m, p in (("1790000000001", "GET", path), ("1790000000000", "POST", path),
                     ("1790000000000", "GET", "/trade-api/v2/portfolio/fills")):
        assert not KV.verify(pub, sig, ts, m, p)


def test_the_salt_is_the_digest_length_not_the_maximum(key):
    from cryptography.exceptions import InvalidSignature
    path = "/trade-api/v2/portfolio/orders"
    sig = base64.b64decode(KV.sign(key, "1", "GET", path))
    with pytest.raises(InvalidSignature):
        key.public_key().verify(sig, b"1GET" + path.encode(),
                                padding.PSS(mgf=padding.MGF1(SHA256()),
                                            salt_length=222),   # the 2048-bit maximum
                                SHA256())


def test_the_signed_path_is_the_full_api_path_without_a_query(key):
    with pytest.raises(ValueError):
        KV.sign(key, "1", "GET", "/portfolio/balance")
    with pytest.raises(ValueError):
        KV.sign(key, "1", "GET", "/trade-api/v2/portfolio/fills?limit=5")


def test_the_client_signs_each_request_with_the_clock(env, key):
    t = F.RecordingTransport(F.BALANCE_1234, F.POSITIONS_EMPTY)
    c = KV.KalshiClient(t, env=env, clock=lambda: NOW)
    assert c.balance().status == 200
    c.positions(cursor="abc")
    s0, s1 = t.sent
    assert s0["url"] == "https://api.elections.kalshi.com/trade-api/v2/portfolio/balance"
    h = s0["headers"]
    assert set(h) == {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-TIMESTAMP",
                      "KALSHI-ACCESS-SIGNATURE"}
    assert h["KALSHI-ACCESS-KEY"] == KID
    assert h["KALSHI-ACCESS-TIMESTAMP"] == str(int(NOW * 1000))
    assert KV.verify(key.public_key(), h["KALSHI-ACCESS-SIGNATURE"],
                     h["KALSHI-ACCESS-TIMESTAMP"], "GET",
                     "/trade-api/v2/portfolio/balance")
    # params travel separately and are NOT in the signed path
    assert s1["params"] == {"limit": 200, "cursor": "abc"}
    assert KV.verify(key.public_key(), s1["headers"]["KALSHI-ACCESS-SIGNATURE"],
                     s1["headers"]["KALSHI-ACCESS-TIMESTAMP"], "GET",
                     "/trade-api/v2/portfolio/positions")


def test_the_demo_environment_uses_the_demo_base(env):
    t = F.RecordingTransport(F.BALANCE_1234)
    KV.KalshiClient(t, env=dict(env, **{KV.ENV_ENV: "demo"})).balance()
    assert t.sent[0]["url"].startswith(KV.BASE_URLS["demo"])


# ───────────────────────────── fail-closed ─────────────────────────────

AUTH_CALLS = [
    ("balance", ()), ("positions", ()), ("orders", ()), ("fills", ()),
    ("settlements", ()), ("order", ("ord-1",)), ("cancel", ("ord-1",)),
]


@pytest.mark.parametrize("name,args", AUTH_CALLS)
def test_without_credentials_every_authenticated_call_is_refused_unsent(name, args):
    F.RaisingTransport.calls = 0
    c = KV.KalshiClient(F.RaisingTransport(), env={KV.ENV_ENV: "prod"})
    got = getattr(c, name)(*args)
    assert isinstance(got, KV.Refusal)
    assert got.code == KV.KALSHI_CREDENTIAL_ABSENT and got.sent is False
    assert F.RaisingTransport.calls == 0


def test_without_credentials_submit_is_refused_even_with_everything_else_on():
    F.RaisingTransport.calls = 0
    e = {KV.ENV_ENV: "prod", KV.ENABLED_ENV: "1"}
    c = KV.KalshiClient(F.RaisingTransport(), env=e, clock=lambda: NOW)
    got = c.submit({"state": "PLANNED", "payload": {"ticker": F.TICKER_NYY}},
                   control=_control(None), reconciliation=_recon(None))
    assert isinstance(got, KV.Refusal) and got.code == KV.KALSHI_CREDENTIAL_ABSENT
    assert F.RaisingTransport.calls == 0


def test_without_credentials_no_default_transport_is_even_built():
    c = KV.KalshiClient(env={})
    assert isinstance(c.balance(), KV.Refusal)
    assert c._transport is None


def test_without_credentials_the_account_read_is_unreadable_and_unsent():
    F.RaisingTransport.calls = 0
    c = KV.KalshiClient(F.RaisingTransport(), env={})
    got = KA.read_only_reconciliation(c)
    assert got["verdict"] == "UNREADABLE" and got["sent"] is False
    assert got["errors"] == {"credential": KV.KALSHI_CREDENTIAL_ABSENT}
    snap = KA.snapshot(KA.fetch_responses(c), key_fingerprint=None, kalshi_env=None)
    assert snap["verdict"] == "UNREADABLE"
    assert snap["errors"]["balance"] == KV.KALSHI_CREDENTIAL_ABSENT
    assert F.RaisingTransport.calls == 0


def test_a_transport_failure_is_a_response_without_status(env):
    t = F.RecordingTransport(raise_exc=KV.TransportError("ReadTimeout"))
    got = KV.KalshiClient(t, env=env).balance()
    assert isinstance(got, KV.Response) and got.status is None
    assert "ReadTimeout" in got.error


# ───────────────────────────── the submission gate ─────────────────────────────

def _planned():
    return {"state": "PLANNED", "payload": {"ticker": F.TICKER_NYY,
                                            "client_order_id": "c", "side": "bid",
                                            "count": "1.00", "price": "0.5000"}}


def test_the_gate_refuses_in_order_until_every_condition_holds(env):
    cred = KV.credential_state(env)
    fp = cred["key_fingerprint"]
    g = KV.submission_gate
    assert g(KV.credential_state({}), _control(fp), _recon(fp), env_enabled=True,
             now=NOW).code == KV.KALSHI_CREDENTIAL_ABSENT
    assert g(cred, _control(fp), _recon(fp), env_enabled=False,
             now=NOW).code == KV.KALSHI_SMALLLIVE_DISABLED
    assert g(cred, None, _recon(fp), env_enabled=True,
             now=NOW).code == KV.KALSHI_CONTROL_DISABLED
    assert g(cred, _control(fp, enabled=False), _recon(fp), env_enabled=True,
             now=NOW).code == KV.KALSHI_CONTROL_DISABLED
    assert g(cred, _control(fp, stopped=True), _recon(fp), env_enabled=True,
             now=NOW).code == KV.KALSHI_STOPPED
    assert g(cred, _control("other"), _recon(fp), env_enabled=True,
             now=NOW).code == KV.KALSHI_CONTROL_FINGERPRINT_MISMATCH
    assert g(cred, _control(fp, kalshi_env="demo"), _recon(fp), env_enabled=True,
             now=NOW).code == KV.KALSHI_ENV_MISMATCH
    assert g(cred, _control(fp), None, env_enabled=True,
             now=NOW).code == KV.KALSHI_RECONCILIATION_ABSENT
    assert g(cred, _control(fp), _recon("other"), env_enabled=True,
             now=NOW).code == KV.KALSHI_RECONCILIATION_FINGERPRINT_MISMATCH
    assert g(cred, _control(fp), _recon(fp, complete=False, verdict="UNREADABLE"),
             env_enabled=True, now=NOW).code == KV.KALSHI_RECONCILIATION_INCOMPLETE
    assert g(cred, _control(fp), _recon(fp, verdict="NOT_EMPTY"), env_enabled=True,
             now=NOW).code == KV.KALSHI_RECONCILIATION_BASELINE_UNACCEPTED
    assert g(cred, _control(fp), _recon(fp, at=NOW - 3600), env_enabled=True,
             now=NOW).code == KV.KALSHI_RECONCILIATION_STALE
    assert g(cred, _control(fp), _recon(fp), env_enabled=True, now=NOW) is None
    assert g(cred, _control(fp), _recon(fp, verdict="NOT_EMPTY",
                                        baseline_accepted=True),
             env_enabled=True, now=NOW) is None


def test_the_env_switch_defaults_off_and_blocks_submission(env):
    fp = KV.credential_state(env)["key_fingerprint"]
    F.RaisingTransport.calls = 0
    c = KV.KalshiClient(F.RaisingTransport(), env=env, clock=lambda: NOW)
    got = c.submit(_planned(), control=_control(fp), reconciliation=_recon(fp))
    assert got.code == KV.KALSHI_SMALLLIVE_DISABLED and F.RaisingTransport.calls == 0


def test_with_every_condition_met_submit_sends_one_signed_v2_post(env, key):
    e = dict(env, **{KV.ENABLED_ENV: "true"})
    fp = KV.credential_state(e)["key_fingerprint"]
    t = F.RecordingTransport(F.ACK_RESTING)
    c = KV.KalshiClient(t, env=e, clock=lambda: NOW)
    got = c.submit(_planned(), control=_control(fp), reconciliation=_recon(fp))
    assert got.status == 201 and len(t.sent) == 1
    s = t.sent[0]
    assert s["method"] == "POST"
    assert s["url"].endswith("/trade-api/v2/portfolio/events/orders")
    assert s["json"] == _planned()["payload"]
    assert KV.verify(key.public_key(), s["headers"]["KALSHI-ACCESS-SIGNATURE"],
                     s["headers"]["KALSHI-ACCESS-TIMESTAMP"], "POST",
                     "/trade-api/v2/portfolio/events/orders")


def test_an_excluded_plan_is_never_sent(env):
    e = dict(env, **{KV.ENABLED_ENV: "1"})
    fp = KV.credential_state(e)["key_fingerprint"]
    F.RaisingTransport.calls = 0
    c = KV.KalshiClient(F.RaisingTransport(), env=e, clock=lambda: NOW)
    got = c.submit({"state": "EXCLUDED", "payload": None}, control=_control(fp),
                   reconciliation=_recon(fp))
    assert got.code == KV.KALSHI_PLAN_NOT_SUBMITTABLE
    assert F.RaisingTransport.calls == 0


def test_cancel_needs_the_credential_but_not_the_enable_switch(env):
    t = F.RecordingTransport(F.ok({}, 204))
    got = KV.KalshiClient(t, env=env).cancel("ord-9")
    assert got.status == 204
    assert t.sent[0]["method"] == "DELETE"
    assert t.sent[0]["url"].endswith("/trade-api/v2/portfolio/events/orders/ord-9")


# ───────────────────────────── account reconciliation ─────────────────────────────

def test_a_fresh_account_is_empty():
    s = KA.snapshot(F.empty_account(), key_fingerprint="fp1", kalshi_env="prod", at=NOW)
    assert s["verdict"] == "EMPTY" and s["complete"] and s["balance_usd"] == 0
    assert s["read_only"] is True and s["key_fingerprint"] == "fp1"


def test_a_flat_position_row_is_still_empty():
    r = dict(F.empty_account(), positions=[F.POSITIONS_FLAT_ROW])
    assert KA.snapshot(r, key_fingerprint="fp", kalshi_env="prod")["verdict"] == "EMPTY"


def test_a_used_account_is_not_empty_with_dialects_parsed():
    s = KA.snapshot(F.busy_account(), key_fingerprint="fp1", kalshi_env="prod", at=NOW)
    assert s["verdict"] == "NOT_EMPTY" and s["complete"]
    assert s["balance_usd"] == Decimal("12.34")
    pos = {p["ticker"]: p for p in s["positions"]}
    assert pos[F.TICKER_NYY]["position"] == Decimal("526.00")
    assert pos[F.TICKER_NYY]["exposure_usd"] == Decimal("199.880000")
    assert pos[F.TICKER_BOS]["exposure_usd"] == Decimal("1.41")       # cents fallback
    assert s["exposure_usd"] == Decimal("201.29")
    assert len(s["resting_orders"]) == 1 and s["fills_recent"] == 1
    assert s["settlements_recent"] == 1


@pytest.mark.parametrize("part,resp,why", [
    ("positions", F.ok({}, 401), "http_401"),
    ("positions", F.ok({"positions": []}), "SHAPE_MARKET_POSITIONS_ABSENT"),
    ("orders", KV.Response(status=None, error="transport:x"), "transport"),
    ("fills", F.ok({"fills": [], "cursor": "more"}), "PAGE_CAP_REACHED"),
    ("settlements", KV.Refusal(KV.KALSHI_CREDENTIAL_ABSENT), KV.KALSHI_CREDENTIAL_ABSENT),
])
def test_any_failed_or_unrecognised_part_is_unreadable_never_empty(part, resp, why):
    r = F.empty_account()
    r[part] = [resp]
    s = KA.snapshot(r, key_fingerprint="fp", kalshi_env="prod")
    assert s["verdict"] == "UNREADABLE" and s["complete"] is False
    assert s["errors"][part] == why


def test_a_missing_balance_field_or_position_field_is_unreadable():
    r = dict(F.empty_account(), balance=F.ok({"balance_cents": 5}))
    assert KA.snapshot(r, key_fingerprint="fp", kalshi_env="prod")["verdict"] == "UNREADABLE"
    r = dict(F.empty_account(), positions=[F.ok({"market_positions": [
        {"ticker": "X"}], "cursor": ""})])
    s = KA.snapshot(r, key_fingerprint="fp", kalshi_env="prod")
    assert s["verdict"] == "UNREADABLE" and "positions_shape" in s["errors"]


def test_the_read_walks_pages_through_the_client(env):
    t = F.RecordingTransport(
        F.ok({"balance": 500}),
        F.ok({"market_positions": [], "cursor": "p2"}), F.POSITIONS_EMPTY,
        F.ORDERS_EMPTY, F.FILLS_EMPTY, F.SETTLEMENTS_EMPTY)
    got = KA.read_only_reconciliation(KV.KalshiClient(t, env=env), now=NOW)
    assert got["verdict"] == "EMPTY" and got["balance_usd"] == Decimal(5)
    assert [s["method"] for s in t.sent] == ["GET"] * 6
    assert t.sent[2]["params"]["cursor"] == "p2"
    assert t.sent[3]["params"]["status"] == "resting"
    assert got["key_fingerprint"] == KV.fingerprint(KID)


def test_the_reconciliation_record_accepts_a_baseline_only_when_not_empty():
    e = KA.snapshot(F.empty_account(), key_fingerprint="fp", kalshi_env="prod", at=NOW)
    b = KA.snapshot(F.busy_account(), key_fingerprint="fp", kalshi_env="prod", at=NOW)
    assert KA.reconciliation_record(e, baseline_accepted=True)["baseline_accepted"] is False
    rec = KA.reconciliation_record(b, baseline_accepted=True, actor="ops")
    assert rec["baseline_accepted"] is True and rec["verdict"] == "NOT_EMPTY"
    assert rec["positions"][0]["position"] == "526.00"
    gate_ok = KV.submission_gate(
        {"state": "PRESENT", "key_fingerprint": "fp", "environment": "prod"},
        _control("fp"), rec, env_enabled=True, now=NOW + 10)
    assert gate_ok is None


def test_final_state_reconciliation_against_the_account():
    s = KA.snapshot(F.busy_account(), key_fingerprint="fp", kalshi_env="prod")
    ok = KA.reconcile_positions(s, {F.TICKER_NYY: 526, F.TICKER_BOS: 3})
    assert ok["reconciled"] is True
    bad = KA.reconcile_positions(s, {F.TICKER_NYY: 520, F.TICKER_BOS: 3})
    assert bad["reconciled"] is False and F.TICKER_NYY in bad["differences"]
    based = KA.reconcile_positions(s, {F.TICKER_NYY: 520}, baseline={
        F.TICKER_NYY: 6, F.TICKER_BOS: 3})
    assert based["reconciled"] is True
    e = KA.snapshot(F.empty_account(), key_fingerprint="fp", kalshi_env="prod")
    assert KA.reconcile_positions(e, {"KX-SETTLED": 4},
                                  settled={"KX-SETTLED"})["reconciled"] is True
    u = dict(e, verdict="UNREADABLE")
    assert KA.reconcile_positions(u, {})["reason"] == "SNAPSHOT_UNREADABLE"


def test_a_negative_venue_position_is_named():
    r = dict(F.empty_account(), positions=[F.ok({"market_positions": [
        {"ticker": "KX-N", "position_fp": "-4.00",
         "market_exposure_dollars": "1.00"}], "cursor": ""})])
    s = KA.snapshot(r, key_fingerprint="fp", kalshi_env="prod")
    assert s["negative_positions"] == ["KX-N"] and s["verdict"] == "NOT_EMPTY"


# ───────────────────────────── translation ─────────────────────────────

def _target(holding="LONG", ticker=F.TICKER_NYY):
    return MappingVerdict("ESTABLISHED", holding, ticker, "mlb-bos-nyy-2026-10-03")


def _order(**k):
    o = {"mirror_id": "km:paperord:1", "order_id": "paperord:1", "group_id": "g1",
         "role": "ENTRY", "intent": "ORDER_INTENT_BUY_LONG",
         "wire_price": Decimal("0.55"), "qty": Decimal("2000"),
         "order_type": "MARKETABLE", "time_in_force": "IOC",
         "us_market_slug": "mlb-bos-nyy-2026-10-03",
         "expires_at": dt.datetime(2026, 10, 3, 23, 0, tzinfo=dt.timezone.utc)}
    o.update(k)
    return o


def test_a_buy_long_becomes_a_v2_yes_bid():
    p = KO.plan(_order(), _target())
    assert p["state"] == "PLANNED" and p["live_qty"] == 2
    assert p["payload"] == {
        "ticker": F.TICKER_NYY, "client_order_id": KO.client_order_id("km:paperord:1"),
        "side": "bid", "count": "2.00", "price": "0.5500",
        "self_trade_prevention_type": "taker_at_cross",
        "time_in_force": "immediate_or_cancel"}
    assert (p["action"], p["contract_side"], p["holding"]) == ("buy", "yes", "LONG")
    assert p["live_notional"] == Decimal("1.10")
    assert p["fee_estimate"] == KO.fee_for(2, Decimal("0.55"))


def test_the_client_order_id_is_deterministic_per_mirror_id():
    a = KO.client_order_id("km:paperord:1")
    assert a == KO.client_order_id("km:paperord:1")
    assert a != KO.client_order_id("km:paperord:2")
    assert str(uuid.UUID(a)) == a
    with pytest.raises(ValueError):
        KO.client_order_id("")


def test_a_buy_short_is_yes_on_the_established_complement_at_one_minus_wire():
    p = KO.plan(_order(intent="BUY_SHORT"), _target("SHORT", F.TICKER_BOS))
    assert p["state"] == "PLANNED" and p["ticker"] == F.TICKER_BOS
    assert p["payload"]["side"] == "bid" and p["payload"]["price"] == "0.4500"


def test_a_short_needs_a_short_mapping():
    p = KO.plan(_order(intent="BUY_SHORT"), _target("LONG"))
    assert p["exclusion"] == KO.MAPPING_NOT_ESTABLISHED
    nm = MappingVerdict("NOT_ESTABLISHED", "LONG", F.TICKER_NYY, "s",
                        missing=["kalshi.event.start_time"])
    p = KO.plan(_order(), nm)
    assert p["exclusion"] == KO.MAPPING_NOT_ESTABLISHED
    assert p["detail"]["missing"] == ["kalshi.event.start_time"]
    assert KO.plan(_order(), None)["exclusion"] == KO.MAPPING_NOT_ESTABLISHED


@pytest.mark.parametrize("intent,wire,want", [
    ("BUY_LONG", "0.557", "0.55"),      # buy rounds DOWN
    ("BUY_LONG", "0.551", "0.55"),
    ("BUY_SHORT", "0.553", "0.44"),     # leg 0.447 -> down
    ("BUY_LONG", "0.99", "0.99"),
])
def test_buy_prices_round_down_to_the_cent(intent, wire, want):
    holding = "SHORT" if "SHORT" in intent else "LONG"
    p = KO.plan(_order(intent=intent, wire_price=Decimal(wire)), _target(holding))
    assert p["payload"]["price"] == Decimal(want).quantize(Decimal("0.0001")).to_eng_string()
    assert p["price_rounding_delta"] <= 0


def test_sell_prices_round_up_and_off_range_prices_are_excluded():
    p = KO.plan(_order(intent="SELL_LONG", wire_price=Decimal("0.553"), role="EXIT"),
                _target(), live_held=2, paper_open_qty=Decimal("2000"),
                opened_intent="ORDER_INTENT_BUY_LONG")
    assert p["state"] == "PLANNED" and p["payload"]["price"] == "0.5600"
    assert p["payload"]["side"] == "ask" and p["price_rounding_delta"] > 0
    lo = KO.plan(_order(wire_price=Decimal("0.004")), _target())
    assert lo["exclusion"] == KO.PRICE_OUT_OF_RANGE
    short_lo = KO.plan(_order(intent="BUY_SHORT", wire_price=Decimal("0.995")),
                       _target("SHORT"))                 # leg 0.005 -> 0.00
    assert short_lo["exclusion"] == KO.PRICE_OUT_OF_RANGE
    hi = KO.plan(_order(intent="SELL_LONG", wire_price=Decimal("0.995"), role="EXIT"),
                 _target(), live_held=2, paper_open_qty=Decimal("2000"))
    assert hi["exclusion"] == KO.PRICE_OUT_OF_RANGE      # 0.995 up -> 1.00


@pytest.mark.parametrize("qty", [499, 500, 501, 1500, 2500, 2702, 3500, 12_345])
def test_quantity_rounding_is_execmirror_scale_qty(qty):
    exact, live, delta = EM.scale_qty(qty, 1000)
    p = KO.plan(_order(qty=Decimal(qty), wire_price=Decimal("0.10")), _target())
    assert p["raw_scaled_qty"] == exact and p["rounding_delta"] == delta
    if live < 1:
        assert p["exclusion"] == KO.BELOW_VENUE_MINIMUM and p["live_qty"] == 0
    else:
        assert p["live_qty"] == live and p["payload"]["count"] == "%d.00" % live


def test_a_sub_minimum_is_never_rounded_up():
    p = KO.plan(_order(qty=Decimal("499")), _target())
    assert p["exclusion"] == "BELOW_VENUE_MINIMUM" and p["payload"] is None
    assert p["venue_minimum"] == 1
    assert KO.plan(_order(qty=Decimal("500")), _target())["exclusion"] \
        == "BELOW_VENUE_MINIMUM"                         # half to even -> 0


def test_time_in_force_mapping():
    gtd = KO.plan(_order(time_in_force="GTD"), _target())
    assert gtd["payload"]["time_in_force"] == "good_till_canceled"
    assert gtd["payload"]["expiration_time"] == int(
        dt.datetime(2026, 10, 3, 23, 0, tzinfo=dt.timezone.utc).timestamp())
    naive = KO.plan(_order(time_in_force="GTD", expires_at=dt.datetime(2026, 10, 3)),
                    _target())
    assert naive["exclusion"] == KO.UNSUPPORTED_ORDER
    fok = KO.plan(_order(time_in_force="FOK"), _target())
    assert fok["exclusion"] == KO.UNSUPPORTED_ORDER
    assert KO.plan(_order(intent="ORDER_INTENT_MERGE"), _target())["exclusion"] \
        == KO.UNSUPPORTED_ORDER


def test_post_only_is_checked_against_the_book():
    o = _order(order_type="RESTING", time_in_force="GTD", wire_price=Decimal("0.52"))
    assert KO.plan(o, _target())["exclusion"] == KO.POST_ONLY_BOOK_UNKNOWN
    cross = KO.plan(o, _target(), book={"best_ask": Decimal("0.52")})
    assert cross["exclusion"] == KO.POST_ONLY_WOULD_CROSS
    rest = KO.plan(o, _target(), book={"best_ask": Decimal("0.54")})
    assert rest["state"] == "PLANNED" and rest["post_only"] is True
    assert rest["payload"]["time_in_force"] == "good_till_canceled"
    assert "post_only" not in rest["payload"]            # no unverified flag sent


def test_caps_and_cash_include_the_fee():
    big = KO.plan(_order(qty=Decimal("60000")), _target(), max_order_usd=25)
    assert big["exclusion"] == KO.ABOVE_ORDER_CAP and big["live_qty"] == 60
    # 2 x 0.55 = 1.10 notional + 0.04 fee: 1.12 of cash is not enough
    poor = KO.plan(_order(), _target(), buying_power=Decimal("1.12"))
    assert poor["exclusion"] == KO.INSUFFICIENT_CASH
    assert KO.plan(_order(), _target(), buying_power=Decimal("1.14"))["state"] == "PLANNED"


def test_exits_sell_the_same_fraction_of_live_inventory():
    o = _order(intent="SELL_LONG", role="REDUCE", qty=Decimal("1500"))
    p = KO.plan(o, _target(), live_held=3, paper_open_qty=Decimal("3000"),
                opened_intent="ORDER_INTENT_BUY_LONG")
    assert p["state"] == "PLANNED" and p["live_qty"] == 2   # 1.5 -> 2 (half-even)
    assert KO.plan(o, _target(), live_held=0, paper_open_qty=Decimal("3000"))[
        "exclusion"] == EM.NO_LIVE_INVENTORY
    assert KO.plan(o, _target(), live_held=3, paper_open_qty=Decimal("3000"),
                   opened_intent="ORDER_INTENT_BUY_SHORT")["exclusion"] \
        == EM.INTENT_MISMATCH
    assert KO.plan(o, _target(), live_held=3, live_committed=3,
                   paper_open_qty=Decimal("3000"))["exclusion"] \
        == EM.INVENTORY_COMMITTED


# ───────────────────────────── fees ─────────────────────────────

def test_the_taker_fee_is_rounded_up_to_the_cent_per_order():
    assert KO.fee_for(1, "0.50") == Decimal("0.02")          # 1.75c -> 2c
    assert KO.fee_for(10, "0.50") == Decimal("0.18")         # 17.5c -> 18c
    assert KO.fee_for(100, "0.50") == Decimal("1.75")
    assert KO.fee_for(100, "0.50") != 100 * KO.fee_for(1, "0.50")   # size-dependent
    assert KO.fee_for(3, "0.01") == Decimal("0.01")          # 0.2079c -> 1c
    assert KO.fee_for(7, "0.30") == KO.fee_for(7, "0.70")    # symmetric
    assert KO.fee_for(7, "0.30", side="no") == KO.fee_for(7, "0.30", side="yes")


def test_maker_fee_defaults_to_the_taker_formula_and_is_configurable():
    assert KO.MAKER_FEE_CONFIRMED is False
    assert KO.fee_for(10, "0.40", maker=True) == KO.fee_for(10, "0.40")
    assert KO.fee_for(100, "0.50", maker=True,
                      maker_coefficient="0.0175") == Decimal("0.44")   # 43.75c up
    assert KO.fee_for(100, "0.50", maker=True, maker_coefficient=0) == 0


@pytest.mark.parametrize("count,price,side", [
    (0, "0.5", "yes"), (1.5, "0.5", "yes"), (1, "0", "yes"), (1, "1", "yes"),
    (1, "0.5", "maybe")])
def test_fee_inputs_are_validated(count, price, side):
    with pytest.raises(ValueError):
        KO.fee_for(count, price, side=side)


# ───────────────────────────── state machine ─────────────────────────────

def test_legal_and_illegal_transitions():
    assert KO.transition("PLANNED", "SUBMITTING") == "SUBMITTING"
    assert KO.transition("SUBMITTING", "UNKNOWN") == "UNKNOWN"
    assert KO.transition("UNKNOWN", "OPEN") == "OPEN"
    assert KO.transition("OPEN", "OPEN") == "OPEN"
    assert KO.transition("CANCEL_REQUESTED", "FILLED") == "FILLED"
    for a, b in (("PLANNED", "OPEN"), ("FILLED", "CANCELLED"), ("REJECTED", "OPEN"),
                 ("CANCELLED", "CANCELLED"), ("OPEN", "SUBMITTING"),
                 ("UNKNOWN", "SUBMITTING")):
        with pytest.raises(KO.IllegalTransition):
            KO.transition(a, b)


@pytest.mark.parametrize("status,fill,count,prev,want", [
    ("resting", 0, 3, None, "OPEN"), ("resting", 1, 3, None, "PARTIALLY_FILLED"),
    ("resting", 1, 3, "CANCEL_REQUESTED", "CANCEL_REQUESTED"),
    ("executed", 3, 3, None, "FILLED"), ("canceled", 1, 3, None, "CANCELLED"),
    ("canceled", 3, 3, None, "FILLED"), ("cancelled", 0, 3, None, "CANCELLED"),
    ("pending", 0, 3, None, "UNKNOWN"), (None, 0, 3, None, "UNKNOWN"),
])
def test_venue_status_translation(status, fill, count, prev, want):
    assert KO.state_from_venue(status, fill, count, prev) == want


def test_acknowledgements():
    a = KO.parse_ack(F.ACK_RESTING, 3)
    assert (a["outcome"], a["order_id"], a["state"]) == ("ACCEPTED", "ord-1", "OPEN")
    assert a["ack_fill_count"] == 0 and a["fill_field_present"]
    e = KO.parse_ack(F.ACK_EXECUTED, 2)
    assert e["state"] == "FILLED" and e["ack_fill_count"] == Decimal("2.00")
    n = KO.parse_ack(F.ACK_NO_FILL_FIELD, 2)
    assert n["ack_fill_count"] == 0 and n["fill_field_present"] is False
    r = KO.parse_ack(F.ACK_REMAINING_ONLY, 3)
    assert r["ack_fill_count"] == Decimal(2) and r["state"] == "CANCELLED"
    assert KO.parse_ack(F.ACK_WITHOUT_ID, 1)["outcome"] == "AMBIGUOUS"
    for st in (400, 401, 403, 404, 410, 422):
        assert KO.parse_ack(F.ok({}, st), 1)["outcome"] == "REJECTED"
    for st in (409, 429, 500, 502, 503):        # 409: may already exist
        assert KO.parse_ack(F.ok({}, st), 1)["outcome"] == "AMBIGUOUS"
    t = KO.parse_ack(KV.Response(status=None, error="transport:ReadTimeout"), 1)
    assert t["outcome"] == "AMBIGUOUS" and t["state"] == "UNKNOWN"
    f = KO.parse_ack(KV.Refusal(KV.KALSHI_CREDENTIAL_ABSENT), 1)
    assert f["outcome"] == "REFUSED" and f["sent"] is False


def test_order_status_poll():
    row = {"state": "OPEN", "live_qty": 3}
    assert KO.poll_translate(row, {"order_id": "o", "status": "resting",
                                   "fill_count": "1.00"})["state"] == "PARTIALLY_FILLED"
    assert KO.poll_translate(row, {"order_id": "o", "status": "executed",
                                   "fill_count": "3.00"})["state"] == "FILLED"
    p = KO.poll_translate(row, {"order_id": "o", "status": "canceled",
                                "remaining_count": "1.00"})
    assert p["state"] == "CANCELLED" and p["cum"] == Decimal(2)
    odd = KO.poll_translate(row, {"order_id": "o", "status": "weird"})
    assert odd["state"] == "OPEN" and odd["flag"] == "UNRECOGNISED_VENUE_STATUS"
    with pytest.raises(KO.IllegalTransition):
        KO.poll_translate({"state": "FILLED", "live_qty": 3},
                          {"order_id": "o", "status": "resting"})


def test_fills_are_parsed_in_both_dialects_and_deduplicated():
    rows = [F.fill("t1", "ord-1"), F.fill("t1", "ord-1"),
            F.fill("t2", "ord-1", dialect="cents", count="2", price="0.47", fee="0.02"),
            F.fill("t3", "ord-other"), F.fill("", "ord-1"),
            F.fill("t4", "ord-1", fee=None)]
    got = KO.new_fills(rows, known_trade_ids={"t0"}, order_ids={"ord-1"})
    assert [f["trade_id"] for f in got] == ["t1", "t2", "t4"]
    a, b, c = got
    assert (a["count"], a["price"], a["fee_usd"]) == (Decimal("3.00"),
                                                      Decimal("0.4500"),
                                                      Decimal("0.0500"))
    assert (b["count"], b["price"], b["fee_usd"]) == (Decimal(2), Decimal("0.47"),
                                                      Decimal("0.02"))
    assert c["fee_usd"] is None and c["usable"] is True
    no = KA.parse_fill(F.fill("t5", "o", side="no", price="0.3000"))
    assert no["price"] == Decimal("0.3000") and no["side"] == "no"


def test_cancel_request_and_outcomes():
    assert KO.cancel_request("ord-7") == {
        "method": "DELETE", "path": "/trade-api/v2/portfolio/events/orders/ord-7"}
    with pytest.raises(ValueError):
        KO.cancel_request("")
    assert KO.parse_cancel(F.ok({}, 200))["outcome"] == "CANCELLED"
    assert KO.parse_cancel(F.ok({}, 204))["repost_safe"] is True
    gone = KO.parse_cancel(F.ok({}, 404))
    assert gone["outcome"] == "GONE" and gone["repost_safe"] is False
    assert KO.parse_cancel(F.ok({}, 500))["outcome"] == "ERROR"
    assert KO.parse_cancel(KV.Response(status=None))["repost_safe"] is False
    assert KO.parse_cancel(KV.Refusal("X"))["outcome"] == "REFUSED"


# ───────────────────────────── ambiguous submission ─────────────────────────────

def _unknown_row():
    return {"mirror_id": "km:paperord:1", "client_order_id": KO.client_order_id("km:paperord:1"),
            "ticker": F.TICKER_NYY, "action": "buy", "live_qty": 2,
            "submit_started_at": NOW - 100}


def test_an_ambiguous_submit_is_adopted_by_client_order_id():
    row = _unknown_row()
    orders = F.ok({"orders": [
        {"order_id": "other", "client_order_id": "x", "ticker": F.TICKER_NYY,
         "status": "resting"},
        {"order_id": "ord-77", "client_order_id": row["client_order_id"],
         "ticker": F.TICKER_NYY, "status": "executed", "fill_count": "2.00"}],
        "cursor": ""})
    got = KO.reconcile_ambiguous(row, orders, F.FILLS_EMPTY, now=NOW)
    assert got["outcome"] == "ADOPT" and got["order_id"] == "ord-77"
    assert got["state"] == "FILLED" and got["retry_permitted"] is False


def test_an_unattributed_fill_forbids_a_retry():
    row = _unknown_row()
    fills = F.ok({"fills": [F.fill("t9", "ord-mystery",
                                   created="2026-09-21T13:00:00Z")], "cursor": ""})
    row["submit_started_at"] = dt.datetime(2026, 9, 21, 12, 59, tzinfo=dt.timezone.utc)
    got = KO.reconcile_ambiguous(row, F.ORDERS_EMPTY, fills, now=NOW)
    assert got["outcome"] == "UNATTRIBUTED" and got["retry_permitted"] is False
    mapped = KO.reconcile_ambiguous(row, F.ORDERS_EMPTY, fills, now=NOW,
                                    mapped_order_ids={"ord-mystery"})
    assert mapped["outcome"] == "NOT_FOUND"


def test_no_retry_inside_the_grace_window_or_after_a_failed_read():
    row = dict(_unknown_row(), submit_started_at=NOW - 5)
    assert KO.reconcile_ambiguous(row, F.ORDERS_EMPTY, F.FILLS_EMPTY,
                                  now=NOW)["why"] == "GRACE"
    row = _unknown_row()
    r = KO.reconcile_ambiguous(row, F.ok({}, 500), F.FILLS_EMPTY, now=NOW)
    assert r["outcome"] == "WAIT" and r["why"] == "READ_FAILED"
    r = KO.reconcile_ambiguous(row, F.ok({"orders": [], "cursor": "more"}),
                               F.FILLS_EMPTY, now=NOW)
    assert r["why"] == "READ_INCOMPLETE" and r["retry_permitted"] is False


def test_not_found_after_grace_permits_a_retry_with_the_same_client_order_id():
    row = _unknown_row()
    got = KO.reconcile_ambiguous(row, F.ORDERS_EMPTY, F.FILLS_EMPTY, now=NOW)
    assert got["outcome"] == "NOT_FOUND" and got["retry_permitted"] is True
    assert got["retry_client_order_id"] == row["client_order_id"]


def test_a_timed_out_submit_is_ambiguous_end_to_end(env):
    e = dict(env, **{KV.ENABLED_ENV: "1"})
    fp = KV.credential_state(e)["key_fingerprint"]
    plan = KO.plan(_order(), _target())
    t = F.RecordingTransport(raise_exc=KV.TransportError("ReadTimeout"))
    c = KV.KalshiClient(t, env=e, clock=lambda: NOW)
    ack = KO.parse_ack(c.submit(plan, control=_control(fp), reconciliation=_recon(fp)),
                       plan["live_qty"])
    assert ack["outcome"] == "AMBIGUOUS" and len(t.sent) == 1
    assert t.sent[0]["json"]["client_order_id"] == plan["client_order_id"]
    row = dict(_unknown_row(), client_order_id=plan["client_order_id"])
    orders = F.ok({"orders": [{"order_id": "ord-late",
                               "client_order_id": plan["client_order_id"],
                               "ticker": F.TICKER_NYY, "status": "resting",
                               "fill_count": "0.00"}], "cursor": ""})
    got = KO.reconcile_ambiguous(row, orders, F.FILLS_EMPTY, now=NOW)
    assert got["outcome"] == "ADOPT" and got["state"] == "OPEN"
    assert KO.transition("UNKNOWN", got["state"]) == "OPEN"
