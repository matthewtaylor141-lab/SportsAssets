"""THE EXPERIMENTAL LANE'S PERSISTENCE. Reads, seals, executions.

Owner directive 2026-09-19 22:4xZ §8 / §10 and 23:0xZ (the bridge).

WHY THIS IS A SEPARATE MODULE FROM THE ENGINE. The engine is pure: it
seals a decision and walks a book, and it can be reasoned about and
tested without a database. Every statement that touches the ledger is
here, which is also what keeps the V2 code boundary honest -- the
decision path does not read the decision ledger.

THE ONE PROPERTY EVERY QUERY BELOW PROTECTS. A decision is sealed on
one tick and executed on a later one, because the arrival book comes
through the GitHub bridge minutes afterwards. So `evidence_for` will
only return a book whose RECEIVED TIMESTAMP IS AFTER the seal's
instant. A book observed before the decision is not an arrival; it is
the thing the decision was made on, and walking it would reconstruct a
fill at a price the decision already knew.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from . import shadow as sh
from . import shadow_experiments as xp
from . import shadow_l2 as l2

LANE = xp.EXPERIMENTAL_LANE

# The eligibility rule, typed once and hashed onto every population.
ELIGIBILITY_RULE = (
    "a bettor_opportunity whose microstructure carries feature source %s "
    "and bboBinding %s, observed inside the collection window, with at "
    "least %d captured mid samples on its symbol"
    % (l2.FEATURE_SOURCE_VERSION, l2.BIND_YES, 3))
ELIGIBILITY_RULE_SHA = hashlib.sha256(
    ELIGIBILITY_RULE.encode()).hexdigest()[:16]

SEALED = "SEALED"
EXECUTED_SEAL = "EXECUTED"
EXPIRED_SEAL = "EXPIRED"

PURPOSE_ARRIVAL = "X1_ARRIVAL"
PURPOSE_MARKOUT = "MARKOUT"


class ExperimentalStoreRefusal(sh.ShadowRefusal):
    """A row this module will not write."""


def _now():
    return datetime.now(tz=timezone.utc)


def _j(value):
    return None if value is None else json.dumps(value, default=str)


def _load(value):
    """A JSONB column as Python. asyncpg hands these back as text."""
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


# ── what the collector left for us ───────────────────────────────────

REQUIRED_TABLES = ("bettor_eligible_populations", "bettor_experiments",
                   "bettor_experimental_decisions",
                   "bettor_experimental_positions",
                   "bettor_experimental_seals", "bettor_l2_requests",
                   "bettor_l2_evidence")
REQUIRED_COLUMNS = (("bettor_experimental_decisions", "l2_evidence_id"),
                    ("bettor_experimental_decisions", "latency_regime"),
                    ("bettor_experimental_decisions", "walked_book_sha"))


async def store_ready(pool) -> dict:
    """Are the experimental tables actually there, with their columns?

    ASKED, NOT ASSUMED. A deployment whose migrations have not run yet
    must cost a heartbeat naming the blocker -- not a crash loop, and
    certainly not a half-written decision. The column check is separate
    from the table check because an ALTER that did not run leaves a
    table that exists and an insert that cannot.
    """
    present = {r["table_name"] for r in await pool.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name = ANY($1::text[])", list(REQUIRED_TABLES))}
    problems = ["table %s is absent" % t
                for t in REQUIRED_TABLES if t not in present]
    if not problems:
        cols = {(r["table_name"], r["column_name"])
                for r in await pool.fetch(
                    "SELECT table_name, column_name "
                    "FROM information_schema.columns "
                    "WHERE table_name = ANY($1::text[])",
                    list({t for t, _c in REQUIRED_COLUMNS}))}
        problems = ["%s.%s is absent" % pair
                    for pair in REQUIRED_COLUMNS if pair not in cols]
    return {"storeReady": not problems, "problems": problems}


ELIGIBLE_SQL = """
    SELECT bettor_opportunity_id, symbol, outcome_leg, event_id,
           observed_at, microstructure, evidence_source
      FROM bettor_opportunities
     WHERE observed_at > now() - ($1 || ' seconds')::interval
       AND microstructure->>'featureSourceVersion' = $2
       AND microstructure->>'bboBinding' = $3
     ORDER BY symbol, observed_at
