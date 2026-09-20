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
#
# IT CHANGED ONCE, ON PURPOSE, AND THE HASH SAYS SO. The first version
# read the decision-grade collector's opportunities; that feed samples
# a market about once an hour, which cannot produce the successive
# captured samples X1's frozen rule requires. The rule was not tuned to
# fit the feed -- the feed was replaced, and because the rule string is
# hashed onto every population, the populations drawn under each
# version are permanently distinguishable instead of silently merged.
ELIGIBILITY_RULE = (
    "a bettor_experimental_observation from this lane's own focus-set "
    "sampler carrying feature source %s and bboBinding %s, readable, "
    "observed inside the collection window, with at least %d captured "
    "mid samples on its symbol"
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
                   "bettor_l2_evidence",
                   "bettor_experimental_observations")
# EVERY COLUMN A LATER MIGRATION ADDED THAT AN INSERT HERE NAMES. An
# ALTER that has not run leaves a table that exists and a statement
# that cannot, and the failure would otherwise surface as a tick error
# per market rather than as one heartbeat naming the missing column.
REQUIRED_COLUMNS = (("bettor_experimental_decisions", "l2_evidence_id"),
                    ("bettor_experimental_decisions", "latency_regime"),
                    ("bettor_experimental_decisions", "walked_book_sha"),
                    ("bettor_experimental_decisions", "why"),
                    ("bettor_experimental_markouts", "target_at"),
                    ("bettor_experimental_markouts", "observed_lag_ms"),
                    ("bettor_experimental_markouts", "exitable_qty"),
                    ("bettor_experimental_seals",
                     "experimental_observation_id"),
                    ("bettor_experimental_decisions",
                     "experimental_observation_id"))


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


# ── the lane's own sampler, and why it has one ───────────────────────
#
# The decision-grade collector selects with `ORDER BY updated_at DESC
# LIMIT 10` over a churning board, so it sees each market roughly once
# an hour -- verified in production at 00:01Z: seventeen YES-bound rows
# across seventeen symbols. X1's frozen rule is the signed drift over
# the last five CAPTURED SAMPLES against a 60-second horizon, so on
# that feed it can never fire. The rule is frozen and is not being
# tuned to fit the feed; the lane samples its own focus set instead.

EVIDENCE_SOURCE_EXPERIMENTAL = "PMUS_BBO_EXPERIMENTAL"
SELECTION_FOCUS = "EXPERIMENTAL_FOCUS_SET_MOST_RECENTLY_ELIGIBLE"


def observation_id(symbol, leg, at, cadence_s) -> str:
    """One row per market per tick bucket, so a restart mid-tick
    cannot write the same instant twice."""
    epoch = int(at.timestamp())
    if cadence_s:
        epoch = epoch // int(cadence_s) * int(cadence_s)
    raw = "|".join([str(symbol), str(leg or ""), str(epoch)])
    return "xobs_" + hashlib.sha256(raw.encode()).hexdigest()[:36]


async def record_observation(pool, obs: dict) -> bool:
    micro = obs.get("microstructure") or {}
    row = await pool.fetchrow(
        """
        INSERT INTO bettor_experimental_observations (
            experimental_observation_id, observed_at, symbol, outcome_leg,
            event_id, selection_reason, evidence_source, cadence_s,
            readable, why_unreadable, bid, ask, mid, spread,
            spread_relative, venue_state, bbo_binding,
            feature_source_version, microstructure)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                $17,$18,$19::jsonb)
        ON CONFLICT (experimental_observation_id) DO NOTHING
        RETURNING experimental_observation_id
        """,
        obs["experimentalObservationId"], obs["observedAt"], obs["symbol"],
        obs.get("outcomeLeg"), obs.get("eventId"),
        obs.get("selectionReason", SELECTION_FOCUS),
        obs.get("evidenceSource", EVIDENCE_SOURCE_EXPERIMENTAL),
        obs.get("cadenceS"), bool(obs.get("readable")),
        obs.get("whyUnreadable"), micro.get("bid"), micro.get("ask"),
        micro.get("mid"), micro.get("spread"), micro.get("spreadRelative"),
        obs.get("venueState"), micro.get("bboBinding", l2.BIND_MARKET_LEVEL),
        micro.get("featureSourceVersion", l2.FEATURE_SOURCE_VERSION),
        _j(micro))
    return row is not None


