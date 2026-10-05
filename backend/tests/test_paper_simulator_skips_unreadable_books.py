"""P0 INCIDENT (2026-10-04): AN UNREADABLE BOOK OBSERVATION IS SKIPPED, NOT A
VERDICT ON THE ORDER.

Measured in production on 191b299: the marketable-order simulator took the
FIRST paper_book_observations row at or after `eligible_at`, errored or not,
and an errored row expired the order at once (THE_OBSERVED_BOOK_WAS_UNREADABLE)
-- 44 paper entry orders a day, 61% of all of them, while orders that met a
readable in-window book filled 20 of 24. Most errored rows record a read that
never left our process: the venue request gate refusing because a 429 cooldown
outlasted the pass deadline, or our own deadline cutting the wait.

The repaired rule, pinned here on production-shaped observation sequences:

  * the order is evaluated on the FIRST READABLE observation in
    [eligible_at, expires_at]; errored rows before it are skipped and NAMED
    on the fill's evidence and FILL event (and on a release event);
  * an order that meets only errored rows stays pending until `expires_at`
    and then expires NO_READABLE_BOOK_BEFORE_EXPIRY with the counts of
    gate-refused, deadline-cut and venue-error reads;
  * a readable row followed by an errored one fills on the readable one;
  * THE FILL RULE ITSELF IS UNCHANGED: the first readable book decides with
    the same walk, limit, depth and consumption -- a later, better book is
    never used;
  * the delayed-fill step reads again for an order that has met only
    errored rows.
"""
from __future__ import annotations

import time

import pytest

from sportsassets import bettor_paper_guard as G
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import venue_request_gate as GRT

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

#: THE ERROR STRINGS PRODUCTION WRITES on paper_book_observations.error:
#: our gate's refusal codes, our deadline's code, the venue SDK's exception
#: class names and the payload refusal (record_book stores `read["error"]`).
GATE_COOLDOWN = GRT.R_COOLDOWN_EXCEEDS_DEADLINE
DEADLINE_CUT = G.R_BOOK_READ_DEADLINE
VENUE_429 = "RateLimitError"
VENUE_TIMEOUT = "APITimeoutError"


async def unreadable(conn, slug, at, error, *, refused_by=None):
    read = {"marketData": None, "error": error, "observed_at": at}
    if refused_by:
        read["refused_by"] = refused_by
    got = await SIM.record_book(conn, slug=slug, read=read,
                                source="PAPER_MARKET_DATA_CLIENT",
                                read_basis="OPEN_ORDER_OR_POSITION")
    return got["obs_id"]


def SL(a, n):
    return "%s:%s" % (a["account_id"], n)


# ── pure ────────────────────────────────────────────────────────────
def test_the_unreadable_classes_are_pinned_to_their_sources():
    # every refusal our venue request gate can raise is GATE_REFUSED -- a new
    # gate code must be added here, not silently counted as a venue error
    gate_codes = {v for k, v in vars(GRT).items()
                  if k.startswith("R_") and isinstance(v, str)}
    assert gate_codes == set(SIM.GATE_REFUSAL_CODES)
    assert SIM.DEADLINE_CUT_CODES == {G.R_BOOK_READ_DEADLINE}
    for code in gate_codes:
        assert SIM.unreadable_class(code) == SIM.U_GATE_REFUSED
    assert SIM.unreadable_class(DEADLINE_CUT) == SIM.U_DEADLINE_CUT
    for venue in (VENUE_429, VENUE_TIMEOUT, "NotFoundError",
                  "NO_MARKET_DATA_IN_PAYLOAD", "NO_MARKET_DATA", None):
        assert SIM.unreadable_class(venue) == SIM.U_VENUE_ERROR
    s = SIM.unreadable_summary([
        {"obs_id": 1, "observed_at": 10.0, "error": GATE_COOLDOWN},
        {"obs_id": 2, "observed_at": 11.0, "error": VENUE_429},
        {"obs_id": 3, "observed_at": 12.0, "error": DEADLINE_CUT},
        {"obs_id": 4, "observed_at": 13.0, "error": GATE_COOLDOWN}])
    assert (s["total"], s["gate_refused"], s["deadline_cut"],
            s["venue_error"]) == (4, 2, 1, 1)
    assert s["by_error"] == {GATE_COOLDOWN: 2, VENUE_429: 1,
                             DEADLINE_CUT: 1}
    assert [n["book_obs_id"] for n in s["named"]] == [1, 2, 3, 4]
    assert not s["named_truncated"]
    many = SIM.unreadable_summary([{"obs_id": i, "observed_at": float(i),
                                    "error": VENUE_429} for i in range(30)])
    assert many["total"] == 30 and len(many["named"]) == SIM.MAX_SKIPPED_NAMED
    assert many["named_truncated"]


