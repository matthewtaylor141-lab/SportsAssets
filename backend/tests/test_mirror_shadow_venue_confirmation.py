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


def _ledger_tick(monkeypatch):
    P._fresh_tick(monkeypatch)
    P._topology(monkeypatch, secret=P.RSA_PEM_B64, key_id=P.PMX_CLIENT,
                pmx_client=P.PMX_CLIENT)
    p = P._LedgerPool(fills=T.HIS, ledger_rows_json=[
        {"slug": T.SLUG, "src": "live_orders", "net": 147.0, "n": 1}],
        ledger_rows=[{"sh": 147.0, "intent": "ORDER_INTENT_BUY_LONG"}],
        whales_ratio_fills=T._ratio_fills())
    return _run(MS.tick_once(p, P._NoVenueWalk(bid=0.30, ask=0.32),
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
    P._fresh_tick(monkeypatch)
    P._topology(monkeypatch, secret=P.ED25519,
                key_id="9f1c2d3e-0000-4000-8000-000000000001",
                pmx_client=P.PMX_CLIENT)
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
