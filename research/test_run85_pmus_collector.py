#!/usr/bin/env python3
"""Offline tests for the Run 85 PMUS public collector. Contacts nothing.

The transport is a fake. Every venue response here is hand-written, so a pass
says the COLLECTOR behaves as specified and says nothing whatever about the
venue -- the same separation Run 83.6A's synthetic suite kept.

The load-bearing tests are the capability ones. A collector that merely
happens not to authenticate today is one edit away from authenticating
tomorrow, so the absence is proved by scanning the module's own source for the
constructs that would be required, not by asserting on behaviour.

Run:  python3 -m pytest research/test_run85_pmus_collector.py -q
      python3 research/test_run85_pmus_collector.py
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

SRC = Path(__file__).with_name("run85_pmus_collector.py")
_spec = importlib.util.spec_from_file_location("run85", SRC)
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)


def code_only(path: Path) -> str:
    """The module's EXECUTABLE source, with comments and docstrings removed.

    The capability scanners below must read code, not prose. This file explains
    at length which headers the collector must never construct, and naming them
    in a docstring is the opposite of a defect -- it is the documentation. A
    scanner that cannot tell `X-PM-Signature` in an explanation from
    `X-PM-Signature` in a dict literal would force the explanation out, and the
    file would get less honest to keep the test green.

    ast.unparse drops comments; docstrings are removed explicitly. Real string
    VALUES survive, which is what the host-name test needs.
    """
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)
    return ast.unparse(tree)


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {"Content-Type": "application/json",
                                   "X-RateLimit-Limit": "60"}

    @property
    def content(self):
        return (json.dumps(self._payload) if self._payload is not None
                else "not json").encode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeHTTP:
    """Records every request so the tests can assert on what was sent."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        r = self.responses.pop(0) if self.responses else FakeResponse(500)
        if isinstance(r, Exception):
            raise r
        return r


def book(bids, offers, state="MARKET_STATE_OPEN"):
    return {"marketSlug": "s", "state": state, "transactTime": "1789000000000",
            "bids": [{"px": p, "qty": q} for p, q in bids],
            "offers": [{"px": p, "qty": q} for p, q in offers],
            "stats": {"lastTradePx": "0.50", "sharesTraded": "100"}}


