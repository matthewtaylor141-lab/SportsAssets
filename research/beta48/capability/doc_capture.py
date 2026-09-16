#!/usr/bin/env python3
"""Capture Polymarket US PUBLIC DOCUMENTATION pages. READ ONLY. NO CREDENTIAL.

SEPARATE FROM THE FORWARD COLLECTOR, ON PURPOSE. `fwd_collect.py` and the
§10 capture are frozen and are not touched by this file. This is its own
script, its own workflow, its own output tree and its own branch namespace, so
nothing here can perturb a running segment or alter a captured dataset.

WHAT IT DOES. Fetches public documentation pages over HTTPS GET and stores each
one verbatim with a sha256. That is the only way to answer "determine from
PRIMARY OFFICIAL DOCUMENTATION" -- without it, the institutional scope list is
a quotation of a quotation.

WHAT IT CANNOT DO. It reaches docs.polymarket.us and nothing else: a literal
host allow-list is checked on every request, and a URL naming any other host
raises. There is no credential, no Auth0 exchange, no signature, no WebSocket,
no gRPC, no FIX, no market-data subscription and no order path -- and no code
here could construct one. `test_doc_capture.py` proves that by AST scan before
the workflow is allowed to fetch anything.

THE INDEX FIRST. `llms.txt` is the venue's own machine-readable page index, so
the page list is READ rather than guessed. A guessed URL that 404s teaches
nothing; the index tells us which pages exist, which is itself part of the
answer (if no FIX market-data page is listed, that is a finding).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

CAPTURE_VERSION = "beta48-capability-docs/1"

# THE ONLY HOST THIS FILE MAY REACH. A literal, checked per request.
DOCS_HOST = "docs.polymarket.us"
DOCS_BASE = "https://" + DOCS_HOST
INDEX_PATH = "/llms.txt"

# OUR restraint, not a measured venue ceiling.
MAX_RPS = 2.0
MIN_SPACING_S = 1.0 / MAX_RPS
TIMEOUT_S = 20.0
MAX_PAGES = 120

# The pages the capability questions turn on. Seeded so they are fetched even
# if the index omits them; anything the index adds is fetched too.
SEED_PATHS = (
    "/trader-guide/authentication",      # THE SCOPE PAGE
    "/trader-guide/overview",
    "/data-guide/overview",
    "/api-reference/introduction",
    "/api-reference/websocket/overview",
    "/market-structure/collateral-and-margin",
    "/market-structure/mutually-exclusive-collateral-return",
    "/partners/overview",
    "/incentives/overview",
)

# Which captured pages a later reader should search for each question. Recorded
# so the analysis step cannot quietly widen its evidence base.
QUESTION_PAGES = {
    "SCOPES": ("/trader-guide/authentication",),
    "MARKET_DATA_STREAM": ("/data-guide/overview", "/trader-guide/overview"),
    "FIX": ("/data-guide/overview",),
    "COLLATERAL": ("/market-structure/collateral-and-margin",
                   "/market-structure/mutually-exclusive-collateral-return"),
}


def _assert_docs_host(url: str) -> str:
    """Refuse any URL that is not the documentation host. Checked per call."""
    host = (urlsplit(url).hostname or "").lower()
    if host != DOCS_HOST:
        raise RuntimeError("doc_capture: refusing %r: not %s" % (host,
                                                                 DOCS_HOST))
    if urlsplit(url).scheme != "https":
        raise RuntimeError("doc_capture: refusing a non-https URL")
    return url


def _now():
    return datetime.now(tz=timezone.utc).isoformat()


class Pacer:
    def __init__(self):
        self._last = None
        self.requests = 0

    def wait(self):
        if self._last is not None:
            slack = (self._last + MIN_SPACING_S) - time.monotonic()
            if slack > 0:
                time.sleep(slack)
        self._last = time.monotonic()
        self.requests += 1


def fetch(http, pacer, path: str) -> dict:
    """One GET. A failure is recorded AS a row, never left absent."""
    url = _assert_docs_host(DOCS_BASE + path)
    row = {"url": url, "path": path, "requested_at_utc": _now(),
           "http_status": None, "error": None, "text": None,
           "sha256": None, "bytes": None, "capture_version": CAPTURE_VERSION}
    try:
        pacer.wait()
        r = http.get(url, timeout=TIMEOUT_S,
                     headers={"User-Agent": CAPTURE_VERSION})
        row["http_status"] = r.status_code
        raw = r.content
        row["bytes"] = len(raw)
        row["sha256"] = hashlib.sha256(raw).hexdigest()
        if r.status_code == 200:
            row["text"] = r.text
        else:
            row["error"] = "http_%d" % r.status_code
    except httpx.HTTPError as exc:
        row["error"] = type(exc).__name__
    row["fetched_at_utc"] = _now()
    return row


def paths_from_index(text: str) -> list:
    """Every same-host page the venue's own index names.

    Absolute links to the docs host and bare paths are both accepted; anything
    pointing elsewhere is dropped by the host check at fetch time anyway.
    """
    found = set()
    for m in re.findall(r"https://" + re.escape(DOCS_HOST) + r"(/[^\s)\"'<>]*)",
                        text):
        found.add(m.rstrip(".,);"))
    for m in re.findall(r"\]\((/[a-z0-9\-/._]+)\)", text):
        found.add(m)
    return sorted(p for p in found
                  if not p.endswith((".png", ".jpg", ".svg", ".ico")))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES)
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pacer = Pacer()
    rows = []

    with httpx.Client(follow_redirects=True) as http:
        index = fetch(http, pacer, INDEX_PATH)
        rows.append(index)

        discovered = (paths_from_index(index["text"])
                      if index.get("text") else [])
        todo, seen = [], set()
        for p in list(SEED_PATHS) + discovered:
            if p not in seen:
                seen.add(p)
                todo.append(p)
        todo = todo[:max(1, int(a.max_pages))]

        for p in todo:
            rows.append(fetch(http, pacer, p))

    ok = [r for r in rows if r["http_status"] == 200]
    summary = {
        "capture_version": CAPTURE_VERSION,
        "captured_at_utc": _now(),
        "DOCS_HOST": DOCS_HOST,
        "INDEX_REACHABLE": "YES" if index["http_status"] == 200 else "NO",
        "INDEX_PATHS_DISCOVERED": len(discovered),
        "pages_requested": len(rows),
        "pages_200": len(ok),
        "requests_used": pacer.requests,
        "seed_paths": list(SEED_PATHS),
        "question_pages": QUESTION_PAGES,
        "sha256": {r["path"]: r["sha256"] for r in rows},
        "statuses": {r["path"]: r["http_status"] for r in rows},
        # Nothing here parses a scope, a formula or a capability. The analysis
        # is a separate step reading these stored bytes, so the capture cannot
        # drift into the conclusion.
        "PARSED_HERE": "NOTHING",
        "CREDENTIAL_USED": "NONE",
        "CONNECTIONS_OPENED": "HTTPS_GET_TO_DOCS_HOST_ONLY",
    }

    with (out / "docs_raw.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    (out / "docs_index.json").write_text(json.dumps(summary, indent=1))
    print("index %s | discovered %d | requested %d | 200s %d | requests %d"
          % (summary["INDEX_REACHABLE"], len(discovered), len(rows), len(ok),
             pacer.requests))
    for r in rows:
        if r["http_status"] != 200:
            print("  MISS %-58s %s" % (r["path"], r["http_status"]
                                       or r["error"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
