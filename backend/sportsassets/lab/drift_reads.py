"""LAB-F DRIFT SENTINEL: THE READ-ONLY READERS AND THE ONE POINT-IN-TIME
ACCESSOR.

Every row the sentinel computes on comes from one of the SELECTs below and
then through `point_in_time()` -- THE choke point: a row is kept only when
EVERY one of its own record stamps is at or before the replay clock (an
unknown stamp drops the row: fail closed), and its event time is too. The
SQL also filters on the same stamps (bounded reads); the accessor re-checks
them in Python so a reader that forgot the predicate still cannot leak a
future row (tests/test_lab_drift_sentinel.py feeds deliberately future-dated
rows to both).

THE SOURCES (table.column, the record stamp the accessor checks):

  versions     paper_decisions: strategy, policy_version, the parameter
               version the decision records (policy_decision->parameters->>
               version_id), first / last decided_at -- grouped over rows
               with decided_at AND recorded_at <= clock; each group carries
               max(recorded_at) so the accessor checks the aggregate too
  latest       paper_decisions: each strategy's most recent decision (its
               version is the ACTIVE version), DISTINCT ON (strategy) ORDER
               BY decided_at DESC, decision_id DESC
  decisions    paper_decisions (recorded_at): verdict, refusal,
               policy_decision->>gross_edge_pp, economics->acquisition->>
               expected_net_profit_usd / qty, pinnacle->>at / received_at /
               age_s / provider, book->>age_at_decision_s, label->>
               competition / market_type, fixture, contract (us_market_slug,
               holding_side); external_valuations.sport_family (by
               valuation_id) for an unattributed league. Fetched only for
               each strategy's ACTIVE version and only inside the SUPERSET
               [first, first + REF_MAX) U [clock - CMP_MAX, clock] of the
               windows the sentinel derives (it applies the exact windows)
  orders       paper_orders role ENTRY (created_at; outcome by terminal_at
               <= clock -- the table is mutable, terminal_at is stamped by
               the terminal transition) with the filled quantity summed from
               APPEND-ONLY paper_fills rows recorded <= clock; joined to the
               decision (recorded_at) for version and league
  eddie        eddie_execution_outcomes (created_at): realized_slippage_pp,
               realized_adverse_selection_pp, source PAPER / ACTUAL; the
               newest outcome per (decision, source) recorded <= clock
  econ         pos_position_economics (computed_at): the newest revision per
               (book, position_key) computed <= clock, CLOSED, released in
               range: time_committed_h; version through the group's sleeve
               classification (classified_at) and its decision
  settlements  paper_settlements (recorded_at): the newest version per
               position_key recorded <= clock, settled in range: outcome
  shadow       LIVE_SHADOW record counts: canonical_intent_executions mode
               SHADOW (migration 225) and execmirror_orders by state, in
               range -- context for an UNAVAILABLE reason, never a statistic
  actual       ACTUAL fill counts: execmirror_fills, bettor_funded_fills,
               kalshi_live_fills, in range -- the same

READ ONLY: SELECT statements only, inside the caller's READ ONLY
transaction. This module imports no order, venue, execution or funded module
and writes nothing.
"""
from __future__ import annotations

import json
import math

VERSION = "LAB_DRIFT_READS_V1"

MAX_DECISION_ROWS = 50_000
MAX_ROWS = 20_000
LOOKBACK_S = 14 * 86400.0

#: (source, relation that must exist, reason when it does not)
RELATIONS = {
    "decisions": ("paper_decisions", "PAPER_DECISIONS_TABLE_ABSENT"),
    "orders": ("paper_orders", "PAPER_ORDERS_TABLE_ABSENT"),
    "fills": ("paper_fills", "PAPER_FILLS_TABLE_ABSENT"),
    "eddie": ("eddie_execution_outcomes", "MIGRATION_217_NOT_APPLIED"),
    "econ": ("pos_position_economics", "MIGRATION_216_NOT_APPLIED"),
    "sleeves": ("paper_sleeve_classifications", "MIGRATION_223_NOT_APPLIED"),
    "settlements": ("paper_settlements", "PAPER_SETTLEMENTS_TABLE_ABSENT"),
    "canonical_executions": ("canonical_intent_executions",
                             "MIGRATION_225_NOT_APPLIED"),
    "execmirror_orders": ("execmirror_orders", "MIGRATION_192_NOT_APPLIED"),
    "execmirror_fills": ("execmirror_fills", "MIGRATION_192_NOT_APPLIED"),
    "bettor_funded_fills": ("bettor_funded_fills",
                            "FUNDED_FILLS_TABLE_ABSENT"),
    "kalshi_live_fills": ("kalshi_live_fills", "MIGRATION_196_NOT_APPLIED"),
}

