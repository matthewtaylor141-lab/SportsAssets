"""CAPITAL-CRITICAL (rc6.2 pmus-exec): THE RETAIL ACTUAL PATH, AUDITED BEFORE
ANY ACTIVATION -- ActualLane (execution_intent.py) -> execmirror.Venue on the
PMUS_EXECMIRROR retail Ed25519 account.

The independent read-only audit of this path (scratch probes A1-A8) found six
code blockers. Each is fixed minimally and proven here; every test in §1-§5
asserts the FIXED behaviour and fails on the base (rc6/int-62 @ 14170049):

  §1  managing an ACTUAL position: a risk-reducing SELL (EXIT / REDUCE /
      STANDING_PROTECTION / ORPHAN_CLOSE) is admitted without a token only
      outside SHADOW, only for those roles, only on the held side, and only
      up to the group's uncommitted venue-confirmed inventory re-read under
      the row lock; a BUY always needs the canonical authorization; in SHADOW
      the SELL is refused exactly as before (probe A1 inverted);
  §2  duplicate exposure: one market, one exposure (a per-market transaction
      lock in the claim; a non-terminal order or held contracts refuse by
      name), and the account's open + held notional within
      execmirror_control's aggregate cap (NULL -> max_order_usd, fail closed
      small), read under an account-wide lock taken after the market's so
      two markets' claims at once never pass it on one reading (probe A7
      inverted; review: two dispatches on two markets);
  §3  cancellation: a cancel the venue did not accept is never recorded as
      requested-and-accepted, and a CANCEL_REQUESTED order still working at
      the venue is re-sent with bounded attempts and back-off, each an event
      (probe A4 inverted);
  §4  reconciliation: signed net positions; Audrey never calls a group
      MATCHED / NOT_MIRRORED from a snapshot older than 180 s (STALE); the
      ACTUAL lane refuses while the newest snapshot is unreconciled (probe A6
      inverted). The read-only snapshot while STOPPED stays an OWNER decision
      (redteam/controls.OWNER_SNAPSHOT_WHILE_STOPPED) -- pinned unchanged;
  §5  the kill switch: the claim is conditional on the control IN THE INSERT
      (the control row share-locked), re-checked immediately before the
      send; emergency_stop is done only when the venue's cancel-all
      succeeded and no SUBMITTING / UNKNOWN / OPEN / PARTIALLY_FILLED row
      remains, nor a CANCEL_REQUESTED one whose venue record, read again after
      the cancel-all, does not say the order ended (its cancel re-sent on the
      bounded back-off meanwhile), else STOP_INCOMPLETE and retried (probes
      A2, A3, A5 inverted; review: a 503 cancel beside a 2xx cancel-all);
  §6  THE WIRE: a SUCCESSFUL order on the activation path -- ActualLane._run
      against the REAL execmirror.Venue built on the pinned polymarket_us
      1.0.2 client over httpx.MockTransport, a test-only LiveAuthorization
      under a monkeypatched non-SHADOW mode: one signed IOC create (Ed25519
      verified with the test key), the order-record read, a partial fill
      booked from the record, the remainder's cancel, the Xavier handoff, the
      exit SELL admitted under §1, reconciliation against a mocked
      /v1/portfolio/positions -- and the SAME flow in SHADOW refusing every
      write with nothing on the wire;
  §7  the ~$1 pilot's sizing at scale 1,000 and max_order_usd 1.00 (a size
      below one whole contract refused, never enlarged -- review: 0.6 -> 1).

THE MODE NEVER CHANGES OUTSIDE A TEST. SMALL_LIVE_MODE stays SHADOW in
live_authorization and live_parity (pinned below), migration 225's CHECK
stays; the non-SHADOW mode and the LiveAuthorization exist only inside the
tests that monkeypatch them. Fake venue, recording client or mock transport
only: no credential (a throwaway Ed25519 key generated per test), no network,
no order anywhere. Own Postgres (RN1X_TEST_DSN); the control row is restored.
"""
from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
import uuid
from decimal import Decimal

import httpx
import pytest

from sportsassets import execmirror as M
from sportsassets import execmirror_probe as EP
from sportsassets import execution_intent as EI
from sportsassets import live_authorization as LA
from sportsassets import live_parity as LP
from sportsassets import venue_pace as VP

from tests import test_execmirror as TE

pg = TE.pg
TEST_MODE = "LIVE_TEST_ONLY_NOT_IN_THIS_RELEASE"


# ───────────────────────────── shared helpers ─────────────────────────────

@pytest.fixture(autouse=True)
async def _restore_control():
    """Every test here leaves execmirror_control and the lane's tables as it
    found them (a FILLED row left behind reads as ACTUAL capital elsewhere)."""
    if not TE.DSN:
        yield
        return
    conn = await TE._conn()
    try:
        before = dict(await conn.fetchrow(
            "SELECT * FROM execmirror_control WHERE id = 1"))
    finally:
        await conn.close()
    yield
    conn = await TE._conn()
    try:
        cols = [c for c in before if c != "id"]
        await conn.execute(
            "UPDATE execmirror_control SET %s WHERE id = 1" % ", ".join(
                "%s = $%d" % (c, i + 1) for i, c in enumerate(cols)),
            *[before[c] for c in cols])
        await conn.execute("TRUNCATE smalllive_reviews, smalllive_handoffs, "
                           "smalllive_reconciliations")
        await conn.execute("TRUNCATE execmirror_fills, execmirror_events, "
                           "execmirror_snapshots, execmirror_orders")
    finally:
        await conn.close()


def _live_mode(monkeypatch) -> None:
    """TEST-ONLY: the activation this release does not have, in BOTH modules
    (execmirror.small_live_is_shadow fails closed if either says SHADOW)."""
    monkeypatch.setattr(LA, "SMALL_LIVE_MODE", TEST_MODE)
    monkeypatch.setattr(LP, "SMALL_LIVE_MODE", TEST_MODE)


def _canonical_stand_in(monkeypatch) -> list:
    """live_parity.authorize_live_exposure replaced by its own TAIL: the
    governance verdict is stated admissible (a test assumption) and the
    token is whatever live_parity.issue_live_authorization issues in the
    current mode -- a real LiveAuthorization outside SHADOW, None in SHADOW
    (-> the production refusal). Returns the issued tokens."""
    issued: list = []

    async def authorize(conn, *, decision_id=None, named=None, order=None,
                        now=None, **kw):
        tok = LP.issue_live_authorization(
            {"intent_id": "cdi_test_%s" % decision_id,
             "content_sha": "0" * 64},
            governance={"admissible": True, "stated_by_test": True},
            now=time.time() if now is None else now)
        if not LP.canonical_live_authorized(tok):
            return {"ok": False, "refusal": LP.R_NO_LIVE_AUTHORIZATION,
                    "detail": {"mode": LP.SMALL_LIVE_MODE}, "token": None}
        issued.append(tok)
        return {"ok": True, "refusal": None, "token": tok,
                "detail": {"intent_id": tok.intent_id,
                           "governance_stated_by_test": True}}
    monkeypatch.setattr(LP, "authorize_live_exposure", authorize)
    return issued


class _RecClient:
    """Stands where the SDK client stands under execmirror.Venue: records
    every create and hands it to the FakeVenue (so the order record exists
    for the reads that follow)."""

    def __init__(self, fake):
        outer = self
        self.created: list = []

        class orders:
            @staticmethod
            def create(params):
                outer.created.append(dict(params))
                return fake.place({k: v for k, v in params.items()
                                   if k != "synchronousExecution"})
        self.orders = orders


class ProdPlace(M.Venue):
    """The PRODUCTION Venue.place (canonical authorization OR the
    risk-reducing admission, then the client's create); every other call
    delegated to the FakeVenue."""

    def __init__(self, fake):
        self.rec = _RecClient(fake)
        super().__init__(client=self.rec)
        self.f = fake
        self._pace = lambda: None

    def cancel(self, vid, slug):
        return self.f.cancel(vid, slug)

    def cancel_all(self):
        return self.f.cancel_all()

    def close(self, slug, bips=300):
        return self.f.close(slug, bips)

    def order(self, vid):
        return self.f.order(vid)

    def open_orders(self, slugs=None):
        return self.f.open_orders(slugs)

    def own_trades(self, slug, since):
        return self.f.own_trades(slug, since)

    def balances(self):
        return self.f.balances()

    def positions(self):
        return self.f.positions()

    def bbo(self, slug):
        return self.f.bbo(slug)

    def quote(self, slug):
        return self.f.quote(slug)


async def _held(conn, group_id) -> int:
    return (await M.live_inventory(conn, group_id))["held"]


async def _events(conn, kind, mirror_id=None) -> list:
    rows = await conn.fetch(
        "SELECT detail FROM execmirror_events WHERE kind = $1"
        " AND ($2::text IS NULL OR mirror_id = $2) ORDER BY event_id",
        kind, mirror_id)
    return [json.loads(r["detail"]) if isinstance(r["detail"], str)
            else r["detail"] for r in rows]


def _j(v):
    return json.loads(v) if isinstance(v, str) else (v or {})


async def _held_position(conn, monkeypatch, *, qty=2000, fill=2):
    """An ACTUAL position of `fill` contracts (entry through the lane on the
    FakeVenue) with its paper sibling filled."""
    acct, venue, mirror = await TE._setup(conn, monkeypatch)
    entry = await TE._paper_order(conn, acct, qty=qty)
    venue.behaviour = [{"fill": fill}]
    await mirror.tick(conn)
    await TE._paper_fill(conn, acct, entry, qty=qty)
    assert await _held(conn, entry["group_id"]) == fill
    return acct, venue, mirror, entry


