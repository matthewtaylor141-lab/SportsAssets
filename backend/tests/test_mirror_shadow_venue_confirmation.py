"""THE MIRROR SHADOW NEVER CALLS A LEDGER-DERIVED READING 'ok' (RC6 identity lane).

Production, release 732cc0c6 (pm-acceptance 37884042844, loop_health.json):
the mirror_shadow heartbeat read beat_status 'ok' on every tick while its
positions came from the funded ledger (positions_source.authority
LEDGER_DERIVED_NOT_VENUE_CONFIRMED; the PMUS slot holds the PMX RSA client,
PMUS_SECRET_SLOT_HOLDS_NO_ED25519_KEY). A reading the venue never confirmed
must carry an explicit, named, safe refusal -- the credential class is
unavailable and only the owner can provision it -- on the heartbeat, in the
capital-readiness gate's evidence and in loop health, never a venue-confirmed
claim. Only a venue walk that produced a reading may report 'ok'.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from sportsassets import loop_health as LH
from sportsassets import mirror_positions_source as MPS
from sportsassets import refusal_taxonomy_table as TT
from sportsassets.capital_readiness import feeds as F
from sportsassets.workers import mirror_shadow as MS
from tests import test_mirror_shadow as T
from tests import test_mirror_shadow_positions_source as P

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


def _run(coro):
    return asyncio.run(coro)


def _fresh(monkeypatch):
    """A fresh tick in a fresh PROCESS: no positions reading yet. The last
    reading's confirmation is process state (tick_once's skipped ticks carry
    it), so every test starts without one and gets the module's own value
    back afterwards."""
    P._fresh_tick(monkeypatch)
    monkeypatch.setattr(MS, "_last_confirmation", None, raising=False)


def _ledger_topology(monkeypatch):
    P._topology(monkeypatch, secret=P.RSA_PEM_B64, key_id=P.PMX_CLIENT,
                pmx_client=P.PMX_CLIENT)


def _venue_topology(monkeypatch):
    P._topology(monkeypatch, secret=P.ED25519,
                key_id="9f1c2d3e-0000-4000-8000-000000000001",
                pmx_client=P.PMX_CLIENT)


def _ledger_pool(**k):
    return P._LedgerPool(fills=T.HIS, ledger_rows_json=[
        {"slug": T.SLUG, "src": "live_orders", "net": 147.0, "n": 1}],
        ledger_rows=[{"sh": 147.0, "intent": "ORDER_INTENT_BUY_LONG"}],
        whales_ratio_fills=T._ratio_fills(), **k)


def _ledger_tick(monkeypatch):
    _fresh(monkeypatch)
    _ledger_topology(monkeypatch)
    return _run(MS.tick_once(_ledger_pool(), P._NoVenueWalk(bid=0.30, ask=0.32),
                             now_ts=5000.0))


# ── the heartbeat ─────────────────────────────────────────────────────

def test_a_ledger_derived_tick_heartbeats_degraded_with_the_named_refusal(
        monkeypatch):
    stats = _ledger_tick(monkeypatch)
    # the tick still plans (the reading is usable for a shadow that orders
    # nothing) -- but its status is what the heartbeat writes
    assert stats["rows"] == 1 and not stats.get("abandoned")
    assert stats["status"] == "degraded"
    assert stats["venue_confirmed"] is False
    assert stats["refusal"] == MPS.R_VENUE_UNCONFIRMED
    assert stats["owner_blocker"] == MPS.OWNER_BLOCKER
    assert stats["positions_authority"] == MPS.AUTHORITY_LEDGER
    assert stats["primary_refusal"] == MS.R_PMUS_SECRET_NOT_ED25519
    # the credential CLASS by shape (an enum), never a value
    assert stats["credential_class"] == stats["positions_source"][
        "pmus_slot_shape"]
    assert P.RSA_PEM_B64 not in json.dumps(stats, default=str)
    assert P.PMX_CLIENT not in json.dumps(stats, default=str)


def test_main_writes_the_ticks_own_status_so_the_heartbeat_reads_degraded(
        monkeypatch):
    """main() heartbeats str(stats['status']): the degraded tick's beat is
    'degraded', with the refusal and the owner blocker in its detail."""
    beats = []

    async def fake_heartbeat(service, status, detail):
        beats.append((service, status, detail))
        raise asyncio.CancelledError  # stop main() after one beat

    stats = _ledger_tick(monkeypatch)

    async def fake_tick(pool, pmus, *a, **k):
        return dict(stats)

    async def fake_pool():
        return object()

    monkeypatch.setattr(MS, "enabled", lambda: True)
    monkeypatch.setattr(MS, "get_pool", fake_pool)
    monkeypatch.setattr(MS, "tick_once", fake_tick)
    monkeypatch.setattr(MS, "heartbeat", fake_heartbeat)
    with pytest.raises(asyncio.CancelledError):
        _run(MS.main())
    (service, status, detail), = beats
    assert service == "mirror_shadow" and status == "degraded"
    assert detail["refusal"] == MPS.R_VENUE_UNCONFIRMED
    assert detail["owner_blocker"] == MPS.OWNER_BLOCKER
    assert detail["venue_confirmed"] is False


def test_a_venue_walk_reading_is_the_only_ok(monkeypatch):
    _fresh(monkeypatch)
    _venue_topology(monkeypatch)
    p = P._LedgerPool(fills=T.HIS)
    pm = T._Pmus(bid=0.30, ask=0.32, held={"other-slug": 5.0})
    stats = _run(MS.tick_once(p, pm, now_ts=5000.0))
    assert stats["positions_source"]["source"] == MPS.SRC_VENUE
    assert stats["status"] == "ok"
    assert stats["venue_confirmed"] is True
    assert stats["positions_authority"] == MPS.AUTHORITY_VENUE
    assert "refusal" not in stats and "owner_blocker" not in stats


def test_confirmation_is_pure_and_never_upgrades_a_non_venue_reading():
    led = {"source": MPS.SRC_LEDGER, "authority": MPS.AUTHORITY_LEDGER,
           "primary": MPS.SRC_VENUE,
           "primary_refusal": MS.R_PMUS_SECRET_NOT_ED25519,
           "pmus_slot_shape": "RSA_PEM_PRIVATE_KEY_SHAPE_NOT_A_PMUS_RETAIL_KEY"}
    c = MPS.confirmation(led)
    assert c == {"venue_confirmed": False, "status": "degraded",
                 "refusal": MPS.R_VENUE_UNCONFIRMED,
                 "positions_authority": MPS.AUTHORITY_LEDGER,
                 "primary_refusal": MS.R_PMUS_SECRET_NOT_ED25519,
                 "credential_class": led["pmus_slot_shape"],
                 "owner_blocker": MPS.OWNER_BLOCKER}
    # a venue walk that FAILED under a usable key is a venue answer, not a
    # credential gap: degraded, no owner blocker, never confirmed
    failed = MPS.confirmation({"source": MPS.SRC_VENUE,
                               "unreadable": "venue_walk_failed"})
    assert failed["venue_confirmed"] is False and failed["status"] == "degraded"
    assert failed["owner_blocker"] is None
    # no source at all, a refused fallback: named, never confirmed
    none = MPS.confirmation({"source": None,
                             "refusal": MPS.R_NO_POSITIONS_SOURCE,
                             "primary_refusal": MS.R_PMUS_SECRET_NOT_ED25519})
    assert none["venue_confirmed"] is False
    assert none["refusal"] == MPS.R_NO_POSITIONS_SOURCE
    assert none["owner_blocker"] == MPS.OWNER_BLOCKER
    assert MPS.confirmation(None)["venue_confirmed"] is False
    ok = MPS.confirmation({"source": MPS.SRC_VENUE, "as_of_epoch": 1.0})
    assert ok["venue_confirmed"] is True and ok["status"] == "ok"


def test_the_new_codes_are_classified_as_capability_gaps_not_economics():
    for code in (MPS.R_VENUE_UNCONFIRMED,):
        cls, fam, _stage = TT.TABLE[code]
        assert (cls, fam) == ("SOFTWARE", "CAPABILITY"), code
    # no reading in this process is a data gap, like no source at all
    assert TT.TABLE[MPS.R_NO_READING_YET][:2] == ("SOFTWARE", "DATA")


# ── the ticks that make no reading (independent review, CHANGES_REQUIRED) ──
#
# Production, release 732cc0c6 (render-ops 37940782266, workers logs
# 2026-10-09 00:00-13:59Z): 26 "consecutive venue misses ... abandoning the
# tick, backing off 60s" events, about two an hour, on the ledger topology.
# Every abandon sets a 60 s backoff (BACKOFF_S) and the worker polls every
# 30 s (POLL_S), so the next one or two ticks return early, before any
# positions reading -- and those early returns beat the tick's initial
# status 'ok' with no refusal, no owner blocker and no venue_confirmed. A
# degraded loop read as green. A tick that makes no reading may claim only
# what the last reading in this process supported: 'ok' only after a venue
# walk's reading; after anything else, 'degraded' with that reading's named
# refusal; before any reading, a named non-success.

_MISS_CONDS = ["c%d" % i for i in range(5)]


def _drive_main(monkeypatch, plan):
    """main() over the given (pool, pmus, now_ts) ticks, the REAL tick_once
    each time; returns every heartbeat (status, detail) main wrote."""
    real_tick = MS.tick_once
    steps = iter(plan)
    beats: list = []

    async def driven_tick(_pool, _pmus):
        pool, pm, now_ts = next(steps)
        return await real_tick(pool, pm, now_ts=now_ts)

    async def fake_heartbeat(service, status, detail):
        assert service == "mirror_shadow"
        beats.append((status, json.loads(json.dumps(detail, default=str))))
        if len(beats) == len(plan):
            raise asyncio.CancelledError  # stop main() after the last beat

    async def fake_pool():
        return object()

    monkeypatch.setattr(MS, "enabled", lambda: True)
    monkeypatch.setattr(MS, "get_pool", fake_pool)
    monkeypatch.setattr(MS, "tick_once", driven_tick)
    monkeypatch.setattr(MS, "heartbeat", fake_heartbeat)
    monkeypatch.setattr(MS, "POLL_S", 0.0)
    with pytest.raises(asyncio.CancelledError):
        _run(MS.main())
    return beats


def _assert_named_ledger_refusal(status, d):
    assert status == "degraded", d
    assert d["venue_confirmed"] is False
    assert d["refusal"] == MPS.R_VENUE_UNCONFIRMED
    assert d["owner_blocker"] == MPS.OWNER_BLOCKER
    assert d["positions_authority"] == MPS.AUTHORITY_LEDGER
    assert d["primary_refusal"] == MS.R_PMUS_SECRET_NOT_ED25519


def test_no_tick_on_the_ledger_topology_heartbeats_ok_backoff_and_switch_included(
        monkeypatch):
    """THE PRODUCTION SEQUENCE: a ledger-derived tick abandoned on the miss
    streak, the next poll inside its 60 s backoff, then a tick with the DB
    switch off. None beats 'ok'; each carries the named refusal and the owner
    blocker, so loop health and readiness name it too."""
    _fresh(monkeypatch)
    _ledger_topology(monkeypatch)
    poll = 30.0                     # production's POLL_S default
    assert poll < MS.BACKOFF_S      # so a backoff tick always follows
    t0 = 5000.0
    beats = _drive_main(monkeypatch, [
        (_ledger_pool(conds=_MISS_CONDS), P._NoVenueWalk(raise_bbo=True), t0),
        (_ledger_pool(), P._NoVenueWalk(), t0 + poll),
        (_ledger_pool(switch=json.dumps("off")), P._NoVenueWalk(),
         t0 + MS.BACKOFF_S + 1.0),
    ])
    (s1, d1), (s2, d2), (s3, d3) = beats
    # (1) the abandon: the venue misses, on a ledger-derived reading
    assert d1["abandoned"] is True and d1["markets"] == MS.MISS_STREAK_ABANDON
    _assert_named_ledger_refusal(s1, d1)
    # (2) the backoff tick: no reading, the last one's claim and no more
    assert d2["skipped_backoff"] is True and d2["markets"] == 0
    _assert_named_ledger_refusal(s2, d2)
    assert d2["confirmation_basis"] == MPS.BASIS_LAST_READING
    assert d2["confirmation_read_at_epoch"] == t0
    # (3) the switched-off tick: the same
    assert d3.get("switched_off") is True and d3["markets"] == 0
    _assert_named_ledger_refusal(s3, d3)
    assert d3["confirmation_basis"] == MPS.BASIS_LAST_READING
    assert all(s != "ok" for s, _d in beats)
    # the readiness gate on the backoff beat names it -- not refusal=None
    g = _run(F.gate_mirror_positions_readable(_HbConn(s2, d2),
                                              {"now": 10_000.0}))
    assert g["evidence"]["status"] == "degraded"
    assert g["evidence"]["venue_confirmed"] is False
    assert g["evidence"]["refusal"] == MPS.R_VENUE_UNCONFIRMED
    assert g["evidence"]["owner_blocker"] == MPS.OWNER_BLOCKER
    assert g["evidence"]["confirmation_basis"] == MPS.BASIS_LAST_READING
    # the ledger reading it rests on WAS readable: the gate's value holds
    assert g["value"] is True and g["evidence"]["last_reading_readable"] is True
    for _s, d in beats:
        assert P.RSA_PEM_B64 not in json.dumps(d)
        assert P.PMX_CLIENT not in json.dumps(d)


def test_the_backoff_tick_after_a_venue_confirmed_reading_may_stay_ok(
        monkeypatch):
    """The venue-walk topology: the reading was the venue's word, the
    abandon was a run of book misses -- the backoff tick after it may beat
    'ok', and says whose reading it rests on."""
    _fresh(monkeypatch)
    _venue_topology(monkeypatch)
    p = P._LedgerPool(fills=T.HIS, conds=_MISS_CONDS)
    pm = T._Pmus(raise_bbo=True, held={"other-slug": 5.0})
    s1 = _run(MS.tick_once(p, pm, now_ts=6000.0))
    assert s1["abandoned"] is True and s1["venue_confirmed"] is True
    s2 = _run(MS.tick_once(P._LedgerPool(fills=T.HIS), T._Pmus(),
                           now_ts=6030.0))
    assert s2["skipped_backoff"] is True
    assert s2["status"] == "ok"
    assert s2["venue_confirmed"] is True
    assert s2["positions_authority"] == MPS.AUTHORITY_VENUE
    assert s2["confirmation_basis"] == MPS.BASIS_LAST_READING
    assert s2["last_reading_readable"] is True
    assert "refusal" not in s2 and "owner_blocker" not in s2
    g = _run(F.gate_mirror_positions_readable(
        _HbConn("ok", json.loads(json.dumps(s2, default=str))),
        {"now": 10_000.0}))
    assert g["value"] is True and g["evidence"]["venue_confirmed"] is True


def test_a_backoff_after_an_unreadable_reading_is_never_ok(monkeypatch):
    """A walk (or ledger) that produced no reading backs off too: the tick
    that follows rests on NO reading, so it is degraded under the unreadable
    reading's own name -- under a usable key no owner blocker (the venue
    answered), on the ledger topology the owner blocker."""
    _fresh(monkeypatch)
    _venue_topology(monkeypatch)
    s1 = _run(MS.tick_once(P._LedgerPool(fills=T.HIS),
                           T._Pmus(raise_walk=True), now_ts=6000.0))
    assert s1["positions_unreadable"] is True and s1["status"] == "degraded"
    assert s1["venue_confirmed"] is False
    assert s1["refusal"] == MPS.R_NO_POSITIONS_SOURCE
    s2 = _run(MS.tick_once(P._LedgerPool(fills=T.HIS), T._Pmus(),
                           now_ts=6030.0))
    assert s2["skipped_backoff"] is True and s2["status"] == "degraded"
    assert s2["venue_confirmed"] is False
    assert s2["refusal"] == MPS.R_NO_POSITIONS_SOURCE
    assert s2["owner_blocker"] is None

    _fresh(monkeypatch)
    _ledger_topology(monkeypatch)
    s3 = _run(MS.tick_once(P._LedgerPool(fills=T.HIS, ledger_raises=True),
                           P._NoVenueWalk(), now_ts=7000.0))
    assert s3["positions_unreadable"] is True and s3["status"] == "degraded"
    assert s3["refusal"] == MPS.R_NO_POSITIONS_SOURCE
    assert s3["owner_blocker"] == MPS.OWNER_BLOCKER
    s4 = _run(MS.tick_once(P._LedgerPool(fills=T.HIS), P._NoVenueWalk(),
                           now_ts=7030.0))
    assert s4["skipped_backoff"] is True and s4["status"] == "degraded"
    assert s4["refusal"] == MPS.R_NO_POSITIONS_SOURCE
    assert s4["owner_blocker"] == MPS.OWNER_BLOCKER
    assert s4["venue_confirmed"] is False
    # readiness: a backoff beat resting on an UNREADABLE reading is not
    # evidence of a readable account (it read 'readable' before: the beat
    # carried no positions_unreadable of its own)
    for s in (s2, s4):
        assert s["last_reading_readable"] is False
        d = json.loads(json.dumps(s, default=str))
        g = _run(F.gate_mirror_positions_readable(
            _HbConn(s["status"], d), {"now": 10_000.0}))
        assert g["value"] is False
        assert g["evidence"]["refusal"] == MPS.R_NO_POSITIONS_SOURCE


@pytest.mark.parametrize("skip", ["switched_off", "skipped_backoff"])
def test_before_any_reading_in_the_process_a_skipped_tick_is_a_named_non_success(
        monkeypatch, skip):
    """No reading yet in this process: nothing to rest a claim on. The tick
    is 'degraded' under R_NO_READING_YET, never 'ok'; the owner blocker rides
    beside it only where the slot's own precondition refuses the venue walk
    (read from the credential's FORMAT -- no venue call is made)."""
    for topology, blocker, primary in (
            (_ledger_topology, MPS.OWNER_BLOCKER, MS.R_PMUS_SECRET_NOT_ED25519),
            (_venue_topology, None, None)):
        _fresh(monkeypatch)
        topology(monkeypatch)
        if skip == "switched_off":
            p = P._LedgerPool(fills=T.HIS, switch=json.dumps("off"))
        else:
            p = P._LedgerPool(fills=T.HIS)
            MS._backoff_until = 9_000.0
        pm = P._NoVenueWalk() if blocker else T._Pmus()
        s = _run(MS.tick_once(p, pm, now_ts=8000.0))
        MS._backoff_until = 0.0
        assert s.get(skip) is True and s["markets"] == 0
        assert s["status"] == "degraded", topology.__name__
        assert s["venue_confirmed"] is False
        assert s["refusal"] == MPS.R_NO_READING_YET
        assert s["owner_blocker"] == blocker
        assert s["primary_refusal"] == primary
        assert s["positions_authority"] is None
        assert s["confirmation_basis"] == MPS.BASIS_NO_READING
        assert s["last_reading_readable"] is None
        assert pm.calls == [] and p.ledger_sql == []
        if not blocker:
            assert pm.portfolio.calls == 0
        # readiness: nothing read in this process is never "readable"
        g = _run(F.gate_mirror_positions_readable(
            _HbConn(s["status"], json.loads(json.dumps(s, default=str))),
            {"now": 10_000.0}))
        assert g["value"] is False
        assert g["evidence"]["refusal"] == MPS.R_NO_READING_YET


def test_carried_confirmation_never_claims_more_than_the_last_reading():
    """Pure: the claim a tick that made no reading may carry."""
    led = MPS.confirmation({"source": MPS.SRC_LEDGER,
                            "authority": MPS.AUTHORITY_LEDGER,
                            "primary_refusal": MS.R_PMUS_SECRET_NOT_ED25519})
    c = MPS.carried_confirmation(dict(led, read_at_epoch=1.0))
    assert c["venue_confirmed"] is False and c["status"] == "degraded"
    assert c["refusal"] == MPS.R_VENUE_UNCONFIRMED
    assert c["owner_blocker"] == MPS.OWNER_BLOCKER
    assert c["confirmation_basis"] == MPS.BASIS_LAST_READING
    assert c["read_at_epoch"] == 1.0
    ven = MPS.confirmation({"source": MPS.SRC_VENUE, "as_of_epoch": 2.0})
    assert MPS.carried_confirmation(ven)["venue_confirmed"] is True
    none = MPS.carried_confirmation(None, primary_refusal=None)
    assert none["venue_confirmed"] is False and none["status"] == "degraded"
    assert none["refusal"] == MPS.R_NO_READING_YET
    assert none["owner_blocker"] is None
    assert none["confirmation_basis"] == MPS.BASIS_NO_READING
    blocked = MPS.carried_confirmation(
        None, primary_refusal=MS.R_PMUS_SECRET_NOT_ED25519)
    assert blocked["owner_blocker"] == MPS.OWNER_BLOCKER
    # the caller's dict is never mutated
    assert "confirmation_basis" not in led


def test_a_carried_venue_confirmation_never_upgrades_a_degraded_tick():
    """A tick already degraded for another reason (an unreadable intent
    probe) stays degraded when the last reading was the venue's."""
    ven = MPS.carried_confirmation(
        MPS.confirmation({"source": MPS.SRC_VENUE, "as_of_epoch": 2.0}))
    stats = {"status": "degraded", "intent_guard_unreadable": "TimeoutError"}
    MS._apply_confirmation(stats, ven)
    assert stats["status"] == "degraded" and stats["venue_confirmed"] is True
    ok = {"status": "ok"}
    MS._apply_confirmation(ok, ven)
    assert ok["status"] == "ok"


# ── readiness ─────────────────────────────────────────────────────────

class _HbConn:
    def __init__(self, status, detail, age_s=10.0, now=10_000.0):
        import datetime as dt
        self.row = {"status": status, "detail": json.dumps(detail),
                    "beat_at": dt.datetime.fromtimestamp(
                        now - age_s, dt.timezone.utc)}

    async def fetchval(self, sql, *a):
        return True          # every table exists

    async def fetchrow(self, sql, *a):
        assert "service_heartbeats" in sql
        return self.row


def test_the_readiness_gate_carries_the_refusal_and_never_claims_the_venue(
        monkeypatch):
    stats = _ledger_tick(monkeypatch)
    detail = json.loads(json.dumps(stats, default=str))
    g = _run(F.gate_mirror_positions_readable(
        _HbConn("degraded", detail), {"now": 10_000.0}))
    # readable (its value is unchanged) -- and named as NOT the venue's word
    assert g["value"] is True
    ev = g["evidence"]
    assert ev["status"] == "degraded"
    assert ev["venue_confirmed"] is False
    assert ev["positions_authority"] == MPS.AUTHORITY_LEDGER
    assert ev["refusal"] == MPS.R_VENUE_UNCONFIRMED
    assert ev["owner_blocker"] == MPS.OWNER_BLOCKER


def test_the_readiness_gate_on_a_pre_change_heartbeat_reads_its_authority():
    """A heartbeat written by the deployed 732cc0c6 code carries no
    venue_confirmed field: the gate reads it as NOT confirmed, with the
    receipt's own authority, never as confirmed."""
    detail = {"positions_source": {"source": MPS.SRC_LEDGER,
                                   "authority": MPS.AUTHORITY_LEDGER}}
    g = _run(F.gate_mirror_positions_readable(
        _HbConn("ok", detail), {"now": 10_000.0}))
    assert g["evidence"]["venue_confirmed"] is False
    assert g["evidence"]["positions_authority"] == MPS.AUTHORITY_LEDGER


# ── loop health ───────────────────────────────────────────────────────

@pg
def test_loop_health_names_the_refusal_and_the_owner_blocker_on_postgres():
    """The degraded beat is not a success (DEGRADED is never green) and the
    loop's error names WHY and WHO, never a nameless failure."""
    import asyncpg

    async def main(status, detail):
        c = await asyncpg.connect(DSN)
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute(
                "INSERT INTO service_heartbeats (service, status, detail, "
                "beat_at) VALUES ('mirror_shadow', $1, $2::jsonb, now()) "
                "ON CONFLICT (service) DO UPDATE SET status = $1, "
                "detail = $2::jsonb, beat_at = now()",
                status, json.dumps(detail))
            return {(lp["name"], lp["process"]): lp
                    for lp in (await LH.read(c, env={}))["loops"]}
        finally:
            await tx.rollback()
            await c.close()

    bad = asyncio.run(main("degraded", {
        "refusal": MPS.R_VENUE_UNCONFIRMED,
        "owner_blocker": MPS.OWNER_BLOCKER}))[("mirror_shadow", "workers")]
    assert bad["status"] != LH.HEALTHY
    assert bad["beat_status"] == "degraded"
    assert bad["last_error"] == ("NON_SUCCESS_BEAT:degraded:%s:OWNER_BLOCKER=%s"
                                 % (MPS.R_VENUE_UNCONFIRMED, MPS.OWNER_BLOCKER))
    good = asyncio.run(main("ok", {}))[("mirror_shadow", "workers")]
    assert good["status"] == LH.HEALTHY and good["beat_status"] == "ok"


@pg
def test_loop_health_reads_the_backoff_tick_after_a_ledger_reading_as_degraded_by_name(
        monkeypatch):
    """The beat main() writes on the backoff tick that follows a
    ledger-derived abandon, read back through loop health on Postgres: not
    healthy, named, with the owner blocker -- the reviewer's reproduction
    (that beat read 'ok', i.e. HEALTHY) turned into a regression."""
    import asyncpg

    _fresh(monkeypatch)
    _ledger_topology(monkeypatch)
    beats = _drive_main(monkeypatch, [
        (_ledger_pool(conds=_MISS_CONDS), P._NoVenueWalk(raise_bbo=True),
         5000.0),
        (_ledger_pool(), P._NoVenueWalk(), 5030.0)])
    status, detail = beats[1]
    assert detail["skipped_backoff"] is True

    async def main():
        c = await asyncpg.connect(DSN)
        tx = c.transaction()
        await tx.start()
        try:
            await c.execute(
                "INSERT INTO service_heartbeats (service, status, detail, "
                "beat_at) VALUES ('mirror_shadow', $1, $2::jsonb, now()) "
                "ON CONFLICT (service) DO UPDATE SET status = $1, "
                "detail = $2::jsonb, beat_at = now()",
                status, json.dumps(detail))
            return {(lp["name"], lp["process"]): lp
                    for lp in (await LH.read(c, env={}))["loops"]}
        finally:
            await tx.rollback()
            await c.close()

    lp = asyncio.run(main())[("mirror_shadow", "workers")]
    assert lp["status"] != LH.HEALTHY
    assert lp["beat_status"] == "degraded"
    assert lp["last_error"] == ("NON_SUCCESS_BEAT:degraded:%s:OWNER_BLOCKER=%s"
                                % (MPS.R_VENUE_UNCONFIRMED, MPS.OWNER_BLOCKER))
