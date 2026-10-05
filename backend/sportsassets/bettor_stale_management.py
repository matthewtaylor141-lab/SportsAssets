"""THE STALE-MANAGEMENT INTERFACE: ONE SMALL READ FUNCTION, NOTHING ELSE.

The paper turnaround lane (bettor_strategy_lifecycle) refuses new ENTRY orders
for a strategy whose STALE-MANAGEMENT RATE exceeds a declared threshold, so
stale exposure cannot keep growing. It does not own mark freshness: another
lane does (bettor_paper_ledger.latest_marks / MARK_STALE_AFTER_S and the paper
mark-refresh steps). This module is the seam between the two:

    stale_management_rate(conn, account_id, strategy, now=...) -> dict

It READS the freshness lane's answer and never edits it. A position counts as
STALE-MANAGED when its current mark is STALE (older than the freshness lane's
own MARK_STALE_AFTER_S) or UNAVAILABLE (no readable book): the strategy cannot
be managing it on current evidence. The rate is stale / open over the
strategy's OPEN positions on the account.

IF THE FRESHNESS LANE CHANGES ITS DEFINITION, this function follows it
(it calls `latest_marks`, it does not copy the rule); if it replaces
`latest_marks`, only this function changes. An unreadable answer is returned
as such (`ok: False`), never as a rate of zero.
"""
from __future__ import annotations

VERSION = "STALE_MANAGEMENT_INTERFACE_V1"


async def stale_management_rate(conn, account_id: str, strategy: str, *,
                                now: float) -> dict:
    from . import bettor_paper_ledger as L
    try:
        pos = [p for p in await L.positions(conn, account_id)
               if (p.get("strategy") or L.DEFAULT_STRATEGY) == strategy]
        marks = await L.latest_marks(conn, [p["us_market_slug"]
                                            for p in pos], now=now)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "version": VERSION, "rate": None,
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    stale, unavailable = [], []
    for p in pos:
        m = (marks.get(p["us_market_slug"]) or {}).get(p["holding_side"])
        if not m or m.get("price") is None:
            unavailable.append(p["position_key"])
        elif m.get("stale"):
            stale.append(p["position_key"])
    n = len(pos)
    return {"ok": True, "version": VERSION, "open_positions": n,
            "stale": len(stale), "unavailable": len(unavailable),
            "rate": (round((len(stale) + len(unavailable)) / n, 9)
                     if n else None),
            "stale_after_s": L.MARK_STALE_AFTER_S,
            "definition": ("open positions of this strategy whose current "
                           "mark is STALE (bettor_paper_ledger."
                           "MARK_STALE_AFTER_S) or UNAVAILABLE, over its "
                           "open positions"),
            "freshness_owner": "bettor_paper_ledger.latest_marks (read only)"}