ELIGIBLE_SQL = """
    SELECT experimental_observation_id, symbol, outcome_leg, event_id,
           observed_at, microstructure, evidence_source
      FROM bettor_experimental_observations
     WHERE observed_at > now() - ($1 || ' seconds')::interval
       AND feature_source_version = $2
       AND bbo_binding = $3
       AND readable IS TRUE
     ORDER BY symbol, observed_at
"""


async def eligible_observations(pool, *, window_s=1800) -> dict:
    """{symbol: [observation, ...]} oldest first, corrected + YES-bound.

    THE FILTER IS IN THE QUERY ON PURPOSE. §3 forbids an experiment
    training or evaluating on rows whose leg binding was wrong. Those
    rows are still kept as evidence; they simply never reach an
    experiment, and a filter the database applies cannot be forgotten
    by a caller.

    ONE FEED, NOT TWO. The decision-grade collector's YES-bound rows
    are deliberately NOT unioned in here: mixing an hourly sample and a
    per-minute one into "the last five captured samples" would make the
    drift measure describe the sampling schedule rather than the market.
    """
    rows = await pool.fetch(ELIGIBLE_SQL, str(int(window_s)),
                            l2.FEATURE_SOURCE_VERSION, l2.BIND_YES)
    out: dict = {}
    for r in rows:
        micro = _load(r["microstructure"]) or {}
        out.setdefault(r["symbol"], []).append({
            "experimentalObservationId": r["experimental_observation_id"],
            "bettorOpportunityId": None,
            "symbol": r["symbol"],
            "outcomeLeg": r["outcome_leg"],
            "eventId": r["event_id"],
            "observedAt": r["observed_at"],
            "evidenceSource": r["evidence_source"],
            "microstructure": micro,
        })
    return out


FOCUS_SQL = """
    SELECT DISTINCT ON (symbol) symbol, outcome_leg, event_id
      FROM bettor_experimental_observations
     WHERE observed_at > now() - ($1 || ' seconds')::interval
       AND readable IS TRUE
     ORDER BY symbol, observed_at DESC
"""

FOCUS_TOPUP_SQL = """
    SELECT DISTINCT ON (symbol) symbol, outcome_leg, event_id
      FROM bettor_opportunities
     WHERE observed_at > now() - ($1 || ' seconds')::interval
       AND microstructure->>'bboBinding' = $2
       AND microstructure->>'status' = 'MEASURED'
     ORDER BY symbol, observed_at DESC
     LIMIT $3
"""


async def focus_set(pool, *, size, hold_s=1800, discover_s=7200) -> list:
    """The markets this lane samples every tick.

    STICKY FIRST, THEN TOPPED UP. A focus set re-chosen from scratch
    each tick would never accumulate a series on any market, which is
    the whole reason it exists -- so markets this lane has already
    observed keep their place, and the remainder is filled from the
    markets the decision-grade collector most recently found YES-bound
    and readable.

    THE SELECTION CANNOT KNOW THE SIGNAL. Recency of observation and
    readability are the only criteria; nothing here reads a price, a
    direction or an experiment's output. A population chosen by what
    the model would say is not a population.
    """
    held = [dict(r) for r in await pool.fetch(FOCUS_SQL, str(int(hold_s)))]
    out = [{"symbol": r["symbol"],
            "outcomeLeg": r["outcome_leg"] or "yes",
            "eventId": r["event_id"], "held": True} for r in held[:size]]
    if len(out) >= size:
        return out
    seen = {r["symbol"] for r in out}
    for r in await pool.fetch(FOCUS_TOPUP_SQL, str(int(discover_s)),
                              l2.BIND_YES, int(size) * 4):
        if r["symbol"] in seen:
            continue
        out.append({"symbol": r["symbol"],
                    "outcomeLeg": r["outcome_leg"] or "yes",
                    "eventId": r["event_id"], "held": False})
        seen.add(r["symbol"])
        if len(out) >= size:
            break
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


