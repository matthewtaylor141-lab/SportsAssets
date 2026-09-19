"""PROSPECTIVE RN1 OBSERVATION, AND THE DECISION THAT MAY FOLLOW IT.

Owner directive 2026-09-19: "START ACCUMULATING PROSPECTIVE RN1_SHADOW
EVIDENCE AS SOON AS POSSIBLE. Do not wait for COMMAND UI, PDF reporting
or the complete API before prospective collection begins."

THE SPLIT, AND WHY IT IS TWO STEPS AND NOT ONE.

  STEP 1, at arrival, on the ingestion path: write the OBSERVATION.
  No venue call, no book read, nothing that can block or fail slowly.
  The one fact only this system holds is WHEN BETTOR HEARD, and it is
  worthless if it is measured after a network round trip. This step is
  contained so completely that it cannot affect ingestion: a shadow
  ledger that can drop a real fill is not an instrument, it is a new
  way to lose money.

  STEP 2, in its own worker: read the book, decide, write the DECISION.
  This is slower, because getting a book is slower, and that is the
  point -- the gap between bettor_received_ts and decision_ts is a
  MEASUREMENT of this system, not an embarrassment to hide by writing
  both timestamps at once.

OBSERVATION IS NOT AUTOMATICALLY A TRADE. Every sighting gets a
decision row, and most early ones will be NO_TRADE with a named
blocker. Those rows are the evidence: "If the engine is blocked: show
the blocker" is only possible if the blocker was written down.

DO NOT CHANGE RN1 DETECTION METHODOLOGY TO CREATE MORE ROWS. Nothing
here alters what the chain listener or the poller look for, how often
they look, or what they accept. This module reads the fills those paths
already produce and writes beside them.

RN1_PRICE IS NOT BETTOR'S EXECUTABLE PRICE. It is recorded because the
comparison is the whole point of the lane, and it is never the price a
shadow fill is struck at. The shadow price comes from the book BETTOR
actually saw, at the instant BETTOR actually arrived.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from . import shadow as sh
from . import shadow_lanes as lanes
from . import shadow_policy as pol
from . import shadow_store as store

log = logging.getLogger(__name__)

MODEL_VERSION = "rn1_shadow_mechanism_v1"

# The evidence source for the SIGNAL. The market state carries its own,
# separately, because they are genuinely different feeds and merging
# them is the failure this whole design is built against.
SIGNAL_SOURCE = "RN1_OBSERVED_FILL"

SOURCE_TYPE_OF_LANE = {
    "chain": "CHAIN",
    "s1": "S1",
    "poll": "POLL",
    "backfill": "BACKFILL",
    "reconciler": "RECONCILER",
}

# reason / blocker vocabulary, so COMMAND can name a blocker rather
# than showing an empty panel
B_NO_MARKET_STATE = "MARKET_STATE_NOT_CAPTURED"
B_BOOK_UNREADABLE = "BOOK_UNREADABLE"
B_NO_DEPTH = "OBSERVED_DEPTH_NOT_ESTABLISHED"
B_SIZE_FLOORS_TO_ZERO = "SIZE_FLOORS_TO_ZERO"
B_NO_SYMBOL = "SYMBOL_NOT_RESOLVED"
R_MIRROR_RN1_SIDE = "RN1_MECHANISM_MIRROR_OF_OBSERVED_SIDE"
R_SIZED_BY_FROZEN_POLICY = "SIZED_BY_FROZEN_SIZING_POLICY"


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ── STEP 1: the sighting ─────────────────────────────────────────────


def observation_from_trade_event(ev, *, received_at=None,
                                 source_type=None) -> dict:
    """Build the prospective observation from a detected RN1 fill.

    PURE. No database, no venue, no clock beyond the one the caller
    hands in -- because the caller stamps `received_at` at the instant
    the event entered the process, and a clock read inside here would
    measure this function instead.

    THE SOURCE TIMESTAMP'S STATUS TRAVELS WITH IT. The ingestion path
    already knows when a block timestamp had to be substituted with our
    own wall clock; that fact is carried through as
    FALLBACK_SUBSTITUTED rather than being laundered into a venue
    instant we never received.
    """
    lane_name = getattr(ev, "source", None)
    stype = source_type or SOURCE_TYPE_OF_LANE.get(lane_name)
    if stype is None:
        raise store.StoreRefusal(
            "refused: %r is not a detection lane this store recognises"
            % lane_name)

    ts_epoch = getattr(ev, "ts_epoch", None)
    fallback = bool(getattr(ev, "ts_fallback", False))
    if not ts_epoch:
        source_ts, status = None, store.TS_MISSING
    elif fallback:
        source_ts = datetime.fromtimestamp(ts_epoch, tz=timezone.utc)
        status = store.FALLBACK_SUBSTITUTED
    else:
        source_ts = datetime.fromtimestamp(ts_epoch, tz=timezone.utc)
        status = store.SOURCE_SUPPLIED

    return store.observation_record(
        source_type=stype,
        source_event_id=getattr(ev, "dedupe_key", None),
        source_reference=getattr(ev, "tx_hash", None),
        raw_evidence_reference=getattr(ev, "tx_hash", None),
        rn1_source_ts=source_ts,
        source_ts_status=status,
        bettor_received_ts=received_at or _now(),
        event_id=getattr(ev, "event_slug", None),
        market_id=getattr(ev, "condition_id", None),
        symbol=getattr(ev, "market_slug", None),
        outcome_leg=getattr(ev, "outcome", None),
        side=getattr(ev, "side", None),
        rn1_price=getattr(ev, "price", None),
        rn1_quantity=getattr(ev, "size", None),
        tx_hash=getattr(ev, "tx_hash", None),
        asset=getattr(ev, "asset", None),
    )


async def observe(ev, *, received_at=None, pool=None,
                  source_type=None) -> tuple[str, bool]:
    """Append the sighting. Returns (id, was_this_the_first_sight)."""
    record = observation_from_trade_event(
        ev, received_at=received_at, source_type=source_type)
    return await store.record_observation(record, pool=pool)


# ── STEP 2: the decision ─────────────────────────────────────────────


def _decision_id(observation_id: str, policy_version: str) -> str:
    raw = "|".join([observation_id, policy_version, lanes.RN1_SHADOW])
    return "sdec_" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def _depth_shares(depth, side: str):
    """Total shares visible on the side we would have to take.

    A BUY consumes asks; a SELL consumes bids. Reading the wrong side is
    how a reconstruction quietly grants itself liquidity that was never
    offered to it.
    """
    if not depth:
        return None
    levels = depth.get("asks" if side == "BUY" else "bids")
    if not levels:
        return None
    total = 0.0
    for level in levels:
        try:
            if isinstance(level, dict):
                total += float(level.get("size", level.get("quantity", 0)))
            else:
                total += float(level[1])
        except (TypeError, ValueError, IndexError, KeyError):
            return None
    return total or None


def decide(observation: dict, market_state: dict | None, *,
           decision_ts=None, policy_version=None) -> dict:
    """The RN1_SHADOW mechanism decision. Prospective, and often
    NO_TRADE.

    THE LANE HOLDS NO BELIEF. This is not a view on whether RN1 is
    right; it is a measurement of what BETTOR's own latency, sizing and
    execution machinery would have produced observing him. P_BETTOR and
    INFORMATION_EV stay NOT_ESTABLISHED, here and in the row.

    A BLOCKED DECISION IS STILL A DECISION. Every blocker below writes a
    NO_TRADE row naming itself, because an absent row and a refused
    trade look identical in a ledger and mean opposite things.
    """
    policy_version = policy_version or pol.RN1_SHADOW_POLICY_VERSION
    decision_ts = decision_ts or _now()
    side = observation["side"]
    symbol = observation.get("symbol")

    common = dict(
        shadowDecisionId=_decision_id(observation["rn1ObservationId"],
                                      policy_version),
        symbol=symbol or "UNRESOLVED",
        outcomeLeg=observation.get("outcomeLeg") or "UNRESOLVED",
        eventId=observation.get("eventId"),
        marketId=observation.get("marketId"),
        modelVersion=MODEL_VERSION,
        policyVersion=policy_version,
        decisionTs=decision_ts,
        venueSourceTs=observation.get("rn1SourceTs"),
        bettorReceivedTs=observation["bettorReceivedTs"],
        pFillStatus=lanes.NOT_ESTABLISHED,
    )

    def blocked(code, why, **extra):
        return store.rn1_decision_record(
            rn1_observation_id=observation["rn1ObservationId"],
            market_state_id=(market_state or {}).get("marketStateId"),
            rn1_price=observation["rn1Price"],
            proposedAction=sh.NO_TRADE,
            blockers=[{"code": code, "why": why}],
            reasonCodes=[code],
            gateResults={"marketState": (market_state or {}).get("readable")},
            **common, **extra)

    if not symbol:
        return blocked(B_NO_SYMBOL,
                       "the fill's market is not resolved to a symbol yet, "
                       "so there is no book to decide against")
    if market_state is None:
        return blocked(B_NO_MARKET_STATE,
                       "no book was captured at observation time, and a "
                       "decision does not execute at its own book")
    if not market_state.get("readable"):
        return blocked(B_BOOK_UNREADABLE,
                       market_state.get("whyUnreadable")
                       or "the book could not be read")

    observed = (market_state.get("ask") if side == "BUY"
                else market_state.get("bid"))
    depth = _depth_shares(market_state.get("availableDepth"), side)
    sized = pol.shadow_size(observation["rn1Quantity"],
                            observed_depth_shares=depth, price=observed)

    priced = dict(
        marketBid=market_state.get("bid"),
        marketAsk=market_state.get("ask"),
        mid=market_state.get("mid"),
        spread=market_state.get("spread"),
        availableDepth=market_state.get("availableDepth"),
        l2Reference=market_state.get("l2Reference"),
        pMarket=market_state.get("mid"),
    )
    # The two observable prices go through the SNAKE-CASE parameters of
    # rn1_decision_record, never as loose camelCase fields: that
    # function assigns those keys itself, so a field of the same name
    # riding in **fields would be silently replaced by the None it was
    # not given -- a price that vanished between the branch that knew it
    # and the row that was written.
    seen = dict(price_when_bettor_observed=observed,
                price_when_bettor_decided=observed)

    if sized["status"] == pol.NOT_IDENTIFIED:
        return blocked(B_NO_DEPTH, sized["why"], **seen, **priced)
    if not sized["shares"]:
        return blocked(B_SIZE_FLOORS_TO_ZERO, sized["why"], **seen, **priced)

    return store.rn1_decision_record(
        rn1_observation_id=observation["rn1ObservationId"],
        market_state_id=market_state["marketStateId"],
        rn1_price=observation["rn1Price"],
        price_when_bettor_observed=observed,
        price_when_bettor_decided=observed,
        proposedAction=sh.BUY if side == "BUY" else sh.SELL,
        proposedSide=side,
        # THE PROPOSED PRICE IS OUR BOOK'S, NOT RN1'S. His fill price is
        # in rn1_price, one column over, and never borrowed.
        proposedPrice=observed,
        proposedQuantity=sized["shares"],
        reasonCodes=[R_MIRROR_RN1_SIDE, R_SIZED_BY_FROZEN_POLICY]
        + sized["reasons"],
        gateResults={"marketState": True, "sizing": sized["status"]},
        alternatives=[
            {"action": sh.NO_TRADE,
             "why": "the mechanism benchmark mirrors the observed side; "
                    "NO_TRADE is recorded when a gate refuses, not as a "
                    "judgement on RN1"}],
        marketBid=market_state.get("bid"),
        marketAsk=market_state.get("ask"),
        mid=market_state.get("mid"),
        spread=market_state.get("spread"),
        availableDepth=market_state.get("availableDepth"),
        l2Reference=market_state.get("l2Reference"),
        pMarket=market_state.get("mid"),
        **common)


async def write_decision(observation: dict, market_state: dict | None, *,
                         pool=None, decision_ts=None) -> tuple[str, bool]:
    """Persist the market state (when there is one) and the decision."""
    if market_state is not None:
        await store.record_market_state(market_state, pool=pool)
    record = decide(observation, market_state, decision_ts=decision_ts)
    return await store.record_decision(record, pool=pool)


# ── reading back what has no decision yet ────────────────────────────

PENDING_SQL = """
    SELECT o.*
      FROM rn1_observations o
      LEFT JOIN shadow_decisions d
             ON d.rn1_observation_id = o.rn1_observation_id
     WHERE o.record_kind = 'OBSERVATION'
       AND d.shadow_decision_id IS NULL
       AND o.bettor_received_ts > now() - ($1 || ' seconds')::interval
     ORDER BY o.bettor_received_ts ASC
     LIMIT $2