# ═════════════════════════════════════════════════════════════════════
# THE ONE POINT-IN-TIME ACCESSOR
# ═════════════════════════════════════════════════════════════════════

#: the record stamps each source's rows carry (all must be <= clock)
RECORD_KEYS = {
    "versions": ("recorded_at",),
    "latest": ("recorded_at",),
    "decisions": ("recorded_at",),
    "orders": ("recorded_at", "decision_recorded_at"),
    "eddie": ("recorded_at", "decision_recorded_at"),
    "econ": ("recorded_at",),
    "settlements": ("recorded_at",),
}
#: the event time each source's rows carry (must be <= clock)
EVENT_KEY = {"versions": "last_at", "latest": "t", "decisions": "t",
             "orders": "t", "eddie": "t", "econ": "t", "settlements": "t"}


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    if hasattr(v, "timestamp"):
        return float(v.timestamp())
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def point_in_time(rows: list, clock: float, *, source: str) -> list:
    """THE CHOKE POINT. Keep a row only when every record stamp named for
    its source is known and <= clock, and its event time is known and <=
    clock. Optional joined stamps (a LEFT JOIN that found nothing) are
    named by a None-valued key ending in `_optional_recorded_at` and are
    checked only when present."""
    keys = RECORD_KEYS[source]
    ev = EVENT_KEY[source]
    c = float(clock)
    out = []
    for r in rows:
        ok = True
        for k in keys:
            v = _num(r.get(k))
            if v is None or v > c:
                ok = False
                break
        if not ok:
            continue
        for k, v in r.items():
            if k.endswith("_optional_recorded_at") and v is not None:
                if _num(v) is None or _num(v) > c:
                    ok = False
                    break
        if not ok:
            continue
        t = _num(r.get(ev))
        if t is None or t > c:
            continue
        out.append(r)
    return out


# ═════════════════════════════════════════════════════════════════════
# THE SELECTS ($1 = clock epoch; the others as named per query)
# ═════════════════════════════════════════════════════════════════════

PARAM = "d.policy_decision->'parameters'->>'version_id'"

Q_VERSIONS = (
    "SELECT d.strategy, d.policy_version, "
    "       " + PARAM + " AS param_version_id, "
    "       extract(epoch FROM min(d.decided_at))::float8 AS first_at, "
    "       extract(epoch FROM max(d.decided_at))::float8 AS last_at, "
    "       extract(epoch FROM max(d.recorded_at))::float8 AS recorded_at, "
    "       count(*)::int AS n, "
    "       count(*) FILTER (WHERE d.verdict = 'ENTER')::int AS enter_n "
    "  FROM paper_decisions d "
    " WHERE d.decided_at <= to_timestamp($1) "
    "   AND d.recorded_at <= to_timestamp($1) "
    " GROUP BY 1, 2, 3 "
    " ORDER BY 1, 4")

Q_LATEST = (
    "SELECT DISTINCT ON (d.strategy) d.strategy, d.policy_version, "
    "       " + PARAM + " AS param_version_id, d.decision_id, "
    "       extract(epoch FROM d.decided_at)::float8 AS t, "
    "       extract(epoch FROM d.recorded_at)::float8 AS recorded_at "
    "  FROM paper_decisions d "
    " WHERE d.decided_at <= to_timestamp($1) "
    "   AND d.recorded_at <= to_timestamp($1) "
    " ORDER BY d.strategy, d.decided_at DESC, d.decision_id DESC")