# ── §3/§5: the DIRECT worker's own institutional evidence ────────────

REGIME_DIRECT = "DIRECT_INSTITUTIONAL_WORKER"
REGIME_BRIDGE = "GITHUB_BRIDGE"


def direct_evidence_id(symbol, received_at, sha) -> str:
    raw = "|".join(["DIRECT", str(symbol), received_at.isoformat(), str(sha)])
    return "l2dw_" + hashlib.sha256(raw.encode()).hexdigest()[:36]


async def record_direct_evidence(pool, row: dict) -> bool:
    """One in-memory book, written down as immutable evidence.

    SAME TABLE, DIFFERENT REGIME. A book the worker holds in memory and
    a book a CI runner fetched minutes late are both OBSERVED_PRODUCTION
    and belong in one ledger -- but they are different execution
    environments, so `latency_regime` separates them and the schema's
    CHECK refuses anything that is neither.

    `bridge_latency_ms` is deliberately NULL here. There is no bridge
    round trip to measure, and writing a zero would claim one was
    measured at zero.
    """
    received = row["BETTOR_RECEIVED_TIMESTAMP"]
    eid = direct_evidence_id(row["INSTRUMENT_ID"], received, row["BOOK_SHA"])
    out = await pool.fetchrow(
        """
        INSERT INTO bettor_l2_evidence (
            l2_evidence_id, request_id, instrument_id, source_timestamp,
            received_timestamp, l2_book_sha, bids, offers, price_scale,
            quantity_scale, evidence_class, venue_request_ms,
            bridge_latency_ms, latency_regime, venue_state,
            instrument_record, evidence_environment, market_data_lag_ms,
            freshness_status)
        VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9,$10,
                'OBSERVED_PRODUCTION',$11,NULL,$12,$13,$14::jsonb,$15,$16,$17)
        ON CONFLICT (l2_evidence_id) DO NOTHING
        RETURNING l2_evidence_id
        """,
        eid, row.get("requestId"), row["INSTRUMENT_ID"],
        row.get("SOURCE_TIMESTAMP"), received, row["BOOK_SHA"],
        _j(row.get("BIDS") or []), _j(row.get("OFFERS") or []),
        row.get("priceScale"), row.get("qtyScale"),
        row.get("venueRequestMs"), REGIME_DIRECT, row.get("venueState"),
        _j((row.get("instrumentRecord") or None)),
        row.get("evidenceEnvironment", REGIME_DIRECT),
        row.get("MARKET_DATA_LAG_MS"), row.get("FRESHNESS_STATUS"))
    return out is not None


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
            symbol, outcome_leg, action, sealed_at, seal,
            experimental_observation_id)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12)
        ON CONFLICT (experimental_decision_id) DO NOTHING
        RETURNING experimental_decision_id
        """,
        sealed["sealSha"], decision_id, sealed["experimentId"],
        sealed["experimentSha"], sealed.get("eligiblePopulationId"),
        sealed.get("bettorOpportunityId"), sealed["marketId"],
        sealed.get("outcomeLeg"), sealed["action"],
        sealed["decisionTimestamp"], _j(sealed),
        sealed.get("experimentalObservationId"))
    return row is not None


async def sealed_already(pool, observation_ids) -> set:
    """{(experimentId, observationId)} already sealed.

    ONE SEAL PER EXPERIMENT PER OBSERVATION, enforced by asking rather
    than assumed. Without it a tick that ran twice on one observation
    -- a restart, an overlapping cycle -- would decide the same book
    twice and the leaderboard would count the tick rate as trades.
    """
    if not observation_ids:
        return set()
    rows = await pool.fetch(
        """
        SELECT experiment_id, experimental_observation_id
          FROM bettor_experimental_seals
         WHERE experimental_observation_id = ANY($1::text[])
        """, list(observation_ids))
    return {(r["experiment_id"], r["experimental_observation_id"])
            for r in rows}


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
        observed_arrival_latency_ms, why, experimental_observation_id,
        venue_source_timestamp, bettor_received_timestamp,
        features_sealed_timestamp, model_start_timestamp,
        model_end_timestamp, modeled_send_timestamp,
        modeled_arrival_timestamp, persisted_timestamp,
        market_data_lag_ms, feature_compute_ms, model_compute_ms,
        source_to_decision_ms, modeled_execution_latency_ms,
        source_to_modeled_arrival_ms, book_freshness_status, book_age_ms,
        execution_latency_basis, evidence_environment)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,$16,
            $17::jsonb,$18,$19,$20,$21,$22,$23,$24::jsonb,$25,$26,$27,$28,
            $29,$30,$31,$32,$33,$34,$35,$36,$37,$38,$39,$40,$41,$42,$43,
            $44,$45,$46,$47,$48,$49,$50,$51,$52,$53,$54,$55,$56,$57,$58,
            $59,$60,$61,$62)
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


async def record_decision(pool, sealed: dict, execution: dict,
                          latency: dict | None = None) -> bool:
    """Append the sealed decision together with the arrival that scored it.

    NOTHING IS UPDATED. If the row is already there this is a no-op and
    returns False -- a re-run of a tick cannot rewrite a trade, which
    is the whole reason the id is derived from the seal's own hash.

    `latency` IS §8, AND IT IS OPTIONAL BY DESIGN. The bridge path has
    no in-memory book and therefore no venue source instant or market
    data lag to state; it writes NULLs rather than zeroes, because a
    zero in a lag column is a measurement and NULL is the absence of
    one. The direct path supplies every instant it actually observed.
    """
    ex = execution or {}
    lat = latency or {}
    # THE PERSIST INSTANT IS STAMPED HERE, where the persist happens.
    # Taking it from the caller would let a value computed before an
    # await describe a write that landed later.
    persisted = _ts(lat.get("persistedTimestamp")) or datetime.now(
        tz=timezone.utc)
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
        ex.get("observedArrivalLatencyMs"),
        # THE REASON, AS ITS OWN COLUMN. "Management should eventually
        # see which blockers prevent the most trades" is a GROUP BY,
        # and a reason inside a JSONB blob is not one.
        ex.get("why") or sealed.get("why"),
        sealed.get("experimentalObservationId"),
        # ── §8: the instants, each where it happened ──────────────────
        # VENUE_SOURCE_TIMESTAMP stays TEXT exactly as the venue sent
        # it. Parsing it into a timestamptz here would silently invent
        # a timezone for a string whose format the venue owns.
        lat.get("venueSourceTimestamp"),
        _ts(lat.get("bettorReceivedTimestamp")),
        _ts(lat.get("featuresSealedTimestamp")),
        _ts(lat.get("modelStartTimestamp")),
        _ts(lat.get("modelEndTimestamp")),
        _ts(lat.get("modeledSendTimestamp")),
        _ts(lat.get("modeledArrivalTimestamp")),
        persisted,
        # ── the intervals, each derived from two named instants ───────
        lat.get("marketDataLagMs"), lat.get("featureComputeMs"),
        lat.get("modelComputeMs"), lat.get("sourceToDecisionMs"),
        lat.get("modeledExecutionLatencyMs"),
        lat.get("sourceToModeledArrivalMs"),
        lat.get("bookFreshnessStatus"), lat.get("bookAgeMs"),
        # §9: the basis is a word on the row. It is never written as
        # an observed execution latency, because none was observed.
        lat.get("executionLatencyBasis"), lat.get("evidenceEnvironment"))
    return row is not None


# ── §12: the later facts, appended ───────────────────────────────────

MARKOUT_SUBJECTS_SQL = """
    SELECT d.experimental_decision_id, d.market_id, d.decision_timestamp,
           d.position_id, p.entry_qty, p.entry_vwap
      FROM bettor_experimental_decisions d
      JOIN bettor_experimental_positions p
        ON p.position_id = d.position_id
     WHERE d.decision_timestamp > now() - ($1 || ' seconds')::interval
       AND EXISTS (SELECT 1 FROM bettor_l2_evidence e
                    WHERE e.instrument_id = d.market_id)
     ORDER BY d.decision_timestamp
     LIMIT $2