"""


async def eligible_opportunities(pool, *, window_s=3600) -> dict:
    """{symbol: [opportunity, ...]} oldest first, corrected + YES-bound.

    THE FILTER IS IN THE QUERY ON PURPOSE. §3 forbids an experiment
    training or evaluating on the rows written while `yes` and `no`
    carried the same book. Those rows are still there and are still
    evidence; they simply never reach an experiment, and a filter the
    database applies cannot be forgotten by a caller.
    """
    rows = await pool.fetch(ELIGIBLE_SQL, str(int(window_s)),
                            l2.FEATURE_SOURCE_VERSION, l2.BIND_YES)
    out: dict = {}
    for r in rows:
        micro = _load(r["microstructure"]) or {}
        out.setdefault(r["symbol"], []).append({
            "bettorOpportunityId": r["bettor_opportunity_id"],
            "symbol": r["symbol"],
            "outcomeLeg": r["outcome_leg"],
            "eventId": r["event_id"],
            "observedAt": r["observed_at"],
            "evidenceSource": r["evidence_source"],
            "microstructure": micro,
        })
    return out


def mid_series_of(opportunities) -> list:
    """The captured mids, oldest first, with the unreadable ones gone.

    A gap is NOT interpolated. X1's rule counts SAMPLES, not seconds,
    and inventing a mid to fill a hole would feed the model a price
    nobody observed.
    """
    return [o["microstructure"].get("mid") for o in (opportunities or [])
            if isinstance(o.get("microstructure"), dict)
            and o["microstructure"].get("mid") is not None]


# ── the institutional side of the binding ────────────────────────────

INSTRUMENT_SQL = """
    SELECT DISTINCT ON (instrument_id)
           instrument_id, instrument_record, price_scale, quantity_scale,
           received_timestamp
      FROM bettor_l2_evidence
     WHERE instrument_id = ANY($1::text[])
       AND instrument_record IS NOT NULL
     ORDER BY instrument_id, received_timestamp DESC
