"""THE SHARED PINNAPI CURRENT-STATE CACHE (pure: no socket, no database).

One process owns the provider's single WebSocket (see pinnapi_owner); this
module is the state it feeds and the ONLY accessor Derek and Xavier read.

AUTHORITY. Every connection is an EPOCH. Quotes are stored with the epoch that
delivered them. `revoke()` is called synchronously the moment ownership is in
doubt (lease connection lost, lease no longer held, socket closed, gap); from
then on every read answers None with a named reason until a NEW epoch has
been granted AND has resynchronized (the provider's snapshot for each
subscribed (stream, sport) has arrived on that epoch). Quotes from an older
epoch are never served again.

TIME. Four clocks are kept apart and never substituted for one another:
  source_change_ms  the provider frame stamp (`ts`) of the frame in which THIS
                    market's price last CHANGED. A snapshot, a re-sent
                    unchanged price, a heartbeat or a reconnect never sets it.
                    A market first seen in a snapshot has NO change time
                    (None) until a delta changes it: its age is unknown and it
                    is never fresh.
  frame_ts_ms       the provider stamp of the latest frame that carried the
                    market (snapshot or delta) -- provenance only.
  received_ms       our wall clock when that frame arrived -- provenance only.
  evaluated_ms      supplied by the consumer at read time; the quote age is
                    evaluated_ms - source_change_ms (negative => refused).

BOUNDS. The cache holds at most MAX_EVENTS events and MAX_MARKETS markets;
events the provider deletes or closes are dropped at once; the oldest-touched
events are evicted beyond the cap (and counted). Latency samples live in
fixed-size rings. Nothing here persists raw frames.

PARSING. The provider forwards Pinnacle's own records. Their inner schema is
isolated in `extract_markets` (versioned PARSER_VERSION); a record that does
not validate is counted as UNPARSED and contributes nothing.
"""
from __future__ import annotations

import collections
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

PARSER_VERSION = "ARCADIA_RAW_V1_OBSERVED_2026_10_01"
MAX_EVENTS = 4000
MAX_MARKETS = 120_000
RING = 4096

# read refusals (named, never a silent None)
R_NO_AUTHORITY = "FEED_OWNERSHIP_NOT_HELD"
R_NOT_SYNCED = "FEED_EPOCH_NOT_RESYNCHRONIZED"
R_UNKNOWN_MARKET = "FEED_MARKET_NOT_IN_CURRENT_STATE"
R_OLD_EPOCH = "FEED_QUOTE_FROM_A_PREVIOUS_CONNECTION"
R_NO_CHANGE_TIME = "FEED_QUOTE_AGE_UNKNOWN_NO_OBSERVED_CHANGE"
R_FUTURE = "FEED_QUOTE_CHANGE_TIME_IN_THE_FUTURE"
R_STALE = "FEED_QUOTE_OLDER_THAN_LIMIT"
R_CLOSED = "FEED_MARKET_CLOSED"


def _now_ms() -> float:
    return time.time() * 1000.0


@dataclass
class Quote:
    key: str
    event_id: int
    sport_id: Optional[int]
    stream: str                     # live | prematch
    period: Optional[int]
    market_type: Optional[str]
    side: Optional[str]
    line: Optional[float]
    prices: dict                    # designation -> provider price (raw)
    epoch: int
    source_change_ms: Optional[float]
    frame_ts_ms: Optional[float]
    received_ms: float
    open: bool = True
    alternate: Optional[bool] = None
    market_version: Optional[float] = None

    def decimal_prices(self) -> dict:
        return {d: american_to_decimal(p) for d, p in self.prices.items()}


@dataclass
class Ring:
    size: int = RING
    xs: collections.deque = field(default_factory=lambda: collections.deque(
        maxlen=RING))

    def add(self, v):
        if v is not None and math.isfinite(v):
            self.xs.append(float(v))

    def summary(self) -> dict:
        s = sorted(self.xs)
        if not s:
            return {"n": 0}

        def q(p):
            return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 1)
        return {"n": len(s), "p50": q(.5), "p95": q(.95), "p99": q(.99),
                "max": round(s[-1], 1)}