"""


async def markout_subjects(pool, *, window_s=86400, limit=100) -> list:
    """The filled positions whose markouts may still be takeable.

    A decision with no position has nothing to mark: §12 measures where
    a POSITION went, and a NO_TRADE or an unfilled walk never opened
    one. Those rows are already complete evidence as they stand.
    """
    rows = await pool.fetch(MARKOUT_SUBJECTS_SQL, str(int(window_s)),
                            int(limit))
    return [{"experimentalDecisionId": r["experimental_decision_id"],
             "symbol": r["market_id"],
             "decisionTimestamp": r["decision_timestamp"],
             "positionId": r["position_id"],
             "qty": r["entry_qty"], "vwap": float(r["entry_vwap"])}
            for r in rows]


async def markouts_taken(pool, decision_ids) -> set:
    if not decision_ids:
        return set()
    rows = await pool.fetch(
        "SELECT experimental_decision_id, horizon "
        "FROM bettor_experimental_markouts "
        "WHERE experimental_decision_id = ANY($1::text[])",
        list(decision_ids))
    return {(r["experimental_decision_id"], r["horizon"]) for r in rows}


async def record_markout(pool, decision_id, position_id, m: dict) -> bool:
    """Append one markout. Never an update -- §12's whole point."""
    row = await pool.fetchrow(
        """
        INSERT INTO bettor_experimental_markouts (
            markout_id, experimental_decision_id, position_id, horizon,
            observed_at, mid_markout_usd, executable_markout_usd,
            mark_price, l2_book_sha, l2_source_timestamp, status, why,
            target_at, tolerance_ms, observed_lag_ms, latency_regime,
            l2_evidence_id, position_qty, entry_vwap, exitable_qty,
            exit_vwap)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                $17,$18,$19,$20,$21)
        ON CONFLICT (markout_id) DO NOTHING
        RETURNING markout_id
        """,
        markout_id(decision_id, m["horizon"]), decision_id, position_id,
        m["horizon"], m.get("observedAt") or m["targetAt"],
        m.get("midMarkoutUsd"), m.get("executableMarkoutUsd"),
        m.get("markPrice"), m.get("l2BookSha"), m.get("l2SourceTimestamp"),
        m["status"], m.get("why"), m.get("targetAt"), m.get("toleranceMs"),
        m.get("observedLagMs"), m.get("latencyRegime"),
        m.get("l2EvidenceId"), m.get("positionQty"), m.get("entryVwap"),
        m.get("exitableQty"), m.get("exitVwap"))
    return row is not None