# ═════════════════════════════════════════════════════════════════════
# §0 THE MODE IS SHADOW IN CODE, AND STAYS SO
# ═════════════════════════════════════════════════════════════════════

def test_small_live_is_shadow_in_both_modules_and_the_new_constants_are_named():
    assert LA.SMALL_LIVE_MODE == LP.SMALL_LIVE_MODE == "SHADOW"
    assert M.small_live_is_shadow() is True
    assert M.RISK_REDUCING_ROLES == ("EXIT", "REDUCE", "STANDING_PROTECTION",
                                     "ORPHAN_CLOSE")
    assert M.R_SELL_ABOVE_UNCOMMITTED_INVENTORY == \
        "RISK_REDUCING_SELL_ABOVE_UNCOMMITTED_LIVE_INVENTORY"
    assert EI.R_VENUE_POSITION_DISAGREES == \
        "VENUE_POSITION_DISAGREES_WITH_MIRROR_FILLS"
    assert EI.R_MARKET_HAS_OPEN_ORDER == "ACTUAL_MARKET_HAS_A_NON_TERMINAL_ORDER"
    assert EI.R_MARKET_ALREADY_HELD == "ACTUAL_MARKET_ALREADY_HELD"
    assert EI.R_AGGREGATE_ABOVE_CAP == "ACTUAL_OPEN_AND_HELD_NOTIONAL_ABOVE_CAP"
    assert M.R_STOP_INCOMPLETE == "EMERGENCY_STOP_INCOMPLETE"
    # the snapshot admissibility bound is the ACTUAL lane's own
    assert M.ACCOUNT_SNAPSHOT_MAX_AGE_S == EI.MAX_ACCOUNT_SNAPSHOT_AGE_S == 180.0


def test_either_module_saying_shadow_keeps_the_sell_admission_closed(monkeypatch):
    monkeypatch.setattr(LP, "SMALL_LIVE_MODE", TEST_MODE)
    assert M.small_live_is_shadow() is True          # live_authorization: SHADOW
    monkeypatch.setattr(LP, "SMALL_LIVE_MODE", "SHADOW")
    monkeypatch.setattr(LA, "SMALL_LIVE_MODE", TEST_MODE)
    assert M.small_live_is_shadow() is True          # live_parity: SHADOW
    _live_mode(monkeypatch)
    assert M.small_live_is_shadow() is False


# ═════════════════════════════════════════════════════════════════════
# §1 MANAGING AN ACTUAL POSITION
# ═════════════════════════════════════════════════════════════════════

def _adm(**k):
    base = dict(mirror_id="em:x", group_id="g", role="EXIT", slug="s",
                intent="ORDER_INTENT_SELL_LONG", qty=2, held=2,
                committed_other=0)
    base.update(k)
    return M.RiskReducingSell(**base)


SELL = {"marketSlug": "s", "intent": "ORDER_INTENT_SELL_LONG",
        "type": "ORDER_TYPE_LIMIT", "quantity": 2,
        "price": {"value": "0.4000", "currency": "USD"},
        "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"}


def test_the_last_line_admits_only_the_admitted_sell_outside_shadow(monkeypatch):
    assert M.risk_reducing_sell_admitted(SELL, _adm()) is False     # SHADOW
    _live_mode(monkeypatch)
    assert M.risk_reducing_sell_admitted(SELL, _adm()) is True
    for params, adm in (
            (dict(SELL, intent="ORDER_INTENT_BUY_LONG"), _adm()),    # a BUY
            (dict(SELL, intent="ORDER_INTENT_BUY_SHORT"),
             _adm(intent="ORDER_INTENT_BUY_SHORT")),                 # a BUY
            (dict(SELL, quantity=3), _adm()),                        # not admitted qty
            (dict(SELL, quantity=3), _adm(qty=3)),                   # > available
            (SELL, _adm(committed_other=1)),                         # > available
            (dict(SELL, marketSlug="other"), _adm()),                # another market
            (dict(SELL, intent="ORDER_INTENT_SELL_SHORT"), _adm()),  # another intent
            (SELL, _adm(role="HEDGE")),                              # not risk-reducing
            (SELL, _adm(role="ENTRY")),
            (SELL, {"role": "EXIT", "qty": 2}),                      # not an admission
            (SELL, None),
            (dict(SELL, quantity=0), _adm(qty=0))):
        assert M.risk_reducing_sell_admitted(params, adm) is False, (params, adm)


def test_the_production_adapter_sends_an_admitted_sell_and_refuses_everything_else(
        monkeypatch):
    rec = []

    class C:
        class orders:
            @staticmethod
            def create(p):
                rec.append(p)
                return {"id": "x"}
    v = M.Venue(client=C)
    v._pace = lambda: None
    with pytest.raises(M.LegacyOriginationRetired):          # SHADOW
        v.place(dict(SELL), risk_reducing_sell=_adm())
    _live_mode(monkeypatch)
    with pytest.raises(M.LegacyOriginationRetired):          # a BUY, no token
        v.place(dict(SELL, intent="ORDER_INTENT_BUY_LONG"),
                risk_reducing_sell=_adm(intent="ORDER_INTENT_BUY_LONG"))
    with pytest.raises(M.LegacyOriginationRetired):          # no admission
        v.place(dict(SELL))
    assert rec == []
    assert v.place(dict(SELL), risk_reducing_sell=_adm()) == {"id": "x"}
    assert rec == [dict(SELL, synchronousExecution=True)]


@pg
async def test_an_exit_of_an_actual_position_reaches_the_venue_outside_shadow(
        monkeypatch):
    """PROBE A1 INVERTED: the paper EXIT of a 2-lot ACTUAL position, with the
    mode flipped off SHADOW in-test, reaches the production adapter's client
    as a SELL_LONG of 2 (no token: the risk-reducing admission) and closes
    the position. Base: REJECTED LegacyOriginationRetired, held 2."""
    conn = await TE._conn()
    try:
        acct, venue, mirror, entry = await _held_position(conn, monkeypatch)
        _live_mode(monkeypatch)
        prod = ProdPlace(venue)
        mirror._venue = prod
        ex = await TE._paper_order(conn, acct, role="EXIT", direction="SELL",
                                   intent="ORDER_INTENT_SELL_LONG", qty=2000,
                                   group=entry["group_id"])
        venue.behaviour = [{"fill": 2}]
        await mirror.tick(conn)
        r = await TE._row(conn, ex["order_id"])
        assert [(p["intent"], p["quantity"]) for p in prod.rec.created] == [
            ("ORDER_INTENT_SELL_LONG", 2)]
        assert r["state"] == "FILLED" and r["error"] is None, r
        assert _j(r["detail"])["risk_reducing_sell"]["admitted"] is True
        assert _j(r["detail"])["risk_reducing_sell"]["available"] == 2
        assert await _held(conn, entry["group_id"]) == 0
    finally:
        await conn.close()


@pg
async def test_in_shadow_the_same_exit_is_refused_exactly_as_before(monkeypatch):
    """THE SHADOW PROOF for the SELL path: claimed, refused by the adapter
    before its client, REJECTED LegacyOriginationRetired, never retried."""
    conn = await TE._conn()
    try:
        acct, venue, mirror, entry = await _held_position(conn, monkeypatch)
        prod = ProdPlace(venue)
        mirror._venue = prod
        ex = await TE._paper_order(conn, acct, role="EXIT", direction="SELL",
                                   intent="ORDER_INTENT_SELL_LONG", qty=2000,
                                   group=entry["group_id"])
        await mirror.tick(conn)
        await mirror.tick(conn)
        r = await TE._row(conn, ex["order_id"])
        assert r["state"] == "REJECTED"
        assert _j(r["error"])["error"] == "LegacyOriginationRetired"
        assert "risk_reducing_sell" not in _j(r["detail"])
        assert prod.rec.created == []
        assert await _held(conn, entry["group_id"]) == 2
    finally:
        await conn.close()


@pg
async def test_a_sell_above_the_uncommitted_inventory_is_excluded_under_the_lock(
        monkeypatch):
    """The exit was planned at 2; before it is claimed another open SELL of
    the group commits 1 of the 2 held. The re-read under the row lock finds
    1 available: EXCLUDED by name, nothing sent. Base: REJECTED by the
    adapter (no admission path at all)."""
    conn = await TE._conn()
    try:
        acct, venue, mirror, entry = await _held_position(conn, monkeypatch)
        _live_mode(monkeypatch)
        prod = ProdPlace(venue)
        mirror._venue = prod
        ex = await TE._paper_order(conn, acct, role="EXIT", direction="SELL",
                                   intent="ORDER_INTENT_SELL_LONG", qty=2000,
                                   group=entry["group_id"])
        await mirror.plan_new(conn, await M.control(conn))
        assert (await TE._row(conn, ex["order_id"]))["live_qty"] == 2
        await conn.execute(
            """INSERT INTO execmirror_orders (mirror_id, group_id, role, strategy,
                   us_market_slug, intent, order_type, tif, wire_price, live_qty,
                   state, venue_order_id, cum_qty)
               VALUES ('em:rival', $1, 'STANDING_PROTECTION',
                       'PINNACLE_COMPLETED_GAME_PAPER', $2, 'ORDER_INTENT_SELL_LONG',
                       'RESTING', 'GTD', 0.70, 1, 'OPEN', 'v-rival', 0)""",
            entry["group_id"], entry["slug"])
        await mirror.submit_planned(conn)
        r = await TE._row(conn, ex["order_id"])
        assert r["state"] == "EXCLUDED", r
        assert r["exclusion"] == "RISK_REDUCING_SELL_ABOVE_UNCOMMITTED_LIVE_INVENTORY"
        d = _j(r["detail"])["risk_reducing_sell"]
        assert (d["held"], d["committed_by_other_sells"], d["available"]) == (2, 1, 1)
        assert prod.rec.created == []
    finally:
        await conn.close()


