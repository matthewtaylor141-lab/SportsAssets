"""THE ONLY LOADER OF THE LOST OPPORTUNITY LEDGER. SELECT only, bounded
(a lookback window and a LIMIT on every read).

  settled_refusals    Derek REFUSE paper decisions whose market later
                      settled, not yet classified by this classifier version,
                      with the subsequent settlement of the same market slug
                      (the same side's row first, else the opposite side's)
  coverage_refusals   the coverage ledger's (ext_candidate_outcomes) last
                      REFUSED row per (provider event, market) before that
                      market settled, not yet classified
  score_candidates    the latest pos_capacity rows not yet scored, with the
                      decision's verdict / strategy / league and the event
                      start; plus the CAPITAL (PAPER) and CAPACITY snapshots
                      the score reads AS OF EACH DECISION (no look-ahead)
"""
from __future__ import annotations

from ..profitability import common as C
from ..profitability import reads as PR

LOOKBACK_DAYS = 30.0
MAX_PER_CYCLE = 2000
SCORE_LOOKBACK_H = 48.0
MAX_SCORES_PER_CYCLE = 1500
#: the Derek strategy the ledger classifies (the brief: Derek's refusals)
STRATEGIES = ("DEREK_ENTRY_POLICY_V2",)
DECISION_COLS = (
    "decision_id", "strategy", "us_market_slug", "holding_side", "fixture",
    "refusal", "refusals", "p_internal", "p_pinnacle", "p_blended",
    "proposed_qty", "limit_price", "book", "book_obs_id", "economics",
    "policy_decision", "pinnacle", "internal_model", "label", "valuation_id")
JSON_KEYS = ("book", "economics", "policy_decision", "pinnacle",
             "internal_model", "label")

# Only a MARKET settlement establishes the contract's outcome: WON, LOST or
# VOID_REFUND. A paper position closed at the venue's price
# (SETTLED_AT_VENUE_PRICE, written by Xavier's management) says what that
# position realised, not how the contract resolved, so it is never used as
# the outcome of a refused decision (and lol_ledger's CHECK refuses it).
SETTLEMENT_OUTCOMES = ("WON", "LOST", "VOID_REFUND")

SETTLED_REFUSALS_SQL = """
    SELECT %s,
           extract(epoch FROM d.decided_at)::float8 AS decided_at,
           st.settlement_id, st.holding_side AS st_side, st.outcome,
           st.payout_per_contract, st.version AS st_version,
           extract(epoch FROM st.settled_at)::float8 AS settled_at
      FROM paper_decisions d
      JOIN LATERAL (
            SELECT s.settlement_id, s.holding_side, s.outcome,
                   s.payout_per_contract, s.settled_at, s.version
              FROM paper_settlements s
             WHERE s.us_market_slug = d.us_market_slug
               AND s.outcome IN ('WON', 'LOST', 'VOID_REFUND')
               AND s.settled_at >= d.decided_at
               AND s.settled_at <= to_timestamp($2)
             ORDER BY (s.holding_side = d.holding_side) DESC NULLS LAST,
                      s.settled_at DESC, s.version DESC
             LIMIT 1) st ON true
     WHERE d.verdict = 'REFUSE'
       AND d.strategy = ANY($3::text[])
       AND d.decided_at >= to_timestamp($1)
       AND d.decided_at <= to_timestamp($2)
       AND d.us_market_slug IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM lol_ledger l
                        WHERE l.decision_ref = d.decision_id
                          AND l.classifier_version = $4)
     ORDER BY d.decided_at ASC
     LIMIT $5
""" % ", ".join("d.%s" % c for c in DECISION_COLS)

