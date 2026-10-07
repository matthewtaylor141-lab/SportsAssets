"""BOUNDED, CACHED PMX REFERENCE DATA FOR THE MARKET PLANE (completion readiness).

Venue guidance (2026-10-07): ListInstruments returns full instrument
definitions, pages to 1,000 and is capped at 6 calls per minute (a full
~74k pull is ~74 pages, ~12+ minutes). Pull the full universe ONCE, cache
it, and do not repoll it; follow changes incrementally.

This module is the PLANNER only -- no I/O. Every call slot (one per
60/calls_per_min seconds, the whole budget, sequential) goes to exactly one
action, in this order, so reference data never competes with what capital
needs:

  1. PRIORITY  held / candidate / imminent contracts without refdata: ONE
               batched read naming up to 1,000 symbols (refdata by symbols
               answers only the ones that exist; a 200 that omits a symbol
               is the venue saying it does not list it).
  2. PAGE      the next page of a full pull in progress (pageToken).
  3. START     a full pull, only when no COMPLETE pull is younger than
               FULL_REFRESH_S -- the durable receipt (market_plane_events
               REFDATA_FULL_PULL) survives restarts, so a redeploy does not
               repoll the universe.
  4. NEW       contracts the registry gained since the last complete pull
               (new listings) and unlisted retries: batched by symbols. A
               new listing enters WITHOUT another full sweep.

A pull is COMPLETE only when the venue's own pagination ends (eof, or no
next token). Running out of MAX_PAGES or MAX_PULL_S, or a page that cannot
be read after MAX_PAGE_FAILURES tries, ends it TRUNCATED -- never COMPLETE --
and a truncated pull proves nothing about what it did not read (no contract
is marked unlisted from it).

INSTRUMENT STATE CHANGES: `CreateInstrumentStateChangeSubscription` is not
in the venue's published proto bundle (refdata.proto sha256 6c1f131b...,
fetched 2026-10-07 from the official link: RefDataAPI has ListInstruments,
ListSymbols and GetInstrumentMetadata only). Wire numbers are never guessed
(vendor/pmx_proto/README.md), so it is named VENUE_SCHEMA_NOT_PUBLISHED; the
subscribe-all market-data stream carries every instrument's state on each
update, and new listings enter through step 4.
"""
from __future__ import annotations

import time

VERSION = "PMX_REFDATA_UNIVERSE_V1"
CALLS_PER_MIN_DEFAULT = 5          # of the venue's 6/min, headroom for the
CALLS_PER_MIN_MAX = 6              # workers' own focus-set refdata reads
FULL_REFRESH_S = 24 * 3600.0
MAX_PAGES = 120                    # 120k instruments; ~74 expected
MAX_PULL_S = 45 * 60.0
MAX_PAGE_FAILURES = 3
BATCH_MAX = 1000
BACKOFF_429_S = 60.0
STATE_CHANGE_STREAM = {
    "status": "VENUE_SCHEMA_NOT_PUBLISHED",
    "rpc": "CreateInstrumentStateChangeSubscription",
    "why": ("not in the venue's published proto bundle (refdata.proto "
            "sha256 6c1f131bb25b11fb61a4e760bb7cebc76fd3dd4b29be1e04cfb3b80"
            "2e3095351); wire numbers are never guessed"),
    "covered_by": ("subscribe-all stream state on every update + batched "
                   "by-symbol refdata for registry contracts added since the "
                   "last complete pull"),
    "owner_action": ("obtain the proto for "
                     "CreateInstrumentStateChangeSubscription from the "
                     "Polymarket representative")}

A_PRIORITY, A_PAGE, A_NEW = "PRIORITY_SYMBOLS", "FULL_PULL_PAGE", "NEW_SYMBOLS"
COMPLETE, TRUNCATED, RUNNING = "COMPLETE", "TRUNCATED", "RUNNING"


def calls_per_min(env) -> int:
    try:
        v = int(str((env or {}).get("UMP_REFDATA_CALLS_PER_MIN",
                                    CALLS_PER_MIN_DEFAULT)))
    except (TypeError, ValueError):
        v = CALLS_PER_MIN_DEFAULT
    return max(1, min(CALLS_PER_MIN_MAX, v))


def parse_page(result: dict) -> dict:
    """One `instruments` response -> {ok, status, records{symbol: rec},
    next_token, eof, http_429}. Never raises."""
    r = dict(result or {})
    status = r.get("status")
    body = r.get("body") if isinstance(r.get("body"), dict) else None
    rows = (body or {}).get("instruments")
    ok = status == 200 and isinstance(rows, list)
    recs = {}
    if ok:
        for x in rows:
            if isinstance(x, dict) and x.get("symbol"):
                recs[str(x["symbol"])] = x
    tok = None
    if body:
        tok = (body.get("nextPageToken") or body.get("next_page_token")
               or None)
    return {"ok": ok, "status": status, "records": recs,
            "next_token": str(tok) if tok else None,
            "eof": bool((body or {}).get("eof")) or (ok and not tok),
            "http_429": status == 429,
            "transport_error": r.get("transportError"), "ms": r.get("ms")}


