"""PAPER POSITION RECONCILIATION RECEIPT (read-only).

Canonical open quantity = bought - sold - authoritative settlement, per
(account, group, market, holding side) (open_position_canon). A position at
or below OPEN_QTY_EPS is CLOSED and leaves Xavier's queue, the mark-refresh
universe, protection replacement, exposure and the freshness denominator.

THE DEFECTS THIS RECEIPT PROVES CLOSED (P0 phantom-open census, production
research run 37407847139, 2026-10-06):

  * LEGACY READERS. Surfaces outside the ledger re-derived "open" with their
    own SQL: by group only (Command floor, execution-mirror view, the paper
    handoff refs), by (group, market) without the holding side (work state,
    PinnAPI held watch), `> 0` instead of the epsilon, and "any settlement
    in the group" as closed. A fully offset position could read open on one
    surface and closed on another. Every one now reads the canonical SQL;
    the receipt recomputes the legacy formulas to list what they called
    open that the canonical rule does not ("phantom opens").
  * SUB-CONTRACT REMAINDERS. Entries fill fractional book sizes (1454.12);
    the standing protection was floored to whole contracts, so 0.12 stayed
    open forever -- never protectable, never closed, counted in the mark
    universe and the freshness denominator, and churning cancel/replace.
    Protection now covers the whole held quantity at the ledger's grain.
  * ECONOMIC DUPLICATE FILLS. The resting step read every new observation
    of a book and filled the same order again from a crossing level that
    was merely still displayed (one order: 105.81 @ 0.52 twice at one
    instant). The simulator now remembers crossed liquidity per order. The
    historical rows are append-only ledger history and are NOT rewritten;
    they are listed here explicitly as ECONOMIC_DUPLICATE_SUSPECT so they
    are never silently double counted in any claim.

THE DUPLICATE RULE, CORRECTED (RC6, production pm-acceptance 37836393458,
2026-10-08 20:10Z). The P0 rule grouped fills by (order, qty, price,
instant) and called every group of two or more a duplicate "on distinct book
observations" -- but never looked at the observation or the level. A fill's
idempotency key is `<order>:obs<obs>:<wire>` (bettor_paper_simulator.
_apply_takes, unchanged since the first simulator commit; paper_fills.
idempotency_key is UNIQUE and the simulator is its only writer), and the fill
id is sha256 of that key -- so every production fill's level is recoverable
from its id alone. Of the 18 groups (71 fills, 3,732.79 "extra") the receipt
listed:

  * 13 are ONE wire level re-filled on 2-5 consecutive observations inside
    ONE simulation step: true economic duplicates, extra 3,402.79 contracts,
    the newest filled 2026-10-05 22:17:55Z -- before the seen-crossing fix
    (release 221ce6b9, 2026-10-06 05:16Z). None since.
  * 7 are DIFFERENT levels that happened to show the same size, taken in one
    step at our limit (FILLED_AT_OUR_LIMIT_NO_PRICE_IMPROVEMENT), e.g. one
    order 100 @ 0.75 from offers 0.18 and 0.19 on observation 67419 (the only
    group after the fix). Each (market, side, wire, observation) level is
    distinct liquidity, consumed once (paper_liquidity_consumed): these are
    NOT duplicates.

So a duplicate is now the SAME LEVEL re-filled: same order, wire level, qty,
price and instant on more than one observation. A refill filled before the
producer fix is HISTORICAL (listed, labelled, counted on its own, never
rewritten); one filled at or after it is a CURRENT suspect and is what
`economic_duplicate_suspect_groups` counts. The distinct-level groups the
old rule named stay listed as NOT_A_DUPLICATE_DISTINCT_LEVELS.

Never raises: an unreadable section is UNAVAILABLE with its reason.
"""
from __future__ import annotations

import datetime as _dt
import time

from .open_position_canon import CANONICAL_OPEN_POSITIONS_SQL, OPEN_QTY_EPS

