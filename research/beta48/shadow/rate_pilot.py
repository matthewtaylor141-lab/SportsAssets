#!/usr/bin/env python3
"""READ-ONLY RATE-LIMIT PILOT. Find a sustainable request rate before capturing.

WHY THIS EXISTS. Run 35120338223 polled 24 markets at 2.0 rps and took 5,835
HTTP 429s -- 81% of every read. Six markets survived, all NFL, and they survived
because they were polled FIRST. The pipeline worked perfectly and the sample was
destroyed. Repeating that experiment at the same rate would destroy it again.

So: measure the limit before spending another capture window on it.

WHAT THIS DOES, AND THE FOUR RULES IT FOLLOWS.

    SINGLE GLOBAL SCHEDULER   one pacer, one thread, no concurrency anywhere.
    NO BURSTS                 the gap is enforced BEFORE each request, always.
    GLOBAL BACKOFF ON 429     a 429 pauses EVERY market, not the one that hit
                              it. A per-market retry loop is exactly what
                              starves the markets later in the round.
    HONOUR RETRY-AFTER        if the venue sends one, we wait that long. If it
                              does not, we back off on our own schedule and say
                              which of the two happened.

WHAT THIS REFUSES TO DO.

Headers are REPORTED, never INTERPRETED. If the venue sends something that
looks like a budget header we record its name and value verbatim and stop
there. `RATE_LIMIT_SEMANTICS = NOT_IDENTIFIED` until the venue documents them:
guessing that some `x-ratelimit-remaining` counts requests per minute, and
pacing against that guess, would be the SHARES_TRADED mistake again in a
different field.

And the rate is not chosen for being fast. `SUSTAINABLE_RATE_SELECTED` is the
fastest rate whose measured 429 share is at or under the threshold with margin,
and the reason is recorded beside it. A rate that merely completed is not
evidence that it was sustainable.

NO ORDERS. NO CREDENTIALS. GET ONLY. This module contacts the venue, and
`test_rate_pilot.py` proves the boundary by AST rather than by grep.
"""
from __future__ import annotations

import json
import sys
import time
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import collect as C                                            # noqa: E402

NOT_IDENTIFIED = "NOT_IDENTIFIED"

READ_ONLY = True
ORDER_PATH_EXISTS = False
CREDENTIAL_PATH = "NONE"
mirror_live = False

# The one host, imported rather than retyped -- the error that was caught
# before the first dispatch and is not going to be reintroduced here.
HOST = C.HOST

# Header names worth LOOKING for. Finding one records it; it does not license
# an interpretation.
RATE_LIMIT_HEADER_CANDIDATES = (
    "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining",
    "x-ratelimit-reset", "ratelimit-limit", "ratelimit-remaining",
    "ratelimit-reset", "x-rate-limit-limit", "x-rate-limit-remaining",
    "x-rate-limit-reset",
)
RATE_LIMIT_SEMANTICS = NOT_IDENTIFIED
HEADERS_ARE_REPORTED_NOT_INTERPRETED = True

# The ladder, frozen here so it is not chosen after seeing the results.
DEFAULT_RATE_LADDER = ("0.25", "0.5", "1.0", "2.0")
REQUESTS_PER_RATE = 40
ACCEPTABLE_429_SHARE = D("0.01")
MARGIN_RULE = "PICK_THE_FASTEST_PASSING_RATE_ONE_STEP_BELOW_THE_FIRST_FAILURE"

# A 429 stops everything for this long when the venue does not say otherwise.
DEFAULT_BACKOFF_S = 30.0
MAX_BACKOFF_S = 120.0


