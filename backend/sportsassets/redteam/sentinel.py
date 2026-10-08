"""ADRIANA'S TWO-LEG EXECUTION SENTINEL (red_team.two_leg_sentinel), SHADOW.

GUARANTEED_AFTER_COSTS_IF_FILLED is a statement about ECONOMICS: if both
legs fill at the evaluated quantity, every settlement state pays at least
the cost. EXECUTION_LOCKED is a statement about FILLS: both legs confirmed
at the target matched quantity. They are never the same thing, and a pair
is never called locked before its fills say so.

Every Adriana opportunity (GUARANTEED_AFTER_COSTS from the engine) enters
the package state machine as DISCOVERED and is REVALIDATED immediately
before any action, from the database, not from the scan's memory:

  books current        each leg's current book is the one priced (no newer
                       book since the decision), inside the age bound, and
                       each venue's health domain is green (a cross-venue
                       pair needs BOTH)
  settlement current   each leg's rules fingerprint now == the one the
                       decision was certified against
  economics positive   the engine's worst-case net profit after costs > 0
  venue capital        each venue's available capital is CONFIRMED for its
                       leg's all-in cost (no assumption of transfers);
                       unconfirmed freezes the pair

ADRIANA HAS NO SUBMIT AUTHORITY: a REVALIDATED pair is never ARMED here
(the receipt says why). The fill half of the machine -- PARTIAL, MATCHED,
LOCKED, REPAIR_REQUIRED, FROZEN on overfill, the max unmatched loss cap,
dedup by venue fill identity, deterministic client order ids, and
reconcile-before-retry on an ambiguous acknowledgement -- is implemented
here for the reviewed execution adapter that does not exist yet, and is
proven by tests/test_red_team_chaos.py. Fills, not acknowledgements, drive
position truth.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from decimal import Decimal

from ..red_team import two_leg_sentinel as TL

VERSION = "TWO_LEG_SENTINEL_V1"
GUARANTEED_IF_FILLED = "GUARANTEED_AFTER_COSTS_IF_FILLED"
NOT_GUARANTEED = "NOT_GUARANTEED"
LOCKED_LABEL = "EXECUTION_LOCKED"
NOT_LOCKED_LABEL = "NOT_EXECUTION_LOCKED"
NO_SUBMIT_AUTHORITY = "SHADOW_NO_SUBMIT_AUTHORITY_NEVER_ARMED"
CLIENT_ID_NAMESPACE = uuid.UUID("6f1c2d4e-9a7b-5c3d-8e2f-1a0b9c8d7e6f")
MAX_BOOK_AGE_S = 30.0
MULTI_LEG = "MULTI_LEG_STRUCTURE_NOT_A_TWO_LEG_PAIR"

# ambiguous acknowledgements (kalshi_orders.reconcile_ambiguous's contract)
ACK_ACCEPTED, ACK_REJECTED, ACK_AMBIGUOUS = "ACCEPTED", "REJECTED", "AMBIGUOUS"
REC_ADOPT, REC_WAIT, REC_NOT_FOUND, REC_UNATTRIBUTED = (
    "ADOPT", "WAIT", "NOT_FOUND", "UNATTRIBUTED")


def D(v) -> Decimal:
    return Decimal(str(v if v is not None else 0))


def running_sha() -> str:
    """The serving build's commit (Render sets RENDER_GIT_COMMIT)."""
    import os
    return (os.environ.get("RENDER_GIT_COMMIT") or "").strip().lower()


def client_order_id(pair_id: str, leg: str) -> str:
    """Deterministic: the same pair and leg always carry the same client
    id, so a retry after NOT_FOUND re-uses it and a venue that saw the
    first request deduplicates the second."""
    return str(uuid.uuid5(CLIENT_ID_NAMESPACE, "%s:%s" % (pair_id, leg)))


