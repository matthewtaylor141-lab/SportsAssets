"""(rc6.3 kalshi-shadow) THE KALSHI SHADOW PLANNER AND THE READ-ONLY
RECONCILIATION WRITER, on real Postgres (RN1X_TEST_DSN, migrated through
367). Issue #6 gates 2 / 3 / 6: the Kalshi execution path made verifiable in
SHADOW only.

  PLANNED      a linked PAPER decision whose held side is a certified claim
               a certified Kalshi YES market carries is planned with
               kalshi_orders.plan at 1:1000 of the paper quantity, walked
               against the current Kalshi asks at the plan's limit, priced
               all-in with the published fee (kalshi_fees) and recorded only
               while its net EV per contract is positive;
  EXCLUDED     with the named reason: no certified counterpart (which part
               is missing), a NO leg only, ambiguous, below the venue
               minimum, a fee that makes the EV negative (and EV not
               positive at the Kalshi price), a stale decision or book, no
               ask at the limit, too little depth, unknown fee terms;
  idempotent   one row per decision, append-only, a second pass writes
               nothing;
  SHADOW only  the database refuses any state but PLANNED / EXCLUDED and
               any mode but SHADOW; the runner stands down while the Kalshi
               control is enabled, the Kalshi small-live switch is on or
               SMALL LIVE is not SHADOW;
  no mutation  whole passes run with every network-mutating primitive
               (Kalshi submit / cancel / the real transport, the mirror's
               Venue.place, the PMUS order adapters) patched to raise; the
               read-only client refuses a cancel and a fully-gated submit in
               its transport; reconciliation reads are GETs only.
ALL MARKET DATA, ACCOUNTS AND KEYS HERE ARE SYNTHETIC TEST DATA.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import uuid
from decimal import Decimal

import asyncpg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from sportsassets import execmirror as EM
from sportsassets import kalshi_account as KA
from sportsassets import kalshi_market_data as KMD
from sportsassets import kalshi_orders as KO
from sportsassets import kalshi_shadow as KS
from sportsassets import kalshi_venue as KV
from sportsassets import refusal_taxonomy_table as TT
from tests import kalshi_fixtures as F
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
DAY = 86400.0
#: every DB test plans at its own instant, ten days apart, so one test's
#: decisions are never inside another's window (population is bounded above
#: by now + CLOCK_SKEW_S and below by now - LOOKBACK_S); the base is a random
#: past reconciliation window, so a re-run on the same database never meets
#: the previous run's windows (append-only records are never deleted)
BASE = float(1_600_000_000 + random.randrange(0, 100_000) * 900)
NO_CRED_ENV = {"KALSHI_ENV": "demo"}


def _now(k: int) -> float:
    return BASE + k * 10 * DAY


@pytest.fixture(autouse=True)
def _drained():
    """Steady state (LOOKBACK_S) unless a test sets the backfill itself."""
    KS.STATE["drained"] = True
    yield
    KS.STATE["drained"] = True


def _ed25519_pem() -> str:
    k = ed25519.Ed25519PrivateKey.generate()
    return k.private_bytes(serialization.Encoding.PEM,
                           serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()


def cred_env(**extra) -> dict:
    env = {"KALSHI_API_KEY_ID": str(uuid.uuid4()),
           "KALSHI_PRIVATE_KEY_PEM": _ed25519_pem(), "KALSHI_ENV": "demo"}
    env.update(extra)
    return env


# ── seeding (synthetic) ──────────────────────────────────────────────

async def pool():
    return await asyncpg.create_pool(H.DSN, min_size=1, max_size=4)


def names(tag: str) -> dict:
    t = "%s%s" % (tag.upper().replace("_", ""), uuid.uuid4().hex[:6].upper())
    series = "KXT" + t
    return {"slug": "test-kshadow-%s-%s" % (tag, uuid.uuid4().hex[:8]),
            "series": series, "event": series + "-26OCT10",
            "ticker": series + "-26OCT10-HOME",
            "fp": hashlib.sha256(t.encode()).hexdigest()}


async def seed_decision(conn, acct, n, *, now, holding="LONG", qty=2000,
                        wire="0.45", p_blended=0.55, p_pinnacle=None,
                        age=5.0, tif="IOC", order_type="MARKETABLE") -> str:
    did = "paper_dec_kshadow_%s" % uuid.uuid4().hex[:12]
    oid = "paper_ord_kshadow_%s" % uuid.uuid4().hex[:12]
    at = now - age
    intent = "ORDER_INTENT_BUY_" + holding
    limit = Decimal(wire) if holding == "LONG" else Decimal(1) - Decimal(wire)
    await conn.execute(
        "INSERT INTO paper_decisions (decision_id, session_id, account_id, "
        " decided_at, us_market_slug, holding_side, intent, verdict, "
        " internal_model, pinnacle, p_blended, p_pinnacle, "
        " qualification_gaps, policy_version, simulator_version) VALUES "
        " ($1,$2,$3,to_timestamp($4::float8),$5,$6,$7,'ENTER','{}','{}',$8,"
        " $9,'[]','TEST_POLICY','TEST_SIM')",
        did, acct["session_id"], acct["account_id"], at, n["slug"], holding,
        intent, p_blended, p_pinnacle)
    await conn.execute(
        "INSERT INTO paper_orders (order_id, idempotency_key, account_id, "
        " session_id, group_id, role, direction, holding_side, intent, "
        " us_market_slug, order_type, time_in_force, allow_partial, qty, "
        " limit_price, wire_price, state, decision_id, decided_at, "
        " eligible_at, expires_at, simulator_version) VALUES ($1,$2,$3,$4,"
        " $5,'ENTRY','BUY',$6,$7,$8,$9,$10,true,$11,$12,$13,'FILLED',$14,"
        " to_timestamp($15::float8),to_timestamp($15::float8),"
        " to_timestamp($16::float8),'TEST_SIM')",
        oid, "kshadow:" + oid, acct["account_id"], acct["session_id"],
        "paper_group_" + oid, holding, intent, n["slug"], order_type, tif,
        Decimal(qty), limit, Decimal(wire), did, at, at + 90)
    return did


async def seed_pair(conn, n, *, holding="LONG", pmus_cert="CERTIFIED",
                    kalshi_cert="CERTIFIED", kalshi_side="YES",
                    second_ticker=False):
    side = "NO" if holding == "SHORT" else "YES"
    rows = [("POLYMARKET_US", n["slug"], side, pmus_cert)]
    if kalshi_cert is not None:
        rows.append(("KALSHI", n["ticker"], kalshi_side, kalshi_cert))
        if second_ticker:
            rows.append(("KALSHI", n["ticker"] + "2", kalshi_side,
                         kalshi_cert))
    for venue, mkt, sd, cert in rows:
        await conn.execute(
            "INSERT INTO canonical_claim_aliases (alias_key, event_key, "
            " claim_fingerprint, venue, market_id, side, mapping_status, "
            " settlement_status, certificate_status, rules_sha256) VALUES "
            " ($1,$2,$3,$4,$5,$6,'ESTABLISHED','PROVEN',$7,'rules-test')",
            "%s|%s|%s" % (venue, mkt, sd), "TEST:" + n["event"],
            n["fp"] if cert == "CERTIFIED" else None, venue, mkt, sd, cert)


async def seed_book(conn, n, *, now, asks=(("0.44", 10), ("0.45", 5)),
                    bids=(("0.42", 10),), age=1.0, readable=True,
                    ticker=None):
    await conn.execute(
        "INSERT INTO kalshi_books_current (ticker, event_ticker, "
        " series_ticker, yes_bids, yes_asks, book_basis, readable, "
        " observed_at) VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,'TEST_WS',$6,"
        " to_timestamp($7::float8)) ON CONFLICT (ticker) DO UPDATE SET "
        " yes_asks = excluded.yes_asks, yes_bids = excluded.yes_bids, "
        " readable = excluded.readable, observed_at = excluded.observed_at",
        ticker or n["ticker"], n["event"], n["series"],
        json.dumps([[p, q] for p, q in bids]),
        json.dumps([[p, q] for p, q in asks]), readable, now - age)


async def seed_fee_terms(conn, n, *, now, fee_type="quadratic", mult="1"):
    await conn.execute(
        "INSERT INTO kalshi_fee_terms (term_id, kind, series_ticker, "
        " fee_type, fee_multiplier, first_observed_at, source) VALUES "
        " ($1,'SERIES_OBSERVED',$2,$3,$4,to_timestamp($5::float8),"
        " 'TEST_SYNTHETIC') ON CONFLICT (term_id) DO NOTHING",
        "observed:%s:%s:%s" % (n["series"], fee_type, mult), n["series"],
        fee_type, Decimal(mult), now - 3600)


async def full_setup(conn, tag, *, now, **kw):
    acct = await H.new_account(conn, tag, now=now - 60)
    n = names(tag)
    await seed_pair(conn, n, holding=kw.get("holding", "LONG"))
    await seed_book(conn, n, now=now)
    await seed_fee_terms(conn, n, now=now)
    did = await seed_decision(conn, acct, n, now=now, **kw)
    return acct, n, did


async def row_of(conn, did):
    r = await conn.fetchrow("SELECT * FROM kalshi_shadow_intents WHERE "
                            "paper_decision_id = $1", did)
    return None if r is None else dict(r)


def j(v):
    return json.loads(v) if isinstance(v, str) else v


async def live_table_counts(conn) -> dict:
    return {t: await conn.fetchval("SELECT count(*) FROM %s" % t)
            for t in ("kalshi_live_intents", "kalshi_live_fills",
                      "kalshi_live_events")}


# ═════════════════════════════════════════════════════════════════════
# PURE
# ═════════════════════════════════════════════════════════════════════

def test_bounds_are_the_mirror_and_the_gate_own():
    assert KS.MAX_DECISION_AGE_S == EM.MAX_INTENT_AGE_S == 30.0
    assert KS.BOOK_SLA_S == KMD.BOOK_SLA_S
    assert KS.BUYING_POWER_MAX_AGE_S == KS.RECON_EVERY_S == \
        KV.RECONCILIATION_MAX_AGE_S
    assert KS.MODE == "SHADOW"
    assert KS.LOCK_KEY not in (EM.LOCK_KEY,) and KS.LOCK_KEY != \
        KS.ACCOUNT_LOCK_KEY


def test_every_named_exclusion_is_classified():
    codes = {v for k, v in vars(KS).items() if k.startswith("R_KSH_")}
    assert len(codes) == 26
    for c in codes:
        assert c in TT.TABLE or c in TT.NOT_REFUSAL, c
    assert TT.TABLE["KALSHI_SHADOW_NO_CERTIFIED_COUNTERPART"][0] == "SOFTWARE"
    assert TT.TABLE["KALSHI_SHADOW_BELOW_VENUE_MINIMUM"] == (
        "ECONOMIC", "RISK_RAIL", "ORDER")
    assert TT.TABLE["KALSHI_SHADOW_FEE_MAKES_EV_NEGATIVE"] == (
        "ECONOMIC", "EV", "EV")
    # every kalshi_orders exclusion maps to a named code of this lane
    for code in (KO.BELOW_VENUE_MINIMUM, KO.ABOVE_ORDER_CAP,
                 KO.INSUFFICIENT_CASH, KO.UNSUPPORTED_ORDER,
                 KO.PRICE_OUT_OF_RANGE, KO.POST_ONLY_WOULD_CROSS,
                 KO.POST_ONLY_BOOK_UNKNOWN, KO.MAPPING_NOT_ESTABLISHED):
        assert KS.PLAN_CODES[code] in TT.TABLE


def _alias(venue, mkt, side, *, fp="fp1", cert="CERTIFIED",
           mapping="ESTABLISHED", settlement="PROVEN"):
    return {"alias_key": "%s|%s|%s" % (venue, mkt, side), "venue": venue,
            "market_id": mkt, "side": side,
            "claim_fingerprint": fp,
            "certificate_status": cert, "mapping_status": mapping,
            "settlement_status": settlement}


def test_the_counterpart_is_fail_closed_and_names_what_is_missing():
    pm = [_alias("POLYMARKET_US", "s", "YES")]
    kk = [_alias("KALSHI", "T", "YES")]
    ok = KS.counterpart("s", "LONG", pm, kk)
    assert ok["ok"] and ok["ticker"] == "T" and ok["claim_fingerprint"] == "fp1"
    # SHORT reads the NO alias of the slug: absent here
    no = KS.counterpart("s", "SHORT", pm, kk)
    assert no["code"] == KS.R_KSH_NO_CERTIFIED_COUNTERPART
    assert no["why"] == KS.W_PMUS_ALIAS_ABSENT
    # a NULL certificate is NOT certified (never coalesced to CERTIFIED)
    for bad in ({"certificate_status": None}, {"certificate_status":
                                               "NOT_CERTIFIED"},
                {"claim_fingerprint": None}, {"settlement_status":
                                              "NOT_PROVEN"},
                {"mapping_status": "NOT_ESTABLISHED"}):
        out = KS.counterpart("s", "LONG", [dict(pm[0], **bad)], kk)
        assert out["why"] == KS.W_PMUS_ALIAS_NOT_CERTIFIED, bad
        out = KS.counterpart("s", "LONG", pm, [dict(kk[0], **bad)])
        assert out["why"] == KS.W_NO_KALSHI_ALIAS, bad
    nol = KS.counterpart("s", "LONG", pm, [_alias("KALSHI", "T", "NO")])
    assert nol["code"] == KS.R_KSH_COUNTERPART_ONLY_A_NO_LEG
    amb = KS.counterpart("s", "LONG", pm, kk + [_alias("KALSHI", "T2", "YES")])
    assert amb["code"] == KS.R_KSH_COUNTERPART_AMBIGUOUS
    assert amb["tickers"] == ["T", "T2"]


def _decision(**kw):
    d = {"decision_id": "paper_dec_pure", "decided_at": 1000.0 - 5,
         "us_market_slug": "s", "holding_side": "LONG", "p_blended": 0.55,
         "p_pinnacle": None}
    d.update(kw)
    return d


def _order(**kw):
    o = {"order_id": "paper_ord_pure", "intent": "ORDER_INTENT_BUY_LONG",
         "qty": Decimal(2000), "wire_price": Decimal("0.45"),
         "limit_price": Decimal("0.45"), "time_in_force": "IOC",
         "order_type": "MARKETABLE", "expires_at": None, "role": "ENTRY",
         "holding_side": "LONG", "us_market_slug": "s"}
    o.update(kw)
    return o


TERMS = {"priced": True, "multiplier": "1", "effective_at": 1.0,
         "fee_type": "quadratic", "version": "quadratic@x1"}


def _eval(decision=None, order=None, *, book=None, terms=TERMS,
          pm=None, kk=None, control=None, bp=None, now=1000.0):
    book = book if book is not None else {
        "ticker": "T", "readable": True, "observed_at": now - 1,
        "yes_asks": [["0.44", 10], ["0.45", 5]], "yes_bids": [["0.42", 10]]}
    return KS.evaluate(
        decision or _decision(), order or _order(),
        pmus_aliases=pm if pm is not None else [
            _alias("POLYMARKET_US", "s", "YES")],
        kalshi_aliases=kk if kk is not None else [_alias("KALSHI", "T",
                                                         "YES")],
        book=book, fee_terms=terms,
        control=control or {"scale": Decimal(1000),
                            "max_order_usd": Decimal(25)},
        buying_power=bp or {"usd": None, "status": "UNREAD"}, now=now)


def test_a_planned_row_is_the_plan_the_walk_the_fee_and_a_positive_ev():
    r = _eval()
    assert r["state"] == "PLANNED" and r["exclusion"] is None
    assert r["live_qty"] == 2 and r["limit_price"] == Decimal("0.45")
    assert r["exec_vwap"] == Decimal("0.440000")
    # kalshi_fees: trade fee ceil6(0.07*2*0.44*0.56)=0.034496, debit ceil to
    # the cent of 0.88 + 0.034496 = 0.92 -> fee 0.04
    assert r["fee_usd"] == Decimal("0.04") and r["all_in_usd"] == Decimal(
        "0.92")
    assert r["all_in_per_contract"] == Decimal("0.460000")
    assert r["ev_per_contract"] == Decimal("0.090000")
    assert r["plan_payload"] == {
        "ticker": "T", "client_order_id": KO.client_order_id(
            "kshadow:paper_dec_pure"), "side": "bid", "count": "2.00",
        "price": "0.4500", "self_trade_prevention_type": "taker_at_cross",
        "time_in_force": "immediate_or_cancel"}


def test_a_short_buys_the_complement_ticker_at_one_minus_the_wire():
    r = _eval(_decision(holding_side="SHORT"),
              _order(intent="ORDER_INTENT_BUY_SHORT", holding_side="SHORT",
                     wire_price=Decimal("0.55"), limit_price=Decimal("0.45")),
              pm=[_alias("POLYMARKET_US", "s", "NO")])
    assert r["state"] == "PLANNED", r["exclusion"]
    assert r["limit_price"] == Decimal("0.45")
    assert r["plan_payload"]["side"] == "bid"
    assert r["plan_payload"]["price"] == "0.4500"


@pytest.mark.parametrize("kw,code", [
    ({"order": _order(time_in_force="FOK")}, KS.R_KSH_UNSUPPORTED_ORDER),
    ({"order": _order(order_type="RESTING", wire_price=Decimal("0.46"))},
     KS.R_KSH_POST_ONLY_WOULD_CROSS),
    ({"order": _order(qty=Decimal(300))}, KS.R_KSH_BELOW_VENUE_MINIMUM),
    ({"order": _order(qty=Decimal(60000))}, KS.R_KSH_ABOVE_ORDER_CAP),
    ({"bp": {"usd": Decimal("0.50"), "status": "READ"}},
     KS.R_KSH_INSUFFICIENT_CASH),
    ({"decision": _decision(p_blended=None, p_pinnacle=None)},
     KS.R_KSH_NO_FAIR_PROBABILITY),
    ({"decision": _decision(decided_at=1000.0 - 31)},
     KS.R_KSH_DECISION_STALE_AT_PLAN),
    ({"book": {"ticker": "T", "readable": False, "observed_at": 999.0}},
     KS.R_KSH_BOOK_UNAVAILABLE),
    ({"book": {"ticker": "T", "readable": True, "observed_at": 1000.0 - 31,
               "yes_asks": [["0.44", 10]]}}, KS.R_KSH_BOOK_STALE),
    ({"book": {"ticker": "T", "readable": True, "observed_at": 999.0,
               "yes_asks": [["0.46", 10]]}}, KS.R_KSH_NOT_EXECUTABLE_AT_LIMIT),
    ({"book": {"ticker": "T", "readable": True, "observed_at": 999.0,
               "yes_asks": [["0.44", 1], ["0.47", 10]]}},
     KS.R_KSH_INSUFFICIENT_DEPTH_AT_LIMIT),
    ({"terms": None}, KS.R_KSH_FEE_TERMS_UNKNOWN),
    ({"terms": dict(TERMS, priced=False)}, KS.R_KSH_FEE_TERMS_UNKNOWN),
    ({"decision": _decision(p_blended=0.45)}, KS.R_KSH_FEE_MAKES_EV_NEGATIVE),
    ({"decision": _decision(p_blended=0.44)}, KS.R_KSH_EV_NOT_POSITIVE),
])
def test_each_exclusion_is_named(kw, code):
    r = _eval(**kw)
    assert r["state"] == "EXCLUDED" and r["exclusion"] == code, (
        r["exclusion"], r["detail"].get("plan"))
    assert r["live_qty"] == 0 and r["plan_payload"] is None


def test_the_fair_probability_falls_back_to_pinnacle_and_is_rounded_down():
    r = _eval(_decision(p_blended=None, p_pinnacle=0.5512345678))
    assert r["fair_basis"] == "p_pinnacle" and r["fair_p"] == Decimal(
        "0.551234")
    assert r["state"] == "PLANNED"


def test_the_read_only_client_refuses_a_cancel_and_a_fully_gated_submit():
    """Even with EVERY switch on -- credential, env switch, an enabled
    control and a fresh complete reconciliation for the same key -- the
    SHADOW client cannot send: the transport raises before the inner
    transport sees a byte. A GET still passes."""
    env = cred_env(KALSHI_SMALLLIVE_ENABLED="1")
    inner = F.RecordingTransport(F.BALANCE_1234)
    client = KA.read_only_client(env, transport=inner, clock=lambda: 1000.0)
    fp = KV.fingerprint(env["KALSHI_API_KEY_ID"])
    control = {"enabled": True, "stopped": False, "key_fingerprint": fp,
               "kalshi_env": "demo"}
    recon = {"key_fingerprint": fp, "complete": True, "verdict": "EMPTY",
             "at": 999.0}
    plan = {"state": "PLANNED", "payload": {"ticker": "T", "count": "1.00"}}
    assert KV.submission_gate(KV.credential_state(env), control, recon,
                              env_enabled=True, now=1000.0) is None
    with pytest.raises(KA.ReadOnlyViolation):
        client.submit(plan, control=control, reconciliation=recon)
    with pytest.raises(KA.ReadOnlyViolation):
        client.cancel("ord-1")
    assert inner.sent == []
    got = client.balance()
    assert got.status == 200 and [s["method"] for s in inner.sent] == ["GET"]
    with pytest.raises(KA.ReadOnlyViolation):
        KA.GetOnlyTransport(inner).send("GET", "u", headers={}, params=None,
                                        json_body={"x": 1}, timeout=1)


# ═════════════════════════════════════════════════════════════════════
# REAL POSTGRES
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_planned_end_to_end_at_one_to_one_thousand():
    now = _now(1)
    p = await pool()
    try:
        async with p.acquire() as conn:
            before = await live_table_counts(conn)
            nrec = await conn.fetchval(
                "SELECT count(*) FROM kalshi_account_reconciliations")
            _acct, n, did = await full_setup(conn, "planned", now=now)
            paper_before = await conn.fetch(
                "SELECT * FROM paper_orders WHERE decision_id = $1", did)
        s = await KS.pass_once(p, now=now, env=NO_CRED_ENV)
        assert s["status"] == "OK", s
        async with p.acquire() as conn:
            r = await row_of(conn, did)
            assert r["state"] == "PLANNED" and r["exclusion"] is None
            assert r["mode"] == "SHADOW" and r["production_effect"] == "NONE"
            assert r["ticker"] == n["ticker"] and r["claim_fingerprint"] == \
                n["fp"]
            assert r["paper_qty"] == Decimal(2000) and r["scale"] == Decimal(
                1000)
            assert r["live_qty"] == 2 and r["venue_minimum"] == 1
            assert r["raw_scaled_qty"] == Decimal(2)
            assert r["paper_notional_usd"] == Decimal("900.000000")
            assert Decimal(j(r["detail"])["paper_notional_over_scale"]) == \
                Decimal("0.9")
            assert r["limit_price"] == Decimal("0.4500")
            assert r["exec_vwap"] == Decimal("0.440000")
            assert r["fee_usd"] == Decimal("0.040000")
            assert r["all_in_usd"] == Decimal("0.920000")
            assert r["ev_per_contract"] == Decimal("0.090000")
            assert r["ev_usd"] == Decimal("0.180000")
            assert j(r["fee_terms"])["version"] == "quadratic@x1"
            pay = j(r["plan_payload"])
            assert pay["count"] == "2.00" and pay["price"] == "0.4500"
            assert pay["client_order_id"] == KO.client_order_id(
                KS.shadow_id(did))
            assert j(r["buying_power"])["status"] == "UNREAD"
            # nothing live moved, no reconciliation without a credential
            assert await live_table_counts(conn) == before
            assert await conn.fetchval(
                "SELECT count(*) FROM kalshi_account_reconciliations") == nrec
            rd = await conn.fetchrow(
                "SELECT * FROM kalshi_shadow_account_reads WHERE read_id = $1",
                KS.read_id(now))
            assert rd["outcome"] == "UNAVAILABLE"
            assert rd["reason"] == KV.KALSHI_CREDENTIAL_ABSENT
            # the PAPER records are read, never written
            paper_after = await conn.fetch(
                "SELECT * FROM paper_orders WHERE decision_id = $1", did)
            assert [dict(x) for x in paper_after] == [
                dict(x) for x in paper_before]
    finally:
        await p.close()


@pg
async def test_excluded_when_no_certified_pair_with_the_missing_part_named():
    now = _now(2)
    p = await pool()
    try:
        async with p.acquire() as conn:
            acct = await H.new_account(conn, "nopair", now=now - 60)
            n1, n2, n3 = names("np1"), names("np2"), names("np3")
            d1 = await seed_decision(conn, acct, n1, now=now)   # no alias
            await seed_pair(conn, n2, pmus_cert="NOT_CERTIFIED")
            d2 = await seed_decision(conn, acct, n2, now=now)
            await seed_pair(conn, n3, kalshi_cert="NOT_CERTIFIED")
            d3 = await seed_decision(conn, acct, n3, now=now)
        await KS.pass_once(p, now=now, env=NO_CRED_ENV)
        async with p.acquire() as conn:
            for did, why in ((d1, KS.W_PMUS_ALIAS_ABSENT),
                             (d2, KS.W_PMUS_ALIAS_NOT_CERTIFIED),
                             (d3, KS.W_NO_KALSHI_ALIAS)):
                r = await row_of(conn, did)
                assert r["state"] == "EXCLUDED"
                assert r["exclusion"] == KS.R_KSH_NO_CERTIFIED_COUNTERPART
                assert j(r["detail"])["why"] == why
                assert r["live_qty"] == 0 and r["plan_payload"] is None
                assert r["ticker"] is None
    finally:
        await p.close()


@pg
async def test_excluded_below_the_venue_minimum_never_rounded_up():
    now = _now(3)
    p = await pool()
    try:
        async with p.acquire() as conn:
            _a, _n, did = await full_setup(conn, "belowmin", now=now, qty=300)
        await KS.pass_once(p, now=now, env=NO_CRED_ENV)
        async with p.acquire() as conn:
            r = await row_of(conn, did)
            assert r["exclusion"] == KS.R_KSH_BELOW_VENUE_MINIMUM
            assert r["plan_exclusion"] == KO.BELOW_VENUE_MINIMUM
            assert r["raw_scaled_qty"] == Decimal("0.3")
            assert r["live_qty"] == 0
            assert j(r["detail"])["plan"]["live_qty"] == 0
    finally:
        await p.close()


@pg
async def test_excluded_when_the_fee_makes_the_ev_negative():
    now = _now(4)
    p = await pool()
    try:
        async with p.acquire() as conn:
            acct, _n, d_fee = await full_setup(conn, "feeev", now=now,
                                              p_blended=0.45)
            n2 = names("evneg")
            await seed_pair(conn, n2)
            await seed_book(conn, n2, now=now)
            await seed_fee_terms(conn, n2, now=now)
            d_ev = await seed_decision(conn, acct, n2, now=now,
                                       p_blended=0.44)
        await KS.pass_once(p, now=now, env=NO_CRED_ENV)
        async with p.acquire() as conn:
            r = await row_of(conn, d_fee)
            assert r["exclusion"] == KS.R_KSH_FEE_MAKES_EV_NEGATIVE
            assert r["ev_per_contract"] == Decimal("-0.010000")
            assert Decimal(j(r["detail"])["ev_gross_per_contract"]) == \
                Decimal("0.010000")
            assert r["fee_usd"] == Decimal("0.040000")
            r = await row_of(conn, d_ev)
            assert r["exclusion"] == KS.R_KSH_EV_NOT_POSITIVE
    finally:
        await p.close()


@pg
async def test_one_row_per_decision_idempotent_and_append_only():
    now = _now(5)
    p = await pool()
    try:
        async with p.acquire() as conn:
            _a, _n, did = await full_setup(conn, "idem", now=now)
        s1 = await KS.pass_once(p, now=now, env=NO_CRED_ENV)
        s2 = await KS.pass_once(p, now=now + 1, env=NO_CRED_ENV)
        assert s1["planner"]["written"] >= 1
        assert s2["planner"]["written"] == 0 and s2["planner"]["seen"] == 0
        async with p.acquire() as conn:
            assert await conn.fetchval(
                "SELECT count(*) FROM kalshi_shadow_intents WHERE "
                "paper_decision_id = $1", did) == 1
            r = await row_of(conn, did)
            # the same row written again is a no-op, never a second row
            again = {c: r[c] for c in KS.COLUMNS}
            again["paper_decided_at"] = r["paper_decided_at"].timestamp()
            again["planned_at"] = r["planned_at"].timestamp()
            for c in KS._JSON:
                again[c] = j(again[c])
            assert await KS.write_row(conn, again) is False
            for sql in ("UPDATE kalshi_shadow_intents SET state = 'EXCLUDED'"
                        " WHERE paper_decision_id = $1",
                        "DELETE FROM kalshi_shadow_intents WHERE "
                        "paper_decision_id = $1"):
                with pytest.raises(asyncpg.RaiseError):
                    await conn.execute(sql, did)
    finally:
        await p.close()


@pg
async def test_the_database_refuses_anything_but_a_shadow_plan():
    now = _now(6)
    p = await pool()
    try:
        async with p.acquire() as conn:
            _a, _n, did = await full_setup(conn, "schema", now=now)
            base = KS.evaluate(
                {"decision_id": did, "decided_at": now - 5,
                 "us_market_slug": "s", "holding_side": "LONG",
                 "p_blended": 0.55},
                _order(order_id="paper_ord_schema"),
                pmus_aliases=[_alias("POLYMARKET_US", "s", "YES")],
                kalshi_aliases=[_alias("KALSHI", "T", "YES")],
                book={"ticker": "T", "readable": True, "observed_at": now - 1,
                      "yes_asks": [["0.44", 10]]},
                fee_terms=TERMS, control={"scale": 1000, "max_order_usd": 25},
                buying_power={"usd": None}, now=now)
            assert base["state"] == "PLANNED"

            async def refused(row, mode="SHADOW", effect="NONE"):
                vals = [KS._sql_value(c, row.get(c)) for c in KS.COLUMNS]
                sql = KS.INSERT_SQL.replace("'SHADOW', 'NONE'", "$%d, $%d" % (
                    len(vals) + 1, len(vals) + 2))
                tx = conn.transaction()
                await tx.start()
                try:
                    with pytest.raises(asyncpg.CheckViolationError):
                        await conn.fetchval(sql, *vals, mode, effect)
                finally:
                    await tx.rollback()
            for k, over in enumerate((
                    {"state": "SUBMITTING"}, {"state": "OPEN"},
                    {"state": "PLANNED", "ev_per_contract": Decimal(0)},
                    {"state": "PLANNED", "plan_payload": None},
                    {"state": "PLANNED", "live_qty": 0},
                    {"state": "EXCLUDED", "exclusion": None},
                    {"state": "EXCLUDED", "exclusion": "X"},  # with a payload
                    {"limit_price": Decimal("1.00")})):
                row = dict(base, shadow_id="kshadow:x%d" % k,
                           paper_decision_id="paper_dec_x%d" % k, **over)
                await refused(row)
            ok = dict(base, shadow_id="kshadow:mode",
                      paper_decision_id="paper_dec_mode")
            await refused(ok, mode="LIVE")
            await refused(ok, effect="ORDER")
    finally:
        await p.close()


@pg
async def test_stands_down_while_the_kalshi_control_is_enabled():
    now = _now(7)
    p = await pool()
    try:
        async with p.acquire() as conn:
            _a, _n, did = await full_setup(conn, "ctlon", now=now)
            rid = await conn.fetchval(
                "INSERT INTO kalshi_account_reconciliations (key_fingerprint,"
                " kalshi_env, verdict, complete, at) VALUES ('fp-test-ctl',"
                " 'demo', 'EMPTY', true, to_timestamp($1::float8 - 999999)) "
                "RETURNING reconciliation_id", now)
            await conn.execute(
                "UPDATE kalshi_smalllive_control SET enabled = true, "
                " key_fingerprint = 'fp-test-ctl', reconciliation_id = $1, "
                " kalshi_env = 'demo' WHERE id = 1", rid)
        try:
            s = await KS.pass_once(p, now=now, env=NO_CRED_ENV)
        finally:
            async with p.acquire() as conn:
                await conn.execute(
                    "UPDATE kalshi_smalllive_control SET enabled = false, "
                    " key_fingerprint = NULL, reconciliation_id = NULL, "
                    " kalshi_env = NULL WHERE id = 1")
                await conn.execute("DELETE FROM kalshi_account_reconciliations"
                                   " WHERE reconciliation_id = $1", rid)
        assert s["status"] == "STOOD_DOWN"
        assert s["stand_down"] == KS.R_KSH_STOOD_DOWN_CONTROL_ENABLED
        assert "planner" not in s and "account_read" not in s
        async with p.acquire() as conn:
            assert await row_of(conn, did) is None
            assert await conn.fetchval(
                "SELECT count(*) FROM kalshi_shadow_account_reads WHERE "
                "read_id = $1", KS.read_id(now)) == 0
    finally:
        await p.close()


@pg
async def test_stands_down_when_the_kalshi_small_live_switch_is_on():
    now = _now(8)
    p = await pool()
    try:
        async with p.acquire() as conn:
            _a, _n, did = await full_setup(conn, "envon", now=now)
        s = await KS.pass_once(p, now=now, env={
            "KALSHI_SMALLLIVE_ENABLED": "1", "KALSHI_ENV": "demo"})
        assert s["status"] == "STOOD_DOWN"
        assert s["stand_down"] == KS.R_KSH_STOOD_DOWN_ENV_SWITCH_ON
        async with p.acquire() as conn:
            assert await row_of(conn, did) is None
    finally:
        await p.close()


@pg
async def test_stands_down_unless_small_live_is_shadow():
    """Migration 225 CHECKs SMALL LIVE to SHADOW; inside a rolled-back
    transaction the CHECK is lifted to prove the planner reads the mode
    itself and stands down on anything else."""
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("ALTER TABLE small_live_control DROP CONSTRAINT "
                           "slc_shadow_only_ck")
        await conn.execute("UPDATE small_live_control SET mode = 'LIVE' "
                           "WHERE id = 1")
        g = await KS.gate(conn, env=NO_CRED_ENV)
        assert g["stand_down"] == KS.R_KSH_STOOD_DOWN_SMALL_LIVE_NOT_SHADOW
        assert g["small_live_mode"] == "LIVE"
    finally:
        await tx.rollback()
        await conn.close()
    conn = await asyncpg.connect(H.DSN)
    try:
        assert await conn.fetchval("SELECT mode FROM small_live_control "
                                   "WHERE id = 1") == "SHADOW"
        g = await KS.gate(conn, env=NO_CRED_ENV)
        assert g["stand_down"] is None
    finally:
        await conn.close()


def _patch_every_mutation(monkeypatch) -> list:
    """Every network-mutating primitive the API process could reach raises
    (and records that it was called)."""
    from sportsassets import live_executor as LE
    from sportsassets import pmus
    called = []

    def boom(name):
        def _f(*a, **k):
            called.append(name)
            raise AssertionError("network mutation reached: %s" % name)
        return _f
    for owner, attr in ((KV.KalshiClient, "submit"),
                        (KV.KalshiClient, "cancel"),
                        (KV.RequestsTransport, "send"),
                        (KV.RequestsTransport, "__init__"),
                        (EM.Venue, "place"), (EM.Venue, "cancel"),
                        (EM.Venue, "cancel_all"), (EM.Venue, "close"),
                        (pmus, "submit_fok"), (pmus, "cancel_order"),
                        (pmus, "close_position"),
                        (LE, "_submit_fok")):
        assert hasattr(owner, attr), (owner, attr)
        monkeypatch.setattr(owner, attr, boom("%s.%s" % (
            getattr(owner, "__name__", owner), attr)))
    return called


@pg
async def test_a_whole_pass_with_every_network_mutation_patched_to_raise(
        monkeypatch):
    """A PLANNED decision, an EXCLUDED one and a PRESENT credential: the pass
    plans, excludes and records a read-only reconciliation from GETs only,
    and not one mutating primitive is called."""
    called = _patch_every_mutation(monkeypatch)
    # inside one reconciliation window (the second read below is in it)
    now = float(int(_now(9) // KS.RECON_EVERY_S) * KS.RECON_EVERY_S + 100)
    env = cred_env()
    inner = F.RecordingTransport(
        F.ok({"balance": 0}), F.POSITIONS_EMPTY, F.ORDERS_EMPTY,
        F.FILLS_EMPTY, F.SETTLEMENTS_EMPTY)
    p = await pool()
    try:
        async with p.acquire() as conn:
            acct, _n, d_plan = await full_setup(conn, "nomut", now=now)
            d_excl = await seed_decision(conn, acct, names("nomut2"), now=now)
            before = await live_table_counts(conn)
        s = await KS.pass_once(p, now=now, env=env, transport=inner)
        assert s["status"] == "OK", s
        assert called == []
        assert inner.sent and all(x["method"] == "GET" and x["json"] is None
                                  for x in inner.sent)
        assert [x["url"].split("/trade-api/v2")[1] for x in inner.sent] == [
            "/portfolio/balance", "/portfolio/positions", "/portfolio/orders",
            "/portfolio/fills", "/portfolio/settlements"]
        assert inner.sent[2]["params"]["status"] == "resting"
        assert s["account_read"]["outcome"] == "RECORDED"
        async with p.acquire() as conn:
            assert (await row_of(conn, d_plan))["state"] == "PLANNED"
            assert (await row_of(conn, d_excl))["exclusion"] == \
                KS.R_KSH_NO_CERTIFIED_COUNTERPART
            rec = await conn.fetchrow(
                "SELECT * FROM kalshi_account_reconciliations WHERE "
                "reconciliation_id = $1", s["account_read"]["reconciliation_id"])
            assert rec["verdict"] == "EMPTY" and rec["complete"] is True
            assert rec["read_only"] is True and rec["baseline_accepted"] is \
                False
            assert rec["actor"] == KS.RECON_ACTOR
            assert rec["key_fingerprint"] == KV.fingerprint(
                env["KALSHI_API_KEY_ID"])
            assert rec["kalshi_env"] == "demo"
            assert abs(rec["at"].timestamp() - now) < 1
            rd = await conn.fetchrow(
                "SELECT * FROM kalshi_shadow_account_reads WHERE read_id = $1",
                KS.read_id(now))
            assert rd["outcome"] == "RECORDED"
            assert rd["reconciliation_id"] == rec["reconciliation_id"]
            # no secret is stored: names presence, shapes, fingerprints only
            blob = json.dumps(j(rd["credential"]))
            assert env["KALSHI_API_KEY_ID"] not in blob
            assert "PRIVATE KEY" not in blob
            assert await live_table_counts(conn) == before
            ctl = await conn.fetchrow("SELECT enabled, key_fingerprint, "
                                      "reconciliation_id FROM "
                                      "kalshi_smalllive_control WHERE id = 1")
            assert tuple(ctl) == (False, None, None)
        # the window is done: a second read in it writes nothing
        again = await KS.account_read(p, now=now + 5, env=env,
                                      transport=F.RaisingTransport())
        assert again["outcome"] == "WINDOW_DONE"
    finally:
        await p.close()


@pg
async def test_an_unreadable_account_is_recorded_as_read_never_as_empty(
        monkeypatch):
    _patch_every_mutation(monkeypatch)
    now = _now(10)
    env = cred_env()
    inner = F.RecordingTransport(F.ok({"error": "x"}, status=401))
    p = await pool()
    try:
        out = await KS.account_read(p, now=now, env=env, transport=inner)
        assert out["outcome"] == "RECORDED" and out["verdict"] == "UNREADABLE"
        async with p.acquire() as conn:
            rec = await conn.fetchrow(
                "SELECT verdict, complete, errors FROM "
                "kalshi_account_reconciliations WHERE reconciliation_id = $1",
                out["reconciliation_id"])
            assert rec["verdict"] == "UNREADABLE" and rec["complete"] is False
            assert j(rec["errors"])["balance"] == "http_401"
    finally:
        await p.close()


@pg
async def test_buying_power_from_a_fresh_complete_read_caps_the_plan():
    now = _now(11)
    p = await pool()
    try:
        async with p.acquire() as conn:
            _a, _n, did = await full_setup(conn, "cash", now=now)
            await conn.execute(
                "INSERT INTO kalshi_account_reconciliations (key_fingerprint,"
                " kalshi_env, verdict, complete, balance_usd, at) VALUES "
                " ('fp-test-cash', 'demo', 'EMPTY', true, 0.50, "
                " to_timestamp($1::float8))", now - 60)
            bp = await KS.buying_power(conn, now=now)
            assert bp["status"] == "READ" and bp["usd"] == Decimal("0.500000")
            # a read stamped in the future is never used
            assert (await KS.buying_power(conn, now=now - 3600))["status"] \
                == "UNREAD"
        await KS.pass_once(p, now=now, env=NO_CRED_ENV)
        async with p.acquire() as conn:
            r = await row_of(conn, did)
            assert r["exclusion"] == KS.R_KSH_INSUFFICIENT_CASH
            assert j(r["buying_power"])["status"] == "READ"
    finally:
        await p.close()


@pg
async def test_the_backfill_reads_seven_days_then_the_steady_window():
    now = _now(12)
    p = await pool()
    try:
        async with p.acquire() as conn:
            acct = await H.new_account(conn, "backfill", now=now - 4 * DAY)
            old = await seed_decision(conn, acct, names("bf1"), now=now,
                                      age=3 * DAY)
            mid = await seed_decision(conn, acct, names("bf2"), now=now,
                                      age=8 * DAY)
        KS.STATE["drained"] = False
        s = await KS.pass_once(p, now=now, env=NO_CRED_ENV)
        assert s["planner"]["window_s"] == KS.BACKFILL_S
        assert KS.STATE["drained"] is True
        async with p.acquire() as conn:
            r = await row_of(conn, old)
            assert r["exclusion"] == KS.R_KSH_NO_CERTIFIED_COUNTERPART
            assert r["decision_age_s"] == Decimal("259200.000")
            assert await row_of(conn, mid) is None       # older than 7 days
            two_h = await seed_decision(conn, acct, names("bf3"), now=now + 60,
                                        age=7200)
        s = await KS.pass_once(p, now=now + 60, env=NO_CRED_ENV)
        assert s["planner"]["window_s"] == KS.LOOKBACK_S
        async with p.acquire() as conn:
            assert await row_of(conn, two_h) is None     # outside one hour
    finally:
        await p.close()


@pg
async def test_a_stale_decision_with_a_certified_pair_is_never_planned():
    now = _now(13)
    p = await pool()
    try:
        async with p.acquire() as conn:
            _a, _n, did = await full_setup(conn, "stale", now=now, age=45)
        await KS.pass_once(p, now=now, env=NO_CRED_ENV)
        async with p.acquire() as conn:
            r = await row_of(conn, did)
            assert r["exclusion"] == KS.R_KSH_DECISION_STALE_AT_PLAN
            assert r["ticker"] is not None and r["live_qty"] == 0
            assert j(r["detail"])["plan_live_qty"] == 2
    finally:
        await p.close()


@pg
async def test_an_enter_decision_without_a_paper_order_is_counted_not_planned():
    """Not linked -> nothing to mirror: no row, counted in the pass."""
    now = _now(15)
    p = await pool()
    try:
        async with p.acquire() as conn:
            acct = await H.new_account(conn, "unlinked", now=now - 60)
            did = "paper_dec_kshadow_unlinked_%s" % uuid.uuid4().hex[:8]
            await conn.execute(
                "INSERT INTO paper_decisions (decision_id, session_id, "
                " account_id, decided_at, us_market_slug, holding_side, "
                " verdict, internal_model, pinnacle, qualification_gaps, "
                " policy_version, simulator_version) VALUES ($1,$2,$3,"
                " to_timestamp($4::float8),'test-unlinked','LONG','ENTER',"
                " '{}','{}','[]','TEST_POLICY','TEST_SIM')",
                did, acct["session_id"], acct["account_id"], now - 5)
        s = await KS.pass_once(p, now=now, env=NO_CRED_ENV)
        assert s["planner"]["enter_without_entry_order"] == 1
        assert s["planner"]["seen"] == 0
        async with p.acquire() as conn:
            assert await row_of(conn, did) is None
    finally:
        await p.close()


@pg
async def test_one_bad_decision_never_stalls_the_pass(monkeypatch):
    """A decision whose evaluation raises, and one whose row the database
    refuses, each get the bare named exclusion (with the error's type) in
    their own savepoint; the good decision beside them is still planned."""
    now = _now(14)
    real = KS.evaluate
    p = await pool()
    try:
        async with p.acquire() as conn:
            acct, _n, good = await full_setup(conn, "poison", now=now)
            boom = await seed_decision(conn, acct, names("poison2"), now=now)
            bad = await seed_decision(conn, acct, names("poison3"), now=now)

        def evaluate(d, o, **kw):
            if d["decision_id"] == boom:
                raise RuntimeError("synthetic evaluation failure")
            row = real(d, o, **kw)
            if d["decision_id"] == bad:
                row = dict(row, state="SUBMITTING")    # the CHECK refuses
            return row
        monkeypatch.setattr(KS, "evaluate", evaluate)
        s = await KS.pass_once(p, now=now, env=NO_CRED_ENV)
        assert s["status"] == "OK", s
        assert s["planner"]["errors"] == ["CheckViolationError"]
        async with p.acquire() as conn:
            assert (await row_of(conn, good))["state"] == "PLANNED"
            for did, err in ((boom, "RuntimeError"),
                             (bad, "CheckViolationError")):
                r = await row_of(conn, did)
                assert r["exclusion"] == KS.R_KSH_PLAN_EXCLUDED
                assert j(r["detail"])["error"] == err
    finally:
        await p.close()


def _mig(name):
    import pathlib
    mig = pathlib.Path(KS.__file__).resolve().parents[1] / "migrations"
    return ((mig / "367_kalshi_shadow_planner.sql").read_text() if name == "up"
            else (mig / "rollback" / "367_kalshi_shadow_planner.down.sql")
            .read_text())


def test_migration_367_touches_no_existing_table():
    """Two NEW tables only: the previous release runs unchanged on this
    schema (the upgrade-path receipt's own statement parser)."""
    import re
    import sys
    import pathlib
    sys.path.insert(0, str(pathlib.Path(KS.__file__).resolve().parents[1]
                           / "tools"))
    import upgrade_path_receipt as UPR
    up = _mig("up")
    created = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", up))
    assert created == {"kalshi_shadow_intents", "kalshi_shadow_account_reads"}
    assert UPR.touched_tables(up) <= created
    # no statement writes a row (the trigger clause "BEFORE UPDATE OR
    # DELETE" is not a statement)
    stmts = re.sub(r"--[^\n]*", " ", up)
    assert not re.search(r"(^|;)\s*(UPDATE|DELETE\s+FROM|INSERT\s+INTO)\b",
                         stmts, re.M | re.I)
    down = _mig("down")
    for t in created:
        assert "DROP TABLE IF EXISTS %s;" % t in down


@pg
async def test_migration_367_is_idempotent_and_its_rollback_refuses_evidence():
    """Inside one rolled-back transaction: UP twice; the rollback refuses
    while either append-only table holds a row; with both empty it drops
    them, twice; UP re-creates them. The database is left as found."""
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(_mig("up"))
        await conn.execute(_mig("up"))
        await conn.execute(
            "INSERT INTO kalshi_shadow_account_reads (read_id, at, outcome, "
            " reason, planner_version) VALUES ('kshadow-acct-rb', now(), "
            " 'UNAVAILABLE', 'KALSHI_CREDENTIAL_ABSENT', 'T')")
        row = KS._failed_row(
            {"decision_id": "paper_dec_rb", "decided_at": 1000.0,
             "us_market_slug": "s", "holding_side": "LONG"},
            _order(order_id="paper_ord_rb"), control={"scale": 1000},
            buying_power={}, now=1000.0, error=None)
        assert await KS.write_row(conn, row) is True

        async def refused(table):
            sp = conn.transaction()
            await sp.start()
            try:
                with pytest.raises(asyncpg.RaiseError) as e:
                    await conn.execute(_mig("down"))
                assert table in str(e.value)
            finally:
                await sp.rollback()
        for t, trg in (("kalshi_shadow_intents", "kshadow_intents_append_only"),
                       ("kalshi_shadow_account_reads",
                        "kshadow_reads_append_only")):
            await refused(t)
            await conn.execute("ALTER TABLE %s DISABLE TRIGGER %s" % (t, trg))
            await conn.execute("DELETE FROM %s" % t)
        await conn.execute(_mig("down"))
        await conn.execute(_mig("down"))
        for t in ("kalshi_shadow_intents", "kalshi_shadow_account_reads"):
            assert await conn.fetchval("SELECT to_regclass($1)", t) is None
        await conn.execute(_mig("up"))
        assert await conn.fetchval(
            "SELECT to_regclass('kalshi_shadow_intents')") is not None
    finally:
        await tx.rollback()
        await conn.close()


def test_the_lifespan_arms_the_runner_with_a_kill_switch(monkeypatch):
    import pathlib
    monkeypatch.setenv("KALSHI_SHADOW_PLANNER", "off")
    assert KS.enabled() is False
    monkeypatch.delenv("KALSHI_SHADOW_PLANNER")
    assert KS.enabled() is True
    app = (pathlib.Path(KS.__file__).parent / "api" / "app.py").read_text()
    life = app[app.index("async def lifespan"):app.index("app = FastAPI(")]
    assert "from .. import kalshi_shadow as _KSHADOW" in life
    assert "_KSHADOW.run(_cap_pool)" in life
    tasks = life.split("tasks = [t for t in (")[1]
    assert "kshadow_task" in tasks[:tasks.index(")")]
    # the mirror's runner is armed first; the planner sits beside it
    assert life.index("_EXM.run(") < life.index("_KSHADOW.run(")


def test_the_loop_health_inventory_copies_the_runner_constants():
    from sportsassets import loop_health as LH
    spec = {s["name"]: s for s in LH.API_LOOPS}["kalshi_shadow.runner"]
    assert spec["process"] == "api" and spec["capital_critical"] is False
    assert spec["cadence_s"] == KS.INTERVAL_S
    assert spec["lease"]["key"] == KS.LOCK_KEY == LH.K_KSHADOW
    assert spec["armed"] == ("env_not_off", KS.ENV_FLAG, "on")
    assert spec["sources"][0][1] == KS.SERVICE
    assert spec["sources"][0][2] == frozenset({"ok", "stood_down"})


def test_the_disabled_runner_returns_without_touching_the_pool(monkeypatch):
    monkeypatch.setenv("KALSHI_SHADOW_PLANNER", "off")

    async def never():
        raise AssertionError("the pool was requested")
    asyncio.run(KS.run(never, first_delay_s=0))


def test_these_proofs_are_capital_critical():
    import pathlib
    listed = (pathlib.Path(__file__).resolve().parents[1] / "tools" /
              "capital_critical_tests.txt").read_text().splitlines()
    assert "tests/test_rc63_kalshi_shadow.py" in listed
    assert "tests/test_kalshi_isolation.py" in listed
