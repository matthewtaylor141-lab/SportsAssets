"""E26 (FILL lane 26, 2026-09-09): the honest page.

"POLLER page full (100)" fired on every poll of every whale with 100
lifetime trades -- 22 lines in the 7 s of hard2/wlog_6m_1734.txt (RN1
at 17:34:33.822 and 17:34:37.182, rows 1037 / 1082: every ~3.4 s) --
because the page is his newest 100 whatever their age. It meant
nothing, and the one page that CAN hide an older new row (a full page
whose every key is new) was never followed. Now: a full all-new page is
followed with the same params plus `offset` (the reconciler's own
request shape: limit 100, offset N, takerOnly false), at most
POLL_OVERFLOW_PAGES more, stopping at the first page holding a seen key
or fewer than 100 rows; the WARNING moves to "POLLER page overflow ..."
and fires ONLY when the bound was reached all-new; `_PAGE_OVERFLOW` /
`page_overflow_counts()` count it beside `_PAGE_FULL` /
`page_full_counts()`, which stay counters and stop logging. The fakes
are test_poller_poll_wallet.py's (`_FakePool`, `fake_polite_get`).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import pathlib
import re

import pytest

from sportsassets.ingestion import poller as P
from sportsassets.ingestion.poller import Poller
from tests.test_poller_poll_wallet import RAW_TRADE, _FakePool

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOGGER = "sportsassets.ingestion.poller"
WHALE = {"id": 1, "address": "0x2005d16a84ceefa912d4e380cd32e7ff827875ea", "username": "RN1"}


def _rows(offset: int, n: int = 100) -> list[dict]:
    """n distinct venue rows: one tx per row, newest first from `offset`."""
    return [dict(RAW_TRADE, transactionHash="0x" + f"{offset + i:064x}",
                 timestamp=RAW_TRADE["timestamp"] - offset - i) for i in range(n)]


class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class _Venue:
    """/trades by offset. `pages[offset]` is a list, an Exception to raise,
    or any non-list body to serve."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.requests: list[dict] = []

    async def get(self, http, path, params=None):
        assert path == "/trades"
        self.requests.append(dict(params or {}))
        body = self.pages.get(int((params or {}).get("offset", 0)), [])
        if isinstance(body, Exception):
            raise body
        return _Resp(body)


def _key(row: dict) -> str:
    """The row's real dedupe key (dedupe.py: tx, asset, side, size, price, ts)."""
    return P.parse_data_api_trade(row, WHALE["id"], WHALE["username"]).dedupe_key


class _SeenPool(_FakePool):
    """The batch pre-probe answers with the keys of the rows it is told
    are known -- the real dedupe keys, as the trades table holds them."""

    def __init__(self, known_rows=()):
        super().__init__()
        self.known = {_key(r) for r in known_rows}

    async def fetch(self, sql, *args):
        keys = list(args[0]) if args else []
        self.any_queries.append(keys)
        return [{"dedupe_key": k} for k in keys if k in self.known]


@pytest.fixture()
def world(monkeypatch):
    P._PAGE_OVERFLOW.clear()
    P._PAGE_FULL.clear()
    ingested: list = []
    state = {"pool": _SeenPool(), "venue": _Venue({})}

    async def fake_get_pool():
        return state["pool"]

    async def fake_polite_get(http, path, params=None):
        return await state["venue"].get(http, path, params)

    async def fake_ingest(ev):
        ingested.append(ev)
        return len(ingested), True

    monkeypatch.setattr(P, "get_pool", fake_get_pool)
    monkeypatch.setattr("sportsassets.ratelimit.polite_get", fake_polite_get)
    monkeypatch.setattr(P, "ingest_trade_result", fake_ingest)
    monkeypatch.setattr(P, "POLL_OVERFLOW_PAGES", 2.0)
    yield state, ingested
    P._PAGE_OVERFLOW.clear()
    P._PAGE_FULL.clear()


def _poll(whale=WHALE):
    p = Poller.__new__(Poller)
    p._http = None
    p.last_lag_s = None
    return asyncio.run(p.poll_wallet(whale))


def _params(user, offset=None):
    d = {"user": user, "limit": 100, "takerOnly": "false"}
    if offset is not None:
        d["offset"] = offset
    return d


def _tx_of(row):
    return row["transactionHash"]


# ── the walk ──────────────────────────────────────────────────────────

def test_a_full_all_new_page_is_followed_to_a_seen_row(world, caplog):
    """100 all-new keys -> the second request is {user, limit 100, offset
    100, takerOnly false}; the second page holds one seen key -> the walk
    stops there, its new rows ingest, no overflow, no warning."""
    state, ingested = world
    p1, p2 = _rows(0), _rows(100)
    state["venue"] = _Venue({0: p1, 100: p2, 200: _rows(200)})
    state["pool"] = _SeenPool(known_rows=[p2[40]])
    caplog.set_level(logging.WARNING, logger=LOGGER)
    new = _poll()
    assert state["venue"].requests == [_params(WHALE["address"]), _params(WHALE["address"], 100)]
    assert new == 199 and len(ingested) == 199
    assert _tx_of(p2[40]) not in {e.tx_hash for e in ingested}, "the seen row is not re-ingested"
    assert P.page_overflow_counts() == {} and P.page_full_counts() == {"RN1": 1}
    assert "overflow" not in caplog.text and "page full" not in caplog.text