@pg
async def test_a_buy_always_needs_the_canonical_authorization(monkeypatch):
    """Outside SHADOW too: a HEDGE (a BUY) copied from a paper order reaches
    the production adapter with no token and is refused before its client."""
    conn = await TE._conn()
    try:
        acct, venue, mirror, entry = await _held_position(conn, monkeypatch)
        _live_mode(monkeypatch)
        prod = ProdPlace(venue)
        mirror._venue = prod
        hedge = await TE._paper_order(conn, acct, role="HEDGE",
                                      group=entry["group_id"],
                                      intent="ORDER_INTENT_BUY_SHORT", qty=2000)
        await mirror.tick(conn)
        h = await TE._row(conn, hedge["order_id"])
        assert h["state"] == "REJECTED"
        assert _j(h["error"])["error"] == "LegacyOriginationRetired"
        assert prod.rec.created == []
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §2 DUPLICATE EXPOSURE
# ═════════════════════════════════════════════════════════════════════

async def _refusal(conn, intent_id):
    return await conn.fetchrow(
        "SELECT actual_state, actual_refusal, evidence FROM execution_intents"
        " WHERE intent_id = $1", intent_id)


@pg
async def test_a_second_decision_on_a_market_with_a_working_order_is_refused(
        monkeypatch):
    """PROBE A7 INVERTED: two decisions (different decision ids) on one
    contract; the first rests. The second is refused by name; one venue
    order. Base: two venue orders."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        a = await TE._paper_order(conn, acct, qty=2000, tif="GTD", otype="RESTING")
        b = await TE._paper_order(conn, acct, qty=2000, group=a["group_id"])
        assert a["slug"] == b["slug"] and a["intent_id"] != b["intent_id"]
        await mirror.snapshot(conn, await M.control(conn))
        ra = await mirror.lane._run(conn, a["intent_id"])
        rb = await mirror.lane._run(conn, b["intent_id"])
        assert ra["state"] == "SUBMITTED"
        assert rb["state"] == "REFUSED", rb
        assert rb["refusal"] == "ACTUAL_MARKET_HAS_A_NON_TERMINAL_ORDER"
        assert [p["marketSlug"] for p in venue.placed] == [a["slug"]]
        row = await _refusal(conn, b["intent_id"])
        assert row["actual_refusal"] == "ACTUAL_MARKET_HAS_A_NON_TERMINAL_ORDER"
        # the SAME intent again is still the idempotency key's DUPLICATE
        await conn.execute("UPDATE execution_intents SET actual_state = 'DISPATCHED'"
                           " WHERE intent_id = $1", a["intent_id"])
        again = await mirror.lane._run(conn, a["intent_id"])
        assert again["state"] == "DUPLICATE" and len(venue.placed) == 1
    finally:
        await conn.close()


@pg
async def test_a_second_decision_on_a_held_market_is_refused(monkeypatch):
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        a = await TE._paper_order(conn, acct, qty=2000)
        b = await TE._paper_order(conn, acct, qty=2000, group=a["group_id"])
        await mirror.snapshot(conn, await M.control(conn))
        venue.behaviour = [{"fill": 2}]
        ra = await mirror.lane._run(conn, a["intent_id"])
        assert ra["state"] == "SUBMITTED"
        assert (await TE._row(conn, a["order_id"]))["state"] == "FILLED"
        rb = await mirror.lane._run(conn, b["intent_id"])
        assert rb["state"] == "REFUSED" and rb["refusal"] == "ACTUAL_MARKET_ALREADY_HELD"
        assert rb["net_held"] == "2.000000" or Decimal(rb["net_held"]) == 2
        assert len(venue.placed) == 1
    finally:
        await conn.close()


@pg
async def test_two_lanes_racing_on_one_market_make_one_claim(monkeypatch):
    """Two decisions on one market dispatched concurrently on two
    connections: the per-market lock in the claim transaction serializes
    them; the second finds the first's claim. Base: two venue orders."""
    conn = await TE._conn()
    conn2 = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        a = await TE._paper_order(conn, acct, qty=2000, tif="GTD", otype="RESTING")
        b = await TE._paper_order(conn, acct, qty=2000, tif="GTD", otype="RESTING",
                                  group=a["group_id"])
        await mirror.snapshot(conn, await M.control(conn))
        lane2 = EI.ActualLane(None, mirror)
        got = await asyncio.gather(mirror.lane._run(conn, a["intent_id"]),
                                   lane2._run(conn2, b["intent_id"]))
        states = sorted(g["state"] for g in got)
        assert states == ["REFUSED", "SUBMITTED"], got
        assert [g for g in got if g["state"] == "REFUSED"][0]["refusal"] == \
            "ACTUAL_MARKET_HAS_A_NON_TERMINAL_ORDER"
        assert len(venue.placed) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM execmirror_orders WHERE us_market_slug = $1",
            a["slug"]) == 1
    finally:
        await conn2.close()
        await conn.close()


@pg
async def test_the_aggregate_cap_counts_open_and_held_notional(monkeypatch):
    """max_open_notional_usd 2.00 (migration 366), per-order cap 25: the
    first order (3 @ 0.55 = $1.65, resting) is within it; a second on
    ANOTHER market ($1.65 more) is refused by name, nothing sent."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        await conn.execute("UPDATE execmirror_control SET max_open_notional_usd = 2.00"
                           " WHERE id = 1")
        a = await TE._paper_order(conn, acct, qty=3000, tif="GTD", otype="RESTING")
        b = await TE._paper_order(conn, acct, qty=3000)
        assert a["slug"] != b["slug"]
        await mirror.snapshot(conn, await M.control(conn))
        assert (await mirror.lane._run(conn, a["intent_id"]))["state"] == "SUBMITTED"
        rb = await mirror.lane._run(conn, b["intent_id"])
        assert rb["state"] == "REFUSED"
        assert rb["refusal"] == "ACTUAL_OPEN_AND_HELD_NOTIONAL_ABOVE_CAP"
        assert (Decimal(rb["open_usd"]), Decimal(rb["held_usd"]),
                Decimal(rb["order_cost_usd"]), Decimal(rb["cap_usd"])) == (
            Decimal("1.65"), Decimal(0), Decimal("1.65"), Decimal("2.00"))
        assert rb["cap_basis"] == "execmirror_control.max_open_notional_usd"
        assert len(venue.placed) == 1
    finally:
        await conn.close()


@pg
async def test_with_no_aggregate_cap_set_the_cap_is_the_per_order_cap(monkeypatch):
    """max_open_notional_usd NULL (its default): fail-closed small, equal to
    max_order_usd. A filled $1.10 holding plus a second $1.10 order on
    another market is $2.20 > $2.00: refused. Base: both sent."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch, cap=2)
        assert (await M.control(conn))["max_open_notional_usd"] is None
        a = await TE._paper_order(conn, acct, qty=2000)
        b = await TE._paper_order(conn, acct, qty=2000)
        await mirror.snapshot(conn, await M.control(conn))
        venue.behaviour = [{"fill": 2}]
        assert (await mirror.lane._run(conn, a["intent_id"]))["state"] == "SUBMITTED"
        rb = await mirror.lane._run(conn, b["intent_id"])
        assert rb["state"] == "REFUSED"
        assert rb["refusal"] == "ACTUAL_OPEN_AND_HELD_NOTIONAL_ABOVE_CAP"
        assert Decimal(rb["held_usd"]) == Decimal("1.10")
        assert Decimal(rb["cap_usd"]) == Decimal("2.00")
        assert rb["cap_basis"].startswith("DEFAULT_EQUALS_max_order_usd")
        assert len(venue.placed) == 1
    finally:
        await conn.close()


