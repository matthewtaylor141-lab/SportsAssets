"""THE DEREK -> XAVIER HANDOFF: OWNERSHIP FOLLOWS CONFIRMED FILLS, ONLY.

WHAT DECIDES OWNERSHIP. The fills ledger (`bettor_funded_fills`, the one
book). Not an intent row (a recorded intent owns nothing: it may never be
sent), not an acknowledgement (an acknowledged order may fill nothing), not a
cycle's report. The moment the ledger holds an ENTRY fill for an entry intent,
that fill's quantity is Xavier's; the rest of the order is tracked as
OUTSTANDING -- the entry's remaining open obligation -- until the order is
terminal, when it is 0.

WHICH INTENTS ARE DEREK'S ENTRIES, READ FROM THE SCHEMA (migrations 125, 126,
131, and the connector's `decision_ref`):
  * `kind = 'ENTRY'` -- an EXIT intent (kind 'EXIT', parent_intent_id set) is
    management's own order and transfers nothing;
  * its fills with `direction = 'ENTRY'`;
  * `decision_ref->>'xavier_decision_id' IS NULL` -- an ENTRY intent that
    names a Xavier decision is a hedge leg Xavier ACQUIRED through its claim
    path (`bettor_funded_execution.submit_for_decision` writes the id onto
    the intent). Xavier originated it; there is nothing to hand over.

DURABLE AND IDEMPOTENT. One `agent_position_handoffs` row per entry intent
(its primary key), one `agent_handoff_fills` row per ledger fill (keyed by the
ledger's own `fill_id`). Later fills update the SAME row; a replay, a restart
or a second concurrent consumer writes nothing new. Quantities are recomputed
from the ledger on every write, never incremented.

NOT AN ORDER PATH AND NOT A BOOK. Nothing here sends, cancels or recovers
anything, and nothing here is read by accounting.
"""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

FROM_AGENT = "DEREK"
OWNER_AGENT = "XAVIER"

#: The entry order's states in which quantity may still arrive
#: (`bettor_funded_order_is_outstanding`, migration 126). UNRESOLVED is one:
#: an unknown outcome is exposure that stands, never zero.
OUTSTANDING_STATES = ("INTENT_RECORDED", "SEND_ATTEMPTED", "ACKNOWLEDGED",
                      "PARTIALLY_FILLED", "UNRESOLVED")

R_NO_SUCH_INTENT = "NO_SUCH_FUNDED_INTENT"
S_NOT_AN_ENTRY = "NOT_AN_ENTRY_INTENT_SO_NOTHING_TRANSFERS"
S_XAVIER_ORIGINATED = "ENTRY_LEG_ACQUIRED_BY_XAVIER_SO_ALREADY_XAVIERS"
S_NO_CONFIRMED_FILL = "NO_CONFIRMED_FILL_SO_NO_OWNERSHIP_TRANSFERS"

RECONCILE_LIMIT = 200

#: Derek's entry intents, as the schema distinguishes them.
DEREK_ENTRY_PREDICATE = (
    "i.kind = 'ENTRY' AND i.parent_intent_id IS NULL "
    "AND (i.decision_ref->>'xavier_decision_id') IS NULL")


def _outstanding(state, ordered, confirmed) -> float:
    if str(state or "") not in OUTSTANDING_STATES:
        return 0.0
    return max(0.0, float(ordered) - float(confirmed))


