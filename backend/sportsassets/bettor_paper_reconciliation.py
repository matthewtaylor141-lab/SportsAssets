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

THE CANONICAL IDENTITY OF OBSERVED LIQUIDITY IS THE READ, NOT THE ROW (RC6
archer-lifecycle; production research runs 37874201361 and 37874710852,
2026-10-09 02:21-02:28Z). Every one of the 71 fills above carries its order's
account, session, group, contract, side, direction and role (71 / 71) and
its idempotency key `<order>:obs<obs>:<wire>` (71 / 71); they span one
account (paper_acct_main), one session, one venue source (the Polymarket US
market-data client) and one position per order: no group mixes accounts,
venues, contracts, sides or position groups. What the row-level rules missed
is that ONE venue read is recorded as SEVERAL paper_book_observations rows --
a read shared with another caller is recorded again with its ORIGINAL receipt
instant (bettor_paper_simulator.record_book, ":SHARED_READ"; 8,437 of the
18,848 rows of 2026-10-08 are such copies) -- while the consumed-liquidity
ledger is keyed by the row. The old resting step met every copy as a new
book: 109 protective fills, 33,947.82 contracts, were taken on a copy of a
read the order had already examined (all before the fix; none since). As the
duplicate authority the two rules above are replaced by the producer's own
rule applied to the READ (`canonical_duplicates`):

  SAME_READ_COPY          a fill taken on a row that is not the first
                          readable row of its read (same market, same
                          receipt instant): the read's liquidity was already
                          offered -- to the queue ahead, to other orders and
                          to this order -- on its first row, so the whole
                          fill is the duplicate.
  STILL_DISPLAYED_REFILL  a RESTING fill, on the first row of its read,
                          larger than what that row shows ABOVE what the
                          previous readable row the order examined showed at
                          the same wire: the seen-crossing memory of release
                          221ce6b9 (a crossing level is the same liquidity
                          until it shrinks). The excess is the duplicate.

