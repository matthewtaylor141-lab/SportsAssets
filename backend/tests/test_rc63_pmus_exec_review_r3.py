"""CAPITAL-CRITICAL (rc6.3 pmus-exec, review round 3): A RISK-REDUCING SELL
ON THE RETAIL ACTUAL PATH IS NEVER DROPPED BY A GAP IN THE VENUE'S EVIDENCE.

cfaf0844 made both venue-evidence refusals terminal (EXCLUDED), so a
position the venue still held lost its live exits:
  * after an owner stop whose flatten did NOT trade and a quick resume, the
    first RUNNING tick submitted before its next snapshot (snapshots come
    every SNAPSHOT_EVERY_S): every SELL was RISK_REDUCING_SELL_VENUE_POSITION_
    NOT_EVIDENCED (probes A, B);
  * with no stop at all, one snapshot read the venue after a resting fill
    landed but before the poll booked it -- the venue holding MORE than our
    fills -- and every SELL until the next snapshot was VENUE_POSITION_
    DISAGREES_WITH_MIRROR_FILLS (probes C, D);
  * resync_protection re-planned a refused STANDING_PROTECTION on every 2 s
    tick until MAX_PROTECTION_ROWS was spent -- no live protection, for good;
    a paper REDUCE, mirrored once and never re-planned, was dropped; an EXIT
    waited for an ORPHAN_CLOSE minutes after the paper position closed.
3fa9d7a9 sent all of them. Now:
  §1 the first RUNNING tick after a stop takes its snapshot BEFORE it
     recovers, plans or submits (outside SHADOW; one attempt, never a read
     per tick): the protection is back and the REDUCE is sent; after a
     flatten that DID trade both are refused by name with nothing sent;
  §2 a snapshot BEHIND our books (the venue holding more than our fills) is
     a WAIT for the next one: the protection spends one row and goes out,
     the REDUCE and the EXIT go out; a newer snapshot that still names the
     market ends the wait by name;
  §3 NOT_EVIDENCED is a WAIT too: the row stays PLANNED (still committed
     against the inventory), one event per change of evidence, and the SAME
     order goes out against the first snapshot that evidences the market;
     waiting rows never hold up another; a stop still excludes a waiting
     row; the wait is bounded (SELL_EVIDENCE_WAIT_MAX_S); a waiting
     protection child is never sent after its paper protection ended;
  §4 a refusal on the venue's evidence never spends MAX_PROTECTION_ROWS:
     after a long disagreement the protection comes back once a snapshot
     agrees;
  §5 SHADOW (as shipped) is unchanged: no extra snapshot, no wait, nothing
     reaches the client.
Each test that names cfaf0844 fails there, on the behaviour the reviewers
reported; the rest are guards. The P4 rule is kept as it was: a snapshot
whose venue holds LESS than our fills (flat after a flatten that traded)
refuses the SELL at once (tests/test_rc63_pmus_exec_review_r2.py §1).

THE MODE NEVER CHANGES OUTSIDE A TEST: SMALL_LIVE_MODE stays SHADOW in
live_authorization and live_parity (tests/test_rc62_pmus_exec_retail_path.py
pins it); the non-SHADOW mode exists only inside the tests that monkeypatch
it. FakeVenue behind the production Venue.place only: no credential, no
network, no order anywhere. Own Postgres (RN1X_TEST_DSN); the control row and
the lane's tables are restored after every test.
"""
from __future__ import annotations

import json
import time

from sportsassets import execmirror as M

from tests import test_execmirror as TE
from tests import test_rc62_pmus_exec_retail_path as R
from tests import test_rc63_pmus_exec_review_r2 as R2

pg = TE.pg
#: every test here leaves execmirror_control and the lane's tables as found
_restore_control = R._restore_control

NOT_EVIDENCED = "RISK_REDUCING_SELL_VENUE_POSITION_NOT_EVIDENCED"
DISAGREES = "VENUE_POSITION_DISAGREES_WITH_MIRROR_FILLS"
GTD = "TIME_IN_FORCE_GOOD_TILL_DATE"
IOC = "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL"


def _j(v):
    return json.loads(v) if isinstance(v, str) else (v or {})


def _sells(prod):
    return [(p["intent"], p["quantity"], p["tif"]) for p in prod.rec.created
            if "SELL" in p["intent"]]


async def _protection_rows(conn):
    return [dict(r) for r in await conn.fetch(
        "SELECT * FROM execmirror_orders WHERE role = 'STANDING_PROTECTION'"
        " ORDER BY created_at, mirror_id")]


def _stepping_clock(step_s=M.TICK_S):
    """The reviewers' clock: each call to step() moves it `step_s` (default
    one tick), or the seconds it is given."""
    t = [time.time()]

    def step(s=None):
        t[0] += step_s if s is None else s
    return (lambda: t[0]), step