def test_one_seen_key_on_the_first_page_makes_no_second_request(world, caplog):
    state, ingested = world
    p1 = _rows(0)
    state["venue"] = _Venue({0: p1, 100: _rows(100)})
    state["pool"] = _SeenPool(known_rows=[p1[99]])
    caplog.set_level(logging.WARNING, logger=LOGGER)
    new = _poll()
    assert state["venue"].requests == [_params(WHALE["address"])]
    assert new == 99 and P.page_overflow_counts() == {}
    assert caplog.text == ""


def test_a_short_page_is_not_followed(world):
    state, ingested = world
    state["venue"] = _Venue({0: _rows(0, 99), 100: _rows(100)})
    assert _poll() == 99
    assert len(state["venue"].requests) == 1 and P.page_full_counts() == {}


def test_a_short_second_page_ends_the_walk_without_overflow(world, caplog):
    state, ingested = world
    state["venue"] = _Venue({0: _rows(0), 100: _rows(100, 50), 200: _rows(200)})
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert _poll() == 150
    assert len(state["venue"].requests) == 2 and P.page_overflow_counts() == {}
    assert caplog.text == ""


def test_the_bound_reached_all_new_counts_overflow_and_says_so(world, caplog):
    """Three all-new pages (the bound 2): page_overflow 1 and the one
    line that means the venue may hold older rows unread."""
    state, ingested = world
    state["venue"] = _Venue({0: _rows(0), 100: _rows(100), 200: _rows(200), 300: _rows(300)})
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert _poll() == 300
    assert [r.get("offset") for r in state["venue"].requests] == [None, 100, 200]
    assert P.page_overflow_counts() == {"RN1": 1}
    assert ("POLLER page overflow for RN1: 3 pages, no row seen -- the venue may hold "
            "older rows this poll did not reach") in caplog.text
    assert caplog.text.count("overflow") == 1


def test_the_walk_never_reads_past_the_bound(world, monkeypatch):
    state, ingested = world
    state["venue"] = _Venue({o: _rows(o) for o in range(0, 1100, 100)})
    monkeypatch.setattr(P, "POLL_OVERFLOW_PAGES", 1.0)
    assert _poll() == 200 and len(state["venue"].requests) == 2
    P._PAGE_OVERFLOW.clear()
    ingested.clear()
    monkeypatch.setattr(P, "POLL_OVERFLOW_PAGES", 2.0)
    state["venue"] = _Venue({o: _rows(o) for o in range(0, 1100, 100)})
    assert _poll() == 300 and len(state["venue"].requests) == 3
    assert P.page_overflow_counts() == {"RN1": 1}


def test_at_zero_pages_the_one_page_is_today_with_the_count(world, caplog, monkeypatch):
    state, ingested = world
    monkeypatch.setattr(P, "POLL_OVERFLOW_PAGES", 0.0)
    state["venue"] = _Venue({0: _rows(0), 100: _rows(100)})
    caplog.set_level(logging.WARNING, logger=LOGGER)
    assert _poll() == 100
    assert len(state["venue"].requests) == 1
    assert P.page_overflow_counts() == {"RN1": 1}
    assert "POLLER page overflow for RN1: 1 pages, no row seen" in caplog.text


# ── fail closed ───────────────────────────────────────────────────────

def test_a_raising_second_page_leaves_the_first_pages_rows_and_counts(world, caplog):
    state, ingested = world
    state["venue"] = _Venue({0: _rows(0), 100: RuntimeError("502 upstream")})
    caplog.set_level(logging.WARNING, logger=LOGGER)
    new = _poll()                                   # no raise out of the cycle
    assert new == 100 and len(ingested) == 100
    assert P.page_overflow_counts() == {"RN1": 1}
    assert "POLLER overflow page at offset 100 for RN1 unreadable (RuntimeError: 502 upstream)" in caplog.text
    assert "the first page's rows stand" in caplog.text
    assert "no row seen" not in caplog.text, "the overflow line is the bound's alone"


def test_a_non_list_second_page_is_the_same(world, caplog):
    for body in ({"error": "upstream"}, "service unavailable", None):
        P._PAGE_OVERFLOW.clear()
        state, ingested = world
        ingested.clear()
        state["venue"] = _Venue({0: _rows(0), 100: body})
        caplog.clear()
        caplog.set_level(logging.WARNING, logger=LOGGER)
        assert _poll() == 100
        assert P.page_overflow_counts() == {"RN1": 1}, body
        assert "unreadable (non-list body:" in caplog.text