COVERAGE_REFUSALS_SQL = """
    SELECT * FROM (
        SELECT DISTINCT ON (o.provider_event_id, o.us_market_slug)
               o.id, o.sport_key, o.family, o.provider_event_id,
               o.us_market_slug, o.stage, o.first_refusal, o.codes,
               extract(epoch FROM o.cycle_at)::float8 AS decided_at,
               st.settlement_id, st.holding_side AS st_side, st.outcome,
               st.payout_per_contract,
               extract(epoch FROM st.settled_at)::float8 AS settled_at
          FROM ext_candidate_outcomes o
          JOIN LATERAL (
                SELECT s.settlement_id, s.holding_side, s.outcome,
                       s.payout_per_contract, s.settled_at
                  FROM paper_settlements s
                 WHERE s.us_market_slug = o.us_market_slug
                   AND s.outcome IN ('WON', 'LOST', 'VOID_REFUND')
                   AND s.settled_at >= o.cycle_at
                   AND s.settled_at <= to_timestamp($2)
                 ORDER BY s.settled_at DESC, s.version DESC
                 LIMIT 1) st ON true
         WHERE o.outcome = 'REFUSED'
           AND o.us_market_slug IS NOT NULL
           AND o.provider_event_id IS NOT NULL
           AND o.cycle_at >= to_timestamp($1)
           AND o.cycle_at <= to_timestamp($2)
         ORDER BY o.provider_event_id, o.us_market_slug, o.cycle_at DESC
    ) x
     WHERE NOT EXISTS (SELECT 1 FROM lol_ledger l
                        WHERE l.decision_ref = 'coverage:'
                              || x.provider_event_id || ':'
                              || x.us_market_slug
                          AND l.classifier_version = $3)
     ORDER BY x.decided_at ASC
     LIMIT $4
"""


def coverage_ref(provider_event_id, slug) -> str:
    return "coverage:%s:%s" % (provider_event_id, slug)


def _settlement(r) -> dict:
    return {"settlement_id": r["settlement_id"],
            "holding_side": r["st_side"], "outcome": r["outcome"],
            "payout_per_contract": C.num(r["payout_per_contract"]),
            "settled_at": C.num(r["settled_at"])}


async def _valuations(conn, ids) -> dict:
    ids = sorted({int(i) for i in ids if i is not None})
    if not ids or not await PR.has(conn, "external_valuations"):
        return {}
    rows = await conn.fetch(
        "SELECT v.id, to_jsonb(v) - 'raw_odds' AS v "
        "  FROM external_valuations v WHERE v.id = ANY($1::bigint[]) "
        " LIMIT $2", ids, MAX_PER_CYCLE)
    out = {}
    for r in rows:
        v = C.jload(r["v"]) or {}
        out[int(r["id"])] = {k: v.get(k) for k in (
            "venue", "payout_event", "settlement_comparison", "sport_family",
            "event_key", "buy_intent")}
    return out


async def _leagues_by_event(conn, event_keys) -> dict:
    keys = sorted({k for k in event_keys if k})
    if not keys or not await PR.has(conn, "ext_candidate_outcomes"):
        return {}
    rows = await conn.fetch(
        "SELECT DISTINCT ON (provider_event_id) provider_event_id, sport_key "
        "  FROM ext_candidate_outcomes "
        " WHERE provider_event_id = ANY($1::text[]) "
        " ORDER BY provider_event_id, cycle_at DESC LIMIT $2", keys,
        MAX_PER_CYCLE)
    return {r["provider_event_id"]: r["sport_key"] for r in rows}


def league_of(label: dict, val: dict | None, by_event: dict):
    """(league, basis). Never guessed: the venue slug's league token on the
    decision label, else the provider competition key of the same provider
    event in the coverage ledger, else UNATTRIBUTED:<sport family>."""
    comp = (label or {}).get("competition")
    if comp:
        return str(comp), "LABEL_COMPETITION_VENUE_EVENT_SLUG_TOKEN"
    ek = (val or {}).get("event_key") or (label or {}).get("event_key")
    if ek and by_event.get(ek):
        return by_event[ek], "COVERAGE_LEDGER_SPORT_KEY_OF_THE_PROVIDER_EVENT"
    fam = (val or {}).get("sport_family")
    return ("UNATTRIBUTED:%s" % (fam or "UNKNOWN"),
            "NO_LEAGUE_RECORDED")