# ── against the database ────────────────────────────────────────────
@pg
async def test_error_then_readable_fills_on_the_readable_book_and_names_the_skipped():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "skip1")
        s = SL(a, "m")
        o = H.order(a, key="e1", qty=100, limit=0.50, slug=s, at=H.T0,
                    delay=2.0, ttl=90.0)
        g = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=H.T0)
        oid = g["order"]["order_id"]
        # before the delay: a readable book that must not be used
        await H.observe(conn, s, H.T0 + 1.0, offers=[(0.40, 100)])
        # the production sequence: our gate refused (429 cooldown longer than
        # the pass deadline), then our own deadline cut the next read ...
        g1 = await unreadable(conn, s, H.T0 + 2.5, GATE_COOLDOWN,
                              refused_by="OUR_REQUEST_GATE")
        d1 = await unreadable(conn, s, H.T0 + 4.0, DEADLINE_CUT)
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 5.0,
                                     fee_fn=H.zero_fee)
        # ... which no longer expires the order: it is still waiting
        assert r["pending"] and r["state"] == "PENDING_SIMULATION", r
        assert r["refusal"] == SIM.R_NO_READABLE_BOOK_YET
        assert r["unreadable_books_skipped"]["gate_refused"] == 1
        assert r["unreadable_books_skipped"]["deadline_cut"] == 1
        # the next read is readable: the order fills on it
        ok = await H.observe(conn, s, H.T0 + 9.0, offers=[(0.49, 60),
                                                          (0.50, 100)])
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 10.0,
                                     fee_fn=H.zero_fee)
        assert r["state"] == "FILLED" and r["book_obs_id"] == ok, r
        fills = await conn.fetch("SELECT * FROM paper_fills WHERE "
                                 "order_id=$1 ORDER BY price", oid)
        assert [(float(f["price"]), float(f["qty"])) for f in fills] == \
            [(0.49, 60.0), (0.50, 40.0)]
        assert {f["book_obs_id"] for f in fills} == {ok}
        ev = H.j(fills[0]["evidence"])["unreadable_books_skipped"]
        assert [n["book_obs_id"] for n in ev["named"]] == [g1, d1]
        assert [n["class"] for n in ev["named"]] == [SIM.U_GATE_REFUSED,
                                                     SIM.U_DEADLINE_CUT]
        assert (ev["total"], ev["gate_refused"], ev["deadline_cut"],
                ev["venue_error"]) == (2, 1, 1, 0)
        fe = await conn.fetch("SELECT detail FROM paper_order_events WHERE "
                              "order_id=$1 AND kind='FILL'", oid)
        assert fe and all(H.j(e["detail"])["unreadable_books_skipped"]
                          ["total"] == 2 for e in fe)
        # the fill's own fields are never overwritten by the extra detail
        assert {H.j(e["detail"])["book_obs_id"] for e in fe} == {ok}
        b = await L.balances(conn, a["account_id"], now=H.T0 + 11)
        assert b["reserved_usd"] == 0.0
        assert b["cash_usd"] == pytest.approx(500000.0 - (0.49 * 60
                                                          + 0.50 * 40))
    finally:
        await conn.close()


