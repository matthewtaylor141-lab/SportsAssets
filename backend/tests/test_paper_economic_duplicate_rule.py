"""CAPITAL-CRITICAL: THE ECONOMIC-DUPLICATE RULE OF THE PAPER RECONCILIATION
RECEIPT, BUILT FROM PRODUCTION'S OWN 18 GROUPS (RC6).

Production pm-acceptance 37836393458 (release 69a8a07e, 2026-10-08 20:10Z)
read `economic_duplicate_suspect_groups` 18, extra 3,732.79 -- the scorecard's
Archer unit `no_economic_duplicate_suspects`. The P0 rule grouped fills by
(order, qty, price, instant) and never looked at the level or the book
observation. A fill id is sha256 of `<order>:obs<obs>:<wire>` (the simulator's
idempotency key), so each production fill's wire level is recovered from its
id alone (pinned below against the ledger's own `fill_id_for`). With the
level known, the 18 groups are:

  * 13 refills of ONE level of one order on 2-5 observations in one step --
    true economic duplicates, extra 3,402.79, all filled 2026-10-02 ..
    2026-10-05 22:17:55Z, before the seen-crossing fix (221ce6b9,
    2026-10-06 05:16:06Z): HISTORICAL, listed, never rewritten;
  * 7 groups of DIFFERENT levels of the same displayed size taken in one
    step at the order's limit (one after the fix: 100 @ 0.75 from offers
    0.18 and 0.19 on observation 67419) -- not duplicates.

Proven here: the pure split on the production shapes; a refill AT OR AFTER
the fix is CURRENT (the gate cannot be hidden by the window); and on a real
ledger, the fixed simulator on production's own 5-observation shape fills
each level once and the receipt names no duplicate, while a pre-fix-style
refill (the old producer's own `_apply_takes`, observation by observation)
is HISTORICAL before the fix and CURRENT after it.
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_reconciliation as REC
from sportsassets import bettor_paper_simulator as SIM
from sportsassets.agents import paper_xavier as PX

from tests import paper_harness as H
from tests import test_xavier_review_probability_freshness as XRF

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

#: production's 18 groups (paper_reconciliation.json, pm-acceptance
#: 37836393458): order, group, market, side, direction, qty, price (the
#: order's limit), instant, and [(book observation, wire level)] per fill,
#: the level recovered from the fill id
PRODUCTION_GROUPS = (
    ("f35bb6ee714d610ca664d33f", "papergrp:e68836877c3a1a448392cee0",
     "aec-kbo-khi-kwt-2026-10-06", "SHORT", "SELL", 100.0, 0.75,
     "2026-10-06T10:19:27.964966Z",
     [(67419, 0.19), (67419, 0.18)]),
    ("c07c30f51e26a4d10581de67", "papergrp:425640007467eeae355634ae",
     "atc-cnl-mtq-slv-2026-10-05-mtq", "SHORT", "SELL", 6.0, 0.67,
     "2026-10-05T22:20:32.889784Z",
     [(47863, 0.29), (47863, 0.3)]),
    ("96afc3f9a9592192d6bcbba6", "paperexpgrp:cc66c2c366c8aef8a6e7fb9f",
     "atc-cnl-mtq-slv-2026-10-05-mtq", "SHORT", "SELL", 5.0, 0.66,
     "2026-10-05T22:17:55.651934Z",
     [(47825, 0.33), (47826, 0.33), (47828, 0.33)]),
    ("96afc3f9a9592192d6bcbba6", "paperexpgrp:cc66c2c366c8aef8a6e7fb9f",
     "atc-cnl-mtq-slv-2026-10-05-mtq", "SHORT", "SELL", 104.0, 0.66,
     "2026-10-05T22:17:55.651934Z",
     [(47825, 0.32), (47826, 0.32), (47828, 0.32)]),
    ("96afc3f9a9592192d6bcbba6", "paperexpgrp:cc66c2c366c8aef8a6e7fb9f",
     "atc-cnl-mtq-slv-2026-10-05-mtq", "SHORT", "SELL", 323.49, 0.66,
     "2026-10-05T22:17:55.651934Z",
     [(47825, 0.31), (47826, 0.31), (47828, 0.31)]),
    ("62c73a8f60f8ec214dc3f0a5", "paperexpgrp:d360f05737f3a24551b00c8c",
     "atc-unl-mne-arm-2026-10-05-mne", "SHORT", "SELL", 3.0, 0.56,
     "2026-10-05T20:09:23.878197Z",
     [(46247, 0.43), (46248, 0.43)]),
    ("84a543eb290cb7be9e104ac0", "papergrp:eab5a64434fae7460fa21be3",
     "atc-unl-ukr-hun-2026-10-05-ukr", "SHORT", "SELL", 1.0, 0.69,
     "2026-10-05T19:54:31.268251Z",
     [(46172, 0.23), (46172, 0.21), (46172, 0.22), (46173, 0.18)]),
    ("94bf0e34989e6f663a5ebc1d", "paperexpgrp:55be1f6cb47c55f648c78b2b",
     "atc-intf-rwa-ken-2026-10-05-rwa", "SHORT", "SELL", 1.0, 0.83,
     "2026-10-05T17:56:52.129258Z",
     [(44825, 0.16), (44825, 0.14), (44825, 0.15), (44826, 0.16),
      (44826, 0.15), (44826, 0.14), (44827, 0.15), (44827, 0.16),
      (44827, 0.14), (44828, 0.14), (44828, 0.15), (44828, 0.16),
      (44829, 0.15), (44829, 0.14), (44829, 0.16)]),
    ("94bf0e34989e6f663a5ebc1d", "paperexpgrp:55be1f6cb47c55f648c78b2b",
     "atc-intf-rwa-ken-2026-10-05-rwa", "SHORT", "SELL", 7.0, 0.83,
     "2026-10-05T17:56:52.129258Z",
     [(44825, 0.13), (44826, 0.13), (44827, 0.13), (44828, 0.13),
      (44829, 0.13)]),
    ("94bf0e34989e6f663a5ebc1d", "paperexpgrp:55be1f6cb47c55f648c78b2b",
     "atc-intf-rwa-ken-2026-10-05-rwa", "SHORT", "SELL", 15.0, 0.83,
     "2026-10-05T17:56:52.129258Z",
     [(44825, 0.12), (44826, 0.12), (44827, 0.12), (44828, 0.12),
      (44829, 0.12)]),
    ("94bf0e34989e6f663a5ebc1d", "paperexpgrp:55be1f6cb47c55f648c78b2b",
     "atc-intf-rwa-ken-2026-10-05-rwa", "SHORT", "SELL", 36.0, 0.83,
     "2026-10-05T17:56:52.129258Z",
     [(44825, 0.11), (44826, 0.11), (44827, 0.11), (44828, 0.11),
      (44829, 0.11)]),
    ("94bf0e34989e6f663a5ebc1d", "paperexpgrp:55be1f6cb47c55f648c78b2b",
     "atc-intf-rwa-ken-2026-10-05-rwa", "SHORT", "SELL", 142.0, 0.83,
     "2026-10-05T17:56:52.129258Z",
     [(44825, 0.1), (44826, 0.1), (44827, 0.1), (44828, 0.1), (44829, 0.1)]),
    ("bd296cafc26e5fd22d01db7d", "paperexpgrp:1a27369ede2c3f8db290417d",
     "atc-intf-jor-ven-2026-10-06-jor", "LONG", "SELL", 539.0, 0.4,
     "2026-10-05T14:07:34.418665Z",
     [(40623, 0.41), (40624, 0.41), (40625, 0.41), (40626, 0.41)]),
    ("5c9f045a7a597ae9359cebf4", "paperexpgrp:c2d980d0163ce61a219993a1",
     "atc-u19f-wal-nir-2026-10-05-wal", "SHORT", "SELL", 1.0, 0.42,
     "2026-10-05T10:51:25.412118Z",
     [(37261, 0.14), (37261, 0.13)]),
    ("5c9f045a7a597ae9359cebf4", "paperexpgrp:c2d980d0163ce61a219993a1",
     "atc-u19f-wal-nir-2026-10-05-wal", "SHORT", "SELL", 5.0, 0.42,
     "2026-10-05T10:51:25.412118Z",
     [(37261, 0.09), (37261, 0.1), (37261, 0.07), (37261, 0.06)]),
    ("dd954687a4007847e1ebc270", "papercggrp:953535d3fe3673ec87a14826",
     "aec-npb-clm-ssl-2026-10-05", "SHORT", "SELL", 105.81, 0.52,
     "2026-10-05T10:36:23.636410Z",
     [(36939, 0.47), (36940, 0.47)]),
    ("49d199d5412c3a0e396a39f0", "papercggrp:dd96b640671d655c10554997",
     "atc-brb-acg-afc-2026-10-03-acg", "LONG", "SELL", 100.0, 0.86,
     "2026-10-03T20:15:19.762839Z",
     [(19284, 0.94), (19284, 0.95), (19284, 0.93)]),
    ("57612207b7265210aec4681c", "paperexpgrp:a51a5f893f337758a2e88174",
     "atc-brb-ber-crb-2026-10-02-ber", "LONG", "SELL", 3.0, 0.42,
     "2026-10-02T23:54:38.020519Z",
     [(10434, 0.59), (10434, 0.63)]),
)

#: production fill ids, verbatim, for the two shapes: two levels on ONE
#: observation (f35bb6ee, after the fix) and ONE level on two observations
#: (dd954687, the P0 directive's 105.81 example)
PRODUCTION_FILL_IDS = {
    ("f35bb6ee714d610ca664d33f", 67419, 0.19):
        "paperfill:2f34df84584a96b9d4678b3b",
    ("f35bb6ee714d610ca664d33f", 67419, 0.18):
        "paperfill:3d95356b8a2b4fcbef236f01",
    ("dd954687a4007847e1ebc270", 36939, 0.47):
        "paperfill:07bbba0657a3c7e737345d53",
    ("dd954687a4007847e1ebc270", 36940, 0.47):
        "paperfill:20a325831b22e791a4a6b85a",
}


def _epoch(iso: str) -> float:
    import datetime as dt
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _fills(groups=PRODUCTION_GROUPS, *, shift_s: float = 0.0) -> list:
    """The candidate rows DUPLICATE_CANDIDATES_SQL returns, one per fill."""
    out = []
    for (oid, gid, slug, side, direction, qty, price, at, levels) in groups:
        t = _epoch(at) + shift_s
        for obs, wire in levels:
            key = "paperord:%s:obs%d:%s" % (oid, obs, SIM._wk(wire))
            out.append({"fill_id": L.fill_id_for(key),
                        "order_id": "paperord:" + oid,
                        "role": "STANDING_PROTECTION", "group_id": gid,
                        "us_market_slug": slug, "holding_side": side,
                        "direction": direction, "qty": qty, "price": price,
                        "wire_price": wire, "filled_at": "%.6f" % t,
                        "filled_epoch": t, "book_obs_id": obs})
    return out


# ═════════════════════════════════════════════════════════════════════
# PURE, ON PRODUCTION'S SHAPES
# ═════════════════════════════════════════════════════════════════════

def test_a_production_fill_id_names_its_level():
    """The level of every production fill is recoverable from its id: the
    simulator's key `<order>:obs<obs>:<wire>` hashed by the ledger."""
    for (oid, obs, wire), fid in PRODUCTION_FILL_IDS.items():
        key = "paperord:%s:obs%d:%s" % (oid, obs, SIM._wk(wire))
        assert L.fill_id_for(key) == fid
    # 71 fills in all, as the production receipt listed
    assert len(_fills()) == 71