#: $2 = REF_MAX seconds, $3 = CMP_MAX seconds, $4 = row limit
Q_DECISIONS = (
    "WITH v AS ( "
    "    SELECT d.strategy, d.policy_version, " + PARAM + " AS pv, "
    "           min(d.decided_at) AS first_at "
    "      FROM paper_decisions d "
    "     WHERE d.decided_at <= to_timestamp($1) "
    "       AND d.recorded_at <= to_timestamp($1) "
    "     GROUP BY 1, 2, 3 "
    "), latest AS ( "
    "    SELECT DISTINCT ON (d.strategy) d.strategy, d.policy_version, "
    "           " + PARAM + " AS pv "
    "      FROM paper_decisions d "
    "     WHERE d.decided_at <= to_timestamp($1) "
    "       AND d.recorded_at <= to_timestamp($1) "
    "     ORDER BY d.strategy, d.decided_at DESC, d.decision_id DESC "
    ") "
    "SELECT d.decision_id, d.strategy, d.policy_version, "
    "       " + PARAM + " AS param_version_id, "
    "       extract(epoch FROM d.decided_at)::float8 AS t, "
    "       extract(epoch FROM d.recorded_at)::float8 AS recorded_at, "
    "       d.verdict, d.refusal, d.us_market_slug, d.holding_side, "
    "       d.fixture, d.label->>'competition' AS competition, "
    "       d.label->>'market_type' AS market_type, ev.sport_family, "
    "       d.policy_decision->>'gross_edge_pp' AS gross_edge_pp, "
    "       d.economics->'acquisition'->>'expected_net_profit_usd' "
    "           AS net_ev_usd, "
    "       d.economics->'acquisition'->>'qty' AS acq_qty, "
    "       d.pinnacle->>'at' AS pin_at, "
    "       d.pinnacle->>'received_at' AS pin_received_at, "
    "       d.pinnacle->>'age_s' AS pin_age_s, "
    "       d.pinnacle->>'provider' AS pin_provider, "
    "       d.book->>'age_at_decision_s' AS book_age_s "
    "  FROM paper_decisions d "
    "  JOIN v ON v.strategy = d.strategy "
    "        AND v.policy_version = d.policy_version "
    "        AND v.pv IS NOT DISTINCT FROM (" + PARAM + ") "
    "  JOIN latest l ON l.strategy = d.strategy "
    "        AND l.policy_version = d.policy_version "
    "        AND l.pv IS NOT DISTINCT FROM (" + PARAM + ") "
    "  LEFT JOIN external_valuations ev ON ev.id = d.valuation_id "
    " WHERE d.decided_at <= to_timestamp($1) "
    "   AND d.recorded_at <= to_timestamp($1) "
    "   AND ((d.decided_at >= v.first_at "
    "         AND d.decided_at < v.first_at "
    "                            + make_interval(secs => $2::float8)) "
    "        OR d.decided_at >= to_timestamp($1) "
    "                           - make_interval(secs => $3::float8)) "
    " ORDER BY d.decided_at, d.decision_id "
    " LIMIT $4")

#: $2 = since epoch, $3 = row limit
Q_ORDERS = (
    "SELECT o.order_id, o.strategy, o.group_id, o.decision_id, "
    "       extract(epoch FROM o.decided_at)::float8 AS t, "
    "       extract(epoch FROM o.created_at)::float8 AS recorded_at, "
    "       extract(epoch FROM o.terminal_at)::float8 AS terminal_at, "
    "       o.terminal_reason, o.qty::float8 AS qty, "
    "       o.us_market_slug, o.holding_side, "
    "       d.policy_version, " + PARAM + " AS param_version_id, "
    "       extract(epoch FROM d.recorded_at)::float8 "
    "           AS decision_recorded_at, "
    "       d.label->>'competition' AS competition, ev.sport_family, "
    "       (SELECT coalesce(sum(f.qty), 0)::float8 FROM paper_fills f "
    "         WHERE f.order_id = o.order_id "
    "           AND f.recorded_at <= to_timestamp($1)) AS filled_qty "
    "  FROM paper_orders o "
    "  JOIN paper_decisions d ON d.decision_id = o.decision_id "
    "  LEFT JOIN external_valuations ev ON ev.id = d.valuation_id "
    " WHERE o.role = 'ENTRY' "
    "   AND o.decided_at >= to_timestamp($2) "
    "   AND o.decided_at <= to_timestamp($1) "
    "   AND o.created_at <= to_timestamp($1) "
    "   AND d.recorded_at <= to_timestamp($1) "
    " ORDER BY o.decided_at, o.order_id "
    " LIMIT $3")

