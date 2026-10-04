"""THE LOST OPPORTUNITY READS (migration 220): GET only, COMMAND auth via
agents_core.require_read (401 without a session).

  GET /api/command/profitability/lost-opportunities
        ?classification=GOOD_REFUSAL|FALSE_REFUSAL|UNKNOWABLE
        &league=&limit=&classifier_version=
      data.rows[]               the ledger rows (newest decision first)
      data.summary.by_class     {CLASS: {n, hypothetical_pnl_usd, priced_n}}
      data.summary.by_league    [{league, n, GOOD_REFUSAL, FALSE_REFUSAL,
                                  UNKNOWABLE}]
      data.summary.by_refusal_reason
                                [{refusal, refusal_category, n, by class}]
      data.false_refusals[]     every FALSE_REFUSAL with its named defect
      data.false_refusal_defects [{defect, n}]
  GET /api/command/profitability/opportunity-scores?status=&limit=
      data.rows[]               latest score per candidate, highest first
                                (UNAVAILABLE rows last, with their reason),
                                each with `components` (the decomposition)
                                and `expand` (Pinnacle probability and age,
                                market price, gross edge, fees, executable
                                edge, capacity, settlement, Derek thesis,
                                Karen challenge, Eddie, allocator, Xavier,
                                Audrey -- UNAVAILABLE parts name why)
      data.counts / unavailable_by_reason / formula / unit
  GET /api/command/profitability/forecast-horizons
      data.{PAPER,ACTUAL}.{24H,7D,30D}  THE INVESTMENT SLEEVE (production
                                confidence, migration 227): expected
                                opportunities, qualified opportunities,
                                turnover, deployable capital, capital-hours,
                                net P&L P10/P50/P90, P(positive), expected
                                drawdown, capacity utilization; status
                                UNPROVEN | UNAVAILABLE
      by_sleeve.{book}.{sleeve}.{horizon}  every sleeve separately (research
                                for TRAINING / BENCHMARK / UNCLASSIFIED);
                                legacy_book_wide: pre-227 pooled forecasts;
                                scores per book:sleeve:horizon

EVERY RESPONSE IS RESEARCH WITH NO AUTHORITY: the envelope of the
profitability reads, a READ ONLY transaction with a statement timeout, and
lol_* / pos_* tables only. The hypothetical P&L is labelled HYPOTHETICAL: it
is what the decision-time quantity at the decision-time executable price
would have paid, never a realized result, and never summed with PAPER or
ACTUAL. A hindsight winner is not a missed opportunity.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query

from .agents_core import _pool, require_read

router = APIRouter()
BASE = "/api/command/profitability"
STATEMENT_TIMEOUT_MS = 8000
CLASSIFIER_VERSION = "LOL_CLASSIFIER_V1"
#: V2 (R30A): executable-freshness capacity, freshness-checked Eddie fill
#: estimate, INVESTMENT-only calibration and fill rates (lost_opportunity/
#: score.py). V1 rows stay in the table, append-only, and are not served as
#: the current score.
SCORE_VERSION = "LOL_OPPORTUNITY_SCORE_V2"
SLEEVES = ("INVESTMENT", "TRAINING", "BENCHMARK", "UNCLASSIFIED")
CLASSES = ("GOOD_REFUSAL", "FALSE_REFUSAL", "UNKNOWABLE")
JSON_COLS = ("detail", "unmeasured", "summary", "components", "quantiles",
             "validation", "basis")
LEDGER_DISCLOSURE = (
    "RESEARCH / SHADOW_NO_AUTHORITY. Classified from the decision-time record "
    "only; the settlement decides that the market settled and prices the "
    "HYPOTHETICAL P&L, and never feeds the class. A hindsight winner is not "
    "a missed opportunity. FALSE_REFUSAL requires a positive executable net "
    "EV under the policy's own thresholds after fees AND a named defect.")
SCORE_FORMULA = ("expected net executable EV (pos_capacity, conditional on "
                 "fill, counted only when its book meets the strategy's "
                 "executable freshness) x fill probability (Eddie's estimate "
                 "on an executable-fresh book, else the CAPACITY snapshot's "
                 "PRODUCTION rate: INVESTMENT strategies' PAPER-simulated "
                 "entries) x capacity factor (min(1, idle PAPER capital / "
                 "executable capacity)) / capital-hours (executable capacity "
                 "x expected hold hours); every input as of the decision "
                 "instant")


def _env(status, why=None, **kw) -> dict:
    from ..profitability import common as C
    return C.envelope(status, why, **kw)


def _row(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if hasattr(v, "timestamp"):
            out[k] = v.timestamp()
        elif k in JSON_COLS and isinstance(v, str):
            try:
                out[k] = json.loads(v)
            except ValueError:
                out[k] = v
        elif v is not None and type(v).__name__ == "Decimal":
            out[k] = float(v)
        else:
            out[k] = v
    return out


async def _read(fn):
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            # a pooled connection is idle: a READ ONLY transaction. (A
            # caller already inside a transaction -- a test's -- gets a
            # savepoint; Postgres cannot make a nested block read only.)
            nested = conn.is_in_transaction()
            async with conn.transaction(readonly=not nested):
                await conn.execute("SET LOCAL statement_timeout = %d"
                                   % STATEMENT_TIMEOUT_MS)
                ready = await conn.fetchval(
                    "SELECT to_regclass('lol_ledger') IS NOT NULL "
                    "   AND to_regclass('lol_opportunity_scores_latest') "
                    "       IS NOT NULL "
                    "   AND to_regclass('lol_horizon_forecasts') IS NOT NULL")
                if not ready:
                    return _env("EMPTY", "MIGRATION_220_NOT_APPLIED",
                                data=None)
                return await fn(conn)
    except Exception as exc:                                    # noqa: BLE001
        return _env("UNAVAILABLE", "%s: %s" % (type(exc).__name__,
                                               str(exc)[:160]), data=None)


async def _last_run(conn, component):
    r = await conn.fetchrow(
        "SELECT run_id, pos_run_id, status, error, summary, "
        "       extract(epoch FROM started_at)::float8 AS started_at, "
        "       extract(epoch FROM finished_at)::float8 AS finished_at "
        "  FROM lol_runs WHERE component = $1 "
        " ORDER BY started_at DESC LIMIT 1", component)
    return None if r is None else _row(r)


LEDGER_SELECT = (
    "SELECT ledger_id, decision_ref, source, classifier_version, strategy, "
    "       league, league_basis, us_market_slug, holding_side, "
    "       classification, reason, defect, refusal, refusals, "
    "       refusal_category, attribution, attribution_code, "
    "       decision_evidence_ids, "
    "       decision_time_net_ev_usd, decision_time_net_ev_basis, "
    "       decision_time_executable_price, decision_time_qty, "
    "       decision_time_fees_usd, decision_time_cost_usd, "
    "       policy_min_gross_edge, policy_min_net_ev_usd, "
    "       settlement_evidence_id, settlement_basis, settlement_outcome, "
    "       payout_per_contract, hypothetical_pnl_usd, "
    "       hypothetical_pnl_label, hypothetical_pnl_why, detail, label, "
    "       authority, "
    "       extract(epoch FROM decided_at)::float8 AS decided_at, "
    "       extract(epoch FROM settled_at)::float8 AS settled_at, "
    "       extract(epoch FROM classified_at)::float8 AS classified_at "
    "  FROM lol_ledger ")


@router.get(BASE + "/lost-opportunities", dependencies=[Depends(require_read)])
async def lost_opportunities(
        classification: str = Query(
            default="", pattern="^(|GOOD_REFUSAL|FALSE_REFUSAL|UNKNOWABLE)$"),
        league: str = Query(default="", max_length=80),
        classifier_version: str = Query(default=CLASSIFIER_VERSION,
                                        max_length=60),
        limit: int = Query(default=200, ge=1, le=1000)) -> dict:
    async def fn(conn):
        v = classifier_version
        rows = [_row(r) for r in await conn.fetch(
            LEDGER_SELECT +
            " WHERE classifier_version = $1 "
            "   AND ($2 = '' OR classification = $2) "
            "   AND ($3 = '' OR league = $3) "
            " ORDER BY decided_at DESC LIMIT $4",
            v, classification, league, int(limit))]
        by_class = {c: {"n": 0, "priced_n": 0, "hypothetical_pnl_usd": None}
                    for c in CLASSES}
        for r in await conn.fetch(
                "SELECT classification, count(*) AS n, "
                "       count(hypothetical_pnl_usd) AS priced_n, "
                "       sum(hypothetical_pnl_usd) AS hyp "
                "  FROM lol_ledger WHERE classifier_version = $1 "
                " GROUP BY classification", v):
            by_class[r["classification"]] = {
                "n": r["n"], "priced_n": r["priced_n"],
                "hypothetical_pnl_usd": (None if r["priced_n"] == 0
                                         else round(float(r["hyp"]), 6)),
                "hypothetical_pnl_label": "HYPOTHETICAL"}
        total = sum(c["n"] for c in by_class.values())
        if total == 0:
            last = await _last_run(conn, "LEDGER")
            return _env("EMPTY", "NO_SETTLED_REFUSAL_CLASSIFIED_YET" if last
                        else "NO_RUN_YET", data=None, last_run=last,
                        classifier_version=v, disclosure_ledger=LEDGER_DISCLOSURE)
        by_league = {}
        for r in await conn.fetch(
                "SELECT league, classification, count(*) AS n "
                "  FROM lol_ledger WHERE classifier_version = $1 "
                " GROUP BY league, classification", v):
            e = by_league.setdefault(r["league"] or "UNATTRIBUTED", dict(
                {"league": r["league"] or "UNATTRIBUTED", "n": 0},
                **{c: 0 for c in CLASSES}))
            e[r["classification"]] += r["n"]
            e["n"] += r["n"]
        by_reason = {}
        for r in await conn.fetch(
                "SELECT refusal, refusal_category, classification, "
                "       count(*) AS n FROM lol_ledger "
                " WHERE classifier_version = $1 "
                " GROUP BY refusal, refusal_category, classification", v):
            key = r["refusal"] or "(none)"
            e = by_reason.setdefault(key, dict(
                {"refusal": key, "refusal_category": r["refusal_category"],
                 "n": 0}, **{c: 0 for c in CLASSES}))
            e[r["classification"]] += r["n"]
            e["n"] += r["n"]
        # UNIQUE OPPORTUNITIES PER REFUSAL (R30A, owner audit 2026-10-04).
        # The ledger holds ONE ROW PER DECISION, and every policy
        # re-evaluates the same live market on each valuation: ranking the
        # reasons by row count ranked how often a market was LOOKED AT
        # (production: 22 decision rows per contract on average, up to 91).
        # The reasons are now ranked by the unique opportunities they
        # refused -- the funnel's key (fixture / market / side / line /
        # period; the fixture, line and period from the decision the row
        # classifies) -- with the rows kept beside it as evaluations, and the
        # unique count split by the refusing strategy's sleeve (migration
        # 223's map; a strategy outside it, or none, is UNCLASSIFIED). A row
        # with no market or side cannot be deduplicated and counts as its own
        # opportunity (`unkeyed`).
        from ..profitability import common as PC
        sl = {k: sorted(s_ for s_, v_ in PC.STRATEGY_SLEEVE.items()
                        if v_ == k)
              for k in (PC.INVESTMENT, PC.TRAINING, PC.BENCHMARK)}
        for r in await conn.fetch(
                "SELECT refusal, count(*) AS n, "
                "       count(DISTINCT k) AS uniq, "
                "       count(*) FILTER (WHERE k IS NULL) AS unkeyed, "
                "       count(DISTINCT k) FILTER (WHERE strategy = "
                "         ANY($2::text[])) AS inv, "
                "       count(DISTINCT k) FILTER (WHERE strategy = "
                "         ANY($3::text[])) AS tr, "
                "       count(DISTINCT k) FILTER (WHERE strategy = "
                "         ANY($4::text[])) AS bench, "
                "       count(DISTINCT k) FILTER (WHERE strategy IS NULL OR "
                "         NOT strategy = ANY($5::text[])) AS unc "
                "  FROM (SELECT x.refusal, x.strategy, "
                "               CASE WHEN x.us_market_slug IS NOT NULL "
                "                     AND x.holding_side IS NOT NULL "
                "                    THEN concat_ws('|', "
                "                         coalesce(d.fixture, ''), "
                "                         x.us_market_slug, "
                "                         upper(x.holding_side), "
                "                         coalesce(d.label->>'line', ''), "
                "                         coalesce(d.label->>'period', '')) "
                "               END AS k "
                "          FROM lol_ledger x "
                "          LEFT JOIN paper_decisions d "
                "            ON d.decision_id = x.decision_ref "
                "         WHERE x.classifier_version = $1) AS keyed "
                " GROUP BY refusal", v, sl[PC.INVESTMENT], sl[PC.TRAINING],
                sl[PC.BENCHMARK], sorted(PC.STRATEGY_SLEEVE)):
            e = by_reason.get(r["refusal"] or "(none)")
            if e is None:
                continue
            uniq = int(r["uniq"]) + int(r["unkeyed"])
            e["unique_opportunities"] = uniq
            e["evaluations"] = int(r["n"])
            e["re_evaluations"] = int(r["n"]) - uniq
            e["unkeyed"] = int(r["unkeyed"])
            e["unique_opportunities_by_sleeve"] = {
                PC.INVESTMENT: int(r["inv"]), PC.TRAINING: int(r["tr"]),
                PC.BENCHMARK: int(r["bench"]),
                PC.UNCLASSIFIED: int(r["unc"])}
        by_attr = {}
        for r in await conn.fetch(
                "SELECT attribution, classification, count(*) AS n, "
                "       sum(hypothetical_pnl_usd) AS hyp, "
                "       count(hypothetical_pnl_usd) AS priced "
                "  FROM lol_ledger WHERE classifier_version = $1 "
                " GROUP BY attribution, classification", v):
            e = by_attr.setdefault(r["attribution"], dict(
                {"attribution": r["attribution"], "n": 0},
                **{c: 0 for c in CLASSES}))
            e[r["classification"]] += r["n"]
            e["n"] += r["n"]
        trend = [_row(r) for r in await conn.fetch(
            "SELECT (decided_at AT TIME ZONE 'UTC')::date::text AS day, "
            "       count(*) FILTER (WHERE classification = 'GOOD_REFUSAL') "
            "         AS good, "
            "       count(*) FILTER (WHERE classification = 'FALSE_REFUSAL') "
            "         AS false_refusal, "
            "       count(*) FILTER (WHERE classification = 'UNKNOWABLE') "
            "         AS unknowable, "
            "       sum(hypothetical_pnl_usd) FILTER (WHERE classification "
            "         = 'FALSE_REFUSAL') AS missed_hypothetical_usd "
            "  FROM lol_ledger WHERE classifier_version = $1 "
            "   AND decided_at >= now() - interval '30 days' "
            " GROUP BY 1 ORDER BY 1", v)]
        m = await conn.fetchrow(
            "SELECT sum(hypothetical_pnl_usd) FILTER (WHERE classification "
            "         = 'FALSE_REFUSAL') AS missed, "
            "       count(hypothetical_pnl_usd) FILTER (WHERE classification "
            "         = 'FALSE_REFUSAL') AS missed_n, "
            "       sum(-hypothetical_pnl_usd) FILTER (WHERE classification "
            "         = 'GOOD_REFUSAL' AND hypothetical_pnl_usd < 0) "
            "         AS avoided, "
            "       count(*) FILTER (WHERE classification = 'GOOD_REFUSAL' "
            "         AND hypothetical_pnl_usd < 0) AS avoided_n, "
            "       count(*) FILTER (WHERE classification = 'GOOD_REFUSAL' "
            "         AND hypothetical_pnl_usd > 0) AS hindsight_n, "
            "       sum(hypothetical_pnl_usd) FILTER (WHERE classification "
            "         = 'GOOD_REFUSAL' AND hypothetical_pnl_usd > 0) "
            "         AS hindsight "
            "  FROM lol_ledger WHERE classifier_version = $1", v)
        management = {
            "label": "HYPOTHETICAL",
            "missed_measurable_profit_usd": (
                None if not m["missed_n"] else round(float(m["missed"]), 2)),
            "missed_measurable_profit_n": m["missed_n"],
            "missed_measurable_profit_why": (
                None if m["missed_n"] else
                "NO_PRICED_FALSE_REFUSAL: nothing measurable was missed"),
            "avoided_losses_usd": (None if not m["avoided_n"]
                                   else round(float(m["avoided"]), 2)),
            "avoided_losses_n": m["avoided_n"],
            "hindsight_winners_correctly_refused_n": m["hindsight_n"],
            "hindsight_winners_correctly_refused_usd": (
                None if not m["hindsight_n"]
                else round(float(m["hindsight"]), 2)),
            "basis": ("missed = HYPOTHETICAL P&L of FALSE_REFUSAL rows at the "
                      "decision-time executable price; avoided = the losses "
                      "GOOD_REFUSAL rows would have taken; hindsight winners "
                      "that were correctly refused are NOT missed profit")}
        false_rows = [_row(r) for r in await conn.fetch(
            "SELECT decision_ref, source, league, strategy, us_market_slug, "
            "       holding_side, refusal, defect, reason, attribution, "
            "       decision_time_net_ev_usd, decision_time_net_ev_basis, "
            "       hypothetical_pnl_usd, hypothetical_pnl_label, "
            "       decision_evidence_ids, settlement_evidence_id, "
            "       extract(epoch FROM decided_at)::float8 AS decided_at, "
            "       extract(epoch FROM settled_at)::float8 AS settled_at "
            "  FROM lol_ledger WHERE classifier_version = $1 "
            "   AND classification = 'FALSE_REFUSAL' "
            " ORDER BY decided_at DESC LIMIT 500", v)]
        defects = {}
        for r in false_rows:
            defects[r["defect"]] = defects.get(r["defect"], 0) + 1
        last = await _last_run(conn, "LEDGER")
        comp = await conn.fetchval(
            "SELECT extract(epoch FROM max(classified_at))::float8 "
            "  FROM lol_ledger WHERE classifier_version = $1", v)
        return _env("OK", None, computed_at=comp, data={
            "classifier_version": v,
            "rows": rows,
            "summary": {
                "total": total, "by_class": by_class,
                "by_league": sorted(by_league.values(),
                                    key=lambda e: (-e["n"], e["league"])),
                # ranked by UNIQUE OPPORTUNITIES; `n` (rows) is kept as
                # the evaluations beside it
                "by_refusal_reason": sorted(
                    by_reason.values(),
                    key=lambda e: (-e.get("unique_opportunities", e["n"]),
                                   -e["n"], e["refusal"])),
                "by_refusal_reason_basis": (
                    "unique opportunities (fixture / market / side / line / "
                    "period) per refusal; `n` / `evaluations` are rows, "
                    "`re_evaluations` the rows beyond the first per "
                    "opportunity; split by the refusing strategy's sleeve"),
                "by_attribution": sorted(
                    by_attr.values(),
                    key=lambda e: (-e["n"], e["attribution"])),
                "trend_30d": trend,
                "management": management},
            "false_refusals": false_rows,
            "false_refusal_defects": [
                {"defect": k, "n": n} for k, n in sorted(
                    defects.items(), key=lambda kv: (-kv[1], kv[0]))]},
            last_run=last, hypothetical_label="HYPOTHETICAL",
            disclosure_ledger=LEDGER_DISCLOSURE,
            summed_across_books=False)
    return await _read(fn)


async def _has(conn, table) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    table))


def _na(why) -> dict:
    return {"status": "UNAVAILABLE", "why": why}


async def _expand(conn, rows: list) -> None:
    """The expandable view of each opportunity, read at request time from
    the records that already exist (decision, capacity, Karen, Audrey,
    allocator, Xavier); every part absent is UNAVAILABLE with its reason.
    Eddie's execution assessment is his latest eddie_execution_estimates
    row for the decision (migration 217, SHADOW_ONLY)."""
    ids = [r["candidate_id"] for r in rows if r.get("candidate_id")]
    caps = [r["capacity_id"] for r in rows if r.get("capacity_id")]
    dec, cap, kar, aud, alloc, xav = {}, {}, {}, {}, {}, {}
    eddie, eddie_why = {}, None
    if not await _has(conn, "eddie_execution_estimates"):
        eddie_why = ("EDDIE_EXECUTION_ESTIMATES_ABSENT (migration 217 not "
                     "applied)")
    elif ids:
        for e in await conn.fetch(
                "SELECT DISTINCT ON (decision_id) decision_id, estimate_id, "
                "       estimator_version, recommendation, "
                "       recommendation_reason, execution_style, "
                "       expected_fill_probability, "
                "       expected_net_executable_edge_pp, "
                "       expected_execution_loss_pp, "
                "       expected_executable_ev_usd, expected_time_to_fill_s, "
                "       book_obs_id, book_age_s, "
                "       extract(epoch FROM estimated_at)::float8 "
                "       AS estimated_at "
                "  FROM eddie_execution_estimates "
                " WHERE decision_id = ANY($1::text[]) "
                " ORDER BY decision_id, estimated_at DESC", ids):
            eddie[e["decision_id"]] = dict(e)
    if ids:
        for d in await conn.fetch(
                "SELECT decision_id, verdict, refusal, refusals, p_pinnacle, "
                "       p_internal, p_blended, pinnacle->>'at' AS pin_at, "
                "       pinnacle->>'age_s' AS pin_age, "
                "       pinnacle->>'limit_s' AS pin_limit, "
                "       pinnacle->>'provider' AS pin_provider, "
                "       policy_decision->>'gross_edge_pp' AS gross_edge_pp, "
                "       policy_decision->>'fees_usd' AS fees_usd, "
                "       policy_decision->>'executable_price' AS exec_price, "
                "       policy_decision->>'net_expected_profit_usd' AS net, "
                "       policy_decision->>'rationale' AS rationale, "
                "       extract(epoch FROM decided_at)::float8 AS decided_at "
                "  FROM paper_decisions WHERE decision_id = ANY($1::text[])",
                ids):
            dec[d["decision_id"]] = dict(d)
        if await _has(conn, "karen_challenges"):
            for k in await conn.fetch(
                    "SELECT DISTINCT ON (target_id) target_id, challenge_id, "
                    "       claim, severity, state, outcome, blocked, "
                    "       extract(epoch FROM challenged_at)::float8 AS at "
                    "  FROM karen_challenges WHERE target_id = ANY($1::text[])"
                    " ORDER BY target_id, challenged_at DESC", ids):
                kar[k["target_id"]] = dict(k)
        if await _has(conn, "paper_audrey_findings"):
            for a in await conn.fetch(
                    "SELECT subject, count(*) AS n, "
                    "       (array_agg(kind ORDER BY found_at DESC))[1] "
                    "       AS kind, "
                    "       (array_agg(severity ORDER BY found_at DESC))[1] "
                    "       AS severity "
                    "  FROM paper_audrey_findings "
                    " WHERE subject = ANY($1::text[]) GROUP BY subject", ids):
                aud[a["subject"]] = dict(a)
        if await _has(conn, "intel_allocations"):
            for a in await conn.fetch(
                    "SELECT DISTINCT ON (decision_id) decision_id, run_id, "
                    "       rank, shadow_usd, binding_constraint, "
                    "       extract(epoch FROM computed_at)::float8 AS at "
                    "  FROM intel_allocations "
                    " WHERE decision_id = ANY($1::text[]) "
                    " ORDER BY decision_id, computed_at DESC", ids):
                alloc[a["decision_id"]] = dict(a)
        if await _has(conn, "xavier_entry_theses"):
            for x in await conn.fetch(
                    "SELECT DISTINCT ON (decision_id) decision_id, thesis_id, "
                    "       entry_ev_usd, entry_probability "
                    "  FROM xavier_entry_theses "
                    " WHERE decision_id = ANY($1::text[]) "
                    " ORDER BY decision_id, entered_at DESC", ids):
                xav[x["decision_id"]] = dict(x)
    if caps:
        for c in await conn.fetch(
                "SELECT capacity_id, best_price, expected_price_impact, "
                "       capacity_ceiling_usd, visible_depth_usd, "
                "       executable_capacity_usd, "
                "       executable_opportunity_dollars, book_age_s "
                "  FROM pos_capacity WHERE capacity_id = ANY($1::text[])",
                caps):
            cap[c["capacity_id"]] = dict(c)

    def f(v):
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    has_aud = await _has(conn, "paper_audrey_findings")
    for i, r in enumerate(rows):
        d = dec.get(r.get("candidate_id"))
        c = cap.get(r.get("capacity_id")) or {}
        ex = {}
        if d is None:
            ex["pinnacle"] = _na("NO_PAPER_DECISION_RECORD")
            ex["derek_thesis"] = _na("NO_PAPER_DECISION_RECORD")
        else:
            ex["pinnacle"] = {
                "status": "OK" if d["p_pinnacle"] is not None
                else "UNAVAILABLE",
                "why": None if d["p_pinnacle"] is not None
                else "NO_PINNACLE_PROBABILITY_RECORDED",
                "probability": d["p_pinnacle"], "observed_at": f(d["pin_at"]),
                "evidence_age_s": f(d["pin_age"]),
                "limit_s": f(d["pin_limit"]), "provider": d["pin_provider"]}
            ex["derek_thesis"] = {
                "status": "OK", "verdict": d["verdict"],
                "refusals": list(d["refusals"] or []),
                "p_internal": d["p_internal"], "p_blended": d["p_blended"],
                "net_expected_profit_usd": f(d["net"]),
                "rationale": d["rationale"]}
        price = f((d or {}).get("exec_price")) or f(c.get("best_price"))
        cap_usd = f(c.get("executable_capacity_usd"))
        opp = f(c.get("executable_opportunity_dollars"))
        ex["market"] = {
            "market_price": price, "price_basis": (
                "policy executable price (depth-weighted)"
                if (d or {}).get("exec_price") else "best recorded price"),
            "gross_edge_pp": f((d or {}).get("gross_edge_pp")),
            "fees_usd": f((d or {}).get("fees_usd")),
            "expected_price_impact": f(c.get("expected_price_impact")),
            "expected_executable_edge_per_dollar": (
                None if not cap_usd or opp is None else round(opp / cap_usd,
                                                              6)),
            "executable_capacity_usd": cap_usd,
            "capacity_ceiling_usd": f(c.get("capacity_ceiling_usd")),
            "visible_depth_usd": f(c.get("visible_depth_usd")),
            "book_age_s": f(c.get("book_age_s"))}
        comps = r.get("components") or {}
        ex["settlement"] = (comps.get("SETTLEMENT_CONFIDENCE")
                            or _na("NOT_RECORDED"))
        k = kar.get(r.get("candidate_id"))
        ex["karen_challenge"] = (dict(k, status="OK") if k else
                                 _na("NO_KAREN_CHALLENGE_ON_THIS_DECISION"))
        e = eddie.get(r.get("candidate_id"))
        if e:
            ex["eddie_execution"] = dict(
                {k: (f(v) if k.startswith("expected_") or k == "book_age_s"
                     else v) for k, v in e.items()},
                status="OK", authority="SHADOW_ONLY",
                basis="eddie_execution_estimates (migration 217), latest "
                      "estimate of this decision")
        else:
            ex["eddie_execution"] = _na(
                eddie_why or "NO_EDDIE_ESTIMATE_FOR_THIS_DECISION")
        a = alloc.get(r.get("candidate_id"))
        ex["allocator"] = (dict(a, status="OK", basis="intel_allocations "
                                "(SHADOW) latest run") if a else dict(
            _na("NO_INTEL_ALLOCATION_FOR_THIS_DECISION"),
            leaderboard_rank=i + 1 if r.get("status") == "MEASURED"
            else None))
        x = xav.get(r.get("candidate_id"))
        ex["xavier"] = (dict(x, status="OK") if x else _na(
            "NO_XAVIER_ENTRY_THESIS: Xavier writes a thesis only for an "
            "entered position"))
        au = aud.get(r.get("candidate_id"))
        ex["audrey"] = (dict(au, status="OK") if au else {
            "status": "OK", "n": 0, "why": None,
            "note": "no Audrey finding names this decision"}) \
            if has_aud else _na(
                "PAPER_AUDREY_FINDINGS_ABSENT")
        r["expand"] = ex


@router.get(BASE + "/opportunity-scores", dependencies=[Depends(require_read)])
async def opportunity_scores(
        status: str = Query(default="", pattern="^(|MEASURED|UNAVAILABLE)$"),
        limit: int = Query(default=100, ge=1, le=1000)) -> dict:
    async def fn(conn):
        rows = [_row(r) for r in await conn.fetch(
            "SELECT score_id, candidate_id, capacity_id, strategy, verdict, "
            "       league, us_market_slug, holding_side, "
            "       expected_net_executable_ev_usd, fill_probability, "
            "       fill_probability_basis, capacity_factor, "
            "       capacity_factor_basis, executable_capacity_usd, "
            "       expected_hold_h, expected_hold_basis, capital_hours, "
            "       opportunity_score, components, score_unit, status, why, "
            "       unmeasured, "
            "       detail, version, label, authority, "
            "       extract(epoch FROM decided_at)::float8 AS decided_at, "
            "       extract(epoch FROM computed_at)::float8 AS computed_at "
            "  FROM lol_opportunity_scores_latest "
            " WHERE version = $1 AND ($2 = '' OR status = $2) "
            " ORDER BY opportunity_score DESC NULLS LAST, decided_at DESC "
            " LIMIT $3", SCORE_VERSION, status, int(limit))]
        counts = {"MEASURED": 0, "UNAVAILABLE": 0}
        for r in await conn.fetch(
                "SELECT status, count(*) AS n "
                "  FROM lol_opportunity_scores_latest WHERE version = $1 "
                " GROUP BY status", SCORE_VERSION):
            counts[r["status"]] = r["n"]
        reasons = [{"why": r["why"], "n": r["n"]} for r in await conn.fetch(
            "SELECT why, count(*) AS n FROM lol_opportunity_scores_latest "
            " WHERE version = $1 AND status = 'UNAVAILABLE' "
            " GROUP BY why ORDER BY count(*) DESC LIMIT 20", SCORE_VERSION)]
        last = await _last_run(conn, "SCORES")
        if not any(counts.values()):
            return _env("EMPTY", "NO_CANDIDATE_SCORED_YET" if last
                        else "NO_RUN_YET", data=None, last_run=last,
                        formula=SCORE_FORMULA)
        # (R30A review) every score row names its scope at the top level:
        # book, sleeve (the decision strategy's, classifier map), strategy,
        # the deciding policy version and its confidence scope, as the
        # scoring run recorded them in the row's detail
        from ..profitability import common as PC
        for r in rows:
            det = r.get("detail") if isinstance(r.get("detail"), dict) \
                else (json.loads(r["detail"]) if isinstance(
                    r.get("detail"), str) else {})
            sl = det.get("sleeve") or PC.strategy_sleeve(r.get("strategy"))
            r["book"] = det.get("book") or "PAPER"
            r["sleeve"] = sl
            r["policy_version"] = det.get("policy_version")
            r["policy_version_why"] = (
                None if det.get("policy_version") else
                "NOT_RECORDED_BEFORE_R30A_REVIEW" if "policy_version"
                not in det else "THE_DECISION_RECORDED_NO_POLICY_VERSION")
            r["confidence_scope"] = PC.confidence_scope(sl)
        await _expand(conn, rows)
        comp = max((r["computed_at"] for r in rows), default=None)
        return _env("OK", None, computed_at=comp, data={
            "rows": rows, "counts": counts,
            "unavailable_by_reason": reasons,
            "unit": "USD_EXPECTED_NET_PER_USD_CAPITAL_HOUR",
            "version": SCORE_VERSION}, formula=SCORE_FORMULA, last_run=last)
    return await _read(fn)


HORIZON_KEYS = ("24H", "7D", "30D")


@router.get(BASE + "/forecast-horizons", dependencies=[Depends(require_read)])
async def forecast_horizons() -> dict:
    """The latest 24H / 7D / 30D forecast per book AND sleeve (migration
    227), with each horizon's forward scoring so far. `data.{PAPER,ACTUAL}`
    is the PRODUCTION-CONFIDENCE scope -- the INVESTMENT sleeve only;
    `by_sleeve` shows every sleeve separately (TRAINING / BENCHMARK /
    UNCLASSIFIED are research), and pre-227 book-wide forecasts are listed
    apart, never as INVESTMENT. UNPROVEN until each scope's own scoring
    validates it."""
    async def fn(conn):
        data = {"PAPER": {}, "ACTUAL": {}}
        by_sleeve = {b: {s: {} for s in SLEEVES} for b in ("PAPER", "ACTUAL")}
        legacy = {"PAPER": {}, "ACTUAL": {}}
        latest = None
        for r in await conn.fetch(
                "SELECT DISTINCT ON (book, sleeve, strategy, horizon) "
                "       forecast_id, book, sleeve, strategy, policy_versions,"
                "       classifier_version, confidence_scope, "
                "       horizon, horizon_days, method, seed, sample_days, "
                "       sample_positions, expected_opportunities, "
                "       expected_qualified_opportunities, "
                "       expected_turnover_usd, deployable_capital_usd, "
                "       expected_capital_hours, expected_pnl_usd, "
                "       p10_pnl_usd, p50_pnl_usd, p90_pnl_usd, prob_positive,"
                "       expected_max_drawdown_usd, capacity_utilization, "
                "       expected_capacity_usd, trailing_30d_committed_usd, "
                "       trailing_30d_capital_turnover, status, why, "
                "       validation, "
                "       unmeasured, basis, version, label, authority, "
                "       extract(epoch FROM issued_at)::float8 AS issued_at, "
                "       extract(epoch FROM horizon_end)::float8 "
                "       AS horizon_end "
                "  FROM lol_horizon_forecasts "
                " ORDER BY book, sleeve, strategy, horizon, issued_at DESC"):
            d = _row(r)
            if d["sleeve"] is None:
                d["scope"] = "BOOK_WIDE_PRE_227"
                legacy[d["book"]][d["horizon"]] = d
                continue
            if d["strategy"] not in (None, "ALL"):
                continue
            by_sleeve[d["book"]][d["sleeve"]][d["horizon"]] = d
            if d["sleeve"] == "INVESTMENT":
                data[d["book"]][d["horizon"]] = d
            latest = max(latest or 0, d["issued_at"])
        scores = {}
        for r in await conn.fetch(
                "SELECT book, coalesce(sleeve, 'BOOK_WIDE_PRE_227') AS sleeve,"
                "       horizon, count(*) AS n, "
                "       avg(inside_p10_p90::int) AS coverage, "
                "       avg(brier_positive) AS brier "
                "  FROM lol_horizon_forecast_scores "
                " GROUP BY book, coalesce(sleeve, 'BOOK_WIDE_PRE_227'), "
                "          horizon"):
            scores["%s:%s:%s" % (r["book"], r["sleeve"], r["horizon"])] = {
                "scored": r["n"], "coverage": float(r["coverage"]),
                "mean_brier": float(r["brier"])}
        last = await _last_run(conn, "HORIZONS")
        if latest is None:
            return _env("EMPTY", "NO_HORIZON_FORECAST_ISSUED_YET" if last
                        else "NO_RUN_YET", data=None, last_run=last,
                        legacy_book_wide=legacy)
        return _env("OK", None, computed_at=latest, data=data,
                    by_sleeve=by_sleeve, legacy_book_wide=legacy,
                    production_confidence_scope={
                        "sleeve": "INVESTMENT", "strategy": "ALL",
                        "rule": ("data.{PAPER,ACTUAL} is the INVESTMENT "
                                 "sleeve only; TRAINING / BENCHMARK / "
                                 "UNCLASSIFIED are in by_sleeve, never "
                                 "pooled into it")},
                    scores=scores, horizons=list(HORIZON_KEYS),
                    summed_across_books=False, summed_across_sleeves=False,
                    last_run=last,
                    forecast_label=("UNPROVEN until each horizon's own "
                                    "persisted forecasts are scored forward "
                                    "and validated"),
                    precision="dollars to $1, probabilities to 0.01, counts "
                              "to 0.1")
    return await _read(fn)