@pg
async def test_two_dispatches_on_two_markets_at_once_never_exceed_the_aggregate_cap(
        monkeypatch):
    """REVIEW (rc6.2 pmus-exec, item 2): the claim took only its market's
    lock before reading the account's open + held notional, so two decisions
    on DIFFERENT markets dispatched at once (dispatch(): one task per intent,
    each on its own pool connection, as in production) both read 0, both
    passed the cap and both were sent -- $2.20 open against a $2.00 cap
    (NULL -> max_order_usd 2.00: one order's worth). Both lanes are held at
    the notional read until the other arrives (or 2 s pass): on the base both
    arrive and both are sent; now the account-wide claim lock
    (ACCOUNT_LOCK_CLASS, taken after the market's) makes the second wait,
    read the first's claim and refuse by name."""
    import asyncpg
    conn = await TE._conn()
    pool = await asyncpg.create_pool(TE.DSN, min_size=2, max_size=4)
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch, cap=2)
        assert (await M.control(conn))["max_open_notional_usd"] is None
        a = await TE._paper_order(conn, acct, qty=2000, tif="GTD", otype="RESTING")
        b = await TE._paper_order(conn, acct, qty=2000, tif="GTD", otype="RESTING")
        assert a["slug"] != b["slug"]
        await mirror.snapshot(conn, await M.control(conn))
        real = M.open_and_held_notional
        arrived: list = []
        both = asyncio.Event()

        async def held_read(c):
            out = await real(c)
            arrived.append(c)
            if len(arrived) >= 2:
                both.set()
            try:
                await asyncio.wait_for(both.wait(), 2.0)
            except asyncio.TimeoutError:
                pass                    # the other lane waits on the claim lock
            return out
        monkeypatch.setattr(M, "open_and_held_notional", held_read)

        async def get_pool():
            return pool
        monkeypatch.setattr(EI, "LANE", EI.ActualLane(get_pool, mirror))
        monkeypatch.setattr(EI, "_TASKS", set())
        for po in (a, b):
            it = dict(await conn.fetchrow(
                "SELECT * FROM execution_intents WHERE intent_id = $1",
                po["intent_id"]), created=True)
            assert EI.dispatch(it) is True
        got = await asyncio.wait_for(asyncio.gather(*list(EI._TASKS)), 30)
        outcome = sorted((g["state"], g.get("refusal")) for g in got)
        assert outcome == [("REFUSED", "ACTUAL_OPEN_AND_HELD_NOTIONAL_ABOVE_CAP"),
                           ("SUBMITTED", None)], got
        refused = [g for g in got if g["state"] == "REFUSED"][0]
        assert (Decimal(refused["open_usd"]), Decimal(refused["order_cost_usd"]),
                Decimal(refused["cap_usd"])) == (
            Decimal("1.10"), Decimal("1.10"), Decimal("2.00"))
        assert len(venue.placed) == 1
        nt = await real(conn)
        assert nt["open_usd"] + nt["held_usd"] == Decimal("1.10")
        assert await conn.fetchval("SELECT count(*) FROM execmirror_orders") == 1
    finally:
        await pool.close()
        await conn.close()


def test_the_claim_takes_the_market_lock_then_the_account_lock():
    """The lock order every claimer follows (market, then account), and the
    account lock's key space: the two-key advisory form, a class of its own
    (never the market locks' class, never a single-key session lock)."""
    import inspect
    import re
    src = inspect.getsource(EI.ActualLane._run)
    m_slug = re.search(r'"SELECT pg_advisory_xact_lock\(\$1, hashtext\(\$2\)\)",'
                       r'\s*SLUG_LOCK_CLASS, slug\)', src)
    m_acct = re.search(r'"SELECT pg_advisory_xact_lock\(\$1, 0\)",'
                       r'\s*ACCOUNT_LOCK_CLASS\)', src)
    assert m_slug and m_acct
    i_slug, i_acct = m_slug.start(), m_acct.start()
    i_read = src.index("M.open_and_held_notional(conn)")
    i_ins = src.index("INSERT INTO execmirror_orders")
    assert i_slug < i_acct < i_read < i_ins
    assert EI.ACCOUNT_LOCK_CLASS != EI.SLUG_LOCK_CLASS
    assert EI.ACCOUNT_LOCK_CLASS != M.LOCK_KEY


# ═════════════════════════════════════════════════════════════════════
# §3 CANCELLATION
# ═════════════════════════════════════════════════════════════════════

async def _resting(conn, monkeypatch):
    acct, venue, mirror = await TE._setup(conn, monkeypatch)
    po = await TE._paper_order(conn, acct, tif="GTD", otype="RESTING")
    await mirror.tick(conn)
    r = await TE._row(conn, po["order_id"])
    assert r["state"] == "OPEN"
    t = [time.time()]
    mirror._now = lambda: t[0]
    return venue, mirror, po, r, t


@pg
async def test_a_cancel_the_venue_did_not_accept_is_named_and_resent(monkeypatch):
    """PROBE A4 INVERTED: a 503 on the cancel is NOT recorded as
    requested-and-accepted (CANCEL_NOT_ACCEPTED, accepted false); while the
    venue's record still shows the order working, the cancel is re-sent
    after CANCEL_RESEND_AFTER_S, as an event; the order then ends.
    Base: CANCEL_REQUESTED recorded and never re-sent."""
    conn = await TE._conn()
    try:
        venue, mirror, po, r, t = await _resting(conn, monkeypatch)
        real_cancel = venue.cancel

        def flaky(vid, slug):
            raise TE._Err(503, "unavailable")
        venue.cancel = flaky
        await mirror._cancel(conn, r, "AUDIT_PROBE")
        mid = r["mirror_id"]
        assert await _events(conn, "CANCEL_NOT_ACCEPTED", mid)
        assert await _events(conn, "CANCEL_REQUESTED", mid) == []
        row = await TE._row(conn, po["order_id"])
        c = _j(row["detail"])["cancel"]
        assert (c["accepted"], c["last_outcome"], c["attempts"]) == (
            False, "NOT_ACCEPTED", 1)
        venue.cancel = real_cancel
        t[0] += M.CANCEL_RESEND_AFTER_S - 1
        await mirror.poll(conn)
        assert venue.cancelled == []                    # not before the back-off
        t[0] += 2
        await mirror.poll(conn)
        assert venue.cancelled == [r["venue_order_id"]]
        (ev,) = await _events(conn, "CANCEL_RESENT", mid)
        assert (ev["attempt"], ev["outcome"], ev["accepted"]) == (2, "SENT", True)
        await mirror.poll(conn)
        assert (await TE._row(conn, po["order_id"]))["state"] == "CANCELLED"
    finally:
        await conn.close()


@pg
async def test_cancel_resends_are_bounded_with_backoff_and_named_when_spent(
        monkeypatch):
    conn = await TE._conn()
    try:
        venue, mirror, po, r, t = await _resting(conn, monkeypatch)
        sent = []

        def down(vid, slug):
            sent.append(t[0])
            raise TE._Err(503, "unavailable")
        venue.cancel = down
        t0 = t[0]
        await mirror._cancel(conn, r, "AUDIT_PROBE")    # attempt 1
        for dt_s in (5, 10, 15, 29, 30, 69, 70, 149, 150, 400, 2000):
            t[0] = t0 + dt_s
            await mirror.poll(conn)
        # attempts at 0, then >=10, >=20, >=40, >=80 s after the previous
        assert [round(s - t0) for s in sent] == [0, 10, 30, 70, 150]
        assert len(sent) == M.CANCEL_MAX_ATTEMPTS
        assert len(await _events(conn, "CANCEL_RESENT", r["mirror_id"])) == 4
        (ex,) = await _events(conn, "CANCEL_RESEND_EXHAUSTED", r["mirror_id"])
        assert ex["code"] == "CANCEL_RESEND_EXHAUSTED" and ex["attempts"] == 5
        row = await TE._row(conn, po["order_id"])
        assert row["state"] == "CANCEL_REQUESTED"       # still read, still owned
        assert _j(row["detail"])["cancel"]["accepted"] is False
    finally:
        await conn.close()


@pg
async def test_a_cancel_that_races_a_fill_is_still_settled_by_the_record(monkeypatch):
    conn = await TE._conn()
    try:
        venue, mirror, po, r, t = await _resting(conn, monkeypatch)
        venue.race.add(r["venue_order_id"])
        await mirror._cancel(conn, r, "ACTUAL_CANCEL")
        assert await _events(conn, "CANCEL_FAILED", r["mirror_id"])
        assert await _events(conn, "CANCEL_REQUESTED", r["mirror_id"])
        row = await TE._row(conn, po["order_id"])
        assert row["state"] == "FILLED" and row["cum_qty"] == 2
        assert _j(row["detail"])["cancel"]["last_outcome"] == "RACE"
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §4 RECONCILIATION
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_the_snapshot_compares_signed_positions(monkeypatch):
    """Our venue fills hold a LONG of 3; the venue states a SHORT of 3. abs()
    called that reconciled (base); signed, it is a difference."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        po = await TE._paper_order(conn, acct, qty=3000)
        venue.behaviour = [{"fill": 3}]
        await mirror.tick(conn)
        venue.positions = lambda: {po["slug"]: {"netPosition": "-3",
                                                "expired": False}}
        rec = await mirror.snapshot(conn, await M.control(conn))
        assert rec["reconciled"] is False
        assert rec["differences"][po["slug"]] == {
            "mirror_fills_net": 3, "baseline": 0, "venue": -3, "signed": True}
    finally:
        await conn.close()


@pg
async def test_a_short_we_hold_reconciles_signed(monkeypatch):
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        po = await TE._paper_order(conn, acct, qty=3000,
                                   intent="ORDER_INTENT_BUY_SHORT", wire=0.40)
        venue.behaviour = [{"fill": 3}]
        await mirror.tick(conn)
        assert venue.positions()[po["slug"]]["netPosition"] == "-3"
        rec = await mirror.snapshot(conn, await M.control(conn))
        assert rec == {"reconciled": True, "differences": {}}
    finally:
        await conn.close()


@pg
async def test_the_lane_refuses_while_the_newest_snapshot_disagrees(monkeypatch):
    """PROBE A6 INVERTED: a venue position our fills do not explain makes
    the newest snapshot unreconciled; the ACTUAL lane refuses new exposure by
    name. Base: SUBMITTED."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        vid = venue._new({"marketSlug": "mlb-foreign-0001",
                          "intent": "ORDER_INTENT_BUY_LONG", "quantity": 5,
                          "price": {"value": "0.50"}, "tif": "x"})
        venue.fill(vid, 5)
        rec = await mirror.snapshot(conn, await M.control(conn))
        assert rec["reconciled"] is False
        po = await TE._paper_order(conn, acct, qty=2000)
        res = await mirror.lane._run(conn, po["intent_id"])
        assert res["state"] == "REFUSED", res
        assert res["refusal"] == "VENUE_POSITION_DISAGREES_WITH_MIRROR_FILLS"
        assert "mlb-foreign-0001" in res["differences"]
        assert venue.placed == []
    finally:
        await conn.close()


