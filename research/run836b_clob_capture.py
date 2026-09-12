#!/usr/bin/env python3
"""RUN 83.6B -- CLOB public passive capture. A RECORDER, NOT AN ANALYSER.

Run this on your own machine. It connects to the PUBLIC, UNAUTHENTICATED
Polymarket CLOB market-data websocket, records every frame exactly as received,
independently polls the public REST order book as a witness, deliberately
reconnects a few times, and writes frozen raw files with checksums.

IT INTERPRETS NOTHING. It does not decide whether `book` is a full replacement,
whether `price_change` is a delta, whether sizes are absolute or incremental,
whether the hash implies continuity, or whether any reconstruction is correct.
Those are the Run 83.6A questions and they are answered offline, against these
files, afterwards. A capture tool that formed opinions would be deciding the
experiment's outcome inside the instrument.

NO CREDENTIAL OF ANY KIND IS REQUIRED OR ACCEPTED. There is no API key, no
secret, no wallet, no Authorization header, no PMUS. Both endpoints used here
are public. The script refuses any host outside a two-entry allow-list.

WHAT THE ANALYSIS WILL BE ABLE TO DO WITH THIS (recorded here so the capture is
known to be sufficient, NOT applied by this script):

    A book-summary hash algorithm is published, in the ARCHIVED py-clob-client
    0.34.6 `utilities.generate_orderbook_summary_hash`: SHA-1 over a compact
    JSON payload, key order
        market, asset_id, timestamp, hash, bids, asks,
        min_order_size, tick_size, neg_risk, last_trade_price
    with "hash" set to "" while hashing, separators (",", ":"), ensure_ascii
    False, UTF-8.

    That algorithm is LEGACY evidence. The current unified SDK ships no hash
    helper at all, so whether the production stream still hashes this way is
    unverified and is one of the things the offline analysis may test -- it is
    not an assumption this capture relies on. If the recomputation matches the
    venue's own `hash` field, that is a finding; if it does not, the legacy
    algorithm simply no longer describes the surface, and the REST witness
    still stands on its own.

    Either way the capture must preserve every field that could feed such a
    hash, which is why raw frames and raw REST bodies are stored verbatim and
    nothing is normalised away.

CURRENT-SURFACE FIDELITY. The subscribe frame, the identifier field, the two
hosts, the REST book path and the application-level PING/PONG heartbeat below
are taken from the CURRENT unified SDK (polymarket-client 0.10.0), not from the
archived client. The heartbeat matters for the science as well as for liveness:
without it a venue-initiated idle close would be recorded as an involuntary
disconnect and would contaminate the reconnect experiment.

USAGE
    pip install websockets httpx
    python run836b_clob_capture.py --discover          # find candidate tokens
    python run836b_clob_capture.py --token-id A --token-id B --token-id C

Output lands in ./run836b_capture_<UTC>/ and is checksummed. Do not edit the
files afterwards; the analysis runs against the frozen bytes.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

CAPTURE_VERSION = "run836b/2"

# ------------------------------------------------- current-surface constants
# Every value here is copied from the CURRENT unified SDK, polymarket-client
# 0.10.0, so the capture speaks the protocol the venue serves today:
#
#   _internal/streams/clob/market_protocol.py:41  build_initial_frame ->
#       {"type": "market", "assets_ids": [...], "custom_feature_enabled": bool}
#   _internal/streams/clob/heartbeat.py           CLOB_HEARTBEAT_INTERVAL_S =
#       10.0, CLOB_HEARTBEAT_STALE_S = 30.0, text "PING" -> text "PONG"
#   _internal/actions/clob.py:148                 ("/book", {"token_id": ...})
#
# The SDK's own default for custom_feature_enabled is False, so False is this
# script's default too: the frame we send is byte-for-byte the frame the
# official client sends by default. Setting it True is offered as a switch
# because the flag gates the best_bid_ask / new_market / market_resolved event
# classes, but it is NOT the default -- changing a flag whose server-side
# effect on `book` and `price_change` is unestablished would be changing the
# thing under study.
SUBSCRIBE_TYPE = "market"
SUBSCRIBE_IDENTIFIER_FIELD = "assets_ids"
HEARTBEAT_TEXT = "PING"
HEARTBEAT_REPLY_TEXT = "PONG"
HEARTBEAT_INTERVAL_S = 10.0
REST_BOOK_PATH = "/book"
REST_BOOK_PARAM = "token_id"

# ---------------------------------------------------------------- allow-list
# THE ONLY TWO HOSTS THIS SCRIPT MAY CONTACT. Enforced by _assert_allowed on
# every URL before every connection. Fails closed: an unknown host raises and
# the script exits rather than "falling back" anywhere.
WS_HOST = "ws-subscriptions-clob.polymarket.com"
REST_HOST = "clob.polymarket.com"
ALLOWED_HOSTS = frozenset({WS_HOST, REST_HOST})

DEFAULT_WS_URL = f"wss://{WS_HOST}/ws/market"
DEFAULT_REST_URL = f"https://{REST_HOST}"

# Deliberately NOT a default token list. Stale ids would silently produce an
# empty capture that looks like a quiet market. Supply them, or use --discover.
EXAMPLE_ONLY_NOTE = (
    "no token ids supplied -- run with --discover to list candidates, then "
    "pass them with --token-id (repeatable)")


class HostNotAllowed(RuntimeError):
    """A URL outside the two-entry allow-list. The script stops."""


def _assert_allowed(url: str, *, allow_local: bool) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host in ALLOWED_HOSTS:
        return host
    if allow_local and host in ("127.0.0.1", "localhost", "::1"):
        return host
    raise HostNotAllowed(
        f"refusing to contact {host!r}. This script may contact only "
        f"{sorted(ALLOWED_HOSTS)}. (Local addresses are permitted only under "
        f"--i-am-running-the-self-tests, which is for the offline test rig.)")


def _now_wall() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _now_mono_ns() -> int:
    return time.monotonic_ns()


class Writer:
    """Append-only JSONL, flushed per line so a crash keeps what it had."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = path.open("w", encoding="utf-8")
        self.count = 0

    def write(self, row: dict) -> None:
        self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._fh.flush()
        self.count += 1

    def close(self) -> None:
        self._fh.close()


