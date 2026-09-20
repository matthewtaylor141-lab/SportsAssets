"""Writers for the unselected state capture. APPEND ONLY.

Three statements, and every one of them is an INSERT. There is no
UPDATE and no DELETE anywhere in this module, and a test asserts that
over the source text rather than over behaviour.

That is the whole point of splitting state from outcome: the state row
was written before any outcome existed, and there must be no code path
by which a later value edits it. `ON CONFLICT DO NOTHING` on the state
insert means a second read in the same bucket is DISCARDED, never
merged -- the first observation of a bucket is the observation.
"""

from __future__ import annotations

import json

from . import bettor_state_capture as sc
from .db import get_pool

APPEND_ONLY = (
    "INSERT only. No UPDATE, no DELETE. A later outcome may never edit "
    "the state that preceded it")


def _j(v):
    return None if v is None else json.dumps(v, default=str)


def _jj(v):
    """JSONB column that may legitimately hold NOT_IDENTIFIED."""
    if v is None:
        return None
    if v == sc.NOT_IDENTIFIED:
        return json.dumps({"status": sc.NOT_IDENTIFIED})
    return json.dumps(v, default=str)


_STATE_INSERT = """
    INSERT INTO bettor_state_observations (
        observation_id, observed_at, observation_bucket,
        universe_version, rule_sha, selection_cycle,
        slice_truncated, slice_truncated_by,
        event_id, market_id, instrument_id, condition_id, outcome_leg,
        sport, league, market_type, sport_source_raw, league_source_raw,
        identity_status, time_to_event_s, live_status, book_source_ts,
        book_received_ts, book_age_s,
        yes_bid, yes_ask, yes_depth, no_bid, no_ask, no_depth,
        spread, mid, multi_level_depth, book_imbalance,
        recent_price_move, realised_volatility, stats_shares_traded,
        venue_state, book_readability_status, missing_field_reasons,
        raw_source, fill_status)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
            $17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27::jsonb,$28,$29,
            $30::jsonb,$31,$32,$33::jsonb,$34,$35,$36,$37,$38,$39,
            $40::jsonb,$41::jsonb,$42)
    ON CONFLICT DO NOTHING
    RETURNING observation_id
"""


def _text(v):
    """NOT_IDENTIFIED survives into the column. It is a value, not a
    hole: a NULL cannot say why a field is absent."""
    return None if v is None else str(v)


async def record_state(row: dict, pool=None) -> tuple[str, bool]:
    """Append one state observation. Returns (id, was_first_in_bucket)."""
    pool = pool or await get_pool()
    r = row
    got = await pool.fetchrow(
        _STATE_INSERT,
        r["OBSERVATION_ID"], r["OBSERVED_AT"], r["OBSERVATION_BUCKET"],
        r["UNIVERSE_VERSION"], r["RULE_SHA"],
        None if r["SELECTION_CYCLE"] == sc.NOT_IDENTIFIED
        else int(r["SELECTION_CYCLE"]),
        bool(r["SLICE_TRUNCATED"]), int(r["SLICE_TRUNCATED_BY"]),
        _text(r["EVENT_ID"]), _text(r["MARKET_ID"]),
        _text(r["INSTRUMENT_ID"]), _text(r["CONDITION_ID"]),
        _text(r["OUTCOME_LEG"]), _text(r["SPORT"]), _text(r["LEAGUE"]),
        _text(r["MARKET_TYPE"]), _text(r["SPORT_SOURCE_RAW"]),
        _text(r["LEAGUE_SOURCE_RAW"]), _text(r["IDENTITY_STATUS"]),
        _text(r["TIME_TO_EVENT_S"]), _text(r["LIVE_STATUS"]),
        _text(r["BOOK_SOURCE_TIMESTAMP"]), r["BOOK_RECEIVED_TIMESTAMP"],
        _text(r["BOOK_AGE_S"]),
        _text(r["YES_BID"]), _text(r["YES_ASK"]), _jj(r["YES_DEPTH"]),
        _text(r["NO_BID"]), _text(r["NO_ASK"]), _jj(r["NO_DEPTH"]),
        _text(r["SPREAD"]), _text(r["MID"]), _jj(r["MULTI_LEVEL_DEPTH"]),
        _text(r["BOOK_IMBALANCE"]), _text(r["RECENT_PRICE_MOVE"]),
        _text(r["REALISED_VOLATILITY"]), _text(r["STATS_SHARES_TRADED"]),
        _text(r["VENUE_STATE"]), _text(r["BOOK_READABILITY_STATUS"]),
        _j(r["MISSING_FIELD_REASONS"]), _j(r["RAW_SOURCE"]),
        r["FILL_STATUS"])
    return r["OBSERVATION_ID"], got is not None


