"""A local fake PMUS websocket endpoint that VERIFIES THE SIGNATURE ITSELF.

This is the independent half of the offline end-to-end proof. It does not
import the broker; it re-derives the venue's scheme from first principles --

    message = timestamp + method + path
    Ed25519 verify with the PUBLIC key

-- so a handshake succeeding here means the broker produced something a
third party, holding only the public key, accepts. If the broker and this file
agreed only because they shared code, the test would prove nothing.

WHAT IT SERVES, AND WHAT IT REFUSES:

    /v1/ws/markets   accepts a signature minted for GET /v1/ws/markets
    /v1/ws/private   the SAME material is refused -- the path is signed, so a
                     market handshake does not authenticate the private socket
    /v1/orders       likewise refused
    any method other than GET   refused

Those three refusals are the point. They are not policy in this file: the
verifier simply recomputes `timestamp + method + path` for the path actually
requested, and a signature minted for a different path does not verify.

It also records every attempt to a JSON file so the test can assert on what was
presented, including that a retry used DIFFERENT material from the first try.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import pathlib
import time

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
from websockets.asyncio.server import serve

MARKET_PATH = "/v1/ws/markets"
PRIVATE_PATH = "/v1/ws/private"
ORDERS_PATH = "/v1/orders"

# How far a timestamp may be from now and still be considered. The real venue's
# tolerance is UNDOCUMENTED and unknown -- this number is the fake's own choice,
# and nothing in the design depends on the real one matching it.
CLOCK_TOLERANCE_MS = 30_000


class Recorder:
    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.attempts: list[dict] = []
        self.frames_out = 0
        self.frames_in: list[str] = []

    def record(self, **kw) -> None:
        self.attempts.append(kw)
        self.flush()

    def flush(self) -> None:
        self.path.write_text(json.dumps({
            "attempts": self.attempts,
            "frames_out": self.frames_out,
            "frames_in": self.frames_in,
        }, indent=2))


def verify(headers, path: str, method: str, verify_key: VerifyKey) -> tuple[bool, str]:
    """Re-derive the venue scheme independently. Returns (ok, reason)."""
    key_id = headers.get("X-PM-Access-Key")
    ts = headers.get("X-PM-Timestamp")
    sig = headers.get("X-PM-Signature")
    if not (key_id and ts and sig):
        return False, "AUTH_HEADERS_ABSENT"
    try:
        drift = abs(int(time.time() * 1000) - int(ts))
    except ValueError:
        return False, "TIMESTAMP_NOT_AN_INTEGER"
    if drift > CLOCK_TOLERANCE_MS:
        return False, f"TIMESTAMP_OUT_OF_TOLERANCE:{drift}"

    message = f"{ts}{method}{path}".encode()
    try:
        verify_key.verify(message, base64.b64decode(sig))
    except (BadSignatureError, ValueError):
        # THE CENTRAL REFUSAL. A signature minted for GET /v1/ws/markets does
        # not verify against "GET/v1/orders" or "POST/v1/ws/markets", because
        # the path and the method are inside the signed bytes.
        return False, "SIGNATURE_DOES_NOT_VERIFY_FOR_THIS_METHOD_AND_PATH"
    return True, "VERIFIED"


async def main_async(args) -> None:
    verify_key = VerifyKey(base64.b64decode(args.public_key))
    rec = Recorder(pathlib.Path(args.record))
    fail_first = {"n": args.fail_first}

    async def process_request(connection, request):
        path = request.path.split("?")[0]
        # A websocket upgrade is always a GET at the HTTP layer. `--method` lets
        # the test drive the "different method" case explicitly.
        method = args.method
        ok, reason = verify(request.headers, path, method, verify_key)
        sig = request.headers.get("X-PM-Signature", "")
        rec.record(path=path, method=method, ok=ok, reason=reason,
                   # a short fingerprint, never the signature itself
                   signature_fp=sig[:12], ts=request.headers.get("X-PM-Timestamp"))
        if not ok:
            return connection.respond(401, reason + "\n")
        if path != MARKET_PATH:
            return connection.respond(403, "PATH_NOT_SERVED\n")
        if fail_first["n"] > 0:
            # Drive the re-mint path: accept the signature, then refuse the
            # connection, so the collector must come back with NEW material.
            fail_first["n"] -= 1
            rec.record(path=path, method=method, ok=False,
                       reason="FORCED_CONNECT_FAILURE", signature_fp=sig[:12],
                       ts=request.headers.get("X-PM-Timestamp"))
            return connection.respond(503, "FORCED_CONNECT_FAILURE\n")
        return None

    async def handler(ws):
        async for raw in ws:
            rec.frames_in.append(raw if isinstance(raw, str) else "<binary>")
            rec.flush()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if "subscribe" in msg:
                slugs = msg["subscribe"].get("marketSlugs") or ["offline-test-market"]
                for i in range(args.frames):
                    await ws.send(json.dumps({"marketData": {
                        "marketSlug": slugs[0],
                        "bids": [{"px": f"0.4{i}", "qty": "500"}],
                        "offers": [{"px": f"0.4{i + 2}", "qty": "400"}],
                        "state": "OPEN", "stats": {},
                        "transactTime": str(int(time.time() * 1000))}}))
                    rec.frames_out += 1
                rec.flush()

    async with serve(handler, "127.0.0.1", args.port,
                     process_request=process_request):
        pathlib.Path(args.ready).write_text("ready")
        await asyncio.Future()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--public-key", required=True)
    ap.add_argument("--record", required=True)
    ap.add_argument("--ready", required=True)
    ap.add_argument("--frames", type=int, default=3)
    ap.add_argument("--fail-first", type=int, default=0)
    ap.add_argument("--method", default="GET")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