async def _held_with_protection(conn, monkeypatch, venue, clock):
    """An ACTUAL position of 2 (entry through the lane, paper sibling
    filled), the mode flipped off SHADOW in-test, the production Venue.place
    in front of `venue`, and the paper STANDING_PROTECTION held live (a
    resting SELL_LONG of 2, OPEN)."""
    acct, _fake, mirror = await TE._setup(conn, monkeypatch)
    mirror._venue_factory = lambda: venue
    mirror._venue = None
    mirror._now = clock
    entry = await TE._paper_order(conn, acct, qty=2000)
    venue.behaviour = [{"fill": 2}]
    await mirror.tick(conn)
    await TE._paper_fill(conn, acct, entry, qty=2000)
    assert await R._held(conn, entry["group_id"]) == 2
    R._live_mode(monkeypatch)
    prod = R.ProdPlace(venue)
    mirror._venue = prod
    prot = await TE._paper_order(conn, acct, role="STANDING_PROTECTION",
                                 direction="SELL", intent="ORDER_INTENT_SELL_LONG",
                                 qty=2000, group=entry["group_id"], tif="GTD",
                                 otype="RESTING", state="RESTING")
    for _ in range(3):
        await mirror.tick(conn)
    (row,) = await _protection_rows(conn)
    assert (row["state"], row["live_qty"]) == ("OPEN", 2), row
    return acct, mirror, prod, entry, prot


async def _stop_with_flatten_then_resume(conn, mirror):
    """The owner's stop with flatten, carried out to STOPPED, then the
    owner's resume (api/app.py moves the revision on each action)."""
    await conn.execute("UPDATE execmirror_control SET stopped = true,"
                       " stop_done_at = NULL, flatten_on_stop = true,"
                       " revision = revision + 1 WHERE id = 1")
    out = await mirror.tick(conn)
    assert out["state"] == "STOPPED" and len(out["closed"]) == 1, out
    await conn.execute("UPDATE execmirror_control SET stopped = false,"
                       " stop_done_at = NULL, flatten_on_stop = false,"
                       " revision = revision + 1 WHERE id = 1")


async def _filled_before_the_poll_booked_it(conn, monkeypatch, *, protection=False,
                                            live=True, foreign=0):
    """NO STOP. A RESTING live entry of 2 (through the lane) fills at the
    venue and its paper sibling fills; an account snapshot then reads the
    venue BEFORE the lane's poll books that fill (as the runner's end-of-tick
    snapshot can while the ActualLane's order rests): that one snapshot names
    the market with the venue holding MORE than our fills said. `foreign`
    contracts bought on the same market outside the lane keep it so. With
    `protection`, a paper STANDING_PROTECTION exists from before the fill
    (its first live row: EXCLUDED NO_LIVE_INVENTORY). Then the mode flipped
    off SHADOW in-test (unless not `live`), the production Venue.place in
    front of the fake venue, and a stepping clock."""
    acct, venue, mirror = await TE._setup(conn, monkeypatch)
    clock, step = _stepping_clock()
    mirror._now = clock
    entry = await TE._paper_order(conn, acct, qty=2000, tif="GTD", otype="RESTING")
    await mirror.tick(conn)
    e = await TE._row(conn, entry["order_id"])
    assert e["state"] == "OPEN" and e["venue_order_id"], e
    if live:
        R._live_mode(monkeypatch)
    prod = R.ProdPlace(venue)
    mirror._venue = prod
    prot = None
    if protection:
        prot = await TE._paper_order(conn, acct, role="STANDING_PROTECTION",
                                     direction="SELL", intent="ORDER_INTENT_SELL_LONG",
                                     qty=2000, group=entry["group_id"], tif="GTD",
                                     otype="RESTING", state="RESTING")
        step()
        await mirror.tick(conn)
        (row,) = await _protection_rows(conn)
        assert (row["state"], row["exclusion"]) == ("EXCLUDED", "NO_LIVE_INVENTORY")
    venue.fill(e["venue_order_id"], 2)
    if foreign:
        vid = venue._new({"marketSlug": entry["slug"], "intent": "ORDER_INTENT_BUY_LONG",
                          "quantity": foreign, "price": {"value": "0.50"}, "tif": "x"})
        venue.fill(vid, foreign)
    await TE._paper_fill(conn, acct, entry, qty=2000)
    rec = await mirror.snapshot(conn, await M.control(conn))
    assert rec == {"reconciled": False, "differences": {entry["slug"]: {
        "mirror_fills_net": 0, "baseline": 0, "venue": 2 + foreign,
        "signed": True}}}, rec
    return acct, venue, mirror, entry, prod, step, prot


# ═════════════════════════════════════════════════════════════════════
# §1 THE FIRST RUNNING TICK AFTER A STOP SNAPSHOTS BEFORE IT SUBMITS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_quick_resume_after_a_flatten_that_did_not_trade_restores_the_live_protection(
        monkeypatch):
    """PROBE A, INVERTED. The stop cancels the live protection and asks the
    venue to close the position; the close is answered but does not trade
    (the venue still holds 2). The owner resumes at once. The first RUNNING
    tick snapshots first (the venue holds 2: it agrees), the protection is
    planned again and sent on the next tick, and 120 s later exactly one
    live protection is working, on 2 rows. cfaf0844: r1..r11 all EXCLUDED
    NOT_EVIDENCED, 0 working protection."""
    conn = await TE._conn()
    try:
        venue = TE.FakeVenue()                          # close: 2xx, no trade
        clock, step = _stepping_clock()
        acct, mirror, prod, entry, prot = await _held_with_protection(
            conn, monkeypatch, venue, clock)
        step()
        await _stop_with_flatten_then_resume(conn, mirror)
        assert venue.positions()[entry["slug"]]["netPosition"] == "2"
        pre = await conn.fetchval("SELECT max(snapshot_id) FROM execmirror_snapshots")
        step()
        out = await mirror.tick(conn)
        assert out["snapshot"] == {"reconciled": True, "differences": {}}, out
        step()
        await mirror.tick(conn)
        rows = await _protection_rows(conn)
        assert [(r["state"], r["exclusion"]) for r in rows] == [
            ("CANCELLED", None), ("OPEN", None)], rows
        rr = _j(rows[1]["detail"])["risk_reducing_sell"]
        assert rr["admitted"] is True and "awaited" not in rr
        assert rr["venue_position"]["account_snapshot_id"] > pre
        for _ in range(58):                             # 120 s in all
            step()
            await mirror.tick(conn)
        rows = await _protection_rows(conn)
        assert [(r["state"], r["live_qty"]) for r in rows] == [
            ("CANCELLED", 2), ("OPEN", 2)], rows
        assert _sells(prod) == [("ORDER_INTENT_SELL_LONG", 2, GTD)] * 2
        assert venue.positions()[entry["slug"]]["netPosition"] == "2"
    finally:
        await conn.close()