def markout_id(decision_id, horizon) -> str:
    return "xmk_" + hashlib.sha256(
        ("%s|%s" % (decision_id, horizon)).encode()).hexdigest()[:36]


async def evidence_nearest(pool, symbol, *, target) -> dict | None:
    """The institutional book observed CLOSEST to `target`, either side.

    Nearest, not next: a markout is about an instant, and the honest
    nearest observation to T+60s may be a little before it. Whether
    that observation is close ENOUGH is the markout's own decision, not
    this query's -- it returns the candidate and the lag travels with
    it.
    """
    row = await pool.fetchrow(
        """
        SELECT l2_evidence_id, request_id, instrument_id, source_timestamp,
               received_timestamp, l2_book_sha, bids, offers, price_scale,
               quantity_scale, latency_regime, venue_state
          FROM bettor_l2_evidence
         WHERE instrument_id = $1
           AND price_scale IS NOT NULL
         ORDER BY abs(extract(epoch FROM (received_timestamp - $2)))
         LIMIT 1
        """, symbol, target)
    if row is None:
        return None
    book = l2.book_from(
        {"symbol": row["instrument_id"], "state": row["venue_state"],
         l2.F_TRANSACT_TIME: row["source_timestamp"],
         l2.SIDE_BIDS: _load(row["bids"]) or [],
         l2.SIDE_OFFERS: _load(row["offers"]) or []},
        price_scale=row["price_scale"], qty_scale=row["quantity_scale"],
        request_id=row["request_id"],
        received_at=row["received_timestamp"])
    book.update({"l2EvidenceId": row["l2_evidence_id"],
                 "l2BookSha": row["l2_book_sha"],
                 "latencyRegime": row["latency_regime"]})
    return book


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