Q_EDDIE = (
    "SELECT DISTINCT ON (o.decision_id, o.source) o.outcome_id, "
    "       o.decision_id, o.source, "
    "       extract(epoch FROM o.created_at)::float8 AS recorded_at, "
    "       o.realized_slippage_pp, o.realized_adverse_selection_pp, "
    "       (o.unmeasured ? 'realized_adverse_selection') "
    "           AS adverse_selection_unmeasured, "
    "       d.strategy, d.policy_version, "
    "       " + PARAM + " AS param_version_id, "
    "       extract(epoch FROM d.decided_at)::float8 AS t, "
    "       extract(epoch FROM d.recorded_at)::float8 "
    "           AS decision_recorded_at, "
    "       d.us_market_slug, d.holding_side, "
    "       d.label->>'competition' AS competition, ev.sport_family "
    "  FROM eddie_execution_outcomes o "
    "  JOIN paper_decisions d ON d.decision_id = o.decision_id "
    "  LEFT JOIN external_valuations ev ON ev.id = d.valuation_id "
    " WHERE o.created_at <= to_timestamp($1) "
    "   AND d.recorded_at <= to_timestamp($1) "
    "   AND d.decided_at >= to_timestamp($2) "
    "   AND d.decided_at <= to_timestamp($1) "
    " ORDER BY o.decision_id, o.source, o.created_at DESC, o.outcome_id DESC "
    " LIMIT $3")

_GROUP_VERSION = (
    "  LEFT JOIN LATERAL ( "
    "    SELECT c.policy_version AS c_policy_version, c.decision_id, "
    "           extract(epoch FROM c.classified_at)::float8 "
    "               AS sleeve_optional_recorded_at "
    "      FROM paper_sleeve_classifications c "
    "     WHERE c.group_id = x.group_id "
    "       AND c.classified_at <= to_timestamp($1) "
    "     ORDER BY c.classified_at DESC, c.classification_id DESC "
    "     LIMIT 1) c ON true "
    "  LEFT JOIN paper_decisions d ON d.decision_id = c.decision_id "
    "        AND d.recorded_at <= to_timestamp($1) "
    "  LEFT JOIN external_valuations ev ON ev.id = d.valuation_id ")

_GROUP_COLS = (
    "       c.c_policy_version, c.sleeve_optional_recorded_at, "
    "       d.policy_version, " + PARAM + " AS param_version_id, "
    "       d.strategy AS decision_strategy, "
    "       extract(epoch FROM d.recorded_at)::float8 "
    "           AS decision_optional_recorded_at, "
    "       d.label->>'competition' AS competition, ev.sport_family ")

Q_ECON = (
    "SELECT x.*, " + _GROUP_COLS +
    "  FROM ( "
    "    SELECT DISTINCT ON (e.book, e.position_key) e.econ_id, e.book, "
    "           e.position_key, e.group_id, e.strategy, e.state, "
    "           e.revision, "
    "           extract(epoch FROM e.computed_at)::float8 AS recorded_at, "
    "           extract(epoch FROM e.released_at)::float8 AS t, "
    "           e.time_committed_h, e.us_market_slug, e.holding_side "
    "      FROM pos_position_economics e "
    "     WHERE e.book IN ('PAPER', 'ACTUAL') "
    "       AND e.computed_at <= to_timestamp($1) "
    "     ORDER BY e.book, e.position_key, e.revision DESC) x "
    + _GROUP_VERSION +
    " WHERE x.state = 'CLOSED' "
    "   AND x.t >= $2::float8 AND x.t <= $1::float8 "
    " ORDER BY x.t, x.position_key "
    " LIMIT $3")

Q_SETTLEMENTS = (
    "SELECT x.*, " + _GROUP_COLS +
    "  FROM ( "
    "    SELECT DISTINCT ON (s.position_key) s.settlement_id, "
    "           s.position_key, s.group_id, s.outcome, s.version, "
    "           s.us_market_slug, s.holding_side, "
    "           extract(epoch FROM s.settled_at)::float8 AS t, "
    "           extract(epoch FROM s.recorded_at)::float8 AS recorded_at "
    "      FROM paper_settlements s "
    "     WHERE s.recorded_at <= to_timestamp($1) "
    "     ORDER BY s.position_key, s.version DESC) x "
    + _GROUP_VERSION +
    " WHERE x.t >= $2::float8 AND x.t <= $1::float8 "
    " ORDER BY x.t, x.position_key "
    " LIMIT $3")

