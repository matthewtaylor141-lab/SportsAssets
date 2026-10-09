"""CAPITAL-CRITICAL: THE XAVIER PACKET MEASURE COUNTS EVERY HELD POSITION,
AND A GATE GREEN OVER NOTHING IS NOT A RATE OF 1.0 (RC6.2 p-xavier M-1/M-2).

M-1 (members 0 at the read, the next packet would carry it). With no open
paper position the capital-readiness gate xavier_complete is GREEN over
nothing, and completion readiness mapped any GREEN gate to
xavier_packet_complete_rate 1.0 -- so XAVIER_PACKET_COMPLETENESS_BELOW_TARGET
vanished without evidence; the pm-acceptance binding did the same
(xavier_complete_rate 1.0). Now the rate is UNMEASURED (None) when the gate
counted no open position: the blocker stays named and the harness field is
without machine evidence. Stricter only; no threshold moves; a RED gate is
still 0.0 and a GREEN gate over counted positions is still 1.0.

M-2. The gate took its verdict from the allocation rail's applicable set,
which leaves out positions whose venue market is terminal or not open
(EXTERNAL_UNAVAILABLE). It could read GREEN with such a held position and
no complete packet, and the scorecard's rate x held (paper_freshness counts
every open position) then counted that member complete. Each one is now
counted, never complete, and named. The allocation rail
(strategy_management_integrity) is untouched.

SYNTHETIC data in a scratch test database; no network, no order authority.
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_paper_freshness as PMF
from sportsassets import bettor_paper_simulator as SIM
from sportsassets.capital_readiness import feeds as CRF
from sportsassets.completion import read as CR
from sportsassets.pm_bind import acceptance as PA

from tests import paper_harness as H
from tests import test_completion_readiness_readback as CRR
from tests import test_pm_acceptance_bind as PAB
from tests import test_xavier_review_probability_freshness as XRF

#: the strict management rail runs its production functions here
MANAGEMENT_RAIL_ENFORCED = True

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
AT = XRF.AT
B_XAV = "XAVIER_PACKET_COMPLETENESS_BELOW_TARGET"


def _gate(value, **ev):
    return {"value": value, "reason": None if value else "XAVIER_PACKETS_"
            "INCOMPLETE", "evidence": ev}


# ═════════════════════════════════════════════════════════════════════
# M-1 · THE RATE (pure)
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("gate,rate", [
    (_gate(True, open_positions=0, counted_open_positions=0), None),
    (_gate(True, open_positions=0), None),
    (_gate(True), None),                       # nothing counted: unproven
    ({"value": True}, None),
    (None, None), ({}, None),
    (_gate(True, open_positions=3, counted_open_positions=3), 1.0),
    (_gate(False, open_positions=3, counted_open_positions=3), 0.0),
    (_gate(False, open_positions=0), 0.0),     # RED is never better
])
def test_the_rate_of_the_gate(gate, rate):
    assert CRF.xavier_complete_rate(gate) == rate


def test_completion_readiness_keeps_the_blocker_when_nothing_is_held():
    """THE REGRESSION. On b3f1b0cd the vacuous GREEN read 1.0 and the
    blocker was gone."""
    kw = CRR._inputs()
    kw["gates"] = dict(kw["gates"], xavier_complete=_gate(
        True, open_positions=0, applicable_open_positions=0,
        counted_open_positions=0, complete_current_packets=0))
    d = CR.readiness_block(**kw)
    assert d["evidence"]["xavier_packet_complete_rate"] is None
    assert B_XAV in d["blockers"]


def test_completion_readiness_is_unchanged_with_counted_positions():
    kw = CRR._inputs()
    kw["gates"] = dict(kw["gates"], xavier_complete=_gate(
        True, open_positions=2, counted_open_positions=2,
        complete_current_packets=2))
    d = CR.readiness_block(**kw)
    assert d["evidence"]["xavier_packet_complete_rate"] == 1.0
    assert B_XAV not in d["blockers"]
    kw["gates"] = dict(kw["gates"], xavier_complete=_gate(
        False, open_positions=2, counted_open_positions=2,
        complete_current_packets=1))
    d = CR.readiness_block(**kw)
    assert d["evidence"]["xavier_packet_complete_rate"] == 0.0
    assert B_XAV in d["blockers"]


def test_the_pm_binding_binds_no_rate_from_a_gate_green_over_nothing():
    """THE REGRESSION. On b3f1b0cd the binding put 1.0 and the harness's
    xavier_complete gate passed with nothing held."""
    red = PAB.green_red()
    red["completion"]["gate_evidence"]["xavier_complete"] = _gate(
        True, open_positions=0, counted_open_positions=0)
    r = PAB.ev(red=red)
    assert "xavier_complete_rate" not in r["evidence_input"]
    assert "xavier_complete_rate" in r["fields_without_machine_evidence"]
    assert r["gates"]["xavier_complete"]["pass"] is False
    assert r["pm_state"] != "GREEN"


def test_the_pm_binding_is_unchanged_with_counted_positions():
    r = PAB.ev()
    assert r["evidence_input"]["xavier_complete_rate"] == 1.0
    assert r["gates"]["xavier_complete"]["pass"] is True
    red = PAB.green_red()
    red["completion"]["gate_evidence"]["xavier_complete"] = _gate(
        False, open_positions=3, counted_open_positions=3)
    r = PAB.ev(red=red)
    assert r["evidence_input"]["xavier_complete_rate"] == 0.0
    assert r["gates"]["xavier_complete"]["pass"] is False


# ═════════════════════════════════════════════════════════════════════
# M-2 · AN EXTERNAL_UNAVAILABLE MEMBER IS COUNTED, NEVER COMPLETE (Postgres)
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_held_position_in_a_terminal_market_is_counted_not_complete():
    """THE REGRESSION. On b3f1b0cd the only held position, its venue market
    EXPIRED, was left out of the gate's set: GREEN, rate 1.0 x 1 held."""
    conn = await H.connect()
    slugs = []
    try:
        a, g, slug = await XRF._held(conn, "pxm2", entry_age_s=3600)
        slugs.append(slug)
        await XRF._review(conn, a, g)
        # the venue says the market has ended (a successful read, no levels)
        await SIM.record_book(conn, slug=slug, read={
            "marketData": {"bids": [], "offers": [],
                           "state": "MARKET_STATE_EXPIRED"},
            "observed_at": AT + 1}, source="TEST", read_basis="TEST")
        acct = a["account_id"]
        # THE ALLOCATION RAIL IS UNTOUCHED: it still leaves the member out
        iv = await PMF.strategy_management_integrity(
            conn, acct, XRF.CG, now=AT + 2)
        assert iv["open_positions"] == 0
        assert iv["excluded_external_unavailable_count"] == 1
        assert iv["refusal"] is None
        # THE GATE COUNTS IT, NOT COMPLETE, BY NAME
        gate = await CRF.gate_xavier_complete(conn, {"account_id": acct,
                                                     "now": AT + 2})
        assert gate["value"] is False
        assert gate["reason"] == "XAVIER_PACKETS_INCOMPLETE"
        ev = gate["evidence"]
        pk = iv["excluded_external_unavailable"][0]
        assert ev["open_positions"] == 1
        assert ev["applicable_open_positions"] == 0
        assert ev["counted_open_positions"] == 1
        assert ev["complete_current_packets"] == 0
        assert ev["external_unavailable_counted_not_complete"] == 1
        assert ev["excluded_external_unavailable"] == [pk]
        assert pk in ev["packet_incomplete"]
        assert ev["blockers"] == [{
            "position_key": pk, "why": CRF.XAVIER_EXTERNAL_UNAVAILABLE,
            "protection_state": CRF.PROTECTION_NOT_JUDGED_EXTERNAL,
            "latest_review_missing": ev["blockers"][0][
                "latest_review_missing"]}]
        assert ev["blockers"][0]["latest_review_missing"] is not None
        # and the rate the readiness / the binding read is 0.0, not 1.0
        assert CRF.xavier_complete_rate(gate) == 0.0
    finally:
        await XRF._purge(conn, slugs)
        await conn.close()


@pg
async def test_with_nothing_held_the_gate_counts_nothing_and_the_rate_is_unmeasured():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "pxm1", now=AT - 100)
        gate = await CRF.gate_xavier_complete(
            conn, {"account_id": a["account_id"], "now": AT})
        assert gate["value"] is True              # the verdict is unchanged
        assert gate["evidence"]["open_positions"] == 0
        assert gate["evidence"]["counted_open_positions"] == 0
        assert CRF.xavier_complete_rate(gate) is None
    finally:
        await conn.close()
