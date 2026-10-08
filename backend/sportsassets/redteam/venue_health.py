"""VENUE HEALTH ISOLATION + HELD / PRIORITY / GLOBAL FRESHNESS
(red_team.freshness_guard, bound to BETTOR's own records).

Two venues, two health domains, never one blended "market healthy":

  KALSHI          the Kalshi market-data loop's own heartbeat
                  (service kalshi_market_data, domain KALSHI_HEALTH): its
                  tracked books current inside the 30 s SLA (numerator /
                  denominator), its 429 backoff
  POLYMARKET_US   the PMUS paths' own records: held-position books
                  (bettor_paper_freshness: freshly manageable / markable),
                  and the institutional stream (service institutional_md):
                  connected, gap

A Kalshi 429 never touches POLYMARKET_US; a Polymarket reconnect never
makes KALSHI green. A cross-venue structure needs BOTH (pair_gate).

Three freshness denominators, never merged: HELD (open positions), PRIORITY
(the market plane's priority universe), GLOBAL (the whole universe). A 99%
global rate cannot hide one stale held book; capital reads HELD and
PRIORITY. SLAs are the system's own (no widening): Kalshi BOOK_SLA_S,
paper MARK_STALE_AFTER_S, the market plane's FRESH_SLA_S.
"""
from __future__ import annotations

import json

from ..red_team import freshness_guard as FG
from ..red_team.models import VenueHealth

KALSHI = "KALSHI"
POLYMARKET_US = "POLYMARKET_US"
HEARTBEAT_MAX_AGE_S = 300.0
MIN_RATE = 0.95


def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


async def _beat(conn, service: str):
    r = await conn.fetchrow("SELECT status, detail, extract(epoch FROM "
                            "beat_at) AS at FROM service_heartbeats WHERE "
                            "service = $1", service)
    if r is None:
        return None
    return {"status": r["status"], "detail": _j(r["detail"]) or {},
            "at": float(r["at"])}


def kalshi_health(beat: dict | None, *, now: float) -> VenueHealth:
    """KALSHI from its own heartbeat only. No heartbeat / a stale one is
    zero current books (never 'unknown = fine')."""
    if beat is None or now - beat["at"] > HEARTBEAT_MAX_AGE_S:
        d = (beat or {}).get("detail") or {}
        den = int(((d.get("freshness") or {}).get("denominator")) or 0)
        return VenueHealth(KALSHI, 0, den, den, 0, False,
                           (beat or {}).get("at") or 0.0,
                           "kalshi_market_data heartbeat %s" % (
                               "absent" if beat is None else "stale"))
    d = beat["detail"]
    f = d.get("freshness") or {}
    num, den = int(f.get("numerator") or 0), int(f.get("denominator") or 0)
    h = d.get("health") or {}
    return VenueHealth(KALSHI, num, den, max(0, den - num), 0,
                       bool(h.get("backing_off")), beat["at"],
                       "kalshi_market_data heartbeat (KALSHI_HEALTH)")


#: The institutional stream states that are a GAP for POLYMARKET_US (the
#: stream's own names: institutional_stream.S_*). "REFUSED" was listed here,
#: which the stream never reports -- its state is REFUSED_BY_VENUE.
STREAM_GAP_STATES = ("RECONNECTING", "GAVE_UP", "REFUSED_BY_VENUE",
                     "CREDENTIAL_REFUSED_BY_IDENTITY_GUARD",
                     "TRANSPORT_UNAVAILABLE", "STOPPED")