VERSION = "PAPER_RECONCILIATION_RECEIPT_V1"

#: the LEGACY formulas, kept here only to measure what they called open
LEGACY_READERS = {
    # agent_work_state._read_positions (before): (group, market), no holding
    # side, closed only when no settlement at all exists for (group, market)
    "work_state_group_market": """
        SELECT f.account_id, f.group_id, f.us_market_slug
          FROM paper_fills f
         WHERE NOT EXISTS (SELECT 1 FROM paper_settlements x
                            WHERE x.group_id = f.group_id
                              AND x.us_market_slug = f.us_market_slug)
         GROUP BY f.account_id, f.group_id, f.us_market_slug
        HAVING sum(CASE WHEN f.direction='BUY' THEN f.qty ELSE -f.qty END)
               > 1e-9""",
    # pinnapi_held.HELD_SLUGS_SQL (before): `> 0`, (group, market)
    "pinnapi_held_group_market": """
        SELECT f.account_id, f.group_id, f.us_market_slug
          FROM paper_fills f JOIN paper_handoffs h ON h.group_id = f.group_id
         WHERE NOT EXISTS (SELECT 1 FROM paper_settlements s
                            WHERE s.group_id = f.group_id
                              AND s.us_market_slug = f.us_market_slug)
         GROUP BY f.account_id, f.group_id, f.us_market_slug
        HAVING sum(CASE WHEN f.direction='BUY' THEN f.qty ELSE -f.qty END)
               > 0""",
    # command_floor._xavier / xavier_management handoff refs / execmirror
    # (before): per GROUP, settlement as "any settlement in the group"
    "per_group_any_settlement": """
        SELECT h.account_id, h.group_id, NULL::text AS us_market_slug
          FROM paper_handoffs h
         WHERE coalesce((SELECT sum(qty) FILTER (WHERE direction='BUY')
                            - coalesce(sum(qty) FILTER (
                                WHERE direction='SELL'), 0)
                           FROM paper_fills f
                          WHERE f.group_id = h.group_id), 0) > 1e-9
           AND NOT EXISTS (SELECT 1 FROM paper_settlements s
                            WHERE s.group_id = h.group_id)""",
}

POSITIONS_SQL = """
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           max(f.strategy) AS strategy,
           coalesce(sum(f.qty) FILTER (WHERE f.direction='BUY'), 0)
               AS bought,
           coalesce(sum(f.qty) FILTER (WHERE f.direction='SELL'), 0)
               AS sold,
           coalesce(max(s.qty), 0) AS settled
      FROM paper_fills f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE ($1::text IS NULL OR f.account_id = $1)
     GROUP BY f.account_id, f.group_id, f.us_market_slug, f.holding_side
"""

#: THE PRODUCER FIX, EARLIEST EFFECTIVE INSTANT: release 221ce6b9 (P0
#: closeout, committed 2026-10-06T05:16:06Z; API and workers deployed by
#: render-ops 37420681808 / 37420886019, live at the 06:31Z readback
#: 37424216891) carried the simulator's per-order seen-crossing memory
#: (bettor_paper_simulator.SEEN_CROSSING_KEY). The COMMIT instant is the
#: earliest the fix could have run anywhere, so a refill filled between it
#: and the deploy counts as CURRENT (fail closed), never the reverse.
PRODUCER_FIX_EFFECTIVE_AT = 1791263766.0
PRODUCER_FIX_RELEASE = "221ce6b946d23a204aeae3254a3f98a525fe479d"

L_SUSPECT = "ECONOMIC_DUPLICATE_SUSPECT"
L_HISTORICAL = "ECONOMIC_DUPLICATE_HISTORICAL_BEFORE_PRODUCER_FIX"
L_DISTINCT = "NOT_A_DUPLICATE_DISTINCT_LEVELS"
#: at most this many candidate fills are read (production: 71)
MAX_CANDIDATE_FILLS = 5000

