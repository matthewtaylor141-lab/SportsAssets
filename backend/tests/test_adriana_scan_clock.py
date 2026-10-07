"""Adriana evaluates a scan AS OF THE MOMENT ITS BOOKS WERE READ. Production
088af82: 21 of 139 claim structures were refused BOOK_TIME_IN_FUTURE because
the scan clock was taken before the read and the workers persisted fresher
books meanwhile. A book written during the read is current; a venue clock
genuinely ahead of ours (red team C14) is still refused; staleness only
gets stricter."""
from __future__ import annotations

import asyncio
import json
import time

from sportsassets import canonical_claims as CC
from sportsassets import canonical_claims_db as KCDB
from sportsassets.pm_bind import golden as G
from sportsassets.redteam import settlement as RTS


def _fixture(book_at: float):
    fx = CC.Fixture(event_key="CLOCK:TB@NYY", sport="BASEBALL", league="MLB",
                    start_epoch=time.time() + 4 * 3600,
                    outcome_kind="TWO_WAY", home="NYY", away="TB")
    a = G._inst("KALSHI", "K-NYY", "YES", "HOME", "0.42",
                kalshi_terms=G._kterms(), at=book_at)
    b = G._inst("POLYMARKET_US", "aec-mlb-tb-nyy", "NO", "HOME", "0.42",
                at=book_at)
    return fx, [a, b]


def _run(monkeypatch, *, book_lead_s: float, now=None):
    async def assemble(conn, *, now):
        fx, insts = _fixture(time.time() + book_lead_s)
        await asyncio.sleep(max(0.0, book_lead_s) + 0.2)   # the read takes time
        return [(fx, CC.build_claims(fx, insts), insts)]

    async def passthrough(conn, built):
        return built, {}
    monkeypatch.setattr(KCDB, "assemble", assemble)
    monkeypatch.setattr(RTS, "apply", passthrough)
    return asyncio.run(KCDB.claims_census(None, now=now))


def test_a_book_persisted_during_the_read_is_not_in_the_future(monkeypatch):
    res = _run(monkeypatch, book_lead_s=0.1)
    assert len(res["opportunities"]) + len(res["refusals"]) >= 1
    assert "BOOK_TIME_IN_FUTURE" not in json.dumps(res, default=str)


def test_a_venue_clock_ahead_of_ours_is_still_refused(monkeypatch):
    res = _run(monkeypatch, book_lead_s=0.0, now=time.time() - 30.0)
    assert "BOOK_TIME_IN_FUTURE" in json.dumps(res, default=str)