@pg
async def test_a_quick_resume_after_a_flatten_that_did_not_trade_sends_the_paper_reduce(
        monkeypatch):
    """PROBE B, INVERTED: the same stop and resume, then a paper REDUCE of
    1,000 (half the position). It is sent on the first RUNNING tick and
    fills: held 1, the venue 1. cfaf0844: EXCLUDED NOT_EVIDENCED, nothing
    sent, held 2 after 80 s."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        clock, step = _stepping_clock()
        mirror._now = clock
        entry = await TE._paper_order(conn, acct, qty=2000)
        venue.behaviour = [{"fill": 2}]
        await mirror.tick(conn)
        await TE._paper_fill(conn, acct, entry, qty=2000)
        R._live_mode(monkeypatch)
        prod = R.ProdPlace(venue)
        mirror._venue = prod
        step()
        await _stop_with_flatten_then_resume(conn, mirror)
        red = await TE._paper_order(conn, acct, role="REDUCE", direction="SELL",
                                    intent="ORDER_INTENT_SELL_LONG", qty=1000,
                                    group=entry["group_id"])
        venue.behaviour = [{"fill": 1}]
        step()
        await mirror.tick(conn)
        r = await TE._row(conn, red["order_id"])
        assert (r["state"], r["exclusion"], r["live_qty"]) == ("FILLED", None, 1), r
        for _ in range(39):
            step()
            await mirror.tick(conn)
        assert _sells(prod) == [("ORDER_INTENT_SELL_LONG", 1, IOC)]
        assert await R._held(conn, entry["group_id"]) == 1
        assert venue.positions()[entry["slug"]]["netPosition"] == "1"
    finally:
        await conn.close()


@pg
async def test_after_a_flatten_that_traded_protection_and_reduce_stay_excluded_with_nothing_sent(
        monkeypatch):
    """The close-position DID trade: the venue is flat while our fills still
    read 2. The first RUNNING tick's snapshot says so, and the REDUCE and the
    protection are refused by name at once (VENUE_POSITION_DISAGREES_WITH_
    MIRROR_FILLS -- the venue holds LESS: the P4 rule, unchanged), nothing
    sent, no short opened. The protection is planned again only after a
    minute's pause (as after a REJECTED), not on every tick. cfaf0844:
    refused as NOT_EVIDENCED on the first ticks and 11 rows spent within 11
    ticks."""
    conn = await TE._conn()
    try:
        fv = R2.FlattenVenue()
        acct, mirror, prod, entry, prot = await _held_with_protection(
            conn, monkeypatch, fv, time.time)
        created_before = len(prod.rec.created)
        await _stop_with_flatten_then_resume(conn, mirror)
        assert fv.positions() == {}                     # the flatten traded
        red = await TE._paper_order(conn, acct, role="REDUCE", direction="SELL",
                                    intent="ORDER_INTENT_SELL_LONG", qty=1000,
                                    group=entry["group_id"])
        out = await mirror.tick(conn)
        assert out["snapshot"]["reconciled"] is False
        assert entry["slug"] in out["snapshot"]["differences"]
        r = await TE._row(conn, red["order_id"])
        assert (r["state"], r["exclusion"]) == ("EXCLUDED", DISAGREES), r
        vp = _j(r["detail"])["risk_reducing_sell"]["venue_position"]
        assert vp["venue_holds_more_than_fills"] is False
        for _ in range(20):                             # well inside a minute
            await mirror.tick(conn)
        rows = await _protection_rows(conn)
        assert [(x["state"], x["exclusion"]) for x in rows] == [
            ("CANCELLED", None), ("EXCLUDED", DISAGREES)], rows
        # a minute passes for that refusal (its row aged on the database's
        # clock, which the pause reads): one more attempt, refused alike, and
        # the pause holds again
        await conn.execute(
            "UPDATE execmirror_orders SET updated_at = updated_at"
            " - make_interval(secs => $2) WHERE mirror_id = $1",
            rows[1]["mirror_id"], M.PROTECTION_PAUSE_S + 1)
        for _ in range(3):
            await mirror.tick(conn)
        rows = await _protection_rows(conn)
        assert [(x["state"], x["exclusion"]) for x in rows] == [
            ("CANCELLED", None), ("EXCLUDED", DISAGREES),
            ("EXCLUDED", DISAGREES)], rows
        assert len(prod.rec.created) == created_before  # nothing after the stop
        assert fv.positions() == {}                     # no short was opened
        assert await R._events(conn, M.AWAITING_VENUE_EVIDENCE) == []
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §2 A SNAPSHOT BEHIND OUR BOOKS IS A WAIT FOR THE NEXT ONE
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_snapshot_taken_before_a_fill_was_booked_never_spends_the_protection(
        monkeypatch):
    """PROBE C, INVERTED. No stop. The one snapshot that read the venue
    before the poll booked the fill names the market (venue 2, our fills
    0). The protection planned after the poll WAITS on one row (PLANNED, one
    event), and goes out once the next snapshot agrees; 90 s later exactly
    one live protection is working, on that row. cfaf0844: r1..r11 EXCLUDED
    VENUE_POSITION_DISAGREES_WITH_MIRROR_FILLS, 0 working, sells []."""
    conn = await TE._conn()
    try:
        acct, venue, mirror, entry, prod, step, prot = \
            await _filled_before_the_poll_booked_it(conn, monkeypatch, protection=True)
        snaps = []
        for _ in range(45):                             # 90 s
            step()
            out = await mirror.tick(conn)
            if "snapshot" in out:
                snaps.append(out["snapshot"])
            rows = await _protection_rows(conn)
            assert len(rows) <= 2, rows                 # never a row per tick
        assert snaps == [{"reconciled": True, "differences": {}}]
        rows = await _protection_rows(conn)
        assert [(x["state"], x["exclusion"], x["live_qty"]) for x in rows] == [
            ("EXCLUDED", "NO_LIVE_INVENTORY", 0), ("OPEN", None, 2)], rows
        rr = _j(rows[1]["detail"])["risk_reducing_sell"]
        assert rr["admitted"] is True
        assert rr["awaited"]["awaiting"] == DISAGREES
        assert rr["awaited"]["venue_position"]["venue_holds_more_than_fills"] is True
        assert rr["awaited"]["venue_position"]["difference"] == {
            "mirror_fills_net": 0, "baseline": 0, "venue": 2, "signed": True}
        assert rr["venue_position"]["account_snapshot_id"] > \
            rr["awaited"]["waiting_on_snapshot_id"]
        assert len(await R._events(conn, M.AWAITING_VENUE_EVIDENCE,
                                   rows[1]["mirror_id"])) == 1
        assert _sells(prod) == [("ORDER_INTENT_SELL_LONG", 2, GTD)]
        assert await R._held(conn, entry["group_id"]) == 2
    finally:
        await conn.close()


@pg
async def test_a_reduce_and_an_exit_in_that_window_wait_and_go_out(monkeypatch):
    """PROBE D, INVERTED, and the REDUCE the reviewer named. No stop; the
    same snapshot behind our books. A paper REDUCE of half, then the paper
    EXIT of the rest, both decided before the next snapshot: each WAITS
    (PLANNED, nothing sent) and goes out once the next snapshot agrees --
    no ORPHAN_CLOSE needed. cfaf0844: both EXCLUDED VENUE_POSITION_DISAGREES_
    WITH_MIRROR_FILLS at once; the REDUCE was lost, the EXIT recovered only by
    an ORPHAN_CLOSE minutes after the paper position closed."""
    conn = await TE._conn()
    try:
        acct, venue, mirror, entry, prod, step, _ = \
            await _filled_before_the_poll_booked_it(conn, monkeypatch)
        step()
        await mirror.tick(conn)                         # the poll books the fill
        assert await R._held(conn, entry["group_id"]) == 2
        red = await TE._paper_order(conn, acct, role="REDUCE", direction="SELL",
                                    intent="ORDER_INTENT_SELL_LONG", qty=1000,
                                    group=entry["group_id"])
        step()
        await mirror.tick(conn)
        ex = await TE._paper_order(conn, acct, role="EXIT", direction="SELL",
                                   intent="ORDER_INTENT_SELL_LONG", qty=1000,
                                   group=entry["group_id"])
        venue.behaviour = [{"fill": 1}, {"fill": 1}]
        for _ in range(10):
            step()
            await mirror.tick(conn)
        for po in (red, ex):
            r = await TE._row(conn, po["order_id"])
            assert (r["state"], r["exclusion"], r["live_qty"]) == ("PLANNED", None, 1), r
            assert _j(r["detail"])["risk_reducing_sell"]["awaiting"] == DISAGREES
        assert prod.rec.created == []
        assert (await M.live_inventory(conn, entry["group_id"]))["committed"] == 2
        for _ in range(25):                             # past the next snapshot
            step()
            await mirror.tick(conn)
        for po in (red, ex):
            r = await TE._row(conn, po["order_id"])
            assert (r["state"], r["exclusion"]) == ("FILLED", None), r
            assert _j(r["detail"])["risk_reducing_sell"]["admitted"] is True
        assert _sells(prod) == [("ORDER_INTENT_SELL_LONG", 1, IOC)] * 2
        assert await R._held(conn, entry["group_id"]) == 0
        assert venue.positions() == {}                  # flat, no short
        assert await conn.fetchval("SELECT count(*) FROM execmirror_orders"
                                   " WHERE role = 'ORPHAN_CLOSE'") == 0
    finally:
        await conn.close()


@pg
async def test_a_newer_snapshot_that_still_names_the_market_ends_the_wait(monkeypatch):
    """THE WAIT IS FOR ONE NEWER SNAPSHOT. The venue holds 1 contract more
    than the lane ever bought (a purchase outside the lane): the next
    snapshot still names the market (venue 3, our fills 2), and the waiting
    REDUCE is then refused by that name -- nothing sent, the wait kept beside
    the refusal, its inventory released."""
    conn = await TE._conn()
    try:
        acct, venue, mirror, entry, prod, step, _ = \
            await _filled_before_the_poll_booked_it(conn, monkeypatch, foreign=1)
        step()
        await mirror.tick(conn)
        red = await TE._paper_order(conn, acct, role="REDUCE", direction="SELL",
                                    intent="ORDER_INTENT_SELL_LONG", qty=1000,
                                    group=entry["group_id"])
        snaps = []
        for _ in range(32):
            step()
            out = await mirror.tick(conn)
            if "snapshot" in out:
                snaps.append(out["snapshot"])
        assert snaps == [{"reconciled": False, "differences": {entry["slug"]: {
            "mirror_fills_net": 2, "baseline": 0, "venue": 3, "signed": True}}}]
        r = await TE._row(conn, red["order_id"])
        assert (r["state"], r["exclusion"]) == ("EXCLUDED", DISAGREES), r
        rr = _j(r["detail"])["risk_reducing_sell"]
        assert rr["refused"] == DISAGREES and "wait_expired" not in rr
        assert rr["awaited"]["awaiting"] == DISAGREES
        assert rr["venue_position"]["account_snapshot_id"] > \
            rr["awaited"]["waiting_on_snapshot_id"]
        assert prod.rec.created == []
        assert (await M.live_inventory(conn, entry["group_id"]))["committed"] == 0
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §3 NOT EVIDENCED IS A WAIT, NOT A REFUSAL
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_sell_whose_market_is_not_evidenced_waits_and_the_same_order_goes_out(
        monkeypatch):
    """The account reads fail after the resume (the first RUNNING tick's one
    attempt fails), so the newest snapshot predates the close-position: the
    paper EXIT's live order WAITS (PLANNED, the evidence on the row, ONE
    event), nothing sent, and the account is not read again on every tick.
    When the reads recover, the next snapshot agrees (the close did not
    trade) and that same order goes out, its wait kept beside its admission.
    cfaf0844: EXCLUDED on the first tick and never sent."""
    conn = await TE._conn()
    try:
        venue = TE.FakeVenue()                          # close: 2xx, no trade
        acct, mirror, entry = await R2._held_then_stopped_with_flatten(
            conn, monkeypatch, venue)
        clock, step = _stepping_clock()
        mirror._now = clock
        prod = R.ProdPlace(venue)
        mirror._venue = prod
        real_balances = venue.balances
        R2._account_reads_fail(venue)
        ex = await R2._exit(conn, acct, entry)
        for _ in range(5):
            step()
            await mirror.tick(conn)
        r = await TE._row(conn, ex["order_id"])
        assert (r["state"], r["exclusion"], r["live_qty"]) == ("PLANNED", None, 2), r
        assert r["attempts"] == 0                       # a wait is no attempt
        rr = _j(r["detail"])["risk_reducing_sell"]
        assert rr["awaiting"] == NOT_EVIDENCED
        assert rr["venue_position"]["flatten_requests_since_snapshot"] == 1
        (ev,) = await R._events(conn, M.AWAITING_VENUE_EVIDENCE, r["mirror_id"])
        assert ev["awaiting"] == NOT_EVIDENCED
        assert len(await R._events(conn, "SNAPSHOT_FAILED")) == 1
        # still committed: nothing else may sell those contracts meanwhile
        assert (await M.live_inventory(conn, entry["group_id"]))["committed"] == 2
        assert prod.rec.created == []
        venue.balances = real_balances
        venue.behaviour = [{"fill": 2}]
        step(M.SNAPSHOT_EVERY_S)
        out = await mirror.tick(conn)
        assert out["snapshot"] == {"reconciled": True, "differences": {}}, out
        step()
        await mirror.tick(conn)
        r = await TE._row(conn, ex["order_id"])
        assert r["state"] == "FILLED", r
        rr = _j(r["detail"])["risk_reducing_sell"]
        assert rr["admitted"] is True and "awaiting" not in rr
        assert rr["awaited"]["awaiting"] == NOT_EVIDENCED
        assert rr["waited_s"] >= M.SNAPSHOT_EVERY_S
        assert _sells(prod) == [("ORDER_INTENT_SELL_LONG", 2, IOC)]
        assert await R._held(conn, entry["group_id"]) == 0
        assert venue.positions() == {}
    finally:
        await conn.close()


@pg
async def test_a_protection_waiting_for_evidence_spends_one_row(monkeypatch):
    """The live protection after a resume whose snapshot fails: ONE child
    row, waiting (PLANNED), over 40 s of ticks -- not a row per tick -- and
    it is sent once a snapshot evidences the market. cfaf0844: 11 rows
    EXCLUDED, MAX_PROTECTION_ROWS spent, none ever sent."""
    conn = await TE._conn()
    try:
        venue = TE.FakeVenue()                          # close: 2xx, no trade
        clock, step = _stepping_clock()
        acct, mirror, prod, entry, prot = await _held_with_protection(
            conn, monkeypatch, venue, clock)
        await _stop_with_flatten_then_resume(conn, mirror)
        real_balances = venue.balances
        R2._account_reads_fail(venue)
        for _ in range(20):
            step()
            await mirror.tick(conn)
        rows = await _protection_rows(conn)
        assert [(x["state"], x["exclusion"]) for x in rows] == [
            ("CANCELLED", None), ("PLANNED", None)], rows
        assert _j(rows[1]["detail"])["risk_reducing_sell"]["awaiting"] == NOT_EVIDENCED
        assert len(await R._events(conn, M.AWAITING_VENUE_EVIDENCE)) == 1
        venue.balances = real_balances
        step(M.SNAPSHOT_EVERY_S)
        await mirror.tick(conn)
        step()
        await mirror.tick(conn)
        rows = await _protection_rows(conn)
        assert [(x["state"], x["live_qty"]) for x in rows] == [
            ("CANCELLED", 2), ("OPEN", 2)], rows
        assert _sells(prod) == [("ORDER_INTENT_SELL_LONG", 2, GTD)] * 2
    finally:
        await conn.close()


@pg
async def test_waiting_sells_never_hold_up_another_planned_order(monkeypatch):
    """Ten risk-reducing SELLs wait for evidence on ten markets (a
    close-position requested on each after the newest snapshot) and fill the
    pass's batch of ten; an evidenced EXIT planned after them on an
    eleventh market is still sent on the first pass."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        await mirror.snapshot(conn, await M.control(conn))
        R._live_mode(monkeypatch)
        prod = R.ProdPlace(venue)
        mirror._venue = prod

        async def held_one(i, slug, *, flatten):
            gid = "grp_wait_%02d" % i
            await conn.execute(
                """INSERT INTO execmirror_orders (mirror_id, group_id, role, strategy,
                       us_market_slug, intent, order_type, tif, wire_price, live_qty,
                       state, venue_order_id, cum_qty)
                   VALUES ($1, $2, 'ENTRY', 'PINNACLE_COMPLETED_GAME_PAPER', $3,
                           'ORDER_INTENT_BUY_LONG', 'MARKETABLE', 'IOC', 0.50, 1,
                           'FILLED', $4, 1)""",
                "em:held:%02d" % i, gid, slug, "vh%02d" % i)
            await conn.execute(
                """INSERT INTO execmirror_fills (fill_key, mirror_id, venue_order_id,
                       group_id, us_market_slug, intent, qty, price)
                   VALUES ($1, $2, $3, $4, $5, 'ORDER_INTENT_BUY_LONG', 1, 0.50)""",
                "vh%02d:1" % i, "em:held:%02d" % i, "vh%02d" % i, gid, slug)
            vid = venue._new({"marketSlug": slug, "intent": "ORDER_INTENT_BUY_LONG",
                              "quantity": 1, "price": {"value": "0.50"}, "tif": "x"})
            venue.fill(vid, 1)
            if flatten:
                await M._event(conn, M.FLATTEN_REQUESTED, slug=slug, net="1")
            o = {"order_id": None, "group_id": gid, "us_market_slug": slug,
                 "intent": "ORDER_INTENT_SELL_LONG", "order_type": "MARKETABLE",
                 "time_in_force": "IOC", "wire_price": 0.40, "qty": None,
                 "decided_at": None, "strategy": None}
            await mirror._insert(conn, o, M.Plan("PLANNED", live_qty=1,
                                                 params=M.venue_params(o, 1)),
                                 mirror_id="em:exit:%02d" % i, role="EXIT")
            return "em:exit:%02d" % i

        waiting = [await held_one(i, "mlb-wait-%04d" % i, flatten=True)
                   for i in range(10)]
        # the first pass records the ten waits (it reaches nothing else)
        await mirror.submit_planned(conn)
        states = {r["mirror_id"]: r["state"] for r in await conn.fetch(
            "SELECT mirror_id, state FROM execmirror_orders WHERE role = 'EXIT'")}
        assert all(states[m] == "PLANNED" for m in waiting), states
        later = await held_one(10, "mlb-free-0010", flatten=False)
        venue.behaviour = [{"fill": 1}]
        await mirror.submit_planned(conn)
        st = await conn.fetchval("SELECT state FROM execmirror_orders"
                                 " WHERE mirror_id = $1", later)
        assert st == "FILLED", st
        assert [p["marketSlug"] for p in prod.rec.created] == ["mlb-free-0010"]
        assert await conn.fetchval(
            "SELECT count(*) FROM execmirror_orders WHERE mirror_id = ANY($1::text[])"
            " AND state = 'PLANNED'", waiting) == 10
    finally:
        await conn.close()