async def _snapshot_row(conn, age_s: float):
    """An account snapshot as Mirror.snapshot writes it (USD buying power
    100, nothing held, reconciled), `age_s` old by the database clock."""
    await conn.execute(
        """INSERT INTO execmirror_snapshots (at, account_fingerprint, balances,
             positions, open_orders, reconciliation)
           VALUES (now() - make_interval(secs => $1), 'fp',
                   '[{"currency": "USD", "buyingPower": 100,
                      "currentBalance": 100}]'::jsonb,
                   '[]'::jsonb, 0, '{"reconciled": true, "differences": {}}'::jsonb)""",
        float(age_s))


@pg
async def test_audrey_never_reconciles_from_a_snapshot_older_than_its_bound(
        monkeypatch):
    """A paper-only group with the newest snapshot 200 s old: STALE (what she
    would have said, NOT_MIRRORED, kept on the chain); with a current one:
    NOT_MIRRORED. Base: NOT_MIRRORED from the old snapshot."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        po = await TE._paper_order(conn, acct, qty=2702,
                                   strategy="PINNACLE_EXPLORATION_PAPER",
                                   policy_version="PINNACLE_EXPLORATION_PAPER_V3")
        await _snapshot_row(conn, 200)                  # > the 180 s bound
        await mirror.audrey_reconcile(conn)
        rec = await conn.fetchrow("SELECT * FROM smalllive_reconciliations"
                                  " WHERE group_id = $1", po["group_id"])
        assert rec["status"] == "STALE", rec
        chain = _j(rec["chain"])
        assert chain["stale"]["would_be"] == "NOT_MIRRORED"
        assert chain["stale"]["code"] == "AUDREY_ACCOUNT_SNAPSHOT_NOT_CURRENT"
        assert chain["stale"]["account_snapshot_age_s"] > 180
        assert await _events(conn, "AUDREY_RECONCILIATION_STALE")
        await _snapshot_row(conn, 5)
        await mirror.audrey_reconcile(conn)
        rec = await conn.fetchrow("SELECT * FROM smalllive_reconciliations"
                                  " WHERE group_id = $1", po["group_id"])
        assert rec["status"] == "NOT_MIRRORED"
        assert _j(rec["chain"])["stale"] is None
    finally:
        await conn.close()


@pg
async def test_audrey_never_calls_a_live_chain_matched_without_a_current_snapshot(
        monkeypatch):
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        # a clean chain (tests/test_execmirror's): decision -> paper order ->
        # paper fill -> actual order -> venue fill
        grp = "paper_group_%s" % uuid.uuid4().hex[:8]
        did = await TE._decision(conn, acct, "mlb-test-%s" % grp[-4:])
        po = await TE._paper_order(conn, acct, qty=2702, group=grp, decision_id=did)
        await TE._paper_fill(conn, acct, po, qty=2702)
        venue.behaviour = [{"fill": 3}]
        await mirror.tick(conn)
        rec = await conn.fetchrow("SELECT * FROM smalllive_reconciliations"
                                  " WHERE group_id = $1", po["group_id"])
        assert rec["status"] == "MATCHED"               # current snapshot
        await conn.execute("UPDATE execmirror_snapshots SET at = at - interval '10 minutes'")
        await mirror.audrey_reconcile(conn)
        rec = await conn.fetchrow("SELECT * FROM smalllive_reconciliations"
                                  " WHERE group_id = $1", po["group_id"])
        assert _j(rec["discrepancies"]) == []
        assert rec["status"] == "STALE", rec
        assert _j(rec["chain"])["stale"]["would_be"] == "MATCHED"
    finally:
        await conn.close()


@pg
async def test_the_snapshot_while_stopped_stays_an_owner_decision(monkeypatch):
    """NOT CHANGED HERE (redteam/controls.OWNER_SNAPSHOT_WHILE_STOPPED): a
    stopped lane whose stop is done takes no account snapshot and builds no
    venue client; only the RUNNING lane writes execmirror_snapshots."""
    from sportsassets.redteam import controls as C
    assert "today only the RUNNING lane writes it" in C.OWNER_SNAPSHOT_WHILE_STOPPED
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        await conn.execute("UPDATE execmirror_control SET stopped = true,"
                           " stop_done_at = now() WHERE id = 1")

        def no_venue():
            raise AssertionError("a stopped lane must not build a venue")
        mirror._venue, mirror._venue_factory = None, no_venue
        out = await M.Mirror.tick(mirror, conn)
        assert out["state"] == "STOPPED"
        assert await conn.fetchval("SELECT count(*) FROM execmirror_snapshots") == 0
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §5 THE KILL SWITCH
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_stop_completed_during_the_lanes_checks_stops_its_claim(monkeypatch):
    """PROBE A2 INVERTED: the emergency stop lands and completes while the
    lane is between its control read (step 1) and its claim (step 5). The
    claim's INSERT is conditional on the control: no row, nothing sent,
    refused ACTUAL_LANE_STOPPED. Base: SUBMITTED after stop_done_at."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        po = await TE._paper_order(conn, acct, qty=2000)
        await mirror.snapshot(conn, await M.control(conn))

        async def stop_then_authorize(c, **kw):
            await c.execute("UPDATE execmirror_control SET stopped = true WHERE id = 1")
            out = await mirror.emergency_stop(c, await M.control(c))
            assert out["state"] == "STOPPED"
            return {"ok": True, "refusal": None, "token": None, "detail": {}}
        monkeypatch.setattr(LP, "authorize_live_exposure", stop_then_authorize)
        res = await mirror.lane._run(conn, po["intent_id"])
        assert res["state"] == "REFUSED" and res["refusal"] == "ACTUAL_LANE_STOPPED", res
        assert res.get("at") == "CLAIM"
        assert venue.placed == []
        assert await conn.fetchval("SELECT count(*) FROM execmirror_orders") == 0
    finally:
        await conn.close()


@pg
async def test_a_stop_after_the_claim_stops_the_send(monkeypatch):
    """The stop commits after the claim and before the send: the control is
    re-read immediately before place; the claimed row is EXCLUDED (nothing
    sent) and the intent refused. Base: sent."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        po = await TE._paper_order(conn, acct, qty=2000)
        await mirror.snapshot(conn, await M.control(conn))
        real = M.control

        async def control(c):
            if await c.fetchval(
                    "SELECT 1 FROM execmirror_orders WHERE execution_intent_id = $1"
                    " AND state = 'SUBMITTING'", po["intent_id"]):
                await c.execute("UPDATE execmirror_control SET stopped = true"
                                " WHERE id = 1")
            return await real(c)
        monkeypatch.setattr(M, "control", control)
        res = await mirror.lane._run(conn, po["intent_id"])
        assert res["state"] == "REFUSED" and res["refusal"] == "ACTUAL_LANE_STOPPED", res
        assert res["at"] == "IMMEDIATELY_BEFORE_PLACE"
        assert venue.placed == []
        r = await TE._row(conn, po["order_id"])
        assert r["state"] == "EXCLUDED" and r["exclusion"] == "ACTUAL_LANE_STOPPED"
        it = await _refusal(conn, po["intent_id"])
        assert (it["actual_state"], it["actual_refusal"]) == (
            "REFUSED", "ACTUAL_LANE_STOPPED")
    finally:
        await conn.close()


@pg
async def test_a_stop_in_flight_on_another_session_is_never_raced_by_a_claim(
        monkeypatch):
    """The operator's stop is an uncommitted UPDATE on another session when
    the lane reaches its claim: the claim waits on the share-locked control
    row and, once the stop commits, writes nothing. Base: the claim does not
    read the control at all, and the order is sent."""
    conn = await TE._conn()
    other = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        po = await TE._paper_order(conn, acct, qty=2000)
        await mirror.snapshot(conn, await M.control(conn))
        tx = other.transaction()
        await tx.start()
        await other.execute("UPDATE execmirror_control SET stopped = true WHERE id = 1")
        task = asyncio.create_task(mirror.lane._run(conn, po["intent_id"]))
        pid = conn.get_server_pid()
        waited = False
        for _ in range(100):
            if task.done():
                break
            if await other.fetchval(
                    "SELECT wait_event_type = 'Lock' FROM pg_stat_activity"
                    " WHERE pid = $1", pid):
                waited = True
                break
            await asyncio.sleep(0.05)
        await tx.commit()
        res = await asyncio.wait_for(task, 30)
        assert waited, "the claim did not wait for the stop in flight"
        assert res["state"] == "REFUSED" and res["refusal"] == "ACTUAL_LANE_STOPPED", res
        assert venue.placed == []
    finally:
        await other.close()
        await conn.close()


@pg
async def test_a_failed_cancel_all_leaves_the_stop_incomplete_and_retried(
        monkeypatch):
    """PROBE A5 INVERTED: the venue refuses the cancel-all: stop_done_at stays
    NULL, EMERGENCY_STOP_INCOMPLETE is recorded, the pass is retried after
    STOP_RETRY_S and completes once the cancel-all succeeds. Base: STOPPED,
    stop_done_at set beside STOP_CANCEL_ALL_FAILED."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        t = [time.time()]
        mirror._now = lambda: t[0]
        calls = []

        def boom():
            calls.append(t[0])
            raise TE._Err(503, "unavailable")
        venue.cancel_all = boom
        await conn.execute("UPDATE execmirror_control SET stopped = true WHERE id = 1")
        out = await mirror.tick(conn)
        assert out["state"] == "STOP_INCOMPLETE", out
        assert out["code"] == "EMERGENCY_STOP_INCOMPLETE" and out["cancel_all"] == "FAILED"
        assert (await M.control(conn))["stop_done_at"] is None
        (ev,) = await _events(conn, "EMERGENCY_STOP_INCOMPLETE")
        assert ev["cancel_all"] == "FAILED"
        t[0] += M.STOP_RETRY_S / 2
        out = await mirror.tick(conn)
        assert out["state"] == "STOP_INCOMPLETE" and len(calls) == 1   # not yet
        venue.cancel_all = lambda: {"canceledOrderIds": []}
        t[0] += M.STOP_RETRY_S
        out = await mirror.tick(conn)
        assert out["state"] == "STOPPED", out
        assert (await M.control(conn))["stop_done_at"] is not None
        assert len(await _events(conn, "EMERGENCY_STOP_DONE")) == 1
        assert len(await _events(conn, "EMERGENCY_STOP_INCOMPLETE")) == 1
    finally:
        await conn.close()