class GlobalPacer:
    """One gap for the whole run, plus a global pause after a 429.

    The pause is GLOBAL on purpose. The previous capture's per-market read
    simply moved on to the next slug after a refusal, which meant the earliest
    slugs consumed the budget every round and the later ones never got a turn.
    """

    def __init__(self, rps):
        self.gap = 1.0 / float(rps)
        self.last = 0.0
        self.paused_until = 0.0
        self.pauses = 0
        self.paused_s = 0.0

    def wait(self):
        now = time.monotonic()
        due = max(self.last + self.gap, self.paused_until)
        if now < due:
            time.sleep(due - now)
        self.last = time.monotonic()

    def back_off(self, retry_after=None):
        """Pause every market. Honour Retry-After when the venue sends one."""
        if retry_after is not None:
            try:
                wait = min(float(retry_after), MAX_BACKOFF_S)
                src = "RETRY_AFTER_HEADER"
            except (TypeError, ValueError):
                wait, src = DEFAULT_BACKOFF_S, "OUR_OWN_SCHEDULE"
        else:
            wait, src = DEFAULT_BACKOFF_S, "OUR_OWN_SCHEDULE"
        self.paused_until = time.monotonic() + wait
        self.pauses += 1
        self.paused_s += wait
        return {"BACKOFF_S": wait, "BACKOFF_SOURCE": src}


def observed_headers(headers):
    """Any rate-limit-looking header, verbatim. No meaning attached."""
    if not headers:
        return {}
    low = {str(k).lower(): v for k, v in dict(headers).items()}
    return {k: low[k] for k in RATE_LIMIT_HEADER_CANDIDATES if k in low}


def probe(http, pacer, slug):
    """One paced public GET, keeping the response headers. GET only."""
    pacer.wait()
    url = HOST + C.BOOK_PATH.format(slug=slug)
    recv = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    t0 = time.monotonic()
    try:
        r = http.get(url, timeout=20.0)
    except Exception as exc:                                   # noqa: BLE001
        return {"slug": slug, "RECEIPT_UTC": recv, "status": None,
                "error": "%s: %s" % (type(exc).__name__, exc),
                "LATENCY_S": time.monotonic() - t0, "HEADERS": {}}
    return {
        "slug": slug, "RECEIPT_UTC": recv, "status": r.status_code,
        "error": None if r.status_code == 200 else "http_%d" % r.status_code,
        "LATENCY_S": time.monotonic() - t0,
        "HEADERS": observed_headers(getattr(r, "headers", None)),
    }


def run_rate(http, slugs, rps, requests=REQUESTS_PER_RATE, rows=None):
    """Probe at ONE rate, rotating through the slugs. Returns the result row.

    The rotation matters even in the pilot: if a rate is marginal, we want the
    refusals spread across markets rather than concentrated on whichever slug
    happens to be last.
    """
    pacer = GlobalPacer(rps)
    ok = n429 = other = 0
    headers_seen, backoffs = {}, []
    t0 = time.monotonic()
    for i in range(requests):
        r = probe(http, pacer, slugs[i % len(slugs)])
        if rows is not None:
            rows.append(dict(r, RATE=str(rps)))
        for k, v in r["HEADERS"].items():
            headers_seen.setdefault(k, str(v))
        if r["status"] == 200:
            ok += 1
        elif r["status"] == 429:
            n429 += 1
            backoffs.append(pacer.back_off(r["HEADERS"].get("retry-after")))
        else:
            other += 1
    share = (D(n429) / D(requests)) if requests else NOT_IDENTIFIED
    return {
        "RATE_RPS": str(rps),
        "REQUESTS": requests,
        "SUCCESSFUL_REQUESTS": ok,
        "HTTP_429_COUNT": n429,
        "OTHER_FAILURES": other,
        "HTTP_429_SHARE": share,
        "WALL_S": time.monotonic() - t0,
        "GLOBAL_BACKOFFS": len(backoffs),
        "BACKOFF_SOURCES": sorted({b["BACKOFF_SOURCE"] for b in backoffs}),
        "RATE_LIMIT_HEADERS_SEEN": headers_seen,
        "PASSES": share != NOT_IDENTIFIED and share <= ACCEPTABLE_429_SHARE,
    }