@pg
async def test_all_error_reads_expire_only_at_expires_at_with_counts_by_who_refused():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "skip2")
        s = SL(a, "m")
        o = H.order(a, key="e2", qty=100, limit=0.50, slug=s, at=H.T0,
                    delay=2.0, ttl=90.0)
        g = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=H.T0)
        oid = g["order"]["order_id"]
        ids = [await unreadable(conn, s, H.T0 + 3.0, GATE_COOLDOWN,
                                refused_by="OUR_REQUEST_GATE"),
               await unreadable(conn, s, H.T0 + 20.0, VENUE_429),
               await unreadable(conn, s, H.T0 + 40.0, DEADLINE_CUT),
               await unreadable(conn, s, H.T0 + 60.0, VENUE_TIMEOUT)]
        # outside the window: neither counted nor named
        await unreadable(conn, s, H.T0 + 1.0, VENUE_429)
        await unreadable(conn, s, H.T0 + 95.0, VENUE_429)
        for t in (H.T0 + 4, H.T0 + 45, H.T0 + 89.9):
            r = await SIM.simulate_order(conn, oid, now=t, fee_fn=H.zero_fee)
            assert r["pending"] and r["refusal"] == SIM.R_NO_READABLE_BOOK_YET
        # expires_at = decided + ttl = T0 + 90
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 90.0,
                                     fee_fn=H.zero_fee)
        assert r["state"] == "EXPIRED" and r["refusal"] == \
            SIM.R_NO_READABLE_BOOK, r
        row = await conn.fetchrow("SELECT state, terminal_reason, filled_qty "
                                  " FROM paper_orders WHERE order_id=$1", oid)
        assert row["terminal_reason"] == SIM.R_NO_READABLE_BOOK
        assert float(row["filled_qty"]) == 0.0
        ev = await conn.fetchrow("SELECT detail FROM paper_order_events WHERE "
                                 "order_id=$1 AND kind='EXPIRED'", oid)
        d = H.j(ev["detail"])
        assert d["reason"] == SIM.R_NO_READABLE_BOOK
        sk = d["unreadable_books_skipped"]
        assert (sk["total"], sk["gate_refused"], sk["deadline_cut"],
                sk["venue_error"]) == (4, 1, 1, 2)
        assert [n["book_obs_id"] for n in sk["named"]] == ids
        assert [n["error"] for n in sk["named"]] == [
            GATE_COOLDOWN, VENUE_429, DEADLINE_CUT, VENUE_TIMEOUT]
        assert not await conn.fetchval(
            "SELECT count(*) FROM paper_fills WHERE order_id=$1", oid)
        b = await L.balances(conn, a["account_id"], now=H.T0 + 93)
        assert b["cash_usd"] == 500000.0 and b["reserved_usd"] == 0.0
        # NOTHING observed at all in the window keeps its own precise reason
        o2 = H.order(a, key="e2b", qty=10, limit=0.50, slug=SL(a, "none"),
                     at=H.T0, delay=2.0, ttl=30.0)
        g2 = await L.submit_order(conn, o2, fee_fn=H.zero_fee, now=H.T0)
        r2 = await SIM.simulate_order(conn, g2["order"]["order_id"],
                                      now=H.T0 + 31, fee_fn=H.zero_fee)
        assert r2["refusal"] == SIM.R_NO_BOOK_IN_WINDOW
        assert r2["unreadable_books_skipped"]["total"] == 0
    finally:
        await conn.close()


@pg
async def test_readable_then_error_fills_on_the_readable_book():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "skip3")
        s = SL(a, "m")
        o = H.order(a, key="e3", qty=50, limit=0.50, slug=s, at=H.T0)
        g = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=H.T0)
        oid = g["order"]["order_id"]
        ok = await H.observe(conn, s, H.T0 + 2.0, offers=[(0.48, 100)])
        await unreadable(conn, s, H.T0 + 3.0, GATE_COOLDOWN,
                         refused_by="OUR_REQUEST_GATE")
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 4.0,
                                     fee_fn=H.zero_fee)
        assert r["state"] == "FILLED" and r["book_obs_id"] == ok
        assert r["unreadable_books_skipped"]["total"] == 0
        f = await conn.fetchrow("SELECT * FROM paper_fills WHERE "
                                "order_id=$1", oid)
        assert float(f["price"]) == 0.48
        assert "unreadable_books_skipped" not in H.j(f["evidence"])
    finally:
        await conn.close()