async def settled_refusals(conn, *, now, classifier_version,
                           days=LOOKBACK_DAYS, limit=MAX_PER_CYCLE) -> list:
    rows = await conn.fetch(SETTLED_REFUSALS_SQL,
                            float(now) - days * 86400.0, float(now),
                            list(STRATEGIES), classifier_version, int(limit))
    vals = await _valuations(conn, [r["valuation_id"] for r in rows])
    decs = []
    for r in rows:
        d = {c: r[c] for c in DECISION_COLS}
        for k in JSON_KEYS:
            d[k] = C.jload(d[k])
        d["refusals"] = list(d["refusals"] or [])
        for k in ("p_internal", "p_pinnacle", "p_blended", "proposed_qty",
                  "limit_price"):
            d[k] = C.num(d[k])
        d["decided_at"] = C.num(r["decided_at"])
        d["decision_ref"] = r["decision_id"]
        d["source"] = "PAPER_DECISION"
        d["valuation"] = (vals.get(int(r["valuation_id"]))
                          if r["valuation_id"] is not None else None)
        d["settlement"] = dict(_settlement(r), version=r["st_version"])
        decs.append(d)
    by_event = await _leagues_by_event(conn, [
        ((d["valuation"] or {}).get("event_key")
         or (d["label"] or {}).get("event_key")) for d in decs
        if not (d["label"] or {}).get("competition")])
    for d in decs:
        d["league"], d["league_basis"] = league_of(d["label"] or {},
                                                   d["valuation"], by_event)
    return decs


async def coverage_refusals(conn, *, now, classifier_version,
                            days=LOOKBACK_DAYS, limit=MAX_PER_CYCLE) -> list:
    if not await PR.has(conn, "ext_candidate_outcomes"):
        return []
    rows = await conn.fetch(COVERAGE_REFUSALS_SQL,
                            float(now) - days * 86400.0, float(now),
                            classifier_version, int(limit))
    out = []
    for r in rows:
        codes = [str(c) for c in (C.jload(r["codes"]) or []) if c]
        first = r["first_refusal"]
        refusals = [first] + [c for c in codes if c != first]
        out.append({
            "decision_ref": coverage_ref(r["provider_event_id"],
                                         r["us_market_slug"]),
            "source": "COVERAGE_LEDGER", "decision_id": None,
            "coverage_row_id": int(r["id"]),
            "provider_event_id": r["provider_event_id"],
            "stage": r["stage"], "strategy": None,
            "us_market_slug": r["us_market_slug"], "holding_side": None,
            "refusal": first, "refusals": refusals,
            "decided_at": C.num(r["decided_at"]),
            "league": r["sport_key"] or "UNATTRIBUTED:%s" % (
                r["family"] or "UNKNOWN"),
            "league_basis": ("COVERAGE_LEDGER_SPORT_KEY" if r["sport_key"]
                             else "NO_LEAGUE_RECORDED"),
            "settlement": _settlement(r)})
    return out


# ═════════════════════════════════════════════════════════════════════
# OPPORTUNITY SCORE INPUTS
# ═════════════════════════════════════════════════════════════════════

