"""READ-ONLY loaders of the learning layer. SELECTs only (pinned by
tests/test_poslearn_authority.py); every read is bounded by a window and a
LIMIT. This is the ONLY module of the package that reads the valuation
table (it reads BOTH record purposes: the layer scores probabilities and
selects no candidate, sizes nothing and places nothing -- classified in
tests/test_calibration_only_records_cannot_trade.py).
"""
from __future__ import annotations

import datetime as _dt

from ..intel import reads as IR
from . import common as C

MAX_ROWS = 20000
POLICY_KEY = "PINNACLE_COMPLETED_GAME_PAPER"


def ts(e):
    return _dt.datetime.fromtimestamp(float(e), _dt.timezone.utc)


async def tables_ready(conn) -> bool:
    return bool(await conn.fetchval(
        "SELECT to_regclass('poslearn_registrations') IS NOT NULL "
        "   AND to_regclass('poslearn_experiment_outcomes') IS NOT NULL"))


async def approved_threshold(conn):
    """(threshold_pp, basis) of the ACTIVE approved paper policy version.
    Unreadable -> the shipped 0.5 pp with a basis that says so."""
    from . import models as M
    try:
        row = await conn.fetchrow(
            "SELECT v.version_id, v.params->>'min_gross_edge_pp' AS pp "
            "  FROM paper_policy_parameter_heads h "
            "  JOIN paper_policy_parameter_versions v "
            "    ON v.version_id = h.active_version_id "
            " WHERE h.policy_key = $1", POLICY_KEY)
    except Exception:                                          # noqa: BLE001
        row = None
    if row is None or C.num(row["pp"]) is None:
        return M.DEFAULT_THRESHOLD_PP, M.THRESHOLD_BASIS_DEFAULT
    return float(row["pp"]), "ACTIVE_VERSION:%s" % row["version_id"]


VAL_COLS = (
    "id, event_key, us_market_slug, sport_family, market, probability, "
    "executable_price, cost_per_contract, record_purpose, "
    "calibration_only_evidence, buy_intent, payout_event, age_s, "
    "settlement_rule, outcome_known, outcome, outcome_basis, "
    "extract(epoch FROM decided_at)::float8 AS decided_at, "
    "extract(epoch FROM outcome_at)::float8 AS outcome_at")

PENDING_SQL = (
    "SELECT " + VAL_COLS + " FROM external_valuations "
    " WHERE decided_at >= $1 AND decided_at <= $2 "
    "   AND outcome_known = false AND probability IS NOT NULL "
    "   AND event_key IS NOT NULL "
    " ORDER BY decided_at, id LIMIT $3")

RESOLVED_SQL = (
    "SELECT " + VAL_COLS + " FROM external_valuations "
    " WHERE decided_at >= $1 AND probability IS NOT NULL "
    "   AND event_key IS NOT NULL AND outcome_known = true "
    "   AND outcome_at <= $2 "
    " ORDER BY decided_at DESC LIMIT $3")

QUOTES_SQL = (
    "SELECT us_market_slug, payout_event, probability, "
    "       extract(epoch FROM decided_at)::float8 AS at "
    "  FROM external_valuations "
    " WHERE us_market_slug = ANY($1::text[]) "
    "   AND decided_at >= $2 AND decided_at <= $3 "
    "   AND probability IS NOT NULL "
    " ORDER BY decided_at LIMIT $4")

SOURCE_OUTCOME_SQL = (
    "SELECT id, outcome_known, outcome, outcome_basis, "
    "       extract(epoch FROM outcome_at)::float8 AS outcome_at "
    "  FROM external_valuations WHERE id = ANY($1::bigint[])")


async def pending_valuations(conn, *, since, until, limit=2000) -> list:
    return [dict(r) for r in await conn.fetch(PENDING_SQL, ts(since),
                                              ts(until), int(limit))]


async def resolved_valuations(conn, *, since, until, limit=MAX_ROWS) -> list:
    return [dict(r) for r in await conn.fetch(RESOLVED_SQL, ts(since),
                                              ts(until), int(limit))]


async def quotes(conn, slugs, *, since, until, limit=MAX_ROWS) -> dict:
    """{(slug, payout_event): [(at, p)]} -- for the as-of feature window
    and Xavier's post-entry replay."""
    slugs = sorted({s for s in slugs if s})
    if not slugs:
        return {}
    out: dict = {}
    for r in await conn.fetch(QUOTES_SQL, slugs, ts(since), ts(until),
                              int(limit)):
        out.setdefault((r["us_market_slug"], r["payout_event"]), []).append(
            (float(r["at"]), C.num(r["probability"])))
    return out


async def source_outcomes(conn, ids) -> dict:
    ids = sorted({int(i) for i in ids if i is not None})
    if not ids:
        return {}
    return {int(r["id"]): dict(r)
            for r in await conn.fetch(SOURCE_OUTCOME_SQL, ids)}


