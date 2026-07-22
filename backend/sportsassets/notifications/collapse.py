"""Per-user burst collapsing. Pure logic, unit-tested.

Policy (spec §6): if more than `threshold` alerts from ONE whale would reach
one user within `window_seconds`, deliver a single summary push covering the
burst instead of individual pushes. The raw feed is untouched — collapsing
only shapes push delivery.

The dispatcher batches pending outbox rows and calls `plan_deliveries` per
recipient; recent-delivery history (what this user already got in the last
window) is passed in so collapsing works across dispatch ticks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .format import burst_summary, trade_body, trade_title


@dataclass
class Delivery:
    """One push to send to one recipient."""

    kind: str  # 'single' | 'summary'
    title: str
    body: str
    url: str
    trade_ids: list[int] = field(default_factory=list)


def plan_deliveries(
    pending: list[dict],
    recent_count_by_whale: dict[int, int],
    threshold: int = 5,
    base_url: str = "",
) -> list[Delivery]:
    """Turn pending trade payloads for ONE user into deliveries.

    pending: trade payloads (chronological) that passed the user's prefs.
    recent_count_by_whale: pushes already delivered to this user per whale
        within the collapse window (so a burst spanning two ticks collapses).
    Rule: for each whale, if already-delivered + pending would exceed
    `threshold` in the window, emit one summary for that whale's pending
    trades; otherwise emit singles.
    """
    by_whale: dict[int, list[dict]] = {}
    for t in pending:
        by_whale.setdefault(int(t["whale_id"]), []).append(t)

    deliveries: list[Delivery] = []
    for whale_id, trades in by_whale.items():
        already = recent_count_by_whale.get(whale_id, 0)
        if already + len(trades) > threshold:
            username = trades[0].get("whale_username") or f"whale#{whale_id}"
            deliveries.append(
                Delivery(
                    kind="summary",
                    title=burst_summary(username, trades),
                    body=f"{len(trades)} fills — open the live feed for detail",
                    url=f"{base_url}/?whale={whale_id}",
                    trade_ids=[int(t["id"]) for t in trades],
                )
            )
        else:
            for t in trades:
                deliveries.append(
                    Delivery(
                        kind="single",
                        title=trade_title(t),
                        body=trade_body(t),
                        url=f"{base_url}/trade/{t['id']}",
                        trade_ids=[int(t["id"])],
                    )
                )
    return deliveries


def passes_prefs(trade: dict, prefs: dict) -> bool:
    """Delivery filter (feed is never filtered). prefs keys:
    min_notional (float), muted_whales (list[int]), sports (list[str], empty=all)."""
    if float(trade.get("notional") or 0) < float(prefs.get("min_notional") or 0):
        return False
    if int(trade.get("whale_id", -1)) in set(prefs.get("muted_whales") or []):
        return False
    sports = prefs.get("sports") or []
    if sports and trade.get("sport") not in sports:
        # Unenriched trades ('unclassified') pass a sport filter: better an
        # occasional off-sport push than a late one.
        if trade.get("sport") != "unclassified":
            return False
    return True