def test_production_18_groups_are_13_historical_refills_and_7_distinct():
    got = REC.classify_duplicates(_fills())
    assert got["former_rule_groups"] == 18
    assert got["current"] == []
    assert len(got["historical"]) == 13
    assert round(sum(g["extra_qty"] for g in got["historical"]), 6) == \
        pytest.approx(3402.79)
    assert len(got["distinct_levels"]) == 7
    assert got["former_rule_groups_with_a_refill"] == 11
    # every historical refill is one level on distinct observations, all
    # before the producer fix; the newest is 2026-10-05 22:17:55Z
    for g in got["historical"]:
        assert len(set(g["wires"])) == 1
        assert len(set(g["obs_ids"])) == g["n"] >= 2
        assert g["filled_epoch"] < REC.PRODUCER_FIX_EFFECTIVE_AT
        assert g["label"] == REC.L_HISTORICAL
    assert max(g["filled_epoch"] for g in got["historical"]) == \
        pytest.approx(_epoch("2026-10-05T22:17:55.651934Z"))
    # the P0 directive's example: 105.81 @ 0.52 twice, one level 0.47
    (ex,) = [g for g in got["historical"] if g["qty"] == 105.81]
    assert ex["n"] == 2 and ex["wire"] == 0.47 and ex["extra_qty"] == 105.81
    # the one group after the fix: two different levels, one observation
    (after,) = [g for g in got["distinct_levels"]
                if g["filled_epoch"] >= REC.PRODUCER_FIX_EFFECTIVE_AT]
    assert after["order_id"] == "paperord:f35bb6ee714d610ca664d33f"
    assert after["obs_ids"] == [67419, 67419]
    assert sorted(after["wires"]) == [0.18, 0.19]
    assert after["label"] == REC.L_DISTINCT