@pg
async def test_a_stop_excludes_a_waiting_protection_and_the_resume_plans_it_at_once(
        monkeypatch):
    """A stop still excludes every PLANNED row, a waiting one included
    (EMERGENCY_STOP). That exclusion is not a refusal on the venue's
    evidence: after the resume the protection is planned again at once and
    sent (no minute's pause)."""
    conn = await TE._conn()
    try:
        venue = TE.FakeVenue()                          # close: 2xx, no trade
        acct, mirror, prod, entry, prot = await _held_with_protection(
            conn, monkeypatch, venue, time.time)
        await _stop_with_flatten_then_resume(conn, mirror)
        real_balances = venue.balances
        R2._account_reads_fail(venue)
        for _ in range(3):
            await mirror.tick(conn)
        assert (await _protection_rows(conn))[-1]["state"] == "PLANNED"
        await _stop_with_flatten_then_resume(conn, mirror)
        rows = await _protection_rows(conn)
        assert (rows[-1]["state"], rows[-1]["exclusion"]) == (
            "EXCLUDED", "EMERGENCY_STOP"), rows
        venue.balances = real_balances
        for _ in range(2):
            await mirror.tick(conn)
        rows = await _protection_rows(conn)
        assert [(x["state"], x["exclusion"]) for x in rows] == [
            ("CANCELLED", None), ("EXCLUDED", "EMERGENCY_STOP"),
            ("OPEN", None)], rows
    finally:
        await conn.close()