@pg
async def test_the_stop_reconciles_a_lost_response_and_cancels_what_it_finds(
        monkeypatch):
    """A resting order whose create response was lost (UNKNOWN). The stop
    decides it from the venue's open orders (recovery, never a resend),
    cancels it, and only then is done. Base: STOPPED with the row UNKNOWN and
    the order still resting at the venue."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        po = await TE._paper_order(conn, acct, qty=2000, tif="GTD", otype="RESTING")
        await mirror.snapshot(conn, await M.control(conn))
        venue.behaviour = [{"raise": 504, "create_anyway": True}]
        await mirror.lane._run(conn, po["intent_id"])
        assert (await TE._row(conn, po["order_id"]))["state"] == "UNKNOWN"
        await conn.execute("UPDATE execmirror_control SET stopped = true WHERE id = 1")
        out = await mirror.tick(conn)
        r = await TE._row(conn, po["order_id"])
        assert r["state"] == "CANCELLED" and venue.cancelled == ["v1"], r
        assert len(venue.placed) == 1                   # never re-sent
        assert out["state"] == "STOPPED", out
        assert out["recovered"] == 1 and out["cancelled"] == 1
        assert (await M.control(conn))["stop_done_at"] is not None
    finally:
        await conn.close()


@pg
async def test_an_unknown_submission_the_venue_cannot_decide_keeps_the_stop_incomplete(
        monkeypatch):
    """PROBE A3: an UNKNOWN IOC that traded but cannot be attributed from the
    trade log is not a stopped lane: STOP_INCOMPLETE names it (remaining
    UNKNOWN 1). Base: STOPPED."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        po = await TE._paper_order(conn, acct, qty=3000)
        await mirror.snapshot(conn, await M.control(conn))
        venue.behaviour = [{"raise": 504, "create_anyway": True}]
        await mirror.lane._run(conn, po["intent_id"])
        venue.fill("v1", 3)
        r = await TE._row(conn, po["order_id"])
        await conn.execute("UPDATE execmirror_orders SET submit_started_at ="
                           " now() - interval '10 minutes' WHERE mirror_id = $1",
                           r["mirror_id"])
        await conn.execute("UPDATE execmirror_control SET stopped = true WHERE id = 1")
        out = await mirror.tick(conn)
        assert out["state"] == "STOP_INCOMPLETE", out
        assert out["remaining"] == {"UNKNOWN": 1}
        assert (await M.control(conn))["stop_done_at"] is None
    finally:
        await conn.close()


@pg
async def test_a_stop_is_not_done_while_an_unaccepted_cancel_leaves_the_order_working(
        monkeypatch):
    """REVIEW (rc6.2 pmus-exec, items 5 and 3): the per-order cancel answered
    503 and the cancel-all answered 2xx but cancelled nothing. The base moved
    the row to CANCEL_REQUESTED (outside the blocking states), never re-read
    it after the cancel-all, wrote stop_done_at while the venue record said
    ORDER_STATE_NEW, and -- the stop being done -- never re-sent the cancel.
    Now a CANCEL_REQUESTED row is re-read after the cancel-all and blocks the
    stop while its record is not terminal (STOP_INCOMPLETE, remaining
    CANCEL_REQUESTED 1); the incomplete-stop loop re-sends its cancel on the
    bounded back-off (10 s, then doubling), each a CANCEL_RESENT event, and
    the stop is done only once the venue's record says the order ended."""
    conn = await TE._conn()
    try:
        venue, mirror, po, r, t = await _resting(conn, monkeypatch)
        t0 = t[0]
        sent: list = []
        real_cancel = venue.cancel

        def down(vid, slug):
            sent.append(round(t[0] - t0))
            raise TE._Err(503, "unavailable")
        venue.cancel = down
        ids: list = []
        venue.cancel_all = lambda: (ids.append(1), {"canceledOrderIds": []})[1]
        await conn.execute("UPDATE execmirror_control SET stopped = true WHERE id = 1")
        out = await mirror.tick(conn)
        assert out["state"] == "STOP_INCOMPLETE", out
        assert out["cancel_all"] == "OK" and out["remaining"] == {"CANCEL_REQUESTED": 1}
        assert (await M.control(conn))["stop_done_at"] is None
        assert await _events(conn, "EMERGENCY_STOP_DONE") == []
        row = await TE._row(conn, po["order_id"])
        assert (row["state"], row["venue_state"]) == ("CANCEL_REQUESTED", "ORDER_STATE_NEW")
        assert _j(row["detail"])["cancel"]["accepted"] is False
        # the incomplete stop is retried every STOP_RETRY_S; the cancel is
        # re-sent only when its back-off has passed (10 s, then 20 s)
        for _ in range(3):
            t[0] += M.STOP_RETRY_S
            out = await mirror.tick(conn)
            assert out["state"] == "STOP_INCOMPLETE", out
        assert sent == [0, 10, 30]
        assert [e["attempt"] for e in await _events(conn, "CANCEL_RESENT",
                                                    r["mirror_id"])] == [2, 3]
        assert (await M.control(conn))["stop_done_at"] is None
        venue.cancel = real_cancel                      # the venue recovers
        while venue.orders[r["venue_order_id"]]["state"] == "ORDER_STATE_NEW":
            t[0] += M.STOP_RETRY_S
            out = await mirror.tick(conn)
            assert t[0] - t0 <= 200, out
        assert round(t[0] - t0) == 70                   # 30 + 40: the 4th send
        assert out["state"] == "STOPPED", out
        assert (await TE._row(conn, po["order_id"]))["state"] == "CANCELLED"
        assert (await M.control(conn))["stop_done_at"] is not None
        assert len(await _events(conn, "EMERGENCY_STOP_DONE")) == 1
        assert len(await _events(conn, "EMERGENCY_STOP_INCOMPLETE")) == 1
        assert len(ids) == 8                            # cancel-all every pass
    finally:
        await conn.close()


@pg
async def test_a_stop_with_an_accepted_cancel_still_working_waits_for_the_record(
        monkeypatch):
    """An accepted cancel (2xx) whose order the venue still shows working
    after the cancel-all is not a done stop either: the record is read again
    on the next pass and the stop completes when it says the order ended."""
    conn = await TE._conn()
    try:
        venue, mirror, po, r, t = await _resting(conn, monkeypatch)
        vid = r["venue_order_id"]
        venue.cancel = lambda v, s: venue.cancelled.append(v)   # accepted, pending
        await conn.execute("UPDATE execmirror_control SET stopped = true WHERE id = 1")
        out = await mirror.tick(conn)
        assert out["state"] == "STOP_INCOMPLETE", out
        assert out["remaining"] == {"CANCEL_REQUESTED": 1}
        assert _j((await TE._row(conn, po["order_id"]))["detail"])["cancel"][
            "accepted"] is True
        venue.orders[vid]["state"] = "ORDER_STATE_CANCELED"     # the venue acts
        t[0] += M.STOP_RETRY_S
        out = await mirror.tick(conn)
        assert out["state"] == "STOPPED", out
        assert (await TE._row(conn, po["order_id"]))["state"] == "CANCELLED"
        assert venue.cancelled == [vid]                 # no resend was due
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §6 THE WIRE: A SUCCESSFUL ORDER ON THE ACTIVATION PATH
# ═════════════════════════════════════════════════════════════════════

nacl_signing = pytest.importorskip("nacl.signing")
sdk = pytest.importorskip("polymarket_us")