def test_a_refill_at_or_after_the_fix_is_current_never_hidden_by_the_window():
    """The SAME production shapes moved to after the fix are all CURRENT:
    the window labels history, it can never hide a new refill."""
    shift = REC.PRODUCER_FIX_EFFECTIVE_AT - _epoch("2026-10-02T00:00:00Z")
    got = REC.classify_duplicates(_fills(shift_s=shift))
    assert got["historical"] == []
    assert len(got["current"]) == 13
    assert all(g["label"] == REC.L_SUSPECT for g in got["current"])
    assert round(sum(g["extra_qty"] for g in got["current"]), 6) == \
        pytest.approx(3402.79)
    # a refill exactly AT the fix instant is current (fail closed)
    (oid, gid, slug, side, d, qty, px, _at, lv) = PRODUCTION_GROUPS[15]
    at_fix = [dict(f, filled_epoch=REC.PRODUCER_FIX_EFFECTIVE_AT)
              for f in _fills(((oid, gid, slug, side, d, qty, px,
                                "2026-10-05T10:36:23.636410Z", lv),))]
    got = REC.classify_duplicates(at_fix)
    assert len(got["current"]) == 1 and got["historical"] == []


def test_distinct_levels_on_one_observation_are_not_a_duplicate():
    (g,) = [x for x in PRODUCTION_GROUPS if x[0].startswith("f35bb6ee")]
    got = REC.classify_duplicates(_fills((g,)))
    assert got["current"] == [] and got["historical"] == []
    assert len(got["distinct_levels"]) == 1
    assert REC.classify_duplicates([]) == {
        "current": [], "historical": [], "distinct_levels": [],
        "former_rule_groups": 0, "former_rule_groups_with_a_refill": 0}