class FeedAuthority:
    """Which connection, if any, may publish usable prices."""

    def __init__(self):
        self.epoch = 0
        self.granted = False
        self.reason = R_NO_AUTHORITY
        self.revocations = 0
        self.expected_snapshots: set = set()
        self.seen_snapshots: set = set()

    def grant(self, subscriptions) -> int:
        """A NEW epoch for a freshly opened socket under a held lease.
        Usable only after resync (all expected snapshots seen)."""
        self.epoch += 1
        self.granted = True
        self.reason = R_NOT_SYNCED
        self.expected_snapshots = set(subscriptions)
        self.seen_snapshots = set()
        return self.epoch

    def revoke(self, reason: str = R_NO_AUTHORITY) -> None:
        if self.granted:
            self.revocations += 1
        self.granted = False
        self.reason = reason

    def snapshot_seen(self, epoch: int, stream: str, sport_id) -> None:
        if self.granted and epoch == self.epoch:
            self.seen_snapshots.add((stream, sport_id))

    @property
    def synced(self) -> bool:
        return self.granted and self.expected_snapshots <= self.seen_snapshots

    def state(self) -> dict:
        return {"epoch": self.epoch, "granted": self.granted,
                "synced": self.synced,
                "reason": None if self.synced else self.reason,
                "awaiting_snapshots": sorted(
                    "%s/%s" % x for x in
                    self.expected_snapshots - self.seen_snapshots),
                "revocations": self.revocations}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def american_to_decimal(a) -> Optional[float]:
    """Pinnacle's raw frames carry AMERICAN odds (observed 2026-10-01:
    -164 / 127). Decimal = 1 + a/100 for a >= 100, 1 + 100/|a| for
    a <= -100; anything in (-100, 100) is not an American price."""
    v = _num(a)
    if v is None or -100 < v < 100:
        return None
    return round(1.0 + (v / 100.0 if v > 0 else 100.0 / abs(v)), 6)


def participants(rec: dict) -> dict:
    """{'home': name, 'away': name} from `participants[].alignment`."""
    out = {}
    for p in rec.get("participants") or []:
        if isinstance(p, dict) and p.get("alignment") in ("home", "away") \
                and p.get("name"):
            out[p["alignment"]] = str(p["name"])
    return out


def extract_markets(rec: dict) -> Optional[list]:
    """Pinnacle record -> [(key, fields)], or None when the record does not
    validate. SCHEMA AS OBSERVED in the bounded ws_sample of 2026-10-01
    (PARSER_VERSION): `markets` is a list of dicts with `key` ("s;0;m",
    "s;0;s;0.25", ...), `type` (moneyline|spread|total|team_total),
    `period`, `status`, `isAlternate`, `version` (epoch seconds of the
    market record -- provenance only, never a price-change time) and
    `prices` = [{designation, price (AMERICAN), points?}]."""
    ms = rec.get("markets")
    if ms is None:
        return []
    if not isinstance(ms, list):
        return None
    out = []
    for m in ms:
        if not isinstance(m, dict) or not m.get("type"):
            return None
        prices = m.get("prices")
        pr, line = {}, None
        if isinstance(prices, list):
            for p in prices:
                if not isinstance(p, dict) or "designation" not in p:
                    return None
                pr[str(p["designation"])] = _num(p.get("price"))
                if line is None and p.get("points") is not None:
                    line = _num(p.get("points"))
        elif prices is not None:
            return None
        period = m.get("period")
        side = m.get("side")
        key = m.get("key") or "%s|%s|%s|%s" % (m.get("type"), period or 0,
                                               side or "", "" if line is None
                                               else line)
        out.append((str(key), {
            "market_type": str(m.get("type")), "period": period,
            "side": side, "line": line, "prices": pr,
            "open": (m.get("status") in (None, "open")),
            "alternate": m.get("isAlternate"),
            "market_version": _num(m.get("version"))}))
    return out


def closed_periods(rec: dict) -> set:
    """Periods the record marks anything but open (closed / settled): every
    market of such a period is closed, listed or not."""
    ps = rec.get("periods")
    if not isinstance(ps, list):
        return set()
    return {p.get("period") for p in ps if isinstance(p, dict)
            and p.get("status") not in (None, "open")}


