"""THE TAPE: every captured BBO body, in time order, nothing deduplicated.

`bettor_economic_test.load_holdout()` deduplicates on
(slug, bid, ask, sharesTraded) and drops the timestamp. That is right
for a cross-sectional identity check and WRONG for anything that has a
beginning and an end. An episode needs a sequence: when the quote went
up, what traded while it rested, when it was cancelled, what was still
held afterwards.

WHAT THE CAPTURE ACTUALLY CARRIES, read from the bodies rather than
assumed:

    local_request_wall_utc   our clock at request start
    bestBid / bestAsk        the touch
    bidDepth / askDepth      shares at the touch
    sharesTraded             CUMULATIVE volume -- its DELTA is a print
    lastTradePx              the price of the most recent print
    state                    OPEN | HALTED | EXPIRED
    settlementPx             see the warning below
    currentPx, lastPriceSample, openInterest

THE settlementPx TRAP, and it is a real one.

`settlementPx` is present on OPEN rows and it is NOT the eventual
settlement. It is CONSTANT for the whole open life of a market and
only moves after expiry, sometimes hours after:

    aec-cfb-coast-del-2026-09-19   spx 0.37 for all 4640 OPEN rows,
                                   still 0.37 for the first 5 EXPIRED
                                   rows, then 0.0.  Market traded down
                                   to 0.03/0.05 and settled at ZERO.
    aec-nfl-chi-car-2026-09-13     spx 0.60 while open, 0.60 for the
                                   first 5 EXPIRED rows at 20:38, then
                                   1.0 from 04:06 the next morning.

Reading `settlementPx` off an OPEN row and calling it the outcome
would be a lookahead bug that also happens to be WRONG -- 0.37 against
a true 0.0. The realised outcome is the value on the LAST expired row,
and only when it has actually moved off the open-state value. Where it
has not moved, this module reports SETTLEMENT_NOT_OBSERVED rather than
guessing.

DATA STATUS. Every byte here is DEVELOPMENT data. All 27 segments were
read by the Class C test; the settlement labels are read for the first
time in this module and are development data from that moment on.
There is no untouched PMUS holdout and this file does not manufacture
one.
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import gzip
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CAPTURE = os.path.join(ROOT, "research", "evidence", "capture")

OPEN = "MARKET_STATE_OPEN"
HALTED = "MARKET_STATE_HALTED"
EXPIRED = "MARKET_STATE_EXPIRED"

SETTLEMENT_NOT_OBSERVED = "SETTLEMENT_NOT_OBSERVED"


def _amt(x):
    """PMUS money fields are {'value': '0.0050', 'currency': 'USD'}."""
    if isinstance(x, dict):
        x = x.get("value")
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _ts(s):
    try:
        return dt.datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return None


def load_tape(capture_dir: str = CAPTURE):
    """Return (by_slug, files). by_slug[slug] is time-ordered, complete."""
    by_slug = collections.defaultdict(list)
    files = sorted(glob.glob(os.path.join(capture_dir, "*",
                                          "request_log.jsonl.gz")))
    for path in files:
        seg = os.path.basename(os.path.dirname(path))
        try:
            fh = gzip.open(path, "rt")
        except OSError:
            continue
        with fh as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("http_status") != 200:
                    continue
                if not (r.get("path") or "").endswith("/bbo"):
                    continue
                body = r.get("body")
                if isinstance(body, str):
                    try:
                        body = json.loads(body)
                    except ValueError:
                        continue
                md = (body or {}).get("marketData") or {}
                slug = md.get("marketSlug")
                if not slug:
                    continue
                by_slug[slug].append({
                    "slug": slug,
                    "segment": seg,
                    "t_iso": r.get("local_request_wall_utc"),
                    "t": _ts(r.get("local_request_wall_utc")),
                    "bid": _amt(md.get("bestBid")),
                    "ask": _amt(md.get("bestAsk")),
                    "bid_depth": md.get("bidDepth"),
                    "ask_depth": md.get("askDepth"),
                    "shares_traded": _amt(md.get("sharesTraded")),
                    "last_trade_px": _amt(md.get("lastTradePx")),
                    "current_px": _amt(md.get("currentPx")),
                    "open_interest": _amt(md.get("openInterest")),
                    "settlement_px_field": _amt(md.get("settlementPx")),
                    "state": md.get("state"),
                })
    for slug in by_slug:
        by_slug[slug].sort(key=lambda r: (r["t"] if r["t"] is not None else 0))
    return dict(by_slug), files


def settlement_label(rows):
    """The REALISED settlement, or SETTLEMENT_NOT_OBSERVED.

    Rule, stated so it can be argued with:

      * the market must reach EXPIRED inside the capture;
      * the value on the LAST expired row must DIFFER from the value
        carried throughout the open life. `settlementPx` while open is
        a static reference, so an unchanged value after expiry means
        the outcome has not been published yet, not that the reference
        is the outcome.

    Returns (value_or_marker, detail_dict).
    """
    open_vals = {r["settlement_px_field"] for r in rows
                 if r["state"] == OPEN}
    expired = [r for r in rows if r["state"] == EXPIRED]
    detail = {"open_state_settlement_px": sorted(
                  v for v in open_vals if v is not None),
              "expired_rows": len(expired),
              "first_expired_at": expired[0]["t_iso"] if expired else None,
              "last_row_at": rows[-1]["t_iso"] if rows else None}
    if not expired:
        detail["reason"] = "market never reached EXPIRED inside the capture"
        return SETTLEMENT_NOT_OBSERVED, detail
    final = expired[-1]["settlement_px_field"]
    detail["final_expired_settlement_px"] = final
    if final is None:
        detail["reason"] = "expired rows carry no settlementPx"
        return SETTLEMENT_NOT_OBSERVED, detail
    if final in open_vals:
        detail["reason"] = ("settlementPx never moved off its open-state "
                            "value after expiry -- the outcome had not been "
                            "published by the end of the capture")
        return SETTLEMENT_NOT_OBSERVED, detail
    detail["moved_at"] = next(
        (r["t_iso"] for r in expired
         if r["settlement_px_field"] == final), None)
    detail["publication_lag_s"] = None
    if detail["moved_at"] and detail["first_expired_at"]:
        a, b = _ts(detail["first_expired_at"]), _ts(detail["moved_at"])
        if a is not None and b is not None:
            detail["publication_lag_s"] = round(b - a, 1)
    return final, detail


def infer_tick(bid: float, ask: float) -> float:
    """The market's tick, INFERRED from the price grid.

    `orderPriceMinTickSize` is not in the BBO body, so this is inferred
    and labelled inferred wherever it is used. A price that is not a
    whole number of cents can only sit on a finer grid; half a cent is
    the finest grid observed in this corpus.
    """
    for p in (bid, ask):
        if p is None:
            continue
        c = round(p * 100.0, 6)
        if abs(c - round(c)) > 1e-6:
            return 0.005
    return 0.01


def market_tick(rows) -> float:
    """One tick per MARKET, from every price it ever showed.

    Inferring per observation would call the same market one-cent on
    one row and half-cent on the next, purely because that row's two
    prices happened to land on whole cents.
    """
    t = 0.01
    for r in rows:
        if infer_tick(r["bid"], r["ask"]) == 0.005:
            return 0.005
    return t


def event_of(slug: str) -> str:
    """The underlying EVENT, so clustering is not done at the market.

    Four NFL markets captured on 2026-09-13 are four different games,
    but they share a league, a day and a capture cadence. Six markets
    on one game would share far more. The key here is the venue's own
    slug structure minus the outcome suffix.
    """
    parts = (slug or "").split("-")
    # aec-cfb-coast-del-2026-09-19  -> aec-cfb-coast-del-2026-09-19
    # atc-lmx-ame-tij-2026-09-05-tij -> atc-lmx-ame-tij-2026-09-05
    for i, p in enumerate(parts):
        if len(p) == 4 and p.isdigit():          # the year
            return "-".join(parts[:i + 3])
    return slug


def describe_tape():
    by_slug, files = load_tape()
    out = {"capture_dir": os.path.relpath(CAPTURE, ROOT),
           "segments": len(files),
           "markets": len(by_slug),
           "observations": sum(len(v) for v in by_slug.values()),
           "data_status": "DEVELOPMENT -- no untouched PMUS holdout exists",
           "by_market": {}}
    for slug, rows in sorted(by_slug.items()):
        lab, det = settlement_label(rows)
        two = [r for r in rows
               if r["bid"] is not None and r["ask"] is not None]
        prints, vol, prev = 0, 0.0, None
        for r in rows:
            s = r["shares_traded"]
            if s is None:
                continue
            if prev is not None and s > prev + 1e-9:
                prints += 1
                vol += s - prev
            prev = s
        out["by_market"][slug] = {
            "event": event_of(slug),
            "observations": len(rows),
            "two_sided": len(two),
            "tick_inferred": market_tick(rows),
            "print_intervals": prints,
            "shares_printed": round(vol, 1),
            "first": rows[0]["t_iso"], "last": rows[-1]["t_iso"],
            "settlement": lab, "settlement_detail": det}
    return out


if __name__ == "__main__":
    d = describe_tape()
    print(json.dumps({k: v for k, v in d.items() if k != "by_market"},
                     indent=2))
    print()
    hdr = "%-50s %-24s %6s %6s %7s %10s  %s"
    print(hdr % ("market", "event", "obs", "2side", "prints", "shares",
                 "settlement"))
    for slug, m in d["by_market"].items():
        print(hdr % (slug[:50], m["event"][-24:], m["observations"],
                     m["two_sided"], m["print_intervals"],
                     m["shares_printed"], m["settlement"]))