def test_junk_rows_on_an_overflow_page_cost_one_row_each(world):
    state, ingested = world
    p2 = _rows(100)
    p2[3] = None
    p2[4] = {"size": "inf"}
    p2[5] = dict(RAW_TRADE, transactionHash="0x" + "ee" * 32, size=float("nan"))
    state["venue"] = _Venue({0: _rows(0), 100: p2, 200: _rows(200, 10)})
    assert _poll() == 100 + 97 + 10


def test_the_first_page_guards_of_rounds_23_24_36_37_are_09b35cds():
    """The first page's text from the def to the pre-probe's `seen =
    set()`, with the page-full block excised (it lost its warning), is
    09b35cd's byte for byte; the ingest loop and the round-24 / 37 guards
    from `new = 0` to the end are 09b35cd's untouched (sha256[:16])."""
    src = inspect.getsource(Poller.poll_wallet)
    cut = "            seen = set()\n"
    head = src[:src.index(cut) + len(cut)]
    a = head.index("        # A FULL PAGE MEANS WE MAY HAVE MISSED")
    b = head.index("        if page and bad == len(page):")
    head = head[:a] + head[b:]
    # review MEDIUM-1's two lines excised the same way: the guards are 09b35cd's
    head = head.replace("        probe_failed = False                # E26 review: an unreadable probe is not an all-new page\n", "")
    head = head.replace("            probe_failed = True\n", "")
    assert hashlib.sha256(head.encode()).hexdigest()[:16] == "2af4512c434873d5"
    tail = src[src.index("        new = 0\n"):]
    assert hashlib.sha256(tail.encode()).hexdigest()[:16] == "919891d91205d0d4"
    for guard in ('raise ValueError(\n                "venue served a non-list /trades body: "',
                  'raise ValueError(\n                f"venue served {bad} rows, none usable")',
                  '"whale with known fills — a cold venue "',
                  'f"all {attempted} attempted rows failed inside ingest")',
                  '"frozen index, not a quiet wallet")'):
        assert guard in src, guard
    # the walk sits after the pre-probe and before the ingest loop, on the first page's shape
    assert src.index(cut) < src.index("int(POLL_OVERFLOW_PAGES)") < src.index("        new = 0\n")
    assert "if len(page) >= 100 and keys and not probe_failed and not any(k in seen for k in keys):" in src


def test_the_overflow_pages_walk_the_first_pages_containment():
    helper = inspect.getsource(Poller._parse_page)
    first = inspect.getsource(Poller.poll_wallet)
    for line in ('ev = parse_data_api_trade(raw, whale["id"], whale["username"])',
                 "if not key_fields_valid(ev):", "bad += 1", "continue", "events.append(ev)"):
        assert line in helper and line in first
    walk = first[first.index("int(POLL_OVERFLOW_PAGES)"):first.index("        new = 0\n")]
    assert "self._parse_page(page2, whale)" in walk
    assert "AND NOT (source = 's1' AND venue_seen_at IS NULL)" in walk, "the same pre-probe"
    assert '"offset": offset' in walk and '"takerOnly": "false"' in walk and '"limit": 100' in walk
    for verb in ("return", "raise ", "continue"):
        assert verb not in walk, f"the walk does {verb}: it must end the cycle's page set, never the cycle"


def test_the_page_full_count_stays_and_stops_logging():
    src = inspect.getsource(Poller.poll_wallet)
    i = src.index("        if len(page) >= 100:")
    block = src[i:src.index("        if page and bad == len(page):")]
    assert "_PAGE_FULL[" in block and "log." not in block
    assert callable(P.page_full_counts) and callable(P.page_overflow_counts)
    assert P.page_full_counts() == {} and P.page_overflow_counts() == {}


def test_the_beat_carries_page_overflow_beside_new():
    src = inspect.getsource(Poller.run)
    i = src.index('"new": new,')
    assert '"page_overflow": _PAGE_OVERFLOW.get(' in src[i:i + 300]


def test_the_rail_is_read_downward_only():
    src = pathlib.Path(P.__file__).read_text()
    assert 'POLL_OVERFLOW_PAGES = _rules.capped_env("POLL_OVERFLOW_PAGES", 2.0, floor=0.0)' in src
    assert src.count("capped_env(") == 1


def test_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    assert re.search(r"^## 68\. E26 -- .* \(2026-09-09, FILL lane 26\)", doc, re.M)
    sec = doc[doc.index("## 68. E26"):]
    for k in ("POLL_OVERFLOW_PAGES", "page_overflow", "page_full", "offset", "takerOnly",
              "22 lines", "3.4 s", "test_e26_poll_overflow.py", "test_poller_page_is_not_silently_truncated"):
        assert k in sec, k
