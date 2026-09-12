#!/usr/bin/env python3
"""RUN 83.6B token selection -- THE SINGLE SOURCE OF THE SELECTION RULE.

This file is the ONE place the rule lives. The Colab notebook embeds this exact
source as its selection cell, and the GitHub Actions workflow runs this exact
file. Two copies of a selection rule are two rules, and the one that ran would
then be a question rather than a fact.

It is deliberately top-level code, not a function behind a main() guard, so the
notebook can run the same bytes as a cell and end up with TOKEN_IDS defined.

Run standalone:  python run836b_select_tokens.py --out tokens.json

DISCOVERY IS NOT EVIDENCE. It only decides which token ids the frozen capture
is pointed at. The ids it picks are passed explicitly on the command line and
recorded by the instrument itself in manifest.json, and that record -- not
anything here -- is the scientific input.
"""

import json, sys

import httpx
from datetime import datetime, timezone

TARGET_TOKENS = 4

# ---- THE SELECTION RULE, FIXED BEFORE ANY RECORDING ----------------------
# eligible : a sports market that is active, not closed, still accepting
#            orders, has an order book, and publishes at least one token id
# ranked by: 24-hour dollar volume, highest first -- a plain busyness proxy
#            chosen in advance. It is NOT anything about what the feed sends.
# taken    : the top market from each DIFFERENT event, first token of each
# This rule cannot see any message the feed later sends, because no message
# has been received when it runs.
# --------------------------------------------------------------------------

SPORT_WORDS = ("nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball",
               "baseball", "hockey", "tennis", "golf", "ufc", "mma", "boxing",
               "cricket", "rugby", "epl", "premier league", "la liga",
               "serie a", "bundesliga", "champions league", "ncaa",
               "college football", "f1", "formula", "esports", "cs2", "lol",
               "dota", "valorant", "sports")

