"""RED TEAM CLOSEOUT V1 -- THE CANONICAL EXPOSURE LOCK, BOUND TO THE PAPER
LEDGER'S ENTRY PATH (redteam.exposure; chaos C19).

BETTOR sizes economic claims, not venue instruments: two venue aliases the
canonical layer proved equal are ONE claim (correlation 1), two strategies
on one contract are ONE exposure, and an ENTRY that passes every venue-level
cap but carries its claim / event past the limit is refused under the
account lock. Exits / reductions never reach the lock. Synthetic scratch
accounts only; every write is rolled back.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets.redteam import exposure as X
from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


# ── pure ─────────────────────────────────────────────────────────────

def test_a_proven_alias_keys_the_claim_and_labels_never_split_it():
    aliases = {("pmus-nyy", "YES"): "fpNYY", ("pmus-nyy-2", "YES"): "fpNYY"}
    a = X.row(slug="pmus-nyy", holding_side="LONG", fixture="F1",
              notional=60, qty=100, alias_group="DEREK", aliases=aliases)
    b = X.row(slug="pmus-nyy-2", holding_side="LONG", fixture="F1",
              notional=60, qty=100, alias_group="ARCHER", aliases=aliases)
    c = X.row(slug="pmus-nyy", holding_side="LONG", fixture="F1",
              notional=10, qty=10, alias_group="ALLIE", aliases=aliases)
    g = X.gate([a, b, c], claim_cap=Decimal(100), event_cap=Decimal(500))
    assert set(g["claims"]) == {"fpNYY"}
    assert g["claims"]["fpNYY"]["signed_notional"] == Decimal(130)
    assert g["claims"]["fpNYY"]["alias_count"] == 3
    assert g["blockers"] == ("CLAIM_LIMIT:fpNYY",)


def test_an_unproven_instrument_is_never_merged_into_another_claim():
    a = X.row(slug="m1", holding_side="LONG", fixture="F", notional=50,
              qty=1, alias_group="s", aliases={})
    b = X.row(slug="m1", holding_side="SHORT", fixture="F", notional=50,
              qty=1, alias_group="s", aliases={})
    g = X.gate([a, b], claim_cap=Decimal(60), event_cap=Decimal(90))
    assert set(g["claims"]) == {"POLYMARKET_US:m1:YES", "POLYMARKET_US:m1:NO"}
    # the two sides are different claims, but one event
    assert g["blockers"] == ("EVENT_LIMIT:F",)


def test_the_limits_are_the_existing_fixture_cap_or_tighter():
    from sportsassets import allie_capital as A
    assert X.limits(None) == (Decimal(str(A.FIXTURE_CAP_USD)),) * 2
    assert X.limits({"per_market_cap_usd": 10000.0,
                     "per_fixture_cap_usd": 15000.0}) == (Decimal(10000),
                                                          Decimal(15000))
    # a looser account cap never loosens the lock
    assert X.limits({"per_market_cap_usd": 1e9}) == (
        Decimal(str(A.FIXTURE_CAP_USD)),) * 2


# ── against the ledger ───────────────────────────────────────────────

def _o(acct, *, key, slug, qty, limit, strategy=None, fixture="fx-rt",
       side="LONG", role="ENTRY", direction="BUY", group_id=None):
    o = H.order(acct, key=key, slug=slug, qty=qty, limit=limit,
                fixture=fixture, holding_side=side, role=role,
                direction=direction, group_id=group_id)
    if strategy:
        o["strategy"] = strategy
    return o


async def _alias(conn, slug, side, fp, event_key="ev"):
    await conn.execute(
        "INSERT INTO canonical_claim_aliases (alias_key, event_key, "
        " claim_fingerprint, venue, market_id, side, subject, "
        " mapping_status, settlement_status, certificate_status) VALUES "
        "($1,$2,$3,'POLYMARKET_US',$4,$5,'HOME','ESTABLISHED','PROVEN',"
        " 'CERTIFIED')", "POLYMARKET_US|%s|%s" % (slug, side), event_key, fp,
        slug, side)


@pg
async def test_two_aliases_of_one_claim_are_one_exposure_and_refuse():
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    try:
        acct = await H.new_account(conn, "rtx")
        a, b = ("%s:nyy-a" % acct["account_id"],
                "%s:nyy-b" % acct["account_id"])
        await _alias(conn, a, "YES", "fpNYY")
        await _alias(conn, b, "YES", "fpNYY")
        # 70k on alias A (reserved), then 70k on alias B: each market alone
        # is under every venue-level cap; the CLAIM is 140k > 125k
        got = await L.submit_order(conn, _o(acct, key="a", slug=a,
                                            qty=140000, limit=0.5),
                                   fee_fn=H.zero_fee, now=H.T0)
        assert got["ok"], got
        got = await L.submit_order(conn, _o(acct, key="b", slug=b,
                                            qty=140000, limit=0.5),
                                   fee_fn=H.zero_fee, now=H.T0 + 1)
        assert not got["ok"] and got["refusal"] == X.R_CLAIM, got
        assert got["claim"]["claim_key"] == "fpNYY"
        assert got["claim"]["alias_count"] == 2
        assert got["under_lock"] is True
        # nothing was reserved for the refused order
        n = await conn.fetchval("SELECT count(*) FROM paper_orders WHERE "
                                "account_id = $1", acct["account_id"])
        assert n == 1
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_two_strategies_on_one_contract_are_one_exposure():
    """C19: a strategy / agent label never hides duplicate exposure."""
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    try:
        acct = await H.new_account(conn, "rtx")
        s = "%s:same" % acct["account_id"]
        got = await L.submit_order(conn, _o(acct, key="s1", slug=s,
                                            qty=140000, limit=0.5),
                                   fee_fn=H.zero_fee, now=H.T0)
        assert got["ok"], got
        got = await L.submit_order(
            conn, _o(acct, key="s2", slug=s, qty=140000, limit=0.5,
                     strategy="RED_TEAM_SECOND_LABEL"),
            fee_fn=H.zero_fee, now=H.T0 + 1)
        assert not got["ok"], got
        assert got["refusal"] in (X.R_CLAIM, L.R_SAME_CONTRACT_HELD), got
        if got["refusal"] == X.R_CLAIM:
            assert got["claim"]["claim_key"] == \
                "POLYMARKET_US:%s:YES" % s
            assert Decimal(got["claim"]["notional_usd"]) == Decimal(140000)
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_the_event_limit_counts_every_claim_of_the_fixture():
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    try:
        acct = await H.new_account(conn, "rtx")
        m1, m2 = ("%s:ml" % acct["account_id"],
                  "%s:spread" % acct["account_id"])
        got = await L.submit_order(conn, _o(acct, key="e1", slug=m1,
                                            qty=140000, limit=0.5),
                                   fee_fn=H.zero_fee, now=H.T0)
        assert got["ok"], got
        got = await L.submit_order(conn, _o(acct, key="e2", slug=m2,
                                            qty=120000, limit=0.5),
                                   fee_fn=H.zero_fee, now=H.T0 + 1)
        assert not got["ok"] and got["refusal"] == X.R_EVENT, got
        assert got["event_key"] == "fx-rt"
        assert Decimal(got["event_notional_usd"]) == Decimal(130000)
        # another fixture is not blocked by this one's exposure
        got = await L.submit_order(
            conn, _o(acct, key="e3", slug="%s:other" % acct["account_id"],
                     qty=1000, limit=0.5, fixture="fx-other"),
            fee_fn=H.zero_fee, now=H.T0 + 2)
        assert got["ok"], got
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_an_exit_on_a_breached_claim_is_never_refused_by_the_lock():
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    try:
        acct = await H.new_account(conn, "rtx")
        s = "%s:held" % acct["account_id"]
        o = _o(acct, key="buy", slug=s, qty=100, limit=0.5)
        got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=H.T0)
        assert got["ok"], got
        await H.observe(conn, s, H.T0 + 3.0, offers=[(0.5, 100)])
        await SIM.simulate_order(conn, got["order"]["order_id"],
                                 now=H.T0 + 4.0, fee_fn=H.zero_fee)
        # every limit is now 0: the book itself breaches
        tight = {"per_market_cap_usd": 0.0, "per_fixture_cap_usd": 0.0}
        assert X.limits(tight) == (Decimal(0), Decimal(0))
        buy = await L.submit_order(conn, _o(acct, key="buy2", slug=s, qty=1,
                                            limit=0.5,
                                            group_id=o["group_id"]),
                                   fee_fn=H.zero_fee, now=H.T0 + 5,
                                   caps=tight)
        assert not buy["ok"]
        sale = await L.submit_order(
            conn, _o(acct, key="exit", slug=s, qty=100, limit=0.49,
                     role="EXIT", direction="SELL", group_id=o["group_id"]),
            fee_fn=H.zero_fee, now=H.T0 + 6, caps=tight)
        assert sale["ok"], sale
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_an_unreadable_exposure_refuses_and_the_transaction_survives(
        monkeypatch):
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    try:
        acct = await H.new_account(conn, "rtx")

        async def boom(conn_, o, **kw):
            await conn_.execute("SELECT * FROM no_such_table_red_team")

        monkeypatch.setattr(X, "entry_refusal", boom)
        got = await L.submit_order(
            conn, _o(acct, key="u", slug="%s:u" % acct["account_id"],
                     qty=10, limit=0.5), fee_fn=H.zero_fee, now=H.T0)
        assert not got["ok"]
        assert got["refusal"] == L.R_CANONICAL_EXPOSURE_UNREADABLE
        assert await conn.fetchval("SELECT 1") == 1
    finally:
        await tr.rollback()
        await conn.close()


@pg
async def test_the_census_writes_one_receipt_per_claim_with_aliases():
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    try:
        acct = await H.new_account(conn, "rtx")
        a, b = ("%s:c-a" % acct["account_id"], "%s:c-b" % acct["account_id"])
        await _alias(conn, a, "YES", "fpC")
        await _alias(conn, b, "YES", "fpC")
        for k, s in (("c1", a), ("c2", b)):
            got = await L.submit_order(conn, _o(acct, key=k, slug=s, qty=100,
                                                limit=0.5),
                                       fee_fn=H.zero_fee, now=H.T0)
            assert got["ok"], got
        cen = await X.census(conn, acct["account_id"], sha="t" * 40,
                             at=H.T0)
        assert cen["eligible"] and cen["claims"] == 1
        r = cen["receipts"][0]
        assert r["claim_key"] == "fpC" and r["alias_count"] == 2
        assert r["payoff_fingerprint"] == "fpC"
        assert Decimal(r["signed_notional"]) == Decimal(100)
    finally:
        await tr.rollback()
        await conn.close()
