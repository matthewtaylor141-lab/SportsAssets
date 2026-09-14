#!/usr/bin/env python3
"""RUN 85 TRACK B-L — one frozen BLOCK. READ ONLY. NO CREDENTIAL.

Separate experiment. Never pooled with Track A. Track A is frozen, unmodified
and running; this job shares Track A's CONCURRENCY GROUP so the two can never
execute at the same time. That is the rate gate: platform-level mutual
exclusion over actual execution, not an argument about schedules.

Why mutual exclusion and not pacing. Two independently paced streams can
collide however each paces itself -- neither stream's spacing bounds the
combined minimum gap. And a pre-flight "is Track A running?" check cannot
help: GitHub creates a delayed scheduled run's record only when it starts
(Track A run #4: cron 18:00:00Z, record created 20:30:42Z), so a late run is
invisible for an unbounded period. Only the concurrency group survives that.

--------------------------------------------------------------------------
BLOCK LENGTH AND WHAT IT COSTS
--------------------------------------------------------------------------
A block is capped at 20 minutes so that a Track A segment falling due mid-block
waits minutes, not hours. Four 300 s cycles fit.

The long horizons are free because they are later cycles' t0 reads:

    5m  = cycle c+1      10m = cycle c+2      15m = cycle c+3

and therefore 30m and 60m are NOT reachable inside a 20-minute block. They are
reported NOT_OBSERVED_WITHIN_BLOCK_LENGTH rather than silently dropped. A
60-minute horizon needs a 60-minute block, which is a separate decision about
how long Track A may be made to wait.

--------------------------------------------------------------------------
THE SCHEDULE
--------------------------------------------------------------------------
Six market lanes, staggered so no two reads ever want the same instant. Burst
offsets {0, 5, 10, 30, 60} s differ by {5,10,20,25,30,50,55,60}, so a stagger
is admissible only if it avoids that set; the first six admissible staggers on
the 2.5 s grid are used. Minimum spacing inside the block is 2.5 s.

--------------------------------------------------------------------------
DISCOVERY -- THE CURRENT UNIVERSE, NOT THE ARCHIVE
--------------------------------------------------------------------------
/v1/events is queried WITH the venue-side scope filters active=true and
closed=false, which is what the working discovery path always sent. Without
them the endpoint serves the whole historical archive and no reachable offset
is the present -- that is exactly how BLOCK_2 walked to offset 64000 and found
August. Offset is the only pagination mechanism; page= and skip= were disproved
in Phase 2E. And because a parameter that was SENT says nothing about what came
back, the returned frame is re-counted (open / resolved / closed / quoted) and
the block REFUSES TO CAPTURE if the payload contradicts the scope.

--------------------------------------------------------------------------
SELECTION -- OBSERVABLE AT SELECTION TIME ONLY
--------------------------------------------------------------------------
Candidates are scored from the DISCOVERY payload alone, which under that query
already carries bestBidQuote/bestAskQuote per market, so scoring costs no extra
requests. Sport and league come from normalize_tag(), which reads the venue's
primaryTag OBJECT field by field -- never the object used as a key, never a
serialization of it treated as a sport.
Nothing about later profitability can enter, because nothing later exists yet.

Diversity is enforced, not optimised: one market per event, round robin over
sports, then over price band and spread bucket. spread/mid is RECORDED but is
never the objective -- B-L exists to learn whether tighter books trade
differently, not to assume it.

The block is frozen at selection: market list, identities, selection rule
version and timestamp are written before the first capture read.

--------------------------------------------------------------------------
ORDER MODEL
--------------------------------------------------------------------------
At each t0: LONG rests at bestBid(t0), SHORT rests at 1 - bestAsk(t0). Those
prices are frozen for the life of that hypothetical order. No repricing, no
chasing. A touch is never a fill.
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import importlib.util
import json
import platform
import sys
import time
from decimal import Decimal as D
from pathlib import Path

import httpx

_c = importlib.util.spec_from_file_location(
    "run85c", Path(__file__).with_name("run85_pmus_collector.py"))
C = importlib.util.module_from_spec(_c)
_c.loader.exec_module(C)

_b = importlib.util.spec_from_file_location(
    "run85b", Path(__file__).with_name("run85_phase2b.py"))
B = importlib.util.module_from_spec(_b)
_b.loader.exec_module(B)

_fe = importlib.util.spec_from_file_location(
    "run85fee", Path(__file__).with_name("run85_trackb_fees.py"))
FEE = importlib.util.module_from_spec(_fe)
_fe.loader.exec_module(FEE)

PHASE = "run85/trackbl/block/1"
SELECTION_RULE_VERSION = "BL-SELECT-2"

SPACING_S = 2.5
CYCLE_S = 300.0
BURST_OFFSETS = (0.0, 5.0, 10.0, 30.0, 60.0)
LANE_STAGGERS = (0.0, 2.5, 15.0, 17.5, 80.0, 82.5)
N_LANES = len(LANE_STAGGERS)
MAX_CYCLES = 4                                   # 20 minutes
DERIVABLE_LONG_HORIZONS = {"5m": 1, "10m": 2, "15m": 3}
UNREACHABLE_HORIZONS = ("30m", "60m")

MAX_DISCOVERY_PAGES = 16
MAX_VENUE_REQUESTS = 220
PAGE_LIMIT = 100
# THE BLOCK_2 REPAIR. The unfiltered /v1/events is the venue's ENTIRE HISTORICAL
# ARCHIVE, id-ascending: offset 0 was 2025-10-31 and offset 64000 was still only
# 2026-08-15..28, so a geometric crawl toward "the current end" ran out of
# configured search before it ran out of list, and every one of the 8,532 market
# rows it did reach was MARKET_STATUS_RESOLVED. These are the venue-side filters
# the WORKING discovery path (Phase 2B, 2G-R) always sent. With them the list IS
# the current universe, so offset 0 is the right place to start and there is no
# archive to crawl. Enumeration of history is not the objective and is not done.
DISCOVERY_QUERY = {"active": "true", "closed": "false"}

# ---------------------------------------------------------------- BL-SELECT-2
# BL-SELECT-1 IS RETIRED: ACTIVITY_BLIND_SELECTION. It scored candidates from
# the discovery payload alone, and /v1/events carries NO activity field at all
# (the only quantity is minimumTradeQty). Ranking on tightest spread/mid then
# selects the markets whose spread is narrow BECAUSE nobody is there: BLOCK_3's
# cohort included two markets that had not traded in ~55 hours and three with
# negligible or no lifetime volume, and produced 0 first-leg touches in 24
# cycles. BLOCK_3 is preserved exactly as it is, under BL-SELECT-1.
#
# BL-SELECT-2 adds a STAGE 2: probe /book for a prospective shortlist and read
# the venue's own activity fields before freezing anything. Thresholds are
# derived from the probed distribution and from the experiment's own 20-minute
# length -- never from any subsequent touch outcome.
MAX_BOOK_PROBES = 64             # stage-2 /book probes, inside the rate budget
SHORTLIST_PER_BAND = {"NEAR_MID": 28, "MODERATE": 22, "TAIL": 14}
# A market that has not traded in an hour is unlikely to trade in the next 20
# minutes. This floor is reasoned from the block length, fixed BEFORE the data.
MAX_SECONDS_SINCE_LAST_TRADE = 3600.0
BAND_TARGET = {"NEAR_MID": 2, "MODERATE": 2, "TAIL": 2}   # TAIL is a CEILING
BAND_CEILING = {"TAIL": 2}
HYPOTHETICAL_PAIR_CONTRACTS = 100        # for the displayed budget component
BODY_SAMPLE_EVENTS = 50          # head and tail retained per page, for diagnosis
REQUIRED_BLOCK_SIZE = 4          # below this the block FAILS; the target is N_LANES
EVENTS_PATH = "/v1/events"
BOOK_PATH = "/v1/markets/%s/book"

GAME_TYPES = ("SPORTS_MARKET_TYPE_MONEYLINE", "SPORTS_MARKET_TYPE_SPREAD",
              "SPORTS_MARKET_TYPE_TOTAL", "SPORTS_MARKET_TYPE_TOTALS",
              "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME")


def say(s=""):
    print(s)
    sys.stdout.flush()


def _fmt(v):
    return "-" if v is None else ("%.4f" % v if isinstance(v, float) else str(v))


def _census(rows, key):
    out = {}
    for r in rows:
        for reason in r.get(key) or []:
            out[reason] = out.get(reason, 0) + 1
    return dict(sorted(out.items()))


class Budget:
    def __init__(self, cap):
        self.cap, self.spent = cap, 0

    def take(self, n=1):
        if self.spent + n > self.cap:
            raise RuntimeError("BOUND_REACHED spent=%d cap=%d" % (self.spent, self.cap))
        self.spent += n


def amount(v):
    if v is None:
        return None
    raw = v.get("value") if isinstance(v, dict) else v
    try:
        return D(str(raw))
    except Exception:                                      # noqa: BLE001
        return None


def price_band(mid):
    if mid is None:
        return None
    return "TAIL" if mid < D("0.10") or mid > D("0.90") else (
        "NEAR_MID" if D("0.35") <= mid <= D("0.65") else "MODERATE")


def spread_bucket(spread, tick):
    if spread is None or not tick:
        return None
    t = D(str(tick))
    if t <= 0:
        return None
    n = (spread / t).to_integral_value()
    return "S_1T" if n <= 1 else ("S_2_3T" if n <= 3 else "S_4T_PLUS")


TAG_SHAPES = ("OBJECT", "STRING", "NULL", "UNKNOWN")
NOT_IDENTIFIED = "NOT_IDENTIFIED"


def _text(v):
    """A venue string, or None. Never a stringified object."""
    return v if isinstance(v, str) and v.strip() else None


def normalize_tag(raw):
    """Explicit, deterministic normalization of the venue's primaryTag.

    THE BLOCK_2 SECOND DEFECT. primaryTag is an OBJECT -- {"id", "label",
    "slug", "league": {...}} -- and the old code put it straight into `sport`,
    which select_block then used as a dict key. On a successful candidate frame
    that raises TypeError: unhashable type: 'dict'. Reproduced against a real
    sealed venue event before this was written.

    Known fields are parsed by name. The object is NEVER serialized and treated
    as a sport, and there is no text search over it: a shape this does not
    recognise is classified NOT_IDENTIFIED and carried, not guessed at and not
    crashed on. A bare string is supported only as a documented compatibility
    case -- the venue has not been observed to send one.
    """
    out = {"PRIMARY_TAG_SHAPE": "UNKNOWN", "PRIMARY_TAG_ID": None,
           "PRIMARY_TAG_LABEL": None, "PRIMARY_TAG_LEAGUE": None,
           "PRIMARY_TAG_SPORT_ID": None,
           "SPORT_KEY": NOT_IDENTIFIED, "LEAGUE_KEY": NOT_IDENTIFIED}
    if raw is None:
        out["PRIMARY_TAG_SHAPE"] = "NULL"
        return out
    if isinstance(raw, str):
        # COMPATIBILITY CASE ONLY, documented: not observed from this venue.
        out["PRIMARY_TAG_SHAPE"] = "STRING"
        out["PRIMARY_TAG_LABEL"] = _text(raw)
        out["SPORT_KEY"] = _text(raw) or NOT_IDENTIFIED
        out["LEAGUE_KEY"] = _text(raw) or NOT_IDENTIFIED
        return out
    if not isinstance(raw, dict):
        return out
    tid = raw.get("id")
    out["PRIMARY_TAG_ID"] = str(tid) if isinstance(tid, (str, int)) else None
    out["PRIMARY_TAG_LABEL"] = _text(raw.get("label"))
    league = raw.get("league")
    lg_slug = lg_name = None
    if isinstance(league, dict):
        lg_slug = _text(league.get("slug"))
        lg_name = _text(league.get("name"))
        sid = league.get("sportId")
        if isinstance(sid, int):
            out["PRIMARY_TAG_SPORT_ID"] = sid
    out["PRIMARY_TAG_LEAGUE"] = lg_slug or lg_name
    known = (out["PRIMARY_TAG_ID"] or out["PRIMARY_TAG_LABEL"]
             or out["PRIMARY_TAG_LEAGUE"])
    if not known:
        # an object, but none of the fields we know how to read
        return out
    out["PRIMARY_TAG_SHAPE"] = "OBJECT"
    # Sport grouping comes from the venue's own numeric sport id where it is
    # given, and never from prose. Diversity round-robin only ever sees these.
    out["SPORT_KEY"] = ("sportId:%d" % out["PRIMARY_TAG_SPORT_ID"]
                        if out["PRIMARY_TAG_SPORT_ID"] is not None
                        else NOT_IDENTIFIED)
    out["LEAGUE_KEY"] = (out["PRIMARY_TAG_LEAGUE"] or _text(raw.get("slug"))
                         or out["PRIMARY_TAG_ID"] or NOT_IDENTIFIED)
    return out


def tag_slugs(ev):
    """The event's tag slugs, read by name. Objects are never stringified."""
    out = []
    for t in (ev.get("tags") or []):
        s = _text(t.get("slug")) if isinstance(t, dict) else _text(t)
        if s and s not in out:
            out.append(s)
    return out