class Capture:
    def __init__(self, args) -> None:
        self.args = args
        self.allow_local = args.i_am_running_the_self_tests
        self.out = Path(args.out_dir)
        self.out.mkdir(parents=True, exist_ok=False)
        self.frames = Writer(self.out / "websocket_frames.jsonl")
        self.rest = Writer(self.out / "rest_books.jsonl")
        self.sessions = Writer(self.out / "sessions.jsonl")
        self.errors = Writer(self.out / "errors.jsonl")
        self.started_wall = _now_wall()
        self.session_count = 0
        self.opened_sessions = 0
        self.rest_ok = 0
        self.rest_fail = 0

    # ---------------------------------------------------------------- errors
    def error(self, where: str, exc: BaseException | str, **extra) -> None:
        self.errors.write({
            "capture_version": CAPTURE_VERSION,
            "local_wall_utc": _now_wall(),
            "local_monotonic_ns": _now_mono_ns(),
            "where": where,
            "error_type": type(exc).__name__ if isinstance(exc, BaseException) else "Reported",
            "error": str(exc)[:2000],
            **extra,
        })

    # ------------------------------------------------------------- websocket
    async def run_session(self, index: int) -> None:
        import websockets

        session_id = str(uuid.uuid4())
        self.session_count += 1
        url = self.args.ws_url
        _assert_allowed(url, allow_local=self.allow_local)

        row: dict = {
            "capture_version": CAPTURE_VERSION,
            "session_id": session_id,
            "session_index": index,
            "ws_url": url,
            "token_ids": self.args.token_id,
            "connect_attempt_wall_utc": _now_wall(),
            "connect_attempt_monotonic_ns": _now_mono_ns(),
            "opened": False,
        }
        frame_index = 0
        heartbeat_sent = 0
        heartbeat_task = None
        try:
            async with websockets.connect(url, open_timeout=self.args.open_timeout,
                                          max_size=None) as ws:
                row["opened"] = True
                row["open_wall_utc"] = _now_wall()
                row["open_monotonic_ns"] = _now_mono_ns()
                self.opened_sessions += 1

                # The CURRENT SDK's initial frame, field for field.
                sub = {
                    "type": SUBSCRIBE_TYPE,
                    SUBSCRIBE_IDENTIFIER_FIELD: list(self.args.token_id),
                    "custom_feature_enabled": bool(self.args.custom_feature_enabled),
                }
                row["subscribe_payload"] = sub
                row["subscribe_sent_wall_utc"] = _now_wall()
                row["subscribe_sent_monotonic_ns"] = _now_mono_ns()
                await ws.send(json.dumps(sub))

                # Application-level heartbeat, as the current SDK sends it. The
                # venue's PONG replies arrive on the same socket and are
                # recorded as ordinary frames -- they are part of the record,
                # not filtered out of it.
                async def _beat() -> None:
                    nonlocal heartbeat_sent
                    try:
                        while True:
                            await asyncio.sleep(self.args.heartbeat_interval)
                            await ws.send(HEARTBEAT_TEXT)
                            heartbeat_sent += 1
                    except asyncio.CancelledError:
                        return
                    except Exception as exc:           # noqa: BLE001 -- recorded
                        self.error("heartbeat", exc, session_id=session_id)

                if self.args.heartbeat_interval > 0:
                    heartbeat_task = asyncio.create_task(_beat())

                deadline = time.monotonic() + self.args.session_seconds
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining))
                    except asyncio.TimeoutError:
                        break
                    recv_wall, recv_mono = _now_wall(), _now_mono_ns()
                    self._record_frame(session_id, frame_index, raw, recv_wall, recv_mono)
                    frame_index += 1
                row["close_reason_local"] = "session_duration_elapsed"
        except Exception as exc:                       # noqa: BLE001 -- recorded
            row["error_type"] = type(exc).__name__
            row["error"] = str(exc)[:2000]
            self.error("websocket_session", exc, session_id=session_id)
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except (asyncio.CancelledError, Exception):   # noqa: BLE001
                    pass
            row["heartbeat_text"] = HEARTBEAT_TEXT
            row["heartbeat_interval_seconds"] = self.args.heartbeat_interval
            row["heartbeats_sent"] = heartbeat_sent
            row["frames_recorded"] = frame_index
            row["close_wall_utc"] = _now_wall()
            row["close_monotonic_ns"] = _now_mono_ns()
            self.sessions.write(row)

    def _record_frame(self, session_id: str, frame_index: int, raw,
                      recv_wall: str, recv_mono: int) -> None:
        """Store the frame EXACTLY as received, plus a parse attempt beside it.

        Binary frames are base64-encoded and flagged, so the stored value is
        still losslessly the bytes that arrived. Nothing is normalised, no
        unknown field is dropped, and no price or size is converted -- the raw
        payload stays authoritative.
        """
        row: dict = {
            "capture_version": CAPTURE_VERSION,
            "session_id": session_id,
            "frame_index_within_session": frame_index,
            "local_receive_wall_utc": recv_wall,
            "local_receive_monotonic_ns": recv_mono,
        }
        if isinstance(raw, (bytes, bytearray)):
            import base64
            row["raw_frame_base64"] = base64.b64encode(bytes(raw)).decode("ascii")
            row["raw_is_base64"] = True
            text = None
            try:
                text = bytes(raw).decode("utf-8")
            except UnicodeDecodeError as exc:
                row["decode_error"] = str(exc)
        else:
            row["raw_frame"] = raw
            row["raw_is_base64"] = False
            text = raw

        if text is not None:
            try:
                row["parsed_json"] = json.loads(text)
            except ValueError as exc:
                row["parse_error"] = str(exc)
        self.frames.write(row)

    # ------------------------------------------------------------------ REST
    async def poll_rest(self, stop: asyncio.Event) -> None:
        """The INDEPENDENT WITNESS. It never touches websocket state.

        Nothing in this script feeds a REST response back into the stream side.
        The two records are written separately and reconciled offline, which is
        the only way the comparison can be evidence rather than a repair.
        """
        import httpx

        url = f"{self.args.rest_url.rstrip('/')}{REST_BOOK_PATH}"
        _assert_allowed(url, allow_local=self.allow_local)

        # follow_redirects=False: a redirect to another host would be a way
        # around the allow-list, so redirects are recorded and never followed.
        async with httpx.AsyncClient(timeout=self.args.rest_timeout,
                                     follow_redirects=False) as client:
            while not stop.is_set():
                for token in self.args.token_id:
                    if stop.is_set():
                        break
                    await self._one_rest(client, url, token)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.args.rest_interval)
                except asyncio.TimeoutError:
                    pass

    async def _one_rest(self, client, url: str, token: str) -> None:
        import httpx

        row: dict = {
            "capture_version": CAPTURE_VERSION,
            "request_id": str(uuid.uuid4()),
            "token_id": token,
            "url": url,
            "local_request_start_wall_utc": _now_wall(),
            "local_request_start_monotonic_ns": _now_mono_ns(),
        }
        try:
            resp = await client.get(url, params={REST_BOOK_PARAM: token})
            row["local_response_wall_utc"] = _now_wall()
            row["local_response_monotonic_ns"] = _now_mono_ns()
            row["http_status"] = resp.status_code
            row["response_headers"] = {
                k: v for k, v in resp.headers.items()
                if k.lower() in ("date", "content-type", "content-length",
                                 "server", "cf-ray", "x-request-id", "location")}
            row["raw_response_body"] = resp.text
            try:
                row["parsed_json"] = resp.json()
                if resp.status_code == 200:
                    self.rest_ok += 1
                else:
                    self.rest_fail += 1
            except ValueError as exc:
                row["parse_error"] = str(exc)
                self.rest_fail += 1
        except Exception as exc:                       # noqa: BLE001 -- recorded
            row["local_response_wall_utc"] = _now_wall()
            row["local_response_monotonic_ns"] = _now_mono_ns()
            row["error_type"] = type(exc).__name__
            row["error"] = str(exc)[:2000]
            self.rest_fail += 1
            self.error("rest_book", exc, token_id=token)
        self.rest.write(row)
        del httpx

    # ----------------------------------------------------------------- drive
    async def run(self) -> None:
        stop = asyncio.Event()
        rest_task = asyncio.create_task(self.poll_rest(stop))
        try:
            for i in range(self.args.sessions):
                await self.run_session(i)
                if i < self.args.sessions - 1:
                    await asyncio.sleep(self.args.pause_seconds)
        finally:
            stop.set()
            try:
                await asyncio.wait_for(rest_task, timeout=30)
            except Exception as exc:                   # noqa: BLE001
                rest_task.cancel()
                self.error("rest_task_shutdown", exc)

    # -------------------------------------------------------------- finalise
    def finalise(self) -> dict:
        for w in (self.frames, self.rest, self.sessions, self.errors):
            w.close()

        complete = (self.opened_sessions > 0 and self.frames.count > 0
                    and self.rest_ok > 0)
        reasons = []
        if self.opened_sessions == 0:
            reasons.append("no websocket session opened")
        if self.frames.count == 0:
            reasons.append("no websocket frames recorded")
        if self.rest_ok == 0:
            reasons.append("no successful REST witness response")

        manifest = {
            "capture_version": CAPTURE_VERSION,
            "script_sha256": _self_sha256(),
            "python_version": sys.version,
            "platform": platform.platform(),
            "start_utc": self.started_wall,
            "end_utc": _now_wall(),
            "token_ids": list(self.args.token_id),
            "websocket_url": self.args.ws_url,
            "rest_url": self.args.rest_url,
            # What surface this capture spoke, so the analysis never has to
            # guess which protocol shape produced these bytes.
            "subscribe_type": SUBSCRIBE_TYPE,
            "subscribe_identifier_field": SUBSCRIBE_IDENTIFIER_FIELD,
            "custom_feature_enabled": bool(self.args.custom_feature_enabled),
            "heartbeat_text": HEARTBEAT_TEXT,
            "heartbeat_interval_seconds": self.args.heartbeat_interval,
            "rest_book_path": REST_BOOK_PATH,
            "rest_book_param": REST_BOOK_PARAM,
            "current_surface_source": "polymarket-client 0.10.0",
            "configured_sessions": self.args.sessions,
            "configured_session_seconds": self.args.session_seconds,
            "configured_pause_seconds": self.args.pause_seconds,
            "rest_poll_interval_seconds": self.args.rest_interval,
            "dependency_versions": _dependency_versions(),
            "sessions_attempted": self.session_count,
            "sessions_opened": self.opened_sessions,
            "websocket_frames": self.frames.count,
            "rest_requests": self.rest.count,
            "rest_successful": self.rest_ok,
            "rest_failed": self.rest_fail,
            "errors_recorded": self.errors.count,
            "files": ["manifest.json", "websocket_frames.jsonl",
                      "rest_books.jsonl", "sessions.jsonl", "errors.jsonl"],
            # The honest verdict on whether 83.6A can be answered from this
            # capture. NOT a protocol conclusion -- only a statement about
            # whether both evidence streams exist.
            "CAPTURE_COMPLETE_FOR_RECONSTRUCTION": "YES" if complete else "NO",
            "incomplete_reasons": reasons,
        }
        (self.out / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

        # Checksums LAST, over everything including the manifest. Nothing is
        # written to this directory afterwards.
        lines = []
        for name in manifest["files"]:
            p = self.out / name
            if p.exists():
                lines.append(f"{_sha256_file(p)}  {name}")
        (self.out / "checksums.sha256").write_text("\n".join(lines) + "\n",
                                                   encoding="utf-8")
        return manifest


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _self_sha256() -> str:
    try:
        return _sha256_file(Path(__file__).resolve())
    except Exception:                                  # noqa: BLE001
        return "unavailable"


def _dependency_versions() -> dict:
    out = {}
    for mod in ("websockets", "httpx"):
        try:
            out[mod] = __import__(mod).__version__
        except Exception as exc:                       # noqa: BLE001
            out[mod] = f"unavailable: {type(exc).__name__}"
    return out


# ------------------------------------------------------------------ discover
def discover(args) -> int:
    """PUBLIC_MARKET_DISCOVERY_CANDIDATES -- a convenience, not evidence.

    This mode captures nothing and writes nothing. It prints token ids for you
    to choose from; you can equally paste ids you already have and skip it.

    Two honest caveats, neither of which affects the capture itself:

    * `/sampling-markets` appears in the ARCHIVED py-clob-client. The current
      unified SDK does not reference it at all, so whether the venue still
      serves it is NOT_IDENTIFIED. If it 404s, that is the answer, and this
      mode reports the status rather than falling back anywhere.
    * Whatever it returns is a list of PUBLIC MARKET DISCOVERY CANDIDATES.
      "Sampling" is the venue's own word and its current meaning is not
      established here. These rows are NOT claimed to be liquid, rewarded, or
      actively traded -- you judge that yourself before choosing tokens.

    The current SDK's own market discovery runs against a DIFFERENT host
    (gamma-api.polymarket.com, `/markets/keyset`). That host is deliberately
    not in this script's two-entry allow-list: discovery is a convenience and
    is not worth widening the network surface of the instrument.
    """
    import httpx

    url = f"{args.rest_url.rstrip('/')}/sampling-markets"
    _assert_allowed(url, allow_local=args.i_am_running_the_self_tests)
    try:
        with httpx.Client(timeout=args.rest_timeout, follow_redirects=False) as c:
            resp = c.get(url, params={"next_cursor": "MA=="})
    except Exception as exc:                           # noqa: BLE001
        print(f"discovery failed: {type(exc).__name__}: {exc}")
        return 2
    if resp.status_code != 200:
        print(f"discovery failed: HTTP {resp.status_code}")
        if resp.status_code == 404:
            print("this endpoint is in the ARCHIVED client and is not in the "
                  "current SDK; a 404 means the venue no longer serves it. "
                  "Pass --token-id values directly instead.")
        print(resp.text[:500])
        return 2

    body = resp.json()
    markets = body.get("data") or body.get("markets") or []
    shown = 0
    print(f"{len(markets)} PUBLIC_MARKET_DISCOVERY_CANDIDATES returned "
          f"(not a liquidity claim). Sports-looking, still open:\n")
    for m in markets:
        if not m.get("active", True) or m.get("closed"):
            continue
        question = (m.get("question") or "")[:78]
        tags = " ".join(str(t) for t in (m.get("tags") or []))
        looks_sport = any(w in (question + " " + tags).lower() for w in (
            "nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball",
            "tennis", "vs.", " vs ", "match", "game", "win"))
        if not looks_sport:
            continue
        for tok in (m.get("tokens") or []):
            tid = tok.get("token_id")
            if tid:
                print(f"  --token-id {tid}    # {tok.get('outcome','?')} | {question}")
        shown += 1
        if shown >= args.discover_limit:
            break
    if shown == 0:
        print("nothing matched the sports filter. Re-run with --discover-limit "
              "raised, or pick any token_id from the full response yourself.")
    print("\nPick 3-5 token ids from DIFFERENT markets that look actively "
          "traded, then run the capture with those --token-id values.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="RUN 83.6B CLOB public passive capture (recorder only).")
    p.add_argument("--token-id", action="append", default=[],
                   help="CLOB token id to subscribe to. Repeatable. 3-5 advised.")
    p.add_argument("--tokens-file",
                   help="file with one token id per line (combined with --token-id)")
    p.add_argument("--sessions", type=int, default=3)
    p.add_argument("--session-seconds", type=float, default=75.0)
    p.add_argument("--pause-seconds", type=float, default=5.0)
    p.add_argument("--rest-interval", type=float, default=15.0)
    p.add_argument("--rest-timeout", type=float, default=15.0)
    p.add_argument("--open-timeout", type=float, default=20.0)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--ws-url", default=DEFAULT_WS_URL)
    p.add_argument("--rest-url", default=DEFAULT_REST_URL)
    p.add_argument("--heartbeat-interval", type=float, default=HEARTBEAT_INTERVAL_S,
                   help=("seconds between application-level PING frames, as the "
                         "current SDK sends them (default %(default)s). 0 disables "
                         "the heartbeat; the venue may then close an idle socket, "
                         "which would contaminate the reconnect experiment."))
    p.add_argument("--custom-feature-enabled", action="store_true",
                   help=("send custom_feature_enabled=true in the subscribe frame. "
                         "The current SDK's own default is false, which is this "
                         "script's default too. The flag gates the best_bid_ask / "
                         "new_market / market_resolved event classes; its effect on "
                         "book and price_change is not established, so turning it "
                         "on is a second, separate capture, not the baseline one."))
    p.add_argument("--discover", action="store_true",
                   help="list candidate token ids and exit (captures nothing)")
    p.add_argument("--discover-limit", type=int, default=12)
    p.add_argument("--i-am-running-the-self-tests", action="store_true",
                   help=argparse.SUPPRESS)   # offline test rig only
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.tokens_file:
        extra = [ln.strip() for ln in Path(args.tokens_file).read_text().splitlines()]
        args.token_id = list(args.token_id) + [t for t in extra if t and not t.startswith("#")]

    if args.discover:
        return discover(args)

    if not args.token_id:
        print(EXAMPLE_ONLY_NOTE)
        return 2

    # Fail closed BEFORE creating anything.
    try:
        _assert_allowed(args.ws_url, allow_local=args.i_am_running_the_self_tests)
        _assert_allowed(args.rest_url, allow_local=args.i_am_running_the_self_tests)
    except HostNotAllowed as exc:
        print(str(exc))
        return 3

    if args.out_dir is None:
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        args.out_dir = f"run836b_capture_{stamp}"

    cap = Capture(args)
    print(f"capture -> {cap.out}")
    print(f"  tokens   : {len(args.token_id)}")
    print(f"  sessions : {args.sessions} x {args.session_seconds:.0f}s "
          f"(pause {args.pause_seconds:.0f}s)")
    print(f"  REST poll: every {args.rest_interval:.0f}s per token")
    print(f"  heartbeat: {HEARTBEAT_TEXT} every {args.heartbeat_interval:.0f}s"
          if args.heartbeat_interval > 0 else "  heartbeat: DISABLED")
    print(f"  custom_feature_enabled: {bool(args.custom_feature_enabled)}")
    try:
        asyncio.run(cap.run())
    except KeyboardInterrupt:
        cap.error("main", "KeyboardInterrupt -- finalising what was captured")
        print("\ninterrupted; finalising")
    manifest = cap.finalise()

    print("\n--- capture finished ---")
    print(f"  sessions opened : {manifest['sessions_opened']}/{manifest['sessions_attempted']}")
    print(f"  ws frames       : {manifest['websocket_frames']}")
    print(f"  REST ok/failed  : {manifest['rest_successful']}/{manifest['rest_failed']}")
    print(f"  errors          : {manifest['errors_recorded']}")
    print(f"  CAPTURE_COMPLETE_FOR_RECONSTRUCTION = "
          f"{manifest['CAPTURE_COMPLETE_FOR_RECONSTRUCTION']}")
    for r in manifest["incomplete_reasons"]:
        print(f"    - {r}")
    print(f"\n  files in {cap.out}/ (checksummed; do not edit)")
    return 0 if manifest["CAPTURE_COMPLETE_FOR_RECONSTRUCTION"] == "YES" else 1


if __name__ == "__main__":
    sys.exit(main())
