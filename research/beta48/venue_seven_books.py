"""Reconcile the seven unresolved mirror books against the venue.

READ ONLY, AND COUNTED. Every outbound request is counted before it is
made and refused past a hard cap, so the report can state the number
rather than estimate it. Retries and pagination count.

WHY NOT sportsassets.venue_reconcile.run(). That function is read-only
and AST-proven, and it is still the wrong tool under a 14-request cap:

    venue_bid(slug) for up to 80 slugs      up to 80 requests
    positions, MAX_PAGES                    up to 80
    activities, MAX_PAGES                   up to 80

Its three READ primitives are exactly what is needed; its orchestration
is not. So the primitives are re-implemented here with counting and a
cap, and this module is held to the same no-order discipline by
test_venue_seven_books_is_read_only.py, which walks this file's own AST.

WHAT A POSITION READ CAN AND CANNOT SETTLE. It gives CURRENT holdings.
It cannot, by itself, say when proceeds became spendable: a position
that is gone may have settled, been traded out, or never existed. Those
are distinguished here by joining to activities, not assumed from
absence.

HISTORICAL PEAK EXPOSURE IS NOT CURRENT EXPOSURE. peak_exposure_usd is
a high-water mark over a book's whole life. A book that traded out
still carries it. It is reported beside the venue's current position
and never in place of it.

Run on a runner with venue credentials, via venue-reconcile.yml.
"""
from __future__ import annotations

import json
import os
import sys
import time

# The seven books, pinned by slug so the reads are aimed. Filled from
# the seven-books SQL; overridable by env for a re-run.
BOOK_SLUGS = [s for s in (os.environ.get("SEVEN_SLUGS") or "").split(",")
              if s.strip()]

MAX_REQUESTS = int(os.environ.get("MAX_REQUESTS") or 14)
PAGE = 100
SINCE_TS = float(os.environ.get("SINCE_TS")
                 or (time.time() - 30 * 86400))


class Budget:
    """A cap that refuses rather than warns."""

    def __init__(self, cap):
        self.cap = cap
        self.n = 0
        self.log = []

    def spend(self, what):
        if self.n >= self.cap:
            raise RuntimeError(
                "REQUEST CAP REACHED at %d; refusing '%s'" % (self.cap, what))
        self.n += 1
        self.log.append({"n": self.n, "what": what,
                         "at": time.strftime("%H:%M:%SZ", time.gmtime())})
        return self.n


