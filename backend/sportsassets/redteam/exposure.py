"""CANONICAL EXPOSURE LOCK (red_team.claim_exposure, bound to the PAPER
ledger's ENTRY path).

BETTOR sizes ECONOMIC CLAIMS, not venue instruments. Every open position
and every open BUY reservation is keyed by the claim it pays on:

  * the canonical claim fingerprint where the Kalshi canonical layer has
    proven the alias (canonical_claim_aliases: venue POLYMARKET_US, market
    = the slug, LONG = YES / SHORT = NO -- the venue's own intent), so a
    Kalshi YES, a Kalshi opponent-NO and a PMUS YES proven equal are ONE
    claim with correlation 1;
  * otherwise the instrument itself ("POLYMARKET_US:<slug>:<side>"), never
    merged with anything it has not been proven equal to.

The event is the fixture. Strategy, agent, venue, ticker and side labels
never split a claim: two strategies on one claim are one exposure.

Limits: the system's existing single-fixture concentration cap
(allie_capital.FIXTURE_CAP_USD, 25% of the paper account) bounds both one
claim and one event, unless the account's own per-market / per-fixture
caps are set (then those, the tighter of the two). No new number is
introduced and no existing cap is loosened: the lock only refuses.

An ENTRY that passes every venue-level cap but would carry a claim or an
event past its limit is refused with the claim, its aliases and the
notional. Exits, reductions and protection never reach this code.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from .. import allie_capital as ALLIE
from ..red_team import claim_exposure as RTX
from ..red_team.models import ClaimExposure

VERSION = "CANONICAL_EXPOSURE_LOCK_V1"
R_CLAIM = "ABOVE_THE_CANONICAL_CLAIM_EXPOSURE_CAP"
R_EVENT = "ABOVE_THE_CANONICAL_EVENT_EXPOSURE_CAP"
CLAIM_CAP_USD = Decimal(str(ALLIE.FIXTURE_CAP_USD))
EVENT_CAP_USD = Decimal(str(ALLIE.FIXTURE_CAP_USD))
PMUS = "POLYMARKET_US"
SIDE_OF = {"LONG": "YES", "SHORT": "NO", "YES": "YES", "NO": "NO"}


def D(v) -> Decimal:
    return Decimal(str(v if v is not None else 0))


def limits(caps: dict | None = None) -> tuple:
    """(claim cap, event cap): the account's own per-market / per-fixture
    caps where set and tighter, else the existing fixture cap."""
    c = dict(caps or {})
    claim, event = CLAIM_CAP_USD, EVENT_CAP_USD
    if c.get("per_market_cap_usd") is not None:
        claim = min(claim, D(c["per_market_cap_usd"]))
    if c.get("per_fixture_cap_usd") is not None:
        event = min(event, D(c["per_fixture_cap_usd"]))
    return claim, event


def instrument_claim(slug: str, holding_side: str) -> str:
    return "%s:%s:%s" % (PMUS, slug, SIDE_OF.get(str(holding_side),
                                                 str(holding_side)))


def row(*, slug, holding_side, fixture, notional, qty, alias_group,
        aliases: dict) -> ClaimExposure:
    """One position / reservation as a ClaimExposure: the canonical claim
    when the alias is proven, else the instrument."""
    side = SIDE_OF.get(str(holding_side), str(holding_side))
    fp = aliases.get((slug, side))
    key = fp or instrument_claim(slug, holding_side)
    return ClaimExposure(
        claim_key=key, event_key=str(fixture or slug),
        payoff_fingerprint=fp or ("INSTRUMENT:" + key),
        venue=PMUS, instrument_id="%s:%s" % (slug, side),
        qty=D(qty), signed_notional=D(notional),
        alias_group=str(alias_group or "?"))


async def _aliases(conn, slugs: list) -> dict:
    """{(slug, YES|NO): claim_fingerprint} for every PMUS alias the
    canonical layer has proven (certified, fingerprint present)."""
    if not slugs or not await conn.fetchval(
            "SELECT to_regclass('canonical_claim_aliases') IS NOT NULL"):
        return {}
    cert = await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns WHERE table_name ="
        " 'canonical_claim_aliases' AND column_name = 'certificate_status'")
    rows = await conn.fetch(
        "SELECT market_id, side, claim_fingerprint FROM "
        "canonical_claim_aliases WHERE venue = $1 AND market_id = "
        "ANY($2::text[]) AND claim_fingerprint IS NOT NULL%s"
        % (" AND coalesce(certificate_status, 'CERTIFIED') = 'CERTIFIED'"
           if cert else ""), PMUS, sorted(set(slugs)))
    return {(r["market_id"], r["side"]): r["claim_fingerprint"] for r in rows}


async def book_rows(conn, account_id: str, *, extra_slugs=()) -> tuple:
    """([ClaimExposure], aliases): every open position (cost basis) and
    open BUY reservation of the account, as claim exposures."""
    from .. import bettor_paper_ledger as L
    pos = await L.positions(conn, account_id)
    res = await conn.fetch(
        "SELECT us_market_slug, holding_side, fixture, strategy, "
        "       sum(reserved_remaining_usd) AS usd, sum(qty) AS qty "
        "  FROM paper_orders WHERE account_id = $1 AND direction = 'BUY' "
        "   AND state = ANY($2::text[]) GROUP BY 1, 2, 3, 4",
        account_id, list(L.OPEN_STATES))
    aliases = await _aliases(conn, [p["us_market_slug"] for p in pos]
                             + [r["us_market_slug"] for r in res]
                             + [s for s in extra_slugs if s])
    out = [row(slug=p["us_market_slug"], holding_side=p["holding_side"],
               fixture=p["fixture"], notional=p["cost_basis_usd"],
               qty=p["open_qty"], alias_group=p["strategy"], aliases=aliases)
           for p in pos]
    out += [row(slug=r["us_market_slug"], holding_side=r["holding_side"],
                fixture=r["fixture"], notional=r["usd"], qty=r["qty"],
                alias_group=r["strategy"], aliases=aliases)
            for r in res if D(r["usd"]) > 0]
    return out, aliases


#: venues whose YES and NO in ONE market are one pool and net (Kalshi rep
#: 2026-10-07: "YES and NO positions in the same market net; the exchange
#: will not hold both sides independently in one market")
NETTING_VENUES = frozenset({"KALSHI"})


def net_same_market(rows: list) -> list:
    """Net each netting venue's YES and NO of ONE market into one row: the
    excess quantity on its side, at that side's average notional per
    contract. Cross-market aliases are untouched (separate books)."""
    out, groups = [], {}
    for r in rows:
        mkt, _sep, side = str(r.instrument_id).rpartition(":")
        if r.venue in NETTING_VENUES and side in ("YES", "NO") and mkt:
            groups.setdefault((r.venue, mkt), []).append((side, r))
        else:
            out.append(r)
    for (venue, mkt), items in sorted(groups.items()):
        q = {"YES": Decimal(0), "NO": Decimal(0)}
        n = {"YES": Decimal(0), "NO": Decimal(0)}
        keep = {}
        for side, r in items:
            q[side] += D(r.qty)
            n[side] += D(r.signed_notional)
            keep[side] = r
        net = q["YES"] - q["NO"]
        if net == 0:
            continue
        side = "YES" if net > 0 else "NO"
        per = n[side] / q[side] if q[side] else Decimal(0)
        r = keep[side]
        out.append(ClaimExposure(
            claim_key=r.claim_key, event_key=r.event_key,
            payoff_fingerprint=r.payoff_fingerprint, venue=venue,
            instrument_id="%s:%s" % (mkt, side), qty=abs(net),
            signed_notional=abs(net) * per, alias_group=r.alias_group))
    return out


def gate(rows: list, *, claim_cap: Decimal, event_cap: Decimal) -> dict:
    """The package's exposure gate over the claim rows (same-market
    YES / NO netted first on netting venues)."""
    rows = net_same_market(rows)
    return RTX.exposure_gate(rows, max_abs_notional_by_claim=claim_cap,
                             max_abs_notional_by_event=event_cap)


async def entry_refusal(conn, o: dict, *, reserve: Decimal,
                        caps: dict | None = None) -> dict | None:
    """UNDER THE ACCOUNT LOCK, for an ENTRY BUY only: refuse when adding
    this order's reservation carries its canonical claim or its event past
    the limit. Breaches already on the book before this order do not block
    an order on another claim / event (the lock never strands the book);
    an order that adds to a breached claim or event is refused."""
    claim_cap, event_cap = limits(caps)
    rows, aliases = await book_rows(conn, o["account_id"],
                                    extra_slugs=[o.get("us_market_slug")])
    new = row(slug=o.get("us_market_slug"), holding_side=o.get("holding_side"),
              fixture=o.get("fixture"), notional=reserve, qty=o.get("qty"),
              alias_group=o.get("strategy"), aliases=aliases)
    after = gate(rows + [new], claim_cap=claim_cap, event_cap=event_cap)
    c = after["claims"].get(new.claim_key) or {}
    if abs(D(c.get("signed_notional"))) > claim_cap:
        return {"refusal": R_CLAIM, "under_lock": True,
                "claim": _claim_view(new.claim_key, c),
                "claim_cap_usd": str(claim_cap), "lock": VERSION}
    ev = D(after["event_notional"].get(new.event_key))
    if abs(ev) > event_cap:
        return {"refusal": R_EVENT, "under_lock": True,
                "event_key": new.event_key, "event_notional_usd": str(ev),
                "event_cap_usd": str(event_cap),
                "claim": _claim_view(new.claim_key, c), "lock": VERSION}
    return None


def _claim_view(key, c: dict) -> dict:
    return {"claim_key": key,
            "payoff_fingerprint": c.get("payoff_fingerprint"),
            "notional_usd": str(D(c.get("signed_notional"))),
            "qty": str(D(c.get("qty"))),
            "alias_count": c.get("alias_count"),
            "aliases": [list(a) for a in (c.get("aliases") or ())],
            "event_keys": list(c.get("event_keys") or ())}


def receipt_rows(rows: list, *, claim_cap: Decimal, event_cap: Decimal,
                 sha: str, at: float) -> tuple:
    """(gate, [receipt dict]) -- one append-only receipt per claim."""
    g = gate(rows, claim_cap=claim_cap, event_cap=event_cap)
    out = []
    for key, c in sorted(g["claims"].items()):
        ev = (c.get("event_keys") or ("?",))[0]
        blockers = [b for b in g["blockers"]
                    if b in ("CLAIM_LIMIT:%s" % key, "EVENT_LIMIT:%s" % ev)]
        body = {"claim_key": key, "event_key": ev,
                "payoff_fingerprint": c["payoff_fingerprint"],
                "alias_count": c["alias_count"],
                "signed_notional": str(c["signed_notional"]),
                "aliases": [list(a) for a in c["aliases"]],
                "event_notional": str(D(g["event_notional"].get(ev))),
                "claim_limit_usd": str(claim_cap),
                "event_limit_usd": str(event_cap),
                "eligible": not blockers, "blockers": blockers}
        h = hashlib.sha256(json.dumps(body, sort_keys=True).encode()
                           ).hexdigest()
        out.append(dict(body, receipt_id="rtx:%d:%s" % (int(at), h[:24]),
                        implementation_sha=sha, evidence_hash=h))
    return g, out


async def census(conn, account_id: str, *, caps: dict | None = None,
                 sha: str = "", at: float = 0.0) -> dict:
    claim_cap, event_cap = limits(caps)
    rows, _aliases_ = await book_rows(conn, account_id)
    g, receipts = receipt_rows(rows, claim_cap=claim_cap,
                               event_cap=event_cap, sha=sha, at=at)
    multi = [r for r in receipts if r["alias_count"] > 1]
    return {"version": VERSION, "account_id": account_id,
            "eligible": g["eligible"], "blockers": list(g["blockers"]),
            "claim_cap_usd": str(claim_cap), "event_cap_usd": str(event_cap),
            "claims": len(receipts), "multi_alias_claims": len(multi),
            "largest_claims": sorted(
                receipts, key=lambda r: -abs(D(r["signed_notional"])))[:10],
            "largest_events": sorted(
                ({"event_key": k, "notional_usd": str(v)}
                 for k, v in g["event_notional"].items()),
                key=lambda r: -abs(D(r["notional_usd"])))[:10],
            "receipts": receipts}