# ── §1/§2: the PRE-BOUND identity, resolved before the signal ────────
#
# Owner directive 2026-09-20 (the identity blocker). The binding used to
# be recomputed every tick from whatever instrument record the GitHub
# bridge had written into bettor_l2_evidence; the direct worker writes
# none, so every lookup returned nothing and every BUY was stamped
# NOT_IDENTIFIED. These two functions replace that lookup with a table
# the market-data loop fills ahead of time.

IDENTITY_INSERT = """
    INSERT INTO bettor_identity_bindings (
        identity_binding_sha, binding_version, market_id,
        retail_native_id, outcome_leg, event_slug,
        institutional_instrument_id, institutional_event_id,
        outcome_strike, event_outcome, identity_status,
        execution_eligible, settlement_equivalence, price_scale,
        quantity_scale, payout_value, agree, why, resolved_at,
        evidence_environment, contract_family, settlement_rule,
        settlement_prose_conflict, strike_value, evaluation_type,
        long_participant_id, short_participant_id,
        complement_instrument_id)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
            $17::jsonb,$18::jsonb,$19,$20,$21,$22,$23,$24,$25,$26,$27,
            $28)
    ON CONFLICT (identity_binding_sha) DO NOTHING
    RETURNING identity_binding_sha
"""


async def record_identity_binding(pool, row: dict, *, at=None) -> bool:
    """Append one resolved binding. A re-resolution that agrees is a no-op.

    THE SHA IS THE KEY, so a binding re-derived unchanged writes nothing
    and a binding that has genuinely changed writes a NEW row beside the
    old one. Decisions carry the sha, so a trade is always readable
    against the binding it actually executed under.
    """
    out = await pool.fetchrow(
        IDENTITY_INSERT,
        row["identity_binding_sha"], row["binding_version"],
        row["market_id"], row.get("retail_native_id"), row["outcome_leg"],
        row.get("event_slug"), row.get("institutional_instrument_id"),
        row.get("institutional_event_id"), row.get("outcome_strike"),
        row.get("event_outcome"), row["identity_status"],
        bool(row.get("execution_eligible")),
        row.get("settlement_equivalence"), row.get("price_scale"),
        row.get("quantity_scale"), row.get("payout_value"),
        _j(row.get("agree") or []), _j(row.get("why") or []),
        at or datetime.now(tz=timezone.utc),
        row.get("evidence_environment", REGIME_DIRECT),
        # ── the contract family, and the venue fields that proved it ──
        row.get("contract_family"), row.get("settlement_rule"),
        row.get("settlement_prose_conflict"), row.get("strike_value"),
        row.get("evaluation_type"), row.get("long_participant_id"),
        row.get("short_participant_id"),
        row.get("complement_instrument_id"))
    return out is not None


IDENTITY_SQL = """
    SELECT DISTINCT ON (market_id, outcome_leg) *
      FROM bettor_identity_bindings
     WHERE market_id = ANY($1::text[])
     ORDER BY market_id, outcome_leg, resolved_at DESC
"""


async def bound_identities(pool, symbols) -> dict:
    """{(market, leg): the NEWEST binding for it}.

    Newest, not first: a binding is a belief about two venues' keys and
    the current belief is the one a new decision must be taken under.
    Older rows stay readable for the decisions that used them.
    """
    if not symbols:
        return {}
    rows = await pool.fetch(IDENTITY_SQL, list(symbols))
    out = {}
    for r in rows:
        row = dict(r)
        row["agree"] = _load(row.get("agree")) or []
        row["why"] = _load(row.get("why")) or []
        out[(row["market_id"], (row["outcome_leg"] or "").lower())] = row
    return out


IDENTITY_CENSUS_SQL = """
    SELECT DISTINCT ON (market_id, outcome_leg)
           market_id, outcome_leg, identity_status, execution_eligible
      FROM bettor_identity_bindings
     ORDER BY market_id, outcome_leg, resolved_at DESC
"""


