"""Copy-trade feasibility probes.

Workflow being measured: whale fills on-venue → our platform detects it
(Path A ~1-3s / Path B ~3-10s) → an executor would act NOW. At exactly that
moment we snapshot the residual CLOB order book and compute what price WE
could get for standard clip sizes.

Residual ROI model: if the whale's measured edge is E at his price p_h (e.g.
E = 2.3% profit per dollar staked), the expected payout per share is
p_h * (1 + E). Copying at our achievable price p_us yields
    residual_roi = (1 + E) * p_h / p_us - 1
so every cent of slippage is measured directly against the edge.

Probes are BUY-side only (the reference strategy is buy-only) and only for
fresh detections — a probe minutes after the fill measures nothing real.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx

from .config import settings
from .db import get_pool

log = logging.getLogger(__name__)

_SEM = asyncio.Semaphore(4)
CLIPS = (1_000.0, 5_000.0)
MAX_REACTION_S = 120.0  # older detections (reconciler catch-ups) measure nothing


def compute_book_metrics(
    asks: list[tuple[float, float]], his_price: float, assumed_edge: float
) -> dict:
    """Pure math over ask levels [(price, shares)...] sorted ascending."""
    out: dict = {"best_ask": None, "best_ask_usd": None, "slippage_cents": None}
    if not asks:
        return out
    best_price, best_size = asks[0]
    out["best_ask"] = best_price
    out["best_ask_usd"] = round(best_price * best_size, 2)
    out["slippage_cents"] = round((best_price - his_price) * 100, 4)

    for clip in CLIPS:
        key = f"{int(clip/1000)}k"
        remaining = clip
        cost = 0.0
        shares = 0.0
        for price, size in asks:
            usd = price * size
            take = min(usd, remaining)
            if price > 0:
                shares += take / price
            cost += take
            remaining -= take
            if remaining <= 1e-9:
                break
        fillable = remaining <= 1e-9
        vwap = cost / shares if shares > 0 else None
        out[f"fillable_{key}"] = fillable
        out[f"vwap_{key}"] = round(vwap, 6) if vwap else None
        out[f"residual_roi_{key}"] = (
            round((1 + assumed_edge) * his_price / vwap - 1, 6) if (fillable and vwap) else None
        )
    return out


async def probe_trade(payload: dict) -> None:
    """Fire-and-forget probe for one freshly detected trade."""
    cfg = settings()
    if not cfg.copy_probe_enabled or payload.get("side") != "BUY":
        return
    latency = payload.get("latency_s")
    if latency is not None and float(latency) > MAX_REACTION_S:
        return
    try:
        async with _SEM:
            probe_at = datetime.now(tz=timezone.utc)
            asks: list[tuple[float, float]] = []
            error = None
            try:
                async with httpx.AsyncClient(base_url=cfg.clob_api_base, timeout=8) as http:
                    resp = await http.get("/book", params={"token_id": str(payload["asset"])})
                if resp.status_code == 200:
                    d = resp.json()
                    asks = sorted(
                        ((float(x["price"]), float(x["size"])) for x in d.get("asks") or []),
                        key=lambda t: t[0],
                    )
                else:
                    error = f"http_{resp.status_code}"
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                error = f"{type(exc).__name__}"

            his_price = float(payload["price"])
            metrics = compute_book_metrics(asks, his_price, cfg.copy_probe_assumed_edge)
            fill_ts = payload.get("ts")
            reaction = None
            if fill_ts:
                try:
                    fill_dt = datetime.fromisoformat(str(fill_ts).replace("Z", "+00:00"))
                    reaction = round((probe_at - fill_dt).total_seconds(), 3)
                except ValueError:
                    pass

            pool = await get_pool()
            await pool.execute(
                """
                INSERT INTO copy_probes
                    (trade_id, whale_id, username, asset, side, his_price, his_size,
                     his_notional, fill_ts, probe_at, reaction_s, book_ok, best_ask,
                     best_ask_usd, slippage_cents, vwap_1k, vwap_5k, fillable_1k,
                     fillable_5k, residual_roi_1k, residual_roi_5k, depth, error)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
                        $18,$19,$20,$21,$22::jsonb,$23)
                """,
                payload.get("id"), payload["whale_id"], payload.get("whale_username"),
                str(payload["asset"]), payload["side"], his_price,
                payload.get("size"), payload.get("notional"),
                datetime.fromisoformat(str(fill_ts).replace("Z", "+00:00")) if fill_ts else None,
                probe_at, reaction, bool(asks), metrics["best_ask"], metrics["best_ask_usd"],
                metrics["slippage_cents"], metrics.get("vwap_1k"), metrics.get("vwap_5k"),
                metrics.get("fillable_1k"), metrics.get("fillable_5k"),
                metrics.get("residual_roi_1k"), metrics.get("residual_roi_5k"),
                json.dumps(asks[:8]), error,
            )
    except Exception:  # noqa: BLE001 — probing must never disturb ingestion
        log.exception("copy probe failed for trade %s", payload.get("id"))
