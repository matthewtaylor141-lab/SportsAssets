#!/usr/bin/env python3
"""PUBLIC EXECUTION TAPE — read-only capture. NO CREDENTIAL. SEPARATE PIPELINE.

It does not import, touch or share a process with fwd_collect.py. Different
tree, different workflow, different output. The §10 collector stays frozen.

WHAT THIS REACHES. Two public landing pages on www.polymarketexchange.com,
named on our own captured documentation page (docs.polymarket.us/faqs/
execution-tape.md, sha256 5ff446a6abd4fbca...), plus whatever CSV files those
pages LINK TO on the same host. Nothing else.

WHY LINK-FOLLOWING AND NOT A CONSTRUCTED URL. The docs give a FILE NAMING
convention -- `YYYYMMDD-time-and-sales.csv` -- and never give a download URL.
Turning a filename convention into a URL means guessing a path on a host we
have never fetched, which is precisely the kind of invention this programme
exists to avoid: a guessed 404 is indistinguishable from "the venue does not
publish this", and a guessed 200 on the wrong path is worse. So the page is
fetched, its own links are read, and only those are followed. If the page links
nothing this file reports NO_LINKS_FOUND rather than manufacturing candidates.

WHAT IT DOES NOT DO. No API key, no Auth0 token, no client assertion, no
WebSocket, no gRPC, no FIX, no order path, no POST/PUT/PATCH/DELETE. The AST
gate in test_tape_capture.py proves the file contains no other host, no
credential vocabulary, no environment read and no mutating verb.

THE BUSINESS-DATE TRAP, recorded here because it is the C-6 error in a new
place. The venue's reporting day is "as of 5:00 PM Eastern Time each business
day" (captured: learn/trading/access-and-limits/trading-hours.md). A file named
20260113 therefore does NOT cover 2026-01-13 00:00-24:00 UTC. Joining a 5pm-ET
business date to UTC book snapshots as though they were the same day misaligns
the entire tape by up to seven hours -- silently, and in a direction that looks
like ordinary noise. This file records the filename and the fetch, and computes
NO date arithmetic at all; the alignment is a separate, tested step.

Usage:
    python3 tape_capture.py --out DIR [--max-files N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

CAPTURE_VERSION = "beta48-tape-public/1"

# The one host, and the two pages the captured documentation links by name.
TAPE_HOST = "www.polymarketexchange.com"
SCHEME = "https://"
TAPE_BASE = SCHEME + TAPE_HOST
LANDING_PAGES = ("/time-and-sales.html", "/daily-market-report.html")

# OUR restraint, not evidence about the venue's ceiling.
MAX_RPS = 1.0
MIN_SPACING_S = 1.0 / MAX_RPS
TIMEOUT_S = 30.0
MAX_BYTES = 64 * 1024 * 1024

# What the captured page says the tape contains, carried so the parser can be
# checked against the documentation rather than against its own expectations.
DOCUMENTED_TAPE_COLUMNS = ("Transaction Time", "Symbol", "Last Price",
                           "Last Quantity")
DOCUMENTED_TAPE_FILENAME = r"^\d{8}-time-and-sales\.csv$"
DOCUMENTED_REPORT_FILENAME = r"^\d{8}-daily-market-report\.csv$"


def _assert_tape_host(url: str) -> str:
    """The only reachable host, checked on every single request."""
    p = urlparse(url)
    if p.scheme != "https" or p.hostname != TAPE_HOST:
        raise RuntimeError("refusing non-tape host: %r" % (url,))
    return url


class Pacer:
    def __init__(self):
        self.spacing = MIN_SPACING_S
        self._last = -1e9

    def wait(self):
        gap = self.spacing - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()


def _now():
    return datetime.now(tz=timezone.utc).isoformat()


def fetch(client, pacer, url, binary=False):
    """One GET, recorded as a row whether or not it worked.

    An absent row and a failed fetch look identical afterwards, and only one of
    them is honest.
    """
    _assert_tape_host(url)
    pacer.wait()
    row = {"url": url, "requested_at_utc": _now(),
           "capture_version": CAPTURE_VERSION,
           "http_status": None, "error": None, "bytes": None, "sha256": None}
    try:
        r = client.get(url, timeout=TIMEOUT_S, follow_redirects=False)
        row["http_status"] = r.status_code
        body = r.content[:MAX_BYTES]
        row["bytes"] = len(body)
        row["sha256"] = hashlib.sha256(body).hexdigest()
        row["truncated"] = len(r.content) > MAX_BYTES
        row["content_type"] = r.headers.get("content-type")
        if not binary:
            row["text"] = body.decode("utf-8", "replace")
        else:
            row["body"] = body
    except Exception as exc:                       # recorded, never swallowed
        row["error"] = type(exc).__name__
    return row


_HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)


def csv_links(page_text, page_url):
    """CSV links the page itself offers, on the same host. Nothing invented."""
    out = []
    for raw in _HREF.findall(page_text or ""):
        url = urljoin(page_url, raw)
        p = urlparse(url)
        if p.scheme == "https" and p.hostname == TAPE_HOST \
                and p.path.lower().endswith(".csv"):
            if url not in out:
                out.append(url)
    return out


def parse_header(csv_text):
    """The first line's columns, verbatim, with no renaming or reordering.

    Returns the observed header AND whether it matches the documented one. A
    mismatch is reported, never corrected: if the venue's real file differs
    from its own documentation, that is the finding.
    """
    first = (csv_text or "").splitlines()[0] if csv_text else ""
    cols = [c.strip().strip('"') for c in first.split(",")] if first else []
    return {
        "OBSERVED_COLUMNS": cols,
        "DOCUMENTED_COLUMNS": list(DOCUMENTED_TAPE_COLUMNS),
        "HEADER_MATCHES_DOCUMENTATION":
            "YES" if cols == list(DOCUMENTED_TAPE_COLUMNS) else "NO",
    }


def capture(outdir: Path, max_files: int = 0) -> dict:
    """Fetch the landing pages, and at most `max_files` of the CSVs they link."""
    outdir.mkdir(parents=True, exist_ok=True)
    pacer = Pacer()
    raw = outdir / "tape_raw.jsonl"
    summary = {
        "capture_version": CAPTURE_VERSION,
        "captured_at_utc": _now(),
        "PUBLIC_TIME_SALES_AVAILABLE": "NOT_IDENTIFIED",
        "AUTH_REQUIRED": "NO",
        "landing_pages": [],
        "csv_links_found": [],
        "files_fetched": [],
        "HEADER_MATCHES_DOCUMENTATION": "NOT_IDENTIFIED",
        "TAPE_TIMESTAMP_PRECISION": "NOT_IDENTIFIED",
        "PUBLICATION_LATENCY": "NOT_IDENTIFIED",
        "SYMBOL_JOINS_TO_MARKET_SLUG": "NOT_IDENTIFIED",
        "BUSINESS_DATE_CUTOVER": "17:00 America/New_York (captured)",
        "BUSINESS_DATE_IS_NOT_A_UTC_DAY": "YES",
    }
    links = []
    with httpx.Client(headers={"User-Agent": CAPTURE_VERSION}) as client, \
            raw.open("a") as fh:
        for path in LANDING_PAGES:
            url = TAPE_BASE + path
            row = fetch(client, pacer, url)
            fh.write(json.dumps({"kind": "LANDING", **row}) + "\n")
            summary["landing_pages"].append(
                {"url": url, "http_status": row["http_status"],
                 "error": row["error"], "sha256": row["sha256"]})
            if row["http_status"] == 200:
                summary["PUBLIC_TIME_SALES_AVAILABLE"] = "YES"
                for u in csv_links(row.get("text"), url):
                    if u not in links:
                        links.append(u)
        summary["csv_links_found"] = links
        if not links:
            summary["CSV_LINK_DISCOVERY"] = "NO_LINKS_FOUND"
        for url in links[:int(max_files)]:
            row = fetch(client, pacer, url)
            hdr = parse_header(row.get("text"))
            fh.write(json.dumps({"kind": "CSV", **row, **hdr}) + "\n")
            summary["files_fetched"].append(
                {"url": url, "http_status": row["http_status"],
                 "bytes": row["bytes"], "sha256": row["sha256"],
                 **hdr})
            if summary["HEADER_MATCHES_DOCUMENTATION"] == "NOT_IDENTIFIED":
                summary["HEADER_MATCHES_DOCUMENTATION"] = \
                    hdr["HEADER_MATCHES_DOCUMENTATION"]

    (outdir / "tape_summary.json").write_text(json.dumps(summary, indent=1))
    print("tape landing %d | csv links %d | fetched %d"
          % (len(summary["landing_pages"]), len(links),
             len(summary["files_fetched"])))
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-files", type=int, default=0,
                    help="CSV files to download; 0 discovers links only")
    a = ap.parse_args(argv)
    capture(Path(a.out), a.max_files)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