async def identity_census(pool) -> list:
    """Every market's CURRENT binding, for §6's report."""
    return [dict(r) for r in await pool.fetch(IDENTITY_CENSUS_SQL)]


# ── THE FUNNEL: why activity is or is not occurring ──────────────────
#
# Owner directive 2026-09-20: "instrument the funnel so management can
# see why activity is or is not occurring."
#
# DERIVED FROM THE LEDGER, NOT COUNTED ALONGSIDE IT. Every bucket below
# is a predicate over rows that already exist, so the funnel cannot
# drift from the evidence it describes and cannot be wrong in a way the
# rows are right. A counter incremented in the worker would be a second
# account of the same events, and the two would disagree the first time
# a tick died between the increment and the insert.
#
# THE NO_TRADE SPLIT IS EXHAUSTIVE AND COMES FROM THE CODE. X1's frozen
# gate (`shadow_experiment_signals.m1_gate`) emits exactly two reasons,
# SPREAD_NOT_IDENTIFIED and SPREAD_ABOVE_FROZEN_MAX, and returns None
# otherwise; every other NO_TRADE therefore came from the direction
# signal. So `why LIKE 'SPREAD_%'` partitions the two with nothing left
# over, and the SQL asserts that by also counting the remainder.
#
# NOTHING HERE TUNES ANYTHING. It reads. The frozen spread threshold,
# the lookback and the thresholds are untouched by this file.

FUNNEL_SQL = """
    WITH d AS (
        SELECT * FROM bettor_experimental_decisions
         WHERE decision_timestamp > now() - ($1 || ' seconds')::interval
    ),
    obs AS (
        SELECT count(*) AS n
          FROM bettor_experimental_observations
         WHERE observed_at > now() - ($1 || ' seconds')::interval
    )
    SELECT
      (SELECT n FROM obs)                                AS opportunities,
      count(*)                                           AS decisions,
      count(*) FILTER (WHERE experiment_id = $2)         AS x1_eligible,
      count(*) FILTER (WHERE action = 'NO_TRADE'
                         AND why LIKE 'SPREAD\\_%')       AS no_trade_spread,
      count(*) FILTER (WHERE action = 'NO_TRADE'
                         AND (why IS NULL
                              OR why NOT LIKE 'SPREAD\\_%')) AS no_trade_signal,
      count(*) FILTER (WHERE action = 'BUY_YES')         AS buy_yes,
      count(*) FILTER (WHERE action = 'BUY_NO')          AS buy_no,
      count(*) FILTER (WHERE action LIKE 'BUY%'
                         AND execution_status =
                             'BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE')
                                                         AS buy_blocked_identity,
      count(*) FILTER (WHERE action LIKE 'BUY%'
                         AND book_freshness_status IN ('STALE', 'ABSENT'))
                                                         AS buy_blocked_stale_book,
      count(*) FILTER (WHERE action LIKE 'BUY%'
                         AND COALESCE(executed_notional_usd, 0) > 0)
                                                         AS buy_executed,
      count(*) FILTER (WHERE COALESCE(executed_notional_usd, 0) > 0
                         AND COALESCE(unfilled_notional_usd, 0) > 0)
                                                         AS partial_fills,
      count(*) FILTER (WHERE COALESCE(executed_notional_usd, 0) > 0
                         AND COALESCE(unfilled_notional_usd, 0) = 0)
                                                         AS full_fills,
      -- THE REMAINDER, printed so the partition can be checked rather
      -- than trusted: a BUY that is neither executed nor blocked by
      -- identity nor blocked by a stale book is a bucket nobody named.
      count(*) FILTER (WHERE action LIKE 'BUY%'
                         AND COALESCE(executed_notional_usd, 0) = 0
                         AND execution_status <>
                             'BLOCKED_IDENTITY_NOT_EXECUTION_ELIGIBLE'
                         AND (book_freshness_status IS NULL
                              OR book_freshness_status NOT IN
                                 ('STALE', 'ABSENT')))   AS buy_unaccounted,
      count(*) FILTER (WHERE action NOT IN ('NO_TRADE', 'BUY_YES', 'BUY_NO'))
                                                         AS action_unaccounted
      FROM d
"""