Re-verified one by one, the P0 rule's 18 groups are 12 duplicate groups and
6 that are not: 84a543eb (1 @ 0.69, called distinct levels) took its 0.18 lot
-- and 1,326.48 more contracts -- on row 46173, a copy of the read of row
46172 whose crossing size the queue ahead had taken; 62c73a8f's rows 46247,
46248 and 46250 are one read showing row 46246's sizes again; bd296caf sold
0.41 x 539 a second time on the first row of the 14:06 read after the 13:42
read had shown it, then three times more on that read's copies. f35bb6ee,
c07c30f5, 5c9f045a (two groups), 49d199d5 and 57612207 took distinct levels
on the first row of a read the order had not seen them on: not duplicates.
Every duplicate is HISTORICAL; the receipt names the positions whose sales
they inflated and never rewrites a row.

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
#: different levels of the same size. (The receipt reads CANONICAL_FILLS_SQL
#: since RC6 archer-lifecycle; this is the former rule's read, kept beside
#: the pure split it fed.)
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
    """Pure. THE WIRE-LEVEL SPLIT OF THE P0 RULE'S GROUPS (RC6 archer-dups),
    kept as the record of that split. The receipt's duplicate authority is
    `canonical_duplicates`: a "distinct-level" group here can still be a
    duplicate when its fills sit on a copy of one venue read.

    THE CANDIDATE FILLS (DUPLICATE_CANDIDATES_SQL rows), split into

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


# ═════════════════════════════════════════════════════════════════════
# THE CANONICAL IDENTITY OF OBSERVED LIQUIDITY: THE READ (RC6)
# ═════════════════════════════════════════════════════════════════════

K_COPY = "SAME_READ_COPY"
K_STILL = "STILL_DISPLAYED_REFILL"
DUP_KINDS = (K_COPY, K_STILL)
#: at most this many judged fills are read, newest first (production
#: 2026-10-09: 1,219 fills in all, a few hundred judged)
MAX_CANONICAL_FILLS = 20000
DUP_QTY_EPS = 1e-9


def _shown_sql(side: str, wire: str) -> str:
    """SQL: the quantity a book side (jsonb) shows at one wire price, in
    the venue's level shape ({"px": {"value": "0.52"}, "qty": "25"} or bare
    scalars, as bettor_book_snapshot._level reads it); NULL when the side
    does not show that wire. Unparseable levels are skipped, as the
    simulator skips them."""
    px = ("coalesce(e->'px'->>'value', e->>'px', e->'price'->>'value', "
          "e->>'price')")
    q = ("coalesce(e->'qty'->>'value', e->>'qty', e->'size'->>'value', "
         "e->>'size')")
    num = r"'^[0-9]*\.?[0-9]+$'"
    return ("(SELECT max(CASE WHEN {q} ~ {num} THEN ({q})::numeric END) "
            "   FROM jsonb_array_elements(CASE WHEN jsonb_typeof({side}) = "
            "        'array' THEN {side} ELSE '[]'::jsonb END) e "
            "  WHERE {px} ~ {num} "
            "    AND abs(({px})::numeric - {wire}) < 0.0000005)").format(
                q=q, px=px, num=num, side=side, wire=wire)


#: THE SIDE A FILL CONSUMED (bettor_paper_simulator.side_consumed): offers
#: for a BUY of the long or a SELL of the short, bids otherwise
_SIDE = ("CASE WHEN (x.direction = 'SELL') = (x.holding_side = 'SHORT') "
         "THEN {b}.offers ELSE {b}.bids END")

#: EVERY FILL THAT CAN BE JUDGED, ON ITS READ. Per fill: its row, the FIRST
#: readable row of its read (same market, same receipt instant -- a shared
#: read is recorded once per caller with the original instant), and, for a
#: RESTING order, the readable row the simulator examined just before it
#: (same market, inside the order's window, after its placement row, by
#: obs_id -- exactly the rows `_resting` walks) with what that row and the
#: fill's own row show at the fill's wire. Only fills that are a copy, that
#: follow a row showing their wire, or that belong to a P0-rule group are
#: returned; `level_fills` counts every fill of the order at that wire.
CANONICAL_FILLS_SQL = """
    WITH x AS (
        SELECT f.fill_id, f.order_id, f.account_id, f.group_id,
               f.us_market_slug, f.holding_side, f.direction, f.role,
               f.qty, f.price, f.wire_price, f.book_obs_id, f.filled_at,
               extract(epoch FROM f.filled_at) AS filled_epoch,
               f.evidence->'level'->>'displayed' AS displayed_recorded,
               o.order_type, o.eligible_at, o.expires_at,
               CASE WHEN o.queue_basis->>'placement_obs_id' ~ '^[0-9]+$'
                    THEN (o.queue_basis->>'placement_obs_id')::bigint
                    ELSE 0 END AS placement_obs,
               count(*) OVER (PARTITION BY f.order_id, f.wire_price)
                   AS level_fills,
               count(*) OVER (PARTITION BY f.order_id, f.qty, f.price,
                                           f.filled_at) AS former_n
          FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
         WHERE ($1::text IS NULL OR f.account_id = $1)),
    j AS (
        SELECT x.*, extract(epoch FROM b.observed_at) AS read_epoch,
               b.source AS read_source, rd.first_obs AS read_first_obs,
               rd.rows_n AS read_rows, pv.obs_id AS prev_obs,
               extract(epoch FROM pv.observed_at) AS prev_read_epoch,
               """ + _shown_sql(_SIDE.format(b="pv"), "x.wire_price") + """
                   AS prev_shown,
               """ + _shown_sql(_SIDE.format(b="b"), "x.wire_price") + """
                   AS shown
          FROM x
          LEFT JOIN paper_book_observations b ON b.obs_id = x.book_obs_id
          LEFT JOIN LATERAL (
              SELECT min(b2.obs_id) AS first_obs, count(*) AS rows_n
                FROM paper_book_observations b2
               WHERE b2.us_market_slug = b.us_market_slug
                 AND b2.observed_at = b.observed_at
                 AND coalesce(b2.error, '') = '') rd ON true
          LEFT JOIN LATERAL (
              SELECT p.obs_id, p.observed_at, p.bids, p.offers
                FROM paper_book_observations p
               WHERE x.order_type = 'RESTING'
                 AND p.us_market_slug = x.us_market_slug
                 AND p.observed_at >= x.eligible_at
                 AND p.observed_at <= x.expires_at
                 AND p.obs_id < x.book_obs_id
                 AND p.obs_id > x.placement_obs
                 AND coalesce(p.error, '') = ''
               ORDER BY p.obs_id DESC LIMIT 1) pv ON true)
    SELECT fill_id, order_id, account_id, group_id, us_market_slug,
           holding_side, direction, role, order_type, qty, price,
           wire_price, book_obs_id, filled_at, filled_epoch, read_epoch,
           read_source, read_first_obs, read_rows, placement_obs, prev_obs,
           prev_read_epoch, prev_shown, shown, displayed_recorded,
           level_fills, former_n
      FROM j
     WHERE (read_first_obs IS NOT NULL AND book_obs_id <> read_first_obs)
        OR coalesce(prev_shown, 0) > 0 OR former_n > 1
     ORDER BY filled_at DESC, order_id, wire_price, book_obs_id
     LIMIT """ + str(MAX_CANONICAL_FILLS)

#: every fill the rule was applied over (the denominator of the receipt)
FILLS_SCANNED_SQL = """
    SELECT count(*) AS n, extract(epoch FROM min(filled_at)) AS oldest,
           count(*) FILTER (WHERE filled_at >= to_timestamp($2)) AS current_n
      FROM paper_fills WHERE ($1::text IS NULL OR account_id = $1)
