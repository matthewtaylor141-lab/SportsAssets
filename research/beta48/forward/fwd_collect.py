#!/usr/bin/env python3
"""BETA48 FORWARD — PMUS public market-data collector. READ ONLY. NO CREDENTIAL.

WHY A NEW COLLECTOR AND NOT THE OLD ONE.

`research/run85_pmus_collector.py` proved the capability boundary: the PMUS
REST market-data surface answers on the PUBLIC gateway with no signature at
all, so observability never needed a trading key. That boundary is carried
here verbatim and re-proved by AST scan in test_fwd_collect.py.

What it could not do is the thing the whole retrospective programme died for:

  * `discover()` reads ONE page of 100 markets. The archaeology recorded the
    consequence as `DISCOVERY_LIST_EXHAUSTED = NO` -- a PREFIX of the board,
    not the board. A prefix reintroduces exactly the selection bias that made
    the RN1 snapshot unusable for fair value.
  * Nothing ever re-read a captured market after it settled. That single
    missing step is why 30 markets had two-sided books, 10,257 had outcomes,
    and the intersection was ZERO.

This collector fixes both, and separates two sampling jobs that must not be
confused:

  BREADTH  one pass over the WHOLE board, every eligible two-outcome event,
           no scoring, no shortlist, no activity filter. The selection rule is
           "it was on the board". This feeds fair value and the settlement
           sample.
  DEPTH    dense repeated reads of a rotating cohort, for execution questions
           -- quote changes, touches, markouts. A 2-hour breadth cadence can
           never answer a 5-second markout, and pretending otherwise is how a
           fill probability gets invented.

THROUGHPUT IS MEASURED, NEVER ASSUMED. The first runs record board size,
achieved request rate and settlement arrivals. Every calendar estimate in the
programme derives from those numbers afterwards. `PMUS_SETTLEMENT_THROUGHPUT`
stays NOT_IDENTIFIED until this measures it.

CAPABILITY BOUNDARY. GET only. gateway.polymarket.us only -- the authenticated
host is not a constant in this file and there is no branch that could build an
auth header. No credential, no secret, no wallet, no signer, no SDK, no order
or cancel endpoint, no POST/PUT/PATCH/DELETE. Rate is OUR restraint, not
evidence about what the venue permits.

NO PROFITABILITY CLAIM. This file records responses and derived observations.
It computes no expectancy, no ROI, no fill probability and no fee-adjusted
figure. Fills are decided offline, later, under frozen models, and TOUCH is
never written to a FILL field.

Usage:
    python3 fwd_collect.py board   --out DIR [--max-pages N]
    python3 fwd_collect.py breadth --out DIR --board FILE
    python3 fwd_collect.py depth   --out DIR --board FILE --seconds N
                                   [--cohort K] [--interval S]
    python3 fwd_collect.py settle  --out DIR --registry FILE
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

COLLECTOR_VERSION = "beta48-forward-public/1"

# The public gateway. The authenticated host is deliberately NOT a constant
# here: there is nothing in this file that could use one.
GATEWAY_BASE = "https://gateway.polymarket.us"
MARKETS_PATH = "/v1/markets"
BOOK_PATH = "/v1/markets/{slug}/book"

# OUR restraint. RPS_LIMIT_NOT_ESTABLISHED stays locked; this is not evidence
# about the venue's ceiling.
MAX_RPS = 2.0
MIN_SPACING_S = 1.0 / MAX_RPS
TIMEOUT_S = 10.0
PAGE_LIMIT = 100
MAX_PAGES_DEFAULT = 200          # a real terminal boundary, not a prefix


def _now():
    return (datetime.now(tz=timezone.utc).isoformat(), time.monotonic_ns())


class Pacer:
    """Never yields faster than MIN_SPACING_S. Honours Retry-After exactly."""

    def __init__(self, spacing_s=MIN_SPACING_S):
        self.spacing = max(float(spacing_s), MIN_SPACING_S)
        self._last = None
        self.requests = 0

    def wait(self):
        if self._last is not None:
            slack = (self._last + self.spacing) - time.monotonic()
            if slack > 0:
                time.sleep(slack)
        self._last = time.monotonic()
        self.requests += 1

    def backoff(self, seconds: float):
        time.sleep(max(0.0, float(seconds)))
        self._last = time.monotonic()


def _get(http, path, params=None):
    """One public GET. A failure is recorded AS a row, never left absent."""
    wall, mono = _now()
    row = {"path": path, "params": params, "local_request_wall_utc": wall,
           "local_request_monotonic_ns": mono, "http_status": None,
           "error": None, "body": None, "response_headers": None,
           "response_bytes": None, "response_sha256": None,
           "collector_version": COLLECTOR_VERSION}
    try:
        resp = http.get(GATEWAY_BASE + path, params=params, timeout=TIMEOUT_S)
        row["http_status"] = resp.status_code
        try:
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            row["response_headers"] = {
                k: v for k, v in hdrs.items()
                if ("ratelimit" in k or "rate-limit" in k or "retry" in k
                    or k in ("cache-control", "age", "date", "server",
                             "content-length", "content-type"))}
        except Exception:                                    # noqa: BLE001
            row["response_headers"] = None
        try:
            raw = resp.content
            row["response_bytes"] = len(raw)
            row["response_sha256"] = hashlib.sha256(raw).hexdigest()
        except Exception:                                    # noqa: BLE001
            pass
        if resp.status_code == 200:
            try:
                row["body"] = resp.json()
            except ValueError:
                row["error"] = "NON_JSON_BODY"
        else:
            row["error"] = "http_%d" % resp.status_code
    except httpx.HTTPError as exc:
        row["error"] = type(exc).__name__
    wall2, mono2 = _now()
    row["local_response_wall_utc"] = wall2
    row["local_response_monotonic_ns"] = mono2
    row["latency_ms"] = (mono2 - mono) / 1e6
    return row


def _retry_after(row) -> float | None:
    h = row.get("response_headers") or {}
    for k in ("retry-after", "x-ratelimit-reset"):
        if k in h:
            try:
                return float(h[k])
            except (TypeError, ValueError):
                return 60.0
    return None


def _paced_get(http, pacer, path, params=None, attempts=3):
    """GET with the pacer and an exact Retry-After honour. Never speeds up."""
    for i in range(attempts):
        pacer.wait()
        row = _get(http, path, params)
        if row.get("http_status") == 429 or row.get("http_status") == 503:
            ra = _retry_after(row)
            if i + 1 < attempts:
                pacer.backoff(ra if ra is not None else 60.0)
                continue
        return row
    return row


# ------------------------------------------------------------------ board --

def board(outdir: Path, pacer, http, max_pages=MAX_PAGES_DEFAULT) -> dict:
    """Enumerate the WHOLE active board, walked to a terminal boundary.

    `DISCOVERY_LIST_EXHAUSTED` is written as YES only when a page comes back
    short or empty. If the page cap is hit first it is written NO, and the
    dataset is then a prefix and must be treated as one.
    """
    raw, pages = [], 0
    offset = 0
    exhausted = False
    advanced = True
    by_slug: dict = {}                 # DEDUPE. See why, below.
    while pages < max_pages:
        params = {"active": "true", "closed": "false",
                  "limit": PAGE_LIMIT, "offset": offset}
        r = _paced_get(http, pacer, MARKETS_PATH, params)
        raw.append(r)
        pages += 1
        body = r.get("body") or {}
        items = body.get("markets") or body.get("data") or []
        if not isinstance(items, list) or not items:
            exhausted = True
            break
        # DEDUPE BY SLUG, AND DETECT A PAGINATION THAT DOES NOT ADVANCE.
        #
        # The first live run walked all 200 pages in 101 s and then selected
        # ZERO two-outcome events. That is the signature of a repeated page:
        # the same market arriving many times makes its event look like it has
        # 200 outcomes, so every event fails the len(ms) == 2 test and the
        # whole board is silently discarded. The repository already knows this
        # venue family ignores paging params -- reference_pull.py carries
        # "NEVER the offset param" in its header for the data-api.
        #
        # Deduping makes the grouping correct whatever the API does, and a page
        # that contributes no NEW slug ends the walk and is NAMED, so a
        # non-advancing cursor can never again be mistaken for a large board.
        fresh = 0
        for m in items:
            if not isinstance(m, dict):
                continue
            slug = m.get("slug")
            if not slug or slug in by_slug:
                continue
            by_slug[slug] = m
            fresh += 1
        if fresh == 0:
            advanced = False
            exhausted = True
            break
        if len(items) < PAGE_LIMIT:
            exhausted = True
            break
        offset += len(items)

    markets = list(by_slug.values())
    by_event = {}
    for m in markets:
        ev, slug = m.get("eventSlug"), m.get("slug")
        if not ev or not slug:
            continue
        by_event.setdefault(ev, []).append(m)

    selected, skipped = [], []
    for ev, ms in by_event.items():
        if len(ms) == 2:
            selected.append({
                "eventSlug": ev,
                "slugs": sorted(x["slug"] for x in ms),
                "titles": [x.get("title") for x in ms],
                "gameStartTime": next((x.get("gameStartTime") for x in ms
                                       if x.get("gameStartTime")), None),
                "endDate": next((x.get("endDate") for x in ms
                                 if x.get("endDate")), None),
                "sportsMarketTypeV2": next(
                    (x.get("sportsMarketTypeV2") for x in ms
                     if x.get("sportsMarketTypeV2")), None),
                "tags": next((x.get("tags") for x in ms if x.get("tags")), None),
                "status": [x.get("status") for x in ms],
            })
        else:
            skipped.append({"eventSlug": ev, "outcome_market_count": len(ms),
                            "reason": "NOT_A_TWO_OUTCOME_EVENT"})

    wall, _ = _now()
    out = {"collector_version": COLLECTOR_VERSION,
           "captured_at_utc": wall,
           "pages_walked": pages,
           "DISCOVERY_LIST_EXHAUSTED": "YES" if exhausted else "NO",
           "PAGINATION_ADVANCED": "YES" if advanced else "NO",
           "markets_seen": len(markets),
           "events_two_outcome": len(selected),
           "events_skipped": len(skipped),
           "requests_used": pacer.requests,
           "selected": selected, "skipped": skipped}
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "board_raw.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in raw))
    (outdir / "board.json").write_text(json.dumps(out, indent=1))
    print("distinct markets %d | two-outcome events %d | pages %d | "
          "exhausted %s | pagination advanced %s"
          % (len(markets), len(selected), pages,
             out["DISCOVERY_LIST_EXHAUSTED"], out["PAGINATION_ADVANCED"]))
    return out


# ---------------------------------------------------------------- breadth --

def breadth(outdir: Path, events, pacer, http) -> int:
    """One pass over EVERY event on the board. Both legs back to back.

    The two legs are read consecutively and the gap between them is recorded
    on every pair. Run 84 established that an uncontrolled gap IS the result;
    the whole reconstructed-mid finding this sprint rests on the gap being
    small and known.
    """
    path = outdir / "breadth.jsonl"
    n = 0
    with path.open("a") as fh:
        for ev in events:
            slugs = ev.get("slugs") or []
            if len(slugs) != 2:
                continue
            legs = []
            for s in slugs:
                legs.append(_paced_get(http, pacer, BOOK_PATH.format(slug=s)))
            gap_ns = abs(legs[1]["local_request_monotonic_ns"]
                         - legs[0]["local_request_monotonic_ns"])
            fh.write(json.dumps({
                "kind": "BREADTH",
                "eventSlug": ev.get("eventSlug"),
                "slugs": slugs,
                "gameStartTime": ev.get("gameStartTime"),
                "endDate": ev.get("endDate"),
                "sportsMarketTypeV2": ev.get("sportsMarketTypeV2"),
                "tags": ev.get("tags"),
                "leg_gap_ns": gap_ns,
                "leg_gap_s": gap_ns / 1e9,
                "legs": legs}) + "\n")
            n += 1
    print("breadth events written %d | requests %d" % (n, pacer.requests))
    return n


# ------------------------------------------------------------------ depth --

def depth(outdir: Path, events, seconds, cohort, interval_s, pacer, http) -> int:
    """Dense repeated reads of a rotating cohort, for EXECUTION questions.

    Cohort selection is by TIME TO EVENT -- the markets closest to starting --
    because that is where a maker would be quoting. This is a deliberate,
    declared selection and it is NOT the fair-value population: breadth is.
    Recorded separately for exactly that reason.
    """
    def tte(ev):
        g = ev.get("gameStartTime")
        if not g:
            return 1e18
        try:
            t = datetime.fromisoformat(str(g).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return 1e18
        return abs(t - time.time())

    picked = sorted(events, key=tte)[:max(1, int(cohort))]
    path = outdir / "depth.jsonl"
    t_end = time.monotonic() + float(seconds)
    rounds = 0
    with path.open("a") as fh:
        while time.monotonic() < t_end:
            cycle_start = time.monotonic()
            for ev in picked:
                if time.monotonic() >= t_end:
                    break
                slugs = ev.get("slugs") or []
                if len(slugs) != 2:
                    continue
                legs = [_paced_get(http, pacer, BOOK_PATH.format(slug=s))
                        for s in slugs]
                gap_ns = abs(legs[1]["local_request_monotonic_ns"]
                             - legs[0]["local_request_monotonic_ns"])
                fh.write(json.dumps({
                    "kind": "DEPTH",
                    "eventSlug": ev.get("eventSlug"),
                    "slugs": slugs,
                    "gameStartTime": ev.get("gameStartTime"),
                    "endDate": ev.get("endDate"),
                    "round": rounds,
                    "leg_gap_ns": gap_ns,
                    "leg_gap_s": gap_ns / 1e9,
                    "legs": legs}) + "\n")
            rounds += 1
            slack = (cycle_start + float(interval_s)) - time.monotonic()
            if slack > 0:
                time.sleep(slack)
    print("depth cohort %d | rounds %d | requests %d"
          % (len(picked), rounds, pacer.requests))
    return rounds


# ----------------------------------------------------------------- settle --

def settle(outdir: Path, registry_path: Path, pacer, http) -> dict:
    """Re-read captured slugs until the venue calls them resolved.

    THE STEP WHOSE ABSENCE WAS THE ENTIRE GAP. Without it a capture holds
    prices with no outcomes and an archive holds outcomes with no prices, and
    they never meet.
    """
    reg = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    pending = [s for s, v in reg.items() if not v.get("resolved")]
    now = time.time()

    def due(slug):
        ed = reg[slug].get("endDate")
        if not ed:
            return True
        try:
            return datetime.fromisoformat(
                str(ed).replace("Z", "+00:00")).timestamp() <= now
        except (TypeError, ValueError):
            return True

    todo = [s for s in pending if due(s)]
    raw, newly = [], 0
    for s in todo:
        r = _paced_get(http, pacer, MARKETS_PATH, {"slug": s})
        raw.append(r)
        body = r.get("body") or {}
        items = body.get("markets") or body.get("data") or []
        m = items[0] if isinstance(items, list) and items else None
        if not isinstance(m, dict):
            continue
        status = m.get("status")
        prices = m.get("outcomePrices") or m.get("outcome_prices")
        reg[s]["last_seen_status"] = status
        reg[s]["last_checked_utc"] = _now()[0]
        if status and "RESOLVED" in str(status).upper() and prices:
            reg[s]["resolved"] = True
            reg[s]["outcomePrices"] = prices
            reg[s]["resolved_seen_utc"] = _now()[0]
            newly += 1

    outdir.mkdir(parents=True, exist_ok=True)
    with (outdir / "settle_raw.jsonl").open("a") as fh:
        for x in raw:
            fh.write(json.dumps(x) + "\n")
    registry_path.write_text(json.dumps(reg, indent=1, sort_keys=True))
    total = sum(1 for v in reg.values() if v.get("resolved"))
    print("settle checked %d | newly resolved %d | resolved total %d of %d"
          % (len(todo), newly, total, len(reg)))
    return {"checked": len(todo), "newly_resolved": newly,
            "resolved_total": total, "registry_size": len(reg)}


def update_registry(registry_path: Path, events) -> int:
    """Every event seen by BREADTH enters the settlement registry."""
    reg = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    added = 0
    for ev in events:
        for s in (ev.get("slugs") or []):
            if s not in reg:
                reg[s] = {"eventSlug": ev.get("eventSlug"),
                          "endDate": ev.get("endDate"),
                          "gameStartTime": ev.get("gameStartTime"),
                          "first_seen_utc": _now()[0],
                          "resolved": False}
                added += 1
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(reg, indent=1, sort_keys=True))
    return added


# ------------------------------------------------------------------- main --

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("board", "breadth", "depth", "settle"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--board")
    ap.add_argument("--registry")
    ap.add_argument("--seconds", type=float, default=600.0)
    ap.add_argument("--cohort", type=int, default=8)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES_DEFAULT)
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pacer = Pacer()
    with httpx.Client(headers={"User-Agent": COLLECTOR_VERSION}) as http:
        if a.mode == "board":
            board(out, pacer, http, a.max_pages)
            return 0
        if a.mode == "settle":
            settle(out, Path(a.registry), pacer, http)
            return 0
        b = json.loads(Path(a.board).read_text())
        events = b.get("selected") or []
        if a.mode == "breadth":
            if a.registry:
                n = update_registry(Path(a.registry), events)
                print("registry additions %d" % n)
            breadth(out, events, pacer, http)
        else:
            depth(out, events, a.seconds, a.cohort, a.interval, pacer, http)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