async def books_asof(conn, pairs, *, max_age_s) -> dict:
    """{(slug, t): {bids, offers, observed_at}} -- the latest readable book
    observation at or before t and no older than max_age_s (no look-ahead)."""
    pairs = sorted({(s, float(t)) for s, t in pairs if s and t is not None})
    if not pairs:
        return {}
    rows = await conn.fetch(
        "SELECT q.slug, q.t, b.bids, b.offers, "
        "       extract(epoch FROM b.observed_at)::float8 AS observed_at "
        "  FROM unnest($1::text[], $2::float8[]) AS q(slug, t) "
        "  CROSS JOIN LATERAL ("
        "    SELECT bids, offers, observed_at FROM paper_book_observations "
        "     WHERE us_market_slug = q.slug AND error IS NULL "
        "       AND observed_at <= to_timestamp(q.t) "
        "       AND observed_at >= to_timestamp(q.t - $3) "
        "     ORDER BY observed_at DESC LIMIT 1) b",
        [p[0] for p in pairs], [p[1] for p in pairs], float(max_age_s))
    return {(r["slug"], float(r["t"])): {"bids": r["bids"],
                                         "offers": r["offers"],
                                         "observed_at": r["observed_at"]}
            for r in rows}


async def books_between(conn, slug, *, since, until, limit=500) -> list:
    rows = await conn.fetch(
        "SELECT bids, offers, extract(epoch FROM observed_at)::float8 AS at "
        "  FROM paper_book_observations WHERE us_market_slug = $1 "
        "   AND error IS NULL AND observed_at > $2 AND observed_at <= $3 "
        " ORDER BY observed_at LIMIT $4", slug, ts(since), ts(until),
        int(limit))
    return [dict(r) for r in rows]


async def premap(conn, slugs) -> dict:
    return await IR.premap(conn, slugs)


async def regimes(conn, *, since) -> list:
    """[(computed_at, recommendation)] ascending (intel, migration 208)."""
    if not await conn.fetchval(
            "SELECT to_regclass('intel_regime_states') IS NOT NULL"):
        return []
    rows = await conn.fetch(
        "SELECT extract(epoch FROM computed_at)::float8 AS at, "
        "       recommendation FROM intel_regime_states "
        " WHERE computed_at >= $1 ORDER BY computed_at LIMIT $2",
        ts(since), MAX_ROWS)
    return [(float(r["at"]), r["recommendation"]) for r in rows]


async def eddie_estimates(conn, slugs) -> tuple:
    """THE EDDIE INTERFACE (claude/pos-agents builds EDDIE in parallel):
    ({slug: [{at, execution_uncertainty, fill_probability}]}, why). A
    READ of `eddie_execution_estimates` when the table exists; absent, or a
    shape this reader does not recognise, -> ({}, reason). Never an import
    of an execution module."""
    if not await conn.fetchval(
            "SELECT to_regclass('eddie_execution_estimates') IS NOT NULL"):
        return {}, "EDDIE_INTERFACE_ABSENT_NO_eddie_execution_estimates_TABLE"
    slugs = sorted({s for s in slugs if s})
    try:
        rows = await conn.fetch(
            "SELECT us_market_slug, execution_uncertainty, fill_probability,"
            "       extract(epoch FROM estimated_at)::float8 AS at "
            "  FROM eddie_execution_estimates "
            " WHERE us_market_slug = ANY($1::text[]) "
            " ORDER BY estimated_at LIMIT $2", slugs, MAX_ROWS)
    except Exception as exc:                                   # noqa: BLE001
        return {}, "EDDIE_INTERFACE_SHAPE_UNRECOGNISED_%s" % type(
            exc).__name__
    out: dict = {}
    for r in rows:
        out.setdefault(r["us_market_slug"], []).append(dict(r))
    return out, None


# ── the layer's own tables ───────────────────────────────────────────

def _reg(r) -> dict:
    d = dict(r)
    d["document"] = C.jload(d["document"])
    return d


async def registrations(conn) -> list:
    rows = await conn.fetch(
        "SELECT registration_id, kind, subject_id, version, family, role, "
        "       document, sha256, status, status_reason, min_sample, "
        "       family_size, "
        "       extract(epoch FROM registered_at)::float8 AS registered_at "
        "  FROM poslearn_registrations ORDER BY subject_id, version")
    return [_reg(r) for r in rows]


