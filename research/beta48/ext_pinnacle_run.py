"""Run PINNACLE_DEVIG_V1 against the REAL feed and record what happens.

WHY A RUNNER AND NOT THE API. `EDGE_ODDS_API_KEY` is provisioned on
edge-shadow and as a repository secret. It is NOT on sportsassets-api,
where the shadow decision path runs, and copying a production credential
between services is refused. A runner already has both this key and
DATABASE_URL, and `feed-coverage.yml` established the pattern, so the
valuation happens where the key already legitimately is.

WHAT IT DOES, once, bounded:

  1. asks the provider for h2h on the SUPPORTED sports only (measured:
     soccer_epl, soccer_mexico_ligamx, baseball_mlb carry Pinnacle;
     basketball_nba and icehockey_nhl carry none at all)
  2. pulls PINNACLE's own complete outcome set per event, with its
     last_update as the quote's observed_at and our fetch instant as
     received_at
  3. counts DISTINCT SHARP books quoting each mapped outcome
  4. tries to map the event to an unresolved venue contract in `markets`
  5. runs the real `bettor_external_shadow.evaluate`, which runs the real
     `bettor_entry_gate.admit`
  6. persists every record -- admitted or refused -- and prints the
     refusal census

IT WRITES NOTHING ELSE. No order, no trading flag, no control row, and
`external_valuations.order_submitted` has a CHECK that it is FALSE.

REPORTING RULE: this script never concludes that an opportunity exists.
It prints what it measured, including the reason each candidate failed,
and a run in which nothing clears is a result rather than a failure.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "backend"))

from sportsassets import bettor_external_shadow as X       # noqa: E402
from sportsassets import bettor_pinnacle_devig as D        # noqa: E402

BASE = "https://api.the-odds-api.com/v4"
REGIONS = os.environ.get("EDGE_ODDS_REGIONS", "eu,uk,us")

#: Only where Pinnacle was MEASURED to quote. The sport family is what
#: PINNACLE_DEVIG_V1's SUPPORTED table is keyed on.
SPORTS = (
    ("soccer_epl", "soccer"),
    ("soccer_mexico_ligamx", "soccer"),
    ("baseball_mlb", "baseball"),
)

SHARP = {"pinnacle", "betfair_ex_eu", "betfair_ex_uk", "betfair_ex_au",
         "smarkets", "matchbook", "betanysports", "lowvig"}

#: Polymarket US taker fee, the schedule this repo already uses:
#: theta * C * p * (1 - p) with theta = 0.0695.
THETA = 0.0695


def fee_fn(*, qty, price):
    return THETA * float(qty) * float(price) * (1.0 - float(price))


def _get(path: str, params: dict):
    url = "%s%s?%s" % (BASE, path, urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        body = json.loads(r.read().decode())
        return body, dict(r.headers)


def _iso_to_ts(iso) -> float | None:
    if not iso:
        return None
    from datetime import datetime, timezone
    try:
        return datetime.fromisoformat(
            str(iso).replace("Z", "+00:00")).astimezone(
            timezone.utc).timestamp()
    except ValueError:
        return None


def _norm(s) -> str:
    return " ".join(str(s or "").strip().lower().replace(".", "").split())


def pinnacle_sets(events: list, fetched_at: float) -> list:
    """One record per event that PINNACLE priced h2h on, complete set only.

    A set that is not complete is still returned, so the refusal
    OUTCOME_SET_INCOMPLETE is recorded rather than the event silently
    disappearing from the census.
    """
    out = []
    for ev in events:
        books = ev.get("bookmakers") or []
        pin = next((b for b in books if b.get("key") == "pinnacle"), None)
        # per-OUTCOME sharp depth, not per-event
        depth: dict = {}
        for b in books:
            if b.get("key") not in SHARP:
                continue
            for m in b.get("markets") or []:
                if m.get("key") != "h2h":
                    continue
                for oc in m.get("outcomes") or []:
                    depth.setdefault(_norm(oc.get("name")), set()).add(b["key"])
        if pin is None:
            out.append({"event": ev, "quote": None, "depth": depth,
                        "no_pinnacle": True})
            continue
        mkt = next((m for m in (pin.get("markets") or [])
                    if m.get("key") == "h2h"), None)
        if mkt is None:
            out.append({"event": ev, "quote": None, "depth": depth,
                        "no_pinnacle": True})
            continue
        outcomes = {}
        for oc in mkt.get("outcomes") or []:
            try:
                outcomes[str(oc.get("name"))] = float(oc.get("price"))
            except (TypeError, ValueError):
                pass
        observed = (_iso_to_ts(mkt.get("last_update"))
                    or _iso_to_ts(pin.get("last_update")))
        out.append({
            "event": ev, "depth": depth, "no_pinnacle": False,
            "quote": {
                "book": "pinnacle", "outcomes": outcomes,
                "observed_at": observed, "received_at": fetched_at,
                "event_key": None, "period": "FULL_GAME", "line": None,
                "settlement_rule": None,
            },
        })
    return out


MARKET_LOOKUP = """
    SELECT condition_id, title, event_title, slug, sport
      FROM markets
     WHERE NOT resolved AND NOT coalesce(closed, FALSE)
       AND (lower(coalesce(event_title, '')) LIKE $1
            OR lower(coalesce(title, '')) LIKE $1)
     LIMIT 20
