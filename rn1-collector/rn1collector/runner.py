"""Collector entry point, used as a REAL PROCESS by the offline end-to-end test.

It mints one handshake from the broker, opens the market socket, reads a few
frames through the real decoder, and writes a JSON result file. It is the same
code path a deployed collector would take; only the destination differs, and in
the test that destination is a local fake endpoint that verifies the signature
itself.

WHAT IT WILL NOT DO, BY CONSTRUCTION RATHER THAN BY POLICY:

  * it cannot read a secret key -- there is no env var here for one, and no
    signing primitive is importable in this artifact;
  * it cannot ask for a different path -- the broker's mint operation has no
    parameter for one;
  * it cannot emit an order -- there is no order client in this artifact.

NOTHING IN THIS FILE NAMES A REAL VENUE HOST. The URL arrives as an argument, so
running it against production requires someone to type a production address.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import pathlib
import sys

import httpx
import websockets

from sportsassets.obs import clock, handshake, pmus_stream, streamstate
from sportsassets.obs.config import OFFSETS

from . import transport

log = logging.getLogger("rn1collector")


async def run_once(*, ws_url: str, market_slug: str, frames: int,
                   out_path: pathlib.Path) -> int:
    result: dict = {
        "ok": False,
        "underlying_credential_authority": handshake.UNDERLYING_CREDENTIAL_AUTHORITY,
        "collector_signing_capability": handshake.COLLECTOR_SIGNING_CAPABILITY,
        "signature_host_binding": handshake.SIGNATURE_HOST_BINDING,
        "host_restriction": handshake.HOST_RESTRICTION,
        "offsets": [lbl for lbl, _ in OFFSETS],
        "frames": 0,
        "mint_ids": [],
        "secret_seen": False,        # see the assertion below
        "orders_emitted": 0,         # there is no code path that could raise it
        "error": None,
    }

    # THE SECRET IS NOT REACHABLE FROM HERE, and the runner says so in its own
    # output rather than leaving it to be inferred. `handshake` exposes only a
    # broker URL and this collector's caller token; there is no attribute on it
    # that could hold venue key material.
    result["secret_seen"] = any(
        hasattr(handshake, name)
        for name in ("secret_key", "SECRET_KEY", "signing_key", "sign"))

    state = pmus_stream.PmusStreamState(feed_session_id="offline-e2e")

    try:
        async with httpx.AsyncClient() as http:
            ws = await transport.open_pmus_market_socket(
                http, websockets.connect, url=ws_url)
            async with ws:
                await ws.send(json.dumps({
                    "subscribe": {"requestId": "rn1-1",
                                  "subscriptionType": "SUBSCRIPTION_TYPE_MARKET_DATA",
                                  "marketSlugs": [market_slug]}}))
                anchor = clock.now()
                for _ in range(frames):
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    receive = clock.now()     # before parsing
                    state.on_frame(json.loads(raw), receive=receive)
                    result["frames"] += 1

                hist = state.history(market_slug)
                obs = streamstate.capture_zero_ms(
                    hist, observation_slot_id="offline-e2e-0ms",
                    observation_channel=pmus_stream.CHANNEL,
                    anchor=anchor, stale_tolerance_s=5.0)
                result["zero_ms_status"] = obs.status
                result["zero_ms_age_ms"] = obs.state_age_at_receipt_ms
                result["depth_authority"] = (
                    obs.state.depth_authority if obs.state else None)
        result["ok"] = result["frames"] == frames
    except Exception as exc:                    # noqa: BLE001 - reported, never raised
        result["error"] = f"{type(exc).__name__}: {exc}"[:300]

    out_path.write_text(json.dumps(result, indent=2, default=str))
    return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws-url", required=True)
    ap.add_argument("--market-slug", default="offline-test-market")
    ap.add_argument("--frames", type=int, default=3)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    return asyncio.run(run_once(ws_url=args.ws_url,
                                market_slug=args.market_slug,
                                frames=args.frames,
                                out_path=pathlib.Path(args.out)))


if __name__ == "__main__":
    sys.exit(main())