def candidates(events, now_epoch):
    """Score from the discovery payload only. No extra requests, no hindsight."""
    out = []
    for ev in events or []:
        eid = str(ev.get("id"))
        tags = tag_slugs(ev)
        tag = normalize_tag(ev.get("primaryTag"))
        sport = tag["SPORT_KEY"]
        for m in (ev.get("markets") or []):
            if m.get("closed") or m.get("archived") or not m.get("active"):
                continue
            if m.get("sportsMarketTypeV2") not in GAME_TYPES:
                continue
            b = amount(m.get("bestBidQuote"))
            a = amount(m.get("bestAskQuote"))
            if b is None or a is None or b <= 0 or a <= 0 or a <= b:
                continue
            tick = m.get("orderPriceMinTickSize")
            mid = (a + b) / 2
            sp = a - b
            out.append({
                "native_event_id": eid, "sport": sport,
                "league": tag["LEAGUE_KEY"], "tag_slugs": tags,
                "PRIMARY_TAG_SHAPE": tag["PRIMARY_TAG_SHAPE"],
                "PRIMARY_TAG_ID": tag["PRIMARY_TAG_ID"],
                "PRIMARY_TAG_LABEL": tag["PRIMARY_TAG_LABEL"],
                "PRIMARY_TAG_LEAGUE": tag["PRIMARY_TAG_LEAGUE"],
                "PRIMARY_TAG_SPORT_ID": tag["PRIMARY_TAG_SPORT_ID"],
                "market_slug": m.get("slug"),
                "sports_market_type_v2": m.get("sportsMarketTypeV2"),
                "tick_size": str(tick) if tick is not None else None,
                "bid": str(b), "ask": str(a), "mid": str(mid), "spread": str(sp),
                "spread_over_mid": str(sp / mid) if mid else None,
                "spread_bucket": spread_bucket(sp, tick),
                "price_band": price_band(mid),
                "game_start_time": m.get("gameStartTime"),
                "state": m.get("status"),
                "selected_from": "discovery_payload",
            })
    return out