async def score_candidates(conn, *, now, version, hours=SCORE_LOOKBACK_H,
                           limit=MAX_SCORES_PER_CYCLE) -> list:
    rows = await conn.fetch(
        "SELECT c.capacity_id, c.candidate_id, c.status, c.why, "
        "       c.us_market_slug, c.holding_side, c.strategy, "
        "       c.executable_opportunity_dollars, c.executable_capacity_usd, "
        "       c.capacity_ceiling_usd, "
        "       extract(epoch FROM c.decided_at)::float8 AS decided_at, "
        "       d.verdict, d.label->>'competition' AS league, "
        "       d.valuation_id, "
        "       (SELECT extract(epoch FROM g.game_start)::float8 "
        "          FROM us_premap g WHERE g.market_slug = c.us_market_slug "
        "           AND g.game_start IS NOT NULL "
        "         ORDER BY g.updated_at DESC NULLS LAST LIMIT 1) "
        "       AS event_start_at "
        "  FROM pos_capacity_latest c "
        "  LEFT JOIN paper_decisions d ON d.decision_id = c.candidate_id "
        " WHERE c.decided_at >= to_timestamp($1) "
        "   AND NOT EXISTS (SELECT 1 FROM lol_opportunity_scores s "
        "                    WHERE s.candidate_id = c.candidate_id "
        "                      AND s.capacity_id = c.capacity_id "
        "                      AND s.version = $2) "
        " ORDER BY c.decided_at DESC LIMIT $3",
        float(now) - hours * 3600.0, version, int(limit))
    return [dict(r) for r in rows]


async def snapshots_since(conn, *, since) -> dict:
    """{'CAPITAL': [(t, idle, why)], 'CAPACITY': [(t, fp, basis)]} ascending,
    from the latest snapshot at or before `since` onwards."""
    out = {"CAPITAL": [], "CAPACITY": []}
    for comp, book, sel in (
            ("CAPITAL", "PAPER",
             "payload->'idle_capital_usd' AS v, "
             "payload->'unmeasured'->>'idle_capital_usd' AS why"),
            ("CAPACITY", "NONE",
             "payload->'rates'->'fill_probability' AS v, NULL AS why")):
        rows = await conn.fetch(
            "SELECT extract(epoch FROM computed_at)::float8 AS t, %s "
            "  FROM pos_snapshots WHERE component = $1 AND book = $2 "
            "   AND computed_at >= coalesce((SELECT max(computed_at) "
            "         FROM pos_snapshots WHERE component = $1 AND book = $2 "
            "          AND computed_at <= to_timestamp($3)), "
            "       to_timestamp($3)) "
            " ORDER BY computed_at ASC LIMIT 2000" % sel, comp, book,
            float(since))
        for r in rows:
            v = C.jload(r["v"])
            if comp == "CAPACITY":
                v = v if isinstance(v, dict) else {}
                fp = C.num(v.get("value"))
                why = ("%s (n=%s)" % (v.get("basis"), v.get("n"))
                       if fp is not None else (v.get("why")
                                               or "NO_FILL_RATE_IN_SNAPSHOT"))
                out[comp].append((C.num(r["t"]), fp, why))
            else:
                out[comp].append((C.num(r["t"]), C.num(v), r["why"]))
    return out


def as_of(series, t):
    """The latest (t, v, why) entry at or before t, else None."""
    best = None
    for e in series:
        if e[0] is not None and t is not None and e[0] <= t:
            best = e
    return best


async def lag_samples(conn, *, now) -> list:
    return await PR.settlement_lag_samples(conn, now=now)