@pg
async def test_the_fill_rule_is_unchanged_the_first_readable_book_decides():
    """Skipping an errored row never turns into choosing the best book: the
    FIRST readable observation is evaluated by the same walk, and when it
    shows nothing within the limit the order ends there, even though a later
    readable book would have filled it. An FOK short of depth on that book
    is refused exactly as before."""
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "skip4")
        s = SL(a, "m")
        o = H.order(a, key="e4", qty=100, limit=0.50, slug=s, at=H.T0)
        g = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=H.T0)
        oid = g["order"]["order_id"]
        await unreadable(conn, s, H.T0 + 2.5, VENUE_429)
        first = await H.observe(conn, s, H.T0 + 5.0, offers=[(0.55, 500)])
        await H.observe(conn, s, H.T0 + 8.0, offers=[(0.30, 500)])
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 9.0,
                                     fee_fn=H.zero_fee)
        assert r["state"] == "EXPIRED" and r["book_obs_id"] == first
        assert r["refusal"] == SIM.R_NOTHING_WITHIN_LIMIT
        rel = await conn.fetchrow("SELECT detail FROM paper_order_events "
                                  " WHERE order_id=$1 AND kind='EXPIRED'",
                                  oid)
        d = H.j(rel["detail"])
        assert d["reason"] == SIM.R_NOTHING_WITHIN_LIMIT
        assert d["unreadable_books_skipped"]["venue_error"] == 1
        # FOK: the first readable book's depth within the limit decides
        s2 = SL(a, "fok")
        o2 = H.order(a, key="e4f", qty=300, limit=0.50, slug=s2, at=H.T0,
                     tif="FOK", allow_partial=False)
        g2 = await L.submit_order(conn, o2, fee_fn=H.zero_fee, now=H.T0)
        await unreadable(conn, s2, H.T0 + 2.1, DEADLINE_CUT)
        await H.observe(conn, s2, H.T0 + 3.0, offers=[(0.50, 200)])
        await H.observe(conn, s2, H.T0 + 4.0, offers=[(0.50, 1000)])
        r2 = await SIM.simulate_order(conn, g2["order"]["order_id"],
                                      now=H.T0 + 5.0, fee_fn=H.zero_fee)
        assert r2["state"] == "EXPIRED" and r2["refusal"] == SIM.R_FOK_SHORT
    finally:
        await conn.close()


@pg
async def test_an_exit_order_skips_unreadable_reads_too():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "skip5")
        s = SL(a, "m")
        buy = H.order(a, key="b5", qty=100, limit=0.50, slug=s, at=H.T0,
                      group_id="paper_grp_skip5")
        gb = await L.submit_order(conn, buy, fee_fn=H.zero_fee, now=H.T0)
        await H.observe(conn, s, H.T0 + 2.0, offers=[(0.50, 100)])
        assert (await SIM.simulate_order(conn, gb["order"]["order_id"],
                                         now=H.T0 + 3,
                                         fee_fn=H.zero_fee))["state"] == \
            "FILLED"
        sell = H.order(a, key="s5", qty=100, limit=0.40, slug=s,
                       at=H.T0 + 10, direction="SELL", role="EXIT",
                       group_id="paper_grp_skip5")
        gs = await L.submit_order(conn, sell, fee_fn=H.zero_fee,
                                  now=H.T0 + 10)
        assert gs.get("ok"), gs
        await unreadable(conn, s, H.T0 + 12.5, GATE_COOLDOWN,
                         refused_by="OUR_REQUEST_GATE")
        await H.observe(conn, s, H.T0 + 14.0, bids=[(0.45, 100)])
        r = await SIM.simulate_order(conn, gs["order"]["order_id"],
                                     now=H.T0 + 15, fee_fn=H.zero_fee)
        assert r["state"] == "FILLED", r
        assert r["unreadable_books_skipped"]["gate_refused"] == 1
    finally:
        await conn.close()


class _Book:
    """A read-only market-data stand-in: one readable book per read."""

    def __init__(self, at, offers):
        self.at, self.offers, self.reads = at, offers, []

    async def read_book(self, slug, **kw):
        self.reads.append(slug)
        return {"marketData": H.md(offers=self.offers),
                "observed_at": self.at}


@pg
async def test_the_delayed_fill_step_reads_again_for_an_order_that_met_only_errors():
    from sportsassets.agents import paper_derek as PD
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "skip6")
        s = SL(a, "m")
        o = H.order(a, key="e6", qty=40, limit=0.50, slug=s, at=H.T0)
        g = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=H.T0)
        oid = g["order"]["order_id"]
        await unreadable(conn, s, H.T0 + 2.5, GATE_COOLDOWN,
                         refused_by="OUR_REQUEST_GATE")
        md = _Book(H.T0 + 6.0, [(0.47, 100)])

        async def nosleep(_s):
            return None

        ctx = {"account_id": a["account_id"], "pending_entries": [],
               "clock": (lambda: H.T0 + 5.0), "now": H.T0 + 5.0,
               "deadline": time.monotonic() + 30.0, "sleep": nosleep,
               "config": {"cadence": {"max_book_reads_per_pass": 10}},
               "books_read": 0, "market_data": md, "first_fills": [],
               "fills": 0, "fee_fn": H.zero_fee}
        got = await PD.step_after_delay(conn, ctx)
        assert got["pending"] == 1 and md.reads == [s], got
        assert got["results"][0]["state"] == "FILLED", got
        f = await conn.fetchrow("SELECT price FROM paper_fills WHERE "
                                "order_id=$1", oid)
        assert float(f["price"]) == 0.47
    finally:
        await conn.close()
