"""THE PAPER SLEEVE ECONOMICS: GET /api/command/profitability/sleeves
(GET only, COMMAND auth via agents_core.require_read). READ ONLY.

The profitability cockpit DEFAULTS to the INVESTMENT sleeve: a training
(exploration) loss is a research cost and a training win is not production
alpha, so neither may move the investment economics.

    ?sleeve=INVESTMENT (default) | TRAINING | BENCHMARK | UNCLASSIFIED |
            COMBINED (the whole paper book, every sleeve)

ANSWERS (the profitability envelope):
    {label: RESEARCH, authority: SHADOW_NO_AUTHORITY, status, why,
     computed_at, disclosure,
     data: {sleeve, sleeves_available, equity_method,
            live: {...the selected sleeve's ledger figures: realized,
                   unrealized, exposure, marks + last genuine mark update...},
            sleeves: {INVESTMENT|TRAINING|BENCHMARK|UNCLASSIFIED: {...}},
            accounting: {starting cash + every sleeve == account equity},
            north_star: {METRIC: {value, sample_n, ci_low, ci_high, status,
                                  why, unit, ...}},   # this sleeve's closed
                                                      # positions only
            capital: {realized_net_profit_usd, realized_sample, window_days,
                      capital_locked_positions_usd, ...},
            research_source: ..., research_as_of: ...}}

`live` comes from the paper ledger exactly as the equity wall reads it
(bettor_paper_sleeves.sleeve_book). `north_star` / `capital` re-run the
research layer's PURE metric functions (profitability.metrics.compute) over
pos_economics_latest rows of the PAPER book whose group belongs to the
sleeve -- the hourly runner's book-level metrics are not reused for a
sleeve, so INVESTMENT never includes a TRAINING position.

Everything runs inside a READ ONLY transaction under a statement timeout.
This module imports no order, venue, execution or funded module and writes
nothing.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, Query

from .agents_core import _pool, require_read

router = APIRouter()
PATH = "/api/command/profitability/sleeves"
STATEMENT_TIMEOUT_MS = 6000
CACHE_S = 15.0
WINDOW_DAYS = 30.0
LOOKBACK_DAYS = 60.0
CHOICES = ("INVESTMENT", "TRAINING", "BENCHMARK", "UNCLASSIFIED", "COMBINED")
_CACHE: dict = {}


def _ep(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    if type(v).__name__ == "Decimal":
        return float(v)
    return v


def econ_rows(rows: list) -> list:
    out = []
    for r in rows:
        d = {k: _ep(v) for k, v in dict(r).items()}
        out.append(d)
    return out


def select_rows(econs: list, sleeve_of_group: dict, sleeve: str) -> list:
    """The sleeve's PAPER economics rows (a group without a durable
    classification is UNCLASSIFIED -- never INVESTMENT). Pure."""
    if sleeve == "COMBINED":
        return [e for e in econs if e.get("book") == "PAPER"]
    return [e for e in econs if e.get("book") == "PAPER"
            and sleeve_of_group.get(e.get("group_id"), "UNCLASSIFIED")
            == sleeve]


def capital_summary(rows: list, *, now: float,
                    window_days: float = WINDOW_DAYS) -> dict:
    """The sleeve's capital picture from its own positions. Pure. A sleeve
    has no separate capital (one shared cash ledger): idle capital is
    UNMEASURED with that reason, never invented."""
    lo = now - window_days * 86400.0
    open_ = [e for e in rows if e.get("state") == "OPEN"]
    closed = [e for e in rows if e.get("state") == "CLOSED"]
    win = [e for e in closed if (e.get("released_at") or 0) >= lo
           and e.get("net_profit_usd") is not None]
    rp = [(e["net_profit_usd"], e.get("capital_hours") or 0.0) for e in win]
    ch = sum(b for _, b in rp)
    unmeasured = {"idle_capital_usd":
                  "SLEEVE_HAS_NO_SEPARATE_CAPITAL_ONE_SHARED_CASH_LEDGER",
                  "capital_utilization":
                  "SLEEVE_HAS_NO_SEPARATE_CAPITAL_ONE_SHARED_CASH_LEDGER"}
    if not rp:
        unmeasured["realized_net_profit_usd"] = "NO_POSITION_CLOSED_IN_WINDOW"
    return {
        "positions": len(rows), "open_positions": len(open_),
        "closed_positions": len(closed),
        "capital_locked_positions_usd": round(sum(
            e.get("open_cost_basis_usd") or 0.0 for e in open_), 2),
        "window_days": window_days, "window_start": lo,
        "realized_sample": len(rp),
        "realized_net_profit_usd": (round(sum(a for a, _ in rp), 2)
                                    if rp else None),
        "realized_capital_hours": round(ch, 6) if rp else None,
        "REALIZED_PROFIT_PER_CAPITAL_HOUR": (
            round(sum(a for a, _ in rp) / ch, 9) if rp and ch > 0 else None),
        "idle_capital_usd": None, "capital_utilization": None,
        "unmeasured": unmeasured, "computed_at": now,
        "basis": ("pos_economics_latest (PAPER) rows of this sleeve's "
                  "groups; net profit of positions released in the window"),
    }


async def _read(conn, sleeve: str, now: float) -> dict:
    from .. import bettor_paper_ledger as L
    from .. import bettor_paper_sleeves as SL
    from ..profitability import metrics as MT
    tr = conn.transaction() if conn.is_in_transaction() else \
        conn.transaction(readonly=True)
    await tr.start()
    try:
        await conn.execute("SET LOCAL statement_timeout = %d"
                           % STATEMENT_TIMEOUT_MS)
        if not await SL.schema(conn):
            return {"status": "UNAVAILABLE", "why": SL.R_NO_SCHEMA}
        book = await SL.sleeve_book(conn, L.ACCOUNT_ID, now=now)
        groups = {g: c["sleeve"] for g, c in
                  (await SL.classifications(conn, L.ACCOUNT_ID)).items()}
        has_pos = bool(await conn.fetchval(
            "SELECT to_regclass('pos_economics_latest') IS NOT NULL"))
        econs, research_as_of = [], None
        if has_pos:
            econs = econ_rows(await conn.fetch(
                "SELECT book, position_key, group_id, strategy, state, "
                "       net_profit_usd, expected_net_profit_usd, "
                "       capital_committed_usd, capital_hours, "
                "       open_cost_basis_usd, predicted_edge_per_dollar, "
                "       realized_edge_per_dollar, released_at, last_event_at,"
                "       first_fill_at, computed_at "
                "  FROM pos_economics_latest WHERE book = 'PAPER'"))
            research_as_of = max((e.get("computed_at") or 0 for e in econs),
                                 default=None) or None
    finally:
        await tr.rollback()
    rows = select_rows(econs, groups, sleeve)
    ns = None
    if has_pos:
        ms = await asyncio.to_thread(MT.compute, rows, book="PAPER", now=now,
                                     lookback_days=LOOKBACK_DAYS)
        ns = {}
        for m in ms:
            m["computed_at"] = now
            m["data_as_of"] = max((e.get("last_event_at") or 0
                                   for e in rows), default=None) or None
            m["sleeve"] = sleeve
            ns[m["metric"]] = m
    live = None
    if book.get("status") == "OK":
        live = (book["accounting"] if sleeve == "COMBINED"
                else book["sleeves"][sleeve])
    return {
        "status": "OK" if book.get("status") == "OK" else "UNAVAILABLE",
        "why": book.get("why"),
        "data": {
            "sleeve": sleeve, "sleeves_available": list(CHOICES),
            "default_sleeve": "INVESTMENT",
            "equity_method": SL.EQUITY_METHOD,
            "classifier_version": SL.CLASSIFIER_VERSION,
            "live": live,
            "sleeves": book.get("sleeves"),
            "accounting": book.get("accounting"),
            "last_genuine_mark_update_at":
                book.get("last_genuine_mark_update_at"),
            "north_star": ns,
            "north_star_why": None if has_pos else
            "MIGRATION_216_NOT_APPLIED",
            "capital": capital_summary(rows, now=now) if has_pos else None,
            "research_rows": len(rows),
            "research_as_of": research_as_of,
            "research_source": ("pos_economics_latest (PAPER) filtered to "
                                "this sleeve's groups, metrics recomputed "
                                "with profitability.metrics.compute"),
            "live_source": book.get("source"),
            "labels": {"TRAINING": {"loss": "RESEARCH COST",
                                    "win": "NOT PRODUCTION ALPHA"},
                       "BENCHMARK": {"win": "NOT PRODUCTION ALPHA"},
                       "UNCLASSIFIED": {"note": "never counted as "
                                                "investment"}},
        }}


@router.get(PATH, dependencies=[Depends(require_read)])
async def profitability_sleeves(
        sleeve: str = Query(default="INVESTMENT",
                            pattern="^(INVESTMENT|TRAINING|BENCHMARK|"
                                    "UNCLASSIFIED|COMBINED)$")) -> dict:
    from ..profitability import common as C
    now = time.time()
    hit = _CACHE.get(sleeve)
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            got = await _read(conn, sleeve, now)
    except Exception as exc:                                    # noqa: BLE001
        return C.envelope("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                                     str(exc)[:160]),
                          data=None, sleeve=sleeve)
    out = C.envelope(got["status"], got.get("why"), computed_at=now,
                     data=got.get("data"), sleeve=sleeve,
                     summed_across_books=False)
    _CACHE[sleeve] = (now, out)
    return out