def stream_gap(stream_beat: dict | None, *, now: float) -> str | None:
    """PURE. Why the institutional_md stream state is a gap, or None.
    FAIL CLOSED: an absent heartbeat, one older than HEARTBEAT_MAX_AGE_S, or
    a beat without a stream digest is a gap (unknown is never healthy); so is
    a gap state, and -- as before -- any stream that is not connected
    (IDLE_NO_SYMBOLS_REQUESTED and DISABLED_BY_CONFIGURATION included)."""
    if stream_beat is None:
        return "STREAM_HEARTBEAT_ABSENT"
    if now - float(stream_beat.get("at") or 0.0) > HEARTBEAT_MAX_AGE_S:
        return "STREAM_HEARTBEAT_STALE"
    st = ((stream_beat.get("detail") or {}).get("stream"))
    if not isinstance(st, dict) or not st.get("state"):
        return "STREAM_STATE_UNREADABLE"
    state = str(st.get("state"))
    if state in STREAM_GAP_STATES:
        return "STREAM_%s" % state
    if st.get("connected") is not True:
        return "STREAM_NOT_CONNECTED:%s" % state
    return None


def polymarket_health(held: dict | None, stream_beat: dict | None, *,
                      now: float) -> VenueHealth:
    """POLYMARKET_US from the PMUS paths only: held books current over held
    books markable; a stream gap open is a gap (`stream_gap`, fail closed).
    A missing held read is zero current books."""
    held = held or {}
    den = int(held.get("markable") or 0)
    num = int(held.get("freshly_manageable") or 0)
    gaps = 1 if stream_gap(stream_beat, now=now) else 0
    return VenueHealth(POLYMARKET_US, num, den, max(0, den - num), gaps,
                       False, float(held.get("as_of") or now),
                       "bettor_paper_freshness (held PMUS books) + "
                       "institutional_md stream state")


def report(items: list, *, min_rate: float = MIN_RATE) -> dict:
    """The package's isolated report: one entry per venue, never blended."""
    return FG.isolated_venue_health(items, min_rate=min_rate, max_stale=0,
                                    max_gaps=0)


def pair_gate(rep: dict, venue_a: str, venue_b: str) -> dict:
    if venue_a == venue_b:
        a = rep.get(venue_a) or {}
        return {"green": bool(a.get("green")), "blockers": () if a.get(
            "green") else ("%s_NOT_HEALTHY" % venue_a,)}
    return FG.pair_market_data_gate(rep, venue_a, venue_b)


def denominators(*, held: dict | None, priority: dict | None,
                 total: dict | None) -> dict:
    """HELD / PRIORITY / GLOBAL apart; each a numerator / denominator, a
    rate and green against the 95% target, or UNMEASURED (never green)."""
    def one(name, num, den, source):
        if num is None or not den:
            return {"name": name, "numerator": num, "denominator": den,
                    "rate": None, "green": False, "status": "UNMEASURED",
                    "source": source}
        rate = round(float(num) / float(den), 4)
        return {"name": name, "numerator": int(num), "denominator": int(den),
                "rate": rate, "green": rate >= MIN_RATE,
                "status": "GREEN" if rate >= MIN_RATE else "RED",
                "source": source}
    h = held or {}
    p = priority or {}
    t = total or {}
    return {
        "held": one("HELD", h.get("freshly_manageable"), h.get("markable"),
                    "bettor_paper_freshness (open PAPER positions)"),
        "priority": one("PRIORITY", p.get("numerator"), p.get("denominator"),
                        "market plane priority universe"),
        "global": one("GLOBAL", t.get("numerator"), t.get("denominator"),
                      "market plane total universe"),
        "capital_reads": ["held", "priority"],
        "target": MIN_RATE}


async def read(conn, *, held: dict | None, priority: dict | None,
               total: dict | None, now: float) -> dict:
    k = kalshi_health(await _beat(conn, "kalshi_market_data"), now=now)
    sb = await _beat(conn, "institutional_md")
    p = polymarket_health(held, sb, now=now)
    rep = report([p, k])
    return {"venues": rep, "isolated": set(rep) == {KALSHI, POLYMARKET_US},
            "polymarket_stream_gap": stream_gap(sb, now=now),
            "blended_status": None,
            "cross_venue_pair": pair_gate(rep, POLYMARKET_US, KALSHI),
            "freshness": denominators(held=held, priority=priority,
                                      total=total)}