async def score_context(conn, *, cands: list, since: float) -> dict:
    """What the decomposition reads, as of each decision: the PAPER
    EDGE_CALIBRATION observations, the intel regime states, the
    candidates' valuation rows (settlement comparison), and the pos-learn
    edge confidence of each valuation when migration 218 is present."""
    out = {"calibration": [], "regimes": [], "valuations": {},
           "edge_confidence": {}, "edge_confidence_why": None,
           "eddie": {}, "eddie_why": None}
    for r in await conn.fetch(
            "SELECT extract(epoch FROM computed_at)::float8 AS t, value, "
            "       status, why, sample_n, detail->>'unit' AS unit "
            "  FROM pos_metric_observations "
            " WHERE book = 'PAPER' AND metric = 'EDGE_CALIBRATION' "
            "   AND computed_at >= coalesce((SELECT max(computed_at) "
            "        FROM pos_metric_observations WHERE book = 'PAPER' "
            "         AND metric = 'EDGE_CALIBRATION' "
            "         AND computed_at <= to_timestamp($1)), "
            "       to_timestamp($1)) "
            " ORDER BY computed_at ASC LIMIT 2000", float(since)):
        out["calibration"].append((C.num(r["t"]), {
            "value": C.num(r["value"]), "status": r["status"],
            "why": r["why"], "sample_n": r["sample_n"], "unit": r["unit"]}))
    if await PR.has(conn, "intel_regime_states"):
        for r in await conn.fetch(
                "SELECT extract(epoch FROM computed_at)::float8 AS t, "
                "       recommendation FROM intel_regime_states "
                " WHERE computed_at >= coalesce((SELECT max(computed_at) "
                "        FROM intel_regime_states "
                "        WHERE computed_at <= to_timestamp($1)), "
                "       to_timestamp($1)) "
                " ORDER BY computed_at ASC LIMIT 2000", float(since)):
            out["regimes"].append((C.num(r["t"]), {
                "recommendation": r["recommendation"]}))
    vids = [c.get("valuation_id") for c in cands]
    out["valuations"] = await _valuations(conn, vids)
    ids = sorted({int(v) for v in vids if v is not None})
    if not (await PR.has(conn, "poslearn_forecasts")
            and await PR.has(conn, "poslearn_opportunities")):
        out["edge_confidence_why"] = ("POS_LEARN_EDGE_CONFIDENCE_NOT_DEPLOYED"
                                      " (migration 218 absent)")
    elif ids:
        for r in await conn.fetch(
                "SELECT DISTINCT ON (o.source_id) o.source_id, "
                "       f.edge_confidence, f.registration_id, "
                "       extract(epoch FROM f.predicted_at)::float8 AS t "
                "  FROM poslearn_opportunities o "
                "  JOIN poslearn_forecasts f "
                "    ON f.opportunity_id = o.opportunity_id "
                " WHERE o.source_kind = 'EXTERNAL_VALUATION' "
                "   AND o.source_id = ANY($1::bigint[]) "
                "   AND f.edge_confidence IS NOT NULL "
                " ORDER BY o.source_id, f.predicted_at DESC LIMIT $2",
                ids, MAX_SCORES_PER_CYCLE):
            out["edge_confidence"][int(r["source_id"])] = {
                "value": C.num(r["edge_confidence"]),
                "basis": "poslearn_forecasts %s at %s (SHADOW)" % (
                    r["registration_id"], r["t"])}
    out["eddie"], out["eddie_why"] = await eddie_estimates(
        conn, [c.get("candidate_id") for c in cands])
    return out


#: Eddie's (migration 217, SHADOW_ONLY) execution estimate of one paper
#: decision, computed from that decision's recorded book and inputs.
EDDIE_COLS = (
    "estimate_id", "decision_id", "estimator_version", "book_obs_id",
    "book_age_s", "expected_fill_probability",
    "expected_net_executable_edge_pp", "expected_execution_loss_pp",
    "expected_executable_ev_usd", "expected_time_to_fill_s",
    "max_executable_qty", "execution_style", "recommendation",
    "recommendation_reason")
EDDIE_NUM = (
    "book_age_s", "expected_fill_probability",
    "expected_net_executable_edge_pp", "expected_execution_loss_pp",
    "expected_executable_ev_usd", "expected_time_to_fill_s",
    "max_executable_qty")


