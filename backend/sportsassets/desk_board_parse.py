"""THE DESK BOARD'S PAGE PARSE, IMPORTABLE ON ITS OWN (RC6, 2026-10-09).

WHY THIS MODULE EXISTS. The API's desk sweep (pmus.list_desk_events, every
~2.5 min: 15 venue pages of 100 events, ~80,000 markets) parsed each page's
JSON and built its slim rows in a worker thread of the API process. A thread
is not off the interpreter: json.loads holds the interpreter lock in C for a
whole page, and the slim build is Python competing for the same lock, so the
API event loop starved while a sweep ran. Production 2026-10-09 (the loop
watchdog's persisted ring, research-sql rc6_api-responsive_loop_stalls.sql):
six of twenty API loop stalls of 2.3-3.3 s fell inside a sweep, with the
sweep thread in `_slim` / `re.sub` / the response decode and the watchdog
itself locked out for up to 0.8 s (its overrun); 9 of the 10 high-overrun
stalls of 2026-10-08 were inside sweeps too.

So the page parse and the slim build run in a CHILD PROCESS (pmus owns the
pool and its in-process fallback). That child imports this module and nothing
else of ours: json and re only, no database, no venue client, no credential.
The functions are PURE and are also what the in-process fallback runs, so a
page yields byte-for-byte the same rows wherever it is parsed
(test_rc6_api_responsive_desk pins it against the pre-RC6 `_slim`).
"""
from __future__ import annotations

import json
import re

#: the three keys venue_catalogue.PageWalk keys an event by (in its order)
WALK_KEYS = ("slug", "eventSlug", "id")


def clean_title(t: str | None) -> str | None:
    """A searchable matchup from a global market title.

    The whales' titles carry market decorations the US venue's search
    chokes on ("Spread: Atlanta Dream (-2.5)", "Halmstads BK vs. IK
    Sirius: O/U 3.5", "Celtic FC vs. Dundee FC - More Markets"). All 305
    of the first live copy attempts died in search with these — strip to
    the matchup itself.
    """
    if not t:
        return None
    t = t.split(" - More Markets")[0]
    t = re.sub(r"^(Spread|Total|Moneyline|O/U)\s*:\s*", "", t)
    if ":" in t:
        # Keep the side holding the matchup: "Canadian Open: A vs B" wants
        # the right side; "A vs. B: O/U 3.5" wants the left.
        parts = t.split(":")
        vs = [p for p in parts if " vs" in p.lower()]
        t = vs[-1] if vs else max(parts, key=len)
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -")
    return t or None


def ev_volume_usd(ev: dict) -> float | None:
    """Traded volume in dollars from the venue's own event row (desk v8
    feed cards sort on it). The listing payloads have carried the figure
    under several spellings across venue revisions — probe them
    defensively, TOTAL volume preferred, liquidity as the last resort.
    None when the venue doesn't say: volume is never invented."""
    for k in ("volume", "volumeNum", "volume24hr", "liquidity"):
        v = ev.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def px(v) -> float | None:
    try:
        f = float(v)
        return f if 0 < f < 1 else None
    except (TypeError, ValueError):
        return None


def slim_event(ev: dict) -> dict | None:
    """One raw venue event as the desk keeps it: its head (slug, title,
    league, start, volume_usd, close_time) and every open market as a slim
    row; None when the event carries no slug (the desk skips it). The rows
    are exactly what pmus._desk_sweep's `_slim` appended before RC6."""
    eslug = ev.get("slug") or ev.get("eventSlug") or ""
    if not eslug:
        return None
    out = {"slug": eslug,
           "title": clean_title(ev.get("title")) or eslug,
           "league": (eslug.split("-", 1)[0] or "").lower(),
           "start": ev.get("startTime") or ev.get("startDate"),
           "volume_usd": ev_volume_usd(ev),
           "close_time": (ev.get("endTime") or ev.get("endDate") or None),
           "markets": []}
    rows = out["markets"]
    for m in ev.get("markets") or []:
        if m.get("closed"):
            continue
        title = (clean_title(m.get("question") or m.get("title"))
                 or m.get("slug") or "")
        sides = [x for x in (m.get("marketSides") or [])
                 if isinstance(x, dict)]
        if sides:
            for x in sides:
                ident = x.get("identifier")
                desc = x.get("description")
                if not ident or not desc:
                    continue
                rows.append({
                    "us_slug": ident,
                    "kind": (ident.split("-", 1)[0] or "").lower(),
                    "label": f"{title} — {desc}",
                    "price": px(x.get("price")),
                    # THE VENUE'S OWN MARKET TYPE, RETAINED. `kind` above is
                    # OUR reading of the slug prefix, and the venue's Sports
                    # Schema directs consumers away from parsing identifiers.
                    # None when the venue omits it -- absence is a fact and
                    # must not read as a value.
                    "sports_market_type_v2": m.get("sportsMarketTypeV2"),
                    "sports_market_type": m.get("sportsMarketType"),
                    # participant identity, likewise the venue's
                    "team": (x.get("team") or {}).get("name")
                            if isinstance(x.get("team"), dict)
                            else x.get("team"),
                    "team_id": x.get("teamId")})
        elif m.get("slug"):
            p = next((v for v in (px(m.get(k)) for k in
                                  ("bestAsk", "best_ask", "price"))
                      if v is not None), None)
            rows.append({
                "us_slug": m["slug"],
                "kind": (m["slug"].split("-", 1)[0] or "").lower(),
                "label": (f"{title} — {m['outcome']}"
                          if m.get("outcome") else title),
                "price": p,
                "sports_market_type_v2": m.get("sportsMarketTypeV2"),
                "sports_market_type": m.get("sportsMarketType"),
                "team": None, "team_id": None})
    return out


def page_rows(doc) -> dict:
    """A decoded events.list page -> {"has_events", "stubs", "slims"}.

    `has_events` is the probe ladder's test (`page.get("events")` truthy).
    `stubs` are one small dict per dict event, in page order, carrying only
    the keys PageWalk keys an event by (WALK_KEYS, when present) plus "_i",
    the event's position here; `slims[_i]` is that event's slim_event. The
    walk dedupes and counts on the stubs exactly as on the raw events (it
    reads nothing else), and the caller merges the slims of the stubs it
    returns -- so the raw page never has to exist in the caller's process."""
    events = doc.get("events") if isinstance(doc, dict) else None
    stubs, slims = [], []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        st = {k: ev[k] for k in WALK_KEYS if k in ev}
        st["_i"] = len(slims)
        stubs.append(st)
        slims.append(slim_event(ev))
    return {"has_events": bool(events), "stubs": stubs, "slims": slims}


def parse_page(body: bytes) -> dict:
    """The CHILD PROCESS's entry point: the raw response body of one
    events.list page -> page_rows. An empty body is {} (the SDK's own rule:
    `if not response.text: return {}`)."""
    return page_rows(json.loads(body) if body else {})