"""


async def instrument_records(pool, symbols) -> dict:
    """{symbol: the venue's own instrument record}, newest observation.

    Read from the BRIDGE'S EVIDENCE, because Render holds no
    institutional credential: the production refdata reaches this
    process only as something the authenticated lane already wrote
    down. That is the whole shape of the bridge, and it means the
    identity binding is made against a record the venue actually
    returned rather than against anything this process could invent.
    """
    if not symbols:
        return {}
    rows = await pool.fetch(INSTRUMENT_SQL, list(symbols))
    return {r["instrument_id"]: _load(r["instrument_record"]) or {}
            for r in rows}


RETAIL_SQL = """
    SELECT DISTINCT ON (market_slug, side_norm)
           identifier, market_slug, event_slug, side_norm, kind, line
      FROM us_premap
     WHERE market_slug = ANY($1::text[])
     ORDER BY market_slug, side_norm, updated_at DESC
"""


async def retail_rows(pool, symbols) -> dict:
    """{(slug, leg): the retail venue's own row}."""
    if not symbols:
        return {}
    rows = await pool.fetch(RETAIL_SQL, list(symbols))
    return {(r["market_slug"], (r["side_norm"] or "").lower()): dict(r)
            for r in rows}


# ── the arrival book, and only one observed AFTER the seal ───────────

EVIDENCE_SQL = """
    SELECT l2_evidence_id, l2_request_id, request_id, instrument_id,
           source_timestamp, received_timestamp, l2_book_sha, bids, offers,
           price_scale, quantity_scale, venue_request_ms, bridge_latency_ms,
           latency_regime, venue_state
      FROM bettor_l2_evidence
     WHERE instrument_id = $1
       AND received_timestamp > $2
     ORDER BY received_timestamp
     LIMIT 1
"""


async def evidence_for(pool, symbol, *, after) -> dict | None:
    """The FIRST institutional book observed after `after`, as a book.

    "First", not "latest". The arrival this lane reconstructs is the
    earliest observation that could possibly have followed the
    decision; taking the newest instead would quietly charge the trade
    a book minutes further from its own instant whenever the worker
    fell behind, and the P&L would move with the worker's lag.
    """
    row = await pool.fetchrow(EVIDENCE_SQL, symbol, after)
    if row is None:
        return None
    if row["price_scale"] is None or row["quantity_scale"] is None:
        # Recorded, but not priceable. Saying so is the honest answer;
        # assuming 100 is the failure shadow_l2 exists to refuse.
        raise l2.ScalesRequired(
            "refused: evidence %s for %r carries no instrument scales"
            % (row["l2_evidence_id"], symbol))
    book = l2.book_from(
        {"symbol": row["instrument_id"], "state": row["venue_state"],
         l2.F_TRANSACT_TIME: row["source_timestamp"],
         l2.SIDE_BIDS: _load(row["bids"]) or [],
         l2.SIDE_OFFERS: _load(row["offers"]) or []},
        price_scale=row["price_scale"], qty_scale=row["quantity_scale"],
        request_id=row["request_id"],
        received_at=row["received_timestamp"])
    book.update({
        "l2EvidenceId": row["l2_evidence_id"],
        "l2RequestId": row["l2_request_id"],
        "l2BookSha": row["l2_book_sha"],
        "latencyRegime": row["latency_regime"],
        "venueRequestMs": row["venue_request_ms"],
        # THE MEASURED FIGURE, never a manufactured one. Where the
        # bridge could not measure its own round trip the value is
        # NULL and stays NULL.
        "bridgeLatencyMs": row["bridge_latency_ms"],
    })
    return book


# ── the request the bridge drains ────────────────────────────────────


async def request_l2(pool, *, symbol, requested_by, purpose=PURPOSE_ARRIVAL,
                     identity_binding_sha=None, at=None) -> str:
    at = at or _now()
    rid = "l2rq_" + hashlib.sha256(
        ("|".join([str(symbol), str(purpose), at.isoformat()]))
        .encode()).hexdigest()[:36]
    await pool.execute(
        """
        INSERT INTO bettor_l2_requests (
            l2_request_id, requested_at, requested_by, symbol,
            identity_binding_sha, purpose, status)
        VALUES ($1,$2,$3,$4,$5,$6,'PENDING')
        ON CONFLICT (l2_request_id) DO NOTHING
        """, rid, at, requested_by, symbol, identity_binding_sha, purpose)
    return rid


# ── the frozen registry, written down ────────────────────────────────


async def freeze_experiments(pool, experiments) -> dict:
    """Append every declared experiment. Idempotent by (id, sha).

    A CHANGED RULE DOES NOT OVERWRITE ITS PREDECESSOR -- it cannot,
    because the sha is half the key and the table is append-only. It
    lands beside it, and every trade stays readable under the exact
    declaration it was taken under.
    """
    written = 0
    for e in experiments:
        row = await pool.fetchrow(
            """
            INSERT INTO bettor_experiments (
                experiment_id, experiment_sha, policy_version,
                model_version, role, control_for, readiness, declaration)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
            ON CONFLICT (experiment_id, experiment_sha) DO NOTHING
            RETURNING experiment_id
            """,
            e["experimentId"], e["experimentSha"], e["policyVersion"],
            e["modelVersion"], e["role"], e.get("controlFor"),
            e["readiness"], _j(e))
        if row is not None:
            written += 1
    return {"declared": len(experiments), "written": written}


# ── the eligible population (§7) ─────────────────────────────────────


async def record_population(pool, *, population_id, sealed_at,
                            opportunity_count) -> None:
    await pool.execute(
        """
        INSERT INTO bettor_eligible_populations (
            eligible_population_id, sealed_at, feature_source_version,
            opportunity_count, eligibility_rule, eligibility_rule_sha)
        VALUES ($1,$2,$3,$4,$5,$6)
        ON CONFLICT (eligible_population_id) DO NOTHING
        """, population_id, sealed_at, l2.FEATURE_SOURCE_VERSION,
        int(opportunity_count), ELIGIBILITY_RULE, ELIGIBILITY_RULE_SHA)


# ── the seal, before its evidence exists ─────────────────────────────


async def record_seal(pool, sealed: dict, *, decision_id) -> bool:
    """Write the T0 seal down. True if this call is the one that wrote it."""
    row = await pool.fetchrow(
        """
        INSERT INTO bettor_experimental_seals (
            seal_sha, experimental_decision_id, experiment_id,
            experiment_sha, eligible_population_id, bettor_opportunity_id,
            symbol, outcome_leg, action, sealed_at, seal)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb)
        ON CONFLICT (experimental_decision_id) DO NOTHING
        RETURNING experimental_decision_id
        """,
        sealed["sealSha"], decision_id, sealed["experimentId"],
        sealed["experimentSha"], sealed.get("eligiblePopulationId"),
        sealed.get("bettorOpportunityId"), sealed["marketId"],
        sealed.get("outcomeLeg"), sealed["action"],
        sealed["decisionTimestamp"], _j(sealed))
    return row is not None


async def sealed_already(pool, opportunity_ids) -> set:
    """{(experimentId, opportunityId)} already sealed.

    ONE SEAL PER EXPERIMENT PER OPPORTUNITY, enforced by asking. The
    collector writes one opportunity per market per 300s bucket while
    this loop runs every 60s, so without this the same market would be
    decided five times on one book and the leaderboard would count the
    tick rate as trades.
    """
    if not opportunity_ids:
        return set()
    rows = await pool.fetch(
        """
        SELECT experiment_id, bettor_opportunity_id
          FROM bettor_experimental_seals
         WHERE bettor_opportunity_id = ANY($1::text[])
        """, list(opportunity_ids))
    return {(r["experiment_id"], r["bettor_opportunity_id"]) for r in rows}


OPEN_SEALS_SQL = """
    SELECT experimental_decision_id, seal, sealed_at, symbol, action
      FROM bettor_experimental_seals
     WHERE status = 'SEALED'
     ORDER BY sealed_at
     LIMIT $1
"""


async def open_seals(pool, *, limit=40) -> list:
    rows = await pool.fetch(OPEN_SEALS_SQL, int(limit))
    out = []
    for r in rows:
        body = _load(r["seal"]) or {}
        # The timestamps come back from JSONB as text. The hash was
        # taken over the canonical form with default=str, so restoring
        # them as text is what re-derives -- parsing them back into
        # datetimes would change the digest and refuse every seal.
        out.append({"experimentalDecisionId": r["experimental_decision_id"],
                    "sealedAt": r["sealed_at"], "symbol": r["symbol"],
                    "action": r["action"], "seal": body})
    return out


async def close_seal(pool, decision_id, *, status, l2_request_id=None,
                     at=None) -> None:
    if status not in (EXECUTED_SEAL, EXPIRED_SEAL):
        raise ExperimentalStoreRefusal("refused: %r is not a seal status"
                                       % status)
    await pool.execute(
        """
        UPDATE bettor_experimental_seals
           SET status = $2, claimed_at = $3,
               l2_request_id = COALESCE($4, l2_request_id)
         WHERE experimental_decision_id = $1 AND status = 'SEALED'
        """, decision_id, status, at or _now(), l2_request_id)


async def attach_request(pool, decision_id, l2_request_id) -> None:
    await pool.execute(
        """
        UPDATE bettor_experimental_seals SET l2_request_id = $2
         WHERE experimental_decision_id = $1 AND l2_request_id IS NULL
        """, decision_id, l2_request_id)


# ── the decision and its arrival, written together ───────────────────

DECISION_INSERT = """
    INSERT INTO bettor_experimental_decisions (
        experimental_decision_id, experiment_id, experiment_version,
        experiment_sha, control_id, eligible_population_id,
        bettor_opportunity_id, market_id, outcome_leg,
        institutional_instrument_id, identity_binding_status,
        identity_binding_sha, feature_source_version, feature_asof,
        features, features_sha, model_output, signal_strength, action,
        decision_timestamp, intended_notional_usd, arrival_timestamp,
        execution_status, l2_reference, l2_source_timestamp,
        l2_received_timestamp, l2_book_sha, walked_book_sha,
        price_scale, quantity_scale,
        execution_contract, execution_contract_sha, executed_notional_usd,
        unfilled_notional_usd, filled_qty, vwap, slippage, spread_cost,
        position_id, l2_evidence_id, latency_regime,
        observed_arrival_latency_ms)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,$16,
            $17::jsonb,$18,$19,$20,$21,$22,$23,$24::jsonb,$25,$26,$27,$28,
            $29,$30,$31,$32,$33,$34,$35,$36,$37,$38,$39,$40,$41,$42)
    ON CONFLICT (experimental_decision_id) DO NOTHING
    RETURNING experimental_decision_id
"""

# The insert's columns in order, so a reader -- or a test -- can name a
# position rather than count to it. An index counted by hand is the
# defect class that put a decision's vwap in its slippage column
# elsewhere in this codebase; the list is derived from the statement
# itself, so the two cannot drift.
DECISION_COLUMNS = tuple(
    c.strip() for c in DECISION_INSERT.split("(", 1)[1].split(")")[0]
    .replace("\n", " ").split(","))


def _ts(value):
    """A timestamp column's value, whatever the seal round-tripped as."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


async def record_decision(pool, sealed: dict, execution: dict) -> bool:
    """Append the sealed decision together with the arrival that scored it.

    NOTHING IS UPDATED. If the row is already there this is a no-op and
    returns False -- a re-run of a tick cannot rewrite a trade, which
    is the whole reason the id is derived from the seal's own hash.
    """
    ex = execution or {}
    row = await pool.fetchrow(
        DECISION_INSERT,
        ex["experimentalDecisionId"], sealed["experimentId"],
        sealed["experimentVersion"], sealed["experimentSha"],
        sealed.get("controlId"), sealed.get("eligiblePopulationId"),
        sealed.get("bettorOpportunityId"), sealed["marketId"],
        sealed.get("outcomeLeg"), sealed.get("institutionalInstrumentId"),
        sealed["identityBindingStatus"], sealed.get("identityBindingSha"),
        sealed["featureSourceVersion"], _ts(sealed.get("featureAsof")),
        _j(sealed.get("features")), sealed["featuresSha"],
        _j(sealed.get("modelOutput")), sealed.get("signalStrength"),
        sealed["action"], _ts(sealed["decisionTimestamp"]),
        sealed["intendedNotionalUsd"], _ts(ex.get("arrivalTimestamp")),
        ex["executionStatus"],
        _j({"source": l2.L2_SOURCE_INSTITUTIONAL,
            "why": ex.get("why")}),
        ex.get("l2SourceTimestamp"), _ts(ex.get("l2ReceivedTimestamp")),
        ex.get("l2BookSha"), ex.get("walkedBookSha"),
        ex.get("priceScale"), ex.get("quantityScale"),
        ex.get("executionContract"), ex.get("executionContractSha"),
        ex.get("executedNotionalUsd"), ex.get("unfilledNotionalUsd"),
        ex.get("filledQty"), ex.get("vwap"), ex.get("slippage"),
        ex.get("spreadCost"), ex.get("positionId"),
        ex.get("l2EvidenceId"), ex.get("latencyRegime"),
        ex.get("observedArrivalLatencyMs"))
    return row is not None


async def open_position(pool, sealed: dict, execution: dict) -> str | None:
    """The shadow position an execution opened. Never one without a fill."""
    ex = execution or {}
    if not ex.get("positionId") or not ex.get("filledQty"):
        return None
    await pool.execute(
        """
        INSERT INTO bettor_experimental_positions (
            position_id, experimental_decision_id, experiment_id,
            market_id, institutional_instrument_id, side, opened_at,
            entry_qty, entry_vwap, entry_notional_usd)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        ON CONFLICT (position_id) DO NOTHING
        """,
        ex["positionId"], ex["experimentalDecisionId"],
        sealed["experimentId"], sealed["marketId"],
        sealed.get("institutionalInstrumentId"), sealed["action"],
        _ts(ex.get("arrivalTimestamp")) or _now(),
        ex["filledQty"], ex["vwap"], ex["executedNotionalUsd"])
    return ex["positionId"]