class WireVenue:
    """A scripted Polymarket US retail API behind httpx.MockTransport: it
    records every request that reaches the socket and checks each one's
    authentication headers against the test key (Ed25519 over
    timestamp + method + path, docs.polymarket.us/api/authentication)."""

    def __init__(self, verify_key, key_id):
        self.vk, self.kid = verify_key, key_id
        self.seen: list = []
        self.bodies: list = []
        self.auth_failures: list = []
        self.lock = threading.Lock()
        self.orders: dict = {}
        self.fills: list = []           # per create: contracts executed
        self.first_read_working: set = set()
        self.positions_net: list = []   # per positions read: {slug: net}
        self.n = 0

    def _check_auth(self, request) -> None:
        h = request.headers
        ts, sig = h.get("X-PM-Timestamp"), h.get("X-PM-Signature")
        why = None
        if h.get("X-PM-Access-Key") != self.kid:
            why = "access key"
        elif not (ts and ts.isdigit()) or abs(int(ts) - time.time() * 1000) > 30_000:
            why = "timestamp"
        else:
            try:
                self.vk.verify(("%s%s%s" % (ts, request.method,
                                            request.url.path)).encode(),
                               base64.b64decode(sig))
            except Exception as exc:                      # noqa: BLE001
                why = "signature: %s" % type(exc).__name__
        if why:
            self.auth_failures.append((request.method, request.url.path, why))

    def handler(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.seen.append((request.method, request.url.path))
            body = json.loads(request.content) if request.content else None
            self.bodies.append(body)
            assert request.url.scheme == "https"
            assert request.url.host == "api.polymarket.us"
            self._check_auth(request)
            m, p = request.method, request.url.path
            if (m, p) == ("POST", "/v1/orders"):
                self.n += 1
                oid = "ord-%d" % self.n
                q = int(body["quantity"])
                k = self.fills.pop(0)
                self.orders[oid] = {
                    "id": oid, "marketSlug": body["marketSlug"],
                    "intent": body["intent"], "quantity": q,
                    "price": body["price"], "tif": body["tif"],
                    "cumQuantity": str(k), "avgPx": body["price"],
                    "commissionNotionalTotalCollected": {
                        "value": "%.4f" % (0.01 * k)},
                    "state": ("ORDER_STATE_FILLED" if k >= q
                              else "ORDER_STATE_PARTIALLY_FILLED"
                              if oid in self.first_read_working or k
                              else "ORDER_STATE_CANCELED")}
                return httpx.Response(200, json={
                    "id": oid, "executions": [{"lastShares": str(k)}] if k else []},
                    request=request)
            if m == "GET" and p.startswith("/v1/order/"):
                oid = p.rsplit("/", 1)[1]
                return httpx.Response(200, json={"order": dict(self.orders[oid])},
                                      request=request)
            if m == "POST" and p.startswith("/v1/order/") and p.endswith("/cancel"):
                oid = p.split("/")[3]
                o = self.orders[oid]
                if o["state"] in ("ORDER_STATE_NEW", "ORDER_STATE_PARTIALLY_FILLED"):
                    o["state"] = "ORDER_STATE_CANCELED"
                return httpx.Response(200, json={}, request=request)
            if (m, p) == ("GET", "/v1/account/balances"):
                return httpx.Response(200, json={"balances": [
                    {"currency": "USD", "currentBalance": 100,
                     "buyingPower": 100}]}, request=request)
            if (m, p) == ("GET", "/v1/portfolio/positions"):
                net = self.positions_net.pop(0) if self.positions_net else {}
                return httpx.Response(200, json={
                    "positions": {s: {"netPosition": str(v), "expired": False,
                                      "cost": {"value": "0.50"}}
                                  for s, v in net.items()},
                    "eof": True}, request=request)
            if (m, p) == ("GET", "/v1/orders/open"):
                return httpx.Response(200, json={"orders": []}, request=request)
            return httpx.Response(418, json={"message": "NOT_SCRIPTED"},
                                  request=request)


async def _wire_world(conn, monkeypatch):
    """The production adapter, as execmirror.run builds it (M.Venue() from
    the PMUS_EXECMIRROR environment names, the pinned SDK, retries off),
    over a mock socket; a throwaway Ed25519 key generated here; the lane's
    world from tests/test_execmirror (per-order cap $1.00, scale 1,000)."""
    acct, fake, mirror = await TE._setup(conn, monkeypatch, cap=1)
    sk = nacl_signing.SigningKey.generate()
    kid = str(uuid.uuid4())
    monkeypatch.setenv(EP.KEY_ID_ENV, kid)
    monkeypatch.setenv(EP.SECRET_ENV, base64.b64encode(bytes(sk)).decode())
    await conn.execute("UPDATE execmirror_control SET account_fingerprint = $1"
                       " WHERE id = 1", EP.fingerprint(kid))
    monkeypatch.setattr(M, "PACE_S", 0.0)
    VP._penalty_until = 0.0
    v = M.Venue()
    assert sdk.__name__ == type(v._c).__module__.split(".")[0]
    assert v._c.max_retries == 0
    wire = WireVenue(sk.verify_key, kid)
    v._c._http = httpx.Client(transport=httpx.MockTransport(wire.handler))
    mirror._venue = v
    return acct, mirror, wire


@pg
async def test_a_successful_actual_order_on_the_wire_outside_shadow(monkeypatch):
    conn = await TE._conn()
    try:
        acct, mirror, wire = await _wire_world(conn, monkeypatch)
        _live_mode(monkeypatch)
        issued = _canonical_stand_in(monkeypatch)
        # the runner's account snapshot (reads only)
        rec = await mirror.snapshot(conn, await M.control(conn))
        assert rec["reconciled"] is True and mirror._buying_power == 100
        # ONE DECISION: $1,000 of paper at 0.50 -> 2 contracts, $1.00 live
        po = await TE._paper_order(conn, acct, qty=2000, wire=0.50)
        wire.fills = [1]                                # 1 of 2 executes
        wire.first_read_working.add("ord-1")
        res = await mirror.lane._run(conn, po["intent_id"])
        assert res["state"] == "SUBMITTED", res
        assert res["venue_order_id"] == "ord-1" and len(issued) == 1
        assert isinstance(issued[0], LA.LiveAuthorization)
        create = wire.bodies[wire.seen.index(("POST", "/v1/orders"))]
        assert create == {"marketSlug": po["slug"], "intent": "ORDER_INTENT_BUY_LONG",
                          "type": "ORDER_TYPE_LIMIT",
                          "price": {"value": "0.5000", "currency": "USD"},
                          "quantity": 2, "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
                          "synchronousExecution": True}
        # the order record was read at once; the partial fill booked from it
        r = await TE._row(conn, po["order_id"])
        assert (r["state"], r["cum_qty"], r["live_qty"]) == ("PARTIALLY_FILLED", 1, 2)
        fills = await conn.fetch("SELECT * FROM execmirror_fills WHERE mirror_id = $1",
                                 r["mirror_id"])
        assert [(f["qty"], f["price"], f["source"]) for f in fills] == [
            (Decimal(1), Decimal("0.5"), "VENUE_ORDER_RECORD")]
        h = await conn.fetchrow("SELECT * FROM smalllive_handoffs WHERE group_id = $1",
                                po["group_id"])
        assert h["owner"] == "XAVIER" and h["live_held"] == 1 and h["state"] == "OPEN"
        # the remainder's cancel, then the record: CANCELLED with the cum kept
        await mirror._cancel(conn, r, "IOC_REMAINDER")
        r = await TE._row(conn, po["order_id"])
        assert (r["state"], r["cum_qty"]) == ("CANCELLED", 1)
        cancel = wire.bodies[wire.seen.index(("POST", "/v1/order/ord-1/cancel"))]
        assert cancel == {"marketSlug": po["slug"]}
        # reconciliation against /v1/portfolio/positions: SIGNED
        wire.positions_net = [{po["slug"]: -1}, {po["slug"]: 1}]
        bad = await mirror.snapshot(conn, await M.control(conn))
        assert bad["reconciled"] is False and po["slug"] in bad["differences"]
        good = await mirror.snapshot(conn, await M.control(conn))
        assert good == {"reconciled": True, "differences": {}}
        # Xavier's / the paper EXIT: a SELL admitted under the risk-reducing
        # rule (no token), sized to the held contract, IOC
        await TE._paper_fill(conn, acct, po, qty=2000, price=0.50)
        ex = await TE._paper_order(conn, acct, role="EXIT", direction="SELL",
                                   intent="ORDER_INTENT_SELL_LONG", qty=2000,
                                   wire=0.48, group=po["group_id"])
        await mirror.plan_new(conn, await M.control(conn))
        wire.fills = [1]
        await mirror.submit_planned(conn)
        x = await TE._row(conn, ex["order_id"])
        assert (x["state"], x["live_qty"], x["venue_order_id"]) == ("FILLED", 1, "ord-2")
        assert _j(x["detail"])["risk_reducing_sell"]["admitted"] is True
        sell = wire.bodies[[i for i, s in enumerate(wire.seen)
                            if s == ("POST", "/v1/orders")][1]]
        assert (sell["intent"], sell["quantity"], sell["price"]["value"]) == (
            "ORDER_INTENT_SELL_LONG", 1, "0.4800")
        assert len(issued) == 1                         # the SELL used no token
        assert await _held(conn, po["group_id"]) == 0
        await mirror.live_handoffs(conn)
        assert (await conn.fetchval("SELECT state FROM smalllive_handoffs"
                                    " WHERE group_id = $1", po["group_id"])) == "CLOSED"
        wire.positions_net = [{}]
        assert (await mirror.snapshot(conn, await M.control(conn)))["reconciled"] is True
        # THE WIRE, request by request, every one signed with the test key
        snap = [("GET", "/v1/account/balances"), ("GET", "/v1/portfolio/positions"),
                ("GET", "/v1/orders/open")]
        assert wire.seen == (snap + [("POST", "/v1/orders"), ("GET", "/v1/order/ord-1"),
                                     ("POST", "/v1/order/ord-1/cancel"),
                                     ("GET", "/v1/order/ord-1")]
                             + snap + snap
                             + [("POST", "/v1/orders"), ("GET", "/v1/order/ord-2")]
                             + snap), wire.seen
        assert wire.auth_failures == []
    finally:
        await conn.close()


@pg
async def test_the_same_flow_in_shadow_refuses_every_write_with_nothing_on_the_wire(
        monkeypatch):
    """SMALL LIVE IS SHADOW (as shipped): the canonical stand-in issues no
    token -> the lane refuses before its claim; an ACTUAL position's exit is
    claimed and refused by the adapter (REJECTED, as before); the adapter
    refuses a LiveAuthorization built directly; nothing reaches the socket.
    EXACTLY AS TODAY: this test passes unchanged on the base release too (a
    risk-reducing admission handed to the adapter in SHADOW is refused in
    test_the_production_adapter_sends_an_admitted_sell_and_refuses_everything_else)."""
    conn = await TE._conn()
    try:
        acct, mirror, wire = await _wire_world(conn, monkeypatch)
        _canonical_stand_in(monkeypatch)
        await _snapshot_row(conn, 1)                    # current, reconciled
        po = await TE._paper_order(conn, acct, qty=2000, wire=0.50)
        res = await mirror.lane._run(conn, po["intent_id"])
        assert res["state"] == "REFUSED"
        assert res["refusal"] == LP.R_NO_LIVE_AUTHORIZATION
        assert await conn.fetchval("SELECT count(*) FROM execmirror_orders") == 0
        # an ACTUAL position of 1 (as if bought before), and its paper EXIT
        await conn.execute(
            """INSERT INTO execmirror_orders (mirror_id, group_id, role, strategy,
                   us_market_slug, intent, order_type, tif, wire_price, live_qty,
                   state, venue_order_id, cum_qty)
               VALUES ('em:held', $1, 'ENTRY', 'PINNACLE_COMPLETED_GAME_PAPER', $2,
                       'ORDER_INTENT_BUY_LONG', 'MARKETABLE', 'IOC', 0.50, 1,
                       'FILLED', 'ord-held', 1)""", po["group_id"], po["slug"])
        await conn.execute(
            """INSERT INTO execmirror_fills (fill_key, mirror_id, venue_order_id,
                   group_id, us_market_slug, intent, qty, price)
               VALUES ('ord-held:1', 'em:held', 'ord-held', $1, $2,
                       'ORDER_INTENT_BUY_LONG', 1, 0.50)""", po["group_id"], po["slug"])
        await TE._paper_fill(conn, acct, po, qty=2000, price=0.50)
        ex = await TE._paper_order(conn, acct, role="EXIT", direction="SELL",
                                   intent="ORDER_INTENT_SELL_LONG", qty=2000,
                                   wire=0.48, group=po["group_id"])
        await mirror.plan_new(conn, await M.control(conn))
        await mirror.submit_planned(conn)
        x = await TE._row(conn, ex["order_id"])
        assert x["state"] == "REJECTED"
        assert _j(x["error"])["error"] == "LegacyOriginationRetired"
        # the adapter itself, whatever it is handed
        v = mirror._venue
        params = {"marketSlug": po["slug"], "intent": "ORDER_INTENT_BUY_LONG",
                  "type": "ORDER_TYPE_LIMIT", "quantity": 2,
                  "price": {"value": "0.5000", "currency": "USD"},
                  "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"}
        direct = LA.LiveAuthorization(intent_id="x", content_sha="y", issued_at=1.0)
        for kw in ({}, {"canonical_live_authorization": direct}):
            for p in (params, dict(params, intent="ORDER_INTENT_SELL_LONG",
                                   quantity=1)):
                with pytest.raises(M.LegacyOriginationRetired):
                    v.place(dict(p), **kw)
        assert wire.seen == []
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §7 THE ~$1 PILOT'S SIZE (scale 1,000; max_order_usd 1.00)
# ═════════════════════════════════════════════════════════════════════
#
# THE VENUE'S RULE (docs.polymarket.us, read 2026-10-09): each market states
# `minimumTradeQty` ("Minimum order quantity in contracts (e.g. 0.01 = 1% of
# a contract, 1.0 = one whole contract)"); most markets trade in steps of
# 0.01 contract, a few (mostly futures) in whole contracts; POST /v1/orders
# `quantity` "Supports decimal quantities on markets whose minimumTradeQty is
# less than 1". THE LANE'S RULE (execmirror.entry_live_qty over scale_qty,
# rounding NEAREST_WHOLE_CONTRACT): the venue quantity is a WHOLE contract, so
# the lane's own floor is one contract (stricter than the venue's 0.01 where
# a market allows it). raw = paper qty / 1,000 EXACTLY:
#   raw < 1          -> BELOW_VENUE_MINIMUM, live 0: never enlarged (REVIEW:
#                       nearest half-even rounding sent raw 0.6 .. 0.99 as ONE
#                       contract -- a size below the minimum, enlarged);
#   raw >= 1         -> nearest whole contract, half to even (1.5 -> 2,
#                       2.5 -> 2, 1.818 -> 2);
#   cost > the cap   -> ABOVE_ORDER_CAP, never shrunk.

def _cg(qty, wire):
    return {"us_market_slug": "s", "intent": "ORDER_INTENT_BUY_LONG",
            "qty": Decimal(str(qty)), "wire_price": Decimal(str(wire)),
            "time_in_force": "IOC", "order_type": "MARKETABLE"}


@pytest.mark.parametrize("paper_usd,wire,live,cost,state,why", [
    (1500, "0.50", 3, "1.50", "EXCLUDED", "ABOVE_ORDER_CAP"),     # never shrunk to 2
    (1000, "0.50", 2, "1.00", "PLANNED", None),                  # exactly the cap
    (1000, "0.55", 2, "1.10", "EXCLUDED", "ABOVE_ORDER_CAP"),    # 1.818 -> 2, never 1
    (250, "0.50", 0, None, "EXCLUDED", "BELOW_VENUE_MINIMUM"),   # 0.5 -> 0 (half even)
    (200, "0.50", 0, None, "EXCLUDED", "BELOW_VENUE_MINIMUM"),   # 0.4 -> 0, never 1
    (750, "0.50", 2, "1.00", "PLANNED", None),                   # 1.5 -> 2 (half even)
    (300, "0.50", 0, None, "EXCLUDED", "BELOW_VENUE_MINIMUM"),   # 0.6 -> 0, never 1
    (495, "0.50", 0, None, "EXCLUDED", "BELOW_VENUE_MINIMUM"),   # 0.99 -> 0, never 1
    (500, "0.50", 1, "0.50", "PLANNED", None),                   # exactly one contract
])
def test_the_one_dollar_pilot_maps_paper_dollars_to_live_contracts(
        paper_usd, wire, live, cost, state, why):
    qty = Decimal(paper_usd) / Decimal(wire)
    p = M.plan_buy(_cg(qty, wire), scale=1000, buying_power=1000, max_order_usd="1.00")
    assert (p.state, p.exclusion, p.live_qty) == (state, why, live), p
    if cost is not None:
        assert Decimal(p.detail["cost_usd"]) == Decimal(cost)
    if state == "PLANNED":
        assert p.params["quantity"] == live and isinstance(p.params["quantity"], int)


@pg
async def test_the_one_dollar_pilot_through_the_actual_lane(monkeypatch):
    """The same rules on the lane: a $1,500 decision is refused ABOVE_ORDER_CAP
    with its 3-contract live intent recorded (never shrunk), sub-contract
    ones (0.4 and 0.6 of a contract) BELOW_VENUE_MINIMUM with live 0 recorded
    (never enlarged), and the $1,000 decision goes out as 2 contracts for
    $1.00."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch, cap=1)
        await mirror.snapshot(conn, await M.control(conn))
        big = await TE._paper_order(conn, acct, qty=3000, wire=0.50)
        small = await TE._paper_order(conn, acct, qty=400, wire=0.50)
        near = await TE._paper_order(conn, acct, qty=600, wire=0.50)
        ok = await TE._paper_order(conn, acct, qty=2000, wire=0.50)
        rb = await mirror.lane._run(conn, big["intent_id"])
        assert (rb["state"], rb["refusal"]) == ("REFUSED", "ABOVE_ORDER_CAP")
        assert Decimal(rb["cost_usd"]) == Decimal("1.50")
        assert Decimal(rb["cap_usd"]) == Decimal("1.00")
        it = await conn.fetchrow("SELECT live_qty, live_raw_qty FROM execution_intents"
                                 " WHERE intent_id = $1", big["intent_id"])
        assert (it["live_qty"], it["live_raw_qty"]) == (3, Decimal("3"))
        rs = await mirror.lane._run(conn, small["intent_id"])
        assert (rs["state"], rs["refusal"]) == ("REFUSED", "BELOW_VENUE_MINIMUM")
        assert Decimal(rs["live_raw_qty"]) == Decimal("0.4")
        # REVIEW: 0.6 of a contract ($300 of paper at 0.50) was rounded UP to
        # one contract and sent; it is below the minimum and never enlarged
        rn = await mirror.lane._run(conn, near["intent_id"])
        assert (rn["state"], rn["refusal"]) == ("REFUSED", "BELOW_VENUE_MINIMUM"), rn
        assert Decimal(rn["live_raw_qty"]) == Decimal("0.6")
        it = await conn.fetchrow("SELECT live_qty, live_raw_qty FROM execution_intents"
                                 " WHERE intent_id = $1", near["intent_id"])
        assert (it["live_qty"], it["live_raw_qty"]) == (0, Decimal("0.6"))
        assert venue.placed == []
        ro = await mirror.lane._run(conn, ok["intent_id"])
        assert ro["state"] == "SUBMITTED"
        assert [(p["quantity"], p["price"]["value"]) for p in venue.placed] == [
            (2, "0.5000")]
    finally:
        await conn.close()
