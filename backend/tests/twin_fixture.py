"""Synthetic recorded streams for the RESEARCH twin proofs (tests/test_twin_*).

ALL DATA HERE IS SYNTHETIC TEST DATA. The pure builders produce the raw
shape sportsassets.twin.reads.paper_raw returns (so the engine's builder is
exercised too); the DB helpers reuse tests/intel_fixture.py and write into a
scratch database inside a transaction each test rolls back.
"""
from __future__ import annotations

import copy

from sportsassets.twin import engine as E

T0 = 1_790_000_000.0          # a fixed instant: the proofs are clock-free
SPORT = "baseball"


def decision(did, at, *, verdict="ENTER", p=0.62, vwap=0.52, qty=100.0,
             fees=1.0, depth=500.0, slug=None, side="LONG",
             strategy="SYN_STRATEGY", valuation_id=None, event_key=None):
    econ = {"depth_within_limit": depth}
    if vwap is not None:
        econ["acquisition"] = {"vwap": vwap, "fees_usd": fees}
    return {"decision_id": did, "at": float(at), "verdict": verdict,
            "refusal": None if verdict == "ENTER" else "SYN_REFUSAL",
            "valuation_id": valuation_id,
            "us_market_slug": slug or ("syn-mkt-" + did),
            "holding_side": side, "p_pinnacle": p, "p_blended": None,
            "p_internal": None, "limit_price": vwap, "proposed_qty": qty,
            "economics": econ, "strategy": strategy,
            "label": {"event_key": event_key or ("e-" + did)},
            "book_obs_id": None, "account_id": "syn"}


def fill(gid, at, *, qty, price, fee=0.0, role="ENTRY", direction="BUY",
         slug, side="LONG", fid=None):
    return {"fill_id": fid or "f-%s-%s-%s" % (gid, role, at),
            "group_id": gid, "role": role, "direction": direction,
            "holding_side": side, "us_market_slug": slug, "qty": qty,
            "price": price, "fee_usd": fee, "gross_usd": qty * price,
            "at": float(at)}


def settlement(gid, *, slug, side="LONG", qty, payout, at, outcome=None):
    return {"position_key": "pk-%s-%s-%s" % (gid, slug, side),
            "group_id": gid, "us_market_slug": slug, "holding_side": side,
            "qty": qty, "outcome": outcome or ("WON" if payout >= 1 else
                                               "LOST"),
            "payout_per_contract": payout, "at": float(at)}


def book(slug, at, *, bids=(), offers=(), obs_id=None):
    b = sorted(((float(p), float(q)) for p, q in bids), key=lambda x: -x[0])
    o = sorted(((float(p), float(q)) for p, q in offers), key=lambda x: x[0])
    bb, bo = (b[0][0] if b else None), (o[0][0] if o else None)
    return {"obs_id": obs_id or int(at * 10) % 2_000_000_000, "slug": slug,
            "at": float(at), "best_bid": bb, "best_offer": bo,
            "spread": None if None in (bb, bo) else bo - bb,
            "mid": None if None in (bb, bo) else (bb + bo) / 2,
            "depth_usd": sum(p * q for p, q in b[:5] + o[:5]),
            "bids": b, "offers": o}


def scenario_raw():
    """A small recorded world:
      A  ENTER p=.62 plan .52 fee 1/100 -> filled 100 @ .53 fee 1, held,
         settled WON (realized = 100*(1-.53) - 1 = 46)
      B  REFUSED p=.55 plan .52 fee .01/c -> never filled; its contract
         later paid (oracle payoff 1 via another group's settlement)
      C  ENTER p=.60 plan .50 -> filled 100 @ .50 fee 1, SOLD 100 @ .60
         fee 1 at +100 s, settled LOST (realized 8; hold -51)
      D  ENTER p=.58 plan .55 -> filled 100 @ .56 fee 1, open, unsettled
    """
    t = T0
    a = decision("A", t, p=0.62, vwap=0.52, fees=1.0, slug="syn-a")
    b = decision("B", t + 10, verdict="REFUSE", p=0.55, vwap=0.52,
                 fees=1.0, slug="syn-b")
    c = decision("C", t + 20, p=0.60, vwap=0.50, fees=1.0, slug="syn-c",
                 depth=150.0)
    d = decision("D", t + 30, p=0.58, vwap=0.55, fees=1.0, slug="syn-d")
    fills = [fill("gA", t + 2, qty=100, price=0.53, fee=1.0, slug="syn-a"),
             fill("gC", t + 22, qty=100, price=0.50, fee=1.0, slug="syn-c"),
             fill("gC", t + 120, qty=100, price=0.60, fee=1.0, role="EXIT",
                  direction="SELL", slug="syn-c"),
             fill("gD", t + 32, qty=100, price=0.56, fee=1.0, slug="syn-d")]
    setts = [settlement("gA", slug="syn-a", qty=100, payout=1.0,
                        at=t + 7200),
             settlement("gC", slug="syn-c", qty=0, payout=0.0, at=t + 7300),
             settlement("gOther", slug="syn-b", qty=10, payout=1.0,
                        at=t + 7400)]
    return {"decisions": [a, b, c, d],
            "groups": {"A": "gA", "C": "gC", "D": "gD"},
            "fills": fills, "settlements": setts}


def scenario_books():
    t = T0
    return [book("syn-a", t + 1, bids=((0.54, 500),), offers=((0.56, 500),)),
            book("syn-c", t + 21, bids=((0.48, 200),), offers=((0.52, 200),)),
            book("syn-d", t + 31, bids=((0.55, 500),), offers=((0.57, 500),))]


def stream(*, raw=None, books=None, karen=(), allocations=(), regimes=(),
           eddie=None, scout=None, iface_why=None, basis="PAPER",
           theses=()):
    raw = copy.deepcopy(raw or scenario_raw())
    b = E.build_paper(raw, vals={}, prem={}, theses=list(theses))
    return E.Stream(basis=basis, opps=b["opps"], positions=b["positions"],
                    oracle=b["oracle"],
                    books=copy.deepcopy(books if books is not None
                                        else scenario_books()),
                    regimes=list(regimes), allocations=list(allocations),
                    karen=list(karen), eddie=eddie, scout=scout,
                    iface_why=iface_why or {
                        "EDDIE": "INTERFACE_ABSENT:pos_iface_eddie_execution",
                        "SCOUT": "INTERFACE_ABSENT:pos_iface_scout_feature_"
                                 "effects"},
                    window=(T0 - 86400.0, T0 + 86400.0))


def by_subject(result) -> dict:
    return {t["subject_id"]: t for t in result["rows"]}


# ── control / production tables a twin cycle must leave unchanged ────
CONTROL_TABLES = ("paper_control", "execmirror_control",
                  "kalshi_smalllive_control", "agent_policy_artifacts",
                  "live_rule_artifacts", "agent_slack_control_audit")


async def control_snapshot(conn) -> dict:
    """{table: md5 of every row} for each control table that exists -- a
    content check, not just a count."""
    out = {}
    for t in CONTROL_TABLES:
        if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t):
            out[t] = await conn.fetchval(
                'SELECT md5(coalesce(string_agg(md5(x::text), \',\' '
                'ORDER BY md5(x::text)), \'\')) FROM "%s" x' % t)
    return out
