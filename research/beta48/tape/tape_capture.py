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
# The third page is the block publication. Its path is NOT documented in any
# of our 340 captured pages, so it is a CANDIDATE: fetched, and recorded as
# absent if it 404s. A 404 here is a real finding -- it means row-level block
# exclusion is unavailable and every symbol-day carrying block volume stays
# blocked for queue inference.
LANDING_PAGES = ("/time-and-sales.html", "/daily-market-report.html",
                 "/block-trade-data.html")
BLOCK_TRADE_PAGE = "/block-trade-data.html"

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
# The landing pages turned out to be JavaScript shells: 0 anchors, 0
# .csv strings, one page-specific script each. An href scan over them
# finds nothing, which looks identical to "the venue publishes
# nothing" and is not the same finding. So the page's OWN script
# reference is followed one level, and the strings that script names
# are reported. Still no URL is constructed from a convention.
_SRC = re.compile(r"""src\s*=\s*["']([^"']+)["']""", re.I)
# String literals inside a script that look like a path or a URL.
_JS_PATHISH = re.compile(r"""["'](/[A-Za-z0-9_./-]{2,}|https?://[^"']{4,})["']""")


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


def script_links(page_text, page_url):
    """Same-host script assets the page itself references."""
    out = []
    for raw in _SRC.findall(page_text or ""):
        url = urljoin(page_url, raw)
        p = urlparse(url)
        if p.scheme == "https" and p.hostname == TAPE_HOST \
                and p.path.lower().endswith(".js"):
            if url not in out:
                out.append(url)
    return out


def script_pathish(script_text):
    """Path-shaped literals a script names. Reported, never fetched
    blindly: a candidate this produces is still the SCRIPT's word, and
    a caller decides whether to follow it."""
    out = []
    for m in _JS_PATHISH.findall(script_text or ""):
        if m not in out:
            out.append(m)
    return out


def manifest_csvs(text, manifest_url):
    """CSV entries a manifest lists, whatever shape it takes.

    A manifest may be a list of names, a list of objects, or an object
    with a list inside. Rather than assume one, every string anywhere
    in the parsed JSON is examined and the ones ending .csv are
    resolved against the manifest's own URL. Shape-agnostic on purpose:
    a parser that knew the shape would break silently when the venue
    changed it, and report NO FILES rather than an unreadable index.
    """
    try:
        doc = json.loads(text or "")
    except ValueError:
        return []
    found, seen = [], set()

    def walk(node):
        if isinstance(node, str):
            if node.lower().endswith(".csv") and node not in seen:
                seen.add(node)
                u = urljoin(manifest_url, node)
                p = urlparse(u)
                if p.scheme == "https" and p.hostname == TAPE_HOST:
                    found.append(u)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc)
    return found


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
        # C-14. Tape volume is an UPPER BOUND on CLOB volume until blocks can
        # be identified row by row, because a block executes apart from the
        # book and depletes no queue.
        "BLOCK_TRADE_DATA_PUBLIC": "NOT_IDENTIFIED",
        "BLOCK_TRADE_PAGE_HTTP_STATUS": None,
        "ROW_LEVEL_MATCH_TO_TIME_SALES_POSSIBLE": "NOT_IDENTIFIED",
        "TAPE_VOLUME_IS_UPPER_BOUND_ON_CLOB_VOLUME": "YES",
        # Publication times differ, so the join key is the BUSINESS DATE and
        # never a fetch or discovery timestamp.
        "TAPE_PUBLICATION_TIME_ET_RELAYED": "~18:00",
        "DMR_PUBLICATION_TIME_ET_RELAYED": "~00:00",
        "JOIN_KEY": "BUSINESS_DATE",
        "JOIN_KEY_IS_NOT": ("UTC_CALENDAR_DATE", "FILE_DISCOVERY_TIMESTAMP",
                            "FETCH_TIMESTAMP"),
    }
    links = []
    script_sources = []
    with httpx.Client(headers={"User-Agent": CAPTURE_VERSION}) as client, \
            raw.open("a") as fh:
        for path in LANDING_PAGES:
            url = TAPE_BASE + path
            row = fetch(client, pacer, url)
            fh.write(json.dumps({"kind": "LANDING", **row}) + "\n")
            summary["landing_pages"].append(
                {"url": url, "http_status": row["http_status"],
                 "error": row["error"], "sha256": row["sha256"]})
            if path == BLOCK_TRADE_PAGE:
                summary["BLOCK_TRADE_PAGE_HTTP_STATUS"] = row["http_status"]
                summary["BLOCK_TRADE_DATA_PUBLIC"] = (
                    "YES" if row["http_status"] == 200 else
                    "NO" if row["http_status"] == 404 else "NOT_IDENTIFIED")
            elif row["http_status"] == 200:
                summary["PUBLIC_TIME_SALES_AVAILABLE"] = "YES"
                for u in csv_links(row.get("text"), url):
                    if u not in links:
                        links.append(u)
                for sj in script_links(row.get("text"), url):
                    if sj not in script_sources:
                        script_sources.append(sj)
        summary["csv_links_found"] = links
        if not links:
            summary["CSV_LINK_DISCOVERY"] = "NO_LINKS_FOUND"
            # The pages are script shells. Follow the script each page
            # names, and report the paths IT names, so the difference
            # between "venue publishes nothing" and "our reader reads
            # the wrong layer" is settled by evidence.
            scripts = []
            for url in script_sources:
                row = fetch(client, pacer, url)
                fh.write(json.dumps({"kind": "SCRIPT", **row}) + "\n")
                named = script_pathish(row.get("text"))
                scripts.append({"url": url,
                                "http_status": row["http_status"],
                                "bytes": row["bytes"],
                                "pathsNamed": named[:80]})
                for cand in named:
                    cu = urljoin(url, cand)
                    pu = urlparse(cu)
                    if (pu.scheme == "https" and pu.hostname == TAPE_HOST
                            and pu.path.lower().endswith(".csv")
                            and cu not in links):
                        links.append(cu)
            summary["script_assets"] = scripts
            # THE SCRIPTS NAME A MANIFEST, NOT A FILE. Production:
            # daily-market-report.js names
            # /files/daily-market-report/manifest.json. A manifest is
            # the venue's own INDEX of what it publishes, so following
            # it is still reading what the venue points at -- one more
            # level of the same discipline, and not a constructed URL.
            manifests = []
            for sc in scripts:
                for cand in sc.get("pathsNamed") or ():
                    mu = urljoin(sc["url"], cand)
                    pu = urlparse(mu)
                    if (pu.scheme == "https" and pu.hostname == TAPE_HOST
                            and pu.path.lower().endswith(".json")
                            and mu not in [m["url"] for m in manifests]):
                        row = fetch(client, pacer, mu)
                        fh.write(json.dumps(
                            {"kind": "MANIFEST", **row}) + "\n")
                        named = manifest_csvs(row.get("text"), mu)
                        manifests.append({
                            "url": mu, "http_status": row["http_status"],
                            "bytes": row["bytes"],
                            "csvEntriesFound": len(named),
                            "csvSample": named[:10],
                        })
                        for cu in named:
                            if cu not in links:
                                links.append(cu)
            summary["manifests"] = manifests
            summary["csv_links_found"] = links
            if links:
                summary["CSV_LINK_DISCOVERY"] = (
                    "FOUND_VIA_PAGE_SCRIPT_MANIFEST" if manifests
                    else "FOUND_VIA_PAGE_SCRIPT")
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