Q_SHADOW_CANONICAL = (
    "SELECT state, count(*)::int AS n "
    "  FROM canonical_intent_executions "
    " WHERE mode = 'SHADOW' "
    "   AND created_at >= to_timestamp($2) "
    "   AND created_at <= to_timestamp($1) "
    " GROUP BY state ORDER BY state")
Q_SHADOW_EXECMIRROR = (
    "SELECT state, count(*)::int AS n "
    "  FROM execmirror_orders "
    " WHERE created_at >= to_timestamp($2) "
    "   AND created_at <= to_timestamp($1) "
    " GROUP BY state ORDER BY state")
Q_ACTUAL_FILLS = {
    "execmirror_fills": (
        "SELECT count(*)::int AS n FROM execmirror_fills "
        " WHERE observed_at >= to_timestamp($2) "
        "   AND observed_at <= to_timestamp($1)"),
    "bettor_funded_fills": (
        "SELECT count(*)::int AS n FROM bettor_funded_fills "
        " WHERE recorded_at >= to_timestamp($2) "
        "   AND recorded_at <= to_timestamp($1)"),
    "kalshi_live_fills": (
        "SELECT count(*)::int AS n FROM kalshi_live_fills "
        " WHERE observed_at >= to_timestamp($2) "
        "   AND observed_at <= to_timestamp($1)"),
}


def _rows(records) -> list:
    out = []
    for r in records:
        d = dict(r)
        for k, v in list(d.items()):
            if hasattr(v, "timestamp"):
                d[k] = float(v.timestamp())
            elif v is not None and type(v).__name__ == "Decimal":
                d[k] = float(v)
        out.append(d)
    return out


async def _has(conn, rel: str) -> bool:
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                        rel))
    except Exception:                                           # noqa: BLE001
        return False


def query_plan(*, clock: float, since: float, ref_max_s: float,
               cmp_max_s: float, present: set) -> list:
    """[(source, sql, args)] -- the ONE list of statements the endpoint and
    the production export both run (the export renders the same SQL with
    literal arguments)."""
    c, s = float(clock), float(since)
    plan = []
    if "decisions" in present:
        plan += [("versions", Q_VERSIONS, (c,)),
                 ("latest", Q_LATEST, (c,)),
                 ("decisions", Q_DECISIONS,
                  (c, float(ref_max_s), float(cmp_max_s),
                   MAX_DECISION_ROWS))]
    if {"orders", "fills", "decisions"} <= present:
        plan.append(("orders", Q_ORDERS, (c, s, MAX_ROWS)))
    if {"eddie", "decisions"} <= present:
        plan.append(("eddie", Q_EDDIE, (c, s, MAX_ROWS)))
    if {"econ", "sleeves", "decisions"} <= present:
        plan.append(("econ", Q_ECON, (c, s, MAX_ROWS)))
    if {"settlements", "sleeves", "decisions"} <= present:
        plan.append(("settlements", Q_SETTLEMENTS, (c, s, MAX_ROWS)))
    if "canonical_executions" in present:
        plan.append(("shadow_canonical", Q_SHADOW_CANONICAL, (c, s)))
    if "execmirror_orders" in present:
        plan.append(("shadow_execmirror", Q_SHADOW_EXECMIRROR, (c, s)))
    for name, sql in Q_ACTUAL_FILLS.items():
        if name in present:
            plan.append(("actual_" + name, sql, (c, s)))
    return plan


LIMITS = {"decisions": MAX_DECISION_ROWS, "orders": MAX_ROWS,
          "eddie": MAX_ROWS, "econ": MAX_ROWS, "settlements": MAX_ROWS}