def retry_allowed(ack: str, reconcile: str | None) -> dict:
    """A timeout / ambiguous response NEVER triggers a blind retry: only a
    reconciliation that positively shows the original order does not exist
    (NOT_FOUND) permits one retry, with the same client id."""
    if ack == ACK_ACCEPTED:
        return {"retry": False, "why": "ACCEPTED_NO_RETRY"}
    if ack == ACK_REJECTED:
        return {"retry": False, "why": "REJECTED_NEW_DECISION_REQUIRED"}
    if reconcile == REC_NOT_FOUND:
        return {"retry": True, "why": "VENUE_SHOWS_NO_ORIGINAL_ORDER",
                "same_client_id": True}
    if reconcile == REC_ADOPT:
        return {"retry": False, "why": "ORIGINAL_ORDER_FOUND_ADOPTED"}
    if reconcile == REC_UNATTRIBUTED:
        return {"retry": False, "why": "UNATTRIBUTED_FILL_FREEZE"}
    return {"retry": False, "why": "AMBIGUOUS_RECONCILE_FIRST"}


def apply_fills(state: TL.PairState, fills: list, *, seen: set,
                worst_case_unmatched_loss_per_contract) -> TL.PairState:
    """Venue fills -> the pair state. Each fill carries its venue identity
    (fill_id); a duplicate is ignored (C11). Fills accumulate, so their
    order does not change the converged state unless the pair froze or
    entered repair on the way (C12)."""
    s = state
    for f in fills:
        fid = str(f["fill_id"])
        if fid in seen:
            continue
        seen.add(fid)
        if s.state in (TL.FROZEN, TL.REPAIR_REQUIRED, TL.LOCKED):
            break
        s = TL.fill(s, f["leg"], int(f["qty"]), D(f["cost"]),
                    worst_case_unmatched_loss_per_contract=D(
                        worst_case_unmatched_loss_per_contract))
    return s


def labels(s: TL.PairState, economics_positive: bool) -> dict:
    return {"economics_label": GUARANTEED_IF_FILLED if economics_positive
            else NOT_GUARANTEED,
            "execution_label": TL.guarantee_label(s)}


# ── SHADOW revalidation of one opportunity ───────────────────────────────

def legs_of(rec: dict) -> list:
    eco = rec.get("economics") or {}
    return [x for x in (eco.get("legs") or []) if isinstance(x, dict)]


def pair_id_of(rec: dict, scan_id: str) -> str:
    legs = legs_of(rec)
    key = json.dumps([[x.get("venue"), x.get("market_id"), x.get("side")]
                      for x in legs], sort_keys=True)
    return "pair:%s:%s" % (scan_id, hashlib.sha256(
        key.encode()).hexdigest()[:20])


def contract_id(venue: str, market_id: str) -> str:
    return ("kalshi:%s" % market_id) if venue == "KALSHI" else market_id


