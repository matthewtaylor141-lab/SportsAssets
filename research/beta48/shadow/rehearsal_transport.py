#!/usr/bin/env python3
"""OFFLINE MOCK TRANSPORT for the rehearsal. Test-only; nothing imports it.

WHAT IS MOCKED AND WHAT IS NOT
------------------------------
Mocked: the TRANSPORT. `httpx.Client` is replaced so no socket is opened and
no venue is contacted. Requests are answered from retained fixtures.

NOT mocked: every decision. The real `substantive_select._cli` runs, the real
`events_adapter` flattens, the real `event_identity` binds, the real
`eligibility.decision_screen` screens, the real ranking ranks, the real
`freeze` freezes and the real serializer writes the file. The rehearsal never
supplies an eligibility or activity verdict -- it supplies bodies and lets the
frozen code decide.

Install by importing this module and calling `install(...)` BEFORE the CLI
runs. `substantive_select._cli` imports httpx inside the function, so patching
the module attribute here is seen by it.
"""
from __future__ import annotations

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

EVENTS_FIXTURE = os.path.join(HERE, "fixtures_events_block3.json")
BOOKS_FIXTURE = os.path.join(HERE, "fixtures_books_block4.json")

PROV_RETAINED = "RETAINED"
PROV_SYNTHETIC = "SYNTHETIC_DERIVED_FROM_RETAINED_SHAPE"


def _px(v):
    return {"px": {"value": "%.4f" % v, "currency": "USD"}, "qty": "500.0000"}


def synthetic_book(slug, as_of_iso, bid=0.4700, ask=0.4800):
    """A two-sided book in the VENUE'S OWN SHAPE.

    marketData.bids / marketData.offers -- not top-level bids/asks. Getting
    this wrong is precisely the defect the rehearsal exists to expose, so the
    fixture copies the retained shape rather than the shape the selection code
    happens to expect.
    """
    return {"marketData": {
        "marketSlug": slug,
        "bids": [_px(bid), _px(bid - 0.01)],
        "offers": [_px(ask), _px(ask + 0.01)],
        "state": "MARKET_STATE_OPEN",
        "transactTime": as_of_iso,
        "stats": {"lastTradeSetTime": as_of_iso,
                  "lastTradePx": {"value": "%.4f" % bid, "currency": "USD"}},
    }}


class _Resp(object):
    """Enough of an httpx.Response for the code under test, including the
    raw bytes the retention path must be able to take before parsing."""

    def __init__(self, body, status=200, raw=None):
        self.status_code = status
        self._body = body
        self.content = (raw if raw is not None
                        else json.dumps(body).encode("utf-8"))

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.content.decode("utf-8"))


class MockClient(object):
    """Stands in for httpx.Client. Records every request it answers."""

    def __init__(self, pages, books, as_of_iso, log, book_status=None,
                 **_kw):
        self._pages = pages
        self._books = books
        self._as_of = as_of_iso
        self._log = log
        self._book_status = book_status or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None, timeout=None):
        self._log.append({"url": url, "params": dict(params or {})})
        m = re.search(r"/v1/markets/(?P<slug>[^/]+)/book$", url)
        if m:
            slug = m.group("slug")
            st = self._book_status.get(slug, 200)
            if st != 200:
                return _Resp({}, status=st, raw=b'{"error":"mocked"}')
            body, prov = self._books(slug, self._as_of)
            return _Resp(body)
        if "/v1/events" in url:
            off = int((params or {}).get("offset", 0))
            lim = int((params or {}).get("limit", 100)) or 100
            i = off // lim
            page = self._pages[i] if i < len(self._pages) else {"events": []}
            if isinstance(page, tuple):                  # (status, body)
                return _Resp(page[1], status=page[0],
                             raw=json.dumps(page[1]).encode())
            return _Resp(page)
        raise AssertionError("UNMOCKED_URL: %s" % url)


def load_fixtures():
    with open(EVENTS_FIXTURE) as fh:
        ev = json.load(fh)
    with open(BOOKS_FIXTURE) as fh:
        bk = json.load(fh)
    return ev, bk


def book_server(books_fixture, provenance_log=None):
    """slug -> (body, provenance). Retained where one exists, else synthetic."""
    retained = books_fixture["RETAINED"]["BODIES"]

    def serve(slug, as_of_iso):
        if slug in retained:
            prov = PROV_RETAINED
            body = retained[slug]
        else:
            prov = PROV_SYNTHETIC
            body = synthetic_book(slug, as_of_iso)
        if provenance_log is not None:
            provenance_log[slug] = prov
        return body, prov
    return serve


def install(pages, as_of_iso, request_log, provenance_log=None,
            book_status=None):
    """Patch httpx.Client for the duration of the process. Returns nothing."""
    import httpx
    _, bk = load_fixtures()
    serve = book_server(bk, provenance_log)

    def factory(*a, **kw):
        return MockClient(pages, serve, as_of_iso, request_log,
                          book_status=book_status)
    httpx.Client = factory
    return factory
