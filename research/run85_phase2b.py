#!/usr/bin/env python3
"""RUN 85 PHASE 2B -- repaired calibration. READ ONLY. NO CREDENTIAL.

Repairs the three defects Phase 2A exposed, plus the sealing defect. Every
venue call still goes through run85_pmus_collector._get, whose capability
boundary is the one under test in test_run85_pmus_collector.py: GET only,
public gateway only, no credential, no signer, no order path.

--------------------------------------------------------------------------
THE SELECTION RULE, DOCUMENTED BEFORE CAPTURE (specification section 3)
--------------------------------------------------------------------------
A market enters the cohort only if ALL of these hold:

  1. EVENT IDENTITY IS NATIVE. It comes from /v1/events, which supplies an
     event id AND slug. /v1/markets returns eventSlug = None on every row --
     measured, not assumed -- which is why Phase 2A's per-row slug fallback
     turned twelve markets of two championship families into twelve "events".
     Fallback grouping is FORBIDDEN here: no event identity, no cohort entry.

  2. SPORTS EVIDENCE IS STRUCTURED. A field read as a field: the event's own
     series/tags, or the market's sportsMarketType / gameId / gameStartTime /
     team.league. No substring search over any text blob, ever -- Run 83.6E.1
     matched "epl" inside an Oprah description and that must not recur.

  3. THE CONTRACT IS A VERIFIED BINARY. The venue's own marketSides shows
     exactly two sides, exactly one long and one short.

Among the survivors the cohort is chosen for DISTINCT ECONOMIC REGIMES, not
for slug variety, one market per event, in this fixed order:

     NEAREST_TERM    soonest startTime
     LONGEST_TERM    latest startTime
     MOST_LIQUID     highest liquidity
     THINNEST        lowest NON-ZERO liquidity

Ties and unavailable fields drop the slot rather than substituting a
lookalike; a slot that cannot be filled is reported empty.

--------------------------------------------------------------------------
ONE REQUEST CARRIES BOTH SIDES (specification section 7)
--------------------------------------------------------------------------
WIRE EVIDENCE, from the Phase 2A archive
(sha256 30cadd87a76c15b90e402905a7bc86cf08777d304033b4886fd8dd624b30c3aa):
a single GET /v1/markets/{slug}/book returns marketData.bids AND
marketData.offers together, under one marketSlug, with one transactTime. For a
binary contract quoted in long-price terms the short side is the long side
inverted, so both legs of the complement are in that one response.

Consequence, and the reason this matters: there is NO cross-leg REST timestamp
skew, one local receive timestamp covers both legs, and the venue's own
transactTime context is identical for them. Run 84 established that leg skew is
what manufactures fake pair edge. Requesting the two sides separately would
reintroduce exactly that, so it is not done.

--------------------------------------------------------------------------
CADENCE (sections 4-6)
--------------------------------------------------------------------------
Phase 2A proved 2.0 rps is not safe: 73% of its requests were 429. The ceiling
here is 0.5 rps, Retry-After is honoured exactly, a 429 doubles the spacing,
and recovery decays 10% per success rather than snapping back.

The run is two segments because one cohort size cannot answer both questions:

     FAST    1 market   -> revisit ~= 2 s, answers the 2 s question
     COHORT  3 markets  -> revisit ~= 6 s, answers 5 s and 10 s and carries
                           the regime diversity

Nominal figures are stated here; only ACHIEVED distributions are reported.

Usage: python3 run85_phase2b.py --out DIR [--fast-seconds 300]
                                [--cohort-seconds 600] [--cohort 3]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import statistics
import sys
import time
from decimal import Decimal
from pathlib import Path

import httpx

_spec = importlib.util.spec_from_file_location(
    "run85c", Path(__file__).with_name("run85_pmus_collector.py"))
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)

PHASE = "run85/phase2b/1"

BASE_SPACING_S = 2.0        # 0.5 rps aggregate ceiling
MAX_SPACING_S = 30.0
BACKOFF_MULT = 2.0
RECOVER_DECAY = 0.90        # per success; never an instant return to full rate

EVENTS_PATH = "/v1/events"
MARKET_PATH = "/v1/market/slug/%s"


# ----------------------------------------------------------- wire schema
def market_data(body):
    """The venue's real envelope, not the SDK's TypedDict.

    Measured on the wire: the book arrives as {"marketData": {...}}. The SDK's
    MarketBook declares marketSlug/bids/offers/state at the top level and knows
    nothing of the wrapper. Both shapes are accepted so a venue change degrades
    loudly rather than silently returning nothing.
    """
    if not isinstance(body, dict):
        return {}
    inner = body.get("marketData")
    return inner if isinstance(inner, dict) else body


def amount(v):
    """px is {"value": "0.0010", "currency": "USD"} on the wire, not a scalar.

    Returns (Decimal|None, currency|None). The currency is carried rather than
    discarded: a book that ever quotes something other than USD must not be
    silently summed with one that does.
    """
    if v is None:
        return None, None
    if isinstance(v, dict):
        raw, cur = v.get("value"), v.get("currency")
    else:
        raw, cur = v, None
    if raw is None:
        return None, cur
    try:
        return Decimal(str(raw)), cur
    except Exception:                                  # noqa: BLE001
        return None, cur


def ladder(levels):
    """[(price, qty, currency)] from a wire ladder, bad rows dropped loudly."""
    out = []
    for x in levels or []:
        if not isinstance(x, dict):
            continue
        px, cur = amount(x.get("px"))
        try:
            qty = Decimal(str(x.get("qty")))
        except Exception:                              # noqa: BLE001
            qty = None
        if px is not None and qty is not None:
            out.append((px, qty, cur))
    return out


def book_view(body):
    """Everything sections 9-11 need, from the observed schema only."""
    d = market_data(body)
    bids = ladder(d.get("bids"))
    offers = ladder(d.get("offers"))
    bb = max((p for p, _, _ in bids), default=None)
    ba = min((p for p, _, _ in offers), default=None)
    bid_touch = sum(q for p, q, _ in bids if p == bb) if bb is not None else Decimal(0)
    ask_touch = sum(q for p, q, _ in offers if p == ba) if ba is not None else Decimal(0)
    return {
        "market_slug": d.get("marketSlug"),
        "state": d.get("state"),
        "transact_time": d.get("transactTime"),
        "best_bid": bb, "best_ask": ba,
        "mid": ((bb + ba) / 2) if (bb is not None and ba is not None) else None,
        "spread": (ba - bb) if (bb is not None and ba is not None) else None,
        "bid_levels": len(bids), "ask_levels": len(offers),
        "bid_depth_usd": sum(p * q for p, q, _ in bids),
        "ask_depth_usd": sum(p * q for p, q, _ in offers),
        "bid_qty_at_touch": bid_touch, "ask_qty_at_touch": ask_touch,
        "currencies": sorted({c for _, _, c in bids + offers if c}),
        "stats_keys": sorted((d.get("stats") or {}).keys()),
    }


# ------------------------------------------------------------- throttling
class AdaptivePacer:
    """0.5 rps ceiling, exact Retry-After, backoff that does not snap back."""

    def __init__(self, base=BASE_SPACING_S):
        self.base = base
        self.spacing = base
        self._last = None
        self.events = []

    def wait(self):
        if self._last is not None:
            slack = (self._last + self.spacing) - time.monotonic()
            if slack > 0:
                time.sleep(slack)
        self._last = time.monotonic()

    def on_429(self, retry_after):
        """Honour Retry-After exactly, THEN widen the spacing."""
        wait_s = None
        if retry_after is not None:
            try:
                wait_s = max(0.0, float(retry_after))
            except (TypeError, ValueError):
                wait_s = None
        before = self.spacing
        self.spacing = min(MAX_SPACING_S, self.spacing * BACKOFF_MULT)
        self.events.append({"event": "429", "retry_after": retry_after,
                            "slept_s": wait_s, "spacing_before": before,
                            "spacing_after": self.spacing})
        if wait_s:
            time.sleep(wait_s)
        self._last = time.monotonic()

    def on_success(self):
        if self.spacing > self.base:
            self.spacing = max(self.base, self.spacing * RECOVER_DECAY)


def get_paced(http, path, pacer, params=None, attempts=4):
    """One paced GET. Every attempt is recorded, including the refused ones."""
    rows = []
    for _ in range(attempts):
        pacer.wait()
        r = C._get(http, path, params)
        rows.append(r)
        if r.get("http_status") == 429:
            pacer.on_429((r.get("response_headers") or {}).get("retry-after"))
            continue
        if r.get("http_status") == 200:
            pacer.on_success()
        return r, rows
    return rows[-1], rows


# ------------------------------------------------------------- sealing
def seal(outdir: Path, report_lines):
    """Sections 1: finish the report, CLOSE it, then hash, then checksums.

    Phase 2A hashed the terminal report while `tee` still held it open, so the
    recorded digest was the empty string and the archive shipped a check that
    could never pass. The writer is owned here and closed before anything is
    hashed; nothing may write into outdir after this returns.
    """
    rp = outdir / "run85_terminal_report.txt"
    with rp.open("w") as fh:
        fh.write("\n".join(report_lines) + "\n")
        fh.flush()
    lines = []
    for p in sorted(outdir.iterdir()):
        if p.is_file() and p.name != "checksums.sha256":
            lines.append("%s  %s" % (
                hashlib.sha256(p.read_bytes()).hexdigest(), p.name))
    (outdir / "checksums.sha256").write_text("\n".join(lines) + "\n")
    return lines


def verify(outdir: Path):
    """Re-derive every digest. Returns (ok, failures)."""
    text = (outdir / "checksums.sha256").read_text()
    bad = []
    for line in text.strip().splitlines():
        want, name = line.split("  ", 1)
        got = hashlib.sha256((outdir / name).read_bytes()).hexdigest()
        if got != want:
            bad.append(name)
    return (not bad), bad


# ------------------------------------------------------------- selection
def structured_sport(market, event):
    team = market.get("team")
    if isinstance(team, dict) and (team.get("league") or team.get("abbreviation")):
        return "STRUCTURED_TEAM_METADATA", "team.league=%s" % team.get("league")
    for f in ("sportsMarketType", "gameId", "gameStartTime"):
        if market.get(f):
            return "STRUCTURED_SPORTS_METADATA", "%s present" % f
    ser = (event or {}).get("series")
    if isinstance(ser, dict) and ser.get("slug"):
        return "STRUCTURED_SERIES", "series.slug=%s" % ser["slug"]
    tags = (event or {}).get("tags")
    if isinstance(tags, list):
        slugs = [t.get("slug") for t in tags if isinstance(t, dict) and t.get("slug")]
        if slugs:
            return "STRUCTURED_TAG", "tags=%s" % ",".join(slugs[:3])
    return "NOT_IDENTIFIED", "no structured sports evidence"


def binary_sides(detail):
    sides = detail.get("marketSides")
    if not isinstance(sides, list) or not sides:
        return None, "no marketSides array"
    longs = [s for s in sides if isinstance(s, dict) and s.get("long") is True]
    shorts = [s for s in sides if isinstance(s, dict) and s.get("long") is False]
    if len(sides) == 2 and len(longs) == 1 and len(shorts) == 1:
        return {"long": longs[0], "short": shorts[0]}, "marketSides=2, one long one short"
    return None, "marketSides=%d longs=%d shorts=%d" % (
        len(sides), len(longs), len(shorts))


def discover(out, http, pacer, log, want):
    """Sections 2 and 3. Event identity native, or the market is excluded."""
    print("=== B/C. EVENT-NATIVE DISCOVERY ===")
    r, rows = get_paced(http, EVENTS_PATH, pacer,
                        {"active": "true", "closed": "false", "limit": 100})
    for x in rows:
        x["stage"] = "discovery_events"
    log.extend(rows)
    body = r.get("body") or {}
    events = body.get("events") or body.get("data") or []
    if not isinstance(events, list):
        events = []
    print("events returned: %d (http=%s)" % (len(events), r.get("http_status")))
    if events:
        print("event keys seen: %s" % sorted(
            {k for e in events[:40] if isinstance(e, dict) for k in e}))

    cands, excluded = [], []
    for e in events:
        if not isinstance(e, dict):
            continue
        ev_id, ev_slug = e.get("id"), e.get("slug")
        if ev_id is None or not ev_slug:
            excluded.append({"reason": "NO_NATIVE_EVENT_IDENTITY",
                             "event": str(e.get("slug") or e.get("id"))})
            continue
        for m in (e.get("markets") or []):
            if not isinstance(m, dict) or not m.get("slug"):
                continue
            prov, why = structured_sport(m, e)
            if prov == "NOT_IDENTIFIED":
                excluded.append({"reason": "NO_STRUCTURED_SPORTS_EVIDENCE",
                                 "market": m["slug"]})
                continue
            cands.append({
                "event_id": ev_id, "event_slug": ev_slug,
                "event_title": e.get("title"), "start_time": e.get("startTime"),
                "end_time": e.get("endTime"),
                "series": (e.get("series") or {}).get("slug")
                          if isinstance(e.get("series"), dict) else None,
                "tags": [t.get("slug") for t in (e.get("tags") or [])
                         if isinstance(t, dict)],
                "market_slug": m["slug"], "market_title": m.get("title"),
                "outcome": m.get("outcome"),
                "liquidity": m.get("liquidity"), "volume": m.get("volume"),
                "sports_provenance": prov, "sports_reason": why,
            })
    print("candidate markets with NATIVE event identity + structured sport: %d"
          % len(cands))
    print("excluded: %d" % len(excluded))

    # verify the binary structure on each candidate's own market detail
    verified = []
    for c in cands[: max(want * 6, 18)]:
        d, drows = get_paced(http, MARKET_PATH % c["market_slug"], pacer)
        for x in drows:
            x["stage"] = "complement_probe"
            x["market_slug"] = c["market_slug"]
        log.extend(drows)
        detail = ((d.get("body") or {}).get("market")
                  if isinstance(d.get("body"), dict) else None) or {}
        sides, why = binary_sides(detail)
        c["complement_reason"] = why
        if sides is None:
            c["complement_class"] = "NOT_BINARY"
            excluded.append({"reason": "NOT_BINARY_COMPLEMENT",
                             "market": c["market_slug"], "detail": why})
            continue
        c["complement_class"] = "BINARY_COMPLEMENT_VERIFIED"
        c["long_side"] = sides["long"].get("identifier")
        c["short_side"] = sides["short"].get("identifier")
        c["market_type"] = (detail.get("sportsMarketType")
                            or detail.get("marketType") or detail.get("type"))
        c["market_state"] = detail.get("status") or detail.get("state")
        # TICK SIZE AND FEE COEFFICIENT ARE ON THE WIRE, per market.
        # Phase 2A reported tick size NOT_IDENTIFIED because it looked in the
        # book payload and the SDK's types; both carry it in the MARKET DETAIL
        # as orderPriceMinTickSize (0.001 on the probed markets). Section 11
        # forbids manufacturing a tick, so capturing the venue's own value is
        # what makes the one-tick-behind measurement legitimate rather than
        # invented. feeCoefficient (0.06) rides along for the fee track: the
        # COEFFICIENT is the venue's, the p(1-p) FORM is borrowed from another
        # venue's published schedule and is NOT verified for PMUS.
        c["order_price_min_tick_size"] = detail.get("orderPriceMinTickSize")
        c["fee_coefficient"] = detail.get("feeCoefficient")
        c["minimum_trade_qty"] = detail.get("minimumTradeQty")
        c["best_bid_quote"] = detail.get("bestBidQuote")
        c["best_ask_quote"] = detail.get("bestAskQuote")
        c["market_detail_keys"] = sorted(detail.keys())
        verified.append(c)
    print("BINARY_COMPLEMENT_VERIFIED: %d" % len(verified))

    # regime slots, one market per event, order fixed before capture
    def pick(name, key, reverse=False, nonzero=False):
        pool = [x for x in verified
                if x["event_id"] not in used_events and key(x) is not None
                and (not nonzero or (key(x) or 0) > 0)]
        if not pool:
            print("  slot %-13s EMPTY (no candidate carries the field)" % name)
            return None
        x = sorted(pool, key=key, reverse=reverse)[0]
        x["regime_slot"] = name
        used_events.add(x["event_id"])
        print("  slot %-13s %s  (event %s)" % (name, x["market_slug"],
                                               x["event_slug"]))
        return x

    used_events, cohort = set(), []
    for nm, kf, rev, nz in (("NEAREST_TERM", lambda x: x.get("start_time"), False, False),
                            ("LONGEST_TERM", lambda x: x.get("start_time"), True, False),
                            ("MOST_LIQUID", lambda x: x.get("liquidity"), True, False),
                            ("THINNEST", lambda x: x.get("liquidity"), False, True)):
        got = pick(nm, kf, rev, nz)
        if got:
            cohort.append(got)
    cohort = cohort[:want]
    distinct = len({x["event_id"] for x in cohort})
    print("cohort: %d markets across %d DISTINCT native events"
          % (len(cohort), distinct))
    (Path(out) / "selection.json").write_text(json.dumps(
        {"phase": PHASE, "events_seen": len(events),
         "candidates": len(cands), "verified": len(verified),
         "cohort": cohort, "excluded": excluded[:200],
         "distinct_events_in_cohort": distinct}, indent=2))
    return cohort


def sample(out, http, pacer, markets, seconds, label, fh, log):
    """Fixed-clock sampling. One request per market carries BOTH sides."""
    t_end = time.monotonic() + seconds
    n = 0
    while time.monotonic() < t_end:
        for m in markets:
            if time.monotonic() >= t_end:
                break
            r, rows = get_paced(http, C.BOOK_PATH.format(slug=m["market_slug"]),
                                pacer)
            for x in rows:
                x["stage"] = "sample"
                x["segment"] = label
                x["market_slug"] = m["market_slug"]
                x["event_id"] = m["event_id"]
                x["event_slug"] = m["event_slug"]
                x["regime_slot"] = m.get("regime_slot")
                x["tick_size"] = m.get("order_price_min_tick_size")
                x["fee_coefficient"] = m.get("fee_coefficient")
                x["phase"] = PHASE
                if x.get("http_status") == 200:
                    v = book_view(x.get("body"))
                    x["view"] = {k: (str(val) if isinstance(val, Decimal) else val)
                                 for k, val in v.items()}
                fh.write(json.dumps(x) + "\n")
                n += 1
    print("  segment %-6s reads=%d spacing_now=%.2fs" % (label, n, pacer.spacing))
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--fast-seconds", type=float, default=300.0)
    ap.add_argument("--cohort-seconds", type=float, default=600.0)
    ap.add_argument("--cohort", type=int, default=3)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    # The driver owns the terminal report. Phase 2A let `tee` own it and sealed
    # it while the writer still held it open, recording the empty-string digest
    # for a file that was not empty. Nothing outside this process writes into
    # outdir, and the report is closed before anything is hashed.
    R = []
    _real_print = print

    def emit(*parts):
        line = " ".join(str(x) for x in parts)
        R.append(line)
        _real_print(line)

    import builtins
    builtins.print = emit          # discover()/sample() log through the report
    src = Path(__file__).with_name("run85_pmus_collector.py")
    meta = {
        "phase": PHASE,
        "collector_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python": sys.version, "platform": platform.platform(),
        "httpx": httpx.__version__, "gateway": C.GATEWAY_BASE,
        "base_spacing_s": BASE_SPACING_S,
        "aggregate_rps_ceiling": 1.0 / BASE_SPACING_S,
        "fast_seconds": a.fast_seconds, "cohort_seconds": a.cohort_seconds,
        "cohort_target": a.cohort,
    }
    (out / "runner_metadata.json").write_text(json.dumps(meta, indent=2))

    log = []
    pacer = AdaptivePacer()
    rc = 0
    try:
        emit(json.dumps(meta, indent=2))
        with httpx.Client(headers={"Accept": "application/json"}) as http:
            emit("=== A. PREFLIGHT ===")
            r, rows = get_paced(http, EVENTS_PATH, pacer, {"limit": 1})
            for x in rows:
                x["stage"] = "preflight"
            log.extend(rows)
            emit("events http=%s bytes=%s latency=%.1fms err=%s"
                 % (r.get("http_status"), r.get("response_bytes"),
                    r.get("latency_ms") or -1, r.get("error")))
            emit("headers %s" % json.dumps(r.get("response_headers") or {}))
            if r.get("http_status") != 200:
                emit("UNAUTHENTICATED_BOOK_ACCESS_STILL_CONFIRMED = NO")
                rc = 2
            else:
                emit("UNAUTHENTICATED_BOOK_ACCESS_STILL_CONFIRMED = YES")
                cohort = discover(out, http, pacer, log, a.cohort)
                if not cohort:
                    emit("NO COHORT under the documented rule -- nothing sampled.")
                    rc = 3
                else:
                    emit("")
                    emit("=== D. SAMPLING ===")
                    with (out / "book_samples.jsonl").open("w") as fh:
                        sample(out, http, pacer, cohort[:1], a.fast_seconds,
                               "FAST", fh, log)
                        sample(out, http, pacer, cohort, a.cohort_seconds,
                               "COHORT", fh, log)
    finally:
        builtins.print = _real_print

    (out / "request_log.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in log))

    R.append("")
    R.append("=== E. THROTTLE BEHAVIOUR ===")
    R.append("   429 backoff events: %d" % len(pacer.events))
    for e in pacer.events[:40]:
        R.append("     retry_after=%s slept=%s spacing %.2f -> %.2f"
                 % (e["retry_after"], e["slept_s"], e["spacing_before"],
                    e["spacing_after"]))
    R.append("   final spacing: %.2fs  (base %.2fs)" % (pacer.spacing, BASE_SPACING_S))
    (out / "throttle_events.json").write_text(json.dumps(pacer.events, indent=2))

    lines = seal(out, R)
    ok, bad = verify(out)
    print("=== J. SEAL ===")
    for line in lines:
        print(line)
    print("SEAL_SELF_VERIFY = %s%s"
          % ("PASS" if ok else "FAIL", "" if ok else " " + str(bad)))
    if not ok:
        return 4
    return rc


if __name__ == "__main__":
    sys.exit(main())