"""


async def main() -> int:
    key = (os.environ.get("EDGE_ODDS_API_KEY") or "").strip()
    if not key:
        print("REFUSED: EDGE_ODDS_API_KEY is not set in this environment")
        return 1
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        print("REFUSED: DATABASE_URL is not set in this environment")
        return 1
    import asyncpg

    conn = await asyncpg.connect(dsn)
    written = 0
    census: dict = {}
    priced = mapped = admitted = 0
    events_seen = 0
    try:
        with open(os.path.join(os.path.dirname(__file__), "..", "..",
                              "backend", "migrations",
                              "103_external_valuations.sql")) as fh:
            await conn.execute(fh.read())

        # Does the venue list ANY unresolved market for these sports? The
        # first thing to measure, because if it does not then every
        # mapping refusal downstream has one cause.
        venue_sports = await conn.fetch(
            "SELECT coalesce(sport, '(null)') AS sport, count(*) AS n "
            "FROM markets WHERE NOT resolved AND NOT coalesce(closed, FALSE) "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 15")
        print("== unresolved venue markets by sport ==")
        for r in venue_sports:
            print("   %-28s %d" % (r["sport"], r["n"]))

        for sport_key, family in SPORTS:
            try:
                events, hdrs = _get("/sports/%s/odds" % sport_key,
                                    {"apiKey": key, "regions": REGIONS,
                                     "markets": "h2h",
                                     "oddsFormat": "decimal"})
            except Exception as exc:                       # noqa: BLE001
                print("\n%s: provider error %s: %s"
                      % (sport_key, type(exc).__name__, exc))
                continue
            fetched_at = time.time()
            rem = hdrs.get("x-requests-remaining")
            used = hdrs.get("x-requests-used")
            print("\n== %s: %d events  (credits used=%s remaining=%s) =="
                  % (sport_key, len(events), used, rem))
            events_seen += len(events)

            for rec in pinnacle_sets(events, fetched_at):
                ev = rec["event"]
                home, away = ev.get("home_team"), ev.get("away_team")
                ekey = "%s|%s|%s" % (sport_key, _norm(home), _norm(away))
                quote = rec["quote"]
                if quote is None:
                    # No Pinnacle on this event. Recorded as a census line
                    # without a row, since there is no quote to persist.
                    census["NO_PINNACLE_ON_EVENT"] = census.get(
                        "NO_PINNACLE_ON_EVENT", 0) + 1
                    continue
                priced += 1
                quote["event_key"] = ekey
                quote["settlement_rule"] = (
                    "REGULATION_90" if family == "soccer" else "FULL_GAME_9")

                # ── the venue mapping ───────────────────────────────
                hits = []
                for team in (home, away):
                    pat = "%" + _norm(team).replace(" ", "%") + "%"
                    hits += [dict(r) for r in
                             await conn.fetch(MARKET_LOOKUP, pat)]
                by_cid = {h["condition_id"]: h for h in hits}
                both = [h for h in by_cid.values()
                        if _norm(home).split()[0] in _norm(
                            h.get("event_title") or h.get("title"))
                        and _norm(away).split()[0] in _norm(
                            h.get("event_title") or h.get("title"))]

                contract = {
                    "venue": "PMUS", "sport_family": family, "market": "h2h",
                    "selection": home, "event_key": ekey,
                    "period": "FULL_GAME", "line": None,
                    "settlement_rule": quote["settlement_rule"],
                    "condition_id": None,
                }
                market_state = {"readable": False, "ask": None, "depth": None}
                if len(both) == 1:
                    mapped += 1
                    contract["condition_id"] = both[0]["condition_id"]
                elif len(both) > 1:
                    contract["condition_id"] = None

                books = len(rec["depth"].get(_norm(home), ()))
                out = X.evaluate(
                    contract=contract, quote=quote,
                    market_state=market_state,
                    execution_estimate={}, size=None,
                    risk={"permitted": True}, fee_fn=fee_fn,
                    now=fetched_at, outcome_books=books, armed=True)
                if len(both) > 1:
                    out["refusals"].append(D.R_AMBIGUOUS_MAPPING)
                elif len(both) == 0:
                    out["refusals"].append(D.R_NO_MAPPING)
                if out.get("admissible"):
                    admitted += 1
                for code in out["refusals"]:
                    census[code] = census.get(code, 0) + 1
                await X.persist(conn, out)
                written += 1

        print("\n===================== RESULT =====================")
        print("events examined ......... %d" % events_seen)
        print("pinnacle complete quotes  %d" % priced)
        print("venue contracts mapped .. %d" % mapped)
        print("rows persisted .......... %d" % written)
        print("ADMITTED (BUY) .......... %d" % admitted)
        print("\n-- refusal census, most common first --")
        for code, n in sorted(census.items(), key=lambda kv: -kv[1]):
            print("   %-46s %d" % (code, n))
        if admitted == 0:
            print("\nNo opportunity cleared. The census above is the reason,")
            print("and it is the deliverable -- nothing was forced.")
        db = await X.census(conn, hours=24)
        print("\n-- persisted summary --")
        print(json.dumps(db["summary"], default=str, indent=2))
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