"""


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def judge_fill(f: dict) -> dict:
    """Pure. THE CANONICAL VERDICT ON ONE FILL (a CANONICAL_FILLS_SQL row).

      SAME_READ_COPY          the fill's row is not the first readable row
                              of its read: the whole fill is the duplicate
      STILL_DISPLAYED_REFILL  a RESTING fill larger than what its row shows
                              above what the previous examined row showed at
                              the same wire (the producer's seen-crossing
                              memory): the excess is the duplicate
      None                    neither: the fill took liquidity the order had
                              not been offered before

    A fill whose own row cannot be read at its wire falls back to the level
    the simulator recorded on the fill; when neither is known the cap is 0
    (fail closed: unverifiable new liquidity is not assumed)."""
    qty = float(f["qty"])
    obs, first = f.get("book_obs_id"), f.get("read_first_obs")
    if obs is not None and first is not None and int(obs) != int(first):
        return {"kind": K_COPY, "dup_qty": round(qty, 6),
                "read_first_obs": int(first),
                "why": ("taken on row %d, a copy of the read first recorded "
                        "as row %d (same market, same receipt instant): "
                        "that read's liquidity was already offered on its "
                        "first row" % (int(obs), int(first)))}
    prev = _num(f.get("prev_shown"))
    if str(f.get("order_type") or "") == "RESTING" and prev is not None \
            and prev > DUP_QTY_EPS:
        shown = _num(f.get("shown"))
        if shown is None:
            shown = _num(f.get("displayed_recorded"))
        cap = max(0.0, (shown or 0.0) - prev)
        dup = round(qty - cap, 6)
        if dup > DUP_QTY_EPS:
            return {"kind": K_STILL, "dup_qty": dup, "new_liquidity": round(
                cap, 6), "shown": shown, "prev_shown": prev,
                "prev_obs": f.get("prev_obs"),
                "why": ("row %s shows %s at this wire; the row examined "
                        "before it (%s) showed %s, so at most %s was new "
                        "liquidity and %s was a level merely still "
                        "displayed" % (obs, shown, f.get("prev_obs"), prev,
                                       round(cap, 6), dup))}
    return {"kind": None, "dup_qty": 0.0}


def canonical_duplicates(fills: list, *,
                         fixed_at: float = PRODUCER_FIX_EFFECTIVE_AT) -> dict:
    """Pure. THE ECONOMIC DUPLICATES BY THE CANONICAL IDENTITY OF OBSERVED
    LIQUIDITY -- (market, consumed side, wire, READ) -- over
    CANONICAL_FILLS_SQL rows (`judge_fill` per fill):

      current / historical  per (order, wire level, window) the fills with a
                    duplicate quantity, filled at or after / before
                    `fixed_at` (the window labels history; it never hides a
                    new duplicate). `n` counts every fill of the order at
                    that wire, `extra_qty` sums the duplicate quantities.
      distinct_levels  the P0 rule's (order, qty, price, instant) groups in
                    which NO fill is a duplicate -- different levels taken
                    once each.
      former_rule   every P0-rule group with its verdict.
      positions     per (account, group, market, side): the duplicate
                    quantity bought and sold and its gross, so no claim on
                    those positions double counts it (rows never rewritten).
      fills_by_kind per window and kind: fills and quantity."""
    judged = [(f, judge_fill(f)) for f in fills or []]
    groups: dict = {}
    by_former: dict = {}
    positions: dict = {}
    by_kind: dict = {}
    for f, j in judged:
        if int(f.get("former_n") or 0) > 1:
            by_former.setdefault(
                (f["order_id"], float(f["qty"]), float(f["price"]),
                 str(f["filled_at"])), []).append((f, j))
        if j["dup_qty"] <= DUP_QTY_EPS:
            continue
        hist = float(f["filled_epoch"]) < float(fixed_at)
        win = "BEFORE_PRODUCER_FIX" if hist else "AT_OR_AFTER_PRODUCER_FIX"
        g = groups.setdefault((f["order_id"], _wire(f["wire_price"]), hist), {
            "order_id": f["order_id"], "role": f.get("role"),
            "account_id": f.get("account_id"), "group_id": f.get("group_id"),
            "us_market_slug": f.get("us_market_slug"),
            "holding_side": f.get("holding_side"),
            "direction": f.get("direction"),
            "wire": float(f["wire_price"]), "qty": float(f["qty"]),
            "price": float(f["price"]),
            "n": int(f.get("level_fills") or 1), "extra_qty": 0.0,
            "filled_epoch": float(f["filled_epoch"]),
            "filled_at": str(f["filled_at"]),
            "label": L_HISTORICAL if hist else L_SUSPECT, "window": win,
            "kinds": [], "duplicate_fills": []})
        g["extra_qty"] = round(g["extra_qty"] + j["dup_qty"], 6)
        if float(f["filled_epoch"]) > g["filled_epoch"]:
            g["filled_epoch"] = float(f["filled_epoch"])
            g["filled_at"] = str(f["filled_at"])
        if j["kind"] not in g["kinds"]:
            g["kinds"].append(j["kind"])
        g["duplicate_fills"].append(dict(
            {k: v for k, v in j.items() if k != "dup_qty"},
            fill_id=f["fill_id"], book_obs_id=f["book_obs_id"],
            qty=float(f["qty"]), dup_qty=j["dup_qty"],
            read_epoch=_num(f.get("read_epoch"))))
        pk = _key(f.get("account_id"), f.get("group_id"),
                  f.get("us_market_slug"), f.get("holding_side"))
        p = positions.setdefault(pk, {
            "position_key": pk, "group_id": f.get("group_id"),
            "us_market_slug": f.get("us_market_slug"),
            "holding_side": f.get("holding_side"),
            "duplicate_qty_sold": 0.0, "duplicate_qty_bought": 0.0,
            "duplicate_gross_usd": 0.0, "windows": []})
        side = ("duplicate_qty_sold" if f.get("direction") == "SELL"
                else "duplicate_qty_bought")
        p[side] = round(p[side] + j["dup_qty"], 6)
        p["duplicate_gross_usd"] = round(
            p["duplicate_gross_usd"] + j["dup_qty"] * float(f["price"]), 6)
        if win not in p["windows"]:
            p["windows"].append(win)
        k = by_kind.setdefault(win, {}).setdefault(
            j["kind"], {"fills": 0, "qty": 0.0})
        k["fills"] += 1
        k["qty"] = round(k["qty"] + j["dup_qty"], 6)
    for g in groups.values():
        g["basis"] = (
            "%s: %d fill(s) of one order at wire %s carried %s contracts of "
            "liquidity the order had already been offered (the READ is the "
            "canonical identity: market, consumed side, wire, receipt "
            "instant); append-only PAPER history, never rewritten"
            % ("+".join(g["kinds"]), len(g["duplicate_fills"]),
               _wire(g["wire"]), g["extra_qty"]))
    distinct, former = [], []
    for key, fjs in by_former.items():
        fs = [f for f, _j in fjs]
        dup_qty = round(sum(j["dup_qty"] for _f, j in fjs), 6)
        former.append({"order_id": key[0], "qty": key[1], "price": key[2],
                       "filled_at": key[3], "n": len(fs),
                       "verdict": ("DUPLICATE" if dup_qty > DUP_QTY_EPS
                                   else "NOT_A_DUPLICATE"),
                       "duplicate_qty": dup_qty,
                       "kinds": sorted({j["kind"] for _f, j in fjs
                                        if j["kind"]})})
        if dup_qty <= DUP_QTY_EPS:
            distinct.append(_group(fs, label=L_DISTINCT, basis=(
                "different wire levels of one size taken once each, every "
                "one on the first row of its read and above what the row "
                "examined before it showed -- not a duplicate")))

    def _newest_first(gs):
        return sorted(gs, key=lambda g: (g["filled_epoch"], g["order_id"],
                                         g.get("wire") or 0.0),
                      reverse=True)
    cur = [g for (_o, _w, hist), g in groups.items() if not hist]
    his = [g for (_o, _w, hist), g in groups.items() if hist]
    return {"current": _newest_first(cur), "historical": _newest_first(his),
            "distinct_levels": _newest_first(distinct),
            "former_rule": sorted(former, key=lambda r: (
                r["filled_at"], r["order_id"], r["qty"]), reverse=True),
            "former_rule_groups": len(by_former),
            "former_rule_groups_with_a_duplicate": sum(
                1 for r in former if r["verdict"] == "DUPLICATE"),
            "positions": sorted(positions.values(), key=lambda p: (
                -p["duplicate_qty_sold"] - p["duplicate_qty_bought"],
                p["position_key"])),
            "fills_by_kind": by_kind}


# ═════════════════════════════════════════════════════════════════════
# THE PAPER ORDER LIFECYCLE AND ITS QUANTITY ACCOUNTING (RC6)
# ═════════════════════════════════════════════════════════════════════

#: every PAPER order by role, type and state, with what it filled
ORDER_LIFECYCLE_SQL = """
    SELECT o.role, o.order_type, o.state, count(*) AS orders,
           round(sum(o.qty), 6) AS qty, round(sum(o.filled_qty), 6) AS filled,
           count(*) FILTER (WHERE o.filled_qty > 0) AS with_fill,
           count(*) FILTER (WHERE o.filled_qty > 0 AND o.filled_qty < o.qty)
               AS partly_filled
      FROM paper_orders o WHERE ($1::text IS NULL OR o.account_id = $1)
     GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
