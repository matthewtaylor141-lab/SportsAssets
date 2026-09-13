#!/usr/bin/env python3
"""RUN 83.6B token selection -- THE SINGLE SOURCE OF THE SELECTION RULE.

This file is the ONE place the rule lives. The Colab notebook embeds this exact
source as its selection cell, and the GitHub Actions workflow runs this exact
file. Two copies of a selection rule are two rules, and the one that actually
ran would then be a question rather than a fact.

It is deliberately top-level code, not a function behind a main() guard, so the
notebook can run the same bytes as a cell and end up with TOKEN_IDS defined.

    python run836b_select_tokens.py --diagnose        # look, decide nothing
    python run836b_select_tokens.py --out tokens.json # freeze the ids

DISCOVERY IS NOT EVIDENCE. It only decides which token ids the frozen capture
is pointed at. The ids are passed explicitly on the command line and recorded
by the instrument itself in manifest.json, and that record -- not anything
here -- is the scientific input.

-------------------------------------------------------------------------------
WHY THE CLASSIFIER IS STRUCTURED AND NOT KEYWORD-BASED
-------------------------------------------------------------------------------
The first Actions run classified "Will Oprah Winfrey win the 2028 Democratic
presidential nomination?" as a sports market. The runner printed the reason
itself:

    sport signal text:description~epl        (group event:30829)

-- the market's `description` contains the substring "epl", which sat in the
sport-word list as the English Premier League abbreviation. Fifty-one markets
belonging to that ONE event matched the same way, which is also why only a
single market could be selected: the per-event rule was working correctly on a
candidate set that was entirely false positives.

Substring search over free text cannot tell a league abbreviation from three
letters inside an unrelated English word, so it is retired as a classifier. It
survives only as a separately labelled FALLBACK_TEXT signal that may NOT select
a market for this capture.
"""
from __future__ import annotations

import json
import sys

import httpx

TARGET_TOKENS = 4

GAMMA = "https://gamma-api.polymarket.com"

# ---- PROVENANCE CLASSES ---------------------------------------------------
# Only the first two may select a market for the scientific capture.
STRUCTURED_SPORTS_METADATA = "STRUCTURED_SPORTS_METADATA"
STRUCTURED_SPORTS_TAG = "STRUCTURED_SPORTS_TAG"
FALLBACK_TEXT = "FALLBACK_TEXT"
NOT_IDENTIFIED = "NOT_IDENTIFIED"
CAPTURE_ALLOWED_PROVENANCE = (STRUCTURED_SPORTS_METADATA, STRUCTURED_SPORTS_TAG)

# Sports tag slugs/labels are compared as WHOLE TOKENS, never as substrings.
# "epl" equals "epl"; it is not found inside "deeply".
SPORTS_TAG_TOKENS = frozenset("""
sports sport nfl nba mlb nhl ncaa ncaaf ncaab soccer football basketball
baseball hockey tennis golf ufc mma boxing cricket rugby f1 formula-1
motorsports epl premier-league laliga la-liga seriea serie-a bundesliga
ligue-1 mls champions-league uefa fifa olympics esports cs2 csgo lol dota
valorant games
""".split())

# Retired as a classifier. Kept only to LABEL what a text rule would have
# claimed, with its context, so a false positive is visible rather than argued.
FALLBACK_TEXT_WORDS = tuple(sorted(SPORTS_TAG_TOKENS))