def assemble(raw: dict, *, clock: float, since: float,
             present: set) -> dict:
    """Raw {source: [row dicts]} -> the sentinel's input, every row-level
    source through point_in_time(). Pure (the endpoint and the production
    export's parser both call it)."""
    out: dict = {"clock": float(clock), "since": float(since),
                 "sources": {}, "shadow": {}, "actual": {}}
    for name, (rel, why) in RELATIONS.items():
        out["sources"][name] = {"relation": rel, "present": name in present,
                                "why": None if name in present else why}
    for src in ("versions", "latest", "decisions", "orders", "eddie", "econ",
                "settlements"):
        rows = list(raw.get(src) or [])
        kept = point_in_time(rows, clock, source=src)
        lim = LIMITS.get(src)
        out[src] = kept
        out["sources"].setdefault(src, {})
        out["sources"][src].update(
            rows_read=len(rows), rows_kept=len(kept),
            dropped_by_point_in_time=len(rows) - len(kept),
            truncated=bool(lim is not None and len(rows) >= lim))
    out["shadow"] = {
        "canonical_intent_executions": (
            {r["state"]: int(r["n"]) for r in raw.get("shadow_canonical") or []}
            if "canonical_executions" in present else None),
        "execmirror_orders": (
            {r["state"]: int(r["n"])
             for r in raw.get("shadow_execmirror") or []}
            if "execmirror_orders" in present else None)}
    for name in Q_ACTUAL_FILLS:
        rows = raw.get("actual_" + name)
        out["actual"][name] = (int(rows[0]["n"]) if rows else 0) \
            if name in present else None
    return out


MAX_LOOKBACK_S = 60 * 86400.0


async def adaptive_since(conn, *, clock: float, params: dict) -> float:
    """The earliest instant the position / order / outcome reads must cover:
    the earliest reference-window start of an ACTIVE version (from the same
    versions / latest SELECTs, through the same accessor and the sentinel's
    own window rule), never later than clock - CMP_MAX and never earlier
    than clock - MAX_LOOKBACK_S."""
    from . import drift_sentinel as DS
    c = float(clock)
    versions = point_in_time(_rows(await conn.fetch(Q_VERSIONS, c)), c,
                             source="versions")
    latest = point_in_time(_rows(await conn.fetch(Q_LATEST, c)), c,
                           source="latest")
    wins = DS.derive_windows(versions, latest, clock=c, params=params)
    starts = [w["ref"][0] for w in wins.values()
              if w.get("active") and w.get("ref")]
    since = min(starts + [c - float(params["cmp_max_s"])])
    return max(since, c - MAX_LOOKBACK_S)


async def gather(conn, *, clock: float, since: float | None,
                 ref_max_s: float, cmp_max_s: float) -> dict:
    """SELECTs only; the caller holds the READ ONLY transaction. `since`
    None: derived by adaptive_since()."""
    present = set()
    for name, (rel, _why) in RELATIONS.items():
        if await _has(conn, rel):
            present.add(name)
    if since is None:
        since = (await adaptive_since(conn, clock=clock, params={
            "ref_max_s": ref_max_s, "cmp_max_s": cmp_max_s})
            if "decisions" in present else float(clock) - LOOKBACK_S)
    raw: dict = {}
    for src, sql, args in query_plan(clock=clock, since=since,
                                     ref_max_s=ref_max_s,
                                     cmp_max_s=cmp_max_s, present=present):
        raw[src] = _rows(await conn.fetch(sql, *args))
    return assemble(raw, clock=clock, since=since, present=present)


# ═════════════════════════════════════════════════════════════════════
# THE PRODUCTION EXPORT (research-sql.yml): THE SAME SELECTS, RENDERED
# ═════════════════════════════════════════════════════════════════════

EXPORT_TAG = "LABDRIFT"


def _literal(v) -> str:
    if isinstance(v, bool) or v is None:
        raise ValueError("unsupported literal")
    if isinstance(v, int):
        return str(int(v))
    f = float(v)
    if not math.isfinite(f):
        raise ValueError("non-finite literal")
    return repr(f)


def render(sql: str, args: tuple) -> str:
    """Substitute $n with literals, highest n first ($10 before $1)."""
    out = sql
    for i in range(len(args), 0, -1):
        out = out.replace("$%d" % i, _literal(args[i - 1]))
    if "$" in out.replace("$$", ""):
        raise ValueError("an unbound placeholder remains")
    return out