class FeedCache:
    def __init__(self, *, authority: Optional[FeedAuthority] = None,
                 extract: Callable = extract_markets,
                 max_events: int = MAX_EVENTS,
                 max_markets: int = MAX_MARKETS):
        self.authority = authority or FeedAuthority()
        self.extract = extract
        self.max_events, self.max_markets = max_events, max_markets
        self.events: "collections.OrderedDict[int, dict]" = \
            collections.OrderedDict()
        self.quotes: dict = {}          # (event_id, key) -> Quote
        self.counts = collections.Counter()
        self.provider_to_receipt = Ring()
        self.receipt_to_eval = Ring()
        self.last_frame_received_ms: Optional[float] = None
        self.last_change_received_ms: Optional[float] = None

    # ── lifecycle ────────────────────────────────────────────────────
    def new_connection(self, subscriptions) -> int:
        """A new socket epoch. Everything from earlier epochs is dropped: a
        reconnect never leaves an old price usable."""
        self.events.clear()
        self.quotes.clear()
        self.counts["epochs"] += 1
        return self.authority.grant(subscriptions)

    def lost(self, reason: str) -> None:
        self.authority.revoke(reason)

    # ── ingestion ────────────────────────────────────────────────────
    def apply(self, msg: dict, *, epoch: int,
              received_ms: Optional[float] = None) -> str:
        """Apply one envelope from connection `epoch`. Returns what it was."""
        rx = received_ms if received_ms is not None else _now_ms()
        if epoch != self.authority.epoch or not self.authority.granted:
            self.counts["frames_from_revoked_epoch"] += 1
            return "IGNORED_REVOKED_EPOCH"
        t = msg.get("type") if isinstance(msg, dict) else None
        self.counts["frames"] += 1
        self.last_frame_received_ms = rx
        ts = _num(msg.get("ts")) if isinstance(msg, dict) else None
        if t in ("ping", "pong", "subscribed", "unsubscribed"):
            return t                      # heartbeats never touch a quote
        if t == "error":
            self.counts["provider_errors"] += 1
            return "error"
        if t == "snapshot":
            stream, sport = msg.get("stream"), msg.get("sport_id")
            for ev in msg.get("events") or []:
                if isinstance(ev, dict) and ev.get("id") is not None:
                    self._replace_event(ev, stream=stream, sport=sport,
                                        epoch=epoch, frame_ts=ts, rx=rx,
                                        as_change=False)
            if msg.get("sport_id") is not None:
                self.authority.snapshot_seen(epoch, stream, sport)
            return "snapshot"
        if t == "live":
            if ts is not None:
                self.provider_to_receipt.add(rx - ts)
            rec = msg.get("rec") or {}
            op = msg.get("op")
            eid = rec.get("id")
            if eid is None:
                self.counts["unparsed"] += 1
                return "UNPARSED"
            if op == "del":
                self._drop_event(eid)
                return "del"
            self._merge_event(rec, stream="live", sport=msg.get("sport_id"),
                              epoch=epoch, frame_ts=ts, rx=rx)
            return "live:%s" % op
        if t == "prematch_markets":
            eid = msg.get("matchup_id")
            data = msg.get("data")
            if eid is None or not isinstance(data, list):
                self.counts["unparsed"] += 1
                return "UNPARSED"
            # authoritative: replace this event's markets; a price that
            # differs from the one we held is a CHANGE at ts, an unchanged
            # one keeps its earlier change time (or none)
            self._replace_event({"id": eid, "markets": data},
                                stream="prematch", sport=msg.get("sport_id"),
                                epoch=epoch, frame_ts=ts, rx=rx,
                                as_change=True, keep_meta=True)
            return "prematch_markets"
        if t == "prematch_matchups":
            for ev in msg.get("data") or []:
                if isinstance(ev, dict) and ev.get("id") is not None:
                    self._touch_meta(ev, stream="prematch",
                                     sport=msg.get("sport_id"))
            return "prematch_matchups"
        self.counts["unknown_type"] += 1
        return "UNKNOWN"

    def _touch_meta(self, ev, *, stream, sport):
        eid = ev["id"]
        cur = self.events.get(eid) or {"id": eid, "stream": stream,
                                       "sport_id": sport}
        for k in ("participants", "league", "startTime", "status", "isLive",
                  "parentId", "periods", "version", "type"):
            if k in ev:
                cur[k] = ev[k]
        self.events[eid] = cur
        self.events.move_to_end(eid)
        self._bound()

    def _drop_event(self, eid):
        self.events.pop(eid, None)
        for k in [k for k in self.quotes if k[0] == eid]:
            del self.quotes[k]
        self.counts["events_deleted"] += 1

    def _replace_event(self, ev, *, stream, sport, epoch, frame_ts, rx,
                       as_change, keep_meta=False):
        eid = ev["id"]
        parsed = self.extract(ev)
        if parsed is None:
            self.counts["unparsed"] += 1
            return
        if not keep_meta:
            self._touch_meta(ev, stream=stream, sport=sport)
        else:
            self.events.setdefault(eid, {"id": eid, "stream": stream,
                                         "sport_id": sport})
            self.events.move_to_end(eid)
        old = {k: q for k, q in self.quotes.items() if k[0] == eid}
        for k in old:
            del self.quotes[k]
        closed = closed_periods(ev)
        for key, f in parsed:
            if not f["open"] or (f["period"] or 0) in closed:
                continue
            prev = old.get((eid, key))
            changed = prev is None or prev.prices != f["prices"]
            if as_change and changed and prev is not None:
                change = frame_ts
            elif as_change and prev is None:
                change = None        # first sight: age unknown
            else:
                change = prev.source_change_ms if (
                    prev and not changed) else None
            self._put(eid, key, f, stream, sport, epoch, change, frame_ts,
                      rx)
        self._bound()

    def _merge_event(self, rec, *, stream, sport, epoch, frame_ts, rx):
        eid = rec["id"]
        self._touch_meta(rec, stream=stream, sport=sport)
        closed = closed_periods(rec)
        for k in [k for k, q in self.quotes.items()
                  if k[0] == eid and (q.period or 0) in closed]:
            del self.quotes[k]
        parsed = self.extract(rec)
        if parsed is None:
            self.counts["unparsed"] += 1
            return
        for key, f in parsed:
            k = (eid, key)
            if not f["open"] or (f["period"] or 0) in closed:
                self.quotes.pop(k, None)
                self.counts["markets_closed"] += 1
                continue
            prev = self.quotes.get(k)
            changed = prev is None or prev.prices != f["prices"]
            change = frame_ts if changed else prev.source_change_ms
            if changed:
                self.counts["price_changes"] += 1
                self.last_change_received_ms = rx
            self._put(eid, key, f, stream, sport, epoch, change, frame_ts, rx)
        self._bound()

    def _put(self, eid, key, f, stream, sport, epoch, change, frame_ts, rx):
        self.quotes[(eid, key)] = Quote(
            key=key, event_id=eid, sport_id=sport, stream=stream,
            period=f["period"], market_type=f["market_type"],
            side=f["side"], line=f["line"], prices=dict(f["prices"]),
            epoch=epoch, source_change_ms=change, frame_ts_ms=frame_ts,
            received_ms=rx, open=True, alternate=f["alternate"],
            market_version=f.get("market_version"))

    def _bound(self):
        while len(self.events) > self.max_events:
            eid, _ = self.events.popitem(last=False)
            for k in [k for k in self.quotes if k[0] == eid]:
                del self.quotes[k]
            self.counts["events_evicted"] += 1
        if len(self.quotes) > self.max_markets:
            # evict whole least-recently-touched events until within bound
            for eid in list(self.events):
                if len(self.quotes) <= self.max_markets:
                    break
                self._drop_event(eid)
                self.counts["events_evicted"] += 1

    # ── the ONE read path for Derek and Xavier ──────────────────────
    def read(self, event_id, key, *, evaluated_ms: Optional[float] = None,
             max_age_s: float = 30.0) -> dict:
        ev_ms = evaluated_ms if evaluated_ms is not None else _now_ms()
        a = self.authority
        if not a.granted:
            return {"ok": False, "reason": a.reason or R_NO_AUTHORITY}
        if not a.synced:
            return {"ok": False, "reason": R_NOT_SYNCED}
        q = self.quotes.get((event_id, key))
        if q is None:
            return {"ok": False, "reason": R_UNKNOWN_MARKET}
        if q.epoch != a.epoch:
            return {"ok": False, "reason": R_OLD_EPOCH}
        if not q.open:
            return {"ok": False, "reason": R_CLOSED}
        prov = {"source_change_ms": q.source_change_ms,
                "frame_ts_ms": q.frame_ts_ms, "received_ms": q.received_ms,
                "evaluated_ms": ev_ms, "epoch": q.epoch,
                "parser": PARSER_VERSION}
        if q.source_change_ms is None:
            return {"ok": False, "reason": R_NO_CHANGE_TIME, "quote": q,
                    "provenance": prov}
        age = (ev_ms - q.source_change_ms) / 1000.0
        prov["quote_age_s"] = round(age, 3)
        self.receipt_to_eval.add(ev_ms - q.received_ms)
        if age < 0:
            return {"ok": False, "reason": R_FUTURE, "quote": q,
                    "provenance": prov}
        if age > max_age_s:
            return {"ok": False, "reason": R_STALE, "quote": q,
                    "provenance": prov}
        return {"ok": True, "quote": q, "provenance": prov}

    # ── census / health (bounded, for the heartbeat) ────────────────
    def census(self) -> dict:
        by = collections.Counter()
        unknown_age = 0
        for q in self.quotes.values():
            by["%s|%s|%s" % (q.sport_id, q.market_type, q.stream)] += 1
            unknown_age += q.source_change_ms is None
        return {"parser": PARSER_VERSION, "authority": self.authority.state(),
                "events": len(self.events), "markets": len(self.quotes),
                "markets_age_unknown": unknown_age,
                "markets_by_sport_type_phase": dict(by),
                "counts": dict(self.counts),
                "provider_stamp_to_receipt_ms":
                    self.provider_to_receipt.summary(),
                "receipt_to_evaluation_ms": self.receipt_to_eval.summary(),
                "bounds": {"max_events": self.max_events,
                           "max_markets": self.max_markets, "ring": RING}}