async def on_fills(conn, *, intent_id, now: float | None = None) -> dict:
    """RECOMPUTE ONE ENTRY INTENT'S HANDOFF FROM THE FILLS LEDGER.

    Inserts one `agent_handoff_fills` row per ledger fill (ON CONFLICT on the
    ledger's own fill id) and upserts the intent's single handoff row:
    confirmed_qty = the recorded fills' ledger quantity, ordered_qty = the
    intent's quantity, outstanding_qty = what the order may still fill (0 once
    terminal). No confirmed fill -> no row. Raises only on a database error;
    the book's caller contains that (`bettor_funded_book.ingest_fills`)."""
    at = float(now if now is not None else time.time())
    it = await conn.fetchrow(
        "SELECT intent_id, kind, parent_intent_id, portfolio_group_id, "
        "       quantity::float8 AS quantity, state, "
        "       (decision_ref->>'xavier_decision_id') AS xavier_decision_id "
        "  FROM bettor_funded_intents WHERE intent_id=$1", str(intent_id))
    if it is None:
        return {"ok": False, "refusal": R_NO_SUCH_INTENT,
                "entry_intent_id": str(intent_id)}
    base = {"ok": True, "entry_intent_id": str(intent_id), "handoff": None}
    if it["kind"] != "ENTRY" or it["parent_intent_id"] is not None:
        return dict(base, skipped=S_NOT_AN_ENTRY)
    if it["xavier_decision_id"]:
        return dict(base, skipped=S_XAVIER_ORIGINATED,
                    xavier_decision_id=it["xavier_decision_id"])
    fills = await conn.fetch(
        "SELECT fill_id, qty::float8 AS qty, extract(epoch FROM at)::float8 "
        "       AS at FROM bettor_funded_fills "
        " WHERE intent_id=$1 AND direction='ENTRY' ORDER BY at, fill_id",
        str(intent_id))
    if not fills:
        # A REJECTED, UNFILLED, UNSENT OR MERELY ACKNOWLEDGED ORDER: no
        # inventory exists, so nobody is made its owner.
        return dict(base, skipped=S_NO_CONFIRMED_FILL)
    ordered = float(it["quantity"])
    first, last = fills[0], fills[-1]
    total = sum(float(f["qty"]) for f in fills)
    new_fills = 0
    async with conn.transaction():
        res = await conn.execute(
            "INSERT INTO agent_position_handoffs (entry_intent_id, "
            " portfolio_group_id, from_agent, owner_agent, ordered_qty, "
            " confirmed_qty, outstanding_qty, first_fill_id, first_fill_at, "
            " last_fill_id, last_fill_at, handoff_at, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,to_timestamp($9),$10,"
            " to_timestamp($11),to_timestamp($12),to_timestamp($12)) "
            "ON CONFLICT (entry_intent_id) DO NOTHING",
            str(intent_id), it["portfolio_group_id"], FROM_AGENT, OWNER_AGENT,
            ordered, total, _outstanding(it["state"], ordered, total),
            first["fill_id"], first["at"], last["fill_id"], last["at"], at)
        created = res.endswith("1")
        # ONE WRITER AT A TIME PER HANDOFF, whatever connection it is on.
        await conn.execute(
            "SELECT 1 FROM agent_position_handoffs WHERE entry_intent_id=$1 "
            "   FOR UPDATE", str(intent_id))
        for f in fills:
            # THE LEDGER'S OWN IDENTITY. A replay conflicts and writes
            # nothing; the DO UPDATE only ever fires when the mirror row no
            # longer matches the ledger (never for an immutable ledger fill).
            got = await conn.fetchval(
                "INSERT INTO agent_handoff_fills (fill_id, entry_intent_id, "
                " qty, filled_at, recorded_at) "
                "VALUES ($1,$2,$3,to_timestamp($4),to_timestamp($5)) "
                "ON CONFLICT (fill_id) DO UPDATE SET "
                " entry_intent_id=EXCLUDED.entry_intent_id, "
                " qty=EXCLUDED.qty, filled_at=EXCLUDED.filled_at "
                " WHERE (agent_handoff_fills.entry_intent_id, "
                "        agent_handoff_fills.qty, "
                "        agent_handoff_fills.filled_at) IS DISTINCT FROM "
                "       (EXCLUDED.entry_intent_id, EXCLUDED.qty, "
                "        EXCLUDED.filled_at) "
                "RETURNING (xmax = 0)",
                f["fill_id"], str(intent_id), float(f["qty"]), f["at"], at)
            if got:
                new_fills += 1
        agg = await conn.fetchrow(
            "SELECT coalesce(sum(b.qty), 0)::float8 AS confirmed, "
            "       (array_agg(b.fill_id ORDER BY b.at, b.fill_id))[1] AS ff, "
            "       min(b.at) AS ffa, "
            "       (array_agg(b.fill_id ORDER BY b.at DESC, b.fill_id DESC)"
            "        )[1] AS lf, max(b.at) AS lfa "
            "  FROM agent_handoff_fills h JOIN bettor_funded_fills b "
            "    ON b.fill_id = h.fill_id AND b.intent_id = h.entry_intent_id "
            "   AND b.direction = 'ENTRY' "
            " WHERE h.entry_intent_id=$1", str(intent_id))
        confirmed = float(agg["confirmed"])
        outstanding = _outstanding(it["state"], ordered, confirmed)
        await conn.execute(
            "UPDATE agent_position_handoffs SET "
            " portfolio_group_id=$2, ordered_qty=$3, confirmed_qty=$4, "
            " outstanding_qty=$5, first_fill_id=$6, first_fill_at=$7, "
            " last_fill_id=$8, last_fill_at=$9, updated_at=to_timestamp($10) "
            " WHERE entry_intent_id=$1",
            str(intent_id), it["portfolio_group_id"], ordered, confirmed,
            outstanding, agg["ff"], agg["ffa"], agg["lf"], agg["lfa"], at)
    return dict(base, handoff={
        "entry_intent_id": str(intent_id),
        "portfolio_group_id": it["portfolio_group_id"],
        "from_agent": FROM_AGENT, "owner_agent": OWNER_AGENT,
        "ordered_qty": ordered, "confirmed_qty": confirmed,
        "outstanding_qty": outstanding, "intent_state": it["state"]},
        created=created, fills_recorded=new_fills,
        fills_in_ledger=len(fills))