# The identity side of the funnel is a different table, and a market
# that is eligible but has produced no decision yet is exactly the
# thing management wants to see BEFORE a trade appears.
FUNNEL_IDENTITY_SQL = """
    WITH current_binding AS (
        SELECT DISTINCT ON (market_id, outcome_leg) *
          FROM bettor_identity_bindings
         ORDER BY market_id, outcome_leg, resolved_at DESC
    )
    SELECT count(DISTINCT market_id)                     AS markets,
           count(*) FILTER (WHERE execution_eligible
                              AND outcome_leg IN ('yes', 'long'))
                                                         AS yes_eligible,
           count(*) FILTER (WHERE execution_eligible
                              AND outcome_leg IN ('no', 'short'))
                                                         AS no_eligible,
           count(*) FILTER (WHERE identity_status = 'EXACT_SAME_CONTRACT')
                                                         AS exact,
           count(*) FILTER (WHERE identity_status LIKE 'STRUCTURALLY%')
                                                         AS complement_pending,
           count(*) FILTER (WHERE identity_status = 'AMBIGUOUS')
                                                         AS ambiguous,
           count(*) FILTER (WHERE identity_status = 'NOT_IDENTIFIED')
                                                         AS unresolved,
           count(*) FILTER (WHERE settlement_prose_conflict IS NOT NULL)
                                                         AS prose_conflicts
      FROM current_binding
"""


async def funnel(pool, *, window_s=86400, experiment_id="X1_SHORT_HORIZON_"
                                                        "DIRECTION") -> dict:
    """WHY ACTIVITY IS OR IS NOT OCCURRING, in the directive's own names.

    Read-only. Every number is a count of rows that already exist.
    """
    row = await pool.fetchrow(FUNNEL_SQL, str(int(window_s)), experiment_id)
    ident_row = await pool.fetchrow(FUNNEL_IDENTITY_SQL)
    r, i = dict(row or {}), dict(ident_row or {})

    out = {
        "OPPORTUNITIES": r.get("opportunities") or 0,
        "X1_ELIGIBLE": r.get("x1_eligible") or 0,
        "NO_TRADE_SPREAD": r.get("no_trade_spread") or 0,
        "NO_TRADE_SIGNAL": r.get("no_trade_signal") or 0,
        "BUY_YES": r.get("buy_yes") or 0,
        "BUY_NO": r.get("buy_no") or 0,
        "BUY_BLOCKED_IDENTITY": r.get("buy_blocked_identity") or 0,
        "BUY_BLOCKED_STALE_BOOK": r.get("buy_blocked_stale_book") or 0,
        "BUY_EXECUTED": r.get("buy_executed") or 0,
        "PARTIAL_FILLS": r.get("partial_fills") or 0,
        "FULL_FILLS": r.get("full_fills") or 0,
    }
    out["identity"] = {
        "MARKETS_BOUND": i.get("markets") or 0,
        "YES_EXECUTION_ELIGIBLE": i.get("yes_eligible") or 0,
        "NO_EXECUTION_ELIGIBLE": i.get("no_eligible") or 0,
        "EXACT_SAME_CONTRACT": i.get("exact") or 0,
        "COMPLEMENT_PENDING": i.get("complement_pending") or 0,
        "AMBIGUOUS": i.get("ambiguous") or 0,
        "UNRESOLVED": i.get("unresolved") or 0,
        "SETTLEMENT_PROSE_CONFLICTS": i.get("prose_conflicts") or 0,
    }
    # THE PARTITION, CHECKED RATHER THAN ASSUMED. If either remainder is
    # non-zero a decision landed in a bucket nobody named, and the
    # funnel says so instead of quietly losing it.
    out["unaccounted"] = {
        "BUY_ROWS_IN_NO_NAMED_BUCKET": r.get("buy_unaccounted") or 0,
        "ACTIONS_OUTSIDE_THE_THREE": r.get("action_unaccounted") or 0,
    }
    out["decisions"] = r.get("decisions") or 0
    out["windowSeconds"] = int(window_s)
    return out