# ═════════════════════════════════════════════════════════════════════
# A REAL LEDGER (scratch test database; no network, no real order)
# ═════════════════════════════════════════════════════════════════════

AFTER_FIX = REC.PRODUCER_FIX_EFFECTIVE_AT + 3600.0


async def _protected_long(conn, tag: str, *, qty: float, at: float):
    a = await H.new_account(conn, tag, now=at)
    slug = "rc6-dup-%s" % a["account_id"][-10:]
    g = "paper_g_%s_rc6" % a["account_id"][-10:]
    o = H.order(a, key="e%s" % qty, qty=qty, limit=0.40, slug=slug, at=at,
                group_id=g)
    got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at)
    assert got["ok"], got
    await H.observe(conn, slug, at + 3, offers=[(0.40, qty)])
    await SIM.simulate_order(conn, got["order"]["order_id"], now=at + 4,
                             fee_fn=H.zero_fee)
    await PX.step_handoff(conn, XRF._ctx(a, at + 5))
    (oid,) = await H.protect(conn, XRF._ctx(a, at + 6), g, at=at + 6)
    return a, slug, g, oid


async def _dup_counts(conn, account_id, at):
    rc = await REC.receipt(conn, account_id, now=at)
    assert rc["status"] == "OK", rc
    c = rc["counts"]
    return rc, (c["economic_duplicate_suspect_groups"],
                c["historical_economic_duplicate_groups"],
                c["same_instant_distinct_level_groups"])


@pg
async def test_two_levels_of_one_size_on_one_observation_are_not_a_duplicate():
    """Production f35bb6ee after the fix: one protection took 100 from each
    of two crossing levels on ONE observation in one step. Distinct
    liquidity; the P0 rule called it a duplicate."""
    conn = await H.connect()
    try:
        a, slug, g, oid = await _protected_long(conn, "rc6two", qty=300,
                                                at=AFTER_FIX)
        await H.observe(conn, slug, AFTER_FIX + 10,
                        bids=[(0.95, 100), (0.94, 100)])
        await SIM.simulate_order(conn, oid, now=AFTER_FIX + 20,
                                 fee_fn=H.zero_fee)
        fills = await conn.fetch(
            "SELECT qty, wire_price, book_obs_id FROM paper_fills "
            " WHERE order_id=$1 ORDER BY wire_price", oid)
        assert [(float(f["qty"]), float(f["wire_price"])) for f in fills] \
            == [(100.0, 0.94), (100.0, 0.95)]
        assert len({f["book_obs_id"] for f in fills}) == 1
        rc, counts = await _dup_counts(conn, a["account_id"], AFTER_FIX + 30)
        assert counts == (0, 0, 1)
        assert rc["counts"]["economic_duplicate_suspect_extra_qty"] == 0
        (d,) = rc["sections"]["same_instant_distinct_levels"]
        assert d["order_id"] == oid and d["label"] == REC.L_DISTINCT
        assert sorted(d["wires"]) == [0.94, 0.95]
    finally:
        await conn.close()