# ======================= BL-SELECT-2: STAGE 2 AND THE GATES =================
def _epoch(ts):
    """Venue RFC3339 -> epoch seconds, or None. Never guessed."""
    if not isinstance(ts, str) or not ts:
        return None
    t = ts.replace("Z", "+00:00")
    if "." in t:                      # nanosecond precision the stdlib refuses
        head, rest = t.split(".", 1)
        frac = "".join(ch for ch in rest if ch.isdigit())[:6]
        tail = rest[len(frac):] if rest[len(frac):].startswith(("+", "-")) else "+00:00"
        for ch in rest:
            if ch in "+-":
                tail = rest[rest.index(ch):]
                break
        t = "%s.%s%s" % (head, frac or "0", tail)
    try:
        return datetime.datetime.fromisoformat(t).timestamp()
    except ValueError:
        return None


def shortlist(cands, now_epoch):
    """STAGE 1 -- a prospective shortlist, stratified by price band.

    Ranked on TIME TO GAME START, not on spread/mid. Discovery carries no
    activity field, so imminence is the only prospective activity proxy
    available before spending a /book request, and it uses nothing that
    happens after selection. One market per event.
    """
    best = {}
    for c in cands:
        eid = c["native_event_id"]
        st = _epoch(c.get("game_start_time"))
        c = dict(c, start_epoch=st,
                 seconds_to_start=(st - now_epoch) if st is not None else None)
        prev = best.get(eid)
        if prev is None or (D(c["spread_over_mid"] or "999")
                            < D(prev["spread_over_mid"] or "999")):
            best[eid] = c
    by_band = {}
    for c in best.values():
        by_band.setdefault(c["price_band"] or "UNKNOWN", []).append(c)

    def key(c):
        # imminent first; a game already under way sorts by how recently it
        # started. Unknown start times go last, deterministically.
        s = c["seconds_to_start"]
        return (s is None, abs(s) if s is not None else 0.0, c["market_slug"] or "")

    out = []
    for band, cap in SHORTLIST_PER_BAND.items():
        rows = sorted(by_band.get(band, []), key=key)
        out.extend(rows[:cap])
    return out[:MAX_BOOK_PROBES]


def activity_fields(row, probe_epoch):
    """STAGE 2 -- the venue's own activity and liquidity fields, by name.

    Every one of these is present in /book and ABSENT from /v1/events, which is
    precisely why BL-SELECT-1 could not see them.
    """
    md = ((row.get("body") or {}).get("marketData") or {})
    st = md.get("stats") or {}

    def num(v):
        raw = v.get("value") if isinstance(v, dict) else v
        try:
            return D(str(raw))
        except Exception:                                  # noqa: BLE001
            return None

    bids, offers = md.get("bids") or [], md.get("offers") or []
    b = bq = a = aq = None
    for lv in bids:
        p, q = amount(lv.get("px")), num(lv.get("qty"))
        if p is None or q is None or q <= 0:
            continue
        if b is None or p > b:
            b, bq = p, q
    for lv in offers:
        p, q = amount(lv.get("px")), num(lv.get("qty"))
        if p is None or q is None or q <= 0:
            continue
        if a is None or p < a:
            a, aq = p, q
    last = _epoch(st.get("lastTradeSetTime"))
    return {
        "market_state": md.get("state"),
        "SECONDS_SINCE_LAST_TRADE": (probe_epoch - last) if last is not None else None,
        "last_trade_set_time": st.get("lastTradeSetTime"),
        "SHARES_TRADED": num(st.get("sharesTraded")),
        "NOTIONAL_TRADED": num(st.get("notionalTraded")),
        "OPEN_INTEREST": num(st.get("openInterest")),
        "BEST_BID": b, "BEST_ASK": a,
        "BEST_BID_SIZE": bq, "BEST_ASK_SIZE": aq,
        "probe_epoch": probe_epoch,
        "selected_from": "book_probe_receipt",
    }


def derive_economics(af, tick):
    """SPREAD_* and the DISPLAYED_PAIR_BUDGET component. Independent of activity."""
    b, a = af.get("BEST_BID"), af.get("BEST_ASK")
    if b is None or a is None or a <= b or b <= 0:
        return {"SPREAD_ABSOLUTE": None, "SPREAD_TICKS": None,
                "SPREAD_OVER_MID": None, "MID": None,
                "PRICE_BAND": None, "DISPLAYED_PAIR_BUDGET": None,
                "DISPLAYED_SPREAD_CAPTURE": None, "REBATE_LONG": None,
                "REBATE_SHORT": None}
    mid, sp = (a + b) / 2, a - b
    ticks = None
    if tick:
        t = D(str(tick))
        if t > 0:
            ticks = int((sp / t).to_integral_value())
    c = HYPOTHETICAL_PAIR_CONTRACTS
    _, rl = FEE.maker_rebate(c, b)
    _, rs = FEE.maker_rebate(c, D(1) - a)
    cap = sp * c
    return {"SPREAD_ABSOLUTE": sp, "SPREAD_TICKS": ticks,
            "SPREAD_OVER_MID": sp / mid, "MID": mid,
            "PRICE_BAND": price_band(mid),
            "DISPLAYED_SPREAD_CAPTURE": cap,
            "REBATE_LONG": rl, "REBATE_SHORT": rs,
            "DISPLAYED_PAIR_BUDGET": cap + rl + rs}