UNOWNED_SQL = """
    WITH f AS (
        SELECT intent_id, sum(qty) AS q
          FROM bettor_funded_fills WHERE direction = 'ENTRY'
         GROUP BY intent_id)
    SELECT i.intent_id, (h.entry_intent_id IS NULL) AS unowned
      FROM bettor_funded_intents i
      JOIN f ON f.intent_id = i.intent_id
      LEFT JOIN agent_position_handoffs h ON h.entry_intent_id = i.intent_id
     WHERE {pred}
       AND (h.entry_intent_id IS NULL
            OR h.confirmed_qty <> f.q
            OR (h.outstanding_qty > 0
                AND NOT bettor_funded_order_is_outstanding(i.state))
            OR EXISTS (
                SELECT 1 FROM bettor_funded_fills b
                 WHERE b.intent_id = i.intent_id AND b.direction = 'ENTRY'
                   AND NOT EXISTS (
                       SELECT 1 FROM agent_handoff_fills a
                        WHERE a.fill_id = b.fill_id
                          AND a.entry_intent_id = i.intent_id)))
     ORDER BY i.intent_id
     LIMIT $1
""".format(pred=DEREK_ENTRY_PREDICATE)


async def reconcile_unowned(conn, *, now: float | None = None,
                            limit: int = RECONCILE_LIMIT) -> dict:
    """FIND AND REPAIR INVENTORY WITHOUT A MANAGER. Never raises.

    Every Derek entry intent whose ledger holds ENTRY fills with no handoff
    row, a handoff that no longer matches the ledger, a fill the handoff has
    not recorded, or an outstanding quantity on an order that is now terminal.
    Called in every servicing pass, so a missed hook, a crash between the
    fills commit and the hook, or a restart cannot leave held contracts
    without an owner for longer than one pass."""
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"ok": True, "at": at, "examined": 0,
                           "created": 0, "refreshed": 0, "repaired": [],
                           "failed": []}
    try:
        rows = await conn.fetch(UNOWNED_SQL, int(limit))
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal="HANDOFF_SCAN_FAILED",
                    error=type(exc).__name__)
    for r in rows:
        out["examined"] += 1
        try:
            got = await on_fills(conn, intent_id=r["intent_id"], now=at)
        except Exception as exc:                                # noqa: BLE001
            out["failed"].append({"entry_intent_id": r["intent_id"],
                                  "error": type(exc).__name__})
            continue
        if got.get("handoff") is None:
            continue
        out["repaired"].append(r["intent_id"])
        if got.get("created"):
            out["created"] += 1
        else:
            out["refreshed"] += 1
    if out["failed"]:
        out["ok"] = False
    try:
        out["owned_open_positions"] = await owned_open_positions(conn)
    except Exception as exc:                                    # noqa: BLE001
        # UNKNOWN IS NOT ZERO.
        out["owned_open_positions"] = None
        out["owned_read_error"] = type(exc).__name__
    return out