@pg
async def test_the_fixed_simulator_on_productions_five_observation_shape():
    """Production 94bf0e34 (2026-10-05 17:56Z, before the fix): one step met
    the same seven crossing levels on five observations and filled every
    level five times. The fixed producer fills each level ONCE, and the
    receipt names no duplicate (the three 1-lots are three levels)."""
    levels = [(0.90, 142), (0.89, 36), (0.88, 15), (0.87, 7), (0.86, 1),
              (0.85, 1), (0.84, 1)]
    conn = await H.connect()
    try:
        a, slug, g, oid = await _protected_long(conn, "rc6five", qty=1000,
                                                at=AFTER_FIX)
        for k in range(5):
            await H.observe(conn, slug, AFTER_FIX + 10 + k, bids=levels)
        await SIM.simulate_order(conn, oid, now=AFTER_FIX + 20,
                                 fee_fn=H.zero_fee)
        fills = await conn.fetch(
            "SELECT qty, wire_price FROM paper_fills WHERE order_id=$1 "
            " ORDER BY wire_price DESC", oid)
        assert [(float(f["wire_price"]), float(f["qty"])) for f in fills] \
            == [(p, float(q)) for p, q in levels]
        rc, counts = await _dup_counts(conn, a["account_id"], AFTER_FIX + 30)
        assert counts == (0, 0, 1)
        (d,) = rc["sections"]["same_instant_distinct_levels"]
        assert d["qty"] == 1.0 and sorted(d["wires"]) == [0.84, 0.85, 0.86]
    finally:
        await conn.close()


async def _old_producer_refill(conn, oid: str, slug: str, *, at: float,
                               observations: int = 2) -> None:
    """THE PRE-FIX PRODUCER, emulated with its own writer: the resting step
    met the same crossing level on each observation and, with no memory,
    took it again through `_apply_takes` -- same order, level and instant,
    one fill per observation."""
    obs = [await H.observe(conn, slug, at - 5 + k, bids=[(0.95, 100)])
           for k in range(observations)]
    async with conn.transaction():
        acct = await conn.fetchval(
            "SELECT account_id FROM paper_orders WHERE order_id=$1", oid)
        await L._lock(conn, acct)
        for k, obs_id in enumerate(obs):
            o = await conn.fetchrow(
                "SELECT * FROM paper_orders WHERE order_id=$1", oid)
            await SIM._apply_takes(
                conn, o, takes=[{"wire": 0.95, "price": 0.95, "qty": 100,
                                 "take": 100.0}],
                obs={"obs_id": obs_id, "observed_at": at - 5 + k},
                basis=SIM.BASIS_CROSS, now=at, fee_fn=H.zero_fee,
                evidence={"emulates": "PRE_FIX_PRODUCER"})


@pg
async def test_a_refill_is_historical_before_the_fix_and_current_after_it():
    conn = await H.connect()
    try:
        before = REC.PRODUCER_FIX_EFFECTIVE_AT - 86400.0
        a1, slug1, _g1, oid1 = await _protected_long(conn, "rc6old",
                                                     qty=300, at=before)
        await _old_producer_refill(conn, oid1, slug1, at=before + 20)
        rc, counts = await _dup_counts(conn, a1["account_id"], before + 30)
        # history stays VISIBLE and counted on its own; it never reaches the
        # current count and is never rewritten
        assert counts == (0, 1, 0)
        assert rc["counts"]["historical_economic_duplicate_extra_qty"] == \
            pytest.approx(100.0)
        (h,) = rc["sections"]["historical_economic_duplicates"]
        assert h["order_id"] == oid1 and h["n"] == 2
        assert h["label"] == REC.L_HISTORICAL
        assert h["window"] == "BEFORE_PRODUCER_FIX"
        assert await conn.fetchval("SELECT count(*) FROM paper_fills WHERE "
                                   " order_id=$1", oid1) == 2
        rule = rc["sections"]["economic_duplicate_rule"]
        assert rule["producer_fix_effective_at"] == "2026-10-06T05:16:06Z"
        assert rule["producer_fix_release"].startswith("221ce6b9")

        a2, slug2, _g2, oid2 = await _protected_long(conn, "rc6new",
                                                     qty=300, at=AFTER_FIX)
        await _old_producer_refill(conn, oid2, slug2, at=AFTER_FIX + 20,
                                   observations=3)
        rc, counts = await _dup_counts(conn, a2["account_id"], AFTER_FIX + 30)
        assert counts == (1, 0, 0)
        assert rc["counts"]["economic_duplicate_suspect_extra_qty"] == \
            pytest.approx(200.0)
        (s,) = rc["sections"]["economic_duplicate_suspects"]
        assert s["order_id"] == oid2 and s["n"] == 3
        assert s["label"] == REC.L_SUSPECT
    finally:
        await conn.close()