def pctiles(vals):
    """p10/p25/median/p75/p90 over the non-null values, plus n and n_missing."""
    xs = sorted(float(v) for v in vals if v is not None)
    out = {"n": len(xs), "n_missing": len(list(vals)) - len(xs)}
    if not xs:
        return out
    def at(p):
        return xs[min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))]
    out.update({"p10": at(0.10), "p25": at(0.25), "median": at(0.50),
                "p75": at(0.75), "p90": at(0.90)})
    return out


def activity_gate(probed, dist):
    """The ACTIVITY component. Threshold is prospective, twice over.

    A market passes only if it BOTH traded inside the reasoned absolute floor
    (MAX_SECONDS_SINCE_LAST_TRADE, fixed from the 20-minute block length before
    any data) AND sits in the more active half of what was actually probed
    today. Neither half looks at any subsequent touch outcome; the second is
    read off the distribution printed above it, so the rule is auditable.
    """
    med_sec = dist["SECONDS_SINCE_LAST_TRADE"].get("median")
    med_not = dist["NOTIONAL_TRADED"].get("median")
    for m in probed:
        secs, notl = m.get("SECONDS_SINCE_LAST_TRADE"), m.get("NOTIONAL_TRADED")
        oi = m.get("OPEN_INTEREST")
        reasons = []
        if secs is None:
            reasons.append("NO_LAST_TRADE_TIME")
        elif secs > MAX_SECONDS_SINCE_LAST_TRADE:
            reasons.append("STALE_GT_%ds" % int(MAX_SECONDS_SINCE_LAST_TRADE))
        elif med_sec is not None and secs > med_sec:
            reasons.append("SLOWER_THAN_PROBED_MEDIAN")
        if notl is None or notl <= 0:
            reasons.append("NO_TRADED_NOTIONAL")
        elif med_not is not None and float(notl) < med_not:
            reasons.append("NOTIONAL_BELOW_PROBED_MEDIAN")
        if oi is None or oi <= 0:
            reasons.append("NO_OPEN_INTEREST")
        m["ACTIVITY_ELIGIBLE"] = not reasons
        m["activity_reject"] = reasons
    return probed


def economic_gate(probed):
    """The LIQUIDITY and DISPLAYED_PAIR_BUDGET components, reported separately."""
    for m in probed:
        reasons = []
        if m.get("market_state") != "MARKET_STATE_OPEN":
            reasons.append("NOT_OPEN")
        if m.get("BEST_BID") is None or m.get("BEST_ASK") is None:
            reasons.append("ONE_SIDED")
        if not m.get("SPREAD_TICKS"):
            reasons.append("NO_POSITIVE_SPREAD")
        for side in ("BEST_BID_SIZE", "BEST_ASK_SIZE"):
            if not m.get(side) or m[side] <= 0:
                reasons.append("NO_" + side)
        bud = m.get("DISPLAYED_PAIR_BUDGET")
        if bud is None or bud <= 0:
            reasons.append("NO_DISPLAYED_BUDGET")
        m["ECONOMICALLY_ELIGIBLE"] = not reasons
        m["economic_reject"] = reasons
    return probed


def select_block_v2(final, cap=N_LANES):
    """Diversity-aware selection. Price regimes are TARGETS, never manufactured.

    NEAR_MID >= 2, MODERATE >= 2, TAIL <= 2 where the board supplies them. The
    TAIL ceiling is enforced; the NEAR_MID and MODERATE targets are attempted
    and an inability to meet them is REPORTED, never fixed by relaxing a gate.
    """
    by_band = {}
    for m in final:
        by_band.setdefault(m.get("PRICE_BAND") or "UNKNOWN", []).append(m)
    for rows in by_band.values():
        # most recently traded first, then deterministic by slug
        rows.sort(key=lambda r: ((r.get("SECONDS_SINCE_LAST_TRADE")
                                  if r.get("SECONDS_SINCE_LAST_TRADE") is not None
                                  else float("inf")), r["market_slug"] or ""))
    out, used_events = [], set()

    def take(band, n):
        got = 0
        for m in by_band.get(band, []):
            if got >= n or len(out) >= cap:
                break
            if m in out or m["native_event_id"] in used_events:
                continue
            out.append(m)
            used_events.add(m["native_event_id"])
            got += 1
        return got

    for band, n in BAND_TARGET.items():
        take(band, n)
    # fill any remainder from the most active eligible markets, TAIL capped
    rest = sorted((m for m in final if m not in out),
                  key=lambda r: ((r.get("SECONDS_SINCE_LAST_TRADE")
                                  if r.get("SECONDS_SINCE_LAST_TRADE") is not None
                                  else float("inf")), r["market_slug"] or ""))
    for m in rest:
        if len(out) >= cap:
            break
        band = m.get("PRICE_BAND") or "UNKNOWN"
        ceil = BAND_CEILING.get(band)
        if ceil is not None and sum(1 for x in out
                                    if (x.get("PRICE_BAND") or "UNKNOWN") == band) >= ceil:
            continue
        if m["native_event_id"] in used_events:
            continue
        out.append(m)
        used_events.add(m["native_event_id"])
    return out


def select_block(cands, cap=N_LANES):
    """Round robin over sports, then strata. One market per event. Deterministic."""
    by_event = {}
    for c in cands:
        by_event.setdefault(c["native_event_id"], []).append(c)
    # one market per event: the tightest spread/mid available for that event,
    # chosen on selection-time evidence only
    picks = {}
    for eid, rows in by_event.items():
        rows.sort(key=lambda r: (D(r["spread_over_mid"] or "999"), r["market_slug"]))
        picks[eid] = rows[0]
    by_sport = {}
    for eid, r in picks.items():
        if not r["sport"]:
            continue
        by_sport.setdefault(r["sport"], []).append(r)
    for rows in by_sport.values():
        rows.sort(key=lambda r: (D(r["spread_over_mid"] or "999"), r["market_slug"]))
    order = sorted(by_sport, key=lambda s: (-len(by_sport[s]), s))
    out, i = [], 0
    while len(out) < cap and any(by_sport.values()):
        s = order[i % len(order)]
        if by_sport[s]:
            out.append(by_sport[s].pop(0))
        i += 1
        if i > 10000:
            break
    return out


def page_record(offset, r, evs, body_bytes):
    """Everything needed to tell PAGINATION_ERROR from PARSER_SHAPE_ERROR from
    TRUE_ZERO_CANDIDATES, without keeping a 68 MB page."""
    ids = [str(e.get("id")) for e in (evs or [])]
    return {
        "path": EVENTS_PATH,
        "params": {"limit": PAGE_LIMIT, "offset": offset},
        "offset": offset,
        "http_status": r.get("http_status"),
        "error": r.get("error"),
        "response_bytes": r.get("response_bytes"),
        "response_sha256": r.get("response_sha256"),
        "body_sha256_recomputed": hashlib.sha256(body_bytes).hexdigest(),
        "event_count": len(ids),
        "first_event_id": ids[0] if ids else None,
        "last_event_id": ids[-1] if ids else None,
        "event_ids": ids,
        "top_level_keys": sorted((r.get("body") or {}).keys())
                          if isinstance(r.get("body"), dict) else None,
    }


