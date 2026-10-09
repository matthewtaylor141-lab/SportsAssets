"""CAPITAL-CRITICAL: AUDREY'S RECONCILIATION SOURCE, TRACEABLE END TO END, AND
A TRUTH QUORUM THAT NAMES EXACTLY WHICH SOURCES ARE MISSING (RC6 archer-
lifecycle).

Production research run 37875517525 (2026-10-09 02:38Z, release 69a8a07e):
smalllive_reconciliations held 8 rows, newest 2026-10-03T02:39:15Z (the
owner's stop); her universe held 2,840 groups, 2,832 never reconciled; the
newest execution-mirror account snapshot was 2026-10-03T02:39:11Z (144 h,
one balance, no position); the lane is enabled, STOPPED, stop done; no venue
fill exists. red_team.json (pm-acceptance 37836393458) read TRUTH_QUORUM RED
with MISSING_SOURCE for AUDREY_RECONCILIATION, VENUE_BALANCE and
VENUE_POSITIONS and STALE_SOURCE for AUDREY_RECONCILIATION and VENUE_BALANCE:
the imported quorum (red_team.truth_quorum, byte-for-byte the closeout
package) skips a stale row before its presence check, so a stale source is
named missing too, and nothing said why a source was absent.

Proven here:

  * the quorum's evidence names each source exactly -- CURRENT / STALE /
    MISSING, the stale source's newest instant, why, and on whom it depends
    (VENUE_POSITIONS: not venue-confirmed, the PMUS slot holds no Ed25519
    key, the owner's funded retail key; VENUE_BALANCE: the newest account
    snapshot, taken only by the RUNNING lane through that key); the imported
    blockers are kept verbatim beside it, and the evidence carries no clock
    (the runner's receipts stay deduplicated);
  * Audrey's source is LINKED to the positions it reconciled: an ACTUAL
    position whose group she has not reconciled within the quorum's max age
    is a named blocker, gone once her lane-off pass reconciles it (no venue
    is ever built);
  * every reconciliation row names the positions it covers: the group's
    PAPER positions (canonical open qty, PAPER_SIMULATED) and the ACTUAL
    inventory (venue fills, VENUE_CONFIRMED), never one number.
"""
from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from decimal import Decimal

import pytest

from sportsassets import execmirror as M
from sportsassets.red_team.models import PositionTruth
from sportsassets.redteam import controls as C

from tests import test_execmirror as TE

pg = pytest.mark.skipif(not TE.DSN, reason="needs RN1X_TEST_DSN")


def _epoch(iso: str) -> float:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


#: production 2026-10-08 20:03Z (pm-acceptance 37836393458) and research
#: run 37875517525: the rows the quorum read, and the completion's
#: venue-position verdict
NOW = _epoch("2026-10-08T20:03:00Z")
SNAPSHOT_AT = _epoch("2026-10-03T02:39:11Z")
AUDREY_AT = _epoch("2026-10-03T02:39:15Z")
PRODUCTION_ROWS = [
    PositionTruth("INTERNAL_LEDGER", "NO_OPEN_POSITION", Decimal(0), None,
                  NOW),
    PositionTruth("VENUE_BALANCE", "BALANCE", Decimal(1), None, SNAPSHOT_AT),
    PositionTruth("MARKET_DATA", "MD", Decimal(1), None, NOW),
    PositionTruth("AUDREY_RECONCILIATION", "RECONCILED", Decimal(0), None,
                  AUDREY_AT)]
PRODUCTION_VENUE = {
    "venue_confirmed": False, "status": "OWNER_CREDENTIAL_REQUIRED",
    "primary_refusal": "PMUS_SECRET_SLOT_HOLDS_NO_ED25519_KEY"}
PRODUCTION_DETAIL = {"snapshot": {"at": SNAPSHOT_AT, "has_balances": True,
                                  "positions_n": 0},
                     "lane_state": "STOPPED", "audrey_positions": []}