def _rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("data", "markets", "results"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []

def _fetch():
    base = "https://gamma-api.polymarket.com"
    attempts = [
        # the path the current official SDK uses
        (base + "/markets/keyset", {"closed": "false", "active": "true", "limit": 500}),
        (base + "/markets", {"closed": "false", "active": "true", "limit": 500,
                             "order": "volume24hr", "ascending": "false"}),
        (base + "/markets", {"closed": "false", "active": "true", "limit": 500}),
    ]
    with httpx.Client(timeout=60, follow_redirects=True) as c:
        for url, params in attempts:
            try:
                r = c.get(url, params=params)
            except Exception as exc:
                print("  %s -> %s" % (url, type(exc).__name__))
                continue
            print("  %s -> HTTP %d" % (url, r.status_code))
            if r.status_code == 200:
                try:
                    rows = _rows(r.json())
                except ValueError:
                    continue
                if rows:
                    return rows, url
    return [], None

def _tokens(m):
    v = m.get("clobTokenIds") or m.get("clob_token_ids")
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return [str(t) for t in v if t] if isinstance(v, list) else []

def _sport_signal(m):
    """Return the reason this row counts as sport, or None.

    Returning the REASON rather than a bare bool is what makes a false
    positive diagnosable. The first run of this rule picked a politics market
    and there was no way to see which field had matched.
    """
    if m.get("gameStartTime"):
        return "field:gameStartTime"
    if m.get("sportsMarketType"):
        return "field:sportsMarketType"
    fields = {}
    for k in ("question", "slug", "description", "seriesSlug", "category"):
        fields[k] = str(m.get(k, "") or "")
    for i, ev in enumerate(m.get("events") or []):
        if not isinstance(ev, dict):
            continue
        fields["event%d.slug" % i] = str(ev.get("slug", "") or "")
        fields["event%d.title" % i] = str(ev.get("title", "") or "")
        for j, tag in enumerate(ev.get("tags") or []):
            if isinstance(tag, dict):
                fields["event%d.tag%d" % (i, j)] = (
                    str(tag.get("slug", "") or "") + " " + str(tag.get("label", "") or ""))
    for name, text in fields.items():
        low = text.lower()
        for w in SPORT_WORDS:
            if w in low:
                return "text:%s~%s" % (name, w)
    return None

def _is_sport(m):
    return _sport_signal(m) is not None

def _num(m, *keys):
    for k in keys:
        v = m.get(k)
        if v in (None, ""):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0

def _event_key(m, index=0):
    evs = m.get("events") or []
    if evs and isinstance(evs[0], dict):
        for k in ("id", "slug", "ticker"):
            v = evs[0].get(k)
            if v:
                return "event:%s" % v
    for k in ("gameId", "conditionId", "questionID", "questionId", "slug", "id"):
        v = m.get(k)
        if v:
            return "%s:%s" % (k, v)
    # Nothing identifies this row's event. Returning a constant here would make
    # every such row look like the SAME event and silently cap the selection at
    # one market -- which is exactly what happened on the first run. A per-row
    # key degrades "distinct events" to "distinct markets", which is weaker and
    # is reported as such rather than hidden.
    return "row:%d" % index

print("asking the public market list...")
rows, source = _fetch()
print("\n%d markets returned from %s" % (len(rows), source))
if not rows:
    raise SystemExit(
        "STEP 4 FAILED: the public market list returned nothing. Nothing is "
        "substituted. Report this and stop.")

# ---- THE ACCEPTING-ORDERS GATE ------------------------------------------
# "acceptingOrders" and "enableOrderBook" are REAL field names on the current
# discovery surface -- they are declared on the current SDK's own gamma Market
# model (models/gamma/market.py:71-78, validation_alias "acceptingOrders" /
# "enableOrderBook"). Neither name is invented here.
#
# Both are declared OPTIONAL (bool | None), so a given response may or may not
# carry them. That is decided per run, from the response in hand, rather than
# assumed either way:
#
#   carried by at least one row -> ENFORCED, and a market must be
#                                  acceptingOrders == True to qualify
#   carried by no row at all    -> NOT_IDENTIFIED, the gate is not applied,
#                                  and the public CLOB preflight + capture are
#                                  left to fail closed on their own
ACCEPTING_FIELD = "acceptingOrders"
_carrying = sum(1 for m in rows if ACCEPTING_FIELD in m)
DISCOVERY_ACCEPTING_ORDERS_GATE = "ENFORCED" if _carrying else "NOT_IDENTIFIED"
print("\nDISCOVERY_ACCEPTING_ORDERS_GATE = " + DISCOVERY_ACCEPTING_ORDERS_GATE
      + "  (" + str(_carrying) + " of " + str(len(rows))
      + " rows carry '" + ACCEPTING_FIELD + "')")
if DISCOVERY_ACCEPTING_ORDERS_GATE == "NOT_IDENTIFIED":
    print("  the discovery surface did not report accepting-orders status, so")
    print("  it is not used as a filter. Nothing is assumed in its place: the")
    print("  public feed and price-book checks still have to succeed.")

dropped_not_accepting = 0
eligible = []
for _idx, m in enumerate(rows):
    if m.get("closed") or m.get("active") is False:
        continue
    if DISCOVERY_ACCEPTING_ORDERS_GATE == "ENFORCED":
        if m.get(ACCEPTING_FIELD) is not True:
            dropped_not_accepting += 1
            continue
    if m.get("enableOrderBook") is False:
        continue
    toks = _tokens(m)
    if not toks:
        continue
    _sig = _sport_signal(m)
    if _sig is None:
        continue
    eligible.append({
        "question": (m.get("question") or m.get("slug") or "?")[:70],
        "slug": m.get("slug", ""),
        "event": _event_key(m, _idx),
        "vol24": _num(m, "volume24hr", "volume24hrClob", "volumeNum", "volume"),
        "liq": _num(m, "liquidityNum", "liquidity"),
        "token": toks[0],
        "sport_signal": _sig,
    })

_real_events = sum(1 for e in eligible if e["event"].startswith("event:"))
DISCOVERY_EVENT_GROUPING = ("EVENT_LEVEL" if _real_events == len(eligible) and eligible
                            else ("MIXED" if _real_events else "MARKET_LEVEL_ONLY"))
print("DISCOVERY_EVENT_GROUPING = " + DISCOVERY_EVENT_GROUPING
      + "  (%d of %d eligible rows carry an event identifier)" % (_real_events, len(eligible)))
if DISCOVERY_EVENT_GROUPING != "EVENT_LEVEL":
    print("  rows without an event identifier are kept apart per MARKET, so")
    print("  'different events' is only guaranteed where the identifier exists.")

eligible.sort(key=lambda e: (-e["vol24"], -e["liq"], e["slug"]))

chosen, seen = [], set()
for e in eligible:
    if e["event"] in seen:
        continue
    seen.add(e["event"])
    chosen.append(e)
    if len(chosen) >= TARGET_TOKENS:
        break

print("\n%d eligible open sports markets; taking the top %d from different "
      "events:\\n" % (len(eligible), len(chosen)))
for i, e in enumerate(chosen, 1):
    print("  %d. %s" % (i, e["question"]))
    print("     24h volume $%s | token %s" % (format(e["vol24"], ",.0f"), e["token"]))
    print("     sport signal %s | group %s" % (e["sport_signal"], e["event"]))

if "--diagnose" in sys.argv:
    print("\n--- DIAGNOSE: key names on the first row ---")
    print(sorted(rows[0].keys()) if rows else "(no rows)")
    print("\n--- DIAGNOSE: first 8 eligible ---")
    for e in eligible[:8]:
        print("  %-58s | %-26s | %s" % (e["question"][:58], e["sport_signal"], e["event"]))
    raise SystemExit(0)

TOKEN_IDS = [e["token"] for e in chosen]

if len(TOKEN_IDS) < 3:
    raise SystemExit(
        "STEP 4 FAILED: only %d eligible open sports market(s) right now, "
        "fewer than the 3 required. That is a real outcome, not an error to "
        "work around. Report it and stop -- the capture is not weakened to "
        "fit what happens to be open." % len(TOKEN_IDS))

SELECTION_RECORD = {
    "chosen_at_utc": datetime.now(tz=timezone.utc).isoformat(),
    "discovery_source": source,
    "rule": "open sports markets, ranked by 24h volume, top one per event",
    "accepting_orders_gate": DISCOVERY_ACCEPTING_ORDERS_GATE,
    "dropped_not_accepting_orders": dropped_not_accepting,
    "eligible_count": len(eligible),
    "event_grouping": DISCOVERY_EVENT_GROUPING,
    "selected": chosen,
}
print("\nDISCOVERY_ACCEPTING_ORDERS_GATE = " + DISCOVERY_ACCEPTING_ORDERS_GATE
      + " | dropped for not accepting orders: " + str(dropped_not_accepting))
print("\nSTEP 4 OK -- these token ids are now fixed and will not change.")

# --------------------------------------------------------------- standalone
# Only runs when invoked as a script with --out. In the notebook there is no
# --out argument, so nothing here fires and TOKEN_IDS is simply left defined.
if "--out" in sys.argv:
    _dest = sys.argv[sys.argv.index("--out") + 1]
    with open(_dest, "w", encoding="utf-8") as _fh:
        json.dump({"token_ids": TOKEN_IDS,
                   "selection_record": SELECTION_RECORD}, _fh, indent=2)
    print("wrote " + _dest)