def sample_body(evs):
    """Bounded raw sample: the head and tail events of a page, verbatim."""
    if not evs:
        return {"events": [], "sampled": 0, "total": 0}
    head = evs[:BODY_SAMPLE_EVENTS]
    tail = evs[-BODY_SAMPLE_EVENTS:] if len(evs) > BODY_SAMPLE_EVENTS else []
    return {"events_head": head, "events_tail": tail,
            "sampled": len(head) + len(tail), "total": len(evs)}


def query_for(offset):
    """The discovery query. The venue-side scope filters are part of it."""
    q = dict(DISCOVERY_QUERY)
    q["limit"] = PAGE_LIMIT
    q["offset"] = offset          # offset is the ONLY pagination mechanism
    return q


def get_page(http, pacer, budget, offset, pages, samples):
    budget.take()
    r, _rows = B.get_paced(http, EVENTS_PATH, pacer, query_for(offset))
    body = r.get("body") if isinstance(r.get("body"), dict) else {}
    evs = body.get("events") or []
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    rec = page_record(offset, r, evs, raw)
    pages.append(rec)
    samples.append({"offset": offset, **sample_body(evs)})
    return evs, rec


def scope_census(events):
    """VERIFY THE FRAME THE VENUE RETURNED. A query parameter that was SENT is
    not evidence about what came back -- BLOCK_2 sent a walk it believed reached
    the present and got August. These counts are re-derived from the payload."""
    c = {"EVENTS_RETURNED": len(events), "EVENTS_WITH_CLOSED_TRUE": 0,
         "MARKETS_TOTAL": 0, "MARKETS_OPEN": 0, "MARKETS_RESOLVED": 0,
         "MARKETS_WITH_BID_AND_ASK": 0,
         "PRIMARY_TAG_OBJECT_COUNT": 0, "PRIMARY_TAG_STRING_COUNT": 0,
         "PRIMARY_TAG_NULL_COUNT": 0, "PRIMARY_TAG_UNKNOWN_COUNT": 0}
    for e in events:
        if e.get("closed") is True:
            c["EVENTS_WITH_CLOSED_TRUE"] += 1
        c["PRIMARY_TAG_%s_COUNT" % normalize_tag(e.get("primaryTag"))
          ["PRIMARY_TAG_SHAPE"]] += 1
        for m in (e.get("markets") or []):
            c["MARKETS_TOTAL"] += 1
            st = m.get("status")
            if st == "MARKET_STATUS_OPEN":
                c["MARKETS_OPEN"] += 1
            elif st == "MARKET_STATUS_RESOLVED":
                c["MARKETS_RESOLVED"] += 1
            if (amount(m.get("bestBidQuote")) is not None
                    and amount(m.get("bestAskQuote")) is not None):
                c["MARKETS_WITH_BID_AND_ASK"] += 1
    return c


def discover(http, pacer, budget, pages, samples, res):
    """Walk the CURRENT-UNIVERSE list forward from offset 0.

    With active=true&closed=false the list IS the current universe, so offset 0
    is the correct place to start and the archive is never crawled. Offset is
    the only pagination mechanism; page= and skip= were disproved in Phase 2E
    (HTTP 200 with the identical first page) and are never used.
    """
    events, seen = [], set()
    prev_ids = None
    terminal = None
    for i in range(MAX_DISCOVERY_PAGES):
        off = i * PAGE_LIMIT
        evs, rec = get_page(http, pacer, budget, off, pages, samples)
        say("  page   offset=%-6d events=%-4d first=%s last=%s"
            % (off, rec["event_count"], rec["first_event_id"], rec["last_event_id"]))
        if rec["event_count"] == 0:
            terminal = off                 # a REAL terminal boundary, observed
            break
        if i == 0:
            res["FIRST_PAGE_EVENT_IDS"] = rec["event_ids"][:10]
        if prev_ids is not None:
            overlap = len(set(rec["event_ids"]) & set(prev_ids))
            res["OVERLAP_CHECK"] = "%d shared ids" % overlap
            res["PAGINATION_ADVANCES"] = "YES" if overlap == 0 else "NO"
        prev_ids = rec["event_ids"]
        for e in evs:
            k = str(e.get("id"))
            if k not in seen:
                seen.add(k)
                events.append(e)

    res["QUERY_FILTERS"] = dict(DISCOVERY_QUERY)
    res["DISCOVERY_LIST_EXHAUSTED"] = "YES" if terminal is not None else "NO"
    res["FIRST_TERMINAL_OFFSET"] = terminal
    res["discovery_pages"] = len(pages)
    res["discovery_events"] = len(events)
    res.update(scope_census(events))
    # the owner's preflight spelling, same numbers, not recomputed
    res["EVENTS_DISCOVERED"] = res["EVENTS_RETURNED"]
    res["MARKET_ROWS"] = res["DISCOVERY_MARKET_ROWS"] = res["MARKETS_TOTAL"]
    res["OPEN_MARKET_ROWS"] = res["MARKETS_OPEN"]
    res["RESOLVED_MARKET_ROWS"] = res["MARKETS_RESOLVED"]
    return events