async def eddie_estimates(conn, decision_ids) -> tuple:
    """({decision_id: estimate}, why-absent). The latest Eddie estimate per
    decision; ({}, reason) without migration 217."""
    ids = sorted({str(i) for i in decision_ids if i})
    if not await PR.has(conn, "eddie_execution_estimates"):
        return {}, ("EDDIE_EXECUTION_ESTIMATES_ABSENT (migration 217 not "
                    "applied)")
    if not ids:
        return {}, None
    got = {}
    for r in await conn.fetch(
            "SELECT DISTINCT ON (decision_id) %s, "
            "       extract(epoch FROM estimated_at)::float8 AS estimated_at,"
            "       unmeasured->>'fill_probability' AS fill_why "
            "  FROM eddie_execution_estimates "
            " WHERE decision_id = ANY($1::text[]) "
            " ORDER BY decision_id, estimated_at DESC LIMIT $2"
            % ", ".join(EDDIE_COLS), ids, MAX_SCORES_PER_CYCLE):
        d = dict(r)
        for k in EDDIE_NUM:
            d[k] = C.num(d.get(k))
        got[str(r["decision_id"])] = d
    return got, None


# ═════════════════════════════════════════════════════════════════════
# HORIZON FORECAST INPUTS
# ═════════════════════════════════════════════════════════════════════

OPP_LOOKBACK_DAYS = 14.0


async def opportunity_counts(conn, *, now, days=OPP_LOOKBACK_DAYS) -> dict:
    r = await conn.fetchrow(
        "SELECT count(*) AS n, count(*) FILTER (WHERE status = 'MEASURED' "
        "         AND executable_opportunity_dollars > 0) AS q, "
        "       extract(epoch FROM min(decided_at))::float8 AS first "
        "  FROM pos_capacity_latest "
        " WHERE decided_at >= to_timestamp($1) "
        "   AND decided_at <= to_timestamp($2)",
        float(now) - days * 86400.0, float(now))
    first = C.num(r["first"])
    span = 0.0 if first is None else min(days, (float(now) - first) / 86400.0)
    return {"candidates": int(r["n"]), "qualified": int(r["q"]),
            "days": round(span, 3), "lookback_days": days}


async def capital_now(conn) -> dict:
    out = {}
    for book in ("PAPER", "ACTUAL"):
        r = await conn.fetchrow(
            "SELECT payload->'idle_capital_usd' AS idle, "
            "       payload->'unmeasured'->>'idle_capital_usd' AS why, "
            "       payload->'account_capital_usd' AS acct "
            "  FROM pos_snapshots WHERE component = 'CAPITAL' AND book = $1 "
            " ORDER BY computed_at DESC LIMIT 1", book)
        out[book] = None if r is None else {
            "idle_capital_usd": C.num(C.jload(r["idle"])),
            "idle_why": r["why"],
            "account_capital_usd": C.num(C.jload(r["acct"]))}
    return out


async def unscored_horizons(conn, *, now) -> list:
    rows = await conn.fetch(
        "SELECT f.forecast_id, f.book, f.horizon, f.quantiles, "
        "       f.prob_positive, f.p10_pnl_usd, f.p50_pnl_usd, "
        "       f.p90_pnl_usd, "
        "       extract(epoch FROM f.horizon_start)::float8 AS hs, "
        "       extract(epoch FROM f.horizon_end)::float8 AS he "
        "  FROM lol_horizon_forecasts f "
        " WHERE f.status <> 'UNAVAILABLE' "
        "   AND f.horizon_end <= to_timestamp($1) "
        "   AND NOT EXISTS (SELECT 1 FROM lol_horizon_forecast_scores s "
        "                    WHERE s.forecast_id = f.forecast_id) "
        " ORDER BY f.issued_at LIMIT 300", float(now))
    return [dict(r) for r in rows]


async def horizon_scores(conn, *, book, horizon) -> list:
    rows = await conn.fetch(
        "SELECT inside_p10_p90, brier_positive "
        "  FROM lol_horizon_forecast_scores "
        " WHERE book = $1 AND horizon = $2 ORDER BY scored_at LIMIT 5000",
        book, horizon)
    return [dict(r) for r in rows]


async def realized_between(conn, *, book, start, end) -> tuple:
    return await PR.realized_between(conn, book=book, start=start, end=end)