def export_sql(*, clock: float, since: float, ref_max_s: float,
               cmp_max_s: float, present: set, sources=None,
               between: tuple | None = None) -> str:
    """The research SQL file: per source one `LABDRIFT|<source>#cols|[names]`
    line, then one `LABDRIFT|<source>|[values]` line per row (row_to_json of
    the very SELECT the endpoint runs, in column order). `sources` limits the
    file to those sources (the meta and relations lines are always written);
    `between` = (a, b) keeps only rows with a <= t < b -- an OUTER filter on
    the same SELECT, so one large source can be split across several files
    (a job log is read back through a size-limited tool)."""
    lines = [
        "-- LAB-F DRIFT SENTINEL: PRODUCTION EXPORT (SELECT only, read-only).",
        "-- Generated by sportsassets.lab.drift_runner --export-sql from the",
        "-- SAME SELECT statements the read-only endpoint runs",
        "-- (sportsassets/lab/drift_reads.py), with literal arguments:",
        "--   clock %r, since %r, ref_max_s %r, cmp_max_s %r."
        % (float(clock), float(since), float(ref_max_s), float(cmp_max_s)),
        "-- Output rows: LABDRIFT|<source>#cols|[names], then one",
        "-- LABDRIFT|<source>|[values] per row; the parser",
        "-- (drift_runner --from-log) applies point_in_time() and the pure",
        "-- sentinel exactly as the endpoint does. Bounded by LIMITs.",
        "",
        "\\echo === LABDRIFT relations present",
    ]
    lines.append("SELECT '%s|meta|' || json_build_object('clock', %s, "
                 "'since', %s, 'ref_max_s', %s, 'cmp_max_s', %s)::text "
                 "AS line;" % (EXPORT_TAG, _literal(float(clock)),
                               _literal(float(since)),
                               _literal(float(ref_max_s)),
                               _literal(float(cmp_max_s))))
    rels = ", ".join(
        "(%s, to_regclass(%s) IS NOT NULL)" % (
            "'" + name + "'", "'" + rel + "'")
        for name, (rel, _w) in RELATIONS.items())
    lines.append("SELECT '%s|relations|' || json_object_agg(k, v)::text "
                 "AS line FROM (VALUES %s) AS r(k, v);" % (EXPORT_TAG, rels))
    for src, sql, args in query_plan(clock=clock, since=since,
                                     ref_max_s=ref_max_s, cmp_max_s=cmp_max_s,
                                     present=present):
        if sources is not None and src not in sources:
            continue
        body = render(sql, args)
        where = ""
        if between is not None:
            where = " WHERE q.t >= %s AND q.t < %s" % (
                _literal(float(between[0])), _literal(float(between[1])))
        lines.append("\\echo === LABDRIFT %s" % src)
        # COMPACT: the column names once (json_each keeps row_to_json's
        # column order), then one positional array per row -- the job log
        # stays a fraction of the size of keyed JSON. The SELECT is the same.
        lines.append("SELECT '%s|%s#cols|' || (SELECT json_agg(e.key) FROM "
                     "json_each(row_to_json(q)) e)::text AS line FROM (%s) q "
                     "LIMIT 1;" % (EXPORT_TAG, src, body))
        lines.append("SELECT '%s|%s|' || (SELECT json_agg(e.value) FROM "
                     "json_each(row_to_json(q)) e)::text AS line FROM (%s) q"
                     "%s;" % (EXPORT_TAG, src, body, where))
    return "\n".join(lines) + "\n"


def parse_export(text: str) -> dict:
    """A research-sql job log -> {"relations": {...}, source: [rows]}.
    Lines that are not export rows are ignored."""
    out: dict = {}
    cols: dict = {}
    marker = EXPORT_TAG + "|"
    for line in text.splitlines():
        i = line.find(marker)
        if i < 0:
            continue
        body = line[i + len(marker):].rstrip()
        src, sep, payload = body.partition("|")
        if not sep:
            continue
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        if src in ("relations", "meta"):
            out[src] = obj
        elif src.endswith("#cols"):
            cols[src[:-5]] = list(obj)
        elif isinstance(obj, list):
            names = cols.get(src)
            if names is None or len(names) != len(obj):
                raise ValueError("positional row of %s without its columns"
                                 % src)
            out.setdefault(src, []).append(dict(zip(names, obj)))
        else:
            out.setdefault(src, []).append(obj)
    return out