def run_block(http, pacer, budget, block, log_fh, res):
    """Four 300 s cycles. Long horizons fall out of later cycles' t0 reads."""
    t0 = time.monotonic()
    slots = []
    for cyc in range(MAX_CYCLES):
        for lane, m in enumerate(block):
            base = cyc * CYCLE_S + LANE_STAGGERS[lane]
            for off in BURST_OFFSETS:
                slots.append((base + off, lane, m["market_slug"], cyc, off))
    slots.sort()
    gaps = [slots[i + 1][0] - slots[i][0] for i in range(len(slots) - 1)]
    res["planned_min_gap_s"] = min(gaps) if gaps else None
    res["planned_reads"] = len(slots)
    if res["planned_min_gap_s"] is not None and res["planned_min_gap_s"] < SPACING_S - 1e-9:
        raise RuntimeError("PLAN_VIOLATES_SPACING min=%.4f" % res["planned_min_gap_s"])

    last = None
    n429 = 0
    for target, lane, slug, cyc, off in slots:
        budget.take()
        when = t0 + target
        now = time.monotonic()
        floor = (last + SPACING_S) if last is not None else now
        fire = max(when, floor)
        if fire > now:
            time.sleep(fire - now)
        start = time.monotonic()
        last = start
        row = C._get(http, BOOK_PATH % slug, None)
        row["_bl"] = {"lane": lane, "cycle": cyc, "burst_offset_s": off,
                      "planned_s": target, "actual_s": start - t0,
                      "timing_error_s": (start - t0) - target}
        log_fh.write(json.dumps(row, default=str) + "\n")
        if row.get("http_status") == 429:
            n429 += 1
            pacer.on_429((row.get("response_headers") or {}).get("retry-after"))
            last = time.monotonic()
        elif row.get("http_status") == 200:
            pacer.on_success()
    res["http_429"] = n429
    res["cycles"] = MAX_CYCLES
    res["block_elapsed_s"] = time.monotonic() - t0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--block", required=True)
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res = {}
    budget = Budget(MAX_VENUE_REQUESTS)
    pacer = B.AdaptivePacer(base=SPACING_S)

    meta = {
        "phase": PHASE, "block": a.block,
        "selection_rule_version": SELECTION_RULE_VERSION,
        "python": sys.version, "platform": platform.platform(),
        "httpx": httpx.__version__, "gateway": C.GATEWAY_BASE,
        "spacing_s": SPACING_S, "cycle_s": CYCLE_S,
        "burst_offsets_s": list(BURST_OFFSETS),
        "lane_staggers_s": list(LANE_STAGGERS),
        "max_cycles": MAX_CYCLES,
        "derivable_long_horizons": DERIVABLE_LONG_HORIZONS,
        "unreachable_horizons": list(UNREACHABLE_HORIZONS),
        "unreachable_reason": "NOT_OBSERVED_WITHIN_BLOCK_LENGTH",
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "selection_timestamp_utc": None,
        "planned_start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    pages, samples, probe_rows = [], [], []
    res["planned_capture_reads"] = N_LANES * MAX_CYCLES * len(BURST_OFFSETS)
    say("=== RATE BUDGET, COMPUTED BEFORE ANY VENUE CONTACT ===")
    say("TRACK_A_RATE            0.2860 rps  (its design maximum)")
    say("B_L_DISCOVERY_RATE      %.4f rps  (%d pages at the %.1f s floor)"
        % (1 / SPACING_S, MAX_DISCOVERY_PAGES, SPACING_S))
    say("B_L_BOOK_PROBE_RATE     %.4f rps  (<=%d probes at the floor)"
        % (1 / SPACING_S, MAX_BOOK_PROBES))
    say("B_L_CAPTURE_RATE        %.4f rps  (%d reads over %.0f s)"
        % (res["planned_capture_reads"] / (MAX_CYCLES * CYCLE_S + max(BURST_OFFSETS)),
           res["planned_capture_reads"], MAX_CYCLES * CYCLE_S + max(BURST_OFFSETS)))
    say("TOTAL_WORST_CASE_RATE   %.4f rps  -- NOT A SUM. The shared concurrency"
        % (1 / SPACING_S))
    say("  group is platform-level mutual exclusion, so at most one stream runs")
    say("  at any instant and every request waits behind the same floor. Adding")
    say("  the rates would describe a world the group makes impossible.")
    say("REQUEST BUDGET          %d + %d + %d = %d of %d"
        % (MAX_DISCOVERY_PAGES, MAX_BOOK_PROBES, res["planned_capture_reads"],
           MAX_DISCOVERY_PAGES + MAX_BOOK_PROBES + res["planned_capture_reads"],
           MAX_VENUE_REQUESTS))
    say("MIN_REQUEST_SPACING_S   %.1f  (unchanged, never weakened)" % SPACING_S)
    say()
    with httpx.Client(timeout=30.0) as http:
        say("=== DISCOVERY (current universe, offset walk from 0) ===")
        events = discover(http, pacer, budget, pages, samples, res)
        say()
        say("=== QUERY-SCOPE VERIFICATION (re-derived from the payload) ===")
        for k in ("QUERY_FILTERS", "EVENTS_RETURNED", "EVENTS_WITH_CLOSED_TRUE",
                  "MARKETS_TOTAL", "MARKETS_OPEN", "MARKETS_RESOLVED",
                  "MARKETS_WITH_BID_AND_ASK", "DISCOVERY_LIST_EXHAUSTED",
                  "FIRST_TERMINAL_OFFSET", "PAGINATION_ADVANCES", "OVERLAP_CHECK"):
            say("%-28s %s" % (k, res.get(k)))
        # A parameter that was SENT is not evidence about what came back.
        scope_fail = None
        if res.get("EVENTS_WITH_CLOSED_TRUE"):
            scope_fail = "FAILED_QUERY_SCOPE_NOT_HONOURED"
        elif not res.get("MARKETS_OPEN"):
            scope_fail = "FAILED_NO_OPEN_MARKET_ROWS"
        now = time.time()
        cands = candidates(events, now)
        res["candidates"] = res["STRUCTURALLY_ELIGIBLE_MARKETS"] = len(cands)
        res["CURRENT_EVENTS"] = res["EVENTS_DISCOVERED"]
        say("structurally eligible markets: %d" % len(cands))

        # ---------------- STAGE 1: prospective shortlist ----------------
        short = shortlist(cands, now)
        res["SHORTLIST"] = len(short)
        say("stage-1 shortlist (by imminence, stratified by band): %d" % len(short))
        say("   %s" % {b: sum(1 for m in short if m["price_band"] == b)
                       for b in SHORTLIST_PER_BAND})

        # ---------------- STAGE 2: /book activity probe ----------------
        say()
        say("=== STAGE 2: BOOK ACTIVITY PROBE ===")
        probed = []
        for m in short:
            if budget.spent + 1 > MAX_VENUE_REQUESTS - res["planned_capture_reads"]:
                say("   probe budget exhausted at %d probes" % len(probed))
                break
            budget.take()
            r, _rows = B.get_paced(http, BOOK_PATH % m["market_slug"], pacer, None)
            probe_rows.append({"market_slug": m["market_slug"],
                               "http_status": r.get("http_status"),
                               "response_sha256": r.get("response_sha256"),
                               "probe_wall_utc": r.get("local_request_wall_utc"),
                               "body": r.get("body")})
            if r.get("http_status") == 200:
                pacer.on_success()
            af = activity_fields(r, time.time())
            ec = derive_economics(af, m.get("tick_size"))
            probed.append({**m, **af, **ec})
        res["BOOKS_PROBED"] = len(probed)
        say("   books probed: %d" % len(probed))

        # ---- distributions FIRST, thresholds after -- never the reverse ----
        dist = {k: pctiles([m.get(k) for m in probed]) for k in
                ("SECONDS_SINCE_LAST_TRADE", "NOTIONAL_TRADED",
                 "SHARES_TRADED", "OPEN_INTEREST")}
        res["RECENT_TRADE_DISTRIBUTION"] = dist["SECONDS_SINCE_LAST_TRADE"]
        res["NOTIONAL_DISTRIBUTION"] = dist["NOTIONAL_TRADED"]
        res["SHARES_DISTRIBUTION"] = dist["SHARES_TRADED"]
        res["OPEN_INTEREST_DISTRIBUTION"] = dist["OPEN_INTEREST"]
        say()
        say("   CANDIDATE DISTRIBUTIONS (before any threshold is applied)")
        for k, d in dist.items():
            say("     %-26s n=%-4s miss=%-4s p10=%-12s p25=%-12s med=%-12s "
                "p75=%-12s p90=%s"
                % (k, d.get("n"), d.get("n_missing"),
                   _fmt(d.get("p10")), _fmt(d.get("p25")), _fmt(d.get("median")),
                   _fmt(d.get("p75")), _fmt(d.get("p90"))))

        activity_gate(probed, dist)
        economic_gate(probed)
        act = [m for m in probed if m["ACTIVITY_ELIGIBLE"]]
        eco = [m for m in probed if m["ECONOMICALLY_ELIGIBLE"]]
        final = [m for m in probed
                 if m["ACTIVITY_ELIGIBLE"] and m["ECONOMICALLY_ELIGIBLE"]]
        res["ACTIVITY_ELIGIBLE"] = len(act)
        res["ECONOMICALLY_ELIGIBLE"] = len(eco)
        res["FINAL_CANDIDATES"] = len(final)
        res["activity_rejects"] = _census(probed, "activity_reject")
        res["economic_rejects"] = _census(probed, "economic_reject")
        say()
        say("   ACTIVITY_ELIGIBLE        %d   rejects %s"
            % (len(act), res["activity_rejects"]))
        say("   ECONOMICALLY_ELIGIBLE    %d   rejects %s"
            % (len(eco), res["economic_rejects"]))
        say("   FINAL_CANDIDATES         %d   (both gates)" % len(final))

        block = select_block_v2(final)
        meta["selection_timestamp_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        res["block_size"] = len(block)
        say()
        say("=== FROZEN BLOCK %s (%d markets) ===" % (a.block, len(block)))
        for m in block:
            say("  %s" % str(m["market_slug"])[:70])
            say("     sport=%-14s league=%-12s event=%s"
                % (m["sport"], str(m["league"])[:12], m["native_event_id"]))
            say("     price mid=%-8s spread=%-8s ticks=%-3s s/m=%-8s band=%s"
                % (m.get("MID"), m.get("SPREAD_ABSOLUTE"), m.get("SPREAD_TICKS"),
                   _fmt(float(m["SPREAD_OVER_MID"]) if m.get("SPREAD_OVER_MID")
                        is not None else None), m.get("PRICE_BAND")))
            say("     ACTIVITY   since_last_trade=%-10s shares=%-12s notional=%-14s oi=%s"
                % (_fmt(m.get("SECONDS_SINCE_LAST_TRADE")), m.get("SHARES_TRADED"),
                   m.get("NOTIONAL_TRADED"), m.get("OPEN_INTEREST")))
            say("     LIQUIDITY  best_bid_size=%-12s best_ask_size=%s"
                % (m.get("BEST_BID_SIZE"), m.get("BEST_ASK_SIZE")))
            say("     BUDGET     spread_capture=%-10s reb_long=%-8s reb_short=%-8s "
                "pre_adverse_selection_pair_budget=%s"
                % (m.get("DISPLAYED_SPREAD_CAPTURE"), m.get("REBATE_LONG"),
                   m.get("REBATE_SHORT"), m.get("DISPLAYED_PAIR_BUDGET")))
        blob = json.dumps(block, indent=1, sort_keys=True).encode()
        (out / "block_cohort.json").write_bytes(blob)
        meta["block_cohort_sha256"] = hashlib.sha256(blob).hexdigest()

        # ---- PREFLIGHT, reported before anything is captured ----
        res["DISTINCT_EVENTS"] = len({m["native_event_id"] for m in block})
        res["SPORTS"] = sorted({m["sport"] for m in block if m.get("sport")})
        res["PRICE_BANDS"] = sorted({m.get("PRICE_BAND") for m in block
                                     if m.get("PRICE_BAND")})
        res["SPREAD_REGIMES"] = sorted({spread_bucket(m.get("SPREAD_ABSOLUTE"),
                                                      m.get("tick_size"))
                                        for m in block} - {None})
        res["LEAGUES"] = sorted({str(m["league"]) for m in block if m.get("league")})
        bands = [m.get("PRICE_BAND") for m in block]
        res["NEAR_MID_COUNT"] = bands.count("NEAR_MID")
        res["MODERATE_COUNT"] = bands.count("MODERATE")
        res["TAIL_COUNT"] = bands.count("TAIL")
        res["SELECTED_MARKETS"] = len(block)
        res["BAND_TARGETS_MET"] = {
            "NEAR_MID>=2": res["NEAR_MID_COUNT"] >= 2,
            "MODERATE>=2": res["MODERATE_COUNT"] >= 2,
            "TAIL<=2": res["TAIL_COUNT"] <= 2}
        # An unmet target is REPORTED. It is never met by relaxing a gate.
        res["BAND_TARGET_SHORTFALL_REASON"] = (
            "BOARD_DID_NOT_SUPPLY_ELIGIBLE_MARKETS_IN_BAND"
            if not all(res["BAND_TARGETS_MET"].values()) else None)
        res["QUEUE_REGIMES"] = "NOT_IDENTIFIED_AT_SELECTION (needs book reads)"
        # The rule DID change between blocks: BLOCK_3 ran BL-SELECT-1, this runs
        # BL-SELECT-2. What is unchanged is the rule WITHIN this block -- frozen
        # before the first capture read and never touched afterwards. Spelling it
        # the long way so nobody reads "UNCHANGED" as "same as BLOCK_3".
        res["SELECTION_RULE_UNCHANGED_WITHIN_BLOCK"] = "YES"
        res["SELECTION_RULE_SUPERSEDES"] = "BL-SELECT-1 (RETIRED_FOR_TRACK_B_L)"
        res["SHARED_CONCURRENCY_GROUP"] = "YES"
        res["BLOCK_FROZEN"] = "YES"
        res["BLOCK_SIZE"] = len(block)
        res["REQUIRED_BLOCK_SIZE"] = REQUIRED_BLOCK_SIZE
        res["RATE_GATE"] = "PASS"
        res["TRACK_A_OVERLAP"] = "IMPOSSIBLE_BY_SHARED_CONCURRENCY"
        say()
        say("=== PREFLIGHT ===")
        res["SELECTION_RULE_VERSION"] = SELECTION_RULE_VERSION
        for k in ("QUERY_FILTERS", "CURRENT_EVENTS", "EVENTS_DISCOVERED",
                  "EVENTS_WITH_CLOSED_TRUE", "MARKET_ROWS", "OPEN_MARKET_ROWS",
                  "RESOLVED_MARKET_ROWS", "MARKETS_WITH_BID_AND_ASK",
                  "PAGINATION_ADVANCES", "OVERLAP_CHECK",
                  "DISCOVERY_LIST_EXHAUSTED",
                  "STRUCTURALLY_ELIGIBLE_MARKETS", "SHORTLIST", "BOOKS_PROBED",
                  "RECENT_TRADE_DISTRIBUTION", "NOTIONAL_DISTRIBUTION",
                  "SHARES_DISTRIBUTION", "OPEN_INTEREST_DISTRIBUTION",
                  "ACTIVITY_ELIGIBLE", "ECONOMICALLY_ELIGIBLE",
                  "FINAL_CANDIDATES", "SELECTED_MARKETS", "DISTINCT_EVENTS",
                  "NEAR_MID_COUNT", "MODERATE_COUNT", "TAIL_COUNT",
                  "BAND_TARGETS_MET", "BAND_TARGET_SHORTFALL_REASON",
                  "PRIMARY_TAG_OBJECT_COUNT", "PRIMARY_TAG_STRING_COUNT",
                  "PRIMARY_TAG_NULL_COUNT", "PRIMARY_TAG_UNKNOWN_COUNT",
                  "SPORTS", "LEAGUES", "PRICE_BANDS", "SPREAD_REGIMES",
                  "QUEUE_REGIMES", "SELECTION_RULE_VERSION",
                  "SELECTION_RULE_SUPERSEDES",
                  "SELECTION_RULE_UNCHANGED_WITHIN_BLOCK",
                  "REQUIRED_BLOCK_SIZE", "BLOCK_SIZE", "BLOCK_FROZEN",
                  "RATE_GATE", "TRACK_A_OVERLAP", "SHARED_CONCURRENCY_GROUP"):
            say("%-28s %s" % (k, res.get(k)))
        say()
        if scope_fail:
            say("BLOCK_STATUS = %s -- the venue's own payload contradicts the "
                "query scope; no capture, exit non-zero." % scope_fail)
            res["BLOCK_STATUS"] = scope_fail
        elif len(block) < REQUIRED_BLOCK_SIZE:
            say("BLOCK_STATUS = FAILED_BLOCK_TOO_SMALL "
                "(%d < %d) -- no capture, exit non-zero."
                % (len(block), REQUIRED_BLOCK_SIZE))
            res["BLOCK_STATUS"] = "FAILED_BLOCK_TOO_SMALL"
        else:
            say("=== CAPTURE (%d cycles x %ds) ===" % (MAX_CYCLES, int(CYCLE_S)))
            meta["actual_start_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            with gzip.open(out / "block_log.jsonl.gz", "wt") as fh:
                run_block(http, pacer, budget, block, fh, res)
            meta["actual_end_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    res.setdefault("BLOCK_STATUS", "OK" if res.get("cycles") else "FAILED_NO_CAPTURE")
    res["venue_requests"] = budget.spent
    res["throttle_events"] = pacer.events
    res["SCIENTIFIC_OBSERVATIONS"] = res.get("planned_reads", 0) if res.get("cycles") else 0
    res["ECONOMICALLY_USABLE"] = "YES" if res["BLOCK_STATUS"] == "OK" else "NO"
    (out / "runner_metadata.json").write_text(json.dumps(meta, indent=1))
    (out / "block_summary.json").write_text(json.dumps(res, indent=1, default=str))
    # Discovery diagnostics are sealed WHETHER OR NOT the block succeeds -- they
    # are the only evidence that can separate a pagination error from a parser
    # error from a genuinely unsuitable universe.
    with gzip.open(out / "discovery_pages.jsonl.gz", "wt") as fh:
        for r in pages:
            fh.write(json.dumps(r, default=str) + "\n")
    with gzip.open(out / "discovery_body_samples.jsonl.gz", "wt") as fh:
        for r in samples:
            fh.write(json.dumps(r, default=str) + "\n")
    # BL-SELECT-2: the stage-2 probe receipts are the selection-time evidence.
    # Sealed whether or not the block succeeds, for the same reason the
    # discovery bodies are.
    with gzip.open(out / "book_probe_receipts.jsonl.gz", "wt") as fh:
        for r in probe_rows:
            fh.write(json.dumps(r, default=str) + "\n")

    lines = ["=== RUN 85 TRACK B-L BLOCK %s ===" % a.block,
             "SELECTION_RULE_VERSION = %s" % SELECTION_RULE_VERSION,
             "BLOCK_COHORT_SHA256 = %s" % meta.get("block_cohort_sha256"),
             "BLOCK_STATUS = %s" % res.get("BLOCK_STATUS"),
             "BLOCK_SIZE = %d" % res.get("block_size", 0),
             "REQUIRED_BLOCK_SIZE = %d" % REQUIRED_BLOCK_SIZE,
             "SCIENTIFIC_OBSERVATIONS = %s" % res.get("SCIENTIFIC_OBSERVATIONS"),
             "ECONOMICALLY_USABLE = %s" % res.get("ECONOMICALLY_USABLE"),
             "QUERY_FILTERS = %s" % json.dumps(res.get("QUERY_FILTERS"),
                                               sort_keys=True),
             "EVENTS_DISCOVERED = %s" % res.get("EVENTS_DISCOVERED"),
             "EVENTS_WITH_CLOSED_TRUE = %s" % res.get("EVENTS_WITH_CLOSED_TRUE"),
             "OPEN_MARKET_ROWS = %s" % res.get("OPEN_MARKET_ROWS"),
             "RESOLVED_MARKET_ROWS = %s" % res.get("RESOLVED_MARKET_ROWS"),
             "MARKETS_WITH_BID_AND_ASK = %s" % res.get("MARKETS_WITH_BID_AND_ASK"),
             "DISCOVERY_LIST_EXHAUSTED = %s" % res.get("DISCOVERY_LIST_EXHAUSTED"),
             "PAGINATION_ADVANCES = %s" % res.get("PAGINATION_ADVANCES"),
             "OVERLAP_CHECK = %s" % res.get("OVERLAP_CHECK"),
             "VENUE_REQUESTS = %d" % budget.spent,
             "PLANNED_MIN_GAP_S = %s" % res.get("planned_min_gap_s"),
             "HTTP_429 = %s" % res.get("http_429"),
             "HORIZONS_DERIVABLE = %s" % json.dumps(DERIVABLE_LONG_HORIZONS),
             "HORIZONS_UNREACHABLE = %s (NOT_OBSERVED_WITHIN_BLOCK_LENGTH)"
             % list(UNREACHABLE_HORIZONS),
             "TOUCH_IS_NOT_FILL. MAKER_FILL_PROBABILITY = NOT_IDENTIFIED.",
             "PAIR_COMPLETION_PROBABILITY = NOT_IDENTIFIED.",
             "NOT POOLED WITH TRACK A. mirror_live = false."]
    for l in lines:
        say(l)
    B.seal(out, lines)
    ok, bad = B.verify(out)
    say()
    say("SEAL_VERIFIED = %s%s" % (ok, "" if ok else " BAD=%s" % bad))
    if not ok:
        return 3
    # A block that collected no scientific data must NOT read as success, must
    # not advance block numbering as though it had, and must fail the workflow.
    if res["BLOCK_STATUS"] != "OK":
        say()
        say("EXIT NON-ZERO: BLOCK_STATUS = %s" % res["BLOCK_STATUS"])
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