def revalidate_shadow(rec: dict, *, scan_id: str, now: float,
                      books_now: dict, rules_now: dict, capital: dict,
                      venue_health: dict) -> dict:
    """One opportunity through DISCOVERED -> REVALIDATED | FROZEN.

    books_now  {(venue, market_id): observed_at epoch} -- the CURRENT book
    rules_now  {contract_id: rules_sha256} -- the CURRENT rules fingerprint
    capital    {venue: available USD or None (unconfirmed)}
    venue_health  redteam.venue_health.report(...)"""
    legs = legs_of(rec)
    eco = rec.get("economics") or {}
    pid = pair_id_of(rec, scan_id)
    net = D(eco.get("worst_case_net_profit"))
    qty = int(D(eco.get("qty")))
    positive = rec.get("verdict") == "GUARANTEED_AFTER_COSTS" and net > 0
    # the unmatched cap: one leg filled alone may lose at most what the
    # full pair would have made
    cap = max(net, Decimal(0))
    s = TL.PairState(TL.DISCOVERED, qty, max_unmatched_loss=cap)
    ev = {"legs": [], "pair_id": pid, "venues": sorted({x.get("venue")
                                                        for x in legs})}
    if len(legs) != 2:
        s = replace(s, state=TL.FROZEN, reason=MULTI_LEG)
        return _receipt(s, rec, ev, positive, legs, now)
    decided = {(b.get("venue"), b.get("market_id")): b.get("observed_at")
               for b in ((rec.get("inputs") or {}).get("books") or [])}
    certified = rec.get("rules_certified") or (
        rec.get("claim_pair") or {}).get("rules") or {}
    books_ok, rules_ok, why_books, why_rules = True, True, [], []
    for x in legs:
        v, m = x.get("venue"), x.get("market_id")
        cur = books_now.get((v, m))
        dec = _epoch(decided.get((v, m)))
        age = None if cur is None else now - float(cur)
        leg_ev = {"venue": v, "market_id": m, "side": x.get("side"),
                  "qty": x.get("qty"), "all_in": x.get("total_cost"),
                  "book_observed_at_decision": dec,
                  "book_observed_at_now": cur, "book_age_s": age}
        if cur is None or age is None or age > MAX_BOOK_AGE_S:
            books_ok = False
            why_books.append("BOOK_STALE_OR_ABSENT:%s:%s" % (v, m))
        elif dec is not None and abs(float(cur) - dec) > 1e-6:
            books_ok = False
            why_books.append("BOOK_CHANGED_SINCE_DECISION:%s:%s" % (v, m))
        cid = contract_id(v, m)
        want, have = certified.get(cid), rules_now.get(cid)
        leg_ev.update(rules_certified=want, rules_now=have)
        if not want or not have or want != have:
            rules_ok = False
            why_rules.append(("RULES_CHANGED_SINCE_CERTIFICATION:%s" % cid)
                             if want and have else
                             "RULES_FINGERPRINT_MISSING:%s" % cid)
        ev["legs"].append(leg_ev)
    venues = ev["venues"]
    pair_h = _pair_health(venue_health, venues)
    if not pair_h["green"]:
        books_ok = False
        why_books.extend(pair_h["blockers"])
    ev.update(books_reasons=why_books, rules_reasons=why_rules,
              venue_health=pair_h)
    s = TL.revalidate(s, books_current=books_ok, settlement_current=rules_ok,
                      economics_positive=positive)
    if s.state == TL.REVALIDATED:
        need = {}
        for x in legs:
            need[x.get("venue")] = need.get(x.get("venue"), Decimal(0)) + D(
                x.get("total_cost"))
        short = [v for v, usd in sorted(need.items())
                 if capital.get(v) is None or D(capital.get(v)) < usd]
        ev["capital"] = {v: {"needed_usd": str(usd),
                             "available_usd": (None if capital.get(v) is None
                                               else str(capital.get(v)))}
                         for v, usd in need.items()}
        if short:
            s = replace(s, state=TL.FROZEN, reason="VENUE_CAPITAL_"
                        "UNCONFIRMED:%s" % ",".join(short))
        else:
            # REVALIDATED stays REVALIDATED: Adriana cannot arm
            s = replace(s, reason=NO_SUBMIT_AUTHORITY)
    return _receipt(s, rec, ev, positive, legs, now)


def _pair_health(rep: dict, venues: list) -> dict:
    from . import venue_health as VH
    if not venues:
        return {"green": False, "blockers": ("NO_VENUE",)}
    if len(venues) == 1:
        return VH.pair_gate(rep, venues[0], venues[0])
    return VH.pair_gate(rep, venues[0], venues[1])


def _epoch(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(v)).timestamp()
    except ValueError:
        return None


