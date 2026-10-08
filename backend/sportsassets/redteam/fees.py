"""FEE EVIDENCE (red_team.fee_guard) for every money-path fee.

Each venue's fee on a route carries: venue, market / series key, schedule
id, schedule version, effective time, taker known, maker known. Unknown =
ineligible (never zero); a maker fee is never assumed zero.

  KALSHI         kalshi_fees.effective_terms: the venue's own fee-change id,
                 fee_type x multiplier (the version), its effective time
  POLYMARKET_US  bettor_fee_schedule.for_date: the dated published schedule,
                 its id, a content version (coefficients + rounding +
                 effective date), maker known only when VERIFIED_APPLIED
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .. import kalshi_fees as KF
from ..red_team import fee_guard as FG
from ..red_team.fee_guard import FeeEvidence


def _epoch_date(d: str) -> float | None:
    try:
        return datetime.fromisoformat(str(d)[:10]).replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def pmus_evidence(*, sport: str | None, at: float,
                  market_key: str) -> FeeEvidence:
    from .. import bettor_fee_schedule as FS
    from ..agents import adriana_arb as A
    when = datetime.fromtimestamp(float(at), timezone.utc)
    try:
        sched = FS.for_date(when.date().isoformat())
    except Exception:                                           # noqa: BLE001
        sched = None
    probe = A.order_fee(A.POLYMARKET_US, [(__import__("decimal").Decimal(
        "0.5"), 1)], at=when, sport=sport)
    if sched is None:
        return FeeEvidence("POLYMARKET_US", market_key, False, None, None,
                           None, False, False)
    version = hashlib.sha256(("%s|%s|%s|%s|%s|%s" % (
        sched.schedule_id, sched.effective_from, sched.theta_taker,
        sched.theta_maker, sched.taker_rounding, sched.maker_rounding)
    ).encode()).hexdigest()[:16]
    return FeeEvidence(
        venue="POLYMARKET_US", market_key=market_key,
        fee_known=bool(probe.known), schedule_id=sched.schedule_id,
        schedule_version="%s:%s" % (sched.status, version),
        effective_at=_epoch_date(sched.effective_from),
        maker_known=sched.status == "VERIFIED_APPLIED",
        taker_known=bool(probe.known))


def gate_row(e: FeeEvidence, *, now: float | None = None) -> dict:
    import time
    g = FG.fee_gate(e, now=float(now if now is not None else time.time()))
    return {"venue": e.venue, "market_key": e.market_key,
            "schedule_id": e.schedule_id,
            "schedule_version": e.schedule_version,
            "effective_at": e.effective_at, "fee_known": e.fee_known,
            "taker_known": e.taker_known, "maker_known": e.maker_known,
            "green": g["green"], "taker_eligible": g["taker_eligible"],
            "maker_eligible": g["maker_eligible"],
            "blockers": list(g["blockers"])}


def route_fee_evidence(*, kalshi_terms: dict | None, pmus_sport: str | None,
                       at: float, market_key: str) -> dict:
    """{venue: gate row} for the venues a route may use."""
    k = KF.fee_evidence(kalshi_terms, market_key=market_key)
    p = pmus_evidence(sport=pmus_sport, at=at, market_key=market_key)
    return {"KALSHI": dict(gate_row(k, now=at), terms=kalshi_terms),
            "POLYMARKET_US": gate_row(p, now=at),
            "account_discounts": KF.ACCOUNT_DISCOUNTS,
            "incentives": KF.INCENTIVES}


async def census(conn, *, now: float) -> list:
    """FeeEvidence for every tracked Kalshi series / event (terms in force
    now) and the PMUS schedule of today, for the FEE_EVIDENCE control."""
    from .. import canonical_claims_db as KCDB
    out = []
    if await conn.fetchval(
            "SELECT to_regclass('kalshi_fixtures_current') IS NOT NULL"):
        rows = await conn.fetch(
            "SELECT DISTINCT series_ticker, event_ticker, sport FROM "
            "kalshi_fixtures_current WHERE mapping_status = 'ESTABLISHED' "
            " AND start_at > now() - interval '4 hours' AND start_at < "
            " now() + interval '36 hours' LIMIT 200")
        for r in rows:
            t = await KCDB.kalshi_fee_terms(conn, r["series_ticker"],
                                            r["event_ticker"], now=now)
            out.append(KF.fee_evidence(t, market_key="%s/%s" % (
                r["series_ticker"], r["event_ticker"])))
    for sport in ("BASEBALL", "BASKETBALL", "HOCKEY", "FOOTBALL"):
        out.append(pmus_evidence(sport=sport, at=now,
                                 market_key="EXCHANGE_WIDE:%s" % sport))
    return out