async def owned_open_positions(conn, *, account_id: str | None = None,
                               venue: str | None = None) -> int:
    """How many handed-over positions are still open (held or with an order
    outstanding). Raises on a failed read -- the caller must not read a
    failure as zero."""
    return int(await conn.fetchval(
        "SELECT count(*) FROM agent_position_handoffs h "
        "  JOIN bettor_funded_intents i ON i.intent_id = h.entry_intent_id "
        " WHERE bettor_funded_position_is_open(i.state, i.residual_qty, "
        "                                      i.closed_at) "
        "   AND ($1::text IS NULL OR i.account_id = $1) "
        "   AND ($2::text IS NULL OR i.venue = $2)", account_id, venue))


async def handoffs(conn, *, limit: int = 50, entry_intent_id=None) -> list:
    """The handoff rows with their fills and the position's live state, for
    the API. Raises on a failed read."""
    rows = await conn.fetch(
        "SELECT h.*, i.state AS intent_state, "
        "       i.residual_qty::float8 AS residual_qty, i.closed_at, "
        "       i.closed_reason, i.account_id, i.venue, i.us_market_slug, "
        "       bettor_funded_position_is_open(i.state, i.residual_qty, "
        "                                      i.closed_at) AS position_open "
        "  FROM agent_position_handoffs h "
        "  LEFT JOIN bettor_funded_intents i "
        "    ON i.intent_id = h.entry_intent_id "
        " WHERE ($2::text IS NULL OR h.entry_intent_id = $2) "
        " ORDER BY h.updated_at DESC, h.entry_intent_id LIMIT $1",
        max(1, min(int(limit or 50), 500)),
        None if entry_intent_id is None else str(entry_intent_id))
    ids = [r["entry_intent_id"] for r in rows]
    fills: dict[str, list] = {}
    if ids:
        for f in await conn.fetch(
                "SELECT fill_id, entry_intent_id, qty::float8 AS qty, "
                "       extract(epoch FROM filled_at)::float8 AS filled_at, "
                "       extract(epoch FROM recorded_at)::float8 AS recorded_at"
                "  FROM agent_handoff_fills WHERE entry_intent_id = ANY($1) "
                " ORDER BY filled_at, fill_id", ids):
            fills.setdefault(f["entry_intent_id"], []).append(dict(f))
    out = []
    for r in rows:
        d = {}
        for k, v in dict(r).items():
            if hasattr(v, "timestamp"):
                v = v.timestamp()
            elif hasattr(v, "as_tuple"):
                v = float(v)
            d[k] = v
        d["fills"] = fills.get(d["entry_intent_id"], [])
        d["evidence"] = (
            [{"kind": "bettor_funded_intents", "id": d["entry_intent_id"],
              "href": "/api/command/xavier/%s" % d["entry_intent_id"]}]
            + [{"kind": "bettor_funded_fills", "id": f["fill_id"],
                "href": "/api/command/agents/handoffs?entry_intent_id=%s"
                % d["entry_intent_id"]} for f in d["fills"]])
        out.append(d)
    return out
