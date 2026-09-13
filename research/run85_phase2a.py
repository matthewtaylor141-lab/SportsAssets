#!/usr/bin/env python3
"""RUN 85 PHASE 2A -- PMUS calibration capture. Sections A-D only.

This file COLLECTS. It does not interpret. Sections E-H of the Phase 2A
specification are computed later, from the retrieved raw evidence, so that no
verdict rests on a terminal log.

It adds no request primitive of its own: every venue call goes through
run85_pmus_collector._get, whose capability boundary is the one under test in
test_run85_pmus_collector.py. GET only, public gateway only, no credential.

ONE STRUCTURAL NOTE THAT SHAPES SECTION C, recorded before any data arrives.

A PMUS market carries a `marketSides` array whose entries have a `long`
boolean (backend/sportsassets/pmus.py:653-700, which reads it in production).
So a single market holds BOTH sides of one binary contract. That gives two
different pair constructions, and they are not equally trustworthy:

  WITHIN-MARKET   long + short of the SAME contract. Mutually exclusive and
                  exhaustive BY CONSTRUCTION -- it is one contract, not two
                  correlated ones. Both sides come from ONE book response, so
                  the two legs carry ZERO time skew. Run 84 established that
                  leg skew is what manufactures fake pair edge; here it cannot
                  arise.

  ACROSS-MARKET   outcome A of one market + outcome B of another, sharing an
                  eventSlug. NOT guaranteed to sum to $1: a three-way football
                  event shares its eventSlug with a draw market too. This needs
                  positive evidence of a two-outcome structure and still leaves
                  a basis, so it is classified separately and never merged with
                  the within-market set.

Both are captured. They are never pooled.

Usage:
  python3 run85_phase2a.py --out DIR --seconds 900 --interval 10 --markets 12
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import sys
import time
from pathlib import Path

import httpx

_spec = importlib.util.spec_from_file_location(
    "run85c", Path(__file__).with_name("run85_pmus_collector.py"))
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)

PHASE = "run85/phase2a/1"

# Structured sports evidence ONLY. Phase 2A section B forbids broad substring
# classification, and Run 83.6E.1 is why: a free-text rule matched "epl" inside
# an Oprah market's description and put it in a sports cohort. These are
# structured PMUS fields, each read as a field and never as a substring of a
# blob.
SPORTS_EVIDENCE_FIELDS = ("team", "league", "sportsMarketType", "gameId",
                          "gameStartTime", "series", "tags")


def classify_sports(market: dict, event: dict | None):
    """(provenance, reason). Structured fields only; no free-text matching."""
    team = market.get("team")
    if isinstance(team, dict) and (team.get("league") or team.get("abbreviation")):
        return "STRUCTURED_TEAM_METADATA", "team.league=%s" % team.get("league")
    for f in ("sportsMarketType", "gameId", "gameStartTime"):
        if market.get(f):
            return "STRUCTURED_SPORTS_METADATA", "%s present" % f
    if event:
        ser = event.get("series")
        if isinstance(ser, dict) and ser.get("slug"):
            return "STRUCTURED_SERIES", "series.slug=%s" % ser["slug"]
        tags = event.get("tags")
        if isinstance(tags, list) and tags:
            labels = [t.get("slug") for t in tags if isinstance(t, dict)]
            if any(labels):
                return "STRUCTURED_TAG", "tags=%s" % ",".join(
                    str(x) for x in labels[:3])
    return "NOT_IDENTIFIED", "no structured sports evidence"


def classify_complement(market_detail: dict):
    """BINARY_COMPLEMENT_VERIFIED / MULTI_OUTCOME_EVENT / NOT_IDENTIFIED.

    Verified requires the venue's own marketSides to show exactly two sides,
    exactly one of them long. Anything else is named, never assumed into the
    verified class.
    """
    sides = market_detail.get("marketSides")
    if not isinstance(sides, list) or not sides:
        return "NOT_IDENTIFIED", "no marketSides array", None
    longs = [s for s in sides if isinstance(s, dict) and s.get("long") is True]
    shorts = [s for s in sides if isinstance(s, dict) and s.get("long") is False]
    if len(sides) == 2 and len(longs) == 1 and len(shorts) == 1:
        return ("BINARY_COMPLEMENT_VERIFIED",
                "marketSides=2, one long, one short",
                {"long_identifier": longs[0].get("identifier"),
                 "short_identifier": shorts[0].get("identifier")})
    if len(sides) > 2:
        return "MULTI_OUTCOME_EVENT", "marketSides=%d" % len(sides), None
    return "NOT_IDENTIFIED", "marketSides=%d longs=%d shorts=%d" % (
        len(sides), len(longs), len(shorts)), None


def preflight(out, pacer, http, log):
    """Section A. Two calls, everything recorded, no interpretation."""
    print("=== A. PREFLIGHT ===")
    pacer.wait()
    r1 = C._get(http, "/v1/markets", {"limit": 1})
    r1["stage"] = "preflight_discovery"
    log.append(r1)
    print("discovery  http=%s bytes=%s latency=%.1fms err=%s"
          % (r1["http_status"], r1["response_bytes"],
             r1.get("latency_ms") or -1, r1["error"]))
    print("headers    %s" % json.dumps(r1.get("response_headers") or {}))

    slug = None
    body = r1.get("body") or {}
    items = body.get("markets") or body.get("data") or []
    if isinstance(items, list) and items and isinstance(items[0], dict):
        slug = items[0].get("slug")
    if not slug:
        print("PREFLIGHT: no market slug in the discovery body")
        print("PMUS_PUBLIC_BOOK_ACCESS_VENUE_VERIFIED = NO")
        return False, None

    pacer.wait()
    r2 = C._get(http, C.BOOK_PATH.format(slug=slug))
    r2["stage"] = "preflight_book"
    r2["market_slug"] = slug
    log.append(r2)
    print("book       slug=%s http=%s bytes=%s latency=%.1fms err=%s"
          % (slug, r2["http_status"], r2["response_bytes"],
             r2.get("latency_ms") or -1, r2["error"]))
    print("headers    %s" % json.dumps(r2.get("response_headers") or {}))
    ok = r2["http_status"] == 200 and isinstance(r2.get("body"), dict)
    if ok:
        b = r2["body"]
        print("body keys  %s" % sorted(b.keys()))
        print("bids=%d offers=%d state=%s transactTime=%s"
              % (len(b.get("bids") or []), len(b.get("offers") or []),
                 b.get("state"), b.get("transactTime")))
    print("PMUS_PUBLIC_BOOK_ACCESS_VENUE_VERIFIED = %s" % ("YES" if ok else "NO"))
    return ok, slug


def discover(out, pacer, http, log, want):
    """Sections B and C."""
    print()
    print("=== B/C. DISCOVERY AND COMPLEMENT VALIDATION ===")
    pacer.wait()
    r = C._get(http, "/v1/markets",
               {"active": "true", "closed": "false", "limit": 200})
    r["stage"] = "discovery"
    log.append(r)
    body = r.get("body") or {}
    markets = body.get("markets") or body.get("data") or []
    if not isinstance(markets, list):
        markets = []
    print("markets returned: %d  (http=%s)" % (len(markets), r["http_status"]))
    if markets:
        print("market keys seen: %s" % sorted(
            {k for m in markets[:50] if isinstance(m, dict) for k in m}))

    rows = []
    for m in markets:
        if not isinstance(m, dict) or not m.get("slug"):
            continue
        prov, reason = classify_sports(m, None)
        rows.append({"slug": m["slug"], "eventSlug": m.get("eventSlug"),
                     "title": m.get("title"), "outcome": m.get("outcome"),
                     "sports_provenance": prov, "sports_reason": reason,
                     "liquidity": m.get("liquidity"), "volume": m.get("volume"),
                     "active": m.get("active"), "closed": m.get("closed")})

    sporty = [x for x in rows if x["sports_provenance"] != "NOT_IDENTIFIED"]
    print("structured-sports markets: %d of %d" % (len(sporty), len(rows)))
    from collections import Counter
    print("provenance: %s" % dict(Counter(x["sports_provenance"] for x in rows)))

    # Diversity without free text: spread the sample across distinct events.
    #
    # MEASURED 2026-09-13: /v1/markets returns eventSlug = None on EVERY row, so
    # the fallback below is not a rare edge -- it is the whole population. The
    # first calibration run keyed 12 markets from two MLB championship-futures
    # families as 12 separate "events" and reported diversity it did not have.
    # Run 83.6E.1 required that a per-row fallback be disclosed if any selected
    # market uses it; it was used by all of them and went unreported, so the
    # grouping now NAMES its own source and the caller cannot miss it.
    grouped_by = "eventSlug"
    if not any(x.get("eventSlug") for x in sporty):
        grouped_by = "PER_ROW_SLUG_FALLBACK"
    by_event = {}
    for x in sporty:
        by_event.setdefault(x.get("eventSlug") or x["slug"], []).append(x)
    chosen = []
    for ev, xs in by_event.items():
        chosen.append(sorted(xs, key=lambda z: -(z["liquidity"] or 0))[0])
        if len(chosen) >= want:
            break
    print("DISCOVERY_EVENT_GROUPING = %s  (%d of %d rows carry an eventSlug)"
          % (grouped_by, sum(1 for x in sporty if x.get("eventSlug")), len(sporty)))
    if grouped_by == "PER_ROW_SLUG_FALLBACK":
        print("  WARNING: these are NOT verified distinct events. Event identity")
        print("  is unavailable from /v1/markets and must come from /v1/events.")
        print("  Across-market pair economics (section G2) CANNOT be computed")
        print("  from this selection, and diversity must not be claimed.")
    print("selected markets: %d" % len(chosen))

    verified = []
    for x in chosen:
        pacer.wait()
        d = C._get(http, "/v1/market/slug/%s" % x["slug"])
        d["stage"] = "complement_probe"
        d["market_slug"] = x["slug"]
        log.append(d)
        md = ((d.get("body") or {}).get("market")
              if isinstance(d.get("body"), dict) else None) or {}
        cls, why, ids = classify_complement(md)
        x["complement_class"] = cls
        x["complement_reason"] = why
        x["side_identifiers"] = ids
        x["market_detail_keys"] = sorted(md.keys()) if md else []
        verified.append(x)
        print("  %-44s %-28s %s" % (x["slug"][:44], cls, why))

    (Path(out) / "selection.json").write_text(json.dumps(
        {"phase": PHASE, "markets_seen": len(rows), "selected": verified,
         "sports_evidence_fields": list(SPORTS_EVIDENCE_FIELDS)}, indent=2))
    return verified


def sample(out, pacer, http, events, seconds, interval, log):
    """Section D. Fixed schedule. Nothing external can enter it."""
    print()
    print("=== D. CALIBRATION SAMPLING (%.0fs, every %.0fs) ==="
          % (seconds, interval))
    fh = (Path(out) / "book_samples.jsonl").open("w")
    t_end = time.monotonic() + seconds
    n = cycles = 0
    while time.monotonic() < t_end:
        c0 = time.monotonic()
        for x in events:
            if time.monotonic() >= t_end:
                break
            pacer.wait()
            r = C._get(http, C.BOOK_PATH.format(slug=x["slug"]))
            r["stage"] = "sample"
            r["market_slug"] = x["slug"]
            r["event_slug"] = x["eventSlug"]
            r["complement_class"] = x.get("complement_class")
            r["cycle"] = cycles
            r["phase"] = PHASE
            fh.write(json.dumps(r) + "\n")
            n += 1
        cycles += 1
        slack = c0 + interval - time.monotonic()
        if slack > 0:
            time.sleep(min(slack, max(0.0, t_end - time.monotonic())))
    fh.close()
    print("book reads: %d over %d cycles" % (n, cycles))
    return n, cycles


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=900.0)
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--markets", type=int, default=12)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    src = Path(__file__).with_name("run85_pmus_collector.py")
    import hashlib
    meta = {
        "phase": PHASE,
        "collector_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python": sys.version, "platform": platform.platform(),
        "httpx": httpx.__version__,
        "gateway": C.GATEWAY_BASE, "max_rps": C.MAX_RPS,
        "seconds": a.seconds, "interval": a.interval, "markets": a.markets,
    }
    (out / "runner_metadata.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))

    log = []
    with httpx.Client(headers={"Accept": "application/json"}) as http:
        pacer = C.Pacer()
        ok, _ = preflight(out, pacer, http, log)
        if not ok:
            (out / "request_log.jsonl").write_text(
                "".join(json.dumps(x) + "\n" for x in log))
            C.seal(out)
            return 2
        events = discover(out, pacer, http, log, a.markets)
        if not events:
            print("no selectable markets; stopping before sampling")
            (out / "request_log.jsonl").write_text(
                "".join(json.dumps(x) + "\n" for x in log))
            C.seal(out)
            return 3
        sample(out, pacer, http, events, a.seconds, a.interval, log)

    (out / "request_log.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in log))
    print()
    print("=== J. EVIDENCE ===")
    C.seal(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
