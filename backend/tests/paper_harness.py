"""Shared fixtures for the paper-trading proofs. ALL MARKET DATA HERE IS
SYNTHETIC TEST DATA written into a scratch test database; nothing touches the
live paper account (`paper_acct_main`) except the initialization proofs, and
no test writes a trade into it."""
from __future__ import annotations

import json
import os
import uuid

import asyncpg

from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_session as S
from sportsassets import bettor_paper_simulator as SIM

DSN = os.environ.get("RN1X_TEST_DSN", "")
T0 = 1_790_300_000.0
ZERO_FEES_BASIS = "ZERO_FEES_FOR_THE_PROOF"


def zero_fee(qty, price, at=None):
    return (0.0, ZERO_FEES_BASIS)


def flat_fee(per_contract):
    def fn(qty, price, at=None):
        return (round(float(qty) * float(per_contract), 6), "FLAT_TEST_FEE")
    return fn


async def connect():
    return await asyncpg.connect(DSN)


def new_account_id(tag: str = "t") -> str:
    return "paper_test_%s_%s" % (tag, uuid.uuid4().hex[:10])


async def new_account(conn, tag: str = "t", *, config: dict | None = None,
                      now: float = T0) -> dict:
    """A fresh fictional $500,000 paper account with its own session."""
    acct = new_account_id(tag)
    cfg = dict(config or S.default_config())
    sess = await S.ensure_session(conn, account_id=acct, config=cfg, now=now)
    assert sess["ok"], sess
    return {"account_id": acct, "session_id": sess["session_id"],
            "config": cfg}


def md(*, bids=(), offers=()) -> dict:
    """A venue-shaped marketData: levels {"px": {"value": "0.52"},
    "qty": "25"} (the shape bettor_book_snapshot reads)."""
    return {"bids": [{"px": {"value": "%.2f" % p}, "qty": "%s" % q}
                     for p, q in bids],
            "offers": [{"px": {"value": "%.2f" % p}, "qty": "%s" % q}
                       for p, q in offers]}


async def observe(conn, slug: str, at: float, **book) -> int:
    got = await SIM.record_book(
        conn, slug=slug, read={"marketData": md(**book), "observed_at": at},
        source="TEST_FIXTURE_SYNTHETIC_BOOK", read_basis="TEST")
    return got["obs_id"]


def order(acct: dict, *, key: str, slug: str = "test-mkt-yankees",
          direction: str = "BUY", holding_side: str = "LONG",
          qty: float, limit: float, role: str = "ENTRY",
          group_id: str | None = None, order_type: str = "MARKETABLE",
          tif: str = "IOC", allow_partial: bool = True, at: float = T0,
          delay: float = 2.0, ttl: float = 90.0, fixture: str = "fx-1",
          wire: float | None = None, queue_ahead=None,
          queue_basis=None) -> dict:
    key = "%s:%s" % (acct["account_id"], key)
    return {"idempotency_key": key, "account_id": acct["account_id"],
            "session_id": acct["session_id"],
            "group_id": group_id or "paper_group_%s" % key,
            "role": role, "direction": direction,
            "holding_side": holding_side,
            "intent": SIM.intent_of(direction, holding_side),
            "us_market_slug": slug, "fixture": fixture,
            "label": {"participant": "TEST TEAM", "market_type": "MONEYLINE",
                      "side": holding_side, "period": "FT",
                      "competition": "TEST", "event_date": "2026-10-01"},
            "order_type": order_type, "time_in_force": tif,
            "allow_partial": allow_partial, "qty": qty, "limit_price": limit,
            "wire_price": wire if wire is not None else (
                limit if holding_side == "LONG" else round(1 - limit, 6)),
            "decision_id": None, "decided_at": at, "eligible_at": at + delay,
            "expires_at": at + ttl, "queue_ahead_qty": queue_ahead,
            "queue_basis": queue_basis,
            "simulator_version": SIM.VERSION}


async def ledger_kinds(conn, account_id: str) -> list:
    return [r["kind"] for r in await conn.fetch(
        "SELECT kind FROM paper_ledger WHERE account_id=$1 ORDER BY seq",
        account_id)]


async def funded_table_counts(conn) -> dict:
    """Row counts of every funded table, to prove paper activity wrote none."""
    names = [r["relname"] for r in await conn.fetch(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
        "  ON n.oid = c.relnamespace WHERE n.nspname='public' "
        "   AND c.relkind='r' AND c.relname LIKE 'bettor_funded%'")]
    out = {}
    for n in sorted(names):
        out[n] = await conn.fetchval('SELECT count(*) FROM "%s"' % n)
    return out


def j(v):
    return json.loads(v) if isinstance(v, str) else v