def test_the_quorum_names_every_source_exactly_on_productions_rows():
    q = C.quorum(PRODUCTION_ROWS, now=NOW, audrey_open_discrepancies=0,
                 source_detail=PRODUCTION_DETAIL, venue=PRODUCTION_VENUE)
    # the imported quorum's blockers, verbatim (what production read)
    assert q["status"] == C.RED
    assert q["blockers"] == [
        "MISSING_SOURCE:AUDREY_RECONCILIATION", "MISSING_SOURCE:VENUE_BALANCE",
        "MISSING_SOURCE:VENUE_POSITIONS", "STALE_SOURCE:AUDREY_RECONCILIATION",
        "STALE_SOURCE:VENUE_BALANCE"]
    ev = q["evidence"]
    # ...and exactly which source is missing and which only stale
    assert ev["missing_sources"] == ["VENUE_POSITIONS"]
    assert ev["stale_sources"] == ["AUDREY_RECONCILIATION", "VENUE_BALANCE"]
    s = ev["sources"]
    assert {k: v["state"] for k, v in s.items()} == {
        "INTERNAL_LEDGER": C.SRC_CURRENT, "MARKET_DATA": C.SRC_CURRENT,
        "VENUE_POSITIONS": C.SRC_MISSING, "VENUE_BALANCE": C.SRC_STALE,
        "AUDREY_RECONCILIATION": C.SRC_STALE}
    assert s["VENUE_POSITIONS"]["why"] == \
        "NOT_VENUE_CONFIRMED:PMUS_SECRET_SLOT_HOLDS_NO_ED25519_KEY"
    assert "Ed25519" in s["VENUE_POSITIONS"]["dependency"]
    # the key alone is not enough while the lane stays stopped: only the
    # RUNNING lane writes the account snapshot both venue sources read
    assert "read-only account snapshot" in s["VENUE_POSITIONS"]["dependency"]
    assert "read-only account snapshot" in s["VENUE_BALANCE"]["dependency"]
    assert s["VENUE_BALANCE"]["why"] == \
        "NEWEST_ACCOUNT_SNAPSHOT_AT:2026-10-03T02:39:11Z"
    assert "STOPPED" in s["VENUE_BALANCE"]["dependency"]
    assert "Ed25519" in s["VENUE_BALANCE"]["dependency"]
    assert s["AUDREY_RECONCILIATION"]["why"] == \
        "NEWEST_RECONCILIATION_AT:2026-10-03T02:39:15Z"
    assert s["AUDREY_RECONCILIATION"]["newest_at"] == "2026-10-03T02:39:15Z"
    assert s["INTERNAL_LEDGER"]["newest_at"] is None   # CURRENT: no clock
    assert ev["audrey"]["actual_positions"] == 0


def test_the_quorum_evidence_carries_no_clock():
    """The red-team runner appends a control receipt only when the evidence
    changes: the same sources read a minute apart give the same evidence."""
    later = [PositionTruth(r.source, r.claim_key, r.qty, r.value,
                           NOW + 60 if r.as_of == NOW else r.as_of)
             for r in PRODUCTION_ROWS]
    a = C.quorum(PRODUCTION_ROWS, now=NOW, audrey_open_discrepancies=0,
                 source_detail=PRODUCTION_DETAIL, venue=PRODUCTION_VENUE)
    b = C.quorum(later, now=NOW + 60, audrey_open_discrepancies=0,
                 source_detail=PRODUCTION_DETAIL, venue=PRODUCTION_VENUE)
    assert a == b and "sources" in a["evidence"]


def test_an_unreconciled_actual_position_is_a_named_blocker():
    fresh = [PositionTruth(s, "C", Decimal(0), None, NOW) for s in (
        "INTERNAL_LEDGER", "VENUE_POSITIONS", "VENUE_BALANCE", "MARKET_DATA",
        "AUDREY_RECONCILIATION")]
    pos = {"us_market_slug": "mlb-x", "group_id": "g1", "held": 3,
           "source": "execmirror_fills"}
    q = C.quorum(fresh, now=NOW, audrey_open_discrepancies=0,
                 source_detail={"audrey_positions": [
                     dict(pos, reconciled_at=None, status=None)]})
    assert q["status"] == C.RED
    assert q["blockers"] == ["AUDREY_UNRECONCILED_POSITION:mlb-x"]
    q = C.quorum(fresh, now=NOW, audrey_open_discrepancies=0,
                 source_detail={"audrey_positions": [
                     dict(pos, reconciled_at=NOW - 2000, status="MATCHED")]})
    assert q["blockers"] == ["AUDREY_UNRECONCILED_POSITION:mlb-x"]
    q = C.quorum(fresh, now=NOW, audrey_open_discrepancies=0,
                 source_detail={"audrey_positions": [
                     dict(pos, reconciled_at=NOW - 60, status="MATCHED")]})
    assert q["status"] == C.GREEN and q["blockers"] == []
    (p,) = q["evidence"]["audrey"]["positions"]
    assert p["current"] is True and p["reconciled"] is True


def _no_venue():
    raise AssertionError("Audrey's lane-off pass must never build a venue")


async def _quorum(conn):
    now = time.time()
    det: dict = {}
    rows, disc = await C.quorum_rows(conn, now=now, venue_confirmed=False,
                                     market_data_green=True, detail=det)
    return C.quorum(rows, now=now, audrey_open_discrepancies=disc,
                    source_detail=det, venue={"venue_confirmed": False})