@pg
async def test_the_wait_is_bounded(monkeypatch):
    """BOUNDED: while no snapshot can be taken (the account reads keep
    failing), a waiting REDUCE is refused by the code it waited on once it
    has waited SELL_EVIDENCE_WAIT_MAX_S -- as long as an account snapshot is
    admissible evidence at all -- nothing sent, its inventory released, and
    the snapshot is attempted at the usual cadence meanwhile (not on every
    tick)."""
    assert M.SELL_EVIDENCE_WAIT_MAX_S == M.ACCOUNT_SNAPSHOT_MAX_AGE_S
    conn = await TE._conn()
    try:
        venue = TE.FakeVenue()                          # close: 2xx, no trade
        acct, mirror, entry = await R2._held_then_stopped_with_flatten(
            conn, monkeypatch, venue)
        clock, step = _stepping_clock()
        mirror._now = clock
        prod = R.ProdPlace(venue)
        mirror._venue = prod
        R2._account_reads_fail(venue)
        red = await TE._paper_order(conn, acct, role="REDUCE", direction="SELL",
                                    intent="ORDER_INTENT_SELL_LONG", qty=1000,
                                    group=entry["group_id"])
        step()
        await mirror.tick(conn)
        r = await TE._row(conn, red["order_id"])
        assert r["state"] == "PLANNED", r
        since = _j(r["detail"])["risk_reducing_sell"]["awaiting_since_epoch_s"]
        for _ in range(3):                              # 60 s apart: 180 s
            step(M.SNAPSHOT_EVERY_S)
            await mirror.tick(conn)
            r = await TE._row(conn, red["order_id"])
            assert r["state"] == "PLANNED", r
        assert clock() - since == M.SELL_EVIDENCE_WAIT_MAX_S
        assert len(await R._events(conn, "SNAPSHOT_FAILED")) == 4
        step()
        await mirror.tick(conn)
        r = await TE._row(conn, red["order_id"])
        assert (r["state"], r["exclusion"]) == ("EXCLUDED", NOT_EVIDENCED), r
        rr = _j(r["detail"])["risk_reducing_sell"]
        assert rr["wait_expired"] is True and rr["refused"] == NOT_EVIDENCED
        assert rr["awaited"]["awaiting"] == NOT_EVIDENCED
        assert rr["waited_s"] > M.SELL_EVIDENCE_WAIT_MAX_S
        assert r["attempts"] == 0
        assert len(await R._events(conn, M.AWAITING_VENUE_EVIDENCE,
                                   r["mirror_id"])) == 1
        assert prod.rec.created == []
        assert (await M.live_inventory(conn, entry["group_id"]))["committed"] == 0
        assert await R._held(conn, entry["group_id"]) == 2
    finally:
        await conn.close()