"""


async def pending_observations(pool, *, within_s=3600, limit=50) -> list:
    """Sightings with no decision behind them yet.

    BOUNDED BY AGE ON PURPOSE. A decision written four hours after the
    observation is not prospective in any useful sense, and quietly
    back-filling one would put a stale book in a row that claims to be
    a live one. An observation that ages out keeps its OBSERVATION row
    and simply never gets a decision -- visible as a gap, which is what
    it is.
    """
    rows = await pool.fetch(PENDING_SQL, str(int(within_s)), int(limit))
    return [_row_to_observation(r) for r in rows]


def _row_to_observation(row) -> dict:
    return {
        "rn1ObservationId": row["rn1_observation_id"],
        "recordKind": row["record_kind"],
        "idempotencyKey": row["idempotency_key"],
        "idempotencyBasis": row["idempotency_basis"],
        "sourceType": row["source_type"],
        "sourceReference": row["source_reference"],
        "ingestVersion": row["ingest_version"],
        "rn1SourceTs": row["rn1_source_ts"],
        "bettorReceivedTs": row["bettor_received_ts"],
        "sourceTsStatus": row["source_ts_status"],
        "eventId": row["event_id"],
        "marketId": row["market_id"],
        "symbol": row["symbol"],
        "outcomeLeg": row["outcome_leg"],
        "side": row["side"],
        "rn1Price": float(row["rn1_price"]),
        "rn1Quantity": float(row["rn1_quantity"]),
    }