"""

#: THE LIFECYCLE INVARIANTS, each a count that must be 0 (the ledger's own
#: transitions: PENDING_SIMULATION -> RESTING -> PARTIALLY_FILLED ->
#: FILLED, or -> CANCEL_PENDING -> CANCELED, or -> EXPIRED; REJECTED never
#: fills). Read in one statement over the orders, their fills and events.
LIFECYCLE_BREAKS_SQL = """
    WITH o AS (SELECT * FROM paper_orders
                WHERE ($1::text IS NULL OR account_id = $1)),
         s AS (SELECT order_id, sum(qty) AS q, count(*) AS n
                 FROM paper_fills WHERE ($1::text IS NULL OR account_id = $1)
                GROUP BY 1),
         e AS (SELECT ev.order_id,
                      count(*) FILTER (WHERE ev.kind = 'FILL') AS fill_ev,
                      bool_or(ev.kind = 'EXPIRED') AS expired_ev,
                      bool_or(ev.kind = 'CANCELED') AS canceled_ev,
                      bool_or(ev.kind = 'REJECTED') AS rejected_ev
                 FROM paper_order_events ev JOIN o USING (order_id)
                GROUP BY 1),
         p AS (SELECT account_id, group_id, us_market_slug, holding_side,
                      coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0)
                          AS bought,
                      coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0)
                          AS sold
                 FROM paper_fills WHERE ($1::text IS NULL OR account_id = $1)
                GROUP BY 1, 2, 3, 4)
    SELECT count(*) AS orders,
           count(*) FILTER (WHERE abs(coalesce(s.q, 0) - o.filled_qty) > 1e-6)
               AS fills_sum_ne_filled_qty,
           count(*) FILTER (WHERE o.state = 'FILLED'
                              AND o.filled_qty < o.qty - 1e-6)
               AS filled_state_not_complete,
           count(*) FILTER (WHERE o.state IN ('REJECTED', 'PENDING_SIMULATION')
                              AND o.filled_qty > 0)
               AS rejected_or_pending_with_fill,
           count(*) FILTER (WHERE o.state = 'RESTING' AND o.filled_qty > 0)
               AS resting_with_fill,
           count(*) FILTER (WHERE o.state = 'PARTIALLY_FILLED'
                              AND (o.filled_qty <= 0
                                   OR o.filled_qty >= o.qty))
               AS partial_without_fill_or_complete,
           count(*) FILTER (WHERE o.state IN ('FILLED', 'EXPIRED', 'CANCELED',
                                              'REJECTED')
                              AND o.terminal_at IS NULL)
               AS terminal_without_terminal_at,
           count(*) FILTER (WHERE o.state IN ('PENDING_SIMULATION', 'RESTING',
                                              'PARTIALLY_FILLED',
                                              'CANCEL_PENDING')
                              AND o.terminal_at IS NOT NULL)
               AS open_with_terminal_at,
           count(*) FILTER (WHERE o.state IN ('FILLED', 'EXPIRED', 'CANCELED',
                                              'REJECTED')
                              AND o.reserved_remaining_usd > 0)
               AS terminal_with_reservation_left,
           count(*) FILTER (WHERE coalesce(e.fill_ev, 0) <> coalesce(s.n, 0))
               AS fill_events_ne_fills,
           count(*) FILTER (WHERE (o.state = 'EXPIRED'
                                   AND NOT coalesce(e.expired_ev, false))
                               OR (o.state = 'CANCELED'
                                   AND NOT coalesce(e.canceled_ev, false))
                               OR (o.state = 'REJECTED'
                                   AND NOT coalesce(e.rejected_ev, false)))
               AS terminal_without_terminal_event,
           count(*) FILTER (WHERE o.direction = 'SELL' AND NOT EXISTS (
                   SELECT 1 FROM p WHERE p.account_id = o.account_id
                      AND p.group_id = o.group_id
                      AND p.us_market_slug = o.us_market_slug
                      AND p.holding_side = o.holding_side AND p.bought > 0))
               AS sale_without_a_position,
           (SELECT count(*) FROM p WHERE p.sold > p.bought + 1e-6)
               AS positions_sold_beyond_bought,
           count(*) FILTER (WHERE o.state IN ('PENDING_SIMULATION', 'RESTING',
                                              'PARTIALLY_FILLED',
                                              'CANCEL_PENDING')
                              AND o.expires_at < now())
               AS open_orders_past_expiry
      FROM o LEFT JOIN s USING (order_id) LEFT JOIN e USING (order_id)
