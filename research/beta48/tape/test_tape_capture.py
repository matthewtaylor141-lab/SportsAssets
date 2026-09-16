#!/usr/bin/env python3
"""The tape capturer's boundary, proved by AST scan before it fetches anything.

The claim is physical: this file can reach the public reports host and nothing
else, and it has no code path that could authenticate, subscribe, stream or
trade even if a credential were handed to it. It also cannot invent a download
URL, which is the failure mode specific to THIS pipeline.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SRC = HERE / "tape_capture.py"
sys.path.insert(0, str(HERE))

import tape_capture as C  # noqa: E402

TREE = ast.parse(SRC.read_text())


def _code_only(tree):
    """Executable code with docstrings stripped.

    A raw substring scan is the wrong test and has bitten this programme
    repeatedly: it fails on the very prose that states the guarantee, and it
    would PASS a file that dropped the promise while keeping the behaviour.
    """
    t = ast.parse(ast.unparse(tree))
    for node in ast.walk(t):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(t))


CODE = _code_only(TREE)


def test_get_only_no_mutating_verb():
    bad = {"post", "put", "patch", "delete", "send", "stream"}
    found = [n.attr for n in ast.walk(TREE)
             if isinstance(n, ast.Attribute) and n.attr in bad]
    assert not found, f"mutating/opaque verbs: {found}"


def test_the_only_reachable_host_is_the_public_reports_host():
    assert C.TAPE_HOST == "www.polymarketexchange.com"
    assert C.TAPE_BASE == "https://www.polymarketexchange.com"

    urlish = {n.value for n in ast.walk(TREE)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)
              and n.value.startswith(("http://", "https://"))}
    assert urlish == {"https://"}, sorted(urlish)

    hosts = {n.value for n in ast.walk(TREE)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and re.fullmatch(r"[a-z0-9.\-]+\.[a-z]{2,}", n.value)}
    assert hosts == {C.TAPE_HOST}, sorted(hosts)


def test_it_cannot_reach_the_collectors_hosts_or_any_trading_host():
    """Separate pipeline means separate reach. It must not be able to touch
    the §10 gateway either, or the two captures could interfere."""
    for banned in ("gateway.polymarket.us", "api.polymarket.us",
                   "docs.polymarket.us", "polymarketexchange.com/v1",
                   "auth0.com", "wss://", "grpc", "fix://"):
        assert banned not in CODE, f"{banned} reachable from the tape capturer"


def test_no_credential_or_signing_vocabulary():
    banned = ["Authorization", "Bearer", "client_assertion", "private_key",
              "PRIVATE_KEY", "secret", "SECRET", "api_key", "x-participant-id",
              "client_credentials", "jwt", "JWT", "Ed25519", "signer"]
    hits = [b for b in banned if b in CODE]
    assert not hits, f"credential vocabulary present: {hits}"


def test_reads_no_environment_at_all():
    assert "os.environ" not in CODE and "getenv" not in CODE
    for n in ast.walk(TREE):
        assert not (isinstance(n, ast.Import)
                    and any(x.name == "os" for x in n.names)), "imports os"


def test_imports_are_stdlib_plus_httpx_only():
    mods = set()
    for n in ast.walk(TREE):
        if isinstance(n, ast.Import):
            mods.update(x.name.split(".")[0] for x in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    allowed = {"argparse", "hashlib", "json", "re", "time", "datetime",
               "pathlib", "urllib", "httpx", "__future__"}
    assert mods <= allowed, f"unexpected imports: {mods - allowed}"


def test_it_does_not_import_the_frozen_collector():
    """The §10 collector is frozen. A separate pipeline that imports it is not
    separate, and could not be reasoned about independently."""
    assert "fwd_collect" not in CODE


def test_the_host_guard_refuses_every_other_host():
    for bad in ("https://gateway.polymarket.us/v1/markets",
                "https://docs.polymarket.us/faqs/execution-tape",
                "https://evil.example.com/www.polymarketexchange.com",
                "https://polymarketexchange.com/time-and-sales.html"):
        with pytest.raises(RuntimeError):
            C._assert_tape_host(bad)


def test_the_host_guard_refuses_plain_http():
    with pytest.raises(RuntimeError):
        C._assert_tape_host("http://www.polymarketexchange.com/x.csv")


def test_the_guard_accepts_the_reports_host():
    u = "https://www.polymarketexchange.com/time-and-sales.html"
    assert C._assert_tape_host(u) == u


def test_the_pacer_cannot_exceed_the_ceiling():
    assert C.MAX_RPS == 1.0
    assert C.MIN_SPACING_S == 1.0


def test_no_csv_url_is_ever_CONSTRUCTED_from_the_filename_convention():
    """THE FAILURE MODE SPECIFIC TO THIS PIPELINE.

    The documentation gives a filename convention (YYYYMMDD-time-and-sales.csv)
    and never gives a download URL. Building one would mean guessing a path on
    a host we have never fetched: a guessed 404 is indistinguishable from "the
    venue does not publish this", and a guessed 200 on the wrong path is worse.

    So there must be no string-formatting of a .csv path anywhere. The only
    source of a CSV URL is a link the page itself served.
    """
    for n in ast.walk(TREE):
        if isinstance(n, ast.JoinedStr):                     # f-string
            src = ast.unparse(n)
            assert ".csv" not in src, f"csv URL built by f-string: {src}"
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod):
            src = ast.unparse(n)
            assert ".csv" not in src, f"csv URL built by %%: {src}"
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "format":
            src = ast.unparse(n)
            assert ".csv" not in src, f"csv URL built by format(): {src}"


def test_a_page_that_links_nothing_reports_it_rather_than_guessing():
    assert C.csv_links("<html>no links here</html>",
                       "https://www.polymarketexchange.com/x.html") == []


def test_only_same_host_csv_links_are_followed():
    page = ('<a href="/20260113-time-and-sales.csv">a</a>'
            '<a href="https://evil.example.com/b.csv">b</a>'
            '<a href="https://www.polymarketexchange.com/c.csv">c</a>'
            '<a href="/regulatory.html">d</a>')
    got = C.csv_links(page, "https://www.polymarketexchange.com/x.html")
    assert got == ["https://www.polymarketexchange.com/20260113-time-and-sales.csv",
                   "https://www.polymarketexchange.com/c.csv"]
    assert not any("evil" in g for g in got)
    assert not any(g.endswith(".html") for g in got)


def test_a_failed_fetch_is_recorded_as_a_row():
    class Boom:
        def get(self, *a, **k):
            raise C.httpx.ConnectError("refused")
    p = C.Pacer(); p._last = -1e9
    row = C.fetch(Boom(), p, "https://www.polymarketexchange.com/x.html")
    assert row["error"] == "ConnectError"
    assert row["http_status"] is None


def test_the_header_is_reported_verbatim_and_a_mismatch_is_not_corrected():
    """If the venue's real file differs from its own documentation, that IS
    the finding. Renaming the observed columns to match would erase it."""
    good = C.parse_header("Transaction Time,Symbol,Last Price,Last Quantity\n1")
    assert good["HEADER_MATCHES_DOCUMENTATION"] == "YES"

    odd = C.parse_header("Time,Sym,Px,Qty\n1")
    assert odd["HEADER_MATCHES_DOCUMENTATION"] == "NO"
    assert odd["OBSERVED_COLUMNS"] == ["Time", "Sym", "Px", "Qty"]
    assert odd["DOCUMENTED_COLUMNS"] == list(C.DOCUMENTED_TAPE_COLUMNS)


def test_the_capturer_computes_no_date_arithmetic():
    """THE BUSINESS-DATE TRAP, C-6 in a new place. The venue's reporting day is
    as of 5:00 PM ET, so a file named 20260113 is NOT the UTC day 2026-01-13.
    Aligning them is a separate, tested step; this file must not quietly do it.

    STRUCTURAL, NOT A SUBSTRING SCAN. The file RECORDS the 5pm-ET cutover as a
    captured fact, so the string "America/New_York" appears in it -- and a
    scan for that string fails on the very label that states the guarantee.
    That is the third time this programme has written that test wrong. What
    must be absent is date ARITHMETIC: no timedelta, no parsing, no timezone
    conversion.
    """
    for n in ast.walk(TREE):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            assert n.func.attr not in (
                "strptime", "astimezone", "fromtimestamp", "date",
                "timestamp", "utcoffset"), ast.unparse(n)
        if isinstance(n, ast.Name):
            assert n.id not in ("timedelta", "ZoneInfo", "tzinfo"), n.id
    mods = set()
    for n in ast.walk(TREE):
        if isinstance(n, ast.ImportFrom) and n.module:
            mods.update("%s.%s" % (n.module, x.name) for x in n.names)
    assert "datetime.timedelta" not in mods, mods
    assert not any(m.startswith("zoneinfo") for m in mods), mods


def test_the_capturer_derives_no_fill_or_economics():
    for banned in ("COUNTERFACTUAL", "MARKOUT", "QUEUE_AHEAD", "edge", "ROI"):
        assert banned not in CODE, (
            "capture and interpretation are separate steps: %s" % banned)


def test_the_open_questions_start_open():
    """A capture that pre-answers its own questions is not evidence."""
    import inspect
    src = inspect.getsource(C.capture)
    for f in ("TAPE_TIMESTAMP_PRECISION", "PUBLICATION_LATENCY",
              "SYMBOL_JOINS_TO_MARKET_SLUG"):
        assert '"%s": "NOT_IDENTIFIED"' % f in src, f


def test_the_block_page_is_a_candidate_and_a_404_is_a_finding():
    """Its path is in none of our 340 captured pages, so it is fetched as a
    CANDIDATE. A 404 is not a failure of the capture -- it is the answer that
    row-level block exclusion is unavailable, which keeps every symbol-day
    carrying block volume blocked for queue inference."""
    assert C.BLOCK_TRADE_PAGE in C.LANDING_PAGES
    import inspect
    src = inspect.getsource(C.capture)
    assert '"BLOCK_TRADE_DATA_PUBLIC"' in src
    assert '"NO" if row["http_status"] == 404' in src


def test_a_missing_block_page_does_not_claim_the_tape_is_clean():
    """The dangerous default: no block page found, therefore no blocks. The
    summary must say the tape is an upper bound regardless."""
    import inspect
    src = inspect.getsource(C.capture)
    assert '"TAPE_VOLUME_IS_UPPER_BOUND_ON_CLOB_VOLUME": "YES"' in src


def test_the_join_key_is_the_business_date_and_not_a_clock_reading():
    """Tape and DMR publish at different times of day (~18:00 ET and ~00:00
    ET), so a fetch or discovery timestamp would join the wrong rows."""
    import inspect
    src = inspect.getsource(C.capture)
    assert '"JOIN_KEY": "BUSINESS_DATE"' in src
    for bad in ("UTC_CALENDAR_DATE", "FILE_DISCOVERY_TIMESTAMP",
                "FETCH_TIMESTAMP"):
        assert bad in src, bad


def test_the_block_page_does_not_widen_the_host_surface():
    """A third page, still one host."""
    for p in C.LANDING_PAGES:
        assert p.startswith("/")
        C._assert_tape_host(C.TAPE_BASE + p)