def _receipt(s: TL.PairState, rec: dict, ev: dict, positive: bool,
             legs: list, now: float) -> dict:
    lab = labels(s, positive)
    eco = rec.get("economics") or {}
    per = [D(x.get("total_cost")) / D(x.get("qty") or 1) for x in legs]
    worst_unmatched = max(per) if per else Decimal(0)
    return {"pair_id": ev["pair_id"], "state": s.state,
            "target_qty": s.target_qty,
            "leg_a_target": int(D((legs[0] if legs else {}).get("qty"))),
            "leg_b_target": int(D((legs[1] if len(legs) > 1 else {})
                                  .get("qty"))),
            "leg_a_filled": s.a_filled, "leg_b_filled": s.b_filled,
            "matched_qty": s.matched_qty, "unmatched_qty": s.unmatched_qty,
            "unmatched_risk_usd": str(D(s.unmatched_qty) * worst_unmatched),
            "max_unmatched_loss_usd": str(s.max_unmatched_loss),
            "execution_locked": s.execution_locked, "reason": s.reason,
            **lab,
            "evidence": dict(ev, worst_case_net_profit=eco.get(
                "worst_case_net_profit"), topology=(rec.get("claim_pair")
                                                    or {}).get("topology"),
                worst_case_unmatched_loss_per_contract=str(worst_unmatched),
                at=now, version=VERSION)}


# ── the database side (Adriana's runner) ─────────────────────────────────

async def current_books(conn, legs: list) -> dict:
    out = {}
    kal = sorted({x["market_id"] for x in legs if x.get("venue") == "KALSHI"})
    pm = sorted({x["market_id"] for x in legs
                 if x.get("venue") == "POLYMARKET_US"})
    if kal and await conn.fetchval(
            "SELECT to_regclass('kalshi_books_current') IS NOT NULL"):
        for r in await conn.fetch(
                "SELECT ticker, extract(epoch FROM observed_at) AS at, "
                "readable FROM kalshi_books_current WHERE ticker = "
                "ANY($1::text[])", kal):
            if r["readable"]:
                out[("KALSHI", r["ticker"])] = float(r["at"])
    for s in pm:
        at = await conn.fetchval(
            "SELECT extract(epoch FROM max(observed_at)) FROM "
            "paper_book_observations WHERE us_market_slug = $1 AND error "
            "IS NULL AND observed_at > now() - interval '15 minutes'", s)
        if at is not None:
            out[("POLYMARKET_US", s)] = float(at)
    return out


async def current_rules(conn, cids: list) -> dict:
    if not cids:
        return {}
    return {r["contract_id"]: r["rules_sha256"] for r in await conn.fetch(
        "SELECT contract_id, rules_sha256 FROM market_plane_rules WHERE "
        "contract_id = ANY($1::text[])", sorted(set(cids)))}


async def venue_capital(conn) -> dict:
    """{venue: available USD | None}. POLYMARKET_US from the newest venue
    balance snapshot (execmirror_snapshots.balances buyingPower) when it is
    fresh; KALSHI has no account read in this build -> None (UNCONFIRMED).
    Never an assumption of capital or of a transfer."""
    out = {"KALSHI": None, "POLYMARKET_US": None}
    if not await conn.fetchval(
            "SELECT to_regclass('execmirror_snapshots') IS NOT NULL"):
        return out
    r = await conn.fetchrow(
        "SELECT balances, extract(epoch FROM now() - at) AS age FROM "
        "execmirror_snapshots ORDER BY at DESC LIMIT 1")
    if r is None or r["age"] is None or float(r["age"]) > 900:
        return out
    b = r["balances"]
    b = json.loads(b) if isinstance(b, str) else (b or {})
    bp = None
    if isinstance(b, dict):
        bp = b.get("buyingPower") or b.get("buying_power")
    elif isinstance(b, list) and b and isinstance(b[0], dict):
        bp = b[0].get("buyingPower") or b[0].get("buying_power")
    try:
        out["POLYMARKET_US"] = None if bp is None else Decimal(str(bp))
    except Exception:                                           # noqa: BLE001
        out["POLYMARKET_US"] = None
    return out