@pg
async def test_audrey_reconciles_an_actual_position_and_names_what_she_covered(
        monkeypatch):
    """A group with an ACTUAL position (a venue fill of 3 contracts) and a
    paper-only group: before her pass the quorum names the actual position
    as unreconciled; her lane-off pass (stopped lane, no venue) reconciles
    both and each row names the positions it covers."""
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        po = await TE._paper_order(conn, acct, qty=2702)
        await TE._paper_fill(conn, acct, po, qty=2702)
        mid = "em_%s" % uuid.uuid4().hex[:10]
        await conn.execute(
            "INSERT INTO execmirror_orders (mirror_id, paper_order_id, "
            " group_id, role, us_market_slug, intent, order_type, tif, "
            " live_qty, state, venue_order_id, cum_qty) VALUES ($1,$2,$3,"
            " 'ENTRY',$4,'ORDER_INTENT_BUY_LONG','LIMIT','IOC',3,'FILLED',"
            " $5, 3)", mid, po["order_id"], po["group_id"], po["slug"],
            "v_" + mid)
        await conn.execute(
            "INSERT INTO execmirror_fills (fill_key, mirror_id, "
            " venue_order_id, group_id, us_market_slug, intent, qty, price)"
            " VALUES ($1,$2,$3,$4,$5,'ORDER_INTENT_BUY_LONG',3,0.55)",
            "fk_" + mid, mid, "v_" + mid, po["group_id"], po["slug"])
        q = await _quorum(conn)
        assert "AUDREY_UNRECONCILED_POSITION:%s" % po["slug"] in q["blockers"]
        assert q["evidence"]["audrey"]["actual_positions"] == 1
        assert q["evidence"]["sources"]["AUDREY_RECONCILIATION"][
            "state"] == C.SRC_MISSING
        await conn.execute("UPDATE execmirror_control SET stopped = true,"
                           " stop_done_at = now() WHERE id = 1")
        mirror._venue, mirror._venue_factory = None, _no_venue
        mirror._last_management = 0.0
        out = await M.Mirror.tick(mirror, conn)
        assert out["state"] == "STOPPED" and out["audrey_reconciled"] >= 1
        rec = await conn.fetchrow("SELECT * FROM smalllive_reconciliations "
                                  " WHERE group_id = $1", po["group_id"])
        chain = json.loads(rec["chain"]) if isinstance(rec["chain"], str) \
            else rec["chain"]
        pos = chain["positions"]
        (pp,) = pos["paper"]
        assert pp["position_key"] == "paperpos:%s:%s:%s:LONG" % (
            acct["account_id"], po["group_id"], po["slug"])
        assert Decimal(pp["open_qty"]) == Decimal(2702)
        assert pp["execution_environment"] == "PAPER_SIMULATED"
        assert pos["actual"]["execution_environment"] == "VENUE_CONFIRMED"
        assert Decimal(pos["actual"]["live_held"]) == Decimal(3)
        assert pos["actual"]["us_market_slug"] == po["slug"]
        q = await _quorum(conn)
        assert not [b for b in q["blockers"]
                    if b.startswith("AUDREY_UNRECONCILED_POSITION")], q
        assert q["evidence"]["sources"]["AUDREY_RECONCILIATION"][
            "state"] == C.SRC_CURRENT
        (p,) = q["evidence"]["audrey"]["positions"]
        assert p["current"] is True and p["group_id"] == po["group_id"]
        # records only: nothing placed, cancelled or flattened
        assert venue.placed == [] and venue.cancelled == []
        assert venue.cancel_all_calls == 0 and venue.closed == []
    finally:
        await conn.execute("UPDATE execmirror_control SET enabled = false, "
                           " stopped = false, stop_done_at = NULL WHERE id = 1")
        await conn.execute("TRUNCATE execmirror_fills, smalllive_reviews, "
                           " smalllive_handoffs, smalllive_reconciliations")
        await conn.close()


@pg
async def test_a_paper_only_reconciliation_names_its_paper_position(
        monkeypatch):
    conn = await TE._conn()
    try:
        acct, venue, mirror = await TE._setup(conn, monkeypatch)
        po = await TE._paper_order(
            conn, acct, qty=2702, strategy="PINNACLE_EXPLORATION_PAPER",
            policy_version="PINNACLE_EXPLORATION_PAPER_V3")
        await TE._paper_fill(conn, acct, po, qty=2702)
        await TE._paper_fill(conn, acct, po, qty=702, direction="SELL",
                             role="STANDING_PROTECTION")
        await conn.execute("UPDATE execmirror_control SET enabled = false"
                           " WHERE id = 1")
        mirror._venue, mirror._venue_factory = None, _no_venue
        mirror._last_management = 0.0
        out = await M.Mirror.tick(mirror, conn)
        assert out["state"] == "DISABLED" and out["audrey_reconciled"] == 1
        rec = await conn.fetchrow("SELECT * FROM smalllive_reconciliations "
                                  " WHERE group_id = $1", po["group_id"])
        # rc6.2 pmus-exec (audit item 4): no current account snapshot (the
        # lane is off) -> STALE, never NOT_MIRRORED; the chain still names
        # the paper position and the verdict she would have given
        assert rec["status"] == M.AUDREY_STALE
        chain = json.loads(rec["chain"]) if isinstance(rec["chain"], str) \
            else rec["chain"]
        assert chain["stale"]["would_be"] == "NOT_MIRRORED"
        (pp,) = chain["positions"]["paper"]
        assert Decimal(pp["open_qty"]) == Decimal(2000)
        assert Decimal(chain["positions"]["actual"]["live_held"]) == 0
    finally:
        await conn.execute("UPDATE execmirror_control SET enabled = false, "
                           " stopped = false, stop_done_at = NULL WHERE id = 1")
        await conn.close()