class Planner:
    def __init__(self, *, calls_per_minute: int = CALLS_PER_MIN_DEFAULT,
                 last_complete_at: float | None = None,
                 max_pages: int = MAX_PAGES, max_pull_s: float = MAX_PULL_S,
                 full_refresh_s: float = FULL_REFRESH_S):
        self.interval_s = 60.0 / max(1, min(CALLS_PER_MIN_MAX,
                                            int(calls_per_minute)))
        self.next_call_at = 0.0
        self.last_complete_at = last_complete_at
        self.max_pages, self.max_pull_s = int(max_pages), float(max_pull_s)
        self.full_refresh_s = float(full_refresh_s)
        self.pull = None
        self.last_receipt = None
        self.totals = {"calls": 0, "ok": 0, "failed": 0, "http_429": 0,
                       "priority_calls": 0, "page_calls": 0, "new_calls": 0,
                       "records": 0}

    # -- scheduling -------------------------------------------------------

    def full_due(self, now: float) -> bool:
        return (self.last_complete_at is None
                or now - self.last_complete_at >= self.full_refresh_s)

    def next_action(self, *, now: float, priority_pending=(),
                    other_pending=()) -> dict | None:
        if now < self.next_call_at:
            return None
        pri = [s for s in priority_pending if s][:BATCH_MAX]
        if pri:
            return {"kind": A_PRIORITY, "symbols": pri}
        if self.pull is not None:
            return {"kind": A_PAGE, "token": self.pull["token"],
                    "page": self.pull["pages"] + 1}
        if self.full_due(now):
            self.pull = {"id": "pull:%d" % int(now), "started_at": now,
                         "pages": 0, "token": None, "instruments": 0,
                         "seen": set(), "failures": 0, "status": RUNNING,
                         "pending_at_start": None}
            return {"kind": A_PAGE, "token": None, "page": 1, "start": True}
        oth = [s for s in other_pending if s][:BATCH_MAX]
        if oth:
            return {"kind": A_NEW, "symbols": oth}
        return None

    def body_for(self, action: dict) -> dict:
        from .. import pmx_institutional as PMX
        if action["kind"] == A_PAGE:
            return PMX.instruments_body(page_token=action.get("token"))
        return PMX.instruments_body(symbols=action["symbols"])

    # -- results ----------------------------------------------------------

    def record(self, action: dict, result: dict, *, now: float) -> dict:
        """Account one call. Returns {parsed, unlisted, finished} where
        `unlisted` are symbols a successful by-symbol read proved absent and
        `finished` is the pull receipt when this call ended a pull."""
        p = parse_page(result)
        t = self.totals
        t["calls"] += 1
        t[{A_PRIORITY: "priority_calls", A_PAGE: "page_calls",
           A_NEW: "new_calls"}[action["kind"]]] += 1
        self.next_call_at = now + self.interval_s
        if p["http_429"]:
            t["http_429"] += 1
            self.next_call_at = now + max(self.interval_s, BACKOFF_429_S)
        if p["ok"]:
            t["ok"] += 1
            t["records"] += len(p["records"])
        else:
            t["failed"] += 1
        out = {"parsed": p, "unlisted": [], "finished": None}
        if action["kind"] in (A_PRIORITY, A_NEW):
            if p["ok"]:
                out["unlisted"] = [s for s in action["symbols"]
                                   if s not in p["records"]]
            return out
        pull = self.pull
        if pull is None:
            return out
        if p["ok"]:
            pull["pages"] += 1
            pull["instruments"] += len(p["records"])
            pull["seen"].update(p["records"])
            pull["token"] = p["next_token"]
            pull["failures"] = 0
            if p["eof"]:
                out["finished"] = self._finish(COMPLETE, now, "VENUE_EOF")
                return out
        elif not p["http_429"]:
            pull["failures"] += 1
            if pull["failures"] >= MAX_PAGE_FAILURES:
                out["finished"] = self._finish(
                    TRUNCATED, now, "PAGE_UNREADABLE:%s" % (
                        p["status"] or p["transport_error"]))
                return out
        if pull["pages"] >= self.max_pages:
            out["finished"] = self._finish(TRUNCATED, now, "MAX_PAGES")
        elif now - pull["started_at"] >= self.max_pull_s:
            out["finished"] = self._finish(TRUNCATED, now, "MAX_PULL_S")
        return out

    def _finish(self, status: str, now: float, why: str) -> dict:
        pull, self.pull = self.pull, None
        rec = {"id": pull["id"], "status": status, "why": why,
               "started_at": pull["started_at"], "finished_at": now,
               "seconds": round(now - pull["started_at"], 1),
               "pages": pull["pages"], "instruments": pull["instruments"],
               "distinct_symbols": len(pull["seen"]),
               "max_pages": self.max_pages, "max_pull_s": self.max_pull_s,
               "version": VERSION}
        if status == COMPLETE:
            self.last_complete_at = now
        self.last_receipt = rec
        # the seen set and the pending-at-start set go with the receipt to
        # the caller (unlisted proof needs both), never into it
        rec_full = dict(rec, _seen=pull["seen"],
                        _pending_at_start=pull.get("pending_at_start"))
        return rec_full

    def digest(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        pull = self.pull
        return {"version": VERSION, "interval_s": round(self.interval_s, 2),
                "calls_per_min": round(60.0 / self.interval_s, 2),
                "next_call_in_s": round(max(0.0, self.next_call_at - now), 1),
                "last_complete_at": self.last_complete_at,
                "full_due": self.full_due(now),
                "pull": None if pull is None else {
                    "id": pull["id"], "status": RUNNING,
                    "pages": pull["pages"], "instruments": pull["instruments"],
                    "elapsed_s": round(now - pull["started_at"], 1),
                    "max_pages": self.max_pages,
                    "max_pull_s": self.max_pull_s},
                "last_receipt": self.last_receipt,
                "totals": dict(self.totals),
                "state_change_stream": STATE_CHANGE_STREAM}