_MID_INSERT = """
    INSERT INTO bettor_state_mids (
        observation_id, horizon_s, read_at, actual_lag_s,
        within_tolerance, mid, status)
    VALUES ($1,$2,$3,$4,$5,$6,$7)
    ON CONFLICT DO NOTHING
"""


async def record_mid(row: dict, pool=None) -> None:
    pool = pool or await get_pool()
    await pool.execute(
        _MID_INSERT, row["OBSERVATION_ID"], int(row["HORIZON_S"]),
        row.get("READ_AT"), _text(row.get("ACTUAL_LAG_S")),
        row.get("WITHIN_TOLERANCE"), _text(row.get("MID")),
        str(row["STATUS"]))


_SETTLEMENT_INSERT = """
    INSERT INTO bettor_state_settlements (
        observation_id, settlement_outcome, settlement_ts,
        settlement_status, settlement_semantics_status)
    VALUES ($1,$2,$3,$4,$5)
    ON CONFLICT DO NOTHING
"""


async def record_settlement(row: dict, pool=None) -> None:
    pool = pool or await get_pool()
    await pool.execute(
        _SETTLEMENT_INSERT, row["OBSERVATION_ID"],
        _text(row.get("SETTLEMENT_OUTCOME")),
        _text(row.get("SETTLEMENT_TIMESTAMP")),
        str(row["SETTLEMENT_STATUS"]),
        str(row["SETTLEMENT_SEMANTICS_STATUS"]))


# ── reads the capture itself needs ───────────────────────────────────

_HISTORY_SQL = """
    SELECT mid, observed_at
      FROM bettor_state_observations
     WHERE market_id = $1 AND outcome_leg IS NOT DISTINCT FROM $2
       AND mid IS NOT NULL AND mid <> 'NOT_IDENTIFIED'
     ORDER BY observed_at DESC
     LIMIT $3
"""


async def history(market_id, leg, *, limit=12, pool=None) -> list:
    """Prior observations OF THIS DATASET, newest first.

    Used only for RECENT_PRICE_MOVE and REALISED_VOLATILITY, both of
    which are T0-knowable state features. It reads no outcome table, so
    a feature computed from it cannot see the future.
    """
    pool = pool or await get_pool()
    rows = await pool.fetch(_HISTORY_SQL, market_id, leg, int(limit))
    return [{"mid": r["mid"], "observedAt": r["observed_at"]}
            for r in rows]


_DUE_SQL = """
    SELECT o.observation_id, o.observed_at, o.market_id, o.outcome_leg
      FROM bettor_state_observations o
     WHERE o.observed_at <= now() - ($1 || ' seconds')::interval
       AND o.observed_at >  now() - ($2 || ' seconds')::interval
       AND NOT EXISTS (SELECT 1 FROM bettor_state_mids m
                        WHERE m.observation_id = o.observation_id
                          AND m.horizon_s = $3)
     ORDER BY o.observed_at
     LIMIT $4
"""


async def mids_due(horizon_s: int, *, limit=8, pool=None) -> list:
    """Observations whose horizon has come and gone unrecorded.

    The upper bound is the horizon plus HORIZON_DUE_WINDOW_S. A late
    read is still recorded with its ACTUAL lag and with
    WITHIN_TOLERANCE false, so it is never mistaken for an on-time one
    -- the wide window buys coverage, the tight tolerance keeps the
    honesty. At a 30s window only 3.4% of observations ever got their
    60s mid, because the window closed while the tick was busy.
    """
    pool = pool or await get_pool()
    rows = await pool.fetch(
        _DUE_SQL, str(int(horizon_s)),
        str(int(horizon_s + sc.HORIZON_DUE_WINDOW_S)),
        int(horizon_s), int(limit))
    return [dict(r) for r in rows]


_READY_SQL = """
    SELECT table_name FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = ANY($1::text[])
"""

REQUIRED_TABLES = ("bettor_state_observations", "bettor_state_mids",
                   "bettor_state_settlements")


async def store_ready(pool=None) -> dict:
    """ASKED, NOT ASSUMED. Registering this loop on a deployment whose
    migrations have not run costs a heartbeat naming the blocker."""
    pool = pool or await get_pool()
    try:
        rows = await pool.fetch(_READY_SQL, list(REQUIRED_TABLES))
    except Exception as exc:                                   # noqa: BLE001
        return {"storeReady": False,
                "problems": ["CATALOG_UNREADABLE:%s" % type(exc).__name__]}
    have = {r["table_name"] for r in rows}
    missing = [t for t in REQUIRED_TABLES if t not in have]
    return {"storeReady": not missing,
            "problems": ["TABLE_ABSENT:%s" % t for t in missing],
            "appendOnly": APPEND_ONLY}
