#!/usr/bin/env python3
"""RESPONSE-BODY RETENTION, EXERCISED THROUGH THE REAL COMMAND.

The defect: `_cli` called `discovery_walk` with no `retain_bodies`, so the
retention callback existed and was never wired. Run 35333848994 therefore
sealed no response evidence and its failure could not be diagnosed afterwards.
A callback that only works in a unit test does not close that, so every test
here drives `substantive_select._cli` itself and then reads the files the
command left on disk.
"""
import json
import os
import sys

import pytest

import rehearsal_transport as T
import substantive_select as S

AS_OF = "2026-09-14T17:13:39Z"


def run_cli(tmp_path, pages, book_status=None):
    """Run the ACTUAL CLI against mocked transport. Returns (rc, selection)."""
    import httpx
    real = httpx.Client
    log, prov = [], {}
    T.install(pages, AS_OF, log, prov, book_status=book_status)
    out = str(tmp_path / "selection.json")
    argv = sys.argv
    sys.argv = ["substantive_select.py", "--out", out,
                "--rate", "200", "--as-of", AS_OF]
    rc = 0
    try:
        S._cli()
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = argv
        httpx.Client = real
    sel = json.load(open(out)) if os.path.exists(out) else None
    return rc, sel, tmp_path


def bodies_dir(tmp_path):
    return tmp_path / "discovery_bodies"


@pytest.fixture
def page0():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "fixtures_events_block3.json")) as fh:
        return json.load(fh)["RETAINED_PAGES"][0]["body"]


# ------------------------------------------------- the happy walk retains

def test_the_real_cli_writes_a_body_file_for_every_page(tmp_path, page0):
    rc, sel, tp = run_cli(tmp_path, [page0, {"events": []}])
    names = list(sel["RETAINED_RESPONSE_BODIES"])
    assert len(names) == 2                      # the page and the terminal page
    for n in names:
        f = bodies_dir(tp) / n
        assert f.exists() and f.stat().st_size > 0


def test_each_receipt_identifies_its_own_stored_body(tmp_path, page0):
    rc, sel, tp = run_cli(tmp_path, [page0, {"events": []}])
    import hashlib
    for r in sel["BOARD_REQUEST_RECEIPTS"]:
        art = r["BODY_ARTIFACT"]
        assert art != S.NOT_IDENTIFIED
        f = tp / art
        assert f.exists()
        # The receipt's byte hash is the hash of the bytes on disk.
        assert hashlib.sha256(f.read_bytes()).hexdigest() == \
            r["RESPONSE_BYTES_SHA256"]
        # ...and the artifact is NAMED by that hash, not by the canonical one.
        assert r["RESPONSE_BYTES_SHA256"][:16] in art


def test_the_byte_hash_and_the_canonical_hash_are_kept_apart(tmp_path, page0):
    rc, sel, tp = run_cli(tmp_path, [page0, {"events": []}])
    r = sel["BOARD_REQUEST_RECEIPTS"][0]
    assert r["RESPONSE_BYTES_SHA256"] != S.NOT_IDENTIFIED
    assert r["BODY_CANONICAL_SHA256"] != S.NOT_IDENTIFIED
    # Different digests of different things: exact bytes vs sorted-key JSON.
    assert r["RESPONSE_BYTES_SHA256"] != r["BODY_CANONICAL_SHA256"]


# ------------------------------------------- the failure paths retain too

def test_a_non_200_response_body_is_retained_before_it_is_rejected(
        tmp_path, page0):
    """The 503's body is the only thing that can explain the 503."""
    rc, sel, tp = run_cli(tmp_path, [page0, (503, {"error": "upstream"})])
    assert sel["BOARD_RETRIEVAL_STATUS"] == S.BOARD_HTTP_FAILURE
    last = sel["BOARD_REQUEST_RECEIPTS"][-1]
    assert last["HTTP_STATUS"] == 503
    assert last["BODY_ARTIFACT"] != S.NOT_IDENTIFIED
    f = tp / last["BODY_ARTIFACT"]
    assert f.exists()
    assert b"upstream" in f.read_bytes()
    # The body was taken BEFORE the status rejection, so it survived.
    assert last["RESPONSE_BYTES"] > 0