async def opportunities(conn, *, since, limit=MAX_ROWS) -> dict:
    rows = await conn.fetch(
        "SELECT opportunity_id, source_id, unit, us_market_slug, sport, "
        "       league, market, live_state, p_reference, price, price_basis, "
        "       fee, features, "
        "       extract(epoch FROM opportunity_at)::float8 AS opportunity_at,"
        "       extract(epoch FROM captured_at)::float8 AS captured_at "
        "  FROM poslearn_opportunities WHERE opportunity_at >= $1 "
        " ORDER BY opportunity_at DESC LIMIT $2", ts(since), int(limit))
    out = {}
    for r in rows:
        d = dict(r)
        d["features"] = C.jload(d["features"]) or {}
        out[d["opportunity_id"]] = d
    return out


async def captured_units(conn, units) -> set:
    units = sorted({u for u in units if u})
    if not units:
        return set()
    rows = await conn.fetch(
        "SELECT unit FROM poslearn_opportunities "
        " WHERE source_kind = 'EXTERNAL_VALUATION' "
        "   AND unit = ANY($1::text[])", units)
    return {r["unit"] for r in rows}


async def outcomes(conn, ids) -> dict:
    ids = sorted(set(ids))
    if not ids:
        return {}
    rows = await conn.fetch(
        "SELECT opportunity_id, outcome_class, outcome AS o, basis, "
        "       extract(epoch FROM outcome_at)::float8 AS outcome_at "
        "  FROM poslearn_outcomes WHERE opportunity_id = ANY($1::text[])",
        ids)
    return {r["opportunity_id"]: dict(r) for r in rows}


async def forecasts(conn, ids) -> list:
    ids = sorted(set(ids))
    if not ids:
        return []
    rows = await conn.fetch(
        "SELECT registration_id, opportunity_id, probability, "
        "       predicted_net_edge, action, abstain_reason, shadow_usd, "
        "       edge_confidence, expected_net_edge, avoidance_level, "
        "       avoidance_risk, output, "
        "       extract(epoch FROM predicted_at)::float8 AS predicted_at "
        "  FROM poslearn_forecasts WHERE opportunity_id = ANY($1::text[])",
        ids)
    out = []
    for r in rows:
        d = dict(r)
        d["output"] = C.jload(d["output"]) or {}
        out.append(d)
    return out


async def steps(conn) -> list:
    rows = await conn.fetch(
        "SELECT registration_id, step, actor, outcome, evidence, "
        "       extract(epoch FROM at)::float8 AS at "
        "  FROM poslearn_promotion_steps ORDER BY step_id")
    return [dict(r, evidence=C.jload(r["evidence"])) for r in rows]


async def approvals(conn) -> list:
    rows = await conn.fetch(
        "SELECT approval_id, registration_id, decision, approver, statement,"
        "       extract(epoch FROM approved_at)::float8 AS approved_at "
        "  FROM poslearn_human_approvals")
    return [dict(r) for r in rows]


async def experiments(conn) -> list:
    rows = await conn.fetch(
        "SELECT *, extract(epoch FROM start_at)::float8 AS start_epoch, "
        "       extract(epoch FROM stop_at)::float8 AS stop_epoch "
        "  FROM poslearn_experiments ORDER BY experiment_id")
    out = []
    for r in rows:
        d = dict(r)
        for k in ("primary_metric", "secondary_metrics", "randomization",
                  "arms", "policy_versions", "stopping_rule",
                  "failure_criteria", "result"):
            d[k] = C.jload(d[k])
        out.append(d)
    return out


async def assignments(conn, experiment_id) -> list:
    rows = await conn.fetch(
        "SELECT unit_id, opportunity_id, arm, draw, "
        "       extract(epoch FROM assigned_at)::float8 AS assigned_at "
        "  FROM poslearn_experiment_assignments WHERE experiment_id = $1 "
        " ORDER BY assigned_at, unit_id", experiment_id)
    return [dict(r) for r in rows]


async def experiment_outcomes(conn, experiment_id) -> dict:
    rows = await conn.fetch(
        "SELECT unit_id, metrics FROM poslearn_experiment_outcomes "
        " WHERE experiment_id = $1", experiment_id)
    return {r["unit_id"]: C.jload(r["metrics"]) for r in rows}


async def reviews(conn, experiment_id) -> list:
    rows = await conn.fetch(
        "SELECT kind, actor, outcome, findings, "
        "       extract(epoch FROM at)::float8 AS at "
        "  FROM poslearn_experiment_reviews WHERE experiment_id = $1 "
        " ORDER BY review_id", experiment_id)
    return [dict(r, findings=C.jload(r["findings"])) for r in rows]


async def last_run(conn, component) -> dict | None:
    r = await conn.fetchrow(
        "SELECT summary, extract(epoch FROM started_at)::float8 AS at "
        "  FROM poslearn_runs WHERE component = $1 AND status = 'OK' "
        " ORDER BY started_at DESC LIMIT 1", component)
    return None if r is None else {"summary": C.jload(r["summary"]),
                                   "at": r["at"]}