"""
#: the counts above that are BREAKS (must be 0); open_orders_past_expiry is
#: the simulator's lag, reported beside them (every protection reader treats
#: such an order as expired: bettor_paper_freshness.protection_state)
LIFECYCLE_BREAKS = (
    "fills_sum_ne_filled_qty", "filled_state_not_complete",
    "rejected_or_pending_with_fill", "resting_with_fill",
    "partial_without_fill_or_complete", "terminal_without_terminal_at",
    "open_with_terminal_at", "terminal_with_reservation_left",
    "fill_events_ne_fills", "terminal_without_terminal_event",
    "sale_without_a_position", "positions_sold_beyond_bought")

#: THE VENUE-CONFIRMED (ACTUAL) BOOK, read APART: the execution mirror's
#: orders and venue fills. Never added to a PAPER count; reported under its
#: own label so no readback can mistake a simulated fill for a venue one.
VENUE_CONFIRMED_SQL = """
    SELECT 'execmirror_orders' AS source, state, count(*) AS rows_n,
           count(*) FILTER (WHERE venue_order_id IS NOT NULL) AS at_venue
      FROM execmirror_orders GROUP BY 1, 2
    UNION ALL
    SELECT 'execmirror_fills', 'VENUE_FILL', count(*), count(*)
      FROM execmirror_fills