def select_rate(ladder_results):
    """The fastest rate that PASSED, and no faster -- with the reason.

    Ordered ascending, the first failure ends it: a faster rate that happened
    to pass after a slower one failed would be noise, and taking it would be
    choosing the rate that flattered the result.
    """
    chosen, reason = None, ""
    for row in ladder_results:
        if row["PASSES"]:
            chosen = row["RATE_RPS"]
        else:
            reason = ("stopped at the first rate whose 429 share exceeded "
                      "%s (%s rps)" % (ACCEPTABLE_429_SHARE, row["RATE_RPS"]))
            break
    if chosen is None:
        return {
            "SUSTAINABLE_RATE_SELECTED": NOT_IDENTIFIED,
            "SELECTION_REASON": ("no tested rate held the 429 share at or "
                                 "under %s; the ladder needs a slower rung"
                                 % ACCEPTABLE_429_SHARE),
            "CAPTURE_MAY_PROCEED": False,
        }
    return {
        "SUSTAINABLE_RATE_SELECTED": chosen,
        "SELECTION_REASON": (reason or
                             "every tested rate passed; the ladder's top rung "
                             "is the selection and a faster one is UNTESTED"),
        "MARGIN_RULE": MARGIN_RULE,
        "NOT_CHOSEN_FOR_BEING_FASTEST": True,
        "CAPTURE_MAY_PROCEED": True,
    }


def pilot(outdir, slugs, http, ladder=DEFAULT_RATE_LADDER,
          requests=REQUESTS_PER_RATE):
    """The whole pilot: ascending ladder, per-rate results, one selection."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    rows, ladder_results = [], []
    for rps in ladder:
        ladder_results.append(run_rate(http, slugs, rps, requests, rows))
    with (out / "pilot_rows.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, default=str) + "\n")

    total = sum(r["REQUESTS"] for r in ladder_results)
    ok = sum(r["SUCCESSFUL_REQUESTS"] for r in ladder_results)
    n429 = sum(r["HTTP_429_COUNT"] for r in ladder_results)
    headers = {}
    for r in ladder_results:
        headers.update(r["RATE_LIMIT_HEADERS_SEEN"])

    report = {
        "PILOT_REQUESTS": total,
        "SUCCESSFUL_REQUESTS": ok,
        "HTTP_429_COUNT": n429,
        "HTTP_429_RATE": (D(n429) / D(total)) if total else NOT_IDENTIFIED,
        "OTHER_FAILURES": sum(r["OTHER_FAILURES"] for r in ladder_results),
        "TESTED_REQUEST_RATES": [r["RATE_RPS"] for r in ladder_results],
        "PER_RATE": ladder_results,
        "MARKETS_PROBED": len(slugs),

        # Headers: recorded, not interpreted.
        "RATE_LIMIT_HEADERS_SEEN": headers,
        "RATE_LIMIT_HEADERS_FOUND": bool(headers),
        "RATE_LIMIT_SEMANTICS": RATE_LIMIT_SEMANTICS,
        "HEADERS_ARE_REPORTED_NOT_INTERPRETED":
            HEADERS_ARE_REPORTED_NOT_INTERPRETED,

        "ACCEPTABLE_429_SHARE": ACCEPTABLE_429_SHARE,
        "SCHEDULER": "SINGLE_GLOBAL_PACER_NO_CONCURRENCY",
        "BACKOFF": "GLOBAL_ON_429_HONOURING_RETRY_AFTER_WHEN_PRESENT",
        "PER_MARKET_RETRY_LOOP": "NONE_BY_CONSTRUCTION",

        "ORDERS_PLACED": 0,
        "CREDENTIALS": CREDENTIAL_PATH,
        "mirror_live": mirror_live,
    }
    report.update(select_rate(ladder_results))
    with (out / "pilot_report.json").open("w") as fh:
        json.dump(report, fh, indent=1, sort_keys=True, default=str)
    return report


def _cli():                                                   # pragma: no cover
    import argparse
    import httpx
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--markets", type=int, default=6)
    ap.add_argument("--requests", type=int, default=REQUESTS_PER_RATE)
    a = ap.parse_args()
    u = json.loads(Path(a.universe).read_text())
    slugs = [m["slug"] for m in u["MARKETS"]][:a.markets]
    with httpx.Client(headers={"accept": "application/json"}) as http:
        rep = pilot(a.out, slugs, http, requests=a.requests)
    print(json.dumps({k: str(v) for k, v in rep.items()
                      if k != "PER_RATE"}, indent=1, sort_keys=True))
    for r in rep["PER_RATE"]:
        print(" RATE", r["RATE_RPS"], "ok", r["SUCCESSFUL_REQUESTS"],
              "429", r["HTTP_429_COUNT"], "share", str(r["HTTP_429_SHARE"]),
              "passes", r["PASSES"])


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
