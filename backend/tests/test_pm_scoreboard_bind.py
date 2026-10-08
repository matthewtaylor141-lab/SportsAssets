"""PM Evidence Pack component 2 bound: the forward scoreboard is a READ
layer over BETTOR's existing truth (attribution, route receipts, Adriana +
sentinel receipts, the PAPER ledger), never a second ledger. Thresholds are
the frozen V1; one value per independent event; P&L reconciles to the PAPER
ledger group by group or the receipt says it does not."""
from __future__ import annotations

import asyncio
import hashlib
import os
import pathlib
from decimal import Decimal as D

import pytest

from sportsassets.pm_bind import scoreboard as SB

DATA = pathlib.Path(SB.__file__).resolve().parents[1] / "pm_evidence" / "data"
SINCE = SB.cohort_start(SB.thresholds())
DSN = os.environ.get("RN1X_TEST_DSN")


def row(gid, *, at=SINCE + 60, me="1.00", ex="0.10", mg="0.00", var="-0.50",
        st="0", cash=None, claimed=True):
    cash = cash if cash is not None else str(
        D(me) + D(ex) + D(mg) + D(st) + D(var))
    return {"group_id": gid, "decided_at": at, "identity_claimed": claimed,
            "model_edge_usd": me, "execution_edge_usd": ex,
            "management_usd": mg, "settlement_usd": st,
            "outcome_variance_usd": var, "realized_pnl_usd": cash,
            "cash_pnl_usd": cash, "fees_usd": "0.02",
            "us_market_slug": "slug-" + gid}


def test_thresholds_are_the_frozen_v1():
    th = SB.thresholds()
    assert th["version"].endswith("V1")
    assert th["frozen_at"] == "2026-10-07"
    pack = (pathlib.Path(__file__).resolve().parents[2] / "research" /
            "pm_evidence_acceptance" / "BETTOR_PM_EVIDENCE_ACCEPTANCE_PACK_V1")
    src = next(pack.rglob("thresholds.json"))
    assert hashlib.sha256(src.read_bytes()).hexdigest() == hashlib.sha256(
        (DATA / "thresholds.json").read_bytes()).hexdigest()


def test_event_rows_are_forward_only_identity_only_one_event_per_fixture():
    att = [row("g1"), row("g2"), row("g-old", at=SINCE - 1),
           row("g-open", claimed=False)]
    fx = {"g1": "FX-A", "g2": "FX-A"}
    ev = SB.event_rows(att, fx, since=SINCE)
    assert {e.event_key for e in ev} == {"FX-A"}
    assert {e.mechanism for e in ev} == {"DIRECTIONAL", "EXECUTION_ALPHA",
                                         "XAVIER_MANAGEMENT"}
    d = [e for e in ev if e.mechanism == "DIRECTIONAL"]
    # directional realized = model edge + outcome variance (the coin)
    assert d[0].realized_pnl == D("0.50") and d[0].expected_pnl == D("1.00")


def test_routing_counts_compare_the_chosen_route_with_its_runner_up():
    pairs = [({"venue": "KALSHI", "side": "NO"},
              {"venue": "KALSHI", "side": "YES"}),
             ({"venue": "KALSHI", "side": "YES"},
              {"venue": "POLYMARKET_US", "side": "NO"}),
             ({"venue": "POLYMARKET_US", "side": "YES"},
              {"venue": "KALSHI", "side": "NO"}),
             ({"venue": "KALSHI", "side": "YES"}, {})]
    c = SB.routing_counts(pairs)
    assert c == {"kalshi_no_beat_kalshi_yes": 1, "kalshi_beat_pmus": 1,
                 "pmus_beat_kalshi": 1, "comparable": 3}


def pos(gid, realized, open_qty="0"):
    return {"group_id": gid, "realized_pnl_usd": realized,
            "open_qty": open_qty}


def test_ledger_reconciliation_is_exact_group_by_group():
    claimed = [row("g1", cash="0.60"), row("g2", cash="-1.00")]
    led = [pos("g1", "0.50"), pos("g1", "0.10"),      # entry + hedge leg
           pos("g2", "-1.00")]
    r = SB.ledger_reconciliation(claimed, led, tolerance=D("0.01"))
    assert r["green"] is True
    assert r["groups_reconciled"] == 2
    assert r["residual_usd"] == "0.00"


def test_a_scoreboard_dollar_the_ledger_never_booked_is_red():
    claimed = [row("g1", cash="0.60")]
    r = SB.ledger_reconciliation(claimed, [pos("g1", "0.40")],
                                 tolerance=D("0.01"))
    assert r["green"] is False
    assert r["groups_mismatched"][0]["group_id"] == "g1"
    r = SB.ledger_reconciliation(claimed, [], tolerance=D("0.01"))
    assert r["green"] is False and r["groups_absent_from_ledger"] == ["g1"]


def test_a_group_still_open_in_the_ledger_is_never_counted_reconciled():
    r = SB.ledger_reconciliation([row("g1", cash="0.60")],
                                 [pos("g1", "0.10", open_qty="5")],
                                 tolerance=D("0.01"))
    assert r["groups_reconciled"] == 0
    assert r["groups_still_open_in_ledger"] == 1


@pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
def test_read_over_a_migrated_database_is_a_read_only_layer():
    import asyncpg

    async def go():
        conn = await asyncpg.connect(DSN)
        try:
            async with conn.transaction(readonly=True):
                return await SB.read(conn, now=SINCE + 3600)
        finally:
            await conn.close()
    r = asyncio.run(go())
    assert r["authority"] == "READ_ONLY_EVIDENCE_NO_LEDGER"
    assert r["theoretical_arb_is_not_realized"] is True
    assert set(r["mechanisms"]) >= {"DIRECTIONAL", "EXECUTION_ALPHA",
                                    "XAVIER_MANAGEMENT", "SAME_VENUE_ARB",
                                    "CROSS_VENUE_ARB"}
    assert r["reconciliation"]["green"] is True
    assert r["ledger_reconciliation"]["basis"].startswith("PAPER ledger")


def test_all_seven_mechanisms_are_always_reported_apart():
    head = {"mechanisms": {}, "mechanism_states": {},
            "routing": {"decisions": 0}, "arbitrage": {}}
    m, st = SB.all_mechanisms(head, SB.thresholds())
    assert tuple(m) == SB.MECHANISMS == tuple(st)
    for k in ("SAME_VENUE_ARB", "CROSS_VENUE_ARB", "ROUTING_SAVINGS",
              "ALLOCATION_ALPHA"):
        assert m[k]["realized_pnl"] == 0
        assert st[k]["status"] == "SHADOW_ONLY"
    assert "INSUFFICIENT_INDEPENDENT_EVENTS" in st["DIRECTIONAL"]["blockers"]