def main():
    from sportsassets import pmus

    b = Budget(MAX_REQUESTS)
    out = {"asof": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "cap": MAX_REQUESTS, "book_slugs": BOOK_SLUGS,
           "since_ts": SINCE_TS,
           "since_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                      time.gmtime(SINCE_TS))}
    client = pmus._get_client()

    # 1. POSITIONS -- what the account holds NOW.
    positions, cursor, pages = {}, "", 0
    try:
        while pages < 3:
            b.spend("positions page %d" % (pages + 1))
            resp = client.portfolio.positions(
                {"limit": PAGE, **({"cursor": cursor} if cursor else {})}) or {}
            for slug, p in (resp.get("positions") or {}).items():
                positions[str(slug)] = p or {}
            pages += 1
            cursor = resp.get("nextCursor") or ""
            if resp.get("eof") or not cursor:
                break
        out["positions_pages"] = pages
        out["positions_total"] = len(positions)
        out["positions_truncated"] = bool(cursor)
    except Exception as exc:                                # noqa: BLE001
        out["positions_error"] = repr(exc)[:300]

    # 2. OPEN ORDERS -- commitments that are not yet fills.
    try:
        b.spend("open_orders")
        open_ord = pmus.open_orders() or []
        out["open_orders_total"] = len(open_ord)
    except Exception as exc:                                # noqa: BLE001
        open_ord = []
        out["open_orders_error"] = repr(exc)[:300]

    # 3. ACTIVITIES -- newest first, so settlements of these books come
    #    first and a small page budget is enough.
    acts, cursor, pages = [], "", 0
    try:
        while pages < 8:
            b.spend("activities page %d" % (pages + 1))
            resp = client.portfolio.activities(
                {"limit": PAGE, "sortOrder": "SORT_ORDER_DESCENDING",
                 "types": ["ACTIVITY_TYPE_TRADE",
                           "ACTIVITY_TYPE_POSITION_RESOLUTION"],
                 **({"cursor": cursor} if cursor else {})}) or {}
            page = resp.get("activities") or []
            acts.extend(page)
            pages += 1
            oldest = None
            for a in page:
                for k in ("timestamp", "createdAt", "transactTime", "time"):
                    v = a.get(k)
                    if v:
                        try:
                            t = float(v)
                        except (TypeError, ValueError):
                            continue
                        oldest = t if oldest is None else min(oldest, t)
                        break
            cursor = resp.get("nextCursor") or ""
            if resp.get("eof") or not cursor or not page:
                break
            if oldest and oldest < SINCE_TS:
                break
        out["activities_pages"] = pages
        out["activities_total"] = len(acts)
        out["activities_truncated"] = bool(cursor)
    except Exception as exc:                                # noqa: BLE001
        out["activities_error"] = repr(exc)[:300]

    # ── per-book classification ──────────────────────────────────────
    def slug_of(d):
        for k in ("marketSlug", "us_market_slug", "slug"):
            v = d.get(k)
            if v:
                return str(v)
        for k in ("positionResolution", "trade"):
            sub = d.get(k) or {}
            if isinstance(sub, dict) and sub.get("marketSlug"):
                return str(sub["marketSlug"])
        return ""

    acts_by_slug = {}
    for a in acts:
        acts_by_slug.setdefault(slug_of(a), []).append(a)
    orders_by_slug = {}
    for o in open_ord:
        orders_by_slug.setdefault(slug_of(o), []).append(o)

    books = []
    for slug in BOOK_SLUGS:
        pos = positions.get(slug)
        net = None
        if pos:
            for k in ("netPosition", "net", "quantity", "shares"):
                if pos.get(k) is not None:
                    try:
                        net = float(pos[k])
                    except (TypeError, ValueError):
                        pass
                    break
        mine = acts_by_slug.get(slug, [])
        res = [a for a in mine
               if a.get("type") == "ACTIVITY_TYPE_POSITION_RESOLUTION"]
        trd = [a for a in mine if a.get("type") == "ACTIVITY_TYPE_TRADE"]
        resting = orders_by_slug.get(slug, [])

        if resting:
            cls = "RESTING_ORDER"
        elif net is not None and abs(net) > 1e-9:
            cls = "EXISTING_POSITION"
        elif res:
            cls = "SETTLED_POSITION"
        elif pos is not None or trd:
            cls = "STALE_LOCAL_RECORD"
        else:
            cls = "UNRESOLVED"

        books.append({
            "slug": slug, "classification": cls,
            "venue_position_present": pos is not None,
            "venue_net_position": net,
            "venue_open_orders": len(resting),
            "resolution_events": len(res),
            "trade_activities_in_window": len(trd),
            "resolution_detail": [
                {k: v for k, v in (a.get("positionResolution") or {}).items()
                 if k in ("marketSlug", "payout", "proceeds", "amount",
                          "quantity", "shares", "outcome")}
                | {"ts": a.get("timestamp") or a.get("createdAt")}
                for a in res[:4]],
        })
    out["books"] = books
    out["requests_made"] = b.n
    out["request_log"] = b.log

    print("== SEVEN-BOOK VENUE RECONCILIATION ==")
    print(json.dumps({k: v for k, v in out.items()
                      if k not in ("books", "request_log")},
                     indent=2, sort_keys=True))
    print("== PER BOOK ==")
    print(json.dumps(books, indent=2, sort_keys=True))
    print("== REQUESTS (%d of %d) ==" % (b.n, MAX_REQUESTS))
    print(json.dumps(b.log, indent=2))
    os.makedirs("out", exist_ok=True)
    with open("out/seven_books.json", "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print("STOPPED: %s" % exc)
        raise SystemExit(3)
