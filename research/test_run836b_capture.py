"""RUN 83.6B -- self-tests for the capture harness. NOTHING CONTACTS A VENUE.

A local fake websocket server and a local fake REST server stand in for the
venue. The capture script is driven against them exactly as it would be driven
against Polymarket, and the fifteen required properties are checked on the files
it produces.

The fakes deliberately misbehave in the ways the real thing might: a frame that
is not JSON, a binary frame, an unknown field, a REST 500, and a REST body that
is not JSON. A recorder that only works on well-formed input is not a recorder.

Run:  python3 -m pytest test_run836b_capture.py -q
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "run836b_clob_capture.py"

pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="capture script absent")


# ---------------------------------------------------------------- the fakes
class FakeRest(BaseHTTPRequestHandler):
    """Serves /book. Fails on purpose for one token, to prove failures record."""

    def do_GET(self):                                   # noqa: N802
        if self.path.startswith("/book"):
            if "FAILTOKEN" in self.path:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"not json at all")
                return
            body = json.dumps({
                "market": "0xmarket", "asset_id": "TOK1", "timestamp": "1700000000000",
                "hash": "abc123", "bids": [{"price": "0.40", "size": "500"}],
                "asks": [{"price": "0.42", "size": "400"}],
                "min_order_size": "5", "tick_size": "0.01", "neg_risk": False,
                "last_trade_price": "0.41",
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *a):                          # noqa: A003
        pass


def _start_rest():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeRest)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


async def _ws_handler(ws):
    """Send a book, a price_change, an unknown type, bad JSON, and bytes."""
    try:
        await ws.recv()                                  # the subscribe frame
    except Exception:                                    # noqa: BLE001
        return
    await ws.send(json.dumps([{
        "event_type": "book", "asset_id": "TOK1", "market": "0xmarket",
        "timestamp": "1700000000000", "hash": "abc123",
        "bids": [{"price": "0.40", "size": "500"}],
        "asks": [{"price": "0.42", "size": "400"}],
        "an_unknown_field": {"kept": True},
    }]))
    await ws.send(json.dumps({
        "event_type": "price_change", "asset_id": "TOK1",
        "changes": [{"price": "0.41", "size": "120", "side": "BUY"}],
        "timestamp": "1700000000500", "hash": "def456",
    }))
    await ws.send("this is not json {{{")
    await ws.send(b"\x00\x01binary-frame")
    while True:
        await asyncio.sleep(0.2)


def _start_ws():
    import websockets

    box = {}
    ready = threading.Event()

    def run():
        async def main():
            async with websockets.serve(_ws_handler, "127.0.0.1", 0) as srv:
                box["port"] = srv.sockets[0].getsockname()[1]
                box["loop"] = asyncio.get_running_loop()
                ready.set()
                await asyncio.Future()
        try:
            asyncio.run(main())
        except Exception:                                # noqa: BLE001
            ready.set()

    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(15), "fake websocket server did not start"
    return box["port"]


def _run_capture(tmp_path, ws_port, rest_port, tokens, sessions=2,
                 seconds=2.0, pause=0.4, interval=1.0):
    out = tmp_path / "cap"
    cmd = [sys.executable, str(SCRIPT),
           "--ws-url", f"ws://127.0.0.1:{ws_port}/ws/market",
           "--rest-url", f"http://127.0.0.1:{rest_port}",
           "--out-dir", str(out),
           "--sessions", str(sessions),
           "--session-seconds", str(seconds),
           "--pause-seconds", str(pause),
           "--rest-interval", str(interval),
           "--i-am-running-the-self-tests"]
    for t in tokens:
        cmd += ["--token-id", t]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    return out, proc


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


@pytest.fixture(scope="module")
def servers():
    rest_srv, rest_port = _start_rest()
    ws_port = _start_ws()
    yield ws_port, rest_port
    rest_srv.shutdown()


@pytest.fixture(scope="module")
def capture(servers, tmp_path_factory):
    ws_port, rest_port = servers
    tmp = tmp_path_factory.mktemp("run836b")
    out, proc = _run_capture(tmp, ws_port, rest_port, ["TOK1", "FAILTOKEN"])
    return out, proc


# =====================================================================
# 1-4. THE FRAME RECORD
# =====================================================================
def test_1_raw_websocket_frame_is_preserved_exactly(capture):
    out, proc = capture
    frames = _jsonl(out / "websocket_frames.jsonl")
    assert frames, proc.stdout + proc.stderr

    bad = [f for f in frames if f.get("raw_frame") == "this is not json {{{"]
    assert bad, "the non-JSON frame was not stored verbatim"
    assert "parse_error" in bad[0], "a bad frame must record WHY it did not parse"
    assert "parsed_json" not in bad[0]

    # An unknown field inside a well-formed frame survives untouched.
    books = [f for f in frames if isinstance(f.get("parsed_json"), list)
             and f["parsed_json"] and f["parsed_json"][0].get("event_type") == "book"]
    assert books
    assert books[0]["parsed_json"][0]["an_unknown_field"] == {"kept": True}
    # And the raw text still contains it, so the parse is a convenience only.
    assert "an_unknown_field" in books[0]["raw_frame"]


def test_1b_binary_frames_are_base64_and_flagged(capture):
    out, _ = capture
    frames = _jsonl(out / "websocket_frames.jsonl")
    binaries = [f for f in frames if f.get("raw_is_base64")]
    assert binaries, "the binary frame was not captured"
    import base64
    assert base64.b64decode(binaries[0]["raw_frame_base64"]) == b"\x00\x01binary-frame"


def test_2_parsed_json_is_stored_separately_from_raw(capture):
    out, _ = capture
    frames = _jsonl(out / "websocket_frames.jsonl")
    parsed = [f for f in frames if "parsed_json" in f]
    assert parsed
    for f in parsed:
        assert "raw_frame" in f or "raw_frame_base64" in f, (
            "a parsed frame lost its raw form; raw must stay authoritative")


def test_3_and_4_every_frame_carries_both_clocks_and_a_session(capture):
    out, _ = capture
    for f in _jsonl(out / "websocket_frames.jsonl"):
        assert isinstance(f["local_receive_monotonic_ns"], int)
        assert f["local_receive_wall_utc"].endswith("+00:00")
        assert f["session_id"]
        assert f["capture_version"] == "run836b/1"


# =====================================================================
# 5 & 6. SESSIONS
# =====================================================================
def test_5_session_ids_differ_across_reconnects(capture):
    out, _ = capture
    sessions = _jsonl(out / "sessions.jsonl")
    assert len(sessions) == 2
    ids = [s["session_id"] for s in sessions]
    assert len(set(ids)) == 2, "reconnect reused a session id"
    for s in sessions:
        for k in ("connect_attempt_wall_utc", "connect_attempt_monotonic_ns",
                  "close_wall_utc", "close_monotonic_ns", "opened"):
            assert k in s
        if s["opened"]:
            assert "open_wall_utc" in s and "open_monotonic_ns" in s


def test_6_frame_indices_restart_each_session(capture):
    out, _ = capture
    frames = _jsonl(out / "websocket_frames.jsonl")
    by_session: dict[str, list[int]] = {}
    for f in frames:
        by_session.setdefault(f["session_id"], []).append(
            f["frame_index_within_session"])
    assert len(by_session) == 2, by_session.keys()
    for sid, idx in by_session.items():
        assert idx == list(range(len(idx))), f"{sid}: {idx[:10]}"
        assert idx[0] == 0


# =====================================================================
# 7 & 8. THE REST WITNESS
# =====================================================================
def test_7_rest_request_and_response_timing_recorded(capture):
    out, _ = capture
    rows = _jsonl(out / "rest_books.jsonl")
    ok = [r for r in rows if r.get("http_status") == 200]
    assert ok
    r = ok[0]
    for k in ("request_id", "token_id", "local_request_start_wall_utc",
              "local_request_start_monotonic_ns", "local_response_wall_utc",
              "local_response_monotonic_ns", "raw_response_body", "parsed_json",
              "response_headers"):
        assert k in r, k
    assert r["local_response_monotonic_ns"] >= r["local_request_start_monotonic_ns"]
    # Every field the published hash algorithm consumes must survive.
    for k in ("market", "asset_id", "timestamp", "hash", "bids", "asks",
              "min_order_size", "tick_size", "neg_risk", "last_trade_price"):
        assert k in r["parsed_json"], f"the hash input field {k} was not preserved"


def test_8_rest_failure_is_recorded_without_crashing_the_capture(capture):
    out, proc = capture
    rows = _jsonl(out / "rest_books.jsonl")
    failed = [r for r in rows if r.get("token_id") == "FAILTOKEN"]
    assert failed, "the failing token produced no record"
    assert failed[0]["http_status"] == 500
    assert failed[0]["raw_response_body"] == "not json at all"
    assert "parse_error" in failed[0]
    # and the capture still completed
    assert (out / "manifest.json").exists()


# =====================================================================
# 9. WEBSOCKET FAILURE
# =====================================================================
def test_9_websocket_failure_is_recorded_and_exits_cleanly(servers, tmp_path):
    _, rest_port = servers
    out = tmp_path / "wsfail"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--ws-url", "ws://127.0.0.1:1/ws/market",        # nothing listening
         "--rest-url", f"http://127.0.0.1:{rest_port}",
         "--out-dir", str(out), "--sessions", "1",
         "--session-seconds", "2", "--rest-interval", "1",
         "--token-id", "TOK1", "--i-am-running-the-self-tests"],
        capture_output=True, text=True, timeout=120)

    sessions = _jsonl(out / "sessions.jsonl")
    assert sessions and sessions[0]["opened"] is False
    assert "error_type" in sessions[0]
    assert _jsonl(out / "errors.jsonl"), "the failure was not recorded in errors"

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["CAPTURE_COMPLETE_FOR_RECONSTRUCTION"] == "NO"
    assert any("websocket" in r for r in manifest["incomplete_reasons"])
    assert proc.returncode == 1, "an incomplete capture must not report success"
    # It exits cleanly -- no traceback reaches the user.
    assert "Traceback" not in proc.stderr


# =====================================================================
# 10. CHECKSUMS
# =====================================================================
def test_10_checksums_reproduce_exactly(capture):
    out, _ = capture
    lines = (out / "checksums.sha256").read_text().strip().splitlines()
    assert len(lines) == 5, lines
    for line in lines:
        digest, name = line.split("  ", 1)
        h = hashlib.sha256((out / name).read_bytes()).hexdigest()
        assert h == digest, f"{name} does not match its recorded checksum"


def test_10b_the_manifest_is_inside_the_checksum_set(capture):
    out, _ = capture
    names = [ln.split("  ", 1)[1] for ln in
             (out / "checksums.sha256").read_text().strip().splitlines()]
    assert "manifest.json" in names


# =====================================================================
# 11-13. THE ALLOW-LIST, CREDENTIALS, PMUS
# =====================================================================
@pytest.mark.parametrize("host", [
    "wss://evil.example.com/ws/market",
    "wss://api.polymarket.us/v1/ws/markets",            # PMUS -- forbidden here
    "wss://ws-subscriptions-clob.polymarket.com.evil.com/ws",
])
def test_11_and_13_allow_list_rejects_non_clob_hosts_in_production_mode(tmp_path, host):
    """No --i-am-running-the-self-tests, so localhost is not exempt either."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--ws-url", host,
         "--out-dir", str(tmp_path / "nope"), "--token-id", "TOK1"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "refusing to contact" in proc.stdout
    assert not (tmp_path / "nope").exists(), (
        "the script created an output directory before failing closed")