def _rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("data", "markets", "results"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def _get(client, url, params):
    try:
        r = client.get(url, params=params)
    except Exception as exc:                                   # noqa: BLE001
        return None, "%s: %s" % (type(exc).__name__, exc)
    if r.status_code != 200:
        return None, "HTTP %d" % r.status_code
    try:
        return _rows(r.json()), "HTTP 200"
    except ValueError:
        return None, "HTTP 200 not JSON"


def _tokens(m):
    v = m.get("clobTokenIds") or m.get("clob_token_ids")
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return [str(t) for t in v if t] if isinstance(v, list) else []


def _tag_tokens(m):
    """Every tag slug/label on the market and on its events, as whole tokens."""
    out = set()

    def _add(value):
        if isinstance(value, str) and value.strip():
            out.add(value.strip().lower())

    for tag in (m.get("tags") or []):
        if isinstance(tag, dict):
            _add(tag.get("slug"))
            _add(tag.get("label"))
    for ev in (m.get("events") or []):
        if not isinstance(ev, dict):
            continue
        for tag in (ev.get("tags") or []):
            if isinstance(tag, dict):
                _add(tag.get("slug"))
                _add(tag.get("label"))
    return out


def _fallback_text_hit(m):
    """What a text rule WOULD have matched, plus the surrounding context.

    Reported so a false positive can be read in full instead of asserted. It
    never selects a market.
    """
    for field in ("question", "description", "slug"):
        text = str(m.get(field, "") or "")
        low = text.lower()
        for w in FALLBACK_TEXT_WORDS:
            i = low.find(w)
            if i >= 0:
                ctx = text[max(0, i - 45):i + len(w) + 45].replace("\n", " ")
                return "%s~%s" % (field, w), ctx
    return None, None


def classify(m):
    """Return (provenance, reason, fallback_context).

    A: structured sports metadata on the market itself.
    B: an explicit sports tag, matched as a whole token.
    C: free text -- labelled, never trusted for capture selection.
    """
    smt = m.get("sportsMarketType")
    if isinstance(smt, str) and smt.strip():
        return STRUCTURED_SPORTS_METADATA, "sportsMarketType=%s" % smt.strip(), None
    if m.get("gameStartTime"):
        return STRUCTURED_SPORTS_METADATA, "gameStartTime present", None
    if m.get("gameId"):
        return STRUCTURED_SPORTS_METADATA, "gameId present", None

    hits = sorted(_tag_tokens(m) & SPORTS_TAG_TOKENS)
    if hits:
        return STRUCTURED_SPORTS_TAG, "tag=%s" % ",".join(hits[:3]), None

    reason, ctx = _fallback_text_hit(m)
    if reason:
        return FALLBACK_TEXT, reason, ctx
    return NOT_IDENTIFIED, "no sports evidence", None


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
    """Group by the strongest identifier the row actually carries.

    The SOURCE is returned alongside the key, so grouping strength is reported
    rather than assumed. A row identifying no event falls back to a per-row key
    so distinctness degrades to market level instead of silently collapsing
    every such row into a single fake group.
    """
    evs = m.get("events") or []
    if evs and isinstance(evs[0], dict):
        for k in ("id", "slug", "ticker"):
            v = evs[0].get(k)
            if v:
                return "event.%s" % k, "event:%s" % v
    for k in ("gameId", "conditionId", "questionID", "questionId", "slug", "id"):
        v = m.get(k)
        if v:
            return k, "%s:%s" % (k, v)
    return "per-row fallback", "row:%d" % index


# =========================================================================
# FETCH -- several public queries, because one arbitrary page is not a sample
# =========================================================================
# The first run ranked "by 24h volume" across whatever 100 rows one unordered
# keyset page happened to return. That is not the busiest markets, only the
# busiest of an arbitrary page. These are tried in order and the FIRST that
# yields structurally-classified sports markets is used; every attempt's
# outcome is printed, so which query fed the selection is on the record.
ATTEMPTS = [
    (GAMMA + "/markets", {"closed": "false", "active": "true", "limit": 500,
                          "order": "volume24hr", "ascending": "false",
                          "related_tags": "true", "tag_id": 1}),
    (GAMMA + "/markets", {"closed": "false", "active": "true", "limit": 500,
                          "order": "volume24hr", "ascending": "false"}),
    (GAMMA + "/markets", {"closed": "false", "active": "true", "limit": 500}),
    (GAMMA + "/markets/keyset", {"closed": "false", "active": "true", "limit": 500}),
]

print("asking the public market list (discovery only; not evidence)...")
rows, source, attempt_log = [], None, []
with httpx.Client(timeout=60, follow_redirects=True) as _c:
    for _url, _params in ATTEMPTS:
        got, status = _get(_c, _url, _params)
        n_struct = 0
        if got:
            n_struct = sum(1 for r in got
                           if isinstance(r, dict)
                           and classify(r)[0] in CAPTURE_ALLOWED_PROVENANCE)
        attempt_log.append({"url": _url, "params": _params, "status": status,
                            "rows": len(got or []), "structured_sports": n_struct})
        print("  %-22s tag_id=%-4s %-18s rows=%-4s structured-sports=%s"
              % (_url.replace(GAMMA, ""), _params.get("tag_id", "-"), status,
                 len(got or []), n_struct))
        if got and n_struct > 0:
            rows, source = got, _url
            break
        if got and not rows:
            rows, source = got, _url          # keep the last readable page

if not rows:
    raise SystemExit(
        "STEP 4 FAILED: no public market list could be read. Nothing is "
        "substituted. Report this and stop.")
print("\nusing %d rows from %s" % (len(rows), source))

# ---- THE ACCEPTING-ORDERS GATE ------------------------------------------
# "acceptingOrders" and "enableOrderBook" are real current field names, declared
# on the current SDK's gamma Market model (models/gamma/market.py:71-78). Both
# are OPTIONAL (bool | None), so presence is decided per response.
ACCEPTING_FIELD = "acceptingOrders"
_carrying = sum(1 for m in rows if isinstance(m, dict) and ACCEPTING_FIELD in m)
DISCOVERY_ACCEPTING_ORDERS_GATE = "ENFORCED" if _carrying else "NOT_IDENTIFIED"
print("\nDISCOVERY_ACCEPTING_ORDERS_GATE = " + DISCOVERY_ACCEPTING_ORDERS_GATE
      + "  (%d of %d rows carry '%s')" % (_carrying, len(rows), ACCEPTING_FIELD))
if DISCOVERY_ACCEPTING_ORDERS_GATE == "NOT_IDENTIFIED":
    print("  the surface did not report accepting-orders status, so it is not")
    print("  used as a filter and nothing is assumed in its place.")

# =========================================================================
# ELIGIBILITY
# =========================================================================
candidates, rejected_fallback, grouping_counts = [], [], {}
for _idx, m in enumerate(rows):
    if not isinstance(m, dict):
        continue
    if m.get("closed") or m.get("active") is False:
        continue
    if DISCOVERY_ACCEPTING_ORDERS_GATE == "ENFORCED" and m.get(ACCEPTING_FIELD) is not True:
        continue
    if m.get("enableOrderBook") is False:
        continue
    toks = _tokens(m)
    if not toks:
        continue

    prov, reason, ctx = classify(m)
    if prov == NOT_IDENTIFIED:
        continue

    gsrc, gkey = _event_key(m, _idx)
    row = {
        "id": m.get("id"),
        "condition_id": m.get("conditionId"),
        "question": (m.get("question") or m.get("slug") or "?")[:70],
        "active": m.get("active"),
        "closed": m.get("closed"),
        "acceptingOrders": m.get("acceptingOrders"),
        "enableOrderBook": m.get("enableOrderBook"),
        "vol24": _num(m, "volume24hr", "volume24hrClob", "volumeNum", "volume"),
        "liq": _num(m, "liquidityNum", "liquidity"),
        "token_count": len(toks),
        "token": toks[0],
        "provenance": prov,
        "reason": reason,
        "fallback_context": ctx,
        "group_source": gsrc,
        "event": gkey,
    }
    if prov in CAPTURE_ALLOWED_PROVENANCE:
        candidates.append(row)
        grouping_counts[gsrc] = grouping_counts.get(gsrc, 0) + 1
    else:
        rejected_fallback.append(row)

candidates.sort(key=lambda e: (-e["vol24"], -e["liq"], str(e["id"])))

print("\nSPORTS_CLASSIFICATION: %d admitted on structured evidence; "
      "%d rejected as FALLBACK_TEXT only"
      % (len(candidates), len(rejected_fallback)))
for r in rejected_fallback[:5]:
    print("  rejected: %-50s %s" % (r["question"][:50], r["reason"]))
    if r["fallback_context"]:
        print("            context: ...%s..." % r["fallback_context"])

print("\nEVENT_GROUPING_SOURCE counts among admitted candidates:")
for k in ("event.id", "event.slug", "event.ticker", "gameId", "conditionId",
          "questionID", "questionId", "slug", "id", "per-row fallback"):
    print("  %-18s %d" % (k, grouping_counts.get(k, 0)))

chosen, seen = [], set()
for e in candidates:
    if e["event"] in seen:
        continue
    seen.add(e["event"])
    chosen.append(e)
    if len(chosen) >= TARGET_TOKENS:
        break

print("\n--- CANDIDATE TABLE (admitted on structured evidence only) ---")
for i, e in enumerate(candidates[:20], 1):
    print("%2d. %s" % (i, e["question"]))
    print("    id=%s cond=%s" % (e["id"], str(e["condition_id"])[:24]))
    print("    active=%s closed=%s accepting=%s book=%s vol24=$%s tokens=%d"
          % (e["active"], e["closed"], e["acceptingOrders"], e["enableOrderBook"],
             format(e["vol24"], ",.0f"), e["token_count"]))
    print("    SPORTS_CLASSIFICATION_PROVENANCE=%s  source=%s"
          % (e["provenance"], e["reason"]))
    print("    group=%s (source %s)" % (e["event"], e["group_source"]))

_fallback_selected = [e for e in chosen if e["group_source"] == "per-row fallback"]
if _fallback_selected:
    print("\nDISCLOSURE: %d selected market(s) group by per-row fallback, so their"
          % len(_fallback_selected))
    print("distinctness is MARKET level, not event level.")

print("\nPROPOSED TOKEN IDS:")
for e in chosen:
    print("  %s   # %s | %s" % (e["token"], e["question"][:48], e["provenance"]))

_ok = (len(chosen) >= 3
       and all(e["provenance"] in CAPTURE_ALLOWED_PROVENANCE for e in chosen)
       and all(e["closed"] is not True and e["active"] is not False for e in chosen)
       and all(e["token_count"] >= 1 for e in chosen))
RUN836E_SPORTS_SELECTION_VERIFIED = "YES" if _ok else "NO"
print("\nRUN836E_SPORTS_SELECTION_VERIFIED = " + RUN836E_SPORTS_SELECTION_VERIFIED)

if "--diagnose" in sys.argv:
    print("(diagnose mode: nothing frozen, nothing recorded)")
    raise SystemExit(0)

if RUN836E_SPORTS_SELECTION_VERIFIED != "YES":
    raise SystemExit(
        "STEP 4 STOPPED: only %d market(s) qualified on structured sports "
        "evidence, fewer than the 3 required. That is a real outcome, not an "
        "error to work around -- the capture is not weakened to fit what "
        "happens to be open." % len(chosen))

TOKEN_IDS = [e["token"] for e in chosen]

SELECTION_RECORD = {
    "discovery_source": source,
    "attempts": attempt_log,
    "rule": ("open sports markets admitted ONLY on structured evidence "
             "(sportsMarketType / gameStartTime / gameId / explicit sports "
             "tag), ranked by 24h volume, top one per event group"),
    "accepting_orders_gate": DISCOVERY_ACCEPTING_ORDERS_GATE,
    "event_grouping_source_counts": grouping_counts,
    "admitted_count": len(candidates),
    "rejected_fallback_text_count": len(rejected_fallback),
    "selected": chosen,
    "RUN836E_SPORTS_SELECTION_VERIFIED": RUN836E_SPORTS_SELECTION_VERIFIED,
}

if "--out" in sys.argv:
    _dest = sys.argv[sys.argv.index("--out") + 1]
    with open(_dest, "w", encoding="utf-8") as _fh:
        json.dump({"token_ids": TOKEN_IDS, "selection_record": SELECTION_RECORD},
                  _fh, indent=2)
    print("wrote " + _dest)