"""


async def _lifecycle_reads(conn, account_id) -> tuple:
    """The lifecycle census and invariants, and the venue-confirmed book,
    each in its own savepoint: a failed read is UNAVAILABLE with its
    reason, never zeros, and never takes the rest of the receipt with it."""
    life: dict = {"census": None, "breaks": lifecycle_breaks(None)}
    try:
        async with conn.transaction():
            life["census"] = [
                {k: (float(v) if k in ("qty", "filled") and v is not None
                     else v) for k, v in dict(r).items()}
                for r in await conn.fetch(ORDER_LIFECYCLE_SQL, account_id)]
            row = await conn.fetchrow(LIFECYCLE_BREAKS_SQL, account_id)
            life["breaks"] = lifecycle_breaks(
                None if row is None else dict(row))
    except Exception as exc:                                    # noqa: BLE001
        life.update(census=None, breaks=lifecycle_breaks(None),
                    why="ORDER_LIFECYCLE_READ_FAILED: %s: %s"
                        % (type(exc).__name__, str(exc)[:160]))
    venue: dict = {"label": "ACTUAL (venue-confirmed) -- read apart, never "
                            "added to a PAPER count", "rows": None}
    try:
        async with conn.transaction():
            if await conn.fetchval(
                    "SELECT to_regclass('execmirror_orders') IS NOT NULL "
                    "   AND to_regclass('execmirror_fills') IS NOT NULL"):
                venue["rows"] = [dict(r) for r in await conn.fetch(
                    VENUE_CONFIRMED_SQL)]
                venue["venue_fills"] = sum(
                    int(r["rows_n"]) for r in venue["rows"]
                    if r["source"] == "execmirror_fills")
                venue["orders_at_venue"] = sum(
                    int(r["at_venue"]) for r in venue["rows"]
                    if r["source"] == "execmirror_orders")
            else:
                venue["why_unavailable"] = "EXECUTION_MIRROR_TABLES_ABSENT"
    except Exception as exc:                                    # noqa: BLE001
        venue["why_unavailable"] = "VENUE_CONFIRMED_READ_FAILED: %s" % (
            type(exc).__name__)
    return life, venue


def lifecycle_breaks(row: dict | None) -> dict:
    """Pure. The invariant counts with the total of BREAKS (None when the
    read failed: never 0 by default)."""
    if row is None:
        return {"breaks": None, "counts": None}
    counts = {k: int(row.get(k) or 0) for k in LIFECYCLE_BREAKS}
    return {"breaks": sum(counts.values()), "counts": counts,
            "orders": int(row.get("orders") or 0),
            "open_orders_past_expiry": int(row.get(
                "open_orders_past_expiry") or 0)}

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
                CANONICAL_FILLS_SQL, account_id)]
            scanned = dict(await conn.fetchrow(
                FILLS_SCANNED_SQL, account_id,
                float(PRODUCER_FIX_EFFECTIVE_AT)))
            live_closed = [dict(r) for r in await conn.fetch(
                LIVE_PROTECTION_ON_CLOSED_SQL, account_id)]
            life, venue = await _lifecycle_reads(conn, account_id)
    except Exception as exc:                                    # noqa: BLE001
        out.update(status="UNAVAILABLE",
                   why="RECONCILIATION_READ_FAILED: %s: %s"
                       % (type(exc).__name__, str(exc)[:160]))
        return out
    dup = canonical_duplicates(cand)
    cur, hist = dup["current"], dup["historical"]
    truncated = len(cand) >= MAX_CANONICAL_FILLS
    # THE CURRENT COUNT IS NEVER CLAIMED FROM A TRUNCATED READ: the judged
    # fills are read newest first, so the current window is complete unless
    # the read stopped inside it -- then the count is unknown (None), never 0
    current_complete = not truncated or (
        bool(cand) and float(cand[-1]["filled_epoch"])
        < PRODUCER_FIX_EFFECTIVE_AT)

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
            # CURRENT: a duplicate (SAME_READ_COPY or STILL_DISPLAYED_REFILL)
            # filled at or after the producer fix -- the number that must be
            # zero; None when the read could not cover the current window
            "economic_duplicate_suspect_groups": (
                len(cur) if current_complete else None),
            "economic_duplicate_suspect_extra_qty": (
                _extra(cur) if current_complete else None),
            # HISTORICAL: before the fix; immutable PAPER history, counted
            # and listed on its own, never rewritten or dropped
            "historical_economic_duplicate_groups": len(hist),
            "historical_economic_duplicate_extra_qty": _extra(hist),
            "economic_duplicate_fills_by_kind": dup["fills_by_kind"],
            "positions_with_economic_duplicates": len(dup["positions"]),
            # what the P0 rule called a duplicate that is different levels
            # taken once each
            "same_instant_distinct_level_groups": len(
                dup["distinct_levels"]),
            "former_rule_groups": dup["former_rule_groups"],
            "former_rule_groups_with_a_duplicate": dup[
                "former_rule_groups_with_a_duplicate"],
            "fills_scanned": int(scanned.get("n") or 0),
            "fills_scanned_in_current_window": int(
                scanned.get("current_n") or 0),
            "duplicate_candidate_fills": len(cand),
            "duplicate_candidates_truncated": truncated,
            "live_protection_on_closed_positions": len(live_closed),
            # THE ORDER LIFECYCLE: every invariant break of the PAPER
            # orders' state machine and quantity accounting (None when the
            # read failed -- never 0 by default)
            "order_lifecycle_breaks": life["breaks"]["breaks"],
            "open_orders_past_expiry": life["breaks"].get(
                "open_orders_past_expiry")},
        sections={
            "legacy_reader_phantoms": phantoms,
            "true_open": cls["true_open"][:500],
            "sub_contract_remainders": cls["sub_contract_remainders"],
            "economic_duplicate_rule": {
                "rule": ("the canonical identity of observed liquidity is "
                         "the READ (market, consumed side, wire, receipt "
                         "instant), not the observation row. SAME_READ_COPY: "
                         "a fill on a row that is not the first readable "
                         "row of its read (the whole fill). "
                         "STILL_DISPLAYED_REFILL: a resting fill larger "
                         "than what its row shows above what the row the "
                         "order examined before it showed at that wire (the "
                         "excess)"),
                "identity": ["us_market_slug", "side_consumed",
                             "wire_price", "observed_at"],
                "kinds": list(DUP_KINDS),
                "current_complete": current_complete,
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
            "former_rule_groups": dup["former_rule"][:500],
            "positions_with_economic_duplicates": dup["positions"][:500],
            "live_protection_on_closed_positions": live_closed,
            "order_lifecycle": {
                "execution_environment": "PAPER_SIMULATED",
                "by_role_type_state": life["census"],
                "invariants": life["breaks"].get("counts"),
                "invariants_must_be_zero": list(LIFECYCLE_BREAKS),
                "why_unavailable": life.get("why")},
            # THE VENUE-CONFIRMED BOOK, APART: never added to a PAPER count
            "venue_confirmed_book": venue})
    return out