# ------------------------------------------------- capability isolation
def test_the_collector_never_imports_the_trading_sdk():
    tree = ast.parse(SRC.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "polymarket_us" not in imported
    assert "py_clob_client" not in imported
    assert imported <= {"argparse", "hashlib", "json", "sys", "time",
                        "datetime", "pathlib", "httpx", "__future__"}


def test_the_collector_source_contains_no_signing_construct():
    src = code_only(SRC)
    for forbidden in ("SigningKey", "nacl", "create_auth_headers",
                      "X-PM-Access-Key", "X-PM-Signature", "X-PM-Timestamp",
                      "secret_key", "key_id", "private_key", "authenticated"):
        assert forbidden not in src, forbidden


def test_the_collector_names_no_authenticated_host():
    """api.polymarket.us must not appear even as a string.

    Header scope is not key scope (Run 83.3 section 2): the venue does not bind
    a signature to a hostname. Keeping the authenticated host out of the file
    entirely means there is nothing here to point at it.
    """
    assert "api.polymarket.us" not in code_only(SRC)
    assert C.GATEWAY_BASE == "https://gateway.polymarket.us"


def test_no_order_or_cancel_path_is_reachable_from_this_module():
    src = code_only(SRC).lower()
    for forbidden in ("/v1/orders", ".post(", ".delete(", "cancel", "place_order"):
        assert forbidden not in src, forbidden


def test_every_request_the_collector_makes_is_a_bare_get():
    http = FakeHTTP([FakeResponse(200, book([("0.40", "10")], [("0.60", "5")]))])
    C._get(http, "/v1/markets/x/book")
    assert len(http.calls) == 1
    assert http.calls[0]["url"].startswith("https://gateway.polymarket.us")


# ----------------------------------------------------------- pacing
def test_the_pacer_refuses_to_go_faster_than_the_ceiling():
    p = C.Pacer(spacing_s=0.0)
    assert p.spacing == C.MIN_SPACING_S


def test_the_pacer_keeps_a_slower_spacing_when_asked():
    assert C.Pacer(spacing_s=3.0).spacing == 3.0


# -------------------------------------------------- a miss is a row
def test_a_non_200_is_recorded_as_a_row_not_dropped():
    http = FakeHTTP([FakeResponse(503)])
    r = C._get(http, "/v1/markets/x/book")
    assert r["http_status"] == 503
    assert r["error"] == "http_503"
    assert r["body"] is None


def test_a_transport_error_is_recorded_as_a_row_not_dropped():
    http = FakeHTTP([__import__("httpx").ConnectError("boom")])
    r = C._get(http, "/v1/markets/x/book")
    assert r["http_status"] is None
    assert r["error"] == "ConnectError"


def test_a_200_with_a_non_json_body_is_named_rather_than_guessed():
    http = FakeHTTP([FakeResponse(200, None)])
    r = C._get(http, "/v1/markets/x/book")
    assert r["error"] == "NON_JSON_BODY"


def test_every_row_records_headers_size_hash_and_latency():
    """Section A of Phase 2A needs all four, and a row that lacks them cannot
    be repaired afterwards -- the response is gone."""
    http = FakeHTTP([FakeResponse(200, book([("0.4", "1")], []))])
    r = C._get(http, "/v1/markets/x/book")
    assert r["response_headers"]["x-ratelimit-limit"] == "60"
    assert r["response_bytes"] > 0
    assert len(r["response_sha256"]) == 64
    assert r["latency_ms"] >= 0


def test_rate_limit_headers_are_kept_and_unrelated_ones_dropped():
    http = FakeHTTP([FakeResponse(200, {}, headers={
        "X-RateLimit-Remaining": "59", "Retry-After": "1",
        "Cache-Control": "no-store", "X-Irrelevant": "zzz"})])
    r = C._get(http, "/v1/markets/x/book")
    assert set(r["response_headers"]) == {
        "x-ratelimit-remaining", "retry-after", "cache-control"}


def test_every_row_carries_both_clocks_at_request_and_response():
    http = FakeHTTP([FakeResponse(200, book([], []))])
    r = C._get(http, "/v1/markets/x/book")
    for k in ("local_request_wall_utc", "local_request_monotonic_ns",
              "local_response_wall_utc", "local_response_monotonic_ns"):
        assert r[k] is not None


def test_monotonic_is_nanoseconds_and_advances():
    _, a = C._now()
    _, b = C._now()
    assert isinstance(a, int) and b >= a


# ------------------------------------------------------- discovery
def test_discovery_selects_only_two_outcome_events(tmp_path=Path("/tmp/r85a")):
    tmp_path.mkdir(parents=True, exist_ok=True)
    payload = {"markets": [
        {"slug": "a-yes", "eventSlug": "ev1", "title": "A"},
        {"slug": "a-no", "eventSlug": "ev1", "title": "B"},
        {"slug": "b-1", "eventSlug": "ev2", "title": "C"},
        {"slug": "c-1", "eventSlug": "ev3", "title": "D"},
        {"slug": "c-2", "eventSlug": "ev3", "title": "E"},
        {"slug": "c-3", "eventSlug": "ev3", "title": "F"},
    ]}
    http = FakeHTTP([FakeResponse(200, payload)])
    sel = C.discover(tmp_path, 0, C.Pacer(0.0), http)
    assert [e["eventSlug"] for e in sel] == ["ev1"]
    saved = json.loads((tmp_path / "selected_events.json").read_text())
    reasons = {s["eventSlug"]: s["outcome_market_count"] for s in saved["skipped"]}
    assert reasons == {"ev2": 1, "ev3": 3}


def test_discovery_records_the_skipped_events_rather_than_dropping_them():
    tmp = Path("/tmp/r85b")
    tmp.mkdir(parents=True, exist_ok=True)
    http = FakeHTTP([FakeResponse(200, {"markets": [
        {"slug": "z", "eventSlug": "solo", "title": "Z"}]})])
    C.discover(tmp, 0, C.Pacer(0.0), http)
    saved = json.loads((tmp / "selected_events.json").read_text())
    assert saved["selected"] == []
    assert saved["skipped"][0]["reason"] == "NOT_A_TWO_OUTCOME_EVENT"


def test_discovery_survives_a_failed_listing_without_inventing_markets():
    tmp = Path("/tmp/r85c")
    tmp.mkdir(parents=True, exist_ok=True)
    http = FakeHTTP([FakeResponse(500)])
    sel = C.discover(tmp, 0, C.Pacer(0.0), http)
    assert sel == []
    assert json.loads((tmp / "selected_events.json").read_text())["markets_seen"] == 0


# -------------------------------------------------------- sampling
def test_sampling_reads_both_legs_and_records_the_gap():
    tmp = Path("/tmp/r85d")
    tmp.mkdir(parents=True, exist_ok=True)
    ev = [{"eventSlug": "ev1", "slugs": ["a-yes", "a-no"]}]
    http = FakeHTTP([FakeResponse(200, book([("0.40", "10")], [("0.44", "10")])),
                     FakeResponse(200, book([("0.56", "10")], [("0.60", "10")]))])
    n, pairs = C.sample(tmp, ev, seconds=0.01, interval_s=0.0,
                        pacer=C.Pacer(0.0), http=http)
    rows = [json.loads(x) for x in
            (tmp / "book_samples.jsonl").read_text().splitlines()]
    gaps = [r for r in rows if r.get("record") == "PAIR_GAP"]
    assert pairs == 1 and len(gaps) == 1
    assert gaps[0]["leg_gap_ns"] >= 0
    assert gaps[0]["slugs"] == ["a-yes", "a-no"]


def test_sampling_is_not_triggered_by_any_external_event():
    """The schedule is a clock and a slug list. Nothing else can enter it.

    Run 84's opportunity set was whale-selected and that limit could not be
    removed afterwards; this signature is what prevents the same bias here.
    """
    import inspect
    params = set(inspect.signature(C.sample).parameters)
    assert params == {"outdir", "events", "seconds", "interval_s",
                      "pacer", "http"}


def test_a_failed_leg_still_produces_rows_and_no_pair_gap_is_faked():
    tmp = Path("/tmp/r85e")
    tmp.mkdir(parents=True, exist_ok=True)
    ev = [{"eventSlug": "ev1", "slugs": ["a", "b"]}]
    http = FakeHTTP([FakeResponse(200, book([], [])), FakeResponse(503)])
    C.sample(tmp, ev, seconds=0.01, interval_s=0.0, pacer=C.Pacer(0.0), http=http)
    rows = [json.loads(x) for x in
            (tmp / "book_samples.jsonl").read_text().splitlines()]
    books = [r for r in rows if r.get("record") != "PAIR_GAP"]
    assert len(books) == 2
    assert books[1]["error"] == "http_503"
    # the gap row still exists because both legs were ATTEMPTED, and the failed
    # leg is visible in its own row rather than erased by the pair's absence
    assert any(r.get("record") == "PAIR_GAP" for r in rows)


def test_both_legs_carry_the_event_and_market_identity():
    tmp = Path("/tmp/r85f")
    tmp.mkdir(parents=True, exist_ok=True)
    ev = [{"eventSlug": "ev9", "slugs": ["p", "q"]}]
    http = FakeHTTP([FakeResponse(200, book([], [])), FakeResponse(200, book([], []))])
    C.sample(tmp, ev, seconds=0.01, interval_s=0.0, pacer=C.Pacer(0.0), http=http)
    rows = [json.loads(x) for x in
            (tmp / "book_samples.jsonl").read_text().splitlines()]
    books = [r for r in rows if r.get("record") != "PAIR_GAP"]
    assert [r["market_slug"] for r in books] == ["p", "q"]
    assert {r["event_slug"] for r in books} == {"ev9"}


def test_the_body_is_stored_verbatim_including_both_ladders():
    http = FakeHTTP([FakeResponse(200, book([("0.40", "10")], [("0.60", "5")]))])
    r = C._get(http, "/v1/markets/x/book")
    assert r["body"]["bids"] == [{"px": "0.40", "qty": "10"}]
    assert r["body"]["offers"] == [{"px": "0.60", "qty": "5"}]
    assert r["body"]["state"] == "MARKET_STATE_OPEN"
    assert r["body"]["transactTime"] == "1789000000000"


def test_a_suspended_market_is_stored_not_filtered():
    """Market state is evidence. Dropping non-OPEN states here would hide the
    denominator that any fill model needs."""
    http = FakeHTTP([FakeResponse(200, book([], [], "MARKET_STATE_SUSPENDED"))])
    r = C._get(http, "/v1/markets/x/book")
    assert r["body"]["state"] == "MARKET_STATE_SUSPENDED"


# ----------------------------------------------------------- sealing
def test_seal_hashes_every_file_and_excludes_its_own_output():
    tmp = Path("/tmp/r85g")
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "a.jsonl").write_text("x\n")
    (tmp / "b.json").write_text("y\n")
    C.seal(tmp)
    text = (tmp / "checksums.sha256").read_text()
    assert "a.jsonl" in text and "b.json" in text
    assert "checksums.sha256" not in text


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(vars().items())
           if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in fns:
        try:
            fn()
        except Exception as exc:                      # noqa: BLE001
            failed.append((name, exc))
    for name, exc in failed:
        print("FAIL %s: %r" % (name, exc))
    print("%d passed, %d failed" % (len(fns) - len(failed), len(failed)))
    sys.exit(1 if failed else 0)