def test_a_non_json_response_is_retained_and_named_unparseable(tmp_path):
    """An HTML error page is exactly what a gateway sends under load."""
    class HtmlPage(dict):
        pass

    import httpx
    real = httpx.Client
    log = []

    class Cli(T.MockClient):
        def get(self, url, params=None, timeout=None):
            log.append(url)
            return T._Resp(None, status=200,
                           raw=b"<html><body>429 Too Many Requests</body></html>")

    httpx.Client = lambda *a, **k: Cli([], lambda s, a_: ({}, "X"), AS_OF, log)
    out = str(tmp_path / "selection.json")
    argv = sys.argv
    sys.argv = ["substantive_select.py", "--out", out, "--rate", "200",
                "--as-of", AS_OF]
    try:
        S._cli()
    except SystemExit:
        pass
    finally:
        sys.argv = argv
        httpx.Client = real
    sel = json.load(open(out))
    last = sel["BOARD_REQUEST_RECEIPTS"][-1]
    assert sel["BOARD_RETRIEVAL_STATUS"] == S.BOARD_SCHEMA_FAILURE
    assert last["RESPONSE_SHAPE"] == "UNPARSEABLE_BODY"
    assert last["BODY_ARTIFACT"] != S.NOT_IDENTIFIED
    f = tmp_path / last["BODY_ARTIFACT"]
    assert b"429 Too Many Requests" in f.read_bytes()
    # A body that never parsed has no canonical hash, and does not borrow one.
    assert last["BODY_CANONICAL_SHA256"] == S.NOT_IDENTIFIED
    assert last["RESPONSE_BYTES_SHA256"] != S.NOT_IDENTIFIED


def test_a_transport_failure_says_there_was_no_response(tmp_path):
    import httpx
    real = httpx.Client

    class Boom(object):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            raise OSError("connection reset by peer")

    httpx.Client = lambda *a, **k: Boom()
    out = str(tmp_path / "selection.json")
    argv = sys.argv
    sys.argv = ["substantive_select.py", "--out", out, "--rate", "200",
                "--as-of", AS_OF]
    try:
        S._cli()
    except SystemExit:
        pass
    finally:
        sys.argv = argv
        httpx.Client = real
    sel = json.load(open(out))
    last = sel["BOARD_REQUEST_RECEIPTS"][-1]
    assert sel["BOARD_RETRIEVAL_STATUS"] == S.BOARD_HTTP_FAILURE
    # NOT an empty body and NOT a zero-byte artifact: nothing arrived.
    assert last["BODY_ARTIFACT"] == S.NO_RESPONSE_RECEIVED
    assert last["RESPONSE_BYTES"] == S.NO_RESPONSE_RECEIVED
    assert last["RESPONSE_BYTES_SHA256"] == S.NOT_IDENTIFIED
    assert "connection reset" in last["ERROR"]
    assert list(sel["RETAINED_RESPONSE_BODIES"]) == []


# --------------------------------------------------------- the injected clock

def test_the_selection_records_which_clock_decided_it(tmp_path, page0):
    rc, sel, tp = run_cli(tmp_path, [page0, {"events": []}])
    assert sel["DECISION_CLOCK_INJECTED"] is True
    assert sel["DECISION_CLOCK_SOURCE"] == "INJECTED_AS_OF_OFFLINE_REHEARSAL"
    assert sel["DECISION_TIME_UTC"].startswith("2026-09-14T17:13:39")


def test_the_live_default_is_the_wall_clock_and_says_so(tmp_path, page0):
    """No --as-of: the command must not silently inherit a rehearsal clock."""
    import httpx
    real = httpx.Client
    T.install([page0, {"events": []}], AS_OF, [], {})
    out = str(tmp_path / "selection.json")
    argv = sys.argv
    sys.argv = ["substantive_select.py", "--out", out, "--rate", "200"]
    try:
        S._cli()
    except SystemExit:
        pass
    finally:
        sys.argv = argv
        httpx.Client = real
    sel = json.load(open(out))
    assert sel["DECISION_CLOCK_INJECTED"] is False
    assert sel["DECISION_CLOCK_SOURCE"] == "WALL_CLOCK"