@pg
async def test_a_waiting_protection_child_is_never_sent_after_its_paper_protection_ended(
        monkeypatch):
    """A protection child (resync_protection; no paper_order_id of its own)
    waits for evidence; meanwhile its paper protection is cancelled. When a
    snapshot evidences the market, the child is EXCLUDED
    PAPER_ORDER_ENDED_BEFORE_SUBMIT -- the same revalidation as the row of
    the paper order itself -- and nothing is sent."""
    conn = await TE._conn()
    try:
        venue = TE.FakeVenue()                          # close: 2xx, no trade
        clock, step = _stepping_clock()
        acct, mirror, prod, entry, prot = await _held_with_protection(
            conn, monkeypatch, venue, clock)
        created_before = len(prod.rec.created)
        await _stop_with_flatten_then_resume(conn, mirror)
        real_balances = venue.balances
        R2._account_reads_fail(venue)
        for _ in range(3):
            step()
            await mirror.tick(conn)
        child = (await _protection_rows(conn))[-1]
        assert (child["state"], child["paper_order_id"]) == ("PLANNED", None), child
        await conn.execute("UPDATE paper_orders SET state = 'CANCELED'"
                           " WHERE order_id = $1", prot["order_id"])
        venue.balances = real_balances
        step(M.SNAPSHOT_EVERY_S)
        await mirror.tick(conn)
        step()
        await mirror.tick(conn)
        rows = await _protection_rows(conn)
        assert [(x["state"], x["exclusion"]) for x in rows] == [
            ("CANCELLED", None), ("EXCLUDED", "PAPER_ORDER_ENDED_BEFORE_SUBMIT")], rows
        assert len(prod.rec.created) == created_before
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §4 A REFUSAL ON THE VENUE'S EVIDENCE NEVER SPENDS MAX_PROTECTION_ROWS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_refusals_on_the_venues_evidence_never_spend_the_protection_rows(monkeypatch):
    """The venue's positions read lags (it shows the market flat while the
    lane holds 2): every snapshot names the market with the venue holding
    LESS, so each protection attempt is refused at once, one a minute.
    After more such refusals than MAX_PROTECTION_ROWS the read catches up,
    the next snapshot agrees, and the protection goes out. cfaf0844 (and a
    pause that still counted those rows): the protection stopped at
    MAX_PROTECTION_ROWS rows and never came back."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        clock, step = _stepping_clock(M.PROTECTION_PAUSE_S + 1)
        mirror._now = clock
        entry = await TE._paper_order(conn, acct, qty=2000)
        venue.behaviour = [{"fill": 2}]
        await mirror.tick(conn)
        await TE._paper_fill(conn, acct, entry, qty=2000)
        assert await R._held(conn, entry["group_id"]) == 2
        venue.positions = lambda: {}                    # the read lags
        rec = await mirror.snapshot(conn, await M.control(conn))
        assert rec["differences"][entry["slug"]]["venue"] is None
        R._live_mode(monkeypatch)
        prod = R.ProdPlace(venue)
        mirror._venue = prod
        await TE._paper_order(conn, acct, role="STANDING_PROTECTION",
                              direction="SELL", intent="ORDER_INTENT_SELL_LONG",
                              qty=2000, group=entry["group_id"], tif="GTD",
                              otype="RESTING", state="RESTING")
        n = M.MAX_PROTECTION_ROWS + 2
        for _ in range(n):                              # one refusal a minute
            step()
            await mirror.tick(conn)
        rows = await _protection_rows(conn)
        refused = [x for x in rows if (x["state"], x["exclusion"]) == (
            "EXCLUDED", DISAGREES)]
        assert len(refused) >= n > M.MAX_PROTECTION_ROWS, rows
        assert prod.rec.created == []
        del venue.positions                             # the read catches up
        for _ in range(2):
            step()
            await mirror.tick(conn)
        rows = await _protection_rows(conn)
        assert (rows[-1]["state"], rows[-1]["live_qty"]) == ("OPEN", 2), rows
        assert all(x["state"] == "EXCLUDED" for x in rows[:-1]), rows
        assert _sells(prod) == [("ORDER_INTENT_SELL_LONG", 2, GTD)]
    finally:
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §5 SHADOW, AS SHIPPED, IS UNCHANGED
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_in_shadow_a_resume_takes_no_extra_snapshot_and_nothing_reaches_the_client(
        monkeypatch):
    """SMALL LIVE IS SHADOW (as shipped): the same stop with flatten and
    resume, a standing protection and a paper REDUCE. The first RUNNING tick
    takes no snapshot (SHADOW keeps its cadence), no create reaches the
    client, and the REDUCE is claimed and refused by the adapter exactly as
    before (REJECTED LegacyOriginationRetired, no admission recorded)."""
    conn = await TE._conn()
    try:
        assert M.small_live_is_shadow()
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        entry = await TE._paper_order(conn, acct, qty=2000)
        venue.behaviour = [{"fill": 2}]
        await mirror.tick(conn)
        await TE._paper_fill(conn, acct, entry, qty=2000)
        prod = R.ProdPlace(venue)
        mirror._venue = prod
        await TE._paper_order(conn, acct, role="STANDING_PROTECTION",
                              direction="SELL", intent="ORDER_INTENT_SELL_LONG",
                              qty=2000, group=entry["group_id"], tif="GTD",
                              otype="RESTING", state="RESTING")
        for _ in range(3):
            await mirror.tick(conn)
        await _stop_with_flatten_then_resume(conn, mirror)
        reads = []
        real = venue.balances
        venue.balances = lambda: reads.append(1) or real()
        red = await TE._paper_order(conn, acct, role="REDUCE", direction="SELL",
                                    intent="ORDER_INTENT_SELL_LONG", qty=1000,
                                    group=entry["group_id"])
        out = await mirror.tick(conn)
        assert "snapshot" not in out and reads == [], out
        for _ in range(5):
            await mirror.tick(conn)
        r = await TE._row(conn, red["order_id"])
        assert r["state"] == "REJECTED", r
        assert _j(r["error"])["error"] == "LegacyOriginationRetired"
        assert "risk_reducing_sell" not in _j(r["detail"])
        assert prod.rec.created == []
        assert await R._events(conn, M.AWAITING_VENUE_EVIDENCE) == []
    finally:
        await conn.close()


@pg
async def test_in_shadow_a_snapshot_behind_our_books_changes_nothing(monkeypatch):
    """SHADOW (as shipped), the probe-C world: the admission is never
    reached, so nothing waits -- the paper REDUCE's live order is claimed
    and refused by the adapter exactly as before (REJECTED
    LegacyOriginationRetired, no admission recorded), and no create reaches
    the client."""
    conn = await TE._conn()
    try:
        assert M.small_live_is_shadow()
        acct, venue, mirror, entry, prod, step, _ = \
            await _filled_before_the_poll_booked_it(conn, monkeypatch, live=False)
        step()
        await mirror.tick(conn)                         # the poll books the fill
        red = await TE._paper_order(conn, acct, role="REDUCE", direction="SELL",
                                    intent="ORDER_INTENT_SELL_LONG", qty=1000,
                                    group=entry["group_id"])
        for _ in range(5):
            step()
            await mirror.tick(conn)
        r = await TE._row(conn, red["order_id"])
        assert r["state"] == "REJECTED", r
        assert _j(r["error"])["error"] == "LegacyOriginationRetired"
        assert not await conn.fetchval(
            "SELECT count(*) FROM execmirror_orders"
            " WHERE detail ? 'risk_reducing_sell'")
        assert prod.rec.created == []
        assert await R._events(conn, M.AWAITING_VENUE_EVIDENCE) == []
    finally:
        await conn.close()