async def latest_venue_health(conn, *, now: float,
                              max_age_s: float = 900.0) -> dict:
    """The newest VENUE_HEALTH control receipt (the red-team runner writes
    it every pass): {venue: {green, ...}}. Absent or older than max_age_s =
    {} -- no venue is healthy, every pair freezes."""
    if not await conn.fetchval(
            "SELECT to_regclass('red_team_control_receipts') IS NOT NULL"):
        return {}
    r = await conn.fetchrow(
        "SELECT evidence, extract(epoch FROM computed_at) at FROM "
        "red_team_control_receipts WHERE control = 'VENUE_HEALTH' ORDER BY "
        "computed_at DESC LIMIT 1")
    if r is None or now - float(r["at"]) > max_age_s:
        return {}
    ev = r["evidence"]
    ev = json.loads(ev) if isinstance(ev, str) else (ev or {})
    return ev.get("venues") or {}


async def certify_rules(conn, result: dict) -> int:
    """AT DECISION TIME (before the census is recorded): the rules
    fingerprint of every leg of every opportunity that does not already
    carry the one it was built from. Returns the opportunities stamped."""
    n = 0
    opps = [r for r in (result.get("opportunities") or [])
            if not (r.get("claim_pair") or {}).get("rules")]
    cids = [contract_id(x.get("venue"), x.get("market_id"))
            for r in opps for x in legs_of(r)]
    rules = await current_rules(conn, cids)
    for r in opps:
        r["rules_certified"] = {
            contract_id(x.get("venue"), x.get("market_id")): rules.get(
                contract_id(x.get("venue"), x.get("market_id")))
            for x in legs_of(r)}
        n += 1
    return n


async def shadow_pass(conn, result: dict, *, scan_id: str, now: float,
                      venue_health: dict, sha: str) -> dict:
    """Revalidate every opportunity of one recorded census and append its
    SHADOW pair receipt (migration 315). Writes nothing else."""
    opps = list(result.get("opportunities") or [])
    if not opps:
        return {"pairs": 0, "by_state": {}}
    legs = [x for r in opps for x in legs_of(r)]
    books = await current_books(conn, legs)
    rules = await current_rules(conn, [contract_id(x.get("venue"),
                                                   x.get("market_id"))
                                       for x in legs])
    cap = await venue_capital(conn)
    by: dict = {}
    has = await conn.fetchval(
        "SELECT to_regclass('red_team_pair_execution_receipts') IS NOT NULL")
    for r in opps:
        rc = revalidate_shadow(r, scan_id=scan_id, now=now, books_now=books,
                               rules_now=rules, capital=cap,
                               venue_health=venue_health)
        by[rc["state"]] = by.get(rc["state"], 0) + 1
        if has:
            await write_receipt(conn, rc, sha=sha, now=now)
    return {"pairs": len(opps), "by_state": by}


async def write_receipt(conn, rc: dict, *, sha: str, now: float) -> None:
    rid = "rtp:%s:%d" % (rc["pair_id"], int(now * 1000))
    await conn.execute(
        "INSERT INTO red_team_pair_execution_receipts (receipt_id, "
        " observed_at, implementation_sha, pair_id, state, target_qty, "
        " leg_a_filled, leg_b_filled, matched_qty, unmatched_qty, "
        " execution_locked, reason, evidence, leg_a_target, leg_b_target, "
        " unmatched_risk_usd, max_unmatched_loss_usd, economics_label, "
        " execution_label) VALUES ($1, to_timestamp($2), $3, $4, $5, $6, "
        " $7, $8, $9, $10, $11, $12, $13::jsonb, $14, $15, $16, $17, $18, "
        " $19) ON CONFLICT (receipt_id) DO NOTHING",
        rid, now, sha or "UNKNOWN", rc["pair_id"], rc["state"],
        rc["target_qty"], rc["leg_a_filled"], rc["leg_b_filled"],
        rc["matched_qty"], rc["unmatched_qty"], rc["execution_locked"],
        rc["reason"], json.dumps(rc["evidence"], default=str),
        rc["leg_a_target"], rc["leg_b_target"],
        Decimal(rc["unmatched_risk_usd"]),
        Decimal(rc["max_unmatched_loss_usd"]), rc["economics_label"],
        rc["execution_label"])