def test_11b_localhost_is_rejected_without_the_test_flag(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--ws-url", "ws://127.0.0.1:9/ws",
         "--out-dir", str(tmp_path / "nope2"), "--token-id", "TOK1"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 3
    assert "refusing to contact" in proc.stdout


def test_12_no_credential_is_required_or_referenced_functionally():
    """The words may appear in prose. They may not appear in executable code."""
    import ast

    src = SCRIPT.read_text()
    tree = ast.parse(src)

    # Strip every docstring, then look at what is left.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body = node.body[1:]
    code_only = ast.unparse(tree)

    for banned in ("API_KEY", "SECRET", "PRIVATE_KEY", "Authorization",
                   "Bearer", "PMUS", "api_key", "secret_key", "private_key"):
        assert banned not in code_only, (
            f"{banned!r} appears in executable code, not just prose")

    # And no auth header is ever constructed.
    assert "headers=" not in code_only or "X-PM" not in code_only


def _code_only(path: Path) -> str:
    """The script with every docstring removed.

    THE FOURTH TIME THIS PROJECT HAS MADE THE SAME MISTAKE, so it is now a
    shared helper. A substring scan cannot tell an explanation from a
    violation: the capture script's own docstring says "no wallet, no
    Authorization header, no PMUS", and a naive scan reads that as all three
    being present. Strip the prose, then scan what actually executes.
    """
    import ast

    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                node.body = node.body[1:]
    return ast.unparse(tree)


def test_13b_no_pmus_or_order_path_in_the_executable_code():
    code = _code_only(SCRIPT).lower()
    for banned in ("polymarket.us", "api.polymarket.us", "/v1/orders",
                   "create_order", "place_order", "cancel_order", "post_order",
                   "close_position", "private_key", "wallet", "signature"):
        assert banned not in code, f"{banned} appears in executable code"
    # The two permitted hosts DO appear in code -- that is the allow-list.
    assert "ws-subscriptions-clob.polymarket.com" in code
    assert "clob.polymarket.com" in code


def test_14_no_order_or_trading_method_in_the_runtime_path():
    """The only outbound operations are: ws connect, ws send(subscribe), GET."""
    import ast

    tree = ast.parse(SCRIPT.read_text())
    sends = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr in ("send", "post",
                                                             "put", "delete",
                                                             "patch"):
                sends.append(fn.attr)
    assert "post" not in sends and "put" not in sends
    assert "delete" not in sends and "patch" not in sends
    assert sends.count("send") == 1, (
        f"expected exactly one ws send (the subscribe), found {sends}")


# =====================================================================
# 15. SELF-CONTAINED OUTPUT
# =====================================================================
def test_15_output_directory_is_self_contained(capture):
    out, _ = capture
    names = {p.name for p in out.iterdir()}
    assert names == {"manifest.json", "websocket_frames.jsonl", "rest_books.jsonl",
                     "sessions.jsonl", "errors.jsonl", "checksums.sha256"}, names

    manifest = json.loads((out / "manifest.json").read_text())
    for k in ("capture_version", "script_sha256", "python_version", "platform",
              "start_utc", "end_utc", "token_ids", "websocket_url", "rest_url",
              "configured_sessions", "configured_session_seconds",
              "rest_poll_interval_seconds", "dependency_versions",
              "sessions_attempted", "files",
              "CAPTURE_COMPLETE_FOR_RECONSTRUCTION"):
        assert k in manifest, k
    assert manifest["CAPTURE_COMPLETE_FOR_RECONSTRUCTION"] == "YES"
    # No credential material can be in there because none exists.
    blob = json.dumps(manifest)
    for banned in ("secret", "Authorization", "Bearer", "api_key"):
        assert banned not in blob


def test_the_script_interprets_nothing(capture):
    """No protocol conclusion may appear in the output.

    The capture's only verdict is whether BOTH evidence streams exist. If it
    ever started emitting a semantics judgement, the analysis would be reading
    the instrument's opinion back to itself.
    """
    out, _ = capture
    blob = "\n".join((out / n).read_text() for n in
                     ("manifest.json", "sessions.jsonl", "errors.jsonl"))

    # CAPTURE_COMPLETE_FOR_RECONSTRUCTION is REQUIRED by the run order and is
    # not a protocol conclusion -- it says only whether both evidence streams
    # exist. Remove it before scanning, so the scan tests what it means to.
    blob = blob.replace("CAPTURE_COMPLETE_FOR_RECONSTRUCTION", "<mandated-field>")

    for verdict in ("INCREMENTAL_LEVEL_UPDATE", "FULL_REPLACEMENT",
                    "PARTIALLY_IDENTIFIED", "CONTINUITY_VERIFIED",
                    "RECONSTRUCTION", "is_delta", "absolute_size",
                    "DEPTH_PENDING", "LEVEL_DELTA", "BEST_QUOTE"):
        assert verdict not in blob, f"the capture emitted a conclusion: {verdict}"