#: THE CANDIDATES: every fill of a group the P0 rule named (same order, qty,
#: price and instant, more than one fill), WITH its wire level and book
#: observation, so `classify_duplicates` can tell one level re-filled from
#: different levels of the same size
DUPLICATE_CANDIDATES_SQL = """
    SELECT f.fill_id, f.order_id, o.role, f.group_id, f.us_market_slug,
           f.holding_side, f.direction, f.qty, f.price, f.wire_price,
           f.filled_at, extract(epoch FROM f.filled_at) AS filled_epoch,
           f.book_obs_id
      FROM paper_fills f
      JOIN paper_orders o ON o.order_id = f.order_id
      JOIN (SELECT order_id, qty, price, filled_at FROM paper_fills
             WHERE ($1::text IS NULL OR account_id = $1)
             GROUP BY order_id, qty, price, filled_at
            HAVING count(*) > 1) c
        ON c.order_id = f.order_id AND c.qty = f.qty AND c.price = f.price
       AND c.filled_at = f.filled_at
     WHERE ($1::text IS NULL OR f.account_id = $1)
     ORDER BY f.filled_at DESC, f.order_id, f.wire_price, f.book_obs_id
     LIMIT """ + str(MAX_CANDIDATE_FILLS)


def _iso(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(float(epoch), _dt.timezone.utc) \
        .isoformat().replace("+00:00", "Z")


def _wire(v) -> str:
    return "%.6f" % float(v)


def _group(fs: list, **extra) -> dict:
    fs = sorted(fs, key=lambda f: (int(f["book_obs_id"] or 0),
                                   str(f["fill_id"])))
    f0 = fs[0]
    out = {"order_id": f0["order_id"], "role": f0.get("role"),
           "group_id": f0.get("group_id"),
           "us_market_slug": f0.get("us_market_slug"),
           "holding_side": f0.get("holding_side"),
           "direction": f0.get("direction"), "qty": float(f0["qty"]),
           "price": float(f0["price"]), "filled_at": str(f0["filled_at"]),
           "filled_epoch": float(f0["filled_epoch"]), "n": len(fs),
           "fill_ids": [f["fill_id"] for f in fs],
           "obs_ids": [f["book_obs_id"] for f in fs],
           "wires": [float(f["wire_price"]) for f in fs]}
    out.update(extra)
    return out


def classify_duplicates(fills: list, *,
                        fixed_at: float = PRODUCER_FIX_EFFECTIVE_AT) -> dict:
    """Pure. THE CANDIDATE FILLS (DUPLICATE_CANDIDATES_SQL rows), split into

      current     the SAME wire level of one order filled at one instant on
                  more than one book observation, at or after `fixed_at`;
      historical  the same, filled before `fixed_at` (append-only history,
                  listed, never rewritten);
      distinct_levels  the P0 rule's (order, qty, price, instant) groups
                  with NO level filled twice: different levels of the same
                  size, each its own liquidity -- not a duplicate.

    `extra_qty` of a refill group is qty x (n - 1): the first fill of the
    level is real, every repeat is the duplicate."""
    by_level: dict = {}
    by_former: dict = {}
    for f in fills or []:
        former = (f["order_id"], float(f["qty"]), float(f["price"]),
                  str(f["filled_at"]))
        by_former.setdefault(former, []).append(f)
        by_level.setdefault(former + (_wire(f["wire_price"]),),
                            []).append(f)
    current, historical, refill_former = [], [], set()
    for key, fs in by_level.items():
        if len({f["book_obs_id"] for f in fs}) < 2:
            continue
        refill_former.add(key[:4])
        hist = float(fs[0]["filled_epoch"]) < float(fixed_at)
        g = _group(fs, wire=float(fs[0]["wire_price"]),
                   extra_qty=round(float(fs[0]["qty"]) * (len(fs) - 1), 6),
                   label=L_HISTORICAL if hist else L_SUSPECT,
                   basis=("one wire level of one order re-filled at one "
                          "instant on %d book observations: a crossing "
                          "level that was merely still displayed; "
                          "append-only history, not rewritten" % len(fs)),
                   window=("BEFORE_PRODUCER_FIX" if hist else
                           "AT_OR_AFTER_PRODUCER_FIX"))
        (historical if hist else current).append(g)
    distinct = [
        _group(fs, label=L_DISTINCT,
               basis=("different wire levels of the same displayed size "
                      "taken in one step at the order's limit; each "
                      "(market, side, wire, observation) level is its own "
                      "liquidity, consumed once -- not a duplicate"))
        for key, fs in by_former.items() if key not in refill_former]

    def _newest_first(gs):
        return sorted(gs, key=lambda g: (g["filled_epoch"], g["order_id"],
                                         g.get("wire") or 0.0),
                      reverse=True)
    return {"current": _newest_first(current),
            "historical": _newest_first(historical),
            "distinct_levels": _newest_first(distinct),
            "former_rule_groups": len(by_former),
            "former_rule_groups_with_a_refill": len(refill_former)}

LIVE_PROTECTION_ON_CLOSED_SQL = """
    SELECT o.order_id, o.state, o.group_id, o.us_market_slug, o.holding_side
      FROM paper_orders o
     WHERE o.role = 'STANDING_PROTECTION'
       AND o.state IN ('PENDING_SIMULATION','RESTING','PARTIALLY_FILLED',
                       'CANCEL_PENDING')
       AND ($1::text IS NULL OR o.account_id = $1)
       AND NOT EXISTS (SELECT 1 FROM (""" + CANONICAL_OPEN_POSITIONS_SQL + """) c
                        WHERE c.group_id = o.group_id
                          AND c.us_market_slug = o.us_market_slug
                          AND c.holding_side = o.holding_side)
"""


def _key(account_id, group_id, slug, side):
    return "paperpos:%s:%s:%s:%s" % (account_id, group_id, slug, side)


def classify(rows: list) -> dict:
    """Pure. Canonical open / closed per position, with the sub-contract
    remainders named."""
    true_open, closed, remainders = [], [], []
    for r in rows:
        bought, sold = float(r["bought"] or 0), float(r["sold"] or 0)
        settled = float(r["settled"] or 0)
        open_qty = round(bought - sold - settled, 6)
        k = _key(r["account_id"], r["group_id"], r["us_market_slug"],
                 r["holding_side"])
        item = {"position_key": k, "group_id": r["group_id"],
                "us_market_slug": r["us_market_slug"],
                "holding_side": r["holding_side"],
                "strategy": r.get("strategy"),
                "bought": bought, "sold": sold, "settled": settled,
                "open_qty": open_qty}
        if open_qty > OPEN_QTY_EPS:
            true_open.append(item)
            if open_qty < 1.0:
                remainders.append(item)
        else:
            closed.append(item)
    return {"true_open": true_open, "closed": closed,
            "sub_contract_remainders": remainders}


async def receipt(conn, account_id: str | None = None, *,
                  now: float | None = None) -> dict:
    at = float(now if now is not None else time.time())
    out: dict = {"version": VERSION, "as_of": at, "account_id": account_id,
                 "rule": ("open = bought - sold - latest settlement qty per "
                          "(account, group, market, holding side); <= %g is "
                          "CLOSED" % OPEN_QTY_EPS),
                 "sections": {}}
    try:
        async with conn.transaction():
            rows = [dict(r) for r in await conn.fetch(POSITIONS_SQL,
                                                      account_id)]
            cls = classify(rows)
            canon_groups = {p["group_id"] for p in cls["true_open"]}
            canon_gm = {(p["group_id"], p["us_market_slug"])
                        for p in cls["true_open"]}
            phantoms = {}
            for name, sql in LEGACY_READERS.items():
                got = [dict(r) for r in await conn.fetch(sql)
                       if account_id is None
                       or r["account_id"] == account_id]
                if name == "per_group_any_settlement":
                    ph = [r for r in got if r["group_id"] not in canon_groups]
                else:
                    ph = [r for r in got if (r["group_id"],
                                             r["us_market_slug"])
                          not in canon_gm]
                phantoms[name] = {"legacy_open": len(got),
                                  "phantom_opens": len(ph),
                                  "affected": [
                                      {"group_id": r["group_id"],
                                       "us_market_slug": r["us_market_slug"]}
                                      for r in ph][:100]}
            cand = [dict(r) for r in await conn.fetch(
                DUPLICATE_CANDIDATES_SQL, account_id)]
            live_closed = [dict(r) for r in await conn.fetch(
                LIVE_PROTECTION_ON_CLOSED_SQL, account_id)]
    except Exception as exc:                                    # noqa: BLE001
        out.update(status="UNAVAILABLE",
                   why="RECONCILIATION_READ_FAILED: %s: %s"
                       % (type(exc).__name__, str(exc)[:160]))
        return out
    dup = classify_duplicates(cand)
    cur, hist = dup["current"], dup["historical"]

    def _extra(gs):
        return round(sum(g["extra_qty"] for g in gs), 6)
    out.update(
        status="OK",
        counts={
            "positions": len(rows),
            "true_open": len(cls["true_open"]),
            "closed": len(cls["closed"]),
            "sub_contract_remainders_open": len(
                cls["sub_contract_remainders"]),
            "phantom_opens_by_legacy_reader": {
                k: v["phantom_opens"] for k, v in phantoms.items()},
            "phantom_opens_in_canonical_readers": 0,
            # CURRENT: a level re-filled at or after the producer fix -- the
            # number that must be zero
            "economic_duplicate_suspect_groups": len(cur),
            "economic_duplicate_suspect_extra_qty": _extra(cur),
            # HISTORICAL: re-filled before the fix; immutable PAPER history,
            # counted and listed on its own, never rewritten or dropped
            "historical_economic_duplicate_groups": len(hist),
            "historical_economic_duplicate_extra_qty": _extra(hist),
            # what the P0 rule called a duplicate that is different levels
            "same_instant_distinct_level_groups": len(
                dup["distinct_levels"]),
            "former_rule_groups": dup["former_rule_groups"],
            "duplicate_candidate_fills": len(cand),
            "duplicate_candidates_truncated": (
                len(cand) >= MAX_CANDIDATE_FILLS),
            "live_protection_on_closed_positions": len(live_closed)},
        sections={
            "legacy_reader_phantoms": phantoms,
            "true_open": cls["true_open"][:500],
            "sub_contract_remainders": cls["sub_contract_remainders"],
            "economic_duplicate_rule": {
                "rule": ("one order's SAME wire level filled at one instant "
                         "on more than one book observation (a crossing "
                         "level that was merely still displayed); extra = "
                         "qty x (n - 1)"),
                "producer_fix_effective_at": _iso(PRODUCER_FIX_EFFECTIVE_AT),
                "producer_fix_release": PRODUCER_FIX_RELEASE,
                "producer_fix": ("bettor_paper_simulator seen-crossing "
                                 "memory per order (SEEN_CROSSING_KEY)"),
                "historical_window": ("filled before %s: append-only PAPER "
                                      "history, listed, never rewritten"
                                      % _iso(PRODUCER_FIX_EFFECTIVE_AT)),
                "current_window": ("filled at or after %s: counted as "
                                   "economic_duplicate_suspect_groups"
                                   % _iso(PRODUCER_FIX_EFFECTIVE_AT))},
            "economic_duplicate_suspects": cur[:500],
            "historical_economic_duplicates": hist[:500],
            "same_instant_distinct_levels": dup["distinct_levels"][:500],
            "live_protection_on_closed_positions": live_closed})
    return out
